"""Partial Namespace leaves retain a real guard's file and custody checks.

These tests prove only the lifecycle leaves. They do not install or fabricate
the Namespace owner/context that must bracket them with directory sweeps.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context, copy_context
import os
import sqlite3

import pytest

from reno import storage_lifecycle as lifecycle


def _profile(root):
    return lifecycle.StorageProfile(root / "data/reno.db", root / "media",
                                    root / "reports", root / "checkpoints")


def _native(path):
    return lifecycle.safe._native_path(path)


@pytest.fixture
def profiles(tmp_path):
    result = (_profile(tmp_path / "first"), _profile(tmp_path / "second"))
    for profile in result:
        lifecycle.initialize(profile)
        with sqlite3.connect(profile.database) as connection:
            connection.execute("CREATE TABLE synthetic_entry (id INTEGER PRIMARY KEY)")
        connection.close()
    return result


@pytest.fixture
def held(profiles):
    with lifecycle.shared_many(profiles, timeout=0) as guard:
        plan = lifecycle._capture_guard_file_plan_original(guard)
        assert plan is not None
        yield guard, plan


class _ChangedStat:
    def __init__(self, original, **changes):
        self._original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _assert_code(code, call):
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        call()
    assert caught.value.code == code


def test_ordinary_require_guard_remains_full_after_plan_capture(profiles, held, monkeypatch):
    guard, plan = held
    lifecycle._verify_guard_plan_directories_original(plan)
    lifecycle._verify_guard_plan_files_original(plan, profiles[0])
    target = _native(profiles[0].database.parent)
    original_stat = os.stat
    observations = []

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            observations.append(path)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", changed_stat)
        # The leaf alone deliberately grants no complete directory proof.
        lifecycle._verify_guard_plan_files_original(plan, profiles[0])
        assert observations == []
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle.require_guard(profiles[0]))
    assert observations and guard._active


def test_each_file_leaf_reads_every_lock_and_every_covered_database(profiles, held, monkeypatch):
    guard, plan = held
    descriptors = {lock.stream.fileno(): index for index, lock in enumerate(guard._locks)}
    entries = {}
    for index, lock in enumerate(guard._locks):
        key = (_native(lock.directory.path / lifecycle.LOCK_NAME), None) if os.name == "nt" else (
            lifecycle.LOCK_NAME, lock.directory.fd)
        entries[key] = ("entry", index)
    directories = {lock.directory.path: lock.directory for lock in guard._locks}
    for index, profile in enumerate(profiles):
        key = (_native(profile.database), None) if os.name == "nt" else (
            profile.database.name, directories[profile.database.parent].fd)
        entries[key] = ("database", index)
    original_stat, original_fstat, original_read = os.stat, os.fstat, os.read
    events = []

    def observe_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        event = entries.get((path, kwargs.get("dir_fd")))
        if event is not None:
            assert kwargs.get("follow_symlinks") is False
            events.append(event)
        return result

    def observe_fstat(descriptor):
        result = original_fstat(descriptor)
        if descriptor in descriptors:
            events.append(("fstat", descriptors[descriptor]))
        return result

    def observe_read(descriptor, size):
        result = original_read(descriptor, size)
        if descriptor in descriptors:
            events.append(("marker", descriptors[descriptor]))
            assert size == len(lifecycle._MARKER) + 1 and result == lifecycle._MARKER
        return result

    expected = [(kind, index) for index in range(len(guard._locks))
                for kind in ("entry", "fstat", "marker", "entry", "fstat")]
    expected += [("database", index) for index in range(len(profiles))]
    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        patch.setattr(os, "fstat", observe_fstat)
        patch.setattr(os, "read", observe_read)
        for _ in range(2):
            events.clear()
            lifecycle._verify_guard_plan_files_original(plan, profiles[0])
            assert events == expected


def test_request_for_first_profile_rejects_unsafe_other_database(profiles, held, monkeypatch):
    guard, plan = held
    other = profiles[1]
    directory = next(lock.directory for lock in guard._locks if lock.directory.path == other.database.parent)
    target = (_native(other.database), None) if os.name == "nt" else (other.database.name, directory.fd)
    original_stat = os.stat
    observations = []

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if (path, kwargs.get("dir_fd")) == target:
            observations.append(path)
            return _ChangedStat(result, st_nlink=2)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", changed_stat)
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))
    assert observations


def test_each_file_leaf_rejects_changed_last_marker(profiles, held):
    guard, plan = held
    last = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
    lifecycle._verify_guard_plan_files_original(plan, profiles[0])
    with last.open("r+b", buffering=0) as writer:
        try:
            # Only byte zero is lifetime-locked on Windows; retain that lock,
            # this file's identity and all original handles while changing data.
            writer.seek(1)
            assert writer.write(b"!") == 1
            _assert_code("STORAGE_LOCK_INVALID", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))
        finally:
            writer.seek(1)
            assert writer.write(lifecycle._MARKER[1:2]) == 1


@pytest.mark.parametrize("kind", ["nonmember", "exclusive", "invalid_exclusive"])
def test_file_leaf_retains_membership_and_exclusive_requirements(profiles, held, tmp_path, kind):
    _, plan = held
    if kind == "nonmember":
        call = lambda: lifecycle._verify_guard_plan_files_original(plan, _profile(tmp_path / "unheld"))
        code = "STORAGE_PROFILE_MISMATCH"
    elif kind == "exclusive":
        call = lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0], exclusive=True)
        code = "STORAGE_LOCK_UPGRADE"
    else:
        call = lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0], exclusive="yes")
        code = "STORAGE_INVALID_PROFILE"
    _assert_code(code, call)


def test_exclusive_file_leaf_uses_actual_exclusive_guard(profiles):
    with lifecycle.exclusive_many(profiles, timeout=0) as guard:
        plan = lifecycle._capture_guard_file_plan_original(guard)
        lifecycle._verify_guard_plan_directories_original(plan)
        lifecycle._verify_guard_plan_files_original(plan, profiles[0], exclusive=True)
        lifecycle._verify_guard_plan_directories_original(plan)


def test_file_leaf_requires_current_context(profiles, held):
    _, plan = held
    _assert_code("STORAGE_GUARD_REQUIRED", lambda: Context().run(
        lifecycle._verify_guard_plan_files_original, plan, profiles[0]))


def test_copied_context_in_another_thread_does_not_transfer_guard(profiles, held):
    _, plan = held
    context = copy_context()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(context.run, lifecycle._verify_guard_plan_files_original, plan, profiles[0])
        _assert_code("STORAGE_GUARD_CONTEXT", lambda: future.result(timeout=5))


def test_plan_does_not_keep_closed_guard_alive(profiles):
    with lifecycle.shared_many(profiles, timeout=0) as guard:
        plan = lifecycle._capture_guard_file_plan_original(guard)
    assert not guard._active
    _assert_code("STORAGE_GUARD_REQUIRED", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))


@pytest.mark.parametrize("change", ["directory", "stream", "profile", "profiles_tuple", "locks_tuple",
                                    "handles_list", "identities_list", "lock_identity", "lock_state"])
def test_file_plan_rejects_replaced_shape_without_adopting_it(profiles, held, monkeypatch, change):
    guard, plan = held
    first, second = guard._locks[:2]
    with monkeypatch.context() as patch:
        original_media = profiles[0].media
        try:
            if change == "directory":
                patch.setattr(first, "directory", second.directory)
            elif change == "stream":
                patch.setattr(first, "stream", second.stream)
            elif change == "profile":
                object.__setattr__(profiles[0], "media", original_media / "changed")
            elif change == "profiles_tuple":
                patch.setattr(guard, "_profiles", tuple(list(guard._profiles)))
            elif change == "locks_tuple":
                patch.setattr(guard, "_locks", tuple(list(guard._locks)))
            elif change == "handles_list":
                patch.setattr(first.directory, "handles", list(first.directory.handles))
            elif change == "identities_list":
                patch.setattr(first.directory, "identities", list(first.directory.identities))
            elif change == "lock_identity":
                patch.setattr(first, "identity", (first.identity[0], first.identity[1] + 1))
            else:
                patch.setattr(first, "locked", False)
            _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))
        finally:
            if change == "profile":
                object.__setattr__(profiles[0], "media", original_media)
    lifecycle._verify_guard_plan_files_original(plan, profiles[0])


@pytest.mark.parametrize("callback", ["guard", "lock", "lock_entry", "directory", "entry", "fileno"])
def test_custom_dispatch_capture_defers_to_ordinary_full_guard(profiles, held, monkeypatch, callback):
    guard, _ = held
    lock = guard._locks[0]
    owner, name = {
        "guard": (guard, "verify"), "lock": (lock, "verify"),
        "lock_entry": (lock, "_verify_entry_stat"), "directory": (lock.directory, "verify"),
        "entry": (lock.directory, "entry_stat"), "fileno": (lock.stream, "fileno"),
    }[callback]
    original = getattr(owner, name)
    target = _native(lock.directory.path)
    original_stat = os.stat
    calls = []

    def custom(*args, **kwargs):
        calls.append(name)
        return original(*args, **kwargs)

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        return _ChangedStat(result, st_ino=result.st_ino + 1) if path == target else result

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, custom)
        assert lifecycle._capture_guard_file_plan_original(guard) is None
        assert calls == []
        assert lifecycle.require_guard(profiles[0]) is guard
        assert calls
        patch.setattr(os, "stat", changed_stat)
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle.require_guard(profiles[0]))


def test_new_directory_sweep_observes_changes_between_phases(profiles, held, monkeypatch):
    _, plan = held
    target = _native(profiles[0].database.parent)
    original_stat = os.stat
    phase = 0
    observations = []

    def observe_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            observations.append(phase)
            if phase:
                return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        lifecycle._verify_guard_plan_directories_original(plan)
        phase = 1
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_directories_original(plan))
    assert observations == [0, 1]


def test_state_leaf_performs_no_physical_io_and_returns_no_authority(profiles, held, monkeypatch):
    _, plan = held

    def unexpected_io(*args, **kwargs):
        raise AssertionError("State-only check attempted physical I/O")

    with monkeypatch.context() as patch:
        for name in ("stat", "fstat", "read", "lseek", "open"):
            patch.setattr(os, name, unexpected_io)
        assert lifecycle._verify_guard_plan_state_original(plan, profiles[0]) is None


def test_state_leaf_cannot_replace_full_tail_or_ordinary_guard(profiles, held):
    guard, plan = held
    marker = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
    with marker.open("r+b", buffering=0) as writer:
        try:
            writer.seek(1)
            assert writer.write(b"!") == 1
            assert lifecycle._verify_guard_plan_state_original(plan, profiles[0]) is None
            _assert_code("STORAGE_LOCK_INVALID", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))
            _assert_code("STORAGE_LOCK_INVALID", lambda: lifecycle.require_guard(profiles[0]))
        finally:
            writer.seek(1)
            assert writer.write(lifecycle._MARKER[1:2]) == 1


@pytest.mark.parametrize("boundary", ["membership", "exclusive", "invalid_exclusive", "invalid_profile", "context", "thread"])
def test_state_leaf_keeps_live_logical_boundaries(profiles, held, tmp_path, boundary):
    _, plan = held
    check = lifecycle._verify_guard_plan_state_original
    if boundary == "membership":
        _assert_code("STORAGE_PROFILE_MISMATCH", lambda: check(plan, _profile(tmp_path / "unheld")))
    elif boundary == "exclusive":
        _assert_code("STORAGE_LOCK_UPGRADE", lambda: check(plan, profiles[0], exclusive=True))
    elif boundary == "invalid_exclusive":
        _assert_code("STORAGE_INVALID_PROFILE", lambda: check(plan, profiles[0], exclusive="yes"))
    elif boundary == "invalid_profile":
        _assert_code("STORAGE_INVALID_PROFILE", lambda: check(plan, object()))
    elif boundary == "context":
        _assert_code("STORAGE_GUARD_REQUIRED", lambda: Context().run(check, plan, profiles[0]))
    else:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, check, plan, profiles[0])
            _assert_code("STORAGE_GUARD_CONTEXT", lambda: future.result(timeout=5))


def test_state_leaf_does_not_keep_closed_guard_alive(profiles):
    with lifecycle.shared_many(profiles, timeout=0) as guard:
        plan = lifecycle._capture_guard_file_plan_original(guard)
    _assert_code("STORAGE_GUARD_REQUIRED", lambda: lifecycle._verify_guard_plan_state_original(plan, profiles[0]))


@pytest.mark.parametrize("change", ["directory", "stream", "profile", "profiles_tuple", "identity_list"])
def test_state_leaf_does_not_adopt_changed_plan_shape(profiles, held, monkeypatch, change):
    guard, plan = held
    first, second = guard._locks[:2]
    original_media = profiles[0].media
    with monkeypatch.context() as patch:
        try:
            if change == "directory":
                patch.setattr(first, "directory", second.directory)
            elif change == "stream":
                patch.setattr(first, "stream", second.stream)
            elif change == "profile":
                object.__setattr__(profiles[0], "media", original_media / "changed")
            elif change == "profiles_tuple":
                patch.setattr(guard, "_profiles", tuple(list(guard._profiles)))
            else:
                patch.setattr(first.directory, "identities", list(first.directory.identities))
            _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_state_original(plan, profiles[0]))
        finally:
            if change == "profile":
                object.__setattr__(profiles[0], "media", original_media)
    lifecycle._verify_guard_plan_state_original(plan, profiles[0])


def test_custom_raw_fileno_is_not_called_by_admission_or_state_but_full_dispatch_remains(profiles, held, monkeypatch):
    guard, plan = held
    raw = guard._locks[0].stream.raw
    original = raw.fileno
    calls = []

    def custom_fileno():
        calls.append("raw.fileno")
        return original()

    with monkeypatch.context() as patch:
        patch.setattr(raw, "fileno", custom_fileno)
        assert lifecycle._capture_guard_file_plan_original(guard) is None
        assert calls == []
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_state_original(plan, profiles[0]))
        _assert_code("STORAGE_UNSAFE_PATH", lambda: lifecycle._verify_guard_plan_files_original(plan, profiles[0]))
        assert calls == []
        assert lifecycle.require_guard(profiles[0]) is guard
        assert calls
    lifecycle._verify_guard_plan_state_original(plan, profiles[0])
