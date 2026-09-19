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
            from reno import pipeline
            con = db.connect()
            vid = res["video_id"]
            con.close()
            import threading
            threading.Thread(target=pipeline.run_video, args=(vid,),
                             daemon=True).start()
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)[:300])


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
        return {"results": [{"id": a["id"], "claim": a["claim"], "category": a["category"],
                             "space": a["space"], "video": a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else None,
                             "ms": a["evidence_refs"][0]["start_ms"] if a["evidence_refs"] else 0}
                            for a in rows[:80]]}
    finally:
        con.close()
