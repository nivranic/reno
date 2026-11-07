"""Capture fixed personal facts from the configured primary, strictly read-only.

The original committed DB/WAL is copied only to private TEMP by the existing
reader. All ten table projections come from that one image. The existing image
contract requires the source DB/WAL signatures to remain stable until capture
ends, so concurrent commits can yield retriable SOURCE_CHANGED even when only
knowledge changed. No weaker reader, seed, migration or source SHM is used.

Ready attachment bodies and the existing node identity stay open on no-follow
handles throughout the audit. Staging/failed attachment rows are copied intact;
their temporary/failed bodies are not claimed as ready. Extra unmanaged files
are neither adopted nor deleted. Capture writes no source file or lock.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import config, storage_admin
from . import storage_lifecycle as lifecycle, storage_readonly as readonly
from . import storage_source as source, sync_media as safe
from . import personal_mirror_contract as contract

MAX_ATTACHMENT_COUNT = 1024
MAX_ATTACHMENT_BYTES = 1024 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_SQL_STEPS = 10_000_000
NODE_IDENTITY_MAX_BYTES = 4096


@dataclass(frozen=True)
class CapturedSnapshot:
    """A fact document plus local capture observations, never an authorization."""
    snapshot: contract.SnapshotFacts
    observation_json: str

    @property
    def observations(self):
        return contract._decode(self.observation_json)


def _fail(code, *, retriable=False):
    raise contract.PersonalSnapshotError(code, retriable=retriable)


def _enabled(value):
    return value is True or type(value) is str and value.lower() in {"true", "1"}


def _admission():
    role = str(config.get("node_role") or "all").lower()
    if role not in {"all", "home"}:
        _fail("PERSONAL_CAPTURE_ROLE_DISABLED")
    if not _enabled(config.get("personal_features_enabled", False)):
        _fail("PERSONAL_CAPTURE_DISABLED")
    profile = storage_admin.profile()
    data = Path(config.DATA).absolute()
    if os.path.normcase(str(data)) != os.path.normcase(str(profile.database.parent)):
        _fail("PERSONAL_CAPTURE_PROFILE_MISMATCH")
    return role, profile


def _identity_record(info):
    device, inode, size, modified, changed = source.file_signature(info)
    return {"device": str(device), "inode": str(inode), "byte_size": size,
            "mtime_ns": str(modified), "ctime_ns": str(changed)}


class _HeldFile:
    def __init__(self, directory, name, stack):
        self.directory, self.name = directory, name
        self.path_signature = source.file_signature(directory.entry_stat(name))
        self.stream = stack.enter_context(directory.open_file(name))
        self.handle_signature = source.file_signature(os.fstat(self.stream.fileno()))
        # Windows stat/fstat ctime differs; each API is rechecked against itself.
        if self.path_signature[:4] != self.handle_signature[:4]:
            _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
        self.identity = _identity_record(os.fstat(self.stream.fileno()))
        # Registered after the stream context: LIFO checks this original
        # handle before closing it, while its pinned directory is also alive.
        stack.push(self._finish)

    def _finish(self, exc_type, _exc, _traceback):
        if exc_type is not None:
            # There will be no returned fact document. A cleanup verification
            # must never replace the caller's cancellation or primary failure.
            return False
        try:
            self.verify()
        except (OSError, source.SourceReadError, safe.SyncError):
            _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
        return False

    def verify(self):
        if (source.file_signature(os.fstat(self.stream.fileno())) != self.handle_signature
                or source.file_signature(self.directory.entry_stat(self.name)) != self.path_signature):
            _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
        self.directory.verify()

    def read_identity(self, check):
        if not 0 < self.handle_signature[2] <= NODE_IDENTITY_MAX_BYTES:
            _fail("PERSONAL_CAPTURE_NODE_IDENTITY_INVALID")
        check()
        raw = self.stream.read(NODE_IDENTITY_MAX_BYTES + 1)
        self.verify()
        check()
        if len(raw) != self.handle_signature[2]:
            _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
        value = contract._decode(raw)
        if set(value) != {"instance_id"}:
            _fail("PERSONAL_CAPTURE_NODE_IDENTITY_INVALID")
        instance = contract._uuid(value["instance_id"])
        return instance, hashlib.sha256(raw).hexdigest()

    def digest(self, expected_size, check):
        if self.handle_signature[2] != expected_size:
            _fail("PERSONAL_CAPTURE_ATTACHMENT_SIZE_MISMATCH")
        digest, total = hashlib.sha256(), 0
        while True:
            check()
            chunk = self.stream.read(128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
            digest.update(chunk)
        check()
        if total != expected_size:
            _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
        self.verify()
        return digest.hexdigest()


def _projection_authorizer(columns):
    def authorize(action, first, second, schema, origin):
        if origin is not None:
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main":
            allowed = columns.get(first, ())
            if second in allowed or second == "" and first in columns:
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first in {"table_xinfo", "index_list", "index_info", "foreign_key_list"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second == "count":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def _capture_rows(image, check):
    con = image.connection
    pinned = contract.schema_descriptor()
    allowed = {name: {c["name"] for c in descriptor["columns"]}
               for name, descriptor in pinned["tables"].items()}
    allowed.update(meta={"key", "value"}, sqlite_master={"type", "name", "tbl_name", "rootpage", "sql"},
                   sqlite_schema={"type", "name", "tbl_name", "rootpage", "sql"})
    con.set_authorizer(_projection_authorizer(allowed))
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, contract.MAX_CELL_BYTES)
    con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16 * 1024)
    steps, cancellation = 0, None
    def budget():
        nonlocal steps, cancellation
        try:
            check()
        except BaseException as exc:
            # sqlite3 translates exceptions in a progress callback to
            # OperationalError('interrupted'); retain and rethrow the original.
            cancellation = exc
            return 1
        steps += 1000
        return int(steps > MAX_SQL_STEPS)
    con.set_progress_handler(budget, 1000)
    try:
        return _project_rows(con, check)
    except sqlite3.Error:
        if cancellation is not None:
            raise cancellation
        if steps > MAX_SQL_STEPS:
            _fail("PERSONAL_CAPTURE_SQL_BUDGET_EXCEEDED")
        raise
    finally:
        con.set_progress_handler(None, 0)


def _project_rows(con, check):
    descriptor = contract.validate_database_schema(con)
    # Meta is deliberately a fixed whitelist; worker state, knowledge epochs,
    # receiver identity and synchronization cursors are never mirrored.
    meta = [{"key": row[0], "value": row[1]} for row in con.execute(
        "SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key", contract.META_KEYS)]
    if [row["key"] for row in meta] != list(contract.META_KEYS):
        _fail("SNAPSHOT_META_MISMATCH")
    tables, total_rows, total_bytes = {}, 0, len(contract._json(descriptor).encode("utf-8"))
    for name in contract.TABLE_NAMES:
        check()
        layout = descriptor["tables"][name]
        columns = [c["name"] for c in layout["columns"]]
        query = f"SELECT {','.join(columns)} FROM {name} ORDER BY {','.join(layout['order_by'])}"
        rows = []
        for values in con.execute(query):
            check()
            total_rows += 1
            if total_rows > contract.MAX_ROWS:
                _fail("SNAPSHOT_TOO_MANY_ROWS")
            row = dict(zip(columns, values))
            try:
                total_bytes += len(contract._json(row).encode("utf-8"))
            except (TypeError, ValueError, UnicodeError):
                _fail("SNAPSHOT_INVALID_VALUE")
            if total_bytes > contract.MAX_SNAPSHOT_BYTES:
                _fail("SNAPSHOT_SIZE_INVALID")
            rows.append(row)
        tables[name] = rows
    check()
    return descriptor, meta, tables


def capture_snapshot(*, snapshot_id, expected_library_id, expected_primary_id, expected_epoch,
                     prior_snapshot_json=None, check_cancelled=None):
    """Capture the configured enabled all/home primary and its complete facts.

    Explicit expected identities pin the caller's intended source. They are
    checked against actual committed meta and the actual existing local node
    file; callers cannot supply a connection, rows, schema, role or attachments.
    Lifecycle facilities must already exist. Only private TEMP is written.

    Cancellation exceptions propagate unchanged. No model, API, receiver,
    publication, persistent receipt, schema upgrade or primary switch occurs.
    """
    contract._uuid(snapshot_id)
    contract._uuid(expected_library_id)
    contract._uuid(expected_primary_id)
    contract._integer(expected_epoch, 1)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("PERSONAL_CAPTURE_INVALID_ARGUMENT")
    check = check_cancelled or (lambda: None)
    check()
    role, profile = _admission()
    # Use the same owner/private-ACL initialization as original personal
    # preservation. Import only at capture time: that reader also interprets
    # sender history, so a module-level import would create a transport cycle.
    from .personal_preservation_source import _PersonalPrivateDirectory
    # Do not catch caller cancellation in filesystem error translation.
    with lifecycle.shared(profile, check_cancelled=check):
        with readonly.committed_image_locked(profile, check_cancelled=check,
                private_directory_factory=_PersonalPrivateDirectory) as image:
            descriptor, meta_rows, tables = _capture_rows(image, check)
            meta = {row["key"]: row["value"] for row in meta_rows}
            expected = {"library_id": expected_library_id, "personal_primary_id": expected_primary_id,
                        "primary_epoch": expected_epoch}
            if (meta.get("library_id") != expected_library_id
                    or meta.get("personal_primary_id") != expected_primary_id
                    or meta.get("primary_epoch") != str(expected_epoch)):
                _fail("PERSONAL_CAPTURE_PRIMARY_CHANGED")
            ready = sorted((r for r in tables["source_attachment"] if r["state"] == "ready"),
                           key=lambda row: row["attachment_id"])
            if len(ready) > MAX_ATTACHMENT_COUNT:
                _fail("PERSONAL_CAPTURE_TOO_MANY_ATTACHMENTS")
            held, manifest, root_identity = [], [], None
            with ExitStack() as stack:
                try:
                    directory = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
                    node = _HeldFile(directory, "node-identity.json", stack)
                except (OSError, source.SourceReadError, safe.SyncError):
                    _fail("PERSONAL_CAPTURE_NODE_IDENTITY_UNSAFE")
                instance_id, node_sha = node.read_identity(check)
                held.append(node)
                if instance_id != expected_primary_id:
                    _fail("PERSONAL_CAPTURE_READ_ONLY_REPLICA")
                if ready:
                    try:
                        root = profile.database.parent / "personal-attachments"
                        attachment_directory = stack.enter_context(safe._Directory(root, base=root, parent=directory))
                        files_root = root / "files"
                        files_directory = stack.enter_context(safe._Directory(files_root, base=files_root, parent=attachment_directory))
                    except (OSError, safe.SyncError):
                        _fail("PERSONAL_CAPTURE_ATTACHMENT_MISSING_OR_UNSAFE")
                    _, device, inode = attachment_directory.identities[-1]
                    root_identity = {"device": str(device), "inode": str(inode)}
                    total_attachment_bytes = 0
                    for row in ready:
                        check()
                        key = contract.storage_key(row["storage_key"])
                        contract._integer(row["byte_size"], 1)
                        contract._sha(row["sha256"])
                        total_attachment_bytes += row["byte_size"]
                        if (row["byte_size"] > MAX_ATTACHMENT_BYTES
                                or total_attachment_bytes > MAX_ATTACHMENT_TOTAL_BYTES):
                            _fail("PERSONAL_CAPTURE_ATTACHMENTS_TOO_LARGE")
                        try:
                            body = _HeldFile(files_directory, key.name, stack)
                        except (OSError, source.SourceReadError, safe.SyncError):
                            _fail("PERSONAL_CAPTURE_ATTACHMENT_MISSING_OR_UNSAFE")
                        held.append(body)
                        digest = body.digest(row["byte_size"], check)
                        if digest != row["sha256"]:
                            _fail("PERSONAL_CAPTURE_ATTACHMENT_SHA256_MISMATCH")
                        manifest.append({"attachment_id": row["attachment_id"], "storage_key": row["storage_key"],
                                         "sha256": digest, "byte_size": row["byte_size"], "file_identity": body.identity})
                counts = {table: len(tables[table]) for table in contract.TABLE_NAMES}
                counts.update(meta=len(meta_rows), attachment_manifest=len(manifest))
                document = {"format_version": contract.FORMAT_VERSION, "snapshot_id": snapshot_id,
                            "source": expected, "meta": meta_rows, "schema": descriptor, "tables": tables,
                            "attachment_manifest": manifest, "counts": counts}
                snapshot = contract.validate_snapshot(contract._json(document), prior_snapshot_json=prior_snapshot_json)
                check()
                for body in held:
                    try:
                        body.verify()
                    except (OSError, source.SourceReadError, safe.SyncError):
                        _fail("PERSONAL_CAPTURE_FILE_CHANGED", retriable=True)
                source_fingerprint = image.verify_source()
                final_role, final_profile = _admission()
                if final_role != role or final_profile.identity != profile.identity:
                    _fail("PERSONAL_CAPTURE_ADMISSION_CHANGED", retriable=True)
                observation = {"source_fingerprint": source_fingerprint, "instance_id": instance_id,
                               "node_identity_sha256": node_sha, "node_identity_file": node.identity,
                               "attachment_root_identity": root_identity}
                check()
            # Returning is delayed until held files, private image and actual
            # lifecycle guard have all completed their exit verification.
            result = CapturedSnapshot(snapshot, contract._json(observation))
    return result
