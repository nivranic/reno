"""Unrun r777 Windows source-unit integration candidates.

Root must apply all matching candidates together, review, freeze and select an
isolated native lane.  These tests use original G1 and the actual sealed native
kernel, never supply a ledger, backend, FD, closer, census or fabricated native
counter.  The materialized detached history is synthetic historic declaration
data; its CURRENT files/ACLs/handles/Find/read/EOF/closures are real at execution.
Only fresh QA fixture objects receive initial private ACLs.  Production code
checks existing permissions and does not initialize or repair any ACL.
"""
import asyncio
import ctypes
import hashlib
import os
from pathlib import Path
import threading
import sys

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_source as source
from reno import personal_authority_replica_source_native as native
from reno import personal_authority_replica_selector as selector
from reno import personal_authority_raw_preservation2 as raw2
from reno import personal_preservation_files as private_files
from reno import storage_lifecycle as lifecycle
from tests import test_personal_authority_replica_source as source_fixture
from tests import test_personal_authority_replica_operation as operation_fixture


home = source_fixture.home
other = source_fixture.other
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Actual supported Windows source-unit ABI only")
SOURCE_OPERATION_ID = "abcdef12-1234-4234-8234-123456789abc"
NODE_ID = "99999999-9999-4999-8999-999999999999"
OUTPUT_FAMILY = ".reno-authority-raw-preservation-replicas-v1"
CHUNK = 131072


def prepare_profile_source(home, tmp_path, *, source_home=None):
    """Actual original raw2 create on already isolated/private fresh roots.

    No hand-written identity enters this path.  The real old factory writes
    request/control/payload and records the real old four bases, namespace and
    operation identities.  The source-census kernel must independently reopen
    and bind them; its result remains a readonly census declaration.
    """
    chosen = home if source_home is None else source_home
    node = home.database.parent / "node-identity.json"
    created_node = not node.exists()
    node.write_bytes(("{\"instance_id\":\"" + NODE_ID + "\"}").encode("ascii"))
    # The fixture has already initialized private fresh target roots/node.
    # If this test creates the node first, it initializes ONLY that new object.
    if created_node:
        private_files._windows_acl(node, directory=False, initialize=True)
    with lifecycle.exclusive(chosen):
        created = raw2.create_raw_preservation_locked(chosen, operation_id=SOURCE_OPERATION_ID)
    root = chosen.database.parent / ".reno-authority-raw-preservation-v2" / created.operation_id
    source_value = selector.ProfileRaw2Source(profile=chosen, operation_id=created.operation_id,
        generation=created.generation, expected_request_sha256=created.request_sha256)
    return source_value, root, created


def _invoke(home, source_value, *, callback=None, profiles=None):
    with operation_fixture.held((home,) if profiles is None else profiles) as (group_token, guard):
        result = operation.check_source_census_locked(home, group_token, source=source_value,
            operation_id=operation_fixture.OPERATION_ID, check_cancelled=callback)
        assert lifecycle.current_guard() is guard
    return result


def _assert_closed_result(result):
    assert result["unit_native_normal_closed"] is result["global_settled"] is True
    assert result["complete_preview"] is result["source_closed"] is result["producer"] is False
    assert result["copy_performed"] is result["data_restored"] is result["write_authority"] is result["sender_authority"] is False
    report = result["native"]
    counts = report["global_resources"]
    assert counts["resource_acquires"] == counts["resource_normal_closes"] > 0
    assert counts["current_resources"] == 0
    assert report["global_settled"] is True
    assert report["end"]["current_resources"] == report["end"]["pending_resources"] == 0
    assert report["end"]["uncertain_resource_acquires"] == 0
    assert counts["peak_owned_resources"] == report["end"]["peak_resources"]
    # Actual global peak is a real chronological maximum, not a sum of scope
    # peaks; sources overlap with already live coordinator bases.
    assert counts["peak_owned_resources"] <= sum(scope["peak_owned_resources"] for scope in report["scope_reports"].values())
    for role, scope in report["scope_reports"].items():
        assert scope["normal_closed"] is True
        assert scope["resource_acquires"] == scope["resource_normal_closes"]
        assert scope["current_resources"] == scope["pending_identities"] == scope["unknown_resources"] == 0
        assert scope["incomplete_portable_identities"] == 0
        assert scope["portable_identity_complete"] is True
        assert len(scope["resource_events_sha256"]) == 64
        assert scope["begin"] is not None and scope["end"] is not None
        assert scope["begin"]["elapsed_ns"] <= scope["end"]["elapsed_ns"]
        Q = scope["Q"]
        assert Q["confirmed_write_bytes"] == Q["write_calls"] == Q["uncertain_write_bytes"] == 0
        if role != "source":
            assert Q["source_read_bytes"] == Q["source_read_calls"] == Q["uncertain_source_read_calls"] == 0
    assert result["counters"]["read_bytes"] == sum(scope["Q"]["read_bytes"] for scope in report["scope_reports"].values())
    assert result["counters"]["read_calls"] == sum(scope["Q"]["read_calls"] for scope in report["scope_reports"].values())
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is False


