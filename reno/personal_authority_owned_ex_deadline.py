"""Opt-in Windows lock streams with one registered numeric FD closer.

The existing legacy directory is borrowed, and no Guard, lock acquisition,
writer permit or source_closed fact is created here. Exact returned wrappers
borrow the FD; raw caller methods and opaque native calls remain ungated.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import io
import math
import os
import time

from . import personal_authority_namespace3_contract as contract
from . import personal_authority_namespace3_deadline as deadline
from . import storage_lifecycle as lifecycle
from . import sync_media as safe

_WINDOWS = os.name == "nt"
_monotonic = time.monotonic
_file_io, _buffered_reader, _close_fd = io.FileIO, io.BufferedReader, os.close
if _WINDOWS:
    _create_file = safe._kernel.CreateFileW
    _get_info = safe._kernel.GetFileInformationByHandle
    _close_handle = safe._kernel.CloseHandle
    _open_osfhandle = safe.msvcrt.open_osfhandle
else:
    _create_file = _get_info = _close_handle = _open_osfhandle = None

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_NEW, _OPENING, _ACTIVE, _RETIRED = "new", "opening", "active", "retired"
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN, _TRANSFERRED = "closed", "unknown", "transferred"
_TRANSFER_PENDING = "transfer_pending"


class OwnedExDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "OWNED_EX_DEADLINE_" + suffix
        super().__init__(self.code)


Error = OwnedExDeadlineError


def _fail(suffix):
    raise OwnedExDeadlineError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "OWNED_EX_DEADLINE_CLEANUP_FAILED")
    except BaseException:
        pass


def _token(value):
    try:
        return deadline._token(value)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _now():
    value = _monotonic()
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        _fail("INVALID_ARGUMENT")
    return value


def _clock(token):
    if _now() >= _token(token).absolute_monotonic:
        _fail("DEADLINE")


def _operation(value):
    if type(value) is not tuple or len(value) != 3:
        _fail("INVALID_ARGUMENT")
    try:
        for item in value:
            contract._uuid(item)
    except contract.ContractError:
        _fail("INVALID_ARGUMENT")
    return value


@dataclass(frozen=True, slots=True, init=False)
class _OwnedExBinding:
    """Process-local lineage and fixed owner slots, never an authority DTO."""
    _operation: tuple[str, str, str]
    _parent_token: deadline.Namespace3Deadline
    _effective_token: deadline.Namespace3Deadline
    _scope_identity: object
    _pins: tuple
    _owners: list
    _group_claim: object | None

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def operation(self):
        return self._operation

    @property
    def parent_token(self):
        return self._parent_token

    @property
    def effective_token(self):
        return self._effective_token

    @property
    def scope_identity(self):
        return self._scope_identity


def _new_owned_ex_binding(operation, *, parent_token, effective_token, scope_identity):
    operation = _operation(operation)
    parent, effective = _token(parent_token), _token(effective_token)
    if type(scope_identity) is not object:
        _fail("INVALID_ARGUMENT")
    started = _now()
    if (effective.absolute_monotonic > parent.absolute_monotonic
            or effective.absolute_monotonic > started + deadline.locked.MAX_SECONDS):
        _fail("INVALID_ARGUMENT")
    if started >= effective.absolute_monotonic:
        _fail("DEADLINE")
    result = object.__new__(_OwnedExBinding)
    object.__setattr__(result, "_operation", operation)
    object.__setattr__(result, "_parent_token", parent)
    object.__setattr__(result, "_effective_token", effective)
    object.__setattr__(result, "_scope_identity", scope_identity)
    object.__setattr__(result, "_pins", (operation, parent, effective, scope_identity,
        parent.absolute_monotonic, effective.absolute_monotonic, started))
    object.__setattr__(result, "_owners", [None] * 4)
    object.__setattr__(result, "_group_claim", None)
    return result


def _binding(value):
    if type(value) is not _OwnedExBinding:
        _fail("INVALID_ARGUMENT")
    try:
        pins, owners = value._pins, value._owners
        if (type(pins) is not tuple or len(pins) != 7
                or type(owners) is not list or len(owners) != 4
                or (value._group_claim is not None and type(value._group_claim) is not object)
                or value._operation is not pins[0]
                or value._parent_token is not pins[1]
                or value._effective_token is not pins[2]
                or value._scope_identity is not pins[3]
                or type(value._scope_identity) is not object):
            _fail("INVALID_ARGUMENT")
        _operation(value._operation)
        parent, effective = _token(value._parent_token), _token(value._effective_token)
        if (type(pins[4]) is not float or type(pins[5]) is not float
                or type(pins[6]) is not float or not math.isfinite(pins[6]) or pins[6] < 0.0
                or parent.absolute_monotonic != pins[4]
                or effective.absolute_monotonic != pins[5]
                or pins[5] > pins[4] or pins[5] > pins[6] + deadline.locked.MAX_SECONDS):
            _fail("INVALID_ARGUMENT")
    except AttributeError:
        _fail("INVALID_ARGUMENT")
    return value


@dataclass(frozen=True, slots=True)
class LockStreamCloseReceiptV1:
    """Own cached close facts; TRANSFERRED is custody transfer, not close."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    directory_borrowed: bool
    root_ordinal: int
    handle_state: str
    fd_state: str
    raw_state: str
    buffer_state: str
    transfer_state: str
    native_file_identity: tuple[int, int, int] | None
    fd_close_owner: str


