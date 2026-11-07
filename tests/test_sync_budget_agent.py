"""Home budget terminal histories use real disposable StateStore images."""
from copy import deepcopy
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from reno import config, db, ingest, source_budget, source_resolution_budget, storage_admin, sync_agent as agent
from reno import sync_claims, sync_home_status, sync_snapshot, sync_sources, sync_state, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_sync_source_execution_state import enable, execution, ready, request, receipt, resume
from tests.test_sync_state import claim, store
from tests.test_sync_agent import add_video, env, install_import
from tests.test_sync_budget_propagation import home_cycle, raw_job
from tests.test_sync_source_execution_binding import bind, executing


def resumed(store):
    core = ready(store)
    original = request(core)
    store.install_budget_defer_request(original)
    store.record_budget_defer_receipt(original["request_id"], receipt(original))
    followup = resume(original)
    store.install_budget_resume_request(followup)
    store.record_budget_resume_receipt(followup["request_id"], receipt(followup))
    return core, store.assert_budget_pending()


def history_files(store):
    root = store.data_dir / "sync-work-history"
    return {path.name: path.read_bytes() for path in root.glob("*.json")} if root.exists() else {}


def test_resume_terminal_archives_original_proofs_and_only_clears_budget(store):
    core, pending = resumed(store)
    before = store.read()
    after = store.complete_budget_resume(core, pending)
    assert after == {**before, "budget_pending": None}
    saved = history_files(store)
    assert len(saved) == 1
    history = json.loads(next(iter(saved.values())))
    assert sync_state.validate_source_history(history) == history
    assert history["source_execution"] == core and history["budget_pending"] == pending
    assert history["reason"] == history["source_terminal"]["kind"] == "budget_resumed"
    assert store.complete_budget_resume(core, pending) == after
    assert history_files(store) == saved


def test_terminal_history_survives_state_write_crash_and_reuses_original_bytes(store, monkeypatch):
    core, pending = resumed(store)
    before = (store.data_dir / "sync-state.json").read_bytes()
    original = sync_state._write
    def interrupt(directory, name, data, **options):
        if name == "sync-state.json":
            raise RuntimeError("synthetic state write crash")
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError):
        store.complete_budget_resume(core, pending)
    assert (store.data_dir / "sync-state.json").read_bytes() == before
    saved = history_files(store)
    assert len(saved) == 1
    monkeypatch.setattr(sync_state, "_write", original)
    store.complete_budget_resume(core, pending)
    assert history_files(store) == saved


@pytest.mark.parametrize("mutation", ["core", "receipt", "fence", "missing_resume"])
def test_stale_or_unproved_resume_never_clears_original_pointer(store, mutation):
    core, pending = resumed(store)
    if mutation == "core":
        core = {**core, "root_job_id": core["root_job_id"] + 1}
    elif mutation == "receipt":
        pending["resume_receipt"]["attempts"] += 1
    elif mutation == "fence":
        recovery = store.read()["recovery"]
        store.observe_recovery({**recovery, "revision": 1, "generation": 1})
    else:
        pending["resume_receipt"] = None
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.complete_budget_resume(core, pending)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["unknown", "bool", "sha", "kind", "reason", "proof"])
def test_history3_parser_rejects_damaged_or_open_terminal_contract(store, mutation):
    core, pending = resumed(store)
    store.complete_budget_resume(core, pending)
    history = json.loads(next(iter(history_files(store).values())))
    if mutation == "unknown":
        history["private_url"] = "https://private.invalid"
    elif mutation == "bool":
        history["source_terminal"]["format_version"] = True
    elif mutation == "sha":
        history["source_terminal"]["core_sha256"] = "f" * 64
    elif mutation == "kind":
        history["source_terminal"]["kind"] = "published"
    elif mutation == "reason":
        history["reason"] = "recovery"
    else:
        history["source_terminal"]["proof"]["resume_receipt"]["item_id"] += 1
    with pytest.raises(SyncError):
        sync_state.validate_source_history(history)


