"""Real Namespace preread sharing keeps full reads, hashes, clocks and lock checks."""
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
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r719"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r719.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r719/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "1cf99b186b8193725abd4a6fda2bfa4a3c7062efaf11730f9ec38cc072933cbd"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


def _reference_only(patch, namespace):
    original = namespace._runtime_preread_identity_binding_original

    def transparent(*args, **kwargs):
        return original(*args, **kwargs)

    patch.setattr(namespace, "_runtime_preread_identity_binding_original", transparent)


def _preread(namespace, owner, patch, *, on_identity=None, window=None):
    """Use a genuine factory/reading scope; observe only the two owned calls."""
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
            namespace._runtime_body_preread_identity_original(held, scope)
        finally:
            active = False
            if window is not None:
                window[0] = False
        after = {id(row): row.observed_bytes for row in resources}
    return resources, counts, before, after, scope


@pytest.mark.parametrize("mode", ["shared", "exclusive"])
def test_fixed_preread_saves_only_one_nonlock_identity_sampling(namespace, initialized, monkeypatch, mode):
    with getattr(lifecycle, mode)(initialized) as guard:
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert held.storage_guard is guard and held.exclusive
            with monkeypatch.context() as patch:
                rows, actual, before, after, first_scope = _preread(namespace, owner, patch)
            with monkeypatch.context() as patch:
                _reference_only(patch, namespace)
                expected_rows, expected, ref_before, ref_after, second_scope = _preread(namespace, owner, patch)
            assert all(a is b for a, b in zip(rows, expected_rows, strict=True))
            assert first_scope is not second_scope
            for scope in (first_scope, second_scope):
                assert id(scope) not in namespace._RUNTIME_DIRECTORY_SCOPES
            fileholds = [row for row in rows if row.resource_kind == "filehold"]
            assert len(fileholds) > 1
            for row in rows:
                if row.resource_kind == "filehold":
                    # Every later file still optimizes after earlier real reads
                    # have changed their observed_bytes counters.
                    assert actual[id(row)] == 2 and expected[id(row)] == 3
                    assert after[id(row)] - before[id(row)] == row.byte_size > 0
                    assert ref_after[id(row)] - ref_before[id(row)] == row.byte_size
                else:
                    assert row.resource_kind == "lock" and row.locked and row.exclusive
                    assert actual[id(row)] == expected[id(row)] == 3
                    assert after[id(row)] - before[id(row)] == 1
                    assert ref_after[id(row)] - ref_before[id(row)] == 1
    assert all(row.finished for row in rows)


def test_next_preread_uses_a_new_scope_and_fresh_reads(namespace, initialized, monkeypatch):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            with monkeypatch.context() as patch:
                rows, first, before, after, first_scope = _preread(namespace, owner, patch)
            with monkeypatch.context() as patch:
                _, second, next_before, next_after, second_scope = _preread(namespace, owner, patch)
            assert first_scope is not second_scope and first == second
            for row in rows:
                assert first[id(row)] == (3 if row.resource_kind == "lock" else 2)
                size = 1 if row.resource_kind == "lock" else row.byte_size
                assert after[id(row)] - before[id(row)] == next_after[id(row)] - next_before[id(row)] == size
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
    assert all(row.finished for row in rows)


def test_original_runtime_body_naturally_uses_one_preread(namespace, initialized, monkeypatch):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            rows = tuple(namespace._held_io._scope_original(held.stack.scope).resources)
            original = namespace._runtime_body_preread_identity_original
            identities = namespace._held_io.original._identities
            active, scopes, counts, consumed = [False], [], Counter(), []

            def observe_identity(record, **kwargs):
                result = identities(record, **kwargs)
                if active[0] and any(record is row for row in rows):
                    counts[id(record)] += 1
                return result

            def observe_preread(actual, scope):
                assert actual is held and namespace._RUNTIME_DIRECTORY_CURRENT.get() is scope
                scopes.append(scope)
                before = {id(row): row.observed_bytes for row in rows}
                active[0] = True
                try:
                    return original(actual, scope)
                finally:
                    active[0] = False
                    consumed.append({id(row): row.observed_bytes - before[id(row)] for row in rows})

            with monkeypatch.context() as patch:
                patch.setattr(namespace._held_io.original, "_identities", observe_identity)
                patch.setattr(namespace, "_runtime_body_preread_identity_original", observe_preread)
                assert namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True) is held
            assert len(scopes) == len(consumed) == 1
            assert id(scopes[0]) not in namespace._RUNTIME_DIRECTORY_SCOPES
            for row in rows:
                assert counts[id(row)] == (3 if row.resource_kind == "lock" else 2)
                assert consumed[0][id(row)] == (1 if row.resource_kind == "lock" else row.byte_size)


