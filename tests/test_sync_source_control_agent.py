"""Source failure delivery uses real original Home ledger and durable state."""
from copy import deepcopy
import hashlib
import json
import threading
import uuid

import pytest

from reno import budget, config, db, ingest, source_budget, source_resolution as adapter
from reno import source_resolution_budget as resolver, source_resolution_contract as codec
from reno import source_terminal, sync_agent as agent, sync_claims, sync_home_status, sync_source_execution, sync_sources, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_source_terminal import failed_source, genesis
from tests.test_sync_agent import add_video, env, install_import
from tests.test_sync_budget_propagation import home_cycle, raw_job
from tests.test_sync_source_control_state import control_receipt
from tests.test_sync_source_execution_binding import bind, executing
from tests.test_sync_source_execution_state import request


def failure_transport(value, monkeypatch, *, lose_reply=False, corrupt_reply=False):
    calls, receipts = [], {}
    def deliver(body):
        pending = value.store.assert_source_control_pending()
        assert pending["request"] == body and pending["receipt"] is None
        calls.append(deepcopy(body))
        if body["request_id"] not in receipts:
            receipts[body["request_id"]] = control_receipt(body)
            # The Cloud has permanently accepted this exact original failure.
            value.client.claims = [entry for entry in value.client.claims
                                   if entry["item_id"] != body["claim"]["item_id"]]
            if lose_reply:
                raise SyncError("synthetic lost failure reply", "SYNC_CLIENT_NETWORK", 503)
        result = {**receipts[body["request_id"]], "replayed": len(calls) > 1}
        if corrupt_reply:
            result["failure_sha256"] = "f" * 64
        return result
    monkeypatch.setattr(value.client, "source_control", deliver, raising=False)
    return calls


def original_failure(value):
    binding = bind(value)
    _, attempt, _ = failed_source(binding)
    return binding, source_terminal.known_failure(binding), attempt


def history_bytes(value):
    return {path.name: path.read_bytes() for path in
            (value.store.data_dir / "sync-work-history").glob("*.json")}


def strict_metadata_failure(monkeypatch):
    config.set_local({"budget_enabled": True})
    monkeypatch.setattr(resolver, "resolve_url", lambda binding, **kw:
                        sync_sources.resolve_url(binding.submitted_url))
    def fail(url, **kw):
        binding = source_budget.current_scope().home_execution
        failed_source(binding)
        raise source_budget.SourceProcessError()
    monkeypatch.setattr(ingest, "ingest_url", fail)


