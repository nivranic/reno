"""Real Windows directory acquisition with explicit close-result fault seams.

Windows faults first close the actual owned native handle, then substitute the
reported BOOL or raise an exact exception object. This tests caller handling,
not a real failed CloseHandle or a claim that an unknown failed handle closed.
The single POSIX case uses synthetic descriptor values and is not native POSIX
evidence. No lifecycle grant, source observation, or accepted DTO is invented.
"""
from contextlib import contextmanager, ExitStack
import errno
import os
from types import SimpleNamespace

import pytest

from reno import sync_media as media


class KernelFacade:
    """Consumer-local facade; never replace a native DLL function object."""
    def __init__(self, original, native_ctypes):
        self.original, self.ctypes = original, native_ctypes
        self.faults, self.events, self.calls = {}, [], []
        self.closed = set()

    def __getattr__(self, name):
        return getattr(self.original, name)

    def CloseHandle(self, handle):
        index = len(self.calls)
        self.calls.append(handle)
        self.events.append(("close", handle))
        result = self.original.CloseHandle(handle)
        assert result, "Fault seam could not close its actually acquired native handle"
        self.closed.add(handle)
        selected = self.faults.get(index)
        if isinstance(selected, BaseException):
            raise selected
        if selected is not None:
            kind, code = selected
            assert kind == "false"
            self.ctypes.set_last_error(code)
            return 0
        # A later success deliberately overwrites the thread-local error slot.
        # The source must already have captured any preceding false BOOL code.
        self.ctypes.set_last_error(4321)
        return result


class CtypesFacade:
    def __init__(self, original, events):
        self.original, self.events = original, events

    def __getattr__(self, name):
        return getattr(self.original, name)

    def get_last_error(self):
        result = self.original.get_last_error()
        self.events.append(("last_error", result))
        return result


@pytest.fixture
def windows_tree(tmp_path):
    if os.name != "nt":
        pytest.skip("This acquisition and CloseHandle group requires native Windows")
    root = tmp_path / "owned-directory-root"
    path = root / "first" / "second"
    path.mkdir(parents=True)
    (path / "owned-marker.bin").write_bytes(b"retained")
    return root, path


@contextmanager
def native_owner(tree, monkeypatch):
    root, path = tree
    original_kernel, native_ctypes = media._kernel, media.ctypes
    directory = media._Directory(path, base=root)
    assert directory.__enter__() is directory
    handles = tuple(directory.handles)
    assert len(handles) == 3 and len(set(handles)) == 3
    facade = KernelFacade(original_kernel, native_ctypes)
    with monkeypatch.context() as patch:
        patch.setattr(media, "_kernel", facade)
        patch.setattr(media, "ctypes", CtypesFacade(native_ctypes, facade.events))
        try:
            yield directory, handles, facade
        finally:
            # Only still-unattempted true acquisitions need test cleanup. Failed
            # reported values are never retried: the seam already closed them.
            for handle in reversed(handles):
                if handle not in facade.calls:
                    assert original_kernel.CloseHandle(handle)


def assert_full_reverse_attempts(handles, facade):
    assert facade.calls == list(reversed(handles))
    assert facade.closed == set(handles)


def assert_false_error(error, winerror):
    # Windows maps winerror into an errno and may select PermissionError.
    # Retained native error code and fixed safe message are the contract.
    assert isinstance(error, OSError)
    assert error.winerror == winerror
    assert error.strerror == "Media directory handle close failed"


