"""Windows creation custody for new namespace3 filesystem facts only.

Every directory is an owned native HANDLE, never a caller's numeric handle.
NtCreateFile must synchronously return FILE_CREATED for new directories. There
is no CreateDirectory/open-existing fallback or partial-object adoption. SID
values are temporary and are never included in records, errors, or results.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import os
from pathlib import Path

from . import personal_authority_namespace3_contract as contract
from . import personal_preservation_files as facts
from . import storage_lifecycle as lifecycle, sync_media as safe
from . import personal_authority_enrollment_owner_dispatch as _custody

CHUNK_BYTES = 8192
_DIR_READ = 0x00120081  # LIST_DIRECTORY | READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE
_DIR_CREATE = _DIR_READ | 0x00000006  # ADD_FILE | ADD_SUBDIRECTORY, never DELETE
_FILE_READ = 0x80120000
_FILE_CREATE = 0xC0130000  # READ/WRITE | DELETE | READ_CONTROL | SYNCHRONIZE
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class Namespace3IOError(ValueError):
    def __init__(self, suffix):
        self.code = "NAMESPACE3_IO_" + suffix
        super().__init__(self.code)


Error = Namespace3IOError


def _fail(suffix):
    raise Namespace3IOError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "NAMESPACE3_IO_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    resource = context.__enter__()
    try:
        yield resource
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


class _Owner:
    """One registered owner; failed numeric resources are never retried."""
    def __init__(self, value, closer, *, _custody_slot=None):
        self.value, self.closer = value, closer
        self.retired, self.failure = False, None
        if _custody_slot is not None:
            self._custody_slot = _custody_slot
            _custody._bind_owner(_custody_slot, self, value, closer)

    def __enter__(self):
        if self.retired:
            _fail("OWNER_RETIRED")
        return self

    def close(self):
        if self.retired:
            if self.failure is not None:
                raise self.failure
            return
        self.retired = True
        try:
            slot = _custody._find_owner_slot(self)
            if slot is None:
                self.closer(self.value)
            else:
                try:
                    _custody._owner_slot(self, slot)
                except BaseException as primary:
                    _cleanup_pending(slot, primary)
                    raise
                _close_custody(slot)
        except BaseException as error:
            self.failure = error
            raise

    def __exit__(self, _kind, primary, _tb):
        if primary is not None:
            try:
                slot = _custody._find_owner_slot(self)
                if slot is not None:
                    _custody._failed(slot, primary)
            except BaseException:
                _note(primary)
        try:
            self.close()
        except BaseException:
            if primary is None:
                raise
            _note(primary)
        return None


_OWNER_DICT = _Owner.__dict__["__dict__"]


if os.name == "nt":
    from ctypes import wintypes as w

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("length", w.USHORT), ("maximum_length", w.USHORT), ("buffer", w.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [("length", w.ULONG), ("root", w.HANDLE),
                    ("name", ctypes.POINTER(_UnicodeString)), ("attributes", w.ULONG),
                    ("security_descriptor", w.LPVOID), ("security_qos", w.LPVOID)]

    class _StatusUnion(ctypes.Union):
        _fields_ = [("status", w.LONG), ("pointer", w.LPVOID)]

    class _IoStatus(ctypes.Structure):
        _anonymous_ = ("completion",)
        _fields_ = [("completion", _StatusUnion), ("information", ctypes.c_size_t)]

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", w.DWORD), ("high", w.DWORD)]

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("created", _FileTime),
                    ("accessed", _FileTime), ("written", _FileTime),
                    ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
                    ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]

    class _AclSize(ctypes.Structure):
        _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]

    class _AceHeader(ctypes.Structure):
        _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD)]

    class _AllowedAce(ctypes.Structure):
        _fields_ = [("header", _AceHeader), ("mask", w.DWORD), ("sid_start", w.DWORD)]

    # Separate DLL function objects; no shared module ABI is changed.
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

    def _abi(dll, name, arguments, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, result
        return function

    _abi(_kernel, "CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
    _abi(_kernel, "CloseHandle", [w.HANDLE], w.BOOL)
    _abi(_kernel, "LocalFree", [w.HLOCAL], w.HLOCAL)
    _abi(_kernel, "GetCurrentProcess", [], w.HANDLE)
    _abi(_kernel, "GetFileInformationByHandle", [w.HANDLE, ctypes.POINTER(_HandleInfo)], w.BOOL)
    _abi(_kernel, "ReadFile", [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL)
    _abi(_kernel, "WriteFile", [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL)
    _abi(_kernel, "FlushFileBuffers", [w.HANDLE], w.BOOL)
    _abi(_kernel, "SetFilePointerEx", [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD], w.BOOL)
    _abi(_advapi, "OpenProcessToken", [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL)
    _abi(_advapi, "GetTokenInformation", [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, "ConvertSidToStringSidW", [w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL)
    _abi(_advapi, "ConvertStringSidToSidW", [w.LPCWSTR, ctypes.POINTER(w.LPVOID)], w.BOOL)
    _abi(_advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
         [w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, "GetSecurityInfo", [w.HANDLE, ctypes.c_int, w.DWORD, ctypes.POINTER(w.LPVOID),
         ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID)], w.DWORD)
    _abi(_advapi, "GetSecurityDescriptorControl", [w.LPVOID, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, "IsValidSecurityDescriptor", [w.LPVOID], w.BOOL)
    _abi(_advapi, "IsValidSid", [w.LPVOID], w.BOOL)
    _abi(_advapi, "GetLengthSid", [w.LPVOID], w.DWORD)
    _abi(_advapi, "EqualSid", [w.LPVOID, w.LPVOID], w.BOOL)
    _abi(_advapi, "GetAclInformation", [w.LPVOID, w.LPVOID, w.DWORD, ctypes.c_int], w.BOOL)
    _abi(_advapi, "GetAce", [w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)], w.BOOL)
    _abi(_ntdll, "NtCreateFile", [ctypes.POINTER(w.HANDLE), w.DWORD,
         ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatus), w.LPVOID,
         w.ULONG, w.ULONG, w.ULONG, w.ULONG, w.LPVOID, w.ULONG], w.LONG)
    _abi(_ntdll, "NtSetInformationFile", [w.HANDLE, ctypes.POINTER(_IoStatus), w.LPVOID, w.ULONG, ctypes.c_int], w.LONG)


if os.name == "nt":
    _IO_STATUS_STATUS_NATIVE = _IoStatus.status


def _supported():
    if os.name != "nt":
        _fail("UNSUPPORTED")
    expected = (16, 48, 16) if ctypes.sizeof(ctypes.c_void_p) == 8 else (8, 24, 8)
    if tuple(ctypes.sizeof(item) for item in (_UnicodeString, _ObjectAttributes, _IoStatus)) != expected:
        _fail("ABI_UNSUPPORTED")


def _native_error(number, operation):
    return OSError(errno.EIO, "Namespace3 native " + operation + " failed", None, number)


def _native_close(handle, *, _custody_slot=None):
    if _custody_slot is None:
        if not _kernel.CloseHandle(handle):
            number = ctypes.get_last_error()
            raise _native_error(number, "close")
        return
    argument = _custody._begin_close(_custody_slot, _custody._HANDLE)
    if argument is None:
        return
    try:
        raw = _kernel.CloseHandle(argument)
        success = _custody._closed(_custody_slot, raw)
        if not success:
            if _custody._signed32(raw) and raw == 0:
                number = ctypes.get_last_error()
                raise _native_error(number, "close")
            _fail("NATIVE_CLOSE_UNCONFIRMED")
    except BaseException as primary:
        try:
            _custody._failed(_custody_slot, primary)
        except BaseException:
            _note(primary)
        raise


def _native_free(pointer, *, _custody_slot=None):
    if _custody_slot is None:
        if _kernel.LocalFree(pointer):
            number = ctypes.get_last_error()
            raise _native_error(number, "free")
        return
    argument = _custody._begin_close(_custody_slot, _custody._LOCALALLOC)
    if argument is None:
        return
    try:
        raw = _kernel.LocalFree(argument)
        success = _custody._closed(_custody_slot, raw)
        if not success:
            if type(raw) is int and 0 < raw <= _custody._POINTER_MAX:
                number = ctypes.get_last_error()
                raise _native_error(number, "free")
            _fail("NATIVE_FREE_UNCONFIRMED")
    except BaseException as primary:
        try:
            _custody._failed(_custody_slot, primary)
        except BaseException:
            _note(primary)
        raise


def _close_custody(slot):
    slot = _custody._slot(slot)
    if slot._kind == _custody._HANDLE:
        _native_close(None, _custody_slot=slot)
    elif slot._kind == _custody._LOCALALLOC:
        _native_free(None, _custody_slot=slot)
    else:
        _fail("NATIVE_OWNER_UNCONFIRMED")


def _cleanup_pending(slot, primary):
    if slot is None:
        return
    try:
        _custody._failed(slot, primary)
    except BaseException:
        _note(primary)
    try:
        _close_custody(slot)
    except BaseException as cleanup:
        if cleanup is not primary:
            _note(primary)


def _registered(value, closer, budget, *, _custody_slot=None):
    """Register successful native ownership before the post-call clock gate."""
    try:
        if _custody_slot is None:
            owner = _Owner(value, closer)
        else:
            owner = _Owner(value, closer, _custody_slot=_custody_slot)
    except BaseException as primary:
        try:
            if _custody_slot is None:
                closer(value)
            else:
                _cleanup_pending(_custody_slot, primary)
        except BaseException:
            _note(primary)
        raise
    try:
        budget.clock_only()
    except BaseException as primary:
        if _custody_slot is not None:
            try:
                _custody._failed(_custody_slot, primary)
            except BaseException as propagation:
                if propagation is not primary:
                    _note(primary)
        try:
            owner.close()
        except BaseException:
            _note(primary)
        raise
    return owner


def _acquire_token(token, process, budget):
    slot = _custody._reserve(budget, "token", token)
    try:
        raw = _advapi.OpenProcessToken(process, 8, ctypes.byref(token))
        _custody._returned_bool(slot, raw)
        if not raw:
            number = ctypes.get_last_error()
            raise _native_error(number, "token")
        return _registered(token.value, _native_close, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _acquire_system_sid(sid, budget):
    slot = _custody._reserve(budget, "system_sid", sid)
    try:
        raw = _advapi.ConvertStringSidToSidW("S-1-5-18", ctypes.byref(sid))
        _custody._returned_bool(slot, raw)
        if not raw:
            number = ctypes.get_last_error()
            raise _native_error(number, "system SID")
        return _registered(sid.value, _native_free, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _acquire_sid_text(text, current, budget):
    slot = _custody._reserve(budget, "sid_text", text)
    try:
        raw = _advapi.ConvertSidToStringSidW(current, ctypes.byref(text))
        _custody._returned_bool(slot, raw)
        if not raw:
            number = ctypes.get_last_error()
            raise _native_error(number, "SID text")
        # Any cast/read failure now has the actual output slot available.
        pointer = ctypes.cast(text, w.LPVOID).value
        return _registered(pointer, _native_free, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _acquire_private_descriptor(descriptor, text, budget):
    slot = _custody._reserve(budget, "private_descriptor", descriptor)
    try:
        raw = _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text, 1, ctypes.byref(descriptor), None)
        _custody._returned_bool(slot, raw)
        if not raw:
            number = ctypes.get_last_error()
            raise _native_error(number, "private descriptor")
        return _registered(descriptor.value, _native_free, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _acquire_security_descriptor(handle, owner, dacl, descriptor, budget):
    slot = _custody._reserve(budget, "security_descriptor", descriptor)
    try:
        status = _advapi.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None,
                                       ctypes.byref(dacl), None, ctypes.byref(descriptor))
        _custody._returned_status(slot, status)
        if status != 0:
            raise _native_error(status, "security information")
        return _registered(descriptor.value, _native_free, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _predicate(function, budget, *arguments):
    budget.clock_only()
    value = function(*arguments)
    budget.clock_only()
    return value


@contextmanager
def _token_user(budget):
    token, length = w.HANDLE(), w.DWORD()
    process = _predicate(_kernel.GetCurrentProcess, budget)
    budget.clock_only()
    with _preserve_primary(_acquire_token(token, process, budget)):
        budget.clock_only()
        success = _advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        number = ctypes.get_last_error() if not success else 0
        budget.clock_only()
        if success or number != 122 or not ctypes.sizeof(ctypes.c_void_p) <= length.value <= 65536:
            _fail("PRIVATE_OWNER_UNPROVEN")
        buffer = ctypes.create_string_buffer(length.value)
        budget.clock_only()
        if not _advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(length)):
            number = ctypes.get_last_error()
            raise _native_error(number, "token information")
        budget.clock_only()
        if not ctypes.sizeof(ctypes.c_void_p) <= length.value <= len(buffer):
            _fail("PRIVATE_OWNER_UNPROVEN")
        sid = ctypes.cast(buffer, ctypes.POINTER(w.LPVOID))[0]
        base = ctypes.addressof(buffer)
        if not sid or not base <= sid < base + length.value or not _predicate(_advapi.IsValidSid, budget, sid):
            _fail("PRIVATE_OWNER_UNPROVEN")
        size = _predicate(_advapi.GetLengthSid, budget, sid)
        if not 8 <= size <= 68 or sid + size > base + length.value:
            _fail("PRIVATE_OWNER_UNPROVEN")
        # Keep the Python token buffer alive; SID is an interior pointer only.
        yield sid


@contextmanager
def _system_sid(budget):
    sid = w.LPVOID()
    budget.clock_only()
    with _preserve_primary(_acquire_system_sid(sid, budget)):
        if not sid.value or not _predicate(_advapi.IsValidSid, budget, sid):
            _fail("PRIVATE_OWNER_UNPROVEN")
        yield sid.value


@contextmanager
def _private_descriptor(budget):
    with _preserve_primary(_token_user(budget)) as current, _preserve_primary(_system_sid(budget)) as system:
        text = w.LPWSTR()
        budget.clock_only()
        with _preserve_primary(_acquire_sid_text(text, current, budget)):
            sid = text.value
            if type(sid) is not str or not 0 < len(sid) <= 256:
                _fail("PRIVATE_OWNER_UNPROVEN")
            aces = f"(A;;FA;;;{sid})" + ("" if _predicate(_advapi.EqualSid, budget, current, system) else "(A;;FA;;;SY)")
            descriptor = w.LPVOID()
            budget.clock_only()
            with _preserve_primary(_acquire_private_descriptor(descriptor, "O:" + sid + "D:P" + aces, budget)):
                if not descriptor.value or not _predicate(_advapi.IsValidSecurityDescriptor, budget, descriptor):
                    _fail("PRIVATE_OWNER_UNPROVEN")
                yield descriptor.value


def _security_check(handle, budget):
    budget.clock_only()
    with _preserve_primary(_token_user(budget)) as current, _preserve_primary(_system_sid(budget)) as system:
        owner, dacl, descriptor = w.LPVOID(), w.LPVOID(), w.LPVOID()
        budget.clock_only()
        # owner/dacl are aliases into this one descriptor allocation.
        with _preserve_primary(_acquire_security_descriptor(handle, owner, dacl, descriptor, budget)):
            control, revision = w.WORD(), w.DWORD()
            if (not descriptor.value or not owner.value or not dacl.value
                    or not _predicate(_advapi.IsValidSecurityDescriptor, budget, descriptor)
                    or not _predicate(_advapi.IsValidSid, budget, owner)
                    or not _predicate(_advapi.EqualSid, budget, owner, current)):
                _fail("PRIVATE_OWNER_UNPROVEN")
            budget.clock_only()
            if not _advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                number = ctypes.get_last_error()
                raise _native_error(number, "security descriptor control")
            budget.clock_only()
            if not control.value & 0x1000:
                _fail("PRIVATE_OWNER_UNPROVEN")
            size = _AclSize()
            budget.clock_only()
            if not _advapi.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
                number = ctypes.get_last_error()
                raise _native_error(number, "ACL information")
            budget.clock_only()
            same = bool(_predicate(_advapi.EqualSid, budget, current, system))
            expected = {"current"} if same else {"current", "system"}
            if size.count != len(expected) or not 8 <= size.used <= 65536:
                _fail("PRIVATE_OWNER_UNPROVEN")
            seen = set()
            for index in range(size.count):
                budget.clock_only()
                pointer = w.LPVOID()
                if not _advapi.GetAce(dacl, index, ctypes.byref(pointer)):
                    number = ctypes.get_last_error()
                    raise _native_error(number, "ACL entry")
                budget.clock_only()
                if not pointer.value:
                    _fail("PRIVATE_OWNER_UNPROVEN")
                ace = ctypes.cast(pointer, ctypes.POINTER(_AllowedAce)).contents
                sid = pointer.value + _AllowedAce.sid_start.offset
                if (ace.header.kind != 0 or ace.header.flags != 0 or not 16 <= ace.header.size <= 80
                        or ace.mask != 0x001F01FF or not _predicate(_advapi.IsValidSid, budget, sid)):
                    _fail("PRIVATE_OWNER_UNPROVEN")
                sid_size = _predicate(_advapi.GetLengthSid, budget, sid)
                if not 8 <= sid_size <= 68 or _AllowedAce.sid_start.offset + sid_size != ace.header.size:
                    _fail("PRIVATE_OWNER_UNPROVEN")
                role = "current" if _predicate(_advapi.EqualSid, budget, sid, current) else (
                    "system" if _predicate(_advapi.EqualSid, budget, sid, system) else None)
                if role not in expected or role in seen:
                    _fail("PRIVATE_OWNER_UNPROVEN")
                seen.add(role)
            if seen != expected:
                _fail("PRIVATE_OWNER_UNPROVEN")
    budget.clock_only()


def _info(handle, *, directory, budget):
    value = _HandleInfo()
    budget.clock_only()
    if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(value)):
        number = ctypes.get_last_error()
        raise _native_error(number, "identity")
    budget.clock_only()
    if value.attributes & 0x400 or bool(value.attributes & 0x10) != directory:
        _fail("OBJECT_UNSAFE")
    if not directory and value.links != 1:
        _fail("OBJECT_UNSAFE")
    native = {"volume_serial": str(value.volume), "file_index": str((value.index_high << 32) | value.index_low)}
    stamp = (value.attributes, value.volume, value.index_high, value.index_low,
             value.size_high, value.size_low, value.links, value.created.high, value.created.low,
             value.written.high, value.written.low)
    return native, (value.size_high << 32) | value.size_low, stamp


def _open_parent_raw(path, *, write, budget):
    budget.clock_only()
    slot = _custody._reserve(budget, "parent", None)
    try:
        handle = _kernel.CreateFileW(safe._native_path(path), _DIR_CREATE if write else _DIR_READ,
                                    3, None, 3, 0x02200000, None)
        _custody._returned_handle(slot, handle)
        if handle is None or handle == _INVALID_HANDLE:
            number = ctypes.get_last_error()
            raise _native_error(number, "parent open")
        return _registered(handle, _native_close, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise


def _relative_open(parent, name, budget, *, directory, create=False, descriptor=None, reader_share=1):
    safe._entry_name(name)
    encoded = name.encode("utf-16-le", errors="strict")
    if len(encoded) > 65532:
        _fail("INVALID_ARGUMENT")
    text = ctypes.create_unicode_buffer(name)
    unicode = _UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(text, w.LPWSTR))
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
                                  ctypes.pointer(unicode), 0x40, descriptor, None)
    handle, status = w.HANDLE(), _IoStatus()
    access = (_DIR_CREATE if create else _DIR_READ) if directory else (_FILE_CREATE if create else _FILE_READ)
    budget.clock_only()
    slot = _custody._reserve(budget, "relative", handle)
    try:
        result = _ntdll.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attributes), ctypes.byref(status),
            None, 0x10 if directory else 0x80, 3 if directory else (1 if create else reader_share),
            2 if create else 1, (1 if directory else 0x40) | 0x20 | 0x200000, None, 0)
        _custody._returned_relative(slot, result, status)
        # A nonzero/pending status leaves an unconfirmed numeric value unknown.
        # Do not close an unconfirmed possibly unrelated number or adopt residuals.
        if result != 0 or status.status != 0:
            _fail("CREATE_UNAVAILABLE" if create else "OPEN_UNAVAILABLE")
        if handle.value is None or handle.value == _INVALID_HANDLE:
            _fail("HANDLE_UNCONFIRMED")
        owner = _registered(handle.value, _native_close, budget, _custody_slot=slot)
    except BaseException as primary:
        _cleanup_pending(slot, primary)
        raise
    if slot is None:
        if status.information != (2 if create else 1):
            with _preserve_primary(owner):
                _fail("COMPLETION_UNCONFIRMED")
    else:
        # The actual confirmed HANDLE is already registered. Protect the
        # information read, comparison and truth evaluation before return.
        try:
            if status.information != (2 if create else 1):
                completion_matches = False
            else:
                completion_matches = True
            if not completion_matches:
                with _preserve_primary(owner):
                    _fail("COMPLETION_UNCONFIRMED")
        except BaseException as primary:
            _cleanup_pending(slot, primary)
            raise
    return owner


class _Directory:
    def __init__(self, path, owner, budget, *, parent=None, name=None):
        self.path, self.owner, self.budget = Path(path), owner, budget
        self.parent, self.name = parent, name
        self.native_identity = _info(owner.value, directory=True, budget=budget)[0]
        value = self.path.stat(follow_symlinks=False)
        safe._safe_stat(value, directory=True)
        self.path_identity = {"device": str(value.st_dev), "inode": str(value.st_ino)}
        self.verify()

    @property
    def handle(self):
        if self.owner.retired:
            if self.owner.failure is not None:
                raise self.owner.failure
            _fail("OWNER_RETIRED")
        return self.owner.value

    def verify(self):
        self.budget.clock_only()
        if self.parent is not None:
            self.parent.verify()
        native = _info(self.handle, directory=True, budget=self.budget)[0]
        value = self.path.stat(follow_symlinks=False)
        safe._safe_stat(value, directory=True)
        path = {"device": str(value.st_dev), "inode": str(value.st_ino)}
        if native != self.native_identity or path != self.path_identity:
            _fail("ROOT_IDENTITY_MISMATCH")
        if self.parent is None:
            other = _open_parent_raw(self.path, write=False, budget=self.budget)
        else:
            other = _relative_open(self.parent.handle, self.name, self.budget, directory=True)
        with _preserve_primary(other):
            if _info(other.value, directory=True, budget=self.budget)[0] != self.native_identity:
                _fail("ROOT_IDENTITY_MISMATCH")
        self.budget.clock_only()


def _check(profile, directory, budget):
    _supported()
    budget.clock_only()
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile, exclusive=True)
    if type(directory) is not _Directory or directory.budget is not budget:
        _fail("INVALID_ARGUMENT")
    if directory.path not in (profile.database.parent, profile.database.parent / contract.ROOT_NAME):
        _fail("PROFILE_CHANGED")
    directory.verify()


@contextmanager
def open_parent_locked(profile, budget, *, write=False):
    _supported()
    budget.clock_only()
    profile = lifecycle._profile(profile)
    paths = tuple(profile.identity)
    guard = lifecycle.require_guard(profile, exclusive=True)
    with _preserve_primary(_open_parent_raw(profile.database.parent, write=write, budget=budget)) as owner:
        parent = _Directory(profile.database.parent, owner, budget)
        yield parent
        parent.verify()
        if tuple(profile.identity) != paths or lifecycle.require_guard(profile, exclusive=True) is not guard:
            _fail("PROFILE_CHANGED")
    budget.clock_only()


@contextmanager
def create_root_locked(profile, parent, budget):
    _check(profile, parent, budget)
    if parent.path != profile.database.parent:
        _fail("PROFILE_CHANGED")
    with _preserve_primary(_private_descriptor(budget)) as descriptor:
        with _preserve_primary(_relative_open(parent.handle, contract.ROOT_NAME, budget, directory=True,
                                              create=True, descriptor=descriptor)) as owner:
            root = _Directory(parent.path / contract.ROOT_NAME, owner, budget, parent=parent, name=contract.ROOT_NAME)
            _security_check(root.handle, budget)
            yield root
            root.verify()
            _security_check(root.handle, budget)
    budget.clock_only()


@contextmanager
def open_root_locked(profile, parent, budget):
    _check(profile, parent, budget)
    if parent.path != profile.database.parent:
        _fail("PROFILE_CHANGED")
    with _preserve_primary(_relative_open(parent.handle, contract.ROOT_NAME, budget, directory=True)) as owner:
        root = _Directory(parent.path / contract.ROOT_NAME, owner, budget, parent=parent, name=contract.ROOT_NAME)
        _security_check(root.handle, budget)
        yield root
        root.verify()
        _security_check(root.handle, budget)
    budget.clock_only()


def _read_chunk(handle, requested, budget):
    amount = budget.request(min(requested, CHUNK_BYTES))
    buffer, count = ctypes.create_string_buffer(amount), w.DWORD()
    if not _kernel.ReadFile(handle, buffer, amount, ctypes.byref(count), None):
        number = ctypes.get_last_error()
        raise _native_error(number, "read")
    if count.value > amount:
        _fail("READ_INVALID")
    budget.charge(count.value)
    return buffer.raw[:count.value]


def _write_chunk(handle, raw, budget):
    amount = budget.request(min(len(raw), CHUNK_BYTES))
    buffer, count = ctypes.create_string_buffer(raw[:amount], amount), w.DWORD()
    if not _kernel.WriteFile(handle, buffer, amount, ctypes.byref(count), None):
        number = ctypes.get_last_error()
        raise _native_error(number, "write")
    if not 0 < count.value <= amount:
        _fail("WRITE_INVALID")
    budget.charge(count.value)
    return count.value


def _rewind(handle, budget):
    budget.clock_only()
    if not _kernel.SetFilePointerEx(handle, 0, None, 0):
        number = ctypes.get_last_error()
        raise _native_error(number, "seek")
    budget.clock_only()


def _read_exact(handle, byte_size, maximum, budget):
    if type(byte_size) is not int or not 0 < byte_size <= maximum:
        _fail("LIMIT")
    _rewind(handle, budget)
    parts, total = [], 0
    while total < byte_size:
        chunk = _read_chunk(handle, byte_size - total, budget)
        if not chunk:
            _fail("READ_INCOMPLETE")
        parts.append(chunk)
        total += len(chunk)
    if _read_chunk(handle, 1, budget) != b"":
        _fail("READBACK_CHANGED")
    budget.clock_only()
    return b"".join(parts)


class HeldControl:
    def __init__(self, directory, name, owner, maximum, budget):
        self.directory, self.name, self.owner, self.maximum, self.budget = directory, name, owner, maximum, budget
        self.path = directory.path / name
        self.native_identity, self.byte_size, self.stamp = _info(owner.value, directory=False, budget=budget)
        self.path_identity = facts.identity(self.path.stat(follow_symlinks=False))
        if self.path_identity["byte_size"] != self.byte_size:
            _fail("READBACK_CHANGED")
        self.raw = _read_exact(owner.value, self.byte_size, maximum, budget)
        self.raw_sha256 = hashlib.sha256(self.raw).hexdigest()
        self.verify()

    def verify(self):
        self.budget.clock_only()
        self.directory.verify()
        if self.owner.retired:
            if self.owner.failure is not None:
                raise self.owner.failure
            _fail("OWNER_RETIRED")
        native, size, stamp = _info(self.owner.value, directory=False, budget=self.budget)
        if (native != self.native_identity or size != self.byte_size or stamp != self.stamp
                or facts.identity(self.path.stat(follow_symlinks=False)) != self.path_identity):
            _fail("READBACK_CHANGED")
        self.budget.clock_only()

    def bytes(self):
        self.verify()
        raw = _read_exact(self.owner.value, self.byte_size, self.maximum, self.budget)
        if raw != self.raw:
            _fail("READBACK_CHANGED")
        self.verify()
        return raw


@contextmanager
def read_control_locked(profile, directory, name, budget, maximum, *, creator_live=False):
    _check(profile, directory, budget)
    safe._entry_name(name)
    if type(maximum) is not int or not 0 < maximum <= 64 * 1024:
        _fail("INVALID_ARGUMENT")
    with _preserve_primary(_relative_open(directory.handle, name, budget, directory=False,
                                          reader_share=7 if creator_live else 1)) as owner:
        held = HeldControl(directory, name, owner, maximum, budget)
        yield held
        held.bytes()
    budget.clock_only()


@dataclass(frozen=True, slots=True)
class PublishedFile:
    raw_sha256: str
    byte_size: int
    native_identity: dict
    path_identity: dict


def _publish_owned(directory, owner, name, budget):
    budget.clock_only()
    safe._entry_name(name)
    encoded = name.encode("utf-16-le")
    name_offset = safe._WinRenameInfo.FileName.offset
    buffer_size = max(ctypes.sizeof(safe._WinRenameInfo) + len(encoded), name_offset + len(encoded) + 2)
    if buffer_size > 0xFFFFFFFF:
        _fail("PUBLISH_UNAVAILABLE")
    backing = bytearray(buffer_size)
    value = safe._WinRenameInfo.from_buffer(backing)
    value.Replace.ReplaceIfExists = False
    # All fixed callers create their stage in this same held directory.
    # This private publisher does not expose a cross-directory operation.
    value.RootDirectory = None
    value.FileNameLength = len(encoded)
    backing[name_offset:name_offset + len(encoded)] = encoded
    status = _IoStatus()
    result = _ntdll.NtSetInformationFile(owner.value, ctypes.byref(status), ctypes.byref(value), buffer_size, 10)
    if result != 0 or status.status != 0:
        _fail("PUBLISH_UNAVAILABLE")
    budget.clock_only()


def publish_new_locked(profile, directory, name, stage_name, raw, budget):
    _check(profile, directory, budget)
    if type(raw) is not bytes or not 0 < len(raw) <= contract.MAX_RECORD_BYTES:
        _fail("INVALID_ARGUMENT")
    decoded = contract.decode(raw)
    expected_name = {"namespace_reservation": contract.RESERVATION_NAME,
                     "namespace_root_seal": contract.ROOT_SEAL_NAME,
                     "namespace_header": contract.HEADER_NAME}[decoded["kind"]]
    if (name != expected_name or stage_name != contract.stage_name(name, decoded["init_operation_id"])
            or (name == contract.ROOT_SEAL_NAME) != (directory.path == profile.database.parent / contract.ROOT_NAME)):
        _fail("INVALID_ARGUMENT")
    with _preserve_primary(_private_descriptor(budget)) as descriptor:
        with _preserve_primary(_relative_open(directory.handle, stage_name, budget, directory=False,
                                              create=True, descriptor=descriptor)) as owner:
            original = _info(owner.value, directory=False, budget=budget)
            if original[1] != 0:
                _fail("STAGE_CREATE_FAILED")
            offset = 0
            while offset < len(raw):
                offset += _write_chunk(owner.value, raw[offset:offset + CHUNK_BYTES], budget)
            budget.clock_only()
            if not _kernel.FlushFileBuffers(owner.value):
                number = ctypes.get_last_error()
                raise _native_error(number, "flush")
            budget.clock_only()
            if _read_exact(owner.value, len(raw), contract.MAX_RECORD_BYTES, budget) != raw:
                _fail("READBACK_CHANGED")
            written = _info(owner.value, directory=False, budget=budget)
            if written[0] != original[0] or written[1] != len(raw):
                _fail("READBACK_CHANGED")
            directory.verify()
            _publish_owned(directory, owner, name, budget)
            if _read_exact(owner.value, len(raw), contract.MAX_RECORD_BYTES, budget) != raw:
                _fail("READBACK_CHANGED")
            with _preserve_primary(read_control_locked(profile, directory, name, budget,
                                                       contract.MAX_RECORD_BYTES, creator_live=True)) as held:
                if held.native_identity != original[0] or held.raw != raw:
                    _fail("READBACK_CHANGED")
            published_native = _info(owner.value, directory=False, budget=budget)[0]
    # Windows timestamps stabilize only after the final creator write HANDLE
    # closed. Bind post-close metadata, preserving the original native inode.
    with _preserve_primary(read_control_locked(profile, directory, name, budget,
                                               contract.MAX_RECORD_BYTES)) as held:
        if held.native_identity != published_native or held.raw != raw:
            _fail("READBACK_CHANGED")
        result = PublishedFile(held.raw_sha256, held.byte_size, dict(held.native_identity), dict(held.path_identity))
    directory.verify()
    budget.clock_only()
    return result
