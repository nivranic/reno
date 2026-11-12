"""Real Namespace owners exercise a source-bound guard observation lifetime.

Hooks below only observe original calls or inject explicit test failures. They
are not product callbacks, and never manufacture an owner, binding or token.
"""
from collections import Counter
from contextvars import copy_context
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle, sync_media as safe


@pytest.fixture
def namespace(monkeypatch):
    revision = os.environ.get("H01_NAMESPACE_RUNTIME_PACKAGE", "r666")
    digests = {
        "r653": "41357b1ef3d8524516b967fd2bd3486eabb77a98d6a283c5a02be02360edc189",
        "r655": "05234258143204bdba4d8ec229d5514c022d71ed9ea3e7d5c7d4fb5d4991a98b",
        "r657": "c6328e363a49d105f1203f7cfbd470f1dceb599701cd0216061d6aaf30df973b",
        "r661": "57e6cec0239b1dc4d9c4bbe51542e2017d469d874b15cd916a6a8b2a626ccf6e",
        "r663": "4120e9db3dd4448313623365bcdc0b3fbb9b34f81c9421cf8bec5244abd96870",
        "r666": "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073",
    }
    assert revision in digests
    graph = Path(__file__).resolve().parents[1] / f"data/t16-runtime-origin-graph-{revision}"
    monkeypatch.syspath_prepend(str(graph))
    namespace = import_historical_snapshot(f"authority_runtime_origin_{revision}.personal_authority_namespace")
    path = graph / f"authority_runtime_origin_{revision}/personal_authority_namespace.py"
    assert Path(namespace.__file__).resolve() == path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digests[revision]
    assert namespace.lifecycle is lifecycle and namespace.DEADLINE_SECONDS == 30.0
    return namespace


@pytest.fixture
def profile(tmp_path):
    # Preserve the original consumer's real SQLite/node/lifecycle fixture.
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve()
    assert tmp_path.resolve().is_relative_to(qa)
    roots = {name: tmp_path / name for name in ("data", "media", "reports", "checkpoints")}
    for path in roots.values():
        path.mkdir(mode=0o700)
    actual = lifecycle.StorageProfile(roots["data"] / "reno.db", roots["media"],
        roots["reports"], roots["checkpoints"])
    connection = sqlite3.connect(actual.database)
    try:
        connection.execute("CREATE TABLE original_fixture (key TEXT PRIMARY KEY, value BLOB)")
        connection.execute("INSERT INTO original_fixture VALUES ('original',?)", (b"original SQL unchanged",))
        connection.commit()
    finally:
        connection.close()
    (roots["data"] / "node-identity.json").write_bytes(
        json.dumps({"instance_id": str(uuid.uuid4())}).encode("utf-8"))
    lifecycle.initialize(actual)
    return actual


@pytest.fixture
def initialized(namespace, profile):
    before = Path(safe._native_path(profile.database)).read_bytes()
    with lifecycle.exclusive(profile):
        namespace.initialize_explicit_locked(profile, init_operation_id=str(uuid.uuid4()),
            domain_id=str(uuid.uuid4()), facility_id=str(uuid.uuid4()))
    yield profile
    assert Path(safe._native_path(profile.database)).read_bytes() == before


class _ChangedStat:
    def __init__(self, original, **changes):
        self._original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _current_binding(namespace, owner):
    scope = namespace._RUNTIME_DIRECTORY_CURRENT.get()
    assert scope is not None
    binding = namespace._runtime_directory_binding_original(scope)
    assert binding.held.original is owner
    assert namespace._CLOSED_NAMESPACE_OWNERS[id(owner)] is binding.held
    assert binding.guard_plan is not None and binding.guard_plan.guard is binding.held.storage_guard
    assert binding.phase == "reading"
    return scope, binding


def _assert_retired(namespace, scopes):
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
    assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
    assert all(id(scope) not in namespace._RUNTIME_DIRECTORY_SCOPES for scope in scopes)


