"""Independent Q1 declarations and small native traces with the original ledger.

The supplied occupied-slot tuple is a declaration, never evidence of a disk
claim, owner, completed factory or crash recovery.  Native traces below only
check the slot-limit adapter against the existing single-run budget engine.
"""
from copy import deepcopy
import builtins
import os
from pathlib import Path
import sqlite3
from types import MappingProxyType

import pytest

from reno import personal_authority_preservation_budget as budget
from reno import personal_authority_resume_policy as policy


GIB = 1024**3
ERROR = policy.AuthorityResumePolicyError
PREFIX = "AUTHORITY_RAW_PRESERVATION2_POLICY_"
Q1 = {
    "profile": "q1", "max_attempts": 2,
    "max_read_bytes": 512 * GIB, "max_write_bytes": 128 * GIB,
    "source_max_read_bytes": 384 * GIB, "active_ms": 120000,
    "slot_read_bytes": 256 * GIB, "slot_write_bytes": 64 * GIB,
    "slot_source_read_bytes": 192 * GIB, "slot_active_ms": 60000,
    "max_request_bytes": 1048576,
}
SLOT = {"read_bytes": 256 * GIB, "write_bytes": 64 * GIB,
    "source_read_bytes": 192 * GIB, "active_ms": 60000, "attempts": 1}
CAPSULE_LIMITS = {
    "max_files": 100000, "max_directories": 1000000, "max_entries": 64000000,
    "max_artifacts": 100000, "max_links": 64000000,
    "max_json_bytes": 134217728, "max_total_bytes": 68719476736,
    "max_raw_json_bytes": 134217728, "max_json_depth": 64,
    "max_json_nodes": 4000000, "max_json_string_bytes": 134217728,
    "max_json_integer_digits": 4300,
}


def reservation(slot):
    return {"profile": "q1", "slot": slot, "read_bytes": 256 * GIB,
        "write_bytes": 64 * GIB, "source_read_bytes": 192 * GIB, "active_ms": 60000}


def expected_limits():
    return {"capsule_limits": dict(CAPSULE_LIMITS), "source_max_read_bytes": 192 * GIB,
        "max_read_bytes": 256 * GIB, "max_write_bytes": 64 * GIB, "deadline_ms": 60000,
        "node_json_bytes": 4096, "chunk_bytes": 131072, "max_control_files": 300032,
        "max_target_entries": 400064, "max_target_depth": 8, "max_fact_bytes": 134217728,
        "max_failure_bytes": 16384, "max_attempts": 1}


def reject(function, *args, suffix=None, **options):
    with pytest.raises(ERROR) as raised:
        function(*args, **options)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith(PREFIX)
    if suffix is not None:
        assert raised.value.code == PREFIX + suffix
    return raised.value


def hostile_types():
    """Build hostile subclasses without invoking their hooks during setup."""
    calls = []
    armed = [False]
    def hook(name):
        if armed[0]:
            calls.append(name)
            raise AssertionError("Caller subclass hook was invoked: " + name)

    class HostileStr(str):
        def __eq__(self, other):
            hook("str.eq")
            return str.__eq__(self, other)
        def __hash__(self):
            hook("str.hash")
            return str.__hash__(self)
        def __len__(self):
            hook("str.len")
            return str.__len__(self)
        def __iter__(self):
            hook("str.iter")
            return str.__iter__(self)

    class HostileInt(int):
        def __eq__(self, other):
            hook("int.eq")
            return int.__eq__(self, other)
        def __hash__(self):
            hook("int.hash")
            return int.__hash__(self)
        def __int__(self):
            hook("int.int")
            return int.__int__(self)
        def __index__(self):
            hook("int.index")
            return int.__index__(self)

    class HostileDict(dict):
        def __len__(self):
            hook("dict.len")
            return dict.__len__(self)
        def __iter__(self):
            hook("dict.iter")
            return dict.__iter__(self)
        def __getitem__(self, key):
            hook("dict.getitem")
            return dict.__getitem__(self, key)
        def keys(self):
            hook("dict.keys")
            return dict.keys(self)
        def items(self):
            hook("dict.items")
            return dict.items(self)

    class HostileTuple(tuple):
        def __len__(self):
            hook("tuple.len")
            return tuple.__len__(self)
        def __iter__(self):
            hook("tuple.iter")
            return tuple.__iter__(self)
        def __getitem__(self, key):
            hook("tuple.getitem")
            return tuple.__getitem__(self, key)
        def __eq__(self, other):
            hook("tuple.eq")
            return tuple.__eq__(self, other)
        def __hash__(self):
            hook("tuple.hash")
            return tuple.__hash__(self)

    return calls, armed, HostileStr, HostileInt, HostileDict, HostileTuple


