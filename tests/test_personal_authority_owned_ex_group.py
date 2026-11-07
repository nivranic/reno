"""r865 static candidate: actual isolated Windows owners and narrow fault seams.

Root alone may admit/import/collect/run. Source_closed/write_authority remain
False. Original replica entry/scope/Guard gates remain authoritative. These
selected tests use the ordinary dormant operation lane; active original
operation/catalog/native graph execution is separately pending.
"""
from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_contract as contract
from reno import personal_authority_enrollment_io as publication
from reno import personal_authority_namespace3 as namespace
from reno import personal_authority_namespace3_contract as ns_contract
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_effect_owner_deadline as namespace_owner
from reno import personal_authority_origin_reservation_contract as origin_contract
from reno import personal_authority_origin_reservation_owner_deadline as origin_owner
from reno import personal_mirror_contract as mirror
from reno import storage_lifecycle as lifecycle


EXPECTED_FIELDS = (
    "expected_origin_reservation_sha256", "expected_origin_observation_sha256",
    "expected_namespace_reservation_sha256", "expected_root_seal_sha256",
    "expected_header_sha256",
)
KINDS = ("enrollment_binding", "origin_init_intent", "local_contract",
         "init_seal", "genesis_grant", "authority_prefix")
RESULT_FIELDS = {
    "scope", "init_operation_id", "domain_id", "facility_id",
    "origin_reservation_sha256", "origin_observation_sha256",
    "namespace_reservation_sha256", "root_seal_sha256", "header_sha256",
    "binding_sha256", "prefix_sha256",
}


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def token():
    # Each borrowed entry gets its own original 30s; no caller expands it.
    return deadline.Namespace3Deadline(deadline.time.monotonic() + 30.0)


def assert_error(call, error_type, code):
    with pytest.raises(error_type) as raised:
        call()
    assert raised.value.code == code
    return raised.value


