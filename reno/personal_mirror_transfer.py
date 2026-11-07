"""Persistent single-primary personal mirrors, with no public read capability.

Preparation freezes original source facts; installation copies only the ten
personal tables and three personal metadata keys. Receiver knowledge identity,
node identity and user-operation receipts are never synthesized. All expensive
snapshot/schema/file validation precedes the short SQL writer. These internal
entrypoints require an existing lifecycle guard, explicit opt-in and the real
paired live session; no request installs facilities or promotes a primary.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import os
import sqlite3

from . import budget_writer_witness as budget_sql
from . import config, db, personal_mirror_contract as facts
from . import personal_mirror_transfer_contract as wire, personal_mirror_transfer_schema as schema
from . import personal_mirror_sqlite as mirror_sql
from . import storage_admin, storage_lifecycle as lifecycle, storage_source as source_files
from . import sync_media as safe, sync_queue as queue, sync_transfer
from .sync_protocol import SyncError

PERSONAL_META = ("default_project_id", "personal_primary_id", "primary_epoch")
MAX_NODE_BYTES = 4096
_WRITE_WINDOW = ContextVar("reno_personal_mirror_writer", default=None)
_RESERVATION = ContextVar("reno_personal_mirror_reservation", default=None)
_RESERVATION_SEAL = object()


def in_write_window():
    """A rejection signal for full I/O, never permission to borrow a writer."""
    return _WRITE_WINDOW.get() is not None


def _require_read_window():
    from . import personal_mirror_transfer_files as files
    if in_write_window() or files.in_write_window() or budget_sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")


def _begin_writer(con):
    _require_read_window()
    if con.in_transaction:
        _fail("WRITER_REQUIRED")
    token = _WRITE_WINDOW.set(con)
    try:
        mirror_sql.begin_writer(con)
    except BaseException:
        _WRITE_WINDOW.reset(token)
        raise
    return token


@dataclass(frozen=True, slots=True)
class _ReservationCapture:
    connection: object
    profile: object
    guard: object
    record_items: tuple
    manifest_json: str
    owner: tuple
    seal: object

    def manifest(self, con, *, profile, operation_id, expected_prepare_sha256):
        if (type(self) is not _ReservationCapture or self.seal is not _RESERVATION_SEAL
                or _RESERVATION.get() is not self or _WRITE_WINDOW.get() is not con
                or self.connection is not con or not con.in_transaction
                or self.owner != lifecycle._owner()
                or lifecycle.require_guard(profile) is not self.guard
                or profile.identity != self.profile.identity):
            _fail("RESERVATION_CREATOR_REQUIRED")
        storage_admin.from_connection(con, profile=profile)
        original = dict(self.record_items)
        _same((operation_id, expected_prepare_sha256),
              (original["operation_id"], original["prepare_sha256"]), "OPERATION_CONFLICT")
        _same(_raw_intent(con, operation_id, expected_prepare_sha256), original, "INTENT_CHANGED")
        # This bounded manifest was derived outside the writer from exactly the
        # original bytes now read back from this creator's actual parent row.
        # The caller supplies selectors only, not a manifest or permission DTO.
        return facts._decode(self.manifest_json, list)


@contextmanager
def _reservation_capture(con, profile, record, prepared):
    _require_read_window()
    if _RESERVATION.get() is not None:
        _fail("RESERVATION_CREATOR_REQUIRED")
    capture = _ReservationCapture(con, profile, lifecycle.require_guard(profile),
        tuple(sorted(record.items())), facts._json(wire.attachment_manifest(prepared)),
        lifecycle._owner(), _RESERVATION_SEAL)
    token = _RESERVATION.set(capture)
    try:
        yield
    finally:
        _RESERVATION.reset(token)


def _prepared_manifest_for_reservation_locked(con, *, profile, operation_id, expected_prepare_sha256):
    captured = _RESERVATION.get()
    if type(captured) is not _ReservationCapture:
        _fail("RESERVATION_CREATOR_REQUIRED")
    return captured.manifest(con, profile=profile, operation_id=operation_id,
                             expected_prepare_sha256=expected_prepare_sha256)


def _fail(code, *, status=409):
    raise SyncError("Personal mirror facts could not be verified", "PERSONAL_MIRROR_" + code, status) from None


def _checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            # A cancellation callback may observe external caller objects.
            # Freeze selectors before it can replace the original peer or
            # session generation; every use still requires actual admission.
            for name in ("source", "lease"):
                if name in kwargs:
                    kwargs[name] = deepcopy(kwargs[name])
            return function(*args, **kwargs)
        except (wire.PersonalMirrorTransferError, schema.PersonalMirrorSchemaError,
                facts.PersonalSnapshotError) as exc:
            raise SyncError("Personal mirror facts could not be verified", exc.code, 409) from None
    return wrapped


def _same(actual, expected, code="SOURCE_CHANGED"):
    if actual != expected:
        _fail(code)


def _enabled():
    # Follow the existing fresh credential path without invalidating the model
    # cache or serializing any other local configuration field.
    try:
        local = config._read_local(config.local_path())
        value = os.environ.get("RENO_PERSONAL_MIRROR_ENABLED", local.get("personal_mirror_enabled", False))
    except (OSError, ValueError, TypeError):
        _fail("CONFIG_UNAVAILABLE", status=503)
    return value is True or type(value) is str and value.lower() in {"true", "1"}


def _admit_receiver_locked(con, *, profile, source, lease, now=None):
    """Actual receiver/pair/session admission, not a transferable DTO proof."""
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    if type(con) is not sqlite3.Connection and not mirror_sql.is_owned(con):
        _fail("CONNECTION_REQUIRED")
    storage_admin.from_connection(con, profile=profile)
    if not _enabled():
        _fail("DISABLED", status=403)
    sync_transfer._admit(con, source, lease, now)
    if con.execute("SELECT 1 FROM sqlite_temp_master LIMIT 1").fetchone() is not None:
        _fail("TEMP_NAMESPACE_PRESENT")
    if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        _fail("FOREIGN_KEYS_REQUIRED")


@contextmanager
def _connection(profile):
    _require_read_window()
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    con = mirror_sql.open_existing(profile)
    try:
        storage_admin.from_connection(con, profile=profile)
        yield con
    except BaseException as exc:
        rejected = mirror_sql.was_rejected(con)
        mirror_sql.abort_writer(con)
        if rejected and isinstance(exc, sqlite3.Error):
            _fail("WRITER_CHANGED")
        raise
    else:
        mirror_sql.close_owned(con)


class _NodeHold:
    def __init__(self, profile, stack):
        self.profile = profile
        self.directory = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        self.path_signature = source_files.file_signature(self.directory.entry_stat("node-identity.json"))
        self.stream = stack.enter_context(self.directory.open_file("node-identity.json"))
        self.signature = source_files.file_signature(os.fstat(self.stream.fileno()))
        # The Windows path and handle APIs expose different ctime semantics.
        # Freeze and recheck each complete signature independently, as the
        # existing snapshot file holder does; do not merge their ctime fields.
        _same(self.path_signature[:4], self.signature[:4])
        if not 0 < self.signature[2] <= MAX_NODE_BYTES:
            _fail("NODE_IDENTITY_INVALID")
        self.stream.seek(0)
        self.raw = self.stream.read(MAX_NODE_BYTES + 1)
        try:
            value = facts._decode(self.raw)
            if set(value) != {"instance_id"}:
                _fail("NODE_IDENTITY_INVALID")
            self.node_id = facts._uuid(value["instance_id"])
        except (ValueError, TypeError):
            _fail("NODE_IDENTITY_INVALID")
        self.short()

    def short(self):
        lifecycle.require_guard(self.profile)
        self.directory.verify()
        opened = os.fstat(self.stream.fileno())
        path = self.directory.entry_stat("node-identity.json")
        safe._safe_stat(opened, directory=False)
        safe._safe_stat(path, directory=False)
        if opened.st_nlink != 1 or path.st_nlink != 1:
            _fail("NODE_IDENTITY_INVALID")
        _same(source_files.file_signature(opened), self.signature)
        _same(source_files.file_signature(path), self.path_signature)

    def full(self):
        _require_read_window()
        self.short()
        self.stream.seek(0)
        _same(self.stream.read(MAX_NODE_BYTES + 1), self.raw)
        self.short()


def _row(con, table, column, value):
    return con.execute(f'SELECT * FROM main."{table}" WHERE "{column}"=?', (value,)).fetchone()


def _raw_intent(con, operation_id, expected_prepare_sha256):
    value = _row(con, schema.INTENT, "operation_id", operation_id)
    if value is None:
        _fail("PREPARE_REQUIRED", status=404)
    record = dict(value)
    _same(record["prepare_sha256"], expected_prepare_sha256, "OPERATION_CONFLICT")
    return record


def _validated_intent(record):
    _require_read_window()
    prepared = wire.normalize_prepare(facts._decode(record["prepare_json"]))
    _same(wire._digest(prepared), record["prepare_sha256"], "INTENT_CHANGED")
    expected = _intent_values(prepared, record["created_at"])
    for key in expected:
        if key not in {"state", "updated_at"}:
            _same(record[key], expected[key], "INTENT_CHANGED")
    if record["state"] not in {"prepared", "installed"}:
        _fail("INTENT_CHANGED")
    return prepared


def _intent_values(prepared, stamp):
    source, receiver, previous = prepared["source"], prepared["receiver"], prepared["previous"]
    return {"operation_id": prepared["operation_id"], "format_version": 1,
        "prepare_json": facts._json(prepared), "prepare_sha256": wire.prepare_sha256(prepared),
        "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
        "source_library_id": source["library_id"], "source_node_id": source["node_id"],
        "source_primary_id": source["personal_primary_id"], "source_primary_epoch": source["primary_epoch"],
        "receiver_library_id": receiver["library_id"], "receiver_node_id": receiver["node_id"],
        "previous_snapshot_id": None if previous is None else previous["snapshot_id"],
        "previous_snapshot_sha256": None if previous is None else previous["snapshot_sha256"],
        "expected_revision": prepared["expected_revision"], "state": "prepared", "created_at": stamp, "updated_at": stamp}


def _binding(con, prepared, source, node):
    _same((prepared["source"]["library_id"], prepared["source"]["node_id"]),
          (source["library_id"], source["node_id"]), "PEER_MISMATCH")
    library, instance = queue._local_identity(con)
    _same(instance, node.node_id, "NODE_IDENTITY_CHANGED")
    _same(prepared["receiver"], {"library_id": library, "node_id": instance}, "RECEIVER_CHANGED")


def _receipt_record(con, intent, prepared):
    row = _row(con, schema.RECEIPT, "operation_id", prepared["operation_id"])
    if row is None:
        if intent["state"] == "installed":
            _fail("RECEIPT_MISSING")
        return None
    record = dict(row)
    result = wire.normalize_receipt(prepared, facts._decode(record["receipt_json"]))
    if result["replayed"] is not False or intent["state"] != "installed":
        _fail("RECEIPT_CHANGED")
    for name in ("operation_id", "format_version", "prepare_sha256", "snapshot_id", "snapshot_sha256",
                 "installed_revision", "receipt_sha256", "committed_at"):
        _same(record[name], result[name], "RECEIPT_CHANGED")
    return record, result


def _head(con):
    rows = con.execute(f'SELECT * FROM main."{schema.HEAD}" ORDER BY slot').fetchall()
    if len(rows) > 1 or rows and rows[0]["slot"] != 1:
        _fail("HEAD_CHANGED")
    return None if not rows else dict(rows[0])


def _meta(con):
    keys = ("library_id", *PERSONAL_META)
    return dict(con.execute("SELECT key,value FROM main.meta WHERE key IN (?,?,?,?)", keys))


def _personal_schema(con):
    _require_read_window()
    descriptor = facts.validate_database_schema(con)
    names = set(facts.TABLE_NAMES)
    if any(str(row[0]).lower() in names for row in con.execute(
            "SELECT tbl_name FROM main.sqlite_master WHERE type='trigger'")):
        _fail("PERSONAL_SCHEMA_CHANGED")
    return descriptor


def _actual_tables(con, descriptor, *, check_cancelled=None):
    _require_read_window()
    result, count, byte_size = {}, 0, 0
    check = check_cancelled or (lambda: None)
    for table in facts.TABLE_NAMES:
        check()
        columns = [item["name"] for item in descriptor["tables"][table]["columns"]]
        projection = ",".join('"' + name + '"' for name in columns)
        order = ",".join('"' + name + '"' for name in descriptor["tables"][table]["order_by"])
        rows = con.execute(f'SELECT {projection} FROM main."{table}" ORDER BY {order}')
        result[table] = []
        for row in rows:
            check()
            count += 1
            if count > facts.MAX_ROWS:
                _fail("ROW_LIMIT")
            item = dict(zip(columns, tuple(row)))
            for value in item.values():
                if value is not None and not isinstance(value, (str, int, float)):
                    _fail("CELL_LIMIT")
                if isinstance(value, str) and len(value.encode("utf-8")) > facts.MAX_CELL_BYTES:
                    _fail("CELL_LIMIT")
            byte_size += len(facts._json(item).encode("utf-8"))
            if byte_size > facts.MAX_SNAPSHOT_BYTES:
                _fail("BYTE_LIMIT")
            result[table].append(item)
    return result


def _accepted(con, *, check_tables=True, check_cancelled=None):
    descriptor = _personal_schema(con)
    head, meta = _head(con), _meta(con)
    if head is None:
        if (meta.get("personal_primary_id") or meta.get("default_project_id")
                or meta.get("primary_epoch") not in {None, "1"}):
            _fail("EXISTING_PERSONAL_DATA")
        for table in facts.TABLE_NAMES:
            if con.execute(f'SELECT 1 FROM main."{table}" LIMIT 1').fetchone() is not None:
                _fail("EXISTING_PERSONAL_DATA")
        return None, None, meta
    original = _row(con, schema.INTENT, "operation_id", head["operation_id"])
    if original is None:
        _fail("HEAD_CHANGED")
    intent = _raw_intent(con, head["operation_id"], original["prepare_sha256"])
    prepared = _validated_intent(intent)
    selected = _receipt_record(con, intent, prepared)
    if selected is None:
        _fail("HEAD_CHANGED")
    _, receipt = selected
    expected = _head_values(prepared, receipt)
    _same(head, expected, "HEAD_CHANGED")
    document = facts.validate_snapshot(prepared["snapshot_json"]).document
    wanted = {row["key"]: row["value"] for row in document["meta"] if row["key"] in PERSONAL_META}
    _same({key: meta.get(key) for key in PERSONAL_META}, wanted, "ACCEPTED_DATA_CHANGED")
    _same(meta.get("library_id"), prepared["receiver"]["library_id"], "RECEIVER_CHANGED")
    if check_tables:
        _same(_actual_tables(con, descriptor, check_cancelled=check_cancelled), document["tables"], "ACCEPTED_DATA_CHANGED")
    return head, prepared, meta


def _predecessor(prepared, head, previous):
    if head is None:
        _same((prepared["previous"], prepared["expected_revision"]), (None, 0), "PREDECESSOR_CHANGED")
    else:
        _same((prepared["previous"], prepared["expected_revision"]),
              ({"snapshot_id": head["snapshot_id"], "snapshot_sha256": head["snapshot_sha256"]}, head["revision"]),
              "PREDECESSOR_CHANGED")
        _same(prepared["source"], previous["source"], "PRIMARY_CHANGED")
        wire.normalize_prepare(prepared, prior_snapshot_json=previous["snapshot_json"])


def _capture_fixed(con):
    return con.execute("PRAGMA main.schema_version").fetchone()[0], con.execute("PRAGMA data_version").fetchone()[0]


def _fixed(con, *, profile, source, lease, node, observed, head, intent=None, now=None):
    mirror_sql.check_writer(con)
    if _WRITE_WINDOW.get() is not con or not con.in_transaction:
        _fail("WRITER_REQUIRED")
    _same(_capture_fixed(con), observed)
    node.short()
    _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
    _same(_head(con), head)
    if intent is not None:
        _same(dict(_row(con, schema.INTENT, "operation_id", intent["operation_id"]) or {}), intent)


def _insert(con, table, record):
    columns = ",".join('"' + name + '"' for name in record)
    con.execute(f'INSERT INTO main."{table}"({columns}) VALUES({",".join("?" for _ in record)})', tuple(record.values()))


def _prepare_result(record, prepared):
    return {"operation_id": prepared["operation_id"], "prepare_sha256": record["prepare_sha256"],
        "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
        "state": record["state"], "attachment_count": len(wire.attachment_manifest(prepared))}


@_checked
def original_prepare_locked(*, profile, source, lease, operation_id, expected_prepare_sha256, now=None):
    """Read the original persisted request; returned facts grant no writer."""
    facts._uuid(operation_id)
    facts._sha(expected_prepare_sha256)
    with _connection(profile) as con, ExitStack() as stack:
        node = _NodeHold(profile, stack)
        con.execute("BEGIN")
        _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
        schema.validate(con)
        record = _raw_intent(con, operation_id, expected_prepare_sha256)
        prepared = _validated_intent(record)
        _binding(con, prepared, source, node)
        node.full()
        return deepcopy(prepared)


@_checked
def prepare_mirror_locked(*, profile, source, lease, prepare, check_cancelled=None, now=None):
    """Freeze one exact parent and all ready descriptors; never install facts."""
    from . import personal_mirror_transfer_files as files
    _require_read_window()
    prepared = wire.normalize_prepare(prepare)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("CANCELLATION_INVALID")
    check = check_cancelled or (lambda: None)
    check()
    with _connection(profile) as con, ExitStack() as stack:
        node = _NodeHold(profile, stack)
        con.execute("BEGIN")
        _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
        schema.validate(con)
        _binding(con, prepared, source, node)
        existing = _row(con, schema.INTENT, "operation_id", prepared["operation_id"])
        if existing is not None:
            record = dict(existing)
            wire.require_same_prepare(_validated_intent(record), prepared)
            _receipt_record(con, record, prepared)
            node.full()
            return _prepare_result(record, prepared)
        head, prior, _ = _accepted(con)
        _predecessor(prepared, head, prior)
        observed = _capture_fixed(con)
        _, stamp = queue._clock(now)
        record = _intent_values(prepared, stamp)
        node.full()
        check()
        con.rollback()
        with _reservation_capture(con, profile, record, prepared):
            writer_token = _begin_writer(con)
            try:
                _fixed(con, profile=profile, source=source, lease=lease, node=node, observed=observed, head=head, now=now)
                _insert(con, schema.INTENT, record)
                files.reserve_prepared_attachments_locked(con, profile=profile,
                    operation_id=record["operation_id"], expected_prepare_sha256=record["prepare_sha256"])
                check()
                _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
                node.short()
                mirror_sql.commit_writer(con)
            except BaseException:
                mirror_sql.abort_writer(con)
                raise
            finally:
                _WRITE_WINDOW.reset(writer_token)
        return _prepare_result(record, prepared)


@_checked
def mirror_receipt_locked(*, profile, source, lease, commit, now=None):
    """Pure historical committed lookup; no file upload, install or new UUID."""
    selected = wire.normalize_commit(commit)
    with _connection(profile) as con, ExitStack() as stack:
        node = _NodeHold(profile, stack)
        con.execute("BEGIN")
        _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
        schema.validate(con)
        intent = _raw_intent(con, selected["operation_id"], selected["prepare_sha256"])
        prepared = _validated_intent(intent)
        wire.normalize_commit(selected, prepare=prepared)
        _binding(con, prepared, source, node)
        saved = _receipt_record(con, intent, prepared)
        node.full()
        return None if saved is None else {**deepcopy(saved[1]), "replayed": True}


def _head_values(prepared, receipt):
    source, receiver = prepared["source"], prepared["receiver"]
    return {"slot": 1, "format_version": 1, "operation_id": prepared["operation_id"],
        "source_library_id": source["library_id"], "source_node_id": source["node_id"],
        "source_primary_id": source["personal_primary_id"], "source_primary_epoch": source["primary_epoch"],
        "receiver_library_id": receiver["library_id"], "receiver_node_id": receiver["node_id"],
        "revision": receipt["installed_revision"], "snapshot_id": prepared["snapshot_id"],
        "snapshot_sha256": prepared["snapshot_sha256"], "receipt_sha256": receipt["receipt_sha256"],
        "updated_at": receipt["committed_at"]}


def _install_personal_rows(con, document, *, check_cancelled):
    con.execute("PRAGMA defer_foreign_keys=ON")
    for table in reversed(facts.TABLE_NAMES):
        check_cancelled()
        con.execute(f'DELETE FROM main."{table}"')
    for table in facts.TABLE_NAMES:
        rows = document["tables"][table]
        columns = [item["name"] for item in document["schema"]["tables"][table]["columns"]]
        projection = ",".join('"' + name + '"' for name in columns)
        sql = f'INSERT INTO main."{table}"({projection}) VALUES({",".join("?" for _ in columns)})'
        for row in rows:
            check_cancelled()
            con.execute(sql, tuple(row[name] for name in columns))
    for row in document["meta"]:
        if row["key"] in PERSONAL_META:
            con.execute("INSERT INTO main.meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (row["key"], row["value"]))


@_checked
def commit_mirror_locked(*, profile, source, lease, commit, check_cancelled=None, now=None):
    """Install original prepared facts after real held files and predecessor CAS."""
    from . import personal_mirror_transfer_files as files
    _require_read_window()
    selected = wire.normalize_commit(commit)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("CANCELLATION_INVALID")
    check = check_cancelled or (lambda: None)
    check()
    with _connection(profile) as con, ExitStack() as stack:
        node = _NodeHold(profile, stack)
        con.execute("BEGIN")
        _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
        schema.validate(con)
        intent = _raw_intent(con, selected["operation_id"], selected["prepare_sha256"])
        prepared = _validated_intent(intent)
        wire.normalize_commit(selected, prepare=prepared)
        _binding(con, prepared, source, node)
        saved = _receipt_record(con, intent, prepared)
        if saved is not None:
            node.full()
            return {**deepcopy(saved[1]), "replayed": True}
        head, prior, original_meta = _accepted(con)
        _predecessor(prepared, head, prior)
        document = facts.validate_snapshot(prepared["snapshot_json"]).document
        observed = _capture_fixed(con)
        node.full()
        con.rollback()
        with files.hold_prepared_attachments_locked(profile=profile, operation_id=selected["operation_id"],
                expected_prepare_sha256=selected["prepare_sha256"], check_cancelled=check) as held:
            if type(held) is not files.PreparedAttachmentHold:
                _fail("ORIGINAL_FILES_REQUIRED")
            held.verify_source()
            _, stamp = queue._clock(now)
            result = {"format_version": 1, "kind": "personal_mirror_receipt",
                "operation_id": prepared["operation_id"], "prepare_sha256": selected["prepare_sha256"],
                "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
                "source": deepcopy(prepared["source"]), "receiver": deepcopy(prepared["receiver"]),
                "installed_revision": prepared["expected_revision"] + 1, "counts": deepcopy(document["counts"]),
                "attachment_manifest_sha256": wire.attachment_manifest_sha256(prepared),
                "committed_at": stamp, "receipt_sha256": "0" * 64, "replayed": False}
            result["receipt_sha256"] = wire.receipt_sha256(result)
            wire.normalize_receipt(prepared, result)
            next_head = _head_values(prepared, result)
            receipt = {name: result[name] for name in ("operation_id", "format_version", "prepare_sha256", "snapshot_id",
                "snapshot_sha256", "installed_revision", "receipt_sha256", "committed_at")}
            receipt["receipt_json"] = facts._json(result)
            check()
            # Long immutable-file verification may overlap harmless queue
            # heartbeats. Re-read the complete personal visibility boundary
            # now, outside the writer, instead of treating those earlier WAL
            # commits as personal changes or weakening the actual-row proof.
            con.execute("BEGIN")
            _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], observed[0])
            current_intent = _raw_intent(con, selected["operation_id"], selected["prepare_sha256"])
            _same({key: value for key, value in current_intent.items() if key not in {"state", "updated_at"}},
                  {key: value for key, value in intent.items() if key not in {"state", "updated_at"}}, "INTENT_CHANGED")
            completed = _receipt_record(con, current_intent, prepared)
            if completed is not None:
                node.full()
                return {**deepcopy(completed[1]), "replayed": True}
            current_head, current_prior, current_meta = _accepted(con)
            _predecessor(prepared, current_head, current_prior)
            _same(current_head, head, "PREDECESSOR_CHANGED")
            _same(current_prior, prior, "PREDECESSOR_CHANGED")
            _same(current_meta, original_meta, "ACCEPTED_DATA_CHANGED")
            intent, observed = current_intent, _capture_fixed(con)
            node.full()
            con.rollback()
            writer_token = _begin_writer(con)
            try:
                _fixed(con, profile=profile, source=source, lease=lease, node=node, observed=observed, head=head, intent=intent, now=now)
                held.verify_for_commit(con, profile=profile)
                _install_personal_rows(con, document, check_cancelled=check)
                _insert(con, schema.RECEIPT, receipt)
                if head is None:
                    _insert(con, schema.HEAD, next_head)
                else:
                    assignment = ",".join('"' + name + '"=?' for name in next_head if name != "slot")
                    values = tuple(value for name, value in next_head.items() if name != "slot")
                    changed = con.execute(f'UPDATE main."{schema.HEAD}" SET {assignment} WHERE slot=1 AND revision=? AND operation_id=?',
                        (*values, head["revision"], head["operation_id"])).rowcount
                    if changed != 1:
                        _fail("PREDECESSOR_CHANGED")
                changed = con.execute(f'UPDATE main."{schema.INTENT}" SET state=\'installed\',updated_at=? WHERE operation_id=? AND state=\'prepared\' AND prepare_sha256=?',
                    (stamp, selected["operation_id"], selected["prepare_sha256"])).rowcount
                if changed != 1:
                    _fail("INTENT_CHANGED")
                con.execute(f'UPDATE main."{schema.ATTACHMENT}" SET state=\'installed\' WHERE operation_id=? AND state=\'stored\'',
                            (selected["operation_id"],))
                check()
                _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
                node.short()
                held.verify_for_commit(con, profile=profile)
                mirror_sql.commit_writer(con)
            except BaseException:
                mirror_sql.abort_writer(con)
                raise
            finally:
                _WRITE_WINDOW.reset(writer_token)
        return deepcopy(result)


@_checked
def accepted_mirror_facts_locked(*, profile, source, lease, now=None):
    """Read actual accepted rows/receipt only; returned facts grant no reading API."""
    from . import personal_mirror_transfer_files as files
    with _connection(profile) as con, ExitStack() as stack:
        node = _NodeHold(profile, stack)
        con.execute("BEGIN")
        _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
        schema.validate(con)
        head, prepared, _ = _accepted(con)
        if head is None:
            return None
        _binding(con, prepared, source, node)
        node.full()
        observed = _capture_fixed(con)
        original = _row(con, schema.INTENT, "operation_id", head["operation_id"])
        if original is None:
            _fail("HEAD_CHANGED")
        original_sha256 = original["prepare_sha256"]
        snapshot = facts.validate_snapshot(prepared["snapshot_json"])
        con.rollback()
        with files.hold_prepared_attachments_locked(profile=profile, operation_id=head["operation_id"],
                expected_prepare_sha256=original_sha256) as held:
            if type(held) is not files.PreparedAttachmentHold:
                _fail("ORIGINAL_FILES_REQUIRED")
            held.verify_source()
            _same(_capture_fixed(con), observed)
            con.execute("BEGIN")
            _same(_head(con), head)
            _admit_receiver_locked(con, profile=profile, source=source, lease=lease, now=now)
            node.full()
            result = {"head": deepcopy(head), "snapshot": snapshot}
        return result
