"""A real Store stack preserves every directory while batching path reads."""
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
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r671"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r671.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r671/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r671.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "244088ff995472fe51869bfeab6ee24dfbf8298bc3419bb3a24f3be664ab78f4"
    assert module.ns is namespace
    return module


@pytest.fixture
def actual_stack(namespace, store, initialized):
    with lifecycle.exclusive(initialized):
        with namespace._closed_namespace_owned_locked(initialized, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                record = store._STACK_RECORDS[id(stack)]
                assert record.original is stack and len(record.directories) == 3
                assert len({id(directory) for directory in record.directories}) == 3
                assert len({directory.original.path for directory in record.directories}) == 1
                assert all(directory.entered and not directory.closed for directory in record.directories)
                yield record
            assert record.closed and record.first is None and id(stack) not in store._STACK_RECORDS
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


def test_single_sweep_shares_paths_but_checks_every_real_handle_and_next_sweep_is_fresh(namespace, store, actual_stack, monkeypatch):
    record = actual_stack
    directories = record.directories
    paths = {safe._native_path(path) for directory in directories for path, _, _ in directory.original.identities}
    expected_handles = Counter(handle for directory in directories for handle in directory.handles)
    assert paths and expected_handles
    seen_paths, seen_handles = Counter(), Counter()
    original_stat = namespace.os.stat
    original_native = namespace._closed_directory_native

    def stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        seen_paths[path] += 1
        return result

    def native(handle):
        result = original_native(handle)
        seen_handles[handle] += 1
        return result

    with monkeypatch.context() as patch:
        patch.setattr(namespace.os, "stat", stat)
        patch.setattr(namespace, "_closed_directory_native", native)
        assert store._store_directory_sweep_dispatch_original()
        store._store_directories_verify_original(record)
        assert seen_paths == Counter({path: 1 for path in paths})
        assert seen_handles == expected_handles
        store._store_directories_verify_original(record)
        assert seen_paths == Counter({path: 2 for path in paths})
        assert seen_handles == expected_handles + expected_handles
    assert record.directories is directories


def test_later_sweep_observes_and_rejects_changed_path_identity(namespace, store, actual_stack, monkeypatch):
    record = actual_stack
    store._store_directories_verify_original(record)
    target = safe._native_path(record.directories[-1].original.path)
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
        assert store._store_directory_sweep_dispatch_original()
        with pytest.raises(safe.SyncError):
            store._store_directories_verify_original(record)
    assert faults == [target]
    store._store_directories_verify_original(record)


def test_shared_paths_do_not_skip_a_later_records_native_handle(namespace, store, actual_stack, monkeypatch):
    record = actual_stack
    target = record.directories[-1].handles[-1]
    original_native = namespace._closed_directory_native
    inspected = []

    def changed_native(handle):
        identity = original_native(handle)
        inspected.append(handle)
        return (identity[0], identity[1] + 1) if handle == target else identity

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "_closed_directory_native", changed_native)
        assert store._store_directory_sweep_dispatch_original()
        with pytest.raises(namespace.NamespaceError) as caught:
            store._store_directories_verify_original(record)
    assert caught.value.code == "NAMESPACE_STORE_ORIGINAL_DIRECTORY_HANDLE_CHANGED"
    assert inspected[-1] == target
    assert all(directory.handles[-1] in inspected for directory in record.directories)
    store._store_directories_verify_original(record)


def test_custom_single_verifier_keeps_original_arguments_and_first_exception(namespace, store, actual_stack, monkeypatch):
    record = actual_stack
    original_single = namespace._closed_directory_verify_original
    calls = []
    first = RuntimeError("synthetic original directory callback error")

    def custom(directory):
        calls.append(directory)
        if len(calls) == 2:
            raise first
        return original_single(directory)

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "_closed_directory_verify_original", custom)
        assert not store._store_directory_sweep_dispatch_original()
        with pytest.raises(RuntimeError) as caught:
            store._store_directories_verify_original(record)
    assert caught.value is first and calls == list(record.directories[:2])
    store._store_directories_verify_original(record)
