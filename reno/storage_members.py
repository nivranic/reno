"""Monotone root membership, explicit completeness, no garbage collection.

Uses shared/exclusive lifecycle groups. The public preview never creates a file,
opens a DB before its complete lock group, or removes an unavailable member.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import uuid

from reno import storage_lifecycle as lifecycle, storage_source as source, sync_media as safe

REGISTRY = ".reno-storage-members-v1.json"
COMMIT_PREFIX = ".reno-storage-members-commit-"
SCHEMA = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 128
MAX_IDENTITY_COPY_BYTES = source.DEFAULT_COPY_MAX_BYTES
IDENTITY_READ_ATTEMPTS = source.MAX_CAPTURE_ATTEMPTS
FIELDS = ("database", "media", "reports", "checkpoints")
ROLES = FIELDS[1:]
NAMESPACES = {"media": ("sync_media",), "reports": ("local_report", "sync_report"), "checkpoints": ("checkpoint",)}
_SEAL = object()


class MembershipError(ValueError):
    def __init__(self, code, *, details=None):
        super().__init__(code)
        self.code, self.details = code, details


def _fail(code, **details):
    raise MembershipError(code, details=details or None)


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _uuid(value):
    if type(value) is not str:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    return value


def _profile(value):
    if isinstance(value, lifecycle.StorageProfile):
        return value
    if type(value) is not dict or set(value) != set(FIELDS) or any(type(v) is not str for v in value.values()):
        _fail("MEMBERSHIP_INVALID_PROFILE")
    return lifecycle.StorageProfile(*(Path(value[k]) for k in FIELDS))


def _profiles(values):
    if type(values) not in (list, tuple) or not values or len(values) > MAX_MEMBERS:
        _fail("MEMBERSHIP_INVALID_PROFILE")
    unique = {_profile(p).identity: _profile(p) for p in values}
    return tuple(unique[k] for k in sorted(unique))


def _profile_json(value):
    return {k: _key(getattr(value, k)) for k in FIELDS}


def _root_map(profiles):
    result = {}
    for profile in profiles:
        for role in ROLES:
            root = Path(getattr(profile, role))
            result.setdefault(_key(root), {"path": root, "roles": set()})["roles"].add(role)
    return result


def _root_identity(path):
    with safe._Directory(path, base=path) as directory:
        _, dev, ino = directory.identities[-1]
        directory.verify()
        return {"path": _key(path), "device": str(dev), "inode": str(ino)}


def _private(info):
    safe._safe_stat(info, directory=False)
    if info.st_nlink != 1 or (os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
        _fail("MEMBERSHIP_UNSAFE_SIDECAR")


def _read_bytes(root, name):
    with safe._Directory(root, base=root) as directory:
        try:
            before = directory.entry_stat(name)
        except FileNotFoundError:
            return None
        _private(before)
        with directory.open_file(name) as stream:
            opened = os.fstat(stream.fileno())
            _private(opened)
            if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                _fail("MEMBERSHIP_SIDECAR_CHANGED")
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > MAX_BYTES or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                _fail("MEMBERSHIP_INVALID_REGISTRY")
        again = directory.entry_stat(name)
        if (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _fail("MEMBERSHIP_SIDECAR_CHANGED")
        directory.verify()
        return raw


def _object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                _fail("MEMBERSHIP_INVALID_REGISTRY")
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if type(value) is not dict:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    return value


def _identity(value):
    if type(value) is not dict or set(value) != {"path", "device", "inode"}:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if any(type(value[k]) is not str for k in value) or not value["device"].isdigit() or not value["inode"].isdigit():
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if not Path(value["path"]).is_absolute() or _key(value["path"]) != value["path"]:
        _fail("MEMBERSHIP_INVALID_REGISTRY")


def _validate_record(record):
    if type(record) is not dict or set(record) != {"member_id", "profile", "library_id", "database_identity", "roots"}:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    profile = _profile(record["profile"])
    if record["profile"] != _profile_json(profile):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    _uuid(record["library_id"])
    _identity(record["database_identity"])
    if record["database_identity"]["path"] != _key(profile.database):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if type(record["roots"]) is not dict or set(record["roots"]) != set(ROLES):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    for role, identity in record["roots"].items():
        _identity(identity)
        if identity["path"] != _key(getattr(profile, role)):
            _fail("MEMBERSHIP_INVALID_REGISTRY")
    if record["member_id"] != "mem_" + _sha(_json({"profile": record["profile"], "library_id": record["library_id"]})):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    return profile


def _validate_registry(value, identity):
    if value.get("schema") != SCHEMA or type(value.get("schema")) is not int:
        _fail("MEMBERSHIP_SCHEMA_UNKNOWN")
    if set(value) != {"schema", "generation", "root", "members", "group_profiles", "completeness", "operation_id", "coordinator"}:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if type(value["generation"]) is not int or not 1 <= value["generation"] < 2**63:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    _identity(value["root"])
    _identity(value["coordinator"])
    _uuid(value["operation_id"])
    if value["root"] != identity:
        _fail("MEMBERSHIP_ROOT_CHANGED")
    members = value["members"]
    if type(members) is not list or not members or len(members) > MAX_MEMBERS:
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    seen = set()
    for member in members:
        _validate_record(member)
        if member["member_id"] in seen or identity not in member["roots"].values():
            _fail("MEMBERSHIP_INVALID_REGISTRY")
        seen.add(member["member_id"])
    group = _profiles(value["group_profiles"])
    identities = {p.identity for p in group}
    if any(_profile(member["profile"]).identity not in identities for member in members):
        _fail("MEMBERSHIP_INVALID_REGISTRY")
    if value["coordinator"]["path"] not in _root_map(group):
        _fail("MEMBERSHIP_COORDINATOR_OUTSIDE_GROUP")
    complete = value["completeness"]
    if (type(complete) is not dict or set(complete) != {"kind", "member_ids"}
            or complete["kind"] not in ("unknown", "explicit", "fresh", "inherited")
            or complete["member_ids"] != sorted(seen)):
        _fail("MEMBERSHIP_INVALID_REGISTRY")


def _registry(root):
    identity = _root_identity(root)
    raw = _read_bytes(root, REGISTRY)
    if raw is None:
        return {"identity": identity, "raw_sha": None, "value": None, "state": "membership_unknown"}
    value = _object(raw)
    _validate_registry(value, identity)
    # Discovery never follows the coordinator path. It only expands declared
    # profiles; the receipt is read after that complete group is protected.
    return {"identity": identity, "raw_sha": _sha(raw), "value": value, "state": "unverified"}


def _validate_commit(receipt, operation_id):
    if (set(receipt) != {"schema", "operation_id", "registries"} or type(receipt["schema"]) is not int
            or receipt["schema"] != SCHEMA or receipt["operation_id"] != operation_id
            or type(receipt["registries"]) is not dict or not receipt["registries"]):
        _fail("MEMBERSHIP_INVALID_COMMIT")
    for path, digest in receipt["registries"].items():
        if (type(path) is not str or not Path(path).is_absolute() or _key(path) != path
                or type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            _fail("MEMBERSHIP_INVALID_COMMIT")


def _complete_roots(roots, guard):
    protected = _root_map(guard.profiles)
    if set(roots) - set(protected):
        _fail("MEMBERSHIP_GROUP_INCOMPLETE")
    receipts, outcomes = {}, {}
    for key, observed in roots.items():
        value = observed["value"]
        if value is None:
            continue
        coordinator = value["coordinator"]
        location = coordinator["path"]
        if location not in roots or roots[location]["identity"] != coordinator:
            _fail("MEMBERSHIP_COORDINATOR_OUTSIDE_GROUP")
        token = (location, value["operation_id"])
        if token in outcomes:
            committed, covered = outcomes[token]
            if covered is not None and key not in covered:
                _fail("MEMBERSHIP_INVALID_COMMIT")
            observed["state"] = "enrollment_incomplete" if not committed else (
                "membership_unknown" if value["completeness"]["kind"] == "unknown" else "complete")
            continue
        if token not in receipts:
            raw = _read_bytes(Path(location), COMMIT_PREFIX + value["operation_id"] + ".json")
            receipts[token] = _object(raw) if raw is not None else None
        receipt = receipts[token]
        committed = False
        if receipt is not None:
            _validate_commit(receipt, value["operation_id"])
            if set(receipt["registries"]) - set(roots):
                _fail("MEMBERSHIP_COMMIT_OUTSIDE_GROUP")
            if key not in receipt["registries"]:
                _fail("MEMBERSHIP_INVALID_COMMIT")
            committed = all(roots[path]["raw_sha"] == digest for path, digest in receipt["registries"].items())
            # Admission witnesses are published only after the coordinator.
            # GC requires the whole update, including all local witnesses.
            for path in receipt["registries"]:
                witness_raw = _read_bytes(Path(path), COMMIT_PREFIX + value["operation_id"] + ".json")
                if witness_raw is None or _object(witness_raw) != receipt:
                    committed = False
        outcomes[token] = committed, (set(receipt["registries"]) if receipt is not None else None)
        observed["state"] = "enrollment_incomplete" if not committed else (
            "membership_unknown" if value["completeness"]["kind"] == "unknown" else "complete")
    guard.verify()


def _discover(profiles):
    found = {p.identity: p for p in _profiles(profiles)}
    roots, errors = {}, {}
    while True:
        pending = {k: r for k, r in _root_map(found.values()).items() if k not in roots and k not in errors}
        if not pending:
            return tuple(found[k] for k in sorted(found)), roots, errors
        for key, entry in pending.items():
            try:
                roots[key] = _registry(entry["path"])
                value = roots[key]["value"]
                for entry in value["group_profiles"] if value else ():
                    p = _profile(entry)
                    if p.identity not in found and len(found) >= MAX_MEMBERS:
                        _fail("MEMBERSHIP_TOO_MANY_MEMBERS")
                    found[p.identity] = p
            except (MembershipError, lifecycle.StorageLifecycleError, safe.SyncError, OSError) as exc:
                if getattr(exc, "code", None) == "MEMBERSHIP_TOO_MANY_MEMBERS":
                    raise
                errors[key] = getattr(exc, "code", "MEMBERSHIP_ROOT_UNAVAILABLE")


@contextmanager
def _group(profiles, exclusive):
    # Discovery is metadata-only and never trusted until reread under all locks.
    if lifecycle.current_guard() is not None:
        _fail("MEMBERSHIP_GROUP_MUST_BE_ACQUIRED_FIRST")
    for _ in range(4):
        expanded, _, errors = _discover(profiles)
        if errors:
            _fail("MEMBERSHIP_DISCOVERY_BLOCKED", roots=errors)
        method = lifecycle.exclusive_many if exclusive else lifecycle.shared_many
        with method(expanded) as guard:
            again, roots, errors = _discover(expanded)
            if any(not guard.allows(p) for p in again):
                continue
            if errors:
                _fail("MEMBERSHIP_DISCOVERY_BLOCKED", roots=errors)
            _complete_roots(roots, guard)
            yield again, roots, guard
            return
    _fail("MEMBERSHIP_CHANGED_RETRY")


def _member(profile):
    lifecycle.require_guard(profile)
    # Root identity is frozen before a read-only DB is opened. No schema writes.
    roots = {role: _root_identity(getattr(profile, role)) for role in ROLES}
    with safe._Directory(profile.database.parent, base=profile.database.parent) as directory:
        before = directory.entry_stat(profile.database.name)
        safe._safe_stat(before, directory=False)
        if before.st_nlink != 1:
            _fail("MEMBERSHIP_DATABASE_UNSAFE")
        library_id = _read_identity_source(profile.database, directory)
        after = directory.entry_stat(profile.database.name)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            _fail("MEMBERSHIP_DATABASE_CHANGED")
        directory.verify()
    base = {"profile": _profile_json(profile), "library_id": library_id}
    return {"member_id": "mem_" + _sha(_json(base)), **base,
            "database_identity": {"path": _key(profile.database), "device": str(after.st_dev), "inode": str(after.st_ino)}, "roots": roots}


def _file_signature(info):
    try:
        return source.file_signature(info)
    except source.SourceReadError as exc:
        _source_failure(exc)


def _source_failure(exc):
    _fail("MEMBERSHIP_SOURCE_CHANGED" if exc.code == "SOURCE_CHANGED" else "MEMBERSHIP_DATABASE_UNSAFE")


def _source_state(directory, name):
    try:
        return source.source_state(directory, name)
    except source.SourceReadError as exc:
        _source_failure(exc)


@contextmanager
def _source_reader(directory, name):
    """Compatibility boundary for domain errors and existing reader spies."""
    try:
        with source.source_reader(directory, name) as stream:
            yield stream
    except source.SourceReadError as exc:
        _source_failure(exc)


def _copy_identity_file(directory, name, destination, expected):
    try:
        return source.copy_file(directory, name, destination, expected, reader=_source_reader)
    except source.SourceReadError as exc:
        _source_failure(exc)


def _read_identity_source(database, directory):
    """Never ask SQLite to open a mutable source DB or touch its WAL/SHM.

    An absent/empty WAL permits immutable reads, fenced by source metadata on
    both sides. Nonempty WAL is replayed only by SQLite against a bounded private
    copy; this code never parses WAL or ignores its committed transactions.
    """
    for _ in range(IDENTITY_READ_ATTEMPTS):
        try:
            before = _source_state(directory, database.name)
            if before["-journal"] is not None and before["-journal"][2]:
                _fail("MEMBERSHIP_DATABASE_BUSY")
            wal = before["-wal"]
            if wal is None or wal[2] == 0:
                result = _query_identity(database, immutable=True)
            else:
                if before[""][2] + wal[2] > MAX_IDENTITY_COPY_BYTES:
                    _fail("MEMBERSHIP_SNAPSHOT_TOO_LARGE")
                # tempfile owns and cleans only this newly created private tree.
                with tempfile.TemporaryDirectory(prefix="reno-members-identity-") as folder:
                    temporary = Path(folder)
                    if temporary.resolve().parent != Path(tempfile.gettempdir()).resolve():
                        _fail("MEMBERSHIP_TEMP_UNSAFE")
                    copied = temporary / "identity.db"
                    _copy_identity_file(directory, database.name, copied, before[""])
                    _copy_identity_file(directory, database.name + "-wal", copied.with_name("identity.db-wal"), wal)
                    if _source_state(directory, database.name) != before:
                        _fail("MEMBERSHIP_SOURCE_CHANGED")
                    result = _query_identity(copied, immutable=False)
            if _source_state(directory, database.name) != before:
                _fail("MEMBERSHIP_SOURCE_CHANGED")
            return result
        except MembershipError as exc:
            if exc.code != "MEMBERSHIP_SOURCE_CHANGED":
                raise
    _fail("MEMBERSHIP_SOURCE_CHANGED")


def _query_identity(database, *, immutable):
    uri = database.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    con = sqlite3.connect(uri, uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        steps = 0
        def budget():
            nonlocal steps
            steps += 1
            return int(steps > 50)  # At most about 50k SQLite VM steps.
        def authorize(action, first, second, schema, _source):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and schema == "main" and first in ("sqlite_master", "meta"):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_progress_handler(budget, 1000)
        con.set_authorizer(authorize)
        con.execute("BEGIN")
        schema = con.execute("SELECT type,sql FROM sqlite_master WHERE name='meta' LIMIT 2").fetchmany(2)
        if (len(schema) != 1 or schema[0][0] != "table" or type(schema[0][1]) is not str
                or "CREATE VIRTUAL TABLE" in " ".join(schema[0][1].upper().split())):
            _fail("MEMBERSHIP_DATABASE_META_UNSAFE")
        rows = con.execute("SELECT value FROM meta WHERE key='library_id' LIMIT 2").fetchmany(2)
        if len(rows) != 1:
            _fail("MEMBERSHIP_DATABASE_IDENTITY_MISSING")
        return _uuid(rows[0][0])
    finally:
        con.close()


def _declarations(values):
    if type(values) not in (list, tuple):
        _fail("MEMBERSHIP_INVALID_DECLARATION")
    result = {}
    for value in values:
        if type(value) is not dict or set(value) != {"root", "databases"} or type(value["databases"]) is not list:
            _fail("MEMBERSHIP_INVALID_DECLARATION")
        if any(type(path) is not str or not Path(path).is_absolute() or ".." in Path(path).parts
               for path in [value["root"], *value["databases"]]):
            _fail("MEMBERSHIP_INVALID_DECLARATION")
        root = _key(value["root"])
        databases = sorted({_key(p) for p in value["databases"]})
        if root in result or not databases or len(databases) != len(value["databases"]):
            _fail("MEMBERSHIP_INVALID_DECLARATION")
        result[root] = databases
    return result


def _plan(profiles, roots, declarations, fresh=()):
    declarations = _declarations(declarations)
    if set(declarations) - set(roots):
        _fail("MEMBERSHIP_DECLARATION_ROOT_UNKNOWN")
    saved = {}
    for observed in roots.values():
        for record in observed["value"]["members"] if observed["value"] else ():
            identity = _profile(record["profile"]).identity
            if identity in saved and saved[identity] != record:
                _fail("MEMBERSHIP_MEMBER_REGISTRY_CONFLICT")
            saved[identity] = record
    records, unavailable = [], {}
    for profile in profiles:
        try:
            record = _member(profile)
            if profile.identity in saved and saved[profile.identity] != record:
                _fail("MEMBERSHIP_MEMBER_CHANGED")
        except (MembershipError, lifecycle.StorageLifecycleError, safe.SyncError, OSError, sqlite3.Error) as exc:
            unavailable[profile.identity] = {"database": _key(profile.database),
                "code": getattr(exc, "code", "MEMBERSHIP_MEMBER_UNAVAILABLE")}
            record = saved.get(profile.identity)
        if record is not None:
            records.append(record)
    candidates, output = {}, []
    roles = _root_map(profiles)
    fresh = {receipt.root["path"]: receipt for receipt in fresh}
    for key in sorted(roots):
        observed = roots[key]
        previous = observed["value"]
        existing = previous["members"] if previous else []
        members = [r for r in records if observed["identity"] in r["roots"].values()]
        current = {r["member_id"]: r for r in members}
        for old in existing:
            if current.get(old["member_id"]) != old:
                _fail("MEMBERSHIP_MEMBER_CHANGED", root=key, database=old["profile"]["database"])
        members.sort(key=lambda r: r["member_id"])
        ids = [r["member_id"] for r in members]
        missing = [issue for identity, issue in unavailable.items() if key in identity[1:]]
        kind = "unknown"
        if observed["state"] == "complete":
            kind = "inherited"
        if key in declarations:
            if declarations[key] != sorted({r["profile"]["database"] for r in members}):
                _fail("MEMBERSHIP_DECLARATION_OMITS_MEMBER", root=key)
            kind = "explicit"
        if key in fresh:
            fresh[key].check(observed["identity"])
            if previous is not None or len(members) != 1:
                _fail("MEMBERSHIP_FRESH_ROOT_CONFLICT", root=key)
            kind = "fresh"
        candidates[key] = {"schema": SCHEMA, "generation": (previous["generation"] if previous else 0) + 1,
                           "root": observed["identity"], "members": members,
                           "group_profiles": [_profile_json(p) for p in profiles],
                           "completeness": {"kind": kind, "member_ids": ids}}
        namespaces = sorted({ns for role in roles[key]["roles"] for ns in NAMESPACES[role]})
        output.append({"root": observed["identity"], "namespaces": namespaces, "generation": previous["generation"] if previous else 0,
            "state": observed["state"], "proposed_state": "membership_unknown" if kind == "unknown" else "complete",
            "existing_members": existing, "proposed_members": members,
            "blockers": ([] if observed["state"] == "complete" else [observed["state"]]) + (["member_unavailable"] if missing else []),
            "unavailable_members": missing})
    binding = {"roots": {key: {"raw_sha": roots[key]["raw_sha"], "state": roots[key]["state"]} for key in sorted(roots)},
               "candidates": candidates, "declarations": declarations, "unavailable_members": list(unavailable.values())}
    return {"schema": SCHEMA, "digest": _sha(_json(binding)), "roots": output,
            "members": records, "profiles": [_profile_json(p) for p in profiles],
            "blocked": bool(unavailable), "unavailable_members": list(unavailable.values())}, candidates


def membership_preview(profiles, *, declarations=()):
    try:
        with _group(profiles, False) as (expanded, roots, _):
            return _plan(expanded, roots, declarations)[0]
    except (MembershipError, lifecycle.StorageLifecycleError, safe.SyncError, OSError, sqlite3.Error) as exc:
        # No database body, secret or URL is included. Paths locate affected roots.
        try:
            expanded, roots, errors = _discover(profiles)
        except MembershipError as discovery_error:
            return {"schema": SCHEMA, "digest": None, "blocked": True, "code": discovery_error.code,
                    "roots": [], "profiles": [_profile_json(p) for p in _profiles(profiles)]}
        known = []
        for key, observed in roots.items():
            known.append({"root": observed["identity"], "state": observed["state"],
                          "existing_members": observed["value"]["members"] if observed["value"] else []})
        return {"schema": SCHEMA, "digest": None, "blocked": True, "code": getattr(exc, "code", "MEMBERSHIP_UNAVAILABLE"),
                "roots": known, "profiles": [_profile_json(p) for p in expanded], "unavailable_roots": errors}


def snapshot_locked(profiles):
    supplied = _profiles(profiles)
    for profile in supplied:
        lifecycle.require_guard(profile)
    expanded, roots, errors = _discover(supplied)
    guard = lifecycle.current_guard()
    if errors:
        _fail("MEMBERSHIP_DISCOVERY_BLOCKED", roots=errors)
    if any(not guard.allows(p) for p in expanded):
        _fail("MEMBERSHIP_GROUP_INCOMPLETE")
    _complete_roots(roots, guard)
    return _plan(expanded, roots, ())[0]


def _write(root, name, payload, *, replace):
    temporary = ".reno-storage-members-write-" + uuid.uuid4().hex + ".tmp"
    owned = None
    with safe._Directory(root, base=root) as directory:
        try:
            with directory.open_file(temporary, write=True, create=True) as stream:
                info = os.fstat(stream.fileno())
                owned = info.st_dev, info.st_ino
                _private(info)
                if stream.write(payload) != len(payload):
                    _fail("MEMBERSHIP_WRITE_FAILED")
                stream.flush()
                os.fsync(stream.fileno())
            directory.verify()
            before = directory.entry_stat(temporary)
            if (before.st_dev, before.st_ino) != owned:
                _fail("MEMBERSHIP_SIDECAR_CHANGED")
            if replace:
                if os.name == "nt":
                    os.replace(safe._native_path(root / temporary), safe._native_path(root / name))
                else:
                    os.replace(temporary, name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
            else:
                safe._rename_new(directory, temporary, directory, name)
            if os.name != "nt":
                os.fsync(directory.fd)
        finally:
            if owned is not None:
                try:
                    info = directory.entry_stat(temporary)
                    if (info.st_dev, info.st_ino) == owned and info.st_nlink == 1:
                        directory.unlink(temporary)
                except FileNotFoundError:
                    pass


def _apply(candidates, operation_id):
    _uuid(operation_id)
    coordinator = candidates[min(candidates)]["root"]
    root = Path(coordinator["path"])
    receipt_name = COMMIT_PREFIX + operation_id + ".json"
    if _read_bytes(root, receipt_name) is not None:
        _fail("MEMBERSHIP_OPERATION_ALREADY_USED")
    encoded = {key: _json({**value, "operation_id": operation_id, "coordinator": coordinator}) for key, value in candidates.items()}
    for key in sorted(encoded):
        _write(Path(key), REGISTRY, encoded[key], replace=True)
    receipt = {"schema": SCHEMA, "operation_id": operation_id, "registries": {key: _sha(raw) for key, raw in encoded.items()}}
    receipt_bytes = _json(receipt)
    _write(root, receipt_name, receipt_bytes, replace=False)
    for key in sorted(encoded):
        if key != coordinator["path"]:
            _write(Path(key), receipt_name, receipt_bytes, replace=False)
    return {"operation_id": operation_id, "changed_roots": sorted(encoded)}


def enroll(profiles, *, expected_digest, declarations=(), operation_id):
    if type(expected_digest) is not str or len(expected_digest) != 64:
        _fail("MEMBERSHIP_DIGEST_REQUIRED")
    with _group(profiles, True) as (expanded, roots, guard):
        preview, candidates = _plan(expanded, roots, declarations)
        if preview["blocked"]:
            _fail("MEMBERSHIP_MEMBER_UNAVAILABLE")
        if preview["digest"] != expected_digest:
            _fail("MEMBERSHIP_PREVIEW_STALE")
        result = _apply(candidates, operation_id)
        guard.verify()
        return {**result, "preview_digest": expected_digest}


def require_enrolled(profile):
    """Normal admission needs only this profile, unlike GC's full snapshot.

    Local immutable witnesses are written only after all registry files and the
    coordinator receipt. Validate all three of our roots against those receipts,
    without following another member's paths or requiring another DB online.
    """
    guard = lifecycle.require_guard(profile)
    roots = {key: _registry(entry["path"]) for key, entry in _root_map((profile,)).items()}
    record = _member(profile)
    for key, observed in roots.items():
        value = observed["value"]
        if value is None or record not in value["members"]:
            _fail("MEMBERSHIP_NOT_ENROLLED", root=key)
        raw = _read_bytes(Path(key), COMMIT_PREFIX + value["operation_id"] + ".json")
        if raw is None:
            _fail("MEMBERSHIP_NOT_ENROLLED", root=key)
        receipt = _object(raw)
        _validate_commit(receipt, value["operation_id"])
        if any(receipt["registries"].get(path) != own["raw_sha"] for path, own in roots.items()):
            _fail("MEMBERSHIP_NOT_ENROLLED", root=key)
    guard.verify()
    return record


@dataclass(frozen=True)
class _CreatedRoot:
    root: dict
    seal: object

    def check(self, identity):
        if self.seal is not _SEAL or self.root != identity:
            _fail("MEMBERSHIP_CREATION_RECEIPT_INVALID")
        path = Path(identity["path"])
        with safe._Directory(path, base=path) as directory:
            if set(directory.names()) - {lifecycle.LOCK_NAME}:
                _fail("MEMBERSHIP_FRESH_ROOT_NOT_EMPTY", root=identity["path"])


def initialize_member(profile):
    """Explicit setup: only actual new directories carry freshness evidence.

    No DB read until group EX. A crash loses the freshness receipt; a subsequent
    operator can review and declare all members rather than guessing freshness.
    """
    profile = _profile(profile)
    if lifecycle.current_guard() is not None:
        _fail("MEMBERSHIP_GROUP_MUST_BE_ACQUIRED_FIRST")
    # Setup is not a bypass for CAS enrollment or an interrupted update. Check
    # all existing metadata before creating even one other missing directory.
    existing = False
    for entry in _root_map((profile,)).values():
        try:
            if _read_bytes(entry["path"], REGISTRY) is not None:
                existing = True
        except FileNotFoundError:
            pass
    if existing:
        try:
            # Normal startup depends only on this member's live roots and local
            # commit witnesses. GC, unlike admission, checks the whole group.
            with lifecycle.shared(profile):
                record = require_enrolled(profile)
                return {"unchanged": True, "changed_roots": [], "member_id": record["member_id"]}
        except (MembershipError, lifecycle.StorageLifecycleError, safe.SyncError, OSError, sqlite3.Error) as exc:
            _fail("MEMBERSHIP_ENROLLMENT_REQUIRED", cause=getattr(exc, "code", "MEMBERSHIP_UNAVAILABLE"))
    fresh = []
    # Parents must already exist; avoid inventing provenance for recursive mkdir.
    for entry in _root_map((profile,)).values():
        path = entry["path"]
        with safe._Directory(path.parent, base=path.parent) as parent:
            try:
                if os.name == "nt":
                    os.mkdir(safe._native_path(path))
                else:
                    os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
            except FileExistsError:
                pass
            else:
                fresh.append(_CreatedRoot(_root_identity(path), _SEAL))
            parent.verify()
    lifecycle.initialize(profile)
    with _group((profile,), True) as (expanded, roots, guard):
        if any(observed["value"] is not None for observed in roots.values()):
            # Another initializer may have won after our initial absence check.
            # Only its fully committed exact member is an idempotent success;
            # partial metadata, a new member, or changed identity still needs CAS.
            try:
                record = require_enrolled(profile)
                return {"unchanged": True, "changed_roots": [], "member_id": record["member_id"]}
            except (MembershipError, lifecycle.StorageLifecycleError, safe.SyncError, OSError, sqlite3.Error) as exc:
                _fail("MEMBERSHIP_ENROLLMENT_REQUIRED", cause=getattr(exc, "code", "MEMBERSHIP_UNAVAILABLE"))
        preview, candidates = _plan(expanded, roots, (), fresh)
        if preview["blocked"]:
            _fail("MEMBERSHIP_MEMBER_UNAVAILABLE")
        result = _apply(candidates, str(uuid.uuid4()))
        guard.verify()
        return {**result, "unchanged": False, "roots": preview["roots"]}
