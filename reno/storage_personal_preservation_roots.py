"""Pinned, read-only knowledge roots from independent personal packages.

The caller already owns the whole lifecycle group. No facility, producer,
configuration, network or live State is consulted here. Every safe package raw
file and directory inventory remains pinned until the caller's final CAS.
Incomplete/corrupt packages retain known SQL edges plus conservative blockers.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from . import personal_preservation_contract as contract
from . import personal_preservation_files as files
from . import personal_preservation_source as preserved_source
from . import storage_artifacts as artifacts, storage_lifecycle as lifecycle
from . import storage_root_inputs as inputs, storage_checkpoint_roots as checkpoints
from . import sync_media as safe

CATALOG = contract.CATALOG
BUNDLES = contract.DIRECTORY
_NS = tuple(sorted(artifacts.NAMESPACES))
_ROOT_ROLE = {"sync_media": "media", "local_report": "reports", "sync_report": "reports", "checkpoint": "checkpoints"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_CONTROL = re.compile(r"([0-9a-f-]{36})\.(request|selection|source-verified|state|receipt)\.json\Z")
_ENTRY = re.compile(r"f[0-9]{8}\Z")


class PersonalPreservationRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _CallerCancelled(BaseException):
    def __init__(self, original):
        self.original = original


@dataclass(frozen=True)
class PersonalPreservationLimits:
    max_operations: int = 4096
    max_entries: int = 100_000
    max_file_bytes: int = 512 * 1024 * 1024
    max_snapshot_bytes: int = 512 * 1024 * 1024
    max_metadata_file_bytes: int = 32 * 1024 * 1024
    max_metadata_bytes: int = 128 * 1024 * 1024
    deadline_seconds: float = 120.0
    input_limits: inputs.InputLimits = inputs.InputLimits()

    def __post_init__(self):
        for name in ("max_operations", "max_entries", "max_file_bytes", "max_snapshot_bytes",
                     "max_metadata_file_bytes", "max_metadata_bytes"):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 8 * 1024**3:
                raise ValueError("Invalid personal preservation root limits")
        if (self.max_operations > 4096 or self.max_entries > 1_000_000
                or type(self.deadline_seconds) not in (int, float)
                or not math.isfinite(self.deadline_seconds) or not 0 < self.deadline_seconds <= 120
                or not isinstance(self.input_limits, inputs.InputLimits)):
            raise ValueError("Invalid personal preservation root limits")


def _hash(value):
    return hashlib.sha256(inputs.canonical_bytes(value)).hexdigest()


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid canonical operation id")
    return value


def _merge(scans):
    def union(field):
        values = {value for scan in scans for value in getattr(scan, field)}
        return tuple(sorted(values, key=lambda value: inputs.canonical_bytes(asdict(value))))
    return inputs.InputScan(*(union(name) for name in ("references", "selectors", "blockers", "source_bindings")))


def _origin(scan):
    # The existing whole-DB parser has the closed live/backup vocabulary. Its
    # read-only projections are reused; these independent sources are rebound
    # after parsing and never enrolled into the generic backup catalog.
    return inputs.InputScan(*(tuple(replace(value, source_kind="personal_preservation")
                                   for value in getattr(scan, name))
                              for name in ("references", "selectors", "blockers", "source_bindings")))


class _Budget:
    def __init__(self, limits, callback):
        self.limits, self.callback = limits, callback or (lambda: None)
        self.started = time.monotonic()
        self.entries = self.snapshot_bytes = self.metadata_bytes = 0
        self.cancelled = None

    def check(self):
        if self.cancelled is not None:
            raise _CallerCancelled(self.cancelled)
        try:
            self.callback()
        except BaseException as exc:
            self.cancelled = exc
            raise _CallerCancelled(exc) from None
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_DEADLINE_LIMIT")

    def entry(self):
        self.check()
        self.entries += 1
        if self.entries > self.limits.max_entries:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_ENTRY_LIMIT")

    def file(self, hold, *, metadata):
        size = hold.path_identity["byte_size"]
        self.snapshot_bytes += size
        if self.snapshot_bytes > self.limits.max_snapshot_bytes:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_BYTES_LIMIT")
        if metadata:
            self.metadata_bytes += size
            if self.metadata_bytes > self.limits.max_metadata_bytes:
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_METADATA_LIMIT")


class _Tree:
    """Pure no-follow inventory holding every safe directory and raw handle."""
    def __init__(self, profile, budget, stack):
        self.profile, self.budget, self.stack = profile, budget, stack
        self.data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        self.data_identity = files.directory_identity(self.data)
        self.roots, self.directories, self.files, self.issues = {}, {}, {}, []
        for name in (CATALOG, BUNDLES):
            self._root(name)

    def _root(self, name):
        self.budget.check()
        try:
            info = self.data.entry_stat(name)
        except FileNotFoundError:
            self.roots[name] = None
            return
        except (safe.SyncError, OSError):
            self.roots[name] = {"unreadable": True}
            self.issues.append((name, "PERSONAL_PRESERVATION_ROOT_UNREADABLE"))
            return
        self.roots[name] = {"device": str(info.st_dev), "inode": str(info.st_ino), "mode": info.st_mode}
        try:
            safe._safe_stat(info, directory=True)
            directory = self.stack.enter_context(safe._Directory(self.data.path / name, base=self.data.path, parent=self.data))
            files.require_private(directory.path, directory=True)
            self._walk(directory, name)
        except PersonalPreservationRootError:
            raise
        except (safe.SyncError, files.PreservationFileError, OSError):
            self.issues.append((name, "PERSONAL_PRESERVATION_ROOT_UNSAFE"))

    def _walk(self, directory, key):
        self.budget.entry()
        files.require_private(directory.path, directory=True)
        names = tuple(sorted(directory.names()))
        self.directories[key] = (directory, files.directory_identity(directory), names)
        for name in names:
            self.budget.entry()
            relative = key + "/" + name
            try:
                safe._entry_name(name)
                info = directory.entry_stat(name)
                if stat.S_ISDIR(info.st_mode):
                    safe._safe_stat(info, directory=True)
                    child = self.stack.enter_context(safe._Directory(directory.path / name, base=self.data.path, parent=directory))
                    self._walk(child, relative)
                else:
                    safe._safe_stat(info, directory=False)
                    if info.st_nlink != 1:
                        raise files.PreservationFileError("PERSONAL_PRESERVATION_FILE_UNSAFE")
                    files.require_private(directory.path / name, directory=False)
                    metadata = name.endswith(".json") and not relative.startswith(BUNDLES + "/payload/")
                    maximum = self.budget.limits.max_metadata_file_bytes if metadata else self.budget.limits.max_file_bytes
                    hold = self.stack.enter_context(files.FileHold(directory.path / name, maximum=maximum,
                                                                   check_cancelled=self.budget.check, collect=metadata))
                    self.files[relative] = hold
                    self.budget.file(hold, metadata=metadata)
            except PersonalPreservationRootError:
                raise
            except (safe.SyncError, files.PreservationFileError, OSError):
                self.issues.append((relative, "PERSONAL_PRESERVATION_FILE_UNREADABLE"))
        directory.verify()

    def verify(self):
        self.budget.check()
        if files.directory_identity(self.data) != self.data_identity:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True)
        for name, observed in self.roots.items():
            if observed == {"unreadable": True}:
                # No successful reading/eligibility can be certified here.
                continue
            try:
                info = self.data.entry_stat(name)
                current = {"device": str(info.st_dev), "inode": str(info.st_ino), "mode": info.st_mode}
            except FileNotFoundError:
                current = None
            except (safe.SyncError, OSError):
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True) from None
            if current != observed:
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True)
        for directory, identity, names in self.directories.values():
            self.budget.check()
            files.require_private(directory.path, directory=True)
            if files.directory_identity(directory) != identity or tuple(sorted(directory.names())) != names:
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True)
        for hold in self.files.values():
            try:
                files.require_private(hold.path, directory=False)
                hold.verify()
            except (safe.SyncError, files.PreservationFileError, OSError):
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True) from None
        self.data.verify()

    def binding(self):
        return {"roots": self.roots,
                "directories": [[key, identity, _hash(names)] for key, (_, identity, names) in sorted(self.directories.items())],
                "files": [[key, hold.sha256, hold.path_identity, hold.handle_identity]
                          for key, hold in sorted(self.files.items())], "issues": self.issues}


@dataclass
class PersonalPreservationCapture:
    scan: inputs.InputScan
    semantic_bindings: tuple
    protected_paths: tuple[Path, ...]
    protected_directories: tuple[Path, ...]
    snapshot_bytes: int
    file_count: int
    _profile: lifecycle.StorageProfile
    _tree: _Tree
    _budget: _Budget
    _active: bool = True

    @property
    def inputs(self):
        return self.scan

    def verify_source(self):
        if not self._active:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_CAPTURE_CLOSED")
        self._budget.check()
        self._tree.verify()
        lifecycle.require_guard(self._profile).verify()


def _fact(tree, key, validator, **options):
    held = tree.files.get(key)
    if held is None:
        return None
    if held.raw is None:
        raise PersonalPreservationRootError("PERSONAL_PRESERVATION_FACT_UNREADABLE")
    value = validator(contract.decode(held.raw), **options)
    if contract.canonical_bytes(value) != held.raw:
        raise PersonalPreservationRootError("PERSONAL_PRESERVATION_FACT_NONCANONICAL")
    return value


def _safe_code(exc, fallback="PERSONAL_PRESERVATION_PACKAGE_UNPROVEN"):
    code = getattr(exc, "code", None)
    if type(code) is str and re.fullmatch(r"PERSONAL_PRESERVATION_[A-Z0-9_]{1,100}", code):
        return code
    return fallback


def _collector(profile, root_ids, member_id, source_id, limits):
    return inputs._Collector(profile, root_ids, member_id, "backup", source_id, limits.input_limits)


def _attempts(tree, prefix, selection, request, collector):
    attempts = {}
    for key in sorted(tree.files):
        if not key.startswith(prefix + "attempts/"):
            continue
        name = key[len(prefix + "attempts/"):]
        if name.startswith(".preservation-write-") and name.endswith(".tmp") and "/" not in name:
            continue
        try:
            attempt = _fact(tree, key, contract.validate_copy_attempt, selection=selection, request=request)
            expected = attempt["attempt_id"] + "-" + attempt["entry_id"] + ".json"
            if name != expected:
                raise ValueError
            attempts[expected] = attempt
        except (contract.PersonalPreservationContractError, PersonalPreservationRootError,
                ValueError, TypeError, KeyError):
            collector.block(_NS, "PERSONAL_PRESERVATION_ATTEMPT_UNPROVEN", ["attempt", key])
    declared = {attempt["target_relative_key"] for attempt in attempts.values()}
    for key in tree.files:
        if key.startswith(prefix + "payload/") and key[len(prefix):] not in declared:
            collector.block(_NS, "PERSONAL_PRESERVATION_COPY_UNDECLARED", ["payload", key])
    return attempts


def _copy_fact(tree, key, attempts, selection, request, entry_id):
    record = _fact(tree, key, contract.validate_copy_record, selection=selection, request=request)
    if record is None:
        return None
    if record["entry_id"] != entry_id:
        raise PersonalPreservationRootError("PERSONAL_PRESERVATION_COPY_ENTRY_CHANGED")
    attempt = attempts.get(record["attempt_id"] + "-" + entry_id + ".json")
    if attempt is None:
        raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ATTEMPT_UNPROVEN")
    return contract.validate_copy_record(record, selection=selection, attempt=attempt, request=request)


def _copies(tree, prefix, selection, request, collector):
    attempts = _attempts(tree, prefix, selection, request, collector)
    selected = {entry["entry_id"]: entry for entry in selection["files"]}
    index_prefix = CATALOG + "/" + request["operation_id"] + ".copy-index/"
    records, copies = [], {}
    first_complete = True
    for key in sorted(tree.files):
        current_prefix = (index_prefix if key.startswith(index_prefix)
                          else prefix + "copy-records/" if key.startswith(prefix + "copy-records/") else None)
        if current_prefix is None:
            continue
        name = key[len(current_prefix):]
        if name.startswith(".preservation-write-") and name.endswith(".tmp") and "/" not in name:
            continue
        if not name.endswith(".json") or name[:-5] not in selected or "/" in name:
            first_complete = False
            collector.block(_NS, "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_UNDECLARED"
                            if current_prefix == index_prefix else "PERSONAL_PRESERVATION_COPY_UNDECLARED", ["record", key])
    for entry_id, entry in selected.items():
        anchor_key = index_prefix + entry_id + ".json"
        record_key = prefix + "copy-records/" + entry_id + ".json"
        anchor = record = None
        for key, kind in ((anchor_key, "anchor"), (record_key, "record")):
            try:
                value = _copy_fact(tree, key, attempts, selection, request, entry_id)
                if kind == "anchor":
                    anchor = value
                else:
                    record = value
            except (contract.PersonalPreservationContractError, PersonalPreservationRootError,
                    ValueError, TypeError, KeyError) as exc:
                first_complete = False
                collector.block(_NS, _safe_code(exc, "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_UNPROVEN"
                                                if kind == "anchor" else "PERSONAL_PRESERVATION_COPY_UNPROVEN"), [kind, entry_id])
        if anchor is None:
            first_complete = False
            if record is not None:
                collector.block(_NS, "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_MISSING", ["anchor", entry_id])
        if record is None:
            first_complete = False
            if anchor is not None:
                collector.block(_NS, "PERSONAL_PRESERVATION_FIRST_COPY_RECORD_MISSING", ["record", entry_id])
        if anchor is not None and record is not None:
            if (tree.files[anchor_key].raw != tree.files[record_key].raw
                    or tree.files[anchor_key].sha256 != tree.files[record_key].sha256):
                first_complete = False
                collector.block(_NS, "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_CHANGED", ["anchor_record", entry_id])
        # An immutable anchor can prove known raw SQL roots across the legal
        # anchor-before-record gap. A bundle record never replaces that first
        # identity, and missing either fact still fences all four namespaces.
        chosen = anchor if anchor is not None else record
        if chosen is None:
            continue
        try:
            held = tree.files.get(prefix + chosen["target_relative_key"])
            if held is None:
                first_complete = False
                collector.block(_NS, "PERSONAL_PRESERVATION_COPY_MISSING", ["copy", entry["entry_id"]])
                continue
            if (held.sha256 != entry["sha256"] or held.path_identity != chosen["copy_path_identity"]
                    or held.handle_identity != chosen["copy_handle_identity"]
                    or held.directory_identity != chosen["target_parent_identity"]):
                raise PersonalPreservationRootError("PERSONAL_PRESERVATION_COPY_CHANGED")
            records.append(chosen)
            copies[entry["entry_id"]] = held
        except (contract.PersonalPreservationContractError, PersonalPreservationRootError,
                ValueError, TypeError, KeyError) as exc:
            first_complete = False
            collector.block(_NS, _safe_code(exc, "PERSONAL_PRESERVATION_COPY_UNPROVEN"), ["copy", entry_id])
    records.sort(key=lambda item: item["entry_id"])
    if set(copies) != set(selected):
        collector.block(_NS, "PERSONAL_PRESERVATION_COPY_INCOMPLETE", "records")
    return records, copies, first_complete


def _layout(tree, prefix, collector):
    allowed = {"payload", "copy-records", "attempts"}
    for key in tree.directories:
        if not key.startswith(prefix):
            continue
        relative = key[len(prefix):]
        pieces = relative.split("/")
        valid = pieces[0] in allowed and (len(pieces) == 1 or pieces[0] == "payload" and len(pieces) == 2)
        if valid and len(pieces) == 2:
            try:
                _uuid(pieces[1])
            except (ValueError, TypeError):
                valid = False
        if not valid:
            collector.block(_NS, "PERSONAL_PRESERVATION_PACKAGE_UNCLASSIFIED", ["directory", key])
    for key in tree.files:
        if not key.startswith(prefix):
            continue
        relative = key[len(prefix):]
        pieces = relative.split("/")
        if len(pieces) == 1:
            if relative == "manifest.json" or relative.startswith(".preservation-write-") and relative.endswith(".tmp"):
                continue
        elif pieces[0] in {"copy-records", "attempts"} and len(pieces) == 2:
            continue  # Associated closed validators check the concrete names.
        elif pieces[0] == "payload" and len(pieces) == 3:
            continue  # Associated immutable attempt must declare the body.
        collector.block(_NS, "PERSONAL_PRESERVATION_PACKAGE_UNCLASSIFIED", ["file", key])


def _external_sql_roots(connection, projection, collector, limits, check):
    """Keep fixed historical descriptors; never read today's external bodies."""
    budget = checkpoints._Budget(checkpoints.CheckpointLimits(
        max_rows=limits.input_limits.max_rows, max_cell_bytes=limits.input_limits.max_cell_bytes,
        max_sql_bytes=limits.input_limits.max_total_bytes, max_vm_steps=limits.input_limits.max_vm_steps,
        deadline_seconds=limits.input_limits.deadline_seconds), check)
    reader = checkpoints._Reader(connection, budget)
    try:
        for table in ("sync_checkpoint", "sync_recovery_checkpoint"):
            try:
                rows = reader.rows(table)
                collector.binding(table, "present", rows)
                for index, row in enumerate(rows):
                    try:
                        collector.reference("checkpoint", row["storage_key"], [table, index],
                                            row["sha256"], row["byte_size"])
                    except (inputs._Invalid, ValueError, TypeError, KeyError):
                        collector.block(("checkpoint",), "PERSONAL_PRESERVATION_CHECKPOINT_DESCRIPTOR_UNKNOWN", [table, index])
                if rows:
                    collector.block(("checkpoint", "sync_media", "sync_report"),
                                    "PERSONAL_PRESERVATION_CHECKPOINT_DEPENDENCIES_UNKNOWN", table)
            except (checkpoints._Invalid, sqlite3.Error, ValueError, TypeError, KeyError):
                collector.block(("checkpoint", "sync_media", "sync_report"),
                                "PERSONAL_PRESERVATION_CHECKPOINT_PROJECTION_UNKNOWN", table)
        # The main restricted parser already validates these current-schema
        # public projections. Their scalar rows prove unresolved historical
        # dependencies; do not reinterpret private DTO columns or live trees.
        for binding in projection.source_bindings:
            if binding.section in {checkpoints.home_recovery_roots.TABLE, "source_continuity", "source_provenance"}:
                if binding.state == "unknown":
                    collector.block(_NS, "PERSONAL_PRESERVATION_KNOWLEDGE_PROJECTION_UNKNOWN", binding.section)
                elif binding.row_count and binding.section != "source_provenance":
                    collector.block(_NS, "PERSONAL_PRESERVATION_KNOWLEDGE_DEPENDENCIES_UNKNOWN", binding.section)
    finally:
        reader.finish()


