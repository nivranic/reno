"""Real initialization permits exercise the bounded guard observation lifetime.

Every observer delegates to its original verifier. Explicit test faults affect
only fresh synthetic fixtures; no owner, permit, registry or token is supplied.
"""
from contextvars import copy_context
import hashlib
import importlib
import os
from pathlib import Path
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import profile  # noqa: F401


_NAMESPACE_SHA256 = "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r666"
    monkeypatch.syspath_prepend(str(graph))
    namespace = importlib.import_module("authority_runtime_origin_r666.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r666/personal_authority_namespace.py"
    assert Path(namespace.__file__).resolve() == path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _NAMESPACE_SHA256
    assert namespace.lifecycle is lifecycle and namespace.DEADLINE_SECONDS == 30.0
    return namespace


@pytest.fixture(autouse=True)
def original_database_is_preserved(profile):
    database = Path(safe._native_path(profile.database))
    before = database.read_bytes()
    yield
    assert database.read_bytes() == before


class _ChangedStat:
    def __init__(self, original, **changes):
        self._original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _planned_binding(namespace, budget):
    scope = namespace._init_directory_context._CURRENT.get()
    if scope is None:
        return None
    binding = namespace._initialization_directory_binding_original(scope)
    if binding.guard_plan is None:
        return None
    state = namespace._initialization_state(budget, required=True)
    permit = state.permit
    marker = namespace._NATIVE_VERIFICATION.get()
    assert namespace._INITIALIZATION_DIRECTORY_SCOPES[id(scope)] is binding
    assert binding.original is scope and binding.phase == "reading"
    assert binding.budget is budget and binding.state is state
    assert type(permit) is namespace._InitializationPermit
    assert namespace._LIVE_INITIALIZATION_BUDGETS[id(budget)] is state
    assert permit.budget is budget and permit.execution_owner == namespace._owner()
    assert permit.guard is lifecycle._CURRENT.get() and permit.guard.exclusive
    assert binding.guard_plan.guard is permit.guard
    assert marker is not None and binding.marker is marker
    assert namespace._LIVE_NATIVE_VERIFICATIONS[id(marker)] is marker
    assert marker[1] is budget and marker[2] == namespace._owner()
    assert binding.ledger.state is state and binding.ledger.permit is permit
    assert binding.ledger.custodies is binding.custodies
    assert binding.ledger.directories is binding.directories
    assert namespace._init_directory_context._CONTEXT.get() is scope
    assert budget.callback is None and budget.started == permit.started
    assert namespace._initialization_guard_dispatch_original(permit.guard)
    return scope, binding


def _initialize(namespace, profile):
    return namespace.initialize_explicit_locked(
        profile,
        init_operation_id=str(uuid.uuid4()),
        domain_id=str(uuid.uuid4()),
        facility_id=str(uuid.uuid4()),
    )


def _assert_retired(namespace, scopes):
    assert namespace._init_directory_context._CURRENT.get() is None
    assert namespace._init_directory_context._CONTEXT.get() is None
    assert all(id(scope) not in namespace._INITIALIZATION_DIRECTORY_SCOPES for scope in scopes)


class _TailObserver:
    """Observe one real domain's attempts without changing dispatch admission."""

    def __init__(self, namespace, monkeypatch):
        self.namespace = namespace
        self.scope = None
        self.binding = None
        self.events = []
        self.first_errors = []
        self.file_errors = []
        self.after_first_directory = None
        self.on_files_failure = None
        self._install(monkeypatch)

    def select(self, scope):
        assert self.scope is None
        self.scope = scope
        self.binding = self.namespace._initialization_directory_binding_original(scope)
        assert self.binding.guard_plan is not None and self.binding.files
        assert self.binding.directories

    def closing(self, plan=None):
        if self.scope is None:
            return False
        current = self.namespace._INITIALIZATION_DIRECTORY_SCOPES.get(id(self.scope))
        return (current is not None and current.phase == "closing"
                and (plan is None or current.guard_plan is plan))

    def _install(self, patch):
        namespace = self.namespace
        original_directories = lifecycle._verify_guard_plan_directories_original
        original_files = lifecycle._verify_guard_plan_files_original
        original_file_tail = namespace._initialization_file_tail_original
        original_file_full = namespace._initialization_file_full_original
        original_directory_match = namespace._store_directory_close_matches
        original_ledger = namespace._initialization_directory_ledger_original
        original_finish = namespace._initialization_directory_finish_original

        def directories(plan):
            selected = self.closing(plan)
            if selected:
                self.events.append(("guard_directories", plan))
            result = original_directories(plan)
            if (selected and self.after_first_directory is not None
                    and sum(kind == "guard_directories" for kind, _ in self.events) == 1):
                self.after_first_directory()
            return result

        def files(plan, profile, exclusive=False):
            selected = self.closing(plan)
            if selected:
                self.events.append(("guard_files", plan))
            try:
                return original_files(plan, profile, exclusive=exclusive)
            except lifecycle.StorageLifecycleError as failure:
                if selected:
                    self.file_errors.append(failure)
                    if self.on_files_failure is not None:
                        self.on_files_failure(failure)
                raise

        def file_tail(binding, failures):
            if self.closing() and binding.original is self.scope:
                self.events.append(("file_tail", binding))
            return original_file_tail(binding, failures)

        def file_full(binding, observation):
            if self.closing() and binding.original is self.scope:
                self.events.append(("file_full", observation))
            return original_file_full(binding, observation)

        def directory_match(record):
            if self.closing():
                self.events.append(("directory_match", record))
            return original_directory_match(record)

        def ledger(budget, closing_stack):
            if self.closing() and budget is self.binding.budget:
                self.events.append(("ledger", budget))
            return original_ledger(budget, closing_stack)

        def finish(scope, failures):
            try:
                return original_finish(scope, failures)
            finally:
                if scope is self.scope:
                    self.first_errors.append(None if failures.first is None else failures.first[0])

        patch.setattr(lifecycle, "_verify_guard_plan_directories_original", directories)
        patch.setattr(lifecycle, "_verify_guard_plan_files_original", files)
        patch.setattr(namespace, "_initialization_file_tail_original", file_tail)
        patch.setattr(namespace, "_initialization_file_full_original", file_full)
        patch.setattr(namespace, "_store_directory_close_matches", directory_match)
        patch.setattr(namespace, "_initialization_directory_ledger_original", ledger)
        patch.setattr(namespace, "_initialization_directory_finish_original", finish)

    def assert_complete_attempts(self, first_error):
        assert self.scope is not None and self.binding is not None
        assert [kind for kind, _ in self.events[:4]] == [
            "guard_directories", "guard_files", "guard_directories", "file_tail"]
        assert sum(kind == "guard_directories" for kind, _ in self.events) == 2
        assert sum(kind == "guard_files" for kind, _ in self.events) == 1
        assert sum(kind == "file_tail" for kind, _ in self.events) == 1
        actual_files = [value for kind, value in self.events if kind == "file_full"]
        assert len(actual_files) == len(self.binding.files)
        assert all(actual is expected for actual, expected in
                   zip(actual_files, self.binding.files, strict=True))
        actual_directories = [value for kind, value in self.events if kind == "directory_match"]
        assert all(any(actual is expected for actual in actual_directories)
                   for expected in self.binding.directories)
        assert sum(kind == "ledger" for kind, _ in self.events) == 1
        assert self.first_errors == [first_error]
        assert self.first_errors[0] is first_error
        _assert_retired(self.namespace, [self.scope])
        retained = self.namespace._RETAINED_INITIALIZATION_DIRECTORY_SCOPES[id(self.scope)]
        assert retained.original is self.scope and retained.phase == "closing"


def test_real_initialization_plans_are_distinct_and_ordinary_checks_stay_full(namespace, profile, monkeypatch):
    original_body = namespace._initialization_verify_native_body_original
    original_state = lifecycle._verify_guard_plan_state_original
    original_stat = os.stat
    target = safe._native_path(profile.database.parent)
    scopes, plans, guards, state_checks, physical_checks, delivered = [], [], [], [], [], []
    fault_injected = False

    def changed_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            physical_checks.append(path)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    def observe_state(plan, observed_profile, exclusive=False):
        scope = namespace._init_directory_context._CURRENT.get()
        if scope is not None:
            binding = namespace._initialization_directory_binding_original(scope)
            if binding.phase == "reading" and binding.guard_plan is plan:
                state_checks.append((scope, plan))
        return original_state(plan, observed_profile, exclusive=exclusive)

    def observe_body(budget, *, closing_stack=None):
        nonlocal fault_injected
        selected = _planned_binding(namespace, budget)
        if selected is not None:
            scope, binding = selected
            scopes.append(scope)
            plans.append(binding.guard_plan)
            guards.append(binding.state.permit.guard)
        result = original_body(budget, closing_stack=closing_stack)
        if selected is not None and not fault_injected:
            fault_injected = True
            # Ordinary entry points still make physical checks inside a real
            # native observation. Restore the stat fault before normal finish.
            with monkeypatch.context() as fault:
                fault.setattr(os, "stat", changed_stat)
                with pytest.raises(lifecycle.StorageLifecycleError) as budget_error:
                    budget.check()
                assert budget_error.value.code == "STORAGE_UNSAFE_PATH"
                with pytest.raises(lifecycle.StorageLifecycleError) as guard_error:
                    lifecycle.require_guard(profile, exclusive=True)
                assert guard_error.value.code == "STORAGE_UNSAFE_PATH"
        return result

    with lifecycle.exclusive(profile) as guard:
        with monkeypatch.context() as patch:
            patch.setattr(namespace, "_initialization_verify_native_body_original", observe_body)
            patch.setattr(lifecycle, "_verify_guard_plan_state_original", observe_state)
            delivered.append(_initialize(namespace, profile))
        assert all(actual is guard for actual in guards)
    assert len(delivered) == 1 and delivered[0]["state"] == "namespace_ready"
    assert fault_injected and len(physical_checks) == 2
    assert len(plans) >= 2 and len({id(plan) for plan in plans}) == len(plans)
    assert len({id(scope) for scope in scopes}) == len(scopes)
    assert all(any(actual_scope is scope and actual_plan is plan
                   for actual_scope, actual_plan in state_checks)
               for scope, plan in zip(scopes, plans, strict=True))
    _assert_retired(namespace, scopes)


def test_marker_change_is_rejected_by_real_full_files_tail_and_restored_before_cleanup(namespace, profile, monkeypatch):
    original_body = namespace._initialization_verify_native_body_original
    delivered, rejected, restored = [], [], []
    mutation = {}

    def restore_marker():
        with open(mutation["path"], "r+b", buffering=0) as stream:
            stream.seek(1)
            assert stream.write(mutation["byte"]) == 1
            stream.flush()
            os.fsync(stream.fileno())
        mutation["dirty"] = False

    with lifecycle.exclusive(profile):
        try:
            with monkeypatch.context() as patch:
                observer = _TailObserver(namespace, patch)

                def observe_body(budget, *, closing_stack=None):
                    selected = _planned_binding(namespace, budget)
                    result = original_body(budget, closing_stack=closing_stack)
                    if observer.scope is None and selected is not None and selected[1].files:
                        observer.select(selected[0])
                    return result

                def corrupt_marker():
                    lock = observer.binding.guard_plan.locks[-1]
                    marker = safe._native_path(lock.directory.path / lifecycle.LOCK_NAME)
                    # Byte zero is lifetime-locked on Windows. Alter only byte
                    # one in place, retaining the genuine file and open handles.
                    with open(marker, "r+b", buffering=0) as stream:
                        stream.seek(1)
                        original_byte = stream.read(1)
                        assert original_byte == lifecycle._MARKER[1:2]
                        mutation.update(path=marker, byte=original_byte, dirty=True)
                        stream.seek(1)
                        assert stream.write(bytes([original_byte[0] ^ 1])) == 1
                        stream.flush()
                        os.fsync(stream.fileno())

                def restore_after_real_rejection(failure):
                    rejected.append(failure)
                    assert mutation["dirty"]
                    restore_marker()
                    restored.append(failure)
                    assert failure.code == "STORAGE_LOCK_INVALID"

                observer.after_first_directory = corrupt_marker
                observer.on_files_failure = restore_after_real_rejection
                patch.setattr(namespace, "_initialization_verify_native_body_original", observe_body)
                with pytest.raises(namespace.NamespaceError) as caught:
                    delivered.append(_initialize(namespace, profile))
            assert caught.value.code == "STORAGE_LOCK_INVALID"
            assert len(rejected) == 1 and restored == rejected
            assert rejected[0] is observer.file_errors[0] is restored[0]
            assert mutation["dirty"] is False and delivered == []
            observer.assert_complete_attempts(rejected[0])
        finally:
            # A fallback protects the outer real EX guard on a test failure;
            # only the explicit assertion above proves in-tail restoration.
            if mutation.get("dirty"):
                restore_marker()


def test_copied_context_rejects_real_initialization_and_attempts_all_tails(namespace, profile, monkeypatch):
    original_body = namespace._initialization_verify_native_body_original
    errors, delivered = [], []
    with lifecycle.exclusive(profile):
        with monkeypatch.context() as patch:
            observer = _TailObserver(namespace, patch)

            def copied_context_body(budget, *, closing_stack=None):
                selected = _planned_binding(namespace, budget)
                result = original_body(budget, closing_stack=closing_stack)
                if observer.scope is None and selected is not None and selected[1].files:
                    observer.select(selected[0])
                    try:
                        copy_context().run(namespace._initialization_directory_check_original, selected[0])
                    except namespace.NamespaceError as failure:
                        errors.append(failure)
                        raise
                return result

            patch.setattr(namespace, "_initialization_verify_native_body_original", copied_context_body)
            with pytest.raises(namespace.NamespaceError) as caught:
                delivered.append(_initialize(namespace, profile))
        assert len(errors) == 1 and caught.value is errors[0]
        assert caught.value.code == "NAMESPACE_INITIALIZATION_DIRECTORY_SCOPE_REQUIRED"
        assert delivered == []
        observer.assert_complete_attempts(errors[0])


def test_body_failure_precedes_directory_tail_failure_and_all_tails_are_attempted(namespace, profile, monkeypatch):
    original_body = namespace._initialization_verify_native_body_original
    sentinel = RuntimeError("synthetic original initialization body failure")
    tail_failure = OSError("synthetic initialization directory tail failure")
    delivered, injected_tail = [], []
    with lifecycle.exclusive(profile):
        with monkeypatch.context() as patch:
            observer = _TailObserver(namespace, patch)

            def failing_body(budget, *, closing_stack=None):
                selected = _planned_binding(namespace, budget)
                result = original_body(budget, closing_stack=closing_stack)
                if observer.scope is None and selected is not None and selected[1].files:
                    observer.select(selected[0])
                    raise sentinel
                return result

            def fail_first_completed_directory():
                injected_tail.append(tail_failure)
                raise tail_failure

            observer.after_first_directory = fail_first_completed_directory
            patch.setattr(namespace, "_initialization_verify_native_body_original", failing_body)
            with pytest.raises(RuntimeError) as caught:
                delivered.append(_initialize(namespace, profile))
        assert caught.value is sentinel and delivered == []
        assert injected_tail == [tail_failure]
        observer.assert_complete_attempts(sentinel)