def test_q1_declaration_is_the_exact_fixed_profile_and_detached_each_time():
    first, second = policy.q1_declaration(), policy.q1_declaration()
    assert type(first) is dict and first == second == Q1
    assert first is not second
    first["max_write_bytes"] = 1
    first["invented_refund"] = True
    assert second == policy.q1_declaration() == Q1


def test_normalize_q1_returns_a_new_plain_declaration_without_modifying_input():
    value = dict(Q1)
    before = deepcopy(value)
    normalized = policy.normalize_q1(value)
    assert type(normalized) is dict and normalized == value == before
    assert normalized is not value
    normalized.clear()
    assert value == before and policy.normalize_q1(value) == Q1


@pytest.mark.parametrize("key", tuple(Q1))
def test_q1_missing_extra_and_replaced_fields_are_rejected(key):
    missing = dict(Q1)
    missing.pop(key)
    reject(policy.normalize_q1, missing, suffix="FIELDS_INVALID")
    extra = {**Q1, "observed_counters": {"write_bytes": 0}}
    reject(policy.normalize_q1, extra, suffix="FIELDS_INVALID")
    replaced = dict(missing, not_the_original_field=Q1[key])
    reject(policy.normalize_q1, replaced, suffix="FIELDS_INVALID")


@pytest.mark.parametrize("key", tuple(key for key in Q1 if key != "profile"))
@pytest.mark.parametrize("delta", [-1, 1])
def test_q1_rejects_both_smaller_and_larger_custom_numeric_quotas(key, delta):
    value = {**Q1, key: Q1[key] + delta}
    reject(policy.normalize_q1, value, suffix="Q1_MISMATCH")


@pytest.mark.parametrize("name", ["Q1", "q2", " q1", "q1 ", ""])
def test_q1_profile_is_not_coerced_or_case_normalized(name):
    reject(policy.normalize_q1, {**Q1, "profile": name}, suffix="Q1_MISMATCH")


@pytest.mark.parametrize("key", tuple(key for key in Q1 if key != "profile"))
@pytest.mark.parametrize("invalid", [True, False, 2.0, "2", None])
def test_q1_numeric_fields_require_exact_integer_type(key, invalid):
    reject(policy.normalize_q1, {**Q1, key: invalid}, suffix="TYPE_INVALID")


