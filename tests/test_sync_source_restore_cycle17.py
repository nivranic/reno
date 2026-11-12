"""Actual Genesis17 -> genuine knowledge restore -> readmit State CAS.

The original source is an actual SDK16 checkpoint created before17 is
installed. No restore certificate, origin, worker or admission seal is faked.
Only the external HTTP peer is isolated, as in the real Client parser tests.
"""
from copy import deepcopy
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import threading
import uuid

import pytest

from reno import budget, budget_writer_witness as sql, config, db
from reno import home_recovery17 as restore, home_recovery_runtime17 as runtime
from reno import source_resolution_contract as resolver_wire
from reno import sync_source_admission17 as admissions, sync_source_binding17 as bindings
from reno import home_recovery_source as sources, storage_admin, sync_access
from reno import sync_source_lifecycle17 as effects, sync_source_readmit_contract as wire
from reno import sync_source_authority as permanent
from reno import sync_source_readmit_schema, sync_state, tasks, worker
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import future17_gate, create
from tests.test_sync_source_claim import environment, observe
from tests import test_sync_source_readmit_contract as sample


def actual_call(function, **selectors):
    """Expose isolated test cause below the production sanitized envelope."""
    try:
        return function(**selectors)
    except SyncError as error:
        original, seen = error, set()
        while original.__context__ is not None and id(original) not in seen:
            seen.add(id(original))
            original = original.__context__
        raise original from None


def waiting_resolution(value):
    """An actual reconstructible source input and actual original budget wait."""
    from reno.budget_contract import BudgetDeferred
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.core["item_id"])
    operation = binding.resolution_operation
    plan = resolver_wire.ResolutionPlan("dns", binding.submitted_url, binding.submitted_url,
        operation.input_sha256, config.source_resolution_attempt_limit())
    attempted = budget.AttemptSpec(plan.request_sha256, resolver_contract_version=1,
        resolver_phase="dns", resolver_max_attempts=plan.max_attempts)
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=value.core["item_id"], binding=binding) as view:
        def guard(connection):
            view.verify_for_commit(connection, writer_permit=sql.require_writer(connection,
                profile=value.profile))
        now = datetime.now(timezone.utc)
        budget.reserve(operation, attempted, profile=value.profile, now=now, reservation_guard=guard)
        with pytest.raises(BudgetDeferred) as deferred:
            budget.reserve(operation, attempted, profile=value.profile, now=now - timedelta(days=2),
                reservation_guard=guard)
        assert deferred.value.reason == "clock_rollback"
    ref = actual_call(effects.freeze_budget_defer17_locked, profile=value.profile, store=value.store,
        client=value.client, operation_id=deferred.value.operation_id)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(ref)
    return operation, ref, record


def register_original_control(value):
    """Original source_register facts come from the actual HTTP pipeline."""
    intent = actual_call(effects.create_registration17_locked, profile=value.profile,
        store=value.store, client=value.client, control_request_id=value.control_ref["record_id"])
    body = intent["record"]["request"]
    answer = sample.registration(body)
    answer.update(core_sha256=body["core_sha256"], item_id=body["item_id"])
    answer["registration_sha256"] = wire.registration_sha256(answer)
    value.remote.payloads["register"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store, client=value.client,
        request_id=body["request_id"])
    actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    return answer


def complete_metadata_resolution(value, binding):
    """Store the original DNS/header predecessor before the metadata effect.

    External answers are synthetic, while reserve, dispatch, settlement and
    their current writer permissions use the actual original Home operation.
    Restore must resolve the request from this retained chain, not an invented
    mapping or a caller-only URL.
    """
    operation = binding.resolution_operation
    maximum = config.source_resolution_attempt_limit()
    dns = resolver_wire.ResolutionPlan("dns", binding.submitted_url,
        binding.submitted_url, operation.input_sha256, maximum)
    previous = None
    for ordinal, phase in enumerate(("dns", "headers"), start=1):
        plan = dns if phase == "dns" else resolver_wire.ResolutionPlan(
            "headers", binding.submitted_url, binding.submitted_url,
            operation.input_sha256, maximum, dns_attempt_id=previous.attempt_id,
            dns_settlement_sha256=previous.settlement_sha256, peer_ip="8.8.8.8")
        with value.store.reference_operation(), admissions.capture_current_admission_locked(
                profile=value.profile, store=value.store, client=value.client,
                item_id=value.core["item_id"], binding=binding) as view:
            def guard(connection):
                view.verify_for_commit(connection,
                    writer_permit=sql.require_writer(connection, profile=value.profile))
            attempted = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, ordinal,
                resolver_contract_version=1, resolver_phase=phase,
                resolver_max_attempts=maximum), profile=value.profile, reservation_guard=guard)
            assert budget.mark_dispatching(attempted.attempt_id, attempted.reservation_sha256,
                str(uuid.uuid4()), expected_revision=attempted.revision, profile=value.profile,
                dispatch_guard=guard).won
        fields = {"addresses": ("8.8.8.8",)} if phase == "dns" else {
            "headers_complete": True, "transport_closed": True, "status": 200}
        response = resolver_wire.ResolutionResult(plan.request_sha256, phase, True, "completed",
            observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **fields)
        previous = budget.settle(attempted.attempt_id, plan.request_sha256,
            outcome="source_step_complete", profile=value.profile,
            result_text=resolver_wire.encode_envelope(plan, response,
                input_sha256=operation.input_sha256))
        assert previous.storage_state == "stored" and previous.outcome == "source_step_complete"


