"""Independent RP1 ledger faults and one small native file trace.

Large quota frontiers use explicitly injected historical counter states; they
are not TiB I/O or acquired-handle evidence. Synthetic resource returns test
accounting and cleanup-lease delivery only. The final trace performs native
file I/O, but is neither a replica factory nor a source/target close proof.
"""
import os
from types import MappingProxyType

import pytest

from reno import personal_authority_replica_budget as budget
from reno import personal_authority_replica_policy as policy


ERROR = budget.AuthorityReplicaBudgetError
PREFIX = "AUTHORITY_REPLICA_BUDGET1_"
DEADLINE_NS = 1800000000000
READ_CAP = 2199023255552
SOURCE_CAP = 1099511627776
WRITE_CAP = 274877906944
FAILURE_CAP = 16384
RESOURCE_CAP = 1000000
SNAPSHOT_FIELDS = {
    "read_bytes", "source_read_bytes", "read_calls", "source_read_calls", "seek_calls",
    "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes", "elapsed_ns",
    "current_resources", "peak_resources", "pending_resources", "uncertain_resource_acquires",
}


@pytest.fixture
def tick(monkeypatch):
    """Explicit clock fault seam; callers cannot supply a clock to the API."""
    current = [5000000]
    monkeypatch.setattr(budget.time, "monotonic_ns", lambda: current[0])
    return current


def reject(function, *args, suffix=None, **kwargs):
    with pytest.raises(ERROR) as raised:
        function(*args, **kwargs)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith(PREFIX)
    if suffix is not None:
        assert raised.value.code == PREFIX + suffix
    return raised.value


def same_error(original, function, *args, **kwargs):
    with pytest.raises(type(original)) as raised:
        function(*args, **kwargs)
    assert raised.value is original


def payload_demand():
    return policy.payload_demand({
        "package_file_count": 1, "package_directory_count": 1, "package_bytes": 17,
        "request_bytes": 31, "attempt_bytes": 29, "source_selection_bytes": 37,
        "predecessor_attempt_bytes": 0, "observer_node_bytes": 23,
    })


def snapshot(ledger):
    result = ledger.snapshot()
    assert type(result) is dict and set(result) == SNAPSHOT_FIELDS
    assert all(type(value) is int and value >= 0 for value in result.values())
    return result


@pytest.mark.parametrize("mode", ["producer", "readonly"])
def test_fixed_limits_and_detached_zero_snapshot_do_not_claim_factory_success(tick, mode):
    ledger = budget.ReplicaBudget(mode)
    assert type(ledger.limits) is MappingProxyType
    assert dict(ledger.limits) == policy.runtime_limits(mode)
    assert ledger.first is None and ledger.remaining_seconds == 1800
    observed = snapshot(ledger)
    assert all(value == 0 for value in observed.values())
    observed["read_bytes"] = 9
    assert snapshot(ledger)["read_bytes"] == 0
    with pytest.raises(TypeError):
        ledger.limits["max_write_bytes"] = 1
    ledger.assert_settled()


@pytest.mark.parametrize("mode", [None, True, 1, "", "preview", "PRODUCER"])
def test_invalid_constructor_mode_is_rejected_before_clock(mode, monkeypatch):
    calls = []
    monkeypatch.setattr(budget.time, "monotonic_ns", lambda: calls.append("clock"))
    reject(budget.ReplicaBudget, mode, suffix="MODE_INVALID")
    assert calls == []


def test_constructor_has_no_quota_clock_or_existing_ledger_override(tick):
    for name in ("limits", "clock", "ledger", "source_adapter", "slot"):
        with pytest.raises(TypeError):
            budget.ReplicaBudget("producer", **{name: object()})
    reject(budget.ReplicaBudget, "producer", check_cancelled=1, suffix="TYPE_INVALID")


@pytest.mark.parametrize("initial", [True, 0.0, None, -1])
def test_clock_start_requires_exact_nonnegative_integer(initial, monkeypatch):
    monkeypatch.setattr(budget.time, "monotonic_ns", lambda: initial)
    reject(budget.ReplicaBudget, "producer", suffix="DEADLINE_LIMIT")


def test_exact_deadline_is_expired_and_cannot_call_cancellation_callback(tick):
    calls = []
    ledger = budget.ReplicaBudget("producer", lambda: calls.append("cancel"))
    tick[0] += DEADLINE_NS - 1
    ledger.check()
    assert calls == ["cancel"]
    tick[0] += 1
    assert ledger.remaining_seconds == 0
    original = reject(ledger.check, suffix="DEADLINE_LIMIT")
    assert calls == ["cancel"] and ledger.first is original
    same_error(original, ledger.check)


@pytest.mark.parametrize("replacement", [True, 1.0, None, -1, 4999999])
def test_invalid_or_backwards_clock_preserves_first_fault(tick, replacement):
    ledger = budget.ReplicaBudget("producer")
    tick[0] = replacement
    original = reject(ledger.check, suffix="DEADLINE_LIMIT")
    assert ledger.first is original
    tick[0] = 5000000
    same_error(original, ledger.check)


def test_callback_return_does_not_bypass_the_post_callback_deadline(tick):
    def late_callback():
        tick[0] += DEADLINE_NS

    ledger = budget.ReplicaBudget("producer", late_callback)
    original = reject(ledger.check, suffix="DEADLINE_LIMIT")
    assert ledger.first is original


def test_callback_exception_identity_and_earliest_error_survive_all_later_checks(tick):
    original = RuntimeError("original cancellation")
    calls = []

    def cancelled():
        calls.append("cancel")
        raise original

    ledger = budget.ReplicaBudget("producer", cancelled)
    same_error(original, ledger.check)
    assert ledger.remember(ValueError("later")) is original
    same_error(original, ledger.begin_read, 1)
    same_error(original, ledger.assert_settled)
    assert calls == ["cancel"] and ledger.first is original


