"""Prepare/freeze one isolated Stage scope identity consolidation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r671"
NEW = ROOT / "data/t16-runtime-origin-graph-r673"
OLD_PACKAGE = OLD / "authority_runtime_origin_r671"
PACKAGE = NEW / "authority_runtime_origin_r673"
OLD_MANIFEST_SHA = "157400f5727114f81c25760fa45674e626d92f57facfa542dad5d81e9d9e0412"

HELPER = '''def _store_scope_identity_binding_original(scope):
    if (type(scope) is not _StageScope or _SCOPES.get(id(scope)) is not scope
            or scope.closed or scope.execution_owner != ns._owner()
            or type(scope.mode) is not str or scope.mode != "store"
            or _verify_store_stage_scope_original is not _STORE_STAGE_VERIFY_ORIGINAL
            or store._stage_stack_original is not _STORE_STACK_LIVE_ORIGINAL
            or store._held_io is not held
            or _HELD_IDENTITY is not _SCOPE_IDENTITY_ORIGINAL
            or held._verify_scope_identity_original is not _SCOPE_IDENTITY_ORIGINAL):
        return None
    custody = store._STACK_RECORDS.get(id(scope.stack))
    if (type(custody) is store._StoreStackRecord and custody is scope.permit
            and custody.original is scope.stack and not custody.closed and not custody.closing
            and custody.execution_owner == ns._owner()
            and custody.scope is scope.held_scope and custody.budget is scope.budget
            and held._SCOPES.get(id(scope.held_scope)) is custody.held_original
            and custody.first is None and custody.held_original.first is None):
        return custody
    return None


'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": digest(raw)}


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def json_new(path, value):
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def original_inputs():
    path = OLD / "r671-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(path.read_text(encoding="utf-8"))["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "owned_stage_io_r251.py").read_bytes()
    new = (PACKAGE / "owned_stage_io_r251.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if b.get(key) != a[key]] == ["_scope_live"]
    assert set(b) - set(a) == {"_store_scope_identity_binding_original"}
    same = 0
    for p in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / p.name
        compile(target.read_bytes(), str(target), "exec")
        if p.name != "owned_stage_io_r251.py":
            assert target.read_bytes() == p.read_bytes()
            same += 1
    for name in ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py"):
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r673", b"r671").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "stage_scope_identity_static_only", "original_inputs": inputs,
            "changed_definitions": ["_scope_live"], "new_definitions": ["_store_scope_identity_binding_original"],
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "store_namespace_held_budgets_effects_close_unchanged": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        target = PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name
        write_new(target, path.read_bytes())
    target = PACKAGE / "owned_stage_io_r251.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    changes = [
        ('_N2_EFFECT = store._native_namespace_verify\n', '_N2_EFFECT = store._native_namespace_verify\n_SCOPE_IDENTITY_ORIGINAL = _HELD_IDENTITY\n_STORE_STACK_LIVE_ORIGINAL = store._stage_stack_original\n'),
        ('def _scope_live(scope):\n', HELPER + 'def _scope_live(scope):\n'),
        ('    _HELD_IDENTITY(scope.held_scope)  # Metadata/graph only: no extra payload charge.\n',
         '    shared_store_custody = _store_scope_identity_binding_original(scope)\n    if shared_store_custody is None:\n        _HELD_IDENTITY(scope.held_scope)  # Original full path for Namespace/custom/mismatched scopes.\n'),
        ('    else:\n        _verify_store_stage_scope_original(scope)\n    return scope\n',
         '    else:\n        _verify_store_stage_scope_original(scope)\n    if shared_store_custody is not None:\n        if scope.first_failure is not None:\n            raise scope.first_failure\n        if shared_store_custody.first is not None:\n            raise shared_store_custody.first\n        if shared_store_custody.held_original.first is not None:\n            first = shared_store_custody.held_original.first\n            raise BaseException.with_traceback(first, BaseException.__dict__["__traceback__"].__get__(first))\n        _need(_store_scope_identity_binding_original(scope) is shared_store_custody,\n            "STAGE_ORIGINAL_BUDGET_CHANGED")\n    return scope\n'),
        ('def _new_store_stage_scope_original(stack, held_scope, *, cancellation):\n',
         '_STORE_STAGE_VERIFY_ORIGINAL = _verify_store_stage_scope_original\n\n\ndef _new_store_stage_scope_original(stack, held_scope, *, cancellation):\n'),
    ]
    new = old
    for before, after in changes:
        a, b = before.encode().replace(b"\n", eol), after.encode().replace(b"\n", eol)
        assert new.count(a) == 1, before
        new = new.replace(a, b, 1)
    target.write_bytes(new)
    for name in ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py"):
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r671", b"r673").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    diff = "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target)))
    write_new(NEW / "stage.diff", diff.encode())
    write_new(NEW / "README.md", b"# r673\n\nConsolidate duplicate held identity validation only inside one genuine Stage store scope check. Same live Store custody and canonical dispatch are required at entry and exit. Namespace/custom/mismatch retain the original full path. Ordinary effects, budgets, cancellation, clocks and closing stay intact. Static preparation only.\n")
    print(json.dumps({"stage": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / name for name in
        ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")]
    artifacts = [NEW / name for name in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "stage.diff", "README.md")]
    path = NEW / "r673-candidate-manifest.json"
    json_new(path, {"state": "stage_scope_identity_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "unchanged_support_modules": 15,
        "unchanged_original_definitions": evidence["unchanged_original_definitions"],
        "original_inputs_preserved": True, "compiled_without_execution": 20,
        "consumer_inverse_bytes_equal": True, "product_execution": False})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
