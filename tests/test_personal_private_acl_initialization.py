"""Actual Windows inherited Modify does not grant WRITE_OWNER to its owner."""
import ctypes
from ctypes import wintypes
import os
import re

import pytest

from reno import personal_preservation_files as files, sync_media as safe

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows security descriptor and access checks")

READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
MODIFY = "0x1301bf"


class WindowsSecurity:
    """Native fixture setup/observation, independent of the private ACL helper."""
    def __init__(self):
        self.kernel = safe._kernel
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel.LocalFree.restype = wintypes.HLOCAL
        self.advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.HANDLE)]
        self.advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
        self.advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
        self.advapi.GetSecurityDescriptorOwner.argtypes = [wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
        self.advapi.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
        self.advapi.SetSecurityInfo.restype = wintypes.DWORD
        self.advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID)]
        self.advapi.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
        self.user_sid = self.token_sid(1)  # TokenUser
        self.default_owner_sid = self.token_sid(4)  # TokenOwner

    def sid_text(self, pointer):
        text = wintypes.LPWSTR()
        assert self.advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text))
        try:
            return text.value
        finally:
            self.kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))

    def token_sid(self, information_class):
        token, length = wintypes.HANDLE(), wintypes.DWORD()
        assert self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token))
        try:
            self.advapi.GetTokenInformation(token, information_class, None, 0, ctypes.byref(length))
            assert 0 < length.value <= 65536
            buffer = ctypes.create_string_buffer(length.value)
            assert self.advapi.GetTokenInformation(token, information_class, buffer,
                                                  length.value, ctypes.byref(length))
            return self.sid_text(ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0])
        finally:
            self.kernel.CloseHandle(token)

    def open(self, path, *, directory, access):
        return self.kernel.CreateFileW(safe._native_path(path), access, 3, None, 3,
            0x00200000 | (0x02000000 if directory else 0), None)

    def setup(self, path, *, directory, owner=None, mask="FA"):
        """Only fresh test objects; fixture creation starts with TEMP full access."""
        owner = self.user_sid if owner is None else owner
        descriptor = wintypes.LPVOID()
        inheritance = "OICI" if directory else ""
        sddl = f"O:{owner}D:P(A;{inheritance};{mask};;;{self.user_sid})(A;{inheritance};FA;;;SY)"
        assert self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None)
        handle = self.open(path, directory=directory, access=READ_CONTROL | WRITE_DAC | WRITE_OWNER)
        assert handle != ctypes.c_void_p(-1).value, "Fixture object did not start with TEMP full access"
        try:
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
            owner_ptr = wintypes.LPVOID()
            assert self.advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                ctypes.byref(dacl), ctypes.byref(defaulted)) and present.value and dacl.value
            assert self.advapi.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner_ptr), ctypes.byref(defaulted))
            assert self.advapi.SetSecurityInfo(handle, 1, 0x80000005, owner_ptr, None, dacl, None) == 0
        finally:
            self.kernel.CloseHandle(handle)
            self.kernel.LocalFree(descriptor)

    def set_owner(self, path, *, directory, owner):
        descriptor = wintypes.LPVOID()
        assert self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "O:" + owner, 1, ctypes.byref(descriptor), None)
        handle = self.open(path, directory=directory, access=READ_CONTROL | WRITE_OWNER)
        assert handle != ctypes.c_void_p(-1).value
        try:
            owner_ptr, defaulted = wintypes.LPVOID(), wintypes.BOOL()
            assert self.advapi.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner_ptr), ctypes.byref(defaulted))
            assert self.advapi.SetSecurityInfo(handle, 1, 1, owner_ptr, None, None, None) == 0
        finally:
            self.kernel.CloseHandle(handle)
            self.kernel.LocalFree(descriptor)

    def observe(self, path, *, directory):
        handle = self.open(path, directory=directory, access=READ_CONTROL)
        assert handle != ctypes.c_void_p(-1).value
        descriptor = wintypes.LPVOID()
        try:
            assert self.advapi.GetSecurityInfo(handle, 1, 5, None, None, None, None, ctypes.byref(descriptor)) == 0
            owner, defaulted, text = wintypes.LPVOID(), wintypes.BOOL(), wintypes.LPWSTR()
            assert self.advapi.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted))
            owner_sid = self.sid_text(owner)
            assert self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 4, ctypes.byref(text), None)
            try:
                return owner_sid, text.value
            finally:
                self.kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)
            self.kernel.CloseHandle(handle)

    def assert_private(self, path, *, directory):
        owner, dacl = self.observe(path, directory=directory)
        assert owner == self.user_sid and dacl.startswith("D:P")
        aces = re.findall(r"\(([^()]*)\)", dacl)
        assert len(aces) == 2
        parts = [ace.split(";") for ace in aces]
        principal = "SY" if self.user_sid == "S-1-5-18" else self.user_sid
        assert {ace[5] for ace in parts} == {principal, "SY"}
        assert all(ace[0] == "A" and ace[2] in {"FA", "0x1f01ff"} for ace in parts)


