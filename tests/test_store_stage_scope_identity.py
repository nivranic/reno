"""Real Stage/Store scopes retain complete identity checks and fallbacks."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path

import pytest

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile, _ChangedStat  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r673"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r673.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r673/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r673.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "244088ff995472fe51869bfeab6ee24dfbf8298bc3419bb3a24f3be664ab78f4"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = importlib.import_module("authority_runtime_origin_r673.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "b8cf7065c7c684d7eb48c6d48c5768e0e7e15ff4b4a87091cff3c31b108fb420"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


@contextmanager
def _live_stage(namespace, store, stage, profile):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                scope = store._stage_scope_original(stack)
                custody = store._STACK_RECORDS[id(stack)]
                assert stage._SCOPES[id(scope)] is scope
                assert custody.stage is scope and scope.permit is custody and scope.held_scope is custody.scope
                assert len(custody.held_original.resources) == 3
                assert all(not row.finished and row.resource_kind == "filehold" for row in custody.held_original.resources)
                yield scope, custody
            assert custody.closed and scope.closed and scope.first_failure is None
            assert id(stack) not in store._STACK_RECORDS and id(scope) not in stage._SCOPES
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


def test_original_store_scope_verifies_every_resource_once_and_next_call_is_fresh(namespace, store, stage, initialized, monkeypatch):
    with _live_stage(namespace, store, stage, initialized) as (scope, custody):
        original = stage.held._verify_identity_record
        seen = Counter()
        expected = Counter({id(row): 1 for row in custody.held_original.resources})

        def observe(row, **kwargs):
            result = original(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            return result

        with monkeypatch.context() as patch:
            patch.setattr(stage.held, "_verify_identity_record", observe)
            assert stage._store_scope_identity_binding_original(scope) is custody
            assert stage._scope_live(scope) is scope
            assert seen == expected
            assert stage._scope_live(scope) is scope
            assert seen == expected + expected


def test_custom_store_verifier_retains_both_original_identity_passes(namespace, store, stage, initialized, monkeypatch):
    with _live_stage(namespace, store, stage, initialized) as (scope, custody):
        original_leaf = stage.held._verify_identity_record
        original_verify = stage._verify_store_stage_scope_original
        seen, calls = Counter(), []
        expected = Counter({id(row): 2 for row in custody.held_original.resources})

        def observe(row, **kwargs):
            result = original_leaf(row, **kwargs)
            if row.scope is scope.held_scope:
                seen[id(row)] += 1
            return result

        def custom(value):
            calls.append(value)
            return original_verify(value)

        with monkeypatch.context() as patch:
            patch.setattr(stage.held, "_verify_identity_record", observe)
            patch.setattr(stage, "_verify_store_stage_scope_original", custom)
            assert stage._store_scope_identity_binding_original(scope) is None
            assert stage._scope_live(scope) is scope
            assert seen == expected and calls == [scope]


def test_other_real_held_scope_never_qualifies_and_original_binding_gate_rejects(namespace, store, stage, initialized):
    with _live_stage(namespace, store, stage, initialized) as (scope, custody):
        with store._ClosingStack(scope.budget) as other:
            other_custody = store._STACK_RECORDS[id(other)]
            assert other_custody.scope is not custody.scope
            assert stage.held._scope_original(other_custody.scope) is other_custody.held_original
            original_scope = scope.held_scope
            try:
                scope.held_scope = other_custody.scope
                assert stage._store_scope_identity_binding_original(scope) is None
                with pytest.raises(stage.OwnedStageError) as caught:
                    stage._scope_live(scope)
                assert caught.value.code == "STAGE_ORIGINAL_BUDGET_CHANGED"
            finally:
                scope.held_scope = original_scope
        assert stage._scope_live(scope) is scope


def test_real_file_identity_failure_and_recorded_first_are_preserved(namespace, store, stage, initialized, monkeypatch):
    first_errors = []
    with pytest.raises(stage.lower.SharedIoError) as outer:
        with _live_stage(namespace, store, stage, initialized) as (scope, custody):
            resource = custody.held_original.resources[0]
            target = safe._native_path(resource.directory.path / resource.name)
            original_stat = namespace.os.stat
            faults = []

            def changed_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == target:
                    faults.append(path)
                    return _ChangedStat(result, st_ino=result.st_ino + 1)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(namespace.os, "stat", changed_stat)
                assert stage._store_scope_identity_binding_original(scope) is custody
                with pytest.raises(stage.lower.SharedIoError) as caught:
                    stage._scope_live(scope)
            first_errors.append(caught.value)
            assert faults and custody.held_original.first is caught.value
            assert stage._store_scope_identity_binding_original(scope) is None
            with pytest.raises(stage.lower.SharedIoError) as repeated:
                stage._scope_live(scope)
            assert repeated.value is caught.value
            raise caught.value
    assert len(first_errors) == 1 and outer.value is first_errors[0]


def test_dispatch_change_during_verification_is_rejected_without_replaying_body(namespace, store, stage, initialized, monkeypatch):
    with _live_stage(namespace, store, stage, initialized) as (scope, custody):
        original_leaf = stage.held._verify_identity_record
        original_stack = store._stage_stack_original
        changed, custom_calls = [], []
        with monkeypatch.context() as patch:
            def custom_stack(stack):
                custom_calls.append(stack)
                return original_stack(stack)

            def change_after_leaf(row, **kwargs):
                result = original_leaf(row, **kwargs)
                if row.scope is scope.held_scope and not changed:
                    patch.setattr(store, "_stage_stack_original", custom_stack)
                    changed.append(row)
                return result

            patch.setattr(stage.held, "_verify_identity_record", change_after_leaf)
            assert stage._store_scope_identity_binding_original(scope) is custody
            with pytest.raises(stage.OwnedStageError) as caught:
                stage._scope_live(scope)
            assert caught.value.code == "STAGE_ORIGINAL_BUDGET_CHANGED"
            assert len(changed) == 1 and custom_calls == []
        assert stage._scope_live(scope) is scope
