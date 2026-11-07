"""Trusted-writer registration of immutable system files, never deletion.

Callers supply the descriptor from their successful creation/publication, not
from scanning an unknown directory. A row proves verified ownership only;
current roots, pins and a fresh identity check still govern eventual GC.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import uuid
from types import MappingProxyType

from .sync_media import _Directory, _safe_stat
from .sync_protocol import SyncError, _json_bytes, validate_media_descriptor

NAMESPACES = ("sync_media", "local_report", "sync_report", "checkpoint")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HEX_UUID = re.compile(r"[0-9a-f]{32}\Z")
_LIMITS = {"sync_media": 512 * 1024 * 1024, "local_report": 4 * 1024 * 1024,
           "sync_report": 4 * 1024 * 1024, "checkpoint": 256 * 1024 * 1024}
IDENTITY_FIELDS = ("file_device", "file_inode", "file_mtime_ns", "file_ctime_ns")
_PROOF_SEAL = object()


@dataclass(frozen=True)
class _VerifiedArtifact:
    """Process-local proof from a completed SHA pass under this exact guard."""
    record: MappingProxyType
    root: Path
    guard: object
    seal: object
    profile_identity: tuple[str, str, str, str]
    media_hold: object = None


def _fail(message, code="STORAGE_ARTIFACT_INVALID", status=409):
    raise SyncError(message, code, status)


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def require_transaction(con, profile):
    """No implicit BEGIN or lock acquisition below an existing transaction."""
    from .storage_lifecycle import require_guard
    guard = require_guard(profile)
    if not con.in_transaction:
        _fail("Storage mutation requires the caller's transaction", "STORAGE_TRANSACTION_REQUIRED")
    database = next(row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main")
    if not database or _path_key(database) != _path_key(profile.database):
        _fail("Storage guard belongs to another database", "STORAGE_PROFILE_MISMATCH")
    return guard


@contextmanager
def operation(con, profile):
    """An atomic domain operation that preserves the caller's earlier writes."""
    guard = require_transaction(con, profile)
    name = "storage_" + uuid.uuid4().hex
    con.execute("SAVEPOINT " + name)
    try:
        # Acquire the SQLite writer before any read/modify/write decision.
        con.execute("UPDATE storage_artifact SET artifact_id=artifact_id WHERE 0")
        yield guard
        guard.verify()
        con.execute("RELEASE SAVEPOINT " + name)
    except BaseException as exc:
        con.execute("ROLLBACK TO SAVEPOINT " + name)
        con.execute("RELEASE SAVEPOINT " + name)
        if isinstance(exc, sqlite3.OperationalError) and getattr(exc, "sqlite_errorcode", 0) & 255 in (5, 6):
            _fail("Storage is busy; retry the transaction", "STORAGE_BUSY")
        raise


def validate_storage_key(namespace, storage_key):
    """Accept exactly the existing immutable-file naming contracts."""
    if namespace not in NAMESPACES or type(storage_key) is not str or not storage_key:
        _fail("Invalid storage namespace or key")
    parts = storage_key.split("/")
    if any(part in ("", ".", "..") for part in parts) or "\\" in storage_key or ":" in storage_key:
        _fail("Storage key must be an allowlisted relative POSIX key")
    if namespace == "sync_media":
        if len(parts) != 3 or parts[0] != "sync" or "." not in parts[2]:
            _fail("Invalid sync media key")
        digest, extension = parts[2].rsplit(".", 1)
        validate_media_descriptor({"video_id": parts[1], "sha256": digest, "extension": extension, "byte_size": 1})
    elif namespace == "checkpoint":
        try:
            if len(parts) != 1 or not storage_key.endswith(".sqlite") or str(uuid.UUID(storage_key[:-7])) != storage_key[:-7]:
                raise ValueError
        except (ValueError, AttributeError):
            _fail("Invalid checkpoint key")
    else:
        from .report import REPORTS
        prefix = ".versions" if namespace == "local_report" else ".sync-versions"
        if len(parts) != 2 or parts[0] != prefix or not parts[1].endswith(".md") or "-" not in parts[1]:
            _fail("Invalid immutable report key")
        name, suffix = parts[1][:-3].rsplit("-", 1)
        if name not in REPORTS or not (_HEX_UUID if namespace == "local_report" else _SHA).fullmatch(suffix):
            _fail("Invalid immutable report key")
    return storage_key


