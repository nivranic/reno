"""Independent RP1 declarations and remaining-demand event accounting.

Only pure policy calls are exercised.  A declaration is not a disk admission,
resource acquisition, preemptible timeout, replica factory or close receipt.
Expected costs below are sums of named future steps, independently of the
product's implementation; the initial source scan is already spent.
"""
from copy import deepcopy
import builtins
import io
import os
from pathlib import Path
import socket
import sqlite3
import time
from types import MappingProxyType

import pytest

from reno import personal_authority_replica_policy as policy


PREFIX = "AUTHORITY_REPLICA_POLICY1_"
ERROR = policy.AuthorityReplicaPolicyError
PRODUCER = {
    "max_read_bytes": 2199023255552,
    "source_max_read_bytes": 1099511627776,
    "max_write_bytes": 274877906944,
    "deadline_ms": 1800000,
    "chunk_bytes": 131072,
    "max_failure_bytes": 16384,
    "max_resources": 1000000,
}
READONLY = {
    "max_read_bytes": 1099511627776,
    "source_max_read_bytes": 1099511627776,
    "max_write_bytes": 0,
    "deadline_ms": 1800000,
    "chunk_bytes": 131072,
    "max_failure_bytes": 0,
    "max_resources": 1000000,
}
STRUCTURE = {
    "max_package_bytes": 137438953472,
    "max_package_entries": 400064,
    "max_request_bytes": 1048576,
    "max_fact_bytes": 134217728,
    "max_path_components": 128,
    "max_node_bytes": 4096,
    "max_json_depth": 64,
    "max_json_nodes": 4000000,
    "max_json_integer_digits": 4300,
}
CAPS = {
    "request": 1048576,
    "attempt": 16384,
    "source_selection": 134217728,
    "copy_attempt": 8192,
    "copy_record": 16384,
    "replica": 134217728,
    "source_closed": 16384,
    "target_closed": 16384,
    "manifest": 134217728,
    "receipt": 16384,
    "failure": 16384,
}
RP1 = {
    "format_version": 1, "profile": "replica-rp1", "max_attempts": 2,
    "producer": PRODUCER, "readonly": READONLY,
    "structure": STRUCTURE, "control_caps": CAPS,
}
SLOT = {
    "read_bytes": 2199023255552, "source_read_bytes": 1099511627776,
    "write_bytes": 274877906944, "active_ms": 1800000, "attempts": 1,
}
RP1_NUMERIC_PATHS = (
    ("format_version",), ("max_attempts",),
    *((group, key) for group in ("producer", "readonly", "structure", "control_caps")
      for key in RP1[group]),
)
DEMAND_NUMERIC_PATHS = (
    ("format_version",), ("failure_write_reserve_bytes",),
    ("peak_additional_resources",),
    ("read_bytes",), ("source_read_bytes",), ("write_bytes",),
    *((group, key) for group in ("known_minimum", "capacity_reserve")
      for key in ("read_bytes", "source_read_bytes", "write_bytes")),
    ("read_headroom", "read_bytes"), ("read_headroom", "source_read_bytes"),
)


def reject(function, *args, suffix=None, **kwargs):
    with pytest.raises(ERROR) as raised:
        function(*args, **kwargs)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith(PREFIX)
    if suffix is not None:
        assert raised.value.code == PREFIX + suffix
    return raised.value


def field_parent(value, path):
    for key in path[:-1]:
        value = value[key]
    return value, path[-1]


def assert_plain(value):
    if type(value) is dict:
        for key, child in value.items():
            assert type(key) is str
            assert_plain(child)
    elif type(value) is list:
        for child in value:
            assert_plain(child)
    else:
        assert type(value) in (str, int)


def selection(**changes):
    value = {
        "package_file_count": 2, "package_directory_count": 3,
        "package_bytes": 257, "request_bytes": 101, "attempt_bytes": 83,
        "source_selection_bytes": 197, "predecessor_attempt_bytes": 0,
        "observer_node_bytes": 47,
    }
    value.update(changes)
    return value


