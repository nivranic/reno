"""Home budget waits keep their original worker task and private progress."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading

import pytest

from reno import db, ingest, report, storage_admin, sync_agent as agent, sync_sources, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_state import StateStore
from tests.test_sync_agent import install_import
from tests.conftest import released16_env as env


def postponed(reason="daily_limit", *, timed=True):
    return BudgetDeferred(reason, resume_at=datetime.now(timezone.utc) + timedelta(days=1) if timed else None)


def raise_wait(deferred):
    def waiting(*args, **kwargs):
        raise deferred
    return waiting


@contextmanager
def home_cycle(remote):
    profile = storage_admin.profile()
    source, version = agent._local()
    options = agent.settings()
    binding = {"home_library_id": source["library_id"], "home_node_id": source["node_id"],
               "target_library_id": options["sync_target_library_id"],
               "target_node_id": options["sync_target_node_id"], "target_url": options["url"]}
    store = StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired, store.exclusive():
        assert acquired
        store.initialize(binding, version["library_epoch"])
        value = agent._Cycle(store, remote, source, options, threading.Event())
        value.acquire()
        recovery = value.recovery()
        value.knowledge_generation = recovery["generation"]
        value.knowledge_stream = recovery["stream"]
        store.update({"claims": deepcopy(remote.claims)})
        yield value


def raw_job(profile, job_id):
    con = db.connect(profile.database)
    try:
        return dict(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())
    finally:
        con.close()


def progress(value):
    return {key: deepcopy(value[key]) for key in ("claims", "work", "pending_publication")}


@pytest.mark.parametrize("boundary", ["resolution", "metadata"])
def test_process_claim_propagates_original_wait_without_cloud_failure_or_retirement(env, monkeypatch, boundary):
    install_import(env, monkeypatch)
    deferred = postponed()
    monkeypatch.setattr(sync_sources if boundary == "resolution" else ingest,
                        "resolve_url" if boundary == "resolution" else "ingest_url", raise_wait(deferred))
    with home_cycle(env) as value:
        before = progress(value.store.read())
        retired = []
        original_retire = value.store.retire_progress
        def retire(*args, **kwargs):
            retired.append(kwargs)
            return original_retire(*args, **kwargs)
        monkeypatch.setattr(value.store, "retire_progress", retire)
        with pytest.raises(BudgetDeferred) as caught:
            value.process_claim()
        assert caught.value is deferred
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
        assert retired == []
        assert progress(value.store.read()) == before
        assert value.store.read()["status"]["error_code"] is None


def test_run_cycle_does_not_convert_wait_into_successful_retry_status(env, monkeypatch):
    deferred = postponed("indeterminate", timed=False)
    monkeypatch.setattr(agent._Cycle, "execute", raise_wait(deferred))
    with tasks.process_lock() as acquired:
        assert acquired
        with pytest.raises(BudgetDeferred) as caught:
            agent.run_cycle(client=env)
    assert caught.value is deferred
    state = StateStore(storage_admin.profile().database.parent).read()
    assert state["status"]["error_code"] is None
    assert state["status"]["state"] == "idle"


def test_report_wait_does_not_fail_or_retire_an_active_claim(env, monkeypatch):
    install_import(env, monkeypatch)
    deferred = postponed("reported_tokens_limit")
    monkeypatch.setattr(report, "run_all", raise_wait(deferred))
    with home_cycle(env) as value:
        before = progress(value.store.read())
        monkeypatch.setattr(value, "process_claim", lambda: [])
        monkeypatch.setattr(value, "needs_publication", lambda: True)
        retired = []
        original_retire = value.store.retire_progress
        def retire(*args, **kwargs):
            retired.append(kwargs)
            return original_retire(*args, **kwargs)
        monkeypatch.setattr(value.store, "retire_progress", retire)
        with pytest.raises(BudgetDeferred) as caught:
            value.execute()
        assert caught.value is deferred
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
        assert retired == []
        assert progress(value.store.read()) == before


@pytest.mark.parametrize("reason,timed", [("daily_limit", True), ("indeterminate", False)])
def test_actual_worker_defers_original_sync_job_without_failure_or_new_lineage(env, monkeypatch, reason, timed):
    install_import(env, monkeypatch)
    deferred = postponed(reason, timed=timed)
    monkeypatch.setattr(sync_sources, "resolve_url", raise_wait(deferred))
    monkeypatch.setattr(agent, "SyncClient", lambda *args, **kwargs: env)
    seen, finished = [], []
    original_defer, original_finish = tasks.defer, tasks.finish
    def defer(job_id, owner_id, waiting, **kwargs):
        seen.append((job_id, waiting, raw_job(kwargs["profile"], job_id)["owner_id"] == owner_id))
        return original_defer(job_id, owner_id, waiting, **kwargs)
    def finish(*args, **kwargs):
        finished.append((args, kwargs))
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(tasks, "defer", defer)
    monkeypatch.setattr(tasks, "finish", finish)
    profile = storage_admin.profile()
    job_id = tasks.submit_sync(profile=profile)
    before = raw_job(profile, job_id)
    assert worker.process_pending(max_jobs=1) == 1
    after = raw_job(profile, job_id)
    assert seen == [(job_id, deferred, True)]
    assert finished == []
    assert after["status"] == "pending" and after["attempts"] == 1
    assert (after["continuation_id"], after["execution_generation"], after["payload_json"], after["scope_key"]) == (
        before["continuation_id"], before["execution_generation"], before["payload_json"], before["scope_key"])
    assert after["owner_id"] is after["lease_until"] is after["error"] is after["completed_at"] is None
    assert after["blocked_reason"] == reason
    assert after["wait_mode"] == ("until" if timed else "blocked")
    assert (after["not_before"] is not None) is timed
    assert tasks.submit_sync(profile=profile) == job_id
    state = StateStore(profile.database.parent).read()
    assert state["claims"] == env.claims and state["work"] is None and state["pending_publication"] is None
    assert state["status"]["error_code"] is None
    assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