def test_callback_can_remember_cancellation_without_throwing_but_cannot_hide_it(tick):
    original = KeyboardInterrupt("synthetic cancellation")
    holder = {}
    ledger = budget.ReplicaBudget("producer", lambda: holder["ledger"].remember(original))
    holder["ledger"] = ledger
    same_error(original, ledger.check)
    assert ledger.first is original


@pytest.mark.parametrize("method", ["remember", "fail_read", "fail_write", "fail_resources"])
def test_real_exception_subclass_keeps_identity_without_reading_class_property(tick, method):
    calls = []

    class OriginalError(OSError):
        @property
        def __class__(self):
            calls.append("exception.class")
            raise AssertionError("Exception identity must use its actual type")

    original = OriginalError("real exception subclass")
    ledger = budget.ReplicaBudget("producer")
    if method == "remember":
        assert ledger.remember(original) is original
    elif method == "fail_read":
        ticket = ledger.begin_read(5, source=True)
        same_error(original, ledger.fail_read, ticket, original)
        assert snapshot(ledger)["uncertain_source_read_calls"] == 1
    elif method == "fail_write":
        ticket = ledger.begin_write(5)
        same_error(original, ledger.fail_write, ticket, original)
        assert snapshot(ledger)["uncertain_write_bytes"] == 5
    else:
        ticket = ledger.begin_resources(3)
        lease, error = ledger.fail_resources(ticket, original, acquired=2)
        assert error is original and snapshot(ledger)["current_resources"] == 2
        ledger.release_resources(lease)
        assert snapshot(ledger)["current_resources"] == 0
    assert calls == [] and ledger.first is original


@pytest.mark.parametrize("method", ["remember", "fail_read", "fail_write", "fail_resources"])
@pytest.mark.parametrize("has_first", [False, True])
def test_nonexception_class_hook_is_not_dispatched_or_allowed_to_lose_cleanup_lease(tick, method, has_first):
    calls = []

    class NonException:
        @property
        def __class__(self):
            calls.append("nonexception.class")
            raise AssertionError("Untrusted class hook must not run after ticket consumption")

    ledger = budget.ReplicaBudget("producer")
    invalid = NonException()
    if method == "fail_read":
        ticket = ledger.begin_read(5, source=True)
    elif method == "fail_write":
        ticket = ledger.begin_write(5)
    elif method == "fail_resources":
        ticket = ledger.begin_resources(3)
    earlier = RuntimeError("earliest fault") if has_first else None
    if earlier is not None:
        ledger.remember(earlier)
    if method == "remember":
        result = ledger.remember(invalid)
    elif method == "fail_resources":
        settled = ledger.fail_resources(ticket, invalid, acquired=2)
        assert type(settled) is tuple and len(settled) == 2
        lease, result = settled
        assert type(lease) is object
        observed = snapshot(ledger)
        assert observed["current_resources"] == observed["peak_resources"] == 2
        assert observed["pending_resources"] == observed["uncertain_resource_acquires"] == 0
        ledger.release_resources(lease)
        assert snapshot(ledger)["current_resources"] == 0
    else:
        with pytest.raises(RuntimeError if has_first else ERROR) as raised:
            getattr(ledger, method)(ticket, invalid)
        result = raised.value
        observed = snapshot(ledger)
        if method == "fail_read":
            assert observed["read_bytes"] == observed["source_read_bytes"] == 0
            assert observed["uncertain_read_calls"] == observed["uncertain_source_read_calls"] == 1
        else:
            assert observed["confirmed_write_bytes"] == 0 and observed["uncertain_write_bytes"] == 5
    if has_first:
        assert result is earlier
    else:
        assert type(result) is ERROR and result.code == PREFIX + "SOURCE_FAILED"
    assert calls == [] and ledger.first is result


@pytest.mark.parametrize("invalid", [0, -1, 131073, True, 1.0, "1", None])
@pytest.mark.parametrize("method", ["begin_read", "begin_write"])
def test_io_request_is_an_exact_positive_chunk_bounded_integer(tick, method, invalid):
    ledger = budget.ReplicaBudget("producer")
    reject(getattr(ledger, method), invalid, suffix="TYPE_INVALID")
    assert snapshot(ledger)["read_calls"] == snapshot(ledger)["write_calls"] == 0


@pytest.mark.parametrize("method,flag", [("begin_read", "source"), ("begin_write", "failure"),
                                        ("begin_resources", "failure"), ("check", "callback")])
def test_flags_require_exact_bool_before_callback_or_counter_effect(tick, method, flag):
    calls = []
    ledger = budget.ReplicaBudget("producer", lambda: calls.append("cancel"))
    args = () if method == "check" else (1,)
    reject(getattr(ledger, method), *args, **{flag: 1}, suffix="TYPE_INVALID")
    assert calls == []
    assert snapshot(ledger)["read_calls"] == snapshot(ledger)["write_calls"] == 0


def test_short_reads_and_eof_charge_actual_bytes_with_source_inside_total(tick):
    ledger = budget.ReplicaBudget("producer")
    events = [(b"abc", True), (b"de", False), (b"", True)]
    for raw, source in events:
        ticket = ledger.begin_read(8, source=source)
        assert ledger.end_read(ticket, raw) == len(raw)
    ledger.note_seek()
    observed = snapshot(ledger)
    assert observed["read_bytes"] == sum(len(raw) for raw, _ in events) == 5
    assert observed["source_read_bytes"] == sum(len(raw) for raw, source in events if source) == 3
    assert observed["read_calls"] == 3 and observed["source_read_calls"] == 2
    assert observed["seek_calls"] == 1
    assert observed["uncertain_read_calls"] == observed["uncertain_source_read_calls"] == 0
    ledger.assert_settled()


