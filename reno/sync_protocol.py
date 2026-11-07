"""Closed, bounded wire contract for knowledge sync; never writes a database.

The allowlist is independent of introspection: a future database column cannot
silently become a sync field. Personal data, queue ownership, identities and
filesystem paths are deliberately outside this contract. Callers must still
validate closure references and existing row ownership inside their transaction.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

from . import migrations

PROTOCOL_VERSION = 3
SOURCE_CONTROL_CONTINUITY_VERSION = 1
SOURCE_READMIT_VERSION = 2
SOURCE_DISPOSE_VERSION = 2
MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_ROWS = 10_000
MAX_CELL_BYTES = 256 * 1024
MAX_ROW_BYTES = 512 * 1024
MAX_BATCH_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 16_384
MAX_URL_LENGTH = 4096
MEDIA_EXTENSIONS = ("mp4", "mkv", "webm", "mov", "avi", "flv", "m4v")
STEP_NAMES = ("media", "asr", "ocr", "vlm", "atomize")


class SyncError(ValueError):
    def __init__(self, message, code="INVALID_SYNC_REQUEST", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _table(columns, primary_key, scope, mode, *, logical_key=None, server_allocated=()):
    return MappingProxyType({
        "columns": tuple(columns.split()), "primary_key": tuple(primary_key),
        "logical_key": tuple(logical_key if logical_key is not None else primary_key),
        "server_allocated": tuple(server_allocated), "scope": scope, "mode": mode,
    })


# primary_key is the actual SQL key, not a caller-supplied conflict target.
# Replacement outputs keep their video-scoped keys except evidence_ref.id,
# which is allocated by the receiving server. user_decision also allocates its
# local decision_id, but merges globally by its UNIQUE event_id instead.
TABLES = MappingProxyType({
    "video_asset": _table(
        "video_id source_platform source_type source_url title author source_published_at "
        "duration_ms width height fps content_sha256 audio_sha256 imported_at status",
        ("video_id",), "video", "upsert"),
    "knowledge_atom": _table(
        "id video_id category stage space subject claim reason risk_if_ignored polarity "
        "conditions_json parameters_json confidence cluster_id model prompt_version created_at "
        "dimension evidence_nature exceptions prices_json", ("id",), "video", "upsert"),
    "evidence_ref": _table(
        "atom_id video_id modality source_item_id start_ms end_ms evidence_text weight",
        ("id",), "video", "replace", logical_key=(), server_allocated=("id",)),
    "transcript_segment": _table(
        "id video_id idx start_ms end_ms text", ("video_id", "id"), "video", "replace"),
    "ocr_span": _table(
        "id video_id start_ms end_ms text conf frame_ids_json",
        ("video_id", "id"), "video", "replace"),
    "frame": _table(
        "frame_id video_id pts_ms origin dhash path", ("video_id", "frame_id"), "video", "replace"),
    "visual_observation": _table(
        "id video_id frame_id pts_ms scene_description materials_json tools_json subtitle_text measurement",
        ("video_id", "id"), "video", "replace"),
    "knowledge_cluster": _table(
        "cluster_id canonical_topic relation judge_reason members_json linked_conflict created_at derivation_json",
        ("cluster_id",), "global", "replace"),
    "conflict_case": _table(
        "conflict_id ctype side_a_json side_b_json analysis_json recommended_action created_at derivation_json",
        ("conflict_id",), "global", "replace"),
    "user_decision": _table(
        "atom_id conflict_id action revised_claim note created_at event_id client_id decided_at "
        "target_fingerprint target_snapshot_json event_schema_version",
        ("decision_id",), "global", "append", logical_key=("event_id",),
        server_allocated=("decision_id",)),
    "meta": _table("key value", ("key",), "step", "replace"),
})

_INTEGER_COLUMNS = {
    "video_asset": ("duration_ms", "width", "height"),
    "transcript_segment": ("idx", "start_ms", "end_ms"),
    "ocr_span": ("start_ms", "end_ms"), "frame": ("pts_ms",),
    "visual_observation": ("pts_ms",), "evidence_ref": ("start_ms", "end_ms"),
    "user_decision": ("event_schema_version",),
}
_REAL_COLUMNS = {
    "video_asset": ("fps",), "ocr_span": ("conf",),
    "knowledge_atom": ("confidence",), "evidence_ref": ("weight",),
}
# Known local-only SQL columns are checked and fingerprinted, never accepted on
# the wire. sync_state is a local readiness fence added by schema 6.
_LOCAL_COLUMNS = {
    "video_asset": {"files_json": "TEXT", "sync_state": "TEXT"},
    "knowledge_atom": {"status": "TEXT"}, "conflict_case": {"status": "TEXT"},
    "evidence_ref": {"id": "INTEGER"}, "user_decision": {"decision_id": "INTEGER"},
}
_JSON_ROOTS = {
    "conditions_json": (dict,), "parameters_json": (list,), "exceptions": (list,),
    "prices_json": (list,), "frame_ids_json": (list,), "materials_json": (list,),
    "tools_json": (list,), "members_json": (list,), "side_a_json": (dict,),
    "side_b_json": (dict,), "analysis_json": (dict,), "target_snapshot_json": (dict,),
    "derivation_json": (dict,),
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VIDEO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
_DEVICE_NAMES.update(f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10))


def _fail(message, *, code="INVALID_SYNC_REQUEST", status=400):
    raise SyncError(message, code, status)


def _utf8_size(value):
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        _fail("Sync text contains invalid Unicode")


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("Sync payload is not bounded JSON")


def _schema_error(message):
    _fail(message, code="INCOMPATIBLE_SYNC_SCHEMA", status=409)


def schema_rules():
    """Return the actual closed descriptor used by the handshake fingerprint.

    This is a detached representation, not proof that an optional runtime
    capability has been activated. Readers cannot mutate protocol constants.
    """
    rules = {
        "claim_origin_version": 1,
        "recovery_fence_version": 1,
        "derivation_binding_version": 1,
        "upload_intent_version": 1,
        "source_control_continuity_version": SOURCE_CONTROL_CONTINUITY_VERSION,
        "source_readmit_version": SOURCE_READMIT_VERSION,
        "source_dispose_version": SOURCE_DISPOSE_VERSION,
        "json_roots": {key: [kind.__name__ for kind in kinds] for key, kinds in _JSON_ROOTS.items()},
        "integer_columns": _INTEGER_COLUMNS, "real_columns": _REAL_COLUMNS,
        "local_columns": _LOCAL_COLUMNS, "step_names": STEP_NAMES,
        "media_extensions": MEDIA_EXTENSIONS,
        "limits": [MAX_MEDIA_BYTES, MAX_ROWS, MAX_CELL_BYTES, MAX_ROW_BYTES,
                   MAX_BATCH_BYTES, MAX_JSON_DEPTH, MAX_JSON_NODES, MAX_URL_LENGTH],
    }
    return json.loads(_json_bytes(rules))


def schema_handshake(con):
    """Read and fingerprint core SQL structure plus the explicit wire contract.

    Column order, index names and personal/operational tables do not affect
    compatibility. Additional core columns do affect the hash, but never the
    allowlist. This works with either sqlite.Row or the default tuple factory.
    No metadata, table or identity is initialized or changed here.
    """
    try:
        version_row = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()
        if version_row is None or str(version_row[0]) != str(migrations.SCHEMA_VERSION):
            _schema_error("Database needs an explicit supported migration before sync")
        schema = {}
        for table, spec in TABLES.items():
            # Identifiers come exclusively from the fixed contract above.
            rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = {r[1]: {"name": r[1], "type": str(r[2]).upper(),
                               "notnull": r[3], "default": r[4], "pk": r[5]} for r in rows}
            expected = {name: "TEXT" for name in spec["columns"]}
            expected.update({name: "INTEGER" for name in _INTEGER_COLUMNS.get(table, ())})
            expected.update({name: "REAL" for name in _REAL_COLUMNS.get(table, ())})
            expected.update(_LOCAL_COLUMNS.get(table, {}))
            if any(name not in columns or columns[name]["type"] != datatype
                   for name, datatype in expected.items()):
                _schema_error("Core sync table is missing a column or has an incompatible type")
            actual_pk = tuple(c["name"] for c in sorted(columns.values(), key=lambda c: c["pk"])
                              if c["pk"])
            if actual_pk != spec["primary_key"]:
                _schema_error("Core sync table has an incompatible primary key")
            unique_keys = []
            for index in con.execute(f'PRAGMA index_list("{table}")'):
                if not index[2]:
                    continue
                # index names are DB data, so quote them instead of interpolating
                # unescaped. Include partial uniqueness in the hash and refuse
                # to mistake it for the global event/content identity guarantee.
                quoted_name = str(index[1]).replace('"', '""')
                key = tuple(r[2] for r in con.execute(f'PRAGMA index_info("{quoted_name}")'))
                unique_keys.append({"columns": key, "partial": bool(index[4])})
            required_unique = {"video_asset": ("content_sha256",), "user_decision": ("event_id",)}.get(table)
            if required_unique and {"columns": required_unique, "partial": False} not in unique_keys:
                _schema_error("Core sync table is missing a global uniqueness constraint")
            schema[table] = {
                "columns": sorted(columns.values(), key=lambda c: c["name"]),
                "unique_keys": sorted(unique_keys, key=lambda k: _json_bytes(k)),
                "foreign_keys": sorted(tuple(r) for r in con.execute(f'PRAGMA foreign_key_list("{table}")')),
            }
    except sqlite3.DatabaseError as exc:
        raise SyncError("Unable to inspect the sync schema", "INCOMPATIBLE_SYNC_SCHEMA", 409) from exc
    contract = {table: dict(spec) for table, spec in TABLES.items()}
    rules = schema_rules()
    digest = hashlib.sha256(_json_bytes({"schema": schema, "tables": contract, "rules": rules})).hexdigest()
    return {"protocol_version": PROTOCOL_VERSION, "migration_version": migrations.SCHEMA_VERSION,
            "core_schema_sha256": digest}


def validate_handshake(remote, local):
    required = {"protocol_version", "migration_version", "core_schema_sha256"}
    for handshake in (remote, local):
        if type(handshake) is not dict or set(handshake) != required:
            _fail("Handshake must contain exactly the protocol, migration and core schema versions")
        if any(type(handshake[key]) is not int or handshake[key] < 1
               for key in ("protocol_version", "migration_version")):
            _fail("Handshake versions must be positive integers")
        if not isinstance(handshake["core_schema_sha256"], str) or not _SHA256.fullmatch(handshake["core_schema_sha256"]):
            _fail("Handshake core schema digest is invalid")
    if (local["protocol_version"] != PROTOCOL_VERSION
            or local["migration_version"] != migrations.SCHEMA_VERSION or remote != local):
        _fail("Sync protocol or core schema does not match", code="INCOMPATIBLE_SYNC_SCHEMA", status=409)


def validate_video_id(value):
    if not isinstance(value, str) or not _VIDEO_ID.fullmatch(value) or value.upper() in _DEVICE_NAMES:
        _fail("video_id must be a safe platform identifier")
    return value


def validate_ingest_url(value):
    """Accept supported public platform URLs, without resolving or requesting.

    This checks the submitted URL only. Downloaders must apply the same policy
    after redirects; a permitted short URL is not proof of its final target.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        _fail("A bounded public platform URL is required")
    # urlsplit strips certain controls before parsing; reject them first.
    decoded = value
    for _ in range(16):
        if any(ord(c) <= 32 or 127 <= ord(c) <= 159 or c.isspace() or c == "\\" for c in decoded):
            _fail("Platform URL contains a forbidden character")
        following = unquote(decoded)
        if following == decoded:
            break
        decoded = following
    else:
        _fail("Platform URL encoding is too deeply nested")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        _fail("Platform URL is malformed")
    if (parsed.scheme not in ("http", "https") or not host or parsed.username is not None
            or parsed.password is not None or parsed.fragment or not parsed.netloc
            or parsed.netloc.endswith(":")):
        _fail("Platform URL must use HTTP(S) without credentials or fragments")
    if port not in (None, 80 if parsed.scheme == "http" else 443):
        _fail("Platform URL must use its default HTTP(S) port")
    if len(host) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
        _fail("Platform URL hostname is invalid")
    if not (host == "b23.tv" or any(host == root or host.endswith("." + root)
                                   for root in ("bilibili.com", "douyin.com"))):
        _fail("URL does not belong to a supported public video platform")
    _utf8_size(value)
    return value


