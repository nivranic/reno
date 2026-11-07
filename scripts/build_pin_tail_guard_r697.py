"""Build a fresh Pin-tail candidate; no product imports or execution."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r695"
NEW = ROOT / "data/t16-runtime-origin-graph-r697"
OLD_PACKAGE = OLD / "authority_runtime_origin_r695"
PACKAGE = NEW / "authority_runtime_origin_r697"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
HELPERS = '''_PIN_TAIL_STATE_ORIGINAL = _pin_verify_stack_state_original
_PIN_TAIL_LIFECYCLE = ns.lifecycle
_PIN_TAIL_CONTEXT = ns.lifecycle._CURRENT
_PIN_TAIL_REQUIRE_GUARD = ns.lifecycle.require_guard
_PIN_TAIL_PROFILE = ns.lifecycle._profile
_PIN_TAIL_KEY = ns.lifecycle._key
_PIN_TAIL_OWNER = ns.lifecycle._owner
_PIN_TAIL_DISPATCH = ns.lifecycle._guard_has_original_dispatch
_PIN_TAIL_METHOD = ns.lifecycle._original_bound_method
_PIN_TAIL_LOCK_DISPATCH = ns.lifecycle._original_lock_dispatch
_PIN_TAIL_LOCK_GROUP = ns.lifecycle._verify_lock_group
_PIN_TAIL_DATABASE = ns.lifecycle._database
_PIN_TAIL_GUARD_TYPE = ns.lifecycle.StorageGuard
_PIN_TAIL_PROFILE_TYPE = ns.lifecycle.StorageProfile
_PIN_TAIL_LOCK_TYPE = ns.lifecycle._FileLock
_PIN_TAIL_DIRECTORY_TYPE = safe._Directory
_PIN_TAIL_GUARD_VERIFY = ns.lifecycle.StorageGuard.verify
_PIN_TAIL_GUARD_ALLOWS = ns.lifecycle.StorageGuard.allows
_PIN_TAIL_PROFILE_IDENTITY = ns.lifecycle.StorageProfile.identity
_PIN_TAIL_PROFILE_FIELDS = ("database", "media", "reports", "checkpoints")


def _pin_verify_guard_dispatch_original(guard):
    life = ns.lifecycle
    # Check descriptors without invoking unknown getters. This admission is
    # never a physical observation or a replacement for require_guard.
    return (life is _PIN_TAIL_LIFECYCLE and life._CURRENT is _PIN_TAIL_CONTEXT
        and ns._require_profile is _PIN_VERIFY_REQUIRE_PROFILE_ORIGINAL
        and life.require_guard is _PIN_TAIL_REQUIRE_GUARD
        and life._profile is _PIN_TAIL_PROFILE and life._key is _PIN_TAIL_KEY
        and life._owner is _PIN_TAIL_OWNER
        and life._guard_has_original_dispatch is _PIN_TAIL_DISPATCH
        and life._original_bound_method is _PIN_TAIL_METHOD
        and life._original_lock_dispatch is _PIN_TAIL_LOCK_DISPATCH
        and life._verify_lock_group is _PIN_TAIL_LOCK_GROUP and life._database is _PIN_TAIL_DATABASE
        and life.StorageGuard is _PIN_TAIL_GUARD_TYPE and type(guard) is _PIN_TAIL_GUARD_TYPE
        and life.StorageProfile is _PIN_TAIL_PROFILE_TYPE
        and life._FileLock is _PIN_TAIL_LOCK_TYPE and safe._Directory is _PIN_TAIL_DIRECTORY_TYPE
        and life.StorageGuard.__dict__.get("verify") is _PIN_TAIL_GUARD_VERIFY
        and life.StorageGuard.__dict__.get("allows") is _PIN_TAIL_GUARD_ALLOWS
        and life.StorageGuard.__dict__.get("__getattribute__") is None
        and all(life.StorageGuard.__dict__.get(name) is None
            for name in ("_profiles", "_profile_ids", "_exclusive", "_locks", "_owner", "_active"))
        and life.StorageProfile.__dict__.get("__getattribute__") is None
        and life.StorageProfile.__dict__.get("identity") is _PIN_TAIL_PROFILE_IDENTITY
        and all(life.StorageProfile.__dict__.get(name) is None for name in _PIN_TAIL_PROFILE_FIELDS)
        and life._FileLock.__dict__.get("verify") is life._FILE_LOCK_VERIFY_ORIGINAL
        and life._FileLock.__dict__.get("verify_stat") is life._FILE_LOCK_STAT_ORIGINAL
        and life._FileLock.__dict__.get("_verify_entry_stat") is life._FILE_LOCK_ENTRY_ORIGINAL
        and life._FileLock.__dict__.get("__getattribute__") is None
        and all(life._FileLock.__dict__.get(name) is None
            for name in ("directory", "stream", "identity", "locked"))
        and safe._Directory.__dict__.get("entry_stat") is life._DIRECTORY_ENTRY_ORIGINAL
        and life._guard_has_original_dispatch(guard))


def _pin_verify_guard_binding_original(stack):
    record = _STACK_RECORDS.get(id(stack))
    if (type(record) is not _StoreStackRecord or record.original is not stack
            or _pin_verify_stack_state_original is not _PIN_TAIL_STATE_ORIGINAL
            or not _pin_verify_guard_dispatch_original(record.guard)):
        return None
    guard, profile = record.guard, record.namespace.profile
    if (ns.lifecycle._CURRENT.get() is not guard or type(profile) is not _PIN_TAIL_PROFILE_TYPE
            or any(type(getattr(profile, name)) is not ns.lifecycle._PROFILE_PATH_TYPE
                   for name in _PIN_TAIL_PROFILE_FIELDS)):
        return None
    # Private expected object bindings, held only in this verifier's locals.
    # No caller can submit them to a checker or use them to authorize an effect.
    values = [record, record.namespace, guard, profile, guard._owner, guard._profiles,
        guard._profile_ids, guard._locks, guard._exclusive, guard._active]
    for selected in (profile,) + guard._profiles:
        values.append(selected)
        values.extend(getattr(selected, name) for name in _PIN_TAIL_PROFILE_FIELDS)
    for lock in guard._locks:
        values.extend((lock, lock.directory, lock.stream, lock.stream.raw, lock.identity, lock.locked))
    return tuple(values)


def _pin_verify_tail_state_original(stack):
    record = _STACK_RECORDS.get(id(stack))
    _need(type(stack) in {_ClosingStack, _IoStack, _ReadStack} and type(record) is _StoreStackRecord
        and record.original is stack and record.execution_owner == ns._owner() and not record.closed
        and stack.budget is record.budget and stack.scope is record.scope
        and stack.native_owner is record.native_owner and stack.store2 is record.store2
        and stack.retire_owner is record.retire_owner and stack.closed is False,
        "FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED")
    if record.first is not None:
        raise record.first
    if record.stage is not None and record.stage.first_failure is not None:
        raise record.stage.first_failure
    if type(stack) is _IoStack:
        _need(stack.state is record.state and stack.execution_owner == record.execution_owner,
            "FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED")
    _budget_record_original(record.budget)
    life, guard, profile = ns.lifecycle, record.guard, record.namespace.profile
    if _pin_verify_guard_dispatch_original(guard) and life._CURRENT.get() is guard:
        ns._need(type(profile) is life.StorageProfile, "NAMESPACE_PROFILE_INVALID")
        if not guard._active:
            life._fail("STORAGE_GUARD_REQUIRED")
        if guard._owner != life._owner():
            life._fail("STORAGE_GUARD_CONTEXT")
        if profile.identity not in guard._profile_ids:
            life._fail("STORAGE_PROFILE_MISMATCH")
    else:
        # The prefix already ran once. Keep the remaining original full call
        # for unknown dispatch or a different current guard, without replay.
        guard = ns._require_profile(profile)
    _need(guard is record.guard
        and ns._CLOSED_NAMESPACE_OWNERS.get(id(record.namespace.original)) is record.namespace,
        "NATIVE_OWNER_BUDGET_CHANGED")
    return record


'''
BEFORE_BINDING = '''            selected_budget, binding = record.budget, shared.budget
            before = _held_io._file_observation_original(record.scope, record.lease).observed_bytes
'''
AFTER_BINDING = '''            selected_budget, binding = record.budget, shared.budget
            guard_expected = _pin_verify_guard_binding_original(record.stack)
            before = _held_io._file_observation_original(record.scope, record.lease).observed_bytes
'''
BEFORE_TAIL = '''            current = _pin_verify_stack_state_original(record.stack)
            try:
'''
AFTER_TAIL = '''            if (guard_expected is not None
                    and _pin_verify_stack_state_original is _PIN_TAIL_STATE_ORIGINAL):
                current = _pin_verify_tail_state_original(record.stack)
                guard_current = _pin_verify_guard_binding_original(record.stack)
                _need(guard_current is not None and len(guard_current) == len(guard_expected)
                    and all(actual is expected for actual, expected in zip(guard_current, guard_expected)),
                    "NATIVE_ORIGINAL_GUARD_CHANGED")
            else:
                current = _pin_verify_stack_state_original(record.stack)
            try:
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


def inputs():
    manifest = OLD / "r695-candidate-manifest.json"
    assert digest(manifest.read_bytes()) == "ec20558a42fe470bc3f32841bd9a8c69254d33b48e8acbec84b74c475abb8283"
    rows = json.loads(manifest.read_bytes())["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def replacements(raw):
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in
        (("def _verify_pin_original(pin):\n", HELPERS + "def _verify_pin_original(pin):\n"),
         (BEFORE_BINDING, AFTER_BINDING), (BEFORE_TAIL, AFTER_TAIL))]


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def validate():
    original = inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if a[key] != b[key]] == ["_verify_pin_original"]
    added = {"_pin_verify_guard_dispatch_original", "_pin_verify_guard_binding_original", "_pin_verify_tail_state_original"}
    assert set(b) - set(a) == added
    restored = new
    for before, after in reversed(replacements(old)):
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
        assert raw.replace(b"r697", b"r695").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_tail_guard_static_only", "original_inputs": original,
            "changed_definitions": ["_verify_pin_original"], "new_definitions": sorted(added),
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "store_inverse_bytes_equal": True, "consumer_inverse_bytes_equal": True,
            "compiled_without_execution": 20, "product_execution": False}


def prepare():
    original = inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in original:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r695", b"r697").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": original})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True), fromfile="r695", tofile="r697")).encode())
    write_new(NEW / "README.md", b"# r697\n\nRemove only the added fixed Pin tail's extra physical guard pass. Original complete prechecks, logical ownership and local binding checks, unknown dispatch fallback, actual reads/hash, charging and cleanup remain. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r697-candidate-manifest.json"
    json_new(path, {"state": "pin_tail_guard_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
