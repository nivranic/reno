"""B05A source accounting uses isolated storage and no external transports."""
from datetime import timedelta
import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from reno import budget, budget_contract as contract, budget_schema, db, migrations
from tests.migration_helpers import migration_profile
from tests.test_budget_ledger import NOW, INPUT, REQUEST, profile, policy, operation, query, dispatch

SOURCE_KINDS = ("source_metadata", "source_enumeration", "source_resolution")


def source(kind, *, cap=8):
    return operation(kind=kind, key="source:0"), policy(model_daily_attempts=0,
        download_daily_videos=0, model_operation_max_attempts=cap)


def request(ordinal=1, *, segment=None, digest=REQUEST):
    return budget.AttemptSpec(digest, ordinal=ordinal, download_segment=segment)


def reserve(profile, spec, attempt, settings, now=NOW):
    return budget.reserve(spec, attempt, profile=profile, policy=settings, now=now)


def finish(profile, attempt, *, digest=REQUEST, outcome="full_result", text='{"complete":true}', now=NOW):
    return budget.settle(attempt.attempt_id, digest, profile=profile,
                         outcome=outcome, result_text=text, now=now)


@pytest.mark.parametrize("kind", SOURCE_KINDS)
def test_probes_are_durable_attempts_without_model_or_video_admission(profile, kind):
    spec, settings = source(kind)
    first = reserve(profile, spec, request(), settings)
    assert dispatch(profile, first, settings).won
    finish(profile, first)
    value = budget.summarize(profile=profile, policy=settings, now=NOW)
    assert value[kind + "_attempts"] == 1
    assert value["model_attempt_admissions"] == value["download_logical_admissions"] == 0
    assert value["download_process_attempts"] == value["unknown_usage_attempts"] == 0
    assert value["reserved_output_tokens"] == 0
    assert budget.get_result(first.attempt_id, REQUEST, profile=profile).text == '{"complete":true}'
    assert json.loads(query(profile, "SELECT wait_details_json FROM budget_operation")[0]["wait_details_json"]) == {}


@pytest.mark.parametrize("kind", ("source_metadata", "source_resolution"))
def test_completed_source_step_allows_bounded_refresh_in_same_operation(profile, kind, monkeypatch):
    if kind == "source_resolution":
        monkeypatch.setattr(budget.config, "source_resolution_attempt_limit", lambda: 2)
    spec, settings = source(kind, cap=2)
    first = reserve(profile, spec, request(), settings)
    dispatch(profile, first, settings)
    finish(profile, first, outcome="source_step_complete")
    refresh_sha = hashlib.sha256(b"fresh signed response request").hexdigest()
    second = reserve(profile, spec, request(2, digest=refresh_sha), settings)
    assert second.operation_id == first.operation_id
    dispatch(profile, second, settings)
    finish(profile, second, digest=refresh_sha, outcome="source_step_complete")
    with pytest.raises(contract.BudgetDeferred, match="attempt_limit"):
        reserve(profile, spec, request(3), settings)
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 2
    assert budget.get_result(first.attempt_id, REQUEST, profile=profile).text == '{"complete":true}'


def test_enumeration_full_result_can_only_be_reused_without_new_dispatch(profile):
    spec, settings = source("source_enumeration")
    first = reserve(profile, spec, request(), settings)
    dispatch(profile, first, settings)
    finish(profile, first, text='{"items":["synthetic-video"]}')
    replay = reserve(profile, spec, request(), settings)
    assert replay.attempt_id == first.attempt_id and not dispatch(profile, replay, settings).won
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, spec, request(2), settings)
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


@pytest.mark.parametrize("kind", ("model", "source_enumeration"))
def test_source_step_complete_is_not_a_model_repair_or_enumeration_success(profile, kind):
    spec = operation(kind=kind)
    attempt = budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100) if kind == "model" else request()
    settings = policy()
    first = reserve(profile, spec, attempt, settings)
    dispatch(profile, first, settings)
    with pytest.raises(budget.BudgetLedgerError):
        finish(profile, first, outcome="source_step_complete")
    assert query(profile, "SELECT state FROM budget_attempt")[0]["state"] == "dispatching"