def original_publication(value, monkeypatch, *, version, acknowledged):
    config.set_local({"budget_enabled": True})
    binding = bind(value)
    genesis(binding)
    if version == 4:
        value.store.enable_source_control()
    # Controlled accepted content; no provider or processing step is dispatched.
    path = add_video("BVfake123")
    resolution = sync_sources.resolve_url(binding.submitted_url)
    with db.connect(value.storage_profile.database) as con:
        con.execute("UPDATE video_asset SET source_type='url',source_platform='bilibili',source_url=? WHERE video_id='BVfake123'",
                    (resolution["resolved_url"],))
        con.commit()
    origin = {"origin_version": 1, "source_library_id": value.source["library_id"],
        "source_node_id": value.source["node_id"], "submitted_url": binding.submitted_url,
        "resolved_url": resolution["resolved_url"], "resolution_sha256": "d" * 64,
        "extracted_video_id": "BVfake123", "video_id": "BVfake123",
        "media_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "resolved_at": sync_sources._now(), "recorded_at": sync_sources._now()}
    origin["mapping_id"] = sync_claims._origin_digest(origin)
    bindings = [{"video_id": "BVfake123", "claim": value.store.read()["claims"][0]["claim"], "origin": origin}]
    value.freeze(bindings)
    metadata = value.store.read()["pending_publication"]
    envelope = value.store.read_publication(metadata, proved_stream=binding.core["recovery_stream"])
    response = value.client.push_snapshot(value.source, value.lease(), envelope["snapshot"], envelope["claims"],
        recovery_generation=binding.core["recovery_generation"], recovery_stream=binding.core["recovery_stream"])
    request = value.store.record_published_terminal(metadata, response,
        recovery_generation=binding.core["recovery_generation"], recovery_stream=binding.core["recovery_stream"])
    value.store.complete_published_source(binding.core, request)
    if acknowledged:
        assert value._complete_published(request)
    return binding, request


def test_normal_paid_metadata_failure_saves_original_control_before_rpc_and_closes_only_core(env, monkeypatch):
    install_import(env, monkeypatch)
    strict_metadata_failure(monkeypatch)
    with home_cycle(env) as value, executing(value):
        original = value.store.read()
        calls = failure_transport(value, monkeypatch)
        with pytest.raises(agent._SourceFailed):
            value.process_claim()
        after = value.store.read()
        assert after["format_version"] == 4 and after["source_execution"] is None
        assert after["source_control_pending"] is None and after["source_admission"] is None
        assert all(after[key] == original.get(key) for key in ("claims", "work", "budget_pending", "pending_publication"))
        assert len(calls) == 1 and calls[0]["kind"] == "source_fail"
        assert calls[0]["claim"] == original["claims"][0]["claim"]
        assert calls[0]["submitted_url_sha256"] == hashlib.sha256(original["claims"][0]["url"].encode()).hexdigest()
        assert original["claims"][0]["url"] not in json.dumps(calls[0])
        saved = json.loads(next(iter(history_bytes(value).values())))
        assert saved["reason"] == "source_failed" and saved["source_control_pending"]["request"] == calls[0]
        assert after["status"]["state"] == "source_failed"
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


def test_normal_paid_resolver_rejection_uses_its_settled_original_failure(env, monkeypatch):
    install_import(env, monkeypatch)
    config.set_local({"budget_enabled": True})
    def reject(prepared, **kwargs):
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", True, "completed",
            dns_error="non_public", failure_class="dns_rejected", observed_at="2026-10-02T00:00:00.000000Z")
    monkeypatch.setattr(adapter, "run", reject)
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("Rejected resolver cannot ingest"))
    with home_cycle(env) as value, executing(value):
        calls = failure_transport(value, monkeypatch)
        with pytest.raises(agent._SourceFailed):
            value.process_claim()
        assert calls[0]["failure"]["operation_kind"] == "source_resolution"
        assert calls[0]["failure"]["error_class"] == "invalid_url"