def expected_payload(value):
    """A small-input oracle composed from individual protocol events."""
    package = value["package_bytes"]
    package_reads = [
        ("source_same_fd_copy", package, True),
        ("source_final_pass_1", package, True),
        ("source_final_pass_2", package, True),
        ("target_individual_readback", package, False),
        ("target_whole_package_readback", package, False),
        ("target_final_pass_1", package, False),
        ("target_final_pass_2", package, False),
        ("publisher_reopen_package", package, False),
        ("publisher_final_pass_1", package, False),
        ("publisher_final_pass_2", package, False),
    ]
    published_reads = [
        (phase + ":" + field, value[field], False)
        for phase in ("target_final_1", "target_final_2", "publisher_reopen",
                      "publisher_final_1", "publisher_final_2")
        for field in ("request_bytes", "attempt_bytes", "source_selection_bytes",
                      "predecessor_attempt_bytes", "observer_node_bytes")
    ]
    copy_controls = [
        ("copy_attempt_" + str(index), 8192) if kind == "attempt"
        else ("copy_record_" + str(index), 16384)
        for index in range(value["package_file_count"])
        for kind in ("attempt", "record")
    ]
    late_controls = [
        ("replica", 134217728), ("source_closed", 16384),
        ("target_closed", 16384), ("manifest", 134217728), ("receipt", 16384),
    ]
    copy_reads = [
        (phase + ":" + name, cap)
        for name, cap in copy_controls
        for phase in ("temp_readback", "formal_readback", "target_final_1",
                      "target_final_2", "publisher_reopen", "publisher_final_1",
                      "publisher_final_2")
    ]
    late_reads = [
        (phase + ":" + name, cap)
        for name, cap in late_controls
        for phase in ("temp_readback", "formal_readback", "publisher_final_1",
                      "publisher_final_2")
    ]
    additional_resources = [
        ("target_package_readers", value["package_file_count"]),
        ("copy_attempt_readers", value["package_file_count"]),
        ("copy_record_readers", value["package_file_count"]),
        ("target_package_directories", value["package_directory_count"]),
        ("fixed_and_transient", 64),
    ]
    known = {
        "read_bytes": sum(amount for _, amount, _ in package_reads + published_reads),
        "source_read_bytes": sum(amount for _, amount, source in package_reads if source),
        "write_bytes": package,
    }
    capacity = {
        "read_bytes": sum(amount for _, amount in copy_reads + late_reads),
        "source_read_bytes": 0,
        "write_bytes": sum(amount for _, amount in copy_controls + late_controls),
    }
    return {
        "format_version": 1, "profile": "replica-rp1", "stage": "before_payload",
        "inputs": deepcopy(value), "known_minimum": known,
        "capacity_reserve": capacity,
        "read_headroom": {"read_bytes": 131072, "source_read_bytes": 131072},
        "failure_write_reserve_bytes": 16384,
        "peak_additional_resources": sum(amount for _, amount in additional_resources),
        "read_bytes": sum((known["read_bytes"], capacity["read_bytes"], 131072)),
        "source_read_bytes": sum((known["source_read_bytes"], 131072)),
        "write_bytes": sum((known["write_bytes"], capacity["write_bytes"], 16384)),
    }


def expected_bootstrap(slot):
    if slot == 1:
        reads = [("request_temp", 1048576), ("request_formal", 1048576),
                 ("attempt_temp", 16384), ("attempt_formal", 16384),
                 ("observer_node", 4096)]
        writes = [("request", 1048576), ("attempt", 16384)]
    else:
        reads = [("original_request", 1048576), ("predecessor_attempt", 16384),
                 ("old_receipt_probe", 16384), ("new_attempt_temp", 16384),
                 ("new_attempt_formal", 16384), ("observer_node", 4096)]
        writes = [("new_attempt", 16384)]
    return {
        "format_version": 1, "profile": "replica-rp1", "stage": "bootstrap",
        "inputs": {"slot": slot},
        "known_minimum": {"read_bytes": 0, "source_read_bytes": 0, "write_bytes": 0},
        "capacity_reserve": {"read_bytes": sum(amount for _, amount in reads),
                             "source_read_bytes": 0,
                             "write_bytes": sum(amount for _, amount in writes)},
        "read_headroom": {"read_bytes": 131072, "source_read_bytes": 0},
        "failure_write_reserve_bytes": 16384, "peak_additional_resources": 4096,
        "read_bytes": sum(amount for _, amount in reads) + 131072,
        "source_read_bytes": 0,
        "write_bytes": sum(amount for _, amount in writes) + 16384,
    }


