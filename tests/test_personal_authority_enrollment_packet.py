"""r883 candidate: actual ordinary Windows packet paths and local fault seams.

Root alone admits/runs this file after candidate application. Actual temporary
profiles use the complete canonical fixture and modules. Consumer faults,
omitted-retirement and callback replay are explicit local models, not natural
id reuse, C/OOM/native failure, active graph or source_closed/write authority.
"""
from collections import Counter
from dataclasses import FrozenInstanceError, fields, replace
import gc
import os
from pathlib import Path
import tempfile
import weakref

import pytest

from tests.test_personal_authority_owned_ex_group import (
    actual_prefix_env, original_bytes, raw_database_node, tree, RESULT_FIELDS,
)
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_contract as contract
from reno import personal_authority_enrollment_custody as custody
from reno import personal_authority_enrollment_packet as packet
from reno import personal_authority_enrollment_physical as physical
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_owned_ex_group as groups
from reno import personal_authority_replica_operation as operation
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r883 actual Windows enrollment packet")

PAYLOAD_FIELDS = (
    "first_bytes", "second_bytes", "namespace_reservation_bytes", "root_seal_bytes",
    "header_bytes", "binding_bytes", "prefix_bytes", "origin_intent_bytes",
    "local_contract_bytes", "init_seal_bytes", "genesis_grant_bytes",
)
ORIGINAL_CAPTURE_SLOTS = (
    "_seal", "_group", "_operation", "_parent", "_effective", "_identity", "_ordinal",
    "_phase", "_state", "_guard", "_sources", "_source_reads", "_first", "_first_tb",
    "_completed", "_finish_attempted", "_receipt", "_pins", "_invocation_pin", "_source_pins",
    "_budget", "_restricted", "_read_budget", "_node_parent", "_support_pins", "_image_pin",
)


@pytest.fixture(autouse=True)
def isolated_packet_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is custody._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is custody._CURRENT.get() is None


def options(env):
    return {**env["args"], **env["expected"]}


def publish(env):
    return enrollment.publish_prefix_owned_ex(env["profile"], **options(env))


def packet_error(call, suffix):
    with pytest.raises(packet.EnrollmentPacketError) as caught:
        call()
    assert caught.value.code == "ENROLLMENT_PACKET_" + suffix
    return caught.value


def same_error(call, expected):
    with pytest.raises(type(expected)) as caught:
        call()
    assert caught.value is expected


def no_action(*_args, **_kwargs):
    pytest.fail("packet qualification, retry or unrelated provider unexpectedly reached")


def copy_slots(value):
    copied = object.__new__(type(value))
    for name in type(value).__slots__:
        if name != "__weakref__" and hasattr(value, name):
            object.__setattr__(copied, name, object.__getattribute__(value, name))
    return copied


def remember_staged(monkeypatch):
    seen, original = [], packet._stage_original_bytes

    def staged(carrier, **kwargs):
        value = original(carrier, **kwargs)
        assert value is None
        seen.append((carrier, packet._slot_for_carrier(carrier)))
        return value

    monkeypatch.setattr(packet, "_stage_original_bytes", staged)
    return seen


def retired(seen):
    assert seen
    for carrier, slot in seen:
        assert id(carrier) not in packet._PACKETS
        assert slot._retired is True
        assert slot._payload is slot._payload_pin is slot._result is slot._result_pin is None
        assert slot._context_pin is None and slot._capture_refs == ()


