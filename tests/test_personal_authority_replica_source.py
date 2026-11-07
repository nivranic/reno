"""Unrun fixed-source candidates; real isolated native integration only.

The historical declarations become physical original-byte files in an isolated
package.  Native observations come from the actual source-unit kernel; fixture
dicts and pure cache objects are never submitted as an operation context.
"""
import hashlib
import os
import threading

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_selector as selector
from reno import personal_authority_replica_source as source
from reno import personal_authority_replica_source_native as native
from reno import personal_preservation_files as fixture_files
from tests import test_personal_authority_replica_contract as historical
from tests import test_personal_authority_replica_operation as operation_fixture


SOURCE_ERRORS = (source.AuthorityReplicaSourceError, operation.AuthorityReplicaOperationError)


def initialize_fresh_profile_fixture(path):
    assert not path.exists()
    value = operation_fixture.make_profile(path)
    operation_fixture.lifecycle.initialize(value)
    if os.name == "nt":
        # Test fixture only: all four roots were freshly created here.
        # The source-unit product merely checks existing ACLs.
        for root in (value.database.parent, value.media, value.reports, value.checkpoints):
            fixture_files._windows_acl(root, directory=True, initialize=True)
    return value


@pytest.fixture
def home(tmp_path):
    return initialize_fresh_profile_fixture(tmp_path / "source-home")


@pytest.fixture
def other(tmp_path):
    return initialize_fresh_profile_fixture(tmp_path / "source-other")


def prepare_source(home, tmp_path, *, kind="g1", populated=True):
    """Return (actual selector, original bundle, actual detached package root).

    Reusable only for Root's isolated tests.  All bytes and empty directories
    are materialized; this helper neither supplies census/native DTOs nor
    changes existing ACLs.  A missing native prerequisite must fail the run.
    """
    bundle, generation = historical.history(kind, populated=populated)
    root = home.media / ("source-package-" + kind)
    root.mkdir()
    if os.name == "nt":
        fixture_files._windows_acl(root, directory=True, initialize=True)
    for key in sorted(bundle["directories"], key=lambda key: (key.count("/"), key.encode("utf-8"))):
        if key:
            (root / key).mkdir()
            if os.name == "nt":
                fixture_files._windows_acl(root / key, directory=True, initialize=True)
    for key, raw in bundle["raw_files"].items():
        with (root / key).open("xb") as stream:
            stream.write(raw)
        if os.name == "nt":
            fixture_files._windows_acl(root / key, directory=False, initialize=True)
    node_raw = historical.wire({"instance_id": historical.NODE_ID})
    node = home.database.parent / "node-identity.json"
    with node.open("xb") as stream:
        stream.write(node_raw)
    if os.name == "nt":
        fixture_files._windows_acl(node, directory=False, initialize=True)
    value = selector.DetachedRaw2Source(package_root=root, generation=generation,
        expected_request_sha256=hashlib.sha256(bundle["facts"]["request.json"]).hexdigest())
    return value, bundle, root


def actual_source_record():
    registrations = list(operation._LIVE.values())
    assert len(registrations) == 1
    ctx = registrations[0].source_context
    assert ctx is registrations[0].record.source_context
    assert ctx.source_record.token is ctx.source_token
    assert source._LIVE[id(ctx.source_token)] is ctx.source_record
    return registrations[0], ctx, ctx.source_record