@pytest.fixture
def security():
    return WindowsSecurity()


@pytest.mark.parametrize("directory", [True, False])
@pytest.mark.parametrize("inherited", [False, True])
def test_fresh_current_owner_with_only_modify_is_initialized_in_two_physical_stages(
        tmp_path, monkeypatch, security, directory, inherited):
    if security.user_sid == "S-1-5-18":
        pytest.skip("SYSTEM FullControl cannot be separated from the current SYSTEM principal")
    parent = tmp_path / "modify-parent"
    parent.mkdir()
    security.setup(parent, directory=True)
    path = parent / "empty-object"
    path.mkdir() if directory else path.touch()
    if inherited:
        # Establish the source scenario: a current-owned empty child inherits
        # Modify, without a protected ACL or any explicit FullControl ACE.
        security.set_owner(path, directory=directory, owner=security.user_sid)
        security.setup(parent, directory=True, mask=MODIFY)
        owner, dacl = security.observe(path, directory=directory)
        assert owner == security.user_sid and "P" not in dacl.split("(", 1)[0]
    else:
        security.setup(path, directory=directory, mask=MODIFY)
    original = path.stat()
    old_handle = security.open(path, directory=directory, access=READ_CONTROL | WRITE_DAC | WRITE_OWNER)
    if old_handle != ctypes.c_void_p(-1).value:
        security.kernel.CloseHandle(old_handle)
        pytest.skip("This process already has ownership privileges beyond the Modify-only DACL")
    assert ctypes.get_last_error() == 5  # Native ERROR_ACCESS_DENIED, not a synthetic opener.
    observed = []
    class TrackingKernel:
        def __getattr__(self, name):
            return getattr(security.kernel, name)
        def CreateFileW(self, path, access, *arguments):
            observed.append(access)
            return security.kernel.CreateFileW(path, access, *arguments)
    monkeypatch.setattr(safe, "_kernel", TrackingKernel())
    files._windows_acl(path, directory=directory, initialize=True)
    assert observed == [READ_CONTROL | WRITE_DAC, READ_CONTROL | WRITE_OWNER]
    security.assert_private(path, directory=directory)
    files.require_private(path, directory=directory)
    final = path.stat()
    assert (final.st_dev, final.st_ino, final.st_size) == (original.st_dev, original.st_ino, original.st_size)
    if not directory:
        assert path.read_bytes() == b""


@pytest.mark.parametrize("directory", [True, False])
def test_fresh_actual_token_default_owner_is_rebound_to_the_actual_user(tmp_path, security, directory):
    path = tmp_path / "default-owner-object"
    path.mkdir() if directory else path.touch()
    security.setup(path, directory=directory, owner=security.default_owner_sid)
    assert security.observe(path, directory=directory)[0] == security.default_owner_sid
    original = path.stat()
    files._windows_acl(path, directory=directory, initialize=True)
    security.assert_private(path, directory=directory)
    files.require_private(path, directory=directory)
    assert (path.stat().st_dev, path.stat().st_ino) == (original.st_dev, original.st_ino)


@pytest.mark.parametrize("directory", [True, False])
def test_read_only_privacy_check_does_not_repair_existing_modify_acl(tmp_path, security, directory):
    path = tmp_path / "existing-object"
    path.mkdir() if directory else path.touch()
    security.setup(path, directory=directory, mask=MODIFY)
    before = security.observe(path, directory=directory)
    with pytest.raises(files.PreservationFileError) as caught:
        files.require_private(path, directory=directory)
    assert caught.value.code == "PERSONAL_PRESERVATION_PRIVATE_ACL_UNPROVEN"
    assert security.observe(path, directory=directory) == before


def test_existing_group_owner_is_rejected_without_repair(tmp_path, security):
    if security.default_owner_sid == security.user_sid:
        pytest.skip("This token's actual default owner is the user, not a distinct owner group")
    path = tmp_path / "existing-group-owner"
    path.mkdir()
    security.setup(path, directory=True, owner=security.default_owner_sid)
    before = security.observe(path, directory=True)
    with pytest.raises(files.PreservationFileError) as caught:
        files.require_private(path, directory=True)
    assert caught.value.code == "PERSONAL_PRESERVATION_PRIVATE_OWNER_UNPROVEN"
    assert security.observe(path, directory=True) == before


def test_initialization_refuses_a_hardlinked_object_before_changing_security(tmp_path, security):
    path, alias = tmp_path / "empty-file", tmp_path / "other-name"
    path.touch()
    try:
        os.link(path, alias)
    except OSError as exc:
        pytest.skip(f"Filesystem cannot create this hardlink: {exc.errno}")
    before = security.observe(path, directory=False)
    with pytest.raises(files.PreservationFileError) as caught:
        files._windows_acl(path, directory=False, initialize=True)
    assert caught.value.code == "PERSONAL_PRESERVATION_FILE_UNSAFE"
    assert security.observe(path, directory=False) == before
    assert path.stat().st_ino == alias.stat().st_ino and path.read_bytes() == b""
