"""One real source batch owns one directory and three independent fileholds."""
from contextlib import contextmanager
import hashlib
from pathlib import Path

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r691"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r691.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r691/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r691.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "117eedb45812c78a83abd82fa66d9f1b7f354b58613b837fd46be6885962550e"
    assert module.ns is namespace
    return module


@contextmanager
def empty_stack(namespace, store, profile):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                custody = store._STACK_RECORDS[id(stack)]
                assert not custody.directories and not custody.pins and not custody.held_original.resources
                yield stack, custody, budget
            assert custody.closed and custody.first is None and id(stack) not in store._STACK_RECORDS
            assert all(row.closed and row.attempted == row.released == set(range(len(row.handles))) for row in custody.directories)
            assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)
            assert all(pin.closed and id(pin.original) not in store._PIN_RECORDS for pin in custody.pins)
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


def sources(store):
    return ((store.ns, store.NAMESPACE_SOURCE_SHA256), (store.v2, store.V2_SOURCE_SHA256),
            (store.mc, store.MUTATION_SOURCE_SHA256))


def test_one_directory_keeps_all_real_files_hashes_reads_charges_and_close(namespace, store, initialized):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        store._source_pins(stack, budget)
        assert len(custody.directories) == 1 and len(custody.pins) == len(custody.held_original.resources) == 3
        directory = custody.directories[0]
        assert namespace._CLOSED_DIRECTORY_RECORDS[id(directory.original)] is directory
        assert directory.entered and not directory.closed
        assert len({id(row.lease) for row in custody.pins}) == 3
        assert len({row.fd for row in custody.held_original.resources}) == 3
        total = 0
        for (module, expected), pin_record, resource in zip(sources(store), custody.pins, custody.held_original.resources, strict=True):
            raw = Path(module.__file__).read_bytes()
            total += len(raw)
            assert pin_record.directory is resource.directory is directory.original
            assert pin_record.scope is resource.scope is custody.scope
            assert pin_record.lease is resource.lease and pin_record.kind == "source"
            assert pin_record.maximum == resource.maximum == 512 * 1024
            assert pin_record.name == Path(module.__file__).name
            assert resource.resource_kind == "filehold" and resource.payload == raw
            assert resource.sha256 == hashlib.sha256(raw).hexdigest() == expected
            assert resource.observed_bytes == 2 * len(raw)  # Original initial read + verify.
        assert budget.bytes == 0
        for pin in custody.pins:
            pin.original.verify()
        assert budget.bytes == total
        assert all(row.observed_bytes == 3 * row.byte_size for row in custody.held_original.resources)
    assert budget.bytes == 2 * total  # Original normal close verifies every source again.
    assert all(row.observed_bytes == 4 * row.byte_size for row in custody.held_original.resources)


def test_next_batch_acquires_new_directory_and_independent_file_leases(namespace, store, initialized):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        store._source_pins(stack, budget)
        first_directory, first_pins = custody.directories[0], custody.pins
        first_resources = tuple(custody.held_original.resources)
        store._source_pins(stack, budget)
        assert len(custody.directories) == 2 and custody.directories[0] is first_directory
        second_directory = custody.directories[1]
        assert second_directory is not first_directory and second_directory.original is not first_directory.original
        assert second_directory.original.path == first_directory.original.path
        assert len(custody.pins) == 6 and custody.pins[:3] == first_pins
        assert len({id(pin.lease) for pin in custody.pins}) == 6
        assert tuple(custody.held_original.resources[:3]) == first_resources
        assert all(pin.directory is first_directory.original for pin in custody.pins[:3])
        assert all(pin.directory is second_directory.original for pin in custody.pins[3:])
        assert all(row.observed_bytes == 2 * row.byte_size for row in custody.held_original.resources)
        assert budget.bytes == 0


@pytest.mark.parametrize("target", ["source", "entry", "directory_enter", "file_acquire", "stack"])
def test_custom_helpers_keep_three_original_directories(namespace, store, initialized, monkeypatch, target):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        module, name = {
            "source": (store, "_source_acquire_original"),
            "entry": (store._ClosingStack, "enter_context"),
            "directory_enter": (namespace, "_closed_directory_enter_original"),
            "file_acquire": (store._held_io, "_acquire_file_original"),
            "stack": (store, "_stage_stack_original"),
        }[target]
        original, calls = getattr(module, name), []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            assert store._source_batch_paths_original(stack) is None
            store._source_pins(stack, budget)
        assert len(custody.directories) == len(custody.pins) == len(custody.held_original.resources) == 3
        assert len({id(pin.directory) for pin in custody.pins}) == 3
        assert all(row.observed_bytes == 2 * row.byte_size for row in custody.held_original.resources)
        assert budget.bytes == 0
        if target == "source":
            assert calls == [((stack, module, expected, budget), {}) for module, expected in sources(store)]
        elif target == "stack":
            assert len(calls) == 12 and all(args == (stack,) and not kwargs for args, kwargs in calls)
        else:
            assert len(calls) == 3
            if target == "entry":
                assert all(args[0] is stack and type(args[1]) is store.safe._Directory and not kwargs for args, kwargs in calls)
            elif target == "directory_enter":
                assert all(args[1] is budget and args[2].original is args[0] and not kwargs for args, kwargs in calls)
            else:
                assert all(args[0] is custody.scope and kwargs == {"maximum": 512 * 1024, "budget": custody.clock, "kind": "filehold"} for args, kwargs in calls)


