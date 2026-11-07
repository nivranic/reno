"""Real lock cleanup methods with unregistered Fake self/I/O, never a grant."""
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from reno import personal_authority_files as locks


class Halt(BaseException):
    pass


class HiddenTraceback(RuntimeError):
    def __getattribute__(self, name):
        if name == '__traceback__':
            raise Halt('ordinary traceback lookup must not prevent cleanup')
        return super().__getattribute__(name)


class HostileAddNote(RuntimeError):
    def add_note(self, _note):
        raise Halt('ordinary add_note must not run')


class HostileNotesLookup(RuntimeError):
    def __getattribute__(self, name):
        if name == '__notes__':
            raise Halt('optional builtin annotation may fail')
        return super().__getattribute__(name)


@pytest.fixture
def cleanup(monkeypatch):
    events, failures = [], {}
    # No constructor, token, owner, guard, LIVE registration, or source factory.
    # This self only supplies the I/O boundary consumed by the actual methods.
    state = SimpleNamespace(locked=True, overlap=locks._LockOverlapped(), timeout=0,
        lock_name='authority.lock', exclusive=False)
    def step(name):
        events.append(name)
        error = failures.get(name)
        if error is not None:
            raise error
    class Stream:
        reenter = False
        def fileno(self):
            step('fileno')
            return 9090909
        def close(self):
            assert not state.locked and state.stream is None
            step('close')
            if self.reenter:
                self.reenter = False
                with pytest.raises(locks.FilePrimitiveError):
                    locks._ExistingLock.__exit__(state, None, None, None)
    stream = Stream()
    state.stream = stream
    def verify(**options):
        step('verify-wait' if options.get('content') is False else 'verify')
        if state.stream is None:
            raise locks.FilePrimitiveError('FILES_LOCK_OWNER_INVALID')
    state.verify = verify
    state.check = lambda: step('check')
    state.__exit__ = lambda kind, error, tb: locks._ExistingLock.__exit__(state, kind, error, tb)
    def unlock(*arguments):
        assert len(arguments) == 5
        assert [argument.value for argument in arguments[:4]] == [9999, 0, 1, 0]
        assert state.locked and state.stream is stream
        step('unlock')
        return 1
    def get_handle(descriptor):
        assert descriptor == 9090909
        step('get-handle')
        return 9999
    monkeypatch.setattr(locks, '_lock_kernel', SimpleNamespace(UnlockFileEx=unlock))
    original_ctypes = locks.safe.ctypes
    def unexpected(*_args, **_options):
        raise AssertionError('Unexpected Fake I/O boundary')
    # Replace module dependency namespaces, never shared os/files/ctypes.
    monkeypatch.setattr(locks, 'safe', SimpleNamespace(
        msvcrt=SimpleNamespace(get_osfhandle=get_handle),
        ctypes=SimpleNamespace(c_void_p=original_ctypes.c_void_p,
            c_uint32=original_ctypes.c_uint32, byref=original_ctypes.byref,
            get_last_error=unexpected, WinError=unexpected)))
    monkeypatch.setattr(locks, 'os', SimpleNamespace(name='nt', fstat=unexpected))
    monkeypatch.setattr(locks, 'files', SimpleNamespace(
        identity=locks.files.identity, require_private=unexpected))
    def close(primary=None, *, kind='normal'):
        return locks._ExistingLock.__exit__(state, None if primary is None or kind == 'inconsistent' else type(primary), primary, None)
    def enter_failure(stage, first):
        state.locked = False
        info = SimpleNamespace(st_dev=1, st_ino=2, st_size=1, st_mtime_ns=3, st_ctime_ns=4,
            st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_file_attributes=0)
        directory = SimpleNamespace(path=Path('unregistered-fake-lock-io'),
            verify=lambda: step('directory'), entry_stat=lambda _name: info)
        state.directory = directory
        def private(path, **options):
            if path == directory.path / state.lock_name:
                step('private-io')
                return None
            raise AssertionError('Unexpected private Fake path')
        def fstat(descriptor):
            if descriptor == 9090909:
                step('fstat')
                return info
            raise AssertionError('Unexpected Fake descriptor')
        monkeypatch.setattr(locks.files, 'require_private', private)
        monkeypatch.setattr(locks.os, 'fstat', fstat)
        monkeypatch.setattr(locks, '_open_lock', lambda _directory: stream)
        def attempt():
            step('attempt')
            return True
        state._attempt = attempt
        failures[stage] = first
        return locks._ExistingLock.__enter__(state)
    return SimpleNamespace(state=state, stream=stream, events=events, failures=failures,
        close=close, enter_failure=enter_failure)


def finished(cleanup, *, unlock=True, close=True):
    assert cleanup.events.count('unlock') == int(unlock)
    assert cleanup.events.count('close') == int(close)
    assert cleanup.state.stream is None and not cleanup.state.locked


@pytest.mark.parametrize('locked', [False, True])
def test_normal_exit_keeps_verification_and_one_close_attempt(cleanup, locked):
    cleanup.state.locked = locked
    assert cleanup.close() is None
    assert cleanup.events[0] == 'verify'
    finished(cleanup, unlock=locked)


