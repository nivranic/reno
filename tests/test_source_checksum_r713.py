"""Source-batch checksums retain original acquisition, late gates and short-circuit."""
from collections import Counter
import hashlib
from pathlib import Path
import time

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_source_directory_batch import (  # noqa: F401
    empty_stack,
    sources,
    test_one_directory_keeps_all_real_files_hashes_reads_charges_and_close,
    test_next_batch_acquires_new_directory_and_independent_file_leases,
)
from tests.test_stage_effect_profile_r709 import (  # noqa: F401
    test_original_dispatch_metadata_publication_reads_back_and_closes,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r713"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r713.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r713/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r713.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f776b3f3e31df7b7b9bf0993ee627ec71607c3f0804ac8f239bd5b7bb652beaf"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = import_historical_snapshot("authority_runtime_origin_r713.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


def _observe_checksum(patch, store, custody, *, on_identity=None):
    leaf, sha256 = store._held_io.original._identities, hashlib.sha256
    metadata_identities, all_identities, hashes = Counter(), Counter(), []

    def observe_identity(resource, **kwargs):
        result = leaf(resource, **kwargs)
        if resource.scope is custody.scope:
            all_identities[id(resource)] += 1
            selected = next((row for row in custody.pins if row.lease is resource.lease), None)
            if selected is not None:
                # Acquisition's initial read and second FileHold verification
                # precede creation of this real Store Pin wrapper.
                metadata_identities[id(resource)] += 1
                if on_identity is not None:
                    on_identity(selected, resource, metadata_identities[id(resource)])
        return result

    def observe_hash(*args, **kwargs):
        if args and type(args[0]) is bytes:
            selected = next((row for row in custody.held_original.resources
                             if row.payload is args[0]), None)
            if selected is not None:
                pin_record = next((row for row in custody.pins if row.lease is selected.lease), None)
                if pin_record is not None:
                    hashes.append((pin_record, selected, metadata_identities[id(selected)]))
        return sha256(*args, **kwargs)

    # Neither leaf participates in the new fixed callable admission. In
    # particular Store._sha itself stays original throughout the fast cases.
    patch.setattr(store._held_io.original, "_identities", observe_identity)
    patch.setattr(hashlib, "sha256", observe_hash)
    return metadata_identities, all_identities, hashes


def _disable_only_new_checksum(patch, store):
    original = store._source_checksum_binding_original

    def transparent(pin):
        return original(pin)

    patch.setattr(store, "_source_checksum_binding_original", transparent)


def _assert_three_genuine_sources(store, custody, budget):
    assert len(custody.directories) == 1
    assert len(custody.pins) == len(custody.held_original.resources) == 3
    assert len({id(row.lease) for row in custody.pins}) == 3
    assert len({row.fd for row in custody.held_original.resources}) == 3
    total = 0
    for (module, expected), pin, resource in zip(sources(store), custody.pins,
                                               custody.held_original.resources, strict=True):
        raw = Path(module.__file__).read_bytes()
        total += len(raw)
        assert pin.kind == "source" and resource.resource_kind == "filehold"
        assert pin.directory is resource.directory is custody.directories[0].original
        assert pin.scope is resource.scope is custody.scope and pin.lease is resource.lease
        assert pin.name == Path(module.__file__).name and resource.payload == raw
        assert resource.sha256 == hashlib.sha256(raw).hexdigest() == expected
        assert resource.observed_bytes == 2 * len(raw)
    assert budget.bytes == 0
    return total


def test_batch_checksum_saves_one_target_identity_without_reducing_real_reads_or_close(
        namespace, store, initialized, monkeypatch):
    physical_counts = []
    for reference in (False, True):
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            with monkeypatch.context() as patch:
                _, identities, hashes = _observe_checksum(patch, store, custody)
                if reference:
                    _disable_only_new_checksum(patch, store)
                store._source_pins(stack, budget)
                assert len(hashes) == 3
                assert [row[0] for row in hashes] == list(custody.pins)
                assert [count for _, _, count in hashes] == [4 if reference else 3] * 3
                physical_counts.append(tuple(identities[id(resource)]
                                             for resource in custody.held_original.resources))
            total = _assert_three_genuine_sources(store, custody, budget)
        assert budget.bytes == total
        assert all(row.observed_bytes == 3 * row.byte_size for row in custody.held_original.resources)
    # Complete second Store sweeps remain; only each checksum's extra target
    # producer identity is absent. Acquisition and later sources are fresh.
    assert physical_counts[1] == tuple(value + 1 for value in physical_counts[0])


@pytest.mark.parametrize("component", ["raw", "sha256", "observation"])
def test_custom_property_or_observation_keeps_original_two_observations(
        namespace, store, initialized, monkeypatch, component):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        selected = store._ClosedStorePin
        original = (selected._observation if component == "observation"
                    else selected.__dict__[component].fget)
        calls = []

        def custom(pin):
            calls.append(pin)
            return original(pin)

        with monkeypatch.context() as patch:
            _, _, hashes = _observe_checksum(patch, store, custody)
            patch.setattr(selected, "_observation" if component == "observation" else component,
                          custom if component == "observation" else property(custom))
            store._source_pins(stack, budget)
            assert len(hashes) == 3 and [count for _, _, count in hashes] == [4] * 3
            per_pin = 2 if component == "observation" else 1
            assert calls == [row.original for row in custody.pins for _ in range(per_pin)]
        _assert_three_genuine_sources(store, custody, budget)


@pytest.mark.parametrize("component", ["single", "sweep"])
def test_supported_directory_custom_falls_back_to_original_target_identity_and_call_sequence(
        namespace, store, initialized, monkeypatch, component):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        # The checksum target comes from the actual default three-file batch;
        # this test only repeats its metadata expression, without acquiring a
        # fabricated Pin or changing any original payload/guard/owner.
        store._source_pins(stack, budget)
        pin_record = custody.pins[1]
        resource = custody.held_original.resources[1]
        pin, expected = pin_record.original, store.V2_SOURCE_SHA256
        name = "_closed_directory_verify_original" if component == "single" else "_closed_directory_verify_sweep_original"
        original, calls = getattr(namespace, name), []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        before = tuple(row.observed_bytes for row in custody.held_original.resources), budget.bytes
        with monkeypatch.context() as patch:
            metadata, physical, hashes = _observe_checksum(patch, store, custody)
            patch.setattr(namespace, name, custom)
            assert store._source_pin_checksum_original(pin, expected) is True
            assert [(row, count) for row, _, count in hashes] == [(pin_record, 4)]
            first_physical, first_calls = physical.copy(), list(calls)
            if component == "single":
                assert first_calls
                assert all(len(args) == 1 and type(args[0]) is namespace._ClosedDirectoryRecord
                           and kwargs == {} for args, kwargs in first_calls)
            metadata.clear()
            physical.clear()
            hashes.clear()
            calls.clear()
            _disable_only_new_checksum(patch, store)
            assert store._source_pin_checksum_original(pin, expected) is True
            assert [(row, count) for row, _, count in hashes] == [(pin_record, 4)]
            assert physical == first_physical and calls == first_calls
        assert (tuple(row.observed_bytes for row in custody.held_original.resources), budget.bytes) == before
        assert resource.failure is custody.held_original.first is custody.first is None


@pytest.mark.parametrize("also_cancel", [False, True])
def test_wrong_first_sha_short_circuits_raw_hash_and_later_cancellation(
        namespace, store, initialized, monkeypatch, also_cancel):
    with pytest.raises(store.v2.StoreError) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            changed = []

            def after_first_observation(pin, resource, count):
                if count == 2 and not changed:
                    changed.append(pin)
                    if also_cancel:
                        store._held_io.original.request_cancellation(custody.cancellation)

            with monkeypatch.context() as patch:
                patch.setattr(store, "NAMESPACE_SOURCE_SHA256", "0" * 64)
                metadata, _, hashes = _observe_checksum(patch, store, custody,
                                                       on_identity=after_first_observation)
                with pytest.raises(store.v2.StoreError) as caught:
                    store._source_pins(stack, budget)
            assert caught.value.code == "PERSONAL_STORE_CODEC_SOURCE_BINDING_CHANGED"
            assert len(custody.pins) == len(custody.held_original.resources) == 1
            pin, resource = custody.pins[0], custody.held_original.resources[0]
            assert changed == [pin] and metadata[id(resource)] == 2 and hashes == []
            assert resource.observed_bytes == 2 * resource.byte_size and budget.bytes == 0
            assert custody.first is store._BUDGET_RECORDS[id(budget)].first is caught.value
            assert resource.failure is custody.held_original.first is None
            raise caught.value
    assert outer.value is caught.value


@pytest.mark.parametrize("change_at,component", [(2, "raw"), (3, "raw"), (3, "observation"), (3, "sha_function")])
def test_late_dispatch_keeps_only_the_original_remaining_raw_chain(
        namespace, store, initialized, monkeypatch, change_at, component):
    with empty_stack(namespace, store, initialized) as (stack, custody, budget):
        selected = store._ClosedStorePin
        original = (selected.raw.fget if component == "raw" else store._sha
                    if component == "sha_function" else selected._observation)
        changed, calls = [], []

        def custom(pin):
            calls.append(pin)
            return original(pin)

        with monkeypatch.context() as patch:
            def change(pin, resource, count):
                if len(custody.pins) == 1 and count == change_at and not changed:
                    if component == "sha_function":
                        patch.setattr(store, "_sha", custom)
                    else:
                        patch.setattr(selected, component if component == "raw" else "_observation",
                                      property(custom) if component == "raw" else custom)
                    changed.append(pin)

            _, _, hashes = _observe_checksum(patch, store, custody, on_identity=change)
            store._source_pins(stack, budget)
            assert changed == [custody.pins[0]]
            assert [count for _, _, count in hashes] == [4] * 3
            if component == "sha_function":
                # The original expression loads _sha before evaluating raw.
                # A replacement during that raw's second stack must affect
                # only the following source expressions.
                assert len(calls) == 2
                assert all(raw is row.payload for raw, row in zip(calls, custody.held_original.resources[1:], strict=True))
            else:
                first_calls = 1 if change_at == 2 else 0
                later_calls = 1 if component == "raw" else 2
                expected = [custody.pins[0].original] * first_calls
                expected += [row.original for row in custody.pins[1:] for _ in range(later_calls)]
                assert calls == expected
        _assert_three_genuine_sources(store, custody, budget)


@pytest.mark.parametrize("failure", ["cancel", "deadline", "held_first"])
def test_second_stack_late_gate_rejects_before_raw_hash_with_original_failure_bookkeeping(
        namespace, store, initialized, monkeypatch, failure):
    lower = store._held_io.original
    expected_type = (lower.SharedIoCancelledError if failure == "cancel"
                     else lower.SharedIoError if failure == "deadline" else RuntimeError)
    first = RuntimeError("original held first before checksum raw hash")
    with pytest.raises(expected_type) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            changed = []
            clocks = custody.clock.started, custody.clock.deadline, budget.deadline, budget.namespace_deadline

            def change(pin, resource, count):
                if count == 3 and not changed:
                    changed.append(pin)
                    if failure == "cancel":
                        lower.request_cancellation(custody.cancellation)
                    elif failure == "held_first":
                        custody.held_original.first = first
                    else:
                        deadline = resource.clock_contract[2]
                        remaining = deadline - time.monotonic()
                        assert 0 < remaining <= 30.0
                        while time.monotonic() <= deadline:
                            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))

            with monkeypatch.context() as patch:
                metadata, _, hashes = _observe_checksum(patch, store, custody, on_identity=change)
                with pytest.raises(expected_type) as caught:
                    store._source_pins(stack, budget)
            assert len(custody.pins) == len(custody.held_original.resources) == 1
            pin, resource = custody.pins[0], custody.held_original.resources[0]
            assert changed == [pin] and metadata[id(resource)] == 3 and hashes == []
            assert resource.observed_bytes == 2 * resource.byte_size and budget.bytes == 0
            if failure == "cancel":
                assert caught.value is custody.cancellation._failure
            elif failure == "deadline":
                assert caught.value.code == "OWNED_IO_NAMESPACE_DEADLINE"
            else:
                assert caught.value is first
            assert (custody.clock.started, custody.clock.deadline, budget.deadline, budget.namespace_deadline) == clocks
            assert resource.failure is custody.held_original.first is caught.value
            assert custody.first is store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


