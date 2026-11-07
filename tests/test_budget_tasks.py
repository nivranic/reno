"""Durable budget waits on artificial queues; no provider or platform I/O."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from reno import config, db, pipeline, storage_admin, storage_lifecycle, tasks, worker
from reno.budget_contract import BudgetDeferred


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "all", "budget_enabled": False})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": "video", "content_sha256": "artificial-video", "title": "人工样本"})
        con.commit()
    yield profile
    assert worker.stop_running()


def raw(store, job_id):
    con = db.connect(store.database)
    try:
        return dict(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())
    finally:
        con.close()


def edit(store, sql, values=()):
    with storage_admin.connection(store) as con:
        con.execute(sql, values)
        con.commit()


def clone_store(store, tmp_path):
    clone = storage_lifecycle.StorageProfile(tmp_path / "clone/data/reno.db", tmp_path / "clone/media",
        tmp_path / "clone/data/generated", tmp_path / "clone/data/sync-checkpoints")
    clone.database.parent.mkdir(parents=True)
    source, target = db.connect(store.database), sqlite3.connect(clone.database)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    storage_admin.initialize(clone)
    return clone


def select_profile(monkeypatch, profile):
    monkeypatch.setattr(config, "DATA", profile.database.parent)
    monkeypatch.setattr(config, "MEDIA", profile.media)


def claimed(store, kind="process"):
    job = tasks.submit(kind, "video" if kind == "process" else None, profile=store)
    assert tasks.acquire_worker("owner")
    con = db.connect(store.database)
    try:
        result = tasks.claim(con, "owner")
    finally:
        con.close()
    assert result["job_id"] == job["job_id"]
    return result


def test_new_jobs_have_lineage_and_active_dedup_keeps_it(store):
    first = tasks.submit("process", "video", profile=store)
    again = tasks.submit("process", "video", profile=store)
    before = raw(store, first["job_id"])
    assert again["job_id"] == first["job_id"]
    assert str(uuid.UUID(before["continuation_id"])) == before["continuation_id"]
    assert before["execution_generation"] == 1
    edit(store, "UPDATE job SET status='done' WHERE job_id=?", (first["job_id"],))
    next_job = tasks.submit("process", "video", profile=store)
    assert raw(store, next_job["job_id"])["continuation_id"] != before["continuation_id"]


def test_retry_keeps_lineage_and_generation_when_force_is_downgraded(store):
    first = tasks.submit("process", "video", {"force": True}, profile=store)
    edit(store, "UPDATE job SET status='failed',execution_generation=7 WHERE job_id=?", (first["job_id"],))
    retried = tasks.retry(first["job_id"], profile=store)
    before, after = raw(store, first["job_id"]), raw(store, retried["job_id"])
    assert retried["payload"] == {"force": False}
    assert (after["continuation_id"], after["execution_generation"]) == (before["continuation_id"], 7)


@pytest.mark.parametrize("use_receipt", [False, True])
def test_retry_cannot_merge_another_lineage_even_with_same_payload(store, use_receipt):
    first = tasks.submit("process", "video", profile=store)
    edit(store, "UPDATE job SET status='failed' WHERE job_id=?", (first["job_id"],))
    request = str(uuid.uuid4())
    unrelated = tasks.submit("process", "video", request_id=request, profile=store)
    with pytest.raises(tasks.TaskError) as caught:
        tasks.retry(first["job_id"], request if use_receipt else None, profile=store)
    assert caught.value.code == "TASK_LINEAGE_CONFLICT" and caught.value.status == 409
    assert raw(store, unrelated["job_id"])["status"] == "pending"


def test_retry_receipt_after_completion_returns_original_same_lineage(store):
    first = tasks.submit("judge", profile=store)
    edit(store, "UPDATE job SET status='failed' WHERE job_id=?", (first["job_id"],))
    request = str(uuid.uuid4())
    retried = tasks.retry(first["job_id"], request, profile=store)
    edit(store, "UPDATE job SET status='done' WHERE job_id=?", (retried["job_id"],))
    assert tasks.retry(first["job_id"], request, profile=store)["job_id"] == retried["job_id"]


def test_missing_legacy_lineage_is_not_guessed_by_retry(store):
    first = tasks.submit("judge", profile=store)
    edit(store, "UPDATE job SET status='failed',continuation_id=NULL WHERE job_id=?", (first["job_id"],))
    with pytest.raises(tasks.TaskError) as caught:
        tasks.retry(first["job_id"], profile=store)
    assert caught.value.code == "TASK_LINEAGE_UNAVAILABLE"


def test_timed_defer_is_owner_cas_and_preserves_work_without_failure(store):
    job = claimed(store)
    edit(store, "INSERT INTO meta(key,value) VALUES('step:video:media','keep')")
    deadline = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=1001)
    assert not tasks.defer(job["job_id"], "wrong-owner", BudgetDeferred("daily_limit", resume_at=deadline), profile=store)
    assert tasks.defer(job["job_id"], "owner", BudgetDeferred("daily_limit", resume_at=deadline), profile=store)
    value = raw(store, job["job_id"])
    assert value["status"] == "pending" and value["attempts"] == 1
    assert value["not_before"] == 1893456000002
    assert value["wait_mode"] == "until" and value["blocked_reason"] == "daily_limit"
    assert value["owner_id"] is value["lease_until"] is value["error"] is value["completed_at"] is None
    assert (value["payload_json"], value["continuation_id"], value["execution_generation"]) == (
        job["payload_json"], job["continuation_id"], job["execution_generation"])
    con = db.connect(store.database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='step:video:media'").fetchone()[0] == "keep"
    finally:
        con.close()
    assert not tasks.defer(job["job_id"], "owner", BudgetDeferred("indeterminate"), profile=store)


def test_infinite_defer_is_explicit_and_keeps_manual_pause(store):
    job = claimed(store)
    tasks.pause(job["job_id"], profile=store)
    assert tasks.defer(job["job_id"], "owner", BudgetDeferred("indeterminate"), profile=store)
    value = raw(store, job["job_id"])
    assert value["wait_mode"] == "blocked" and value["blocked_reason"] == "indeterminate"
    assert value["not_before"] is None and value["manual_paused"] == 1


def test_invalid_preepoch_wait_is_controlled_and_preserves_owner(store):
    job = claimed(store)
    before = raw(store, job["job_id"])
    with pytest.raises(tasks.TaskError) as caught:
        tasks.defer(job["job_id"], "owner", BudgetDeferred("daily_limit",
                    resume_at=datetime(1900, 1, 1, tzinfo=timezone.utc)), profile=store)
    assert caught.value.code == "INVALID_TASK" and raw(store, job["job_id"]) == before


@pytest.mark.parametrize("action", ["defer", "pause", "resume"])
def test_wait_control_guard_outlives_commit_and_cancellation_rolls_back(store, monkeypatch, action):
    job = claimed(store)
    if action == "resume":
        tasks.pause(job["job_id"], profile=store)
    before = raw(store, job["job_id"])
    class Cancelled(BaseException):
        pass
    cancellation = Cancelled("synthetic wait-control cancellation")
    observed = []
    original = db.connect
    class Connection:
        def __init__(self, con):
            self.con = con
        def __getattr__(self, name):
            return getattr(self.con, name)
        def commit(self):
            storage_lifecycle.require_guard(store)
            observed.append("commit")
            raise cancellation
        def rollback(self):
            storage_lifecycle.require_guard(store)
            observed.append("rollback")
            return self.con.rollback()
        def close(self):
            storage_lifecycle.require_guard(store)
            observed.append("close")
            return self.con.close()
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", lambda path=None: Connection(original(path)))
        with pytest.raises(Cancelled) as caught:
            if action == "defer":
                tasks.defer(job["job_id"], "owner", BudgetDeferred("indeterminate"), profile=store)
            else:
                getattr(tasks, action)(job["job_id"], profile=store)
    assert caught.value is cancellation and observed == ["commit", "rollback", "close"]
    assert storage_lifecycle.current_guard() is None and raw(store, job["job_id"]) == before


def test_defer_rejects_foreign_or_missing_operation_without_changing_job(store):
    job = claimed(store)
    before = raw(store, job["job_id"])
    with pytest.raises(tasks.TaskError) as caught:
        tasks.defer(job["job_id"], "owner", BudgetDeferred("indeterminate", operation_id=str(uuid.uuid4())), profile=store)
    assert caught.value.code == "TASK_BUDGET_OPERATION_MISMATCH"
    assert raw(store, job["job_id"]) == before


@pytest.mark.parametrize("difference", [None, "library", "continuation", "generation"])
def test_defer_operation_binds_exact_library_and_lineage(store, difference):
    job = claimed(store)
    operation = str(uuid.uuid4())
    con = db.connect(store.database)
    try:
        library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    finally:
        con.close()
    stamp = "2030-01-01T00:00:00.000000Z"
    edit(store, """INSERT INTO budget_operation(operation_id,library_id,identity_sha256,continuation_id,
        execution_generation,kind,suboperation_key,input_sha256,created_at,updated_at)
        VALUES(?,?,?,?,?,'model','asr',?,?,?)""", (operation, str(uuid.uuid4()) if difference == "library" else library,
        "1" * 64, str(uuid.uuid4()) if difference == "continuation" else job["continuation_id"],
        2 if difference == "generation" else job["execution_generation"], "2" * 64, stamp, stamp))
    deferred = BudgetDeferred("indeterminate", operation_id=operation)
    if difference is None:
        assert tasks.defer(job["job_id"], "owner", deferred, profile=store)
        assert raw(store, job["job_id"])["budget_operation_id"] == operation
        tasks.resume(job["job_id"], profile=store)
        assert raw(store, job["job_id"])["budget_operation_id"] == operation
    else:
        before = raw(store, job["job_id"])
        with pytest.raises(tasks.TaskError) as caught:
            tasks.defer(job["job_id"], "owner", deferred, profile=store)
        assert caught.value.code == "TASK_BUDGET_OPERATION_MISMATCH"
        assert raw(store, job["job_id"]) == before


def test_claim_skips_blocked_paused_and_future_heads_using_one_clock(store, monkeypatch):
    jobs = [tasks.submit("report", payload={"mode": "static"}, profile=store)]
    edit(store, "UPDATE job SET wait_mode='blocked',blocked_reason='indeterminate' WHERE job_id=?", (jobs[0]["job_id"],))
    jobs.append(tasks.submit("judge", profile=store))
    tasks.pause(jobs[1]["job_id"], profile=store)
    jobs.append(tasks.submit("process", "video", profile=store))
    edit(store, "UPDATE job SET wait_mode='until',blocked_reason='daily_limit',not_before=1234001 WHERE job_id=?", (jobs[2]["job_id"],))
    ready = tasks.submit("report", payload={"mode": "summary"}, profile=store)
    edit(store, "UPDATE job SET wait_mode='until',blocked_reason='daily_limit',not_before=1234000 WHERE job_id=?", (ready["job_id"],))
    assert tasks.acquire_worker("owner")
    calls = []
    monkeypatch.setattr(tasks, "clock_ms", lambda: calls.append(True) or 1234000)
    con = db.connect(store.database)
    try:
        job = tasks.claim(con, "owner")
    finally:
        con.close()
    assert job["job_id"] == ready["job_id"] and len(calls) == 1
    assert job["wait_mode"] == "none" and job["not_before"] is job["blocked_reason"] is None
    assert all(raw(store, item["job_id"])["status"] == "pending" for item in jobs)


def test_expired_lease_recovery_preserves_lineage_pause_and_wait(store):
    job = claimed(store)
    tasks.pause(job["job_id"], profile=store)
    edit(store, "UPDATE job SET lease_until=0,wait_mode='blocked',blocked_reason='indeterminate' WHERE job_id=?", (job["job_id"],))
    edit(store, "UPDATE worker_lease SET lease_until=0")
    before = raw(store, job["job_id"])
    assert tasks.acquire_worker("replacement")
    after = raw(store, job["job_id"])
    assert after["status"] == "pending"
    for name in ("continuation_id", "execution_generation", "manual_paused", "blocked_reason", "wait_mode", "budget_operation_id"):
        assert after[name] == before[name]


def test_pause_running_does_not_return_inflight_work_to_pending(store):
    job = claimed(store)
    result = tasks.pause(job["job_id"], profile=store)
    assert result["manual_paused"] is True and result["can_resume"] is True
    value = raw(store, job["job_id"])
    assert value["status"] == "running" and value["owner_id"] == "owner"
    assert value["lease_until"] == job["lease_until"]
    assert tasks.resume(job["job_id"], profile=store)["manual_paused"] is False
    assert raw(store, job["job_id"])["status"] == "running"


def test_resume_racing_observed_pause_before_defer_is_not_lost(store):
    job = claimed(store)
    tasks.pause(job["job_id"], profile=store)
    with pytest.raises(BudgetDeferred) as caught:
        with worker._execution(job, "owner", None):
            worker.check_admission()
    assert caught.value.reason == "manual_pause"
    tasks.resume(job["job_id"], profile=store)
    assert tasks.defer(job["job_id"], "owner", caught.value, profile=store)
    value = raw(store, job["job_id"])
    assert value["status"] == "pending" and value["wait_mode"] == "none" and value["manual_paused"] == 0
    assert value["not_before"] is value["blocked_reason"] is None


def test_resume_same_job_clears_wait_but_not_lineage_or_attempts(store):
    job = claimed(store)
    tasks.defer(job["job_id"], "owner", BudgetDeferred("indeterminate"), profile=store)
    before = raw(store, job["job_id"])
    result = tasks.resume(job["job_id"], profile=store)
    assert result["job_id"] == job["job_id"] and result["wait_mode"] == "none"
    after = raw(store, job["job_id"])
    assert after["not_before"] is after["blocked_reason"] is None
    for name in ("continuation_id", "execution_generation", "attempts", "budget_operation_id", "payload_json"):
        assert after[name] == before[name]


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_resume_reject_cloud_internal_and_terminal(store, action):
    apply = getattr(tasks, action)
    internal = tasks.submit_sync(profile=store)
    with pytest.raises(tasks.TaskError) as caught:
        apply(internal, profile=store)
    assert caught.value.code == "NOT_FOUND"
    job = tasks.submit("judge", profile=store)
    edit(store, "UPDATE job SET status='done' WHERE job_id=?", (job["job_id"],))
    with pytest.raises(tasks.TaskError) as caught:
        apply(job["job_id"], profile=store)
    assert caught.value.code == "TASK_NOT_ACTIVE"
    config.set_local({"node_role": "cloud"})
    with pytest.raises(tasks.TaskError) as caught:
        apply(job["job_id"], profile=store)
    assert caught.value.code == "LOCAL_EXECUTION_UNAVAILABLE"


def test_budget_wait_worker_never_finishes_failed_and_skips_to_other_work(store, monkeypatch):
    deferred = tasks.submit("process", "video", profile=store)
    ready = tasks.submit("judge", profile=store)
    def dispatch(job):
        if job["kind"] == "process":
            raise BudgetDeferred("indeterminate")
        return {}
    monkeypatch.setattr(worker, "dispatch", dispatch)
    assert worker.process_pending() == 2
    waiting = tasks.get(deferred["job_id"])
    assert waiting["status"] == "pending" and waiting["wait_mode"] == "blocked"
    assert waiting["error"] is waiting["completed_at"] is None
    assert tasks.get(ready["job_id"])["status"] == "done"
    assert worker.execution_context() is None


def test_execution_context_is_bounded_copy_and_nested_exception_restores_outer():
    outer = {"job_id": 1, "continuation_id": str(uuid.uuid4()), "execution_generation": 3,
             "kind": "process", "video_id": "video", "payload_json": "secret-private-payload"}
    inner = dict(outer, job_id=2)
    stop = object()
    assert worker.execution_context() is None
    with worker._execution(outer, "outer-owner", stop):
        value = worker.execution_context()
        assert set(value) == {"job_id", "owner_id", "continuation_id", "execution_generation", "kind", "video_id"}
        value["job_id"] = 999
        assert worker.execution_context()["job_id"] == 1
        with pytest.raises(RuntimeError):
            with worker._execution(inner, "inner-owner", object()):
                assert worker.execution_context()["job_id"] == 2
                raise RuntimeError("synthetic nested dispatch")
        assert worker.execution_context()["job_id"] == 1 and worker._current.stop is stop
    assert worker.execution_context() is None and not hasattr(worker._current, "stop")


def test_private_execution_profile_restores_outer_and_is_not_in_public_context(store, tmp_path):
    clone = clone_store(store, tmp_path)
    job = {"job_id": 1, "kind": "judge", "continuation_id": str(uuid.uuid4()), "execution_generation": 1}
    assert worker._execution_profile() is None
    with worker._execution(job, "owner", None, profile=store):
        assert worker._execution_profile() is store and "profile" not in worker.execution_context()
        with pytest.raises(RuntimeError):
            with worker._execution(job, "owner", None, profile=clone):
                assert worker._execution_profile() is clone
                raise RuntimeError("synthetic profile context cancellation")
        assert worker._execution_profile() is store
    assert worker._execution_profile() is None and not hasattr(worker._current, "profile")


def test_changed_profile_clone_same_job_owner_defers_original_only(store, tmp_path, monkeypatch):
    job = tasks.submit("judge", profile=store)
    clone_box = []
    def dispatch(value):
        clone = clone_store(store, tmp_path)
        clone_box.append(clone)
        assert raw(clone, value["job_id"])["owner_id"] == value["owner_id"]
        select_profile(monkeypatch, clone)
        worker.check_admission()
        pytest.fail("switching profile must stop the next call even with cloned owner and lineage")
    monkeypatch.setattr(worker, "dispatch", dispatch)
    assert worker.process_pending() == 1
    original, copied = raw(store, job["job_id"]), raw(clone_box[0], job["job_id"])
    assert original["status"] == "pending" and original["blocked_reason"] == "ledger_unavailable"
    assert original["wait_mode"] == "blocked" and original["error"] is None
    assert copied["status"] == "running" and copied["blocked_reason"] is None
    original_con, clone_con = db.connect(store.database), db.connect(clone_box[0].database)
    try:
        assert original_con.execute("SELECT owner_id FROM worker_lease").fetchone()[0] is None
        assert clone_con.execute("SELECT owner_id FROM worker_lease").fetchone()[0] == copied["owner_id"]
    finally:
        original_con.close()
        clone_con.close()
    assert worker.execution_context() is None and worker._execution_profile() is None


@pytest.mark.parametrize("mode", ["done", "failed"])
def test_finish_after_profile_switch_targets_original_clone_owner_is_untouched(store, tmp_path, monkeypatch, mode):
    job = tasks.submit("judge", profile=store)
    clone_box = []
    def dispatch(value):
        clone = clone_store(store, tmp_path)
        clone_box.append(clone)
        select_profile(monkeypatch, clone)
        if mode == "failed":
            raise RuntimeError("synthetic failure after profile switch")
        return {"paid": True}
    monkeypatch.setattr(worker, "dispatch", dispatch)
    assert worker.process_pending() == 1
    assert raw(store, job["job_id"])["status"] == mode
    assert raw(clone_box[0], job["job_id"])["status"] == "running"


def test_pipeline_paid_step_switching_profile_commits_original_then_stops(store, tmp_path, monkeypatch):
    job = tasks.submit("process", "video", profile=store)
    clone_box = []
    monkeypatch.setattr(pipeline, "_active_steps", lambda: ["asr", "atomize"])
    def paid_step(con, video_id):
        clone = clone_store(store, tmp_path)
        clone_box.append(clone)
        select_profile(monkeypatch, clone)
        return {"paid": True}
    monkeypatch.setattr(pipeline.asr, "run", paid_step)
    monkeypatch.setattr(pipeline.atomize, "run", lambda *args: pytest.fail("profile switch must block new steps"))
    assert worker.process_pending() == 1
    assert raw(store, job["job_id"])["blocked_reason"] == "ledger_unavailable"
    assert raw(clone_box[0], job["job_id"])["status"] == "running"
    for selected, expected in ((store, True), (clone_box[0], False)):
        con = db.connect(selected.database)
        try:
            assert bool(con.execute("SELECT value FROM meta WHERE key='step:video:asr'").fetchone()) is expected
            assert con.execute("SELECT COUNT(*) FROM processing_run WHERE ok=0").fetchone()[0] == 0
        finally:
            con.close()


def test_explicit_profile_lease_renew_and_claim_use_original_database(store, tmp_path, monkeypatch):
    job = tasks.submit("judge", profile=store)
    clone = clone_store(store, tmp_path)
    select_profile(monkeypatch, clone)
    assert tasks.acquire_worker("original-owner", profile=store)
    assert tasks.renew_worker("original-owner", profile=store)
    con = db.connect(store.database)
    try:
        assert tasks.claim(con, "original-owner", profile=store)["job_id"] == job["job_id"]
    finally:
        con.close()
    assert raw(clone, job["job_id"])["status"] == "pending"
    con = db.connect(clone.database)
    try:
        with pytest.raises(storage_lifecycle.StorageLifecycleError) as caught:
            tasks.claim(con, "original-owner", profile=store)
        assert caught.value.code == "STORAGE_PROFILE_MISMATCH" and not con.in_transaction
        assert con.execute("SELECT COUNT(*) FROM worker_lease").fetchone()[0] == 0
    finally:
        con.close()


def test_boundary_checks_full_profile_even_when_database_is_unchanged(store, monkeypatch):
    job = claimed(store)
    with worker._execution(job, "owner", None, profile=store):
        worker.check_admission()
        monkeypatch.setattr(config, "MEDIA", store.media / "different-media-root")
        with pytest.raises(BudgetDeferred) as caught:
            worker.check_admission()
        assert caught.value.reason == "ledger_unavailable"


@pytest.mark.parametrize("difference", ["owner", "generation", "continuation"])
def test_check_admission_rejects_changed_execution_identity(store, difference):
    job = claimed(store)
    with worker._execution(job, "owner", None):
        worker.check_admission()
        field, value = {"owner": ("owner_id", "replacement"), "generation": ("execution_generation", 2),
                        "continuation": ("continuation_id", str(uuid.uuid4()))}[difference]
        edit(store, f"UPDATE job SET {field}=? WHERE job_id=?", (value, job["job_id"]))
        with pytest.raises(tasks.TaskError) as caught:
            worker.check_admission()
        assert caught.value.code == "TASK_OWNER_LOST"


def test_manual_pause_during_paid_step_commits_it_and_defers_before_next_step(store, monkeypatch):
    job = tasks.submit("process", "video", profile=store)
    seen = []
    monkeypatch.setattr(pipeline, "_active_steps", lambda: ["asr", "atomize"])
    def paid_step(con, video_id):
        context = worker.execution_context()
        assert context["job_id"] == job["job_id"] and context["continuation_id"] == raw(store, job["job_id"])["continuation_id"]
        tasks.pause(job["job_id"], profile=store)
        seen.append("paid response")
        return {"ok": True}
    monkeypatch.setattr(pipeline.asr, "run", paid_step)
    monkeypatch.setattr(pipeline.atomize, "run", lambda *args: pytest.fail("paused next step must not run"))
    assert worker.process_pending() == 1
    value = raw(store, job["job_id"])
    assert value["status"] == "pending" and value["blocked_reason"] == "manual_pause" and value["manual_paused"] == 1
    con = db.connect(store.database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='step:video:asr'").fetchone()
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE ok=0").fetchone()[0] == 0
        assert con.execute("SELECT status FROM video_asset WHERE video_id='video'").fetchone()[0] == "done_asr"
    finally:
        con.close()
    assert seen == ["paid response"]


@pytest.mark.parametrize("signal", ["requested", "lost"])
def test_stop_or_lost_lease_during_paid_step_defers_before_next_call(store, monkeypatch, signal):
    job = tasks.submit("process", "video", profile=store)
    requested = threading.Event()
    monkeypatch.setattr(pipeline, "_active_steps", lambda: ["asr", "atomize"])
    def paid_step(con, video_id):
        if signal == "requested":
            requested.set()
        else:
            worker._current.stop.lost.set()
        return {"paid": True}
    monkeypatch.setattr(pipeline.asr, "run", paid_step)
    monkeypatch.setattr(pipeline.atomize, "run", lambda *args: pytest.fail("stop must block next external step"))
    assert worker.process_pending(stop_event=requested) == 1
    value = raw(store, job["job_id"])
    assert value["status"] == "pending" and value["wait_mode"] == "none" and value["blocked_reason"] is None
    assert value["error"] is value["completed_at"] is None
    con = db.connect(store.database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='step:video:asr'").fetchone()
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE ok=0").fetchone()[0] == 0
    finally:
        con.close()
    assert worker.execution_context() is None and not hasattr(worker._current, "stop")


def test_pipeline_budget_deferred_rolls_back_step_and_raises_same_exception(store, monkeypatch):
    deferred = BudgetDeferred("daily_limit", resume_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(pipeline, "_active_steps", lambda: ["asr"])
    def waits(con, video_id):
        con.execute("INSERT INTO meta(key,value) VALUES('uncommitted-budget-step','bad')")
        raise deferred
    monkeypatch.setattr(pipeline.asr, "run", waits)
    with pytest.raises(BudgetDeferred) as caught:
        pipeline.run_video("video")
    assert caught.value is deferred
    con = db.connect(store.database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='uncommitted-budget-step'").fetchone() is None
        assert con.execute("SELECT COUNT(*) FROM processing_run").fetchone()[0] == 0
        assert con.execute("SELECT status FROM video_asset WHERE video_id='video'").fetchone()[0] != "error"
    finally:
        con.close()


def test_budget_enabled_missing_lineage_defers_before_dispatch(store, monkeypatch):
    job = tasks.submit("judge", profile=store)
    edit(store, "UPDATE job SET continuation_id=NULL WHERE job_id=?", (job["job_id"],))
    config.set_local({"budget_enabled": True})
    monkeypatch.setattr(worker, "dispatch", lambda job: pytest.fail("missing lineage cannot admit paid work"))
    assert worker.process_pending() == 1
    value = tasks.get(job["job_id"])
    assert value["wait_mode"] == "blocked" and value["blocked_reason"] == "ledger_unavailable"


def test_pause_resume_api_reuses_job_and_resume_wakes_worker(store, monkeypatch):
    from app.task_api import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    wakes = []
    monkeypatch.setattr(worker, "ensure_running", lambda: wakes.append(True) or True)
    job = tasks.submit("judge", profile=store)
    paused = client.post(f"/api/jobs/{job['job_id']}/pause", json={})
    assert paused.status_code == 202 and paused.json()["manual_paused"] is True
    resumed = client.post(f"/api/jobs/{job['job_id']}/resume", json={})
    assert resumed.status_code == 202 and resumed.json()["job_id"] == job["job_id"] and len(wakes) == 1
    config.set_local({"node_role": "cloud"})
    assert client.post(f"/api/jobs/{job['job_id']}/resume", json={}).status_code == 403
    assert not any(word in json.dumps(paused.json()) for word in ("owner_id", "lease_until", "result_text"))
