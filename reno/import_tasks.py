"""Durable imports use the existing job/receipt queue and worker identity."""
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

from . import db, storage_admin, tasks

MAX_BATCH_ITEMS = 10_000


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _local_path(value):
    # Normalizing a persisted task must stay lexical, including when a Linux
    # root reader inspects a backup made on Windows. Never follow symlinks here.
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        return ntpath.normpath(value)
    return os.path.abspath(value)


def _url(value):
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 8192 or "\0" in value:
        raise tasks.TaskError("需要有效的 http(s) 链接")
    value = value.strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError
        parts.port
    except ValueError:
        raise tasks.TaskError("需要有效的 http(s) 链接") from None
    return value


def normalize_ingest(video_id, payload, *, internal=False):
    if video_id is not None:
        if not internal:
            raise tasks.TaskError("导入任务不能指定 video_id")
        from .sync_protocol import validate_video_id
        validate_video_id(video_id)
    if payload.keys() - {"target", "source", "process"} or type(payload.get("process", True)) is not bool:
        raise tasks.TaskError("无效的导入任务参数")
    target = payload.get("target")
    if type(target) is not str or not target.strip() or len(target.encode("utf-8")) > 8192 or "\0" in target:
        raise tasks.TaskError("导入任务需要 target")
    target = target.strip()
    if target.lower().startswith(("http://", "https://")):
        target, source = _url(target), "url"
    else:
        if not internal or (urlsplit(target).scheme and not re.match(r"^[A-Za-z]:[\\/]", target)):
            raise tasks.TaskError("此入口只接受 http(s) 链接")
        target, source = _local_path(target), "file"
    if "source" in payload and payload["source"] != source:
        raise tasks.TaskError("导入来源与 target 不一致")
    normalized = {"target": target, "source": source, "process": payload.get("process", True)}
    return "ingest:" + _sha({"target": target, "source": source}), normalized


def normalize_favlist(video_id, payload, *, internal=False):
    if video_id is not None or payload.keys() - {"url", "limit", "cookies"}:
        raise tasks.TaskError("无效的收藏夹任务参数")
    url = _url(payload.get("url"))
    limit = payload.get("limit", 0)
    if type(limit) is not int or not 0 <= limit <= MAX_BATCH_ITEMS:
        raise tasks.TaskError("收藏夹 limit 必须为 0 至 10000 的整数")
    cookies = payload.get("cookies")
    if cookies is not None:
        if not internal or type(cookies) is not str or not cookies.strip() or "\0" in cookies:
            raise tasks.TaskError("私密收藏夹凭据只能在本地 CLI 指定")
        cookies = _local_path(cookies)
    return "favlist:" + _sha(url), {"url": url, "limit": limit, "cookies": cookies}


def _submit(kind, payload, request_id, *, allow_local, profile):
    profile = tasks._writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            job_id = tasks.enqueue(con, kind=kind, payload=payload, request_id=request_id,
                                   internal=allow_local, profile=profile)
            value = tasks.serialize(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())
            con.commit()
            return value
        except BaseException:
            tasks._rollback_after_error(con)
            raise


def submit_one(target, request_id=None, *, allow_local=False, process=True, profile=None):
    tasks.require_local()
    if type(allow_local) is not bool:
        raise tasks.TaskError("无效的导入入口")
    return _submit("ingest", {"target": target, "process": process}, request_id,
                   allow_local=allow_local, profile=profile)


def submit_favlist(url, request_id=None, *, cookies=None, limit=0, profile=None):
    tasks.require_local()
    return _submit("favlist", {"url": url, "cookies": cookies, "limit": limit}, request_id,
                   allow_local=True, profile=profile)


