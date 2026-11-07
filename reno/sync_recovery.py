"""Local-only logical recovery with CAS, durable hold and immutable-file pins.

restore/resume own their entire transaction, including COMMIT while file
handles remain pinned. Network publication guards instead participate in the
caller's transaction. No function restores identities, queues or raw events.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from . import config, db, review, sync_checkpoints as checkpoints, sync_reports, storage_lifecycle
from .sync_media import _Directory, _safe_stat
from .sync_protocol import SyncError

MAX_INTEGER = 2**63 - 1


def _fail(message, code="SYNC_RECOVERY_INVALID", status=409):
    raise SyncError(message, code, status)


def _integer(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        _fail("Recovery generation/revision must be a nonnegative integer")
    return value


def _next(value):
    if _integer(value) == MAX_INTEGER:
        _fail("Recovery counter is exhausted", "SYNC_RECOVERY_COUNTER_EXHAUSTED")
    return value + 1


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def state(con):
    """Read-only wire DTO; migration never seeds an artificial hold."""
    version = db.knowledge_version(con)
    stream = {"library_id": version["library_id"], "library_epoch": version["library_epoch"]}
    row = con.execute("SELECT state,generation,revision,source_floor_json FROM sync_recovery_control WHERE slot=1").fetchone()
    if row is None:
        return {"generation": 0, "revision": 0, "state": "running", "source_floor": None, "stream": stream}
    try:
        generation, revision = _integer(row["generation"]), _integer(row["revision"])
        floor = checkpoints._version(json.loads(row["source_floor_json"])) if row["source_floor_json"] is not None else None
        if row["state"] not in {"running", "held", "resume_pending"} or revision < generation:
            raise ValueError
        if row["state"] != "running" and generation == 0:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        _fail("Persistent recovery state is invalid", "SYNC_RECOVERY_STATE_INVALID", 503)
    return {"generation": generation, "revision": revision, "state": row["state"], "source_floor": floor, "stream": stream}


def assert_recovery_fence(con, generation, recovery_stream=None, *, allow_resume=False):
    """Fence receiver epoch and generation inside the caller's transaction.

    Only legacy generation-zero Python callers may omit the receiver stream.
    HTTP callers must require it explicitly before entering this helper.
    """
    _integer(generation)
    control = state(con)
    legacy = recovery_stream is None and generation == control["generation"] == 0
    if not legacy and (
            type(recovery_stream) is not dict or set(recovery_stream) != {"library_id", "library_epoch"}
            or type(recovery_stream["library_epoch"]) is not int
            or recovery_stream != control["stream"]):
        _fail("Request belongs to another receiver recovery stream", "SYNC_RECOVERY_STREAM_MISMATCH")
    if generation != control["generation"]:
        _fail("Publication belongs to another recovery generation", "SYNC_RECOVERY_GENERATION_MISMATCH")
    if control["state"] == "held":
        _fail("Knowledge publication is held after recovery", "SYNC_RECOVERY_HELD")
    if control["state"] == "resume_pending" and not allow_resume:
        _fail("Resume requires a freshly generated source revision", "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED")
    return control


def assert_publish_allowed(con, generation, source_version, recovery_stream=None):
    """Check the fence before any replay or publication mutation."""
    control = assert_recovery_fence(con, generation, recovery_stream, allow_resume=True)
    source = checkpoints._version(source_version)
    floor = control["source_floor"]
    if floor is not None:
        if source["library_id"] != floor["library_id"]:
            _fail("Publication source differs from the recovery fence", "SYNC_PEER_MISMATCH")
        if control["state"] == "resume_pending" and (source["library_epoch"], source["kb_revision"]) <= (floor["library_epoch"], floor["kb_revision"]):
            _fail("Resume requires a freshly generated source revision", "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED")
    return control


def mark_published(con, generation, source_version, recovery_stream=None):
    """Complete resume inside a successful NEW publication's transaction."""
    if not con.in_transaction:
        _fail("Publication activation requires its existing write transaction")
    control = assert_publish_allowed(con, generation, source_version, recovery_stream)
    if control["state"] == "resume_pending":
        con.execute("UPDATE sync_recovery_control SET state='running',revision=?,updated_at=? WHERE slot=1",
                    (_next(control["revision"]), _stamp()))
    return state(con)


