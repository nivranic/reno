"""Task roots cooperate with EX; all libraries and child processes are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from reno import config, db, storage_admin, storage_lifecycle as lifecycle, tasks


@pytest.fixture()
def store(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(value.database)
    storage_admin.initialize(value)
    with storage_admin.connection(value) as con:
        db.upsert_asset(con, {"video_id": "video", "content_sha256": "synthetic"})
        con.commit()
    return value


def prepare(value, action):
    if action in ("submit", "sync"):
        return None
    job = tasks.submit("process", "video", profile=value)["job_id"]
    with storage_admin.connection(value) as con:
        if action == "retry":
            con.execute("UPDATE job SET status='failed' WHERE job_id=?", (job,))
        else:
            con.execute("UPDATE job SET status='running',owner_id='owner' WHERE job_id=?", (job,))
        con.commit()
    return job


def invoke(value, action, job=None):
    if action == "submit":
        return tasks.submit("process", "video", profile=value)
    if action == "sync":
        return tasks.submit_sync(profile=value)
    if action == "retry":
        return tasks.retry(job, profile=value)
    return tasks.finish(job, "owner", error="任务已中断" if action == "cancel" else None,
                        profile=value)


def rows(value):
    con = db.connect(value.database)
    try:
        return [dict(row) for row in con.execute("SELECT * FROM job ORDER BY job_id")]
    finally:
        con.close()


_CHILD = """
import json, pathlib, sys
from reno import db, storage_lifecycle as lifecycle, tasks
arg = json.loads(sys.argv[1])
value = lifecycle.StorageProfile(**arg['profile'])
original = db.connect
def observed_connect(*args, **kwargs):
    lifecycle.require_guard(value)
    pathlib.Path(arg['connected']).write_text('connected', encoding='ascii')
    return original(*args, **kwargs)
db.connect = observed_connect
pathlib.Path(arg['ready']).write_text('attempting', encoding='ascii')
action, job = arg['action'], arg['job']
if action == 'submit':
    result = tasks.submit('process', 'video', profile=value)
elif action == 'sync':
    result = tasks.submit_sync(profile=value)
elif action == 'retry':
    result = tasks.retry(job, profile=value)
else:
    result = tasks.finish(job, 'owner', error='cancelled' if action == 'cancel' else None, profile=value)