def _project_sql(tree, selection, copies, profile, root_ids, member_id, operation_id, collector, limits):
    projection = inputs.InputScan()
    try:
        with preserved_source.payload_image_locked(selection, copies, check_cancelled=tree.budget.check) as image:
            projection = inputs.parse_image(image.connection, profile=profile, root_ids=root_ids,
                member_id=member_id, source_kind="backup", source_id=operation_id, limits=limits.input_limits)
            _external_sql_roots(image.connection, projection, collector, limits, tree.budget.check)
            image.verify()
        return projection
    except (PersonalPreservationRootError, files.PreservationFileError, sqlite3.Error,
            OSError, ValueError, TypeError, KeyError) as exc:
        if getattr(exc, "retriable", False):
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True) from None
        collector.block(_NS, _safe_code(exc, "PERSONAL_PRESERVATION_SQL_PAYLOAD_UNPROVEN"), "sql")
        return projection


def _package(tree, operation_id, profile, root_ids, member_id, source_identity, limits):
    collector = _collector(profile, root_ids, member_id, operation_id, limits)
    scans = []
    base = CATALOG + "/" + operation_id
    request = _fact(tree, base + ".request.json", contract.validate_request)
    if request is None:
        collector.block(_NS, "PERSONAL_PRESERVATION_REQUEST_MISSING", "request")
        return collector.result(), {"source_id": _hash(operation_id), "phase": "unproven"}
    if (request["operation_id"] != operation_id
            or request["source_profile"] != {name: _key(getattr(profile, name))
                for name in ("database", "media", "reports", "checkpoints")}
            or request["source_root_identity"] != tree.data_identity):
        collector.block(_NS, "PERSONAL_PRESERVATION_SOURCE_BINDING_UNKNOWN", "request")
    bundle_id = request["bundle_id"]
    prefix = BUNDLES + "/" + bundle_id + "/"
    bundle = tree.directories.get(BUNDLES + "/" + bundle_id)
    parent = tree.directories.get(BUNDLES)
    if (bundle is None or parent is None or request["target"] != _key(profile.database.parent / BUNDLES / bundle_id)
            or parent[1] != request["target_parent_identity"]):
        collector.block(_NS, "PERSONAL_PRESERVATION_TARGET_UNPROVEN", "target")
    selection = _fact(tree, base + ".selection.json", contract.validate_selection, request=request)
    if selection is None:
        collector.block(_NS, "PERSONAL_PRESERVATION_RESERVED", "selection")
        state = _fact(tree, base + ".state.json", contract.validate_state, request=request)
        phase = state["phase"] if state else "reserved"
        if phase == "failed":
            collector.block(_NS, "PERSONAL_PRESERVATION_FAILED", "state")
        return collector.result(), {"source_id": _hash(operation_id), "bundle_id": bundle_id, "phase": phase}
    # Historical root identities must still bind to this group. A new node or
    # library is uncertainty, never permission to replay a copied sender.
    observed = selection["source_observation"]
    if source_identity is None or any(observed.get(key) != source_identity[key] for key in ("library_id", "node_id")):
        collector.block(_NS, "PERSONAL_PRESERVATION_SOURCE_IDENTITY_UNKNOWN", "identity")
    for label, root in (("DATA", profile.database.parent), ("MEDIA", profile.media),
                        ("REPORTS", profile.reports), ("CHECKPOINTS", profile.checkpoints)):
        info = root.stat(follow_symlinks=False)
        frozen = observed["roots"][label]
        if (frozen["path"] != _key(root) or frozen["device"] != str(info.st_dev)
                or frozen["inode"] != str(info.st_ino)):
            collector.block(_NS, "PERSONAL_PRESERVATION_HISTORICAL_ROOT_UNKNOWN", label)
    _layout(tree, prefix, collector)
    records, copies, first_complete = _copies(tree, prefix, selection, request, collector)
    scans.append(_project_sql(tree, selection, copies, profile, root_ids, member_id,
                              operation_id, collector, limits))
    if any(entry["role"] == "knowledge_state_raw" for entry in selection["files"]):
        collector.block(_NS, "PERSONAL_PRESERVATION_KNOWLEDGE_DEPENDENCIES_UNKNOWN", "knowledge_state")
    collector.block(("local_report",), "PERSONAL_PRESERVATION_REPORT_MANIFESTS_UNKNOWN", "report_manifests")
    verified = manifest = receipt = state = None
    try:
        verified = _fact(tree, base + ".source-verified.json", contract.validate_source_verified,
                         selection=selection, copy_records=records, request=request)
        if verified is None:
            collector.block(_NS, "PERSONAL_PRESERVATION_SOURCE_UNVERIFIED", "source_verified")
        else:
            manifest = _fact(tree, prefix + "manifest.json", contract.validate_manifest,
                selection=selection, source_verified=verified, copy_records=records, request=request)
            if manifest is None:
                collector.block(_NS, "PERSONAL_PRESERVATION_MANIFEST_MISSING", "manifest")
            else:
                receipt = _fact(tree, base + ".receipt.json", contract.validate_receipt,
                    manifest=manifest, selection=selection, source_verified=verified, request=request)
                if receipt is None:
                    collector.block(_NS, "PERSONAL_PRESERVATION_RECEIPT_MISSING", "receipt")
                elif bundle is None or receipt["target_directory_identity"] != bundle[1]:
                    collector.block(_NS, "PERSONAL_PRESERVATION_TARGET_CHANGED", "receipt")
        state = _fact(tree, base + ".state.json", contract.validate_state, request=request,
                      selection=selection, source_verified=verified, manifest=manifest, receipt=receipt)
        if state is None or state["phase"] != "ready":
            collector.block(_NS, "PERSONAL_PRESERVATION_FAILED" if state and state["phase"] == "failed"
                            else "PERSONAL_PRESERVATION_STATE_INCOMPLETE", "state")
    except (contract.PersonalPreservationContractError, PersonalPreservationRootError,
            ValueError, TypeError, KeyError) as exc:
        collector.block(_NS, _safe_code(exc), "terminal")
    collector.binding("package", "ready" if first_complete and receipt is not None and state and state["phase"] == "ready" else "incomplete",
                      [tree.files[base + ".request.json"].sha256,
                       tree.files[base + ".selection.json"].sha256,
                       None if manifest is None else contract.digest(manifest),
                       None if receipt is None else contract.digest(receipt)])
    scans.append(collector.result())
    return _merge(scans), {"source_id": _hash(operation_id), "bundle_id": bundle_id,
                          "phase": state["phase"] if state else "incomplete",
                          "first_copy_complete": first_complete,
                          "selection_sha256": contract.digest(selection)}


