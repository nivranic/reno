# -*- coding: utf-8 -*-
"""API endpoints: real-time frame extraction (the ±1s fix), video streaming
with Range support, import, decisions, search."""
import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from reno import ask as ask_module
from reno import config, db, frame_cache, ingest, review, storage_admin, sync_visibility
from reno.media import extract_frame

router = APIRouter(prefix="/api")

async def json_body(request: Request) -> dict:
    """Parse the body as a JSON object. Malformed JSON, bad encoding and
    non-object bodies are client errors (400), never a 500."""
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001 - json/Unicode decode errors
        raise HTTPException(400, f"invalid JSON body ({e.__class__.__name__})")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


@router.get("/frame/{video_id}/{ms}")
def frame_at(video_id: str, ms: int):
    """Real-time frame extraction at evidence time - frame-accurate jump,
    cached. This replaces nearest-kept-frame lookup (feasibility §3.6)."""
    from .storage_response import protected_media_path
    profile = storage_admin.profile()
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN")
        asset = db.get_asset(con, video_id, public=True)
        if asset is None:
            raise HTTPException(404, "unknown video")
        files = json.loads(asset["files_json"])
        v = config.resolve_media(files["video"])
        if v is None:
            raise HTTPException(404, "no media file")
        v = protected_media_path(v, profile=profile)
        if not v.is_file():
            raise HTTPException(404, "no media file")
        # clamp 200ms inside the file: seeking the very last instant of a
        # container yields no frame and must not fall out as a 500
        dur = asset["duration_ms"] or ms
        ms = max(0, min(ms, max(0, dur - 200)))
        try:
            budget = config.frame_cache_budget_bytes()
        except (OSError, ValueError):
            raise HTTPException(503, "帧缓存容量配置无效，请检查本机配置。") from None
        try:
            data = frame_cache.get_frame(video_id, asset["content_sha256"], ms, v,
                                         root=config.FRAME_CACHE, budget_bytes=budget,
                                         extractor=extract_frame)
        except frame_cache.FrameCacheError as exc:
            return JSONResponse({"code": exc.code, "detail": "帧暂时不可用，请稍后重试或检查本机缓存。"},
                                status_code=exc.status)
        # Capture bytes while the cross-process cache guard is held. Returning
        # FileResponse would reopen the path after an eviction could remove it.
        return Response(data, media_type="image/jpeg")


@router.get("/video/{video_id}/file")
def video_file(video_id: str, request: Request):
    from .storage_response import media_response
    profile = storage_admin.profile()
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN")
        asset = db.get_asset(con, video_id, public=True)
        if asset is None:
            raise HTTPException(404)
        files = json.loads(asset["files_json"])
        resolved = config.resolve_media(files["video"])
        if resolved is None or not resolved.is_file():
            raise HTTPException(404, "no media file")
        return media_response(resolved, request.headers.get("range"), profile=profile)


