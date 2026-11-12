"""Actual installation Stage checks keep exclusive guard semantics and clocks."""
from collections import Counter
import hashlib
from pathlib import Path
import time
from types import MethodType

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle
from tests.test_stage_directory_entry_r701 import live_stage, initialized, profile  # noqa: F401
from tests.test_store_pin_verify import live_pin
from tests.test_store_stage_effect_integration import (  # noqa: F401
    test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates,
)


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r705"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot("authority_runtime_origin_r705.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r705/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "56f2833e0470e0a0af0cc5aa8381193e5ccc4573ee57a70f7c5f829b6245e75e"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = import_historical_snapshot("authority_runtime_origin_r705.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "dda8b7c6832ef42b44a3cab9690c9f896629a5b290011235b2811478aa7b0cdc"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = import_historical_snapshot("authority_runtime_origin_r705.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "5e714478c4f982ea66293ac4bc5125a632cf0cae77f27ee4e0bad116574a0496"
    assert module.ns is namespace and module.store is store and module.held is store._held_io
    return module


def test_install_scope_shares_one_marker_pass_and_each_call_is_fresh(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        descriptors = {lock.stream.fileno() for lock in custody.guard._locks}
        assert len(descriptors) == len(custody.guard._locks) > 0
        read, identities, require = lifecycle.os.read, stage.held.original._identities, namespace._require_profile
        markers, sources, calls = Counter(), Counter(), []

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def observe_identity(row, **kwargs):
            result = identities(row, **kwargs)
            if row.scope is scope.held_scope:
                sources[id(row)] += 1
            return result

        def custom_profile(*args, **kwargs):
            calls.append((args, kwargs))
            return require(*args, **kwargs)

        expected_sources = Counter({id(row): 1 for row in custody.held_original.resources})
        expected_markers = Counter({descriptor: 1 for descriptor in descriptors})
        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.os, "read", observe_read)
            patch.setattr(stage.held.original, "_identities", observe_identity)
            stage._verify_store_stage_scope_original(scope)
            assert markers == expected_markers and sources == expected_sources
            stage._verify_store_stage_scope_original(scope)
            assert markers == expected_markers + expected_markers
            assert sources == expected_sources + expected_sources
            markers.clear()
            sources.clear()
            # Unknown profile dispatch must execute both original complete
            # calls, on these same actual resources and original deadlines.
            patch.setattr(namespace, "_require_profile", custom_profile)
            stage._verify_store_stage_scope_original(scope)
        assert markers == expected_markers + expected_markers and sources == expected_sources
        assert calls == [((custody.namespace.profile,), {}),
                         ((custody.namespace.profile,), {"exclusive": True})]
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None


@pytest.mark.parametrize("component", ["class_verify", "instance_allows", "exclusive_getter"])
def test_unknown_guard_dispatch_preserves_both_original_marker_passes(namespace, store, stage, initialized, monkeypatch, component):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard = custody.guard
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        read, markers, calls = lifecycle.os.read, Counter(), []
        original = (lifecycle.StorageGuard.verify if component == "class_verify"
                    else lifecycle.StorageGuard.allows if component == "instance_allows"
                    else lifecycle.StorageGuard.exclusive.fget)

        def custom(receiver, *args, **kwargs):
            calls.append((receiver, args, kwargs))
            return original(receiver, *args, **kwargs)

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.os, "read", observe_read)
            if component == "class_verify":
                patch.setattr(lifecycle.StorageGuard, "verify", custom)
            elif component == "instance_allows":
                patch.setattr(guard, "allows", MethodType(custom, guard))
            else:
                patch.setattr(lifecycle.StorageGuard, "exclusive", property(custom))
            stage._verify_store_stage_scope_original(scope)
        assert markers == Counter({descriptor: 2 for descriptor in descriptors})
        expected_args = (custody.namespace.profile,) if component == "instance_allows" else ()
        assert calls == [(guard, expected_args, {})] * (1 if component == "exclusive_getter" else 2)
        assert scope.first_failure is custody.first is custody.held_original.first is None