def tree(root):
    return {str(path.relative_to(root)): "directory" if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")}


def raw_database_node(env):
    paths = {suffix: Path(str(env["database"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["data"] / "node-identity.json"
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def original_bytes(env):
    data = env["data"]
    return tuple(path.read_bytes() for path in (
        data / origin_contract.RESERVATION_NAME, data / origin_contract.OBSERVATION_NAME,
        data / ns_contract.RESERVATION_NAME,
        data / ns_contract.ROOT_NAME / ns_contract.ROOT_SEAL_NAME,
        data / ns_contract.HEADER_NAME,
    ))


@pytest.fixture
def actual_prefix_env(tmp_path, monkeypatch):
    """New physical profile, actual personal receipt, and actual five originals."""
    root = tmp_path / "p"
    data = root / "data"
    data.mkdir(parents=True)
    local = root / "config.synthetic.json"
    local.write_bytes(encoded({"node_role": "home", "personal_features_enabled": True,
                              "field_checks_enabled": True}))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for name in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED", "RENO_FIELD_CHECKS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
                       "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    database = data / "reno.db"
    db.initialize(database)
    profile = lifecycle.StorageProfile(database, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with closing(sqlite3.connect(database)) as con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", mirror.META_KEYS))
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    mutation = uid()
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": mutation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {"title": "H01 独立装修记录", "question": "材料与施工次序待核对",
                    "content_snapshot": {"kind": "note", "text": "新 profile 的真实 mutation receipt"}},
    })
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as con:
        assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (mutation,)).fetchone() == (1,)
    arguments = {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}
    pair = origin_owner.reserve_origin(profile, **arguments)
    first, second = ((data / name).read_bytes() for name in
                     (origin_contract.RESERVATION_NAME, origin_contract.OBSERVATION_NAME))
    assert (pair.reservation_sha256, pair.observation_sha256) == (sha(first), sha(second))
    origin_contract.cross_validate(first, second)
    namespace_facts = namespace_owner.initialize_namespace3(profile, **arguments,
        expected_origin_reservation_sha256=sha(first), expected_origin_observation_sha256=sha(second))
    env = {"root": root, "data": data, "database": database, "profile": profile, "args": arguments}
    originals = original_bytes(env)
    ns_contract.cross_validate_complete(*originals)
    assert (namespace_facts.namespace_reservation_sha256, namespace_facts.root_seal_sha256,
            namespace_facts.header_sha256) == tuple(sha(raw) for raw in originals[2:])
    env["originals"] = originals
    env["expected"] = dict(zip(EXPECTED_FIELDS, (sha(raw) for raw in originals)))
    assert lifecycle.current_guard() is None
    return env



# This appended block is static-only until Root admits the stable candidate.
import ctypes
import gc
import io
import tempfile
import weakref
from collections import Counter
from dataclasses import FrozenInstanceError, fields

from reno import personal_authority_owned_ex_group as group
from reno import personal_authority_owned_ex_deadline as owned
from reno import personal_authority_origin_capture_deadline as capture
from reno import sync_media as safe

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r865 actual Windows owned EX group")
OPERATION = ("00000000-0000-4000-8000-000000000865",
             "00000000-0000-4000-8000-000000000866",
             "00000000-0000-4000-8000-000000000867")


@pytest.fixture(autouse=True)
def isolated_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is None


def forbidden(*_args, **_kwargs):
    pytest.fail("retired getter/retry reached a forbidden provider or native seam")


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def owned_error(call, suffix):
    return assert_error(call, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_" + suffix)


def legal_bool(value):
    assert type(value) is int and -(1 << 31) <= value < (1 << 31) and value != 0
    return value


def make_env(tmp_path, layout="four"):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert lifecycle.current_guard() is None
    root = tmp_path / "g"
    if layout == "coalesced":
        profile = lifecycle.StorageProfile(root / "r.db", root, root, root)
    else:
        assert layout == "four"
        profile = lifecycle.StorageProfile(root / "d/r.db", root / "m", root / "r", root / "c")
    expected_roots = tuple(sorted(set((profile.database.parent, profile.media,
                                      profile.reports, profile.checkpoints)), key=lifecycle._key))
    assert profile.roots == expected_roots and len(expected_roots) == (1 if layout == "coalesced" else 4)
    assert lifecycle.initialize(profile)["created_locks"] == len(expected_roots)
    return SimpleNamespace(profile=profile, paths=expected_roots)


def make_lease(env, *, captures=0):
    restricted = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
    effective = restricted.effective_deadline
    assert 0.0 < effective.absolute_monotonic - deadline._now() <= 30.0
    binding = owned._new_owned_ex_binding(OPERATION,
        parent_token=restricted.requested_deadline, effective_token=effective, scope_identity=object())
    selected = group.OwnedExclusiveDeadlineLease(env.profile, binding=binding,
                                                declared_capture_count=captures)
    return selected


def no_authority(receipt):
    assert receipt.source_closed is False
    if type(receipt) is group.ExclusiveGroupCloseReceiptV1:
        assert receipt.write_authority is False
    else:
        assert type(receipt) is group.CaptureLineageReceiptV1
        assert receipt.outer_ex_borrowed is True and receipt.owned_outer_ex_closed is False
    assert not hasattr(receipt, "__dict__")
    with pytest.raises(FrozenInstanceError):
        receipt.source_closed = True
    assert not ({item.name for item in fields(receipt)} & {"handle", "fd", "callback", "exception"})


def closed_generated(selected, *, partial_constructor=None, constructor_stage=None):
    # Check actual returned lower receipts; unconstructed/missing roots are not
    # invented as completed declarations or native closers.
    if partial_constructor is not None:
        assert partial_constructor is selected._roots[1].stream
        assert type(partial_constructor) is owned.LockStreamDeadlineLease
        assert constructor_stage in ("fileio", "buffer")
    for root in selected._roots:
        if root.stream is not None and hasattr(root.stream, "_phase"):
            value = root.stream.close_receipt
            assert value.fd_state in ("empty", "closed")
            if root.stream is partial_constructor:
                # The injected second constructor returned no actual wrapper.
                # Its cached UNKNOWN is retained; the sole known FD still closes.
                assert value.enter_returned is False and value.failed is True and value.normal_exit is False
                assert value.fd_state == "closed" and value.fd_close_owner == "numeric_owner"
                assert root.stream._buffer[0] is None
                if constructor_stage == "fileio":
                    assert root.stream._raw[0] is None
                    assert value.raw_state == "unknown" and value.buffer_state == "empty"
                else:
                    assert type(root.stream._raw[0]) is io.FileIO
                    assert value.raw_state == "closed" and value.buffer_state == "unknown"
            else:
                assert value.raw_state in ("empty", "closed") and value.buffer_state in ("empty", "closed")
        if root.directory_journal is not None and root.directory_journal._owner is root.directory:
            assert root.directory.owned_close_receipt.handle_states in (("empty",), ("closed",))


def settled(selected):
    receipt = selected.close_receipt
    assert type(receipt) is group.ExclusiveGroupCloseReceiptV1
    assert receipt.phase == "retired" and receipt.exit_attempted is True
    assert receipt.declared_root_count == len(selected._profile.roots)
    assert lifecycle._CURRENT.get() is None
    no_authority(receipt)
    return receipt


def terminal(selected):
    receipt = settled(selected)
    assert receipt.enter_returned is True and receipt.normal_exit is True and receipt.failed is False
    assert receipt.owned_outer_ex_closed is True and receipt.outer_ex_borrowed is False
    assert receipt.guard_retired is True and selected._guard._active is False
    assert receipt.context_reset_state == "reset" and receipt.effective_gate_passed is True
    assert receipt.declared_roots_complete is receipt.generated_resources_terminal_complete is True
    assert receipt.acquired_root_count == receipt.declared_root_count
    assert receipt.capture_lineage_complete is True
    for index, (dr, sr, lr) in enumerate(zip(receipt.directory_receipts,
            receipt.lock_stream_receipts, receipt.lock_receipts)):
        assert dr.root_ordinal == sr.root_ordinal == lr.root_ordinal == index
        assert dr.handle_states == ("closed",) and dr.close_consumed is True
        assert sr.fd_close_owner == "numeric_owner" and sr.fd_state == sr.raw_state == sr.buffer_state == "closed"
        assert lr.acquire_state == "acquired" and lr.unlock_state == "unlocked" and lr.unlock_consumed is True
    return receipt


@pytest.mark.parametrize("layout", ["four", "coalesced"], ids=["four-physical-roots", "one-physical-root"])
def test_actual_physical_groups_close_all_real_owners_and_consume_once(tmp_path, monkeypatch, layout):
    env, events = make_env(tmp_path, layout), []
    selected = make_lease(env)
    with selected as guard:
        assert type(guard) is lifecycle.StorageGuard and guard is lifecycle.require_guard(env.profile, exclusive=True)
        assert guard.verify() is guard and lifecycle._original_lock_dispatch(guard._locks) is True
        assert tuple(lock.directory.path for lock in guard._locks) == env.paths
        for lock in guard._locks:
            assert type(lock.stream) is io.BufferedReader and type(lock.stream.raw) is io.FileIO
            assert lock.stream.raw.closefd is False and lock.locked is True
        real_unlock, real_fd, real_dir = lifecycle._owned_lock_unlock_file, owned._close_fd, safe._owned_directory_close_handle
        def unlock(*args):
            result = legal_bool(real_unlock(*args))
            events.append("unlock")
            return result
        def close_fd(fd):
            assert real_fd(fd) is None
            events.append("fd")
        def close_dir(handle):
            result = legal_bool(real_dir(handle))
            events.append("directory")
            return result
        monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)
        monkeypatch.setattr(owned, "_close_fd", close_fd)
        monkeypatch.setattr(safe, "_owned_directory_close_handle", close_dir)
    receipt = terminal(selected)
    assert events == ["unlock", "fd", "directory"] * len(env.paths)
    assert group._consume_owned_ex_group(selected) is receipt
    owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")
    assert selected.close_receipt is receipt


@pytest.mark.parametrize("stage", ["fileio", "buffer"], ids=["fileio-constructor", "buffer-constructor"])
def test_controlled_partial_second_root_constructor_keeps_prior_and_known_fd_cleanup(tmp_path, monkeypatch, stage):
    selected = make_lease(make_env(tmp_path))
    calls, first = Counter(), MemoryError("r865 wrapper constructor first")
    name = "_file_io" if stage == "fileio" else "_buffered_reader"
    real = getattr(owned, name)
    def construct(*args, **kwargs):
        calls["constructor"] += 1
        if calls["constructor"] == 2:
            raise first
        return real(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(owned, name, construct)
        same_error(selected.__enter__, first)
    receipt = settled(selected)
    assert calls["constructor"] == 2 and receipt.acquired_root_count == 1
    assert receipt.declared_roots_complete is False and receipt.normal_exit is False
    closed_generated(selected, partial_constructor=selected._roots[1].stream, constructor_stage=stage)
    assert receipt.generated_resources_terminal_complete is False
    same_error(lambda: group._consume_owned_ex_group(selected), first)
    owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")


def test_controlled_second_directory_enter_query_failure_closes_inside_original_api(tmp_path, monkeypatch):
    selected = make_lease(make_env(tmp_path))
    real, calls, first = safe._owned_directory_get_info, Counter(), SystemExit(865)
    def query(*args):
        calls["query"] += 1
        if calls["query"] == 2:
            raise first
        return real(*args)
    with monkeypatch.context() as patch:
        patch.setattr(safe, "_owned_directory_get_info", query)
        same_error(selected.__enter__, first)
    receipt = settled(selected)
    assert calls["query"] == 2 and receipt.enter_returned is False
    assert receipt.acquired_root_count == 1 and receipt.declared_roots_complete is False
    closed_generated(selected)
    assert selected._roots[1].directory.owned_close_receipt.enter_returned is False


@pytest.mark.parametrize("after", [False, True], ids=["before-native-acquire", "after-native-acquire"])
def test_controlled_opaque_second_acquire_preserves_first_and_retires_other_owners(tmp_path, monkeypatch, after):
    selected, calls, first = make_lease(make_env(tmp_path)), Counter(), SystemExit(866)
    real_acquire, real_unlock = lifecycle._owned_lock_lock_file, lifecycle._owned_lock_unlock_file
    def acquire(*args):
        calls["acquire_expression"] += 1
        if calls["acquire_expression"] == 2:
            if after:
                legal_bool(real_acquire(*args))
                calls["controlled_real_acquire"] += 1
            raise first
        return real_acquire(*args)
    def unlock(*args):
        result = real_unlock(*args)
        calls["unlock"] += 1
        assert type(result) is int
        if calls["unlock"] == 1 and not after:
            assert result == 0
        else:
            legal_bool(result)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "_owned_lock_lock_file", acquire)
        patch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)
        same_error(selected.__enter__, first)
    receipt = settled(selected)
    assert calls["acquire_expression"] == calls["unlock"] == 2
    assert receipt.acquired_root_count == 1 and receipt.normal_exit is False
    opaque = selected._roots[1].lock.owned_lock_receipt
    assert opaque.acquire_state == "unknown" and opaque.acquire_returned is False
    assert opaque.unlock_consumed is True and opaque.unlock_state == ("unlocked" if after else "unknown")
    closed_generated(selected)
    same_error(lambda: group._consume_owned_ex_group(selected), first)


@pytest.mark.parametrize("after", [False, True], ids=["before-directory-create", "after-directory-create"])
def test_controlled_opaque_directory_create_unknown_uses_only_fixture_known_handle_recovery(tmp_path, monkeypatch, after):
    selected, witness, calls = make_lease(make_env(tmp_path)), {}, Counter()
    first, real_create, real_close = SystemExit(867), safe._owned_directory_create_file, safe._owned_directory_close_handle
    def create(path):
        calls["create_expression"] += 1
        if calls["create_expression"] == 2:
            if after:
                actual = real_create(path)
                assert type(actual) is int and actual != ctypes.c_void_p(-1).value and actual >= 0
                witness["handle"] = actual
            raise first
        return real_create(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(safe, "_owned_directory_create_file", create)
            same_error(selected.__enter__, first)
        receipt = settled(selected)
        assert receipt.normal_exit is False and receipt.declared_roots_complete is False
        assert selected._roots[0].directory.owned_close_receipt.handle_states == ("closed",)
        assert selected._roots[0].stream.close_receipt.fd_state == "closed"
        assert selected._roots[1].directory.owned_close_receipt.handle_states == ("unknown",)
        assert calls["create_expression"] == 2
    finally:
        # The fixture witnessed this exact native return. No probing UNKNOWN,
        # no subject retry, and no upgrade of the failed receipt follows.
        if "handle" in witness:
            legal_bool(real_close(witness["handle"]))
            calls["fixture_recovery"] += 1
    assert calls["fixture_recovery"] == int(after)
    assert selected._roots[1].directory.owned_close_receipt.handle_states == ("unknown",)


@pytest.mark.parametrize("kind, seam", [("ordinary", "unlock"), ("memory", "fd"),
    ("interrupt", "directory"), ("exit", "unlock")], ids=["body-runtime-unlock", "body-memory-fd", "body-interrupt-directory", "body-exit-unlock"])
def test_controlled_body_first_survives_real_postclose_faults_and_all_independent_cleanup(tmp_path, monkeypatch, kind, seam):
    selected, calls = make_lease(make_env(tmp_path)), Counter()
    first = {"ordinary": RuntimeError("body first"), "memory": MemoryError("body first"),
             "interrupt": KeyboardInterrupt("body first"), "exit": SystemExit(868)}[kind]
    real_unlock, real_fd, real_dir = lifecycle._owned_lock_unlock_file, owned._close_fd, safe._owned_directory_close_handle
    def unlock(*args):
        calls["unlock"] += 1
        result = legal_bool(real_unlock(*args))
        if seam == "unlock":
            raise OSError("controlled post-unlock")
        return result
    def close_fd(fd):
        calls["fd"] += 1
        assert real_fd(fd) is None
        if seam == "fd":
            raise OSError("controlled post-fd-close")
    def close_dir(handle):
        calls["directory"] += 1
        result = legal_bool(real_dir(handle))
        if seam == "directory":
            raise OSError("controlled post-handle-close")
        return result
    def invoke():
        with selected:
            raise first
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)
        patch.setattr(owned, "_close_fd", close_fd)
        patch.setattr(safe, "_owned_directory_close_handle", close_dir)
        same_error(invoke, first)
        receipt = settled(selected)
        assert calls == {"unlock": 4, "fd": 4, "directory": 4}
        assert receipt.failed is True and receipt.normal_exit is False and receipt.owned_outer_ex_closed is False
        same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"unlock": 4, "fd": 4, "directory": 4}


