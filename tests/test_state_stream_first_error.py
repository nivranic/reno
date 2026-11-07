"""Actual State stream functions against fake I/O; no native handles or State DB."""
from pathlib import PurePath
from types import SimpleNamespace

import pytest

from reno import sync_media as real_media
from reno import sync_state as state
from reno.sync_protocol import SyncError


class Halt(BaseException):
    pass


class HiddenTraceback(ValueError):
    def __getattribute__(self, name):
        if name == '__traceback__':
            raise RuntimeError('synthetic ordinary traceback lookup blocked')
        return super().__getattribute__(name)


@pytest.fixture
def io(monkeypatch):
    events, failures, counts, exits = [], {}, {}, []
    info = SimpleNamespace(st_mode=0o100600, st_nlink=1, st_dev=7, st_ino=31,
        st_size=3, st_mtime_ns=11, st_ctime_ns=13, st_file_attributes=0)
    fixture = SimpleNamespace(events=events, failures=failures, counts=counts,
        exits=exits, info=info, suppress=False, temporary=False, changed=False,
        native_result=True, close_result=True)

    def step(name):
        events.append(name)
        counts[name] = counts.get(name, 0) + 1
        error = failures.get(name + '/' + str(counts[name]), failures.get(name))
        if error is not None:
            raise error

    class Stream:
        def fileno(self):
            step('fileno')
            return 941
        def read(self, maximum):
            assert maximum == 4
            step('read')
            return b'abc'
        def write(self, data):
            assert data == b'abc'
            step('write')
            return len(data)
        def flush(self):
            step('flush')

    stream = Stream()

    class Manager:
        def __enter__(self):
            step('enter')
            return stream
        def __exit__(self, kind, error, traceback):
            exits.append(error)
            step('exit')
            return fixture.suppress

    manager = Manager()

    class Directory:
        fd = 841
        path = PurePath('fake-state-stream')
        def open_file(self, name, **kwargs):
            if kwargs:
                assert kwargs == {'write': True, 'create': True}
                assert name == '.sync-fake.part'
                fixture.temporary = True
            else:
                assert name == 'image'
            step('open_file')
            return manager
        def verify(self):
            step('verify')
        def entry_stat(self, name):
            if name == state._LOCK:
                step('lock_stat')
                if fixture.changed and counts['lock_stat'] > 1:
                    return SimpleNamespace(**{**vars(info), 'st_ino': 32})
                return info
            assert name == '.sync-fake.part'
            step('temp_stat')
            if not fixture.temporary:
                raise FileNotFoundError('synthetic published temp absent')
            return info
        def unlink(self, name):
            assert name == '.sync-fake.part'
            step('unlink')
            fixture.temporary = False

    directory = Directory()

    def opened(name, flags, *, dir_fd):
        assert dir_fd == 841
        assert name in ('image', state._LOCK)
        assert flags == ((1 if name == 'image' else 2) | 4)
        step('os_open')
        return 941

    def fstat(fd):
        assert fd == 941
        step('fstat')
        return info

    def fdopen(fd, mode, **kwargs):
        assert fd == 941 and mode in ('rb', 'r+b')
        assert kwargs == ({'buffering': 0} if mode == 'r+b' else {})
        step('fdopen')
        return manager

    def close(fd):
        assert fd == 941
        step('os_close')

    def fsync(fd):
        assert fd in (941, 841)
        step('fsync' if fd == 941 else 'directory_fsync')

    def publish(*args):
        assert args[:3] == (directory, '.sync-fake.part', 'image')
        assert args[3] == (7, 31)
        step('replace')
        fixture.temporary = False

    def rename(source, temporary, target, name):
        assert source is target is directory
        assert (temporary, name) == ('.sync-fake.part', 'image')
        step('rename_new')
        fixture.temporary = False

    def native_create(path, access, share, security, disposition, flags, template):
        assert (security, disposition, flags, template) == (None, 3, 0x00200000, None)
        assert (access, share) == ((0x80000000, 5) if fixture.native_observation else (0xC0000000, 3))
        step('native_create')
        return 111

    def native_info(handle, value):
        assert handle == 111 and value is native_info_value
        step('native_info')
        return fixture.native_result

    def native_close(handle):
        assert handle == 111
        step('native_close')
        return fixture.close_result

    def convert(handle, flags):
        assert handle == 111
        assert flags == (8 | (1 if fixture.native_observation else 2))
        step('convert')
        return 941

    native_info_value = SimpleNamespace(attributes=0, links=1)
    native_error = OSError('synthetic GetInfo failure')
    fake_os = SimpleNamespace(name='posix', O_RDONLY=1, O_RDWR=2, O_NOFOLLOW=4,
        O_BINARY=8, open=opened, fstat=fstat, fdopen=fdopen, close=close, fsync=fsync)
    fake_media = SimpleNamespace(_entry_name=real_media._entry_name,
        _safe_stat=real_media._safe_stat, _rename_new=rename, _REPARSE=0x400,
        _native_path=lambda path: str(path), _HandleInfo=lambda: native_info_value,
        _kernel=SimpleNamespace(CreateFileW=native_create,
            GetFileInformationByHandle=native_info, CloseHandle=native_close),
        msvcrt=SimpleNamespace(open_osfhandle=convert))
    fake_ctypes = SimpleNamespace(c_void_p=state.ctypes.c_void_p, byref=lambda value: value,
        WinError=lambda code: native_error, get_last_error=lambda: 6)
    monkeypatch.setattr(state, 'os', fake_os)
    monkeypatch.setattr(state, 'media', fake_media)
    monkeypatch.setattr(state, 'ctypes', fake_ctypes)
    monkeypatch.setattr(state, 'uuid', SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='fake')))
    monkeypatch.setattr(state, '_replace', publish)
    fixture.directory, fixture.stream, fixture.manager = directory, stream, manager
    fixture.native_error, fixture.os = native_error, fake_os
    fixture.read = lambda observation=False: state._read_bytes(directory, 'image', 3, observation=observation)
    fixture.write = lambda immutable=False: state._write(directory, 'image', b'abc', immutable=immutable)
    fixture.existing = lambda: state._existing_lock_stream(directory)
    fixture.observe = lambda: state._observation_stream(directory, 'image')
    return fixture