@router.get("/video/{video_id}/events")
def events(video_id: str, from_ms: int = None, to_ms: int = None):
    """Fused timeline events for the detail page panes. Optional from_ms/to_ms
    window filters events by timestamp (atoms stay complete)."""
    from_ms = max(0, from_ms) if from_ms is not None else None
    to_ms = max(0, to_ms) if to_ms is not None else None
    con = db.connect()
    try:
        con.execute("BEGIN")
        if config.is_cloud() and db.get_asset(con, video_id, public=True) is None:
            raise HTTPException(404, "unknown video")
        ev = []
        for r in con.execute("SELECT id, start_ms, end_ms, text FROM transcript_segment WHERE video_id=? ORDER BY start_ms", (video_id,)):
            ev.append({"id": r["id"], "ms": r["start_ms"], "end": r["end_ms"],
                       "mod": "ASR", "text": r["text"]})
        for r in con.execute("SELECT id, start_ms, end_ms, text FROM ocr_span WHERE video_id=? ORDER BY start_ms", (video_id,)):
            ev.append({"id": r["id"], "ms": r["start_ms"], "end": r["end_ms"],
                       "mod": "OCR", "text": r["text"]})
        for r in con.execute("SELECT id, pts_ms, scene_description, subtitle_text, measurement FROM visual_observation WHERE video_id=? ORDER BY pts_ms", (video_id,)):
            t = r["scene_description"] or ""
            if r["subtitle_text"]:
                t += f' | 字幕:"{r["subtitle_text"]}"'
            ev.append({"id": r["id"], "ms": r["pts_ms"], "end": r["pts_ms"] + 1200,
                       "mod": "VIS", "text": t})
        ev.sort(key=lambda x: x["ms"])
        if from_ms is not None:
            ev = [e for e in ev if e["ms"] >= from_ms]
        if to_ms is not None:
            ev = [e for e in ev if e["ms"] <= to_ms]
        atoms = []
        for a in review.annotate_atoms(con, db.all_atoms(con, video_id=video_id, public=True), public=True):
            if a["evidence_refs"]:
                atoms.append({"id": a["id"], "category": a["category"],
                              "space": a["space"], "claim": a["claim"],
                              "polarity": a["polarity"], "confidence": a["confidence"],
                              "status": a["status"],
                              "stage": a.get("stage"),
                              "dimension": a.get("dimension"),
                              "evidence_nature": a.get("evidence_nature"),
                              "exceptions": a.get("exceptions") or [],
                              "conditions": a.get("conditions") or {},
                              "prices": a.get("prices") or [],
                              "review_status": a.get("review_status"),
                              "review_stale": a.get("review_stale", False),
                              "has_conflict": a.get("has_conflict", False),
                              "has_outdated_conflict": a.get("has_outdated_conflict", False),
                              "target_fingerprint": a.get("target_fingerprint"),
                              "cluster_id": a["cluster_id"],
                              "parameters": a["parameters"],
                              "evidence": [{"id": e["source_item_id"],
                                            "ms": e["start_ms"], "end": e["end_ms"],
                                            "mod": e["modality"],
                                            "text": e["evidence_text"]}
                                           for e in a["evidence_refs"]]})
        return {"events": ev, "atoms": atoms}
    finally:
        con.close()


@router.post("/import")
async def import_url(request: Request):
    body = await json_body(request)
    raw_url = body.get("url")
    if not isinstance(raw_url, str):
        raise HTTPException(400, "url must be an http(s) link")
    target = raw_url.strip()
    if not target:
        raise HTTPException(400, "url required")
    from urllib.parse import urlparse
    try:
        scheme = urlparse(target).scheme.lower()
    except ValueError:
        raise HTTPException(400, "url must be a valid http(s) link") from None
    if scheme not in ("http", "https"):
        raise HTTPException(400, "url must be an http(s) link")
    if config.is_cloud():
        # cloud node never downloads/ingests (docs/sync-plan.md §1) - queue
        # the URL for the home sync agent instead
        from reno.sync_protocol import SyncError
        con = db.connect()
        try:
            db.enqueue_sync_urls(con, [target])
        except SyncError as exc:
            raise HTTPException(exc.status, "只支持有效的 B站或抖音视频链接") from None
        finally:
            con.close()
        return JSONResponse({"status": "queued", "queued": 1, "node_role": "cloud",
                             "note": "node_role=cloud: 已排队等待本地同步器处理"})
    try:
        from starlette.concurrency import run_in_threadpool
        from reno import import_tasks, tasks, worker
        task = await run_in_threadpool(import_tasks.submit_one, target, body.get("request_id"))
        worker.ensure_running()
        return JSONResponse({"status": "queued", "queued": 1, "node_role": config.get("node_role", "all"),
                             "task_id": task["task_id"], "request_id": task["request_id"]}, status_code=202)
    except tasks.TaskError as exc:
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(500, "导入任务提交失败，可用原 request_id 重试") from None


@router.post("/import-batch")
async def import_batch(request: Request):
    """Bulk ingest: {urls: [...]} - one link per line from the textarea, or
    submitted by the douyin/bilibili bookmarklet from the user's own browser."""
    body = await json_body(request)
    urls_raw = body.get("urls")
    if not isinstance(urls_raw, list) or not all(isinstance(u, str) for u in urls_raw):
        raise HTTPException(400, "urls must be a list of strings")
    urls = [u.strip() for u in urls_raw if u.strip()]
    if not urls:
        raise HTTPException(400, "urls required")
    if config.is_cloud():
        from reno.sync_protocol import SyncError, validate_ingest_url
        ok = []
        for url in urls[:200]:
            try:
                ok.append(validate_ingest_url(url))
            except SyncError:
                continue
        con = db.connect()
        try:
            n = db.enqueue_sync_urls(con, ok) if ok else 0
        finally:
            con.close()
        return JSONResponse({"queued": n, "skipped": len(urls) - len(ok), "node_role": "cloud",
                             "note": "node_role=cloud: 已排队等待本地同步器处理"})
    from reno import import_tasks, tasks, worker
    from starlette.concurrency import run_in_threadpool
    try:
        result = await run_in_threadpool(import_tasks.submit_batch, urls[:200], body.get("request_id"))
    except tasks.TaskError as exc:
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
    worker.ensure_running()
    return JSONResponse({**result, "node_role": config.get("node_role", "all"), "skipped": max(0, len(urls) - 200)},
                        status_code=202)