def assert_successfully_closed_file_operations_reject_io(directory, monkeypatch, os_name):
    attempts = []
    child = media._Directory(directory.path / "never-created-child", base=directory.path,
                             parent=directory)

    def unexpected(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Successfully closed owner reached filesystem I/O")

    with monkeypatch.context() as patch:
        patch.setattr(media, "os", SimpleNamespace(name=os_name, stat=unexpected,
            listdir=unexpected, unlink=unexpected, open=unexpected, close=unexpected))
        patch.setattr(media, "_win_open", unexpected, raising=False)
        operations = [lambda: directory.entry_stat("owned-marker.bin"), directory.names,
                      lambda: directory.unlink("owned-marker.bin"),
                      lambda: directory.open_file("owned-marker.bin"), child.__enter__]
        for operation in operations:
            with pytest.raises(OSError) as caught:
                operation()
            assert caught.value.errno == errno.EBADF
            assert caught.value.strerror == "Media directory is closed"
            assert caught.value.filename is None
    assert attempts == []


def test_actual_windows_normal_close_retires_every_handle_and_is_repeat_safe(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        assert directory.__exit__(None, None, None) is None
        assert_full_reverse_attempts(handles, facade)
        assert directory.handles == [] and directory.fd is None
        assert directory.__exit__(None, None, None) is None
        assert_full_reverse_attempts(handles, facade)
        assert_successfully_closed_file_operations_reject_io(directory, monkeypatch, "nt")
        assert directory.__enter__() is directory
        reopened = tuple(directory.handles)
        try:
            assert len(reopened) == 3
            assert directory.entry_stat("owned-marker.bin").st_size == len(b"retained")
        finally:
            assert directory.__exit__(None, None, None) is None
        assert facade.calls == list(reversed(handles)) + list(reversed(reopened))
        assert directory.handles == [] and directory.fd is None


def test_false_bool_captures_last_error_before_next_native_close(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        facade.faults[0] = ("false", 5)
        with pytest.raises(OSError) as caught:
            directory.__exit__(None, None, None)
        assert_false_error(caught.value, 5)
        assert_full_reverse_attempts(handles, facade)
        assert facade.events[:3] == [("close", handles[-1]), ("last_error", 5), ("close", handles[-2])]
        assert directory.fd is None
        assert directory.handles == [handles[-1]]


def test_first_false_bool_wins_while_later_raise_and_false_are_still_attempted(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        later = SystemExit(71)
        facade.faults = {0: ("false", 6), 1: later, 2: ("false", 87)}
        with pytest.raises(OSError) as caught:
            directory.__exit__(None, None, None)
        assert_false_error(caught.value, 6)
        assert_full_reverse_attempts(handles, facade)
        assert [(kind, value) for kind, value in facade.events if kind == "last_error"] == [
            ("last_error", 6), ("last_error", 87)]
        assert directory.handles == list(handles) and directory.fd is None


def test_first_raised_native_exception_keeps_identity_and_closes_other_handles(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        first = OSError("synthetic native raised failure")
        facade.faults = {0: first, 1: ("false", 5)}
        with pytest.raises(OSError) as caught:
            directory.__exit__(None, None, None)
        assert caught.value is first
        assert_full_reverse_attempts(handles, facade)
        assert directory.handles == list(handles[-2:]) and directory.fd is None


def test_body_control_object_survives_false_bool_cleanup_with_fixed_note(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        facade.faults = {0: ("false", 5)}
        original = SystemExit(29)
        with pytest.raises(SystemExit) as caught:
            with ExitStack() as stack:
                stack.push(directory)  # Own only __exit__; acquisition above was real.
                raise original
        assert caught.value is original
        assert "SYNC_MEDIA_DIRECTORY_CLOSE_FAILED" in original.__notes__
        assert_full_reverse_attempts(handles, facade)
        assert directory.fd is None


def test_body_original_error_survives_raised_cleanup_control(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        facade.faults = {0: SystemExit(93), 1: OSError("synthetic later close")}
        original = RuntimeError("synthetic original body error")
        with pytest.raises(RuntimeError) as caught:
            with ExitStack() as stack:
                stack.push(directory)
                raise original
        assert caught.value is original
        assert "SYNC_MEDIA_DIRECTORY_CLOSE_FAILED" in original.__notes__
        assert_full_reverse_attempts(handles, facade)


@pytest.mark.parametrize("original", [OSError("synthetic entry verification"), SystemExit(47)],
                         ids=["oserror", "systemexit"])
def test_enter_failure_keeps_original_object_while_cleanup_attempts_all_owned_handles(
        windows_tree, monkeypatch, original):
    root, path = windows_tree
    native_kernel, native_ctypes, actual_open = media._kernel, media.ctypes, media._win_open
    opened = []
    facade = KernelFacade(native_kernel, native_ctypes)
    facade.faults = {0: ("false", 5), 1: RuntimeError("synthetic entry cleanup")}

    def tracked_open(*args, **kwargs):
        handle = actual_open(*args, **kwargs)
        opened.append(handle)
        return handle

    class VerifyFailureDirectory(media._Directory):
        def verify(self):
            super().verify()
            if len(self.handles) == 3:
                raise original

    with monkeypatch.context() as patch:
        patch.setattr(media, "_kernel", facade)
        patch.setattr(media, "ctypes", CtypesFacade(native_ctypes, facade.events))
        patch.setattr(media, "_win_open", tracked_open)
        directory = VerifyFailureDirectory(path, base=root)
        try:
            with pytest.raises(type(original)) as caught:
                directory.__enter__()
            assert caught.value is original
            assert "SYNC_MEDIA_DIRECTORY_CLOSE_FAILED" in original.__notes__
            assert len(opened) == 3
            assert_full_reverse_attempts(opened, facade)
            assert directory.fd is None
        finally:
            for handle in reversed(opened):
                if handle not in facade.calls:
                    assert native_kernel.CloseHandle(handle)


def test_failed_close_is_sticky_and_never_retries_values_while_new_owner_is_live(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        facade.faults[0] = ("false", 5)
        with pytest.raises(OSError) as first:
            directory.__exit__(None, None, None)
        assert_full_reverse_attempts(handles, facade)
        # A new real owner may receive reused numeric values. Never close any
        # remembered failed value again, even when another owner is live.
        new_owner = media._Directory(windows_tree[1], base=windows_tree[0])
        new_owner.__enter__()
        new_handles = tuple(new_owner.handles)
        try:
            with pytest.raises(OSError) as repeated:
                directory.__exit__(None, None, None)
            assert repeated.value is first.value
            assert_full_reverse_attempts(handles, facade)
            for handle in new_handles:
                info = media._HandleInfo()
                assert facade.original.GetFileInformationByHandle(handle, facade.ctypes.byref(info))
        finally:
            new_owner.__exit__(None, None, None)


def test_failed_owner_rejects_all_further_operations_before_any_io(windows_tree, monkeypatch):
    with native_owner(windows_tree, monkeypatch) as (directory, handles, facade):
        plan = media._directory_verification_plan([directory])
        assert plan is not None
        media._verify_directory_plan(plan)
        facade.faults[0] = ("false", 5)
        with pytest.raises(OSError) as caught:
            directory.__exit__(None, None, None)
        failure = caught.value
        attempts = []
        child = media._Directory(directory.path / "never-created-child", base=directory.path,
                                 parent=directory)

        def unexpected(*args, **kwargs):
            attempts.append((args, kwargs))
            raise AssertionError("Failed owner reached filesystem I/O")

        # POSIX mode is not selected here; this is a Windows consumer-local OS
        # namespace with all filesystem calls trapped after the actual close.
        with monkeypatch.context() as patch:
            patch.setattr(media, "os", SimpleNamespace(name="nt", stat=unexpected,
                listdir=unexpected, unlink=unexpected, open=unexpected, close=unexpected))
            operations = [directory.__enter__, directory.verify,
                          lambda: directory.entry_stat("synthetic-file"), directory.names,
                          lambda: directory.unlink("synthetic-file"),
                          lambda: directory.open_file("synthetic-file").__enter__(),
                          lambda: media._directory_verification_plan([directory]),
                          lambda: media._verify_directory_plan(plan), child.__enter__]
            for operation in operations:
                with pytest.raises(OSError) as blocked:
                    operation()
                assert blocked.value is failure
        assert attempts == []
        assert_full_reverse_attempts(handles, facade)


def test_synthetic_posix_close_exhausts_fake_descriptors_and_invalidates_fd(tmp_path, monkeypatch):
    directory = media._Directory(tmp_path)
    # Synthetic descriptor values only: no POSIX acquisition, os.close, or
    # platform-acceptance claim. Native Windows cases above remain separate.
    directory.handles.extend([101, 202, 303])
    directory.fd = 303
    calls = []
    first, later = OSError("synthetic POSIX first close"), SystemExit(57)

    def fake_close(descriptor):
        calls.append(descriptor)
        if descriptor == 303:
            raise first
        if descriptor == 202:
            raise later

    monkeypatch.setattr(media, "os", SimpleNamespace(name="posix", close=fake_close))
    with pytest.raises(OSError) as caught:
        directory.__exit__(None, None, None)
    assert caught.value is first
    assert calls == [303, 202, 101]
    assert directory.handles == [202, 303] and directory.fd is None
    with pytest.raises(OSError) as repeated:
        directory.__exit__(None, None, None)
    assert repeated.value is first and calls == [303, 202, 101]
    successful = media._Directory(tmp_path)
    successful.handles.extend([404, 505])
    successful.fd = 505
    assert successful.__exit__(None, None, None) is None
    assert calls == [303, 202, 101, 505, 404]
    assert successful.handles == [] and successful.fd is None
    assert_successfully_closed_file_operations_reject_io(successful, monkeypatch, "posix")
