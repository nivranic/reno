"""Synthetic TEMP-only contract tests; never read repository config or DB."""
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

import pytest

from reno import config, migrations, storage_lifecycle as lifecycle, storage_backups as backups

SHA = "a" * 64
VIDEO = "bilibili_BV1Synthetic"
MEDIA_KEY = f"sync/{VIDEO}/{SHA}.mp4"
LOCAL_KEY = ".versions/checklist-" + "b" * 32 + ".md"
SYNC_KEY = ".sync-versions/prices-" + "c" * 64 + ".md"


@pytest.fixture(autouse=True)
def no_real_config(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No real configuration is allowed in draft tests")
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "local_path", forbidden)


def synthetic(root, version=10, *, initialize=True):
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media", root / "reports", root / "checkpoints")
    profile.database.parent.mkdir(parents=True)
    con = sqlite3.connect(profile.database)
    con.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE video_asset(files_json TEXT,content_sha256 TEXT);
        CREATE TABLE frame(path TEXT);
        CREATE TABLE transaction_probe(value TEXT);
    """)
    con.execute("INSERT INTO meta VALUES('migration_version',?)", (str(version),))
    if version:
        con.executemany("INSERT INTO meta VALUES(?,?)", [("library_id", str(uuid.uuid4())), ("library_epoch", "2"), ("kb_revision", "7")])
    con.execute("INSERT INTO video_asset VALUES(?,?)", (json.dumps({"video": "media/" + MEDIA_KEY}), SHA))
    con.execute("INSERT INTO transaction_probe VALUES('committed')")
    if version >= 6:
        con.execute("CREATE TABLE sync_report(storage_key TEXT,sha256 TEXT,byte_size INTEGER)")
        con.execute("INSERT INTO sync_report VALUES(?,?,?)", (SYNC_KEY, "c" * 64, 3))
    if version >= 7:
        con.execute("CREATE TABLE sync_checkpoint(checkpoint_id TEXT,storage_key TEXT,sha256 TEXT,byte_size INTEGER)")
    if version >= 8:
        con.execute("CREATE TABLE sync_recovery_checkpoint(checkpoint_id TEXT,storage_key TEXT,sha256 TEXT,byte_size INTEGER)")
    if version >= 10:
        con.executescript("""
            CREATE TABLE sync_upload_pin(kind TEXT,state TEXT,descriptor_json TEXT);
            CREATE TABLE sync_checkpoint_retirement(origin_kind TEXT,checkpoint_id TEXT,operation_id TEXT,storage_key TEXT,sha256 TEXT,byte_size INTEGER);
            CREATE TABLE storage_gc_operation(operation_id TEXT,state TEXT,request_sha256 TEXT,preview_sha256 TEXT,roots_sha256 TEXT,request_json TEXT,policy_json TEXT,result_json TEXT);
        """)
    con.commit()
    if initialize:
        con.close()
        backups.initialize_offline(profile)
        con = sqlite3.connect(profile.database)
    return profile, con


@contextmanager
def failure(code):
    with pytest.raises((backups.BackupError, lifecycle.StorageLifecycleError)) as caught:
        yield
    assert caught.value.code == code


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in root.rglob("*")}


def create(con, profile, name, *, backup_id=None, kind="judge", external=None):
    target = (external or profile.database.parent) / (name + ".db")
    return backups.create(con, target, profile=profile, kind=kind, backup_id=backup_id)


def record(profile, backup_id):
    path = profile.database.parent / backups.CATALOG / (backup_id + ".json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def discover(profile):
    with lifecycle.shared(profile):
        return backups.discover_locked(profile)


@pytest.mark.parametrize("version", [0, 6, 7, 10])
def test_supported_schemas_committed_image_and_external_registration(tmp_path, version):
    profile, con = synthetic(tmp_path / "source", version)
    external = tmp_path / "external"
    external.mkdir()
    try:
        result = create(con, profile, "outside", external=external)
        description = json.loads(result["descriptor_path"].read_text(encoding="utf-8"))
        assert description["schema_version"] == version
        assert description["consistency"] == "committed-db-plus-observed-manifests"
        assert description["source_profile"] == backups._profile_json(profile)
        if version == 0:
            assert description["source_version"] == {"library_id": None, "library_epoch": None, "kb_revision": None}
        assert description["blockers"] == {}
        before = snapshot(tmp_path)
        found = discover(profile)
        assert snapshot(tmp_path) == before
        assert len(found["backups"]) == 1
        assert backups._key(found["backups"][0]["path"]) == backups._key(result["path"])
        assert found["blockers"] == {}
        assert any(r["storage_key"] == MEDIA_KEY for r in found["backups"][0]["dependencies"])
        if version >= 6:
            assert any(r["storage_key"] == SYNC_KEY for r in found["backups"][0]["dependencies"])
    finally:
        con.close()


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_pending_write_is_not_copied_and_caller_transaction_is_untouched(tmp_path, journal):
    profile, con = synthetic(tmp_path)
    try:
        con.execute("PRAGMA journal_mode=" + journal)
        with lifecycle.shared(profile):
            con.execute("INSERT INTO transaction_probe VALUES('pending')")
            result = create(con, profile, "before")
            assert con.in_transaction
            assert con.execute("SELECT value FROM transaction_probe").fetchall() == [("committed",), ("pending",)]
            with sqlite3.connect(result["path"]) as saved:
                assert saved.execute("SELECT value FROM transaction_probe").fetchall() == [("committed",)]
            con.rollback()
        assert con.execute("SELECT value FROM transaction_probe").fetchall() == [("committed",)]
    finally:
        con.close()


def test_active_transaction_without_guard_cannot_acquire_one(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        con.execute("INSERT INTO transaction_probe VALUES('pending')")
        with failure("STORAGE_GUARD_REQUIRED"):
            create(con, profile, "before")
        assert con.in_transaction
        assert not (profile.database.parent / backups.CATALOG).exists()
    finally:
        con.close()


def test_source_version_comes_only_from_committed_frozen_image(tmp_path):
    profile, con = synthetic(tmp_path)
    committed_id = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    try:
        with lifecycle.shared(profile):
            con.execute("UPDATE meta SET value='91' WHERE key='kb_revision'")
            result = create(con, profile, "before")
            descriptor = json.loads(result["descriptor_path"].read_text())
            assert descriptor["source_version"] == {"library_id": committed_id, "library_epoch": 2, "kb_revision": 7}
            assert con.in_transaction
            con.commit()
        assert discover(profile)["backups"][0]["source_version"]["kb_revision"] == 7
    finally:
        con.close()


def test_two_databases_in_one_data_directory_keep_separate_registered_backups(tmp_path):
    profile, con = synthetic(tmp_path)
    second = lifecycle.StorageProfile(profile.database.with_name("second.db"), profile.media, profile.reports, profile.checkpoints)
    shutil.copyfile(profile.database, second.database)
    backups.initialize_offline(second)
    other = sqlite3.connect(second.database)
    try:
        first_old = create(con, profile, "first-old")
        first_new = create(con, profile, "first-new")
        other_old = create(other, second, "second-old")
        other_new = create(other, second, "second-new")
        assert len(discover(profile)["backups"]) == 2
        assert len(discover(second)["backups"]) == 2
        assert discover(profile)["blockers"] == {}
        assert discover(second)["blockers"] == {}
        result = backups.retain_recent(profile, keep=1, operation_id=str(uuid.uuid4()))
        assert result == {"retired": [first_old["backup_id"]], "blocked": []}
        assert first_new["path"].exists() and other_old["path"].exists() and other_new["path"].exists()
        assert discover(second)["blockers"] == {}
    finally:
        other.close()
        con.close()


def test_old_schema_requires_explicit_offline_initialization(tmp_path):
    profile, con = synthetic(tmp_path, 0, initialize=False)
    try:
        before = snapshot(tmp_path)
        with failure("STORAGE_NOT_INITIALIZED"):
            create(con, profile, "before")
        assert snapshot(tmp_path) == before
        con.close()
        backups.initialize_offline(profile)
        con = sqlite3.connect(profile.database)
        assert create(con, profile, "before")["path"].is_file()
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "0"
    finally:
        con.close()


def test_frozen_manifest_bytes_survive_current_manifest_replacement(tmp_path):
    profile, con = synthetic(tmp_path)
    manifest = profile.reports / "checklist.manifest.json"
    original = b'{ "content_file" : "' + LOCAL_KEY.encode() + b'", "content_sha256": "' + SHA.encode() + b'", "byte_size": 17 }\n'
    manifest.write_bytes(original)
    try:
        result = create(con, profile, "before")
        manifest.write_text('{"content_file":".versions/checklist-' + "d" * 32 + '.md"}', encoding="utf-8")
        found = discover(profile)["backups"][0]
        assert base64.b64decode(found["frozen_manifests"]["checklist"]["raw_base64"]) == original
        assert any(r["storage_key"] == LOCAL_KEY for r in found["dependencies"])
        assert not any("d" * 32 in r["storage_key"] for r in found["dependencies"])
        assert len(json.loads(result["descriptor_path"].read_text())["manifests"]) == 5
    finally:
        con.close()


@pytest.mark.parametrize("style", ["relative", "backslashes", "absolute"])
def test_every_files_json_field_and_frame_path_protect_managed_media(tmp_path, style):
    profile, con = synthetic(tmp_path)
    second_key = f"sync/{VIDEO}/" + "d" * 64 + ".mp4"
    frame_key = f"sync/{VIDEO}/" + "e" * 64 + ".mp4"
    def reference(key):
        if style == "absolute":
            return str(profile.media / key)
        value = "media/" + key
        return value.replace("/", "\\") if style == "backslashes" else value
    try:
        con.execute("UPDATE video_asset SET files_json=?", (json.dumps({"video": reference(MEDIA_KEY), "audio": reference(second_key),
            "source": reference("originals/local.mp4"), "thumbnail": reference("frames/local.jpg")}),))
        con.execute("INSERT INTO frame VALUES(?)", (reference(frame_key),))
        con.execute("INSERT INTO frame VALUES(?)", (reference("frames/local.jpg"),))
        con.commit()
        create(con, profile, "before")
        found = discover(profile)["backups"][0]
        keys = {r["storage_key"] for r in found["dependencies"] if r["namespace"] == "sync_media"}
        assert keys == {MEDIA_KEY, second_key, frame_key}
        assert found["blockers"] == {}
    finally:
        con.close()


@pytest.mark.parametrize("source", ["file", "frame"])
def test_unmappable_legacy_references_block_managed_media_namespace(tmp_path, source):
    profile, con = synthetic(tmp_path)
    try:
        unknown = str(tmp_path / "other-media" / MEDIA_KEY)
        if source == "file":
            con.execute("UPDATE video_asset SET files_json=?", (json.dumps({"video": "media/" + MEDIA_KEY, "audio": unknown}),))
        else:
            con.execute("INSERT INTO frame VALUES(?)", (unknown,))
        con.commit()
        create(con, profile, "before")
        assert "media_mapping_unknown" in discover(profile)["blockers"]["sync_media"]
    finally:
        con.close()


@pytest.mark.parametrize("broken", ["bad JSON", "{\"content_file\":42}"])
def test_unreadable_manifest_is_preserved_and_blocks_local_report(tmp_path, broken):
    profile, con = synthetic(tmp_path)
    (profile.reports / "prices.manifest.json").write_text(broken)
    try:
        create(con, profile, "before")
        assert "local_report" in discover(profile)["blockers"]
    finally:
        con.close()


def test_hot_rollback_journal_blocks_legacy_image_without_recovering_source(tmp_path):
    profile, con = synthetic(tmp_path, 7)
    legacy = profile.database.parent / "maintenance-journal/before/reno.db"
    legacy.parent.mkdir(parents=True)
    target = sqlite3.connect(legacy)
    try:
        con.backup(target)
    finally:
        target.close()
    con.close()
    changed = json.dumps({"video": "media/sync/" + VIDEO + "/" + "e" * 64 + ".mp4"})
    code = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode=DELETE')
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE video_asset SET files_json=?', (sys.argv[2],))
for i in range(80):
    connection.execute('INSERT INTO transaction_probe VALUES(?)', ('spill-' + str(i) + '-' + 'x' * 8000,))
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", code, str(legacy), changed],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    journal = legacy.with_name(legacy.name + "-journal")
    assert journal.stat().st_size > 0
    raw = sqlite3.connect(legacy.as_uri() + "?immutable=1", uri=True)
    try:
        assert json.loads(raw.execute("SELECT files_json FROM video_asset").fetchone()[0]) == json.loads(changed)
    finally:
        raw.close()
    # Recover only a private copy to demonstrate that the committed pointer is
    # different. Discovery must never perform this write on the legacy image.
    recovered = tmp_path / "recovered.db"
    shutil.copyfile(legacy, recovered)
    shutil.copyfile(journal, recovered.with_name(recovered.name + "-journal"))
    recovered_connection = sqlite3.connect(recovered)
    try:
        committed = json.loads(recovered_connection.execute("SELECT files_json FROM video_asset").fetchone()[0])
        assert committed["video"] == "media/" + MEDIA_KEY
    finally:
        recovered_connection.close()
    originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (legacy, journal)}
    found = discover(profile)
    entry = next(b for b in found["backups"] if b["path"] == str(legacy))
    assert set(entry["blockers"]) == set(backups.NAMESPACES)
    assert all("unmaterialized_journal" in reasons for reasons in entry["blockers"].values())
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals} == originals


def test_all_fixed_builtin_locations_remain_discoverable_without_catalog(tmp_path):
    profile, con = synthetic(tmp_path, 7)
    paths = [profile.database.parent / "reno.db.bak-judge-old",
             profile.database.parent / "maintenance-old/before/reno.db",
             profile.database.parent / "maintenance-schema7/reno-before-schema7.db",
             profile.database.parent / "service-old/reno-before-reload.db"]
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(profile.database, path)
        before = snapshot(tmp_path)
        found = discover(profile)
        assert {r["path"] for r in found["backups"]} == {str(p) for p in paths}
        assert found["blockers"] == {"local_report": ["legacy_manifest_unknown"]}
        assert snapshot(tmp_path) == before
        assert not (profile.database.parent / backups.CATALOG).exists()
    finally:
        con.close()


@pytest.mark.parametrize("mode", ["missing", "corrupt", "wal", "descriptor"])
def test_missing_or_corrupt_registered_external_backup_blocks_every_namespace(tmp_path, mode):
    profile, con = synthetic(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    try:
        result = create(con, profile, "before", external=external)
        if mode == "missing":
            result["path"].unlink()
        elif mode == "corrupt":
            with result["path"].open("ab") as stream:
                stream.write(b"unexpected")
        elif mode == "wal":
            result["path"].with_name(result["path"].name + "-wal").write_bytes(b"unmaterialized")
        else:
            result["descriptor_path"].write_bytes(b"{}")
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
    finally:
        con.close()


def test_idempotent_complete_backup_and_different_parameters_conflict(tmp_path):
    profile, con = synthetic(tmp_path)
    backup_id = str(uuid.uuid4())
    try:
        result = create(con, profile, "before", backup_id=backup_id)
        before = snapshot(tmp_path)
        replay = create(con, profile, "before", backup_id=backup_id)
        assert replay["replayed"] is True
        assert snapshot(tmp_path) == before
        with failure("BACKUP_OPERATION_CONFLICT"):
            create(con, profile, "different", backup_id=backup_id)
        with failure("BACKUP_OPERATION_CONFLICT"):
            create(con, profile, "before", backup_id=backup_id, kind="manual")
        assert result["path"].is_file()
    finally:
        con.close()


@pytest.mark.parametrize("failure_stage", ["descriptor", "ready"])
def test_reserved_retry_only_recovers_an_already_complete_original_image(tmp_path, monkeypatch, failure_stage):
    profile, con = synthetic(tmp_path)
    backup_id = str(uuid.uuid4())
    real_write = backups._write_json
    def fail_publish(path, value, **kwargs):
        if failure_stage == "descriptor" and str(path).endswith(backups.SUFFIX) or failure_stage == "ready" and value.get("state") == "ready":
            raise OSError("synthetic interrupted publish")
        return real_write(path, value, **kwargs)
    try:
        monkeypatch.setattr(backups, "_write_json", fail_publish)
        with pytest.raises(OSError):
            create(con, profile, "before", backup_id=backup_id)
        monkeypatch.setattr(backups, "_write_json", real_write)
        assert record(profile, backup_id)[1]["state"] == "reserved"
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
        (profile.reports / "checklist.manifest.json").write_text(json.dumps({"content_file": LOCAL_KEY}))
        if failure_stage == "descriptor":
            with failure("BACKUP_RESERVED_INCOMPLETE"):
                create(con, profile, "before", backup_id=backup_id)
            assert record(profile, backup_id)[1]["state"] == "reserved"
        else:
            replay = create(con, profile, "before", backup_id=backup_id)
            assert replay["replayed"] is True
            assert record(profile, backup_id)[1]["state"] == "ready"
            found = discover(profile)["backups"][0]
            assert found["frozen_manifests"]["checklist"] == {"state": "absent"}
    finally:
        con.close()


@pytest.mark.parametrize("target", ["source", "wal", "config", "media", "report", "checkpoint", "catalog", "outbox", "identity"])
def test_sensitive_destinations_are_rejected_before_creating_catalog(tmp_path, target):
    profile, con = synthetic(tmp_path)
    choices = {"source": profile.database, "wal": profile.database.with_name("reno.db-wal"),
               "config": tmp_path / "config.local.json", "media": profile.media / "backup.db",
               "report": profile.reports / "backup.db", "checkpoint": profile.checkpoints / "backup.db",
               "catalog": profile.database.parent / backups.CATALOG / "backup.db",
               "outbox": profile.database.parent / "sync-outbox/backup.db",
               "identity": profile.database.parent / "node-identity.json"}
    try:
        before = snapshot(tmp_path)
        with failure("BACKUP_INVALID_DESTINATION"):
            backups.create(con, choices[target], profile=profile)
        assert snapshot(tmp_path) == before
    finally:
        con.close()


def test_existing_destination_is_not_overwritten(tmp_path):
    profile, con = synthetic(tmp_path)
    target = profile.database.parent / "before.db"
    target.write_bytes(b"user-owned")
    try:
        with failure("BACKUP_DESTINATION_EXISTS"):
            create(con, profile, "before")
        assert target.read_bytes() == b"user-owned"
    finally:
        con.close()


def test_retirement_partial_failure_preserves_dependencies_and_retries_exact_set(tmp_path, monkeypatch):
    profile, con = synthetic(tmp_path)
    (profile.reports / "checklist.manifest.json").write_text(json.dumps({"content_file": LOCAL_KEY}))
    operation_id = str(uuid.uuid4())
    try:
        old = create(con, profile, "old")
        latest = create(con, profile, "latest")
        real_unlink = backups.safe._Directory.unlink
        def fail_sidecar(directory, name):
            if name == old["descriptor_path"].name:
                raise OSError("synthetic sidecar removal failure")
            return real_unlink(directory, name)
        monkeypatch.setattr(backups.safe._Directory, "unlink", fail_sidecar)
        result = backups.retain_recent(profile, keep=1, operation_id=operation_id)
        assert result == {"retired": [], "blocked": [old["backup_id"]]}
        assert not old["path"].exists() and old["descriptor_path"].exists()
        assert record(profile, old["backup_id"])[1]["state"] == "retiring"
        found = discover(profile)
        assert set(found["blockers"]) == set(backups.NAMESPACES)
        assert any(r["storage_key"] == LOCAL_KEY for b in found["backups"] for r in b["dependencies"])
        with failure("BACKUP_RETIRE_OPERATION_CONFLICT"):
            backups.retain_recent(profile, keep=2, operation_id=operation_id)
        with failure("BACKUP_RETIRE_OPERATION_CONFLICT"):
            backups.retain_recent(profile, keep=1, operation_id=str(uuid.uuid4()))
        newest = create(con, profile, "newest")
        monkeypatch.setattr(backups.safe._Directory, "unlink", real_unlink)
        result = backups.retain_recent(profile, keep=1, operation_id=operation_id)
        assert result == {"retired": [old["backup_id"]], "blocked": []}
        assert latest["path"].exists() and newest["path"].exists()
        assert not old["descriptor_path"].exists()
        assert backups.retain_recent(profile, keep=1, operation_id=operation_id) == result
        with failure("BACKUP_RETIRE_OPERATION_CONFLICT"):
            backups.retain_recent(profile, keep=2, operation_id=operation_id)
        assert discover(profile)["blockers"] == {}
    finally:
        con.close()


def test_retention_does_not_delete_unknown_legacy_or_other_kinds(tmp_path):
    profile, con = synthetic(tmp_path)
    legacy = profile.database.parent / "reno.db.bak-judge-old"
    shutil.copyfile(profile.database, legacy)
    try:
        manual = create(con, profile, "manual", kind="manual")
        old = create(con, profile, "old")
        new = create(con, profile, "new")
        result = backups.retain_recent(profile, keep=1, operation_id=str(uuid.uuid4()))
        assert result["retired"] == [old["backup_id"]]
        assert manual["path"].exists() and legacy.exists() and new["path"].exists()
    finally:
        con.close()


def test_retirement_replaced_file_is_not_deleted(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        old = create(con, profile, "old")
        create(con, profile, "new")
        old["path"].unlink()
        old["path"].write_bytes(b"foreign replacement")
        result = backups.retain_recent(profile, keep=1, operation_id=str(uuid.uuid4()))
        assert result == {"retired": [], "blocked": [old["backup_id"]]}
        assert old["path"].read_bytes() == b"foreign replacement"
    finally:
        con.close()


def test_retirement_records_do_not_remove_archive_boundaries_without_verifier(tmp_path):
    profile, con = synthetic(tmp_path)
    checkpoint, operation = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        con.execute("INSERT INTO sync_checkpoint VALUES(?,?,?,?)", (checkpoint, checkpoint + ".sqlite", SHA, 123))
        con.execute("INSERT INTO sync_checkpoint_retirement VALUES(?,?,?,?,?,?)", ("publication", checkpoint, operation, checkpoint + ".sqlite", SHA, 123))
        con.execute("INSERT INTO storage_gc_operation VALUES(?,?,?,?,?,?,?,?)", (operation, "completed", SHA, SHA, SHA, "{}", "{}", "{}"))
        con.commit()
        create(con, profile, "before")
        found = discover(profile)["backups"][0]
        assert found["checkpoint_boundaries"][0]["checkpoint_id"] == checkpoint
        assert found["retirement_facts"][0]["operation"]["request_json"] == "{}"
        assert found["blockers"]["checkpoint"] == ["retirement_unproven"]
    finally:
        con.close()


def test_sql_views_or_unknown_schema_fail_closed_without_executing_view(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        con.executescript("""
            DROP TABLE sync_report;
            CREATE VIEW sync_report AS WITH RECURSIVE bomb(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM bomb)
            SELECT n AS storage_key,n AS sha256,n AS byte_size FROM bomb;
        """)
        create(con, profile, "before")
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
    finally:
        con.close()


def test_bad_catalog_fields_are_a_blocker_and_never_a_delete_target(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        first = create(con, profile, "first")
        create(con, profile, "second")
        path, value = record(profile, first["backup_id"])
        del value["destination"]
        path.write_text(json.dumps(value))
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
        with failure("BACKUP_INVALID_CATALOG"):
            backups.retain_recent(profile, keep=1, operation_id=str(uuid.uuid4()))
        assert first["path"].exists()
    finally:
        con.close()


def test_discovery_requires_guard_and_has_no_implicit_catalog_creation(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        before = snapshot(tmp_path)
        with failure("STORAGE_GUARD_REQUIRED"):
            backups.discover_locked(profile)
        with lifecycle.shared(profile):
            assert backups.discover_locked(profile)["backups"] == []
        assert snapshot(tmp_path) == before
    finally:
        con.close()


def test_backup_holds_source_guard_until_ready_catalog_is_written(tmp_path, monkeypatch):
    profile, con = synthetic(tmp_path)
    seen = []
    write = backups._write_json
    def guarded_write(path, value, **kwargs):
        assert lifecycle.require_guard(profile).verify()
        seen.append(value.get("state", "descriptor"))
        return write(path, value, **kwargs)
    monkeypatch.setattr(backups, "_write_json", guarded_write)
    try:
        create(con, profile, "before")
        assert seen == ["reserved", "descriptor", "ready"]
        assert lifecycle.current_guard() is None
    finally:
        con.close()


def test_foreign_connection_cannot_write_a_profile_catalog(tmp_path):
    profile, con = synthetic(tmp_path / "first")
    other, other_con = synthetic(tmp_path / "second")
    try:
        before = snapshot(tmp_path)
        with failure("BACKUP_PROFILE_MISMATCH"):
            create(con, other, "before")
        assert snapshot(tmp_path) == before
    finally:
        other_con.close()
        con.close()


@pytest.mark.parametrize("damage", ["future_schema", "missing_table", "bad_pin", "missing_sha_match"])
def test_invalid_metadata_or_unknown_schema_cannot_hide_dependencies(tmp_path, damage):
    profile, con = synthetic(tmp_path)
    try:
        if damage == "future_schema":
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(migrations.SCHEMA_VERSION + 1),))
        elif damage == "missing_table":
            con.execute("DROP TABLE sync_report")
        elif damage == "bad_pin":
            con.execute("INSERT INTO sync_upload_pin VALUES('unknown','future',NULL)")
        else:
            con.execute("UPDATE video_asset SET content_sha256=?", ("e" * 64,))
        con.commit()
        create(con, profile, "before")
        assert discover(profile)["blockers"]
    finally:
        con.close()


def test_ordinary_maintenance_logs_do_not_mimic_nested_backup_directories(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        (profile.database.parent / "service-latest.log").write_text("synthetic log")
        (profile.database.parent / "maintenance-latest.json").write_text("{}")
        assert discover(profile)["blockers"] == {}
    finally:
        con.close()


def test_catalog_size_limit_becomes_explicit_blocker(tmp_path, monkeypatch):
    profile, con = synthetic(tmp_path)
    try:
        create(con, profile, "first")
        create(con, profile, "second")
        monkeypatch.setattr(backups, "MAX_BACKUPS", 1)
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
    finally:
        con.close()


def test_hardlinked_backup_image_is_rejected(tmp_path):
    profile, con = synthetic(tmp_path)
    try:
        first = create(con, profile, "first")
        alias = tmp_path / "hardlink.db"
        os.link(first["path"], alias)
        assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
        assert first["path"].exists() and alias.exists()
    finally:
        con.close()


@pytest.mark.skipif(os.name == "nt", reason="Creating Windows symlinks requires extra privilege")
@pytest.mark.parametrize("target", ["catalog", "maintenance", "destination"])
def test_symlink_paths_fail_closed_and_no_target_mutation(tmp_path, target):
    profile, con = synthetic(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        if target == "catalog":
            (profile.database.parent / backups.CATALOG).symlink_to(outside, target_is_directory=True)
            assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
        elif target == "maintenance":
            (profile.database.parent / "maintenance-outside").symlink_to(outside, target_is_directory=True)
            assert set(discover(profile)["blockers"]) == set(backups.NAMESPACES)
        else:
            linked = tmp_path / "linked"
            linked.symlink_to(outside, target_is_directory=True)
            with pytest.raises(ValueError):
                create(con, profile, "before", external=linked)
        assert list(outside.iterdir()) == []
    finally:
        con.close()
