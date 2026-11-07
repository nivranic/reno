"""Build an isolated Stage effect profile candidate; file-only preparation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r705"
NEW = ROOT / "data/t16-runtime-origin-graph-r709"
OLD_PACKAGE = OLD / "authority_runtime_origin_r705"
PACKAGE = NEW / "authority_runtime_origin_r709"
OLD_MANIFEST_SHA = "0fcb36ec9d8c7d39c1017dd32a51016549341299beef28f758a2c7013408a366"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''_EFFECT_STORE_MODULE = store
_EFFECT_NS_MODULE = ns
_EFFECT_V2_MODULE = store.v2
_EFFECT_INSTALL_BINDING = _stage_install_guard_binding_original
_EFFECT_NATIVE = _N2_EFFECT
_EFFECT_STORE_CALLS = tuple((name, getattr(store, name)) for name in (
    "_native_namespace_verify", "_native_effect_scope_dispatch_original",
    "_native_effect_budget_dispatch_original", "_native_effect_budget_inside_original",
    "_native_effect_budget_after_original", "_namespace_verification", "_native_deadline_check",
    "_budget_record_original", "_remember_budget_first_original"))
_EFFECT_NS_CALLS = tuple((name, getattr(ns, name)) for name in (
    "_verify_closed_namespace_owner_runtime_native", "_verify_closed_namespace_owner_runtime_body_original",
    "_closed_runtime_directory_observation_original", "_runtime_directory_begin_original",
    "_runtime_directory_finish_original", "_runtime_directory_check_original",
    "_runtime_directory_binding_original", "_runtime_directory_replace_original",
    "_runtime_body_binding_original", "_runtime_body_profile_original",
    "_runtime_body_stack_check_original", "_runtime_body_write_check_original",
    "_closed_stack_check_original", "_closed_stack_custody_original",
    "_closed_stack_capture_first_original", "_require_runtime_namespace_write_original", "_need"))
_EFFECT_LIFE_CALLS = tuple((name, getattr(ns.lifecycle, name)) for name in (
    "_capture_guard_file_plan_original", "_verify_guard_plan_shape_original",
    "_verify_guard_plan_context_original", "_verify_guard_plan_state_original",
    "_verify_guard_plan_files_original", "_verify_guard_plan_directories_original", "_fail"))
_EFFECT_CLASS_METHODS = tuple((cls, tuple((name, cls.__dict__[name]) for name in names)) for cls, names in (
    (ns._ExitFailures, ("__init__", "capture", "raise_first")),
    (store._Budget, ("check",)), (store.v2._Budget, ("check",)),
    (ns._Budget, ("check",)), (ns._ClosedCancellation, ("__call__",))))
_EFFECT_NS_CLASSES = tuple((name, getattr(ns, name)) for name in (
    "_ExitFailures", "_ClosedRuntimeDirectoryObservation", "_ClosedRuntimeDirectoryBinding",
    "_ClosedRuntimeClosingObservation", "_Budget", "_ClosedCancellation"))
_EFFECT_BUDGET_TYPE = store._Budget
_EFFECT_BUDGET_BASE = store.v2._Budget
_EFFECT_BUDGET_RECORD_TYPE = store._BudgetRecord
_EFFECT_BUDGET_FIELDS = ("namespace_owner", "original_deadline")
_EFFECT_BUDGET_RECORD_FIELDS = ("original", "acquisition", "execution_owner", "binding",
    "namespace_stack", "namespace_scope", "stacks", "first")
_EFFECT_RUNTIME_CURRENT = ns._RUNTIME_DIRECTORY_CURRENT
_EFFECT_RUNTIME_CONTEXT = ns._RUNTIME_DIRECTORY_CONTEXT
_EFFECT_RUNTIME_LOCK = ns._RUNTIME_DIRECTORY_LOCK
_EFFECT_GUARD_PLAN_TYPE = ns.lifecycle._GuardFilePlan
_EFFECT_GUARD_PLAN_SLOTS = tuple((name, ns.lifecycle._GuardFilePlan.__dict__[name])
    for name in ns.lifecycle._GuardFilePlan.__slots__)
_EFFECT_MISSING = object()


def _stage_effect_profile_dispatch_original():
    # Admission reads only original module/class dictionaries, never a custom
    # guard property or the retired Namespace observation token.
    return (store is _EFFECT_STORE_MODULE and ns is _EFFECT_NS_MODULE
        and vars(ns).get("lifecycle") is _INSTALL_LIFECYCLE and vars(store).get("v2") is _EFFECT_V2_MODULE
        and _stage_install_guard_binding_original is _EFFECT_INSTALL_BINDING
        and _N2_EFFECT is _EFFECT_NATIVE
        and all(vars(store).get(name, _EFFECT_MISSING) is original for name, original in _EFFECT_STORE_CALLS)
        and all(vars(ns).get(name, _EFFECT_MISSING) is original for name, original in _EFFECT_NS_CALLS)
        and all(vars(ns.lifecycle).get(name, _EFFECT_MISSING) is original for name, original in _EFFECT_LIFE_CALLS)
        and all(vars(ns).get(name, _EFFECT_MISSING) is original for name, original in _EFFECT_NS_CLASSES)
        and store._Budget is _EFFECT_BUDGET_TYPE and store.v2._Budget is _EFFECT_BUDGET_BASE
        and store._BudgetRecord is _EFFECT_BUDGET_RECORD_TYPE
        and all(cls.__dict__.get(name) is original for cls, methods in _EFFECT_CLASS_METHODS
            for name, original in methods)
        and ns._RUNTIME_DIRECTORY_CURRENT is _EFFECT_RUNTIME_CURRENT
        and ns._RUNTIME_DIRECTORY_CONTEXT is _EFFECT_RUNTIME_CONTEXT
        and ns._RUNTIME_DIRECTORY_LOCK is _EFFECT_RUNTIME_LOCK
        and type(ns._RUNTIME_DIRECTORY_SCOPES) is dict
        and type(store._BUDGET_RECORDS) is dict
        and ns.lifecycle._GuardFilePlan is _EFFECT_GUARD_PLAN_TYPE
        and "__getattribute__" not in _EFFECT_GUARD_PLAN_TYPE.__dict__
        and "__getattr__" not in _EFFECT_GUARD_PLAN_TYPE.__dict__
        and all(_EFFECT_GUARD_PLAN_TYPE.__dict__.get(name) is original
            for name, original in _EFFECT_GUARD_PLAN_SLOTS)
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            and all(name not in cls.__dict__ for name in fields) for cls, fields in (
                (_EFFECT_BUDGET_TYPE, _EFFECT_BUDGET_FIELDS), (_EFFECT_BUDGET_BASE, _EFFECT_BUDGET_FIELDS),
                (_EFFECT_BUDGET_RECORD_TYPE, _EFFECT_BUDGET_RECORD_FIELDS))))


def _stage_effect_profile_binding_original(scope):
    if not _stage_effect_profile_dispatch_original():
        return None
    binding = _stage_install_guard_binding_original(scope)
    if binding is None:
        return None
    custody = binding[0]
    budget = custody.budget
    if type(budget) is not _EFFECT_BUDGET_TYPE:
        return None
    budget_values = tuple(getattr(budget, name, _EFFECT_MISSING) for name in _EFFECT_BUDGET_FIELDS)
    record = store._BUDGET_RECORDS.get(id(budget))
    if type(record) is not _EFFECT_BUDGET_RECORD_TYPE:
        return None
    record_values = tuple(getattr(record, name, _EFFECT_MISSING) for name in _EFFECT_BUDGET_RECORD_FIELDS)
    if any(value is _EFFECT_MISSING for value in budget_values + record_values):
        return None
    actual = custody.namespace
    if (record.original is not budget or record.acquisition is not actual or record.first is not None
            or budget.namespace_owner is not actual.original or budget.original_deadline is not actual.budget
            or ns._CLOSED_NAMESPACE_OWNERS.get(id(budget.namespace_owner)) is not actual
            or actual.storage_guard is not custody.guard
            or ns._RUNTIME_DIRECTORY_CURRENT.get() is not None
            or ns._RUNTIME_DIRECTORY_CONTEXT.get() is not None):
        return None
    return binding + (store._BUDGET_RECORDS, record, ns._RUNTIME_DIRECTORY_SCOPES) + budget_values + record_values


def _stage_effect_profile_state_original(scope, actual):
    # Re-resolve the real registry objects here. No supplied binding, cached
    # observation or retired Namespace token is accepted as authorization.
    custody = store._STACK_RECORDS.get(id(scope.stack))
    record = store._BUDGET_RECORDS.get(id(scope.budget))
    if (type(scope) is not _StageScope or _SCOPES.get(id(scope)) is not scope
            or type(custody) is not store._StoreStackRecord or custody is not scope.permit
            or custody.original is not scope.stack or custody.budget is not scope.budget
            or type(record) is not store._BudgetRecord or record.original is not scope.budget
            or actual is not custody.namespace or actual is not record.acquisition
            or ns._CLOSED_NAMESPACE_OWNERS.get(id(scope.budget.namespace_owner)) is not actual):
        return ns._require_profile(actual.profile)
    life, profile = ns.lifecycle, actual.profile
    ns._need(type(profile) is life.StorageProfile, "NAMESPACE_PROFILE_INVALID")
    guard = life._CURRENT.get()
    if guard is not actual.storage_guard:
        return ns._require_profile(profile)
    if not guard._active:
        life._fail("STORAGE_GUARD_REQUIRED")
    if guard._owner != life._owner():
        life._fail("STORAGE_GUARD_CONTEXT")
    if profile.identity not in guard._profile_ids:
        life._fail("STORAGE_PROFILE_MISMATCH")
    return guard


_EFFECT_PROFILE_DISPATCH = _stage_effect_profile_dispatch_original
_EFFECT_PROFILE_BINDING = _stage_effect_profile_binding_original
_EFFECT_PROFILE_STATE = _stage_effect_profile_state_original


'''

BEFORE_EFFECT = '''    else:
        actual = _N2_EFFECT(scope.budget)  # Original callback-free effect gate.
        _need(ns._require_profile(actual.profile) is actual.storage_guard,
            "STAGE_ORIGINAL_PROFILE_GUARD_REQUIRED")
'''
AFTER_EFFECT = '''    else:
        fixed = (_stage_effect_profile_dispatch_original is _EFFECT_PROFILE_DISPATCH
            and _stage_effect_profile_binding_original is _EFFECT_PROFILE_BINDING
            and _stage_effect_profile_state_original is _EFFECT_PROFILE_STATE)
        binding = _stage_effect_profile_binding_original(scope) if fixed else None
        actual = _N2_EFFECT(scope.budget)  # Original callback-free effect gate.
        current_binding = (_stage_effect_profile_binding_original(scope)
            if binding is not None
            and _stage_effect_profile_dispatch_original is _EFFECT_PROFILE_DISPATCH
            and _stage_effect_profile_binding_original is _EFFECT_PROFILE_BINDING
            and _stage_effect_profile_state_original is _EFFECT_PROFILE_STATE else None)
        _need((_stage_effect_profile_state_original(scope, actual)
            if current_binding is not None and len(current_binding) == len(binding)
            and all(current is expected for current, expected in zip(current_binding, binding))
            and actual is current_binding[0].namespace else ns._require_profile(actual.profile)) is actual.storage_guard,
            "STAGE_ORIGINAL_PROFILE_GUARD_REQUIRED")
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
    path = OLD / "r705-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    manifest = json.loads(path.read_bytes())
    for row in manifest["source_files"] + manifest["artifacts"]:
        assert fact(ROOT / row["path"]) == row
    return manifest["source_files"]


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    pairs = ((BEFORE_EFFECT, AFTER_EFFECT),
        ("def _directory_original(scope, directory):\n", HELPERS + "def _directory_original(scope, directory):\n"))
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in pairs]


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "owned_stage_io_r251.py").read_bytes()
    new = (PACKAGE / "owned_stage_io_r251.py").read_bytes()
    a, b = definitions(old), definitions(new)
    added = {"_stage_effect_profile_dispatch_original", "_stage_effect_profile_binding_original",
        "_stage_effect_profile_state_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if b[key] != a[key]] == ["_effect"]
    inverse = new
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
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
        assert raw.replace(b"r709", b"r705").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "stage_effect_profile_static_only", "original_inputs": inputs,
        "changed_definitions": ["_effect"], "new_definitions": sorted(added),
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
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r705", b"r709").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "stage.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r705", tofile="r709")).encode())
    write_new(NEW / "README.md", b"# r709\n\nSame-effect installation profile observation after the complete original native Namespace verifier. The original Stage entry and tail, full native begin/body/finish, Store budgets/cancellation/clocks, actual result and non-exclusive profile semantics remain. Unknown/late dispatch follows the original remaining profile call. Static preparation only.\n")
    print(json.dumps({"stage": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "stage.diff", "README.md")]
    path = NEW / "r709-candidate-manifest.json"
    json_new(path, {"state": "stage_effect_profile_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