def _batch_receipt(con, request_id, value, *, signature=None):
    """Verify the receipt against each immutable child request and job input."""
    try:
        if (type(value) is not dict or set(value) != {"signature", "task_ids"}
                or type(value["signature"]) is not str or type(value["task_ids"]) is not list
                or len(value["task_ids"]) > MAX_BATCH_ITEMS
                or signature is not None and value["signature"] != signature):
            raise ValueError
        normalized = []
        namespace = uuid.UUID(request_id)
        for index, job_id in enumerate(value["task_ids"]):
            if type(job_id) is not int or job_id < 1:
                raise ValueError
            child_id = request_id if index == 0 else str(uuid.uuid5(namespace, "import-item:v1:" + str(index)))
            receipt = con.execute("SELECT job_id,payload_signature FROM task_request WHERE request_id=?", (child_id,)).fetchone()
            job = con.execute("SELECT kind,scope_key,payload_json FROM job WHERE job_id=?", (job_id,)).fetchone()
            if receipt is None or job is None or receipt["job_id"] != job_id or job["kind"] != "ingest":
                raise ValueError
            raw_payload = json.loads(job["payload_json"])
            if type(raw_payload) is not dict:
                raise ValueError
            scope, payload = normalize_ingest(None, raw_payload, internal=True)
            if scope != job["scope_key"] or receipt["payload_signature"] != _sha({"kind": "ingest", "video_id": None, "payload": payload}):
                raise ValueError
            normalized.append(payload)
        if _sha(normalized) != value["signature"]:
            raise ValueError
    except (ValueError, TypeError, KeyError, tasks.TaskError):
        raise tasks.TaskError("批量导入恢复回执不一致", "IMPORT_RECOVERY_INVALID", 409) from None
    return value["task_ids"]


def _enqueue_batch(con, targets, request_id, *, allow_local, process, profile, allow_empty=False):
    """Save one bounded receipt with child job receipts in the caller's commit."""
    if type(targets) not in (list, tuple) or not (0 if allow_empty else 1) <= len(targets) <= MAX_BATCH_ITEMS:
        raise tasks.TaskError("批量导入需要 1 至 10000 个来源")
    request_id = tasks._request_id(request_id)
    normalized = [normalize_ingest(None, {"target": item, "process": process}, internal=allow_local)[1]
                  for item in targets]
    signature = _sha(normalized)
    key = "import:batch-receipt:v1:" + request_id
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is not None:
        try:
            value = json.loads(row["value"])
            if (set(value) != {"signature", "task_ids"} or value["signature"] != signature
                    or type(value["task_ids"]) is not list or len(value["task_ids"]) != len(normalized)):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise tasks.TaskError("此请求编号已用于另一批导入", "IDEMPOTENCY_KEY_REUSED", 409) from None
        _batch_receipt(con, request_id, value, signature=signature)
        return {"status": "queued", "queued": len(value["task_ids"]), "request_id": request_id,
                "task_ids": value["task_ids"]}
    if con.execute("SELECT 1 FROM task_request WHERE request_id=?", (request_id,)).fetchone() is not None:
        raise tasks.TaskError("此请求编号已用于另一任务", "IDEMPOTENCY_KEY_REUSED", 409)
    namespace = uuid.UUID(request_id)
    job_ids = [tasks.enqueue(con, kind="ingest", payload=payload,
                            request_id=request_id if index == 0 else str(uuid.uuid5(namespace, "import-item:v1:" + str(index))),
                            internal=allow_local, profile=profile)
               for index, payload in enumerate(normalized)]
    con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, _canonical({"signature": signature, "task_ids": job_ids})))
    return {"status": "queued", "queued": len(job_ids), "request_id": request_id, "task_ids": job_ids}


def submit_batch(targets, request_id=None, *, allow_local=False, process=True, profile=None):
    tasks.require_local()
    if type(allow_local) is not bool:
        raise tasks.TaskError("无效的导入入口")
    profile = tasks._writer_profile(profile)
    with storage_admin.connection(profile) as con:
        try:
            value = _enqueue_batch(con, targets, request_id, allow_local=allow_local, process=process, profile=profile)
            con.commit()
            return value
        except BaseException:
            tasks._rollback_after_error(con)
            raise


def _owner(con, job):
    row = con.execute("SELECT status,owner_id,continuation_id,execution_generation FROM job WHERE job_id=?",
                      (job["job_id"],)).fetchone()
    if row is None or row["status"] != "running" or (row["owner_id"], row["continuation_id"], row["execution_generation"]) != (
            job["owner_id"], job["continuation_id"], job["execution_generation"]):
        raise tasks.TaskError("导入任务执行身份已失效", "TASK_OWNER_LOST", 409)