DECISION_ACTIONS = ("accept_a", "accept_b", "both", "reject")


@router.post("/decision")
async def decision(request: Request):
    body = await json_body(request)
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        for kind, target_id in (("atom", body.get("atom_id")), ("conflict", body.get("conflict_id"))):
            if target_id and not sync_visibility.target_visible(con, kind, target_id):
                raise review.ReviewError("unknown target", "NOT_FOUND", 404)
        result = review.record_decision(
            con, atom_id=body.get("atom_id"), conflict_id=body.get("conflict_id"),
            action=body.get("action"), note=body.get("note"),
            revised_claim=body.get("revised_claim"),
            event_id=body.get("event_id", body.get("operation_id")),
            client_id=body.get("client_id"), target_fingerprint=body.get("target_fingerprint"))
        con.commit()
        return result
    except review.ReviewError as exc:
        con.rollback()
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
    finally:
        con.close()


@router.get("/reviews")
def reviews(kind: str = "", target_id: str = ""):
    con = db.connect()
    try:
        con.execute("BEGIN")
        return {"reviews": review.review_history(con, kind or None, target_id or None, public=True)}
    except review.ReviewError as exc:
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
    finally:
        con.close()


@router.get("/capabilities")
def capabilities(request: Request):
    from reno import personal
    from .personal_read_api import read, error_response
    try:
        return read(lambda con: {"node_role": config.get("node_role", "all"),
                "processing": not config.is_cloud(), "model_write": not config.is_cloud(),
                "tasks": not config.is_cloud(),
                "task_reason": "云端仅接收采集请求，处理与报告任务由家庭节点执行" if config.is_cloud() else "",
                **personal.capabilities(con), **db.knowledge_version(con)}, request=request, gate=False, allow_disabled=True)
    except personal.PersonalError as error:
        return error_response(error)


# per-token sliding window for /ask (public billing surface on a cloud node;
# limit is ask_rate_per_min per minute, 0 disables - sync-plan §6)
_ASK_BUCKETS: dict = {}
_ASK_LOCK = threading.Lock()


def _ask_allowed(key: str) -> bool:
    from collections import deque
    import time as _time
    try:
        limit = int(config.get("ask_rate_per_min", 10) or 0)
    except (TypeError, ValueError):
        limit = 10
    if limit <= 0:
        return True
    now = _time.monotonic()
    with _ASK_LOCK:
        dq = _ASK_BUCKETS.setdefault(key, deque())
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


@router.post("/ask")
async def ask_question(request: Request):
    """Grounded Q&A: {question, history?: [{role, content}], k?: int}.

    Stateless multi-turn: retrieval is per-turn; history only provides
    follow-up context. Returns {answer, refs, conflicts} with refs carrying
    video_id + start_ms for evidence jump links."""
    body = await json_body(request)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(400, "question required")
    history = body.get("history")
    if history is not None and not isinstance(history, list):
        raise HTTPException(400, "history must be a list of turns")
    k = body.get("k", 24)
    if not isinstance(k, int) or isinstance(k, bool) or not (1 <= k <= 100):
        raise HTTPException(400, "k must be an integer between 1 and 100")
    if not _ask_allowed(request.headers.get("x-reno-token")
                        or request.query_params.get("token") or "anon"):
        raise HTTPException(429, "rate limited, retry in a minute")
    options = {}
    if "context_snapshot" in body:
        try:
            ask_module.normalize_context(body["context_snapshot"])
        except (TypeError, ValueError):
            raise HTTPException(400, "context_snapshot must contain bounded, valid condition values")
        options["context_snapshot"] = body["context_snapshot"]
    if "project_id" in body:
        if not isinstance(body["project_id"], str) or len(body["project_id"]) > 100:
            raise HTTPException(400, "invalid project_id")
        options["project_id"] = body["project_id"]
    if "context_revision" in body:
        if type(body["context_revision"]) is not int or body["context_revision"] < 1:
            raise HTTPException(400, "invalid context_revision")
        options["context_revision"] = body["context_revision"]
    try:
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(ask_module.ask, question.strip(), history=history or [], k=k, **options)
        return JSONResponse(res)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        # provider detail must not reach visitors (key validity probing);
        # ask() itself already degrades LLM failures to retrieval-only
        print(f"[ask] error: {e!r}", flush=True)
        raise HTTPException(500, "问答服务暂不可用，请稍后重试")


