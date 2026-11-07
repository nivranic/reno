"""Checkpoint lifetime/retirement contracts on isolated, empty receiver libraries."""
from concurrent.futures import ThreadPoolExecutor
import json
import uuid

import pytest

from reno import config, db, storage_artifacts, storage_lifecycle as lifecycle
from reno import sync_checkpoints as checkpoints, sync_recovery as recovery
from reno.sync_protocol import SyncError


@pytest.fixture()
def library(tmp_path, monkeypatch):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    monkeypatch.setattr(config, "DATA", profile.database.parent)
    monkeypatch.setattr(config, "MEDIA", profile.media)
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "cloud"})
    db.initialize(profile.database)
    con = db.connect(profile.database)
    try:
        yield con, profile
    finally:
        con.close()


def capture(con, profile, *, commit=True):
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        entry = checkpoints.stage(con, str(uuid.uuid4()), profile=profile)
        con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", (
            entry["publication_id"], str(uuid.uuid4()), str(uuid.uuid4()), 1, 1,
            "a" * 64, "b" * 64, "2026-10-01T00:00:00Z", json.dumps(db.knowledge_version(con))))
        checkpoints.register(con, entry, profile=profile)
        if commit:
            con.commit()
        else:
            con.rollback()
        return entry


def retire(con, profile, entry, *, mismatch=False):
    with lifecycle.exclusive(profile):
        con.execute("BEGIN IMMEDIATE")
        operation = str(uuid.uuid4())
        stamp = "2026-10-01T00:00:00Z"
        con.execute("INSERT INTO storage_gc_operation VALUES(?,?,?,?,?,?,?,?,?,?)", (
            operation, "a" * 64, "b" * 64, "c" * 64, "{}", "{}", "{}", "completed", stamp, stamp))
        con.execute("INSERT INTO sync_checkpoint_retirement VALUES(?,?,?,?,?,?,?)", (
            "recovery" if entry["format_version"] == 2 else "publication", entry["checkpoint_id"], operation,
            entry["storage_key"], "0" * 64 if mismatch else entry["sha256"], entry["byte_size"], stamp))
        con.commit()


def apply(con, profile, entry):
    return recovery.restore(con, entry["checkpoint_id"], expected_version=db.knowledge_version(con),
        expected_control_revision=recovery.state(con)["revision"], operation_id=str(uuid.uuid4()), profile=profile)


def test_missing_facilities_and_existing_snapshot_never_acquire_or_create(library, monkeypatch):
    con, profile = library
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        checkpoints.list_checkpoints(con, profile=profile)
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not profile.media.exists() and not profile.checkpoints.exists()
    lifecycle.initialize(profile)
    con.execute("BEGIN")
    con.execute("SELECT value FROM meta").fetchall()
    monkeypatch.setattr(lifecycle, "shared", lambda *_: pytest.fail("Must not lock below a caller snapshot"))
    for action in (lambda: checkpoints.list_checkpoints(con, profile=profile),
                   lambda: checkpoints.preview(con, str(uuid.uuid4()), profile=profile),
                   lambda: recovery.preview_restore(con, str(uuid.uuid4()), profile=profile)):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            action()
        assert caught.value.code == "STORAGE_GUARD_REQUIRED" and con.in_transaction
    con.rollback()


def test_stage_requires_existing_guard_and_registered_artifact_rolls_back(library):
    con, profile = library
    lifecycle.initialize(profile)
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        checkpoints.stage(con, str(uuid.uuid4()), profile=profile)
    assert caught.value.code == "STORAGE_GUARD_REQUIRED" and con.in_transaction
    con.rollback()
    orphan = capture(con, profile, commit=False)
    assert (profile.checkpoints / orphan["storage_key"]).is_file()
    assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
    assert checkpoints.list_checkpoints(con, profile=profile) == []
    entry = capture(con, profile)
    with lifecycle.shared(profile):
        row = con.execute("SELECT artifact_id FROM storage_artifact WHERE namespace='checkpoint'").fetchone()
        artifact = storage_artifacts.verify(con, row[0], profile=profile, root=profile.checkpoints)
        assert artifact["storage_key"] == entry["storage_key"] and artifact["sha256"] == entry["sha256"]