@pytest.mark.parametrize("custom", ["stack", "scope", "file", "read", "identity", "directory", "profile", "guard", "read_leaf", "seek_leaf", "hash", "read_result"])
def test_custom_dispatch_retains_original_scope_reads(namespace, initialized, monkeypatch, custom):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            calls, window = [], [False]
            targets = {
                "stack": (type(held.stack), "check"),
                "scope": (namespace._held_io, "_verify_scope_original"),
                "file": (namespace._held_io, "_verify_file_record"),
                "read": (namespace._held_io, "_read_owned"),
                "identity": (namespace._held_io, "_verify_identity_record"),
                "directory": (namespace, "_closed_runtime_held_directory_verify_original"),
                "profile": (namespace, "_require_profile"),
                "guard": (lifecycle, "require_guard"),
            }
            if custom in {"read_leaf", "seek_leaf"}:
                target = (namespace._held_io.original._BUFFER_READ if custom == "read_leaf"
                          else namespace._held_io.original._BUFFER_SEEK)
                row = next(row for row in namespace._held_io._scope_original(held.stack.scope).resources
                           if row.resource_kind == "filehold")
                name = row.kind
                original = target[name]
            else:
                targets["hash"] = (namespace._held_io.hashlib, "sha256")
                targets["read_result"] = (namespace._held_io, "HeldRead")
                target, name = targets[custom]
                original = getattr(target, name)

            def observe(*args, **kwargs):
                if window[0]:
                    calls.append((args, kwargs))
                return original(*args, **kwargs)

            with monkeypatch.context() as patch:
                if custom in {"read_leaf", "seek_leaf"}:
                    patch.setitem(target, name, observe)
                else:
                    patch.setattr(target, name, observe)
                rows, actual, before, after, _ = _preread(namespace, owner, patch, window=window)
                first_calls = list(calls)
                calls.clear()
                _reference_only(patch, namespace)
                _, reference, ref_before, ref_after, _ = _preread(namespace, owner, patch, window=window)
                assert actual == reference == Counter({id(row): 3 for row in rows})
                assert calls == first_calls
                if custom in {"stack", "scope", "file", "directory"}:
                    assert calls and all(len(args) == 1 and kwargs == {} for args, kwargs in calls)
                for row in rows:
                    size = 1 if row.resource_kind == "lock" else row.byte_size
                    assert after[id(row)] - before[id(row)] == ref_after[id(row)] - ref_before[id(row)] == size


@pytest.mark.parametrize("change_at", ["first_stack_end", "second_scope_lock"])
def test_late_identity_dispatch_never_replays_reads(namespace, initialized, monkeypatch, change_at):
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
                    if not changed and (at_first_end if change_at == "first_stack_end" else at_second_lock):
                        changed.append(record)
                        patch.setattr(namespace._held_io, "_verify_identity_record", custom)

                rows, counts, before, after, _ = _preread(namespace, owner, patch, on_identity=replace, window=window)
            assert len(changed) == 1
            lock_index = next(index for index, row in enumerate(rows) if row.resource_kind == "lock")
            for index, row in enumerate(rows):
                expected = 3 if row.resource_kind == "lock" or change_at == "first_stack_end" or index > lock_index else 2
                assert counts[id(row)] == expected
                assert after[id(row)] - before[id(row)] == (1 if row.resource_kind == "lock" else row.byte_size)
            expected_custom = 2 * len(rows) if change_at == "first_stack_end" else 1 + 2 * (len(rows) - lock_index - 1)
            assert len(calls) == expected_custom


def test_late_native_read_dispatch_runs_only_unread_remainder(namespace, initialized, monkeypatch):
    with lifecycle.shared(initialized):
        with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            resources = tuple(namespace._held_io._scope_original(held.stack.scope).resources)
            target = next(row for row in resources if row.resource_kind == "filehold")
            original_read = namespace._held_io.original._BUFFER_READ[target.kind]
            changed, calls, window = [], [], [False]
            with monkeypatch.context() as patch:
                def late_read(buffer, wanted):
                    if window[0]:
                        calls.append((buffer, wanted))
                    return original_read(buffer, wanted)

                def replace(record, count):
                    # count 2 is the real read's final physical identity, since
                    # its leading sample was shared with the original stack.
                    if record is target and count == 2:
                        assert record.observed_bytes >= record.byte_size
                        assert not changed
                        changed.append(record)
                        patch.setitem(namespace._held_io.original._BUFFER_READ, target.kind, late_read)

                rows, counts, before, after, _ = _preread(namespace, owner, patch, on_identity=replace, window=window)
            assert changed == [target] and calls
            assert all(buffer is not target.buffer for buffer, _ in calls)
            target_index = next(i for i, row in enumerate(rows) if row is target)
            for index, row in enumerate(rows):
                assert counts[id(row)] == (3 if row.resource_kind == "lock" or index > target_index else 2)
                assert after[id(row)] - before[id(row)] == (1 if row.resource_kind == "lock" else row.byte_size)