def paid_failure_control(value):
    """Original settled paid identity, followed by a real control producer."""
    from reno import source_contract, source_terminal
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.core["item_id"])
    actual_call(complete_metadata_resolution, value=value, binding=binding)
    plan = source_contract.SourcePlan("source_metadata", binding.submitted_url)
    input_sha = hashlib.sha256(source_contract.canonical_json({"kind": "source_metadata",
        "source": binding.submitted_url, "runner_contract": "yt_dlp_v1"}).encode()).hexdigest()
    operation = binding.operation("source_metadata", "ingest:metadata:v1", input_sha)
    with value.store.reference_operation(), admissions.capture_current_admission_locked(
            profile=value.profile, store=value.store, client=value.client,
            item_id=value.core["item_id"], binding=binding) as view:
        def guard(connection):
            view.verify_for_commit(connection, writer_permit=sql.require_writer(connection, profile=value.profile))
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
    ref = actual_call(effects.freeze_source_failure17_locked, profile=value.profile, store=value.store,
        client=value.client, attempt_id=attempted.attempt_id)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(ref)
    value.failed_operation, value.failed_attempt = operation, attempted
    return ref, record


@pytest.fixture
def restored_origin17(released16_env, monkeypatch, request):
    env = released16_env
    profile = storage_admin.profile()
    source_id = str(uuid.uuid4())
    sources.create_checkpoint(profile=profile, source_id=source_id)
    with tasks.process_lock(profile=profile) as acquired:
        assert acquired
        con = db.connect(profile.database)
        try:
            source, version = sync_access.identity(con), db.knowledge_version(con)
        finally:
            con.close()
        target = {key: env.target[key] for key in ("library_id", "node_id")}
        binding = {"target_url": "https://sync.example.test/reno",
            "home_library_id": source["library_id"], "home_node_id": source["node_id"],
            "target_library_id": target["library_id"], "target_node_id": target["node_id"]}
        store = sync_state.StateStore(profile.database.parent, profile=profile)
        store.initialize(binding, version["library_epoch"])
        store.enable_source_readmit()
        job_id, owner = tasks.submit_sync(profile=profile), str(uuid.uuid4())
        assert tasks.acquire_worker(owner, profile=profile)
        con = db.connect(profile.database)
        try:
            job = tasks.claim(con, owner, profile=profile)
            assert job["job_id"] == job_id
            sync_source_readmit_schema.install(con)
            con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
            con.commit()
        finally:
            con.close()
    value = SimpleNamespace(profile=profile, store=store, target=target, binding=binding,
        source_id=source_id, job=job, owner=owner, stop=threading.Event())
    with future17_gate(value, monkeypatch):
        con = db.connect(profile.database)
        try:
            actual_source = sync_access.identity(con)
        finally:
            con.close()
        remote = environment()
        remote.source.clear()
        remote.source.update(actual_source)
        remote.target.clear()
        remote.target.update(target)
        remote.recovery["stream"]["library_id"] = target["library_id"]
        continuity = remote.payloads["continuity"]
        continuity["source_identity"] = {key: actual_source[key] for key in ("library_id", "node_id")}
        continuity["target_identity"] = deepcopy(target)
        continuity["handshake"] = deepcopy(actual_source["handshake"])
        continuity["receiver_version"]["library_id"] = target["library_id"]
        value.source, value.remote, value.client = actual_source, remote, remote.client
        observe(remote)
        store.update({"session_id": remote.lease["session_id"], "lease": deepcopy(remote.lease),
            "claims": [deepcopy(remote.entry)], "recovery": deepcopy(remote.recovery)})
        with tasks.process_lock(profile=profile) as acquired:
            assert acquired
            with worker._execution(job, owner, value.stop, profile=profile):
                value.core = create(value)
                if getattr(request, "param", None) in {"defer", "resume"}:
                    value.operation, value.control_ref, value.control = waiting_resolution(value)
                    effects.install_control17_locked(profile=profile, store=store,
                        request_id=value.control_ref["record_id"])
                    value.registration = register_original_control(value)
                    if request.param == "resume":
                        value.defer_ref, value.defer = value.control_ref, value.control
                        value.defer_receipt = sample.old_receipt(value.defer["request"], attempts=0)
                        value.remote.payloads["push"] = value.defer_receipt
                        actual_call(effects.deliver_control17, profile=profile, store=store,
                            client=value.client, request_id=value.defer_ref["record_id"])
                        actual_call(effects.record_control_receipt17_locked, profile=profile, store=store,
                            request_id=value.defer_ref["record_id"])
                        tasks.resume_sync(job_id, profile=profile)
                        value.control_ref = actual_call(effects.freeze_budget_resume17_locked,
                            profile=profile, store=store, client=value.client)
                        actual_call(effects.install_control17_locked, profile=profile, store=store,
                            request_id=value.control_ref["record_id"])
                        with store.reference_operation(), permanent.owned(store) as original_owner:
                            value.control = original_owner.read(value.control_ref)
                        value.registration = register_original_control(value)
        before = store.read()
        preview = restore.preview(profile=profile, source_id=source_id)
        value.restore_operation = str(uuid.uuid4())
        value.restored = restore.apply(profile=profile, source_id=source_id,
            expected_digest=preview["digest"], operation_id=value.restore_operation)
        assert store.read() == {**before, "local_epoch": before["local_epoch"] + 1}
        if getattr(request, "param", None) in {"double", "admitted_double", "admitted_resume_double", "admitted_defer_double", "admitted_failure_double"}:
            if request.param in {"admitted_double", "admitted_resume_double", "admitted_defer_double", "admitted_failure_double"}:
                with tasks.process_lock(profile=profile) as acquired:
                    assert acquired
                    assert tasks.renew_worker(owner, profile=profile)
                    with worker._execution(job, owner, value.stop, profile=profile):
                        value.admission_intent = actual_call(effects.create_current_readmit17_locked,
                            profile=profile, store=store, client=value.client)
                        admitted_body = value.admission_intent["record"]["request"]
                        admitted_answer = sample.admission(admitted_body, attempts=2)
                        admitted_answer["format_version"] = 2
                        admitted_answer["admission_sha256"] = wire.admission_sha256(admitted_answer)
                        value.remote.payloads["readmit"] = admitted_answer
                        actual_call(effects.install_recovery_pending17_locked, profile=profile, store=store,
                            request_id=admitted_body["request_id"])
                        value.admission_receipt = actual_call(effects.deliver_recovery17, profile=profile,
                            store=store, client=value.client, request_id=admitted_body["request_id"])
                        admitted_state = actual_call(effects.reconcile_recovery17_locked, profile=profile,
                            store=store, request_id=admitted_body["request_id"])
                        value.oldadm_ref = deepcopy(admitted_state["source_admission"])
                        value.remote.entry.update(claim=deepcopy(admitted_answer["claim"]),
                            attempts=admitted_answer["attempts"], lease_until=admitted_answer["session"]["lease_until"])
                        observe(value.remote)
                        if request.param in {"admitted_resume_double", "admitted_defer_double"}:
                            value.operation, value.control_ref, value.control = waiting_resolution(value)
                            actual_call(effects.install_control17_locked, profile=profile, store=store,
                                request_id=value.control_ref["record_id"])
                            value.registration = register_original_control(value)
                        if request.param == "admitted_resume_double":
                            value.defer_ref, value.defer = value.control_ref, value.control
                            value.defer_receipt = sample.old_receipt(value.defer["request"], attempts=0)
                            value.remote.payloads["push"] = value.defer_receipt
                            actual_call(effects.deliver_control17, profile=profile, store=store,
                                client=value.client, request_id=value.defer_ref["record_id"])
                            actual_call(effects.record_control_receipt17_locked, profile=profile, store=store,
                                request_id=value.defer_ref["record_id"])
                            tasks.resume_sync(job_id, profile=profile)
                            value.control_ref = actual_call(effects.freeze_budget_resume17_locked,
                                profile=profile, store=store, client=value.client)
                            actual_call(effects.install_control17_locked, profile=profile, store=store,
                                request_id=value.control_ref["record_id"])
                            with store.reference_operation(), permanent.owned(store) as original_owner:
                                value.control = original_owner.read(value.control_ref)
                            value.registration = register_original_control(value)
                        if request.param == "admitted_failure_double":
                            value.control_ref, value.control = paid_failure_control(value)
                            actual_call(effects.install_control17_locked, profile=profile, store=store,
                                request_id=value.control_ref["record_id"])
                            value.registration = register_original_control(value)
            with store.reference_operation(), runtime.capture_completed_locked(profile=profile, store=store,
                    operation_id=value.restore_operation) as original_restore:
                value.original_restore_ref = original_restore.provenance_ref()
                value.original_preservation = original_restore.preservation_observation(from_epoch=value.core["local_epoch"])
            second_preview = restore.preview(profile=profile, source_id=source_id)
            value.current_restore_operation = str(uuid.uuid4())
            restore.apply(profile=profile, source_id=source_id, expected_digest=second_preview["digest"],
                operation_id=value.current_restore_operation)
            assert store.read()["local_epoch"] == before["local_epoch"] + 2
        with tasks.process_lock(profile=profile) as acquired:
            assert acquired
            assert tasks.renew_worker(owner, profile=profile)
            with worker._execution(job, owner, value.stop, profile=profile):
                yield value


