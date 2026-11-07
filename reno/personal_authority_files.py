"""Filesystem primitives only; no SQL, config, grant or activation.

All roots are explicit lexical absolute paths. A declaration is merely stored
bytes. A live guard proves held filesystem objects, never personal write,
primary, peer, model-budget or dispatch permission. In particular files-seal is
not the authority protocol's init_seal. Publication failures retain unknown
temporary files and fail closed; this module never removes an entry or repairs
an existing ACL. Pre-reservation leftovers cannot be resumed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from . import personal_preservation_files as files
from . import sync_media as safe


if os.name == "nt":
    class _LockOverlapped(safe.ctypes.Structure):
        _fields_ = [("internal", safe.ctypes.c_size_t),
                    ("internal_high", safe.ctypes.c_size_t),
                    ("offset", safe.wintypes.DWORD),
                    ("offset_high", safe.wintypes.DWORD),
                    ("event", safe.wintypes.HANDLE)]

    # DLL instances cache their own function objects. Lifecycle locks use a
    # different OVERLAPPED class; their strict pointer ABI must stay intact.
    _lock_kernel = safe.ctypes.WinDLL("kernel32", use_last_error=True)
    _lock_kernel.LockFileEx.argtypes = [safe.wintypes.HANDLE,
        safe.wintypes.DWORD, safe.wintypes.DWORD, safe.wintypes.DWORD,
        safe.wintypes.DWORD, safe.ctypes.POINTER(_LockOverlapped)]
    _lock_kernel.LockFileEx.restype = safe.wintypes.BOOL
    _lock_kernel.UnlockFileEx.argtypes = [safe.wintypes.HANDLE,
        safe.wintypes.DWORD, safe.wintypes.DWORD, safe.wintypes.DWORD,
        safe.ctypes.POINTER(_LockOverlapped)]
    _lock_kernel.UnlockFileEx.restype = safe.wintypes.BOOL


_TOKEN = object()
_LIVE_GUARDS = {}
_ROOT_NAMES = frozenset({"init-reservation.json", "declared-binding.json", "authority.lock",
                         "files-seal.json", "facts", "head.json"})
_REQUIRED = _ROOT_NAMES - {"head.json"}
_LOCK_BYTES = b"\x00"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FACT_ID = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_IDENTITY_FIELDS = frozenset({"device", "inode"})
_FILE_FIELDS = frozenset({"device", "inode", "byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"})
_RESERVATION_FIELDS = frozenset({"format_version", "init_operation_id", "binding_sha256", "root_identity"})
_BINDING_FIELDS = frozenset({"domain_id", "facility_id", "node_id", "profile_sha256"})
_SEAL_FIELDS = frozenset({"format_version", "init_operation_id", "reservation_sha256", "binding_sha256",
                          "root_identity", "facts_identity", "lock_path_identity", "lock_handle_identity"})
_HEAD_FIELDS = frozenset({"format_version", "files_seal_sha256", "fact_count", "total_bytes", "facts_sha256"})


class FilePrimitiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(value, code):
    if not value:
        raise FilePrimitiveError(code)


@dataclass(frozen=True, slots=True)
class Limits:
    max_metadata_bytes: int = 4096
    max_facts: int = 64
    max_fact_bytes: int = 32768
    max_total_bytes: int = 524288

    def __post_init__(self):
        for field, maximum in (("max_metadata_bytes", 4096), ("max_facts", 64),
                               ("max_fact_bytes", 32768), ("max_total_bytes", 524288)):
            value = getattr(self, field)
            _require(type(value) is int and 0 < value <= maximum, "FILES_LIMIT_INVALID")


DEFAULT_LIMITS = Limits()


def _limits(value):
    _require(type(value) is Limits, "FILES_LIMIT_INVALID")
    value.__post_init__()
    return Limits(value.max_metadata_bytes, value.max_facts, value.max_fact_bytes, value.max_total_bytes)


def _root(value):
    _require(type(value) in (str, Path) or isinstance(value, Path), "FILES_ROOT_INVALID")
    text = os.fspath(value)
    _require(bool(text) and ".." not in re.split(r"[/\\]", text), "FILES_ROOT_INVALID")
    path = Path(text)
    _require(path.is_absolute() and path.parent != path and bool(path.name), "FILES_ROOT_INVALID")
    safe._entry_name(path.name)
    return path  # Do not resolve: reparse/link checks belong to held low-level opens.


def _operation(value):
    _require(type(value) is str, "FILES_OPERATION_INVALID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise FilePrimitiveError("FILES_OPERATION_INVALID") from None
    _require(str(parsed) == value and parsed.int != 0, "FILES_OPERATION_INVALID")
    return value


def _sha(value):
    _require(type(value) is str and _SHA.fullmatch(value), "FILES_METADATA_INVALID")
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "FILES_METADATA_INVALID")
        result[key] = value
    return result


def _non_integer(_value):
    raise FilePrimitiveError("FILES_METADATA_INVALID")


def _json(raw, fields, limits):
    _require(type(raw) is bytes and 0 < len(raw) <= limits.max_metadata_bytes, "FILES_METADATA_LIMIT")
    # Metadata depth is tiny and closed; reject nested resource abuse before JSON.
    depth = 0
    in_string = escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                in_string = False
        elif char == 34:
            in_string = True
        elif char in (91, 123):
            depth += 1
            _require(depth <= 3, "FILES_METADATA_INVALID")
        elif char in (93, 125):
            depth -= 1
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_non_integer, parse_constant=_non_integer)
        _require(type(value) is dict and set(value) == fields and _canonical(value) == raw,
                 "FILES_METADATA_INVALID")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError):
        raise FilePrimitiveError("FILES_METADATA_INVALID") from None


def _identity(value, *, file=False):
    wanted = _FILE_FIELDS if file else _IDENTITY_FIELDS
    _require(type(value) is dict and set(value) == wanted, "FILES_METADATA_INVALID")
    for key in wanted & {"device", "inode", "mtime_ns", "ctime_ns"}:
        item = value[key]
        _require(type(item) is str and re.fullmatch(r"0|[1-9][0-9]*", item), "FILES_METADATA_INVALID")
    if file:
        _require(type(value["byte_size"]) is int and value["byte_size"] == 1
                 and type(value["mode"]) is int and value["mode"] > 0
                 and type(value["nlink"]) is int and value["nlink"] == 1, "FILES_METADATA_INVALID")
    return value


def _binding(raw, limits):
    value = _json(raw, _BINDING_FIELDS, limits)
    for key in ("domain_id", "facility_id", "node_id"):
        _operation(value[key])
    _sha(value["profile_sha256"])
    return value


def _reservation(raw, limits):
    value = _json(raw, _RESERVATION_FIELDS, limits)
    _require(type(value["format_version"]) is int and value["format_version"] == 1,
             "FILES_METADATA_INVALID")
    _operation(value["init_operation_id"])
    _sha(value["binding_sha256"])
    _identity(value["root_identity"])
    return value


def _seal(raw, limits):
    value = _json(raw, _SEAL_FIELDS, limits)
    _require(type(value["format_version"]) is int and value["format_version"] == 1,
             "FILES_METADATA_INVALID")
    _operation(value["init_operation_id"])
    for key in ("reservation_sha256", "binding_sha256"):
        _sha(value[key])
    for key in ("root_identity", "facts_identity"):
        _identity(value[key])
    for key in ("lock_path_identity", "lock_handle_identity"):
        _identity(value[key], file=True)
    return value


def _head(raw, limits):
    value = _json(raw, _HEAD_FIELDS, limits)
    _require(type(value["format_version"]) is int and value["format_version"] == 1,
             "FILES_METADATA_INVALID")
    for key in ("files_seal_sha256", "facts_sha256"):
        _sha(value[key])
    _require(type(value["fact_count"]) is int and 0 <= value["fact_count"] <= limits.max_facts
             and type(value["total_bytes"]) is int and 0 <= value["total_bytes"] <= limits.max_total_bytes,
             "FILES_METADATA_INVALID")
    return value


@contextmanager
def _filesystem():
    try:
        yield
    except FilePrimitiveError:
        raise
    except files.PreservationFileError as error:
        raise FilePrimitiveError(error.code) from None
    except safe.SyncError:
        raise FilePrimitiveError("FILES_UNSAFE_PATH") from None
    except OSError:
        raise FilePrimitiveError("FILES_IO_UNAVAILABLE") from None


def _scan(directory, *, maximum, check, root=False, fact_limits=None):
    directory.verify()
    names, total = set(), 0
    with os.scandir(safe._native_path(directory.path) if os.name == "nt" else directory.fd) as entries:
        for number, entry in enumerate(entries, 1):
            check()
            _require(number <= maximum, "FILES_DIRECTORY_LIMIT")
            name = safe._entry_name(entry.name)
            if root:
                _require(name in _ROOT_NAMES, "FILES_UNKNOWN_ENTRY")
                safe._safe_stat(directory.entry_stat(name), directory=name == "facts")
            else:
                _require(name.endswith(".json") and _FACT_ID.fullmatch(name[:-5]), "FILES_UNKNOWN_ENTRY")
                info = directory.entry_stat(name)
                safe._safe_stat(info, directory=False)
                if fact_limits is not None:
                    _require(0 < info.st_size <= fact_limits.max_fact_bytes, "FILES_FACT_BYTES_LIMIT")
                    total += info.st_size
                    _require(total <= fact_limits.max_total_bytes, "FILES_TOTAL_BYTES_LIMIT")
            names.add(name)
    directory.verify()
    check()
    return names


def _hold(stack, path, maximum, check):
    files.require_private(path, directory=False)
    held = stack.enter_context(files.FileHold(path, maximum=maximum, check_cancelled=check))
    files.require_private(path, directory=False)
    held.verify()
    return held


def _open_lock(directory, *, lock_name="authority.lock"):
    _require(type(lock_name) is str and lock_name in ("authority.lock", "handoff.lock"),
             "FILES_LOCK_OPTIONS_INVALID")
    if os.name != "nt":
        return directory.open_file(lock_name)  # no create and no lock=True
    handle = safe._kernel.CreateFileW(safe._native_path(directory.path / lock_name),
                                      0x80000000, 3, None, 3, 0x00200000, None)
    if handle == safe.ctypes.c_void_p(-1).value:
        raise safe.ctypes.WinError(safe.ctypes.get_last_error())
    try:
        info = safe._HandleInfo()
        if not safe._kernel.GetFileInformationByHandle(handle, safe.ctypes.byref(info)):
            raise safe.ctypes.WinError(safe.ctypes.get_last_error())
        _require(not info.attributes & (safe._REPARSE | 0x10) and info.links == 1, "FILES_LOCK_UNSAFE")
        descriptor = safe.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
    except BaseException:
        safe._kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


class _ExistingLock:
    def __init__(self, directory, *, exclusive, timeout, check, lock_name="authority.lock"):
        _require(type(exclusive) is bool and type(timeout) in (int, float)
                 and math.isfinite(timeout) and 0 <= timeout <= 30, "FILES_LOCK_OPTIONS_INVALID")
        _require(type(lock_name) is str and lock_name in ("authority.lock", "handoff.lock"),
                 "FILES_LOCK_OPTIONS_INVALID")
        self.lock_name = lock_name
        self.directory, self.exclusive, self.timeout, self.check = directory, exclusive, timeout, check
        self.stream = None
        self.locked = False
        self.owner = (os.getpid(), threading.get_ident())

    def verify(self, *, cancel=True, content=True):
        _require(self.stream is not None and self.owner == (os.getpid(), threading.get_ident()),
                 "FILES_LOCK_OWNER_INVALID")
        if cancel:
            self.check()
        self.directory.verify()
        files.require_private(self.directory.path / self.lock_name, directory=False)
        _require(files.identity(self.directory.entry_stat(self.lock_name)) == self.path_identity
                 and files.identity(os.fstat(self.stream.fileno())) == self.handle_identity, "FILES_LOCK_CHANGED")
        if content:
            self.stream.seek(0)
            _require(self.stream.read(2) == _LOCK_BYTES, "FILES_LOCK_CHANGED")
        self.directory.verify()

    def _attempt(self):
        if os.name != "nt":
            import fcntl
            try:
                fcntl.flock(self.stream.fileno(), (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                return False
        flags = 1 | (2 if self.exclusive else 0)  # FAIL_IMMEDIATELY, EXCLUSIVE_LOCK
        result = _lock_kernel.LockFileEx(safe.ctypes.c_void_p(safe.msvcrt.get_osfhandle(self.stream.fileno())),
            safe.ctypes.c_uint32(flags), safe.ctypes.c_uint32(0), safe.ctypes.c_uint32(1),
            safe.ctypes.c_uint32(0), safe.ctypes.byref(self.overlap))
        if result:
            return True
        error = safe.ctypes.get_last_error()
        if error == 33:
            return False
        raise safe.ctypes.WinError(error)

    def __enter__(self):
        self.check()
        self.directory.verify()
        files.require_private(self.directory.path / self.lock_name, directory=False)
        self.path_identity = files.identity(self.directory.entry_stat(self.lock_name))
        _require(self.path_identity["byte_size"] == 1, "FILES_LOCK_CHANGED")
        self.stream = (_open_lock(self.directory) if self.lock_name == "authority.lock"
                       else _open_lock(self.directory, lock_name=self.lock_name))
        try:
            self.handle_identity = files.identity(os.fstat(self.stream.fileno()))
            _require(all(self.path_identity[key] == self.handle_identity[key]
                         for key in _FILE_FIELDS - {"ctime_ns"}), "FILES_LOCK_CHANGED")
            if os.name == "nt":
                self.overlap = _LockOverlapped()
            deadline = time.monotonic() + self.timeout
            while True:
                self.check()
                # Windows byte-range locks also deny reads by a contender.
                # Path/fd metadata is checked while waiting; lock bytes are
                # inspected only after actually acquiring our SH/EX range.
                self.verify(content=False)
                if self._attempt():
                    self.locked = True
                    break
                remaining = deadline - time.monotonic()
                _require(remaining > 0, "FILES_LOCK_BUSY")
                time.sleep(min(0.025, remaining))
            self.verify()
            return self
        except BaseException as primary:
            try:
                self.__exit__(type(primary), primary,
                    BaseException.__dict__["__traceback__"].__get__(primary))
            except BaseException:
                try:
                    BaseException.add_note(primary, "FILES_LOCK_CLEANUP_FAILED")
                except BaseException:
                    pass
            raise

    def __exit__(self, exc_type, exc, tb):
        first, raise_first = exc, False
        cleanup_note = close_note = False
        try:
            if exc_type is None:
                self.verify()
        except BaseException as error:
            first, raise_first = error, True
        finally:
            try:
                if self.locked:
                    if os.name == "nt":
                        result = _lock_kernel.UnlockFileEx(
                            safe.ctypes.c_void_p(safe.msvcrt.get_osfhandle(self.stream.fileno())),
                            safe.ctypes.c_uint32(0), safe.ctypes.c_uint32(1), safe.ctypes.c_uint32(0),
                            safe.ctypes.byref(self.overlap))
                        if not result:
                            raise safe.ctypes.WinError(safe.ctypes.get_last_error())
                    else:
                        import fcntl
                        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            except BaseException as error:
                if first is None:
                    first, raise_first = error, True
                else:
                    cleanup_note = True
            finally:
                self.locked = False
                stream, self.stream = self.stream, None
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        if first is None:
                            first, raise_first = error, True
                        else:
                            close_note = True
        # Optional annotation happens only after the owned close attempt.
        if cleanup_note:
            try:
                BaseException.add_note(first, "FILES_LOCK_CLEANUP_FAILED")
            except BaseException:
                pass
        if close_note:
            try:
                BaseException.add_note(first, "FILES_LOCK_CLOSE_FAILED")
            except BaseException:
                pass
        if raise_first:
            raise first


def _check(value):
    _require(value is None or callable(value), "FILES_CANCEL_INVALID")
    if value is None:
        return lambda: None
    # Preserve the actual supplied callable even when its truth value is false.
    # A normal function wrapper also prevents the reused FileHold's historical
    # `check_cancelled or default` from swallowing a false-valued callable.
    def preserved_callback():
        return value()
    return preserved_callback


def _timeout(value):
    _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 30,
             "FILES_LOCK_OPTIONS_INVALID")
    return value


def _publish(directory, name, raw, limits, *, replace=False, check):
    _require(type(raw) is bytes and 0 < len(raw) <= limits.max_metadata_bytes, "FILES_METADATA_LIMIT")
    check()
    directory.verify()
    result = files.publish(directory, name, raw, replace=replace)
    directory.verify()
    check()
    return result


def initialize(root, *, init_operation_id, declared_binding_raw, limits=DEFAULT_LIMITS,
               timeout=30, check_cancelled=None):
    """Explicit fresh init. Before first durable reservation: retained_unproven."""
    limits, root, operation, check = _limits(limits), _root(root), _operation(init_operation_id), _check(check_cancelled)
    timeout = _timeout(timeout)
    _binding(declared_binding_raw, limits)
    with _filesystem(), ExitStack() as stack:
        check()
        parent = stack.enter_context(safe._Directory(root.parent, base=root.parent))
        try:
            parent.entry_stat(root.name)
        except FileNotFoundError:
            pass
        else:
            raise FilePrimitiveError("FILES_INIT_ROOT_EXISTS")
        # Exclusive creation establishes freshness. Never call initialize ACL on
        # an existing root, including an empty pre-reservation leftover.
        if os.name == "nt":
            os.mkdir(safe._native_path(root))
        else:
            os.mkdir(root.name, mode=0o700, dir_fd=parent.fd)
            os.fsync(parent.fd)
        directory = stack.enter_context(safe._Directory(root, base=root, parent=parent))
        if os.name == "nt":
            files._windows_acl(root, directory=True, initialize=True)
        files.require_private(root, directory=True)
        directory.verify()
        reservation_raw = _canonical({"format_version": 1, "init_operation_id": operation,
            "binding_sha256": hashlib.sha256(declared_binding_raw).hexdigest(),
            "root_identity": files.directory_identity(directory)})
        _publish(directory, "init-reservation.json", reservation_raw, limits, check=check)
    return resume_initialize(root, init_operation_id=operation, declared_binding_raw=declared_binding_raw,
                             limits=limits, timeout=timeout, check_cancelled=check)


def resume_initialize(root, *, init_operation_id, declared_binding_raw, limits=DEFAULT_LIMITS,
                      timeout=30, check_cancelled=None):
    """Complete only a durable original reservation; never adopt unknown temps."""
    limits, root, operation, check = _limits(limits), _root(root), _operation(init_operation_id), _check(check_cancelled)
    timeout = _timeout(timeout)
    _binding(declared_binding_raw, limits)
    with _filesystem(), ExitStack() as stack:
        directory = stack.enter_context(safe._Directory(root, base=root))
        files.require_private(root, directory=True)
        names = _scan(directory, maximum=len(_ROOT_NAMES), check=check, root=True)
        _require("init-reservation.json" in names, "FILES_RESERVATION_UNPROVEN")
        reserved = _hold(stack, root / "init-reservation.json", limits.max_metadata_bytes, check)
        reservation = _reservation(reserved.raw, limits)
        _require(reservation["init_operation_id"] == operation
                 and reservation["binding_sha256"] == hashlib.sha256(declared_binding_raw).hexdigest(),
                 "FILES_RESUME_MISMATCH")
        _require(reservation["root_identity"] == files.directory_identity(directory), "FILES_ROOT_CHANGED")
        if "files-seal.json" in names:
            # A seal makes missing/corrupt objects immutable damage, never work
            # that the initialization resumer is permitted to fabricate.
            with guard(root, exclusive=True, expected_declared_binding_sha256=reservation["binding_sha256"],
                       limits=limits, timeout=timeout, check_cancelled=check) as held:
                _require(held.reservation["init_operation_id"] == operation, "FILES_RESUME_MISMATCH")
                return FileGuard.summary(held)
        _require("head.json" not in names, "FILES_RESERVED_LAYOUT_INVALID")
        if "declared-binding.json" not in names:
            _publish(directory, "declared-binding.json", declared_binding_raw, limits, check=check)
        binding = _hold(stack, root / "declared-binding.json", limits.max_metadata_bytes, check)
        _require(binding.raw == declared_binding_raw, "FILES_RESUME_MISMATCH")
        if "authority.lock" not in names:
            _publish(directory, "authority.lock", _LOCK_BYTES, limits, check=check)
        lock = stack.enter_context(_ExistingLock(directory, exclusive=True, timeout=timeout, check=check))
        # Recheck after acquisition: multiple original-UUID resumers may have
        # waited on this same existing OS lock.
        names = _scan(directory, maximum=len(_ROOT_NAMES), check=check, root=True)
        reserved.verify()
        binding.verify()
        if "files-seal.json" in names:
            current = _hold(stack, root / "files-seal.json", limits.max_metadata_bytes, check)
            seal = _seal(current.raw, limits)
            _verify_seal(seal, reservation, reserved.sha256, binding.sha256, directory, lock,
                         root / "facts", stack)
            return {"state": "ready", "filesystem_only": True, "init_operation_id": operation}
        if "facts" not in names:
            check()
            files.mkdir_child(directory, "facts")
        facts = stack.enter_context(safe._Directory(root / "facts", base=root / "facts", parent=directory))
        files.require_private(facts.path, directory=True)
        _require(not _scan(facts, maximum=limits.max_facts, check=check), "FILES_RESERVED_FACTS_NOT_EMPTY")
        seal_raw = _canonical({"format_version": 1, "init_operation_id": operation,
            "reservation_sha256": reserved.sha256, "binding_sha256": binding.sha256,
            "root_identity": reservation["root_identity"], "facts_identity": files.directory_identity(facts),
            "lock_path_identity": lock.path_identity, "lock_handle_identity": lock.handle_identity})
        _publish(directory, "files-seal.json", seal_raw, limits, check=check)
        reserved.verify()
        binding.verify()
        lock.verify()
        return {"state": "ready", "filesystem_only": True, "init_operation_id": operation}


def _verify_seal(seal, reservation, reservation_sha, binding_sha, directory, lock, facts_path, stack):
    _require(seal["init_operation_id"] == reservation["init_operation_id"]
             and seal["reservation_sha256"] == reservation_sha
             and seal["binding_sha256"] == binding_sha == reservation["binding_sha256"]
             and seal["root_identity"] == reservation["root_identity"] == files.directory_identity(directory),
             "FILES_SEAL_BINDING_INVALID")
    _require(seal["lock_path_identity"] == lock.path_identity
             and seal["lock_handle_identity"] == lock.handle_identity, "FILES_LOCK_CHANGED")
    facts = stack.enter_context(safe._Directory(facts_path, base=facts_path, parent=directory))
    files.require_private(facts.path, directory=True)
    _require(seal["facts_identity"] == files.directory_identity(facts), "FILES_FACTS_CHANGED")
    return facts


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    """Owned original bytes; this snapshot is not a capability or a live fd."""
    fact_id: str
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _DirectoryObservation:
    path: Path


@dataclass(frozen=True, slots=True)
class _LockObservation:
    exclusive: bool


@dataclass(frozen=True, slots=True)
class _MetadataObservation:
    path: Path
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _GuardAcquisition:
    original: object
    owner: tuple
    exclusive: bool
    directory: object
    facts: object
    lock: object
    lock_stream: object
    lock_fd: int
    root_handles: tuple
    facts_handles: tuple
    root_identity: tuple
    facts_identity: tuple
    lock_path_identity: tuple
    lock_handle_identity: tuple
    metadata: tuple
    metadata_observations: tuple
    reservation_raw: bytes
    seal_raw: bytes
    limits: Limits
    check: object


def _identity_tuple(value):
    return tuple(sorted(value.items()))


def _acquisition(held):
    _require(type(held) is FileGuard, "FILES_GUARD_INVALID")
    state = _LIVE_GUARDS.get(id(held))
    _require(state is not None and state.original is held
             and state.owner == (os.getpid(), threading.get_ident()), "FILES_GUARD_INVALID")
    return state


def _verify_guard(held, *, writing=False):
    state = _acquisition(held)
    _require(not writing or state.exclusive, "FILES_EXCLUSIVE_REQUIRED")
    state.check()
    _require(state.lock.locked and state.lock.stream is state.lock_stream
             and not state.lock_stream.closed and state.lock_stream.fileno() == state.lock_fd
             and state.lock.owner == state.owner
             and _identity_tuple(state.lock.path_identity) == state.lock_path_identity
             and _identity_tuple(state.lock.handle_identity) == state.lock_handle_identity
             and tuple(state.directory.handles) == state.root_handles
             and tuple(state.facts.handles) == state.facts_handles, "FILES_GUARD_INVALID")
    state.lock.verify()
    state.directory.verify()
    state.facts.verify()
    _require(_identity_tuple(files.directory_identity(state.directory)) == state.root_identity
             and _identity_tuple(files.directory_identity(state.facts)) == state.facts_identity, "FILES_ROOT_CHANGED")
    files.require_private(state.directory.path, directory=True)
    files.require_private(state.facts.path, directory=True)
    for item, observed in zip(state.metadata, state.metadata_observations, strict=True):
        _require(item.path == observed.path and item.raw == observed.raw and item.sha256 == observed.sha256,
                 "FILES_GUARD_INVALID")
        files.require_private(item.path, directory=False)
        item.verify()
    names = _scan(state.directory, maximum=len(_ROOT_NAMES), check=state.check, root=True)
    _require(_REQUIRED <= names, "FILES_REQUIRED_ENTRY_MISSING")
    _scan(state.facts, maximum=state.limits.max_facts, check=state.check, fact_limits=state.limits)
    return state


class FileGuard:
    """Public observations never own mutable acquisition or resource references.

    Ordinary instance attributes (including legacy _exclusive/_active names)
    have no effect on registry-owned OS facts. Copies are never registered.
    Filesystem APIs call _verify_guard directly, so shadowing verify() cannot
    bypass the exact original-object, original owner and real mode checks.
    """
    def __init__(self, token):
        _require(token is _TOKEN, "FILES_GUARD_INVALID")

    @property
    def directory(self):
        return _DirectoryObservation(Path(os.fspath(_acquisition(self).directory.path)))

    @property
    def facts(self):
        return _DirectoryObservation(Path(os.fspath(_acquisition(self).facts.path)))

    @property
    def lock(self):
        return _LockObservation(_acquisition(self).exclusive)

    @property
    def metadata(self):
        return tuple(_MetadataObservation(Path(os.fspath(item.path)), item.raw, item.sha256)
                     for item in _acquisition(self).metadata_observations)

    @property
    def reservation(self):
        return json.loads(_acquisition(self).reservation_raw.decode("utf-8"))

    @property
    def seal(self):
        return json.loads(_acquisition(self).seal_raw.decode("utf-8"))

    @property
    def limits(self):
        return _limits(_acquisition(self).limits)

    @property
    def check(self):
        return _acquisition(self).check

    def verify(self, *, writing=False):
        _verify_guard(self, writing=writing)

    def summary(self):
        state = _verify_guard(self)
        reservation, seal = _reservation(state.reservation_raw, state.limits), _seal(state.seal_raw, state.limits)
        return {"state": "ready", "filesystem_only": True,
                "init_operation_id": reservation["init_operation_id"],
                "declared_binding_sha256": seal["binding_sha256"],
                "windows_directory_fsync_claimed": False}


@contextmanager
def guard(root, *, exclusive=False, expected_declared_binding_sha256=None, limits=DEFAULT_LIMITS,
          timeout=30, check_cancelled=None):
    """Existing-only true OS lock, granting filesystem operations only."""
    limits, root, check = _limits(limits), _root(root), _check(check_cancelled)
    timeout = _timeout(timeout)
    if expected_declared_binding_sha256 is not None:
        _sha(expected_declared_binding_sha256)
    held = None
    with _filesystem(), ExitStack() as stack:
        directory = stack.enter_context(safe._Directory(root, base=root))
        files.require_private(root, directory=True)
        lock = stack.enter_context(_ExistingLock(directory, exclusive=exclusive, timeout=timeout, check=check))
        names = _scan(directory, maximum=len(_ROOT_NAMES), check=check, root=True)
        _require(_REQUIRED <= names, "FILES_REQUIRED_ENTRY_MISSING")
        original = [_hold(stack, root / name, limits.max_metadata_bytes, check) for name in
                    ("init-reservation.json", "declared-binding.json", "files-seal.json")]
        reserved, binding, sealed = original
        reservation, declaration, seal = _reservation(reserved.raw, limits), _binding(binding.raw, limits), _seal(sealed.raw, limits)
        _require(expected_declared_binding_sha256 is None or binding.sha256 == expected_declared_binding_sha256,
                 "FILES_DECLARED_BINDING_MISMATCH")
        facts = _verify_seal(seal, reservation, reserved.sha256, binding.sha256, directory, lock, root / "facts", stack)
        _scan(facts, maximum=limits.max_facts, check=check, fact_limits=limits)
        if "head.json" in names:
            # Derived head is inspected with a transient owner so its handle
            # cannot obstruct an explicit head replacement on Windows.
            with ExitStack() as transient:
                head = _hold(transient, root / "head.json", limits.max_metadata_bytes, check)
                _head(head.raw, limits)
        held = FileGuard(_TOKEN)
        _LIVE_GUARDS[id(held)] = _GuardAcquisition(held, (os.getpid(), threading.get_ident()), exclusive,
            directory, facts, lock, lock.stream, lock.stream.fileno(), tuple(directory.handles), tuple(facts.handles),
            _identity_tuple(files.directory_identity(directory)), _identity_tuple(files.directory_identity(facts)),
            _identity_tuple(lock.path_identity), _identity_tuple(lock.handle_identity), tuple(original),
            tuple(_MetadataObservation(item.path, item.raw, item.sha256) for item in original),
            reserved.raw, sealed.raw, _limits(limits), check)
        try:
            _verify_guard(held)
            yield held
            _verify_guard(held)
        finally:
            if held is not None:
                _LIVE_GUARDS.pop(id(held), None)


def _fact_id(value):
    _require(type(value) is str and _FACT_ID.fullmatch(value), "FILES_FACT_ID_INVALID")
    return value


def _facts_snapshot(held, stack):
    state = _verify_guard(held)
    names = _scan(state.facts, maximum=state.limits.max_facts, check=state.check, fact_limits=state.limits)
    items, total = [], 0
    for name in sorted(names):
        item = _hold(stack, state.facts.path / name, state.limits.max_fact_bytes, state.check)
        total += len(item.raw)
        _require(total <= state.limits.max_total_bytes, "FILES_TOTAL_BYTES_LIMIT")
        items.append((name[:-5], item.sha256, len(item.raw)))
    _verify_guard(held)
    return items, total


def read_fact(held, fact_id):
    _require(type(held) is FileGuard, "FILES_GUARD_INVALID")
    fact_id = _fact_id(fact_id)
    with _filesystem(), ExitStack() as stack:
        state = _verify_guard(held)
        item = _hold(stack, state.facts.path / (fact_id + ".json"), state.limits.max_fact_bytes, state.check)
        result = FactSnapshot(fact_id, item.raw, item.sha256)
        _verify_guard(held)
        return result


def put_immutable(held, fact_id, raw):
    """Opaque original bytes. Same slot/bytes is a read-only retry, no rewrite."""
    _require(type(held) is FileGuard, "FILES_GUARD_INVALID")
    fact_id = _fact_id(fact_id)
    with _filesystem(), ExitStack() as stack:
        state = _verify_guard(held, writing=True)
        _require(type(raw) is bytes and 0 < len(raw) <= state.limits.max_fact_bytes, "FILES_FACT_BYTES_LIMIT")
        items, total = _facts_snapshot(held, stack)
        existing = next((item for item in items if item[0] == fact_id), None)
        if existing is not None:
            current = _hold(stack, state.facts.path / (fact_id + ".json"), state.limits.max_fact_bytes, state.check)
            _require(current.raw == raw, "FILES_IMMUTABLE_CONFLICT")
            _verify_guard(held, writing=True)
            return current.sha256
        _require(len(items) < state.limits.max_facts, "FILES_FACT_COUNT_LIMIT")
        _require(total + len(raw) <= state.limits.max_total_bytes, "FILES_TOTAL_BYTES_LIMIT")
        state.check()
        digest = files.publish(state.facts, fact_id + ".json", raw, replace=False)
        _verify_guard(held, writing=True)
        return digest


def derive_head(held):
    _require(type(held) is FileGuard, "FILES_GUARD_INVALID")
    with _filesystem(), ExitStack() as stack:
        state = _verify_guard(held, writing=True)
        items, total = _facts_snapshot(held, stack)
        sealed = next(item for item in state.metadata if item.path.name == "files-seal.json")
        raw = _canonical({"format_version": 1, "files_seal_sha256": sealed.sha256,
            "fact_count": len(items), "total_bytes": total, "facts_sha256": hashlib.sha256(_canonical(items)).hexdigest()})
        digest = _publish(state.directory, "head.json", raw, state.limits, replace=True, check=state.check)
        _verify_guard(held, writing=True)
        return digest


def status(root, *, limits=DEFAULT_LIMITS, timeout=0, check_cancelled=None,
           expected_declared_binding_sha256=None, expected_node_id=None,
           expected_profile_sha256=None):
    """No creation, repair, body output or permission inferred from declarations.

    Optional server pins are checked against owned reservation/binding bytes.
    A reserved prefix without binding still cannot prove a node/profile match.
    """
    limits, root, check = _limits(limits), _root(root), _check(check_cancelled)
    timeout = _timeout(timeout)
    if expected_declared_binding_sha256 is not None:
        _sha(expected_declared_binding_sha256)
    if expected_node_id is not None:
        _operation(expected_node_id)
    if expected_profile_sha256 is not None:
        _sha(expected_profile_sha256)
    try:
        with _filesystem(), ExitStack() as stack:
            check()
            try:
                root.stat(follow_symlinks=False)
            except FileNotFoundError:
                return {"state": "absent", "filesystem_only": True}
            directory = stack.enter_context(safe._Directory(root, base=root))
            files.require_private(root, directory=True)
            try:
                directory.entry_stat("init-reservation.json")
            except FileNotFoundError:
                return {"state": "retained_unproven", "filesystem_only": True}
            names = _scan(directory, maximum=len(_ROOT_NAMES), check=check, root=True)
            reserved = _hold(stack, root / "init-reservation.json", limits.max_metadata_bytes, check)
            reservation = _reservation(reserved.raw, limits)
            _require(reservation["root_identity"] == files.directory_identity(directory), "FILES_ROOT_CHANGED")
            _require(expected_declared_binding_sha256 is None
                     or reservation["binding_sha256"] == expected_declared_binding_sha256,
                     "FILES_DECLARED_BINDING_MISMATCH")
            if "files-seal.json" not in names:
                _require("head.json" not in names, "FILES_RESERVED_LAYOUT_INVALID")
                if "declared-binding.json" in names:
                    binding = _hold(stack, root / "declared-binding.json", limits.max_metadata_bytes, check)
                    declaration = _binding(binding.raw, limits)
                    _require(binding.sha256 == reservation["binding_sha256"], "FILES_RESUME_MISMATCH")
                    _require(expected_node_id is None or declaration["node_id"] == expected_node_id,
                             "FILES_NODE_MISMATCH")
                    _require(expected_profile_sha256 is None
                             or declaration["profile_sha256"] == expected_profile_sha256,
                             "FILES_PROFILE_MISMATCH")
                if "authority.lock" in names:
                    with _ExistingLock(directory, exclusive=False, timeout=timeout, check=check):
                        pass
                if "facts" in names:
                    facts = stack.enter_context(safe._Directory(root / "facts", base=root / "facts", parent=directory))
                    files.require_private(facts.path, directory=True)
                    _require(not _scan(facts, maximum=limits.max_facts, check=check), "FILES_RESERVED_FACTS_NOT_EMPTY")
                reserved.verify()
                return {"state": "reserved", "filesystem_only": True,
                        "init_operation_id": reservation["init_operation_id"]}
        with guard(root, exclusive=False, limits=limits, timeout=timeout, check_cancelled=check,
                   expected_declared_binding_sha256=expected_declared_binding_sha256) as held:
            with ExitStack() as stack:
                if expected_node_id is not None or expected_profile_sha256 is not None:
                    binding = next(item for item in _acquisition(held).metadata
                                   if item.path.name == "declared-binding.json")
                    declaration = _binding(binding.raw, limits)
                    _require(expected_node_id is None or declaration["node_id"] == expected_node_id,
                             "FILES_NODE_MISMATCH")
                    _require(expected_profile_sha256 is None
                             or declaration["profile_sha256"] == expected_profile_sha256,
                             "FILES_PROFILE_MISMATCH")
                items, total = _facts_snapshot(held, stack)
                state = _acquisition(held)
                result = FileGuard.summary(held)
                result.update(fact_count=len(items), total_bytes=total, head_state="missing")
                if "head.json" in _scan(state.directory, maximum=len(_ROOT_NAMES), check=check, root=True):
                    head = _hold(stack, root / "head.json", limits.max_metadata_bytes, check)
                    value = _head(head.raw, limits)
                    sealed = next(item for item in state.metadata if item.path.name == "files-seal.json")
                    result["head_state"] = "matches" if value == {"format_version": 1,
                        "files_seal_sha256": sealed.sha256, "fact_count": len(items), "total_bytes": total,
                        "facts_sha256": hashlib.sha256(_canonical(items)).hexdigest()} else "stale"
                return result
    except FilePrimitiveError as error:
        return {"state": "unavailable", "filesystem_only": True, "code": error.code}