def test_actual_full_package_zero_marker_retained_emptydirs_stream_Q_and_normal_ledger_close(home, tmp_path):
    value, bundle, root = source_fixture.prepare_source(home, tmp_path, kind="g1_with_g2")
    before = operation_fixture.original_tree(root)
    result = _invoke(home, value)
    _assert_closed_result(result)
    assert operation_fixture.original_tree(root) == before
    Q = result["native"]["scope_reports"]["source"]["Q"]
    actual_size = sum(len(raw) for raw in bundle["raw_files"].values())
    assert result["native"]["source"]["S"] == actual_size
    assert Q["read_bytes"] == Q["source_read_bytes"] == Q["package_read_bytes"] == actual_size
    # Each file executes at least ceil(size/chunk) native body calls plus one
    # real EOF call; zero-byte marker has a read(1), not a skipped zero case.
    minimum_calls = sum((len(raw) + CHUNK - 1) // CHUNK + 1 for raw in bundle["raw_files"].values())
    assert Q["read_calls"] == Q["source_read_calls"] >= minimum_calls
    assert Q["uncertain_read_calls"] == Q["uncertain_source_read_calls"] == 0
    assert Q["outer_control_read_bytes"] == Q["observer_node_read_bytes"] == Q["other_native_control_read_bytes"] == 0
    observer = result["native"]["scope_reports"]["observer"]["Q"]
    node_raw = source_fixture.historical.wire({"instance_id": source_fixture.historical.NODE_ID})
    assert observer["read_bytes"] == observer["observer_node_read_bytes"] == len(node_raw)
    assert observer["read_calls"] >= 2
    assert result["native"]["source"]["N"] == len(bundle["raw_files"])
    assert result["native"]["source"]["D"] == len(bundle["directories"])


@pytest.mark.parametrize("distinct", [False, True])
def test_actual_profile_source_uses_own_fourbases_and_actual_original_request_bindings(home, other, tmp_path, distinct):
    chosen = other if distinct else home
    value, root, created = prepare_profile_source(home, tmp_path, source_home=chosen)
    before = operation_fixture.original_tree(root)
    result = _invoke(home, value, profiles=(home, chosen) if distinct else (home,))
    _assert_closed_result(result)
    assert operation_fixture.original_tree(root) == before
    assert result["source_kind"] == "profile_raw2"
    assert result["source"]["parser_work"]["original_codec_calls"] == 3
    location = result["native"]["source"]["location"]
    assert location["original_operation_id"] == created.operation_id
    assert location["namespace_identity"] == {
        "device": str(os.stat(root.parent, follow_symlinks=False).st_dev),
        "inode": str(os.stat(root.parent, follow_symlinks=False).st_ino)}
    assert location["root_identity"] == location["ancestors"][-1]["identity"]
    assert set(location["raw2_profile"]) == {"profile_paths", "roots"}
    assert set(location["raw2_profile"]["roots"]) == {"data", "media", "reports", "checkpoints"}
    for name, row in location["raw2_profile"]["roots"].items():
        actual = os.stat(row["path"], follow_symlinks=False)
        assert row["identity"] == {"device": str(actual.st_dev), "inode": str(actual.st_ino)}


@pytest.mark.parametrize("state", ["namespace_absent", "operation_absent", "exists"])
def test_output_exists_or_two_full_listing_absence_witness_has_actual_parent_identity(home, tmp_path, state):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    namespace = home.database.parent / OUTPUT_FAMILY
    actual_output = namespace / operation_fixture.OPERATION_ID
    if state != "namespace_absent":
        namespace.mkdir()
        private_files._windows_acl(namespace, directory=True, initialize=True)
    if state == "exists":
        actual_output.mkdir()
        private_files._windows_acl(actual_output, directory=True, initialize=True)
    result = _invoke(home, value)
    _assert_closed_result(result)
    bound = result["native"]["output_binding"]
    assert bound["state"] == state
    if state == "exists":
        info = os.stat(actual_output, follow_symlinks=False)
        assert bound["operation_identity"] == {"device": str(info.st_dev), "inode": str(info.st_ino)}
    else:
        assert "namespace_identity" not in bound and "operation_identity" not in bound
        parent = home.database.parent if state == "namespace_absent" else namespace
        info = os.stat(parent, follow_symlinks=False)
        assert bound["held_parent_identity"] == {"device": str(info.st_dev), "inode": str(info.st_ino)}


@pytest.mark.parametrize("bad_node", [b'{"instance_id":"' + NODE_ID.encode("ascii") + b'","instance_id":"' + NODE_ID.encode("ascii") + b'"}',
    b'{"instance_id":"' + NODE_ID.encode("ascii") + b'","extra":0}', b'{"instance_id":"00000000-0000-0000-0000-000000000000"}',
    b"", b"x" * 4097])
def test_actual_observer_strict_duplicate_schema_uuid_cap_preserves_failure_without_unknown_debt(home, tmp_path, bad_node):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    (home.database.parent / "node-identity.json").write_bytes(bad_node)
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(BaseException):
            operation.check_source_census_locked(home, group_token, source=value, operation_id=operation_fixture.OPERATION_ID)
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is False


@pytest.mark.parametrize("alias", ["unit", "holder", "new_holder", "cleanup"])
def test_same_owner_callback_cannot_call_exposed_kernel_aliases_or_obtain_authority_state(home, tmp_path, alias):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    caught = []
    def cancelled():
        registrations = list(operation._LIVE.values())
        if caught or not registrations:
            return
        registration = registrations[0]
        ctx = registration.source_context
        if ctx.source_record is None or ctx.source_record.phase != "selection":
            return
        # No actual authority dictionary is obtained: raw resolver itself is
        # the tested rejected first path. Supplying malformed state must obey
        # the same-owner reentry gate before argument rejection.
        try:
            if alias == "unit":
                native._RESOLVE_UNIT(ctx.unit_key)
            elif alias == "holder":
                native._RESOLVE_HOLDER(object())
            elif alias == "new_holder":
                native._NEW_HOLDER({}, "source", "file_handle", "forged")
            else:
                native._CLEANUP({})
        except BaseException as error:
            caught.append(error)
        assert caught and caught[0].code == "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
        assert operation._FIRST_PEEK(registration) is caught[0]
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(BaseException) as rejected:
            operation.check_source_census_locked(home, group_token, source=value,
                operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
    assert caught and rejected.value is caught[0]
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is False


def test_foreign_thread_kernel_resolution_during_owner_callback_cannot_change_owner_first(home, tmp_path):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    observed, done = [], False
    def cancelled():
        nonlocal done
        registration = next(iter(operation._LIVE.values()))
        ctx = registration.source_context
        if done or ctx.source_record is None or ctx.source_record.phase != "selection":
            return
        done = True
        def foreign():
            try:
                native._RESOLVE_UNIT(ctx.unit_key)
            except BaseException as error:
                observed.append(error)
        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join(timeout=20)
        assert not worker.is_alive()
        assert len(observed) == 1 and observed[0].code.endswith("OWNER_MISMATCH")
        assert operation._FIRST_PEEK(registration) is None
    result = _invoke(home, value, callback=cancelled)
    assert done
    _assert_closed_result(result)


@pytest.mark.parametrize("phase", ["begin", "selection"])
def test_source_begin_or_post_body_cancel_closes_known_resources_without_permanent_debt(home, tmp_path, phase):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    actual_cancel = StopIteration("r777 ordinary actual callback cancellation")
    observed = []
    def cancelled():
        registration = next(iter(operation._LIVE.values()))
        ctx = registration.source_context
        if ctx.source_record is not None and ctx.source_record.phase == phase:
            observed.append(ctx.source_record.begin)
            raise actual_cancel
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(StopIteration) as rejected:
            operation.check_source_census_locked(home, group_token, source=value,
                operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
    assert observed and rejected.value is actual_cancel
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is False


def test_actual_malformed_file_name_metadata_failure_never_reads_body_or_creates_unknown_debt(home, tmp_path):
    value, _bundle, root = source_fixture.prepare_source(home, tmp_path)
    new_file = root / "unrecognized.json"
    new_file.write_bytes(b"{}")
    private_files._windows_acl(new_file, directory=False, initialize=True)
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(BaseException):
            operation.check_source_census_locked(home, group_token, source=value, operation_id=operation_fixture.OPERATION_ID)
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is False


def test_canonical_source_output_ancestor_alias_rejects_before_any_original_native_acquire(home, tmp_path):
    value = selector.DetachedRaw2Source(package_root=home.database.parent / OUTPUT_FAMILY / operation_fixture.OPERATION_ID,
        generation=1, expected_request_sha256="0" * 64)
    with operation_fixture.held((home,)) as (group_token, _guard):
        with pytest.raises(native.AuthorityReplicaSourceNativeError) as rejected:
            operation.check_source_census_locked(home, group_token, source=value, operation_id=operation_fixture.OPERATION_ID)
    assert rejected.value.code.endswith("SOURCE_OUTPUT_ALIAS")
    assert not operation._LIVE and native._failure_blocked_original() is False


def test_actual_source_root_reparse_known_handle_close_keeps_identity_incomplete_without_permanent_debt(home, tmp_path):
    """No substituted closer/ledger: real OPEN_REPARSE_POINT H then rejection.

    This exact fresh link is outside the original package. Only the host's
    actual symlink permission can establish the trigger; lack of permission
    is a named skip, not a synthetic reparse or successful native fault lane.
    """
    value, _bundle, root = source_fixture.prepare_source(home, tmp_path)
    before = operation_fixture.original_tree(root)
    alias = tmp_path / "source-root-reparse"
    try:
        os.symlink(root, alias, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Actual Windows symlink privilege is unavailable for this fresh QA reparse lane")
        raise
    records = []
    def observe_original_record():
        registration = next(iter(operation._LIVE.values()))
        ctx = registration.source_context
        if not records and ctx.source_record is not None:
            records.append(ctx.source_record)
    try:
        actual = os.stat(alias, follow_symlinks=False)
        assert actual.st_file_attributes & 1024
        reparse_source = selector.DetachedRaw2Source(package_root=alias,
            generation=value.generation, expected_request_sha256=value.expected_request_sha256)
        with operation_fixture.held((home,)) as (group_token, _guard):
            with pytest.raises(native.AuthorityReplicaSourceNativeError) as rejected:
                operation.check_source_census_locked(home, group_token, source=reparse_source,
                    operation_id=operation_fixture.OPERATION_ID, check_cancelled=observe_original_record)
        assert rejected.value.code.endswith("IDENTITY_CHANGED")
        assert len(records) == 1
        record = records[0]
        assert record.first is rejected.value
        assert record.cleanup_attempted is record.cleanup_normal is True
        assert record.unknown is False
        assert record.location is record.metadata is record.read is record.encoded is record.selection is None
        assert not operation._LIVE and not source._LIVE and not source._MANAGERS
        assert native._failure_blocked_original() is source._failure_blocked_original() is False
        # A separate actual original RP1 enrollment must still be possible;
        # no reset/clear/adopt API is used to recover from the ordinary failure.
        _assert_closed_result(_invoke(home, value))
        assert operation_fixture.original_tree(root) == before
    finally:
        alias.unlink(missing_ok=True)


@pytest.mark.parametrize("mutation", ["sys_audit", "cast_argtypes", "cast_restype", "cast_errcheck",
    "wstring_argtypes", "wstring_restype", "wstring_errcheck", "wchar_pointer", "void_pointer",
    "wchar_buffer_class", "byte_buffer_class"])
def test_actual_source_callback_ctypes_dispatch_or_prototype_mutation_is_rejected_before_new_helper_dispatch(home, tmp_path, mutation):
    """Real helper metadata mutation; no fabricated native return or authority.

    Original properties/cache entries/classes are restored inside the held
    G1 scope after the operation has unwound its actual known resources.
    """
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    dispatched, changed = [], []
    def poison(*arguments):
        dispatched.append(arguments)
        raise AssertionError("Unadmitted ctypes helper was dispatched")
    if mutation == "sys_audit":
        original = sys.audit
        def change():
            sys.audit = poison
        def restore():
            sys.audit = original
    elif mutation.startswith("cast_") or mutation.startswith("wstring_"):
        pointer = ctypes._cast if mutation.startswith("cast_") else ctypes._wstring_at
        field = mutation.split("_", 1)[1]
        original = getattr(pointer, field)
        replacement = poison if field == "errcheck" else ctypes.c_void_p if field == "restype" else list(original)
        def change():
            setattr(pointer, field, replacement)
        def restore():
            if field == "errcheck" and original is None:
                delattr(pointer, field)
            else:
                setattr(pointer, field, original)
    elif mutation.endswith("_pointer"):
        key = ctypes.c_wchar if mutation == "wchar_pointer" else ctypes.c_void_p
        cache = ctypes._pointer_type_cache
        original = cache[key]
        replacement = native._LPVOID_POINTER if mutation == "wchar_pointer" else native._WCHAR_POINTER
        def change():
            cache[key] = replacement
        def restore():
            cache[key] = original
    else:
        chosen = native._WCHAR_BUFFER_TYPE if mutation == "wchar_buffer_class" else native._BYTE_BUFFER_TYPE
        original = chosen.__dict__.get("__init__")
        def change():
            chosen.__init__ = poison
        def restore():
            if original is None:
                del chosen.__init__
            else:
                chosen.__init__ = original
    def cancelled():
        registration = next(iter(operation._LIVE.values()))
        ctx = registration.source_context
        if changed or ctx.source_record is None or ctx.source_record.phase != "begin":
            return
        changed.append(True)
        change()
    with operation_fixture.held((home,)) as (group_token, _guard):
        try:
            with pytest.raises(BaseException) as rejected:
                operation.check_source_census_locked(home, group_token, source=value,
                    operation_id=operation_fixture.OPERATION_ID, check_cancelled=cancelled)
        finally:
            if changed:
                restore()
    assert changed and rejected.value.code.endswith("RECORD_CHANGED")
    assert not dispatched
    assert not operation._LIVE and not source._LIVE and not source._MANAGERS
    assert native._failure_blocked_original() is source._failure_blocked_original() is False


def test_unrelated_real_pointer_cache_addition_during_callback_does_not_freeze_whole_cache(home, tmp_path):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    class UnrelatedScalar(ctypes.c_int):
        pass
    added = []
    cache = ctypes._pointer_type_cache
    def cancelled():
        registration = next(iter(operation._LIVE.values()))
        ctx = registration.source_context
        if added or ctx.source_record is None or ctx.source_record.phase != "begin":
            return
        added.append(ctypes.POINTER(UnrelatedScalar))
    try:
        result = _invoke(home, value, callback=cancelled)
        assert len(added) == 1 and cache[UnrelatedScalar] is added[0]
        _assert_closed_result(result)
    finally:
        cache.pop(UnrelatedScalar, None)


def test_actual_admitted_task_subclass_retains_same_cleanup_actor_identity(home, tmp_path):
    value, _bundle, _root = source_fixture.prepare_source(home, tmp_path)
    class ActualTask(asyncio.Task):
        pass
    async def run():
        actual_task = asyncio.current_task()
        assert type(actual_task) is ActualTask
        result = _invoke(home, value)
        assert asyncio.current_task() is actual_task
        _assert_closed_result(result)
    actual_loop = asyncio.new_event_loop()
    try:
        original_task = ActualTask(run(), loop=actual_loop)
        actual_loop.run_until_complete(original_task)
    finally:
        actual_loop.close()
