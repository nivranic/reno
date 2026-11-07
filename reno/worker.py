"""Cross-process single-writer task worker with persistent leases and heartbeats."""
from contextlib import contextmanager
import sys
import sqlite3
import threading
import random
import time
import uuid

from . import config, db, pipeline, storage_admin, tasks
from .budget_contract import BudgetDeferred

_threads = {}
_guard = threading.Lock()
_wake = threading.Event()
_current = threading.local()
_MISSING = object()


@contextmanager
def _execution(job, owner_id, stop, *, profile=None):
    previous_job = getattr(_current, "job", _MISSING)
    previous_stop = getattr(_current, "stop", _MISSING)
    previous_profile = getattr(_current, "profile", _MISSING)
    _current.job = {"job_id": job["job_id"], "owner_id": owner_id,
                    "continuation_id": job.get("continuation_id"),
                    "execution_generation": job.get("execution_generation", 1),
                    "kind": job["kind"], "video_id": job.get("video_id")}
    _current.stop = stop
    _current.profile = profile
    try:
        yield
    finally:
        for name, previous in (("job", previous_job), ("stop", previous_stop), ("profile", previous_profile)):
            if previous is _MISSING:
                delattr(_current, name)
            else:
                setattr(_current, name, previous)


def execution_context():
    """Bounded identity copy for a model/download adapter; no raw payload."""
    value = getattr(_current, "job", None)
    return None if value is None else dict(value)


def _execution_profile():
    """Private immutable profile; never included in the adapter identity DTO."""
    return getattr(_current, "profile", None)


def _profile_matches(profile):
    return storage_admin.profile().identity == profile.identity


def check_admission(*, connection=None):
    """Check the current owner and manual pause before a step or HTTP call."""
    value = execution_context()
    if value is not None:
        profile = _execution_profile()
        if profile is not None and not _profile_matches(profile):
            raise BudgetDeferred("ledger_unavailable")
        stop = getattr(_current, "stop", None)
        if stop is not None and stop.is_set():
            # Nothing new starts after shutdown or lost lease. The current
            # paid response still commits, then work resumes under a new owner.
            raise BudgetDeferred("manual_pause")
        parameters = {} if connection is None else {"connection": connection}
        tasks.check_admission(value["job_id"], value["owner_id"], value["continuation_id"],
                              value["execution_generation"], profile=profile, **parameters)


class _Stop:
    def __init__(self, requested, lost):
        self.requested, self.lost = requested, lost

    def is_set(self):
        return self.requested.is_set() or self.lost.is_set()


@contextmanager
def _wait_for_process_lock(follow, requested_stop, profile):
    while not requested_stop.is_set() and _profile_matches(profile):
        with tasks.process_lock(profile=profile) as acquired:
            if acquired:
                yield True
                return
        if not follow:
            break
        # Another CLI/service may finish after this daemon starts. Keep a
        # follower alive so durable pending work does not need another click.
        _wake.wait(2)
        _wake.clear()
    yield False


