"""Bounded, pure home-reference projections and no-follow file capture.

The caller acquires the complete lifecycle group before capture. No StateStore,
SourceMap, SQLite connection, configuration lookup or network client is created.
Raw capture bindings prove one reading round only; InputScan binds the semantic
reference projection, deliberately excluding lease/display heartbeat fields.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import math
import os
import re
import time
import uuid

from . import storage_lifecycle as lifecycle, storage_root_inputs as inputs
from . import storage_source, sync_media as safe, sync_protocol as protocol
from . import sync_snapshot, sync_sources, sync_state, sync_reports, sync_no_resend
from .sync_transfer import _bindings, publication_payload_sha256

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_BASE_KINDS = ("state", "outbox", "history", "sources", "sources_v2", "source_root")
_SENDER_KINDS = ("sender_anchors", "sender_root", "sender_version", "publications", "receipts", "references")
_REGULAR_KINDS = (*_BASE_KINDS, *_SENDER_KINDS)
_PAYLOAD_KINDS = ("publication_payload_root", "publication_payload")
_KINDS = (*_REGULAR_KINDS, *_PAYLOAD_KINDS, "part")
_DIRECTORIES = {"outbox": "sync-outbox", "history": "sync-work-history", "sources": "sync-sources/v1",
    "sources_v2": "sync-sources/v2", "source_root": "sync-sources", "sender_root": "sync-no-resend", "sender_version": "sync-no-resend/v1",
    **{kind: "sync-no-resend/v1/" + kind for kind in ("publications", "receipts", "references")}}
_FIXED_FILES = {"state": ("sync-state.json",),
                "sender_anchors": (sync_no_resend.WITNESS, sync_no_resend.SEAL)}
_STRUCTURAL_NAMES = {"source_root": {"v1", "v2"}, "sender_root": {"v1"},
                     "sender_version": {"publications", "receipts", "references"}}
_SENDER_DIRECTORY_LIMITS = {"sender_root": 2, "sender_version": 16,
    **{kind: sync_no_resend.MAX_RECORDS + 32 for kind in ("publications", "receipts", "references")}}
_AFFECTED = {kind: ("sync_media", "local_report", "sync_report") for kind in _KINDS}
_AFFECTED["sources"] = _AFFECTED["sources_v2"] = _AFFECTED["source_root"] = ("sync_media",)
_AFFECTED["authority"] = ("sync_media",)
_AFFECTED["legacy_pending_state"] = ("sync_media", "local_report", "sync_report")
for _payload_kind in (*_PAYLOAD_KINDS, "part"):
    _AFFECTED[_payload_kind] = ("sync_media",)
_HISTORY_FIELDS = {"format_version", "reason", "binding", "local_epoch", "claims", "work", "inbound",
                   "outbound", "pending_publication", "recovery_attempt", "recovery", "legacy_pending_stream"}
_HISTORY_V2_FIELDS = _HISTORY_FIELDS | {"sender_contract", "publication_media", "uploaded_media"}
_STAMP = "2000-01-01T00:00:00Z"
_UUID = "00000000-0000-4000-8000-000000000001"
_CODES = frozenset({"HOME_DEADLINE_LIMIT", "HOME_FILE_COUNT_LIMIT", "HOME_TOTAL_BYTES_LIMIT",
    "HOME_JSON_LIMIT", "HOME_JSON_INVALID", "HOME_FILE_BYTES_LIMIT", "HOME_UNSAFE_PATH",
    "HOME_FILE_UNAVAILABLE", "HOME_RECORD_NAME_INVALID", "HOME_RECORD_INVALID", "HOME_DUPLICATE_RECORD",
    "HOME_PENDING_OUTBOX_UNPROVEN", "HOME_ATTEMPT_OUTBOX_UNPROVEN", "HOME_SOURCE_MAP_MISSING",
    "HOME_CAPTURE_INCOMPLETE", "HOME_SENDER_EVIDENCE_INVALID", "HOME_RETIREMENT_UNPROVEN",
    "HOME_RETIREMENT_DEPENDENCY_ACTIVE", "HOME_AUTHORITY_OUTBOX_UNPROVEN",
    "HOME_PUBLICATION_PAYLOAD_UNPROVEN", "HOME_PUBLICATION_PART_UNKNOWN",
    "HOME_LEGACY_PENDING_STATE_UNPROVEN"})


@dataclass(frozen=True)
class HomeLimits:
    max_files: int = 4096
    max_total_bytes: int = 128 * 1024 * 1024
    max_state_bytes: int = sync_state.MAX_STATE_BYTES
    max_outbox_bytes: int = sync_state.MAX_OUTBOX_BYTES
    max_source_bytes: int = sync_sources.MAX_RECORD_BYTES
    max_sender_record_bytes: int = sync_no_resend.MAX_RECORD_BYTES
    max_sender_catalog_bytes: int = sync_no_resend.MAX_CATALOG_BYTES
    max_sender_total_bytes: int = sync_no_resend.MAX_TOTAL_BYTES
    max_json_nodes: int = 1_000_000
    max_json_depth: int = 32
    deadline_seconds: float = 10.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError("Invalid home limits")
            elif type(value) is not int or value <= 0:
                raise ValueError("Invalid home limits")


class HomeRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _Invalid(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _changed():
    raise HomeRootError("HOME_SOURCE_CHANGED", retriable=True)


def _hash(value):
    return hashlib.sha256(inputs.canonical_bytes(value)).hexdigest()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid UUID")
    return value


@dataclass(frozen=True)
class CapturedRecord:
    kind: str
    name: str
    raw: bytes | None = None
    error: str | None = None


@dataclass(frozen=True)
class CapturedBundle:
    records: tuple[CapturedRecord, ...]
    directories: tuple[tuple[str, str], ...]
    issues: tuple[tuple[str, str], ...] = ()
    directory_entries: tuple[tuple[str, tuple[str, ...]], ...] = ()


class _Budget:
    def __init__(self, limits, check=None):
        self.limits, self.check_cancelled = limits, check or (lambda: None)
        self.started, self.files, self.size, self.nodes = time.monotonic(), 0, 0, 0

    def check(self):
        self.check_cancelled()  # Outside all filesystem exception translators.
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            raise _Invalid("HOME_DEADLINE_LIMIT")

    def file(self):
        self.check()
        self.files += 1
        if self.files > self.limits.max_files:
            raise _Invalid("HOME_FILE_COUNT_LIMIT")

    def bytes(self, size):
        self.check()
        self.size += size
        if self.size > self.limits.max_total_bytes:
            raise _Invalid("HOME_TOTAL_BYTES_LIMIT")


def _io(operation):
    try:
        return operation()
    except (safe.SyncError, storage_source.SourceReadError):
        raise _Invalid("HOME_UNSAFE_PATH") from None
    except OSError:
        raise _Invalid("HOME_FILE_UNAVAILABLE") from None


def _read(directory, name, maximum, budget, *, allow_missing=False):
    try:
        before = directory.entry_stat(name)
    except FileNotFoundError:
        if allow_missing:
            return None, None
        _changed()  # Enumerated entries cannot silently disappear.
    except OSError:
        raise _Invalid("HOME_FILE_UNAVAILABLE") from None
    signature = _io(lambda: storage_source.file_signature(before))
    if before.st_size > maximum:
        raise _Invalid("HOME_FILE_BYTES_LIMIT")
    budget.bytes(before.st_size)
    stream = _io(lambda: directory.open_file(name))
    try:
        opened = _io(lambda: storage_source.file_signature(os.fstat(stream.fileno())))
        if signature[:4] != opened[:4]:
            _changed()
        chunks, length = [], 0
        while True:
            budget.check()
            chunk = _io(lambda: stream.read(min(128 * 1024, maximum + 1 - length)))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum or length > before.st_size:
                _changed()
        after = _io(lambda: storage_source.file_signature(os.fstat(stream.fileno())))
        if after != opened or length != before.st_size:
            _changed()
    finally:
        stream.close()
    try:
        info = directory.entry_stat(name)
    except FileNotFoundError:
        _changed()
    except OSError:
        raise _Invalid("HOME_FILE_UNAVAILABLE") from None
    again = _io(lambda: storage_source.file_signature(info))
    if again != signature:
        _changed()
    _io(directory.verify)
    raw = b"".join(chunks)
    return raw, (signature, hashlib.sha256(raw).hexdigest())


def _record_name(kind, name):
    if kind in {"source_root", "sender_root", "publication_payload_root"}:
        return False
    if kind in _FIXED_FILES:
        return name in _FIXED_FILES[kind]
    if kind == "sender_version":
        return name == "catalog.json"
    if not name.endswith(".json"):
        return False
    token = name[:-5]
    if kind in {"outbox", "sources_v2", "publications", "receipts", "references"}:
        try:
            return _uuid(token) is not None
        except ValueError:
            return False  # Unknown files never become managed records.
    return bool((_HEX if kind == "history" else _SHA).fullmatch(token))


def _maximum(kind, limits):
    if kind in {"publication_payload", "part"}:
        return sync_state.MAX_OUTBOX_BYTES
    if kind == "outbox":
        return limits.max_outbox_bytes
    if kind in {"sources", "sources_v2"}:
        return limits.max_source_bytes
    if kind in _SENDER_KINDS:
        return limits.max_sender_catalog_bytes if kind == "sender_version" else limits.max_sender_record_bytes
    return limits.max_state_bytes


def _capture(profile, limits, check):
    budget = _Budget(limits, check)
    records, directories, issues, bindings, entries = [], [], [], [], []
    data = profile.database.parent
    for kind in _REGULAR_KINDS:
        path = data if kind in _FIXED_FILES else data / _DIRECTORIES[kind]
        try:
            budget.check()
            try:
                directory = safe._Directory(path, base=data).__enter__()
            except FileNotFoundError:
                directories.append((kind, "absent"))
                entries.append((kind, ()))
                bindings.append((kind, "absent"))
                continue
            except (OSError, safe.SyncError):
                raise _Invalid("HOME_UNSAFE_PATH") from None
            try:
                identity = tuple((str(p), dev, ino) for p, dev, ino in directory.identities)
                directories.append((kind, "present"))
                if kind in _FIXED_FILES:
                    names = list(_FIXED_FILES[kind])
                else:
                    names = []
                    iterator = _io(lambda: os.scandir(directory.fd if os.name != "nt" else safe._native_path(path)))
                    with iterator:
                        while True:
                            try:
                                entry = _io(lambda: next(iterator))
                            except StopIteration:
                                break
                            budget.file()
                            names.append(entry.name)
                    names.sort()
                entries.append((kind, tuple(names)))
                bindings.append((kind, "directory", identity, tuple(names)))
                for name in names:
                    budget.check()
                    if kind in _FIXED_FILES:
                        budget.file()
                    elif kind in (*_SENDER_KINDS, "source_root") and (name in _STRUCTURAL_NAMES.get(kind, set())
                            or sync_no_resend._PART.fullmatch(name)):
                        info = _io(lambda: directory.entry_stat(name))
                        _io(lambda: safe._safe_stat(info, directory=name in _STRUCTURAL_NAMES.get(kind, set())))
                        bindings.append((kind, name, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)))
                        continue
                    elif not _record_name(kind, name):
                        if kind in (*_SENDER_KINDS, "source_root") or kind in {"history", "sources", "sources_v2"} and name.endswith(".json"):
                            records.append(CapturedRecord(kind, name, error="HOME_RECORD_NAME_INVALID"))
                        continue
                    maximum = _maximum(kind, limits)
                    try:
                        raw, fingerprint = _read(directory, name, maximum, budget, allow_missing=kind in _FIXED_FILES)
                        records.append(CapturedRecord(kind, name, raw))
                        bindings.append((kind, name, fingerprint))
                    except _Invalid as exc:
                        records.append(CapturedRecord(kind, name, error=exc.code))
                        bindings.append((kind, name, exc.code))
                        if exc.code in {"HOME_TOTAL_BYTES_LIMIT", "HOME_DEADLINE_LIMIT"}:
                            raise
                _io(directory.verify)
            finally:
                directory.__exit__(None, None, None)
        except _Invalid as exc:
            issues.append((kind, exc.code))
            bindings.append((kind, "error", exc.code))
    _capture_publication_payloads(profile, limits, check, records, directories, issues, entries, bindings)
    # Issues describe scoped failures, not one count per captured filename.
    # Keep every name, raw record and physical binding while aggregating the
    # same failure; permitted internal parts must not exhaust the global
    # directory-issue allowance and block unrelated report namespaces.
    return CapturedBundle(tuple(records), tuple(directories), tuple(dict.fromkeys(issues)), tuple(entries)), tuple(bindings)


def _payload_limits(limits):
    from .sync_source_publication_local2 import MAX_PAYLOAD_FILES, MAX_PAYLOAD_TOTAL_BYTES, PAYLOAD_DEADLINE_SECONDS
    return replace(limits, max_files=min(limits.max_files, MAX_PAYLOAD_FILES) + 32,
        max_total_bytes=min(limits.max_total_bytes, MAX_PAYLOAD_TOTAL_BYTES),
        deadline_seconds=PAYLOAD_DEADLINE_SECONDS)


def _payload_part(record):
    from .sync_source_authority import _PART
    from .sync_source_publication_local2 import PAYLOAD_DIRECTORY, PAYLOAD_FAMILY
    if record.kind == "publication_payload":
        return bool(_PART.fullmatch(record.name))
    if record.kind != "part":
        return False
    for prefix in (PAYLOAD_FAMILY + "/", PAYLOAD_DIRECTORY + "/"):
        if record.name.startswith(prefix) and _PART.fullmatch(record.name[len(prefix):]):
            return True
    return False


def _capture_publication_payloads(profile, limits, check, records, directories, issues, entries, bindings):
    """Independent body namespace, without changing legacy absence bindings."""
    from .sync_source_authority import _PART, MAX_INTERNAL_PARTS
    from .sync_source_publication_local2 import PAYLOAD_DIRECTORY, PAYLOAD_VERSION
    data, budget = profile.database.parent, _Budget(_payload_limits(limits), check)
    try:
        outer = safe._Directory(data / PAYLOAD_DIRECTORY, base=data).__enter__()
    except FileNotFoundError:
        return
    except (OSError, safe.SyncError):
        issues.append(("publication_payload_root", "HOME_UNSAFE_PATH"))
        bindings.append(("publication_payload_root", "error", "HOME_UNSAFE_PATH"))
        return
    try:
        directories.append(("publication_payload_root", "present"))
        names = []
        with _io(lambda: os.scandir(outer.fd if os.name != "nt" else safe._native_path(outer.path))) as iterator:
            for entry in iterator:
                budget.check()
                if len(names) >= MAX_INTERNAL_PARTS + 1:
                    raise _Invalid("HOME_FILE_COUNT_LIMIT")
                names.append(entry.name)
                info = _io(lambda: outer.entry_stat(entry.name))
                _io(lambda: safe._safe_stat(info, directory=entry.name == PAYLOAD_VERSION))
                if entry.name != PAYLOAD_VERSION:
                    issues.append(("publication_payload_root", "HOME_PUBLICATION_PART_UNKNOWN"
                        if _PART.fullmatch(entry.name) else "HOME_RECORD_NAME_INVALID"))
                    if _PART.fullmatch(entry.name):
                        budget.file()
                        raw, fingerprint = _read(outer, entry.name, sync_state.MAX_OUTBOX_BYTES, budget)
                        records.append(CapturedRecord("part", PAYLOAD_DIRECTORY + "/" + entry.name, raw))
                        bindings.append(("part", PAYLOAD_DIRECTORY + "/" + entry.name, fingerprint))
        names.sort()
        entries.append(("publication_payload_root", tuple(names)))
        bindings.append(("publication_payload_root", "directory",
            tuple((str(path), dev, ino) for path, dev, ino in outer.identities), tuple(names)))
        try:
            inner = safe._Directory(outer.path / PAYLOAD_VERSION, parent=outer).__enter__()
        except FileNotFoundError:
            directories.append(("publication_payload", "absent"))
            entries.append(("publication_payload", ()))
            bindings.append(("publication_payload", "absent"))
            return
        except (OSError, safe.SyncError):
            raise _Invalid("HOME_UNSAFE_PATH") from None
        try:
            directories.append(("publication_payload", "present"))
            names, parts = [], 0
            with _io(lambda: os.scandir(inner.fd if os.name != "nt" else safe._native_path(inner.path))) as iterator:
                for entry in iterator:
                    budget.file()
                    names.append(entry.name)
                    is_part = bool(_PART.fullmatch(entry.name))
                    if is_part:
                        parts += 1
                        if parts > MAX_INTERNAL_PARTS:
                            raise _Invalid("HOME_FILE_COUNT_LIMIT")
                    if not is_part and not _record_name("publication_payload", entry.name):
                        records.append(CapturedRecord("publication_payload", entry.name, error="HOME_RECORD_NAME_INVALID"))
                        bindings.append(("publication_payload", entry.name, "HOME_RECORD_NAME_INVALID"))
                        continue
                    try:
                        raw, fingerprint = _read(inner, entry.name, sync_state.MAX_OUTBOX_BYTES, budget)
                        records.append(CapturedRecord("publication_payload", entry.name, raw))
                        bindings.append(("publication_payload", entry.name, fingerprint))
                    except _Invalid as error:
                        records.append(CapturedRecord("publication_payload", entry.name, error=error.code))
                        bindings.append(("publication_payload", entry.name, error.code))
                        if error.code in {"HOME_TOTAL_BYTES_LIMIT", "HOME_DEADLINE_LIMIT"}:
                            raise
            names.sort()
            entries.append(("publication_payload", tuple(names)))
            bindings.append(("publication_payload", "directory",
                tuple((str(path), dev, ino) for path, dev, ino in inner.identities), tuple(names)))
            _io(inner.verify)
        finally:
            inner.__exit__(None, None, None)
        _io(outer.verify)
    except _Invalid as error:
        issues.append(("publication_payload", error.code))
        bindings.append(("publication_payload", "error", error.code))
    finally:
        outer.__exit__(None, None, None)


class HomeCapture:
    def __init__(self, profile, guard, bundle, bindings, limits, check):
        self.bundle = bundle
        self._profile, self._guard, self._bindings = profile, guard, bindings
        self._limits, self._check, self._active = limits, check, True

    @property
    def rawbindings(self):
        """Private read-consistency evidence, never a semantic GC CAS token."""
        return deepcopy(self._bindings)

    def verify_source(self):
        if not self._active:
            raise HomeRootError("HOME_CAPTURE_CLOSED")
        if lifecycle.require_guard(self._profile) is not self._guard:
            raise HomeRootError("HOME_GUARD_CHANGED")
        _bundle, bindings = _capture(self._profile, self._limits, self._check)
        if bindings != self._bindings:
            _changed()
        self._guard.verify()
        return self.rawbindings


@contextmanager
def capture_locked(profile, *, limits=None, check_cancelled=None):
    limits = HomeLimits() if limits is None else limits
    if not isinstance(limits, HomeLimits) or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid capture options")
    guard = lifecycle.require_guard(profile)
    check = check_cancelled or (lambda: None)
    bundle, bindings = _capture(profile, limits, check)
    capture = HomeCapture(profile, guard, bundle, bindings, limits, check)
    try:
        yield capture
        capture.verify_source()
    finally:
        capture._active = False


def _decode(record, budget):
    if type(record.raw) is not bytes:
        raise _Invalid("HOME_JSON_INVALID")
    maximum = _maximum(record.kind, budget.limits)
    if len(record.raw) > maximum:
        raise _Invalid("HOME_FILE_BYTES_LIMIT")
    budget.bytes(len(record.raw))
    try:
        value = sync_state._decode(record.raw)
        stack = [(value, 0)]
        while stack:
            budget.check()
            item, depth = stack.pop()
            budget.nodes += 1
            if budget.nodes > budget.limits.max_json_nodes or depth > budget.limits.max_json_depth:
                raise _Invalid("HOME_JSON_LIMIT")
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, _Invalid):
            raise
        raise _Invalid("HOME_JSON_INVALID") from None


def _state(value, identity, *, history=False):
    if type(value) is not dict:
        raise ValueError("Invalid state")
    value = deepcopy(value)
    if history:
        if value.get("format_version") in (3, 4, 5):
            # Terminal proofs authorize only their own pointer transition. They
            # never retire the original work/cache/publication reference roots.
            value = sync_state.validate_source_history(value)
            value.pop("reason")
            value.pop("source_terminal")
        else:
            allowed = (_HISTORY_V2_FIELDS,) if value.get("format_version") == 2 else (_HISTORY_FIELDS, _HISTORY_FIELDS - {"legacy_pending_stream"})
            if (set(value) not in allowed
                    or value.pop("reason") not in {"recovery", "claim_changed", "epoch_changed"}):
                raise ValueError("Invalid history")
        legacy_cache = value.get("uploaded_media", {})
        value.update(session_id=_UUID, lease=None, seeded=False, published_version=None,
                     published_content_sha256=None, published_reports_sha256=None, uploaded_media=legacy_cache,
                     applied_recovery_generation=0,
                     status={"state": "idle", "error_code": None, "updated_at": _STAMP,
                             "last_success_at": None, "retry_at": None})
    if set(value) == sync_state._FIELDS - sync_state._RECOVERY_DEFAULTS.keys():
        value.update(deepcopy(sync_state._RECOVERY_DEFAULTS))
        if value.get("pending_publication") is not None:
            value["legacy_pending_stream"] = deepcopy(value.get("inbound", {}).get("stream"))
    elif set(value) == sync_state._FIELDS - {"legacy_pending_stream"}:
        value["legacy_pending_stream"] = (deepcopy(value.get("inbound", {}).get("stream"))
                                           if value.get("pending_publication") is not None else None)
    if type(value.get("recovery")) is dict and "stream" not in value["recovery"]:
        stream = value.get("inbound", {}).get("stream")
        if stream is None:
            # A recovery attempt is a root even when legacy namespace proof is
            # missing. Do not copy StateStore's resume normalization that drops it.
            raise ValueError("Unproven legacy recovery stream")
        value["recovery"]["stream"] = deepcopy(stream)
        if value.get("recovery_attempt") is not None:
            value["recovery_attempt"]["stream"] = deepcopy(stream)
    sync_state._validate(value)
    if (value["binding"]["home_library_id"] != identity["library_id"]
            or value["binding"]["home_node_id"] != identity["node_id"]):
        raise ValueError("Foreign source identity")
    return value


def current_binding(bundle, *, source_identity, limits=None):
    """Actual captured current State binding; never derive it from a facility.

    Missing, invalid or foreign current State returns None. Histories cannot
    substitute an expected live peer, and this representation grants no owner.
    """
    if not isinstance(bundle, CapturedBundle) or source_identity is None:
        return None
    candidates = [record for record in bundle.records if isinstance(record, CapturedRecord)
        and record.kind == "state" and record.name == "sync-state.json"]
    if len(candidates) != 1 or candidates[0].raw is None or candidates[0].error:
        return None
    try:
        budget = _Budget(HomeLimits() if limits is None else limits)
        state = _state(_decode(candidates[0], budget), source_identity)
        return deepcopy(state["binding"])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return None


def has_authority_refs(bundle, *, source_identity, limits=None):
    """Actual State5/history5 refs require a facility observation even if absent."""
    if not isinstance(bundle, CapturedBundle) or source_identity is None:
        return False
    budget = _Budget(HomeLimits() if limits is None else limits)
    for record in bundle.records:
        if isinstance(record, CapturedRecord) and record.kind in {"state", "history"} and record.raw is not None and not record.error:
            try:
                state = _state(_decode(record, budget), source_identity, history=record.kind == "history")
                if state["format_version"] == 5 and any(state[name] is not None for name in
                        ("source_authority", "source_admission", "source_recovery_pending")):
                    return True
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
                # The ordinary Home parser retains its existing bad-record
                # blockers. This helper neither clears those nor invents refs.
                continue
    return False


def _outbox(value, name, identity):
    if type(value) is not dict:
        raise ValueError("Invalid outbox")
    if set(value) == {"snapshot", "claims"}:
        value = {**value, "recovery_generation": 0}
    if set(value) == {"snapshot", "claims", "recovery_generation"}:
        value = {**value, "recovery_stream": None}
    sync_state._exact(value, ("snapshot", "claims", "recovery_generation", "recovery_stream"))
    sync_state._integer(value["recovery_generation"], minimum=0)
    sync_state._cursor({"stream": value["recovery_stream"], "after": 0, "through": None})
    snapshot = sync_snapshot.validate_snapshot(value["snapshot"])
    claims = _bindings(value["claims"], {v["video_id"] for v in snapshot["videos"]})
    if (value["snapshot"] != snapshot or value["claims"] != claims
            or snapshot["publication_id"] != name[:-5]
            or snapshot["version"]["library_id"] != identity["library_id"]):
        raise ValueError("Outbox identity mismatch")
    sync_reports.validate_reports(snapshot["reports"], snapshot["version"])
    for claim in claims:
        if "origin" in claim and (claim["origin"]["source_library_id"] != identity["library_id"]
                                   or claim["origin"]["source_node_id"] != identity["node_id"]):
            raise ValueError("Outbox origin mismatch")
    return value


def _payload_value(record, budget, identity):
    """Read the UUID from the actual body; a SHA filename is never its UUID."""
    value = _decode(record, budget)
    sync_state._exact(value, ("snapshot", "claims", "recovery_generation", "recovery_stream"))
    identifier = value["snapshot"]["publication_id"]
    _uuid(identifier)
    value = _outbox(value, identifier + ".json", identity)
    raw = sync_state._bounded(value, maximum=sync_state.MAX_OUTBOX_BYTES,
        depth=sync_snapshot.MAX_ENVELOPE_DEPTH + 1)
    if raw != record.raw:
        raise _Invalid("HOME_PUBLICATION_PAYLOAD_UNPROVEN")
    return value


def _payload_media(record, budget, collector, identity):
    """Preparation bodies retain references, including readable original parts."""
    try:
        budget.file()
        if record.error:
            raise _Invalid(record.error)
        value = _payload_value(record, budget, identity)
        # Typed facts survive a mismatched SHA filename or an unpublished part.
        for video in value["snapshot"]["videos"]:
            collector.media(record, video["media"])
        if _payload_part(record):
            raise _Invalid("HOME_PUBLICATION_PART_UNKNOWN")
        if record.kind != "publication_payload" or not _record_name(record.kind, record.name):
            raise _Invalid("HOME_PUBLICATION_PAYLOAD_UNPROVEN")
        if hashlib.sha256(record.raw).hexdigest() != record.name[:-5]:
            raise _Invalid("HOME_PUBLICATION_PAYLOAD_UNPROVEN")
        collector.binding(record, "valid", {"publication_id": value["snapshot"]["publication_id"],
            "sha256": record.name[:-5], "byte_size": len(record.raw),
            "claims_sha256": _hash(value["claims"]), "media": [v["media"] for v in value["snapshot"]["videos"]]})
        return value
    except (ValueError, safe.SyncError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        code = error.code if isinstance(error, _Invalid) else "HOME_PUBLICATION_PAYLOAD_UNPROVEN"
        collector.block(record, code, media_only=True)
        collector.binding(record, "unknown", {"code": code})
        return None


def _publication_payload_edge(row):
    from .sync_source_publication_local2 import PAYLOAD_FAMILY
    edges = [edge for edge in row["refs"] if type(edge) is dict and edge.get("kind") == "publication_payload"]
    if len(edges) != 1:
        raise ValueError("Publication payload selection missing")
    edge = edges[0]
    if (set(edge) != {"kind", "format_version", "family", "sha256", "byte_size", "claims_sha256"}
            or type(edge["format_version"]) is not int or edge["format_version"] != 1
            or edge["family"] != PAYLOAD_FAMILY
            or type(edge["sha256"]) is not str or not _SHA.fullmatch(edge["sha256"])
            or type(edge["claims_sha256"]) is not str or not _SHA.fullmatch(edge["claims_sha256"])
            or type(edge["byte_size"]) is not int or not 2 <= edge["byte_size"] <= sync_state.MAX_OUTBOX_BYTES):
        raise ValueError("Invalid publication payload edge")
    return edge


def _publication_payload_origin(row, value, rows, identity):
    refs = [edge for edge in row["refs"] if type(edge) is dict
        and edge.get("kind") == "authority_record" and edge.get("record_kind") == "origin"]
    if len(refs) != 1:
        raise ValueError("Original publication origin missing")
    ref = refs[0]
    if set(ref) != {"kind", "format_version", "record_kind", "record_id", "record_sha256"}:
        raise ValueError("Invalid original publication origin")
    _uuid(ref["record_id"])
    if type(ref["format_version"]) is not int or ref["format_version"] != 1 or not _SHA.fullmatch(ref["record_sha256"]):
        raise ValueError("Invalid original publication origin")
    origins = [origin for origin in rows if origin["kind"] == "origins" and origin["state"] == "committed"
        and (origin["record_id"], origin["record_sha256"], origin["core_sha256"])
        == (ref["record_id"], ref["record_sha256"], row["core_sha256"])]
    if len(origins) != 1:
        raise ValueError("Original publication core missing")
    cores = [edge for edge in origins[0]["refs"] if type(edge) is dict and edge.get("kind") == "source_core"]
    fields = {"kind", "format_version", "item_id", "submitted_url_sha256", "root_job_id",
              "continuation_id", "execution_generation", "local_epoch", "resolution_input_sha256"}
    if len(cores) != 1 or set(cores[0]) != fields:
        raise ValueError("Invalid original publication core")
    core = cores[0]
    if (type(core["format_version"]) is not int or core["format_version"] != 1
            or any(type(core[name]) is not int or core[name] < 1 for name in
                ("item_id", "root_job_id", "execution_generation", "local_epoch"))
            or any(type(core[name]) is not str or not _SHA.fullmatch(core[name]) for name in
                ("submitted_url_sha256", "resolution_input_sha256"))):
        raise ValueError("Invalid original publication core")
    _uuid(core["continuation_id"])
    selected = [binding for binding in value["claims"] if binding["claim"]["item_id"] == core["item_id"]]
    if len(selected) != 1 or "origin" not in selected[0]:
        raise ValueError("Publication original claim missing")
    origin = selected[0]["origin"]
    if ((origin["source_library_id"], origin["source_node_id"])
            != (identity["library_id"], identity["node_id"])
            or hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]):
        raise ValueError("Publication original source mismatch")
    descriptors = {video["video_id"]: video["media"] for video in value["snapshot"]["videos"]}
    if origin["media_sha256"] != descriptors[selected[0]["video_id"]]["sha256"]:
        raise ValueError("Publication original media mismatch")


def _mapping(value, name, identity, *, generation=None):
    return sync_sources.validate_source_record(value,
        mapping_id=name[:-5] if generation is None else None, mapping_generation=generation,
        source_library_id=identity["library_id"], source_node_id=identity["node_id"])


class _Collector:
    def __init__(self, root_ids, member_id):
        if (type(root_ids) is not dict or set(root_ids) != set(inputs.NAMESPACES)
                or any(type(v) is not str or not _SHA.fullmatch(v) for v in root_ids.values())
                or type(member_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", member_id)):
            raise ValueError("Invalid home reference binding")
        self.roots, self.member = dict(root_ids), member_id
        self.refs, self.selectors, self.blockers, self.bindings = [], [], [], []

    def origin(self, record):
        return {"member_id": self.member, "source_kind": "home", "source_id": _hash((record.kind, record.name))}

    def block(self, record, code, *, media_only=False):
        code = code if type(code) is str and code in _CODES else "HOME_RECORD_INVALID"
        for ns in ("sync_media",) if media_only else _AFFECTED[record.kind]:
            self.blockers.append(inputs.Blocker(ns, self.roots[ns], **self.origin(record), code=code,
                                                locator=_hash((record.kind, record.name, code))))

    def selector(self, record, video, sha=None, *, edge=None):
        protocol.validate_video_id(video)
        if sha is not None and not _SHA.fullmatch(sha):
            raise ValueError("Invalid selector")
        value = inputs.Selector("sync_media", self.roots["sync_media"], video,
            **self.origin(record), locator=_hash((video, sha, edge)), sha256=sha, extension=None)
        self.selectors.append(value)
        return value

    def media(self, record, descriptor):
        descriptor = protocol.validate_media_descriptor(descriptor)
        key = f"sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
        value = inputs.Reference("sync_media", self.roots["sync_media"], key, **self.origin(record),
            locator=_hash(key), sha256=descriptor["sha256"], byte_size=descriptor["byte_size"])
        self.refs.append(value)
        return value

    def binding(self, record, state, projection):
        self.bindings.append(inputs.SourceBinding(**self.origin(record), section=record.kind, state=state,
                                                  sha256=_hash(projection), row_count=len(projection)))

    def result(self):
        ordered = lambda values: tuple(sorted(set(values), key=lambda v: inputs.canonical_bytes(asdict(v))))
        return inputs.InputScan(ordered(self.refs), ordered(self.selectors), ordered(self.blockers), ordered(self.bindings))


def _sender_evidence(*, bundle, profile, identity, contexts, records, states, entries, budget):
    """Pure evidence verification after one bounded directory closure capture."""
    for kind in _SENDER_KINDS:
        if kind not in states or kind not in entries:
            raise _Invalid("HOME_CAPTURE_INCOMPLETE")
        if states[kind] == "absent" and entries[kind]:
            raise _Invalid("HOME_CAPTURE_INCOMPLETE")
        if kind in _SENDER_DIRECTORY_LIMITS and len(entries[kind]) > _SENDER_DIRECTORY_LIMITS[kind]:
            raise _Invalid("HOME_FILE_COUNT_LIMIT")
    raw_records = {kind: {} for kind in ("publications", "receipts", "references")}
    total = 0
    for (kind, name), record in records.items():
        budget.check()
        if record.error:
            raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
        if record.raw is None:
            if kind != "sender_anchors":
                raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
            continue
        total += len(record.raw)
        if total > budget.limits.max_sender_total_bytes:
            raise _Invalid("HOME_TOTAL_BYTES_LIMIT")
        _decode(record, budget)  # Apply this round's JSON/depth/byte limits too.
        if kind in raw_records:
            raw_records[kind][name] = record.raw
    def raw(kind, name):
        record = records.get((kind, name))
        return None if record is None else record.raw
    contract_raw, seal_raw = (raw("sender_anchors", name) for name in _FIXED_FILES["sender_anchors"])
    catalog_raw = raw("sender_version", "catalog.json")
    virgin = all(value is None for value in (contract_raw, seal_raw, catalog_raw)) and not any(raw_records.values())
    if virgin:
        if any(states[kind] != "absent" for kind in _SENDER_KINDS if kind != "sender_anchors"):
            raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
    else:
        if any(states[kind] != "present" for kind in _SENDER_KINDS):
            raise _Invalid("HOME_CAPTURE_INCOMPLETE")
        for kind in ("sender_root", "sender_version", "publications", "receipts", "references"):
            expected = _STRUCTURAL_NAMES.get(kind, set())
            if kind == "sender_version":
                expected = expected | {"catalog.json"}
            elif kind in raw_records:
                expected = set(raw_records[kind])
            names = set(entries[kind])
            if not expected <= names or any(name not in expected and not sync_no_resend._PART.fullmatch(name) for name in names):
                raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
    contracts = [value["sender_contract"] for _, value in contexts if value["format_version"] >= 2]
    if contracts and any(value != contracts[0] for value in contracts):
        raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
    proof = sync_no_resend.validate_frozen_evidence(contract_raw, seal_raw, catalog_raw, raw_records,
        profile=profile, home_identity=identity, state_contract=contracts[0] if contracts else None)
    if proof["contract"] is not None:
        for record, value in contexts:
            if record.kind == "state" and any(value["binding"][key] != proof["contract"][key] for key in sync_no_resend._PEER):
                raise _Invalid("HOME_SENDER_EVIDENCE_INVALID")
    return proof


def _marker_matches(request, found, contexts):
    if found is None:
        return False
    record, envelope = found
    metadata, binding = request["metadata"], request["binding"]
    snapshot = envelope["snapshot"]
    if (type(record.raw) is not bytes or len(record.raw) != metadata["byte_size"]
            or hashlib.sha256(record.raw).hexdigest() != metadata["sha256"]
            or snapshot["publication_id"] != metadata["publication_id"]
            or snapshot["version"] != binding["source_version"]
            or envelope["recovery_generation"] != binding["recovery_generation"]
            or (envelope["recovery_stream"] is not None and envelope["recovery_stream"] != binding["recovery_stream"])
            or (envelope["recovery_stream"] is None and envelope["recovery_generation"] != 0)
            or publication_payload_sha256(snapshot, envelope["claims"],
                recovery_generation=envelope["recovery_generation"], recovery_stream=binding["recovery_stream"]) != request["payload_sha256"]):
        return False
    # A committed immutable request is the original saved stream fact for a
    # legacy generation-zero envelope; never use current recovery/work to fill it.
    for _, context in contexts:
        pending, attempt = context["pending_publication"], context["recovery_attempt"]
        if pending is not None and pending["publication_id"] == metadata["publication_id"]:
            if pending != metadata or any(context["binding"][key] != request[key] for key in sync_no_resend._PEER):
                return False
        if attempt is not None and attempt["publication_id"] == metadata["publication_id"]:
            if (attempt["version"] != binding["source_version"] or attempt["generation"] != binding["recovery_generation"]
                    or attempt["stream"] != binding["recovery_stream"]
                    or any(context["binding"][key] != request[key] for key in sync_no_resend._PEER)):
                return False
    return True


def _selected_edge(selector, *, contract, contexts, mappings, outboxes, publications, markers, edges):
    from .sync_local_retirement import work_key
    kind = selector["kind"]
    if kind == "source_map":
        key = selector["mapping_generation"], selector["origin_mapping_id"]
        found = mappings.get(key)
        if found is None or hashlib.sha256(found[0].raw).hexdigest() != selector["raw_sha256"]:
            raise _Invalid("HOME_RETIREMENT_UNPROVEN")
        return edges[(found[0].kind, found[0].name, "source_map")], key
    holder_kind = "history" if kind == "history_work" else selector["holder_kind"]
    holder_name = selector["history_name"] if kind == "history_work" else selector["holder_name"]
    found = next(((record, value) for record, value in contexts if record.kind == holder_kind and record.name == holder_name), None)
    if found is None:
        raise _Invalid("HOME_RETIREMENT_UNPROVEN")
    record, value = found
    if any(value["binding"][key] != contract[key] for key in sync_no_resend._PEER):
        raise _Invalid("HOME_RETIREMENT_UNPROVEN")
    digest = hashlib.sha256(record.raw).hexdigest()
    if kind == "history_work":
        if digest != selector["raw_sha256"] or work_key(value["work"]) != selector["work_key"]:
            raise _Invalid("HOME_RETIREMENT_UNPROVEN")
        return edges[(holder_kind, holder_name, "work")], None
    video_id = selector["video_id"]
    if kind == "legacy_cache":
        candidate = value["uploaded_media"].get(video_id)
        key = holder_kind, holder_name, kind, video_id
        matching = candidate == selector["descriptor"]
    else:
        original_marker = publications.get(selector["publication_id"])
        if (original_marker is None or selector["publication_id"] not in markers
                or original_marker["record"]["binding"] != selector["binding"]):
            raise _Invalid("HOME_RETIREMENT_UNPROVEN")
        metadata = original_marker["record"]["metadata"]
        item = value.get("publication_media", {}).get(selector["publication_id"])
        candidate = None if item is None else item["media"].get(video_id)
        key = holder_kind, holder_name, kind, selector["publication_id"], video_id
        matching = candidate == selector["descriptor"] and item["binding"] == selector["binding"] if item is not None else False
        if matching:
            original = outboxes.get(selector["publication_id"])
            binding = selector["binding"]
            if (original is None or len(original[0].raw) != metadata["byte_size"]
                    or hashlib.sha256(original[0].raw).hexdigest() != metadata["sha256"]
                    or original[1]["snapshot"]["version"] != binding["source_version"]
                    or original[1]["recovery_generation"] != binding["recovery_generation"]
                    or (original[1]["recovery_stream"] is not None and original[1]["recovery_stream"] != binding["recovery_stream"])
                    or selector["descriptor"] not in [video["media"] for video in original[1]["snapshot"]["videos"]]):
                raise _Invalid("HOME_RETIREMENT_UNPROVEN")
    if not matching and holder_kind == "state" and digest != selector["holder_raw_sha256"]:
        # The already-authorized live cache entry may have been detached. Keep
        # any new entry; this proof never releases a separate history holder.
        return None, None
    if not matching or digest != selector["holder_raw_sha256"]:
        raise _Invalid("HOME_RETIREMENT_UNPROVEN")
    return edges[key], None


def _map_in_use(key, origin, *, contexts, outboxes, markers, removed, edges, budget):
    generation, mapping_id = key
    for record, value in contexts:
        budget.check()
        work = value["work"]
        if work is None or edges.get((record.kind, record.name, "work")) in removed:
            continue
        active_generation = work.get("source_mapping_generation")
        if active_generation is not None:
            if active_generation == generation:
                return True
        elif work.get("source_mapping_id") is not None:
            if generation is None and work["source_mapping_id"] == mapping_id:
                return True
        elif work["video_id"] == origin["video_id"]:
            return True
    for publication_id, (_, envelope) in outboxes.items():
        budget.check()
        if publication_id in markers:
            continue
        # Outboxes have no independently saved local generation. A wire origin
        # or matching descriptor therefore retains every related map instance.
        if any(entry.get("origin", {}).get("mapping_id") == mapping_id for entry in envelope["claims"]):
            return True
        if any(video["media"]["video_id"] == origin["video_id"] and video["media"]["sha256"] == origin["media_sha256"]
               for video in envelope["snapshot"]["videos"]):
            return True
    return False


def _retire_frozen(bundle, *, profile, identity, contexts, mappings, outboxes, sender_records,
                   directory_states, directory_entries, edges, collector, budget):
    control = CapturedRecord("sender_anchors", "retirement_proof")
    try:
        proof = _sender_evidence(bundle=bundle, profile=profile, identity=identity, contexts=contexts,
            records=sender_records, states=directory_states, entries=directory_entries, budget=budget)
        catalog = proof["catalog"]
        if catalog is None:
            return
        collector.binding(control, "valid", {"proof_version": 1, "contract": proof["contract"],
            "catalog_sha256": _hash(catalog), "incomplete": proof["incomplete"]})
        # Missing or malformed base capture can hide an independent dependency.
        # Retain the complete baseline until that capture is fully proven.
        if collector.blockers:
            return
        markers = set()
        for publication_id, entry in catalog["publications"].items():
            budget.check()
            if entry["state"] != "committed" or not _marker_matches(entry["record"], outboxes.get(publication_id), contexts):
                raise _Invalid("HOME_RETIREMENT_UNPROVEN")
            markers.add(publication_id)
        removed, selected_maps = set(), set()
        for entry in catalog["references"].values():
            budget.check()
            if entry["state"] != "committed":
                raise _Invalid("HOME_RETIREMENT_UNPROVEN")
            for selector in entry["record"]["selected"]:
                budget.check()
                selected, mapping = _selected_edge(selector, contract=entry["record"], contexts=contexts,
                    mappings=mappings, outboxes=outboxes, publications=catalog["publications"], markers=markers, edges=edges)
                if selected is not None:
                    removed.add(selected)
                if mapping is not None:
                    selected_maps.add(mapping)
        for key in selected_maps:
            if _map_in_use(key, mappings[key][1], contexts=contexts, outboxes=outboxes, markers=markers,
                           removed=removed, edges=edges, budget=budget):
                raise _Invalid("HOME_RETIREMENT_DEPENDENCY_ACTIVE")
        # The plan is frozen before any subtraction. One unproven selection
        # keeps every original edge and emits a controlled namespace blocker.
        drop_refs = {value for key, value in edges.items() if key[0] == "outbox" and key[1][:-5] in markers}
        collector.refs[:] = [value for value in collector.refs if value not in drop_refs]
        collector.selectors[:] = [value for value in collector.selectors if value not in removed]
        collector.binding(control, "retired", {"publications": sorted(markers),
            "references": sorted(catalog["references"]), "selected_sha256": _hash([
                entry["record"]["selected"] for entry in catalog["references"].values()])})
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        collector.block(control, exc.code if isinstance(exc, _Invalid) else "HOME_SENDER_EVIDENCE_INVALID")


def parse_bundle(bundle, *, profile, root_ids, member_id, source_identity, limits=None):
    """Pure parsing of captured bytes. Never discovers a source from config."""
    limits = HomeLimits() if limits is None else limits
    if (not isinstance(bundle, CapturedBundle) or not isinstance(limits, HomeLimits)
            or not isinstance(profile, lifecycle.StorageProfile) or type(source_identity) is not dict
            or set(source_identity) != {"library_id", "node_id"}):
        raise ValueError("Invalid home parser inputs")
    for value in source_identity.values():
        _uuid(value)
    budget, collector = _Budget(limits), _Collector(root_ids, member_id)
    outboxes, mappings, contexts, seen, sender_records, edges = {}, {}, [], set(), {}, {}
    if (type(bundle.records) is not tuple or type(bundle.directories) is not tuple
            or type(bundle.issues) is not tuple or type(bundle.directory_entries) is not tuple):
        raise ValueError("Invalid captured collections")
    payload_limits = _payload_limits(limits)
    ordinary_count = sum(record.kind not in {*_PAYLOAD_KINDS, "part"}
        for record in bundle.records if isinstance(record, CapturedRecord))
    payload_count = len(bundle.records) - ordinary_count
    if (ordinary_count > limits.max_files or payload_count > payload_limits.max_files
            or len(bundle.directories) > len(_KINDS)
            or len(bundle.issues) > len(_KINDS) or len(bundle.directory_entries) > len(_KINDS)):
        for kind in _KINDS:
            collector.block(CapturedRecord(kind, "directory"), "HOME_FILE_COUNT_LIMIT")
        return collector.result()
    covered = set()
    collector.binding(CapturedRecord("state", "parser_limits"), "valid", {
        "version": 2, "home": asdict(limits), "sender": {"records": sync_no_resend.MAX_RECORDS,
        "record_bytes": sync_no_resend.MAX_RECORD_BYTES, "catalog_bytes": sync_no_resend.MAX_CATALOG_BYTES,
        "total_bytes": sync_no_resend.MAX_TOTAL_BYTES, "deadline_seconds": sync_no_resend.DEADLINE_SECONDS,
        "directory_entries": _SENDER_DIRECTORY_LIMITS}})
    for kind, state in bundle.directories:
        if kind not in _KINDS or state not in {"absent", "present"}:
            raise ValueError("Invalid captured directory")
        if kind in covered:
            collector.block(CapturedRecord(kind, "directory"), "HOME_DUPLICATE_RECORD")
        covered.add(kind)
        collector.binding(CapturedRecord(kind, "directory"), state, [])
    for kind, code in bundle.issues:
        if kind not in _KINDS:
            raise ValueError("Invalid captured source")
        covered.add(kind)
        collector.block(CapturedRecord(kind, "directory"), code)
    payload_present = any(kind in _PAYLOAD_KINDS for kind in covered) or any(
        isinstance(record, CapturedRecord) and record.kind in {*_PAYLOAD_KINDS, "part"} for record in bundle.records)
    required = set(_REGULAR_KINDS) | (set(_PAYLOAD_KINDS) if payload_present else set())
    for kind in required - covered:
        collector.block(CapturedRecord(kind, "directory"), "HOME_CAPTURE_INCOMPLETE")
    directory_states = dict(bundle.directories)
    directory_entries = {}
    for kind, names in bundle.directory_entries:
        if (kind not in _KINDS or type(names) is not tuple or any(type(name) is not str or len(name) > 1024 for name in names)):
            raise ValueError("Invalid captured directory membership")
        maximum_names = payload_limits.max_files if kind in _PAYLOAD_KINDS else limits.max_files
        if kind in directory_entries or len(set(names)) != len(names) or len(names) > maximum_names:
            collector.block(CapturedRecord(kind, "directory"), "HOME_DUPLICATE_RECORD")
        directory_entries[kind] = names
        collector.binding(CapturedRecord(kind, "directory_membership"), directory_states.get(kind, "unknown"), list(names))
    state_records = [record for record in bundle.records if isinstance(record, CapturedRecord)
                     and record.kind == "state" and record.name == "sync-state.json"]
    if directory_states.get("state") == "present" and len(state_records) != 1:
        collector.block(CapturedRecord("state", "directory"), "HOME_CAPTURE_INCOMPLETE")
    payload_budget = _Budget(payload_limits)
    for record in bundle.records:
        if (not isinstance(record, CapturedRecord) or record.kind not in _KINDS or type(record.name) is not str
                or len(record.name) > 1024):
            raise ValueError("Invalid captured record")
        if record.kind in {"publication_payload", "part"}:
            started = time.monotonic()
            try:
                key = record.kind, record.name
                if key in seen or record.kind == "part" and not _payload_part(record):
                    collector.block(record, "HOME_PUBLICATION_PAYLOAD_UNPROVEN", media_only=True)
                    continue
                seen.add(key)
                if record.kind == "publication_payload" and directory_states.get(record.kind) != "present":
                    collector.block(record, "HOME_CAPTURE_INCOMPLETE", media_only=True)
                _payload_media(record, payload_budget, collector, source_identity)
            finally:
                # Body parsing has its own declared budget; ordinary State/map
                # records retain their released limits and deadline.
                budget.started += time.monotonic() - started
            continue
        try:
            budget.file()
            key = record.kind, record.name
            if key in seen:
                raise _Invalid("HOME_DUPLICATE_RECORD")
            seen.add(key)
            if record.kind in _SENDER_KINDS:
                sender_records[key] = record
                collector.binding(record, "captured", {"sha256": hashlib.sha256(record.raw).hexdigest()
                    if type(record.raw) is bytes else None, "byte_size": len(record.raw)
                    if type(record.raw) is bytes else None, "error": record.error if type(record.error) is str else None})
                if directory_states.get(record.kind) != "present" or record.error:
                    raise _Invalid(record.error if record.error else "HOME_CAPTURE_INCOMPLETE")
                continue
            if directory_states.get(record.kind) != "present":
                raise _Invalid("HOME_CAPTURE_INCOMPLETE")
            if record.error:
                raise _Invalid(record.error)
            if record.kind == "state" and record.name != "sync-state.json":
                raise _Invalid("HOME_RECORD_NAME_INVALID")
            if record.raw is None and record.kind == "state":
                collector.binding(record, "absent", [])
                continue
            if record.kind != "state" and not _record_name(record.kind, record.name):
                raise _Invalid("HOME_RECORD_NAME_INVALID")
            value = _decode(record, budget)
            if record.kind in {"state", "history"}:
                value = _state(value, source_identity, history=record.kind == "history")
                contexts.append((record, value))
            elif record.kind == "outbox":
                value = _outbox(value, record.name, source_identity)
                snapshot = value["snapshot"]
                outboxes[snapshot["publication_id"]] = (record, value)
                for video in snapshot["videos"]:
                    edges[(record.kind, record.name, video["video_id"])] = collector.media(record, video["media"])
                collector.binding(record, "valid", {"publication_id": snapshot["publication_id"],
                    "version": snapshot["version"], "media": [v["media"] for v in snapshot["videos"]],
                    "reports_inline": [{"name": r["name"], "sha256": r["sha256"]} for r in snapshot["reports"]],
                    "generation": value["recovery_generation"], "stream": value["recovery_stream"]})
            else:
                generation = record.name[:-5] if record.kind == "sources_v2" else None
                value = _mapping(value, record.name, source_identity, generation=generation)
                mappings[(generation, value["mapping_id"])] = (record, value)
                edges[(record.kind, record.name, "source_map")] = collector.selector(record, value["video_id"],
                    value["media_sha256"], edge=("source_map", generation, value["mapping_id"]))
                collector.binding(record, "valid", {**{key: value[key] for key in (
                    "mapping_id", "source_library_id", "source_node_id", "video_id", "media_sha256")},
                    "mapping_generation": generation, "raw_sha256": hashlib.sha256(record.raw).hexdigest()})
            budget.check()
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
            collector.block(record, exc.code if isinstance(exc, _Invalid) else "HOME_RECORD_INVALID")
    for record, value in contexts:
        try:
            budget.check()
            work = value["work"]
            if work is not None:
                edges[(record.kind, record.name, "work")] = collector.selector(record, work["video_id"], edge="work")
                mapping_id = work.get("source_mapping_id")
                mapping_key = work.get("source_mapping_generation"), mapping_id
                if mapping_id and (mapping_key not in mappings or mappings[mapping_key][1]["video_id"] != work["video_id"]):
                    collector.block(record, "HOME_SOURCE_MAP_MISSING", media_only=True)
            for video_id, descriptor in value["uploaded_media"].items():
                edges[(record.kind, record.name, "legacy_cache", video_id)] = collector.selector(record, video_id,
                    descriptor["sha256"], edge=("legacy_cache", video_id))
            # N01's new hints carry precise publication provenance. They
            # remain independent roots until N04's explicit retirement proof
            # and production reuse contract are connected.
            for publication_id, item in value.get("publication_media", {}).items():
                for video_id, descriptor in item["media"].items():
                    edges[(record.kind, record.name, "publication_cache", publication_id, video_id)] = collector.selector(
                        record, video_id, descriptor["sha256"], edge=("publication_cache", publication_id, video_id))
            pending = value["pending_publication"]
            if pending is not None:
                found = outboxes.get(pending["publication_id"])
                if (found is None or len(found[0].raw) != pending["byte_size"]
                        or hashlib.sha256(found[0].raw).hexdigest() != pending["sha256"]):
                    collector.block(record, "HOME_PENDING_OUTBOX_UNPROVEN")
                elif (found[1]["recovery_stream"] is not None
                      and found[1]["recovery_stream"]["library_id"] != value["binding"]["target_library_id"]):
                    collector.block(record, "HOME_PENDING_OUTBOX_UNPROVEN")
            attempt = value["recovery_attempt"]
            if attempt is not None:
                found = outboxes.get(attempt["publication_id"])
                if (found is None or found[1]["snapshot"]["version"] != attempt["version"]
                        or found[1]["recovery_generation"] != attempt["generation"]
                        or (found[1]["recovery_stream"] or value["legacy_pending_stream"]) != attempt["stream"]):
                    collector.block(record, "HOME_ATTEMPT_OUTBOX_UNPROVEN")
            projection = {"binding": {k: v for k, v in value["binding"].items() if k != "target_url"},
                "local_epoch": value["local_epoch"], "work": None if work is None else {
                    k: work[k] for k in ("item_id", "video_id", "source_mapping_id", "source_mapping_generation", "source_mapping_inputs") if k in work},
                "pending_publication": pending, "recovery_attempt": attempt, "recovery": value["recovery"],
                "legacy_pending_stream": value["legacy_pending_stream"], "uploaded_media": value["uploaded_media"]}
            if value["format_version"] >= 2:
                projection.update(sender_contract=value["sender_contract"], publication_media=value["publication_media"])
            if record.kind == "history":
                projection["raw_sha256"] = hashlib.sha256(record.raw).hexdigest()
            collector.binding(record, "valid", projection)
        except (ValueError, TypeError, KeyError) as exc:
            collector.block(record, exc.code if isinstance(exc, _Invalid) else "HOME_RECORD_INVALID")
    _retire_frozen(bundle, profile=profile, identity=source_identity, contexts=contexts, mappings=mappings,
        outboxes=outboxes, sender_records=sender_records, directory_states=directory_states,
        directory_entries=directory_entries, edges=edges, collector=collector, budget=budget)
    return collector.result()


def _legacy_pending_contexts(component, images, profile, identity, budget, collector):
    """Parse separately captured full States, never ordinary State/history rows.

    The physical reader owns the namespace/file proof. This pure comparison
    binds every supplied image to that reader's independent State-set digest.
    A damaged sibling does not erase independently readable typed roots.
    """
    from . import storage_legacy_pending_state17 as pending
    from . import sync_source_authority as authority
    marker = CapturedRecord("legacy_pending_state", "component")
    contexts, summaries, keys, total = [], [], [], 0
    invalid = False
    try:
        if (type(component) is not dict or set(component) != {"summary", "references", "unknown"}
                or type(images) is not tuple or len(images) > 2 * pending.MAX_FILES):
            raise ValueError("Invalid private pending inputs")
        summary = pending.normalize_summary(component["summary"],
            owner_profile_sha256=authority.profile_sha256(profile))
        if (type(component["references"]) is not list or type(component["unknown"]) is not list
                or summary["unknown_count"] != len(component["unknown"])
                or summary["unknown_sha256"] != _hash(component["unknown"])
                or summary["reference_set_sha256"] != _hash(component["references"])):
            raise ValueError("Invalid pending metadata")
        invalid = bool(component["unknown"])
        for image in images:
            budget.check()
            try:
                if (type(image) is not dict or set(image) != {"request_id", "phase", "state_json", "state_sha256"}
                        or image["phase"] not in {"before", "after"} or type(image["state_json"]) is not str):
                    raise ValueError("Invalid pending State image")
                _uuid(image["request_id"])
                raw = image["state_json"].encode("utf-8")
                total += len(raw)
                if (len(raw) > sync_state.MAX_STATE_BYTES or total > 2 * pending.MAX_TOTAL_BYTES
                        or hashlib.sha256(raw).hexdigest() != image["state_sha256"]):
                    raise ValueError("Invalid pending State digest")
                state = sync_state._decode(raw)
                if state["format_version"] != 5 or sync_state._validate(state) != raw:
                    raise ValueError("Invalid pending State encoding")
                state = _state(state, identity)
                key = image["request_id"], image["phase"]
                if key in keys:
                    raise ValueError("Duplicate pending image")
                keys.append(key)
                public = sync_state.source_readmit_projection(state)
                summaries.append({"request_id": image["request_id"], "phase": image["phase"],
                    "state_sha256": image["state_sha256"], "source_core_sha256": public["core_sha256"],
                    "refset_sha256": public["refset_sha256"]})
                record = CapturedRecord("legacy_pending_state", image["request_id"] + ":" + image["phase"])
                contexts.append((record, state))
                collector.binding(record, "captured", summaries[-1])
            except (ValueError, protocol.SyncError, TypeError, KeyError, AttributeError, UnicodeError,
                    OverflowError, RecursionError):
                invalid = True
        if _hash(summaries) != summary["state_set_sha256"]:
            invalid = True
    except (ValueError, protocol.SyncError, TypeError, KeyError, AttributeError, UnicodeError,
            OverflowError, RecursionError):
        invalid = True
    if invalid:
        collector.block(marker, "HOME_LEGACY_PENDING_STATE_UNPROVEN")
    return contexts, invalid


def _legacy_pending_roots(bundle, contexts, identity, budget, collector):
    """Permanent capsule media roots do not participate in pointer retirement."""
    from . import storage_legacy_pending_dependencies17 as dependencies
    # New dependency bodies have a separate count/byte envelope. Preserve the
    # ordinary Home parser's per-file, JSON node/depth and deadline limits;
    # a shared checkpoint outbox is decoded once in this independent pass.
    dependency_budget = _Budget(replace(budget.limits,
        max_files=dependencies.MAX_FILES, max_total_bytes=dependencies.MAX_TOTAL_BYTES), budget.check)
    dependency_budget.started = budget.started
    mappings, outboxes = {}, {}
    seen = {}
    for record in bundle.records:
        if not isinstance(record, CapturedRecord) or record.error or record.raw is None:
            continue
        if record.kind not in {"sources", "sources_v2", "outbox"}:
            continue
        key = record.kind, record.name
        if key in seen:
            if seen[key] != record.raw:
                collector.block(record, "HOME_LEGACY_PENDING_DEPENDENCY_UNPROVEN")
                return True
            continue
        seen[key] = record.raw
        try:
            dependency_budget.file()
            if record.kind in {"sources", "sources_v2"}:
                generation = record.name[:-5] if record.kind == "sources_v2" else None
                value = _mapping(_decode(record, dependency_budget), record.name, identity, generation=generation)
                mappings[(generation, value["mapping_id"])] = value
            elif record.kind == "outbox":
                value = _outbox(_decode(record, dependency_budget), record.name, identity)
                outboxes[value["snapshot"]["publication_id"]] = (record, value)
        except (ValueError, protocol.SyncError, TypeError, KeyError, AttributeError):
            continue
    invalid = False
    for record, state in contexts:
        budget.check()
        work = state["work"]
        if work is not None:
            collector.selector(record, work["video_id"], edge="work")
            mapping_id = work.get("source_mapping_id")
            if mapping_id is not None:
                mapping = mappings.get((work.get("source_mapping_generation"), mapping_id))
                if mapping is None or mapping["video_id"] != work["video_id"]:
                    collector.block(record, "HOME_SOURCE_MAP_MISSING", media_only=True)
                    invalid = True
                else:
                    collector.selector(record, mapping["video_id"], mapping["media_sha256"], edge="source_map")
        for video_id, descriptor in state["uploaded_media"].items():
            collector.selector(record, video_id, descriptor["sha256"], edge=("legacy_cache", video_id))
        for publication_id, item in state.get("publication_media", {}).items():
            for video_id, descriptor in item["media"].items():
                collector.selector(record, video_id, descriptor["sha256"],
                    edge=("publication_cache", publication_id, video_id))
        for field in ("pending_publication", "recovery_attempt"):
            selected = state[field]
            if selected is None:
                continue
            found = outboxes.get(selected["publication_id"])
            valid = found is not None
            if valid and field == "pending_publication":
                valid = (len(found[0].raw), hashlib.sha256(found[0].raw).hexdigest()) == (
                    selected["byte_size"], selected["sha256"])
            elif valid:
                valid = (found[1]["snapshot"]["version"], found[1]["recovery_generation"],
                    found[1]["recovery_stream"] or state["legacy_pending_stream"]) == (
                    selected["version"], selected["generation"], selected["stream"])
            if not valid:
                collector.block(record, "HOME_PENDING_OUTBOX_UNPROVEN"
                    if field == "pending_publication" else "HOME_ATTEMPT_OUTBOX_UNPROVEN")
                invalid = True
            if found is not None:
                for video in found[1]["snapshot"]["videos"]:
                    collector.media(record, video["media"])
        collector.binding(record, "valid", {key: state[key] for key in (
            "work", "uploaded_media", "publication_media", "pending_publication", "recovery_attempt",
            "recovery", "legacy_pending_stream")})
    return invalid


def parse_authority_links(bundle, projection, *, profile, root_ids, member_id, source_identity,
                          provenance=None, manual_proofs=None, limits=None, check_cancelled=None,
                          legacy_pending_images=()):
    """Retain actual permanent edges, separately from P05 pointer retirement.

    The group caller supplies its captured public rows and actual SQL producer
    projection. This pure parser opens no files or database, creates no owner,
    and cannot turn a detached ref/receipt into fresh or executable authority.
    """
    limits = HomeLimits() if limits is None else limits
    if (not isinstance(bundle, CapturedBundle) or not isinstance(profile, lifecycle.StorageProfile)
            or not isinstance(limits, HomeLimits) or type(source_identity) is not dict
            or set(source_identity) != {"library_id", "node_id"}
            or check_cancelled is not None and not callable(check_cancelled)):
        raise ValueError("Invalid permanent Home root inputs")
    for value in source_identity.values():
        _uuid(value)
    budget, collector = _Budget(limits, check_cancelled), _Collector(root_ids, member_id)
    marker = CapturedRecord("authority", "permanent")
    unknown, observed = [], []
    fields = {"summary", "references", "unknown"}
    version = projection.get("summary", {}).get("format_version") if type(projection) is dict and type(projection.get("summary")) is dict else None
    if type(version) is int and version == 2:
        fields.add("legacy_pending_state")
    if (type(projection) is not dict or set(projection) != fields
            or type(projection["references"]) is not list or type(projection["unknown"]) is not list
            or type(projection["summary"]) is not dict):
        collector.binding(marker, "unknown", {"code": "HOME_AUTHORITY_PROJECTION_INVALID"})
        if version == 2 or legacy_pending_images:
            collector.block(CapturedRecord("legacy_pending_state", "projection"), "HOME_LEGACY_PENDING_STATE_UNPROVEN")
        return collector.result()
    if len(projection["references"]) > 8192:
        collector.binding(marker, "unknown", {"code": "HOME_AUTHORITY_COUNT_LIMIT"})
        if version == 2 or legacy_pending_images:
            collector.block(CapturedRecord("legacy_pending_state", "projection"), "HOME_LEGACY_PENDING_STATE_UNPROVEN")
        return collector.result()
    if (projection["unknown"] or projection["summary"].get("presence") not in {"absent", "present"}
            or projection["summary"].get("presence") == "absent" and projection["references"]):
        unknown.append("HOME_AUTHORITY_SCOPE_UNPROVEN")
    rows = []
    for row in projection["references"]:
        budget.check()
        try:
            if (type(row) is not dict or set(row) != {"kind", "record_id", "record_sha256", "core_sha256",
                    "state", "refs", "publication_metadata"}
                    or row["kind"] not in {"origins", "controls", "requests", "receipts", "publications"}
                    or row["state"] not in {"reserved", "committed"} or type(row["refs"]) is not list):
                raise ValueError("Invalid public row")
            _uuid(row["record_id"])
            if (type(row["record_sha256"]) is not str or not _SHA.fullmatch(row["record_sha256"])
                    or row["core_sha256"] is not None and (type(row["core_sha256"]) is not str
                    or not _SHA.fullmatch(row["core_sha256"]))):
                raise ValueError("Invalid public SHA")
            budget.bytes(len(inputs.canonical_bytes(row)))
            rows.append(row)
        except (ValueError, TypeError, KeyError):
            unknown.append("HOME_AUTHORITY_PROJECTION_INVALID")

    def resolve(ref, core):
        folders = {"origin": "origins", "control": "controls", "publication": "publications",
            "recovery_intent": "requests", "recovery_receipt": "receipts"}
        found = [row for row in rows if row["kind"] == folders.get(ref["record_kind"])
                 and row["record_id"] == ref["record_id"] and row["record_sha256"] == ref["record_sha256"]
                 and row["core_sha256"] == core and row["state"] == "committed"]
        if len(found) != 1:
            raise ValueError("Permanent committed ref missing")
        return found[0]

    pending_contexts = []
    if version == 2:
        pending_contexts, invalid = _legacy_pending_contexts(projection["legacy_pending_state"],
            legacy_pending_images, profile, source_identity, budget, collector)
        roots_invalid = _legacy_pending_roots(bundle, pending_contexts, source_identity, budget, collector)
        if (invalid or roots_invalid or projection["summary"].get("legacy_pending_state") !=
                projection["legacy_pending_state"]["summary"]):
            unknown.append("HOME_LEGACY_PENDING_STATE_UNPROVEN")
        try:
            from . import storage_legacy_pending_state17 as pending
            for ref in projection["legacy_pending_state"]["references"]:
                budget.check()
                if (type(ref) is not dict or set(ref) != pending.REFERENCE_FIELDS
                        or type(ref["format_version"]) is not int or ref["format_version"] != 1
                        or ref["record_kind"] not in pending.REFERENCE_KINDS):
                    raise ValueError("Invalid pending committed reference")
                resolve(ref, ref["core_sha256"])
        except (ValueError, TypeError, KeyError, AttributeError):
            unknown.append("HOME_LEGACY_PENDING_STATE_UNPROVEN")
            collector.block(CapturedRecord("legacy_pending_state", "references"), "HOME_LEGACY_PENDING_STATE_UNPROVEN")
    elif legacy_pending_images:
        unknown.append("HOME_LEGACY_PENDING_STATE_UNPROVEN")
        collector.block(CapturedRecord("legacy_pending_state", "uncovered"), "HOME_LEGACY_PENDING_STATE_UNPROVEN")

    # A permanent publication continues holding its original media even when a
    # mutable pointer or a P05 catalog independently retires that outbox edge.
    outboxes = {record.name: record for record in bundle.records if isinstance(record, CapturedRecord)
                and record.kind == "outbox"}
    payload_budget = _Budget(_payload_limits(limits), check_cancelled)
    payloads, payload_values, payload_seen = {}, {}, set()
    for record in bundle.records:
        if not isinstance(record, CapturedRecord) or record.kind not in {"publication_payload", "part"}:
            continue
        started = time.monotonic()
        try:
            if record.kind == "part" and not _payload_part(record) or record.name in payload_seen:
                collector.block(record, "HOME_PUBLICATION_PAYLOAD_UNPROVEN", media_only=True)
                continue
            payload_seen.add(record.name)
            value = _payload_media(record, payload_budget, collector, source_identity)
            if value is None:
                continue
            payloads[record.name], payload_values[record.name] = record, value
        finally:
            budget.started += time.monotonic() - started
    for row in rows:
        budget.check()
        if row["kind"] != "publications":
            continue
        publication_marker = CapturedRecord("authority", "publication:" + row["record_id"])
        try:
            metadata = row["publication_metadata"]
            sync_state._metadata(metadata)
            if metadata["publication_id"] != row["record_id"]:
                raise ValueError("Publication identity mismatch")
            payload_edges = [edge for edge in row["refs"] if type(edge) is dict and edge.get("kind") == "publication_payload"]
            if payload_edges:
                edge = _publication_payload_edge(row)
                if (edge["sha256"], edge["byte_size"]) != (metadata["sha256"], metadata["byte_size"]):
                    raise ValueError("Publication payload metadata mismatch")
                record = payloads.get(edge["sha256"] + ".json")
                value = payload_values.get(edge["sha256"] + ".json")
                if record is None or value is None or value["snapshot"]["publication_id"] != row["record_id"]:
                    raise ValueError("Original publication body missing")
                if _hash(value["claims"]) != edge["claims_sha256"]:
                    raise ValueError("Original publication claims mismatch")
                _publication_payload_origin(row, value, rows, source_identity)
                outbox = outboxes.get(metadata["publication_id"] + ".json")
                if outbox is not None and (outbox.error or outbox.raw != record.raw):
                    raise ValueError("Original outbox bytes differ")
                if row["state"] == "reserved":
                    unknown.append("HOME_AUTHORITY_SCOPE_UNPROVEN")
            else:
                # A format2 body whose edge was removed cannot silently become
                # a legacy row. Actual format1 rows keep their old contract.
                if any(value["snapshot"]["publication_id"] == row["record_id"] for value in payload_values.values()):
                    raise ValueError("Publication payload edge missing")
                record = outboxes.get(metadata["publication_id"] + ".json")
                if (record is None or record.error or type(record.raw) is not bytes
                        or len(record.raw) != metadata["byte_size"]
                        or hashlib.sha256(record.raw).hexdigest() != metadata["sha256"]):
                    raise ValueError("Original outbox missing")
                value = _outbox(_decode(record, budget), record.name, source_identity)
            for video in value["snapshot"]["videos"]:
                collector.media(publication_marker, video["media"])
            observed.append({"publication_id": row["record_id"], "outbox_sha256": metadata["sha256"],
                             "byte_size": metadata["byte_size"]})
        except (ValueError, safe.SyncError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            unknown.append("HOME_AUTHORITY_OUTBOX_UNPROVEN")
            collector.block(publication_marker, "HOME_AUTHORITY_OUTBOX_UNPROVEN", media_only=True)
            original_outbox = outboxes.get(row["record_id"] + ".json")
            if original_outbox is not None and not original_outbox.error:
                # A missing/invalid selected blob does not erase an independently
                # readable original outbox's typed media facts.
                try:
                    value = _outbox(_decode(original_outbox, budget), original_outbox.name, source_identity)
                    for video in value["snapshot"]["videos"]:
                        collector.media(original_outbox, video["media"])
                except (ValueError, safe.SyncError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
                    pass

    state_contexts = [(record, None) for record in bundle.records if isinstance(record, CapturedRecord)
        and record.kind in {"state", "history"} and record.raw is not None]
    state_contexts.extend(pending_contexts)
    for record, private_state in state_contexts:
        budget.check()
        publication_ref = None
        try:
            raw = _decode(record, budget) if private_state is None else private_state
            state = _state(raw, source_identity, history=record.kind == "history")
            if state["format_version"] != 5:
                continue
            public = sync_state.source_readmit_projection(state)
            core, refs = public["core_sha256"], public["source_authority"]
            if refs is not None:
                publication_ref = refs["publication"]
                for name in ("origin", "defer", "resume", "failure", "publication"):
                    if refs[name] is not None:
                        resolve(refs[name], core)
            admission = public["source_admission"]
            if admission is not None:
                found = [row for row in rows if row["kind"] == "receipts" and row["state"] == "committed"
                    and row["record_id"] == admission["admission_id"]
                    and row["record_sha256"] == admission["record_sha256"] and row["core_sha256"] == core
                    and any(type(edge) is dict and edge.get("kind") == "admission"
                            and {key: edge.get(key) for key in admission} == admission for edge in row["refs"])]
                if len(found) != 1:
                    raise ValueError("Admission record missing")
            pending = public["source_recovery_pending"]
            if pending is not None:
                found = [row for row in rows if row["kind"] == "requests" and row["state"] == "committed"
                    and row["record_id"] == pending["request_id"] and row["record_sha256"] == pending["intent_sha256"]
                    and row["core_sha256"] == core and any(type(edge) is dict and edge.get("kind") == "recovery_request"
                        and edge.get("request_id") == pending["request_id"]
                        and edge.get("request_sha256") == pending["request_sha256"]
                        and edge.get("request_kind") == pending["kind"]
                        and edge.get("core_sha256") == core for edge in row["refs"])]
                if len(found) != 1:
                    raise ValueError("Original recovery intent missing")
            terminal = raw.get("source_terminal") if record.kind == "history" else None
            if terminal is not None and terminal.get("format_version") == 2:
                actual = resolve(terminal["authority_ref"], core)
                if _hash(actual) != terminal["authority_sha256"]:
                    raise ValueError("Full committed public projection mismatch")
            observed.append({"kind": record.kind, "record": _hash(record.name), "refset_sha256": public["refset_sha256"]})
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            unknown.append("HOME_AUTHORITY_STATE_REFS_UNPROVEN")
            if publication_ref is not None:
                collector.block(CapturedRecord("authority", "missing-publication:" + publication_ref["record_id"]),
                    "HOME_AUTHORITY_OUTBOX_UNPROVEN", media_only=True)

    home_receipts = {} if type(provenance) is not dict else {
        row["operation_id"]: row["result_sha256"] for row in provenance.get(inputs.home_recovery_roots.TABLE, [])}
    manual_proofs = {} if type(manual_proofs) is not dict else manual_proofs
    for row in rows:
        budget.check()
        for edge in row["refs"]:
            if type(edge) is not dict or edge.get("producer") not in {"home_knowledge_restore", "sync_budget_resume"}:
                continue
            actual = (home_receipts.get(edge.get("operation_id")) if edge["producer"] == "home_knowledge_restore" else
                      manual_proofs.get((row["core_sha256"], edge.get("operation_id"))))
            if actual != edge.get("receipt_sha256"):
                unknown.append("HOME_AUTHORITY_PROVENANCE_UNPROVEN")
    collector.binding(marker, "unknown" if unknown else projection["summary"]["presence"], {"reference_set_sha256": _hash(rows),
        "observed": observed, "unknown": sorted(set(unknown))})
    return collector.result()


def scan_locked(profile, *, root_ids, member_id, source_identity, limits=None, check_cancelled=None):
    """Convenience scan; multi-source callers hold capture until round end."""
    with capture_locked(profile, limits=limits, check_cancelled=check_cancelled) as captured:
        return parse_bundle(captured.bundle, profile=profile, root_ids=root_ids, member_id=member_id,
                            source_identity=source_identity, limits=limits)