def test_nonterminal_history3_preserves_core_and_pending_wait(store):
    core = ready(store)
    original = request(core)
    store.install_budget_defer_request(original)
    before = store.read()
    store.archive_progress("recovery")
    history = json.loads(next(iter(history_files(store).values())))
    assert history["format_version"] == 3 and history["source_terminal"] is None
    assert history["source_execution"] == core and history["budget_pending"] == before["budget_pending"]
    assert sync_state.validate_source_history(history) == history
    assert store.read() == before


def published_source(store, *, record=True):
    enable(store)
    binding = store.read()["binding"]
    item = claim(7)
    core = execution(store)
    core["submitted_url_sha256"] = hashlib.sha256(item["url"].encode()).hexdigest()
    store.install_source_execution(core)
    db.initialize(store.profile.database)
    con = db.connect(store.profile.database)
    try:
        con.execute("UPDATE meta SET value=? WHERE key='library_id'", (binding["home_library_id"],))
        con.execute("INSERT INTO video_asset(video_id,source_platform,source_type,source_url,content_sha256,status) "
                    "VALUES('BV1Test','bilibili','url',? ,?,'processed')", (item["url"], "c" * 64))
        con.commit()
        con.execute("BEGIN")
        snapshot = sync_snapshot.export_snapshot(con, publication_id=str(uuid.uuid4()),
            media={"BV1Test": {"video_id": "BV1Test", "sha256": "c" * 64, "byte_size": 13, "extension": "mp4"}})
    finally:
        con.close()
    origin = {"origin_version": 1, "source_library_id": binding["home_library_id"],
        "source_node_id": binding["home_node_id"], "submitted_url": item["url"],
        "resolved_url": item["url"], "resolution_sha256": "d" * 64,
        "extracted_video_id": "BV1Test", "video_id": "BV1Test", "media_sha256": "c" * 64,
        "resolved_at": "2026-10-02T00:00:00.000000Z", "recorded_at": "2026-10-02T00:00:01.000000Z"}
    origin["mapping_id"] = sync_claims._origin_digest(origin)
    metadata = store.write_publication(snapshot, [{"video_id": "BV1Test", "claim": item["claim"], "origin": origin}],
                                      recovery_stream=core["recovery_stream"])
    store.update({"pending_publication": metadata, "claims": [item]})
    if not record:
        return core, metadata
    result = {"publication_id": metadata["publication_id"], "source_version": snapshot["version"],
        "local_version": {**core["recovery_stream"], "kb_revision": 9},
        "committed_at": "2026-10-02T00:00:02Z", "replayed": False}
    original = store.record_published_terminal(metadata, result,
        recovery_generation=core["recovery_generation"], recovery_stream=core["recovery_stream"])
    return core, original


def test_published_source_archives_permanent_receipt_and_original_claims_before_core_clear(store):
    core, original = published_source(store)
    before = store.read()
    after = store.complete_published_source(core, original)
    assert after == {**before, "source_execution": None}
    history = json.loads(next(iter(history_files(store).values())))
    assert sync_state.validate_source_history(history) == history
    proof = history["source_terminal"]["proof"]
    assert proof["request"] == original
    assert proof["response"] == store.no_resend_receipt(original["operation_id"])
    assert proof["claims"] == store.read_publication(original["metadata"])["claims"]
    assert after["claims"] == before["claims"] and after["pending_publication"] == original["metadata"]
    saved = history_files(store)
    assert store.complete_published_source(core, original) == after
    assert history_files(store) == saved


@pytest.mark.parametrize("mutation", ["foreign_item", "foreign_url", "fence", "missing_receipt"])
def test_published_source_without_original_terminal_authority_cannot_clear(store, mutation):
    core, original = published_source(store)
    if mutation in {"foreign_item", "foreign_url"}:
        core = deepcopy(core)
        if mutation == "foreign_item":
            core["item_id"] += 1
        else:
            core["submitted_url_sha256"] = "f" * 64
    elif mutation == "fence":
        recovery = store.read()["recovery"]
        store.observe_recovery({**recovery, "generation": 1, "revision": 1})
    else:
        original = {**original, "operation_id": str(uuid.uuid4())}
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.complete_published_source(core, original)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_cleared_pending_without_original_history_cannot_manufacture_terminal(store):
    core = ready(store)
    original = request(core)
    pending = {"defer_request": original, "defer_receipt": receipt(original),
               "resume_request": resume(original), "resume_receipt": None}
    pending["resume_receipt"] = receipt(pending["resume_request"])
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.complete_budget_resume(core, pending)
    assert (store.data_dir / "sync-state.json").read_bytes() == before
    assert history_files(store) == {}