def test_actual_restored_readmit_receipt_adopts_only_its_same_original_core(restored_origin17):
    value = restored_origin17
    before = value.store.read()
    intent = actual_call(effects.create_current_readmit17_locked,
        profile=value.profile, store=value.store, client=value.client)
    request = intent["record"]["request"]
    assert request["preservation"]["from_epoch"] == value.core["local_epoch"]
    assert request["preservation"]["to_epoch"] == before["local_epoch"]
    result = sample.admission(request)
    result["format_version"] = 2
    result["admission_sha256"] = wire.admission_sha256(result)
    value.remote.payloads["readmit"] = result
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=request["request_id"])
    effects.deliver_recovery17(profile=value.profile, store=value.store, client=value.client,
        request_id=request["request_id"])
    assert value.store.read()["source_admission"] is None
    after = actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=request["request_id"])
    assert after["source_execution"] == value.core == before["source_execution"]
    assert after["source_recovery_pending"] is None
    assert after["source_admission"]["admission_id"] == request["request_id"]
    assert after["claims"][0]["claim"] == result["claim"]
    assert after["work"] == before["work"] and after["pending_publication"] == before["pending_publication"]
    assert after["source_authority"] == before["source_authority"]


def test_restored_capture_rejects_state_change_without_its_actual_held_cas_guard(restored_origin17):
    value = restored_origin17
    before = value.store.read()
    with pytest.raises(runtime.RuntimeRestoreError, match="STATE_CHANGED"):
        with value.store.reference_operation(), runtime.capture_completed_locked(profile=value.profile,
                store=value.store, operation_id=value.restore_operation) as captured:
            with value.store.reference_operation():
                value.store.update({"seeded": not before["seeded"]})
                try:
                    captured.verify_source()
                finally:
                    value.store.update({"seeded": before["seeded"]})