def test_read_actual_return_is_recorded_before_any_callback_can_fail(tick):
    calls, armed = [], [False]
    original = RuntimeError("cancel after native read")

    def callback():
        calls.append("callback")
        if armed[0]:
            raise original

    ledger = budget.ReplicaBudget("producer", callback)
    ticket = ledger.begin_read(9, source=True)
    armed[0] = True
    assert ledger.end_read(ticket, b"read") == 4
    assert calls == ["callback"]
    assert snapshot(ledger)["read_bytes"] == snapshot(ledger)["source_read_bytes"] == 4
    same_error(original, ledger.check)
    assert calls == ["callback", "callback"]


@pytest.mark.parametrize("source", [False, True])
def test_over_returned_read_is_charged_before_limit_error(tick, source):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_read(2, source=source)
    original = reject(ledger.end_read, ticket, b"four", suffix="READ_LIMIT")
    observed = snapshot(ledger)
    assert observed["read_bytes"] == 4
    assert observed["source_read_bytes"] == (4 if source else 0)
    assert observed["uncertain_read_calls"] == 0
    same_error(original, ledger.end_read, ticket, b"four")
    assert snapshot(ledger)["read_bytes"] == 4


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("raw", [None, bytearray(b"abc"), memoryview(b"abc"), "abc"])
def test_unknown_read_return_keeps_uncertainty_without_inventing_eof(tick, source, raw):
    ledger = budget.ReplicaBudget("producer")
    prior = ledger.begin_read(1, source=True)
    ledger.end_read(prior, b"x")
    ticket = ledger.begin_read(5, source=source)
    reject(ledger.end_read, ticket, raw, suffix="SOURCE_FAILED")
    observed = snapshot(ledger)
    assert observed["read_bytes"] == observed["source_read_bytes"] == 1
    assert observed["uncertain_read_calls"] == 1
    assert observed["uncertain_source_read_calls"] == int(source)


def test_hostile_read_bytes_subclass_cannot_run_len_hook(tick):
    calls = []

    class HostileBytes(bytes):
        def __len__(self):
            calls.append("len")
            raise AssertionError("untrusted byte length")

    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_read(9, source=True)
    reject(ledger.end_read, ticket, HostileBytes(b"abc"), suffix="SOURCE_FAILED")
    assert calls == []
    assert snapshot(ledger)["uncertain_source_read_calls"] == 1


def test_failed_read_preserves_original_exception_and_previous_actual_counters(tick):
    ledger = budget.ReplicaBudget("producer")
    ledger.end_read(ledger.begin_read(4, source=True), b"seen")
    original = OSError("native read failed without a byte return")
    ticket = ledger.begin_read(8, source=True)
    same_error(original, ledger.fail_read, ticket, original)
    observed = snapshot(ledger)
    assert observed["read_bytes"] == observed["source_read_bytes"] == 4
    assert observed["uncertain_read_calls"] == observed["uncertain_source_read_calls"] == 1
    assert ledger.first is original


@pytest.mark.parametrize("dimension", ["total", "source"])
def test_read_quota_frontier_counts_pending_request_and_source_subset(tick, monkeypatch, dimension):
    ledger = budget.ReplicaBudget("producer")
    # Synthetic historical counters isolate the cap check; no TiB read occurred.
    if dimension == "total":
        monkeypatch.setattr(ledger, "_read_bytes", READ_CAP - 2)
        source, suffix = False, "READ_LIMIT"
    else:
        monkeypatch.setattr(ledger, "_read_bytes", SOURCE_CAP - 2)
        monkeypatch.setattr(ledger, "_source_read_bytes", SOURCE_CAP - 2)
        source, suffix = True, "SOURCE_READ_LIMIT"
    ticket = ledger.begin_read(2, source=source)
    ledger.end_read(ticket, b"ab")
    reject(ledger.begin_read, 1, source=source, suffix=suffix)
    assert snapshot(ledger)["read_calls"] == 1


def test_short_writes_count_only_actual_return_and_can_continue_ordinary_stream(tick):
    ledger = budget.ReplicaBudget("producer")
    assert ledger.end_write(ledger.begin_write(9), 3) == 3
    assert ledger.end_write(ledger.begin_write(6), 6) == 6
    observed = snapshot(ledger)
    assert observed["confirmed_write_bytes"] == 9 and observed["write_calls"] == 2
    assert observed["uncertain_write_bytes"] == 0
    ledger.assert_settled()


def test_write_actual_return_is_recorded_before_callback_and_validation(tick):
    calls, armed = [], [False]
    original = RuntimeError("cancel after returned write")

    def callback():
        calls.append("callback")
        if armed[0]:
            raise original

    ledger = budget.ReplicaBudget("producer", callback)
    ticket = ledger.begin_write(11)
    armed[0] = True
    assert ledger.end_write(ticket, 7) == 7
    assert calls == ["callback"] and snapshot(ledger)["confirmed_write_bytes"] == 7
    same_error(original, ledger.check)
    assert snapshot(ledger)["confirmed_write_bytes"] == 7


@pytest.mark.parametrize("count", [None, True, 1.0, "1", -1, 10])
def test_invalid_write_return_keeps_entire_requested_amount_uncertain(tick, count):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_write(9)
    original = reject(ledger.end_write, ticket, count, suffix="WRITE_UNCERTAIN")
    observed = snapshot(ledger)
    assert observed["confirmed_write_bytes"] == 0 and observed["uncertain_write_bytes"] == 9
    same_error(original, ledger.end_write, ticket, 2)
    assert snapshot(ledger)["uncertain_write_bytes"] == 9


def test_zero_write_is_confirmed_zero_then_rejected_without_unknown_bytes(tick):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_write(7)
    reject(ledger.end_write, ticket, 0, suffix="WRITE_FAILED")
    observed = snapshot(ledger)
    assert observed["confirmed_write_bytes"] == observed["uncertain_write_bytes"] == 0
    assert observed["write_calls"] == 1