def test_real_effects_get_distinct_plans_and_ordinary_guard_stays_full(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_stat = os.stat
    target = safe._native_path(profile.database.parent)
    scopes, plans, physical_checks = [], [], []

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            physical_checks.append(path)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    def observe_body(owner, *, writing=False):
        scope, binding = _current_binding(namespace, owner)
        scopes.append(scope)
        plans.append(binding.guard_plan)
        result = original_body(owner, writing=writing)
        # This is an explicit test-only stat fault inside a genuine read scope.
        # Ordinary writers must still use their full guard, not this plan.
        with monkeypatch.context() as fault:
            fault.setattr(os, "stat", changed_stat)
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                lifecycle.require_guard(profile)
            assert caught.value.code == "STORAGE_UNSAFE_PATH"
        assert namespace._runtime_directory_binding_original(scope).guard_plan is binding.guard_plan
        return result

    with lifecycle.shared(profile) as guard:
        with namespace._closed_namespace_owned_locked(profile) as owner:
            with monkeypatch.context() as patch:
                patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", observe_body)
                first = namespace._verify_closed_namespace_owner_runtime_native(owner)
                second = namespace._verify_closed_namespace_owner_runtime_native(owner)
            assert first is second is namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert all(plan.guard is guard for plan in plans)
    assert len(plans) == 2 and plans[0] is not plans[1] and scopes[0] is not scopes[1]
    assert len(physical_checks) == 2
    _assert_retired(namespace, scopes)


def test_same_thread_copied_context_cannot_deliver_a_runtime_result(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    scopes, errors, delivered = [], [], []

    def copied_context_body(owner, *, writing=False):
        scope, _ = _current_binding(namespace, owner)
        scopes.append(scope)
        result = original_body(owner, writing=writing)
        try:
            copy_context().run(namespace._runtime_directory_check_original, scope)
        except namespace.NamespaceError as failure:
            errors.append(failure)
            raise
        return result

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", copied_context_body)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    assert len(errors) == 1 and caught.value is errors[0]
    assert caught.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
    assert delivered == [] and len(scopes) == 1
    _assert_retired(namespace, scopes)


def test_body_failure_keeps_first_error_and_completes_remaining_tail_checks(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_directories = lifecycle._verify_guard_plan_directories_original
    original_files = lifecycle._verify_guard_plan_files_original
    original_record = namespace._closed_directory_verify_with_paths_original
    original_identity = namespace.pf.directory_identity
    original_stack = namespace._closed_stack_check_original
    sentinel = RuntimeError("synthetic original runtime body failure")
    tail_failure = OSError("synthetic first directory tail failure")
    armed = False
    scopes, expected_records, expected_directories, events = [], set(), set(), []
    delivered = []

    def closing():
        if not armed or not scopes:
            return False
        binding = namespace._RUNTIME_DIRECTORY_SCOPES.get(id(scopes[0]))
        return binding is not None and binding.phase == "closing"

    def failing_body(owner, *, writing=False):
        nonlocal armed
        scope, binding = _current_binding(namespace, owner)
        scopes.append(scope)
        expected_records.update(id(row[0]) for row in binding.graph[3])
        expected_directories.update(id(record.original) for record, _ in binding.identities)
        original_body(owner, writing=writing)
        armed = True
        raise sentinel

    def observe_directories(plan):
        is_tail = closing()
        if is_tail:
            events.append(("guard_directories", id(plan)))
        result = original_directories(plan)
        if is_tail and sum(kind == "guard_directories" for kind, _ in events) == 1:
            raise tail_failure
        return result

    def observe_files(plan, requested_profile, exclusive=False):
        if closing():
            events.append(("guard_files", id(plan)))
        return original_files(plan, requested_profile, exclusive=exclusive)

    def observe_record(record, observed):
        if closing():
            events.append(("record", id(record)))
        return original_record(record, observed)

    def observe_identity(directory):
        if closing():
            events.append(("identity", id(directory)))
        return original_identity(directory)

    def observe_stack(stack, **kwargs):
        if closing():
            events.append(("stack", id(stack)))
        return original_stack(stack, **kwargs)

    with pytest.raises(RuntimeError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", failing_body)
                    patch.setattr(lifecycle, "_verify_guard_plan_directories_original", observe_directories)
                    patch.setattr(lifecycle, "_verify_guard_plan_files_original", observe_files)
                    patch.setattr(namespace, "_closed_directory_verify_with_paths_original", observe_record)
                    patch.setattr(namespace.pf, "directory_identity", observe_identity)
                    patch.setattr(namespace, "_closed_stack_check_original", observe_stack)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    assert caught.value is sentinel and delivered == []
    kinds = Counter(kind for kind, _ in events)
    assert kinds["guard_directories"] == 2 and kinds["guard_files"] == 1
    assert {value for kind, value in events if kind == "record"} == expected_records
    assert {value for kind, value in events if kind == "identity"} == expected_directories
    assert kinds["stack"] >= 1
    assert any("cleanup also failed" in note for note in getattr(sentinel, "__notes__", ()))
    _assert_retired(namespace, scopes)


def test_real_marker_change_before_body_return_is_rejected_by_full_tail(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_files = lifecycle._verify_guard_plan_files_original
    scopes, delivered, tail_errors = [], [], []
    changed = False

    with lifecycle.shared(profile) as guard:
        marker = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
        with pytest.raises(namespace.NamespaceError) as caught:
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with marker.open("r+b", buffering=0) as writer:
                    def corrupt_after_original_body(actual_owner, *, writing=False):
                        nonlocal changed
                        scope, binding = _current_binding(namespace, actual_owner)
                        assert actual_owner is owner and binding.guard_plan.guard is guard
                        scopes.append(scope)
                        result = original_body(actual_owner, writing=writing)
                        # Preserve identity, file length and the held first-byte
                        # lock; mutate actual marker data only after the body.
                        writer.seek(1)
                        assert writer.write(b"!") == 1
                        writer.seek(1)
                        assert writer.read(1) == b"!"
                        changed = True
                        return result

                    def observe_full_tail(plan, requested_profile, exclusive=False):
                        binding = (namespace._RUNTIME_DIRECTORY_SCOPES.get(id(scopes[0]))
                                   if scopes else None)
                        closing = changed and binding is not None and binding.phase == "closing"
                        try:
                            return original_files(plan, requested_profile, exclusive=exclusive)
                        except lifecycle.StorageLifecycleError as failure:
                            if closing:
                                tail_errors.append(failure)
                            raise

                    try:
                        with monkeypatch.context() as patch:
                            patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", corrupt_after_original_body)
                            patch.setattr(lifecycle, "_verify_guard_plan_files_original", observe_full_tail)
                            delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
                    finally:
                        # Restore while the real owner/guard still owns every
                        # original handle, before their unchanged exit checks.
                        writer.seek(1)
                        assert writer.write(lifecycle._MARKER[1:2]) == 1
                        writer.seek(1)
                        assert writer.read(1) == lifecycle._MARKER[1:2]
        assert caught.value.code == "STORAGE_LOCK_INVALID"
        assert guard.verify() is guard
    assert changed and delivered == [] and len(scopes) == 1
    assert len(tail_errors) == 1 and tail_errors[0].code == "STORAGE_LOCK_INVALID"
    _assert_retired(namespace, scopes)


@pytest.mark.parametrize("changed_object", ["path", "native_handle"])
def test_closing_requires_new_successful_physical_observations(namespace, initialized, monkeypatch, changed_object):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_stat, original_native = os.stat, namespace._closed_directory_native
    scopes, observations, delivered = [], [], []
    target_path = target_handle = None

    def closing():
        binding = namespace._RUNTIME_DIRECTORY_SCOPES.get(id(scopes[0])) if scopes else None
        return binding is not None and binding.phase == "closing"

    def observe_body(owner, *, writing=False):
        nonlocal target_path, target_handle
        scope, binding = _current_binding(namespace, owner)
        scopes.append(scope)
        record = next(row[0] for row in binding.graph[3] if row[1] is binding.held.facts)
        target_path = safe._native_path(record.original.path)
        target_handle = record.handles[-1]
        return original_body(owner, writing=writing)

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if changed_object == "path" and closing() and path == target_path:
            observations.append("path")
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    def changed_native(handle):
        result = original_native(handle)
        if changed_object == "native_handle" and closing() and handle == target_handle:
            observations.append("native_handle")
            return result[0], result[1] + 1
        return result

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", observe_body)
                    patch.setattr(os, "stat", changed_stat)
                    patch.setattr(namespace, "_closed_directory_native", changed_native)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    expected = ("NAMESPACE_UNSAFE_PATH" if changed_object == "path"
                else "NAMESPACE_STORE_ORIGINAL_DIRECTORY_HANDLE_CHANGED")
    assert caught.value.code == expected and observations and delivered == []
    _assert_retired(namespace, scopes)


def test_failed_first_record_does_not_suppress_real_pf_or_other_records(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_native = namespace._closed_directory_native
    original_identity = namespace.pf.directory_identity
    original_stack = namespace._closed_stack_check_original
    sentinel = RuntimeError("synthetic first closing record failure")
    scopes, delivered, native_seen, identities = [], [], [], []
    expected_handles, expected_directories = set(), set()
    target_handle = None
    failed = False
    stack_attempts = []

    def closing():
        binding = namespace._RUNTIME_DIRECTORY_SCOPES.get(id(scopes[0])) if scopes else None
        return binding is not None and binding.phase == "closing"

    def observe_body(owner, *, writing=False):
        nonlocal target_handle
        scope, binding = _current_binding(namespace, owner)
        scopes.append(scope)
        expected_handles.update(handle for row in binding.graph[3] for handle in row[0].handles)
        expected_directories.update(id(record.original) for record, _ in binding.identities)
        target_handle = binding.graph[3][0][0].handles[-1]
        return original_body(owner, writing=writing)

    def fail_first_native(handle):
        nonlocal failed
        result = original_native(handle)
        if closing():
            native_seen.append(handle)
            if handle == target_handle and not failed:
                failed = True
                raise sentinel
        return result

    def observe_identity(directory):
        result = original_identity(directory)
        if closing():
            identities.append(id(directory))
        return result

    def observe_stack(stack, **kwargs):
        if closing():
            stack_attempts.append(kwargs.get("_closing_observation"))
        return original_stack(stack, **kwargs)

    with pytest.raises(RuntimeError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", observe_body)
                    patch.setattr(namespace, "_closed_directory_native", fail_first_native)
                    # This transparent PF observer intentionally selects the
                    # original fallback, and does not claim fast-path coverage.
                    patch.setattr(namespace.pf, "directory_identity", observe_identity)
                    patch.setattr(namespace, "_closed_stack_check_original", observe_stack)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    assert caught.value is sentinel and failed and delivered == []
    assert set(native_seen) == expected_handles and native_seen.count(target_handle) >= 2
    assert set(identities) == expected_directories
    assert stack_attempts and all(token is None for token in stack_attempts)
    _assert_retired(namespace, scopes)


@pytest.mark.parametrize("boundary", ["success", "cancel", "copied_context"])
def test_registered_closing_observation_keeps_scope_and_cancellation_checks(namespace, initialized, monkeypatch, boundary):
    profile = initialized
    original_stack = namespace._closed_stack_check_original
    original_stat = os.stat
    scopes, tokens, delivered, copied_errors = [], [], [], []
    physical_guard_checks = []
    cancellation_error = None
    target = safe._native_path(profile.database.parent)

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            physical_guard_checks.append(path)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    def observe_stack(stack, **kwargs):
        nonlocal cancellation_error
        token = kwargs.get("_closing_observation")
        if token is None:
            return original_stack(stack, **kwargs)
        scope = namespace._RUNTIME_DIRECTORY_CURRENT.get()
        binding = namespace._runtime_directory_binding_original(scope)
        assert binding.phase == "closing" and binding.closing_observation is token
        assert binding.held.stack is stack and binding.closing_identities
        assert namespace._CLOSED_NAMESPACE_OWNERS[id(binding.held.original)] is binding.held
        # Closing remains unavailable to the ordinary ambient read path.
        assert namespace._runtime_directory_current_original() is None
        scopes.append(scope)
        tokens.append(token)
        with monkeypatch.context() as fault:
            fault.setattr(os, "stat", changed_stat)
            with pytest.raises(lifecycle.StorageLifecycleError) as guard_error:
                lifecycle.require_guard(profile)
            assert guard_error.value.code == "STORAGE_UNSAFE_PATH"
        if boundary == "cancel":
            cancellation_error = binding.cancellation._failure
            namespace._closed_io.request_cancellation(binding.cancellation)
        if boundary == "copied_context":
            try:
                return copy_context().run(original_stack, stack, **kwargs)
            except namespace.NamespaceError as failure:
                copied_errors.append(failure)
                raise
        return original_stack(stack, **kwargs)

    def run():
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    # PF, held, guard and initialization dispatch stay original;
                    # only the final consumer entry is transparently observed.
                    patch.setattr(namespace, "_closed_stack_check_original", observe_stack)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))

    if boundary == "success":
        run()
        assert len(delivered) == 1
    else:
        expected_error = (namespace._closed_io.SharedIoCancelledError
                          if boundary == "cancel" else namespace.NamespaceError)
        with pytest.raises(expected_error) as caught:
            run()
        assert delivered == []
        if boundary == "cancel":
            assert cancellation_error is not None and caught.value is cancellation_error
        else:
            assert len(copied_errors) == 1 and caught.value is copied_errors[0]
            assert caught.value.code == "NAMESPACE_CLOSING_OBSERVATION_REQUIRED"
    assert len(tokens) == len(scopes) == 1 and physical_guard_checks == [target]
    _assert_retired(namespace, scopes)
    for token in tokens:
        with pytest.raises(namespace.NamespaceError) as retired:
            namespace._runtime_closing_binding_original(token)
        assert retired.value.code == "NAMESPACE_CLOSING_OBSERVATION_REQUIRED"
    for scope in scopes:
        retained = namespace._RETAINED_RUNTIME_DIRECTORY_SCOPES.get(id(scope))
        if retained is not None:
            assert retained.closing_observation is None and retained.closing_identities == ()


@pytest.mark.parametrize("damage", ["path", "marker"])
def test_ordinary_stack_check_inside_reading_scope_keeps_full_physical_checks(namespace, initialized, monkeypatch, damage):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_stat = os.stat
    target = safe._native_path(profile.database.parent)
    scopes, checks_returned, delivered, path_observations = [], [], [], []
    marker_changed = False

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            path_observations.append(path)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    with lifecycle.shared(profile) as guard:
        marker = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
        with pytest.raises(namespace.NamespaceError) as caught:
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with marker.open("r+b", buffering=0) as writer:
                    def ordinary_check_body(actual_owner, *, writing=False):
                        nonlocal marker_changed
                        scope, binding = _current_binding(namespace, actual_owner)
                        scopes.append(scope)
                        result = original_body(actual_owner, writing=writing)
                        assert namespace._runtime_directory_binding_original(scope).phase == "reading"
                        if damage == "path":
                            with monkeypatch.context() as fault:
                                fault.setattr(os, "stat", changed_stat)
                                binding.held.stack.check()
                        else:
                            writer.seek(1)
                            assert writer.write(b"!") == 1
                            marker_changed = True
                            binding.held.stack.check()
                        checks_returned.append(True)
                        return result

                    try:
                        with monkeypatch.context() as patch:
                            patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", ordinary_check_body)
                            delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
                    finally:
                        if marker_changed:
                            writer.seek(1)
                            assert writer.write(lifecycle._MARKER[1:2]) == 1
        assert guard.verify() is guard
    expected = "STORAGE_UNSAFE_PATH" if damage == "path" else "STORAGE_LOCK_INVALID"
    assert caught.value.code == expected and checks_returned == [] and delivered == []
    assert path_observations if damage == "path" else marker_changed
    _assert_retired(namespace, scopes)


@pytest.mark.parametrize("misuse", ["other_owner", "retired"])
def test_explicit_reading_scope_rejects_another_real_owner_or_retirement(namespace, initialized, monkeypatch, misuse):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    scopes, rejected_results, completed_verifiers, attempts = [], [], [], []

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as first_owner:
                first = namespace._CLOSED_NAMESPACE_OWNERS[id(first_owner)]
                if misuse == "other_owner":
                    # Both real shared owners are acquired before any runtime
                    # read scope starts; no registry or token is fabricated.
                    with namespace._closed_namespace_owned_locked(profile) as second_owner:
                        second = namespace._CLOSED_NAMESPACE_OWNERS[id(second_owner)]
                        assert first is not second and first.stack is not second.stack

                        def wrong_owner_body(actual_owner, *, writing=False):
                            scope, binding = _current_binding(namespace, actual_owner)
                            assert binding.held is first
                            assert namespace._CLOSED_NAMESPACE_OWNERS[id(second_owner)] is second
                            scopes.append(scope)
                            attempts.append("other_owner")
                            rejected_results.append(namespace._runtime_body_binding_original(scope, second.stack))
                            return original_body(actual_owner, writing=writing)

                        with monkeypatch.context() as patch:
                            patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", wrong_owner_body)
                            rejected_results.append(namespace._verify_closed_namespace_owner_runtime_native(first_owner))
                else:
                    def observe_body(actual_owner, *, writing=False):
                        scope, binding = _current_binding(namespace, actual_owner)
                        assert binding.held is first
                        scopes.append(scope)
                        return original_body(actual_owner, writing=writing)

                    with monkeypatch.context() as patch:
                        patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", observe_body)
                        completed_verifiers.append(namespace._verify_closed_namespace_owner_runtime_native(first_owner))
                    _assert_retired(namespace, scopes)
                    attempts.append("retired")
                    rejected_results.append(namespace._runtime_body_binding_original(scopes[0], first.stack))
    assert caught.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
    assert attempts == [misuse] and rejected_results == [] and len(scopes) == 1
    assert len(completed_verifiers) == (1 if misuse == "retired" else 0)
    _assert_retired(namespace, scopes)


def test_custom_stack_check_receives_no_private_kwargs_and_preserves_error(namespace, initialized, monkeypatch):
    profile = initialized
    original_check = namespace._ClosedNamespaceStack.check
    sentinel = RuntimeError("synthetic original custom stack callback failure")
    called, delivered = [], []
    target = None

    def custom_check(stack):
        if stack is target:
            called.append(stack)
            raise sentinel
        return original_check(stack)

    with pytest.raises(RuntimeError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                target = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)].stack
                with monkeypatch.context() as patch:
                    patch.setattr(namespace._ClosedNamespaceStack, "check", custom_check)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    assert caught.value is sentinel and called == [target] and delivered == []
    _assert_retired(namespace, ())


def test_existing_custody_failure_precedes_custom_stack_dispatch(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    scopes, original_errors, descriptor_calls, delivered = [], [], [], []
    second_error = RuntimeError("synthetic custom descriptor must remain untouched")

    def forbidden_descriptor(stack):
        descriptor_calls.append(stack)
        raise second_error

    def first_error_body(owner, *, writing=False):
        scope, binding = _current_binding(namespace, owner)
        scopes.append(scope)
        original_body(owner, writing=writing)
        try:
            copy_context().run(namespace._runtime_directory_check_original, scope)
        except namespace.NamespaceError as failure:
            original_errors.append(failure)
        assert len(original_errors) == 1
        assert namespace._closed_stack_custody_original(binding.held.stack).first is original_errors[0]
        with monkeypatch.context() as fault:
            fault.setattr(namespace._ClosedNamespaceStack, "check", property(forbidden_descriptor))
            return namespace._runtime_body_stack_check_original(binding.held.stack, scope)

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", first_error_body)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner))
    assert len(original_errors) == 1 and caught.value is original_errors[0]
    assert caught.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
    assert descriptor_calls == [] and delivered == []
    _assert_retired(namespace, scopes)


def test_runtime_writing_uses_namespace_ex_with_profile_shared_and_verifies_real_lock(namespace, initialized, monkeypatch):
    profile = initialized
    original_dispatch = namespace._runtime_body_write_check_original
    original_helper = namespace._require_runtime_namespace_write_original
    original_lock_verify = namespace._held_io._verify_authority_lock_original
    assert namespace._RUNTIME_BODY_REQUIRE_WRITE is original_helper
    scopes, held_values, lock_checks, delivered = [], [], [], []
    active_write = None

    def observe_write(held, scope):
        nonlocal active_write
        actual_scope, binding = _current_binding(namespace, held.original)
        assert scope is actual_scope and binding.held is held
        assert held.exclusive and held.lock.exclusive and not held.storage_guard.exclusive
        assert namespace._require_runtime_namespace_write_original is original_helper
        assert namespace._RUNTIME_BODY_REQUIRE_WRITE is original_helper
        scopes.append(scope)
        held_values.append(held)
        active_write = held
        try:
            return original_dispatch(held, scope)
        finally:
            active_write = None

    def observe_lock(scope, lease):
        result = original_lock_verify(scope, lease)
        if active_write is not None:
            held = active_write
            assert scope is held.lock.scope and lease is held.lock.lease
            record = namespace._held_io._resource_original(scope, lease, namespace._held_io._LockLease)
            assert record.locked and record.exclusive
            lock_checks.append((scope, lease))
        return result

    with lifecycle.shared(profile) as guard:
        # This actual runtime factory permits Namespace EX under profile SH;
        # the older ordinary factory has a different write-entry contract.
        with namespace._closed_namespace_runtime_owned_locked(profile, exclusive=True) as owner:
            held = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert held.storage_guard is guard and not guard.exclusive
            with monkeypatch.context() as patch:
                # The write helper and the actual lock.verify remain original.
                # These outer/inner observers do not select custom fallback.
                patch.setattr(namespace, "_runtime_body_write_check_original", observe_write)
                patch.setattr(namespace._held_io, "_verify_authority_lock_original", observe_lock)
                delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True))
            assert delivered == [held] and delivered[0] is held
            assert held_values == [held] and len(scopes) == 1
            assert lock_checks == [(held.lock.scope, held.lock.lease)]
    _assert_retired(namespace, scopes)


def test_runtime_writing_rejects_a_real_namespace_shared_owner(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    original_dispatch = namespace._runtime_body_write_check_original
    scopes, write_calls, delivered = [], [], []

    def observe_body(owner, *, writing=False):
        scope, binding = _current_binding(namespace, owner)
        assert writing and not binding.held.exclusive and not binding.held.lock.exclusive
        scopes.append(scope)
        return original_body(owner, writing=writing)

    def observe_write(held, scope):
        write_calls.append((held, scope))
        return original_dispatch(held, scope)

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_runtime_owned_locked(profile) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", observe_body)
                    patch.setattr(namespace, "_runtime_body_write_check_original", observe_write)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True))
    assert caught.value.code == "NAMESPACE_STORE_OWNER_REQUIRED"
    assert delivered == [] and write_calls == [] and len(scopes) == 1
    _assert_retired(namespace, scopes)


def test_ordinary_write_helper_inside_reading_scope_rejects_real_bad_marker(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    scopes, rejected, restored, ordinary_returns, delivered = [], [], [], [], []

    with lifecycle.shared(profile) as guard:
        marker = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
        with pytest.raises(namespace.NamespaceError) as caught:
            with namespace._closed_namespace_runtime_owned_locked(profile, exclusive=True) as owner:
                def ordinary_write_body(actual_owner, *, writing=False):
                    scope, binding = _current_binding(namespace, actual_owner)
                    assert writing and binding.held.exclusive and not guard.exclusive
                    scopes.append(scope)
                    result = original_body(actual_owner, writing=writing)
                    assert namespace._runtime_directory_binding_original(scope).phase == "reading"
                    with marker.open("r+b", buffering=0) as writer:
                        # Retain the real marker identity/length and byte-zero
                        # lock. The ordinary no-scope call must still read it.
                        writer.seek(1)
                        original_byte = writer.read(1)
                        assert original_byte == lifecycle._MARKER[1:2]
                        writer.seek(1)
                        assert writer.write(bytes([original_byte[0] ^ 1])) == 1
                        try:
                            ordinary_returns.append(namespace._require_runtime_namespace_write_original(binding.held))
                        except lifecycle.StorageLifecycleError as failure:
                            rejected.append(failure)
                            raise
                        finally:
                            # Restoration precedes every observation/owner exit,
                            # including when an unexpected test failure occurs.
                            writer.seek(1)
                            assert writer.write(original_byte) == 1
                            writer.seek(1)
                            assert writer.read(1) == original_byte
                            restored.append(True)
                    return result

                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", ordinary_write_body)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True))
        assert caught.value.code == "STORAGE_LOCK_INVALID"
        assert guard.verify() is guard
    assert len(rejected) == 1 and rejected[0].code == "STORAGE_LOCK_INVALID"
    assert restored == [True] and ordinary_returns == [] and delivered == [] and len(scopes) == 1
    _assert_retired(namespace, scopes)


def test_custom_write_helper_keeps_original_arguments_and_recorded_first_error(namespace, initialized, monkeypatch):
    profile = initialized
    original_body = namespace._verify_closed_namespace_owner_runtime_body_original
    custom_failure = RuntimeError("synthetic original custom write helper failure")
    scopes, custom_calls, custom_errors, first_errors, delivered = [], [], [], [], []

    def custom_write(held):
        # A private scope keyword must never be added to custom dispatch.
        custom_calls.append(held)
        raise custom_failure

    def first_error_body(owner, *, writing=False):
        scope, binding = _current_binding(namespace, owner)
        assert writing and binding.held.exclusive
        scopes.append(scope)
        original_body(owner, writing=writing)
        custody = namespace._closed_stack_custody_original(binding.held.stack)
        assert custody.first is None
        with monkeypatch.context() as fault:
            fault.setattr(namespace, "_require_runtime_namespace_write_original", custom_write)
            with pytest.raises(RuntimeError) as caught_custom:
                namespace._runtime_body_write_check_original(binding.held, scope)
            custom_errors.append(caught_custom.value)
            assert caught_custom.value is custom_failure and custom_calls == [binding.held]
            assert custody.first is None
            try:
                copy_context().run(namespace._runtime_directory_check_original, scope)
            except namespace.NamespaceError as failure:
                first_errors.append(failure)
            assert len(first_errors) == 1 and custody.first is first_errors[0]
            assert first_errors[0].code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
            # The real checker recorded this first error; the test never writes
            # registry/custody state. Custom dispatch must not be called again.
            return namespace._runtime_body_write_check_original(binding.held, scope)

    with pytest.raises(namespace.NamespaceError) as caught:
        with lifecycle.shared(profile):
            with namespace._closed_namespace_runtime_owned_locked(profile, exclusive=True) as owner:
                with monkeypatch.context() as patch:
                    patch.setattr(namespace, "_verify_closed_namespace_owner_runtime_body_original", first_error_body)
                    delivered.append(namespace._verify_closed_namespace_owner_runtime_native(owner, writing=True))
    assert len(custom_calls) == len(custom_errors) == len(first_errors) == len(scopes) == 1
    assert custom_errors[0] is custom_failure and caught.value is first_errors[0]
    assert delivered == []
    _assert_retired(namespace, scopes)
