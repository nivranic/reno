"""Resolution v4 admission and genuine v13 migration use synthetic facts only."""
from dataclasses import replace
from contextlib import closing
import json
import sqlite3

import pytest

from reno import budget, budget_schema, config, db, migrations
from reno import source_resolution_contract as codec
from tests.migration_helpers import migration_profile, initialize_migration_storage, drop_future_home_recovery
from tests.test_budget_ledger import NOW, INPUT, REQUEST, profile, operation, policy, query, dispatch
from tests.test_budget_source_v13 import envelope
from tests.test_budget_sources import legacy_rows, assert_legacy_rows
from tests.schema_fixtures import released16, assert_current17_runtime


URL = "https://www.bilibili.com/video/BV13x41117TL"


def freeze_v13_database(path):
    """Build actual old CHECK/queue DDL, keeping every existing synthetic fact."""
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN IMMEDIATE")
        drop_future_home_recovery(con)
        con.execute(f"CREATE TABLE _fixture_budget_attempt_v13 ({budget_schema.V13_TABLES['budget_attempt']})")
        names = ",".join(budget_schema.V12_COLUMNS["budget_attempt"])
        con.execute(f"INSERT INTO _fixture_budget_attempt_v13({names}) SELECT {names} FROM budget_attempt")
        con.execute("DROP TABLE budget_attempt")
        con.execute("ALTER TABLE _fixture_budget_attempt_v13 RENAME TO budget_attempt")
        budget_schema._install(con, budget_schema.V13_TABLES)
        con.execute("DROP TABLE sync_queue_budget_receipt")
        con.execute("DROP TABLE sync_queue_source_receipt")
        con.execute("ALTER TABLE sync_queue DROP COLUMN budget_wait_reason")
        con.execute("ALTER TABLE sync_queue DROP COLUMN budget_operation_id")
        con.execute("UPDATE meta SET value='13' WHERE key='migration_version'")
        budget_schema.validate(con, version=13)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def plan(cap=24):
    return codec.ResolutionPlan("dns", URL, URL, INPUT, cap)


def attempt(value, ordinal=1):
    return budget.AttemptSpec(value.request_sha256, ordinal=ordinal, resolver_contract_version=1,
                              resolver_phase=value.phase, resolver_max_attempts=value.max_attempts)


def proof(value, *, successful=False):
    result = codec.ResolutionResult(value.request_sha256, "dns", True, "completed",
        addresses=("8.8.8.8",) if successful else None,
        failure_class=None if successful else "dns_failure", dns_error=None if successful else "temporary",
        observed_at=budget._stamp(NOW))
    return codec.encode_envelope(value, result)


def test_v4_freezes_new_resolution_cap_and_does_not_change_policy_hash(profile, monkeypatch):
    monkeypatch.setattr(config, "source_resolution_attempt_limit", lambda: 3)
    spec, value, settings = operation(kind="source_resolution"), plan(3), policy()
    first = budget.reserve(spec, attempt(value), profile=profile, policy=settings, now=NOW)
    snapshot = json.loads(query(profile, "SELECT initial_reservation_json FROM budget_attempt")[0]["initial_reservation_json"])
    assert snapshot["format_version"] == 4 and snapshot["resolver_max_attempts"] == 3
    assert snapshot["policy"] == settings.public() and "source_resolution_max_attempts" not in snapshot["policy"]
    assert dispatch(profile, first, settings).won
    budget.settle(first.attempt_id, value.request_sha256, profile=profile, outcome="resolution_retry", result_text=proof(value), now=NOW)
    monkeypatch.setattr(config, "source_resolution_attempt_limit", lambda: 1)
    for ordinal in (2, 3):
        current = budget.reserve(spec, attempt(value, ordinal), profile=profile, policy=settings, now=NOW)
        assert dispatch(profile, current, settings).won
        budget.settle(current.attempt_id, value.request_sha256, profile=profile, outcome="resolution_retry", result_text=proof(value), now=NOW)
    with pytest.raises(budget.contract.BudgetDeferred, match="attempt_limit"):
        budget.reserve(spec, attempt(value, 4), profile=profile, policy=settings, now=NOW)
    view = budget.get_operation(spec, profile=profile)
    assert view.max_attempts == 3 and len(view.attempts) == 3
    assert view.attempts[0].resolver_phase == "dns"
    assert budget.summarize(profile=profile, policy=settings, now=NOW)["source_resolution_attempts"] == 3


@pytest.mark.parametrize("case", ["kind", "cap", "mixed", "missing", "bool"])
def test_v4_rejects_mixed_or_unbound_resolver_admission(profile, case):
    value = plan()
    with pytest.raises(budget.BudgetLedgerError):
        if case == "mixed":
            replace(attempt(value), source_contract_version=1)
        elif case == "missing":
            replace(attempt(value), resolver_max_attempts=None)
        elif case == "bool":
            replace(attempt(value), resolver_contract_version=True)
        else:
            request = replace(attempt(value), resolver_max_attempts=23) if case == "cap" else attempt(value)
            budget.reserve(operation(kind="download" if case == "kind" else "source_resolution"), request,
                           profile=profile, policy=policy(), now=NOW)
    assert query(profile, "SELECT * FROM budget_attempt") == []


@pytest.mark.parametrize("outcome,success", [("source_step_complete", False), ("resolution_retry", True),
                                           ("terminal_error", True), ("http_retry", False), ("full_result", True)])