@pytest.mark.parametrize("component", ["directory_verify", "directory_plan", "plan_verify"])
def test_unknown_directory_dispatch_keeps_original_profile_calls(namespace, store, stage, initialized, monkeypatch, component):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard = custody.guard
        selected = next(lock.directory for lock in guard._locks if lock.directory.path == initialized.reports)
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        read, require = lifecycle.os.read, namespace._require_profile
        markers, calls, require_calls = Counter(), [], []
        original = {"directory_verify": store.safe._Directory.verify,
                    "directory_plan": store.safe._directory_verification_plan,
                    "plan_verify": store.safe._verify_directory_plan}[component]

        def custom(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def full_profile(*args, **kwargs):
            require_calls.append((args, kwargs))
            return require(*args, **kwargs)

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            patch.setattr(lifecycle.os, "read", observe_read)
            if component == "directory_verify":
                patch.setattr(selected, "verify", MethodType(custom, selected))
            else:
                name = "_directory_verification_plan" if component == "directory_plan" else "_verify_directory_plan"
                patch.setattr(store.safe, name, custom)
            stage._verify_store_stage_scope_original(scope)
            candidate_calls = calls.copy()
            assert candidate_calls and markers == Counter({descriptor: 2 for descriptor in descriptors})
            markers.clear()
            calls.clear()
            # Keep the same custom directory dispatch and force the original
            # profile-call path. Its actual arguments and physical reads are
            # the reference, without assuming a count for internal helpers.
            patch.setattr(namespace, "_require_profile", full_profile)
            stage._verify_store_stage_scope_original(scope)
        assert calls == candidate_calls and markers == Counter({descriptor: 2 for descriptor in descriptors})
        assert require_calls == [((custody.namespace.profile,), {}),
                                 ((custody.namespace.profile,), {"exclusive": True})]
        if component == "directory_verify":
            assert all(args == (selected,) and kwargs == {} for args, kwargs in calls)
        else:
            assert all(len(args) == 1 and kwargs == {} for args, kwargs in calls)
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None


def test_custom_guard_directory_keeps_pin_original_four_passes_and_actual_reread(namespace, store, initialized, monkeypatch):
    with live_pin(namespace, store, initialized) as (pin, record, resource, custody, budget):
        guard = custody.guard
        selected = next(lock.directory for lock in guard._locks if lock.directory.path == initialized.reports)
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        read, verify = lifecycle.os.read, store.safe._Directory.verify
        markers, calls = Counter(), []

        def custom_directory(receiver):
            calls.append(receiver)
            return verify(receiver)

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        observed, charged = resource.observed_bytes, budget.bytes
        with monkeypatch.context() as patch:
            patch.setattr(selected, "verify", MethodType(custom_directory, selected))
            patch.setattr(lifecycle.os, "read", observe_read)
            pin.verify()
        # Pin record, Namespace budget, pre-observation state and original
        # tail state each keep their complete profile verification. Each
        # per-lock fallback invokes directory.verify both before and after its marker.
        assert markers == Counter({descriptor: 4 for descriptor in descriptors})
        assert calls == [selected] * 8
        assert resource.observed_bytes - observed == budget.bytes - charged == resource.byte_size
        assert record.budget is budget and resource.budget.actual is budget
        assert custody.first is custody.held_original.first is resource.failure is None
        assert store._BUDGET_RECORDS[id(budget)].first is None


def test_custom_guard_directory_keeps_stage_scope_and_lookup_original_passes(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard = custody.guard
        selected = next(lock.directory for lock in guard._locks if lock.directory.path == initialized.reports)
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        read, verify, identities = lifecycle.os.read, store.safe._Directory.verify, stage.held.original._identities
        markers, sources, calls = Counter(), Counter(), []

        def custom_directory(receiver):
            calls.append(receiver)
            return verify(receiver)

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def observe_identity(row, **kwargs):
            result = identities(row, **kwargs)
            if row.scope is scope.held_scope:
                sources[id(row)] += 1
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            patch.setattr(selected, "verify", MethodType(custom_directory, selected))
            patch.setattr(lifecycle.os, "read", observe_read)
            patch.setattr(stage.held.original, "_identities", observe_identity)
            assert stage._directory_original(scope, directory) is namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        # The original Stage scope has two profile calls; the remaining
        # original Store lookup has one, with no replay of the Stage scope.
        assert markers == Counter({descriptor: 3 for descriptor in descriptors})
        assert calls == [selected] * 6
        assert sources == Counter({id(row): 2 for row in custody.held_original.resources})
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(scope.budget)].first is None


@pytest.mark.parametrize("mutation,code", [
    ("active", "STORAGE_GUARD_REQUIRED"),
    ("owner", "STORAGE_GUARD_CONTEXT"),
    ("current", "STORAGE_GUARD_REQUIRED"),
    ("member", "STORAGE_PROFILE_MISMATCH"),
    ("mode", "STORAGE_LOCK_UPGRADE"),
])
def test_late_guard_state_keeps_original_exclusive_error(namespace, store, stage, initialized, monkeypatch, mutation, code):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard, last = custody.guard, custody.held_original.resources[-1]
        leaf = stage.held.original._identities
        read = lifecycle.os.read
        descriptors = {lock.stream.fileno() for lock in guard._locks}
        markers = Counter()
        originals = {name: getattr(guard, name) for name in ("_active", "_owner", "_profile_ids", "_exclusive")}
        visits, tokens = [], []

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def change(row, **kwargs):
            result = leaf(row, **kwargs)
            if row is last:
                visits.append(row)
                if len(visits) == 1:
                    if mutation == "current":
                        tokens.append(lifecycle._CURRENT.set(None))
                    else:
                        name, value = {"active": ("_active", False), "owner": ("_owner", ()),
                                       "member": ("_profile_ids", guard._profile_ids - {custody.namespace.profile.identity}),
                                       "mode": ("_exclusive", False)}[mutation]
                        setattr(guard, name, value)
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", observe_read)
                patch.setattr(stage.held.original, "_identities", change)
                with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                    stage._verify_store_stage_scope_original(scope)
        finally:
            for token in reversed(tokens):
                lifecycle._CURRENT.reset(token)
            for name, value in originals.items():
                setattr(guard, name, value)
        assert caught.value.code == code and visits == [last]
        assert markers == Counter({descriptor: 2 if mutation in {"member", "mode"} else 1
                                   for descriptor in descriptors})
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(scope.budget)].first is None
        stage._verify_store_stage_scope_original(scope)


