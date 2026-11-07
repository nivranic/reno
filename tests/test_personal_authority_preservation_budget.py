"""Independent byte traces and fault injections for the internal operation ledger."""
import os

import pytest

from reno import personal_authority_preservation_budget as budget
from reno import personal_authority_raw_preservation_contract as contract


def limits(**changes):
    value = contract.default_limits()
    value.update(max_read_bytes=128, max_write_bytes=96, max_failure_bytes=16)
    value.update(changes)
    return value


def error(suffix):
    return pytest.raises(budget.AuthorityRawPreservationBudgetError,
                         match="AUTHORITY_RAW_PRESERVATION_" + suffix + "$" )


def assert_same_first(ledger, original):
    assert ledger.first is original
    with pytest.raises(type(original)) as raised:
        ledger.check()
    assert raised.value is original


def test_real_partial_writes_and_reads_match_independent_trace(tmp_path):
    ledger = budget.OperationBudget(limits())
    raw = b"actual original bytes, short writes included"
    trace = []
    fd = os.open(tmp_path / "payload", os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    try:
        offset = 0
        while offset < len(raw):
            requested = len(raw) - offset
            ticket = ledger.begin_write(requested)
            # Fault boundary: force legitimate short writes through real os.write.
            count = os.write(fd, raw[offset:offset + 7])
            trace.append(("write", count))
            assert ledger.end_write(ticket, count) == count
            ledger.check()
            offset += count
        os.fsync(fd)
        ledger.note_seek()
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        while True:
            ticket = ledger.begin_read(13)
            part = os.read(fd, 13)
            trace.append(("read", len(part)))
            assert ledger.end_read(ticket, part) == len(part)
            ledger.check()
            if not part:
                break
            chunks.append(part)
    finally:
        os.close(fd)
    assert b"".join(chunks) == raw == (tmp_path / "payload").read_bytes()
    observed = ledger.snapshot()
    assert observed["read_bytes"] == sum(n for kind, n in trace if kind == "read") == len(raw)
    assert observed["confirmed_write_bytes"] == sum(n for kind, n in trace if kind == "write") == len(raw)
    assert observed["read_calls"] == sum(kind == "read" for kind, _ in trace)
    assert observed["write_calls"] == sum(kind == "write" for kind, _ in trace)
    assert observed["seek_calls"] == 1
    assert observed["uncertain_read_calls"] == observed["uncertain_write_bytes"] == 0


@pytest.mark.parametrize("kind", ["read", "write"])
def test_actual_return_is_accounted_before_post_cancel(kind):
    cancelled = RuntimeError("explicit cancellation object")
    enabled = False
    def callback():
        if enabled:
            raise cancelled
    ledger = budget.OperationBudget(limits(), callback)
    ticket = ledger.begin_read(9) if kind == "read" else ledger.begin_write(9)
    enabled = True
    count = ledger.end_read(ticket, b"abc") if kind == "read" else ledger.end_write(ticket, 3)
    assert count == 3  # End calls neither callback nor a postcheck.
    key = "read_bytes" if kind == "read" else "confirmed_write_bytes"
    assert ledger.snapshot()[key] == 3
    assert_same_first_after_check(ledger, cancelled)


def assert_same_first_after_check(ledger, original):
    with pytest.raises(type(original)) as raised:
        ledger.check()
    assert raised.value is original
    assert_same_first(ledger, original)


def test_write_cap_includes_failure_reserve():
    ledger = budget.OperationBudget(limits(max_write_bytes=20, max_failure_bytes=8))
    ticket = ledger.begin_write(12)
    ledger.end_write(ticket, 12)
    with error("WRITE_LIMIT"):
        ledger.begin_write(1)
    assert ledger.snapshot()["write_calls"] == 1
    original = ledger.first
    ledger.begin_failure()
    ticket = ledger.begin_write(8, failure=True)
    ledger.end_write(ticket, 8)
    assert ledger.snapshot()["confirmed_write_bytes"] == 20
    assert_same_first(ledger, original)


def test_unused_partial_write_reservation_can_be_used_by_later_write():
    ledger = budget.OperationBudget(limits(max_write_bytes=20, max_failure_bytes=8))
    ledger.end_write(ledger.begin_write(12), 3)
    ledger.end_write(ledger.begin_write(9), 9)
    assert ledger.snapshot()["confirmed_write_bytes"] == 12
    with error("WRITE_LIMIT"):
        ledger.begin_write(1)


@pytest.mark.parametrize("invalid", [None, True, False, -1, 9, 3.0, "3"])
def test_invalid_write_result_retains_whole_unknown_request(invalid):
    ledger = budget.OperationBudget(limits(max_write_bytes=20, max_failure_bytes=8))
    ticket = ledger.begin_write(8)
    with error("WRITE_UNCERTAIN"):
        ledger.end_write(ticket, invalid)
    original = ledger.first
    assert ledger.snapshot()["confirmed_write_bytes"] == 0
    assert ledger.snapshot()["uncertain_write_bytes"] == 8
    ledger.begin_failure()
    ledger.end_write(ledger.begin_write(8, failure=True), 5)
    state = ledger.snapshot()
    assert state["confirmed_write_bytes"] + state["uncertain_write_bytes"] == 13
    with pytest.raises(type(original)) as raised:
        ledger.begin_write(1, failure=True)
    assert raised.value is original


def test_write_exception_keeps_original_and_unknown_reservation():
    ledger = budget.OperationBudget(limits())
    original = OSError("injected write may have changed disk")
    ticket = ledger.begin_write(9)
    with pytest.raises(OSError) as raised:
        ledger.fail_write(ticket, original)
    assert raised.value is original
    assert ledger.snapshot()["uncertain_write_bytes"] == 9
    assert ledger.snapshot()["confirmed_write_bytes"] == 0
    assert_same_first(ledger, original)


def test_zero_write_is_known_no_progress():
    ledger = budget.OperationBudget(limits())
    with error("PUBLICATION_FAILED"):
        ledger.end_write(ledger.begin_write(9), 0)
    assert ledger.snapshot()["confirmed_write_bytes"] == 0
    assert ledger.snapshot()["uncertain_write_bytes"] == 0
    assert ledger.snapshot()["write_calls"] == 1


def test_read_limit_uses_actual_return_but_preflights_whole_request():
    ledger = budget.OperationBudget(limits(max_read_bytes=10))
    ledger.end_read(ledger.begin_read(9), b"abc")
    ledger.end_read(ledger.begin_read(7), b"1234567")
    with error("READ_LIMIT"):
        ledger.begin_read(1)
    assert ledger.snapshot()["read_bytes"] == 10
    assert ledger.snapshot()["read_calls"] == 2


@pytest.mark.parametrize("raw", [None, bytearray(b"a"), memoryview(b"a"), 2])
def test_nonbytes_reader_return_is_uncertain(raw):
    ledger = budget.OperationBudget(limits())
    with error("SOURCE_FAILED"):
        ledger.end_read(ledger.begin_read(3), raw)
    assert ledger.snapshot()["uncertain_read_calls"] == 1
    assert ledger.snapshot()["read_bytes"] == 0


def test_oversized_reader_return_keeps_actual_count_before_rejection():
    ledger = budget.OperationBudget(limits())
    with error("READ_LIMIT"):
        ledger.end_read(ledger.begin_read(2), b"four")
    assert ledger.snapshot()["read_bytes"] == 4


def test_read_exception_retains_first_and_records_uncertain_call():
    ledger = budget.OperationBudget(limits())
    original = OSError("injected read failure")
    with pytest.raises(OSError) as raised:
        ledger.fail_read(ledger.begin_read(8), original)
    assert raised.value is original
    assert ledger.snapshot()["read_calls"] == ledger.snapshot()["uncertain_read_calls"] == 1
    assert_same_first(ledger, original)


@pytest.mark.parametrize("other", ["read", "write", "seek"])
def test_active_io_cannot_be_reentered(other):
    ledger = budget.OperationBudget(limits())
    ticket = ledger.begin_read(3)
    with error("REENTRANT"):
        {"read": lambda: ledger.begin_read(1), "write": lambda: ledger.begin_write(1),
         "seek": ledger.note_seek}[other]()
    original = ledger.first
    # Original I/O completion still counts its actual return after a fault.
    ledger.end_read(ticket, b"abc")
    assert ledger.snapshot()["read_bytes"] == 3
    assert_same_first(ledger, original)


def test_callback_cannot_swallow_reentry_failure():
    ledger = None
    def callback():
        with error("REENTRANT"):
            ledger.check()
    ledger = budget.OperationBudget(limits(), callback)
    with error("REENTRANT"):
        ledger.begin_read(1)
    assert ledger.snapshot()["read_calls"] == 0


@pytest.mark.parametrize("kind", ["read", "write"])
def test_ticket_is_bound_to_original_ledger_and_consumed_once(kind):
    left, right = budget.OperationBudget(limits()), budget.OperationBudget(limits())
    begin = lambda current: current.begin_read(3) if kind == "read" else current.begin_write(3)
    end = lambda current, ticket: current.end_read(ticket, b"abc") if kind == "read" else current.end_write(ticket, 3)
    ticket, wrong = begin(left), begin(right)
    with error("REENTRANT"):
        end(right, ticket)
    end(left, ticket)
    end(right, wrong)  # Its actual return still has to be accounted.
    with error("REENTRANT"):
        end(left, ticket)
    key = "read_bytes" if kind == "read" else "confirmed_write_bytes"
    assert left.snapshot()[key] == right.snapshot()[key] == 3


def test_deadline_is_single_origin_and_applies_in_terminal_window(monkeypatch):
    now = 100
    monkeypatch.setattr(budget.time, "monotonic_ns", lambda: now)
    callbacks = []
    ledger = budget.OperationBudget(limits(deadline_ms=1), lambda: callbacks.append(1))
    now += 500_000
    ledger.check()
    with ledger.terminal_window():
        with ledger.terminal_window():
            ledger.check()
            assert callbacks == [1]
        now += 500_001
        with error("DEADLINE_LIMIT"):
            ledger.check()
    assert ledger.snapshot()["elapsed_ns"] == 1_000_001
    assert ledger.remaining_seconds == 0


def test_failure_uses_original_deadline_without_replacing_first(monkeypatch):
    now = 0
    monkeypatch.setattr(budget.time, "monotonic_ns", lambda: now)
    ledger = budget.OperationBudget(limits(deadline_ms=1))
    original = ledger.remember(RuntimeError("original"))
    now = 1_000_001
    with pytest.raises(RuntimeError) as raised:
        ledger.begin_failure()
    assert raised.value is original
    assert ledger.snapshot()["write_calls"] == 0


def test_failure_channel_no_callbacks_one_partial_write_and_original_first():
    callbacks = []
    ledger = budget.OperationBudget(limits(), lambda: callbacks.append(1))
    original = ledger.remember(RuntimeError("original"))
    ledger.begin_failure()
    ledger.end_write(ledger.begin_write(8, failure=True), 2)
    assert callbacks == []
    assert ledger.snapshot()["confirmed_write_bytes"] == 2
    for action in (ledger.begin_failure, lambda: ledger.begin_write(1, failure=True)):
        with pytest.raises(RuntimeError) as raised:
            action()
        assert raised.value is original


def test_failure_write_exception_is_secondary_and_keeps_its_unknown_amount():
    ledger = budget.OperationBudget(limits())
    original = ledger.remember(RuntimeError("original"))
    ledger.begin_failure()
    ticket = ledger.begin_write(8, failure=True)
    with pytest.raises(RuntimeError) as raised:
        ledger.fail_write(ticket, OSError("secondary injected failure"))
    assert raised.value is original
    assert ledger.snapshot()["uncertain_write_bytes"] == 8


def test_receipt_start_disables_failure_publication():
    ledger = budget.OperationBudget(limits())
    ledger.mark_receipt_started()
    original = ledger.remember(OSError("publisher close failed"))
    with pytest.raises(OSError) as raised:
        ledger.begin_failure()
    assert raised.value is original
    assert ledger.snapshot()["write_calls"] == 0


def test_readonly_observation_has_no_write_or_failure_channel():
    ledger = budget.OperationBudget(limits(), readonly=True)
    ledger.end_read(ledger.begin_read(2), b"ok")
    with error("WRITE_LIMIT"):
        ledger.begin_write(1)
    first = ledger.first
    with pytest.raises(type(first)) as raised:
        ledger.begin_failure()
    assert raised.value is first
    assert ledger.snapshot()["write_calls"] == 0


@pytest.mark.parametrize("kind", ["read", "write"])
def test_snapshot_is_not_settled_proof_and_pending_ticket_is_rejected(kind):
    ledger = budget.OperationBudget(limits())
    ticket = ledger.begin_read(3) if kind == "read" else ledger.begin_write(3)
    assert ledger.snapshot()["uncertain_write_bytes"] == 0
    with error("REENTRANT"):
        ledger.assert_settled()
    first = ledger.first
    if kind == "read":
        ledger.end_read(ticket, b"abc")
    else:
        ledger.end_write(ticket, 3)
    assert_same_first(ledger, first)


def test_settled_gate_never_calls_user_but_keeps_first():
    calls = []
    ledger = budget.OperationBudget(limits(), lambda: calls.append(1))
    ledger.end_read(ledger.begin_read(3), b"abc")
    ledger.assert_settled()
    assert calls == [1]
    original = ledger.remember(OSError("later close failure"))
    with pytest.raises(OSError) as raised:
        ledger.assert_settled()
    assert raised.value is original


def test_input_limits_and_nested_capsule_are_detached_and_immutable():
    selected = limits()
    ledger = budget.OperationBudget(selected)
    original = ledger.limits["capsule_limits"]["max_files"]
    selected["max_read_bytes"] = 999
    selected["capsule_limits"]["max_files"] = 0
    assert ledger.limits["max_read_bytes"] == 128
    assert ledger.limits["capsule_limits"]["max_files"] == original
    with pytest.raises(TypeError):
        ledger.limits["capsule_limits"]["max_files"] = 1


def test_string_subclass_key_is_rejected_before_lookup_or_copy():
    equality_calls = []
    class Key(str):
        __hash__ = str.__hash__
        def __eq__(self, other):
            equality_calls.append(other)
            return str.__eq__(self, other)
    selected = limits()
    value = selected.pop("capsule_limits")
    selected[Key("capsule_limits")] = value
    equality_calls.clear()
    original_keys = tuple(selected)
    with error("LIMIT_INVALID"):
        budget.OperationBudget(selected)
    assert not equality_calls
    assert all(left is right for left, right in zip(original_keys, selected, strict=True))


@pytest.mark.parametrize("change", [{"max_read_bytes": True}, {"deadline_ms": 1.1},
    {"max_write_bytes": 0}, {"max_failure_bytes": 97}, {"chunk_bytes": 1}, {"max_attempts": 2}])
def test_invalid_limits_fail_before_ledger_effects(change):
    with error("LIMIT_INVALID"):
        budget.OperationBudget(limits(**change))


@pytest.mark.parametrize("count", [0, -1, True, 1.0, 131_073])
def test_invalid_io_size_rejected_before_counting(count):
    ledger = budget.OperationBudget(limits())
    with error("LIMIT_INVALID"):
        ledger.begin_read(count)
    assert ledger.snapshot()["read_calls"] == 0
