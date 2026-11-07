"""Build a fixed Pin-record identity consolidation in a new isolated package."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r677"
NEW = ROOT / "data/t16-runtime-origin-graph-r679"
OLD_PACKAGE = OLD / "authority_runtime_origin_r677"
PACKAGE = NEW / "authority_runtime_origin_r679"
OLD_MANIFEST_SHA = "8b448e956be3f75939d28ae2a7f44d3af46a033dd25fa9a946c57183d4192953"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
HELPER = '''def _pin_stack_identity_binding_original(record):
    if (_pin_record_original is not _PIN_RECORD_CHECK_ORIGINAL
            or _stage_stack_original is not _PIN_STACK_CHECK_ORIGINAL
            or _held_io._verify_scope_identity_original is not _PIN_SCOPE_IDENTITY_ORIGINAL
            or _held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL
            or _held_io._resource_original is not _PIN_RESOURCE_LOOKUP_ORIGINAL):
        return None
    custody = _STACK_RECORDS.get(id(record.stack))
    if (type(record) is not _StorePinRecord or type(record.original) is not _ClosedStorePin
            or _PIN_RECORDS.get(id(record.original)) is not record or record.closed
            or record.execution_owner != ns._owner()
            or type(custody) is not _StoreStackRecord or custody.original is not record.stack
            or custody.closed or custody.closing or custody.first is not None
            or custody.scope is not record.scope or not any(pin is record for pin in custody.pins)
            or _held_io._SCOPES.get(id(record.scope)) is not custody.held_original
            or custody.held_original.first is not None
            or type(record.lease) is not _held_io._FileLease
            or record.kind not in {"source", "pin"}
            or any(type(item) is not _held_io._OwnedRecord for item in custody.held_original.resources)):
        return None
    selected = [item for item in custody.held_original.resources if item.lease is record.lease]
    if (len(selected) == 1 and selected[0].scope is record.scope and not selected[0].finished
            and selected[0].resource_kind == ("filehold" if record.kind == "source" else "pin")):
        return selected[0]
    return None


'''
BEFORE = '''    _stage_stack_original(record.stack)
    _held_io._verify_file_identity_original(record.scope, record.lease)
    return record
'''
AFTER = '''    shared_resource = _pin_stack_identity_binding_original(record)
    _stage_stack_original(record.stack)
    if shared_resource is None:
        _held_io._verify_file_identity_original(record.scope, record.lease)
    else:
        # Keep the original late lookup, cancellation/deadline/first gate and
        # failure bookkeeping. Only the repeated physical identity is merged.
        current = _held_io._resource_original(record.scope, record.lease, _held_io._FileLease)
        try:
            _held_io._poll_record(current)
            _need(current is shared_resource
                and _pin_stack_identity_binding_original(record) is shared_resource,
                "NATIVE_ORIGINAL_PIN_REQUIRED")
        except BaseException as primary:
            _held_io._remember_failure(current, primary)
            raise
    return record
'''
POINTERS = '''_PIN_STACK_CHECK_ORIGINAL = _stage_stack_original
_PIN_SCOPE_IDENTITY_ORIGINAL = _held_io._verify_scope_identity_original
_PIN_RESOURCE_LOOKUP_ORIGINAL = _held_io._resource_original


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
    path = OLD / "r677-candidate-manifest.json"
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
        ("def _pin_record_original(pin):\n", HELPER + "def _pin_record_original(pin):\n"),
        (BEFORE, AFTER),
        ("def _directory_record_original(stack, directory):\n", POINTERS + "def _directory_record_original(stack, directory):\n"))]


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if b.get(key) != a[key]] == ["_pin_record_original"]
    assert set(b) - set(a) == {"_pin_stack_identity_binding_original"}
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
        assert raw.replace(b"r679", b"r677").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_record_identity_static_only", "original_inputs": inputs,
            "changed_definitions": ["_pin_record_original"], "new_definitions": ["_pin_stack_identity_binding_original"],
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
        p.write_bytes(p.read_bytes().replace(b"r677", b"r679").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r679\n\nMerge a duplicate Pin physical identity check only when the real original Store stack has checked the same registered held resource. Preserve late lease lookup, poll, failure bookkeeping and post binding. Custom or mismatched inputs keep the original full chain. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r679-candidate-manifest.json"
    json_new(path, {"state": "pin_record_identity_frozen_static_only", "source_files": [fact(p) for p in sources],
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
