"""Create-only origin publication at one cooperative absolute deadline.

The caller lends an ACTIVE readonly directory pin and its actual EX. Publication
owns a fresh native parent, a CREATE_NEW writer and an independent share-7 reader.
After the writer closes, a new readonly lease binds stable post-close metadata.
Returned metadata is not custody, enrollment or a source-close witness.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import hashlib
import io
import math
import os
from pathlib import Path
import time
import uuid

from . import personal_authority_origin_reservation_io as ordinary
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_directory_deadline as directories
from . import personal_preservation_files as private
from . import storage_lifecycle as lifecycle, sync_media as safe

OriginReservationIOError = ordinary.OriginReservationIOError
PublishedFile = ordinary.PublishedFile
RESERVATION_NAME = ordinary.RESERVATION_NAME
OBSERVATION_NAME = ordinary.OBSERVATION_NAME
MAX_BYTES = ordinary.MAX_BYTES
_NAMES = frozenset({RESERVATION_NAME, OBSERVATION_NAME})
_WINDOWS = os.name == "nt"
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_POINTER_MAX = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
_NEW, _ACTIVE, _RETIRED = "new", "active", "retired"
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN, _TRANSFERRED = "closed", "unknown", "transferred"
_TRANSFER_PENDING = "transfer_pending"


def _fail(code):
    raise OriginReservationIOError(code) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ORIGIN_RESERVATION_IO_CLEANUP_FAILED")
    except BaseException:
        pass


# P01-P52 are fixed functional boundaries, with the reviewed signatures.
def _monotonic():
    return time.monotonic()


def _token(value):
    try:
        return deadline._token(value)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _profile(profile):
    return lifecycle._profile(profile)


def _profile_paths(profile):
    return tuple(profile.identity)


def _require_guard(profile, *, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _directory_verify(value):
    return directories.DeadlineDirectory.verify(value)


def _directory_token(value):
    return value.deadline_token


def _directory_path(value):
    return value.path


def _operation(stage_name, name):
    if type(name) is not str or name not in _NAMES or type(stage_name) is not str:
        _fail("INVALID_ARGUMENT")
    prefix, suffix = name + ".", ".stage"
    if not stage_name.startswith(prefix) or not stage_name.endswith(suffix):
        _fail("INVALID_ARGUMENT")
    text = stage_name[len(prefix):-len(suffix)]
    try:
        value = uuid.UUID(text)
    except (ValueError, AttributeError):
        _fail("INVALID_ARGUMENT")
    if str(value) != text or value.int == 0:
        _fail("INVALID_ARGUMENT")


def _native_path(path):
    return safe._native_path(path)


def _path_stat(path):
    return os.stat(safe._native_path(path), follow_symlinks=False)


def _safe_stat(info, *, directory):
    return safe._safe_stat(info, directory=directory)


def _identity(info):
    return private.identity(info)


if _WINDOWS:
    from ctypes import wintypes
    import msvcrt

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID),
                    ("inherit", wintypes.BOOL)]

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    class _IoStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    # These DLL/function objects belong to this module, never the old caches.
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _kernel.GetCurrentProcess.argtypes = []
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel.LocalFree.restype = wintypes.HLOCAL
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HandleInfo)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi.OpenProcessToken.restype = wintypes.BOOL
    _advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _advapi.GetTokenInformation.restype = wintypes.BOOL
    _advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatus),
        wintypes.LPVOID, wintypes.ULONG, ctypes.c_int]
    _ntdll.NtSetInformationFile.restype = wintypes.LONG


def _create_file(path, access, share, descriptor, disposition, flags, template):
    return _kernel.CreateFileW(path, access, share, descriptor, disposition, flags, template)


def _file_information(handle, info):
    return _kernel.GetFileInformationByHandle(handle, info)


def _close_handle(handle):
    return _kernel.CloseHandle(handle)


def _current_process():
    return _kernel.GetCurrentProcess()


def _open_process_token(process, access, token_out):
    return _advapi.OpenProcessToken(process, access, token_out)


def _token_information(token, kind, buffer, length, length_out):
    return _advapi.GetTokenInformation(token, kind, buffer, length, length_out)


def _sid_string(sid, text_out):
    return _advapi.ConvertSidToStringSidW(sid, text_out)


def _security_descriptor(sddl, revision, descriptor_out, length_out):
    return _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, revision, descriptor_out, length_out)


def _local_free(pointer):
    return _kernel.LocalFree(pointer)


def _get_last_error():
    return ctypes.get_last_error()


def _open_osfhandle(handle, flags):
    return msvcrt.open_osfhandle(handle, flags)


def _file_io(fd, mode, *, closefd=False):
    return io.FileIO(fd, mode, closefd=closefd)


def _close_fd(fd):
    return os.close(fd)


def _retire_raw(stream):
    return io.FileIO.close(stream)


def _raw_fileno(stream):
    if type(stream) is io.FileIO:
        return io.FileIO.fileno(stream)
    if type(stream) is io.BufferedReader:
        return io.BufferedReader.fileno(stream)
    _fail("INVALID_RESULT")


def _raw_seek(stream, offset, whence):
    if type(stream) is io.FileIO:
        return io.FileIO.seek(stream, offset, whence)
    if type(stream) is io.BufferedReader:
        return io.BufferedReader.seek(stream, offset, whence)
    _fail("INVALID_RESULT")


def _raw_read(stream, requested):
    if type(stream) is io.FileIO:
        return io.FileIO.read(stream, requested)
    if type(stream) is io.BufferedReader:
        return io.BufferedReader.read(stream, requested)
    _fail("INVALID_RESULT")


def _raw_write(stream, raw):
    return io.FileIO.write(stream, raw)


def _raw_flush(stream):
    return io.FileIO.flush(stream)


def _fsync(fd):
    return os.fsync(fd)


def _fstat(fd):
    return os.fstat(fd)


def _nt_set_information(handle, io_status, info, length, kind):
    return _ntdll.NtSetInformationFile(handle, io_status, info, length, kind)


def _readonly_lease(directory, name):
    return directories.DeadlineDirectory.open_file(directory, name)


def _readonly_enter(value):
    return directories.DeadlineReadonlyLease.__enter__(value)


def _readonly_exit(value, kind, primary, tb):
    return directories.DeadlineReadonlyLease.__exit__(value, kind, primary, tb)


def _parent(profile, borrowed_directory, *, deadline_token):
    return _PublicationParent(profile, borrowed_directory, deadline_token)


def _parent_enter(value):
    return _PublicationParent.__enter__(value)


def _parent_exit(value, kind, primary, tb):
    return _PublicationParent.__exit__(value, kind, primary, tb)


def _parent_verify(value):
    return _PublicationParent.verify(value)


def _parent_entry_stat(value, name):
    return _PublicationParent.entry_stat(value, name)


def _creator(parent, stage_name, *, deadline_token):
    return _CreatorLease(parent, stage_name, deadline_token)


def _creator_enter(value):
    return _CreatorLease.__enter__(value)


def _creator_exit(value, kind, primary, tb):
    return _CreatorLease.__exit__(value, kind, primary, tb)


def _alive_reader(parent, name, *, deadline_token):
    return _AliveReaderLease(parent, name, deadline_token)


def _alive_enter(value):
    return _AliveReaderLease.__enter__(value)


def _alive_exit(value, kind, primary, tb):
    return _AliveReaderLease.__exit__(value, kind, primary, tb)


def _sha256_hex(raw):
    return hashlib.sha256(raw).hexdigest()


def _published_file(raw_sha, byte_size, identity):
    return PublishedFile(raw_sha, byte_size, identity)


def _copy_identity(value):
    return dict(value)


class _FirstState:
    __slots__ = ("token", "first", "first_tb")

    def __init__(self, token):
        self.token, self.first, self.first_tb = token, None, None


def _remember(state, failure, tb=None):
    if state.first is None:
        state.first = failure
        state.first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                          if tb is None else tb)
    elif failure is not state.first:
        _note(state.first)


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.first_tb) from None


def _clock(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("INVALID_RESULT")
        if now >= state.token.absolute_monotonic:
            _fail("DEADLINE")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)


def _cleanup_clock(state):
    try:
        _clock(state)
    except BaseException as failure:
        _remember(state, failure)


def _none(value):
    if value is not None:
        _fail("INVALID_RESULT")


def _fd_value(value):
    if type(value) is not int or value < 0:
        _fail("INVALID_RESULT")


def _handle_value(value):
    if type(value) is not int or not 0 < value < _POINTER_MAX:
        _fail("INVALID_RESULT")


def _stat_value(value):
    if type(value) is not os.stat_result:
        _fail("INVALID_RESULT")


def _pseudo_handle_value(value):
    if type(value) is not int or not 0 < value <= _POINTER_MAX:
        _fail("INVALID_RESULT")


def _paths_value(value):
    if type(value) is not tuple or len(value) != 4 or any(
            type(item) is not str or not item for item in value):
        _fail("INVALID_RESULT")


def _sha_value(value):
    if type(value) is not str or len(value) != 64 or any(
            item not in "0123456789abcdef" for item in value):
        _fail("INVALID_RESULT")


def _exact_value(value, expected_type):
    if type(value) is not expected_type:
        _fail("INVALID_RESULT")


def _same_value(value, expected, code="INVALID_RESULT"):
    if value is not expected:
        _fail(code)


def _identity_value(value):
    if type(value) is not dict or set(value) != {
            "device", "inode", "byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}:
        _fail("INVALID_RESULT")
    if any(type(value[key]) is not str for key in ("device", "inode", "mtime_ns", "ctime_ns")):
        _fail("INVALID_RESULT")
    if (type(value["byte_size"]) is not int or value["byte_size"] < 0
            or type(value["mode"]) is not int or type(value["nlink"]) is not int
            or value["nlink"] != 1):
        _fail("INVALID_RESULT")


def _call(state, operation, /, *args, validate=None, **kwargs):
    _clock(state)
    try:
        value = operation(*args, **kwargs)
        if validate is not None:
            validate(value)
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    _clock(state)
    return value


def _cpu(state, operation, /, *args, validate=None, **kwargs):
    return _call(state, operation, *args, validate=validate, **kwargs)


def _last_error():
    # Deliberately no clock between a failed native result and this E getter.
    number = _get_last_error()
    if type(number) is not int or not 0 <= number <= 0xFFFFFFFF:
        _fail("INVALID_RESULT")
    return number


def _bool_value(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        _last_error()
        _fail("INVALID_RESULT")


def _bool_success(value, message, code=None):
    _bool_value(value)
    if value == 0:
        number = _last_error()
        if code is not None:
            _fail(code)
        raise OSError(errno.EIO, message, None, number)


def _native_information(state, handle, *, directory):
    info = _cpu(state, _HandleInfo)
    address = _cpu(state, ctypes.byref, info)
    def information_result(value):
        _bool_success(value, "Origin reservation information unavailable")
        if info.attributes & safe._REPARSE or bool(info.attributes & 0x10) != directory:
            if directory:
                safe._error("Media paths must not contain links or unexpected file types")
            _fail("FILE_UNSAFE")
        if not directory and info.links != 1:
            _fail("FILE_UNSAFE")

    _call(state, _file_information, handle, address, validate=information_result)
    return info


def _acquire_handle(state, slot, path, access, share, descriptor, disposition, flags,
                    *, failure_code=None):
    _clock(state)
    try:
        handle = _create_file(path, access, share, descriptor, disposition, flags, None)
        if handle == _INVALID_HANDLE and type(handle) is int:
            number = _last_error()
            if failure_code is not None:
                _fail(failure_code)
            raise ctypes.WinError(number)
        try:
            _handle_value(handle)
        except BaseException:
            slot[1] = _UNKNOWN
            raise
        slot[0], slot[1] = handle, _OWNED
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    _clock(state)
    return handle


def _close_slot(state, slot, kind):
    if slot[1] != _OWNED:
        return
    slot[1] = _ATTEMPTED
    _cleanup_clock(state)
    try:
        if kind == "handle":
            result = _close_handle(slot[0])
            _bool_success(result, "Origin reservation native close failed")
        elif kind == "pointer":
            result = _local_free(slot[0])
            if result is not None:
                number = _last_error()
                if type(result) is not int or not 0 < result <= _POINTER_MAX:
                    _fail("INVALID_RESULT")
                raise OSError(errno.EIO, "Origin reservation native free failed", None, number)
        elif kind == "fd":
            _none(_close_fd(slot[0]))
        elif kind == "raw":
            _none(_retire_raw(slot[0]))
        else:
            _fail("INVALID_RESULT")
    except BaseException as failure:
        slot[1] = _UNKNOWN
        _remember(state, failure)
    else:
        slot[1] = _CLOSED
    _cleanup_clock(state)


def _resource_bool(state, operation, args, slot, output, code):
    _clock(state)
    try:
        result = operation(*args)
        _bool_success(result, "Origin reservation private creation unavailable", code)
        value = output()
        _handle_value(value)
        slot[0], slot[1] = value, _OWNED
    except BaseException as failure:
        if slot[1] == _EMPTY:
            slot[1] = _UNKNOWN
        _remember(state, failure)
        _raise_first(state)
    _clock(state)


@contextmanager
def _private_descriptor(state):
    token_slot, text_slot, descriptor_slot = [None, _EMPTY], [None, _EMPTY], [None, _EMPTY]
    try:
        token, length = _cpu(state, lambda: (wintypes.HANDLE(), wintypes.DWORD()))
        process = _call(state, _current_process, validate=_pseudo_handle_value)
        token_address = _cpu(state, ctypes.byref, token)
        _resource_bool(state, _open_process_token, (process, 0x0008, token_address),
                       token_slot, lambda: token.value, "PRIVATE_CREATION_UNAVAILABLE")
        try:
            length_address = _cpu(state, ctypes.byref, length)

            def probe_result(value):
                _bool_value(value)
                if value == 0:
                    _last_error()
                if not 0 < length.value <= 65536:
                    _fail("PRIVATE_CREATION_UNAVAILABLE")

            _call(state, _token_information, token_slot[0], 1, None, 0, length_address,
                  validate=probe_result)
            user = _cpu(state, ctypes.create_string_buffer, length.value)
            _call(state, _token_information, token_slot[0], 1, user, length, length_address,
                  validate=lambda value: _bool_success(value, "Token information unavailable",
                                                       "PRIVATE_CREATION_UNAVAILABLE"))
            sid_pointer = _cpu(state, lambda: ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0])
            text = _cpu(state, wintypes.LPWSTR)
            text_address = _cpu(state, ctypes.byref, text)
            _resource_bool(state, _sid_string, (sid_pointer, text_address), text_slot,
                           lambda: ctypes.cast(text, wintypes.HLOCAL).value,
                           "PRIVATE_CREATION_UNAVAILABLE")
            try:
                def sddl_text():
                    sid = text.value
                    if type(sid) is not str or not sid.startswith("S-1-") or len(sid) > 256:
                        _fail("PRIVATE_CREATION_UNAVAILABLE")
                    return f"O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)"

                sddl = _cpu(state, sddl_text)
            except BaseException as primary:
                _remember(state, primary)
                raise
            finally:
                _close_slot(state, text_slot, "pointer")
            _raise_first(state)
        except BaseException as primary:
            _remember(state, primary)
            raise
        finally:
            _close_slot(state, token_slot, "handle")
        _raise_first(state)
        descriptor = _cpu(state, wintypes.LPVOID)
        address = _cpu(state, ctypes.byref, descriptor)
        _resource_bool(state, _security_descriptor, (sddl, 1, address, None),
                       descriptor_slot, lambda: descriptor.value,
                       "PRIVATE_CREATION_UNAVAILABLE")
        yield descriptor_slot[0]
    except BaseException as primary:
        _remember(state, primary)
        raise
    finally:
        # Reverse acquisition order includes partial paths, not just success.
        _close_slot(state, descriptor_slot, "pointer")
        _close_slot(state, text_slot, "pointer")
        _close_slot(state, token_slot, "handle")
        _raise_first(state)


class _PublicationParent:
    """Fixed existing-parent clone of safe._Directory, with own native slots."""
    __slots__ = ("path", "base", "create", "parent", "_state", "_paths",
                 "_protected", "_identities", "_handles", "_phase")

    def __init__(self, profile, borrowed_directory, token):
        if type(self) is not _PublicationParent:
            _fail("INVALID_RESULT")
        self._state = _FirstState(token)
        # create=False/base=path/parent=None are the fixed reviewed call. Whole
        # original create/parent/POSIX branches stay preserved as ancestry.
        self.path = _cpu(self._state, Path, profile.database.parent)
        self.base, self.create, self.parent = self.path, False, None
        if type(borrowed_directory) is not directories.DeadlineDirectory:
            _fail("INVALID_ARGUMENT")
        if _cpu(self._state, _directory_path, borrowed_directory) != self.path:
            _fail("PROFILE_CHANGED")

        def paths():
            values = [Path(self.path.anchor)]
            for component in self.path.parts[1:]:
                values.append(values[-1] / component)
            return tuple(values)

        self._paths = _cpu(self._state, paths)
        self._protected = _cpu(self._state, self._paths.index, self.base)
        self._identities = [None] * len(self._paths)
        self._handles = [[None, _EMPTY] for _ in self._paths[self._protected:]]
        self._phase = _NEW

    def __enter__(self):
        if self._phase != _NEW:
            _fail("INVALID_RESULT")
        state = self._state
        try:
            _clock(state)
            for index, path in enumerate(self._paths):
                # Existing-parent absence propagates; no mkdir/create fallback.
                before = _call(state, _path_stat, path, validate=_stat_value)
                _cpu(state, _safe_stat, before, directory=True, validate=_none)
                self._identities[index] = (before.st_dev, before.st_ino)
                if index < self._protected:
                    continue
                slot = self._handles[index - self._protected]
                native = _cpu(state, _native_path, path)
                handle = _acquire_handle(state, slot, native, 0x1, 3, None, 3,
                                         0x00200000 | 0x02000000)
                _native_information(state, handle, directory=True)
            self._phase = _ACTIVE
            self.verify()
            _clock(state)
            return self
        except BaseException as primary:
            _remember(state, primary)
            self._finish(primary)
            raise

    def verify(self):
        _clock(self._state)
        if self._phase != _ACTIVE:
            _fail("INVALID_RESULT")
        for path, expected in zip(self._paths, self._identities):
            current = _call(self._state, _path_stat, path, validate=_stat_value)
            _cpu(self._state, _safe_stat, current, directory=True, validate=_none)
            if _cpu(self._state, lambda: (current.st_dev, current.st_ino) != expected):
                safe._error("Media directory changed during the operation")

    def entry_stat(self, name):
        _clock(self._state)
        if self._phase != _ACTIVE or type(name) is not str:
            _fail("INVALID_RESULT")
        _cpu(self._state, safe._entry_name, name)
        # The enclosing target-absence boundary alone accepts FileNotFoundError.
        return _path_stat(self.path / name)

    def _finish(self, primary):
        if primary is not None:
            _remember(self._state, primary)
        self._phase = _RETIRED
        for slot in reversed(self._handles):
            _close_slot(self._state, slot, "handle")
        if primary is None:
            _raise_first(self._state)
            _clock(self._state)

    def __exit__(self, _kind, primary, _tb):
        self._finish(primary)
        return None


class _NativeFileLease:
    """Private common implementation; only the two fixed concrete leases enter."""
    __slots__ = ("_parent", "_name", "_create", "_state", "_phase", "_handle",
                 "_fd", "_raw")

    def __init__(self, parent, name, token, create):
        if type(parent) is not _PublicationParent or parent._phase != _ACTIVE:
            _fail("INVALID_RESULT")
        if token is not parent._state.token:
            _fail("INVALID_ARGUMENT")
        self._parent, self._name, self._create = parent, name, create
        self._state, self._phase = parent._state, _NEW
        self._handle, self._fd, self._raw = [None, _EMPTY], [None, _EMPTY], [None, _EMPTY]

    def _transfer(self):
        state = self._state
        _clock(state)
        self._handle[1] = _TRANSFER_PENDING
        try:
            flags = os.O_BINARY | (os.O_RDWR if self._create else os.O_RDONLY)
            fd = _open_osfhandle(self._handle[0], flags)
            _fd_value(fd)
            self._fd[0], self._fd[1] = fd, _OWNED
            self._handle[1] = _TRANSFERRED
        except BaseException as failure:
            self._handle[1] = _UNKNOWN
            _remember(state, failure)
            _raise_first(state)
        _clock(state)

    def _open_raw(self):
        state = self._state
        _clock(state)
        try:
            stream = _file_io(self._fd[0], "r+b" if self._create else "rb", closefd=False)
            # This getter is part of the R legality gate before its postclock;
            # the known FD remains owned if the wrapper output is lost/illegal.
            if (type(stream) is not io.FileIO or stream.closed is not False
                    or stream.closefd is not False):
                _fail("INVALID_RESULT")
            returned_fd = _raw_fileno(stream)
            _fd_value(returned_fd)
            if returned_fd != self._fd[0]:
                _fail("INVALID_RESULT")
            self._raw[0], self._raw[1] = stream, _OWNED
        except BaseException as failure:
            self._raw[1] = _UNKNOWN
            _remember(state, failure)
            _raise_first(state)
        _clock(state)

    def __enter__(self):
        if type(self) not in (_CreatorLease, _AliveReaderLease) or self._phase != _NEW:
            _fail("INVALID_RESULT")
        state = self._state
        try:
            _clock(state)
            native = _cpu(state, _native_path, self._parent.path / self._name)
            if self._create:
                with _private_descriptor(state) as descriptor:
                    attributes = _cpu(state, _SecurityAttributes,
                                      ctypes.sizeof(_SecurityAttributes), descriptor, False)
                    address = _cpu(state, ctypes.byref, attributes)
                    handle = _acquire_handle(state, self._handle, native,
                        0xC0030000, 1, address, 1, 0x00200000,
                        failure_code="STAGE_CREATE_FAILED")
                    _native_information(state, handle, directory=False)
            else:
                handle = _acquire_handle(state, self._handle, native,
                    0x80000000, 7, None, 3, 0x00200000,
                    failure_code="READBACK_UNAVAILABLE")
                _native_information(state, handle, directory=False)
            self._transfer()
            self._open_raw()
            self._phase = _ACTIVE
            _clock(state)
            return self._raw[0]
        except BaseException as primary:
            _remember(state, primary)
            self._finish(primary)
            raise

    def _finish(self, primary):
        if primary is not None:
            _remember(self._state, primary)
        self._phase = _RETIRED
        _close_slot(self._state, self._raw, "raw")
        _close_slot(self._state, self._fd, "fd")
        _close_slot(self._state, self._handle, "handle")
        if primary is None:
            _raise_first(self._state)
            _clock(self._state)

    def __exit__(self, _kind, primary, _tb):
        self._finish(primary)
        return None


class _CreatorLease(_NativeFileLease):
    __slots__ = ()

    def __init__(self, parent, stage_name, token):
        if type(self) is not _CreatorLease:
            _fail("INVALID_RESULT")
        super().__init__(parent, stage_name, token, True)


class _AliveReaderLease(_NativeFileLease):
    __slots__ = ()

    def __init__(self, parent, name, token):
        if type(self) is not _AliveReaderLease:
            _fail("INVALID_RESULT")
        super().__init__(parent, name, token, False)


def _file_stream(value):
    if type(value) is not io.FileIO or value.closed is not False or value.closefd is not False:
        _fail("INVALID_RESULT")


def _readonly_stream(value):
    if type(value) is not io.BufferedReader or value.closed is not False:
        _fail("INVALID_RESULT")


def _native_file_result(value, lease):
    _file_stream(value)
    if value is not lease._raw[0] or lease._fd[1] != _OWNED:
        _fail("INVALID_RESULT")


@contextmanager
def _scope(state, context, enter, leave, validate):
    # Prepared objects do not own resources. Only a legal enter return adopts.
    slot = [context, _EMPTY]

    def finish():
        if slot[1] != _OWNED:
            return
        slot[1] = _ATTEMPTED
        _cleanup_clock(state)
        primary = state.first
        try:
            _none(leave(context, type(primary) if primary is not None else None,
                        primary, state.first_tb))
        except BaseException as failure:
            slot[1] = _UNKNOWN
            _remember(state, failure)
        else:
            slot[1] = _CLOSED
        _cleanup_clock(state)

    try:
        _clock(state)
        value = enter(context)
        validate(value)
        slot[1] = _OWNED
        _clock(state)
        yield value
    except BaseException as primary:
        _remember(state, primary)
        finish()
        _raise_first(state)
    else:
        finish()
        _raise_first(state)


def _same_file(left, right):
    return all(left[key] == right[key] for key in ("device", "inode", "byte_size", "mtime_ns"))


def _file_identity(state, stream, expected_fd):
    def fd_result(value):
        _fd_value(value)
        if value != expected_fd:
            _fail("INVALID_RESULT")

    fd = _call(state, _raw_fileno, stream, validate=fd_result)
    info = _call(state, _fstat, fd, validate=_stat_value)
    return _cpu(state, _identity, info, validate=_identity_value)


def _target_absent(state, parent, name):
    _clock(state)
    try:
        value = _parent_entry_stat(parent, name)
    except FileNotFoundError:
        _clock(state)
        return
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    try:
        _stat_value(value)
        _fail("TARGET_EXISTS")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)


def _write_all(state, stream, raw):
    offset = 0
    while offset < len(raw):
        _clock(state)
        piece = _cpu(state, lambda: raw[offset:offset + 8192])

        def written_result(written):
            nonlocal offset
            if type(written) is not int or written <= 0 or written > min(8192, len(raw) - offset):
                _fail("WRITE_INCOMPLETE")
            offset += written

        _call(state, _raw_write, stream, piece, validate=written_result)
    _clock(state)


def _read_exact(state, stream, raw):
    position = _call(state, _raw_seek, stream, 0, os.SEEK_SET, validate=_fd_value)
    if position != 0:
        _fail("READBACK_CHANGED")
    offset = 0
    while offset < len(raw):
        _clock(state)
        request = min(8192, len(raw) - offset)

        def read_result(chunk):
            nonlocal offset
            if (type(chunk) is not bytes or not chunk or len(chunk) > request
                    or chunk != raw[offset:offset + len(chunk)]):
                _fail("READBACK_CHANGED")
            offset += len(chunk)

        _call(state, _raw_read, stream, request, validate=read_result)
    _clock(state)

    def eof_result(chunk):
        if type(chunk) is not bytes or chunk != b"":
            _fail("READBACK_CHANGED")

    _call(state, _raw_read, stream, 1, validate=eof_result)
    _clock(state)


def _publish_owned(state, parent, creator, name):
    # Original HANDLE is a borrowed value of this known CRT-owned FD. No
    # get_osfhandle/probe reconstructs an UNKNOWN transfer or caller custody.
    if creator._fd[1] != _OWNED or creator._handle[1] != _TRANSFERRED:
        _fail("INVALID_RESULT")
    if parent._phase != _ACTIVE or parent._handles[-1][1] != _OWNED:
        _fail("INVALID_RESULT")
    encoded = _cpu(state, str.encode, name, "utf-16-le")

    def rename_info():
        class _RenameInfo(ctypes.Structure):
            _fields_ = [("replace", wintypes.BOOLEAN), ("directory", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", wintypes.WCHAR * len(name))]

        return _RenameInfo(False, parent._handles[-1][0], len(encoded), name)

    value = _cpu(state, rename_info)
    status = _cpu(state, _IoStatus)
    status_address = _cpu(state, ctypes.byref, status)
    value_address = _cpu(state, ctypes.byref, value)
    length = _cpu(state, ctypes.sizeof, value)

    def rename_result(result):
        if type(result) is not int or not -(1 << 31) <= result < (1 << 31):
            _fail("INVALID_RESULT")
        if result != 0:
            _fail("PUBLISH_UNAVAILABLE")
        completion = status.status
        if completion is None:
            completion = 0
        if type(completion) is not int or not 0 <= completion <= _POINTER_MAX:
            _fail("INVALID_RESULT")
        if completion & 0xFFFFFFFF:
            _fail("PUBLISH_UNAVAILABLE")

    _call(state, _nt_set_information, creator._handle[0], status_address,
          value_address, length, 10, validate=rename_result)


def _independent_readback(state, parent, name, stream, raw, creator_fd):
    _clock(state)
    lease = _call(state, _alive_reader, parent, name, deadline_token=state.token,
                  validate=lambda value: _exact_value(value, _AliveReaderLease))
    with _scope(state, lease, _alive_enter, _alive_exit,
                lambda value: _native_file_result(value, lease)) as other:
        expected = _file_identity(state, stream, creator_fd)
        if not _cpu(state, _same_file, expected, _file_identity(state, other, lease._fd[0])):
            _fail("READBACK_CHANGED")
        _read_exact(state, other, raw)
        if not _cpu(state, _same_file, expected, _file_identity(state, other, lease._fd[0])):
            _fail("READBACK_CHANGED")
    _clock(state)


def _post_close_readback(state, directory, parent, name, original, raw):
    _clock(state)
    lease = _call(state, _readonly_lease, directory, name,
                  validate=lambda value: _exact_value(value, directories.DeadlineReadonlyLease))
    with _scope(state, lease, _readonly_enter, _readonly_exit, _readonly_stream) as stream:
        delivered_fd = _call(state, _raw_fileno, stream, validate=_fd_value)
        current = _file_identity(state, stream, delivered_fd)
        if _cpu(state, lambda: any(current[key] != original[key]
                                   for key in ("device", "inode", "byte_size"))):
            _fail("READBACK_CHANGED")
        _read_exact(state, stream, raw)
        final = _file_identity(state, stream, delivered_fd)
        if _cpu(state, lambda: current != final):
            _fail("READBACK_CHANGED")
        info = _call(state, _parent_entry_stat, parent, name, validate=_stat_value)
        path_identity = _cpu(state, _identity, info, validate=_identity_value)
        if not _cpu(state, _same_file, final, path_identity):
            _fail("READBACK_CHANGED")
    return final


def _same_profile(state, profile, paths, guard):
    def paths_result(value):
        _paths_value(value)
        if value != paths:
            _fail("PROFILE_CHANGED")

    _cpu(state, _profile_paths, profile, validate=paths_result)
    _call(state, _require_guard, profile, exclusive=True,
          validate=lambda value: _same_value(value, guard, "PROFILE_CHANGED"))


def publish_new_locked(profile, directory, name, stage_name, raw, *, deadline_token):
    """Publish exactly one fixed new anchor; no caller callback or writer grant."""
    token = _token(deadline_token)
    if type(token) is not deadline.Namespace3Deadline or token is not deadline_token:
        _fail("INVALID_RESULT")
    try:
        deadline._token(token)
    except deadline.Namespace3Error:
        _fail("INVALID_RESULT")
    state = _FirstState(token)
    _clock(state)
    try:
        if type(profile) is not lifecycle.StorageProfile:
            _fail("INVALID_ARGUMENT")
        _cpu(state, _operation, stage_name, name, validate=_none)
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
            _fail("INVALID_ARGUMENT")
        if not _WINDOWS:
            _fail("UNSUPPORTED")
        _cpu(state, _profile, profile,
             validate=lambda value: _same_value(value, profile))
        paths = _cpu(state, _profile_paths, profile, validate=_paths_value)
        guard = _call(state, _require_guard, profile, exclusive=True,
                      validate=lambda value: _exact_value(value, lifecycle.StorageGuard))
        if type(directory) is not directories.DeadlineDirectory:
            _fail("PROFILE_CHANGED")
        _cpu(state, _directory_token, directory,
             validate=lambda value: _same_value(value, token, "PROFILE_CHANGED"))

        def directory_path_result(value):
            expected = profile.database.parent
            if type(value) is not type(expected) or value != expected:
                _fail("PROFILE_CHANGED")

        _cpu(state, _directory_path, directory, validate=directory_path_result)
        _call(state, _directory_verify, directory, validate=_none)
        _same_profile(state, profile, paths, guard)
        parent = _call(state, _parent, profile, directory, deadline_token=token,
                       validate=lambda value: _exact_value(value, _PublicationParent))

        def parent_result(value):
            if type(value) is not _PublicationParent or value is not parent:
                _fail("INVALID_RESULT")

        with _scope(state, parent, _parent_enter, _parent_exit, parent_result):
            _clock(state)
            _same_profile(state, profile, paths, guard)
            _target_absent(state, parent, name)
            creator = _call(state, _creator, parent, stage_name, deadline_token=token,
                            validate=lambda value: _exact_value(value, _CreatorLease))
            with _scope(state, creator, _creator_enter, _creator_exit,
                        lambda value: _native_file_result(value, creator)) as stream:
                original = _file_identity(state, stream, creator._fd[0])
                if original["byte_size"] != 0:
                    _fail("STAGE_CREATE_FAILED")
                _write_all(state, stream, raw)
                _call(state, _raw_flush, stream, validate=_none)
                def creator_fd_result(value):
                    _fd_value(value)
                    if value != creator._fd[0]:
                        _fail("INVALID_RESULT")

                fd = _call(state, _raw_fileno, stream, validate=creator_fd_result)
                _call(state, _fsync, fd, validate=_none)
                _clock(state)
                _read_exact(state, stream, raw)
                written = _file_identity(state, stream, creator._fd[0])
                if _cpu(state, lambda: (original["device"], original["inode"])
                                       != (written["device"], written["inode"])):
                    _fail("READBACK_CHANGED")
                if written["byte_size"] != len(raw):
                    _fail("READBACK_CHANGED")
                _call(state, _parent_verify, parent, validate=_none)
                _clock(state)
                _publish_owned(state, parent, creator, name)
                _clock(state)
                _read_exact(state, stream, raw)
                _independent_readback(state, parent, name, stream, raw, creator._fd[0])
                final = _file_identity(state, stream, creator._fd[0])
                if not _cpu(state, _same_file, written, final):
                    _fail("READBACK_CHANGED")
                info = _call(state, _parent_entry_stat, parent, name, validate=_stat_value)
                path_identity = _cpu(state, _identity, info, validate=_identity_value)
                if not _cpu(state, _same_file, final, path_identity):
                    _fail("READBACK_CHANGED")
            _clock(state)
            closed_identity = _post_close_readback(state, directory, parent, name, final, raw)
            copied_identity = _cpu(state, _copy_identity, closed_identity, validate=_identity_value)
            raw_sha = _cpu(state, _sha256_hex, raw, validate=_sha_value)
            def published_result(value):
                if (type(value) is not PublishedFile or value.raw_sha256 != raw_sha
                        or type(value.byte_size) is not int or value.byte_size != len(raw)
                        or type(value.identity) is not dict or value.identity != copied_identity):
                    _fail("INVALID_RESULT")

            result = _cpu(state, _published_file, raw_sha, len(raw), copied_identity,
                          validate=published_result)
            _call(state, _parent_verify, parent, validate=_none)
            _same_profile(state, profile, paths, guard)
            _call(state, _directory_verify, directory, validate=_none)
            _clock(state)
        _clock(state)
        return result
    except BaseException as primary:
        _remember(state, primary)
        _raise_first(state)
