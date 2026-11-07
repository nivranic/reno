"""Persistent, single-writer maintenance queue shared by CLI and web requests."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import time
import threading
import uuid

from . import config, db, storage_admin, storage_lifecycle
from .budget_contract import BudgetDeferred

LEASE_MS = 120_000
WORKER_SCOPE = "knowledge_tasks"
KINDS = {"process", "judge", "report", "ingest", "favlist"}
_lock_context = threading.local()


class TaskError(ValueError):
    def __init__(self, message, code="INVALID_TASK", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clock_ms():
    return int(time.time() * 1000)


def require_local():
    if config.is_cloud():
        raise TaskError("本节点暂不能执行处理、争议或报告任务", "LOCAL_EXECUTION_UNAVAILABLE", 403)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _request_id(value):
    if value is None:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise TaskError("request_id 必须是 UUID") from None


def normalize(kind, video_id=None, payload=None, *, internal=False):
    if kind == "personal_mirror" and internal:
        if (video_id is not None or type(payload) is not dict
                or set(payload) != {"operation_id", "request_sha256"}):
            raise TaskError("个人镜像任务仅接受原操作编号和请求摘要", "PERSONAL_MIRROR_TASK_INVALID")
        operation_id, request_sha = payload["operation_id"], payload["request_sha256"]
        try:
            valid_id = type(operation_id) is str and str(uuid.UUID(operation_id)) == operation_id
        except (ValueError, AttributeError):
            valid_id = False
        if (not valid_id or type(request_sha) is not str or len(request_sha) != 64
                or any(char not in "0123456789abcdef" for char in request_sha)):
            raise TaskError("个人镜像原请求身份无效", "PERSONAL_MIRROR_TASK_INVALID")
        return "personal_mirror:" + operation_id, dict(payload)
    if kind == "sync" and internal:
        if video_id is not None or payload not in (None, {}):
            raise TaskError("同步任务不接受外部参数")
        return "sync:cycle", {}
    if not isinstance(kind, str) or kind not in KINDS:
        raise TaskError("kind 必须为 process、judge、report、ingest 或 favlist")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise TaskError("payload 必须是对象")
    payload = dict(payload)
    if kind in ("ingest", "favlist"):
        from . import import_tasks
        return (import_tasks.normalize_ingest if kind == "ingest" else import_tasks.normalize_favlist)(
            video_id, payload, internal=internal)
    if kind == "process":
        if not isinstance(video_id, str) or not video_id.strip() or len(video_id) > 200:
            raise TaskError("process 任务需要 video_id")
        if payload.keys() - {"force"} or type(payload.get("force", False)) is not bool:
            raise TaskError("无效的视频处理参数")
        return "process:" + video_id, {"force": payload.get("force", False)}
    if video_id is not None:
        raise TaskError("judge/report 任务不能指定 video_id")
    if kind == "judge":
        threshold = payload.get("threshold", 0.22)
        if (payload.keys() - {"threshold"} or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or not 0 <= threshold <= 1):
            raise TaskError("threshold 必须为 0 至 1 的数值")
        return "judge:all", {"threshold": threshold}
    mode = payload.get("mode", "static")
    if payload.keys() - {"mode"} or mode not in ("static", "summary"):
        raise TaskError("report mode 必须为 static 或 summary")
    return "report:" + mode, {"mode": mode}


def serialize(row):
    if row is None:
        return None
    value = dict(row)
    local_active = value["kind"] in KINDS and value["status"] in ("pending", "running") and not config.is_cloud()
    paused = bool(value.get("manual_paused", 0))
    payload = db.uj(value.get("payload_json"), {})
    if value["kind"] == "favlist":
        payload = {key: item for key, item in payload.items() if key != "cookies"}
    if value["kind"] == "ingest" and payload.get("source") == "file":
        import ntpath
        payload = {**payload, "file_name": ntpath.basename(payload["target"])}
        payload.pop("target", None)
    return {"task_id": value["job_id"], "job_id": value["job_id"],
            "kind": value["kind"], "video_id": value["video_id"],
            "status": value["status"], "attempts": value["attempts"],
            "request_id": value.get("request_id"),
            "payload": payload,
            "result": db.uj(value.get("result_json")),
            "error": value["error"], "created_at": value["created_at"],
            "updated_at": value["updated_at"], "started_at": value.get("started_at"),
            "completed_at": value.get("completed_at"),
            "can_retry": value["status"] == "failed" and value["kind"] != "personal_mirror" and not config.is_cloud(),
            "not_before": value.get("not_before"), "blocked_reason": value.get("blocked_reason"),
            "wait_mode": value.get("wait_mode", "none"), "manual_paused": paused,
            "budget_operation_id": value.get("budget_operation_id"),
            "can_pause": local_active and not paused,
            "can_resume": local_active and (paused or value.get("wait_mode", "none") != "none")}


def _lineage(continuation_id, execution_generation):
    try:
        if (type(continuation_id) is not str or str(uuid.UUID(continuation_id)) != continuation_id
                or type(execution_generation) is not int or not 1 <= execution_generation <= 2**63 - 1):
            raise ValueError
    except (ValueError, AttributeError):
        raise TaskError("任务执行身份不可用", "TASK_LINEAGE_UNAVAILABLE", 409) from None
    return continuation_id, execution_generation


def _same_lineage(row, lineage):
    if row is None or (row["continuation_id"], row["execution_generation"]) != lineage:
        raise TaskError("同一范围已有另一执行任务", "TASK_LINEAGE_CONFLICT", 409)


def _writer_profile(value):
    if value is None:
        return storage_admin.profile()
    if not isinstance(value, storage_lifecycle.StorageProfile):
        raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_PROFILE", 400)
    return value


def _rollback_after_error(con):
    """Best-effort cleanup inside a primary exception handler only."""
    try:
        con.rollback()
    except BaseException:
        # A failed rollback must not replace cancellation or commit failure.
        # The enclosing protected connection still closes before releasing SH.
        pass


def enqueue(con, video_id=None, kind="process", payload=None, request_id=None, *, internal=False,
            profile=None, continuation_id=None, execution_generation=None):
    """Queue under the caller's existing lifetime guard; never acquire SQL→SH.

    The guard must outlive the encompassing commit, including when this helper
    starts a transaction. Public submit/retry wrappers acquire it before opening
    SQLite; callers sharing their own connection must do the same.
    """
    require_local()
    profile = storage_admin.from_connection(con, profile=profile)
    storage_lifecycle.require_guard(profile)
    scope, payload = normalize(kind, video_id, payload, internal=internal)
    # Only internal callers (retry) supply a lineage. Public submit never accepts
    # it. Do not generate anything for a receipt or an existing active scope.
    lineage = None
    if continuation_id is not None or execution_generation is not None:
        lineage = _lineage(continuation_id, execution_generation)
    request_id = _request_id(request_id)
    signature = hashlib.sha256(_canonical({"kind": kind, "video_id": video_id,
                                           "payload": payload}).encode()).hexdigest()
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    if con.execute("SELECT 1 FROM meta WHERE key=?", ("import:batch-receipt:v1:" + request_id,)).fetchone():
        raise TaskError("此请求编号已用于批量导入", "IDEMPOTENCY_KEY_REUSED", 409)
    receipt = con.execute("SELECT * FROM task_request WHERE request_id=?", (request_id,)).fetchone()
    if receipt:
        if receipt["payload_signature"] != signature:
            raise TaskError("此请求编号已用于另一任务", "IDEMPOTENCY_KEY_REUSED", 409)
        target = con.execute("SELECT * FROM job WHERE job_id=?", (receipt["job_id"],)).fetchone()
        if (target is None or target["kind"] != kind or target["scope_key"] != scope
                or db.uj(target["payload_json"]) != payload
                or (kind != "ingest" or video_id is not None) and target["video_id"] != video_id):
            raise TaskError("任务恢复回执不一致", "IMPORT_RECOVERY_INVALID", 409)
        if lineage is not None:
            _same_lineage(target, lineage)
        return receipt["job_id"]
    if kind == "process" and not db.get_asset(con, video_id):
        raise TaskError("视频不存在", "NOT_FOUND", 404)
    active = con.execute("SELECT * FROM job WHERE scope_key=? AND status IN ('pending','running')",
                         (scope,)).fetchone()
    if active:
        if lineage is not None:
            _same_lineage(active, lineage)
        if db.uj(active["payload_json"], {}) != payload:
            raise TaskError("同一范围已有不同参数的任务在运行", "TASK_SCOPE_BUSY", 409)
        job_id = active["job_id"]
    else:
        when = utc_now()
        if lineage is None:
            lineage = (str(uuid.uuid4()), 1)
        cur = con.execute("""INSERT INTO job
            (video_id,kind,status,created_at,updated_at,request_id,scope_key,payload_json,
             continuation_id,execution_generation)
            VALUES (?,?,'pending',?,?,?,?,?,?,?)""",
            (video_id, kind, when, when, request_id, scope, _canonical(payload), *lineage))
        job_id = cur.lastrowid
    con.execute("INSERT INTO task_request(request_id,job_id,payload_signature,created_at) VALUES (?,?,?,?)",
                (request_id, job_id, signature, utc_now()))
    return job_id


def submit(kind, video_id=None, payload=None, request_id=None, *, profile=None):
    require_local()
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            job_id = enqueue(con, video_id, kind, payload, request_id, profile=profile)
            row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
            storage_lifecycle.require_guard(profile)
            con.commit()
            return serialize(row)
        except BaseException:
            _rollback_after_error(con)
            raise


def submit_sync(*, profile=None):
    """Only the local CLI/worker scheduler may enqueue an internal sync cycle."""
    # A normalized internal sync task has no video selector. Its cycle installs
    # actual Home roots independently; protect this queue write nonetheless.
    require_local()
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            job_id = enqueue(con, kind="sync", internal=True, profile=profile)
            storage_lifecycle.require_guard(profile)
            con.commit()
            return job_id
        except BaseException:
            _rollback_after_error(con)
            raise


def submit_personal_mirror(operation_id, expected_request_sha256, request_id=None, *, profile=None):
    """Explicitly queue only an existing immutable personal mirror request.

    A task receipt has its own UUID. Neither this entry nor startup/timers may
    capture a new snapshot; the worker rechecks the exact original selector.
    """
    require_local()
    payload = {"operation_id": operation_id, "request_sha256": expected_request_sha256}
    normalize("personal_mirror", payload=payload, internal=True)
    request_id = _request_id(request_id)
    if request_id == operation_id:
        raise TaskError("任务请求编号须独立于个人镜像操作编号", "PERSONAL_MIRROR_TASK_REQUEST_CONFLICT", 409)
    profile = _writer_profile(profile)
    from . import personal_mirror_admin
    # The original file observation finishes before SQLite writer ownership.
    # Delivery still verifies it again, under actual worker and state ownership.
    personal_mirror_admin.status(profile, operation_id=operation_id,
                                 expected_request_sha256=expected_request_sha256)
    with storage_admin.connection(profile) as con:
        try:
            job_id = enqueue(con, kind="personal_mirror", payload=payload,
                             request_id=request_id, internal=True, profile=profile)
            row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
            storage_lifecycle.require_guard(profile)
            con.commit()
            return serialize(row)
        except BaseException:
            _rollback_after_error(con)
            raise


def retry(job_id, request_id=None, *, profile=None):
    require_local()
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["kind"] not in KINDS:
                raise TaskError("任务不存在", "NOT_FOUND", 404)
            if row["status"] != "failed":
                raise TaskError("仅失败任务可继续处理", "TASK_NOT_RETRYABLE", 409)
            lineage = _lineage(row["continuation_id"], row["execution_generation"])
            payload = db.uj(row["payload_json"], {})
            if row["kind"] == "process":
                payload["force"] = False  # Recovery must never erase completed steps.
            new_id = enqueue(con, row["video_id"], row["kind"], payload, request_id,
                             internal=row["kind"] in ("ingest", "favlist"), profile=profile,
                             continuation_id=lineage[0], execution_generation=lineage[1])
            result = serialize(con.execute("SELECT * FROM job WHERE job_id=?", (new_id,)).fetchone())
            storage_lifecycle.require_guard(profile)
            con.commit()
            return result
        except BaseException:
            _rollback_after_error(con)
            raise


def get(job_id, *, include_internal=False, profile=None):
    con = db.connect(None if profile is None else _writer_profile(profile).database)
    try:
        row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
        if row is not None and row["kind"] not in KINDS and not include_internal:
            return None
        return serialize(row)
    finally:
        con.close()


def list_tasks(limit=50):
    con = db.connect()
    try:
        return [serialize(row) for row in con.execute(
            "SELECT * FROM job WHERE kind IN ('process','judge','report','ingest','favlist') ORDER BY job_id DESC LIMIT ?", (limit,))]
    finally:
        con.close()


@contextmanager
def process_lock(*, profile=None):
    """An OS lock outlives expired heartbeats and is released on process death.

    The persistent lease provides ownership/recovery state; this extra lock
    prevents a slow living writer from overlapping its replacement after TTL.
    An explicit profile selects its own database directory and thread proof;
    it does not consult the configured DATA or database.
    """
    if profile is None:
        path = config.DATA / "worker.lock"
        database = config.db_path().resolve()
    else:
        profile = _writer_profile(profile)
        path = profile.database.parent / "worker.lock"
        database = profile.database.resolve()
    from . import sync_media as safe
    resources = ExitStack()
    try:
        directory = resources.enter_context(safe._Directory(path.parent, create=True))
        stream = resources.enter_context(directory.open_file(path.name, write=True, lock=True))
    except BaseException:
        try:
            resources.close()
        except BaseException:
            pass
        raise
    acquired = False
    failure = None
    previous_database = getattr(_lock_context, "database", None)
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError):
            pass
        if acquired:
            directory.verify()
            _lock_context.database = database
        yield acquired
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_errors = []
        try:
            if acquired:
                # Nested failed acquisition never replaces the proof; a
                # successful other-profile lock restores its caller on exit.
                _lock_context.database = previous_database
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            resources.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            if failure is None:
                raise cleanup_errors[0]
            failure.add_note("WORKER_LOCK_CLEANUP_FAILED")


def holds_process_lock(*, profile=None):
    """Proof tied to this thread and database, not merely a lock file's existence."""
    database = (config.db_path().resolve() if profile is None
                else _writer_profile(profile).database.resolve())
    return getattr(_lock_context, "database", None) == database


