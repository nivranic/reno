"""r755 independent Windows DATA-chain tests; draft 05, not executed.

Original product entry/G1 alone issue the operation and ledger. Hand-written
oracles, exact original scope inspectors, ordinary F and two dedicated unknown
child lanes are proposed here. Late/partial faults without lawful seams remain
explicitly unverified. Product/tests/harness source review is still required.
Root alone may apply, import, freeze and run. This module never spawns a child.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import gc
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from types import BuiltinFunctionType
import uuid
import weakref

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_directory_native as native
from reno import personal_authority_replica_group as group
from reno import personal_authority_replica_budget as budget
from reno import personal_authority_replica_selector as selector
from reno import personal_authority_preservation_routing as routing
from reno import personal_authority_raw_preservation as raw1
from reno import personal_authority_raw_preservation2 as raw2
from reno import storage_lifecycle as lifecycle


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows DATA-chain tests require native Windows filesystem APIs"
)

OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_OPERATION_ID = "abcdef12-1234-4234-8234-123456789abc"
SOURCE_SHA = "0123456789abcdef" * 4
EXPECTED_RP1_SHA = "fedcba9876543210" * 4
FIELDS = ("database", "media", "reports", "checkpoints")
MODES_AND_SOURCES = [
    ("preview", "detached"), ("preview", "same"), ("preview", "distinct"),
    ("create", "detached"), ("create", "same"), ("create", "distinct"),
    ("resume", "detached"), ("resume", "same"), ("resume", "distinct"),
    ("verify", "none"),
]
IO_FIELDS = {
    "read_bytes", "source_read_bytes", "read_calls", "source_read_calls", "seek_calls",
    "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes",
}
SNAPSHOT_FIELDS = IO_FIELDS | {
    "elapsed_ns", "current_resources", "peak_resources", "pending_resources",
    "uncertain_resource_acquires",
}
ATTRIBUTABLE_FIELDS = IO_FIELDS | {
    "package_read_bytes", "outer_control_read_bytes", "observer_node_read_bytes",
    "other_native_control_read_bytes",
}
RESULT_FIELDS = {
    "format_version", "kind", "operation_id", "run_id", "mode", "scope_id",
    "directory_count", "resource_acquires", "resource_normal_closes",
    "peak_owned_resources", "resource_events_sha256", "unit_begin", "unit_end",
    "unit_attributable", "unit_native_normal_closed", "operation_context_returned",
    "original_registry_retired", "outer_lifecycle_exited", "source_observed",
    "source_native_closure", "target_native_closure", "acl_validated", "copy_performed",
    "data_restored", "write_authority", "sender_authority",
}
ERROR = operation.AuthorityReplicaOperationError
ERROR_PREFIX = "AUTHORITY_REPLICA_OPERATION1_"
KNOWN_ERRORS = (
    ERROR, native.AuthorityReplicaDirectoryError, group.AuthorityReplicaGroupError, selector.AuthorityReplicaSelectorError,
    budget.AuthorityReplicaBudgetError, lifecycle.StorageLifecycleError,
)
RESOURCE_EVENT_DOMAIN = b"reno-authority-replica-runtime-v1\0resource-events\0"


def canonical(value):
    # This oracle uses stdlib directly, not any product encoder/digest helper.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def path_key(value):
    return os.path.normcase(os.path.normpath(str(value)))


def data_chain(profile):
    data = profile.database.parent
    require(data.is_absolute() and len(data.drive) == 2 and (data.drive[1] == ':'), 'r755 independent assertion failed at retained draft04 source line 91')
    require(data.anchor.endswith('\\') and (not data.anchor.startswith('\\\\')), 'r755 independent assertion failed at retained draft04 source line 92')
    chain, current = [], Path(data.anchor)
    chain.append(current)
    for part in data.parts[1:]:
        require(part not in ('', '.', '..') and ':' not in part and ('\x00' not in part), 'r755 independent assertion failed at retained draft04 source line 96')
        require(not part.endswith((' ', '.')), 'r755 independent assertion failed at retained draft04 source line 97')
        current = current / part
        chain.append(current)
    require(path_key(chain[-1]) == path_key(data), 'r755 independent assertion failed at retained draft04 source line 100')
    require(1 <= len(chain) <= 129, 'r755 independent assertion failed at retained draft04 source line 101')
    return tuple(chain)


def original_identities(chain):
    identities = []
    for path in chain:
        actual = os.stat(path, follow_symlinks=False)
        require(stat.S_ISDIR(actual.st_mode), 'r755 independent assertion failed at retained draft04 source line 109')
        require(actual.st_file_attributes & 1024 == 0, 'r755 independent assertion failed at retained draft04 source line 110')
        require(type(actual.st_dev) is type(actual.st_ino) is int, 'r755 independent assertion failed at retained draft04 source line 111')
        require(actual.st_dev >= 0 and actual.st_ino > 0, 'r755 independent assertion failed at retained draft04 source line 112')
        device, inode = str(actual.st_dev), str(actual.st_ino)
        require(len(device) <= 40 and len(inode) <= 40, 'r755 independent assertion failed at retained draft04 source line 114')
        identities.append({"device": device, "inode": inode})
    return tuple(identities)


def event_oracle(identities):
    # Hand-written H/H2/H3 graph: no product registry/counter drives this graph.
    n, events = len(identities), []
    def emit(resource_id, action, identity):
        events.append({
            "sequence": len(events) + 1,
            "resource_id": "r%08d" % resource_id,
            "kind": "directory_handle",
            "action": action,
            "identity_sha256": hashlib.sha256(canonical(identity)).hexdigest(),
        })
    for index, identity in enumerate(identities):
        emit(2 * index, "acquired", identity)
        emit(2 * index + 1, "acquired", identity)
        emit(2 * index + 1, "normal_closed", identity)
    for close_index, index in enumerate(range(n - 1, -1, -1)):
        identity = identities[index]
        transient = 2 * n + close_index
        emit(transient, "acquired", identity)
        emit(transient, "normal_closed", identity)
        emit(2 * index, "normal_closed", identity)
    return events


def plain(value):
    if type(value) is dict:
        require(all((type(key) is str for key in value)), 'r755 independent assertion failed at retained draft04 source line 145')
        for child in value.values():
            plain(child)
    elif type(value) is list:
        for child in value:
            plain(child)
    else:
        require(value is None or type(value) in (bool, str, int), 'r755 independent assertion failed at retained draft04 source line 152')


def assert_uuid(value):
    require(type(value) is str, 'r755 independent assertion failed at retained draft04 source line 156')
    parsed = uuid.UUID(value)
    require(parsed.int and str(parsed) == value, 'r755 independent assertion failed at retained draft04 source line 158')


def assert_result(result, profile, mode, chain, identities):
    require(type(result) is dict and set(result) == RESULT_FIELDS, 'r755 independent assertion failed at retained draft04 source line 162')
    plain(result)
    require(type(result['format_version']) is int and result['format_version'] == 1, 'r755 independent assertion failed at retained draft04 source line 164')
    require(result['kind'] == 'replica_target_data_chain_check', 'r755 independent assertion failed at retained draft04 source line 165')
    require(result['operation_id'] == OPERATION_ID and result['mode'] == mode, 'r755 independent assertion failed at retained draft04 source line 166')
    assert_uuid(result["run_id"])
    assert_uuid(result["scope_id"])
    n = len(chain)
    require(type(result['directory_count']) is int and result['directory_count'] == n, 'r755 independent assertion failed at retained draft04 source line 170')
    require(type(result['resource_acquires']) is int and result['resource_acquires'] == 3 * n, 'r755 independent assertion failed at retained draft04 source line 171')
    require(type(result['resource_normal_closes']) is int and result['resource_normal_closes'] == 3 * n, 'r755 independent assertion failed at retained draft04 source line 172')
    require(type(result['peak_owned_resources']) is int and result['peak_owned_resources'] == n + 1, 'r755 independent assertion failed at retained draft04 source line 173')
    expected_events = event_oracle(identities)
    expected_digest = hashlib.sha256(RESOURCE_EVENT_DOMAIN + canonical(expected_events)).hexdigest()
    require(result['resource_events_sha256'] == expected_digest, 'r755 independent assertion failed at retained draft04 source line 176')
    for field in ("unit_native_normal_closed", "operation_context_returned", "original_registry_retired"):
        require(result[field] is True, 'r755 independent assertion failed at retained draft04 source line 178')
    for field in ("outer_lifecycle_exited", "source_observed", "acl_validated",
                  "copy_performed", "data_restored", "write_authority", "sender_authority"):
        require(result[field] is False, 'r755 independent assertion failed at retained draft04 source line 181')
    require(result['source_native_closure'] == result['target_native_closure'] == 'not_established', 'r755 independent assertion failed at retained draft04 source line 182')
    begin, end = result["unit_begin"], result["unit_end"]
    for snapshot in (begin, end):
        require(type(snapshot) is dict and set(snapshot) == SNAPSHOT_FIELDS, 'r755 independent assertion failed at retained draft04 source line 185')
        require(all((type(value) is int and value >= 0 for value in snapshot.values())), 'r755 independent assertion failed at retained draft04 source line 186')
        require(all((snapshot[key] == 0 for key in IO_FIELDS)), 'r755 independent assertion failed at retained draft04 source line 187')
        require(snapshot['current_resources'] == snapshot['pending_resources'] == 0, 'r755 independent assertion failed at retained draft04 source line 188')
        require(snapshot['uncertain_resource_acquires'] == 0, 'r755 independent assertion failed at retained draft04 source line 189')
    require(begin['peak_resources'] == 0, 'r755 independent assertion failed at retained draft04 source line 190')
    require(end['peak_resources'] == n + 1, 'r755 independent assertion failed at retained draft04 source line 191')
    require(0 <= begin['elapsed_ns'] <= end['elapsed_ns'] < 1800000000000, 'r755 independent assertion failed at retained draft04 source line 192')
    attributable = result["unit_attributable"]
    require(type(attributable) is dict and set(attributable) == ATTRIBUTABLE_FIELDS, 'r755 independent assertion failed at retained draft04 source line 194')
    require(all((type(value) is int and value == 0 for value in attributable.values())), 'r755 independent assertion failed at retained draft04 source line 195')
    # Independent final path observation; this is not an external WinAPI trace.
    require(original_identities(chain) == identities, 'r755 independent assertion failed at retained draft04 source line 197')
    require(path_key(chain[-1]) == path_key(profile.database.parent), 'r755 independent assertion failed at retained draft04 source line 198')


def make_profile(root):
    return lifecycle.StorageProfile(root / "data/reno.db", root / "media",
                                    root / "reports", root / "checkpoints")


def require_qa_root(path):
    qa_value = os.environ.get("RENO_QA_ROOT")
    require(qa_value, 'Root must freeze RENO_QA_ROOT before execution')
    qa_root = Path(qa_value)
    require(qa_root.is_absolute(), 'r755 independent assertion failed at retained draft04 source line 210')
    path.relative_to(qa_root)


@pytest.fixture
def home(tmp_path):
    require(os.name == 'nt', 'Normal core is a Windows lane; it must not be skipped')
    require_qa_root(tmp_path)
    profile = make_profile(tmp_path / "home")
    lifecycle.initialize(profile)
    (profile.media / "preserved.bin").write_bytes(b"r755 original synthetic\x00\xff")
    (profile.reports / "empty").mkdir()
    return profile


@pytest.fixture
def other(tmp_path):
    require_qa_root(tmp_path)
    profile = make_profile(tmp_path / "other")
    lifecycle.initialize(profile)
    return profile


@contextmanager
def held(profiles):
    # Actual original G1, and its actual closure are separate from RP1 unit closure.
    with group.checked_exclusive_many(tuple(profiles)) as group_token:
        guard = group.guard_original(group_token)
        require(type(guard) is lifecycle.StorageGuard, 'r755 independent assertion failed at retained draft04 source line 238')
        require(lifecycle.current_guard() is guard, 'r755 independent assertion failed at retained draft04 source line 239')
        yield group_token, guard
        require(lifecycle.current_guard() is guard, 'r755 independent assertion failed at retained draft04 source line 241')
    require(lifecycle.current_guard() is None, 'r755 independent assertion failed at retained draft04 source line 242')
    closed = group.consume_closed_original(group_token)
    require(closed['native_normal_closed'] is True, 'r755 independent assertion failed at retained draft04 source line 244')
    require(closed['lifecycle_context_returned'] is True, 'r755 independent assertion failed at retained draft04 source line 245')
    require(closed['native_resources_acquired'] == closed['native_resources_normal_closed'] > 0, 'r755 independent assertion failed at retained draft04 source line 246')
    require(closed['current_resources'] == closed['pending_resources'] == 0, 'r755 independent assertion failed at retained draft04 source line 247')
    require(closed['write_authority'] is closed['sender_authority'] is False, 'r755 independent assertion failed at retained draft04 source line 248')


def source_for(profile, other_profile, root, kind):
    if kind == "none":
        return None
    if kind == "detached":
        return selector.DetachedRaw2Source(
            package_root=root / "uncreated-detached-package", generation=1,
            expected_request_sha256=SOURCE_SHA)
    require(kind in ('same', 'distinct'), 'r755 independent assertion failed at retained draft04 source line 258')
    return selector.ProfileRaw2Source(
        profile=profile if kind == "same" else other_profile,
        operation_id=SOURCE_OPERATION_ID, generation=2,
        expected_request_sha256=SOURCE_SHA)


def arguments(profile, group_token, root, *, mode="create", source=None, callback=None):
    if source is None and mode != "verify":
        source = source_for(profile, None, root, "detached")
    return dict(group_token=group_token, source=source, mode=mode,
                operation_id=OPERATION_ID,
                expected_request_sha256=EXPECTED_RP1_SHA if mode in ("resume", "verify") else None,
                check_cancelled=callback)


def check_chain(profile, group_token, root, **options):
    return operation.check_target_data_chain_locked(
        profile, **arguments(profile, group_token, root, **options))


def check_legacy_checker(profile, group_token, root, **options):
    return operation.check_operation_locked(
        profile, **arguments(profile, group_token, root, **options))


def assert_zero_checker(result):
    require(result['kind'] == 'replica_operation_check', 'r755 independent assertion failed at retained draft04 source line 285')
    require(type(result['rp1_owned_native_resources']) is int, 'r755 independent assertion failed at retained draft04 source line 286')
    require(result['rp1_owned_native_resources'] == 0, 'r755 independent assertion failed at retained draft04 source line 287')
    require(result['source_observed'] is result['copy_performed'] is False, 'r755 independent assertion failed at retained draft04 source line 288')
    require(result['data_restored'] is result['write_authority'] is result['sender_authority'] is False, 'r755 independent assertion failed at retained draft04 source line 289')
    counters = result["counters"]
    require(type(counters) is dict and set(counters) == SNAPSHOT_FIELDS, 'r755 independent assertion failed at retained draft04 source line 291')
    for key, value in counters.items():
        require(type(value) is int and value >= 0, 'r755 independent assertion failed at retained draft04 source line 293')
        if key != "elapsed_ns":
            require(value == 0, 'r755 independent assertion failed at retained draft04 source line 295')


@pytest.mark.parametrize("mode,source_kind", MODES_AND_SOURCES)
def test_real_directory_chain_mode_source_matrix(home, other, tmp_path, mode, source_kind):
    source = source_for(home, other, tmp_path, source_kind)
    profiles = (home, other) if source_kind == "distinct" else (home,)
    chain = data_chain(home)
    require(not (tmp_path / 'uncreated-detached-package').exists(), 'r755 independent assertion failed at retained draft04 source line 303')
    with held(profiles) as (group_token, _guard):
        identities = original_identities(chain)
        result = check_chain(home, group_token, tmp_path, mode=mode, source=source)
        assert_result(result, home, mode, chain, identities)
        require(not operation._LIVE and not operation._OWNERS and not operation._NATIVE_DEBTS, "normal operation has live/native debt state")
        assert_zero_checker(check_legacy_checker(home, group_token, tmp_path, mode=mode, source=source))
    require(not (tmp_path / 'uncreated-detached-package').exists(), 'r755 independent assertion failed at retained draft04 source line 310')
    require((home.media / 'preserved.bin').read_bytes() == b'r755 original synthetic\x00\xff', 'r755 independent assertion failed at retained draft04 source line 311')
    require(list((home.reports / 'empty').iterdir()) == [], 'r755 independent assertion failed at retained draft04 source line 312')


def test_real_deeper_chinese_chain_adds_one_directory(home, tmp_path):
    # Both are new legal QA profiles; no drive-root n=1 fixture or ACL change.
    shallow = make_profile(tmp_path / "深度")
    deeper = make_profile(tmp_path / "深度" / "绍兴")
    lifecycle.initialize(shallow)
    lifecycle.initialize(deeper)
    results, lengths = [], []
    for profile in (shallow, deeper):
        chain = data_chain(profile)
        with held((profile,)) as (group_token, _guard):
            identities = original_identities(chain)
            result = check_chain(profile, group_token, tmp_path)
            assert_result(result, profile, "create", chain, identities)
        results.append(result)
        lengths.append(len(chain))
    require(lengths[1] == lengths[0] + 1, 'r755 independent assertion failed at retained draft04 source line 330')
    require(results[1]['resource_acquires'] == results[0]['resource_acquires'] + 3, 'r755 independent assertion failed at retained draft04 source line 331')
    require(results[1]['resource_normal_closes'] == results[0]['resource_normal_closes'] + 3, 'r755 independent assertion failed at retained draft04 source line 332')
    require(results[1]['peak_owned_resources'] == results[0]['peak_owned_resources'] + 1, 'r755 independent assertion failed at retained draft04 source line 333')
    require(results[0]['scope_id'] != results[1]['scope_id'], 'r755 independent assertion failed at retained draft04 source line 334')
    require(results[0]['run_id'] != results[1]['run_id'], 'r755 independent assertion failed at retained draft04 source line 335')


def test_normal_unit_leaves_caller_g1_held_and_checker_zero_effect(home, tmp_path):
    chain = data_chain(home)
    with held((home,)) as (group_token, guard):
        identities = original_identities(chain)
        assert_zero_checker(check_legacy_checker(home, group_token, tmp_path))
        result = check_chain(home, group_token, tmp_path)
        assert_result(result, home, "create", chain, identities)
        require(result['outer_lifecycle_exited'] is False, 'r755 independent assertion failed at retained draft04 source line 345')
        require(lifecycle.current_guard() is guard, 'r755 independent assertion failed at retained draft04 source line 346')
        require(lifecycle.require_guard(home, exclusive=True) is guard, 'r755 independent assertion failed at retained draft04 source line 347')
        assert_zero_checker(check_legacy_checker(home, group_token, tmp_path))


@pytest.mark.parametrize("n", [1, 129])
def test_pure_boundary_oracle_includes_anchor_and_is_not_native_coverage(n):
    # Pure synthetic identities only. This is not a root-volume native case.
    identities = tuple({"device": "0", "inode": str(index + 1)} for index in range(n))
    events = event_oracle(identities)
    acquired = [event for event in events if event["action"] == "acquired"]
    closed = [event for event in events if event["action"] == "normal_closed"]
    require(len(acquired) == len(closed) == 3 * n, 'r755 independent assertion failed at retained draft04 source line 358')
    require(len(events) == 6 * n, 'r755 independent assertion failed at retained draft04 source line 359')
    require([event['sequence'] for event in events] == list(range(1, 6 * n + 1)), 'r755 independent assertion failed at retained draft04 source line 360')
    require([event['resource_id'] for event in acquired] == ['r%08d' % index for index in range(3 * n)], 'r755 independent assertion failed at retained draft04 source line 361')
    current, peak = set(), 0
    for event in events:
        if event["action"] == "acquired":
            require(event['resource_id'] not in current, 'r755 independent assertion failed at retained draft04 source line 365')
            current.add(event["resource_id"])
            peak = max(peak, len(current))
        else:
            current.remove(event["resource_id"])
    require(not current and peak == n + 1, 'r755 independent assertion failed at retained draft04 source line 370')


def captured_operation():
    # Read only the fixed factory's real row; no caller-made owner or ledger.
    rows = list(operation._LIVE.values())
    require(len(rows) == 1, 'r755 independent assertion failed at retained draft04 source line 376')
    registration = rows[0]
    record, token = registration.record, registration.token
    require(operation._LIVE[id(token)] is registration and record.token is token, 'r755 independent assertion failed at retained draft04 source line 379')
    require(type(record.owner) is tuple and len(record.owner) == 3, 'r755 independent assertion failed at retained draft04 source line 380')
    require(record.owner[0] == os.getpid() and record.owner[1] == threading.get_ident(), 'r755 independent assertion failed at retained draft04 source line 381')
    try:
        actual_task = asyncio.current_task()
    except RuntimeError:
        actual_task = None
    require(record.owner[2] is actual_task, 'r755 independent assertion failed at retained draft04 source line 386')
    actual_budget = record.budget
    require(type(actual_budget) is budget.ReplicaBudget, 'r755 independent assertion failed at retained draft04 source line 388')
    dictionary = object.__getattribute__(actual_budget, "__dict__")
    require(type(dictionary) is dict and all((type(key) is str for key in dictionary)), 'r755 independent assertion failed at retained draft04 source line 390')
    require(type(dictionary['_started']) is int and dictionary['_started'] >= 0, 'r755 independent assertion failed at retained draft04 source line 391')
    require(dictionary['_in_callback'] is True, 'r755 independent assertion failed at retained draft04 source line 392')
    require(dictionary['_pending_resources'] == 0 and (not dictionary['_resource_pending']), 'r755 independent assertion failed at retained draft04 source line 393')
    require(record.callback_active is True, 'r755 independent assertion failed at retained draft04 source line 394')
    capture = {"registration": registration, "record": record, "token": token,
               "budget": actual_budget, "dictionary": dictionary,
               "started": dictionary["_started"], "current": dictionary["_current_resources"]}
    return capture_native_scope(capture)


def assert_ordinary_retired(capture, *, accounting_settled=True):
    require(id(capture["token"]) not in operation._LIVE, "original operation is still registered")
    require(not operation._OWNERS and not operation._NATIVE_DEBTS, "ordinary operation retains owner/native debt")
    if accounting_settled:
        dictionary = capture["dictionary"]
        require(dictionary['_current_resources'] == dictionary['_pending_resources'] == 0, 'r755 independent assertion failed at retained draft04 source line 406')
        require(not dictionary['_resource_leases'] and (not dictionary['_resource_pending']), 'r755 independent assertion failed at retained draft04 source line 407')
    assert_scope_terminal(capture, accounting_settled=accounting_settled)


def assert_original_first(capture, first):
    require(capture['record'].first is first, 'r755 independent assertion failed at retained draft04 source line 412')
    require(capture['dictionary']['_first'] is first, 'r755 independent assertion failed at retained draft04 source line 413')


def known_error(call, *args, **kwargs):
    with pytest.raises(KNOWN_ERRORS) as raised:
        call(*args, **kwargs)
    return raised.value


def assert_new_normal(profile, group_token, root):
    chain = data_chain(profile)
    identities = original_identities(chain)
    assert_result(check_chain(profile, group_token, root), profile, "create", chain, identities)
    assert_zero_checker(check_legacy_checker(profile, group_token, root))


def test_real_checkpoints_retain_one_original_record_budget_and_started(home, tmp_path):
    captures = []
    def callback():
        capture = captured_operation()
        require(capture['record'].first is None and capture['dictionary']['_first'] is None, 'r755 independent assertion failed at retained draft04 source line 433')
        captures.append(capture)
    chain = data_chain(home)
    with held((home,)) as (group_token, _guard):
        identities = original_identities(chain)
        result = check_chain(home, group_token, tmp_path, callback=callback)
        assert_result(result, home, "create", chain, identities)
        require(len(captures) == len(chain) + 2, "normal native checkpoint count mismatch")
        first = captures[0]
        require(first['current'] == 0, 'r755 independent assertion failed at retained draft04 source line 442')
        require(any((capture['current'] == len(chain) for capture in captures)), 'r755 independent assertion failed at retained draft04 source line 443')
        for capture in captures:
            require(capture["registration"] is first["registration"], "original registration was replaced")
            require(capture["record"] is first["record"] and capture["token"] is first["token"], "original record/token replaced")
            require(capture["budget"] is first["budget"], "original ledger was replaced")
            require(capture["dictionary"] is first["dictionary"] and capture["started"] == first["started"], "original ledger dictionary/start replaced")
        assert_ordinary_retired(first)


@pytest.mark.parametrize("position", ["before", "held"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit, StopIteration])
def test_callback_baseexception_keeps_original_first_and_cleanup(home, tmp_path, position, error_type):
    first = error_type("original native-unit callback failure")
    captures = []
    def callback():
        capture = captured_operation()
        if not captures and ((position == "before" and capture["current"] == 0)
                             or (position == "held" and capture["current"] > 0)):
            captures.append(capture)
            raise first
    with held((home,)) as (group_token, _guard):
        with pytest.raises(error_type) as raised:
            check_chain(home, group_token, tmp_path, callback=callback)
        require(raised.value is first and len(captures) == 1, 'r755 independent assertion failed at retained draft04 source line 466')
        assert_original_first(captures[0], first)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("route", ["new", "new_bad", "checker", "checker_bad", "raw1", "raw2", "observer"])
def test_reentry_with_owned_h_swallowed_error_stays_first_and_cleanup(home, tmp_path, route):
    captures, caught = [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            capture = captured_operation()
            if captures or capture["current"] == 0:
                return
            captures.append(capture)
            def nested():
                if route == "new":
                    return check_chain(home, group_token, tmp_path)
                if route == "new_bad":
                    return operation.check_target_data_chain_locked(unexpected=object())
                if route == "checker":
                    return check_legacy_checker(home, group_token, tmp_path)
                if route == "checker_bad":
                    return operation.check_operation_locked(unexpected=object())
                if route in ("raw1", "raw2"):
                    module = raw1 if route == "raw1" else raw2
                    return module.create_raw_preservation_locked(home, operation_id=SOURCE_OPERATION_ID)
                return lifecycle.current_guard()
            first = known_error(nested)
            require(type(first) is ERROR and first.code == ERROR_PREFIX + 'REENTRANT', 'r755 independent assertion failed at retained draft04 source line 495')
            caught.append(first)
            assert_original_first(capture, first)
        terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(len(captures) == len(caught) == 1 and terminal is caught[0], 'r755 independent assertion failed at retained draft04 source line 499')
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


def trap_code(function):
    # An explicit F code replacement, never used as a normal caller/helper.
    def replacement(*args, **kwargs):
        _R755_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("changed original downstream function executed")
    return replacement.__code__.replace(co_freevars=function.__code__.co_freevars)


@pytest.mark.parametrize("field", ["__getattribute__", "__setattr__"])
@pytest.mark.parametrize("path", ["return", "observer", "restore"])
def test_budget_attribute_fault_with_owned_h_preserves_trusted_cleanup(home, tmp_path, field, path, monkeypatch):
    captures, caught, hooks = [], [], []
    def hostile_getattribute(instance, name):
        hooks.append("getattribute")
        raise AssertionError("changed actual budget getattribute executed")
    def hostile_setattr(instance, name, value):
        hooks.append("setattr")
        raise AssertionError("changed actual budget setattr executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                actual_type = type(capture["budget"])
                require(actual_type is budget.ReplicaBudget, 'r755 independent assertion failed at retained draft04 source line 530')
                replacement = hostile_getattribute if field == "__getattribute__" else hostile_setattr
                patch.setattr(actual_type, field, replacement)
                if path != "return":
                    caught.append(known_error(lifecycle.current_guard))
                if path == "restore":
                    patch.undo()
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(len(captures) == 1 and hooks == [], 'r755 independent assertion failed at retained draft04 source line 538')
        if path == "return":
            require(caught == [], 'r755 independent assertion failed at retained draft04 source line 540')
        else:
            require(len(caught) == 1 and terminal is caught[0], 'r755 independent assertion failed at retained draft04 source line 542')
        assert_original_first(captures[0], terminal)
        # Actual normal-close is mandatory; accounting may be unsafe after F.
        assert_ordinary_retired(captures[0], accounting_settled=False)
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("name", ["terminal_window", "release_resources"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_budget_terminal_or_release_code_fault_with_owned_h_preserves_first_and_unique_closer(home, tmp_path, name, restore_before_return, monkeypatch):
    function = type.__getattribute__(budget.ReplicaBudget, "__dict__")[name]
    original_code = function.__code__
    captures, caught, hooks = [], [], []
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                patch.setitem(function.__globals__, "_R755_TEST_HOOK_EVENTS", hooks)
                patch.setattr(function, "__code__", trap_code(function))
                caught.append(known_error(lifecycle.current_guard))
                if restore_before_return:
                    function.__code__ = original_code
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(function.__code__ is original_code, 'r755 independent assertion failed at retained draft04 source line 568')
        require(len(captures) == len(caught) == 1 and terminal is caught[0] and (hooks == []), 'r755 independent assertion failed at retained draft04 source line 569')
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0], accounting_settled=False)
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("callee", ["lifecycle_owner", "asyncio_current_task"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_actual_owner_callee_fault_with_owned_h_rejects_before_hook_and_keeps_first(home, tmp_path, callee, restore_before_return, monkeypatch):
    python_callee = callee == "lifecycle_owner"
    function = lifecycle._owner if python_callee else asyncio.current_task
    if python_callee:
        original_code = function.__code__
    else:
        require(type(function) is BuiltinFunctionType and function is asyncio.tasks.current_task, 'r755 independent assertion failed at retained draft04 source line 583')
    captures, caught, hooks = [], [], []
    def replacement(*args, **kwargs):
        hooks.append("current_task")
        raise AssertionError("replaced actual C current_task binding executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                if python_callee:
                    patch.setitem(function.__globals__, "_R755_TEST_HOOK_EVENTS", hooks)
                    patch.setattr(function, "__code__", trap_code(function))
                else:
                    patch.setattr(asyncio, "current_task", replacement)
                caught.append(known_error(lifecycle.current_guard))
                require(type(caught[-1]) is ERROR, 'r755 independent assertion failed at retained draft04 source line 601')
                require(caught[-1].code == ERROR_PREFIX + 'RECORD_CHANGED', 'r755 independent assertion failed at retained draft04 source line 602')
                assert_original_first(capture, caught[-1])
                if restore_before_return:
                    if python_callee:
                        function.__code__ = original_code
                    else:
                        patch.setattr(asyncio, "current_task", function)
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        if python_callee:
            require(function.__code__ is original_code, 'r755 independent assertion failed at retained draft04 source line 611')
        else:
            require(asyncio.current_task is function and asyncio.tasks.current_task is function, 'r755 independent assertion failed at retained draft04 source line 613')
        require(len(captures) == len(caught) == 1 and terminal is caught[0] and (hooks == []), 'r755 independent assertion failed at retained draft04 source line 614')
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("name", ["dict", "BaseException", "StopIteration"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_builtin_shadow_with_owned_h_never_runs_or_erases_first(home, tmp_path, name, restore_before_return, monkeypatch):
    missing, captures, caught, hooks = object(), [], [], []
    original = operation.__dict__.get(name, missing)
    class HostileGlobal:
        def __call__(self, *args, **kwargs):
            hooks.append("call")
            raise AssertionError("changed operation builtin called")
        def __eq__(self, other):
            hooks.append("eq")
            raise AssertionError("changed operation builtin compared")
        def __hash__(self):
            hooks.append("hash")
            raise AssertionError("changed operation builtin hashed")
        def __repr__(self):
            hooks.append("repr")
            raise AssertionError("changed operation builtin rendered")
    replacement = HostileGlobal()
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                patch.setitem(operation.__dict__, name, replacement)
                caught.append(known_error(lifecycle.current_guard))
                if restore_before_return:
                    patch.undo()
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        if original is missing:
            require(name not in operation.__dict__, 'r755 independent assertion failed at retained draft04 source line 651')
        else:
            require(operation.__dict__[name] is original, 'r755 independent assertion failed at retained draft04 source line 653')
        require(len(captures) == len(caught) == 1 and terminal is caught[0] and (hooks == []), 'r755 independent assertion failed at retained draft04 source line 654')
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


def test_real_foreign_thread_inherited_context_cannot_poison_parent_first(home, tmp_path):
    captures, foreign = [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            capture = captured_operation()
            if captures or capture["current"] == 0:
                return
            captures.append(capture)
            context = copy_context()
            def probe():
                actual_thread = threading.get_ident()
                error = known_error(check_chain, home, group_token, tmp_path)
                return actual_thread, error
            with ThreadPoolExecutor(max_workers=1) as pool:
                actual_thread, error = pool.submit(context.run, probe).result(timeout=30)
            require(actual_thread != capture['record'].owner[1], 'r755 independent assertion failed at retained draft04 source line 675')
            require(isinstance(error, KNOWN_ERRORS), 'r755 independent assertion failed at retained draft04 source line 676')
            foreign.append(actual_thread)
            require(capture['record'].first is None and capture['dictionary']['_first'] is None, 'r755 independent assertion failed at retained draft04 source line 678')
        chain = data_chain(home)
        identities = original_identities(chain)
        result = check_chain(home, group_token, tmp_path, callback=callback)
        assert_result(result, home, "create", chain, identities)
        require(len(captures) == len(foreign) == 1, 'r755 independent assertion failed at retained draft04 source line 683')
        assert_ordinary_retired(captures[0])


def test_real_foreign_eager_task_cannot_poison_parent_first(home, tmp_path):
    async def scenario():
        captures, probes = [], []
        parent_task = asyncio.current_task()
        with held((home,)) as (group_token, _guard):
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                async def probe():
                    actual_task = asyncio.current_task()
                    error = known_error(check_chain, home, group_token, tmp_path)
                    probes.append((actual_task, error))
                actual_task = asyncio.Task(probe(), loop=asyncio.get_running_loop(), eager_start=True)
                require(actual_task.done() and actual_task.result() is None, 'r755 independent assertion failed at retained draft04 source line 702')
                require(len(probes) == 1 and probes[0][0] is actual_task and (actual_task is not parent_task), 'r755 independent assertion failed at retained draft04 source line 703')
                require(asyncio.current_task() is parent_task and capture['record'].owner[2] is parent_task, 'r755 independent assertion failed at retained draft04 source line 704')
                require(capture['record'].first is None and capture['dictionary']['_first'] is None, 'r755 independent assertion failed at retained draft04 source line 705')
            chain = data_chain(home)
            identities = original_identities(chain)
            result = check_chain(home, group_token, tmp_path, callback=callback)
            assert_result(result, home, "create", chain, identities)
            require(len(captures) == 1, 'r755 independent assertion failed at retained draft04 source line 710')
            assert_ordinary_retired(captures[0])
    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["normal", "reentrant"])
def test_real_registered_armed_task_uses_no_hash_eq_repr_in_native_unit(home, tmp_path, route):
    hooks = []
    class ArmedTask(asyncio.Task):
        armed = False
        def __hash__(self):
            if self.armed:
                hooks.append("hash")
                raise AssertionError("armed actual Task hashed")
            return asyncio.Task.__hash__(self)
        def __eq__(self, other):
            if self.armed:
                hooks.append("eq")
                raise AssertionError("armed actual Task compared")
            return asyncio.Task.__eq__(self, other)
        def __repr__(self):
            if self.armed:
                hooks.append("repr")
                raise AssertionError("armed actual Task rendered")
            return asyncio.Task.__repr__(self)
    async def body():
        actual_task = asyncio.current_task()
        require(type(actual_task) is ArmedTask, 'r755 independent assertion failed at retained draft04 source line 737')
        captures, caught = [], []
        with held((home,)) as (group_token, _guard):
            def callback():
                capture = captured_operation()
                require(capture['record'].owner[2] is actual_task, 'r755 independent assertion failed at retained draft04 source line 742')
                if capture["current"] == 0 or captures:
                    return
                captures.append(capture)
                if route == "reentrant":
                    caught.append(known_error(operation.check_target_data_chain_locked))
            chain = data_chain(home)
            identities = original_identities(chain)
            actual_task.armed = True
            try:
                if route == "normal":
                    result = check_chain(home, group_token, tmp_path, callback=callback)
                else:
                    terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
            finally:
                actual_task.armed = False
            require(len(captures) == 1 and hooks == [], 'r755 independent assertion failed at retained draft04 source line 758')
            if route == "normal":
                assert_result(result, home, "create", chain, identities)
            else:
                require(len(caught) == 1 and terminal is caught[0], 'r755 independent assertion failed at retained draft04 source line 762')
                assert_original_first(captures[0], terminal)
            assert_ordinary_retired(captures[0])
    async def scenario():
        actual_task = ArmedTask(body(), loop=asyncio.get_running_loop())
        await actual_task
    asyncio.run(scenario())


@pytest.mark.parametrize("failed", [False, True])
def test_retired_native_unit_does_not_keep_task_guard_or_callback_alive(home, tmp_path, failed):
    references = {}
    class Callback:
        def __call__(self):
            capture = captured_operation()
            if capture["current"] == 0:
                return
            references["task"] = weakref.ref(capture["record"].owner[2])
            references["guard"] = weakref.ref(capture["record"].guard)
            if failed:
                raise RuntimeError("weak failure callback")
    async def scenario():
        with held((home,)) as (group_token, _guard):
            callback = Callback()
            references["callback"] = weakref.ref(callback)
            if failed:
                with pytest.raises(RuntimeError):
                    check_chain(home, group_token, tmp_path, callback=callback)
            else:
                chain = data_chain(home)
                identities = original_identities(chain)
                assert_result(check_chain(home, group_token, tmp_path, callback=callback),
                              home, "create", chain, identities)
            del callback
    asyncio.run(scenario())
    gc.collect()
    require(set(references) == {'task', 'guard', 'callback'}, 'r755 independent assertion failed at retained draft04 source line 798')
    require(all((reference() is None for reference in references.values())), 'r755 independent assertion failed at retained draft04 source line 799')


def test_caller_output_and_consumed_token_cannot_become_native_scope_capability(home, tmp_path):
    captures = []
    with held((home,)) as (group_token, _guard):
        def callback():
            if not captures:
                captures.append(captured_operation())
        chain = data_chain(home)
        identities = original_identities(chain)
        result = check_chain(home, group_token, tmp_path, callback=callback)
        assert_result(result, home, "create", chain, identities)
        require(len(captures) == 1, 'r755 independent assertion failed at retained draft04 source line 812')
        token = captures[0]["token"]
        known_error(operation._consume_data_chain_unit_closed_original, token)
        known_error(operation._consume_data_chain_unit_closed_original, result)
        known_error(operation._open_native_scope_original, token)
        result["write_authority"] = result["sender_authority"] = True
        result["unit_native_normal_closed"] = False
        bad = arguments(home, result, tmp_path)
        known_error(operation.check_target_data_chain_locked, home, **bad)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


def require(condition, message):
    # A plain helper avoids pytest printing private holders/raw values on failure.
    if not condition:
        raise AssertionError(message)


def capture_native_scope(capture):
    registration, record = capture["registration"], capture["record"]
    require(registration.kind == record.kind == "target_data_chain_unit", "wrong original driver kind")
    scope = record.native_scope
    require(type(scope) is native._NativeScope and registration.native_scope is scope,
            "record/registration must refer to one exact native scope")
    require(type(scope.key) is native._NativeScopeKey, "wrong original scope key type")
    require(scope.registration is registration and scope.operation_token is capture["token"],
            "scope must belong to this actual original operation")
    require(scope.budget is capture["budget"] and scope.owner is record.owner,
            "scope owner/ledger must be the original objects")
    require(registration.phase == record.phase == "native_checkpoint", "callback outside native checkpoint")
    require(scope.phase in ("before_acquire", "component_finalized", "before_final_validation"),
            "callback entered a pending/cleanup phase")
    require(type(scope.chain) is tuple and all(type(path) is str for path in scope.chain), "invalid frozen native chain")
    profile = record.inputs[0]
    chain = data_chain(profile)
    require(tuple(path_key(path) for path in scope.chain) == tuple(path_key(path) for path in chain),
            "scope chain is not the original target DATA chain")
    identities = original_identities(chain)
    n, j = len(chain), scope.next_ordinal
    require(type(j) is int and 0 <= j <= n, "invalid completed component ordinal")
    require(scope.pending_identities == 0 and scope.unknown_resources == 0,
            "unsafe callback has pending or unknown identity")
    require(scope.current_resources == capture["current"] == j, "scope/ledger actual current mismatch")
    require(scope.resource_acquires == 2 * j and scope.resource_normal_closes == j,
            "stable H/H2 history counters mismatch")
    require(scope.peak_owned_resources == (j + 1 if j else 0), "stable acquired peak mismatch")
    require(type(scope.holders) is list and type(scope.directory_keys) is list and type(scope.events) is list,
            "wrong original native containers")
    require(len(scope.directory_keys) == j, "original H key count mismatch")
    expected_events = event_oracle(identities)[:3 * j]
    require(scope.events == expected_events, "actual stable resource history differs from independent graph")
    require(type(scope.begin) is dict and set(scope.begin) == SNAPSHOT_FIELDS, "missing actual unit begin snapshot")
    require(scope.begin["current_resources"] == scope.begin["pending_resources"] == 0,
            "unit did not begin with zero owned resources")
    require(scope.end is None and scope.normal_closed is False and scope.retired is False,
            "live checkpoint cannot claim unit retirement/closure")
    acquired = []
    for holder in scope.holders:
        require(type(holder) is native._ResourceHolder and holder.scope is scope, "holder not from original scope")
        require(holder.kind == "directory_handle" and holder.purpose in ("H", "H2", "H3"), "wrong original holder role")
        if holder.resource_id is None:
            require(holder.ownership == "empty", "unacquired slot is not empty")
            continue
        acquired.append(holder)
        require(holder.purpose in ("H", "H2") and type(holder.ordinal) is int and 0 <= holder.ordinal < j,
                "unexpected acquired resource in stable acquisition checkpoint")
        original_D = identities[holder.ordinal]
        require(holder.D == original_D and holder.identity_sha256 == hashlib.sha256(canonical(original_D)).hexdigest(),
                "holder did not commit the actual wide acquisition identity")
        require(type(holder.ticket) is object and type(holder.lease) is object, "missing actual ticket/lease objects")
        if holder.purpose == "H":
            require(type(holder.key) is native._DirectoryKey and scope.directory_keys[holder.ordinal] is holder.key,
                    "H key is not the original registered key")
            require(holder.ownership == "owned" and holder.closer == "handle" and holder.transfer == "not_started",
                    "original H must remain owned by its original HANDLE closer")
            require(holder.normal_closed is False and holder.close_attempted is False and holder.lease_released is False,
                    "live H falsely claims closure")
            require(capture["dictionary"]["_resource_leases"].get(holder.lease) == 1,
                    "live H must have its independent actual lease")
        else:
            require(type(holder.key) is native._ResourceKey and holder.closer == "fd" and holder.transfer == "transferred",
                    "H2 must transfer the same resource to the fd closer")
            require(holder.ownership == "closed" and holder.close_outcome == "normal" and holder.normal_closed is True,
                    "stable checkpoint precedes actual H2 normal close")
            require(holder.close_attempted is True and holder.release_attempted is True and holder.lease_released is True,
                    "H2 lease was not actually released after normal close")
    require(len(acquired) == 2 * j, "stable acquired holder count mismatch")
    if scope.phase == "before_acquire":
        require(j == 0 and record.checkpoint_count == 0, "before-acquire checkpoint has effects")
    elif scope.phase == "component_finalized":
        require(j > 0 and record.checkpoint_count == j, "component checkpoint count mismatch")
    else:
        require(j == n and record.checkpoint_count == n + 1, "final checkpoint is not the complete leaf window")
    capture["scope"] = scope
    capture["scope_phase"] = scope.phase
    capture["ordinal"] = j
    capture["checkpoint_count"] = record.checkpoint_count
    capture["identities"] = identities
    return capture


def assert_scope_terminal(capture, *, accounting_settled=True):
    scope = capture["scope"]
    success = capture["record"].first is None
    require(scope.retired is True and scope.phase == ("retired_closed" if success else "retired_failed"),
            "original native scope was not actually retired")
    require(scope.normal_closed is success, "failed scope must not claim normal unit history")
    require(scope.current_resources == scope.pending_identities == scope.unknown_resources == 0,
            "ordinary scope has unresolved native resources")
    require(scope.debt_transferred is False, "ordinary failure must not invent native unknown debt")
    actual = [holder for holder in scope.holders if holder.resource_id is not None]
    require(scope.resource_acquires == scope.resource_normal_closes == len(actual),
            "actual acquired resources were not all normally closed")
    for holder in actual:
        require(holder.ownership == "closed" and holder.close_attempted is True,
                "ordinary original holder was not checked-closed")
        require(holder.normal_closed is True and holder.close_outcome == "normal", "closer did not actually return normally")
        if accounting_settled:
            require(holder.release_attempted is True and holder.lease_released is True,
                    "actual closed resource's own lease was not released")
    require(len(scope.events) == 2 * len(actual), "ordinary actual event graph incomplete")
    require(all(event["identity_sha256"] is not None for event in scope.events), "ordinary stable fault left pending identities")
    if success:
        expected = event_oracle(capture["identities"])
        require(scope.events == expected, "normal terminal graph differs from independent H/H2/H3 oracle")
        require(type(scope.end) is dict and set(scope.end) == SNAPSHOT_FIELDS, "normal scope lacks actual terminal snapshot")


@pytest.mark.parametrize("target", ["record_kind", "record_scope", "scope_budget", "scope_phase", "holder_ordinal", "started"])
def test_actual_native_fixed_field_fault_is_not_adopted_and_known_owned_cleanup_remains(home, tmp_path, target, monkeypatch):
    captures, hooks = [], []
    class Hostile:
        def __eq__(self, other):
            hooks.append("eq")
            raise AssertionError("fake fixed field compared")
        def __hash__(self):
            hooks.append("hash")
            raise AssertionError("fake fixed field hashed")
        def __repr__(self):
            hooks.append("repr")
            raise AssertionError("fake fixed field rendered")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                scope = capture["scope"]
                if target == "record_kind":
                    patch.setattr(capture["record"], "kind", "check_only")
                elif target == "record_scope":
                    patch.setattr(capture["record"], "native_scope", Hostile())
                elif target == "scope_budget":
                    patch.setattr(scope, "budget", Hostile())
                elif target == "scope_phase":
                    patch.setattr(scope, "phase", "unregistered_phase")
                elif target == "holder_ordinal":
                    holder = next(holder for holder in scope.holders if holder.purpose == "H" and holder.resource_id is not None)
                    patch.setattr(holder, "ordinal", Hostile())
                else:
                    patch.setitem(capture["dictionary"], "_started", capture["started"] + 1)
                # Return while the F persists: postcallback state validation must reject it.
                # An observer's universal active-callback REENTRANT is not this oracle.
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(len(captures) == 1 and hooks == [], "fixed field was adopted or a hostile hook ran")
        valid_codes = {ERROR_PREFIX + "RECORD_CHANGED", "AUTHORITY_REPLICA_DIRECTORY1_RECORD_CHANGED"}
        if target == "scope_phase":
            valid_codes.add("AUTHORITY_REPLICA_DIRECTORY1_PHASE_INVALID")
        if target in ("scope_budget", "started"):
            valid_codes.add("AUTHORITY_REPLICA_DIRECTORY1_ACCOUNTING_CHANGED")
        require(getattr(terminal, "code", None) in valid_codes,
                "Fixed-field rejection must come from state integrity, not generic dispatch/native failure")
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("field", ["__code__", "__defaults__", "__kwdefaults__"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_actual_native_downstream_function_fault_never_executes_or_clears_first(home, tmp_path, field, restore_before_return, monkeypatch):
    function = native._verify_directory_before_close_original
    original = getattr(function, field)
    captures, caught, hooks = [], [], []
    class HostileDefault:
        def __eq__(self, other):
            hooks.append("eq")
            raise AssertionError("changed native default compared")
        def __repr__(self):
            hooks.append("repr")
            raise AssertionError("changed native default rendered")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                if field == "__code__":
                    patch.setitem(function.__globals__, "_R755_TEST_HOOK_EVENTS", hooks)
                    patch.setattr(function, field, trap_code(function))
                elif field == "__defaults__":
                    patch.setattr(function, field, (HostileDefault(),))
                else:
                    patch.setattr(function, field, {"unrecognized": HostileDefault()})
                caught.append(known_error(lifecycle.current_guard))
                if restore_before_return:
                    setattr(function, field, original)
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(getattr(function, field) is original, "native callee original field not restored")
        require(len(captures) == len(caught) == 1 and terminal is caught[0] and hooks == [],
                "native callee fault ran a hook or replaced first")
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("target", ["scope_phase", "holder_scope"])
def test_actual_native_class_descriptor_fault_never_runs_during_cleanup(home, tmp_path, target, monkeypatch):
    captures, hooks = [], []
    class HostileDescriptor:
        def __get__(self, instance, owner=None):
            hooks.append("get")
            raise AssertionError("changed original native descriptor executed")
        def __set__(self, instance, value):
            hooks.append("set")
            raise AssertionError("changed original native descriptor assigned")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["current"] == 0:
                    return
                captures.append(capture)
                cls, field = (native._NativeScope, "phase") if target == "scope_phase" else (native._ResourceHolder, "scope")
                patch.setattr(cls, field, HostileDescriptor())
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(len(captures) == 1 and hooks == [], "native descriptor hook ran")
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


def _require_unknown_lane(name):
    require(os.environ.get("RENO_REPLICA_DIRECTORY_UNKNOWN_LANE") == name,
            "Permanent unknown scenario needs its one frozen dedicated child lane")


def _protect_original_leaf_once_test_only(holder):
    # Test-side bindings only. Product FuncPtr/argtypes/restype/errcheck unchanged.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_flags, set_flags = kernel.GetHandleInformation, kernel.SetHandleInformation
    get_flags.argtypes, get_flags.restype = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
    set_flags.argtypes, set_flags.restype = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL
    require(ctypes.sizeof(wintypes.HANDLE) == 8 and ctypes.sizeof(wintypes.DWORD) == ctypes.sizeof(wintypes.BOOL) == 4,
            "Wrong fixed test-side Windows handle flag ABI")
    require(holder.purpose == "H" and holder.ownership == "owned" and holder.closer == "handle",
            "Protect target must be one original still-owned H")
    require(type(holder.raw) is int and holder.raw > 0 and holder.fd is None,
            "Protect target is not an original HANDLE holder")
    before = wintypes.DWORD(0)
    returned = get_flags(wintypes.HANDLE(holder.raw), ctypes.byref(before))
    ctypes.get_last_error()
    require(type(returned) is int and returned != 0, "Test-side original GetHandleInformation failed")
    require(before.value & 2 == 0, "Protect fixture was already armed")
    returned = set_flags(wintypes.HANDLE(holder.raw), 2, 2)
    ctypes.get_last_error()
    require(type(returned) is int and returned != 0, "Test-side original SetHandleInformation failed")
    after = wintypes.DWORD(0)
    returned = get_flags(wintypes.HANDLE(holder.raw), ctypes.byref(after))
    ctypes.get_last_error()
    require(type(returned) is int and returned != 0, "Test-side original GetHandleInformation readback failed")
    require(after.value & 2 == 2 and ((before.value ^ after.value) & ~2) == 0,
            "Protect fixture readback did not preserve other handle flags")
    # Intentionally no clear flag, test-side CloseHandle, fd transfer or retry.


def _assert_sticky_unknown_across_real_owners(home, group_token, root, scope):
    count = len(operation._NATIVE_DEBTS)
    require(count > 0, "Original unknown was not transferred before retirement")
    require(not operation._LIVE and not operation._OWNERS, "Unknown operation still registered")
    require(scope.retired is True and scope.phase == "retired_failed" and scope.normal_closed is False,
            "Unknown scope must actually retire failed")
    require(scope.debt_transferred is True and scope.unknown_resources > 0,
            "Original scope did not retain unknown responsibility")
    before = (scope.resource_acquires, scope.resource_normal_closes, len(scope.events), scope.current_resources)
    def attempt(mode, bad=False):
        if bad:
            error = known_error(operation.check_target_data_chain_locked, unexpected=object())
        else:
            error = known_error(check_chain, home, group_token, root, mode=mode)
        require(type(error) is ERROR and error.code == ERROR_PREFIX + "UNSETTLED", "Sticky unknown did not precede new input/native")
        require(len(operation._NATIVE_DEBTS) == count, "New entry changed original native debt")
    for mode in ("preview", "create", "resume", "verify"):
        attempt(mode)
    attempt("create", bad=True)
    original_owner = (os.getpid(), threading.get_ident(), asyncio.current_task())
    context = copy_context()
    def thread_probe():
        require(threading.get_ident() != original_owner[1], "Sticky thread probe was not foreign")
        for mode in ("preview", "create", "resume", "verify"):
            attempt(mode)
        attempt("create", bad=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(context.run, thread_probe).result(timeout=30)
    async def task_probe():
        require(asyncio.current_task() is not original_owner[2], "Sticky Task probe was not foreign")
        for mode in ("preview", "create", "resume", "verify"):
            attempt(mode)
        attempt("create", bad=True)
    actual_task = asyncio.Task(task_probe(), loop=asyncio.get_running_loop(), eager_start=True)
    require(actual_task.done() and actual_task.result() is None, "Sticky real eager Task did not terminate")
    require((scope.resource_acquires, scope.resource_normal_closes, len(scope.events), scope.current_resources) == before,
            "Sticky attempts re-opened or re-closed old native resources")
    return count


def test_real_protected_leaf_close_unknown_sticky_gate(home, tmp_path):
    _require_unknown_lane("protected_leaf_unknown")
    references, report = {}, {}
    class Callback:
        protected = False
        capture = None
        def __call__(self):
            capture = captured_operation()
            if capture["scope_phase"] != "before_final_validation":
                return
            require(not self.protected, "Protected leaf fixture executed more than once")
            scope = capture["scope"]
            n = len(scope.chain)
            require(scope.next_ordinal == n and scope.pending_identities == 0, "Not the complete stable leaf checkpoint")
            candidates = [holder for holder in scope.holders
                          if holder.purpose == "H" and holder.ordinal == n - 1 and holder.resource_id is not None]
            require(len(candidates) == 1, "Original leaf holder is not unique")
            leaf = candidates[0]
            require(scope.directory_keys[-1] is leaf.key, "Leaf key is not original DATA key")
            _protect_original_leaf_once_test_only(leaf)
            self.protected, self.capture = True, capture
            references["task"] = weakref.ref(capture["record"].owner[2])
            references["guard"] = weakref.ref(capture["record"].guard)
    async def scenario():
        with held((home,)) as (group_token, _guard):
            callback = Callback()
            references["callback"] = weakref.ref(callback)
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
            require(callback.protected and callback.capture is not None, "Protect fixture did not establish")
            capture = callback.capture
            scope, n = capture["scope"], len(capture["scope"].chain)
            leaves = [holder for holder in scope.holders if holder.purpose == "H" and holder.ordinal == n - 1]
            require(len(leaves) == 1, "Protected leaf original source missing")
            leaf = leaves[0]
            require(leaf.close_attempted is True and leaf.close_outcome == "false" and leaf.normal_closed is False,
                    "Original protected leaf CloseHandle false was not reached")
            require(type(terminal) is native.AuthorityReplicaDirectoryError and terminal.code == "AUTHORITY_REPLICA_DIRECTORY1_CLOSE_UNKNOWN",
                    "Actual original protected close failure was not first")
            assert_original_first(capture, terminal)
            require(leaf.debt_submitted is True and leaf.lease_released is False, "Protected close unknown was refunded")
            require(scope.resource_acquires == 2 * n + 1 and scope.resource_normal_closes == 2 * n,
                    "Protected leaf did not follow the conditional actual resource graph")
            require(scope.current_resources == scope.unknown_resources == 1 and len(scope.events) == 4 * n + 1,
                    "Protected close pending debt/event state mismatch")
            require(not any(event["resource_id"] == leaf.resource_id and event["action"] == "normal_closed" for event in scope.events),
                    "Protected leaf has a false normal-close event")
            for holder in scope.holders:
                if holder.resource_id is not None and holder is not leaf:
                    require(holder.normal_closed is True and holder.close_outcome == "normal" and holder.lease_released is True,
                            "Independent original resources were not normally cleaned up")
            count = _assert_sticky_unknown_across_real_owners(home, group_token, tmp_path, scope)
            report.update({"scenario": "protected_leaf_unknown", "evidence": "actual_original_CloseHandle_false",
                           "operation_id": OPERATION_ID, "run_id": capture["record"].run_id, "scope_id": scope.scope_id,
                           "directory_count": n, "resource_acquires": scope.resource_acquires,
                           "resource_normal_closes": scope.resource_normal_closes, "unknown_resources": scope.unknown_resources,
                           "debt_count": count, "first_code": terminal.code, "normal_unit_returned": False,
                           "manual_close_or_flag_clear": False})
            callback.capture = None
    asyncio.run(scenario())
    gc.collect()
    require(set(references) == {"task", "guard", "callback"} and all(value() is None for value in references.values()),
            "Original sticky debt strongly retains Task/guard/callback")
    print("R755_UNKNOWN_SCALARS " + canonical(report).decode("utf-8"))


def test_untrusted_original_closer_is_never_dispatched_and_sticky_gate_is_kept(home, tmp_path):
    _require_unknown_lane("untrusted_closer_fault")
    references, report, hooks = {}, {}, []
    function = native._WIN_CLOSE_HANDLE
    original_errcheck = function.errcheck
    def hostile_errcheck(result, function, arguments):
        hooks.append("errcheck")
        raise AssertionError("Untrusted original closer errcheck executed")
    async def scenario():
        captures, caught = [], []
        armed = False
        with held((home,)) as (group_token, _guard):
            class Callback:
                def __call__(self):
                    nonlocal armed
                    capture = captured_operation()
                    if captures or capture["current"] == 0:
                        return
                    captures.append(capture)
                    references["task"] = weakref.ref(capture["record"].owner[2])
                    references["guard"] = weakref.ref(capture["record"].guard)
                    function.errcheck = hostile_errcheck
                    armed = True
                    caught.append(known_error(lifecycle.current_guard))
            callback = Callback()
            references["callback"] = weakref.ref(callback)
            try:
                terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
                require(len(captures) == len(caught) == 1 and terminal is caught[0] and hooks == [],
                        "Untrusted closer was dispatched or original first changed")
                capture = captures[0]
                scope = capture["scope"]
                assert_original_first(capture, terminal)
                require(scope.resource_acquires == 2 and scope.resource_normal_closes == 1,
                        "Closer integrity F did not occur after first stable H/H2")
                actual_H = [holder for holder in scope.holders if holder.purpose == "H" and holder.resource_id is not None]
                require(len(actual_H) == 1, "Untrusted closer fixture did not hold exactly one original H")
                require(actual_H[0].normal_closed is False and actual_H[0].close_outcome == "untrusted",
                        "Damaged closer was treated as a normal original close")
                require(actual_H[0].debt_submitted is True and actual_H[0].lease_released is False,
                        "Untrusted closer unknown responsibility was lost/refunded")
            finally:
                # CPython ctypes default errcheck=None cannot be restored by assignment.
                # Root d0ad94 confirmed setter(None) TypeError and delattr -> None
                # on an uncalled synthetic CFUNCTYPE object, not product/Win32 I/O.
                if armed:
                    if original_errcheck is None:
                        del function.errcheck
                    else:
                        function.errcheck = original_errcheck
            # Restore only after actual failure retirement; never reclose the old H.
            require(native._WIN_CLOSE_HANDLE is function and function.errcheck is original_errcheck and hooks == [],
                    "Original closer object/prototype did not restore before sticky test")
            count = _assert_sticky_unknown_across_real_owners(home, group_token, tmp_path, scope)
            report.update({"scenario": "untrusted_closer_fault", "evidence": "explicit_closer_signature_F",
                           "operation_id": OPERATION_ID, "run_id": capture["record"].run_id, "scope_id": scope.scope_id,
                           "resource_acquires": scope.resource_acquires, "resource_normal_closes": scope.resource_normal_closes,
                           "unknown_resources": scope.unknown_resources, "debt_count": count,
                           "first_code": terminal.code, "untrusted_hook_calls": len(hooks),
                           "actual_CloseHandle_false_claimed": False, "manual_close_or_flag_clear": False})
    asyncio.run(scenario())
    gc.collect()
    require(set(references) == {"task", "guard", "callback"} and all(value() is None for value in references.values()),
            "Original untrusted-closer debt strongly retains Task/guard/callback")
    print("R755_UNKNOWN_SCALARS " + canonical(report).decode("utf-8"))


@pytest.mark.parametrize("route", ["scope_getter", "holder_getter", "scope_updater", "holder_updater"])
def test_exported_native_authority_helpers_reject_real_callback_before_returning_state(home, tmp_path, route):
    captures, caught, returned = [], [], []
    with held((home,)) as (group_token, _guard):
        def callback():
            capture = captured_operation()
            if captures or capture["scope_phase"] != "component_finalized":
                return
            captures.append(capture)
            scope = capture["scope"]
            actual = [item for item in scope.holders
                      if item.purpose == "H" and item.resource_id is not None and item.ordinal == scope.next_ordinal - 1]
            require(len(actual) == 1, "Stable callback lacks a unique actual original H mirror")
            holder = actual[0]
            # Actual original mirror/key/raw only. Never resolve/adopt an authority dict
            # or fabricate a raw HANDLE/FD. Reentry must precede updater type parsing.
            try:
                if route == "scope_getter":
                    value = native._scope_state_original(scope.key)
                elif route == "holder_getter":
                    value = native._holder_state_original(holder.key)
                elif route == "scope_updater":
                    value = native._update_scope_original(scope, phase=scope.phase)
                else:
                    value = native._update_holder_original(holder, raw=holder.raw)
            except BaseException as error:
                require(type(error) is ERROR, "Native callback helper must reject through original operation first")
                dictionary = BaseException.__dict__["__dict__"].__get__(error, ERROR)
                require(dict.get(dictionary, "code") == ERROR_PREFIX + "REENTRANT",
                        "Native callback helper did not reject before state/type parsing")
                caught.append(error)
                assert_original_first(capture, error)
            else:
                returned.append(value)
                raise AssertionError("Native callback helper returned authority/mutation data")
        terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require(len(captures) == len(caught) == 1 and not returned and terminal is caught[0],
                "Exported authority helper lost first or leaked a state result")
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("restore_before_return", [False, True])
def test_handle_info_added_allocator_shadow_is_rejected_before_hook_and_cleanup(home, tmp_path, restore_before_return, monkeypatch):
    captures, caught, hooks = [], [], []
    def hostile_new(cls, *args, **kwargs):
        hooks.append("new")
        raise AssertionError("Added HandleInfo allocator shadow executed")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["scope_phase"] != "component_finalized":
                    return
                captures.append(capture)
                require("__new__" not in type.__getattribute__(native._HandleInfo, "__dict__"),
                        "Allocator case requires an added original absence key")
                patch.setattr(native._HandleInfo, "__new__", staticmethod(hostile_new), raising=False)
                try:
                    lifecycle.current_guard()
                except BaseException as error:
                    require(type(error) is ERROR, "Allocator shadow rejection changed the original operation error type")
                    dictionary = BaseException.__dict__["__dict__"].__get__(error, ERROR)
                    require(dict.get(dictionary, "code") == ERROR_PREFIX + "RECORD_CHANGED",
                            "Added allocator shadow did not fail complete class catalog")
                    caught.append(error)
                    assert_original_first(capture, error)
                else:
                    raise AssertionError("Added HandleInfo allocator shadow was accepted")
                if restore_before_return:
                    patch.undo()
            terminal = known_error(check_chain, home, group_token, tmp_path, callback=callback)
        require("__new__" not in type.__getattribute__(native._HandleInfo, "__dict__"),
                "Original allocator absence was not restored")
        require(len(captures) == len(caught) == 1 and terminal is caught[0] and hooks == [],
                "Allocator rejection dispatched a hook or changed first")
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("module_name", ["operation", "native"])
@pytest.mark.parametrize("hook", ["__init__", "__new__", "__setattr__"])
@pytest.mark.parametrize("restore_before_return", [False, True])
def test_mutable_error_class_hooks_do_not_dispatch_in_original_rejection_factory(home, tmp_path, module_name, hook, restore_before_return, monkeypatch):
    # Save original C access/type pointers before any mutable exception hook is armed.
    error_type = ERROR if module_name == "operation" else native.AuthorityReplicaDirectoryError
    get_dictionary = BaseException.__dict__["__dict__"].__get__
    dictionary_get, type_of = dict.get, type
    expected_code = (ERROR_PREFIX if module_name == "operation" else "AUTHORITY_REPLICA_DIRECTORY1_") + "RECORD_CHANGED"
    captures, caught, hooks = [], [], []
    def hostile_hook(*args, **kwargs):
        hooks.append(hook)
        raise AssertionError("Mutable rejection exception-class hook executed")
    def capture_error(call):
        # pytest.raises must never inspect the class while its hooks are polluted.
        try:
            call()
        except BaseException as error:
            return error
        raise AssertionError("Original typed rejection factory accepted the fault")
    with held((home,)) as (group_token, _guard):
        with monkeypatch.context() as patch:
            def callback():
                capture = captured_operation()
                if captures or capture["scope_phase"] != "component_finalized":
                    return
                captures.append(capture)
                replacement = staticmethod(hostile_hook) if hook == "__new__" else hostile_hook
                patch.setattr(error_type, hook, replacement, raising=False)
                try:
                    if module_name == "operation":
                        lifecycle.current_guard()
                    else:
                        # Explicit original rejection-factory F, not a hardware error.
                        # Re-raise its actual error so the real wrapper records first.
                        native._fail("RECORD_CHANGED")
                except BaseException as error:
                    require(type_of(error) is error_type, "Rejection dispatched a mutable error constructor hook")
                    dictionary = get_dictionary(error, error_type)
                    require(dictionary_get(dictionary, "code") == expected_code,
                            "Original C exception dictionary lost the exact rejection code")
                    caught.append(error)
                    if restore_before_return:
                        patch.undo()
                    raise
                else:
                    raise AssertionError("Original rejection factory returned without error")
            terminal = capture_error(lambda: check_chain(home, group_token, tmp_path, callback=callback))
            require(type_of(terminal) is error_type,
                    "Outer delivery lost the original typed rejection while class hooks were armed")
            require(dictionary_get(get_dictionary(terminal, error_type), "code") == expected_code,
                    "Outer rejection code changed during polluted-class cleanup")
            require(len(captures) == len(caught) == 1 and terminal is caught[0] and hooks == [],
                    "Error hook ran or first was replaced during callback/cleanup")
        assert_original_first(captures[0], terminal)
        assert_ordinary_retired(captures[0])
        assert_new_normal(home, group_token, tmp_path)


@pytest.mark.parametrize("route", ["trusted_retirement", "enroll"])
def test_checker_registering_mirrors_cannot_borrow_native_driver_retirement_or_enrollment(home, tmp_path, route):
    # Only a real fixed zero-effect checker creates this registration and ledger.
    # The actual DATA path tuple is valid; no owner/registry/closure/native FD is forged.
    chain = data_chain(home)
    captures, caught, returned = [], [], []
    registration_mirrors = ("callback_active", "phase", "budget", "budget_dict", "budget_anchor")
    record_mirrors = ("callback_active", "phase", "budget")
    zero_fields = (
        "_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources",
        "_read_bytes", "_source_read_bytes", "_read_calls", "_source_read_calls", "_seek_calls",
        "_uncertain_reads", "_uncertain_source_reads", "_confirmed", "_write_calls", "_uncertain_writes",
    )
    with held((home,)) as (group_token, original_guard):
        def callback():
            # Independent limited checker inspector: captured_operation requires S
            # and is intentionally not used by this checker-origin regression.
            registrations = list(operation._LIVE.values())
            require(len(registrations) == 1 and not captures,
                    "Checker callback must retain one actual fresh original registration")
            registration = registrations[0]
            record, token = registration.record, registration.token
            ledger = record.budget
            dictionary = object.__getattribute__(ledger, "__dict__")
            require(type(registration) is operation._OperationRegistration and type(record) is operation._OperationRecord,
                    "Checker inspector did not observe original fixed record types")
            require(type(ledger) is budget.ReplicaBudget and type(dictionary) is dict
                    and all(type(key) is str for key in dictionary),
                    "Checker inspector did not observe original plain ledger storage")
            require(operation._LIVE[id(token)] is registration
                    and operation._OWNERS[registration.owner_key] is registration,
                    "Checker callback lost its actual original registry responsibility")
            require(record.token is token and record.owner is registration.owner
                    and record.owner_key is registration.owner_key and record.inputs is registration.inputs,
                    "Checker callback changed its original owner/token/input selection")
            require(type(record.owner) is tuple and len(record.owner) == 3
                    and record.owner[0] == os.getpid() and record.owner[1] == threading.get_ident(),
                    "Checker callback does not run on its actual original process/thread")
            try:
                actual_task = asyncio.current_task()
            except RuntimeError:
                actual_task = None
            require(record.owner[2] is actual_task, "Checker callback does not run on its actual original task")
            require(registration.guard is record.guard is original_guard
                    and registration.inputs[1] is group_token and registration.inputs[6] is callback,
                    "Checker callback borrowed a different guard/group/callback")
            require(registration.phase == "checking" and record.phase is registration.phase
                    and registration.callback_active is record.callback_active is True,
                    "Checker regression did not reach the original checking callback")
            require(registration.kind == record.kind == "check_only"
                    and registration.native_scope is record.native_scope is None
                    and registration.window is None and not operation._NATIVE_DEBTS
                    and not native._failure_blocked_original()
                    and operation._driver_anchor_original(registration) is None,
                    "Fresh checker unexpectedly owns a native scope/debt/driver anchor")
            require(registration.budget is ledger and registration.budget_dict is dictionary
                    and dictionary["_in_callback"] is True
                    and registration.first is record.first is dictionary["_first"] is None,
                    "Checker regression must start with the original in-callback ledger and no first")
            require(all(type(dictionary[name]) is int and dictionary[name] == 0 for name in zero_fields)
                    and not dictionary["_resource_pending"] and not dictionary["_resource_leases"]
                    and dictionary["_active"] is None and dictionary["_terminal"] == 0,
                    "Zero-effect checker already performed resource or IO effects")
            stable_registration = tuple((name, getattr(registration, name)) for name in (
                "record", "token", "owner", "owner_key", "inputs", "anchor", "guard", "guard_fields",
                "callback_wrapper", "callback_pin", "budget_fixed", "budget_last", "checkpoint_count", "run_id",
            ))
            stable_record = tuple((name, getattr(record, name)) for name in (
                "token", "owner", "owner_key", "inputs", "guard", "group_token", "profiles", "checkpoint_count", "run_id",
            ))
            saved_registration = tuple((name, getattr(registration, name)) for name in registration_mirrors)
            saved_record = tuple((name, getattr(record, name)) for name in record_mirrors)
            saved_ledger_fields = tuple(dictionary.items())
            capture = {"registration": registration, "record": record, "token": token,
                       "budget": ledger, "dictionary": dictionary,
                       "started": dictionary["_started"], "guard": original_guard,
                       "stable_registration": stable_registration, "stable_record": stable_record,
                       "saved_registration": saved_registration, "saved_record": saved_record,
                       "saved_ledger_fields": saved_ledger_fields}
            captures.append(capture)
            try:
                # Precisely the reviewed mirror-only F: original ledger storage
                # and its independent _in_callback remain untouched and True.
                registration.callback_active = record.callback_active = False
                registration.phase = record.phase = "registering"
                registration.budget = record.budget = None
                registration.budget_dict = registration.budget_anchor = None
                try:
                    if route == "trusted_retirement":
                        value = operation._trusted_driver_retirement_original(registration)
                    else:
                        value = operation._enroll_native_driver_original(registration, chain)
                except BaseException as error:
                    require(type(error) is ERROR, "Checker driver issuer did not return the original typed rejection")
                    error_dictionary = BaseException.__dict__["__dict__"].__get__(error, ERROR)
                    require(dict.get(error_dictionary, "code") == ERROR_PREFIX + "GUARD_REQUIRED",
                            "Checker registering mirrors qualified for a DATA-only driver issuer")
                    caught.append(error)
                    require(registration.kind == record.kind == "check_only"
                            and registration.native_scope is record.native_scope is None
                            and operation._driver_anchor_original(registration) is None,
                            "Checker rejection happened after native enrollment or anchor issuance")
                    require(len(dictionary) == len(saved_ledger_fields)
                            and all(dictionary[name] is value for name, value in saved_ledger_fields)
                            and dictionary["_in_callback"] is True,
                            "Driver issuer changed original checker ledger storage before callback failure delivery")
                    require(all(getattr(registration, name) is value for name, value in stable_registration)
                            and all(getattr(record, name) is value for name, value in stable_record)
                            and operation._LIVE[id(token)] is registration
                            and operation._OWNERS[registration.owner_key] is registration,
                            "Driver issuer changed original checker selection or registry before rejection")
                    # Finally restores every altered mirror before this SAME
                    # actual error reaches the real checker wrapper's first kernel.
                    raise
                else:
                    returned.append(value)
                    raise AssertionError("Checker borrowed a driver retirement closure or enrollment result")
            finally:
                for name, value in saved_registration:
                    setattr(registration, name, value)
                for name, value in saved_record:
                    setattr(record, name, value)
        try:
            check_legacy_checker(home, group_token, tmp_path, callback=callback)
        except BaseException as error:
            terminal = error
        else:
            raise AssertionError("Checker driver-promotion fault returned normally")
        require(len(captures) == len(caught) == 1 and not returned and terminal is caught[0],
                "Checker failure delivery lost the actual driver rejection or returned a capability")
        capture = captures[0]
        registration, record, token = capture["registration"], capture["record"], capture["token"]
        dictionary, ledger = capture["dictionary"], capture["budget"]
        require(type(terminal) is ERROR and terminal.code == ERROR_PREFIX + "GUARD_REQUIRED",
                "Checker outer delivery replaced the original typed driver rejection")
        require(registration.first is record.first is dictionary["_first"] is terminal,
                "Real checker callback/outer did not preserve the same original first")
        require(registration.budget is record.budget is ledger
                and object.__getattribute__(ledger, "__dict__") is dictionary
                and registration.budget_dict is dictionary and dictionary["_started"] == capture["started"],
                "Checker retirement changed its original ledger/dictionary/start selection")
        require(all(dictionary[name] is value for name, value in capture["saved_ledger_fields"]
                    if name not in ("_first", "_in_callback", "_last_tick")),
                "Checker failure delivery changed original zero-effect ledger fields")
        require(all(type(dictionary[name]) is int and dictionary[name] == 0 for name in zero_fields)
                and not dictionary["_resource_pending"] and not dictionary["_resource_leases"]
                and dictionary["_active"] is None and dictionary["_terminal"] == 0
                and dictionary["_in_callback"] is False,
                "Checker-origin rejection left resource/IO effects or a callback latch")
        require(registration.kind == record.kind == "check_only"
                and registration.native_scope is record.native_scope is None
                and registration.window is None and operation._driver_anchor_original(registration) is None
                and registration.phase == "retired_failed" and record.phase is registration.phase
                and registration.callback_active is record.callback_active is False,
                "Original checker failed retirement changed kind/scope/anchor or retained callback state")
        require(id(token) not in operation._LIVE and id(token) not in operation._CLOSED
                and not operation._OWNERS and not operation._GUARD_CALLS and not operation._NATIVE_DEBTS
                and not native._failure_blocked_original(),
                "Original failed checker retains registration/owner/window/native debt or successful history")
        require(all(getattr(registration, name) is value for name, value in capture["stable_registration"])
                and all(getattr(record, name) is value for name, value in capture["stable_record"]),
                "Checker failure retirement changed the actual original owner/token/inputs/guard")
        assert_new_normal(home, group_token, tmp_path)
