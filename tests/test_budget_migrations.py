"""Budget schema upgrades use genuine old DDL and isolated artificial data."""
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import db, migrations, storage_schema, sync_protocol
from tests.migration_helpers import migration_profile
from tests.test_storage_migrations import v9_database
from tests.test_sync_migrations import historical_database, snapshot_old_columns, assert_snapshot
from tests.test_storage_roots_group import libraries, enroll, scan, registered


def v10_database(path):
    v9_database(path)
    with sqlite3.connect(path) as con:
        storage_schema.install(con)
        con.execute("UPDATE meta SET value='10' WHERE key='migration_version'")
        con.execute("INSERT INTO job(video_id,kind,status,attempts,error,created_at,updated_at) "
                    "SELECT video_id,kind,'failed',attempts,error,created_at,updated_at FROM job LIMIT 1")


def test_new_live14_installs_empty_budget_tables_and_job_defaults(tmp_path):
    from reno import budget_schema
    path = tmp_path / "fresh.db"
    result = db.initialize(path)
    assert result.version == migrations.SCHEMA_VERSION
    con = db.connect(path)
    try:
        budget_schema.validate(con)
        assert set(budget_schema.TABLES) == {"budget_operation", "budget_attempt"}
        assert all(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0 for name in budget_schema.TABLES)
        con.execute("INSERT INTO job(kind,status) VALUES('process','pending')")
        row = con.execute("SELECT continuation_id,execution_generation,not_before,blocked_reason,"
                          "wait_mode,manual_paused,budget_operation_id FROM job").fetchone()
        assert tuple(row) == (None, 1, None, None, "none", 0, None)
    finally:
        con.close()


def test_v10_upgrade_preserves_knowledge_personal_and_seeds_each_job_separately(tmp_path, monkeypatch):
    from reno import budget_schema, personal_schema, review
    path = tmp_path / "v10.db"
    v10_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        library = db.knowledge_version(con)["library_id"]
        jobs = [row[0] for row in con.execute("SELECT job_id FROM job ORDER BY job_id")]
    retained = {name: (tmp_path / name).read_bytes() for name in ("node-identity.json", "personal-attachments/fixture.pdf")}
    def forbidden(*_args, **_kwargs):
        pytest.fail("Budget migration cannot rewrite knowledge or personal identities")
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(review, "replay", forbidden)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (10, migrations.SCHEMA_VERSION)
    with sqlite3.connect(result.backup_path) as backup:
        assert_snapshot(backup, before, upgraded=False)
        assert not set(budget_schema.TABLES) & migrations._tables(backup)
    con = db.connect(path)
    try:
        budget_schema.validate(con)
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
        actual = con.execute("SELECT job_id,continuation_id,execution_generation FROM job ORDER BY job_id").fetchall()
        expected = [(job, str(uuid.uuid5(uuid.UUID(library), f"budget-lineage:v1:{job}")), 1) for job in jobs]
        assert actual == expected and len({row[1] for row in actual}) == len(jobs)
        assert all(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0 for name in budget_schema.TABLES)
    finally:
        con.close()
    assert all((tmp_path / name).read_bytes() == raw for name, raw in retained.items())
    assert not db.migrate(path, profile=migration_profile(path)).changed


def test_v1_upgrade_reaches_live14_without_guessing_old_retry_lineage(tmp_path):
    from reno import budget_schema
    path = tmp_path / "v1.db"
    historical_database(path, 1)
    with sqlite3.connect(path) as con:
        library = db.knowledge_version(con)["library_id"]
        old_atoms = con.execute("SELECT id,claim FROM knowledge_atom").fetchall()
    result = db.migrate(path, profile=migration_profile(path))
    assert result.version == migrations.SCHEMA_VERSION
    con = db.connect(path)
    try:
        budget_schema.validate(con)
        assert [tuple(row) for row in con.execute("SELECT id,claim FROM knowledge_atom")] == old_atoms
        row = con.execute("SELECT job_id,continuation_id,execution_generation FROM job").fetchone()
        assert row[1:] == (str(uuid.uuid5(uuid.UUID(library), f"budget-lineage:v1:{row[0]}")), 1)
    finally:
        con.close()


