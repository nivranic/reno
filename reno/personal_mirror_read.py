"""Installed personal mirrors grant a read only inside this owned factory.

The committed source image is copied into private TEMP without opening source
SQLite/SHM. An independent mode=ro connection owns its fixed transaction; no
transport lease, returned DTO, writer seal or caller connection grants reads.
Attachments retain the existing personal-attachments/files layout. Metadata
checks actual installed rows and physical identities; downloads hash the exact
held fd and transfer that already-authorized I/O result only on successful exit.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import time

from . import config, db, evidence_identity as identity
from . import personal_mirror_contract as facts, personal_mirror_transfer as transfer
from . import personal_mirror_transfer_contract as wire, personal_mirror_transfer_files as files
from . import personal_mirror_transfer_schema as schema, personal_preservation_source
from . import storage_lifecycle as lifecycle, storage_readonly, sync_media as safe
from .sync_protocol import SyncError

MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_SQL_STEPS = 20_000_000
MAX_SECONDS = 30
MAX_SCHEMA_OBJECTS = 10_000
_SEAL = object()
_OPENING = ContextVar("reno_personal_mirror_read_open", default=None)
_CURRENT = ContextVar("reno_personal_mirror_read_current", default=None)
_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}
_SCALAR_PRAGMAS = {"database_list", "query_only", "foreign_keys", "schema_version", "data_version", "trusted_schema"}


class PersonalMirrorReadError(ValueError):
    def __init__(self, suffix, status=409):
        self.code, self.status = "PERSONAL_MIRROR_READ_" + suffix, status
        super().__init__("个人镜像暂不可读取，请刷新后核对。")


def _fail(suffix="PROOF_CHANGED", status=409):
    raise PersonalMirrorReadError(suffix, status) from None


class _Budget:
    def __init__(self, check):
        self.callback = check or (lambda: None)
        self.deadline = time.monotonic() + MAX_SECONDS
        self.steps, self.failure = 0, None

    def check(self):
        self.callback()
        if time.monotonic() > self.deadline:
            _fail("LIMIT", 507)

    def progress(self):
        try:
            self.check()
            self.steps += 1000
            if self.steps > MAX_SQL_STEPS:
                _fail("LIMIT", 507)
        except BaseException as error:
            self.failure = error
            return 1
        return 0


class _ReadConnection(sqlite3.Connection):
    """Our own readonly connection; replacing a caller hook preserves guards."""
    def __init__(self, *args, **kwargs):
        if type(self) is not _ReadConnection or _OPENING.get() is not _SEAL:
            _fail("FACTORY_REQUIRED")
        kwargs["cached_statements"] = 0
        super().__init__(*args, **kwargs)
        self._owner = lifecycle._owner()
        self._protected = self._closed = self._rejected = False
        self._user_authorizer = self._user_progress = self._budget = None
        sqlite3.Connection.set_authorizer(self, self._authorize)

    def _authorize(self, action, first, second, database, origin):
        if self._protected:
            allowed = (action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
                or action == sqlite3.SQLITE_FUNCTION and second != "load_extension"
                or action == sqlite3.SQLITE_PRAGMA and database in {None, "main"} and
                    (first in _PRAGMAS or first in _SCALAR_PRAGMAS and second is None))
            if not allowed:
                self._rejected = True
                return sqlite3.SQLITE_DENY
        if self._user_authorizer is not None:
            return self._user_authorizer(action, first, second, database, origin)
        return sqlite3.SQLITE_OK

    def set_authorizer(self, callback):
        if callback is not None and not callable(callback):
            raise TypeError("authorizer_callback must be callable or None")
        self._user_authorizer = callback
        sqlite3.Connection.set_authorizer(self, self._authorize)

    def _progress(self):
        if self._budget is not None and self._budget.progress():
            return 1
        return int(bool(self._user_progress and self._user_progress()))

    def set_progress_handler(self, callback, instructions):
        if callback is not None and not callable(callback):
            raise TypeError("progress_handler must be callable or None")
        self._user_progress = callback
        sqlite3.Connection.set_progress_handler(self, self._progress, 1000)

    def commit(self):
        if self._protected:
            self._rejected = True
            _fail("CONNECTION_CHANGED")
        return super().commit()

    def rollback(self):
        if self._protected:
            self._rejected = True
            _fail("CONNECTION_CHANGED")
        return super().rollback()

    def close(self):
        if self._protected:
            self._rejected = True
            _fail("CONNECTION_CHANGED")
        self._closed = True
        return super().close()


def _open_owned(image, budget):
    original = next(row[2] for row in image.connection.execute("PRAGMA database_list") if row[1] == "main")
    token = _OPENING.set(_SEAL)
    try:
        con = sqlite3.connect(Path(original).as_uri() + "?mode=ro", uri=True,
            timeout=1, factory=_ReadConnection, cached_statements=0)
    finally:
        _OPENING.reset(token)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        # Instantiate SQLite's built-in JSON virtual table before the fixed
        # user window. Its xConnect schema declaration is not a user writer.
        con.execute("SELECT value FROM json_each('[]')").fetchall()
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, facts.MAX_SNAPSHOT_BYTES + 256 * 1024)
        con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
        con._budget = budget
        con.set_progress_handler(None, 1000)
        con.execute("BEGIN")
        con._protected = True
        con.execute("SELECT rootpage FROM main.sqlite_schema LIMIT 1").fetchall()
        return con
    except BaseException:
        sqlite3.Connection.close(con)
        con._closed = True
        raise


def _close_owned(con):
    try:
        sqlite3.Connection.close(con)
    finally:
        con._closed, con._protected = True, False
        con._user_authorizer = con._user_progress = con._budget = None


def _receiver_schema(con):
    """Same exact pure schema contract, without accepting a writer capability."""
    if con.execute("SELECT count(*) FROM main.sqlite_master").fetchone()[0] > MAX_SCHEMA_OBJECTS:
        _fail("LIMIT", 507)
    if con.execute("SELECT 1 FROM sqlite_temp_master LIMIT 1").fetchone() is not None:
        _fail("PROOF_CHANGED")
    installed_objects = schema._objects(con, "main.sqlite_master")
    if not installed_objects:
        _fail("NOT_INSTALLED", 403)
    reference = sqlite3.connect(":memory:")
    try:
        schema._install_reference(reference)
        if (installed_objects != schema._objects(reference, "main.sqlite_master")
                or any(schema._contract(con, table) != schema._contract(reference, table) for table in schema.TABLES)):
            _fail("PROOF_CHANGED")
    finally:
        reference.close()


def _attachment_rows(con, prepared):
    manifest = wire.attachment_manifest(prepared)
    rows = [dict(row) for row in con.execute(f'SELECT * FROM main."{schema.ATTACHMENT}" WHERE operation_id=? ORDER BY attachment_id LIMIT ?',
        (prepared["operation_id"], wire.MAX_ATTACHMENT_COUNT + 1))]
    if len(rows) != len(manifest) or {row["attachment_id"] for row in rows} != {item["attachment_id"] for item in manifest}:
        _fail("PROOF_CHANGED")
    descriptors = {item["attachment_id"]: item for item in manifest}
    for row in rows:
        item = descriptors[row["attachment_id"]]
        files._filename(item)
        expected = (item["storage_key"], item["sha256"], item["byte_size"], facts._json(item), wire._digest(item))
        if (row["state"] != "installed" or row["stored_at"] is None
                or tuple(row[name] for name in ("storage_key", "sha256", "byte_size", "descriptor_json", "descriptor_sha256")) != expected
                or any(row[name] is None for name in files._IDENTITY_COLUMNS)):
            _fail("PROOF_CHANGED")
    return descriptors, {row["attachment_id"]: row for row in rows}


def _ready_signature(con, budget):
    """Same ready-state input as sync_visibility, with bounded streaming rows."""
    digest, total = hashlib.sha256(), 0
    digest.update(b"[")
    for number, row in enumerate(con.execute("SELECT video_id,sync_state FROM main.video_asset ORDER BY video_id LIMIT ?",
            (facts.MAX_ROWS + 1,))):
        budget.check()
        if number >= facts.MAX_ROWS:
            _fail("LIMIT", 507)
        raw = facts._json(list(row)).encode("utf-8")
        total += len(raw)
        if total > facts.MAX_SNAPSHOT_BYTES:
            _fail("LIMIT", 507)
        if number:
            digest.update(b",")
        digest.update(raw)
    digest.update(b"]")
    return "cloud:" + digest.hexdigest()


class _ReadProof:
    def __init__(self, con, profile, guard, image, node, budget, head, prepared):
        self.con, self.profile, self.guard, self.image, self.node, self.budget = con, profile, guard, image, node, budget
        self.owner, self.active, self.seal = lifecycle._owner(), True, _SEAL
        self.head, self.prepared = deepcopy(head), deepcopy(prepared)
        self.descriptors, self.rows = _attachment_rows(con, prepared)
        self.pending = []
        self.cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
        version = {"receiver": prepared["receiver"], "source": prepared["source"],
            "installed_revision": head["revision"], "snapshot_id": head["snapshot_id"], "snapshot_sha256": head["snapshot_sha256"],
            "knowledge": db.knowledge_version(con), "ready": _ready_signature(con, budget)}
        self.read_version = hashlib.sha256(facts._json(version).encode()).hexdigest()

    def verify(self):
        self.budget.check()
        if (type(self) is not _ReadProof or self.seal is not _SEAL or not self.active or _CURRENT.get() is not self
                or self.owner != lifecycle._owner() or lifecycle.require_guard(self.profile) is not self.guard
                or type(self.con) is not _ReadConnection or self.con._closed or self.con._rejected
                or self.con._owner != self.owner or not self.con._protected or not self.con.in_transaction):
            _fail("FACTORY_REQUIRED")
        if (self.con.execute("PRAGMA query_only").fetchone()[0] != 1
                or self.con.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or self.con.execute("PRAGMA trusted_schema").fetchone()[0] != 0
                or self.con.execute("PRAGMA main.schema_version").fetchone()[0] != self.cookie
                or self.con.execute("SELECT 1 FROM sqlite_temp_master LIMIT 1").fetchone() is not None):
            _fail("CONNECTION_CHANGED")
        self.node.full()
        self.image.verify_source()


def _proof(con):
    proof = _CURRENT.get()
    if type(proof) is not _ReadProof or proof.con is not con:
        _fail("FACTORY_REQUIRED")
    proof.verify()
    return proof


@contextmanager
def open_installed_read(profile, expected_read_version=None, check_cancelled=None):
    """Own private readonly SQL/SH/identity proof; the yielded con is no DTO."""
    if (_CURRENT.get() is not None or check_cancelled is not None and not callable(check_cancelled)
            or expected_read_version is not None and (type(expected_read_version) is not str
                or len(expected_read_version) != 64 or any(c not in "0123456789abcdef" for c in expected_read_version))):
        _fail("INVALID_ARGUMENT", 400)
    budget, proof = _Budget(check_cancelled), None
    try:
        with lifecycle.shared(profile, timeout=1, check_cancelled=budget.check):
            with storage_readonly.committed_image_locked(profile, max_bytes=MAX_IMAGE_BYTES, check_cancelled=budget.check,
                    private_directory_factory=personal_preservation_source._PersonalPrivateDirectory) as image:
                with ExitStack() as stack:
                    con = _open_owned(image, budget)
                    stack.callback(_close_owned, con)
                    _receiver_schema(con)
                    node = transfer._NodeHold(profile, stack)
                    head, prepared, meta = transfer._accepted(con, check_cancelled=budget.check)
                    if head is None:
                        _fail("NOT_INSTALLED", 403)
                    if prepared["receiver"] != {"library_id": meta["library_id"], "node_id": node.node_id}:
                        _fail("PROOF_CHANGED")
                    proof = _ReadProof(con, profile, lifecycle.require_guard(profile), image, node, budget, head, prepared)
                    if expected_read_version is not None and expected_read_version != proof.read_version:
                        _fail("VERSION_CHANGED")
                    token = _CURRENT.set(proof)
                    try:
                        proof.verify()
                        yield con
                        proof.verify()
                    finally:
                        proof.active = False
                        _CURRENT.reset(token)
        # Resource/source verification and both context exits must succeed
        # before an exact original fd becomes a response-owned I/O result.
        if proof is not None:
            for holder in proof.pending:
                holder._publish(_SEAL)
    except BaseException as error:
        if proof is not None:
            for holder in proof.pending:
                try:
                    holder.close()
                except BaseException:
                    error.add_note("PERSONAL_MIRROR_READ_ATTACHMENT_CLEANUP_FAILED")
        if budget.failure is not None:
            raise budget.failure
        if isinstance(error, PersonalMirrorReadError) or not isinstance(error, Exception):
            raise
        if proof is not None and proof.con._rejected:
            _fail("CONNECTION_CHANGED")
        if isinstance(error, (facts.PersonalSnapshotError, wire.PersonalMirrorTransferError, schema.PersonalMirrorSchemaError, SyncError)):
            _fail("PROOF_CHANGED")
        if isinstance(error, storage_readonly.ReadonlyImageError):
            _fail("PROOF_CHANGED" if error.retriable else "UNAVAILABLE", 409 if error.retriable else 503)
        if isinstance(error, (sqlite3.Error, OSError, lifecycle.StorageLifecycleError)):
            _fail("UNAVAILABLE", 503)
        raise


def capabilities_for(con):
    """No capability is returned for an ordinary/cross-owner/closed connection."""
    current = _CURRENT.get()
    if type(current) is not _ReadProof or current.con is not con:
        return None
    proof = _proof(con)
    source = proof.prepared["source"]
    return {"library_id": proof.prepared["receiver"]["library_id"], "instance_id": proof.node.node_id,
        "personal_primary_id": source["personal_primary_id"], "primary_epoch": source["primary_epoch"],
        "personal_read": True, "personal_write": False, "personal_upload": False,
        "personal_integrity_ok": True, "personal_mode": "mirror", "personal_reason": "云端只读镜像；修改请在主实例进行。",
        "personal_read_version": proof.read_version, "personal_mirror": {
            "source_library_id": source["library_id"], "source_node_id": source["node_id"],
            "source_primary_id": source["personal_primary_id"], "source_primary_epoch": source["primary_epoch"],
            "installed_revision": proof.head["revision"], "committed_at": proof.head["updated_at"],
            "snapshot_id": proof.head["snapshot_id"], "snapshot_sha256": proof.head["snapshot_sha256"]}}


def _raw_attachment(proof, attachment_id):
    try:
        facts._uuid(attachment_id)
    except facts.PersonalSnapshotError:
        _fail("INVALID_ARGUMENT", 400)
    row = proof.con.execute("SELECT * FROM main.source_attachment WHERE attachment_id=?", (attachment_id,)).fetchone()
    if row is None:
        _fail("NOT_FOUND", 404)
    return dict(row)


def _view(raw, *, readable, missing, reason=""):
    view = {key: raw[key] for key in ("attachment_id", "source_id", "project_id", "sha256", "byte_size",
        "media_type", "original_name", "state", "created_at")}
    view.update(missing=missing, readable=readable,
        read_state="ready" if readable else "missing" if missing else "not_ready", reason=reason, read_reason=reason)
    view["target_fingerprint"] = identity.fingerprint({key: view[key] for key in
        ("attachment_id", "source_id", "project_id", "sha256", "byte_size", "media_type", "original_name")})
    return view


def _held_attachment(proof, raw, stack, *, full):
    descriptor, row = proof.descriptors.get(raw["attachment_id"]), proof.rows.get(raw["attachment_id"])
    if descriptor is None or row is None or raw["state"] != "ready":
        _fail("ATTACHMENT_UNAVAILABLE")
    root = stack.enter_context(safe._Directory(proof.profile.database.parent / files.ROOT,
        base=proof.profile.database.parent))
    directory = stack.enter_context(safe._Directory(root.path / "files", parent=root))
    held = files._HeldFile(directory, files._filename(descriptor), stack, descriptor)
    files._bind_row(held, row)
    if full:
        held.full(proof.budget.check)
    held.stream.seek(0)
    return held


def attachment_view(con, attachment_id):
    proof = _proof(con)
    raw = _raw_attachment(proof, attachment_id)
    if raw["state"] != "ready":
        return _view(raw, readable=False, missing=False, reason="附件尚未就绪")
    try:
        with ExitStack() as stack:
            _held_attachment(proof, raw, stack, full=False)
            proof.verify()
        return _view(raw, readable=True, missing=False)
    except (OSError, SyncError):
        proof.verify()
        return _view(raw, readable=False, missing=True, reason="镜像附件缺失或已变化，文字记录仍保留")


class _OwnedStream:
    def __init__(self, holder):
        self.holder = holder

    @property
    def closed(self):
        return self.holder._state == "closed"

    def _held(self):
        if self.holder._state != "ready":
            _fail("ATTACHMENT_NOT_PUBLISHED")
        try:
            self.holder._held.short()
        except (OSError, SyncError):
            _fail("ATTACHMENT_CHANGED")
        return self.holder._held.stream

    def read(self, size=-1):
        stream = self._held()
        result = stream.read(size)
        self._held()
        return result

    def seek(self, offset, whence=0):
        return self._held().seek(offset, whence)

    def tell(self):
        return self._held().tell()

    def fileno(self):
        return self._held().fileno()

    def close(self):
        self.holder.close()


class OwnedAttachment:
    """A response owns this exact authorized fd; it cannot open another file."""
    def __init__(self, seal, stack, held, view, read_version):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._stack, self._held, self._state = stack, held, "pending"
        self.view, self.personal_read_version = deepcopy(view), read_version
        self.stream = _OwnedStream(self)

    def _publish(self, seal):
        if seal is not _SEAL or self._state != "pending":
            _fail("ATTACHMENT_NOT_PUBLISHED")
        self._held.short()
        self._state = "ready"

    def close(self):
        if self._state != "closed":
            self._state = "closed"
            self._stack.close()


def open_owned_attachment(con, attachment_id):
    """Produce a pending fd result; factory success alone transfers ownership."""
    proof = _proof(con)
    raw = _raw_attachment(proof, attachment_id)
    stack = ExitStack()
    try:
        held = _held_attachment(proof, raw, stack, full=True)
        proof.verify()
        holder = OwnedAttachment(_SEAL, stack, held, _view(raw, readable=True, missing=False), proof.read_version)
        proof.pending.append(holder)
        return holder
    except BaseException as error:
        try:
            stack.close()
        except BaseException:
            error.add_note("PERSONAL_MIRROR_READ_ATTACHMENT_CLEANUP_FAILED")
        if isinstance(error, (OSError, SyncError)):
            _fail("ATTACHMENT_CHANGED")
        raise


@contextmanager
def open_attachment(con, attachment_id):
    """Consume an exact held fd inside the original active read window."""
    proof = _proof(con)
    raw = _raw_attachment(proof, attachment_id)
    with ExitStack() as stack:
        held = _held_attachment(proof, raw, stack, full=True)
        yield held.stream, _view(raw, readable=True, missing=False)
        held.short()
        proof.verify()
