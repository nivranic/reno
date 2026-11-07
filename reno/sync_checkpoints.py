"""Immutable, knowledge-only preimages registered with a publication transaction.

The archive is a freshly constructed SQLite database, never a backup of the
receiver. Its closed tables cannot carry private data, queues, review events or
credentials. Files without a committed sync_checkpoint/publication join are
orphans and are deliberately never enumerated as recovery points.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import config, db, sync_protocol as protocol, sync_reports, storage_admin, storage_artifacts, storage_lifecycle
from .sync_media import _Directory, _rename_new, _verify_stream
from .sync_protocol import SyncError

FORMAT_VERSION = 1
RECOVERY_FORMAT_VERSION = 2
ARCHIVE_SCHEMA_VERSION = 9
SUPPORTED_SCHEMA_VERSIONS = frozenset({7, 9})
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")

# Independent closed lists: adding a live DB column never exports it here.
KNOWLEDGE_COLUMNS_V7 = {
    "video_asset": "video_id source_platform source_type source_url title author source_published_at duration_ms width height fps content_sha256 audio_sha256 imported_at files_json status sync_state",
    "frame": "frame_id video_id pts_ms origin dhash path",
    "transcript_segment": "id video_id idx start_ms end_ms text",
    "ocr_span": "id video_id start_ms end_ms text conf frame_ids_json",
    "visual_observation": "id video_id frame_id pts_ms scene_description materials_json tools_json subtitle_text measurement",
    "knowledge_atom": "id video_id category stage space subject claim reason risk_if_ignored polarity conditions_json parameters_json confidence status cluster_id model prompt_version created_at dimension evidence_nature exceptions prices_json",
    "evidence_ref": "id atom_id video_id modality source_item_id start_ms end_ms evidence_text weight",
    "knowledge_cluster": "cluster_id canonical_topic relation judge_reason members_json linked_conflict created_at",
    "conflict_case": "conflict_id ctype status side_a_json side_b_json analysis_json recommended_action created_at",
    "meta": "key value",
    "sync_report": "name publication_id storage_key sha256 byte_size policy_version source_version_json local_version_json generated_at review_events_sha256",
}
KNOWLEDGE_COLUMNS_V7 = {name: tuple(columns.split()) for name, columns in KNOWLEDGE_COLUMNS_V7.items()}
KNOWLEDGE_COLUMNS = {
    name: columns + (("derivation_json",) if name in ("knowledge_cluster", "conflict_case") else ())
    for name, columns in KNOWLEDGE_COLUMNS_V7.items()
}


def columns_for_schema(schema_version):
    """Frozen archive contracts, independent of the live operational schema."""
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _fail("Checkpoint contains an unsupported knowledge schema", "SYNC_CHECKPOINT_DAMAGED")
    return KNOWLEDGE_COLUMNS_V7 if schema_version == 7 else KNOWLEDGE_COLUMNS


_EXTRA_DDL = {
    "checkpoint_manifest": "slot INTEGER PRIMARY KEY, manifest_json TEXT NOT NULL",
    "checkpoint_media": "video_id TEXT PRIMARY KEY, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL, extension TEXT NOT NULL",
}
_ENTRY_KEYS = {"checkpoint_id", "publication_id", "receiver_version_json", "format_version",
               "schema_version", "storage_key", "sha256", "byte_size", "created_at"}
_RECOVERY_ENTRY_KEYS = (_ENTRY_KEYS - {"publication_id"}) | {"operation_id", "origin_kind"}
_INTEGER = {
    "video_asset": {"duration_ms", "width", "height"}, "frame": {"pts_ms"},
    "transcript_segment": {"idx", "start_ms", "end_ms"}, "ocr_span": {"start_ms", "end_ms"},
    "visual_observation": {"pts_ms"}, "evidence_ref": {"id", "start_ms", "end_ms"},
    "sync_report": {"byte_size"},
}
_REAL = {"video_asset": {"fps"}, "ocr_span": {"conf"}, "knowledge_atom": {"confidence"}, "evidence_ref": {"weight"}}
_PRIMARY_KEYS = {
    "video_asset": ("video_id",), "frame": ("video_id", "frame_id"),
    "transcript_segment": ("video_id", "id"), "ocr_span": ("video_id", "id"),
    "visual_observation": ("video_id", "id"), "knowledge_atom": ("id",),
    "evidence_ref": ("id",), "knowledge_cluster": ("cluster_id",),
    "conflict_case": ("conflict_id",), "meta": ("key",), "sync_report": ("name",),
}


def _fail(message, code="SYNC_CHECKPOINT_INVALID", status=409):
    raise SyncError(message, code, status)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _fail("Checkpoint identifiers must be canonical UUIDs")
    return value


def _version(value):
    if (type(value) is not dict or set(value) != {"library_id", "library_epoch", "kb_revision"}
            or type(value["library_epoch"]) is not int or value["library_epoch"] < 1
            or type(value["kb_revision"]) is not int or value["kb_revision"] < 0):
        _fail("Checkpoint receiver version is invalid")
    _uuid(value["library_id"])
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _root(con, root=None):
    if root is None:
        path = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
        if not path:
            _fail("Checkpoint storage requires a persistent receiver database")
        root = Path(path).parent / "sync-checkpoints"
    value = Path(os.path.abspath(os.fspath(root)))
    if len(value.parts) < 3 or ".." in Path(root).parts:
        _fail("Checkpoint storage requires a dedicated absolute directory")
    return value


def _node_id(con):
    path = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not path:
        _fail("Checkpoint needs a persistent receiver identity")
    directory_path = Path(path).parent
    try:
        with _Directory(directory_path, base=directory_path) as directory:
            with directory.open_file("node-identity.json") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                _fail("Receiver node identity is invalid", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
            value = json.loads(raw)
            if type(value) is not dict or set(value) != {"instance_id"}:
                _fail("Receiver node identity is invalid", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
            directory.verify()
            return _uuid(value["instance_id"])
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, SyncError):
            raise
        _fail("Receiver node identity is unavailable", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")


def _media_root(media_store=None):
    # Do not instantiate MediaStore during read-only preview: its constructor
    # creates directories. Its existing root is the only property needed here.
    return Path(os.path.abspath(os.fspath(media_store.root if media_store is not None else config.MEDIA)))


def _report_root(report_root=None):
    if report_root is None:
        from . import report
        report_root = report.out_dir()
    return Path(os.path.abspath(os.fspath(report_root)))


def _write_transaction(con):
    if not config.is_cloud():
        _fail("Checkpoints require a cloud receiver", "SYNC_ROLE_REQUIRED", 403)
    if not con.in_transaction:
        _fail("Checkpoint creation requires an existing publication transaction")
    # Promote a deferred transaction without changing any rows or revisions.
    con.execute("UPDATE sync_checkpoint SET checkpoint_id=checkpoint_id WHERE 0")


def _profile(con, *, profile=None, root=None, media_store=None, report_root=None):
    return storage_admin.from_connection(con, profile=profile,
        media=media_store.root if media_store is not None else None,
        reports=report_root, checkpoints=root)


@contextmanager
def _read_transaction(con, profile):
    # An existing snapshot may predate collection. Never acquire a new lock
    # below it: only a caller that already protected its snapshot may proceed.
    if con.in_transaction:
        storage_lifecycle.require_guard(profile)
        yield
        return
    with storage_lifecycle.shared(profile):
        con.execute("BEGIN")
        try:
            yield
        finally:
            con.rollback()


def _assert_not_retired(con, entry):
    kind = "recovery" if entry["format_version"] == RECOVERY_FORMAT_VERSION else "publication"
    row = con.execute("SELECT storage_key,sha256,byte_size FROM sync_checkpoint_retirement "
                      "WHERE origin_kind=? AND checkpoint_id=?", (kind, entry["checkpoint_id"])).fetchone()
    if row is None:
        return
    if tuple(row) != tuple(entry[key] for key in ("storage_key", "sha256", "byte_size")):
        _fail("Checkpoint retirement descriptor does not match", "SYNC_CHECKPOINT_RETIREMENT_MISMATCH")
    _fail("Checkpoint was retired by the retention policy", "SYNC_CHECKPOINT_RETIRED", 410)


def _register_artifact(con, entry, profile):
    return storage_artifacts.register(con, profile=profile, namespace="checkpoint", root=profile.checkpoints,
        storage_key=entry["storage_key"], sha256=entry["sha256"], byte_size=entry["byte_size"])


def _ddl(schema_version=ARCHIVE_SCHEMA_VERSION):
    # Both publication/recovery formats use the archive's own column version.
    # New live columns must never change the DDL expected from a schema-7 file.
    result = {}
    for table, columns in columns_for_schema(schema_version).items():
        definitions = []
        for name in columns:
            datatype = "INTEGER" if name in _INTEGER.get(table, ()) else "REAL" if name in _REAL.get(table, ()) else "TEXT"
            definitions.append(f'"{name}" {datatype}')
        definitions.append("PRIMARY KEY (" + ",".join(f'"{name}"' for name in _PRIMARY_KEYS[table]) + ")")
        result[table] = f'CREATE TABLE "{table}" ({",".join(definitions)})'
    for table, declaration in _EXTRA_DDL.items():
        result[table] = f'CREATE TABLE "{table}" ({declaration})'
    return result


def _archive_connection():
    if not all(callable(getattr(sqlite3.Connection, name, None)) for name in ("serialize", "deserialize")):
        _fail("Checkpoint storage requires Python 3.11+ with SQLite serialization support",
              "SYNC_CHECKPOINT_RUNTIME_UNSUPPORTED", 503)
    con = sqlite3.connect(":memory:")
    try:
        # A custom SQLite build may expose methods but disable their runtime
        # implementation. Probe only a self-created, nonempty in-memory image.
        con.execute("CREATE TABLE runtime_probe(value INTEGER)")
        con.execute("INSERT INTO runtime_probe VALUES(1)")
        con.commit()
        con.deserialize(con.serialize())
        if con.execute("SELECT value FROM runtime_probe").fetchone()[0] != 1:
            raise sqlite3.NotSupportedError("SQLite serialization probe failed")
        con.execute("DROP TABLE runtime_probe")
        con.commit()
    except (AttributeError, sqlite3.Error):
        con.close()
        _fail("Checkpoint storage requires working SQLite serialization support",
              "SYNC_CHECKPOINT_RUNTIME_UNSUPPORTED", 503)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA page_size=4096")
    con.execute(f"PRAGMA max_page_count={MAX_ARCHIVE_BYTES // 4096}")
    con.execute("PRAGMA trusted_schema=OFF")
    return con


def _insert_rows(con, table, columns, rows):
    names = ",".join(f'"{column}"' for column in columns)
    con.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})', rows)


def _issue(code, target):
    return {"code": code, "target": target}


def _descriptor(asset, root):
    try:
        protocol.validate_video_id(asset["video_id"])
        files = json.loads(asset["files_json"] or "{}")
        digest = asset["content_sha256"]
        if not isinstance(digest, str) or not _SHA.fullmatch(digest) or type(files) is not dict:
            return None
        value = files.get("video")
        if not isinstance(value, str):
            return None
        prefix = f"{root.name}/sync/{asset['video_id']}/{digest}."
        if not value.startswith(prefix) or value[len(prefix):] not in protocol.MEDIA_EXTENSIONS:
            return None
        return {"video_id": asset["video_id"], "sha256": digest,
                "extension": value[len(prefix):], "storage_key": value[len(root.name) + 1:]}
    except (ValueError, TypeError):
        return None


def _capture_media(archive, root):
    issues = []
    for asset in archive.execute("SELECT video_id,files_json,content_sha256,sync_state FROM video_asset"):
        if asset["sync_state"] != "ready":
            continue
        value = _descriptor(asset, root)
        if value is None:
            issues.append(_issue("unsupported_media_reference", asset["video_id"]))
            continue
        path = root / value["storage_key"]
        try:
            with _Directory(path.parent, base=root) as directory:
                with directory.open_file(path.name) as stream:
                    value["byte_size"] = os.fstat(stream.fileno()).st_size
                    descriptor = {key: value[key] for key in ("video_id", "sha256", "extension", "byte_size")}
                    protocol.validate_media_descriptor(descriptor)
                    _verify_stream(stream, descriptor)
                directory.verify()
            columns = ("video_id", "storage_key", "sha256", "byte_size", "extension")
            _insert_rows(archive, "checkpoint_media", columns, [tuple(value[key] for key in columns)])
        except (OSError, SyncError):
            issues.append(_issue("media_unavailable", asset["video_id"]))
    return issues


def _reference_issues(archive, media_root, report_root, *, receiver=None, profile):
    storage_lifecycle.require_guard(profile)
    issues, warnings = [], []
    descriptors = {row["video_id"]: dict(row) for row in archive.execute("SELECT * FROM checkpoint_media")}
    for asset in archive.execute("SELECT video_id,files_json,content_sha256,sync_state FROM video_asset"):
        if asset["sync_state"] != "ready":
            warnings.append(_issue("video_not_ready", asset["video_id"]))
            continue
        expected = _descriptor(asset, media_root)
        value = descriptors.pop(asset["video_id"], None)
        if expected is None or value is None or any(value[key] != expected[key] for key in expected):
            issues.append(_issue("media_descriptor_missing_or_invalid", asset["video_id"]))
            continue
        try:
            descriptor = {key: value[key] for key in ("video_id", "sha256", "extension", "byte_size")}
            protocol.validate_media_descriptor(descriptor)
            path = media_root / value["storage_key"]
            with _Directory(path.parent, base=media_root) as directory:
                with directory.open_file(path.name) as stream:
                    _verify_stream(stream, descriptor)
                directory.verify()
        except (OSError, SyncError):
            issues.append(_issue("media_unavailable", asset["video_id"]))
    if descriptors:
        issues.append(_issue("unexpected_media_descriptor", "checkpoint_media"))
    for row in archive.execute("SELECT * FROM sync_report"):
        if receiver is not None and not receiver.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (row["publication_id"],)).fetchone():
            issues.append(_issue("report_publication_missing", row["name"]))
        value = {"report_type": row["name"], "content_file": row["storage_key"],
                 "content_sha256": row["sha256"], "byte_size": row["byte_size"]}
        try:
            if sync_reports.read_content(report_root, value, profile=profile) is None:
                issues.append(_issue("report_unavailable", row["name"]))
        except (OSError, SyncError, ValueError, TypeError):
            issues.append(_issue("report_unavailable", row["name"]))
    for table in ("knowledge_atom", "frame", "transcript_segment", "ocr_span", "visual_observation", "evidence_ref"):
        if archive.execute(f'SELECT 1 FROM "{table}" x LEFT JOIN video_asset v ON v.video_id=x.video_id '
                           "WHERE v.video_id IS NULL LIMIT 1").fetchone():
            issues.append(_issue("orphan_knowledge_reference", table))
    if archive.execute("SELECT 1 FROM evidence_ref e LEFT JOIN knowledge_atom a ON a.id=e.atom_id "
                       "WHERE a.id IS NULL OR a.video_id IS NOT e.video_id LIMIT 1").fetchone():
        issues.append(_issue("orphan_evidence_reference", "evidence_ref"))
    # Reuse the same complete graph checks used for an incoming publication;
    # timestamp-only legacy evidence remains valid, populated pointers must
    # resolve by video AND modality, and cluster/conflict links are reciprocal.
    from .sync_snapshot import _check_local_references, _check_global_references
    videos = []
    for asset in archive.execute("SELECT video_id FROM video_asset"):
        video = {"video_id": asset[0], "tables": {}}
        for table in ("knowledge_atom", "frame", "transcript_segment", "ocr_span", "visual_observation", "evidence_ref"):
            video["tables"][table] = [dict(row) for row in archive.execute(f'SELECT * FROM "{table}" WHERE video_id=?', (asset[0],))]
        videos.append(video)
        try:
            _check_local_references(video)
        except (SyncError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            issues.append(_issue("incomplete_video_knowledge_graph", asset[0]))
    try:
        _check_global_references({"videos": videos, "globals": {
            table: [dict(row) for row in archive.execute(f'SELECT * FROM "{table}"')]
            for table in ("knowledge_cluster", "conflict_case")}})
    except (SyncError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        issues.append(_issue("incomplete_global_knowledge_graph", "knowledge_graph"))
    return issues, warnings


def _entry(value):
    recovery = type(value) is dict and value.get("format_version") == RECOVERY_FORMAT_VERSION
    expected = _RECOVERY_ENTRY_KEYS if recovery else _ENTRY_KEYS
    if type(value) is not dict or set(value) != expected:
        _fail("Checkpoint registration fields are invalid")
    _uuid(value["checkpoint_id"])
    _uuid(value["operation_id"] if recovery else value["publication_id"])
    if recovery and value["origin_kind"] != "recovery":
        _fail("Recovery checkpoint origin is invalid")
    if (value["format_version"] not in (FORMAT_VERSION, RECOVERY_FORMAT_VERSION) or type(value["format_version"]) is not int
            or type(value["schema_version"]) is not int or value["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS
            or value["storage_key"] != value["checkpoint_id"] + ".sqlite"
            or not isinstance(value["sha256"], str) or not _SHA.fullmatch(value["sha256"])
            or type(value["byte_size"]) is not int or not 0 < value["byte_size"] <= MAX_ARCHIVE_BYTES):
        _fail("Checkpoint archive descriptor is invalid")
    try:
        _version(json.loads(value["receiver_version_json"]))
        stamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _fail("Checkpoint identity metadata is invalid")
    return value


def _readonly_authorizer(action, arg1, arg2, database, source):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def _decode(data, entry, *, configure_read=None):
    if len(data) != entry["byte_size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        _fail("Checkpoint archive checksum or size differs", "SYNC_CHECKPOINT_DAMAGED")
    archive = _archive_connection()
    try:
        if configure_read is not None:
            configure_read(archive)
        archive.deserialize(data)
        archive.execute("PRAGMA trusted_schema=OFF")
        archive.execute("PRAGMA query_only=ON")
        expected = _ddl(entry["schema_version"])
        objects = archive.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if (len(objects) != len(expected) or any(row["type"] != "table" or expected.get(row["name"]) != row["sql"] for row in objects)):
            _fail("Checkpoint contains an unsupported schema", "SYNC_CHECKPOINT_DAMAGED")
        if [row[0] for row in archive.execute("PRAGMA integrity_check")] != ["ok"]:
            _fail("Checkpoint SQLite integrity check failed", "SYNC_CHECKPOINT_DAMAGED")
        manifests = archive.execute("SELECT slot,manifest_json FROM checkpoint_manifest").fetchall()
        if len(manifests) != 1 or manifests[0]["slot"] != 1:
            _fail("Checkpoint manifest is missing", "SYNC_CHECKPOINT_DAMAGED")
        manifest = json.loads(manifests[0]["manifest_json"])
        origin_keys = ("operation_id", "origin_kind") if entry["format_version"] == RECOVERY_FORMAT_VERSION else ("publication_id",)
        expected_identity = {key: entry[key] for key in ("checkpoint_id", *origin_keys, "format_version", "schema_version", "created_at")}
        if (type(manifest) is not dict or set(manifest) != {*expected_identity, "receiver_version", "receiver_node_id", "row_counts", "issues"}
                or any(manifest.get(key) != value for key, value in expected_identity.items())
                or manifest["receiver_version"] != json.loads(entry["receiver_version_json"])
                or type(manifest["issues"]) is not list):
            _fail("Checkpoint manifest identity does not match", "SYNC_CHECKPOINT_DAMAGED")
        _uuid(manifest["receiver_node_id"])
        if any(type(issue) is not dict or set(issue) != {"code", "target"}
               or any(not isinstance(value, str) for value in issue.values()) for issue in manifest["issues"]):
            _fail("Checkpoint integrity issue manifest is invalid", "SYNC_CHECKPOINT_DAMAGED")
        counts = {table: archive.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in KNOWLEDGE_COLUMNS}
        if counts != manifest["row_counts"]:
            _fail("Checkpoint row counts differ", "SYNC_CHECKPOINT_DAMAGED")
        # Metadata is restricted even if a tampered file and its registered hash
        # were substituted together. Node/library credentials never fit here.
        if archive.execute("SELECT 1 FROM meta WHERE substr(key,1,5) != 'step:' LIMIT 1").fetchone():
            _fail("Checkpoint contains non-knowledge metadata", "SYNC_CHECKPOINT_DAMAGED")
        archive.set_authorizer(_readonly_authorizer)
        return archive, manifest
    except BaseException:
        archive.close()
        raise


@contextmanager
def _open_entry(con, entry, *, root=None, profile=None):
    profile = _profile(con, profile=profile, root=root)
    storage_lifecycle.require_guard(profile)
    entry = _entry(entry)
    _assert_not_retired(con, entry)
    current = _version(db.knowledge_version(con))
    if json.loads(entry["receiver_version_json"])["library_id"] != current["library_id"]:
        _fail("Checkpoint belongs to another receiver", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
    directory_path = profile.checkpoints
    archive = None
    caller_error = None
    try:
        with _Directory(directory_path, base=directory_path) as directory:
            with directory.open_file(entry["storage_key"]) as stream:
                if os.fstat(stream.fileno()).st_size != entry["byte_size"]:
                    _fail("Checkpoint archive size differs", "SYNC_CHECKPOINT_DAMAGED")
                data = stream.read(MAX_ARCHIVE_BYTES + 1)
                archive, manifest = _decode(data, entry)
                del data
                if manifest["receiver_node_id"] != _node_id(con):
                    _fail("Checkpoint belongs to another receiver node", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
                directory.verify()
                try:
                    yield archive, manifest
                except BaseException as exc:
                    caller_error = exc
                    raise
                directory.verify()
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        if exc is caller_error or isinstance(exc, SyncError):
            raise
        _fail("Checkpoint archive cannot be verified", "SYNC_CHECKPOINT_DAMAGED")
    finally:
        if archive is not None:
            archive.close()


def _stage(con, origin_id, *, origin_kind, root=None, media_store=None, report_root=None, profile=None):
    """Common closed builder; each origin has a distinct registration contract."""
    profile = _profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    storage_lifecycle.require_guard(profile)
    _write_transaction(con)
    _uuid(origin_id)
    recovery = origin_kind == "recovery"
    if not recovery and con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (origin_id,)).fetchone():
        _fail("An existing publication must reuse its committed receipt")
    identifier = str(uuid.uuid4())
    entry = {"checkpoint_id": identifier,
             "receiver_version_json": _json(_version(db.knowledge_version(con))),
             "format_version": RECOVERY_FORMAT_VERSION if recovery else FORMAT_VERSION, "schema_version": ARCHIVE_SCHEMA_VERSION,
             "storage_key": identifier + ".sqlite",
             "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    entry.update({"operation_id": origin_id, "origin_kind": "recovery"} if recovery else {"publication_id": origin_id})
    archive = _archive_connection()
    try:
        for statement in _ddl().values():
            archive.execute(statement)
        for table, columns in KNOWLEDGE_COLUMNS.items():
            names = ",".join(f'"{name}"' for name in columns)
            where = " WHERE substr(key,1,5)='step:'" if table == "meta" else ""
            _insert_rows(archive, table, columns, (tuple(row) for row in con.execute(f'SELECT {names} FROM "{table}"{where}')))
        media_root = profile.media
        issues = _capture_media(archive, media_root)
        reference_issues, _ = _reference_issues(archive, media_root, profile.reports, receiver=con, profile=profile)
        for issue in reference_issues:
            if issue not in issues:
                issues.append(issue)
        origin_keys = ("operation_id", "origin_kind") if recovery else ("publication_id",)
        manifest = {key: entry[key] for key in ("checkpoint_id", *origin_keys, "format_version", "schema_version", "created_at")}
        manifest.update(receiver_version=json.loads(entry["receiver_version_json"]), receiver_node_id=_node_id(con), issues=issues,
                        row_counts={table: archive.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in KNOWLEDGE_COLUMNS})
        archive.execute("INSERT INTO checkpoint_manifest VALUES(1,?)", (_json(manifest),))
        archive.commit()
        data = archive.serialize()
    except sqlite3.DatabaseError:
        _fail("Checkpoint exceeds storage limits or contains invalid knowledge", "SYNC_CHECKPOINT_STORAGE_FAILED", 503)
    finally:
        archive.close()
    if len(data) > MAX_ARCHIVE_BYTES:
        _fail("Checkpoint exceeds its size limit", "SYNC_CHECKPOINT_STORAGE_FAILED", 507)
    entry.update(byte_size=len(data), sha256=hashlib.sha256(data).hexdigest())
    directory_path = profile.checkpoints
    temporary = "." + identifier + ".part"
    try:
        with _Directory(directory_path, create=True, base=directory_path) as directory:
            try:
                with directory.open_file(temporary, write=True, create=True) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                del data
                with directory.open_file(temporary) as stream:
                    verified, _ = _decode(stream.read(MAX_ARCHIVE_BYTES + 1), entry)
                    verified.close()
                directory.verify()
                _rename_new(directory, temporary, directory, entry["storage_key"])
                if os.name != "nt":
                    os.fsync(directory.fd)
                directory.verify()
            finally:
                try:
                    directory.unlink(temporary)
                except FileNotFoundError:
                    pass
    except OSError:
        _fail("Checkpoint storage failed", "SYNC_CHECKPOINT_STORAGE_FAILED", 503)
    return entry


def stage(con, publication_id, *, root=None, media_store=None, report_root=None, profile=None):
    """Capture a publication's prior knowledge without registering/committing."""
    return _stage(con, publication_id, origin_kind="publication", root=root,
                  media_store=media_store, report_root=report_root, profile=profile)


