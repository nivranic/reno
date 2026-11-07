"""Shared file lifetimes and exclusive collection across physical storage roots.

Initialize facilities explicitly, before serving requests. Ordinary acquisition
never creates a directory or lock file. Lock files are private, marked, pinned
and never removed. All cooperating processes use the same canonical root order.
No database is opened here; acquire before BEGIN and only require_guard inside
an existing transaction. Same-account actors ignoring locks are not a sandbox.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
import errno
import io
from itertools import islice
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
from types import BuiltinMethodType, MethodType
import uuid

from . import sync_media as safe
from .sync_protocol import SyncError

LOCK_NAME = ".reno-storage-lifecycle-v1.lock"
MAX_PROFILES = 256
_MARKER = b"reno-storage-lifecycle-lock-v1\n"
_CURRENT: ContextVar[StorageGuard | None] = ContextVar("reno_storage_lifecycle", default=None)


class StorageLifecycleError(ValueError):
    def __init__(self, code, status=409):
        super().__init__("Storage lifecycle protection could not be established safely")
        self.code, self.status = code, status


def _fail(code, status=409):
    raise StorageLifecycleError(code, status)


def _path(value):
    try:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
            raise ValueError
        return Path(os.path.abspath(os.fspath(path)))
    except (ValueError, TypeError, OSError):
        _fail("STORAGE_INVALID_PROFILE", 400)


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


@dataclass(frozen=True)
class StorageProfile:
    database: Path
    media: Path
    reports: Path
    checkpoints: Path

    def __post_init__(self):
        for field in ("database", "media", "reports", "checkpoints"):
            object.__setattr__(self, field, _path(getattr(self, field)))

    @property
    def roots(self):
        paths = (self.database.parent, self.media, self.reports, self.checkpoints)
        distinct = {_key(path): path for path in paths}
        return tuple(distinct[key] for key in sorted(distinct))

    @property
    def identity(self):
        return tuple(_key(getattr(self, field)) for field in ("database", "media", "reports", "checkpoints"))


def _profile(value):
    if not isinstance(value, StorageProfile):
        _fail("STORAGE_INVALID_PROFILE", 400)
    return value


def _profiles(values):
    """Bound the input before deduplicating exact profiles; preserve primary."""
    try:
        submitted = tuple(islice(iter(values), MAX_PROFILES + 1))
    except TypeError:
        _fail("STORAGE_INVALID_PROFILES", 400)
    if not submitted:
        _fail("STORAGE_INVALID_PROFILES", 400)
    if len(submitted) > MAX_PROFILES:
        _fail("STORAGE_TOO_MANY_PROFILES", 400)
    distinct = {}
    for value in submitted:
        value = _profile(value)
        distinct.setdefault(value.identity, value)
    return tuple(distinct.values())


def _roots(profiles):
    """Every physical root is locked once, independent of profile order."""
    distinct = {}
    for profile in profiles:
        for root in profile.roots:
            distinct.setdefault(_key(root), root)
    return tuple(distinct[key] for key in sorted(distinct))


@contextmanager
def _filesystem():
    try:
        yield
    except StorageLifecycleError:
        raise
    except FileNotFoundError:
        _fail("STORAGE_NOT_INITIALIZED", 503)
    except SyncError:
        _fail("STORAGE_UNSAFE_PATH", 503)
    except OSError:
        _fail("STORAGE_IO_ERROR", 503)


if os.name == "nt":
    ctypes, wintypes = safe.ctypes, safe.wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    _kernel = safe._kernel
    _kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.LockFileEx.restype = wintypes.BOOL
    _kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.UnlockFileEx.restype = wintypes.BOOL


def _open_lock(directory):
    if os.name != "nt":
        return directory.open_file(LOCK_NAME)
    # OPEN_EXISTING, shared READ|WRITE, never FILE_SHARE_DELETE. The byte lock
    # provides true shared/exclusive semantics without creating a missing file.
    handle = _kernel.CreateFileW(safe._native_path(directory.path / LOCK_NAME),
        0x80000000, 3, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = safe._HandleInfo()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (safe._REPARSE | 0x10) or info.links != 1:
            _fail("STORAGE_UNSAFE_PATH", 503)
        fd = safe.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
    except BaseException:
        _kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


@dataclass(frozen=True, slots=True)
class OwnedLockCloseReceiptV1:
    format_version: int
    kind: str
    scope: str
    phase: str
    constructed_returned: bool
    failed: bool
    normal_exit: bool
    root_ordinal: int
    directory_borrowed: bool
    stream_borrowed: bool
    acquire_expression_started: bool
    acquire_native_attempted: bool
    acquire_returned: bool
    acquire_return_legal: bool
    acquire_attempts: int
    acquire_state: str
    unlock_owed: bool
    unlock_consumed: bool
    unlock_native_attempted: bool
    unlock_returned: bool
    unlock_return_legal: bool
    unlock_state: str
    file_identity: tuple[int, int] | None


_OWNED_LOCK_SEAL = object()


class _OwnedLockJournal:
    __slots__ = ("_seal", "_binding", "_token", "_root_ordinal", "_stream_owner",
        "_directory_owner", "_stream", "_owner", "_phase", "_first", "_first_tb",
        "_failed", "_normal_exit", "_constructed_returned", "_acquire_expression_started",
        "_acquire_native_attempted", "_acquire_returned", "_acquire_return_legal",
        "_acquire_attempts", "_acquire_state", "_unlock_owed", "_unlock_consumed",
        "_unlock_native_attempted", "_unlock_returned", "_unlock_return_legal",
        "_unlock_state", "_file_identity")

    def __init__(self, *args, **kwargs):
        _owned_lock_fail("INVALID_ARGUMENT")


def _owned_lock_module():
    from . import personal_authority_owned_ex_deadline as owned
    return owned


def _owned_lock_fail(suffix):
    _owned_lock_module()._fail(suffix)


def _owned_lock_clock(token):
    _owned_lock_module()._clock(token)


def _owned_lock_get_osfhandle(fd):
    return safe.msvcrt.get_osfhandle(fd)


def _owned_lock_lock_file(handle, flags, address):
    return _kernel.LockFileEx(handle, flags, 0, 1, 0, address)


def _owned_lock_unlock_file(handle, address):
    return _kernel.UnlockFileEx(handle, 0, 1, 0, address)


def _owned_lock_handle(value):
    if type(value) is not int or not 0 < value < ctypes.c_void_p(-1).value:
        _owned_lock_fail("INVALID_RESULT")
    return value


def _owned_lock_bool(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        _owned_lock_fail("INVALID_RESULT")
    if value == 0:
        raise ctypes.WinError(ctypes.get_last_error())


def _owned_lock_capture(journal, failure, *, traceback=None, cleanup=False):
    journal._failed = True
    if journal._first is None:
        journal._first = failure
        journal._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                             if traceback is None else traceback)
    elif cleanup and failure is not journal._first:
        try:
            BaseException.add_note(journal._first, "OWNED_EX_LOCK_CLEANUP_FAILED")
        except BaseException:
            pass


def _owned_lock_raise(journal):
    if journal._first is not None:
        raise BaseException.with_traceback(journal._first, journal._first_tb) from None


def _new_owned_lock_journal(binding, *, root_ordinal, stream_owner, directory_owner):
    owned = _owned_lock_module()
    if os.name != "nt":
        owned._fail("UNSUPPORTED")
    binding = owned._binding(binding)
    if (type(root_ordinal) is not int or not 0 <= root_ordinal < 4
            or type(stream_owner) is not owned.LockStreamDeadlineLease
            or type(directory_owner) is not safe._Directory):
        owned._fail("INVALID_ARGUMENT")
    # Read only actual exact owners' own cached references, never receipt DTOs.
    if (stream_owner._binding is not binding or stream_owner._root_ordinal != root_ordinal
            or stream_owner._directory is not directory_owner
            or stream_owner._token is not binding._effective_token
            or binding._owners[root_ordinal] is not stream_owner
            or stream_owner._phase != "active" or stream_owner._enter_returned is not True
            or type(stream_owner._buffer[0]) is not io.BufferedReader
            or type(stream_owner._raw[0]) is not io.FileIO):
        owned._fail("INVALID_ARGUMENT")
    directory_journal = object.__getattribute__(directory_owner, "__dict__").get("_owned_close_journal")
    if (type(directory_journal) is not safe._OwnedDirectoryJournal
            or directory_journal._seal is not safe._OWNED_DIRECTORY_SEAL
            or directory_journal._owner is not directory_owner
            or directory_journal._binding is not binding
            or directory_journal._root_ordinal != root_ordinal
            or directory_journal._phase != "active" or directory_journal._enter_returned is not True):
        owned._fail("INVALID_ARGUMENT")
    journal = object.__new__(_OwnedLockJournal)
    journal._seal, journal._binding, journal._token = _OWNED_LOCK_SEAL, binding, binding._effective_token
    journal._root_ordinal, journal._stream_owner, journal._directory_owner = root_ordinal, stream_owner, directory_owner
    journal._stream, journal._owner, journal._phase = stream_owner._buffer[0], None, "new"
    journal._first = journal._first_tb = journal._file_identity = None
    journal._failed = journal._normal_exit = journal._constructed_returned = False
    journal._acquire_expression_started = journal._acquire_native_attempted = False
    journal._acquire_returned = journal._acquire_return_legal = False
    journal._acquire_attempts, journal._acquire_state = 0, "not_started"
    journal._unlock_owed = journal._unlock_consumed = journal._unlock_native_attempted = False
    journal._unlock_returned = journal._unlock_return_legal = False
    journal._unlock_state = "not_owed"
    return journal


def _owned_lock_journal(value):
    if type(value) is not _OwnedLockJournal or value._seal is not _OWNED_LOCK_SEAL:
        _owned_lock_fail("INVALID_ARGUMENT")
    binding = _owned_lock_module()._binding(value._binding)
    if value._token is not binding._effective_token:
        _owned_lock_fail("INVALID_ARGUMENT")
    return value


def _owned_lock_construct(owner, directory, stream, journal):
    journal = _owned_lock_journal(journal)
    if (type(owner) is not _FileLock or os.name != "nt"
            or journal._owner is not None or journal._phase != "new"
            or directory is not journal._directory_owner or stream is not journal._stream):
        _owned_lock_fail("INVALID_ARGUMENT")
    # Claim the actual self before fstat/overlap allocation can fail.
    journal._owner, journal._phase = owner, "constructing"
    owner._owned_lock_journal = journal
    try:
        _owned_lock_clock(journal._token)
        owner.directory, owner.stream = directory, stream
        info = os.fstat(stream.fileno())
        owner.identity = (info.st_dev, info.st_ino)
        journal._file_identity = owner.identity
        owner.locked = False
        owner.overlapped = _Overlapped()
        if not _original_lock_dispatch((owner,)):
            _owned_lock_fail("INVALID_ARGUMENT")
        _owned_lock_clock(journal._token)
        journal._phase, journal._constructed_returned = "active", True
    except BaseException as failure:
        _owned_lock_capture(journal, failure)
        journal._phase = "failed_constructor"
        _owned_lock_raise(journal)


def _owned_lock_actual_handle(owner, journal):
    if (type(owner) is not _FileLock or journal._owner is not owner
            or owner.directory is not journal._directory_owner
            or owner.stream is not journal._stream
            or not _original_lock_dispatch((owner,))):
        _owned_lock_fail("INVALID_ARGUMENT")
    stream_owner = journal._stream_owner
    # Exact verification dispatch does not itself establish that manually
    # reinitialized built-in wrappers still borrow the registered sole FD.
    if (stream_owner._directory is not journal._directory_owner
            or stream_owner._buffer[0] is not owner.stream
            or stream_owner._buffer[1] != "owned" or stream_owner._raw[1] != "owned"
            or type(stream_owner._raw[0]) is not io.FileIO
            or stream_owner._fd[1] != "owned"
            or stream_owner._handle[1] != "transferred"
            or stream_owner._transfer_state != "transferred"
            or stream_owner._fd_close_owner != "numeric_owner"
            or stream_owner._phase != "active"
            or io.BufferedReader.raw.__get__(owner.stream, io.BufferedReader) is not stream_owner._raw[0]
            or io.FileIO.closefd.__get__(stream_owner._raw[0], io.FileIO) is not False):
        _owned_lock_fail("INVALID_ARGUMENT")
    fd = owner.stream.fileno()
    raw_fd = io.FileIO.fileno(stream_owner._raw[0])
    if (type(fd) is not int or fd < 0 or type(raw_fd) is not int
            or fd != raw_fd or fd != stream_owner._fd[0]):
        _owned_lock_fail("INVALID_RESULT")
    handle = _owned_lock_handle(_owned_lock_get_osfhandle(fd))
    cached_handle = stream_owner._handle[0]
    if type(cached_handle) is not int or handle != cached_handle:
        _owned_lock_fail("INVALID_RESULT")
    return handle


def _owned_lock_native_attempt(owner, journal, exclusive):
    try:
        handle = _owned_lock_actual_handle(owner, journal)
        flags = 1 | (2 if exclusive else 0)
        address = ctypes.byref(owner.overlapped)
    except OSError as failure:
        if failure.errno in (errno.EACCES, errno.EAGAIN):
            return False
        _fail("STORAGE_IO_ERROR", 503)
    # Getter/native preparation may consume the remaining budget. This strict
    # gate is outside legacy OSError mapping and before any native lock debt.
    _owned_lock_clock(journal._token)
    journal._acquire_attempts += 1
    journal._acquire_state, journal._unlock_owed = "unknown", True
    journal._unlock_state = "not_started"
    journal._acquire_native_attempted = True
    try:
        success = _owned_lock_lock_file(handle, flags, address)
        journal._acquire_returned = True
        journal._acquire_return_legal = type(success) is int and -(1 << 31) <= success < (1 << 31)
        if not journal._acquire_return_legal:
            _owned_lock_fail("INVALID_RESULT")
        if success == 0:
            # Exact native zero is known not acquired, independent of errno.
            journal._acquire_state, journal._unlock_owed, journal._unlock_state = "not_acquired", False, "not_owed"
            error = ctypes.get_last_error()
            if error == 33:
                return False
            raise ctypes.WinError(error)
        journal._acquire_state = "acquired"
        return True
    except OSError as failure:
        # Preserve the established native/getter busy mapping. New deadline
        # checks run outside this mapping boundary and retain their own first.
        if failure.errno in (errno.EACCES, errno.EAGAIN):
            if journal._acquire_native_attempted and not journal._acquire_returned:
                journal._failed = True
            return False
        _fail("STORAGE_IO_ERROR", 503)


def _owned_lock_attempt(owner, journal, exclusive):
    _owned_lock_raise(journal)
    if type(exclusive) is not bool:
        _owned_lock_fail("INVALID_ARGUMENT")
    if (journal._owner is not owner or journal._phase != "active"
            or journal._unlock_consumed
            or journal._acquire_state in ("acquired", "unknown")):
        _owned_lock_fail("CLOSED")
    journal._acquire_expression_started = True
    journal._acquire_native_attempted = journal._acquire_returned = journal._acquire_return_legal = False
    try:
        _owned_lock_clock(journal._token)
        acquired = _owned_lock_native_attempt(owner, journal, exclusive)
        _owned_lock_clock(journal._token)
        if not acquired:
            return False
        owner.locked = True
        return True
    except BaseException as failure:
        _owned_lock_capture(journal, failure)
        _owned_lock_raise(journal)


def _owned_lock_cleanup_clock(journal):
    try:
        _owned_lock_clock(journal._token)
    except BaseException as failure:
        _owned_lock_capture(journal, failure, cleanup=True)


def _remember_owned_lock_primary(owner, primary, traceback=None):
    if type(owner) is not _FileLock or not isinstance(primary, BaseException):
        _owned_lock_fail("INVALID_ARGUMENT")
    journal = object.__getattribute__(owner, "__dict__").get("_owned_lock_journal")
    if type(journal) is not _OwnedLockJournal or journal._owner is not owner:
        _owned_lock_fail("INVALID_ARGUMENT")
    _owned_lock_capture(journal, primary, traceback=traceback)


def _owned_lock_release(owner, journal):
    if journal._unlock_consumed:
        _owned_lock_raise(journal)
        return None
    # The release attempt is consumed even if a getter/clock fails below.
    journal._unlock_consumed, journal._phase = True, "retired"
    try:
        owner.locked = False
    except BaseException as failure:
        _owned_lock_capture(journal, failure, cleanup=True)
    if journal._unlock_owed:
        journal._unlock_state = "unknown"
        _owned_lock_cleanup_clock(journal)
        try:
            with _filesystem():
                handle = _owned_lock_actual_handle(owner, journal)
                address = ctypes.byref(owner.overlapped)
                journal._unlock_native_attempted = True
                success = _owned_lock_unlock_file(handle, address)
                journal._unlock_returned = True
                journal._unlock_return_legal = type(success) is int and -(1 << 31) <= success < (1 << 31)
                _owned_lock_bool(success)
                # Record physical success before the surrounding context exits.
                journal._unlock_state = "unlocked"
        except BaseException as failure:
            _owned_lock_capture(journal, failure, cleanup=True)
        else:
            _owned_lock_cleanup_clock(journal)
    _owned_lock_cleanup_clock(journal)
    journal._normal_exit = (journal._constructed_returned and not journal._failed
        and journal._first is None and journal._acquire_state == "acquired"
        and journal._unlock_state == "unlocked")
    _owned_lock_raise(journal)
    return None


def _owned_lock_receipt(owner):
    journal = object.__getattribute__(owner, "__dict__").get("_owned_lock_journal")
    if journal is None:
        return None
    # Own cached scalars/tuples only; no binding/clock/stream or native probes.
    return OwnedLockCloseReceiptV1(1, "owned_lock", "native_owner_close_facts_only",
        journal._phase, journal._constructed_returned, journal._failed or journal._first is not None,
        journal._normal_exit and journal._first is None, journal._root_ordinal, True, True,
        journal._acquire_expression_started, journal._acquire_native_attempted,
        journal._acquire_returned, journal._acquire_return_legal, journal._acquire_attempts,
        journal._acquire_state, journal._unlock_owed, journal._unlock_consumed,
        journal._unlock_native_attempted, journal._unlock_returned, journal._unlock_return_legal,
        journal._unlock_state, journal._file_identity)


class _FileLock:
    def __init__(self, directory, stream, *, _owned_lock_journal=None):
        if object.__getattribute__(self, "__dict__").get("_owned_lock_journal") is not None:
            _owned_lock_fail("CLOSED")
        if _owned_lock_journal is not None and object.__getattribute__(self, "__dict__"):
            _owned_lock_fail("CLOSED")
        if _owned_lock_journal is not None:
            _owned_lock_construct(self, directory, stream, _owned_lock_journal)
            return
        self.directory, self.stream = directory, stream
        info = os.fstat(stream.fileno())
        self.identity = (info.st_dev, info.st_ino)
        self.locked = False
        self.overlapped = _Overlapped() if os.name == "nt" else None

    @property
    def owned_lock_receipt(self):
        return _owned_lock_receipt(self)

    def verify_stat(self):
        with _filesystem():
            self.directory.verify()
            self._verify_entry_stat()

    def _verify_entry_stat(self):
        with _filesystem():
            entry = self.directory.entry_stat(LOCK_NAME)
            info = os.fstat(self.stream.fileno())
            safe._safe_stat(entry, directory=False)
            if (entry.st_nlink != 1 or info.st_nlink != 1
                    or (entry.st_dev, entry.st_ino) != self.identity
                    or (info.st_dev, info.st_ino) != self.identity
                    or entry.st_size != len(_MARKER) or info.st_size != len(_MARKER)):
                _fail("STORAGE_UNSAFE_PATH", 503)
            if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
                _fail("STORAGE_UNSAFE_PATH", 503)

    def verify(self):
        self.verify_stat()
        with _filesystem():
            os.lseek(self.stream.fileno(), 0, os.SEEK_SET)
            if os.read(self.stream.fileno(), len(_MARKER) + 1) != _MARKER:
                _fail("STORAGE_LOCK_INVALID", 503)
        self.verify_stat()

    def attempt(self, exclusive):
        journal = object.__getattribute__(self, "__dict__").get("_owned_lock_journal")
        if journal is not None:
            return _owned_lock_attempt(self, journal, exclusive)
        try:
            if os.name == "nt":
                flags = 1 | (2 if exclusive else 0)  # FAIL_IMMEDIATELY | EXCLUSIVE_LOCK
                success = _kernel.LockFileEx(safe.msvcrt.get_osfhandle(self.stream.fileno()),
                    flags, 0, 1, 0, ctypes.byref(self.overlapped))
                if not success:
                    error = ctypes.get_last_error()
                    if error == 33:  # ERROR_LOCK_VIOLATION; synchronous handle.
                        return False
                    raise ctypes.WinError(error)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            _fail("STORAGE_IO_ERROR", 503)
        self.locked = True
        return True

    def release(self):
        journal = object.__getattribute__(self, "__dict__").get("_owned_lock_journal")
        if journal is not None:
            return _owned_lock_release(self, journal)
        if not self.locked:
            return
        self.locked = False
        with _filesystem():
            if os.name == "nt":
                if not _kernel.UnlockFileEx(safe.msvcrt.get_osfhandle(self.stream.fileno()),
                    0, 1, 0, ctypes.byref(self.overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)


_FILE_LOCK_VERIFY_ORIGINAL = _FileLock.verify
_FILE_LOCK_STAT_ORIGINAL = _FileLock.verify_stat
_FILE_LOCK_ENTRY_ORIGINAL = _FileLock._verify_entry_stat
_DIRECTORY_ENTRY_ORIGINAL = safe._Directory.entry_stat
_LOCK_STREAM_TYPE = io.BufferedReader
_LOCK_STREAM_FILENO_ORIGINAL = io.BufferedReader.fileno
_LOCK_RAW_TYPE = io.FileIO
_LOCK_RAW_FILENO_ORIGINAL = io.FileIO.fileno


def _original_bound_method(instance, name, function):
    method = getattr(instance, name, None)
    return (type(method) is MethodType and method.__self__ is instance
            and method.__func__ is function)


def _original_lock_dispatch(locks):
    return all(type(lock) is _FileLock
        and _original_bound_method(lock, "verify", _FILE_LOCK_VERIFY_ORIGINAL)
        and _original_bound_method(lock, "verify_stat", _FILE_LOCK_STAT_ORIGINAL)
        and _original_bound_method(lock, "_verify_entry_stat", _FILE_LOCK_ENTRY_ORIGINAL)
        and type(lock.directory) is safe._Directory
        and _original_bound_method(lock.directory, "entry_stat", _DIRECTORY_ENTRY_ORIGINAL)
        and type(lock.stream) is _LOCK_STREAM_TYPE
        and type(lock.stream.fileno) is BuiltinMethodType
        and lock.stream.fileno == _LOCK_STREAM_FILENO_ORIGINAL.__get__(lock.stream, _LOCK_STREAM_TYPE)
        and type(lock.stream.raw) is _LOCK_RAW_TYPE
        and type(lock.stream.raw.fileno) is BuiltinMethodType
        and lock.stream.raw.fileno == _LOCK_RAW_FILENO_ORIGINAL.__get__(lock.stream.raw, _LOCK_RAW_TYPE)
        for lock in locks)


def _verify_lock_group(locks):
    """Surround all fixed marker reads with fresh full directory observations.

    Lock/path/fd/content checks remain per lock and keep their original order.
    Only repeated reads of a common ancestor within each phase are combined;
    nothing is retained across guard checks, callbacks or write operations.
    """
    original = _original_lock_dispatch(locks)
    with _filesystem():
        plan = safe._directory_verification_plan(tuple(lock.directory for lock in locks)) if original else None
    if plan is None:
        # Preserve custom verifiers' dispatch and original exception objects.
        for lock in locks:
            lock.verify()
        return
    with _filesystem():
        safe._verify_directory_plan(plan)
        for lock in locks:
            lock._verify_entry_stat()
            os.lseek(lock.stream.fileno(), 0, os.SEEK_SET)
            if os.read(lock.stream.fileno(), len(_MARKER) + 1) != _MARKER:
                _fail("STORAGE_LOCK_INVALID", 503)
            lock._verify_entry_stat()
        safe._verify_directory_plan(plan)


def _database(profile, directory):
    with _filesystem():
        try:
            info = directory.entry_stat(profile.database.name)
        except FileNotFoundError:
            return
        safe._safe_stat(info, directory=False)
        if info.st_nlink != 1:
            _fail("STORAGE_UNSAFE_PATH", 503)


def _initialization_shared(lock, *, deadline, check):
    """A peer may briefly own EX while publishing this profile's enrollment."""
    while True:
        check()
        if lock.attempt(False):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("STORAGE_BUSY")
        time.sleep(min(0.025, remaining))


