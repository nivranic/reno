"""Actual isolated schema17 SQL cannot stand in for State/permanent witnesses."""
from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
import json
import sqlite3

import pytest

from reno import migrations, storage_lifecycle
from reno import source_preservation as legacy, source_preservation_v17 as prospective
from reno import sync_source_readmit_schema as schema
from tests import test_sync_source_readmit_contract as sample
from tests import test_sync_source_readmit_schema as records
from tests.test_source_preservation import env as released16_environment
from tests.test_source_preservation import build_released16_environment, seed, transaction
from tests.schema_fixtures import assert_current17_runtime, assert_released16_image, released16


def _original_sql_rows(con):
    # Include every original operational/knowledge/shadow table and the actual
    # operational sequence cells; no paid/private namespace is filtered away.
    tables = [row[0] for row in con.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' ORDER BY name")]
    return {name: sorted((tuple(row) for row in con.execute(
        'SELECT * FROM main."' + name.replace('"', '""') + '"')), key=repr) for name in tables}


def _original_files(value):
    # Every non-SQLite original file under all four roots remains exact.
    # Snapshot outside the profile guard so Windows lock bytes are not read
    # through a lock handle owned by the fixture itself.
    excluded = {value.profile.database.with_name(value.profile.database.name + suffix)
                for suffix in ("", "-wal", "-shm", "-journal")}
    roots = (value.profile.database.parent, value.profile.media,
             value.profile.reports, value.profile.checkpoints)
    return {(label, str(path.relative_to(root))): path.read_bytes()
        for label, root in enumerate(roots) for path in root.rglob("*")
        if path.is_file() and path not in excluded}


def build_historical17_environment(tmp_path, monkeypatch, *, paid_unknown=False):
    # First create original16 SQL/tasks/State and, when requested, the genuine
    # original paid/unknown bodies. Close ALL old owners and version rules
    # before the first explicit historical-v1 DDL and current17 consumers.
    with released16(monkeypatch):
        value = build_released16_environment(tmp_path)
        if paid_unknown:
            value.historical_paid_unknown = seed(value)
    assert_current17_runtime()
    assert_released16_image(value.profile.database)
    files = _original_files(value)
    with storage_lifecycle.shared(value.profile):
        # This is an explicit isolated fixture transition, not an ordinary
        # connection bypass, production migration or a fabricated admission.
        with closing(sqlite3.connect(value.profile.database)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("BEGIN IMMEDIATE")
            assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
            assert not schema.preflight(con, require_absent=True)
            before = _original_sql_rows(con)
            schema.install(con)
            con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
            schema.validate(con)
            migrations.validate(con)
            after = _original_sql_rows(con)
            assert set(after) == set(before) | set(schema.COLUMNS)
            for table, rows in before.items():
                if table == "meta":
                    unchanged = lambda values: [row for row in values if row[0] != "migration_version"]
                    assert unchanged(after[table]) == unchanged(rows)
                else:
                    assert after[table] == rows
            assert con.execute("PRAGMA foreign_key_check").fetchone() is None
            con.commit()
    assert _original_files(value) == files
    assert_current17_runtime()
    return value


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Historical17-v1 SQL is now consumed under the real current17 gate.
    # Never inherit a yielding old16 parent or reverse-install over default17.
    return build_historical17_environment(tmp_path, monkeypatch)


@pytest.fixture
def historical17_paid_environment(tmp_path, monkeypatch):
    return build_historical17_environment(tmp_path, monkeypatch, paid_unknown=True)


@contextmanager
def schema17(env):
    # The v1 DDL was installed ONCE after closing the original16 producer.
    # Subsequent windows authenticate that actual historical namespace; no
    # table/trigger is removed and no current default-v2 image is relabelled.
    assert_current17_runtime()
    with transaction(env, released_v15=False) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
        schema.validate(con)
        for item in (7, 8):
            con.execute("INSERT OR IGNORE INTO sync_queue(item_id,url,status,created_at,updated_at) "
                        "VALUES(?,?,'queued',?,?)", (item, "https://synthetic.example.test/video",
                                                       sample.COMMITTED, sample.COMMITTED))
        yield con

def capture(env, con, *, root=True):
    return prospective.capture_sql(con, profile=env.profile, root_job_id=env.root if root else None)


def full_records(con):
    active = records.activation()
    registered = records.registration(active=active)
    disposed = records.disposition(registered=registered, applied=False,
                                  old=json.loads(registered["original_request_json"]))
    admitted = records.admission()
    values = ((schema.ACTIVATION, active), (schema.REGISTRATION, registered),
              (schema.DISPOSITION, disposed), (schema.ADMISSION, admitted))
    for table, row in values:
        records.insert(con, table, schema.validate_record(table, row))
    return dict(values)


def rewrite_record(con, table, row):
    # Controlled fixture damage/replacement, never a runtime repair path.
    con.execute('DROP TRIGGER "' + table + '_no_update"')
    fields = schema.COLUMNS[table]
    primary = fields[0]
    con.execute("UPDATE " + table + " SET " + ",".join('"' + field + '"=?' for field in fields) +
                ' WHERE "' + primary + '"=?', (*[row[field] for field in fields], row[primary]))
    con.execute(next(sql for sql in schema.TRIGGERS if sql.startswith(
        "CREATE TRIGGER " + table + "_no_update ")))


def test_all_four_full_sql_tables_and_old_paid_unknown_private_facts_are_captured(historical17_paid_environment):
    env = historical17_paid_environment
    paid, unknown = env.historical_paid_unknown
    with schema17(env) as con:
        values = full_records(con)
        con.execute("INSERT INTO meta(key,value) VALUES('private-schema17-blob',?)", (b"\x00\xffprivate",))
        seen = set()

        def audit(action, table, column, _database, _origin):
            if action == sqlite3.SQLITE_READ and table in schema.COLUMNS:
                seen.add((table, column))
            return sqlite3.SQLITE_OK

        con.set_authorizer(audit)
        before = capture(env, con)
        document = before.public()
        assert document["format_version"] == 1 and document["schema_version"] == "17"
        assert document["scope"] == "sql_capture_comparison" and document["continuity"] == "not_established"
        assert not {"preserved_sha256", "checkpoint", "state_authority", "permanent_witnesses"} & set(document)
        assert {table: row["rows"] for row in document["tables"] for table in schema.COLUMNS
                if row["name"] == table} == dict.fromkeys(schema.COLUMNS, 1)
        assert {(table, column) for table, columns in schema.COLUMNS.items() for column in columns} <= seen
        attempts = {entry["attempt_id"]: entry for operation in document["frontier"]["operations"]
                    for entry in operation["attempts"]}
        assert attempts[paid.attempt_id]["state"] == "settled"
        assert attempts[unknown.attempt_id]["state"] == "dispatching"
        for marker in ("private", sample.LEASE["lease_token"], sample.CLAIM["lease_token"],
                       json.loads(values[schema.ADMISSION]["result_json"])["claim"]["lease_token"]):
            assert marker not in before.document_json
        assert prospective.verify_sql_unchanged(con, before, profile=env.profile).unchanged
        assert con.in_transaction


def test_other_original_core_and_item_are_not_hidden_by_selected_root(env, monkeypatch):
    with schema17(env) as con:
        full_records(con)
        before = capture(env, con)
        with monkeypatch.context() as scoped:
            scoped.setattr(sample, "CORE", "9" * 64)
            scoped.setattr(sample, "CLAIM", {**sample.CLAIM, "item_id": 8})
            row = records.admission()
        records.insert(con, schema.ADMISSION, row)
        assert {entry["name"]: entry["rows"] for entry in capture(env, con).public()["tables"]}[schema.ADMISSION] == 2
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            prospective.verify_sql_unchanged(con, before, profile=env.profile)
        # The same global table remains preserved when no root is selected.
        assert {entry["name"]: entry["rows"] for entry in capture(env, con, root=False).public()["tables"]}[schema.ADMISSION] == 2


def test_valid_private_admission_result_replacement_cannot_pass_sql_comparison(env):
    with schema17(env) as con:
        old = full_records(con)[schema.ADMISSION]
        before = capture(env, con)
        replacement = records.admission(body=json.loads(old["request_json"]))
        assert replacement["admission_id"] == old["admission_id"]
        assert json.loads(replacement["result_json"])["claim"]["lease_token"] != json.loads(old["result_json"])["claim"]["lease_token"]
        rewrite_record(con, schema.ADMISSION, replacement)
        schema.validate(con)
        schema.validate_record(schema.ADMISSION, replacement)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            prospective.verify_sql_unchanged(con, before, profile=env.profile)


@pytest.mark.parametrize("table,field", [(schema.ACTIVATION, "contract_sha256"),
    (schema.REGISTRATION, "original_request_sha256"), (schema.DISPOSITION, "result_sha256"),
    (schema.ADMISSION, "request_sha256")])
def test_full_private_typed_validator_rejects_each_table_digest_mismatch(env, table, field):
    with schema17(env) as con:
        row = full_records(con)[table]
        row[field] = "8" * 64
        rewrite_record(con, table, row)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_LEDGER_INVALID"):
            capture(env, con)


@pytest.mark.parametrize("statement", [
    "CREATE TABLE future_private_schema17(value BLOB)",
    "ALTER TABLE sync_source_admission ADD COLUMN forgotten_private BLOB",
    "CREATE TEMP TABLE sync_source_admission(value BLOB)",
    "CREATE TEMP VIEW idx_source_registration_sha AS SELECT 1 AS hidden",
    "DROP INDEX idx_source_admission_core",
    "DROP TRIGGER sync_source_control_registration_no_delete",
])
def test_unknown_schema_names_columns_indexes_and_triggers_are_not_silently_omitted(env, statement):
    with schema17(env) as con:
        con.execute(statement)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            capture(env, con)


def test_foreign_queue_record_cannot_be_signed_in_a_deferred_inconsistent_transaction(env):
    with schema17(env) as con:
        con.execute("PRAGMA defer_foreign_keys=ON")
        row = records.admission()
        row["item_id"] = 9
        # Use a genuinely matching body/result with an absent item FK.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sample, "CLAIM", {**sample.CLAIM, "item_id": 9})
            row = records.admission()
        records.insert(con, schema.ADMISSION, row)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_LEDGER_INVALID"):
            capture(env, con)


def test_cell_bounds_reject_private_body_before_materializing_the_row(env, monkeypatch):
    with schema17(env) as con:
        full_records(con)
        order, _, _, projections = legacy._schema(con, prospective_schema17=True)
        seen = []
        con.set_trace_callback(seen.append)
        monkeypatch.setattr(legacy, "MAX_CELL_BYTES", 500)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_LIMIT"):
            list(legacy._rows(con, schema.ADMISSION, order, [0, 0], projections))
        assert any(query.startswith("SELECT length(CAST(") for query in seen)
        assert not any(query.startswith('SELECT "admission_id"') for query in seen)


def test_sql_only_capture_does_not_read_initialize_or_claim_state_or_witnesses(env, monkeypatch):
    with schema17(env) as con:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("SQL-only capture must not claim State/witness observation")

        monkeypatch.setattr(env.store, "_read", forbidden)
        before = capture(env, con)
        statements = []
        con.set_trace_callback(statements.append)
        con.execute("PRAGMA query_only=ON")
        after = capture(env, con)
        assert after.document_json == before.document_json
        assert con.in_transaction
        assert not any(query.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "BEGIN", "COMMIT")) for query in statements)