def stage_recovery(con, operation_id, *, root=None, media_store=None, report_root=None, profile=None):
    """Capture a recovery protection image; never invent a sync publication."""
    return _stage(con, operation_id, origin_kind="recovery", root=root,
                  media_store=media_store, report_root=report_root, profile=profile)


def register(con, entry, *, root=None, profile=None):
    """Attach a staged preimage after publication INSERT, inside that transaction."""
    profile = _profile(con, profile=profile, root=root)
    storage_lifecycle.require_guard(profile)
    _write_transaction(con)
    entry = _entry(entry)
    if entry["format_version"] != FORMAT_VERSION:
        _fail("Recovery images cannot be registered as publications")
    if not con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (entry["publication_id"],)).fetchone():
        _fail("Checkpoint registration requires its new publication")
    path = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not path:
        _fail("Checkpoint registration requires a persistent publication")
    committed = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=0)
    try:
        if committed.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (entry["publication_id"],)).fetchone():
            _fail("Checkpoint must be registered in the new publication's transaction")
    finally:
        committed.close()
    with storage_artifacts.operation(con, profile):
        with _open_entry(con, entry, profile=profile):
            columns = tuple(sorted(_ENTRY_KEYS))
            _insert_rows(con, "sync_checkpoint", columns, [tuple(entry[key] for key in columns)])
            _register_artifact(con, entry, profile)
    return entry["checkpoint_id"]


