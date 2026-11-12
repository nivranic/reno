"""Genuine Store publication entries keep late resource and directory checks."""
from collections import Counter
from contextlib import contextmanager
import hashlib
from pathlib import Path
import time
import uuid

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r717"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r717.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r717/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "62a1d30835444f14880d96579773d2c9259102db56d4998831e051767176cee5"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r717.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "dae1e849df7b3d46a4a50d813c4ba225c8367c99276638ce94625faf3a72f6f7"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = import_historical_snapshot("authority_runtime_origin_r717.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
    assert module.ns is namespace and module.store is store
    return module


@contextmanager
def live_entry(namespace, store, profile):
    before_db = Path(safe._native_path(profile.database)).read_bytes()
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                custody = store._STACK_RECORDS[id(stack)]
                assert custody.stage is None and len(custody.held_original.resources) == 3
                assert all(row.resource_kind == "filehold" for row in custody.held_original.resources)
                yield stack, custody, budget
            assert custody.closed and id(stack) not in store._STACK_RECORDS
            assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)
            assert all(row.closed for row in custody.pins)
            if custody.stage is not None:
                assert custody.stage.closed
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS
    assert Path(safe._native_path(profile.database)).read_bytes() == before_db


def observe_entry(patch, store, custody, *, on_identity=None):
    read, identities = lifecycle.os.read, store._held_io.original._identities
    descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
    markers, samples = Counter(), Counter()

    def marker(fd, amount):
        raw = read(fd, amount)
        if fd in descriptors:
            assert raw == lifecycle._MARKER and amount == len(raw) + 1
            markers[fd] += 1
        return raw

    def identity(resource, **kwargs):
        result = identities(resource, **kwargs)
        if resource.scope is custody.scope:
            samples[id(resource)] += 1
            if on_identity is not None:
                on_identity(resource, samples[id(resource)])
        return result

    patch.setattr(lifecycle.os, "read", marker)
    patch.setattr(store._held_io.original, "_identities", identity)
    return descriptors, markers, samples


def reference_only(patch, store):
    original = store._publish_entry_profile_binding_original

    def transparent(stack):
        return original(stack)

    patch.setattr(store, "_publish_entry_profile_binding_original", transparent)


@pytest.mark.parametrize("selected", ["root", "facts"])
def test_fixed_publish_entry_shares_only_second_profile_observation(namespace, store, initialized, monkeypatch, selected):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        directory = custody.namespace.directory if selected == "root" else custody.namespace.facts
        record = namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        resources = tuple(custody.held_original.resources)
        before_reads = tuple(row.observed_bytes for row in resources)
        before_bytes = budget.bytes
        with monkeypatch.context() as patch:
            descriptors, markers, samples = observe_entry(patch, store, custody)
            assert store._publish_store_directory_entry_original(stack, directory, budget) is record
            first_markers, first_samples = markers.copy(), samples.copy()
            assert first_markers == Counter({fd: 1 for fd in descriptors})
            assert first_samples == Counter({id(row): 2 for row in resources})
            store._publish_store_directory_entry_original(stack, directory, budget)
            assert markers == first_markers + first_markers
            assert samples == first_samples + first_samples
            markers.clear()
            samples.clear()
            reference_only(patch, store)
            assert store._publish_store_directory_entry_original(stack, directory, budget) is record
            assert markers == Counter({fd: 2 for fd in descriptors})
            assert samples == first_samples
        assert tuple(row.observed_bytes for row in resources) == before_reads
        assert budget.bytes == before_bytes == 0
        assert custody.stage is None and custody.first is custody.held_original.first is None
    assert budget.bytes == sum(row.byte_size for row in resources)


