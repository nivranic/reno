# -*- coding: utf-8 -*-
"""SQLite access layer. Postgres-portable: no dialect tricks, ms timestamps."""
import json
import sqlite3
import time
from pathlib import Path

from . import config

SCHEMA_PATH = Path(__file__).parent / "schema" / "schema.sql"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def connect(db: Path = None) -> sqlite3.Connection:
    db = db or config.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(con)
    return con


def _migrate(con):
    """Idempotent lightweight migrations (ALTER ADD COLUMN style)."""
    need = {"visual_observation": {"measurement": "TEXT"}}
    for table, cols in need.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for col, typ in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # v2: step-output tables had video-global PKs -> cross-video ids collide
    # (asr_0000 / ocr_000 / f0000200 exist in EVERY video). Rebuild with
    # composite PKs. Single pass over all four tables; step meta cleared so
    # the pipeline re-runs everything.
    rebuilt = False
    for table in ("frame", "visual_observation", "transcript_segment", "ocr_span"):
        pk = [r["name"] for r in con.execute(f"PRAGMA table_info({table})")
              if r["pk"]]
        if pk and "video_id" not in pk:
            con.execute(f"DROP TABLE {table}")
            rebuilt = True
    if rebuilt:
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.execute("DELETE FROM meta WHERE key LIKE 'step:%'")
    con.commit()


def j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