@pytest.mark.parametrize("restored_origin17", ["defer"], indirect=True)
@pytest.mark.parametrize("applied", [True, False])
def test_restored_disposition_receipt_adopts_only_its_original_wait(restored_origin17, applied, monkeypatch):
    value = restored_origin17
    before = deepcopy(value.store.read())
    original = value.control
    intent = actual_call(effects.create_current_dispose17_locked, profile=value.profile,
        store=value.store, client=value.client, control_request_id=original["record_id"])
    body = intent["record"]["request"]
    assert body["current_home_epoch"] == before["local_epoch"] > value.core["local_epoch"]
    assert body["preservation"]["from_epoch"] == value.core["local_epoch"]
    assert body["registration"] == value.registration
    answer = sample.disposition(body, applied=applied)
    answer["format_version"] = 2
    answer.update(core_sha256=body["original"]["core_sha256"], item_id=body["original"]["item_id"])
    if applied:
        answer["original_receipt"] = sample.old_receipt(original["request"], attempts=0)
    value.remote.payloads["dispose"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store, client=value.client,
        request_id=body["request_id"])
    assert value.store.read()["budget_pending"]["defer_receipt"] is None
    after = actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    expected = deepcopy(before)
    if applied:
        expected["budget_pending"]["defer_receipt"] = answer["original_receipt"]
    else:
        expected["budget_pending"] = None
        expected["source_authority"]["defer"] = None
    assert after == expected
    assert after["source_execution"] == value.core and after["source_admission"] is None
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        assert owner.read(value.control_ref) == original
        assert owner.observe()["entries"]["receipts"][body["request_id"]]["record"]["result"] == answer
    def no_rpc(*args, **kwargs):
        pytest.fail("An original permanent disposition replay dispatched again")
    monkeypatch.setattr(value.client, "_request", no_rpc)
    if applied:
        def no_state_write(*args, **kwargs):
            pytest.fail("An already cached positive disposition replaced State again")
        monkeypatch.setattr(sync_state, "_write", no_state_write)
        assert actual_call(effects.record_control_receipt17_locked, profile=value.profile, store=value.store,
            request_id=original["record_id"]) == after
        assert value.store.read() == after
    else:
        with pytest.raises(SyncError):
            effects.deliver_control17(profile=value.profile, store=value.store, client=value.client,
                request_id=original["record_id"])