@pytest.mark.skipif(os.name != "nt", reason="Original source-unit native candidate is Windows only")
@pytest.mark.parametrize("kind", ["g1", "g1_with_g2", "resumed", "partial_B"])
def test_real_detached_complete_census_and_original_source_cleanup_protocol(home, tmp_path, kind):
    value, bundle, root = prepare_source(home, tmp_path, kind=kind)
    before = operation_fixture.original_tree(root)
    with operation_fixture.held((home,)) as (group_token, guard):
        result = operation.check_source_census_locked(home, group_token, source=value,
            operation_id=operation_fixture.OPERATION_ID)
        assert operation_fixture.lifecycle.current_guard() is guard
    assert operation_fixture.original_tree(root) == before
    assert result["source_observed"] is result["unit_native_normal_closed"] is result["global_settled"] is True
    assert result["complete_preview"] is result["source_closed"] is result["producer"] is False
    assert result["copy_performed"] is result["data_restored"] is result["write_authority"] is result["sender_authority"] is False
    actual = result["source"]
    current = actual["source_declaration"]["current"]
    assert {row["relative_key"] for row in current["directories"]} == set(bundle["directories"])
    assert {row["relative_key"] for row in current["files"]} == set(bundle["raw_files"])
    assert [(row["relative_key"], row["sha256"], row["byte_size"]) for row in current["files"]] == [
        (key, hashlib.sha256(bundle["raw_files"][key]).hexdigest(), len(bundle["raw_files"][key]))
        for key in sorted(bundle["raw_files"], key=lambda key: key.encode("utf-8"))]
    assert actual["source_begin"]["source_read_bytes"] == actual["source_begin"]["source_read_calls"] == actual["source_begin"]["uncertain_source_read_calls"] == 0
    assert actual["parser_work"]["original_codec_calls"] == 2
    assert actual["parser_work"]["original_codec_input_bytes"] == 2 * sum(len(raw) for raw in bundle["facts"].values())
    assert actual["parser_work"]["native_hash_input_bytes"] == sum(len(raw) for raw in bundle["raw_files"].values())
    assert actual["parser_work"]["raw_parse_calls"] == len(bundle["facts"])
    assert actual["own_parser_work_complete"] is True
    audit = result["native"]["scope_reports"]["source"]
    assert type(audit) is dict and audit["normal_closed"] is True
    assert audit["resource_acquires"] == audit["resource_normal_closes"] > 0
    assert audit["current_resources"] == audit["pending_identities"] == audit["unknown_resources"] == 0
    assert result["counters"]["current_resources"] == result["counters"]["pending_resources"] == 0
    assert not source._LIVE and not source._MANAGERS and not operation._LIVE


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows original source ownership")
def test_real_foreign_thread_source_entry_does_not_poison_parent_first_or_close_actual_readers(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    observed = []
    done = False
    def cancelled():
        nonlocal done
        registrations = list(operation._LIVE.values())
        if done or not registrations or registrations[0].source_context.source_record is None:
            return
        registration, ctx, record = actual_source_record()
        if record.phase != "selection":
            return
        done = True
        first_before = operation._FIRST_PEEK(registration)
        def foreign():
            try:
                source.location_original(ctx.source_token)
            except BaseException as error:
                observed.append(error)
        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join(timeout=20)
        assert not worker.is_alive()
        assert len(observed) == 1 and observed[0].code.endswith("OWNER_MISMATCH")
        assert operation._FIRST_PEEK(registration) is first_before is None
    with operation_fixture.held((home,)) as (group_token, _guard):
        result = operation.check_source_census_locked(home, group_token, source=value,
            operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
    assert done and result["unit_native_normal_closed"] is True


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows callback lifecycle")
def test_callback_cannot_obtain_original_source_closers_from_manager_or_record(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    caught = []
    def cancelled():
        registrations = list(operation._LIVE.values())
        if not registrations or registrations[0].source_context.source_record is None:
            return
        registration, ctx, record = actual_source_record()
        manager = next(item for item in source._MANAGERS.values() if item.record is record)
        try:
            source._trusted_scope_callbacks_original(manager)
        except BaseException as error:
            caught.append(error)
        assert operation._FIRST_PEEK(registration) is caught[0]
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(BaseException) as rejected:
            operation.check_source_census_locked(home, group_token, source=value,
                operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
    assert caught and rejected.value is caught[0]
    assert not source._LIVE and not source._MANAGERS and not operation._LIVE


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows empty source scope cleanup")
def test_source_begin_cancel_before_first_acquire_is_normal_cleanup_without_permanent_debt(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    original_error = StopIteration("original-source-begin-control")
    captured = []
    assert source._failure_blocked_original() is False
    debts_before = dict(operation._NATIVE_DEBTS)
    def cancelled():
        registrations = list(operation._LIVE.values())
        if not registrations:
            return
        ctx = registrations[0].source_context
        if ctx.source_record is not None and ctx.source_record.phase == "begin":
            captured.append(ctx.source_record)
            raise original_error
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(StopIteration) as caught:
            operation.check_source_census_locked(home, group_token, source=value,
                operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
    assert caught.value is original_error and len(captured) == 1
    record = captured[0]
    assert record.location is record.metadata is record.read is None
    assert record.cleanup_attempted is record.cleanup_normal is True
    assert record.unknown is False and source._failure_blocked_original() is False
    assert operation._NATIVE_DEBTS == debts_before
    assert not source._LIVE and not source._MANAGERS and not operation._LIVE


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows original hidden cleanup root")
def test_callback_public_cleanup_code_fault_cannot_replace_captured_actual_release(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    altered = []
    captured = []
    def replacement_factory(cell):
        def replacement():
            cell
            raise AssertionError("mutable public cleanup proxy was dispatched")
        return replacement
    def cancelled():
        registrations = list(operation._LIVE.values())
        if altered or not registrations or registrations[0].source_context.source_record is None:
            return
        _registration, _ctx, record = actual_source_record()
        manager = next(item for item in source._MANAGERS.values() if item.record is record)
        proxy = manager.cleanup
        replacement = replacement_factory(manager)
        assert len(proxy.__closure__) == len(replacement.__closure__) == 1
        altered.append((proxy, proxy.__code__))
        captured.append(record)
        proxy.__code__ = replacement.__code__
    try:
        with operation_fixture.held((home,)) as (group_token, _guard):
            with pytest.raises(BaseException) as caught:
                operation.check_source_census_locked(home, group_token, source=value,
                    operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
        assert altered and caught.value.code.endswith("BINDING_CHANGED")
        assert captured[0].cleanup_attempted is captured[0].cleanup_normal is True
        assert captured[0].unknown is False
    finally:
        for function, code in altered:
            function.__code__ = code
    assert not source._LIVE and not source._MANAGERS and not operation._LIVE


@pytest.mark.parametrize("fake", [object(), {}, None])
def test_caller_values_cannot_enroll_or_supply_a_source_token(fake):
    with pytest.raises(SOURCE_ERRORS):
        source.source_scope_original(fake)
    with pytest.raises(SOURCE_ERRORS):
        source.location_original(fake)
    assert not source._LIVE and not source._MANAGERS


@pytest.mark.skipif(os.name != "nt", reason="Actual source reads then persistent original code pollution")
def test_persistent_capture_code_fault_after_actual_reads_retires_resources_and_source_registries_before_restore(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    capture_function = source.capture_original
    before_code = capture_function.__code__
    captured, original_first = [], []
    changed = False
    debts_before = dict(operation._NATIVE_DEBTS)

    def forbidden_capture(*args, **kwargs):
        raise AssertionError("polluted capture body must never be dispatched")

    def cancelled():
        nonlocal changed
        if changed:
            return
        registrations = list(operation._LIVE.values())
        if not registrations or registrations[0].source_context.source_record is None:
            return
        registration, ctx, record = actual_source_record()
        if record.phase != "captured":
            return
        # These are actual functional ledger observations, not timings or a
        # fabricated census.  Source native readers had to acquire and return
        # physical bytes before this original phase can be reached.
        assert record.read is not None and record.cache is not None
        assert registration.budget_dict["_source_read_calls"] > 0
        assert registration.budget_dict["_source_read_bytes"] > 0
        assert registration.budget_dict["_current_resources"] > 0
        captured.append((registration, ctx, record))
        assert capture_function.__closure__ is forbidden_capture.__closure__ is None
        capture_function.__code__ = forbidden_capture.__code__
        changed = True
        try:
            # Retain the real original verifier's first object.  The code
            # remains polluted through driver cleanup/retirement and all
            # after-finally assertions; test restoration is last.
            operation._integrity_original()
        except BaseException as error:
            assert type(error) is operation.AuthorityReplicaOperationError
            assert operation._FIRST_PEEK(registration) is error
            original_first.append(error)
        assert len(original_first) == 1

    try:
        with operation_fixture.held((home,)) as (group_token, _guard):
            with pytest.raises(BaseException) as caught:
                operation.check_source_census_locked(home, group_token, source=value,
                    operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
            assert changed and len(captured) == len(original_first) == 1
            assert capture_function.__code__ is forbidden_capture.__code__
            assert caught.value is original_first[0]
            registration, _ctx, record = captured[0]
            assert registration.first is record.first is caught.value
            # The source flag can become true only after its original captured
            # native audit verified each source holder's normal close.  Global
            # ledger zero below separately covers coordinator/other scopes.
            assert record.cleanup_attempted is record.cleanup_normal is True
            assert record.unknown is False
            actual = registration.budget_dict
            assert actual["_current_resources"] == actual["_pending_resources"] == actual["_uncertain_resources"] == 0
            assert actual["_resource_pending"] == actual["_resource_leases"] == {}
            assert actual["_active"] is None
            assert not source._LIVE and not source._MANAGERS and not operation._LIVE and not operation._OWNERS
            assert operation._NATIVE_DEBTS == debts_before
            assert source._failure_blocked_original() is False
        assert capture_function.__code__ is forbidden_capture.__code__
    finally:
        capture_function.__code__ = before_code


@pytest.mark.skipif(os.name != "nt", reason="Actual source reads then persistent original record token mutation")
def test_persistent_record_token_mutation_after_actual_reads_keeps_own_first_and_retires_before_restore(home, tmp_path):
    value, _bundle, _root = prepare_source(home, tmp_path)
    replacement_token = object()
    captured = []
    changed = False
    debts_before = dict(operation._NATIVE_DEBTS)

    def cancelled():
        nonlocal changed
        if changed:
            return
        registrations = list(operation._LIVE.values())
        if not registrations or registrations[0].source_context.source_record is None:
            return
        registration, ctx, record = actual_source_record()
        if record.phase != "captured":
            return
        # Preserve this actual enrolled token before changing its writable
        # record mirror.  No fabricated native row or foreign owner is used.
        original_token = ctx.source_token
        assert record.token is original_token
        assert operation._FIRST_PEEK(registration) is registration.first is record.first is None
        assert record.read is not None and record.cache is not None
        assert registration.budget_dict["_source_read_calls"] > 0
        assert registration.budget_dict["_source_read_bytes"] > 0
        assert registration.budget_dict["_current_resources"] > 0
        captured.append((registration, ctx, record, original_token))
        record.token = replacement_token
        changed = True
        assert ctx.source_token is original_token
        assert source._LIVE[id(original_token)] is record
        # Return normally: the original callback's post-record validator must
        # reject the actual token mirror and retain that real first.  Leave
        # the mirror polluted throughout actual cleanup and retirement.

    try:
        with operation_fixture.held((home,)) as (group_token, _guard):
            with pytest.raises(source.AuthorityReplicaSourceError) as caught:
                operation.check_source_census_locked(home, group_token, source=value,
                    operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
            assert changed and len(captured) == 1
            assert caught.value.code.endswith("REGISTRY_CHANGED")
            registration, ctx, record, original_token = captured[0]
            assert record.token is replacement_token
            assert ctx.source_token is original_token
            assert registration.first is record.first is caught.value
            # Normal-close audit flags and global resource settlement are
            # separate assertions; restoring a mirror cannot satisfy either.
            assert record.cleanup_attempted is record.cleanup_normal is True
            assert record.unknown is False
            actual = registration.budget_dict
            assert actual["_current_resources"] == actual["_pending_resources"] == actual["_uncertain_resources"] == 0
            assert actual["_resource_pending"] == actual["_resource_leases"] == {}
            assert actual["_active"] is None
            assert not source._LIVE and not source._MANAGERS and not operation._LIVE and not operation._OWNERS
            assert operation._NATIVE_DEBTS == debts_before
            assert source._failure_blocked_original() is False
        assert record.token is replacement_token and record.first is caught.value
    finally:
        # Restoration is last, after the fixed driver and held-group exits.
        for _registration, _ctx, record, original_token in captured:
            record.token = original_token
