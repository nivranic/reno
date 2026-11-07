"""S13 uses genuine frozen v12 images and synthetic source receipts only."""
from datetime import timedelta
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract as contract, budget_schema, db, migrations
from tests.migration_helpers import migration_profile
from tests.test_budget_ledger import NOW, INPUT, REQUEST, profile, policy, operation, query, dispatch
from tests.test_budget_sources import seed_legacy_attempts, legacy_rows, assert_legacy_rows


def source_attempt(*, ordinal=1, segment=None, request=REQUEST, version=1):
    return budget.AttemptSpec(request, ordinal=ordinal, download_segment=segment,
                              source_contract_version=version)


def envelope(kind="download", segment="video", *, request=REQUEST, **changes):
    empty_sha = hashlib.sha256(b"").hexdigest()
    result = {"format_version": 1, "runner_contract": "yt_dlp_v1", "request_sha256": request,
        "process_exited": True, "drain_complete": True, "returncode": 1, "termination": "exited",
        "stdout_bytes": 0, "stdout_sha256": empty_sha, "stderr_bytes": 0, "stderr_sha256": empty_sha,
        "metadata_json": None, "items": None, "artifact": None, "partials": [], "failure_class": "process_failure"}
    result.update(changes)
    return budget._json({"format_version": 1, "runner_contract": "yt_dlp_v1", "kind": kind,
        "segment": segment, "input_sha256": INPUT, "request_sha256": request, "result": result})


def reserve(profile, spec, attempt, settings=None, now=NOW):
    return budget.reserve(spec, attempt, profile=profile, policy=settings or policy(), now=now)