@pytest.mark.parametrize("stage", ["before", "after"], ids=["reset-before-call", "reset-after-actual-return"])
def test_controlled_context_reset_unknown_is_distinct_from_actual_native_closure(tmp_path, monkeypatch, stage):
    selected, calls, first = make_lease(make_env(tmp_path)), Counter(), SystemExit(869)
    real_reset = group._reset_current
    def reset(value):
        calls["reset_expression"] += 1
        if stage == "after":
            assert real_reset(value) is None
            calls["actual_reset"] += 1
        raise first
    try:
        with selected:
            monkeypatch.setattr(group, "_reset_current", reset)
    except SystemExit as raised:
        assert raised is first
        value = selected.close_receipt
        assert value.context_reset_state == "unknown" and value.generated_resources_terminal_complete is True
        assert value.normal_exit is False and value.owned_outer_ex_closed is False
        assert calls["reset_expression"] == 1
        closed_generated(selected)
    else:
        pytest.fail("context-reset failure must prevent normal delivery")
    finally:
        monkeypatch.setattr(group, "_reset_current", real_reset)
        if stage == "before" and selected._current_token is not None:
            assert lifecycle._CURRENT.get() is selected._guard
            assert real_reset(selected._current_token) is None
            calls["fixture_reset_recovery"] += 1
    assert lifecycle._CURRENT.get() is None and calls["fixture_reset_recovery"] == int(stage == "before")


