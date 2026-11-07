"""Actual isolated two-anchor reservation; no writer grant or seeded source DTO.

Production current17 initialization, a real personal receipt, actual lifecycle
EX, r815 committed-image capture, creator writes and readbacks supply success.
Consumer-local wrappers inject explicit faults only after their actual owned
operation where specified. Native failure/ACL policy is not inferred from them.
"""
import base64
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_legacy_fence as fence
from reno import personal_authority_origin_reservation as reservation
from reno import personal_authority_origin_reservation_contract as contract
from reno import personal_authority_origin_reservation_io as publication
from reno import personal_authority_origin_source as origin
from reno import personal_mirror_contract as facts
from reno import personal_preservation_contract as preservation
from reno import personal_preservation_files as files
from reno import storage_lifecycle as lifecycle, sync_media as safe


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def reservation_env(tmp_path, monkeypatch):
    root = tmp_path / "actual-home"
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
    operation = uid()
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": operation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {"title": "绍兴防水核对", "question": "工序与材料型号未定",
                    "content_snapshot": {"kind": "note", "text": "真实隔离的个人记录"}}})
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as con, con:
        assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (operation,)).fetchone() == (1,)
    return {"root": root, "data": data, "database": database, "profile": profile,
            "node": data / "node-identity.json", "meta": meta,
            "args": {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}}


