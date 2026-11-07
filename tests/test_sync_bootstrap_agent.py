"""Recovery bootstrap keeps original source work and never admits paid work."""
from copy import deepcopy
import threading
import uuid

import pytest

from reno import budget, config, db, ingest, review, sync_agent as agent, sync_source_execution, sync_state, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_sync_agent import add_video, env, install_import
from tests.test_sync_budget_propagation import home_cycle, raw_job
from tests.test_sync_source_execution_binding import bind, executing
from tests.test_sync_source_execution_state import receipt, request


def prepare(value, remote, *, budget_wait=True, publication=True, unfinished=True):
    add_video("ready")
    binding = bind(value)
    original = binding.core
    if publication:
        value.freeze([])
    if unfinished:
        add_video("unfinished", status="imported")
        with db.connect(value.storage_profile.database) as con:
            db.replace_atoms(con, "unfinished", [{"id": "unfinished_atom", "claim": "尚未完成的处理",
                "polarity": "recommend", "conditions": {}, "parameters": [], "evidence_refs": []}])
            con.commit()
        value.store.update({"work": {"item_id": original["item_id"], "lease_generation": 1,
            "video_id": "unfinished", "phase": "downloaded"}})
    if budget_wait:
        reserved = budget.reserve(binding.resolution_operation, budget.AttemptSpec("e" * 64), profile=value.storage_profile)
        body = request(original)
        body["budget_operation_id"] = reserved.operation_id
        body["claim"] = value.store.read()["claims"][0]["claim"]
        value.store.install_budget_defer_request(body)
        value.store.record_budget_defer_receipt(body["request_id"], receipt(body,
            item_id=body["claim"]["item_id"], lease_generation=body["claim"]["lease_generation"]))
        agent._status(value.store, "budget_wait", retry_at=body["retry_at"])
    remote.recovery.update(generation=1, revision=1, state="resume_pending")
    return original, deepcopy(value.store.read())


