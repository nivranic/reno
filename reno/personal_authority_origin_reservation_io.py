"""Create-only private origin anchors, retaining every unknown failure artifact.

The creator FD survives write, fsync, full readback and no-replace publication.
This module owns a fresh actual parent directory under the caller's EX. No
caller directory handle, returned metadata or stored anchor is a writer grant.
No existing ACL is inspected/repaired and no entry is removed on failure.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import os
import uuid

from . import personal_preservation_files as private
from . import storage_lifecycle as lifecycle, sync_media as safe

RESERVATION_NAME = ".reno-personal-authority-origin-v1-reservation.json"
OBSERVATION_NAME = ".reno-personal-authority-origin-v1-observation.json"
MAX_BYTES = 64 * 1024
_NAMES = frozenset({RESERVATION_NAME, OBSERVATION_NAME})


class OriginReservationIOError(ValueError):
    def __init__(self, code):
        self.code = "ORIGIN_RESERVATION_IO_" + code
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class PublishedFile:
    raw_sha256: str
    byte_size: int
    identity: dict


def _fail(code):
    raise OriginReservationIOError(code) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ORIGIN_RESERVATION_IO_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    item = context.__enter__()
    try:
        yield item
    except BaseException as primary:
        try:
            context.__exit__(type(primary), primary,
                BaseException.__dict__["__traceback__"].__get__(primary))
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary)
        raise
    else:
        context.__exit__(None, None, None)


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


def _file_identity(stream):
    return private.identity(os.fstat(stream.fileno()))


def _same_file(left, right):
    return all(left[key] == right[key] for key in ("device", "inode", "byte_size", "mtime_ns"))


if os.name == "nt":
    from ctypes import wintypes
    import msvcrt

    # Own DLL function objects; do not change other modules' cached ABI.
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID),
                    ("inherit", wintypes.BOOL)]

    _kernel.GetCurrentProcess.argtypes = []
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel.LocalFree.restype = wintypes.HLOCAL
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(safe._HandleInfo)]
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
    _ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(safe._IoStatus),
        wintypes.LPVOID, wintypes.ULONG, ctypes.c_int]
    _ntdll.NtSetInformationFile.restype = wintypes.LONG

    def _close_native(handle):
        if not _kernel.CloseHandle(handle):
            number = ctypes.get_last_error()
            raise OSError(errno.EIO, "Origin reservation native close failed", None, number)

    def _free_native(pointer):
        if _kernel.LocalFree(pointer):
            number = ctypes.get_last_error()
            raise OSError(errno.EIO, "Origin reservation native free failed", None, number)

    @contextmanager
    def _native_owner(value, closer):
        try:
            yield value
        except BaseException as primary:
            try:
                closer(value)
            except BaseException:
                _note(primary)
            raise
        else:
            closer(value)

    @contextmanager
    def _private_descriptor():
        token, length = wintypes.HANDLE(), wintypes.DWORD()
        if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            _fail("PRIVATE_CREATION_UNAVAILABLE")
        with _native_owner(token, _close_native):
            _advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not 0 < length.value <= 65536:
                _fail("PRIVATE_CREATION_UNAVAILABLE")
            user = ctypes.create_string_buffer(length.value)
            if not _advapi.GetTokenInformation(token, 1, user, length, ctypes.byref(length)):
                _fail("PRIVATE_CREATION_UNAVAILABLE")
            sid_pointer = ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0]
            text = wintypes.LPWSTR()
            if not _advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
                _fail("PRIVATE_CREATION_UNAVAILABLE")
            with _native_owner(ctypes.cast(text, wintypes.HLOCAL), _free_native):
                sid = text.value
                if type(sid) is not str or not sid.startswith("S-1-") or len(sid) > 256:
                    _fail("PRIVATE_CREATION_UNAVAILABLE")
                sddl = f"O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)"
        descriptor = wintypes.LPVOID()
        if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            _fail("PRIVATE_CREATION_UNAVAILABLE")
        with _native_owner(descriptor, _free_native):
            yield descriptor

    def _checked_handle(handle):
        info = safe._HandleInfo()
        if (not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                or info.attributes & (safe._REPARSE | 0x10) or info.links != 1):
            _fail("FILE_UNSAFE")

    def _windows_open(path, *, create):
        handle, fd = None, None
        try:
            if create:
                with _private_descriptor() as descriptor:
                    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
                    handle = _kernel.CreateFileW(safe._native_path(path), 0xC0030000, 1,
                        ctypes.byref(attributes), 1, 0x00200000, None)
                    if handle == ctypes.c_void_p(-1).value:
                        handle = None
                        _fail("STAGE_CREATE_FAILED")
                    _checked_handle(handle)
            else:
                # The still-live creator denies all other writes/deletes. This
                # independent read handle must share its original RW+DELETE.
                handle = _kernel.CreateFileW(safe._native_path(path), 0x80000000, 7,
                    None, 3, 0x00200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    handle = None
                    _fail("READBACK_UNAVAILABLE")
                _checked_handle(handle)
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY))
            handle = None  # CRT is now the sole owner, even if fdopen fails.
            stream = os.fdopen(fd, "r+b" if create else "rb", buffering=0)
            fd = None
            return stream
        except BaseException as primary:
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException:
                    _note(primary)
            if handle is not None:
                try:
                    _close_native(handle)
                except BaseException:
                    _note(primary)
            raise


def _open_stage(directory, stage_name):
    if os.name == "nt":
        return _windows_open(directory.path / stage_name, create=True)
    fd = os.open(stage_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory.fd)
    try:
        return os.fdopen(fd, "r+b", buffering=0)
    except BaseException as primary:
        try:
            os.close(fd)
        except BaseException:
            _note(primary)
        raise


def _write_all(stream, raw, check):
    offset = 0
    while offset < len(raw):
        check()
        written = stream.write(raw[offset:offset + 8192])
        if type(written) is not int or written <= 0 or written > min(8192, len(raw) - offset):
            _fail("WRITE_INCOMPLETE")
        offset += written
    check()


def _flush(stream):
    stream.flush()
    os.fsync(stream.fileno())


def _read_exact(stream, raw, check):
    stream.seek(0)
    offset = 0
    while offset < len(raw):
        check()
        chunk = stream.read(min(8192, len(raw) - offset))
        if type(chunk) is not bytes or not chunk or chunk != raw[offset:offset + len(chunk)]:
            _fail("READBACK_CHANGED")
        offset += len(chunk)
    check()
    if stream.read(1) != b"":
        _fail("READBACK_CHANGED")
    check()


def _publish_owned(directory, stream, name):
    if os.name == "nt":
        encoded = name.encode("utf-16-le")

        class _RenameInfo(ctypes.Structure):
            _fields_ = [("replace", wintypes.BOOLEAN), ("directory", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", wintypes.WCHAR * len(name))]

        value = _RenameInfo(False, directory.handles[-1], len(encoded), name)
        status = _ntdll.NtSetInformationFile(msvcrt.get_osfhandle(stream.fileno()),
            ctypes.byref(safe._IoStatus()), ctypes.byref(value), ctypes.sizeof(value), 10)
        # Only synchronous STATUS_SUCCESS establishes completion. Pending or
        # any other nonzero NTSTATUS retains unknown artifacts and rejects.
        if status != 0:
            _fail("PUBLISH_UNAVAILABLE")
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        _fail("PUBLISH_UNSUPPORTED")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    source = getattr(stream, "_origin_stage_name", None)
    if type(source) is not str:
        _fail("INVALID_ARGUMENT")
    if rename(directory.fd, os.fsencode(source), directory.fd, os.fsencode(name), 1) != 0:
        _fail("PUBLISH_UNAVAILABLE")
    os.fsync(directory.fd)


def _independent_readback(directory, name, stream, raw, check):
    check()
    if os.name == "nt":
        opened = _windows_open(directory.path / name, create=False)
    else:
        opened = directory.open_file(name)
    with _preserve_primary(opened) as other:
        expected = _file_identity(stream)
        if not _same_file(expected, _file_identity(other)):
            _fail("READBACK_CHANGED")
        _read_exact(other, raw, check)
        if not _same_file(expected, _file_identity(other)):
            _fail("READBACK_CHANGED")
    check()


def _post_close_readback(directory, name, original, raw, check):
    """Observe stable timestamps after the last creator write handle closed.

    Windows can defer last-write timestamp updates until that actual close.
    Require original file identity/size and full raw bytes, then bind current
    read-only handle/path metadata rather than comparing pre-close timestamps.
    """
    check()
    with _preserve_primary(directory.open_file(name)) as stream:
        current = _file_identity(stream)
        if any(current[key] != original[key] for key in ("device", "inode", "byte_size")):
            _fail("READBACK_CHANGED")
        _read_exact(stream, raw, check)
        final = _file_identity(stream)
        if current != final:
            _fail("READBACK_CHANGED")
        if not _same_file(final, private.identity(directory.entry_stat(name))):
            _fail("READBACK_CHANGED")
    return final


def publish_new_locked(profile, directory, name, stage_name, raw, check_cancelled=None):
    """Publish one new immutable anchor, without adopting any existing entry.

    A returned tuple of metadata is not an ownership lease. The factory keeps
    its EX and independently holds the published anchor before continuing.
    """
    _operation(stage_name, name)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        _fail("INVALID_ARGUMENT")
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    callback_primary = None

    def check():
        nonlocal callback_primary
        if callback_primary is not None:
            raise callback_primary
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as primary:
                callback_primary = primary
                raise

    check()
    profile = lifecycle._profile(profile)
    paths = tuple(profile.identity)
    guard = lifecycle.require_guard(profile, exclusive=True)
    parent_path = profile.database.parent
    if type(directory) is not safe._Directory or directory.path != parent_path:
        _fail("PROFILE_CHANGED")
    directory.verify()
    if tuple(profile.identity) != paths or lifecycle.require_guard(profile, exclusive=True) is not guard:
        _fail("PROFILE_CHANGED")
    # Open privately owned native custody. Public caller fields/handles are
    # never used as the directory HANDLE/FD of CreateNew or rename.
    with _preserve_primary(safe._Directory(parent_path)) as parent:
        check()
        if tuple(profile.identity) != paths or lifecycle.require_guard(profile, exclusive=True) is not guard:
            _fail("PROFILE_CHANGED")
        try:
            parent.entry_stat(name)
        except FileNotFoundError:
            pass
        else:
            _fail("TARGET_EXISTS")
        with _preserve_primary(_open_stage(parent, stage_name)) as stream:
            stream._origin_stage_name = stage_name
            original = _file_identity(stream)
            if original["byte_size"] != 0:
                _fail("STAGE_CREATE_FAILED")
            _write_all(stream, raw, check)
            _flush(stream)
            check()
            _read_exact(stream, raw, check)
            written = _file_identity(stream)
            if (original["device"], original["inode"]) != (written["device"], written["inode"]):
                _fail("READBACK_CHANGED")
            if written["byte_size"] != len(raw):
                _fail("READBACK_CHANGED")
            parent.verify()
            check()
            _publish_owned(parent, stream, name)
            check()
            _read_exact(stream, raw, check)
            _independent_readback(parent, name, stream, raw, check)
            final = _file_identity(stream)
            if not _same_file(written, final):
                _fail("READBACK_CHANGED")
            path_identity = private.identity(parent.entry_stat(name))
            if not _same_file(final, path_identity):
                _fail("READBACK_CHANGED")
        check()
        closed_identity = _post_close_readback(parent, name, final, raw, check)
        result = PublishedFile(hashlib.sha256(raw).hexdigest(), len(raw), dict(closed_identity))
        parent.verify()
        if tuple(profile.identity) != paths or lifecycle.require_guard(profile, exclusive=True) is not guard:
            _fail("PROFILE_CHANGED")
        directory.verify()
        check()
    check()
    return result