def test_actual_publish_and_cold_verify_keep_eleven_readback_bytes_and_two_weak_captures(actual_prefix_env, monkeypatch):
    env, observations, counts, loaded, readback = actual_prefix_env, [], Counter(), {}, {}
    before = raw_database_node(env)
    real_load, real_prefix, real_read = enrollment._load_originals, enrollment._read_prefix, enrollment._read
    real_stage, real_consume = packet._stage_original_bytes, packet._consume_locked
    real_retire = packet._retire_for_carrier

    def load(*args, **kwargs):
        value = real_load(*args, **kwargs)
        loaded["originals"] = value[1]
        return value

    def prefix(*args, **kwargs):
        value = real_prefix(*args, **kwargs)
        readback["binding"], readback["content"] = value[1], value[2]
        return value

    def read(*args, **kwargs):
        counts["enrollment_read"] += 1
        return real_read(*args, **kwargs)

    def stage(carrier, *, originals, observed_binding, observed_content):
        assert originals is loaded["originals"]
        assert observed_binding is readback["binding"] and observed_content is readback["content"]
        assert real_stage(carrier, originals=originals, observed_binding=observed_binding,
                          observed_content=observed_content) is None
        slot = packet._slot_for_carrier(carrier)
        expected = (*originals, observed_binding, *(getattr(observed_content, name)
                    for name in PAYLOAD_FIELDS[6:]))
        assert tuple(field.name for field in fields(slot._payload)) == PAYLOAD_FIELDS
        assert all(getattr(slot._payload, name) is raw for name, raw in zip(PAYLOAD_FIELDS, expected))
        assert all(type(raw) is bytes for raw in expected) and len(expected) == 11
        assert sum(map(len, expected)) <= contract.MAX_INPUT_BYTES + 6 * contract.MAX_RECORD_BYTES
        assert not any(hasattr(slot._payload, name) for name in ("owner", "group", "source_closed", "write_authority"))
        assert weakref.ref(slot)() is slot and not hasattr(slot, "__dict__")
        with pytest.raises(FrozenInstanceError):
            slot._payload.first_bytes = b"replacement"
        observations.append({"carrier": carrier, "slot": slot, "payload": slot._payload,
                             "expected": expected, "captures": tuple(carrier._captures)})

    def consume(slot, *, source_consumer, source_precondition, result):
        selected = observations[-1]
        carrier = selected["carrier"]
        assert selected["slot"] is slot and slot._result is result is carrier._result
        assert lifecycle._CURRENT.get() is carrier._guard and carrier._guard._active is True
        assert custody._CURRENT.get() is None and carrier._group._phase == "active"
        assert source_consumer is carrier._consumer and source_consumer._attempted is True
        assert source_consumer._receipt is carrier._receipt is source_precondition
        assert source_precondition.source_closed is source_precondition.write_authority is False
        assert source_precondition.acquisition_coverage_proven is False
        assert len(slot._capture_refs) == len(selected["captures"]) == 2
        for ordinal, (reference, actual) in enumerate(zip(slot._capture_refs, selected["captures"])):
            assert type(reference) is weakref.ReferenceType and reference() is actual
            assert weakref.ref(actual)() is actual and type(actual) is groups.BorrowedCaptureDeadlineLease
            assert not hasattr(actual, "__dict__") and actual._ordinal == ordinal
            assert actual._receipt is source_precondition.capture_receipts[ordinal]
            assert carrier._capture_returns[ordinal] is actual
        returned = real_consume(slot, source_consumer=source_consumer,
                                source_precondition=source_precondition, result=result)
        assert returned is selected["payload"] and slot._consume_attempted is True
        packet_error(lambda: real_consume(slot, source_consumer=source_consumer,
                     source_precondition=source_precondition, result=result), "CLOSED")
        counts["consume"] += 1
        return returned

    def retire(carrier, *, primary=None):
        assert primary is None and lifecycle._CURRENT.get() is custody._CURRENT.get() is None
        assert carrier._group._consume_attempted is True
        value = real_retire(carrier, primary=primary)
        counts["retire"] += 1
        return value

    for name, value in (("_load_originals", load), ("_read_prefix", prefix), ("_read", read)):
        monkeypatch.setattr(enrollment, name, value)
    for name, value in (("_stage_original_bytes", stage), ("_consume_locked", consume), ("_retire_for_carrier", retire)):
        monkeypatch.setattr(packet, name, value)
    published = publish(env)
    assert counts["enrollment_read"] == 17
    written = tree(env["root"])
    verified = enrollment.verify_prefix_owned_ex(env["profile"], **options(env),
        expected_binding_sha256=published.binding_sha256, expected_prefix_sha256=published.prefix_sha256)
    assert verified == published and type(published) is enrollment.AuthorityPrefixFiles
    assert set(published.__slots__) == RESULT_FIELDS and published.scope == enrollment.SCOPE
    assert counts == Counter(enrollment_read=28, consume=2, retire=2) and len(observations) == 2
    assert observations[0]["carrier"] is not observations[1]["carrier"]
    assert len({id(capture) for observation in observations for capture in observation["captures"]}) == 4
    for observation in observations:
        assert tuple(getattr(observation["payload"], name) for name in PAYLOAD_FIELDS[:5]) == env["originals"]
    retired([(observation["carrier"], observation["slot"]) for observation in observations])
    assert tree(env["root"]) == written and original_bytes(env) == env["originals"]
    assert raw_database_node(env) == before


