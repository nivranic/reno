# -*- coding: utf-8 -*-
"""API endpoints: real-time frame extraction (the ±1s fix), video streaming
with Range support, import, decisions, search."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from reno import config, db, ingest
from reno.media import extract_frame

router = APIRouter(prefix="/api")


@router.get("/frame/{video_id}/{ms}")
def frame_at(video_id: str, ms: int):
    """Real-time frame extraction at evidence time - frame-accurate jump,
    cached. This replaces nearest-kept-frame lookup (feasibility §3.6)."""
    con = db.connect()
    try:
        asset = db.get_asset(con, video_id)
        if asset is None:
            raise HTTPException(404, "unknown video")
        files = json.loads(asset["files_json"])
        v = config.ROOT / files["video"]
        ms = max(0, min(ms, asset["duration_ms"] or ms))
        out = config.FRAME_CACHE / video_id / f"f{ms:07d}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            if not extract_frame(v, ms, out):
                raise HTTPException(500, "extract failed")
        return FileResponse(out, media_type="image/jpeg")
    finally:
        con.close()


@router.get("/video/{video_id}/file")
def video_file(video_id: str, request: Request):
    con = db.connect()
    try:
        asset = db.get_asset(con, video_id)
        if asset is None:
            raise HTTPException(404)
        files = json.loads(asset["files_json"])
        v = Path(config.ROOT / files["video"]).resolve()
        size = v.stat().st_size
        range_h = request.headers.get("range")
        if not range_h:
            return FileResponse(v, media_type="video/mp4",
                                headers={"Accept-Ranges": "bytes"})
        start = int(range_h.replace("bytes=", "").split("-")[0] or 0)
        end = min(start + 4 * 1024 * 1024, size - 1)
        with open(v, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        return Response(data, status_code=206, media_type="video/mp4",
                        headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                 "Accept-Ranges": "bytes",
                                 "Content-Length": str(len(data))})
    finally:
        con.close()


@router.get("/video/{video_id}/events")
def events(video_id: str):
    """Fused timeline events for the detail page panes."""
    con = db.connect()
    try:
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
        atoms = []
        for a in db.all_atoms(con):
            if a["evidence_refs"] and a["evidence_refs"][0]["video_id"] == video_id:
                atoms.append({"id": a["id"], "category": a["category"],
                              "space": a["space"], "claim": a["claim"],
                              "polarity": a["polarity"], "confidence": a["confidence"],
                              "status": a["status"],
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
    body = await request.json()
    target = (body or {}).get("url", "").strip()
    if not target:
        raise HTTPException(400, "url required")
    try:
        res = ingest.ingest_url(target)
        if res["status"] != "duplicate":
            from ..reno import pipeline
            con = db.connect()
            vid = res["video_id"]
            con.close()
            import threading
            threading.Thread(target=pipeline.run_video, args=(vid,),
                             daemon=True).start()
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)[:300])


@router.post("/import-batch")
async def import_batch(request: Request):
    """Bulk ingest: {urls: [...]} - one link per line from the textarea, or
    submitted by the douyin/bilibili bookmarklet from the user's own browser."""
    body = await request.json()
    urls = [u.strip() for u in (body or {}).get("urls", []) if u.strip()]
    if not urls:
        raise HTTPException(400, "urls required")
    from reno.favlist import import_urls
    stats = import_urls(urls[:200])
    return JSONResponse({k: len(v) for k, v in stats.items()} |
                        {"failed_sample": stats["failed"][:3]})


@router.post("/decision")
async def decision(request: Request):
    body = await request.json()
    con = db.connect()
    try:
        db.add_decision(con, atom_id=body.get("atom_id"),
                        conflict_id=body.get("conflict_id"),
                        action=body.get("action"),
                        revised_claim=body.get("revised_claim"),
                        note=body.get("note"))
        if body.get("conflict_id"):
            con.execute("UPDATE conflict_case SET status=? WHERE conflict_id=?",
                        (f"decided:{body.get('action')}", body["conflict_id"]))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# ---------- read APIs for the React client (previously Jinja2-injected) ----------

