"""External, permanent Cloud control evidence, separate from SQLite images.

This is the file half of the continuity contract. It does not create SQL
activation/registration facts, authorize a queue effect, or infer not_applied
from a missing receipt. Consumers must resolve the exact SQL records while
holding this same real OS lock, before BEGIN IMMEDIATE, under lifecycle SH.

Immutable journal reservations precede every record. Two external heads bind
the journal frontier, so restoring only SQLite, losing a record, or rewinding
one external head cannot make a begun effect appear never dispatched. Removing
or restoring the entire external witness set is outside supported recovery.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from functools import wraps
import errno
import hashlib
import math
import os
import re
import stat
import threading
import time
import uuid

from . import storage_lifecycle as lifecycle, sync_media as media
from . import sync_source_readmit_contract as contract
from . import sync_state
from .sync_protocol import SyncError

DIRECTORY = ".reno-source-continuity-v1"
LOCK_NAME = ".reno-source-continuity-v1.lock"
INTENT_NAME = ".reno-source-continuity-v1.intent.json"
SEAL_NAME = ".reno-source-continuity-v1.seal.json"
DB_HEAD_NAME = ".reno-source-continuity-v1.head.json"
CHECKPOINT_HEAD_NAME = ".reno-source-continuity-v1.checkpoint-head.json"
LOCK_MARKER = b"reno-source-continuity-lock-v1\n"
MAX_RECORDS = 4096
MAX_INTERNAL_PARTS = 32
MAX_RECORD_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEADLINE_SECONDS = 10.0
KINDS = ("registrations", "effects", "tombstones")
_CONTRACT_FIELDS = frozenset(("format_version", "continuity_id", "owner_profile_sha256",
    "source_identity", "target_identity", "target_epoch", "activated_at"))
_HEAD_FIELDS = frozenset(("format_version", "continuity_id", "ordinal", "sha256"))
_JOURNAL_FIELDS = frozenset(("format_version", "continuity_id", "ordinal", "previous_sha256",
    "kind", "key", "record_sha256", "record", "state"))
_EFFECT_FIELDS = frozenset(("format_version", "continuity_id", "original_request_id",
    "original_request_sha256", "original_kind", "core_sha256", "item_id",
    "registration_sha256", "begun_at"))
_ORDINAL = re.compile(r"[0-9]{8}\.json\Z")
_PART = re.compile(r"\.sync-[0-9a-f]{32}\.part\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")
_CURRENT = ContextVar("reno_source_continuity_guard", default=None)


def _fail(code="INVALID", status=409):
    raise SyncError("Source continuity evidence could not be verified",
                    "SYNC_SOURCE_CONTINUITY_" + code, status) from None


@contextmanager
def _errors():
    try:
        yield
    except SyncError as exc:
        if exc.code.startswith("SYNC_SOURCE_CONTINUITY_"):
            raise
        _fail("UNSAFE_PATH" if "UNSAFE_PATH" in exc.code else "INVALID")
    except FileNotFoundError:
        _fail("MISSING")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError,
            OverflowError, RecursionError):
        _fail()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail()


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()


def _integer(value, *, minimum=0, maximum=2 ** 63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _time(value):
    if type(value) is not str or not _TIME.fullmatch(value):
        _fail()
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        with _errors():
            return function(*args, **kwargs)
    return checked


def _encoded(value):
    return contract.canonical_json(value, maximum=MAX_RECORD_BYTES).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def profile_sha256(profile):
    lifecycle._profile(profile)
    return _digest({key: lifecycle._key(getattr(profile, key))
        for key in ("database", "media", "reports", "checkpoints")})


def validate_contract(value, *, profile=None):
    """Validate representation only; activation is an explicit SQL operation."""
    with _errors():
        _exact(value, _CONTRACT_FIELDS)
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail()
        _uuid(value["continuity_id"])
        _sha(value["owner_profile_sha256"])
        _integer(value["target_epoch"], minimum=1)
        _time(value["activated_at"])
        for name in ("source_identity", "target_identity"):
            _exact(value[name], ("library_id", "node_id"))
            for child in value[name].values():
                _uuid(child)
        if any(value["source_identity"][key] == value["target_identity"][key]
               for key in ("library_id", "node_id")):
            _fail()
        if profile is not None and value["owner_profile_sha256"] != profile_sha256(profile):
            _fail("OWNER_MISMATCH")
        _encoded(value)
        return deepcopy(value)


def _record(kind, value, activation):
    if kind == "registrations":
        contract._registration_shape(value)
    elif kind == "tombstones":
        contract._dispose_shape_versioned(value)
        if value["disposition"] != "not_applied":
            _fail()
    elif kind == "effects":
        _exact(value, _EFFECT_FIELDS)
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail()
        _uuid(value["original_request_id"])
        for field in ("original_request_sha256", "core_sha256", "registration_sha256"):
            _sha(value[field])
        if value["original_kind"] not in {"budget_defer", "budget_resume", "source_fail"}:
            _fail()
        _integer(value["item_id"], minimum=1)
        _time(value["begun_at"])
    else:
        _fail()
    if value["continuity_id"] != activation["continuity_id"]:
        _fail("CONTRACT_MISMATCH")
    if kind != "effects" and value["replayed"] is not False:
        _fail()
    _encoded(value)
    return deepcopy(value)


def _same_original(first, second):
    fields = ("original_request_id", "original_request_sha256", "original_kind",
              "core_sha256", "item_id", "continuity_id")
    if any(first[key] != second[key] for key in fields):
        _fail("REQUEST_MISMATCH")


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Lock:
    def __init__(self, directory, stream):
        self.directory, self.stream = directory, stream
        self.identity = _signature(os.fstat(stream.fileno()))
        self.path_identity = _signature(directory.entry_stat(LOCK_NAME))
        if self.path_identity[:4] != self.identity[:4]:
            _fail("UNSAFE_PATH")
        self.held = False

    def verify(self, *, marker=True):
        self.directory.verify()
        current = self.directory.entry_stat(LOCK_NAME)
        actual = os.fstat(self.stream.fileno())
        media._safe_stat(current, directory=False)
        if (current.st_nlink != 1 or actual.st_nlink != 1
                or _signature(current) != self.path_identity or _signature(actual) != self.identity):
            _fail("UNSAFE_PATH")
        if os.name != "nt" and (actual.st_uid != os.geteuid() or stat.S_IMODE(actual.st_mode) & 0o077):
            _fail("UNSAFE_PATH")
        if marker:
            self.stream.seek(0)
            if self.stream.read(len(LOCK_MARKER) + 1) != LOCK_MARKER:
                _fail("LOCK_INVALID")

    def acquire(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            self.verify(marker=False)
            try:
                if os.name == "nt":
                    self.stream.seek(0)
                    media.msvcrt.locking(self.stream.fileno(), media.msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.held = True
                self.verify()
                return
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(exc, "winerror", None) != 33:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("BUSY")
                time.sleep(min(remaining, 0.025))

    def release(self):
        if not self.held:
            return
        self.held = False
        if os.name == "nt":
            self.stream.seek(0)
            media.msvcrt.locking(self.stream.fileno(), media.msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)


def _lock_stream(directory):
    if os.name != "nt":
        return directory.open_file(LOCK_NAME, write=True)
    # Other writers must open the same inode before contending on its byte.
    handle = media._kernel.CreateFileW(media._native_path(directory.path / LOCK_NAME),
        0xC0000000, 3, None, 3, 0x00200000, None)
    if handle == media.ctypes.c_void_p(-1).value:
        raise media.ctypes.WinError(media.ctypes.get_last_error())
    try:
        info = media._HandleInfo()
        if not media._kernel.GetFileInformationByHandle(handle, media.ctypes.byref(info)):
            raise media.ctypes.WinError(media.ctypes.get_last_error())
        if info.attributes & (media._REPARSE | 0x10) or info.links != 1:
            _fail("UNSAFE_PATH")
        descriptor = media.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDWR)
    except BaseException:
        media._kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "r+b", buffering=0)


def _names(directory, allowed, maximum, check):
    ordinary_count = part_count = 0
    with os.scandir(directory.fd if os.name != "nt" else media._native_path(directory.path)) as entries:
        for entry in entries:
            check()
            info = directory.entry_stat(entry.name)
            media._safe_stat(info, directory=entry.name in {"journal", *KINDS})
            if _PART.fullmatch(entry.name):
                # A killed atomic publisher may leave its unpublished private
                # part. Keep it, bound it, and never treat it as a reservation
                # or effect. Published journal/file loss remains fail-closed.
                part_count += 1
                if part_count > MAX_INTERNAL_PARTS:
                    _fail("COUNT_LIMIT")
                if info.st_nlink != 1 or (os.name != "nt" and
                        (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
                    _fail("UNSAFE_PATH")
                if info.st_size > MAX_RECORD_BYTES:
                    _fail("BYTES_LIMIT")
                continue
            ordinary_count += 1
            if ordinary_count > maximum:
                _fail("COUNT_LIMIT")
            if not allowed(entry.name):
                _fail("UNEXPECTED_FILE")
    directory.verify()


class ContinuityGuard:
    """A live lock/handle owner, not a caller-supplied admission certificate."""
    def __init__(self, profile, root, checkpoint_root, lock, stack):
        self.profile, self.root, self.checkpoint_root = profile, root, checkpoint_root
        self.lock, self.stack = lock, stack
        self.thread = threading.get_ident()
        self.started = time.monotonic()
        self.total_bytes = 0
        self.pins = []
        self.pin_index = {}
        self.head_pins = {}
        self._snapshot = None

    def _check(self):
        if _CURRENT.get() is not self or threading.get_ident() != self.thread or not self.lock.held:
            _fail("LOCK_REQUIRED")
        lifecycle.require_guard(self.profile)
        self.lock.verify()
        self.checkpoint_root.verify()
        if time.monotonic() - self.started > DEADLINE_SECONDS:
            _fail("DEADLINE_LIMIT")

    def _read(self, directory, name, *, pin=True):
        self._check()
        key = (directory.path, name)
        existing = self.pin_index.get(key) if pin else None
        if existing is not None:
            _, _, stream, signature, path_signature, _ = existing
            if (_signature(directory.entry_stat(name)) != path_signature
                    or _signature(os.fstat(stream.fileno())) != signature):
                _fail("CHANGED")
        else:
            stream = directory.open_file(name)
            if pin:
                self.stack.enter_context(stream)
        try:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_RECORD_BYTES or (os.name != "nt" and before.st_mode & 0o077):
                _fail("BYTES_LIMIT" if before.st_size > MAX_RECORD_BYTES else "UNSAFE_PATH")
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            raw = os.read(stream.fileno(), MAX_RECORD_BYTES + 1)
            after = os.fstat(stream.fileno())
            entry = directory.entry_stat(name)
            if (len(raw) > MAX_RECORD_BYTES or entry.st_nlink != 1 or before.st_nlink != 1
                    or _signature(before) != _signature(after) or _signature(entry)[:4] != _signature(after)[:4]):
                _fail("CHANGED")
            self.total_bytes += len(raw)
            if self.total_bytes > MAX_TOTAL_BYTES:
                _fail("BYTES_LIMIT")
            value = sync_state._decode(raw)
            if _encoded(value) != raw:
                _fail("RECORD_INVALID")
            if pin and existing is None:
                evidence = (directory, name, stream, _signature(after), _signature(entry), hashlib.sha256(raw).digest())
                self.pins.append(evidence)
                self.pin_index[key] = evidence
            elif existing is not None and hashlib.sha256(raw).digest() != existing[-1]:
                _fail("CHANGED")
            self._check()
            return value
        finally:
            if not pin:
                stream.close()

    def _verify_pins(self, *, full):
        self._check()
        for directory, name, stream, signature, path_signature, sha in self.pins:
            directory.verify()
            entry = directory.entry_stat(name)
            actual = os.fstat(stream.fileno())
            if entry.st_nlink != 1 or actual.st_nlink != 1 or _signature(entry) != path_signature or _signature(actual) != signature:
                _fail("CHANGED")
            if full:
                os.lseek(stream.fileno(), 0, os.SEEK_SET)
                if hashlib.sha256(os.read(stream.fileno(), MAX_RECORD_BYTES + 1)).digest() != sha:
                    _fail("CHANGED")
        for directory, name, signature, expected in self.head_pins.values():
            if _signature(directory.entry_stat(name)) != signature or self._read(directory, name, pin=False) != expected:
                _fail("HEAD_CHANGED")

    def verify(self):
        """Full content check, outside the SQL writer on normal service exit."""
        self._verify_pins(full=True)
        if self._snapshot is not None:
            self._verify_names(self._snapshot)

    def _copy_snapshot(self, value):
        return {key: child if key == "ledger" else deepcopy(child) for key, child in value.items()}

    def _verify_names(self, observed):
        ledger = observed.get("ledger")
        if ledger is None:
            for directory, name in ((self.root, DIRECTORY), (self.root, INTENT_NAME),
                    (self.root, DB_HEAD_NAME), (self.checkpoint_root, SEAL_NAME),
                    (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                try:
                    directory.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("CHANGED")
            return
        _names(ledger, lambda name: name in {"contract.json", "journal", *KINDS}, 5, self._check)
        journal = self._directory(ledger, "journal")
        expected = {f"{ordinal:08d}.json" for ordinal in range(1, observed["head"]["ordinal"] + 1)}
        _names(journal, lambda name: name in expected, MAX_RECORDS * 2, self._check)
        if {name for name in journal.names() if not _PART.fullmatch(name)} != expected:
            _fail("JOURNAL_INVALID")
        for kind in KINDS:
            directory = self._directory(ledger, kind)
            allowed = {key + ".json" for key in observed["entries"][kind]}
            _names(directory, lambda name: name in allowed, MAX_RECORDS, self._check)
            actual = {name for name in directory.names() if not _PART.fullmatch(name)}
            required = {key + ".json" for key, entry in observed["entries"][kind].items()
                        if entry["state"] == "committed"}
            if not required <= actual:
                _fail("RECORD_MISSING")

    @_checked
    def verify_for_commit(self):
        """Recheck the same live owner and captured objects without old SHA reads.

        Windows pins deny writes/deletes. POSIX same-handle and path
        size/mtime/ctime/inode observations catch replacement or in-place writes.
        Complete contents were hashed before BEGIN and are hashed at exit again.
        This check does not establish SQL or caller admission authority.
        """
        if self._snapshot is None:
            _fail("CLOSURE_REQUIRED")
        self._verify_pins(full=False)
        self._verify_names(self._snapshot)

    @_checked
    def view(self):
        """Return a detached view of this guard's previously captured closure."""
        self.verify_for_commit()
        return self._copy_snapshot(self._snapshot)

    def _publication_view(self, *, resume=None):
        if self._snapshot is None:
            return self.observe(resume=resume)
        return self.view()

    def _capture_heads(self, head):
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            if self._read(directory, name, pin=False) != head:
                _fail("HEAD_CHANGED")
            self.head_pins[(directory.path, name)] = (directory, name,
                _signature(directory.entry_stat(name)), deepcopy(head))

    def _directory(self, parent, name, *, create=False):
        return self.stack.enter_context(media._Directory(parent.path / name, parent=parent, create=create))

    def _head(self, activation, ordinal, sha):
        return {"format_version": 1, "continuity_id": activation["continuity_id"],
                "ordinal": ordinal, "sha256": sha}

    @_checked
    def activate_files(self, activation):
        """Explicit file setup only. The caller must separately commit SQL activation.

        An interrupted unsealed setup can resume only while its ledger is empty.
        No old controls gain negative proof through this operation.
        """
        self._check()
        activation = validate_contract(activation, profile=self.profile)
        try:
            original = self._read(self.root, INTENT_NAME)
        except FileNotFoundError:
            for directory, name in ((self.root, DIRECTORY), (self.root, DB_HEAD_NAME),
                    (self.checkpoint_root, SEAL_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                try:
                    directory.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("ACTIVATION_INCOMPLETE")
            sync_state._write(self.root, INTENT_NAME, _encoded(activation), immutable=True)
        else:
            if original != activation:
                _fail("CONTRACT_MISMATCH")
        try:
            seal = self._read(self.checkpoint_root, SEAL_NAME)
        except FileNotFoundError:
            seal = None
        if seal is not None:
            if seal != activation:
                _fail("CONTRACT_MISMATCH")
            if self.observe()["contract"] != activation:
                _fail("CONTRACT_MISMATCH")
            return activation
        ledger = self._directory(self.root, DIRECTORY, create=True)
        _names(ledger, lambda name: name in {"contract.json", "journal", *KINDS}, 5, self._check)
        try:
            existing = self._read(ledger, "contract.json")
        except FileNotFoundError:
            sync_state._write(ledger, "contract.json", _encoded(activation), immutable=True)
        else:
            if existing != activation:
                _fail("CONTRACT_MISMATCH")
        for name in ("journal", *KINDS):
            directory = self._directory(ledger, name, create=True)
            _names(directory, lambda _: False, 0, self._check)
        head = self._head(activation, 0, _digest(activation))
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            try:
                existing = self._read(directory, name, pin=False)
            except FileNotFoundError:
                sync_state._write(directory, name, _encoded(head), immutable=True)
            else:
                if existing != head:
                    _fail("ACTIVATION_INCOMPLETE")
        sync_state._write(self.checkpoint_root, SEAL_NAME, _encoded(activation), immutable=True)
        self._capture_heads(head)
        self._snapshot = {"contract": deepcopy(activation), "entries": {kind: {} for kind in KINDS},
            "incomplete": [], "head": head, "ledger": ledger}
        return activation

    @_checked
    def observe(self, *, resume=None, resume_request=None):
        """Read a bounded journal closure. Optional resume is one exact record.

        A reservation with a missing file is reported incomplete, not absent.
        Interrupted head publication may only resume the same kind/key/SHA.
        """
        self._check()
        if resume is not None and resume_request is not None:
            _fail()
        if resume_request is not None:
            if type(resume_request) is not tuple or len(resume_request) != 3 or resume_request[0] not in KINDS:
                _fail()
            _uuid(resume_request[1])
            _sha(resume_request[2])
        self._snapshot = None
        try:
            activation = self._read(self.root, INTENT_NAME)
        except FileNotFoundError:
            for directory, name in ((self.root, DIRECTORY), (self.root, DB_HEAD_NAME),
                    (self.checkpoint_root, SEAL_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                try:
                    directory.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("ACTIVATION_INCOMPLETE")
            observed = {"contract": None, "entries": {kind: {} for kind in KINDS}, "incomplete": []}
            self._snapshot = self._copy_snapshot(observed)
            return observed
        activation = validate_contract(activation, profile=self.profile)
        if self._read(self.checkpoint_root, SEAL_NAME) != activation:
            _fail("CONTRACT_MISMATCH")
        ledger = self._directory(self.root, DIRECTORY)
        _names(ledger, lambda name: name in {"contract.json", "journal", *KINDS}, 5, self._check)
        if self._read(ledger, "contract.json") != activation:
            _fail("CONTRACT_MISMATCH")
        heads = [self._read(directory, name, pin=False) for directory, name in
            ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME))]
        for head in heads:
            _exact(head, _HEAD_FIELDS)
            if type(head["format_version"]) is not int or head["format_version"] != 1 or head["continuity_id"] != activation["continuity_id"]:
                _fail("HEAD_INVALID")
            _integer(head["ordinal"], maximum=MAX_RECORDS * 2)
            _sha(head["sha256"])
        journal = self._directory(ledger, "journal")
        _names(journal, lambda name: bool(_ORDINAL.fullmatch(name)), MAX_RECORDS * 2, self._check)
        # List after the bounded scandir; all entries must form this exact chain.
        names = sorted(name for name in journal.names() if not _PART.fullmatch(name))
        entries = {kind: {} for kind in KINDS}
        hashes = [_digest(activation)]
        events = []
        for ordinal, name in enumerate(names, 1):
            if name != f"{ordinal:08d}.json":
                _fail("JOURNAL_INVALID")
            event = self._read(journal, name)
            _exact(event, _JOURNAL_FIELDS)
            if (type(event["format_version"]) is not int or event["format_version"] != 1
                    or type(event["ordinal"]) is not int or event["ordinal"] != ordinal
                    or event["continuity_id"] != activation["continuity_id"]
                    or event["previous_sha256"] != hashes[-1] or event["kind"] not in KINDS
                    or event["state"] not in {"reserved", "committed"}):
                _fail("JOURNAL_INVALID")
            _uuid(event["key"])
            record = _record(event["kind"], event["record"], activation)
            if record["original_request_id"] != event["key"] or event["record_sha256"] != _digest(record):
                _fail("JOURNAL_INVALID")
            previous = entries[event["kind"]].get(event["key"])
            if previous is None:
                if event["state"] != "reserved":
                    _fail("JOURNAL_INVALID")
            elif previous["state"] != "reserved" or event["state"] != "committed" or previous["record"] != record:
                _fail("JOURNAL_INVALID")
            entries[event["kind"]][event["key"]] = {"state": event["state"], "record": record}
            if sum(len(values) for values in entries.values()) > MAX_RECORDS:
                _fail("COUNT_LIMIT")
            events.append(event)
            hashes.append(_digest(event))
        latest = self._head(activation, len(events), hashes[-1])
        for head in heads:
            if head["ordinal"] > len(events) or hashes[head["ordinal"]] != head["sha256"]:
                _fail("HEAD_INVALID")
        if any(head != latest for head in heads):
            if (resume is None and resume_request is None) or not events or any(head["ordinal"] < len(events) - 1 for head in heads):
                _fail("HEAD_INCOMPLETE")
            if resume is not None:
                kind, key, sha = resume
                if any(events[-1][field] != expected for field, expected in
                       (("kind", kind), ("key", key), ("record_sha256", sha))):
                    _fail("HEAD_INCOMPLETE")
            else:
                kind, request_id, request_sha = resume_request
                record = events[-1]["record"]
                id_field, sha_field = ("original_request_id", "original_request_sha256") if kind == "effects" else ("request_id", "request_sha256")
                if (events[-1]["kind"] != kind or record[id_field] != request_id or record[sha_field] != request_sha):
                    _fail("HEAD_INCOMPLETE")
            for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                sync_state._write(directory, name, _encoded(latest))
        self._capture_heads(latest)
        incomplete = []
        for kind in KINDS:
            directory = self._directory(ledger, kind)
            allowed = {key + ".json" for key in entries[kind]}
            _names(directory, lambda name: name in allowed, MAX_RECORDS, self._check)
            for key, entry in entries[kind].items():
                try:
                    actual = self._read(directory, key + ".json")
                except FileNotFoundError:
                    if entry["state"] == "committed":
                        _fail("RECORD_MISSING")
                    incomplete.append((kind, key))
                    continue
                if actual != entry["record"]:
                    _fail("RECORD_INVALID")
                if entry["state"] != "committed":
                    incomplete.append((kind, key))
        for kind in ("effects", "tombstones"):
            for key, entry in entries[kind].items():
                registration = entries["registrations"].get(key)
                if registration is None or registration["state"] != "committed":
                    _fail("REGISTRATION_REQUIRED")
                _same_original(entry["record"], registration["record"])
                if entry["record"]["registration_sha256"] != registration["record"]["registration_sha256"]:
                    _fail("REQUEST_MISMATCH")
                if key in entries["effects"] and key in entries["tombstones"]:
                    _fail("OUTCOME_MISMATCH")
        self.verify()
        observed = {"contract": activation, "entries": entries, "incomplete": incomplete,
                    "head": latest, "ledger": ledger}
        self._snapshot = self._copy_snapshot(observed)
        return observed

    def _append(self, observed, kind, key, record, state):
        self.verify_for_commit()
        if observed != self._snapshot:
            _fail("CHANGED")
        head = observed["head"]
        event = {"format_version": 1, "continuity_id": observed["contract"]["continuity_id"],
            "ordinal": head["ordinal"] + 1, "previous_sha256": head["sha256"],
            "kind": kind, "key": key, "record_sha256": _digest(record), "record": record, "state": state}
        if event["ordinal"] > MAX_RECORDS * 2:
            _fail("COUNT_LIMIT")
        updated = self._copy_snapshot(observed)
        existing = updated["entries"][kind].get(key)
        if (state == "reserved" and existing is not None or state == "committed" and
                (existing is None or existing["state"] != "reserved" or existing["record"] != record)):
            _fail("REQUEST_MISMATCH")
        self._snapshot = None
        journal = self._directory(observed["ledger"], "journal")
        sync_state._write(journal, f"{event['ordinal']:08d}.json", _encoded(event), immutable=True)
        latest = self._head(observed["contract"], event["ordinal"], _digest(event))
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            sync_state._write(directory, name, _encoded(latest))
        self._capture_heads(latest)
        if self._read(journal, f"{event['ordinal']:08d}.json") != event:
            _fail("CHANGED")
        updated["entries"][kind][key] = {"state": state, "record": deepcopy(record)}
        updated["head"] = latest
        updated["incomplete"] = [item for item in updated["incomplete"] if item != (kind, key)]
        if state == "reserved":
            updated["incomplete"].append((kind, key))
        self._snapshot = updated

    def _publish(self, kind, record):
        self._check()
        key = record["original_request_id"]
        observed = self._publication_view(resume=(kind, key, _digest(record)))
        if observed["contract"] is None:
            _fail("ACTIVATION_REQUIRED")
        record = _record(kind, record, observed["contract"])
        if any(item != (kind, key) for item in observed["incomplete"]):
            _fail("RECORD_INCOMPLETE")
        existing = observed["entries"][kind].get(key)
        if existing is not None:
            if existing["record"] != record:
                _fail("REQUEST_MISMATCH")
            if existing["state"] == "committed":
                return deepcopy(record)
        else:
            if sum(len(values) for values in observed["entries"].values()) >= MAX_RECORDS:
                _fail("COUNT_LIMIT")
            if kind != "registrations":
                registration = observed["entries"]["registrations"].get(key)
                if registration is None or registration["state"] != "committed":
                    _fail("REGISTRATION_REQUIRED")
                _same_original(record, registration["record"])
                if record["registration_sha256"] != registration["record"]["registration_sha256"]:
                    _fail("REQUEST_MISMATCH")
                opposite = "effects" if kind == "tombstones" else "tombstones"
                if key in observed["entries"][opposite]:
                    _fail("OUTCOME_MISMATCH")
            self._append(observed, kind, key, record, "reserved")
            observed = self.view()
        directory = self._directory(observed["ledger"], kind)
        try:
            actual = self._read(directory, key + ".json")
        except FileNotFoundError:
            sync_state._write(directory, key + ".json", _encoded(record), immutable=True)
            if self._read(directory, key + ".json") != record:
                _fail("CHANGED")
        else:
            if actual != record:
                _fail("REQUEST_MISMATCH")
        self._append(observed, kind, key, record, "committed")
        self.verify_for_commit()
        return deepcopy(record)

    @_checked
    def register(self, request, result):
        request = contract.normalize_register_request(request)
        result = contract.normalize_register_result(request, result)
        observed = self._publication_view(resume=("registrations", result["original_request_id"], _digest(result)))
        activation = observed["contract"]
        if activation is None:
            _fail("ACTIVATION_REQUIRED")
        if (request["continuity"] != {"format_version": 1, "continuity_id": activation["continuity_id"],
                "contract_sha256": _digest(activation), "target_epoch": activation["target_epoch"]}
                or {key: request["source"][key] for key in ("library_id", "node_id")} != activation["source_identity"]
                or request["target"] != activation["target_identity"]):
            _fail("CONTRACT_MISMATCH")
        return self._publish("registrations", result)

    @_checked
    def begin_effect(self, registration, *, begun_at):
        """Persist a possible-effect witness BEFORE the caller changes SQL.

        Existing begin evidence does not prove a missing SQL effect is safe to
        execute again. Only the SQL consumer can resolve an exact positive replay.
        """
        contract._registration_shape(registration)
        marker = {key: registration[key] for key in ("continuity_id", "original_request_id",
            "original_request_sha256", "original_kind", "core_sha256", "item_id", "registration_sha256")}
        marker.update(format_version=1, begun_at=begun_at)
        return self._publish("effects", marker)

    @_checked
    def commit_not_applied(self, request, result):
        """Persist a supplied closed negative result; SQL authority is separate.

        Any reserved or committed effect for this original UUID rejects it.
        This method never creates a negative result or interprets receipt absence.
        """
        result = contract.normalize_dispose_result(request, result)
        if result["disposition"] != "not_applied":
            _fail("OUTCOME_MISMATCH")
        return self._publish("tombstones", result)


@contextmanager
def locked(profile, *, initialize=False, timeout=1.0):
    """Acquire the independent OS writer lock; never open SQLite here.

    Ordinary acquisition requires existing facilities and lifecycle SH. Only
    an explicitly authorized activation path may pass initialize=True.
    """
    lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    if type(initialize) is not bool or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
        _fail("INVALID", 400)
    if _CURRENT.get() is not None:
        _fail("BUSY")
    stack = ExitStack()
    lock = None
    token = None
    failure = None
    try:
        with _errors():
            root = stack.enter_context(media._Directory(profile.database.parent))
            checkpoint_root = stack.enter_context(media._Directory(profile.checkpoints))
            if initialize:
                try:
                    root.entry_stat(LOCK_NAME)
                except FileNotFoundError:
                    try:
                        sync_state._write(root, LOCK_NAME, LOCK_MARKER, immutable=True)
                    except FileExistsError:
                        pass
            stream = stack.enter_context(_lock_stream(root))
            lock = _Lock(root, stream)
            lock.acquire(timeout)
            guard = ContinuityGuard(profile, root, checkpoint_root, lock, stack)
            token = _CURRENT.set(guard)
        # Preserve the caller's original SQL/callback exception; failures must
        # not be disguised as file corruption by the facility error boundary.
        try:
            yield guard
        except BaseException as exc:
            failure = exc
            raise
        else:
            with _errors():
                guard.verify()
    finally:
        if token is not None:
            _CURRENT.reset(token)
        try:
            if lock is not None:
                lock.release()
        except BaseException:
            if failure is None:
                raise
        finally:
            stack.close()