@pytest.mark.parametrize("failure", ["ddl", "lineage"])
def test_v10_failure_rolls_back_budget_tables_columns_and_version(tmp_path, monkeypatch, failure):
    from reno import budget_schema
    path = tmp_path / "v10.db"
    v10_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    method = "install" if failure == "ddl" else "seed_legacy_job_lineage"
    original = getattr(budget_schema, method)
    def fail(con):
        original(con)
        raise sqlite3.DatabaseError("synthetic budget migration interruption")
    monkeypatch.setattr(budget_schema, method, fail)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert not set(budget_schema.TABLES) & migrations._tables(con)
        assert "continuation_id" not in {row[1] for row in con.execute("PRAGMA table_info(job)")}


def test_ordinary_connect_and_initialize_never_upgrade_v10(tmp_path):
    path = tmp_path / "v10.db"
    v10_database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


@pytest.mark.parametrize("damage", ["attempt_unique", "table_check", "job_default"])
def test_schema_tampering_is_rejected_without_repair(tmp_path, damage):
    from reno import budget_schema
    path = tmp_path / "fresh.db"
    db.initialize(path)
    with sqlite3.connect(path) as con:
        if damage == "attempt_unique":
            con.execute("DROP INDEX idx_budget_attempt_ordinal")
        elif damage == "table_check":
            con.execute("DROP TABLE budget_operation")
            con.execute("CREATE TABLE budget_operation(operation_id TEXT PRIMARY KEY)")
        else:
            con.execute("PRAGMA writable_schema=ON")
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name='job'").fetchone()[0]
            assert "execution_generation" in sql
            con.execute("UPDATE sqlite_master SET sql=? WHERE name='job'", (sql.replace("execution_generation INTEGER NOT NULL DEFAULT 1", "execution_generation INTEGER NOT NULL DEFAULT 2"),))
            con.execute("PRAGMA writable_schema=OFF")
        con.commit()
    with pytest.raises((db.SchemaVersionError, sqlite3.DatabaseError)):
        db.connect(path)


def test_budget_install_is_ddl_only_and_job_wait_modes_are_explicit(tmp_path):
    from reno import budget_schema
    path = tmp_path / "v10.db"
    v10_database(path)
    with sqlite3.connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        budget_schema.install(con)
        assert con.in_transaction
        assert con.execute("SELECT COUNT(*) FROM job WHERE continuation_id IS NULL").fetchone()[0] == 2
        for values in (("until", None, "daily_limit"), ("blocked", 1, "indeterminate"), ("none", 1, None)):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("UPDATE job SET wait_mode=?,not_before=?,blocked_reason=?", values)
        con.execute("UPDATE job SET wait_mode='blocked',not_before=NULL,blocked_reason='indeterminate'")
        con.execute("UPDATE job SET wait_mode='until',not_before=123,blocked_reason='daily_limit'")
        con.rollback()
    with sqlite3.connect(path) as con:
        assert "continuation_id" not in {row[1] for row in con.execute("PRAGMA table_info(job)")}


def test_live14_wire_core_hash_unchanged_and_archive_stays_7_9(tmp_path, monkeypatch):
    from reno import budget_schema, sync_checkpoints
    path = tmp_path / "v10.db"
    v10_database(path)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_VERSION", 10)
        with sqlite3.connect(path) as con:
            before = sync_protocol.schema_handshake(con)
    db.migrate(path, profile=migration_profile(path))
    con = db.connect(path)
    try:
        after = sync_protocol.schema_handshake(con)
        assert before["core_schema_sha256"] == after["core_schema_sha256"]
        assert (before["migration_version"], after["migration_version"]) == (10, migrations.SCHEMA_VERSION)
        with pytest.raises(sync_protocol.SyncError):
            sync_protocol.validate_handshake(before, after)
        assert not set(budget_schema.TABLES) & set(sync_protocol.TABLES)
        assert sync_checkpoints.ARCHIVE_SCHEMA_VERSION == 9
        assert set(sync_checkpoints.SUPPORTED_SCHEMA_VERSIONS) == {7, 9}
    finally:
        con.close()