def test_actual_publication_creates_stage_only_after_entry_and_reads_back(namespace, store, stage, initialized, monkeypatch):
    entries = []
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        actual = custody.namespace
        legacy, _, names = store._legacy(actual, stack, budget)
        assert not ({"store-init-reservation.json", "store-header.json", store.v2.JOURNAL} & names)
        raw = store._canonical({
            "format_version": store.v2.FORMAT,
            "kind": "store_init_reservation",
            "store_init_operation_id": str(uuid.uuid4()),
            "declared_binding_sha256": store._sha(store._canonical(actual.declaration)),
            "legacy": legacy, "layout": store.v2._layout(),
        })
        original = store._publish_store_directory_entry_original

        def observe(stack_arg, directory_arg, budget_arg):
            assert stack_arg is stack and directory_arg is actual.directory and budget_arg is budget
            assert custody.stage is None
            result = original(stack_arg, directory_arg, budget_arg)
            assert custody.stage is None
            entries.append(result)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(store, "_publish_store_directory_entry_original", observe)
            result = store._publish_store_metadata_original(stack, actual.directory, "store-init-reservation.json", raw, budget)
        assert entries == [namespace._CLOSED_DIRECTORY_RECORDS[id(actual.directory)]]
        assert result == hashlib.sha256(raw).hexdigest()
        assert Path(safe._native_path(actual.directory.path / "store-init-reservation.json")).read_bytes() == raw
        scope = custody.stage
        assert scope is not None and not scope.resources and scope.first_failure is None
        assert not any(row.scope is scope for row in stage._STORE_METADATA_ORIGINALS.values())
        assert not any(row.scope is scope for row in stage._RETAINED_STORE_METADATA_ORIGINALS.values())
    assert scope.closed and id(scope) not in stage._SCOPES


def test_other_real_budget_rejects_before_directory_raw_or_stage_selection(namespace, store, initialized, monkeypatch):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        other = store._Budget(custody.namespace.original)
        assert other is not budget and other.namespace_owner is budget.namespace_owner
        calls = []

        def unexpected(*args, **kwargs):
            calls.append((args, kwargs))
            pytest.fail("budget identity must be checked before later publication selection")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_directory_record_original", unexpected)
            patch.setattr(store, "_store_metadata_maximum_original", unexpected)
            patch.setattr(store, "_stage_scope_original", unexpected)
            with pytest.raises(store.v2.StoreError) as caught:
                store._publish_store_metadata_original(stack, object(), "store-init-reservation.json", object(), other)
        assert caught.value.code == "PERSONAL_STORE_STAGE_STORE_SCOPE_REQUIRED"
        assert calls == [] and custody.stage is None
        assert custody.first is custody.held_original.first is None


@pytest.mark.parametrize("component", ["stack", "lookup", "profile", "guard", "guard_binding", "directory", "limits_eq"])
def test_custom_dispatch_keeps_full_profile_and_original_call_sequence(namespace, store, initialized, monkeypatch, component):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        target, name = {
            "stack": (store, "_stage_stack_original"),
            "lookup": (store, "_directory_record_original"),
            "profile": (namespace, "_require_profile"),
            "guard": (lifecycle, "require_guard"),
            "guard_binding": (store, "_pin_pre_profile_guard_binding_original"),
            "directory": (namespace, "_closed_directory_verify_original"),
            "limits_eq": (type(budget.limits), "__eq__"),
        }[component]
        original, calls = getattr(target, name), []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(target, name, custom)
            descriptors, markers, samples = observe_entry(patch, store, custody)
            directory = custody.namespace.directory
            store._publish_store_directory_entry_original(stack, directory, budget)
            expected_markers, expected_samples, expected_calls = markers.copy(), samples.copy(), list(calls)
            markers.clear()
            samples.clear()
            calls.clear()
            reference_only(patch, store)
            store._publish_store_directory_entry_original(stack, directory, budget)
            assert markers == expected_markers == Counter({fd: 2 for fd in descriptors})
            assert samples == expected_samples == Counter({id(row): 2 for row in custody.held_original.resources})
            assert calls == expected_calls
        assert custody.stage is None