def _root(profile, namespace, root):
    from .storage_lifecycle import require_guard
    guard = require_guard(profile)
    expected = profile.media if namespace == "sync_media" else (profile.checkpoints if namespace == "checkpoint" else profile.reports)
    if _path_key(root) != _path_key(expected):
        _fail("Storage key uses another physical root", "STORAGE_PROFILE_MISMATCH")
    return guard, Path(os.path.abspath(os.fspath(root)))


def _root_record(device, inode):
    values = {"root_device": str(device), "root_inode": str(inode)}
    return {"root_id": hashlib.sha256(_json_bytes(values)).hexdigest(), **values}


def root_identity(root, *, profile):
    from .storage_lifecycle import require_guard
    guard = require_guard(profile)
    if _path_key(root) not in {_path_key(p) for p in (profile.media, profile.reports, profile.checkpoints)}:
        _fail("Unrecognized physical storage root", "STORAGE_PROFILE_MISMATCH")
    path = Path(os.path.abspath(os.fspath(root)))
    with _Directory(path, base=path) as directory:
        _, device, inode = directory.identities[-1]
        guard.verify()
        return _root_record(device, inode)


def _inspect(*, profile, namespace, root, storage_key, sha256, byte_size, check_cancelled=None):
    check = check_cancelled or (lambda: None)
    check()
    validate_storage_key(namespace, storage_key)
    if type(sha256) is not str or not _SHA.fullmatch(sha256):
        _fail("Artifact SHA must be a lowercase SHA-256")
    minimum = 0 if namespace in ("local_report", "sync_report") else 1
    if type(byte_size) is not int or not minimum <= byte_size <= _LIMITS[namespace]:
        _fail("Artifact size is outside the namespace limit")
    if namespace == "sync_media" and storage_key.rsplit("/", 1)[1].split(".", 1)[0] != sha256:
        _fail("Media key and content SHA differ")
    if namespace == "sync_report" and storage_key.rsplit("-", 1)[1][:-3] != sha256:
        _fail("Report key and content SHA differ")
    guard, root = _root(profile, namespace, root)
    path = root / storage_key
    try:
        with _Directory(path.parent, base=root) as directory:
            before_path = directory.entry_stat(path.name)
            _safe_stat(before_path, directory=False)
            if before_path.st_nlink != 1:
                _fail("Artifact has external hard links", "STORAGE_ARTIFACT_UNSAFE")
            with directory.open_file(path.name) as stream:
                before = os.fstat(stream.fileno())
                # Windows path stat and fstat have different ctime semantics.
                cross = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                stable = lambda s: (*cross(s), s.st_ctime_ns, s.st_nlink)
                if cross(before_path) != cross(before) or before.st_size != byte_size:
                    _fail("Artifact changed before opening", "STORAGE_ARTIFACT_CHANGED")
                digest, received = hashlib.sha256(), 0
                for chunk in iter(lambda: stream.read(128 * 1024), b""):
                    check()
                    received += len(chunk)
                    if received > byte_size:
                        _fail("Artifact grew during verification", "STORAGE_ARTIFACT_CHANGED")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
                check()
                if stable(before) != stable(after) or received != byte_size or digest.hexdigest() != sha256:
                    _fail("Artifact failed integrity verification", "STORAGE_ARTIFACT_CHANGED")
                if cross(directory.entry_stat(path.name)) != cross(after):
                    _fail("Artifact name changed during verification", "STORAGE_ARTIFACT_CHANGED")
                directory.verify()
                guard.verify()
                _, device, inode = next(item for item in directory.identities if item[0] == root)
                identity = _root_record(device, inode)
                result = {"namespace": namespace, **identity, "storage_key": storage_key,
                          "sha256": sha256, "byte_size": byte_size,
                          "file_device": str(after.st_dev), "file_inode": str(after.st_ino),
                          "file_mtime_ns": str(after.st_mtime_ns), "file_ctime_ns": str(after.st_ctime_ns)}
                result["artifact_id"] = "art_" + hashlib.sha256(_json_bytes({k: result[k] for k in ("namespace", "root_id", "storage_key")})).hexdigest()
                return result
    except FileNotFoundError:
        _fail("Registered artifact file is missing", "STORAGE_ARTIFACT_MISSING", 404)
    except OSError:
        _fail("Artifact could not be verified safely", "STORAGE_ARTIFACT_UNSAFE")


