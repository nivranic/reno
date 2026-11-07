"""Owned EX delivery tests using the actual r820 Windows fixture and files.

Consumer-local context/clock faults are explicit synthetic failures. Accepted
facts originate from actual namespace3 creation, never a seeded success DTO.
"""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import os

import pytest

from reno import personal_authority_namespace3 as locked
from reno import personal_authority_namespace3_owner as owner
from reno import storage_lifecycle as lifecycle
from tests.test_personal_authority_namespace3 import (
    namespace_env, originals, records, tree, sha)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='actual namespace3 original Windows HANDLE fixture')


def initialize(env, **options):
    return owner.initialize_namespace3(env['profile'], **env['args'], **env['expected'], **options)


def verification_arguments(result):
    return {'expected_namespace_reservation_sha256': result.namespace_reservation_sha256,
        'expected_root_seal_sha256': result.root_seal_sha256,
        'expected_header_sha256': result.header_sha256}


def verify(env, result, **options):
    return owner.verify_namespace3(env['profile'], **env['args'], **env['expected'],
        **verification_arguments(result), **options)


def error(call, suffix):
    with pytest.raises(owner.Namespace3OwnerError) as raised:
        call()
    assert raised.value.code == 'NAMESPACE3_OWNER_' + suffix


def test_actual_inner_facts_return_only_after_own_ex_normal_exit(namespace_env, monkeypatch):
    env = namespace_env
    actual_exclusive = lifecycle.exclusive
    actual_initialize = locked.initialize_namespace3_locked
    observed = {}
    @contextmanager
    def exclusive(profile, **options):
        with actual_exclusive(profile, **options) as guard:
            observed['guard'] = guard
            yield guard
            assert observed['facts'].scope == 'filesystem_namespace3_facts_only'
        assert lifecycle.current_guard() is None
        observed['exited'] = True
    def create(profile, **options):
        assert lifecycle.require_guard(profile, exclusive=True) is observed['guard']
        result = actual_initialize(profile, **options)
        observed['facts'] = result
        assert not observed.get('exited')
        return result
    monkeypatch.setattr(lifecycle, 'exclusive', exclusive)
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', create)
    result = initialize(env)
    assert result is observed['facts'] and observed['exited']
    assert result.origin_reservation_sha256 == sha(env['first'])
    assert result.origin_observation_sha256 == sha(env['second'])
    assert originals(env) == (env['first'], env['second'])
    assert lifecycle.current_guard() is None
    # A new ordinary EX can be acquired after the public owner returns.
    with actual_exclusive(env['profile']):
        assert lifecycle.require_guard(env['profile'], exclusive=True)


def test_complete_public_verify_keeps_all_fixture_files_unchanged(namespace_env):
    env = namespace_env
    result = initialize(env)
    before = tree(env)
    assert verify(env, result) == result and tree(env) == before
    assert lifecycle.current_guard() is None


def test_public_resume_uses_actual_unstarted_reservation_and_own_ex(namespace_env, monkeypatch):
    env = namespace_env
    from reno import personal_authority_namespace3_io as io
    from reno import personal_authority_namespace3_contract as contract
    actual = io.create_root_locked
    failure = RuntimeError('synthetic refusal before first root creation')
    @contextmanager
    def refused(*args, **kwargs):
        raise failure
        yield  # A context seam; no OS create or accepted DTO is synthesized.
    monkeypatch.setattr(io, 'create_root_locked', refused)
    with pytest.raises(RuntimeError) as raised:
        initialize(env)
    assert raised.value is failure
    reserved = (env['data'] / contract.RESERVATION_NAME).read_bytes()
    assert not (env['data'] / contract.ROOT_NAME).exists()
    assert lifecycle.current_guard() is None
    monkeypatch.setattr(io, 'create_root_locked', actual)
    result = owner.resume_unstarted_namespace3(env['profile'], **env['args'], **env['expected'],
        expected_namespace_reservation_sha256=sha(reserved))
    assert result.namespace_reservation_sha256 == sha(reserved)
    assert (env['data'] / contract.RESERVATION_NAME).read_bytes() == reserved
    assert verify(env, result) == result and lifecycle.current_guard() is None


@pytest.mark.parametrize('mode,guard_kind', [
    ('initialize', 'sh'), ('initialize', 'ex'), ('resume', 'sh'),
    ('resume', 'ex'), ('verify', 'sh'), ('verify', 'ex')],
    ids=['init-sh', 'init-ex', 'resume-sh', 'resume-ex', 'verify-sh', 'verify-ex'])
