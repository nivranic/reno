"""Migration acquires existing physical protection before SQL and backup."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from reno import cli, db, migrations, storage_backups as backups, storage_lifecycle as lifecycle
from tests.migration_helpers import initialize_migration_storage, migration_arguments, migration_profile
from tests.test_personal_migrations import legacy_database


def contents(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_missing_facilities_fail_before_sql_without_implicit_initialization(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path, initialize_storage=False)
    profile = migration_profile(path)
    before = contents(tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("SQL opened before lifecycle facilities were available")
    monkeypatch.setattr(migrations, "_open", forbidden)
    with pytest.raises(lifecycle.StorageLifecycleError) as error:
        db.migrate(path, profile=profile)
    assert error.value.code == "STORAGE_NOT_INITIALIZED"
    assert contents(tmp_path) == before
    assert not profile.media.exists()


def test_custom_database_requires_explicit_complete_matching_profile(tmp_path):
    path = tmp_path / "custom.db"
    legacy_database(path)
    before = contents(tmp_path)
    with pytest.raises(lifecycle.StorageLifecycleError) as error:
        db.migrate(path)
    assert error.value.code == "STORAGE_PROFILE_REQUIRED"
    with pytest.raises(lifecycle.StorageLifecycleError) as error:
        db.migrate(path, profile=migration_profile(tmp_path / "different.db"))
    assert error.value.code == "STORAGE_PROFILE_MISMATCH"
    assert contents(tmp_path) == before


def test_backup_requires_preexisting_guard_and_preserves_callers_transaction(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    con = migrations._open(path)
    con.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(lifecycle.StorageLifecycleError) as error:
            migrations._backup(con, path, 0, {}, profile=migration_profile(path))
        assert error.value.code == "STORAGE_GUARD_REQUIRED"
        assert con.in_transaction
        assert not list(tmp_path.glob("*.bak-migrate-*"))
    finally:
        con.rollback()
        con.close()


def test_migration_guard_precedes_sql_blocks_gc_and_preserves_both_backup_formats(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    profile = migration_profile(path)
    open_original, backup_original, integrity_original = migrations._open, migrations._backup, migrations._integrity
    observed = []
    checked_images = []
    def checked_integrity(con):
        checked_images.append(con.execute("PRAGMA database_list").fetchone()[2])
        return integrity_original(con)
    def checked_open(*args, **kwargs):
        lifecycle.require_guard(profile)
        observed.append("open-protected")
        return open_original(*args, **kwargs)
    def gc_attempt():
        try:
            with lifecycle.exclusive(profile, timeout=0):
                return "incorrectly-acquired"
        except lifecycle.StorageLifecycleError as exc:
            return exc.code
    def checked_backup(con, *args, **kwargs):
        assert con.in_transaction
        lifecycle.require_guard(profile)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(gc_attempt).result(timeout=5) == "STORAGE_BUSY"
        observed.append("backup-protected")
        return backup_original(con, *args, **kwargs)
    monkeypatch.setattr(migrations, "_open", checked_open)
    monkeypatch.setattr(migrations, "_backup", checked_backup)
    monkeypatch.setattr(migrations, "_integrity", checked_integrity)
    result = db.migrate(path, profile=profile)
    assert observed == ["open-protected", "backup-protected"]
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    assert any(name.replace("\\", "/").endswith(result.backup_path.name) for name in checked_images)
    descriptor = result.backup_path.with_name(result.backup_path.name + backups.SUFFIX)
    assert descriptor != result.manifest_path and descriptor.is_file()
    note = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    frozen = json.loads(descriptor.read_text(encoding="utf-8"))
    assert note["from_version"] == frozen["schema_version"] == 0
    assert note["attachment_files_copied"] is False
    assert frozen["kind"] == "migration"
    assert frozen["source_version"] == {"library_id": None, "library_epoch": None, "kb_revision": None}
    with lifecycle.exclusive(profile, timeout=0):
        assert lifecycle.require_guard(profile, exclusive=True)


def test_current_schema_noop_uses_guard_but_creates_no_backup_or_catalog(tmp_path):
    path = tmp_path / "current.db"
    db.initialize(path)
    profile = migration_profile(path)
    with pytest.raises(lifecycle.StorageLifecycleError, match="protection"):
        db.migrate(path, profile=profile)
    initialize_migration_storage(path)
    before = contents(tmp_path)
    result = db.migrate(path, profile=profile)
    assert not result.changed and result.backup_path is None and result.manifest_path is None
    assert contents(tmp_path) == before
    assert not (path.parent / backups.CATALOG).exists()


def test_cli_offline_flag_is_explicit_and_runs_before_migration_open(tmp_path, monkeypatch, capsys):
    path = tmp_path / "legacy.db"
    legacy_database(path, initialize_storage=False)
    arguments = ["migrate-db", *migration_arguments(path)]
    assert cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_NOT_INITIALIZED"
    assert not migration_profile(path).media.exists()
    original, observations = migrations._open, []
    def checked_open(*args, **kwargs):
        lifecycle.require_guard(migration_profile(path))
        observations.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(migrations, "_open", checked_open)
    assert cli.main([*arguments, "--initialize-storage-offline"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] and observations
    assert result["backup_path"] and result["manifest_path"]


def test_cli_partial_custom_profile_rejected_before_offline_setup(tmp_path, capsys):
    path = tmp_path / "legacy.db"
    legacy_database(path, initialize_storage=False)
    before = contents(tmp_path)
    assert cli.main(["migrate-db", "--db", str(path), "--initialize-storage-offline"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_PROFILE_REQUIRED"
    assert contents(tmp_path) == before
