"""Schema 7 upgrades from genuine v6 DDL, with preserved private/sync state."""
import json
import sqlite3

import pytest

from tests.migration_helpers import migration_profile, migration_notes

from reno import db, migrations, personal_schema, sync_checkpoint_schema, sync_transfer_schema
from tests.test_sync_migrations import assert_snapshot, snapshot_old_columns
from tests.test_sync_transfer_migrations import v5_database


def v6_database(path):
    v5_database(path)
    with sqlite3.connect(path) as con:
        sync_transfer_schema.install(con)
        con.execute("UPDATE meta SET value='6' WHERE key='migration_version'")
        con.execute("INSERT INTO sync_publication VALUES('old-publication','source-library','source-node',1,7,?,?,?,?)",
                    ("a" * 64, "b" * 64, "2026-10-01T00:00:00.000Z", "{}"))
        con.execute("INSERT INTO sync_report VALUES('checklist','old-publication','immutable-old-report',?,7,'v1','{}','{}',?,?)",
                    ("c" * 64, "2026-10-01T00:00:00.000Z", "d" * 64))


def test_upgrade_is_additive_preserves_all_old_rows_files_and_peer_identity(tmp_path, monkeypatch):
    path = tmp_path / "v6.db"
    v6_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='sync_checkpoint'").fetchall()
    identity = (tmp_path / "node-identity.json").read_bytes()
    attachment = (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Checkpoint migration cannot seed or replay data")
    from reno import review
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(review, "replay", forbidden)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    with sqlite3.connect(result.backup_path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='sync_checkpoint'").fetchall()
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=True)
        sync_checkpoint_schema.validate(con)
        assert con.execute("SELECT COUNT(*) FROM sync_checkpoint").fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (6, migrations.SCHEMA_VERSION)
    assert (tmp_path / "node-identity.json").read_bytes() == identity
    assert (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes() == attachment
    assert not (tmp_path / "sync-checkpoints").exists()
    assert not db.migrate(path, profile=migration_profile(path)).changed


@pytest.mark.parametrize("failure", ["install", "validate"])
def test_failure_rolls_back_entire_schema7_change(tmp_path, monkeypatch, failure):
    path = tmp_path / "old.db"
    v6_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    original = getattr(sync_checkpoint_schema, failure)
    def fail(con):
        original(con)
        raise sqlite3.DatabaseError("controlled checkpoint migration failure")
    monkeypatch.setattr(sync_checkpoint_schema, failure, fail)
    with pytest.raises((sqlite3.DatabaseError, db.SchemaVersionError), match="controlled checkpoint"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='sync_checkpoint'").fetchall()
    assert len(migration_notes(path)) == 1


def test_ordinary_connect_and_initialize_never_upgrade_v6(tmp_path):
    path = tmp_path / "v6.db"
    v6_database(path)
    before = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


def test_installer_is_transactional_empty_and_idempotent(tmp_path):
    path = tmp_path / "v6.db"
    v6_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        con.execute("BEGIN IMMEDIATE")
        sync_checkpoint_schema.install(con)
        sync_checkpoint_schema.install(con)
        sync_checkpoint_schema.validate(con)
        assert con.in_transaction
        assert_snapshot(con, before, upgraded=False)
        assert con.execute("SELECT COUNT(*) FROM sync_checkpoint").fetchone()[0] == 0
        con.rollback()
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='sync_checkpoint'").fetchall()


@pytest.mark.parametrize("damage", ["missing", "nullable", "wrong_type", "no_publication_unique", "no_storage_unique", "no_fk", "no_pk", "index"])
def test_ordinary_validation_rejects_weakened_checkpoint_contract(tmp_path, damage):
    path = tmp_path / "fresh.db"
    db.initialize(path)
    with sqlite3.connect(path) as con:
        if damage == "index":
            con.execute("DROP INDEX idx_sync_checkpoint_created")
        else:
            con.execute("DROP TABLE sync_checkpoint")
            declaration = sync_checkpoint_schema.TABLES["sync_checkpoint"]
            if damage == "missing":
                declaration = declaration.replace("sha256 TEXT NOT NULL,", "")
            elif damage == "nullable":
                declaration = declaration.replace("sha256 TEXT NOT NULL", "sha256 TEXT")
            elif damage == "wrong_type":
                declaration = declaration.replace("byte_size INTEGER", "byte_size TEXT")
            elif damage == "no_publication_unique":
                declaration = declaration.replace("publication_id TEXT NOT NULL UNIQUE", "publication_id TEXT NOT NULL")
            elif damage == "no_storage_unique":
                declaration = declaration.replace("storage_key TEXT NOT NULL UNIQUE", "storage_key TEXT NOT NULL")
            elif damage == "no_fk":
                declaration = declaration.replace(" REFERENCES sync_publication(publication_id)", "")
            elif damage == "no_pk":
                declaration = declaration.replace(" PRIMARY KEY", "")
            con.execute(f"CREATE TABLE sync_checkpoint ({declaration})")
            for sql in sync_checkpoint_schema.INDEXES:
                con.execute(sql)
    with pytest.raises(db.SchemaVersionError):
        db.connect(path)
