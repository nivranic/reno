"""Complete-group, read-only reference previews; no enrollment or deletion.

Acquire the whole member closure before SQL. Stable images, immutable archives,
Home files, backup catalogs and manifests are held/revalidated as one round.
Only the selected owner may propose retirements or files; other owners' edges
remain roots. Private diagnostics are never serialized by public_preview().
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from . import storage_artifacts as artifacts, storage_backups as backups
from . import storage_checkpoint_roots as checkpoint_roots
from . import storage_backup_roots as backup_roots
from . import storage_personal_preservation_roots as preservation_roots
from . import storage_personal_authority_roots as personal_authority_roots
from . import storage_gc_contract as contract, storage_home_roots as home
from . import storage_lifecycle as lifecycle, storage_members as members
from . import storage_readonly as readonly, storage_root_inputs as inputs
from . import storage_source as source, storage_schema
from . import sync_media as safe, sync_no_resend
from . import home_recovery_schema, storage_home_recovery_roots as home_recovery_roots
from . import storage_source_continuity_roots as continuity_roots
from . import storage_source_authority_roots as authority_roots
from . import sync_source_continuity, sync_source_authority

NAMESPACES = tuple(sorted(artifacts.NAMESPACES))
_ROOT_ROLE = {"sync_media": "media", "local_report": "reports", "sync_report": "reports", "checkpoint": "checkpoints"}
_ARTIFACT_FIELDS = ("artifact_id", "namespace", "root_id", "root_device", "root_inode", "storage_key", "sha256",
                    "byte_size", "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns")
_PROJECTIONS = {
    "meta": ("key", "value"),
    "storage_artifact": _ARTIFACT_FIELDS,
    "storage_gc_operation": ("operation_id", "request_sha256", "preview_sha256", "roots_sha256", "policy_json", "request_json", "state"),
    "storage_gc_item": ("operation_id", *_ARTIFACT_FIELDS[:1], "namespace", "root_id", "storage_key", "sha256", "byte_size",
                        "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns", "state", "deleted_bytes"),
    "sync_checkpoint_retirement": ("origin_kind", "checkpoint_id", "operation_id", "storage_key", "sha256", "byte_size", "retired_at"),
    home_recovery_roots.TABLE: home_recovery_roots.COLUMNS,
    **inputs.CONTINUITY_COLUMNS,
}
# The transaction-time projection must include every SQL input used by the
# image parsers, in addition to the immutable ownership and journal fields.
# Recovery result_json is an actual restore proof; GC result_json, worker
# leases and display timestamps are deliberately outside this projection.
_OWNER_SQL_PROJECTIONS = {}
for _projection in (inputs._COLUMNS, checkpoint_roots._COLUMNS, _PROJECTIONS):
    for _table, _fields in _projection.items():
        _OWNER_SQL_PROJECTIONS[_table] = tuple(dict.fromkeys((*_OWNER_SQL_PROJECTIONS.get(_table, ()), *_fields)))
del _projection, _table, _fields
_CODES = re.compile(r"[A-Za-z0-9_]{1,100}\Z")


class RootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _CallerCancelled(BaseException):
    """Keep cancellation out of filesystem/parser exception translators."""
    def __init__(self, original):
        self.original = original


@dataclass(frozen=True)
class RootLimits:
    max_rows: int = 100_000
    max_cell_bytes: int = contract.MAX_JSON_BYTES
    max_metadata_bytes: int = 128 * 1024 * 1024
    max_snapshot_bytes: int = 512 * 1024 * 1024
    max_inventory_entries: int = 100_000
    # Child readers keep their own bound, also included in the CAS binding.
    # This is the owner-candidate pass, not aggregate I/O from all readers.
    max_candidate_hash_bytes: int = 4 * 1024 * 1024 * 1024
    max_vm_steps: int = 2_000_000
    deadline_seconds: float = 120.0

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError("Invalid root limits")
            elif type(value) is not int or value < 1:
                raise ValueError("Invalid root limits")


def _canonical(value):
    return inputs.canonical_bytes(value)


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _code(exc, fallback="ROOTS_SOURCE_UNAVAILABLE"):
    value = getattr(exc, "code", fallback)
    return value if type(value) is str and _CODES.fullmatch(value) else fallback


def _fail(code, *, retriable=False):
    raise RootError(code, retriable=retriable)


class _Budget:
    def __init__(self, limits, check):
        self.limits, self.callback = limits, check or (lambda: None)
        self.started, self.rows, self.metadata, self.snapshots, self.entries, self.hash_bytes = time.monotonic(), 0, 0, 0, 0, 0
        self.cancelled = None

    def check(self):
        if self.cancelled is not None:
            raise _CallerCancelled(self.cancelled)
        try:
            self.callback()
        except BaseException as exc:
            self.cancelled = exc
            raise _CallerCancelled(exc) from None
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            _fail("ROOTS_DEADLINE_LIMIT")

    def propagate_cancelled(self):
        if self.cancelled is not None:
            raise _CallerCancelled(self.cancelled) from None

    def consume(self, name, amount, maximum):
        self.check()
        value = getattr(self, name) + amount
        if value > maximum:
            _fail("ROOTS_" + name.upper() + "_LIMIT")
        setattr(self, name, value)

    def projection(self, scan):
        # Each component is bounded independently. Charge the group before
        # retaining another component, so many members cannot evade its cap.
        for field in ("references", "selectors", "blockers", "source_bindings"):
            for item in getattr(scan, field):
                self.consume("rows", 1, self.limits.max_rows)
                self.consume("metadata", len(_canonical(asdict(item))), self.limits.max_metadata_bytes)
        return scan

    def binding(self, value):
        self.consume("metadata", len(_canonical(value)), self.limits.max_metadata_bytes)
        return value


class _Queries:
    """Only fixed ordinary-table projections from a private committed image."""
    def __init__(self, con, budget):
        self.con, self.budget, self.steps, self.interrupted = con, budget, 0, False
        self.provenance17 = False

    def authorize(self, action, first, second, schema, origin):
        provenance = inputs.provenance_authorize(action, first, second, schema, origin, enabled=self.provenance17)
        if provenance is not None:
            return provenance
        continuity = inputs.continuity_authorize(action, first, second, schema, origin)
        if continuity is not None:
            return continuity
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and first == "sqlite_temp_master" and second == "name":
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second == "lower":
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and origin is None:
            if first in ("sqlite_master", "sqlite_schema"):
                return sqlite3.SQLITE_OK
            if second in _PROJECTIONS.get(first, ()):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def progress(self):
        self.steps += 1000
        try:
            self.budget.check()
        except BaseException:
            self.interrupted = True
            return 1
        if (self.steps > self.budget.limits.max_vm_steps
                or time.monotonic() - self.budget.started > self.budget.limits.deadline_seconds):
            self.interrupted = True
            return 1
        return 0

    def __enter__(self):
        self.before = self.con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
        self.con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, min(self.before, self.budget.limits.max_cell_bytes))
        self.con.set_authorizer(self.authorize)
        self.con.set_progress_handler(self.progress, 1000)
        return self

    def __exit__(self, *_):
        self.con.set_progress_handler(None, 0)
        self.con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self.before)

    def rows(self, table):
        self.budget.check()
        try:
            schema = self.con.execute("SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (table,)).fetchall()
            if (len(schema) != 1 or schema[0][0] != "table" or type(schema[0][1]) is not str
                    or "CREATE VIRTUAL TABLE" in " ".join(schema[0][1].upper().split())):
                _fail("ROOTS_TABLE_UNSAFE")
            fields = _PROJECTIONS[table]
            columns = {row[1]: tuple(row) for row in self.con.execute(f'PRAGMA table_xinfo("{table}")')}
            for field in fields:
                if field not in columns or columns[field][6] != 0:
                    _fail("ROOTS_COLUMNS_UNSAFE")
            if table == home_recovery_roots.TABLE:
                home_recovery_schema.validate(self.con)
            elif table in inputs.CONTINUITY_COLUMNS:
                inputs.continuity_schema.validate(self.con)
            elif table != "meta":
                expected = contract._storage_contracts()[table]
                if storage_schema._contract(self.con, table) != expected:
                    _fail("ROOTS_STORAGE_SCHEMA_UNSAFE")
            query = f"SELECT {','.join(fields)} FROM {table}"
            if table == "meta":
                query += " WHERE key IN ('migration_version','library_id','library_epoch','kb_revision')"
            result = []
            for row in self.con.execute(query):
                self.budget.consume("rows", 1, self.budget.limits.max_rows)
                value = dict(zip(fields, tuple(row)))
                if any(item is not None and type(item) not in (str, int) for item in value.values()):
                    _fail("ROOTS_CELL_INVALID")
                raw = _canonical(value)
                self.budget.consume("metadata", len(raw), self.budget.limits.max_metadata_bytes)
                result.append(value)
            return sorted(result, key=_canonical)
        except sqlite3.Error:
            self.budget.propagate_cancelled()
            _fail("ROOTS_QUERY_LIMIT" if self.interrupted else "ROOTS_TABLE_UNSAFE")


class _OwnerSQLQueries(_Queries):
    """Fixed projections only, without executing an unsafe table or view."""
    def __init__(self, con, budget):
        super().__init__(con, budget)
        self.projections = {table: fields for table, fields in _OWNER_SQL_PROJECTIONS.items()
                            if table not in inputs.CONTINUITY_COLUMNS}

    def __exit__(self, *_):
        # owner_sql_sha256 restores every hook even after a partial __enter__;
        # cleanup failures there cannot replace the caller's primary exception.
        return None

    def authorize(self, action, first, second, schema, origin):
        provenance = inputs.provenance_authorize(action, first, second, schema, origin, enabled=self.provenance17)
        if provenance is not None:
            return provenance
        continuity = inputs.continuity_authorize(action, first, second, schema, origin)
        if continuity is not None:
            return continuity
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and origin is None:
            if first in ("sqlite_master", "sqlite_schema") and second in ("name", "type", "sql", "tbl_name"):
                return sqlite3.SQLITE_OK
            if second in self.projections.get(first, ()):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second in self.projections:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _row(self, row):
        self.budget.consume("rows", 1, self.budget.limits.max_rows)
        result = []
        for value in row:
            if value is None or type(value) in (str, int):
                encoded = value
            elif type(value) is bytes:
                # Bad cells still need a stable CAS binding. The corresponding
                # parser fences their namespace; never serialize their bytes.
                encoded = {"sql_blob_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            elif type(value) is float:
                encoded = {"sql_float": value.hex()}
            else:
                _fail("ROOTS_CELL_INVALID")
            size = len(value) if type(value) is bytes else len(_canonical(encoded))
            if size > self.budget.limits.max_cell_bytes:
                _fail("ROOTS_CELL_LIMIT")
            self.budget.consume("metadata", size, self.budget.limits.max_metadata_bytes)
            result.append(encoded)
        return result

    def projection(self, table):
        self.budget.check()
        try:
            schema = [self._row(row) for row in self.con.execute(
                "SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (table,))]
            # A trigger added between capture and BEGIN can change the journal
            # INSERT's behavior without changing the ordinary table's SQL.
            auxiliary = sorted((self._row(row) for row in self.con.execute(
                "SELECT name,type,sql,tbl_name FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger')",
                (table,))), key=_canonical)
            result = {"schema": schema, "auxiliary": auxiliary, "columns": [], "rows": []}
            if (len(schema) != 1 or schema[0][0] != "table" or type(schema[0][1]) is not str
                    or not " ".join(schema[0][1].upper().split()).startswith("CREATE TABLE ")
                    or "CREATE VIRTUAL TABLE" in " ".join(schema[0][1].upper().split())):
                # Bind the unsafe schema, but do not read from it. The normal
                # image parser separately blocks every namespace it can touch.
                result["status"] = "unsafe_table"
                return result
            columns = [self._row(row) for row in self.con.execute(f'PRAGMA table_xinfo("{table}")')]
            result["columns"] = columns
            by_name = {row[1]: row for row in columns if type(row[1]) is str}
            fields = self.projections[table]
            if any(field not in by_name or by_name[field][6] != 0 for field in fields):
                result["status"] = "unsafe_columns"
                return result
            query = f'SELECT {",".join(fields)} FROM "{table}"'
            if table == "meta":
                query += " WHERE key IN ('migration_version','library_id','library_epoch','kb_revision')"
            result["rows"] = sorted((self._row(row) for row in self.con.execute(query)), key=_canonical)
            result["status"] = "ordinary"
            return result
        except sqlite3.Error:
            self.budget.propagate_cancelled()
            _fail("ROOTS_QUERY_LIMIT" if self.interrupted else "ROOTS_TABLE_UNSAFE")


def owner_sql_sha256(con, *, check_cancelled=None, limits=None):
    """Hash the bounded SQL reference graph on this exact connection.

    Used both on the private committed image and inside the GC owner's short
    BEGIN IMMEDIATE transaction. No file reads, config lookup or new live SQL
    connection occur here. This internal API owns authorizer/progress hooks;
    callers supply a dedicated image or their own GC writer, never a borrowed
    connection with an unknown prior policy. Hooks are cleared on exit so the
    GC writer can commit its journal; row_factory/length limits are restored.
    """
    limits = RootLimits() if limits is None else limits
    if (not isinstance(con, sqlite3.Connection) or not isinstance(limits, RootLimits)
            or check_cancelled is not None and not callable(check_cancelled)):
        raise ValueError("Invalid owner SQL projection options")
    budget = _Budget(limits, check_cancelled)
    before_row = con.row_factory
    before_length = con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    primary = None
    con.row_factory = None
    try:
        with _OwnerSQLQueries(con, budget) as queries:
            # Preserve the exact legacy table/hash shape when no future name
            # exists. Schema17 adds its actual public rows and producer facts;
            # an old-schema future-name collision is bound only when observed.
            metadata = queries.projection("meta")
            versions = [row[1] for row in metadata["rows"] if len(row) == 2 and row[0] == "migration_version"]
            queries.provenance17 = versions == ["17"]
            if queries.provenance17:
                queries.projections.update(inputs.CONTINUITY_COLUMNS)
                for table, fields in inputs.PROVENANCE_COLUMNS_V17.items():
                    queries.projections[table] = tuple(dict.fromkeys((*queries.projections.get(table, ()), *fields)))
                queries.projections["job"] = tuple(dict.fromkeys((*queries.projections["job"], *inputs.PUBLIC_JOB_COLUMNS_V17)))
            values = {table: metadata if table == "meta" else queries.projection(table)
                      for table in sorted(queries.projections)}
            if not queries.provenance17:
                reserved = inputs.continuity_reserved_projection(con)
                if reserved:
                    for row in reserved:
                        budget.consume("rows", 1, limits.max_rows)
                        budget.consume("metadata", len(_canonical(row)), limits.max_metadata_bytes)
                    values["reserved_source_continuity"] = reserved
            budget.check()
            return _digest({"format_version": 1, "tables": values})
    except _CallerCancelled as exc:
        primary = exc.original
        raise exc.original from None
    except BaseException as exc:
        primary = exc
        raise
    finally:
        failures = []
        for restore in (lambda: con.set_progress_handler(None, 0), lambda: con.set_authorizer(None),
                        lambda: con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, before_length),
                        lambda: setattr(con, "row_factory", before_row)):
            try:
                restore()
            except BaseException as exc:
                failures.append(exc)
        if failures:
            if primary is None:
                raise failures[0]
            primary.add_note("ROOTS_OWNER_SQL_CLEANUP_FAILED")


def _version(rows, library_id):
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows) or set(values) != {"migration_version", "library_id", "library_epoch", "kb_revision"}:
        _fail("ROOTS_VERSION_INVALID")
    try:
        if values["library_id"] != library_id:
            _fail("ROOTS_LIBRARY_CHANGED")
        contract._uuid(values["library_id"])
        if values["migration_version"] not in {"16", "17"}:
            _fail("ROOTS_SCHEMA_UNSUPPORTED")
        result = {"library_id": library_id}
        for key, minimum in (("library_epoch", 1), ("kb_revision", 0)):
            value = values[key]
            if type(value) is not str or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
                _fail("ROOTS_VERSION_INVALID")
            number = int(value)
            contract._integer(number, minimum)
            result[key] = number
        return result
    except contract.GCContractError:
        _fail("ROOTS_VERSION_INVALID")


@dataclass(frozen=True)
class _FileObservation:
    path: Path
    base: Path
    maximum: int
    raw: bytes | None
    fingerprint: object
    code: str | None

    def verify(self, budget):
        again = _observe(self.path, self.base, self.maximum, budget)
        if (again.fingerprint, again.code) != (self.fingerprint, self.code):
            _fail("ROOTS_SOURCE_CHANGED", retriable=True)


def _observe(path, base, maximum, budget):
    budget.check()
    try:
        with safe._Directory(path.parent, base=base) as directory:
            try:
                before = source.file_signature(directory.entry_stat(path.name))
            except FileNotFoundError:
                return _FileObservation(path, base, maximum, None, None, None)
            if before[2] > maximum:
                _fail("ROOTS_FILE_TOO_LARGE")
            budget.consume("metadata", before[2], budget.limits.max_metadata_bytes)
            with directory.open_file(path.name) as stream:
                opened = source.file_signature(os.fstat(stream.fileno()))
                if opened[:4] != before[:4]:
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
                chunks, length = [], 0
                while True:
                    budget.check()
                    chunk = stream.read(128 * 1024)
                    if not chunk:
                        break
                    length += len(chunk)
                    if length > maximum or length > before[2]:
                        _fail("ROOTS_SOURCE_CHANGED", retriable=True)
                    chunks.append(chunk)
                after = source.file_signature(os.fstat(stream.fileno()))
                if after != opened or length != before[2]:
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
            if source.file_signature(directory.entry_stat(path.name)) != before:
                _fail("ROOTS_SOURCE_CHANGED", retriable=True)
            directory.verify()
            raw = b"".join(chunks)
            return _FileObservation(path, base, maximum, raw, (before, hashlib.sha256(raw).hexdigest()), None)
    except RootError as exc:
        if exc.retriable or exc.code != "ROOTS_FILE_TOO_LARGE":
            raise
        return _FileObservation(path, base, maximum, None, None, exc.code)
    except (OSError, safe.SyncError, source.SourceReadError) as exc:
        budget.propagate_cancelled()
        return _FileObservation(path, base, maximum, None, None, "ROOTS_FILE_UNAVAILABLE")


def _node(observation):
    if observation.code or observation.raw is None:
        return None
    try:
        value = contract.decode(observation.raw)
        contract._exact(value, {"instance_id"})
        return contract._uuid(value["instance_id"])
    except contract.GCContractError:
        return None


def _join(scans):
    def union(field):
        return tuple(sorted({item for scan in scans for item in getattr(scan, field)}, key=lambda item: _canonical(asdict(item))))
    return inputs.InputScan(*(union(field) for field in ("references", "selectors", "blockers", "source_bindings")))


def _blocked(root_ids, member_id, namespaces, code, kind="live", source_id="unavailable"):
    return inputs.InputScan(blockers=tuple(inputs.Blocker(ns, root_ids[ns], member_id, kind, _digest(source_id), code, _digest(code))
                                           for ns in sorted(set(namespaces))))


def _physical(namespace, root_id, key):
    # Windows ID spelling is not a separate physical path. Ownership remains
    # per owner, while reference union and space accounting use this key.
    return namespace, root_id, key.casefold() if os.name == "nt" else key


def _protection(profiles):
    """Private physical exclusions, independent of a claimed artifact owner."""
    from . import home_recovery_source, home_recovery, home_recovery17, source_preservation_bundle, sync_state
    from . import sync_source_legacy_pending_state17, personal_mirror_transport
    exact, directories = set(), set()
    for profile in profiles:
        data = profile.database.parent
        for suffix in ("", "-wal", "-shm", "-journal", backups.SUFFIX):
            exact.add(members._key(profile.database.with_name(profile.database.name + suffix)))
        for name in ("config.json", "config.local.json", "node-identity.json", "sync-state.json", "dy_cookies.json",
                     sync_no_resend.WITNESS, sync_no_resend.SEAL,
                     sync_source_continuity.INTENT_NAME, sync_source_continuity.DB_HEAD_NAME, sync_source_continuity.LOCK_NAME,
                     sync_source_authority.INTENT_NAME, sync_source_authority.DB_HEAD_NAME, sync_state._LOCK):
            exact.add(members._key(data / name))
        # Entire evidence trees remain excluded after logical retirement,
        # including v2 mappings, immutable histories and ledger companions.
        for name in ("personal-attachments", "sync-outbox", "sync-work-history", "sync-sources", sync_no_resend.DIRECTORY, backups.CATALOG,
                     personal_mirror_transport.ROOT,
                     preservation_roots.CATALOG, preservation_roots.BUNDLES,
                     personal_authority_roots.RAW_PRESERVATION_DIRECTORY,
                     personal_authority_roots.RAW_PRESERVATION2_DIRECTORY,
                     personal_authority_roots.REPLICA_DIRECTORY,
                     personal_authority_roots.HANDOFF_DIRECTORY,
                     sync_source_continuity.DIRECTORY, sync_source_authority.DIRECTORY,
                     home_recovery_source.DIRECTORY, home_recovery.INTENT_DIRECTORY, home_recovery17.DIRECTORY,
                      source_preservation_bundle.OPERATIONS, source_preservation_bundle.DIRECTORY,
                      sync_source_legacy_pending_state17.DIRECTORY):
            directories.add(members._key(data / name))
        for name in (sync_source_continuity.SEAL_NAME, sync_source_continuity.CHECKPOINT_HEAD_NAME,
                     sync_source_authority.SEAL_NAME, sync_source_authority.CHECKPOINT_HEAD_NAME):
            exact.add(members._key(profile.checkpoints / name))
        # POSIX GC stages only its already journaled frozen files here. A
        # later preview must never enroll or propose those recovery bytes.
        for root in (profile.media, profile.reports, profile.checkpoints):
            directories.add(members._key(root / ".reno-gc-stage-v1"))
    return exact, directories


def _protected_path(path, exact, directories):
    path = members._key(path)
    return path in exact or any(path == root or path.startswith(root + os.sep) for root in directories)


def _protected(item, exact, directories, inodes):
    path = members._key(getattr(item["profile"], _ROOT_ROLE[item["namespace"]]) / item["storage_key"])
    return _protected_path(path, exact, directories) or (item["file_device"], item["file_inode"]) in inodes


def _protected_inodes(paths, budget):
    identities = set()
    for path in sorted(paths):
        budget.check()
        if path.endswith("-shm"):
            # The exact source SHM path is excluded; never stat/read its bytes.
            continue
        try:
            info = os.lstat(path)
            if stat.S_ISREG(info.st_mode):
                identities.add((str(info.st_dev), str(info.st_ino)))
        except OSError:
            # Missing/unreadable special files still retain lexical protection.
            pass
    return identities


def _verify_artifact_identity(item, budget):
    """Do not let a change between SHA and inventory become a fresh baseline."""
    budget.check()
    root = getattr(item["profile"], _ROOT_ROLE[item["namespace"]])
    path = root / item["storage_key"]
    try:
        with safe._Directory(path.parent, base=root) as directory:
            before = directory.entry_stat(path.name)
            safe._safe_stat(before, directory=False)
            with directory.open_file(path.name) as stream:
                opened = os.fstat(stream.fileno())
                actual = {"file_device": str(opened.st_dev), "file_inode": str(opened.st_ino),
                          "file_mtime_ns": str(opened.st_mtime_ns), "file_ctime_ns": str(opened.st_ctime_ns),
                          "byte_size": opened.st_size}
                if opened.st_nlink != 1 or any(actual[key] != item[key] for key in actual):
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
                cross = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
                if cross(before) != cross(opened) or cross(directory.entry_stat(path.name)) != cross(opened):
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
                directory.verify()
    except (OSError, safe.SyncError):
        _fail("ROOTS_SOURCE_CHANGED", retriable=True)
    budget.check()


def _referenced(file, scan):
    position = _physical(file["namespace"], file["root_id"], file["storage_key"])
    if any(_physical(ref.namespace, ref.root_id, ref.storage_key) == position for ref in scan.references):
        return True
    if file["namespace"] == "sync_media":
        _, video, name = file["storage_key"].split("/")
        sha, extension = name.rsplit(".", 1)
        for selector in scan.selectors:
            selected_video = selector.video_id.casefold() if os.name == "nt" else selector.video_id
            actual_video = video.casefold() if os.name == "nt" else video
            if (selector.root_id == file["root_id"] and selector.namespace == "sync_media"
                    and selected_video == actual_video and (selector.sha256 is None or selector.sha256 == sha)
                    and (selector.extension is None or selector.extension == extension)):
                return True
    return False


def _inventory(profile, namespace, budget, protected_paths=(), protected_directories=()):
    root = getattr(profile, _ROOT_ROLE[namespace])
    start = root if namespace == "checkpoint" else root / {
        "sync_media": "sync", "local_report": ".versions", "sync_report": ".sync-versions"}[namespace]
    observed, keys = [], set()
    def visit(path, depth):
        try:
            directory = safe._Directory(path, base=root).__enter__()
        except FileNotFoundError:
            observed.append((path.relative_to(root).as_posix(), "absent"))
            return
        with _opened_directory(directory):
            observed.append((path.relative_to(root).as_posix(), "directory", tuple((str(p), d, i) for p, d, i in directory.identities)))
            entries = []
            with os.scandir(directory.fd if os.name != "nt" else safe._native_path(path)) as iterator:
                for item in iterator:
                    budget.consume("entries", 1, budget.limits.max_inventory_entries)
                    entries.append(item.name)
            for name in sorted(entries):
                if _protected_path(path / name, protected_paths, protected_directories):
                    continue
                try:
                    info = directory.entry_stat(name)
                except FileNotFoundError:
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
                key = (path / name).relative_to(root).as_posix()
                observed.append((key, info.st_mode, str(info.st_dev), str(info.st_ino), info.st_size, str(info.st_mtime_ns), str(info.st_ctime_ns)))
                if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and namespace == "sync_media" and depth == 0:
                    try:
                        safe._safe_stat(info, directory=True)
                        visit(path / name, depth + 1)
                    except (OSError, safe.SyncError):
                        observed.append((key, "unsafe_directory"))
                elif stat.S_ISREG(info.st_mode):
                    try:
                        artifacts.validate_storage_key(namespace, key)
                        keys.add(key.casefold() if os.name == "nt" else key)
                    except safe.SyncError:
                        pass
            directory.verify()
    try:
        visit(start, 0)
        return tuple(observed), keys, None
    except (OSError, safe.SyncError) as exc:
        budget.propagate_cancelled()
        return tuple(observed), keys, "ROOTS_INVENTORY_UNAVAILABLE"


@contextmanager
def _opened_directory(directory):
    """Close an already acquired no-follow directory without entering twice."""
    try:
        yield directory
    finally:
        directory.__exit__(None, None, None)


@dataclass(frozen=True)
class RootScan:
    owner: dict
    policy: dict
    limits: dict
    members_binding: dict
    base_inputs: inputs.InputScan
    effective_inputs: inputs.InputScan
    proposed_retirements: tuple
    files: tuple
    eligible_artifacts: tuple
    unknown_counts: dict
    source_bindings: tuple
    roots_sha256: str
    preview_sha256: str
    owner_sql_sha256: str | None


class RootCapture:
    def __init__(self, scan, verify):
        self.scan, self._verify, self._active = scan, verify, True

    def verify_source(self):
        if not self._active:
            _fail("ROOTS_CAPTURE_CLOSED")
        self._verify()
        return self.scan.roots_sha256


def _root_ids(profile):
    return {ns: artifacts.root_identity(getattr(profile, _ROOT_ROLE[ns]), profile=profile) for ns in NAMESPACES}


def _permanent_presence(profile, facility, budget, stack):
    """Pin actual presence independently of State format and mutable pointers."""
    data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
    checkpoint = stack.enter_context(safe._Directory(profile.checkpoints, base=profile.checkpoints))
    paths = [(data, facility.DIRECTORY), (data, facility.INTENT_NAME), (data, facility.DB_HEAD_NAME),
             (checkpoint, facility.SEAL_NAME), (checkpoint, facility.CHECKPOINT_HEAD_NAME)]
    if facility is sync_source_continuity:
        paths.append((data, facility.LOCK_NAME))
    def observe():
        found = []
        for directory, name in paths:
            budget.check()
            directory.verify()
            try:
                info = directory.entry_stat(name)
                found.append((info.st_dev, info.st_ino, info.st_mode, info.st_ctime_ns))
            except FileNotFoundError:
                found.append(None)
        return tuple(found)
    initial = observe()
    def verify():
        if observe() != initial:
            _fail("ROOTS_SOURCE_CHANGED", retriable=True)
    return any(value is not None for value in initial), verify


def _manual_proofs(con, projection, provenance, library_id, budget):
    """Resolve actual manual receipts with the original origin/defer lineage.

    Only public job scalars and the real task_request receipt are read. The
    derived identity uses the tasks writer's exact canonical payload algorithm;
    this observation is a retained provenance edge, never dispatch permission.
    """
    from . import tasks
    if type(provenance) is not dict:
        return {}
    origins, result = {}, {}
    for row in projection["references"]:
        if row["kind"] == "origins" and row["state"] == "committed":
            for edge in row["refs"]:
                if edge.get("kind") == "source_core":
                    origins.setdefault(row["core_sha256"], []).append(edge)
    receipts = {row["request_id"]: row for row in provenance["task_request"]}
    with _Queries(con, budget) as queries:
        queries.provenance17 = True
        metadata = con.execute("SELECT type,sql FROM main.sqlite_master WHERE name='job' COLLATE NOCASE LIMIT 2").fetchall()
        if (len(metadata) != 1 or metadata[0][0] != "table" or type(metadata[0][1]) is not str
                or not " ".join(metadata[0][1].upper().split()).startswith("CREATE TABLE ")):
            return {}
        columns = {raw[1]: tuple(raw) for raw in con.execute('PRAGMA main.table_xinfo("job")')}
        if (any(name not in columns or columns[name][6] or columns[name][2].upper() !=
                ("INTEGER" if name in {"job_id", "execution_generation"} else "TEXT")
                for name in inputs.PUBLIC_JOB_COLUMNS_V17) or columns["job_id"][5] != 1):
            return {}
        for row in projection["references"]:
            budget.check()
            if row["state"] != "committed":
                continue
            # The proven origin scope is fixed before later caller callbacks.
            # A detached request projection cannot move its real SQL receipt
            # to another source merely by changing this borrowed dictionary.
            selected_core_sha256 = row["core_sha256"]
            cores = origins.get(selected_core_sha256, [])
            if len(cores) != 1:
                continue
            core = cores[0]
            manual = [edge for edge in row["refs"] if edge.get("kind") == "manual_resume"]
            for edge in row["refs"]:
                if edge.get("producer") != "sync_budget_resume":
                    continue
                # The wire authorization hashes its canonical payload. The
                # provenance hashes the complete original four-column row;
                # the producer's timestamp makes these different documents.
                expected = [value for value in manual if value.get("authorization_id") == edge["operation_id"]]
                receipt = receipts.get(edge["operation_id"])
                if (len(expected) != 1 or receipt is None or receipt["job_id"] != core["root_job_id"]
                        or receipt["payload_signature"] != expected[0].get("authorization_sha256")
                        or set(receipt) != {"request_id", "job_id", "payload_signature", "created_at"}
                        or sync_source_authority.digest(receipt) != edge["receipt_sha256"]):
                    continue
                fields = ("request_id", "job_id", "payload_signature", "created_at")
                actual = con.execute('SELECT ' + ','.join(fields) + ' FROM main.task_request WHERE request_id=? LIMIT 2',
                    (receipt["request_id"],)).fetchall()
                for raw in actual:
                    budget.consume("rows", 1, budget.limits.max_rows)
                    budget.consume("metadata", len(_canonical(tuple(raw))), budget.limits.max_metadata_bytes)
                if len(actual) != 1 or dict(zip(fields, actual[0])) != receipt:
                    continue
                # Later budget callbacks may change the detached provenance
                # dictionary. Keep the actual SQLite values privately; every
                # returned proof must name this observed row and timestamp.
                actual_receipt = dict(zip(fields, actual[0]))
                defer = expected[0]["defer_request_id"]
                if not any(value.get("kind") == "control_receipt" and value.get("original_kind") == "budget_defer"
                        and value.get("request_id") == defer for value in row["refs"]):
                    continue
                fields = inputs.PUBLIC_JOB_COLUMNS_V17
                found = con.execute('SELECT ' + ','.join(fields) + ' FROM main.job WHERE job_id=? LIMIT 2',
                                    (core["root_job_id"],)).fetchall()
                for raw in found:
                    budget.consume("rows", 1, budget.limits.max_rows)
                    budget.consume("metadata", len(_canonical(tuple(raw))), budget.limits.max_metadata_bytes)
                if len(found) != 1:
                    continue
                job = dict(zip(fields, found[0]))
                if (job["kind"] != "sync" or job["scope_key"] != "sync:cycle" or job["video_id"] is not None
                        or type(job["job_id"]) is not int or type(job["execution_generation"]) is not int
                        or job["continuation_id"] != core["continuation_id"]
                        or job["execution_generation"] != core["execution_generation"]):
                    continue
                body = {"format_version": 1, "action": "sync_budget_resume", "library_id": library_id,
                    "job_id": core["root_job_id"], "continuation_id": core["continuation_id"],
                    "execution_generation": core["execution_generation"], "defer_request_id": defer}
                request_id = str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "sync-budget-resume:v1:" + defer))
                signature = hashlib.sha256(tasks._canonical(body).encode("utf-8")).hexdigest()
                if request_id == actual_receipt["request_id"] and signature == actual_receipt["payload_signature"]:
                    result[(selected_core_sha256, actual_receipt["request_id"])] = sync_source_authority.digest(actual_receipt)
    return result


def _scan(profiles, owner_profile, policy, limits, check, stack, authority_group=None):
    budget = _Budget(limits, check)
    budget.check()
    for profile in profiles:
        lifecycle.require_guard(profile)
    membership = members.snapshot_locked(profiles)
    expanded = tuple(members._profile(p) for p in membership["profiles"])
    guard = lifecycle.current_guard()
    if any(not guard.allows(p) for p in expanded):
        _fail("ROOTS_GROUP_INCOMPLETE")
    if owner_profile.identity not in {p.identity for p in expanded}:
        _fail("ROOTS_OWNER_OUTSIDE_GROUP")
    records = {members._profile(record["profile"]).identity: record for record in membership["members"]}
    unavailable = {members._key(item["database"]): item["code"] for item in membership["unavailable_members"]}
    if any("SOURCE_CHANGED" in code for code in unavailable.values()):
        _fail("ROOTS_SOURCE_CHANGED", retriable=True)
    root_info = {p.identity: _root_ids(p) for p in expanded}
    base, effective, verify, sources, owned, graphs = [], [], [], [], [], {}
    protected_paths, protected_directories = _protection(expanded)
    archive_cache = checkpoint_roots.ArchiveCache()
    def add(scan):
        base.append(budget.projection(scan))
    def bind(value):
        sources.append(budget.binding(value))
    owner = records.get(owner_profile.identity)
    owner_sql = None
    if owner is None:
        _fail("ROOTS_OWNER_UNAVAILABLE")
    # This independent capture contains only permanent authority objects, not
    # the SQL image or candidates that a subsequent GC operation may change.
    if authority_group is None:
        authority_group = stack.enter_context(personal_authority_roots.capture_group_locked(
            expanded, check_cancelled=budget.check))
    personal_authority_roots.verify_group(authority_group, profiles=expanded)
    authority_inodes = set()
    authority_unknown = False
    for profile in expanded:
        observed = personal_authority_roots.AuthorityProtectionGroup.capture_for(authority_group, profile)
        record = records.get(profile.identity)
        member_id = record["member_id"] if record else "mem_" + _digest(profile.identity)
        add(replace(observed.inputs, source_bindings=tuple(
            replace(item, member_id=member_id) for item in observed.inputs.source_bindings),
            blockers=tuple(replace(item, member_id=member_id) for item in observed.inputs.blockers)))
        protected_paths.update(members._key(path) for path in observed.protected_paths)
        protected_directories.update(members._key(path) for path in observed.protected_directories)
        authority_inodes.update(observed.protected_inodes)
        bind({"member_id": member_id, "personal_authority": observed.source_binding})
        authority_unknown = authority_unknown or not observed.complete
    if authority_unknown:
        # An unreadable tree can hide an inode alias in another member's root,
        # even when the two artifact-root identities differ.
        for profile in expanded:
            record = records.get(profile.identity)
            ids = {ns: info["root_id"] for ns, info in root_info[profile.identity].items()}
            add(_blocked(ids, record["member_id"] if record else "membership", NAMESPACES,
                         "ROOTS_PERSONAL_AUTHORITY_UNPROVEN", kind="personal_authority"))
    verify.append(lambda: personal_authority_roots.verify_group(authority_group, profiles=expanded))
    for root in membership["roots"]:
        physical_id = artifacts._root_record(root["root"]["device"], root["root"]["inode"])["root_id"]
        for reason in root["blockers"]:
            for ns in root["namespaces"]:
                base.append(inputs.InputScan(blockers=(inputs.Blocker(ns, physical_id, "membership", "live", _digest(root["root"]),
                                                                     reason, _digest(reason)),)))
    for profile in expanded:
        budget.check()
        record = records.get(profile.identity)
        ids = {ns: info["root_id"] for ns, info in root_info[profile.identity].items()}
        member_id = record["member_id"] if record else "mem_" + _digest(profile.identity)
        if members._key(profile.database) in unavailable or record is None:
            base.append(_blocked(ids, member_id, NAMESPACES, unavailable.get(members._key(profile.database), "ROOTS_MEMBER_UNAVAILABLE")))
            continue
        try:
            remaining = limits.max_snapshot_bytes - budget.snapshots
            if remaining < 1:
                _fail("ROOTS_SNAPSHOTS_LIMIT")
            image = stack.enter_context(readonly.committed_image_locked(profile, max_bytes=min(readonly.DEFAULT_MAX_BYTES, remaining),
                                                                        check_cancelled=budget.check))
            fingerprints = image.source_fingerprint["files"]
            budget.consume("snapshots", sum(item["byte_size"] for item in fingerprints.values() if item), limits.max_snapshot_bytes)
            if any(str(fingerprints[""][part]) != record["database_identity"][part] for part in ("device", "inode")):
                _fail("ROOTS_LIBRARY_CHANGED")
        except (RootError, readonly.ReadonlyImageError, OSError) as exc:
            budget.propagate_cancelled()
            if getattr(exc, "code", None) == "SOURCE_CHANGED":
                _fail("ROOTS_SOURCE_CHANGED", retriable=True)
            base.append(_blocked(ids, member_id, NAMESPACES, _code(exc)))
            continue
        verify.append(image.verify_source)
        if profile.identity == owner_profile.identity:
            owner_sql = owner_sql_sha256(image.connection, limits=limits, check_cancelled=budget.check)
        metadata, artifact_rows, journal, home_receipts, schema17 = None, [], {}, [], False
        actual_schema, provenance = 16, None
        try:
            with _Queries(image.connection, budget) as queries:
                metadata_rows = queries.rows("meta")
                metadata = _version(metadata_rows, record["library_id"])
                schema17 = any(row == {"key": "migration_version", "value": "17"} for row in metadata_rows)
                actual_schema = 17 if schema17 else 16
                queries.provenance17 = schema17
                artifact_rows = queries.rows("storage_artifact")
                journal = {table: queries.rows(table) for table in ("storage_gc_operation", "storage_gc_item", "sync_checkpoint_retirement")}
                home_receipts = home_recovery_roots.normalize_projection(queries.rows(home_recovery_roots.TABLE),
                    schema_version=actual_schema)
        except (RootError, home_recovery_roots.HomeRecoveryRootError) as exc:
            budget.propagate_cancelled()
            base.append(_blocked(ids, member_id, NAMESPACES, exc.code))
        if metadata is not None and schema17:
            def consume_provenance(row):
                budget.consume("rows", 1, limits.max_rows)
                budget.consume("metadata", len(_canonical(tuple(row))), limits.max_metadata_bytes)
            try:
                with _Queries(image.connection, budget) as queries:
                    queries.provenance17 = True
                    provenance = inputs.read_provenance_projection17(image.connection,
                        check_cancelled=budget.check, consume_row=consume_provenance)
            except (inputs._Invalid, RootError, sqlite3.Error, ValueError, TypeError, KeyError):
                budget.propagate_cancelled()
                base.append(_blocked(ids, member_id, NAMESPACES, "ROOTS_SOURCE_PROVENANCE_UNKNOWN"))
        bind({"member_id": member_id, "version": metadata, "artifacts": artifact_rows, "journal": journal})
        own_ids = set()
        for row in artifact_rows:
            item = {**row, "member_id": member_id, "profile": profile, "status": "unverified"}
            try:
                contract._file({**row, "intent": "retain"})
                if (row["artifact_id"] in own_ids or row["root_id"] != ids[row["namespace"]]
                        or any(row[part] != root_info[profile.identity][row["namespace"]][part] for part in ("root_device", "root_inode"))):
                    _fail("ROOTS_ARTIFACT_INVALID")
                own_ids.add(row["artifact_id"])
            except (contract.GCContractError, RootError, KeyError, TypeError):
                item["status"] = "invalid"
            owned.append(item)
        add(inputs.parse_image(image.connection, profile=profile, root_ids=ids, member_id=member_id))
        node_file = _observe(profile.database.parent / "node-identity.json", profile.database.parent, 4096, budget)
        verify.append(lambda observation=node_file: observation.verify(budget))
        node_id = _node(node_file)
        bind({"member_id": member_id, "node_id": node_id, "node_state": node_file.code or ("present" if node_file.raw else "absent")})
        cloud_present, cloud_presence_verify = _permanent_presence(profile, sync_source_continuity, budget, stack)
        home_present, home_presence_verify = _permanent_presence(profile, sync_source_authority, budget, stack)
        verify.extend((cloud_presence_verify, home_presence_verify))
        if cloud_present:
            public_sql = {table: [] for table in inputs.CONTINUITY_COLUMNS}
            cloud_reason = None if schema17 else "ROOTS_SOURCE_CONTINUITY_SCHEMA_UNKNOWN"
            def consume_continuity(row):
                budget.consume("rows", 1, limits.max_rows)
                budget.consume("metadata", len(_canonical(tuple(row))), limits.max_metadata_bytes)
            try:
                with _Queries(image.connection, budget):
                    public_sql = inputs.read_continuity_projection(image.connection, check_cancelled=budget.check,
                        consume_row=consume_continuity)
            except (inputs._Invalid, RootError, sqlite3.Error, ValueError, TypeError, KeyError):
                budget.propagate_cancelled()
                cloud_reason = cloud_reason or "ROOTS_SOURCE_CONTINUITY_SQL_UNKNOWN"
            continuity = stack.enter_context(continuity_roots.capture_locked(profile,
                sql_projection=public_sql, check_cancelled=budget.check))
            verify.append(continuity.verify_source)
            if (node_id is None or any(row["target_library_id"] != record["library_id"]
                    or row["target_node_id"] != node_id for row in public_sql[inputs.continuity_schema.ACTIVATION])):
                cloud_reason = cloud_reason or "ROOTS_SOURCE_CONTINUITY_OWNER_UNKNOWN"
            projection = continuity.public_summary()
            if cloud_reason:
                projection["unknown"].append({"scope": "cloud_continuity", "code": cloud_reason})
            bind({"member_id": member_id, "source_continuity": projection})
            add(inputs.InputScan(source_bindings=(inputs.SourceBinding(member_id, "live", _digest("source_continuity"),
                "source_continuity", "unknown" if projection["unknown"] else continuity.summary["presence"],
                _digest(projection), len(projection["references"])),)))
            budget.consume("snapshots", continuity._reader.total, limits.max_snapshot_bytes)
            budget.consume("rows", len(continuity._reader.files), limits.max_rows)
        if metadata is not None:
            proof_roots = stack.enter_context(home_recovery_roots.capture_locked(home_receipts, profile=profile,
                root_ids=ids, member_id=member_id, library_id=record["library_id"], node_id=node_id,
                check_cancelled=budget.check, schema_version=actual_schema,
                provenance=None if not schema17 else {row["operation_id"]: row["result_sha256"]
                    for row in (provenance or {}).get(home_recovery_roots.TABLE, ())}))
            add(proof_roots.inputs)
            verify.append(proof_roots.verify_source)
        for name in inputs.REPORT_NAMES:
            captured = _observe(profile.reports / (name + ".manifest.json"), profile.reports, 1024 * 1024, budget)
            verify.append(lambda observation=captured: observation.verify(budget))
            add(inputs.parse_manifest(captured.raw, name=name, profile=profile, root_ids=ids, member_id=member_id,
                                     unreadable=bool(captured.code)))
        captured_home = stack.enter_context(home.capture_locked(profile, check_cancelled=budget.check))
        verify.append(captured_home.verify_source)
        home_absent = (not captured_home.bundle.issues and all(item.raw is None and item.error is None for item in captured_home.bundle.records))
        if node_id is not None:
            add(home.parse_bundle(captured_home.bundle, profile=profile, root_ids=ids, member_id=member_id,
                                  source_identity={"library_id": record["library_id"], "node_id": node_id}))
        elif not home_absent:
            base.append(_blocked(ids, member_id, ("sync_media", "local_report", "sync_report"), "ROOTS_NODE_UNPROVEN", kind="home"))
        else:
            bind({"member_id": member_id, "home": "absent"})
        source_identity = None if node_id is None else {"library_id": record["library_id"], "node_id": node_id}
        if schema17 or home_present or home.has_authority_refs(captured_home.bundle, source_identity=source_identity):
            expected_binding = home.current_binding(captured_home.bundle, source_identity=source_identity)
            authority_version = 2 if schema17 else 1
            authority_context = (authority_roots.capture_locked(profile, binding=expected_binding,
                format_version=authority_version, check_cancelled=budget.check)
                if home_present else authority_roots.capture_absence_locked(profile,
                    format_version=authority_version, check_cancelled=budget.check))
            authority = stack.enter_context(authority_context)
            verify.append(authority.verify_source)
            projection = authority.public_summary()
            if not schema17:
                projection["unknown"].append({"scope": "home_authority", "code": "ROOTS_SOURCE_AUTHORITY_SCHEMA_UNKNOWN"})
            if schema17 and provenance is None:
                projection["unknown"].append({"scope": "home_authority", "code": "ROOTS_SOURCE_PROVENANCE_UNKNOWN"})
            if source_identity is not None:
                manual = _manual_proofs(image.connection, projection, provenance, record["library_id"], budget)
                links = home.parse_authority_links(captured_home.bundle, projection, profile=profile,
                    root_ids=ids, member_id=member_id, source_identity=source_identity, provenance=provenance,
                    manual_proofs=manual, check_cancelled=budget.check,
                    legacy_pending_images=(() if authority._legacy_pending is None
                        else authority._legacy_pending.state_images))
                add(links)
                if any(value.section == "authority" and value.state == "unknown"
                        for value in links.source_bindings):
                    projection["unknown"].append({"scope": "home_authority", "code": "ROOTS_SOURCE_AUTHORITY_REFS_UNKNOWN"})
            bind({"member_id": member_id, "source_authority": projection})
            add(inputs.InputScan(source_bindings=(inputs.SourceBinding(member_id, "home", _digest("source_authority"),
                "source_authority", "unknown" if projection["unknown"] else authority.summary["presence"],
                _digest(projection), len(projection["references"])),)))
            budget.consume("snapshots", authority._reader.total, limits.max_snapshot_bytes)
            budget.consume("rows", len(authority._reader.files), limits.max_rows)
            if authority._legacy_pending is not None:
                budget.consume("snapshots", authority._legacy_pending._reader.total, limits.max_snapshot_bytes)
                budget.consume("rows", len(authority._legacy_pending._reader.files), limits.max_rows)
        graph = stack.enter_context(checkpoint_roots.capture_image_locked(image.connection, profile=profile, root_ids=ids,
            member_id=member_id, library_id=record["library_id"], node_id=node_id, policy=policy,
            live_retention=profile.identity == owner_profile.identity, cache=archive_cache, check_cancelled=budget.check))
        graphs[profile.identity] = graph
        add(graph.inputs)
        verify.append(graph.verify_source)
        bind({"member_id": member_id, "checkpoints": tuple(asdict(value) for value in graph.semantic_bindings)})
        backup_cap = limits.max_snapshot_bytes - budget.snapshots
        if backup_cap < 1:
            _fail("ROOTS_SNAPSHOTS_LIMIT")
        default_backups = backup_roots.BackupLimits()
        backup_limits = replace(default_backups, max_total_image_bytes=min(default_backups.max_total_image_bytes, backup_cap),
                                max_image_bytes=min(default_backups.max_image_bytes, backup_cap))
        old = stack.enter_context(backup_roots.capture_locked(profile, root_ids=ids, member_id=member_id,
            library_id=record["library_id"], node_id=node_id, policy=policy, limits=backup_limits,
            cache=archive_cache, check_cancelled=budget.check))
        budget.consume("snapshots", old.image_bytes, limits.max_snapshot_bytes)
        # The remaining image allowance derives partly from raw WAL size.
        # Bind the fixed rule, not this incidental per-round remainder; all
        # actual failures and source projections still remain in the digest.
        declared_limit_hash = _digest({"group": asdict(limits), "backup": asdict(default_backups)})
        old_inputs = replace(old.inputs, source_bindings=tuple(
            replace(value, sha256=declared_limit_hash)
            if (value.section == "limits" and value.source_kind == "backup"
                and value.source_id == _digest("discovery") and value.member_id == member_id) else value
            for value in old.inputs.source_bindings))
        add(old_inputs)
        protected_paths.update(members._key(path) for path in old.protected_paths)
        verify.append(old.verify_source)
        bind({"member_id": member_id, "backups": old.semantic_bindings})
        preservation_cap = limits.max_snapshot_bytes - budget.snapshots
        if preservation_cap < 1:
            _fail("ROOTS_SNAPSHOTS_LIMIT")
        default_preservation = preservation_roots.PersonalPreservationLimits()
        preservation_limits = replace(default_preservation,
            max_snapshot_bytes=min(default_preservation.max_snapshot_bytes, preservation_cap),
            max_file_bytes=min(default_preservation.max_file_bytes, preservation_cap))
        preserved = stack.enter_context(preservation_roots.capture_locked(profile, root_ids=ids,
            member_id=member_id, source_identity=source_identity, limits=preservation_limits,
            check_cancelled=budget.check))
        budget.consume("snapshots", preserved.snapshot_bytes, limits.max_snapshot_bytes)
        budget.consume("rows", preserved.file_count, limits.max_rows)
        preservation_limit_hash = _digest({"group": asdict(limits), "personal_preservation": asdict(default_preservation)})
        preserved_inputs = replace(preserved.inputs, source_bindings=tuple(
            replace(value, sha256=preservation_limit_hash)
            if (value.section == "limits" and value.source_kind == "personal_preservation"
                and value.source_id == _digest("discovery") and value.member_id == member_id) else value
            for value in preserved.inputs.source_bindings))
        add(preserved_inputs)
        protected_paths.update(members._key(path) for path in preserved.protected_paths)
        protected_directories.update(members._key(path) for path in preserved.protected_directories)
        verify.append(preserved.verify_source)
        bind({"member_id": member_id, "personal_preservation": preserved.semantic_bindings})
    combined = _join(base)
    blocked = {(item.namespace, item.root_id) for item in combined.blockers}
    by_position = {}
    for item in owned:
        if item["status"] == "invalid":
            ns, key = item.get("namespace"), item.get("storage_key")
            if ns in NAMESPACES and type(key) is str:
                actual_root = root_info[item["profile"].identity][ns]["root_id"]
                by_position.setdefault(_physical(ns, actual_root, key), []).append(item)
            continue
        by_position.setdefault(_physical(item["namespace"], item["root_id"], item["storage_key"]), []).append(item)
    for group in by_position.values():
        immutable = {tuple(item.get(field) for field in ("sha256", "byte_size", "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns")) for item in group}
        if len(immutable) != 1 or any(item["status"] == "invalid" for item in group):
            for item in group:
                item["status"] = "conflict"
    protected_inodes = _protected_inodes(protected_paths, budget) | authority_inodes
    for item in owned:
        if item["status"] != "unverified" or item["profile"].identity != owner_profile.identity:
            continue
        if _protected(item, protected_paths, protected_directories, protected_inodes):
            item["status"] = "protected"
            continue
        if item["namespace"] not in policy["namespaces"]:
            item["status"] = "out_of_policy"
            continue
        if (item["namespace"], item["root_id"]) in blocked:
            item["status"] = "blocked"
            continue
        budget.consume("hash_bytes", item["byte_size"], limits.max_candidate_hash_bytes)
        try:
            actual = artifacts._inspect(profile=item["profile"], namespace=item["namespace"],
                root=getattr(item["profile"], _ROOT_ROLE[item["namespace"]]), storage_key=item["storage_key"],
                sha256=item["sha256"], byte_size=item["byte_size"], check_cancelled=budget.check)
            item["status"] = "valid" if all(actual[field] == item[field] for field in _ARTIFACT_FIELDS) else "changed"
            if item["status"] == "valid":
                verify.append(lambda frozen=dict(item): _verify_artifact_identity(frozen, budget))
        except (safe.SyncError, OSError, RootError) as exc:
            budget.propagate_cancelled()
            item["status"] = "missing" if _code(exc) == "STORAGE_ARTIFACT_MISSING" else "changed"
    own_graph = graphs.get(owner_profile.identity)
    proposed = []
    if own_graph is not None:
        valid_items = {item["artifact_id"] for item in owned if item["profile"].identity == owner_profile.identity and item["status"] == "valid"}
        for retirement in own_graph.proposed_retirements if "checkpoint" in policy["namespaces"] else ():
            path = owner_profile.checkpoints / retirement["storage_key"]
            if _protected_path(path, protected_paths, protected_directories):
                continue
            if (("checkpoint", retirement["root_id"]) not in blocked
                    and (retirement["archive_item_id"] is None or retirement["archive_item_id"] in valid_items)):
                proposed.append(retirement)
    # Replace this owner's base archive projection only. Other live members and
    # old backup images continue to hold their original logical recovery edges.
    effective = [scan for scan in base if own_graph is None or scan is not own_graph.inputs]
    if own_graph is not None:
        effective.append(own_graph.inputs_for_retirements({(item["origin_kind"], item["checkpoint_id"]) for item in proposed}))
    effective_scan = _join(effective)
    file_rows, eligible, seen_physical = [], [], set()
    for item in owned:
        if item["profile"].identity != owner_profile.identity:
            continue
        value = {key: item[key] for key in _ARTIFACT_FIELDS}
        position = _physical(value["namespace"], value["root_id"], value["storage_key"])
        status = item["status"]
        if status == "valid":
            if _referenced(value, effective_scan):
                status = "referenced"
            elif position in seen_physical:
                status = "duplicate"
            else:
                status = "candidate"
                seen_physical.add(position)
                eligible.append(value)
        file_rows.append({**value, "status": status})
    unknown_counts, inventories, late_problems = {}, [], []
    for profile in expanded:
        record = records.get(profile.identity)
        for ns in NAMESPACES:
            rid = root_info[profile.identity][ns]["root_id"]
            token = ns, rid
            if token in unknown_counts:
                continue
            raw, keys, issue = _inventory(profile, ns, budget, protected_paths, protected_directories)
            inventory_owners = {item["storage_key"].casefold() if os.name == "nt" else item["storage_key"] for item in owned
                                if item.get("namespace") == ns and item.get("root_id") == rid}
            unknown_counts[token] = len(keys - inventory_owners)
            inventories.append({"namespace": ns, "root_id": rid, "sha256": _digest(raw)})
            def verify_inventory(p=profile, namespace=ns, observed=raw, code=issue):
                new, _, changed_code = _inventory(p, namespace, budget, protected_paths, protected_directories)
                if new != observed or changed_code != code:
                    _fail("ROOTS_SOURCE_CHANGED", retriable=True)
            verify.append(verify_inventory)
            if issue:
                problem = _blocked({key: info["root_id"] for key, info in root_info[profile.identity].items()},
                                   record["member_id"] if record else "membership", (ns,), issue)
                combined = _join([combined, problem])
                effective_scan = _join([effective_scan, problem])
                late_problems.append(problem)
    # Inventory failures discovered late still fence every candidate in scope.
    final_blocked = {(item.namespace, item.root_id) for item in effective_scan.blockers}
    proposed = tuple(sorted((item for item in proposed if ("checkpoint", item["root_id"]) not in final_blocked), key=_canonical))
    # Removing a late-blocked retirement must put its transitive roots back.
    # Keeping the earlier effective graph would otherwise release its media.
    effective = [scan for scan in base if own_graph is None or scan is not own_graph.inputs]
    effective.extend(late_problems)
    if own_graph is not None:
        effective.append(own_graph.inputs_for_retirements({(item["origin_kind"], item["checkpoint_id"]) for item in proposed}))
    effective_scan = _join(effective)
    seen_physical, eligible = set(), []
    for file in file_rows:
        if file["status"] not in ("candidate", "referenced", "duplicate"):
            continue
        position = _physical(file["namespace"], file["root_id"], file["storage_key"])
        if (file["namespace"], file["root_id"]) in final_blocked:
            file["status"] = "blocked"
        elif _referenced(file, effective_scan):
            file["status"] = "referenced"
        elif position in seen_physical:
            file["status"] = "duplicate"
        else:
            file["status"] = "candidate"
            seen_physical.add(position)
            eligible.append({key: file[key] for key in _ARTIFACT_FIELDS})
    eligible = tuple(sorted(eligible, key=_canonical))
    membership_binding = {"digest": membership["digest"], "members": membership["members"], "profiles": membership["profiles"]}
    limits_binding = {"group": asdict(limits), "inputs": asdict(inputs.InputLimits()), "home": asdict(home.HomeLimits()),
                      "checkpoint": asdict(checkpoint_roots.CheckpointLimits()), "backup": asdict(backup_roots.BackupLimits()),
                      "personal_preservation": asdict(preservation_roots.PersonalPreservationLimits()),
                      "personal_authority": asdict(personal_authority_roots.PersonalAuthorityRootLimits())}
    binding = {"format_version": 1, "algorithm_version": 2, "budget_rule": "remaining_source_images_v1",
               "owner": owner["member_id"], "policy": policy, "limits": limits_binding,
               "protected": {"paths_sha256": _digest(sorted(protected_paths)), "directories_sha256": _digest(sorted(protected_directories))},
               "members": membership_binding, "sources": sources, "inventories": inventories,
               "base": json.loads(combined.canonical_bytes()), "effective": json.loads(effective_scan.canonical_bytes())}
    roots_sha = _digest(binding)
    preview_sha = _digest({"roots_sha256": roots_sha, "retirements": proposed, "files": sorted(file_rows, key=_canonical),
                           "unknown": sorted((ns, rid, count) for (ns, rid), count in unknown_counts.items())})
    scan = RootScan(owner, policy, limits_binding, membership_binding, combined, effective_scan, proposed,
                    tuple(sorted(file_rows, key=_canonical)), eligible, unknown_counts, tuple(sources), roots_sha, preview_sha,
                    owner_sql)
    def verify_all():
        budget.check()
        for method in verify:
            method()
        latest = members.snapshot_locked(expanded)
        if latest["digest"] != membership["digest"]:
            _fail("ROOTS_MEMBERS_CHANGED", retriable=True)
        guard.verify()
        budget.propagate_cancelled()
    return RootCapture(scan, verify_all), budget


@contextmanager
def capture_locked(profiles, *, owner_profile=None, policy=None, limits=None, check_cancelled=None,
                   _authority_group=None):
    profiles = lifecycle._profiles(profiles)
    owner_profile = profiles[0] if owner_profile is None else lifecycle._profile(owner_profile)
    policy = contract.default_policy() if policy is None else contract.validate_policy(policy)
    limits = RootLimits() if limits is None else limits
    if not isinstance(limits, RootLimits) or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid root scan options")
    capture = None
    try:
        with ExitStack() as stack:
            try:
                capture, budget = _scan(profiles, owner_profile, policy, limits, check_cancelled, stack,
                                        _authority_group)
                yield capture
                capture.verify_source()
            finally:
                if capture is not None:
                    capture._active = False
    except _CallerCancelled as exc:
        raise exc.original from None
    except (authority_roots.AuthorityRootError, continuity_roots.ContinuityRootError) as exc:
        if exc.retriable:
            _fail("ROOTS_SOURCE_CHANGED", retriable=True)
        _fail("ROOTS_SOURCE_UNPROVEN")


def scan_locked(profiles, **kwargs):
    with capture_locked(profiles, **kwargs) as captured:
        return captured.scan


def public_preview(scan):
    safe_files = []
    for file in scan.files:
        try:
            contract._file({**{key: file[key] for key in _ARTIFACT_FIELDS}, "intent": "retain"})
            safe_files.append({key: file[key] for key in ("artifact_id", "namespace", "root_id", "storage_key", "byte_size", "status")})
        except (contract.GCContractError, KeyError, TypeError):
            # Corrupt registrations are counted as exclusions, not echoed.
            pass
    by_namespace = []
    for ns in NAMESPACES:
        files = [file for file in scan.files if file["namespace"] == ns]
        by_namespace.append({"namespace": ns, "candidate_count": sum(f["status"] == "candidate" for f in files),
                             "candidate_bytes": sum(f["byte_size"] for f in files if f["status"] == "candidate"),
                             "referenced_count": sum(f["status"] == "referenced" for f in files),
                             "missing_count": sum(f["status"] == "missing" for f in files),
                             "excluded_count": sum(f["status"] not in ("candidate", "referenced", "missing") for f in files),
                             "unknown_count": sum(count for (namespace, _), count in scan.unknown_counts.items() if namespace == ns)})
    return {"format_version": 1, "owner_member_id": scan.owner["member_id"],
            "member_count": len(scan.members_binding["members"]), "policy": scan.policy,
            "roots_sha256": scan.roots_sha256, "preview_sha256": scan.preview_sha256,
            "candidate_bytes": sum(file["byte_size"] for file in scan.eligible_artifacts), "namespaces": by_namespace,
            "files": safe_files,
            "retirements": [{key: item[key] for key in ("origin_kind", "checkpoint_id", "byte_size", "archive_disposition")} for item in scan.proposed_retirements],
            "blockers": sorted({(b.namespace, b.root_id, b.member_id, b.source_kind, b.code) for b in scan.effective_inputs.blockers})}


def preview(profiles, *, policy=None, limits=None, check_cancelled=None):
    submitted = lifecycle._profiles(profiles)
    if lifecycle.current_guard() is not None:
        _fail("ROOTS_GROUP_MUST_BE_ACQUIRED_FIRST")
    for attempt in range(2):
        try:
            with members._group(submitted, False) as (expanded, _, _):
                return public_preview(scan_locked(expanded, owner_profile=submitted[0], policy=policy,
                                                  limits=limits, check_cancelled=check_cancelled))
        except (members.MembershipError, lifecycle.StorageLifecycleError) as exc:
            raise RootError("ROOTS_GROUP_UNAVAILABLE") from None
        except (RootError, readonly.ReadonlyImageError, home.HomeRootError,
                checkpoint_roots.CheckpointRootError, backup_roots.BackupRootError,
                preservation_roots.PersonalPreservationRootError,
                personal_authority_roots.PersonalAuthorityRootError) as exc:
            if getattr(exc, "retriable", False) and attempt == 0:
                continue
            raise