def test_lost_failure_reply_replays_original_uuid_body_and_lease_without_new_source(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        saved = value.store.assert_source_control_pending()
        assert saved["receipt"] is None and value.store.read()["status"]["state"] == "pending_delivery"
        value.store.update({"lease": {**value.store.read()["lease"], "generation": 2,
                                     "lease_token": str(uuid.uuid4())}})
        monkeypatch.setattr(resolver, "resolve_url", lambda *a, **k: pytest.fail("Control replay cannot resolve"))
        with pytest.raises(agent._SourceFailed):
            value.resume_source_control()
        assert calls == [saved["request"], saved["request"]]
        assert calls[-1]["lease"] != value.store.read()["lease"]
        assert value.store.read()["source_execution"] is None and len(history_bytes(value)) == 1


def test_saved_failure_receipt_recovers_without_rpc_and_reuses_original_history(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch)
        original = value.store.complete_source_failure
        def interrupted(core, pending, **kwargs):
            raise RuntimeError("synthetic history interruption")
        monkeypatch.setattr(value.store, "complete_source_failure", interrupted)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        saved = value.store.assert_source_control_pending()
        assert saved["receipt"] is not None and len(calls) == 1
        monkeypatch.setattr(value.store, "complete_source_failure", original)
        monkeypatch.setattr(value.client, "source_control", lambda *a: pytest.fail("Stored receipt cannot RPC"))
        with pytest.raises(agent._SourceFailed):
            value.resume_source_control()
        assert value.store.read()["source_execution"] is None and len(history_bytes(value)) == 1


def test_next_owned_invocation_keeps_original_job_and_failure_control_body(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        with executing(value) as (job, owner):
            binding, proof, _ = original_failure(value)
            calls = failure_transport(value, monkeypatch, lose_reply=True)
            with pytest.raises(BudgetDeferred) as caught:
                value.fail_source(binding, proof)
            assert tasks.defer(job["job_id"], owner, caught.value, profile=value.storage_profile)
        body = value.store.assert_source_control_pending()["request"]
        tasks.resume_sync(job["job_id"], profile=value.storage_profile)
        with executing(value) as (again, new_owner):
            assert again["job_id"] == job["job_id"] and new_owner != owner
            with pytest.raises(agent._SourceFailed):
                value.resume_source_control()
        assert calls == [body, body]


@pytest.mark.parametrize("change", ["fence", "budget", "publication"])
def test_failure_receipt_does_not_close_unproved_fence_wait_or_publication(env, monkeypatch, change):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        core = value.store.read()["source_execution"]
        if change == "fence":
            recovery = value.store.read()["recovery"]
            value.store.observe_recovery({**recovery, "generation": 1, "revision": 1})
        elif change == "budget":
            value.store.install_budget_defer_request(request(core))
        else:
            # Real outbox/freeze remains an independent root and terminal.
            value.freeze([])
        before = value.store.read()
        with pytest.raises(BudgetDeferred):
            value.resume_source_control()
        after = value.store.read()
        assert after["source_execution"] == core and after["source_control_pending"]["receipt"] is not None
        assert all(after[key] == before[key] for key in ("claims", "work", "budget_pending", "pending_publication"))
        assert after["status"]["state"] == "source_blocked" and len(calls) == 2


def test_control_replay_rechecks_paid_settlement_before_rpc(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, attempted = original_failure(value)
        calls = failure_transport(value, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        before = value.store.read()
        with budget._connection(value.storage_profile, write=True) as con:
            con.execute("UPDATE budget_attempt SET settlement_sha256=? WHERE attempt_id=?", ("e" * 64, attempted.attempt_id))
        with pytest.raises(BudgetDeferred):
            value.resume_source_control()
        after = value.store.read()
        assert after["source_execution"] == before["source_execution"]
        assert after["source_control_pending"] == before["source_control_pending"] and len(calls) == 1


def test_actual_database_epoch_change_does_not_close_still_old_state_core(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        before = value.store.read()
        with budget._connection(value.storage_profile, write=True) as con:
            con.execute("UPDATE meta SET value=? WHERE key='library_epoch'", (str(binding.core["local_epoch"] + 1),))
        assert value.store.read()["local_epoch"] == before["local_epoch"]
        with pytest.raises(BudgetDeferred):
            value.resume_source_control()
        after = value.store.read()
        assert after["source_execution"] == binding.core
        assert after["source_control_pending"]["receipt"] is not None and len(calls) == 2
        assert after["status"]["state"] == "source_blocked"


def test_malformed_remote_receipt_preserves_original_intent_and_waits(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch, corrupt_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        assert len(calls) == 1 and value.store.assert_source_control_pending()["receipt"] is None
        assert value.store.read()["status"]["error_code"] == "SYNC_SOURCE_CONTROL_DELIVERY_PENDING"


def test_generic_pipeline_error_cannot_close_using_an_unrelated_known_source_failure(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        # This error arose in later local processing, outside resolver/ingest.
        monkeypatch.setattr(resolver, "resolve_url", lambda binding, **kw:
                            sync_sources.resolve_url(binding.submitted_url))
        monkeypatch.setattr(value, "advance_work", lambda work: (_ for _ in ()).throw(RuntimeError("local pipeline error")))
        calls = failure_transport(value, monkeypatch)
        with pytest.raises(BudgetDeferred):
            value.process_claim()
        assert calls == [] and value.store.read().get("source_control_pending") is None
        assert value.store.read()["source_execution"] == binding.core


def test_execute_prioritizes_original_failure_control_before_budget_or_processing(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        calls = failure_transport(value, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            value.fail_source(binding, proof)
        monkeypatch.setattr(value, "resume_budget", lambda: pytest.fail("Failure pending must run first"))
        monkeypatch.setattr(value, "process_claim", lambda: pytest.fail("Failure pending cannot process"))
        monkeypatch.setattr(value, "publish", lambda: pytest.fail("Failure pending cannot publish"))
        with pytest.raises(agent._SourceFailed):
            value.execute()
        assert len(calls) == 2


def test_run_cycle_returns_closed_failure_without_reporting_or_publishing(env, monkeypatch):
    install_import(env, monkeypatch)
    strict_metadata_failure(monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        failure_transport(value, monkeypatch)
        profile = value.storage_profile
    monkeypatch.setattr(agent.report, "run_all", lambda: pytest.fail("Source failure cannot report"))
    monkeypatch.setattr(agent._Cycle, "publish", lambda self: None)
    with tasks.process_lock(profile=profile) as acquired, worker._execution(job, owner, threading.Event(), profile=profile):
        assert acquired
        result = agent.run_cycle(client=env)
        assert result == {"state": "source_failed", "error_code": "SYNC_SOURCE_FAILED"}
        assert not env.publications


def test_state_clear_before_worker_finish_recovers_same_job_without_any_rpc_or_new_work(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        binding, proof, _ = original_failure(value)
        failure_transport(value, monkeypatch)
        with pytest.raises(agent._SourceFailed):
            value.fail_source(binding, proof)
        # Model the process dying immediately after its successful state write.
        agent._status(value.store, "processing")
        profile = value.storage_profile
        assert raw_job(profile, job["job_id"])["status"] == "running"
    monkeypatch.setattr(env, "handshake", lambda: pytest.fail("Stored terminal cannot handshake"))
    monkeypatch.setattr(env, "source_control", lambda *a: pytest.fail("Stored terminal cannot resend"))
    monkeypatch.setattr(agent._Cycle, "acquire", lambda *a, **k: pytest.fail("Stored terminal cannot acquire"))
    monkeypatch.setattr(agent._Cycle, "process_claim", lambda *a: pytest.fail("Stored terminal cannot process"))
    monkeypatch.setattr(agent.report, "run_all", lambda: pytest.fail("Stored terminal cannot report"))
    with tasks.process_lock(profile=profile) as acquired, worker._execution(job, owner, threading.Event(), profile=profile):
        assert acquired
        result = agent.run_cycle(client=env)
        assert result == {"state": "source_failed", "error_code": "SYNC_SOURCE_FAILED"}
        assert tasks.finish(job["job_id"], owner, result=result, profile=profile)
    assert raw_job(profile, job["job_id"])["status"] == "done"


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_published_source_clear_before_finish_recovers_original_ack_and_success_without_new_work(env, monkeypatch, version, acknowledged):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        binding, request = original_publication(value, monkeypatch, version=version, acknowledged=acknowledged)
        profile = value.storage_profile
        before = value.store.read()
        assert before["source_execution"] is None
        assert (before["pending_publication"] is None) is acknowledged
    monkeypatch.setattr(env, "handshake", lambda: pytest.fail("Permanent publication cannot handshake"))
    monkeypatch.setattr(agent._Cycle, "acquire", lambda *a, **k: pytest.fail("Permanent publication cannot acquire"))
    monkeypatch.setattr(agent._Cycle, "process_claim", lambda *a: pytest.fail("Permanent publication cannot process"))
    monkeypatch.setattr(agent.report, "run_all", lambda: pytest.fail("Permanent publication cannot report"))
    with tasks.process_lock(profile=profile) as acquired, worker._execution(job, owner, threading.Event(), profile=profile):
        assert acquired
        result = agent.run_cycle(client=env)
        assert result == {"state": "published", "published": True}
        assert tasks.finish(job["job_id"], owner, result=result, profile=profile)
    assert raw_job(profile, job["job_id"])["status"] == "done"
    state = value.store.read()
    assert state["source_execution"] is None and state["pending_publication"] is None
    assert value.store.no_resend_request(request["operation_id"]) == request
    assert len(env.publications) == 1


@pytest.mark.parametrize("damage", ["receipt", "outbox", "ack"])
def test_published_completion_requires_original_permanent_receipt_outbox_and_exact_ack(env, monkeypatch, damage):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, request = original_publication(value, monkeypatch, version=4, acknowledged=True)
        if damage == "receipt":
            monkeypatch.setattr(value.store, "no_resend_receipt", lambda *a, **kw: None)
        elif damage == "outbox":
            (value.store.data_dir / "sync-outbox" / (request["metadata"]["publication_id"] + ".json")).write_bytes(b"synthetic damaged outbox")
        else:
            value.store.update({"published_content_sha256": "e" * 64})
        monkeypatch.setattr(value, "acquire", lambda *a, **kw: pytest.fail("Unproved publication cannot acquire"))
        monkeypatch.setattr(value, "process_claim", lambda: pytest.fail("Unproved publication cannot process"))
        with pytest.raises(BudgetDeferred):
            value.execute()
        assert value.store.read()["source_execution"] is None
        assert value.store.read()["status"]["state"] == "source_blocked"
        assert len(env.publications) == 1


def test_completed_publication_binding_cannot_be_borrowed_by_new_job(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        with executing(value) as (job, owner):
            binding, request = original_publication(value, monkeypatch, version=4, acknowledged=True)
            history = value.store.completed_source_publication(root_job_id=job["job_id"],
                continuation_id=job["continuation_id"], execution_generation=job["execution_generation"])
            completed = sync_source_execution.completed_binding(value.store, value.storage_profile, history)
            with pytest.raises((SyncError, budget.BudgetLedgerError)):
                completed.check()
            assert tasks.finish(job["job_id"], owner, result={"state": "published"}, profile=value.storage_profile)
        with executing(value) as (another, _):
            assert another["job_id"] != job["job_id"]
            assert value.resume_completed_source_failure() is False
            with pytest.raises((SyncError, budget.BudgetLedgerError)):
                sync_source_execution.completed_binding(value.store, value.storage_profile, history)


def test_completed_failure_read_binding_cannot_dispatch_or_be_borrowed_by_new_job(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        with executing(value) as (job, owner):
            binding, proof, _ = original_failure(value)
            failure_transport(value, monkeypatch)
            with pytest.raises(agent._SourceFailed):
                value.fail_source(binding, proof)
            history = value.store.completed_source_failure(root_job_id=job["job_id"],
                continuation_id=job["continuation_id"], execution_generation=job["execution_generation"])
            completed = sync_source_execution.completed_binding(value.store, value.storage_profile, history)
            assert source_terminal.revalidate_failure(completed, history["source_control_pending"]["request"]["failure"]).public() == proof.public()
            with pytest.raises((budget.BudgetLedgerError, SyncError)):
                completed.check()
            damaged = deepcopy(history)
            damaged["source_control_pending"]["request"]["failure"]["settlement_sha256"] = "e" * 64
            with pytest.raises((budget.BudgetLedgerError, SyncError)):
                sync_source_execution.completed_binding(value.store, value.storage_profile, damaged)
            assert tasks.finish(job["job_id"], owner, result={"state": "source_failed"}, profile=value.storage_profile)
        with executing(value) as (another, _):
            assert another["job_id"] != job["job_id"] and another["continuation_id"] != job["continuation_id"]
            assert value.resume_completed_source_failure() is False
            with pytest.raises((budget.BudgetLedgerError, SyncError)):
                sync_source_execution.completed_binding(value.store, value.storage_profile, history)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "epoch"])
def test_lost_or_stale_completion_evidence_blocks_same_paid_job_before_any_source_rpc(env, monkeypatch, damage):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding, proof, _ = original_failure(value)
        failure_transport(value, monkeypatch)
        with pytest.raises(agent._SourceFailed):
            value.fail_source(binding, proof)
        original = next((value.store.data_dir / "sync-work-history").glob("*.json"))
        if damage == "missing":
            original.unlink()
        elif damage == "corrupt":
            original.write_bytes(b"not-json")
        else:
            with budget._connection(value.storage_profile, write=True) as con:
                con.execute("UPDATE meta SET value=? WHERE key='library_epoch'", (str(binding.core["local_epoch"] + 1),))
        monkeypatch.setattr(env, "handshake", lambda: pytest.fail("Unproved terminal cannot handshake"))
        monkeypatch.setattr(value, "acquire", lambda *a, **k: pytest.fail("Unproved terminal cannot acquire"))
        monkeypatch.setattr(value, "process_claim", lambda: pytest.fail("Unproved terminal cannot process"))
        with pytest.raises(BudgetDeferred):
            value.execute()
        assert value.store.read()["source_execution"] is None
        assert value.store.read()["status"]["state"] == "source_blocked"


@pytest.mark.parametrize("label,code", [("source_failed", "SYNC_SOURCE_FAILED"),
                                      ("pending_delivery", "SYNC_SOURCE_CONTROL_DELIVERY_PENDING")])
def test_failure_public_projection_is_closed_and_contains_no_original_private_proof(env, monkeypatch, label, code):
    install_import(env, monkeypatch)
    config.set_local({"sync_token": "synthetic-test-only-token"})
    with home_cycle(env) as value, executing(value):
        binding, _, _ = original_failure(value)
        agent._status(value.store, label, error=code)
        summary = sync_home_status.summary()
        assert summary["state"] == label and summary["error_code"] == code
        text = json.dumps(summary)
        assert all(secret not in text for secret in (binding.core["continuation_id"], binding.submitted_url,
            binding.core["submitted_url_sha256"], "source_control_pending", "settlement_sha256", "failure"))
