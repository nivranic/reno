"""Real Pin budget rebinding preserves accounting, poll order and cleanup."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path
import time

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r693"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r693.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r693/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r693.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "6a980e86e9f5dad2f757255986f887553ad39ef7ed044215648ccb28eddad7c5"
    assert module.ns is namespace
    return module


@contextmanager
def live_pin(namespace, store, profile, kind="pin"):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            target = store._Budget(owner)
            acquisition = store._budget_record_original(budget).acquisition
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                custody = store._STACK_RECORDS[id(stack)]
                pin = custody.pins[0].original if kind == "source" else stack.enter_context(
                    store._PinSelection(acquisition.directory, acquisition.metadata[1].path.name, 65536, budget))
                record = store._PIN_RECORDS[id(pin)]
                resource = store._held_io._resource_original(record.scope, record.lease, store._held_io._FileLease)
                assert store._pin_stack_identity_binding_original(record) is resource
                yield pin, record, resource, custody, budget, target
            assert custody.closed and id(stack) not in store._STACK_RECORDS
            assert all(p.closed and id(p.original) not in store._PIN_RECORDS for p in custody.pins)
            assert all(r.finished and r.native_close_returned for r in custody.held_original.resources)
        for value in (budget, target):
            assert id(value) not in store._BUDGET_RECORDS and id(value) not in store._RETAINED_BUDGETS


@pytest.mark.parametrize("kind,same", [("pin", True), ("pin", False), ("source", False)])
def test_real_binding_single_stack_and_target_reread_accounting(namespace, store, initialized, monkeypatch, kind, same):
    with live_pin(namespace, store, initialized, kind) as (pin, record, resource, custody, budget, other):
        target = budget if same else other
        old_binding = resource.budget
        leaf = store._held_io._verify_identity_record
        seen = Counter()

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        before = budget.bytes, other.bytes, resource.observed_bytes
        with monkeypatch.context() as patch:
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            pin.budget = target
        assert seen == Counter({id(row): 1 for row in custody.held_original.resources})
        assert record.budget is target
        if kind == "pin":
            assert resource.budget is not old_binding and resource.budget.actual is target
            assert resource.budget.namespace_clock is custody.clock
        else:
            assert resource.budget is old_binding is custody.clock
        assert (budget.bytes, other.bytes, resource.observed_bytes) == before
        target_before, observed = target.bytes, resource.observed_bytes
        pin.verify()
        assert target.bytes - target_before == resource.byte_size
        assert resource.observed_bytes - observed == resource.byte_size
        if not same:
            assert budget.bytes == before[0]
    # Natural close rehashes and charges the same target once more.
    assert target.bytes - target_before >= 2 * resource.byte_size


@pytest.mark.parametrize("component", ["record", "budget", "rebind", "poll", "bind", "directories"])
def test_custom_dispatch_preserves_original_rebind_and_second_stack(namespace, store, initialized, monkeypatch, component):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget, target):
        module, name = {
            "record": (store, "_pin_record_original"), "budget": (store, "_budget_record_original"),
            "rebind": (store._held_io, "_rebind_store_pin_budget_original"),
            "poll": (store._held_io, "_poll_record"), "bind": (store._held_io, "_bind_pin_budget_original"),
            "directories": (store, "_store_directories_verify_original"),
        }[component]
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

        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            pin.budget = target
        expected = Counter({id(row): 2 for row in custody.held_original.resources})
        if component == "record":
            expected[id(resource)] += 1  # Original custom record path's specific identity.
        assert seen == expected and calls
        assert record.budget is resource.budget.actual is target
        if component == "rebind":
            assert calls == [((record.stack, record.lease, target), {})]


@pytest.mark.parametrize("invalid", ["unregistered", "owner"])
def test_invalid_target_rejects_without_rebinding_or_charging(namespace, store, initialized, invalid):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget, target):
        before = resource.budget, budget.bytes, target.bytes, resource.observed_bytes
        original_owner = target.namespace_owner
        try:
            if invalid == "owner":
                target.namespace_owner = object()
            with pytest.raises(store.v2.StoreError) as caught:
                pin.budget = object() if invalid == "unregistered" else target
            assert caught.value.code == ("PERSONAL_STORE_NATIVE_BUDGET_OWNER_CHANGED" if invalid == "unregistered"
                                         else "PERSONAL_STORE_NATIVE_BUDGET_CHANGED")
        finally:
            target.namespace_owner = original_owner
        assert record.budget is budget and (resource.budget, budget.bytes, target.bytes, resource.observed_bytes) == before
        assert resource.failure is custody.first is custody.held_original.first is None


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_both_original_polls_reject_real_cancel_or_elapsed_deadline(namespace, store, initialized, monkeypatch, phase, reason):
    lower = store._held_io.original
    error_type = lower.SharedIoCancelledError if reason == "cancel" else lower.SharedIoError
    with pytest.raises(error_type) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget, target):
            original = lower.poll_cancellation
            # One scope poll, one per real resource, then Pin-record poll.
            # The next two are exactly the retained rebind pre/post polls.
            trigger = len(custody.held_original.resources) + (3 if phase == "pre" else 4)
            calls, triggered = [], []
            before = resource.budget, budget.bytes, target.bytes, resource.observed_bytes

            def interrupt(cancellation):
                if cancellation is custody.cancellation:
                    calls.append(resource.budget.actual)
                    if len(calls) == trigger:
                        triggered.append(True)
                        if reason == "cancel":
                            lower.request_cancellation(cancellation)
                        else:
                            end = min(resource.budget.deadline, custody.clock.deadline)
                            while time.monotonic() <= end:
                                time.sleep(min(0.02, max(0.001, end - time.monotonic())))
                return original(cancellation)

            with monkeypatch.context() as patch:
                patch.setattr(lower, "poll_cancellation", interrupt)
                with pytest.raises(error_type) as caught:
                    pin.budget = target
            assert triggered == [True] and len(calls) == trigger
            assert record.budget is budget
            if phase == "pre":
                assert resource.budget is before[0] and calls[-1] is budget
            else:
                assert resource.budget is not before[0] and resource.budget.actual is target and calls[-1] is target
            assert (budget.bytes, target.bytes, resource.observed_bytes) == before[1:]
            # Original rebind has no catch/remember wrapper; keep that boundary.
            assert resource.failure is custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(budget)].first is store._BUDGET_RECORDS[id(target)].first is None
            if reason == "cancel":
                assert caught.value is custody.cancellation._failure
            else:
                assert caught.value.code in {"OWNED_IO_NAMESPACE_DEADLINE", "OWNED_IO_PIN_DEADLINE"}
            raise caught.value
    assert outer.value is caught.value


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("mutation", ["lease", "bind"])
def test_late_change_rejects_without_replay_and_keeps_binding_failure_state(namespace, store, initialized, monkeypatch, phase, mutation):
    lower = store._held_io.original
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget, target):
        original, lease = lower.poll_cancellation, record.lease
        binding = store._held_io._bind_pin_budget_original
        trigger = len(custody.held_original.resources) + (3 if phase == "pre" else 4)
        calls = []

        def change(cancellation):
            if cancellation is custody.cancellation:
                calls.append(resource.budget.actual)
                if len(calls) == trigger:
                    if mutation == "lease":
                        record.lease = store._held_io._FileLease()
                    else:
                        store._held_io._bind_pin_budget_original = lambda *args: pytest.fail("late dispatch executed")
            return original(cancellation)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(lower, "poll_cancellation", change)
                with pytest.raises(store.v2.StoreError) as caught:
                    pin.budget = target
            assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED"
            assert len(calls) == trigger and record.budget is budget
            assert resource.budget.actual is (budget if phase == "pre" else target)
            assert resource.failure is custody.first is custody.held_original.first is None
        finally:
            record.lease = lease
            store._held_io._bind_pin_budget_original = binding
        # No rollback of a successful native binding; finish through a new real setter.
        pin.budget = target
        assert resource.budget.actual is record.budget is target


def test_initial_real_cancellation_precedes_invalid_target(namespace, store, initialized):
    lower = store._held_io.original
    with pytest.raises(lower.SharedIoCancelledError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget, target):
            first = custody.cancellation._failure
            before = resource.budget, budget.bytes, target.bytes, resource.observed_bytes
            lower.request_cancellation(custody.cancellation)
            with pytest.raises(lower.SharedIoCancelledError) as caught:
                pin.budget = object()
            assert caught.value is first
            assert (resource.budget, budget.bytes, target.bytes, resource.observed_bytes) == before
            assert record.budget is budget
            raise caught.value
    assert outer.value is first
