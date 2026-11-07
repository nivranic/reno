"""Real Store budgets consume fresh, fully closed Namespace observations.

Only synthetic profiles and original Namespace/Store factories are used. Hooks
observe real effects or inject stated faults; no owner or registry is supplied.
"""
from contextlib import contextmanager
from contextvars import copy_context
import hashlib
import importlib
from pathlib import Path
import time

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401


_NAMESPACE_SHA256 = "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
_STORE_SHA256 = "3cb7989b7ae73aeb73d3e589f30dcbcfe12fed2fa81a9b2bbf986b1801ccf970"


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r668"
    monkeypatch.syspath_prepend(str(graph))
    namespace = importlib.import_module("authority_runtime_origin_r668.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r668/personal_authority_namespace.py"
    assert Path(namespace.__file__).resolve() == path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _NAMESPACE_SHA256
    assert namespace.lifecycle is lifecycle and namespace.DEADLINE_SECONDS == 30.0
    return namespace


@pytest.fixture
def store(namespace):
    store = importlib.import_module("authority_runtime_origin_r668.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(store.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _STORE_SHA256
    assert store.ns is namespace and store.v2.DEFAULT_LIMITS.max_seconds == 30
    return store


@contextmanager
def _actual_budget(namespace, store, profile):
    with lifecycle.shared(profile) as guard:
        with namespace._closed_namespace_runtime_owned_locked(profile, exclusive=True) as owner:
            actual = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert actual.storage_guard is guard and not guard.exclusive and actual.exclusive
            budget = store._Budget(owner)
            record = store._budget_record_original(budget)
            assert record.original is budget and record.acquisition is actual
            assert budget.original_deadline is actual.budget and record.first is None
            yield owner, actual, budget, record


def _assert_retired(namespace, scopes):
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
    assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
    assert scopes and all(id(scope) not in namespace._RUNTIME_DIRECTORY_SCOPES for scope in scopes)


class _EffectObserver:
    """Transparent hooks outside the canonical dispatch identities."""

    def __init__(self, namespace, store, patch):
        self.ns, self.store = namespace, store
        self.active = False
        self.budget = None
        self.scopes, self.bindings, self.body_entries = [], [], []
        self.inside_entries, self.after_entries, self.closing_leaves = [], [], []
        self.body_after = self.inside_call = self.inside_after = self.after_before = None
        self.files_failed = None
        self._install(patch)

    def capture(self, owner):
        scope = self.ns._RUNTIME_DIRECTORY_CURRENT.get()
        assert scope is not None
        binding = self.ns._runtime_directory_binding_original(scope)
        assert binding.held.original is owner and binding.phase == "reading"
        assert self.ns._CLOSED_NAMESPACE_OWNERS[id(owner)] is binding.held
        assert binding.held.storage_guard is lifecycle._CURRENT.get()
        assert self.budget.namespace_owner is owner
        if not any(previous is scope for previous in self.scopes):
            self.scopes.append(scope)
            self.bindings.append(binding)
        return scope, binding

    def run(self, budget):
        assert not self.active
        self.budget, self.active = budget, True
        try:
            return self.store._native_namespace_verify(budget)
        finally:
            self.active = False

    def _closing(self, plan):
        if not self.active:
            return False
        scope = self.ns._RUNTIME_DIRECTORY_CURRENT.get()
        if scope is None or not any(previous is scope for previous in self.scopes):
            return False
        binding = self.ns._runtime_directory_binding_original(scope)
        return binding.phase == "closing" and binding.guard_plan is plan

    def _install(self, patch):
        namespace, store = self.ns, self.store
        original_body_leaf = namespace._closed_effect_lock_observation_original
        original_inside = store._native_effect_budget_inside_original
        original_after = store._native_effect_budget_after_original
        original_directories = lifecycle._verify_guard_plan_directories_original
        original_files = lifecycle._verify_guard_plan_files_original

        def body_leaf(held):
            selected = self.active and held.original is self.budget.namespace_owner
            if selected:
                scope, binding = self.capture(held.original)
                self.body_entries.append((scope, held))
            result = original_body_leaf(held)
            if selected and self.body_after is not None:
                self.body_after(scope, binding)
            return result

        def inside(budget, record, actual, scope):
            selected = self.active and budget is self.budget
            if selected:
                actual_scope, binding = self.capture(actual.original)
                assert actual_scope is scope and binding.held is actual
                assert store._BUDGET_RECORDS[id(budget)] is record
                self.inside_entries.append((scope, record, actual))
            if selected and self.inside_call is not None:
                result = self.inside_call(original_inside, budget, record, actual, scope)
            else:
                result = original_inside(budget, record, actual, scope)
            if selected and self.inside_after is not None:
                self.inside_after(budget, record, actual, scope)
            return result

        def after(budget, record, actual):
            selected = self.active and budget is self.budget
            if selected:
                _assert_retired(namespace, self.scopes)
                assert store._BUDGET_RECORDS[id(budget)] is record
                assert namespace._CLOSED_NAMESPACE_OWNERS[id(actual.original)] is actual
                self.after_entries.append((record, actual))
                if self.after_before is not None:
                    self.after_before(budget, record, actual)
            return original_after(budget, record, actual)

        def directories(plan):
            if self._closing(plan):
                self.closing_leaves.append("directories")
            return original_directories(plan)

        def files(plan, profile, exclusive=False):
            selected = self._closing(plan)
            if selected:
                self.closing_leaves.append("files")
            try:
                return original_files(plan, profile, exclusive=exclusive)
            except lifecycle.StorageLifecycleError as failure:
                if selected and self.files_failed is not None:
                    self.files_failed(failure)
                raise

        patch.setattr(namespace, "_closed_effect_lock_observation_original", body_leaf)
        patch.setattr(store, "_native_effect_budget_inside_original", inside)
        patch.setattr(store, "_native_effect_budget_after_original", after)
        patch.setattr(lifecycle, "_verify_guard_plan_directories_original", directories)
        patch.setattr(lifecycle, "_verify_guard_plan_files_original", files)


@contextmanager
def _changed_marker(guard):
    marker = guard._locks[-1].directory.path / lifecycle.LOCK_NAME
    with marker.open("r+b", buffering=0) as writer:
        writer.seek(1)
        original = writer.read(1)
        assert original == lifecycle._MARKER[1:2]
        writer.seek(1)
        assert writer.write(bytes([original[0] ^ 1])) == 1
        try:
            yield
        finally:
            writer.seek(1)
            assert writer.write(original) == 1
            writer.seek(1)
            assert writer.read(1) == original


def test_each_store_effect_gets_a_distinct_real_scope_and_finishes_before_return(namespace, store, initialized, monkeypatch):
    with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
        with monkeypatch.context() as patch:
            observer = _EffectObserver(namespace, store, patch)
            first = observer.run(budget)
            _assert_retired(namespace, observer.scopes)
            second = observer.run(budget)
        assert first is second is actual
        assert len(observer.scopes) == len(observer.inside_entries) == len(observer.after_entries) == 2
        assert observer.scopes[0] is not observer.scopes[1]
        plans = [binding.guard_plan for binding in observer.bindings]
        assert plans[0] is not None and plans[1] is not None and plans[0] is not plans[1]
        assert observer.closing_leaves == ["directories", "files", "directories"] * 2
        assert record.first is None and id(budget) not in store._RETAINED_BUDGETS
        _assert_retired(namespace, observer.scopes)
    assert id(budget) not in store._BUDGET_RECORDS


def test_ordinary_store_budget_inside_effect_still_checks_real_marker(namespace, store, initialized, monkeypatch):
    original_errors, returned = [], []
    with pytest.raises(namespace.NamespaceError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def ordinary_budget(budget, current, held, scope):
                    assert namespace._runtime_directory_binding_original(scope).guard_plan is not None
                    with _changed_marker(held.storage_guard):
                        budget.check()

                observer.inside_after = ordinary_budget
                with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                    returned.append(observer.run(budget))
                original_errors.append(caught.value)
                assert caught.value.code == "STORAGE_LOCK_INVALID"
                assert record.first is caught.value
                assert store._RETAINED_BUDGETS[id(budget)] is record
                assert observer.after_entries == []
                _assert_retired(namespace, observer.scopes)
                raise caught.value
    assert outer.value.code == "STORAGE_LOCK_INVALID" and returned == []
    assert len(original_errors) == 1


def test_store_deadline_rechecks_after_namespace_scope_has_retired(namespace, store, initialized, monkeypatch):
    returned, clock_samples, failures = [], [], []
    with pytest.raises(namespace.NamespaceError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            original_deadlines = (budget.deadline, budget.namespace_deadline, budget.original_started)
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def expire_after_finish(budget, current, held):
                    _assert_retired(namespace, observer.scopes)
                    before = time.monotonic()
                    assert before < budget.namespace_deadline
                    target = budget.namespace_deadline + 0.02
                    remaining = target - before
                    assert 0 < remaining <= 30
                    # Wait on the actual original clock; never extend, replace,
                    # backdate or otherwise mutate either recorded deadline.
                    time.sleep(remaining)
                    after = time.monotonic()
                    assert after > budget.namespace_deadline
                    clock_samples.append((before, after))

                observer.after_before = expire_after_finish
                with pytest.raises(namespace.NamespaceError) as caught:
                    returned.append(observer.run(budget))
                failures.append(caught.value)
                assert caught.value.code == "NAMESPACE_DEADLINE"
                assert record.first is caught.value
                assert record.namespace_stack.first is caught.value
                assert record.namespace_stack.first_tb is not None
                assert store._RETAINED_BUDGETS[id(budget)] is record
                assert (budget.deadline, budget.namespace_deadline, budget.original_started) == original_deadlines
                _assert_retired(namespace, observer.scopes)
                raise caught.value
    assert len(clock_samples) == len(failures) == 1 and outer.value is failures[0]
    assert returned == []


def test_cancellation_after_namespace_finish_keeps_original_wrapper_and_budget_first(namespace, store, initialized, monkeypatch):
    returned, requested, wrapped = [], [], []
    with pytest.raises(namespace._closed_io.SharedIoCancelledError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def cancel_after_finish(budget, current, held):
                    _assert_retired(namespace, observer.scopes)
                    cancellation = observer.bindings[-1].cancellation
                    assert cancellation is record.namespace_stack.cancellation
                    assert cancellation is actual.budget.callback.data
                    requested.append(cancellation._failure)
                    namespace._closed_io.request_cancellation(cancellation)

                observer.after_before = cancel_after_finish
                with pytest.raises(namespace._ProducerFailure) as caught:
                    returned.append(observer.run(budget))
                wrapped.append(caught.value)
                assert len(requested) == 1 and caught.value.original is requested[0]
                assert record.first is caught.value
                assert record.namespace_stack.first is caught.value
                assert record.namespace_stack.first_tb is not None
                assert store._RETAINED_BUDGETS[id(budget)] is record
                assert len(observer.inside_entries) == len(observer.after_entries) == 1
                _assert_retired(namespace, observer.scopes)
                # The unchanged outer Namespace error adapter unwraps this same
                # original producer failure only after the real owner closes.
                raise caught.value
    assert len(wrapped) == 1 and outer.value is requested[0]
    assert wrapped[0].original is outer.value and returned == []


def test_missing_current_guard_after_finish_rejects_and_preserves_both_first_errors(namespace, store, initialized, monkeypatch):
    returned, failures, restored = [], [], []
    guard_token = None
    with pytest.raises(namespace.NamespaceError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def remove_current_guard_after_finish(budget, current, held):
                    nonlocal guard_token
                    _assert_retired(namespace, observer.scopes)
                    assert lifecycle._CURRENT.get() is held.storage_guard
                    # A real Context token temporarily removes the live guard;
                    # no replacement guard, owner or registry is manufactured.
                    guard_token = lifecycle._CURRENT.set(None)

                observer.after_before = remove_current_guard_after_finish
                with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                    try:
                        returned.append(observer.run(budget))
                    finally:
                        if guard_token is not None:
                            lifecycle._CURRENT.reset(guard_token)
                            guard_token = None
                            restored.append(True)
                failures.append(caught.value)
                assert caught.value.code == "STORAGE_GUARD_REQUIRED"
                assert record.first is record.namespace_stack.first is caught.value
                assert record.namespace_stack.first_tb is not None
                assert store._RETAINED_BUDGETS[id(budget)] is record
                assert restored == [True] and lifecycle._CURRENT.get() is actual.storage_guard
                assert len(observer.inside_entries) == len(observer.after_entries) == 1
                _assert_retired(namespace, observer.scopes)
                raise caught.value
    assert outer.value.code == "STORAGE_GUARD_REQUIRED"
    assert len(failures) == 1 and returned == [] and guard_token is None


def test_namespace_body_failure_does_not_preemptively_set_store_budget_first(namespace, store, initialized, monkeypatch):
    sentinel = RuntimeError("synthetic original Namespace body leaf failure")
    returned = []
    with pytest.raises(RuntimeError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def fail_body(scope, binding):
                    assert binding.guard_plan is not None
                    raise sentinel

                observer.body_after = fail_body
                with pytest.raises(RuntimeError) as caught:
                    returned.append(observer.run(budget))
                assert caught.value is sentinel
                # This point is before the real outer owner exit, whose
                # lifetime closer may subsequently retain its original error.
                assert record.first is None and id(budget) not in store._RETAINED_BUDGETS
                assert observer.inside_entries == observer.after_entries == []
                assert observer.closing_leaves == ["directories", "files", "directories"]
                _assert_retired(namespace, observer.scopes)
                raise caught.value
    assert outer.value is sentinel and returned == []


def test_namespace_finish_failure_does_not_preemptively_set_store_budget_first(namespace, store, initialized, monkeypatch):
    returned, file_errors, restored = [], [], []
    mutation = None
    with pytest.raises(namespace.NamespaceError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            try:
                with monkeypatch.context() as patch:
                    observer = _EffectObserver(namespace, store, patch)

                    def change_before_finish(budget, current, held, scope):
                        nonlocal mutation
                        assert namespace._runtime_directory_binding_original(scope).guard_plan is not None
                        mutation = _changed_marker(held.storage_guard)
                        mutation.__enter__()

                    def restore_at_files_failure(failure):
                        nonlocal mutation
                        file_errors.append(failure)
                        assert mutation is not None
                        mutation.__exit__(None, None, None)
                        mutation = None
                        restored.append(True)
                        assert failure.code == "STORAGE_LOCK_INVALID"

                    observer.inside_after = change_before_finish
                    observer.files_failed = restore_at_files_failure
                    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                        returned.append(observer.run(budget))
                    assert len(file_errors) == 1 and file_errors[0] is caught.value
                    assert restored == [True] and mutation is None
                    assert record.first is None and id(budget) not in store._RETAINED_BUDGETS
                    assert len(observer.inside_entries) == 1 and observer.after_entries == []
                    assert observer.closing_leaves == ["directories", "files", "directories"]
                    _assert_retired(namespace, observer.scopes)
                    raise caught.value
            finally:
                if mutation is not None:
                    mutation.__exit__(None, None, None)
    assert outer.value.code == "STORAGE_LOCK_INVALID" and returned == []


def test_inside_adapter_records_real_copied_context_failure_as_budget_first(namespace, store, initialized, monkeypatch):
    returned, failures = [], []
    with pytest.raises(namespace.NamespaceError) as outer:
        with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
            with monkeypatch.context() as patch:
                observer = _EffectObserver(namespace, store, patch)

                def copied_inside(original, budget, current, held, scope):
                    return copy_context().run(original, budget, current, held, scope)

                observer.inside_call = copied_inside
                with pytest.raises(namespace.NamespaceError) as caught:
                    returned.append(observer.run(budget))
                failures.append(caught.value)
                assert caught.value.code == "NAMESPACE_DIRECTORY_OBSERVATION_REQUIRED"
                assert record.first is caught.value
                assert store._RETAINED_BUDGETS[id(budget)] is record
                assert observer.after_entries == []
                _assert_retired(namespace, observer.scopes)
                raise caught.value
    assert len(failures) == 1 and outer.value is failures[0] and returned == []


def test_entry_custom_dispatch_keeps_original_three_step_sequence(namespace, store, initialized, monkeypatch):
    original_record = store._budget_record_original
    original_facade = store._namespace_verification
    original_check = store._Budget.check
    events = []
    inside_check = False
    with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
        with monkeypatch.context() as patch:
            observer = _EffectObserver(namespace, store, patch)

            def observe_record(value):
                if observer.active and value is budget and not inside_check:
                    events.append("record")
                return original_record(value)

            def custom_facade(value, *, writing=False):
                if observer.active:
                    assert value is owner and writing is True
                    events.append("namespace")
                return original_facade(value, writing=writing)

            def custom_check(value):
                nonlocal inside_check
                assert value is budget
                events.append("budget")
                inside_check = True
                try:
                    return original_check(value)
                finally:
                    inside_check = False

            patch.setattr(store, "_budget_record_original", observe_record)
            patch.setattr(store, "_namespace_verification", custom_facade)
            patch.setattr(store._Budget, "check", custom_check)
            result = observer.run(budget)
        assert result is actual and events == ["record", "namespace", "budget"]
        assert observer.inside_entries == observer.after_entries == []
        assert len(observer.body_entries) == 1 and record.first is None
        _assert_retired(namespace, observer.scopes)


def test_budget_dispatch_changed_in_body_runs_once_after_finish_without_replaying_body(namespace, store, initialized, monkeypatch):
    original_check = store._Budget.check
    checks = []
    with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
        with monkeypatch.context() as patch:
            observer = _EffectObserver(namespace, store, patch)

            def custom_check(value):
                assert value is budget
                _assert_retired(namespace, observer.scopes)
                checks.append(value)
                return original_check(value)

            def change_dispatch(scope, binding):
                assert binding.guard_plan is not None
                patch.setattr(store._Budget, "check", custom_check)

            observer.body_after = change_dispatch
            result = observer.run(budget)
        assert result is actual and checks == [budget]
        assert len(observer.body_entries) == 1 and len(observer.scopes) == 1
        assert observer.inside_entries == observer.after_entries == []
        assert record.first is None
        _assert_retired(namespace, observer.scopes)


def test_real_custom_guard_without_plan_uses_original_budget_after_finish(namespace, store, initialized, monkeypatch):
    original_guard = lifecycle.StorageGuard.verify
    original_v2_check = store.v2._Budget.check
    checks = []
    with _actual_budget(namespace, store, initialized) as (owner, actual, budget, record):
        with monkeypatch.context() as patch:
            observer = _EffectObserver(namespace, store, patch)

            def custom_guard(guard):
                return original_guard(guard)

            def observe_v2_check(value):
                if observer.active and value is budget:
                    _assert_retired(namespace, observer.scopes)
                    checks.append(value)
                return original_v2_check(value)

            # The real original admission detects custom verify dispatch and
            # naturally yields no plan; no plan or registry value is injected.
            patch.setattr(lifecycle.StorageGuard, "verify", custom_guard)
            patch.setattr(store.v2._Budget, "check", observe_v2_check)
            result = observer.run(budget)
        assert result is actual and checks == [budget]
        assert len(observer.scopes) == 1 and observer.bindings[0].guard_plan is None
        assert observer.inside_entries == observer.after_entries == []
        assert record.first is None
        _assert_retired(namespace, observer.scopes)