def test_failed_write_keeps_full_requested_charge_and_original_error(tick):
    ledger = budget.ReplicaBudget("producer")
    ledger.end_write(ledger.begin_write(3), 3)
    original = OSError("some native output may exist")
    ticket = ledger.begin_write(11)
    same_error(original, ledger.fail_write, ticket, original)
    observed = snapshot(ledger)
    assert observed["confirmed_write_bytes"] == 3 and observed["uncertain_write_bytes"] == 11
    same_error(original, ledger.begin_write, 1)
    assert policy.slot_reservation(1)["write_bytes"] == WRITE_CAP
    assert policy.slot_reservation(2)["write_bytes"] == WRITE_CAP
    assert snapshot(ledger)["uncertain_write_bytes"] == 11


def test_ordinary_write_quota_keeps_failure_reserve_and_uncertain_history(tick, monkeypatch):
    ledger = budget.ReplicaBudget("producer")
    # Deliberate synthetic state: arithmetic coverage, not recoverable unknown I/O.
    monkeypatch.setattr(ledger, "_confirmed", WRITE_CAP - FAILURE_CAP - 19)
    monkeypatch.setattr(ledger, "_uncertain_writes", 17)
    ticket = ledger.begin_write(2)
    ledger.end_write(ticket, 2)
    reject(ledger.begin_write, 1, suffix="WRITE_LIMIT")
    observed = snapshot(ledger)
    assert observed["confirmed_write_bytes"] + observed["uncertain_write_bytes"] + FAILURE_CAP == WRITE_CAP


@pytest.mark.parametrize("kind", ["read", "write"])
def test_io_ticket_is_single_use_and_wrong_kind_cannot_consume_it(tick, kind):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_read(3) if kind == "read" else ledger.begin_write(3)
    wrong = ledger.end_write if kind == "read" else ledger.end_read
    original = reject(wrong, ticket, 3 if kind == "read" else b"abc", suffix="TICKET_INVALID")
    if kind == "read":
        assert ledger.end_read(ticket, b"abc") == 3
        same_error(original, ledger.end_read, ticket, b"abc")
    else:
        assert ledger.end_write(ticket, 3) == 3
        same_error(original, ledger.end_write, ticket, 3)
    observed = snapshot(ledger)
    assert observed["read_bytes"] + observed["confirmed_write_bytes"] == 3


def test_cross_instance_and_unknown_io_tickets_leave_original_pending_for_cleanup(tick):
    first, second = budget.ReplicaBudget("producer"), budget.ReplicaBudget("producer")
    own = first.begin_read(4)
    foreign = second.begin_read(7)
    original = reject(first.end_read, foreign, b"bad", suffix="TICKET_INVALID")
    same_error(original, first.end_read, object(), b"bad")
    assert first.end_read(own, b"good") == 4
    # The foreign owner still has its untouched ticket; use a legal return.
    assert second.end_read(foreign, b"other") == 5
    assert snapshot(first)["read_bytes"] == 4 and snapshot(second)["read_bytes"] == 5


@pytest.mark.parametrize("next_method", ["begin_read", "begin_write", "note_seek"])
def test_second_io_or_seek_cannot_hide_an_existing_pending_io(tick, next_method):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_read(4)
    args = () if next_method == "note_seek" else (1,)
    original = reject(getattr(ledger, next_method), *args, suffix="REENTRANT")
    assert ledger.end_read(ticket, b"done") == 4
    same_error(original, ledger.assert_settled)


@pytest.mark.parametrize("kind", ["read", "write", "resources"])
def test_late_actual_return_is_settled_even_after_deadline(tick, kind):
    ledger = budget.ReplicaBudget("producer")
    if kind == "read":
        ticket = ledger.begin_read(5, source=True)
    elif kind == "write":
        ticket = ledger.begin_write(5)
    else:
        ticket = ledger.begin_resources(5)
    tick[0] += DEADLINE_NS
    original = reject(ledger.check, suffix="DEADLINE_LIMIT")
    if kind == "read":
        assert ledger.end_read(ticket, b"late") == 4
        assert snapshot(ledger)["read_bytes"] == 4
    elif kind == "write":
        assert ledger.end_write(ticket, 4) == 4
        assert snapshot(ledger)["confirmed_write_bytes"] == 4
    else:
        lease, error = ledger.end_resources(ticket, 4)
        assert error is None and snapshot(ledger)["current_resources"] == 4
        ledger.release_resources(lease)
        assert snapshot(ledger)["current_resources"] == 0
    same_error(original, ledger.assert_settled)


@pytest.mark.parametrize("invalid", [0, -1, 1000001, True, 1.0, None])
def test_resource_reservation_requires_exact_positive_bounded_count(tick, invalid):
    ledger = budget.ReplicaBudget("producer")
    reject(ledger.begin_resources, invalid, suffix="TYPE_INVALID")
    assert snapshot(ledger)["pending_resources"] == snapshot(ledger)["current_resources"] == 0


def test_nested_resource_reservations_settle_actual_counts_then_release_individual_leases(tick):
    ledger = budget.ReplicaBudget("producer")
    first, second = ledger.begin_resources(4), ledger.begin_resources(5)
    assert snapshot(ledger)["pending_resources"] == 9
    lease2, error2 = ledger.end_resources(second, 3)
    assert type(lease2) is object and error2 is None
    assert snapshot(ledger)["pending_resources"] == 4 and snapshot(ledger)["current_resources"] == 3
    lease1, error1 = ledger.end_resources(first, 2)
    assert error1 is None and snapshot(ledger)["peak_resources"] == 5
    ledger.release_resources(lease2)
    assert snapshot(ledger)["current_resources"] == 2
    ledger.release_resources(lease1)
    assert snapshot(ledger)["current_resources"] == 0 and snapshot(ledger)["peak_resources"] == 5
    ledger.assert_settled()


