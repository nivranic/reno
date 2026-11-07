"""Pure simulated directory lifetime: no real handles, files, or native calls."""
import errno
import os
from pathlib import Path

import pytest

from reno import personal_authority_preservation_source as source


@pytest.fixture
def owned(monkeypatch):
    path = Path("C:/reno-memory-only") if os.name == "nt" else Path("/reno-memory-only")
    directory = source._OwnedDirectory(path)
    directory._raw_opening = False
    directory.handles = [101, 102]
    directory.fd = 102
    closed = []
    def close(handle):
        closed.append(handle)
        return True
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    return directory, closed


@pytest.mark.parametrize("operation", ["open_file", "entry_stat", "names", "unlink"])
def test_closed_directory_rejects_inherited_io_before_any_path_backend(owned, monkeypatch, operation):
    directory, closed = owned
    directory.__exit__(None, None, None)
    reached = []
    def forbidden(*_args, **_kwargs):
        reached.append(True)
        raise AssertionError("closed directory reached a path backend")
    with monkeypatch.context() as boundary:
        boundary.setattr(source.safe, "_entry_name", forbidden)
        boundary.setattr(source.safe.os, "listdir", forbidden)
        with pytest.raises(OSError) as raised:
            if operation == "names":
                directory.names()
            else:
                getattr(directory, operation)("child")
    assert raised.value.errno == errno.EBADF
    assert not reached and closed == [102, 101]


def test_closed_directory_cannot_be_used_as_a_child_parent(owned, monkeypatch):
    directory, _closed = owned
    directory.__exit__(None, None, None)
    child = source.safe._Directory(directory.path / "child", parent=directory)
    reached = []
    def forbidden(*_args, **_kwargs):
        reached.append(True)
        raise AssertionError("closed parent reached filesystem stat")
    with monkeypatch.context() as boundary:
        boundary.setattr(source.safe.os, "stat", forbidden)
        with pytest.raises(OSError) as raised:
            child.__enter__()
    assert raised.value.errno == errno.EBADF and not reached


def test_retirement_and_detach_precede_all_closers_and_prevent_reentrant_double_close(owned, monkeypatch):
    directory, _closed = owned
    calls = []
    def close(handle):
        assert directory._known_closed and directory.fd is None and directory.handles == []
        calls.append(handle)
        if len(calls) == 1:
            assert directory.__exit__(None, None, None) is False
        return True
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    assert directory.__exit__(None, None, None) is False
    assert calls == [102, 101] and directory._close_failure is None


@pytest.mark.parametrize("has_primary", [False, True])
def test_all_handles_close_and_first_close_error_is_retained_without_replacing_primary(owned, monkeypatch, has_primary):
    directory, _closed = owned
    first, later, primary = OSError("first synthetic close"), OSError("later synthetic close"), RuntimeError("original primary")
    calls = []
    def close(handle):
        calls.append(handle)
        raise first if handle == 102 else later
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    if has_primary:
        with pytest.raises(RuntimeError) as raised:
            try:
                raise primary
            except RuntimeError:
                if not directory.__exit__(type(primary), primary, None):
                    raise
        assert raised.value is primary
    else:
        with pytest.raises(OSError) as raised:
            directory.__exit__(None, None, None)
        assert raised.value is first
    assert calls == [102, 101] and directory._close_failure is first


def test_failed_exit_is_not_retried_and_repeated_exit_keeps_the_original_close_failure(owned, monkeypatch):
    directory, _closed = owned
    first, calls = OSError("one uncertain numeric close"), []
    def close(handle):
        calls.append(handle)
        if handle == 102:
            raise first
        return True
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    for _ in range(2):
        with pytest.raises(OSError) as raised:
            directory.__exit__(None, None, None)
        assert raised.value is first
    assert calls == [102, 101]


def test_failed_directory_cannot_reenter_or_reach_a_path_backend(owned, monkeypatch):
    directory, _closed = owned
    first = OSError("synthetic earlier close")
    directory._close_failure = first
    reached = []
    def forbidden(*_args, **_kwargs):
        reached.append(True)
        raise AssertionError("failed directory attempted filesystem entry")
    with monkeypatch.context() as boundary:
        boundary.setattr(source.safe.os, "stat", forbidden)
        with pytest.raises(OSError) as raised:
            directory.__enter__()
    assert raised.value is first and not reached


def test_primary_note_failure_cannot_mask_primary_or_skip_remaining_closes(owned, monkeypatch):
    directory, _closed = owned
    class Primary(RuntimeError):
        def add_note(self, _text):
            raise RuntimeError("synthetic optional annotation failure")
    primary, first, calls = Primary("primary"), OSError("close"), []
    def close(handle):
        calls.append(handle)
        raise first
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    assert directory.__exit__(type(primary), primary, None) is False
    assert calls == [102, 101] and directory._close_failure is first


def test_first_close_note_failure_cannot_skip_remaining_closes(owned, monkeypatch):
    directory, _closed = owned
    class CloseFailure(OSError):
        def add_note(self, _text):
            raise RuntimeError("synthetic optional annotation failure")
    first, calls = CloseFailure("first close"), []
    def close(handle):
        calls.append(handle)
        raise first if handle == 102 else OSError("later close")
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    with pytest.raises(CloseFailure) as raised:
        directory.__exit__(None, None, None)
    assert raised.value is first and calls == [102, 101]


def test_partial_opening_preserves_the_current_primary_without_requiring_traceback_access(owned, monkeypatch):
    directory, _closed = owned
    primary, first, calls = RuntimeError("opening primary"), OSError("partial close"), []
    directory._raw_opening = True
    def close(handle):
        calls.append(handle)
        raise first
    monkeypatch.setattr(source, "_close_directory_handle_original", close)
    with pytest.raises(RuntimeError) as raised:
        try:
            raise primary
        except RuntimeError:
            if not directory.__exit__(None, None, None):
                raise
    assert raised.value is primary and directory._close_failure is first and calls == [102, 101]


def test_empty_repeated_successful_exit_performs_no_native_close(owned):
    directory, closed = owned
    directory.handles = []
    assert directory.__exit__(None, None, None) is False
    assert directory.__exit__(None, None, None) is False
    assert directory._known_closed and directory.fd is None and not closed