def test_controlled_final_effective_deadline_keeps_physical_facts_but_blocks_consumer(tmp_path, monkeypatch):
    selected, first = make_lease(make_env(tmp_path)), owned.OwnedExDeadlineError("DEADLINE")
    real_clock = group._clock
    def clock(actual):
        assert actual is selected.deadline_token
        if selected._phase == "retired":
            raise first
        return real_clock(actual)
    with monkeypatch.context() as patch:
        patch.setattr(group, "_clock", clock)
        same_error(lambda: run_empty(selected), first)
    value = settled(selected)
    assert value.generated_resources_terminal_complete is True and value.context_reset_state == "reset"
    assert value.effective_gate_passed is False and value.normal_exit is False
    closed_generated(selected)
    same_error(lambda: group._consume_owned_ex_group(selected), first)


def run_empty(selected):
    with selected:
        pass


def test_actual_cached_group_facts_do_not_read_lower_owners(tmp_path, monkeypatch):
    selected = make_lease(make_env(tmp_path))
    run_empty(selected)
    receipt = terminal(selected)
    with monkeypatch.context() as patch:
        for module, names in ((owned, ("_binding", "_clock", "_create_file", "_get_info", "_open_osfhandle", "_close_handle", "_close_fd")),
                (safe, ("_owned_directory_clock", "_owned_directory_create_file", "_owned_directory_get_info", "_owned_directory_close_handle")),
                (lifecycle, ("_owned_lock_clock", "_owned_lock_get_osfhandle", "_owned_lock_lock_file", "_owned_lock_unlock_file"))):
            for name in names:
                patch.setattr(module, name, forbidden)
        patch.setattr(safe._Directory, "owned_close_receipt", property(forbidden))
        patch.setattr(owned.LockStreamDeadlineLease, "close_receipt", property(forbidden))
        patch.setattr(lifecycle._FileLock, "owned_lock_receipt", property(forbidden))
        for _ in range(2):
            assert selected.close_receipt is receipt and selected.deadline_token is selected._token


@pytest.mark.parametrize("kind", ["getter-error", "getter-dto", "getter-writer"], ids=["error-consumes", "dto-consumes", "writer-grant-consumes"])
def test_controlled_once_attempt_precedes_receipt_getter_and_invalid_result_never_retries(tmp_path, monkeypatch, kind):
    selected, calls = make_lease(make_env(tmp_path)), Counter()
    run_empty(selected)
    receipt, first = terminal(selected), MemoryError("receipt getter first")
    def getter(actual):
        assert actual is selected and selected._consume_attempted is True
        calls["getter"] += 1
        if kind == "getter-error":
            raise first
        if kind == "getter-dto":
            return SimpleNamespace(**{field.name: getattr(receipt, field.name) for field in fields(receipt)})
        return replace(receipt, write_authority=True)
    with monkeypatch.context() as patch:
        patch.setattr(group.OwnedExclusiveDeadlineLease, "close_receipt", property(getter))
        if kind == "getter-error":
            same_error(lambda: group._consume_owned_ex_group(selected), first)
        else:
            owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_RESULT")
        owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")
    assert calls == {"getter": 1} and selected.close_receipt is receipt


@pytest.mark.parametrize("kind", ["equal-token", "operation", "owner", "roots", "captures", "owner-record-graft"], ids=["equal-token-identity", "operation-triple", "wrong-owner", "different-root-tuple", "different-capture-list", "foreign-closed-owner-records"])
def test_controlled_registered_group_tamper_rejects_before_getter_and_consumes_attempt(tmp_path, monkeypatch, kind):
    selected = make_lease(make_env(tmp_path))
    if kind == "owner-record-graft":
        check_foreign_closed_owner_records_before_actual_exit(selected, monkeypatch)
        return
    run_empty(selected)
    receipt = terminal(selected)
    if kind == "equal-token":
        selected._token = deadline.Namespace3Deadline(selected._token.absolute_monotonic)
    elif kind == "operation":
        object.__setattr__(selected._binding, "_operation", OPERATION[:2] + (uid(),))
    elif kind == "owner":
        selected._owner = selected._owner[:2] + (object(),)
    elif kind == "roots":
        selected._roots = tuple(list(selected._roots))
    else:
        selected._captures = list(selected._captures)
    with monkeypatch.context() as patch:
        patch.setattr(group.OwnedExclusiveDeadlineLease, "close_receipt", property(forbidden))
        owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_ARGUMENT")
        owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")
    assert selected._receipt is receipt and selected._consume_attempted is True


def test_dto_and_subclass_cannot_register_or_grant_group_authority(tmp_path):
    env = make_env(tmp_path)
    dto = SimpleNamespace(normal_exit=True, owned_outer_ex_closed=True, source_closed=True, write_authority=True)
    owned_error(lambda: group._consume_owned_ex_group(dto), "INVALID_ARGUMENT")
    def subclass():
        class Foreign(group.OwnedExclusiveDeadlineLease):
            pass
    owned_error(subclass, "INVALID_ARGUMENT")
    assert lifecycle.current_guard() is None and env.profile.roots


def test_actual_same_binding_remains_once_claimed_after_successful_consume(tmp_path):
    env = make_env(tmp_path)
    selected = make_lease(env)
    run_empty(selected)
    receipt = terminal(selected)
    assert group._consume_owned_ex_group(selected) is receipt
    owned_error(lambda: group.OwnedExclusiveDeadlineLease(env.profile, binding=selected._binding), "CLOSED")
    owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")


