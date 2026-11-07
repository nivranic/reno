"""Build an isolated candidate with only the fixed Stage directory check changed."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r697"
NEW = ROOT / "data/t16-runtime-origin-graph-r701"
OLD_PACKAGE = OLD / "authority_runtime_origin_r697"
PACKAGE = NEW / "authority_runtime_origin_r701"
OLD_MANIFEST_SHA = "927530e18fbefcad89b950757344fbd532520b6a8bbbedcc1b4952f027db9b0e"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = 'def _directory_original(scope, directory):\n    _scope_live(scope)\n    _need(type(directory) is safe._Directory, "STAGE_ORIGINAL_DIRECTORY_REQUIRED")\n    if scope.mode == "store":\n        return store._directory_record_original(scope.stack, directory)\n    entries = ns._store_original_resource_custody(scope.stack).resources\n    record = next((item for item in entries if type(item) is ns._StoreDirectoryClose\n        and item.original is directory), None)\n    _need(type(record) is ns._StoreDirectoryClose and record.original is directory,\n        "STAGE_ORIGINAL_DIRECTORY_REQUIRED")\n    ns._store_directory_close_matches(record)\n    _DIRECTORY_VERIFY(directory)\n    return record\n'
AFTER = '_DIRECTORY_SCOPE_LIVE_ORIGINAL = _scope_live\n_DIRECTORY_SCOPE_BINDING_ORIGINAL = _store_scope_identity_binding_original\n_DIRECTORY_SCOPE_TYPE = _StageScope\n_DIRECTORY_SCOPE_SLOTS = tuple((name, _StageScope.__dict__[name]) for name in _StageScope.__slots__)\n_DIRECTORY_STORE_RECORD_TYPE = store._StoreStackRecord\n_DIRECTORY_RECORD_FIELDS = ("original", "execution_owner", "budget", "namespace", "guard",\n    "cancellation", "clock", "scope", "held_original", "directories", "pins", "children",\n    "owned_walks", "native_owner", "store2", "retire_owner", "state", "first", "stage", "closed", "closing")\n_DIRECTORY_ENTRY_CLOCK = _CLOCK\n_DIRECTORY_ENTRY_CANCEL = _CANCEL_POLL\n_DIRECTORY_ENTRY_HELD_SCOPE = _HELD_SCOPE\n_DIRECTORY_ENTRY_NEED = _need\n_DIRECTORY_ENTRY_STORE_CALLS = tuple((name, getattr(store, name)) for name in\n    ("_budget_record_original", "_bound_budget", "_state", "_store_directories_verify_original", "_need"))\n_DIRECTORY_ENTRY_OWNER = ns._owner\n_DIRECTORY_ENTRY_PROFILE = ns._require_profile\n_DIRECTORY_MISSING = object()\n_DIRECTORY_ENTRY_GUARD_DISPATCH = store._pin_verify_guard_dispatch_original\n_DIRECTORY_STACK_TYPES = (store._ClosingStack, store._IoStack, store._ReadStack)\n_DIRECTORY_STACK_FIELDS = ("budget", "scope", "native_owner", "store2", "retire_owner", "closed")\n_DIRECTORY_STACK_EXTRA_FIELDS = ("state", "execution_owner")\n_DIRECTORY_HELD_CALLS = tuple((name, getattr(held, name)) for name in\n    ("_scope_original", "_verify_identity_record", "_verify_lock_record"))\n_DIRECTORY_NESTED_TYPES = (ns._ClosedNamespaceAcquisition, held._ScopeRecord, held._OwnedRecord)\n_DIRECTORY_NESTED_SLOTS = tuple(tuple((name, cls.__dict__[name]) for name in cls.__slots__)\n    for cls in _DIRECTORY_NESTED_TYPES)\n\n\ndef _directory_store_dispatch_original():\n    return (_scope_live is _DIRECTORY_SCOPE_LIVE_ORIGINAL\n        and _store_scope_identity_binding_original is _DIRECTORY_SCOPE_BINDING_ORIGINAL\n        and _verify_store_stage_scope_original is _STORE_STAGE_VERIFY_ORIGINAL\n        and store._stage_stack_original is _STORE_STACK_LIVE_ORIGINAL\n        and store._directory_record_original is _STORE_DIRECTORY_LOOKUP_ORIGINAL\n        and ns._closed_directory_verify_original is _NAMESPACE_DIRECTORY_CHECK_ORIGINAL\n        and store.ns is ns and store._held_io is held\n        and _HELD_IDENTITY is _SCOPE_IDENTITY_ORIGINAL\n        and held._verify_scope_identity_original is _SCOPE_IDENTITY_ORIGINAL\n        and _CLOCK is _DIRECTORY_ENTRY_CLOCK and _CANCEL_POLL is _DIRECTORY_ENTRY_CANCEL\n        and _HELD_SCOPE is _DIRECTORY_ENTRY_HELD_SCOPE and _need is _DIRECTORY_ENTRY_NEED\n        and ns._owner is _DIRECTORY_ENTRY_OWNER and ns._require_profile is _DIRECTORY_ENTRY_PROFILE\n        and all(getattr(store, name) is original for name, original in _DIRECTORY_ENTRY_STORE_CALLS)\n        and _StageScope is _DIRECTORY_SCOPE_TYPE\n        and _StageScope.__dict__.get("__getattribute__") is None\n        and _StageScope.__dict__.get("__getattr__") is None\n        and all(_StageScope.__dict__.get(name) is original for name, original in _DIRECTORY_SCOPE_SLOTS)\n        and store._StoreStackRecord is _DIRECTORY_STORE_RECORD_TYPE\n        and store._StoreStackRecord.__dict__.get("__getattribute__") is None\n        and store._StoreStackRecord.__dict__.get("__getattr__") is None\n        and all(store._StoreStackRecord.__dict__.get(name) is None for name in _DIRECTORY_RECORD_FIELDS)\n        and store._pin_verify_guard_dispatch_original is _DIRECTORY_ENTRY_GUARD_DISPATCH\n        and all(actual is original for actual, original in zip(\n            (store._ClosingStack, store._IoStack, store._ReadStack), _DIRECTORY_STACK_TYPES))\n        and all(cls.__dict__.get("__getattribute__") is None\n            and cls.__dict__.get("__getattr__") is None\n            and all(cls.__dict__.get(name) is None\n                for name in _DIRECTORY_STACK_FIELDS + _DIRECTORY_STACK_EXTRA_FIELDS)\n            for cls in _DIRECTORY_STACK_TYPES)\n        and all(getattr(held, name) is original for name, original in _DIRECTORY_HELD_CALLS)\n        and all(actual is original for actual, original in zip(\n            (ns._ClosedNamespaceAcquisition, held._ScopeRecord, held._OwnedRecord), _DIRECTORY_NESTED_TYPES))\n        and all(cls.__dict__.get("__getattribute__") is None and cls.__dict__.get("__getattr__") is None\n            and all(cls.__dict__.get(name) is original for name, original in slots)\n            for cls, slots in zip(_DIRECTORY_NESTED_TYPES, _DIRECTORY_NESTED_SLOTS))\n        and type(_SCOPES) is dict and type(store._STACK_RECORDS) is dict\n        and type(ns._CLOSED_NAMESPACE_OWNERS) is dict and type(held._SCOPES) is dict)\n\n\ndef _directory_store_binding_original(scope):\n    # Pure actual-object selection. Unknown descriptors are rejected before\n    # any field access. This tuple is local comparison data, never permission.\n    if not _directory_store_dispatch_original() or type(scope) is not _DIRECTORY_SCOPE_TYPE:\n        return None\n    if _SCOPES.get(id(scope)) is not scope:\n        return None\n    scope_values = tuple(getattr(scope, name, _DIRECTORY_MISSING) for name, _ in _DIRECTORY_SCOPE_SLOTS)\n    if any(value is _DIRECTORY_MISSING for value in scope_values):\n        return None\n    if type(scope.mode) is not str or scope.mode != "store":\n        return None\n    custody = store._STACK_RECORDS.get(id(scope.stack))\n    if type(custody) is not _DIRECTORY_STORE_RECORD_TYPE or custody is not scope.permit:\n        return None\n    stack = scope.stack\n    if type(stack) not in _DIRECTORY_STACK_TYPES:\n        return None\n    selected = [custody]\n    selected.extend(scope_values)\n    selected.extend(getattr(custody, name, _DIRECTORY_MISSING) for name in _DIRECTORY_RECORD_FIELDS)\n    selected.extend(getattr(stack, name, _DIRECTORY_MISSING) for name in _DIRECTORY_STACK_FIELDS)\n    if type(stack) is store._IoStack:\n        selected.extend(getattr(stack, name, _DIRECTORY_MISSING) for name in _DIRECTORY_STACK_EXTRA_FIELDS)\n    if any(value is _DIRECTORY_MISSING for value in selected):\n        return None\n    if (type(custody.namespace) is not _DIRECTORY_NESTED_TYPES[0]\n            or type(custody.held_original) is not _DIRECTORY_NESTED_TYPES[1]):\n        return None\n    for value, slots in zip((custody.namespace, custody.held_original), _DIRECTORY_NESTED_SLOTS):\n        selected.extend(getattr(value, name, _DIRECTORY_MISSING) for name, _ in slots)\n    if any(value is _DIRECTORY_MISSING for value in selected):\n        return None\n    resources = custody.held_original.resources\n    if type(resources) is not list:\n        return None\n    selected.append(resources)\n    for resource in resources:\n        if type(resource) is not _DIRECTORY_NESTED_TYPES[2]:\n            return None\n        selected.append(resource)\n        selected.extend(getattr(resource, name, _DIRECTORY_MISSING) for name, _ in _DIRECTORY_NESTED_SLOTS[2])\n    if custody.stage is not None:\n        if type(custody.stage) is not _DIRECTORY_SCOPE_TYPE:\n            return None\n        selected.append(getattr(custody.stage, "first_failure", _DIRECTORY_MISSING))\n    if any(value is _DIRECTORY_MISSING for value in selected):\n        return None\n    # Only this entry\'s actual registry members; unrelated scopes are not read.\n    selected.extend((_SCOPES, _SCOPES.get(id(scope)), store._STACK_RECORDS,\n        store._STACK_RECORDS.get(id(stack)), ns._CLOSED_NAMESPACE_OWNERS,\n        ns._CLOSED_NAMESPACE_OWNERS.get(id(custody.namespace.original)), held._SCOPES,\n        held._SCOPES.get(id(scope.held_scope))))\n    return tuple(selected)\n\n\ndef _directory_original(scope, directory):\n    binding = _directory_store_binding_original(scope)\n    _scope_live(scope)\n    _need(type(directory) is safe._Directory, "STAGE_ORIGINAL_DIRECTORY_REQUIRED")\n    if scope.mode == "store":\n        current_binding = _directory_store_binding_original(scope) if binding is not None else None\n        if (current_binding is None or len(current_binding) != len(binding)\n                or any(current is not expected for current, expected in zip(current_binding, binding))\n                or not store._pin_verify_guard_dispatch_original(current_binding[0].guard)):\n            # Unknown or late dispatch keeps precisely the remaining original\n            # lookup; the complete Stage prefix is never replayed.\n            return store._directory_record_original(scope.stack, directory)\n        record = binding[0]\n        original = ns._CLOSED_DIRECTORY_RECORDS.get(id(directory))\n        store._need(type(original) is ns._ClosedDirectoryRecord and original.original is directory,\n            "NATIVE_ORIGINAL_DIRECTORY_CHANGED")\n        ns._closed_directory_verify_original(original)\n        current = directory\n        for _ in range(128):\n            if current is record.namespace.directory:\n                return original\n            store._need(current.parent is not None and ns._CLOSED_DIRECTORY_RECORDS.get(id(current.parent)) is not None,\n                "NATIVE_ORIGINAL_DIRECTORY_CHANGED")\n            current = current.parent\n        raise store.v2.StoreError("NATIVE_ORIGINAL_DIRECTORY_CHANGED")\n    entries = ns._store_original_resource_custody(scope.stack).resources\n    record = next((item for item in entries if type(item) is ns._StoreDirectoryClose\n        and item.original is directory), None)\n    _need(type(record) is ns._StoreDirectoryClose and record.original is directory,\n        "STAGE_ORIGINAL_DIRECTORY_REQUIRED")\n    ns._store_directory_close_matches(record)\n    _DIRECTORY_VERIFY(directory)\n    return record\n'


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
    path = OLD / "r697-candidate-manifest.json"
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
    assert set(b) - set(a) == {"_directory_store_dispatch_original", "_directory_store_binding_original"}
    assert [key for key in a if b[key] != a[key]] == ["_directory_original"]
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    assert new.replace(AFTER.encode().replace(b"\n", eol), BEFORE.encode().replace(b"\n", eol), 1) == old
    same = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name != "owned_stage_io_r251.py":
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r701", b"r697").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "stage_directory_static_only", "original_inputs": inputs,
            "changed_definitions": ["_directory_original"], "new_definitions": ["_directory_store_dispatch_original", "_directory_store_binding_original"],
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "stage_inverse_bytes_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "owned_stage_io_r251.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    before, after = BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol)
    assert old.count(before) == 1
    new = old.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r697", b"r701").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "stage.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r701\n\nOne fixed Stage directory entry shares its own complete Store stack check with the adjacent original directory lookup. Real directory checks and ancestry remain. Unknown or late dispatch keeps the remaining original lookup. All other Stage definitions and support modules unchanged. Static preparation only.\n")
    print(json.dumps({"stage": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "stage.diff", "README.md")]
    path = NEW / "r701-candidate-manifest.json"
    json_new(path, {"state": "stage_directory_frozen_static_only", "source_files": [fact(p) for p in sources],
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