def _preflight(profile, *, deadline, check):
    """Validate all existing roots/locks before the first setup mutation."""
    for path in profile.roots:
        with ExitStack() as stack:
            with _filesystem():
                try:
                    directory = stack.enter_context(safe._Directory(path, base=path))
                except FileNotFoundError:
                    continue
                if _key(path) == _key(profile.database.parent):
                    _database(profile, directory)
                try:
                    stream = stack.enter_context(_open_lock(directory))
                except FileNotFoundError:
                    continue
            lock = _FileLock(directory, stream)
            lock.verify_stat()
            _initialization_shared(lock, deadline=deadline, check=check)
            stack.callback(lock.release)
            lock.verify()


def _publish_lock(directory, temporary):
    if os.name == "nt":
        safe._rename_new(directory, temporary, directory, LOCK_NAME)
        return
    # link/unlink publication would briefly expose nlink=2, which must remain
    # invalid for a lifetime lock. Refuse unsupported atomic-rename platforms.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    function = "renameatx_np" if sys.platform == "darwin" else "renameat2"
    rename = getattr(libc, function, None)
    if rename is None:
        _fail("STORAGE_IO_ERROR", 503)
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory.fd, os.fsencode(temporary), directory.fd, os.fsencode(LOCK_NAME),
              4 if sys.platform == "darwin" else 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _initialize_lock(directory):
    try:
        directory.entry_stat(LOCK_NAME)
        return False
    except FileNotFoundError:
        pass
    temporary = ".reno-storage-initialize-" + uuid.uuid4().hex + ".tmp"
    owned = None
    try:
        with directory.open_file(temporary, write=True, create=True) as stream:
            info = os.fstat(stream.fileno())
            owned = info.st_dev, info.st_ino
            if stream.write(_MARKER) != len(_MARKER):
                _fail("STORAGE_IO_ERROR", 503)
            stream.flush()
            os.fsync(stream.fileno())
        directory.verify()
        entry = directory.entry_stat(temporary)
        safe._safe_stat(entry, directory=False)
        if (entry.st_dev, entry.st_ino) != owned or entry.st_nlink != 1:
            _fail("STORAGE_UNSAFE_PATH", 503)
        try:
            _publish_lock(directory, temporary)
        except FileExistsError:
            return False
        if os.name != "nt":
            os.fsync(directory.fd)
        return True
    finally:
        # Only this invocation's unpublished temporary is removable; crash
        # remnants and unknown files are never adopted or deleted on startup.
        if owned is not None:
            try:
                directory.verify()
                entry = directory.entry_stat(temporary)
                if (entry.st_dev, entry.st_ino) == owned and entry.st_nlink == 1 and stat.S_ISREG(entry.st_mode):
                    directory.unlink(temporary)
            except FileNotFoundError:
                pass