def get(con, artifact_id):
    cursor = con.execute("SELECT * FROM storage_artifact WHERE artifact_id=?", (artifact_id,))
    row = cursor.fetchone()
    return dict(zip((d[0] for d in cursor.description), row)) if row is not None else None


def register(con, *, profile, namespace, root, storage_key, sha256, byte_size, check_cancelled=None):
    """Verify a trusted writer's file, allowing same-content retransmission."""
    with operation(con, profile):
        record = _inspect(profile=profile, namespace=namespace, root=root, storage_key=storage_key,
                          sha256=sha256, byte_size=byte_size, check_cancelled=check_cancelled)
        return _register_record(con, record)


def _register_record(con, value):
    record = dict(value)
    previous = get(con, record["artifact_id"])
    if previous is not None:
        immutable = ("namespace", "root_id", "root_device", "root_inode", "storage_key", "sha256", "byte_size")
        if any(previous[key] != record[key] for key in immutable):
            _fail("Registered path belongs to different content", "STORAGE_ARTIFACT_CONFLICT")
        if all(previous[key] == record[key] for key in IDENTITY_FIELDS):
            return previous
        record["created_at"] = previous["created_at"]
    else:
        record["created_at"] = _stamp()
    record["updated_at"] = _stamp()
    keys = tuple(record)
    con.execute(f"INSERT INTO storage_artifact({','.join(keys)}) VALUES({','.join('?' for _ in keys)}) "
                "ON CONFLICT(artifact_id) DO UPDATE SET " + ",".join(k + "=excluded." + k for k in (*IDENTITY_FIELDS, "updated_at")),
                tuple(record.values()))
    return record


def verify(con, artifact_id, *, profile, root, check_cancelled=None):
    """Read-only check of the current file against its registered identity."""
    previous = get(con, artifact_id)
    if previous is None:
        _fail("Artifact is not registered", "STORAGE_ARTIFACT_NOT_FOUND", 404)
    current = _inspect(profile=profile, namespace=previous["namespace"], root=root, storage_key=previous["storage_key"],
                       sha256=previous["sha256"], byte_size=previous["byte_size"], check_cancelled=check_cancelled)
    if any(previous[key] != value for key, value in current.items()):
        _fail("Registered artifact identity changed", "STORAGE_ARTIFACT_CHANGED")
    return previous