def test_same_guard_directory_identity_list_change_keeps_original_second_pass(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard = custody.guard
        lock = guard._locks[0]
        changed_directory = lock.directory
        identity_list = changed_directory.identities
        old_identities = tuple(identity_list)
        assert old_identities
        descriptors = {value.stream.fileno() for value in guard._locks}
        read, leaf = lifecycle.os.read, stage.held.original._identities
        last, changed, markers, sources = custody.held_original.resources[-1], [], Counter(), Counter()

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def change(row, **kwargs):
            result = leaf(row, **kwargs)
            if row.scope is scope.held_scope:
                sources[id(row)] += 1
            if row is last and not changed:
                # Duplicate one already genuine expected identity in the same
                # list. The original full verifier checks both expectations;
                # no filesystem path, lock, owner or native identity is forged.
                identity_list.append(old_identities[-1])
                changed.append(row)
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", observe_read)
                patch.setattr(stage.held.original, "_identities", change)
                stage._verify_store_stage_scope_original(scope)
                assert lock.directory is changed_directory and changed_directory.identities is identity_list
                assert tuple(identity_list) == old_identities + (old_identities[-1],)
        finally:
            identity_list[:] = old_identities
        assert custody.guard is guard and changed == [last]
        assert markers == Counter({descriptor: 2 for descriptor in descriptors})
        assert sources == Counter({id(row): 1 for row in custody.held_original.resources})
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None


def test_mutable_guard_identity_row_uses_full_fallback_and_rejects_late_wrong_inode(namespace, store, stage, initialized, monkeypatch):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        guard = custody.guard
        lock = next(value for value in guard._locks if value.directory.path == initialized.reports)
        changed_directory = lock.directory
        index, original_row = next((index, row) for index, row in enumerate(changed_directory.identities)
                                   if row[0] == changed_directory.path)
        assert type(original_row) is tuple and len(original_row) == 3
        mutable_row = list(original_row)
        descriptors = {value.stream.fileno() for value in guard._locks}
        read, leaf = lifecycle.os.read, stage.held.original._identities
        last, changed, markers = custody.held_original.resources[-1], [], Counter()

        def observe_read(descriptor, amount):
            raw = read(descriptor, amount)
            if descriptor in descriptors:
                assert raw == lifecycle._MARKER and amount == len(lifecycle._MARKER) + 1
                markers[descriptor] += 1
            return raw

        def change(row, **kwargs):
            result = leaf(row, **kwargs)
            if row is last and not changed:
                mutable_row[2] += 1
                changed.append(row)
            return result

        before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        try:
            changed_directory.identities[index] = mutable_row
            assert stage._stage_install_guard_binding_original(scope) is None
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.os, "read", observe_read)
                # The original full verifier accepts equivalent list-shaped
                # rows and performs both complete physical guard passes.
                stage._verify_store_stage_scope_original(scope)
                assert markers == Counter({descriptor: 2 for descriptor in descriptors})
                markers.clear()
                patch.setattr(stage.held.original, "_identities", change)
                with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                    stage._verify_store_stage_scope_original(scope)
                assert changed_directory.identities[index] is mutable_row
        finally:
            changed_directory.identities[index] = original_row
        # The reports directory is checked by the guard, independently from
        # the Store's source directories. Its second guard pre-marker sweep
        # raises SyncError, translated by lifecycle._filesystem to this code.
        assert caught.value.code == "STORAGE_UNSAFE_PATH" and caught.value.status == 503
        assert changed == [last]
        assert markers == Counter({descriptor: 1 for descriptor in descriptors})
        assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
        assert scope.first_failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(scope.budget)].first is None


