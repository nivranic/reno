"""Exclusive-scope fake backends; no native lock, State image, SQL or filesystem I/O.

The actual StateStore, RLock, ExitStack and public sanitizer execute. Nothing here
establishes a real owner/grant or fixes the unknown q1162 native State failure.
"""
import errno
from types import SimpleNamespace

import pytest

from reno import sync_state as state
from reno.sync_protocol import SyncError


class Halt(BaseException):
    pass


@pytest.fixture(params=[False, True], ids=['creating', 'existing'])
def scope(request, monkeypatch):
    store=state.StateStore('reno-fake-state-scope')
    events=[]
    failures={}
    exits=[]
    existing=request.param

    def step(name):
        events.append(name)
        if name in failures:
            raise failures[name]

    class Stream:
        def __enter__(self):
            step('stream_enter')
            return self
        def __exit__(self, kind, error, traceback):
            assert store._directory is None
            exits.append(error)
            if existing and error is None:
                step('existing_success_check')
            step('stream_exit')
        def seek(self, offset):
            assert offset==0
            step('seek')
        def fileno(self):
            return 941

    class Directory:
        def __init__(self, path, *, create):
            assert create is (not existing)
            step('directory_new')
        def __enter__(self):
            step('directory_enter')
            return self
        def __exit__(self, kind, error, traceback):
            assert store._directory is None
            step('directory_exit')
        def open_file(self, name, *, write, lock):
            assert not existing and name==state._LOCK and write and lock
            step('creating_stream')
            return Stream()

    def existing_stream(directory):
        assert existing and isinstance(directory,Directory)
        step('existing_stream')
        return Stream()

    def locking(fd, mode, length):
        assert fd==941 and length==1
        if mode==7:
            assert store._directory is None
            step('acquire')
        else:
            assert mode==8 and store._directory is None
            step('unlock')

    monkeypatch.setattr(state,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(state,'media',SimpleNamespace(_Directory=Directory,
        msvcrt=SimpleNamespace(locking=locking,LK_NBLCK=7,LK_UNLCK=8)))
    monkeypatch.setattr(state,'_existing_lock_stream',existing_stream)
    return SimpleNamespace(store=store,events=events,failures=failures,exits=exits,
        existing=existing,enter=lambda:store.exclusive(existing_only=existing))


def closed(scope):
    assert scope.store._directory is None
    if 'stream_enter' in scope.events and 'stream_enter' not in scope.failures:
        assert scope.events.count('stream_exit')==1
    if 'directory_enter' in scope.events and 'directory_enter' not in scope.failures:
        assert scope.events.count('directory_exit')==1


def test_success_uses_original_mode_and_retires_before_unlock_and_exits(scope):
    with scope.enter() as store:
        assert store is scope.store and store._directory is not None
        assert 'unlock' not in scope.events
    closed(scope)
    assert scope.events.index('unlock')<scope.events.index('stream_exit')<scope.events.index('directory_exit')
    assert scope.exits==[None]
    assert ('creating_stream' in scope.events) is (not scope.existing)
    assert ('existing_stream' in scope.events) is scope.existing


@pytest.mark.parametrize('fault',['unlock','stream','all'])
def test_caller_error_survives_all_scope_cleanup_and_reentry(scope,fault):
    first=ValueError('synthetic caller first')
    if fault in ('unlock','all'):
        scope.failures['unlock']=OSError('synthetic unlock later')
    if fault in ('stream','all'):
        scope.failures['stream_exit']=RuntimeError('synthetic stream later')
    if fault=='all':
        scope.failures['directory_exit']=TypeError('synthetic directory later')
    with pytest.raises(ValueError) as caught:
        with scope.enter():
            raise first
    assert caught.value is first
    closed(scope)
    assert scope.exits==[first]
    assert 'existing_success_check' not in scope.events
    scope.failures.clear()
    with scope.enter():
        assert scope.store._directory is not None
    assert scope.store._directory is None


def test_baseexception_cancellation_survives_unlock_and_all_registered_exits(scope):
    first=Halt('synthetic cancellation first')
    scope.failures.update(unlock=OSError('later unlock'),stream_exit=ValueError('later stream'),
        directory_exit=RuntimeError('later directory'))
    with pytest.raises(Halt) as caught:
        with scope.enter():
            raise first
    assert caught.value is first and scope.exits==[first]
    closed(scope)


@pytest.mark.parametrize('fault',['unlock','stream','all'])
def test_successful_caller_reports_scope_cleanup_error_without_hiding_unknowns(scope,fault):
    unknown=RuntimeError('synthetic unknown stream close')
    if fault in ('unlock','all'):
        scope.failures['unlock']=OSError('synthetic unlock first')
    if fault in ('stream','all'):
        scope.failures['stream_exit']=unknown
    if fault=='all':
        scope.failures['directory_exit']=ValueError('synthetic directory later')
    with pytest.raises(RuntimeError if fault=='stream' else SyncError) as caught:
        with scope.enter():
            pass
    if fault=='stream':
        assert caught.value is unknown
    else:
        assert caught.value.code=='SYNC_STATE_STORAGE_ERROR'
    closed(scope)
    assert scope.events.count('unlock')==1


@pytest.mark.parametrize('site',['stream_enter','acquire'])
def test_setup_first_survives_registered_cleanup_failures(scope,site):
    first=SyncError('synthetic setup first','SYNC_STATE_SETUP_FIRST',409)
    scope.failures.update({site:first,'directory_exit':OSError('synthetic directory later')})
    if site=='acquire':
        scope.failures['stream_exit']=ValueError('synthetic stream later')
    with pytest.raises(SyncError) as caught:
        with scope.enter():
            raise AssertionError('setup failure reached caller')
    assert caught.value is first and 'unlock' not in scope.events
    closed(scope)


def test_optional_annotation_failure_cannot_replace_caller_first(scope):
    first=ValueError('synthetic caller first with invalid annotation storage')
    first.__notes__=()
    scope.failures['unlock']=OSError('synthetic later unlock')
    with pytest.raises(ValueError) as caught:
        with scope.enter():
            raise first
    assert caught.value is first
    closed(scope)


def test_busy_rejection_does_not_touch_backend_or_an_existing_scope(scope):
    original=object()
    scope.store._directory=original
    with pytest.raises(SyncError) as caught:
        with scope.enter():
            raise AssertionError('busy scope reached caller')
    assert caught.value.code=='SYNC_BUSY'
    assert scope.store._directory is original and scope.events==[]
    scope.store._directory=None


def test_actual_acquisition_busy_mapping_survives_cleanup_fault(scope):
    scope.failures.update(acquire=OSError(errno.EACCES,'synthetic competing lock'),
        stream_exit=ValueError('synthetic later stream'),directory_exit=RuntimeError('synthetic later directory'))
    with pytest.raises(SyncError) as caught:
        with scope.enter():
            raise AssertionError('failed acquisition reached caller')
    assert caught.value.code=='SYNC_BUSY'
    closed(scope)


def test_directory_entry_error_does_not_attempt_unacquired_stream_or_unlock(scope):
    first=Halt('synthetic directory entry first')
    scope.failures['directory_enter']=first
    with pytest.raises(Halt) as caught:
        with scope.enter():
            raise AssertionError('failed entry reached caller')
    assert caught.value is first and scope.store._directory is None
    assert scope.events==['directory_new','directory_enter']


class HiddenTraceback(ValueError):
    def __getattribute__(self, name):
        if name == '__traceback__':
            raise RuntimeError('synthetic caller blocks ordinary traceback lookup')
        return super().__getattribute__(name)


def test_caller_traceback_override_cannot_skip_registered_scope_exits(scope):
    first=HiddenTraceback('synthetic caller first')
    with pytest.raises(HiddenTraceback) as caught:
        with scope.enter():
            raise first
    assert caught.value is first
    assert scope.exits==[first]
    closed(scope)
    assert scope.events.count('unlock')==1
    assert 'existing_success_check' not in scope.events