@pytest.mark.parametrize("changed_field", ["generation", "stream"])
def test_low_level_resume_request_cannot_cross_original_execution_fence(store, changed_field):
    core = ready(store)
    original = request(core)
    store.install_budget_defer_request(original)
    store.record_budget_defer_receipt(original["request_id"], receipt(original))
    followup = resume(original)
    if changed_field == "generation":
        followup["recovery_generation"] += 1
    else:
        followup["recovery_stream"]["library_epoch"] += 1
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.install_budget_resume_request(followup)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def budget_transport(value, monkeypatch, *, lose_defer=False, lose_resume=False):
    calls, receipts = [], {}
    def deliver(body, *, defer_receipt=None):
        # The exact original body must exist locally before transport begins.
        pending = value.store.assert_budget_pending()
        field = "defer_request" if body["kind"] == "budget_defer" else "resume_request"
        assert pending[field] == body
        calls.append(deepcopy(body))
        if body["request_id"] not in receipts:
            item = body["claim"] if body["kind"] == "budget_defer" else defer_receipt
            proof = receipt(body, item_id=item["item_id"],
                lease_generation=item["lease_generation"])
            receipts[body["request_id"]] = proof
            if (body["kind"] == "budget_defer" and lose_defer or body["kind"] == "budget_resume" and lose_resume):
                raise SyncError("synthetic reply lost", "SYNC_CLIENT_NETWORK", 503)
        return {**receipts[body["request_id"]], "replayed": len([v for v in calls if v["request_id"] == body["request_id"]]) > 1}
    monkeypatch.setattr(value.client, "budget_request", deliver, raising=False)
    return calls