@pytest.mark.parametrize("first_owner", ["stage", "store"])
def test_original_first_precedes_unknown_exclusive_descriptor(namespace, store, stage, initialized, monkeypatch, first_owner):
    first = RuntimeError("original owned first before exclusive guard selection")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            if first_owner == "stage":
                scope.first_failure = first
            else:
                custody.first = first
            getters = []

            def get_exclusive(receiver):
                getters.append(receiver)
                pytest.fail("unknown exclusive getter preceded existing owned first")

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.StorageGuard, "exclusive", property(get_exclusive))
                with pytest.raises(RuntimeError) as caught:
                    stage._verify_store_stage_scope_original(scope)
            assert caught.value is first and getters == []
            assert custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is first


def test_missing_guard_fields_and_unknown_descriptor_do_not_preempt_stage_first(namespace, store, stage, initialized, monkeypatch):
    first = RuntimeError("actual Stage first precedes missing guard snapshot fields")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            guard, getters = custody.guard, []
            saved = {name: getattr(guard, name) for name in ("_profiles", "_locks")}
            scope.first_failure = first

            def get_exclusive(receiver):
                getters.append(receiver)
                pytest.fail("exclusive getter preempted Stage first with missing guard field")

            with monkeypatch.context() as patch:
                patch.setattr(lifecycle.StorageGuard, "exclusive", property(get_exclusive))
                for name, original in saved.items():
                    try:
                        delattr(guard, name)
                        assert stage._stage_install_guard_binding_original(scope) is None
                        with pytest.raises(RuntimeError) as caught:
                            stage._verify_store_stage_scope_original(scope)
                        assert caught.value is first and getters == []
                    finally:
                        setattr(guard, name, original)
            assert scope.first_failure is first and custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise first
    assert outer.value is first and scope.closed and custody.closed
    assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)