def test_default_owned_and_borrowed_paths_do_not_register_packets(actual_prefix_env, monkeypatch):
    env = actual_prefix_env
    for name in ("_stage_original_bytes", "_bind_verified_result", "_slot_for_carrier", "_consume_locked", "_retire_for_carrier"):
        monkeypatch.setattr(packet, name, no_action)
    published = enrollment.publish_prefix(env["profile"], **options(env))
    hashes = {"expected_binding_sha256": published.binding_sha256, "expected_prefix_sha256": published.prefix_sha256}
    verified = enrollment.verify_prefix(env["profile"], **options(env), **hashes)
    with lifecycle.exclusive(env["profile"], timeout=0):
        borrowed = enrollment.verify_prefix_locked(env["profile"], **options(env), **hashes,
            deadline_token=deadline.Namespace3Deadline(deadline._now() + 30.0))
    assert published == verified == borrowed and original_bytes(env) == env["originals"]


def test_fresh_catalog_binds_appended_capture_weakref_and_packet_classes(monkeypatch):
    cls = groups.BorrowedCaptureDeadlineLease
    assert cls.__slots__ == ORIGINAL_CAPTURE_SLOTS + ("__weakref__",)
    assert "__dict__" not in cls.__dict__ and "__weakref__" in packet._PacketSlot.__slots__
    assert enrollment.packet is packet and packet in groups._catalog_modules_original()
    for selected in (cls, packet._PacketSlot, packet._PacketBytes):
        rows = [row for row in operation._CALLEE_PINS[1] if row[0] is selected]
        assert len(rows) == 1
        own = next(values for base, values in rows[0][2] if base is selected)
        mapping = dict(own)
        assert mapping["__slots__"] is selected.__dict__["__slots__"]
        if selected is not packet._PacketBytes:
            assert mapping["__weakref__"] is selected.__dict__["__weakref__"]
    assert operation._verify_catalog_original(operation._CALLEE_PINS) is None
    for name, replacement in (("__weakref__", property(lambda _self: None)), ("__slots__", ORIGINAL_CAPTURE_SLOTS)):
        with monkeypatch.context() as patch:
            patch.setattr(cls, name, replacement)
            with pytest.raises(operation.AuthorityReplicaOperationError) as caught:
                operation._verify_catalog_original(operation._CALLEE_PINS)
            assert caught.value.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
        assert operation._verify_catalog_original(operation._CALLEE_PINS) is None