def test_runtime_defer_freezes_original_body_before_rpc_and_preserves_original_exception(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core = bind(value).core
        calls = budget_transport(value, monkeypatch)
        waiting = BudgetDeferred("daily_limit", resume_at=datetime.now(timezone.utc) + timedelta(days=1),
                                 operation_id=str(uuid.uuid4()))
        with pytest.raises(BudgetDeferred) as caught:
            value.defer_budget(waiting)
        assert caught.value is waiting
        pending = value.store.assert_budget_pending()
        assert pending["defer_receipt"] is not None and pending["defer_request"] == calls[0]
        assert pending["defer_request"]["budget_operation_id"] == waiting.operation_id
        assert value.store.read()["source_execution"] == core
        assert value.store.read()["status"]["state"] == "budget_wait"
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


def test_runtime_lost_defer_reply_replays_original_body_before_due_resume(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        bind(value)
        calls = budget_transport(value, monkeypatch, lose_defer=True)
        waiting = BudgetDeferred("download_pacing", resume_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                                 operation_id=str(uuid.uuid4()))
        with pytest.raises(BudgetDeferred):
            value.defer_budget(waiting)
        original = value.store.assert_budget_pending()["defer_request"]
        assert value.store.read()["status"]["state"] == "pending_delivery"
        assert value.resume_budget() is True
        assert calls[0] == calls[1] == original
        assert calls[2]["kind"] == "budget_resume" and calls[2]["defer_request_id"] == original["request_id"]
        assert value.store.read()["budget_pending"] is None
        assert value.store.read()["source_execution"] is not None


def test_runtime_early_claim_is_not_manual_resume_authority_and_original_approval_is_durable(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        bind(value)
        calls = budget_transport(value, monkeypatch)
        waiting = BudgetDeferred("indeterminate", operation_id=str(uuid.uuid4()))
        with pytest.raises(BudgetDeferred):
            value.defer_budget(waiting)
        original = value.store.assert_budget_pending()["defer_request"]
        with pytest.raises(BudgetDeferred) as caught:
            value.resume_budget()
        assert caught.value.reason == "indeterminate" and caught.value.resume_at is None
        assert len(calls) == 1
        tasks.resume_sync(job["job_id"], profile=value.storage_profile)
        assert tasks.sync_resume_authorized(job["job_id"], original["request_id"], profile=value.storage_profile)
        assert value.resume_budget() is True
        assert len(calls) == 2 and calls[-1]["kind"] == "budget_resume"


def test_runtime_lost_resume_reply_reuses_original_current_lease_body(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        bind(value)
        calls = budget_transport(value, monkeypatch, lose_resume=True)
        waiting = BudgetDeferred("download_pacing", resume_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                                 operation_id=str(uuid.uuid4()))
        with pytest.raises(BudgetDeferred):
            value.defer_budget(waiting)
        with pytest.raises(BudgetDeferred):
            value.resume_budget()
        pending = value.store.assert_budget_pending()
        original = pending["resume_request"]
        assert original is not None and pending["resume_receipt"] is None
        lease = {**value.store.read()["lease"], "generation": 2, "lease_token": str(uuid.uuid4())}
        value.store.update({"lease": lease})
        assert value.resume_budget() is True
        assert calls[-1] == calls[-2] == original
        assert original["lease"] != lease


def test_normal_home_strict_entry_binds_before_resolver_and_scopes_only_ingest(env, monkeypatch):
    install_import(env, monkeypatch)
    config.set_local({"budget_enabled": True})
    steps = []
    legacy_resolve = sync_sources.resolve_url
    def resolve(binding, *, check_cancelled):
        assert source_budget.current_scope() is None
        assert binding.store.assert_source_execution() == binding.core
        binding.check()
        steps.append("resolution")
        return legacy_resolve(binding.submitted_url)
    def ingest_paid(url, *, enqueue_process, metadata_validator):
        scope = source_budget.current_scope()
        assert scope.home_execution.core == scope.home_execution.store.assert_source_execution()
        assert url == "https://www.bilibili.com/video/BVfake123" and enqueue_process is False
        metadata_validator({"id": "BVfake123", "extractor": "BiliBili", "webpage_url": url})
        path = add_video("BVfake123")
        steps.append("ingest")
        return {"video_id": "BVfake123", "extracted_video_id": "BVfake123",
                "extracted_media_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(source_resolution_budget, "resolve_url", resolve)
    monkeypatch.setattr(ingest, "ingest_url", ingest_paid)
    with home_cycle(env) as value, executing(value):
        result = value.process_claim()
        assert steps == ["resolution", "ingest"] and len(result) == 1
        assert value.store.read()["source_execution"] is not None
        assert value.store.read()["work"]["source_mapping_inputs"]["resolution"]["submitted_url"] == env.claims[0]["url"]
        assert source_budget.current_scope() is None


def test_strict_normal_source_failure_preserves_core_and_original_job_without_cloud_fail(env, monkeypatch):
    install_import(env, monkeypatch)
    config.set_local({"budget_enabled": True})
    def failed(binding, **kwargs):
        binding.check()
        raise source_budget.SourceProcessError()
    monkeypatch.setattr(source_resolution_budget, "resolve_url", failed)
    with home_cycle(env) as value, executing(value) as (job, owner):
        with pytest.raises(BudgetDeferred) as caught:
            value.process_claim()
        assert caught.value.reason == "indeterminate" and caught.value.operation_id is None
        core = value.store.assert_source_execution()
        assert core["root_job_id"] == job["job_id"]
        assert tasks.defer(job["job_id"], owner, caught.value, profile=value.storage_profile)
        after = raw_job(value.storage_profile, job["job_id"])
        assert after["status"] == "pending" and after["wait_mode"] == "blocked"
        assert after["continuation_id"] == core["continuation_id"]
        assert value.store.read()["status"]["state"] == "source_blocked"
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


def test_review_only_entry_keeps_waiting_job_and_never_claims_or_processes_source(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        core = bind(value).core
        waiting = BudgetDeferred("indeterminate")
        assert tasks.defer(job["job_id"], owner, waiting, profile=value.storage_profile)
        agent._status(value.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
        profile = value.storage_profile
    before = raw_job(profile, job["job_id"])
    env.calls.clear()
    monkeypatch.setattr(worker, "check_admission", lambda **kwargs: pytest.fail("Review wait must bypass source admission"))
    monkeypatch.setattr(agent._Cycle, "process_claim", lambda *a: pytest.fail("Review wait cannot process a source"))
    monkeypatch.setattr(agent._Cycle, "publish", lambda *a: pytest.fail("Review wait cannot publish"))
    monkeypatch.setattr(agent.report, "run_all", lambda *a: pytest.fail("Review wait cannot generate reports"))
    with tasks.process_lock(profile=profile) as acquired, worker._execution(before, str(uuid.uuid4()),
        threading.Event(), profile=profile):
        assert acquired
        result = agent.run_cycle(client=env, review_only=True)
    assert result == {"state": "source_blocked", "reviews_synced": True}
    assert raw_job(profile, job["job_id"]) == before
    assert all(call[2] == 0 for call in env.calls if isinstance(call, tuple) and call[0] == "pull")
    assert not env.publications
    saved = sync_state.StateStore(profile.database.parent).read()
    assert saved["source_execution"] == core and saved["claims"] == env.claims


@pytest.mark.parametrize("changed_fence", [False, True])
def test_normal_cycle_cannot_finish_original_job_while_source_core_has_no_terminal(env, monkeypatch, changed_fence):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        core = bind(value).core
        profile = value.storage_profile
    def incomplete(cycle):
        if changed_fence:
            recovery = cycle.store.read()["recovery"]
            cycle.store.observe_recovery({**recovery, "generation": 1, "revision": 1})
        return {"state": "idle"}
    monkeypatch.setattr(agent._Cycle, "execute", incomplete)
    with tasks.process_lock(profile=profile) as acquired, worker._execution(job, owner, threading.Event(), profile=profile):
        assert acquired
        with pytest.raises(BudgetDeferred) as caught:
            agent.run_cycle(client=env)
    assert caught.value.reason == ("indeterminate" if changed_fence else "download_pacing")
    assert (caught.value.resume_at is None) is changed_fence
    assert sync_state.StateStore(profile.database.parent).read()["source_execution"] == core


@pytest.mark.parametrize("code", ["INVALID_SYNC_SNAPSHOT", "SYNC_CLAIM_EXPIRED"])
def test_tracked_publication_failure_has_no_authority_to_fail_or_retire_original_progress(env, monkeypatch, code):
    install_import(env, monkeypatch)
    add_video()
    with home_cycle(env) as value, executing(value):
        core = bind(value).core
        value.freeze([])
        before = value.store.read()
        def failed(*args, **kwargs):
            raise SyncError("synthetic tracked publication failure", code, 409)
        monkeypatch.setattr(value.client, "push_snapshot", failed)
        with pytest.raises(SyncError) as caught:
            value.publish()
        assert caught.value.code == code
        after = value.store.read()
        assert after["source_execution"] == core
        assert all(after[key] == before[key] for key in ("claims", "work", "pending_publication"))
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


@pytest.mark.parametrize("label,code", [("budget_wait", None), ("pending_delivery", "SYNC_BUDGET_DELIVERY_PENDING"),
                                      ("source_blocked", "SYNC_SOURCE_EXECUTION_BLOCKED")])
def test_public_status_exposes_closed_wait_states_without_private_execution_or_requests(env, monkeypatch, label, code):
    install_import(env, monkeypatch)
    config.set_local({"sync_token": "synthetic-test-only-token"})
    with home_cycle(env) as value, executing(value):
        core = bind(value).core
        agent._status(value.store, label, error=code)
        summary = sync_home_status.summary()
        assert summary["state"] == label and summary["error_code"] == code
        assert set(summary) == {"enabled", "configured", "state", "updated_at", "last_success_at", "retry_at",
                               "error_code", "seeded", "has_pending_publication", "work_phase", "lease_valid"}
        text = json.dumps(summary)
        assert all(secret not in text for secret in (core["continuation_id"], core["submitted_url_sha256"],
                                                     core["binding"]["target_url"], "source_execution", "defer_request"))