def hostile_types():
    calls, armed = [], [False]

    def hook(name):
        if armed[0]:
            calls.append(name)
            raise AssertionError("Caller hook executed: " + name)

    class Text(str):
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

    class Integer(int):
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
        def __lt__(self, other):
            hook("int.lt")
            return int.__lt__(self, other)
        def __add__(self, other):
            hook("int.add")
            return int.__add__(self, other)

    class Dictionary(dict):
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

    class Tuple(tuple):
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

    return calls, armed, Text, Integer, Dictionary, Tuple


def test_rp1_fixed_declaration_is_plain_detached_and_not_caller_configurable():
    value = policy.rp1_declaration()
    assert policy.PROFILE == "replica-rp1"
    assert value == RP1
    assert_plain(value)
    original = deepcopy(value)
    normalized = policy.normalize_rp1(value)
    assert normalized == original and normalized is not value
    for group in ("producer", "readonly", "structure", "control_caps"):
        assert normalized[group] is not value[group]
        normalized[group].clear()
        assert value[group] == original[group]
        value[group].clear()
    assert policy.rp1_declaration() == RP1
    assert policy.normalize_rp1(deepcopy(RP1)) == RP1


@pytest.mark.parametrize("path", RP1_NUMERIC_PATHS)
@pytest.mark.parametrize("delta", [-1, 1])
def test_every_fixed_integer_rejects_smaller_or_larger_custom_policy(path, delta):
    value = deepcopy(RP1)
    parent, key = field_parent(value, path)
    parent[key] += delta
    reject(policy.normalize_rp1, value, suffix="RP1_MISMATCH")


@pytest.mark.parametrize("path", RP1_NUMERIC_PATHS)
def test_every_fixed_integer_rejects_boolean_even_when_numerically_equal(path):
    value = deepcopy(RP1)
    parent, key = field_parent(value, path)
    parent[key] = bool(parent[key])
    reject(policy.normalize_rp1, value, suffix="TYPE_INVALID")


@pytest.mark.parametrize("group", [None, "producer", "readonly", "structure", "control_caps"])
def test_rp1_field_sets_are_closed_at_every_dictionary(group):
    prototype = RP1 if group is None else RP1[group]
    for key in prototype:
        value = deepcopy(RP1)
        parent = value if group is None else value[group]
        parent.pop(key)
        reject(policy.normalize_rp1, value, suffix="FIELDS_INVALID")
    value = deepcopy(RP1)
    parent = value if group is None else value[group]
    parent["refund"] = 0
    reject(policy.normalize_rp1, value, suffix="FIELDS_INVALID")


@pytest.mark.parametrize("profile", ["", "rp1", "replica-RP1", " replica-rp1", "replica-rp1 "])
def test_profile_is_not_normalized_or_coerced(profile):
    reject(policy.normalize_rp1, {**deepcopy(RP1), "profile": profile}, suffix="RP1_MISMATCH")


@pytest.mark.parametrize("mode,expected", [("producer", PRODUCER), ("readonly", READONLY)])
def test_runtime_limits_are_fixed_single_operation_declarations(mode, expected):
    first, second = policy.runtime_limits(mode), policy.runtime_limits(mode)
    assert first == second == expected
    assert_plain(first)
    assert first is not second
    first.clear()
    assert second == policy.runtime_limits(mode) == expected
    assert expected["source_max_read_bytes"] <= expected["max_read_bytes"]
    if mode == "readonly":
        assert expected["max_write_bytes"] == expected["max_failure_bytes"] == 0
    # A duration declaration provides no clock, deadline token or OS preemption.
    assert set(second).isdisjoint({"deadline", "started", "preemptible", "slot", "ticket"})


