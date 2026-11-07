"""Pin tail guard sharing retains actual bindings and original fallback paths."""
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
    test_each_verify_rereads_and_charges_actual_budget_then_closes,
    test_custom_dispatch_keeps_remaining_original_calls_and_arguments,
    test_dispatch_changed_after_first_stack_uses_remaining_chain_without_replay,
    test_cancel_after_pre_observation_rejects_before_read_and_keeps_both_first_errors,
    test_real_deadline_after_pre_observation_rejects_before_read_and_keeps_first,
    test_wrong_expected_sha_rejects_after_real_reread_with_original_charge_boundary,
    test_source_read_then_cancel_still_runs_full_post_gate_without_charging,
    test_pre_observation_late_change_rejects_without_read_or_replay,
    test_changed_stack_mirror_after_budget_check_rejects_before_observation,
    test_initial_record_failure_stays_outside_budget_failure_ledger,
    test_custom_descriptor_getter_does_not_precede_initial_record_failure,
    test_store_first_during_observation_precedes_extra_binding_gate,
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r697"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r697.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r697/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r697.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "92d19bf5b406b7a627c92649ea05b5bd7a6a5dcb1a1b7848f78f28c8cba59065"
    assert module.ns is namespace
    return module


def test_tail_shares_exactly_one_guard_marker_pass_with_fresh_rereads(namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        guard = custody.guard
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        assert len(descriptors) == len(guard._locks) > 0
        original, reads = lifecycle.os.read, Counter()

        def observe(descriptor, amount):
            raw = original(descriptor, amount)
            if descriptor in descriptors:
                assert amount == len(lifecycle._MARKER) + 1 and raw == lifecycle._MARKER
                reads[descriptor] += 1
            return raw

        charged, observed = budget.bytes, resource.observed_bytes
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.os, "read", observe)
            pin.verify()
            current = reads.copy()
            reads.clear()
            # A second genuine verification reinstates only the original
            # r695 complete tail gate. No read, deadline or fixture is skipped.
            patch.setattr(store, "_pin_verify_tail_state_original", store._pin_verify_stack_state_original)
            pin.verify()
            original_tail = reads.copy()
        assert current == Counter({descriptor: 3 for descriptor in descriptors})
        assert original_tail == Counter({descriptor: 4 for descriptor in descriptors})
        assert original_tail - current == Counter({descriptor: 1 for descriptor in descriptors})
        assert resource.observed_bytes - observed == budget.bytes - charged == 2 * resource.byte_size
        assert resource.failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(budget)].first is None


