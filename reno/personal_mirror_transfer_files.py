"""Immutable personal-mirror attachment bodies, separate from knowledge media.

Only a persisted prepare selects the original ready manifest. Streaming/SHA
work belongs outside SQL writers; the short commit consumer retains the actual
directory/file handles and checks their original identities. No reader creates
a facility, repairs an unknown file, installs personal rows or grants a primary.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import errno
import hashlib
import os
from pathlib import PurePosixPath
import sqlite3
import threading
import uuid
from types import MappingProxyType

from . import budget_writer_witness as budget_sql
from . import personal_mirror_contract as facts, personal_mirror_transfer_contract as wire
from . import personal_mirror_transfer_schema as schema, storage_admin
from . import storage_lifecycle as lifecycle, storage_source as physical
from . import sync_media as safe, sync_queue as queue
from .sync_protocol import SyncError

ROOT = "personal-attachments"
STAGING = ".mirror-staging"
MAX_TEMP_BYTES = wire.MAX_ATTACHMENT_BYTES
MAX_TEMP_FILES = 4096
_SEAL = object()
_CURRENT = ContextVar("reno_personal_mirror_attachment_hold", default=None)
_CONTEXT = ContextVar("reno_personal_mirror_attachment_context", default=None)
_WRITE_WINDOW = ContextVar("reno_personal_mirror_attachment_writer", default=None)
_IDENTITY_COLUMNS = ("local_file_device", "local_file_inode", "local_file_mtime_ns", "local_file_ctime_ns")
_MUTABLE_INTENT = {"state", "updated_at"}
_MUTABLE_ATTACHMENT = {"state"}


def _fail(code, status=409):
    raise SyncError("Personal mirror attachment could not be verified", "PERSONAL_MIRROR_FILES_" + code, status) from None


def _same(actual, expected):
    if actual != expected:
        _fail("SOURCE_CHANGED")


def _checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            # Keep the peer/session selected at entry. A cancellation callback
            # can mutate external caller dictionaries, but must not switch this
            # operation to a subsequently acquired session generation.
            for name in ("source", "lease"):
                if name in kwargs:
                    kwargs[name] = deepcopy(kwargs[name])
            return function(*args, **kwargs)
        except (facts.PersonalSnapshotError, wire.PersonalMirrorTransferError, schema.PersonalMirrorSchemaError) as exc:
            raise SyncError("Personal mirror attachment could not be verified", exc.code, 409) from None
    return wrapped


def _outside_writer():
    from . import personal_mirror_transfer as runtime
    if budget_sql.in_write_window() or _WRITE_WINDOW.get() is not None or runtime.in_write_window():
        _fail("READ_WINDOW_REQUIRED")


def in_write_window():
    """Rejection signal for runtime full readers; no writer capability."""
    return _WRITE_WINDOW.get() is not None


def _selectors(profile, operation_id, expected_prepare_sha256):
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    facts._uuid(operation_id)
    facts._sha(expected_prepare_sha256)
    return profile


def _connection(con, profile, *, writer=False):
    from . import personal_mirror_sqlite as mirror_sql
    if type(con) is not sqlite3.Connection and not mirror_sql.is_owned(con):
        _fail("CONNECTION_REQUIRED")
    lifecycle.require_guard(profile)
    storage_admin.from_connection(con, profile=profile)
    if writer and (not con.in_transaction or con.execute("PRAGMA query_only").fetchone()[0] != 0):
        _fail("WRITER_REQUIRED")


def _attachments(con, operation_id):
    cursor = con.execute(f'SELECT * FROM main."{schema.ATTACHMENT}" WHERE operation_id=? ORDER BY attachment_id LIMIT ?',
        (operation_id, wire.MAX_ATTACHMENT_COUNT + 1))
    columns = tuple(item[0] for item in cursor.description)
    rows = [dict(zip(columns, row)) for row in cursor]
    if len(rows) > wire.MAX_ATTACHMENT_COUNT:
        _fail("ATTACHMENT_COUNT_LIMIT")
    return rows


def _filename(descriptor):
    path = facts.storage_key(descriptor["storage_key"])
    # Keep the original UUID from the original source storage key. Do not
    # rename it to this transfer operation or to the attachment selector.
    if path.suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        _fail("ATTACHMENT_PATH_INVALID")
    facts._uuid(path.stem)
    safe._entry_name(path.name)
    return path.name


def _immutable(record, mutable):
    return tuple((name, record[name]) for name in sorted(record) if name not in mutable)


def _prepared(con, profile, operation_id, expected_prepare_sha256, *, allow_missing=False):
    _outside_writer()
    from . import personal_mirror_transfer as runtime
    _connection(con, profile)
    schema.validate(con)
    intent = runtime._raw_intent(con, operation_id, expected_prepare_sha256)
    prepared = runtime._validated_intent(intent)
    _same(prepared["receiver"], dict(zip(("library_id", "node_id"), queue._local_identity(con))))
    manifest = wire.attachment_manifest(prepared)
    for descriptor in manifest:
        _filename(descriptor)
    expected = {item["attachment_id"]: item for item in manifest}
    rows = _attachments(con, operation_id)
    if len({row["attachment_id"] for row in rows}) != len(rows):
        _fail("ATTACHMENT_ROWS_CHANGED")
    if set(row["attachment_id"] for row in rows) - set(expected):
        _fail("ATTACHMENT_ROWS_CHANGED")
    if not allow_missing and set(row["attachment_id"] for row in rows) != set(expected):
        _fail("ATTACHMENT_ROWS_REQUIRED")
    for row in rows:
        descriptor = expected[row["attachment_id"]]
        _same((row["storage_key"], row["sha256"], row["byte_size"], row["descriptor_json"], row["descriptor_sha256"]),
            (descriptor["storage_key"], descriptor["sha256"], descriptor["byte_size"],
             facts._json(descriptor), wire._digest(descriptor)))
        if row["state"] not in {"reserved", "stored", "installed"}:
            _fail("ATTACHMENT_ROWS_CHANGED")
        if intent["state"] == "installed" and row["state"] != "installed":
            _fail("ATTACHMENT_ROWS_CHANGED")
    return intent, prepared, manifest, rows


@_checked
def reserve_prepared_attachments_locked(con, *, profile, operation_id, expected_prepare_sha256):
    """Reserve the whole actual parent manifest; caller supplies no descriptors."""
    profile = _selectors(profile, operation_id, expected_prepare_sha256)
    _connection(con, profile, writer=True)
    from . import personal_mirror_transfer as runtime
    manifest = runtime._prepared_manifest_for_reservation_locked(con, profile=profile,
        operation_id=operation_id, expected_prepare_sha256=expected_prepare_sha256)
    rows = _attachments(con, operation_id)
    expected = {item["attachment_id"]: item for item in manifest}
    if len(rows) > len(manifest) or set(row["attachment_id"] for row in rows) - set(expected):
        _fail("ATTACHMENT_ROWS_CHANGED")
    for row in rows:
        descriptor = expected[row["attachment_id"]]
        _same((row["storage_key"], row["sha256"], row["byte_size"], row["descriptor_json"], row["descriptor_sha256"]),
            (descriptor["storage_key"], descriptor["sha256"], descriptor["byte_size"],
             facts._json(descriptor), wire._digest(descriptor)))
    present = {row["attachment_id"] for row in rows}
    missing = [item for item in manifest if item["attachment_id"] not in present]
    for descriptor in missing:
        _filename(descriptor)
        con.execute(f'INSERT INTO main."{schema.ATTACHMENT}" '
            '(operation_id,attachment_id,storage_key,sha256,byte_size,descriptor_json,descriptor_sha256,state) '
            "VALUES(?,?,?,?,?,?,?,'reserved')", (operation_id, descriptor["attachment_id"], descriptor["storage_key"],
            descriptor["sha256"], descriptor["byte_size"], facts._json(descriptor), wire._digest(descriptor)))
    actual = _attachments(con, operation_id)
    _same(set(row["attachment_id"] for row in actual), set(expected))
    for row in actual:
        descriptor = expected[row["attachment_id"]]
        _same((row["storage_key"], row["sha256"], row["byte_size"], row["descriptor_json"], row["descriptor_sha256"]),
            (descriptor["storage_key"], descriptor["sha256"], descriptor["byte_size"],
             facts._json(descriptor), wire._digest(descriptor)))
    runtime._prepared_manifest_for_reservation_locked(con, profile=profile,
        operation_id=operation_id, expected_prepare_sha256=expected_prepare_sha256)
    return len(missing)


class _HeldFile:
    __slots__ = ("directory", "name", "stream", "path_signature", "handle_signature", "descriptor")
    def __init__(self, directory, name, stack, descriptor=None):
        self.directory, self.name = directory, name
        self.descriptor = None if descriptor is None else MappingProxyType({**descriptor,
            "file_identity": MappingProxyType(dict(descriptor["file_identity"]))})
        initial = directory.entry_stat(name)
        safe._safe_stat(initial, directory=False)
        if initial.st_nlink != 1:
            _fail("FILE_LINKED")
        self.path_signature = physical.file_signature(initial)
        self.stream = stack.enter_context(directory.open_file(name))
        self.handle_signature = physical.file_signature(os.fstat(self.stream.fileno()))
        _same(self.path_signature[:4], self.handle_signature[:4])
        self.short()

    def short(self):
        self.directory.verify()
        path, opened = self.directory.entry_stat(self.name), os.fstat(self.stream.fileno())
        safe._safe_stat(path, directory=False)
        safe._safe_stat(opened, directory=False)
        if path.st_nlink != 1 or opened.st_nlink != 1:
            _fail("FILE_LINKED")
        _same(physical.file_signature(path), self.path_signature)
        _same(physical.file_signature(opened), self.handle_signature)

    def full(self, check):
        _outside_writer()
        self.short()
        if self.descriptor is None:
            if not 0 < self.handle_signature[2] <= 4096:
                _fail("NODE_IDENTITY_INVALID")
            check()
            self.stream.seek(0)
            raw = self.stream.read(4097)
            value = facts._decode(raw)
            if type(value) is not dict or set(value) != {"instance_id"}:
                _fail("NODE_IDENTITY_INVALID")
            facts._uuid(value["instance_id"])
            self.short()
            return value["instance_id"], hashlib.sha256(raw).hexdigest()
        descriptor = self.descriptor
        _same(self.handle_signature[2], descriptor["byte_size"])
        check()
        self.stream.seek(0)
        total, digest = 0, hashlib.sha256()
        while True:
            check()
            chunk = self.stream.read(128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > descriptor["byte_size"]:
                _fail("FILE_SIZE_MISMATCH")
            digest.update(chunk)
        check()
        if total != descriptor["byte_size"] or digest.hexdigest() != descriptor["sha256"]:
            _fail("FILE_HASH_MISMATCH")
        self.short()
        return self.handle_signature


def _local_identity(held):
    device, inode, _, modified, changed = held.handle_signature
    return tuple(str(value) for value in (device, inode, modified, changed))


def _bind_row(held, row):
    _same(tuple(row[name] for name in _IDENTITY_COLUMNS), _local_identity(held))
    _same(row["byte_size"], held.handle_signature[2])
    if row["stored_at"] is None or row["state"] not in {"stored", "installed"}:
        _fail("ATTACHMENT_NOT_STORED")


def _short_rows(con, profile, intent, rows, cookie):
    from . import personal_mirror_transfer as runtime
    _connection(con, profile, writer=True)
    _same(con.execute("PRAGMA main.schema_version").fetchone()[0], cookie)
    if schema._objects(con, "sqlite_temp_master"):
        _fail("TEMP_COLLISION")
    current = runtime._raw_intent(con, intent["operation_id"], intent["prepare_sha256"])
    _same(_immutable(current, _MUTABLE_INTENT), _immutable(intent, _MUTABLE_INTENT))
    if current["state"] not in ({"installed"} if intent["state"] == "installed" else {"prepared", "installed"}):
        _fail("INTENT_CHANGED")
    actual = _attachments(con, intent["operation_id"])
    _same(tuple(_immutable(row, _MUTABLE_ATTACHMENT) for row in actual),
        tuple(_immutable(row, _MUTABLE_ATTACHMENT) for row in rows))
    for current_row, original in zip(actual, rows):
        if current_row["state"] not in ({"installed"} if original["state"] == "installed" else {"stored", "installed"}):
            _fail("ATTACHMENT_ROWS_CHANGED")


class PreparedAttachmentHold:
    """Exact closed factory's held physical facts; never a personal write grant."""
    __slots__ = ("_seal", "_active", "_thread", "_context_token", "_guard", "_profile", "_intent", "_rows",
        "_cookie", "_held", "_node", "_node_fact", "_check", "_signature")

    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def _context(self):
        if (type(self) is not PreparedAttachmentHold or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self or self._thread != threading.get_ident()
                or lifecycle.require_guard(self._profile) is not self._guard
                or type(self._node) is not _HeldFile or type(self._held) is not tuple
                or any(type(item) is not _HeldFile for item in self._held)):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self._guard.verify()
        self._check()
        _same(self._signature, self._closure_signature())

    def _closure_signature(self):
        entries = (self._node, *self._held)
        return (self._profile.identity, id(self._guard), self._cookie, self._node_fact,
            id(self._intent), id(self._rows), id(self._held), id(self._node), id(self._check),
            tuple((id(item), type(item), id(item.directory), item.directory.path, item.name,
                id(item.stream), item.path_signature, item.handle_signature, id(item.descriptor)) for item in entries))

    def verify_for_commit(self, con, *, profile):
        self._context()
        profile = lifecycle._profile(profile)
        _same(profile.identity, self._profile.identity)
        _short_rows(con, profile, self._intent, self._rows, self._cookie)
        self._guard.verify()
        self._node.short()
        for held in self._held:
            held.short()

    def verify_source(self):
        _outside_writer()
        self._context()
        from . import personal_mirror_transfer as runtime
        with runtime._connection(self._profile) as con:
            actual, prepared, manifest, rows = _prepared(con, self._profile,
                self._intent["operation_id"], self._intent["prepare_sha256"])
            _same(_immutable(actual, _MUTABLE_INTENT), _immutable(self._intent, _MUTABLE_INTENT))
            _same(tuple(_immutable(row, _MUTABLE_ATTACHMENT) for row in rows),
                tuple(_immutable(row, _MUTABLE_ATTACHMENT) for row in self._rows))
            if actual["state"] not in ({"installed"} if self._intent["state"] == "installed" else {"prepared", "installed"}):
                _fail("INTENT_CHANGED")
            for row, held in zip(rows, self._held):
                _bind_row(held, row)
            _same(prepared["receiver"]["node_id"], self._node_fact[0])
        _same(self._node.full(self._check), self._node_fact)
        for held in self._held:
            held.full(self._check)
        self._guard.verify()