@pytest.mark.parametrize("restored_origin17", ["resume"], indirect=True)
def test_restored_negative_resume_then_new_scope_refund_readmit_keeps_original_paid_identity(restored_origin17, monkeypatch):
    value = restored_origin17
    paid_before = budget.get_operation(value.operation, profile=value.profile)
    original = value.control
    intent = actual_call(effects.create_current_dispose17_locked, profile=value.profile, store=value.store,
        client=value.client, control_request_id=original["record_id"])
    body = intent["record"]["request"]
    answer = sample.disposition(body, applied=False)
    answer.update(format_version=2, core_sha256=body["original"]["core_sha256"], item_id=body["original"]["item_id"])
    value.remote.payloads["dispose"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store, client=value.client,
        request_id=body["request_id"])
    retired = actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    assert retired["source_authority"]["resume"] is None
    assert retired["budget_pending"]["resume_request"] is retired["budget_pending"]["resume_receipt"] is None
    assert retired["budget_pending"]["defer_receipt"] == value.defer_receipt
    assert tasks.renew_worker(value.owner, profile=value.profile)
    new_ref = actual_call(effects.freeze_budget_resume17_locked, profile=value.profile, store=value.store,
        client=value.client)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        new_control = owner.read(new_ref)
        assert owner.read(value.control_ref) == original
    assert new_ref["record_id"] != original["record_id"]
    assert new_control["request"]["defer_request_id"] == value.defer_ref["record_id"]
    assert new_control["creation_basis"]["home_epoch"] == retired["local_epoch"] > value.core["local_epoch"]
    installed = actual_call(effects.install_control17_locked, profile=value.profile, store=value.store,
        request_id=new_ref["record_id"])
    assert installed["source_execution"] == value.core and installed["source_admission"] is None
    assert installed["budget_pending"]["defer_receipt"] == value.defer_receipt
    assert budget.get_operation(value.operation, profile=value.profile) == paid_before
    assert actual_call(effects.freeze_budget_resume17_locked, profile=value.profile, store=value.store,
        client=value.client) == new_ref
    # The new original resume is registered/delivered through the actual HTTP
    # pipeline; the genuine four-column manual authorization is reused.
    value.control_ref, value.control = new_ref, new_control
    value.registration = register_original_control(value)
    resumed_receipt = sample.old_receipt(new_control["request"], attempts=0,
        parent=value.defer["request"]["claim"])
    value.remote.payloads["push"] = resumed_receipt
    actual_call(effects.deliver_registered_control17, profile=value.profile, store=value.store,
        client=value.client, request_id=new_ref["record_id"])
    actual_call(effects.record_control_receipt17_locked, profile=value.profile, store=value.store,
        request_id=new_ref["record_id"])
    assert tasks.renew_worker(value.owner, profile=value.profile)
    terminal = actual_call(effects.complete_terminal17_locked, profile=value.profile, store=value.store,
        kind="budget_resumed")
    assert terminal["budget_pending"] is None and terminal["source_execution"] == value.core
    readmit = actual_call(effects.create_current_readmit17_locked, profile=value.profile, store=value.store,
        client=value.client)
    readmit_body = readmit["record"]["request"]
    assert readmit_body["dispositions"] == []
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        negative = owner.observe()["entries"]["receipts"][body["request_id"]]["record"]
        assert negative["result"]["original_request_id"] == original["record_id"]
        assert negative["result"]["disposition"] == "not_applied"
    assert readmit_body["defer_receipt"] == value.defer_receipt
    assert readmit_body["resume_receipt"] == resumed_receipt
    admission = sample.admission(readmit_body, attempts=1)
    admission["format_version"] = 2
    admission["admission_sha256"] = wire.admission_sha256(admission)
    value.remote.payloads["readmit"] = admission
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=readmit_body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store, client=value.client,
        request_id=readmit_body["request_id"])
    admitted = actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=readmit_body["request_id"])
    assert admitted["source_execution"] == value.core
    assert admitted["source_authority"]["defer"] is admitted["source_authority"]["resume"] is None
    assert admitted["source_admission"]["admission_id"] == readmit_body["request_id"]
    assert admitted["claims"][0]["attempts"] == 1
    assert budget.get_operation(value.operation, profile=value.profile) == paid_before
    def no_rpc(*args, **kwargs):
        pytest.fail("An immutable original resume replay dispatched again")
    monkeypatch.setattr(value.client, "_request", no_rpc)
    with pytest.raises(SyncError):
        effects.deliver_control17(profile=value.profile, store=value.store, client=value.client,
            request_id=original["record_id"])


