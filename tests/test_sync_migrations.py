"""Explicit sync v5 upgrades against genuine historical DDL, isolated on disk."""
import hashlib
import json
import re
import sqlite3
import uuid

import pytest

from tests.migration_helpers import initialize_migration_storage, migration_profile, migration_notes

from reno import db, migrations, personal_schema, sync_protocol, sync_schema


OLD_QUEUE_SQL = """CREATE TABLE IF NOT EXISTS sync_queue (
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  status TEXT DEFAULT 'queued',
  attempts INTEGER DEFAULT 0,
  lease_until TEXT,
  last_error_class TEXT,
  created_at TEXT,
  updated_at TEXT
);"""
STAMP = "2026-09-29T09:00:00.000Z"


def historical_database(path, version=4):
    """Build old tables directly, never initialize current schema then relabel.

    v0 lacks review/knowledge enrichments, triggers and queue ownership; v1 has
    review identities but old jobs; v2 has durable jobs but no personal tables;
    v3 has personal data but no explicit card space/stage. All use the actual
    eight-column pre-v5 sync queue, with no pair/session/publication/binding tables.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = migrations.SCHEMA_PATH.read_text(encoding="utf-8")
    schema = schema.replace(",\n  derivation_json TEXT", "")
    schema = schema.replace("  OR OLD.derivation_json IS NOT NEW.derivation_json\n", "")
    schema = schema.replace(",\n  sync_state TEXT NOT NULL DEFAULT 'ready'", "")
    schema, count = re.subn(r"CREATE TABLE IF NOT EXISTS sync_queue \([\s\S]*?\n\);",
                            lambda _match: OLD_QUEUE_SQL, schema, count=1)
    assert count == 1
    if version == 0:
        schema = schema.split("-- Knowledge revision", 1)[0]
        schema = schema.replace("CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_event ON user_decision(event_id);", "")
    elif version == 1:
        schema = schema.split("-- Task dispatch", 1)[0]
    if version <= 1:
        removed = migrations.ADDITIONS if version == 0 else {"job": migrations.ADDITIONS["job"]}
        for table, additions in removed.items():
            start = schema.index("CREATE TABLE IF NOT EXISTS " + table + " (")
            end = schema.index("\n);", start)
            declaration = schema[start:end]
            for column, datatype in additions.items():
                declaration = declaration.replace(",\n  " + column + " " + datatype, "")
            schema = schema[:start] + declaration + schema[end:]
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executescript(schema)
        if version:
            con.executemany("INSERT INTO meta VALUES (?,?)", [
                ("migration_version", str(version)), ("library_id", str(uuid.uuid4())),
                ("library_epoch", "7"), ("kb_revision", "39")])
        con.execute("INSERT INTO meta VALUES ('step:v1:atomize','old completion marker')")
        con.execute("INSERT INTO video_asset(video_id,title,content_sha256,status) "
                    "VALUES ('v1','原视频',?,'processed')", ("a" * 64,))
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim,status) VALUES('a1','v1','旧观点','rejected')")
        con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) "
                    "VALUES('asr_1','v1',0,100,'原转录证据')")
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,start_ms,end_ms,evidence_text) "
                    "VALUES('a1','v1','asr',0,100,'原证据')")
        con.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json) "
                    "VALUES('cluster1','旧主题','conflicting','[\"a1\"]')")
        con.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json) "
                    "VALUES('conflict1','contradiction','old-derived-state','{\"atoms\":[\"a1\"]}','{}')")
        con.execute("INSERT INTO frame(frame_id,video_id,pts_ms,path) "
                    "VALUES('f1','v1',0,'C:\\historical\\frame.jpg')")
        con.execute("INSERT INTO ocr_span(id,video_id,start_ms,end_ms,text) VALUES('ocr1','v1',0,100,'旧OCR')")
        con.execute("INSERT INTO visual_observation(id,video_id,frame_id,pts_ms,scene_description) "
                    "VALUES('vis1','v1','f1',0,'旧画面')")
        if version:
            con.execute("INSERT INTO user_decision(atom_id,action,note,event_id,client_id,"
                        "event_schema_version,target_snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        ("a1", "reject", "旧复核备注", str(uuid.uuid4()), "legacy", 0,
                         '{"binding_status":"legacy_unbound","atom_id":"a1"}', STAMP))
        else:
            con.execute("INSERT INTO user_decision(atom_id,action,note,created_at) VALUES('a1','reject','旧复核备注',?)",
                        (STAMP,))
        con.execute("INSERT INTO job(video_id,kind,status,attempts,error,created_at,updated_at) "
                    "VALUES('v1','process','cancelled',2,'已保留旧错误',?,?)", (STAMP, STAMP))
        for i, state in enumerate(("queued", "claimed", "done", "retry_wait", "dead"), 1):
            con.execute("INSERT INTO sync_queue(item_id,url,status,attempts,lease_until,last_error_class,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (i, f"https://b23.tv/old{i}", state, i, "legacy lease text", "old_class", STAMP, STAMP))
        if version >= 3:
            for table, columns in personal_schema.TABLES.items():
                if version == 3 and table == "decision_card":
                    columns = columns.replace("space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '',", "")
                con.execute(f"CREATE TABLE {table} ({columns})")
            for statement in personal_schema.INDEXES:
                if version == 3 and "idx_personal_card_space_stage" in statement:
                    continue
                con.execute(statement)
            personal_schema.seed(con, path.parent)
            project_id = con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone()[0]
            con.execute("INSERT INTO decision_card(card_id,project_id,title,created_at,updated_at) "
                        "VALUES('card1',?,'业主已保存决定',?,?)", (project_id, STAMP, STAMP))
            con.execute("INSERT INTO source_record(source_id,project_id,source_type,title,created_at,updated_at) "
                        "VALUES('source1',?,'manual_note','业主原资料',?,?)", (project_id, STAMP, STAMP))
            content = b"artificial attachment retained across the migration"
            attachment = path.parent / "personal-attachments" / "fixture.pdf"
            attachment.parent.mkdir()
            attachment.write_bytes(content)
            con.execute("INSERT INTO source_attachment(attachment_id,source_id,project_id,sha256,byte_size,"
                        "media_type,original_name,storage_key,state,created_at,operation_id) "
                        "VALUES('attachment1','source1',?,?,?,'application/pdf','fixture.pdf','fixture.pdf','ready',?,?)",
                        (project_id, hashlib.sha256(content).hexdigest(), len(content), STAMP, "op-attachment"))
            con.execute("INSERT INTO personal_revision VALUES('card','card1',1,?,'op-card',?)",
                        ('{"title":"业主已保存决定","revision":1}', STAMP))
            con.execute("INSERT INTO mutation_receipt VALUES('op-card','card:create','request-hash',201,?,?)",
                        ('{"card_id":"card1"}', STAMP))
        con.commit()
    finally:
        con.close()
    initialize_migration_storage(path)


def snapshot_old_columns(con):
    snapshots = {}
    for table, in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
        selected = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        snapshots[table] = (columns, con.execute(f'SELECT {selected} FROM "{table}" ORDER BY {selected}').fetchall())
    return snapshots


def assert_snapshot(con, snapshots, *, upgraded):
    for table, (columns, previous) in snapshots.items():
        selected = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        current = con.execute(f'SELECT {selected} FROM "{table}" ORDER BY {selected}').fetchall()
        if table == "meta" and upgraded:
            expected = dict(previous)
            expected["migration_version"] = str(migrations.SCHEMA_VERSION)
            assert dict(current) == expected
        else:
            assert current == previous, table


def test_v4_upgrade_preserves_every_original_column_and_personal_artifact(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    historical_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        assert len(con.execute("PRAGMA table_info(sync_queue)").fetchall()) == 8
        assert not con.execute("SELECT name FROM sqlite_master WHERE name IN "
                               "('sync_pair','sync_session','sync_published_video','sync_queue_publication')").fetchall()
    identity_path = tmp_path / "node-identity.json"
    attachment_path = tmp_path / "personal-attachments" / "fixture.pdf"
    identity, attachment = identity_path.read_bytes(), attachment_path.read_bytes()

    def unexpected_replay_or_seed(*_args, **_kwargs):
        raise AssertionError("v4 sync upgrade must not replay knowledge or seed personal data")

    from reno import review
    monkeypatch.setattr(review, "replay", unexpected_replay_or_seed)
    monkeypatch.setattr(personal_schema, "seed", unexpected_replay_or_seed)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    assert result.backup_path.is_file()
    with sqlite3.connect(result.backup_path) as backup:
        assert_snapshot(backup, before, upgraded=False)
    con = db.connect(path)
    try:
        # tuple-based comparison includes the knowledge FTS shadow tables.
        con.row_factory = None
        assert_snapshot(con, before, upgraded=True)
        assert con.execute("SELECT DISTINCT owner_generation,lease_generation FROM sync_queue").fetchall() == [(0, 0)]
        assert con.execute("SELECT COUNT(*) FROM sync_queue WHERE owner_id IS NOT NULL OR lease_token IS NOT NULL "
                           "OR retry_at IS NOT NULL OR video_id IS NOT NULL OR publication_id IS NOT NULL OR result_hash IS NOT NULL").fetchone()[0] == 0
        for table in sync_schema.TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        handshake = sync_protocol.schema_handshake(con)
        assert handshake["migration_version"] == migrations.SCHEMA_VERSION
        with sqlite3.connect(":memory:") as fresh:
            migrations._create_schema(fresh)
            fresh.execute("INSERT INTO meta VALUES('migration_version',?)", (str(migrations.SCHEMA_VERSION),))
            assert sync_protocol.schema_handshake(fresh) == handshake
    finally:
        con.close()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["from_version"] == 4 and manifest["to_version"] == migrations.SCHEMA_VERSION
    assert manifest["attachments"] == [{"storage_key": "fixture.pdf", "byte_size": len(attachment),
                                        "sha256": hashlib.sha256(attachment).hexdigest()}]
    assert identity_path.read_bytes() == identity and attachment_path.read_bytes() == attachment
    assert not db.migrate(path, profile=migration_profile(path)).changed
    assert len(migration_notes(path)) == 1


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_all_old_upgrade_paths_add_queue_columns_before_new_indexes(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    historical_database(path, version)
    with sqlite3.connect(path) as con:
        old_queue = con.execute("SELECT * FROM sync_queue ORDER BY item_id").fetchall()
        old_jobs = con.execute("SELECT job_id,video_id,kind,status,attempts,error,created_at,updated_at FROM job").fetchall()
        assert len(con.execute("PRAGMA table_info(sync_queue)").fetchall()) == 8
        for table in sync_schema.TABLES:
            assert not con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchall()
        if version < 3:
            assert not con.execute("SELECT name FROM sqlite_master WHERE name='decision_card'").fetchall()
        elif version == 3:
            assert "space" not in {row[1] for row in con.execute("PRAGMA table_info(decision_card)")}
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    con = db.connect(path)
    try:
        con.row_factory = None
        old_columns = "item_id,url,status,attempts,lease_until,last_error_class,created_at,updated_at"
        assert con.execute(f"SELECT {old_columns} FROM sync_queue ORDER BY item_id").fetchall() == old_queue
        assert con.execute("SELECT job_id,video_id,kind,status,attempts,error,created_at,updated_at FROM job").fetchall() == old_jobs
        assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sync_published_video").fetchone()[0] == 0
        sync_schema.validate(con)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
    assert not db.migrate(path, profile=migration_profile(path)).changed


@pytest.mark.parametrize("failure", ["after_ddl", "after_validation"])
def test_v5_failure_rolls_back_schema_and_rows_with_recovery_backup(tmp_path, monkeypatch, failure):
    path = tmp_path / "rollback.db"
    historical_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
    identity = (tmp_path / "node-identity.json").read_bytes()
    attachment = (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes()
    if failure == "after_ddl":
        original = sync_schema.install

        def fail(con):
            original(con)
            raise RuntimeError("controlled v5 DDL failure")

        monkeypatch.setattr(sync_schema, "install", fail)
    else:
        original = migrations.validate

        def fail(con):
            original(con)
            raise RuntimeError("controlled v5 validation failure")

        monkeypatch.setattr(migrations, "validate", fail)
    with pytest.raises(RuntimeError, match="controlled v5"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert_snapshot(con, before, upgraded=False)
        assert len(con.execute("PRAGMA table_info(sync_queue)").fetchall()) == 8
        for table in sync_schema.TABLES:
            assert not con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchall()
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = migration_notes(path)
    assert len(backups) == 1
    with sqlite3.connect(str(backups[0])[:-5]) as backup:
        assert_snapshot(backup, before, upgraded=False)
    assert (tmp_path / "node-identity.json").read_bytes() == identity
    assert (tmp_path / "personal-attachments" / "fixture.pdf").read_bytes() == attachment


def test_ordinary_open_does_not_upgrade_old_v4_database(tmp_path):
    path = tmp_path / "old.db"
    historical_database(path)
    original = path.read_bytes()
    for operation in (db.connect, db.initialize):
        with pytest.raises(db.SchemaVersionError, match="migrate-db"):
            operation(path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.bak-migrate-*"))


def test_install_is_transactional_idempotent_and_has_no_authority_side_effects(tmp_path):
    path = tmp_path / "old.db"
    historical_database(path)
    with sqlite3.connect(path) as con:
        before = snapshot_old_columns(con)
        con.execute("BEGIN IMMEDIATE")
        sync_schema.install(con)
        sync_schema.install(con)
        assert con.in_transaction
        assert_snapshot(con, before, upgraded=False)
        for table in sync_schema.TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        con.rollback()
        assert len(con.execute("PRAGMA table_info(sync_queue)").fetchall()) == 8
        for table in sync_schema.TABLES:
            assert not con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchall()


def test_new_schema_enforces_single_pair_session_parent_and_publication_identity(tmp_path):
    path = tmp_path / "new.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        pair = ("library-source", "node-source", "library-target", "node-target", 1, "a" * 64, STAMP)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_pair VALUES(2,?,?,?,?,?,?,?)", pair)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_session(slot,session_id,lease_token,generation,lease_until,updated_at) "
                        "VALUES(1,'session','token',1,?,?)", (STAMP, STAMP))
        con.execute("INSERT INTO sync_pair VALUES(1,?,?,?,?,?,?,?)", pair)
        con.execute("INSERT INTO sync_session(slot,session_id,lease_token,generation,lease_until,updated_at) "
                    "VALUES(1,'session','token',1,?,?)", (STAMP, STAMP))
        assert con.execute("SELECT metrics_json FROM sync_session").fetchone()[0] == "{}"
        values = ("pub1", "v1", "source-lib", "source-node", 1, 8, "c" * 64, STAMP)
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)", values)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)", values)
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)", ("pub1", "v2", *values[2:]))
    finally:
        con.close()


def test_queue_publication_binds_a_claim_to_existing_queue_and_video_receipt(tmp_path):
    path = tmp_path / "new.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        binding = (1, 1, "lease-token", "session-id", 7, "pub1", "v1", STAMP)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)", binding)
        con.execute("INSERT INTO sync_queue(item_id,url) VALUES(1,'https://b23.tv/a1')")
        # A queue claim alone is insufficient: the exact publication/video
        # receipt must already exist in the same eventual publish transaction.
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)", binding)
        receipt = ("pub1", "v1", "source-library", "source-node", 1, 8, "c" * 64, STAMP)
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)", receipt)
        con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)", binding)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)",
                        (1, 1, "replacement-token", *binding[3:]))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)",
                        (2, *binding[1:]))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)",
                        (1, 2, "new-token", "session-id", 7, "pub1", "wrong-video", STAMP))
        # The receipt may satisfy separately established bindings for duplicate
        # queue URLs or a later claim; uniqueness is on item + claim generation.
        con.execute("INSERT INTO sync_queue(item_id,url) VALUES(2,'https://b23.tv/a1')")
        con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)", (2, *binding[1:]))
        con.execute("INSERT INTO sync_queue_publication VALUES(?,?,?,?,?,?,?,?)",
                    (1, 2, "new-token", *binding[3:]))
        assert con.execute("SELECT COUNT(*) FROM sync_queue_publication").fetchone()[0] == 3
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


@pytest.mark.parametrize("damage", ["missing_column", "wrong_pk", "missing_index", "missing_fk",
                                     "missing_singleton", "missing_publication_fk", "wrong_binding_pk"])
def test_normal_validation_rejects_incomplete_or_weakened_sync_schema(tmp_path, damage):
    path = tmp_path / "new.db"
    db.initialize(path)
    with sqlite3.connect(path) as con:
        if damage == "missing_column":
            con.execute("ALTER TABLE sync_queue DROP COLUMN result_hash")
        elif damage == "missing_index":
            con.execute("DROP INDEX idx_syncq_claim")
        elif damage == "missing_singleton":
            con.execute("DROP TABLE sync_pair")
            con.execute("CREATE TABLE sync_pair(" + sync_schema.TABLES["sync_pair"].replace(" CHECK(slot=1)", "") + ")")
        elif damage in ("missing_publication_fk", "wrong_binding_pk"):
            con.execute("DROP TABLE sync_queue_publication")
            definition = sync_schema.TABLES["sync_queue_publication"]
            if damage == "missing_publication_fk":
                definition = definition.split(",\n        FOREIGN KEY", 1)[0]
            else:
                definition = definition.replace("PRIMARY KEY(item_id,lease_generation)", "PRIMARY KEY(item_id)")
            con.execute("CREATE TABLE sync_queue_publication(" + definition + ")")
        else:
            con.execute("DROP TABLE sync_session")
            definition = sync_schema.TABLES["sync_session"]
            if damage == "wrong_pk":
                definition = definition.replace("slot INTEGER PRIMARY KEY", "slot INTEGER")
            else:
                definition = definition.replace(" REFERENCES sync_pair(slot)", "")
            con.execute("CREATE TABLE sync_session(" + definition + ")")
    with pytest.raises(db.SchemaVersionError):
        db.connect(path)
