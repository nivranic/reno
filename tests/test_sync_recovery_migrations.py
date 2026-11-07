"""Schema 8 is additive over actual schema 7; migrations touch only fake DBs."""
import json
import sqlite3
import uuid

import pytest

from tests.migration_helpers import migration_profile

from reno import db, migrations, personal_schema, sync_checkpoint_schema, sync_recovery, sync_recovery_schema
from tests.test_sync_checkpoint_migrations import v6_database
from tests.test_sync_migrations import assert_snapshot, snapshot_old_columns


def v7_database(path):
    v6_database(path)
    with sqlite3.connect(path) as con:
        sync_checkpoint_schema.install(con)
        con.execute("UPDATE meta SET value='7' WHERE key='migration_version'")
        identifier = str(uuid.uuid4())
        con.execute("INSERT INTO sync_checkpoint VALUES(?,?,?,?,?,?,?,?,?)",
                    (identifier, "old-publication", "{}", 1, 7, identifier + ".sqlite", "f" * 64, 8192,
                     "2026-10-01T00:00:00.000Z"))


def test_additive_upgrade_preserves_every_original_column_and_identity(tmp_path, monkeypatch):
    path = tmp_path / "v7.db"
    v7_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        assert not con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sync_recovery_%'").fetchall()
    node = (tmp_path / "node-identity.json").read_bytes()
    attachment = (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Recovery migration cannot replay or seed identities")
    from reno import review
    monkeypatch.setattr(review, "replay", forbidden)
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    with sqlite3.connect(result.backup_path) as con:
        assert_snapshot(con, before, upgraded=False)
    con = db.connect(path)
    try:
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
        sync_recovery_schema.validate(con)
        for table in sync_recovery_schema.TABLES:
            assert con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        con.row_factory = sqlite3.Row
        version = db.knowledge_version(con)
        assert sync_recovery.state(con) == {"state": "running", "generation": 0, "revision": 0, "source_floor": None,
            "stream": {"library_id": version["library_id"], "library_epoch": version["library_epoch"]}}
    finally:
        con.close()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (7, migrations.SCHEMA_VERSION)
    assert (tmp_path / "node-identity.json").read_bytes() == node
    assert (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes() == attachment
    assert not db.migrate(path, profile=migration_profile(path)).changed


@pytest.mark.parametrize("failure", ["install", "validate"])
def test_failed_upgrade_rolls_back_all_new_tables_and_preserves_checkpoint_rows(tmp_path, monkeypatch, failure):
    path = tmp_path / "v7.db"
    v7_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    original = getattr(sync_recovery_schema, failure)
    def fail(con):
        original(con)
        raise sqlite3.DatabaseError("controlled recovery migration failure")
    monkeypatch.setattr(sync_recovery_schema, failure, fail)
    with pytest.raises((sqlite3.DatabaseError, db.SchemaVersionError), match="controlled recovery"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        for table in sync_recovery_schema.TABLES:
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchall()


def test_normal_open_never_installs_recovery_tables(tmp_path):
    path = tmp_path / "v7.db"
    v7_database(path)
    before = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == before and not list(tmp_path.glob("*.bak-migrate-*"))


def test_installer_is_transactional_idempotent_and_does_not_seed_hold(tmp_path):
    path = tmp_path / "v7.db"
    v7_database(path)
    with sqlite3.connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        sync_recovery_schema.install(con)
        sync_recovery_schema.install(con)
        sync_recovery_schema.validate(con)
        assert con.in_transaction
        for table in sync_recovery_schema.TABLES:
            assert con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        con.rollback()
        for table in sync_recovery_schema.TABLES:
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchall()


@pytest.mark.parametrize("damage", ["table", "state_check", "generation_check", "origin", "unique", "foreign_key", "index"])
def test_validation_rejects_weakened_recovery_contract(tmp_path, damage):
    path = tmp_path / "new.db"
    db.initialize(path)
    with sqlite3.connect(path) as con:
        if damage == "index":
            con.execute("DROP INDEX idx_sync_recovery_created")
        elif damage == "table":
            con.execute("DROP TABLE sync_publication_fence")
        else:
            table = "sync_recovery_control" if damage in {"state_check", "generation_check"} else "sync_recovery_checkpoint"
            con.execute(f"DROP TABLE {table}")
            declaration = sync_recovery_schema.TABLES[table]
            old, new = {
                "state_check": (" CHECK(state IN ('running','held','resume_pending'))", ""),
                "generation_check": (" CHECK(generation>=0)", ""),
                "origin": (" CHECK(origin_kind='recovery')", ""),
                "unique": ("operation_id TEXT NOT NULL UNIQUE", "operation_id TEXT NOT NULL"),
                "foreign_key": (" REFERENCES sync_recovery_operation(operation_id)", ""),
            }[damage]
            con.execute(f"CREATE TABLE {table} ({declaration.replace(old, new)})")
            for sql in sync_recovery_schema.INDEXES:
                con.execute(sql)
    with pytest.raises(db.SchemaVersionError):
        db.connect(path)
