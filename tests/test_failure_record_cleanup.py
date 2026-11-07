"""Actual dependency-free cleanup used by all three real failure writers."""
from types import SimpleNamespace

import pytest

from reno import personal_authority_failure_cleanup as cleanup


class Halt(BaseException):
    pass


class HiddenTraceback(RuntimeError):
    def __getattribute__(self, name):
        if name == '__traceback__':
            raise Halt('ordinary traceback lookup must not skip a directory exit')
        return super().__getattribute__(name)


class HostileMethod(RuntimeError):
    def add_note(self, _value):
        raise Halt('overridden method is not the optional builtin')


class HostileLookup(RuntimeError):
    def __getattribute__(self, name):
        if name == '__notes__':
            raise Halt('optional builtin may fail')
        return super().__getattribute__(name)


@pytest.fixture
def resources():
    events, errors, exits, closer_calls = [], {}, [], []
    class Resource:
        def __init__(self, name):
            self.name = name
        def close(self):
            events.append('close/' + self.name)
            if self.name in errors:
                raise errors[self.name]
        def __exit__(self, kind, error, _transported_traceback):
            # No traceback/chain/frame/local/note observation.
            exits.append((self.name, kind, error))
            events.append('exit/' + self.name)
            if self.name in errors:
                raise errors[self.name]
            return False
    def close_stream(stream):
        closer_calls.append(stream)
        stream.close()
    return SimpleNamespace(events=events, errors=errors, exits=exits, closer_calls=closer_calls,
        make=Resource, closer=close_stream)


def run(resources, primary, *, stream=True, count=2):
    original = resources.make('stream') if stream else None
    owned = [resources.make(str(index)) for index in range(count)]
    before = tuple(owned)
    assert cleanup._close_failure_original(original, owned, primary, resources.closer) is None
    assert tuple(owned) == before
    assert resources.closer_calls == ([original] if stream else [])
    assert resources.events == (['close/stream'] if stream else []) + ['exit/' + str(index) for index in reversed(range(count))]
    assert resources.exits == [(str(index), type(primary), primary) for index in reversed(range(count))]


def test_success_keeps_original_closer_then_reverse_directory_order(resources):
    run(resources, RuntimeError('actual original first'))


@pytest.mark.parametrize('kind', [RuntimeError, Halt])
def test_every_secondary_error_retains_actual_primary_and_continues(resources, kind):
    resources.errors.update({name: RuntimeError('later closer') for name in ['stream', '0', '1']})
    run(resources, kind('actual original first'))


@pytest.mark.parametrize('count', [0, 1, 6])
def test_partial_directory_acquisition_never_invents_stream_or_other_closers(resources, count):
    run(resources, RuntimeError('actual original first'), stream=False, count=count)


def test_stream_acquired_before_no_directory_state_closes_once(resources):
    run(resources, RuntimeError('actual original first'), count=0)


@pytest.mark.parametrize('site', ['stream', '0', '1'])
def test_single_closer_failure_cannot_skip_remaining_original_resources(resources, site):
    resources.errors[site] = Halt('secondary cleanup cancellation')
    run(resources, RuntimeError('actual original first'))


def test_raw_traceback_transport_ignores_ordinary_attribute_override(resources):
    run(resources, HiddenTraceback('actual original first'))


@pytest.mark.parametrize('kind', [HostileMethod, HostileLookup])
def test_optional_note_failure_cannot_stop_later_closers(resources, kind):
    resources.errors.update({name: Halt('secondary cleanup cancellation') for name in ['stream', '0', '1']})
    run(resources, kind('actual original first'))


def test_traceback_override_and_directory_failure_still_attempt_parent_exit(resources):
    resources.errors['1'] = RuntimeError('secondary exit failure')
    run(resources, HiddenTraceback('actual original first'))


@pytest.mark.parametrize('body_is_primary', [True, False])
def test_pending_writer_body_error_survives_finally_and_keeps_original_first_operand(resources, body_is_primary):
    primary = RuntimeError('original budget first')
    body = primary if body_is_primary else Halt('later writer body error')
    resources.errors.update({name: RuntimeError('later cleanup') for name in ['stream', '0', '1']})
    try:
        try:
            raise body
        finally:
            run(resources, primary)
    except BaseException as caught:
        assert caught is body
    else:
        pytest.fail('pending body error was lost')


@pytest.mark.parametrize('kind', [HostileMethod, HostileLookup])
def test_coordinator_optional_note_never_replaces_stable_failure(kind):
    failure = kind('stable outward failure')
    cleanup._note_original(failure, 'AUTHORITY_RAW_PRESERVATION_FAILURE_RECORD_UNAVAILABLE')


@pytest.mark.parametrize('mode', ['rb', 'w+b'])
def test_actual_buffered_stream_closes_before_directory_after_existing_first(resources, tmp_path, mode):
    path = tmp_path / 'failure.bin'
    path.write_bytes(b'failure cleanup fixture')
    stream = path.open(mode)
    first = HiddenTraceback('actual original first')
    owned = [resources.make('0')]
    try:
        cleanup._close_failure_original(stream, owned, first, resources.closer)
        assert stream.closed and resources.closer_calls == [stream]
        assert resources.exits == [('0', HiddenTraceback, first)]
    finally:
        stream.close()


def test_original_stream_closer_operand_is_used_without_replacing_it(resources):
    stream = resources.make('stream')
    first = RuntimeError('actual original first')
    called = []
    def original_closer(actual):
        called.append(actual)
        raise Halt('fixed module-local closer failed')
    cleanup._close_failure_original(stream, [resources.make('0')], first, original_closer)
    assert called == [stream] and resources.events == ['exit/0']
    assert resources.exits == [('0', RuntimeError, first)]