@pytest.mark.parametrize("actual", [3, 1000001])
def test_resource_over_return_still_delivers_cleanup_lease_and_records_actual_count(tick, actual):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_resources(2)
    result = ledger.end_resources(ticket, actual)
    assert type(result) is tuple and len(result) == 2
    lease, original = result
    assert type(lease) is object and type(original) is ERROR
    assert original.code == PREFIX + "RESOURCE_LIMIT" and ledger.first is original
    observed = snapshot(ledger)
    assert observed["current_resources"] == observed["peak_resources"] == actual
    assert observed["pending_resources"] == observed["uncertain_resource_acquires"] == 0
    same_error(original, ledger.check)
    ledger.release_resources(lease)
    assert snapshot(ledger)["current_resources"] == 0
    same_error(original, ledger.assert_settled)


@pytest.mark.parametrize("actual", [None, True, 1.0, "2", -1])
def test_unconfirmed_resource_return_retains_maximum_and_delivers_cleanup_lease(tick, actual):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_resources(7)
    lease, original = ledger.end_resources(ticket, actual)
    assert type(lease) is object and original.code == PREFIX + "RESOURCE_UNCERTAIN"
    observed = snapshot(ledger)
    assert observed["current_resources"] == observed["peak_resources"] == 7
    assert observed["uncertain_resource_acquires"] == 1 and observed["pending_resources"] == 0
    ledger.release_resources(lease)
    assert snapshot(ledger)["current_resources"] == 0 and ledger.first is original


@pytest.mark.parametrize("acquired,retained,unknown", [(0, 0, 0), (2, 2, 0), (9, 9, 0),
                                                     (None, 7, 1), (-1, 7, 1), (True, 7, 1)])
def test_failed_partial_resource_acquire_preserves_original_error_and_cleanup_count(tick, acquired, retained, unknown):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_resources(7)
    original = OSError("native acquire partially failed")
    result = ledger.fail_resources(ticket, original, acquired=acquired)
    assert type(result) is tuple and len(result) == 2
    lease, error = result
    assert error is original and ledger.first is original
    assert snapshot(ledger)["current_resources"] == retained
    assert snapshot(ledger)["uncertain_resource_acquires"] == unknown
    ledger.release_resources(lease)
    assert snapshot(ledger)["current_resources"] == 0
    same_error(original, ledger.assert_settled)


@pytest.mark.parametrize("actual", [2, 4, None])
def test_resource_settlement_never_loses_cleanup_lease_or_replaces_existing_first(tick, actual):
    ledger = budget.ReplicaBudget("producer")
    ticket = ledger.begin_resources(3)
    original = RuntimeError("earlier operation failed")
    ledger.remember(original)
    lease, error = ledger.end_resources(ticket, actual)
    assert type(lease) is object
    assert error is (None if actual == 2 else original)
    assert ledger.first is original
    ledger.release_resources(lease)
    assert snapshot(ledger)["current_resources"] == 0


def test_resource_cap_counts_all_current_and_pending_without_anticipated_close_refund(tick):
    ledger = budget.ReplicaBudget("producer")
    held, _ = ledger.end_resources(ledger.begin_resources(999994), 999994)
    pending = ledger.begin_resources(3)
    original = reject(ledger.begin_resources, 4, suffix="RESOURCE_LIMIT")
    assert snapshot(ledger)["current_resources"] == 999994
    assert snapshot(ledger)["pending_resources"] == 3
    later, error = ledger.end_resources(pending, 2)
    assert error is None and ledger.first is original
    ledger.release_resources(later)
    ledger.release_resources(held)
    assert snapshot(ledger)["current_resources"] == snapshot(ledger)["pending_resources"] == 0


def test_wrong_resource_ticket_and_cross_instance_lease_cannot_steal_count(tick):
    first, second = budget.ReplicaBudget("producer"), budget.ReplicaBudget("producer")
    ticket1, ticket2 = first.begin_resources(2), second.begin_resources(3)
    original = reject(first.end_resources, ticket2, 1, suffix="TICKET_INVALID")
    assert snapshot(first)["pending_resources"] == 2 and snapshot(second)["pending_resources"] == 3
    lease1, _ = first.end_resources(ticket1, 2)
    lease2, _ = second.end_resources(ticket2, 3)
    same_error(original, first.release_resources, lease2)
    assert snapshot(first)["current_resources"] == 2 and snapshot(second)["current_resources"] == 3
    first.release_resources(lease1)
    same_error(original, first.release_resources, lease1)
    second.release_resources(lease2)
    assert snapshot(first)["current_resources"] == snapshot(second)["current_resources"] == 0


def test_io_and_resource_ticket_kinds_cannot_settle_each_others_reservations(tick):
    ledger = budget.ReplicaBudget("producer")
    io_ticket = ledger.begin_read(3)
    resource_ticket = ledger.begin_resources(2)
    original = reject(ledger.end_resources, io_ticket, 1, suffix="TICKET_INVALID")
    same_error(original, ledger.end_read, resource_ticket, b"bad")
    assert snapshot(ledger)["pending_resources"] == 2
    assert snapshot(ledger)["read_bytes"] == snapshot(ledger)["current_resources"] == 0
    ledger.end_read(io_ticket, b"yes")
    lease, error = ledger.end_resources(resource_ticket, 2)
    assert error is None
    ledger.release_resources(lease)
    assert snapshot(ledger)["read_bytes"] == 3 and snapshot(ledger)["current_resources"] == 0


@pytest.mark.parametrize("target", ["mode", "read_size", "write_size", "resource_maximum",
                                  "write_return", "resource_return", "ticket", "lease"])