@pytest.mark.parametrize("restored_origin17", ["double"], indirect=True)
def test_historical_restore_prefix_reads_the_original_certificate_under_the_complete_current_chain(restored_origin17):
    value = restored_origin17
    original = value.original_restore_ref
    before = deepcopy(value.store.read())
    with value.store.reference_operation(), runtime.capture_historical_prefix_locked(profile=value.profile,
            store=value.store, operation_id=original["operation_id"],
            expected_result_sha256=original["receipt_sha256"], to_epoch=value.core["local_epoch"] + 1) as captured:
        assert type(captured) is runtime.HistoricalRestorePrefixCapture17
        assert not isinstance(captured, runtime.CompletedRestoreCapture17)
        assert captured.provenance_ref() == original
        assert captured.preservation_observation(from_epoch=value.core["local_epoch"]) == value.original_preservation
        with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
            copy_context().run(captured.provenance_ref)
        assert captured._current._version["library_epoch"] == before["local_epoch"] > value.core["local_epoch"] + 1
        with sql.connection(value.profile, write=True) as con:
            captured.verify_for_commit(con, writer_permit=sql.require_writer(con, profile=value.profile))
        captured.verify_source()
    assert value.store.read() == before
    with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
        captured.verify_source()
    # The existing current producer remains strict; this old operation cannot
    # reinterpret a later hop as its own current preservation certificate.
    with value.store.reference_operation(), runtime.capture_completed_locked(profile=value.profile,
            store=value.store, operation_id=original["operation_id"],
            expected_result_sha256=original["receipt_sha256"]) as old_current:
        with pytest.raises(runtime.RuntimeRestoreError, match="CHAIN_INCOMPLETE"):
            old_current.preservation_observation(from_epoch=value.core["local_epoch"])


@pytest.mark.parametrize("restored_origin17", ["double"], indirect=True)
@pytest.mark.parametrize("wrong", ["epoch", "result"])
def test_historical_restore_prefix_rejects_newer_epoch_or_wrong_original_result(restored_origin17, wrong):
    value = restored_origin17
    original = value.original_restore_ref
    expected_sha = ("0" if original["receipt_sha256"][0] != "0" else "1") + original["receipt_sha256"][1:]
    with value.store.reference_operation(), pytest.raises(runtime.RuntimeRestoreError, match="PROVENANCE_INVALID"):
        with runtime.capture_historical_prefix_locked(profile=value.profile, store=value.store,
                operation_id=original["operation_id"],
                expected_result_sha256=expected_sha if wrong == "result" else original["receipt_sha256"],
                to_epoch=value.store.read()["local_epoch"] if wrong == "epoch" else value.core["local_epoch"] + 1):
            pytest.fail("A historical selector filled its missing fact from the current tip")


def negative_original_control(value):
    old = deepcopy(value.control)
    intent = actual_call(effects.create_current_dispose17_locked, profile=value.profile, store=value.store,
        client=value.client, control_request_id=old["record_id"])
    body = intent["record"]["request"]
    answer = sample.disposition(body, applied=False)
    answer.update(format_version=2, core_sha256=body["original"]["core_sha256"],
        item_id=body["original"]["item_id"])
    value.remote.payloads["dispose"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store,
        client=value.client, request_id=body["request_id"])
    actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        receipt = owner.observe()["entries"]["receipts"][body["request_id"]]["record"]
    return old, permanent.record_ref(receipt)


def observe_new_control_revision(value):
    """Receiver effect is isolated; its observation uses the real HTTP client."""
    value.remote.recovery["revision"] += 1
    heartbeat, _, _ = observe(value.remote)
    with value.store.reference_operation():
        value.store.update({"recovery": deepcopy(heartbeat["recovery"])})