def test_public_entry_never_borrows_or_releases_existing_guard(namespace_env, mode, guard_kind):
    env = namespace_env
    context = lifecycle.shared if guard_kind == 'sh' else lifecycle.exclusive
    before = tree(env)
    with context(env['profile']) as guard:
        if mode == 'initialize':
            call = lambda: initialize(env)
        elif mode == 'resume':
            call = lambda: owner.resume_unstarted_namespace3(env['profile'], **env['args'], **env['expected'],
                expected_namespace_reservation_sha256='a' * 64)
        else:
            call = lambda: owner.verify_namespace3(env['profile'], **env['args'], **env['expected'],
                expected_namespace_reservation_sha256='a' * 64, expected_root_seal_sha256='b' * 64,
                expected_header_sha256='c' * 64)
        error(call, 'GUARD_ALREADY_HELD')
        assert lifecycle.current_guard() is guard
        lifecycle.require_guard(env['profile'], exclusive=guard_kind == 'ex')
    assert tree(env) == before and lifecycle.current_guard() is None


@pytest.mark.parametrize('field,value', [
    ('domain_id', '00000000-0000-0000-0000-000000000000'), ('facility_id', 1),
    ('init_operation_id', 'not-a-uuid'), ('expected_origin_reservation_sha256', 'BAD'),
    ('expected_origin_observation_sha256', None)],
    ids=['nil-domain', 'integer-facility', 'bad-init', 'bad-first-sha', 'missing-second-sha'])
def test_invalid_parameters_rejected_before_new_ex_effect(namespace_env, monkeypatch, field, value):
    env = namespace_env
    options = {**env['args'], **env['expected'], field: value}
    before = tree(env)
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid parameters must precede EX acquisition')
    monkeypatch.setattr(lifecycle, 'exclusive', forbidden)
    error(lambda: owner.initialize_namespace3(env['profile'], **options), 'INVALID_ARGUMENT')
    assert tree(env) == before


def test_false_valued_callback_is_once_before_ex_and_inner_receives_clock_only(namespace_env, monkeypatch):
    env = namespace_env
    actual = locked.initialize_namespace3_locked
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            self.calls += 1
            assert lifecycle.current_guard() is None
    callback = Callback()
    def create(profile, **options):
        assert callback.calls == 1 and options['check_cancelled'] is not callback
        for _ in range(3):
            options['check_cancelled']()
        return actual(profile, **options)
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', create)
    initialize(env, check_cancelled=callback)
    assert callback.calls == 1 and callback.bool_calls == 0


@pytest.mark.parametrize('failure', [RuntimeError('original callback'), SystemExit(821)],
                         ids=['ordinary', 'control'])
def test_callback_failure_is_original_object_before_ex(namespace_env, monkeypatch, failure):
    env = namespace_env
    before = tree(env)
    def forbidden(*args, **kwargs):
        raise AssertionError('failed callback must precede EX acquisition')
    def callback():
        raise failure
    monkeypatch.setattr(lifecycle, 'exclusive', forbidden)
    with pytest.raises(type(failure)) as raised:
        initialize(env, check_cancelled=callback)
    assert raised.value is failure and tree(env) == before


def test_guard_installed_by_callback_remains_callback_owned_and_blocks_new_owner(namespace_env):
    env = namespace_env
    before = tree(env)
    context = lifecycle.shared(env['profile'])
    entered = []
    def callback():
        entered.append(context.__enter__())
    try:
        error(lambda: initialize(env, check_cancelled=callback), 'GUARD_ALREADY_HELD')
        assert len(entered) == 1 and lifecycle.current_guard() is entered[0]
        assert tree(env) == before
    finally:
        if entered:
            context.__exit__(None, None, None)
    assert lifecycle.current_guard() is None