def test_artifact_failure_cannot_leave_a_registered_checkpoint_in_caller_transaction(library, monkeypatch):
    con, profile = library
    lifecycle.initialize(profile)
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        entry = checkpoints.stage(con, str(uuid.uuid4()), profile=profile)
        con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", (
            entry["publication_id"], str(uuid.uuid4()), str(uuid.uuid4()), 1, 1,
            "a" * 64, "b" * 64, "2026-10-01T00:00:00Z", json.dumps(db.knowledge_version(con))))
        def fail(*_args):
            raise OSError("synthetic ownership registration failure")
        monkeypatch.setattr(checkpoints, "_register_artifact", fail)
        with pytest.raises(OSError, match="synthetic"):
            checkpoints.register(con, entry, profile=profile)
        assert con.in_transaction
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM sync_checkpoint").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
        con.rollback()


def test_read_and_restore_lock_before_begin_and_hold_through_protection_registration(library, monkeypatch):
    con, profile = library
    lifecycle.initialize(profile)
    entry = capture(con, profile)
    observations = []
    con.set_trace_callback(lambda sql: observations.append(lifecycle.current_guard() is not None)
                           if sql.startswith("BEGIN") else None)
    assert checkpoints.preview(con, entry["checkpoint_id"], profile=profile)["recoverable"]
    original = checkpoints._register_artifact
    def exclusive_attempt():
        with pytest.raises(lifecycle.StorageLifecycleError) as caught, lifecycle.exclusive(profile, timeout=0):
            pass
        assert caught.value.code == "STORAGE_BUSY"
    def record(connection, descriptor, held):
        assert connection.in_transaction and lifecycle.require_guard(profile)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(exclusive_attempt).result(timeout=5)
        return original(connection, descriptor, held)
    monkeypatch.setattr(checkpoints, "_register_artifact", record)
    result = apply(con, profile, entry)
    assert observations and all(observations)
    assert lifecycle.current_guard() is None and not con.in_transaction
    with lifecycle.exclusive(profile, timeout=0):
        assert con.execute("SELECT count(*) FROM storage_artifact WHERE namespace='checkpoint'").fetchone()[0] == 2
    assert result["control"]["state"] == "held"


@pytest.mark.parametrize("mismatch", [False, True])
def test_retirement_is_checked_before_opening_any_checkpoint_file(library, monkeypatch, mismatch):
    con, profile = library
    lifecycle.initialize(profile)
    entry = capture(con, profile)
    retire(con, profile, entry, mismatch=mismatch)
    (profile.checkpoints / entry["storage_key"]).unlink()
    original = checkpoints._Directory.open_file
    def no_archive(self, name, **kwargs):
        assert not name.endswith(".sqlite"), "Retired/ambiguous descriptor must not open archive bytes"
        return original(self, name, **kwargs)
    monkeypatch.setattr(checkpoints._Directory, "open_file", no_archive)
    code = "SYNC_CHECKPOINT_RETIREMENT_MISMATCH" if mismatch else "SYNC_CHECKPOINT_RETIRED"
    rows = checkpoints.list_checkpoints(con, profile=profile)
    assert len(rows) == 1 and not rows[0]["recoverable"]
    assert rows[0]["issues"] == [{"code": code, "target": "archive"}]
    for action in (lambda: checkpoints.preview(con, entry["checkpoint_id"], profile=profile),
                   lambda: recovery.preview_restore(con, entry["checkpoint_id"], profile=profile),
                   lambda: apply(con, profile, entry)):
        with pytest.raises(SyncError) as caught:
            action()
        assert caught.value.code == code
        assert not con.in_transaction
    with pytest.raises(SyncError) as caught, checkpoints.open_verified(con, entry["checkpoint_id"], profile=profile):
        pass
    assert caught.value.code == code
    assert con.execute("SELECT count(*) FROM sync_recovery_operation").fetchone()[0] == 0


def test_recovery_protection_retirement_uses_its_own_origin(library):
    con, profile = library
    lifecycle.initialize(profile)
    entry = capture(con, profile)
    result = apply(con, profile, entry)
    protection = dict(con.execute("SELECT * FROM sync_recovery_checkpoint WHERE checkpoint_id=?",
                                  (result["protection_checkpoint_id"],)).fetchone())
    retire(con, profile, protection)
    rows = recovery.list_recovery_checkpoints(con, profile=profile)
    assert len(rows) == 1 and not rows[0]["recoverable"]
    assert rows[0]["issues"][0]["code"] == "SYNC_CHECKPOINT_RETIRED"
    with pytest.raises(SyncError) as caught:
        recovery.preview_restore(con, protection["checkpoint_id"], checkpoint_kind="recovery", profile=profile)
    assert caught.value.code == "SYNC_CHECKPOINT_RETIRED"
    assert checkpoints.preview(con, entry["checkpoint_id"], profile=profile)["recoverable"]
