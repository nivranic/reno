# -*- coding: utf-8 -*-
"""Atomize step: LLM structured extraction over the whole-video fused
timeline -> validated atoms with evidence refs + deterministic param
normalization + demo composite confidence."""
import json
import time
from pathlib import Path

from . import config, db, llm
from .dict import normalize_param, taxonomy

PROMPT_FILE = Path(__file__).parent / "prompts" / "atomize_v1.txt"
TAX = taxonomy()


def timeline_text(con, video_id: str) -> str:
    events = []
    for r in con.execute("SELECT id, start_ms, text FROM transcript_segment WHERE video_id=? ORDER BY start_ms", (video_id,)):
        events.append((r["start_ms"], "ASR", r["text"], r["id"]))
    for r in con.execute("SELECT id, start_ms, text FROM ocr_span WHERE video_id=? ORDER BY start_ms", (video_id,)):
        events.append((r["start_ms"], "OCR", r["text"], r["id"]))
    for r in con.execute("SELECT id, pts_ms, scene_description, subtitle_text, measurement FROM visual_observation WHERE video_id=? ORDER BY pts_ms", (video_id,)):
        txt = r["scene_description"] or ""
        if r["subtitle_text"]:
            txt += f' | 字幕:"{r["subtitle_text"]}"'
        if r["measurement"]:
            txt += f' | 数值:{r["measurement"]}'
        events.append((r["pts_ms"], "VIS", txt, r["id"]))
    events.sort(key=lambda e: e[0])
    lines = []
    for ms, mod, text, eid in events:
        ts = f"{ms//60000:02d}:{(ms%60000)//1000:02d}.{(ms%1000)//100:01d}"
        lines.append(f"{ts} [{mod}] {text} <{eid}>")
    return "\n".join(lines)


def composite_confidence(atom, refs):
    c = 0.5
    mods = {r["modality"] for r in refs}
    c += 0.2 if "ocr" in mods else 0.0
    c += 0.1 if len(mods) >= 2 else 0.0
    c += 0.1 if (atom.get("conditions") or {}).get("authority_level") else 0.0
    if mods <= {"asr"}:
        c -= 0.05
    if atom.get("parameters") and len({r["video_id"] for r in refs}) == 1:
        c -= 0.05
    return round(max(0.0, min(1.0, c)), 2)


def _validate_atom(raw, video_id, idx, index, dur):
    """Deterministic post-LLM validation & enrichment. Returns atom or None."""
    claim = (raw.get("claim") or "").strip()
    if not claim or len(claim) < 6:
        return None
    ev_refs = []
    for ev in raw.get("evidence", []):
        sid = ev.get("source_item_id")
        if sid not in index:
            continue
        it = index[sid]
        start, end = it["start_ms"], it["end_ms"]
        if end > dur:
            end = dur  # clamp (feasibility finding)
        ev_refs.append({"video_id": video_id, "modality": it["modality"],
                        "source_item_id": sid, "start_ms": start, "end_ms": end,
                        "evidence_text": it["text"],
                        "weight": float(ev.get("weight", 0.7))})
    if not ev_refs:
        return None  # zero valid evidence -> drop (100% traceability rule)
    params = [normalize_param(p) for p in raw.get("parameters", [])
              if isinstance(p, dict) and p.get("name")]
    cat = raw.get("category") or "其他"
    if cat not in TAX["categories"]:
        cat = "其他"
    space = raw.get("space") or "全屋"
    if space not in TAX["spaces"]:
        space = "全屋"
    pol = raw.get("polarity") or "neutral"
    if pol not in TAX["polarities"]:
        pol = "neutral"
    atom = {
        "id": f"atom_{video_id[-6:]}_{idx:03d}".replace("_", "_", 1),
        "video_id": video_id, "category": cat,
        "stage": raw.get("stage"), "space": space, "subject": raw.get("subject"),
        "claim": claim, "reason": raw.get("reason"),
        "risk_if_ignored": raw.get("risk_if_ignored"), "polarity": pol,
        "conditions": raw.get("conditions") or {},
        "parameters": params, "evidence_refs": ev_refs,
        "confidence": composite_confidence(raw, ev_refs),
        "status": "candidate", "cluster_id": None,
        "model": None, "prompt_version": config.get("atomize_prompt_version"),
        "created_at": db.now(),
    }
    if raw.get("notes"):
        atom["conditions"]["_notes"] = raw["notes"]
    return atom


def run(con, video_id: str) -> dict:
    asset = db.get_asset(con, video_id)
    dur = asset["duration_ms"] or 1
    index = db.timeline_index(con, video_id)
    tl = timeline_text(con, video_id)
    if not tl.strip():
        db.log_run(con, video_id, "atomize", ok=False, detail="empty timeline")
        con.commit()
        return {"n_atoms": 0, "error": "empty timeline"}
    system = PROMPT_FILE.read_text(encoding="utf-8")
    messages = [{"role": "system", "content": system},
                {"role": "user",
                 "content": f"视频标题:{asset['title']}\nUP主:{asset['author']}\n时长:{dur/1000:.0f}秒\n\n时间轴:\n{tl}"}]
    t0 = time.time()
    obj, model = llm.chat_json(messages, model=config.get("atomize_model"),
                               max_tokens=8192)
    dt = time.time() - t0
    raw_atoms = obj.get("atoms", []) if isinstance(obj, dict) else []
    atoms, dropped = [], []
    for i, raw in enumerate(raw_atoms):
        a = _validate_atom(raw, video_id, i, index, dur)
        (atoms if a else dropped).append(a or raw.get("claim", "?")[:30])
    # unique ids
    seen = set()
    for a in atoms:
        while a["id"] in seen:
            a["id"] += "x"
        seen.add(a["id"])
    db.replace_atoms(con, video_id, atoms)
    stats = {"n_raw": len(raw_atoms), "n_atoms": len(atoms),
             "n_dropped": len(dropped), "dropped": dropped[:5],
             "model": model, "process_s": round(dt, 1)}
    db.log_run(con, video_id, "atomize", model=model,
               prompt_version=config.get("atomize_prompt_version"), ok=True,
               detail=str({k: v for k, v in stats.items() if k != "dropped"}))
    con.commit()
    return stats
