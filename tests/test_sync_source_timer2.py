"""Genuine original Home wait/control/HTTP facts and due timer producers."""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from reno import budget, budget_contract
from reno import budget_writer_witness as sql
from reno import sync_source_binding17 as bindings, sync_source_admission17 as admissions
from reno import sync_source_lifecycle17 as lifecycle17
from reno import sync_source_timer2 as timer, sync_source_authority as permanent
from reno import sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create


def produce_defer(value, *, infinite=False):
    create(value)
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.remote.entry["item_id"])
    with value.store.reference_operation():
        with admissions.capture_current_admission_locked(profile=value.profile, store=value.store,
                client=value.client, item_id=value.remote.entry["item_id"], binding=binding) as view:
            core = binding.core
            operation = budget.OperationSpec(core["continuation_id"], core["execution_generation"],
                "model", "atomize:BV1xx411c7mD", hashlib.sha256(b"original-timer-input").hexdigest(), core["root_job_id"])
            policy = budget_contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0})
            def guard(connection):
                view.verify_for_commit(connection, writer_permit=sql.require_writer(connection, profile=value.profile))
            with pytest.raises(budget_contract.BudgetDeferred) as deferred:
                budget.reserve(operation, budget.AttemptSpec(hashlib.sha256(b"original-request").hexdigest(), 1, "paas_v4", 32),
                    profile=value.profile, policy=policy, now=datetime.now(timezone.utc) - timedelta(days=2),
                    reservation_guard=guard)
    try:
        ref = lifecycle17.freeze_budget_defer17_locked(profile=value.profile, store=value.store,
            client=value.client, operation_id=deferred.value.operation_id)
    except SyncError as exc:
        current = exc
        while current.__context__ is not None:
            current = current.__context__
        raise current from None
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(ref)
    lifecycle17.install_control17_locked(profile=value.profile, store=value.store, request_id=ref["record_id"])
    # Fresh normal17 effects first commit their real Cloud registration. The
    # result and its typed HTTP capture come from the actual Cloud SQL writer.
    receipt = lifecycle17.deliver_registered_control17(profile=value.profile, store=value.store,
        client=value.client, request_id=ref["record_id"])
    lifecycle17.record_control_receipt17_locked(profile=value.profile, store=value.store, request_id=ref["record_id"])
    return record, receipt


def test_original_due_timer_is_permanent_before_resume_and_does_not_create_user_authorization(live17):
    original, receipt = produce_defer(live17)
    from reno import storage_admin
    with storage_admin.connection(live17.profile) as con:
        original_requests = [tuple(row) for row in con.execute("SELECT * FROM task_request ORDER BY request_id")]
    with live17.store.reference_operation():
        due = timer.create_due_locked(profile=live17.profile, store=live17.store,
            defer_request_id=original["record_id"])
        with timer.capture_timer_locked(profile=live17.profile, store=live17.store,
                defer_request_id=original["record_id"]) as captured:
            assert captured.parent_defer == {"request": original["request"], "receipt": receipt["record"]["result"]}
            assert captured.wire_basis["timer_operation_id"] == wire.timer_operation_id(original["record_id"])
            assert captured.wire_basis["timer_receipt_sha256"] == due["ref"]["record_sha256"]
    with storage_admin.connection(live17.profile) as con:
        assert [tuple(row) for row in con.execute("SELECT * FROM task_request ORDER BY request_id")] == original_requests
    assert live17.store.read()["budget_pending"]["resume_request"] is None
    assert_current17_runtime()


def test_timer_replay_preserves_original_clock_uuid_and_receipt_bytes(live17, monkeypatch):
    original, _ = produce_defer(live17)
    with live17.store.reference_operation():
        before = timer.create_due_locked(profile=live17.profile, store=live17.store, defer_request_id=original["record_id"])
    from reno import tasks
    monkeypatch.setattr(tasks, "clock_ms", lambda: 0)
    with live17.store.reference_operation():
        after = timer.create_due_locked(profile=live17.profile, store=live17.store, defer_request_id=original["record_id"])
    assert after == before