def test_actual_namespace_survives_synthetic_normal_ex_exit_failure_without_facts_delivery(namespace_env, monkeypatch):
    env = namespace_env
    original = lifecycle.exclusive
    failure = RuntimeError('synthetic wrapper failure after actual EX exit')
    @contextmanager
    def failed(profile, **options):
        with original(profile, **options) as guard:
            yield guard
        assert lifecycle.current_guard() is None
        raise failure
    monkeypatch.setattr(lifecycle, 'exclusive', failed)
    with pytest.raises(RuntimeError) as raised:
        initialize(env)
    assert raised.value is failure
    assert len(records(env)) == 3 and originals(env) == (env['first'], env['second'])
    assert lifecycle.current_guard() is None
    monkeypatch.setattr(lifecycle, 'exclusive', original)
    raw = records(env)
    facts = SimpleNamespace(namespace_reservation_sha256=sha(raw[0]),
        root_seal_sha256=sha(raw[1]), header_sha256=sha(raw[2]))
    assert verify(env, facts).header_sha256 == sha(raw[2])


@pytest.mark.parametrize('failure', [RuntimeError('original inner'), SystemExit(821)],
                         ids=['ordinary', 'control'])
def test_inner_original_failure_wins_over_synthetic_ex_cleanup_failure(namespace_env, monkeypatch, failure):
    env = namespace_env
    before = tree(env)
    original = lifecycle.exclusive
    @contextmanager
    def failed(profile, **options):
        try:
            with original(profile, **options) as guard:
                yield guard
        finally:
            raise RuntimeError('synthetic cleanup wrapper failure')
    def refused(*args, **kwargs):
        raise failure
    monkeypatch.setattr(lifecycle, 'exclusive', failed)
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', refused)
    with pytest.raises(type(failure)) as raised:
        initialize(env)
    assert raised.value is failure and tree(env) == before
    assert lifecycle.current_guard() is None


def test_original_outer_deadline_includes_actual_ex_normal_exit(namespace_env, monkeypatch):
    env = namespace_env
    original = lifecycle.exclusive
    clock = [0.0]
    @contextmanager
    def late(profile, **options):
        with original(profile, **options) as guard:
            yield guard
        clock[0] = 30.0
    monkeypatch.setattr(owner, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(lifecycle, 'exclusive', late)
    error(lambda: initialize(env), 'DEADLINE')
    assert len(records(env)) == 3 and originals(env) == (env['first'], env['second'])
    assert lifecycle.current_guard() is None


def test_duck_result_is_refused_after_inner_before_delivery(namespace_env, monkeypatch):
    env = namespace_env
    before = tree(env)
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', lambda *args, **kwargs: SimpleNamespace())
    error(lambda: initialize(env), 'RESULT_INVALID')
    assert tree(env) == before and lifecycle.current_guard() is None


def test_uninitialized_exact_facts_type_is_rejected_with_finite_error(namespace_env, monkeypatch):
    env = namespace_env
    before = tree(env)
    rejected = object.__new__(locked.Namespace3FilesystemFacts)
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', lambda *args, **kwargs: rejected)
    error(lambda: initialize(env), 'RESULT_INVALID')
    assert tree(env) == before and lifecycle.current_guard() is None


@pytest.mark.parametrize('field,value', [
    ('scope', 'writer_grant'), ('creation_kind', 'mkdir_then_open'), ('domain_id', '0' * 36),
    ('origin_reservation_sha256', 'a' * 64), ('header_sha256', 'BAD')],
    ids=['wrong-scope', 'wrong-creation', 'wrong-domain', 'wrong-first-binding', 'malformed-header-sha'])
def test_tampered_actual_facts_cannot_pass_context_scope_or_hash_delivery_gate(namespace_env, monkeypatch, field, value):
    env = namespace_env
    actual_facts = initialize(env)
    before = tree(env)
    rejected = replace(actual_facts, **{field: value})
    # Altered objects are rejection inputs only; accepted facts came from the
    # actual native factory before this consumer-local malformed-result seam.
    monkeypatch.setattr(locked, 'initialize_namespace3_locked', lambda *args, **kwargs: rejected)
    error(lambda: initialize(env), 'RESULT_INVALID')
    assert tree(env) == before and lifecycle.current_guard() is None


def test_verify_result_must_retain_caller_expected_successor_hashes(namespace_env, monkeypatch):
    env = namespace_env
    actual_facts = initialize(env)
    before = tree(env)
    rejected = replace(actual_facts, root_seal_sha256='a' * 64)
    monkeypatch.setattr(locked, 'verify_namespace3_locked', lambda *args, **kwargs: rejected)
    error(lambda: verify(env, actual_facts), 'RESULT_INVALID')
    assert tree(env) == before and lifecycle.current_guard() is None
