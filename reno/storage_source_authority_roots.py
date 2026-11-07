"""Bounded Home permanent-file observations without State or writer authority.

Physical capture owns only no-follow read handles. Mutable heads are released
promptly, and every file, directory and absence is rechecked before returning.
Record relationships are interpreted by sync_source_authority.pure_projection.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
import stat
import time
import uuid

from . import storage_lifecycle as lifecycle, storage_source as source
from . import sync_media as safe, sync_source_authority as authority, sync_state


class AuthorityRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        self.code, self.retriable = code, retriable
        super().__init__(code)


class _Cancelled(BaseException):
    def __init__(self, error):
        self.error = error


def _fail(code, *, retriable=False):
    raise AuthorityRootError("AUTHORITY_ROOT_" + code, retriable=retriable) from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CaptureLimits:
    max_records: int = 4096
    max_journal: int = 8192
    max_parts: int = 32
    max_record_bytes: int = 64 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    deadline_seconds: float = 10.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError("Invalid capture limit")
            elif (type(value) is not int or value < 1
                    or value > type(self).__dataclass_fields__[name].default):
                raise ValueError("Invalid capture limit")


class _Reader:
    def __init__(self, profile, stack, limits, callback):
        self.profile, self.stack, self.limits, self.callback = profile, stack, limits, callback
        self.started, self.total = time.monotonic(), 0
        self.directories, self.files, self.absences, self.names = [], [], [], []
        self.payload, self.facility_seen = None, False
        self.anchor_errors = []
        self.payload_total = self.payload_count = 0
        self.payload_started = None
        self.payload_elapsed = 0.0
        self.payload_window_started = None
        self.legacy_pending_elapsed = 0.0
        self.legacy_pending_started = None
        self.publication_payloads, self.publication_payload_errors = {}, []

    def check(self):
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as error:
                raise _Cancelled(error) from None
        now = time.monotonic()
        active_payload = 0.0 if self.payload_window_started is None else now - self.payload_window_started
        active_pending = 0.0 if self.legacy_pending_started is None else now - self.legacy_pending_started
        if (now - self.started - self.payload_elapsed - active_payload
                - self.legacy_pending_elapsed - active_pending > self.limits.deadline_seconds):
            _fail("DEADLINE_LIMIT")

    @contextmanager
    def legacy_pending_window(self):
        """Keep the independent capsule budget outside small-record timing."""
        if self.legacy_pending_started is not None:
            yield
            return
        self.check()
        self.legacy_pending_started = time.monotonic()
        try:
            yield
        finally:
            self.legacy_pending_elapsed += time.monotonic() - self.legacy_pending_started
            self.legacy_pending_started = None

    def _payload_check(self):
        from .sync_source_publication_local2 import PAYLOAD_DEADLINE_SECONDS
        self.check()
        if self.payload_started is not None and time.monotonic() - self.payload_started > PAYLOAD_DEADLINE_SECONDS:
            _fail("PAYLOAD_DEADLINE_LIMIT")

    @contextmanager
    def payload_window(self):
        """Separate body I/O time from the unchanged small-record budget."""
        if self.payload_window_started is not None:
            yield
            return
        self.check()
        self.payload_window_started = time.monotonic()
        if self.payload_started is None:
            self.payload_started = self.payload_window_started
        try:
            self._payload_check()
            yield
            self._payload_check()
        finally:
            self.payload_elapsed += time.monotonic() - self.payload_window_started
            self.payload_window_started = None

    def directory(self, path, *, base):
        self.check()
        directory = self.stack.enter_context(safe._Directory(path, base=base))
        self.directories.append(directory)
        return directory

    def read(self, directory, name, *, mutable=False, optional=False, decode=True, _payload=False):
        self.check()
        try:
            before_info = directory.entry_stat(name)
        except FileNotFoundError:
            if not optional:
                _fail("PARTIAL")
            self.absences.append((directory, name))
            return None
        before = source.file_signature(before_info)
        maximum = self.limits.max_record_bytes
        if _payload:
            from .sync_source_publication_local2 import MAX_PAYLOAD_BYTES, MAX_PAYLOAD_TOTAL_BYTES, MAX_PAYLOAD_FILES
            maximum = MAX_PAYLOAD_BYTES
            self.payload_total += before[2]
            self.payload_count += 1
            if self.payload_total > MAX_PAYLOAD_TOTAL_BYTES or self.payload_count > MAX_PAYLOAD_FILES + authority.MAX_INTERNAL_PARTS:
                _fail("PAYLOAD_TOTAL_LIMIT")
        if before[2] > maximum:
            _fail("RECORD_LIMIT")
        if os.name != "nt" and (before_info.st_uid != os.geteuid() or stat.S_IMODE(before_info.st_mode) & 0o077):
            _fail("UNSAFE")
        if not _payload:
            self.total += before[2]
            if self.total > self.limits.max_total_bytes:
                _fail("TOTAL_LIMIT")
        context = source.source_reader(directory, name) if mutable else directory.open_file(name)
        stream = context.__enter__() if mutable else self.stack.enter_context(context)
        try:
            opened = source.file_signature(os.fstat(stream.fileno()))
            if before[:4] != opened[:4]:
                _fail("SOURCE_CHANGED", retriable=True)
            raw, size = bytearray(), 0
            while True:
                self._payload_check() if _payload else self.check()
                chunk = os.read(stream.fileno(), min(128 * 1024, maximum + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                raw.extend(chunk)
                if size > before[2]:
                    _fail("SOURCE_CHANGED", retriable=True)
            if size != before[2] or source.file_signature(os.fstat(stream.fileno())) != opened:
                _fail("SOURCE_CHANGED", retriable=True)
        finally:
            if mutable:
                context.__exit__(None, None, None)
        if source.file_signature(directory.entry_stat(name)) != before:
            _fail("SOURCE_CHANGED", retriable=True)
        raw = bytes(raw)
        self.files.append((directory, name, None if mutable else stream, before, opened, hashlib.sha256(raw).digest()))
        if not decode:
            return raw
        value = sync_state._decode(raw)
        if authority._encoded(value) != raw:
            _fail("RECORD_INVALID")
        return value

    def read_publication_payload(self, directory, name):
        with self.payload_window():
            return self.read(directory, name, decode=False, _payload=True)

    def publication_payload_listing(self, directory, allowed, maximum, *, subdirectories=()):
        """Pin this fixed family without feeding large parts to record readers."""
        with self.payload_window():
            names, ordinary, parts = [], 0, 0
            with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
                for entry in entries:
                    self._payload_check()
                    names.append(entry.name)
                    info = directory.entry_stat(entry.name)
                    safe._safe_stat(info, directory=entry.name in subdirectories)
                    if authority._PART.fullmatch(entry.name):
                        parts += 1
                        if parts > self.limits.max_parts:
                            _fail("PART_LIMIT")
                        self.read_publication_payload(directory, entry.name)
                        self.publication_payload_errors.append("PAYLOAD_UNPUBLISHED_PART")
                        continue
                    ordinary += 1
                    if ordinary > maximum or not allowed(entry.name):
                        _fail("PAYLOAD_COUNT_LIMIT")
            names.sort()
            self.names.append((directory, tuple(names)))
            return [name for name in names if not authority._PART.fullmatch(name)]

    def _is_publication_payload(self, directory, name):
        from .sync_source_publication_local2 import PAYLOAD_FAMILY, PAYLOAD_DIRECTORY
        try:
            return directory.path.relative_to(self.profile.database.parent).as_posix() in {
                PAYLOAD_DIRECTORY, PAYLOAD_FAMILY}
        except ValueError:
            return False

    def listing(self, directory, allowed, maximum, *, subdirectories=()):
        self.check()
        names, ordinary, parts = [], 0, 0
        with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
            for entry in entries:
                self.check()
                names.append(entry.name)
                info = directory.entry_stat(entry.name)
                safe._safe_stat(info, directory=entry.name in subdirectories)
                if authority._PART.fullmatch(entry.name):
                    parts += 1
                    if parts > self.limits.max_parts:
                        _fail("PART_LIMIT")
                    self.read(directory, entry.name, decode=False)
                    continue
                ordinary += 1
                if ordinary > maximum:
                    _fail("COUNT_LIMIT")
                if not allowed(entry.name):
                    _fail("UNEXPECTED_FILE")
        names.sort()
        self.names.append((directory, tuple(names)))
        return [name for name in names if not authority._PART.fullmatch(name)]

    def _verify_namespace(self):
        self.check()
        for directory in self.directories:
            directory.verify()
        for directory, name in self.absences:
            self.check()
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("SOURCE_CHANGED", retriable=True)
        for directory, names in self.names:
            with self.payload_window() if self._is_publication_payload(directory, "") else nullcontext():
                self.check()
                current = []
                with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
                    for entry in entries:
                        self._payload_check() if self._is_publication_payload(directory, "") else self.check()
                        if len(current) >= len(names):
                            _fail("SOURCE_CHANGED", retriable=True)
                        current.append(entry.name)
                if tuple(sorted(current)) != names:
                    _fail("SOURCE_CHANGED", retriable=True)

    def verify(self):
        self._verify_namespace()
        for directory, name, stream, before, opened, digest in self.files:
            payload_file = self._is_publication_payload(directory, name)
            with self.payload_window() if payload_file else nullcontext():
                self._payload_check() if payload_file else self.check()
                if source.file_signature(directory.entry_stat(name)) != before:
                    _fail("SOURCE_CHANGED", retriable=True)
                if stream is None:
                    with source.source_reader(directory, name) as current:
                        found = self._rehash(current, before[2], payload=payload_file)
                else:
                    if source.file_signature(os.fstat(stream.fileno())) != opened:
                        _fail("SOURCE_CHANGED", retriable=True)
                    os.lseek(stream.fileno(), 0, os.SEEK_SET)
                    found = self._rehash(stream, before[2], payload=payload_file)
                if found.digest() != digest or source.file_signature(directory.entry_stat(name)) != before:
                    _fail("SOURCE_CHANGED", retriable=True)

    def verify_for_commit(self):
        """Use held immutable pins; reread only bounded mutable heads."""
        self._verify_namespace()
        for directory, name, stream, before, opened, digest in self.files:
            self.check()
            if source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)
            if stream is None:
                with source.source_reader(directory, name) as current:
                    if source.file_signature(os.fstat(current.fileno())) != opened:
                        _fail("SOURCE_CHANGED", retriable=True)
                    found = self._rehash(current, before[2])
                    if (found.digest() != digest
                            or source.file_signature(os.fstat(current.fileno())) != opened):
                        _fail("SOURCE_CHANGED", retriable=True)
            elif source.file_signature(os.fstat(stream.fileno())) != opened:
                _fail("SOURCE_CHANGED", retriable=True)
            if source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)
        self.check()

    def _rehash(self, stream, size, *, payload=False):
        """Bound final reads even if a mutable file grows after its stat."""
        found, remaining = hashlib.sha256(), size
        while remaining:
            self._payload_check() if payload else self.check()
            chunk = os.read(stream.fileno(), min(128 * 1024, remaining))
            if not chunk:
                _fail("SOURCE_CHANGED", retriable=True)
            remaining -= len(chunk)
            found.update(chunk)
        self._payload_check() if payload else self.check()
        if os.read(stream.fileno(), 1):
            _fail("SOURCE_CHANGED", retriable=True)
        return found


class AuthorityCapture:
    def __init__(self, reader, guard):
        self._reader, self._guard, self._active = reader, guard, True
        self._summary, self._references, self._unknown = {}, (), ()
        self._format_version, self._legacy_pending = 1, None

    @property
    def summary(self):
        return deepcopy(self._summary)

    @property
    def references(self):
        return deepcopy(self._references)

    @property
    def unknown(self):
        return deepcopy(self._unknown)

    def verify_source(self):
        if not self._active:
            _fail("CAPTURE_CLOSED")
        try:
            self._guard.verify()
            self._reader.verify()
            if self._legacy_pending is not None:
                with self._reader.legacy_pending_window():
                    self._legacy_pending.verify_source()
        except (source.SourceReadError, safe.SyncError, OSError):
            _fail("SOURCE_CHANGED", retriable=True)

    def verify_for_commit(self):
        """Short physical CAS inside a SQL window; grants no authority.

        Entry and exit still perform the full SHA verification. This method
        relies only on those already held immutable pins and rereads the small
        mutable heads, without hashing the complete ledger in the SQL writer.
        """
        if not self._active:
            _fail("CAPTURE_CLOSED")
        try:
            self._guard.verify()
            self._reader.verify_for_commit()
            if self._legacy_pending is not None:
                with self._reader.legacy_pending_window():
                    self._legacy_pending.verify_for_commit()
        except (source.SourceReadError, safe.SyncError, OSError):
            _fail("SOURCE_CHANGED", retriable=True)

    def public_summary(self):
        projected = {"summary": self.summary, "references": list(self.references), "unknown": list(self.unknown)}
        if self._format_version == 2:
            projected["legacy_pending_state"] = (self._legacy_pending.public_summary()
                if self._legacy_pending is not None else {"summary": {
                    "format_version": 1, "kind": "legacy_pending_state", "presence": "unknown"},
                    "references": [], "unknown": [{"code": "LEGACY_PENDING_CAPTURE_UNPROVEN"}]})
        return deepcopy(projected)

    def _prepare_legacy_pending_append17(self, writer):
        """Keep this original Home object while its exact pending child grows."""
        from . import home_authority_pending_contract17 as pending_contract
        from . import storage_legacy_pending_state17 as pending_roots
        from . import sync_source_legacy_pending_state17 as capsules
        if type(writer) is not capsules._PendingStateAppendWriter17:
            _fail("LEGACY_PENDING_APPEND_PRODUCER_REQUIRED")
        writer._authorize_home(self)
        if (type(self) is not AuthorityCapture or not self._active or self._format_version != 2
                or self._unknown or self._summary.get("presence") != "present"
                or type(self._legacy_pending) is not pending_roots.PendingStateCapture):
            _fail("LEGACY_PENDING_APPEND_PRODUCER_REQUIRED")
        pending_contract.normalize_projection(self.public_summary(), expected_version=2)
        self._guard.verify()
        # The authority ledger itself is unchanged by a private capsule append.
        # Do not invoke the old child's now-satisfied namespace absence here.
        self._reader.verify()
        with self._reader.legacy_pending_window():
            plan = pending_roots._prepare_append17(self._legacy_pending, writer=writer, home=self)
            self._pending_append_summary17(writer, plan)
        return plan

    def _pending_append_summary17(self, writer, plan):
        from . import storage_legacy_pending_state17 as pending_roots
        pending = pending_roots._check_append_plan17(plan, writer=writer,
            home=self, captured=self._legacy_pending)
        base = {key: deepcopy(value) for key, value in self._summary.items() if key != "legacy_pending_state"}
        base["format_version"] = 1
        return _with_legacy_pending(base, self._references, pending)

    def _validate_legacy_pending_append17(self, writer, plan):
        writer._authorize_home(self)
        self._guard.verify()
        self._reader.verify()
        with self._reader.legacy_pending_window():
            self._pending_append_summary17(writer, plan)

    def _adopt_legacy_pending_append17(self, writer, plan):
        from . import storage_legacy_pending_state17 as pending_roots
        # Both plans have passed their final full reads. No callback or I/O
        # may run between this batch's two in-place memory updates.
        pending = pending_roots._fixed_append_plan17(plan, writer=writer,
            home=self, captured=self._legacy_pending)
        base = {key: deepcopy(value) for key, value in self._summary.items() if key != "legacy_pending_state"}
        base["format_version"] = 1
        summary, references = _with_legacy_pending(base, self._references, pending)
        pending_roots._adopt_append17(plan, writer=writer, home=self, captured=self._legacy_pending)
        self._summary, self._references = summary, references
        writer._accepted_home(self, plan)


def _record_filename(name):
    if not name.endswith(".json"):
        return False
    key = name[:-5]
    try:
        return str(uuid.UUID(key)) == key
    except ValueError:
        return False


def _optional_directory(reader, parent, name, *, base):
    try:
        return reader.directory(parent.path / name, base=base)
    except FileNotFoundError:
        reader.absences.append((parent, name))
        return None


def _optional_anchor(reader, directory, name, *, mutable=False):
    """Retain independent journal roots when an anchor is unparseable.

    Static anchor damage remains scoped unknown after the bounded capture.
    Races, cancellation and exhausted total/time budgets terminate immediately.
    No missing or invalid anchor is ever reconstructed from another file.
    """
    # Physical/path/budget failures are deliberately outside the decode-only
    # recovery boundary; they must stop the capture immediately.
    raw = reader.read(directory, name, mutable=mutable, optional=True, decode=False)
    if raw is None:
        return None
    reader.facility_seen = True
    try:
        value = sync_state._decode(raw)
        if authority._encoded(value) != raw:
            _fail("RECORD_INVALID")
        return value
    except (AuthorityRootError, safe.SyncError, ValueError, TypeError, KeyError,
            UnicodeError, OverflowError, RecursionError):
        reader.anchor_errors.append("AUTHORITY_ROOT_UNPROVEN")
    return None


def _capture_publication_payloads(reader, data):
    """Pin the complete fixed body family, without selecting a publication.

    Candidates and unpublished parts are independent preservation inputs.
    Missing selected names are pinned by the relational check after the real
    journal has been read. This adds no fields to the eight-field projection.
    """
    from . import sync_source_publication_local2 as publication
    reader.publication_payloads = {}
    reader.publication_payload_errors = []
    reader.publication_payload_directory = None
    outer = _optional_directory(reader, data, publication.PAYLOAD_DIRECTORY, base=data.path)
    if outer is None:
        return
    reader.publication_payload_listing(outer, lambda name: name == publication.PAYLOAD_VERSION,
        1, subdirectories={publication.PAYLOAD_VERSION})
    directory = _optional_directory(reader, outer, publication.PAYLOAD_VERSION, base=data.path)
    if directory is None:
        reader.publication_payload_errors.append("PAYLOAD_FAMILY_PARTIAL")
        return
    reader.publication_payload_directory = directory
    names = reader.publication_payload_listing(directory,
        lambda name: name.endswith(".json") and bool(authority._SHA.fullmatch(name[:-5])),
        publication.MAX_PAYLOAD_FILES)
    for name in names:
        raw = reader.read_publication_payload(directory, name)
        reader.publication_payloads[name] = raw
        # The filename is a body SHA, never the selected publication UUID.
        ref = {"format_version": 1, "family": publication.PAYLOAD_FAMILY,
               "sha256": name[:-5], "byte_size": len(raw)}
        try:
            with reader.payload_window():
                publication.validate_payload_bytes2(raw, ref)
        except (safe.SyncError, ValueError, TypeError, KeyError, UnicodeError,
                OverflowError, RecursionError):
            reader.publication_payload_errors.append("PAYLOAD_BODY_INVALID")


def _validate_publication_payloads(reader, entries):
    """Resolve selected original bodies against the actual ledger closure.

    Original-completion readers may supply their independently proven raw
    reserved frame; ordinary captures call this only after pure_projection.
    No absent body is filled from an outbox, SQL image or live directory.
    """
    from . import sync_source_publication_local2 as publication
    for item in entries["publications"].values():
        record = item["record"]
        if record["format_version"] != 2:
            continue
        name = publication.payload_name2(record["payload_ref"])
        raw = reader.publication_payloads.get(name)
        if raw is None:
            if reader.publication_payload_directory is not None:
                reader.read(reader.publication_payload_directory, name, optional=True, decode=False)
            _fail("PAYLOAD_MISSING")
        with reader.payload_window():
            publication.validate_record_payload2(raw, record, entries)
    # Unselected candidates/parts remain physical preservation inputs. Their
    # media uncertainty belongs to the independent pure Home root parser;
    # they cannot fabricate a ledger record or block preserving original bytes.


def _read_payload(reader):
    """Capture only the frozen eight-field pure-projection input shape."""
    profile = reader.profile
    data = reader.directory(profile.database.parent, base=profile.database.parent)
    checkpoints = reader.directory(profile.checkpoints, base=profile.checkpoints)
    _capture_publication_payloads(reader, data)
    payload = {"format_version": 1, "intent": _optional_anchor(reader, data, authority.INTENT_NAME),
        "seal": _optional_anchor(reader, checkpoints, authority.SEAL_NAME),
        "db_head": _optional_anchor(reader, data, authority.DB_HEAD_NAME, mutable=True),
        "checkpoint_head": _optional_anchor(reader, checkpoints, authority.CHECKPOINT_HEAD_NAME, mutable=True),
        "contract": None, "journal": [], "records": {kind: {} for kind in authority.KINDS}}
    reader.payload = payload
    outer = _optional_directory(reader, data, authority.DIRECTORY, base=data.path)
    if outer is None:
        return payload
    reader.facility_seen = True
    reader.listing(outer, lambda name: name == authority.VERSION_DIRECTORY, 1,
                   subdirectories={authority.VERSION_DIRECTORY})
    ledger = _optional_directory(reader, outer, authority.VERSION_DIRECTORY, base=data.path)
    if ledger is None:
        _fail("PARTIAL")
    reader.listing(ledger, lambda name: name in {"contract.json", "journal", *authority.KINDS},
                   len(authority.KINDS) + 2, subdirectories={"journal", *authority.KINDS})
    payload["contract"] = _optional_anchor(reader, ledger, "contract.json")
    journal = _optional_directory(reader, ledger, "journal", base=data.path)
    if journal is None:
        _fail("PARTIAL")
    names = reader.listing(journal, lambda name: bool(authority._ORDINAL.fullmatch(name)), reader.limits.max_journal)
    for ordinal, name in enumerate(names, 1):
        reader.check()
        if name != f"{ordinal:08d}.json":
            _fail("JOURNAL_INVALID")
        payload["journal"].append(reader.read(journal, name))
    total_records = 0
    for kind in authority.KINDS:
        directory = _optional_directory(reader, ledger, kind, base=data.path)
        if directory is None:
            _fail("PARTIAL")
        names = reader.listing(directory, _record_filename, reader.limits.max_records)
        total_records += len(names)
        if total_records > reader.limits.max_records:
            _fail("COUNT_LIMIT")
        for name in names:
            reader.check()
            payload["records"][kind][name[:-5]] = reader.read(directory, name)
    return payload


def _known_references(reader):
    """Typed single-event facts remain roots when whole-chain proof is unknown.

    None of these rows establishes closure, restores a missing record, or
    substitutes the caller's expected peer binding. Invalid events contribute
    no fabricated identifiers or paths.
    """
    payload = reader.payload
    if payload is None:
        return ()
    activation = None
    for candidate in (payload["contract"], payload["intent"], payload["seal"]):
        if candidate is None:
            continue
        try:
            # This retains actual typed facts only. Full owner/binding checks
            # are mandatory in pure_projection before scope can be healthy.
            activation = authority.validate_contract(candidate)
            break
        except (safe.SyncError, ValueError, TypeError, KeyError):
            continue
    if activation is None:
        return ()
    refs = {}
    for event in payload["journal"]:
        reader.check()
        try:
            authority._exact(event, authority._JOURNAL_FIELDS)
            authority._version(event["format_version"])
            authority._integer(event["ordinal"], minimum=1, maximum=reader.limits.max_journal)
            authority._sha(event["previous_sha256"])
            authority._uuid(event["key"])
            authority._sha(event["record_sha256"])
            if event["facility_id"] != activation["facility_id"] or event["kind"] not in authority.KINDS:
                continue
            row = authority.public_record_projection(event["kind"], event["record"],
                state=event["state"], activation=activation)
            if event["key"] != row["record_id"] or event["record_sha256"] != row["record_sha256"]:
                continue
            # Preserve distinct actual hashes/outcomes; inconsistent duplicates
            # must not overwrite the earlier bounded root fact.
            refs[(row["kind"], row["record_id"], row["record_sha256"], row["state"])] = row
        except (safe.SyncError, ValueError, TypeError, KeyError):
            continue
    return tuple(refs[key] for key in sorted(refs))


def _capture_base1(reader, binding):
    payload = _read_payload(reader)
    if reader.anchor_errors:
        _fail("ANCHOR_INVALID")
    sync_state._exact(binding, ("home_library_id", "home_node_id", "target_library_id", "target_node_id", "target_url"))
    sync_state._bounded(binding, maximum=reader.limits.max_record_bytes, nodes=16, depth=2)
    supplied = sync_state._binding(binding)
    if (not reader.facility_seen and payload["contract"] is None and payload["intent"] is None
            and payload["seal"] is None and payload["db_head"] is None and payload["checkpoint_head"] is None):
        _validate_publication_payloads(reader, {kind: {} for kind in authority.KINDS})
        public = {key: supplied[key] for key in ("home_library_id", "home_node_id", "target_library_id", "target_node_id")}
        absent = {"format_version": 1, "kind": "home_authority", "presence": "absent"}
        return {**absent, "absence_sha256": _digest(absent), "owner_profile_sha256": authority.profile_sha256(reader.profile),
            "identity": None, "binding": public, "contract_sha256": None, "head_ordinal": 0,
            "head_sha256": None, "journal_sha256": None, "record_set_sha256": None,
            "reference_set_sha256": _digest([]), "record_count": 0, "journal_count": 0}, ()
    result = authority.pure_projection(payload, binding=supplied,
        owner_profile_sha256=authority.profile_sha256(reader.profile))
    entries = {kind: {} for kind in authority.KINDS}
    for event in payload["journal"]:
        entries[event["kind"]][event["key"]] = {"state": event["state"], "record": event["record"]}
    _validate_publication_payloads(reader, entries)
    refs = tuple(result["records"])
    if any(row["state"] != "committed" for row in refs):
        _fail("RESERVED")
    return {"format_version": 1, "kind": "home_authority", "presence": "present", "absence_sha256": None,
        "owner_profile_sha256": result["owner_profile_sha256"], "identity": {"facility_id": result["facility_id"]},
        "binding": result["binding"], "contract_sha256": _digest(payload["contract"]),
        "head_ordinal": len(payload["journal"]), "head_sha256": result["head_sha256"],
        "journal_sha256": _digest(payload["journal"]), "record_set_sha256": _digest(refs),
        "reference_set_sha256": _digest(refs), "record_count": len(refs), "journal_count": len(payload["journal"])}, refs


def _summary_version(format_version):
    if type(format_version) is not int or format_version not in {1, 2}:
        raise ValueError("Invalid Home authority observation version")
    return format_version


def _with_legacy_pending(base, references, legacy_pending):
    """Combine only explicit original facts; no file lookup or capability."""
    from . import home_authority_pending_contract17 as pending_contract
    projected = pending_contract.build_projection({"summary": base,
        "references": list(references), "unknown": []}, legacy_pending_state=legacy_pending)
    folders = {"origin": "origins", "control": "controls", "publication": "publications",
        "recovery_intent": "requests", "recovery_receipt": "receipts"}
    originals = {(row["kind"], row["record_id"], row["record_sha256"], row["core_sha256"])
        for row in references if row["state"] == "committed"}
    for ref in projected["legacy_pending_state"]["references"]:
        if (folders[ref["record_kind"]], ref["record_id"], ref["record_sha256"], ref["core_sha256"]) not in originals:
            _fail("LEGACY_PENDING_REFERENCE_UNPROVEN")
    return projected["summary"], tuple(projected["references"])


def _capture(reader, binding, *, format_version=1, legacy_pending=None):
    """Read original ledger1, then explicitly combine a captured/copied2 DTO.

    The copied-file owner must verify its component against the original
    document/map. A private DTO here is comparison data, not a physical seal.
    """
    version = _summary_version(format_version)
    if version == 1 and legacy_pending is not None or version == 2 and legacy_pending is None:
        _fail("LEGACY_PENDING_VERSION_REQUIRED")
    base, references = _capture_base1(reader, binding)
    return (base, references) if version == 1 else _with_legacy_pending(base, references, legacy_pending)


@contextmanager
def capture_locked(profile, *, binding, format_version=1, limits=None, check_cancelled=None):
    """Capture permanent Home facts under caller-owned SH; never write or lock.

    Full binding is an expected identity, not dispatch authority. Unknown is
    scoped to Home authority evidence, with no managed-path GC blocker. SQL
    producer resolution, other roots and final GC decisions remain caller-owned.
    """
    with _capture_context(profile, lambda reader: _capture(reader, binding),
            format_version=format_version, limits=limits, check_cancelled=check_cancelled) as capture:
        yield capture


def _capture_absence_base1(reader):
    payload = _read_payload(reader)
    if (reader.facility_seen or reader.anchor_errors or any(payload[key] is not None
            for key in ("contract", "intent", "seal", "db_head", "checkpoint_head"))):
        _fail("ABSENCE_UNPROVEN")
    _validate_publication_payloads(reader, {kind: {} for kind in authority.KINDS})
    absent = {"format_version": 1, "kind": "home_authority", "presence": "absent"}
    return {**absent, "absence_sha256": _digest(absent),
        "owner_profile_sha256": authority.profile_sha256(reader.profile), "identity": None, "binding": None,
        "contract_sha256": None, "head_ordinal": 0, "head_sha256": None, "journal_sha256": None,
        "record_set_sha256": None, "reference_set_sha256": _digest([]), "record_count": 0, "journal_count": 0}, ()


def _capture_absence(reader, *, format_version=1, legacy_pending=None):
    version = _summary_version(format_version)
    if version == 1 and legacy_pending is not None or version == 2 and legacy_pending is None:
        _fail("LEGACY_PENDING_VERSION_REQUIRED")
    base, references = _capture_absence_base1(reader)
    return (base, references) if version == 1 else _with_legacy_pending(base, references, legacy_pending)


@contextmanager
def capture_absence_locked(profile, *, format_version=1, limits=None, check_cancelled=None):
    """Prove only actual absence; never derive a peer or a healthy facility.

    The shared StateStore lock is not an authority-facility file. Any actual
    authority tree/anchor/head fragment keeps scoped unknown. A real producer,
    not this detached DTO, owns any subsequent certificate or fresh permission.
    """
    with _capture_context(profile, _capture_absence, format_version=format_version, limits=limits,
            check_cancelled=check_cancelled) as capture:
        yield capture


@contextmanager
def _capture_context(profile, operation, *, format_version=1, limits=None, check_cancelled=None):
    version = _summary_version(format_version)
    limits = CaptureLimits() if limits is None else limits
    if not isinstance(limits, CaptureLimits) or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid authority capture options")
    guard = lifecycle.require_guard(profile)
    with ExitStack() as stack:
        reader = _Reader(profile, stack, limits, check_cancelled)
        capture = AuthorityCapture(reader, guard)
        capture._format_version = version
        try:
            try:
                capture._summary, capture._references = operation(reader)
            except (AuthorityRootError, source.SourceReadError, safe.SyncError, OSError,
                    ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as error:
                if getattr(error, "retriable", False):
                    raise
                if isinstance(error, source.SourceReadError) and error.code == "SOURCE_CHANGED":
                    _fail("SOURCE_CHANGED", retriable=True)
                capture._references = _known_references(reader)
                code = error.code if isinstance(error, AuthorityRootError) else "AUTHORITY_ROOT_UNPROVEN"
                if isinstance(error, safe.SyncError) and error.code.startswith("SYNC_SOURCE_AUTHORITY_"):
                    code = "AUTHORITY_ROOT_" + error.code.removeprefix("SYNC_SOURCE_AUTHORITY_")
                capture._summary = {"format_version": 1, "kind": "home_authority", "presence": "unknown"}
                capture._unknown = ({"scope": "home_authority", "code": code, "boundscope": {
                    "observed_files": len(reader.files),
                    "observed_file_set_sha256": _digest([[name, digest.hex()] for _directory, name,
                        _stream, _before, _opened, digest in reader.files])}},)
            if version == 2:
                from . import storage_legacy_pending_state17 as pending_roots
                def pending_check():
                    if check_cancelled is not None:
                        try:
                            check_cancelled()
                        except BaseException as error:
                            raise _Cancelled(error) from None
                try:
                    with reader.legacy_pending_window():
                        capture._legacy_pending = stack.enter_context(pending_roots.capture_locked(profile,
                            limits=pending_roots.CaptureLimits(deadline_seconds=limits.deadline_seconds),
                            check_cancelled=pending_check))
                        if not capture._unknown:
                            capture._summary, capture._references = _with_legacy_pending(capture._summary,
                                capture._references, capture._legacy_pending.public_summary())
                except (AuthorityRootError, pending_roots.PendingStateRootError, source.SourceReadError,
                        safe.SyncError, OSError, ValueError, TypeError, KeyError,
                        UnicodeError, OverflowError, RecursionError) as error:
                    if getattr(error, "retriable", False):
                        raise
                    if isinstance(error, source.SourceReadError) and error.code == "SOURCE_CHANGED":
                        _fail("SOURCE_CHANGED", retriable=True)
                    code = getattr(error, "code", "AUTHORITY_ROOT_LEGACY_PENDING_UNPROVEN")
                    capture._unknown += ({"scope": "home_authority", "code": code},)
                if capture._unknown:
                    # Unknown2 retains independent original rows and any
                    # parsed capsule refs; it is neither healthy2 nor old1.
                    capture._summary = {"format_version": 2, "kind": "home_authority", "presence": "unknown"}
                    if capture._legacy_pending is not None:
                        capture._summary["legacy_pending_state"] = capture._legacy_pending.summary
            capture.verify_source()
            yield capture
            capture.verify_source()
        except _Cancelled as error:
            raise error.error from None
        finally:
            capture._active = False
