"""Actual isolated owner EX; no seeded successful inner result or authority.

The new owner wraps actual r818 production calls. Consumer-local seams only
record completed acquisition/close or inject explicit faults after real work.
They do not prove real native failures, full source_closed, or a writer grant.
"""
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_legacy_fence as fence
from reno import personal_authority_origin_reservation as locked
from reno import personal_authority_origin_reservation_contract as contract
from reno import personal_authority_origin_reservation_owner as owner
from reno import personal_mirror_contract as facts
from reno import personal_preservation_contract as preservation
from reno import storage_lifecycle as lifecycle


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def owner_env(tmp_path, monkeypatch):
    root = tmp_path / "actual-owner-home"
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
    with closing(sqlite3.connect(database)) as con, con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", facts.META_KEYS))
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    mutation_operation = uid()
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": mutation_operation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {"title": "绍兴防水核对", "question": "工序与材料品牌型号未定",
                    "content_snapshot": {"kind": "note", "text": "独立 owner 临时真实记录"}}})
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as con, con:
        assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (mutation_operation,)).fetchone() == (1,)
    return {"root": root, "data": data, "database": database, "profile": profile,
            "node": data / "node-identity.json", "meta": meta,
            "args": {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}}


def raw_sources(env):
    paths = {suffix: Path(str(env["database"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["node"]
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def tree(env):
    return {str(path.relative_to(env["root"])): "directory" if path.is_dir() else path.read_bytes()
            for path in env["root"].rglob("*")}


def sql_snapshot(env):
    with closing(sqlite3.connect(env["database"])) as con, con:
        result = {"meta": list(con.execute("SELECT key,value FROM meta ORDER BY key"))}
        for name, columns in preservation.PERSONAL_COLUMNS.items():
            query = 'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM "' + name + '"'
            result[name] = list(con.execute(query))
    return result


def anchors(env):
    return tuple((env["data"] / name).read_bytes()
                 for name in (contract.RESERVATION_NAME, contract.OBSERVATION_NAME))


def reserve(env, **options):
    return owner.reserve_origin(env["profile"], **env["args"], **options)


def resume(env, expected_first, **options):
    return owner.resume_origin(env["profile"], **env["args"],
        expected_reservation_sha256=expected_first, **options)


def assert_owner_error(call, suffix):
    with pytest.raises(owner.OriginReservationOwnerError) as raised:
        call()
    assert raised.value.code == "ORIGIN_RESERVATION_OWNER_" + suffix


def assert_released(env):
    assert lifecycle.current_guard() is None
    with pytest.raises(lifecycle.StorageLifecycleError) as raised:
        lifecycle.require_guard(env["profile"], exclusive=True)
    assert raised.value.code == "STORAGE_GUARD_REQUIRED"
    with lifecycle.exclusive(env["profile"], timeout=0) as guard:
        assert lifecycle.require_guard(env["profile"], exclusive=True) is guard
    assert lifecycle.current_guard() is None


def substitute_ex(monkeypatch, replacement):
    monkeypatch.setattr(owner, "lifecycle", SimpleNamespace(
        StorageProfile=lifecycle.StorageProfile, _profile=lifecycle._profile,
        current_guard=lifecycle.current_guard, require_guard=lifecycle.require_guard,
        exclusive=replacement))


def substitute_locked(monkeypatch, *, reserve_call=None, resume_call=None):
    monkeypatch.setattr(owner, "locked", SimpleNamespace(
        OriginReservationResult=locked.OriginReservationResult,
        reserve_origin_locked=locked.reserve_origin_locked if reserve_call is None else reserve_call,
        resume_origin_locked=locked.resume_origin_locked if resume_call is None else resume_call))


def acquisition_spy(monkeypatch):
    calls = []
    @contextmanager
    def acquired(*args, **kwargs):
        calls.append(True)
        with lifecycle.exclusive(*args, **kwargs) as guard:
            yield guard
    substitute_ex(monkeypatch, acquired)
    return calls


def close_then_fault(failure, closed):
    @contextmanager
    def replacement(*args, **kwargs):
        context = lifecycle.exclusive(*args, **kwargs)
        guard = context.__enter__()
        try:
            yield guard
        except BaseException as primary:
            context.__exit__(type(primary), primary, primary.__traceback__)
            closed.append(True)
            raise failure
        else:
            context.__exit__(None, None, None)
            closed.append(True)
            raise failure
    return replacement


def test_real_reserve_returns_same_inner_declaration_only_after_own_ex_close(owner_env, monkeypatch):
    env = owner_env
    assert lifecycle.current_guard() is None
    before_sources, before_sql, before_tree = raw_sources(env), sql_snapshot(env), tree(env)
    events, inner_results, inner_guards = [], [], []
    @contextmanager
    def owned_ex(*args, **kwargs):
        assert kwargs["timeout"] == 0
        with lifecycle.exclusive(*args, **kwargs) as guard:
            events.append("entered")
            yield guard
        assert lifecycle.current_guard() is None
        events.append("closed")
    def actual_inner(*args, **kwargs):
        guard = lifecycle.require_guard(env["profile"], exclusive=True)
        inner_guards.append(guard)
        result = locked.reserve_origin_locked(*args, **kwargs)
        inner_results.append(result)
        events.append("inner-returned")
        return result
    substitute_ex(monkeypatch, owned_ex)
    substitute_locked(monkeypatch, reserve_call=actual_inner)
    result = reserve(env)
    events.append("owner-returned")
    assert events == ["entered", "inner-returned", "closed", "owner-returned"]
    assert len(inner_results) == len(inner_guards) == 1 and result is inner_results[0]
    with pytest.raises(lifecycle.StorageLifecycleError):
        inner_guards[0].verify()
    first, second = anchors(env)
    contract.cross_validate(first, second)
    assert (result.init_operation_id, result.reservation_sha256, result.observation_sha256) == (
        env["args"]["init_operation_id"], sha(first), sha(second))
    assert set(result.__slots__) == {"init_operation_id", "reservation_sha256", "observation_sha256"}
    with pytest.raises(FrozenInstanceError):
        result.observation_sha256 = "0" * 64
    assert raw_sources(env) == before_sources and sql_snapshot(env) == before_sql
    after_tree = tree(env)
    for name in (contract.RESERVATION_NAME, contract.OBSERVATION_NAME):
        after_tree.pop(str((env["data"] / name).relative_to(env["root"])))
    assert after_tree == before_tree
    assert_released(env)


def test_real_resume_owns_fresh_ex_and_preserves_both_original_anchors(owner_env, monkeypatch):
    env = owner_env
    first_result = reserve(env)
    before = anchors(env)
    calls, inner_results = [], []
    def actual_resume(*args, **kwargs):
        calls.append(lifecycle.require_guard(env["profile"], exclusive=True))
        result = locked.resume_origin_locked(*args, **kwargs)
        inner_results.append(result)
        return result
    substitute_locked(monkeypatch, resume_call=actual_resume)
    result = resume(env, first_result.reservation_sha256,
                    expected_observation_sha256=first_result.observation_sha256)
    assert len(calls) == 1 and result is inner_results[0] and result == first_result
    assert anchors(env) == before
    assert_released(env)


def test_real_resume_binding_error_keeps_original_inner_error_and_releases_ex(owner_env, monkeypatch):
    env = owner_env
    reserve(env)
    before, failures, delivered = anchors(env), [], []
    def actual_resume(*args, **kwargs):
        try:
            return locked.resume_origin_locked(*args, **kwargs)
        except BaseException as failure:
            failures.append(failure)
            raise
    substitute_locked(monkeypatch, resume_call=actual_resume)
    with pytest.raises(locked.OriginReservationError) as raised:
        delivered.append(resume(env, "0" * 64))
    assert len(failures) == 1 and raised.value is failures[0]
    assert raised.value.code == "ORIGIN_RESERVATION_RESERVATION_MISMATCH"
    assert delivered == [] and anchors(env) == before
    assert_released(env)


def test_owner_declarations_leave_legacy_personal_writes_fenced_after_release(owner_env):
    env = owner_env
    reserve(env)
    before = raw_sources(env), sql_snapshot(env), anchors(env)
    assert_released(env)
    with pytest.raises(personal.PersonalError) as raised:
        personal.create_card(env["meta"]["default_project_id"], {"operation_id": uid(),
            "primary_epoch": int(env["meta"]["primary_epoch"]), "changes": {"title": "应拒绝"}})
    assert raised.value.code == fence.REQUIRED
    assert (raw_sources(env), sql_snapshot(env), anchors(env)) == before
    assert_released(env)


@pytest.mark.parametrize("action", ["reserve", "resume"])
@pytest.mark.parametrize("kind", ["shared", "exclusive"])
def test_existing_guard_is_refused_without_borrowing_or_releasing_callers_guard(owner_env, monkeypatch, action, kind):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    with getattr(lifecycle, kind)(env["profile"]) as guard:
        call = (lambda: reserve(env)) if action == "reserve" else (lambda: resume(env, "a" * 64))
        assert_owner_error(call, "GUARD_ALREADY_HELD")
        assert lifecycle.current_guard() is guard and guard.verify() is guard
    assert calls == [] and tree(env) == before
    assert_released(env)


@pytest.mark.parametrize("field", ["domain_id", "facility_id", "init_operation_id"])
@pytest.mark.parametrize("bad", ["00000000-0000-0000-0000-000000000000",
                                  "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 1],
                         ids=["nil", "uppercase", "integer"])
def test_invalid_ids_are_refused_before_any_new_ex_acquisition(owner_env, monkeypatch, field, bad):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    changed = {**env, "args": {**env["args"], field: bad}}
    assert_owner_error(lambda: reserve(changed), "INVALID_ARGUMENT")
    assert calls == [] and tree(env) == before
    assert lifecycle.current_guard() is None


@pytest.mark.parametrize("bad", [None, "A" * 64, "short", 1], ids=["none", "uppercase", "short", "integer"])
def test_resume_invalid_expected_first_sha_is_refused_before_new_ex(owner_env, monkeypatch, bad):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    assert_owner_error(lambda: resume(env, bad), "INVALID_ARGUMENT")
    assert calls == [] and tree(env) == before and lifecycle.current_guard() is None


@pytest.mark.parametrize("bad", ["A" * 64, "short", 1], ids=["uppercase", "short", "integer"])
def test_resume_invalid_optional_second_sha_is_refused_before_new_ex(owner_env, monkeypatch, bad):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    assert_owner_error(lambda: resume(env, "a" * 64, expected_observation_sha256=bad), "INVALID_ARGUMENT")
    assert calls == [] and tree(env) == before and lifecycle.current_guard() is None


@pytest.mark.parametrize("bad", [None, SimpleNamespace()], ids=["none", "duck-profile"])
def test_invalid_profile_is_not_accepted_by_duck_typing_before_ex(owner_env, monkeypatch, bad):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    assert_owner_error(lambda: owner.reserve_origin(bad, **env["args"]), "INVALID_ARGUMENT")
    assert calls == [] and tree(env) == before


@pytest.mark.parametrize("bad", [False, 1], ids=["false", "integer"])
def test_noncallable_cancellation_is_refused_before_ex(owner_env, monkeypatch, bad):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    assert_owner_error(lambda: reserve(env, check_cancelled=bad), "INVALID_ARGUMENT")
    assert calls == [] and tree(env) == before


def test_false_valued_user_callback_runs_once_across_outer_inner_and_close(owner_env):
    env = owner_env
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            assert lifecycle.current_guard() is None
            self.calls += 1
    callback = Callback()
    reserve(env, check_cancelled=callback)
    assert callback.calls == 1 and callback.bool_calls == 0
    assert_released(env)


@pytest.mark.parametrize("failure", [RuntimeError("original callback"), KeyboardInterrupt("original callback control")],
                         ids=["ordinary", "control"])
def test_callback_original_object_is_preserved_without_acquisition(owner_env, monkeypatch, failure):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    def refused():
        raise failure
    with pytest.raises(type(failure)) as raised:
        reserve(env, check_cancelled=refused)
    assert raised.value is failure and calls == [] and tree(env) == before
    assert lifecycle.current_guard() is None


def test_guard_acquired_by_last_user_callback_is_refused_and_remains_callers_owned(owner_env, monkeypatch):
    env = owner_env
    before = tree(env)
    calls = acquisition_spy(monkeypatch)
    context, guards = lifecycle.shared(env["profile"]), []
    def entered_by_callback():
        guards.append(context.__enter__())
    try:
        assert_owner_error(lambda: reserve(env, check_cancelled=entered_by_callback), "GUARD_ALREADY_HELD")
        assert len(guards) == 1 and lifecycle.current_guard() is guards[0]
        assert guards[0].verify() is guards[0]
    finally:
        if guards:
            context.__exit__(None, None, None)
    assert calls == [] and tree(env) == before
    assert_released(env)


def test_outer_thirty_second_equal_boundary_includes_actual_ex_acquisition(owner_env, monkeypatch):
    env = owner_env
    clock, events = [100.0], []
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    @contextmanager
    def elapsed_after_actual_enter(*args, **kwargs):
        try:
            with lifecycle.exclusive(*args, **kwargs) as guard:
                events.append("entered")
                clock[0] = 130.0
                yield guard
        finally:
            assert lifecycle.current_guard() is None
            events.append("closed")
    substitute_ex(monkeypatch, elapsed_after_actual_enter)
    before = tree(env)
    assert_owner_error(lambda: reserve(env), "DEADLINE")
    assert events == ["entered", "closed"] and tree(env) == before
    assert_released(env)


def test_outer_thirty_second_equal_boundary_includes_actual_ex_close(owner_env, monkeypatch):
    env = owner_env
    clock, events = [100.0], []
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    @contextmanager
    def elapsed_after_actual_close(*args, **kwargs):
        with lifecycle.exclusive(*args, **kwargs) as guard:
            yield guard
        events.append("closed")
        clock[0] = 130.0
    substitute_ex(monkeypatch, elapsed_after_actual_close)
    delivered = []
    with pytest.raises(owner.OriginReservationOwnerError) as raised:
        delivered.append(reserve(env))
    assert raised.value.code == "ORIGIN_RESERVATION_OWNER_DEADLINE"
    assert events == ["closed"] and delivered == []
    contract.cross_validate(*anchors(env))
    assert_released(env)


def test_last_user_callback_at_equal_deadline_cannot_start_ex(owner_env, monkeypatch):
    env = owner_env
    clock = [100.0]
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = acquisition_spy(monkeypatch)
    before = tree(env)
    def elapsed():
        clock[0] = 130.0
    assert_owner_error(lambda: reserve(env, check_cancelled=elapsed), "DEADLINE")
    assert calls == [] and tree(env) == before


@pytest.mark.parametrize("failure", [OSError("explicit actual EX normal-close fault"), SystemExit(819)],
                         ids=["ordinary", "control"])
def test_normal_ex_close_fault_after_real_inner_result_discards_dto_and_keeps_anchors(owner_env, monkeypatch, failure):
    env = owner_env
    closed, delivered = [], []
    substitute_ex(monkeypatch, close_then_fault(failure, closed))
    with pytest.raises(type(failure)) as raised:
        delivered.append(reserve(env))
    assert raised.value is failure and closed == [True] and delivered == []
    contract.cross_validate(*anchors(env))
    assert_released(env)


@pytest.mark.parametrize("failure", [RuntimeError("original inner ordinary"), SystemExit(819),
                                      KeyboardInterrupt("original inner control")],
                         ids=["ordinary", "systemexit", "keyboardinterrupt"])
def test_original_inner_exception_wins_over_fault_after_actual_ex_cleanup(owner_env, monkeypatch, failure):
    env = owner_env
    closed, actual_results, delivered = [], [], []
    def actual_inner_then_fault(*args, **kwargs):
        actual_results.append(locked.reserve_origin_locked(*args, **kwargs))
        raise failure
    substitute_locked(monkeypatch, reserve_call=actual_inner_then_fault)
    substitute_ex(monkeypatch, close_then_fault(RuntimeError("explicit cleanup fault"), closed))
    with pytest.raises(type(failure)) as raised:
        delivered.append(reserve(env))
    assert raised.value is failure and len(actual_results) == 1 and closed == [True] and delivered == []
    assert "ORIGIN_RESERVATION_OWNER_EX_CLEANUP_FAILED" in getattr(failure, "__notes__", ())
    contract.cross_validate(*anchors(env))
    assert_released(env)


def test_real_inner_result_replaced_with_duck_dto_is_rejected_after_actual_close(owner_env, monkeypatch):
    env = owner_env
    actual_results = []
    def duck_result(*args, **kwargs):
        actual = locked.reserve_origin_locked(*args, **kwargs)
        actual_results.append(actual)
        # Explicit failure substitute only; this is never seeded as a successful
        # producer/owner result, even though the original disk I/O was real.
        return SimpleNamespace(init_operation_id=actual.init_operation_id,
                               reservation_sha256=actual.reservation_sha256,
                               observation_sha256=actual.observation_sha256)
    substitute_locked(monkeypatch, reserve_call=duck_result)
    assert_owner_error(lambda: reserve(env), "RESULT_INVALID")
    assert len(actual_results) == 1
    contract.cross_validate(*anchors(env))
    assert_released(env)