def _lookup(con, checkpoint_id):
    _uuid(checkpoint_id)
    row = con.execute("SELECT c.* FROM sync_checkpoint c JOIN sync_publication p USING(publication_id) "
                      "WHERE c.checkpoint_id=?", (checkpoint_id,)).fetchone()
    if row is None:
        _fail("Committed checkpoint was not found", "SYNC_CHECKPOINT_NOT_FOUND", 404)
    entry = dict(row)
    _assert_not_retired(con, entry)
    return entry


def _preview(con, entry, *, root=None, media_store=None, report_root=None, profile=None):
    profile = _profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    storage_lifecycle.require_guard(profile)
    result = {**entry, "receiver_version": None,
              "recoverable": False, "row_counts": {}, "issues": [], "warnings": []}
    try:
        with _open_entry(con, entry, profile=profile) as (archive, manifest):
            result.update(receiver_version=manifest["receiver_version"], receiver_node_id=manifest["receiver_node_id"])
            issues, warnings = _reference_issues(archive, profile.media, profile.reports, receiver=con, profile=profile)
            for issue in manifest["issues"]:
                if issue not in issues:
                    issues.append(issue)
            result.update(row_counts=manifest["row_counts"], issues=issues, warnings=warnings,
                          recoverable=not issues)
    except (SyncError, sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        result["issues"] = [_issue(getattr(exc, "code", "SYNC_CHECKPOINT_DAMAGED"), "archive")]
    return result


def list_checkpoints(con, *, root=None, media_store=None, report_root=None, limit=100, profile=None):
    """List only committed entries, verifying files without repairing/creating any."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        _fail("Checkpoint list limit must be between 1 and 1000")
    profile = _profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with _read_transaction(con, profile):
        rows = con.execute("SELECT c.* FROM sync_checkpoint c JOIN sync_publication p USING(publication_id) "
                           "ORDER BY c.created_at DESC,c.checkpoint_id DESC LIMIT ?", (limit,)).fetchall()
        return [_preview(con, dict(row), profile=profile) for row in rows]


def preview(con, checkpoint_id, *, root=None, media_store=None, report_root=None, profile=None):
    profile = _profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with _read_transaction(con, profile):
        return _preview(con, _lookup(con, checkpoint_id), profile=profile)


@contextmanager
def open_verified(con, checkpoint_id, *, root=None, media_store=None, report_root=None, profile=None):
    """Open a verified immutable image under a protected knowledge snapshot.

    An existing caller transaction requires its existing lifecycle guard. An
    idle connection acquires protection before BEGIN and releases it on exit.
    This never writes or restores live data.
    """
    profile = _profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with _read_transaction(con, profile):
        entry = _lookup(con, checkpoint_id)
        with _open_entry(con, entry, profile=profile) as (archive, manifest):
            issues, _ = _reference_issues(archive, profile.media, profile.reports, receiver=con, profile=profile)
            if issues or manifest["issues"]:
                _fail("Checkpoint references are incomplete", "SYNC_CHECKPOINT_NOT_RECOVERABLE")
            yield archive, manifest
