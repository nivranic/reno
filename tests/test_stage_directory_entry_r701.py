"""Actual Stage directory entry preserves full ownership gates and input order."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path
import time
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r701"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r701.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r701/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r701.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "92d19bf5b406b7a627c92649ea05b5bd7a6a5dcb1a1b7848f78f28c8cba59065"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = importlib.import_module("authority_runtime_origin_r701.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "17e1a4facef032d74dd66573a6c1d5c14104031e5a99f9c9b759f2b7720ade9a"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


@contextmanager
def live_stage(namespace, store, stage, profile):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                scope = store._stage_scope_original(stack)
                custody = store._STACK_RECORDS[id(stack)]
                assert stage._SCOPES[id(scope)] is scope and custody.stage is scope
                assert scope.permit is custody and scope.held_scope is custody.scope
                assert len(custody.held_original.resources) == 3
                assert all(row.resource_kind == "filehold" and not row.finished for row in custody.held_original.resources)
                yield scope, custody, custody.namespace.directory
            assert custody.closed and scope.closed and scope.first_failure is None
            assert id(stack) not in store._STACK_RECORDS and id(scope) not in stage._SCOPES
            assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


def test_directory_entry_checks_each_real_source_once_and_next_entry_is_fresh(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        original, seen = stage.held.original._identities, Counter()
        expected = Counter({id(row): 1 for row in custody.held_original.resources})

        def observe(row, **kwargs):
            result = original(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            return result

        native = namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            patch.setattr(stage.held.original, "_identities", observe)
            assert stage._directory_original(scope, directory) is native
            assert seen == expected
            assert stage._directory_original(scope, directory) is native
            assert seen == expected + expected
            created = stage._new_resource(scope, directory, "entry-probe.tmp", b"entry probe", "entry-probe.json",
                                          write=False, custody=True)
            assert seen == expected + expected + expected
        assert created.directory_record is native and created.fd is created.buffer is None
        assert stage._LEASES[id(created.lease)] is created and stage.lower._ACTIVE[id(created)] is created
        assert scope.resources == [created]
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert stage._finish_resource(created, None) is None
        assert not scope.resources and id(created) not in stage.lower._ACTIVE
        assert id(created.lease) not in stage._LEASES


@pytest.mark.parametrize("component", ["lookup", "store_stack", "namespace", "scope"])
def test_custom_dispatch_preserves_complete_remaining_lookup(namespace, store, stage, initialized, monkeypatch, component):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        module, name = {
            "lookup": (store, "_directory_record_original"),
            "store_stack": (store, "_stage_stack_original"),
            "namespace": (namespace, "_closed_directory_verify_original"),
            "scope": (stage, "_scope_live"),
        }[component]
        original, leaf = getattr(module, name), stage.held.original._identities
        calls, seen = [], Counter()

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            return result

        native = namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(stage.held.original, "_identities", observe)
            assert stage._directory_original(scope, directory) is native
        assert seen == Counter({id(row): 3 if component == "store_stack" else 2
                                for row in custody.held_original.resources})
        if component == "lookup":
            assert calls == [((scope.stack, directory), {})]
        elif component == "scope":
            assert calls == [((scope,), {})]
        elif component == "store_stack":
            assert calls == [((scope.stack,), {})] * 2
        else:
            assert calls[-1] == ((native,), {})
            assert calls[:-1] == [((custody.directories[0],), {})] * 2


@pytest.mark.parametrize("component", ["guard", "held_identity", "bound_budget"])
def test_custom_guard_or_nested_identity_retains_second_store_sweep(namespace, store, stage, initialized, monkeypatch, component):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        module, name = {"guard": (lifecycle.StorageGuard, "verify"),
                        "held_identity": (stage.held, "_verify_identity_record"),
                        "bound_budget": (store, "_bound_budget")}[component]
        original, leaf = getattr(module, name), stage.held.original._identities
        calls, seen = [], Counter()

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(stage.held.original, "_identities", observe)
            if component in {"held_identity", "bound_budget"}:
                assert stage._directory_store_binding_original(scope) is None
            assert stage._directory_original(scope, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
            if component == "bound_budget":
                candidate_calls, candidate_seen = calls.copy(), seen.copy()
                calls.clear()
                seen.clear()
                # Execute the original Stage-prefix + Store-lookup expression
                # on the same live resources, without changing any deadline.
                assert stage._scope_live(scope) is scope
                assert store._directory_record_original(scope.stack, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
                assert calls == candidate_calls and seen == candidate_seen
        expected = Counter({id(row): 2 for row in custody.held_original.resources})
        assert seen == expected
        if component == "guard":
            assert calls == [((custody.guard,), {})] * 3
        elif component == "held_identity":
            assert Counter(id(args[0]) for args, _ in calls) == expected
            assert all(len(args) == 1 and kwargs == {} for args, kwargs in calls)
        else:
            assert calls and all(args == (scope.budget, scope.budget.namespace_owner, scope.budget.original_deadline)
                                 and kwargs == {} for args, kwargs in calls)
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before


@pytest.mark.parametrize("missing", ["stack_native_owner", "scope_mode", "custody_namespace"])
def test_missing_stack_mirror_does_not_preempt_existing_stage_first(namespace, store, stage, initialized, missing):
    first = RuntimeError("Stage first precedes absent ordinary stack mirror")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            target, name = {"stack_native_owner": (scope.stack, "native_owner"),
                            "scope_mode": (scope, "mode"),
                            "custody_namespace": (custody, "namespace")}[missing]
            original = getattr(target, name)
            scope.first_failure = first
            try:
                delattr(target, name)
                assert stage._directory_store_binding_original(scope) is None
                with pytest.raises(RuntimeError) as caught:
                    stage._directory_original(scope, directory)
            finally:
                setattr(target, name, original)
            assert caught.value is first and scope.first_failure is first
            assert custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is first and scope.closed and custody.closed
    assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)


def test_unknown_snapshot_descriptors_are_not_called_before_stage_first(namespace, store, stage, initialized, monkeypatch):
    first = RuntimeError("Stage first precedes unknown snapshot descriptors")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            scope.first_failure = first
            getters, identities = [], []
            leaf = stage.held.original._identities

            def getter(receiver):
                getters.append(receiver)
                pytest.fail("snapshot invoked an unknown descriptor before Stage first")

            def observe(row, **kwargs):
                identities.append(row)
                return leaf(row, **kwargs)

            for cls, name in ((stage._StageScope, "held_scope"),
                              (store._StoreStackRecord, "namespace"),
                              (namespace._ClosedNamespaceAcquisition, "profile"),
                              (stage.held._ScopeRecord, "resources")):
                with monkeypatch.context() as patch:
                    patch.setattr(cls, name, property(getter), raising=False)
                    patch.setattr(stage.held.original, "_identities", observe)
                    assert stage._directory_store_binding_original(scope) is None
                    with pytest.raises(RuntimeError) as caught:
                        stage._directory_original(scope, directory)
                    assert caught.value is first and getters == [] and identities == []
            assert scope.first_failure is first and custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise first
    assert outer.value is first and scope.closed and custody.closed
    assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)


@pytest.mark.parametrize("owner", ["stage", "store"])
def test_existing_first_precedes_bad_directory_without_new_failure_bookkeeping(namespace, store, stage, initialized, owner):
    first = RuntimeError("original owned first before directory selection")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            if owner == "stage":
                scope.first_failure = first
            else:
                custody.first = first
            with pytest.raises(RuntimeError) as caught:
                stage._directory_original(scope, object())
            assert caught.value is first
            assert custody.held_original.first is None
            assert all(row.failure is None for row in custody.held_original.resources)
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is first


def test_new_resource_checks_payload_and_names_before_scope_and_never_registers_invalid_input(namespace, store, stage, initialized):
    first = RuntimeError("real Stage first remains pending behind input validation")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            scope.first_failure = first
            before = dict(stage._LEASES), dict(stage.lower._ACTIVE), scope.budget.bytes
            for payload, name, target, error_type, code in (
                (bytearray(b"probe"), "probe.tmp", "probe.json", stage.OwnedStageError, "STAGE_ORIGINAL_INPUT_REQUIRED"),
                (b"probe", "../probe.tmp", "probe.json", safe.SyncError, "SYNC_MEDIA_UNSAFE_PATH"),
                (b"probe", "probe.tmp", "probe.tmp", stage.OwnedStageError, "STAGE_ORIGINAL_INPUT_REQUIRED"),
            ):
                with pytest.raises(error_type) as caught:
                    stage._new_resource(scope, directory, name, payload, target, write=False, custody=True)
                assert caught.value.code == code and caught.value is not first
            assert not scope.resources
            assert (dict(stage._LEASES), dict(stage.lower._ACTIVE), scope.budget.bytes) == before
            assert scope.first_failure is first and custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise first
    assert outer.value is first


def test_real_unregistered_and_registered_foreign_directories_are_rejected(namespace, store, stage, initialized):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        # Open an existing fixture directory through its genuine ordinary
        # lifetime. Opening does not register it as a Namespace/Store directory.
        with safe._Directory(initialized.media, base=initialized.media) as unregistered:
            assert unregistered.handles and id(unregistered) not in namespace._CLOSED_DIRECTORY_RECORDS
            with pytest.raises(store.v2.StoreError) as absent:
                stage._directory_original(scope, unregistered)
            assert absent.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_DIRECTORY_CHANGED"
        foreign = custody.directories[0].original
        assert namespace._CLOSED_DIRECTORY_RECORDS[id(foreign)] is custody.directories[0]
        assert foreign.parent is None and foreign is not directory
        with pytest.raises(store.v2.StoreError) as ancestry:
            stage._directory_original(scope, foreign)
        assert ancestry.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_DIRECTORY_CHANGED"
        assert stage._directory_original(scope, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        assert not scope.resources and scope.first_failure is custody.first is custody.held_original.first is None


def test_late_lookup_dispatch_uses_remaining_original_chain_without_scope_replay(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        original, leaf = store._directory_record_original, stage.held.original._identities
        last = custody.held_original.resources[-1]
        seen, calls, changed = Counter(), [], []

        def custom(stack, selected):
            calls.append((stack, selected))
            return original(stack, selected)

        with monkeypatch.context() as patch:
            def change(row, **kwargs):
                result = leaf(row, **kwargs)
                if row.scope is scope.held_scope:
                    seen[id(row)] += 1
                if row is last and not changed:
                    patch.setattr(store, "_directory_record_original", custom)
                    changed.append(row)
                return result

            patch.setattr(stage.held.original, "_identities", change)
            assert stage._directory_original(scope, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        assert calls == [(scope.stack, directory)] and changed == [last]
        assert seen == Counter({id(row): 2 for row in custody.held_original.resources})


@pytest.mark.parametrize("mirror", ["scope", "native_owner"])
def test_late_real_stack_mirror_change_reaches_original_store_rejection(namespace, store, stage, initialized, monkeypatch, mirror):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        leaf, last = stage.held.original._identities, custody.held_original.resources[-1]
        previous, seen, changed = getattr(scope.stack, mirror), Counter(), []

        def change(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            if row is last and not changed:
                setattr(scope.stack, mirror, object())
                changed.append(row)
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(stage.held.original, "_identities", change)
                with pytest.raises(store.v2.StoreError) as caught:
                    stage._directory_original(scope, directory)
        finally:
            setattr(scope.stack, mirror, previous)
        assert caught.value.code == "PERSONAL_STORE_FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED"
        assert changed == [last] and seen == Counter({id(row): 1 for row in custody.held_original.resources})
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(scope.budget)].first is None
        assert stage._directory_original(scope, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]


def test_initial_real_cancellation_precedes_directory_type_and_reads(namespace, store, stage, initialized, monkeypatch):
    with pytest.raises(stage.lower.SharedIoCancelledError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            original, seen = stage.held.original._identities, []

            def observe(row, **kwargs):
                seen.append(row)
                return original(row, **kwargs)

            before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
            stage.lower.request_cancellation(scope.cancellation)
            with monkeypatch.context() as patch:
                patch.setattr(stage.held.original, "_identities", observe)
                with pytest.raises(stage.lower.SharedIoCancelledError) as caught:
                    stage._directory_original(scope, object())
            assert caught.value is scope.cancellation._failure and seen == []
            assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
            assert scope.first_failure is custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is caught.value


def test_real_clock_expiry_at_scope_tail_keeps_original_stage_deadline(namespace, store, stage, initialized, monkeypatch):
    with pytest.raises(stage.OwnedStageError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            leaf, last, visits = stage.held.original._identities, custody.held_original.resources[-1], []
            before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
            clocks = scope.clock, scope.budget.deadline, scope.budget.namespace_deadline

            def expire(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is last:
                    visits.append(row)
                    if len(visits) == 1:
                        deadline = min(scope.budget.deadline, scope.budget.namespace_deadline)
                        assert 0 < deadline - time.monotonic() <= 30
                        while time.monotonic() <= deadline:
                            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
                return result

            with monkeypatch.context() as patch:
                patch.setattr(stage.held.original, "_identities", expire)
                with pytest.raises(stage.OwnedStageError) as caught:
                    stage._directory_original(scope, directory)
            assert caught.value.code == "STAGE_ORIGINAL_DEADLINE" and visits == [last]
            assert (scope.clock, scope.budget.deadline, scope.budget.namespace_deadline) == clocks
            assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
            assert scope.first_failure is custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is caught.value


def test_real_initialization_namespace_directory_path_remains_original(namespace, store, stage, profile, monkeypatch):
    original, calls = stage._directory_original, []
    before = Path(safe._native_path(profile.database)).read_bytes()

    def observe(scope, directory):
        result = original(scope, directory)
        if scope.mode == "namespace":
            assert stage._SCOPES[id(scope)] is scope and not scope.closed
            assert type(result) is namespace._StoreDirectoryClose and result.original is directory
            assert any(record is result for record in scope.stack.resources)
            assert scope.permit.profile is profile and scope.permit.guard is lifecycle._CURRENT.get()
            calls.append((scope, directory, result))
        return result

    with lifecycle.exclusive(profile):
        with monkeypatch.context() as patch:
            patch.setattr(stage, "_directory_original", observe)
            namespace.initialize_explicit_locked(profile, init_operation_id=str(uuid.uuid4()),
                domain_id=str(uuid.uuid4()), facility_id=str(uuid.uuid4()))
    assert calls and all(scope.closed and id(scope) not in stage._SCOPES for scope, _, _ in calls)
    assert Path(safe._native_path(profile.database)).read_bytes() == before
