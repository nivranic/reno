"""Real Namespace tail observations preserve the original gates and lock reads."""
from collections import Counter
from contextvars import copy_context
import hashlib
import importlib
from pathlib import Path
import time

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import (  # noqa: F401
    initialized, profile,
    test_real_effects_get_distinct_plans_and_ordinary_guard_stays_full,
    test_same_thread_copied_context_cannot_deliver_a_runtime_result,
    test_body_failure_keeps_first_error_and_completes_remaining_tail_checks,
    test_real_marker_change_before_body_return_is_rejected_by_full_tail,
    test_closing_requires_new_successful_physical_observations,
    test_failed_first_record_does_not_suppress_real_pf_or_other_records,
    test_registered_closing_observation_keeps_scope_and_cancellation_checks,
    test_ordinary_stack_check_inside_reading_scope_keeps_full_physical_checks,
    test_explicit_reading_scope_rejects_another_real_owner_or_retirement,
    test_custom_stack_check_receives_no_private_kwargs_and_preserves_error,
    test_existing_custody_failure_precedes_custom_stack_dispatch,
    test_runtime_writing_uses_namespace_ex_with_profile_shared_and_verifies_real_lock,
    test_runtime_writing_rejects_a_real_namespace_shared_owner,
    test_ordinary_write_helper_inside_reading_scope_rejects_real_bad_marker,
    test_custom_write_helper_keeps_original_arguments_and_recorded_first_error,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r715"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r715.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r715/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "62a1d30835444f14880d96579773d2c9259102db56d4998831e051767176cee5"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


def _reference_only(patch, namespace):
    original = namespace._runtime_tail_identity_binding_original

    def transparent(*args, **kwargs):
        return original(*args, **kwargs)

    patch.setattr(namespace, "_runtime_tail_identity_binding_original", transparent)


def _tail(namespace, owner, patch, *, writing=True, on_identity=None, window=None):
    """The real factory owns every object; count only the explicit tail call."""
    held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
    resources = tuple(namespace._held_io._scope_original(held.stack.scope).resources)
    leaf = namespace._held_io.original._identities
    counts = Counter()
    active = False

    def observe(record, **kwargs):
        result = leaf(record, **kwargs)
        if active and any(record is actual for actual in resources):
            counts[id(record)] += 1
            if on_identity is not None:
                on_identity(record, counts[id(record)])
        return result

    patch.setattr(namespace._held_io.original, "_identities", observe)
    with namespace._closed_runtime_directory_observation_original(owner):
        scope = namespace._RUNTIME_DIRECTORY_CURRENT.get()
        binding = namespace._runtime_directory_binding_original(scope)
        assert binding.held is held and binding.phase == "reading"
        before = {id(row): row.observed_bytes for row in resources}
        active = True
        if window is not None:
            window[0] = True
        try:
            namespace._runtime_body_tail_identity_original(held, scope, writing=writing)
        finally:
            active = False
            if window is not None:
                window[0] = False
        after = {id(row): row.observed_bytes for row in resources}
    return resources, counts, before, after, scope


@pytest.mark.parametrize("mode", ["shared", "exclusive"])
def test_fixed_tail_saves_only_nonlock_identity_sampling(namespace, initialized, monkeypatch, mode):
    profile = initialized
    with getattr(lifecycle, mode)(profile) as guard:
        with namespace._closed_namespace_runtime_owned_locked(profile, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert held.exclusive and held.storage_guard is guard
            with monkeypatch.context() as patch:
                rows, actual, before, after, first_scope = _tail(namespace, owner, patch)
            with monkeypatch.context() as patch:
                _reference_only(patch, namespace)
                expected_rows, expected, reference_before, reference_after, second_scope = _tail(namespace, owner, patch)
            assert len(rows) == len(expected_rows)
            assert all(a is b for a, b in zip(rows, expected_rows, strict=True))
            assert first_scope is not second_scope
            assert id(first_scope) not in namespace._RUNTIME_DIRECTORY_SCOPES
            assert id(second_scope) not in namespace._RUNTIME_DIRECTORY_SCOPES
            for row in rows:
                if row.resource_kind == "filehold":
                    assert actual[id(row)] == 1 and expected[id(row)] == 2
                    assert after[id(row)] == before[id(row)]
                    assert reference_after[id(row)] == reference_before[id(row)]
                else:
                    assert row.resource_kind == "lock" and row.locked and row.exclusive
                    assert actual[id(row)] == expected[id(row)] == 4
                    assert after[id(row)] - before[id(row)] == 1
                    assert reference_after[id(row)] - reference_before[id(row)] == 1
    assert all(row.finished for row in rows)


def test_nonwriting_and_next_tail_do_not_borrow_a_previous_observation(namespace, initialized, monkeypatch):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            with monkeypatch.context() as patch:
                rows, first, _, _, first_scope = _tail(namespace, owner, patch, writing=False)
            with monkeypatch.context() as patch:
                _reference_only(patch, namespace)
                _, reference, _, _, reference_scope = _tail(namespace, owner, patch, writing=False)
            assert first == reference == Counter({id(row): 1 for row in rows})
            with monkeypatch.context() as patch:
                _, next_counts, _, _, next_scope = _tail(namespace, owner, patch)
            assert all(next_counts[id(row)] == (4 if row.resource_kind == "lock" else 1) for row in rows)
            assert len({id(first_scope), id(reference_scope), id(next_scope)}) == 3
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None


def test_original_runtime_body_reaches_one_fresh_optimized_tail(namespace, initialized, monkeypatch):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            rows = tuple(namespace._held_io._scope_original(held.stack.scope).resources)
            tail = namespace._runtime_body_tail_identity_original
            identities = namespace._held_io.original._identities
            active, scopes, counts = [False], [], Counter()

            def observe_identity(record, **kwargs):
                result = identities(record, **kwargs)
                if active[0] and any(record is row for row in rows):
                    counts[id(record)] += 1
                return result

            def observe_tail(actual, scope, *, writing):
                assert actual is held and writing is True
                assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is scope
                scopes.append(scope)
                active[0] = True
                try:
                    return tail(actual, scope, writing=writing)
                finally:
                    active[0] = False

            with monkeypatch.context() as patch:
                patch.setattr(namespace._held_io.original, "_identities", observe_identity)
                patch.setattr(namespace, "_runtime_body_tail_identity_original", observe_tail)
                assert namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True) is held
            assert len(scopes) == 1 and id(scopes[0]) not in namespace._RUNTIME_DIRECTORY_SCOPES
            assert all(counts[id(row)] == (4 if row.resource_kind == "lock" else 1) for row in rows)


@pytest.mark.parametrize("custom", ["stack", "write", "identity", "directory", "profile", "guard"])
def test_custom_dispatch_keeps_the_original_remaining_observations(namespace, initialized, monkeypatch, custom):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            calls = []
            window = [False]
            with monkeypatch.context() as patch:
                if custom == "stack":
                    target, name = type(held.stack), "check"
                elif custom == "write":
                    target, name = namespace, "_require_runtime_namespace_write_original"
                elif custom == "identity":
                    target, name = namespace._held_io, "_verify_identity_record"
                elif custom == "profile":
                    target, name = namespace, "_require_profile"
                elif custom == "guard":
                    target, name = lifecycle, "require_guard"
                else:
                    target, name = namespace, "_closed_runtime_held_directory_verify_original"
                original = getattr(target, name)

                def observe(*args, **kwargs):
                    if window[0]:
                        calls.append((args, kwargs))
                    return original(*args, **kwargs)

                patch.setattr(target, name, observe)
                rows, actual, _, _, _ = _tail(namespace, owner, patch, window=window)
                first_calls = list(calls)
                calls.clear()
                _reference_only(patch, namespace)
                _, reference, _, _, _ = _tail(namespace, owner, patch, window=window)
                assert actual == reference
                assert calls == first_calls
                if custom in {"stack", "write", "directory"}:
                    assert all(len(args) == 1 and kwargs == {} for args, kwargs in calls)
                assert all(actual[id(row)] == (4 if row.resource_kind == "lock" else 2) for row in rows)


@pytest.mark.parametrize("failure", ["cancel", "deadline", "held_first"])
def test_late_second_sweep_keeps_original_cancellation_clock_and_first(namespace, initialized, monkeypatch, failure):
    inner, fired = [], []
    first = RuntimeError("real registered resource first during second tail sweep")
    expected_type = (namespace._closed_io.SharedIoCancelledError if failure == "cancel"
                     else RuntimeError if failure == "held_first" else namespace.NamespaceError)
    with pytest.raises(expected_type) as outer:
        with lifecycle.shared(initialized):
            with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
                held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
                custody = namespace._closed_stack_custody_original(held.stack)
                io_scope = namespace._held_io._scope_original(held.stack.scope)
                original_clock = (held.budget.started, held.stack.clock.started, held.stack.clock.deadline)

                def inject(record, count):
                    if record.resource_kind == "lock" and count == 2:
                        assert not fired
                        fired.append(record)
                        if failure == "cancel":
                            namespace._closed_io.request_cancellation(custody.cancellation)
                        elif failure == "held_first":
                            namespace._held_io._remember_failure(record, first)
                        else:
                            deadline = held.budget.started + namespace.DEADLINE_SECONDS
                            while time.monotonic() <= deadline:
                                time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))

                with monkeypatch.context() as patch:
                    try:
                        _tail(namespace, owner, patch, on_identity=inject)
                    except BaseException as error:
                        inner.append(error)
                        assert (held.budget.started, held.stack.clock.started, held.stack.clock.deadline) == original_clock
                        raise
    assert len(fired) == len(inner) == 1
    assert custody.first is inner[0]
    if failure == "held_first":
        assert inner[0] is first and outer.value is first
        assert io_scope.first is first
    elif failure == "cancel":
        assert type(inner[0]) is namespace._ProducerFailure
        assert inner[0].original is outer.value is custody.cancellation._failure
    else:
        assert inner[0].code == "NAMESPACE_DEADLINE"
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None