def _proof_connection(con, profile):
    """Hashing requests never borrow or terminate the caller's SQL transaction."""
    from .storage_lifecycle import require_guard
    guard = require_guard(profile)
    if con.in_transaction:
        _fail("File verification requires an idle SQL connection", "STORAGE_TRANSACTION_ACTIVE")
    database = next(row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main")
    if not database or _path_key(database) != _path_key(profile.database):
        _fail("Verification belongs to another database", "STORAGE_PROFILE_MISMATCH")
    return guard


def _inspect_for_registration(con, *, profile, namespace, root, storage_key, sha256, byte_size, check_cancelled=None):
    """Internal trusted-writer proof; no database write and no input boolean."""
    guard = _proof_connection(con, profile)
    record = _inspect(profile=profile, namespace=namespace, root=root, storage_key=storage_key,
                      sha256=sha256, byte_size=byte_size, check_cancelled=check_cancelled)
    return _VerifiedArtifact(MappingProxyType(record), Path(root), guard, _PROOF_SEAL, profile.identity)


def _verify_registered(con, artifact_id, *, profile, root, check_cancelled=None):
    guard = _proof_connection(con, profile)
    record = verify(con, artifact_id, profile=profile, root=root, check_cancelled=check_cancelled)
    immutable = {key: value for key, value in record.items() if key not in ("created_at", "updated_at")}
    return _VerifiedArtifact(MappingProxyType(immutable), Path(root), guard, _PROOF_SEAL, profile.identity)


def _validate_verified(con, proof, *, profile, require_registered=True):
    """Recheck a real SHA proof with bounded metadata reads inside the writer.

    The shared guard remains continuous between the SHA pass and this check.
    Immutable-path cooperative writers may replace a same-content object; its
    new inode/timestamps invalidate this proof. GC cannot acquire exclusive.
    """
    guard = require_transaction(con, profile)
    if (not isinstance(proof, _VerifiedArtifact) or proof.seal is not _PROOF_SEAL
            or proof.guard is not guard or proof.profile_identity != profile.identity):
        _fail("File verification proof is not from this protected operation", "STORAGE_PROOF_INVALID")
    record = proof.record
    if proof.media_hold is not None:
        parts = record["storage_key"].split("/")
        proof.media_hold._identity({"video_id": parts[1], "sha256": record["sha256"],
            "byte_size": record["byte_size"], "extension": parts[2].rsplit(".", 1)[1]}, guard)
    validate_storage_key(record["namespace"], record["storage_key"])
    _, root = _root(profile, record["namespace"], proof.root)
    path = root / record["storage_key"]
    try:
        with _Directory(path.parent, base=root) as directory:
            before_path = directory.entry_stat(path.name)
            _safe_stat(before_path, directory=False)
            with directory.open_file(path.name) as stream:
                info = os.fstat(stream.fileno())
                cross = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                identity = {"file_device": str(info.st_dev), "file_inode": str(info.st_ino),
                            "file_mtime_ns": str(info.st_mtime_ns), "file_ctime_ns": str(info.st_ctime_ns)}
                if (cross(before_path) != cross(info) or info.st_size != record["byte_size"] or info.st_nlink != 1
                        or any(record[key] != value for key, value in identity.items())):
                    _fail("File identity changed after its SHA pass", "STORAGE_ARTIFACT_CHANGED")
                if cross(directory.entry_stat(path.name)) != cross(info):
                    _fail("File name changed after its SHA pass", "STORAGE_ARTIFACT_CHANGED")
                _, device, inode = next(item for item in directory.identities if item[0] == root)
                if any(record[key] != value for key, value in _root_record(device, inode).items()):
                    _fail("File root changed after its SHA pass", "STORAGE_ARTIFACT_CHANGED")
                directory.verify()
                guard.verify()
    except FileNotFoundError:
        _fail("Verified file is missing", "STORAGE_ARTIFACT_MISSING", 404)
    except OSError:
        _fail("Verified file cannot be checked safely", "STORAGE_ARTIFACT_UNSAFE")
    if require_registered:
        current = get(con, record["artifact_id"])
        if current is None or any(current[key] != value for key, value in record.items()):
            _fail("Artifact registration changed after verification", "STORAGE_ARTIFACT_CHANGED")
        return current
    return dict(record)


def _register_verified(con, proof, *, profile):
    with operation(con, profile):
        record = _validate_verified(con, proof, profile=profile, require_registered=False)
        return _register_record(con, record)


def _from_media_hold(con, hold, descriptor, *, profile):
    """Reuse the publisher's existing live SHA pass, never a caller boolean."""
    from .sync_media import _VerifiedMediaHold
    guard = require_transaction(con, profile)
    if not isinstance(hold, _VerifiedMediaHold) or _path_key(hold._root) != _path_key(profile.media):
        _fail("A live media verification hold is required", "STORAGE_PROOF_INVALID")
    media = validate_media_descriptor(descriptor)
    identity = hold._identity(media, guard)
    root = root_identity(profile.media, profile=profile)
    record = {"namespace": "sync_media", **root,
              "storage_key": f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}",
              "sha256": media["sha256"], "byte_size": media["byte_size"],
              "file_device": str(identity[0]), "file_inode": str(identity[1]),
              "file_mtime_ns": str(identity[3]), "file_ctime_ns": str(identity[4])}
    record["artifact_id"] = "art_" + hashlib.sha256(_json_bytes({k: record[k] for k in ("namespace", "root_id", "storage_key")})).hexdigest()
    proof = _VerifiedArtifact(MappingProxyType(record), Path(profile.media), guard, _PROOF_SEAL, profile.identity, hold)
    # The hold proves physical bytes; the target member must also own
    # an exact registration in its own DB before this becomes its proof.
    _validate_verified(con, proof, profile=profile)
    return proof