@contextmanager
def hold_prepared_attachments_locked(*, profile, operation_id, expected_prepare_sha256, check_cancelled=None):
    """Self-read the whole original manifest, including its legitimate emptiness."""
    _outside_writer()
    profile = _selectors(profile, operation_id, expected_prepare_sha256)
    if _CURRENT.get() is not None:
        _fail("FACTORY_REQUIRED")
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("CANCELLATION_INVALID")
    check = check_cancelled or (lambda: None)
    from . import personal_mirror_transfer as runtime
    with ExitStack() as stack:
        with runtime._connection(profile) as con:
            intent, prepared, manifest, rows = _prepared(con, profile, operation_id, expected_prepare_sha256)
            cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
        if any(row["state"] not in {"stored", "installed"} for row in rows):
            _fail("ATTACHMENT_NOT_STORED")
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        node = _HeldFile(data, "node-identity.json", stack)
        node_fact = node.full(check)
        _same(node_fact[0], prepared["receiver"]["node_id"])
        held = []
        descriptors = {item["attachment_id"]: item for item in manifest}
        if rows:
            root = stack.enter_context(safe._Directory(data.path / ROOT, parent=data))
            directory = stack.enter_context(safe._Directory(root.path / "files", parent=root))
            for row in rows:
                check()
                descriptor = descriptors[row["attachment_id"]]
                item = _HeldFile(directory, _filename(descriptor), stack, deepcopy(descriptor))
                item.full(check)
                _bind_row(item, row)
                held.append(item)
        value = object.__new__(PreparedAttachmentHold)
        for name, item in {"_seal": _SEAL, "_active": True, "_thread": threading.get_ident(), "_context_token": None,
            "_guard": lifecycle.require_guard(profile), "_profile": profile, "_intent": MappingProxyType(dict(intent)),
            "_rows": tuple(MappingProxyType(dict(row)) for row in rows), "_cookie": cookie, "_held": tuple(held), "_node": node,
            "_node_fact": node_fact, "_check": check}.items():
            setattr(value, name, item)
        value._signature = value._closure_signature()
        token = _CURRENT.set(value)
        value._context_token = _CONTEXT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CONTEXT.reset(value._context_token)
            _CURRENT.reset(token)


