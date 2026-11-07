"""Read-only original map/outbox dependencies of actual pending State capsules.

This observer never creates maps, outboxes, State, SQL or execution authority.
It selects immutable files only from an already held PendingStateCapture's
complete original before/after images. Missing inputs remain scoped unknown
with actual absence pins; no live State or alternate generation fills a gap.
The private Home2 bundle readers use the three exact roles below to preserve
these original dependencies. Historical Home1 remains uncovered. The copied
parser consumes only its caller's already verified original file map; its
facts grant no writer, paid, network or completed-operation capability.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import stat
import threading
import time
import uuid

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
from . import storage_source as source, storage_legacy_pending_state17 as pending
from . import storage_home_roots as home_roots, sync_media as safe, sync_sources, sync_state
from .sync_protocol import SyncError

FORMAT_VERSION = 1
KIND = "legacy_pending_dependencies"
ROLE_MAPPING_V1 = "legacy_pending_source_map_v1"
ROLE_MAPPING_V2 = "legacy_pending_source_map_v2"
ROLE_OUTBOX = "legacy_pending_outbox"
ROLES = frozenset({ROLE_MAPPING_V1, ROLE_MAPPING_V2, ROLE_OUTBOX})
MAPPING_ROLES = frozenset({ROLE_MAPPING_V1, ROLE_MAPPING_V2})
MAX_FILES = 3 * pending.MAX_FILES
MAX_REQUIREMENTS = 6 * pending.MAX_FILES
MAX_TOTAL_BYTES = 4 * pending.MAX_TOTAL_BYTES
MAX_STATE_TOTAL_BYTES = 2 * pending.MAX_TOTAL_BYTES
SUMMARY_FIELDS = frozenset({"format_version", "kind", "owner_profile_sha256", "coverage",
    "capsule_summary_sha256", "capsule_state_set_sha256", "reference_set_sha256",
    "selected_key_set_sha256", "file_set_sha256", "absence_set_sha256", "unknown_sha256",
    "reference_count", "selected_count", "file_count", "absence_count", "total_bytes", "unknown_count"})
_SEAL = object()
_CURRENT = ContextVar("reno_pending_dependencies17_capture", default=())
_CONTEXT = ContextVar("reno_pending_dependencies17_context", default=())


class PendingDependencyError(ValueError):
    def __init__(self, code, *, retriable=False):
        self.code, self.retriable = "LEGACY_PENDING_DEPENDENCY17_" + code, retriable
        super().__init__(self.code)


class _Cancelled(BaseException):
    def __init__(self, error):
        self.error = error


def _fail(code, *, retriable=False):
    raise PendingDependencyError(code, retriable=retriable) from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(_json(value))


@dataclass(frozen=True)
class CaptureLimits:
    max_files: int = MAX_FILES
    max_requirements: int = MAX_REQUIREMENTS
    max_mapping_bytes: int = sync_sources.MAX_RECORD_BYTES
    max_outbox_bytes: int = sync_state.MAX_OUTBOX_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES
    max_state_total_bytes: int = MAX_STATE_TOTAL_BYTES
    deadline_seconds: float = 10.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError("Invalid pending dependency limit")
            elif (type(value) is not int or value < 1
                    or value > type(self).__dataclass_fields__[name].default):
                raise ValueError("Invalid pending dependency limit")


def role_for_key(key):
    """Recognize only original map instances and original outbox UUID paths."""
    if type(key) is not str or "\\" in key or ":" in key:
        return None
    parts = PurePosixPath(key).parts
    if not parts or "/".join(parts) != key or any(part in {".", ".."} for part in parts):
        return None
    name = parts[-1]
    if not name.endswith(".json"):
        return None
    identifier = name[:-5]
    if parts[:2] == ("sync-sources", "v1") and len(parts) == 3:
        return ROLE_MAPPING_V1 if sync_state._SHA.fullmatch(identifier) else None
    if not ((parts[:2] == ("sync-sources", "v2") and len(parts) == 3)
            or parts[:1] == (sync_state._OUTBOX,) and len(parts) == 2):
        return None
    try:
        if str(uuid.UUID(identifier)) != identifier:
            return None
    except (ValueError, AttributeError):
        return None
    return ROLE_MAPPING_V2 if parts[0] == "sync-sources" else ROLE_OUTBOX


def file_limit(role):
    if role in MAPPING_ROLES:
        return sync_sources.MAX_RECORD_BYTES
    if role == ROLE_OUTBOX:
        return sync_state.MAX_OUTBOX_BYTES
    _fail("ROLE_INVALID")


@dataclass(frozen=True)
class CapturedDependency:
    relative_key: str
    role: str
    raw: bytes
    sha256: str
    byte_size: int

    @property
    def name(self):
        return PurePosixPath(self.relative_key).name

    def descriptor(self):
        return {"root_kind": "DATA", "relative_key": self.relative_key, "role": self.role,
            "sha256": self.sha256, "byte_size": self.byte_size}


@dataclass(frozen=True)
class DependencyRequirement:
    request_id: str
    phase: str
    state_sha256: str
    relative_key: str
    role: str
    expectation_json: str

    @property
    def expectation(self):
        """Private original facts, including saved inputs; never public State."""
        return json.loads(self.expectation_json)

    def public(self):
        return {"request_id": self.request_id, "phase": self.phase,
            "state_sha256": self.state_sha256, "root_kind": "DATA",
            "relative_key": self.relative_key, "role": self.role,
            "expectation_sha256": _sha(self.expectation_json.encode("utf-8"))}


def _issue(requirement, code):
    return {"request_id": requirement.request_id, "phase": requirement.phase,
        "role": requirement.role, "relative_key": requirement.relative_key, "code": code}


def _requirements_from_images(images, *, limits, check):
    """Pure data derivation; only the actual factory below admits its source."""
    if type(images) is not tuple or len(images) > 2 * pending.MAX_FILES:
        _fail("STATE_COUNT_LIMIT")
    requirements, unknown, seen, total = {}, [], set(), 0
    for image in images:
        check()
        try:
            if (type(image) is not dict or set(image) != {"request_id", "phase", "state_json", "state_sha256"}
                    or image["phase"] not in {"before", "after"} or type(image["state_json"]) is not str):
                _fail("STATE_INVALID")
            sync_state._uuid(image["request_id"])
            raw = image["state_json"].encode("utf-8")
            total += len(raw)
            if len(raw) > sync_state.MAX_STATE_BYTES or total > limits.max_state_total_bytes:
                _fail("STATE_BYTES_LIMIT")
            if _sha(raw) != image["state_sha256"]:
                _fail("STATE_INVALID")
            state = sync_state._decode(raw)
            if state["format_version"] != 5 or sync_state._validate(state) != raw:
                _fail("STATE_INVALID")
            identity = image["request_id"], image["phase"]
            if identity in seen:
                _fail("STATE_INVALID")
            seen.add(identity)
            binding = state["binding"]
            common = {"source_library_id": binding["home_library_id"],
                "source_node_id": binding["home_node_id"]}
            work = state["work"]
            selected = []
            if work is not None and work.get("source_mapping_id") is not None:
                generation = work.get("source_mapping_generation")
                key = ("sync-sources/v1/" + work["source_mapping_id"] + ".json" if generation is None
                    else "sync-sources/v2/" + generation + ".json")
                selected.append((key, {**common, "field": "work", "mapping_id": work["source_mapping_id"],
                    "mapping_generation": generation, "video_id": work["video_id"],
                    "inputs": deepcopy(work.get("source_mapping_inputs"))}))
            elif work is not None and work.get("source_mapping_generation") is not None:
                unknown.append({"request_id": image["request_id"], "phase": image["phase"],
                    "code": "MAPPING_ID_UNPROVEN"})
            for field in ("pending_publication", "recovery_attempt"):
                original = state[field]
                if original is not None:
                    selected.append((sync_state._OUTBOX + "/" + original["publication_id"] + ".json",
                        {**common, "field": field, "original": deepcopy(original),
                            "legacy_pending_stream": deepcopy(state["legacy_pending_stream"])}))
            for key, expectation in selected:
                role = role_for_key(key)
                if role is None:
                    _fail("STATE_INVALID")
                requirement = DependencyRequirement(image["request_id"], image["phase"], image["state_sha256"],
                    key, role, _json(expectation).decode("utf-8"))
                requirements[_digest(requirement.public())] = requirement
                if len(requirements) > limits.max_requirements:
                    _fail("REQUIREMENT_COUNT_LIMIT")
        except PendingDependencyError as error:
            if error.code.endswith("_LIMIT"):
                raise
            unknown.append({"code": "STATE_UNPROVEN"})
        except (SyncError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
            unknown.append({"code": "STATE_UNPROVEN"})
        check()
    return tuple(requirements[key] for key in sorted(requirements)), unknown


def validate_dependency(record, requirement):
    """Pure original-byte comparison; this grants no physical/writer capability."""
    if (type(record) is not CapturedDependency or type(requirement) is not DependencyRequirement
            or record.relative_key != requirement.relative_key or record.role != requirement.role
            or role_for_key(record.relative_key) != record.role or type(record.raw) is not bytes
            or record.byte_size != len(record.raw) or record.byte_size > file_limit(record.role)
            or record.sha256 != _sha(record.raw)):
        _fail("RECORD_INVALID")
    expected, value = requirement.expectation, sync_state._decode(record.raw)
    if record.role in MAPPING_ROLES:
        if (type(expected) is not dict or set(expected) != {"source_library_id", "source_node_id", "field",
                "mapping_id", "mapping_generation", "video_id", "inputs"} or expected["field"] != "work"):
            _fail("RECORD_INVALID")
        generation = expected["mapping_generation"]
        original_key = ("sync-sources/v1/" + expected["mapping_id"] + ".json" if generation is None
            else "sync-sources/v2/" + generation + ".json")
        if original_key != record.relative_key:
            _fail("RECORD_INVALID")
        origin = sync_sources.validate_source_record(value, mapping_id=expected["mapping_id"],
            mapping_generation=expected["mapping_generation"], source_library_id=expected["source_library_id"],
            source_node_id=expected["source_node_id"])
        if origin["video_id"] != expected["video_id"]:
            _fail("MAPPING_UNPROVEN")
        saved = expected["inputs"]
        if saved is not None and (value["resolution"] != saved["resolution"]
                or origin["extracted_video_id"] != saved["extracted_video_id"]
                or origin["media_sha256"] != saved["media_sha256"]
                or origin["recorded_at"] != saved["recorded_at"]):
            _fail("MAPPING_UNPROVEN")
        return value
    if (type(expected) is not dict or set(expected) != {"source_library_id", "source_node_id", "field",
            "original", "legacy_pending_stream"}
            or record.relative_key != sync_state._OUTBOX + "/" + expected["original"]["publication_id"] + ".json"):
        _fail("RECORD_INVALID")
    identity = {"library_id": expected["source_library_id"], "node_id": expected["source_node_id"]}
    envelope = home_roots._outbox(value, record.name, identity)
    original = expected["original"]
    if expected["field"] == "pending_publication":
        if (record.sha256, record.byte_size) != (original["sha256"], original["byte_size"]):
            _fail("OUTBOX_UNPROVEN")
    elif expected["field"] == "recovery_attempt":
        if (envelope["snapshot"]["version"], envelope["recovery_generation"],
                envelope["recovery_stream"] or expected["legacy_pending_stream"]) != (
                original["version"], original["generation"], original["stream"]):
            _fail("OUTBOX_UNPROVEN")
    else:
        _fail("RECORD_INVALID")
    return envelope


def copied_dependencies(profile, *, home_format_version, state_images, copies,
                        entries, checkpoint_outboxes=(), limits=None, check_cancelled=None):
    """Validate private dependencies from an original verified copied map.

    A required key omitted by an older Home2 selection remains scoped unknown.
    A declared dependency is different: missing bytes, wrong roles/metadata or
    a mismatched original body reject the selection. A checkpoint role aliases
    an outbox only when the original certificate names that exact SHA/size.
    No path is opened, and no live State or newer generation supplies a gap.
    """
    profile = lifecycle._profile(profile)
    limits = CaptureLimits(deadline_seconds=120) if limits is None else limits
    if (type(home_format_version) is not int or home_format_version not in {1, 2}
            or type(state_images) is not tuple or type(copies) is not dict
            or type(entries) not in {list, tuple} or type(checkpoint_outboxes) not in {list, tuple}
            or type(limits) is not CaptureLimits
            or check_cancelled is not None and not callable(check_cancelled)):
        _fail("COPIED_INPUT_INVALID")
    started = time.monotonic()

    def check():
        if check_cancelled is not None:
            check_cancelled()
        if time.monotonic() - started > limits.deadline_seconds:
            _fail("COPIED_DEADLINE_LIMIT")

    checkpoint = {}
    if len(checkpoint_outboxes) > 2:
        _fail("COPIED_CHECKPOINT_INVALID")
    for item in checkpoint_outboxes:
        check()
        if (type(item) is not dict or set(item) != {"publication_id", "sha256", "byte_size", "semantic_sha256"}
                or type(item["publication_id"]) is not str
                or type(item["byte_size"]) is not int or not 2 <= item["byte_size"] <= sync_state.MAX_OUTBOX_BYTES
                or any(type(item[field]) is not str or not sync_state._SHA.fullmatch(item[field])
                    for field in ("sha256", "semantic_sha256"))):
            _fail("COPIED_CHECKPOINT_INVALID")
        key = sync_state._OUTBOX + "/" + item["publication_id"] + ".json"
        if role_for_key(key) != ROLE_OUTBOX or key in checkpoint:
            _fail("COPIED_CHECKPOINT_INVALID")
        checkpoint[key] = item
    declared, declared_checkpoint, extra = {}, {}, {}
    fields = {"root_kind", "relative_key", "role", "sha256", "byte_size"}
    for entry in entries:
        check()
        if (type(entry) is not dict or set(entry) != fields
                or type(entry["root_kind"]) is not str or type(entry["relative_key"]) is not str
                or type(entry["role"]) is not str):
            _fail("COPIED_SELECTION_INVALID")
        identity = entry["root_kind"], entry["relative_key"]
        if identity in declared:
            _fail("COPIED_SELECTION_INVALID")
        declared[identity] = entry
        if entry["role"] not in ROLES and entry["role"] != "checkpoint_outbox":
            continue
        key, logical_role = entry["relative_key"], role_for_key(entry["relative_key"])
        if (entry["root_kind"] != "DATA" or logical_role is None
                or type(entry["sha256"]) is not str or not sync_state._SHA.fullmatch(entry["sha256"])
                or type(entry["byte_size"]) is not int or not 0 <= entry["byte_size"] <= file_limit(logical_role)):
            _fail("COPIED_SELECTION_INVALID")
        if entry["role"] == "checkpoint_outbox":
            item = checkpoint.get(key)
            if (logical_role != ROLE_OUTBOX or item is None
                    or (entry["sha256"], entry["byte_size"]) != (item["sha256"], item["byte_size"])):
                _fail("COPIED_CHECKPOINT_INVALID")
            declared_checkpoint[key] = entry
        elif entry["role"] != logical_role:
            _fail("COPIED_ROLE_INVALID")
        else:
            extra[key] = entry
    if set(declared_checkpoint) != set(checkpoint):
        _fail("COPIED_CHECKPOINT_INVALID")
    # This pure boundary also rejects injected related copies whose paths were
    # never part of the original manifest. Unrelated generic bundle families
    # retain their owning parser's contract. Only a genuine named checkpoint
    # may independently select an outbox not referenced by capsule images.
    for identity, raw in copies.items():
        check()
        if (type(identity) is not tuple or len(identity) != 2 or identity[0] != "DATA"
                or role_for_key(identity[1]) is None):
            continue
        entry = declared.get(identity)
        if entry is None:
            _fail("COPIED_UNDECLARED_FILE")
        logical_role = role_for_key(identity[1])
        if (entry["role"] != logical_role and not (
                logical_role == ROLE_OUTBOX and entry["role"] == "checkpoint_outbox"
                and identity[1] in checkpoint)):
            _fail("COPIED_ROLE_INVALID")
        if (type(raw) is not bytes or len(raw) != entry["byte_size"]
                or _sha(raw) != entry["sha256"]):
            _fail("COPIED_FILE_CHANGED")
    if home_format_version == 1:
        if extra or state_images:
            _fail("COPIED_HOME1_UNCOVERED")
        return (), (), ()
    requirements, issues = _requirements_from_images(state_images, limits=limits, check=check)
    required_keys = {requirement.relative_key for requirement in requirements}
    if len(required_keys) > limits.max_files:
        _fail("COPIED_FILE_COUNT_LIMIT")
    if set(extra) - required_keys:
        _fail("COPIED_EXTRA_SELECTION")
    records, total = {}, 0
    for key in sorted(required_keys):
        check()
        identity = "DATA", key
        entry = declared.get(identity)
        raw = copies.get(identity)
        if entry is None:
            if identity in copies:
                _fail("COPIED_UNDECLARED_FILE")
            continue
        logical_role = role_for_key(key)
        if (entry["role"] != logical_role and not (
                logical_role == ROLE_OUTBOX and entry["role"] == "checkpoint_outbox" and key in checkpoint)):
            _fail("COPIED_ROLE_INVALID")
        if type(raw) is not bytes:
            _fail("COPIED_FILE_MISSING")
        maximum = limits.max_mapping_bytes if logical_role in MAPPING_ROLES else limits.max_outbox_bytes
        if len(raw) > maximum:
            _fail("COPIED_FILE_BYTES_LIMIT")
        total += len(raw)
        if total > limits.max_total_bytes:
            _fail("COPIED_TOTAL_BYTES_LIMIT")
        digest = _sha(raw)
        if (len(raw), digest) != (entry["byte_size"], entry["sha256"]):
            _fail("COPIED_FILE_CHANGED")
        records[key] = CapturedDependency(key, logical_role, raw, digest, len(raw))
        check()
    for requirement in requirements:
        check()
        record = records.get(requirement.relative_key)
        if record is None:
            issues.append(_issue(requirement, "COPIED_DEPENDENCY_MISSING"))
            continue
        try:
            value = validate_dependency(record, requirement)
        except (PendingDependencyError, SyncError, ValueError, TypeError, KeyError, AttributeError,
                UnicodeError, OverflowError, RecursionError):
            _fail("COPIED_DEPENDENCY_INVALID")
        if (requirement.relative_key in checkpoint
                and _digest(value) != checkpoint[requirement.relative_key]["semantic_sha256"]):
            _fail("COPIED_CHECKPOINT_INVALID")
        check()
    unknown = tuple(json.loads(raw) for raw in sorted({_json(issue) for issue in issues}))
    return requirements, tuple(records[key] for key in sorted(records)), unknown


def _entry_signature(info):
    # Unsafe entries remain observations only, never usable file descriptors.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        info.st_mode, info.st_nlink, getattr(info, "st_uid", None), getattr(info, "st_file_attributes", None))


class _Reader:
    def __init__(self, profile, captured, stack, guard, limits, callback):
        self.profile, self.pending, self.stack, self.guard = profile, captured, stack, guard
        self.limits, self.callback, self.started = limits, callback, time.monotonic()
        self.pending_reader = captured._reader
        self.pending_cache = (captured._projection_json, captured._summary_json, captured._state_images_json)
        self.binding = (profile, profile.identity, captured, captured._reader, guard)
        self.files, self.directories, self.absences, self.uncertain = [], [], [], []
        self.records, self.requirements, self.unknown = [], (), []
        self.cache, self.directory_cache, self.directory_errors = {}, {}, {}
        self.selected_count, self.total = 0, 0

    def fixed_pending(self):
        if (self.profile is not self.binding[0] or self.profile.identity != self.binding[1]
                or self.pending is not self.binding[2] or self.pending_reader is not self.binding[3]
                or self.guard is not self.binding[4]
                or type(self.pending) is not pending.PendingStateCapture or not self.pending._active
                or type(self.pending_reader) is not pending._Reader or self.pending._reader is not self.pending_reader
                or self.pending_reader.profile.identity != self.profile.identity
                or self.pending_reader.guard is not self.guard
                or (self.pending._projection_json, self.pending._summary_json,
                    self.pending._state_images_json) != self.pending_cache
                or lifecycle.require_guard(self.profile) is not self.guard):
            _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")

    def check(self, *, short=False):
        if not short and sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.fixed_pending()
        self.guard.verify()
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as error:
                raise _Cancelled(error) from None
        self.fixed_pending()
        self.guard.verify()
        if not short and sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            _fail("DEADLINE_LIMIT")

    def _absence(self, directory, name):
        if not any(parent.path == directory.path and observed == name for parent, observed in self.absences):
            self.absences.append((directory, name))

    def _uncertain(self, directory, name):
        try:
            observed = _entry_signature(directory.entry_stat(name))
        except FileNotFoundError:
            self._absence(directory, name)
            return
        if not any(parent.path == directory.path and key == name for parent, key, _sig in self.uncertain):
            self.uncertain.append((directory, name, observed))

    def directory(self, parts):
        if parts in self.directory_cache:
            return self.directory_cache[parts]
        parent = self.directory(parts[:-1])
        if parent is None:
            self.directory_cache[parts] = None
            return None
        self.check()
        try:
            directory = self.stack.enter_context(safe._Directory(parent.path / parts[-1], base=self.profile.database.parent))
        except FileNotFoundError:
            self._absence(parent, parts[-1])
            self.directory_errors[parts] = "DEPENDENCY_MISSING"
            directory = None
        except (safe.SyncError, OSError):
            self._uncertain(parent, parts[-1])
            self.directory_errors[parts] = "DIRECTORY_UNPROVEN"
            directory = None
        if directory is not None:
            self.directories.append(directory)
        self.directory_cache[parts] = directory
        self.check()
        return directory

    def read(self, key, role):
        if key in self.cache:
            return self.cache[key]
        if role_for_key(key) != role:
            _fail("ROLE_INVALID")
        self.selected_count += 1
        if self.selected_count > self.limits.max_files:
            _fail("FILE_COUNT_LIMIT")
        parts = PurePosixPath(key).parts
        directory = self.directory(parts[:-1])
        if directory is None:
            code = next((self.directory_errors[prefix] for index in range(1, len(parts))
                if (prefix := parts[:index]) in self.directory_errors), "DIRECTORY_UNPROVEN")
            self.cache[key] = (None, code)
            return self.cache[key]
        name = parts[-1]
        self.check()
        try:
            before_info = directory.entry_stat(name)
        except FileNotFoundError:
            self._absence(directory, name)
            self.cache[key] = (None, "DEPENDENCY_MISSING")
            return self.cache[key]
        try:
            before = source.file_signature(before_info)
            if os.name != "nt" and (before_info.st_uid != os.geteuid() or stat.S_IMODE(before_info.st_mode) & 0o077):
                _fail("FILE_UNPROVEN")
            maximum = self.limits.max_mapping_bytes if role in MAPPING_ROLES else self.limits.max_outbox_bytes
            if before[2] > maximum:
                _fail("FILE_UNPROVEN")
            self.total += before[2]
            if self.total > self.limits.max_total_bytes:
                _fail("TOTAL_BYTES_LIMIT")
            held = self.stack.enter_context(directory.open_file(name))
            opened = source.file_signature(os.fstat(held.fileno()))
            if before[:4] != opened[:4]:
                _fail("SOURCE_CHANGED", retriable=True)
            raw = self._read(held, before[2])
            digest = hashlib.sha256(raw).digest()
            if (source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("SOURCE_CHANGED", retriable=True)
            self.files.append((directory, name, held, before, opened, digest))
            record = CapturedDependency(key, role, raw, digest.hex(), len(raw))
            self.records.append(record)
            self.cache[key] = (record, None)
        except PendingDependencyError as error:
            if error.retriable or error.code.endswith("_LIMIT"):
                raise
            self._uncertain(directory, name)
            self.cache[key] = (None, "FILE_UNPROVEN")
        except (source.SourceReadError, safe.SyncError, OSError) as error:
            if (isinstance(error, FileNotFoundError)
                    or isinstance(error, source.SourceReadError) and error.code == "SOURCE_CHANGED"):
                _fail("SOURCE_CHANGED", retriable=True)
            self._uncertain(directory, name)
            self.cache[key] = (None, "FILE_UNPROVEN")
        self.check()
        return self.cache[key]

    def _read(self, held, size):
        os.lseek(held.fileno(), 0, os.SEEK_SET)
        raw, remaining = bytearray(), size
        while remaining:
            self.check()
            part = os.read(held.fileno(), min(128 * 1024, remaining))
            if not part:
                _fail("SOURCE_CHANGED", retriable=True)
            raw.extend(part)
            remaining -= len(part)
        if os.read(held.fileno(), 1):
            _fail("SOURCE_CHANGED", retriable=True)
        return bytes(raw)

    def acquire(self):
        self.check()
        self.pending.verify_source()
        self.check()
        # Bind private images to the actual held raw family, rather than to a
        # detached or altered projection object carrying copied State data.
        originals = tuple(self.pending_reader.records)
        pinned = {name: (directory, before, opened, digest) for directory, name, _held,
            before, opened, digest in self.pending_reader.files}
        if len(pinned) != len(originals):
            _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")
        for record in originals:
            self.check()
            observed = pinned.get(record.name)
            if (type(record) is not pending.CapturedFile or observed is None
                    or observed[0].path != self.profile.database.parent / pending.DIRECTORY
                    or (record.sha256, record.byte_size) != (observed[3].hex(), observed[1][2])
                    or record.sha256 != _sha(record.raw) or len(record.raw) != record.byte_size):
                _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")
        projection, images = pending._project(self.profile, presence=self.pending_reader.presence,
            records=originals, limits=self.pending_reader.limits)
        if self.pending_cache != (_json(projection), _json(projection["summary"]), _json(images)):
            _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")
        self.capsule_summary = projection["summary"]
        if projection["unknown"]:
            self.unknown.append({"code": "CAPSULE_COMPONENT_UNPROVEN"})
        self.requirements, issues = _requirements_from_images(tuple(images), limits=self.limits, check=self.check)
        self.unknown.extend(issues)
        data = self.stack.enter_context(safe._Directory(self.profile.database.parent, base=self.profile.database.parent))
        self.directories.append(data)
        self.directory_cache[()] = data
        for requirement in self.requirements:
            self.check()
            record, code = self.read(requirement.relative_key, requirement.role)
            if code is not None:
                self.unknown.append(_issue(requirement, code))
                continue
            try:
                validate_dependency(record, requirement)
            except (PendingDependencyError, SyncError, ValueError, TypeError, KeyError, AttributeError,
                    UnicodeError, OverflowError, RecursionError):
                self.unknown.append(_issue(requirement,
                    "MAPPING_UNPROVEN" if requirement.role in MAPPING_ROLES else "OUTBOX_UNPROVEN"))
            self.check()
        self.records.sort(key=lambda record: record.relative_key)
        self.unknown = [json.loads(encoded) for encoded in sorted({_json(row) for row in self.unknown})]
        self.verify()

    def verify(self, *, full=True):
        self.check(short=not full)
        self.pending.verify_source() if full else self.pending.verify_for_commit()
        self.check(short=not full)
        for directory in self.directories:
            directory.verify()
        for directory, name in self.absences:
            self.check(short=not full)
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("SOURCE_CHANGED", retriable=True)
        for directory, name, observed in self.uncertain:
            self.check(short=not full)
            try:
                current = _entry_signature(directory.entry_stat(name))
            except FileNotFoundError:
                _fail("SOURCE_CHANGED", retriable=True)
            if current != observed:
                _fail("SOURCE_CHANGED", retriable=True)
        for directory, name, held, before, opened, digest in self.files:
            self.check(short=not full)
            try:
                if (source.file_signature(directory.entry_stat(name)) != before
                        or source.file_signature(os.fstat(held.fileno())) != opened):
                    _fail("SOURCE_CHANGED", retriable=True)
                if full and hashlib.sha256(self._read(held, before[2])).digest() != digest:
                    _fail("SOURCE_CHANGED", retriable=True)
                if (source.file_signature(directory.entry_stat(name)) != before
                        or source.file_signature(os.fstat(held.fileno())) != opened):
                    _fail("SOURCE_CHANGED", retriable=True)
            except (source.SourceReadError, safe.SyncError, OSError):
                _fail("SOURCE_CHANGED", retriable=True)
        self.check(short=not full)


class PendingDependenciesCapture:
    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def _initialize(self, reader):
        self._reader, self._seal, self._active = reader, _SEAL, True
        self._thread, self._context_token = threading.get_ident(), None
        files = [record.descriptor() for record in reader.records]
        references = [requirement.public() for requirement in reader.requirements]
        selected = sorted({requirement.relative_key for requirement in reader.requirements})
        absences = sorted(({"root_kind": "DATA", "relative_key": (directory.path / name)
            .relative_to(reader.profile.database.parent).as_posix()} for directory, name in reader.absences), key=_json)
        summary = {"format_version": FORMAT_VERSION, "kind": KIND,
            "owner_profile_sha256": reader.capsule_summary["owner_profile_sha256"],
            "coverage": "unknown" if reader.unknown else "complete",
            "capsule_summary_sha256": _digest(reader.capsule_summary),
            "capsule_state_set_sha256": reader.capsule_summary["state_set_sha256"],
            "reference_set_sha256": _digest(references), "selected_key_set_sha256": _digest(selected),
            "file_set_sha256": _digest(files), "absence_set_sha256": _digest(absences),
            "unknown_sha256": _digest(reader.unknown), "reference_count": len(references),
            "selected_count": len(selected), "file_count": len(files), "absence_count": len(absences),
            "total_bytes": sum(record.byte_size for record in reader.records), "unknown_count": len(reader.unknown)}
        self._projection_json = _json({"format_version": FORMAT_VERSION, "summary": summary,
            "files": files, "references": references, "absences": absences, "unknown": reader.unknown})
        self._summary_json = _json(summary)

    def _check(self, *, short=False):
        context = _CONTEXT.get()
        if (type(self) is not PendingDependenciesCapture or self._seal is not _SEAL or not self._active
                or threading.get_ident() != self._thread or not any(value is self for value in _CURRENT.get())
                or not any(value is self for value in context)):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(context)
        self._reader.check(short=short)

    @property
    def summary(self):
        self._check(short=True)
        return json.loads(self._summary_json)

    @property
    def unknown(self):
        self._check()
        return tuple(json.loads(self._projection_json)["unknown"])

    @property
    def records(self):
        self._check()
        return tuple(deepcopy(self._reader.records))

    @property
    def requirements(self):
        self._check()
        return tuple(deepcopy(self._reader.requirements))

    def public_projection(self):
        self._check()
        return json.loads(self._projection_json)

    def public_summary(self):
        projected = self.public_projection()
        return {key: projected[key] for key in ("summary", "references", "unknown")}

    def verify_source(self):
        self._check()
        self._reader.verify()

    def verify_for_commit(self):
        # No State export, JSON validation, long SHA, SQL statement or writer
        # acquisition occurs in this short held-file/absence comparison.
        self._check(short=True)
        self._reader.verify(full=False)


@contextmanager
def capture_locked(profile, *, pending_capture, limits=None, check_cancelled=None):
    """Hold only actual selected dependencies in the caller's existing SH."""
    profile = lifecycle._profile(profile)
    source_reader = getattr(pending_capture, "_reader", None)
    if (type(pending_capture) is not pending.PendingStateCapture or not getattr(pending_capture, "_active", False)
            or type(source_reader) is not pending._Reader
            or not isinstance(getattr(source_reader, "profile", None), lifecycle.StorageProfile)
            or source_reader.profile.identity != profile.identity or sql.in_write_window()
            or any(type(getattr(pending_capture, name, None)) is not bytes for name in
                ("_projection_json", "_summary_json", "_state_images_json"))):
        _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")
    limits = CaptureLimits() if limits is None else limits
    if type(limits) is not CaptureLimits or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid pending dependency capture options")
    guard = lifecycle.require_guard(profile)
    if getattr(source_reader, "guard", None) is not guard:
        _fail("ACTUAL_PENDING_CAPTURE_REQUIRED")
    with ExitStack() as stack:
        reader = _Reader(profile, pending_capture, stack, guard, limits, check_cancelled)
        captured = None
        try:
            reader.acquire()
            captured = object.__new__(PendingDependenciesCapture)
            captured._initialize(reader)
            token = _CURRENT.set((*_CURRENT.get(), captured))
            captured._context_token = _CONTEXT.set((*_CONTEXT.get(), captured))
            try:
                captured.verify_source()
                yield captured
                captured.verify_source()
            finally:
                captured._active = False
                _CONTEXT.reset(captured._context_token)
                _CURRENT.reset(token)
        except _Cancelled as error:
            raise error.error from None