# ---------- read APIs for the React client (previously Jinja2-injected) ----------

@router.get("/videos")
def videos():
    """Inbox listing + global stats (was index.html template context)."""
    con = db.connect()
    try:
        con.execute("BEGIN")
        rows = [dict(r) for r in con.execute(
            f"""SELECT va.video_id, va.title, va.author, va.status, va.duration_ms,
                      va.imported_at, va.source_platform,
                      (SELECT COUNT(*) FROM knowledge_atom ka WHERE ka.video_id=va.video_id AND {sync_visibility.atom_sql('ka')}) atoms,
                      (SELECT pr.detail FROM processing_run pr WHERE pr.video_id=va.video_id AND pr.ok=0
                       ORDER BY pr.run_id DESC LIMIT 1) error_detail
               FROM video_asset va WHERE {sync_visibility.video_sql('va')} ORDER BY va.imported_at DESC LIMIT 500""")]
        job_where = (" WHERE video_id IS NULL OR EXISTS (SELECT 1 FROM video_asset va "
                     "WHERE va.video_id=job.video_id AND va.sync_state='ready')") if config.is_cloud() else ""
        jobs = {r["status"]: r["c"] for r in con.execute(
            "SELECT status, COUNT(*) c FROM job" + job_where + " GROUP BY status")}
        stats = {
            "videos": (con.execute("SELECT COUNT(*) FROM video_asset WHERE " + sync_visibility.video_sql()).fetchone()[0]
                       if config.is_cloud() else len(rows)),
            "atoms": con.execute("SELECT COUNT(*) c FROM knowledge_atom WHERE " + sync_visibility.atom_sql()).fetchone()["c"],
            "clusters": con.execute("SELECT COUNT(*) c FROM knowledge_cluster WHERE " + sync_visibility.cluster_sql()).fetchone()["c"],
            "conflicts": con.execute("SELECT COUNT(*) c FROM conflict_case WHERE " + sync_visibility.conflict_sql()).fetchone()["c"],
            "conflicts_open": con.execute(
                "SELECT COUNT(*) c FROM conflict_case WHERE status NOT LIKE 'decided%' AND " + sync_visibility.conflict_sql()).fetchone()["c"],
            "jobs": jobs,
        }
        return {"videos": rows, "stats": stats}
    finally:
        con.close()


@router.get("/videos/{video_id}/meta")
def video_meta(video_id: str):
    """Detail-page header data (was detail.html template context)."""
    con = db.connect()
    try:
        con.execute("BEGIN")
        asset = db.get_asset(con, video_id, public=True)
        if asset is None:
            raise HTTPException(404, "unknown video")
        files = json.loads(asset["files_json"] or "{}")
        has_video = "video" in files and (config.resolve_media(files["video"]) or Path()).exists()
        return {
            "video_id": asset["video_id"], "title": asset["title"],
            "author": asset["author"], "status": asset["status"],
            "duration_ms": asset["duration_ms"], "imported_at": asset["imported_at"],
            "source_platform": asset["source_platform"], "source_url": asset["source_url"],
            "has_media": has_video,
            "atoms": con.execute("SELECT COUNT(*) c FROM knowledge_atom WHERE video_id=? AND " + sync_visibility.atom_sql(),
                                 (video_id,)).fetchone()["c"],
        }
    finally:
        con.close()