@pytest.mark.parametrize("text", (None, ""))
def test_source_step_complete_requires_complete_nonempty_private_result(profile, text):
    spec, settings = source("source_metadata")
    first = reserve(profile, spec, request(), settings)
    dispatch(profile, first, settings)
    with pytest.raises(budget.BudgetLedgerError):
        finish(profile, first, outcome="source_step_complete", text=text)
    with pytest.raises(budget.BudgetLedgerError):
        budget.settle(first.attempt_id, REQUEST, profile=profile, response_complete=False,
                      outcome="source_step_complete")


def test_corrupted_completed_probe_body_cannot_authorize_refresh(profile):
    spec, settings = source("source_metadata")
    first = reserve(profile, spec, request(), settings)
    dispatch(profile, first, settings)
    finish(profile, first, outcome="source_step_complete", text="private signed metadata")
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE budget_attempt SET result_text=? WHERE attempt_id=?", ("private signed metadatX", first.attempt_id))
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, spec, request(2), settings)
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_video_and_audio_are_two_process_attempts_one_logical_video_with_existing_pacing(profile):
    spec = operation(kind="download", key="media:0")
    settings = policy(download_daily_videos=1, download_interval_min_seconds=10,
                      download_interval_max_seconds=10)
    video = reserve(profile, spec, request(segment="video"), settings)
    dispatch(profile, video, settings)
    finish(profile, video, outcome="source_step_complete", text='{"segment":"video"}')
    audio_sha = hashlib.sha256(b"audio media request").hexdigest()
    with pytest.raises(contract.BudgetDeferred, match="download_pacing"):
        reserve(profile, spec, request(2, segment="audio", digest=audio_sha), settings, NOW + timedelta(seconds=5))
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1
    audio = reserve(profile, spec, request(2, segment="audio", digest=audio_sha), settings, NOW + timedelta(seconds=10))
    assert audio.operation_id == video.operation_id
    dispatch(profile, audio, settings, NOW + timedelta(seconds=10))
    finish(profile, audio, digest=audio_sha, text='{"segment":"audio"}', now=NOW + timedelta(seconds=10))
    value = budget.summarize(profile=profile, policy=settings, now=NOW + timedelta(seconds=10))
    assert value["download_process_attempts"] == 2 and value["download_logical_admissions"] == 1
    view = budget.get_operation(spec, profile=profile)
    assert [item.download_segment for item in view.attempts] == ["video", "audio"]
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        reserve(profile, operation(kind="download", key="other"), request(segment="video"), settings,
                NOW + timedelta(seconds=20))


def test_audio_on_next_day_does_not_move_or_duplicate_first_video_window(profile):
    spec = operation(kind="download")
    settings = policy(budget_timezone="UTC", download_daily_videos=1)
    video = reserve(profile, spec, request(segment="video"), settings)
    dispatch(profile, video, settings)
    finish(profile, video, outcome="source_step_complete")
    tomorrow = NOW + timedelta(days=1)
    audio = reserve(profile, spec, request(2, segment="audio"), settings, tomorrow)
    dispatch(profile, audio, settings, tomorrow)
    finish(profile, audio, now=tomorrow)
    assert budget.summarize(profile=profile, policy=settings, now=NOW)["download_logical_admissions"] == 1
    value = budget.summarize(profile=profile, policy=settings, now=tomorrow)
    assert value["download_logical_admissions"] == 0 and value["download_process_attempts"] == 1
    reserve(profile, operation(kind="download", key="tomorrow"), request(segment="video"), settings, tomorrow)
    assert budget.summarize(profile=profile, policy=settings, now=tomorrow)["download_logical_admissions"] == 1


@pytest.mark.parametrize("kind,segment,adapter,output", [
    ("download", None, None, None), ("source_metadata", "video", None, None),
    ("source_enumeration", None, "paas_v4", 100), ("source_resolution", None, "paas_v4", 100),
    ("model", "audio", "paas_v4", 100), ("download", "video", "paas_v4", 100),
])
def test_new_requests_cannot_use_wrong_meter_or_omit_download_segment(profile, kind, segment, adapter, output):
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, operation(kind=kind), budget.AttemptSpec(REQUEST, adapter=adapter,
            effective_max_tokens=output, download_segment=segment), policy())
    assert query(profile, "SELECT * FROM budget_attempt") == []


