"""Guard-local directory observations retain every live lock check."""
from collections import Counter
import os
import stat

import pytest

from reno import storage_lifecycle as lifecycle


def _profile(root):
    return lifecycle.StorageProfile(root / "db/reno.db", root / "media",
                                    root / "reports", root / "checkpoints")


def _native(path):
    return lifecycle.safe._native_path(path)


class _ChangedStat:
    """Delegate real stat fields except the one deliberately changed."""

    def __init__(self, original, **changes):
        self._original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self._original, name)


def test_verify_observes_each_directory_before_and_after_all_lock_markers(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        assert len(guard._locks) == 4
        paths = {_native(path) for lock in guard._locks
                 for path, _, _ in lock.directory.identities}
        descriptor_ids = {lock.stream.fileno(): index for index, lock in enumerate(guard._locks)}
        marker_paths = {_native(lock.directory.path / lifecycle.LOCK_NAME): index
                        for index, lock in enumerate(guard._locks)}
        directory_fds = {lock.directory.fd: index for index, lock in enumerate(guard._locks)}
        observations, events = Counter(), []
        original_stat, original_fstat, original_read = os.stat, os.fstat, os.read

        def observe_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path in paths:
                observations[path] += 1
                events.append(("directory", path))
                assert kwargs.get("follow_symlinks") is False
            if os.name == "nt" and path in marker_paths:
                events.append(("entry", marker_paths[path]))
            elif os.name != "nt" and path == lifecycle.LOCK_NAME:
                events.append(("entry", directory_fds[kwargs["dir_fd"]]))
            return result

        def observe_fstat(descriptor):
            result = original_fstat(descriptor)
            if descriptor in descriptor_ids:
                events.append(("fstat", descriptor_ids[descriptor]))
            return result

        def observe_read(descriptor, size):
            result = original_read(descriptor, size)
            if descriptor in descriptor_ids:
                events.append(("marker", descriptor_ids[descriptor]))
                assert size == len(lifecycle._MARKER) + 1
                assert result == lifecycle._MARKER
            return result

        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", observe_stat)
            patch.setattr(os, "fstat", observe_fstat)
            patch.setattr(os, "read", observe_read)
            assert guard.verify() is guard

        # The stream and entry proof still surrounds each individual marker.
        assert [event for event in events if event[0] != "directory"] == [
            (kind, index) for index in range(len(guard._locks))
            for kind in ("entry", "fstat", "marker", "entry", "fstat")
        ]
        assert observations == Counter({path: 2 for path in paths})
        first_marker = next(index for index, event in enumerate(events) if event[0] == "marker")
        last_marker = max(index for index, event in enumerate(events) if event[0] == "marker")
        assert Counter(value for kind, value in events[:first_marker] if kind == "directory") == Counter(paths)
        assert Counter(value for kind, value in events[last_marker + 1:] if kind == "directory") == Counter(paths)


def test_shared_path_observation_compares_every_expected_identity(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        directory = guard._locks[-1].directory
        assert all(any(path == tmp_path for path, _, _ in lock.directory.identities)
                   for lock in guard._locks)
        conflicting = [(path, device, inode + 1 if path == tmp_path else inode)
                       for path, device, inode in directory.identities]
        with monkeypatch.context() as patch:
            patch.setattr(directory, "identities", conflicting)
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                guard.verify()
        assert caught.value.code == "STORAGE_UNSAFE_PATH"


@pytest.mark.parametrize("change", ["identity", "unsafe_type", "reparse"])
def test_final_directory_observation_rejects_change_after_last_marker(tmp_path, monkeypatch, change):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        target = _native(tmp_path)
        last_descriptor = guard._locks[-1].stream.fileno()
        changed, rejected_observations = False, []
        original_stat, original_read = os.stat, os.read

        def observe_read(descriptor, size):
            nonlocal changed
            result = original_read(descriptor, size)
            if descriptor == last_descriptor:
                assert result == lifecycle._MARKER
                changed = True
            return result

        def observe_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == target and changed:
                rejected_observations.append(path)
                if change == "identity":
                    return _ChangedStat(result, st_ino=result.st_ino + 1)
                if change == "unsafe_type":
                    return _ChangedStat(result, st_mode=stat.S_IFREG | stat.S_IMODE(result.st_mode))
                return _ChangedStat(result, st_file_attributes=
                                    getattr(result, "st_file_attributes", 0) | lifecycle.safe._REPARSE)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", observe_stat)
            patch.setattr(os, "read", observe_read)
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                guard.verify()
        assert changed and rejected_observations
        assert caught.value.code == "STORAGE_UNSAFE_PATH"


def test_directory_observations_do_not_survive_a_verify_call(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        target = _native(tmp_path)
        changed = False
        original_stat = os.stat
        observations = []

        def observe_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == target:
                observations.append(changed)
                if changed:
                    return _ChangedStat(result, st_dev=result.st_dev + 1)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", observe_stat)
            assert guard.verify() is guard
            changed = True
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                guard.verify()
        assert False in observations and True in observations
        assert caught.value.code == "STORAGE_UNSAFE_PATH"


def test_nonfirst_lock_marker_bytes_are_still_read(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        last_lock = guard._locks[-1]
        descriptors = [lock.stream.fileno() for lock in guard._locks]
        marker_path = last_lock.directory.path / lifecycle.LOCK_NAME
        original_read = os.read
        reads = []

        def observe_read(descriptor, size):
            result = original_read(descriptor, size)
            if descriptor in descriptors:
                reads.append((descriptor, result))
            return result

        # The lifecycle lock covers byte zero on Windows. Change a later byte
        # through a real writer without replacing the pinned file or identity.
        with marker_path.open("r+b", buffering=0) as writer:
            try:
                writer.seek(1)
                assert writer.write(b"!") == 1
                writer.seek(1)
                assert writer.read(1) == b"!"
                with monkeypatch.context() as patch:
                    patch.setattr(os, "read", observe_read)
                    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                        guard.verify()
                assert caught.value.code == "STORAGE_LOCK_INVALID"
                assert [descriptor for descriptor, _ in reads] == descriptors
                assert all(value == lifecycle._MARKER for _, value in reads[:-1])
                assert reads[-1][1] != lifecycle._MARKER
            finally:
                writer.seek(1)
                assert writer.write(lifecycle._MARKER[1:2]) == 1


@pytest.mark.parametrize("kind", ["sync", "io"])
def test_custom_lock_verifier_preserves_original_exception(tmp_path, monkeypatch, kind):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    failure = (lifecycle.SyncError("synthetic custom verifier failure", "CUSTOM_VERIFY", 409)
               if kind == "sync" else OSError("synthetic custom verifier I/O failure"))
    with lifecycle.shared(profile, timeout=0) as guard:
        last_lock = guard._locks[-1]
        assert last_lock is not guard._locks[0]
        calls = []

        def custom_verify():
            calls.append(last_lock)
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(last_lock, "verify", custom_verify)
            with pytest.raises(Exception) as caught:
                guard.verify()
        assert calls == [last_lock]
        assert caught.value is failure


def test_custom_entry_callback_keeps_single_lock_directory_check_boundary(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        lock = guard._locks[0]
        target = _native(tmp_path)
        original_stat = os.stat
        original_entry = lock.directory.entry_stat

        def attempt(verify):
            changed = False
            entry_calls = 0
            changed_observations = []
            failure = None

            def custom_entry(name):
                nonlocal changed, entry_calls
                if name == lifecycle.LOCK_NAME:
                    entry_calls += 1
                    if entry_calls == 2:
                        changed = False
                result = original_entry(name)
                if name == lifecycle.LOCK_NAME and entry_calls == 1:
                    changed = True
                return result

            def observe_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == target and changed:
                    changed_observations.append(path)
                    return _ChangedStat(result, st_ino=result.st_ino + 1)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(lock.directory, "entry_stat", custom_entry)
                patch.setattr(os, "stat", observe_stat)
                try:
                    verify()
                except lifecycle.StorageLifecycleError as error:
                    failure = error
            return failure, entry_calls, changed_observations

        original_failure, original_calls, original_observed = attempt(lock.verify)
        assert original_failure is not None
        assert original_failure.code == "STORAGE_UNSAFE_PATH"
        assert original_calls == 1 and original_observed == [target]

        grouped_failure, grouped_calls, grouped_observed = attempt(guard.verify)
        assert grouped_failure is not None, "Grouped verification bypassed the custom callback boundary"
        assert grouped_failure.code == original_failure.code
        assert grouped_calls == original_calls and grouped_observed == original_observed


@pytest.mark.parametrize("callback", ["lock_entry", "fileno"])
def test_custom_lock_io_keeps_original_directory_check_boundary(tmp_path, monkeypatch, callback):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        lock = guard._locks[0]
        target = _native(tmp_path)
        original_stat = os.stat
        owner, name = (lock, "_verify_entry_stat") if callback == "lock_entry" else (lock.stream, "fileno")
        original_callback = getattr(owner, name)
        # fileno runs for entry fstat, marker seek and marker read before the
        # original second directory check. The next entry would be call four.
        restore_call = 2 if callback == "lock_entry" else 4

        def attempt(verify):
            changed = False
            calls = 0
            observations = []
            failure = None

            def custom_callback():
                nonlocal changed, calls
                calls += 1
                if calls == restore_call:
                    changed = False
                result = original_callback()
                if calls == 1:
                    changed = True
                return result

            def observe_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == target and changed:
                    observations.append(path)
                    return _ChangedStat(result, st_ino=result.st_ino + 1)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(owner, name, custom_callback)
                patch.setattr(os, "stat", observe_stat)
                try:
                    verify()
                except lifecycle.StorageLifecycleError as error:
                    failure = error
            return failure, calls, observations

        original_failure, original_calls, original_observed = attempt(lock.verify)
        assert original_failure is not None and original_failure.code == "STORAGE_UNSAFE_PATH"
        assert original_calls == restore_call - 1 and original_observed == [target]
        grouped_failure, grouped_calls, grouped_observed = attempt(guard.verify)
        assert grouped_failure is not None and grouped_failure.code == original_failure.code
        assert grouped_calls == original_calls and grouped_observed == original_observed


@pytest.mark.parametrize("method", ["verify", "verify_stat", "_verify_entry_stat"])
def test_method_bound_to_another_lock_keeps_original_dispatch(tmp_path, monkeypatch, method):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        first, second = guard._locks[:2]
        paths = {_native(path) for lock in guard._locks for path, _, _ in lock.directory.identities}
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        original_stat, original_fstat, original_read = os.stat, os.fstat, os.read

        def observe(verify):
            events = []

            def observe_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path in paths:
                    events.append(("directory", path))
                return result

            def observe_fstat(descriptor):
                result = original_fstat(descriptor)
                if descriptor in descriptors:
                    events.append(("fstat", descriptor))
                return result

            def observe_read(descriptor, size):
                result = original_read(descriptor, size)
                if descriptor in descriptors:
                    events.append(("marker", descriptor))
                    assert result == lifecycle._MARKER
                return result

            with monkeypatch.context() as patch:
                patch.setattr(first, method, getattr(second, method))
                patch.setattr(os, "stat", observe_stat)
                patch.setattr(os, "fstat", observe_fstat)
                patch.setattr(os, "read", observe_read)
                verify()
            return events

        def original_sequence():
            for lock in guard._locks:
                lock.verify()

        expected = observe(original_sequence)
        assert any(kind == "directory" for kind, _ in expected)
        assert any(kind == "marker" for kind, _ in expected)
        assert observe(guard.verify) == expected


def test_custom_directory_descriptor_keeps_original_io_error_boundary(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    lifecycle.initialize(profile)
    with lifecycle.shared(profile, timeout=0) as guard:
        lock = guard._locks[0]
        original = lock.directory
        failure = OSError("synthetic custom directory descriptor failure")

        class CustomDirectory(lifecycle.safe._Directory):
            @property
            def entry_stat(self):
                raise failure

        custom = CustomDirectory(original.path, base=original.base,
                                 parent=original.parent, create=original.create)
        # Borrow this guard's real directory state. No new handle is opened,
        # closed or renamed, and the original directory is restored before exit.
        custom.__dict__.update(original.__dict__)
        assert custom.handles is original.handles and custom.identities is original.identities
        with monkeypatch.context() as patch:
            patch.setattr(lock, "directory", custom)
            with pytest.raises(lifecycle.StorageLifecycleError) as original_error:
                lock.verify()
            assert original_error.value.code == "STORAGE_IO_ERROR"
            with pytest.raises(Exception) as grouped_error:
                guard.verify()
            assert isinstance(grouped_error.value, lifecycle.StorageLifecycleError)
            assert grouped_error.value.code == original_error.value.code