def acquire_worker(owner_id, *, profile=None):
    # Recovery changes running→pending without adding/removing the video/kind/
    # payload selected by the unfinished-job root reader. Lease ownership and
    # display timestamps are not file roots, so no lifecycle lock is required.
    con = db.connect(None if profile is None else _writer_profile(profile).database)
    try:
        con.execute("BEGIN IMMEDIATE")
        now_ms = clock_ms()
        con.execute("INSERT OR IGNORE INTO worker_lease(scope,lease_until) VALUES (?,0)", (WORKER_SCOPE,))
        cur = con.execute("""UPDATE worker_lease SET owner_id=?,lease_until=?,updated_at=?
            WHERE scope=? AND (owner_id IS NULL OR owner_id=? OR lease_until<=?)""",
            (owner_id, now_ms + LEASE_MS, utc_now(), WORKER_SCOPE, owner_id, now_ms))
        if cur.rowcount:
            # A live lease never becomes pending merely because another server starts.
            con.execute("""UPDATE job SET status='pending',owner_id=NULL,lease_until=NULL,updated_at=?
                WHERE status='running' AND (lease_until IS NULL OR lease_until<=?)""", (utc_now(), now_ms))
        con.commit()
        return bool(cur.rowcount)
    finally:
        con.close()


def renew_worker(owner_id, *, profile=None):
    # Only worker/job lease/display fields change, never task root membership.
    con = db.connect(None if profile is None else _writer_profile(profile).database)
    try:
        expiry = clock_ms() + LEASE_MS
        cur = con.execute("UPDATE worker_lease SET lease_until=?,updated_at=? WHERE scope=? AND owner_id=?",
                          (expiry, utc_now(), WORKER_SCOPE, owner_id))
        if cur.rowcount:
            con.execute("UPDATE job SET lease_until=? WHERE owner_id=? AND status='running'", (expiry, owner_id))
        con.commit()
        return bool(cur.rowcount)
    finally:
        con.close()