@pytest.mark.parametrize("segment", (True, 1, "metadata", "", "video "))
def test_download_segment_is_a_closed_typed_fact(segment):
    with pytest.raises(budget.BudgetLedgerError):
        request(segment=segment)


@pytest.mark.parametrize("damage", ("kind", "segment", "units"))
def test_v2_snapshot_cannot_reclassify_media_as_probe(profile, damage):
    first = reserve(profile, operation(kind="download"), request(segment="video"), policy())
    with sqlite3.connect(profile.database) as con:
        raw = con.execute("SELECT current_reservation_json FROM budget_attempt").fetchone()[0]
        value = json.loads(raw)
        assert value["format_version"] == 2
        if damage == "kind": value["kind"] = "source_metadata"
        elif damage == "segment": value["download_segment"] = "audio"
        else: value["units"]["download_videos"] = 0
        raw = budget._json(value)
        con.execute("UPDATE budget_attempt SET current_reservation_json=?,current_reservation_sha256=?",
                    (raw, hashlib.sha256(raw.encode()).hexdigest()))
    with pytest.raises(budget.BudgetLedgerError):
        budget.summarize(profile=profile, policy=policy(), now=NOW)


def legacy_v11_database(path):
    """Create genuine frozen v11 DDL, never relabel a v12 database."""
    from tests.test_budget_migrations import v10_database
    v10_database(path)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        for table, declaration in budget_schema.LEGACY_TABLES.items():
            con.execute(f"CREATE TABLE {table} ({declaration})")
        columns = {row[1] for row in con.execute("PRAGMA table_info(job)")}
        for name, declaration in budget_schema.JOB_COLUMNS.items():
            if name not in columns: con.execute(f"ALTER TABLE job ADD COLUMN {name} {declaration}")
        for statement in budget_schema.INDEXES: con.execute(statement)
        budget_schema.seed_legacy_job_lineage(con)
        con.execute("UPDATE meta SET value='11' WHERE key='migration_version'")
        budget_schema.validate(con, version=11)