@pytest.mark.parametrize("missing", ["guard_profiles", "lock_stream"])
def test_missing_guard_or_lock_field_does_not_preempt_actual_budget_first(namespace, store, stage, initialized, monkeypatch, missing):
    first = RuntimeError("registered Budget first precedes missing guard or lock field")
    with pytest.raises(RuntimeError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            guard = custody.guard
            target, name = ((guard, "_profiles") if missing == "guard_profiles"
                            else (guard._locks[0], "stream"))
            original = getattr(target, name)
            budget_record = store._BUDGET_RECORDS[id(scope.budget)]
            assert budget_record.original is scope.budget and budget_record.first is None
            budget_record.first = first
            getters = []

            def get_verify(receiver):
                getters.append(receiver)
                pytest.fail("unknown guard getter preceded registered Budget first")

            try:
                delattr(target, name)
                # Check the missing-field route while all descriptors are
                # still original, before independently probing getter order.
                assert stage._stage_install_guard_binding_original(scope) is None
                with monkeypatch.context() as patch:
                    patch.setattr(lifecycle.StorageGuard, "verify", property(get_verify))
                    with pytest.raises(RuntimeError) as caught:
                        stage._verify_store_stage_scope_original(scope)
            finally:
                setattr(target, name, original)
            assert caught.value is budget_record.first is first and getters == []
            assert scope.first_failure is custody.first is custody.held_original.first is None
            assert all(row.failure is None for row in custody.held_original.resources)
            raise caught.value
    assert outer.value is budget_record.first is first
    assert scope.closed and custody.closed
    assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)


def test_original_bytes_gate_remains_after_guard_selection(namespace, store, stage, initialized):
    with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
        previous = scope.budget.bytes
        try:
            scope.budget.bytes = -1
            with pytest.raises(stage.OwnedStageError) as caught:
                stage._verify_store_stage_scope_original(scope)
        finally:
            scope.budget.bytes = previous
        assert caught.value.code == "STAGE_ORIGINAL_BYTES_CHANGED"
        assert scope.first_failure is custody.first is custody.held_original.first is None
        assert store._BUDGET_RECORDS[id(scope.budget)].first is None
        stage._verify_store_stage_scope_original(scope)


def test_actual_original_deadline_still_rejects_after_last_source_identity(namespace, store, stage, initialized, monkeypatch):
    with pytest.raises(stage.OwnedStageError) as outer:
        with live_stage(namespace, store, stage, initialized) as (scope, custody, directory):
            leaf, last, visits = stage.held.original._identities, custody.held_original.resources[-1], []
            clocks = scope.clock, scope.budget.deadline, scope.budget.namespace_deadline
            before = scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)

            def expire(row, **kwargs):
                result = leaf(row, **kwargs)
                if row is last:
                    visits.append(row)
                    if len(visits) == 1:
                        deadline = min(scope.budget.deadline, scope.budget.namespace_deadline)
                        assert 0 < deadline - time.monotonic() <= 30
                        while time.monotonic() <= deadline:
                            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
                return result

            with monkeypatch.context() as patch:
                patch.setattr(stage.held.original, "_identities", expire)
                with pytest.raises(stage.OwnedStageError) as caught:
                    stage._verify_store_stage_scope_original(scope)
            assert caught.value.code == "STAGE_ORIGINAL_DEADLINE" and visits == [last]
            assert (scope.clock, scope.budget.deadline, scope.budget.namespace_deadline) == clocks
            assert (scope.budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)) == before
            assert scope.first_failure is custody.first is custody.held_original.first is None
            assert store._BUDGET_RECORDS[id(scope.budget)].first is None
            raise caught.value
    assert outer.value is caught.value
