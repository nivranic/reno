"""Pure fake backends: no filesystem, real descriptors, or native calls.

This verifies first-error ownership, not Windows rename success or q1162 cause.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from reno import sync_state as state
from reno.sync_protocol import SyncError

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows _replace branch')


class Halt(BaseException):
    pass


@pytest.fixture
def replacement(monkeypatch):
    events=[]
    failures={}
    native_status=[0]
    original_ctypes=state.ctypes
    original_media=state.media
    info=SimpleNamespace(st_mode=0o100600,st_nlink=1,st_dev=7,st_ino=31,st_file_attributes=0)
    def step(name, value=None):
        events.append(name)
        if name in failures:
            raise failures[name]
        return value
    class Directory:
        path=Path('C:/reno-fake-only') if os.name=='nt' else Path('/reno-fake-only')
        handles=[901]
        fd=None
        def verify(self):
            step('verify')
        def entry_stat(self,name):
            return step('stat:'+name,info)
    def error(_number):
        events.append('status_error')
        return failures['status_error']
    native=SimpleNamespace(NtSetInformationFile=lambda *_:step('native',native_status[0]),
        RtlNtStatusToDosError=lambda _:5)
    monkeypatch.setattr(state,'os',SimpleNamespace(name='nt',
        fstat=lambda _:step('fstat',info),close=lambda _:step('close')))
    monkeypatch.setattr(state,'media',SimpleNamespace(_safe_stat=original_media._safe_stat,
        _win_open=lambda *args,**kwargs:step('open',41),_nt=native,
        _IoStatus=original_media._IoStatus,
        msvcrt=SimpleNamespace(get_osfhandle=lambda _:step('get_handle',902))))
    monkeypatch.setattr(state,'ctypes',SimpleNamespace(Structure=original_ctypes.Structure,
        byref=original_ctypes.byref,sizeof=original_ctypes.sizeof,WinError=error))
    return SimpleNamespace(events=events,failures=failures,status=native_status,
        run=lambda:state._replace(Directory(),'temporary.part','sync-state.json',(7,31)))


def test_success_has_exactly_one_close(replacement):
    assert replacement.run() is None
    assert replacement.events[-2:]==['native','close']
    assert replacement.events.count('close')==1


def test_close_failure_after_success_is_returned_as_the_first(replacement):
    first=OSError('synthetic successful-operation close failure')
    replacement.failures['close']=first
    with pytest.raises(OSError) as caught:
        replacement.run()
    assert caught.value is first and replacement.events.count('close')==1


@pytest.mark.parametrize('site',['fstat','get_handle','native'])
@pytest.mark.parametrize('close_fails',[False,True])
def test_original_operation_error_survives_one_close_attempt(replacement,site,close_fails):
    first=OSError('synthetic operation first')
    replacement.failures[site]=first
    if close_fails:
        replacement.failures['close']=ValueError('synthetic later cleanup')
    with pytest.raises(OSError) as caught:
        replacement.run()
    assert caught.value is first and replacement.events.count('close')==1


def test_negative_status_error_survives_close_failure(replacement):
    first=OSError('synthetic native-status mapping')
    replacement.status[0]=-1
    replacement.failures.update(status_error=first,close=ValueError('synthetic later cleanup'))
    with pytest.raises(OSError) as caught:
        replacement.run()
    assert caught.value is first and replacement.events.count('close')==1


def test_original_identity_rejection_survives_close_failure(replacement,monkeypatch):
    monkeypatch.setattr(state.os,'fstat',lambda fd:SimpleNamespace(st_dev=7,st_ino=32))
    replacement.failures['close']=OSError('synthetic later close')
    with pytest.raises(SyncError) as caught:
        replacement.run()
    assert caught.value.code=='SYNC_STATE_UNSAFE_PATH'
    assert replacement.events.count('close')==1 and 'native' not in replacement.events


def test_pre_acquisition_error_has_no_close_attempt(replacement):
    first=OSError('synthetic open failure')
    replacement.failures['open']=first
    with pytest.raises(OSError) as caught:
        replacement.run()
    assert caught.value is first and 'close' not in replacement.events


def test_cancellation_survives_cleanup_failure(replacement):
    first=Halt('synthetic cancellation')
    replacement.failures.update(native=first,close=OSError('synthetic later close'))
    with pytest.raises(Halt) as caught:
        replacement.run()
    assert caught.value is first and replacement.events.count('close')==1


def test_public_sanitizer_classifies_original_io_error(replacement):
    replacement.failures.update(native=OSError('synthetic IO first'),close=ValueError('synthetic later cleanup'))
    with pytest.raises(SyncError) as caught:
        with state._sanitized():
            replacement.run()
    assert caught.value.code=='SYNC_STATE_STORAGE_ERROR'
    assert replacement.events.count('close')==1