@router.get("/conflicts")
def conflicts():
    """Parsed conflict cases (was conflicts.html template context)."""
    con = db.connect()
    try:
        con.execute("BEGIN")
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case WHERE " + sync_visibility.conflict_sql() + " ORDER BY conflict_id")]
        from reno import derivation, evidence_identity
        projected = review.projection(con, public=True)
        relation_states = derivation.evaluate_relations(con, public=True)["conflicts"]
        for r in rows:
            r["derivation"] = relation_states[r["conflict_id"]]
            r.pop("derivation_json", None)
            target = evidence_identity.load_target(con, "conflict", r["conflict_id"])
            r["target_fingerprint"] = target[1] if target else None
            r["status"] = projected["conflicts"].get(r["conflict_id"], r["status"])
            r["review_stale"] = any(e.get("conflict_id") == r["conflict_id"] for e in projected["attention"])
            r["side_a"] = json.loads(r.pop("side_a_json") or "{}")
            r["side_b"] = json.loads(r.pop("side_b_json") or "{}")
            r["analysis"] = json.loads(r.pop("analysis_json") or "{}")
        return {"conflicts": rows}
    finally:
        con.close()


@router.get("/facets")
def facets():
    """Distinct controlled-vocabulary values for search filters."""
    con = db.connect()
    try:
        con.execute("BEGIN")
        return {name: [row[0] for row in con.execute(
            f"SELECT DISTINCT {column} FROM knowledge_atom WHERE {column} IS NOT NULL AND "
            + sync_visibility.atom_sql() + f" ORDER BY {column}")]
            for name, column in (("categories", "category"), ("spaces", "space"),
                                 ("stages", "stage"), ("dimensions", "dimension"))}
    finally:
        con.close()


@router.get("/reports")
def reports():
    """Generated markdown reports (was reports.html template context).
    Reads from the data dir (RENO_DATA_DIR aware), never from the git-tracked
    repo checkout - a cloud deploy's git reset would wipe those (sync-plan §3)."""
    from reno import report
    out = {}
    with storage_admin.connection() as con:
        con.execute("BEGIN")
        for name in ("summary.md", "incremental_diff.md", "checklist.md", "conflicts.md", "prices.md"):
            out[name[:-3]] = report.present_report(name[:-3], con=con) or ""
        return {"reports": out}


@router.get("/health")
def health():
    """Connectivity + non-sensitive config status for the client & collect page."""
    cookies = config.get("ytdlp_cookies", "")
    con = db.connect()
    try:
        version = db.knowledge_version(con)
    finally:
        con.close()
    return {
        **version,
        "node_role": config.get("node_role", "all"),
        "ok": True,
        "cookies_configured": bool(cookies),
        "vlm_enabled": bool(config.get("vlm_enabled", True)),
        "atomize_model": config.get("atomize_model", "glm-4.6"),
        "model_presets": MODEL_PRESETS,
    }


# switchable processing models (validated; all verified on /api/anthropic)
MODEL_PRESETS = ["GLM-5.3", "GLM-5.3-Flash", "GLM-5.3-FlashX"]


@router.post("/config/model")
async def set_model(request: Request):
    """Switch the processing model (atomize + judge) at runtime. Writes to
    config.local.json (preserving all other keys incl. secrets) and takes
    effect on the next pipeline run without a server restart."""
    if config.is_cloud():
        # every preset is glm-5.3* (coding plan) - a cloud node must not run
        # the pipeline anyway, and switching would arm the llm guard
        raise HTTPException(403, "node_role=cloud: processing model is managed by the home node")
    body = await json_body(request)
    model = body.get("model")
    if model not in MODEL_PRESETS:
        raise HTTPException(400, f"model must be one of {MODEL_PRESETS}")
    config.set_local({"atomize_model": model, "judge_models": [model]})
    return {"ok": True, "atomize_model": model}


SEARCH_FILTERS = ("category", "space", "stage", "dimension", "polarity", "status")


@router.get("/search")
def search(q: str = "", category: str = "", space: str = "", stage: str = "",
           dimension: str = "", polarity: str = "", status: str = "",
           page: int = 1, page_size: int = 30):
    """Atom search: phrase match first, then CJK-bigram OR fallback (the same
    retrieval as /ask) so multi-term queries like 卫生间防水高度 still hit.
    Filters are exact-match on controlled-vocabulary columns."""
    if page < 1 or not 1 <= page_size <= 100 or len(q) > 2000:
        raise HTTPException(400, "invalid search pagination or query")
    con = db.connect()
    try:
        con.execute("BEGIN")
        from reno.ask import retrieve
        atoms_by_id = {a["id"]: a for a in review.annotate_atoms(con, db.all_atoms(con, public=True), public=True)}
        filters = {"category": category, "space": space, "stage": stage,
                   "dimension": dimension, "polarity": polarity, "status": status}
        rows = retrieve(con, q.strip(), k=max(1, len(atoms_by_id)), atoms_by_id=atoms_by_id,
                        filters=filters, include_rejected=(status == "rejected"))
        titles = {r["video_id"]: dict(r) for r in con.execute(
            "SELECT video_id, title, source_published_at FROM video_asset WHERE " + sync_visibility.video_sql())}
        start = (page - 1) * page_size
        return {"results": [_search_row(a, titles) for a in rows[start:start + page_size]],
                "page": page, "page_size": page_size, "total": len(rows),
                "has_more": start + page_size < len(rows), **db.knowledge_version(con)}
    finally:
        con.close()