def run_ingest(job, *, profile):
    from . import ingest, pipeline, source_budget, worker
    payload = db.uj(job["payload_json"], {})
    normalize_ingest(job.get("video_id"), payload, internal=True)
    worker.check_admission()
    if payload["source"] == "file":
        result = ingest.ingest_file(payload["target"], enqueue_process=False, profile=profile)
    else:
        with source_budget.source_scope(profile):
            result = ingest.ingest_url(payload["target"], enqueue_process=False, source_proof=False, profile=profile)
    video_id = result["video_id"]
    from .sync_protocol import validate_video_id
    validate_video_id(video_id)
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN IMMEDIATE")
        _owner(con, job)
        con.execute("UPDATE job SET video_id=?,updated_at=? WHERE job_id=?", (video_id, tasks.utc_now(), job["job_id"]))
        asset = db.get_asset(con, video_id)
        if asset is None:
            raise tasks.TaskError("导入结果没有对应视频", "IMPORT_RECOVERY_INVALID", 409)
        already_processed = result["status"] == "duplicate" and asset["status"] == "processed"
        con.commit()
    if payload["process"] and not already_processed:
        steps = pipeline.run_video(video_id)
        if any(type(value) is str and value.startswith("ERROR") for value in steps.values()):
            raise tasks.TaskError("处理失败，可继续未完成步骤", "IMPORT_PROCESS_FAILED", 409)
        result = {**result, "steps": steps}
    return result


def run_favlist(job, *, profile):
    from . import favlist, source_budget, worker
    payload = db.uj(job["payload_json"], {})
    normalize_favlist(None, payload, internal=True)
    key = "import:favlist:v1:" + job["continuation_id"] + ":" + str(job["execution_generation"])
    con = db.connect(profile.database)
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        cached = None if row is None else db.uj(row["value"])
    finally:
        con.close()
    signature = _sha(payload)
    batch_request = str(uuid.uuid5(uuid.UUID(job["continuation_id"]),
                                  "favlist-children:v1:" + str(job["execution_generation"])))
    if row is not None and cached is None:
        raise tasks.TaskError("收藏夹恢复记录不一致", "IMPORT_RECOVERY_INVALID", 409)
    if cached is not None:
        if (type(cached) is not dict or set(cached) != {"signature", "result"}
                or cached["signature"] != signature or type(cached["result"]) is not dict
                or set(cached["result"]) != {"status", "queued", "request_id", "task_ids"}
                or cached["result"].get("status") != "queued"
                or cached["result"].get("request_id") != batch_request
                or type(cached["result"].get("task_ids")) is not list
                or any(type(value) is not int for value in cached["result"]["task_ids"])
                or type(cached["result"].get("queued")) is not int
                or cached["result"].get("queued") != len(cached["result"]["task_ids"])):
            raise tasks.TaskError("收藏夹恢复记录不一致", "IMPORT_RECOVERY_INVALID", 409)
        con = db.connect(profile.database)
        try:
            con.execute("BEGIN")
            receipt = con.execute("SELECT value FROM meta WHERE key=?", ("import:batch-receipt:v1:" + batch_request,)).fetchone()
            if receipt is None:
                raise tasks.TaskError("收藏夹恢复回执缺失", "IMPORT_RECOVERY_INVALID", 409)
            try:
                value = json.loads(receipt["value"])
            except (ValueError, TypeError):
                raise tasks.TaskError("收藏夹恢复回执不一致", "IMPORT_RECOVERY_INVALID", 409) from None
            ids = _batch_receipt(con, batch_request, value)
            if ids != cached["result"]["task_ids"]:
                raise tasks.TaskError("收藏夹恢复回执不一致", "IMPORT_RECOVERY_INVALID", 409)
        finally:
            con.close()
        return cached["result"]
    worker.check_admission()
    with source_budget.source_scope(profile):
        items = favlist.enumerate_favlist(payload["url"], payload["cookies"])
    if payload["limit"]:
        items = items[:payload["limit"]]
    if len(items) > MAX_BATCH_ITEMS:
        raise tasks.TaskError("收藏夹超过10000项，请设置 limit", "IMPORT_SELECTION_LIMIT", 400)
    from .sync_protocol import validate_video_id
    targets = ["https://www.bilibili.com/video/" + validate_video_id(item[0]) + "/" for item in items]
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN IMMEDIATE")
        _owner(con, job)
        result = _enqueue_batch(con, targets, batch_request, allow_local=False, process=True, profile=profile, allow_empty=True)
        con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, _canonical({"signature": signature, "result": result})))
        con.commit()
        return result
