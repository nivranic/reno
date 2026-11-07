"""Preserve the original custom observation chain in the Pin-record candidate."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r679"
NEW = ROOT / "data/t16-runtime-origin-graph-r681"
OLD_PACKAGE = OLD / "authority_runtime_origin_r679"
PACKAGE = NEW / "authority_runtime_origin_r681"
OLD_MANIFEST_SHA = "241849d30626038b3d4432dc12a49eb86f5cccc8b5e86bb700e1466bac0f9999"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = 'def _pin_stack_identity_binding_original(record):\n    if (_pin_record_original is not _PIN_RECORD_CHECK_ORIGINAL\n            or _stage_stack_original is not _PIN_STACK_CHECK_ORIGINAL\n            or _held_io._verify_scope_identity_original is not _PIN_SCOPE_IDENTITY_ORIGINAL\n            or _held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL\n            or _held_io._resource_original is not _PIN_RESOURCE_LOOKUP_ORIGINAL):\n        return None\n    custody = _STACK_RECORDS.get(id(record.stack))\n    if (type(record) is not _StorePinRecord or type(record.original) is not _ClosedStorePin\n            or _PIN_RECORDS.get(id(record.original)) is not record or record.closed\n            or record.execution_owner != ns._owner()\n            or type(custody) is not _StoreStackRecord or custody.original is not record.stack\n            or custody.closed or custody.closing or custody.first is not None\n            or custody.scope is not record.scope or not any(pin is record for pin in custody.pins)\n            or _held_io._SCOPES.get(id(record.scope)) is not custody.held_original\n            or custody.held_original.first is not None\n            or type(record.lease) is not _held_io._FileLease\n            or record.kind not in {"source", "pin"}\n            or any(type(item) is not _held_io._OwnedRecord for item in custody.held_original.resources)):\n        return None\n    selected = [item for item in custody.held_original.resources if item.lease is record.lease]\n    if (len(selected) == 1 and selected[0].scope is record.scope and not selected[0].finished\n            and selected[0].resource_kind == ("filehold" if record.kind == "source" else "pin")):\n        return selected[0]\n    return None\n'
AFTER = 'def _pin_stack_identity_binding_original(record):\n    if (_pin_record_original is not _PIN_RECORD_CHECK_ORIGINAL\n            or _stage_stack_original is not _PIN_STACK_CHECK_ORIGINAL\n            or _held_io._verify_scope_identity_original is not _PIN_SCOPE_IDENTITY_ORIGINAL\n            or _held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL\n            or _held_io._file_observation_original is not _PIN_FILE_OBSERVATION_ORIGINAL\n            or _held_io._resource_original is not _PIN_RESOURCE_LOOKUP_ORIGINAL):\n        return None\n    custody = _STACK_RECORDS.get(id(record.stack))\n    if (type(record) is not _StorePinRecord or type(record.original) is not _ClosedStorePin\n            or _PIN_RECORDS.get(id(record.original)) is not record or record.closed\n            or record.execution_owner != ns._owner()\n            or type(custody) is not _StoreStackRecord or custody.original is not record.stack\n            or custody.closed or custody.closing or custody.first is not None\n            or custody.scope is not record.scope or not any(pin is record for pin in custody.pins)\n            or _held_io._SCOPES.get(id(record.scope)) is not custody.held_original\n            or custody.held_original.first is not None\n            or type(record.lease) is not _held_io._FileLease\n            or record.kind not in {"source", "pin"}\n            or any(type(item) is not _held_io._OwnedRecord for item in custody.held_original.resources)):\n        return None\n    selected = [item for item in custody.held_original.resources if item.lease is record.lease]\n    if (len(selected) == 1 and selected[0].scope is record.scope and not selected[0].finished\n            and selected[0].resource_kind == ("filehold" if record.kind == "source" else "pin")):\n        return selected[0]\n    return None\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": digest(raw)}


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def json_new(path, value):
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def original_inputs():
    path = OLD / "r679-candidate-manifest.json"
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
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert a.keys() == b.keys()
    assert [key for key in a if b[key] != a[key]] == ["_pin_stack_identity_binding_original"]
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    assert new.replace(AFTER.encode().replace(b"\n", eol), BEFORE.encode().replace(b"\n", eol), 1) == old
    same = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name != "personal_authority_store.py":
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r681", b"r679").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_record_compatibility_static_only", "original_inputs": inputs,
            "changed_definitions": ["_pin_stack_identity_binding_original"], "new_definitions": [],
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "store_inverse_bytes_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    before, after = BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol)
    assert old.count(before) == 1
    new = old.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r679", b"r681").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r681\n\nRequire original file observation dispatch for Pin-record consolidation. One admission condition added; all old failed sources and original test assertions preserved. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r681-candidate-manifest.json"
    json_new(path, {"state": "pin_record_compatibility_frozen_static_only", "source_files": [fact(p) for p in sources],
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
