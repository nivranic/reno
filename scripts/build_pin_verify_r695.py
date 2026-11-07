"""Build a fresh candidate; no product imports or runtime execution."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r693"
NEW = ROOT / "data/t16-runtime-origin-graph-r695"
OLD_PACKAGE = OLD / "authority_runtime_origin_r693"
PACKAGE = NEW / "authority_runtime_origin_r695"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = '''    def verify(self):
        record = _pin_record_original(self)
        try:
            _Budget.check(record.budget)
            before = self._observation().observed_bytes
            _held_io._verify_file_original(record.scope, record.lease)
            if record.kind == "source":
                after = self._observation().observed_bytes
                record.budget.charge("bytes", after - before)
        except BaseException as failure:
            _remember_budget_first_original(record.budget, failure)
            raise
'''
AFTER = '''    def verify(self):
        _verify_pin_original(self)
'''
HELPERS = '''_PIN_VERIFY_CHECK_ORIGINAL = _Budget.check
_PIN_VERIFY_OBSERVATION_ORIGINAL = _pin_observation_original
_PIN_VERIFY_FILE_ORIGINAL = _held_io._verify_file_original
_PIN_VERIFY_SCOPE_ORIGINAL = _held_io._scope_original
_PIN_VERIFY_REMEMBER_ORIGINAL = _held_io._remember_failure
_PIN_VERIFY_REQUIRE_PROFILE_ORIGINAL = ns._require_profile
_PIN_VERIFY_IDENTITY_ORIGINAL = _held_io._verify_identity_record
_PIN_VERIFY_LOCK_ORIGINAL = _held_io._verify_lock_record


def _pin_verify_dispatch_original():
    return (_Budget.__dict__.get("check") is _PIN_VERIFY_CHECK_ORIGINAL
        and _ClosedStorePin.__dict__.get("_observation") is _PIN_VERIFY_METHOD_OBSERVATION_ORIGINAL
        and _pin_observation_original is _PIN_VERIFY_OBSERVATION_ORIGINAL
        and _held_io._verify_file_original is _PIN_VERIFY_FILE_ORIGINAL
        and _held_io._file_observation_original is _PIN_FILE_OBSERVATION_ORIGINAL
        and _held_io._scope_original is _PIN_VERIFY_SCOPE_ORIGINAL
        and _held_io._poll_record is _PIN_BUDGET_POLL_ORIGINAL
        and _held_io._remember_failure is _PIN_VERIFY_REMEMBER_ORIGINAL
        and _held_io._verify_identity_record is _PIN_VERIFY_IDENTITY_ORIGINAL
        and _held_io._verify_lock_record is _PIN_VERIFY_LOCK_ORIGINAL
        and _pin_stack_identity_binding_original is _PIN_BUDGET_IDENTITY_ORIGINAL
        and _budget_record_original is _PIN_BUDGET_RECORD_ORIGINAL
        and _bound_budget is _PIN_BUDGET_BOUND_ORIGINAL
        and _need is _PIN_BUDGET_NEED_ORIGINAL
        and ns._require_profile is _PIN_VERIFY_REQUIRE_PROFILE_ORIGINAL
        and _store_directories_verify_original is _PIN_ENTRY_DIRECTORIES_ORIGINAL)


def _pin_verify_stack_state_original(stack):
    # Exact leading logical gates of _stage_stack_original, in their original
    # order. Only the caller-owned same-verification physical sweep is shared.
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
    _need(ns._require_profile(record.namespace.profile) is record.guard
        and ns._CLOSED_NAMESPACE_OWNERS.get(id(record.namespace.original)) is record.namespace,
        "NATIVE_OWNER_BUDGET_CHANGED")
    return record


def _verify_pin_original(pin):
    eligible = _pin_verify_dispatch_original()
    initial = _PIN_RECORDS.get(id(pin))
    shared = (_pin_stack_identity_binding_original(initial)
        if eligible and type(initial) is _StorePinRecord else None)
    # Keep this complete original check outside the original Budget catch.
    record = _pin_record_original(pin)
    try:
        _Budget.check(record.budget)
        if (shared is not None and record is initial and _pin_verify_dispatch_original()
                and _pin_stack_identity_binding_original(record) is shared):
            custody = _pin_verify_stack_state_original(record.stack)
            scope = _held_io._scope_original(custody.scope)
            if scope.first is not None:
                first = scope.first
                original_tb = BaseException.__dict__["__traceback__"].__get__(first)
                raise BaseException.with_traceback(first, original_tb)
            _held_io.original.poll_cancellation(scope.cancellation)
            # Keep the entire original second directory pass and its two ends.
            _store_directories_verify_original(custody)
            selected_budget, binding = record.budget, shared.budget
            before = _held_io._file_observation_original(record.scope, record.lease).observed_bytes
            actual = _held_io._resource_original(record.scope, record.lease, _held_io._FileLease)
            try:
                _held_io._poll_record(actual)
            except BaseException as failure:
                _held_io._remember_failure(actual, failure)
                raise
            # Store state failures stay outside held failure bookkeeping.
            current = _pin_verify_stack_state_original(record.stack)
            try:
                _need(current is custody and actual is shared and actual.budget is binding
                    and record.budget is selected_budget and _pin_verify_dispatch_original()
                    and _pin_stack_identity_binding_original(record) is shared,
                    "NATIVE_ORIGINAL_PIN_REQUIRED")
            except BaseException as failure:
                _held_io._remember_failure(actual, failure)
                raise
        else:
            # Unknown and late dispatch keep all remaining original calls;
            # the completed Pin-record and Budget checks are never replayed.
            before = pin._observation().observed_bytes
        _held_io._verify_file_original(record.scope, record.lease)
        if record.kind == "source":
            after = pin._observation().observed_bytes
            record.budget.charge("bytes", after - before)
    except BaseException as failure:
        _remember_budget_first_original(record.budget, failure)
        raise


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
    manifest = OLD / "r693-candidate-manifest.json"
    assert digest(manifest.read_bytes()) == "d6d8fbebc4392a674aff72f247c66b0930122b9dc3b7425cf1acb0e709097d18"
    rows = json.loads(manifest.read_bytes())["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def replacements(raw):
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in
            ((BEFORE, AFTER), ("class _ClosedStorePin:\n", HELPERS + "class _ClosedStorePin:\n"),
             ("class _StoreStackRecord:\n", "_PIN_VERIFY_METHOD_OBSERVATION_ORIGINAL = _ClosedStorePin._observation\n\n\nclass _StoreStackRecord:\n"))]


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def validate():
    original = inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if a[key] != b[key]] == ["_ClosedStorePin"]
    assert set(b) - set(a) == {"_pin_verify_dispatch_original", "_pin_verify_stack_state_original", "_verify_pin_original"}
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
        assert raw.replace(b"r695", b"r693").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_verify_static_only", "original_inputs": original,
            "changed_definitions": ["_ClosedStorePin.verify"],
            "new_definitions": ["_pin_verify_dispatch_original", "_pin_verify_stack_state_original", "_verify_pin_original"],
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
        p.write_bytes(p.read_bytes().replace(b"r693", b"r695").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": original})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True), fromfile="r693", tofile="r695")).encode())
    write_new(NEW / "README.md", b"# r695\n\nFixed Pin verification shares only the repeated pre-read held identity sweep. Original Store logical state, scope first/cancellation, complete directory checks, held file observation, actual reread/hash, source post-observation/charging and all cleanup remain. Unknown dispatch retains original chain. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r695-candidate-manifest.json"
    json_new(path, {"state": "pin_verify_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