@pytest.mark.parametrize("component", ["lookup", "profile"])
def test_late_dispatch_uses_remaining_lookup_without_replaying_first_stack(namespace, store, initialized, monkeypatch, component):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        target, name = (store, "_directory_record_original") if component == "lookup" else (namespace, "_require_profile")
        original, changed, calls = getattr(target, name), [], []
        last = custody.held_original.resources[-1]

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            def replace(resource, count):
                if resource is last and count == 1:
                    changed.append(resource)
                    patch.setattr(target, name, custom)

            descriptors, markers, samples = observe_entry(patch, store, custody, on_identity=replace)
            store._publish_store_directory_entry_original(stack, custody.namespace.directory, budget)
            assert len(changed) == 1 and len(calls) == 1
            assert markers == Counter({fd: 2 for fd in descriptors})
            assert samples == Counter({id(row): 2 for row in custody.held_original.resources})


def test_budget_comparison_uses_need_selected_before_the_first_stack(namespace, store, initialized, monkeypatch):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        original, selected, changed = store._need, [], []
        last = custody.held_original.resources[-1]

        def late_need(condition, code):
            if code == "STAGE_STORE_SCOPE_REQUIRED":
                selected.append(code)
                pytest.fail("first publish budget comparison reselected _need after its stack operand")
            return original(condition, code)

        with monkeypatch.context() as patch:
            def replace(resource, count):
                if resource is last and count == 1:
                    changed.append(resource)
                    patch.setattr(store, "_need", late_need)

            _, _, samples = observe_entry(patch, store, custody, on_identity=replace)
            store._publish_store_directory_entry_original(stack, custody.namespace.directory, budget)
            assert changed == [last] and selected == []
            assert samples == Counter({id(row): 2 for row in custody.held_original.resources})


@pytest.mark.parametrize("owner", ["store", "stage", "budget"])
def test_existing_first_precedes_unknown_guard_getter(namespace, store, stage, initialized, monkeypatch, owner):
    first = RuntimeError("existing registered publication entry failure")
    getters = []
    with pytest.raises(RuntimeError) as outer:
        with live_entry(namespace, store, initialized) as (stack, custody, budget):
            if owner == "stage":
                scope = store._stage_scope_original(stack)
                stage._remember_first(scope, first)
            elif owner == "budget":
                store._remember_budget_first_original(budget, first)
            else:
                custody.first = first

            def unexpected(receiver):
                getters.append(receiver)
                pytest.fail("a guard descriptor must not preempt the original first error")

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.StorageGuard, "verify", property(unexpected))
                with pytest.raises(RuntimeError) as caught:
                    store._publish_store_directory_entry_original(stack, object(), budget)
            assert caught.value is first and getters == []
            raise first
    assert outer.value is first


@pytest.mark.parametrize("failure", ["cancel", "deadline", "held_first"])
def test_second_resource_sweep_keeps_real_cancellation_deadline_and_first(namespace, store, initialized, monkeypatch, failure):
    lower = store._held_io.original
    first = RuntimeError("registered held failure during the second publish entry sweep")
    expected_type = lower.SharedIoCancelledError if failure == "cancel" else RuntimeError if failure == "held_first" else lower.SharedIoError
    fired, inner = [], []
    with pytest.raises(expected_type) as outer:
        with live_entry(namespace, store, initialized) as (stack, custody, budget):
            scope_record = custody.held_original
            initial = scope_record.resources[0]
            clocks = custody.clock.started, custody.clock.deadline, budget.deadline, budget.namespace_deadline
            with monkeypatch.context() as patch:
                def inject(resource, count):
                    if resource is initial and count == 2:
                        assert not fired
                        fired.append(resource)
                        if failure == "cancel":
                            lower.request_cancellation(scope_record.cancellation)
                        elif failure == "held_first":
                            store._held_io._remember_failure(resource, first)
                        else:
                            while time.monotonic() <= custody.clock.deadline:
                                time.sleep(min(0.02, max(0.001, custody.clock.deadline - time.monotonic())))

                observe_entry(patch, store, custody, on_identity=inject)
                try:
                    store._publish_store_directory_entry_original(stack, custody.namespace.directory, budget)
                except BaseException as error:
                    inner.append(error)
                    assert (custody.clock.started, custody.clock.deadline, budget.deadline, budget.namespace_deadline) == clocks
                    raise
    assert len(fired) == 1 and inner == [outer.value]
    assert scope_record.first is outer.value
    if failure == "cancel":
        assert outer.value is scope_record.cancellation._failure
    elif failure == "held_first":
        assert outer.value is first
    else:
        assert outer.value.code == "OWNED_IO_NAMESPACE_DEADLINE"


