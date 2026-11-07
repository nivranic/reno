"""Registered committed DB images, frozen manifests and read-only roots.

Explicit profiles only; no implicit config, migrations, live service changes,
or archive recursion. An operator must stop uncooperative/legacy writers before
initialize_offline. Shared protection covers the whole image/catalog publish.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from reno import storage_lifecycle as lifecycle, sync_media as safe, storage_artifacts as artifacts

CATALOG = ".reno-storage-backups-v1"
SUFFIX = ".reno-backup.json"
NAMESPACES = ("sync_media", "local_report", "sync_report", "checkpoint")
KINDS = ("manual", "judge", "backfill", "rerun", "migration", "maintenance", "service")
REPORTS = ("incremental_diff", "checklist", "conflicts", "prices", "summary")
MAX_JSON = 16 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024 * 1024
MAX_ROWS = 100_000
MAX_BACKUPS = 4096
MAX_SCAN_SECONDS = 120
SUPPORTED_SCHEMAS = frozenset(range(18))
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_READ_CONTROL = ContextVar("storage_backup_read_control", default=None)


class _ReadControl:
    def __init__(self, value):
        self.value, self.error = value, None

    def call(self, method, *args):
        if self.error is not None:
            raise self.error
        callback = getattr(self.value, method, None)
        try:
            if method != "check":
                self.value.check()
            if callback is not None:
                callback(*args)
        except BaseException as exc:
            # SQLite progress callbacks cannot propagate Python exceptions.
            # Save the original object and rethrow after SQLITE_INTERRUPT.
            self.error = exc
            raise


def _read_check(method="check", *args):
    control = _READ_CONTROL.get()
    if control is not None:
        control.call(method, *args)


@contextmanager
def controlled_reads(control):
    """Optional read-only caller budget; defaults/producers remain unchanged."""
    if not callable(getattr(control, "check", None)):
        raise ValueError("Read control requires a check callback")
    token = _READ_CONTROL.set(_ReadControl(control))
    try:
        yield
        _read_check()
    finally:
        _READ_CONTROL.reset(token)


class BackupError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise BackupError(code)


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _path(value):
    try:
        path = Path(value)
    except (TypeError, ValueError):
        _fail("BACKUP_INVALID_PATH")
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        _fail("BACKUP_INVALID_PATH")
    return Path(os.path.abspath(os.fspath(path)))


def _uuid(value):
    if type(value) is not str:
        _fail("BACKUP_INVALID_ID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        _fail("BACKUP_INVALID_ID")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _decode(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _fail("BACKUP_INVALID_JSON")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _fail("BACKUP_INVALID_JSON"))
        if type(value) is not dict:
            _fail("BACKUP_INVALID_JSON")
        return value
    except (ValueError, UnicodeError, RecursionError):
        _fail("BACKUP_INVALID_JSON")


def _identity(info):
    return {"device": str(info.st_dev), "inode": str(info.st_ino), "byte_size": info.st_size,
            "mtime_ns": str(info.st_mtime_ns), "ctime_ns": str(info.st_ctime_ns)}


def _file_identity(value):
    if (type(value) is not dict or set(value) != {"device", "inode", "byte_size", "mtime_ns", "ctime_ns", "sha256"}
            or type(value["byte_size"]) is not int or not 0 <= value["byte_size"] <= MAX_IMAGE
            or type(value["sha256"]) is not str or not _SHA.fullmatch(value["sha256"])
            or any(type(value[k]) is not str or not value[k].isdigit() for k in ("device", "inode", "mtime_ns", "ctime_ns"))):
        _fail("BACKUP_INVALID_IDENTITY")
    return value


def _read(path, *, maximum=MAX_JSON, content=True, read_control=None):
    """No-follow bounded read with same-handle identity and digest evidence."""
    if read_control is not None:
        with controlled_reads(read_control):
            return _read(path, maximum=maximum, content=content)
    _read_check()
    path = _path(path)
    with safe._Directory(path.parent, base=path.parent) as directory:
        before = directory.entry_stat(path.name)
        safe._safe_stat(before, directory=False)
        if before.st_nlink != 1 or before.st_size > maximum:
            _fail("BACKUP_UNSAFE_FILE")
        _read_check("before_read", path, before.st_size, content)
        with directory.open_file(path.name) as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                _fail("BACKUP_FILE_CHANGED")
            data, digest, size = bytearray(), hashlib.sha256(), 0
            started = time.monotonic()
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                _read_check("read_chunk", path, len(chunk), content)
                if time.monotonic() - started > MAX_SCAN_SECONDS:
                    _fail("BACKUP_SCAN_TIMEOUT")
                size += len(chunk)
                if size > maximum:
                    _fail("BACKUP_TOO_LARGE")
                digest.update(chunk)
                if content:
                    data.extend(chunk)
            after = os.fstat(stream.fileno())
            if _identity(opened) != _identity(after) or size != after.st_size or after.st_nlink != 1:
                _fail("BACKUP_FILE_CHANGED")
        current = directory.entry_stat(path.name)
        if current.st_nlink != 1 or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _fail("BACKUP_FILE_CHANGED")
        directory.verify()
    return bytes(data), {**_identity(after), "sha256": digest.hexdigest()}


def _write_json(path, value, *, replace):
    raw = _json(value)
    if len(raw) > MAX_JSON:
        _fail("BACKUP_DESCRIPTOR_TOO_LARGE")
    temporary = ".backup-write-" + uuid.uuid4().hex + ".tmp"
    with safe._Directory(path.parent, base=path.parent) as directory:
        if replace:
            # Replacement is restricted to known catalog/operation records.
            _read(path)
        with directory.open_file(temporary, write=True, create=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            # Callers have validated this exact catalog record and hold the
            # source guard. No DB image or descriptor is ever overwritten.
            if os.name == "nt":
                os.replace(safe._native_path(path.parent / temporary), safe._native_path(path))
            else:
                os.replace(temporary, path.name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
        else:
            safe._rename_new(directory, temporary, directory, path.name)
        if os.name != "nt":
            os.fsync(directory.fd)
        directory.verify()
    return _sha(raw)


def _profile_json(profile):
    return {name: _key(getattr(profile, name)) for name in ("database", "media", "reports", "checkpoints")}


def _profile_value(value):
    fields = ("database", "media", "reports", "checkpoints")
    if type(value) is not dict or set(value) != set(fields):
        _fail("BACKUP_INVALID_PROFILE")
    if any(type(value[k]) is not str or value[k] != _key(_path(value[k])) for k in fields):
        _fail("BACKUP_INVALID_PROFILE")
    return lifecycle.StorageProfile(**value)


def _destination(path, profile):
    path = _path(path)
    key = _key(path)
    roots = (profile.media, profile.reports, profile.checkpoints, profile.database.parent / CATALOG)
    if (key == _key(profile.database) or path.name.startswith(".")
            or path.name.endswith(("-wal", "-shm", ".json"))
            or not (path.suffix.lower() in (".db", ".sqlite") or path.name.startswith(profile.database.name + ".bak-"))):
        _fail("BACKUP_INVALID_DESTINATION")
    for root in roots:
        try:
            if os.path.commonpath((key, _key(root))) == _key(root):
                _fail("BACKUP_INVALID_DESTINATION")
        except ValueError as exc:
            if isinstance(exc, BackupError):
                raise
            # Different drives have no common parent.
    try:
        relative = path.relative_to(profile.database.parent)
    except ValueError:
        relative = None
    if relative is not None and any(part.startswith(".reno-") or part == "sync-outbox" for part in relative.parts[:-1]):
        _fail("BACKUP_INVALID_DESTINATION")
    return path


def _roots(profile):
    result = {}
    for role in ("media", "reports", "checkpoints"):
        with safe._Directory(getattr(profile, role), base=getattr(profile, role)) as directory:
            _, dev, ino = directory.identities[-1]
            result[role] = {"path": _key(getattr(profile, role)), "device": str(dev), "inode": str(ino)}
    return result


@contextmanager
def _protection(con, profile):
    database = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not database or _key(database) != _key(profile.database):
        _fail("BACKUP_PROFILE_MISMATCH")
    if con.in_transaction or lifecycle.current_guard() is not None:
        yield lifecycle.require_guard(profile)
    else:
        with lifecycle.shared(profile) as guard:
            yield guard


def initialize_offline(profile):
    """Explicit stopped-writer maintenance only; accepts old schemas, no DB creation."""
    if lifecycle.current_guard() is not None:
        _fail("BACKUP_OFFLINE_INITIALIZE_WHILE_GUARDED")
    _read(profile.database, maximum=MAX_IMAGE, content=False)
    return lifecycle.initialize(profile)


def _catalog(profile, *, create=False):
    root = profile.database.parent / CATALOG
    if create:
        with safe._Directory(profile.database.parent, base=profile.database.parent) as parent:
            try:
                if os.name == "nt":
                    os.mkdir(safe._native_path(root))
                else:
                    os.mkdir(root.name, mode=0o700, dir_fd=parent.fd)
            except FileExistsError:
                pass
            if os.name != "nt":
                os.fsync(parent.fd)
            parent.verify()
    return root


def _mkdir(path):
    with safe._Directory(path.parent, base=path.parent) as parent:
        if os.name == "nt":
            os.mkdir(safe._native_path(path))
        else:
            os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
            os.fsync(parent.fd)
        parent.verify()


def _manifest_snapshot(profile):
    output = {}
    for name in REPORTS:
        try:
            raw, identity = _read(profile.reports / (name + ".manifest.json"), maximum=256 * 1024)
            output[name] = {"state": "present", "raw_base64": base64.b64encode(raw).decode("ascii"), **identity}
        except FileNotFoundError:
            output[name] = {"state": "absent"}
        except (BackupError, safe.SyncError, OSError):
            output[name] = {"state": "unreadable"}
    return output


def _block(blockers, namespaces, reason):
    for namespace in namespaces:
        blockers.setdefault(namespace, [])
        if reason not in blockers[namespace]:
            blockers[namespace].append(reason)


def _dependency(dependencies, blockers, namespace, key, sha256=None, byte_size=None, **extra):
    try:
        artifacts.validate_storage_key(namespace, key)
        if sha256 is not None and (type(sha256) is not str or not _SHA.fullmatch(sha256)):
            raise ValueError
        if byte_size is not None and (type(byte_size) is not int or byte_size < 0):
            raise ValueError
        if sha256 is not None and (namespace == "sync_media" and key.rsplit("/", 1)[1].split(".", 1)[0] != sha256
                                  or namespace == "sync_report" and key.rsplit("-", 1)[1][:-3] != sha256):
            raise ValueError
    except (ValueError, TypeError):
        _block(blockers, (namespace,), "invalid_dependency")
        return
    dependencies.append({"namespace": namespace, "storage_key": key, "sha256": sha256, "byte_size": byte_size, **extra})


def _manifest_dependencies(manifests, dependencies, blockers):
    if manifests is None:
        _block(blockers, ("local_report",), "legacy_manifest_unknown")
        return
    if set(manifests) != set(REPORTS):
        _block(blockers, ("local_report",), "manifest_set_incomplete")
        return
    # JSON sorts manifest object keys; preserve the original producer order
    # when rebuilding the immutable dependency array of existing backups.
    for name in REPORTS:
        item = manifests[name]
        if item == {"state": "absent"}:
            continue
        try:
            raw = base64.b64decode(item["raw_base64"], validate=True)
            if item["state"] != "present" or len(raw) != item["byte_size"] or _sha(raw) != item["sha256"]:
                raise ValueError
            value = _decode(raw)
            if "content_file" not in value:  # Generating status before a first report.
                if any(k in value for k in ("content_sha256", "byte_size")):
                    raise ValueError
                continue
            key = value["content_file"]
            namespace = "sync_report" if type(key) is str and key.startswith(".sync-versions/") else "local_report"
            _dependency(dependencies, blockers, namespace, key, value.get("content_sha256"), value.get("byte_size"), origin="manifest:" + name)
        except (KeyError, ValueError, TypeError):
            _block(blockers, ("local_report",), "manifest_unreadable")


def _media_reference(value, profile, dependencies, blockers, *, sha256=None, origin):
    """Map only paths provably inside this profile; include non-video refs."""
    if value in (None, ""):
        return
    try:
        if type(value) is not str or "\x00" in value:
            raise ValueError
        normalized = value.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise ValueError
        path = Path(normalized)
        if path.is_absolute():
            relative = path.relative_to(profile.media).as_posix()
        else:
            # Stored relative paths resolve from MEDIA.parent. A foreign OS
            # drive/UNC path cannot be guessed into this profile on Linux.
            if ":" in normalized or normalized.startswith("/"):
                raise ValueError
            prefix = profile.media.name + "/"
            if not normalized.startswith(prefix):
                raise ValueError
            relative = normalized[len(prefix):]
        if relative == "sync" or relative.startswith("sync/"):
            _dependency(dependencies, blockers, "sync_media", relative, sha256, origin=origin)
    except (ValueError, TypeError):
        _block(blockers, ("sync_media",), "media_mapping_unknown")


def _database_dependencies(path, profile, *, manifests):
    """Parse only frozen SQL metadata; return archive boundaries for roots.py."""
    from . import storage_home_recovery_roots as home_recovery_roots
    from . import storage_root_inputs as public_inputs
    dependencies, blockers, boundaries, retirement_facts = [], {}, [], []
    _read_check()
    source_version = {"library_id": None, "library_epoch": None, "kb_revision": None}
    _manifest_dependencies(manifests, dependencies, blockers)
    # immutable deliberately bypasses SQLite recovery. A rollback journal can
    # contain the only committed preimage after a crashed writer spilled dirty
    # pages into the main file, just as a WAL can hold committed newer pages.
    # Legacy images must be materialized explicitly before either is ignored.
    for suffix, reason in (("-wal", "unmaterialized_wal"), ("-journal", "unmaterialized_journal")):
        companion = path.with_name(path.name + suffix)
        try:
            _, companion_info = _read(companion, maximum=MAX_IMAGE, content=False)
            if companion_info["byte_size"]:
                _block(blockers, NAMESPACES, reason)
                return dependencies, blockers, boundaries, None, retirement_facts, source_version
        except FileNotFoundError:
            pass
    con = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    started, steps = time.monotonic(), 0
    def progress():
        nonlocal steps
        try:
            _read_check()
        except BaseException:
            return 1
        steps += 1000
        return int(steps > 10_000_000 or time.monotonic() - started > 10)
    allowed = {"meta", "video_asset", "frame", "sync_report", "sync_checkpoint", "sync_recovery_checkpoint",
               "sync_upload_pin", "sync_checkpoint_retirement", "storage_gc_operation", home_recovery_roots.TABLE}
    def authorize(action, first, second, database, origin):
        continuity = public_inputs.continuity_authorize(action, first, second, database, origin)
        if continuity is not None:
            return continuity
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and first == home_recovery_roots.TABLE:
            return (sqlite3.SQLITE_OK if database == "main" and origin is None
                    and second in home_recovery_roots.COLUMNS else sqlite3.SQLITE_DENY)
        if action == sqlite3.SQLITE_READ and first in allowed | {"sqlite_master", "sqlite_schema"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_info" and second in allowed:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_JSON)
        con.set_progress_handler(progress, 1000)
        con.set_authorizer(authorize)
        tables = {row["name"]: row for row in con.execute("SELECT name,type,sql FROM sqlite_schema") if row["name"] in allowed}
        if any(row["type"] != "table" or "VIRTUAL TABLE" in (row["sql"] or "").upper() for row in tables.values()):
            _fail("BACKUP_UNSUPPORTED_SCHEMA")
        metadata_bytes = 0
        def rows(table, columns):
            nonlocal metadata_bytes
            if table not in tables:
                return []
            actual = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            if not set(columns) <= actual:
                _fail("BACKUP_UNSUPPORTED_SCHEMA")
            # Only these four metadata values describe the image's structure
            # and knowledge identity. Consumer checkpoints and private BLOBs
            # are not dependency inputs and must never be JSON-materialized.
            projection = " WHERE key IN ('migration_version','library_id','library_epoch','kb_revision')" if table == "meta" else ""
            cursor = con.execute("SELECT " + ",".join('"' + c + '"' for c in columns) + ' FROM "' + table + '"' + projection)
            output = []
            # Bound each row before retaining it. fetchmany(MAX_ROWS+1) could
            # materialize a large image before the aggregate byte check ran.
            for index, row in enumerate(cursor):
                _read_check()
                if index >= MAX_ROWS:
                    _fail("BACKUP_TOO_MANY_ROWS")
                value = dict(row)
                metadata_bytes += len(_json(value))
                if metadata_bytes > MAX_JSON:
                    _fail("BACKUP_METADATA_TOO_LARGE")
                output.append(value)
            return output
        meta_rows = rows("meta", ("key", "value"))
        metadata = {row["key"]: row["value"] for row in meta_rows}
        if len(metadata) != len(meta_rows):
            _fail("BACKUP_UNSUPPORTED_SCHEMA")
        if metadata.get("library_id"):
            source_version["library_id"] = _uuid(metadata["library_id"])
        for name, minimum in (("library_epoch", 1), ("kb_revision", 0)):
            if name in metadata:
                value = metadata[name]
                if type(value) is not str or not value.isdecimal() or int(value) < minimum:
                    _fail("BACKUP_INVALID_SOURCE_VERSION")
                source_version[name] = int(value)
        version = int(metadata.get("migration_version", 0))
        if version not in SUPPORTED_SCHEMAS or "video_asset" not in tables:
            _fail("BACKUP_UNSUPPORTED_SCHEMA")
        required = {"sync_report"} if version >= 6 else set()
        if version >= 7:
            required.add("sync_checkpoint")
        if version >= 8:
            required.add("sync_recovery_checkpoint")
        if version >= 10:
            required.update(("sync_upload_pin", "sync_checkpoint_retirement", "storage_gc_operation"))
        if not required <= set(tables):
            _fail("BACKUP_UNSUPPORTED_SCHEMA")
        if version >= 17:
            def consume_continuity(row):
                nonlocal metadata_bytes
                _read_check()
                metadata_bytes += len(_json(tuple(row)))
                if metadata_bytes > MAX_JSON:
                    _fail("BACKUP_METADATA_TOO_LARGE")
            public_inputs.read_continuity_projection(con, check_cancelled=_read_check,
                consume_row=consume_continuity)
        elif public_inputs.continuity_reserved_projection(con):
            _fail("BACKUP_SOURCE_CONTINUITY_SCHEMA_UNKNOWN")
        if version >= 16:
            projected = home_recovery_roots.normalize_projection(rows(home_recovery_roots.TABLE, home_recovery_roots.COLUMNS),
                                                                 schema_version=version)
            if home_recovery_roots.TABLE not in tables or any(row["library_id"] != source_version["library_id"] for row in projected):
                _fail("BACKUP_UNSUPPORTED_SCHEMA")
        elif home_recovery_roots.TABLE in tables:
            _block(blockers, NAMESPACES, "home_recovery_schema_unknown")
        for row in rows("video_asset", ("files_json", "content_sha256")):
            try:
                files = _decode(row["files_json"] or "{}")
                for field, value in files.items():
                    _media_reference(value, profile, dependencies, blockers,
                                     sha256=row["content_sha256"] if field == "video" else None,
                                     origin="video_asset.files_json:" + field)
            except (ValueError, TypeError):
                _block(blockers, ("sync_media",), "invalid_media_pointer")
        for row in rows("frame", ("path",)):
            _media_reference(row["path"], profile, dependencies, blockers, origin="frame.path")
        for row in rows("sync_report", ("storage_key", "sha256", "byte_size")):
            _dependency(dependencies, blockers, "sync_report", row["storage_key"], row["sha256"], row["byte_size"], origin="sync_report")
        if version >= 10:
            operations = {r["operation_id"]: r for r in rows("storage_gc_operation", ("operation_id", "state", "request_sha256", "preview_sha256", "roots_sha256", "request_json", "policy_json", "result_json"))}
            for row in rows("sync_checkpoint_retirement", ("origin_kind", "checkpoint_id", "operation_id", "storage_key", "sha256", "byte_size")):
                # Discovery does not decide historical logical retirement.
                # Keep the original boundary/SQL facts for the group roots
                # reader to verify with storage_gc_contract against this exact
                # immutable DB, never today's live image. No permanent catalog
                # judgement is made here, nor is completed alone trusted.
                retirement_facts.append({"retirement": row, "operation": operations.get(row["operation_id"])})
                _block(blockers, ("checkpoint",), "retirement_unproven")
        for table, kind in (("sync_checkpoint", "publication"), ("sync_recovery_checkpoint", "recovery")):
            for row in rows(table, ("checkpoint_id", "storage_key", "sha256", "byte_size")):
                try:
                    _uuid(row["checkpoint_id"])
                    if row["storage_key"] != row["checkpoint_id"] + ".sqlite":
                        raise ValueError
                except ValueError:
                    _block(blockers, ("checkpoint",), "invalid_checkpoint_boundary")
                _dependency(dependencies, blockers, "checkpoint", row["storage_key"], row["sha256"], row["byte_size"], origin=table)
                boundaries.append({"origin_kind": kind, **row})
        for row in rows("sync_upload_pin", ("kind", "state", "descriptor_json")):
            if (row["kind"] not in ("media", "legacy", "publication") or row["state"] not in ("reserved", "stored", "legacy_hold", "published", "abandoned")
                    or row["kind"] == "legacy" and row["state"] != "legacy_hold"):
                _block(blockers, ("sync_media",), "invalid_upload_pin")
            if row["kind"] in ("media", "legacy") and row["state"] in ("reserved", "stored", "legacy_hold"):
                try:
                    value = safe.validate_media_descriptor(_decode(row["descriptor_json"]))
                    key = f"sync/{value['video_id']}/{value['sha256']}.{value['extension']}"
                    _dependency(dependencies, blockers, "sync_media", key, value["sha256"], value["byte_size"], origin="upload_pin")
                except (ValueError, TypeError):
                    _block(blockers, ("sync_media",), "invalid_upload_pin")
        return dependencies, blockers, boundaries, version, retirement_facts, source_version
    except (sqlite3.Error, BackupError, ValueError, TypeError):
        _read_check()
        _block(blockers, NAMESPACES, "database_dependencies_unreadable")
        return dependencies, blockers, boundaries, None, retirement_facts, source_version
    finally:
        con.close()


def _descriptor(value, record):
    keys = {"format", "backup_id", "kind", "request_sha256", "source_profile", "source_roots", "created_at",
            "schema_version", "consistency", "database_file", "manifests", "dependencies", "blockers",
            "checkpoint_boundaries", "retirement_facts", "source_version"}
    if type(value) is not dict or set(value) != keys or value["format"] != 1 or type(value["format"]) is not int:
        _fail("BACKUP_INVALID_DESCRIPTOR")
    for key in ("backup_id", "kind", "request_sha256", "source_profile", "created_at"):
        if value[key] != record[key]:
            _fail("BACKUP_INVALID_DESCRIPTOR")
    if (value["consistency"] != "committed-db-plus-observed-manifests"
            or type(value["source_roots"]) is not dict or set(value["source_roots"]) != {"media", "reports", "checkpoints"}
            or type(value["manifests"]) is not dict or set(value["manifests"]) != set(REPORTS)
            or any(type(value[k]) is not list for k in ("dependencies", "checkpoint_boundaries", "retirement_facts"))
            or type(value["blockers"]) is not dict or not set(value["blockers"]) <= set(NAMESPACES)
            or (value["schema_version"] is not None and (type(value["schema_version"]) is not int or value["schema_version"] not in SUPPORTED_SCHEMAS))):
        _fail("BACKUP_INVALID_DESCRIPTOR")
    _file_identity(value["database_file"])
    version = value["source_version"]
    if type(version) is not dict or set(version) != {"library_id", "library_epoch", "kb_revision"}:
        _fail("BACKUP_INVALID_DESCRIPTOR")
    if version["library_id"] is not None:
        _uuid(version["library_id"])
    for key, minimum in (("library_epoch", 1), ("kb_revision", 0)):
        if version[key] is not None and (type(version[key]) is not int or version[key] < minimum):
            _fail("BACKUP_INVALID_DESCRIPTOR")
    for role, item in value["source_roots"].items():
        if (type(item) is not dict or set(item) != {"path", "device", "inode"}
                or item["path"] != record["source_profile"][role]
                or any(type(item[k]) is not str or not item[k].isdigit() for k in ("device", "inode"))):
            _fail("BACKUP_INVALID_DESCRIPTOR")
    return value


def _record(path):
    raw, identity = _read(path)
    value = _decode(raw)
    base = {"format", "backup_id", "destination", "kind", "source_profile", "request_sha256", "created_at", "state"}
    state = value.get("state")
    extra = set() if state == "reserved" else {"descriptor_sha256", "descriptor"}
    if state in ("retiring", "retired"):
        extra |= {"retire_operation_id", "retire_parameters", "retire_files"}
    if state == "retired":
        extra.add("retired_at")
    if (set(value) != base | extra or value.get("format") != 1 or type(value.get("format")) is not int
            or state not in ("reserved", "ready", "retiring", "retired")
            or value.get("kind") not in KINDS or type(value.get("created_at")) is not str or not _TIME.fullmatch(value["created_at"])):
        _fail("BACKUP_INVALID_CATALOG")
    _uuid(value["backup_id"])
    profile = _profile_value(value["source_profile"])
    destination = _destination(value["destination"], profile)
    request = {k: value[k] for k in ("backup_id", "destination", "kind", "source_profile")}
    if (value["destination"] != _key(destination) or value["request_sha256"] != _sha(_json(request))
            or path.name != value["backup_id"] + ".json"):
        _fail("BACKUP_INVALID_CATALOG")
    if state != "reserved":
        descriptor = _descriptor(value["descriptor"], value)
        if value["descriptor_sha256"] != _sha(_json(descriptor)):
            _fail("BACKUP_INVALID_CATALOG")
    if state in ("retiring", "retired"):
        _uuid(value["retire_operation_id"])
        parameters = value["retire_parameters"]
        files = value["retire_files"]
        if (type(parameters) is not dict or set(parameters) != {"kind", "keep"} or parameters["kind"] != value["kind"]
                or type(parameters["keep"]) is not int or parameters["keep"] < 1
                or type(files) is not list or len(files) != 2):
            _fail("BACKUP_INVALID_CATALOG")
        for item, expected in zip(files, (destination, destination.with_name(destination.name + SUFFIX))):
            if type(item) is not dict or set(item) != {"path", "identity"} or item["path"] != _key(expected):
                _fail("BACKUP_INVALID_CATALOG")
            _file_identity(item["identity"])
        if files[0]["identity"] != value["descriptor"]["database_file"] or files[1]["identity"]["sha256"] != value["descriptor_sha256"]:
            _fail("BACKUP_INVALID_CATALOG")
    if state == "retired" and (type(value["retired_at"]) is not str or not _TIME.fullmatch(value["retired_at"])):
        _fail("BACKUP_INVALID_CATALOG")
    return value, identity


def _verify_ready(record, profile):
    path = _path(record["destination"])
    raw, _ = _read(path.with_name(path.name + SUFFIX))
    descriptor = _descriptor(_decode(raw), record)
    if (descriptor["source_profile"] != _profile_json(profile)
            or descriptor["source_roots"] != _roots(profile)
            or record.get("descriptor_sha256") not in (None, _sha(raw))
            or record.get("descriptor", descriptor) != descriptor):
        _fail("BACKUP_DESCRIPTOR_CHANGED")
    _, current = _read(path, maximum=MAX_IMAGE, content=False)
    if current != descriptor.get("database_file"):
        _fail("BACKUP_IMAGE_CHANGED")
    deps, blockers, boundaries, schema, retirements, version = _database_dependencies(path, profile, manifests=descriptor["manifests"])
    if any(descriptor[k] != expected for k, expected in (("dependencies", deps), ("blockers", blockers),
            ("checkpoint_boundaries", boundaries), ("schema_version", schema), ("retirement_facts", retirements), ("source_version", version))):
        _fail("BACKUP_DESCRIPTOR_CHANGED")
    if _read(path, maximum=MAX_IMAGE, content=False)[1] != current:
        _fail("BACKUP_IMAGE_CHANGED")
    return descriptor, _sha(raw)


def create(con, destination, *, profile, kind="manual", backup_id=None):
    destination = _destination(destination, profile)
    backup_id = _uuid(backup_id or str(uuid.uuid4()))
    if kind not in KINDS:
        _fail("BACKUP_INVALID_DESTINATION")
    request = {"backup_id": backup_id, "destination": _key(destination), "kind": kind, "source_profile": _profile_json(profile)}
    request_hash = _sha(_json(request))
    with _protection(con, profile) as guard:
        catalog = _catalog(profile, create=True)
        registry = catalog / (backup_id + ".json")
        try:
            previous, _ = _record(registry)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            if previous["request_sha256"] != request_hash:
                _fail("BACKUP_OPERATION_CONFLICT")
            if previous["state"] in ("ready", "reserved"):
                try:
                    descriptor, digest = _verify_ready(previous, profile)
                except FileNotFoundError:
                    _fail("BACKUP_RESERVED_INCOMPLETE")
                if previous["state"] == "reserved":
                    _write_json(registry, {**previous, "state": "ready", "descriptor_sha256": digest, "descriptor": descriptor}, replace=True)
                return {"backup_id": backup_id, "path": destination, "descriptor_path": destination.with_name(destination.name + SUFFIX), "replayed": True}
            _fail("BACKUP_RETIRED")
        with safe._Directory(destination.parent, base=destination.parent) as target:
            for name in (destination.name, destination.name + SUFFIX, destination.name + "-wal",
                         destination.name + "-shm", destination.name + "-journal"):
                try:
                    target.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("BACKUP_DESTINATION_EXISTS")
        record = {"format": 1, **request, "request_sha256": request_hash, "created_at": _stamp(), "state": "reserved"}
        _write_json(registry, record, replace=False)
        # SQLite opens only this new private staging directory. No arbitrary
        # existing target is handed to a writable sqlite3 connection.
        stage = destination.parent / (".reno-backup-stage-" + uuid.uuid4().hex)
        _mkdir(stage)
        temporary = stage / "image.sqlite"
        source = sqlite3.connect(profile.database.as_uri() + "?mode=ro", uri=True, timeout=5)
        dest = sqlite3.connect(temporary)
        started = time.monotonic()
        def progress(*_):
            if time.monotonic() - started > 120:
                _fail("BACKUP_COPY_TIMEOUT")
        try:
            source.execute("PRAGMA query_only=ON")
            if source.execute("PRAGMA page_count").fetchone()[0] * source.execute("PRAGMA page_size").fetchone()[0] > MAX_IMAGE:
                _fail("BACKUP_TOO_LARGE")
            source.execute("BEGIN")
            source.backup(dest, pages=256, progress=progress, sleep=0.01)
            dest.execute("PRAGMA journal_mode=DELETE")
        finally:
            source.close()
            dest.close()
        if os.name != "nt":
            temporary.chmod(0o600)
        manifests = _manifest_snapshot(profile)
        dependencies, blockers, boundaries, schema, retirements, version = _database_dependencies(temporary, profile, manifests=manifests)
        with safe._Directory(stage, base=stage) as origin, safe._Directory(destination.parent, base=destination.parent) as target:
            with origin.open_file(temporary.name, write=True) as stream:
                os.fsync(stream.fileno())
            safe._rename_new(origin, temporary.name, target, destination.name)
            if os.name != "nt":
                os.fsync(target.fd)
        stage.rmdir()
        _, image = _read(destination, maximum=MAX_IMAGE, content=False)
        descriptor = {"format": 1, "backup_id": backup_id, "kind": kind, "request_sha256": request_hash,
            "source_profile": _profile_json(profile), "source_roots": _roots(profile), "created_at": record["created_at"],
            "schema_version": schema, "consistency": "committed-db-plus-observed-manifests", "database_file": image,
            "manifests": manifests, "dependencies": dependencies, "blockers": blockers, "checkpoint_boundaries": boundaries,
            "retirement_facts": retirements, "source_version": version}
        sidecar = destination.with_name(destination.name + SUFFIX)
        digest = _write_json(sidecar, descriptor, replace=False)
        _write_json(registry, {**record, "state": "ready", "descriptor_sha256": digest, "descriptor": descriptor}, replace=True)
        guard.verify()
        return {"backup_id": backup_id, "path": destination, "descriptor_path": sidecar, "replayed": False}


def _catalog_entries(profile):
    catalog = _catalog(profile)
    try:
        with safe._Directory(catalog, base=catalog) as directory:
            names = directory.names()
    except FileNotFoundError:
        # A dangling link is not an absent catalog.
        with safe._Directory(catalog.parent, base=catalog.parent) as parent:
            try:
                parent.entry_stat(catalog.name)
            except FileNotFoundError:
                return []
        _fail("BACKUP_INVALID_CATALOG")
    if len(names) > MAX_BACKUPS * 2:
        _fail("BACKUP_TOO_MANY_RECORDS")
    records = []
    for name in sorted(names):
        _read_check()
        if name == "operations" or name.startswith(".backup-write-") and name.endswith(".tmp"):
            continue
        if not name.endswith(".json"):
            _fail("BACKUP_INVALID_CATALOG")
        value, identity = _record(catalog / name)
        records.append((value, {"name": name, **identity}))
    if len(records) > MAX_BACKUPS:
        _fail("BACKUP_TOO_MANY_RECORDS")
    return records


def _builtins(profile, blockers):
    """Fixed-depth, no-follow discovery; never follows maintenance symlinks."""
    data, found = profile.database.parent, {}
    with safe._Directory(data, base=data) as directory:
        names = directory.names()
    if len(names) > MAX_BACKUPS * 8:
        _fail("BACKUP_TOO_MANY_RECORDS")
    for name in sorted(names):
        _read_check()
        if name.startswith(profile.database.name + ".bak-") and not name.endswith((".json", "-wal", "-shm")):
            found[_key(data / name)] = data / name
        elif name.startswith(("maintenance-", "service-")):
            try:
                with safe._Directory(data, base=data) as parent:
                    item = parent.entry_stat(name)
                    if not stat.S_ISDIR(item.st_mode):
                        # Ordinary log files cannot contain the fixed nested
                        # backup paths. Reparse/symlink entries still block.
                        safe._safe_stat(item, directory=False)
                        continue
                with safe._Directory(data / name, base=data) as directory:
                    children = directory.names()
                if len(children) > MAX_BACKUPS:
                    _fail("BACKUP_TOO_MANY_RECORDS")
                if name.startswith("maintenance-"):
                    for child in children:
                        if child.startswith("reno-before-schema") and child.endswith(".db"):
                            path = data / name / child
                            found[_key(path)] = path
                    if "before" in children:
                        with safe._Directory(data / name / "before", base=data) as directory:
                            try:
                                directory.entry_stat("reno.db")
                                path = data / name / "before/reno.db"
                                found[_key(path)] = path
                            except FileNotFoundError:
                                pass
                elif "reno-before-reload.db" in children:
                    path = data / name / "reno-before-reload.db"
                    found[_key(path)] = path
            except (OSError, ValueError):
                _block(blockers, NAMESPACES, "builtin_backup_discovery_unreadable")
    if len(found) > MAX_BACKUPS:
        _fail("BACKUP_TOO_MANY_RECORDS")
    return found


def discover_locked(profile, *, read_control=None):
    """Read-only closed built-ins UNION registered external destinations."""
    if read_control is not None:
        with controlled_reads(read_control):
            return discover_locked(profile)
    _read_check()
    guard = lifecycle.require_guard(profile)
    blockers, catalog_fingerprints = {}, []
    try:
        legacy = _builtins(profile, blockers)
    except (OSError, ValueError):
        legacy = {}
        _block(blockers, NAMESPACES, "builtin_backup_discovery_unreadable")
    candidates, destinations = [], set()
    try:
        for record, identity in _catalog_entries(profile):
            _read_check("catalog_record", record)
            catalog_fingerprints.append(identity)
            legacy.pop(_key(record["destination"]), None)
            if record["source_profile"] != _profile_json(profile):
                continue
            key = _key(record["destination"])
            if key in destinations:
                _block(blockers, NAMESPACES, "catalog_destination_conflict")
            destinations.add(key)
            legacy.pop(key, None)
            candidates.append({"path": Path(record["destination"]), "record": record})
    except (BackupError, safe.SyncError, OSError):
        _block(blockers, NAMESPACES, "catalog_unreadable")
    candidates.extend({"path": path, "record": None} for path in legacy.values())
    _read_check("candidates", tuple(candidate["path"] for candidate in candidates))
    found = []
    for candidate in sorted(candidates, key=lambda c: (_key(c["path"]), (c["record"] or {}).get("backup_id", ""))):
        _read_check()
        path, record = candidate["path"], candidate["record"]
        result = {"path": str(path), "state": record["state"] if record else "legacy", "dependencies": [], "blockers": {}}
        if record and record["state"] in ("reserved", "retiring"):
            result["dependencies"] = record.get("descriptor", {}).get("dependencies", [])
            _block(result["blockers"], NAMESPACES, "backup_" + record["state"])
        elif record and record["state"] == "retired":
            try:
                with safe._Directory(path.parent, base=path.parent) as directory:
                    for name in (path.name, path.name + SUFFIX):
                        try:
                            directory.entry_stat(name)
                            _block(result["blockers"], NAMESPACES, "retired_backup_reappeared")
                        except FileNotFoundError:
                            pass
            except (OSError, ValueError):
                _block(result["blockers"], NAMESPACES, "retired_backup_location_unreadable")
        else:
            try:
                descriptor = _verify_ready(record, profile)[0] if record else None
                _, identity = _read(path, maximum=MAX_IMAGE, content=False)
                deps, issues, boundaries, schema, retirements, version = _database_dependencies(path, profile, manifests=descriptor["manifests"] if descriptor else None)
                _, after = _read(path, maximum=MAX_IMAGE, content=False)
                if identity != after:
                    _fail("BACKUP_IMAGE_CHANGED")
                result.update(database_file=identity, dependencies=deps, blockers=issues, checkpoint_boundaries=boundaries,
                              schema_version=schema, retirement_facts=retirements, source_version=version,
                              frozen_manifests=descriptor["manifests"] if descriptor else None)
            except (BackupError, safe.SyncError, OSError, KeyError, sqlite3.Error):
                _block(result["blockers"], NAMESPACES, "backup_unreadable_or_missing")
        found.append(result)
        for namespace, reasons in result["blockers"].items():
            for reason in reasons:
                _block(blockers, (namespace,), reason)
    guard.verify()
    _read_check()
    binding = {"backups": found, "catalog": catalog_fingerprints, "source_roots": _roots(profile)}
    return {"backups": found, "blockers": blockers, "digest": _sha(_json(binding))}


def _retention_receipt(path, request):
    raw, _ = _read(path)
    value = _decode(raw)
    keys = {"format", "operation_id", "kind", "keep", "source_profile", "request_sha256", "selected", "state", "result"}
    if (set(value) != keys or type(value["format"]) is not int or value["format"] != 1
            or value["state"] not in ("running", "completed") or type(value["selected"]) is not list
            or len(value["selected"]) > MAX_BACKUPS or type(value["result"]) is not dict
            or set(value["result"]) != {"retired", "blocked"}):
        _fail("BACKUP_INVALID_RETENTION_RECEIPT")
    if any(value[k] != v for k, v in request.items()) or value["request_sha256"] != _sha(_json(request)):
        _fail("BACKUP_RETIRE_OPERATION_CONFLICT")
    ids = set()
    for item in value["selected"]:
        if (type(item) is not dict or set(item) != {"backup_id", "record_sha256"}
                or type(item["record_sha256"]) is not str or not _SHA.fullmatch(item["record_sha256"])):
            _fail("BACKUP_INVALID_RETENTION_RECEIPT")
        _uuid(item["backup_id"])
        ids.add(item["backup_id"])
    if len(ids) != len(value["selected"]):
        _fail("BACKUP_INVALID_RETENTION_RECEIPT")
    lists = value["result"]
    if (any(type(v) is not list or any(type(x) is not str for x in v) or len(v) != len(set(v)) or not set(v) <= ids for v in lists.values())
            or set(lists["retired"]) & set(lists["blocked"])
            or value["state"] == "completed" and (lists["blocked"] or set(lists["retired"]) != ids)):
        _fail("BACKUP_INVALID_RETENTION_RECEIPT")
    return value


def pending_retention(profile, *, kind="judge", keep=3):
    """Read valid unfinished receipts before a producer starts a new batch.

    Discover under SH, release it, then let the caller resume each exact UUID
    through retain_recent's EX revalidation. Never add new files to a receipt.
    """
    if kind not in KINDS or type(keep) is not int or keep < 1:
        _fail("BACKUP_INVALID_RETENTION")
    with lifecycle.shared(profile) as guard:
        catalog = _catalog(profile)
        operations = catalog / "operations"
        try:
            with safe._Directory(operations, base=operations) as directory:
                names = directory.names()
        except FileNotFoundError:
            # Missing facilities are checked by shared(). A missing operations
            # directory is normal only if it is not a dangling link and no
            # already-published retiring record claims an operation there.
            try:
                with safe._Directory(catalog, base=catalog) as parent:
                    try:
                        parent.entry_stat("operations")
                    except FileNotFoundError:
                        pass
                    else:
                        _fail("BACKUP_INVALID_RETENTION_RECEIPT")
            except FileNotFoundError:
                pass
            if any(r["source_profile"] == _profile_json(profile) and r["kind"] == kind
                   and r["state"] == "retiring" for r, _ in _catalog_entries(profile)):
                _fail("BACKUP_RETENTION_RECEIPT_MISSING")
            return []
        if len(names) > MAX_BACKUPS * 2:
            _fail("BACKUP_TOO_MANY_RECORDS")
        pending, observed = [], set()
        for name in sorted(names):
            if name.startswith(".backup-write-") and name.endswith(".tmp"):
                continue
            if not name.endswith(".json"):
                _fail("BACKUP_INVALID_RETENTION_RECEIPT")
            operation_id = _uuid(name[:-5])
            path = operations / name
            value = _decode(_read(path)[0])
            request = {key: value.get(key) for key in ("operation_id", "kind", "keep", "source_profile")}
            if (request["operation_id"] != operation_id or request["kind"] not in KINDS
                    or type(request["keep"]) is not int or request["keep"] < 1):
                _fail("BACKUP_INVALID_RETENTION_RECEIPT")
            _profile_value(request["source_profile"])
            receipt = _retention_receipt(path, request)
            if request["source_profile"] != _profile_json(profile) or request["kind"] != kind:
                continue
            observed.add(operation_id)
            if receipt["state"] != "completed":
                if request["keep"] != keep:
                    _fail("BACKUP_RETIRE_OPERATION_CONFLICT")
                pending.append(operation_id)
        for record, _ in _catalog_entries(profile):
            if (record["source_profile"] == _profile_json(profile) and record["kind"] == kind
                    and record["state"] == "retiring" and record["retire_operation_id"] not in observed):
                _fail("BACKUP_RETENTION_RECEIPT_MISSING")
        guard.verify()
        return pending


def retain_recent(profile, *, kind="judge", keep=3, operation_id):
    """EX + durable operation/record journal; retries keep the selected set."""
    from . import storage_home_recovery_roots as home_recovery_roots
    from . import storage_home_recovery_operation_holds17 as hold_operations
    operation_id = _uuid(operation_id)
    if kind not in KINDS or type(keep) is not int or keep < 1:
        _fail("BACKUP_INVALID_RETENTION")
    request = {"operation_id": operation_id, "kind": kind, "keep": keep, "source_profile": _profile_json(profile)}
    with lifecycle.exclusive(profile) as guard, ExitStack() as proof_stack:
        catalog = _catalog(profile, create=True)
        operations = catalog / "operations"
        try:
            _mkdir(operations)
        except FileExistsError:
            pass
        receipt_path = operations / (operation_id + ".json")
        try:
            receipt = _retention_receipt(receipt_path, request)
        except FileNotFoundError:
            receipt = None
        records = {r["backup_id"]: r for r, _ in _catalog_entries(profile)}
        destinations = [r["destination"] for r in records.values()]
        if len(destinations) != len(set(destinations)):
            _fail("BACKUP_CATALOG_DESTINATION_CONFLICT")
        records = {k: r for k, r in records.items() if r["source_profile"] == _profile_json(profile)}
        if receipt is None:
            if any(r["state"] == "retiring" and r["kind"] == kind for r in records.values()):
                _fail("BACKUP_RETIRE_OPERATION_CONFLICT")
            ready = sorted((r for r in records.values() if r["state"] == "ready" and r["kind"] == kind),
                           key=lambda r: (r["created_at"], r["backup_id"]), reverse=True)
            selected = [{"backup_id": r["backup_id"], "record_sha256": _sha(_json(r))} for r in ready[keep:]]
            receipt = {"format": 1, **request, "request_sha256": _sha(_json(request)), "selected": selected,
                       "state": "running", "result": {"retired": [], "blocked": []}}
            _write_json(receipt_path, receipt, replace=False)
        result = {"retired": [], "blocked": []}
        operation_holds = None
        try:
            operation_holds = proof_stack.enter_context(hold_operations.capture_operation_holds_locked(profile, records))
            home_holds = home_recovery_roots.retention_holds_locked(profile, records,
                [item["backup_id"] for item in receipt["selected"]], operation_holds=operation_holds)
        except (ValueError, OSError, sqlite3.Error):
            # A remaining unreadable operational image can hide a Home proof
            # edge to any selected backup. Do not infer absence from that image.
            home_holds = {item["backup_id"] for item in receipt["selected"]}
        def verify_holds(*, full=False):
            if operation_holds is None:
                _fail("BACKUP_HOME_RECOVERY_PROOF_CHANGED")
            try:
                if full:
                    operation_holds.verify_source()
                else:
                    operation_holds.verify_for_commit()
            except (ValueError, OSError, safe.SyncError):
                _fail("BACKUP_HOME_RECOVERY_PROOF_CHANGED")
        for selected in receipt["selected"]:
            backup_id = selected["backup_id"]
            record = records.get(backup_id)
            try:
                if backup_id in home_holds:
                    _fail("BACKUP_HOME_RECOVERY_REFERENCED")
                verify_holds(full=True)
                if record is None or record["kind"] != kind:
                    _fail("BACKUP_RETIRE_RECORD_CHANGED")
                target = _path(record["destination"])
                registry = catalog / (backup_id + ".json")
                if record["state"] == "ready":
                    if _sha(_json(record)) != selected["record_sha256"]:
                        _fail("BACKUP_RETIRE_RECORD_CHANGED")
                    descriptor, _ = _verify_ready(record, profile)
                    _, sidecar_identity = _read(target.with_name(target.name + SUFFIX))
                    record = {**record, "state": "retiring", "retire_operation_id": operation_id,
                        "retire_parameters": {"kind": kind, "keep": keep}, "retire_files": [
                            {"path": _key(target), "identity": descriptor["database_file"]},
                            {"path": _key(target.with_name(target.name + SUFFIX)), "identity": sidecar_identity}]}
                    verify_holds()
                    _write_json(registry, record, replace=True)
                elif (record["state"] not in ("retiring", "retired") or record["retire_operation_id"] != operation_id
                        or record["retire_parameters"] != {"kind": kind, "keep": keep}):
                    _fail("BACKUP_RETIRE_OPERATION_CONFLICT")
                for item in record["retire_files"]:
                    path = _path(item["path"])
                    try:
                        _, current = _read(path, maximum=MAX_IMAGE, content=False)
                    except FileNotFoundError:
                        continue
                    if record["state"] == "retired" or current != item["identity"]:
                        _fail("BACKUP_RETIRE_IDENTITY_CHANGED")
                    with safe._Directory(path.parent, base=path.parent) as directory:
                        before = directory.entry_stat(path.name)
                        if (str(before.st_dev), str(before.st_ino), before.st_size, str(before.st_mtime_ns)) != (
                                current["device"], current["inode"], current["byte_size"], current["mtime_ns"]):
                            _fail("BACKUP_RETIRE_IDENTITY_CHANGED")
                        guard.verify()
                        verify_holds()
                        directory.unlink(path.name)
                        if os.name != "nt":
                            os.fsync(directory.fd)
                        directory.verify()
                if record["state"] != "retired":
                    verify_holds()
                    _write_json(registry, {**record, "state": "retired", "retired_at": _stamp()}, replace=True)
                result["retired"].append(backup_id)
            except (OSError, BackupError, safe.SyncError):
                result["blocked"].append(backup_id)
        _write_json(receipt_path, {**receipt, "state": "running" if result["blocked"] else "completed", "result": result}, replace=True)
        if operation_holds is not None:
            operation_holds.verify_source()
        guard.verify()
        return result