def test_released_capture_and_comparison_never_relabel_or_consume_sql_only17(env):
    with schema17(env) as con:
        before = capture(env, con)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            legacy.capture(con, profile=env.profile, store=env.store, root_job_id=env.root)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            legacy.verify_unchanged(con, before, profile=env.profile, store=env.store)


def test_released_15_16_certificate_never_accepts_actual_state5_by_widening_an_old_checkpoint(released16_environment):
    env = released16_environment
    env.store.enable_source_readmit()
    assert env.store.read()["format_version"] == 5
    with transaction(env, released_v15=False) as con:
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CHECKPOINT_INVALID"):
            legacy.capture(con, profile=env.profile, store=env.store, root_job_id=env.root)
        assert env.store.read()["format_version"] == 5 and con.in_transaction


@pytest.mark.parametrize("version", [15, 16])
def test_schema17_entry_refuses_old_sql_and_old_certificate_but_old_hash_format_stays_exact(released16_environment, version):
    env = released16_environment
    with transaction(env, released_v15=version == 15) as con:
        before = legacy.capture(con, profile=env.profile, store=env.store, root_job_id=env.root)
        document = before.public()
        assert document["schema_version"] == str(version) and document["format_version"] == 1
        assert hashlib.sha256(legacy._encoded(document)).hexdigest() == before.sha256
        _, knowledge, original_sql = legacy._sql_capture(con, profile=env.profile, root_job_id=env.root)
        assert document["knowledge_version"] == knowledge
        assert {key: document[key] for key in prospective._SQL_FIELDS} == original_sql
        assert document["preserved_sha256"] == legacy._sha({**original_sql, "checkpoint": document["checkpoint"]})
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            capture(env, con)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            prospective.verify_sql_unchanged(con, before, profile=env.profile)


