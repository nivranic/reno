"""Consolidate adjacent fixed Pin acquisition entry checks in a new candidate."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r681"
NEW = ROOT / "data/t16-runtime-origin-graph-r685"
OLD_PACKAGE = OLD / "authority_runtime_origin_r681"
PACKAGE = NEW / "authority_runtime_origin_r685"
OLD_MANIFEST_SHA = "b3c44925d9990f47b697d7e4382f227fb0fa6ab32842b43df8e60c0abae13faf"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
POINTERS = '''_PIN_ENTRY_FACTORY_ORIGINAL = _enter_pin_original
_PIN_ENTRY_DIRECTORIES_ORIGINAL = _store_directories_verify_original


'''
BEFORE = '''    def enter_context(self, resource):
        record = _stage_stack_original(self)
'''
AFTER = '''    def enter_context(self, resource):
        if (type(resource) is _PinSelection
                and _enter_pin_original is _PIN_ENTRY_FACTORY_ORIGINAL
                and _stage_stack_original is _PIN_STACK_CHECK_ORIGINAL
                and _held_io._verify_scope_identity_original is _PIN_SCOPE_IDENTITY_ORIGINAL
                and _store_directories_verify_original is _PIN_ENTRY_DIRECTORIES_ORIGINAL):
            # The same fixed factory begins with the complete original stack
            # check, before inspecting selection fields or acquiring any file.
            return _enter_pin_original(self, resource)
        record = _stage_stack_original(self)
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
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def original_inputs():
    path = OLD / "r681-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(path.read_text(encoding="utf-8"))["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def replacements(eol):
    return [(before.encode().replace(b"\n", eol), after.encode().replace(b"\n", eol)) for before, after in (
        ("class _ClosingStack:\n", POINTERS + "class _ClosingStack:\n"), (BEFORE, AFTER))]


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert a.keys() == b.keys()
    assert [key for key in a if b[key] != a[key]] == ["_ClosingStack"]
    def members(raw):
        cls = next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == "_ClosingStack")
        return {getattr(n, "name", type(n).__name__) + str(i): ast.dump(n, include_attributes=False) for i, n in enumerate(cls.body)}
    x, y = members(old), members(new)
    assert x.keys() == y.keys()
    assert [key for key in x if y[key] != x[key]] == ["enter_context2"]
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    restored = new
    for before, after in reversed(replacements(eol)):
        assert restored.count(after) == 1
        restored = restored.replace(after, before, 1)
    assert restored == old
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
        assert raw.replace(b"r685", b"r681").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_entry_static_only", "original_inputs": inputs,
            "changed_definitions": ["_ClosingStack.enter_context"], "new_definitions": [],
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
    new = old
    for before, after in replacements(eol):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r681", b"r685").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r685\n\nFixed Pin entry delegates to the same original factory's complete leading stack check. Custom direct helpers and all non-Pin branches retain the original chain. Factory, acquisition, budgets, reads, accounting and cleanup unchanged. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r685-candidate-manifest.json"
    json_new(path, {"state": "pin_entry_frozen_static_only", "source_files": [fact(p) for p in sources],
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