def test_wrong_budget_rejects_before_any_directory_or_file_acquisition(namespace, store, initialized):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        with pytest.raises(store.v2.StoreError) as caught:
            store._source_pins(stack, object())
        assert caught.value.code == "PERSONAL_STORE_CODEC_SOURCE_BINDING_CHANGED"
        assert not custody.directories and not custody.pins and not custody.held_original.resources
        assert budget.bytes == 0 and custody.first is None


def test_real_cancellation_precedes_wrong_budget_and_preserves_original_object(namespace, store, initialized):
    lower = store._held_io.original
    first = None
    with pytest.raises(lower.SharedIoCancelledError) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            first = custody.cancellation._failure
            lower.request_cancellation(custody.cancellation)
            with pytest.raises(lower.SharedIoCancelledError) as caught:
                store._source_pins(stack, object())
            assert caught.value is first
            assert not custody.directories and not custody.pins and not custody.held_original.resources
            raise caught.value
    assert outer.value is first


def test_actual_source_sha_mismatch_retains_original_first_and_prior_resources(namespace, store, initialized, monkeypatch):
    failures = []
    with pytest.raises(store.v2.StoreError) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            with monkeypatch.context() as patch:
                patch.setattr(store, "V2_SOURCE_SHA256", "0" * 64)
                with pytest.raises(store.v2.StoreError) as caught:
                    store._source_pins(stack, budget)
            assert caught.value.code == "PERSONAL_STORE_CODEC_SOURCE_BINDING_CHANGED"
            assert len(custody.directories) == 1 and len(custody.pins) == len(custody.held_original.resources) == 2
            assert custody.first is store._BUDGET_RECORDS[id(budget)].first is caught.value
            assert all(row.observed_bytes == 2 * row.byte_size for row in custody.held_original.resources)
            assert custody.pins[0].name == Path(namespace.__file__).name
            assert custody.held_original.resources[1].sha256 == store.V2_SOURCE_SHA256
            failures.append(caught.value)
            raise caught.value
    assert failures == [outer.value]


@pytest.mark.parametrize("change", ["dispatch", "directory"])
def test_late_binding_change_rejects_without_replaying_source_acquisition(namespace, store, initialized, monkeypatch, change):
    error_type = store.v2.StoreError if change == "dispatch" else namespace.NamespaceError
    failures, changed, custom_calls = [], [], []
    with pytest.raises(error_type) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            original_sha, original_source = store._sha, store._source_acquire_original
            saved_directory = saved_base = None
            try:
                with monkeypatch.context() as patch:
                    def custom(*args):
                        custom_calls.append(args)
                        return original_source(*args)

                    def change_after_first_hash(raw):
                        nonlocal saved_directory, saved_base
                        result = original_sha(raw)
                        if len(custody.pins) == 1 and not changed:
                            changed.append(custody.pins[0])
                            if change == "dispatch":
                                patch.setattr(store, "_source_acquire_original", custom)
                            else:
                                saved_directory = custody.directories[0].original
                                saved_base = saved_directory.base
                                saved_directory.base = saved_directory.path.parent
                        return result

                    patch.setattr(store, "_sha", change_after_first_hash)
                    with pytest.raises(error_type) as caught:
                        store._source_pins(stack, budget)
            finally:
                if saved_directory is not None:
                    saved_directory.base = saved_base
            expected = "PERSONAL_STORE_CODEC_SOURCE_BINDING_CHANGED" if change == "dispatch" else "NAMESPACE_STORE_ORIGINAL_DIRECTORY_CHANGED"
            assert caught.value.code == expected
            assert len(changed) == 1 and custom_calls == []
            assert len(custody.directories) == len(custody.pins) == len(custody.held_original.resources) == 1
            assert custody.held_original.resources[0].observed_bytes == 2 * custody.held_original.resources[0].byte_size
            failures.append(caught.value)
            raise caught.value
    assert failures == [outer.value]