def test_real_unregistered_and_registered_external_directories_are_rejected(namespace, store, initialized):
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        with safe._Directory(initialized.media, base=initialized.media) as unregistered:
            assert unregistered.handles and id(unregistered) not in namespace._CLOSED_DIRECTORY_RECORDS
            with pytest.raises(store.v2.StoreError) as caught:
                store._publish_store_directory_entry_original(stack, unregistered, budget)
            assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_DIRECTORY_CHANGED"
        external = custody.directories[0].original
        assert namespace._CLOSED_DIRECTORY_RECORDS[id(external)] is custody.directories[0]
        assert external.parent is None and external is not custody.namespace.directory
        with pytest.raises(store.v2.StoreError) as caught:
            store._publish_store_directory_entry_original(stack, external, budget)
        assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_DIRECTORY_CHANGED"
        assert custody.stage is None
        assert store._publish_store_directory_entry_original(stack, custody.namespace.directory, budget) is namespace._CLOSED_DIRECTORY_RECORDS[id(custody.namespace.directory)]


@pytest.mark.parametrize("payload", ["empty", "wrong_type", "too_large"])
def test_invalid_payload_does_not_create_stage_scope(namespace, store, initialized, payload):
    raw = {"empty": b"", "wrong_type": "not bytes", "too_large": b"x" * 65537}[payload]
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        with pytest.raises(store.v2.StoreError) as caught:
            store._publish_store_metadata_original(stack, custody.namespace.directory, "store-init-reservation.json", raw, budget)
        assert caught.value.code == "PERSONAL_STORE_STAGE_METADATA_SELECTION_REQUIRED"
        assert custody.stage is None and custody.first is None


def test_custom_limits_comparison_cannot_hide_real_marker_damage(namespace, store, initialized, monkeypatch):
    compared, damaged, restored, caught_errors = [], [], [], []
    with pytest.raises(namespace.NamespaceError) as outer:
        with live_entry(namespace, store, initialized) as (stack, custody, budget):
            limit_type = type(budget.limits)
            original_eq = limit_type.__eq__
            marker = custody.guard._locks[-1].directory.path / lifecycle.LOCK_NAME
            # Byte zero remains natively locked. This owned fixture marker has
            # a separate original validation byte that can be damaged/restored.
            with marker.open("r+b", buffering=0) as stream:
                stream.seek(1)
                original_byte = stream.read(1)
                assert original_byte == lifecycle._MARKER[1:2]

                def custom_eq(left, right):
                    result = original_eq(left, right)
                    if left is budget.limits:
                        compared.append((left, right))
                        if len(compared) == 2:
                            stream.seek(1)
                            assert stream.write(bytes([original_byte[0] ^ 1])) == 1
                            damaged.append(True)
                    return result

                try:
                    with monkeypatch.context() as patch:
                        patch.setattr(limit_type, "__eq__", custom_eq)
                        try:
                            store._publish_store_directory_entry_original(stack, custody.namespace.directory, budget)
                        except lifecycle.StorageLifecycleError as error:
                            caught_errors.append(error)
                            raise
                finally:
                    stream.seek(1)
                    assert stream.write(original_byte) == 1
                    stream.seek(1)
                    assert stream.read(1) == original_byte
                    restored.append(True)
    assert len(compared) == 2 and damaged == restored == [True]
    assert len(caught_errors) == 1
    assert caught_errors[0].code == outer.value.code == "STORAGE_LOCK_INVALID"
    assert custody.stage is None
