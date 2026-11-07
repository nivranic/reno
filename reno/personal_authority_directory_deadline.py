"""Windows existing directories and readonly leases at one absolute deadline.

This is a new explicit interface, not a replacement for sync_media._Directory.
Only this module's direct physical consumers receive pre/post clock gates.
Yielded readers, CPython/CRT internals and other borrowed consumers remain
outside those gates. Tokens are process-local clock data, never authority.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import time

from . import personal_authority_namespace3_deadline as deadline
from . import sync_media as safe

_WINDOWS = os.name == "nt"
_stat, _fstat, _close_fd = os.stat, os.fstat, os.close
_file_io, _buffered_reader = io.FileIO, io.BufferedReader
if _WINDOWS:
    import msvcrt
    _create_file = safe._kernel.CreateFileW
    _get_info = safe._kernel.GetFileInformationByHandle
    _close_handle = safe._kernel.CloseHandle
    _open_osfhandle = msvcrt.open_osfhandle
else:
    _create_file = _get_info = _close_handle = _open_osfhandle = None

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_NEW, _ACTIVE, _RETIRED = "new", "active", "retired"
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN, _TRANSFERRED = "closed", "unknown", "transferred"
_TRANSFER_PENDING = "transfer_pending"


@dataclass(frozen=True, slots=True)
class DirectoryCloseReceiptV1:
    """Cached facts only; a copied receipt conveys no ownership or authority."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    parent_borrowed: bool
    handle_states: tuple[str, ...]
    handle_native_identities: tuple[tuple[int, int, int] | None, ...]


@dataclass(frozen=True, slots=True)
class ReadonlyCloseReceiptV1:
    """TRANSFERRED is custody transfer, not an independently closed HANDLE."""
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
    buffer_state: str
    transfer_state: str
    native_file_identity: tuple[int, int, int] | None


class DirectoryDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "DIRECTORY_DEADLINE_" + suffix
        super().__init__(self.code)


Error = DirectoryDeadlineError


def _fail(suffix):
    raise DirectoryDeadlineError(suffix) from None


def _note(primary, note="DIRECTORY_DEADLINE_CLEANUP_FAILED"):
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
    now = time.monotonic()
    if type(now) is not float or not math.isfinite(now) or now < 0.0:
        _fail("INVALID_ARGUMENT")
    if now >= absolute:
        _fail("DEADLINE")


def _false(*values):
    if any(value is not False for value in values):
        _fail("INVALID_ARGUMENT")


def _native(path, token):
    _clock(token)
    value = safe._native_path(path)
    _clock(token)
    return value


def _path_stat(path, token):
    native = _native(path, token)
    _clock(token)
    result = _stat(native, follow_symlinks=False)
    _clock(token)
    return result


def _information(handle, token):
    result = safe._HandleInfo()
    address = ctypes.byref(result)
    _clock(token)
    ok = _get_info(handle, address)
    if not ok:
        number = ctypes.get_last_error()
        raise ctypes.WinError(number)
    _clock(token)
    return result


def _native_identity(info):
    return info.volume, info.index_high, info.index_low


def _native_type(info, *, directory):
    if info.attributes & safe._REPARSE or bool(info.attributes & 0x10) != directory:
        safe._error("Media paths must not contain links or unexpected file types")
    if not directory and info.links != 1:
        safe._error("Media files must not have external hard links")


def _file_identity(info, *, path):
    safe._safe_stat(info, directory=False)
    if info.st_nlink != 1:
        safe._error("Media files must not have external hard links")
    values = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
              info.st_mode, info.st_nlink)
    return values + (info.st_ctime_ns,) if path else values


def _retire_buffer(stream):
    return io.BufferedReader.close(stream)


def _retire_raw(stream):
    return io.FileIO.close(stream)


def _close_owned_handle(handle):
    ok = _close_handle(handle)
    # Keep the native error before any later clock/cleanup action. False is
    # accepted only as a failure seam, never as a successful BOOL result.
    if ok is False or (type(ok) is int and ok == 0):
        number = ctypes.get_last_error()
        raise ctypes.WinError(number)
    if type(ok) is not int or not -(1 << 31) <= ok < (1 << 31):
        _fail("INVALID_RESULT")


def _closed_none(result):
    if result is not None:
        _fail("INVALID_RESULT")