def seed_legacy_attempts(path):
    legacy_v11_database(path)
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        root = con.execute("SELECT job_id,continuation_id,execution_generation FROM job ORDER BY job_id LIMIT 1").fetchone()
        library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        stamp, settings = budget._stamp(NOW), policy()
        for index, (kind, state) in enumerate((("model", "settled"), ("model", "reserved"),
                ("download", "reserved"), ("download", "indeterminate"), ("download", "released"))):
            spec = budget.OperationSpec(root["continuation_id"], root["execution_generation"], kind,
                                        f"legacy:{index}", INPUT, root["job_id"])
            _, identity_sha, op_id = budget._identity(spec, library)
            details = '{"download_interval_seconds":0}' if kind == "download" else '{}'
            con.execute("""INSERT INTO budget_operation(operation_id,library_id,identity_sha256,continuation_id,
                execution_generation,kind,suboperation_key,input_sha256,root_job_id,wait_details_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (op_id, library, identity_sha, spec.continuation_id,
                spec.execution_generation, kind, spec.suboperation_key, INPUT, root["job_id"], details, stamp, stamp))
            adapter = "paas_v4" if kind == "model" else None
            snapshot = {"format_version": 1, "policy": settings.public(),
                "window": contract.period_for(settings, NOW).public(), "units": {
                    "model_attempts": int(kind == "model"), "reserved_output_tokens": 100 if adapter else 0,
                    "download_videos": int(kind == "download")}, "result_bound": budget.MAX_RESULT_BYTES,
                "adapter": adapter, "pacing_slot": None, "admitted_at": stamp}
            raw, sha, attempt_id = budget._json(snapshot), budget._digest(snapshot), str(uuid.uuid4())
            values = {"attempt_id": attempt_id, "operation_id": op_id, "ordinal": 1,
                "request_sha256": REQUEST, "state": state, "initial_reservation_json": raw,
                "initial_reservation_sha256": sha, "current_reservation_json": raw,
                "current_reservation_sha256": sha, "current_policy_sha256": contract.policy_sha256(settings),
                "current_period_key": snapshot["window"]["period_key"],
                "current_window_start_utc": snapshot["window"]["start_utc"],
                "current_window_end_utc": snapshot["window"]["end_utc"], "last_admitted_at": stamp,
                "created_at": stamp, "updated_at": stamp, "reservation_held": int(state != "released")}
            if state in {"settled", "indeterminate"}:
                token = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
                first = {"format_version": 1, "reservation": snapshot, "attempt_id": attempt_id,
                    "ordinal": 1, "request_sha256": REQUEST, "dispatch_token_sha256": token, "dispatched_at": stamp}
                values.update(first_dispatch_json=budget._json(first), first_dispatch_sha256=budget._digest(first),
                              dispatch_token_sha256=token, dispatched_at=stamp)
            if state == "settled":
                text, provider = "paid synthetic response", "private synthetic request id"
                body_sha = hashlib.sha256(text.encode()).hexdigest()
                usage = contract.Usage(adapter, "missing")
                receipt = budget._settlement_receipt(REQUEST, "full_result", usage, body_sha, len(text.encode()), provider)
                values.update(response_complete=1, outcome="full_result", observed_usage_json=budget._json(usage.public()),
                    provider_request_id=provider, result_text=text, result_sha256=body_sha, result_bytes=len(text.encode()),
                    result_storage_state="stored", settlement_sha256=budget._digest(receipt), settled_at=stamp)
            if state == "indeterminate": values["indeterminate_at"] = stamp
            names = tuple(values)
            con.execute(f"INSERT INTO budget_attempt({','.join(names)}) VALUES ({','.join('?' for _ in names)})", tuple(values.values()))
            if index == 0: con.execute("UPDATE job SET budget_operation_id=? WHERE job_id=?", (op_id, root["job_id"]))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def legacy_rows(path):
    with sqlite3.connect(path) as con:
        return {table: (tuple(row[1] for row in con.execute(f"PRAGMA table_info({table})")),
            con.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()) for table, key in
            (("budget_operation", "operation_id"), ("budget_attempt", "attempt_id"), ("job", "job_id"))}


def assert_legacy_rows(path, expected):
    with sqlite3.connect(path) as con:
        for table, (columns, rows) in expected.items():
            key = {"budget_operation": "operation_id", "budget_attempt": "attempt_id", "job": "job_id"}[table]
            assert con.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY {key}").fetchall() == rows
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v11_upgrade_preserves_every_old_budget_and_job_fact_with_protected_backup(tmp_path):
    path = tmp_path / "v11.db"
    seed_legacy_attempts(path)
    before = legacy_rows(path)
    value = db.migrate(path, profile=migration_profile(path))
    assert value.version == migrations.SCHEMA_VERSION
    assert (json.loads(value.manifest_path.read_text())["from_version"],
            json.loads(value.manifest_path.read_text())["to_version"]) == (11, migrations.SCHEMA_VERSION)
    assert_legacy_rows(path, before)
    assert_legacy_rows(value.backup_path, before)
    with sqlite3.connect(value.backup_path) as backup:
        assert "download_segment" not in {row[1] for row in backup.execute("PRAGMA table_info(budget_attempt)")}
    with db.connect(path) as con:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM budget_attempt WHERE download_segment IS NOT NULL").fetchone()[0] == 0
        assert {row[2] for row in con.execute("PRAGMA foreign_key_list(job)")} >= {"budget_operation"}
        assert not any(row[0].startswith("_budget_") for row in con.execute("SELECT name FROM sqlite_master"))


def test_ordinary_connect_and_initialize_refuse_genuine_v11_without_changes(tmp_path):
    path = tmp_path / "v11.db"
    seed_legacy_attempts(path)
    before = path.read_bytes()
    for entry in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            entry(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ("rebuild", "fk"))
def test_v11_rebuild_failure_rolls_back_ddl_all_facts_and_version(tmp_path, monkeypatch, failure):
    path = tmp_path / "v11.db"
    seed_legacy_attempts(path)
    before = legacy_rows(path)
    original = budget_schema.upgrade_v11
    def fail(con):
        original(con)
        if failure == "fk":
            con.execute("UPDATE job SET budget_operation_id=?", (str(uuid.uuid4()),))
        else: raise sqlite3.DatabaseError("synthetic interrupted source upgrade")
    monkeypatch.setattr(budget_schema, "upgrade_v11", fail)
    with pytest.raises((db.SchemaVersionError, sqlite3.DatabaseError)):
        db.migrate(path, profile=migration_profile(path))
    assert_legacy_rows(path, before)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "11"
        budget_schema.validate(con, version=11)


def test_old_v1_results_and_exact_download_replay_remain_usable_after_upgrade(tmp_path):
    path = tmp_path / "v11.db"
    seed_legacy_attempts(path)
    target = migration_profile(path)
    db.migrate(path, profile=target)
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        paid = con.execute("SELECT attempt_id FROM budget_attempt WHERE state='settled'").fetchone()[0]
        old = con.execute("SELECT * FROM budget_operation WHERE kind='download' AND suboperation_key='legacy:2'").fetchone()
        spec = budget.OperationSpec(old["continuation_id"], old["execution_generation"], old["kind"],
                                    old["suboperation_key"], old["input_sha256"], old["root_job_id"])
    assert budget.get_result(paid, REQUEST, profile=target).text == "paid synthetic response"
    replay = reserve(target, spec, budget.AttemptSpec(REQUEST), policy())
    assert budget.get_operation(spec, profile=target).attempts[0].download_segment is None
    assert dispatch(target, replay, policy()).won
    assert not dispatch(target, replay, policy()).won
    with sqlite3.connect(path) as con:
        raw = con.execute("SELECT initial_reservation_json FROM budget_attempt WHERE attempt_id=?", (replay.attempt_id,)).fetchone()[0]
        assert json.loads(raw)["format_version"] == 1 and "download_segment" not in json.loads(raw)


def running_model(profile):
    continuation = str(uuid.uuid4())
    with sqlite3.connect(profile.database) as con:
        job = con.execute("""INSERT INTO job(kind,status,owner_id,continuation_id,execution_generation)
            VALUES ('process','running','synthetic-owner',?,1)""", (continuation,)).lastrowid
    spec = operation(continuation=continuation, root_job_id=job)
    first = reserve(profile, spec, budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100), policy())
    return job, first


def test_dispatch_guard_uses_writer_snapshot_after_waiting_pause_commit(profile, monkeypatch):
    from reno import budget_writer_sqlite

    job, first = running_model(profile)
    entered, observations = threading.Event(), []
    original = budget_writer_sqlite.open_writer
    def connect(value):
        con = original(value)
        con.set_trace_callback(lambda sql: entered.set() if sql == "BEGIN IMMEDIATE" else None)
        return con
    monkeypatch.setattr(budget_writer_sqlite, "open_writer", connect)
    def guard(con):
        assert con.in_transaction
        row = con.execute("SELECT manual_paused FROM job WHERE job_id=?", (job,)).fetchone()
        if row[0]: raise contract.BudgetDeferred("manual_pause")
    def mark():
        try:
            observations.append(budget.mark_dispatching(first.attempt_id, first.reservation_sha256,
                str(uuid.uuid4()), expected_revision=first.revision, profile=profile,
                policy=policy(), now=NOW, dispatch_guard=guard))
        except BaseException as exc: observations.append(exc)
    writer = sqlite3.connect(profile.database)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE job SET manual_paused=1 WHERE job_id=?", (job,))
    worker = threading.Thread(target=mark)
    try:
        worker.start()
        assert entered.wait(5), "mark must have reached the occupied SQL writer"
        writer.commit()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        writer.rollback()
        writer.close()
        worker.join(5)
    assert len(observations) == 1 and isinstance(observations[0], contract.BudgetDeferred)
    assert observations[0].reason == "manual_pause"
    row = query(profile, "SELECT state,first_dispatch_json,dispatch_token_sha256 FROM budget_attempt")[0]
    assert row == {"state": "reserved", "first_dispatch_json": None, "dispatch_token_sha256": None}


def test_guard_exception_rolls_back_reservation_change_and_does_not_give_dispatch_right(profile):
    _, first = running_model(profile)
    before = query(profile, "SELECT * FROM budget_attempt")
    marker = RuntimeError("synthetic guard rejected")
    def guard(con): raise marker
    with pytest.raises(RuntimeError) as caught:
        budget.mark_dispatching(first.attempt_id, first.reservation_sha256, str(uuid.uuid4()),
            expected_revision=first.revision, profile=profile, policy=policy(),
            now=NOW + timedelta(days=1), dispatch_guard=guard)
    assert caught.value is marker
    assert query(profile, "SELECT * FROM budget_attempt") == before


def test_paid_or_quota_rejected_dispatch_never_runs_guard(profile):
    _, first = running_model(profile)
    seen = []
    def guard(con): seen.append(con)
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        budget.mark_dispatching(first.attempt_id, first.reservation_sha256, str(uuid.uuid4()),
            expected_revision=first.revision, profile=profile, policy=policy(model_daily_attempts=0),
            now=NOW, dispatch_guard=guard)
    assert seen == []
    current = budget.get_attempt(first.operation_id, 1, profile=profile)
    with sqlite3.connect(profile.database) as con:
        row = con.execute("SELECT current_reservation_sha256,reservation_revision FROM budget_attempt").fetchone()
    active = budget.Reservation(first.operation_id, current.attempt_id, 1, "reserved", row[0], row[1], False)
    dispatch(profile, active)
    finish(profile, active)
    before = profile.database.read_bytes()
    result = budget.mark_dispatching(first.attempt_id, first.reservation_sha256, str(uuid.uuid4()),
        expected_revision=first.revision, profile=profile, policy=policy(), now=NOW, dispatch_guard=guard)
    assert not result.won and seen == [] and profile.database.read_bytes() == before


def test_reservation_guard_refuses_missing_witness_before_any_operation_or_attempt(profile):
    seen = []
    def guard(con):
        assert con.in_transaction
        seen.append(con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0])
        raise contract.BudgetDeferred("ledger_unavailable")
    with pytest.raises(contract.BudgetDeferred, match="ledger_unavailable"):
        budget.reserve(operation(), budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100),
            profile=profile, policy=policy(), now=NOW, reservation_guard=guard)
    assert seen == [0]
    assert query(profile, "SELECT * FROM budget_operation") == query(profile, "SELECT * FROM budget_attempt") == []


def test_reservation_guard_never_commits_callers_active_business_transaction(profile):
    seen = []
    business = sqlite3.connect(profile.database)
    try:
        business.execute("BEGIN IMMEDIATE")
        business.execute("INSERT INTO job(kind,status) VALUES ('process','pending')")
        with pytest.raises(budget.BudgetLedgerError) as caught:
            budget.reserve(operation(), budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100),
                profile=profile, policy=policy(), now=NOW, business_connection=business,
                reservation_guard=lambda con: seen.append(con))
        assert caught.value.code == "BUDGET_BUSINESS_TRANSACTION_ACTIVE"
        assert business.in_transaction and seen == []
        assert query(profile, "SELECT * FROM job") == []
    finally:
        business.rollback()
        business.close()


@pytest.mark.parametrize("guard", (False, 1))
def test_reservation_guard_is_optional_callable_not_a_meter_flag(profile, guard):
    with pytest.raises(budget.BudgetLedgerError):
        budget.reserve(operation(), budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100),
            profile=profile, policy=policy(), now=NOW, reservation_guard=guard)
    assert query(profile, "SELECT * FROM budget_operation") == []


@pytest.mark.parametrize("state", ("settled", "indeterminate", "released"))
def test_exact_nonreserved_replay_does_not_run_reservation_guard_or_mutate(profile, state):
    _, first = running_model(profile)
    if state in {"settled", "indeterminate"}:
        dispatch(profile, first)
        if state == "settled": finish(profile, first)
        else: budget.settle(first.attempt_id, REQUEST, profile=profile, response_complete=False, now=NOW)
    else:
        budget.release_reserved(first.attempt_id, first.reservation_sha256,
                                expected_revision=first.revision, profile=profile, now=NOW)
    row = query(profile, "SELECT * FROM budget_operation")[0]
    spec = budget.OperationSpec(row["continuation_id"], row["execution_generation"], row["kind"],
                                row["suboperation_key"], row["input_sha256"], row["root_job_id"])
    before = {table: query(profile, "SELECT * FROM " + table) for table in ("budget_operation", "budget_attempt")}
    def guard(con): pytest.fail("exact closed/unknown replay grants no new external right")
    replay = budget.reserve(spec, budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100),
        profile=profile, policy=policy(model_daily_attempts=0), now=NOW, reservation_guard=guard)
    assert replay.attempt_id == first.attempt_id and replay.state == state
    assert {table: query(profile, "SELECT * FROM " + table) for table in before} == before


def test_existing_reserved_readmission_still_checks_guard_before_mutation(profile):
    _, first = running_model(profile)
    row = query(profile, "SELECT * FROM budget_operation")[0]
    spec = budget.OperationSpec(row["continuation_id"], row["execution_generation"], row["kind"],
                                row["suboperation_key"], row["input_sha256"], row["root_job_id"])
    before = query(profile, "SELECT * FROM budget_attempt")
    def guard(con): raise contract.BudgetDeferred("manual_pause")
    with pytest.raises(contract.BudgetDeferred, match="manual_pause"):
        budget.reserve(spec, budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100),
            profile=profile, policy=policy(), now=NOW + timedelta(days=1), reservation_guard=guard)
    assert query(profile, "SELECT * FROM budget_attempt") == before


@pytest.mark.parametrize("failure", (False, True))
def test_private_migration_connection_restores_fk_after_commit_or_rollback(tmp_path, monkeypatch, failure):
    path = tmp_path / "observed-v11.db"
    seed_legacy_attempts(path)
    events, closed = [], []
    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql in {"PRAGMA foreign_keys=OFF", "PRAGMA foreign_keys=ON", "BEGIN IMMEDIATE"}:
                events.append((sql, self.in_transaction))
            return super().execute(sql, *args, **kwargs)
        def close(self):
            closed.append((self.in_transaction, super().execute("PRAGMA foreign_keys").fetchone()[0]))
            return super().close()
    original_connect = sqlite3.connect
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    def connect(database, *args, **kwargs):
        if database == uri: kwargs["factory"] = ObservedConnection
        return original_connect(database, *args, **kwargs)
    monkeypatch.setattr(migrations.sqlite3, "connect", connect)
    if failure:
        original_upgrade = budget_schema.upgrade_v11
        def fail(con):
            original_upgrade(con)
            raise sqlite3.DatabaseError("synthetic observed rollback")
        monkeypatch.setattr(budget_schema, "upgrade_v11", fail)
        with pytest.raises(sqlite3.DatabaseError, match="observed rollback"):
            db.migrate(path, profile=migration_profile(path))
    else:
        assert db.migrate(path, profile=migration_profile(path)).version == migrations.SCHEMA_VERSION
    assert ("PRAGMA foreign_keys=OFF", False) in events
    assert events[-1] == ("PRAGMA foreign_keys=ON", False)
    assert closed == [(False, 1)]


@pytest.mark.parametrize("damage", ("index", "trigger"))
def test_v11_unknown_structure_is_rejected_before_rebuild(tmp_path, damage):
    path = tmp_path / "invalid-v11.db"
    seed_legacy_attempts(path)
    with sqlite3.connect(path) as con:
        if damage == "index": con.execute("DROP INDEX idx_budget_operation_identity")
        else: con.execute("CREATE TRIGGER unknown_budget_hook AFTER INSERT ON budget_operation BEGIN SELECT 1; END")
    before = legacy_rows(path)
    with pytest.raises((db.SchemaVersionError, sqlite3.DatabaseError)):
        db.migrate(path, profile=migration_profile(path))
    assert_legacy_rows(path, before)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "11"
        assert "download_segment" not in {row[1] for row in con.execute("PRAGMA table_info(budget_attempt)")}
