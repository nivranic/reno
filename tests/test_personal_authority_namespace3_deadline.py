"""Finite absolute-deadline tests using real namespace3 origin/file fixtures.

Only new modules' clock aliases and an authorized consumer publication alias
are replaced. Old Budget/perform/time and native ABIs remain untouched. Timer
faults are synthetic checkpoint fixtures, not native hard-interruption proof.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import os

import pytest

from reno import personal_authority_namespace3 as locked
from reno import personal_authority_namespace3_contract as contract
from reno import personal_authority_namespace3_io as publication
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_deadline_owner as owner
from reno import personal_authority_namespace3_owner as old_owner
from reno import storage_lifecycle as lifecycle
from tests.test_personal_authority_namespace3 import namespace_env, originals, records, tree, sha


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real namespace3 Windows creator HANDLE fixture")


class FloatSubclass(float):
    pass


class Overlay:
    def __init__(self, original, **overrides):
        self.original, self.overrides = original, overrides
    def __getattr__(self, name):
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.original, name)


def synthetic_clock(monkeypatch, value=0.0):
    clock = [value]
    provider = Overlay(deadline.time, monotonic=lambda: clock[0])
    monkeypatch.setattr(deadline, "time", provider)
    monkeypatch.setattr(owner, "time", provider)
    return clock


def error(call, suffix):
    with pytest.raises(locked.Namespace3Error) as raised:
        call()
    assert raised.value.code == "NAMESPACE3_" + suffix


def owner_error(call, suffix):
    with pytest.raises(old_owner.Namespace3OwnerError) as raised:
        call()
    assert raised.value.code == "NAMESPACE3_OWNER_" + suffix


def initialize(env, **options):
    return owner.initialize_namespace3(env["profile"], **env["args"], **env["expected"], **options)


def verify(env, result):
    return owner.verify_namespace3(env["profile"], **env["args"], **env["expected"],
        expected_namespace_reservation_sha256=result.namespace_reservation_sha256,
        expected_root_seal_sha256=result.root_seal_sha256, expected_header_sha256=result.header_sha256)


def capture_budget(monkeypatch, observations):
    real_perform = locked._perform
    def perform(*args, **kwargs):
        budget = args[-1]
        observations["budget"] = budget
        observations.setdefault("calls", []).append(budget)
        result = real_perform(*args, **kwargs)
        observations["facts"] = result
        return result
    monkeypatch.setattr(deadline, "locked", Overlay(locked, _perform=perform))


@pytest.mark.parametrize("value", [True, 1, FloatSubclass(1.0), float("nan"),
                                   float("inf"), float("-inf"), -1.0, "30.0"],
                         ids=["bool", "int", "float-subclass", "nan", "positive-inf", "negative-inf", "negative", "text"])
def test_token_requires_exact_finite_nonnegative_float(value):
    error(lambda: deadline.Namespace3Deadline(value), "INVALID_ARGUMENT")


def test_token_is_frozen_slotted_current_clock_data_without_authority_fields():
    token = deadline.Namespace3Deadline(0.0)
    assert token.absolute_monotonic == 0.0 and type(token.absolute_monotonic) is float
    assert not hasattr(token, "__dict__")
    assert set(token.__slots__) == {"absolute_monotonic"}
    with pytest.raises(FrozenInstanceError):
        token.absolute_monotonic = 100.0


@pytest.mark.parametrize("kind", ["subclass", "missing-slot", "bypassed-invalid"],
                         ids=["token-subclass", "token-missing", "token-invalid"])
def test_adapter_revalidates_exact_token_instead_of_adopting_forged_deadline(namespace_env, monkeypatch, kind):
    env = namespace_env
    synthetic_clock(monkeypatch)
    class Child(deadline.Namespace3Deadline):
        pass
    if kind == "subclass":
        token = Child(30.0)
    else:
        token = object.__new__(deadline.Namespace3Deadline)
        if kind == "bypassed-invalid":
            object.__setattr__(token, "absolute_monotonic", float("nan"))
    before = tree(env)
    def forbidden(*args, **kwargs):
        pytest.fail("malformed token reached original perform")
    monkeypatch.setattr(deadline, "locked", Overlay(locked, _perform=forbidden))
    error(lambda: deadline.initialize_namespace3_locked(env["profile"], **env["args"],
        **env["expected"], deadline_token=token), "INVALID_ARGUMENT")
    assert tree(env) == before and lifecycle.current_guard() is None


@pytest.mark.parametrize("absolute", [0.0, 30.0], ids=["expired", "equal-now"])
def test_expired_or_equal_token_is_refused_before_original_perform_or_resources(namespace_env, monkeypatch, absolute):
    env = namespace_env
    synthetic_clock(monkeypatch, 30.0)
    before = tree(env)
    def forbidden(*args, **kwargs):
        pytest.fail("expired token entered original perform")
    monkeypatch.setattr(deadline, "locked", Overlay(locked, _perform=forbidden))
    error(lambda: deadline.initialize_namespace3_locked(env["profile"], **env["args"],
        **env["expected"], deadline_token=deadline.Namespace3Deadline(absolute)), "DEADLINE")
    assert tree(env) == before and lifecycle.current_guard() is None


def test_requested_future_token_cannot_extend_original_local_thirty_seconds_or_refresh_deadline(monkeypatch):
    clock = synthetic_clock(monkeypatch, 5.0)
    token = deadline.Namespace3Deadline(100.0)
    budget = deadline._RestrictedBudget(token)
    assert budget.deadline == 35.0 and budget.callback is None
    assert budget.requested_deadline is token
    assert type(budget.effective_deadline) is deadline.Namespace3Deadline
    assert budget.effective_deadline.absolute_monotonic == 35.0
    budget.name("ordinary-first")
    budget.name("普通")
    budget.charge(7)
    before = budget.names, budget.name_bytes, budget.control_bytes
    with pytest.raises(AttributeError):
        budget.deadline = 100.0
    for field in ("requested_deadline", "effective_deadline"):
        with pytest.raises(AttributeError):
            setattr(budget, field, deadline.Namespace3Deadline(100.0))
    assert before == (2, len("ordinary-first".encode()) + len("普通".encode()), 7)
    clock[0] = 34.0
    assert budget.request(9) == 9 and budget.deadline == 35.0
    assert (budget.names, budget.name_bytes, budget.control_bytes) == before
    clock[0] = 35.0
    error(budget.clock_only, "DEADLINE")
    assert (budget.names, budget.name_bytes, budget.control_bytes) == before


def test_earlier_absolute_token_is_same_effective_object_at_every_budget_check(monkeypatch):
    clock = synthetic_clock(monkeypatch, 5.0)
    token = deadline.Namespace3Deadline(10.0)
    budget = deadline._RestrictedBudget(token)
    assert budget.requested_deadline is token and budget.effective_deadline is token
    budget.name("ordinary")
    clock[0] = 9.0
    budget.poll()
    assert budget.request(3) == 3 and budget.effective_deadline is token
    clock[0] = 10.0
    error(budget.before_next, "DEADLINE")
    assert budget.deadline == 10.0 and budget.effective_deadline is token


@pytest.mark.parametrize("limit", ["names", "name-bytes", "control-bytes"],
                         ids=["original-name-count", "original-utf8-cap", "original-control-cap"])
def test_restricted_budget_keeps_original_cumulative_caps_and_never_refreshes_allowance(monkeypatch, limit):
    """Counter unit inputs are synthetic, without filesystem/native I/O claims."""
    synthetic_clock(monkeypatch)
    budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(30.0))
    if limit == "names":
        for index in range(locked.MAX_NAMES):
            budget.before_next()
            budget.name("ordinary-" + str(index))
        error(budget.before_next, "LIMIT")
        assert budget.names == locked.MAX_NAMES
    elif limit == "name-bytes":
        budget.name("汉" * (locked.MAX_NAME_BYTES // 3))
        budget.name("A")
        error(lambda: budget.name("B"), "LIMIT")
        assert budget.name_bytes == locked.MAX_NAME_BYTES and budget.names == 2
    else:
        budget.charge(locked.MAX_CONTROL_BYTES - 1)
        assert budget.request(8192) == 1
        budget.charge(1)
        error(lambda: budget.request(1), "LIMIT")
        assert budget.control_bytes == locked.MAX_CONTROL_BYTES


def test_owner_deadline_token_and_float_view_are_readonly_and_keep_original_absolute_value(monkeypatch):
    clock = synthetic_clock(monkeypatch)
    budget = owner._OwnerBudget(None)
    token = budget.deadline_token
    assert type(token) is deadline.Namespace3Deadline and token.absolute_monotonic == 30.0
    assert budget.deadline == 30.0
    with pytest.raises(AttributeError):
        budget.deadline = 60.0
    with pytest.raises(AttributeError):
        budget.deadline_token = deadline.Namespace3Deadline(60.0)
    clock[0] = 29.0
    budget.clock_only()
    assert budget.deadline_token is token
    clock[0] = 30.0
    owner_error(budget.clock_only, "DEADLINE")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0],
                         ids=["owner-clock-nan", "owner-clock-inf", "owner-clock-negative"])
def test_owner_rejects_invalid_clock_before_any_deadline_or_guard_effect(monkeypatch, value):
    synthetic_clock(monkeypatch, value)
    owner_error(lambda: owner._OwnerBudget(None), "INVALID_ARGUMENT")


def test_original_charge_records_actual_bytes_before_its_post_charge_expiry_gate(monkeypatch):
    clock = synthetic_clock(monkeypatch)
    budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(10.0))
    budget.charge(4)
    clock[0] = 10.0
    error(lambda: budget.charge(3), "DEADLINE")
    assert budget.control_bytes == 7  # actual amount charged before the original clock gate


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0], ids=["clock-nan", "clock-inf", "clock-negative"])
def test_nonfinite_or_negative_local_clock_does_not_disable_deadline_comparison(monkeypatch, value):
    clock = synthetic_clock(monkeypatch)
    budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(30.0))
    clock[0] = value
    error(budget.clock_only, "INVALID_ARGUMENT")


@pytest.mark.parametrize("guard_kind", ["none", "sh"], ids=["no-ex", "shared-only"])
def test_new_locked_adapter_still_requires_actual_caller_ex(namespace_env, monkeypatch, guard_kind):
    env = namespace_env
    synthetic_clock(monkeypatch)
    before = tree(env)
    call = lambda: deadline.initialize_namespace3_locked(env["profile"], **env["args"],
        **env["expected"], deadline_token=deadline.Namespace3Deadline(30.0))
    if guard_kind == "none":
        error(call, "GUARD_REQUIRED")
    else:
        with lifecycle.shared(env["profile"]) as guard:
            error(call, "GUARD_REQUIRED")
            assert lifecycle.current_guard() is guard
    assert tree(env) == before


def test_actual_new_owner_returns_same_inner_facts_only_after_own_ex_and_does_not_replace_old_budget(namespace_env, monkeypatch):
    env, observed = namespace_env, {}
    old_budget, old_perform, old_time = locked._Budget, locked._perform, locked.time
    synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    actual_exclusive = lifecycle.exclusive
    actual_initialize = deadline.initialize_namespace3_locked
    def inner(profile, **options):
        observed["supplied_token"] = options["deadline_token"]
        return actual_initialize(profile, **options)
    @contextmanager
    def exclusive(profile, **options):
        assert options["timeout"] == 0
        with actual_exclusive(profile, **options) as guard:
            observed["guard"] = guard
            yield guard
            assert observed["facts"].scope == contract.SCOPE
        observed["exited"] = True
        assert lifecycle.current_guard() is None
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=exclusive))
    monkeypatch.setattr(owner, "adapter", Overlay(deadline, initialize_namespace3_locked=inner))
    result = initialize(env)
    assert result is observed["facts"] and observed["exited"]
    assert observed["budget"].deadline == 30.0 and len(observed["calls"]) == 1
    assert observed["budget"].requested_deadline is observed["supplied_token"]
    assert locked._Budget is old_budget and locked._perform is old_perform and locked.time is old_time
    assert originals(env) == (env["first"], env["second"])
    with actual_exclusive(env["profile"]):
        assert lifecycle.require_guard(env["profile"], exclusive=True)


def test_owner_callback_consumes_original_time_before_adapter_and_only_runs_once(namespace_env, monkeypatch):
    env, observed = namespace_env, {}
    clock = synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            assert lifecycle.current_guard() is None
            self.calls += 1
            clock[0] = 15.0
    callback = Callback()
    initialize(env, check_cancelled=callback)
    assert callback.calls == 1 and callback.bool_calls == 0
    assert observed["budget"].deadline == 30.0  # local adapter start+30 would have been45
    assert lifecycle.current_guard() is None


@pytest.mark.parametrize("guard_kind", ["sh", "ex"], ids=["held-sh", "held-ex"])
def test_new_public_owner_never_borrows_or_retires_existing_guard(namespace_env, monkeypatch, guard_kind):
    env = namespace_env
    synthetic_clock(monkeypatch)
    before = tree(env)
    with (lifecycle.shared if guard_kind == "sh" else lifecycle.exclusive)(env["profile"]) as guard:
        owner_error(lambda: initialize(env), "GUARD_ALREADY_HELD")
        assert lifecycle.current_guard() is guard
    assert tree(env) == before


@pytest.mark.parametrize("failure", [RuntimeError("original cancellation"), SystemExit(822)], ids=["ordinary", "control"])
def test_callback_original_object_precedes_any_new_ex_and_inner_call(namespace_env, monkeypatch, failure):
    env = namespace_env
    synthetic_clock(monkeypatch)
    before = tree(env)
    def forbidden(*args, **kwargs):
        pytest.fail("failed callback entered new EX")
    def callback():
        raise failure
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=forbidden))
    with pytest.raises(type(failure)) as raised:
        initialize(env, check_cancelled=callback)
    assert raised.value is failure and tree(env) == before


def test_same_parent_expiry_after_actual_r_publisher_return_blocks_next_checkpoint_and_root(namespace_env, monkeypatch):
    env, observed, events = namespace_env, {}, []
    clock = synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    actual_publish = publication.publish_new_locked
    def publish(*args, **kwargs):
        result = actual_publish(*args, **kwargs)
        if args[2] == contract.RESERVATION_NAME:
            events.append("actual-r-publisher-return")
            clock[0] = observed["budget"].deadline
        return result
    @contextmanager
    def root(*args, **kwargs):
        events.append("root-call")
        pytest.fail("known expiry before root was not rejected")
        yield
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication, publish_new_locked=publish, create_root_locked=root))
        error(lambda: initialize(env), "DEADLINE")
    reserved = (env["data"] / contract.RESERVATION_NAME).read_bytes()
    contract.cross_validate_reservation(env["first"], env["second"], reserved)
    assert events == ["actual-r-publisher-return"]
    assert not (env["data"] / contract.ROOT_NAME).exists() and not (env["data"] / contract.HEADER_NAME).exists()
    assert not any(path.name.endswith(".stage") for path in env["data"].iterdir())
    assert originals(env) == (env["first"], env["second"]) and lifecycle.current_guard() is None


def test_actual_r_only_can_resume_with_a_new_owner_token_without_rewriting_reservation(namespace_env, monkeypatch):
    env = namespace_env
    clock = synthetic_clock(monkeypatch)
    actual_publish = publication.publish_new_locked
    def publish(*args, **kwargs):
        result = actual_publish(*args, **kwargs)
        if args[2] == contract.RESERVATION_NAME:
            clock[0] = 30.0
        return result
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication, publish_new_locked=publish))
        error(lambda: initialize(env), "DEADLINE")
    reserved = (env["data"] / contract.RESERVATION_NAME).read_bytes()
    clock[0] = 50.0
    result = owner.resume_unstarted_namespace3(env["profile"], **env["args"], **env["expected"],
        expected_namespace_reservation_sha256=sha(reserved))
    assert (env["data"] / contract.RESERVATION_NAME).read_bytes() == reserved
    assert result.namespace_reservation_sha256 == sha(reserved)
    assert verify(env, result) == result and lifecycle.current_guard() is None


def test_actual_creator_root_normal_exit_then_expiry_leaves_complete_files_without_late_facts(namespace_env, monkeypatch):
    env, observed, closed = namespace_env, {}, []
    clock = synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    actual_root = publication.create_root_locked
    @contextmanager
    def root(*args, **kwargs):
        with actual_root(*args, **kwargs) as directory:
            yield directory
        closed.append(directory.owner.retired)
        clock[0] = observed["budget"].deadline
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication, create_root_locked=root))
        error(lambda: initialize(env), "DEADLINE")
    assert closed == [True]
    contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert originals(env) == (env["first"], env["second"]) and lifecycle.current_guard() is None


def test_actual_owned_ex_normal_exit_at_absolute_expiry_still_blocks_result_delivery(namespace_env, monkeypatch):
    env, observed = namespace_env, {}
    clock = synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    actual_exclusive = lifecycle.exclusive
    @contextmanager
    def exclusive(profile, **options):
        with actual_exclusive(profile, **options) as guard:
            yield guard
        assert lifecycle.current_guard() is None
        clock[0] = 30.0
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=exclusive))
    owner_error(lambda: initialize(env), "DEADLINE")
    assert type(observed["facts"]) is locked.Namespace3FilesystemFacts
    contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert lifecycle.current_guard() is None


def test_real_publication_and_read_phases_share_budget_identity_and_never_reset_counters(namespace_env, monkeypatch):
    env, observed, snapshots = namespace_env, {}, []
    synthetic_clock(monkeypatch)
    capture_budget(monkeypatch, observed)
    actual_publish, actual_read, actual_root = publication.publish_new_locked, publication.read_control_locked, publication.create_root_locked
    def snapshot(kind, budget, directory=None):
        assert budget is observed["budget"]
        if directory is not None:
            assert directory.budget is budget
        snapshots.append((kind, budget.names, budget.name_bytes, budget.control_bytes))
    def publish(*args, **kwargs):
        snapshot("publish-before", args[5], args[1])
        result = actual_publish(*args, **kwargs)
        snapshot("publish-after", args[5], args[1])
        return result
    @contextmanager
    def read(*args, **kwargs):
        snapshot("read-before", args[3], args[1])
        with actual_read(*args, **kwargs) as held:
            snapshot("read-ready", args[3], args[1])
            yield held
        snapshot("read-closed", args[3], args[1])
    @contextmanager
    def root(*args, **kwargs):
        snapshot("root-before", args[2], args[1])
        with actual_root(*args, **kwargs) as directory:
            snapshot("root-ready", args[2], directory)
            yield directory
        snapshot("root-closed", args[2])
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication,
            publish_new_locked=publish, read_control_locked=read, create_root_locked=root))
        result = initialize(env)
    assert len(observed["calls"]) == 1
    assert [row[0] for row in snapshots].count("publish-after") == 3
    for earlier, later in zip(snapshots, snapshots[1:]):
        assert all(first <= second for first, second in zip(earlier[1:], later[1:]))
    budget = observed["budget"]
    assert snapshots[0][3] > 0  # actual node FileIO already charged before first control read
    assert 0 < budget.names <= locked.MAX_NAMES and 0 < budget.name_bytes <= locked.MAX_NAME_BYTES
    assert sum(map(len, records(env))) < budget.control_bytes <= locked.MAX_CONTROL_BYTES
    assert result is observed["facts"] and originals(env) == (env["first"], env["second"])


def test_new_complete_verify_is_readonly_under_same_deadline_and_caller_expected_hashes(namespace_env, monkeypatch):
    env = namespace_env
    synthetic_clock(monkeypatch)
    result = initialize(env)
    before = tree(env)
    def forbidden(*args, **kwargs):
        pytest.fail("completed read-only verify called authority create/publish")
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication,
            publish_new_locked=forbidden, create_root_locked=forbidden))
        assert verify(env, result) == result
    assert tree(env) == before and lifecycle.current_guard() is None


def test_owner_callback_expiry_at_thirty_precedes_new_ex_and_keeps_original_tree(namespace_env, monkeypatch):
    env = namespace_env
    clock = synthetic_clock(monkeypatch)
    before = tree(env)
    def forbidden(*args, **kwargs):
        pytest.fail("owner equality expiry entered EX")
    def callback():
        clock[0] = 30.0
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=forbidden))
    owner_error(lambda: initialize(env, check_cancelled=callback), "DEADLINE")
    assert tree(env) == before and lifecycle.current_guard() is None


def test_actual_complete_files_survive_normal_ex_close_then_explicit_failure_without_delivery(namespace_env, monkeypatch):
    env = namespace_env
    synthetic_clock(monkeypatch)
    actual_exclusive = lifecycle.exclusive
    failure = RuntimeError("explicit failure after real normal EX close")
    @contextmanager
    def exclusive(profile, **options):
        with actual_exclusive(profile, **options) as guard:
            yield guard
        assert lifecycle.current_guard() is None
        raise failure
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=exclusive))
    with pytest.raises(RuntimeError) as raised:
        initialize(env)
    assert raised.value is failure and lifecycle.current_guard() is None
    contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert originals(env) == (env["first"], env["second"])


@pytest.mark.parametrize("failure", [RuntimeError("original after real R"), SystemExit(822)],
                         ids=["body-ordinary", "body-control"])
def test_actual_r_publication_body_failure_wins_over_real_ex_exit_then_cleanup_fault(namespace_env, monkeypatch, failure):
    env = namespace_env
    synthetic_clock(monkeypatch)
    actual_exclusive, actual_publish = lifecycle.exclusive, publication.publish_new_locked
    closed = []
    @contextmanager
    def exclusive(profile, **options):
        try:
            with actual_exclusive(profile, **options) as guard:
                yield guard
        finally:
            closed.append(lifecycle.current_guard() is None)
            raise RuntimeError("explicit cleanup fault after real EX exit")
    def publish(*args, **kwargs):
        result = actual_publish(*args, **kwargs)
        if args[2] == contract.RESERVATION_NAME:
            raise failure
        return result
    monkeypatch.setattr(owner, "lifecycle", Overlay(lifecycle, exclusive=exclusive))
    with monkeypatch.context() as patch:
        patch.setattr(locked, "publication", Overlay(publication, publish_new_locked=publish))
        with pytest.raises(type(failure)) as raised:
            initialize(env)
        assert raised.value is failure
    assert closed == [True] and lifecycle.current_guard() is None
    assert "NAMESPACE3_OWNER_CLEANUP_FAILED" in getattr(failure, "__notes__", [])
    reserved = (env["data"] / contract.RESERVATION_NAME).read_bytes()
    contract.cross_validate_reservation(env["first"], env["second"], reserved)
    assert not (env["data"] / contract.ROOT_NAME).exists()
    assert originals(env) == (env["first"], env["second"])