capture_installed_files_locked = hold_prepared_attachments_locked


def _cleanup_temp(directory, name, identity):
    """Remove only this call's original inode; never replace the primary error."""
    try:
        directory.verify()
        info = directory.entry_stat(name)
        safe._safe_stat(info, directory=False)
        if (info.st_dev, info.st_ino) == identity:
            directory.unlink(name)
    except (OSError, SyncError):
        pass


def _temp_space(directory, expected_size):
    names = directory.names()
    if len(names) > MAX_TEMP_FILES:
        _fail("TEMP_FILE_COUNT_LIMIT", 507)
    total = 0
    for name in names:
        if name == "upload.lock":
            continue
        info = directory.entry_stat(name)
        safe._safe_stat(info, directory=False)
        total += info.st_size
        if total + expected_size > MAX_TEMP_BYTES:
            _fail("TEMP_QUOTA", 507)


def _write_temp(directory, name, chunks, descriptor, check):
    _outside_writer()
    try:
        iterator = iter(chunks)
    except TypeError:
        _fail("BODY_REQUIRED", 400)
    identity = None
    try:
        with directory.open_file(name, write=True, create=True) as stream:
            allocated = os.fstat(stream.fileno())
            identity = (allocated.st_dev, allocated.st_ino)
            total, digest = 0, hashlib.sha256()
            while True:
                check()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                check()
                if type(chunk) is not bytes:
                    _fail("BODY_CHUNK_INVALID", 400)
                total += len(chunk)
                if total > descriptor["byte_size"]:
                    _fail("BODY_TOO_LARGE", 413)
                if chunk and stream.write(chunk) != len(chunk):
                    raise OSError(errno.EIO, "Incomplete personal attachment write")
                digest.update(chunk)
            if total != descriptor["byte_size"]:
                _fail("BODY_SIZE_MISMATCH", 422)
            if digest.hexdigest() != descriptor["sha256"]:
                _fail("BODY_HASH_MISMATCH", 422)
            stream.flush()
            os.fsync(stream.fileno())
        directory.verify()
        current = directory.entry_stat(name)
        _same((current.st_dev, current.st_ino), identity)
        return identity
    except BaseException:
        if identity is not None:
            _cleanup_temp(directory, name, identity)
        raise


