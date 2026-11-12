"""Real Pin verification keeps fresh reads, accounting and failure boundaries."""
from collections import Counter
from contextlib import contextmanager
import hashlib
from pathlib import Path
import time

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r695"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r695.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r695/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r695.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "c6745b971c89fe4ef7838ef089c7bf29479ec501d757241e60a296bdeeefb3dd"
    assert module.ns is namespace
    return module


@contextmanager
def live_pin(namespace, store, profile, kind="pin", *, rebind=False):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            stack_budget = store._Budget(owner)
            budget = store._Budget(owner) if rebind else stack_budget
            acquisition = store._budget_record_original(stack_budget).acquisition
            with store._ClosingStack(stack_budget) as stack:
                store._source_pins(stack, stack_budget)
                custody = store._STACK_RECORDS[id(stack)]
                pin = custody.pins[0].original if kind == "source" else stack.enter_context(
                    store._PinSelection(acquisition.directory, acquisition.metadata[1].path.name, 65536, stack_budget))
                record = store._PIN_RECORDS[id(pin)]
                resource = store._held_io._resource_original(record.scope, record.lease, store._held_io._FileLease)
                if rebind:
                    pin.budget = budget
                    assert record.budget is budget and record.budget is not custody.budget
                    if kind == "pin":
                        assert resource.budget.actual is budget
                assert store._pin_stack_identity_binding_original(record) is resource
                yield pin, record, resource, custody, budget
            assert custody.closed and id(stack) not in store._STACK_RECORDS
            assert all(p.closed and id(p.original) not in store._PIN_RECORDS for p in custody.pins)
            assert all(r.finished and r.native_close_returned for r in custody.held_original.resources)
        for value in (stack_budget, budget):
            assert id(value) not in store._BUDGET_RECORDS and id(value) not in store._RETAINED_BUDGETS


@pytest.mark.parametrize("kind,rebind", [("pin", False), ("pin", True), ("source", False), ("source", True)])
def test_each_verify_rereads_and_charges_actual_budget_then_closes(namespace, store, initialized, monkeypatch, kind, rebind):
    with live_pin(namespace, store, initialized, kind, rebind=rebind) as (pin, record, resource, custody, budget):
        leaf, seen = store._held_io.original._identities, Counter()

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        expected = Counter({id(row): 2 if kind == "source" else 1 for row in custody.held_original.resources})
        expected[id(resource)] = 6 if kind == "source" else 4
        charged, observed, other_charged = budget.bytes, resource.observed_bytes, custody.budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(store._held_io.original, "_identities", observe)
            pin.verify()
            assert seen == expected
            assert resource.observed_bytes - observed == budget.bytes - charged == resource.byte_size
            pin.verify()
            assert seen == expected + expected
        assert resource.observed_bytes - observed == budget.bytes - charged == 2 * resource.byte_size
        if rebind:
            assert custody.budget.bytes == other_charged
        before_close = budget.bytes
        sizes = {id(row.lease): row.byte_size for row in custody.held_original.resources}
        expected_close = sum(sizes[id(row.lease)] * (1 if row.kind == "source" else 2)
                             for row in custody.pins if row.budget is budget)
    # Sources close with one wrapper rehash; business pins additionally retain
    # the held-scope's original normal rehash before native close.
    assert budget.bytes - before_close == expected_close
    assert resource.observed_bytes - observed == (3 if kind == "source" else 4) * resource.byte_size


@pytest.mark.parametrize("component", ["record", "observation_method", "observation_helper", "held_observation",
                                        "budget_record", "budget_check", "verify_file", "directories", "identity"])
