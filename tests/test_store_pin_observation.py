"""Fixed Pin observations retain real identity, charging and custom dispatch."""
from collections import Counter
from contextlib import contextmanager
import hashlib
from pathlib import Path

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile, _ChangedStat  # noqa: F401
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r675"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r675.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r675/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "f90ad781a2fec3576dd8716d78fdd280e5affdc3a75244a2f98371ec54d63073"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r675.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3e4faa260389083435bdf3f839a91793a34a848a0d011b0d7dd1601200c6095a"
    assert module.ns is namespace
    return module


@contextmanager
def live_pin(namespace, store, profile, kind="source"):
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            actual = store._budget_record_original(budget).acquisition
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                custody = store._STACK_RECORDS[id(stack)]
                if kind == "source":
                    pin = custody.pins[0].original
                else:
                    pin = stack.enter_context(store._PinSelection(actual.directory,
                        actual.metadata[1].path.name, 65536, budget))
                record = store._PIN_RECORDS[id(pin)]
                resource = store._held_io._resource_original(record.scope, record.lease, store._held_io._FileLease)
                assert record.kind == kind and resource in custody.held_original.resources
                yield pin, record, resource, custody, budget
            assert custody.closed and id(stack) not in store._STACK_RECORDS
            assert id(pin) not in store._PIN_RECORDS
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS


@pytest.mark.parametrize("kind", ["source", "pin"])
def test_fixed_observation_is_fresh_and_preserves_charging(namespace, store, initialized, monkeypatch, kind):
    with live_pin(namespace, store, initialized, kind) as (pin, record, resource, custody, budget):
        original = store._held_io._verify_identity_record
        seen = Counter()
        expected = Counter({id(row): 1 for row in custody.held_original.resources})
        expected[id(resource)] += 1

        def observe(row, **kwargs):
            result = original(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        before = budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            assert pin.raw == resource.payload
            assert seen == expected
            assert pin.sha256 == resource.sha256
            assert seen == expected + expected
            seen.clear()
            assert store._pin_record_original(pin) is record
            assert seen == expected
        assert budget.bytes == before
        observed_before = resource.observed_bytes
        pin.verify()
        assert resource.observed_bytes - observed_before == resource.byte_size
        assert budget.bytes - before == resource.byte_size


@pytest.mark.parametrize("target", ["record", "identity", "observation"])
def test_custom_helpers_keep_original_chain(namespace, store, initialized, monkeypatch, target):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        module, name = (store, "_pin_record_original") if target == "record" else (
            store._held_io, "_verify_file_identity_original" if target == "identity" else "_file_observation_original")
        original, leaf = getattr(module, name), store._held_io._verify_identity_record
        calls, seen = [], Counter()

        def custom(*args):
            calls.append(args)
            return original(*args)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is custody.scope:
                seen[id(row)] += 1
            return result

        with monkeypatch.context() as patch:
            patch.setattr(module, name, custom)
            patch.setattr(store._held_io, "_verify_identity_record", observe)
            assert pin.raw == resource.payload
            expected_calls = [(pin,)] if target == "record" else [(record.scope, record.lease)]
            assert calls == expected_calls
            expected = Counter({id(row): 1 for row in custody.held_original.resources})
            expected[id(resource)] += 2
            assert seen == expected


@pytest.mark.parametrize("name", ["_verify_file_identity_original", "_file_observation_original"])
def test_late_custom_dispatch_keeps_remaining_chain_without_stack_replay(namespace, store, initialized, monkeypatch, name):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        original, leaf = getattr(store._held_io, name), store._held_io._verify_identity_record
        calls, seen, changed = [], Counter(), []
        with monkeypatch.context() as patch:
            def custom(*args):
                calls.append(args)
                return original(*args)

            def observe(row, **kwargs):
                result = leaf(row, **kwargs)
                if row.scope is custody.scope:
                    seen[id(row)] += 1
                    if not changed:
                        patch.setattr(store._held_io, name, custom)
                        changed.append(row)
                return result

            patch.setattr(store._held_io, "_verify_identity_record", observe)
            assert pin.raw == resource.payload
            assert calls == [(record.scope, record.lease)]
            expected = Counter({id(row): 1 for row in custody.held_original.resources})
            expected[id(resource)] += 2
            assert seen == expected and len(changed) == 1


def test_final_observer_rejects_identity_changed_after_stack_and_preserves_first(namespace, store, initialized, monkeypatch):
    failures = []
    with pytest.raises(store._held_io.original.SharedIoError) as outer:
        with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
            leaf, original_stat = store._held_io._verify_identity_record, namespace.os.stat
            target = safe._native_path(resource.directory.path / resource.name)
            checked, faults = [], []
            with monkeypatch.context() as patch:
                def changed_stat(path, *args, **kwargs):
                    result = original_stat(path, *args, **kwargs)
                    if path == target:
                        faults.append(path)
                        return _ChangedStat(result, st_ino=result.st_ino + 1)
                    return result

                def observe(row, **kwargs):
                    result = leaf(row, **kwargs)
                    if row is resource and not checked:
                        checked.append(row)
                        patch.setattr(namespace.os, "stat", changed_stat)
                    return result

                patch.setattr(store._held_io, "_verify_identity_record", observe)
                with pytest.raises(store._held_io.original.SharedIoError) as caught:
                    pin.raw
            assert checked == [resource] and faults
            assert custody.held_original.first is caught.value
            failures.append(caught.value)
            with pytest.raises(store._held_io.original.SharedIoError) as repeated:
                pin.raw
            assert repeated.value is caught.value
            raise caught.value
    assert failures == [outer.value]
