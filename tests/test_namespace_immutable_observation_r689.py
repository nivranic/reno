"""One real immutable-file verification keeps rereads, late gates and fallbacks."""
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
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r687"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r687.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r687/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r687.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3c9e39465994e8b426f226c8339c17fa7c8e93a8d899d45b24e096562ee6289a"
    assert module.ns is namespace
    return module


@contextmanager
def live_file(namespace, profile, guard_mode="exclusive"):
    guard_factory = lifecycle.exclusive if guard_mode == "exclusive" else lifecycle.shared
    owner_factory = namespace._closed_namespace_owned_locked if guard_mode == "exclusive" else namespace._closed_namespace_runtime_owned_locked
    with guard_factory(profile) as guard:
        with owner_factory(profile, exclusive=True) as owner:
            actual = namespace._CLOSED_NAMESPACE_OWNERS[id(owner)]
            assert actual.storage_guard is guard and actual.exclusive
            pin = actual.metadata[1]
            assert type(pin) is namespace._ClosedFileHold and pin.scope is actual.stack.scope
            resource = namespace._held_io._resource_original(pin.scope, pin.lease, namespace._held_io._FileLease)
            custody = namespace._held_io._scope_original(pin.scope)
            assert resource.resource_kind == "filehold" and resource.budget is actual.stack.clock
            yield pin, resource, custody, actual
        assert id(owner) not in namespace._CLOSED_NAMESPACE_OWNERS and resource.finished


@pytest.mark.parametrize("guard_mode", ["exclusive", "shared"])
def test_one_observation_preserves_real_reread_and_next_call_is_fresh(namespace, initialized, monkeypatch, guard_mode):
    with live_file(namespace, initialized, guard_mode) as (pin, resource, custody, actual):
        leaf = namespace._held_io._verify_identity_record
        seen = []

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row is resource:
                seen.append(row)
            return result

        before = resource.observed_bytes
        clocks = resource.clock_contract, actual.budget.started, actual.stack.clock
        with monkeypatch.context() as patch:
            patch.setattr(namespace._held_io, "_verify_identity_record", observe)
            assert namespace._closed_immutable_dispatch_original(pin)
            namespace._immutable_pin_verify(pin)
            assert seen == [resource] * 3  # Real read before/after, then one physical observation.
            assert resource.observed_bytes - before == resource.byte_size
            namespace._immutable_pin_verify(pin)
            assert seen == [resource] * 6
            assert resource.observed_bytes - before == 2 * resource.byte_size
        assert (resource.clock_contract, actual.budget.started, actual.stack.clock) == clocks
        assert custody.first is None and resource.failure is None


@pytest.mark.parametrize("target", ["verify", "observation", "raw", "sha256", "held_verify", "held_observation", "hash", "constructor", "lookup", "poll"])
def test_custom_descriptors_and_helpers_preserve_original_full_chain(namespace, initialized, monkeypatch, target):
    with live_file(namespace, initialized) as (pin, resource, custody, actual):
        cls, held = namespace._ClosedFileHold, namespace._held_io
        module, name = {
            "verify": (cls, "verify"), "observation": (cls, "_observation"),
            "raw": (cls, "raw"), "sha256": (cls, "sha256"),
            "held_verify": (held, "_verify_file_original"),
            "held_observation": (held, "_file_observation_original"),
            "hash": (namespace, "_sha"), "constructor": (held, "FileObservation"),
            "lookup": (held, "_resource_original"), "poll": (held, "_poll_record"),
        }[target]
        original, leaf = getattr(module, name), held._verify_identity_record
        calls, seen = [], []

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original.fget(*args, **kwargs) if target in {"raw", "sha256"} else original(*args, **kwargs)

        def observe(row, **kwargs):
            result = leaf(row, **kwargs)
            if row is resource:
                seen.append(row)
            return result

        before = resource.observed_bytes
        with monkeypatch.context() as patch:
            patch.setattr(module, name, property(custom) if target in {"raw", "sha256"} else custom)
            patch.setattr(held, "_verify_identity_record", observe)
            assert not namespace._closed_immutable_dispatch_original(pin)
            namespace._immutable_pin_verify(pin)
        assert seen == [resource] * 5
        assert resource.observed_bytes - before == resource.byte_size
        if target in {"verify", "observation", "raw", "sha256"}:
            count = {"verify": 1, "observation": 3, "raw": 2, "sha256": 1}[target]
            assert calls == [((pin,), {})] * count
        elif target in {"held_verify", "held_observation"}:
            assert calls == [((pin.scope, pin.lease), {})] * (1 if target == "held_verify" else 3)
        elif target == "hash":
            assert calls == [((resource.payload,), {})]
        elif target == "lookup":
            assert calls == [((pin.scope, pin.lease, held._FileLease), {})] * 4
        elif target == "constructor":
            assert len(calls) == 3 and all(args[0] == resource.payload and not kwargs for args, kwargs in calls)
        else:
            assert calls and all(args == (resource,) and not kwargs for args, kwargs in calls)


def test_original_plain_filehold_keeps_legacy_path(namespace, initialized, monkeypatch):
    with live_file(namespace, initialized) as (pin, resource, custody, actual):
        with namespace.pf.FileHold(pin.path, maximum=namespace.MAX_METADATA_BYTES,
                                   check_cancelled=actual.budget.check) as plain:
            assert type(plain) is namespace.pf.FileHold
            original = namespace.pf.FileHold.verify
            calls = []

            def observe(value):
                calls.append(value)
                return original(value)

            with monkeypatch.context() as patch:
                patch.setattr(namespace.pf.FileHold, "verify", observe)
                assert not namespace._closed_immutable_dispatch_original(plain)
                namespace._immutable_pin_verify(plain)
            assert calls == [plain] and plain.raw == resource.payload


