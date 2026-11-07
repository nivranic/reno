"""Actual initial Home17 opportunities consume distinct original SQL tokens.

The normal17 fixture runs the real Cloud ASGI/SQL and closed HTTP parser;
no monkeypatch supplies an owner, permission callback or positive proof.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract, budget_writer_witness as sql
from reno import home_recovery17 as schema17
from reno import sync_source_admission17 as admissions, sync_source_binding17 as bindings
from reno import sync_source_execution as legacy, sync_state, tasks
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create, sql_change


def facts(value):
    create(value)
    return bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.remote.entry["item_id"])


def current(value, binding=None):
    return admissions.capture_current_admission_locked(profile=value.profile, store=value.store,
        client=value.client, item_id=value.remote.entry["item_id"], binding=binding)


def test_one_physical_opportunity_consumes_reserve_and_dispatch_original_tokens(live17):
    binding = facts(live17)
    request_sha = hashlib.sha256(b"isolated actual Home17 model request").hexdigest()
    spec = binding.operation("model", "atomize:initial", hashlib.sha256(b"frozen input").hexdigest())
    policy = budget_contract.parse_policy({"budget_enabled": True})
    observed = []
    with current(live17, binding) as view:
        assert view.kind == "original_core"
        for endpoint in ("heartbeat", "pull", "source/continuity"):
            frame = live17.client._source_claim_broker.latest[endpoint].call.frame
            assert not frame.active and frame.completed and frame.phase == "closed"
        basis = view.creation_basis
        assert basis["origin_ref"] == binding.origin_ref and basis["admission_ref"] is None
        assert basis["claim"] == live17.remote.entry["claim"]
        view.creation_basis["claim"]["lease_generation"] += 1
        assert view.creation_basis == basis
        def guard(con):
            token = sql.require_writer(con, profile=live17.profile)
            observed.append((con, token))
            view.verify_for_commit(con, writer_permit=token)
        reservation = budget.reserve(spec, budget.AttemptSpec(request_sha, 1, "paas_v4", 32),
            profile=live17.profile, policy=policy, reservation_guard=guard)
        dispatch = budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
            str(uuid.uuid4()), expected_revision=reservation.revision, profile=live17.profile,
            policy=policy, dispatch_guard=guard)
        assert dispatch.won
        view.verify_source()
    assert len(observed) == 2 and observed[0][0] is not observed[1][0] and observed[0][1] is not observed[1][1]
    assert not sql.in_write_window()
    assert live17.store._directory is None  # Effect begins after proof owners end.
    for con, token in observed:
        with pytest.raises(sql.BudgetWriterWitnessError):
            sql.check(token, connection=con, profile=live17.profile)
    with pytest.raises(SyncError):
        view.verify_source()
    # Actual late settlement remains independent of new current permission.
    live17.client._source_claim_broker.latest.clear()
    budget.settle(reservation.attempt_id, request_sha, profile=live17.profile, result_text="original result")
    assert budget.get_result(reservation.attempt_id, request_sha, profile=live17.profile).text == "original result"
    assert_current17_runtime()


def test_caller_dtos_and_old_execution_binding_cannot_form_current_view(live17):
    binding = facts(live17)
    old = legacy.HomeExecutionBinding(live17.profile, live17.store, binding.core_json,
        binding.submitted_url, legacy._SEAL)
    for supplied in (old, binding.core, object()):
        with pytest.raises(SyncError):
            with current(live17, supplied):
                pytest.fail("Caller facts produced current Home17 permission")
    with pytest.raises(SyncError):
        admissions.CurrentAdmissionView17(prepared=None, transport=None, initial=None,
            admission=None, restore=None, basis={}, job={}, seal=object())


@pytest.mark.parametrize("change", ["job_expiry", "worker_expiry", "worker_owner", "manual_pause"])
def test_actual_persisted_worker_lease_and_policy_are_current(live17, change):
    binding = facts(live17)
    if change == "job_expiry":
        sql_change(live17, "UPDATE job SET lease_until=0 WHERE job_id=?", (binding.core["root_job_id"],))
    elif change == "worker_expiry":
        sql_change(live17, "UPDATE worker_lease SET lease_until=0")
    elif change == "worker_owner":
        sql_change(live17, "UPDATE worker_lease SET owner_id=?", (str(uuid.uuid4()),))
    else:
        sql_change(live17, "UPDATE job SET manual_paused=1 WHERE job_id=?", (binding.core["root_job_id"],))
    # Original facts remain available; only a new physical opportunity rejects.
    assert bindings.capture_home_binding_locked(profile=live17.profile, store=live17.store,
        item_id=binding.core["item_id"]).core == binding.core
    with pytest.raises((tasks.TaskError, BudgetDeferred, SyncError)):
        with current(live17, binding):
            pytest.fail("Expired/paused worker granted a new opportunity")


def test_heartbeat_total_does_not_extend_specific_initial_claim(live17):
    binding = facts(live17)
    broker = live17.client._source_claim_broker
    old_pull = broker.latest["pull"]
    changed = old_pull.response
    changed["claims"][0]["lease_until"] = "2020-01-01T00:00:00.000Z"
    # Damage only this actual pull's observation. The actual Cloud still owns
    # the original claim; its renewed total cannot extend the observed item.
    live17.remote.corrupt_next("pull", lambda response: response.update(claims=deepcopy(changed["claims"])))
    live17.client.pull(live17.source, live17.remote.lease, claim_limit=1)
    live17.remote.corrupt_next("heartbeat", lambda response: response.update(renewed_claims=1))
    live17.client.heartbeat(live17.source, live17.remote.lease["session_id"], lease=live17.remote.lease)
    live17.store.update({"claims": deepcopy(changed["claims"])})
    with pytest.raises(SyncError):
        with current(live17, binding):
            pytest.fail("Renewed total extended an expired initial claim")


def test_ordinary_reclaim_cannot_replace_original_origin_claim(live17):
    binding = facts(live17)
    changed = deepcopy(live17.remote.entry)
    changed["claim"]["lease_generation"] += 1
    changed["claim"]["lease_token"] = str(uuid.uuid4())
    live17.remote.corrupt_next("pull", lambda response: response.update(claims=[deepcopy(changed)]))
    live17.client.pull(live17.source, live17.remote.lease, claim_limit=1)
    live17.store.update({"claims": [changed]})
    with pytest.raises(SyncError):
        with current(live17, binding):
            pytest.fail("Ordinary pull replaced the permanent origin claim")


def test_live_view_is_bound_to_original_context_thread_and_lifetime(live17):
    binding = facts(live17)
    with current(live17, binding) as view:
        with pytest.raises(SyncError):
            copy_context().run(view.verify_source)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(SyncError):
                executor.submit(view.verify_source).result()
        assert admissions.require_view(view, binding=binding) is view
    with pytest.raises(SyncError):
        admissions.require_view(view)


def test_raw_and_readonly_connections_are_not_budget_writer_permits(live17):
    binding = facts(live17)
    with current(live17, binding) as view:
        con = sqlite3.connect(live17.profile.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises((SyncError, sql.BudgetWriterWitnessError)):
                view.verify_for_commit(con, writer_permit=object())
        finally:
            con.rollback()
            con.close()
        with sql.connection(live17.profile) as readonly:
            with pytest.raises((SyncError, sql.BudgetWriterWitnessError)):
                view.verify_for_commit(readonly, writer_permit=object())


def test_writer_consumption_never_repeats_full_schema_or_journal_scan(live17, monkeypatch):
    binding = facts(live17)
    with current(live17, binding) as view:
        with sql.connection(live17.profile, write=True) as con:
            with monkeypatch.context() as scoped:
                def forbidden(*args, **kwargs):
                    pytest.fail("Full Home17 scan ran in a short business writer")
                scoped.setattr(schema17, "_actual_schema", forbidden)
                token = sql.require_writer(con, profile=live17.profile)
                view.verify_for_commit(con, writer_permit=token)
                con.execute("INSERT INTO meta(key,value) VALUES ('admission17_short_probe','retained')")
                # Keep the ban through the original commit callbacks.
                callbacks = tuple(token._checks)
                for callback in callbacks:
                    callback()
        view.verify_source()
    with sqlite3.connect(live17.profile.database) as con:
        assert con.execute("SELECT value FROM meta WHERE key='admission17_short_probe'").fetchone()[0] == "retained"


@pytest.mark.parametrize("boundary", ["commit", "rollback", "executescript"])
def test_replaced_original_transaction_cannot_finish_current_guard(live17, boundary):
    binding = facts(live17)
    with current(live17, binding) as view:
        with pytest.raises(sql.BudgetWriterWitnessError):
            with sql.connection(live17.profile, write=True) as con:
                token = sql.require_writer(con, profile=live17.profile)
                view.verify_for_commit(con, writer_permit=token)
                if boundary == "commit":
                    con.commit()
                elif boundary == "rollback":
                    con.rollback()
                else:
                    con.executescript("SELECT 1;")
                con.execute("BEGIN IMMEDIATE")
                view.verify_for_commit(con, writer_permit=token)


def test_new_remote_control_observation_invalidates_original_view(live17):
    binding = facts(live17)
    with pytest.raises(SyncError):
        with current(live17, binding) as view:
            def advance_revision(response):
                response["recovery"]["revision"] += 1
            live17.remote.corrupt_next("heartbeat", advance_revision)
            live17.client.heartbeat(live17.source, live17.remote.lease["session_id"], lease=live17.remote.lease)
            view.verify_source()


def test_failed_admission_ref_never_falls_back_to_original_core(live17):
    binding = facts(live17)
    # State5 permits a closed reference representation; no actual receipt,
    # permanent chain or new factory capability exists for this identifier.
    ref = {"format_version": 1, "admission_id": str(uuid.uuid4()), "core_sha256": legacy._sha(binding.core),
        "request_sha256": "a" * 64, "admission_sha256": "b" * 64, "record_sha256": "c" * 64}
    with live17.store.reference_operation():
        forged = live17.store.read()
        forged["source_admission"] = ref
        sync_state._validate(forged)
        # An adversarial/rewound image can contain a well-shaped pointer. Only
        # the actual sealed permanent receipt can establish admission authority.
        sync_state._write(live17.store._directory, sync_state._STATE,
            legacy._canonical(forged).encode())
    with pytest.raises((SyncError, ValueError)):
        with current(live17, binding):
            pytest.fail("Unresolved admission fell back to original_core")


def test_unsealed_head_advance_cannot_change_original_current_permission(live17):
    binding = facts(live17)
    with current(live17, binding) as view:
        original = deepcopy(view._prepared.pins.head)
        for supplied in (object(), {"head": {**original, "ordinal": original["ordinal"] + 2}}, binding):
            with pytest.raises(SyncError):
                view._advance_creation(supplied)
        assert view._prepared.pins.head == original
        view.verify_source()


def admitted(value):
    """Use the real Home creator/HTTP publisher and receipt-first State CAS."""
    from tests.test_sync_source_readmit_local2 import produce_held_receipt
    from reno import sync_source_lifecycle17 as lifecycle
    core, intent, receipt, answer = produce_held_receipt(value)
    lifecycle.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=intent["record"]["record_id"])
    try:
        lifecycle.reconcile_recovery17_locked(profile=value.profile, store=value.store,
            request_id=intent["record"]["record_id"])
    except BaseException as error:
        original, seen = error, set()
        while original.__context__ is not None and id(original) not in seen:
            seen.add(id(original))
            original = original.__context__
        if original is not error:
            raise original from None
        raise
    detached = bindings.capture_home_binding_locked(profile=value.profile, store=value.store, item_id=core["item_id"])
    return detached, intent, receipt, answer


def test_actual_same_epoch_readmit_current_view_needs_no_ordinary_pull(live17):
    binding, intent, receipt, answer = admitted(live17)
    original = intent["record"]["request"]["original"]
    assert legacy._sha(binding.core) == original["core_sha256"]
    assert (binding.core["local_epoch"], binding.core["recovery_generation"], binding.core["recovery_stream"]) == (
        original["home_epoch"], original["recovery_generation"], original["recovery_stream"])
    # The original pull still contains the old token. Its absence or expiry
    # does not replace the separately committed targeted admission's claim.
    live17.client._source_claim_broker.latest.pop("pull")
    with current(live17, binding) as view:
        assert view.kind == "admission"
        assert view.creation_basis["claim"] == answer["claim"]
        assert view.creation_basis["admission_ref"]["record_sha256"] == receipt["ref"]["record_sha256"]
        assert view._restore is None and view._admission.preservation_capture is None
        with sql.connection(live17.profile, write=True) as con:
            token = sql.require_writer(con, profile=live17.profile)
            view.verify_for_commit(con, writer_permit=token)
        with sql.connection(live17.profile, write=True) as con:
            token = sql.require_writer(con, profile=live17.profile)
            view.verify_for_commit(con, writer_permit=token)
        view.verify_source()
    assert binding.core == live17.store.read()["source_execution"]
    assert_current17_runtime()


def test_new_ordinary_claim_cannot_replace_saved_targeted_readmission(live17):
    binding, _, _, answer = admitted(live17)
    replacement = deepcopy(live17.store.read()["claims"][0])
    replacement["claim"]["lease_generation"] += 1
    replacement["claim"]["lease_token"] = str(uuid.uuid4())
    live17.store.update({"claims": [replacement]})
    with pytest.raises(SyncError):
        with current(live17, binding):
            pytest.fail("New claim replaced the saved admission's exact token")
    assert replacement["claim"] != answer["claim"]


def test_actual_frozen_defer_state_gap_cannot_reopen_original_core(live17):
    from tests.test_sync_source_lifecycle17 import waiting_control
    try:
        core, _, ref, record = waiting_control(live17)
    except BaseException as error:
        codes, current_error, seen = [], error, set()
        while current_error is not None and id(current_error) not in seen and len(codes) < 8:
            seen.add(id(current_error))
            codes.append(type(current_error).__name__ + ":" + str(getattr(current_error, "code", "")))
            current_error = current_error.__context__
        error.add_note("Actual producer error codes: " + ", ".join(codes))
        raise
    state = live17.store.read()
    assert state["source_authority"]["defer"] is None and state["budget_pending"] is None
    assert record["request"]["kind"] == "budget_defer" and record["record_id"] == ref["record_id"]
    detached = bindings.capture_home_binding_locked(profile=live17.profile,
        store=live17.store, item_id=core["item_id"])
    assert detached.core == core  # Original paid identity stays readable.
    with pytest.raises(SyncError):
        with current(live17, detached):
            pytest.fail("Committed defer before State CAS reopened the original source")


def test_actual_control_frozen_after_admission_cannot_reuse_that_admission(live17):
    from reno import sync_source_lifecycle17 as lifecycle
    binding, _, _, answer = admitted(live17)
    core = binding.core
    operation = budget.OperationSpec(core["continuation_id"], core["execution_generation"], "model",
        "atomize:admitted-original-key", hashlib.sha256(b"admitted frozen model input").hexdigest(),
        core["root_job_id"])
    with current(live17, binding) as view:
        def guard(con):
            view.verify_for_commit(con, writer_permit=sql.require_writer(con, profile=live17.profile))
        with pytest.raises(BudgetDeferred) as deferred:
            budget.reserve(operation, budget.AttemptSpec(hashlib.sha256(b"admitted original request").hexdigest(),
                1, "paas_v4", 32), profile=live17.profile,
                policy=budget_contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0}),
                now=datetime.now(timezone.utc) - timedelta(days=2), reservation_guard=guard)
    ref = lifecycle.freeze_budget_defer17_locked(profile=live17.profile, store=live17.store,
        client=live17.client, operation_id=deferred.value.operation_id)
    assert live17.store.read()["source_authority"]["defer"] is None
    assert live17.store.read()["budget_pending"] is None
    assert live17.store.read()["source_admission"]["admission_id"] == answer["admission_id"]
    assert ref["record_kind"] == "control"
    with pytest.raises(SyncError):
        with current(live17, binding):
            pytest.fail("A new durable control reused the preceding admission")
