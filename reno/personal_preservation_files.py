"""Pinned private package files; no SQL, configuration or producer calls.

Path and handle identities are separate: Windows exposes different ctime
semantics through stat and fstat. A consumer owns the surrounding lifecycle
guard, size budget and lifetime of these observations.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from . import sync_media as safe


class PreservationFileError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def identity(info):
    safe._safe_stat(info, directory=False)
    if info.st_nlink != 1:
        raise PreservationFileError("PERSONAL_PRESERVATION_FILE_UNSAFE")
    return {"device": str(info.st_dev), "inode": str(info.st_ino),
            "byte_size": info.st_size, "mtime_ns": str(info.st_mtime_ns),
            "ctime_ns": str(info.st_ctime_ns), "mode": info.st_mode,
            "nlink": info.st_nlink}


def directory_identity(directory):
    directory.verify()
    info = directory.path.stat(follow_symlinks=False)
    safe._safe_stat(info, directory=True)
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _windows_acl(path, *, directory, initialize=False):
    """Set/check only package-owned objects, using a pinned no-reparse handle."""
    import ctypes
    from ctypes import wintypes
    kernel = safe._kernel
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorOwner.argtypes = [wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
    advapi.SetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID)]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    token, length = wintypes.HANDLE(), wintypes.DWORD()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not 0 < length.value <= 65536:
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
        user = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, user, length, ctypes.byref(length)):
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
        sid_ptr = ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0]
        sid_string = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_string)):
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
        try:
            sid = sid_string.value
        finally:
            kernel.LocalFree(ctypes.cast(sid_string, wintypes.HLOCAL))
    finally:
        kernel.CloseHandle(token)
    # A fresh object's owner implicitly has WRITE_DAC, but not WRITE_OWNER.
    # Inherited Modify on a workspace is therefore enough for this first open;
    # requesting ownership rights before establishing the private DACL fails.
    access = 0x00020000 | (0x00040000 if initialize else 0)  # READ_CONTROL, WRITE_DAC
    handle = kernel.CreateFileW(safe._native_path(path), access, 3, None, 3,
        0x00200000 | (0x02000000 if directory else 0), None)
    if handle == ctypes.c_void_p(-1).value:
        raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNAVAILABLE")
    try:
        info = safe._HandleInfo()
        if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                or info.attributes & safe._REPARSE
                or bool(info.attributes & 0x10) != directory
                or not directory and info.links != 1):
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_UNSAFE")
        if initialize:
            descriptor = wintypes.LPVOID()
            sddl = f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)"
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl, 1, ctypes.byref(descriptor), None):
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNAVAILABLE")
            try:
                present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
                if (not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                        ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value or not dacl.value
                        or advapi.SetSecurityInfo(handle, 1, 0x80000004, None, None, dacl, None)):
                    raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNAVAILABLE")
            finally:
                kernel.LocalFree(descriptor)
            # The original no-reparse handle remains open without share-delete.
            # FullControl is now granted to the exact process principal. Obtain
            # ownership rights only now, and never act on a replacement object.
            owner_handle = kernel.CreateFileW(safe._native_path(path), 0x000A0000, 3, None, 3,
                0x00200000 | (0x02000000 if directory else 0), None)
            if owner_handle == ctypes.c_void_p(-1).value:
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
            try:
                owner_info = safe._HandleInfo()
                if (not kernel.GetFileInformationByHandle(owner_handle, ctypes.byref(owner_info))
                        or owner_info.attributes & safe._REPARSE
                        or bool(owner_info.attributes & 0x10) != directory
                        or not directory and owner_info.links != 1
                        or (owner_info.volume, owner_info.index_high, owner_info.index_low) !=
                           (info.volume, info.index_high, info.index_low)):
                    raise PreservationFileError("PERSONAL_PRESERVATION_FILE_UNSAFE")
                if advapi.SetSecurityInfo(owner_handle, 1, 1, sid_ptr, None, None, None):
                    raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
            finally:
                kernel.CloseHandle(owner_handle)
        descriptor, text = wintypes.LPVOID(), wintypes.LPWSTR()
        if advapi.GetSecurityInfo(handle, 1, 5, None, None, None, None, ctypes.byref(descriptor)):
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNAVAILABLE")
        try:
            owner, defaulted = wintypes.LPVOID(), wintypes.BOOL()
            if not advapi.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)):
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
            owner_string = wintypes.LPWSTR()
            if not owner.value or not advapi.ConvertSidToStringSidW(owner, ctypes.byref(owner_string)):
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
            try:
                if owner_string.value != sid:
                    raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN")
            finally:
                kernel.LocalFree(ctypes.cast(owner_string, wintypes.HLOCAL))
            if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor, 1, 4, ctypes.byref(text), None):
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNAVAILABLE")
            try:
                sddl = text.value
            finally:
                kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
        finally:
            kernel.LocalFree(descriptor)
        # Only full-control ACEs for this process principal and SYSTEM. There
        # are no caller-provided SIDs, account names or shell commands.
        match = re.fullmatch(r"D:([A-Z]*)(\(.*\))", sddl)
        if match is None or directory and "P" not in match[1]:
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNPROVEN")
        aces = re.findall(r"\(([^()]*)\)", match[2])
        if "".join("(" + ace + ")" for ace in aces) != match[2] or not aces:
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNPROVEN")
        principals = set()
        for ace in aces:
            parts = ace.split(";")
            if (len(parts) != 6 or parts[0] != "A" or parts[2] not in {"FA", "0x1f01ff"}
                    or parts[3:5] != ["", ""] or parts[5] not in {sid, "SY"}
                    or any(flag not in {"OI", "CI", "IO", "ID"} for flag in
                           re.findall("..", parts[1])) or len(parts[1]) % 2):
                raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNPROVEN")
            principals.add(parts[5])
        if sid not in principals and not (sid == "S-1-5-18" and "SY" in principals):
            raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_ACL_UNPROVEN")
    finally:
        kernel.CloseHandle(handle)


def require_private(path, *, directory):
    """Never repair permissions on an existing or read-only package object."""
    info = os.stat(safe._native_path(path), follow_symlinks=False)
    safe._safe_stat(info, directory=directory)
    if os.name == "nt":
        _windows_acl(path, directory=directory)
    elif (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise PreservationFileError("PERSONAL_PRESERVATION_PRIVATE_MODE_UNPROVEN")


class FileHold:
    """No-follow raw read retaining the original directory and file handle."""
    def __init__(self, path, *, maximum, check_cancelled=None, collect=True):
        self.path = Path(path)
        self.maximum = maximum
        self.check = check_cancelled or (lambda: None)
        self.collect = collect
        self._stack = None
        self.stream = None

    def __enter__(self):
        if type(self.maximum) is not int or self.maximum < 0:
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_LIMIT_INVALID")
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_UNSAFE")
        self._stack = ExitStack()
        try:
            self.check()
            self.directory = self._stack.enter_context(safe._Directory(
                self.path.parent, base=self.path.parent))
            self.directory_identity = directory_identity(self.directory)
            self.path_identity = identity(self.directory.entry_stat(self.path.name))
            if self.path_identity["byte_size"] > self.maximum:
                raise PreservationFileError("PERSONAL_PRESERVATION_FILE_TOO_LARGE")
            self.stream = self._stack.enter_context(self.directory.open_file(self.path.name))
            self.handle_identity = identity(os.fstat(self.stream.fileno()))
            if any(self.path_identity[key] != self.handle_identity[key] for key in
                   ("device", "inode", "byte_size", "mtime_ns", "mode", "nlink")):
                raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
            self.sha256, self.raw = self._read(self.collect)
            self.verify()
            return self
        except BaseException as primary:
            try:
                self._stack.close()
            except BaseException:
                primary.add_note("PERSONAL_PRESERVATION_FILE_CLEANUP_FAILED")
            self._stack = None
            raise

    def _read(self, collect):
        self.stream.seek(0)
        digest, size, parts = hashlib.sha256(), 0, []
        while True:
            self.check()
            chunk = self.stream.read(128 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > self.maximum or size > self.handle_identity["byte_size"]:
                raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
            digest.update(chunk)
            if collect:
                parts.append(chunk)
        if size != self.handle_identity["byte_size"]:
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        return digest.hexdigest(), b"".join(parts) if collect else None

    def verify(self):
        if self._stack is None:
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CLOSED")
        self.check()
        self.directory.verify()
        if (directory_identity(self.directory) != self.directory_identity
                or identity(self.directory.entry_stat(self.path.name)) != self.path_identity
                or identity(os.fstat(self.stream.fileno())) != self.handle_identity):
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        if (self._read(False)[0] != self.sha256
                or identity(os.fstat(self.stream.fileno())) != self.handle_identity
                or identity(self.directory.entry_stat(self.path.name)) != self.path_identity
                or directory_identity(self.directory) != self.directory_identity):
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        self.directory.verify()
        self.check()

    def __exit__(self, exc_type, exc, tb):
        primary = exc
        try:
            if exc_type is None:
                self.verify()
        except BaseException as failure:
            primary = failure
            raise
        finally:
            try:
                self._stack.close()
            except BaseException:
                if primary is None:
                    raise
                primary.add_note("PERSONAL_PRESERVATION_FILE_CLEANUP_FAILED")
            finally:
                self._stack = None


def mkdir_child(parent, name):
    """Create one private child under a held parent, retaining existing paths."""
    safe._entry_name(name)
    parent.verify()
    created = False
    try:
        if os.name == "nt":
            os.mkdir(safe._native_path(parent.path / name))
        else:
            os.mkdir(name, mode=0o700, dir_fd=parent.fd)
        created = True
    except FileExistsError:
        safe._safe_stat(parent.entry_stat(name), directory=True)
    if os.name != "nt":
        os.fsync(parent.fd)
    parent.verify()
    if os.name == "nt" and created:
        _windows_acl(parent.path / name, directory=True, initialize=True)
    require_private(parent.path / name, directory=True)
    parent.verify()


def publish(directory, name, raw, *, replace=False):
    """Fsync and publish canonical metadata without replacing immutable facts.

    replace is reserved for derived state, never payloads or original records.
    Unknown temporary metadata is retained if publication fails.
    """
    safe._entry_name(name)
    if type(raw) is not bytes:
        raise PreservationFileError("PERSONAL_PRESERVATION_BYTES_REQUIRED")
    directory.verify()
    temporary = ".preservation-write-" + str(uuid.uuid4()) + ".tmp"
    if replace:
        try:
            previous = directory.entry_stat(name)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            identity(previous)
    with directory.open_file(temporary, write=True, create=True) as stream:
        if os.name == "nt":
            _windows_acl(directory.path / temporary, directory=False, initialize=True)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        identity(os.fstat(stream.fileno()))
    directory.verify()
    if replace:
        if os.name == "nt":
            os.replace(safe._native_path(directory.path / temporary),
                       safe._native_path(directory.path / name))
        else:
            os.replace(temporary, name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
    else:
        safe._rename_new(directory, temporary, directory, name)
    if os.name != "nt":
        os.fsync(directory.fd)
    directory.verify()
    return hashlib.sha256(raw).hexdigest()
