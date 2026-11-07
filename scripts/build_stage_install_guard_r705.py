"""Build an isolated Stage installation guard candidate; file-only preparation."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r701"
NEW = ROOT / "data/t16-runtime-origin-graph-r705"
OLD_PACKAGE = OLD / "authority_runtime_origin_r701"
PACKAGE = NEW / "authority_runtime_origin_r705"
OLD_MANIFEST_SHA = "896825936f4cb1dee314cf50aa2a2ca29de6ee6a61d7ce6f2504aa0fbe551ca8"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''_INSTALL_SCOPE_TYPE = _StageScope
_INSTALL_SCOPE_STATE = _StageScope.__dict__["state"]
_INSTALL_DIRECTORY_BINDING = _directory_store_binding_original
_INSTALL_DIRECTORY_DISPATCH = _directory_store_dispatch_original
_INSTALL_GUARD_BINDING = store._pin_verify_guard_binding_original
_INSTALL_GUARD_DISPATCH = store._pin_verify_guard_dispatch_original
_INSTALL_LIFECYCLE = ns.lifecycle
_INSTALL_GUARD_TYPE = ns.lifecycle.StorageGuard
_INSTALL_PROFILE_TYPE = ns.lifecycle.StorageProfile
_INSTALL_LOCK_TYPE = ns.lifecycle._FileLock
_INSTALL_DIRECTORY_TYPE = safe._Directory
_INSTALL_BUFFER_TYPE = ns.lifecycle._LOCK_STREAM_TYPE
_INSTALL_RAW_TYPE = ns.lifecycle._LOCK_RAW_TYPE
_INSTALL_PATH_TYPE = ns.lifecycle._PROFILE_PATH_TYPE
_INSTALL_EXCLUSIVE = ns.lifecycle.StorageGuard.__dict__["exclusive"]
_INSTALL_MISSING = object()
_INSTALL_GUARD_FIELDS = ("_profiles", "_profile_ids", "_exclusive", "_locks", "_owner", "_active")
_INSTALL_PROFILE_FIELDS = ("database", "media", "reports", "checkpoints")
_INSTALL_LOCK_FIELDS = ("directory", "stream", "identity", "locked")
_INSTALL_DIRECTORY_FIELDS = ("path", "base", "create", "parent", "handles", "identities", "fd")


def _stage_install_guard_binding_original(scope):
    # No physical observation, callback or caller-supplied check result. Missing
    # and unknown ordinary fields stay on the original stack/profile path.
    if (_StageScope is not _INSTALL_SCOPE_TYPE or type(scope) is not _INSTALL_SCOPE_TYPE
            or "__getattribute__" in _StageScope.__dict__
            or "__getattr__" in _StageScope.__dict__
            or _StageScope.__dict__.get("state") is not _INSTALL_SCOPE_STATE
            or getattr(scope, "state", _INSTALL_MISSING) is not None
            or _directory_store_binding_original is not _INSTALL_DIRECTORY_BINDING
            or _directory_store_dispatch_original is not _INSTALL_DIRECTORY_DISPATCH
            or store._pin_verify_guard_binding_original is not _INSTALL_GUARD_BINDING
            or store._pin_verify_guard_dispatch_original is not _INSTALL_GUARD_DISPATCH):
        return None
    actual = _directory_store_binding_original(scope)
    if actual is None:
        return None
    custody = actual[0]
    if (type(scope.stack) is not store._ClosingStack or custody.native_owner is not None
            or scope.first_failure is not None or custody.first is not None
            or custody.held_original.first is not None
            or (custody.stage is not None and custody.stage.first_failure is not None)):
        return None
    life, guard, profile = ns.lifecycle, custody.guard, custody.namespace.profile
    if (life is not _INSTALL_LIFECYCLE or life.StorageGuard is not _INSTALL_GUARD_TYPE
            or life.StorageProfile is not _INSTALL_PROFILE_TYPE
            or life._FileLock is not _INSTALL_LOCK_TYPE or safe._Directory is not _INSTALL_DIRECTORY_TYPE
            or life._LOCK_STREAM_TYPE is not _INSTALL_BUFFER_TYPE or life._LOCK_RAW_TYPE is not _INSTALL_RAW_TYPE
            or life._PROFILE_PATH_TYPE is not _INSTALL_PATH_TYPE
            or type(guard) is not _INSTALL_GUARD_TYPE
            or _INSTALL_GUARD_TYPE.__dict__.get("exclusive") is not _INSTALL_EXCLUSIVE):
        return None
    for cls, fields in ((_INSTALL_GUARD_TYPE, _INSTALL_GUARD_FIELDS),
            (_INSTALL_PROFILE_TYPE, _INSTALL_PROFILE_FIELDS), (_INSTALL_LOCK_TYPE, _INSTALL_LOCK_FIELDS),
            (_INSTALL_DIRECTORY_TYPE, _INSTALL_DIRECTORY_FIELDS)):
        if ("__getattribute__" in cls.__dict__ or "__getattr__" in cls.__dict__
                or any(name in cls.__dict__ for name in fields)):
            return None
    if any(getattr(guard, name, _INSTALL_MISSING) is _INSTALL_MISSING for name in _INSTALL_GUARD_FIELDS):
        return None
    if (type(guard._profiles) is not tuple or type(guard._profile_ids) is not frozenset
            or type(guard._exclusive) is not bool or type(guard._locks) is not tuple
            or type(guard._owner) is not tuple or type(guard._active) is not bool):
        return None
    for selected in (profile,) + guard._profiles:
        if (type(selected) is not _INSTALL_PROFILE_TYPE
                or any(getattr(selected, name, _INSTALL_MISSING) is _INSTALL_MISSING
                    for name in _INSTALL_PROFILE_FIELDS)):
            return None
    directory_values = []
    for lock in guard._locks:
        if (type(lock) is not _INSTALL_LOCK_TYPE
                or any(getattr(lock, name, _INSTALL_MISSING) is _INSTALL_MISSING for name in _INSTALL_LOCK_FIELDS)
                or type(lock.directory) is not _INSTALL_DIRECTORY_TYPE
                or type(lock.stream) is not _INSTALL_BUFFER_TYPE or type(lock.stream.raw) is not _INSTALL_RAW_TYPE):
            return None
        directory = lock.directory
        fields = tuple(getattr(directory, name, _INSTALL_MISSING) for name in _INSTALL_DIRECTORY_FIELDS)
        if (any(value is _INSTALL_MISSING for value in fields)
                or type(directory.handles) is not list or type(directory.identities) is not list
                or directory.parent is not None or any(type(handle) is not int for handle in directory.handles)
                or (directory.fd is not None and type(directory.fd) is not int)
                or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not _INSTALL_PATH_TYPE
                    or type(row[1]) is not int or type(row[2]) is not int for row in directory.identities)):
            return None
        directory_values.extend((directory,) + fields)
        directory_values.extend(directory.handles)
        directory_values.extend(directory.identities)
    if not store._pin_verify_guard_dispatch_original(guard):
        return None
    guard_values = store._pin_verify_guard_binding_original(scope.stack)
    if guard_values is None:
        return None
    return actual + guard_values + tuple(directory_values)


def _stage_install_profile_state_original(scope):
    # The complete original stack in this verifier supplied the physical pass.
    # Resolve the real objects again; no supplied tuple authorizes this helper.
    custody = store._STACK_RECORDS.get(id(scope.stack))
    _need(type(scope) is _StageScope and _SCOPES.get(id(scope)) is scope
        and type(custody) is store._StoreStackRecord and custody is scope.permit
        and custody.original is scope.stack, "STAGE_ORIGINAL_BUDGET_CHANGED")
    life, profile = ns.lifecycle, custody.namespace.profile
    ns._need(type(profile) is life.StorageProfile, "NAMESPACE_PROFILE_INVALID")
    guard = life._CURRENT.get()
    if guard is not custody.guard:
        return ns._require_profile(profile, exclusive=True)
    if not guard._active:
        life._fail("STORAGE_GUARD_REQUIRED")
    if guard._owner != life._owner():
        life._fail("STORAGE_GUARD_CONTEXT")
    if profile.identity not in guard._profile_ids:
        life._fail("STORAGE_PROFILE_MISMATCH")
    if not guard.exclusive:
        life._fail("STORAGE_LOCK_UPGRADE")
    return guard


'''

BEFORE_PROFILE = '''    if scope.state is None:
        _need(type(scope.stack) is store._ClosingStack and custody.native_owner is None
            and ns._require_profile(custody.namespace.profile, exclusive=True) is custody.guard,
            "STAGE_STORE_INSTALLATION_SCOPE_REQUIRED")
'''
AFTER_PROFILE = '''    if scope.state is None:
        current_binding = _stage_install_guard_binding_original(scope) if binding is not None else None
        _need(type(scope.stack) is store._ClosingStack and custody.native_owner is None
            and (_stage_install_profile_state_original(scope)
                if current_binding is not None and len(current_binding) == len(binding)
                and all(current is expected for current, expected in zip(current_binding, binding))
                else ns._require_profile(custody.namespace.profile, exclusive=True)) is custody.guard,
            "STAGE_STORE_INSTALLATION_SCOPE_REQUIRED")
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
    path = OLD / "r701-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(path.read_bytes())["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    pairs = (("def _directory_original(scope, directory):\n", HELPERS + "def _directory_original(scope, directory):\n"),
        ("def _verify_store_stage_scope_original(scope):\n", "def _verify_store_stage_scope_original(scope):\n    binding = _stage_install_guard_binding_original(scope)\n"),
        (BEFORE_PROFILE, AFTER_PROFILE))
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in pairs]


def store_replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    before_constants = "_PIN_TAIL_DIRECTORY_TYPE = safe._Directory\n"
    after_constants = before_constants + '''_PIN_TAIL_DIRECTORY_VERIFY = safe._Directory.__dict__["verify"]
_PIN_TAIL_DIRECTORY_PLAN = safe._directory_verification_plan
_PIN_TAIL_DIRECTORY_PLAN_VERIFY = safe._verify_directory_plan
'''
    before_tail = '''        and safe._Directory.__dict__.get("entry_stat") is life._DIRECTORY_ENTRY_ORIGINAL
        and life._guard_has_original_dispatch(guard))
'''
    after_tail = '''        and safe._Directory.__dict__.get("entry_stat") is life._DIRECTORY_ENTRY_ORIGINAL
        and safe._Directory.__dict__.get("verify") is _PIN_TAIL_DIRECTORY_VERIFY
        and "__getattribute__" not in safe._Directory.__dict__ and "__getattr__" not in safe._Directory.__dict__
        and safe._directory_verification_plan is _PIN_TAIL_DIRECTORY_PLAN
        and safe._verify_directory_plan is _PIN_TAIL_DIRECTORY_PLAN_VERIFY
        and life._guard_has_original_dispatch(guard)
        and all(life._original_bound_method(lock.directory, "verify", _PIN_TAIL_DIRECTORY_VERIFY)
            for lock in guard._locks))
'''
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in
        ((before_constants, after_constants), (before_tail, after_tail))]


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "owned_stage_io_r251.py").read_bytes()
    new = (PACKAGE / "owned_stage_io_r251.py").read_bytes()
    a, b = definitions(old), definitions(new)
    added = {"_stage_install_guard_binding_original", "_stage_install_profile_state_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if b[key] != a[key]] == ["_verify_store_stage_scope_original"]
    inverse = new
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
    old_store = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new_store = (PACKAGE / "personal_authority_store.py").read_bytes()
    store_a, store_b = definitions(old_store), definitions(new_store)
    assert store_a.keys() == store_b.keys()
    assert [key for key in store_a if store_b[key] != store_a[key]] == ["_pin_verify_guard_dispatch_original"]
    inverse_store = new_store
    for before, after in reversed(store_replacements(old_store)):
        assert inverse_store.count(after) == 1
        inverse_store = inverse_store.replace(after, before, 1)
    assert inverse_store == old_store
    same = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name not in {"owned_stage_io_r251.py", "personal_authority_store.py"}:
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r705", b"r701").replace(digest(new).encode(), digest(old).encode()).replace(
            digest(new_store).encode(), digest(old_store).encode()) == (OLD / name).read_bytes()
    return {"state": "stage_install_guard_static_only", "original_inputs": inputs,
        "changed_definitions": ["_verify_store_stage_scope_original"], "new_definitions": sorted(added),
        "changed_store_definitions": ["_pin_verify_guard_dispatch_original"],
        "unchanged_store_definitions": len(store_a) - 1, "store_inverse_bytes_equal": True,
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
    store_path = PACKAGE / "personal_authority_store.py"
    old_store = store_path.read_bytes()
    new_store = old_store
    for before, after in store_replacements(old_store):
        assert new_store.count(before) == 1
        new_store = new_store.replace(before, after, 1)
    store_path.write_bytes(new_store)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r701", b"r705").replace(digest(old).encode(), digest(new).encode()).replace(
            digest(old_store).encode(), digest(new_store).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "stage.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r701", tofile="r705")).encode())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old_store.decode().splitlines(True), new_store.decode().splitlines(True),
        fromfile="r701", tofile="r705")).encode())
    write_new(NEW / "README.md", b"# r705\n\nSame-verifier installation profile observation; shared Store guard dispatch also retains custom Directory verification in every existing consumer. Complete original Store stack, logical profile ownership and exclusive mode, custom/late fallback, runtime branch, budget bytes and both clocks remain. Static preparation only.\n")
    print(json.dumps({"stage": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "stage.diff", "store.diff", "README.md")]
    path = NEW / "r705-candidate-manifest.json"
    json_new(path, {"state": "stage_install_guard_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