def validate_media_descriptor(value):
    required = {"video_id", "sha256", "byte_size", "extension"}
    if type(value) is not dict or set(value) != required:
        _fail("Media descriptor fields are invalid")
    validate_video_id(value["video_id"])
    if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
        _fail("Media sha256 must be a lowercase SHA-256 digest")
    if type(value["byte_size"]) is not int or not 0 < value["byte_size"] <= MAX_MEDIA_BYTES:
        _fail("Media byte_size must be between 1 byte and 512 MiB")
    if not isinstance(value["extension"], str) or value["extension"] not in MEDIA_EXTENSIONS:
        _fail("Media extension is unsupported")
    return dict(value)


def _reject_constant(_value):
    _fail("JSON contains a non-finite number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate object key")
        result[key] = value
    return result


def _validate_json(value, column):
    try:
        root = json.loads(value, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, SyncError):
            raise
        _fail("Sync JSON field is malformed or too deeply nested")
    if root is None and column != "target_snapshot_json":
        return
    if not isinstance(root, _JSON_ROOTS[column]):
        _fail("Sync JSON field has an invalid root type")
    count, pending = 0, [(root, 0)]
    while pending:
        current, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _fail("Sync JSON field exceeds its structural limit")
        if isinstance(current, dict):
            for key, child in current.items():
                _utf8_size(key)
                if "\x00" in key:
                    _fail("Sync JSON key contains a null byte")
                pending.append((child, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
        elif isinstance(current, float) and not math.isfinite(current):
            _fail("JSON contains a non-finite number")
        elif isinstance(current, str):
            _utf8_size(current)
            if "\x00" in current:
                _fail("Sync JSON text contains a null byte")


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail("Sync row contains an invalid identifier")


def _validate_cell(table, column, value):
    if value is None:
        return
    if column in _INTEGER_COLUMNS.get(table, ()):
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            _fail("Sync integer fields must be nonnegative SQLite integers")
    elif column in _REAL_COLUMNS.get(table, ()):
        # sqlite3 binds Python integers as INTEGER even for a REAL column.
        if (type(value) not in (int, float) or (type(value) is int and value > 2**63 - 1)
                or not 0 <= value <= 1.7976931348623157e308
                or not math.isfinite(value)):
            _fail("Sync numeric fields must be finite nonnegative numbers")
    else:
        if not isinstance(value, str) or _utf8_size(value) > MAX_CELL_BYTES or "\x00" in value:
            _fail("Sync text field is invalid or exceeds its size limit")
        if column in _JSON_ROOTS:
            _validate_json(value, column)
            if column == "derivation_json":
                from .derivation import validate_binding
                try:
                    validate_binding(value)
                except ValueError:
                    _fail("Sync relation derivation binding is invalid")
        elif column in ("id", "atom_id", "conflict_id", "cluster_id", "frame_id", "source_item_id", "linked_conflict"):
            _identifier(value)
        elif column in ("content_sha256", "audio_sha256") and not _SHA256.fullmatch(value):
            _fail("Sync content hash must be a lowercase SHA-256 digest")


def validate_rows(table, rows, *, video_id=None):
    """Validate one table batch and return detached, SQLite-bindable row dicts.

    JSON columns stay JSON strings: a nested wire object cannot masquerade as a
    SQL value. A video scope constrains every row and step marker. Global tables
    need their separate endpoint and cannot be included under a video scope.
    Existing ownership of stable atom IDs is checked by the importer, not here.
    """
    if not isinstance(table, str) or table not in TABLES:
        _fail("Table is not permitted in knowledge sync")
    spec = TABLES[table]
    if video_id is not None:
        validate_video_id(video_id)
        if spec["scope"] == "global":
            _fail("Global sync tables cannot be written in a video closure")
    if type(rows) is not list or len(rows) > MAX_ROWS:
        _fail("Sync rows must be a bounded array")
    result, seen, batch_size = [], set(), 2
    allowed = set(spec["columns"])
    for row in rows:
        if type(row) is not dict or not row or not set(row) <= allowed:
            _fail("Sync row contains unknown or forbidden columns")
        required = set(spec["logical_key"])
        if spec["scope"] == "video":
            required.add("video_id")
        if table == "evidence_ref":
            required.add("atom_id")
        if not required <= row.keys() or any(row[key] is None for key in required):
            _fail("Sync row is missing a required identity or scope")
        for column, value in row.items():
            _validate_cell(table, column, value)
        if spec["scope"] == "video":
            validate_video_id(row["video_id"])
            if video_id is not None and row["video_id"] != video_id:
                _fail("Sync row belongs to a different video")
        if table == "meta":
            parts = row["key"].split(":")
            if len(parts) != 3 or parts[0] != "step" or parts[2] not in STEP_NAMES:
                _fail("Only known per-video step markers can be synchronized")
            validate_video_id(parts[1])
            if video_id is not None and parts[1] != video_id:
                _fail("Sync step marker belongs to a different video")
        if table == "video_asset" and row.get("source_url"):
            validate_ingest_url(row["source_url"])
        if table == "frame" and row.get("path") is not None:
            _fail("Frame paths must be null in knowledge sync")
        if table == "user_decision":
            try:
                if str(uuid.UUID(row["event_id"])) != row["event_id"]:
                    raise ValueError("noncanonical UUID")
            except (ValueError, TypeError, AttributeError):
                _fail("Sync event_id must be a canonical UUID")
            if row.get("event_schema_version") not in (0, 1):
                _fail("Sync event schema is unsupported", code="UNSUPPORTED_EVENT_SCHEMA")
        start, end = row.get("start_ms"), row.get("end_ms")
        if start is not None and end is not None and end < start:
            _fail("Sync time interval ends before it starts")
        if spec["logical_key"]:
            key = tuple(row[column] for column in spec["logical_key"])
            if key in seen:
                _fail("Sync batch contains duplicate logical keys")
            seen.add(key)
        size = len(_json_bytes(row))
        batch_size += size + 1
        if size > MAX_ROW_BYTES or batch_size > MAX_BATCH_BYTES:
            _fail("Sync rows exceed the payload size limit", status=413)
        result.append(dict(row))
    return result