def prohibit_work(monkeypatch):
    monkeypatch.setattr(agent._Cycle, "advance_work", lambda *a: pytest.fail("Bootstrap cannot advance source work"))
    monkeypatch.setattr(agent._Cycle, "ensure_work_mapping", lambda *a: pytest.fail("Bootstrap cannot repair source maps"))
    monkeypatch.setattr(agent._Cycle, "process_claim", lambda *a: pytest.fail("Bootstrap cannot process claims"))
    monkeypatch.setattr(agent._Cycle, "resume_budget", lambda *a: pytest.fail("Old budget wait cannot precede bootstrap"))
    monkeypatch.setattr(agent._Cycle, "resume_source_control", lambda *a: pytest.fail("Old source control cannot precede bootstrap"))
    monkeypatch.setattr(agent.pipeline, "run_video", lambda *a, **k: pytest.fail("Bootstrap cannot process media"))
    monkeypatch.setattr(agent.pipeline, "judge_all", lambda *a: pytest.fail("Bootstrap cannot judge"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("Bootstrap cannot import a source"))
    monkeypatch.setattr(budget, "reserve", lambda *a, **k: pytest.fail("Bootstrap cannot reserve paid work"))
    monkeypatch.setattr(budget, "mark_dispatching", lambda *a, **k: pytest.fail("Bootstrap cannot dispatch paid work"))
    monkeypatch.setattr(budget, "settle", lambda *a, **k: pytest.fail("Bootstrap cannot settle paid work"))


def assert_original(value, before):
    after = value.store.read()
    assert all(after.get(key) == before.get(key) for key in (
        "source_execution", "source_control_pending", "source_admission", "budget_pending", "claims", "work", "pending_publication"))
    assert all(after["uploaded_media"].get(key) == item for key, item in before["uploaded_media"].items())
    assert all(after.get("publication_media", {}).get(key) == item for key, item in before.get("publication_media", {}).items())


def test_tracked_bootstrap_runs_before_old_wait_and_preserves_original_publication_and_work(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core, before = prepare(value, env)
        prohibit_work(monkeypatch)
        result = value.execute()
        assert result["recovery_bootstrapped"] is True and len(env.publications) == 1
        published = env.publications[0]
        assert published["claims"] == [] and published["recovery_generation"] == 1
        assert [video["video_id"] for video in published["snapshot"]["videos"]] == ["ready"]
        assert published["snapshot"]["publication_id"] != before["pending_publication"]["publication_id"]
        assert_original(value, before)
        assert value.store.read()["applied_recovery_generation"] == 1
        assert value.store.read()["recovery_attempt"] is None
        assert value.store.completed_source_failure(root_job_id=core["root_job_id"],
            continuation_id=core["continuation_id"], execution_generation=core["execution_generation"]) is None
        assert value.store.completed_source_publication(root_job_id=core["root_job_id"],
            continuation_id=core["continuation_id"], execution_generation=core["execution_generation"]) is None


def test_normal_worker_bootstrap_cannot_finish_original_source_job(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        core, before = prepare(value, env, publication=False)
        profile = value.storage_profile
    prohibit_work(monkeypatch)
    with tasks.process_lock(profile=profile) as acquired, worker._execution(job, owner, threading.Event(), profile=profile):
        assert acquired
        with pytest.raises(BudgetDeferred) as waiting:
            agent.run_cycle(client=env)
        assert raw_job(profile, job["job_id"])["status"] == "running"
        assert waiting.value.operation_id == before["budget_pending"]["defer_request"]["budget_operation_id"]
        assert tasks.defer(job["job_id"], owner, waiting.value, profile=profile)
    actual_wait = raw_job(profile, job["job_id"])
    assert len(env.publications) == 1 and actual_wait["status"] == "pending"
    assert actual_wait["blocked_reason"] == before["budget_pending"]["defer_request"]["reason"]
    assert (actual_wait["continuation_id"], actual_wait["execution_generation"]) == (core["continuation_id"], core["execution_generation"])
    assert actual_wait["completed_at"] is None and actual_wait["owner_id"] is None
    assert_original(value, before)
    assert all(value.store.read()["status"][key] == before["status"][key] for key in ("state", "error_code", "retry_at"))


def test_review_only_wait_can_bootstrap_without_changing_paused_sql_job_or_source_wait(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        core, before = prepare(value, env)
        profile = value.storage_profile
        assert tasks.defer(job["job_id"], owner, BudgetDeferred("indeterminate"), profile=profile)
        tasks.pause_sync(job["job_id"], profile=profile)
    original_job = raw_job(profile, job["job_id"])
    prohibit_work(monkeypatch)
    monkeypatch.setattr(worker, "check_admission", lambda **k: pytest.fail("Static recovery cannot admit source work"))
    with tasks.process_lock(profile=profile) as acquired, worker._execution(original_job, str(uuid.uuid4()), threading.Event(), profile=profile):
        assert acquired
        result = agent.run_cycle(client=env, review_only=True)
    assert result["reviews_synced"] is True and result["recovery_bootstrapped"] is True
    assert raw_job(profile, job["job_id"]) == original_job
    assert_original(value, before)
    assert all(call[2] == 0 for call in env.calls if isinstance(call, tuple) and call[0] == "pull")


def test_held_recovery_exchanges_reviews_before_wait_without_bootstrap_or_source_work(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core, before = prepare(value, env)
        env.recovery["state"] = "held"
        prohibit_work(monkeypatch)
        assert value.execute() == {"state": "recovery_held"}
        assert not env.publications
        assert_original(value, before)


def test_lost_bootstrap_push_reply_reuses_original_uuid_and_outbox_with_original_pending_untouched(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core, before = prepare(value, env)
        prohibit_work(monkeypatch)
        env.fail_publish = True
        with pytest.raises(SyncError):
            value.execute()
        attempt = value.store.read()["recovery_attempt"]
        assert attempt is not None
        saved = value.store.find_publication(attempt["publication_id"])
        original_bytes = (value.store.data_dir / "sync-outbox" / (saved["publication_id"] + ".json")).read_bytes()
        result = value.execute()
        assert result["recovery_bootstrapped"] is True
        assert len(env.publications) == 2
        assert env.publications[0] == env.publications[1]
        assert (value.store.data_dir / "sync-outbox" / (saved["publication_id"] + ".json")).read_bytes() == original_bytes
        assert_original(value, before)


def test_stored_bootstrap_receipt_restores_ack_without_another_push_or_source_terminal(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core, before = prepare(value, env)
        prohibit_work(monkeypatch)
        original = value.store.acknowledge_recovery_publication
        def interrupted(*a, **k):
            raise RuntimeError("synthetic interrupted bootstrap ack")
        monkeypatch.setattr(value.store, "acknowledge_recovery_publication", interrupted)
        with pytest.raises(RuntimeError):
            value.execute()
        assert len(env.publications) == 1
        monkeypatch.setattr(value.store, "acknowledge_recovery_publication", original)
        monkeypatch.setattr(env, "push_snapshot", lambda *a, **k: pytest.fail("Stored bootstrap receipt cannot push"))
        assert value.execute()["recovery_bootstrapped"] is True
        assert_original(value, before)


def test_remote_stream_change_keeps_original_source_publication_claims_and_work(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        core, before = prepare(value, env)
        env.epoch = 2
        prohibit_work(monkeypatch)
        assert value.execute()["recovery_bootstrapped"] is True
        assert_original(value, before)


def test_legacy_claimed_attempt_is_archived_before_independent_bootstrap_and_never_relabelled(env, monkeypatch):
    install_import(env, monkeypatch)
    add_video("ready")
    with home_cycle(env) as value:
        recovery = {**value.recovery(), "generation": 1, "revision": 1}
        value.store.observe_recovery(recovery)
        value.knowledge_generation = 1
        claimed = [{"claim": deepcopy(env.claims[0]["claim"]), "video_id": "ready"}]
        value.freeze(claimed)
        state = value.store.read()
        metadata = state["pending_publication"]
        envelope = value.store.read_publication(metadata)
        attempt = {"generation": 1, "publication_id": metadata["publication_id"],
            "version": envelope["snapshot"]["version"], "stream": recovery["stream"]}
        value.store.update({"recovery_attempt": attempt,
            "work": {"item_id": env.claims[0]["item_id"], "lease_generation": 1, "video_id": "ready", "phase": "reported"}})
        before = value.store.read()
        raw = (value.store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
        env.recovery = {**recovery, "revision": 2, "state": "resume_pending"}
        prohibit_work(monkeypatch)
        assert value.execute()["recovery_bootstrapped"] is True
        assert len(env.publications) == 1 and env.publications[0]["claims"] == []
        assert env.publications[0]["snapshot"]["publication_id"] != attempt["publication_id"]
        assert (value.store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == raw
        assert_original(value, before)
        histories = [sync_state._decode(path.read_bytes()) for path in (value.store.data_dir / "sync-work-history").glob("*.json")]
        assert any(history["recovery_attempt"] == attempt and history["pending_publication"] == metadata for history in histories)


def test_review_version_race_before_first_freeze_retries_same_uuid_without_poisoning_outbox(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        _, before = prepare(value, env)
        prohibit_work(monkeypatch)
        original = agent.report.run_all
        attempts = []
        def reviewed(*a, **k):
            result = original(*a, **k)
            attempts.append(deepcopy(value.store.read()["recovery_attempt"]))
            with db.connect(value.storage_profile.database) as con:
                review.record_decision(con, atom_id="atom_ready", action="confirm", client_id="isolated-bootstrap-race")
                con.commit()
            return result
        monkeypatch.setattr(agent.report, "run_all", reviewed)
        with pytest.raises(SyncError) as conflict:
            value.execute()
        assert conflict.value.code == "SYNC_STATE_RECOVERY_CONFLICT"
        attempt = value.store.read()["recovery_attempt"]
        assert value.store.find_publication(attempt["publication_id"]) is None
        monkeypatch.setattr(agent.report, "run_all", original)
        assert value.execute()["recovery_bootstrapped"] is True
        assert len(env.publications) == 1
        assert env.publications[0]["snapshot"]["publication_id"] == attempts[0]["publication_id"]
        assert env.publications[0]["snapshot"]["version"]["kb_revision"] > attempts[0]["version"]["kb_revision"]
        assert len(env.publications[0]["snapshot"]["reports"]) == 4
        assert_original(value, before)