@pytest.mark.parametrize('observation', [False, True])
def test_read_success_uses_actual_enter_result(io, observation):
    assert io.read(observation) == b'abc'
    assert io.exits == [None] and io.counts['exit'] == 1
    assert io.events.index('exit') < io.events.index('verify')
    assert ('os_open' in io.events) is observation


@pytest.mark.parametrize('observation', [False, True])
def test_read_first_survives_exit_failure(io, observation):
    first = ValueError('synthetic read first')
    io.failures.update(read=first, exit=RuntimeError('synthetic later exit'))
    with pytest.raises(ValueError) as caught:
        io.read(observation)
    assert caught.value is first and io.exits == [first]
    assert io.counts['exit'] == 1 and 'verify' not in io.events


def test_read_validation_first_survives_exit_failure(io):
    io.info.st_size = 4
    io.failures['exit'] = RuntimeError('synthetic later exit')
    with pytest.raises(SyncError) as caught:
        io.read()
    assert io.exits == [caught.value] and 'read' not in io.events


def test_read_successful_body_unknown_exit_error_remains_visible(io):
    first = RuntimeError('synthetic sole exit first')
    io.failures['exit'] = first
    with pytest.raises(RuntimeError) as caught:
        io.read()
    assert caught.value is first and io.exits == [None]


def test_read_truthy_exit_keeps_original_suppression(io):
    first = ValueError('synthetic after-read metadata failure')
    io.failures['fstat/2'] = first
    io.suppress = True
    assert io.read() == b'abc'
    assert io.exits == [first] and io.counts['verify'] == 1


def test_read_enter_failure_does_not_call_exit(io):
    first = Halt('synthetic enter first')
    io.failures['enter'] = first
    with pytest.raises(Halt) as caught:
        io.read()
    assert caught.value is first and io.exits == [] and 'read' not in io.events


@pytest.mark.parametrize('immutable', [False, True])
def test_write_success_preserves_publish_mode_and_order(io, immutable):
    assert io.write(immutable) is None
    publish = 'rename_new' if immutable else 'replace'
    assert io.exits == [None] and not io.temporary
    assert io.events.index('exit') < io.events.index('verify') < io.events.index(publish)
    assert io.events.index(publish) < io.events.index('directory_fsync')
    assert 'unlink' not in io.events


@pytest.mark.parametrize('site', ['write', 'flush', 'fsync'])
def test_write_first_survives_exit_and_temp_cleanup(io, site):
    first = ValueError('synthetic write body first')
    io.failures.update({site: first, 'exit': RuntimeError('later exit'),
        'unlink': TypeError('later temp cleanup')})
    with pytest.raises(ValueError) as caught:
        io.write()
    assert caught.value is first and io.exits == [first]
    assert io.counts['exit'] == io.counts['unlink'] == 1
    assert 'replace' not in io.events and 'rename_new' not in io.events


def test_write_exit_first_precedes_temp_cleanup(io):
    first = RuntimeError('synthetic exit first')
    io.failures.update(exit=first, unlink=ValueError('later temp cleanup'))
    with pytest.raises(RuntimeError) as caught:
        io.write()
    assert caught.value is first and io.exits == [None]
    assert io.counts['unlink'] == 1