def test_custom_dispatch_keeps_remaining_original_calls_and_arguments(namespace, store, initialized, monkeypatch, component):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        module, name, args = {
            "record": (store, "_pin_record_original", (pin,)),
            "observation_method": (store._ClosedStorePin, "_observation", (pin,)),
            "observation_helper": (store, "_pin_observation_original", (pin,)),
            "held_observation": (store._held_io, "_file_observation_original", (record.scope, record.lease)),
            "budget_record": (store, "_budget_record_original", (budget,)),
            "budget_check": (store._Budget, "check", (budget,)),
            "verify_file": (store._held_io, "_verify_file_original", (record.scope, record.lease)),
            "directories": (store, "_store_directories_verify_original", (custody,)),
            "identity": (store._held_io, "_verify_identity_record", (resource,)),
        }[component]
        original, leaf = getattr(module, name), store._held_io.original._identities
        calls, seen = [], Counter()

        def custom(*values, **kwargs):
            calls.append((values, kwargs))
            return original(*values, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        charged, observed = budget.bytes, resource.observed_bytes
        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(store._held_io.original, "_identities", observe)
            pin.verify()
        expected = Counter({id(row): 2 for row in custody.held_original.resources})
        expected[id(resource)] = 7 if component in {"record", "held_observation"} else 5
        assert seen == expected
        if component == "identity":
            selected_calls = [(values, kwargs) for values, kwargs in calls if values[0].scope is custody.scope]
            assert Counter(id(values[0]) for values, _ in selected_calls) == expected
            assert all(len(values) == 1 and kwargs == {} for values, kwargs in selected_calls)
        else:
            assert calls and all(value == (args, {}) for value in calls)
        if component not in {"record", "budget_record", "directories", "identity"}:
            assert len(calls) == 1
        assert budget.bytes - charged == resource.observed_bytes - observed == resource.byte_size


def test_dispatch_changed_after_first_stack_uses_remaining_chain_without_replay(namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        leaf, original = store._held_io.original._identities, store._ClosedStorePin._observation
        seen, calls = Counter(), []

        def custom(value):
            calls.append(value)
            return original(value)

        with monkeypatch.context() as patch:
            def change(row, **kwargs):
                result = leaf(row, **kwargs)
                if row.scope is custody.scope:
                    seen[id(row)] += 1
                if row is resource and seen[id(row)] == 1:
                    patch.setattr(store._ClosedStorePin, "_observation", custom)
                return result

            patch.setattr(store._held_io.original, "_identities", change)
            observed, charged = resource.observed_bytes, budget.bytes
            pin.verify()
        expected = Counter({id(row): 2 for row in custody.held_original.resources})
        expected[id(resource)] = 5
        assert seen == expected and calls == [pin]
        assert resource.observed_bytes - observed == budget.bytes - charged == resource.byte_size


def test_cancel_after_pre_observation_rejects_before_read_and_keeps_both_first_errors(namespace, store, initialized, monkeypatch):
    lower = store._held_io.original
    with pytest.raises(lower.SharedIoCancelledError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, visits = store._held_io.original._identities, []

            def cancel(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is resource:
                    visits.append(row)
                    if len(visits) == 2:
                        lower.request_cancellation(custody.cancellation)
                return result

            before = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                patch.setattr(store._held_io.original, "_identities", cancel)
                with pytest.raises(lower.SharedIoCancelledError) as caught:
                    pin.verify()
            assert len(visits) == 2 and (resource.observed_bytes, budget.bytes) == before
            assert caught.value is custody.cancellation._failure
            assert resource.failure is custody.held_original.first is caught.value
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


def test_real_deadline_after_pre_observation_rejects_before_read_and_keeps_first(namespace, store, initialized, monkeypatch):
    lower = store._held_io.original
    with pytest.raises(lower.SharedIoError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, visits = store._held_io.original._identities, []
            clocks = resource.clock_contract, budget.deadline, budget.namespace_deadline

            def expire(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is resource:
                    visits.append(row)
                    if len(visits) == 2:
                        # This ordinary Pin verifier has no runtime or init
                        # observation. Its remaining directory identity/verify
                        # leaves do physical work without a deadline check.
                        assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
                        assert store.pf._init_directory_context._CURRENT.get() is None
                        deadline = resource.clock_contract[2]
                        assert 0 < deadline - time.monotonic() <= 30.0
                        while time.monotonic() <= deadline:
                            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
                return result

            before = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                patch.setattr(store._held_io.original, "_identities", expire)
                with pytest.raises(lower.SharedIoError) as caught:
                    pin.verify()
            # After the DTO the retained held poll checks namespace deadline
            # before the business-pin deadline, then remembers the same error.
            assert caught.value.code == "OWNED_IO_NAMESPACE_DEADLINE"
            assert len(visits) == 2 and (resource.observed_bytes, budget.bytes) == before
            assert (resource.clock_contract, budget.deadline, budget.namespace_deadline) == clocks
            assert resource.failure is custody.held_original.first is caught.value
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


@pytest.mark.parametrize("kind", ["pin", "source"])
def test_wrong_expected_sha_rejects_after_real_reread_with_original_charge_boundary(namespace, store, initialized, kind):
    lower = store._held_io.original
    with pytest.raises(lower.SharedIoError) as outer:
        with live_pin(namespace, store, initialized, kind) as (pin, record, resource, custody, budget):
            expected, observed, charged = resource.sha256, resource.observed_bytes, budget.bytes
            try:
                resource.sha256 = "0" * 64 if expected != "0" * 64 else "1" * 64
                with pytest.raises(lower.SharedIoError) as caught:
                    pin.verify()
            finally:
                resource.sha256 = expected
            assert caught.value.code == "OWNED_IO_ORIGINAL_FILE_CHANGED"
            assert resource.observed_bytes - observed == resource.byte_size
            assert budget.bytes - charged == (resource.byte_size if kind == "pin" else 0)
            assert resource.failure is custody.held_original.first is caught.value
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


def test_source_read_then_cancel_still_runs_full_post_gate_without_charging(namespace, store, initialized, monkeypatch):
    lower = store._held_io.original
    with pytest.raises(lower.SharedIoCancelledError) as outer:
        with live_pin(namespace, store, initialized, "source") as (pin, record, resource, custody, budget):
            read, calls = store._held_io._read_owned, []

            def cancel_after_read(row, *, collect):
                result = read(row, collect=collect)
                if row is resource:
                    calls.append((row, collect, result))
                    lower.request_cancellation(custody.cancellation)
                return result

            observed, charged = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                patch.setattr(store._held_io, "_read_owned", cancel_after_read)
                with pytest.raises(lower.SharedIoCancelledError) as caught:
                    pin.verify()
            assert len(calls) == 1 and calls[0][:2] == (resource, False)
            assert calls[0][2].sha256 == resource.sha256
            assert resource.observed_bytes - observed == resource.byte_size and budget.bytes == charged
            assert caught.value is custody.cancellation._failure
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            # Scope cancellation precedes the post-scope's resource loop, so
            # neither the successful held read nor that loop remembers a file failure.
            assert resource.failure is custody.held_original.first is None
            raise caught.value
    assert outer.value is caught.value


@pytest.mark.parametrize("mutation", ["lease", "descriptor"])
def test_pre_observation_late_change_rejects_without_read_or_replay(namespace, store, initialized, monkeypatch, mutation):
    with pytest.raises(store.v2.StoreError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, original_lease = store._held_io.original._identities, record.lease
            other = next(row for row in custody.held_original.resources if row is not resource)
            seen, custom_calls = Counter(), []

            def unexpected(value):
                custom_calls.append(value)
                pytest.fail("late descriptor was executed")

            before = resource.observed_bytes, budget.bytes
            try:
                with monkeypatch.context() as patch:
                    def change(row, **kwargs):
                        result = leaf(row, **kwargs)
                        if row.scope is custody.scope:
                            seen[id(row)] += 1
                        if row is resource and seen[id(row)] == 2:
                            if mutation == "lease":
                                record.lease = other.lease
                            else:
                                patch.setattr(store._ClosedStorePin, "_observation", unexpected)
                        return result

                    patch.setattr(store._held_io.original, "_identities", change)
                    with pytest.raises(store.v2.StoreError) as caught:
                        pin.verify()
            finally:
                record.lease = original_lease
            expected = Counter({id(row): 1 for row in custody.held_original.resources})
            expected[id(resource)] = 2
            assert seen == expected and custom_calls == []
            assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED"
            assert (resource.observed_bytes, budget.bytes) == before
            failed = other if mutation == "lease" else resource
            assert failed.failure is custody.held_original.first is caught.value
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


@pytest.mark.parametrize("mirror", ["scope", "native_owner"])
def test_changed_stack_mirror_after_budget_check_rejects_before_observation(namespace, store, initialized, monkeypatch, mirror):
    with pytest.raises(store.v2.StoreError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            original = store.v2._Budget.check
            previous, calls = getattr(record.stack, mirror), []

            def change(value):
                result = original(value)
                if value is budget:
                    calls.append(value)
                    setattr(record.stack, mirror, object())
                return result

            before = resource.observed_bytes, budget.bytes
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(store.v2._Budget, "check", change)
                    with pytest.raises(store.v2.StoreError) as caught:
                        pin.verify()
            finally:
                setattr(record.stack, mirror, previous)
            assert caught.value.code == "PERSONAL_STORE_FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED"
            assert calls == [budget] and (resource.observed_bytes, budget.bytes) == before
            assert resource.failure is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(budget)].first is caught.value
            raise caught.value
    assert outer.value is caught.value


def test_initial_record_failure_stays_outside_budget_failure_ledger(namespace, store, initialized):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        before = resource.observed_bytes, budget.bytes
        try:
            record.closed = True
            with pytest.raises(store.v2.StoreError) as caught:
                pin.verify()
        finally:
            record.closed = False
        assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED"
        assert (resource.observed_bytes, budget.bytes) == before
        assert resource.failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(budget)].first is None


def test_custom_descriptor_getter_does_not_precede_initial_record_failure(namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        getters = []

        class ObservationDescriptor:
            def __get__(self, instance, owner):
                getters.append((instance, owner))
                pytest.fail("custom getter preceded the original Pin-record failure")

        before = resource.observed_bytes, budget.bytes
        try:
            with monkeypatch.context() as patch:
                patch.setattr(store._ClosedStorePin, "_observation", ObservationDescriptor())
                record.closed = True
                with pytest.raises(store.v2.StoreError) as caught:
                    pin.verify()
        finally:
            record.closed = False
        assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED"
        assert getters == [] and (resource.observed_bytes, budget.bytes) == before
        assert resource.failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(budget)].first is None


def test_store_first_during_observation_precedes_extra_binding_gate(namespace, store, initialized, monkeypatch):
    first = RuntimeError("store first during original pre-read observation")
    with pytest.raises(RuntimeError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, visits, custom_calls = store._held_io.original._identities, [], []

            def unexpected(value):
                custom_calls.append(value)
                pytest.fail("late descriptor was executed before Store first")

            before = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                def change(row, **kwargs):
                    result = leaf(row, **kwargs)
                    if row is resource:
                        visits.append(row)
                        if len(visits) == 2:
                            custody.first = first
                            patch.setattr(store._ClosedStorePin, "_observation", unexpected)
                    return result

                patch.setattr(store._held_io.original, "_identities", change)
                with pytest.raises(RuntimeError) as caught:
                    pin.verify()
            assert caught.value is custody.first is first
            assert len(visits) == 2 and custom_calls == []
            assert (resource.observed_bytes, budget.bytes) == before
            assert store._BUDGET_RECORDS[id(budget)].first is first
            assert resource.failure is custody.held_original.first is None
            raise caught.value
    assert outer.value is first