@pytest.mark.parametrize("kind", ["q1", "reservation"])
@pytest.mark.parametrize("invalid", [None, [], (), "q1", True, MappingProxyType({})])
def test_normalizers_require_exact_plain_dictionary_outer_type(kind, invalid):
    normalize = policy.normalize_q1 if kind == "q1" else policy.normalize_reservation
    reject(normalize, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("kind", ["q1", "reservation"])
@pytest.mark.parametrize("shape", ["outer", "key", "profile", "integer"])
def test_dictionary_and_value_subclasses_are_rejected_before_hooks(kind, shape):
    calls, armed, Text, Integer, Dictionary, _Tuple = hostile_types()
    normalize = policy.normalize_q1 if kind == "q1" else policy.normalize_reservation
    value = dict(Q1) if kind == "q1" else reservation(1)
    if shape == "outer":
        value = Dictionary(value)
    elif shape == "key":
        original = value.pop("profile")
        value[Text("profile")] = original
    elif shape == "profile":
        value["profile"] = Text("q1")
    else:
        field = "max_read_bytes" if kind == "q1" else "read_bytes"
        value[field] = Integer(value[field])
    armed[0] = True
    reject(normalize, value, suffix="TYPE_INVALID")
    assert calls == []


@pytest.mark.parametrize("slot", [1, 2])
def test_slot_limits_preserve_original_structural_caps_and_single_run_adapter(slot):
    value = policy.slot_limits(slot)
    assert type(value) is dict and value == expected_limits()
    assert type(value["capsule_limits"]) is dict
    assert value["max_attempts"] == 1  # The outer Q1 policy has two separately admitted slots.
    assert value["max_failure_bytes"] == 16384 < value["max_write_bytes"]
    value["capsule_limits"]["max_files"] = 0
    value["max_write_bytes"] = 1
    assert policy.slot_limits(slot) == expected_limits()
    assert policy.slot_limits(3 - slot) == expected_limits()


@pytest.mark.parametrize("function", [policy.slot_limits, policy.slot_reservation])
@pytest.mark.parametrize("invalid", [None, True, False, "1", 1.0, [], (), {"slot": 1}])
def test_slot_apis_reject_coerced_and_noninteger_slots(function, invalid):
    reject(function, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("function", [policy.slot_limits, policy.slot_reservation])
@pytest.mark.parametrize("invalid", [-1, 0, 3, 2**80])
def test_slot_apis_accept_only_the_two_fixed_ordinals(function, invalid):
    reject(function, invalid, suffix="SLOT_INVALID")


@pytest.mark.parametrize("function", [policy.slot_limits, policy.slot_reservation])
def test_slot_integer_subclass_cannot_invoke_comparison_or_conversion(function):
    calls, armed, _Text, Integer, _Dictionary, _Tuple = hostile_types()
    value = Integer(1)
    armed[0] = True
    reject(function, value, suffix="TYPE_INVALID")
    assert calls == []


@pytest.mark.parametrize("slot", [1, 2])
def test_reservation_is_full_quota_with_no_counters_and_detached(slot):
    value = policy.slot_reservation(slot)
    assert type(value) is dict and value == reservation(slot)
    normalized = policy.normalize_reservation(value)
    assert type(normalized) is dict and normalized == value and normalized is not value
    normalized["read_bytes"] = 0
    value.clear()
    assert policy.slot_reservation(slot) == reservation(slot)


@pytest.mark.parametrize("key", ("profile", "slot", "read_bytes", "write_bytes", "source_read_bytes", "active_ms"))
def test_reservation_field_set_is_closed(key):
    value = reservation(1)
    value.pop(key)
    reject(policy.normalize_reservation, value, suffix="FIELDS_INVALID")
    reject(policy.normalize_reservation, {**reservation(1), "remaining_bytes": 0}, suffix="FIELDS_INVALID")


@pytest.mark.parametrize("key", ("read_bytes", "write_bytes", "source_read_bytes", "active_ms"))
@pytest.mark.parametrize("delta", [-1, 1])
def test_reservation_cannot_refund_or_increase_a_quota(key, delta):
    value = reservation(1)
    value[key] += delta
    reject(policy.normalize_reservation, value, suffix="Q1_MISMATCH")


@pytest.mark.parametrize("slot,suffix", [(True, "TYPE_INVALID"), (1.0, "TYPE_INVALID"),
    ("2", "TYPE_INVALID"), (0, "SLOT_INVALID"), (3, "SLOT_INVALID")])
def test_reservation_slot_field_obeys_the_fixed_ordinal_contract(slot, suffix):
    reject(policy.normalize_reservation, reservation(slot), suffix=suffix)


@pytest.mark.parametrize("occupied", [(), (1,), (1, 2)])
def test_allocation_exact_totals_keep_remaining_slot_available_without_refunds(occupied):
    value = policy.allocation(occupied)
    count = len(occupied)
    expected = {"occupied_slots": occupied,
        "reserved": {key: amount * count for key, amount in SLOT.items()},
        "remaining": {key: amount * (2 - count) for key, amount in SLOT.items()}}
    assert type(value) is MappingProxyType and dict(value) == expected
    assert type(value["reserved"]) is MappingProxyType
    assert type(value["remaining"]) is MappingProxyType
    for key, amount in SLOT.items():
        assert value["reserved"][key] + value["remaining"][key] == amount * 2
    assert value["reserved"]["source_read_bytes"] <= value["reserved"]["read_bytes"]
    assert set(value).isdisjoint({"owner", "claimed", "ready", "verified", "factory_returned"})
    with pytest.raises(TypeError):
        value["occupied_slots"] = ()
    with pytest.raises(TypeError):
        value["reserved"]["write_bytes"] = 0
    with pytest.raises(TypeError):
        value["remaining"]["attempts"] = 3


def test_first_slot_allocation_does_not_consume_the_second_slots_reserved_capacity():
    initial, first, both = (policy.allocation(slots) for slots in ((), (1,), (1, 2)))
    assert initial["remaining"]["read_bytes"] == 512 * GIB
    assert initial["remaining"]["write_bytes"] == 128 * GIB
    assert first["reserved"]["write_bytes"] == first["remaining"]["write_bytes"] == 64 * GIB
    assert first["remaining"]["read_bytes"] == 256 * GIB
    assert first["remaining"]["source_read_bytes"] == 192 * GIB
    assert first["remaining"]["active_ms"] == 60000 and first["remaining"]["attempts"] == 1
    assert all(value == 0 for value in both["remaining"].values())
    # Later pure queries cannot mutate a previously returned historical view.
    assert initial["occupied_slots"] == () and first["occupied_slots"] == (1,)


@pytest.mark.parametrize("invalid", [None, [], [1], {1}, {"slot": 1}, "1", range(2)])
def test_allocation_requires_an_exact_tuple(invalid):
    reject(policy.allocation, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("invalid", [(2,), (0,), (-1,), (3,), (1, 1), (2, 1), (1, 3), (1, 2, 3)])
def test_allocation_rejects_holes_duplicates_reordering_and_extra_attempts(invalid):
    reject(policy.allocation, invalid, suffix="SLOTS_INVALID")


@pytest.mark.parametrize("invalid", [(True,), (1.0,), ("1",), (1, False), (1, None)])
def test_allocation_rejects_nonexact_integer_members(invalid):
    reject(policy.allocation, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("shape", ["outer", "member"])
def test_allocation_rejects_subclasses_without_len_iter_eq_or_hash_callbacks(shape):
    calls, armed, _Text, Integer, _Dictionary, Tuple = hostile_types()
    value = Tuple((1,)) if shape == "outer" else (Integer(1),)
    armed[0] = True
    reject(policy.allocation, value, suffix="TYPE_INVALID")
    assert calls == []


@pytest.mark.parametrize("name", ["counters", "observed_write_bytes", "failure", "receipt", "refund", "elapsed_ms"])
def test_allocation_has_no_inputs_that_can_refund_an_unknown_old_run(name):
    with pytest.raises(TypeError):
        policy.allocation((1,), **{name: 0})
    with pytest.raises(TypeError):
        policy.allocation((1,), {name: 0})
    assert dict(policy.allocation((1,))["reserved"]) == SLOT


@pytest.mark.parametrize("sha", ["0" * 64, "f" * 64, "0123456789abcdef" * 4])
def test_anchor_name_is_a_fixed_metadata_name_without_claiming_hash_verification(sha):
    assert policy.request_anchor_name(sha) == "admission-q1-sha256-" + sha


@pytest.mark.parametrize("sha", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64,
    " " + "a" * 63, "a" * 63 + "\n", "\ufeff" + "a" * 63, "ａ" * 64, "../" + "a" * 61])
def test_anchor_digest_requires_exact_lowercase_ascii_hex_without_path_or_whitespace(sha):
    reject(policy.request_anchor_name, sha, suffix="SHA_INVALID")


@pytest.mark.parametrize("sha", [None, True, 0, b"a" * 64, bytearray(b"a" * 64), ["a"] * 64])
def test_anchor_digest_rejects_nontext_types(sha):
    reject(policy.request_anchor_name, sha, suffix="TYPE_INVALID")


def test_anchor_text_subclass_cannot_execute_len_iteration_or_equality():
    calls, armed, Text, _Integer, _Dictionary, _Tuple = hostile_types()
    value = Text("a" * 64)
    armed[0] = True
    reject(policy.request_anchor_name, value, suffix="TYPE_INVALID")
    assert calls == []


def test_policy_helpers_do_not_open_paths_sql_or_a_clock_to_establish_occupancy(monkeypatch):
    def forbidden(*_args, **_options):
        pytest.fail("Pure Q1 declaration attempted I/O or time-based occupancy inference")
    import time
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "open", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(Path, "write_bytes", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(time, "monotonic_ns", forbidden)
        assert policy.q1_declaration() == policy.normalize_q1(dict(Q1)) == Q1
        for slot in (1, 2):
            assert policy.slot_limits(slot) == expected_limits()
            assert policy.normalize_reservation(reservation(slot)) == policy.slot_reservation(slot)
        assert policy.allocation((1,))["remaining"]["attempts"] == 1
        assert policy.request_anchor_name("a" * 64) == "admission-q1-sha256-" + "a" * 64


@pytest.mark.parametrize("slot", [1, 2])
def test_slot_adapter_works_with_original_budget_and_real_small_read_write_trace(tmp_path, slot):
    limits = policy.slot_limits(slot)
    original_limits = deepcopy(limits)
    ledger = budget.OperationBudget(limits)
    raw = b"independent native bytes through fixed Q1 slot"
    path = tmp_path / "payload.raw"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    writes, reads, chunks = [], [], []
    try:
        offset = 0
        while offset < len(raw):
            ticket = ledger.begin_write(len(raw) - offset)
            count = os.write(fd, raw[offset:offset + 7])
            assert count > 0
            writes.append(count)
            ledger.end_write(ticket, count)
            ledger.check()
            offset += count
        os.fsync(fd)
        ledger.note_seek()
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            ticket = ledger.begin_read(11)
            chunk = os.read(fd, 11)
            reads.append(len(chunk))
            ledger.end_read(ticket, chunk)
            ledger.check()
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    ledger.assert_settled()
    observed = ledger.snapshot()
    assert b"".join(chunks) == path.read_bytes() == raw
    assert observed["read_bytes"] == sum(reads) == len(raw)
    assert observed["confirmed_write_bytes"] == sum(writes) == len(raw)
    assert observed["read_calls"] == len(reads) and observed["write_calls"] == len(writes)
    assert observed["seek_calls"] == 1
    assert observed["uncertain_read_calls"] == observed["uncertain_write_bytes"] == 0
    assert limits == original_limits == expected_limits()
    assert dict(policy.allocation((1,))["reserved"]) == SLOT


def test_unknown_actual_write_and_bounded_failure_remain_inside_original_slot_without_refund(tmp_path):
    ledger = budget.OperationBudget(policy.slot_limits(1))
    raw, diagnostic = b"payload actual return became unknown", b"bounded failure bytes"
    payload = tmp_path / "partial.raw"
    fd = os.open(payload, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    original = OSError("injected after a real partial native write")
    try:
        ticket = ledger.begin_write(len(raw))
        assert os.write(fd, raw[:3]) == 3
        with pytest.raises(OSError) as failed:
            ledger.fail_write(ticket, original)
        assert failed.value is original
    finally:
        os.close(fd)
    assert payload.read_bytes() == raw[:3]
    assert ledger.snapshot()["confirmed_write_bytes"] == 0
    assert ledger.snapshot()["uncertain_write_bytes"] == len(raw)
    ledger.begin_failure()
    failure_path = tmp_path / "failure.raw"
    failure_fd = os.open(failure_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        ticket = ledger.begin_write(len(diagnostic), failure=True)
        count = os.write(failure_fd, diagnostic)
        assert count == len(diagnostic)
        ledger.end_write(ticket, count)
        os.fsync(failure_fd)
    finally:
        os.close(failure_fd)
    assert failure_path.read_bytes() == diagnostic
    observed = ledger.snapshot()
    assert observed["confirmed_write_bytes"] == len(diagnostic)
    assert observed["uncertain_write_bytes"] == len(raw) and observed["write_calls"] == 2
    with pytest.raises(OSError) as still_failed:
        ledger.begin_write(1)
    assert still_failed.value is original and ledger.first is original
    assert dict(policy.allocation((1,))["reserved"]) == SLOT
    assert dict(policy.allocation((1,))["remaining"]) == SLOT
    assert all(value == 0 for value in policy.allocation((1, 2))["remaining"].values())


def test_readonly_ledger_compatibility_does_not_grant_a_producer_slot(tmp_path):
    raw = b"synthetic existing observation bytes"
    path = tmp_path / "existing.raw"
    path.write_bytes(raw)
    ledger = budget.OperationBudget(policy.slot_limits(2), readonly=True)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        ticket = ledger.begin_read(len(raw))
        observed = os.read(fd, len(raw))
        ledger.end_read(ticket, observed)
    finally:
        os.close(fd)
    assert observed == raw
    with pytest.raises(budget.AuthorityRawPreservationBudgetError) as rejected:
        ledger.begin_write(1)
    assert rejected.value.code == "AUTHORITY_RAW_PRESERVATION_WRITE_LIMIT"
    assert ledger.snapshot()["confirmed_write_bytes"] == ledger.snapshot()["write_calls"] == 0
    assert policy.allocation(())["reserved"]["attempts"] == 0