@pytest.mark.parametrize("kind", ["normal", "constructor", "body"], ids=["normal-consume-retirement", "failed-before-native-constructor", "settled-body-failure-retirement"])
def test_actual_registry_does_not_keep_unreferenced_settled_lease_alive(tmp_path, monkeypatch, kind):
    env = make_env(tmp_path)
    selected = make_lease(env)
    if kind == "normal":
        run_empty(selected)
        assert group._consume_owned_ex_group(selected) is terminal(selected)
    elif kind == "body":
        first = SystemExit(870)
        try:
            with selected:
                raise first
        except SystemExit as caught:
            assert caught is first
        assert settled(selected).generated_resources_terminal_complete is True
        del first
    else:
        # Use an independently fresh exact binding and allocation; constructor
        # fails before native creation. The actual external object stays alive
        # during same-binding rejection, then the weak live registry must let go.
        restricted = deadline._RestrictedBudget(token())
        binding = owned._new_owned_ex_binding(OPERATION, parent_token=restricted.requested_deadline,
            effective_token=restricted.effective_deadline, scope_identity=object())
        selected = object.__new__(group.OwnedExclusiveDeadlineLease)
        first = SystemExit(871)
        with monkeypatch.context() as patch:
            patch.setattr(group, "_clock", lambda _token: (_ for _ in ()).throw(first))
            same_error(lambda: group.OwnedExclusiveDeadlineLease.__init__(selected, env.profile, binding=binding), first)
        owned_error(lambda: group.OwnedExclusiveDeadlineLease(env.profile, binding=binding), "CLOSED")
        assert lifecycle._CURRENT.get() is None
        del first
    reference = weakref.ref(selected)
    del selected
    gc.collect()
    assert reference() is None
    # GC is an object retention check. Closure was checked before dropping any
    # settled native owner; GC never supplies a native-close receipt or grant.


@pytest.mark.parametrize("kind", ["state", "restricted"], ids=["same-first-state", "same-restricted-budget"])
def test_controlled_two_capture_invocations_cannot_reuse_first_state_or_budget(tmp_path, kind):
    selected = make_lease(make_env(tmp_path), captures=2)
    with selected as guard:
        budget = capture._budget(selected.deadline_token, None)
        first = group._new_owned_capture(selected, operation=OPERATION,
            parent_token=selected.deadline_token, effective_token=budget.effective_deadline)
        group._begin_capture(first, budget, guard)
        group._finish_capture(first, budget.state, completed=True)
        assert first.close_receipt.context_completed is True and first.close_receipt.normal_exit is False
        no_authority(first.close_receipt)
        second_budget = budget if kind == "state" else capture._OriginBudget(budget.restricted, budget.effective_deadline, None)
        second = group._new_owned_capture(selected, operation=OPERATION,
            parent_token=selected.deadline_token, effective_token=second_budget.effective_deadline)
        owned_error(lambda: group._begin_capture(second, second_budget, guard), "INVALID_ARGUMENT")
    value = settled(selected)
    assert value.capture_lineage_complete is False and value.owned_outer_ex_closed is False
    owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_RESULT")


def capture_spy(monkeypatch, *, tamper=None):
    seen, consumed = [], []
    real_new, real_finish, real_consume = group._new_owned_capture, group._finish_capture, group._consume_owned_ex_group
    def new(*args, **kwargs):
        value = real_new(*args, **kwargs)
        seen.append(value)
        return value
    def finish(*args, **kwargs):
        result = real_finish(*args, **kwargs)
        assert result is None
        return result
    def consume(actual):
        assert actual.close_receipt.source_closed is actual.close_receipt.write_authority is False
        if tamper is not None and actual._consume_attempted is False:
            tamper(actual)
        result = real_consume(actual)
        consumed.append((actual, result))
        return result
    monkeypatch.setattr(group, "_new_owned_capture", new)
    monkeypatch.setattr(group, "_finish_capture", finish)
    monkeypatch.setattr(group, "_consume_owned_ex_group", consume)
    return seen, consumed


def capture_lineage(actual, count):
    receipt = terminal(actual)
    assert len(actual._captures) == receipt.declared_capture_count == count
    identities, states, restricted, budgets = [], [], [], []
    for ordinal, child in enumerate(actual._captures):
        assert type(child) is group.BorrowedCaptureDeadlineLease
        assert child._parent is actual.deadline_token and child._guard is actual._guard
        assert child._identity is not actual._binding.scope_identity
        assert child._effective is child._state.token is child._budget.effective_deadline
        assert child._effective.absolute_monotonic <= child._parent.absolute_monotonic
        assert type(child._state) is capture._FirstState and type(child._budget) is capture._OriginBudget
        assert type(child._restricted) is deadline._RestrictedBudget
        cr = child.close_receipt
        assert cr is receipt.capture_receipts[ordinal] and cr.capture_ordinal == ordinal
        assert cr.invocation_registered is cr.context_completed is cr.normal_exit is True
        assert cr.failed is False
        no_authority(cr)
        assert child._sources[0] is not None and child._sources[1] is not None
        assert child._sources[2] is not None and child._sources[5] is not None
        assert all(value is None or attempted is True for value, attempted in zip(child._sources, cr.lower_fact_reads_attempted))
        identities.append(child._identity); states.append(child._state)
        restricted.append(child._restricted); budgets.append(child._budget)
    for values in (identities, states, restricted, budgets):
        assert len({id(value) for value in values}) == count
    # The original RestrictedBudget can intentionally reuse an unchanged parent
    # token. Identity freshness is asserted for captures/state/budget, not tokens.
    return receipt