def test_foreign_and_copied_self_cannot_consume_registered_slot(actual_prefix_env, monkeypatch):
    seen = remember_staged(monkeypatch)
    real_consume = packet._consume_locked

    def consume(slot, **kwargs):
        carrier = slot._carrier_ref()
        packet_error(lambda: packet._PacketSlot(), "INVALID_ARGUMENT")
        with pytest.raises(packet.EnrollmentPacketError) as caught:
            class NotOriginal(packet._PacketSlot):
                pass
        assert caught.value.code == "ENROLLMENT_PACKET_INVALID_ARGUMENT"
        packet_error(lambda: packet._slot_for_carrier(object()), "INVALID_ARGUMENT")
        packet_error(lambda: packet._slot_for_carrier(copy_slots(carrier)), "INVALID_RESULT")
        for foreign in (object(), copy_slots(slot)):
            packet_error(lambda: real_consume(foreign, **kwargs), "INVALID_ARGUMENT")
            assert slot._consume_attempted is False
        packet_error(lambda: packet._bind_verified_result(carrier, result=kwargs["result"]), "CLOSED")
        returned = real_consume(slot, **kwargs)
        packet_error(lambda: real_consume(slot, **kwargs), "CLOSED")
        return returned

    monkeypatch.setattr(packet, "_consume_locked", consume)
    publish(actual_prefix_env)
    retired(seen)


@pytest.mark.parametrize("damage", ["equal-parent", "equal-effective", "copied-payload", "equal-new-bytes",
    "swapped-captures", "foreign-capture", "copied-consumer", "copied-receipt", "copied-result"],
    ids=["equal-parent", "equal-effective", "copied-payload", "equal-new-bytes", "swapped-captures",
         "foreign-capture", "copied-consumer", "copied-receipt", "copied-result"])
def test_identified_slot_consumes_attempt_before_rejecting_replaced_context(actual_prefix_env, monkeypatch, damage):
    seen, failures = remember_staged(monkeypatch), []
    real_consume = packet._consume_locked

    def consume(slot, **kwargs):
        foreign_capture = None
        if damage in ("equal-parent", "equal-effective"):
            name = "_parent" if damage == "equal-parent" else "_effective"
            original = getattr(slot, name)
            replacement = deadline.Namespace3Deadline(original.absolute_monotonic)
            assert replacement == original and replacement is not original
            setattr(slot, name, replacement)
        elif damage == "copied-payload":
            slot._payload = copy_slots(slot._payload)
        elif damage == "equal-new-bytes":
            original = slot._payload.first_bytes
            replacement = bytes(memoryview(original))
            assert replacement == original and replacement is not original
            object.__setattr__(slot._payload, "first_bytes", replacement)
        elif damage == "swapped-captures":
            slot._capture_refs = tuple(reversed(slot._capture_refs))
        elif damage == "foreign-capture":
            foreign_capture = object.__new__(groups.BorrowedCaptureDeadlineLease)
            slot._capture_refs = (weakref.ref(foreign_capture), slot._capture_refs[1])
        elif damage == "copied-consumer":
            kwargs["source_consumer"] = copy_slots(kwargs["source_consumer"])
        elif damage == "copied-receipt":
            kwargs["source_precondition"] = replace(kwargs["source_precondition"])
        else:
            kwargs["result"] = replace(kwargs["result"])
        first = packet_error(lambda: real_consume(slot, **kwargs), "INVALID_RESULT")
        assert slot._consume_attempted is True
        packet_error(lambda: real_consume(slot, **kwargs), "CLOSED")
        failures.append(first)
        raise first

    monkeypatch.setattr(packet, "_consume_locked", consume)
    with pytest.raises(packet.EnrollmentPacketError) as caught:
        publish(actual_prefix_env)
    assert len(failures) == 1 and caught.value is failures[0]
    retired(seen)