def initialize(profile, *, timeout=30, check_cancelled=None):
    """Explicit setup only; preserves existing contents and never creates a DB."""
    profile = _profile(profile)
    if _CURRENT.get() is not None:
        _fail("STORAGE_INITIALIZE_WHILE_GUARDED")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 3600:
        _fail("STORAGE_INVALID_TIMEOUT", 400)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("STORAGE_INVALID_ARGUMENT", 400)
    check = check_cancelled or (lambda: None)
    check()
    deadline = time.monotonic() + timeout
    _preflight(profile, deadline=deadline, check=check)
    created = 0
    for path in profile.roots:
        check()
        with ExitStack() as stack:
            with _filesystem():
                directory = stack.enter_context(safe._Directory(path, create=True, base=path))
                if _key(path) == _key(profile.database.parent):
                    _database(profile, directory)
                created += int(_initialize_lock(directory))
                reader = stack.enter_context(_open_lock(directory))
            lock = _FileLock(directory, reader)
            lock.verify_stat()
            _initialization_shared(lock, deadline=deadline, check=check)
            stack.callback(lock.release)
            lock.verify()
    return {"roots": [str(path) for path in profile.roots], "created_locks": created}


def _make_replica_operation_gate_original(*, importer=__import__, get=object.__getattribute__,
        dictionary_get=dict.get, function_type=type(lambda: None), kind_of=type,
        error_type=RuntimeError):
    # Trusted one-time bootstrap, before any RP1 operation can call back.
    # The independent original validator is invoked before cache dispatch.
    bound = []
    validator = None

    def bootstrap():
        nonlocal validator
        if validator is not None or bound:
            raise error_type("Original replica operation dispatcher is already bound")
        module = importer("reno.personal_authority_replica_operation",
                          fromlist=("_shared_entry_original",))
        dictionary = get(module, "__dict__")
        dispatch = dictionary_get(dictionary, "_shared_entry_original")
        prepare = dictionary_get(dictionary, "_make_shared_gate_preflight_original")
        if kind_of(dispatch) is not function_type or kind_of(prepare) is not function_type:
            raise error_type("Original replica operation dispatcher is unavailable")
        bound.append(dispatch)
        validator = prepare(bound, dispatch)
        if kind_of(validator) is not function_type:
            raise error_type("Original replica operation validator is unavailable")
        return bound, (dispatch,)

    def gate(kind, subject=None):
        if validator is None:
            # The original operation module completes the no-argument
            # bootstrap before freezing its actual function/closure catalog.
            importer("reno.personal_authority_replica_operation",
                     fromlist=("_shared_entry_original",))
        if validator is None:
            raise error_type("Original replica operation validator is unavailable")
        validator()
        return bound[0](kind, subject)
    return gate, bootstrap