@pytest.mark.parametrize("version", [10, 11, 12, 13, 14, 15])
def test_schema_ge10_never_treats_missing_pins_as_legacy(tmp_path, version):
    from reno import storage_root_inputs as inputs
    path = tmp_path / "library.db"
    if version == 10:
        v10_database(path)
    elif version == 11:
        from tests.test_budget_sources import legacy_v11_database
        legacy_v11_database(path)
    elif version == 12:
        from tests.test_budget_source_v13 import genuine_v12_database
        genuine_v12_database(path)
    elif version == 13:
        from tests.test_budget_resolution_v14 import freeze_v13_database
        db.initialize(path)
        freeze_v13_database(path)
    else:
        db.initialize(path)
    roots = {"sync_media": "a" * 64, "local_report": "b" * 64,
             "sync_report": "b" * 64, "checkpoint": "c" * 64}
    with sqlite3.connect(path) as con:
        if version == 14:
            con.execute("DROP TABLE sync_queue_source_receipt")
            con.execute("UPDATE meta SET value='14' WHERE key='migration_version'")
        con.execute("DROP TABLE sync_upload_pin")
        con.commit()
        result = inputs.parse_image(con, profile=migration_profile(path), root_ids=roots, member_id="budget-test")
        assert any(item.locator == inputs._digest("sync_upload_pin") and item.namespace == "sync_media" for item in result.blockers)


def test_live14_owner_gate_accepts_actual_profile_and_budget_wait_retains_video(libraries):
    from reno import storage_roots
    env = libraries("budget-owner")
    enroll(env)
    path, item = registered(env, video="BV1Budget")
    env["con"].execute("INSERT INTO video_asset(video_id,files_json,content_sha256) VALUES('BV1Budget','{}',?)", (item["sha256"],))
    env["con"].execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES('BV1Budget','process','pending','{}')")
    env["con"].commit()
    before = scan(env)
    assert not before.effective_inputs.blockers
    assert next(file for file in before.files if file["artifact_id"] == item["artifact_id"])["status"] == "referenced"
    env["con"].execute("UPDATE job SET wait_mode='blocked',blocked_reason='indeterminate',manual_paused=1")
    env["con"].commit()
    after = scan(env)
    assert after.effective_inputs.digest == before.effective_inputs.digest
    assert next(file for file in after.files if file["artifact_id"] == item["artifact_id"])["status"] == "referenced"
    assert path.read_bytes() == b"unused media"
    with pytest.raises(storage_roots.RootError) as caught:
        storage_roots._version([
            {"key": "migration_version", "value": "10"}, {"key": "library_id", "value": env["library_id"]},
            {"key": "library_epoch", "value": "1"}, {"key": "kb_revision", "value": "0"}], env["library_id"])
    assert caught.value.code == "ROOTS_SCHEMA_UNSUPPORTED"


def test_live14_retirement_proof_still_requires_p05_operational_tables(tmp_path):
    from reno import storage_gc_contract as contract
    from tests.test_storage_gc_contract import seed, proof
    path = tmp_path / "proof.db"
    db.initialize(path)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        value = {"con": con, "path": path, "library_id": db.knowledge_version(con)["library_id"],
                 "member_id": "mem_" + "a" * 64,
                 "root_id": contract.digest({"root_device": "1", "root_inode": "2"})}
        seed(value)
        assert proof(value).status == "proven"
        con.execute("DROP TABLE sync_checkpoint_retirement")
        con.commit()
        assert proof(value).status == "unproven"
    finally:
        con.close()