def raw_sources(env):
    paths = {suffix: Path(str(env["database"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["node"]
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def sql_snapshot(env):
    with closing(sqlite3.connect(env["database"])) as con, con:
        result = {"meta": list(con.execute("SELECT key,value FROM meta ORDER BY key"))}
        for name, columns in preservation.PERSONAL_COLUMNS.items():
            query = 'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM "' + name + '"'
            result[name] = list(con.execute(query))
    return result


def tree(env):
    return {str(path.relative_to(env["root"])): "directory" if path.is_dir() else path.read_bytes()
            for path in env["root"].rglob("*")}


def authority_names(env):
    return {path.name for path in env["data"].iterdir()
            if path.name.rstrip(" .").casefold().startswith(fence.PREFIX)}


def anchors(env):
    return tuple((env["data"] / name).read_bytes()
                 for name in (contract.RESERVATION_NAME, contract.OBSERVATION_NAME))


def reserve(env, **options):
    with lifecycle.exclusive(env["profile"]):
        return reservation.reserve_origin_locked(env["profile"], **env["args"], **options)


def resume(env, expected_first, **options):
    with lifecycle.exclusive(env["profile"]):
        return reservation.resume_origin_locked(env["profile"], **env["args"],
            expected_reservation_sha256=expected_first, **options)


def assert_error(call, suffix):
    with pytest.raises(reservation.OriginReservationError) as raised:
        call()
    assert raised.value.code == "ORIGIN_RESERVATION_" + suffix


def substitute_capture(monkeypatch, replacement):
    monkeypatch.setattr(reservation, "origin", SimpleNamespace(
        _node_id=origin._node_id, _file_evidence=origin._file_evidence, capture_locked=replacement))


def fail_capture_before_entry(monkeypatch, seen=None):
    @contextmanager
    def refused(*args, **kwargs):
        if seen is not None:
            seen.append(True)
        raise RuntimeError("explicit source entry fault")
        yield  # pragma: no cover - contextmanager protocol, never a DTO
    substitute_capture(monkeypatch, refused)


def first_only(env, monkeypatch):
    with monkeypatch.context() as patch:
        fail_capture_before_entry(patch)
        assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    raw = (env["data"] / contract.RESERVATION_NAME).read_bytes()
    assert authority_names(env) == {contract.RESERVATION_NAME}
    return raw


def substitute_publication(monkeypatch, replacement):
    monkeypatch.setattr(reservation, "publication", SimpleNamespace(publish_new_locked=replacement))


def close_then_fault(factory, failure, on_close=None):
    @contextmanager
    def replacement(*args, **kwargs):
        context = factory(*args, **kwargs)
        value = context.__enter__()
        try:
            yield value
        except BaseException as primary:
            context.__exit__(type(primary), primary, primary.__traceback__)
            if on_close is not None:
                on_close()
            raise failure
        else:
            context.__exit__(None, None, None)
            if on_close is not None:
                on_close()
            raise failure
    return replacement


def test_first_is_actual_durable_before_real_source_and_exact_bytes_form_second(reservation_env, monkeypatch):
    env = reservation_env
    before_sources, before_sql, before_tree = raw_sources(env), sql_snapshot(env), tree(env)
    captured, publication_returns = [], []
    def returned_publication(profile, directory, name, stage_name, raw, check_cancelled=None):
        # The actual producer FD write/fsync/publication/independent readback,
        # creator close and post-close full readback all precede this event.
        result = publication.publish_new_locked(profile, directory, name, stage_name, raw, check_cancelled)
        publication_returns.append(name)
        return result
    substitute_publication(monkeypatch, returned_publication)
    @contextmanager
    def observed_capture(*args, **kwargs):
        assert publication_returns == [contract.RESERVATION_NAME]
        first_path = env["data"] / contract.RESERVATION_NAME
        assert first_path.is_file()
        first = contract.decode(first_path.read_bytes())
        assert first["kind"] == "origin_reservation"
        assert authority_names(env) == {contract.RESERVATION_NAME}
        assert not ({"sql_document", "meta_document", "origin_intent_base64"} & set(first))
        with origin.capture_locked(*args, **kwargs) as value:
            captured.append((value.intent_bytes, value.evidence_bytes))
            yield value
    substitute_capture(monkeypatch, observed_capture)
    result = reserve(env)
    first_raw, second_raw = anchors(env)
    first, second = contract.decode(first_raw), contract.decode(second_raw)
    assert result.init_operation_id == env["args"]["init_operation_id"]
    assert result.reservation_sha256 == sha(first_raw) and result.observation_sha256 == sha(second_raw)
    assert len(captured) == 1
    assert publication_returns == [contract.RESERVATION_NAME, contract.OBSERVATION_NAME]
    assert base64.b64decode(second["origin_intent_base64"], validate=True) == captured[0][0]
    assert base64.b64decode(second["origin_evidence_base64"], validate=True) == captured[0][1]
    assert second["origin_intent_sha256"] == sha(captured[0][0])
    assert second["origin_evidence_sha256"] == sha(captured[0][1])
    contract.cross_validate(first_raw, second_raw)
    assert first["profile"]["profile_paths"] == list(env["profile"].identity)
    assert first["authority_profile_paths_sha256"] == sha(encoded(list(env["profile"].identity)))
    assert first["physical_profile_sha256"] == sha(encoded(first["profile"]))
    assert first["authority_profile_paths_sha256"] != first["physical_profile_sha256"]
    for name, path in zip(("data", "media", "reports", "checkpoints"),
                          (env["data"], env["profile"].media, env["profile"].reports, env["profile"].checkpoints)):
        info = path.stat(follow_symlinks=False)
        assert first["profile"]["roots"][name] == {
            "path": lifecycle._key(path), "identity": {"device": str(info.st_dev), "inode": str(info.st_ino)}}
    assert first["node"]["node_id"] == env["meta"]["personal_primary_id"]
    assert first["node"]["sha256"] == sha(before_sources["node"])
    assert first["node"]["path_identity"] == files.identity(env["node"].stat(follow_symlinks=False))
    with env["node"].open("rb") as stream:
        assert first["node"]["handle_identity"] == files.identity(os.fstat(stream.fileno()))
    assert raw_sources(env) == before_sources and sql_snapshot(env) == before_sql
    after_tree = tree(env)
    for name in (contract.RESERVATION_NAME, contract.OBSERVATION_NAME):
        after_tree.pop(str((env["data"] / name).relative_to(env["root"])))
    assert after_tree == before_tree
    assert set(result.__slots__) == {"init_operation_id", "reservation_sha256", "observation_sha256"}
    with pytest.raises(FrozenInstanceError):
        result.observation_sha256 = "0" * 64


def test_source_entry_fault_keeps_first_and_r817_blocks_real_legacy_mutation(reservation_env, monkeypatch):
    env = reservation_env
    before = sql_snapshot(env), raw_sources(env)
    first = first_only(env, monkeypatch)
    assert contract.decode(first)["init_operation_id"] == env["args"]["init_operation_id"]
    with lifecycle.exclusive(env["profile"]):
        with pytest.raises(fence.LegacyAuthorityFenceError) as raised:
            fence.assert_legacy_write_allowed_locked(env["profile"])
        assert raised.value.code == fence.REQUIRED
    with pytest.raises(personal.PersonalError) as raised:
        personal.create_card(env["meta"]["default_project_id"], {"operation_id": uid(),
            "primary_epoch": int(env["meta"]["primary_epoch"]), "changes": {"title": "应拒绝"}})
    assert raised.value.code == fence.REQUIRED
    assert (sql_snapshot(env), raw_sources(env)) == before
    assert (env["data"] / contract.RESERVATION_NAME).read_bytes() == first


@pytest.mark.parametrize("guard", ["none", "shared", "wrong_profile"])
def test_actual_ex_of_exact_profile_is_required_before_file_side_effects(reservation_env, guard):
    env = reservation_env
    before = tree(env)
    if guard == "none":
        assert_error(lambda: reservation.reserve_origin_locked(env["profile"], **env["args"]), "STATE_UNAVAILABLE")
    elif guard == "shared":
        with lifecycle.shared(env["profile"]):
            assert_error(lambda: reservation.reserve_origin_locked(env["profile"], **env["args"]), "STATE_UNAVAILABLE")
    else:
        other = lifecycle.StorageProfile(env["database"], env["profile"].media,
                    env["data"] / "other-reports", env["profile"].checkpoints)
        with lifecycle.exclusive(env["profile"]):
            assert_error(lambda: reservation.reserve_origin_locked(other, **env["args"]), "STATE_UNAVAILABLE")
    assert tree(env) == before


@pytest.mark.parametrize("kind", ["file", "directory", "stage"])
def test_unknown_authority_entries_are_never_adopted_deleted_or_repaired(reservation_env, kind):
    env = reservation_env
    name = (contract.stage_name(contract.RESERVATION_NAME, env["args"]["init_operation_id"])
            if kind == "stage" else ".RENO-PERSONAL-AUTHORITY-unknown")
    path = env["data"] / name
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"untrusted existing object")
    before = tree(env)
    assert_error(lambda: reserve(env), "ALREADY_PRESENT")
    assert tree(env) == before


def test_new_init_cannot_replace_first_even_with_identical_uuid_or_body(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    assert_error(lambda: reserve(env), "ALREADY_PRESENT")
    changed = {**env, "args": {**env["args"], "init_operation_id": uid()}}
    assert_error(lambda: reserve(changed), "ALREADY_PRESENT")
    assert (env["data"] / contract.RESERVATION_NAME).read_bytes() == first
    assert authority_names(env) == {contract.RESERVATION_NAME}


def test_resume_requires_exact_first_and_reenters_actual_source(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    seen = []
    @contextmanager
    def counted(*args, **kwargs):
        with origin.capture_locked(*args, **kwargs) as actual:
            seen.append((actual.intent_bytes, actual.evidence_bytes))
            yield actual
    substitute_capture(monkeypatch, counted)
    result = resume(env, sha(first))
    assert len(seen) == 1
    assert anchors(env)[0] == first
    assert result.reservation_sha256 == sha(first)
    second = contract.decode(anchors(env)[1])
    assert base64.b64decode(second["origin_intent_base64"], validate=True) == seen[0][0]
    assert base64.b64decode(second["origin_evidence_base64"], validate=True) == seen[0][1]


@pytest.mark.parametrize("changed", ["sha", "init_operation_id", "domain_id", "facility_id"])
def test_resume_does_not_accept_uuid_body_or_expected_sha_change(reservation_env, monkeypatch, changed):
    env = reservation_env
    first = first_only(env, monkeypatch)
    selected, expected = env, sha(first)
    if changed == "sha":
        expected = "0" * 64
    else:
        selected = {**env, "args": {**env["args"], changed: uid()}}
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: resume(selected, expected), "RESERVATION_MISMATCH")
    assert seen == [] and (env["data"] / contract.RESERVATION_NAME).read_bytes() == first


def test_resume_expected_second_requires_actual_second_and_does_not_publish_on_mismatch(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    assert_error(lambda: resume(env, sha(first), expected_observation_sha256="0" * 64), "OBSERVATION_MISMATCH")
    assert authority_names(env) == {contract.RESERVATION_NAME}


def test_completed_resume_still_freshly_captures_and_keeps_anchors_unchanged(reservation_env, monkeypatch):
    env = reservation_env
    result = reserve(env)
    before = anchors(env)
    assert_error(lambda: resume(env, result.reservation_sha256, expected_observation_sha256="0" * 64),
                 "OBSERVATION_MISMATCH")
    seen = []
    @contextmanager
    def counted(*args, **kwargs):
        with origin.capture_locked(*args, **kwargs) as actual:
            seen.append(True)
            yield actual
    substitute_capture(monkeypatch, counted)
    resumed = resume(env, result.reservation_sha256, expected_observation_sha256=result.observation_sha256)
    assert seen == [True] and resumed == result and anchors(env) == before


def test_resume_refuses_unknown_stage_without_source_or_cleanup(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    stage = env["data"] / contract.stage_name(contract.OBSERVATION_NAME, env["args"]["init_operation_id"])
    stage.write_bytes(b"failed prior publication")
    before = tree(env)
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: resume(env, sha(first)), "UNKNOWN_NAMES")
    assert seen == [] and tree(env) == before


def test_resume_refuses_actual_node_change_instead_of_refreshing_first(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    env["node"].write_bytes(encoded({"instance_id": uid()}))
    before = tree(env)
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: resume(env, sha(first)), "RESERVATION_MISMATCH")
    assert seen == [] and tree(env) == before


def test_resume_refuses_replaced_physical_media_root_identity(reservation_env, monkeypatch):
    env = reservation_env
    first = first_only(env, monkeypatch)
    original_identity = contract.decode(first)["profile"]["roots"]["media"]["identity"]
    previous = env["root"] / "previous-media"
    env["profile"].media.rename(previous)
    env["profile"].media.mkdir()
    lifecycle.initialize(env["profile"])
    info = env["profile"].media.stat(follow_symlinks=False)
    assert {"device": str(info.st_dev), "inode": str(info.st_ino)} != original_identity
    before = tree(env)
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: resume(env, sha(first)), "RESERVATION_MISMATCH")
    assert seen == [] and tree(env) == before and previous.is_dir()


def test_completed_resume_refuses_new_actual_raw_sql_even_when_personal_rows_unchanged(reservation_env):
    env = reservation_env
    result = reserve(env)
    before_anchors, before_personal = anchors(env), sql_snapshot(env)
    # Application/user_version is outside personal summaries, but committed raw
    # SQL evidence belongs to the immutable observation and cannot be refreshed.
    with closing(sqlite3.connect(env["database"])) as con, con:
        con.execute("PRAGMA user_version=818")
    assert sql_snapshot(env) == before_personal
    assert_error(lambda: resume(env, result.reservation_sha256), "OBSERVATION_MISMATCH")
    assert anchors(env) == before_anchors


def test_source_terminal_close_fault_discards_result_after_second_is_durable(reservation_env, monkeypatch):
    env = reservation_env
    closed = []
    substitute_capture(monkeypatch, close_then_fault(origin.capture_locked,
        RuntimeError("explicit after actual source close"), lambda: closed.append(True)))
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    assert closed == [True]
    first, second = anchors(env)
    contract.cross_validate(first, second)
    assert authority_names(env) == {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}


def test_actual_noncooperating_sql_change_after_second_publication_rejects_stale_result(reservation_env, monkeypatch):
    env = reservation_env
    node_before = env["node"].read_bytes()
    @contextmanager
    def changed_after_actual_yield(*args, **kwargs):
        with origin.capture_locked(*args, **kwargs) as actual:
            yield actual
            # Explicitly represents a noncooperating SQL writer; lifecycle
            # consumers must not silently adopt its changed raw source image.
            with closing(sqlite3.connect(env["database"])) as con, con:
                con.execute("PRAGMA user_version=818")
    substitute_capture(monkeypatch, changed_after_actual_yield)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    first, second = anchors(env)
    contract.cross_validate(first, second)
    with closing(sqlite3.connect(env["database"])) as con, con:
        assert con.execute("PRAGMA user_version").fetchone() == (818,)
    assert env["node"].read_bytes() == node_before


@pytest.mark.parametrize("seam", ["_write_all", "_flush", "_publish_owned", "_independent_readback",
                                  "_post_close_readback"])
def test_actual_first_publication_fault_retains_owned_partial_or_durable_file(reservation_env, monkeypatch, seam):
    env = reservation_env
    before = raw_sources(env)
    actual = getattr(publication, seam)
    seen = []
    def operated_then_fault(*args, **kwargs):
        actual(*args, **kwargs)
        seen.append(True)
        raise RuntimeError("explicit after actual " + seam)
    monkeypatch.setattr(publication, seam, operated_then_fault)
    source_seen = []
    fail_capture_before_entry(monkeypatch, source_seen)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    assert seen == [True] and source_seen == [] and raw_sources(env) == before
    name = (contract.stage_name(contract.RESERVATION_NAME, env["args"]["init_operation_id"])
            if seam in {"_write_all", "_flush"} else contract.RESERVATION_NAME)
    assert authority_names(env) == {name}
    assert contract.decode((env["data"] / name).read_bytes())["kind"] == "origin_reservation"
    assert_error(lambda: reserve(env), "ALREADY_PRESENT")


def test_actual_creator_normal_close_fault_retains_first_without_source_entry(reservation_env, monkeypatch):
    env = reservation_env
    closed = []
    monkeypatch.setattr(publication, "_open_stage", close_then_fault(publication._open_stage,
        OSError("explicit after actual creator close"), lambda: closed.append(True)))
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    assert closed == [True] and seen == []
    assert authority_names(env) == {contract.RESERVATION_NAME}
    assert contract.decode((env["data"] / contract.RESERVATION_NAME).read_bytes())["kind"] == "origin_reservation"


@pytest.mark.skipif(os.name != "nt", reason="Windows NT rename status contract")
def test_synthetic_positive_nonzero_nt_status_refuses_and_keeps_actual_stage(reservation_env, monkeypatch):
    env = reservation_env
    calls = []
    def pending(*args):
        # Explicit status substitute: this does not call/observe a real pending
        # NT operation. Actual private creation/write/fsync/readback still run.
        calls.append(True)
        return 0x103
    monkeypatch.setattr(publication, "_ntdll", SimpleNamespace(NtSetInformationFile=pending))
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    name = contract.stage_name(contract.RESERVATION_NAME, env["args"]["init_operation_id"])
    assert calls == [True] and seen == [] and authority_names(env) == {name}
    assert contract.decode((env["data"] / name).read_bytes())["kind"] == "origin_reservation"


def test_second_publication_fault_keeps_first_and_second_stage_without_adoption(reservation_env, monkeypatch):
    env = reservation_env
    actual = publication._flush
    calls = []
    def flushed_then_fault(stream):
        actual(stream)
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("explicit after second actual flush")
    monkeypatch.setattr(publication, "_flush", flushed_then_fault)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    stage_name = contract.stage_name(contract.OBSERVATION_NAME, env["args"]["init_operation_id"])
    assert calls == [True, True] and authority_names(env) == {contract.RESERVATION_NAME, stage_name}
    first = (env["data"] / contract.RESERVATION_NAME).read_bytes()
    contract.cross_validate(first, (env["data"] / stage_name).read_bytes())
    before = tree(env)
    assert_error(lambda: resume(env, sha(first)), "UNKNOWN_NAMES")
    assert tree(env) == before


def test_published_metadata_is_crosschecked_against_actual_file_not_treated_as_lease(reservation_env, monkeypatch):
    env = reservation_env
    actual = publication.publish_new_locked
    def mismatched(*args, **kwargs):
        value = actual(*args, **kwargs)
        return publication.PublishedFile(value.raw_sha256, value.byte_size + 1, value.identity)
    substitute_publication(monkeypatch, mismatched)
    seen = []
    fail_capture_before_entry(monkeypatch, seen)
    assert_error(lambda: reserve(env), "PUBLICATION_MISMATCH")
    assert seen == [] and authority_names(env) == {contract.RESERVATION_NAME}


def test_user_callback_is_called_once_even_if_false_valued_and_no_late_dispatch(reservation_env):
    env = reservation_env
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            self.calls += 1
    callback = Callback()
    reserve(env, check_cancelled=callback)
    assert callback.calls == 1 and callback.bool_calls == 0


@pytest.mark.parametrize("failure", [RuntimeError("original callback"), KeyboardInterrupt("original control")])
def test_original_callback_exception_identity_is_preserved_before_reservation(reservation_env, failure):
    env = reservation_env
    before = tree(env)
    def refused():
        raise failure
    with pytest.raises(type(failure)) as raised:
        reserve(env, check_cancelled=refused)
    assert raised.value is failure and tree(env) == before


def test_thirty_second_equal_boundary_rejects_after_last_user_callback(reservation_env, monkeypatch):
    env = reservation_env
    clock = [100.0]
    monkeypatch.setattr(reservation, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    before = tree(env)
    def last_callback():
        clock[0] = 130.0
    assert_error(lambda: reserve(env, check_cancelled=last_callback), "DEADLINE")
    assert tree(env) == before


def test_original_deadline_includes_actual_source_close_after_second_publication(reservation_env, monkeypatch):
    env = reservation_env
    clock = [100.0]
    monkeypatch.setattr(reservation, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    @contextmanager
    def elapsed_after_actual_close(*args, **kwargs):
        with origin.capture_locked(*args, **kwargs) as actual:
            yield actual
        clock[0] = 130.0
    substitute_capture(monkeypatch, elapsed_after_actual_close)
    assert_error(lambda: reserve(env), "DEADLINE")
    contract.cross_validate(*anchors(env))


def test_actual_owned_directory_normal_close_fault_discards_result(reservation_env, monkeypatch):
    env = reservation_env
    closed = []
    selected_safe = SimpleNamespace(_Directory=close_then_fault(safe._Directory,
        OSError("explicit after actual directory close"), lambda: closed.append(True)),
        _native_path=safe._native_path)
    monkeypatch.setattr(reservation, "safe", selected_safe)
    assert_error(lambda: reserve(env), "STATE_UNAVAILABLE")
    assert len(closed) == 4
    contract.cross_validate(*anchors(env))


def test_actual_source_bytes_support_contract_failures_without_inventing_success_dto(reservation_env):
    env = reservation_env
    reserve(env)
    first_raw, second_raw = anchors(env)
    second = contract.decode(second_raw)
    original = encoded(second)
    for field, replacement in (("reservation_sha256", "0" * 64),
                               ("origin_intent_sha256", "0" * 64),
                               ("origin_evidence_sha256", "0" * 64)):
        changed = {**second, field: replacement}
        with pytest.raises(contract.OriginReservationContractError) as raised:
            contract.cross_validate(first_raw, encoded(changed))
        assert raised.value.code == "ORIGIN_RESERVATION_BINDING_MISMATCH"
    changed = {**second, "origin_evidence_base64": second["origin_evidence_base64"] + "\n"}
    with pytest.raises(contract.OriginReservationContractError) as raised:
        contract.decode(encoded(changed))
    assert raised.value.code == "ORIGIN_RESERVATION_BASE64_INVALID"
    wrong_first = contract.decode(first_raw)
    wrong_first["domain_id"] = uid()
    wrong_raw = contract.encode(wrong_first)
    changed = {**second, "reservation_sha256": sha(wrong_raw)}
    with pytest.raises(contract.OriginReservationContractError) as raised:
        contract.cross_validate(wrong_raw, encoded(changed))
    assert raised.value.code == "ORIGIN_RESERVATION_BINDING_MISMATCH"
    assert anchors(env) == (first_raw, second_raw) and second_raw == original