_REPLICA_OPERATION_GATE_ORIGINAL, _bootstrap_replica_operation_gate_original = _make_replica_operation_gate_original()


def _replica_operation_entry_original(kind, *, generator=False):
    # Applied only to the fixed definitions below and the original routing
    # entry. No runtime registration, caller adapter or bypass argument.
    gate = _REPLICA_OPERATION_GATE_ORIGINAL
    kind_of, text_type = type, str

    def decorate(function):
        if generator:
            def entry(*args, **kwargs):
                gate(kind)
                yield from function(*args, **kwargs)
        else:
            def entry(*args, **kwargs):
                subject = args[0] if args else None
                gate(kind, subject)
                return function(*args, **kwargs)
        entry.__name__, entry.__qualname__, entry.__module__ = function.__name__, function.__qualname__, function.__module__
        entry.__doc__ = function.__doc__
        return entry
    return decorate


def _require_replica_operation_entry_original():
    from .personal_authority_replica_operation import _observe_lifecycle_entry_original
    _observe_lifecycle_entry_original()


def _require_replica_operation_scope_original():
    from .personal_authority_replica_operation import _observe_lifecycle_scope_original
    _observe_lifecycle_scope_original()


@_replica_operation_entry_original("guard")
def _require_replica_group_original(guard):
    from .personal_authority_replica_operation import _observe_lifecycle_guard_original
    _observe_lifecycle_guard_original(guard)
    from .personal_authority_replica_group import require_registered_guard_original
    require_registered_guard_original(guard)


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


