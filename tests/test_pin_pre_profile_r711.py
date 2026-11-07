"""Real Pin pre-observation profile sharing keeps fresh IO and original failures."""
from collections import Counter
import hashlib
import importlib
from pathlib import Path
from types import MethodType

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_pin_verify import (  # noqa: F401
    live_pin,
    test_cancel_after_pre_observation_rejects_before_read_and_keeps_both_first_errors,
    test_real_deadline_after_pre_observation_rejects_before_read_and_keeps_first,
    test_wrong_expected_sha_rejects_after_real_reread_with_original_charge_boundary,
)
from tests.test_stage_effect_profile_r709 import (  # noqa: F401
    test_original_dispatch_metadata_publication_reads_back_and_closes,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r711"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r711.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r711/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r711.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "b38a4e85e7f4754de575bc6fe04e690594d75b51bab5c141765b028219b544e9"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = importlib.import_module("authority_runtime_origin_r711.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


def _marker_observer(patch, custody):
    descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
    assert len(descriptors) == len(custody.guard._locks) > 0
    read, markers = lifecycle.os.read, Counter()

    def observe(descriptor, amount):
        raw = read(descriptor, amount)
        if descriptor in descriptors:
            assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
            markers[descriptor] += 1
        return raw

    patch.setattr(lifecycle.os, "read", observe)
    return descriptors, markers


def _disable_only_new_pre_profile(patch, store):
    original = store._pin_pre_profile_binding_original

    def transparent(record):
        return original(record)

    # The original function-pointer admission rejects this custom entry. All
    # pre-existing Pin, Namespace, guard and tail dispatch remains original.
    patch.setattr(store, "_pin_pre_profile_binding_original", transparent)


def _native_resources(store, custody):
    scope = store._held_io._SCOPES[id(custody.namespace.stack.scope)]
    resources = tuple(row for row in scope.resources if not row.finished)
    assert resources and scope is not custody.held_original
    return resources


def _reports_directory(custody, profile):
    return next(lock.directory for lock in custody.guard._locks
                if lock.directory.path == profile.reports)


@pytest.mark.parametrize("kind,rebind", [("pin", False), ("pin", True), ("source", False), ("source", True)])
def test_each_pin_verify_saves_only_one_profile_pass_and_rereads_charges_fresh(
        namespace, store, initialized, monkeypatch, kind, rebind):
    with live_pin(namespace, store, initialized, kind, rebind=rebind) as (pin, record, resource, custody, budget):
        before_bytes, before_observed = budget.bytes, resource.observed_bytes
        other_bytes = custody.budget.bytes
        native = _native_resources(store, custody)
        native_ids = {id(row) for row in native}
        native_passes = 2 if kind == "source" else 1
        identities, visits = store._held_io.original._identities, Counter()

        def observe_identity(row, **kwargs):
            result = identities(row, **kwargs)
            if id(row) in native_ids:
                visits[id(row)] += 1
            return result

        with monkeypatch.context() as patch:
            descriptors, markers = _marker_observer(patch, custody)
            patch.setattr(store._held_io.original, "_identities", observe_identity)
            assert store._pin_pre_profile_binding_original(record) is not None
            pin.verify()
            first = markers.copy()
            assert first == Counter({descriptor: 4 if kind == "source" else 2 for descriptor in descriptors})
            assert visits == Counter({key: native_passes for key in native_ids})
            assert resource.observed_bytes - before_observed == budget.bytes - before_bytes == resource.byte_size
            pin.verify()
            assert markers == first + first
            assert visits == Counter({key: 2 * native_passes for key in native_ids})
            assert resource.observed_bytes - before_observed == budget.bytes - before_bytes == 2 * resource.byte_size
            markers.clear()
            _disable_only_new_pre_profile(patch, store)
            pin.verify()
            assert markers == first + Counter({descriptor: 1 for descriptor in descriptors})
            assert visits == Counter({key: 3 * native_passes for key in native_ids})
        assert resource.observed_bytes - before_observed == budget.bytes - before_bytes == 3 * resource.byte_size
        assert record.budget is budget
        if rebind:
            assert custody.budget.bytes == other_bytes
        assert custody.first is custody.held_original.first is resource.failure is None
        assert store._BUDGET_RECORDS[id(budget)].first is None
        before_close = budget.bytes
        sizes = {id(row.lease): row.byte_size for row in custody.held_original.resources}
        expected_close = sum(sizes[id(row.lease)] * (1 if row.kind == "source" else 2)
                             for row in custody.pins if row.budget is budget)
    assert budget.bytes - before_close == expected_close
    assert resource.observed_bytes - before_observed == (4 if kind == "source" else 5) * resource.byte_size


@pytest.mark.parametrize("component", ["namespace_stack", "guard_directory_verify"])
def test_custom_component_keeps_original_remaining_calls_without_budget_replay(
        namespace, store, initialized, monkeypatch, component):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        selected = _reports_directory(custody, initialized)
        original = (namespace._closed_stack_check_original if component == "namespace_stack"
                    else store.safe._Directory.verify)
        calls = []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        before = resource.observed_bytes, budget.bytes
        with monkeypatch.context() as patch:
            if component == "namespace_stack":
                patch.setattr(namespace, "_closed_stack_check_original", custom)
            else:
                patch.setattr(selected, "verify", MethodType(custom, selected))
            _, markers = _marker_observer(patch, custody)
            assert store._pin_pre_profile_binding_original(record) is None
            pin.verify()
            first_markers, first_calls = markers.copy(), list(calls)
            assert first_calls
            if component == "namespace_stack":
                assert first_calls == [((custody.namespace.stack,), {})]
            else:
                assert all(args == (selected,) and kwargs == {} for args, kwargs in first_calls)
            markers.clear()
            calls.clear()
            _disable_only_new_pre_profile(patch, store)
            pin.verify()
            assert markers == first_markers and calls == first_calls
        assert resource.observed_bytes - before[0] == budget.bytes - before[1] == 2 * resource.byte_size
        assert custody.first is custody.held_original.first is resource.failure is None


def test_late_namespace_dispatch_keeps_only_remaining_profile_without_replaying_budget(
        namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        native = _native_resources(store, custody)
        ids, last = {id(row) for row in native}, native[-1]
        identities, original = store._held_io.original._identities, namespace._closed_stack_check_original
        seen, changed, custom_calls = Counter(), [], []

        def custom(*args, **kwargs):
            custom_calls.append((args, kwargs))
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            descriptors, markers = _marker_observer(patch, custody)
            pin.verify()
            baseline = markers.copy()
            markers.clear()
            before = resource.observed_bytes, budget.bytes

            def change(row, **kwargs):
                result = identities(row, **kwargs)
                if id(row) in ids:
                    seen[id(row)] += 1
                if row is last and not changed:
                    patch.setattr(namespace, "_closed_stack_check_original", custom)
                    changed.append(row)
                return result

            patch.setattr(store._held_io.original, "_identities", change)
            pin.verify()
            assert changed == [last] and custom_calls == []
            assert seen == Counter({key: 1 for key in ids})
            assert markers == baseline + Counter({descriptor: 1 for descriptor in descriptors})
            assert resource.observed_bytes - before[0] == budget.bytes - before[1] == resource.byte_size
        assert custody.first is custody.held_original.first is resource.failure is None


def test_equal_directory_list_replacement_during_budget_keeps_full_remaining_profile(
        namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        directory = _reports_directory(custody, initialized)
        previous, last = directory.identities, _native_resources(store, custody)[-1]
        identities, changed = store._held_io.original._identities, []
        with monkeypatch.context() as patch:
            descriptors, markers = _marker_observer(patch, custody)
            pin.verify()
            baseline = markers.copy()
            markers.clear()
            before = resource.observed_bytes, budget.bytes

            def change(row, **kwargs):
                result = identities(row, **kwargs)
                if row is last and not changed:
                    directory.identities = list(previous)
                    changed.append(row)
                return result

            patch.setattr(store._held_io.original, "_identities", change)
            try:
                pin.verify()
                assert directory.identities is not previous and directory.identities == previous
            finally:
                directory.identities = previous
            assert changed == [last]
            assert markers == baseline + Counter({descriptor: 1 for descriptor in descriptors})
            assert resource.observed_bytes - before[0] == budget.bytes - before[1] == resource.byte_size


def test_mutable_identity_row_uses_full_path_and_late_wrong_inode_rejects_before_pin_read(
        namespace, store, initialized, monkeypatch):
    with pytest.raises(namespace.NamespaceError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            directory = _reports_directory(custody, initialized)
            index, original_row = next((i, row) for i, row in enumerate(directory.identities)
                                      if row[0] == directory.path)
            assert type(original_row) is tuple and len(original_row) == 3
            mutable = list(original_row)
            last = _native_resources(store, custody)[-1]
            identities, changed = store._held_io.original._identities, []
            try:
                directory.identities[index] = mutable
                with monkeypatch.context() as patch:
                    descriptors, markers = _marker_observer(patch, custody)
                    assert store._pin_pre_profile_binding_original(record) is None
                    observed, charged = resource.observed_bytes, budget.bytes
                    pin.verify()
                    assert markers == Counter({descriptor: 3 for descriptor in descriptors})
                    assert resource.observed_bytes - observed == budget.bytes - charged == resource.byte_size
                    markers.clear()
                    before = resource.observed_bytes, budget.bytes

                    def change(row, **kwargs):
                        result = identities(row, **kwargs)
                        if row is last and not changed:
                            mutable[2] += 1
                            changed.append(row)
                        return result

                    patch.setattr(store._held_io.original, "_identities", change)
                    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                        pin.verify()
                    assert changed == [last] and directory.identities[index] is mutable
                    assert markers == Counter({descriptor: 2 for descriptor in descriptors})
                    assert (resource.observed_bytes, budget.bytes) == before
            finally:
                directory.identities[index] = original_row
            assert caught.value.code == "STORAGE_UNSAFE_PATH" and caught.value.status == 503
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            assert custody.first is custody.held_original.first is resource.failure is None
            raise caught.value
    assert outer.value.code == "STORAGE_UNSAFE_PATH"


@pytest.mark.parametrize("field", ["_profiles", "_locks"])
def test_selected_rebound_budget_first_precedes_new_guard_getter_after_initial_pin_record(
        namespace, store, initialized, monkeypatch, field):
    first = RuntimeError("original selected rebound Budget first before new pre-profile binding")
    with pytest.raises(RuntimeError) as outer:
        with live_pin(namespace, store, initialized, rebind=True) as (pin, record, resource, custody, budget):
            selected = store._BUDGET_RECORDS[id(budget)]
            stack_budget = store._BUDGET_RECORDS[id(custody.budget)]
            assert budget is not custody.budget and selected is not stack_budget
            selected.first = first
            assert stack_budget.first is None
            last, identities = custody.held_original.resources[-1], store._held_io.original._identities
            installed, getters = [], []

            def unexpected(receiver):
                getters.append(receiver)
                pytest.fail("new binding invoked unknown guard getter before selected Budget first")

            before = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                def change(row, **kwargs):
                    result = identities(row, **kwargs)
                    if row is last and not installed:
                        # The initial Pin-record's original full profile has
                        # already returned. Its remaining directory/resource
                        # checks do not read this guard field.
                        patch.setattr(lifecycle.StorageGuard, field, property(unexpected), raising=False)
                        installed.append(row)
                    return result

                patch.setattr(store._held_io.original, "_identities", change)
                with pytest.raises(RuntimeError) as caught:
                    pin.verify()
            assert installed == [last] and getters == []
            assert caught.value is selected.first is first
            assert stack_budget.first is None
            assert (resource.observed_bytes, budget.bytes) == before
            assert custody.first is custody.held_original.first is resource.failure is None
            raise caught.value
    assert outer.value is first
