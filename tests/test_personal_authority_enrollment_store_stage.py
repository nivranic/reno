"""r907 independent Store-stage native tests; Root alone admits and executes.

The hook calls the actual continuation factory before inserting its controlled
consumer. It borrows the real original EX and budget, never writes a positive
registry row, and never consumes the packet twice. Fault wrappers always call
the real native API. Product UNKNOWN and fixture-observed OS closure are kept
separate. This file does not assert a Store publisher or SQL writer exists.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
import os
from pathlib import Path
import tempfile

import pytest

from tests.test_personal_authority_owned_ex_group import actual_prefix_env, original_bytes, raw_database_node
from tests.test_personal_authority_enrollment_custody import NATIVE_INVENTORY
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_custody as source
from reno import personal_authority_enrollment_owner_dispatch as dispatch
from reno import personal_authority_enrollment_packet as packet
from reno import personal_authority_enrollment_store_custody as custody
from reno import personal_authority_enrollment_store_runtime as runtime
from reno import personal_authority_namespace3_io as native
from reno import personal_authority_owned_ex_group as groups
from reno import personal_authority_replica_operation as operation
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r907 actual Windows Store-stage native custody")
ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE = False


@pytest.fixture(autouse=True)
def isolated_store_stage_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first
    return caught.value


def stage_error(call, suffix):
    with pytest.raises(runtime.StoreStageError) as caught:
        call()
    assert caught.value.code == "ENROLLMENT_STORE_STAGE_" + suffix
    return caught.value


def custody_error(call, suffix):
    with pytest.raises(custody.StoreCustodyError) as caught:
        call()
    assert caught.value.code == "ENROLLMENT_STORE_CUSTODY_" + suffix
    return caught.value


def copy_slots(value):
    result = object.__new__(type(value))
    for name in type(value).__slots__:
        if name != "__weakref__" and hasattr(value, name):
            object.__setattr__(result, name, object.__getattribute__(value, name))
    return result


def no_authority(value):
    assert value.source_closed is value.write_authority is False
    assert not hasattr(value, "__dict__")
    assert not ({item.name for item in fields(value)} & {"handle", "pointer", "fd", "resource", "callback", "closer"})
    with pytest.raises(FrozenInstanceError):
        value.source_closed = True


def source_unchanged(selected):
    value = selected["continuation"]
    journal, snapshot = selected["source_journal"], selected["source_snapshot"]
    assert value._source_journal is journal and value._source_snapshot is snapshot
    assert source._JOURNAL_RECEIPT.__get__(journal, source._NativeOwnerJournal) is snapshot
    assert source._GROUP_JOURNALS[id(selected["group"])] is selected["source_group_row"]
    assert source._BUDGET_JOURNALS[id(selected["budget"])] is selected["source_budget_row"]
    assert source._CURRENT.get() is None and lifecycle._CURRENT.get() is selected["guard"]
    assert selected["group"]._phase == "active"
    assert snapshot.all_registered_native_closed is True and snapshot.unknown_count == 0
    assert snapshot.reset_state == "reset" and snapshot.first_present is False
    no_authority(snapshot)


def after_retirement(selected, *, debt=False):
    value, slot = selected["continuation"], selected["packet_slot"]
    assert slot._retired is True and slot._payload is slot._context_pin is None
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None
    if debt:
        assert value._phase == "retained_failed"
        assert value._pins is selected["original_pins"] and value._journal is selected["journal"]
    else:
        assert value._phase in ("retired_closed", "retired_uncompleted") and value._pins == ()
        assert all(getattr(value, name) is None for name in runtime._PIN_NAMES)
        assert value._journal is value._stage is value._receipt is None


def observe_factory(monkeypatch, consumer):
    """Call/return, then insert consumer before original EX retirement."""
    seen, counts = [], Counter()
    original_factory = runtime._new_prepare_continuation
    original_consume = packet._consume_locked
    original_retire = runtime._retire_continuation

    def consume(slot, **kwargs):
        counts["packet_consume"] += 1
        returned = original_consume(slot, **kwargs)
        assert returned is slot._payload and type(returned) is packet._PacketBytes
        return returned

    def retire(value, *, primary=None):
        counts["normal_retire"] += 1
        return original_retire(value, primary=primary)

    def factory(slot, **kwargs):
        counts["factory"] += 1
        value = original_factory(slot, **kwargs)
        assert value._slot is slot and value._payload is slot._payload
        assert value._payload is not None and slot._consume_attempted is True
        assert counts["packet_consume"] == counts["factory"] == 1
        selected = {"continuation": value, "packet_slot": slot, "factory_kwargs": kwargs,
                    "original_factory": original_factory, "original_retire": original_retire,
                    "original_pins": value._pins, "source_journal": value._source_journal,
                    "source_snapshot": value._source_snapshot, "group": value._group,
                    "budget": value._budget, "guard": value._guard,
                    "source_group_row": source._GROUP_JOURNALS[id(value._group)],
                    "source_budget_row": source._BUDGET_JOURNALS[id(value._budget)],
                    "actual_owners": [], "native_calls": [], "os_closed": [],
                    "fixture_closed": [], "counts": Counter(), "scope": None, "journal": None}
        seen.append(selected)
        source_unchanged(selected)
        try:
            consumer(value, selected)
        except BaseException as first:
            selected["hook_first"] = first
            counts["exception_retire"] += 1
            try:
                original_retire(value, primary=first)
            except BaseException as cleanup:
                if cleanup is not first:
                    native._note(first)
            raise
        finally:
            # Only independently confirmed acquisitions whose product close
            # has never been attempted may be cleaned by the fixture. Never
            # retry a numeric resource after UNKNOWN product delivery.
            for owner, native_slot, number in reversed(selected["actual_owners"]):
                if not native_slot._close_attempted:
                    raw = selected["original_close"](number)
                    assert type(raw) is int and -(1 << 31) <= raw < (1 << 31) and raw != 0
                    selected["fixture_closed"].append(number)
        return value

    monkeypatch.setattr(packet, "_consume_locked", consume)
    monkeypatch.setattr(runtime, "_new_prepare_continuation", factory)
    monkeypatch.setattr(runtime, "_retire_continuation", retire)
    return seen, counts


def publish(env):
    return enrollment.publish_prefix_owned_ex(env["profile"], **env["args"], **env["expected"])


def acquire_two(value, selected):
    assert custody._CURRENT.get() is selected["journal"]
    for ordinal in range(2):
        owner = native._open_parent_raw(value._profile.database.parent, write=False, budget=value._budget)
        native_slot = custody._find_owner_slot(owner)
        assert type(native_slot) is custody._StoreNativeSlot
        assert native_slot._journal is selected["journal"] and native_slot._ordinal == ordinal
        assert native_slot._role == "parent" and native_slot._kind == custody._HANDLE
        assert native_slot._known is native_slot._representation_confirmed is True
        number = owner.value
        assert type(number) is int and number == native_slot._resource
        assert id(owner) not in source._OWNER_SLOTS
        assert dispatch._find_owner_slot(owner) is native_slot
        selected["actual_owners"].append((owner, native_slot, number))
    assert len(selected["actual_owners"]) == 2
    assert len({number for _owner, _slot, number in selected["actual_owners"]}) == 2
    selected["original_history"] = custody._history(selected["journal"])
    assert len(selected["original_history"]) == 2
    assert all(slot is actual for (_owner, slot, _number), actual in
               zip(selected["actual_owners"], selected["original_history"]))


@contextmanager
def observed_stage(value, selected, monkeypatch, *, close_fault=None, reset_fault=None, seal_fault=None, clock_fault=None):
    """Only fixed native calls and existing cleanup seams; no callback API."""
    original_close, original_reset = native._kernel.CloseHandle, custody._reset
    original_seal, original_clock = custody._seal, enrollment._Budget.clock_only
    selected["original_close"] = original_close

    def close(number):
        actual = next((item for item in selected["actual_owners"] if item[2] == number), None)
        if actual is not None:
            selected["native_calls"].append(number)
        raw = original_close(number)
        if actual is not None:
            assert type(raw) is int and -(1 << 31) <= raw < (1 << 31) and raw != 0
            selected["os_closed"].append(number)
            if close_fault is not None and len(selected["native_calls"]) == 1:
                raise close_fault
        return raw

    def reset(journal):
        if journal is selected["journal"]:
            selected["counts"]["reset_call"] += 1
        returned = original_reset(journal)
        if journal is selected["journal"]:
            selected["counts"]["actual_reset"] += 1
            assert returned is None and custody._CURRENT.get() is None
            if reset_fault is not None:
                raise reset_fault
        return returned

    def seal(journal, **kwargs):
        if journal is selected["journal"]:
            selected["counts"]["seal_call"] += 1
        returned = original_seal(journal, **kwargs)
        if journal is selected["journal"]:
            selected["counts"]["actual_seal"] += 1
            selected["snapshot"] = returned
            assert returned is custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
            if seal_fault is not None:
                raise seal_fault
        return returned

    def clock(budget):
        journal = selected["journal"]
        if (clock_fault is not None and budget is selected["budget"] and journal is not None
                and journal._cleanup_attempted and journal._reset_attempted and journal._seal_attempted):
            selected["counts"]["final_clock_fault"] += 1
            raise clock_fault
        return original_clock(budget)

    with monkeypatch.context() as patch:
        patch.setattr(native._kernel, "CloseHandle", close)
        patch.setattr(custody, "_reset", reset)
        patch.setattr(custody, "_seal", seal)
        if clock_fault is not None:
            patch.setattr(enrollment._Budget, "clock_only", clock)
        scope = runtime._store_prepare_scope(value)
        selected["scope"] = scope
        with scope as journal:
            selected["journal"] = journal
            assert type(journal) is custody._StorePrepareJournal and journal._budget is selected["budget"]
            assert journal is value._journal and journal._continuation is value
            assert journal._scope is value._stage_scope and journal._scope is not value._source_scope
            source_unchanged(selected)
            yield journal


def cleaned(selected, *, unknown=0, reset_state="reset", first=False, count=2):
    journal, snapshot = selected["journal"], selected["snapshot"]
    assert journal._cleanup_attempted is journal._reset_attempted is journal._seal_attempted is True
    assert custody._CURRENT.get() is None
    assert selected["counts"]["reset_call"] == selected["counts"]["actual_reset"] == 1
    assert selected["counts"]["seal_call"] == selected["counts"]["actual_seal"] == 1
    assert snapshot.registered_count == len(snapshot.facts) == count
    assert snapshot.unknown_count == unknown and snapshot.reset_state == reset_state
    assert snapshot.first_present is first
    no_authority(snapshot)
    if count:
        expected = [number for _owner, _slot, number in reversed(selected["actual_owners"])]
        assert selected["native_calls"] == selected["os_closed"] == expected
        assert selected["fixture_closed"] == []
        assert all(fact is slot._receipt for fact, (_owner, slot, _number) in
                   zip(snapshot.facts, selected["actual_owners"]))
        assert all(slot._close_attempted is True for _owner, slot, _number in selected["actual_owners"])
        assert sum(fact.native_state == "UNKNOWN" for fact in snapshot.facts) == unknown
    source_unchanged(selected)


def no_stage_receipt(selected):
    scope, value = selected["scope"], selected["continuation"]
    assert scope._receipt is value._receipt is None
    assert id(scope) not in runtime._CLOSE_RECEIPTS
    with pytest.raises(runtime.StoreStageError):
        runtime._stage_close_receipt(scope)


def test_fixed_source_store_dispatch_catalog():
    assert native._custody is dispatch and enrollment.custody is source and enrollment.store_runtime is runtime
    assert dispatch.source is source and dispatch.store is custody
    assert dispatch._signed32 is source._signed32
    assert dispatch._HANDLE is source._HANDLE and dispatch._LOCALALLOC is source._LOCALALLOC
    assert dispatch._POINTER_MAX is source._POINTER_MAX
    modules = groups._catalog_modules_original()
    for expected in (source, custody, runtime, dispatch, native, enrollment, packet):
        assert sum(value is expected for value in modules) == 1
    assert len(modules) == len(operation._OWNED_EX_FIXED_MODULES)
    assert all(a is b for a, b in zip(modules, operation._OWNED_EX_FIXED_MODULES))
    extras = groups._catalog_extra_original()
    for module, names in ((source, ("_CURRENT", "_GROUP_JOURNALS", "_BUDGET_JOURNALS", "_OWNER_SLOTS")),
            (custody, ("_CURRENT", "_CONTEXT_VARIABLE", "_REGISTRATION_HISTORY", "_JOURNAL_RECEIPT", "_SLOT_RECEIPT")),
            (runtime, ("_CONTINUATIONS", "_SCOPE_ROWS", "_STAGES_BY_CONTINUATION", "_CLOSE_RECEIPTS")),
            (dispatch, ("source", "store", "_signed32"))):
        actual = [selection for selected, selection in extras if selected is module]
        assert len(actual) == 1 and set(names) <= set(actual[0])
    expected = tuple(getattr(getattr(native, dll), name) for dll, names in NATIVE_INVENTORY for name in names)
    assert len(expected) == 24
    actual = groups._native_custody_functions_original()
    assert len(actual) == 24 and all(a is b for a, b in zip(actual, expected))
    for function in expected:
        pins = [row for row in operation._NATIVE_SIGNATURES if row[0] is function]
        assert len(pins) == 1
        assert pins[0][1] is function.argtypes and pins[0][2] == tuple(function.argtypes)
        assert pins[0][3] is function.restype and pins[0][4] is function.errcheck
    with pytest.raises(source.EnrollmentCustodyError) as caught:
        dispatch._slot(object())
    assert caught.value.code == "ENROLLMENT_CUSTODY_INVALID_ARGUMENT"
    assert custody._CURRENT is custody._CONTEXT_VARIABLE and source._CURRENT is source._CONTEXT_VARIABLE
    assert custody._CURRENT is not source._CURRENT
    assert ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE is False


def test_real_stage_two_owners_lifo_and_registered_receipt(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observed_stage(value, selected, monkeypatch):
            acquire_two(value, selected)
        cleaned(selected)
        assert selected["snapshot"].all_registered_native_closed is True
        receipt = runtime._stage_close_receipt(selected["scope"])
        selected["receipt"] = receipt
        assert type(receipt) is runtime._StorePrepareCloseReceipt
        assert receipt is selected["scope"]._receipt is value._receipt
        assert runtime._CLOSE_RECEIPTS[id(selected["scope"])][2] is receipt
        assert receipt.native_snapshot is selected["snapshot"] and receipt.registered_native_count == 2
        assert receipt.source_snapshot_unchanged is receipt.context_reset is receipt.journal_sealed is True
        no_authority(receipt)
    seen, counts = observe_factory(monkeypatch, consumer)
    before = raw_database_node(actual_prefix_env)
    publish(actual_prefix_env)
    assert len(seen) == 1 and counts == Counter(factory=1, packet_consume=1, normal_retire=1)
    after_retirement(seen[0])
    assert original_bytes(actual_prefix_env) == actual_prefix_env["originals"]
    assert raw_database_node(actual_prefix_env) == before


@pytest.mark.parametrize("damage", ["raws-equal-new-tuple", "owner-value"], ids=["raws-equal-new-tuple", "owner-value"])
def test_tampered_business_or_owner_still_cleans(actual_prefix_env, monkeypatch, damage):
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch):
                acquire_two(value, selected)
                if damage == "raws-equal-new-tuple":
                    original = value._raws
                    value._raws = tuple(list(original))
                    assert value._raws == original and value._raws is not original
                    stage_error(lambda: runtime._continuation(value, live=True), "INVALID_ARGUMENT")
                else:
                    owner, native_slot, number = selected["actual_owners"][-1]
                    owner.value = 0
                    assert owner.value != number
                    custody_error(lambda: custody._owner_slot(owner, native_slot), "INVALID_ARGUMENT")
        except BaseException as first:
            selected["expected_first"] = first
            cleaned(selected, first=True)
            no_stage_receipt(selected)
            raise
        pytest.fail("tampered Store stage unexpectedly delivered success")
    seen, counts = observe_factory(monkeypatch, consumer)
    kind = runtime.StoreStageError if damage == "raws-equal-new-tuple" else custody.StoreCustodyError
    with pytest.raises(kind) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    assert counts == Counter(factory=1, packet_consume=1, exception_retire=1)
    after_retirement(seen[0])


def test_both_presentation_lists_tail_deleted_keep_full_cleanup(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch) as journal:
                acquire_two(value, selected)
                del journal._slots[-1]
                del journal._registrations[-1]
                assert len(journal._slots) == len(journal._registrations) == 1
                assert custody._history(journal) is selected["original_history"]
        except custody.StoreCustodyError as first:
            assert first.code == "ENROLLMENT_STORE_CUSTODY_NATIVE_COVERAGE_CHANGED"
            selected["expected_first"] = first
            cleaned(selected, first=True)
            no_stage_receipt(selected)
            raise
        pytest.fail("truncated presentations must not omit an acquired HANDLE")
    seen, _counts = observe_factory(monkeypatch, consumer)
    with pytest.raises(custody.StoreCustodyError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    after_retirement(seen[0])


@pytest.mark.parametrize("damage", ["journal-slots-equal-list", "journal-registrations-equal-list"],
                         ids=["journal-slots-equal-list", "journal-registrations-equal-list"])
def test_equal_presentation_list_replacement_refuses_business_but_keeps_cleanup(actual_prefix_env, monkeypatch, damage):
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch) as journal:
                acquire_two(value, selected)
                name = "_slots" if damage == "journal-slots-equal-list" else "_registrations"
                prior = getattr(journal, name)
                replacement = list(prior)
                assert replacement == prior and replacement is not prior
                setattr(journal, name, replacement)
                assert custody._history(journal) is selected["original_history"]
                custody_error(lambda: custody._journal(journal, live=True), "NATIVE_COVERAGE_CHANGED")
        except custody.StoreCustodyError as first:
            assert first.code == "ENROLLMENT_STORE_CUSTODY_NATIVE_COVERAGE_CHANGED"
            selected["expected_first"] = first
            cleaned(selected, first=True)
            no_stage_receipt(selected)
            raise
        pytest.fail("equal replacement presentation cannot authorize stage completion")
    seen, _counts = observe_factory(monkeypatch, consumer)
    with pytest.raises(custody.StoreCustodyError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    after_retirement(seen[0])


@pytest.mark.parametrize("kind", ["runtime", "exit"], ids=["runtime", "exit"])
def test_body_first_survives_close_secondary(actual_prefix_env, monkeypatch, kind):
    first = (RuntimeError if kind == "runtime" else SystemExit)("r907 actual consumer first")
    cleanup = RuntimeError("r907 call-layer close secondary after real native close")
    def consumer(value, selected):
        def body():
            try:
                raise first
            except BaseException:
                # Capture only the test's own throw traceback node. The
                # later check traverses nodes by identity, without reading
                # product frames, code positions, locals, or arguments.
                selected["body_traceback"] = BaseException.__dict__["__traceback__"].__get__(first)
                raise
        try:
            with observed_stage(value, selected, monkeypatch, close_fault=cleanup):
                acquire_two(value, selected)
                body()
        except BaseException as observed:
            assert observed is first
            cleaned(selected, unknown=1, first=True)
            assert selected["actual_owners"][-1][1]._receipt.native_state == "UNKNOWN"
            assert selected["actual_owners"][0][1]._receipt.native_state == "CLOSED"
            assert cleanup in (selected["actual_owners"][-1][0].failure, selected["actual_owners"][-1][1]._failure)
            no_stage_receipt(selected)
            before = list(selected["native_calls"])
            same_error(selected["actual_owners"][-1][0].close, cleanup)
            assert selected["native_calls"] == before
            raise
    seen, _counts = observe_factory(monkeypatch, consumer)
    same_error(lambda: publish(actual_prefix_env), first)
    traceback = BaseException.__dict__["__traceback__"].__get__(first)
    matches = []
    while traceback is not None:
        if traceback is seen[0]["body_traceback"]:
            matches.append(traceback)
        traceback = traceback.tb_next
    assert len(matches) == 1
    after_retirement(seen[0], debt=True)


def test_cleanup_first_without_body_is_not_replaced_or_retried(actual_prefix_env, monkeypatch):
    first = RuntimeError("r907 cleanup first after actual close")
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch, close_fault=first):
                acquire_two(value, selected)
        except BaseException as observed:
            assert observed is first
            cleaned(selected, unknown=1, first=True)
            no_stage_receipt(selected)
            before = list(selected["native_calls"])
            same_error(selected["actual_owners"][-1][0].close, first)
            assert selected["native_calls"] == before
            raise
    seen, _counts = observe_factory(monkeypatch, consumer)
    same_error(lambda: publish(actual_prefix_env), first)
    after_retirement(seen[0], debt=True)


def test_reset_call_fault_after_actual_reset_still_seals(actual_prefix_env, monkeypatch):
    first = RuntimeError("r907 reset call layer after actual reset")
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch, reset_fault=first):
                acquire_two(value, selected)
        except BaseException as observed:
            assert observed is first
            cleaned(selected, reset_state="UNKNOWN", first=True)
            assert all(fact.native_state == "CLOSED" for fact in selected["snapshot"].facts)
            assert selected["snapshot"].all_registered_native_closed is False
            no_stage_receipt(selected)
            raise
    seen, _counts = observe_factory(monkeypatch, consumer)
    same_error(lambda: publish(actual_prefix_env), first)
    after_retirement(seen[0], debt=True)


def test_seal_call_fault_after_actual_seal_refuses_stage_receipt(actual_prefix_env, monkeypatch):
    first = RuntimeError("r907 seal call layer after actual seal")
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch, seal_fault=first):
                acquire_two(value, selected)
        except BaseException as observed:
            assert observed is first
            cleaned(selected)
            no_stage_receipt(selected)
            before = (list(selected["native_calls"]), selected["counts"].copy())
            stage_error(lambda: runtime._finish_stage(selected["scope"]), "CLOSED")
            assert (selected["native_calls"], selected["counts"]) == before
            raise
    seen, _counts = observe_factory(monkeypatch, consumer)
    same_error(lambda: publish(actual_prefix_env), first)
    after_retirement(seen[0])


def test_final_clock_fault_after_cleanup_keeps_native_facts(actual_prefix_env, monkeypatch):
    first = RuntimeError("r907 final clock fault without changing actual clock or budget")
    def consumer(value, selected):
        prior = (value._budget, value._parent, value._effective)
        try:
            with observed_stage(value, selected, monkeypatch, clock_fault=first):
                acquire_two(value, selected)
        except BaseException as observed:
            assert observed is first
            cleaned(selected)
            assert selected["counts"]["final_clock_fault"] == 1
            assert all(a is b for a, b in zip(prior, (value._budget, value._parent, value._effective)))
            no_stage_receipt(selected)
            raise
    seen, _counts = observe_factory(monkeypatch, consumer)
    same_error(lambda: publish(actual_prefix_env), first)
    after_retirement(seen[0])


def test_factory_stage_and_foreign_continuation_replay_refused(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        stage_error(lambda: selected["original_factory"](selected["packet_slot"], **selected["factory_kwargs"]), "CLOSED")
        stage_error(lambda: runtime._store_prepare_scope(copy_slots(value)), "INVALID_ARGUMENT")
        with observed_stage(value, selected, monkeypatch):
            stage_error(lambda: runtime._store_prepare_scope(value), "CLOSED")
            stage_error(selected["scope"].__enter__, "CLOSED")
            acquire_two(value, selected)
        cleaned(selected)
        receipt = runtime._stage_close_receipt(selected["scope"])
        assert receipt.registered_native_count == 2
        before = (list(selected["native_calls"]), selected["counts"].copy())
        stage_error(lambda: runtime._store_prepare_scope(value), "CLOSED")
        stage_error(lambda: runtime._finish_stage(selected["scope"]), "CLOSED")
        assert (selected["native_calls"], selected["counts"]) == before
    seen, counts = observe_factory(monkeypatch, consumer)
    publish(actual_prefix_env)
    assert counts == Counter(factory=1, packet_consume=1, normal_retire=1)
    after_retirement(seen[0])


def test_empty_stage_closes_context_without_success_receipt(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch):
                assert selected["actual_owners"] == []
        except runtime.StoreStageError as first:
            assert first.code == "ENROLLMENT_STORE_STAGE_NATIVE_STAGE_INCOMPLETE"
            selected["expected_first"] = first
            cleaned(selected, count=0)
            assert selected["snapshot"].all_registered_native_closed is False
            assert selected["native_calls"] == selected["os_closed"] == []
            no_stage_receipt(selected)
            raise
        pytest.fail("empty Store native journal must not issue a close receipt")
    seen, _counts = observe_factory(monkeypatch, consumer)
    with pytest.raises(runtime.StoreStageError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    after_retirement(seen[0])


def test_equal_receipt_copy_is_not_registered_receipt(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observed_stage(value, selected, monkeypatch):
            acquire_two(value, selected)
        cleaned(selected)
        actual = runtime._stage_close_receipt(selected["scope"])
        replacement = replace(actual)
        assert replacement == actual and replacement is not actual
        selected["real_issued_receipt"] = actual
        selected["scope"]._receipt = value._receipt = replacement
        stage_error(lambda: runtime._stage_close_receipt(selected["scope"]), "STAGE_NOT_CLOSED")
        assert runtime._CLOSE_RECEIPTS[id(selected["scope"])][2] is actual
        assert runtime._CLOSE_RECEIPTS[id(selected["scope"])][2] is not replacement
        assert all(fact.native_state == "CLOSED" for fact in actual.native_snapshot.facts)
    seen, counts = observe_factory(monkeypatch, consumer)
    publish(actual_prefix_env)
    assert counts == Counter(factory=1, packet_consume=1, normal_retire=1)
    after_retirement(seen[0])


@pytest.mark.parametrize("damage", ["owner-retired-without-failure", "owner-retired-with-existing-baseexception"],
                         ids=["owner-retired-without-failure", "owner-retired-with-existing-baseexception"])
def test_retired_owner_presentation_cannot_skip_original_slot_cleanup(actual_prefix_env, monkeypatch, damage):
    existing = SystemExit("r907 existing owner BaseException before claimed cleanup") if damage == "owner-retired-with-existing-baseexception" else None

    def consumer(value, selected):
        try:
            with observed_stage(value, selected, monkeypatch):
                acquire_two(value, selected)
                owner, native_slot, number = selected["actual_owners"][-1]
                assert custody._owner_slot(owner, native_slot) is native_slot
                assert owner.retired is False and owner.failure is None
                assert native_slot._known is native_slot._representation_confirmed is True
                assert native_slot._close_attempted is False and native_slot._resource == number
                assert custody._history(selected["journal"]) is selected["original_history"]
                selected["tampered_owner"] = owner
                owner.retired = True
                if existing is not None:
                    try:
                        raise existing
                    except BaseException:
                        selected["existing_failure_traceback"] = BaseException.__dict__["__traceback__"].__get__(existing)
                    owner.failure = existing
                assert owner.retired is True and owner.failure is existing
                assert native_slot._close_attempted is False
                assert owner.value == number and native_slot._owner is owner
        except BaseException as observed:
            if existing is None:
                assert type(observed) is custody.StoreCustodyError
                assert observed.code == "ENROLLMENT_STORE_CUSTODY_INVALID_ARGUMENT"
            else:
                assert observed is existing
                assert selected["tampered_owner"].failure is existing
                assert selected["actual_owners"][-1][1]._failure is existing
            selected["expected_first"] = observed
            cleaned(selected, first=True)
            assert custody._history(selected["journal"]) is selected["original_history"]
            assert all(fact.native_acquired is fact.close_attempted is True and fact.native_state == "CLOSED"
                       for fact in selected["snapshot"].facts)
            assert selected["snapshot"].unknown_count == 0
            assert selected["snapshot"].all_registered_native_closed is False
            assert selected["journal"]._first is observed
            assert value._first is observed
            no_stage_receipt(selected)
            before = (list(selected["native_calls"]), selected["counts"].copy())
            stage_error(lambda: runtime._finish_stage(selected["scope"]), "CLOSED")
            assert (selected["native_calls"], selected["counts"]) == before
            raise
        pytest.fail("retired owner presentation must refuse stage success after complete actual-slot cleanup")

    seen, counts = observe_factory(monkeypatch, consumer)
    if existing is None:
        with pytest.raises(custody.StoreCustodyError) as caught:
            publish(actual_prefix_env)
        assert caught.value is seen[0]["expected_first"]
    else:
        same_error(lambda: publish(actual_prefix_env), existing)
        assert seen[0]["expected_first"] is existing
        traceback = BaseException.__dict__["__traceback__"].__get__(existing)
        matches = 0
        while traceback is not None:
            matches += int(traceback is seen[0]["existing_failure_traceback"])
            traceback = traceback.tb_next
        assert matches == 1
    assert counts == Counter(factory=1, packet_consume=1, exception_retire=1)
    assert seen[0]["fixture_closed"] == []
    after_retirement(seen[0])