def release_worker(owner_id, path=None):
    con = db.connect(path)
    try:
        con.execute("UPDATE worker_lease SET owner_id=NULL,lease_until=0,updated_at=? WHERE scope=? AND owner_id=?",
                    (utc_now(), WORKER_SCOPE, owner_id))
        con.commit()
    finally:
        con.close()


def claim(con, owner_id, *, profile=None):
    # pending→running preserves the unfinished task's exact selector. This
    # transition may operate under an existing SQL transaction without SH.
    if profile is not None:
        profile = _writer_profile(profile)
        name = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
        key = lambda path: os.path.normcase(os.path.abspath(os.fspath(path)))
        if not name or key(name) != key(profile.database):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    now_ms = clock_ms()
    lease = con.execute("SELECT * FROM worker_lease WHERE scope=? AND owner_id=? AND lease_until>?",
                        (WORKER_SCOPE, owner_id, now_ms)).fetchone()
    if not lease:
        con.rollback()
        return None
    row = con.execute("""SELECT job_id FROM job WHERE status='pending' AND manual_paused=0
        AND wait_mode!='blocked' AND (not_before IS NULL OR not_before<=?)
        ORDER BY job_id LIMIT 1""", (now_ms,)).fetchone()
    if row is None:
        con.commit()
        return None
    cur = con.execute("""UPDATE job SET status='running',attempts=attempts+1,owner_id=?,lease_until=?,
        started_at=?,completed_at=NULL,error=NULL,updated_at=?,not_before=NULL,blocked_reason=NULL,
        wait_mode='none' WHERE job_id=? AND status='pending' AND manual_paused=0
        AND wait_mode!='blocked' AND (not_before IS NULL OR not_before<=?)""",
        (owner_id, lease["lease_until"], utc_now(), utc_now(), row["job_id"], now_ms))
    job = con.execute("SELECT * FROM job WHERE job_id=?", (row["job_id"],)).fetchone() if cur.rowcount else None
    con.commit()
    return dict(job) if job else None


