"""Single-owner journaled collection under a complete physical-root EX group.

Only trusted, frozen artifact identities are selected. Logical retirement is
committed before any unlink; retries never enlarge that selection. SQL and
candidate captures end before writes; the separate permanent-authority capture
remains held across each item's actions. Hashing/deletion has no SQL writer.
There is no automatic/HTTP entrypoint or config fallback. Explicit CLI execution
requires a preview digest and operation UUID; resume uses the original UUID.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextlib import ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import os
import re
import sqlite3
import stat

from . import db, storage_artifacts as artifacts, storage_gc_contract as contract
from . import storage_lifecycle as lifecycle, storage_members as members, storage_roots as roots
from . import sync_media as safe
from . import storage_personal_authority_roots as personal_authority_roots

_STATES = frozenset(("pending", "blocked", "retained", "deleted", "missing"))
_TERMINAL = frozenset(("retained", "deleted", "missing"))
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,99}\Z")
STAGE_DIRECTORY = ".reno-gc-stage-v1"
_AUTHORITY_GROUP = ContextVar("reno_gc_authority_group", default=None)
_AUTHORITY_RECOVERY = ContextVar("reno_gc_authority_recovery", default=False)


class GCError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise GCError(code)


class _Cancelled(BaseException):
    def __init__(self, original):
        self.original = original


class _ObservedDelete(BaseException):
    def __init__(self, original, released):
        self.original, self.released = original, released


def _check(callback):
    if callback is not None and not callable(callback):
        _fail("GC_INVALID_ARGUMENT")
    def check():
        if callback is not None:
            try:
                callback()
            except BaseException as exc:
                raise _Cancelled(exc) from None
    return check


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value):
    return contract.canonical_bytes(value).decode("utf-8")


def _reason(exc, fallback):
    code = getattr(exc, "code", fallback)
    return code if type(code) is str and _CODE.fullmatch(code) else fallback


@contextmanager
def _connection(profile):
    lifecycle.require_guard(profile, exclusive=True)
    con = db.connect(profile.database)
    failure = None
    try:
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, contract.MAX_JSON_BYTES + 64 * 1024)
        # Extra SQL triggers are not part of the GC journal contract.
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN "
                       "('storage_gc_operation','storage_gc_item','sync_checkpoint_retirement') LIMIT 1").fetchone():
            _fail("GC_SCHEMA_UNSAFE")
        yield con
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            con.close()
        except BaseException:
            if failure is None:
                raise


@contextmanager
def _transaction(con, profile, check):
    lifecycle.require_guard(profile, exclusive=True)
    if con.in_transaction:
        _fail("GC_TRANSACTION_ACTIVE")
    check()
    con.execute("BEGIN IMMEDIATE")
    try:
        artifacts.require_transaction(con, profile)
        yield
        check()
        lifecycle.require_guard(profile, exclusive=True)
        con.commit()
    except BaseException:
        try:
            con.rollback()
        except BaseException:
            pass
        raise


def _owner(profile, scan):
    value = scan.owner
    if members._profile(value["profile"]).identity != profile.identity:
        _fail("GC_OWNER_MISMATCH")
    return {"member_id": value["member_id"], "library_id": value["library_id"],
            "database_identity": {key: value["database_identity"][key] for key in ("device", "inode")}}


def _make_request(profile, scan, operation_id):
    files = {item["artifact_id"]: {**item, "intent": "delete_if_unreferenced"}
             for item in scan.eligible_artifacts}
    all_files = {item["artifact_id"]: item for item in scan.files}
    retirements = sorted((dict(item) for item in scan.proposed_retirements),
                         key=lambda item: (item["origin_kind"], item["checkpoint_id"]))
    for retirement in retirements:
        identifier = retirement["archive_item_id"]
        if identifier is None:
            continue
        item = all_files.get(identifier)
        # Graph ownership alone never overrides a blocker or physical proof.
        if item is None or item["status"] not in ("candidate", "referenced"):
            _fail("GC_RETIREMENT_ARTIFACT_UNPROVEN")
        if identifier not in files:
            files[identifier] = {**{field: item[field] for field in roots._ARTIFACT_FIELDS}, "intent": "retain"}
    request = {"format_version": 1, "action": "storage_gc", "operation_id": operation_id,
        "owner": _owner(profile, scan), "expected_preview_sha256": scan.preview_sha256,
        "expected_roots_sha256": scan.roots_sha256, "members_sha256": scan.members_binding["digest"],
        "policy_sha256": contract.digest(scan.policy), "retirements": retirements,
        "files": sorted(files.values(), key=lambda f: (f["namespace"], f["root_id"], f["storage_key"], f["artifact_id"]))}
    return contract.validate_request(request, policy=scan.policy)


def _read_operation(con, operation_id):
    row = con.execute("SELECT operation_id,request_sha256,preview_sha256,roots_sha256,policy_json,request_json,"
                      "state,created_at,updated_at FROM storage_gc_operation WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    operation = dict(row)
    policy = contract.validate_policy(contract.decode(operation["policy_json"], canonical=True))
    request = contract.validate_request(contract.decode(operation["request_json"], canonical=True), policy=policy)
    if (request["operation_id"] != operation_id or contract.digest(request) != operation["request_sha256"]
            or request["expected_preview_sha256"] != operation["preview_sha256"]
            or request["expected_roots_sha256"] != operation["roots_sha256"]
            or operation["state"] not in ("planned", "running", "blocked", "completed")):
        _fail("GC_OPERATION_CORRUPT")
    contract._stamp(operation["created_at"])
    contract._stamp(operation["updated_at"])
    return operation, request, policy


def _validate_owner(con, profile, request):
    lifecycle.require_guard(profile, exclusive=True)
    identity = members.require_enrolled(profile)
    value = request["owner"]
    if (identity["member_id"] != value["member_id"] or identity["library_id"] != value["library_id"]
            or any(identity["database_identity"][key] != value["database_identity"][key] for key in ("device", "inode"))
            or db.knowledge_version(con)["library_id"] != value["library_id"]):
        _fail("GC_OWNER_MISMATCH")


def _items(con, request):
    rows = con.execute("SELECT * FROM storage_gc_item WHERE operation_id=? LIMIT ?",
                       (request["operation_id"], contract.MAX_FILES + 1)).fetchall()
    if len(rows) != len(request["files"]):
        _fail("GC_ITEMS_CORRUPT")
    frozen = {file["artifact_id"]: file for file in request["files"]}
    result = {}
    for row in rows:
        item = dict(row)
        file = frozen.get(item["artifact_id"])
        if (file is None or item["artifact_id"] in result or item["state"] not in _STATES
                or any(item[key] != file[key] for key in contract._ITEM_IDENTITY)
                or type(item["deleted_bytes"]) is not int or not 0 <= item["deleted_bytes"] <= file["byte_size"]
                or item["state"] != "deleted" and item["deleted_bytes"] != 0
                or file["intent"] == "retain" and item["state"] != "retained"
                or item["reason_code"] is not None and (type(item["reason_code"]) is not str or not _CODE.fullmatch(item["reason_code"]))):
            _fail("GC_ITEMS_CORRUPT")
        current = artifacts.get(con, file["artifact_id"])
        if current is None or any(current[key] != file[key] for key in contract._ARTIFACT_IMMUTABLE):
            _fail("GC_ARTIFACT_OWNERSHIP_CHANGED")
        result[file["artifact_id"]] = item
    for selected in request["retirements"]:
        row = con.execute("SELECT * FROM sync_checkpoint_retirement WHERE origin_kind=? AND checkpoint_id=?",
            (selected["origin_kind"], selected["checkpoint_id"])).fetchone()
        if (row is None or row["operation_id"] != request["operation_id"]
                or any(row[key] != selected[key] for key in ("storage_key", "sha256", "byte_size"))):
            _fail("GC_RETIREMENT_CORRUPT")
        contract._stamp(row["retired_at"])
    return result


def _journal(con, profile, scan, request, check):
    """The only initial journal commit: operation + files + retirements."""
    stamp = _stamp()
    with _transaction(con, profile, check):
        if roots.owner_sql_sha256(con, check_cancelled=check) != scan.owner_sql_sha256:
            _fail("GC_OWNER_SQL_CHANGED")
        # No member/StateStore discovery below BEGIN: exact guard suffices.
        con.execute("INSERT INTO storage_gc_operation(operation_id,request_sha256,preview_sha256,roots_sha256,"
                    "policy_json,request_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,'planned',?,?)",
                    (request["operation_id"], contract.digest(request), request["expected_preview_sha256"],
                     request["expected_roots_sha256"], _json(scan.policy), _json(request), stamp, stamp))
        for file in request["files"]:
            fields = ("operation_id", *contract._ITEM_IDENTITY, "state", "deleted_bytes", "reason_code", "updated_at")
            values = (request["operation_id"], *(file[key] for key in contract._ITEM_IDENTITY),
                      "retained" if file["intent"] == "retain" else "pending", 0,
                      "GC_FROZEN_RETAIN" if file["intent"] == "retain" else None, stamp)
            con.execute(f"INSERT INTO storage_gc_item({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", values)
        for item in request["retirements"]:
            con.execute("INSERT INTO sync_checkpoint_retirement(origin_kind,checkpoint_id,operation_id,storage_key,"
                        "sha256,byte_size,retired_at) VALUES(?,?,?,?,?,?,?)",
                (item["origin_kind"], item["checkpoint_id"], request["operation_id"], item["storage_key"],
                 item["sha256"], item["byte_size"], stamp))


def _set_item(con, profile, request, file, state, reason, check, *, deleted_bytes=0):
    with _transaction(con, profile, check):
        previous = con.execute("SELECT state FROM storage_gc_item WHERE operation_id=? AND artifact_id=?",
                               (request["operation_id"], file["artifact_id"])).fetchone()
        if previous is None or previous["state"] not in ("pending", "blocked"):
            _fail("GC_ITEM_STATE_CHANGED")
        stamp = _stamp()
        con.execute("UPDATE storage_gc_item SET state=?,deleted_bytes=?,reason_code=?,updated_at=? "
                    "WHERE operation_id=? AND artifact_id=?",
                    (state, deleted_bytes, reason, stamp, request["operation_id"], file["artifact_id"]))
        con.execute("UPDATE storage_gc_operation SET state='running',updated_at=? WHERE operation_id=?",
                    (stamp, request["operation_id"]))


def _hash_frozen(stream, file, check, *, moved=False):
    info = os.fstat(stream.fileno())
    fields = {"file_device": str(info.st_dev), "file_inode": str(info.st_ino),
              "file_mtime_ns": str(info.st_mtime_ns), "byte_size": info.st_size}
    if not moved:
        fields["file_ctime_ns"] = str(info.st_ctime_ns)
    if info.st_nlink != 1 or any(file[key] != value for key, value in fields.items()):
        _fail("GC_FILE_IDENTITY_CHANGED")
    digest, received = hashlib.sha256(), 0
    while True:
        check()
        chunk = stream.read(128 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > file["byte_size"]:
            _fail("GC_FILE_IDENTITY_CHANGED")
        digest.update(chunk)
    after = os.fstat(stream.fileno())
    stable = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
    if stable(info) != stable(after) or received != file["byte_size"] or digest.hexdigest() != file["sha256"]:
        _fail("GC_FILE_IDENTITY_CHANGED")
    return info


def _stage_path(profile, request, file):
    return (getattr(profile, roots._ROOT_ROLE[file["namespace"]]) / STAGE_DIRECTORY /
            request["operation_id"] / file["artifact_id"])


def _write_stage_record(directory, name, expected):
    temporary = ".record-" + os.urandom(16).hex()
    identity, failure = None, None
    try:
        with directory.open_file(temporary, write=True, create=True) as stream:
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            safe._rename_new(directory, temporary, directory, name)
            os.fsync(directory.fd)
        except FileExistsError:
            pass
        with directory.open_file(name) as stream:
            if stream.read(4097) != expected or len(expected) > 4096:
                _fail("GC_STAGE_UNPROVEN")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if identity is not None:
                info = directory.entry_stat(temporary)
                if (info.st_dev, info.st_ino) == identity and info.st_nlink == 1:
                    directory.unlink(temporary)
        except FileNotFoundError:
            pass
        except BaseException:
            if failure is None:
                raise


@contextmanager
def _stage_directory(profile, request, file, *, create=False):
    """Private same-root staging derived solely from this frozen request.

    POSIX cannot atomically unlink by inode. Moving into this private 0700
    directory and checking the moved object closes the public-name swap gap.
    Same-account actors deliberately bypassing locks are not a sandbox.
    """
    lifecycle.require_guard(profile, exclusive=True)
    root = getattr(profile, roots._ROOT_ROLE[file["namespace"]])
    with ExitStack() as stack:
        directory = stack.enter_context(safe._Directory(root, base=root))
        for name in (STAGE_DIRECTORY, request["operation_id"], file["artifact_id"]):
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=directory.fd)
                    os.fsync(directory.fd)
                except FileExistsError:
                    pass
            child = stack.enter_context(safe._Directory(directory.path / name, base=root, parent=directory))
            info = os.fstat(child.fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                _fail("GC_STAGE_UNSAFE")
            directory = child
        expected = contract.canonical_bytes({"format_version": 1, "operation_id": request["operation_id"],
            "request_sha256": contract.digest(request), "file": file})
        if create:
            _write_stage_record(directory, "manifest.json", expected)
        with directory.open_file("manifest.json") as stream:
            if stream.read(4097) != expected or len(expected) > 4096:
                _fail("GC_STAGE_UNPROVEN")
        directory.verify()
        yield directory
        directory.verify()


def _staged(profile, request, file):
    if os.name == "nt":
        return False
    try:
        with _stage_directory(profile, request, file) as directory:
            directory.entry_stat("payload")
            return True
    except FileNotFoundError:
        return False


def _authority_check(profile, file, *, identity=None, actual_path=None):
    """Check the original target and the actual native object, using our scope."""
    group = _AUTHORITY_GROUP.get()
    if group is None:
        _fail("GC_AUTHORITY_SCOPE_REQUIRED")
    root = getattr(profile, roots._ROOT_ROLE[file["namespace"]])
    path = root / file["storage_key"]
    identity = (file["file_device"], file["file_inode"]) if identity is None else identity
    personal_authority_roots.assert_unprotected(group, profile=profile, path=path, identity=identity)
    if actual_path is not None and members._key(actual_path) != members._key(path):
        personal_authority_roots.assert_unprotected(group, profile=profile, path=actual_path, identity=identity)


def _native_entry_unchanged(directory, name, expected):
    """Final no-callback comparison after cancellation/authority callbacks."""
    current = directory.entry_stat(name)
    safe._safe_stat(current, directory=False)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
    if current.st_nlink != 1 or any(getattr(current, key) != getattr(expected, key) for key in fields):
        _fail("GC_FILE_IDENTITY_CHANGED")
    directory.verify()
    return current


def _restore_stage(con, profile, request, file, check):
    """Restore a held object without overwriting any newer original name."""
    _authority_check(profile, file)
    root = getattr(profile, roots._ROOT_ROLE[file["namespace"]])
    path = root / file["storage_key"]
    with _stage_directory(profile, request, file) as stage, safe._Directory(path.parent, base=root) as original:
        with stage.open_file("payload") as stream:
            info = _hash_frozen(stream, file, check, moved=True)
        _write_stage_record(stage, "restore.json", _restore_record(request, file))
        try:
            _authority_check(profile, file, identity=(str(info.st_dev), str(info.st_ino)),
                             actual_path=stage.path / "payload")
            _native_entry_unchanged(stage, "payload", info)
            original.verify()
            safe._rename_new(stage, "payload", original, path.name)
        except FileExistsError:
            return False
        os.fsync(original.fd)
        os.fsync(stage.fd)
    # The physical rename and registration refresh form a non-cancellable
    # recovery segment. Actual process death is covered by restore.json.
    recovery_token = _AUTHORITY_RECOVERY.set(True)
    try:
        _recover_restore(con, profile, request, file)
    finally:
        _AUTHORITY_RECOVERY.reset(recovery_token)
    check()
    return True


def _restore_record(request, file):
    return contract.canonical_bytes({"format_version": 1, "operation_id": request["operation_id"],
        "request_sha256": contract.digest(request), "artifact_id": file["artifact_id"],
        "identity": {key: file[key] for key in ("file_device", "file_inode", "file_mtime_ns", "byte_size", "sha256")}})


def _recover_restore(con, profile, request, file):
    """Recover an authorized original-name restore, never a new file inode."""
    if os.name == "nt":
        return None
    _authority_check(profile, file)
    try:
        with _stage_directory(profile, request, file) as stage:
            try:
                stage.entry_stat("payload")
                return None
            except FileNotFoundError:
                pass
            with stage.open_file("restore.json") as stream:
                if stream.read(4097) != _restore_record(request, file):
                    _fail("GC_STAGE_UNPROVEN")
    except FileNotFoundError:
        return None
    root = getattr(profile, roots._ROOT_ROLE[file["namespace"]])
    # Rename changes ctime. Refresh today's trusted registration using a real
    # outside-writer proof; the operation's frozen descriptor stays unchanged.
    try:
        proof = artifacts._inspect_for_registration(con, profile=profile, namespace=file["namespace"], root=root,
            storage_key=file["storage_key"], sha256=file["sha256"], byte_size=file["byte_size"])
    except safe.SyncError as exc:
        if exc.code == "STORAGE_ARTIFACT_MISSING":
            return None
        raise
    if any(proof.record[key] != file[key] for key in roots._ARTIFACT_FIELDS if key != "file_ctime_ns"):
        _fail("GC_FILE_IDENTITY_CHANGED")
    _authority_check(profile, file, identity=(proof.record["file_device"], proof.record["file_inode"]))
    with _transaction(con, profile, lambda: None):
        artifacts._register_verified(con, proof, profile=profile)
    return proof.record["file_ctime_ns"]


def _dispose_windows(fd):
    # The READ|DELETE handle denies external writes and renames. Disposition is
    # applied to that same verified object, never a second pathname lookup.
    import ctypes
    class Disposition(ctypes.Structure):
        _fields_ = [("delete", safe.wintypes.BOOLEAN)]
    value = Disposition(True)
    result = safe._nt.NtSetInformationFile(safe.msvcrt.get_osfhandle(fd), ctypes.byref(safe._IoStatus()),
        ctypes.byref(value), ctypes.sizeof(value), 13)
    if result < 0:
        raise ctypes.WinError(safe._nt.RtlNtStatusToDosError(result))


def _unlink(con, profile, file, check, *, request, restored_ctime=None):
    observed = {"deleted": False}
    try:
        return _unlink_body(con, profile, file, check, request=request, observed=observed, restored_ctime=restored_ctime)
    except BaseException as exc:
        if observed["deleted"]:
            raise _ObservedDelete(exc, file["byte_size"]) from None
        raise


def _unlink_body(con, profile, file, check, *, request, observed, restored_ctime=None):
    """Delete a verified DELETE handle or a verified private staged object."""
    if con.in_transaction:
        _fail("GC_TRANSACTION_ACTIVE")
    guard = lifecycle.require_guard(profile, exclusive=True)
    _authority_check(profile, file)
    inspected_file = {**file, "file_ctime_ns": restored_ctime} if restored_ctime is not None else file
    root = getattr(profile, roots._ROOT_ROLE[file["namespace"]])
    path = root / file["storage_key"]
    if os.name == "nt":
        with safe._Directory(path.parent, base=root) as directory:
            fd = safe._win_open(path, delete=True)
            with os.fdopen(fd, "rb") as stream:
                info = _hash_frozen(stream, inspected_file, check)
                guard.verify()
                directory.verify()
                _authority_check(profile, file, identity=(str(info.st_dev), str(info.st_ino)), actual_path=path)
                _dispose_windows(stream.fileno())
            observed["deleted"] = True
        return file["byte_size"]
    if _staged(profile, request, file):
        with _stage_directory(profile, request, file) as stage:
            with stage.open_file("payload") as stream:
                # A trusted rename changes ctime; original inode/mtime/size/SHA
                # remain frozen and are checked again on every resume.
                info = _hash_frozen(stream, file, check, moved=True)
            current = stage.entry_stat("payload")
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_nlink) != (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, 1):
                _fail("GC_FILE_IDENTITY_CHANGED")
            guard.verify()
            _authority_check(profile, file, identity=(str(current.st_dev), str(current.st_ino)),
                             actual_path=stage.path / "payload")
            _native_entry_unchanged(stage, "payload", current)
            stage.unlink("payload")
            observed["deleted"] = True
            os.fsync(stage.fd)
        return file["byte_size"]
    actual = artifacts._inspect(profile=profile, namespace=file["namespace"], root=root,
        storage_key=file["storage_key"], sha256=file["sha256"], byte_size=file["byte_size"], check_cancelled=check)
    if any(actual[key] != inspected_file[key] for key in roots._ARTIFACT_FIELDS):
        _fail("GC_FILE_IDENTITY_CHANGED")
    with safe._Directory(path.parent, base=root) as directory, _stage_directory(profile, request, file, create=True) as stage:
        with directory.open_file(path.name) as stream:
            info = os.fstat(stream.fileno())
            values = {"file_device": str(info.st_dev), "file_inode": str(info.st_ino),
                      "file_mtime_ns": str(info.st_mtime_ns), "file_ctime_ns": str(info.st_ctime_ns), "byte_size": info.st_size}
            if info.st_nlink != 1 or any(inspected_file[key] != value for key, value in values.items()):
                _fail("GC_FILE_IDENTITY_CHANGED")
        check()
        guard.verify()
        current = directory.entry_stat(path.name)
        safe._safe_stat(current, directory=False)
        if (current.st_nlink != 1 or (str(current.st_dev), str(current.st_ino), current.st_size, str(current.st_mtime_ns)) !=
                (file["file_device"], file["file_inode"], file["byte_size"], file["file_mtime_ns"])):
            _fail("GC_FILE_IDENTITY_CHANGED")
        directory.verify()
        _authority_check(profile, file, identity=(str(current.st_dev), str(current.st_ino)), actual_path=path)
        _native_entry_unchanged(directory, path.name, current)
        stage.verify()
        safe._rename_new(directory, path.name, stage, "payload")
        os.fsync(directory.fd)
        os.fsync(stage.fd)
        try:
            with stage.open_file("payload") as stream:
                info = _hash_frozen(stream, file, check, moved=True)
        except BaseException as exc:
            # Only a proven identity/content mismatch is a substituted object.
            # Cancellation or I/O keeps the original private payload for resume
            # rather than adding a second ctime-changing restore round trip.
            if isinstance(exc, GCError) and exc.code == "GC_FILE_IDENTITY_CHANGED":
                try:
                    moved = stage.entry_stat("payload")
                    _authority_check(profile, file, identity=(str(moved.st_dev), str(moved.st_ino)),
                                     actual_path=stage.path / "payload")
                    _native_entry_unchanged(stage, "payload", moved)
                    directory.verify()
                    safe._rename_new(stage, "payload", directory, path.name)
                    os.fsync(directory.fd)
                    os.fsync(stage.fd)
                except BaseException:
                    pass
            raise
        guard.verify()
        _authority_check(profile, file, identity=(str(info.st_dev), str(info.st_ino)),
                         actual_path=stage.path / "payload")
        _native_entry_unchanged(stage, "payload", info)
        stage.unlink("payload")
        observed["deleted"] = True
        os.fsync(stage.fd)
        directory.verify()
    return file["byte_size"]


def _classify(scan, file, *, staged=False, restored_ctime=None):
    if any(blocker.namespace == file["namespace"] and blocker.root_id == file["root_id"] for blocker in scan.base_inputs.blockers):
        return "blocked", "GC_ROOTS_BLOCKED"
    current = next((item for item in scan.files if item["artifact_id"] == file["artifact_id"]), None)
    if current is not None and current["status"] == "protected":
        return "blocked", "GC_PERMANENT_PROTECTION"
    if roots._referenced(file, scan.base_inputs):
        return "retained", "GC_CURRENT_REFERENCE"
    if current is None:
        return "blocked", "GC_ARTIFACT_OWNERSHIP_CHANGED"
    inspected_file = {**file, "file_ctime_ns": restored_ctime} if restored_ctime is not None else file
    if any(current[key] != inspected_file[key] for key in roots._ARTIFACT_FIELDS):
        return "blocked", "GC_FILE_IDENTITY_CHANGED"
    if current["status"] == "missing":
        return ("pending", None) if staged else ("missing", "GC_FILE_MISSING")
    if current["status"] not in ("candidate", "referenced"):
        return "blocked", "GC_FILE_UNPROVEN"
    return "pending", None


def _receipt(con, request):
    items = _items(con, request)
    listed = [{key: items[file["artifact_id"]][key] for key in
        ("artifact_id", "namespace", "storage_key", "state", "deleted_bytes", "reason_code")} for file in request["files"]]
    state = "completed" if all(item["state"] in _TERMINAL for item in listed) else "blocked"
    return {"format_version": 1, "operation_id": request["operation_id"], "request_sha256": contract.digest(request),
            "state": state, "selected_count": len(listed), "retired_count": len(request["retirements"]),
            "deleted_bytes": sum(item["deleted_bytes"] for item in listed), "items": listed}


def _execute_item(con, profile, expanded, request, policy, file, check):
    released = None
    def authority_poll():
        # Preserve the existing rename -> registration non-cancellable segment.
        # Physical checks and the collector's deadline remain in effect.
        if not _AUTHORITY_RECOVERY.get():
            check()
    try:
        with personal_authority_roots.capture_group_locked(expanded, check_cancelled=authority_poll) as group:
            token = _AUTHORITY_GROUP.set(group)
            try:
                try:
                    _authority_check(profile, file)
                    restored_ctime = _recover_restore(con, profile, request, file)
                    check()
                    # SQL, inventories and candidate captures still finish here.
                    # Only this separate authority-only group survives the scan.
                    current = roots.scan_locked(expanded, owner_profile=profile, policy=policy,
                                                check_cancelled=check, _authority_group=group)
                    staged = _staged(profile, request, file)
                    state, reason = _classify(current, file, staged=staged, restored_ctime=restored_ctime)
                    if staged and state == "retained":
                        if not _restore_stage(con, profile, request, file, check):
                            reason = "GC_CURRENT_REFERENCE_STAGED"
                except (ValueError, safe.SyncError, OSError) as exc:
                    state, reason = "blocked", _reason(exc, "GC_ROOTS_UNAVAILABLE")
                if state != "pending":
                    return state, reason, 0
                registry = artifacts.get(con, file["artifact_id"])
                inspected_file = {**file, "file_ctime_ns": restored_ctime} if restored_ctime is not None else file
                if registry is None or any(registry[key] != inspected_file[key] for key in roots._ARTIFACT_FIELDS):
                    return "blocked", "GC_ARTIFACT_OWNERSHIP_CHANGED", 0
                try:
                    released = _unlink(con, profile, file, check, request=request, restored_ctime=restored_ctime)
                except (GCError, personal_authority_roots.PersonalAuthorityRootError, safe.SyncError, OSError) as exc:
                    reason = _reason(exc, "GC_UNLINK_FAILED")
                    return ("missing" if reason == "STORAGE_ARTIFACT_MISSING" else "blocked"), reason, 0
                return "deleted", None, released
            finally:
                _AUTHORITY_GROUP.reset(token)
    except BaseException as exc:
        # A normal scope-close failure after a successful native deletion cannot
        # turn the already observed deletion into an unperformed blocked item.
        if released is not None:
            raise _ObservedDelete(exc, released) from None
        raise


def _execute(con, profile, expanded, request, policy, check):
    items = _items(con, request)
    for file in request["files"]:
        check()
        if items[file["artifact_id"]]["state"] in _TERMINAL:
            continue
        try:
            state, reason, released = _execute_item(con, profile, expanded, request, policy, file, check)
        except _ObservedDelete as exc:
            _set_item(con, profile, request, file, "deleted", "GC_DELETE_FINISH_UNCONFIRMED",
                      lambda: None, deleted_bytes=exc.released)
            if not isinstance(exc.original, (GCError, personal_authority_roots.PersonalAuthorityRootError,
                                             safe.SyncError, OSError)):
                raise exc.original
        except (ValueError, safe.SyncError, OSError) as exc:
            _set_item(con, profile, request, file, "blocked", _reason(exc, "GC_ROOTS_UNAVAILABLE"), check)
        else:
            _set_item(con, profile, request, file, state, reason,
                      (lambda: None) if state == "deleted" else check, deleted_bytes=released)
    receipt = _receipt(con, request)
    with _transaction(con, profile, check):
        con.execute("UPDATE storage_gc_operation SET state=?,result_json=?,updated_at=? WHERE operation_id=?",
                    (receipt["state"], _json(receipt), _stamp(), request["operation_id"]))
    return receipt


def _existing(con, profile, record, *, expected=None, policy=None):
    operation, request, original_policy = record
    _validate_owner(con, profile, request)
    if (expected is not None and request["expected_preview_sha256"] != expected
            or policy is not None and original_policy != policy):
        _fail("GC_OPERATION_CONFLICT")
    _items(con, request)
    if operation["state"] == "completed":
        row = con.execute("SELECT result_json FROM storage_gc_operation WHERE operation_id=?", (request["operation_id"],)).fetchone()
        receipt = contract.decode(row["result_json"], canonical=True)
        if receipt != _receipt(con, request) or receipt["state"] != "completed":
            _fail("GC_RECEIPT_CORRUPT")
        return request, original_policy, receipt
    return request, original_policy, None


def apply(profiles, *, expected_preview_sha256, operation_id, policy=None, check_cancelled=None):
    """CAS a new plan or replay the same UUID and exact original parameters."""
    submitted = lifecycle._profiles(profiles)
    operation_id = contract._uuid(operation_id)
    expected_preview_sha256 = contract._sha(expected_preview_sha256)
    policy = contract.default_policy() if policy is None else contract.validate_policy(policy)
    check = _check(check_cancelled)
    try:
        check()
        with members._group(submitted, True) as (expanded, _, _):
            check()
            profile = submitted[0]
            with _connection(profile) as con:
                previous = _read_operation(con, operation_id)
                if previous is not None:
                    request, frozen_policy, receipt = _existing(con, profile, previous,
                        expected=expected_preview_sha256, policy=policy)
                    if receipt is not None:
                        return receipt
                    return _execute(con, profile, expanded, request, frozen_policy, check)
                scan = roots.scan_locked(expanded, owner_profile=profile, policy=policy, check_cancelled=check)
                if scan.preview_sha256 != expected_preview_sha256:
                    _fail("GC_PREVIEW_CHANGED")
                request = _make_request(profile, scan, operation_id)
                _journal(con, profile, scan, request, check)
                return _execute(con, profile, expanded, request, policy, check)
    except _Cancelled as exc:
        raise exc.original from None


def resume(profiles, *, operation_id, check_cancelled=None):
    """Resume only an already committed operation's immutable selected set."""
    submitted = lifecycle._profiles(profiles)
    operation_id = contract._uuid(operation_id)
    check = _check(check_cancelled)
    try:
        check()
        with members._group(submitted, True) as (expanded, _, _):
            check()
            profile = submitted[0]
            with _connection(profile) as con:
                record = _read_operation(con, operation_id)
                if record is None:
                    _fail("GC_OPERATION_NOT_FOUND")
                request, policy, receipt = _existing(con, profile, record)
                return receipt if receipt is not None else _execute(con, profile, expanded, request, policy, check)
    except _Cancelled as exc:
        raise exc.original from None