@pytest.mark.parametrize("fault", ["raw_type", "sha"])
def test_type_and_checksum_errors_remain_outside_held_failure_bookkeeping(namespace, initialized, monkeypatch, fault):
    with live_file(namespace, initialized) as (pin, resource, custody, actual):
        leaf = namespace._held_io._verify_identity_record
        saved = resource.payload, resource.sha256
        seen = []

        def corrupt_after_observation_identity(row, **kwargs):
            result = leaf(row, **kwargs)
            if row is resource:
                seen.append(row)
                if len(seen) == 3:
                    if fault == "raw_type":
                        row.payload = object()
                    else:
                        row.sha256 = "0" * 64
            return result

        try:
            with monkeypatch.context() as patch:
                patch.setattr(namespace._held_io, "_verify_identity_record", corrupt_after_observation_identity)
                with pytest.raises(namespace.NamespaceError) as caught:
                    namespace._immutable_pin_verify(pin)
            assert caught.value.code == "NAMESPACE_IMMUTABLE_PIN_CHANGED"
            assert len(seen) == 3 and custody.first is None and resource.failure is None
        finally:
            resource.payload, resource.sha256 = saved
        namespace._immutable_pin_verify(pin)


@pytest.mark.parametrize("fault", ["deadline", "cancellation"])
def test_hash_time_late_gate_rejects_and_preserves_original_first(namespace, initialized, monkeypatch, fault):
    held = namespace._held_io
    lower = held.original
    error_type = lower.SharedIoError if fault == "deadline" else lower.SharedIoCancelledError
    failures, hashes = [], []
    with pytest.raises(error_type) as outer:
        with live_file(namespace, initialized) as (pin, resource, custody, actual):
            leaf, original_hash = held._verify_identity_record, hashlib.sha256
            seen = []
            clocks = resource.clock_contract, actual.budget.started

            def observe(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is resource:
                    seen.append(row)
                return result

            def hash_with_fault(*args, **kwargs):
                result = original_hash(*args, **kwargs)
                if args and args[0] == resource.payload and len(seen) == 3 and not hashes:
                    hashes.append(args[0])
                    if fault == "deadline":
                        remaining = resource.clock_contract[2] + 0.02 - time.monotonic()
                        assert 0 < remaining <= 30
                        time.sleep(remaining)
                    else:
                        lower.request_cancellation(custody.cancellation)
                return result

            with monkeypatch.context() as patch:
                patch.setattr(held, "_verify_identity_record", observe)
                patch.setattr(hashlib, "sha256", hash_with_fault)
                assert namespace._closed_immutable_dispatch_original(pin)
                with pytest.raises(error_type) as caught:
                    namespace._immutable_pin_verify(pin)
            assert len(hashes) == 1 and len(seen) == 3
            assert custody.first is resource.failure is caught.value
            assert (resource.clock_contract, actual.budget.started) == clocks
            if fault == "deadline":
                assert caught.value.code == "OWNED_IO_NAMESPACE_DEADLINE"
            else:
                assert caught.value is custody.cancellation._failure
            failures.append(caught.value)
            observed_before_repeat = resource.observed_bytes
            with pytest.raises(error_type) as repeated:
                namespace._immutable_pin_verify(pin)
            assert custody.first is caught.value and resource.failure is repeated.value
            assert resource.observed_bytes == observed_before_repeat
            if fault == "deadline":
                assert repeated.value.code == "OWNED_IO_NAMESPACE_DEADLINE"
                assert repeated.value is not caught.value  # Original deadline gate precedes existing first.
            else:
                assert repeated.value is caught.value  # Original cancellation object is stable.
            raise caught.value
    assert failures == [outer.value]


@pytest.mark.parametrize("change", ["descriptor", "lease"])
def test_late_observation_binding_change_rejects_without_replay(namespace, initialized, monkeypatch, change):
    held = namespace._held_io
    errors, custom_calls = [], []
    with pytest.raises(namespace.NamespaceError) as outer:
        with live_file(namespace, initialized) as (pin, resource, custody, actual):
            leaf = held._verify_identity_record
            original_lease = pin.lease
            other = actual.metadata[2]
            seen = []
            try:
                with monkeypatch.context() as patch:
                    def custom(value):
                        custom_calls.append(value)
                        raise AssertionError("late descriptor must not be called")

                    def change_after_observation(row, **kwargs):
                        result = leaf(row, **kwargs)
                        if row is resource:
                            seen.append(row)
                            if len(seen) == 3:
                                if change == "descriptor":
                                    patch.setattr(namespace._ClosedFileHold, "raw", property(custom))
                                else:
                                    pin.lease = other.lease
                        return result

                    patch.setattr(held, "_verify_identity_record", change_after_observation)
                    with pytest.raises(namespace.NamespaceError) as caught:
                        namespace._immutable_pin_verify(pin)
            finally:
                pin.lease = original_lease
            assert caught.value.code == "NAMESPACE_IMMUTABLE_PIN_CHANGED"
            assert len(seen) == 3 and custom_calls == []
            assert custody.first is resource.failure is caught.value
            errors.append(caught.value)
            with pytest.raises(namespace.NamespaceError) as repeated:
                namespace._immutable_pin_verify(pin)
            assert repeated.value is caught.value
            raise caught.value
    assert errors == [outer.value]