def _search_row(a: dict, titles: dict) -> dict:
    ev = a["evidence_refs"][0] if a["evidence_refs"] else None
    vid = ev["video_id"] if ev else None
    info = titles.get(vid, {})
    if isinstance(info, str) or info is None:
        info = {"title": info or ""}
    return {"id": a["id"], "claim": a["claim"], "subject": a.get("subject"),
            "category": a["category"], "space": a["space"], "stage": a.get("stage"),
            "dimension": a.get("dimension"), "evidence_nature": a.get("evidence_nature"),
            "polarity": a["polarity"], "status": a["status"],
            "conditions": a.get("conditions") or {},
            "parameters": a.get("parameters") or [],
            "prices": a.get("prices") or [],
            "exceptions": a.get("exceptions") or [],
            "review_status": a.get("review_status"),
            "review_stale": a.get("review_stale", False),
            "has_conflict": a.get("has_conflict", False),
            "has_outdated_conflict": a.get("has_outdated_conflict", False),
            "target_fingerprint": a.get("target_fingerprint"),
            "source_published_at": info.get("source_published_at"),
            "video": vid,
            "video_title": info.get("title") or "",
            "ms": ev["start_ms"] if ev else 0,
            "mod": ev["modality"] if ev else None,
            "evidence_text": (ev["evidence_text"] or "")[:120] if ev else ""}


# ---------- compare: dimension-grouped side-by-side evidence ----------

def _lexical_gate(a: dict, query: str) -> bool:
    """Require the named object (or a declared synonym), not a shared bigram.

    A shared fragment such as 方案 must not put 方案乙 under 方案甲.
    Whitespace-separated object qualifiers all have to be present.
    """
    from reno.dict import expand_synonyms
    hay = " ".join(filter(None, [
        a.get("claim"), a.get("subject"), a.get("category"), a.get("space"),
        json.dumps(a.get("conditions") or {}, ensure_ascii=False),
        " ".join(p.get("name", "") for p in a.get("parameters") or []),
        " ".join(e.get("evidence_text") or "" for e in a.get("evidence_refs") or []),
        a.get("evidence_context", "")]))
    compact_hay = re.sub(r"\s+", "", hay).casefold()
    def covered(term: str) -> bool:
        if term in compact_hay:
            return True
        if not re.fullmatch(r"[\u4e00-\u9fff]+", term):
            return False
        # Cover the whole compound name with non-overlapping literal pieces:
        # 瓷砖 + 美缝 may be separated in a sentence; sharing only 瓷砖 is not enough.
        reachable = {0}
        for start in range(len(term)):
            if start not in reachable:
                continue
            for end in range(start + 2, len(term) + 1):
                if term[start:end] in compact_hay:
                    reachable.add(end)
            if start and len(term) - start == 1 and term[start:] in compact_hay:
                reachable.add(len(term))
        return len(term) in reachable
    for variant in [query, *expand_synonyms(query)[:4]]:
        parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", variant.casefold())
        if parts and all(covered(part) for part in parts):
            return True
    return False


@router.post("/compare")
async def compare_with_context(request: Request):
    """Conditions stay in the request body, outside navigation and access URLs."""
    body = await json_body(request)
    items, limit = body.get("items", ""), body.get("per_dimension_limit", 5)
    if not isinstance(items, str) or type(limit) is not int:
        raise HTTPException(400, "invalid comparison request")
    snapshot = body.get("context_snapshot", {})
    try:
        ask_module.normalize_context(snapshot)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid context") from None
    if "project_id" in body and (not isinstance(body["project_id"], str) or len(body["project_id"]) > 100):
        raise HTTPException(400, "invalid project_id")
    if "context_revision" in body and (type(body["context_revision"]) is not int or body["context_revision"] < 1):
        raise HTTPException(400, "invalid context_revision")
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(compare, items, limit, json.dumps(snapshot, ensure_ascii=False))


