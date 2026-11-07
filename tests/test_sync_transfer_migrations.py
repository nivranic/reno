"""Current migration preserves genuine v5 data; all databases are artificial."""
import hashlib
import json
import re
import sqlite3
import uuid

import pytest

from tests.migration_helpers import migration_profile, migration_notes

from reno import db, migrations, personal_schema, sync_protocol, sync_queue, sync_schema, sync_transfer_schema
from reno import home_recovery_schema, sync_source_readmit_schema2
from tests.test_sync_migrations import STAMP, assert_snapshot, historical_database, snapshot_old_columns


def v5_database(path):
    """Build actual pre-readiness DDL plus populated v5 ownership records."""
    historical_database(path, version=4)
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        sync_schema.install(con)
        # The current installer also knows schema15. Reconstruct the actual
        # old ownership schema instead of leaving future operational objects.
        assert con.execute("SELECT COUNT(*) FROM sync_queue_budget_receipt").fetchone()[0] == 0
        con.execute("DROP TABLE sync_queue_budget_receipt")
        con.execute("DROP TABLE sync_queue_source_receipt")
        for name in ("budget_wait_reason", "budget_operation_id"):
            con.execute(f"ALTER TABLE sync_queue DROP COLUMN {name}")
        con.execute("UPDATE meta SET value='5' WHERE key='migration_version'")
        assert "sync_state" not in {row[1] for row in con.execute("PRAGMA table_info(video_asset)")}
        # Reconstruct the released v5 wire rules, without changing SQL to fake
        # an old version of the current schema.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(migrations, "SCHEMA_VERSION", 5)
            mp.setattr(sync_protocol, "_LOCAL_COLUMNS", {
                **sync_protocol._LOCAL_COLUMNS, "video_asset": {"files_json": "TEXT"}})
            mp.setattr(sync_protocol, "TABLES", {name: {**spec,
                "columns": tuple(column for column in spec["columns"] if column != "derivation_json")}
                for name, spec in sync_protocol.TABLES.items()})
            handshake = sync_protocol.schema_handshake(con)
        source = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4()), "handshake": handshake}
        target_library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        target_node = personal_schema.node_identity(path.parent)
        con.execute("INSERT INTO sync_pair VALUES(1,?,?,?,?,?,?,?)",
                    (source["library_id"], source["node_id"], target_library, target_node,
                     handshake["protocol_version"], handshake["core_schema_sha256"], STAMP))
        session_id, lease_token = str(uuid.uuid4()), str(uuid.uuid4())
        con.execute("INSERT INTO sync_session VALUES(1,?,?,?,?,?,?)",
                    (session_id, lease_token, 3, "2099-09-30T00:00:00.000Z", STAMP,
                     '{"processed":7}'))
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)",
                    ("old-publication", "v1", source["library_id"], source["node_id"], 4, 38, "b" * 64, STAMP))
        con.execute("UPDATE sync_queue SET owner_id=?,owner_generation=3,lease_token=?,lease_generation=2,"
                    "video_id='v1',publication_id='old-publication',result_hash=? WHERE item_id=3",
                    (session_id, lease_token, "c" * 64))
        con.execute("INSERT INTO sync_queue_publication VALUES(3,2,?,?,3,'old-publication','v1',?)",
                    (lease_token, session_id, STAMP))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    return source


