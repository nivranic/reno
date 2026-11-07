"""Same-effect guard observation with real owned Stage and native Namespace IO."""
from collections import Counter
import hashlib
import importlib
from pathlib import Path
import time
import uuid

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_stage_directory_entry_r701 import live_stage, initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r709"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r709.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r709/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r709.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "dda8b7c6832ef42b44a3cab9690c9f896629a5b290011235b2811478aa7b0cdc"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = importlib.import_module("authority_runtime_origin_r709.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


def _marker_observer(patch, custody):
    descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
    original, markers = lifecycle.os.read, Counter()

    def observe(descriptor, amount):
        raw = original(descriptor, amount)
        if descriptor in descriptors:
            assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
            markers[descriptor] += 1
        return raw

    patch.setattr(lifecycle.os, "read", observe)
    return descriptors, markers


def test_effect_keeps_real_native_reads_and_saves_one_fresh_profile_pass(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        native = stage._N2_EFFECT
        calls = []

        def custom_native(budget):
            calls.append(budget)
            return native(budget)

        before = scope.budget.bytes
        native_resources = tuple(stage.held._SCOPES[id(custody.namespace.stack.scope)].resources)
        assert native_resources and all(not row.finished for row in native_resources)
        observed_before = tuple(row.observed_bytes for row in native_resources)
        with monkeypatch.context() as patch:
            descriptors, markers = _marker_observer(patch, custody)
            assert stage._stage_effect_profile_binding_original(scope) is not None
            stage._effect(scope)
            first = markers.copy()
            first_reads = tuple(row.observed_bytes - old for row, old in zip(native_resources, observed_before))
            assert all(count > 0 for count in first_reads)
            assert set(first) == descriptors and min(first.values()) > 1
            assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
            assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
            stage._effect(scope)
            assert markers == first + first
            assert tuple(row.observed_bytes - old for row, old in zip(native_resources, observed_before)) == tuple(2 * count for count in first_reads)
            markers.clear()
            patch.setattr(stage, "_N2_EFFECT", custom_native)
            stage._effect(scope)
            assert markers == first + Counter({descriptor: 1 for descriptor in descriptors})
            assert tuple(row.observed_bytes - old for row, old in zip(native_resources, observed_before)) == tuple(3 * count for count in first_reads)
        assert calls == [scope.budget]
        assert scope.budget.bytes == before
        assert scope.first_failure is custody.first is custody.held_original.first is None


def test_original_dispatch_metadata_publication_reads_back_and_closes(namespace, store, stage, initialized):
    before_db = Path(store.safe._native_path(initialized.database)).read_bytes()
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        budget, actual = scope.budget, custody.namespace
        legacy, _, names = store._legacy(actual, scope.stack, budget)
        assert not ({"store-init-reservation.json", "store-header.json", store.v2.JOURNAL} & names)
        raw = store._canonical({
            "format_version": store.v2.FORMAT,
            "kind": "store_init_reservation",
            "store_init_operation_id": str(uuid.uuid4()),
            "declared_binding_sha256": store._sha(store._canonical(actual.declaration)),
            "legacy": legacy,
            "layout": store.v2._layout(),
        })
        assert stage._stage_effect_profile_binding_original(scope) is not None
        result = store._publish_store_metadata_original(
            scope.stack, directory, "store-init-reservation.json", raw, budget)
        assert result == hashlib.sha256(raw).hexdigest()
        assert Path(store.safe._native_path(directory.path / "store-init-reservation.json")).read_bytes() == raw
        assert stage._stage_effect_profile_binding_original(scope) is not None
        assert not scope.resources and scope.first_failure is custody.first is None
        assert not any(row.scope is scope for row in stage._STORE_METADATA_ORIGINALS.values())
        assert not any(row.scope is scope for row in stage._RETAINED_STORE_METADATA_ORIGINALS.values())
    assert Path(store.safe._native_path(initialized.database)).read_bytes() == before_db


@pytest.mark.parametrize("component", ["after", "inside", "finish", "files", "directories"])
def test_custom_native_component_keeps_original_remaining_profile(namespace, store, stage, initialized, monkeypatch, component):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        module, name = {
            "after": (store, "_native_effect_budget_after_original"),
            "inside": (store, "_native_effect_budget_inside_original"),
            "finish": (namespace, "_runtime_directory_finish_original"),
            "files": (lifecycle, "_verify_guard_plan_files_original"),
            "directories": (lifecycle, "_verify_guard_plan_directories_original"),
        }[component]
        original, native = getattr(module, name), stage._N2_EFFECT
        calls = []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def custom_native(budget):
            return native(budget)

        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            descriptors, markers = _marker_observer(patch, custody)
            assert stage._stage_effect_profile_binding_original(scope) is None
            stage._effect(scope)
            original_count, original_calls = markers.copy(), len(calls)
            assert set(original_count) == descriptors and original_calls > 0
            markers.clear()
            calls.clear()
            # Transparent custom native forces the original remaining profile
            # path while keeping the same actual custom component in place.
            patch.setattr(stage, "_N2_EFFECT", custom_native)
            stage._effect(scope)
            assert markers == original_count and len(calls) == original_calls
        assert scope.first_failure is custody.first is custody.held_original.first is None


def test_late_native_dispatch_runs_remaining_profile_without_replaying_native(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        native, read = stage._N2_EFFECT, lifecycle.os.read
        descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
        changed, calls, markers = [], [], Counter()

        def custom_native(budget):
            calls.append(budget)
            return native(budget)

        def change_during_native_finish(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER
                markers[descriptor] += 1
                current = namespace._RUNTIME_DIRECTORY_CURRENT.get()
                if current is not None and not changed:
                    binding = namespace._runtime_directory_binding_original(current)
                    if binding.phase == "closing":
                        stage._N2_EFFECT = custom_native
                        changed.append(current)
            return raw

        with monkeypatch.context() as patch:
            _, baseline = _marker_observer(patch, custody)
            stage._effect(scope)
            expected = baseline.copy()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", change_during_native_finish)
                stage._effect(scope)
        finally:
            stage._N2_EFFECT = native
        assert len(changed) == 1 and calls == []
        assert markers == expected + Counter({descriptor: 1 for descriptor in descriptors})
        assert id(changed[0]) not in namespace._RUNTIME_DIRECTORY_SCOPES
        assert scope.first_failure is custody.first is custody.held_original.first is None


@pytest.mark.parametrize("owner", ["stage", "store", "budget"])
def test_original_first_precedes_binding_and_unknown_guard_descriptor(namespace, store, stage, initialized, monkeypatch, owner):
    first = RuntimeError("original first before effect profile binding")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            target, field = {
                "stage": (scope, "first_failure"),
                "store": (custody, "first"),
                "budget": (store._BUDGET_RECORDS[id(scope.budget)], "first"),
            }[owner]
            setattr(target, field, first)
            getters = []

            def unknown(receiver):
                getters.append(receiver)
                pytest.fail("guard getter preempted original first")

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.StorageGuard, "verify", property(unknown))
                with pytest.raises(RuntimeError) as caught:
                    stage._effect(scope)
            assert caught.value is first and getters == []
            raise first
    assert outer.value is first


def test_real_cancellation_in_native_finish_prevents_effect_return(namespace, store, stage, initialized, monkeypatch):
    requested, returned = [], []
    with pytest.raises(namespace._closed_io.SharedIoCancelledError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            read = lifecycle.os.read
            descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
            cancellation = custody.namespace.budget.callback.data

            def cancel_during_finish(descriptor, amount):
                raw = read(descriptor, amount)
                current = namespace._RUNTIME_DIRECTORY_CURRENT.get()
                if descriptor in descriptors and current is not None and not requested:
                    binding = namespace._runtime_directory_binding_original(current)
                    if binding.phase == "closing":
                        requested.append(cancellation._failure)
                        namespace._closed_io.request_cancellation(cancellation)
                return raw

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", cancel_during_finish)
                with pytest.raises(namespace._ProducerFailure) as caught:
                    returned.append(stage._effect(scope))
            assert requested and caught.value.original is requested[0] and returned == []
            assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
            assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
            raise caught.value
    assert len(requested) == 1 and outer.value is requested[0]


def test_original_deadline_expiry_in_native_finish_prevents_effect_return(namespace, store, stage, initialized, monkeypatch):
    expired, returned = [], []
    with pytest.raises((namespace.NamespaceError, stage.lower.SharedIoError)) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            read = lifecycle.os.read
            descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
            clocks = scope.clock, scope.budget.deadline, scope.budget.namespace_deadline

            def expire_during_finish(descriptor, amount):
                raw = read(descriptor, amount)
                current = namespace._RUNTIME_DIRECTORY_CURRENT.get()
                if descriptor in descriptors and current is not None and not expired:
                    binding = namespace._runtime_directory_binding_original(current)
                    if binding.phase == "closing":
                        deadline = min(scope.budget.deadline, scope.budget.namespace_deadline)
                        remaining = deadline - time.monotonic()
                        assert 0 < remaining <= 30
                        expired.append(current)
                        time.sleep(remaining + 0.02)
                return raw

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", expire_during_finish)
                with pytest.raises((namespace.NamespaceError, stage.lower.SharedIoError)) as caught:
                    returned.append(stage._effect(scope))
            assert caught.value.code in {"NAMESPACE_DEADLINE", "OWNED_IO_NAMESPACE_DEADLINE"}
            assert len(expired) == 1 and returned == []
            assert (scope.clock, scope.budget.deadline, scope.budget.namespace_deadline) == clocks
            assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
            assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
            raise caught.value
    assert outer.value.code == caught.value.code
