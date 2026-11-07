"""Original Pin entry retains real acquisition, errors, accounting and cleanup."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path

import pytest

from reno import storage_lifecycle as lifecycle
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r685"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r685.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r685/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r685.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "0fc4abc17f492e624abe93334bfaffeae5d37bed2c9df3626c49e4b8e712af62"
    assert module.ns is namespace
    return module


@contextmanager
def live_stack(namespace, store, profile, kind="closing"):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            actual = store._budget_record_original(budget).acquisition
            expected = actual.metadata[1].raw
            selection = store._PinSelection(actual.directory, actual.metadata[1].path.name, 65536, budget)
            stack_type = store._ClosingStack if kind == "closing" else store._ReadStack
            with stack_type(budget) as stack:
                if kind == "closing":
                    store._source_pins(stack, budget)
                custody = store._STACK_RECORDS[id(stack)]
                yield stack, custody, budget, selection, expected
            assert custody.closed and custody.first is None and id(stack) not in store._STACK_RECORDS
            assert all(pin.closed and id(pin.original) not in store._PIN_RECORDS for pin in custody.pins)
            if kind == "read":
                assert budget.bytes == 2 * len(expected)  # Original acquisition + closing rehash.
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


@pytest.mark.parametrize("kind", ["closing", "read"])
def test_fixed_entry_preserves_real_pin_payload_charges_and_normal_close(namespace, store, initialized, monkeypatch, kind):
    with live_stack(namespace, store, initialized, kind) as (stack, custody, budget, selection, expected_raw):
        original = store._held_io._verify_identity_record
        source_ids = {id(row) for row in custody.held_original.resources}
        seen = Counter()

        def observe(row, **kwargs):
            result = original(row, **kwargs)
            if id(row) in source_ids:
                seen[id(row)] += 1
            return result

        before, pins_before = budget.bytes, custody.pins
        with monkeypatch.context() as patch:
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            pin = stack.enter_context(selection)
        assert seen == Counter({value: 2 for value in source_ids})
        assert budget.bytes - before == len(expected_raw)
        record = store._PIN_RECORDS[id(pin)]
        assert custody.pins == pins_before + (record,) and record.original is pin and record.stack is stack
        assert record.scope is custody.scope and record.directory is selection.directory and record.kind == "pin"
        assert pin.raw == expected_raw and pin.sha256 == hashlib.sha256(expected_raw).hexdigest()
        assert budget.bytes - before == len(expected_raw)
        if kind == "closing":
            pin.verify()
            assert budget.bytes - before == 2 * len(expected_raw)


@pytest.mark.parametrize("target", ["factory", "stack", "scope", "directories"])
def test_custom_entry_components_keep_all_original_checks_and_arguments(namespace, store, initialized, monkeypatch, target):
    with live_stack(namespace, store, initialized) as (stack, custody, budget, selection, expected_raw):
        module, name = {
            "factory": (store, "_enter_pin_original"),
            "stack": (store, "_stage_stack_original"),
            "scope": (store._held_io, "_verify_scope_identity_original"),
            "directories": (store, "_store_directories_verify_original"),
        }[target]
        original, leaf = getattr(module, name), store._held_io._verify_identity_record
        source_ids = {id(row) for row in custody.held_original.resources}
        seen, calls = Counter(), []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if id(row) in source_ids:
                seen[id(row)] += 1
            return result

        before = budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            pin = stack.enter_context(selection)
        assert seen == Counter({value: 3 for value in source_ids})
        expected_args = {"factory": (stack, selection), "stack": (stack,),
                         "scope": (custody.scope,), "directories": (custody,)}[target]
        assert calls == [(expected_args, {})] * (1 if target == "factory" else 3)
        assert budget.bytes - before == len(expected_raw) and pin.raw == expected_raw


def test_wrong_selection_budget_rejects_before_original_file_acquisition(namespace, store, initialized, monkeypatch):
    with live_stack(namespace, store, initialized) as (stack, custody, budget, selection, expected_raw):
        selection.budget = object()
        original = store._held_io._acquire_file_original
        calls = []
        before = budget.bytes, custody.pins, tuple(custody.held_original.resources)

        def observe(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(store._held_io, "_acquire_file_original", observe)
            with pytest.raises(store.v2.StoreError) as caught:
                stack.enter_context(selection)
        assert caught.value.code == "PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED"
        assert calls == [] and (budget.bytes, custody.pins, tuple(custody.held_original.resources)) == before


@pytest.mark.parametrize("custom_factory", [False, True])
def test_real_scope_first_failure_precedes_invalid_selection_and_custom_factory(namespace, store, initialized, monkeypatch, custom_factory):
    first, calls, failures = RuntimeError("injected original held identity failure"), [], []
    with pytest.raises(RuntimeError) as outer:
        with live_stack(namespace, store, initialized) as (stack, custody, budget, selection, expected_raw):
            selection.budget = object()  # Later invalid input must not cover the actual first error.
            original = store._held_io._verify_identity_record
            factory = store._enter_pin_original
            source = custody.held_original.resources[0]
            before = budget.bytes, custody.pins, tuple(custody.held_original.resources)

            def fail(row, **kwargs):
                result = original(row, **kwargs)
                if row is source:
                    raise first
                return result

            def custom(*args):
                calls.append(args)
                return factory(*args)

            with monkeypatch.context() as patch:
                patch.setattr(store._held_io, "_verify_identity_record", fail)
                if custom_factory:
                    patch.setattr(store, "_enter_pin_original", custom)
                with pytest.raises(RuntimeError) as caught:
                    stack.enter_context(selection)
            assert caught.value is first and custody.held_original.first is source.failure is first
            assert calls == [] and (budget.bytes, custody.pins, tuple(custody.held_original.resources)) == before
            failures.append(caught.value)
            raise caught.value
    assert failures == [outer.value] and outer.value is first