def test_write_cancellation_survives_exit(io):
    first = Halt('synthetic write cancellation')
    io.failures.update(write=first, exit=RuntimeError('later exit'))
    with pytest.raises(Halt) as caught:
        io.write()
    assert caught.value is first and io.exits == [first]
    assert io.counts['unlink'] == 1


def test_write_enter_failure_never_unlinks_unproven_temp(io):
    first = ValueError('synthetic enter first')
    io.failures['enter'] = first
    with pytest.raises(ValueError) as caught:
        io.write()
    assert caught.value is first and io.exits == [] and 'unlink' not in io.events


def test_existing_lock_stream_success_checks_both_sides(io):
    with io.existing() as actual:
        assert actual is io.stream
    assert io.exits == [None]
    assert io.counts['verify'] == io.counts['fstat'] == io.counts['lock_stat'] == 2
    assert io.counts['exit'] == 1 and 'os_close' not in io.events


def test_existing_lock_stream_pre_yield_first_survives_close(io):
    first = ValueError('synthetic opened signature first')
    io.failures.update(fstat=first, exit=RuntimeError('later close'))
    with pytest.raises(ValueError) as caught:
        with io.existing():
            raise AssertionError('pre-yield failure reached caller')
    assert caught.value is first and io.exits == [first]
    assert 'verify' not in io.events


def test_existing_lock_caller_first_survives_close_without_post_checks(io):
    first = HiddenTraceback('synthetic caller first')
    io.failures['exit'] = RuntimeError('later close')
    with pytest.raises(HiddenTraceback) as caught:
        with io.existing() as actual:
            assert actual is io.stream
            raise first
    assert caught.value is first and io.exits == [first]
    assert io.counts['verify'] == io.counts['fstat'] == io.counts['lock_stat'] == 1


def test_existing_lock_post_yield_signature_first_survives_close(io):
    io.changed = True
    io.failures['exit'] = RuntimeError('later close')
    with pytest.raises(SyncError) as caught:
        with io.existing():
            pass
    assert caught.value.code == 'SYNC_STATE_UNSAFE_PATH' and io.exits == [caught.value]
    assert io.counts['verify'] == 1 and io.counts['lock_stat'] == 2


def test_existing_fdopen_failure_does_not_add_numeric_close(io):
    first = ValueError('synthetic fdopen first')
    io.failures['fdopen'] = first
    with pytest.raises(ValueError) as caught:
        with io.existing():
            raise AssertionError('fdopen failure reached caller')
    assert caught.value is first and io.exits == [] and 'os_close' not in io.events


def test_existing_successful_body_unknown_exit_error_remains_visible(io):
    first = RuntimeError('synthetic sole exit error')
    io.failures['exit'] = first
    with pytest.raises(RuntimeError) as caught:
        with io.existing():
            pass
    assert caught.value is first and io.exits == [None]


def test_observation_posix_fstat_first_survives_close(io):
    first = ValueError('synthetic fstat first')
    io.failures.update(fstat=first, os_close=RuntimeError('later numeric close'))
    with pytest.raises(ValueError) as caught:
        io.observe()
    assert caught.value is first and io.counts['os_close'] == 1
    assert 'fdopen' not in io.events


def test_observation_posix_unsafe_first_survives_close(io):
    io.info.st_nlink = 2
    io.failures['os_close'] = RuntimeError('later numeric close')
    with pytest.raises(SyncError) as caught:
        io.observe()
    assert caught.value.code == 'SYNC_STATE_UNSAFE_PATH' and io.counts['os_close'] == 1
    assert 'fdopen' not in io.events


def test_observation_posix_returns_actual_manager_without_extra_close(io):
    assert io.observe() is io.manager
    assert io.events == ['os_open', 'fstat', 'fdopen']


@pytest.mark.parametrize('observation', [False, True])
@pytest.mark.parametrize('site', ['native_info', 'convert'])
def test_windows_raw_acquisition_first_survives_raw_close(io, observation, site):
    io.os.name = 'nt'
    io.native_observation = observation
    first = Halt('synthetic native preparation cancellation')
    io.failures.update({site: first, 'native_close': RuntimeError('later raw close')})
    with pytest.raises(Halt) as caught:
        if observation:
            io.observe()
        else:
            with io.existing():
                raise AssertionError('native preparation failure reached caller')
    assert caught.value is first and io.counts['native_close'] == 1
    assert 'fdopen' not in io.events and 'os_close' not in io.events


def test_windows_false_info_and_false_close_keeps_original_error_without_retry(io):
    io.os.name = 'nt'
    io.native_observation = True
    io.native_result = io.close_result = False
    with pytest.raises(OSError) as caught:
        io.observe()
    assert caught.value is io.native_error and io.counts['native_close'] == 1
    assert 'convert' not in io.events and 'fdopen' not in io.events