@pytest.mark.parametrize("mode", ["", "PRODUCER", "preview", "verify", "producer ", "q1"])
def test_runtime_mode_requires_one_of_two_exact_names(mode):
    reject(policy.runtime_limits, mode, suffix="MODE_INVALID")


@pytest.mark.parametrize("invalid", [None, True, 1, 1.0, b"producer", [], {}])
def test_runtime_mode_rejects_nontext_without_coercion(invalid):
    reject(policy.runtime_limits, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("slot", [1, 2])
def test_each_slot_reserves_one_full_fixed_quota_without_transfer(slot):
    expected = {"profile": "replica-rp1", "slot": slot, **SLOT}
    first, second = policy.slot_reservation(slot), policy.slot_reservation(slot)
    assert first == second == expected
    assert_plain(first)
    assert first is not second
    first["write_bytes"] = 0
    first["slot"] = 3
    assert second == policy.slot_reservation(slot) == expected
    assert policy.slot_reservation(3 - slot) == {**expected, "slot": 3 - slot}


@pytest.mark.parametrize("name", ["slot_reservation", "bootstrap_demand"])
@pytest.mark.parametrize("invalid", [True, False, 1.0, "1", None, [], (), {}])
def test_slot_apis_reject_nonexact_integers(name, invalid):
    reject(getattr(policy, name), invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("name", ["slot_reservation", "bootstrap_demand"])
@pytest.mark.parametrize("invalid", [-1, 0, 3, 2**100])
def test_slot_apis_reject_nonordinal_integers(name, invalid):
    reject(getattr(policy, name), invalid, suffix="SLOT_INVALID")


@pytest.mark.parametrize("occupied", [(), (1,), (1, 2)])
def test_allocation_uses_whole_slots_and_one_concurrent_resource_cap(occupied):
    expected = {
        "profile": "replica-rp1", "occupied_slots": list(occupied),
        "reserved": {key: sum(amount for _slot in occupied) for key, amount in SLOT.items()},
        "max_resources": 1000000,
    }
    first = policy.allocation(occupied)
    second = policy.allocation(occupied)
    assert_plain(first)
    assert first == second == expected
    assert first is not second
    assert first["occupied_slots"] is not second["occupied_slots"]
    assert first["reserved"] is not second["reserved"]
    first["occupied_slots"].append(3)
    first["reserved"].clear()
    assert second == policy.allocation(occupied) == expected
    assert set(second).isdisjoint({"owner", "claimed", "ready", "receipt", "remaining"})


@pytest.mark.parametrize("invalid", [None, [], [1], {1}, {"slot": 1}, "1", range(2)])
def test_allocation_requires_exact_tuple(invalid):
    reject(policy.allocation, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("invalid", [(2,), (0,), (-1,), (3,), (1, 1), (2, 1), (1, 3), (1, 2, 3)])
def test_allocation_accepts_only_prefixes_of_fixed_two_slots(invalid):
    reject(policy.allocation, invalid, suffix="OCCUPANCY_INVALID")


@pytest.mark.parametrize("invalid", [(True,), (1.0,), ("1",), (1, False), (1, None)])
def test_allocation_rejects_nonexact_integer_members(invalid):
    reject(policy.allocation, invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("name", ["counters", "failure", "receipt", "refund", "elapsed_ms", "transfer"])
def test_caller_cannot_refund_or_transfer_unknown_failed_slot_occupancy(name):
    with pytest.raises(TypeError):
        policy.allocation((1,), **{name: 0})
    with pytest.raises(TypeError):
        policy.slot_reservation(2, **{name: 0})
    assert policy.allocation((1,))["reserved"] == SLOT
    assert policy.slot_reservation(2) == {"profile": "replica-rp1", "slot": 2, **SLOT}


@pytest.mark.parametrize("slot", [1, 2])
def test_bootstrap_remaining_costs_follow_each_named_control_step(slot):
    value = policy.bootstrap_demand(slot)
    assert value == expected_bootstrap(slot)
    assert_plain(value)
    assert value["source_read_bytes"] == 0
    assert value["known_minimum"] == {"read_bytes": 0, "source_read_bytes": 0, "write_bytes": 0}
    assert value["write_bytes"] - value["capacity_reserve"]["write_bytes"] == 16384
    assert value["peak_additional_resources"] >= sum([129] * 8 + [64, 16])


def test_second_bootstrap_reuses_request_instead_of_rewriting_or_refunding_it():
    first, second = policy.bootstrap_demand(1), policy.bootstrap_demand(2)
    assert first["write_bytes"] == 1081344
    assert second["write_bytes"] == 32768
    assert first["capacity_reserve"]["read_bytes"] == 2134016
    assert second["capacity_reserve"]["read_bytes"] == 1118208
    assert second["capacity_reserve"]["write_bytes"] == 16384
    assert policy.allocation((1,))["reserved"]["write_bytes"] == 274877906944


@pytest.mark.parametrize("changes", [
    {}, {"predecessor_attempt_bytes": 113},
    {"package_file_count": 0, "package_directory_count": 1, "package_bytes": 0},
    {"package_file_count": 1, "package_directory_count": 1, "package_bytes": 0},
    {"package_file_count": 3, "package_directory_count": 7, "package_bytes": 8191,
     "request_bytes": 23, "attempt_bytes": 31, "source_selection_bytes": 97,
     "predecessor_attempt_bytes": 17, "observer_node_bytes": 53},
])
def test_payload_demand_matches_independently_enumerated_future_events(changes):
    source = selection(**changes)
    before = deepcopy(source)
    expected = expected_payload(source)
    value = policy.payload_demand(source)
    assert value == expected and source == before
    assert_plain(value)
    assert value["inputs"] is not source
    assert value["source_read_bytes"] <= value["read_bytes"]
    assert value["write_bytes"] - value["known_minimum"]["write_bytes"] - value["capacity_reserve"]["write_bytes"] == 16384


def test_initial_scan_is_already_spent_and_source_is_a_subset_of_total_reads():
    value = selection()
    without_package = policy.payload_demand({**value, "package_bytes": 0})
    with_package = policy.payload_demand(value)
    per_step_bytes = value["package_bytes"]
    source_future = sum([per_step_bytes, per_step_bytes, per_step_bytes])
    target_future = sum([per_step_bytes, per_step_bytes, per_step_bytes,
                         per_step_bytes, per_step_bytes, per_step_bytes, per_step_bytes])
    assert with_package["read_bytes"] - without_package["read_bytes"] == source_future + target_future
    assert with_package["source_read_bytes"] - without_package["source_read_bytes"] == source_future
    entire_path = sum([per_step_bytes, source_future, target_future])
    assert entire_path == 2827
    assert with_package["read_bytes"] - without_package["read_bytes"] == 2570
    assert with_package["write_bytes"] - without_package["write_bytes"] == per_step_bytes


@pytest.mark.parametrize("field", ["request_bytes", "attempt_bytes", "source_selection_bytes",
                                  "predecessor_attempt_bytes", "observer_node_bytes"])
def test_published_controls_and_observer_have_only_five_remaining_reads(field):
    baseline = selection()
    changed = {**baseline, field: baseline[field] + 1}
    first, second = policy.payload_demand(baseline), policy.payload_demand(changed)
    assert second["read_bytes"] - first["read_bytes"] == sum([1, 1, 1, 1, 1])
    assert second["source_read_bytes"] == first["source_read_bytes"]
    assert second["write_bytes"] == first["write_bytes"]
    assert second["capacity_reserve"] == first["capacity_reserve"]


def test_new_file_reserves_two_small_copy_controls_seven_reads_and_three_readers():
    base = selection(package_file_count=1)
    first = policy.payload_demand(base)
    second = policy.payload_demand({**base, "package_file_count": 2})
    copies = [("attempt", 8192), ("record", 16384)]
    reads = [(phase, amount) for _, amount in copies
             for phase in ("temp", "formal", "target_1", "target_2", "open", "publish_1", "publish_2")]
    assert second["capacity_reserve"]["read_bytes"] - first["capacity_reserve"]["read_bytes"] == sum(amount for _, amount in reads)
    assert second["write_bytes"] - first["write_bytes"] == sum(amount for _, amount in copies)
    assert second["known_minimum"] == first["known_minimum"]
    assert second["peak_additional_resources"] - first["peak_additional_resources"] == sum([1, 1, 1])
    assert second["failure_write_reserve_bytes"] == first["failure_write_reserve_bytes"] == 16384


def test_empty_package_keeps_late_control_capacity_and_eof_ticket_headroom():
    value = policy.payload_demand(selection(package_file_count=0, package_directory_count=1,
                                           package_bytes=0))
    late = [("replica", 134217728), ("source_closed", 16384), ("target_closed", 16384),
            ("manifest", 134217728), ("receipt", 16384)]
    reads = [(phase, cap) for _, cap in late for phase in ("temp", "formal", "final_1", "final_2")]
    assert value["capacity_reserve"] == {
        "read_bytes": sum(cap for _, cap in reads), "source_read_bytes": 0,
        "write_bytes": sum(cap for _, cap in late),
    }
    assert value["capacity_reserve"]["write_bytes"] == 268484608
    assert value["known_minimum"]["write_bytes"] == value["known_minimum"]["source_read_bytes"] == 0
    assert value["read_headroom"] == {"read_bytes": 131072, "source_read_bytes": 131072}
    assert value["source_read_bytes"] == 131072
    assert value["write_bytes"] == 268500992  # No invented one-byte EOF write.
    assert value["peak_additional_resources"] == sum([1, 64])


@pytest.mark.parametrize("field,maximum", [
    ("package_bytes", 137438953472), ("request_bytes", 1048576),
    ("attempt_bytes", 16384), ("source_selection_bytes", 134217728),
    ("predecessor_attempt_bytes", 16384), ("observer_node_bytes", 4096),
])
def test_each_selection_size_accepts_own_cap_and_rejects_one_byte_more(field, maximum):
    value = selection(**{field: maximum})
    assert policy.payload_demand(value) == expected_payload(value)
    reject(policy.payload_demand, {**value, field: maximum + 1}, suffix="LIMIT")


@pytest.mark.parametrize("field", tuple(selection()))
def test_selection_negative_integers_are_not_sizes_or_counts(field):
    reject(policy.payload_demand, selection(**{field: -1}), suffix="SELECTION_INVALID")


@pytest.mark.parametrize("field", ["package_directory_count", "request_bytes", "attempt_bytes",
                                  "source_selection_bytes", "observer_node_bytes"])
def test_required_positive_counts_and_control_sizes_cannot_be_zero(field):
    reject(policy.payload_demand, selection(**{field: 0}), suffix="SELECTION_INVALID")


@pytest.mark.parametrize("field", tuple(selection()))
@pytest.mark.parametrize("invalid", [True, 1.0, "1", None])
def test_selection_dimensions_are_exact_integers(field, invalid):
    reject(policy.payload_demand, selection(**{field: invalid}), suffix="TYPE_INVALID")


def test_zero_files_cannot_explain_nonzero_package_bytes():
    reject(policy.payload_demand, selection(package_file_count=0, package_bytes=1),
           suffix="SELECTION_INVALID")


def test_package_entry_limit_counts_root_and_empty_directories():
    all_dirs = selection(package_file_count=0, package_directory_count=400064, package_bytes=0)
    assert policy.payload_demand(all_dirs)["peak_additional_resources"] == 400128
    reject(policy.payload_demand, {**all_dirs, "package_directory_count": 400065}, suffix="LIMIT")
    many_files = selection(package_file_count=400063, package_directory_count=1)
    assert policy.payload_demand(many_files)["inputs"] == many_files
    reject(policy.payload_demand, {**many_files, "package_file_count": 400064}, suffix="LIMIT")


def test_resource_demand_above_runtime_cap_is_honest_not_clamped_or_refunded():
    value = policy.payload_demand(selection(package_file_count=400063, package_directory_count=1))
    expected_additions = {
        "target_readers": 400063, "attempt_readers": 400063,
        "record_readers": 400063, "target_root": 1, "fixed": 64,
    }
    assert value["peak_additional_resources"] == sum(expected_additions.values()) == 1200254
    assert value["peak_additional_resources"] > PRODUCER["max_resources"]
    # Source handles already held in current ledger are not subtracted here,
    # nor added to this additional-only vector a second time.
    assert policy.normalize_demand(value) == value


def test_selection_shape_disallows_caller_pass_counts_caps_or_spent_refunds():
    for key in selection():
        value = selection()
        value.pop(key)
        reject(policy.payload_demand, value, suffix="FIELDS_INVALID")
    for key in ("read_passes", "write_cap", "already_spent", "source_closed", "current_resources"):
        reject(policy.payload_demand, {**selection(), key: 0}, suffix="FIELDS_INVALID")


@pytest.mark.parametrize("kind", ["bootstrap1", "bootstrap2", "payload"])
def test_demand_normalization_is_full_detached_recomputation(kind):
    value = expected_payload(selection()) if kind == "payload" else expected_bootstrap(int(kind[-1]))
    before = deepcopy(value)
    normalized = policy.normalize_demand(value)
    assert normalized == before == value and normalized is not value
    assert_plain(normalized)
    for key in ("inputs", "known_minimum", "capacity_reserve", "read_headroom"):
        assert normalized[key] is not value[key]
        normalized[key].clear()
        assert value[key] == before[key]
    assert policy.normalize_demand(value) == before


@pytest.mark.parametrize("path", DEMAND_NUMERIC_PATHS)
@pytest.mark.parametrize("delta", [-1, 1])
def test_demand_rejects_altered_components_and_totals(path, delta):
    value = expected_payload(selection())
    parent, key = field_parent(value, path)
    parent[key] += delta
    reject(policy.normalize_demand, value, suffix="DEMAND_MISMATCH")


@pytest.mark.parametrize("path", DEMAND_NUMERIC_PATHS)
def test_demand_rejects_boolean_values_before_numeric_equality(path):
    value = expected_bootstrap(1)
    parent, key = field_parent(value, path)
    parent[key] = bool(parent[key])
    reject(policy.normalize_demand, value, suffix="TYPE_INVALID")


@pytest.mark.parametrize("field", tuple(selection()))
def test_demand_inputs_cannot_be_changed_without_recomputing_remaining_vector(field):
    value = expected_payload(selection())
    value["inputs"][field] += 1
    reject(policy.normalize_demand, value, suffix="DEMAND_MISMATCH")


def test_bootstrap_slot_and_payload_stage_are_bound_to_their_own_shapes():
    changed_slot = expected_bootstrap(1)
    changed_slot["inputs"]["slot"] = 2
    reject(policy.normalize_demand, changed_slot, suffix="DEMAND_MISMATCH")
    for prototype, new_stage in ((expected_bootstrap(1), "before_payload"),
                                 (expected_payload(selection()), "bootstrap")):
        prototype["stage"] = new_stage
        reject(policy.normalize_demand, prototype)
    for stage in ("", "before_copy", "BOOTSTRAP"):
        reject(policy.normalize_demand, {**expected_bootstrap(1), "stage": stage})
    reject(policy.normalize_demand, {**expected_bootstrap(1), "profile": "q1"},
           suffix="DEMAND_MISMATCH")


@pytest.mark.parametrize("kind", ["bootstrap", "payload"])
def test_every_demand_dictionary_rejects_missing_and_extra_keys(kind):
    original = expected_bootstrap(1) if kind == "bootstrap" else expected_payload(selection())
    for group in (None, "inputs", "known_minimum", "capacity_reserve", "read_headroom"):
        prototype = original if group is None else original[group]
        for key in prototype:
            value = deepcopy(original)
            parent = value if group is None else value[group]
            parent.pop(key)
            reject(policy.normalize_demand, value, suffix="FIELDS_INVALID")
        value = deepcopy(original)
        parent = value if group is None else value[group]
        parent["refund"] = 0
        reject(policy.normalize_demand, value, suffix="FIELDS_INVALID")


@pytest.mark.parametrize("name", ["normalize_rp1", "payload_demand", "normalize_demand"])
@pytest.mark.parametrize("invalid", [None, [], (), "replica-rp1", True, MappingProxyType({})])
def test_dictionary_apis_require_exact_builtin_dict(name, invalid):
    reject(getattr(policy, name), invalid, suffix="TYPE_INVALID")


@pytest.mark.parametrize("kind", ["rp1", "selection", "demand"])
@pytest.mark.parametrize("shape", ["outer", "outer_key", "nested", "nested_key", "integer", "text"])
def test_hostile_dictionary_keys_containers_and_values_do_not_execute_hooks(kind, shape):
    calls, armed, Text, Integer, Dictionary, _Tuple = hostile_types()
    if kind == "rp1":
        value, function, group, number = deepcopy(RP1), policy.normalize_rp1, "producer", "max_read_bytes"
    elif kind == "selection":
        value, function, group, number = selection(), policy.payload_demand, None, "package_bytes"
    else:
        value, function, group, number = expected_payload(selection()), policy.normalize_demand, "inputs", "package_bytes"
    if shape == "outer":
        value = Dictionary(value)
    elif shape == "outer_key":
        key = next(iter(value))
        original = value.pop(key)
        value[Text(key)] = original
    elif shape == "nested":
        if group is None:
            value[number] = Dictionary({})
        else:
            value[group] = Dictionary(value[group])
    elif shape == "nested_key":
        parent = value if group is None else value[group]
        original = parent.pop(number)
        parent[Text(number)] = original
    elif shape == "integer":
        parent = value if group is None else value[group]
        parent[number] = Integer(parent[number])
    elif kind == "selection":
        value[number] = Text("257")
    else:
        value["profile"] = Text("replica-rp1")
    armed[0] = True
    reject(function, value, suffix="TYPE_INVALID")
    assert calls == []


@pytest.mark.parametrize("shape", ["mode", "slot", "bootstrap_slot", "occupancy", "occupancy_member"])
def test_hostile_scalar_and_tuple_types_are_rejected_before_callbacks(shape):
    calls, armed, Text, Integer, _Dictionary, Tuple = hostile_types()
    function, value = {
        "mode": (policy.runtime_limits, Text("producer")),
        "slot": (policy.slot_reservation, Integer(1)),
        "bootstrap_slot": (policy.bootstrap_demand, Integer(1)),
        "occupancy": (policy.allocation, Tuple((1,))),
        "occupancy_member": (policy.allocation, (Integer(1),)),
    }[shape]
    armed[0] = True
    reject(function, value, suffix="TYPE_INVALID")
    assert calls == []


def test_all_policy_apis_are_pure_with_no_io_clock_sql_or_network(monkeypatch):
    expected = expected_payload(selection())

    def forbidden(*_args, **_kwargs):
        pytest.fail("Pure RP1 policy attempted I/O or time-based authorization")

    with monkeypatch.context() as patch:
        for target, name in ((builtins, "open"), (io, "open"), (os, "open"),
                             (os, "stat"), (os, "scandir"), (os, "mkdir"),
                             (Path, "read_bytes"), (Path, "write_bytes"),
                             (sqlite3, "connect"), (socket, "socket"),
                             (time, "monotonic"), (time, "monotonic_ns"),
                             (time, "time"), (time, "perf_counter")):
            patch.setattr(target, name, forbidden)
        assert policy.rp1_declaration() == policy.normalize_rp1(deepcopy(RP1)) == RP1
        assert policy.runtime_limits("producer") == PRODUCER
        assert policy.runtime_limits("readonly") == READONLY
        for slot in (1, 2):
            assert policy.slot_reservation(slot) == {"profile": "replica-rp1", "slot": slot, **SLOT}
            assert policy.bootstrap_demand(slot) == expected_bootstrap(slot)
            assert policy.normalize_demand(expected_bootstrap(slot)) == expected_bootstrap(slot)
        assert policy.allocation((1,))["reserved"] == SLOT
        assert policy.payload_demand(selection()) == policy.normalize_demand(expected) == expected
