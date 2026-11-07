"""Copied-fixture backup/restore rehearsal: history, receipts, files and node ID."""
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from reno import config, db, personal, personal_attachments as attachments, personal_schema
from reno import storage_admin, storage_lifecycle as lifecycle
from tests.test_personal_projects import personal_env, envelope, create, add_atom  # noqa: F401
from tests.test_source_attachments import source, upload


def test_database_and_attachment_backup_restore_retains_history_receipts_and_identity(personal_env, tmp_path, monkeypatch):
    from reno import ask, review
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref], decision_reason="备份前的理由")
    source_record = source(personal_env)
    attachment = upload(personal_env, source_record).json()["attachment"]
    con = db.connect()
    db.fts_sync_atoms(con, ["personal-atom"])
    con.commit()
    version_before = db.knowledge_version(con)
    manifest = attachments.attachment_manifest(con)
    before = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in personal_schema.TABLES}
    backup = tmp_path / "backup.db"
    dest = sqlite3.connect(backup)
    con.backup(dest)
    dest.close()
    con.close()
    backup_files = tmp_path / "attachment-backup"
    shutil.copytree(config.DATA / "personal-attachments", backup_files)
    node_before = (config.DATA / "node-identity.json").read_bytes()
    calls = []
    def fake_chat(*args, **kwargs):
        calls.append(1)
        return f"人工回答 {len(calls)} [1]", "test"
    monkeypatch.setattr(ask.llm, "chat", fake_chat)
    ask._answer_cache.clear()
    answer_before = ask.ask("防水")
    assert ask.ask("防水")["answer"] == answer_before["answer"] and len(calls) == 1
    personal.update_card(card["card_id"], envelope({"decision_reason": "恢复点之后的变化"}, base=1))
    # Damage the live attachment, so this rehearsal has to restore BOTH parts
    # of the backup rather than accidentally reusing intact live files.
    damaged = attachments.resolve_storage(manifest[0]["storage_key"])
    damaged.write_bytes(b"damaged-after-backup")
    # Explicit rehearsal maintenance window: no worker/open application DB here.
    live = sqlite3.connect(config.db_path())
    saved = sqlite3.connect(backup)
    saved.backup(live)
    saved.close()
    live.close()
    con = db.connect()
    assert not personal.capabilities(con)["personal_write"]
    # A knowledge restore advances the namespace and replays facts in the same
    # explicit maintenance transaction; restored numeric revisions may repeat.
    con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    review.replay(con)
    con.commit()
    for entry in manifest:
        original_file = backup_files / entry["storage_key"]
        restored_file = attachments.resolve_storage(entry["storage_key"])
        restored_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_file, restored_file)
    assert {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in personal_schema.TABLES} == before
    assert personal.card_view(con, card["card_id"])["decision_reason"] == "备份前的理由"
    assert attachments.attachment_manifest(con) == manifest
    # Restored writability is observed inside the real storage lifetime window.
    with lifecycle.shared(storage_admin.from_connection(con)):
        assert personal.capabilities(con)["personal_write"]
    assert all(entry["complete"] for entry in manifest)
    assert personal.revisions(con, "card", card["card_id"])
    assert con.execute("PRAGMA foreign_key_check").fetchone() is None
    con.close()
    answer_after = ask.ask("防水")
    assert len(calls) == 2 and answer_after["answer"] != answer_before["answer"]
    assert answer_after["library_epoch"] == version_before["library_epoch"] + 1
    assert (config.DATA / "node-identity.json").read_bytes() == node_before
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file").status_code == 200


def test_database_only_restore_reports_missing_files_and_refuses_writes(personal_env, tmp_path):
    record = source(personal_env)
    attachment = upload(personal_env, record).json()["attachment"]
    con = db.connect()
    key = con.execute("SELECT storage_key FROM source_attachment").fetchone()[0]
    path = attachments.resolve_storage(key)
    saved = path.read_bytes()
    path.unlink()
    assert not attachments.attachment_manifest(con)[0]["complete"]
    cap = personal.capabilities(con)
    assert cap["personal_read"] and not cap["personal_write"] and not cap["personal_integrity_ok"]
    con.close()
    response = personal_env["client"].post(f"/api/projects/{personal_env['project']['project_id']}/decision-cards", json=envelope({"title": "不能假装恢复完整"}))
    assert response.status_code == 503 and response.json()["code"] == "PERSONAL_PRIMARY_UNAVAILABLE"
    # Restoring the corresponding immutable file makes the original reference
    # available again; no synthetic 'ready' record or replacement ID is made.
    path.write_bytes(saved)
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file").status_code == 200


def test_copying_database_does_not_copy_or_promote_node_identity(personal_env, tmp_path, monkeypatch):
    create(personal_env)
    replica = tmp_path / "replica"
    replica.mkdir()
    con = db.connect()
    destination = sqlite3.connect(replica / "reno.db")
    con.backup(destination)
    destination.close()
    primary = personal.capabilities(con)["instance_id"]
    con.close()
    (replica / "node-identity.json").write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    monkeypatch.setattr(config, "DATA", replica)
    con = db.connect()
    cap = personal.capabilities(con)
    assert cap["personal_primary_id"] == primary and cap["instance_id"] != primary
    assert cap["personal_read"] and not cap["personal_write"]
    con.close()


def test_disabling_features_preserves_all_personal_tables_and_attachments(personal_env):
    record = source(personal_env)
    upload(personal_env, record)
    create(personal_env)
    con = db.connect()
    counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in personal_schema.TABLES}
    manifest = attachments.attachment_manifest(con)
    con.close()
    config.set_local({"personal_features_enabled": False, "field_checks_enabled": False})
    assert personal_env["client"].get("/api/projects/default").json()["enabled"] is False
    con = db.connect()
    assert counts == {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in personal_schema.TABLES}
    assert attachments.attachment_manifest(con) == manifest
    con.close()


def test_same_length_attachment_corruption_blocks_writes_and_restore_audit_clears_cache(personal_env):
    record = source(personal_env)
    attachment = upload(personal_env, record).json()["attachment"]
    con = db.connect()
    key = con.execute("SELECT storage_key FROM source_attachment").fetchone()[0]
    path = attachments.resolve_storage(key)
    original = path.read_bytes()
    assert personal.capabilities(con)["personal_integrity_ok"]
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert path.stat().st_size == attachment["byte_size"]
    assert not personal.capabilities(con)["personal_integrity_ok"]
    assert not attachments.attachment_manifest(con)[0]["complete"]
    path.write_bytes(original)
    assert attachments.attachment_manifest(con)[0]["complete"]
    # Restored writability is observed inside the real storage lifetime window.
    with lifecycle.shared(storage_admin.from_connection(con)):
        assert personal.capabilities(con)["personal_write"]
    con.close()
