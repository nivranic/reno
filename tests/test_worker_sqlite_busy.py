"""Isolated SQLite connection faults; real worker lock/lease/claim/fences.

The injected connect wrapper is explicitly a TEST boundary. It raises genuine
SQLite-produced errors only at process_pending's direct connection acquisition;
all authority, acquisition, renewal and queue operations remain real. Dispatch
is a bounded model-free fixture action. No developer DB/config is consulted.
"""
import sqlite3
import sys
import threading
import uuid

import pytest

from reno import config, db, storage_admin, storage_lifecycle as lifecycle, tasks, worker


@pytest.fixture
def worker_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "all", "sync_enabled": False})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    worker._wake.clear()
    yield profile
    worker._wake.set()
    assert worker.stop_running(timeout=5)
    worker._wake.clear()


def _sqlite_failure(tmp_path, kind):
    """Generate actual engine error codes in a separate named SQL fixture."""
    if kind == "no_code":
        return sqlite3.OperationalError("database is locked")
    path = tmp_path / ("synthetic-sqlite-" + kind + ".sqlite")
    first = sqlite3.connect(path, timeout=0)
    second = None
    cursor = None
    try:
        first.execute("CREATE TABLE probe(value INTEGER)")
        first.executemany("INSERT INTO probe VALUES(?)", [(1,), (2,)])
        first.commit()
        if kind == "busy":
            # Real schema READ against a separate connection's EXCLUSIVE lock.
            first.execute("BEGIN EXCLUSIVE")
            second = sqlite3.connect(path, timeout=0)
            statement, connection = "SELECT name FROM sqlite_master", second
        elif kind == "locked":
            cursor = first.execute("SELECT value FROM probe")
            assert cursor.fetchone() == (1,)
            statement, connection = "DROP TABLE probe", first
        elif kind == "busy_snapshot":
            assert first.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
            second = sqlite3.connect(path, timeout=0)
            second.execute("BEGIN")
            assert second.execute("SELECT value FROM probe ORDER BY value").fetchall() == [(1,), (2,)]
            first.execute("UPDATE probe SET value=value+10")
            first.commit()
            statement, connection = "UPDATE probe SET value=value+20", second
        elif kind == "not_busy":
            statement, connection = "SELECT nonexistent_column FROM probe", first
        else:
            raise AssertionError("unknown test SQL failure")
        try:
            connection.execute(statement)
        except sqlite3.OperationalError as error:
            expected = sqlite3.SQLITE_ERROR if kind == "not_busy" else (
                sqlite3.SQLITE_LOCKED if kind == "locked" else sqlite3.SQLITE_BUSY)
            assert type(error.sqlite_errorcode) is int
            assert error.sqlite_errorcode & 0xff == expected
            if kind == "busy_snapshot":
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT
            return error
        raise AssertionError("actual fixture SQL did not fail")
    finally:
        if cursor is not None:
            cursor.close()
        if second is not None:
            second.close()
        first.close()


def _lease(profile, connect):
    with lifecycle.shared(profile):
        con = connect(profile.database)
        try:
            return dict(con.execute("SELECT * FROM worker_lease WHERE scope=?", (tasks.WORKER_SCOPE,)).fetchone())
        finally:
            con.close()


def _connect_fault(monkeypatch, profile, error, *, repeat=False, on_fault=None):
    original = db.connect
    observations = []
    def actual_connections_except_explicit_worker_fault(database=None):
        if sys._getframe(1).f_code is worker.process_pending.__code__:
            assert database == profile.database
            assert tasks.holds_process_lock(profile=profile)
            row = _lease(profile, original)
            observations.append(row)
            if len(observations) == 1 or repeat:
                if on_fault is not None:
                    on_fault(row)
                raise error
        return original(database)
    monkeypatch.setattr(db, "connect", actual_connections_except_explicit_worker_fault)
    return original, observations


def _run_follow_once(requested, result, *, max_jobs=1):
    try:
        result["done"] = worker.process_pending(max_jobs=max_jobs, follow=True, stop_event=requested)
    except BaseException as error:
        result["error"] = error


def _assert_released(profile, connect):
    row = _lease(profile, connect)
    assert row["owner_id"] is None and row["lease_until"] == 0
    assert not tasks.holds_process_lock(profile=profile)


