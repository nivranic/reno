"""Cloud publication visibility for user reads, separate from sync/replay facts.

SQL predicates are applied before ranking, LIMIT and aggregate queries. Local
roles deliberately retain their existing behavior. Internal maintenance and
review replay never opt into this user-facing view.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import config


def _alias(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("invalid internal SQL alias")
    return value


def video_sql(alias="video_asset"):
    alias = _alias(alias)
    return f"{alias}.sync_state='ready'" if config.is_cloud() else "1=1"


def atom_sql(alias="knowledge_atom"):
    alias = _alias(alias)
    if not config.is_cloud():
        return "1=1"
    return (f"EXISTS (SELECT 1 FROM video_asset sv_video WHERE sv_video.video_id={alias}.video_id "
            "AND sv_video.sync_state='ready') AND NOT EXISTS (SELECT 1 FROM evidence_ref sv_evidence "
            f"WHERE sv_evidence.atom_id={alias}.id AND (sv_evidence.video_id IS NOT {alias}.video_id "
            "OR NOT EXISTS (SELECT 1 FROM video_asset sv_source WHERE sv_source.video_id=sv_evidence.video_id "
            "AND sv_source.sync_state='ready')))")


def _json(column, fallback):
    return f"CASE WHEN json_valid({column}) THEN {column} ELSE '{fallback}' END"


def _visible_refs(array):
    return (f"NOT EXISTS (SELECT 1 FROM json_each({array}) sv_ref WHERE sv_ref.type<>'text' OR NOT EXISTS "
            "(SELECT 1 FROM knowledge_atom sv_member WHERE sv_member.id=sv_ref.value AND "
            + atom_sql("sv_member") + "))")


def conflict_sql(alias="conflict_case"):
    alias = _alias(alias)
    if not config.is_cloud():
        return "1=1"
    predicates, has_reference = [], []
    for name in ("side_a_json", "side_b_json"):
        column = f"{alias}.{name}"
        side = _json(column, "{}")
        array = f"CASE WHEN json_type({side},'$.atoms')='array' THEN json_extract({side},'$.atoms') ELSE '[]' END"
        predicates.extend((
            f"({column} IS NULL OR (json_valid({column}) AND json_type({side}) IN ('object','null')))",
            f"(json_type({side},'$.atoms') IS NULL OR json_type({side},'$.atoms') IN ('array','null'))",
            _visible_refs(array),
            f"(json_type({side},'$.video') IS NULL OR json_type({side},'$.video')='null' OR "
            f"(json_type({side},'$.video')='text' AND EXISTS (SELECT 1 FROM video_asset sv_side_video "
            f"WHERE sv_side_video.video_id=json_extract({side},'$.video') AND sv_side_video.sync_state='ready')))",
        ))
        has_reference.append(f"json_array_length({array})>0 OR json_type({side},'$.video')='text'")
    return "(" + " AND ".join(predicates) + " AND (" + " OR ".join(has_reference) + "))"


def cluster_sql(alias="knowledge_cluster"):
    alias = _alias(alias)
    if not config.is_cloud():
        return "1=1"
    members = _json(f"{alias}.members_json", "[]")
    array = f"CASE WHEN json_type({members})='array' THEN {members} ELSE '[]' END"
    return (f"(json_valid({alias}.members_json) AND json_type({members})='array' "
            f"AND json_array_length({array})>0 AND " + _visible_refs(array)
            + f" AND ({alias}.linked_conflict IS NULL OR EXISTS (SELECT 1 FROM conflict_case sv_linked "
            f"WHERE sv_linked.conflict_id={alias}.linked_conflict AND " + conflict_sql("sv_linked") + ")))" )


def target_visible(con, kind, target_id):
    if not config.is_cloud():
        return True
    if not isinstance(target_id, str):
        return False
    if kind == "atom":
        return con.execute("SELECT 1 FROM knowledge_atom WHERE id=? AND " + atom_sql(), (target_id,)).fetchone() is not None
    if kind == "conflict":
        return con.execute("SELECT 1 FROM conflict_case WHERE conflict_id=? AND " + conflict_sql(), (target_id,)).fetchone() is not None
    return False


def history_sql(alias="user_decision"):
    alias = _alias(alias)
    if not config.is_cloud():
        return "1=1"
    clauses = [f"({alias}.atom_id IS NOT NULL OR {alias}.conflict_id IS NOT NULL)"]
    for column, table, key, target_alias, predicate in (
        ("atom_id", "knowledge_atom", "id", "sv_target_atom", atom_sql),
        ("conflict_id", "conflict_case", "conflict_id", "sv_target_conflict", conflict_sql),
    ):
        query = f"SELECT 1 FROM {table} {target_alias} WHERE {target_alias}.{key}={alias}.{column}"
        clauses.append(f"({alias}.{column} IS NULL OR NOT EXISTS ({query}) OR EXISTS "
                       f"({query} AND {predicate(target_alias)}))")
    return "(" + " AND ".join(clauses) + ")"


def snapshot_visible(con, value, *, require_proof=False):
    """Historical evidence can refer to a now-hidden source despite a ready target."""
    if not config.is_cloud():
        return True
    if not isinstance(value, dict):
        return False

    proven = []

    def visible_video(video_id):
        if video_id is None:
            return True
        visible = isinstance(video_id, str) and con.execute(
            "SELECT 1 FROM video_asset WHERE video_id=? AND sync_state='ready'", (video_id,)).fetchone() is not None
        if visible:
            proven.append(video_id)
        return visible

    if not visible_video(value.get("video_id")):
        return False
    evidence = value.get("evidence_refs", [])
    if not isinstance(evidence, list) or any(not isinstance(ref, dict) or not visible_video(ref.get("video_id")) for ref in evidence):
        return False
    for side_name in ("side_a", "side_b"):
        if side_name not in value:
            continue
        side = value[side_name]
        if not isinstance(side, dict) or not visible_video(side.get("video")):
            return False
        members = side.get("members", [])
        if not isinstance(members, list):
            return False
        for member in members:
            if not isinstance(member, dict) or not isinstance(member.get("atom_id"), str):
                return False
            exists = con.execute("SELECT 1 FROM knowledge_atom WHERE id=?", (member["atom_id"],)).fetchone() is not None
            if exists and not target_visible(con, "atom", member["atom_id"]):
                return False
            content = member.get("content")
            if content is not None:
                if not snapshot_visible(con, content, require_proof=not exists):
                    return False
                if snapshot_visible(con, content, require_proof=True):
                    proven.append("frozen-member")
            elif not exists:
                return False
    return not require_proof or bool(proven)


def cache_signature(con):
    role = config.get("node_role", "all")
    if not config.is_cloud():
        return str(role)
    rows = [tuple(row) for row in con.execute("SELECT video_id,sync_state FROM video_asset ORDER BY video_id")]
    return "cloud:" + hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def report_visible(con, manifest):
    if not config.is_cloud():
        return True
    from . import db
    if not isinstance(manifest, dict):
        return False
    version = {key: manifest.get(key) for key in ("library_id", "library_epoch", "kb_revision")}
    if manifest.get("synced"):
        pointer = con.execute("SELECT * FROM sync_report WHERE name=?", (manifest.get("report_type"),)).fetchone()
        publication = con.execute("SELECT * FROM sync_publication WHERE publication_id=?", (manifest.get("publication_id"),)).fetchone()
        if pointer is None or publication is None or pointer["publication_id"] != publication["publication_id"]:
            return False
        try:
            if json.loads(pointer["local_version_json"]) != version or json.loads(publication["local_version_json"]) != version:
                return False
            source_version = {"library_id": publication["source_library_id"],
                              "library_epoch": publication["source_epoch"], "kb_revision": publication["source_revision"]}
            if (json.loads(pointer["source_version_json"]) != source_version
                    or manifest.get("source_version") != source_version):
                return False
        except (TypeError, ValueError):
            return False
        if (pointer["storage_key"] != manifest.get("content_file")
                or pointer["sha256"] != manifest.get("content_sha256")):
            return False
        return con.execute(
            "SELECT 1 FROM sync_published_video pv LEFT JOIN video_asset va ON va.video_id=pv.video_id "
            "WHERE pv.publication_id=? AND (va.video_id IS NULL OR va.sync_state IS NOT 'ready' "
            "OR pv.source_library_id<>? OR pv.source_node_id<>? OR pv.source_epoch<>? OR pv.source_revision<>?) LIMIT 1",
            (publication["publication_id"], publication["source_library_id"], publication["source_node_id"],
             publication["source_epoch"], publication["source_revision"])).fetchone() is None
    # Old filesystem manifests do not record per-video membership. Only an
    # exact current version with all videos ready proves safe coverage.
    return (version == db.knowledge_version(con) and con.execute(
        "SELECT 1 FROM video_asset WHERE sync_state IS NOT 'ready' LIMIT 1").fetchone() is None)
