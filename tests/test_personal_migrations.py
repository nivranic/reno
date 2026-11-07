"""Only artificial databases: no production file is opened or upgraded."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

import pytest

from tests.migration_helpers import initialize_migration_storage, migration_profile, migration_notes, migration_arguments

from reno import db, migrations, personal_schema


def legacy_database(path, *, global_step_keys=False, initialize_storage=True):
    """Representative pre-versioning schema, including unknown preserved data."""
    schema = migrations.SCHEMA_PATH.read_text(encoding="utf-8")
    schema = schema.replace(",\n  sync_state TEXT NOT NULL DEFAULT 'ready'", "")
    schema = schema.split("-- Knowledge revision", 1)[0]
    schema = schema.replace("CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_event ON user_decision(event_id);", "")
    for table, additions in migrations.ADDITIONS.items():
        start = schema.index("CREATE TABLE IF NOT EXISTS " + table + " (")
        end = schema.index("\n);", start)
        table_sql = schema[start:end]
        for column, datatype in additions.items():
            table_sql = table_sql.replace(",\n  " + column + " " + datatype, "")
        schema = schema[:start] + table_sql + schema[end:]
    if global_step_keys:
        schema = schema.replace("PRIMARY KEY (video_id, frame_id)", "PRIMARY KEY (frame_id)")
        schema = schema.replace("PRIMARY KEY (video_id, id)", "PRIMARY KEY (id)")
    con = sqlite3.connect(path)
    con.executescript(schema)
    con.execute("INSERT INTO meta VALUES ('step:v1:atomize','done')")
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256) VALUES ('v1','老视频','legacy-sha')")
    con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) VALUES ('asr_1','v1',10,20,'保留旧证据')")
    con.execute("INSERT INTO frame(frame_id,video_id,pts_ms,path) VALUES ('f1','v1',10,'media/keep.png')")
    con.execute("INSERT INTO ocr_span(id,video_id,start_ms,text) VALUES ('ocr1','v1',10,'旧OCR')")
    con.execute("INSERT INTO visual_observation(id,video_id,pts_ms,scene_description) VALUES ('vis1','v1',10,'旧画面')")
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json) VALUES ('a1','v1','旧主张','{\"未知键\":\"保留\"}')")
    con.execute("INSERT INTO evidence_ref(atom_id,video_id,evidence_text) VALUES ('a1','v1','旧证据')")
    con.execute("INSERT INTO user_decision(atom_id,conflict_id,action,note,created_at) VALUES ('a1','c1','confirm','双目标历史仍保留','2026-09-30T10:30:00+08:00')")
    con.execute("CREATE TABLE future_personal_history(id TEXT PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO future_personal_history VALUES ('p1','用户历史')")
    con.commit()
    con.close()
    if initialize_storage:
        initialize_migration_storage(path)


def v3_database(path):
    """Cold-create the v3 reference from test_sync_migrations, without its data.

    Only original personal initialization is seeded. Additional old knowledge
    and reviews would legitimately change kb_revision during their migration
    and obscure this test's exact personal-history/metadata preservation.
    """
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = migrations.SCHEMA_PATH.read_text(encoding="utf-8")
    schema = schema.replace(",\n  derivation_json TEXT", "")
    schema = schema.replace("  OR OLD.derivation_json IS NOT NEW.derivation_json\n", "")
    schema = schema.replace(",\n  sync_state TEXT NOT NULL DEFAULT 'ready'", "")
    old_queue_sql = """CREATE TABLE IF NOT EXISTS sync_queue (
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  status TEXT DEFAULT 'queued',
  attempts INTEGER DEFAULT 0,
  lease_until TEXT,
  last_error_class TEXT,
  created_at TEXT,
  updated_at TEXT
);"""
    schema, count = re.subn(r"CREATE TABLE IF NOT EXISTS sync_queue \([\s\S]*?\n\);",
                            lambda _match: old_queue_sql, schema, count=1)
    assert count == 1
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executescript(schema)
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("migration_version", "3"), ("library_id", str(uuid.uuid4())),
            ("library_epoch", "1"), ("kb_revision", "0")])
        for table, columns in personal_schema.TABLES.items():
            if table == "decision_card":
                classification = "space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '',"
                assert columns.count(classification) == 1
                columns = columns.replace(classification, "")
            con.execute(f"CREATE TABLE {table} ({columns})")
        for statement in personal_schema.INDEXES:
            if "idx_personal_card_space_stage" not in statement:
                con.execute(statement)
        personal_schema.seed(con, path.parent)
        con.commit()
    finally:
        con.close()
    initialize_migration_storage(path)


def test_connect_never_creates_or_upgrades(tmp_path):
    absent = tmp_path / "missing" / "new.db"
    with pytest.raises(db.SchemaVersionError, match="init-db"):
        db.connect(absent)
    assert not absent.parent.exists()
    path = tmp_path / "legacy.db"
    legacy_database(path, global_step_keys=True)
    before = path.read_bytes()
    with pytest.raises(db.SchemaVersionError, match="migrate-db"):
        db.connect(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*"))
    with pytest.raises(db.SchemaVersionError, match="migrate-db"):
        db.initialize(path)
    assert path.read_bytes() == before


def test_new_database_initialization_is_explicit_and_idempotent(tmp_path):
    path = tmp_path / "nested" / "new.db"
    assert db.initialize(path).changed
    initialize_migration_storage(path)
    with db.connect(path) as con:
        metadata = dict(con.execute("SELECT key,value FROM meta"))
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert metadata["kb_revision"] == "0"
    con.close()
    assert not db.initialize(path).changed
    assert not db.migrate(path, profile=migration_profile(path)).changed
    with db.connect(path) as con:
        assert dict(con.execute("SELECT key,value FROM meta")) == metadata
    con.close()


@pytest.mark.parametrize("old_status,action", [("rejected", "reject"), ("verified", "confirm")])
def test_unbound_legacy_review_never_keeps_an_effective_compatibility_status(tmp_path, old_status, action):
    from reno import ask, review
    path = tmp_path / "old-review.db"
    legacy_database(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE knowledge_atom SET status=? WHERE id='a1'", (old_status,))
    con.execute("UPDATE user_decision SET conflict_id=NULL,action=?", (action,))
    con.commit()
    con.close()
    db.migrate(path, profile=migration_profile(path))
    con = db.connect(path)
    try:
        assert con.execute("SELECT status FROM knowledge_atom WHERE id='a1'").fetchone()[0] == "candidate"
        assert review.projection(con)["atoms"]["a1"]["review_status"] == "needs_recheck"
        assert any(atom["id"] == "a1" for atom in ask.retrieve(con, ""))
    finally:
        con.close()


@pytest.mark.parametrize("global_step_keys", [False, True])
def test_upgrade_preserves_rows_history_steps_and_attachment_manifest(tmp_path, global_step_keys):
    path = tmp_path / "old.db"
    legacy_database(path, global_step_keys=global_step_keys)
    attachment = tmp_path / "personal-attachments" / "keep.png"
    attachment.parent.mkdir()
    attachment.write_bytes(b"existing attachment bytes")
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.backup_path.is_file()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["row_counts"]["user_decision"] == 1
    assert manifest["attachments"][0]["sha256"] == hashlib.sha256(attachment.read_bytes()).hexdigest()
    assert manifest["attachment_files_copied"] is False
    with sqlite3.connect(result.backup_path) as old:
        assert old.execute("SELECT note FROM user_decision").fetchone()[0] == "双目标历史仍保留"
        assert old.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() is None
    with db.connect(path) as con:
        assert con.execute("SELECT value FROM meta WHERE key='step:v1:atomize'").fetchone()[0] == "done"
        assert con.execute("SELECT conditions_json FROM knowledge_atom").fetchone()[0] == '{"未知键":"保留"}'
        assert con.execute("SELECT source_published_at FROM video_asset").fetchone()[0] is None
        assert con.execute("SELECT body FROM future_personal_history").fetchone()[0] == "用户历史"
        event = dict(con.execute("SELECT * FROM user_decision").fetchone())
        assert event["atom_id"] == "a1" and event["conflict_id"] == "c1"
        assert event["target_fingerprint"] is None and event["event_schema_version"] == 0
        assert json.loads(event["target_snapshot_json"])["binding_status"] == "legacy_unbound"
        assert event["decided_at"] == "2026-09-30T02:30:00.000Z"
        for table in migrations.STEP_TABLES:
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        if global_step_keys:
            assert con.execute("SELECT text FROM migration_legacy_v0_transcript_segment").fetchone()[0] == "保留旧证据"
        con.execute("INSERT INTO transcript_segment(id,video_id,text) VALUES ('asr_1','v2','可用复合键')")
    con.close()
    first = path.read_bytes()
    assert not db.migrate(path, profile=migration_profile(path)).changed
    assert path.read_bytes() == first
    with db.connect(path) as con:
        assert dict(con.execute("SELECT * FROM user_decision").fetchone()) == event
    con.close()


def test_migration_failure_rolls_back_additions_and_keeps_backup(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    legacy_database(path, global_step_keys=True)
    original = migrations.validate

    def fail_after_writes(con):
        original(con)
        raise RuntimeError("controlled post-upgrade failure")

    monkeypatch.setattr(migrations, "validate", fail_after_writes)
    with pytest.raises(RuntimeError, match="controlled"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT text FROM transcript_segment").fetchone()[0] == "保留旧证据"
        assert "event_id" not in {r[1] for r in con.execute("PRAGMA table_info(user_decision)")}
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() is None
        assert not con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'migration_legacy_%'").fetchall()
    assert len(migration_notes(path)) == 1


@pytest.mark.parametrize("fail_upgrade", [False, True])
def test_v3_classification_upgrade_preserves_personal_history_and_identity(tmp_path, monkeypatch, fail_upgrade):
    path = tmp_path / "personal-v3.db"
    v3_database(path)
    with sqlite3.connect(path) as con:
        project_id = con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone()[0]
        now = "2026-09-30T01:00:00.000Z"
        con.execute("INSERT INTO decision_card(card_id,project_id,title,context_json,created_at,updated_at) "
                    "VALUES ('historical-card',?,'已保存决定',?,?,?)",
                    (project_id, '{"空间":"卫生间","时间":"泥瓦","旧字段":"保留"}', now, now))
        snapshot = '{"card_id":"historical-card","revision":1,"context_snapshot":{"时间":"泥瓦"}}'
        con.execute("INSERT INTO personal_revision VALUES('card','historical-card',1,?,'old-operation',?)", (snapshot, now))
        con.execute("INSERT INTO mutation_receipt VALUES('old-operation','card:create','original-hash',201,?,?)", (snapshot, now))
        columns = [row[1] for row in con.execute("PRAGMA table_info(decision_card)")]
        card = con.execute("SELECT * FROM decision_card").fetchall()
        preserved = {table: con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                     for table in ("personal_revision", "mutation_receipt", "renovation_project", "meta")}
    node_identity = (tmp_path / "node-identity.json").read_bytes()
    if fail_upgrade:
        original = migrations.validate

        def reject_after_alter(con):
            original(con)
            raise RuntimeError("controlled v4 validation failure")

        monkeypatch.setattr(migrations, "validate", reject_after_alter)
        with pytest.raises(RuntimeError, match="controlled v4"):
            db.migrate(path, profile=migration_profile(path))
    else:
        result = db.migrate(path, profile=migration_profile(path))
        assert result.changed and result.version == migrations.SCHEMA_VERSION
        with sqlite3.connect(result.backup_path) as backup:
            assert backup.execute("SELECT * FROM decision_card").fetchall() == card
        assert not db.migrate(path, profile=migration_profile(path)).changed
    with sqlite3.connect(path) as con:
        assert con.execute(f"SELECT {','.join(columns)} FROM decision_card").fetchall() == card
        for table, before in preserved.items():
            after = con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            if table == "meta":
                expected = dict(before)
                expected["migration_version"] = "3" if fail_upgrade else str(migrations.SCHEMA_VERSION)
                assert dict(after) == expected
            else:
                assert after == before
        if fail_upgrade:
            assert "stage" not in {row[1] for row in con.execute("PRAGMA table_info(decision_card)")}
        else:
            assert con.execute("SELECT space,stage FROM decision_card").fetchone() == ("", "")
            assert con.execute("PRAGMA foreign_key_check").fetchall() == []
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (tmp_path / "node-identity.json").read_bytes() == node_identity


def test_future_and_incomplete_schema_fail_closed(tmp_path):
    path = tmp_path / "new.db"
    db.initialize(path)
    initialize_migration_storage(path)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE meta SET value='999' WHERE key='migration_version'")
    with pytest.raises(db.SchemaVersionError, match="Unsupported"):
        db.connect(path)
    with pytest.raises(db.SchemaVersionError, match="Unsupported"):
        db.migrate(path, profile=migration_profile(path))
    with sqlite3.connect(path) as con:
        con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(migrations.SCHEMA_VERSION),))
        con.execute("ALTER TABLE user_decision RENAME COLUMN event_id TO broken_event")
    with pytest.raises(db.SchemaVersionError, match="event_id"):
        db.connect(path)


def test_cli_migration_does_not_implicitly_upgrade(tmp_path, monkeypatch, capsys):
    from reno import cli, config
    path = tmp_path / "reno.db"
    legacy_database(path)
    monkeypatch.setattr(config, "DATA", tmp_path)
    assert cli.main(["status"]) == 2
    assert "migrate-db" in capsys.readouterr().err
    assert cli.main(["migrate-db", *migration_arguments(path)]) == 0
    assert cli.main(["status"]) == 0


def test_deploy_exclusive_import_does_not_overwrite(tmp_path, monkeypatch):
    """Run the real archive importer, including a target created after preflight."""
    import importlib.util
    import io
    import tarfile

    helper = Path(__file__).parents[1] / "deploy" / "deploy_support.py"
    spec = importlib.util.spec_from_file_location("reno_deploy_import_test", helper)
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    archive = tmp_path / "incoming.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in {"data/reno.db": b"incoming", "media/new.mp4": b"incoming media"}.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    destination, data_dir = tmp_path / "app", tmp_path / "state" / "data"
    data_dir.mkdir(parents=True)
    target = data_dir / "reno.db"
    original = b"personal history"
    target.write_bytes(original)
    with pytest.raises(ValueError, match="cannot overwrite an existing database"):
        support.extract_archive(archive, kind="data", destination=destination, data_dir=data_dir)
    assert target.read_bytes() == original
    assert not (data_dir.parent / "media" / "new.mp4").exists()

    target.unlink()
    real_open = Path.open
    created_after_preflight = []

    def create_before_exclusive_open(path, mode="r", *args, **kwargs):
        if path == target and mode == "xb":
            with real_open(target, "wb") as stream:
                stream.write(original)
            created_after_preflight.append(target)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", create_before_exclusive_open)
    with pytest.raises(FileExistsError):
        support.extract_archive(archive, kind="data", destination=destination, data_dir=data_dir)
    assert created_after_preflight == [target]
    assert target.read_bytes() == original
    assert not (data_dir.parent / "media" / "new.mp4").exists()


def test_atom_replacement_preserves_only_matching_content_and_prunes_stale_outputs(tmp_path):
    path = tmp_path / "replace.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        atom = {"id": "a1", "claim": "旧建议", "polarity": "recommend",
                "conditions": {"基层": "混凝土"}, "parameters": [],
                "dimension": "施工工艺", "evidence_nature": "author_opinion",
                "exceptions": ["旧例外"], "prices": [{"amount": 12}],
                "evidence_refs": [{"modality": "asr", "start_ms": 0, "end_ms": 100,
                                   "source_item_id": "asr1", "evidence_text": "旧建议"}]}
        db.replace_atoms(con, "v1", [atom, {**atom, "id": "removed"}])
        db.add_decision(con, atom_id="a1", action="confirm")
        original_revision = db.knowledge_version(con)["kb_revision"]
        db.replace_atoms(con, "v1", [atom, {**atom, "id": "removed"}])
        assert db.knowledge_version(con)["kb_revision"] == original_revision
        assert con.execute("SELECT status FROM knowledge_atom WHERE id='a1'").fetchone()[0] == "verified"
        without_enrichments = {key: value for key, value in atom.items()
                               if key not in {"dimension", "evidence_nature", "exceptions", "prices"}}
        db.replace_atoms(con, "v1", [without_enrichments])
        row = dict(con.execute("SELECT * FROM knowledge_atom WHERE id='a1'").fetchone())
        assert json.loads(row["exceptions"]) == ["旧例外"]
        assert row["status"] == "verified"
        assert not con.execute("SELECT * FROM evidence_ref WHERE atom_id='removed'").fetchall()
        assert not con.execute("SELECT * FROM atom_fts WHERE atom_id='removed'").fetchall()
        db.replace_atoms(con, "v1", [{**without_enrichments, "exceptions": [], "prices": []}])
        row = dict(con.execute("SELECT * FROM knowledge_atom WHERE id='a1'").fetchone())
        assert json.loads(row["exceptions"]) == [] and json.loads(row["prices_json"]) == []
        db.replace_atoms(con, "v1", [{**without_enrichments, "claim": "完全不同的内容"}])
        row = dict(con.execute("SELECT * FROM knowledge_atom WHERE id='a1'").fetchone())
        assert row["dimension"] is None and row["evidence_nature"] is None
        assert row["status"] == "candidate"
        assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
        assert db.knowledge_version(con)["kb_revision"] > original_revision
    finally:
        con.close()


def test_direct_sql_revision_changes_are_transactional_and_ignore_identical_updates(tmp_path):
    path = tmp_path / "revisions.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        con.execute("INSERT INTO knowledge_atom(id,claim) VALUES ('a1','初稿')")
        con.commit()
        base = db.knowledge_version(con)["kb_revision"]
        con.execute("UPDATE knowledge_atom SET claim='改稿' WHERE id='a1'")
        assert db.knowledge_version(con)["kb_revision"] > base
        con.rollback()
        assert db.knowledge_version(con)["kb_revision"] == base
        con.execute("UPDATE knowledge_atom SET claim=claim,created_at='diagnostic timestamp'")
        assert db.knowledge_version(con)["kb_revision"] == base
        mutations = [
            "UPDATE knowledge_atom SET status='rejected' WHERE id='a1'",
            "INSERT INTO evidence_ref(atom_id,evidence_text) VALUES ('a1','新证据')",
            "UPDATE evidence_ref SET evidence_text='改证据'",
            "DELETE FROM evidence_ref",
            "INSERT INTO knowledge_cluster(cluster_id,members_json) VALUES ('c1','[\"a1\"]')",
            "UPDATE knowledge_cluster SET relation='conflicting'",
            "DELETE FROM knowledge_cluster",
            "INSERT INTO conflict_case(conflict_id,status) VALUES ('f1','open')",
            "UPDATE conflict_case SET status='needs_review'",
            "DELETE FROM conflict_case",
            "DELETE FROM knowledge_atom",
        ]
        for statement in mutations:
            before = db.knowledge_version(con)["kb_revision"]
            con.execute(statement)
            assert db.knowledge_version(con)["kb_revision"] > before
        for table in ("knowledge_atom", "evidence_ref", "knowledge_cluster", "conflict_case"):
            before = db.knowledge_version(con)["kb_revision"]
            con.execute(f"DELETE FROM {table} WHERE 0")
            assert db.knowledge_version(con)["kb_revision"] == before
    finally:
        con.close()