def test_actual_observe_has_one_real_capture_and_consumes_owned_ex_without_source_or_writer_grant(actual_prefix_env, monkeypatch):
    env = actual_prefix_env
    before = raw_database_node(env)
    seen, consumed = capture_spy(monkeypatch)
    result = capture.observe_owned_ex(env["profile"], **env["args"])
    assert type(result) is capture.ordinary.OriginObservation
    assert len(seen) == len(consumed) == 1 and seen[0] is consumed[0][0]._captures[0]
    actual, receipt = consumed[0]
    assert capture_lineage(actual, 1) is receipt
    assert actual._consume_attempted is True
    assert original_bytes(env) == env["originals"] and raw_database_node(env) == before
    owned_error(lambda: group._consume_owned_ex_group(actual), "CLOSED")
    cached_capture = seen[0].close_receipt
    with monkeypatch.context() as patch:
        for cls, names in ((capture.directories.DeadlineDirectory, ("close_receipt",)),
                (capture.images.CommittedImageDeadlineLease, ("evidence", "private_pin_close_receipt", "sql_close_receipt", "temp_cleanup_receipt")),
                (capture.live_sql.DeadlineLiveSqlHeldFile, ("close_receipt",)),
                (capture.nodes.DeadlineHeldFile, ("close_receipt",))):
            for name in names:
                patch.setattr(cls, name, property(forbidden))
        patch.setattr(group, "_clock", forbidden)
        patch.setattr(group, "_capture_registered", forbidden)
        for _ in range(2):
            assert seen[0].close_receipt is cached_capture and actual.close_receipt is receipt


def test_actual_enrollment_publish_and_cold_verify_each_have_two_distinct_real_captures(actual_prefix_env, monkeypatch):
    env, original_sources = actual_prefix_env, raw_database_node(actual_prefix_env)
    seen, consumed = capture_spy(monkeypatch)
    options = {**env["args"], **env["expected"]}
    published = enrollment.publish_prefix_owned_ex(env["profile"], **options)
    assert type(published) is enrollment.AuthorityPrefixFiles and published.scope == enrollment.SCOPE
    assert len(seen) == 2 and len(consumed) == 1
    first, first_receipt = consumed[0]
    assert capture_lineage(first, 2) is first_receipt
    written = tree(env["root"])
    hashes = {"expected_binding_sha256": published.binding_sha256,
              "expected_prefix_sha256": published.prefix_sha256}
    verified = enrollment.verify_prefix_owned_ex(env["profile"], **options, **hashes)
    assert verified == published and len(seen) == 4 and len(consumed) == 2
    second, second_receipt = consumed[1]
    assert second is not first and capture_lineage(second, 2) is second_receipt
    assert first._binding.scope_identity is not second._binding.scope_identity
    assert len({id(value._identity) for value in seen}) == 4
    assert tree(env["root"]) == written and original_bytes(env) == env["originals"]
    assert raw_database_node(env) == original_sources
    assert lifecycle.current_guard() is None


@pytest.mark.parametrize("kind", ["identity", "state", "restricted", "parent-token", "operation", "source-dto"], ids=["capture-identity-tamper", "first-state-tamper", "restricted-budget-tamper", "equal-parent-token", "capture-operation-tamper", "source-owner-dto"])
def test_controlled_actual_capture_lineage_tamper_prevents_once_delivery_after_real_close(actual_prefix_env, monkeypatch, kind):
    env, selected, original_sources = actual_prefix_env, [], raw_database_node(actual_prefix_env)
    def tamper(actual):
        selected.append(actual)
        capture_lineage(actual, 1)
        child = actual._captures[0]
        if kind == "identity":
            child._identity = object()
        elif kind == "state":
            child._state = capture._FirstState(child._effective)
        elif kind == "restricted":
            child._restricted = deadline._RestrictedBudget(child._effective)
        elif kind == "parent-token":
            child._parent = deadline.Namespace3Deadline(child._parent.absolute_monotonic)
        elif kind == "operation":
            child._operation = tuple(list(child._operation))
        else:
            child._sources[5] = SimpleNamespace(close_receipt=child._sources[5].close_receipt)
    _seen, consumed = capture_spy(monkeypatch, tamper=tamper)
    with pytest.raises(owned.OwnedExDeadlineError):
        capture.observe_owned_ex(env["profile"], **env["args"])
    assert len(selected) == 1 and consumed == []
    actual = selected[0]
    assert actual._consume_attempted is True and actual._guard._active is False
    owned_error(lambda: group._consume_owned_ex_group(actual), "CLOSED")
    assert lifecycle.current_guard() is None and original_bytes(env) == env["originals"]
    assert raw_database_node(env) == original_sources


@pytest.mark.parametrize("role", ["source_directory", "image", "held_main", "node"], ids=["foreign-directory", "foreign-image", "foreign-held-main", "foreign-node"])
def test_controlled_foreign_exact_closed_source_owner_cannot_be_grafted_between_actual_captures(actual_prefix_env, monkeypatch, role):
    env, selected = actual_prefix_env, []
    def graft(actual):
        selected.append(actual)
        capture_lineage(actual, 2)
        left, right = actual._captures
        index = ("source_directory", "image", "held_main", "held_wal", "held_journal", "node").index(role)
        foreign = left._sources[index]
        assert foreign is not None and foreign is not right._sources[index]
        right._sources[index] = foreign
    _seen, consumed = capture_spy(monkeypatch, tamper=graft)
    with pytest.raises(owned.OwnedExDeadlineError):
        enrollment.publish_prefix_owned_ex(env["profile"], **env["args"], **env["expected"])
    assert len(selected) == 1 and consumed == []
    actual = selected[0]
    assert actual._consume_attempted is True and actual._guard._active is False
    owned_error(lambda: group._consume_owned_ex_group(actual), "CLOSED")
    assert original_bytes(env) == env["originals"] and lifecycle.current_guard() is None


@pytest.mark.parametrize("role", ["source_directory", "image", "held_main", "node"], ids=["register-foreign-directory", "register-foreign-image", "register-foreign-held-main", "register-foreign-node"])
def test_controlled_register_rejects_foreign_exact_closed_owner_before_cache_can_authorize_new_capture(actual_prefix_env, monkeypatch, role):
    env, foreign, selected, calls = actual_prefix_env, {}, [], Counter()
    real_register = group._register_capture_source
    def register(value, actual_role, owner):
        if value._ordinal == 0:
            foreign[actual_role] = owner
        elif actual_role == role:
            selected.append(value._group)
            other = foreign[actual_role]
            assert type(other) is type(owner) and other is not owner
            assert value._group._captures[0].close_receipt.normal_exit is True
            calls["graft_attempt"] += 1
            # This targets owner->invocation registration, before source_pins
            # are frozen. The first capture's genuine closed owner is foreign.
            return real_register(value, actual_role, other)
        return real_register(value, actual_role, owner)
    monkeypatch.setattr(group, "_register_capture_source", register)
    with pytest.raises(owned.OwnedExDeadlineError) as raised:
        enrollment.publish_prefix_owned_ex(env["profile"], **env["args"], **env["expected"])
    first = raised.value
    assert calls == {"graft_attempt": 1} and len(selected) == 1
    actual = selected[0]
    assert actual._guard._active is False and lifecycle._CURRENT.get() is None
    assert actual.close_receipt.normal_exit is False and actual.close_receipt.owned_outer_ex_closed is False
    same_error(lambda: group._consume_owned_ex_group(actual), first)
    owned_error(lambda: group._consume_owned_ex_group(actual), "CLOSED")
    assert original_bytes(env) == env["originals"]