def _retire_buffer(buffer):
    return io.BufferedReader.close(buffer)


def _retire_raw(raw):
    return io.FileIO.close(raw)


def _raw_fileno(raw):
    return io.FileIO.fileno(raw)


def _raw_closefd(raw):
    return io.FileIO.closefd.__get__(raw, io.FileIO)


def _raw_closed(raw):
    return io.FileIO.closed.__get__(raw, io.FileIO)


def _buffer_raw(buffer):
    return io.BufferedReader.raw.__get__(buffer, io.BufferedReader)


def _bool_result(value):
    if value is False or (type(value) is int and value == 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        _fail("INVALID_RESULT")


def _directory(value):
    if (type(value) is not safe._Directory or value.create is not False
            or value.parent is not None or value.base != value.path
            or value._known_closed is not False or value._close_failure is not None
            or type(value.handles) is not list or not value.handles
            or not lifecycle._original_bound_method(value, "verify", safe._DIRECTORY_VERIFY_ORIGINAL)
            or not lifecycle._original_bound_method(value, "entry_stat", lifecycle._DIRECTORY_ENTRY_ORIGINAL)):
        _fail("INVALID_ARGUMENT")
    return value


class LockStreamDeadlineLease:
    """Borrow one exact legacy root; own its new lock stream and sole FD."""
    __slots__ = ("_directory", "_binding", "_token", "_root_ordinal", "_phase",
        "_handle", "_fd", "_raw", "_buffer", "_transfer_state", "_native_file_identity",
        "_first", "_first_tb", "_enter_returned", "_exit_attempted", "_normal_exit",
        "_raw_closefd_cached", "_fd_close_owner")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, directory, *, binding, root_ordinal):
        if type(self) is not LockStreamDeadlineLease:
            _fail("INVALID_ARGUMENT")
        try:
            object.__getattribute__(self, "_phase")
        except AttributeError:
            pass
        else:
            # Reject reinitialization before losing any existing resource slot.
            _fail("CLOSED")
        self._directory, self._binding, self._token = directory, binding, None
        self._root_ordinal, self._phase = root_ordinal, _NEW
        self._handle, self._fd = [None, _EMPTY], [None, _EMPTY]
        self._raw, self._buffer = [None, _EMPTY], [None, _EMPTY]
        self._first = self._first_tb = self._native_file_identity = None
        self._transfer_state, self._fd_close_owner = "not_started", "numeric_owner"
        self._raw_closefd_cached = None
        self._enter_returned = self._exit_attempted = self._normal_exit = False
        try:
            if not _WINDOWS:
                _fail("UNSUPPORTED")
            _directory(directory)
            binding = _binding(binding)
            if type(root_ordinal) is not int or not 0 <= root_ordinal < 4:
                _fail("INVALID_ARGUMENT")
            self._token = binding._effective_token
            if binding._owners[root_ordinal] is not None:
                _fail("CLOSED")
            # Actual owner registration is consumed before native creation.
            binding._owners[root_ordinal] = self
            _clock(self._token)
        except BaseException as failure:
            self._capture(failure)
            self._phase = _RETIRED
            self._raise_first()

    @property
    def deadline_token(self):
        return self._token

    @property
    def close_receipt(self):
        # Only this owner's cached scalar/tuple slots; no binding/lower reads.
        return LockStreamCloseReceiptV1(1, "lock_stream", "native_owner_close_facts_only",
            self._phase, self._enter_returned, self._exit_attempted,
            self._first is not None, self._normal_exit and self._first is None,
            True, self._root_ordinal, self._handle[1], self._fd[1], self._raw[1],
            self._buffer[1], self._transfer_state, self._native_file_identity,
            self._fd_close_owner)

    def _capture(self, failure, *, original_tb=None, cleanup=False):
        if self._first is None:
            self._first = failure
            self._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                              if original_tb is None else original_tb)
        elif cleanup and failure is not self._first:
            _note(self._first)

    def _raise_first(self):
        if self._first is not None:
            raise BaseException.with_traceback(self._first, self._first_tb) from None

    def _verify_directory(self):
        _clock(self._token)
        directory = _directory(self._directory)
        safe._Directory.verify(directory)
        _clock(self._token)

    def _bound(self):
        binding = _binding(self._binding)
        if (binding._effective_token is not self._token
                or binding._owners[self._root_ordinal] is not self):
            _fail("INVALID_ARGUMENT")

    def _unknown_wrapper_custody(self):
        self._raw[1], self._fd[1] = _UNKNOWN, _UNKNOWN
        self._fd_close_owner = "unknown_wrapper"

    def _adopt_raw(self, raw):
        # Retain even rejected objects: do not trigger an alien finalizer here.
        self._raw[0] = raw
        if type(raw) is not io.FileIO:
            self._raw[1] = _UNKNOWN
            _fail("INVALID_RESULT")
        self._raw[1] = _OWNED
        try:
            actual = _raw_fileno(raw)
        except BaseException:
            self._unknown_wrapper_custody()
            raise
        if type(actual) is not int or actual < 0:
            self._unknown_wrapper_custody()
            _fail("INVALID_RESULT")
        if actual != self._fd[0]:
            self._raw[1] = _UNKNOWN
            _fail("INVALID_RESULT")
        try:
            closefd = _raw_closefd(raw)
        except BaseException:
            self._unknown_wrapper_custody()
            raise
        if type(closefd) is not bool:
            self._unknown_wrapper_custody()
            _fail("INVALID_RESULT")
        self._raw_closefd_cached = closefd
        if closefd:
            # This exact same-FD raw may close it. Never follow with os.close.
            self._fd[1] = _UNKNOWN
            self._fd_close_owner = "unknown_wrapper"
            _fail("INVALID_RESULT")

    def _adopt_buffer(self, buffer):
        self._buffer[0] = buffer
        if type(buffer) is not io.BufferedReader:
            self._buffer[1] = _UNKNOWN
            _fail("INVALID_RESULT")
        self._buffer[1] = _OWNED
        try:
            actual = _buffer_raw(buffer)
        except BaseException:
            self._buffer[1] = _UNKNOWN
            raise
        if actual is not self._raw[0]:
            self._buffer[1] = _UNKNOWN
            _fail("INVALID_RESULT")

    def _open(self):
        _clock(self._token)
        native = safe._native_path(self._directory.path / lifecycle.LOCK_NAME)
        _clock(self._token)
        _clock(self._token)
        try:
            handle = _create_file(native, 0x80000000, 3, None, 3, 0x00200000, None)
        except BaseException:
            self._handle[1] = _UNKNOWN
            raise
        if type(handle) is int and handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        if type(handle) is not int or not 0 < handle < _INVALID_HANDLE:
            self._handle[0], self._handle[1] = handle, _UNKNOWN
            _fail("INVALID_RESULT")
        self._handle[0], self._handle[1] = handle, _OWNED
        _clock(self._token)
        info = safe._HandleInfo()
        address = ctypes.byref(info)
        _clock(self._token)
        ok = _get_info(handle, address)
        _bool_result(ok)
        self._native_file_identity = (info.volume, info.index_high, info.index_low)
        _clock(self._token)
        if info.attributes & (safe._REPARSE | 0x10) or info.links != 1:
            lifecycle._fail("STORAGE_UNSAFE_PATH", 503)
        flags = os.O_BINARY | os.O_RDONLY
        _clock(self._token)
        self._handle[1] = self._transfer_state = _TRANSFER_PENDING
        try:
            fd = _open_osfhandle(handle, flags)
            if type(fd) is not int or fd < 0:
                _fail("INVALID_RESULT")
        except BaseException:
            self._handle[1] = self._transfer_state = _UNKNOWN
            raise
        self._fd[0], self._fd[1] = fd, _OWNED
        self._handle[1] = self._transfer_state = _TRANSFERRED
        _clock(self._token)
        _clock(self._token)
        try:
            raw = _file_io(fd, "rb", closefd=False)
        except BaseException:
            # closefd=False borrows this known FD; constructor failure cannot
            # transfer its sole numeric close obligation to an absent wrapper.
            self._raw[1] = _UNKNOWN
            raise
        self._adopt_raw(raw)
        _clock(self._token)
        _clock(self._token)
        try:
            buffer = _buffered_reader(raw)
        except BaseException:
            self._buffer[1] = _UNKNOWN
            raise
        self._adopt_buffer(buffer)
        _clock(self._token)

    def __enter__(self):
        self._raise_first()
        if self._phase != _NEW:
            _fail("CLOSED")
        self._phase = _OPENING
        try:
            self._bound()
            self._verify_directory()
            self._open()
            self._verify_directory()
            _clock(self._token)
            self._phase, self._enter_returned = _ACTIVE, True
            return self._buffer[0]
        except BaseException as failure:
            self._capture(failure)
            self._cleanup()
            self._raise_first()

    def _cleanup_clock(self):
        try:
            _clock(self._token)
        except BaseException as failure:
            self._capture(failure, cleanup=True)

    def _close_buffer(self):
        if self._buffer[1] != _OWNED:
            return
        self._cleanup_clock()
        self._buffer[1] = _ATTEMPTED
        try:
            buffer = self._buffer[0]
            if type(buffer) is not io.BufferedReader or _buffer_raw(buffer) is not self._raw[0]:
                _fail("INVALID_RESULT")
            if _retire_buffer(buffer) is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            self._buffer[1] = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            self._buffer[1] = _CLOSED
            self._cleanup_clock()

    def _close_raw(self):
        if self._raw[1] != _OWNED:
            return
        self._cleanup_clock()
        self._raw[1] = _ATTEMPTED
        try:
            raw = self._raw[0]
            if type(raw) is not io.FileIO:
                _fail("INVALID_RESULT")
            closed = _raw_closed(raw)
            if type(closed) is not bool:
                _fail("INVALID_RESULT")
            if not closed and _retire_raw(raw) is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            self._raw[1] = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            self._raw[1] = _CLOSED
            self._cleanup_clock()

    def _close_fd_slot(self):
        if self._fd[1] != _OWNED:
            return
        self._cleanup_clock()
        self._fd[1] = _ATTEMPTED
        try:
            if self._fd_close_owner != "numeric_owner" or _close_fd(self._fd[0]) is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            self._fd[1] = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            self._fd[1] = _CLOSED
            self._cleanup_clock()

    def _close_handle_slot(self):
        if self._handle[1] != _OWNED:
            return
        self._cleanup_clock()
        self._handle[1] = _ATTEMPTED
        try:
            _bool_result(_close_handle(self._handle[0]))
        except BaseException as failure:
            self._handle[1] = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            self._handle[1] = _CLOSED
            self._cleanup_clock()

    def _cleanup(self):
        self._exit_attempted = True
        self._phase = _RETIRED
        self._close_buffer()
        self._close_raw()
        self._close_fd_slot()
        self._close_handle_slot()
        self._cleanup_clock()

    def _terminal(self):
        self._bound()
        self._verify_directory()
        _clock(self._token)
        if (type(self._buffer[0]) is not io.BufferedReader
                or type(self._raw[0]) is not io.FileIO
                or _buffer_raw(self._buffer[0]) is not self._raw[0]
                or _raw_closefd(self._raw[0]) is not False
                or _raw_fileno(self._raw[0]) != self._fd[0]):
            _fail("INVALID_RESULT")
        _clock(self._token)

    def __exit__(self, _kind, primary, traceback):
        if self._phase != _ACTIVE:
            self._raise_first()
            _fail("CLOSED")
        if primary is not None:
            self._capture(primary, original_tb=traceback)
        elif self._first is None:
            try:
                self._terminal()
            except BaseException as failure:
                self._capture(failure)
        self._cleanup()
        if self._first is None:
            try:
                _clock(self._token)
            except BaseException as failure:
                self._capture(failure)
        if self._first is not None:
            if self._first is primary:
                return None
            self._raise_first()
        if (self._handle[1] != _TRANSFERRED or self._transfer_state != _TRANSFERRED
                or self._fd[1] != _CLOSED or self._raw[1] != _CLOSED
                or self._buffer[1] != _CLOSED or self._fd_close_owner != "numeric_owner"
                or not self._enter_returned or not self._exit_attempted):
            self._capture(OwnedExDeadlineError("INVALID_RESULT"))
            self._raise_first()
        self._normal_exit = True
        return None