def test_hostile_scalar_and_ticket_hooks_do_not_execute(tick, target):
    calls = []

    def forbidden(name):
        calls.append(name)
        raise AssertionError("untrusted caller hook: " + name)

    class Integer(int):
        def __lt__(self, other):
            forbidden("int.lt")
        def __le__(self, other):
            forbidden("int.le")
        def __gt__(self, other):
            forbidden("int.gt")
        def __ge__(self, other):
            forbidden("int.ge")
        def __int__(self):
            forbidden("int.int")

    class Text(str):
        def __eq__(self, other):
            forbidden("str.eq")
        def __hash__(self):
            forbidden("str.hash")

    class UnknownTicket:
        def __hash__(self):
            forbidden("ticket.hash")
        def __eq__(self, other):
            forbidden("ticket.eq")

    ledger = budget.ReplicaBudget("producer")
    if target == "mode":
        reject(budget.ReplicaBudget, Text("producer"), suffix="MODE_INVALID")
    elif target == "read_size":
        reject(ledger.begin_read, Integer(1), suffix="TYPE_INVALID")
    elif target == "write_size":
        reject(ledger.begin_write, Integer(1), suffix="TYPE_INVALID")
    elif target == "resource_maximum":
        reject(ledger.begin_resources, Integer(1), suffix="TYPE_INVALID")
    elif target == "write_return":
        reject(ledger.end_write, ledger.begin_write(2), Integer(1), suffix="WRITE_UNCERTAIN")
        assert snapshot(ledger)["uncertain_write_bytes"] == 2
    elif target == "resource_return":
        lease, error = ledger.end_resources(ledger.begin_resources(2), Integer(1))
        assert error.code == PREFIX + "RESOURCE_UNCERTAIN"
        assert snapshot(ledger)["current_resources"] == 2
        ledger.release_resources(lease)
    elif target == "ticket":
        ticket = ledger.begin_resources(2)
        reject(ledger.end_resources, UnknownTicket(), 1, suffix="TICKET_INVALID")
        lease, error = ledger.end_resources(ticket, 2)
        assert error is None
        ledger.release_resources(lease)
    else:
        lease, error = ledger.end_resources(ledger.begin_resources(2), 2)
        reject(ledger.release_resources, UnknownTicket(), suffix="TICKET_INVALID")
        assert snapshot(ledger)["current_resources"] == 2
        ledger.release_resources(lease)
    assert calls == []


@pytest.mark.parametrize("state", ["read", "pending_resource", "held_resource"])
def test_assert_settled_detects_each_unfinished_accounting_state(tick, state):
    ledger = budget.ReplicaBudget("producer")
    if state == "read":
        ticket = ledger.begin_read(1)
        original = reject(ledger.assert_settled, suffix="REENTRANT")
        ledger.end_read(ticket, b"")
    else:
        ticket = ledger.begin_resources(1)
        lease = None
        if state == "held_resource":
            lease, _ = ledger.end_resources(ticket, 1)
        original = reject(ledger.assert_settled, suffix="UNSETTLED")
        if lease is None:
            lease, _ = ledger.end_resources(ticket, 0)
        ledger.release_resources(lease)
    same_error(original, ledger.assert_settled)


@pytest.mark.parametrize("action", ["end_read", "fail_read", "end_write", "fail_write",
                                  "end_resources", "fail_resources", "release_resources"])
def test_callback_reentry_cannot_consume_outer_ticket_or_release_outer_lease(tick, action):
    armed, holder, caught = [False], {}, []

    def callback():
        if armed[0]:
            try:
                holder["invoke"]()
            except ERROR as exc:
                caught.append(exc)

    ledger = budget.ReplicaBudget("producer", callback)
    injected = RuntimeError("callback must not settle this native result")
    if action.endswith("read"):
        ticket = ledger.begin_read(5, source=True)
        holder["invoke"] = lambda: getattr(ledger, action)(ticket, b"stolen" if action == "end_read" else injected)
    elif action.endswith("write"):
        ticket = ledger.begin_write(5)
        holder["invoke"] = lambda: getattr(ledger, action)(ticket, 2 if action == "end_write" else injected)
    elif action == "release_resources":
        lease, _ = ledger.end_resources(ledger.begin_resources(5), 5)
        holder["invoke"] = lambda: ledger.release_resources(lease)
    else:
        ticket = ledger.begin_resources(5)
        if action == "end_resources":
            holder["invoke"] = lambda: ledger.end_resources(ticket, 2)
        else:
            holder["invoke"] = lambda: ledger.fail_resources(ticket, injected, acquired=2)
    before = snapshot(ledger)
    armed[0] = True
    original = reject(ledger.check, suffix="REENTRANT")
    assert caught == [original] and ledger.first is original
    after = snapshot(ledger)
    assert after == before
    armed[0] = False
    if action.endswith("read"):
        assert ledger.end_read(ticket, b"outer") == 5
    elif action.endswith("write"):
        assert ledger.end_write(ticket, 5) == 5
    elif action == "release_resources":
        ledger.release_resources(lease)
    else:
        cleanup, error = ledger.end_resources(ticket, 5)
        assert error is None
        ledger.release_resources(cleanup)
    assert snapshot(ledger)["current_resources"] == snapshot(ledger)["pending_resources"] == 0
    same_error(original, ledger.assert_settled)


def test_callback_recursion_is_rejected_once_and_cannot_be_swallowed(tick):
    holder, calls = {}, []

    def callback():
        calls.append("outer")
        reject(holder["ledger"].check, suffix="REENTRANT")

    ledger = budget.ReplicaBudget("producer", callback)
    holder["ledger"] = ledger
    original = reject(ledger.check, suffix="REENTRANT")
    assert calls == ["outer"] and ledger.first is original


def test_callback_cannot_enter_terminal_window_and_suppress_future_checks(tick):
    holder, entered, caught = {}, [], []

    def callback():
        try:
            with holder["ledger"].terminal_window():
                entered.append("forbidden body")
        except ERROR as exc:
            caught.append(exc)

    ledger = budget.ReplicaBudget("producer", callback)
    holder["ledger"] = ledger
    original = reject(ledger.check, suffix="REENTRANT")
    assert entered == [] and caught == [original]
    assert ledger._terminal == 0 and ledger.first is original


def test_callback_cannot_start_diagnostic_after_remembering_an_error(tick):
    original = RuntimeError("callback cancellation")
    holder = {}

    def callback():
        holder["ledger"].remember(original)
        same_error(original, holder["ledger"].begin_failure)

    ledger = budget.ReplicaBudget("producer", callback)
    holder["ledger"] = ledger
    same_error(original, ledger.check)
    assert ledger._failure_started is False
    assert snapshot(ledger)["write_calls"] == snapshot(ledger)["pending_resources"] == 0


