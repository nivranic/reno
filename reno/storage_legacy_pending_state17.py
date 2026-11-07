"""Read-only physical capture of original pending State capsules and markers.

This independent namespace observation is a retention input, not a Home
authority witness, writer permit, paid proof or completed-operation capability.
Historical Home witness1 never invokes it and therefore proves no absence in
this namespace. A future Home witness2 must explicitly bind its summary and
the original copied file set. No existing Home consumer is changed here.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
import stat
import time
import uuid

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle, storage_source as source
from . import sync_media as safe, sync_source_authority as authority, sync_state
from . import sync_source_legacy_pending_state17 as capsules
from .sync_protocol import SyncError

DIRECTORY = capsules.DIRECTORY
FORMAT_VERSION = 1
KIND = "legacy_pending_state"
MAX_APPLIED_BYTES = 16 * 1024
MAX_FILES = 2 * authority.MAX_RECORDS
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SUMMARY_FIELDS = frozenset({"format_version", "kind", "owner_profile_sha256", "presence",
    "absence_sha256", "namespace_sha256", "file_set_sha256", "state_set_sha256",
    "reference_set_sha256", "unknown_sha256", "file_count", "capsule_count", "marker_count",
    "applied_count", "total_bytes", "unknown_count"})
REFERENCE_FIELDS = frozenset({"format_version", "record_kind", "record_id", "record_sha256", "core_sha256"})
REFERENCE_KINDS = frozenset({"origin", "control", "publication", "recovery_intent", "recovery_receipt"})


class PendingStateRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        self.code, self.retriable = "LEGACY_PENDING_STATE_ROOT_" + code, retriable
        super().__init__(self.code)


def _fail(code, *, retriable=False):
    raise PendingStateRootError(code, retriable=retriable) from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _digest(value):
    return _sha(_json(value))


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_FIELD")
            result[key] = value
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if _json(value) != raw:
        _fail("NONCANONICAL")
    return value


def _name(name):
    if type(name) is not str:
        return None
    role = "legacy_pending_applied" if name.endswith(".applied.json") else "legacy_pending_state"
    suffix = ".applied.json" if role == "legacy_pending_applied" else ".json"
    if not name.endswith(suffix):
        return None
    identifier = name[:-len(suffix)]
    try:
        if str(uuid.UUID(identifier)) != identifier:
            return None
    except (ValueError, AttributeError):
        return None
    return identifier, role


@dataclass(frozen=True)
class CaptureLimits:
    max_files: int = MAX_FILES
    max_capsule_bytes: int = capsules.MAX_BYTES
    max_applied_bytes: int = MAX_APPLIED_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES
    deadline_seconds: float = 10.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError("Invalid pending State capture limit")
            elif (type(value) is not int or value < 1
                    or value > type(self).__dataclass_fields__[name].default):
                raise ValueError("Invalid pending State capture limit")


@dataclass(frozen=True)
class CapturedFile:
    name: str
    raw: bytes
    sha256: str
    byte_size: int

    def descriptor(self):
        selected = _name(self.name)
        return {"root_kind": "DATA", "relative_key": DIRECTORY + "/" + self.name,
            "role": "legacy_pending_unknown" if selected is None else selected[1],
            "sha256": self.sha256, "byte_size": self.byte_size}


def _images(value, identifier, unknown):
    result = []
    for phase in ("before", "after"):
        try:
            encoded = value[phase + "_json"]
            if type(encoded) is not str or len(encoded.encode()) > sync_state.MAX_STATE_BYTES:
                _fail("STATE_LIMIT")
            state = json.loads(encoded)
            if sync_state._validate(state).decode() != encoded or state["format_version"] != 5:
                _fail("STATE_INVALID")
            result.append({"request_id": identifier, "phase": phase,
                "state_json": encoded, "state_sha256": _sha(encoded.encode())})
        except (PendingStateRootError, SyncError, ValueError, TypeError, KeyError,
                UnicodeError, OverflowError, RecursionError):
            unknown.append({"name": identifier + ".json", "code": "STATE_IMAGE_INVALID", "phase": phase})
    return result


def _validate_capsule(value, identifier, profile, images):
    if (type(value) is not dict or set(value) != capsules.FIELDS
            or type(value["format_version"]) is not int or value["format_version"] != 1
            or value["request_id"] != identifier
            or value["owner_profile_sha256"] != authority.profile_sha256(profile)
            or len(images) != 2):
        _fail("CAPSULE_INVALID")
    for field in ("core_sha256", "intent_sha256", "receipt_sha256", "before_raw_sha256", "after_sha256"):
        authority._sha(value[field])
    if (type(value["before_bytes"]) is not int or not 1 <= value["before_bytes"] <= sync_state.MAX_STATE_BYTES
            or type(value["before_raw_base64"]) is not str):
        _fail("CAPSULE_INVALID")
    raw = base64.b64decode(value["before_raw_base64"], validate=True)
    if (len(raw) != value["before_bytes"] or _sha(raw) != value["before_raw_sha256"]
            or base64.b64encode(raw).decode("ascii") != value["before_raw_base64"]):
        _fail("CAPSULE_INVALID")
    before, after = (json.loads(row["state_json"]) for row in images)
    if (json.loads(raw) != before or before["source_execution"] is None
            or before["source_execution"] != after["source_execution"]
            or authority.digest(before["source_execution"]) != value["core_sha256"]
            or _sha(value["after_json"].encode()) != value["after_sha256"]):
        _fail("CAPSULE_INVALID")
    pending = before["source_recovery_pending"]
    if (pending is None or pending["kind"] not in {"source_dispose", "source_readmit"}
            or (pending["request_id"], pending["core_sha256"], pending["intent_sha256"]) !=
                (identifier, value["core_sha256"], value["intent_sha256"])
            or after["source_recovery_pending"] is not None):
        _fail("CAPSULE_INVALID")


def normalize_summary(value, *, owner_profile_sha256=None):
    """Validate this component's explicit version without changing any hash.

    Unknown observations remain represented and cannot become absence. A Home
    witness2 consumer must separately require unknown_count == 0 and compare
    the summary with its own original captured/copied projection.
    """
    if (type(value) is not dict or set(value) != SUMMARY_FIELDS
            or type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION
            or value["kind"] != KIND or value["presence"] not in {"absent", "present"}):
        _fail("SUMMARY_INVALID")
    for field in ("owner_profile_sha256", "namespace_sha256", "file_set_sha256",
            "state_set_sha256", "reference_set_sha256", "unknown_sha256"):
        authority._sha(value[field])
    if owner_profile_sha256 is not None and value["owner_profile_sha256"] != owner_profile_sha256:
        _fail("SUMMARY_INVALID")
    for field in ("file_count", "capsule_count", "marker_count", "applied_count", "unknown_count", "total_bytes"):
        maximum = MAX_TOTAL_BYTES if field == "total_bytes" else 3 * MAX_FILES if field == "unknown_count" else MAX_FILES
        if type(value[field]) is not int or not 0 <= value[field] <= maximum:
            _fail("SUMMARY_INVALID")
    if (value["capsule_count"] + value["marker_count"] > value["file_count"]
            or value["applied_count"] > min(value["capsule_count"], value["marker_count"])):
        _fail("SUMMARY_INVALID")
    if value["presence"] == "absent":
        absence = {"format_version": FORMAT_VERSION, "kind": KIND, "directory": DIRECTORY,
            "owner_profile_sha256": value["owner_profile_sha256"], "presence": "absent"}
        if (value["absence_sha256"] != _digest(absence)
                or value["namespace_sha256"] != _digest({"presence": "absent", "names": []})
                or any(value[field] for field in ("file_count", "capsule_count", "marker_count", "applied_count", "unknown_count", "total_bytes"))
                or any(value[field] != _digest([]) for field in ("file_set_sha256", "state_set_sha256", "reference_set_sha256", "unknown_sha256"))):
            _fail("SUMMARY_INVALID")
    elif value["absence_sha256"] is not None:
        _fail("SUMMARY_INVALID")
    if value["file_count"] == 0 and (value["namespace_sha256"] != _digest({"presence": value["presence"], "names": []})
            or any(value[field] for field in ("capsule_count", "marker_count", "applied_count", "unknown_count", "total_bytes"))
            or any(value[field] != _digest([]) for field in ("file_set_sha256", "state_set_sha256", "reference_set_sha256", "unknown_sha256"))):
        _fail("SUMMARY_INVALID")
    return deepcopy(value)


def _project(profile, *, presence, records, limits=None):
    """Private parser retains complete State images beside the public facts."""
    profile = lifecycle._profile(profile)
    limits = CaptureLimits() if limits is None else limits
    if type(limits) is not CaptureLimits or presence not in {"absent", "present"} or type(records) is not tuple:
        raise ValueError("Invalid pending State projection input")
    if len(records) > limits.max_files or presence == "absent" and records:
        _fail("COUNT_LIMIT")
    names, descriptors, total = [], [], 0
    unknown, images, references, valid, markers = [], [], {}, {}, {}
    capsule_count = marker_count = applied_count = 0
    for record in records:
        if type(record) is not CapturedFile or type(record.raw) is not bytes:
            _fail("INVALID_INPUT")
        selected = _name(record.name)
        maximum = limits.max_applied_bytes if selected and selected[1] == "legacy_pending_applied" else limits.max_capsule_bytes
        if (type(record.byte_size) is not int or record.byte_size != len(record.raw)
                or record.byte_size > maximum or record.sha256 != _sha(record.raw)):
            _fail("FILE_LIMIT")
        total += record.byte_size
        if total > limits.max_total_bytes:
            _fail("TOTAL_LIMIT")
        names.append(record.name)
        descriptors.append(record.descriptor())
        if selected is None:
            unknown.append({"name": record.name, "code": "NAME_INVALID"})
            continue
        identifier, role = selected
        capsule_count += role == "legacy_pending_state"
        marker_count += role == "legacy_pending_applied"
        try:
            value = _decode(record.raw)
            if type(value) is not dict:
                _fail("RECORD_INVALID")
            if role == "legacy_pending_applied":
                markers[identifier] = (record, value)
                continue
            selected_images = _images(value, identifier, unknown)
            images.extend(selected_images)
            _validate_capsule(value, identifier, profile, selected_images)
            valid[identifier] = (record, value)
            for kind, digest in (("recovery_intent", value["intent_sha256"]),
                                 ("recovery_receipt", value["receipt_sha256"])):
                ref = {"format_version": 1, "record_kind": kind, "record_id": identifier, "record_sha256": digest,
                    "core_sha256": value["core_sha256"]}
                references[_digest(ref)] = ref
        except (PendingStateRootError, SyncError, ValueError, TypeError, KeyError,
                UnicodeError, OverflowError, RecursionError):
            unknown.append({"name": record.name, "code": "CAPSULE_INVALID"})
    if names != sorted(set(names)):
        _fail("NAMESPACE_INVALID")
    # Preserve each independently valid full State, including every field a
    # later exact root parser needs. A damaged sibling/marker cannot erase it.
    state_summaries = []
    for image in images:
        state = json.loads(image["state_json"])
        projected = sync_state.source_readmit_projection(state)
        state_summaries.append({"request_id": image["request_id"], "phase": image["phase"],
            "state_sha256": image["state_sha256"], "source_core_sha256": projected["core_sha256"],
            "refset_sha256": projected["refset_sha256"]})
        original = projected["source_authority"]
        if original is not None:
            for slot in ("origin", "defer", "resume", "failure", "publication"):
                if original[slot] is not None:
                    ref = {**deepcopy(original[slot]), "core_sha256": projected["core_sha256"]}
                    references[_digest(ref)] = ref
        for field in ("source_admission", "source_recovery_pending"):
            if projected[field] is not None:
                original = projected[field]
                admission = field == "source_admission"
                ref = {"format_version": 1,
                    "record_kind": "recovery_receipt" if admission else "recovery_intent",
                    "record_id": original["admission_id"] if admission else original["request_id"],
                    "record_sha256": original["record_sha256"] if admission else original["intent_sha256"],
                    "core_sha256": projected["core_sha256"]}
                references[_digest(ref)] = ref
    for identifier, (record, marker) in sorted(markers.items()):
        try:
            original, value = valid[identifier]
            capsules.validate_applied(marker, capsule={"value": value,
                "identity": {"sha256": original.sha256}})
            applied_count += 1
        except (PendingStateRootError, SyncError, ValueError, TypeError, KeyError,
                UnicodeError, OverflowError, RecursionError):
            unknown.append({"name": record.name, "code": "APPLIED_ORIGINAL_UNPROVEN"})
    references = [references[key] for key in sorted(references)]
    unknown.sort(key=lambda row: _json(row))
    absence = {"format_version": FORMAT_VERSION, "kind": KIND, "directory": DIRECTORY,
        "owner_profile_sha256": authority.profile_sha256(profile), "presence": "absent"}
    summary = {"format_version": FORMAT_VERSION, "kind": KIND,
        "owner_profile_sha256": authority.profile_sha256(profile), "presence": presence,
        "absence_sha256": _digest(absence) if presence == "absent" else None,
        "namespace_sha256": _digest({"presence": presence, "names": names}),
        "file_set_sha256": _digest(descriptors), "state_set_sha256": _digest(state_summaries),
        "reference_set_sha256": _digest(references), "unknown_sha256": _digest(unknown),
        "file_count": len(records), "capsule_count": capsule_count, "marker_count": marker_count,
        "applied_count": applied_count, "total_bytes": total, "unknown_count": len(unknown)}
    normalize_summary(summary)
    return {"format_version": FORMAT_VERSION, "summary": summary, "files": descriptors,
        "references": references, "states": state_summaries, "unknown": unknown}, images


def pure_projection(profile, *, presence, records, limits=None):
    """Project safe facts only, without SQL, I/O or execution authority.

    Complete State, target URLs and personal paths never enter this output.
    The caller's presence flag is data, never physical absence proof. The live
    factory derives it from a held directory. Copied readers must bind it to
    explicit original witness2; witness1 is uncovered. The private _project
    parser also returns complete State images for copied root reconstruction.
    """
    return _project(profile, presence=presence, records=records, limits=limits)[0]


class _Reader:
    def __init__(self, profile, stack, guard, limits, callback):
        self.profile, self.stack, self.guard, self.limits, self.callback = profile, stack, guard, limits, callback
        self.started, self.total = time.monotonic(), 0
        self.files, self.directories, self.absences, self.names = [], [], [], []
        self.records, self.presence = [], None

    def check(self, *, short=False):
        if (not short and sql.in_write_window()) or lifecycle.require_guard(self.profile) is not self.guard:
            _fail("READ_WINDOW_REQUIRED")
        self.guard.verify()
        if self.callback is not None:
            self.callback()
        if (not short and sql.in_write_window()) or lifecycle.require_guard(self.profile) is not self.guard:
            _fail("READ_WINDOW_REQUIRED")
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            _fail("DEADLINE_LIMIT")

    def listing(self, directory, *, short=False):
        self.check(short=short)
        names = []
        with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
            for item in entries:
                self.check(short=short)
                if len(names) >= self.limits.max_files:
                    _fail("COUNT_LIMIT")
                names.append(item.name)
        return tuple(sorted(names))

    def acquire(self):
        self.check()
        data = self.stack.enter_context(safe._Directory(self.profile.database.parent,
            base=self.profile.database.parent))
        self.directories.append(data)
        try:
            directory = self.stack.enter_context(safe._Directory(data.path / DIRECTORY, base=data.path))
        except FileNotFoundError:
            self.presence = "absent"
            self.absences.append((data, DIRECTORY))
            return
        self.presence = "present"
        self.directories.append(directory)
        names = self.listing(directory)
        self.names.append((directory, names))
        for name in names:
            self.check()
            before_info = directory.entry_stat(name)
            before = source.file_signature(before_info)
            if os.name != "nt" and (before_info.st_uid != os.geteuid() or stat.S_IMODE(before_info.st_mode) & 0o077):
                _fail("UNSAFE_FILE")
            selected = _name(name)
            maximum = self.limits.max_applied_bytes if selected and selected[1] == "legacy_pending_applied" else self.limits.max_capsule_bytes
            self.total += before[2]
            if before[2] > maximum:
                _fail("FILE_LIMIT")
            if self.total > self.limits.max_total_bytes:
                _fail("TOTAL_LIMIT")
            # Immutable handles use the original media reader's no-follow and
            # no-delete/write Windows policy, plus original source signatures.
            held = self.stack.enter_context(directory.open_file(name))
            opened = source.file_signature(os.fstat(held.fileno()))
            if before[:4] != opened[:4]:
                _fail("SOURCE_CHANGED", retriable=True)
            raw = self.read(held, before[2])
            digest = hashlib.sha256(raw).digest()
            if (source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("SOURCE_CHANGED", retriable=True)
            self.files.append((directory, name, held, before, opened, digest))
            self.records.append(CapturedFile(name, raw, digest.hex(), len(raw)))

    def read(self, held, size):
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

    def verify(self, *, full=True):
        self.check(short=not full)
        for directory in self.directories:
            directory.verify()
        for parent, name in self.absences:
            try:
                parent.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("SOURCE_CHANGED", retriable=True)
        for directory, names in self.names:
            if self.listing(directory, short=not full) != names:
                _fail("SOURCE_CHANGED", retriable=True)
        for directory, name, held, before, opened, digest in self.files:
            self.check(short=not full)
            if (source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("SOURCE_CHANGED", retriable=True)
            if full and hashlib.sha256(self.read(held, before[2])).digest() != digest:
                _fail("SOURCE_CHANGED", retriable=True)
            if (source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("SOURCE_CHANGED", retriable=True)
        self.check(short=not full)


class PendingStateCapture:
    def __init__(self, reader):
        self._reader, self._active = reader, True
        projection, images = _project(reader.profile, presence=reader.presence,
            records=tuple(reader.records), limits=reader.limits)
        self._projection_json = _json(projection)
        self._summary_json = _json(projection["summary"])
        self._state_images_json = _json(images)

    def _check(self, *, short=False):
        if not self._active:
            _fail("CAPTURE_CLOSED")
        self._reader.check(short=short)

    @property
    def summary(self):
        self._check(short=True)
        return json.loads(self._summary_json)

    @property
    def references(self):
        self._check()
        return tuple(json.loads(self._projection_json)["references"])

    @property
    def state_images(self):
        self._check()
        return tuple(json.loads(self._state_images_json))

    @property
    def unknown(self):
        self._check()
        return tuple(json.loads(self._projection_json)["unknown"])

    @property
    def records(self):
        self._check()
        return tuple(deepcopy(self._reader.records))

    def public_projection(self):
        """Safe namespace/file/strict reference facts; complete State excluded."""
        self._check()
        return json.loads(self._projection_json)

    def public_summary(self):
        """Compact safe input for a distinct Home nested witness/projection2."""
        projected = self.public_projection()
        return {key: projected[key] for key in ("summary", "references", "unknown")}

    def verify_source(self):
        self._check()
        self._reader.verify()

    def verify_for_commit(self):
        # Physical comparison only; no long SHA, State export, SQL statement
        # or writer capability is created inside a caller's bounded writer.
        self._check(short=True)
        self._reader.verify(full=False)


_APPEND_PLAN_SEAL = object()


@dataclass(frozen=True)
class _AppendPlan17:
    seal: object
    writer: object
    home: object
    captured: object
    original: object
    candidate: object
    cache: tuple
    home_summary: dict
    home_references: tuple


def _require_append_writer(writer, home, captured):
    from . import sync_source_legacy_pending_state17 as capsules
    if type(writer) is not capsules._PendingStateAppendWriter17:
        _fail("APPEND_PRODUCER_REQUIRED")
    writer._authorize_home(home)
    if (type(captured) is not PendingStateCapture or not captured._active
            or home._legacy_pending is not captured):
        _fail("APPEND_PRODUCER_REQUIRED")
    captured._check()


def _verify_append_prefix(original, candidate, *, expected_name, writer):
    """Verify old actual pins while permitting only this writer's sole name.

    The old namespace absence/listing cannot be rechecked unchanged after
    the append. Every other old directory, absence, file identity and full
    SHA remains an original comparison, including unknown physical files.
    """
    original.check()
    candidate.verify()
    old_names = tuple(record.name for record in original.records)
    new_names = tuple(record.name for record in candidate.records)
    if (expected_name in old_names or new_names != tuple(sorted((*old_names, expected_name)))
            or candidate.presence != "present"):
        _fail("APPEND_FILE_SET_CHANGED")
    new_directories = {directory.path: directory for directory in candidate.directories}
    for directory in original.directories:
        directory.verify()
        observed = new_directories.get(directory.path)
        if observed is None or tuple(observed.identities) != tuple(directory.identities):
            _fail("APPEND_DIRECTORY_CHANGED", retriable=True)
    for parent, name in original.absences:
        if (original.presence == "absent" and parent.path == original.profile.database.parent
                and name == DIRECTORY):
            continue
        try:
            parent.entry_stat(name)
        except FileNotFoundError:
            continue
        _fail("APPEND_PREFIX_CHANGED", retriable=True)
    for directory, names in original.names:
        if (directory.path != original.profile.database.parent / DIRECTORY
                or tuple(names) != old_names):
            _fail("APPEND_PREFIX_CHANGED")
    observed_files = {(directory.path, name): (before, opened, digest)
        for directory, name, _held, before, opened, digest in candidate.files}
    old_records = {record.name: record for record in original.records}
    if len(old_records) != len(original.files):
        _fail("APPEND_PREFIX_CHANGED")
    for directory, name, held, before, opened, digest in original.files:
        original.check()
        if (observed_files.get((directory.path, name)) != (before, opened, digest)
                or source.file_signature(directory.entry_stat(name)) != before
                or source.file_signature(os.fstat(held.fileno())) != opened
                or hashlib.sha256(original.read(held, before[2])).digest() != digest):
            _fail("APPEND_PREFIX_CHANGED", retriable=True)
        record = old_records.get(name)
        if record is None or (record.sha256, record.byte_size) != (digest.hex(), before[2]):
            _fail("APPEND_PREFIX_CHANGED")
        if (source.file_signature(directory.entry_stat(name)) != before
                or source.file_signature(os.fstat(held.fileno())) != opened):
            _fail("APPEND_PREFIX_CHANGED", retriable=True)
    selected_directory = new_directories.get(original.profile.database.parent / DIRECTORY)
    writer._verify_appended_file(candidate, selected_directory)
    # The producer callback above cannot invalidate an earlier file check.
    # Finish with a bounded pass containing no caller callbacks, then apply
    # only fixed comparisons. Cancellation was checked throughout the reads.
    writer._fixed_context()
    candidate.guard.verify()
    expected = tuple(record.name for record in candidate.records)
    def names_now():
        names = []
        with os.scandir(selected_directory.fd if os.name != "nt"
                else safe._native_path(selected_directory.path)) as entries:
            for entry in entries:
                if (len(names) >= candidate.limits.max_files
                        or time.monotonic() - candidate.started > candidate.limits.deadline_seconds):
                    _fail("APPEND_LIMIT")
                names.append(entry.name)
        return tuple(sorted(names))
    if names_now() != expected:
        _fail("APPEND_FILE_SET_CHANGED", retriable=True)
    for directory in candidate.directories:
        directory.verify()
    for directory, name, held, before, opened, digest in candidate.files:
        if (source.file_signature(directory.entry_stat(name)) != before
                or source.file_signature(os.fstat(held.fileno())) != opened):
            _fail("APPEND_PREFIX_CHANGED", retriable=True)
        os.lseek(held.fileno(), 0, os.SEEK_SET)
        found, remaining = hashlib.sha256(), before[2]
        while remaining:
            if time.monotonic() - candidate.started > candidate.limits.deadline_seconds:
                _fail("APPEND_LIMIT")
            part = os.read(held.fileno(), min(128 * 1024, remaining))
            if not part:
                _fail("APPEND_PREFIX_CHANGED", retriable=True)
            found.update(part)
            remaining -= len(part)
        if (os.read(held.fileno(), 1) or found.digest() != digest
                or source.file_signature(directory.entry_stat(name)) != before
                or source.file_signature(os.fstat(held.fileno())) != opened):
            _fail("APPEND_PREFIX_CHANGED", retriable=True)
    if names_now() != expected:
        _fail("APPEND_FILE_SET_CHANGED", retriable=True)
    writer._fixed_context()


def _prepare_append17(captured, *, writer, home):
    """Build a private plan only for an actual sealed pending writer."""
    _require_append_writer(writer, home, captured)
    original = captured._reader
    projection, images = _project(original.profile, presence=original.presence,
        records=tuple(original.records), limits=original.limits)
    cache = (captured._projection_json, captured._summary_json, captured._state_images_json)
    if cache != (_json(projection), _json(projection["summary"]), _json(images)):
        _fail("APPEND_PREFIX_CHANGED")
    reader = _Reader(original.profile, original.stack, original.guard, original.limits, original.callback)
    # A legal append renews observations, never this operation's deadline.
    reader.started = original.started
    reader.acquire()
    _verify_append_prefix(original, reader, expected_name=writer._expected_name, writer=writer)
    candidate = PendingStateCapture(reader)
    if json.loads(candidate._projection_json)["unknown"]:
        _fail("APPEND_RESULT_UNPROVEN")
    return _AppendPlan17(_APPEND_PLAN_SEAL, writer, home, captured, original, candidate,
        cache, deepcopy(home._summary), deepcopy(home._references))


def _check_append_plan17(plan, *, writer, home, captured):
    _require_append_writer(writer, home, captured)
    if (type(plan) is not _AppendPlan17 or plan.seal is not _APPEND_PLAN_SEAL
            or plan.writer is not writer or plan.home is not home or plan.captured is not captured
            or captured._reader is not plan.original
            or (captured._projection_json, captured._summary_json, captured._state_images_json) != plan.cache
            or home._summary != plan.home_summary or home._references != plan.home_references):
        _fail("APPEND_PRODUCER_REQUIRED")
    _verify_append_prefix(plan.original, plan.candidate._reader,
        expected_name=writer._expected_name, writer=writer)
    projected = json.loads(plan.candidate._projection_json)
    return {key: projected[key] for key in ("summary", "references", "unknown")}


def _fixed_append_plan17(plan, *, writer, home, captured):
    from . import sync_source_legacy_pending_state17 as capsules
    if type(writer) is not capsules._PendingStateAppendWriter17:
        _fail("APPEND_PRODUCER_REQUIRED")
    writer._authorize_home_fixed(home)
    if (type(plan) is not _AppendPlan17 or plan.seal is not _APPEND_PLAN_SEAL
            or plan.writer is not writer or plan.home is not home or plan.captured is not captured
            or not captured._active or home._legacy_pending is not captured
            or captured._reader is not plan.original
            or (captured._projection_json, captured._summary_json, captured._state_images_json) != plan.cache
            or home._summary != plan.home_summary or home._references != plan.home_references):
        _fail("APPEND_PRODUCER_REQUIRED")
    projected = json.loads(plan.candidate._projection_json)
    return {key: projected[key] for key in ("summary", "references", "unknown")}


def _adopt_append17(plan, *, writer, home, captured):
    # The entire batch was physically rechecked before its first adoption.
    # This tail performs no I/O or caller callback between the two updates.
    _fixed_append_plan17(plan, writer=writer, home=home, captured=captured)
    # Preserve the component object registered with the original ExitStack.
    # Its eventual context exit now validates these extended actual pins.
    captured._reader = plan.candidate._reader
    captured._projection_json = plan.candidate._projection_json
    captured._summary_json = plan.candidate._summary_json
    captured._state_images_json = plan.candidate._state_images_json


@contextmanager
def capture_locked(profile, *, limits=None, check_cancelled=None):
    """Pin actual namespace/files under caller-owned SH; never create or write."""
    profile = lifecycle._profile(profile)
    limits = CaptureLimits() if limits is None else limits
    if type(limits) is not CaptureLimits or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid pending State capture options")
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    guard = lifecycle.require_guard(profile)
    with ExitStack() as stack:
        reader = _Reader(profile, stack, guard, limits, check_cancelled)
        reader.acquire()
        captured = PendingStateCapture(reader)
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