class StorageGuard:
    def __init__(self, profiles, exclusive, locks):
        self._profiles = tuple(profiles)
        self._profile_ids = frozenset(profile.identity for profile in self._profiles)
        self._exclusive, self._locks = exclusive, tuple(locks)
        self._owner = _owner()
        self._active = True

    @property
    def profiles(self):
        return self._profiles

    @property
    def profile(self):
        # Compatibility primary: a subset borrow never replaces this guard.
        return self._profiles[0]

    @_replica_operation_entry_original("guard")
    def allows(self, profile):
        """Authorize exact membership only while this same guard is active."""
        _require_replica_group_original(self)
        profile = _profile(profile)
        self.verify()
        return profile.identity in self._profile_ids

    @property
    def exclusive(self):
        return self._exclusive

    @_replica_operation_entry_original("guard")
    def verify(self):
        _require_replica_group_original(self)
        if not self._active or _CURRENT.get() is not self:
            _fail("STORAGE_GUARD_REQUIRED")
        if self._owner != _owner():
            _fail("STORAGE_GUARD_CONTEXT")
        _verify_lock_group(self._locks)
        directories = {_key(lock.directory.path): lock.directory for lock in self._locks}
        for profile in self._profiles:
            _database(profile, directories[_key(profile.database.parent)])
        return self


