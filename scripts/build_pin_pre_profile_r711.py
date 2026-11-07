"""Build an isolated Pin pre-state profile candidate; file-only preparation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r709"
NEW = ROOT / "data/t16-runtime-origin-graph-r711"
OLD_PACKAGE = OLD / "authority_runtime_origin_r709"
PACKAGE = NEW / "authority_runtime_origin_r711"
OLD_MANIFEST_SHA = "ae9fa10fc28bca6ceebb1f9e4e5ccf6975e22d32b7b7cb3dabe9231dde6e428f"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''

# One Pin verifier owns these expected objects across its original Budget call.
# No observation, skip flag or caller-provided authorization is accepted.
_PIN_PRE_NS = ns
_PIN_PRE_HELD = _held_io
_PIN_PRE_V2 = v2
_PIN_PRE_SAFE = safe
_PIN_PRE_FULL_STATE = _pin_verify_stack_state_original
_PIN_PRE_TAIL_STATE = _pin_verify_tail_state_original
_PIN_PRE_GUARD_BINDING = _pin_verify_guard_binding_original
_PIN_PRE_GUARD_DISPATCH = _pin_verify_guard_dispatch_original
_PIN_PRE_VERIFY_DISPATCH = _pin_verify_dispatch_original
_PIN_PRE_NATIVE_DEADLINE = _native_deadline_check
_PIN_PRE_NS_CALLS = tuple((name, getattr(ns, name)) for name in (
    "_closed_stack_check_original", "_closed_stack_custody_original",
    "_closed_stack_directories_verify_batch_original", "_closed_stack_capture_first_original"))
_PIN_PRE_NS_BUDGET_TYPE = ns._Budget
_PIN_PRE_NS_CANCEL_TYPE = ns._ClosedCancellation
_PIN_PRE_NS_BUDGET_CHECK = ns._Budget.check
_PIN_PRE_NS_CANCEL = ns._ClosedCancellation.__call__
_PIN_PRE_HELD_IDENTITY = _held_io._verify_scope_identity_original
_PIN_PRE_V2_BUDGET_CHECK = v2._Budget.check
_PIN_PRE_PATH_TYPE = ns.lifecycle._PROFILE_PATH_TYPE
_PIN_PRE_BUFFER_TYPE = ns.lifecycle._LOCK_STREAM_TYPE
_PIN_PRE_RAW_TYPE = ns.lifecycle._LOCK_RAW_TYPE
_PIN_PRE_GUARD_FIELDS = ("_profiles", "_profile_ids", "_exclusive", "_locks", "_owner", "_active")
_PIN_PRE_LOCK_FIELDS = ("directory", "stream", "identity", "locked")
_PIN_PRE_DIRECTORY_FIELDS = ("path", "base", "create", "parent", "handles", "identities", "fd")
_PIN_PRE_PIN_TYPE = _StorePinRecord
_PIN_PRE_BUDGET_TYPE = _Budget
_PIN_PRE_BUDGET_BASE = v2._Budget
_PIN_PRE_RECORD_TYPE = _BudgetRecord
_PIN_PRE_CUSTODY_TYPE = _StoreStackRecord
_PIN_PRE_PIN_FIELDS = ("original", "stack", "budget")
_PIN_PRE_BUDGET_FIELDS = ("namespace_owner", "original_deadline")
_PIN_PRE_RECORD_FIELDS = ("original", "acquisition", "execution_owner", "binding",
    "namespace_stack", "namespace_scope", "stacks", "first")
_PIN_PRE_CUSTODY_FIELDS = ("original", "budget", "namespace", "guard", "scope", "held_original")
_PIN_PRE_NS_TYPES = (ns._ClosedNamespaceAcquisition, ns._ClosedNamespaceStack, ns._ClosedStackRecord)
_PIN_PRE_NS_SLOTS = tuple(tuple((name, cls.__dict__[name]) for name in cls.__slots__)
    for cls in _PIN_PRE_NS_TYPES)
_PIN_PRE_MISSING = object()


def _pin_pre_profile_dispatch_original():
    return (ns is _PIN_PRE_NS and _held_io is _PIN_PRE_HELD and v2 is _PIN_PRE_V2 and safe is _PIN_PRE_SAFE
        and vars(ns).get("lifecycle") is _PIN_TAIL_LIFECYCLE
        and _pin_verify_stack_state_original is _PIN_PRE_FULL_STATE
        and _pin_verify_tail_state_original is _PIN_PRE_TAIL_STATE
        and _pin_verify_guard_binding_original is _PIN_PRE_GUARD_BINDING
        and _pin_verify_guard_dispatch_original is _PIN_PRE_GUARD_DISPATCH
        and _pin_verify_dispatch_original is _PIN_PRE_VERIFY_DISPATCH
        and _native_deadline_check is _PIN_PRE_NATIVE_DEADLINE
        and all(vars(ns).get(name) is original for name, original in _PIN_PRE_NS_CALLS)
        and vars(ns).get("_Budget") is _PIN_PRE_NS_BUDGET_TYPE
        and vars(ns).get("_ClosedCancellation") is _PIN_PRE_NS_CANCEL_TYPE
        and ns._Budget.__dict__.get("check") is _PIN_PRE_NS_BUDGET_CHECK
        and ns._ClosedCancellation.__dict__.get("__call__") is _PIN_PRE_NS_CANCEL
        and _held_io._verify_scope_identity_original is _PIN_PRE_HELD_IDENTITY
        and v2._Budget is _PIN_PRE_BUDGET_BASE and v2._Budget.__dict__.get("check") is _PIN_PRE_V2_BUDGET_CHECK
        and _StorePinRecord is _PIN_PRE_PIN_TYPE and _Budget is _PIN_PRE_BUDGET_TYPE
        and _BudgetRecord is _PIN_PRE_RECORD_TYPE and _StoreStackRecord is _PIN_PRE_CUSTODY_TYPE
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            and all(name not in cls.__dict__ for name in fields) for cls, fields in (
                (_PIN_PRE_PIN_TYPE, _PIN_PRE_PIN_FIELDS), (_PIN_PRE_BUDGET_TYPE, _PIN_PRE_BUDGET_FIELDS),
                (_PIN_PRE_BUDGET_BASE, _PIN_PRE_BUDGET_FIELDS), (_PIN_PRE_RECORD_TYPE, _PIN_PRE_RECORD_FIELDS),
                (_PIN_PRE_CUSTODY_TYPE, _PIN_PRE_CUSTODY_FIELDS)))
        and all(current is expected for current, expected in zip(
            (ns._ClosedNamespaceAcquisition, ns._ClosedNamespaceStack, ns._ClosedStackRecord), _PIN_PRE_NS_TYPES))
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            and all(cls.__dict__.get(name) is original for name, original in slots)
            for cls, slots in zip(_PIN_PRE_NS_TYPES, _PIN_PRE_NS_SLOTS))
        and type(_PIN_RECORDS) is dict and type(_BUDGET_RECORDS) is dict and type(_STACK_RECORDS) is dict
        and type(ns._CLOSED_NAMESPACE_OWNERS) is dict and type(ns._CLOSED_STACK_RECORDS) is dict
        and type(_held_io._SCOPES) is dict
        and _pin_verify_dispatch_original())


def _pin_pre_profile_guard_binding_original(stack):
    custody = _STACK_RECORDS.get(id(stack))
    if type(custody) is not _PIN_PRE_CUSTODY_TYPE:
        return None
    life, guard, profile = ns.lifecycle, custody.guard, custody.namespace.profile
    if (life is not _PIN_TAIL_LIFECYCLE or life.StorageGuard is not _PIN_TAIL_GUARD_TYPE
            or life.StorageProfile is not _PIN_TAIL_PROFILE_TYPE or life._FileLock is not _PIN_TAIL_LOCK_TYPE
            or safe._Directory is not _PIN_TAIL_DIRECTORY_TYPE
            or life._PROFILE_PATH_TYPE is not _PIN_PRE_PATH_TYPE
            or life._LOCK_STREAM_TYPE is not _PIN_PRE_BUFFER_TYPE or life._LOCK_RAW_TYPE is not _PIN_PRE_RAW_TYPE
            or type(guard) is not _PIN_TAIL_GUARD_TYPE):
        return None
    for cls, fields in ((_PIN_TAIL_GUARD_TYPE, _PIN_PRE_GUARD_FIELDS),
            (_PIN_TAIL_PROFILE_TYPE, _PIN_TAIL_PROFILE_FIELDS), (_PIN_TAIL_LOCK_TYPE, _PIN_PRE_LOCK_FIELDS),
            (_PIN_TAIL_DIRECTORY_TYPE, _PIN_PRE_DIRECTORY_FIELDS)):
        if ("__getattribute__" in cls.__dict__ or "__getattr__" in cls.__dict__
                or any(name in cls.__dict__ for name in fields)):
            return None
    if any(getattr(guard, name, _PIN_PRE_MISSING) is _PIN_PRE_MISSING for name in _PIN_PRE_GUARD_FIELDS):
        return None
    if (type(guard._profiles) is not tuple or type(guard._profile_ids) is not frozenset
            or type(guard._exclusive) is not bool or type(guard._locks) is not tuple
            or type(guard._owner) is not tuple or type(guard._active) is not bool):
        return None
    for selected in (profile,) + guard._profiles:
        if (type(selected) is not _PIN_TAIL_PROFILE_TYPE
                or any(getattr(selected, name, _PIN_PRE_MISSING) is _PIN_PRE_MISSING
                    for name in _PIN_TAIL_PROFILE_FIELDS)):
            return None
    directory_values = []
    for lock in guard._locks:
        if (type(lock) is not _PIN_TAIL_LOCK_TYPE
                or any(getattr(lock, name, _PIN_PRE_MISSING) is _PIN_PRE_MISSING for name in _PIN_PRE_LOCK_FIELDS)
                or type(lock.directory) is not _PIN_TAIL_DIRECTORY_TYPE
                or type(lock.stream) is not _PIN_PRE_BUFFER_TYPE or type(lock.stream.raw) is not _PIN_PRE_RAW_TYPE):
            return None
        directory = lock.directory
        fields = tuple(getattr(directory, name, _PIN_PRE_MISSING) for name in _PIN_PRE_DIRECTORY_FIELDS)
        if (any(value is _PIN_PRE_MISSING for value in fields)
                or type(directory.handles) is not list or type(directory.identities) is not list
                or directory.parent is not None or any(type(handle) is not int for handle in directory.handles)
                or (directory.fd is not None and type(directory.fd) is not int)
                or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not _PIN_PRE_PATH_TYPE
                    or type(row[1]) is not int or type(row[2]) is not int for row in directory.identities)):
            return None
        directory_values.extend((directory,) + fields)
        directory_values.extend(directory.handles)
        directory_values.extend(directory.identities)
    if not _pin_verify_guard_dispatch_original(guard):
        return None
    guard_values = _pin_verify_guard_binding_original(stack)
    return None if guard_values is None else guard_values + tuple(directory_values)


def _pin_pre_profile_binding_original(record):
    # Read the selected Budget's ordinary first field before any guard/profile
    # getter. The original Budget remains solely responsible for raising it.
    if (type(record) is not _PIN_PRE_PIN_TYPE or type(_BUDGET_RECORDS) is not dict
            or _pin_pre_profile_dispatch_original is not _PIN_PRE_DISPATCH
            or _pin_pre_profile_guard_binding_original is not _PIN_PRE_GUARD_BINDING_HELPER):
        return None
    pin_values = object.__getattribute__(record, "__dict__")
    budget = pin_values.get("budget", _PIN_PRE_MISSING)
    selected = _BUDGET_RECORDS.get(id(budget))
    if type(selected) is not _PIN_PRE_RECORD_TYPE:
        return None
    selected_values = object.__getattribute__(selected, "__dict__")
    if selected_values.get("first", _PIN_PRE_MISSING) is not None:
        return None
    if not _pin_pre_profile_dispatch_original():
        return None
    pin_fields = tuple(getattr(record, name, _PIN_PRE_MISSING) for name in _PIN_PRE_PIN_FIELDS)
    if any(value is _PIN_PRE_MISSING for value in pin_fields) or type(budget) is not _PIN_PRE_BUDGET_TYPE:
        return None
    custody = _STACK_RECORDS.get(id(record.stack))
    if type(custody) is not _PIN_PRE_CUSTODY_TYPE:
        return None
    custody_fields = tuple(getattr(custody, name, _PIN_PRE_MISSING) for name in _PIN_PRE_CUSTODY_FIELDS)
    if any(value is _PIN_PRE_MISSING for value in custody_fields):
        return None
    stack_record = _BUDGET_RECORDS.get(id(custody.budget))
    if type(stack_record) is not _PIN_PRE_RECORD_TYPE or type(custody.budget) is not _PIN_PRE_BUDGET_TYPE:
        return None
    selected_fields = tuple(getattr(selected, name, _PIN_PRE_MISSING) for name in _PIN_PRE_RECORD_FIELDS)
    stack_fields = tuple(getattr(stack_record, name, _PIN_PRE_MISSING) for name in _PIN_PRE_RECORD_FIELDS)
    budget_fields = tuple(getattr(value, name, _PIN_PRE_MISSING)
        for value in (budget, custody.budget) for name in _PIN_PRE_BUDGET_FIELDS)
    if any(value is _PIN_PRE_MISSING for value in selected_fields + stack_fields + budget_fields):
        return None
    actual = custody.namespace
    if (type(actual) is not _PIN_PRE_NS_TYPES[0] or selected.original is not budget
            or stack_record.original is not custody.budget or selected.acquisition is not actual
            or stack_record.acquisition is not actual or stack_record.first is not None
            or _PIN_RECORDS.get(id(record.original)) is not record or custody.original is not record.stack):
        return None
    namespace_fields = tuple(getattr(actual, name, _PIN_PRE_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[0])
    if any(value is _PIN_PRE_MISSING for value in namespace_fields):
        return None
    namespace_stack = actual.stack
    namespace_record = ns._CLOSED_STACK_RECORDS.get(id(namespace_stack))
    if type(namespace_stack) is not _PIN_PRE_NS_TYPES[1] or type(namespace_record) is not _PIN_PRE_NS_TYPES[2]:
        return None
    namespace_stack_fields = tuple(getattr(namespace_stack, name, _PIN_PRE_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[1])
    namespace_record_fields = tuple(getattr(namespace_record, name, _PIN_PRE_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[2])
    if any(value is _PIN_PRE_MISSING for value in namespace_stack_fields + namespace_record_fields):
        return None
    if (budget.namespace_owner is not actual.original or custody.budget.namespace_owner is not actual.original
            or budget.original_deadline is not actual.budget or custody.budget.original_deadline is not actual.budget
            or ns._CLOSED_NAMESPACE_OWNERS.get(id(actual.original)) is not actual
            or namespace_record.original is not namespace_stack
            or namespace_stack.owner is not actual.original
            or namespace_stack.profile is not actual.profile or namespace_record.profile is not actual.profile
            or namespace_stack.guard is not custody.guard or namespace_record.guard is not custody.guard
            or actual.storage_guard is not custody.guard or namespace_stack.budget is not actual.budget
            or namespace_record.budget is not actual.budget
            or selected.namespace_stack is not namespace_record or stack_record.namespace_stack is not namespace_record
            or namespace_stack.scope is not namespace_record.scope
            or selected.namespace_scope is not stack_record.namespace_scope
            or _held_io._SCOPES.get(id(namespace_record.scope)) is not selected.namespace_scope):
        return None
    guard_binding = _pin_pre_profile_guard_binding_original(record.stack)
    if guard_binding is None:
        return None
    return (record, budget, selected, custody, stack_record, actual, namespace_stack, namespace_record,
        _PIN_RECORDS, _BUDGET_RECORDS, _STACK_RECORDS, ns._CLOSED_NAMESPACE_OWNERS,
        ns._CLOSED_STACK_RECORDS, _held_io._SCOPES) + pin_fields + custody_fields + selected_fields + stack_fields + (
        budget_fields + namespace_fields + namespace_stack_fields + namespace_record_fields + guard_binding)


_PIN_PRE_DISPATCH = _pin_pre_profile_dispatch_original
_PIN_PRE_GUARD_BINDING_HELPER = _pin_pre_profile_guard_binding_original
_PIN_PRE_BINDING = _pin_pre_profile_binding_original
'''

BEFORE_BUDGET = '''    record = _pin_record_original(pin)
    try:
        _Budget.check(record.budget)
'''
AFTER_BUDGET = '''    record = _pin_record_original(pin)
    try:
        profile_binding = (_pin_pre_profile_binding_original(record)
            if shared is not None and record is initial
            and _pin_pre_profile_binding_original is _PIN_PRE_BINDING else None)
        _Budget.check(record.budget)
'''
BEFORE_STATE = '''            custody = _pin_verify_stack_state_original(record.stack)
            scope = _held_io._scope_original(custody.scope)
'''
AFTER_STATE = '''            profile_current = (_pin_pre_profile_binding_original(record)
                if profile_binding is not None and _pin_pre_profile_binding_original is _PIN_PRE_BINDING else None)
            custody = (_pin_verify_tail_state_original(record.stack)
                if profile_current is not None and len(profile_current) == len(profile_binding)
                and all(current is expected for current, expected in zip(profile_current, profile_binding))
                else _pin_verify_stack_state_original(record.stack))
            scope = _held_io._scope_original(custody.scope)
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
    path = OLD / "r709-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    manifest = json.loads(path.read_bytes())
    for row in manifest["source_files"] + manifest["artifacts"]:
        assert fact(ROOT / row["path"]) == row
    return manifest["source_files"]


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    pairs = ((BEFORE_BUDGET, AFTER_BUDGET), (BEFORE_STATE, AFTER_STATE))
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in pairs]


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
    added = {"_pin_pre_profile_dispatch_original", "_pin_pre_profile_guard_binding_original", "_pin_pre_profile_binding_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if b[key] != a[key]] == ["_verify_pin_original"]
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
        assert raw.replace(b"r711", b"r709").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_pre_profile_static_only", "original_inputs": inputs,
        "changed_definitions": ["_verify_pin_original"], "new_definitions": sorted(added),
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
        p.write_bytes(p.read_bytes().replace(b"r709", b"r711").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r709", tofile="r711")).encode())
    write_new(NEW / "README.md", b"# r711\n\nSame-Pin pre-state profile observation after the complete original Budget.check. Genuine selected and Stack budgets may differ but must share the same registered Namespace acquisition, stack, profile and guard. Unknown/late dispatch retains the original remaining full state call. Original record/check/body/tail/directory/read/hash/charge/cleanup remain. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r711-candidate-manifest.json"
    json_new(path, {"state": "pin_pre_profile_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