def _deadline_ms(instant):
    # Integer arithmetic avoids float rounding before the millisecond ceiling.
    delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    result = (microseconds + 999) // 1000
    if not 0 <= result <= 2**63 - 1:
        raise TaskError("任务等待时间无效", "INVALID_TASK", 400)
    return result


def defer(job_id, owner_id, deferred, *, profile=None):
    """Move this owner's original work to a durable wait without failing it."""
    if not isinstance(deferred, BudgetDeferred):
        raise TaskError("任务等待参数无效", "INVALID_TASK", 400)
    # Revalidate closed metadata even if an exception instance was modified.
    deferred = BudgetDeferred(deferred.reason, resume_at=deferred.resume_at,
                              operation_id=deferred.operation_id)
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM job WHERE job_id=? AND owner_id=? AND status='running'",
                              (job_id, owner_id)).fetchone()
            if row is None:
                con.commit()
                return False
            if deferred.operation_id is not None:
                operation = con.execute("""SELECT library_id,continuation_id,execution_generation
                    FROM budget_operation WHERE operation_id=?""", (deferred.operation_id,)).fetchone()
                library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
                if (operation is None or library is None or operation["library_id"] != library["value"]
                        or (operation["continuation_id"], operation["execution_generation"]) !=
                        (row["continuation_id"], row["execution_generation"])):
                    raise TaskError("任务预算操作不匹配", "TASK_BUDGET_OPERATION_MISMATCH", 409)
            deadline = None if deferred.resume_at is None else _deadline_ms(deferred.resume_at)
            reason = deferred.reason
            wait_mode = "blocked" if deadline is None else "until"
            if row["kind"] == "sync":
                wait_id = _sync_current_wait(profile, row)
                if wait_id is not None and _sync_resume_authorized(con, row, wait_id):
                    # A user may resume after the cloud acknowledgement but
                    # before this original owner persists its local wait.
                    deadline, reason, wait_mode = None, None, "none"
            if reason == "manual_pause" and not row["manual_paused"]:
                # A resume may have committed after the boundary observed a
                # pause. Service stop also uses this transient deferral. Keep
                # the original work immediately claimable for the next owner.
                deadline, reason, wait_mode = None, None, "none"
            cur = con.execute("""UPDATE job SET status='pending',owner_id=NULL,lease_until=NULL,
                error=NULL,completed_at=NULL,updated_at=?,not_before=?,blocked_reason=?,wait_mode=?,
                budget_operation_id=COALESCE(?,budget_operation_id)
                WHERE job_id=? AND owner_id=? AND status='running'""",
                (utc_now(), deadline, reason, wait_mode,
                 deferred.operation_id, job_id, owner_id))
            storage_lifecycle.require_guard(profile)
            con.commit()
            return bool(cur.rowcount)
        except BaseException:
            _rollback_after_error(con)
            raise