@_replica_operation_entry_original("entry")
def current_guard():
    """Return this execution context's live guard without acquiring any lock."""
    _require_replica_operation_entry_original()
    guard = _CURRENT.get()
    if guard is not None:
        _require_replica_group_original(guard)
        guard.verify()
    return guard


@_replica_operation_entry_original("entry")
def require_guard(profile, exclusive=False):
    _require_replica_operation_entry_original()
    profile = _profile(profile)
    if type(exclusive) is not bool:
        _fail("STORAGE_INVALID_PROFILE", 400)
    guard = _CURRENT.get()
    if guard is None:
        _fail("STORAGE_GUARD_REQUIRED")
    _require_replica_group_original(guard)
    if not guard.allows(profile):
        _fail("STORAGE_PROFILE_MISMATCH")
    if exclusive and not guard.exclusive:
        _fail("STORAGE_LOCK_UPGRADE")
    return guard


_GUARD_VERIFY_ORIGINAL = StorageGuard.verify
_GUARD_ALLOWS_ORIGINAL = StorageGuard.allows
_PROFILE_IDENTITY_ORIGINAL = StorageProfile.identity
_PROFILE_PATH_TYPE = type(Path())


@dataclass(frozen=True, slots=True)
class _GuardFilePlan:
    """Expected objects only, never a cached verification or writer permit.

    The fixed Namespace verifier owns the paired directory observations and
    its original binding/context lifetime. These low-level checks cannot
    replace require_guard and are not connected to ordinary writers.
    """
    guard: object
    owner: tuple
    profiles: tuple
    profile_ids: frozenset
    exclusive: bool
    locks: tuple
    profile_fields: tuple
    lock_fields: tuple
    directories: tuple
    directory_fields: tuple


