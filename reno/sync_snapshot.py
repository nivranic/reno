"""Read-only export and closure validation for an entire processed generation.

Media bytes and reports are verified by the publication layer. Default exports
reject incomplete generations. An explicit recovery projection selects whole
closed processed relationships through a caller-owned TEMP view. Neither path
changes the source database, opens media files, commits transactions, or exports
personal/operational data.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import uuid

from . import db, sync_protocol as protocol
from .sync_protocol import SyncError

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_VIDEOS = 10_000
MAX_REPORTS = 5
MAX_ENVELOPE_DEPTH = 24
VIDEO_TABLES = tuple(name for name, spec in protocol.TABLES.items()
                     if spec["scope"] in ("video", "step"))
GLOBAL_TABLES = ("knowledge_cluster", "conflict_case")
JSON_COLUMNS = frozenset(("conditions_json", "parameters_json", "exceptions", "prices_json",
                          "frame_ids_json", "materials_json", "tools_json", "members_json",
                          "side_a_json", "side_b_json", "analysis_json", "derivation_json"))


def _invalid(message, *, status=400):
    raise SyncError(message, "INVALID_SYNC_SNAPSHOT", status)


def _incomplete(message):
    raise SyncError(message, "SYNC_INCOMPLETE_SNAPSHOT", 409)


def _exact(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        _invalid(f"Invalid {label} fields")


def _uuid(value, label):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError, TypeError):
        _invalid(f"{label} must be a canonical UUID")


def _version(value):
    _exact(value, ("library_id", "library_epoch", "kb_revision"), "knowledge version")
    _uuid(value["library_id"], "library_id")
    for name, minimum in (("library_epoch", 1), ("kb_revision", 0)):
        if type(value[name]) is not int or not minimum <= value[name] <= 2**63 - 1:
            _invalid("Knowledge version counters must be bounded integers")


def _check_json(value, depth=0):
    """Bound recursion and reject implicit coercion of non-JSON Python values."""
    if depth > MAX_ENVELOPE_DEPTH:
        _invalid("Snapshot JSON nesting exceeds its limit")
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _invalid("Snapshot JSON object keys must be strings")
            _check_json(key, depth + 1)
            _check_json(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _check_json(child, depth + 1)
    elif type(value) is str:
        if len(value) > MAX_SNAPSHOT_BYTES:
            _invalid("Snapshot exceeds its byte limit", status=413)
    elif type(value) is float:
        if not math.isfinite(value):
            _invalid("Snapshot JSON contains a non-finite number")
    elif value is not None and type(value) not in (bool, int):
        _invalid("Snapshot contains a non-JSON value")


def _encoded(value):
    _check_json(value)
    chunks, size = [], 0
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":"), sort_keys=True)
        for part in encoder.iterencode(value):
            encoded = part.encode("utf-8")
            size += len(encoded)
            if size > MAX_SNAPSHOT_BYTES:
                _invalid("Snapshot exceeds its byte limit", status=413)
            chunks.append(encoded)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        if isinstance(exc, SyncError):
            raise
        _invalid("Snapshot cannot be encoded as bounded JSON")
    return b"".join(chunks)


def _normalize_rows(table, rows, *, video_id=None):
    checked = protocol.validate_rows(table, rows, video_id=video_id)
    for row in checked:
        # Preserve the source processing state. Review status is replayed and
        # receiver sync readiness is never a wire field.
        if "status" in row and table != "video_asset":
            _invalid("Snapshot rows cannot supply derived review status")
        for column in JSON_COLUMNS.intersection(row):
            if row[column] is not None:
                row[column] = json.dumps(json.loads(row[column]), ensure_ascii=False,
                                         allow_nan=False, sort_keys=True, separators=(",", ":"))
    logical_key = protocol.TABLES[table]["logical_key"]
    checked.sort(key=(lambda row: tuple(row[column] for column in logical_key))
                 if logical_key else _encoded)
    return checked


def _json(row, column):
    raw = row.get(column)
    return json.loads(raw) if raw is not None else None


def _refs(value, label):
    if value is None:
        return []
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        _invalid(f"{label} must contain reference identifiers")
    return value


def _check_local_references(video):
    tables = video["tables"]
    atoms = {row["id"] for row in tables["knowledge_atom"]}
    frames = {row["frame_id"] for row in tables["frame"]}
    sources = {
        "asr": {row["id"] for row in tables["transcript_segment"]},
        "ocr": {row["id"] for row in tables["ocr_span"]},
        "vision": {row["id"] for row in tables["visual_observation"]},
    }
    for row in tables["ocr_span"]:
        if not set(_refs(_json(row, "frame_ids_json"), "OCR frames")) <= frames:
            _incomplete("OCR refers to a frame outside its video")
    for row in tables["visual_observation"]:
        if row.get("frame_id") is not None and row["frame_id"] not in frames:
            _incomplete("Visual observation refers to a frame outside its video")
    for row in tables["evidence_ref"]:
        if row["atom_id"] not in atoms:
            _incomplete("Evidence refers to an atom outside its video")
        source_id = row.get("source_item_id")
        # Old nullable source pointers can still describe timestamp evidence.
        # A populated pointer must resolve by modality, never by coincident ID
        # from a different source table or another video.
        if source_id is not None:
            modality = row.get("modality")
            if modality not in sources or source_id not in sources[modality]:
                _incomplete("Evidence source does not resolve within its video and modality")


def _check_global_references(snapshot):
    videos = {video["video_id"] for video in snapshot["videos"]}
    atoms = {}
    for video in snapshot["videos"]:
        for atom in video["tables"]["knowledge_atom"]:
            if atom["id"] in atoms:
                _invalid("Atom IDs must be unique across the entire snapshot")
            atoms[atom["id"]] = atom
    clusters = {row["cluster_id"]: row for row in snapshot["globals"]["knowledge_cluster"]}
    conflicts = {row["conflict_id"]: row for row in snapshot["globals"]["conflict_case"]}
    conflict_atoms = {}
    for conflict_id, conflict in conflicts.items():
        combined = set()
        for column in ("side_a_json", "side_b_json"):
            side = _json(conflict, column) or {}
            refs = _refs(side.get("atoms"), "Conflict side atoms")
            if not set(refs) <= atoms.keys():
                _incomplete("Conflict refers to an atom outside the snapshot")
            side_video = side.get("video")
            if side_video is not None:
                if type(side_video) is not str or side_video not in videos:
                    _incomplete("Conflict side refers to a video outside the snapshot")
                # A side may contain several atoms; judge uses a representative
                # video's evidence rather than a separate video for every atom.
                if refs and side_video not in {atoms[ref]["video_id"] for ref in refs}:
                    _incomplete("Conflict side video does not match its atom evidence")
            combined.update(refs)
        conflict_atoms[conflict_id] = combined
        _check_derivation_references(conflict, combined)
    memberships = {}
    for cluster_id, cluster in clusters.items():
        members = set(_refs(_json(cluster, "members_json"), "Cluster members"))
        if not members <= atoms.keys():
            _incomplete("Cluster refers to an atom outside the snapshot")
        _check_derivation_references(cluster, members)
        for atom_id in members:
            if atom_id in memberships and memberships[atom_id] != cluster_id:
                _incomplete("An atom belongs to multiple clusters")
            memberships[atom_id] = cluster_id
            if atoms[atom_id].get("cluster_id") != cluster_id:
                _incomplete("Cluster membership disagrees with the atom cluster pointer")
        linked = cluster.get("linked_conflict")
        if linked is not None:
            if linked not in conflicts:
                _incomplete("Cluster refers to a conflict outside the snapshot")
            if not conflict_atoms[linked] <= members:
                _incomplete("Linked conflict members do not belong to their cluster")
    for atom_id, atom in atoms.items():
        cluster_id = atom.get("cluster_id")
        if cluster_id is not None and (cluster_id not in clusters or memberships.get(atom_id) != cluster_id):
            _incomplete("Atom cluster pointer has no matching cluster membership")


def _check_derivation_references(row, members):
    from .derivation import validate_binding
    try:
        binding = validate_binding(row.get("derivation_json"))
    except ValueError:
        _invalid("Relation derivation binding is malformed")
    # A historical fingerprint can legitimately differ from current content.
    # Its input identities must still cover exactly the relation's members.
    if binding is not None and {item["atom_id"] for item in binding["inputs"]} != members:
        _incomplete("Relation derivation inputs do not match its members")


def validate_snapshot(payload):
    """Return a normalized deep copy after validating the full knowledge graph.

    Explicit JSON reference fields are OCR ``frame_ids_json``, cluster
    ``members_json``, and conflict ``side_a_json``/``side_b_json`` ``atoms`` and
    optional representative ``video``, and derivation input atom IDs. Other JSON fields contain descriptions,
    conditions, parameters or analysis, not cross-entity references.
    """
    _exact(payload, ("publication_id", "version", "videos", "globals", "reports"), "snapshot")
    _uuid(payload["publication_id"], "publication_id")
    _version(payload["version"])
    if type(payload["videos"]) is not list or len(payload["videos"]) > MAX_VIDEOS:
        _invalid("Snapshot videos must be a bounded list")
    if type(payload["reports"]) is not list or len(payload["reports"]) > MAX_REPORTS:
        _invalid("Snapshot reports must contain at most five entries")
    _exact(payload["globals"], GLOBAL_TABLES, "global tables")
    result = json.loads(_encoded(payload))
    seen = set()
    for video in result["videos"]:
        _exact(video, ("video_id", "media", "tables"), "video closure")
        video_id = protocol.validate_video_id(video["video_id"])
        if video_id in seen:
            _invalid("Snapshot contains duplicate videos")
        seen.add(video_id)
        descriptor = protocol.validate_media_descriptor(video["media"])
        if descriptor["video_id"] != video_id:
            _invalid("Media descriptor belongs to a different video")
        _exact(video["tables"], VIDEO_TABLES, "video tables")
        for table in VIDEO_TABLES:
            video["tables"][table] = _normalize_rows(table, video["tables"][table], video_id=video_id)
        assets = video["tables"]["video_asset"]
        if len(assets) != 1:
            _invalid("Every video closure must contain exactly one video asset")
        if assets[0].get("status") != "processed":
            _incomplete("Snapshot video assets must be fully processed")
        if assets[0].get("content_sha256") != descriptor["sha256"]:
            _invalid("Video content digest does not match its media descriptor")
        if assets[0].get("source_type") == "local_file" and assets[0].get("source_url") is not None:
            _invalid("Local-file video sources cannot supply a source URL")
        _check_local_references(video)
    for table in GLOBAL_TABLES:
        result["globals"][table] = _normalize_rows(table, result["globals"][table])
    _check_global_references(result)
    result["videos"].sort(key=lambda video: video["video_id"])
    if all(type(report) is dict and type(report.get("name")) is str for report in result["reports"]):
        result["reports"].sort(key=lambda report: (report["name"], _encoded(report)))
    # Canonicalizing JSON strings can alter encoding length. Enforce the limit
    # on the returned form too; report fields otherwise remain untouched.
    _encoded(result)
    return result


def _wire_rows(con, table, *, video_id=None, budget):
    columns = protocol.TABLES[table]["columns"]
    selected = ",".join(f'"{name}"' for name in columns)
    where, params = "", ()
    if table == "meta":
        prefix = f"step:{video_id}:"
        # LIKE would treat underscores in IDs as wildcards and cross scopes.
        where, params = " WHERE substr(key,1,?)=?", (len(prefix), prefix)
    elif video_id is not None:
        where, params = " WHERE video_id=?", (video_id,)
    order = ",".join(f'"{name}"' for name in protocol.TABLES[table]["primary_key"])
    cursor = con.execute(f'SELECT {selected} FROM "{table}"{where} ORDER BY {order}', params)
    result = []
    for values in cursor:
        if len(result) >= protocol.MAX_ROWS:
            _invalid("Snapshot table exceeds its row limit", status=413)
        row = dict(zip(columns, values))
        # Do not add a NULL key to pre-P04 payloads: the frozen outbox and
        # committed publication hash must retain their existing wire shape.
        if table in GLOBAL_TABLES and row.get("derivation_json") is None:
            row.pop("derivation_json", None)
        if table == "frame":
            row["path"] = None
        elif table == "video_asset" and row.get("source_type") == "local_file":
            row["source_url"] = None
        row = _normalize_rows(table, [row], video_id=video_id)[0]
        budget[0] += len(_encoded(row)) + 1
        if budget[0] > MAX_SNAPSHOT_BYTES:
            _invalid("Snapshot exceeds its byte limit", status=413)
        result.append(row)
    return result


def export_snapshot(con, *, publication_id, media, reports=None, recovery_projection=False):
    """Use strict full export, or an explicit processed recovery projection."""
    if type(recovery_projection) is not bool:
        _invalid('Recovery projection flag must be boolean')
    if recovery_projection:
        from .sync_recovery_projection import processed_view
        with processed_view(con):
            return _export_snapshot(con, publication_id=publication_id, media=media, reports=reports)
    return _export_snapshot(con, publication_id=publication_id, media=media, reports=reports)


def _export_snapshot(con, *, publication_id, media, reports=None):
    """Export all ``processed`` videos within the caller's consistent read txn.

    The caller supplies already checked media descriptors; the later media
    stage verifies actual file bytes. Missing descriptors cannot silently drop
    a completed video. In-progress videos with no atoms may be omitted, while
    stale atoms from an interrupted rerun make the generation unpublishable.
    """
    if not con.in_transaction:
        raise SyncError("Snapshot export requires a caller-owned consistent read transaction",
                        "SYNC_READ_TRANSACTION_REQUIRED", 409)
    _uuid(publication_id, "publication_id")
    if not isinstance(media, Mapping):
        _invalid("Media descriptors must be keyed by video_id")
    for video_id, descriptor in media.items():
        protocol.validate_video_id(video_id)
        if protocol.validate_media_descriptor(descriptor)["video_id"] != video_id:
            _invalid("Media mapping key does not match its descriptor")
    incomplete = con.execute(
        "SELECT 1 FROM knowledge_atom a LEFT JOIN video_asset v ON v.video_id=a.video_id "
        "WHERE v.video_id IS NULL OR v.status IS NULL OR v.status<>'processed' LIMIT 1").fetchone()
    if incomplete:
        _incomplete("Unprocessed or missing videos still own knowledge atoms")
    video_ids = [row[0] for row in con.execute(
        "SELECT video_id FROM video_asset WHERE status='processed' ORDER BY video_id LIMIT ?", (MAX_VIDEOS + 1,))]
    if len(video_ids) > MAX_VIDEOS:
        _invalid("Snapshot videos exceed their count limit", status=413)
    if set(video_ids) - media.keys():
        _incomplete("Processed videos are missing verified media descriptors")
    try:
        version = db.knowledge_version(con)
    except (KeyError, TypeError, ValueError):
        _invalid("Source knowledge version is missing or invalid")
    _version(version)
    payload = {"publication_id": publication_id, "version": version, "videos": [],
               "globals": {}, "reports": [] if reports is None else reports}
    if type(payload["reports"]) is not list or len(payload["reports"]) > MAX_REPORTS:
        _invalid("Snapshot reports must contain at most five entries")
    budget = [len(_encoded(payload))]
    for video_id in video_ids:
        tables = {table: _wire_rows(con, table, video_id=video_id, budget=budget) for table in VIDEO_TABLES}
        payload["videos"].append({"video_id": video_id, "media": media[video_id], "tables": tables})
    payload["globals"] = {table: _wire_rows(con, table, budget=budget) for table in GLOBAL_TABLES}
    return validate_snapshot(payload)