def _require_sync_job(row):
    if row is None or row["kind"] != "sync" or row["scope_key"] != "sync:cycle" or row["video_id"] is not None:
        raise TaskError("同步任务执行身份无效", "TASK_LINEAGE_UNAVAILABLE", 409)
    try:
        raw_payload = json.loads(row["payload_json"])
    except (ValueError, TypeError, UnicodeError):
        raise TaskError("同步任务执行身份无效", "TASK_LINEAGE_UNAVAILABLE", 409) from None
    if type(raw_payload) is not dict or raw_payload != {}:
        raise TaskError("同步任务执行身份无效", "TASK_LINEAGE_UNAVAILABLE", 409)


def _sync_current_wait(profile, row):
    """Observe an atomic state image without reversing SQL -> state RLock."""
    from .sync_state import StateStore
    from .sync_protocol import SyncError
    try:
        state = StateStore(profile.database.parent, profile=profile).read_observation()
    except SyncError as error:
        if error.code == "SYNC_STATE_MISSING":
            return None
        raise TaskError("同步等待状态不可用", "TASK_SYNC_WAIT_UNAVAILABLE", 409) from None
    pending = state.get("budget_pending")
    if pending is None:
        return None
    core = state.get("source_execution")
    if (core is None or (core["root_job_id"], core["continuation_id"], core["execution_generation"]) !=
            (row["job_id"], row["continuation_id"], row["execution_generation"])):
        raise TaskError("同步等待属于另一执行任务", "TASK_SYNC_WAIT_MISMATCH", 409)
    return pending["defer_request"]["request_id"]