def frozen_v12_tables():
    """The actual old declarations, never latest DDL with a relabelled meta."""
    enum = budget_schema._enum
    legacy = budget_schema.LEGACY_TABLES
    return {
        "budget_operation": legacy["budget_operation"].replace(
            "kind " + enum("kind", ("model", "download")), "kind " + enum("kind",
                ("model", "download", "source_metadata", "source_enumeration", "source_resolution")), 1),
        "budget_attempt": legacy["budget_attempt"].replace(
            "request_sha256 " + budget_schema._sha("request_sha256") + ",\n",
            "request_sha256 " + budget_schema._sha("request_sha256") + ",\n" +
            "download_segment " + enum("download_segment", ("video", "audio"), nullable=True) + ",\n", 1).replace(
            "outcome " + enum("outcome", ("full_result", "http_retry", "repair", "terminal_error"), nullable=True),
            "outcome " + enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete"), nullable=True), 1),
    }


def genuine_v12_database(path):
    seed_legacy_attempts(path)
    tables = frozen_v12_tables()
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN IMMEDIATE")
        for name, declaration in tables.items():
            declaration = declaration.replace("REFERENCES budget_operation(operation_id)",
                                              "REFERENCES _fixture_budget_operation_v12(operation_id)")
            con.execute(f"CREATE TABLE _fixture_{name}_v12 ({declaration})")
            columns = ",".join(budget_schema.LEGACY_COLUMNS[name])
            con.execute(f"INSERT INTO _fixture_{name}_v12({columns}) SELECT {columns} FROM {name}")
        con.execute("DROP TABLE budget_attempt")
        con.execute("DROP TABLE budget_operation")
        for name in tables:
            con.execute(f"ALTER TABLE _fixture_{name}_v12 RENAME TO {name}")
        for statement in budget_schema.INDEXES:
            con.execute(statement)
        con.execute("UPDATE meta SET value='12' WHERE key='migration_version'")
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # Add source v2 paid and media v2 pending facts absent from schema11.
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        stamp, settings = budget._stamp(NOW), policy()
        for kind, segment, state, outcome in (("source_metadata", None, "settled", "source_step_complete"),
                ("source_enumeration", None, "settled", "full_result"),
                ("download", "video", "settled", "source_step_complete"),
                ("download", "audio", "indeterminate", None)):
            spec = operation(kind=kind, key="frozen12:" + str(segment))
            _, identity_sha, op_id = budget._identity(spec, library)
            con.execute("""INSERT INTO budget_operation(operation_id,library_id,identity_sha256,continuation_id,
                execution_generation,kind,suboperation_key,input_sha256,wait_details_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (op_id, library, identity_sha, spec.continuation_id,
                1, kind, spec.suboperation_key, INPUT, '{"download_interval_seconds":0}' if kind == "download" else '{}', stamp, stamp))
            snapshot = {"format_version": 2, "kind": kind, "download_segment": segment,
                "policy": settings.public(), "window": contract.period_for(settings, NOW).public(),
                "units": {"model_attempts": 0, "reserved_output_tokens": 0,
                    "download_videos": int(kind == "download"), "source_requests": int(kind != "download")},
                "result_bound": budget.MAX_RESULT_BYTES, "adapter": None, "pacing_slot": None, "admitted_at": stamp}
            raw, sha, attempt_id = budget._json(snapshot), budget._digest(snapshot), str(uuid.uuid4())
            token = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
            first = {"format_version": 2, "reservation": snapshot, "attempt_id": attempt_id,
                "ordinal": 1, "request_sha256": REQUEST, "dispatch_token_sha256": token, "dispatched_at": stamp}
            values = {"attempt_id": attempt_id, "operation_id": op_id, "ordinal": 1,
                "request_sha256": REQUEST, "download_segment": segment, "state": state,
                "initial_reservation_json": raw, "initial_reservation_sha256": sha,
                "current_reservation_json": raw, "current_reservation_sha256": sha,
                "current_policy_sha256": contract.policy_sha256(settings),
                "current_period_key": snapshot["window"]["period_key"],
                "current_window_start_utc": snapshot["window"]["start_utc"],
                "current_window_end_utc": snapshot["window"]["end_utc"], "last_admitted_at": stamp,
                "created_at": stamp, "updated_at": stamp, "first_dispatch_json": budget._json(first),
                "first_dispatch_sha256": budget._digest(first), "dispatch_token_sha256": token, "dispatched_at": stamp}
            if state == "settled":
                text = '{"frozen12":true}'
                result_sha = hashlib.sha256(text.encode()).hexdigest()
                receipt = budget._settlement_receipt(REQUEST, outcome, None, result_sha, len(text.encode()), None)
                values.update(response_complete=1, outcome=outcome, result_text=text,
                    result_sha256=result_sha, result_bytes=len(text.encode()), result_storage_state="stored",
                    settlement_sha256=budget._digest(receipt), settled_at=stamp)
            else:
                values["indeterminate_at"] = stamp
            columns = ",".join(values)
            con.execute(f"INSERT INTO budget_attempt({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_explicit_genuine12_upgrade_keeps_every_old_fact_and_backup(tmp_path):
    path = tmp_path / "genuine12.db"
    genuine_v12_database(path)
    before = legacy_rows(path)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    assert_legacy_rows(path, before)
    assert_legacy_rows(result.backup_path, before)
    manifest = json.loads(result.manifest_path.read_text())
    assert (manifest["from_version"], manifest["to_version"]) == (12, migrations.SCHEMA_VERSION)
    with sqlite3.connect(result.backup_path) as con:
        budget_schema.validate(con, version=12)
        assert "source_retry" not in con.execute("SELECT sql FROM sqlite_master WHERE name='budget_attempt'").fetchone()[0]
    with db.connect(path) as con:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        assert "source_retry" in con.execute("SELECT sql FROM sqlite_master WHERE name='budget_attempt'").fetchone()[0]
        assert not con.execute("SELECT name FROM sqlite_master WHERE name LIKE '_budget_%'").fetchall()
    assert not db.migrate(path, profile=migration_profile(path)).changed


def test_ordinary_connect_and_initialize_refuse_genuine12_without_writing(tmp_path):
    path = tmp_path / "genuine12.db"
    genuine_v12_database(path)
    before = path.read_bytes()
    for entry in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            entry(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


@pytest.mark.parametrize("failure", ["rebuild", "fk"])
def test_genuine12_rebuild_failure_rolls_back_all_facts_and_fk(tmp_path, monkeypatch, failure):
    path = tmp_path / "genuine12.db"
    genuine_v12_database(path)
    before = legacy_rows(path)
    original = budget_schema.upgrade_v12
    def interrupted(con):
        original(con)
        if failure == "fk":
            con.execute("UPDATE job SET budget_operation_id=?", (str(uuid.uuid4()),))
        else:
            raise sqlite3.DatabaseError("synthetic S13 migration interruption")
    monkeypatch.setattr(budget_schema, "upgrade_v12", interrupted)
    with pytest.raises((db.SchemaVersionError, sqlite3.DatabaseError)):
        db.migrate(path, profile=migration_profile(path))
    assert_legacy_rows(path, before)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "12"
        budget_schema.validate(con, version=12)


@pytest.mark.parametrize("kind", ["source_metadata", "source_enumeration", "download"])
def test_strict_source_snapshot_v3_dispatch_and_read_version(profile, kind):
    segment = "video" if kind == "download" else None
    spec = operation(kind=kind)
    first = reserve(profile, spec, source_attempt(segment=segment))
    initial = json.loads(query(profile, "SELECT initial_reservation_json FROM budget_attempt")[0]["initial_reservation_json"])
    assert initial["format_version"] == 3 and initial["source_contract_version"] == 1
    assert initial["kind"] == kind and initial["download_segment"] == segment
    assert dispatch(profile, first).won
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    assert json.loads(row["first_dispatch_json"])["format_version"] == 3
    assert budget.get_operation(spec, profile=profile).attempts[0].source_contract_version == 1
    assert budget.get_attempt(first.operation_id, 1, profile=profile).source_contract_version == 1


@pytest.mark.parametrize("version", [False, True, 0, 2, "1"])
def test_source_contract_version_is_exact_optional_integer(version):
    with pytest.raises(budget.BudgetLedgerError):
        source_attempt(version=version)


def test_model_never_acquires_source_contract_v3(profile):
    with pytest.raises(budget.BudgetLedgerError):
        budget.reserve(operation(), budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100,
            source_contract_version=1), profile=profile, policy=policy(), now=NOW)
    assert query(profile, "SELECT * FROM budget_operation") == []


def test_resolution_does_not_gain_the_local_source_contract_before_b06(profile):
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, operation(kind="source_resolution"), source_attempt())
    assert query(profile, "SELECT * FROM budget_operation") == []


def test_dispatch_policy_is_frozen_read_only_and_not_current_configuration(profile, monkeypatch):
    spec = operation(kind="download")
    before = policy(download_rate_bytes_per_second=321)
    first = reserve(profile, spec, source_attempt(segment="video"), before)
    with pytest.raises(budget.BudgetLedgerError):
        budget.get_dispatch_policy(first.attempt_id, REQUEST, profile=profile)
    assert dispatch(profile, first, before).won
    def forbidden():
        pytest.fail("first-dispatch policy reads must not use fresh configuration")
    monkeypatch.setattr(budget.config, "budget_settings", forbidden)
    old_rows = query(profile, "SELECT * FROM budget_attempt")
    actual = budget.get_dispatch_policy(first.attempt_id, REQUEST, profile=profile)
    assert type(actual) is contract.Policy and actual == before
    assert query(profile, "SELECT * FROM budget_attempt") == old_rows
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
        budget.get_dispatch_policy(first.attempt_id, "f" * 64, profile=profile)


@pytest.mark.parametrize("kind", ["download", "source_metadata", "source_enumeration"])
def test_normal_typed_source_retry_reuses_operation_and_allows_same_segment(profile, kind):
    segment = "video" if kind == "download" else None
    spec, settings = operation(kind=kind), policy(model_operation_max_attempts=3)
    first = reserve(profile, spec, source_attempt(segment=segment), settings)
    assert dispatch(profile, first, settings).won
    settled = budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                            result_text=envelope(kind, segment), now=NOW)
    assert settled.settlement_sha256 and settled.text == envelope(kind, segment)
    assert budget.get_result(first.attempt_id, REQUEST, profile=profile) == settled
    second = reserve(profile, spec, source_attempt(ordinal=2, segment=segment), settings)
    assert second.operation_id == first.operation_id and dispatch(profile, second, settings).won
    budget.settle(second.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                  result_text=envelope(kind, segment), now=NOW)
    assert reserve(profile, spec, source_attempt(ordinal=3, segment=segment), settings).ordinal == 3
    summary = budget.summarize(profile=profile, policy=settings, now=NOW)
    if kind == "download":
        assert summary["download_logical_admissions"] == 1 and summary["download_process_attempts"] == 3
    else:
        assert summary[kind + "_attempts"] == 3 and summary["download_logical_admissions"] == 0


@pytest.mark.parametrize("changes", [{"returncode": 0}, {"returncode": -1}, {"returncode": True},
    {"process_exited": False}, {"drain_complete": False}, {"termination": "timeout"},
    {"failure_class": "terminal"}])
def test_retry_requires_normal_complete_typed_failure_not_transport_guess(profile, changes):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first).won
    with pytest.raises(budget.BudgetLedgerError):
        budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                      result_text=envelope(**changes), now=NOW)
    assert budget.get_attempt(first.operation_id, 1, profile=profile).state == "dispatching"


def test_v2_source_and_model_cannot_use_new_source_retry(profile):
    for kind in ("model", "source_metadata", "download"):
        spec = operation(kind=kind)
        attempt = (budget.AttemptSpec(REQUEST, adapter="paas_v4", effective_max_tokens=100) if kind == "model"
                   else budget.AttemptSpec(REQUEST, download_segment="video" if kind == "download" else None))
        first = reserve(profile, spec, attempt)
        assert dispatch(profile, first).won
        with pytest.raises(budget.BudgetLedgerError):
            budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                          result_text=envelope(kind, "video" if kind == "download" else None), now=NOW)


def test_retry_body_capacity_loss_does_not_authorize_next_ordinal(profile, monkeypatch):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first).won
    monkeypatch.setattr(budget, "MAX_RESULT_BYTES", 1)
    result = budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                          result_text=envelope(), now=NOW)
    assert result.storage_state == "oversize" and result.settlement_sha256
    with pytest.raises(contract.BudgetDeferred, match="result_capacity"):
        reserve(profile, spec, source_attempt(ordinal=2, segment="video"))
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_metadata_step_then_refresh_result_keeps_old_body_and_v3(profile):
    spec = operation(kind="source_metadata")
    first = reserve(profile, spec, source_attempt())
    assert dispatch(profile, first).won
    budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_step_complete",
                  result_text='{"original":true}', now=NOW)
    refresh_request = hashlib.sha256(b"synthetic refresh failure-bound request").hexdigest()
    second = reserve(profile, spec, source_attempt(ordinal=2, request=refresh_request))
    assert dispatch(profile, second).won
    budget.settle(second.attempt_id, refresh_request, profile=profile, outcome="full_result",
                  result_text='{"refreshed":true}', now=NOW)
    assert budget.get_result(first.attempt_id, REQUEST, profile=profile).text == '{"original":true}'
    assert budget.get_result(second.attempt_id, refresh_request, profile=profile).text == '{"refreshed":true}'
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_STATE_CONFLICT"):
        reserve(profile, spec, source_attempt(ordinal=3))


@pytest.mark.parametrize("damage", ["trigger", "index", "temporary"])
def test_genuine12_unknown_structure_stops_before_any_rebuild(tmp_path, damage):
    path = tmp_path / "genuine12.db"
    genuine_v12_database(path)
    with sqlite3.connect(path) as con:
        if damage == "trigger":
            con.execute("CREATE TRIGGER unknown_budget_hook AFTER INSERT ON budget_attempt BEGIN SELECT 1; END")
        elif damage == "index":
            con.execute("CREATE INDEX unknown_budget_index ON budget_attempt(request_sha256)")
        else:
            con.execute("CREATE TABLE _budget_attempt_v13(unknown TEXT)")
    before = legacy_rows(path)
    with pytest.raises(sqlite3.DatabaseError):
        db.migrate(path, profile=migration_profile(path))
    assert_legacy_rows(path, before)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "12"


def test_old_v1_v2_paid_reads_and_dispatch_policy_keep_all_hashes_after_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "genuine12.db"
    genuine_v12_database(path)
    target = migration_profile(path)
    db.migrate(path, profile=target)
    before = query(target, "SELECT * FROM budget_attempt ORDER BY attempt_id")
    def forbidden():
        pytest.fail("paid reads cannot require current budget settings")
    monkeypatch.setattr(budget.config, "budget_settings", forbidden)
    for row in before:
        if row["state"] in {"settled", "indeterminate"}:
            assert type(budget.get_dispatch_policy(row["attempt_id"], REQUEST, profile=target)) is contract.Policy
        if row["state"] == "settled":
            actual = budget.get_result(row["attempt_id"], REQUEST, profile=target)
            assert actual.text == row["result_text"] and actual.settlement_sha256 == row["settlement_sha256"]
    assert query(target, "SELECT * FROM budget_attempt ORDER BY attempt_id") == before


def test_strict_contract_is_exact_on_replay_and_cannot_downgrade_next_ordinal(profile):
    spec = operation(kind="source_metadata")
    first = reserve(profile, spec, source_attempt())
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
        reserve(profile, spec, budget.AttemptSpec(REQUEST))
    assert dispatch(profile, first).won
    budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_step_complete",
                  result_text='{"initial":true}', now=NOW)
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
        reserve(profile, spec, budget.AttemptSpec(REQUEST, ordinal=2))
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


@pytest.mark.parametrize("outcome", ["http_retry", "repair"])
def test_v3_source_failure_cannot_borrow_model_http_outcomes(profile, outcome):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first).won
    with pytest.raises(budget.BudgetLedgerError):
        budget.settle(first.attempt_id, REQUEST, profile=profile, outcome=outcome,
                      result_text=envelope(), now=NOW)
    assert budget.get_attempt(first.operation_id, 1, profile=profile).state == "dispatching"


def test_first_dispatch_policy_proof_tampering_is_rejected(profile):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first, policy(download_rate_bytes_per_second=41)).won
    with sqlite3.connect(profile.database) as con:
        row = con.execute("SELECT first_dispatch_json FROM budget_attempt").fetchone()[0]
        damaged = json.loads(row)
        damaged["reservation"]["policy"]["download_rate_bytes_per_second"] = 99
        con.execute("UPDATE budget_attempt SET first_dispatch_json=?,first_dispatch_sha256=?",
                    (budget._json(damaged), budget._digest(damaged)))
    with pytest.raises(budget.BudgetLedgerError):
        budget.get_dispatch_policy(first.attempt_id, REQUEST, profile=profile)


def test_retained_retry_body_is_revalidated_before_restore_or_new_ordinal(profile):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first).won
    budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                  result_text=envelope(), now=NOW)
    damaged = envelope(returncode=0)
    raw = damaged.encode()
    sha = hashlib.sha256(raw).hexdigest()
    receipt = budget._settlement_receipt(REQUEST, "source_retry", None, sha, len(raw), None)
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE budget_attempt SET result_text=?,result_sha256=?,result_bytes=?,settlement_sha256=?",
                    (damaged, sha, len(raw), budget._digest(receipt)))
    for action in (lambda: budget.get_result(first.attempt_id, REQUEST, profile=profile),
                   lambda: reserve(profile, spec, source_attempt(ordinal=2, segment="video"))):
        with pytest.raises(budget.BudgetLedgerError):
            action()
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_retry_proof_must_bind_operation_input_kind_segment_and_request(profile):
    spec = operation(kind="download")
    first = reserve(profile, spec, source_attempt(segment="video"))
    assert dispatch(profile, first).won
    for field, value in (("input_sha256", "f" * 64), ("request_sha256", "f" * 64),
                         ("kind", "source_metadata"), ("segment", "audio")):
        raw = json.loads(envelope())
        raw[field] = value
        with pytest.raises(budget.BudgetLedgerError):
            budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry",
                          result_text=budget._json(raw), now=NOW)
    assert budget.get_attempt(first.operation_id, 1, profile=profile).state == "dispatching"


def test_retry_respects_frozen_total_cap_released_ordinals_and_pacing(profile):
    spec = operation(kind="download")
    settings = policy(model_operation_max_attempts=3,
                      download_interval_min_seconds=10, download_interval_max_seconds=10)
    first = reserve(profile, spec, source_attempt(segment="video"), settings)
    assert dispatch(profile, first, settings).won
    budget.settle(first.attempt_id, REQUEST, profile=profile, outcome="source_retry", result_text=envelope(), now=NOW)
    with pytest.raises(contract.BudgetDeferred, match="download_pacing"):
        reserve(profile, spec, source_attempt(ordinal=2, segment="video"), settings)
    later = NOW + timedelta(seconds=10)
    second = reserve(profile, spec, source_attempt(ordinal=2, segment="video"), settings, later)
    budget.release_reserved(second.attempt_id, second.reservation_sha256, expected_revision=second.revision,
                            profile=profile, now=later)
    third = reserve(profile, spec, source_attempt(ordinal=3, segment="video"), settings, later)
    assert dispatch(profile, third, settings, now=later).won
    budget.settle(third.attempt_id, REQUEST, profile=profile, outcome="source_retry", result_text=envelope(), now=later)
    with pytest.raises(contract.BudgetDeferred, match="attempt_limit"):
        reserve(profile, spec, source_attempt(ordinal=4, segment="video"), policy(model_operation_max_attempts=128), later)
    assert budget.get_operation(spec, profile=profile).max_attempts == 3