@pytest.mark.parametrize('kind', [RuntimeError, Halt, HostileAddNote, HostileNotesLookup])
def test_caller_first_survives_unlock_close_and_optional_note_failures(cleanup, kind):
    first = kind('synthetic caller first')
    cleanup.failures.update(unlock=RuntimeError('synthetic later unlock'), close=RuntimeError('synthetic later close'))
    assert cleanup.close(first) is None
    assert 'verify' not in cleanup.events
    finished(cleanup)


@pytest.mark.parametrize('kind', [RuntimeError, Halt])
@pytest.mark.parametrize('unlock_fails', [False, True])
@pytest.mark.parametrize('close_fails', [False, True])
def test_verification_first_survives_all_cleanup_combinations(cleanup, kind, unlock_fails, close_fails):
    first = kind('synthetic validation first')
    cleanup.failures['verify'] = first
    if unlock_fails:
        cleanup.failures['unlock'] = RuntimeError('synthetic later unlock')
    if close_fails:
        cleanup.failures['close'] = RuntimeError('synthetic later close')
    with pytest.raises(kind) as caught:
        cleanup.close()
    assert caught.value is first
    finished(cleanup)


@pytest.mark.parametrize('close_fails', [False, True])
def test_unlock_first_is_not_replaced_by_later_close(cleanup, close_fails):
    first = Halt('synthetic unlock first')
    cleanup.failures['unlock'] = first
    if close_fails:
        cleanup.failures['close'] = RuntimeError('synthetic later close')
    with pytest.raises(Halt) as caught:
        cleanup.close()
    assert caught.value is first
    finished(cleanup)


def test_close_first_is_still_reported(cleanup):
    first = RuntimeError('synthetic close first')
    cleanup.failures['close'] = first
    with pytest.raises(RuntimeError) as caught:
        cleanup.close()
    assert caught.value is first
    finished(cleanup)


def test_no_acquired_stream_or_lock_does_not_fabricate_cleanup_work(cleanup):
    cleanup.state.stream, cleanup.state.locked = None, False
    assert cleanup.close(Halt('synthetic partial acquisition first')) is None
    assert cleanup.events == []
    finished(cleanup, unlock=False, close=False)


def test_false_native_reply_retains_actual_synthetic_error_and_closes(cleanup, monkeypatch):
    first = OSError('synthetic UnlockFileEx false reply')
    def failed_unlock(*_arguments):
        cleanup.events.append('unlock')
        return 0
    monkeypatch.setattr(locks._lock_kernel, 'UnlockFileEx', failed_unlock)
    monkeypatch.setattr(locks.safe.ctypes, 'get_last_error', lambda: 5)
    monkeypatch.setattr(locks.safe.ctypes, 'WinError', lambda code: first if code == 5 else AssertionError())
    with pytest.raises(OSError) as caught:
        cleanup.close()
    assert caught.value is first
    finished(cleanup)


def test_handle_conversion_failure_still_attempts_original_stream_close(cleanup):
    first = Halt('synthetic handle conversion first')
    cleanup.failures.update({'get-handle': first, 'close': RuntimeError('synthetic later close')})
    with pytest.raises(Halt) as caught:
        cleanup.close()
    assert caught.value is first
    finished(cleanup, unlock=False)


def test_closer_reentry_cannot_close_the_detached_stream_twice(cleanup):
    cleanup.stream.reenter = True
    assert cleanup.close() is None
    finished(cleanup)


def test_original_exc_type_gate_and_verification_priority_are_retained(cleanup):
    first = RuntimeError('synthetic verification failure')
    cleanup.failures['verify'] = first
    with pytest.raises(RuntimeError) as caught:
        cleanup.close(first, kind='inconsistent')
    assert caught.value is first
    finished(cleanup)


@pytest.mark.parametrize('stage', ['fstat', 'verify-wait', 'attempt', 'verify'])
def test_acquisition_failure_keeps_original_and_closes_partial_resources(cleanup, stage):
    first = Halt('synthetic acquisition first')
    cleanup.failures['close'] = RuntimeError('synthetic later close')
    with pytest.raises(Halt) as caught:
        cleanup.enter_failure(stage, first)
    assert caught.value is first
    finished(cleanup, unlock=stage == 'verify')


def test_acquisition_error_traceback_override_cannot_skip_original_cleanup(cleanup):
    first = HiddenTraceback('synthetic acquisition first')
    with pytest.raises(HiddenTraceback) as caught:
        cleanup.enter_failure('fstat', first)
    assert caught.value is first
    finished(cleanup, unlock=False)


def test_acquisition_cleanup_reporting_cannot_replace_hostile_caller_first(cleanup):
    first = HostileNotesLookup('synthetic acquisition first')
    cleanup.failures.update(unlock=RuntimeError('synthetic later unlock'), close=RuntimeError('synthetic later close'))
    with pytest.raises(HostileNotesLookup) as caught:
        cleanup.enter_failure('verify', first)
    assert caught.value is first
    finished(cleanup)