def _sync_resume_identity(con, row, defer_request_id):
    _require_sync_job(row)
    _lineage(row["continuation_id"], row["execution_generation"])
    try:
        if type(defer_request_id) is not str or str(uuid.UUID(defer_request_id)) != defer_request_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise TaskError("同步延期编号无效", "INVALID_TASK", 400) from None
    library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
    if library is None:
        raise TaskError("同步任务执行身份无效", "TASK_LINEAGE_UNAVAILABLE", 409)
    body = {"format_version": 1, "action": "sync_budget_resume", "library_id": library[0],
        "job_id": row["job_id"], "continuation_id": row["continuation_id"],
        "execution_generation": row["execution_generation"], "defer_request_id": defer_request_id}
    request_id = str(uuid.uuid5(uuid.UUID(row["continuation_id"]), "sync-budget-resume:v1:" + defer_request_id))
    signature = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    return request_id, signature


def _sync_resume_authorized(con, row, defer_request_id):
    request_id, signature = _sync_resume_identity(con, row, defer_request_id)
    receipt = con.execute("SELECT * FROM task_request WHERE request_id=?", (request_id,)).fetchone()
    if receipt is None:
        return False
    if receipt["job_id"] != row["job_id"] or receipt["payload_signature"] != signature:
        raise TaskError("同步恢复回执不一致", "TASK_SYNC_RESUME_CONFLICT", 409)
    return True