@pytest.mark.parametrize("kind", ["busy", "locked", "busy_snapshot"])
def test_follow_worker_crosses_one_real_sqlite_error_and_claims_exactly_once(worker_store, tmp_path, monkeypatch, kind):
    profile = worker_store
    queued = tasks.submit("report", profile=profile)
    failure = _sqlite_failure(tmp_path, kind)
    original_connect, observations = _connect_fault(monkeypatch, profile, failure)
    dispatched = []
    def model_free_dispatch(job):
        assert tasks.holds_process_lock(profile=profile)
        assert worker._execution_profile().identity == profile.identity
        worker.check_admission()
        dispatched.append(job["job_id"])
        return {}
    monkeypatch.setattr(worker, "dispatch", model_free_dispatch)
    assert worker.process_pending(max_jobs=1, follow=True) == 1
    assert len(observations) == 2 and len({row["owner_id"] for row in observations}) == 1
    assert all(row["owner_id"] and row["lease_until"] > tasks.clock_ms() for row in observations)
    assert dispatched == [queued["task_id"]]
    job = tasks.get(queued["task_id"], profile=profile)
    assert job["status"] == "done" and job["attempts"] == 1
    _assert_released(profile, original_connect)


def test_original_heartbeat_really_renews_while_follow_connection_retry_keeps_owner(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    queued = tasks.submit("report", profile=profile)
    error = _sqlite_failure(tmp_path, "busy")
    original_connect, observations = _connect_fault(monkeypatch, profile, error)
    renewed = threading.Event()
    renew_results = []
    original_renew = tasks.renew_worker
    def observe_actual_renew(owner_id, *, profile=None):
        answer = original_renew(owner_id, profile=profile)
        renew_results.append((owner_id, answer, _lease(profile, original_connect)))
        if answer:
            renewed.set()
        return answer
    monkeypatch.setattr(tasks, "renew_worker", observe_actual_renew)
    def model_free_dispatch(job):
        assert job["job_id"] == queued["task_id"]
        worker.check_admission()
        assert renewed.wait(15), "the original ten-second heartbeat must actually renew"
        worker.check_admission()
        return {}
    monkeypatch.setattr(worker, "dispatch", model_free_dispatch)
    requested, result = threading.Event(), {}
    thread = threading.Thread(target=_run_follow_once, args=(requested, result), daemon=True)
    thread.start()
    try:
        thread.join(20)
        assert not thread.is_alive() and result == {"done": 1}
        assert len(observations) == 2 and renew_results
        owner = observations[0]["owner_id"]
        assert observations[1]["owner_id"] == owner
        assert all(actual_owner == owner and answer and row["owner_id"] == owner
            for actual_owner, answer, row in renew_results)
        assert tasks.get(queued["task_id"], profile=profile)["attempts"] == 1
        _assert_released(profile, original_connect)
    finally:
        requested.set()
        worker._wake.set()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize("kind", ["busy", "locked", "busy_snapshot"])
def test_foreground_keeps_original_sqlite_error_without_retry(worker_store, tmp_path, monkeypatch, kind):
    profile = worker_store
    error = _sqlite_failure(tmp_path, kind)
    original_connect, observations = _connect_fault(monkeypatch, profile, error)
    with pytest.raises(sqlite3.OperationalError) as caught:
        worker.process_pending(follow=False)
    assert caught.value is error and len(observations) == 1
    _assert_released(profile, original_connect)


@pytest.mark.parametrize("kind", ["not_busy", "no_code"])
def test_follow_nonbusy_or_message_only_error_keeps_original_failure(worker_store, tmp_path, monkeypatch, kind):
    profile = worker_store
    error = _sqlite_failure(tmp_path, kind)
    original_connect, observations = _connect_fault(monkeypatch, profile, error)
    with pytest.raises(sqlite3.OperationalError) as caught:
        worker.process_pending(follow=True)
    assert caught.value is error and len(observations) == 1
    _assert_released(profile, original_connect)


def test_stop_requested_during_connect_busy_still_ends_original_loop(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    queued = tasks.submit("report", profile=profile)
    requested = threading.Event()
    def request_stop(_row):
        requested.set()
        worker._wake.set()
    original_connect, observations = _connect_fault(monkeypatch, profile, _sqlite_failure(tmp_path, "busy"),
        on_fault=request_stop)
    monkeypatch.setattr(worker, "dispatch", lambda _job: pytest.fail("stopped worker cannot dispatch"))
    assert worker.process_pending(follow=True, stop_event=requested) == 0
    assert len(observations) == 1
    assert tasks.get(queued["task_id"], profile=profile)["status"] == "pending"
    _assert_released(profile, original_connect)


def test_actual_configured_profile_change_during_connect_busy_still_ends_loop(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    def change_isolated_profile(_row):
        monkeypatch.setattr(config, "DATA", tmp_path / "changed-profile-data")
        worker._wake.set()
    original_connect, observations = _connect_fault(monkeypatch, profile, _sqlite_failure(tmp_path, "busy"),
        on_fault=change_isolated_profile)
    monkeypatch.setattr(worker, "dispatch", lambda _job: pytest.fail("changed profile cannot dispatch"))
    assert worker.process_pending(follow=True) == 0
    assert len(observations) == 1 and storage_admin.profile().identity != profile.identity
    _assert_released(profile, original_connect)


def test_real_heartbeat_owner_loss_ends_connect_retry_without_repairing_lease(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    queued = tasks.submit("report", profile=profile)
    replacement = str(uuid.uuid4())
    swapped = False
    original_connect = db.connect
    def explicit_isolated_owner_loss(row):
        nonlocal swapped
        if not swapped:
            with lifecycle.shared(profile):
                con = original_connect(profile.database)
                try:
                    assert con.execute("UPDATE worker_lease SET owner_id=? WHERE scope=? AND owner_id=?",
                        (replacement, tasks.WORKER_SCOPE, row["owner_id"])).rowcount == 1
                    con.commit()
                finally:
                    con.close()
            swapped = True
    _, observations = _connect_fault(monkeypatch, profile, _sqlite_failure(tmp_path, "busy"),
        repeat=True, on_fault=explicit_isolated_owner_loss)
    monkeypatch.setattr(worker, "dispatch", lambda _job: pytest.fail("lost owner cannot dispatch"))
    requested, result = threading.Event(), {}
    thread = threading.Thread(target=_run_follow_once, args=(requested, result), daemon=True)
    thread.start()
    try:
        thread.join(15)
        assert not thread.is_alive() and result == {"done": 0}
        assert swapped and observations
        assert _lease(profile, original_connect)["owner_id"] == replacement
        assert tasks.get(queued["task_id"], profile=profile)["status"] == "pending"
        assert not tasks.holds_process_lock(profile=profile)
    finally:
        requested.set()
        worker._wake.set()
        thread.join(5)
        assert not thread.is_alive()


def test_claim_sqlite_busy_is_not_retried_by_connection_handler(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    error = _sqlite_failure(tmp_path, "busy")
    original_connect = db.connect
    claims = []
    def explicit_claim_fault(con, owner_id, *, profile=None):
        assert tasks.holds_process_lock(profile=profile)
        claims.append(owner_id)
        raise error
    monkeypatch.setattr(tasks, "claim", explicit_claim_fault)
    with pytest.raises(sqlite3.OperationalError) as caught:
        worker.process_pending(follow=True)
    assert caught.value is error and len(claims) == 1
    _assert_released(profile, original_connect)


def test_dispatch_sqlite_busy_keeps_original_failed_once_behavior(worker_store, tmp_path, monkeypatch):
    profile = worker_store
    queued = tasks.submit("report", profile=profile)
    error = _sqlite_failure(tmp_path, "busy")
    calls = []
    def explicit_model_free_dispatch_fault(job):
        worker.check_admission()
        calls.append(job["job_id"])
        raise error
    monkeypatch.setattr(worker, "dispatch", explicit_model_free_dispatch_fault)
    assert worker.process_pending(max_jobs=1, follow=True) == 1
    assert calls == [queued["task_id"]]
    job = tasks.get(queued["task_id"], profile=profile)
    assert job["status"] == "failed" and job["attempts"] == 1