@pytest.mark.parametrize("change_at", ["first_tail_end", "second_tail_lock"])
def test_late_identity_dispatch_preserves_only_the_original_remaining_chain(namespace, initialized, monkeypatch, change_at):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            resources = tuple(namespace._held_io._scope_original(held.stack.scope).resources)
            original = namespace._held_io._verify_identity_record
            changed, calls, window = [], [], [False]
            with monkeypatch.context() as patch:
                def custom(*args, **kwargs):
                    if window[0]:
                        calls.append((args, kwargs))
                    return original(*args, **kwargs)

                def replace(record, count):
                    at_first_end = record is resources[-1] and count == 1
                    at_second_lock = record.resource_kind == "lock" and count == 2
                    if not changed and (at_first_end if change_at == "first_tail_end" else at_second_lock):
                        changed.append(record)
                        patch.setattr(namespace._held_io, "_verify_identity_record", custom)

                rows, counts, _, _, _ = _tail(namespace, owner, patch, on_identity=replace, window=window)
            assert len(changed) == 1
            lock_index = next(index for index, row in enumerate(rows) if row.resource_kind == "lock")
            for index, row in enumerate(rows):
                if row.resource_kind == "lock":
                    assert counts[id(row)] == 4
                else:
                    expected = 2 if change_at == "first_tail_end" or index > lock_index else 1
                    assert counts[id(row)] == expected
            expected_custom = len(rows) + 2 if change_at == "first_tail_end" else len(rows) - lock_index - 1 + 2
            assert len(calls) == expected_custom


