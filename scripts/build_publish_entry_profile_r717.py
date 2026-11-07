"""File-only construction of the isolated metadata-entry profile observation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r715"
NEW = ROOT / "data/t16-runtime-origin-graph-r717"
OLD_PACKAGE = OLD / "authority_runtime_origin_r715"
PACKAGE = NEW / "authority_runtime_origin_r717"
OLD_MANIFEST_SHA = "005ae33a7aabf736685930441a334aa49c4738550909dfa0fed865e793cc57c3"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''

# One metadata entry owns both checks. These locals cannot authorize another
# call, effect, Stage scope, or a later physical directory observation.
from types import MemberDescriptorType as _PublishMemberDescriptor

_PUBLISH_ENTRY_STACK_TYPES = (_ClosingStack, _IoStack, _ReadStack)
_PUBLISH_ENTRY_STACK_MROS = tuple(cls.__mro__ for cls in _PUBLISH_ENTRY_STACK_TYPES)
_PUBLISH_ENTRY_STACK_FIELDS = ("budget", "closed", "store2", "native_owner", "retire_owner", "scope")
_PUBLISH_ENTRY_CUSTODY_FIELDS = ("original", "execution_owner", "budget", "namespace", "guard",
    "scope", "held_original", "native_owner", "store2", "retire_owner", "state", "first", "stage", "closed", "closing")
_PUBLISH_ENTRY_DICTS = tuple((cls, cls.__dict__["__dict__"]) for cls in
    (_ClosingStack, _StoreStackRecord, _BudgetRecord, v2._Budget))
_PUBLISH_ENTRY_DIRECTORY = _directory_record_original
_PUBLISH_ENTRY_STACK = _stage_stack_original
_PUBLISH_ENTRY_PRE_DISPATCH = _pin_pre_profile_dispatch_original
_PUBLISH_ENTRY_GUARD_BINDING = _pin_pre_profile_guard_binding_original
_PUBLISH_ENTRY_TAIL = _pin_verify_tail_state_original
_PUBLISH_ENTRY_DIRECTORIES = _store_directories_verify_original
_PUBLISH_ENTRY_DIRECTORY_DISPATCH = _store_directory_sweep_dispatch_original
_PUBLISH_ENTRY_OWNER = ns._owner
_PUBLISH_ENTRY_TASK_TYPE = ns.asyncio.Task
_PUBLISH_ENTRY_TASK_EQ = ns.asyncio.Task.__eq__
_PUBLISH_ENTRY_NS_BUDGET_DICT = ns._Budget.__dict__["__dict__"]
_PUBLISH_ENTRY_BUDGET_FIELDS = ("namespace_owner", "original_deadline", "original_started",
    "namespace_deadline", "deadline", "limits", "execution_owner", "extension")
_PUBLISH_ENTRY_LIMIT_TYPES = (v2.Limits, _ExtensionIOLimits)
_PUBLISH_ENTRY_LIMIT_SLOTS = tuple(tuple((name, cls.__dict__[name]) for name in cls.__slots__)
    for cls in _PUBLISH_ENTRY_LIMIT_TYPES)
_PUBLISH_ENTRY_LIMIT_METHODS = tuple(tuple((name, cls.__dict__.get(name))
    for name in ("__eq__", "__init__", "__new__", "__post_init__")) for cls in _PUBLISH_ENTRY_LIMIT_TYPES)
_PUBLISH_ENTRY_DEFAULT_LIMITS = v2.DEFAULT_LIMITS
_PUBLISH_ENTRY_MISSING = object()


def _publish_entry_profile_dispatch_original():
    return (ns is _PIN_PRE_NS and _held_io is _PIN_PRE_HELD and v2 is _PIN_PRE_V2 and safe is _PIN_PRE_SAFE
        and _StoreStackRecord is _PIN_PRE_CUSTODY_TYPE and _BudgetRecord is _PIN_PRE_RECORD_TYPE
        and _Budget is _PIN_PRE_BUDGET_TYPE and v2._Budget is _PIN_PRE_BUDGET_BASE
        and _ClosedStorePin is _SOURCE_CHECKSUM_PIN_TYPE
        and _PUBLISH_ENTRY_TASK_TYPE.__eq__ is _PUBLISH_ENTRY_TASK_EQ
        and v2.Limits is _PUBLISH_ENTRY_LIMIT_TYPES[0]
        and _ExtensionIOLimits is _PUBLISH_ENTRY_LIMIT_TYPES[1]
        and v2.DEFAULT_LIMITS is _PUBLISH_ENTRY_DEFAULT_LIMITS
        and _directory_record_original is _PUBLISH_ENTRY_DIRECTORY
        and _stage_stack_original is _PUBLISH_ENTRY_STACK
        and _pin_pre_profile_dispatch_original is _PUBLISH_ENTRY_PRE_DISPATCH
        and _pin_pre_profile_guard_binding_original is _PUBLISH_ENTRY_GUARD_BINDING
        and _pin_verify_tail_state_original is _PUBLISH_ENTRY_TAIL
        and _store_directories_verify_original is _PUBLISH_ENTRY_DIRECTORIES
        and _store_directory_sweep_dispatch_original is _PUBLISH_ENTRY_DIRECTORY_DISPATCH
        and ns._owner is _PUBLISH_ENTRY_OWNER
        and all(current is expected for current, expected in zip(
            (_ClosingStack, _IoStack, _ReadStack), _PUBLISH_ENTRY_STACK_TYPES))
        and all(cls.__mro__ == mro and "__getattribute__" not in cls.__dict__
            and "__getattr__" not in cls.__dict__
            and all(name not in cls.__dict__ for name in _PUBLISH_ENTRY_STACK_FIELDS + ("state", "execution_owner"))
            for cls, mro in zip(_PUBLISH_ENTRY_STACK_TYPES, _PUBLISH_ENTRY_STACK_MROS))
        and all("__dict__" not in cls.__dict__ for cls in (_IoStack, _ReadStack, _Budget))
        and all(cls.__dict__.get("__dict__") is original for cls, original in _PUBLISH_ENTRY_DICTS)
        and all(name not in _StoreStackRecord.__dict__ for name in _PUBLISH_ENTRY_CUSTODY_FIELDS)
        and _pin_pre_profile_dispatch_original()
        and all(name not in cls.__dict__ for cls in (_Budget, v2._Budget)
            for name in _PUBLISH_ENTRY_BUDGET_FIELDS)
        and ns._Budget.__dict__.get("__dict__") is _PUBLISH_ENTRY_NS_BUDGET_DICT
        and "__getattribute__" not in ns._Budget.__dict__ and "__getattr__" not in ns._Budget.__dict__
        and "started" not in ns._Budget.__dict__
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            and all(cls.__dict__.get(name) is original for name, original in slots + methods)
            for cls, slots, methods in zip(_PUBLISH_ENTRY_LIMIT_TYPES,
                _PUBLISH_ENTRY_LIMIT_SLOTS, _PUBLISH_ENTRY_LIMIT_METHODS))
        and type(_ExtensionIOLimits.__init__.__defaults__) is tuple
        and all(type(value) is int for value in _ExtensionIOLimits.__init__.__defaults__)
        and _store_directory_sweep_dispatch_original())


def _publish_entry_profile_binding_original(stack):
    # Unknown descriptors never run during admission. In particular, an
    # existing Store/Budget/Stage first failure remains the original check's.
    if (_publish_entry_profile_dispatch_original is not _PUBLISH_ENTRY_DISPATCH
            or not _publish_entry_profile_dispatch_original()
            or not any(type(stack) is cls for cls in _PUBLISH_ENTRY_STACK_TYPES)):
        return None
    custody = _STACK_RECORDS.get(id(stack))
    if type(custody) is not _PIN_PRE_CUSTODY_TYPE:
        return None
    values = object.__getattribute__(custody, "__dict__")
    if values.get("first", _PUBLISH_ENTRY_MISSING) is not None:
        return None
    stage = values.get("stage", _PUBLISH_ENTRY_MISSING)
    stage_descriptor = None
    if stage is _PUBLISH_ENTRY_MISSING:
        return None
    if stage is not None:
        cls = type(stage)
        if (type(cls) is not type or cls.__mro__ != (cls, object)
                or "__getattribute__" in cls.__dict__ or "__getattr__" in cls.__dict__):
            return None
        stage_descriptor = cls.__dict__.get("first_failure")
        if (type(stage_descriptor) is not _PublishMemberDescriptor
                or stage_descriptor.__objclass__ is not cls or stage_descriptor.__name__ != "first_failure"):
            return None
        try:
            stage_first = stage_descriptor.__get__(stage, cls)
        except AttributeError:
            return None
        if stage_first is not None:
            return None
    budget = values.get("budget", _PUBLISH_ENTRY_MISSING)
    selected = _BUDGET_RECORDS.get(id(budget))
    if type(budget) is not _PIN_PRE_BUDGET_TYPE or type(selected) is not _PIN_PRE_RECORD_TYPE:
        return None
    selected_values = object.__getattribute__(selected, "__dict__")
    if selected_values.get("first", _PUBLISH_ENTRY_MISSING) is not None:
        return None
    stack_values = object.__getattribute__(stack, "__dict__")
    custody_fields = tuple(values.get(name, _PUBLISH_ENTRY_MISSING) for name in _PUBLISH_ENTRY_CUSTODY_FIELDS)
    stack_fields = tuple(stack_values.get(name, _PUBLISH_ENTRY_MISSING) for name in _PUBLISH_ENTRY_STACK_FIELDS)
    selected_fields = tuple(selected_values.get(name, _PUBLISH_ENTRY_MISSING) for name in _PIN_PRE_RECORD_FIELDS)
    budget_values = object.__getattribute__(budget, "__dict__")
    budget_fields = tuple(budget_values.get(name, _PUBLISH_ENTRY_MISSING) for name in _PUBLISH_ENTRY_BUDGET_FIELDS)
    if any(value is _PUBLISH_ENTRY_MISSING for value in custody_fields + stack_fields + selected_fields + budget_fields):
        return None
    # These checks only select the optimization. Unknown comparison operands
    # retain the whole original second profile check, including a custom
    # equality method which might change physical bytes without changing any
    # of the object references captured above.
    binding = selected_values["binding"]
    if (type(binding) is not tuple or len(binding) != 6
            or any(type(value) is not float for value in binding[2:5])
            or binding[0] is not budget.namespace_owner or binding[1] is not budget.original_deadline
            or type(budget.original_started) is not float or type(budget.namespace_deadline) is not float
            or type(budget.deadline) is not float or type(budget.extension) is not bool
            or type(ns.DEADLINE_SECONDS) is not float or ns.DEADLINE_SECONDS != 30.0
            or type(budget.original_deadline) is not _PIN_PRE_NS_BUDGET_TYPE):
        return None
    original_values = object.__getattribute__(budget.original_deadline, "__dict__")
    original_started = original_values.get("started", _PUBLISH_ENTRY_MISSING)
    if type(original_started) is not float:
        return None
    limits = budget.limits
    if (not any(type(limits) is cls for cls in _PUBLISH_ENTRY_LIMIT_TYPES)
            or binding[5] is not limits):
        return None
    limit_values = []
    for selected_limits in (limits, _PUBLISH_ENTRY_DEFAULT_LIMITS):
        cls_index = 0 if type(selected_limits) is _PUBLISH_ENTRY_LIMIT_TYPES[0] else 1
        fields = tuple(getattr(selected_limits, name, _PUBLISH_ENTRY_MISSING)
            for name, _ in _PUBLISH_ENTRY_LIMIT_SLOTS[cls_index])
        if any(type(value) is not int for value in fields):
            return None
        limit_values.extend((selected_limits,) + fields)
    owners = (custody.execution_owner, selected.execution_owner, budget.execution_owner)
    if type(stack) is _IoStack:
        owners += (stack_values.get("execution_owner", _PUBLISH_ENTRY_MISSING),)
    if any(type(owner) is not tuple or len(owner) != 3
            or type(owner[0]) is not int or type(owner[1]) is not int
            or (owner[2] is not None and type(owner[2]) is not _PUBLISH_ENTRY_TASK_TYPE) for owner in owners):
        return None
    actual = custody.namespace
    if (type(actual) is not _PIN_PRE_NS_TYPES[0] or custody.original is not stack
            or custody.closed is not False or custody.closing is not False
            or stack.budget is not budget or selected.original is not budget
            or selected.acquisition is not actual):
        return None
    namespace_fields = tuple(getattr(actual, name, _PUBLISH_ENTRY_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[0])
    if any(value is _PUBLISH_ENTRY_MISSING for value in namespace_fields):
        return None
    namespace_stack = actual.stack
    namespace_record = ns._CLOSED_STACK_RECORDS.get(id(namespace_stack))
    if type(namespace_stack) is not _PIN_PRE_NS_TYPES[1] or type(namespace_record) is not _PIN_PRE_NS_TYPES[2]:
        return None
    namespace_stack_fields = tuple(getattr(namespace_stack, name, _PUBLISH_ENTRY_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[1])
    namespace_record_fields = tuple(getattr(namespace_record, name, _PUBLISH_ENTRY_MISSING) for name, _ in _PIN_PRE_NS_SLOTS[2])
    if any(value is _PUBLISH_ENTRY_MISSING for value in namespace_stack_fields + namespace_record_fields):
        return None
    if (budget.namespace_owner is not actual.original or budget.original_deadline is not actual.budget
            or ns._CLOSED_NAMESPACE_OWNERS.get(id(actual.original)) is not actual
            or namespace_record.original is not namespace_stack
            or namespace_stack.owner is not actual.original
            or namespace_stack.profile is not actual.profile or namespace_record.profile is not actual.profile
            or namespace_stack.guard is not custody.guard or namespace_record.guard is not custody.guard
            or actual.storage_guard is not custody.guard or namespace_stack.budget is not actual.budget
            or namespace_record.budget is not actual.budget or selected.namespace_stack is not namespace_record
            or namespace_stack.scope is not namespace_record.scope
            or _held_io._SCOPES.get(id(namespace_record.scope)) is not selected.namespace_scope):
        return None
    guard_binding = _pin_pre_profile_guard_binding_original(stack)
    if guard_binding is None:
        return None
    guard_owner = custody.guard._owner
    if (type(guard_owner) is not tuple or len(guard_owner) != 3
            or type(guard_owner[0]) is not int or type(guard_owner[1]) is not int
            or (guard_owner[2] is not None and type(guard_owner[2]) is not _PUBLISH_ENTRY_TASK_TYPE)):
        return None
    return (stack, custody, budget, selected, actual, namespace_stack, namespace_record, stage, stage_descriptor,
        _STACK_RECORDS, _BUDGET_RECORDS, ns._CLOSED_NAMESPACE_OWNERS, ns._CLOSED_STACK_RECORDS,
        _held_io._SCOPES) + custody_fields + stack_fields + selected_fields + budget_fields + (
        namespace_fields + namespace_stack_fields + namespace_record_fields + guard_binding
        + (original_started, binding, guard_owner) + tuple(limit_values) + owners)


def _publish_store_directory_entry_original(stack, directory, budget):
    # Preserve Python's selection of the original _need callee before its
    # stack argument is evaluated, even if that full check changes dispatch.
    need = _need
    before = (_publish_entry_profile_binding_original(stack)
        if _publish_entry_profile_binding_original is _PUBLISH_ENTRY_BINDING else None)
    first = _stage_stack_original(stack)
    need(first.budget is budget, "STAGE_STORE_SCOPE_REQUIRED")
    after = (_publish_entry_profile_binding_original(stack)
        if before is not None and _publish_entry_profile_binding_original is _PUBLISH_ENTRY_BINDING else None)
    if (after is None or len(after) != len(before)
            or not all(current is expected for current, expected in zip(after, before))):
        return _directory_record_original(stack, directory)
    # Only the second profile's immediately repeated physical samples are
    # shared. The second state, complete held sweep and Store directories run.
    record = _pin_verify_tail_state_original(stack)
    _held_io._verify_scope_identity_original(record.scope)
    _store_directories_verify_original(record)
    original = ns._CLOSED_DIRECTORY_RECORDS.get(id(directory))
    _need(type(original) is ns._ClosedDirectoryRecord and original.original is directory,
        "NATIVE_ORIGINAL_DIRECTORY_CHANGED")
    ns._closed_directory_verify_original(original)
    current = directory
    for _ in range(128):
        if current is record.namespace.directory:
            return original
        _need(current.parent is not None and ns._CLOSED_DIRECTORY_RECORDS.get(id(current.parent)) is not None,
            "NATIVE_ORIGINAL_DIRECTORY_CHANGED")
        current = current.parent
    raise v2.StoreError("NATIVE_ORIGINAL_DIRECTORY_CHANGED")


_PUBLISH_ENTRY_DISPATCH = _publish_entry_profile_dispatch_original
_PUBLISH_ENTRY_BINDING = _publish_entry_profile_binding_original
'''

BEFORE = '''def _publish_store_metadata_original(stack, directory, name, raw, budget):
    _need(_stage_stack_original(stack).budget is budget, "STAGE_STORE_SCOPE_REQUIRED")
    _directory_record_original(stack, directory)
'''
AFTER = '''def _publish_store_metadata_original(stack, directory, name, raw, budget):
    _publish_store_directory_entry_original(stack, directory, budget)
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
    path = OLD / "r715-candidate-manifest.json"
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


def nodes(raw):
    return {node.name: node for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def dump(value):
    return ast.dump(value, include_attributes=False)


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = nodes(old), nodes(new)
    added = {"_publish_entry_profile_dispatch_original", "_publish_entry_profile_binding_original", "_publish_store_directory_entry_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if dump(b[key]) != dump(a[key])] == ["_publish_store_metadata_original"]
    helpers = helper_bytes(old)
    assert new.endswith(helpers)
    inverse = new[:-len(helpers)]
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
    entry = b["_publish_store_directory_entry_original"]
    suffix_start = next(i for i, row in enumerate(entry.body) if isinstance(row, ast.Assign)
        and isinstance(row.targets[0], ast.Name) and row.targets[0].id == "original")
    assert [dump(row) for row in entry.body[suffix_start:]] == [dump(row) for row in a["_directory_record_original"].body[1:]]
    assert [dump(row) for row in b["_publish_store_metadata_original"].body[1:]] == [dump(row) for row in a["_publish_store_metadata_original"].body[2:]]
    full = a["_stage_stack_original"]
    first_held = next(i for i, row in enumerate(entry.body) if isinstance(row, ast.Expr)
        and isinstance(row.value, ast.Call) and isinstance(row.value.func, ast.Attribute)
        and row.value.func.attr == "_verify_scope_identity_original")
    assert [dump(row) for row in entry.body[first_held:first_held+2]] == [dump(row) for row in full.body[-3:-1]]
    # Undo the local need/first extraction in its AST to recover the original
    # first prefix, including the unchanged callee-before-stack evaluation.
    selected = next(row for row in entry.body if isinstance(row, ast.Expr) and isinstance(row.value, ast.Call)
        and isinstance(row.value.func, ast.Name) and row.value.func.id == "need")
    original_need = ast.parse(ast.unparse(selected)).body[0]
    original_need.value.func.id = "_need"
    original_need.value.args[0].left.value = ast.parse("_stage_stack_original(stack)", mode="eval").body
    assert dump(original_need) == dump(a["_publish_store_metadata_original"].body[0])
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
        assert raw.replace(b"r717", b"r715").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "publish_entry_profile_static_only", "original_inputs": inputs,
        "changed_definitions": ["_publish_store_metadata_original"], "new_definitions": sorted(added),
        "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
        "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
        "store_inverse_bytes_equal": True, "lookup_suffix_ast_equal": True,
        "publish_suffix_ast_equal": True, "second_held_and_directories_ast_equal": True,
        "first_need_extraction_inverse_ast_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    if NEW.exists():
        assert {p.name for p in NEW.iterdir()} == {"draft-before-admission-order"}
    else:
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
        p.write_bytes(p.read_bytes().replace(b"r715", b"r717").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r715", tofile="r717")).encode())
    write_new(NEW / "README.md", b"# r717\n\nOne fixed metadata-entry helper owns the original first full stack and budget selection, then merges only the second profile's adjacent duplicate physical observation after exact actual binding checks. The second state, complete held resource checks, Store directory sweep, target physical verification and 128-ancestor walk remain. Original payload validation and later Stage creation retain their order. Unknown and late dispatch follow the original remaining path without replay. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r717-candidate-manifest.json"
    json_new(path, {"state": "publish_entry_profile_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