@pytest.mark.parametrize("failure", ["cancel", "deadline", "held_first"])
def test_preread_keeps_late_cancellation_clock_and_first(namespace, initialized, monkeypatch, failure):
    inner, fired = [], []
    first = RuntimeError("actual registered first during preread full scope")
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
                        _preread(namespace, owner, patch, on_identity=inject)
                    except BaseException as error:
                        inner.append(error)
                        assert (held.budget.started, held.stack.clock.started, held.stack.clock.deadline) == original_clock
                        raise
    assert len(fired) == len(inner) == 1 and custody.first is inner[0]
    if failure == "held_first":
        assert inner[0] is outer.value is io_scope.first is first
    elif failure == "cancel":
        assert type(inner[0]) is namespace._ProducerFailure
        assert inner[0].original is outer.value is custody.cancellation._failure
    else:
        assert inner[0].code == "NAMESPACE_DEADLINE"
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None


def test_real_full_read_rejects_corrupt_expected_sha(namespace, initialized, monkeypatch):
    errors, consumed = [], []
    with pytest.raises(namespace._closed_io.SharedIoError) as outer:
        with lifecycle.shared(initialized):
            with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
                held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
                io_scope = namespace._held_io._scope_original(held.stack.scope)
                target = next(row for row in io_scope.resources if row.resource_kind == "filehold")
                expected = target.sha256
                before = target.observed_bytes
                target.sha256 = "0" * 64
                with monkeypatch.context() as patch:
                    try:
                        _preread(namespace, owner, patch)
                    except BaseException as error:
                        errors.append(error)
                        consumed.append(target.observed_bytes - before)
                        raise
                    finally:
                        target.sha256 = expected
    assert errors == [outer.value] and outer.value.code == "OWNED_IO_ORIGINAL_FILE_CHANGED"
    assert consumed == [target.byte_size] and target.byte_size > 0
    assert target.failure is io_scope.first is outer.value


def test_late_expected_identity_change_is_rejected_during_preread(namespace, initialized, monkeypatch):
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
                        _preread(namespace, owner, patch, on_identity=mutate)
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




def test_recorded_first_precedes_custom_stack_descriptor_in_preread(namespace, initialized, monkeypatch):
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
                        namespace._runtime_body_preread_identity_original(held, scope)
    assert outer.value is errors[0] and outer.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
    assert descriptor_calls == [] and custody.first is outer.value




def test_final_native_identity_sample_still_rejects_after_true_read(namespace, initialized, monkeypatch):
    errors, samples, read_counts, window = [], [], [], [False]
    with pytest.raises(namespace._closed_io.SharedIoError) as outer:
        with lifecycle.shared(initialized):
            with namespace._closed_namespace_runtime_owned_locked(initialized, exclusive=True) as owner:
                held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
                io_scope = namespace._held_io._scope_original(held.stack.scope)
                target = next(row for row in io_scope.resources if row.resource_kind == "filehold")
                real_identity = namespace._held_io.original._identities
                before = target.observed_bytes

                def faulty_tail_sample(record, **kwargs):
                    path, handle = real_identity(record, **kwargs)
                    if window[0] and record is target:
                        samples.append(record)
                        if len(samples) == 2:
                            # Inject a changed native sample after genuine IO;
                            # the actual file and expected custody stay intact.
                            read_counts.append(record.observed_bytes - before)
                            path = dict(path)
                            field = next(key for key, value in path.items() if type(value) is int)
                            path[field] += 1
                    return path, handle

                with monkeypatch.context() as patch:
                    patch.setattr(namespace._held_io.original, "_identities", faulty_tail_sample)
                    try:
                        _preread(namespace, owner, patch, window=window)
                    except BaseException as error:
                        errors.append(error)
                        raise
    assert errors == [outer.value] and outer.value.code == "OWNED_IO_ORIGINAL_FILE_CHANGED"
    assert samples == [target, target] and read_counts == [target.byte_size]
    assert target.failure is io_scope.first is outer.value