def test_late_actual_stack_scope_change_is_rejected_by_second_full_stack_before_hash(
        namespace, store, initialized, monkeypatch):
    with pytest.raises(store.v2.StoreError) as outer:
        with empty_stack(namespace, store, initialized) as (stack, custody, budget):
            original_scope = stack.scope
            other_scope = custody.namespace.stack.scope
            assert other_scope is not original_scope and id(other_scope) in store._held_io._SCOPES
            changed = []

            def change(pin, resource, count):
                if count == 2 and not changed:
                    stack.scope = other_scope
                    changed.append(pin)

            try:
                with monkeypatch.context() as patch:
                    metadata, _, hashes = _observe_checksum(patch, store, custody, on_identity=change)
                    with pytest.raises(store.v2.StoreError) as caught:
                        store._source_pins(stack, budget)
            finally:
                stack.scope = original_scope
            assert caught.value.code == "PERSONAL_STORE_FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED"
            assert len(custody.pins) == len(custody.held_original.resources) == 1
            pin, resource = custody.pins[0], custody.held_original.resources[0]
            assert changed == [pin] and metadata[id(resource)] == 2 and hashes == []
            assert resource.observed_bytes == 2 * resource.byte_size and budget.bytes == 0
            assert custody.first is store._BUDGET_RECORDS[id(budget)].first is caught.value
            assert resource.failure is custody.held_original.first is None
            raise caught.value
    assert outer.value is caught.value