def test_late_expected_identity_change_is_rejected_before_a_result(namespace, initialized, monkeypatch):
    changed, inner = [], []
    with pytest.raises(namespace._closed_io.SharedIoError) as outer:
        with lifecycle.shared(initialized):
            with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
                held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
                io_scope = namespace._held_io._scope_original(held.stack.scope)
                custody = namespace._closed_stack_custody_original(held.stack)
                target = io_scope.resources[-1]
                expected = dict(target.path_identity)
                field = next(key for key, value in expected.items() if type(value) is int)
                with monkeypatch.context() as patch:
                    def mutate(record, count):
                        if record.resource_kind == "lock" and count == 2:
                            assert not changed
                            changed.append(target)
                            target.path_identity[field] += 1

                    try:
                        _tail(namespace, owner, patch, on_identity=mutate)
                    except BaseException as error:
                        inner.append(error)
                        raise
                    finally:
                        target.path_identity.clear()
                        target.path_identity.update(expected)
    assert changed == [target] and inner == [outer.value]
    assert outer.value.code == "OWNED_IO_ORIGINAL_FILE_CHANGED"
    assert target.failure is io_scope.first is outer.value
    assert custody.first is outer.value


def test_recorded_first_precedes_any_custom_stack_descriptor_in_tail(namespace, initialized, monkeypatch):
    errors, descriptor_calls = [], []

    class UnexpectedCheck:
        def __get__(self, instance, owner):
            descriptor_calls.append(instance)
            raise AssertionError("A recorded original failure must precede this descriptor")

    with pytest.raises(namespace.NamespaceError) as outer:
        with lifecycle.shared(initialized):
            with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
                held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
                custody = namespace._closed_stack_custody_original(held.stack)
                with namespace._closed_runtime_directory_observation_original(owner):
                    scope = namespace._RUNTIME_DIRECTORY_CURRENT.get()
                    try:
                        copy_context().run(namespace._runtime_directory_check_original, scope)
                    except namespace.NamespaceError as original:
                        errors.append(original)
                    assert len(errors) == 1 and custody.first is errors[0]
                    with monkeypatch.context() as patch:
                        patch.setattr(type(held.stack), "check", UnexpectedCheck())
                        namespace._runtime_body_tail_identity_original(held, scope, writing=True)
    assert outer.value is errors[0] and outer.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
    assert descriptor_calls == [] and custody.first is outer.value