@router.get("/videos")
def videos():
    """Inbox listing + global stats (was index.html template context)."""
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute(
            """SELECT va.video_id, va.title, va.author, va.status, va.duration_ms,
                      va.imported_at, va.source_platform,
                      (SELECT COUNT(*) FROM knowledge_atom ka WHERE ka.video_id=va.video_id) atoms,
                      (SELECT pr.detail FROM processing_run pr WHERE pr.video_id=va.video_id AND pr.ok=0
                       ORDER BY pr.run_id DESC LIMIT 1) error_detail
               FROM video_asset va ORDER BY va.imported_at DESC LIMIT 500""")]
        jobs = {r["status"]: r["c"] for r in con.execute(
            "SELECT status, COUNT(*) c FROM job GROUP BY status")}
        stats = {
            "videos": len(rows),
            "atoms": con.execute("SELECT COUNT(*) c FROM knowledge_atom").fetchone()["c"],
            "clusters": con.execute("SELECT COUNT(*) c FROM knowledge_cluster").fetchone()["c"],
            "conflicts": con.execute("SELECT COUNT(*) c FROM conflict_case").fetchone()["c"],
            "conflicts_open": con.execute(
                "SELECT COUNT(*) c FROM conflict_case WHERE status NOT LIKE 'decided%'").fetchone()["c"],
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
        asset = db.get_asset(con, video_id)
        if asset is None:
            raise HTTPException(404, "unknown video")
        files = json.loads(asset["files_json"] or "{}")
        has_video = "video" in files and (config.ROOT / files["video"]).exists()
        return {
            "video_id": asset["video_id"], "title": asset["title"],
            "author": asset["author"], "status": asset["status"],
            "duration_ms": asset["duration_ms"], "imported_at": asset["imported_at"],
            "source_platform": asset["source_platform"], "source_url": asset["source_url"],
            "has_media": has_video,
            "atoms": con.execute("SELECT COUNT(*) c FROM knowledge_atom WHERE video_id=?",
                                 (video_id,)).fetchone()["c"],
        }
    finally:
        con.close()


@router.get("/conflicts")
def conflicts():
    """Parsed conflict cases (was conflicts.html template context)."""
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case ORDER BY conflict_id")]
        for r in rows:
            r["side_a"] = json.loads(r.pop("side_a_json") or "{}")
            r["side_b"] = json.loads(r.pop("side_b_json") or "{}")
            r["analysis"] = json.loads(r.pop("analysis_json") or "{}")
        return {"conflicts": rows}
    finally:
        con.close()


@router.get("/facets")
def facets():
    """Distinct atom categories / spaces for search filters."""
    con = db.connect()
    try:
        return {
            "categories": [r["category"] for r in con.execute(
                "SELECT DISTINCT category FROM knowledge_atom WHERE category IS NOT NULL ORDER BY category")],
            "spaces": [r["space"] for r in con.execute(
                "SELECT DISTINCT space FROM knowledge_atom WHERE space IS NOT NULL ORDER BY space")],
        }
    finally:
        con.close()


@router.get("/reports")
def reports():
    """Generated markdown reports (was reports.html template context)."""
    gen = Path(__file__).parent.parent / "docs" / "generated"
    out = {}
    for name in ("incremental_diff.md", "checklist.md", "conflicts.md"):
        p = gen / name
        out[name[:-3]] = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"reports": out}


@router.get("/health")
def health():
    """Connectivity + non-sensitive config status for the client & collect page."""
    cookies = config.get("ytdlp_cookies", "")
    return {
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
    body = await request.json()
    model = (body or {}).get("model")
    if model not in MODEL_PRESETS:
        raise HTTPException(400, f"model must be one of {MODEL_PRESETS}")
    config.set_local({"atomize_model": model, "judge_models": [model]})
    return {"ok": True, "atomize_model": model}


@router.get("/search")
def search(q: str = "", category: str = "", space: str = ""):
    con = db.connect()
    try:
        rows = []
        if q:
            from reno.db import fts_prep
            try:
                cur = con.execute(
                    """SELECT atom_id FROM atom_fts WHERE atom_fts MATCH ? LIMIT 100""",
                    ('"' + fts_prep(q).strip().replace('"', '""') + '"',))
                ids = [r["atom_id"] for r in cur]
            except Exception:
                ids = []
            for a in db.all_atoms(con):
                if a["id"] in ids and (not category or a["category"] == category) \
                   and (not space or a["space"] == space):
                    rows.append(a)
        titles = {r["video_id"]: r["title"] for r in con.execute(
            "SELECT video_id, title FROM video_asset")}
        return {"results": [
            {"id": a["id"], "claim": a["claim"], "category": a["category"],
             "space": a["space"], "polarity": a["polarity"], "status": a["status"],
             "video": a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else None,
             "video_title": titles.get(a["evidence_refs"][0]["video_id"], "") if a["evidence_refs"] else "",
             "ms": a["evidence_refs"][0]["start_ms"] if a["evidence_refs"] else 0,
             "mod": a["evidence_refs"][0]["modality"] if a["evidence_refs"] else None,
             "evidence_text": (a["evidence_refs"][0]["evidence_text"] or "")[:120] if a["evidence_refs"] else ""}
            for a in rows[:80]]}
    finally:
        con.close()
