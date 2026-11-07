"""Receiver archives follow real publication commit, retry and rollback rules."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import cli, config, db, personal_schema, review, sync_checkpoints, sync_reports
from reno.sync_protocol import SyncError
from tests.test_sync_transfer import env, next_payload, publish, report_entry, receiver_guard


def checkpoints(env):
    return sync_checkpoints.list_checkpoints(env["con"], media_store=env["store"])


def test_first_publication_checkpoint_is_empty_and_visible_only_after_commit(env):
    with receiver_guard(env):
        receipt = publish(env)
        observer = db.connect(env["target_path"])
        try:
            assert sync_checkpoints.list_checkpoints(observer) == []
            env["con"].commit()
            entries = sync_checkpoints.list_checkpoints(observer)
            assert len(entries) == 1
            assert entries[0]["publication_id"] == receipt["publication_id"]
            assert entries[0]["recoverable"] is True
            assert entries[0]["row_counts"]["video_asset"] == 0
            assert entries[0]["receiver_version"]["kb_revision"] < receipt["local_version"]["kb_revision"]
        finally:
            observer.close()


def test_lost_response_replay_preserves_exact_single_checkpoint(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        before = checkpoints(env)
        files = set((env["target_path"].parent / "sync-checkpoints").iterdir())
        assert publish(env)["replayed"] is True
        env["con"].commit()
        assert checkpoints(env) == before
        assert set((env["target_path"].parent / "sync-checkpoints").iterdir()) == files


@pytest.mark.parametrize("failure", ["outer_rollback", "pointer_failure", "registration_failure"])
def test_failed_publication_never_exposes_checkpoint_or_changes_library(env, monkeypatch, failure):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        before = checkpoints(env)
        revision = db.knowledge_version(env["con"])
        def fail(*args, **kwargs):
            raise OSError("synthetic archive/publication fault")
        if failure != "outer_rollback":
            module, attr = ((sync_reports, "install_pointers") if failure == "pointer_failure"
                            else (sync_checkpoints, "register"))
            monkeypatch.setattr(module, attr, fail)
            with pytest.raises(OSError, match="synthetic"):
                publish(env, next_payload(env))
        else:
            publish(env, next_payload(env))
            env["con"].rollback()
        assert checkpoints(env) == before
        assert db.knowledge_version(env["con"]) == revision
        assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1
        # Durable orphan files do not become selectable recovery points.
        assert len(list((env["target_path"].parent / "sync-checkpoints").glob("*.sqlite"))) == 2


def test_before_image_contains_old_knowledge_but_never_personal_or_event_tables(env):
    with receiver_guard(env):
        publish(env)
        con = env["con"]
        con.commit()
        review.record_decision(con, atom_id="atom", action="reject", client_id="synthetic-reviewer")
        con.commit()
        before_events = [tuple(row) for row in con.execute("SELECT * FROM user_decision")]
        private = {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")]
                   for name in personal_schema.TABLES}
        version = db.knowledge_version(con)
        value = next_payload(env)
        value["videos"][0]["tables"]["knowledge_atom"][0]["claim"] = "新版施工知识"
        publish(env, value)
        con.commit()
        entry = next(row for row in checkpoints(env) if row["publication_id"] == value["publication_id"])
        assert entry["recoverable"] is True
        assert entry["receiver_version"] == version
        with sync_checkpoints.open_verified(con, entry["checkpoint_id"], media_store=env["store"]) as (archive, _):
            assert archive.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "施工前检查基层"
            assert archive.execute("SELECT source_item_id FROM evidence_ref").fetchone()[0] == "seg"
            tables = {row[0] for row in archive.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not (tables & set(personal_schema.TABLES))
            assert not (tables & {"user_decision", "sync_queue", "job", "sync_peer", "sync_publication"})
            assert all(row[0].startswith("step:") for row in archive.execute("SELECT key FROM meta"))
        assert con.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "新版施工知识"
        assert [tuple(row) for row in con.execute("SELECT * FROM user_decision")] == before_events
        assert {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")]
                for name in personal_schema.TABLES} == private


def test_report_only_new_publication_archives_previous_report_pointer(env, tmp_path):
    root = tmp_path / "reports"
    with receiver_guard(env, report_root=root):
        initial = deepcopy(env["payload"])
        initial["reports"] = [report_entry(env)]
        publish(env, initial, report_root=root)
        con = env["con"]
        con.commit()
        old = dict(con.execute("SELECT * FROM sync_report").fetchone())
        version = db.knowledge_version(con)
        value = deepcopy(initial)
        value["publication_id"] = str(uuid.uuid4())
        value["reports"] = [report_entry(env, "# 新版报告\n")]
        publish(env, value, report_root=root)
        con.commit()
        entries = sync_checkpoints.list_checkpoints(con, media_store=env["store"], report_root=root)
        assert len(entries) == 2
        checkpoint = next(row for row in entries if row["publication_id"] == value["publication_id"])
        assert checkpoint["recoverable"] is True
        with sync_checkpoints.open_verified(con, checkpoint["checkpoint_id"], media_store=env["store"], report_root=root) as (archive, _):
            assert dict(archive.execute("SELECT * FROM sync_report").fetchone()) == old
        assert db.knowledge_version(con) == version


def test_readonly_cli_lists_previews_and_exports_without_mutating_library(env, monkeypatch, capsys):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        monkeypatch.setattr(config, "db_path", lambda: env["target_path"])
        before = checkpoints(env)
        assert cli.main(["sync-checkpoints"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows == before
        assert cli.main(["sync-checkpoint-preview", rows[0]["checkpoint_id"]]) == 0
        assert json.loads(capsys.readouterr().out) == rows[0]
        assert cli.main(["reconciliation", "--scope", "all", "--markdown"]) == 0
        assert "# 复核对账" in capsys.readouterr().out
        assert checkpoints(env) == before


@pytest.mark.parametrize("arguments", [["sync-checkpoints"], ["sync-checkpoint-preview", str(uuid.uuid4())], ["reconciliation"]])
def test_readonly_cli_never_initializes_missing_library(tmp_path, monkeypatch, capsys, arguments):
    path = tmp_path / "never-created" / "reno.db"
    monkeypatch.setattr(config, "db_path", lambda: path)
    assert cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert not path.parent.exists()


def test_unsupported_sqlite_runtime_rejects_publication_without_partial_changes(env, monkeypatch):
    with receiver_guard(env):
        version = db.knowledge_version(env["con"])
        with monkeypatch.context() as local:
            local.setattr(sync_checkpoints.sqlite3, "Connection", type("LegacySQLite", (), {}))
            with pytest.raises(SyncError) as error:
                publish(env)
            assert error.value.code == "SYNC_CHECKPOINT_RUNTIME_UNSUPPORTED"
            assert error.value.status == 503
        assert db.knowledge_version(env["con"]) == version
        assert env["con"].execute("SELECT COUNT(*) FROM sync_publication").fetchone()[0] == 0
        assert env["con"].execute("SELECT COUNT(*) FROM knowledge_atom").fetchone()[0] == 0
        assert not list((env["target_path"].parent / "sync-checkpoints").glob("*.sqlite"))


def test_exposed_but_unavailable_serialization_returns_controlled_runtime_error(env, monkeypatch):
    with receiver_guard(env):
        original = sync_checkpoints.sqlite3.connect
        closed = []
        class UnavailableRuntime:
            def __init__(self):
                self.inner = original(":memory:")
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def serialize(self):
                raise sync_checkpoints.sqlite3.NotSupportedError("synthetic unsupported SQLite build")
            def close(self):
                self.inner.close()
                closed.append(True)
        with monkeypatch.context() as local:
            local.setattr(sync_checkpoints.sqlite3, "connect", lambda *args, **kwargs: UnavailableRuntime())
            with pytest.raises(SyncError) as error:
                publish(env)
            assert error.value.code == "SYNC_CHECKPOINT_RUNTIME_UNSUPPORTED"
            assert error.value.status == 503
        assert closed == [True]
        assert env["con"].execute("SELECT COUNT(*) FROM sync_publication").fetchone()[0] == 0
        assert not list((env["target_path"].parent / "sync-checkpoints").glob("*.sqlite"))
