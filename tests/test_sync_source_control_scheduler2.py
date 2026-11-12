"""Actual settled control history never becomes a blanket paid permission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from reno import budget, budget_contract, budget_writer_witness as sql, migrations, tasks
from reno import sync_source_admission17 as admissions, sync_source_binding17 as bindings
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects
from reno import sync_source_opportunity17 as opportunity, sync_source_readmit_contract as wire
from reno import sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_admission17 import admitted
from tests.test_sync_source_lifecycle17 import waiting_control
from tests.test_sync_source_restore_cycle17 import (
    actual_call, negative_original_control, observe_new_control_revision,
    register_original_control, restored_origin17,
)
from tests import test_sync_source_readmit_contract as sample


def read_control(value, reference):
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        return owner.read(reference)


def accept_readmit(value, intent, *, attempts):
    """Use the actual HTTP publisher and original receipt-first State effect."""
    body = intent["record"]["request"]
    answer = sample.admission(body, attempts=attempts)
    answer["format_version"] = 2
    answer["admission_sha256"] = wire.admission_sha256(answer)
    value.remote.payloads["readmit"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile,
        store=value.store, request_id=body["request_id"])
    delivered = actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store,
        client=value.client, request_id=body["request_id"])
    answer = delivered["record"]["result"]
    assert answer["attempts"] == attempts
    after = actual_call(effects.reconcile_recovery17_locked, profile=value.profile,
        store=value.store, request_id=body["request_id"])
    assert after["source_execution"] == value.core
    assert after["source_authority"]["defer"] is after["source_authority"]["resume"] is None
    assert after["source_admission"]["admission_id"] == body["request_id"]
    return answer


def finish_resume_and_readmit(value, *, registered=False):
    resumed = sample.old_receipt(value.control["request"], attempts=0,
        parent=value.defer["request"]["claim"])
    value.remote.payloads["push"] = resumed
    delivered = actual_call(effects.deliver_registered_control17 if registered else effects.deliver_control17,
        profile=value.profile, store=value.store, client=value.client,
        request_id=value.control["record_id"])
    resumed = delivered["record"]["result"]
    actual_call(effects.record_control_receipt17_locked, profile=value.profile,
        store=value.store, request_id=value.control["record_id"])
    assert tasks.renew_worker(value.owner, profile=value.profile)
    after = actual_call(effects.complete_terminal17_locked, profile=value.profile,
        store=value.store, kind="budget_resumed")
    assert after["budget_pending"] is None and after["source_execution"] == value.core
    intent = actual_call(effects.create_current_readmit17_locked, profile=value.profile,
        store=value.store, client=value.client)
    body = intent["record"]["request"]
    assert body["defer_receipt"] == value.defer_receipt
    assert body["resume_receipt"] == resumed
    return intent, accept_readmit(value, intent, attempts=1)


def assert_current_after_readmit(value, answer):
    """A fully settled ledger is readable without another observation or write."""
    original_schema_version = migrations.SCHEMA_VERSION
    assert tasks.renew_worker(value.owner, profile=value.profile)
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.core["item_id"])
    before, files, calls = deepcopy(value.store.read()), permanent_files(value), list(value.remote.seen)
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=value.core["item_id"], binding=binding) as captured:
        assert type(captured) is admissions.CurrentAdmissionView17
        assert captured.kind == "admission"
        assert captured.creation_basis["claim"] == answer["claim"]
        assert captured.creation_basis["admission_ref"] == before["source_admission"]
        with sql.connection(value.profile, write=True) as connection:
            captured.verify_for_commit(connection,
                writer_permit=sql.require_writer(connection, profile=value.profile))
        captured.verify_source()
    assert value.store.read() == before and permanent_files(value) == files
    assert value.remote.seen == calls
    assert migrations.SCHEMA_VERSION == original_schema_version


def test_first_original_core_defer_resume_readmit_keeps_history_and_reopens_current_view(live17):
    value = live17
    value.core, operation, value.defer_ref, value.defer = waiting_control(value)
    assert value.defer["creation_basis"]["admission_ref"] is None
    paid = budget.get_operation(operation, profile=value.profile)
    actual_call(effects.install_control17_locked, profile=value.profile,
        store=value.store, request_id=value.defer_ref["record_id"])
    value.defer_receipt = sample.old_receipt(value.defer["request"], attempts=0)
    value.remote.payloads["push"] = value.defer_receipt
    delivered = actual_call(effects.deliver_registered_control17, profile=value.profile, store=value.store,
        client=value.client, request_id=value.defer_ref["record_id"])
    value.defer_receipt = delivered["record"]["result"]
    actual_call(effects.record_control_receipt17_locked, profile=value.profile,
        store=value.store, request_id=value.defer_ref["record_id"])
    assert tasks.renew_worker(value.owner, profile=value.profile)
    value.control_ref = actual_call(effects.freeze_budget_resume17_locked,
        profile=value.profile, store=value.store, client=value.client)
    value.control = read_control(value, value.control_ref)
    assert value.control["creation_basis"]["admission_ref"] is None
    actual_call(effects.install_control17_locked, profile=value.profile,
        store=value.store, request_id=value.control_ref["record_id"])
    intent, answer = finish_resume_and_readmit(value, registered=True)
    assert intent["record"]["request"]["previous_admission_id"] is None
    assert_current_after_readmit(value, answer)
    assert read_control(value, value.defer_ref) == value.defer
    assert read_control(value, value.control_ref) == value.control
    assert budget.get_operation(operation, profile=value.profile) == paid


@pytest.mark.parametrize("restored_origin17", ["admitted_resume_double"], indirect=True)
def test_three_actual_negative_predecessors_then_readmit_reopens_current_view(restored_origin17):
    value = restored_origin17
    prior = deepcopy(value.oldadm_ref)
    paid = budget.get_operation(value.operation, profile=value.profile)
    negatives, ancestors = [], []
    for iteration in range(3):
        assert tasks.renew_worker(value.owner, profile=value.profile)
        old, negative = negative_original_control(value)
        ancestors.append(old)
        negatives.append(negative)
        if iteration:
            observe_new_control_revision(value)
        value.control_ref = actual_call(effects.freeze_budget_resume17_locked,
            profile=value.profile, store=value.store, client=value.client)
        value.control = read_control(value, value.control_ref)
        assert value.control["retirement_head"] == negative
        assert value.control["creation_basis"]["admission_ref"] == prior
        actual_call(effects.install_control17_locked, profile=value.profile,
            store=value.store, request_id=value.control_ref["record_id"])
        value.registration = register_original_control(value)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        graph = local.control_retirement_graph2(owner.observe()["entries"], value.control["record_id"])
        assert len(graph["ancestor_refs"]) == 3 and graph["retired_disposition_refs"] == negatives
    intent, answer = finish_resume_and_readmit(value, registered=True)
    assert intent["record"]["request"]["previous_admission_id"] == prior["admission_id"]
    assert intent["record"]["request"]["dispositions"] == []
    assert_current_after_readmit(value, answer)
    for old in ancestors:
        assert read_control(value, permanent.record_ref(old)) == old
    assert budget.get_operation(value.operation, profile=value.profile) == paid


def test_actual_control_frozen_after_original_readmit_intent_stays_unconsumed_and_blocks_fresh_paid(live17):
    value = live17
    binding, _intent, _receipt, prior = admitted(value)
    value.core = binding.core
    operation = binding.operation("model", "atomize:unconsumed-old-admission",
        hashlib.sha256(b"actual unconsumed original input").hexdigest())
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=value.core["item_id"], binding=binding) as captured:
        def reserve_guard(connection):
            captured.verify_for_commit(connection,
                writer_permit=sql.require_writer(connection, profile=value.profile))
        with pytest.raises(budget_contract.BudgetDeferred) as deferred:
            budget.reserve(operation, budget.AttemptSpec(hashlib.sha256(b"actual original waiting request").hexdigest(),
                1, "paas_v4", 32), profile=value.profile,
                policy=budget_contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0}),
                now=datetime.now(timezone.utc) - timedelta(days=2), reservation_guard=reserve_guard)
    # Freeze the real readmit first, then persist another original paid control
    # before completing that exact readmit. No mutable body or seal is replaced.
    next_intent = actual_call(effects.create_current_readmit17_locked, profile=value.profile,
        store=value.store, client=value.client)
    body = next_intent["record"]["request"]
    assert body["defer_receipt"] is body["resume_receipt"] is None and body["dispositions"] == []
    reference = actual_call(effects.freeze_budget_defer17_locked, profile=value.profile,
        store=value.store, client=value.client, operation_id=deferred.value.operation_id)
    unconsumed = read_control(value, reference)
    assert unconsumed["creation_basis"]["admission_ref"]["admission_id"] == prior["admission_id"]
    # A held claim does not add an attempt; only the original defer refund
    # can change that count. Keep the new receipt a legitimate Cloud result.
    answer = accept_readmit(value, next_intent, attempts=prior["attempts"])
    before, files, calls = deepcopy(value.store.read()), permanent_files(value), list(value.remote.seen)
    assert before["source_admission"]["admission_id"] == answer["admission_id"] != prior["admission_id"]
    assert before["source_authority"]["defer"] is None and before["budget_pending"] is None
    with sql.connection(value.profile) as connection:
        counts = tuple(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("budget_operation", "budget_attempt"))
    fresh = binding.operation("model", "atomize:must-stay-blocked",
        hashlib.sha256(b"must not create a fresh paid operation").hexdigest())
    with pytest.raises(SyncError) as raised:
        with value.store.reference_operation(), admissions.capture_current_admission_locked(
                profile=value.profile, store=value.store, client=value.client,
                item_id=value.core["item_id"], binding=binding) as captured:
            def fresh_guard(connection):
                captured.verify_for_commit(connection,
                    writer_permit=sql.require_writer(connection, profile=value.profile))
            budget.reserve(fresh, budget.AttemptSpec(hashlib.sha256(b"must not reserve").hexdigest(),
                1, "paas_v4", 32), profile=value.profile, reservation_guard=fresh_guard)
    assert type(raised.value) is not opportunity.ReadmissionRequired17
    assert raised.value.code.endswith("CONTROL_PENDING")
    with sql.connection(value.profile) as connection:
        assert tuple(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("budget_operation", "budget_attempt")) == counts
    assert value.store.read() == before and permanent_files(value) == files and value.remote.seen == calls
    assert read_control(value, reference) == unconsumed
    assert_current17_runtime()