@pytest.mark.parametrize("kind", ["read", "source", "write"])
def test_remaining_demand_counts_pending_io_at_exact_quota_boundary(tick, monkeypatch, kind):
    ledger = budget.ReplicaBudget("producer")
    demand, requested = payload_demand(), 13
    # Controlled historical counter faults test arithmetic without huge IO.
    if kind == "read":
        monkeypatch.setattr(ledger, "_read_bytes", READ_CAP - demand["read_bytes"] - requested)
        ticket = ledger.begin_read(requested)
        field, suffix = "_read_bytes", "READ_LIMIT"
    elif kind == "source":
        spent = SOURCE_CAP - demand["source_read_bytes"] - requested
        monkeypatch.setattr(ledger, "_source_read_bytes", spent)
        monkeypatch.setattr(ledger, "_read_bytes", spent)
        ticket = ledger.begin_read(requested, source=True)
        field, suffix = "_source_read_bytes", "SOURCE_READ_LIMIT"
    else:
        monkeypatch.setattr(ledger, "_confirmed", WRITE_CAP - demand["write_bytes"] - requested - 17)
        monkeypatch.setattr(ledger, "_uncertain_writes", 17)
        ticket = ledger.begin_write(requested)
        field, suffix = "_confirmed", "WRITE_LIMIT"
    before = snapshot(ledger)
    ledger.require_remaining(demand)
    assert snapshot(ledger) == before
    monkeypatch.setattr(ledger, field, getattr(ledger, field) + 1)
    original = reject(ledger.require_remaining, demand, suffix=suffix)
    if kind == "write":
        ledger.end_write(ticket, 1)
    else:
        ledger.end_read(ticket, b"")
    same_error(original, ledger.check)


def test_remaining_write_demand_includes_failure_once_not_twice(tick, monkeypatch):
    ledger = budget.ReplicaBudget("producer")
    demand = policy.bootstrap_demand(1)
    monkeypatch.setattr(ledger, "_confirmed", WRITE_CAP - demand["write_bytes"])
    before = snapshot(ledger)
    ledger.require_remaining(demand)
    assert snapshot(ledger) == before
    assert ledger._failure_remaining == FAILURE_CAP
    assert ledger.first is None


def test_remaining_resources_add_current_pending_and_future_only_once(tick):
    ledger = budget.ReplicaBudget("producer")
    demand, pending = policy.bootstrap_demand(1), 3
    current = RESOURCE_CAP - demand["peak_additional_resources"] - pending
    held, _ = ledger.end_resources(ledger.begin_resources(current), current)
    ticket = ledger.begin_resources(pending)
    before = snapshot(ledger)
    ledger.require_remaining(demand)
    assert snapshot(ledger) == before
    extra = ledger.begin_resources(1)
    original = reject(ledger.require_remaining, demand, suffix="RESOURCE_LIMIT")
    for reservation in (ticket, extra):
        lease, error = ledger.end_resources(reservation, 0)
        assert error is None
        ledger.release_resources(lease)
    ledger.release_resources(held)
    assert snapshot(ledger)["current_resources"] == 0
    same_error(original, ledger.check)


def test_tampered_demand_rejection_does_not_change_counters_or_issue_tickets(tick):
    ledger = budget.ReplicaBudget("producer")
    demand = payload_demand()
    demand["write_bytes"] -= 1
    before = snapshot(ledger)
    original = reject(ledger.require_remaining, demand, suffix="DEMAND_INVALID")
    assert snapshot(ledger) == before and ledger.first is original


def test_first_failure_cannot_be_cleared_by_a_valid_demand_or_new_policy_slot(tick):
    ledger = budget.ReplicaBudget("producer")
    original = OSError("unknown write remains in this attempt")
    same_error(original, ledger.fail_write, ledger.begin_write(9), original)
    same_error(original, ledger.require_remaining, policy.bootstrap_demand(2))
    assert policy.allocation((1, 2))["reserved"]["attempts"] == 2
    assert snapshot(ledger)["uncertain_write_bytes"] == 9 and ledger.first is original


def test_one_diagnostic_write_preserves_first_and_counts_inside_original_quota(tick):
    calls = []
    ledger = budget.ReplicaBudget("producer", lambda: calls.append("cancel"))
    original = OSError("ordinary write became unknown")
    same_error(original, ledger.fail_write, ledger.begin_write(13), original)
    ledger.begin_failure()
    resources = ledger.begin_resources(2, failure=True)
    lease, error = ledger.end_resources(resources, 1)
    assert error is None
    ticket = ledger.begin_write(7, failure=True)
    assert ledger.end_write(ticket, 7) == 7
    ledger.release_resources(lease)
    observed = snapshot(ledger)
    assert observed["uncertain_write_bytes"] == 13 and observed["confirmed_write_bytes"] == 7
    assert observed["write_calls"] == 2 and calls == ["cancel"]
    same_error(original, ledger.begin_failure)
    same_error(original, ledger.begin_write, 1, failure=True)
    same_error(original, ledger.begin_read, 1)
    same_error(original, ledger.mark_receipt_started)
    assert ledger.first is original


@pytest.mark.parametrize("result", ["short", "unknown", "invalid"])
def test_diagnostic_never_gets_a_second_write_ticket_after_short_or_unknown_return(tick, result):
    ledger = budget.ReplicaBudget("producer")
    original = RuntimeError("earlier failure")
    ledger.remember(original)
    ledger.begin_failure()
    ticket = ledger.begin_write(11, failure=True)
    if result == "short":
        assert ledger.end_write(ticket, 3) == 3
    elif result == "unknown":
        same_error(original, ledger.fail_write, ticket, OSError("diagnostic write unknown"))
    else:
        same_error(original, ledger.end_write, ticket, True)
    before = snapshot(ledger)
    same_error(original, ledger.begin_write, 1, failure=True)
    assert snapshot(ledger) == before and before["write_calls"] == 1
    assert before["confirmed_write_bytes"] == (3 if result == "short" else 0)
    assert before["uncertain_write_bytes"] == (0 if result == "short" else 11)


