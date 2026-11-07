"""Windows live-SQL source leases at one existing absolute deadline.

READ|WRITE sharing preserves existing writers; original path/fstat signatures
reject observed changes at normal exit. The lease alone owns numeric FD close.
Yielded unbuffered FileIO borrows that FD, and its caller's read/seek operations
have no automatic clock gate or byte charge. Opaque native/CRT calls may cross
expiry; these cooperative gates do not interrupt them or establish source_closed.
"""
from __future__ import annotations

from dataclasses import dataclass

import ctypes
import io
import math
import os
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_namespace3_deadline as deadline
from . import storage_source as source
from . import sync_media as safe

_WINDOWS = os.name == "nt"
_monotonic = time.monotonic
_file_io, _fstat, _close_fd = io.FileIO, os.fstat, os.close
if _WINDOWS:
    import msvcrt
    _create_file = safe._kernel.CreateFileW
    _get_info = safe._kernel.GetFileInformationByHandle
    _close_handle = safe._kernel.CloseHandle
    _open_osfhandle = msvcrt.open_osfhandle
    _get_osfhandle = msvcrt.get_osfhandle
else:
    _create_file = _get_info = _close_handle = None
    _open_osfhandle = _get_osfhandle = None

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_NEW, _OPENING, _ACTIVE, _RETIRED = "new", "opening", "active", "retired"
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN, _TRANSFERRED = "closed", "unknown", "transferred"
_TRANSFER_PENDING = "transfer_pending"
SourceReadError = source.SourceReadError