def test_stage_validation_failure_keeps_attempt_until_finally_retirement(actual_prefix_env, monkeypatch):
    real_stage, seen, failures = packet._stage_original_bytes, [], []

    def stage(carrier, **kwargs):
        damaged = {**kwargs, "observed_binding": b""}
        first = packet_error(lambda: real_stage(carrier, **damaged), "LIMIT")
        slot = packet._PACKETS[id(carrier)][1]
        seen.append((carrier, slot))
        assert slot._stage_attempted is True and slot._stage_succeeded is False
        packet_error(lambda: real_stage(carrier, **kwargs), "CLOSED")
        key, original_row = id(carrier), packet._PACKETS[id(carrier)]
        try:
            # An explicitly damaged present row is still an existing claim;
            # dict.get(..., None) must not turn it into a fresh registration.
            packet._PACKETS[key] = None
            packet_error(lambda: real_stage(carrier, **kwargs), "CLOSED")
            assert packet._PACKETS[key] is None
        finally:
            packet._PACKETS[key] = original_row
        failures.append(first)
        raise first

    monkeypatch.setattr(packet, "_stage_original_bytes", stage)
    monkeypatch.setattr(packet, "_consume_locked", no_action)
    with pytest.raises(packet.EnrollmentPacketError) as caught:
        publish(actual_prefix_env)
    assert len(failures) == 1 and caught.value is failures[0]
    retired(seen)


def test_actual_carrier_registry_damage_is_rejected_after_packet_attempt(actual_prefix_env, monkeypatch):
    seen, failures = remember_staged(monkeypatch), []
    real_consume = packet._consume_locked

    def consume(slot, **kwargs):
        carrier = slot._carrier_ref()
        key = id(carrier._budget)
        original_row = physical._BUDGET_CARRIERS[key]
        try:
            del physical._BUDGET_CARRIERS[key]
            with pytest.raises(physical.EnrollmentSourceError) as caught:
                real_consume(slot, **kwargs)
            first = caught.value
            assert first.code == "ENROLLMENT_SOURCE_INVALID_ARGUMENT"
            assert slot._consume_attempted is True
            packet_error(lambda: real_consume(slot, **kwargs), "CLOSED")
        finally:
            # Restore only this deliberate metadata corruption before real
            # outer-owner cleanup; it cannot restore the consumed attempt.
            physical._BUDGET_CARRIERS[key] = original_row
        failures.append(first)
        raise first

    monkeypatch.setattr(packet, "_consume_locked", consume)
    with pytest.raises(physical.EnrollmentSourceError) as caught:
        publish(actual_prefix_env)
    assert len(failures) == 1 and caught.value is failures[0]
    retired(seen)


def test_consumer_revalidates_content_even_when_local_payload_pin_is_rebuilt(actual_prefix_env, monkeypatch):
    seen, failures = remember_staged(monkeypatch), []
    real_consume = packet._consume_locked

    def consume(slot, **kwargs):
        object.__setattr__(slot._payload, "first_bytes", b"{}")
        # Explicit forged in-memory metadata model: type, bounds and local
        # identity pins alone must not replace the existing content contract.
        slot._payload_pin = (slot._payload, *(getattr(slot._payload, name) for name in PAYLOAD_FIELDS))
        with pytest.raises(contract.EnrollmentContractError) as caught:
            real_consume(slot, **kwargs)
        first = caught.value
        assert first.code == "ENROLLMENT_CONTRACT_ORIGIN_BINDING_INVALID"
        assert slot._consume_attempted is True
        packet_error(lambda: real_consume(slot, **kwargs), "CLOSED")
        failures.append(first)
        raise first

    monkeypatch.setattr(packet, "_consume_locked", consume)
    with pytest.raises(contract.EnrollmentContractError) as caught:
        publish(actual_prefix_env)
    assert len(failures) == 1 and caught.value is failures[0]
    retired(seen)


@pytest.mark.parametrize("boundary", ["stage", "source-exit", "result", "finish", "issue", "consume", "ex-exit", "group"],
                         ids=["stage", "source-exit", "result", "finish", "issue", "consume", "ex-exit", "group"])
