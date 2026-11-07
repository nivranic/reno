"""Actual normal17 control factories and receipt-first State effects.

The isolated Cloud ASGI/SQL produces actual HTTP facts. Home origin, permanent
records, owned budget SQL, receipt readers and State writers are real factories.
Only external DNS/header answers and source process outcomes are controlled.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid

import pytest

from reno import budget, budget_contract, budget_writer_witness as sql
from reno import sync_source_admission17 as admissions
from reno import sync_source_binding17 as bindings, sync_source_lifecycle17 as controls
from reno import sync_source_authority as permanent
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create, permanent_files


def waiting_control(value):
    core = create(value)
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=core["item_id"])
    # The actual model key remains the original key used by ModelScope. It is
    # not rewritten to a Home source key when source admission is added.
    operation = budget.OperationSpec(core["continuation_id"], core["execution_generation"],
        "model", "atomize:BV1xx411c7mD", hashlib.sha256(b"original-model-input").hexdigest(),
        core["root_job_id"])
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=core["item_id"], binding=binding) as view:
        def guard(con):
            view.verify_for_commit(con, writer_permit=sql.require_writer(con, profile=value.profile))
        with pytest.raises(budget_contract.BudgetDeferred) as deferred:
            budget.reserve(operation, budget.AttemptSpec(hashlib.sha256(b"original-request").hexdigest(),
                1, "paas_v4", 32), profile=value.profile,
                policy=budget_contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0}),
                now=datetime.now(timezone.utc) - timedelta(days=2), reservation_guard=guard)
    try:
        ref = controls.freeze_budget_defer17_locked(profile=value.profile, store=value.store,
            client=value.client, operation_id=deferred.value.operation_id)
    except SyncError as exc:
        # Keep isolated test diagnostics below production's sanitized envelope.
        original = exc
        while original.__context__ is not None:
            original = original.__context__
        raise original from None
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(ref)
    return core, operation, ref, record


def test_actual_model_wait_is_permanent_before_one_pending_state_install(live17):
    core, operation, ref, record = waiting_control(live17)
    assert live17.store.read()["budget_pending"] is None
    installed = controls.install_control17_locked(profile=live17.profile, store=live17.store,
        request_id=ref["record_id"])
    assert installed["source_execution"] == core
    assert installed["source_authority"]["defer"] == ref
    assert installed["budget_pending"]["defer_request"] == record["request"]
    assert installed["budget_pending"]["defer_receipt"] is None
    assert budget.get_operation(operation, profile=live17.profile).operation_id == record["request"]["budget_operation_id"]
    before_files, before_state = permanent_files(live17), deepcopy(live17.store.read())
    assert controls.freeze_budget_defer17_locked(profile=live17.profile, store=live17.store,
        client=live17.client, operation_id=record["request"]["budget_operation_id"]) == ref
    assert live17.store.read() == before_state and permanent_files(live17) == before_files
    assert_current17_runtime()


def test_actual_http_receipt_commits_before_state_and_replay_uses_zero_rpc(live17, monkeypatch):
    core, _, ref, record = waiting_control(live17)
    controls.install_control17_locked(profile=live17.profile, store=live17.store,
        request_id=ref["record_id"])
    delivered = controls.deliver_registered_control17(profile=live17.profile, store=live17.store,
        client=live17.client, request_id=ref["record_id"])
    assert live17.store.read()["budget_pending"]["defer_receipt"] is None
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert owner.read(delivered["ref"]) == delivered["record"]
    updated = controls.record_control_receipt17_locked(profile=live17.profile, store=live17.store,
        request_id=ref["record_id"])
    assert updated["source_execution"] == core
    assert updated["budget_pending"]["defer_receipt"] == delivered["record"]["result"]
    before_files, before_state = permanent_files(live17), deepcopy(live17.store.read())
    def no_rpc(*args, **kwargs):
        pytest.fail("An actual permanent receipt replay dispatched an RPC")
    monkeypatch.setattr(live17.client, "_request", no_rpc)
    assert controls.deliver_control17(profile=live17.profile, store=live17.store,
        client=live17.client, request_id=ref["record_id"]) == delivered
    assert live17.store.read() == before_state and permanent_files(live17) == before_files


def test_actual_registration_commits_original_control_before_delivery(live17):
    core, _, ref, record = waiting_control(live17)
    controls.install_control17_locked(profile=live17.profile, store=live17.store, request_id=ref["record_id"])
    intent = controls.create_registration17_locked(profile=live17.profile, store=live17.store,
        client=live17.client, control_request_id=ref["record_id"])
    request = intent["record"]["request"]
    answer = controls.deliver_registered_control17(profile=live17.profile, store=live17.store,
        client=live17.client, request_id=ref["record_id"])
    assert live17.store.read()["source_recovery_pending"] is None
    assert answer["record"]["request_ref"] == ref
    assert [endpoint for endpoint, _ in live17.remote.seen][-2:] == ["register", "push"]
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        stored = owner.observe()["entries"]["receipts"][request["request_id"]]
        registration = stored["record"]["result"]
        assert stored["state"] == "committed" and registration == live17.remote.payloads["register"]
        assert registration["core_sha256"] == permanent.digest(core) and registration["item_id"] == core["item_id"]


def test_real_due_resume_terminal_history_then_readmit_consumes_only_active_wait_refs(live17):
    from reno import tasks
    core, _, ref, original = waiting_control(live17)
    controls.install_control17_locked(profile=live17.profile, store=live17.store, request_id=ref["record_id"])
    controls.deliver_registered_control17(profile=live17.profile, store=live17.store, client=live17.client,
        request_id=ref["record_id"])
    controls.record_control_receipt17_locked(profile=live17.profile, store=live17.store, request_id=ref["record_id"])
    assert tasks.renew_worker(live17.owner, profile=live17.profile)
    resume = controls.freeze_budget_resume17_locked(profile=live17.profile, store=live17.store, client=live17.client)
    controls.install_control17_locked(profile=live17.profile, store=live17.store, request_id=resume["record_id"])
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        record = owner.read(resume)
    controls.deliver_registered_control17(profile=live17.profile, store=live17.store, client=live17.client,
        request_id=resume["record_id"])
    controls.record_control_receipt17_locked(profile=live17.profile, store=live17.store, request_id=resume["record_id"])
    assert tasks.renew_worker(live17.owner, profile=live17.profile)
    terminal = controls.complete_terminal17_locked(profile=live17.profile, store=live17.store, kind="budget_resumed")
    assert terminal["source_execution"] == core and terminal["budget_pending"] is None
    assert terminal["source_authority"]["defer"] == ref and terminal["source_authority"]["resume"] == resume
    intent = controls.create_current_readmit17_locked(profile=live17.profile, store=live17.store, client=live17.client)
    request = intent["record"]["request"]
    controls.install_recovery_pending17_locked(profile=live17.profile, store=live17.store,
        request_id=request["request_id"])
    delivered = controls.deliver_recovery17(profile=live17.profile, store=live17.store, client=live17.client,
        request_id=request["request_id"])
    assert delivered["record"]["result"]["format_version"] == 2
    after = controls.reconcile_recovery17_locked(profile=live17.profile, store=live17.store,
        request_id=request["request_id"])
    assert after["source_execution"] == core and after["budget_pending"] is None
    assert after["source_authority"]["defer"] is after["source_authority"]["resume"] is None
    assert after["source_admission"]["admission_id"] == request["request_id"]
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert owner.read(ref) == original and owner.read(resume) == record


def completed_failure(value):
    """Real original paid source failure, HTTP fact and terminal5 producer."""
    from reno import source_contract, source_terminal
    core = create(value)
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store, item_id=core["item_id"])
    # The original metadata failure retains real DNS/header predecessors.
    # This helper executes the actual resolver budget writers/settlements;
    # only the external DNS/header answers are controlled.
    from tests.test_sync_source_restore_cycle17 import complete_metadata_resolution
    complete_metadata_resolution(SimpleNamespace(profile=value.profile, store=value.store,
        client=value.client, core=core), binding)
    plan = source_contract.SourcePlan("source_metadata", binding.submitted_url)
    input_sha = hashlib.sha256(source_contract.canonical_json({"kind": "source_metadata",
        "source": binding.submitted_url, "runner_contract": "yt_dlp_v1"}).encode()).hexdigest()
    operation = binding.operation("source_metadata", "ingest:metadata:v1", input_sha)
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=core["item_id"], binding=binding) as admission:
        def guard(connection):
            admission.verify_for_commit(connection, writer_permit=sql.require_writer(connection,
                profile=value.profile))
        attempted = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256,
            source_contract_version=1), profile=value.profile, reservation_guard=guard)
        assert budget.mark_dispatching(attempted.attempt_id, attempted.reservation_sha256,
            str(uuid.uuid4()), expected_revision=attempted.revision, profile=value.profile,
            dispatch_guard=guard).won
    response = source_contract.SourceProcessResult(plan.request_sha256, True, True, 1, "exited",
        failure_class="process_failure")
    budget.settle(attempted.attempt_id, plan.request_sha256, outcome="terminal_error", profile=value.profile,
        result_text=source_contract.encode_envelope(plan, response, input_sha256=operation.input_sha256))
    proof = source_terminal.known_failure(binding, attempt_id=attempted.attempt_id)
    assert proof is not None
    ref = controls.freeze_source_failure17_locked(profile=value.profile, store=value.store,
        client=value.client, attempt_id=attempted.attempt_id)
    controls.install_control17_locked(profile=value.profile, store=value.store, request_id=ref["record_id"])
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(ref)
    controls.deliver_registered_control17(profile=value.profile, store=value.store, client=value.client, request_id=ref["record_id"])
    controls.record_control_receipt17_locked(profile=value.profile, store=value.store, request_id=ref["record_id"])
    closed = controls.complete_terminal17_locked(profile=value.profile, store=value.store, kind="source_failed")
    assert closed["source_execution"] is closed["source_control_pending"] is closed["source_authority"] is None
    history = value.store.completed_source_failure(root_job_id=core["root_job_id"],
        continuation_id=core["continuation_id"], execution_generation=core["execution_generation"])
    assert history is not None and history["source_terminal"]["format_version"] == 2
    return core, operation, attempted, history


def test_real_paid_failure_and_http_fact_close_only_the_original_source(live17):
    core, operation, attempted, history = completed_failure(live17)
    assert history["source_execution"] == core
    view = budget.get_operation(operation, profile=live17.profile)
    assert view.attempts[0].attempt_id == attempted.attempt_id and view.attempts[0].state == "settled"
    assert view.attempts[0].outcome == "terminal_error"
    assert live17.store.read()["source_admission"] is None
