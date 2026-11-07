"""Schema 9 upgrades genuine pre-binding DDL without rewriting historical data."""
import json
import sqlite3

import pytest

from tests.migration_helpers import migration_profile

from reno import db, derivation, migrations, personal_schema, review, sync_recovery_schema
from tests.test_sync_migrations import assert_snapshot, snapshot_old_columns
from tests.test_sync_recovery_migrations import v7_database


def v8_database(path):
    v7_database(path)
    with sqlite3.connect(path) as con:
        sync_recovery_schema.install(con)
        con.execute("UPDATE meta SET value='8' WHERE key='migration_version'")
        for table in ("knowledge_cluster", "conflict_case"):
            assert "derivation_json" not in {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_real_v8_upgrade_preserves_every_column_status_identity_and_private_attachment(tmp_path, monkeypatch):
    path = tmp_path / "v8.db"
    v8_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        version = dict(con.execute("SELECT key,value FROM meta WHERE key IN ('library_id','library_epoch','kb_revision')"))
    node = (tmp_path / "node-identity.json").read_bytes()
    attachment = (tmp_path / "personal-attachments/fixture.pdf").read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("Binding migration cannot fabricate provenance, replay reviews, or change identities")
    monkeypatch.setattr(review, "replay", forbidden)
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(derivation, "bind_inputs", forbidden)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    with sqlite3.connect(result.backup_path) as con:
        assert_snapshot(con, before, upgraded=False)
        for table in ("knowledge_cluster", "conflict_case"):
            assert "derivation_json" not in {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    con = db.connect(path)
    try:
        assert db.knowledge_version(con) == {"library_id": version["library_id"],
            "library_epoch": int(version["library_epoch"]), "kb_revision": int(version["kb_revision"])}
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
        con.row_factory = sqlite3.Row
        for table in ("knowledge_cluster", "conflict_case"):
            assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE derivation_json IS NOT NULL").fetchone()[0] == 0
        assert all(dto["state"] == "unknown" for rows in derivation.evaluate_relations(con).values() for dto in rows.values())
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
    assert (tmp_path / "node-identity.json").read_bytes() == node
    assert (tmp_path / "personal-attachments/fixture.pdf").read_bytes() == attachment
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (8, migrations.SCHEMA_VERSION)
    assert not db.migrate(path, profile=migration_profile(path)).changed


def test_failed_binding_upgrade_rolls_back_columns_triggers_and_all_original_rows(tmp_path, monkeypatch):
    path = tmp_path / "v8.db"
    v8_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        triggers = con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    original = migrations.validate
    def fail(con):
        original(con)
        raise db.SchemaVersionError("synthetic validation failure after binding DDL")
    monkeypatch.setattr(migrations, "validate", fail)
    with pytest.raises(db.SchemaVersionError, match="synthetic"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == triggers
        for table in ("knowledge_cluster", "conflict_case"):
            assert "derivation_json" not in {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_normal_open_and_initialize_never_upgrade_v8_implicitly(tmp_path):
    path = tmp_path / "v8.db"
    v8_database(path)
    before = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == before and not list(tmp_path.glob("*.bak-migrate-*"))


def test_upgraded_revision_triggers_include_binding_only_changes(tmp_path):
    path = tmp_path / "v8.db"
    v8_database(path)
    db.migrate(path, profile=migration_profile(path))
    con = db.connect(path)
    try:
        for table in ("knowledge_cluster", "conflict_case"):
            before = db.knowledge_version(con)["kb_revision"]
            con.execute(f"UPDATE {table} SET derivation_json=?", ('{"version":1,"inputs":[{"atom_id":"a1","fingerprint":"v1:' + "0" * 64 + '"}]}',))
            assert db.knowledge_version(con)["kb_revision"] == before + 1
            con.commit()
            before = db.knowledge_version(con)["kb_revision"]
            con.execute(f"UPDATE {table} SET derivation_json=derivation_json")
            assert db.knowledge_version(con)["kb_revision"] == before
            con.commit()
    finally:
        con.close()