def test_each_later_failure_retires_packet_without_duplicate_source_finish(actual_prefix_env, monkeypatch, boundary):
    first, counts = RuntimeError("r883 controlled boundary delivery loss"), Counter()
    seen = remember_staged(monkeypatch)
    real_finish, real_consume = custody._finish_journal, packet._consume_locked

    def finish(*args, **kwargs):
        counts["finish"] += 1
        return real_finish(*args, **kwargs)

    def consume(*args, **kwargs):
        counts["consume"] += 1
        return real_consume(*args, **kwargs)

    monkeypatch.setattr(custody, "_finish_journal", finish)
    monkeypatch.setattr(packet, "_consume_locked", consume)
    owner, name = {
        "stage": (packet, "_stage_original_bytes"), "source-exit": (physical._InnerTerminalScope, "__exit__"),
        "result": (enrollment, "_result"), "finish": (custody, "_finish_journal"),
        "issue": (physical, "_issue_borrowed_source_precondition"), "consume": (packet, "_consume_locked"),
        "ex-exit": (enrollment._ExclusiveScope, "__exit__"), "group": (groups, "_consume_owned_ex_group"),
    }[boundary]
    original = getattr(owner, name)

    def lose_delivery(*args, **kwargs):
        original(*args, **kwargs)
        counts["fault"] += 1
        raise first

    monkeypatch.setattr(owner, name, lose_delivery)
    same_error(lambda: publish(actual_prefix_env), first)
    assert counts["fault"] == counts["finish"] == 1
    assert counts["consume"] == int(boundary in ("consume", "ex-exit", "group"))
    assert len(seen) == 1
    assert seen[0][1]._consume_attempted is (boundary in ("consume", "ex-exit", "group"))
    retired(seen)


@pytest.mark.parametrize("kind,seam", [("runtime", "lineage"), ("memory", "clock"), ("exit", "receipt-getter"), ("interrupt", "clock")],
                         ids=["runtime-lineage", "memory-clock", "exit-receipt-getter", "interrupt-clock"])
def test_attempt_precedes_fallible_checks_and_first_survives_retire_secondary(actual_prefix_env, monkeypatch, kind, seam):
    first = {"runtime": RuntimeError, "memory": MemoryError, "exit": SystemExit, "interrupt": KeyboardInterrupt}[kind]("r883 first")
    secondary, counts = RuntimeError("r883 retire call-layer secondary"), Counter()
    seen = remember_staged(monkeypatch)
    real_consume, real_retire = packet._consume_locked, packet._retire_for_carrier

    def consume(slot, **kwargs):
        carrier = slot._carrier_ref()

        def fail(*_args, **_kwargs):
            assert slot._consume_attempted is True
            counts["fault"] += 1
            raise first

        with monkeypatch.context() as patch:
            if seam == "lineage":
                patch.setattr(physical, "_lineage", fail)
            elif seam == "receipt-getter":
                class FailedReceiptGetter:
                    def __get__(self, *_args):
                        return fail()
                patch.setattr(custody, "_JOURNAL_RECEIPT", FailedReceiptGetter())
            else:
                patch.setattr(carrier._budget, "clock_only", fail)
            same_error(lambda: real_consume(slot, **kwargs), first)
            assert slot._consume_attempted is True
            packet_error(lambda: real_consume(slot, **kwargs), "CLOSED")
        raise first

    def retire(carrier, *, primary=None):
        assert primary is first
        real_retire(carrier, primary=primary)
        counts["retire"] += 1
        raise secondary

    monkeypatch.setattr(packet, "_consume_locked", consume)
    monkeypatch.setattr(packet, "_retire_for_carrier", retire)
    same_error(lambda: publish(actual_prefix_env), first)
    assert counts == Counter(fault=1, retire=1)
    retired(seen)