def _admitted_original(con, profile, source, lease, operation_id, expected_prepare_sha256, now):
    from . import personal_mirror_transfer as runtime
    runtime._admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
    intent, prepared, manifest, rows = _prepared(con, profile, operation_id, expected_prepare_sha256)
    _same((prepared["source"]["library_id"], prepared["source"]["node_id"]),
        (source["library_id"], source["node_id"]))
    return intent, prepared, manifest, rows


@_checked
def store_attachment_locked(*, profile, source, lease, operation_id, expected_prepare_sha256,
        attachment_id, chunks=None, check_cancelled=None, now=None):
    """Store one selected original body; recover an already published same body."""
    _outside_writer()
    profile = _selectors(profile, operation_id, expected_prepare_sha256)
    facts._uuid(attachment_id)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("CANCELLATION_INVALID")
    check = check_cancelled or (lambda: None)
    from . import personal_mirror_transfer as runtime
    with runtime._connection(profile) as con:
        intent, prepared, manifest, rows = _admitted_original(con, profile, source, lease,
            operation_id, expected_prepare_sha256, now)
        cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
    selected = [row for row in rows if row["attachment_id"] == attachment_id]
    descriptors = [item for item in manifest if item["attachment_id"] == attachment_id]
    if len(selected) != 1 or len(descriptors) != 1:
        _fail("ATTACHMENT_NOT_PREPARED", 404)
    row, descriptor = selected[0], descriptors[0]
    name, temp_name, owned = _filename(descriptor), uuid.uuid4().hex + ".part", None
    with ExitStack() as stack:
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        root = stack.enter_context(safe._Directory(data.path / ROOT, create=True, parent=data))
        directory = stack.enter_context(safe._Directory(root.path / "files", create=True, parent=root))
        temporary = stack.enter_context(safe._Directory(root.path / STAGING, create=True, parent=root))
        stack.enter_context(safe._upload_lock(temporary, check_cancelled=check))
        try:
            try:
                existing = directory.entry_stat(name)
            except FileNotFoundError:
                existing = None
            if existing is None:
                if row["state"] != "reserved":
                    _fail("STORED_FILE_MISSING", 404)
                if chunks is None:
                    _fail("BODY_REQUIRED", 400)
                _temp_space(temporary, descriptor["byte_size"])
                owned = _write_temp(temporary, temp_name, chunks, descriptor, check)
                with runtime._connection(profile) as con:
                    current = _admitted_original(con, profile, source, lease, operation_id, expected_prepare_sha256, now)
                    _same(_immutable(current[0], _MUTABLE_INTENT), _immutable(intent, _MUTABLE_INTENT))
                    _same(current[3], rows)
                check()
                temporary.verify()
                directory.verify()
                # Hash the actual flushed temporary body after the last caller
                # callback/SQL read. Hashing incoming chunks alone cannot prove
                # that the original temporary file still contains those bytes.
                with ExitStack() as temporary_proof:
                    ready_hold = _HeldFile(temporary, temp_name, temporary_proof, deepcopy(descriptor))
                    _same(ready_hold.handle_signature[:2], owned)
                    ready_hold.full(check)
                    ready_path_signature = ready_hold.path_signature
                # Windows publication needs the read hold closed. Keep the
                # final path/identity comparison and rename adjacent, with no
                # caller callback or SQL access between them.
                ready = temporary.entry_stat(temp_name)
                safe._safe_stat(ready, directory=False)
                _same(physical.file_signature(ready), ready_path_signature)
                temporary.verify()
                directory.verify()
                try:
                    safe._rename_new(temporary, temp_name, directory, name)
                except FileExistsError:
                    pass  # Independently created same-name bytes must still verify below.
                if os.name != "nt":
                    os.fsync(directory.fd)
                    os.fsync(temporary.fd)
            held = _HeldFile(directory, name, stack, deepcopy(descriptor))
            held.full(check)
            if row["state"] in {"stored", "installed"}:
                _bind_row(held, row)
            identity = _local_identity(held)
            with runtime._connection(profile) as con:
                runtime._admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
                token = _WRITE_WINDOW.set(con)
                try:
                    from . import personal_mirror_sqlite as mirror_sql
                    mirror_sql.begin_writer(con)
                    runtime._admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
                    _connection(con, profile, writer=True)
                    _same(con.execute("PRAGMA main.schema_version").fetchone()[0], cookie)
                    actual = runtime._raw_intent(con, operation_id, expected_prepare_sha256)
                    _same(_immutable(actual, _MUTABLE_INTENT), _immutable(intent, _MUTABLE_INTENT))
                    current_rows = _attachments(con, operation_id)
                    _same(current_rows, rows)
                    held.short()
                    _, stamp = queue._clock(now)
                    if row["state"] == "reserved":
                        changed = con.execute(f'UPDATE main."{schema.ATTACHMENT}" SET state=\'stored\','
                            'local_file_device=?,local_file_inode=?,local_file_mtime_ns=?,local_file_ctime_ns=?,stored_at=? '
                            "WHERE operation_id=? AND attachment_id=? AND state='reserved' AND descriptor_sha256=?",
                            (*identity, stamp, operation_id, attachment_id, row["descriptor_sha256"])).rowcount
                        if changed != 1:
                            _fail("ATTACHMENT_ROWS_CHANGED")
                    check()
                    runtime._admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
                    held.short()
                    mirror_sql.commit_writer(con)
                except BaseException:
                    mirror_sql.abort_writer(con)
                    raise
                finally:
                    _WRITE_WINDOW.reset(token)
            held.full(check)
            return {"operation_id": operation_id, "prepare_sha256": expected_prepare_sha256,
                "attachment_id": attachment_id, "storage_key": descriptor["storage_key"],
                "sha256": descriptor["sha256"], "byte_size": descriptor["byte_size"],
                "state": row["state"] if row["state"] == "installed" else "stored"}
        finally:
            if owned is not None:
                _cleanup_temp(temporary, temp_name, owned)