def test_v4_settlement_requires_outcome_specific_closed_proof(profile, outcome, success):
    value, spec = plan(), operation(kind="source_resolution")
    reserved = budget.reserve(spec, attempt(value), profile=profile, policy=policy(), now=NOW)
    dispatch(profile, reserved)
    with pytest.raises(budget.BudgetLedgerError):
        budget.settle(reserved.attempt_id, value.request_sha256, profile=profile, outcome=outcome,
                      result_text=proof(value, successful=success), now=NOW)
    assert query(profile, "SELECT state FROM budget_attempt")[0]["state"] == "dispatching"


def test_genuine_v13_rebuild_preserves_v3_receipt_every_byte_and_backup(tmp_path, monkeypatch):
    with released16(monkeypatch):
        original_profile = profile.__wrapped__(tmp_path)
        spec = operation(kind="source_metadata")
        first = budget.reserve(spec, budget.AttemptSpec(REQUEST, source_contract_version=1),
                               profile=original_profile, policy=policy(), now=NOW)
        dispatch(original_profile, first)
        text = envelope("source_metadata", None)
        budget.settle(first.attempt_id, REQUEST, profile=original_profile,
                     outcome="source_retry", result_text=text, now=NOW)
        freeze_v13_database(original_profile.database)
        before = legacy_rows(original_profile.database)
    assert_current17_runtime()
    with pytest.raises(migrations.SchemaVersionError):
        db.connect(original_profile.database)
    result = db.migrate(original_profile.database, profile=original_profile)
    assert result.version == migrations.SCHEMA_VERSION and result.changed
    assert_legacy_rows(original_profile.database, before)
    assert_legacy_rows(result.backup_path, before)
    with sqlite3.connect(result.backup_path) as con:
        budget_schema.validate(con, version=13)
        assert "resolution_retry" not in con.execute("SELECT sql FROM sqlite_master WHERE name='budget_attempt'").fetchone()[0]
    assert budget.get_result(first.attempt_id, REQUEST, profile=original_profile).text == text
    with db.connect(original_profile.database) as con:
        budget_schema.validate(con, version=14)
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM sync_queue_budget_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("correct_shape", [False, True])
@pytest.mark.parametrize("version", [10, 11, 12, 13])
def test_old_unknown_receipt_name_collision_fails_before_any_mutation(tmp_path, monkeypatch, correct_shape, version):
    import gc
    from reno import sync_schema
    from tests.test_budget_migrations import v10_database
    from tests.test_budget_sources import seed_legacy_attempts
    from tests.test_budget_source_v13 import genuine_v12_database
    path = tmp_path / "old-collision.db"
    if version == 10:
        v10_database(path)
    elif version == 11:
        seed_legacy_attempts(path)
    elif version == 12:
        genuine_v12_database(path)
    else:
        with released16(monkeypatch):
            db.initialize(path)
            freeze_v13_database(path)
        assert_current17_runtime()
    # Older fixture builders use sqlite connection contexts, which commit but
    # leave unreachable connection cycles until GC. Release their real Windows
    # handles before the private-file admission check; do not patch the guard.
    gc.collect()
    profile = initialize_migration_storage(path)
    with sqlite3.connect(path) as con:
        declaration = sync_schema.TABLES["sync_queue_budget_receipt"]
        if not correct_shape:
            declaration = declaration.replace("CHECK(recovery_generation>=0)", "")
        con.execute(f"CREATE TABLE sync_queue_budget_receipt ({declaration})")
        con.execute("INSERT INTO video_asset(video_id,title) VALUES('collision-video','original knowledge')")
        before_sql = con.execute("SELECT sql FROM sqlite_master WHERE name='sync_queue_budget_receipt'").fetchone()[0]
        if con.execute("SELECT 1 FROM sync_queue LIMIT 1").fetchone() is None:
            con.execute("INSERT INTO sync_queue(url,status,attempts) VALUES(?,'queued',0)", (URL,))
        item_id = con.execute("SELECT item_id FROM sync_queue LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO sync_queue_budget_receipt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("00000000-0000-4000-8000-000000000001", "a"*64, '{}', 'budget_defer',
             "00000000-0000-4000-8000-000000000002", "00000000-0000-4000-8000-000000000003",
             "00000000-0000-4000-8000-000000000004", "00000000-0000-4000-8000-000000000005",
             1, 0, item_id, 1, '{}', "b"*64, budget._stamp(NOW)))
        tables = ["meta", "video_asset", "knowledge_atom", "job", "sync_queue", "sync_queue_budget_receipt"]
        if version >= 11:
            tables += ["budget_operation", "budget_attempt"]
        before = {table: con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
    identity = (path.parent / "node-identity.json").read_bytes()
    traced = []
    original_open = migrations._open
    def trace_open(path):
        con = original_open(path)
        con.set_trace_callback(traced.append)
        return con
    monkeypatch.setattr(migrations, "_open", trace_open)
    with pytest.raises(migrations.SchemaVersionError, match="collision"):
        db.migrate(path, profile=profile)
    assert not any("_budget_attempt_v14" in sql or "DROP TABLE budget_attempt" in sql for sql in traced)
    assert (path.parent / "node-identity.json").read_bytes() == identity
    with sqlite3.connect(path) as con:
        assert {table: con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables} == before
        assert con.execute("SELECT sql FROM sqlite_master WHERE name='sync_queue_budget_receipt'").fetchone()[0] == before_sql
        if version >= 11:
            budget_schema.validate(con, version=version)