pathlib.Path(arg['result']).write_text(json.dumps(result), encoding='utf-8')
"""


@pytest.mark.parametrize("action", ["submit", "retry", "finish", "cancel", "sync"])
def test_real_ex_blocks_root_writers_before_connection(store, tmp_path, action):
    job = prepare(store, action)
    ready, connected, result = (tmp_path / name for name in ("ready", "connected", "result"))
    arg = {"profile": {name: str(getattr(store, name)) for name in
                       ("database", "media", "reports", "checkpoints")},
           "ready": str(ready), "connected": str(connected), "result": str(result),
           "action": action, "job": job}
    child = None
    try:
        with lifecycle.exclusive(store):
            child = subprocess.Popen([sys.executable, "-B", "-u", "-c", _CHILD, json.dumps(arg)],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 15
            while not ready.exists():
                if child.poll() is not None:
                    pytest.fail(child.communicate()[0].decode("utf-8", "replace"))
                assert time.monotonic() < deadline, "Synthetic task writer failed to start"
                time.sleep(0.02)
            time.sleep(0.15)
            assert child.poll() is None and not connected.exists() and not result.exists()
        output, _ = child.communicate(timeout=15)
        assert child.returncode == 0, output.decode("utf-8", "replace")
        assert connected.exists() and result.exists()
        current = rows(store)
        if action in ("finish", "cancel"):
            assert current[0]["status"] == ("failed" if action == "cancel" else "done")
        elif action == "retry":
            assert [row["status"] for row in current] == ["failed", "pending"]
        else:
            assert len(current) == 1 and current[0]["status"] == "pending"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_enqueue_requires_prior_guard_without_starting_or_ending_transaction(store):
    con = db.connect(store.database)
    try:
        for begun in (False, True):
            if begun:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO meta(key,value) VALUES ('caller-marker','uncommitted')")
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                tasks.enqueue(con, "video", profile=store)
            assert caught.value.code == "STORAGE_GUARD_REQUIRED"
            assert con.in_transaction is begun
            assert con.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
            if begun:
                assert con.execute("SELECT value FROM meta WHERE key='caller-marker'").fetchone()[0] == "uncommitted"
                con.rollback()
    finally:
        con.close()


def test_enqueue_borrows_existing_exact_guard_and_never_commits_caller_transaction(store, monkeypatch):
    request = str(uuid.uuid4())
    with storage_admin.connection(store) as con:
        con.execute("BEGIN IMMEDIATE")
        def forbidden(*args, **kwargs):
            raise AssertionError("enqueue attempted SQL→SH")
        monkeypatch.setattr(lifecycle, "shared", forbidden)
        first = tasks.enqueue(con, "video", request_id=request)
        assert tasks.enqueue(con, "video", request_id=request) == first
        with pytest.raises(tasks.TaskError) as caught:
            tasks.enqueue(con, kind="judge", request_id=request)
        assert caught.value.code == "IDEMPOTENCY_KEY_REUSED"
        assert con.in_transaction
        con.rollback()
    assert rows(store) == []


def test_enqueue_rejects_guard_for_another_profile_without_losing_transaction(store, tmp_path):
    other = lifecycle.StorageProfile(tmp_path / "other/db.sqlite", tmp_path / "other/media",
                                     tmp_path / "other/reports", tmp_path / "other/checkpoints")
    db.initialize(other.database)
    storage_admin.initialize(other)
    with lifecycle.shared(other):
        con = db.connect(store.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                tasks.enqueue(con, "video", profile=store)
            assert caught.value.code == "STORAGE_PROFILE_MISMATCH" and con.in_transaction
            con.rollback()
        finally:
            con.close()


@pytest.mark.parametrize("action", ["submit", "retry", "finish", "cancel", "sync"])
def test_guard_survives_commit_and_baseexception_rolls_back_exact_error(store, monkeypatch, action):
    job = prepare(store, action)
    before = rows(store)
    class Cancelled(BaseException):
        pass
    cancellation = Cancelled("synthetic cancellation at commit")
    observed = []
    original = db.connect
    class Connection:
        def __init__(self, con):
            self.con = con
        def __getattr__(self, name):
            return getattr(self.con, name)
        def commit(self):
            lifecycle.require_guard(store)
            assert self.con.in_transaction
            observed.append("commit")
            raise cancellation
        def rollback(self):
            lifecycle.require_guard(store)
            observed.append("rollback")
            return self.con.rollback()
        def close(self):
            lifecycle.require_guard(store)
            observed.append("close")
            return self.con.close()
    def guarded_open(path=None):
        lifecycle.require_guard(store)
        observed.append("open")
        return Connection(original(path))
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", guarded_open)
        with pytest.raises(Cancelled) as caught:
            invoke(store, action, job)
    assert caught.value is cancellation
    assert observed == ["open", "commit", "rollback", "close"]
    assert lifecycle.current_guard() is None and rows(store) == before


@pytest.mark.parametrize("action", ["submit", "retry", "finish", "sync"])
@pytest.mark.parametrize("primary_kind", ["error", "cancel"])
def test_failed_rollback_preserves_primary_exception_identity(store, monkeypatch, action, primary_kind):
    job = prepare(store, action)
    before = rows(store)
    class Cancelled(BaseException):
        pass
    primary = (Cancelled("synthetic cancellation") if primary_kind == "cancel"
               else OSError("synthetic commit failure"))
    cleanup = RuntimeError("synthetic rollback failure")
    observed = []
    original = db.connect
    class Connection:
        def __init__(self, con):
            self.con = con
        def __getattr__(self, name):
            return getattr(self.con, name)
        def commit(self):
            lifecycle.require_guard(store)
            assert self.con.in_transaction
            observed.append("commit")
            raise primary
        def rollback(self):
            lifecycle.require_guard(store)
            observed.append("rollback")
            raise cleanup
        def close(self):
            lifecycle.require_guard(store)
            observed.append("close")
            return self.con.close()
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", lambda path=None: Connection(original(path)))
        with pytest.raises(type(primary)) as caught:
            invoke(store, action, job)
    assert caught.value is primary
    assert observed == ["commit", "rollback", "close"]
    assert lifecycle.current_guard() is None and rows(store) == before


def test_retry_receipt_keeps_original_job_after_new_job_has_finished(store):
    old = prepare(store, "retry")
    request = str(uuid.uuid4())
    next_job = tasks.retry(old, request, profile=store)
    assert tasks.retry(old, request, profile=store)["job_id"] == next_job["job_id"]
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='done' WHERE job_id=?", (next_job["job_id"],))
        con.commit()
    assert tasks.retry(old, request, profile=store)["job_id"] == next_job["job_id"]
    assert len(rows(store)) == 2 and rows(store)[0]["status"] == "failed"


@pytest.mark.parametrize("action", ["submit", "retry", "sync"])
def test_cloud_role_rejects_before_opening_or_waiting_for_storage(store, monkeypatch, action):
    config.set_local({"node_role": "cloud"})
    def forbidden(*args, **kwargs):
        raise AssertionError("cloud task attempted local storage")
    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(lifecycle, "shared", forbidden)
    with pytest.raises(tasks.TaskError) as caught:
        invoke(store, action, 999)
    assert caught.value.code == "LOCAL_EXECUTION_UNAVAILABLE" and caught.value.status == 403


def test_missing_facilities_do_not_initialize_or_open_database(tmp_path, monkeypatch):
    value = lifecycle.StorageProfile(tmp_path / "missing/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    def forbidden(*args, **kwargs):
        raise AssertionError("uninitialized storage attempted SQLite")
    monkeypatch.setattr(db, "connect", forbidden)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        tasks.submit("judge", profile=value)
    assert caught.value.code == "STORAGE_NOT_INITIALIZED" and not value.database.parent.exists()


@pytest.mark.parametrize("value", [False, 0, {}], ids=["false", "zero", "object"])
def test_explicit_invalid_profile_cannot_fall_back_to_configured_library(monkeypatch, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid profile attempted SQLite")
    monkeypatch.setattr(db, "connect", forbidden)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        tasks.submit("judge", profile=value)
    assert caught.value.code == "STORAGE_INVALID_PROFILE"


def test_schema_error_releases_guard_without_implicit_migration(store):
    with storage_admin.connection(store) as con:
        con.execute("UPDATE meta SET value='9' WHERE key='migration_version'")
        con.commit()
    with pytest.raises(db.SchemaVersionError):
        tasks.submit("judge", profile=store)
    assert lifecycle.current_guard() is None
    with lifecycle.exclusive(store, timeout=0):
        pass


def test_custom_profile_does_not_write_configured_library(store):
    configured = db.connect()
    try:
        before = configured.execute("SELECT COUNT(*) FROM job").fetchone()[0]
        task = tasks.submit("process", "video", profile=store)
        assert rows(store)[0]["job_id"] == task["job_id"]
        assert configured.execute("SELECT COUNT(*) FROM job").fetchone()[0] == before
    finally:
        configured.close()


def test_lease_claim_and_recovery_preserve_the_exact_unfinished_selector(store, monkeypatch):
    task = tasks.submit("process", "video", {"force": True}, profile=store)
    monkeypatch.setattr(config, "DATA", store.database.parent)
    def selector():
        return [(row["job_id"], row["video_id"], row["kind"], row["payload_json"])
                for row in rows(store) if row["status"] in ("pending", "running")]
    before = selector()
    assert tasks.acquire_worker("first")
    con = db.connect(store.database)
    try:
        assert tasks.claim(con, "first")["job_id"] == task["job_id"]
    finally:
        con.close()
    assert selector() == before and tasks.renew_worker("first")
    with storage_admin.connection(store) as con:
        con.execute("UPDATE worker_lease SET lease_until=0")
        con.execute("UPDATE job SET lease_until=0")
        con.commit()
    assert tasks.acquire_worker("replacement") and selector() == before
    assert rows(store)[0]["status"] == "pending"
    tasks.release_worker("replacement")
    assert selector() == before


def test_foreign_worker_lock_never_uses_configured_database_or_data(store, monkeypatch):
    def forbidden():
        raise AssertionError("foreign worker lock read configured database")
    class ForbiddenData:
        def __truediv__(self, other):
            raise AssertionError("foreign worker lock read configured DATA")
    monkeypatch.setattr(config, "db_path", forbidden)
    monkeypatch.setattr(config, "DATA", ForbiddenData())
    assert not tasks.holds_process_lock(profile=store)
    with tasks.process_lock(profile=store) as acquired:
        assert acquired and tasks.holds_process_lock(profile=store)
        assert (store.database.parent / "worker.lock").is_file()
    assert not tasks.holds_process_lock(profile=store)


def test_nested_other_database_lock_has_exact_proof_and_restores_outer(store, tmp_path):
    other = lifecycle.StorageProfile(tmp_path / "other/reno.db", tmp_path / "other/media",
                                     tmp_path / "other/reports", tmp_path / "other/checkpoints")
    with tasks.process_lock(profile=store) as first:
        assert first and tasks.holds_process_lock(profile=store)
        assert not tasks.holds_process_lock(profile=other)
        with tasks.process_lock(profile=other) as second:
            assert second and tasks.holds_process_lock(profile=other)
            assert not tasks.holds_process_lock(profile=store)
            with tasks.process_lock(profile=other) as contested:
                assert not contested and tasks.holds_process_lock(profile=other)
            assert tasks.holds_process_lock(profile=other)
        assert tasks.holds_process_lock(profile=store)
        assert not tasks.holds_process_lock(profile=other)
    assert not tasks.holds_process_lock(profile=store)


@pytest.mark.parametrize("value", [False, 0, {}], ids=["false", "zero", "object"])
def test_worker_lock_rejects_invalid_profile_without_disturbing_outer_proof(store, value):
    with tasks.process_lock(profile=store) as acquired:
        assert acquired
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            with tasks.process_lock(profile=value):
                pytest.fail("invalid profile acquired a worker lock")
        assert caught.value.code == "STORAGE_INVALID_PROFILE"
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            tasks.holds_process_lock(profile=value)
        assert caught.value.code == "STORAGE_INVALID_PROFILE"
        assert tasks.holds_process_lock(profile=store)


def test_nested_failure_and_exception_restore_default_worker_proof(store, monkeypatch):
    monkeypatch.setattr(config, "DATA", store.database.parent)
    class Cancelled(BaseException):
        pass
    error = Cancelled("synthetic nested lock cancellation")
    with tasks.process_lock() as acquired:
        assert acquired and tasks.holds_process_lock()
        with pytest.raises(Cancelled) as caught:
            with tasks.process_lock(profile=store) as contested:
                assert not contested and tasks.holds_process_lock()
                raise error
        assert caught.value is error and tasks.holds_process_lock(profile=store)
    assert not tasks.holds_process_lock()