@pytest.mark.parametrize("count", [True, -1, 3, 1.0, None], ids=["bool-not-count", "negative-count", "third-capture", "float-not-count", "none-not-count"])
def test_invalid_declared_capture_count_rejects_before_any_owned_native_creation(tmp_path, monkeypatch, count):
    env = make_env(tmp_path)
    restricted = deadline._RestrictedBudget(token())
    binding = owned._new_owned_ex_binding(OPERATION, parent_token=restricted.requested_deadline,
        effective_token=restricted.effective_deadline, scope_identity=object())
    with monkeypatch.context() as patch:
        patch.setattr(safe, "_owned_directory_create_file", forbidden)
        patch.setattr(owned, "_create_file", forbidden)
        owned_error(lambda: group.OwnedExclusiveDeadlineLease(env.profile,
            binding=binding, declared_capture_count=count), "INVALID_ARGUMENT")
    assert lifecycle._CURRENT.get() is None


@pytest.mark.parametrize("kind", ["operation-list", "wrong-operation", "parent-dto", "equal-parent", "effective-dto", "widened-effective"], ids=["capture-operation-list", "capture-wrong-operation", "capture-parent-dto", "capture-equal-parent-ref", "capture-effective-dto", "capture-widened-effective"])
def test_invalid_capture_operation_and_token_lineage_cannot_create_registered_source_context(tmp_path, kind):
    selected = make_lease(make_env(tmp_path), captures=1)
    with selected:
        operation, parent, effective = OPERATION, selected.deadline_token, selected.deadline_token
        if kind == "operation-list":
            operation = list(operation)
        elif kind == "wrong-operation":
            operation = operation[:2] + (uid(),)
        elif kind == "parent-dto":
            parent = SimpleNamespace(absolute_monotonic=parent.absolute_monotonic)
        elif kind == "equal-parent":
            parent = deadline.Namespace3Deadline(parent.absolute_monotonic)
        elif kind == "effective-dto":
            effective = SimpleNamespace(absolute_monotonic=effective.absolute_monotonic)
        else:
            effective = deadline.Namespace3Deadline(effective.absolute_monotonic + 0.001)
        owned_error(lambda: group._new_owned_capture(selected, operation=operation,
            parent_token=parent, effective_token=effective), "INVALID_ARGUMENT")
        assert selected._captures == [None]
    receipt = settled(selected)
    assert receipt.generated_resources_terminal_complete is True and receipt.capture_lineage_complete is False
    assert receipt.owned_outer_ex_closed is False
    owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_RESULT")


def test_actual_context_completion_without_declared_capture_does_not_grant_closure_or_writer(tmp_path):
    selected = make_lease(make_env(tmp_path), captures=1)
    run_empty(selected)
    receipt = settled(selected)
    assert receipt.context_reset_state == "reset" and receipt.guard_retired is True
    assert receipt.declared_roots_complete is receipt.generated_resources_terminal_complete is True
    assert receipt.declared_capture_count == 1 and receipt.capture_receipts == (None,)
    assert receipt.capture_lineage_complete is receipt.owned_outer_ex_closed is receipt.normal_exit is False
    owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_RESULT")
    owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")


@pytest.mark.parametrize("reported", ["raise", "true", "none"], ids=["outer-exit-raises-after-real-close", "outer-exit-true-after-real-close", "outer-exit-none-after-real-close"])
def test_controlled_outer_context_postexit_fault_cannot_keep_cached_normal_or_owned_close(tmp_path, reported):
    env = make_env(tmp_path)

    class AfterActualContext:
        # Consumer-local protocol proxy: actual original context and actual
        # native owners remain authoritative. No lifecycle/Guard gate changes.
        def __init__(self, selected, outer_failure):
            self.actual = selected
            self.context = selected._context
            self.failure = outer_failure
            self.exit_calls = 0
            self.native_closed_before_fault = False

        def __enter__(self):
            return self.context.__enter__()

        def __exit__(self, kind, primary, traceback):
            self.exit_calls += 1
            assert self.context.__exit__(kind, primary, traceback) is False
            assert lifecycle._CURRENT.get() is None and self.actual._guard._active is False
            # Observe the actual lower owners after original ExitStack has
            # returned, before the controlled outer protocol fault is emitted.
            for root in self.actual._roots:
                assert root.directory.owned_close_receipt.handle_states == ("closed",)
                sr = root.stream.close_receipt
                assert sr.fd_state == sr.raw_state == sr.buffer_state == "closed"
                lr = root.lock.owned_lock_receipt
                assert lr.unlock_state == "unlocked" and lr.unlock_consumed is True
            self.native_closed_before_fault = True
            if reported == "raise":
                raise self.failure
            return True if reported == "true" else None

    def check_one(*, body_first=None):
        selected = make_lease(env)
        outer_first = RuntimeError("r865 outer context fault after actual native close")
        proxy = AfterActualContext(selected, outer_first)
        selected._context = proxy

        def invoke():
            with selected as guard:
                assert type(guard) is lifecycle.StorageGuard
                assert guard is lifecycle.require_guard(env.profile, exclusive=True)
                assert guard.verify() is guard
                if body_first is not None:
                    raise body_first

        if body_first is not None:
            same_error(invoke, body_first)
            first = body_first
        elif reported == "raise":
            same_error(invoke, outer_first)
            first = outer_first
        else:
            first = owned_error(invoke, "INVALID_RESULT")
        receipt = settled(selected)
        assert proxy.exit_calls == 1 and proxy.native_closed_before_fault is True
        assert receipt.enter_returned is True and receipt.declared_roots_complete is True
        assert receipt.generated_resources_terminal_complete is True
        assert receipt.context_reset_state == "reset" and receipt.guard_retired is True
        assert receipt.failed is True and receipt.normal_exit is False
        assert receipt.owned_outer_ex_closed is False
        assert receipt.source_closed is receipt.write_authority is False
        closed_generated(selected)
        same_error(lambda: group._consume_owned_ex_group(selected), first)
        owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert proxy.exit_calls == 1 and selected.close_receipt is receipt

    check_one()
    if reported == "raise":
        # Fourth scenario is a subassertion of this fixed case, not a fourth
        # test identity/pass: original body control remains first after cleanup
        # and the independent outer RuntimeError cannot replace it.
        check_one(body_first=SystemExit(86504))