def _guard_has_original_dispatch(guard):
    return (type(guard) is StorageGuard
        and _original_bound_method(guard, "verify", _GUARD_VERIFY_ORIGINAL)
        and _original_bound_method(guard, "allows", _GUARD_ALLOWS_ORIGINAL)
        and type(guard._profiles) is tuple and type(guard._profile_ids) is frozenset
        and type(guard._exclusive) is bool and type(guard._locks) is tuple
        and StorageProfile.identity is _PROFILE_IDENTITY_ORIGINAL
        and all(type(profile) is StorageProfile
            and all(type(getattr(profile, name)) is _PROFILE_PATH_TYPE
                for name in ("database", "media", "reports", "checkpoints"))
            for profile in guard._profiles)
        and _original_lock_dispatch(guard._locks))


def _capture_guard_file_plan_original(guard):
    """Capture expected bindings for one fixed Namespace observation only.

    Unknown dispatch is left to the original full guard path. No observation,
    guard authorization, context manager or callable is supplied to a caller.
    """
    if not _guard_has_original_dispatch(guard):
        return None
    with _filesystem():
        directories = safe._directory_verification_plan(tuple(lock.directory for lock in guard._locks))
        if directories is None:
            return None
        profiles = tuple((profile, profile.database, profile.media, profile.reports, profile.checkpoints)
                         for profile in guard._profiles)
        locks = tuple((lock, lock.directory, lock.stream, lock.stream.raw, lock.identity, lock.locked,
                       _LOCK_STREAM_FILENO_ORIGINAL(lock.stream)) for lock in guard._locks)
        directory_fields = tuple((directory, directory.path, directory.base, directory.create,
                                  directory.handles, tuple(directory.handles), directory.identities,
                                  directory.fd) for directory, _, _ in directories)
    return _GuardFilePlan(guard, guard._owner, guard._profiles, guard._profile_ids,
                          guard._exclusive, guard._locks, profiles, locks, directories, directory_fields)


def _verify_guard_plan_shape_original(plan):
    if type(plan) is not _GuardFilePlan:
        _fail("STORAGE_UNSAFE_PATH", 503)
    guard = plan.guard
    if (not _guard_has_original_dispatch(guard) or guard._owner != plan.owner
            or guard._profiles is not plan.profiles or guard._profile_ids is not plan.profile_ids
            or guard._exclusive is not plan.exclusive or guard._locks is not plan.locks):
        _fail("STORAGE_UNSAFE_PATH", 503)
    for profile, database, media, reports, checkpoints in plan.profile_fields:
        if (profile.database, profile.media, profile.reports, profile.checkpoints) != (
                database, media, reports, checkpoints):
            _fail("STORAGE_UNSAFE_PATH", 503)
    for lock, directory, stream, raw, identity, locked, descriptor in plan.lock_fields:
        if (lock.directory is not directory or lock.stream is not stream
                or stream.raw is not raw
                or lock.identity != identity or lock.locked is not locked
                or _LOCK_STREAM_FILENO_ORIGINAL(stream) != descriptor):
            _fail("STORAGE_UNSAFE_PATH", 503)
    for directory, parent, identities in plan.directories:
        if (directory.parent is not parent or tuple(directory.identities) != identities
                or not _original_bound_method(directory, "verify", safe._DIRECTORY_VERIFY_ORIGINAL)):
            _fail("STORAGE_UNSAFE_PATH", 503)
    for directory, path, base, create, handles, handle_values, identities, descriptor in plan.directory_fields:
        if (directory.path != path or directory.base != base or directory.create is not create
                or directory.handles is not handles or tuple(directory.handles) != handle_values
                or directory.identities is not identities or directory.fd != descriptor):
            _fail("STORAGE_UNSAFE_PATH", 503)


