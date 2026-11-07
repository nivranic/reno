# -*- coding: utf-8 -*-
"""SQLite access layer. Postgres-portable: no dialect tricks, ms timestamps."""
import json
import sqlite3
import time
from pathlib import Path

from . import config
from .migrations import SchemaVersionError

SCHEMA_PATH = Path(__file__).parent / "schema" / "schema.sql"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def connect(db: Path = None) -> sqlite3.Connection:
    """Open an initialized database without schema writes or implicit migration."""
    from .migrations import _open, validate
    path = Path(db or config.db_path())
    if not path.is_file():
        raise SchemaVersionError("Database does not exist; run: python -m reno init-db")
    con = _open(path)
    try:
        validate(con)
        return con
    except Exception:
        con.close()
        raise


def initialize(db: Path = None):
    """Explicit new-database setup. Existing legacy databases require migrate."""
    from .migrations import initialize as initialize_database
    return initialize_database(db or config.db_path())


def migrate(db: Path = None, *, profile=None):
    """Explicit maintenance-window upgrade with a consistent backup."""
    from .migrations import migrate as migrate_database
    return migrate_database(db or config.db_path(), profile=profile)


def knowledge_version(con) -> dict:
    values = dict(con.execute(
        "SELECT key,value FROM meta WHERE key IN ('library_id','library_epoch','kb_revision')"))
    return {"library_id": values["library_id"], "library_epoch": int(values["library_epoch"]),
            "kb_revision": int(values["kb_revision"])}


def bump_kb_revision(con) -> int:
    """Participate in the caller's write transaction; never independently commit."""
    cursor = con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'")
    if cursor.rowcount != 1:
        raise SchemaVersionError("Knowledge revision metadata is missing")
    return int(con.execute("SELECT value FROM meta WHERE key='kb_revision'").fetchone()[0])


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

def _require_reference_writer(con, profile=None):
    """Reference mutations borrow a caller guard; never acquire after SQL."""
    from . import storage_admin, storage_lifecycle
    profile = storage_admin.from_connection(con, profile=profile)
    storage_lifecycle.require_guard(profile)
    return profile


def upsert_asset(con, a: dict, *, profile=None):
    _require_reference_writer(con, profile)
    con.execute(
        """INSERT INTO video_asset (video_id, source_platform, source_type, source_url,
               title, author, duration_ms, width, height, fps, content_sha256,
               audio_sha256, imported_at, files_json, status, source_published_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(video_id) DO UPDATE SET
             title=excluded.title, duration_ms=excluded.duration_ms,
             files_json=excluded.files_json, status=excluded.status,
             source_published_at=COALESCE(excluded.source_published_at,video_asset.source_published_at)""",
        (a["video_id"], a.get("source_platform"), a.get("source_type"),
         a.get("source_url"), a.get("title"), a.get("author"),
         a.get("duration_ms"), a.get("width"), a.get("height"), a.get("fps"),
         a["content_sha256"], a.get("audio_sha256"), a.get("imported_at", now()),
         j(a.get("files")), a.get("status", "imported"), a.get("source_published_at")))


def get_asset(con, video_id: str, *, public=False):
    from . import sync_visibility
    restriction = " AND " + sync_visibility.video_sql() if public else ""
    return con.execute("SELECT * FROM video_asset WHERE video_id=?" + restriction,
                       (video_id,)).fetchone()


def snapshot(con, path, *, profile=None, kind="manual", backup_id=None):
    """Registered committed image under the caller's exact storage protection.

    An active transaction must already hold its guard. The independent source
    reader copies committed data without committing or rolling back this con.
    Facility setup belongs to startup or explicit offline maintenance.
    """
    from . import storage_admin, storage_backups
    profile = storage_admin.from_connection(con, profile=profile)
    return storage_backups.create(con, Path(path).absolute(), profile=profile,
                                  kind=kind, backup_id=backup_id)


