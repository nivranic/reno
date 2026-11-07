"""Actual Store Pin-record queries preserve late gates and identity coverage."""
from collections import Counter
import hashlib
import importlib
from pathlib import Path
import time

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_pin_observation import (  # noqa: F401
    live_pin,
    test_custom_helpers_keep_original_chain,
    test_late_custom_dispatch_keeps_remaining_chain_without_stack_replay,
    test_final_observer_rejects_identity_changed_after_stack_and_preserves_first,
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r679"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r679.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r679/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r679.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "57a6306288ddf6ba9f8b2d36cf055504c3a246053f7580a1e0d51b419271ad63"
    assert module.ns is namespace
    return module


@pytest.mark.parametrize("kind", ["source", "pin"])
def test_fixed_record_checks_every_resource_once_and_keeps_observation_and_charges(namespace, store, initialized, monkeypatch, kind):
    with live_pin(namespace, store, initialized, kind) as (pin, record, resource, custody, budget):
        original = store._held_io._verify_identity_record
        seen = Counter()
        expected = Counter({id(row): 1 for row in custody.held_original.resources})

        def observe(row, **kwargs):
            result = original(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        before = budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            assert store._pin_stack_identity_binding_original(record) is resource
            assert store._pin_record_original(pin) is record
            assert seen == expected
            assert pin.name == record.name
            assert seen == expected + expected
            seen.clear()
            assert pin.raw == resource.payload
            observation = expected.copy()
            observation[id(resource)] += 1
            assert seen == observation
        assert budget.bytes == before
        observed = resource.observed_bytes
        pin.verify()
        assert resource.observed_bytes - observed == resource.byte_size
        assert budget.bytes - before == resource.byte_size


@pytest.mark.parametrize("target", ["stack", "scope", "specific", "lookup"])
def test_custom_pin_check_components_keep_original_full_chain(namespace, store, initialized, monkeypatch, target):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        module, name = {
            "stack": (store, "_stage_stack_original"),
            "scope": (store._held_io, "_verify_scope_identity_original"),
            "specific": (store._held_io, "_verify_file_identity_original"),
            "lookup": (store._held_io, "_resource_original"),
        }[target]
        original, leaf = getattr(module, name), store._held_io._verify_identity_record
        calls, seen = [], Counter()

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        expected_args = {
            "stack": (record.stack,), "scope": (record.scope,),
            "specific": (record.scope, record.lease),
            "lookup": (record.scope, record.lease, store._held_io._FileLease),
        }[target]
        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            assert store._pin_stack_identity_binding_original(record) is None
            assert store._pin_record_original(pin) is record
            expected = Counter({id(row): 1 for row in custody.held_original.resources})
            expected[id(resource)] += 1
            assert seen == expected and calls == [(expected_args, {})]


def test_closed_pin_and_missing_original_lease_still_reject(namespace, store, initialized):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        try:
            record.closed = True
            with pytest.raises(store.v2.StoreError) as caught:
                store._pin_record_original(pin)
            assert caught.value.code == "NATIVE_ORIGINAL_PIN_REQUIRED"
        finally:
            record.closed = False
        original_lease = record.lease
        try:
            record.lease = store._held_io._FileLease()
            assert store._pin_stack_identity_binding_original(record) is None
            with pytest.raises(store._held_io.original.SharedIoError) as caught:
                store._pin_record_original(pin)
            assert caught.value.code == "OWNED_IO_ORIGINAL_LEASE_REQUIRED"
        finally:
            record.lease = original_lease
        assert custody.held_original.first is None
        assert store._pin_record_original(pin) is record


@pytest.mark.parametrize("fault", ["deadline", "cancellation"])
def test_late_original_poll_rejects_and_records_same_first(namespace, store, initialized, monkeypatch, fault):
    lower = store._held_io.original
    error_type = lower.SharedIoError if fault == "deadline" else lower.SharedIoCancelledError
    failures, injected = [], []
    with pytest.raises(error_type) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            original = store._store_directories_verify_original
            clocks = (budget.namespace_deadline, budget.deadline, resource.clock_contract)

            def after_directories(value):
                result = original(value)
                assert value is custody and not injected
                if fault == "deadline":
                    remaining = budget.namespace_deadline + 0.02 - time.monotonic()
                    assert 0 < remaining <= 30
                    time.sleep(remaining)  # Actual original deadline, never change its clock or limit.
                    assert time.monotonic() > budget.namespace_deadline
                else:
                    lower.request_cancellation(custody.cancellation)
                injected.append(value)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(store, "_store_directories_verify_original", after_directories)
                assert store._pin_stack_identity_binding_original(record) is resource
                with pytest.raises(error_type) as caught:
                    store._pin_record_original(pin)
            assert len(injected) == 1
            assert custody.held_original.first is resource.failure is caught.value
            assert (budget.namespace_deadline, budget.deadline, resource.clock_contract) == clocks
            if fault == "deadline":
                assert caught.value.code == "OWNED_IO_NAMESPACE_DEADLINE"
            else:
                assert caught.value is custody.cancellation._failure
            failures.append(caught.value)
            with pytest.raises(error_type) as repeated:
                store._pin_record_original(pin)
            assert repeated.value is caught.value
            raise caught.value
    assert failures == [outer.value]


@pytest.mark.parametrize("change", ["dispatch", "lease"])
def test_late_binding_change_rejects_without_replay_and_preserves_first(namespace, store, initialized, monkeypatch, change):
    errors, bodies, custom_calls = [], [], []
    with pytest.raises(store.v2.StoreError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            original = store._store_directories_verify_original
            specific = store._held_io._verify_file_identity_original
            original_lease = record.lease
            other = next(row for row in custody.held_original.resources if row is not resource)
            try:
                with monkeypatch.context() as patch:
                    def custom(*args):
                        custom_calls.append(args)
                        return specific(*args)

                    def change_after_body(value):
                        result = original(value)
                        bodies.append(value)
                        if change == "dispatch":
                            patch.setattr(store._held_io, "_verify_file_identity_original", custom)
                        else:
                            record.lease = other.lease
                        return result

                    patch.setattr(store, "_store_directories_verify_original", change_after_body)
                    with pytest.raises(store.v2.StoreError) as caught:
                        store._pin_record_original(pin)
            finally:
                record.lease = original_lease
            assert caught.value.code == "NATIVE_ORIGINAL_PIN_REQUIRED"
            assert bodies == [custody] and custom_calls == []
            failed_resource = resource if change == "dispatch" else other
            assert failed_resource.failure is custody.held_original.first is caught.value
            errors.append(caught.value)
            with pytest.raises(store.v2.StoreError) as repeated:
                store._pin_record_original(pin)
            assert repeated.value is caught.value
            raise caught.value
    assert errors == [outer.value]
