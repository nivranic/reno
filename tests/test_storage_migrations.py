"""Schema 10 adds empty operational records, preserving genuine schema 9 data."""
import json
import re
import sqlite3

import pytest

from tests.migration_helpers import migration_profile

from reno import db, derivation, migrations, personal_schema, review, storage_schema, sync_protocol, storage_backups, storage_lifecycle
from tests.test_derivation_migrations import v8_database
from tests.test_sync_migrations import assert_snapshot, snapshot_old_columns


def v9_database(path):
    v8_database(path)
    with sqlite3.connect(path) as con:
        for table in ("knowledge_cluster", "conflict_case"):
            con.execute(f"ALTER TABLE {table} ADD COLUMN derivation_json TEXT")
        for name in ("kb_cluster_update", "kb_conflict_update"):
            con.execute(f"DROP TRIGGER IF EXISTS {name}")
        for statement in migrations._statements():
            if re.search(r"CREATE TRIGGER IF NOT EXISTS kb_(?:cluster|conflict)_update\b", statement):
                con.execute(statement)
        con.execute("UPDATE meta SET value='9' WHERE key='migration_version'")
        assert not set(storage_schema.TABLES) & migrations._tables(con)


def test_v9_upgrade_preserves_original_data_identity_and_attachment(tmp_path, monkeypatch):
    path = tmp_path / "v9.db"
    v9_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    files = {name: (tmp_path / name).read_bytes()
             for name in ("node-identity.json", "personal-attachments/fixture.pdf")}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Storage migration cannot replay reviews, fabricate bindings or seed identities")

    monkeypatch.setattr(review, "replay", forbidden)
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(derivation, "bind_inputs", forbidden)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    with sqlite3.connect(result.backup_path) as backup:
        assert_snapshot(backup, before, upgraded=False)
        assert not set(storage_schema.TABLES) & migrations._tables(backup)
    con = db.connect(path)
    try:
        storage_schema.validate(con)
        assert all(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0 for name in storage_schema.TABLES)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
    finally:
        con.close()
    assert all((tmp_path / name).read_bytes() == data for name, data in files.items())
    assert {item.name for item in tmp_path.rglob("*") if item.name.startswith(".reno-storage")} == {
        storage_lifecycle.LOCK_NAME, storage_backups.CATALOG}
    assert (path.parent / storage_backups.CATALOG).is_dir()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (9, migrations.SCHEMA_VERSION)
    assert not db.migrate(path, profile=migration_profile(path)).changed


def test_storage_ddl_failure_rolls_back_version_tables_and_original_rows(tmp_path, monkeypatch):
    path = tmp_path / "v9.db"
    v9_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    original = storage_schema.install

    def fail(con):
        original(con)
        raise sqlite3.DatabaseError("synthetic failure after storage DDL")

    monkeypatch.setattr(storage_schema, "install", fail)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert not set(storage_schema.TABLES) & migrations._tables(con)


def test_existing_incompatible_storage_table_is_not_silently_repaired(tmp_path):
    path = tmp_path / "v9.db"
    v9_database(path)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE storage_artifact(artifact_id TEXT PRIMARY KEY)")
        con.execute("INSERT INTO storage_artifact VALUES('keep')")
        before = snapshot_old_columns(con)
    with pytest.raises((db.SchemaVersionError, sqlite3.DatabaseError)):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert set(storage_schema.TABLES) & migrations._tables(con) == {"storage_artifact"}


def test_open_and_initialize_do_not_upgrade_v9_implicitly(tmp_path):
    path = tmp_path / "v9.db"
    v9_database(path)
    before = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


def test_core_wire_hash_stays_same_but_old_live_schema_handshake_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "v9.db"
    v9_database(path)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_VERSION", 9)
        with sqlite3.connect(path) as con:
            before = sync_protocol.schema_handshake(con)
    db.migrate(path, profile=migration_profile(path))
    con = db.connect(path)
    try:
        after = sync_protocol.schema_handshake(con)
        assert before["core_schema_sha256"] == after["core_schema_sha256"]
        assert (before["migration_version"], after["migration_version"]) == (9, migrations.SCHEMA_VERSION)
        with pytest.raises(sync_protocol.SyncError, match="does not match"):
            sync_protocol.validate_handshake(before, after)
        assert not set(storage_schema.TABLES) & set(sync_protocol.TABLES)
        from reno import sync_checkpoints
        assert sync_checkpoints.ARCHIVE_SCHEMA_VERSION == 9
        assert set(sync_checkpoints.SUPPORTED_SCHEMA_VERSIONS) == {7, 9}
    finally:
        con.close()