def dispatch(job):
    tasks.require_local()
    payload = db.uj(job.get("payload_json"), {})
    if job["kind"] in ("ingest", "favlist"):
        from . import import_tasks
        profile = _execution_profile()
        if profile is None:
            raise tasks.TaskError("导入需要原worker存储身份", "TASK_OWNER_LOST", 409)
        return (import_tasks.run_ingest if job["kind"] == "ingest" else import_tasks.run_favlist)(job, profile=profile)
    if job["kind"] == "sync":
        from .sync_agent import run_cycle
        return run_cycle(stop_event=getattr(_current, "stop", None))
    if job["kind"] == "personal_mirror":
        from . import personal_mirror_admin
        profile = _execution_profile()
        if profile is None:
            raise tasks.TaskError("个人镜像需要原worker存储身份", "TASK_OWNER_LOST", 409)
        tasks.normalize("personal_mirror", job.get("video_id"), payload, internal=True)
        return personal_mirror_admin.deliver_worker(profile,
            operation_id=payload["operation_id"], expected_request_sha256=payload["request_sha256"],
            check_cancelled=check_admission)
    if job["kind"] == "process":
        return pipeline.run_video(job["video_id"], force=payload.get("force", False))
    if job["kind"] == "judge":
        from . import judge
        profile = _execution_profile()
        con = db.connect(None if profile is None else profile.database)
        try:
            result = judge.run(con, payload.get("threshold", 0.22))
            con.execute("INSERT INTO meta(key,value) VALUES('last_judge',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (tasks.utc_now(),))
            con.commit()
            return result
        finally:
            con.close()
    if job["kind"] == "report":
        from . import report
        if payload.get("mode", "static") == "summary":
            return {"report": report.summary_report().name, "mode": "summary"}
        return report.run_all()
    raise tasks.TaskError("不支持的任务类型")


def process_pending(max_jobs: int = 0, follow: bool = False, stop_event=None) -> int:
    if config.is_cloud():
        return 0
    profile = storage_admin.profile()
    database_path = profile.database.resolve()
    requested_stop = stop_event or threading.Event()
    with _wait_for_process_lock(follow, requested_stop, profile) as acquired:
        if not acquired:
            return 0
        owner_id = str(uuid.uuid4())
        while not tasks.acquire_worker(owner_id, profile=profile):
            # Restart after a hard crash may precede lease expiry. A service
            # daemon must retry, otherwise queued work would await a new click.
            if not follow or requested_stop.is_set() or not _profile_matches(profile):
                return 0
            _wake.wait(2)
            _wake.clear()
        stop, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not stop.wait(10):
                if requested_stop.is_set() or not _profile_matches(profile):
                    lost.set()
                    return
                try:
                    if not tasks.renew_worker(owner_id, profile=profile):
                        lost.set()
                        return
                except Exception:
                    # The OS lock still fences other writers. Stop starting work
                    # until ownership can be demonstrated by a fresh process.
                    lost.set()
                    return

        pulse = threading.Thread(target=heartbeat, daemon=True, name="reno-task-heartbeat")
        pulse.start()
        done = 0
        next_sync = 0.0
        review_due = False
        try:
            while not lost.is_set() and not requested_stop.is_set():
                if not _profile_matches(profile):
                    break
                if follow and time.monotonic() >= next_sync:
                    interval = 60
                    if config.get("sync_enabled") is True:
                        try:
                            from .sync_agent import settings
                            interval = settings()["interval"]
                            tasks.submit_sync(profile=profile)
                            review_due = True
                        except Exception:
                            # Invalid/disabled configuration never starts I/O.
                            # Do not leak configuration through worker logs.
                            pass
                    next_sync = time.monotonic() + interval * random.uniform(1, 1.1)
                try:
                    con = db.connect(profile.database)
                except sqlite3.OperationalError as error:
                    if not follow:
                        raise
                    code = getattr(error, "sqlite_errorcode", None)
                    if type(code) is not int or code & 0xff not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise
                    # Retry only connection/schema reads. Claim, dispatch and
                    # writes retain their existing error and ownership paths.
                    _wake.wait(2)
                    _wake.clear()
                    continue
                try:
                    job = tasks.claim(con, owner_id, profile=profile)
                finally:
                    con.close()
                if job is None:
                    if follow and review_due and config.get("sync_enabled") is True:
                        review_due = False
                        try:
                            waiting = tasks.sync_waiting_job(owner_id, profile=profile)
                            if waiting is not None:
                                from .sync_agent import run_cycle
                                # The original SQL row remains pending, with its
                                # exact wait and lineage. Source admission still
                                # rejects this context; this entry exchanges only
                                # reviews and cannot finish the source job.
                                with _execution(waiting, owner_id, _Stop(requested_stop, lost), profile=profile):
                                    run_cycle(review_only=True, stop_event=getattr(_current, "stop", None))
                        except Exception:
                            # Review transport errors leave the source wait
                            # unchanged and use the usual interval on retry.
                            pass
                    if not follow:
                        break
                    _wake.wait(2)
                    _wake.clear()
                    continue
                try:
                    with _execution(job, owner_id, _Stop(requested_stop, lost), profile=profile):
                        check_admission()
                        result = dispatch(job)
                    failed = isinstance(result, dict) and any(
                        isinstance(value, str) and value.startswith("ERROR") for value in result.values())
                    tasks.finish(job["job_id"], owner_id, result=None if failed else result,
                                 error="处理失败，可继续未完成步骤" if failed else None, profile=profile)
                except BudgetDeferred as deferred:
                    # Waiting is neither a failure nor a new logical execution.
                    tasks.defer(job["job_id"], owner_id, deferred, profile=profile)
                except Exception:
                    # Processing logs remain local. Never expose exception text
                    # containing paths, provider URLs or credentials via /tasks.
                    tasks.finish(job["job_id"], owner_id, error="任务执行失败，请检查处理记录后重试", profile=profile)
                except BaseException:
                    # A foreground Ctrl+C must not leave a running scope that
                    # blocks future sync scheduling after we release the lease.
                    tasks.finish(job["job_id"], owner_id, error="任务已中断，可重试未完成步骤", profile=profile)
                    raise
                finally:
                    if job["kind"] == "sync":
                        review_due = False
                        value = config.get("sync_interval_seconds", 60)
                        interval = value if type(value) is int and 15 <= value <= 3600 else 60
                        next_sync = time.monotonic() + interval * random.uniform(1, 1.1)
                done += 1
                if max_jobs and done >= max_jobs:
                    break
            return done
        finally:
            stop.set()
            pulse.join(timeout=1)
            tasks.release_worker(owner_id, database_path)


def ensure_running() -> bool:
    """Ensure a local daemon observes the durable queue; never dispatch directly."""
    if config.is_cloud():
        return False
    key = str(config.db_path().resolve())
    with _guard:
        existing = _threads.get(key)
        current = existing[0] if existing else None
        if current is None or not current.is_alive():
            stop = threading.Event()
            current = threading.Thread(target=process_pending, kwargs={"follow": True, "stop_event": stop},
                                       daemon=True, name="reno-task-worker")
            _threads[key] = (current, stop)
            current.start()
        _wake.set()
    return True


def stop_running(timeout=5):
    """Graceful service/test shutdown. In-flight work owns its lock until done."""
    with _guard:
        threads = list(_threads.values())
        for _, stop in threads:
            stop.set()
        _wake.set()
    for thread, _ in threads:
        thread.join(timeout=timeout)
    return all(not thread.is_alive() for thread, _ in threads)


if __name__ == "__main__":
    process_pending(max_jobs=next((int(a) for a in sys.argv[1:] if a.isdigit()), 0),
                    follow="--follow" in sys.argv)