def test_sql_digest_and_canonical_document_tampering_is_rejected(env):
    with schema17(env) as con:
        before = capture(env, con)
        for damaged in (replace(before, sha256="0" * 64),
                        replace(before, document_json=before.document_json + " ")):
            with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
                prospective.verify_sql_unchanged(con, damaged, profile=env.profile)


def test_actual_sql_transaction_and_profile_are_required(env):
    with schema17(env) as con:
        foreign = replace(env.profile, database=env.profile.database.parent / "other.db")
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_PROFILE_MISMATCH"):
            prospective.capture_sql(con, profile=foreign, root_job_id=env.root)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_TRANSACTION_REQUIRED"):
            prospective.capture_sql({"transaction": con}, profile=env.profile, root_job_id=env.root)
        con.rollback()
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_TRANSACTION_REQUIRED"):
            capture(env, con)


def test_only_knowledge_epoch_revision_and_step_changes_remain_outside_sql_preservation(env):
    with schema17(env) as con:
        before = capture(env, con)
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key IN ('library_epoch','kb_revision')")
        con.execute("INSERT INTO meta(key,value) VALUES('step:isolated:atomize',?)", (b"private-step",))
        result = prospective.verify_sql_unchanged(con, before, profile=env.profile)
        assert result.unchanged and result.before_sha256 != result.after_sha256
        con.execute("UPDATE job SET manual_paused=1 WHERE job_id=?", (env.root,))
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            prospective.verify_sql_unchanged(con, before, profile=env.profile)