@dataclass(frozen=True, slots=True)
class LiveSqlCloseReceiptV1:
    """Actual cached owner facts, without handles or source-close authority."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    directory_borrowed: bool
    handle_state: str
    fd_state: str
    raw_state: str
    transfer_state: str
    native_file_identity: tuple[int, int, int] | None


class LiveSqlDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "LIVE_SQL_DEADLINE_" + suffix
        super().__init__(self.code)


Error = LiveSqlDeadlineError


def _fail(suffix):
    raise LiveSqlDeadlineError(suffix) from None


def _note(primary, note="LIVE_SQL_DEADLINE_CLEANUP_FAILED"):
    try:
        BaseException.add_note(primary, note)
    except BaseException:
        pass


def _token(value):
    try:
        return deadline._token(value)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _clock(token):
    absolute = _token(token).absolute_monotonic
    now = _monotonic()
    if type(now) is not float or not math.isfinite(now) or now < 0.0:
        _fail("INVALID_ARGUMENT")
    if now >= absolute:
        _fail("DEADLINE")


def _raw_fileno(raw):
    return io.FileIO.fileno(raw)


def _retire_raw(raw):
    return io.FileIO.close(raw)


def _information(handle, token):
    info = safe._HandleInfo()
    address = ctypes.byref(info)
    _clock(token)
    ok = _get_info(handle, address)
    if not ok:
        number = ctypes.get_last_error()
        raise ctypes.WinError(number)
    _clock(token)
    return info


def _native_file(info):
    if info.attributes & (safe._REPARSE | 0x10) or info.links != 1:
        raise SourceReadError("SOURCE_UNSAFE")
    return info.volume, info.index_high, info.index_low


class LiveSqlDeadlineLease:
    """Borrow one ACTIVE directory; own HANDLE transfer and sole FD close."""
    __slots__ = ("_directory", "_name", "_deadline_token", "_phase", "_handle",
                 "_fd", "_raw", "_first", "_close_failure", "_transfer_failure",
                 "_before", "_opened", "_native_file_identity",
                 "_enter_returned", "_exit_attempted", "_normal_exit", "_transfer_state")

    def __init__(self, directory, name, *, deadline_token):
        if type(self) is not LiveSqlDeadlineLease:
            _fail("INVALID_ARGUMENT")
        if not _WINDOWS:
            _fail("UNSUPPORTED")
        token = _token(deadline_token)
        if (type(directory) is not directories.DeadlineDirectory
                or directory.deadline_token is not token or type(name) is not str):
            _fail("INVALID_ARGUMENT")
        directory._live()
        safe._entry_name(name)
        _clock(token)
        self._directory, self._name, self._deadline_token = directory, name, token
        self._phase, self._first = _NEW, None
        # Fixed slots exist before any resource-producing call.
        self._handle, self._fd, self._raw = [None, _EMPTY], [None, _EMPTY], [None, _EMPTY]
        self._close_failure = self._transfer_failure = None
        self._before = self._opened = self._native_file_identity = None
        self._enter_returned = self._exit_attempted = self._normal_exit = False
        self._transfer_state = "not_started"
        _clock(token)

    @property
    def close_receipt(self):
        # Only local cached slots; do not query owners, clocks or resources.
        failed = (self._first is not None or self._close_failure is not None
                  or self._transfer_failure is not None)
        return LiveSqlCloseReceiptV1(1, "live_sql_file", "native_owner_close_facts_only",
            self._phase, self._enter_returned, self._exit_attempted,
            failed, self._normal_exit and not failed, True,
            self._handle[1], self._fd[1], self._raw[1], self._transfer_state,
            self._native_file_identity)

    def _bound_live(self):
        if self._directory.deadline_token is not self._deadline_token:
            _fail("INVALID_ARGUMENT")
        self._directory._live()
        _clock(self._deadline_token)

    def _verify_directory(self):
        _clock(self._deadline_token)
        self._directory.verify()
        _clock(self._deadline_token)

    def _path_signature(self):
        _clock(self._deadline_token)
        current = self._directory.entry_stat(self._name)
        _clock(self._deadline_token)
        return source.file_signature(current)

    def _current_fd(self):
        if self._fd[1] != _OWNED or self._raw[1] != _OWNED:
            _fail("CLOSED")
        _clock(self._deadline_token)
        actual = _raw_fileno(self._raw[0])
        _clock(self._deadline_token)
        if type(actual) is not int or actual < 0 or actual != self._fd[0]:
            _fail("INVALID_ARGUMENT")
        return self._fd[0]

    def _handle_signature(self):
        fd = self._current_fd()
        _clock(self._deadline_token)
        current = _fstat(fd)
        _clock(self._deadline_token)
        return source.file_signature(current)

    def _late_native(self):
        if self._fd[1] != _OWNED:
            _fail("CLOSED")
        _clock(self._deadline_token)
        handle = _get_osfhandle(self._fd[0])
        _clock(self._deadline_token)
        if type(handle) is not int or handle < 0 or handle == _INVALID_HANDLE:
            _fail("INVALID_ARGUMENT")
        # This HANDLE is only borrowed for query; it never becomes a closer.
        current = _native_file(_information(handle, self._deadline_token))
        if current != self._native_file_identity:
            raise SourceReadError("SOURCE_CHANGED")

    def __enter__(self):
        if self._first is not None:
            raise self._first
        if self._phase != _NEW:
            _fail("CLOSED")
        self._phase = _OPENING
        token = self._deadline_token
        try:
            self._bound_live()
            self._verify_directory()
            self._before = self._path_signature()
            _clock(token)
            native = safe._native_path(self._directory.path / self._name)
            _clock(token)
            _clock(token)
            handle = _create_file(native, 0x80000000, 3, None, 3, 0x00200000, None)
            if handle == _INVALID_HANDLE:
                number = ctypes.get_last_error()
                raise ctypes.WinError(number)
            self._handle[0], self._handle[1] = handle, _OWNED
            _clock(token)
            self._native_file_identity = _native_file(_information(handle, token))
            flags = os.O_BINARY | os.O_RDONLY
            _clock(token)
            self._handle[1] = _TRANSFER_PENDING
            self._transfer_state = _TRANSFER_PENDING
            try:
                fd = _open_osfhandle(handle, flags)
                if type(fd) is not int or fd < 0:
                    _fail("INVALID_ARGUMENT")
            except BaseException as primary:
                # Only an actual legal FD return can establish transfer.
                self._handle[1] = _UNKNOWN
                self._transfer_state = _UNKNOWN
                self._transfer_failure = primary
                _note(primary, "LIVE_SQL_DEADLINE_TRANSFER_UNKNOWN")
                raise
            self._fd[0], self._fd[1] = fd, _OWNED
            self._handle[1] = _TRANSFERRED
            self._transfer_state = _TRANSFERRED
            _clock(token)
            _clock(token)
            raw = _file_io(fd, "rb", closefd=False)
            self._raw[0], self._raw[1] = raw, _OWNED
            _clock(token)
            if type(raw) is not io.FileIO or raw.closefd is not False:
                _fail("INVALID_ARGUMENT")
            self._opened = self._handle_signature()
            # Windows path stat/fstat ctime must remain separate representations.
            if self._opened[:4] != self._before[:4]:
                raise SourceReadError("SOURCE_CHANGED")
            self._verify_directory()
            _clock(token)
            self._phase = _ACTIVE
            self._enter_returned = True
            return raw
        except BaseException as primary:
            self._first = primary
            cleanup = self._cleanup()
            if cleanup is not None:
                _note(primary)
            raise

    def _terminal(self):
        self._bound_live()
        # Keep original fstat-first short circuit and immutable expectations.
        if self._handle_signature() != self._opened:
            raise SourceReadError("SOURCE_CHANGED")
        self._late_native()
        if self._path_signature() != self._before:
            raise SourceReadError("SOURCE_CHANGED")
        self._verify_directory()
        _clock(self._deadline_token)

    def _cleanup(self):
        self._exit_attempted = True
        self._phase = _RETIRED
        first = self._close_failure
        if self._raw[1] == _OWNED:
            self._raw[1] = _ATTEMPTED
            try:
                if type(self._raw[0]) is not io.FileIO:
                    _fail("INVALID_RESULT")
                if not self._raw[0].closed:
                    actual = _retire_raw(self._raw[0])
                    if actual is not None:
                        _fail("INVALID_RESULT")
            except BaseException as failure:
                self._raw[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._raw[1] = _CLOSED
        if self._fd[1] == _OWNED:
            self._fd[1] = _ATTEMPTED
            try:
                actual = _close_fd(self._fd[0])
                if actual is not None:
                    _fail("INVALID_RESULT")
            except BaseException as failure:
                self._fd[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._fd[1] = _CLOSED
        if self._handle[1] == _OWNED:
            self._handle[1] = _ATTEMPTED
            try:
                ok = _close_handle(self._handle[0])
                if ok is False or (type(ok) is int and ok == 0):
                    number = ctypes.get_last_error()
                    raise ctypes.WinError(number)
                if type(ok) is not int or not -(1 << 31) <= ok < (1 << 31):
                    _fail("INVALID_RESULT")
            except BaseException as failure:
                self._handle[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._handle[1] = _CLOSED
        self._close_failure = first
        return first

    def __exit__(self, _kind, primary, _tb):
        if self._phase != _ACTIVE:
            if self._first is not None:
                raise self._first
            _fail("CLOSED")
        if primary is not None:
            self._first = primary
        else:
            try:
                self._terminal()
            except BaseException as failure:
                self._first = failure
        cleanup = self._cleanup()
        if cleanup is not None:
            if self._first is None:
                self._first = cleanup
            else:
                _note(self._first)
        if self._first is not None:
            if self._first is primary:
                return None
            raise self._first
        try:
            _clock(self._deadline_token)
        except BaseException as failure:
            self._first = failure
            raise
        if (not self._enter_returned or not self._exit_attempted
                or self._handle[1] != _TRANSFERRED or self._transfer_state != _TRANSFERRED
                or self._fd[1] != _CLOSED or self._raw[1] != _CLOSED):
            self._first = LiveSqlDeadlineError("INVALID_RESULT")
            raise self._first from None
        self._normal_exit = True
        return None


def source_reader(directory, name, *, deadline_token):
    return LiveSqlDeadlineLease(directory, name, deadline_token=deadline_token)
