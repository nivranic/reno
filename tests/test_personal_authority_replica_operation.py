"""Independent S1 operation-check tests, not an R02 source/native factory.

Original records, owners, G1 guards and the one ledger come only from the
fixed factory under a real checked group. Named mutations are fault cases.
Root alone imports/runs this module; no subprocess or fake native debt.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import gc
import hashlib
import os
from pathlib import Path
import stat
import threading
import uuid
import weakref

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_group as group
from reno import personal_authority_replica_budget as budget
from reno import personal_authority_replica_selector as selector
from reno import personal_authority_preservation_routing as routing
from reno import personal_authority_raw_preservation as raw1
from reno import personal_authority_raw_preservation2 as raw2
from reno import storage_lifecycle as lifecycle


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_OPERATION_ID = "abcdef12-1234-4234-8234-123456789abc"
SOURCE_SHA = "0123456789abcdef" * 4
EXPECTED_RP1_SHA = "fedcba9876543210" * 4
FIELDS = ("database", "media", "reports", "checkpoints")
ERROR = operation.AuthorityReplicaOperationError
ERROR_PREFIX = "AUTHORITY_REPLICA_OPERATION1_"
ERROR_SUFFIXES = {
    "TYPE_INVALID", "ARGUMENT_INVALID", "OWNER_MISMATCH", "REENTRANT",
    "REGISTRY_CHANGED", "RECORD_CHANGED", "GUARD_REQUIRED", "GUARD_CHANGED",
    "PROFILE_ALIAS", "PHASE_INVALID", "CLOSED", "CONSUMED", "UNSETTLED", "CHECK_INCOMPLETE",
}
KNOWN_ERRORS = (ERROR, selector.AuthorityReplicaSelectorError,
                group.AuthorityReplicaGroupError, budget.AuthorityReplicaBudgetError,
                lifecycle.StorageLifecycleError)
RESULT_FIELDS = {
    "format_version", "kind", "operation_id", "run_id", "mode", "budget_mode",
    "target_profile_paths", "required_profile_paths", "source_kind",
    "checkpoint_completed", "operation_context_returned", "original_registry_retired",
    "counters", "rp1_owned_native_resources", "source_observed", "source_native_closure",
    "target_native_closure", "outer_lifecycle_exited", "copy_performed", "data_restored",
    "write_authority", "sender_authority",
}
COUNTER_FIELDS = {
    "read_bytes", "source_read_bytes", "read_calls", "source_read_calls", "seek_calls",
    "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes", "elapsed_ns",
    "current_resources", "peak_resources", "pending_resources", "uncertain_resource_acquires",
}
MODES_AND_SOURCES = [
    ("preview", "detached"), ("preview", "same"), ("preview", "distinct"),
    ("create", "detached"), ("create", "same"), ("create", "distinct"),
    ("resume", "detached"), ("resume", "same"), ("resume", "distinct"),
    ("verify", "none"),
]


def path_key(path):
    return os.path.normcase(os.path.normpath(str(path)))


def profile_paths(value):
    return [path_key(getattr(value, field)) for field in FIELDS]


def make_profile(root):
    return lifecycle.StorageProfile(root / "data/reno.db", root / "media",
                                    root / "reports", root / "checkpoints")


@pytest.fixture
def home(tmp_path):
    value = make_profile(tmp_path / "home")
    lifecycle.initialize(value)
    (value.media / "preserved.bin").write_bytes(b"S1 synthetic original\x00\xff")
    (value.reports / "empty").mkdir()
    return value


@pytest.fixture
def other(tmp_path):
    value = make_profile(tmp_path / "other")
    lifecycle.initialize(value)
    return value


def original_tree(root):
    result = {}
    for path in [root, *root.rglob("*")]:
        info = path.stat(follow_symlinks=False)
        assert stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        result[path.relative_to(root).as_posix()] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None,
        )
    return result


@contextmanager
def held(profiles):
    with group.checked_exclusive_many(tuple(profiles)) as group_token:
        guard = group.guard_original(group_token)
        assert type(guard) is lifecycle.StorageGuard
        assert lifecycle.current_guard() is guard
        yield group_token, guard
        assert lifecycle.current_guard() is guard
    assert lifecycle.current_guard() is None
    closed = group.consume_closed_original(group_token)
    assert closed["native_normal_closed"] is True
    assert closed["lifecycle_context_returned"] is True
    assert closed["native_resources_acquired"] == closed["native_resources_normal_closed"] > 0
    assert closed["pending_resources"] == closed["current_resources"] == 0
    assert closed["write_authority"] is closed["sender_authority"] is False


def source_for(home, other, root, kind):
    if kind == "none":
        return None
    if kind == "detached":
        return selector.DetachedRaw2Source(package_root=root / "uncreated-detached-package",
            generation=1, expected_request_sha256=SOURCE_SHA)
    assert kind in ("same", "distinct")
    return selector.ProfileRaw2Source(profile=home if kind == "same" else other,
        operation_id=SOURCE_OPERATION_ID, generation=2, expected_request_sha256=SOURCE_SHA)


def arguments(home, group_token, root, *, mode="create", source=None, callback=None):
    if source is None and mode != "verify":
        source = source_for(home, None, root, "detached")
    return dict(group_token=group_token, source=source, mode=mode, operation_id=OPERATION_ID,
                expected_request_sha256=EXPECTED_RP1_SHA if mode in ("resume", "verify") else None,
                check_cancelled=callback)


def check(home, group_token, root, **options):
    return operation.check_operation_locked(home, **arguments(home, group_token, root, **options))


def captured_live():
    # Only read a registration made by the actual fixed factory.
    registrations = list(operation._LIVE.values())
    assert len(registrations) == 1
    registration = registrations[0]
    record, token = registration.record, registration.token
    assert operation._LIVE[id(token)] is registration and record.token is token
    assert type(record.owner) is tuple and len(record.owner) == 3
    assert type(record.owner[0]) is type(record.owner[1]) is int
    assert record.owner[0] == os.getpid() and record.owner[1] == threading.get_ident()
    try:
        actual_task = asyncio.current_task()
    except RuntimeError:
        actual_task = None
    assert record.owner[2] is actual_task
    assert type(record.owner_key) is tuple and len(record.owner_key) == 3
    assert all(type(value) is int for value in record.owner_key)
    assert record.owner_key == (record.owner[0], record.owner[1], id(actual_task))
    assert record.owner_key in operation._OWNERS
    assert type(record.budget) is budget.ReplicaBudget
    assert type(record.budget._started) is int and record.budget._started >= 0
    assert record.phase == "checking" and record.callback_active is True
    assert record.first is None and record.budget.first is None
    assert record.checkpoint_count == 0
    return registration, record, token


def retired(captured):
    _registration, _record, token = captured
    assert id(token) not in operation._LIVE
    assert not operation._OWNERS


def plain(value):
    if type(value) is dict:
        assert all(type(key) is str for key in value)
        for child in value.values():
            plain(child)
    elif type(value) is list:
        for child in value:
            plain(child)
    else:
        assert value is None or type(value) in (str, int, bool)


def assert_result(result, home, profiles, mode, source_kind):
    assert type(result) is dict and set(result) == RESULT_FIELDS
    plain(result)
    assert type(result["format_version"]) is int and result["format_version"] == 1
    assert result["kind"] == "replica_operation_check"
    assert result["operation_id"] == OPERATION_ID and result["mode"] == mode
    run_id = uuid.UUID(result["run_id"])
    assert run_id.int and str(run_id) == result["run_id"]
    assert result["budget_mode"] == ("producer" if mode in ("create", "resume") else "readonly")
    assert result["target_profile_paths"] == profile_paths(home)
    assert result["required_profile_paths"] == [profile_paths(value) for value in profiles]
    assert result["source_kind"] == source_kind
    for field in ("checkpoint_completed", "operation_context_returned", "original_registry_retired"):
        assert result[field] is True
    for field in ("source_observed", "outer_lifecycle_exited", "copy_performed", "data_restored",
                  "write_authority", "sender_authority"):
        assert result[field] is False
    assert result["source_native_closure"] == result["target_native_closure"] == "not_established"
    assert type(result["rp1_owned_native_resources"]) is int and result["rp1_owned_native_resources"] == 0
    counters = result["counters"]
    assert type(counters) is dict and set(counters) == COUNTER_FIELDS
    for name, value in counters.items():
        assert type(value) is int
        if name == "elapsed_ns":
            assert 0 <= value < 1_800_000_000_000
        else:
            assert value == 0


def operation_error(call, *args, suffix=None, **kwargs):
    with pytest.raises(ERROR) as raised:
        call(*args, **kwargs)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith(ERROR_PREFIX)
    assert raised.value.code[len(ERROR_PREFIX):] in ERROR_SUFFIXES
    if suffix is not None:
        assert raised.value.code == ERROR_PREFIX + suffix
    return raised.value


@pytest.mark.parametrize("mode,source_kind", MODES_AND_SOURCES)
def test_real_fixed_factory_registration_ledger_and_exact_result(home, other, tmp_path, mode, source_kind):
    selected = source_for(home, other, tmp_path, source_kind)
    profiles = (home, other) if source_kind == "distinct" else (home,)
    before = original_tree(tmp_path)
    observations = []
    with held(profiles) as (group_token, guard):
        def callback():
            captured = captured_live()
            record = captured[1]
            assert record.guard is guard and record.group_token is group_token
            assert type(record.profiles) is tuple and len(record.profiles) == len(profiles)
            assert all(actual is expected for actual, expected in zip(record.profiles, profiles))
            limits = dict(record.budget.limits)
            expected_read = 2 * 1024**4 if mode in ("create", "resume") else 1024**4
            expected_write = 256 * 1024**3 if mode in ("create", "resume") else 0
            assert limits["max_read_bytes"] == expected_read
            assert limits["source_max_read_bytes"] == 1024**4
            assert limits["max_write_bytes"] == expected_write
            assert limits["deadline_ms"] == 1_800_000
            observations.append(captured)

        result = check(home, group_token, tmp_path, mode=mode, source=selected, callback=callback)
        assert len(observations) == 1
        retired(observations[0])
        kind = "profile_raw2" if source_kind in ("same", "distinct") else "detached_raw2" if source_kind == "detached" else None
        assert_result(result, home, profiles, mode, kind)
        assert lifecycle.current_guard() is guard
        operation_error(operation._consume_check_closed_original, observations[0][2], suffix="CONSUMED")
    assert original_tree(tmp_path) == before
    assert not (tmp_path / "uncreated-detached-package").exists()


def test_equal_distinct_profile_cannot_replace_same_original_source_object(home, tmp_path):
    clone = lifecycle.StorageProfile(**{field: getattr(home, field) for field in FIELDS})
    assert clone is not home and profile_paths(clone) == profile_paths(home)
    selected = selector.ProfileRaw2Source(profile=clone, operation_id=SOURCE_OPERATION_ID,
        generation=1, expected_request_sha256=SOURCE_SHA)
    with held((home,)) as (group_token, _guard):
        operation_error(check, home, group_token, tmp_path, source=selected, suffix="PROFILE_ALIAS")
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("case", ["target_clone", "source_clone", "wrong_primary", "extra_member", "missing_source"])
def test_group_requires_exact_original_profile_members_and_order(home, other, tmp_path, case):
    target = home
    selected = source_for(home, other, tmp_path, "distinct")
    profiles = (home, other)
    if case == "target_clone":
        target = lifecycle.StorageProfile(**{field: getattr(home, field) for field in FIELDS})
    elif case == "source_clone":
        clone = lifecycle.StorageProfile(**{field: getattr(other, field) for field in FIELDS})
        selected = selector.ProfileRaw2Source(profile=clone, operation_id=SOURCE_OPERATION_ID,
            generation=1, expected_request_sha256=SOURCE_SHA)
    elif case == "wrong_primary":
        profiles = (other, home)
    elif case == "extra_member":
        selected = source_for(home, other, tmp_path, "detached")
    else:
        profiles = (home,)
    with held(profiles) as (group_token, _guard):
        with pytest.raises(KNOWN_ERRORS):
            check(target, group_token, tmp_path, source=selected)
        assert not operation._LIVE and not operation._OWNERS


def test_legacy_exclusive_guard_and_caller_object_are_not_a_g1_token(home, tmp_path):
    with lifecycle.exclusive(home):
        with pytest.raises(KNOWN_ERRORS):
            check(home, object(), tmp_path)
        assert not operation._LIVE and not operation._OWNERS
    with held((home,)) as (group_token, _guard):
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


def test_closed_actual_g1_token_cannot_be_reused_with_a_new_current(home, tmp_path):
    with held((home,)) as (old_token, _guard):
        pass
    with held((home,)) as (new_token, _guard):
        with pytest.raises(KNOWN_ERRORS):
            check(home, old_token, tmp_path)
        assert_result(check(home, new_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("case", ["missing_target", "missing_group", "missing_mode", "extra_keyword",
    "extra_positional", "duplicate_target", "bad_mode", "verify_source", "create_expected",
    "resume_no_expected", "zero_operation", "bad_operation_type", "fake_token"])
def test_exact_binding_rejects_invalid_plain_calls_and_retires_registration(home, tmp_path, case):
    with held((home,)) as (group_token, _guard):
        args = [home]
        kwargs = arguments(home, group_token, tmp_path)
        if case == "missing_target":
            args.clear()
        elif case == "missing_group":
            del kwargs["group_token"]
        elif case == "missing_mode":
            del kwargs["mode"]
        elif case == "extra_keyword":
            kwargs["caller_ledger"] = object()
        elif case == "extra_positional":
            args.append(object())
        elif case == "duplicate_target":
            kwargs["target_profile"] = home
        elif case == "bad_mode":
            kwargs["mode"] = True
        elif case == "verify_source":
            kwargs.update(mode="verify", expected_request_sha256=EXPECTED_RP1_SHA)
        elif case == "create_expected":
            kwargs["expected_request_sha256"] = EXPECTED_RP1_SHA
        elif case == "resume_no_expected":
            kwargs["mode"] = "resume"
        elif case == "zero_operation":
            kwargs["operation_id"] = "00000000-0000-0000-0000-000000000000"
        elif case == "bad_operation_type":
            kwargs["operation_id"] = 1
        else:
            kwargs["group_token"] = object()
        with pytest.raises(KNOWN_ERRORS):
            operation.check_operation_locked(*args, **kwargs)
        assert not operation._LIVE and not operation._OWNERS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("case", ["new_valid", "new_missing", "new_extra", "new_duplicate", "new_fake_token",
    "new_bad_selector", "raw1", "raw2", "g1", "checkpoint_fake", "consume_fake"])
def test_callback_reentry_is_sticky_before_binding_token_or_guard(home, tmp_path, case):
    caught, seen = [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            seen.append(captured_live())
            def nested():
                if case == "new_valid":
                    check(home, group_token, tmp_path)
                elif case == "new_missing":
                    operation.check_operation_locked()
                elif case == "new_extra":
                    operation.check_operation_locked(home, surprise=object())
                elif case == "new_duplicate":
                    operation.check_operation_locked(home, target_profile=home)
                elif case == "new_fake_token":
                    check(home, object(), tmp_path)
                elif case == "new_bad_selector":
                    kwargs = arguments(home, group_token, tmp_path)
                    kwargs["source"] = object()
                    operation.check_operation_locked(home, **kwargs)
                elif case in ("raw1", "raw2"):
                    module = raw1 if case == "raw1" else raw2
                    module.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                elif case == "g1":
                    with group.checked_exclusive_many(None):
                        pytest.fail("Reentrant G1 entered")
                elif case == "checkpoint_fake":
                    operation._checkpoint_original(object())
                else:
                    operation._consume_check_closed_original(object())
            caught.append(operation_error(nested, suffix="REENTRANT"))

        terminal = operation_error(check, home, group_token, tmp_path, callback=callback, suffix="REENTRANT")
        assert len(caught) == len(seen) == 1 and terminal is caught[0]
        retired(seen[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("entry", ["current", "require", "verify", "allows"])
@pytest.mark.parametrize("alter", ["original", "wrong_argument", "current_none"])
def test_callback_direct_lifecycle_observation_cannot_bypass_routing(home, tmp_path, entry, alter):
    caught, seen, hook_calls = [], [], []
    with held((home,)) as (group_token, guard):
        class Hostile:
            def __getattribute__(self, name):
                hook_calls.append(name)
                raise AssertionError("Wrong guard argument was dispatched")

        def callback():
            seen.append(captured_live())
            context_token = lifecycle._CURRENT.set(None) if alter == "current_none" else None
            try:
                def attempt():
                    if entry == "current":
                        lifecycle.current_guard()
                    elif entry == "require":
                        lifecycle.require_guard(Hostile() if alter == "wrong_argument" else home,
                                                exclusive=object() if alter == "wrong_argument" else True)
                    elif entry == "verify":
                        lifecycle.StorageGuard.verify(Hostile() if alter == "wrong_argument" else guard)
                    else:
                        lifecycle.StorageGuard.allows(Hostile() if alter == "wrong_argument" else guard,
                                                     Hostile() if alter == "wrong_argument" else home)
                caught.append(operation_error(attempt, suffix="REENTRANT"))
            finally:
                if context_token is not None:
                    lifecycle._CURRENT.reset(context_token)

        terminal = operation_error(check, home, group_token, tmp_path, callback=callback, suffix="REENTRANT")
        assert terminal is caught[0] and hook_calls == []
        retired(seen[0])


@pytest.mark.parametrize("entry", ["shared", "exclusive", "shared_many", "exclusive_many"])
@pytest.mark.parametrize("clear_current", [False, True])
def test_callback_guard_many_rejects_before_borrow_or_new_acquisition(home, tmp_path, entry, clear_current):
    caught = []
    with held((home,)) as (group_token, _guard):
        def callback():
            context_token = lifecycle._CURRENT.set(None) if clear_current else None
            try:
                def attempt():
                    value = (home,) if entry.endswith("many") else home
                    with getattr(lifecycle, entry)(value):
                        pytest.fail("Callback entered a legacy lifecycle scope")
                caught.append(operation_error(attempt, suffix="REENTRANT"))
            finally:
                if context_token is not None:
                    lifecycle._CURRENT.reset(context_token)
        terminal = operation_error(check, home, group_token, tmp_path, callback=callback, suffix="REENTRANT")
        assert terminal is caught[0]


@pytest.mark.parametrize("outer", [raw1, raw2])
@pytest.mark.parametrize("invalid", [False, True])
def test_real_old_family_callback_to_new_preserves_the_old_original_first(home, tmp_path, outer, invalid):
    caught = []
    with lifecycle.exclusive(home):
        def callback():
            with pytest.raises(raw1.AuthorityRawPreservationError) as raised:
                if invalid:
                    operation.check_operation_locked()
                else:
                    check(home, object(), tmp_path)
            caught.append(raised.value)
        with pytest.raises(raw1.AuthorityRawPreservationError) as terminal:
            outer.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID, check_cancelled=callback)
    assert len(caught) == 1 and terminal.value is caught[0]
    assert terminal.value.code == "AUTHORITY_RAW_PRESERVATION_REENTRANT"
    assert not operation._LIVE and not operation._OWNERS


def test_real_g1_acquisition_callback_to_new_preserves_original_g1_first(home, tmp_path):
    caught = []
    def callback():
        with pytest.raises(group.AuthorityReplicaGroupError) as raised:
            operation.check_operation_locked()
        caught.append(raised.value)
    with pytest.raises(group.AuthorityReplicaGroupError) as terminal:
        with group.checked_exclusive_many((home,), check_cancelled=callback):
            pytest.fail("G1 callback reentry was swallowed")
    assert len(caught) == 1 and terminal.value is caught[0]
    assert terminal.value.code == "AUTHORITY_REPLICA_GROUP1_REENTRANT"
    assert not operation._LIVE and not operation._OWNERS


@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_actual_callback_baseexception_identity_survives_retirement(home, tmp_path, exception_type):
    first = exception_type("fixed callback first")
    seen = []
    with held((home,)) as (group_token, _guard):
        def callback():
            seen.append(captured_live())
            raise first
        with pytest.raises(exception_type) as raised:
            check(home, group_token, tmp_path, callback=callback)
        assert raised.value is first
        retired(seen[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


def test_deadline_error_injected_by_callback_only_proves_original_error_propagation(home, tmp_path):
    # Explicit fault, not a real 1800-second expiry or a clock replacement.
    first = budget.AuthorityReplicaBudgetError("DEADLINE_LIMIT")
    with held((home,)) as (group_token, _guard):
        def callback():
            raise first
        with pytest.raises(budget.AuthorityReplicaBudgetError) as raised:
            check(home, group_token, tmp_path, callback=callback)
        assert raised.value is first
        assert not operation._LIVE and not operation._OWNERS


def test_callback_and_exception_truth_or_diagnostic_hooks_are_not_invoked(home, tmp_path):
    calls = []
    class UserFailure(BaseException):
        def __str__(self):
            calls.append("str")
            raise AssertionError("exception str hook")
        def __repr__(self):
            calls.append("repr")
            raise AssertionError("exception repr hook")
    first = UserFailure()
    class Callback:
        def __bool__(self):
            calls.append("bool")
            raise AssertionError("callback truth hook")
        def __repr__(self):
            calls.append("callback-repr")
            raise AssertionError("callback repr hook")
        def __call__(self):
            calls.append("called")
            raise first
    with held((home,)) as (group_token, _guard):
        with pytest.raises(UserFailure) as raised:
            check(home, group_token, tmp_path, callback=Callback())
        assert raised.value is first and calls == ["called"]


def foreign_entry(entry, token, home, guard):
    if entry == "checkpoint":
        return operation._checkpoint_original(token)
    if entry == "consume":
        return operation._consume_check_closed_original(token)
    if entry == "current":
        return lifecycle.current_guard()
    if entry == "require":
        return lifecycle.require_guard(home, exclusive=True)
    if entry == "verify":
        return lifecycle.StorageGuard.verify(guard)
    assert entry == "allows"
    return lifecycle.StorageGuard.allows(guard, home)


@pytest.mark.parametrize("entry", ["checkpoint", "consume", "current", "require", "verify", "allows"])
def test_actual_foreign_thread_is_rejected_without_poisoning_parent(home, tmp_path, entry):
    probes = []
    with held((home,)) as (group_token, guard):
        def callback():
            registration, record, token = captured_live()
            parent_thread = threading.get_ident()
            def probe():
                assert threading.get_ident() != parent_thread and os.getpid() == record.owner[0]
                try:
                    foreign_entry(entry, token, home, guard)
                except KNOWN_ERRORS as error:
                    return error
                raise AssertionError("Foreign thread accessed original operation or guard")
            with ThreadPoolExecutor(max_workers=1) as pool:
                error = pool.submit(copy_context().run, probe).result(timeout=15)
            assert isinstance(error, KNOWN_ERRORS)
            assert record.first is None and record.budget.first is None
            probes.append((registration, token))
        result = check(home, group_token, tmp_path, callback=callback)
        assert len(probes) == 1
        assert_result(result, home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("entry", ["checkpoint", "consume", "current", "require", "verify", "allows"])
def test_actual_eager_foreign_task_is_rejected_and_parent_task_restored(home, tmp_path, entry):
    async def scenario():
        parent_task = asyncio.current_task()
        with held((home,)) as (group_token, guard):
            probes = []
            def callback():
                _registration, record, token = captured_live()
                assert record.owner[2] is parent_task
                captured = {}
                async def probe():
                    captured["task"] = asyncio.current_task()
                    assert captured["task"] is not parent_task
                    assert (os.getpid(), threading.get_ident()) == record.owner[:2]
                    try:
                        foreign_entry(entry, token, home, guard)
                    except KNOWN_ERRORS as error:
                        captured["error"] = error
                        return "rejected"
                    raise AssertionError("Foreign Task accessed original operation or guard")
                task = asyncio.Task(probe(), loop=asyncio.get_running_loop(), eager_start=True)
                assert task.done() and task.result() == "rejected"
                assert captured["task"] is task
                assert asyncio.current_task() is parent_task
                assert record.first is None and record.budget.first is None
                probes.append(captured["error"])
            result = check(home, group_token, tmp_path, callback=callback)
            assert len(probes) == 1
            assert_result(result, home, (home,), "create", "detached_raw2")
    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["normal", "new", "raw1", "raw2"])
def test_real_registered_custom_task_avoids_hash_eq_repr_during_rp1_routing(home, tmp_path, route):
    calls = []
    class ArmedTask(asyncio.Task):
        armed = False
        def __hash__(self):
            if self.armed:
                calls.append("hash")
                raise AssertionError("armed Task hash hook")
            return asyncio.Task.__hash__(self)
        def __eq__(self, other):
            if self.armed:
                calls.append("eq")
                raise AssertionError("armed Task equality hook")
            return asyncio.Task.__eq__(self, other)
        def __repr__(self):
            if self.armed:
                calls.append("repr")
                raise AssertionError("armed Task repr hook")
            return asyncio.Task.__repr__(self)

    async def body():
        task = asyncio.current_task()
        assert type(task) is ArmedTask
        with held((home,)) as (group_token, _guard):
            caught = []
            def callback():
                assert captured_live()[1].owner[2] is task
                if route == "normal":
                    return
                def nested():
                    if route == "new":
                        operation.check_operation_locked()
                    else:
                        module = raw1 if route == "raw1" else raw2
                        module.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                caught.append(operation_error(nested, suffix="REENTRANT"))
            task.armed = True
            try:
                if route == "normal":
                    result = check(home, group_token, tmp_path, callback=callback)
                else:
                    terminal = operation_error(check, home, group_token, tmp_path, callback=callback, suffix="REENTRANT")
                    assert terminal is caught[0]
            finally:
                task.armed = False
            if route == "normal":
                assert_result(result, home, (home,), "create", "detached_raw2")
    async def scenario():
        task = ArmedTask(body(), loop=asyncio.get_running_loop())
        await task
    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("outer", [raw1, raw2])
def test_existing_legacy_owner_lookup_avoids_registered_custom_task_hash(home, tmp_path, outer):
    calls = []
    class ArmedTask(asyncio.Task):
        armed = False
        def __hash__(self):
            if self.armed:
                calls.append("hash")
                raise AssertionError("legacy lookup hashed Task")
            return asyncio.Task.__hash__(self)
        def __eq__(self, other):
            if self.armed:
                calls.append("eq")
                raise AssertionError("legacy lookup compared Task")
            return asyncio.Task.__eq__(self, other)
        def __repr__(self):
            if self.armed:
                calls.append("repr")
                raise AssertionError("legacy lookup repr Task")
            return asyncio.Task.__repr__(self)
    async def body():
        task = asyncio.current_task()
        caught = []
        with lifecycle.exclusive(home):
            def callback():
                task.armed = True
                try:
                    with pytest.raises(raw1.AuthorityRawPreservationError) as raised:
                        operation.check_operation_locked()
                    caught.append(raised.value)
                finally:
                    task.armed = False
            with pytest.raises(raw1.AuthorityRawPreservationError) as terminal:
                outer.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID, check_cancelled=callback)
        assert len(caught) == 1 and terminal.value is caught[0]
    async def scenario():
        task = ArmedTask(body(), loop=asyncio.get_running_loop())
        await task
    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("field", ["token", "owner", "owner_key", "budget", "phase", "first",
    "callback_active", "guard", "group_token", "profiles", "checkpoint_count"])
def test_record_field_fault_rejects_and_original_registry_retires(home, tmp_path, field):
    captured, restoration = [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            current = captured_live()
            captured.append(current)
            record = current[1]
            previous = getattr(record, field)
            replacement = {
                "token": object(), "owner": (0, 0, None), "owner_key": (0, 0, 0), "budget": object(),
                "phase": "closed", "first": RuntimeError("not a raised original failure"),
                "callback_active": False, "guard": object(), "group_token": object(), "profiles": (),
                "checkpoint_count": 99,
            }[field]
            restoration.append((record, field, previous, record.owner_key))
            object.__setattr__(record, field, replacement)
        try:
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
        finally:
            for record, name, previous, _owner_key in restoration:
                object.__setattr__(record, name, previous)
        assert len(captured) == 1
        assert id(captured[0][2]) not in operation._LIVE
        assert restoration[0][3] not in operation._OWNERS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("binding", ["_LIVE", "_OWNERS", "_CLOSED", "_GUARD_CALLS", "_NATIVE_DEBTS"])
def test_registry_alias_fault_preserves_caller_container_and_retires_original(home, tmp_path, binding, monkeypatch):
    original_live, original_owners = operation._LIVE, operation._OWNERS
    replacement = {987654321: object()}
    marker = replacement[987654321]
    captured = []
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                patch.setattr(operation, binding, replacement)
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
            assert set(replacement) == {987654321} and replacement[987654321] is marker
            assert id(captured[0][2]) not in original_live
            assert captured[0][1].owner_key not in original_owners
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("field", FIELDS)
def test_profile_path_pointer_change_during_callback_rejects_even_equal_path(home, tmp_path, field):
    original = getattr(home, field)
    replacement = Path(str(original))
    assert replacement is not original
    with held((home,)) as (group_token, _guard):
        def callback():
            captured_live()
            object.__setattr__(home, field, replacement)
        try:
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
        finally:
            object.__setattr__(home, field, original)
        assert not operation._LIVE and not operation._OWNERS


_S1_TEST_HOOK_EVENTS = []


def trap_code(freevar_count):
    cell0 = cell1 = cell2 = cell3 = None
    def zero(*args, **kwargs):
        _S1_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered downstream function ran")
    def one(*args, **kwargs):
        unused = (cell0,)
        _S1_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered downstream function ran")
    def two(*args, **kwargs):
        unused = (cell0, cell1)
        _S1_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered downstream function ran")
    def three(*args, **kwargs):
        unused = (cell0, cell1, cell2)
        _S1_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered downstream function ran")
    def four(*args, **kwargs):
        unused = (cell0, cell1, cell2, cell3)
        _S1_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered downstream function ran")
    templates = (zero, one, two, three, four)
    assert 0 <= freevar_count < len(templates)
    value = templates[freevar_count].__code__
    assert len(value.co_freevars) == freevar_count
    return value


@pytest.mark.parametrize("callee", ["budget_check", "budget_remember", "budget_settled", "lifecycle_owner",
    "lifecycle_require", "group_guard", "routing", "selector_profile", "operation_checkpoint"])
@pytest.mark.parametrize("part", ["__code__", "__defaults__", "__kwdefaults__"])
def test_same_downstream_function_mutation_cannot_run_hooks_or_fake_exit(home, tmp_path, callee, part, monkeypatch):
    functions = {
        "budget_check": budget.ReplicaBudget.check,
        "budget_remember": budget.ReplicaBudget.remember,
        "budget_settled": budget.ReplicaBudget.assert_settled,
        "lifecycle_owner": lifecycle._owner,
        "lifecycle_require": lifecycle.require_guard,
        "group_guard": group.guard_original,
        "routing": routing.require_no_reentry,
        "selector_profile": selector.project_profile,
        "operation_checkpoint": operation._checkpoint_original,
    }
    function = functions[callee]
    calls, captured = [], []
    class HostileDefault:
        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("default equality hook")
        def __repr__(self):
            calls.append("repr")
            raise AssertionError("default repr hook")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                if part == "__code__":
                    patch.setitem(function.__globals__, "_S1_TEST_HOOK_EVENTS", calls)
                    patch.setattr(function, part, trap_code(len(function.__code__.co_freevars)))
                elif part == "__defaults__":
                    patch.setattr(function, part, (HostileDefault(),))
                else:
                    patch.setattr(function, part, {"_unrelated_default": HostileDefault()})
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
        assert calls == [] and len(captured) == 1
        retired(captured[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("empty", [False, True])
def test_s0_generated_init_closure_fault_is_seen_through_original_projection(home, tmp_path, empty):
    selected = source_for(home, None, tmp_path, "same")
    function = type.__getattribute__(selector.ProfileRaw2Source, "__dict__")["__init__"]
    names = function.__code__.co_freevars
    assert names.count("__dataclass_builtins_object__") == 1
    cell = function.__closure__[names.index("__dataclass_builtins_object__")]
    original = cell.cell_contents
    assert original is object
    calls = []
    class HostileMeta(type):
        def __eq__(cls, other):
            calls.append("eq")
            raise AssertionError("closure equality hook")
        def __repr__(cls):
            calls.append("repr")
            raise AssertionError("closure repr hook")
    class HostileObject(metaclass=HostileMeta):
        @staticmethod
        def __setattr__(instance, name, value):
            calls.append("setattr")
            raise AssertionError("closure allocator hook")
    with held((home,)) as (group_token, _guard):
        def callback():
            captured_live()
            if empty:
                del cell.cell_contents
            else:
                cell.cell_contents = HostileObject
        try:
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, source=selected, callback=callback)
        finally:
            cell.cell_contents = original
        assert calls == [] and cell.cell_contents is object
        assert not operation._LIVE and not operation._OWNERS


def test_failed_token_and_caller_output_cannot_become_closed_history(home, tmp_path):
    failed, good = [], []
    first = RuntimeError("fixed failed callback")
    with held((home,)) as (group_token, _guard):
        def failing():
            failed.append(captured_live()[2])
            raise first
        with pytest.raises(RuntimeError) as raised:
            check(home, group_token, tmp_path, callback=failing)
        assert raised.value is first
        operation_error(operation._consume_check_closed_original, failed[0])
        def normal():
            good.append(captured_live()[2])
        result = check(home, group_token, tmp_path, callback=normal)
        operation_error(operation._consume_check_closed_original, good[0], suffix="CONSUMED")
        operation_error(operation._consume_check_closed_original, result)
        operation_error(operation._checkpoint_original, good[0])
        operation_error(operation._checkpoint_original, object())


def test_normal_retired_operation_does_not_hold_callback_guard_or_task(home, tmp_path):
    references = {}
    class Callback:
        def __call__(self):
            _registration, record, _token = captured_live()
            references["task"] = weakref.ref(record.owner[2])
            references["guard"] = weakref.ref(record.guard)
    async def scenario():
        with held((home,)) as (group_token, _guard):
            callback = Callback()
            references["callback"] = weakref.ref(callback)
            result = check(home, group_token, tmp_path, callback=callback)
            assert_result(result, home, (home,), "create", "detached_raw2")
            del callback
        return None
    asyncio.run(scenario())
    gc.collect()
    assert set(references) == {"task", "guard", "callback"}
    assert all(reference() is None for reference in references.values())


def test_returned_containers_are_detached_and_cannot_authorize_another_operation(home, tmp_path):
    with held((home,)) as (group_token, _guard):
        first = check(home, group_token, tmp_path)
        first["counters"]["read_bytes"] = 999
        first["required_profile_paths"][0][0] = "caller changed value"
        first["write_authority"] = True
        operation_error(operation._consume_check_closed_original, first)
        result = check(home, group_token, tmp_path)
        assert_result(result, home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("field", ["_active", "_read_bytes", "_write_calls", "_current_resources",
    "_pending_resources", "_resource_pending", "_resource_leases", "_started", "_deadline_ns",
    "_readonly", "_callback", "_first"])
def test_checker_ledger_field_fault_never_becomes_real_io_or_normal_completion(home, tmp_path, field):
    restoration, captured = [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            current = captured_live()
            captured.append(current)
            original_budget = current[1].budget
            previous = object.__getattribute__(original_budget, field)
            replacements = {
                "_active": object(), "_read_bytes": 1, "_write_calls": 1,
                "_current_resources": 1, "_pending_resources": 1,
                "_resource_pending": {}, "_resource_leases": {},
                "_started": previous + 1 if field == "_started" else 0,
                "_deadline_ns": previous + 1 if field == "_deadline_ns" else 0,
                "_readonly": not previous if field == "_readonly" else False,
                "_callback": object(), "_first": RuntimeError("not raised by an original caller"),
            }
            restoration.append((original_budget, previous))
            object.__setattr__(original_budget, field, replacements[field])
        try:
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
        finally:
            for original_budget, previous in restoration:
                object.__setattr__(original_budget, field, previous)
        assert len(captured) == 1
        retired(captured[0])
        # These were declared field faults, not resources or permanent debts.
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("target", ["record_owner", "record_guard", "budget_first"])
def test_original_class_descriptor_is_checked_before_dispatching_a_replacement(home, tmp_path, target, monkeypatch):
    calls, captured = [], []
    class HostileDescriptor:
        def __get__(self, instance, owner=None):
            calls.append("descriptor")
            raise AssertionError("Replaced original descriptor executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                current = captured_live()
                captured.append(current)
                if target == "budget_first":
                    value_type, field = budget.ReplicaBudget, "first"
                else:
                    value_type = type(current[1])
                    field = "owner" if target == "record_owner" else "guard"
                patch.setattr(value_type, field, HostileDescriptor())
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
        assert calls == [] and len(captured) == 1
        retired(captured[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("callee", ["_owner_original", "_active_original", "_remember_original"])
@pytest.mark.parametrize("entry", ["lifecycle", "raw1"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_callback_observer_downstream_code_is_pinned_before_it_can_execute(home, tmp_path, callee, entry, restore_before_return, monkeypatch):
    function = getattr(operation, callee)
    original_code = function.__code__
    captured, caught, hooks = [], [], []
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                patch.setitem(function.__globals__, "_S1_TEST_HOOK_EVENTS", hooks)
                patch.setattr(function, "__code__", trap_code(len(function.__code__.co_freevars)))
                assert getattr(operation, callee) is function
                with pytest.raises(KNOWN_ERRORS) as inner:
                    if entry == "lifecycle":
                        lifecycle.current_guard()
                    else:
                        raw1.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                caught.append(inner.value)
                if restore_before_return:
                    function.__code__ = original_code
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
        assert len(captured) == len(caught) == 1
        assert terminal.value is caught[0] and hooks == []
        retired(captured[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("entry", ["lifecycle", "raw1", "new"])
@pytest.mark.parametrize("change_code", [False, True])
def test_empty_catalog_alias_cannot_disable_observer_or_first_protection(home, tmp_path, entry, change_code, monkeypatch):
    original_catalog = operation._CALLEE_PINS
    replacement = ((), (), ())
    assert replacement is not original_catalog
    function = operation._owner_original
    captured, caught, hooks = [], [], []
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                patch.setattr(operation, "_CALLEE_PINS", replacement)
                if change_code:
                    patch.setitem(function.__globals__, "_S1_TEST_HOOK_EVENTS", hooks)
                    patch.setattr(function, "__code__", trap_code(len(function.__code__.co_freevars)))
                with pytest.raises(KNOWN_ERRORS) as inner:
                    if entry == "lifecycle":
                        lifecycle.current_guard()
                    elif entry == "raw1":
                        raw1.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                    else:
                        operation.check_operation_locked()
                caught.append(inner.value)
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
            assert operation._CALLEE_PINS is replacement
            assert replacement == ((), (), ())
        assert operation._CALLEE_PINS is original_catalog
        assert len(captured) == len(caught) == 1
        assert terminal.value is caught[0] and hooks == []
        retired(captured[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


def test_callback_stop_iteration_is_not_replaced_by_generator_protocol_error(home, tmp_path):
    first = StopIteration("original cancellation callback")
    captured = []
    with held((home,)) as (group_token, _guard):
        def callback():
            captured.append(captured_live())
            raise first
        with pytest.raises(StopIteration) as terminal:
            check(home, group_token, tmp_path, callback=callback)
        assert terminal.value is first
        assert len(captured) == 1
        retired(captured[0])
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("field", ["first", "window", "callback_active", "__getattribute__", "__setattr__"])
@pytest.mark.parametrize("path", ["return", "observer", "restore"])
def test_original_registration_descriptor_fault_cannot_dispatch_during_finally(home, tmp_path, field, path, monkeypatch):
    captured, caught, hooks = [], [], []
    class HostileDescriptor:
        def __get__(self, instance, owner=None):
            hooks.append("get")
            raise AssertionError("replacement registration descriptor read")
        def __set__(self, instance, value):
            hooks.append("set")
            raise AssertionError("replacement registration descriptor write")
        def __delete__(self, instance):
            hooks.append("delete")
            raise AssertionError("replacement registration descriptor delete")
    def hostile_getattribute(instance, name):
        hooks.append("getattribute")
        raise AssertionError("replacement registration getattribute")
    def hostile_setattr(instance, name, value):
        hooks.append("setattr")
        raise AssertionError("replacement registration setattr")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                current = captured_live()
                captured.append(current)
                original_type = type(current[0])
                replacement = (hostile_getattribute if field == "__getattribute__" else
                               hostile_setattr if field == "__setattr__" else HostileDescriptor())
                patch.setattr(original_type, field, replacement)
                if path != "return":
                    with pytest.raises(KNOWN_ERRORS) as inner:
                        lifecycle.current_guard()
                    caught.append(inner.value)
                if path == "restore":
                    patch.undo()
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
        assert len(captured) == 1 and hooks == []
        if path == "return":
            assert caught == []
        else:
            assert len(caught) == 1 and terminal.value is caught[0]
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("path", ["return", "observer", "restore"])
def test_actual_builtin_global_shadow_cannot_run_or_replace_original_first(home, tmp_path, path, monkeypatch):
    captured, caught, hooks = [], [], []
    original_globals = operation._owner_original.__globals__
    assert original_globals is operation.__dict__ and "id" not in original_globals
    def hostile_id(value):
        hooks.append("id")
        raise AssertionError("operation builtin shadow executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                patch.setitem(original_globals, "id", hostile_id)
                if path != "return":
                    with pytest.raises(KNOWN_ERRORS) as inner:
                        lifecycle.current_guard()
                    caught.append(inner.value)
                if path == "restore":
                    patch.undo()
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
            if path != "restore":
                assert original_globals["id"] is hostile_id
        assert "id" not in original_globals
        assert len(captured) == 1 and hooks == []
        if path == "return":
            assert caught == []
        else:
            assert len(caught) == 1 and terminal.value is caught[0]
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("source_fault", ["sys_modules", "parent_package", "builtin_import"])
@pytest.mark.parametrize("entry", ["lifecycle", "raw1"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_fixed_operation_import_source_fault_rejects_before_fake_observer(home, tmp_path, source_fault, entry, restore_before_return, monkeypatch):
    import builtins
    import sys
    from types import ModuleType
    import reno as package

    module_name = "reno.personal_authority_replica_operation"
    relative_name = "personal_authority_replica_operation"
    assert sys.modules[module_name] is operation
    assert package.personal_authority_replica_operation is operation
    original_import = builtins.__import__
    fake = ModuleType(module_name)
    captured, caught, hooks = [], [], []
    def fake_observer(*args, **kwargs):
        hooks.append("observer")
        raise AssertionError("replacement operation observer executed")
    def fake_attribute(name):
        hooks.append("attribute")
        raise AssertionError("replacement operation module inspected")
    fake._observe_lifecycle_entry_original = fake_observer
    fake._reject_reentry_original = fake_observer
    fake.__getattr__ = fake_attribute
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        reno_context = type(globals) is dict and globals.get("__package__") == "reno"
        named_member = type(fromlist) in (tuple, list) and relative_name in fromlist
        target = (
            (name == module_name and level == 0)
            or (name == relative_name and level == 1 and reno_context)
            or (name == "reno" and level == 0 and named_member)
            or (name == "" and level == 1 and reno_context and named_member)
        )
        if target:
            hooks.append("import")
            return fake
        return original_import(name, globals, locals, fromlist, level)

    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                if source_fault == "sys_modules":
                    patch.setitem(sys.modules, module_name, fake)
                elif source_fault == "parent_package":
                    patch.setattr(package, relative_name, fake)
                else:
                    patch.setattr(builtins, "__import__", fake_import)
                try:
                    if entry == "lifecycle":
                        lifecycle.current_guard()
                    else:
                        raw1.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                except KNOWN_ERRORS as exc:
                    caught.append(exc)
                else:
                    raise AssertionError("replacement import source was accepted")
                if restore_before_return:
                    patch.undo()
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
        assert sys.modules[module_name] is operation
        assert package.personal_authority_replica_operation is operation
        assert builtins.__import__ is original_import
        assert len(captured) == len(caught) == 1
        assert terminal.value is caught[0] and hooks == []
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("callee", ["lifecycle_owner", "asyncio_current_task"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_actual_owner_callee_code_fault_keeps_first_after_original_code_is_restored(home, tmp_path, callee, restore_before_return, monkeypatch):
    python_callee = callee == "lifecycle_owner"
    function = lifecycle._owner if python_callee else asyncio.current_task
    if python_callee:
        original_code = function.__code__
    else:
        from types import BuiltinFunctionType
        assert type(function) is BuiltinFunctionType
        assert function is asyncio.tasks.current_task
    captured, caught, hooks = [], [], []
    def replacement(*args, **kwargs):
        hooks.append("current_task")
        raise AssertionError("replacement actual current_task binding executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                if python_callee:
                    patch.setitem(function.__globals__, "_S1_TEST_HOOK_EVENTS", hooks)
                    patch.setattr(function, "__code__", trap_code(len(original_code.co_freevars)))
                else:
                    # Fault the current C callee's binding only after real registration.
                    patch.setattr(asyncio, "current_task", replacement)
                try:
                    lifecycle.current_guard()
                except KNOWN_ERRORS as exc:
                    caught.append(exc)
                else:
                    raise AssertionError("changed actual owner callee was accepted")
                if not python_callee:
                    assert type(caught[-1]) is ERROR
                    assert caught[-1].code == ERROR_PREFIX + "RECORD_CHANGED"
                    assert captured[-1][1].first is caught[-1]
                    assert captured[-1][1].budget.first is caught[-1]
                if restore_before_return:
                    if python_callee:
                        function.__code__ = original_code
                    else:
                        patch.setattr(asyncio, "current_task", function)
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
        if python_callee:
            assert function.__code__ is original_code
        else:
            assert asyncio.current_task is function
            assert asyncio.tasks.current_task is function
        assert len(captured) == len(caught) == 1
        assert terminal.value is caught[0] and hooks == []
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("field", ["__getattribute__", "__setattr__"])
@pytest.mark.parametrize("path", ["return", "observer", "restore"])
def test_actual_budget_attribute_fault_does_not_execute_during_callback_cleanup(home, tmp_path, field, path, monkeypatch):
    captured, caught, hooks = [], [], []
    def hostile_getattribute(instance, name):
        hooks.append("getattribute")
        raise AssertionError("changed budget getattribute executed")
    def hostile_setattr(instance, name, value):
        hooks.append("setattr")
        raise AssertionError("changed budget setattr executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                current = captured_live()
                captured.append(current)
                actual_type = type(current[1].budget)
                assert actual_type is budget.ReplicaBudget
                replacement = hostile_getattribute if field == "__getattribute__" else hostile_setattr
                patch.setattr(actual_type, field, replacement)
                if path != "return":
                    try:
                        lifecycle.current_guard()
                    except KNOWN_ERRORS as exc:
                        caught.append(exc)
                    else:
                        raise AssertionError("changed budget attributes were accepted")
                if path == "restore":
                    patch.undo()
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
        assert len(captured) == 1 and hooks == []
        if path == "return":
            assert caught == []
        else:
            assert len(caught) == 1 and terminal.value is caught[0]
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("name", ["dict", "BaseException", "StopIteration"])
@pytest.mark.parametrize("path", ["return", "observer", "restore"])
def test_cleanup_builtin_global_shadow_cannot_dispatch_or_erase_first(home, tmp_path, name, path, monkeypatch):
    missing = object()
    original = operation.__dict__.get(name, missing)
    captured, caught, hooks = [], [], []
    class HostileGlobal:
        def __call__(self, *args, **kwargs):
            hooks.append("call")
            raise AssertionError("changed cleanup global called")
        def __eq__(self, other):
            hooks.append("eq")
            raise AssertionError("changed cleanup global compared")
        def __hash__(self):
            hooks.append("hash")
            raise AssertionError("changed cleanup global hashed")
        def __repr__(self):
            hooks.append("repr")
            raise AssertionError("changed cleanup global rendered")
    replacement = HostileGlobal()
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                captured.append(captured_live())
                patch.setitem(operation.__dict__, name, replacement)
                if path != "return":
                    try:
                        lifecycle.current_guard()
                    except KNOWN_ERRORS as exc:
                        caught.append(exc)
                    else:
                        raise AssertionError("changed cleanup global was accepted")
                if path == "restore":
                    patch.undo()
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
            if path != "restore":
                assert operation.__dict__[name] is replacement
        if original is missing:
            assert name not in operation.__dict__
        else:
            assert operation.__dict__[name] is original
        assert len(captured) == 1 and hooks == []
        if path == "return":
            assert caught == []
        else:
            assert len(caught) == 1 and terminal.value is caught[0]
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("entry", ["lifecycle", "raw1"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_original_shared_cache_element_cannot_be_replaced_with_fake_dispatch(home, tmp_path, entry, restore_before_return):
    captured, caught, hooks, original_cache = [], [], [], []
    def fake_dispatch(*args, **kwargs):
        hooks.append("dispatch")
        raise AssertionError("replacement shared cached dispatch executed")
    with held((home,)) as (group_token, _guard):
        def callback():
            captured.append(captured_live())
            gate = lifecycle._REPLICA_OPERATION_GATE_ORIGINAL
            names = gate.__code__.co_freevars
            assert names.count("bound") == 1
            cache = gate.__closure__[names.index("bound")].cell_contents
            assert type(cache) is list and len(cache) == 1
            dispatch = cache[0]
            assert dispatch is operation._shared_entry_original
            routing_names = routing.require_no_reentry.__code__.co_freevars
            assert routing_names.count("gate") == 1
            assert routing.require_no_reentry.__closure__[routing_names.index("gate")].cell_contents is gate
            original_cache.append((cache, dispatch))
            cache[0] = fake_dispatch
            try:
                if entry == "lifecycle":
                    lifecycle.current_guard()
                else:
                    raw1.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
            except KNOWN_ERRORS as exc:
                caught.append(exc)
            else:
                raise AssertionError("changed shared cache element was accepted")
            if restore_before_return:
                cache[0] = dispatch
        try:
            with pytest.raises(KNOWN_ERRORS) as terminal:
                check(home, group_token, tmp_path, callback=callback)
            assert len(original_cache) == 1
            cache, dispatch = original_cache[0]
            assert len(cache) == 1
            assert cache[0] is (dispatch if restore_before_return else fake_dispatch)
        finally:
            for cache, dispatch in original_cache:
                cache[0] = dispatch
        assert len(captured) == len(caught) == 1
        assert terminal.value is caught[0] and hooks == []
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")


@pytest.mark.parametrize("field", ["_first", "_in_callback"])
def test_actual_budget_dict_collision_key_is_rejected_without_key_hooks_or_repair(home, tmp_path, field):
    captured, hooks, restoration = [], [], []
    armed = False
    collision_hash = hash(field)
    class CollisionKey:
        def __hash__(self):
            if armed:
                hooks.append("hash")
                raise AssertionError("product hashed a non-string budget key")
            return collision_hash
        def __eq__(self, other):
            if armed:
                hooks.append("eq")
                raise AssertionError("product compared a non-string budget key")
            return other == field
        def __repr__(self):
            if armed:
                hooks.append("repr")
                raise AssertionError("product rendered a non-string budget key")
            return "CollisionKey"
    forged = CollisionKey()
    with held((home,)) as (group_token, _guard):
        def callback():
            nonlocal armed
            current = captured_live()
            captured.append(current)
            actual_budget = current[1].budget
            state = object.__getattribute__(actual_budget, "__dict__")
            assert type(state) is dict and all(type(key) is str for key in state)
            original = state[field]
            restoration.append((state, original))
            del state[field]
            state[forged] = original
            assert any(key is forged for key in state)
            hooks.clear()
            armed = True
        try:
            with pytest.raises(KNOWN_ERRORS):
                check(home, group_token, tmp_path, callback=callback)
            assert len(restoration) == 1
            assert any(key is forged for key in restoration[0][0])
        finally:
            armed = False
            for state, original in restoration:
                del state[forged]
                state[field] = original
        assert len(captured) == 1 and hooks == []
        retired(captured[0])
        assert not operation._NATIVE_DEBTS
        assert_result(check(home, group_token, tmp_path), home, (home,), "create", "detached_raw2")
