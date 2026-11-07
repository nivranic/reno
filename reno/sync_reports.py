"""Immutable report files with publication pointers owned by a DB transaction.

Writing an unreferenced content file is harmless; only sync_report makes it
visible. This avoids trying to atomically rename a manifest and commit SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from . import db, storage_admin, storage_artifacts, storage_lifecycle as lifecycle
from . import sync_events, sync_protocol as protocol, sync_queue as queue
from .sync_protocol import SyncError

MAX_REPORT_BYTES = 4 * 1024 * 1024


def _guard(*, profile=None, root=None, con=None):
    if profile is None:
        active = lifecycle.current_guard()
        profile = active.profile if active is not None else storage_admin.profile()
    lifecycle.require_guard(profile)
    if root is not None and os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.fspath(profile.reports)):
        raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
    if con is not None:
        storage_admin.from_connection(con, profile=profile)
    return profile


def event_fingerprint(con):
    digest = hashlib.sha256()
    columns = ",".join(sync_events.WIRE_FIELDS)
    for row in con.execute(f"SELECT {columns} FROM user_decision ORDER BY event_id"):
        digest.update(protocol._json_bytes(dict(row)))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_reports(reports, version):
    from .report import REPORTS
    if type(reports) is not list or len(reports) > len(REPORTS):
        raise SyncError("Invalid report set")
    result, names = [], set()
    for entry in reports:
        required = {"name", "content", "sha256", "policy_version", "version",
                    "generated_at", "review_events_sha256"}
        if type(entry) is not dict or set(entry) != required:
            raise SyncError("Invalid report fields")
        name = entry["name"]
        if not isinstance(name, str) or name not in REPORTS or name in names:
            raise SyncError("Invalid or repeated report name")
        names.add(name)
        # A durable pre-P04 outbox may still carry reports-v2. Preserve those
        # bytes and policy for receipt replay; metadata marks them stale under
        # the current policy. Export continues to select only current reports.
        if (entry["version"] != version or type(entry["policy_version"]) is not str
                or entry["policy_version"] not in {"reports-v2", "reports-v3"}
                or queue._stored_time(entry["generated_at"]) is None):
            raise SyncError("Report must describe this knowledge version and policy")
        if (not isinstance(entry["review_events_sha256"], str)
                or not queue._SHA256.fullmatch(entry["review_events_sha256"])):
            raise SyncError("Invalid report review provenance")
        if not isinstance(entry["content"], str):
            raise SyncError("Report content must be UTF-8 text")
        try:
            content = entry["content"].encode("utf-8")
        except UnicodeError:
            raise SyncError("Invalid report encoding") from None
        if len(content) > MAX_REPORT_BYTES:
            raise SyncError("Report is too large", "SYNC_REPORT_TOO_LARGE", 413)
        digest = hashlib.sha256(content).hexdigest()
        if entry["sha256"] != digest:
            raise SyncError("Report hash does not match", "SYNC_REPORT_HASH_MISMATCH", 400)
        result.append({**entry, "byte_size": len(content)})
    return result


def _path(root, name, digest, *, create=False):
    # Both components originate from the closed report contract, never paths.
    from .report import REPORTS
    if name not in REPORTS or not isinstance(digest, str) or not queue._SHA256.fullmatch(digest):
        raise SyncError("Invalid report file identity")
    root = Path(root).absolute()
    directory = root / ".sync-versions"
    path = directory / f"{name}-{digest}.md"
    for part in (*reversed(root.parents), root, directory, path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SyncError("Report storage contains a redirected path", "SYNC_STORAGE_UNSAFE", 409)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if not path.resolve().is_relative_to(root.resolve()):
        raise SyncError("Report storage escapes its root", "SYNC_STORAGE_UNSAFE", 409)
    return path


def prepare_files(root, entries, *, profile=None):
    """Write content only. No manifest changes and no database writes/commits."""
    from .sync_media import _Directory, _rename_new
    profile = _guard(profile=profile, root=root)
    root = Path(root).absolute()
    result = []
    for entry in entries:
        try:
            path = _path(root, entry["name"], entry["sha256"])
            content = entry["content"].encode("utf-8")
            if len(content) > MAX_REPORT_BYTES or len(content) != entry["byte_size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise SyncError("Report content does not match its descriptor", "SYNC_REPORT_HASH_MISMATCH", 400)
            with _Directory(path.parent, create=True, base=root) as directory:
                try:
                    with directory.open_file(path.name) as stream:
                        if stream.read(MAX_REPORT_BYTES + 1) != content:
                            raise SyncError("Stored report is damaged", "SYNC_REPORT_DAMAGED", 409)
                except FileNotFoundError:
                    temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
                    try:
                        with directory.open_file(temporary, write=True, create=True) as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        directory.verify()
                        try:
                            _rename_new(directory, temporary, directory, path.name)
                        except FileExistsError:
                            with directory.open_file(path.name) as stream:
                                if stream.read(MAX_REPORT_BYTES + 1) != content:
                                    raise SyncError("Stored report is damaged", "SYNC_REPORT_DAMAGED", 409)
                    finally:
                        try:
                            directory.unlink(temporary)
                        except FileNotFoundError:
                            pass
                directory.verify()
            result.append({**entry, "storage_key": path.relative_to(root).as_posix()})
        except OSError:
            raise SyncError("Report storage failed", "SYNC_STORAGE_FAILED", 503) from None
    return result


def install_pointers(con, entries, publication_id, local_version, *, profile=None, root=None):
    profile = _guard(profile=profile, root=root, con=con)
    root = profile.reports if root is None else root
    with storage_artifacts.operation(con, profile):
        for entry in entries:
            storage_artifacts.register(con, profile=profile, namespace="sync_report", root=root,
                storage_key=entry["storage_key"], sha256=entry["sha256"], byte_size=entry["byte_size"])
            values = {"name": entry["name"], "publication_id": publication_id,
                      "storage_key": entry["storage_key"], "sha256": entry["sha256"],
                      "byte_size": entry["byte_size"], "policy_version": entry["policy_version"],
                      "source_version_json": db.j(entry["version"]),
                      "local_version_json": db.j(local_version), "generated_at": entry["generated_at"],
                      "review_events_sha256": entry["review_events_sha256"]}
            columns = ",".join(values)
            updates = ",".join(f"{key}=excluded.{key}" for key in values if key != "name")
            con.execute(f"INSERT INTO sync_report ({columns}) VALUES ({','.join('?' for _ in values)}) "
                        f"ON CONFLICT(name) DO UPDATE SET {updates}", tuple(values.values()))


def manifest(con, name, *, profile=None):
    _guard(profile=profile, con=con)
    row = con.execute("SELECT * FROM sync_report WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    return {"report_type": name, **json.loads(row["local_version_json"]),
            "source_version": json.loads(row["source_version_json"]),
            "publication_id": row["publication_id"], "policy_version": row["policy_version"],
            "generated_at": row["generated_at"], "content_file": row["storage_key"],
            "content_sha256": row["sha256"], "byte_size": row["byte_size"],
            "review_events_sha256": row["review_events_sha256"],
            "source_reviews_match": row["review_events_sha256"] == event_fingerprint(con),
            "status": "latest", "error_class": None, "synced": True}


def read_content(root, value, *, profile=None):
    from .sync_media import _Directory
    _guard(profile=profile, root=root)
    root = Path(root).absolute()
    path = _path(root, value["report_type"], value["content_sha256"])
    if value["content_file"] != path.relative_to(root).as_posix():
        return None
    try:
        with _Directory(path.parent, base=root) as directory:
            with directory.open_file(path.name) as stream:
                size = os.fstat(stream.fileno()).st_size
                if size != value["byte_size"] or size > MAX_REPORT_BYTES:
                    return None
                data = stream.read(MAX_REPORT_BYTES + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != value["content_sha256"]:
                return None
            directory.verify()
        return data.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def export_reports(con, *, profile=None, recovery_projection=False):
    """Export only current, verifiable reports from the source read snapshot."""
    from . import report
    if type(recovery_projection) is not bool:
        raise SyncError('Recovery projection flag must be boolean', 'SYNC_RECOVERY_PROJECTION_INVALID', 400)
    profile = _guard(profile=profile, con=con)
    if not con.in_transaction:
        raise SyncError("Report export requires a consistent read transaction")
    version, result = db.knowledge_version(con), []
    events = event_fingerprint(con)
    for name in report.REPORTS:
        value = report._manifest(name, con=con, profile=profile)
        projected = value.get('knowledge_scope') == 'recovery_processed'
        if recovery_projection:
            if name == 'summary' or not projected:
                continue
        elif projected:
            continue
        basis = {key: value.get(key) for key in version}
        if basis != version or value.get("policy_version") != report.POLICY_VERSION:
            continue
        content = report.read_report(name, manifest=value, con=con, profile=profile)
        if content is None or value.get("source_reviews_match") is False:
            continue
        # Version equality permits older local manifests without this extra
        # digest: event merge already advances kb_revision even on no-op replay.
        digest = value.get("review_events_sha256", events)
        if digest != events:
            continue
        result.append({"name": name, "content": content,
                       "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                       "version": version, "policy_version": report.POLICY_VERSION,
                       "generated_at": value["generated_at"], "review_events_sha256": events})
    validate_reports(result, version)
    return result