@contextmanager
def capture_locked(profile, *, root_ids, member_id, source_identity, limits=None, check_cancelled=None):
    """Hold all observations in the caller's group ExitStack until final CAS."""
    guard = lifecycle.require_guard(profile)
    limits = PersonalPreservationLimits() if limits is None else limits
    if (not isinstance(limits, PersonalPreservationLimits) or type(root_ids) is not dict
            or set(root_ids) != set(_NS) or any(type(value) is not str or not _SHA.fullmatch(value) for value in root_ids.values())
            or type(member_id) is not str or not _TOKEN.fullmatch(member_id)
            or check_cancelled is not None and not callable(check_cancelled)):
        raise ValueError("Invalid personal preservation root options")
    if source_identity is not None:
        if type(source_identity) is not dict or set(source_identity) != {"library_id", "node_id"}:
            raise ValueError("Invalid personal preservation source identity")
        for value in source_identity.values():
            _uuid(value)
    for namespace in _NS:
        if artifacts.root_identity(getattr(profile, _ROOT_ROLE[namespace]), profile=profile)["root_id"] != root_ids[namespace]:
            raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_BINDING_MISMATCH")
    budget = _Budget(limits, check_cancelled)
    result = None
    try:
        with ExitStack() as stack:
            tree = _Tree(profile, budget, stack)
            collector = _collector(profile, root_ids, member_id, "discovery", limits)
            collector.binding("limits", "present", [asdict(limits)])
            collector.binding("catalog", "present" if tree.roots[CATALOG] else "absent", [_hash(tree.binding())])
            scans, semantics = [], []
            for locator, code in tree.issues:
                collector.block(_NS, code, locator)
            operations = set()
            for key in tree.directories:
                if not key.startswith(CATALOG + "/"):
                    continue
                relative = key[len(CATALOG + "/"):]
                try:
                    suffix = next((ending for ending in (".failures", ".copy-index") if relative.endswith(ending)), None)
                    if "/" in relative or suffix is None:
                        raise ValueError
                    operations.add(_uuid(relative[:-len(suffix)]))
                except (ValueError, TypeError):
                    collector.block(_NS, "PERSONAL_PRESERVATION_CATALOG_UNCLASSIFIED", ["catalog_directory", key])
            for key in tree.files:
                if not key.startswith(CATALOG + "/"):
                    continue
                name = key[len(CATALOG + "/"):]
                match = _CONTROL.fullmatch(name)
                try:
                    if match:
                        operations.add(_uuid(match[1]))
                    elif "/" in name and name.split("/", 1)[0].endswith(".failures"):
                        operations.add(_uuid(name.split("/", 1)[0][:-9]))
                    elif "/" in name and name.split("/", 1)[0].endswith(".copy-index"):
                        parent, child = name.split("/", 1)
                        operation_id = _uuid(parent[:-11])
                        if not child.endswith(".json") or not _ENTRY.fullmatch(child[:-5]):
                            raise ValueError
                        operations.add(operation_id)
                    else:
                        raise ValueError
                except (ValueError, TypeError):
                    collector.block(_NS, "PERSONAL_PRESERVATION_CATALOG_UNCLASSIFIED", ["catalog", key])
            if len(operations) > limits.max_operations:
                collector.block(_NS, "PERSONAL_PRESERVATION_ROOT_OPERATION_LIMIT", "operations")
            for operation_id in sorted(operations)[:limits.max_operations]:
                budget.check()
                try:
                    projection, semantic = _package(tree, operation_id, profile, root_ids,
                                                    member_id, source_identity, limits)
                    scans.append(projection)
                    semantics.append(semantic)
                except (contract.PersonalPreservationContractError, PersonalPreservationRootError,
                        files.PreservationFileError, safe.SyncError, sqlite3.Error, OSError,
                        ValueError, TypeError, KeyError) as exc:
                    if getattr(exc, "retriable", False):
                        raise PersonalPreservationRootError("PERSONAL_PRESERVATION_ROOT_SOURCE_CHANGED", retriable=True) from None
                    collector.block(_NS, _safe_code(exc), ["operation", operation_id])
            registered = {semantic.get("bundle_id") for semantic in semantics}
            for key in tree.directories:
                if key.startswith(BUNDLES + "/") and "/" not in key[len(BUNDLES + "/"):] and key[len(BUNDLES + "/"):] not in registered:
                    collector.block(_NS, "PERSONAL_PRESERVATION_ORPHAN_BUNDLE", ["bundle", key])
            for key in tree.files:
                if key.startswith(BUNDLES + "/") and "/" not in key[len(BUNDLES + "/"):]:
                    collector.block(_NS, "PERSONAL_PRESERVATION_ORPHAN_BUNDLE", ["bundle_file", key])
            scans.append(collector.result())
            result = PersonalPreservationCapture(_origin(_merge(scans)), tuple(semantics),
                tuple(hold.path for hold in tree.files.values()),
                (profile.database.parent / CATALOG, profile.database.parent / BUNDLES),
                budget.snapshot_bytes, len(tree.files), profile, tree, budget)
            yield result
            result.verify_source()
            guard.verify()
    except _CallerCancelled as exc:
        raise exc.original from None
    finally:
        if result is not None:
            result._active = False


def scan_locked(profile, **options):
    with capture_locked(profile, **options) as captured:
        return captured.scan
