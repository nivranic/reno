"""Explicit facility setup stays separate from DB migration and read-only status."""
import json

import pytest

from reno import cli, config, db, storage_admin, storage_lifecycle as lifecycle


def arguments(root):
    return ["--db", str(root / "data/reno.db"), "--media", str(root / "media"),
            "--reports", str(root / "reports"), "--checkpoints", str(root / "checkpoints")]


def test_status_of_absent_facilities_is_read_only(tmp_path, capsys):
    root = tmp_path / "absent"
    assert cli.main(["storage-status", *arguments(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"initialized": False, "code": "STORAGE_NOT_INITIALIZED"}
    assert not root.exists()


def test_init_requires_existing_schema_and_never_creates_database(tmp_path, capsys):
    root = tmp_path / "absent"
    assert cli.main(["storage-init", *arguments(root)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert not root.exists()


@pytest.mark.parametrize("command", ["storage-init", "storage-status"])
def test_custom_database_cannot_implicitly_borrow_default_media(tmp_path, capsys, command):
    path = tmp_path / "absent/reno.db"
    assert cli.main([command, "--db", str(path)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_PROFILE_REQUIRED"
    assert not path.parent.exists()


def test_explicit_setup_is_idempotent_and_preserves_database(tmp_path, capsys):
    root = tmp_path / "store"
    database = root / "data/reno.db"
    db.initialize(database)
    con = db.connect(database)
    before = db.knowledge_version(con)
    con.close()
    assert cli.main(["storage-init", *arguments(root)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created_locks"] == 4
    assert cli.main(["storage-init", *arguments(root)]) == 0
    assert json.loads(capsys.readouterr().out)["created_locks"] == 0
    existing = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}
    assert cli.main(["storage-status", *arguments(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"initialized": True}
    assert existing == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}
    con = db.connect(database)
    assert db.knowledge_version(con) == before
    con.close()


def test_default_profile_construction_does_not_load_config_or_create_paths(tmp_path, monkeypatch):
    root = tmp_path / "absent"
    monkeypatch.setattr(config, "DATA", root / "data")
    monkeypatch.setattr(config, "MEDIA", root / "media")
    monkeypatch.setattr(config, "get", lambda *_: pytest.fail("default profile must not load mutable config"))
    value = storage_admin.profile()
    assert value.database == root / "data/reno.db"
    assert value.media == root / "media"
    assert value.reports == root / "data/generated"
    assert value.checkpoints == root / "data/sync-checkpoints"
    assert not root.exists()


def test_application_initializes_only_on_lifespan_after_database_check(tmp_path, monkeypatch):
    from app.main import create_app
    from fastapi.testclient import TestClient

    root = tmp_path / "app"
    monkeypatch.setattr(config, "DATA", root / "data")
    monkeypatch.setattr(config, "MEDIA", root / "media")
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "auth_token": "", "web_ui": "classic"})
    value = storage_admin.profile()
    app = create_app()
    assert not root.exists()
    with TestClient(app) as client:
        assert storage_admin.status(value)["initialized"]
        assert client.get("/api/health").status_code == 200
    with lifecycle.shared(value, timeout=0):
        assert lifecycle.require_guard(value)


def test_existing_transaction_cannot_gain_a_guard_after_reading_pointers():
    con = db.connect()
    try:
        con.execute("BEGIN")
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            storage_admin.from_connection(con, profile=storage_admin.profile())
        assert caught.value.code == "STORAGE_GUARD_REQUIRED"
        assert con.in_transaction
    finally:
        con.close()


def test_custom_database_requires_all_roots_but_can_borrow_held_guard(tmp_path):
    value = storage_admin.profile(database=tmp_path / "data/reno.db", media=tmp_path / "media",
                                  reports=tmp_path / "reports", checkpoints=tmp_path / "checkpoints")
    db.initialize(value.database)
    storage_admin.initialize(value)
    con = db.connect(value.database)
    try:
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            storage_admin.from_connection(con)
        assert caught.value.code == "STORAGE_PROFILE_REQUIRED"
        assert storage_admin.from_connection(con, media=value.media, reports=value.reports,
                                             checkpoints=value.checkpoints) == value
        with lifecycle.shared(value):
            con.execute("BEGIN")
            assert storage_admin.from_connection(con) == value
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                storage_admin.from_connection(con, reports=tmp_path / "another-report-root")
            assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
            con.rollback()
    finally:
        con.close()


def test_foreign_connection_never_borrows_default_guard(tmp_path):
    database = tmp_path / "other.db"
    db.initialize(database)
    con = db.connect(database)
    try:
        with lifecycle.shared(storage_admin.profile()):
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                storage_admin.from_connection(con)
            assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    finally:
        con.close()


def test_connection_guard_spans_commit_rollback_and_closes_on_error():
    value = storage_admin.profile()
    with pytest.raises(RuntimeError, match="synthetic"):
        with storage_admin.connection(value) as con:
            con.execute("BEGIN")
            assert storage_admin.from_connection(con) == value
            con.commit()
            assert lifecycle.require_guard(value)
            con.execute("BEGIN")
            raise RuntimeError("synthetic")
    assert lifecycle.current_guard() is None
    with lifecycle.exclusive(value, timeout=0):
        assert lifecycle.current_guard()