@router.get("/compare")
def compare(items: str = "", per_dimension_limit: int = 5, context: str = ""):
    """Side-by-side evidence for 2-3 objects/schemes: each item gets its own
    retrieval (gated), atoms grouped by knowledge dimension, plus conflict
    hints from judge clusters spanning both sides. No LLM synthesis — the
    grouping is deterministic over real evidence."""
    names = list(dict.fromkeys(s.strip() for s in (items or "").split(",") if s.strip()))
    if not 2 <= len(names) <= 3 or any(len(n) > 100 for n in names):
        raise HTTPException(400, "items must contain 2-3 comma-separated objects")
    if not 1 <= per_dimension_limit <= 100:
        raise HTTPException(400, "per_dimension_limit must be between 1 and 100")
    try:
        context_used, context_unknown = ask_module.normalize_context(json.loads(context) if context else None)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid context")
    con = db.connect()
    try:
        con.execute("BEGIN")
        from reno.ask import retrieve
        from reno.dict import dimensions
        atoms_by_id = {a["id"]: a for a in review.annotate_atoms(con, db.all_atoms(con, public=True), public=True)}
        # A short quote can omit the object's qualifier (e.g. 两米 while its
        # referenced transcript says 防水高度). Use only the actually linked
        # source segment, never unrelated text elsewhere in the video.
        for table, modality in (("transcript_segment", "asr"), ("ocr_span", "ocr")):
            for evidence in con.execute(
                    f"SELECT er.atom_id,s.text FROM evidence_ref er JOIN {table} s "
                    "ON s.video_id=er.video_id AND s.id=er.source_item_id "
                    "JOIN knowledge_atom ka ON ka.id=er.atom_id "
                    "WHERE lower(er.modality)=? AND " + sync_visibility.atom_sql("ka"), (modality,)):
                atom = atoms_by_id.get(evidence["atom_id"])
                if atom is not None:
                    atom["evidence_context"] = atom.get("evidence_context", "") + " " + (evidence["text"] or "")
        titles = {r["video_id"]: dict(r) for r in con.execute(
            "SELECT video_id, title, source_published_at FROM video_asset WHERE " + sync_visibility.video_sql())}
        all_dims = dimensions()
        sides, hit_ids = [], {}
        for name in names:
            hits = [a for a in retrieve(con, name, k=max(1, len(atoms_by_id)), atoms_by_id=atoms_by_id)
                    if _lexical_gate(a, name)]
            grouped = {d: [] for d in all_dims}
            for a in hits:
                from reno.condition_match import assess
                grouped.setdefault(a.get("dimension") or "其他", []).append(
                    {**_search_row(a, titles), "condition_match": assess(a, context_used)})
                hit_ids.setdefault(a["id"], set()).add(name)
            sides.append({"item": name,
                          "groups": [{"dimension": d, "atoms": g[:per_dimension_limit],
                                      "total": len(g), "truncated": len(g) > per_dimension_limit}
                                     for d, g in grouped.items() if g],
                          "missing_dimensions": [d for d in all_dims if not grouped[d]],
                          "total": len(hits), "coverage_scope": "full_filtered_set",
                          "truncated": any(len(g) > per_dimension_limit for g in grouped.values())})
        # conflicts: clusters marked conflicting that contain gated atoms from >=2 items
        from reno import derivation
        relation_states = derivation.evaluate_relations(con, public=True)["clusters"]
        conflict_hints = []
        for c in con.execute("SELECT * FROM knowledge_cluster WHERE relation='conflicting' AND " + sync_visibility.cluster_sql()):
            members = json.loads(c["members_json"] or "[]")
            involved = {nm for m in members for nm in hit_ids.get(m, set())}
            if len(involved) >= 2:
                conflict_hints.append({"cluster_id": c["cluster_id"],
                                       "topic": c["canonical_topic"],
                                       "linked_conflict": c["linked_conflict"],
                                       "derivation": relation_states[c["cluster_id"]],
                                       "items": sorted(involved)})
        return {"sides": sides, "conflicts": conflict_hints,
                "dimensions": all_dims, **db.knowledge_version(con),
                "context_used": context_used, "context_unknown": context_unknown,
                "context_signature": ask_module.hashlib.sha256(
                    ask_module.canonical_json(context_used).encode()).hexdigest(),
                "per_dimension_limit": per_dimension_limit}
    finally:
        con.close()