def uj(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


# ---------- video_asset ----------

def upsert_asset(con, a: dict):
    con.execute(
        """INSERT INTO video_asset (video_id, source_platform, source_type, source_url,
               title, author, duration_ms, width, height, fps, content_sha256,
               audio_sha256, imported_at, files_json, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(video_id) DO UPDATE SET
             title=excluded.title, duration_ms=excluded.duration_ms,
             files_json=excluded.files_json, status=excluded.status""",
        (a["video_id"], a.get("source_platform"), a.get("source_type"),
         a.get("source_url"), a.get("title"), a.get("author"),
         a.get("duration_ms"), a.get("width"), a.get("height"), a.get("fps"),
         a["content_sha256"], a.get("audio_sha256"), a.get("imported_at", now()),
         j(a.get("files")), a.get("status", "imported")))


def get_asset(con, video_id: str):
    return con.execute("SELECT * FROM video_asset WHERE video_id=?",
                       (video_id,)).fetchone()


def asset_by_sha(con, sha: str):
    return con.execute("SELECT * FROM video_asset WHERE content_sha256=?",
                       (sha,)).fetchone()


# ---------- step outputs (replace-by-video semantics for idempotent reruns) ----------

def replace_frames(con, video_id, frames):
    con.execute("DELETE FROM frame WHERE video_id=?", (video_id,))
    con.executemany(
        "INSERT INTO frame (frame_id, video_id, pts_ms, origin, dhash, path) VALUES (?,?,?,?,?,?)",
        [(f["frame_id"], video_id, f["pts_ms"], f["origin"], f["dhash"], f["path"])
         for f in frames])


def replace_segments(con, video_id, segs):
    con.execute("DELETE FROM transcript_segment WHERE video_id=?", (video_id,))
    con.executemany(
        "INSERT INTO transcript_segment (id, video_id, idx, start_ms, end_ms, text) VALUES (?,?,?,?,?,?)",
        [(s["id"], video_id, i, s["start_ms"], s["end_ms"], s["text"])
         for i, s in enumerate(segs)])


def replace_ocr(con, video_id, spans):
    con.execute("DELETE FROM ocr_span WHERE video_id=?", (video_id,))
    con.executemany(
        "INSERT INTO ocr_span (id, video_id, start_ms, end_ms, text, conf, frame_ids_json) VALUES (?,?,?,?,?,?,?)",
        [(o["id"], video_id, o["start_ms"], o["end_ms"], o["text"],
          o.get("conf", 0.0), j(o.get("frame_ids"))) for o in spans])


def replace_visual(con, video_id, obs):
    con.execute("DELETE FROM visual_observation WHERE video_id=?", (video_id,))
    con.executemany(
        "INSERT INTO visual_observation (id, video_id, frame_id, pts_ms, scene_description, materials_json, tools_json, subtitle_text, measurement) VALUES (?,?,?,?,?,?,?,?,?)",
        [(o["id"], video_id, o["frame_id"], o["pts_ms"], o.get("scene_description"),
          j(o.get("materials")), j(o.get("tools")), o.get("subtitle_text"),
          o.get("measurement")) for o in obs])


# ---------- atoms ----------

def replace_atoms(con, video_id, atoms):
    for a in atoms:
        con.execute("DELETE FROM knowledge_atom WHERE id=?", (a["id"],))
        con.execute(
            """INSERT INTO knowledge_atom (id, video_id, category, stage, space, subject,
                   claim, reason, risk_if_ignored, polarity, conditions_json,
                   parameters_json, confidence, status, cluster_id, model, prompt_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["id"], video_id, a.get("category"), a.get("stage"), a.get("space"),
             a.get("subject"), a["claim"], a.get("reason"), a.get("risk_if_ignored"),
             a["polarity"], j(a.get("conditions")), j(a.get("parameters")),
             a.get("confidence", 0.5), a.get("status", "candidate"),
             a.get("cluster_id"), a.get("model"), a.get("prompt_version"),
             a.get("created_at", now())))
        con.execute("DELETE FROM evidence_ref WHERE atom_id=?", (a["id"],))
        for r in a["evidence_refs"]:
            con.execute(
                """INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,
                       start_ms, end_ms, evidence_text, weight) VALUES (?,?,?,?,?,?,?,?)""",
                (a["id"], video_id, r["modality"], r.get("source_item_id"),
                 r["start_ms"], r["end_ms"], r.get("evidence_text"), r.get("weight", 0.7)))
    fts_sync_atoms(con, [a["id"] for a in atoms])


def fts_prep(text: str) -> str:
    """CJK char-spacing so the default unicode61 tokenizer can match Chinese
    (whole-sentence CJK otherwise becomes one giant token -> 0 hits)."""
    import re
    return re.sub(r"([\u4e00-\u9fff])", r"\1 ", text or "")


def fts_sync_atoms(con, atom_ids):
    for aid in atom_ids:
        row = con.execute("SELECT * FROM knowledge_atom WHERE id=?", (aid,)).fetchone()
        con.execute("DELETE FROM atom_fts WHERE atom_id=?", (aid,))
        if row:
            con.execute("INSERT INTO atom_fts (atom_id, claim, reason, category, space) VALUES (?,?,?,?,?)",
                        (aid, fts_prep(row["claim"]), fts_prep(row["reason"] or ""),
                         row["category"] or "", row["space"] or ""))


def fts_reseed(con):
    con.execute("DELETE FROM atom_fts")
    ids = [r["id"] for r in con.execute("SELECT id FROM knowledge_atom")]
    fts_sync_atoms(con, ids)
    con.commit()
    return len(ids)


def all_atoms(con):
    out = []
    for row in con.execute("SELECT * FROM knowledge_atom ORDER BY id"):
        a = dict(row)
        a["conditions"] = uj(a.pop("conditions_json"))
        a["parameters"] = uj(a.pop("parameters_json")) or []
        ev = con.execute("SELECT * FROM evidence_ref WHERE atom_id=? ORDER BY start_ms", (a["id"],)).fetchall()
        a["evidence_refs"] = [dict(e) for e in ev]
        out.append(a)
    return out


def timeline_index(con, video_id: str) -> dict:
    """id -> {modality,start_ms,end_ms,text} for evidence resolution."""
    idx = {}
    for r in con.execute("SELECT id, start_ms, end_ms, text FROM transcript_segment WHERE video_id=?", (video_id,)):
        idx[r["id"]] = {"modality": "asr", "start_ms": r["start_ms"],
                        "end_ms": r["end_ms"], "text": r["text"]}
    for r in con.execute("SELECT id, start_ms, end_ms, text FROM ocr_span WHERE video_id=?", (video_id,)):
        idx[r["id"]] = {"modality": "ocr", "start_ms": r["start_ms"],
                        "end_ms": r["end_ms"], "text": r["text"]}
    for r in con.execute("SELECT id, pts_ms, scene_description FROM visual_observation WHERE video_id=?", (video_id,)):
        idx[r["id"]] = {"modality": "vision", "start_ms": r["pts_ms"],
                        "end_ms": r["pts_ms"] + 1200, "text": r["scene_description"] or ""}
    return idx


# ---------- clusters / conflicts / decisions ----------

def replace_clusters(con, clusters):
    con.execute("DELETE FROM knowledge_cluster")
    con.executemany(
        "INSERT INTO knowledge_cluster (cluster_id, canonical_topic, relation, judge_reason, members_json, linked_conflict, created_at) VALUES (?,?,?,?,?,?,?)",
        [(c["cluster_id"], c["canonical_topic"], c.get("relation"),
          c.get("judge_reason"), j(c["members"]), c.get("linked_conflict"), now())
         for c in clusters])
    con.execute("UPDATE knowledge_atom SET cluster_id=NULL")
    for c in clusters:
        for m in c["members"]:
            con.execute("UPDATE knowledge_atom SET cluster_id=? WHERE id=?", (c["cluster_id"], m))
            if c.get("relation") == "conflicting":
                con.execute("UPDATE knowledge_atom SET status='disputed' WHERE id=?", (m,))


def replace_conflicts(con, conflicts):
    con.execute("DELETE FROM conflict_case")
    con.executemany(
        "INSERT INTO conflict_case (conflict_id, ctype, status, side_a_json, side_b_json, analysis_json, recommended_action, created_at) VALUES (?,?,?,?,?,?,?,?)",
        [(c["conflict_id"], c["type"], c.get("status", "open"),
          j(c.get("members", {}).get("side_a") or c.get("members", {}).get("a")),
          j(c.get("members", {}).get("side_b") or c.get("members", {}).get("b")),
          j({"conditional_conclusion": c.get("conditional_conclusion"),
             "judge_note": c.get("judge_note")}),
          c.get("recommended_action"), now()) for c in conflicts])


def add_decision(con, atom_id=None, conflict_id=None, action="", revised_claim=None, note=None):
    con.execute(
        "INSERT INTO user_decision (atom_id, conflict_id, action, revised_claim, note, created_at) VALUES (?,?,?,?,?,?)",
        (atom_id, conflict_id, action, revised_claim, note, now()))
    if atom_id and action == "confirm":
        con.execute("UPDATE knowledge_atom SET status='verified' WHERE id=?", (atom_id,))
    if atom_id and action == "reject":
        con.execute("UPDATE knowledge_atom SET status='rejected' WHERE id=?", (atom_id,))


# ---------- processing_run / jobs ----------

def log_run(con, video_id, step, model=None, ok=True, detail=None, **ver):
    con.execute(
        """INSERT INTO processing_run (video_id, step, model, prompt_version, schema_version,
               taxonomy_version, started_at, completed_at, ok, detail)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (video_id, step, model, ver.get("prompt_version"), ver.get("schema_version"),
         ver.get("taxonomy_version"), now(), now(), 1 if ok else 0, detail))


def enqueue_job(con, video_id, kind="process"):
    ex = con.execute("SELECT job_id FROM job WHERE video_id=? AND kind=? AND status IN ('pending','running')",
                     (video_id, kind)).fetchone()
    if ex:
        return ex["job_id"]
    cur = con.execute("INSERT INTO job (video_id, kind, status, created_at, updated_at) VALUES (?,?, 'pending', ?, ?)",
                      (video_id, kind, now(), now()))
    return cur.lastrowid


def claim_next_job(con):
    row = con.execute(
        "SELECT * FROM job WHERE status='pending' ORDER BY job_id LIMIT 1").fetchone()
    if not row:
        return None
    con.execute("UPDATE job SET status='running', attempts=attempts+1, updated_at=? WHERE job_id=?",
                (now(), row["job_id"]))
    con.commit()
    return dict(row)