def _lookup(con, checkpoint_id, kind):
    checkpoints._uuid(checkpoint_id)
    if kind == "publication":
        return checkpoints._lookup(con, checkpoint_id)
    if kind != "recovery":
        _fail("Checkpoint kind must be publication or recovery")
    row = con.execute("SELECT c.* FROM sync_recovery_checkpoint c JOIN sync_recovery_operation o USING(operation_id) "
                      "WHERE c.checkpoint_id=? AND o.action='restore'", (checkpoint_id,)).fetchone()
    if row is None:
        _fail("Committed recovery protection checkpoint was not found", "SYNC_CHECKPOINT_NOT_FOUND", 404)
    entry = dict(row)
    checkpoints._assert_not_retired(con, entry)
    return entry


def _counts(con):
    return {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in checkpoints.KNOWLEDGE_COLUMNS if table != "meta"} | {
                "meta": con.execute("SELECT COUNT(*) FROM meta WHERE substr(key,1,5)='step:'").fetchone()[0]}


def preview_restore(con, checkpoint_id, *, checkpoint_kind="publication", root=None, media_store=None, report_root=None, profile=None):
    profile = checkpoints._profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with checkpoints._read_transaction(con, profile):
        entry = _lookup(con, checkpoint_id, checkpoint_kind)
        result = checkpoints._preview(con, entry, profile=profile)
        current, control = db.knowledge_version(con), state(con)
        return {**result, "checkpoint_kind": checkpoint_kind, "current_version": current,
                "control_revision": control["revision"], "control": control,
                "restore_action": {"current_row_counts": _counts(con), "restored_row_counts": result["row_counts"],
                                   "replace_complete_knowledge": True, "replay_current_reviews": True,
                                   "holds_publication": True, "preserves_personal_and_sync_history": True}}


def list_recovery_checkpoints(con, *, root=None, media_store=None, report_root=None, limit=100, profile=None):
    if type(limit) is not int or not 1 <= limit <= 1000:
        _fail("Checkpoint list limit must be between 1 and 1000")
    profile = checkpoints._profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with checkpoints._read_transaction(con, profile):
        rows = con.execute("SELECT c.* FROM sync_recovery_checkpoint c JOIN sync_recovery_operation o USING(operation_id) "
                           "WHERE o.action='restore' ORDER BY c.created_at DESC,c.checkpoint_id DESC LIMIT ?", (limit,)).fetchall()
        return [checkpoints._preview(con, dict(row), profile=profile) for row in rows]


