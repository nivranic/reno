"""Home-produced immutable recovery sources, backed by registered DB images.

Only the closed knowledge projection is read from an image. The Home identity
declaration is produced with the image; existing backups cannot acquire an
invented historical node binding. Public results contain IDs and digests only.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from . import config, db, storage_backups as backups, storage_lifecycle as lifecycle
from . import storage_admin, sync_checkpoints as checkpoints, sync_protocol as protocol
from . import sync_queue, sync_snapshot, sync_state, tasks
from .sync_recovery import _Pins

DIRECTORY = ".reno-home-knowledge-sources-v1"
MAX_DECLARATION_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
MAX_CELL_BYTES = 8 * 1024 * 1024
MAX_KNOWLEDGE_BYTES = 256 * 1024 * 1024
MAX_SQL_STEPS = 10_000_000
MAX_SECONDS = 120
COLUMNS = {name: columns for name, columns in checkpoints.KNOWLEDGE_COLUMNS.items()
           if name != "sync_report"}
_FIELDS = frozenset("format_version source_kind source_id library_id node_id profile_sha256 "
                   "source_version schema_version backup_id descriptor_sha256 image_sha256 "
                   "knowledge_sha256 media_dependencies created_at".split())
_INTENT_FIELDS = (_FIELDS - {"source_kind", "descriptor_sha256", "image_sha256"}) | {"action"}


class HomeRecoverySourceError(ValueError):
    def __init__(self, code):
        self.code = "HOME_RECOVERY_" + code
        super().__init__(self.code)


def _fail(code):
    raise HomeRecoverySourceError(code) from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _declaration(raw):
    try:
        value = backups._decode(raw)
        if set(value) != _FIELDS or type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail("SOURCE_INVALID")
        if value["source_kind"] != "home_checkpoint":
            _fail("SOURCE_INVALID")
        for key in ("source_id", "library_id", "node_id", "backup_id"):
            backups._uuid(value[key])
        for key in ("profile_sha256", "descriptor_sha256", "image_sha256", "knowledge_sha256"):
            if type(value[key]) is not str or not backups._SHA.fullmatch(value[key]):
                _fail("SOURCE_INVALID")
        sync_snapshot._version(value["source_version"])
        if value["source_version"]["library_id"] != value["library_id"]:
            _fail("SOURCE_INVALID")
        if type(value["schema_version"]) is not int or not 10 <= value["schema_version"] <= 16:
            _fail("SOURCE_SCHEMA_UNSUPPORTED")
        if type(value["created_at"]) is not str or not backups._TIME.fullmatch(value["created_at"]):
            _fail("SOURCE_INVALID")
        dependencies = value["media_dependencies"]
        if type(dependencies) is not list or len(dependencies) > MAX_ROWS:
            _fail("SOURCE_INVALID")
        keys = set()
        for item in dependencies:
            if (type(item) is not dict or set(item) != {"storage_key", "sha256", "byte_size"}
                    or type(item["storage_key"]) is not str or not item["storage_key"]
                    or Path(item["storage_key"]).is_absolute() or "\\" in item["storage_key"]
                    or ":" in item["storage_key"] or ".." in Path(item["storage_key"]).parts
                    or type(item["sha256"]) is not str or not backups._SHA.fullmatch(item["sha256"])
                    or type(item["byte_size"]) is not int or not 0 <= item["byte_size"] <= backups.MAX_IMAGE
                    or item["storage_key"] in keys):
                _fail("SOURCE_INVALID")
            keys.add(item["storage_key"])
        return value
    except HomeRecoverySourceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail("SOURCE_INVALID")


def _profile_sha(profile):
    return _sha(backups._profile_json(profile))


def _intent(raw):
    value = backups._decode(raw)
    if set(value) != _INTENT_FIELDS or value["action"] != "produce_home_checkpoint":
        _fail("SOURCE_PROVENANCE_INVALID")
    common = {key: item for key, item in value.items() if key != "action"}
    _declaration(_json({**common, "source_kind": "home_checkpoint",
                       "descriptor_sha256": "0" * 64, "image_sha256": "0" * 64}))
    if value["backup_id"] != str(uuid.uuid5(uuid.UUID(value["source_id"]), "reno.home.knowledge.checkpoint.v1")):
        _fail("SOURCE_PROVENANCE_INVALID")
    return value


def _source_path(profile, source_id):
    backups._uuid(source_id)
    return profile.database.parent / DIRECTORY / (source_id + ".json")


def _pin_file(pins, path, maximum, *, capture=False, expected=None, base=None):
    _, identity = backups._read(path, maximum=maximum, content=False)
    if expected is not None and identity != expected:
        _fail("SOURCE_CHANGED")
    base = path.parent if base is None else base
    return pins.add(base, path.relative_to(base).as_posix(), identity["sha256"], identity["byte_size"],
                    maximum, capture=capture), identity


def _ordinary_columns(con, table):
    row = con.execute("SELECT type,sql FROM sqlite_master WHERE name=? COLLATE NOCASE", (table,)).fetchall()
    if (len(row) != 1 or row[0][0] != "table" or type(row[0][1]) is not str
            or not row[0][1].lstrip().upper().startswith("CREATE TABLE")):
        _fail("SOURCE_SCHEMA_UNSUPPORTED")
    infos = con.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
    if any(info[6] for info in infos) or {info[1] for info in infos} != set(COLUMNS[table]):
        _fail("SOURCE_SCHEMA_UNSUPPORTED")


def _authorize_knowledge(action, first, second, database, _origin):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and database == "main":
        if first in {"sqlite_master", "sqlite_schema"} or (first in COLUMNS and second in COLUMNS[first]):
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second in COLUMNS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {"length", "substr"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _read_knowledge(con):
    rows, total, count = {}, 0, 0
    for table, columns in COLUMNS.items():
        _ordinary_columns(con, table)
        names = ",".join('"' + column + '"' for column in columns)
        where = " WHERE substr(key,1,5)='step:'" if table == "meta" else ""
        lengths = ",".join('length(CAST("' + column + '" AS BLOB))' for column in columns)
        for sizes in con.execute(f'SELECT {lengths} FROM "{table}"{where} LIMIT ?', (MAX_ROWS + 1,)):
            count += 1
            if count > MAX_ROWS or any(size is not None and size > MAX_CELL_BYTES for size in sizes):
                _fail("SOURCE_LIMIT")
            total += sum(size or 0 for size in sizes)
            if total > MAX_KNOWLEDGE_BYTES:
                _fail("SOURCE_LIMIT")
        keys = checkpoints._PRIMARY_KEYS[table]
        ordering = ",".join('"' + column + '"' for column in keys)
        rows[table] = [dict(row) for row in con.execute(f'SELECT {names} FROM "{table}"{where} ORDER BY {ordering}')]
    # Validate local and global semantic references independently of SQL FKs.
    videos = []
    assets = {row["video_id"] for row in rows["video_asset"]}
    for table in ("frame", "transcript_segment", "ocr_span", "visual_observation", "knowledge_atom", "evidence_ref"):
        if any(row["video_id"] not in assets for row in rows[table]):
            _fail("SOURCE_GRAPH_INVALID")
    for asset in rows["video_asset"]:
        protocol.validate_video_id(asset["video_id"])
        video = {"video_id": asset["video_id"], "tables": {}}
        for table in ("frame", "transcript_segment", "ocr_span", "visual_observation", "knowledge_atom", "evidence_ref"):
            selected = [row for row in rows[table] if row["video_id"] == asset["video_id"]]
            wire_columns = protocol.TABLES[table]["columns"]
            wire = [{key: row[key] for key in wire_columns} for row in selected]
            if table == "frame":
                # The network contract deliberately excludes local paths;
                # Home restoration keeps and separately pins those originals.
                for item in wire:
                    item["path"] = None
            protocol.validate_rows(table, wire, video_id=asset["video_id"])
            video["tables"][table] = selected
        sync_snapshot._check_local_references(video)
        videos.append(video)
    for table in ("knowledge_cluster", "conflict_case"):
        wire_columns = protocol.TABLES[table]["columns"]
        protocol.validate_rows(table, [{key: row[key] for key in wire_columns} for row in rows[table]])
    sync_snapshot._check_global_references({"videos": videos, "globals": {
        table: rows[table] for table in ("knowledge_cluster", "conflict_case")}})
    for row in rows["meta"]:
        protocol.validate_rows("meta", [row])
        pieces = row["key"].split(":")
        if len(pieces) != 3 or pieces[1] not in assets:
            _fail("SOURCE_GRAPH_INVALID")
    return rows


def _media_path(profile, value):
    if type(value) is not str or not value:
        _fail("SOURCE_MEDIA_INVALID")
    path = Path(value.replace("\\", "/"))
    if not path.is_absolute():
        path = profile.media.parent / path
    path = Path(os.path.abspath(path))
    # Reject lexical traversal before normalization, including Windows drives.
    if ".." in Path(value.replace("\\", "/")).parts:
        _fail("SOURCE_MEDIA_INVALID")
    try:
        path.relative_to(profile.media)
    except ValueError:
        _fail("SOURCE_MEDIA_INVALID")
    return path


def _pin_media(pins, rows, profile):
    held = {}
    def pin(value, expected_sha=None):
        path = _media_path(profile, value)
        key = backups._key(path)
        if key not in held:
            _, identity = _pin_file(pins, path, backups.MAX_IMAGE, base=profile.media)
            held[key] = identity
        identity = held[key]
        if expected_sha is not None and identity["sha256"] != expected_sha:
            _fail("SOURCE_MEDIA_CHANGED")
    for asset in rows["video_asset"]:
        files = backups._decode((asset["files_json"] or "{}").encode("utf-8"))
        if "video" not in files:
            _fail("SOURCE_MEDIA_INVALID")
        pin(files["video"], asset["content_sha256"])
        if files.get("audio") is not None:
            pin(files["audio"], asset["audio_sha256"])
        elif asset["audio_sha256"] is not None:
            _fail("SOURCE_MEDIA_INVALID")
    for row in rows["frame"]:
        pin(row["path"])
    return sorted(({"storage_key": Path(path).relative_to(profile.media).as_posix(),
                    "sha256": identity["sha256"], "byte_size": identity["byte_size"]}
                   for path, identity in held.items()), key=lambda value: value["storage_key"])


@dataclass(frozen=True, slots=True)
class HomeKnowledgeSource:
    declaration: dict
    declaration_sha256: str
    rows: dict
    knowledge_sha256: str
    _pins: object

    def public(self):
        return {"source_kind": "home_checkpoint", "source_id": self.declaration["source_id"],
                "source_version": dict(self.declaration["source_version"]),
                "source_sha256": self.declaration["image_sha256"],
                "source_binding_sha256": self.declaration_sha256,
                "knowledge_sha256": self.knowledge_sha256,
                "row_counts": {name: len(rows) for name, rows in self.rows.items()}}

    def verify(self):
        self._pins.verify()
        if (_sha(self.rows) != self.knowledge_sha256
                or _sha(self.declaration) != self.declaration_sha256):
            _fail("SOURCE_CHANGED")


@contextmanager
def open_source(con, source_id, *, profile):
    """Borrow SH, read a registered Home declaration, and retain exact pins."""
    lifecycle.require_guard(profile)
    storage_admin.from_connection(con, profile=profile)
    with ExitStack() as stack:
        try:
            pins = _Pins(stack)
            raw, identity = _pin_file(pins, _source_path(profile, source_id), MAX_DECLARATION_BYTES, capture=True)
            declaration = _declaration(raw)
            intent_path = _source_path(profile, source_id).with_name(source_id + ".intent.json")
            intent_raw, _ = _pin_file(pins, intent_path, MAX_DECLARATION_BYTES, capture=True)
            intent = _intent(intent_raw)
            if any(declaration[key] != intent[key] for key in _INTENT_FIELDS - {"action"}):
                _fail("SOURCE_PROVENANCE_INVALID")
            library, node = sync_queue._local_identity(con)
            node_raw, _ = _pin_file(pins, profile.database.parent / "node-identity.json", MAX_DECLARATION_BYTES, capture=True)
            if backups._decode(node_raw) != {"instance_id": node}:
                _fail("SOURCE_IDENTITY_MISMATCH")
            if (declaration["source_id"] != source_id or declaration["library_id"] != library
                    or declaration["node_id"] != node or declaration["profile_sha256"] != _profile_sha(profile)):
                _fail("SOURCE_IDENTITY_MISMATCH")
            registry = backups._catalog(profile) / (declaration["backup_id"] + ".json")
            _pin_file(pins, registry, backups.MAX_JSON)
            record, _ = backups._record(registry)
            if record["state"] != "ready" or record["kind"] != "maintenance":
                _fail("SOURCE_NOT_READY")
            descriptor, descriptor_sha = backups._verify_ready(record, profile)
            if (descriptor_sha != declaration["descriptor_sha256"]
                    or descriptor["database_file"]["sha256"] != declaration["image_sha256"]
                    or descriptor["source_version"] != declaration["source_version"]
                    or descriptor["schema_version"] != declaration["schema_version"]):
                _fail("SOURCE_CHANGED")
            destination = Path(record["destination"])
            _, sidecar_identity = _pin_file(pins, destination.with_name(destination.name + backups.SUFFIX), backups.MAX_JSON)
            if sidecar_identity["sha256"] != descriptor_sha:
                _fail("SOURCE_CHANGED")
            _pin_file(pins, destination, backups.MAX_IMAGE, expected=descriptor["database_file"])
            # Copy the exact pinned standalone committed image into private
            # system TEMP. SQLite never opens the original backup as writable.
            temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-home-recovery-source-"))
            image = Path(temporary) / "image.sqlite"
            stream = pins.files[-1][2]
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            copying_started, copied = time.monotonic(), 0
            with image.open("xb") as target:
                while chunk := os.read(stream.fileno(), 128 * 1024):
                    copied += len(chunk)
                    if copied > descriptor["database_file"]["byte_size"] or time.monotonic() - copying_started > MAX_SECONDS:
                        _fail("SOURCE_LIMIT")
                    target.write(chunk)
            if copied != descriptor["database_file"]["byte_size"]:
                _fail("SOURCE_CHANGED")
            if os.name != "nt":
                image.chmod(0o600)
            archive = sqlite3.connect(image.as_uri() + "?mode=ro&immutable=1", uri=True)
            stack.callback(archive.close)
            archive.row_factory = sqlite3.Row
            archive.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_BYTES)
            archive.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_DECLARATION_BYTES)
            archive.execute("PRAGMA query_only=ON")
            archive.execute("PRAGMA trusted_schema=OFF")
            archive.set_authorizer(_authorize_knowledge)
            _ordinary_columns(archive, "meta")
            started, steps = time.monotonic(), [0]
            def progress():
                steps[0] += 1000
                return int(steps[0] > MAX_SQL_STEPS or time.monotonic() - started > MAX_SECONDS)
            archive.set_progress_handler(progress, 1000)
            if db.knowledge_version(archive) != declaration["source_version"]:
                _fail("SOURCE_CHANGED")
            rows = _read_knowledge(archive)
            if _sha(rows) != declaration["knowledge_sha256"]:
                _fail("SOURCE_CHANGED")
            if _pin_media(pins, rows, profile) != declaration["media_dependencies"]:
                _fail("SOURCE_MEDIA_CHANGED")
            source = HomeKnowledgeSource(declaration, identity["sha256"], rows, _sha(rows), pins)
            source.verify()
        except HomeRecoverySourceError:
            raise
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            control = backups._READ_CONTROL.get()
            if control is not None and control.error is exc:
                raise
            _fail("SOURCE_INVALID")
        yield source
        source.verify()


def create_checkpoint(*, profile, source_id):
    """Explicit maintenance producer; does not copy private facts to knowledge.

    A retry uses the original source/backup IDs. Busy workers are never stopped.
    No network, model, migration, or knowledge write occurs here.
    """
    if config.is_cloud():
        _fail("ROLE_REQUIRED")
    lifecycle._profile(profile)
    path = _source_path(profile, source_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with store.reference_operation():
            con = db.connect(profile.database)
            try:
                con.execute("BEGIN IMMEDIATE")
                library, node = sync_queue._local_identity(con)
                try:
                    with open_source(con, source_id, profile=profile) as source:
                        return {**source.public(), "replayed": True}
                except HomeRecoverySourceError:
                    # Only true declaration absence can start a new producer.
                    with checkpoints._Directory(path.parent.parent, base=path.parent.parent) as parent:
                        try:
                            parent.entry_stat(path.parent.name)
                        except FileNotFoundError:
                            absent = True
                        else:
                            with checkpoints._Directory(path.parent, base=path.parent) as directory:
                                try:
                                    directory.entry_stat(path.name)
                                except FileNotFoundError:
                                    absent = True
                                else:
                                    absent = False
                    if not absent:
                        raise
                source_version = db.knowledge_version(con)
                backup_id = str(uuid.uuid5(uuid.UUID(source_id), "reno.home.knowledge.checkpoint.v1"))
                destination = profile.database.with_name(profile.database.name + ".bak-home-knowledge-" + source_id)
                pins_stack = ExitStack()
                try:
                    pins = _Pins(pins_stack)
                    node_raw, _ = _pin_file(pins, profile.database.parent / "node-identity.json", MAX_DECLARATION_BYTES, capture=True)
                    if backups._decode(node_raw) != {"instance_id": node}:
                        _fail("SOURCE_IDENTITY_MISMATCH")
                    intent_path = path.with_name(source_id + ".intent.json")
                    try:
                        raw, _ = _pin_file(pins, intent_path, MAX_DECLARATION_BYTES, capture=True)
                    except FileNotFoundError:
                        # An already reserved/ready image cannot gain an
                        # invented original node binding after a crash.
                        registry = backups._catalog(profile) / (backup_id + ".json")
                        try:
                            backups._read(registry)
                        except FileNotFoundError:
                            pass
                        else:
                            _fail("SOURCE_PROVENANCE_MISSING")
                        rows = _read_knowledge(con)
                        dependencies = _pin_media(pins, rows, profile)
                        intent = {"format_version": 1, "action": "produce_home_checkpoint", "source_id": source_id,
                            "library_id": library, "node_id": node, "profile_sha256": _profile_sha(profile),
                            "source_version": source_version, "schema_version": int(con.execute(
                                "SELECT value FROM meta WHERE key='migration_version'").fetchone()[0]),
                            "backup_id": backup_id, "knowledge_sha256": _sha(rows),
                            "media_dependencies": dependencies, "created_at": backups._stamp()}
                        _intent(_json(intent))
                        if len(_json(intent)) > MAX_DECLARATION_BYTES:
                            _fail("SOURCE_LIMIT")
                        pins.verify()
                        with checkpoints._Directory(path.parent, base=profile.database.parent, create=True):
                            backups._write_json(intent_path, intent, replace=False)
                        _pin_file(pins, intent_path, MAX_DECLARATION_BYTES)
                    else:
                        intent = _intent(raw)
                    if (intent["source_id"] != source_id or intent["library_id"] != library
                            or intent["node_id"] != node or intent["profile_sha256"] != _profile_sha(profile)):
                        _fail("SOURCE_IDENTITY_MISMATCH")
                    # A ready original backup may be finalized after later KB
                    # edits. Before image creation the original version must
                    # still be current; never create another image under this ID.
                    registry = backups._catalog(profile) / (backup_id + ".json")
                    try:
                        backups._read(registry)
                    except FileNotFoundError:
                        if source_version != intent["source_version"]:
                            _fail("SOURCE_CHANGED")
                    db.snapshot(con, destination, profile=profile, kind="maintenance", backup_id=backup_id)
                    record, _ = backups._record(registry)
                    descriptor, descriptor_sha = backups._verify_ready(record, profile)
                    if (descriptor["source_version"] != intent["source_version"]
                            or descriptor["schema_version"] != intent["schema_version"]):
                        _fail("SOURCE_CHANGED")
                    declaration = {**{key: item for key, item in intent.items() if key != "action"},
                        "source_kind": "home_checkpoint", "descriptor_sha256": descriptor_sha,
                        "image_sha256": descriptor["database_file"]["sha256"]}
                    _declaration(_json(declaration))
                    if len(_json(declaration)) > MAX_DECLARATION_BYTES:
                        _fail("SOURCE_LIMIT")
                    pins.verify()
                    with checkpoints._Directory(path.parent, base=profile.database.parent, create=True):
                        backups._write_json(path, declaration, replace=False)
                    with open_source(con, source_id, profile=profile) as source:
                        return {**source.public(), "replayed": False}
                finally:
                    pins_stack.close()
            finally:
                con.rollback()
                con.close()
