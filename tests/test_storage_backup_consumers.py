"""Production snapshot adapter preserves the caller and exact storage scope."""
import json
from pathlib import Path
import sqlite3

import pytest

from reno import config, db, judge, storage_admin, storage_backups, storage_lifecycle as lifecycle, sync_media


def test_snapshot_adapter_copies_committed_revision_without_finishing_caller_transaction():
    profile = storage_admin.profile()
    con = db.connect()
    destination = config.DATA / "committed-probe.db"
    try:
        committed = db.knowledge_version(con)
        with lifecycle.shared(profile):
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'")
            result = db.snapshot(con, destination, kind="maintenance")
            assert con.in_transaction
            assert db.knowledge_version(con)["kb_revision"] == committed["kb_revision"] + 1
            image = sqlite3.connect(destination)
            image.row_factory = sqlite3.Row
            try:
                assert db.knowledge_version(image) == committed
            finally:
                image.close()
            descriptor = json.loads(result["descriptor_path"].read_text(encoding="utf-8"))
            assert descriptor["source_version"] == committed
            assert descriptor["kind"] == "maintenance"
            con.rollback()
        assert db.knowledge_version(con) == committed
    finally:
        con.close()


def test_snapshot_adapter_does_not_acquire_storage_below_an_existing_write_transaction():
    con = db.connect()
    destination = config.DATA / "must-not-publish.db"
    catalog = config.DATA / ".reno-storage-backups-v1"
    before = {p.name: p.read_bytes() for p in catalog.glob("*.json")} if catalog.exists() else None
    try:
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'")
        with pytest.raises(lifecycle.StorageLifecycleError, match="Storage lifecycle") as error:
            db.snapshot(con, destination)
        assert error.value.code == "STORAGE_GUARD_REQUIRED"
        assert con.in_transaction and not destination.exists()
        after = {p.name: p.read_bytes() for p in catalog.glob("*.json")} if catalog.exists() else None
        assert after == before
    finally:
        con.rollback()
        con.close()


def test_judge_retry_finishes_original_partial_retention_before_a_new_backup(monkeypatch):
    profile = storage_admin.profile()
    con = db.connect()
    db.snapshot(con, config.DATA / "other-kind.db", kind="maintenance")
    for number in range(4):
        db.snapshot(con, config.DATA / f"reno.db.bak-judge-seed-{number}", kind="judge")
    original_unlink = sync_media._Directory.unlink
    failed = []
    def interrupt(directory, name):
        if name.endswith(storage_backups.SUFFIX) and not failed:
            failed.append(name)
            raise PermissionError("Synthetic temporary open handle")
        return original_unlink(directory, name)
    monkeypatch.setattr(sync_media._Directory, "unlink", interrupt)
    monkeypatch.setattr(judge, "_judge_llm", lambda *_: pytest.fail("Empty knowledge must not call a model"))
    try:
        with pytest.raises(judge.JudgeError) as error:
            judge.run(con)
        assert error.value.code == "JUDGE_BACKUP_RETENTION_BLOCKED"
        operation_id = error.value.operation_id
        assert storage_backups.pending_retention(profile) == [operation_id]
        catalog = config.DATA / storage_backups.CATALOG
        before = len(list(catalog.glob("*.json")))
        assert judge.run(con) == {"candidates": 0, "clusters": 0, "conflicts": 0}
        assert len(list(catalog.glob("*.json"))) == before + 1
        assert storage_backups.pending_retention(profile) == []
        receipt = json.loads((catalog / "operations" / (operation_id + ".json")).read_text(encoding="utf-8"))
        assert receipt["state"] == "completed" and not receipt["result"]["blocked"]
        with lifecycle.shared(profile):
            discovery = storage_backups.discover_locked(profile)
            ready = [b for b in discovery["backups"] if b["state"] == "ready"
                     and Path(b["path"]).name.startswith("reno.db.bak-judge-")]
            assert len(ready) == 3
        assert (config.DATA / "other-kind.db").is_file()
    finally:
        con.close()


def test_other_database_partial_retention_does_not_block_this_judge(monkeypatch):
    profile = storage_admin.profile()
    other = lifecycle.StorageProfile(config.DATA / "other.db", config.DATA / "other-media",
        config.DATA / "other-reports", config.DATA / "other-checkpoints")
    source, foreign = db.connect(), sqlite3.connect(other.database)
    try:
        source.backup(foreign)
        lifecycle.initialize(other)
        for number in range(2):
            db.snapshot(foreign, config.DATA / f"other.db.bak-judge-seed-{number}", profile=other, kind="judge")
        original_unlink = sync_media._Directory.unlink
        failed = []
        def interrupt(directory, name):
            if name.endswith(storage_backups.SUFFIX) and not failed:
                failed.append(name)
                raise PermissionError("Synthetic foreign partial delete")
            return original_unlink(directory, name)
        monkeypatch.setattr(sync_media._Directory, "unlink", interrupt)
        import uuid
        operation_id = str(uuid.uuid4())
        assert storage_backups.retain_recent(other, keep=1, operation_id=operation_id)["blocked"]
        assert storage_backups.pending_retention(other, keep=1) == [operation_id]
        assert storage_backups.pending_retention(profile) == []
        monkeypatch.setattr(judge, "_judge_llm", lambda *_: pytest.fail("Empty knowledge must not call a model"))
        assert judge.run(source) == {"candidates": 0, "clusters": 0, "conflicts": 0}
        assert storage_backups.pending_retention(other, keep=1) == [operation_id]
        assert any(p.name.endswith(storage_backups.SUFFIX) for p in config.DATA.glob("other.db.bak-judge-seed-0*"))
    finally:
        source.close()
        foreign.close()