@pytest.mark.parametrize("gate", ["no_first", "readonly", "receipt", "expired", "pending_io"])
def test_diagnostic_window_requires_original_failure_and_every_fixed_gate(tick, gate):
    ledger = budget.ReplicaBudget("readonly" if gate == "readonly" else "producer")
    ticket = None
    if gate == "receipt":
        ledger.mark_receipt_started()
    elif gate == "pending_io":
        ticket = ledger.begin_read(1)
    original = None if gate == "no_first" else RuntimeError("earlier failure")
    if original is not None:
        ledger.remember(original)
    if gate == "expired":
        tick[0] += DEADLINE_NS
    if original is None:
        reject(ledger.begin_failure, suffix="DIAGNOSTIC_UNAVAILABLE")
    else:
        same_error(original, ledger.begin_failure)
    assert ledger._failure_started is False
    assert snapshot(ledger)["write_calls"] == 0
    if ticket is not None:
        ledger.end_read(ticket, b"")


def test_diagnostic_requested_bytes_cannot_exceed_single_failure_cap(tick):
    ledger = budget.ReplicaBudget("producer")
    original = RuntimeError("original")
    ledger.remember(original)
    ledger.begin_failure()
    same_error(original, ledger.begin_write, FAILURE_CAP + 1, failure=True)
    assert snapshot(ledger)["write_calls"] == 0
    assert ledger._failure_write_started is False


@pytest.mark.parametrize("source", [False, True])
def test_readonly_records_preview_or_verify_reads_but_has_no_write_or_failure_channel(tick, source):
    ledger = budget.ReplicaBudget("readonly")
    ledger.end_read(ledger.begin_read(5, source=source), b"view")
    assert snapshot(ledger)["read_bytes"] == 4
    assert snapshot(ledger)["source_read_bytes"] == (4 if source else 0)
    original = reject(ledger.begin_write, 1, suffix="WRITE_LIMIT")
    same_error(original, ledger.begin_failure)
    same_error(original, ledger.begin_write, 1, failure=True)
    assert snapshot(ledger)["write_calls"] == snapshot(ledger)["confirmed_write_bytes"] == 0
    assert ledger.limits["max_read_bytes"] == SOURCE_CAP


def test_terminal_window_suppresses_only_callbacks_and_restores_nested_scope(tick):
    calls = []
    ledger = budget.ReplicaBudget("producer", lambda: calls.append("cancel"))
    with ledger.terminal_window():
        ledger.check()
        with ledger.terminal_window():
            ledger.end_read(ledger.begin_read(1), b"x")
        ledger.check()
    assert calls == []
    ledger.check()
    assert calls == ["cancel"]
    with ledger.terminal_window():
        tick[0] += DEADLINE_NS
        original = reject(ledger.check, suffix="DEADLINE_LIMIT")
    assert calls == ["cancel"] and ledger.first is original


def test_snapshot_never_calls_callback_or_claims_actual_native_close(tick):
    calls = []
    ledger = budget.ReplicaBudget("producer", lambda: calls.append("cancel"))
    ticket = ledger.begin_resources(2)
    lease, _ = ledger.end_resources(ticket, 2)
    calls.clear()
    observed = snapshot(ledger)
    assert observed["current_resources"] == 2 and calls == []
    assert set(observed).isdisjoint({"closed", "factory_returned", "write_authority", "slot", "receipt"})
    ledger.release_resources(lease)


def test_real_small_native_file_trace_records_actual_io_before_checks_and_release_after_close(tmp_path, tick):
    """Real temp-file trace; no SQL, production path, replica or owner is used."""
    ledger = budget.ReplicaBudget("producer")
    raw = b"RP1 independent native accounting trace: bytes, EOF, seek and close."
    path = tmp_path / "replica-ledger.raw"
    reservation = ledger.begin_resources(1)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    lease, error = ledger.end_resources(reservation, 1)
    assert error is None
    writes, reads, chunks = [], [], []
    try:
        offset = 0
        while offset < len(raw):
            piece = raw[offset:offset + 7]
            ticket = ledger.begin_write(len(piece))
            count = os.write(descriptor, piece)
            ledger.end_write(ticket, count)
            writes.append(count)
            ledger.check()
            assert count > 0
            offset += count
        os.fsync(descriptor)
        ledger.note_seek()
        assert os.lseek(descriptor, 0, os.SEEK_SET) == 0
        while True:
            ticket = ledger.begin_read(11, source=True)
            actual = os.read(descriptor, 11)
            ledger.end_read(ticket, actual)
            reads.append(len(actual))
            ledger.check()
            if not actual:
                break
            chunks.append(actual)
        assert snapshot(ledger)["current_resources"] == 1
    finally:
        os.close(descriptor)
        ledger.release_resources(lease)
    ledger.assert_settled()
    observed = snapshot(ledger)
    assert b"".join(chunks) == raw == path.read_bytes()
    assert observed["confirmed_write_bytes"] == sum(writes) == len(raw)
    assert observed["read_bytes"] == observed["source_read_bytes"] == sum(reads) == len(raw)
    assert observed["write_calls"] == len(writes)
    assert observed["read_calls"] == observed["source_read_calls"] == len(reads)
    assert reads[-1] == 0 and observed["seek_calls"] == 1
    assert observed["peak_resources"] == 1 and observed["current_resources"] == 0
    assert observed["pending_resources"] == observed["uncertain_resource_acquires"] == 0
    assert observed["uncertain_read_calls"] == observed["uncertain_source_read_calls"] == 0
    assert observed["uncertain_write_bytes"] == 0