class _Pins:
    """Keep exact file objects; final reads bypass Python's read-ahead buffer."""
    def __init__(self, stack):
        self.stack, self.files = stack, []

    @staticmethod
    def _read(stream, size, *, capture=False):
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
        digest, chunks, total = hashlib.sha256(), [], 0
        while total <= size:
            chunk = os.read(stream.fileno(), min(1024 * 1024, size + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        return digest.hexdigest(), total, b"".join(chunks) if capture else None

    def add(self, root, key, digest, size, maximum, *, capture=False):
        if type(size) is not int or not 0 <= size <= maximum:
            _fail("Recovery file size is invalid", "SYNC_CHECKPOINT_DAMAGED")
        path = Path(root) / key
        directory = self.stack.enter_context(_Directory(path.parent, base=root))
        stream = self.stack.enter_context(directory.open_file(path.name))
        original = os.fstat(stream.fileno())
        found, length, data = self._read(stream, size, capture=capture)
        if length != size or original.st_size != size or found != digest:
            _fail("Recovery dependency checksum differs", "SYNC_CHECKPOINT_DAMAGED")
        item = (directory, path.name, stream, digest, size,
                (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns))
        self.files.append(item)
        self._identity(item)
        return data

    @staticmethod
    def _identity(item):
        directory, name, stream, _, _, original = item
        directory.verify()
        current, handle = directory.entry_stat(name), os.fstat(stream.fileno())
        _safe_stat(current, directory=False)
        if (current.st_dev, current.st_ino) != original[:2] or current.st_nlink != 1 or (
                handle.st_dev, handle.st_ino, handle.st_size, handle.st_mtime_ns, handle.st_ctime_ns) != original:
            _fail("Recovery dependency changed while held", "SYNC_CHECKPOINT_DAMAGED")

    def verify(self):
        for item in self.files:
            self._identity(item)
            _, _, stream, digest, size, _ = item
            found, length, _ = self._read(stream, size)
            if found != digest or length != size:
                _fail("Recovery dependency changed before commit", "SYNC_CHECKPOINT_DAMAGED")
            self._identity(item)


@contextmanager
def _hold_image(con, entry, *, root=None, media_store=None, report_root=None, dependencies=True, profile=None):
    profile = checkpoints._profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    storage_lifecycle.require_guard(profile)
    entry = checkpoints._entry(entry)
    checkpoints._assert_not_retired(con, entry)
    if json.loads(entry["receiver_version_json"])["library_id"] != db.knowledge_version(con)["library_id"]:
        _fail("Checkpoint belongs to another library", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
    with ExitStack() as stack:
        pins = _Pins(stack)
        data = pins.add(profile.checkpoints, entry["storage_key"], entry["sha256"],
                        entry["byte_size"], checkpoints.MAX_ARCHIVE_BYTES, capture=True)
        archive, manifest = checkpoints._decode(data, entry)
        del data
        stack.callback(archive.close)
        if manifest["receiver_node_id"] != checkpoints._node_id(con):
            _fail("Checkpoint belongs to another node", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
        if dependencies:
            media_root, reports = profile.media, profile.reports
            issues, _ = checkpoints._reference_issues(archive, media_root, reports, receiver=con, profile=profile)
            if issues or manifest["issues"]:
                _fail("Checkpoint references are incomplete", "SYNC_CHECKPOINT_NOT_RECOVERABLE")
            for row in archive.execute("SELECT * FROM checkpoint_media"):
                pins.add(media_root, row["storage_key"], row["sha256"], row["byte_size"], 512 * 1024 * 1024)
            for row in archive.execute("SELECT * FROM sync_report"):
                path = sync_reports._path(reports, row["name"], row["sha256"])
                if path.relative_to(reports).as_posix() != row["storage_key"]:
                    _fail("Report pointer escapes its immutable namespace", "SYNC_CHECKPOINT_DAMAGED")
                pins.add(reports, row["storage_key"], row["sha256"], row["byte_size"], sync_reports.MAX_REPORT_BYTES)
        yield archive, manifest, pins


def _request(action, *, expected_version, expected_control_revision, operation_id, **extra):
    checkpoints._uuid(operation_id)
    version = checkpoints._version(expected_version)
    _integer(expected_control_revision)
    request = {"action": action, "expected_version": version, "expected_control_revision": expected_control_revision, **extra}
    encoded = checkpoints._json(request)
    return request, encoded, hashlib.sha256(encoded.encode()).hexdigest()


def _begin(con):
    if not config.is_cloud():
        _fail("Logical recovery requires the local cloud receiver", "SYNC_ROLE_REQUIRED", 403)
    if con.in_transaction:
        _fail("Recovery must own its transaction; finish pending writes first", "SYNC_RECOVERY_TRANSACTION_OPEN")
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            _fail("Recovery storage is busy; retry after the active writer", "SYNC_BUSY")
        raise


def _replayed(con, operation_id, signature):
    row = con.execute("SELECT request_sha256,result_json FROM sync_recovery_operation WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    if row["request_sha256"] != signature:
        _fail("Recovery operation ID was used for another request", "SYNC_RECOVERY_OPERATION_CONFLICT")
    return {**json.loads(row["result_json"]), "replayed": True}


def _cas(con, request):
    if db.knowledge_version(con) != request["expected_version"]:
        _fail("Knowledge changed since recovery preview", "SYNC_RECOVERY_VERSION_CHANGED")
    control = state(con)
    if control["revision"] != request["expected_control_revision"]:
        _fail("Recovery control changed since preview", "SYNC_RECOVERY_CONTROL_CHANGED")
    return control


def _source_floor(con, control):
    pair = con.execute("SELECT source_library_id FROM sync_pair WHERE slot=1").fetchone()
    libraries = [pair[0]] if pair else [row[0] for row in con.execute("SELECT DISTINCT source_library_id FROM sync_publication")]
    if len(libraries) > 1:
        _fail("Recovery source is ambiguous without a peer binding", "SYNC_PEER_MISMATCH")
    row = con.execute("SELECT source_library_id,source_epoch,source_revision FROM sync_publication WHERE source_library_id=? "
                      "ORDER BY source_epoch DESC,source_revision DESC LIMIT 1", (libraries[0],)).fetchone() if libraries else None
    floor = checkpoints._version({"library_id": row[0], "library_epoch": row[1], "kb_revision": row[2]}) if row else None
    previous = control["source_floor"]
    if previous:
        if floor and previous["library_id"] != floor["library_id"]:
            _fail("Recovery cannot rebind the source", "SYNC_PEER_MISMATCH")
        if floor is None or (floor["library_epoch"], floor["kb_revision"]) < (previous["library_epoch"], previous["kb_revision"]):
            floor = previous
    return floor


def _save_operation(con, operation_id, action, encoded, signature, result, stamp):
    con.execute("INSERT INTO sync_recovery_operation VALUES(?,?,?,?,?,?)",
                (operation_id, action, signature, encoded, checkpoints._json(result), stamp))


def _replace_knowledge(con, archive):
    # Complete replacement also removes newer videos, steps, globals and FTS.
    # None of these deletes touch source review facts or publication history.
    manifest = json.loads(archive.execute("SELECT manifest_json FROM checkpoint_manifest WHERE slot=1").fetchone()[0])
    columns_by_table = checkpoints.columns_for_schema(manifest["schema_version"])
    con.execute("DELETE FROM atom_fts")
    order = ("evidence_ref", "knowledge_atom", "visual_observation", "ocr_span", "transcript_segment",
             "frame", "knowledge_cluster", "conflict_case", "video_asset", "sync_report")
    for table in order:
        con.execute(f'DELETE FROM "{table}"')
    con.execute("DELETE FROM meta WHERE substr(key,1,5)='step:'")
    for table, columns in columns_by_table.items():
        names = ",".join(f'"{name}"' for name in columns)
        checkpoints._insert_rows(con, table, columns, (tuple(row) for row in archive.execute(f'SELECT {names} FROM "{table}"')))
    db.fts_sync_atoms(con, [row[0] for row in con.execute("SELECT id FROM knowledge_atom")])
    review.replay(con)
    db.bump_kb_revision(con)


def restore(con, checkpoint_id, *, expected_version, expected_control_revision, operation_id,
            checkpoint_kind="publication", root=None, media_store=None, report_root=None, profile=None):
    if con.in_transaction:
        _fail("Recovery must own its transaction; finish pending writes first", "SYNC_RECOVERY_TRANSACTION_OPEN")
    profile = checkpoints._profile(con, profile=profile, root=root, media_store=media_store, report_root=report_root)
    with storage_lifecycle.shared(profile):
        return _restore(con, checkpoint_id, expected_version=expected_version,
            expected_control_revision=expected_control_revision, operation_id=operation_id,
            checkpoint_kind=checkpoint_kind, profile=profile)


def _restore(con, checkpoint_id, *, expected_version, expected_control_revision, operation_id,
             checkpoint_kind, profile):
    checkpoints._uuid(checkpoint_id)
    request, encoded, signature = _request("restore", expected_version=expected_version,
        expected_control_revision=expected_control_revision, operation_id=operation_id,
        checkpoint_id=checkpoint_id, checkpoint_kind=checkpoint_kind)
    _begin(con)
    try:
        replayed = _replayed(con, operation_id, signature)
        if replayed is not None:
            con.rollback()
            return replayed
        control = _cas(con, request)
        floor = _source_floor(con, control)
        entry = _lookup(con, checkpoint_id, checkpoint_kind)
        with _hold_image(con, entry, profile=profile) as (archive, manifest, pins):
            protection = checkpoints.stage_recovery(con, operation_id, profile=profile)
            with _hold_image(con, protection, dependencies=False, profile=profile) as (_, _, protection_pins):
                _replace_knowledge(con, archive)
                after = db.knowledge_version(con)
                before = request["expected_version"]
                if (after["library_id"] != before["library_id"] or after["library_epoch"] != before["library_epoch"]
                        or after["kb_revision"] <= before["kb_revision"]):
                    _fail("Recovery violated monotonic knowledge version", "SYNC_RECOVERY_VERSION_INVALID")
                stamp = _stamp()
                updated = {**control, "state": "held", "generation": _next(control["generation"]),
                           "revision": _next(control["revision"]), "source_floor": floor}
                result = {"operation_id": operation_id, "action": "restore", "checkpoint_id": checkpoint_id,
                          "checkpoint_kind": checkpoint_kind, "before_version": before, "version": after,
                          "control": updated, "protection_checkpoint_id": protection["checkpoint_id"],
                          "committed_at": stamp, "replayed": False}
                _save_operation(con, operation_id, "restore", encoded, signature, result, stamp)
                columns = tuple(protection)
                checkpoints._insert_rows(con, "sync_recovery_checkpoint", columns, [tuple(protection[key] for key in columns)])
                checkpoints._register_artifact(con, protection, profile)
                con.execute("INSERT INTO sync_recovery_control VALUES(1,?,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET "
                            "state=excluded.state,generation=excluded.generation,revision=excluded.revision,"
                            "source_floor_json=excluded.source_floor_json,restore_operation_id=excluded.restore_operation_id,updated_at=excluded.updated_at",
                            ("held", updated["generation"], updated["revision"], checkpoints._json(floor) if floor else None, operation_id, stamp))
                if con.execute("PRAGMA foreign_key_check").fetchone():
                    _fail("Recovery reference check failed", "SYNC_CHECKPOINT_NOT_RECOVERABLE")
                pins.verify()
                protection_pins.verify()
                if checkpoints._node_id(con) != manifest["receiver_node_id"]:
                    _fail("Receiver identity changed before commit", "SYNC_CHECKPOINT_IDENTITY_MISMATCH")
                con.commit()
                return result
    except OSError:
        if con.in_transaction:
            con.rollback()
        _fail("Recovery files could not be safely verified", "SYNC_CHECKPOINT_DAMAGED")
    except BaseException:
        if con.in_transaction:
            con.rollback()
        raise


def resume(con, *, expected_version, expected_control_revision, operation_id, profile=None):
    if con.in_transaction:
        _fail("Recovery must own its transaction; finish pending writes first", "SYNC_RECOVERY_TRANSACTION_OPEN")
    profile = checkpoints._profile(con, profile=profile)
    with storage_lifecycle.shared(profile):
        return _resume(con, expected_version=expected_version,
            expected_control_revision=expected_control_revision, operation_id=operation_id)


def _resume(con, *, expected_version, expected_control_revision, operation_id):
    request, encoded, signature = _request("resume", expected_version=expected_version,
        expected_control_revision=expected_control_revision, operation_id=operation_id)
    _begin(con)
    try:
        replayed = _replayed(con, operation_id, signature)
        if replayed is not None:
            con.rollback()
            return replayed
        control = _cas(con, request)
        if control["state"] != "held":
            _fail("Only a held receiver can explicitly resume", "SYNC_RECOVERY_NOT_HELD")
        stamp = _stamp()
        updated = {**control, "state": "resume_pending", "revision": _next(control["revision"])}
        result = {"operation_id": operation_id, "action": "resume", "version": request["expected_version"],
                  "control": updated, "committed_at": stamp, "replayed": False}
        _save_operation(con, operation_id, "resume", encoded, signature, result, stamp)
        con.execute("UPDATE sync_recovery_control SET state='resume_pending',revision=?,updated_at=? WHERE slot=1",
                    (updated["revision"], stamp))
        con.commit()
        return result
    except BaseException:
        if con.in_transaction:
            con.rollback()
        raise