def test_normal_path_retire_failure_blocks_public_delivery(actual_prefix_env, monkeypatch):
    first, seen, real_retire = RuntimeError("r883 retire delivery failure"), remember_staged(monkeypatch), packet._retire_for_carrier

    def retire(carrier, *, primary=None):
        assert primary is None
        real_retire(carrier, primary=primary)
        raise first

    monkeypatch.setattr(packet, "_retire_for_carrier", retire)
    same_error(lambda: publish(actual_prefix_env), first)
    retired(seen)


@pytest.mark.parametrize("mode", ["normal-finally", "omitted-retire", "failed-consume"],
                         ids=["normal-finally", "omitted-retire", "failed-consume"])
def test_actual_closed_graph_can_collect_without_packet_registry_owner_root(actual_prefix_env, monkeypatch, mode):
    """Only explicit instance weakrefs; omission is a local cleanup-fault model."""
    def one_operation():
        references, held = {}, {}
        real_consume = packet._consume_locked
        first = RuntimeError("r883 failed consumer GC cycle")

        def consume(slot, **kwargs):
            carrier = slot._carrier_ref()
            held["group"], held["key"] = carrier._group, id(carrier)
            references.update(slot=weakref.ref(slot), carrier=weakref.ref(carrier), group=weakref.ref(carrier._group))
            for ordinal, capture in enumerate(carrier._captures):
                references["capture" + str(ordinal)] = weakref.ref(capture)
            # Here the argument is actually alive. After delivery it may be
            # retired immediately by refcount or later with a cyclic graph.
            assert packet._PACKETS[id(carrier)][1] is slot
            value = real_consume(slot, **kwargs)
            if mode == "failed-consume":
                raise first
            return value

        with monkeypatch.context() as patch:
            patch.setattr(packet, "_consume_locked", consume)
            if mode == "omitted-retire":
                # This deliberately omitted pure-memory call tests GC fallback,
                # not successful product retirement or native failure handling.
                patch.setattr(packet, "_retire_for_carrier", lambda carrier, *, primary=None: None)
            if mode == "failed-consume":
                same_error(lambda: publish(actual_prefix_env), first)
            else:
                publish(actual_prefix_env)
            key, actual_group = held.pop("key"), held.pop("group")
            if mode == "omitted-retire":
                if references["carrier"]() is None:
                    assert key not in packet._PACKETS
                else:
                    assert key in packet._PACKETS
            else:
                assert key not in packet._PACKETS
        # No first/traceback, carrier, slot or wrapper escapes this frame.
        return references, key, actual_group

    references, key, kept_group = one_operation()
    gc.collect()
    assert references["group"]() is kept_group
    assert references["capture0"]() is kept_group._captures[0]
    assert references["capture1"]() is kept_group._captures[1]
    del kept_group
    gc.collect()
    assert all(reference() is None for reference in references.values())
    assert key not in packet._PACKETS


def test_old_callback_and_foreign_reference_cannot_retire_new_claim_row(actual_prefix_env, monkeypatch):
    """Controlled claim/row replay on actual callback; not natural id reuse."""
    class Anchor:
        pass

    seen, real_consume = remember_staged(monkeypatch), packet._consume_locked

    def consume(slot, **kwargs):
        carrier = slot._carrier_ref()
        key, row = id(carrier), packet._PACKETS[id(carrier)]
        reference, callback = row[0], row[0].__callback__
        assert reference() is carrier and callable(callback)
        foreign = Anchor()
        callback(weakref.ref(foreign))
        assert packet._PACKETS[key] is row
        other = Anchor()
        new_row = (weakref.ref(other), object.__new__(packet._PacketSlot), object())
        try:
            packet._PACKETS[key] = new_row
            for triggering in (reference, weakref.ref(foreign)):
                callback(triggering)
                assert packet._PACKETS[key] is new_row
        finally:
            packet._PACKETS[key] = row
        return real_consume(slot, **kwargs)

    monkeypatch.setattr(packet, "_consume_locked", consume)
    publish(actual_prefix_env)
    retired(seen)