def sync_resume_authorized(job_id, defer_request_id, *, profile=None):
    """Pure receipt lookup for one original wait; a claim is not consent."""
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
        return _sync_resume_authorized(con, row, defer_request_id)


def sync_waiting_job(owner_id, *, profile=None):
    """Observe one original source wait for reviews; never claim/reset it."""
    profile = _writer_profile(profile)
    if not holds_process_lock(profile=profile):
        raise TaskError("复核同步需要实际worker所有权", "TASK_OWNER_LOST", 409)
    with storage_admin.connection(profile) as con:
        lease = con.execute("SELECT 1 FROM worker_lease WHERE scope=? AND owner_id=? AND lease_until>?",
                            (WORKER_SCOPE, owner_id, clock_ms())).fetchone()
        if lease is None:
            return None
        row = con.execute("""SELECT * FROM job WHERE kind='sync' AND status='pending'
            AND (manual_paused=1 OR wait_mode='blocked' OR not_before>?) ORDER BY job_id LIMIT 1""",
                          (clock_ms(),)).fetchone()
        if row is None:
            return None
        _require_sync_job(row)
        from .sync_state import StateStore
        state = StateStore(profile.database.parent, profile=profile).read_observation()
        core = state.get("source_execution")
        if (core is None or (core["root_job_id"], core["continuation_id"], core["execution_generation"]) !=
                (row["job_id"], row["continuation_id"], row["execution_generation"])):
            raise TaskError("复核同步任务谱系不一致", "TASK_SYNC_WAIT_MISMATCH", 409)
        return dict(row)