@pytest.mark.parametrize("restored_origin17", ["admitted_resume_double"], indirect=True)
def test_actual_prior_admission_three_negative_scopes_then_refund_readmit_preserves_all_ancestors(restored_origin17):
    from reno import sync_source_readmit_local2 as local
    value = restored_origin17
    original_paid = budget.get_operation(value.operation, profile=value.profile)
    prior, history_epoch = deepcopy(value.oldadm_ref), value.admission_receipt["record"]["result"]["current_home_epoch"]
    ancestors, negatives = [], []
    for iteration in range(3):
        assert tasks.renew_worker(value.owner, profile=value.profile)
        old, negative_ref = negative_original_control(value)
        ancestors.append(old)
        negatives.append(negative_ref)
        if iteration:
            observe_new_control_revision(value)
        selected = actual_call(effects.freeze_budget_resume17_locked, profile=value.profile,
            store=value.store, client=value.client)
        with value.store.reference_operation(), permanent.owned(value.store) as owner:
            record = owner.read(selected)
        assert record["format_version"] == 2
        assert record["record_id"] != old["record_id"]
        assert record["creation_basis"]["kind"] == "admission"
        assert record["creation_basis"]["admission_ref"] == prior
        assert record["creation_basis"]["home_epoch"] == history_epoch < record["control_scope"]["home_epoch"]
        assert record["retirement_head"] == negative_ref
        assert record["parent_defer_refs"]["control_ref"] == value.defer_ref
        assert budget.get_operation(value.operation, profile=value.profile) == original_paid
        actual_call(effects.install_control17_locked, profile=value.profile, store=value.store,
            request_id=selected["record_id"])
        value.control_ref, value.control = selected, record
        value.registration = register_original_control(value)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        graph = local.control_retirement_graph2(owner.observe()["entries"], value.control["record_id"])
        assert len(graph["ancestor_refs"]) == 3
        assert graph["retired_disposition_refs"] == negatives
        for record in ancestors:
            assert owner.read(permanent.record_ref(record)) == record
    resumed = sample.old_receipt(value.control["request"], attempts=0, parent=value.defer["request"]["claim"])
    value.remote.payloads["push"] = resumed
    actual_call(effects.deliver_registered_control17, profile=value.profile, store=value.store,
        client=value.client, request_id=value.control["record_id"])
    actual_call(effects.record_control_receipt17_locked, profile=value.profile, store=value.store,
        request_id=value.control["record_id"])
    terminal = actual_call(effects.complete_terminal17_locked, profile=value.profile, store=value.store,
        kind="budget_resumed")
    assert terminal["budget_pending"] is None and terminal["source_execution"] == value.core
    readmit = actual_call(effects.create_current_readmit17_locked, profile=value.profile, store=value.store,
        client=value.client)
    body = readmit["record"]["request"]
    assert body["dispositions"] == [] and body["previous_admission_id"] == prior["admission_id"]
    assert body["defer_receipt"] == value.defer_receipt and body["resume_receipt"] == resumed
    answer = sample.admission(body, attempts=1)
    answer.update(format_version=2)
    answer["admission_sha256"] = wire.admission_sha256(answer)
    value.remote.payloads["readmit"] = answer
    actual_call(effects.install_recovery_pending17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    actual_call(effects.deliver_recovery17, profile=value.profile, store=value.store,
        client=value.client, request_id=body["request_id"])
    admitted = actual_call(effects.reconcile_recovery17_locked, profile=value.profile, store=value.store,
        request_id=body["request_id"])
    assert admitted["source_execution"] == value.core
    assert admitted["source_authority"]["defer"] is admitted["source_authority"]["resume"] is None
    assert budget.get_operation(value.operation, profile=value.profile) == original_paid


@pytest.mark.parametrize("restored_origin17", ["admitted_defer_double", "admitted_failure_double"], indirect=True)
def test_prior_admission_defer_and_failure_new_controls_keep_original_paid_selector(restored_origin17, monkeypatch):
    value = restored_origin17
    failed = value.control["request"]["kind"] == "source_fail"
    operation = value.failed_operation if failed else value.operation
    paid = budget.get_operation(operation, profile=value.profile)
    old, negative = negative_original_control(value)
    def no_new_paid(*args, **kwargs):
        pytest.fail("Historical control completion acquired another paid attempt")
    monkeypatch.setattr(budget, "reserve", no_new_paid)
    selected = actual_call(effects.freeze_source_failure17_locked if failed else effects.freeze_budget_defer17_locked,
        profile=value.profile, store=value.store, client=value.client,
        **({"attempt_id": value.failed_attempt.attempt_id} if failed else {"operation_id": paid.operation_id}))
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = owner.read(selected)
    assert record["format_version"] == 2 and record["retirement_head"] == negative
    assert record["creation_basis"]["admission_ref"] == value.oldadm_ref
    assert record["creation_basis"]["home_epoch"] < record["control_scope"]["home_epoch"]
    if failed:
        assert record["request"]["failure"] == old["request"]["failure"]
    else:
        assert record["request"]["budget_operation_id"] == old["request"]["budget_operation_id"]
    actual_call(effects.install_control17_locked, profile=value.profile, store=value.store,
        request_id=record["record_id"])
    assert budget.get_operation(operation, profile=value.profile) == paid


@pytest.mark.parametrize("restored_origin17", ["admitted_resume_double", "admitted_defer_double", "admitted_failure_double"], indirect=True)
@pytest.mark.parametrize("gap", ["reserved", "record", "heads", "committed"])
def test_control2_original_reservation_survives_actual_publication_gaps_without_fresh_scope(restored_origin17, monkeypatch, gap):
    value = restored_origin17
    control_kind = value.control["request"]["kind"]
    freeze = {"budget_resume": effects.freeze_budget_resume17_locked,
        "budget_defer": effects.freeze_budget_defer17_locked, "source_fail": effects.freeze_source_failure17_locked}[control_kind]
    selectors = ({} if control_kind == "budget_resume" else
        {"operation_id": value.control["request"]["budget_operation_id"]} if control_kind == "budget_defer" else
        {"attempt_id": value.control["request"]["failure"]["attempt_id"]})
    negative_original_control(value)
    state = deepcopy(value.store.read())
    append, write = permanent.AuthorityGuard._append, permanent.AuthorityGuard._write
    injected = {"done": False}
    def crash_append(owner, observed, kind, key, record, phase):
        answer = append(owner, observed, kind, key, record, phase)
        if (not injected["done"] and kind == "controls" and record["request"]["kind"] == control_kind
                and ((gap == "reserved" and phase == "reserved") or (gap == "committed" and phase == "committed"))):
            injected["done"] = True
            raise RuntimeError("Controlled interruption after original " + phase)
        return answer
    def crash_write(owner, directory, name, raw, *, immutable=False):
        answer = write(owner, directory, name, raw, immutable=immutable)
        if not injected["done"] and ((gap == "record" and directory.path.name == "controls")
                or (gap == "heads" and name == permanent.DB_HEAD_NAME)):
            injected["done"] = True
            raise RuntimeError("Controlled interruption after original " + gap)
        return answer
    monkeypatch.setattr(permanent.AuthorityGuard, "_append", crash_append)
    monkeypatch.setattr(permanent.AuthorityGuard, "_write", crash_write)
    with pytest.raises(Exception):
        actual_call(freeze, profile=value.profile, store=value.store, client=value.client, **selectors)
    assert injected["done"] and value.store.read() == state
    monkeypatch.setattr(permanent.AuthorityGuard, "_append", append)
    monkeypatch.setattr(permanent.AuthorityGuard, "_write", write)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        raw = owner.observe_readonly()
        originals = [entry["record"] for entry in raw["entries"]["controls"].values()
            if entry["record"].get("retirement_head") is not None]
        assert len(originals) == 1
        original = originals[0]
        if gap == "heads":
            assert raw["head_status"] == "lagging" and raw["head"] is None
    def no_fresh(*args, **kwargs):
        pytest.fail("Original reservation reacquired continuity or resume authorization")
    monkeypatch.setattr(effects, "_ControlContinuity", no_fresh)
    selected = actual_call(freeze, profile=value.profile, store=value.store, client=value.client, **selectors)
    assert selected == permanent.record_ref(original)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        complete = owner.observe_readonly()
        assert complete["head_status"] == "complete"
        assert owner.read(selected) == original
    assert value.store.read() == state
    installed = actual_call(effects.install_control17_locked, profile=value.profile, store=value.store,
        request_id=selected["record_id"])
    assert installed["source_execution"] == value.core and installed["source_admission"] == value.oldadm_ref
    if control_kind == "source_fail":
        assert installed["source_control_pending"]["request"] == original["request"]
    else:
        key = "resume_request" if control_kind == "budget_resume" else "defer_request"
        assert installed["budget_pending"][key] == original["request"]
