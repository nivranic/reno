"""Actual cleanup body with Fake I/O; no owner/token/guard/LIVE or factory."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from reno import personal_authority_preservation_source as source


class Halt(BaseException):
    pass


class HiddenTraceback(source.AuthorityRawCaptureError):
    def __getattribute__(self, name):
        if name == '__traceback__':
            raise Halt('ordinary traceback lookup must not block the original exit')
        return super().__getattribute__(name)


class HostileAddNote(RuntimeError):
    def add_note(self, _note):
        raise Halt('ordinary overridden note method must not run')


class HostileNotesLookup(RuntimeError):
    def __getattribute__(self, name):
        if name == '__notes__':
            raise Halt('optional builtin annotation may fail')
        return super().__getattribute__(name)


@pytest.fixture
def cleanup(monkeypatch):
    events, failures, exits, resources = [], {}, {}, {}
    budget = source._Budget(source._validated_options(source.RawCaptureLimits(
        max_read_bytes=8192, deadline_seconds=12)), None)
    # This is only an unregistered self for the cleanup algorithm. It has no
    # authority fields and cannot be passed to any public source consumer.
    state = SimpleNamespace(phase='idle', resources=[], files={}, directory_records={}, budget=budget)

    def step(label):
        events.append(label)
        error = failures.get(label)
        if error is not None:
            raise error

    class Resource:
        def __init__(self, name, kind):
            self.name, self.kind = name, kind
            self.reenter = False
        def close(self):
            step('close/' + self.name)
            if self.reenter:
                self.reenter = False
                source._Source.close(state, None)
        def __exit__(self, kind, error, _transported_traceback):
            # Never record or inspect traceback/frames/locals/notes.
            exits[self.name] = (kind, error)
            step('exit/' + self.name)
            if self.reenter:
                self.reenter = False
                source._Source.close(state, None)
            return False

    def add(name, kind):
        item = Resource(name, kind)
        resources[name] = item
        state.resources.append((kind, item))
        if kind == 'stream':
            state.files[name] = SimpleNamespace(stream=item, name=name)
        elif kind == 'directory':
            state.directory_records[id(item)] = SimpleNamespace(name=name)
        return item

    state.verify_file = lambda record: step('verify-file/' + record.name)
    state.verify_directory = lambda record: step('verify-directory/' + record.name)
    monkeypatch.setattr(source, '_close_stream_original', lambda stream: stream.close())
    def close(primary=None):
        return source._Source.close(state, primary)
    return SimpleNamespace(state=state, events=events, failures=failures, exits=exits,
        resources=resources, add=add, close=close, budget=budget)


def releases(cleanup):
    return [event for event in cleanup.events if event.startswith(('close/', 'exit/'))]


def finished(cleanup, expected):
    assert releases(cleanup) == expected
    assert cleanup.state.resources == [] and cleanup.state.phase == 'closed'
    assert cleanup.budget.terminal == 0


def three(cleanup):
    cleanup.add('directory', 'directory')
    cleanup.add('lock', 'lock')
    cleanup.add('stream', 'stream')


def test_success_keeps_reverse_order_validation_and_one_original_release(cleanup):
    three(cleanup)
    assert cleanup.close() is None
    assert cleanup.events == ['verify-file/stream', 'close/stream', 'exit/lock',
        'verify-directory/directory', 'exit/directory']
    assert cleanup.exits == {'lock': (None, None), 'directory': (None, None)}
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


@pytest.mark.parametrize('kind', [RuntimeError, Halt])
def test_caller_first_skips_validation_and_survives_all_release_failures(cleanup, kind):
    three(cleanup)
    first = kind('synthetic caller first')
    for event in ['close/stream', 'exit/lock', 'exit/directory']:
        cleanup.failures[event] = RuntimeError('synthetic later cleanup failure')
    assert cleanup.close(first) is first
    assert not any(event.startswith('verify-') for event in cleanup.events)
    assert cleanup.exits['lock'] == cleanup.exits['directory'] == (kind, first)
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


@pytest.mark.parametrize('form', ['source', 'wrapped_source', 'wrapped_cancel'])
@pytest.mark.parametrize('close_fails', [False, True])
def test_stream_validation_first_survives_close_and_all_remaining_releases(cleanup, form, close_fails):
    three(cleanup)
    first = Halt('synthetic budget cancellation') if form == 'wrapped_cancel' else source.AuthorityRawCaptureError('SOURCE_CHANGED')
    raw = source._First(first) if form.startswith('wrapped_') else first
    cleanup.failures['verify-file/stream'] = raw
    if close_fails:
        cleanup.failures['close/stream'] = RuntimeError('synthetic close failure')
    assert cleanup.close() is first
    assert cleanup.exits['lock'][1] is cleanup.exits['directory'][1] is first
    assert 'verify-directory/directory' not in cleanup.events
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


@pytest.mark.parametrize('damage', ['budget_first', 'missing_record'])
def test_stream_prevalidation_failure_still_attempts_its_close_and_later_resources(cleanup, damage):
    three(cleanup)
    first = Halt('synthetic previously remembered budget first')
    if damage == 'budget_first':
        cleanup.budget.remember(first)
    else:
        cleanup.state.files.clear()
    result = cleanup.close()
    if damage == 'budget_first':
        assert result is first
    else:
        assert type(result) is source.AuthorityRawCaptureError and result.code == 'AUTHORITY_RAW_CAPTURE_CLOSE_FAILED'
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


@pytest.mark.parametrize('form', ['source', 'wrapped_cancel', 'hidden_traceback'])
@pytest.mark.parametrize('exit_fails', [False, True])
def test_directory_exit_gets_raw_problem_and_later_exit_gets_selected_first(cleanup, form, exit_fails):
    cleanup.add('tail', 'lock')
    cleanup.add('directory', 'directory')
    if form == 'wrapped_cancel':
        first = Halt('synthetic validation cancellation')
        raw = source._First(first)
    else:
        first = (HiddenTraceback if form == 'hidden_traceback' else source.AuthorityRawCaptureError)('SOURCE_CHANGED')
        raw = first
    cleanup.failures['verify-directory/directory'] = raw
    if exit_fails:
        cleanup.failures['exit/directory'] = RuntimeError('synthetic directory exit failure')
    assert cleanup.close() is first
    assert cleanup.exits['directory'] == (type(raw), raw)
    assert cleanup.exits['tail'] == (type(first), first)
    finished(cleanup, ['exit/directory', 'exit/tail'])


@pytest.mark.parametrize('kind', ['stream', 'lock', 'directory'])
def test_first_release_failure_keeps_existing_public_error_mapping(cleanup, kind):
    cleanup.add('tail', 'lock')
    cleanup.add('first', kind)
    event = ('close/' if kind == 'stream' else 'exit/') + 'first'
    cleanup.failures[event] = RuntimeError('synthetic unknown cleanup failure')
    result = cleanup.close()
    assert type(result) is source.AuthorityRawCaptureError and result.code == 'AUTHORITY_RAW_CAPTURE_CLOSE_FAILED'
    assert cleanup.exits['tail'][1] is result
    finished(cleanup, [event, 'exit/tail'])


@pytest.mark.parametrize('kind', [HostileAddNote, HostileNotesLookup])
def test_optional_note_failure_cannot_replace_caller_first_or_stop_cleanup(cleanup, kind):
    three(cleanup)
    first = kind('synthetic caller first with hostile optional annotation')
    for event in ['close/stream', 'exit/lock', 'exit/directory']:
        cleanup.failures[event] = RuntimeError('synthetic secondary release failure')
    assert cleanup.close(first) is first
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


def test_caller_traceback_attribute_override_cannot_skip_original_exits(cleanup):
    three(cleanup)
    first = HiddenTraceback('SOURCE_CHANGED')
    assert cleanup.close(first) is first
    assert cleanup.exits['lock'][1] is cleanup.exits['directory'][1] is first
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


@pytest.mark.parametrize('kind', [RuntimeError, Halt])
def test_mapping_failure_keeps_actual_raw_first_and_attempts_remaining_resources(cleanup, monkeypatch, kind):
    three(cleanup)
    first = OSError('synthetic actual cleanup failure')
    cleanup.failures['close/stream'] = first
    def mapping_failed(_error, _fallback):
        raise kind('synthetic error construction failure')
    monkeypatch.setattr(source, '_error', mapping_failed)
    assert cleanup.close() is first
    assert cleanup.exits['lock'][1] is cleanup.exits['directory'][1] is first
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


def test_original_closer_reentry_does_not_release_detached_resources_twice(cleanup):
    three(cleanup)
    cleanup.resources['stream'].reenter = True
    assert cleanup.close() is None
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])


def test_missing_directory_record_still_passes_raw_lookup_error_to_actual_exit(cleanup):
    cleanup.add('tail', 'lock')
    cleanup.add('directory', 'directory')
    cleanup.state.directory_records.clear()
    result = cleanup.close()
    assert type(result) is source.AuthorityRawCaptureError and result.code == 'AUTHORITY_RAW_CAPTURE_CLOSE_FAILED'
    assert cleanup.exits['directory'][0] is KeyError
    assert cleanup.exits['tail'][1] is result
    finished(cleanup, ['exit/directory', 'exit/tail'])


def test_no_acquired_resource_still_closes_phase_without_fabricating_success_token(cleanup):
    assert cleanup.close() is None
    finished(cleanup, [])


def test_real_terminal_window_exit_fault_still_finalizes_detached_resources(cleanup, monkeypatch):
    three(cleanup)
    failure = Halt('synthetic fault after the real terminal window exited')
    original = cleanup.budget.terminal_window

    @contextmanager
    def terminal_exit_fault():
        with original():
            yield
        raise failure

    monkeypatch.setattr(cleanup.budget, 'terminal_window', terminal_exit_fault)
    with pytest.raises(Halt) as caught:
        cleanup.close()
    assert caught.value is failure
    finished(cleanup, ['close/stream', 'exit/lock', 'exit/directory'])