def test_v5_upgrade_preserves_old_columns_private_artifacts_and_peer_binding(tmp_path, monkeypatch):
    path = tmp_path / "v5.db"
    source = v5_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        old_triggers = con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    identity_path = tmp_path / "node-identity.json"
    attachment_path = tmp_path / "personal-attachments" / "fixture.pdf"
    identity, attachment = identity_path.read_bytes(), attachment_path.read_bytes()

    def no_replay_or_seed(*_args, **_kwargs):
        raise AssertionError("Schema 6 must not rewrite review states, seed private data, or rebind a peer")

    from reno import review
    monkeypatch.setattr(review, "replay", no_replay_or_seed)
    monkeypatch.setattr(personal_schema, "seed", no_replay_or_seed)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    with sqlite3.connect(result.backup_path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert "sync_state" not in {row[1] for row in con.execute("PRAGMA table_info(video_asset)")}
    con = db.connect(path)
    try:
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
        assert con.execute("SELECT video_id,sync_state FROM video_asset").fetchall() == [("v1", "ready")]
        triggers = con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
        legacy_names = {name for name, _sql in old_triggers}
        assert [(name, sql.replace("  OR OLD.derivation_json IS NOT NEW.derivation_json\n", ""))
                for name, sql in triggers if name in legacy_names] == old_triggers
        # Current17 legitimately adds exactly the released recovery/readmit
        # triggers. Unknown extra names or altered old SQL must still fail.
        current_trigger_names = {
            re.search(r"CREATE TRIGGER(?: IF NOT EXISTS)? (\w+)", statement).group(1)
            for statement in home_recovery_schema.TRIGGERS + sync_source_readmit_schema2.TRIGGERS
        }
        assert len(current_trigger_names) == 10
        added_triggers = [(name, sql) for name, sql in triggers if name not in legacy_names]
        assert {name for name, _sql in added_triggers} == current_trigger_names
        for table in sync_transfer_schema.TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        handshake = sync_protocol.schema_handshake(con)
        assert handshake["migration_version"] == migrations.SCHEMA_VERSION
        assert handshake["core_schema_sha256"] != source["handshake"]["core_schema_sha256"]
        assert "sync_state" not in sync_protocol.TABLES["video_asset"]["columns"]
        with sqlite3.connect(":memory:") as fresh:
            migrations._create_schema(fresh, readmit_variant=migrations._current_readmit_variant())
            fresh.execute("INSERT INTO meta VALUES('migration_version',?)", (str(migrations.SCHEMA_VERSION),))
            assert sync_protocol.schema_handshake(fresh) == handshake
            reference_triggers = fresh.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
            assert added_triggers == [(name, sql) for name, sql in reference_triggers
                                      if name in current_trigger_names]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        con.row_factory = sqlite3.Row
        with pytest.raises(sync_protocol.SyncError) as rejected:
            sync_queue.bind_peer(con, {**source, "handshake": handshake})
        assert rejected.value.code == "INCOMPATIBLE_SYNC_SCHEMA"
        assert "maintenance" in str(rejected.value)
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
    finally:
        con.close()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (5, migrations.SCHEMA_VERSION)
    assert manifest["attachments"] == [{"storage_key": "fixture.pdf", "byte_size": len(attachment),
                                        "sha256": hashlib.sha256(attachment).hexdigest()}]
    assert identity_path.read_bytes() == identity
    assert attachment_path.read_bytes() == attachment
    assert not db.migrate(path, profile=migration_profile(path)).changed
    assert len(migration_notes(path)) == 1


@pytest.mark.parametrize("failure", ["after_ddl", "after_validation"])
def test_failed_v6_upgrade_rolls_back_columns_tables_metadata_and_old_bindings(tmp_path, monkeypatch, failure):
    path = tmp_path / "rollback.db"
    v5_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    identity = (tmp_path / "node-identity.json").read_bytes()
    attachment = (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes()
    owner, attribute = (sync_transfer_schema, "install") if failure == "after_ddl" else (migrations, "validate")
    original = getattr(owner, attribute)

    def fail(con):
        original(con)
        raise RuntimeError("controlled schema 6 failure")

    monkeypatch.setattr(owner, attribute, fail)
    with pytest.raises(RuntimeError, match="controlled schema 6"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert "sync_state" not in {row[1] for row in con.execute("PRAGMA table_info(video_asset)")}
        for table in sync_transfer_schema.TABLES:
            assert con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = migration_notes(path)
    assert len(backups) == 1
    with sqlite3.connect(str(backups[0])[:-5]) as con:
        assert_snapshot(con, before, upgraded=False)
    assert (tmp_path / "node-identity.json").read_bytes() == identity
    assert (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes() == attachment


def test_ordinary_open_never_upgrades_v5_or_rebinds_peer(tmp_path):
    path = tmp_path / "v5.db"
    v5_database(path)
    before = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


def test_transfer_installer_is_additive_transactional_and_idempotent(tmp_path):
    path = tmp_path / "v5.db"
    v5_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        con.execute("BEGIN IMMEDIATE")
        sync_transfer_schema.install(con)
        sync_transfer_schema.install(con)
        assert con.in_transaction
        assert_snapshot(con, before, upgraded=False)
        sync_transfer_schema.validate(con)
        for table in sync_transfer_schema.TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        con.rollback()
        assert_snapshot(con, before, upgraded=False)
        assert "sync_state" not in {row[1] for row in con.execute("PRAGMA table_info(video_asset)")}
        for table in sync_transfer_schema.TABLES:
            assert not con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchall()


def test_publication_and_report_schema_enforce_identity_references_and_required_hashes(tmp_path):
    path = tmp_path / "new.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        report = ("summary.md", "pub1", "reports/pub1/summary.md", "a" * 64, 10, "v1", "{}", "{}", STAMP, "d" * 64)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_report VALUES(?,?,?,?,?,?,?,?,?,?)", report)
        publication = ("pub1", "source-library", "source-node", 1, 8, "b" * 64, "c" * 64, STAMP, "{}")
        con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", publication)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", publication)
        con.execute("INSERT INTO sync_report VALUES(?,?,?,?,?,?,?,?,?,?)", report)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_report VALUES(?,?,?,?,?,?,?,?,?,?)", report)
        for statement in ("UPDATE sync_publication SET knowledge_sha256=NULL", "UPDATE sync_report SET review_events_sha256=NULL",
                          "DELETE FROM sync_publication WHERE publication_id='pub1'"):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(statement)
        # One source version may produce another report publication; the
        # application, not an overbroad unique index, checks knowledge equality.
        con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", ("pub2", *publication[1:]))
        con.execute("INSERT INTO video_asset(video_id) VALUES('local1')")
        assert con.execute("SELECT sync_state FROM video_asset").fetchone()[0] == "ready"
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE video_asset SET sync_state=NULL")
        assert [row[2] for row in con.execute("PRAGMA index_info(idx_sync_publication_source)")] == [
            "source_library_id", "source_node_id", "source_epoch", "source_revision"]
    finally:
        con.close()


@pytest.mark.parametrize("damage", [
    "missing_readiness", "wrong_readiness_type", "nullable_readiness", "wrong_readiness_default",
    "missing_report_hash", "wrong_report_type", "nullable_report_hash", "wrong_report_pk", "missing_report_fk",
    "missing_knowledge_hash", "wrong_publication_pk", "missing_index", "wrong_index", "partial_index", "unique_index",
])
def test_validate_rejects_weakened_transfer_contract(tmp_path, damage):
    path = tmp_path / "new.db"
    db.initialize(path)
    with sqlite3.connect(path) as con:
        if damage.endswith("readiness") or damage.startswith("wrong_readiness"):
            con.execute("ALTER TABLE video_asset DROP COLUMN sync_state")
            declaration = {
                "missing_readiness": None, "wrong_readiness_type": "INTEGER NOT NULL DEFAULT 'ready'",
                "nullable_readiness": "TEXT DEFAULT 'ready'", "wrong_readiness_default": "TEXT NOT NULL DEFAULT 'staging'",
            }[damage]
            if declaration:
                con.execute("ALTER TABLE video_asset ADD COLUMN sync_state " + declaration)
        elif damage.endswith("index"):
            con.execute("DROP INDEX idx_sync_publication_source")
            if damage == "wrong_index":
                con.execute("CREATE INDEX idx_sync_publication_source ON sync_publication(source_revision)")
            elif damage != "missing_index":
                statement = sync_transfer_schema.INDEXES[0]
                statement = (statement + " WHERE source_epoch > 1") if damage == "partial_index" else statement.replace("CREATE INDEX", "CREATE UNIQUE INDEX")
                con.execute(statement)
        else:
            table = "sync_publication" if damage in ("missing_knowledge_hash", "wrong_publication_pk") else "sync_report"
            con.execute(f"DROP TABLE {table}")
            declaration = sync_transfer_schema.TABLES[table]
            if damage == "missing_report_hash":
                declaration = declaration.replace(",\n        review_events_sha256 TEXT NOT NULL", "")
            elif damage == "wrong_report_type":
                declaration = declaration.replace("byte_size INTEGER", "byte_size TEXT")
            elif damage == "nullable_report_hash":
                declaration = declaration.replace("review_events_sha256 TEXT NOT NULL", "review_events_sha256 TEXT")
            elif damage == "missing_report_fk":
                declaration = declaration.replace(" REFERENCES sync_publication(publication_id)", "")
            elif damage == "missing_knowledge_hash":
                declaration = declaration.replace(", knowledge_sha256 TEXT NOT NULL", "")
            else:
                declaration = declaration.replace(" PRIMARY KEY", "")
            con.execute(f"CREATE TABLE {table} ({declaration})")
            if table == "sync_publication":
                for statement in sync_transfer_schema.INDEXES:
                    con.execute(statement)
    with pytest.raises(db.SchemaVersionError):
        db.connect(path)


def test_schema6_does_not_require_new_live_process_triggers():
    # A schema 5 process reads these names dynamically from schema.sql. An
    # additive migration must not make that process fail its normal validation.
    source = migrations.SCHEMA_PATH.read_text(encoding="utf-8")
    assert not any("sync" in name for name in re.findall(r"CREATE TRIGGER IF NOT EXISTS (\w+)", source))
    assert all("TRIGGER" not in statement for statement in sync_transfer_schema.INDEXES)