def _verify_guard_plan_directories_original(plan):
    """Make a fresh complete physical observation, even on a failed body exit."""
    if type(plan) is not _GuardFilePlan:
        _fail("STORAGE_UNSAFE_PATH", 503)
    with _filesystem():
        safe._verify_directory_plan(plan.directories)


def _verify_guard_plan_context_original(plan, profile, exclusive):
    if type(plan) is not _GuardFilePlan:
        _fail("STORAGE_UNSAFE_PATH", 503)
    guard = plan.guard
    if not guard._active or _CURRENT.get() is not guard:
        _fail("STORAGE_GUARD_REQUIRED")
    if guard._owner != _owner():
        _fail("STORAGE_GUARD_CONTEXT")
    _verify_guard_plan_shape_original(plan)
    if type(profile) is not StorageProfile or type(exclusive) is not bool:
        _fail("STORAGE_INVALID_PROFILE", 400)


def _verify_guard_plan_state_original(plan, profile, exclusive=False):
    """Read current custody/membership only; never supply a physical proof.

    Only the fixed Namespace read body uses this partial leaf, between actual
    full directory/file observations. Its registry, clock, cancellation,
    Context token and resource ledger checks remain in that original caller.
    No ordinary writer or require_guard path consumes this expected plan.
    """
    _verify_guard_plan_context_original(plan, profile, exclusive)
    if profile.identity not in plan.profile_ids:
        _fail("STORAGE_PROFILE_MISMATCH")
    if exclusive and not plan.exclusive:
        _fail("STORAGE_LOCK_UPGRADE")


def _verify_guard_plan_files_original(plan, profile, exclusive=False):
    """Check live membership and all original files; this is not a full guard.

    The fixed Namespace caller must also make physical directory observations
    before and after its entire read body, retain its original owner/context
    checks, and withhold results until successful exit. Ordinary require_guard
    never uses this partial leaf or any ambient observation state.
    """
    _verify_guard_plan_context_original(plan, profile, exclusive)
    # Preserve verification of every held lock and every covered database,
    # including when the requested profile is only one member of a group.
    with _filesystem():
        for lock in plan.locks:
            _FILE_LOCK_ENTRY_ORIGINAL(lock)
            descriptor = _LOCK_STREAM_FILENO_ORIGINAL(lock.stream)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, len(_MARKER) + 1) != _MARKER:
                _fail("STORAGE_LOCK_INVALID", 503)
            _FILE_LOCK_ENTRY_ORIGINAL(lock)
    directories = {_key(lock.directory.path): lock.directory for lock in plan.locks}
    for covered_profile in plan.profiles:
        _database(covered_profile, directories[_key(covered_profile.database.parent)])
    _verify_guard_plan_state_original(plan, profile, exclusive)


@contextmanager
@_replica_operation_entry_original("scope", generator=True)
def _guard_many(profiles, exclusive, timeout, check_cancelled, *, _owned_ex=None):
    _require_replica_operation_scope_original()
    profiles = _profiles(profiles)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 3600:
        _fail("STORAGE_INVALID_TIMEOUT", 400)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("STORAGE_INVALID_ARGUMENT", 400)
    if _owned_ex is not None:
        from . import personal_authority_owned_ex_group as owned_group
        yield from owned_group._guard_many_owned(profiles, exclusive, timeout, check_cancelled, _owned_ex)
        return
    check = check_cancelled or (lambda: None)
    check()
    guard = _CURRENT.get()
    if guard is not None:
        _require_replica_group_original(guard)
        guard.verify()
        if any(profile.identity not in guard._profile_ids for profile in profiles):
            _fail("STORAGE_PROFILE_MISMATCH")
        if exclusive and not guard.exclusive:
            _fail("STORAGE_LOCK_UPGRADE")
        yield guard
        _require_replica_group_original(guard)
        guard.verify()
        return
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        locks = []
        for root in _roots(profiles):
            check()
            with _filesystem():
                directory = stack.enter_context(safe._Directory(root, base=root))
                stream = stack.enter_context(_open_lock(directory))
            lock = _FileLock(directory, stream)
            lock.verify_stat()
            # timeout=0 still makes exactly one immediate OS attempt per root.
            while True:
                check()
                if lock.attempt(exclusive):
                    stack.callback(lock.release)
                    break
                if time.monotonic() >= deadline:
                    _fail("STORAGE_BUSY")
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
            lock.verify()
            locks.append(lock)
        guard = StorageGuard(profiles, exclusive, locks)
        token = _CURRENT.set(guard)
        try:
            check()
            guard.verify()
            yield guard
            guard.verify()
        finally:
            guard._active = False
            _CURRENT.reset(token)


def _guard(profile, exclusive, timeout, check_cancelled):
    return _guard_many((profile,), exclusive, timeout, check_cancelled)


def shared_many(profiles, *, timeout=30, check_cancelled=None):
    """Borrow a protected subset, or acquire the whole bounded group at once."""
    return _guard_many(profiles, False, timeout, check_cancelled)


def exclusive_many(profiles, *, timeout=30, check_cancelled=None):
    """Acquire all physical roots before any SQLite transaction is started."""
    return _guard_many(profiles, True, timeout, check_cancelled)


def shared(profile, *, timeout=30, check_cancelled=None):
    return _guard(profile, False, timeout, check_cancelled)


def exclusive(profile, *, timeout=30, check_cancelled=None):
    return _guard(profile, True, timeout, check_cancelled)