@pytest.mark.parametrize("damage", ["none", "short", "alien"], ids=["capture-pins-none-preserves-exit", "capture-pins-short-preserves-memory", "capture-pins-alien-preserves-exit"])
def test_controlled_capture_metadata_fault_preserves_primary_and_consumes_finish_before_second_read(tmp_path, damage):
    selected = make_lease(make_env(tmp_path), captures=1)
    first = MemoryError("r865 metadata body first") if damage == "short" else SystemExit(86505)
    calls, captured = Counter(), []

    class AlienRegistration:
        # This is corrupted metadata, not a source owner or successful receipt.
        @property
        def actual_owner(self):
            calls["alien_getter"] += 1
            raise RuntimeError("alien metadata getter")

    def invoke():
        with selected as guard:
            assert guard is lifecycle.require_guard(selected._profile, exclusive=True)
            budget = capture._budget(selected.deadline_token, None)
            child = group._new_owned_capture(selected, operation=OPERATION,
                parent_token=selected.deadline_token, effective_token=budget.effective_deadline)
            group._begin_capture(child, budget, guard)
            captured.append(child)
            # No source-directory/image/held/node owner is constructed here.
            # A real budget/state/carrier alone cannot provide source closure.
            assert all(value is None for value in child._sources)
            child._source_pins = {"none": None, "short": (None,),
                "alien": (AlienRegistration(), None, None, None, None, None)}[damage]
            try:
                raise first
            except BaseException as primary:
                assert primary is first
                try:
                    returned = group._finish_capture(child, budget.state, primary=primary,
                        traceback=BaseException.__dict__["__traceback__"].__get__(primary))
                except BaseException as finish_error:
                    # A protected finish may propagate the exact original; it
                    # may never replace it with a metadata/representation error.
                    assert finish_error is first
                else:
                    assert returned is None
                assert child._first is first and child._finish_attempted is True
                receipt = child.close_receipt
                if receipt is not None:
                    assert type(receipt) is group.CaptureLineageReceiptV1
                    assert receipt.failed is True and receipt.normal_exit is False
                    assert receipt.owned_outer_ex_closed is False
                    no_authority(receipt)
                before_retry = dict(calls)
                owned_error(lambda: group._finish_capture(child, budget.state,
                    primary=primary, completed=True), "CLOSED")
                assert dict(calls) == before_retry and child.close_receipt is receipt
                raise

    same_error(invoke, first)
    assert len(captured) == 1 and all(value is None for value in captured[0]._sources)
    value = settled(selected)
    assert value.declared_roots_complete is value.generated_resources_terminal_complete is True
    assert value.context_reset_state == "reset" and value.guard_retired is True
    assert value.failed is True and value.normal_exit is value.owned_outer_ex_closed is False
    assert value.capture_lineage_complete is False
    closed_generated(selected)
    same_error(lambda: group._consume_owned_ex_group(selected), first)
    owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")


def check_foreign_closed_owner_records_before_actual_exit(selected, monkeypatch):
    env = SimpleNamespace(profile=selected._profile)
    foreign = make_lease(env)
    run_empty(foreign)
    terminal(foreign)
    foreign_records = foreign._owner_records
    foreign_owners = {
        "directory": tuple(root.directory for root in foreign._roots),
        "stream": tuple(root.stream for root in foreign._roots),
        "lock": tuple(root.lock for root in foreign._roots),
    }
    calls = Counter()
    descriptors = {
        "directory": safe._Directory.owned_close_receipt,
        "stream": owned.LockStreamDeadlineLease.close_receipt,
        "lock": lifecycle._FileLock.owned_lock_receipt,
    }
    def guarded_getter(role):
        def getter(actual):
            if any(actual is other for other in foreign_owners[role]):
                calls["foreign_getter"] += 1
                pytest.fail("foreign settled owner's cached getter was used for this EX")
            return descriptors[role].__get__(actual, type(actual))
        return property(getter)
    real_unlock, real_fd, real_dir = lifecycle._owned_lock_unlock_file, owned._close_fd, safe._owned_directory_close_handle
    current_fds, current_handles = set(), set()
    def unlock(*args):
        calls["unlock"] += 1
        return legal_bool(real_unlock(*args))
    def close_fd(fd):
        calls["fd"] += 1
        assert fd in current_fds and real_fd(fd) is None
    def close_dir(handle):
        calls["directory"] += 1
        assert handle in current_handles
        return legal_bool(real_dir(handle))
    with monkeypatch.context() as patch:
        patch.setattr(safe._Directory, "owned_close_receipt", guarded_getter("directory"))
        patch.setattr(owned.LockStreamDeadlineLease, "close_receipt", guarded_getter("stream"))
        patch.setattr(lifecycle._FileLock, "owned_lock_receipt", guarded_getter("lock"))
        with pytest.raises(owned.OwnedExDeadlineError) as raised:
            with selected as guard:
                assert guard is lifecycle.require_guard(selected._profile, exclusive=True)
                assert guard.verify() is guard
                current_fds.update(root.stream._fd[0] for root in selected._roots)
                current_handles.update(handle for root in selected._roots for handle in root.directory.handles)
                assert len(current_fds) == len(current_handles) == len(selected._roots) == 4
                # Replace only the public-facing record container after actual
                # registration, before any actual owner's retirement. Original
                # callback pins must retain this EX's obligations independently.
                selected._owner_records = foreign_records
                patch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)
                patch.setattr(owned, "_close_fd", close_fd)
                patch.setattr(safe, "_owned_directory_close_handle", close_dir)
        first = raised.value
        value = settled(selected)
        assert value.failed is True and value.normal_exit is value.owned_outer_ex_closed is False
        closed_generated(selected)
        assert calls == {"unlock": 4, "fd": 4, "directory": 4}
        owned_error(lambda: group._consume_owned_ex_group(selected), "INVALID_ARGUMENT")
        assert selected._first is first and selected._consume_attempted is True
        owned_error(lambda: group._consume_owned_ex_group(selected), "CLOSED")
        assert calls["foreign_getter"] == 0