@pytest.mark.parametrize("mutation,code", [
    ("active", "STORAGE_GUARD_REQUIRED"),
    ("owner", "STORAGE_GUARD_CONTEXT"),
    ("current", "STORAGE_GUARD_REQUIRED"),
    ("member", "STORAGE_PROFILE_MISMATCH"),
    ("mode", "PERSONAL_STORE_NATIVE_ORIGINAL_GUARD_CHANGED"),
    ("profiles", "PERSONAL_STORE_NATIVE_ORIGINAL_GUARD_CHANGED"),
])
def test_late_actual_guard_state_rejects_before_payload_read(namespace, store, initialized, monkeypatch, mutation, code):
    error_type = store.v2.StoreError if mutation in {"mode", "profiles"} else lifecycle.StorageLifecycleError
    outer_type = namespace.NamespaceError if error_type is lifecycle.StorageLifecycleError else error_type
    with pytest.raises(outer_type) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            guard, leaf = custody.guard, store._held_io.original._identities
            originals = {name: getattr(guard, name) for name in ("_active", "_owner", "_profile_ids", "_exclusive", "_profiles")}
            visits, context_tokens = [], []

            def change(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is resource:
                    visits.append(row)
                    if len(visits) == 2:
                        if mutation == "current":
                            context_tokens.append(lifecycle._CURRENT.set(None))
                        else:
                            field, value = {
                                "active": ("_active", False),
                                "owner": ("_owner", ()),
                                "member": ("_profile_ids", guard._profile_ids - {custody.namespace.profile.identity}),
                                "mode": ("_exclusive", not guard._exclusive),
                                "profiles": ("_profiles", ()),
                            }[mutation]
                            setattr(guard, field, value)
                return result

            before = resource.observed_bytes, budget.bytes
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(store._held_io.original, "_identities", change)
                    with pytest.raises(error_type) as caught:
                        pin.verify()
            finally:
                for token in reversed(context_tokens):
                    lifecycle._CURRENT.reset(token)
                for name, value in originals.items():
                    setattr(guard, name, value)
            assert caught.value.code == code
            assert len(visits) == 2 and (resource.observed_bytes, budget.bytes) == before
            assert resource.failure is custody.held_original.first is None
            saved_budget_record = store._BUDGET_RECORDS[id(budget)]
            assert saved_budget_record.first is caught.value
            raise caught.value
    assert outer.value.code == caught.value.code == code
    assert saved_budget_record.first is caught.value
    if error_type is lifecycle.StorageLifecycleError:
        assert outer.value is not caught.value
    else:
        assert outer.value is caught.value


@pytest.mark.parametrize("dispatch", ["class_verify", "instance_allows"])
def test_custom_guard_dispatch_preserves_original_complete_tail(namespace, store, initialized, monkeypatch, dispatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        guard, calls = custody.guard, []
        original = lifecycle.StorageGuard.verify if dispatch == "class_verify" else lifecycle.StorageGuard.allows

        def custom(receiver, *args, **kwargs):
            calls.append((receiver, args, kwargs))
            return original(receiver, *args, **kwargs)

        before = resource.observed_bytes, budget.bytes
        with monkeypatch.context() as patch:
            if dispatch == "class_verify":
                patch.setattr(lifecycle.StorageGuard, "verify", custom)
            else:
                patch.setattr(guard, "allows", MethodType(custom, guard))
            pin.verify()
        expected_args = () if dispatch == "class_verify" else (custody.namespace.profile,)
        assert calls == [(guard, expected_args, {})] * 4
        assert resource.observed_bytes - before[0] == budget.bytes - before[1] == resource.byte_size
        assert resource.failure is custody.first is custody.held_original.first is None


def test_store_first_precedes_late_guard_descriptor_getter(namespace, store, initialized, monkeypatch):
    first = RuntimeError("original Store first precedes guard tail descriptor")
    with pytest.raises(RuntimeError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, visits, getters = store._held_io.original._identities, [], []

            class GuardVerifyDescriptor:
                def __get__(self, receiver, owner):
                    getters.append((receiver, owner))
                    pytest.fail("guard descriptor getter preceded the original Store first")

            before = resource.observed_bytes, budget.bytes
            with monkeypatch.context() as patch:
                def change(row, **kwargs):
                    result = leaf(row, **kwargs)
                    if row is resource:
                        visits.append(row)
                        if len(visits) == 2:
                            custody.first = first
                            patch.setattr(lifecycle.StorageGuard, "verify", GuardVerifyDescriptor())
                    return result

                patch.setattr(store._held_io.original, "_identities", change)
                with pytest.raises(RuntimeError) as caught:
                    pin.verify()
            assert caught.value is custody.first is first
            assert len(visits) == 2 and getters == []
            assert (resource.observed_bytes, budget.bytes) == before
            assert resource.failure is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(budget)].first is first
            raise caught.value
    assert outer.value is first


def test_guard_field_descriptor_is_not_called_by_admission_and_keeps_full_fallback(namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        guard, getters = custody.guard, []

        class ProfilesDescriptor:
            def __get__(self, receiver, owner):
                if receiver is None:
                    return self
                getters.append(receiver)
                return receiver.__dict__["_profiles"]

            def __set__(self, receiver, value):
                receiver.__dict__["_profiles"] = value

        before = resource.observed_bytes, budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.StorageGuard, "_profiles", ProfilesDescriptor(), raising=False)
            assert store._pin_verify_guard_dispatch_original(guard) is False
            assert store._pin_verify_guard_binding_original(record.stack) is None
            assert getters == []
            pin.verify()
        assert getters and all(value is guard for value in getters)
        assert resource.observed_bytes - before[0] == budget.bytes - before[1] == resource.byte_size
        assert resource.failure is custody.first is custody.held_original.first is None
