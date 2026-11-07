"""Explicit private Home17 preservation producer.

This module preserves an actual owned node, its committed SQL image and the
complete witnessed files. It neither restores live files nor grants source
admission. Home restore must independently bind the original operation, source
and permanent SQL receipt to this bundle. Released backup descriptors and wire
contracts are untouched.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import threading
import time
import uuid

from . import source_preservation as base, source_preservation_certificate17 as certificate
from . import source_preservation_witness as witness, storage_backups as backups
from . import storage_lifecycle as lifecycle, storage_source as source, sync_media as safe
from . import sync_source_authority as authority, sync_source_continuity as continuity, sync_state
from . import home_recovery_source as home_sources, home_preservation_contract17 as home_contract
from . import home_authority_pending_contract17 as pending_contract
from . import storage_legacy_pending_state17 as pending_roots
from . import storage_legacy_pending_dependencies17 as dependency_roots
from . import sync_source_legacy_pending_state17 as pending_state
from .sync_recovery import _Pins

DIRECTORY = ".reno-source-preservation-bundles-v1"
OPERATIONS = ".reno-source-preservation-operations-v1"
MAX_FILES = 25_000
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = max(sync_state.MAX_STATE_BYTES, sync_state.MAX_OUTBOX_BYTES)
MAX_TOTAL_BYTES = 128 * 1024 * 1024 + sync_state.MAX_STATE_BYTES + base.MAX_OUTBOX_TOTAL_BYTES
MAX_PUBLICATION_PAYLOAD_TOTAL_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 120
PENDING_ROLES = frozenset({"legacy_pending_state", "legacy_pending_applied"})
DEPENDENCY_ROLES = dependency_roots.ROLES
_FILE_FIELDS = frozenset({"root_kind", "relative_key", "role", "sha256", "byte_size"})
_RESERVATION_FIELDS = frozenset({"format_version", "action", "operation_id", "bundle_id", "backup_id",
    "root_job_id", "owner_profile_sha256", "library_id", "node_id", "knowledge_version",
    "certificate_sha256", "certificate_bytes", "sql_image_sha256", "files", "file_set_sha256",
    "source_identities", "created_at"})
_MANIFEST_FIELDS = frozenset({"format_version", "bundle_id", "operation_id", "owner_profile_sha256",
    "library_id", "node_id", "knowledge_version", "database_backup", "certificate",
    "permanent_witnesses", "files", "file_set_sha256", "manifest_sha256", "created_at"})
_RECEIPT_FIELDS = frozenset({"format_version", "action", "operation_id", "bundle_id", "reservation_sha256",
    "manifest_sha256", "certificate_sha256", "files", "created_at"})
_CAPTURE = ContextVar("reno_live_preservation_bundle", default=())
_CAPTURE_SEAL = object()


class PreservationBundleError(ValueError):
    def __init__(self, code):
        self.code = "SOURCE_BUNDLE_" + code
        super().__init__(self.code)


def _fail(code):
    raise PreservationBundleError(code) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return backups._json(value)


def _ids(operation_id):
    backups._uuid(operation_id)
    original = uuid.UUID(operation_id)
    return (str(uuid.uuid5(original, "reno.home.preservation.bundle.v1")),
            str(uuid.uuid5(original, "reno.home.preservation.backup.v1")))


def _check(store, started):
    if store.check_cancelled is not None:
        store.check_cancelled()
    if time.monotonic() - started > MAX_SECONDS:
        _fail("LIMIT")
    lifecycle.require_guard(store.profile, exclusive=True).verify()


def _profile(profile, store, operation_id, root_job_id):
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or profile.identity != store.profile.identity
            or lifecycle._key(store.data_dir) != lifecycle._key(profile.database.parent)
            or root_job_id is not None and (type(root_job_id) is not int or root_job_id < 1)):
        _fail("OWNER_REQUIRED")
    _ids(operation_id)
    # A caller's SH or a pre-existing State reference window cannot be upgraded
    # to this explicit EX maintenance operation.
    if store._reference_depth or witness._WINDOW.get() is not None:
        _fail("OWNER_REQUIRED")
    return profile


@contextmanager
def _owned(profile, store):
    with ExitStack() as stack:
        # Preserve an already held real OS owner, but never acquire lifecycle
        # before this Store's original thread owner.
        if store._directory is None:
            stack.enter_context(store.exclusive())
        stack.enter_context(store._guard)
        stack.enter_context(lifecycle.exclusive(profile, timeout=1, check_cancelled=store.check_cancelled))
        stack.enter_context(store.reference_operation())
        authority._Ownership(store).verify()
        yield


def _relative_role(profile, directory, name):
    # Derive keys only from the sealed actual readers. These are not caller
    # paths, and the checkpoint anchors stay CHECKPOINTS even below DATA.
    path = directory.path / name
    checkpoint_names = {authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME,
                        continuity.SEAL_NAME, continuity.CHECKPOINT_HEAD_NAME}
    if name in checkpoint_names and lifecycle._key(directory.path) == lifecycle._key(profile.checkpoints):
        root_kind, relative = "CHECKPOINTS", path.relative_to(profile.checkpoints).as_posix()
    else:
        root_kind, relative = "DATA", path.relative_to(profile.database.parent).as_posix()
    pending_parts = PurePosixPath(relative).parts
    if root_kind == "DATA" and pending_parts[:1] == (pending_state.DIRECTORY,):
        selected = pending_roots._name(name)
        if len(pending_parts) != 2 or selected is None:
            _fail("INVALID")
        role = selected[1]
    elif root_kind == "DATA" and dependency_roots.role_for_key(relative) is not None:
        role = dependency_roots.role_for_key(relative)
    elif name in {authority.INTENT_NAME, continuity.INTENT_NAME}:
        role = "intent"
    elif name in {authority.SEAL_NAME, continuity.SEAL_NAME}:
        role = "seal"
    elif name in {authority.DB_HEAD_NAME, authority.CHECKPOINT_HEAD_NAME,
                  continuity.DB_HEAD_NAME, continuity.CHECKPOINT_HEAD_NAME}:
        role = "head"
    elif name == continuity.LOCK_NAME:
        role = "lock"
    elif relative.startswith("sync-publication-payloads/v1/") and not continuity._PART.fullmatch(name):
        role = "publication_payload"
    elif continuity._PART.fullmatch(name):
        role = "unpublished_part"
    elif name == "contract.json":
        role = "contract"
    elif directory.path.name == "journal":
        role = "journal"
    else:
        role = "record"
    return root_kind, relative, role


def _file_limit(entry):
    """New private roles have their own limits; legacy role limits stay exact."""
    if entry["role"] == "legacy_pending_state":
        return pending_state.MAX_BYTES
    if entry["role"] == "legacy_pending_applied":
        return pending_roots.MAX_APPLIED_BYTES
    if entry["role"] in DEPENDENCY_ROLES:
        return dependency_roots.file_limit(entry["role"])
    return MAX_FILE_BYTES


def _validate_file(entry):
    if type(entry) is not dict or set(entry) != _FILE_FIELDS:
        _fail("INVALID")
    kind, key, role = entry["root_kind"], entry["relative_key"], entry["role"]
    if kind not in {"DATA", "CHECKPOINTS"} or type(key) is not str:
        _fail("INVALID")
    parts = PurePosixPath(key).parts
    if (not parts or PurePosixPath(key).is_absolute() or ":" in key or "\\" in key
            or any(p in {".", ".."} for p in parts) or "/".join(parts) != key):
        _fail("INVALID")
    data_anchors = {authority.INTENT_NAME: "intent", authority.DB_HEAD_NAME: "head",
        continuity.INTENT_NAME: "intent", continuity.DB_HEAD_NAME: "head", continuity.LOCK_NAME: "lock",
        sync_state._STATE: "checkpoint_state"}
    checkpoint_anchors = {authority.SEAL_NAME: "seal", authority.CHECKPOINT_HEAD_NAME: "head",
                          continuity.SEAL_NAME: "seal", continuity.CHECKPOINT_HEAD_NAME: "head"}
    if role in DEPENDENCY_ROLES:
        expected = dependency_roots.role_for_key(key) if kind == "DATA" else None
    elif len(parts) == 1:
        expected = (data_anchors if kind == "DATA" else checkpoint_anchors).get(key)
    elif kind == "DATA" and parts[0] == sync_state._OUTBOX and len(parts) == 2:
        try:
            backups._uuid(parts[1][:-5])
        except ValueError:
            _fail("INVALID")
        expected = "checkpoint_outbox" if parts[1].endswith(".json") else None
    elif kind == "DATA" and parts[0] == "sync-publication-payloads":
        expected = None
        if ((len(parts) == 2 or len(parts) == 3 and parts[1] == "v1")
                and continuity._PART.fullmatch(parts[-1])):
            expected = "unpublished_part"
        elif (len(parts) == 3 and parts[1] == "v1" and parts[2].endswith(".json")
                and backups._SHA.fullmatch(parts[2][:-5])):
            expected = "publication_payload"
    elif kind == "DATA" and parts[0] == pending_state.DIRECTORY:
        selected = pending_roots._name(parts[-1]) if len(parts) == 2 else None
        expected = None if selected is None else selected[1]
    else:
        offset = 1 if parts[0] == continuity.DIRECTORY else 2
        prefix_ok = (kind == "DATA" and (parts[0] == continuity.DIRECTORY
            or parts[:2] == (authority.DIRECTORY, authority.VERSION_DIRECTORY)))
        tail = parts[offset:]
        expected = None
        if prefix_ok and tail:
            kinds = continuity.KINDS if offset == 1 else authority.KINDS
            if len(tail) == 1 and tail[0] == "contract.json":
                expected = "contract"
            elif continuity._PART.fullmatch(tail[-1]) and (len(tail) == 1
                    or len(tail) == 2 and tail[0] in {"journal", *kinds}):
                expected = "unpublished_part"
            elif len(tail) == 2 and tail[0] == "journal" and continuity._ORDINAL.fullmatch(tail[1]):
                expected = "journal"
            elif len(tail) == 2 and tail[0] in kinds and tail[1].endswith(".json"):
                try:
                    backups._uuid(tail[1][:-5])
                except ValueError:
                    _fail("INVALID")
                expected = "record"
    if (expected is None or role != expected or type(entry["sha256"]) is not str
            or not backups._SHA.fullmatch(entry["sha256"])
            or type(entry["byte_size"]) is not int or not 0 <= entry["byte_size"] <= _file_limit(entry)):
        _fail("INVALID")


def _files(entries):
    if type(entries) is not list or len(entries) > MAX_FILES + pending_roots.MAX_FILES + dependency_roots.MAX_FILES:
        _fail("LIMIT")
    keys, total, body_total, body_count, pending_total, pending_count = [], 0, 0, 0, 0, 0
    dependency_total = dependency_count = 0
    for entry in entries:
        _validate_file(entry)
        keys.append((entry["root_kind"], entry["relative_key"]))
        if entry["role"] in PENDING_ROLES:
            pending_total += entry["byte_size"]
            pending_count += 1
        elif entry["role"] in DEPENDENCY_ROLES:
            dependency_total += entry["byte_size"]
            dependency_count += 1
        elif entry["root_kind"] == "DATA" and entry["relative_key"].startswith("sync-publication-payloads/"):
            body_total += entry["byte_size"]
            body_count += 1
        else:
            total += entry["byte_size"]
    if (keys != sorted(set(keys)) or total > MAX_TOTAL_BYTES
            or body_total > MAX_PUBLICATION_PAYLOAD_TOTAL_BYTES
            or body_count > authority.MAX_RECORDS + authority.MAX_INTERNAL_PARTS
            or len(entries) - pending_count - dependency_count > MAX_FILES
            or pending_total > pending_roots.MAX_TOTAL_BYTES or pending_count > pending_roots.MAX_FILES
            or dependency_total > dependency_roots.MAX_TOTAL_BYTES or dependency_count > dependency_roots.MAX_FILES):
        _fail("INVALID")
    return entries


def _pending_file_gate(document, entries):
    """Bind the explicit original namespace to exact declared file descriptors."""
    summary = pending_contract.normalize_summary(document["permanent_witnesses"]["home_authority"])
    selected = [entry for entry in entries if entry["role"] in PENDING_ROLES]
    for entry in selected:
        _validate_file(entry)
    if summary["format_version"] == 1:
        if selected:
            _fail("PENDING_UNCOVERED_BY_HOME1")
        if any(entry["role"] in DEPENDENCY_ROLES for entry in entries):
            _fail("DEPENDENCY_UNCOVERED_BY_HOME1")
        return None
    pending = summary["legacy_pending_state"]
    names = [PurePosixPath(entry["relative_key"]).name for entry in selected]
    if (names != sorted(set(names)) or pending["file_count"] != len(selected)
            or pending["capsule_count"] != sum(entry["role"] == "legacy_pending_state" for entry in selected)
            or pending["marker_count"] != sum(entry["role"] == "legacy_pending_applied" for entry in selected)
            or pending["total_bytes"] != sum(entry["byte_size"] for entry in selected)
            or pending["namespace_sha256"] != _sha(_json({"presence": pending["presence"], "names": names}))
            or pending["file_set_sha256"] != _sha(_json(selected))):
        _fail("PENDING_FILE_SET_CHANGED")
    return pending


def _copied_pending_state(profile, document, copies, *, entries=None, check=None):
    """Parse only an original verified copied map; never open a live namespace.

    The owning bundle reader has verified original manifest/selection hashes,
    exact copied-file names and retained physical pins. Reconstructing an
    absent or present-empty namespace uses its original Home2 component, not
    a current directory lookup. Private State images remain separate from the
    public summary and cannot stand for ordinary current State/history files.
    """
    prefix = pending_state.DIRECTORY + "/"
    observed = {}
    for (kind, key), raw in copies.items():
        if check is not None:
            check()
        if kind == "DATA" and (key == pending_state.DIRECTORY or key.startswith(prefix)):
            name = key[len(prefix):] if key.startswith(prefix) else ""
            selected = pending_roots._name(name)
            if "/" in name or selected is None or type(raw) is not bytes:
                _fail("PENDING_FILE_SET_CHANGED")
            entry = {"root_kind": "DATA", "relative_key": key, "role": selected[1],
                "sha256": _sha(raw), "byte_size": len(raw)}
            _validate_file(entry)
            observed[(kind, key)] = (entry, raw)
    if entries is None:
        entries = [observed[key][0] for key in sorted(observed)]
    selected = [entry for entry in entries if entry["role"] in PENDING_ROLES]
    if {(entry["root_kind"], entry["relative_key"]) for entry in selected} != set(observed):
        _fail("PENDING_FILE_SET_CHANGED")
    for entry in selected:
        if observed[(entry["root_kind"], entry["relative_key"])][0] != entry:
            _fail("PENDING_FILE_SET_CHANGED")
    pending = _pending_file_gate(document, entries)
    if pending is None:
        return None, ()
    records = tuple(pending_roots.CapturedFile(PurePosixPath(entry["relative_key"]).name,
        observed[(entry["root_kind"], entry["relative_key"])][1], entry["sha256"], entry["byte_size"])
        for entry in selected)
    projection, images = pending_roots._project(profile, presence=pending["presence"], records=records,
        limits=pending_roots.CaptureLimits(deadline_seconds=120))
    if projection["summary"] != pending or projection["unknown"]:
        _fail("PENDING_COPIED_PROJECTION_CHANGED")
    return {key: deepcopy(projection[key]) for key in ("summary", "references", "unknown")}, tuple(images)


def _verify_copied_pending_files(profile, document, entries, directory, *, check=None):
    copies = {}
    for entry in entries:
        if entry["role"] not in PENDING_ROLES:
            continue
        if check is not None:
            check()
        raw, identity = backups._read(directory.path / _copy_name(entry), maximum=_file_limit(entry))
        if identity["sha256"] != entry["sha256"] or len(raw) != entry["byte_size"]:
            _fail("PENDING_COPIED_PROJECTION_CHANGED")
        copies[(entry["root_kind"], entry["relative_key"])] = raw
    return _copied_pending_state(profile, document, copies, entries=entries, check=check)


def _copied_pending_dependencies(profile, document, copies, *, entries, check=None):
    """Original declared copies only; never fill historical omissions from live."""
    _files(entries)
    summary = pending_contract.normalize_summary(document["permanent_witnesses"]["home_authority"])
    _pending, images = _copied_pending_state(profile, document, copies, entries=entries, check=check)
    return dependency_roots.copied_dependencies(profile,
        home_format_version=summary["format_version"], state_images=images, copies=copies, entries=entries,
        checkpoint_outboxes=tuple(document["checkpoint"]["outboxes"]),
        check_cancelled=check, limits=dependency_roots.CaptureLimits(deadline_seconds=MAX_SECONDS))


def _verify_copied_pending_dependencies(profile, document, entries, directory, *, check=None,
        require_complete=False):
    copies = {}
    for entry in entries:
        if entry["role"] not in PENDING_ROLES | DEPENDENCY_ROLES | {"checkpoint_outbox"}:
            continue
        if check is not None:
            check()
        raw, identity = backups._read(directory.path / _copy_name(entry), maximum=_file_limit(entry))
        if identity["sha256"] != entry["sha256"] or len(raw) != entry["byte_size"]:
            _fail("DEPENDENCY_COPIED_CHANGED")
        copies[(entry["root_kind"], entry["relative_key"])] = raw
    facts = _copied_pending_dependencies(profile, document, copies, entries=entries, check=check)
    if require_complete and facts[2]:
        _fail("DEPENDENCY_INCOMPLETE")
    return facts


def _verify_pending_prefix(original, latest, old_entries, current_entries):
    """Allow verified append only; original capsule/marker selection is immutable.

    Existing keys retain exact descriptors and source identities in the outer
    prefix check. Later valid capsules or matching immutable applied markers
    may append under a fresh complete Home2 capture. A historic absence can be
    followed by this proven present namespace; it never covers the new files.
    """
    checked = pending_contract.normalize_summary(original)
    if checked["format_version"] == 1:
        if any(entry["role"] in PENDING_ROLES for entry in old_entries):
            _fail("PENDING_UNCOVERED_BY_HOME1")
        return
    current = pending_contract.normalize_summary(latest, expected_version=2)
    before, after = checked["legacy_pending_state"], current["legacy_pending_state"]
    if (before["owner_profile_sha256"] != after["owner_profile_sha256"]
            or before["presence"] == "present" and after["presence"] != "present"
            or any(after[name] < before[name] for name in
                ("file_count", "capsule_count", "marker_count", "applied_count", "total_bytes"))):
        _fail("PENDING_PREFIX_CHANGED")
    old = [entry for entry in old_entries if entry["role"] in PENDING_ROLES]
    new = [entry for entry in current_entries if entry["role"] in PENDING_ROLES]
    if old == new and before["presence"] == after["presence"] and before != after:
        _fail("PENDING_PREFIX_CHANGED")


@contextmanager
def _actual_files(actual, con, profile, store, *, fresh=False):
    witness.require_capture(actual, connection=con, profile=profile, store=store)
    window = witness._WINDOW.get()
    # No long copy, SHA or private State export is allowed inside SQL writer.
    if con.execute("PRAGMA query_only").fetchone()[0] != 1:
        _fail("READ_WINDOW_REQUIRED")
    with ExitStack() as stack:
        selected = {}
        dependencies = None
        pending = getattr(actual._home, "_legacy_pending", None)
        home_version = actual._home.summary["format_version"]
        if (home_version == 1 and pending is not None or home_version == 2 and pending is None
                or home_version not in (1, 2)):
            _fail("PENDING_CAPTURE_REQUIRED")
        captures = (actual._cloud, actual._home) + (() if pending is None else (pending,))
        if fresh:
            if home_version != 2 or pending is None:
                _fail("DEPENDENCY_UNCOVERED_BY_HOME1")
            # This observer is nested inside its actual parent's readonly
            # witness lifetime. Only copied artifact pins outlive that window.
            dependencies = stack.enter_context(dependency_roots.capture_locked(profile, pending_capture=pending,
                limits=dependency_roots.CaptureLimits(deadline_seconds=MAX_SECONDS),
                check_cancelled=store.check_cancelled))
            if dependencies.summary["coverage"] != "complete" or dependencies.unknown:
                _fail("DEPENDENCY_INCOMPLETE")
            captures += (dependencies,)
        for capture in captures:
            for directory, name, held, before, opened, digest in capture._reader.files:
                root, key, role = _relative_role(profile, directory, name)
                identity = (root, key)
                item = ({"root_kind": root, "relative_key": key, "role": role,
                    "sha256": digest.hex(), "byte_size": before[2]}, directory, name, held, before, opened)
                if identity in selected and selected[identity][0] != item[0]:
                    _fail("CHANGED")
                selected[identity] = item
        if actual._state["present"]:
            held = stack.enter_context(source.source_reader(store._directory, sync_state._STATE))
            before = actual._state["signature"]
            selected[("DATA", sync_state._STATE)] = ({"root_kind": "DATA", "relative_key": sync_state._STATE,
                "role": "checkpoint_state", "sha256": actual._state["raw_sha256"], "byte_size": before[2]},
                store._directory, sync_state._STATE, held, before, source.file_signature(os.fstat(held.fileno())))
        for directory, name, held, before, opened, digest in window.checkpoint.files:
            key = sync_state._OUTBOX + "/" + name
            item = ({"root_kind": "DATA", "relative_key": key,
                "role": "checkpoint_outbox", "sha256": digest, "byte_size": before[2]},
                directory, name, held, before, opened)
            prior = selected.get(("DATA", key))
            if prior is not None:
                if (prior[0]["role"] != dependency_roots.ROLE_OUTBOX
                        or {k: value for k, value in prior[0].items() if k != "role"}
                            != {k: value for k, value in item[0].items() if k != "role"}
                        or prior[4:] != item[4:]):
                    _fail("DEPENDENCY_CHECKPOINT_OVERLAP_CHANGED")
            # One actual file, one original descriptor. Its capsule bindings
            # remain independently validated; the current checkpoint list is
            # neither enlarged nor relabeled to accommodate history.
            selected[("DATA", key)] = item
        items = [selected[key] for key in sorted(selected)]
        _files([item[0] for item in items])
        _pending_file_gate({"permanent_witnesses": {"home_authority": actual._home.summary}},
            [item[0] for item in items])
        yield items
        if dependencies is not None:
            dependencies.verify_source()
        actual.verify_source()


def _sql_digest17(con):
    """Fixed typed whole-image comparison, including knowledge and BLOBs.

    This private image check has no profile or execution authority. The caller
    separately binds the registered image to its actual owned live window.
    """
    ordering, schema_sha, version, projections = base._schema(con, prospective_schema17=True)
    if version != "17":
        _fail("SCHEMA_REQUIRED")
    columns_by_table = {**projections, **{table: tuple(cols.split()) for table, cols in base._KNOWLEDGE.items()}}
    scan, tables = [0, 0], []
    for table in sorted(columns_by_table):
        columns = columns_by_table[table]
        fields = ",".join('"' + col + '"' for col in columns)
        order = ",".join('"' + col + '"' for col in ordering[table])
        lengths = ",".join('length(CAST("' + col + '" AS BLOB))' for col in columns)
        for sizes in con.execute(f'SELECT {lengths} FROM main."{table}" LIMIT ?', (base.MAX_ROWS + 1,)):
            scan[0] += 1
            if scan[0] > base.MAX_ROWS or any(n is not None and n > base.MAX_CELL_BYTES for n in sizes):
                _fail("LIMIT")
        digest, count = hashlib.sha256(_json([table, list(columns)])), 0
        for raw in con.execute(f'SELECT {fields} FROM main."{table}" ORDER BY {order}'):
            digest.update(bytes.fromhex(base._row_sha(table, columns, dict(zip(columns, raw)), scan)))
            count += 1
        tables.append([table, count, digest.hexdigest()])
    return _sha(_json({"schema_sha256": schema_sha, "tables": tables}))


def _copy_name(entry):
    return entry["root_kind"] + "-" + _sha(entry["relative_key"].encode("utf-8")) + ".bin"


def _read_json(directory, name):
    raw, identity = backups._read(directory.path / name, maximum=MAX_METADATA_BYTES)
    value = backups._decode(raw)
    if _json(value) != raw:
        _fail("INVALID")
    return value, raw, identity


def _write_json(directory, name, value):
    raw = _json(value)
    if len(raw) > MAX_METADATA_BYTES:
        _fail("LIMIT")
    directory.verify()
    sync_state._write(directory, name, raw, immutable=True)
    if backups._read(directory.path / name, maximum=MAX_METADATA_BYTES)[0] != raw:
        _fail("CHANGED")
    return _sha(raw)


def _pin(pins, path, maximum, *, digest=None, byte_size=None):
    _, identity = home_sources._pin_file(pins, path, maximum)
    if (digest is not None and identity["sha256"] != digest
            or byte_size is not None and identity["byte_size"] != byte_size):
        _fail("CHANGED")
    return identity


def _namespace(directory, expected):
    directory.verify()
    if set(directory.names()) != set(expected):
        _fail("UNEXPECTED_FILE")


def _verify_final(bundle, profile, entries, pins):
    """Recheck the whole closed selection while its original pins are live."""
    _namespace(bundle, {"certificate.json", "manifest.json", "files"})
    with safe._Directory(bundle.path / "files", base=profile.database.parent) as copies:
        expected = {_copy_name(entry) for entry in entries}
        _namespace(copies, expected)
        pins.verify()
        _namespace(copies, expected)
    _namespace(bundle, {"certificate.json", "manifest.json", "files"})


def _copy_source(item, destination, name, store, started):
    entry, directory, original_name, held, before, opened = item
    _check(store, started)
    directory.verify()
    if source.file_signature(directory.entry_stat(original_name)) != before:
        _fail("CHANGED")
    with ExitStack() as stack:
        if held is None:
            held = stack.enter_context(source.source_reader(directory, original_name))
        if source.file_signature(os.fstat(held.fileno())) != opened:
            _fail("CHANGED")
        temporary = ".sync-" + uuid.uuid4().hex + ".part"
        owned, failed = None, None
        try:
            with destination.open_file(temporary, write=True, create=True) as target:
                info = os.fstat(target.fileno())
                owned = (info.st_dev, info.st_ino)
                os.lseek(held.fileno(), 0, os.SEEK_SET)
                digest, copied = hashlib.sha256(), 0
                while chunk := os.read(held.fileno(), min(128 * 1024, entry["byte_size"] + 1 - copied)):
                    _check(store, started)
                    copied += len(chunk)
                    if copied > entry["byte_size"]:
                        _fail("CHANGED")
                    digest.update(chunk)
                    if target.write(chunk) != len(chunk):
                        _fail("COPY_FAILED")
                if copied != entry["byte_size"] or digest.hexdigest() != entry["sha256"]:
                    _fail("CHANGED")
                target.flush()
                os.fsync(target.fileno())
            if (source.file_signature(os.fstat(held.fileno())) != opened
                    or source.file_signature(directory.entry_stat(original_name)) != before):
                _fail("CHANGED")
            destination.verify()
            current = destination.entry_stat(temporary)
            if (current.st_dev, current.st_ino) != owned or current.st_nlink != 1:
                _fail("CHANGED")
            safe._rename_new(destination, temporary, destination, name)
            if os.name != "nt":
                os.fsync(destination.fd)
        except BaseException as exc:
            failed = exc
            raise
        finally:
            try:
                current = destination.entry_stat(temporary)
                if (current.st_dev, current.st_ino) != owned or current.st_nlink != 1:
                    _fail("CHANGED")
                destination.unlink(temporary)
            except FileNotFoundError:
                pass
            except BaseException:
                if failed is None:
                    raise
    directory.verify()


@contextmanager
def _backup_image(con, profile, backup_id, *, store, started, create, pins_out=None):
    destination = profile.database.with_name(profile.database.name + ".bak-preservation17-" + backup_id)
    if create:
        backups.create(con, destination, profile=profile, kind="maintenance", backup_id=backup_id)
    with ExitStack() as stack:
        pins = _Pins(stack)
        if pins_out is not None:
            pins_out.append(pins)
        registry = backups._catalog(profile) / (backup_id + ".json")
        raw, _ = home_sources._pin_file(pins, registry, backups.MAX_JSON, capture=True)
        record, _ = backups._record(registry)
        if record["state"] != "ready" or record["backup_id"] != backup_id or record["destination"] != backups._key(destination):
            _fail("BACKUP_INVALID")
        descriptor, descriptor_sha = backups._verify_ready(record, profile)
        if descriptor["schema_version"] != 17 or descriptor["kind"] != "maintenance":
            _fail("BACKUP_INVALID")
        if backups._decode(raw) != record:
            _fail("CHANGED")
        _, sidecar = home_sources._pin_file(pins, destination.with_name(destination.name + backups.SUFFIX), backups.MAX_JSON)
        if sidecar["sha256"] != descriptor_sha:
            _fail("CHANGED")
        home_sources._pin_file(pins, destination, backups.MAX_IMAGE, expected=descriptor["database_file"])
        original = pins.files[-1][2]
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-preservation17-"))
        path = Path(temporary) / "image.sqlite"
        os.lseek(original.fileno(), 0, os.SEEK_SET)
        copied, digest = 0, hashlib.sha256()
        with path.open("xb") as target:
            while chunk := os.read(original.fileno(), 128 * 1024):
                _check(store, started)
                copied += len(chunk)
                if copied > descriptor["database_file"]["byte_size"]:
                    _fail("CHANGED")
                digest.update(chunk)
                target.write(chunk)
        if copied != descriptor["database_file"]["byte_size"] or digest.hexdigest() != descriptor["database_file"]["sha256"]:
            _fail("CHANGED")
        archive = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        stack.callback(archive.close)
        archive.row_factory = sqlite3.Row
        archive.execute("PRAGMA query_only=ON")
        archive.execute("PRAGMA trusted_schema=OFF")
        archive.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, base.MAX_CELL_BYTES)
        archive.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, base.MAX_SCHEMA_BYTES)
        pins.verify()
        yield archive, {"backup_id": backup_id, "image_sha256": descriptor["database_file"]["sha256"],
                        "descriptor_sha256": descriptor_sha}
        pins.verify()


@dataclass(frozen=True, slots=True, repr=False)
class BundleResult:
    manifest_json: str
    certificate_json: str
    receipt_sha256: str
    replayed: bool

    def public(self):
        manifest = backups._decode(self.manifest_json.encode("utf-8"))
        return {"format_version": 1, "bundle_id": manifest["bundle_id"],
            "operation_id": manifest["operation_id"], "manifest_sha256": manifest["manifest_sha256"],
            "file_set_sha256": manifest["file_set_sha256"], "certificate_sha256": manifest["certificate"]["sha256"],
            "receipt_sha256": self.receipt_sha256, "replayed": self.replayed}


def _reservation(value, profile, operation_id, root_job_id):
    bundle_id, backup_id = _ids(operation_id)
    if (type(value) is not dict or set(value) != _RESERVATION_FIELDS or type(value["format_version"]) is not int
            or value["format_version"] != 1 or value["action"] != "preserve_home17"
            or value["operation_id"] != operation_id or value["bundle_id"] != bundle_id
            or value["backup_id"] != backup_id or value["root_job_id"] != root_job_id
            or value["root_job_id"] is not None and (type(value["root_job_id"]) is not int or value["root_job_id"] < 1)
            or value["owner_profile_sha256"] != _sha(_json(list(profile.identity)))):
        _fail("OPERATION_CONFLICT")
    _files(value["files"])
    if (value["file_set_sha256"] != _sha(_json(value["files"]))
            or type(value["source_identities"]) is not list or len(value["source_identities"]) != len(value["files"])
            or type(value["certificate_bytes"]) is not int or not 0 < value["certificate_bytes"] <= base.MAX_CERTIFICATE_BYTES
            or type(value["created_at"]) is not str or not backups._TIME.fullmatch(value["created_at"])):
        _fail("INVALID")
    for key in ("library_id", "node_id"):
        backups._uuid(value[key])
    for key in ("certificate_sha256", "sql_image_sha256"):
        if type(value[key]) is not str or not backups._SHA.fullmatch(value[key]):
            _fail("INVALID")
    for identity in value["source_identities"]:
        if type(identity) is not list or len(identity) != 5 or any(type(n) is not int or n < 0 for n in identity):
            _fail("INVALID")
    return value


def _bundle_certificate(raw, reservation):
    if len(raw) != reservation["certificate_bytes"] or _sha(raw) != reservation["certificate_sha256"]:
        _fail("CHANGED")
    value = certificate._certificate(certificate.PreservationCapture17(raw.decode("utf-8"), _sha(raw)))
    if (any(value[k] != reservation[r] for k, r in (("profile_sha256", "owner_profile_sha256"),
            ("library_id", "library_id"), ("node_id", "node_id"), ("knowledge_version", "knowledge_version")))
            or value["frontier"]["root_job_id"] != reservation["root_job_id"]):
        _fail("CHANGED")
    _pending_file_gate(value, reservation["files"])
    state_files = [row for row in reservation["files"] if row["role"] == "checkpoint_state"]
    checkpoint = value["checkpoint"]
    if (len(state_files) != int(checkpoint["state_present"]) or state_files and (
            state_files[0]["sha256"] != checkpoint["state_sha256"] or state_files[0]["byte_size"] != checkpoint["state_bytes"])):
        _fail("CHANGED")
    outboxes = {row["relative_key"]: row for row in reservation["files"] if row["role"] == "checkpoint_outbox"}
    if len(outboxes) != len(checkpoint["outboxes"]):
        _fail("CHANGED")
    for row in checkpoint["outboxes"]:
        item = outboxes.get(sync_state._OUTBOX + "/" + row["publication_id"] + ".json")
        if item is None or item["sha256"] != row["sha256"] or item["byte_size"] != row["byte_size"]:
            _fail("CHANGED")
    return value


def _checkpoint_wrapper(document, manifest):
    """Exact new wrapper facts only; no producer or admission capability."""
    return home_contract.normalize_checkpoint({"format_version": 2,
        "checkpoint": document["checkpoint"],
        "certificate": {"format_version": 2, "certificate_sha256": manifest["certificate"]["sha256"],
            "byte_size": manifest["certificate"]["byte_size"], "preserved_sha256": document["preserved_sha256"],
            "frontier_sha256": _sha(_json(document["frontier"]))},
        "bundle": {"format_version": 1, "bundle_id": manifest["bundle_id"],
            "manifest_sha256": manifest["manifest_sha256"], "file_set_sha256": manifest["file_set_sha256"]},
        "state_authority": document["state_authority"], "permanent_witnesses": document["permanent_witnesses"]})


def _verify_prefix(actual, con, profile, store, reservation, document, *, dependency_keys=(), pins=None,
        started=None):
    """A completed snapshot may coexist with legal newer heads, never forks."""
    if pins is None:
        _fail("DEPENDENCY_PINS_REQUIRED")
    if started is None:
        started = time.monotonic()
    required = set(dependency_keys)
    original_dependencies = set()
    # The immutable original selection, not today's capsule union, chooses
    # these sources. A formerly current checkpoint may now be historical; its
    # original path, bytes and full physical identity still must remain exact.
    for entry, original in zip(reservation["files"], reservation["source_identities"]):
        key = (entry["root_kind"], entry["relative_key"])
        if (entry["role"] not in DEPENDENCY_ROLES
                and not (entry["role"] == "checkpoint_outbox" and entry["relative_key"] in required)):
            continue
        _check(store, started)
        identity = _pin(pins, profile.database.parent / entry["relative_key"], _file_limit(entry),
            digest=entry["sha256"], byte_size=entry["byte_size"])
        observed = [int(identity[field]) for field in ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")]
        if observed != original or list(pins.files[-1][5]) != original:
            _fail("DEPENDENCY_PREFIX_CHANGED")
        original_dependencies.add(key)
    current = actual.capsules()
    for kind, original in document["permanent_witnesses"].items():
        latest = current[kind]
        if original["presence"] == "present" and (latest["presence"] != "present"
                or latest["identity"] != original["identity"]
                or latest["contract_sha256"] != original["contract_sha256"]
                or latest["head_ordinal"] < original["head_ordinal"]
                or latest["head_ordinal"] == original["head_ordinal"] and latest["head_sha256"] != original["head_sha256"]):
            _fail("PREFIX_CHANGED")
    with _actual_files(actual, con, profile, store) as items:
        _verify_pending_prefix(document["permanent_witnesses"]["home_authority"], current["home_authority"],
            reservation["files"], [item[0] for item in items])
        current_files = {(item[0]["root_kind"], item[0]["relative_key"]): item for item in items}
        for entry, identity in zip(reservation["files"], reservation["source_identities"]):
            if (entry["root_kind"], entry["relative_key"]) in original_dependencies:
                continue
            if entry["role"] in {"head", "checkpoint_state", "checkpoint_outbox"}:
                continue
            found = current_files.get((entry["root_kind"], entry["relative_key"]))
            if found is None or found[0] != entry or list(found[4]) != identity:
                _fail("PREFIX_CHANGED")
        pins.verify()


def _load_completed(con, actual, operation, bundle, profile, store, operation_id, root_job_id, started, stack,
                    *, proof_pins=None, backup_pins_out=None):
    pins = _Pins(stack) if proof_pins is None else proof_pins
    reservation, reservation_raw, _ = _read_json(operation, operation_id + ".json")
    _pin(pins, operation.path / (operation_id + ".json"), MAX_METADATA_BYTES, digest=_sha(reservation_raw))
    _reservation(reservation, profile, operation_id, root_job_id)
    receipt, receipt_raw, _ = _read_json(operation, operation_id + ".receipt.json")
    _pin(pins, operation.path / (operation_id + ".receipt.json"), MAX_METADATA_BYTES, digest=_sha(receipt_raw))
    if (set(receipt) != _RECEIPT_FIELDS or type(receipt["format_version"]) is not int
            or receipt["format_version"] != 1 or receipt["action"] != "preserve_home17"
            or receipt["operation_id"] != operation_id or receipt["bundle_id"] != reservation["bundle_id"]
            or receipt["reservation_sha256"] != _sha(reservation_raw)
            or receipt["certificate_sha256"] != reservation["certificate_sha256"]
            or receipt["created_at"] != reservation["created_at"]):
        _fail("RECEIPT_INVALID")
    manifest, manifest_raw, _ = _read_json(bundle, "manifest.json")
    _pin(pins, bundle.path / "manifest.json", MAX_METADATA_BYTES, digest=_sha(manifest_raw))
    cert_raw, _ = backups._read(bundle.path / "certificate.json", maximum=base.MAX_CERTIFICATE_BYTES)
    _pin(pins, bundle.path / "certificate.json", base.MAX_CERTIFICATE_BYTES, digest=_sha(cert_raw))
    document = _bundle_certificate(cert_raw, reservation)
    expected = {"format_version": 1, "bundle_id": reservation["bundle_id"], "operation_id": operation_id,
        "owner_profile_sha256": reservation["owner_profile_sha256"], "library_id": reservation["library_id"],
        "node_id": reservation["node_id"], "knowledge_version": reservation["knowledge_version"],
        "database_backup": manifest.get("database_backup"),
        "certificate": {"format_version": 2, "sha256": reservation["certificate_sha256"],
                        "byte_size": reservation["certificate_bytes"]},
        "permanent_witnesses": document["permanent_witnesses"], "files": reservation["files"],
        "file_set_sha256": reservation["file_set_sha256"], "created_at": reservation["created_at"]}
    expected["manifest_sha256"] = _sha(_json(expected))
    if (set(manifest) != _MANIFEST_FIELDS or manifest != expected
            or receipt["manifest_sha256"] != manifest["manifest_sha256"]):
        _fail("CHANGED")
    _checkpoint_wrapper(document, manifest)
    _namespace(bundle, {"certificate.json", "manifest.json", "files"})
    with safe._Directory(bundle.path / "files", base=profile.database.parent) as copies:
        _namespace(copies, {_copy_name(row) for row in reservation["files"]})
        identities = []
        for entry in reservation["files"]:
            _check(store, started)
            name = _copy_name(entry)
            _, identity = backups._read(copies.path / name, maximum=_file_limit(entry), content=False)
            if identity["sha256"] != entry["sha256"] or identity["byte_size"] != entry["byte_size"]:
                _fail("CHANGED")
            identities.append({"name": name, "identity": identity})
            home_sources._pin_file(pins, copies.path / name, _file_limit(entry), expected=identity)
        for name in ("certificate.json", "manifest.json"):
            _, identity = backups._read(bundle.path / name, maximum=MAX_METADATA_BYTES, content=False)
            identities.append({"name": name, "identity": identity})
        if receipt["files"] != identities:
            _fail("CHANGED")
        _verify_copied_pending_files(profile, document, reservation["files"], copies,
            check=lambda: _check(store, started))
        requirements, _records, _unknown = _verify_copied_pending_dependencies(profile, document,
            reservation["files"], copies, check=lambda: _check(store, started))
    archive, facts = stack.enter_context(_backup_image(con, profile, reservation["backup_id"],
        store=store, started=started, create=False, pins_out=backup_pins_out))
    if facts != manifest["database_backup"] or _sql_digest17(archive) != reservation["sql_image_sha256"]:
        _fail("BACKUP_MISMATCH")
    _verify_prefix(actual, con, profile, store, reservation, document,
        dependency_keys=tuple(requirement.relative_key for requirement in requirements), pins=pins, started=started)
    _verify_final(bundle, profile, reservation["files"], pins)
    stack.callback(_verify_final, bundle, profile, reservation["files"], pins)
    # A historic completed bundle is never compared as a fresh absence or used
    # to overwrite current heads. Current facilities are validated separately
    # by the live owned witness window which encloses this read.
    return BundleResult(manifest_raw.decode("utf-8"), cert_raw.decode("utf-8"), _sha(receipt_raw), True)


class BundleCapture:
    """Live artifact pins under original State/EX ownership, never admission."""
    __slots__ = ("_seal", "_store", "_profile", "_owner", "_guard", "_thread", "_active",
                 "_pins", "_backup_pins", "_bundle", "_result", "_manifest", "_document")

    def __init__(self, *, seal, profile, store, owner, guard, pins, backup_pins, bundle, result):
        if seal is not _CAPTURE_SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._profile, self._store, self._owner, self._guard = seal, profile, store, owner, guard
        self._thread, self._active = threading.get_ident(), True
        self._pins, self._backup_pins, self._bundle, self._result = pins, backup_pins, bundle, result
        self._manifest = backups._decode(result.manifest_json.encode("utf-8"))
        self._document = backups._decode(result.certificate_json.encode("utf-8"))

    def _check(self):
        if (not any(value is self for value in _CAPTURE.get()) or not self._active or self._seal is not _CAPTURE_SEAL
                or self._thread != threading.get_ident()):
            _fail("OWNER_REQUIRED")
        self._owner.verify()
        if lifecycle.require_guard(self._profile, exclusive=True) is not self._guard:
            _fail("OWNER_REQUIRED")

    def verify_for_commit(self):
        self._check()
        for pins in (self._pins, *self._backup_pins):
            for item in pins.files:
                pins._identity(item)
        _namespace(self._bundle, {"certificate.json", "manifest.json", "files"})
        with safe._Directory(self._bundle.path / "files", base=self._profile.database.parent) as copies:
            _namespace(copies, {_copy_name(entry) for entry in self._manifest["files"]})

    def verify_source(self):
        self.verify_for_commit()
        current = witness._WINDOW.get()
        if current is not None and current.connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            _fail("READ_WINDOW_REQUIRED")
        _verify_final(self._bundle, self._profile, self._manifest["files"], self._pins)
        for pins in self._backup_pins:
            pins.verify()

    def facts(self):
        self.verify_for_commit()
        return deepcopy({"manifest": self._manifest, "certificate": self._document,
                         "receipt_sha256": self._result.receipt_sha256})

    def before(self):
        self.verify_for_commit()
        return certificate.PreservationCapture17(self._result.certificate_json,
                                                  self._manifest["certificate"]["sha256"])

    def checkpoint_wrapper(self):
        self.verify_for_commit()
        return _checkpoint_wrapper(self._document, self._manifest)


@contextmanager
def open_completed_locked(connection, *, profile, store, operation_id, root_job_id, actual_witness,
                          expected_manifest_sha256=None, expected_receipt_sha256=None):
    """Open actual completed artifacts before a writer, retain their real pins.

    The initial SQL/State/facility proof must be this factory's live readonly
    witness. Artifact ownership then remains tied to the original State frame
    and EX guard across a subsequent independently owned SQL window. It is not
    an old SQL witness reused after COMMIT, nor restore/readmit/dispatch proof.
    """
    profile = lifecycle._profile(profile)
    if type(connection) is not sqlite3.Connection or connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        _fail("OWNER_REQUIRED")
    witness.require_capture(actual_witness, connection=connection, profile=profile, store=store)
    guard = lifecycle.require_guard(profile, exclusive=True)
    owner = authority._Ownership(store)
    owner.verify()
    for prior in _CAPTURE.get():
        prior._check()
        if prior._store is not store or prior._profile.identity != profile.identity or prior._guard is not guard:
            _fail("OWNER_REQUIRED")
    bundle_id, _ = _ids(operation_id)
    with ExitStack() as stack:
        operation = stack.enter_context(safe._Directory(profile.database.parent / OPERATIONS,
            base=profile.database.parent))
        bundle = stack.enter_context(safe._Directory(profile.database.parent / DIRECTORY / bundle_id,
            base=profile.database.parent))
        pins, backup_pins = _Pins(stack), []
        result = _load_completed(connection, actual_witness, operation, bundle, profile, store,
            operation_id, root_job_id, time.monotonic(), stack, proof_pins=pins, backup_pins_out=backup_pins)
        manifest = backups._decode(result.manifest_json.encode("utf-8"))
        if (expected_manifest_sha256 is not None and manifest["manifest_sha256"] != expected_manifest_sha256
                or expected_receipt_sha256 is not None and result.receipt_sha256 != expected_receipt_sha256
                or actual_witness.identity() != {"library_id": manifest["library_id"], "node_id": manifest["node_id"]}):
            _fail("CHANGED")
        capture = BundleCapture(seal=_CAPTURE_SEAL, profile=profile, store=store, owner=owner, guard=guard,
            pins=pins, backup_pins=backup_pins, bundle=bundle, result=result)
        token = _CAPTURE.set((*_CAPTURE.get(), capture))
        try:
            capture.verify_source()
            yield capture
            capture.verify_source()
        finally:
            capture._active = False
            _CAPTURE.reset(token)


def create(*, profile, store, operation_id, root_job_id=None):
    """Freeze real Home17 facts and preserve their registered committed image.

    No connection, source declaration DTO, certificate DTO or arbitrary file
    path is accepted. A failed/unfinished original operation retains its exact
    selection; it cannot change the backup UUID, certificate or mutable heads.
    """
    profile = _profile(profile, store, operation_id, root_job_id)
    bundle_id, backup_id = _ids(operation_id)
    started = time.monotonic()
    with _owned(profile, store), ExitStack() as stack:
        bundle_pins = _Pins(stack)
        with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=profile, store=store) as actual:
                # Schema, identity and all original permanent facilities are
                # checked before any new operation directory or reservation.
                try:
                    backups._read(profile.database.parent / OPERATIONS / (operation_id + ".receipt.json"),
                                  maximum=MAX_METADATA_BYTES, content=False)
                except FileNotFoundError:
                    completed = False
                    before = certificate.capture(con, profile=profile, store=store,
                        root_job_id=root_job_id, witness=actual)
                else:
                    completed = True
                operation = stack.enter_context(safe._Directory(profile.database.parent / OPERATIONS,
                    base=profile.database.parent, create=not completed))
                bundle = stack.enter_context(safe._Directory(profile.database.parent / DIRECTORY / bundle_id,
                    base=profile.database.parent, create=not completed))
                if completed:
                    result = _load_completed(con, actual, operation, bundle, profile, store,
                        operation_id, root_job_id, started, stack)
                    if actual.identity() != {"library_id": backups._decode(result.certificate_json.encode())["library_id"],
                                             "node_id": backups._decode(result.certificate_json.encode())["node_id"]}:
                        _fail("CHANGED")
                    return result
                document = before.public()
                sql_sha = _sql_digest17(con)
                with _actual_files(actual, con, profile, store, fresh=True) as items:
                    entries = [item[0] for item in items]
                    _pending_file_gate(document, entries)
                    selection = [list(item[4]) for item in items]
                    expected = {"format_version": 1, "action": "preserve_home17", "operation_id": operation_id,
                        "bundle_id": bundle_id, "backup_id": backup_id, "root_job_id": root_job_id,
                        "owner_profile_sha256": document["profile_sha256"], "library_id": document["library_id"],
                        "node_id": document["node_id"], "knowledge_version": document["knowledge_version"],
                        "certificate_sha256": before.sha256, "certificate_bytes": len(before.document_json.encode()),
                        "sql_image_sha256": sql_sha, "files": entries, "file_set_sha256": _sha(_json(entries)),
                        "source_identities": selection}
                    try:
                        reserved, reserved_raw, _ = _read_json(operation, operation_id + ".json")
                    except FileNotFoundError:
                        # Unknown pre-existing bundle files are not adopted.
                        _namespace(bundle, set())
                        destination = profile.database.with_name(profile.database.name + ".bak-preservation17-" + backup_id)
                        for path in (backups._catalog(profile) / (backup_id + ".json"), destination,
                                     destination.with_name(destination.name + backups.SUFFIX)):
                            try:
                                backups._read(path, maximum=backups.MAX_IMAGE, content=False)
                            except FileNotFoundError:
                                continue
                            _fail("RESERVATION_MISSING")
                        reserved = {**expected, "created_at": backups._stamp()}
                        _write_json(operation, operation_id + ".json", reserved)
                        reserved_raw = _json(reserved)
                    else:
                        _reservation(reserved, profile, operation_id, root_job_id)
                        if {key: reserved[key] for key in expected} != expected:
                            _fail("OPERATION_CONFLICT")
                    _reservation(reserved, profile, operation_id, root_job_id)
                    _pin(bundle_pins, operation.path / (operation_id + ".json"), MAX_METADATA_BYTES,
                         digest=_sha(reserved_raw))
                    try:
                        original, _ = backups._read(bundle.path / "certificate.json", maximum=base.MAX_CERTIFICATE_BYTES)
                    except FileNotFoundError:
                        _write_json(bundle, "certificate.json", document)
                    else:
                        if original != before.document_json.encode("utf-8"):
                            _fail("CHANGED")
                    _pin(bundle_pins, bundle.path / "certificate.json", base.MAX_CERTIFICATE_BYTES,
                         digest=before.sha256)
                    copies = stack.enter_context(safe._Directory(bundle.path / "files", base=profile.database.parent, create=True))
                    allowed = {_copy_name(entry) for entry in entries}
                    if not set(copies.names()) <= allowed:
                        _fail("UNEXPECTED_FILE")
                    for item in items:
                        entry = item[0]
                        name = _copy_name(entry)
                        try:
                            _, identity = backups._read(copies.path / name, maximum=_file_limit(entry), content=False)
                        except FileNotFoundError:
                            _copy_source(item, copies, name, store, started)
                        else:
                            if identity["sha256"] != entry["sha256"] or identity["byte_size"] != entry["byte_size"]:
                                _fail("CHANGED")
                        _pin(bundle_pins, copies.path / name, _file_limit(entry),
                             digest=entry["sha256"], byte_size=entry["byte_size"])
                    _namespace(copies, allowed)
                    _verify_copied_pending_files(profile, document, entries, copies,
                        check=lambda: _check(store, started))
                    _verify_copied_pending_dependencies(profile, document, entries, copies,
                        check=lambda: _check(store, started), require_complete=True)
                    archive, facts = stack.enter_context(_backup_image(con, profile, backup_id,
                        store=store, started=started, create=True))
                    if _sql_digest17(archive) != sql_sha or _sql_digest17(con) != sql_sha:
                        _fail("BACKUP_MISMATCH")
                    manifest = {"format_version": 1, "bundle_id": bundle_id, "operation_id": operation_id,
                        "owner_profile_sha256": document["profile_sha256"], "library_id": document["library_id"],
                        "node_id": document["node_id"], "knowledge_version": document["knowledge_version"],
                        "database_backup": facts, "certificate": {"format_version": 2,
                            "sha256": before.sha256, "byte_size": len(before.document_json.encode("utf-8"))},
                        "permanent_witnesses": document["permanent_witnesses"], "files": entries,
                        "file_set_sha256": reserved["file_set_sha256"], "created_at": reserved["created_at"]}
                    manifest["manifest_sha256"] = _sha(_json(manifest))
                    _checkpoint_wrapper(document, manifest)
                    try:
                        existing, _, _ = _read_json(bundle, "manifest.json")
                    except FileNotFoundError:
                        _write_json(bundle, "manifest.json", manifest)
                    else:
                        if existing != manifest:
                            _fail("CHANGED")
                    _pin(bundle_pins, bundle.path / "manifest.json", MAX_METADATA_BYTES,
                         digest=_sha(_json(manifest)))
                    _namespace(bundle, {"certificate.json", "manifest.json", "files"})
                    actual.verify_source()
                    certificate.verify_unchanged(con, before, profile=profile, store=store, witness=actual)
                # Full source/State/outbox SHA and namespace checks on witness
                # window exit precede publication of the producer receipt.
        _check(store, started)
        identities = []
        for entry in entries:
            name = _copy_name(entry)
            _, identity = backups._read(copies.path / name, maximum=_file_limit(entry), content=False)
            if identity["sha256"] != entry["sha256"] or identity["byte_size"] != entry["byte_size"]:
                _fail("CHANGED")
            identities.append({"name": name, "identity": identity})
        for name in ("certificate.json", "manifest.json"):
            _, identity = backups._read(bundle.path / name, maximum=MAX_METADATA_BYTES, content=False)
            if identity["sha256"] != (before.sha256 if name == "certificate.json" else _sha(_json(manifest))):
                _fail("CHANGED")
            identities.append({"name": name, "identity": identity})
        _namespace(bundle, {"certificate.json", "manifest.json", "files"})
        _namespace(copies, {_copy_name(entry) for entry in entries})
        receipt = {"format_version": 1, "action": "preserve_home17", "operation_id": operation_id,
            "bundle_id": bundle_id, "reservation_sha256": _sha(reserved_raw),
            "manifest_sha256": manifest["manifest_sha256"], "certificate_sha256": before.sha256,
            "files": identities, "created_at": reserved["created_at"]}
        bundle_pins.verify()
        receipt_sha = _write_json(operation, operation_id + ".receipt.json", receipt)
        _pin(bundle_pins, operation.path / (operation_id + ".receipt.json"), MAX_METADATA_BYTES,
             digest=receipt_sha)
        stack.callback(_verify_final, bundle, profile, entries, bundle_pins)
        return BundleResult(_json(manifest).decode("utf-8"), before.document_json, receipt_sha, False)
