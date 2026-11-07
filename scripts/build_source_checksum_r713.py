"""Build an isolated batch source checksum candidate; file-only preparation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r711"
NEW = ROOT / "data/t16-runtime-origin-graph-r713"
OLD_PACKAGE = OLD / "authority_runtime_origin_r711"
PACKAGE = NEW / "authority_runtime_origin_r713"
OLD_MANIFEST_SHA = "a011b8faca62cbc4d6fcfdef8d2c4819212c80155ba3a928d5c88b77e3df3731"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''

# Only the fixed default batch consumes this same-expression observation.
_SOURCE_CHECKSUM_PIN_TYPE = _ClosedStorePin
_SOURCE_CHECKSUM_RAW = _ClosedStorePin.__dict__["raw"]
_SOURCE_CHECKSUM_SHA256 = _ClosedStorePin.__dict__["sha256"]
_SOURCE_CHECKSUM_OBSERVATION_TYPE = _held_io.FileObservation
_SOURCE_CHECKSUM_SHA = _sha
_SOURCE_CHECKSUM_PRE_BINDING = _pin_pre_profile_binding_original
_SOURCE_CHECKSUM_IDENTITY_BINDING = _pin_stack_identity_binding_original
_SOURCE_CHECKSUM_DIRECTORY_DISPATCH = _store_directory_sweep_dispatch_original
_SOURCE_CHECKSUM_RECORD_FIELDS = ("original", "stack", "directory", "name", "maximum", "budget",
    "kind", "lease", "scope", "execution_owner", "closed")
_SOURCE_CHECKSUM_CUSTODY_FIELDS = ("original", "execution_owner", "closed", "closing", "first",
    "scope", "pins", "held_original")
_SOURCE_CHECKSUM_SCOPE_TYPE = _held_io._ScopeRecord
_SOURCE_CHECKSUM_RESOURCE_TYPE = _held_io._OwnedRecord
_SOURCE_CHECKSUM_SCOPE_SLOTS = tuple((name, _held_io._ScopeRecord.__dict__[name])
    for name in _held_io._ScopeRecord.__slots__)
_SOURCE_CHECKSUM_RESOURCE_SLOTS = tuple((name, _held_io._OwnedRecord.__dict__[name])
    for name in _held_io._OwnedRecord.__slots__)
_SOURCE_CHECKSUM_MISSING = object()


def _source_checksum_dispatch_original(pin):
    return (_held_io is _PIN_PRE_HELD and ns is _PIN_PRE_NS
        and type(pin) is _SOURCE_CHECKSUM_PIN_TYPE and _ClosedStorePin is _SOURCE_CHECKSUM_PIN_TYPE
        and _ClosedStorePin.__dict__.get("raw") is _SOURCE_CHECKSUM_RAW
        and _ClosedStorePin.__dict__.get("sha256") is _SOURCE_CHECKSUM_SHA256
        and _ClosedStorePin.__dict__.get("_observation") is _PIN_VERIFY_METHOD_OBSERVATION_ORIGINAL
        and "__getattribute__" not in _ClosedStorePin.__dict__ and "__getattr__" not in _ClosedStorePin.__dict__
        and _pin_observation_original is _PIN_VERIFY_OBSERVATION_ORIGINAL
        and _pin_record_original is _PIN_RECORD_CHECK_ORIGINAL
        and _stage_stack_original is _PIN_STACK_CHECK_ORIGINAL
        and _pin_pre_profile_binding_original is _SOURCE_CHECKSUM_PRE_BINDING
        and _pin_stack_identity_binding_original is _SOURCE_CHECKSUM_IDENTITY_BINDING
        and _store_directory_sweep_dispatch_original is _SOURCE_CHECKSUM_DIRECTORY_DISPATCH
        and _store_directory_sweep_dispatch_original()
        and _source_checksum_binding_original is _SOURCE_CHECKSUM_BINDING
        and _held_io._verify_file_identity_original is _PIN_FILE_IDENTITY_ORIGINAL
        and _held_io._file_observation_original is _PIN_FILE_OBSERVATION_ORIGINAL
        and _held_io._resource_original is _PIN_RESOURCE_LOOKUP_ORIGINAL
        and _held_io._poll_record is _PIN_BUDGET_POLL_ORIGINAL
        and _held_io._remember_failure is _PIN_VERIFY_REMEMBER_ORIGINAL
        and _held_io.FileObservation is _SOURCE_CHECKSUM_OBSERVATION_TYPE
        and _sha is _SOURCE_CHECKSUM_SHA)


def _source_checksum_binding_original(pin):
    if (_source_checksum_dispatch_original is not _SOURCE_CHECKSUM_DISPATCH
            or not _source_checksum_dispatch_original(pin)):
        return None
    if ("__getattribute__" in _StorePinRecord.__dict__ or "__getattr__" in _StorePinRecord.__dict__
            or any(name in _StorePinRecord.__dict__ for name in _SOURCE_CHECKSUM_RECORD_FIELDS)
            or _held_io._ScopeRecord is not _SOURCE_CHECKSUM_SCOPE_TYPE
            or _held_io._OwnedRecord is not _SOURCE_CHECKSUM_RESOURCE_TYPE):
        return None
    for cls, slots in ((_SOURCE_CHECKSUM_SCOPE_TYPE, _SOURCE_CHECKSUM_SCOPE_SLOTS),
            (_SOURCE_CHECKSUM_RESOURCE_TYPE, _SOURCE_CHECKSUM_RESOURCE_SLOTS)):
        if ("__getattribute__" in cls.__dict__ or "__getattr__" in cls.__dict__
                or any(cls.__dict__.get(name) is not original for name, original in slots)):
            return None
    record = _PIN_RECORDS.get(id(pin))
    if type(record) is not _StorePinRecord:
        return None
    pin_fields = tuple(getattr(record, name, _SOURCE_CHECKSUM_MISSING) for name in _SOURCE_CHECKSUM_RECORD_FIELDS)
    if (any(value is _SOURCE_CHECKSUM_MISSING for value in pin_fields)
            or type(record.kind) is not str or record.kind != "source" or type(record.name) is not str):
        return None
    native = _pin_pre_profile_binding_original(record)
    if native is None:
        return None
    custody = _STACK_RECORDS.get(id(record.stack))
    if (type(custody) is not _StoreStackRecord
            or "__getattribute__" in _StoreStackRecord.__dict__ or "__getattr__" in _StoreStackRecord.__dict__
            or any(name in _StoreStackRecord.__dict__ for name in _SOURCE_CHECKSUM_CUSTODY_FIELDS)):
        return None
    custody_fields = tuple(getattr(custody, name, _SOURCE_CHECKSUM_MISSING)
        for name in _SOURCE_CHECKSUM_CUSTODY_FIELDS)
    if any(value is _SOURCE_CHECKSUM_MISSING for value in custody_fields):
        return None
    scope = _held_io._SCOPES.get(id(record.scope))
    if type(scope) is not _SOURCE_CHECKSUM_SCOPE_TYPE:
        return None
    scope_fields = tuple(getattr(scope, name, _SOURCE_CHECKSUM_MISSING) for name, _ in _SOURCE_CHECKSUM_SCOPE_SLOTS)
    if any(value is _SOURCE_CHECKSUM_MISSING for value in scope_fields) or type(scope.resources) is not list:
        return None
    if any(type(value) is not _SOURCE_CHECKSUM_RESOURCE_TYPE for value in scope.resources):
        return None
    all_resources = []
    for value in scope.resources:
        fields = tuple(getattr(value, name, _SOURCE_CHECKSUM_MISSING)
            for name, _ in _SOURCE_CHECKSUM_RESOURCE_SLOTS)
        if any(field is _SOURCE_CHECKSUM_MISSING for field in fields):
            return None
        all_resources.extend((value,) + fields)
    resource = _pin_stack_identity_binding_original(record)
    if type(resource) is not _SOURCE_CHECKSUM_RESOURCE_TYPE:
        return None
    resource_fields = tuple(getattr(resource, name, _SOURCE_CHECKSUM_MISSING)
        for name, _ in _SOURCE_CHECKSUM_RESOURCE_SLOTS)
    if any(value is _SOURCE_CHECKSUM_MISSING for value in resource_fields):
        return None
    if (type(resource.resource_kind) is not str or resource.resource_kind != "filehold"
            or type(resource.payload) is not bytes or type(resource.name) is not str
            or type(resource.sha256) is not str or resource.directory is not record.directory
            or resource.name != record.name or resource.scope is not record.scope or resource.lease is not record.lease):
        return None
    return (record, resource, scope) + pin_fields + custody_fields + scope_fields + tuple(all_resources) + resource_fields + native


def _source_pin_checksum_original(pin, expected):
    admitted = (_source_checksum_dispatch_original is _SOURCE_CHECKSUM_DISPATCH
        and _source_checksum_dispatch_original(pin))
    if not admitted:
        return pin.sha256 == expected and _sha(pin.raw) == expected
    # Preserve the original first property, complete observation and mismatch
    # short-circuit. No new binding, raw access or hash runs on a mismatch.
    matches = pin.sha256 == expected
    if not matches:
        return matches
    # Python selects the original _sha callable before evaluating pin.raw.
    checksum = _sha
    binding = (_source_checksum_binding_original(pin)
        if _source_checksum_dispatch_original is _SOURCE_CHECKSUM_DISPATCH
        and _source_checksum_dispatch_original(pin) else None)
    if binding is None:
        return checksum(pin.raw) == expected
    # Original fixed raw-observation prefix. Its whole second Store stack still
    # checks every scope resource, guard, directory, first and original budget.
    record = _PIN_RECORDS.get(id(pin))
    _need(type(pin) is _ClosedStorePin and type(record) is _StorePinRecord
        and record.original is pin and record.execution_owner == ns._owner() and not record.closed,
        "NATIVE_ORIGINAL_PIN_REQUIRED")
    _stage_stack_original(record.stack)
    if (_held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL
            or _held_io._file_observation_original is not _PIN_FILE_OBSERVATION_ORIGINAL):
        _held_io._verify_file_identity_original(record.scope, record.lease)
    current = (_source_checksum_binding_original(pin)
        if _source_checksum_dispatch_original is _SOURCE_CHECKSUM_DISPATCH
        and _source_checksum_dispatch_original(pin) else None)
    if (current is None or len(current) != len(binding)
            or not all(actual is before for actual, before in zip(current, binding))):
        # The second stack already returned. Continue only the original raw
        # producer; never replay that stack, the first SHA or acquisition.
        return checksum(_held_io._file_observation_original(record.scope, record.lease).raw) == expected
    actual = _held_io._resource_original(record.scope, record.lease, _held_io._FileLease)
    try:
        _held_io._poll_record(actual)
    except BaseException as failure:
        _held_io._remember_failure(actual, failure)
        raise
    after = (_source_checksum_binding_original(pin)
        if _source_checksum_dispatch_original is _SOURCE_CHECKSUM_DISPATCH
        and _source_checksum_dispatch_original(pin) else None)
    if (after is None or len(after) != len(binding)
            or not all(value is before for value, before in zip(after, binding)) or actual is not binding[1]):
        return checksum(_held_io._file_observation_original(record.scope, record.lease).raw) == expected
    # The just-completed full stack verified this same target's physical
    # identity. Keep its final real lookup/poll before hashing current bytes.
    return checksum(actual.payload) == expected


_SOURCE_CHECKSUM_BINDING = _source_checksum_binding_original
_SOURCE_CHECKSUM_DISPATCH = _source_checksum_dispatch_original
'''

BEFORE = '''        custody.pins += (record,)
        try:
            _need(pin.sha256 == expected and _sha(pin.raw) == expected, "CODEC_SOURCE_BINDING_CHANGED")
'''
AFTER = '''        custody.pins += (record,)
        try:
            _need(_source_pin_checksum_original(pin, expected), "CODEC_SOURCE_BINDING_CHANGED")
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
    path = OLD / "r711-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    manifest = json.loads(path.read_bytes())
    for row in manifest["source_files"] + manifest["artifacts"]:
        assert fact(ROOT / row["path"]) == row
    return manifest["source_files"]


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    return [(BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol))]


def helper_bytes(old):
    return HELPERS.encode().replace(b"\n", b"\r\n" if b"\r\n" in old else b"\n")


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    added = {"_source_checksum_dispatch_original", "_source_checksum_binding_original", "_source_pin_checksum_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if b[key] != a[key]] == ["_source_pin_batch_original"]
    helpers = helper_bytes(old)
    assert new.endswith(helpers)
    inverse = new[:-len(helpers)]
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
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
        assert raw.replace(b"r713", b"r711").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "source_checksum_static_only", "original_inputs": inputs,
        "changed_definitions": ["_source_pin_batch_original"], "new_definitions": sorted(added),
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
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    new += helper_bytes(old)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r711", b"r713").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r711", tofile="r713")).encode())
    write_new(NEW / "README.md", b"# r713\n\nThe fixed source-batch checksum preserves its original SHA getter and mismatch short-circuit, both full Store-stack observations, all real acquisition reads/hash/accounting and independent per-source/batch checks. Only the raw producer's repeated target identity and metadata object are merged after the complete second stack; genuine final lookup/poll/error bookkeeping remain before hashing current source bytes. Unknown/late dispatch continues the original remaining raw path. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r713-candidate-manifest.json"
    json_new(path, {"state": "source_checksum_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