class DeadlineDirectory:
    """Own base-and-below HANDLE pins; borrow one exact live direct parent."""
    __slots__ = ("_path", "_base", "_parent", "_deadline_token", "_paths",
                 "_protected_index", "_identities", "_handles", "_native_ids",
                 "_phase", "_close_failure", "_enter_returned",
                 "_exit_attempted", "_receipt_failed", "_normal_exit")

    def __init__(self, path, *, deadline_token, create=False, base=None, parent=None):
        if type(self) is not DeadlineDirectory:
            _fail("INVALID_ARGUMENT")
        if not _WINDOWS:
            _fail("UNSUPPORTED")
        _false(create)
        token = _token(deadline_token)
        _clock(token)
        try:
            selected = Path(path)
            selected_base = selected if base is None else Path(base)
        except (TypeError, ValueError):
            _fail("INVALID_ARGUMENT")
        if not selected.is_absolute() or not selected_base.is_absolute():
            _fail("INVALID_ARGUMENT")
        paths = [Path(selected.anchor)]
        for component in selected.parts[1:]:
            paths.append(paths[-1] / component)
        if selected_base not in paths:
            _fail("INVALID_ARGUMENT")
        protected = paths.index(selected_base)
        if parent is not None:
            if (type(parent) is not DeadlineDirectory
                    or parent.deadline_token is not token
                    or selected == parent.path or selected.parent != parent.path):
                _fail("INVALID_ARGUMENT")
            parent._live()
            paths, protected = [selected], 0
        self._path, self._base, self._parent = selected, selected_base, parent
        self._deadline_token = token
        self._paths, self._protected_index = tuple(paths), protected
        self._identities = [None] * len(paths)
        # Each resource slot exists before any resource-producing call.
        self._handles = [[None, _EMPTY] for _ in paths[protected:]]
        self._native_ids = [None] * len(self._handles)
        self._phase, self._close_failure = _NEW, None
        self._enter_returned = self._exit_attempted = False
        self._receipt_failed = self._normal_exit = False
        _clock(token)

    @property
    def close_receipt(self):
        # No live check, clock, native probe or lower-owner property access.
        return DirectoryCloseReceiptV1(
            1, "directory", "native_owner_close_facts_only", self._phase,
            self._enter_returned, self._exit_attempted, self._receipt_failed,
            self._normal_exit, self._parent is not None,
            tuple(slot[1] for slot in self._handles), tuple(self._native_ids))

    @property
    def path(self):
        return self._path

    @property
    def base(self):
        return self._base

    @property
    def parent(self):
        return self._parent

    @property
    def deadline_token(self):
        return self._deadline_token

    def _live(self):
        if self._close_failure is not None:
            raise self._close_failure
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        if self.parent is not None:
            self.parent._live()
        _clock(self.deadline_token)

    def __enter__(self):
        if self._close_failure is not None:
            raise self._close_failure
        if self._phase != _NEW:
            _fail("CLOSED")
        try:
            _clock(self.deadline_token)
            if self.parent is not None:
                self.parent.verify()
            for index, path in enumerate(self._paths):
                before = _path_stat(path, self.deadline_token)
                safe._safe_stat(before, directory=True)
                self._identities[index] = (before.st_dev, before.st_ino)
                if index < self._protected_index:
                    continue
                slot_index = index - self._protected_index
                slot = self._handles[slot_index]
                native = _native(path, self.deadline_token)
                _clock(self.deadline_token)
                handle = _create_file(native, 0x1, 3, None, 3,
                                      0x00200000 | 0x02000000, None)
                if handle == _INVALID_HANDLE:
                    number = ctypes.get_last_error()
                    raise ctypes.WinError(number)
                slot[0], slot[1] = handle, _OWNED
                _clock(self.deadline_token)
                info = _information(handle, self.deadline_token)
                _native_type(info, directory=True)
                self._native_ids[slot_index] = _native_identity(info)
                after = _path_stat(path, self.deadline_token)
                safe._safe_stat(after, directory=True)
                if (after.st_dev, after.st_ino) != self._identities[index]:
                    safe._error("Media directory changed while it was opened")
            # Ordinary verify requires ACTIVE, but no caller has received self.
            self._phase = _ACTIVE
            self.verify()
            _clock(self.deadline_token)
            self._enter_returned = True
            return self
        except BaseException as primary:
            self._finish(primary)
            raise

    def verify(self):
        self._live()
        if self.parent is not None:
            self.parent.verify()
        for path, expected in zip(self._paths, self._identities):
            current = _path_stat(path, self.deadline_token)
            safe._safe_stat(current, directory=True)
            if (current.st_dev, current.st_ino) != expected:
                safe._error("Media directory changed during the operation")
        for slot, expected in zip(self._handles, self._native_ids):
            if slot[1] != _OWNED:
                _fail("CLOSED")
            info = _information(slot[0], self.deadline_token)
            _native_type(info, directory=True)
            if _native_identity(info) != expected:
                safe._error("Media directory handle changed during the operation")
        _clock(self.deadline_token)

    def entry_stat(self, name):
        self._live()
        safe._entry_name(name)
        result = _path_stat(self.path / name, self.deadline_token)
        _clock(self.deadline_token)
        return result

    def open_file(self, name, *, write=False, create=False, lock=False):
        _false(write, create, lock)
        self._live()
        safe._entry_name(name)
        lease = DeadlineReadonlyLease(self, name)
        _clock(self.deadline_token)
        return lease

    def _cleanup(self):
        self._phase = _RETIRED
        first = self._close_failure
        for slot in reversed(self._handles):
            if slot[1] != _OWNED:
                continue
            slot[1] = _ATTEMPTED
            try:
                _close_owned_handle(slot[0])
            except BaseException as failure:
                slot[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                slot[1] = _CLOSED
        self._close_failure = first
        return first

    def _finish(self, primary):
        first_exit = not self._exit_attempted
        was_active = self._phase == _ACTIVE
        self._exit_attempted = True
        first = self._cleanup()
        if primary is not None:
            self._receipt_failed, self._normal_exit = True, False
            if first is not None:
                _note(primary)
            return
        if first is not None:
            self._receipt_failed, self._normal_exit = True, False
            raise first
        try:
            _clock(self.deadline_token)
        except BaseException:
            self._receipt_failed, self._normal_exit = True, False
            raise
        if (first_exit and was_active and self._enter_returned
                and not self._receipt_failed):
            self._normal_exit = True

    def __exit__(self, _kind, primary, _tb):
        self._finish(primary)
        return None


class DeadlineReadonlyLease:
    """Own numeric FD; the exact reader/raw wrappers never own numeric close."""
    __slots__ = ("_directory", "_name", "_phase", "_handle", "_fd", "_raw",
                 "_buffer", "_close_failure", "_transfer_failure",
                 "_transfer_unknown", "_native_file_identity", "_enter_returned",
                 "_exit_attempted", "_receipt_failed", "_normal_exit",
                 "_enrollment_mode", "__weakref__")

    def __init__(self, directory, name):
        if type(self) is not DeadlineReadonlyLease or type(directory) is not DeadlineDirectory:
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._reject_factory_reinit(self)
        directory._live()
        safe._entry_name(name)
        self._enrollment_mode = None
        _initialize_readonly_lease(self, directory, name)

    @property
    def close_receipt(self):
        if self._transfer_unknown:
            transfer = "unknown"
        elif self._handle[1] == _TRANSFERRED:
            transfer = _TRANSFERRED
        elif self._handle[1] == _TRANSFER_PENDING:
            transfer = _TRANSFER_PENDING
        else:
            transfer = "not_started"
        return ReadonlyCloseReceiptV1(
            1, "readonly", "native_owner_close_facts_only", self._phase,
            self._enter_returned, self._exit_attempted, self._receipt_failed,
            self._normal_exit, True, self._handle[1], self._fd[1],
            self._raw[1], self._buffer[1], transfer, self._native_file_identity)

    def __enter__(self):
        if self._close_failure is not None:
            raise self._close_failure
        if self._transfer_failure is not None:
            raise self._transfer_failure
        if self._phase != _NEW:
            _fail("CLOSED")
        pin = _enrollment_factory_module()._require_factory_owner_live(self, "node_readonly")
        directory = self._directory
        token = directory.deadline_token
        try:
            directory._live()
            directory.verify()
            before = directory.entry_stat(self._name)
            before_identity = _file_identity(before, path=True)
            path = directory.path / self._name
            native = _native(path, token)
            _clock(token)
            handle = _create_file(native, 0x80000000, 1, None, 3, 0x00200000, None)
            if handle == _INVALID_HANDLE:
                number = ctypes.get_last_error()
                raise ctypes.WinError(number)
            self._handle[0], self._handle[1] = handle, _OWNED
            _clock(token)
            info = _information(handle, token)
            _native_type(info, directory=False)
            self._native_file_identity = _native_identity(info)
            after = directory.entry_stat(self._name)
            if _file_identity(after, path=True) != before_identity:
                safe._error("Media file changed while it was opened")
            flags = os.O_BINARY | os.O_RDONLY
            _clock(token)
            self._handle[1] = _TRANSFER_PENDING
            try:
                fd = _open_osfhandle(handle, flags)
                if type(fd) is not int or fd < 0:
                    _fail("INVALID_ARGUMENT")
            except BaseException as primary:
                # An actual FD return is the only permitted transfer oracle.
                # Never probe/guess/CloseHandle after an opaque no-return.
                self._handle[1] = _UNKNOWN
                self._transfer_unknown = True
                self._transfer_failure = primary
                _note(primary, "DIRECTORY_DEADLINE_TRANSFER_UNKNOWN")
                raise
            self._fd[0], self._fd[1] = fd, _OWNED
            self._handle[1] = _TRANSFERRED
            _clock(token)
            _clock(token)
            raw = _file_io(fd, "rb", closefd=False)
            self._raw[0], self._raw[1] = raw, _OWNED
            _clock(token)
            if type(raw) is not io.FileIO:
                _fail("INVALID_ARGUMENT")
            _clock(token)
            buffer = _buffered_reader(raw)
            self._buffer[0], self._buffer[1] = buffer, _OWNED
            _clock(token)
            if type(buffer) is not io.BufferedReader or buffer.raw is not raw:
                _fail("INVALID_ARGUMENT")
            _clock(token)
            actual = _fstat(fd)
            _clock(token)
            if _file_identity(actual, path=False) != before_identity[:-1]:
                safe._error("Media file handle changed while it was opened")
            directory.verify()
            _clock(token)
            self._phase = _ACTIVE
            if pin is not None:
                _enrollment_factory_module()._mark_enter_owed(self)
            self._enter_returned = True
            return buffer
        except BaseException as primary:
            self._finish(primary)
            raise

    def _cleanup(self):
        pin, identity_failure = _enrollment_cleanup_identity(self)
        failure = _enrollment_cleanup_failure(self)
        if identity_failure is not None:
            failure = identity_failure
        self._phase = _RETIRED
        first = self._close_failure
        if first is None and failure is not None:
            first = failure
        elif failure is not None and failure is not first:
            _note(first)
        if pin is None and identity_failure is not None:
            # This owner/parent is unconfirmed: retain UNKNOWN, never blind close.
            for slot in (self._buffer, self._raw, self._fd, self._handle):
                if type(slot) is list and len(slot) == 2 and slot[1] == _OWNED:
                    slot[1] = _UNKNOWN
            self._close_failure = first
            self._receipt_failed, self._normal_exit = True, False
            return first
        if self._buffer[1] == _OWNED:
            self._buffer[1] = _ATTEMPTED
            try:
                _closed_none(_retire_buffer(self._buffer[0]))
            except BaseException as failure:
                self._buffer[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._buffer[1] = _CLOSED
        if self._raw[1] == _OWNED:
            self._raw[1] = _ATTEMPTED
            try:
                if type(self._raw[0]) is not io.FileIO:
                    _fail("INVALID_RESULT")
                if not self._raw[0].closed:
                    _closed_none(_retire_raw(self._raw[0]))
            except BaseException as failure:
                self._raw[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._raw[1] = _CLOSED
        if self._fd[1] == _OWNED:
            self._fd[1] = _ATTEMPTED
            try:
                _closed_none(_close_fd(self._fd[0]))
            except BaseException as failure:
                self._fd[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._fd[1] = _CLOSED
        if self._handle[1] == _OWNED:
            self._handle[1] = _ATTEMPTED
            try:
                _close_owned_handle(self._handle[0])
            except BaseException as failure:
                self._handle[1] = _UNKNOWN
                if first is None:
                    first = failure
            else:
                self._handle[1] = _CLOSED
        self._close_failure = first
        return first

    def _finish(self, primary):
        first_exit = not self._exit_attempted
        was_active = self._phase == _ACTIVE
        self._exit_attempted = True
        first = self._cleanup()
        if primary is not None:
            self._receipt_failed, self._normal_exit = True, False
            if first is not None:
                _note(primary)
            return
        if first is not None:
            self._receipt_failed, self._normal_exit = True, False
            raise first
        if self._transfer_failure is not None:
            self._receipt_failed, self._normal_exit = True, False
            raise self._transfer_failure
        try:
            pin = _enrollment_factory_module()._require_factory_owner(self, "node_readonly")
            token = self._directory.deadline_token if pin is None else pin.binding.effective_token
            _clock(token)
        except BaseException:
            self._receipt_failed, self._normal_exit = True, False
            raise
        if (first_exit and was_active and self._enter_returned
                and not self._receipt_failed):
            self._normal_exit = True

    def __exit__(self, _kind, primary, _tb):
        pin, failure = _enrollment_cleanup_identity(self)
        if failure is not None:
            try:
                if self._close_failure is None:
                    self._close_failure = failure
                elif failure is not self._close_failure:
                    _note(self._close_failure)
                self._receipt_failed, self._normal_exit = True, False
            except BaseException:
                _preserve_readonly_exit_failure(self, primary, failure)
            if pin is None:
                _preserve_readonly_exit_failure(self, primary, failure)
        if pin is not None:
            try:
                claim = _enrollment_factory_module()._reserve_owner_exit(self)
                if claim is None:
                    _fail("CLOSED")
                return _exit_enrollment_readonly(self, _kind, primary, _tb, claim=claim)
            except BaseException as failure:
                _preserve_readonly_exit_failure(self, primary, failure)
        self._finish(primary)
        return None


def _enrollment_factory_module():
    from . import personal_authority_enrollment_physical as physical
    return physical


def _enrollment_cleanup_failure(owner):
    # A metadata/call-layer failure must not skip independent known ledger work.
    try:
        return _enrollment_factory_module()._begin_owner_cleanup(owner)
    except BaseException as failure:
        return failure


def _enrollment_cleanup_identity(owner):
    try:
        return _enrollment_factory_module()._factory_cleanup_pin(owner)
    except BaseException as failure:
        return None, failure


def _initialize_readonly_lease(self, directory, name):
    _enrollment_factory_module()._begin_factory_initialization(self, "node_readonly")
    self._directory, self._name, self._phase = directory, name, _NEW
    self._handle, self._fd = [None, _EMPTY], [None, _EMPTY]
    self._raw, self._buffer = [None, _EMPTY], [None, _EMPTY]
    self._close_failure = self._transfer_failure = None
    self._transfer_unknown = False
    self._native_file_identity = None
    self._enter_returned = self._exit_attempted = False
    self._receipt_failed = self._normal_exit = False


def _new_enrollment_readonly_lease(binding):
    physical = _enrollment_factory_module()
    binding = physical._validate_node_binding(binding, live=True)
    owner = object.__new__(DeadlineReadonlyLease)
    pin = physical._register_factory_owner(binding, owner, "node_readonly", parent=binding.facade)
    owner._enrollment_mode = pin
    directory, name = binding.facade, "node-identity.json"
    directory._live()
    safe._entry_name(name)
    _initialize_readonly_lease(owner, directory, name)
    physical._require_factory_owner_live(owner, "node_readonly")
    return owner


def _exit_enrollment_readonly(lease, kind, primary, tb, *, claim):
    try:
        if type(lease) is not DeadlineReadonlyLease:
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._begin_claimed_exit(lease, claim)
        DeadlineReadonlyLease._finish(lease, primary)
        return None
    except BaseException as failure:
        _preserve_readonly_exit_failure(lease, primary, failure)


def _preserve_readonly_exit_failure(owner, primary, failure):
    first = None
    if type(owner) is DeadlineReadonlyLease:
        owner._receipt_failed, owner._normal_exit = True, False
        try:
            first = owner._close_failure
        except AttributeError:
            pass
        if first is None:
            owner._close_failure = failure
            first = failure
        elif failure is not first:
            _note(first)
    if primary is not None:
        if failure is not primary:
            _note(primary)
        raise primary from None
    if first is not None and first is not failure:
        raise first from None
    raise failure