def _set_paused(job_id, paused, *, profile=None, internal_sync=False, authorize_sync_resume=False, expected_wait_id=None,
                effect_guard=None):
    require_local()
    if effect_guard is not None and not callable(effect_guard):
        raise TaskError("任务控制条件无效", "INVALID_TASK", 400)
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
            allowed = row is not None and (row["kind"] == "sync" if internal_sync else row["kind"] in KINDS)
            if not allowed:
                raise TaskError("任务不存在", "NOT_FOUND", 404)
            if internal_sync:
                _require_sync_job(row)
            if row["status"] not in ("pending", "running"):
                raise TaskError("仅未完成任务可暂停或恢复", "TASK_NOT_ACTIVE", 409)
            if effect_guard is not None:
                # Recheck a UI selector under the actual SQL writer before
                # changing a job or persisting its original resume authority.
                effect_guard(con, row)
            if authorize_sync_resume:
                if _sync_current_wait(profile, row) != expected_wait_id:
                    raise TaskError("同步预算等待已变化，请重试", "TASK_SYNC_WAIT_MISMATCH", 409)
                if expected_wait_id is not None:
                    request_id, signature = _sync_resume_identity(con, row, expected_wait_id)
                    if not _sync_resume_authorized(con, row, expected_wait_id):
                        con.execute("INSERT INTO task_request(request_id,job_id,payload_signature,created_at) VALUES(?,?,?,?)",
                            (request_id, row["job_id"], signature, utc_now()))
            if paused:
                # In-flight work retains owner/lease and commits its paid result.
                con.execute("UPDATE job SET manual_paused=1,updated_at=? WHERE job_id=?", (utc_now(), job_id))
            else:
                # This is a request for fresh admission, never a ledger reset.
                con.execute("""UPDATE job SET manual_paused=0,not_before=NULL,blocked_reason=NULL,
                    wait_mode='none',updated_at=? WHERE job_id=?""", (utc_now(), job_id))
            result = serialize(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())
            storage_lifecycle.require_guard(profile)
            con.commit()
            return result
        except BaseException:
            _rollback_after_error(con)
            raise


def pause(job_id, *, profile=None):
    return _set_paused(job_id, True, profile=profile)


def resume(job_id, *, profile=None):
    return _set_paused(job_id, False, profile=profile)


def pause_sync(job_id, *, profile=None, effect_guard=None):
    """Dedicated internal sync control; the general task API stays closed."""
    return _set_paused(job_id, True, profile=profile, internal_sync=True, effect_guard=effect_guard)


def resume_sync(job_id, *, profile=None, effect_guard=None):
    """Re-admit the original sync continuation, without resetting its ledger."""
    require_local()
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        row = con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone()
        _require_sync_job(row)
        wait_id = _sync_current_wait(profile, row)
    return _set_paused(job_id, False, profile=profile, internal_sync=True,
                       authorize_sync_resume=True, expected_wait_id=wait_id, effect_guard=effect_guard)


def check_admission(job_id, owner_id, continuation_id, execution_generation, *, profile=None, connection=None):
    """Read-only step/HTTP boundary check; never use after a paid response."""
    profile = _writer_profile(profile)
    con = db.connect(profile.database) if connection is None else connection
    if connection is not None:
        storage_admin.from_connection(con, profile=profile)
    try:
        row = con.execute("""SELECT status,owner_id,continuation_id,execution_generation,
            manual_paused,budget_operation_id FROM job WHERE job_id=?""", (job_id,)).fetchone()
        if (row is None or row["status"] != "running" or row["owner_id"] != owner_id
                or (row["continuation_id"], row["execution_generation"]) !=
                (continuation_id, execution_generation)):
            raise TaskError("任务执行身份已失效", "TASK_OWNER_LOST", 409)
        if row["manual_paused"]:
            raise BudgetDeferred("manual_pause", operation_id=row["budget_operation_id"])
        if config.budget_settings()["budget_enabled"]:
            try:
                _lineage(row["continuation_id"], row["execution_generation"])
            except TaskError:
                raise BudgetDeferred("ledger_unavailable") from None
    finally:
        if connection is None:
            con.close()


def finish(job_id, owner_id, *, result=None, error=None, profile=None):
    # done/failed releases an unfinished process selector. A cancellation is
    # represented by the existing failed/error path and needs the same guard.
    profile = _writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            cur = con.execute("""UPDATE job SET status=?,result_json=?,error=?,completed_at=?,updated_at=?,
                owner_id=NULL,lease_until=NULL WHERE job_id=? AND owner_id=? AND status='running'""",
                ("failed" if error else "done", _canonical(result) if result is not None else None,
                 error, utc_now(), utc_now(), job_id, owner_id))
            storage_lifecycle.require_guard(profile)
            con.commit()
            return bool(cur.rowcount)
        except BaseException:
            _rollback_after_error(con)
            raise
