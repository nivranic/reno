"""Real Stage directory records retain complete original checks and fallbacks."""
from collections import Counter
import hashlib
from pathlib import Path

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile, _ChangedStat  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r677"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r677.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r677/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r677.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3e4faa260389083435bdf3f839a91793a34a848a0d011b0d7dd1601200c6095a"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = import_historical_snapshot("authority_runtime_origin_r677.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1"
    assert module.ns is namespace and module.store is store
    return module


@pytest.fixture
def directory_resource(namespace, store, stage, initialized):
    with lifecycle.exclusive(initialized):
        with namespace._closed_namespace_owned_locked(initialized, exclusive=True) as owner:
            budget = store._Budget(owner)
            actual = store._budget_record_original(budget).acquisition
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                scope = store._stage_scope_original(stack)
                custody = store._STACK_RECORDS[id(stack)]
                # Genuine pre-open resource, with its actual acquired directory
                # and strong original registry. File acquisition is covered by
                # the reused natural metadata publication integration.
                record = stage._new_resource(scope, actual.directory, "directory-probe.tmp",
                    b"probe", "directory-probe.json", write=False, custody=True)
                assert namespace._CLOSED_DIRECTORY_RECORDS[id(actual.directory)] is record.directory_record
                assert stage._LEASES[id(record.lease)] is record and stage.lower._ACTIVE[id(record)] is record
                assert record.directory_record.handles and record.directory_record.entered
                try:
                    yield record
                finally:
                    assert stage._finish_resource(record, None) is None
                assert id(record) not in stage.lower._ACTIVE and id(record.lease) not in stage._LEASES
                assert not scope.resources
            assert custody.closed and scope.closed and scope.first_failure is None
            assert id(stack) not in store._STACK_RECORDS and id(scope) not in stage._SCOPES
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


def test_fixed_directory_check_verifies_all_target_handles_once_and_next_call_is_fresh(namespace, stage, directory_resource, monkeypatch):
    record = directory_resource
    handles = Counter(record.directory_record.handles)
    original, seen = namespace._closed_directory_native, Counter()

    def observe(handle):
        result = original(handle)
        if handle in handles:
            seen[handle] += 1
        return result

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "_closed_directory_native", observe)
        stage._stage_directory_verify_original(record)
        assert seen == handles
        stage._stage_directory_verify_original(record)
        assert seen == handles + handles


@pytest.mark.parametrize("target", ["store", "namespace"])
def test_custom_lookup_or_verifier_keeps_original_arguments_and_both_checks(namespace, store, stage, directory_resource, monkeypatch, target):
    record = directory_resource
    module, name = (store, "_directory_record_original") if target == "store" else (namespace, "_closed_directory_verify_original")
    original, native = getattr(module, name), namespace._closed_directory_native
    calls, seen = [], Counter()
    handles = Counter(record.directory_record.handles)

    def custom(*args):
        calls.append(args)
        return original(*args)

    def observe(handle):
        result = native(handle)
        if handle in handles:
            seen[handle] += 1
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module, name, custom)
        patch.setattr(namespace, "_closed_directory_native", observe)
        stage._stage_directory_verify_original(record)
        assert seen == handles + handles
        if target == "store":
            assert calls == [(record.scope.stack, record.directory)]
        else:
            assert calls[-2:] == [(record.directory_record,), (record.directory_record,)]


@pytest.mark.parametrize("target", ["store", "namespace"])
def test_late_dispatch_change_keeps_remaining_check_without_lookup_replay(namespace, store, stage, directory_resource, monkeypatch, target):
    record = directory_resource
    module, name = (store, "_directory_record_original") if target == "store" else (namespace, "_closed_directory_verify_original")
    original, native = getattr(module, name), namespace._closed_directory_native
    calls, seen, changed = [], Counter(), []
    handles = Counter(record.directory_record.handles)
    with monkeypatch.context() as patch:
        def custom(*args):
            calls.append(args)
            return original(*args)

        def observe(handle):
            result = native(handle)
            if handle in handles:
                seen[handle] += 1
                if handle == record.directory_record.handles[-1] and not changed:
                    patch.setattr(module, name, custom)
                    changed.append(handle)
            return result

        patch.setattr(namespace, "_closed_directory_native", observe)
        stage._stage_directory_verify_original(record)
        assert seen == handles + handles and len(changed) == 1
        assert calls == ([] if target == "store" else [(record.directory_record,)])


def test_different_genuine_directory_record_is_rejected(namespace, store, stage, directory_resource):
    record = directory_resource
    original = record.directory_record
    other = store._STACK_RECORDS[id(record.scope.stack)].directories[0]
    assert other is not original and namespace._CLOSED_DIRECTORY_RECORDS[id(other.original)] is other
    try:
        record.directory_record = other
        with pytest.raises(stage.OwnedStageError) as caught:
            stage._stage_directory_verify_original(record)
        assert caught.value.code == "STAGE_ORIGINAL_DIRECTORY_REQUIRED"
    finally:
        record.directory_record = original
    stage._stage_directory_verify_original(record)


@pytest.mark.parametrize("target", ["path", "handle"])
def test_original_checker_rejects_changed_real_directory_identity(namespace, stage, directory_resource, monkeypatch, target):
    record = directory_resource
    path = safe._native_path(record.directory.path)
    handle = record.directory_record.handles[-1]
    original_stat, original_native = namespace.os.stat, namespace._closed_directory_native
    faults = []

    def changed_stat(value, *args, **kwargs):
        result = original_stat(value, *args, **kwargs)
        if value == path:
            faults.append(value)
            return _ChangedStat(result, st_ino=result.st_ino + 1)
        return result

    def changed_native(value):
        result = original_native(value)
        if value == handle:
            faults.append(value)
            return result[0], result[1] + 1
        return result

    with monkeypatch.context() as patch:
        if target == "path":
            patch.setattr(namespace.os, "stat", changed_stat)
        else:
            patch.setattr(namespace, "_closed_directory_native", changed_native)
        with pytest.raises(safe.SyncError if target == "path" else namespace.NamespaceError):
            stage._stage_directory_verify_original(record)
    assert faults
    stage._stage_directory_verify_original(record)


def test_original_lookup_exception_is_preserved_without_final_verifier(namespace, store, stage, directory_resource, monkeypatch):
    record = directory_resource
    first = RuntimeError("explicit original lookup failure")
    calls = []

    def fail(stack, directory):
        assert stack is record.scope.stack and directory is record.directory
        raise first

    def unexpected(value):
        calls.append(value)
        raise AssertionError("final verifier must not run after original lookup failed")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_directory_record_original", fail)
        patch.setattr(namespace, "_closed_directory_verify_original", unexpected)
        with pytest.raises(RuntimeError) as caught:
            stage._stage_directory_verify_original(record)
    assert caught.value is first and calls == []
