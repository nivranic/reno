"""Bounded immutable sync media; authentication and DB transactions live outside.

Uploads serialize on an OS lock, so a crashed writer's recognized .part files
can be removed before reserving temporary space. Unknown files count against
the quota. Directory handles prevent following substituted symlinks/junctions.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
from time import monotonic, sleep
import uuid
from types import MappingProxyType

from .sync_protocol import SyncError, validate_media_descriptor

DEFAULT_MAX_TEMP_BYTES = 1024 * 1024 * 1024
_PART = re.compile(r"[0-9a-f]{32}\.part\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LOCK_NAME = "upload.lock"
_MEDIA_HOLD_SEAL = object()


class _VerifiedMediaHold(dict):
    """Backward-compatible paths plus unforgeable-in-JSON live SHA evidence."""
    def __init__(self, paths, held, root, *, seal):
        from .storage_lifecycle import current_guard
        super().__init__(paths)
        self._seal = seal
        self._active = True
        self._guard = current_guard()
        self._root = Path(root)
        self._entries = MappingProxyType({media["video_id"]: (directory, name, stream, MappingProxyType(dict(media)), identity)
                                          for directory, name, stream, media, identity in held})

    def _identity(self, descriptor, guard):
        if self._seal is not _MEDIA_HOLD_SEAL or not self._active or guard is not self._guard:
            _error("Media verification hold is no longer active", "STORAGE_PROOF_INVALID", 409)
        guard.verify()
        media = validate_media_descriptor(descriptor)
        entry = self._entries.get(media["video_id"])
        if entry is None or dict(entry[3]) != media:
            _error("Media hold belongs to another descriptor", "STORAGE_PROOF_INVALID", 409)
        directory, name, stream, _, identity = entry
        if stream.closed:
            _error("Media verification handle has been closed", "STORAGE_PROOF_INVALID", 409)
        info = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != identity or info.st_nlink != 1:
            _error("Held media changed after SHA verification", "STORAGE_ARTIFACT_CHANGED", 409)
        current = directory.entry_stat(name)
        _safe_stat(current, directory=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != identity[:4]:
            _error("Held media path changed after SHA verification", "STORAGE_ARTIFACT_CHANGED", 409)
        directory.verify()
        return identity
UPLOAD_LOCK_WAIT_SECONDS = 30.0
_LOCK_RETRY_SECONDS = 0.05


def _error(message, code="SYNC_MEDIA_UNSAFE_PATH", status=409):
    raise SyncError(message, code, status)


def _io_error(exc):
    if exc.errno == errno.ELOOP:
        _error("Media paths must not contain links")
    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)) or getattr(exc, "winerror", None) in (39, 112):
        _error("Media storage has insufficient space", "SYNC_MEDIA_STORAGE_FULL", 507)
    _error("Media storage operation failed", "SYNC_MEDIA_STORAGE_ERROR", 503)


def _safe_stat(info, *, directory):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        _error("Media paths must not contain links or reparse points")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        _error("Media path has an unexpected file type")


def _entry_name(name):
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or any(char in name for char in ("/", "\\", ":"))
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        _error("Media entry must be a plain filename")
    return name


def _native_path(path):
    value = os.path.abspath(os.fspath(path))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return ("\\\\?\\UNC\\" + value[2:]) if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def _readable_path(path):
    return Path(_native_path(path)) if os.name == "nt" and len(str(path)) >= 260 else Path(path)


if os.name == "nt":
    from ctypes import wintypes
    import msvcrt

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD), ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HandleInfo)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  wintypes.LPVOID, wintypes.DWORD]
    _kernel.SetFileInformationByHandle.restype = wintypes.BOOL

    class _WinRenameReplaceFlags(ctypes.Union):
        # Modern SDK's classic ReplaceIfExists/Flags union; use class 3 only.
        _fields_ = [("ReplaceIfExists", wintypes.BOOLEAN), ("Flags", wintypes.DWORD)]

    _WinRenameName = wintypes.WCHAR * 1

    class _WinRenameInfo(ctypes.Structure):
        _fields_ = [("Replace", _WinRenameReplaceFlags), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", _WinRenameName)]

    class _IoStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    _nt = ctypes.WinDLL("ntdll")
    _nt.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatus), wintypes.LPVOID,
                                       wintypes.ULONG, ctypes.c_int]
    _nt.NtSetInformationFile.restype = wintypes.LONG
    _nt.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    _nt.RtlNtStatusToDosError.restype = wintypes.ULONG

    def _win_open(path, *, directory=False, write=False, create=False, lock=False, delete=False, _owned_directory_slot=None):
        if _owned_directory_slot is not None:
            if directory is not True or write is not False or create is not False or lock is not False or delete is not False:
                _owned_directory_fail("INVALID_ARGUMENT")
            return _win_open_owned_directory(path, _owned_directory_slot)
        access = 0x1 if directory else (0xC0000000 if write else 0x80000000)  # FILE_LIST_DIRECTORY
        if delete:
            access |= 0x10000
        # Pin directory names without preventing child-entry publication.
        # Files being verified deny writes/deletes; the upload lock permits
        # other contenders. Directory reparse attributes are rechecked too.
        share = 3 if directory or lock else 1
        disposition = 4 if lock else (1 if create else 3)  # OPEN_ALWAYS / CREATE_NEW / OPEN_EXISTING
        flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT / BACKUP_SEMANTICS
        handle = _kernel.CreateFileW(_native_path(path), access, share, None, disposition, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _HandleInfo()
            if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & _REPARSE or bool(info.attributes & 0x10) != directory:
                _error("Media paths must not contain links or unexpected file types")
            if directory:
                return handle
            if info.links != 1:
                _error("Media files must not have external hard links")
            return msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if write else os.O_RDONLY))
        except BaseException:
            _kernel.CloseHandle(handle)
            raise


@dataclass(frozen=True, slots=True)
class OwnedDirectoryCloseReceiptV1:
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    root_ordinal: int
    handle_states: tuple[str]
    native_file_identity: tuple[int, int, int] | None
    directory_identity: tuple[int, int] | None
    create_native_attempted: bool
    create_returned: bool
    create_return_legal: bool
    query_native_attempted: bool
    query_returned: bool
    query_return_legal: bool
    close_consumed: bool
    close_native_attempted: bool
    close_returned: bool
    close_return_legal: bool


_OWNED_DIRECTORY_SEAL = object()


class _OwnedDirectorySlot:
    __slots__ = ("_journal", "_number", "_state", "_create_native_attempted",
        "_create_returned", "_create_return_legal", "_query_native_attempted",
        "_query_returned", "_query_return_legal", "_close_consumed",
        "_close_native_attempted", "_close_returned", "_close_return_legal")

    def __init__(self, *args, **kwargs):
        _owned_directory_fail("INVALID_ARGUMENT")


class _OwnedDirectoryJournal:
    __slots__ = ("_seal", "_binding", "_token", "_root_ordinal", "_owner", "_slot",
        "_phase", "_first", "_first_tb", "_enter_returned", "_exit_attempted",
        "_normal_exit", "_native_file_identity", "_directory_identity")

    def __init__(self, *args, **kwargs):
        _owned_directory_fail("INVALID_ARGUMENT")


def _owned_directory_module():
    # Fixed lazy dependency: Task1 imports both legacy modules itself.
    from . import personal_authority_owned_ex_deadline as owned
    return owned


def _owned_directory_fail(suffix):
    _owned_directory_module()._fail(suffix)


def _owned_directory_clock(token):
    _owned_directory_module()._clock(token)


def _owned_directory_create_file(native):
    return _kernel.CreateFileW(native, 0x1, 3, None, 3, 0x02200000, None)


def _owned_directory_get_info(handle, address):
    return _kernel.GetFileInformationByHandle(handle, address)


def _owned_directory_close_handle(handle):
    return _kernel.CloseHandle(handle)


def _owned_directory_bool(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        _owned_directory_fail("INVALID_RESULT")
    if value == 0:
        raise ctypes.WinError(ctypes.get_last_error())


def _owned_directory_capture(journal, failure, *, traceback=None, cleanup=False):
    if journal._first is None:
        journal._first = failure
        journal._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                             if traceback is None else traceback)
    elif cleanup and failure is not journal._first:
        try:
            BaseException.add_note(journal._first, "OWNED_EX_DIRECTORY_CLEANUP_FAILED")
        except BaseException:
            pass


def _owned_directory_raise(journal):
    if journal._first is not None:
        raise BaseException.with_traceback(journal._first, journal._first_tb) from None


def _new_owned_directory_journal(binding, *, root_ordinal):
    owned = _owned_directory_module()
    if os.name != "nt":
        owned._fail("UNSUPPORTED")
    binding = owned._binding(binding)
    if type(root_ordinal) is not int or not 0 <= root_ordinal < 4:
        owned._fail("INVALID_ARGUMENT")
    journal = object.__new__(_OwnedDirectoryJournal)
    journal._seal, journal._binding = _OWNED_DIRECTORY_SEAL, binding
    journal._token, journal._root_ordinal = binding._effective_token, root_ordinal
    journal._owner, journal._phase = None, "new"
    journal._first = journal._first_tb = None
    journal._native_file_identity = journal._directory_identity = None
    journal._enter_returned = journal._exit_attempted = journal._normal_exit = False
    slot = object.__new__(_OwnedDirectorySlot)
    slot._journal, slot._number, slot._state = journal, None, "empty"
    slot._create_native_attempted = slot._create_returned = slot._create_return_legal = False
    slot._query_native_attempted = slot._query_returned = slot._query_return_legal = False
    slot._close_consumed = slot._close_native_attempted = False
    slot._close_returned = slot._close_return_legal = False
    journal._slot = slot
    return journal


def _owned_directory_journal(value):
    if (type(value) is not _OwnedDirectoryJournal
            or value._seal is not _OWNED_DIRECTORY_SEAL
            or type(value._slot) is not _OwnedDirectorySlot
            or value._slot._journal is not value):
        _owned_directory_fail("INVALID_ARGUMENT")
    binding = _owned_directory_module()._binding(value._binding)
    if (value._token is not binding._effective_token
            or type(value._root_ordinal) is not int or not 0 <= value._root_ordinal < 4):
        _owned_directory_fail("INVALID_ARGUMENT")
    return value


def _owned_directory_claim(owner, journal, *, create, parent):
    journal = _owned_directory_journal(journal)
    if (os.name != "nt" or type(owner) is not _Directory
            or create is not False or parent is not None
            or journal._owner is not None or journal._phase != "new"):
        _owned_directory_fail("INVALID_ARGUMENT")
    # Register before Path conversion or any possible native acquisition.
    journal._owner = owner
    owner._owned_close_journal = journal
    return journal


def _owned_directory_begin(owner, journal):
    _owned_directory_raise(journal)
    if (journal._owner is not owner or journal._phase != "new"
            or owner.create is not False or owner.parent is not None
            or owner.base != owner.path):
        _owned_directory_fail("CLOSED")
    journal._phase = "opening"
    try:
        _owned_directory_journal(journal)
        _owned_directory_clock(journal._token)
    except BaseException as failure:
        _owned_directory_capture(journal, failure)
        journal._phase = "retired"
        _owned_directory_raise(journal)


def _owned_directory_cleanup_clock(journal):
    try:
        _owned_directory_clock(journal._token)
    except BaseException as failure:
        _owned_directory_capture(journal, failure, cleanup=True)


def _retire_owned_directory_slot(slot):
    if slot._state != "owned" or slot._close_consumed:
        return
    journal = slot._journal
    # Consume before clock/native work. An uncertain numeric close is not retried.
    slot._close_consumed, slot._state = True, "attempted"
    _owned_directory_cleanup_clock(journal)
    try:
        slot._close_native_attempted = True
        result = _owned_directory_close_handle(slot._number)
        slot._close_returned = True
        slot._close_return_legal = type(result) is int and -(1 << 31) <= result < (1 << 31)
        _owned_directory_bool(result)
    except BaseException as failure:
        slot._state = "unknown"
        _owned_directory_capture(journal, failure, cleanup=True)
    else:
        # Physical closure survives a later deadline/getter/allocation failure.
        slot._state = "closed"
        _owned_directory_cleanup_clock(journal)


def _win_open_owned_directory(path, slot):
    if (type(slot) is not _OwnedDirectorySlot
            or type(slot._journal) is not _OwnedDirectoryJournal
            or slot._journal._seal is not _OWNED_DIRECTORY_SEAL
            or slot._journal._slot is not slot or slot._journal._owner is None
            or slot._journal._phase != "opening" or slot._state != "empty"):
        _owned_directory_fail("INVALID_ARGUMENT")
    journal = slot._journal
    try:
        if Path(path) != journal._owner.path:
            _owned_directory_fail("INVALID_ARGUMENT")
        _owned_directory_clock(journal._token)
        native = _native_path(path)
        _owned_directory_clock(journal._token)
        slot._create_native_attempted = True
        try:
            handle = _owned_directory_create_file(native)
        except BaseException:
            slot._state = "unknown"
            raise
        slot._create_returned = True
        if type(handle) is int and handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        if type(handle) is not int or not 0 < handle < ctypes.c_void_p(-1).value:
            slot._number, slot._state = handle, "unknown"
            _owned_directory_fail("INVALID_RESULT")
        slot._number, slot._state, slot._create_return_legal = handle, "owned", True
        _owned_directory_clock(journal._token)
        info = _HandleInfo()
        address = ctypes.byref(info)
        _owned_directory_clock(journal._token)
        slot._query_native_attempted = True
        result = _owned_directory_get_info(handle, address)
        slot._query_returned = True
        slot._query_return_legal = type(result) is int and -(1 << 31) <= result < (1 << 31)
        _owned_directory_bool(result)
        journal._native_file_identity = (info.volume, info.index_high, info.index_low)
        _owned_directory_clock(journal._token)
        if info.attributes & _REPARSE or not info.attributes & 0x10:
            _error("Media paths must not contain links or unexpected file types")
        _owned_directory_clock(journal._token)
        return handle
    except BaseException as failure:
        _owned_directory_capture(journal, failure)
        _retire_owned_directory_slot(slot)
        _owned_directory_raise(journal)


def _owned_directory_activated(journal):
    _owned_directory_clock(journal._token)
    if journal._slot._state != "owned":
        _owned_directory_fail("INVALID_RESULT")
    journal._phase, journal._enter_returned = "active", True


def _owned_directory_exit(owner, journal, primary, traceback):
    if primary is not None:
        _owned_directory_capture(journal, primary, traceback=traceback)
    if journal._phase != "retired":
        journal._exit_attempted, journal._phase = True, "retired"
        owner._known_closed, owner.fd = True, None
        _retire_owned_directory_slot(journal._slot)
        if journal._slot._state == "closed":
            try:
                owner.handles.clear()
            except BaseException as failure:
                _owned_directory_capture(journal, failure, cleanup=True)
        _owned_directory_cleanup_clock(journal)
        journal._normal_exit = (journal._first is None and journal._enter_returned
                                and journal._slot._state == "closed")
        # Legacy verification continues to reject a failed physical close.
        if journal._slot._state == "unknown":
            owner._close_failure = journal._first
    if journal._first is not None and journal._first is not primary:
        _owned_directory_raise(journal)
    return None


def _owned_directory_receipt(owner):
    journal = object.__getattribute__(owner, "__dict__").get("_owned_close_journal")
    if journal is None:
        return None
    slot = journal._slot
    # Own cached scalars/tuples only; no binding/clock/native/lower getter.
    return OwnedDirectoryCloseReceiptV1(1, "owned_directory", "native_owner_close_facts_only",
        journal._phase, journal._enter_returned, journal._exit_attempted,
        journal._first is not None, journal._normal_exit and journal._first is None,
        journal._root_ordinal, (slot._state,), journal._native_file_identity,
        journal._directory_identity, slot._create_native_attempted, slot._create_returned,
        slot._create_return_legal, slot._query_native_attempted, slot._query_returned,
        slot._query_return_legal, slot._close_consumed, slot._close_native_attempted,
        slot._close_returned, slot._close_return_legal)


class _Directory:
    """Hold each validated ancestor; use fd-relative operations on POSIX."""
    def __init__(self, path, *, create=False, base=None, parent=None, _owned_close_journal=None):
        if object.__getattribute__(self, "__dict__").get("_owned_close_journal") is not None:
            _owned_directory_fail("CLOSED")
        if _owned_close_journal is not None and object.__getattribute__(self, "__dict__"):
            _owned_directory_fail("CLOSED")
        if _owned_close_journal is None:
            self.path = Path(path)
            self.base = Path(base) if base is not None else self.path
            self.parent = parent
            self.create = create
            self.handles = []
            self.identities = []
            self.fd = None
            self._close_failure = None
            self._known_closed = False
            return
        journal = _owned_directory_claim(self, _owned_close_journal, create=create, parent=parent)
        try:
            self.path = Path(path)
            self.base = Path(base) if base is not None else self.path
            self.parent = parent
            self.create = create
            self.handles = []
            self.identities = []
            self.fd = None
            self._close_failure = None
            self._known_closed = False
            if self.base != self.path:
                _owned_directory_fail("INVALID_ARGUMENT")
        except BaseException as failure:
            _owned_directory_capture(journal, failure)
            journal._phase = "failed_constructor"
            _owned_directory_raise(journal)
    def __enter__(self):
        journal = object.__getattribute__(self, "__dict__").get("_owned_close_journal")
        if journal is not None:
            _owned_directory_begin(self, journal)
        if self._close_failure is not None:
            raise self._close_failure
        self._known_closed = False
        try:
            paths = [Path(self.path.anchor)]
            for component in self.path.parts[1:]:
                paths.append(paths[-1] / component)
            protected_index = paths.index(self.base)
            parent_fd = None
            protecting = False
            if self.parent is not None:
                if isinstance(self.parent, _Directory):
                    if self.parent._close_failure is not None:
                        raise self.parent._close_failure
                    if self.parent._known_closed:
                        raise OSError(errno.EBADF, "Media directory is closed")
                self.parent.verify()
                if self.path.parent != self.parent.path:
                    _error("Media directory parent does not match")
                paths = [self.path]
                protected_index = 0
                parent_fd = self.parent.fd
                protecting = True
            for index, path in enumerate(paths):
                try:
                    before = os.stat(_native_path(path), follow_symlinks=False) if os.name == "nt" or parent_fd is None else os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    if not self.create:
                        raise
                    if not protecting:
                        # Pin the nearest existing parent only when creating a
                        # new root. System ancestors need metadata checks, not
                        # extra directory-handle permissions or mutation locks.
                        parent = paths[index - 1]
                        if os.name == "nt":
                            handle = _win_open(parent, directory=True)
                        else:
                            handle = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                            info = os.fstat(handle)
                            _, device, inode = self.identities[-1]
                            if (info.st_dev, info.st_ino) != (device, inode):
                                os.close(handle)
                                _error("Media parent changed during creation")
                            parent_fd = handle
                        self.handles.append(handle)
                        protecting = True
                        self.verify()
                    try:
                        if os.name == "nt" or parent_fd is None:
                            os.mkdir(_native_path(path))
                        else:
                            os.mkdir(path.name, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    before = os.stat(_native_path(path), follow_symlinks=False) if os.name == "nt" or parent_fd is None else os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                _safe_stat(before, directory=True)
                if protecting or index >= protected_index:
                    if os.name == "nt":
                        handle = (_win_open(path, directory=True) if journal is None else
                                  _win_open(path, directory=True, _owned_directory_slot=journal._slot))
                        if journal is not None:
                            journal._directory_identity = (before.st_dev, before.st_ino)
                    else:
                        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                        handle = os.open(path if parent_fd is None else path.name, flags, dir_fd=parent_fd)
                        actual = os.fstat(handle)
                        if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
                            os.close(handle)
                            _error("Media directory changed while it was opened")
                        parent_fd = handle
                    self.handles.append(handle)
                    protecting = True
                self.identities.append((path, before.st_dev, before.st_ino))
            self.fd = self.handles[-1] if os.name != "nt" else None
            self.verify()
            if journal is not None:
                _owned_directory_activated(journal)
            return self
        except BaseException as primary:
            original_tb = BaseException.__dict__["__traceback__"].__get__(primary)
            self.__exit__(type(primary), primary, original_tb)
            raise

    def __exit__(self, exc_type, exc, tb):
        journal = object.__getattribute__(self, "__dict__").get("_owned_close_journal")
        if journal is not None:
            return _owned_directory_exit(self, journal, exc, tb)
        first = self._close_failure
        self._known_closed = True
        self.fd = None
        if first is None:
            # Unknown failed numeric handles are retained as observations only.
            # They must never be retried: an OS close can fail after releasing a
            # number, and that number may already name an unrelated resource.
            for index in range(len(self.handles) - 1, -1, -1):
                handle = self.handles[index]
                try:
                    if os.name == "nt":
                        if not _kernel.CloseHandle(handle):
                            number = ctypes.get_last_error()
                            raise OSError(errno.EIO, "Media directory handle close failed", None, number)
                    else:
                        os.close(handle)
                except BaseException as failure:
                    if first is None:
                        first = failure
                else:
                    del self.handles[index]
            self._close_failure = first
        if first is not None:
            if exc is None:
                raise first
            try:
                BaseException.add_note(exc, "SYNC_MEDIA_DIRECTORY_CLOSE_FAILED")
            except BaseException:
                pass
        return None

    @property
    def owned_close_receipt(self):
        return _owned_directory_receipt(self)

    def verify(self):
        if self._close_failure is not None:
            raise self._close_failure
        if self.parent is not None:
            self.parent.verify()
        for path, device, inode in self.identities:
            current = os.stat(_native_path(path), follow_symlinks=False)
            _safe_stat(current, directory=True)
            if (current.st_dev, current.st_ino) != (device, inode):
                _error("Media directory changed during the operation")

    def entry_stat(self, name):
        if self._close_failure is not None:
            raise self._close_failure
        if self._known_closed:
            raise OSError(errno.EBADF, "Media directory is closed")
        _entry_name(name)
        if os.name == "nt":
            return os.stat(_native_path(self.path / name), follow_symlinks=False)
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def names(self):
        if self._close_failure is not None:
            raise self._close_failure
        if self._known_closed:
            raise OSError(errno.EBADF, "Media directory is closed")
        return os.listdir(_native_path(self.path) if os.name == "nt" else self.fd)

    def unlink(self, name):
        if self._close_failure is not None:
            raise self._close_failure
        if self._known_closed:
            raise OSError(errno.EBADF, "Media directory is closed")
        _entry_name(name)
        if os.name == "nt":
            os.unlink(_native_path(self.path / name))
        else:
            os.unlink(name, dir_fd=self.fd)

    def open_file(self, name, *, write=False, create=False, lock=False):
        if self._close_failure is not None:
            raise self._close_failure
        if self._known_closed:
            raise OSError(errno.EBADF, "Media directory is closed")
        _entry_name(name)
        if os.name == "nt":
            fd = _win_open(self.path / name, write=write, create=create, lock=lock)
        else:
            flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW
            if create or lock:
                flags |= os.O_CREAT
            if create and not lock:
                flags |= os.O_EXCL
            fd = os.open(name, flags, 0o600, dir_fd=self.fd)
            try:
                info = os.fstat(fd)
                _safe_stat(info, directory=False)
                if info.st_nlink != 1:
                    _error("Media files must not have external hard links")
            except BaseException:
                os.close(fd)
                raise
        return os.fdopen(fd, "r+b" if write else "rb")


_DIRECTORY_VERIFY_ORIGINAL = _Directory.verify


def _directory_verification_plan(directories):
    """Select one fixed graph for a single read-only group verification.

    Custom directory verifiers retain their ordinary dispatch path. This plan
    holds expected identities only, never a previously successful observation.
    """
    selected, visiting, seen = [], set(), set()

    def visit(directory):
        if (type(directory) is not _Directory
                or getattr(directory.verify, "__func__", None) is not _DIRECTORY_VERIFY_ORIGINAL):
            return False
        if directory._close_failure is not None:
            raise directory._close_failure
        key = id(directory)
        if key in visiting:
            _error("Media directory parent cycle")
        if key in seen:
            return True
        visiting.add(key)
        parent = directory.parent
        if parent is not None and not visit(parent):
            return False
        selected.append((directory, parent, tuple(directory.identities)))
        visiting.remove(key)
        seen.add(key)
        return True

    return tuple(selected) if all(visit(directory) for directory in directories) else None


def _verify_directory_plan(plan):
    # Each invocation makes new physical observations. Different original
    # expectations for the same path must all match this phase's one stat.
    observed = {}
    for directory, parent, identities in plan:
        if directory._close_failure is not None:
            raise directory._close_failure
        if (directory.parent is not parent or tuple(directory.identities) != identities
                or getattr(directory.verify, "__func__", None) is not _DIRECTORY_VERIFY_ORIGINAL):
            _error("Media directory verification graph changed")
        for path, device, inode in identities:
            native = _native_path(path)
            if native not in observed:
                observed[native] = os.stat(native, follow_symlinks=False)
            current = observed[native]
            _safe_stat(current, directory=True)
            if (current.st_dev, current.st_ino) != (device, inode):
                _error("Media directory changed during the operation")


@contextmanager
def _upload_lock(directory, *, check_cancelled=None):
    """Reserve temporary storage with bounded, cancellable OS lock retries."""
    check = check_cancelled or (lambda: None)
    deadline = monotonic() + UPLOAD_LOCK_WAIT_SECONDS
    with directory.open_file(_LOCK_NAME, write=True, lock=True) as stream:
        if os.name != "nt":
            import fcntl
        while True:
            check()
            if monotonic() >= deadline:
                _error("Media upload storage is busy; retry later", "SYNC_MEDIA_BUSY", 409)
            try:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                contention = exc.errno in {errno.EACCES, errno.EAGAIN}
                if os.name == "nt":
                    contention = contention or exc.errno == errno.EDEADLK or getattr(exc, "winerror", None) == 33
                if not contention:
                    raise
                remaining = deadline - monotonic()
                if remaining <= 0:
                    _error("Media upload storage is busy; retry later", "SYNC_MEDIA_BUSY", 409)
                sleep(min(_LOCK_RETRY_SECONDS, remaining))
        try:
            check()
            directory.verify()
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _rename_new(source, name, destination, target):
    """Atomic publication without ever overwriting a pre-existing object."""
    _entry_name(name)
    _entry_name(target)
    if os.name == "nt":
        encoded = target.encode("utf-16-le")
        name_offset = _WinRenameInfo.FileName.offset
        buffer_size = max(ctypes.sizeof(_WinRenameInfo) + len(encoded), name_offset + len(encoded) + 2)
        if buffer_size > 0xFFFFFFFF:
            raise OverflowError("Media rename buffer exceeds DWORD size")
        # Zero backing includes an extra UTF-16 terminator outside the length.
        # A fixed view overlays bytes; no per-name ctypes class is created.
        backing = bytearray(buffer_size)
        value = _WinRenameInfo.from_buffer(backing)
        value.Replace.ReplaceIfExists = False
        value.RootDirectory = None if source is destination else destination.handles[-1]
        value.FileNameLength = len(encoded)
        backing[name_offset:name_offset + len(encoded)] = encoded
        address = ctypes.byref(value)
        source_path = source.path / name
        fd = None
        try:
            fd = _win_open(source_path, delete=True)
            io_status = _IoStatus()
            result = _nt.NtSetInformationFile(msvcrt.get_osfhandle(fd), ctypes.byref(io_status),
                                              address, buffer_size, 10)
            if result != 0:
                raise ctypes.WinError(_nt.RtlNtStatusToDosError(result))
            io_result = ctypes.c_long((io_status.status or 0) & 0xFFFFFFFF).value
            if io_result != 0:
                raise ctypes.WinError(_nt.RtlNtStatusToDosError(io_result))
        except BaseException as first:
            if fd is not None:
                try:
                    # Mutually exclusive with else: never retry a numeric fd.
                    os.close(fd)
                except BaseException:
                    try:
                        BaseException.add_note(first, "SYNC_MEDIA_RENAME_FD_CLOSE_FAILED")
                    except BaseException:
                        pass
            # Preserve the active first and its traceback without observing it.
            raise
        else:
            # Sole close after success; its failure propagates as the first.
            os.close(fd)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is not None:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(source.fd, os.fsencode(name), destination.fd, os.fsencode(target), 1) == 0:
            return
        number = ctypes.get_errno()
        if number not in (errno.ENOSYS, errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)):
            raise OSError(number, os.strerror(number))
    # Portable filesystems without renameat2 retain no-overwrite semantics.
    # A crash between link/unlink leaves a recognized .part for locked cleanup.
    os.link(name, target, src_dir_fd=source.fd, dst_dir_fd=destination.fd, follow_symlinks=False)
    source.unlink(name)


def _verify_stream(stream, descriptor, *, check_cancelled=None):
    check = check_cancelled or (lambda: None)
    check()
    before = os.fstat(stream.fileno())
    if before.st_size != descriptor["byte_size"]:
        _error("Stored media does not match the descriptor", "SYNC_MEDIA_CORRUPT", 409)
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(128 * 1024), b""):
        check()
        size += len(chunk)
        if size > descriptor["byte_size"]:
            _error("Stored media changed during verification", "SYNC_MEDIA_CORRUPT", 409)
        digest.update(chunk)
    after = os.fstat(stream.fileno())
    if (size != descriptor["byte_size"] or digest.hexdigest() != descriptor["sha256"]
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        _error("Stored media failed integrity verification", "SYNC_MEDIA_CORRUPT", 409)


def _verify_file(directory, name, descriptor, *, check_cancelled=None):
    try:
        with directory.open_file(name) as stream:
            _verify_stream(stream, descriptor, check_cancelled=check_cancelled)
        directory.verify()
    except FileNotFoundError:
        _error("Media has not been received", "SYNC_MEDIA_NOT_FOUND", 404)


class MediaStore:
    def __init__(self, root: Path, *, max_temp_bytes=DEFAULT_MAX_TEMP_BYTES):
        if type(max_temp_bytes) is not int or not 0 < max_temp_bytes <= 2**63 - 1:
            _error("Temporary media quota must be a positive integer", "INVALID_SYNC_REQUEST", 400)
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.max_temp_bytes = max_temp_bytes
        try:
            with _Directory(self.root, create=True, base=self.root):
                pass
        except OSError as exc:
            _io_error(exc)

    def storage_key(self, descriptor):
        media = validate_media_descriptor(descriptor)
        return f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}"

    def verified_path(self, descriptor):
        media = validate_media_descriptor(descriptor)
        target = self.root / self.storage_key(media)
        try:
            with _Directory(target.parent, base=self.root) as directory:
                _verify_file(directory, target.name, media)
            return _readable_path(target)
        except FileNotFoundError:
            _error("Media has not been received", "SYNC_MEDIA_NOT_FOUND", 404)
        except OSError as exc:
            _io_error(exc)

    @contextmanager
    def hold_verified(self, descriptors):
        """Pin verified media throughout the caller's publication transaction.

        Windows denies writes/deletes while each file is held. POSIX keeps the
        exact inode open and checks pathname identity plus bytes again on exit.
        A failed exit check must roll back the surrounding publication writes.
        """
        from .sync_snapshot import MAX_VIDEOS
        if type(descriptors) is not list or len(descriptors) > MAX_VIDEOS:
            _error("Publication media must be a bounded list", "INVALID_SYNC_REQUEST", 400)
        checked = [validate_media_descriptor(value) for value in descriptors]
        if len({value["video_id"] for value in checked}) != len(checked):
            _error("Publication media video identifiers must be unique", "INVALID_SYNC_REQUEST", 400)
        caller_error = None
        try:
            with ExitStack() as stack:
                held, paths = [], {}
                directories = {}
                if checked:
                    root = stack.enter_context(_Directory(self.root, base=self.root))
                    common = stack.enter_context(_Directory(self.root / "sync", parent=root))
                for media in checked:
                    path = self.root / self.storage_key(media)
                    directory = directories.get(path.parent)
                    if directory is None:
                        directory = stack.enter_context(_Directory(path.parent, parent=common))
                        directories[path.parent] = directory
                    stream = stack.enter_context(directory.open_file(path.name))
                    _verify_stream(stream, media)
                    original = os.fstat(stream.fileno())
                    held.append((directory, path.name, stream, media,
                                 (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns)))
                    paths[media["video_id"]] = _readable_path(path)
                verified = _VerifiedMediaHold(paths, held, self.root, seal=_MEDIA_HOLD_SEAL)
                try:
                    yield verified
                except BaseException as exc:
                    # ExitStack still releases every pin. Exceptions from the
                    # publication body retain their original type and identity.
                    caller_error = exc
                    raise
                finally:
                    verified._active = False
                for directory, name, stream, media, identity in held:
                    directory.verify()
                    try:
                        current = directory.entry_stat(name)
                    except FileNotFoundError:
                        _error("Publication media disappeared", "SYNC_MEDIA_CORRUPT", 409)
                    _safe_stat(current, directory=False)
                    current_handle = os.fstat(stream.fileno())
                    if ((current.st_dev, current.st_ino) != identity[:2]
                            or (current_handle.st_dev, current_handle.st_ino, current_handle.st_size,
                                current_handle.st_mtime_ns, current_handle.st_ctime_ns) != identity):
                        _error("Publication media was replaced", "SYNC_MEDIA_CORRUPT", 409)
                    _verify_stream(stream, media)
                    directory.verify()
        except FileNotFoundError as exc:
            if exc is caller_error:
                raise
            _error("Media has not been received", "SYNC_MEDIA_NOT_FOUND", 404)
        except OSError as exc:
            if exc is caller_error:
                raise
            _io_error(exc)

    def _reclaim_temp(self, directory):
        total = 0
        for name in directory.names():
            info = directory.entry_stat(name)
            _safe_stat(info, directory=False)
            if _PART.fullmatch(name):
                directory.unlink(name)
            else:
                total += info.st_size
                if total > self.max_temp_bytes:
                    _error("Temporary media storage quota is exhausted", "SYNC_MEDIA_TEMP_QUOTA", 507)
        return total

    def receive(self, descriptor, chunks, *, check_cancelled=None):
        """Receive one immutable file; cancellation releases locks and temp data.

        ``check_cancelled`` is an optional synchronous callback. Its exception
        belongs to the caller and is never rewritten as a stream/storage error.
        A verified file already renamed before cancellation can remain safely
        staged; this method never exposes knowledge or completes queue tasks.
        """
        media = validate_media_descriptor(descriptor)
        if check_cancelled is not None and not callable(check_cancelled):
            _error("Media cancellation check must be callable", "INVALID_SYNC_REQUEST", 400)
        callback_error = None

        def check():
            nonlocal callback_error
            if check_cancelled is not None:
                try:
                    check_cancelled()
                except BaseException as exc:
                    callback_error = exc
                    raise

        check()
        key = self.storage_key(media)
        target = self.root / key
        try:
            iterator = iter(chunks)
        except SyncError:
            raise
        except Exception:
            _error("Media body must be a byte iterator", "SYNC_MEDIA_STREAM_ERROR", 400)
        try:
            with (_Directory(self.root / ".sync-tmp", create=True, base=self.root) as temporary,
                  _upload_lock(temporary, check_cancelled=check)):
                check()
                occupied = self._reclaim_temp(temporary)
                if occupied + media["byte_size"] > self.max_temp_bytes:
                    _error("Temporary media storage quota is exhausted", "SYNC_MEDIA_TEMP_QUOTA", 507)
                with _Directory(target.parent, create=True, base=self.root) as destination:
                    try:
                        existing = destination.entry_stat(target.name)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        _safe_stat(existing, directory=False)
                        _verify_file(destination, target.name, media, check_cancelled=check)
                    name = uuid.uuid4().hex + ".part"
                    owned = None
                    try:
                        with temporary.open_file(name, write=True, create=True) as sink:
                            allocated = os.fstat(sink.fileno())
                            owned = (allocated.st_dev, allocated.st_ino)
                            digest, written = hashlib.sha256(), 0
                            while True:
                                check()
                                try:
                                    chunk = next(iterator)
                                except StopIteration:
                                    break
                                except SyncError:
                                    raise
                                except Exception:
                                    _error("Media stream was interrupted", "SYNC_MEDIA_STREAM_ERROR", 400)
                                check()
                                if not isinstance(chunk, bytes):
                                    _error("Media chunks must be bytes", "SYNC_MEDIA_STREAM_ERROR", 400)
                                if written + len(chunk) > media["byte_size"]:
                                    _error("Media stream exceeds its declared size", "SYNC_MEDIA_TOO_LARGE", 413)
                                if chunk:
                                    if sink.write(chunk) != len(chunk):
                                        raise OSError(errno.EIO, "Incomplete temporary media write")
                                    digest.update(chunk)
                                    written += len(chunk)
                            if written != media["byte_size"]:
                                _error("Media stream is shorter than its declared size", "SYNC_MEDIA_SIZE_MISMATCH", 422)
                            if digest.hexdigest() != media["sha256"]:
                                _error("Media stream hash does not match", "SYNC_MEDIA_HASH_MISMATCH", 422)
                            check()
                            sink.flush()
                            os.fsync(sink.fileno())
                        temporary.verify()
                        destination.verify()
                        current = temporary.entry_stat(name)
                        if (current.st_dev, current.st_ino) != owned:
                            _error("Temporary media changed during upload")
                        _verify_file(temporary, name, media, check_cancelled=check)
                        if existing is not None:
                            _verify_file(destination, target.name, media, check_cancelled=check)
                            check()
                            temporary.unlink(name)
                        else:
                            check()
                            try:
                                _rename_new(temporary, name, destination, target.name)
                            except FileExistsError:
                                # Even an independently created same-name file
                                # is never overwritten or accepted on name alone.
                                _verify_file(destination, target.name, media, check_cancelled=check)
                                temporary.unlink(name)
                            _verify_file(destination, target.name, media, check_cancelled=check)
                        if os.name != "nt":
                            os.fsync(destination.fd)
                            os.fsync(temporary.fd)
                        check()
                        return key
                    finally:
                        try:
                            remaining = temporary.entry_stat(name)
                        except FileNotFoundError:
                            pass
                        else:
                            _safe_stat(remaining, directory=False)
                            if owned == (remaining.st_dev, remaining.st_ino):
                                temporary.unlink(name)
        except BaseException as exc:
            if callback_error is not None:
                if exc is callback_error:
                    raise
                raise callback_error from None
            if isinstance(exc, OSError):
                _io_error(exc)
            raise