def asset_by_sha(con, sha: str):
    return con.execute("SELECT * FROM video_asset WHERE content_sha256=?",
                       (sha,)).fetchone()


# ---------- step outputs (replace-by-video semantics for idempotent reruns) ----------

def replace_frames(con, video_id, frames, *, profile=None):
    _require_reference_writer(con, profile)
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

# Carry missing enrichments only when the underlying content and evidence are
# unchanged. Positional atom ids alone are never a safe identity for carry-over.
V2_ATOM_COLS = ("dimension", "evidence_nature", "exceptions", "prices_json")


def _json_col(v) -> str | None:
    """Accept dict/list round-tripped from all_atoms or a pre-serialized str."""
    if v is None or isinstance(v, str):
        return v
    return j(v)


def replace_atoms(con, video_id, atoms):
    from . import evidence_identity as identity, review
    atoms = [dict(atom) for atom in atoms]
    incoming_ids = [atom["id"] for atom in atoms]
    if len(set(incoming_ids)) != len(incoming_ids):
        raise ValueError("replacement contains duplicate atom ids")
    old_atoms = {row["id"]: dict(row) for row in con.execute(
        "SELECT * FROM knowledge_atom WHERE video_id=?", (video_id,))}
    for atom in old_atoms.values():
        atom["evidence_refs"] = []
    for row in con.execute("SELECT * FROM evidence_ref WHERE atom_id IN "
                           "(SELECT id FROM knowledge_atom WHERE video_id=?)", (video_id,)):
        old_atoms[row["atom_id"]]["evidence_refs"].append(dict(row))

    def comparable(atom, *, base=False):
        snap = identity.atom_snapshot(atom)
        if base:
            for key in ("exceptions", "prices", "evidence_nature"):
                snap.pop(key, None)
        else:
            snap["retrieval"] = {key: atom.get(key) for key in (
                "category", "stage", "space", "subject", "reason", "risk_if_ignored", "dimension")}
            snap["confidence"] = atom.get("confidence", 0.5)
            snap["weights"] = sorted(identity.canonical_json({
                "start_ms": e.get("start_ms"), "source_item_id": e.get("source_item_id"),
                "weight": e.get("weight", 0.7)}) for e in atom.get("evidence_refs", []))
        return identity.canonical_json(snap)

    for atom in atoms:
        other = con.execute("SELECT video_id FROM knowledge_atom WHERE id=?", (atom["id"],)).fetchone()
        if other and other["video_id"] != video_id:
            raise ValueError("replacement atom id belongs to another video")
        atom["video_id"] = video_id
        if "prices" in atom and "prices_json" not in atom:
            atom["prices_json"] = atom["prices"]
        old = old_atoms.get(atom["id"])
        if old and comparable(old, base=True) == comparable(atom, base=True):
            for column in V2_ATOM_COLS:
                if column not in atom:
                    atom[column] = old[column]
        # A stable replay must not briefly reset an unchanged confirmed atom.
        atom["status"] = old["status"] if old and comparable(old) == comparable(atom) else "candidate"
        if old and "cluster_id" not in atom:
            atom["cluster_id"] = old["cluster_id"]
    old_values = {aid: comparable(atom) for aid, atom in old_atoms.items()}
    new_values = {atom["id"]: comparable(atom) for atom in atoms}
    changed_ids = {aid for aid in old_values.keys() | new_values.keys()
                   if old_values.get(aid) != new_values.get(aid)}
    # Remove stale outputs in this video's scope, but keep review events and
    # personal references so their snapshots can be surfaced as stale/missing.
    for atom_id in changed_ids:
        con.execute("DELETE FROM evidence_ref WHERE atom_id=?", (atom_id,))
        con.execute("DELETE FROM atom_fts WHERE atom_id=?", (atom_id,))
        con.execute("DELETE FROM knowledge_atom WHERE id=? AND video_id=?", (atom_id, video_id))
    for a in atoms:
        if a["id"] not in changed_ids:
            continue
        con.execute(
            """INSERT INTO knowledge_atom (id, video_id, category, stage, space, subject,
                   claim, reason, risk_if_ignored, polarity, conditions_json,
                   parameters_json, confidence, status, cluster_id, model, prompt_version, created_at,
                   dimension, evidence_nature, exceptions, prices_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["id"], video_id, a.get("category"), a.get("stage"), a.get("space"),
             a.get("subject"), a["claim"], a.get("reason"), a.get("risk_if_ignored"),
             a["polarity"], j(a.get("conditions")), j(a.get("parameters")),
             a.get("confidence", 0.5), a.get("status", "candidate"),
             a.get("cluster_id"), a.get("model"), a.get("prompt_version"),
             a.get("created_at", now()),
             a.get("dimension"), a.get("evidence_nature"),
             _json_col(a.get("exceptions")), _json_col(a.get("prices_json"))))
        for r in a["evidence_refs"]:
            con.execute(
                """INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,
                       start_ms, end_ms, evidence_text, weight) VALUES (?,?,?,?,?,?,?,?)""",
                (a["id"], video_id, r["modality"], r.get("source_item_id"),
                 r["start_ms"], r["end_ms"], r.get("evidence_text"), r.get("weight", 0.7)))
    fts_sync_atoms(con, changed_ids)
    review.replay(con)


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


def all_atoms(con, video_id: str | None = None, *, public=False):
    """All atoms with grouped evidence_refs in two queries (was one evidence
    query per atom). video_id restricts to atoms evidenced in that video."""
    from . import sync_visibility
    sql = "SELECT * FROM knowledge_atom"
    where = []
    args: tuple = ()
    if video_id:
        where.append("id IN (SELECT atom_id FROM evidence_ref WHERE video_id=?)")
        args = (video_id,)
    if public:
        where.append(sync_visibility.atom_sql())
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = []
    for row in con.execute(sql + " ORDER BY id", args):
        a = dict(row)
        a["conditions"] = uj(a.pop("conditions_json"))
        a["parameters"] = uj(a.pop("parameters_json")) or []
        a["prices"] = uj(a.pop("prices_json")) or []
        a["exceptions"] = uj(a.pop("exceptions")) or []
        a["evidence_refs"] = []
        out.append(a)
    by_id = {a["id"]: a for a in out}
    ev_sql = "SELECT * FROM evidence_ref"
    ev_where = []
    if video_id:
        ev_where.append("video_id=?")
        ev_args: tuple = (video_id,)
    else:
        ev_args = ()
    if public:
        ev_where.append("EXISTS (SELECT 1 FROM knowledge_atom sv_atom WHERE sv_atom.id=evidence_ref.atom_id AND "
                        + sync_visibility.atom_sql("sv_atom") + ")")
    if ev_where:
        ev_sql += " WHERE " + " AND ".join(ev_where)
    for e in con.execute(ev_sql + " ORDER BY atom_id, start_ms", ev_args):
        a = by_id.get(e["atom_id"])
        if a is not None:
            a["evidence_refs"].append(dict(e))
    if public and config.is_cloud():
        visible_clusters = {row[0] for row in con.execute(
            "SELECT cluster_id FROM knowledge_cluster WHERE " + sync_visibility.cluster_sql())}
        for atom in out:
            if atom["cluster_id"] not in visible_clusters:
                atom["cluster_id"] = None
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

def _derivation_json(value):
    from . import derivation, evidence_identity
    checked = derivation.validate_binding(value)
    return evidence_identity.canonical_json(checked) if checked is not None else None


def replace_clusters(con, clusters):
    from . import review
    before = [tuple(row) for row in con.execute(
        "SELECT cluster_id,canonical_topic,relation,judge_reason,members_json,linked_conflict,derivation_json FROM knowledge_cluster ORDER BY cluster_id")]
    incoming = sorted((c["cluster_id"], c["canonical_topic"], c.get("relation"),
                       c.get("judge_reason"), j(c["members"]), c.get("linked_conflict"),
                       _derivation_json(c.get("derivation_json"))) for c in clusters)
    if before == incoming:
        review.replay(con)
        return
    con.execute("DELETE FROM knowledge_cluster")
    con.executemany(
        "INSERT INTO knowledge_cluster (cluster_id, canonical_topic, relation, judge_reason, members_json, linked_conflict, derivation_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
        [(*row, now()) for row in incoming])
    con.execute("UPDATE knowledge_atom SET cluster_id=NULL")
    for c in clusters:
        for m in c["members"]:
            con.execute("UPDATE knowledge_atom SET cluster_id=? WHERE id=?", (c["cluster_id"], m))
    review.replay(con)


def replace_conflicts(con, conflicts):
    from . import review
    before = [tuple(row) for row in con.execute(
        "SELECT conflict_id,ctype,side_a_json,side_b_json,analysis_json,recommended_action,derivation_json FROM conflict_case ORDER BY conflict_id")]
    incoming = sorted((c["conflict_id"], c["type"],
                       j(c.get("members", {}).get("side_a") or c.get("members", {}).get("a")),
                       j(c.get("members", {}).get("side_b") or c.get("members", {}).get("b")),
                       j({"conditional_conclusion": c.get("conditional_conclusion"),
                          "judge_note": c.get("judge_note")}), c.get("recommended_action"),
                       _derivation_json(c.get("derivation_json"))) for c in conflicts)
    if before == incoming:
        review.replay(con)
        return
    old_statuses = dict(con.execute("SELECT conflict_id,status FROM conflict_case"))
    new_statuses = {c["conflict_id"]: c.get("status", "open") for c in conflicts}
    con.execute("DELETE FROM conflict_case")
    con.executemany(
        "INSERT INTO conflict_case (conflict_id, ctype, side_a_json, side_b_json, analysis_json, recommended_action, derivation_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(*row, old_statuses.get(row[0], new_statuses[row[0]]), now()) for row in incoming])
    review.replay(con)


def add_decision(con, atom_id=None, conflict_id=None, action="", revised_claim=None, note=None,
                 event_id=None, client_id=None, target_fingerprint=None):
    from .review import record_decision
    return record_decision(con, atom_id=atom_id, conflict_id=conflict_id, action=action,
                           revised_claim=revised_claim, note=note, event_id=event_id,
                           client_id=client_id, target_fingerprint=target_fingerprint)


# ---------- processing_run / jobs ----------

def log_run(con, video_id, step, model=None, ok=True, detail=None, **ver):
    con.execute(
        """INSERT INTO processing_run (video_id, step, model, prompt_version, schema_version,
               taxonomy_version, started_at, completed_at, ok, detail)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (video_id, step, model, ver.get("prompt_version"), ver.get("schema_version"),
         ver.get("taxonomy_version"), now(), now(), 1 if ok else 0, detail))


def enqueue_job(con, video_id=None, kind="process", payload=None, request_id=None, *, profile=None):
    from .tasks import enqueue
    return enqueue(con, video_id, kind, payload, request_id, profile=profile)


def claim_next_job(con, owner_id=None):
    from .tasks import claim, TaskError
    if not owner_id:
        raise TaskError("认领任务需要有效 worker owner_id")
    return claim(con, owner_id)


def enqueue_sync_urls(con, urls) -> int:
    """Cloud import compatibility wrapper around the validated sync queue.

    This entrypoint retains its historical commit contract. Domain callers
    requiring an encompassing transaction use sync_queue.enqueue directly.
    Content deduplication still belongs to the home processing pipeline.
    """
    from .sync_queue import enqueue
    count = enqueue(con, urls)
    con.commit()
    return count
