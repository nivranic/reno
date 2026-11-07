"""Journaled collection of synthetic TEMP artifacts, never a real library."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from contextlib import contextmanager
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

from reno import storage_gc as gc, storage_gc_contract as contract
from reno import storage_lifecycle as lifecycle, storage_roots as roots, storage_backups as backups
from reno import storage_readonly as readonly, sync_checkpoints as checkpoints
from tests.test_storage_roots_group import libraries, enroll, registered, scan  # noqa: F401
from tests.test_storage_checkpoint_roots import archive
from tests.test_sync_sources import BV, CONTENT, resolution


def preview(env, policy=None):
    return roots.preview([env["profile"]], policy=policy)["preview_sha256"]


def apply(env, policy=None, *, operation_id=None, expected=None):
    operation_id = operation_id or str(uuid.uuid4())
    return gc.apply([env["profile"]], operation_id=operation_id,
        expected_preview_sha256=expected or preview(env, policy), policy=policy)


def operation(env, identifier):
    return dict(env["con"].execute("SELECT * FROM storage_gc_operation WHERE operation_id=?", (identifier,)).fetchone())


def test_real_deletion_and_terminal_uuid_replay_do_not_expand(libraries):
    env = libraries()
    enroll(env)
    path, file = registered(env)
    unknown = path.with_name("c" * 64 + ".mp4")
    unknown.write_bytes(b"unknown keep")
    identifier, digest = str(uuid.uuid4()), preview(env)
    first = apply(env, operation_id=identifier, expected=digest)
    assert first["state"] == "completed" and first["deleted_bytes"] == file["byte_size"]
    assert not path.exists() and unknown.read_bytes() == b"unknown keep"
    later, _ = registered(env, content=b"new candidate beyond old selection")
    assert apply(env, operation_id=identifier, expected=digest) == first
    assert gc.resume([env["profile"]], operation_id=identifier) == first and later.exists()
    assert env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 1


def test_current_references_and_unknown_files_are_never_selected(libraries):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
        (json.dumps({"poster": "media/" + item["storage_key"]}),))
    env["con"].commit()
    result = apply(env)
    assert result["selected_count"] == 0 and result["deleted_bytes"] == 0 and path.exists()


@pytest.mark.parametrize("change", ["reference", "policy", "inode"])
def test_stale_preview_never_writes_journal_or_unlinks(libraries, change):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    digest = preview(env)
    policy = None
    if change == "reference":
        env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
            (json.dumps({"video": "media/" + item["storage_key"]}),))
        env["con"].commit()
    elif change == "policy":
        policy = contract.default_policy(namespaces=["local_report"])
    else:
        payload = path.read_bytes()
        path.unlink()
        path.write_bytes(payload)
    with pytest.raises(gc.GCError, match="GC_PREVIEW_CHANGED"):
        apply(env, expected=digest, policy=policy)
    assert path.exists() and env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["digest", "policy"])
def test_same_operation_uuid_cannot_change_parameters(libraries, change):
    env = libraries()
    enroll(env)
    registered(env)
    identifier, digest = str(uuid.uuid4()), preview(env)
    first = apply(env, operation_id=identifier, expected=digest)
    with pytest.raises(gc.GCError, match="GC_OPERATION_CONFLICT"):
        apply(env, operation_id=identifier, expected="d" * 64 if change == "digest" else digest,
              policy=contract.default_policy(keep_publication=1) if change == "policy" else None)
    assert gc.resume([env["profile"]], operation_id=identifier) == first


def test_journal_commit_precedes_unlink_and_sha_has_no_sql_writer(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, file = registered(env)
    observed = []
    original = gc._unlink
    def inspect(con, profile, frozen, check, *, request, restored_ctime=None):
        assert not con.in_transaction and lifecycle.require_guard(profile, exclusive=True)
        peer = sqlite3.connect(profile.database, timeout=0)
        try:
            peer.execute("BEGIN IMMEDIATE")
            assert peer.execute("SELECT state FROM storage_gc_operation").fetchone()[0] == "planned"
            assert peer.execute("SELECT state FROM storage_gc_item").fetchone()[0] == "pending"
            peer.rollback()
        finally:
            peer.close()
        observed.append(frozen["artifact_id"])
        return original(con, profile, frozen, check, request=request, restored_ctime=restored_ctime)
    monkeypatch.setattr(gc, "_unlink", inspect)
    result = apply(env)
    assert observed == [file["artifact_id"]] and not path.exists() and result["deleted_bytes"] == file["byte_size"]


def test_after_journal_crash_resume_reuses_exact_selected_set(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier, digest = str(uuid.uuid4()), preview(env)
    stopped = RuntimeError("synthetic crash after journal")
    original = gc._execute
    monkeypatch.setattr(gc, "_execute", lambda *a: (_ for _ in ()).throw(stopped))
    with pytest.raises(RuntimeError) as caught:
        apply(env, operation_id=identifier, expected=digest)
    assert caught.value is stopped and path.exists() and operation(env, identifier)["state"] == "planned"
    later, _ = registered(env, content=b"unselected later content")
    monkeypatch.setattr(gc, "_execute", original)
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert not path.exists() and later.exists() and result["selected_count"] == 1
    request = contract.decode(operation(env, identifier)["request_json"], canonical=True)
    assert [file["artifact_id"] for file in request["files"]] == [item["artifact_id"]]


def test_resume_new_current_root_retains_original_file(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    original = gc._execute
    monkeypatch.setattr(gc, "_execute", lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic crash")))
    with pytest.raises(RuntimeError):
        apply(env, operation_id=identifier)
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    env["con"].commit()
    monkeypatch.setattr(gc, "_execute", original)
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and result["deleted_bytes"] == 0 and path.exists()
    assert result["items"][0]["state"] == "retained"


def test_unlink_failure_blocks_without_reverting_journal_and_can_resume(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    original = gc._unlink
    monkeypatch.setattr(gc, "_unlink", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("synthetic unlink denial")))
    result = apply(env, operation_id=identifier)
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0 and path.exists()
    assert result["items"][0]["reason_code"] == "GC_UNLINK_FAILED"
    request = operation(env, identifier)["request_json"]
    monkeypatch.setattr(gc, "_unlink", original)
    resumed = gc.resume([env["profile"]], operation_id=identifier)
    assert resumed["state"] == "completed" and resumed["deleted_bytes"] == item["byte_size"] and not path.exists()
    assert operation(env, identifier)["request_json"] == request


def test_missing_after_unlink_crash_does_not_invent_freed_bytes(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    identifier = str(uuid.uuid4())
    original = gc._set_item
    def crash(con, profile, request, file, state, reason, check, **kwargs):
        if state == "deleted":
            raise RuntimeError("synthetic crash after unlink")
        return original(con, profile, request, file, state, reason, check, **kwargs)
    monkeypatch.setattr(gc, "_set_item", crash)
    with pytest.raises(RuntimeError):
        apply(env, operation_id=identifier)
    assert not path.exists()
    monkeypatch.setattr(gc, "_set_item", original)
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and result["deleted_bytes"] == 0
    assert result["items"][0]["state"] == "missing"


def test_owner_sql_change_after_capture_refuses_journal(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    digest = preview(env)
    original = gc._journal
    def changed(con, profile, captured, request, check):
        env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
            (json.dumps({"video": "media/" + item["storage_key"]}),))
        env["con"].commit()
        return original(con, profile, captured, request, check)
    monkeypatch.setattr(gc, "_journal", changed)
    with pytest.raises(gc.GCError, match="GC_OWNER_SQL_CHANGED"):
        apply(env, expected=digest)
    assert path.exists() and env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_capture_verification_is_not_ignored_before_journal(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    digest = preview(env)
    original = readonly.CommittedImage.verify_source if hasattr(readonly, "CommittedImage") else None
    assert original is not None
    def changed(*args):
        raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True)
    monkeypatch.setattr(readonly.CommittedImage, "verify_source", changed)
    with pytest.raises((readonly.ReadonlyImageError, roots.RootError)):
        apply(env, expected=digest)
    assert path.exists() and env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_foreign_member_and_old_backup_hold_media(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    path, item = registered(a)
    b["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('foreign',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    b["con"].commit()
    first = apply(a)
    assert first["selected_count"] == 0 and path.exists()
    b["con"].execute("DELETE FROM video_asset")
    b["con"].commit()
    a["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('backup',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    a["con"].commit()
    backups.create(a["con"], a["profile"].database.with_name("reno.db.bak-judge-gc"), profile=a["profile"], kind="judge")
    a["con"].execute("DELETE FROM video_asset")
    a["con"].commit()
    second = apply(a)
    assert second["selected_count"] == 0 and path.exists()


def test_one_namespace_blocked_still_deletes_independent_report(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    media, _ = registered(a)
    report, item = registered(a, "local_report")
    b["con"].close()
    b["profile"].database.unlink()
    result = apply(a)
    assert media.exists() and not report.exists() and result["selected_count"] == 1
    assert result["deleted_bytes"] == item["byte_size"]


def test_unknown_membership_never_deletes_even_registered_file(libraries):
    env = libraries()
    enroll(env, complete=False)
    path, _ = registered(env)
    result = apply(env)
    assert path.exists() and result["selected_count"] == 0


@pytest.mark.parametrize("owned", [False, True])
def test_archive_retirement_is_atomic_and_original_proof_survives_physical_stage(libraries, owned):
    env = libraries()
    enroll(env)
    older = archive(env, 0, owned=owned)
    newer = archive(env, 1)
    policy = contract.default_policy(keep_publication=1)
    result = apply(env, policy)
    assert result["retired_count"] == 1 and newer["path"].exists()
    assert older["path"].exists() is (not owned)
    with lifecycle.shared(env["profile"]), readonly.committed_image_locked(env["profile"]) as captured:
        proof = contract.verify_retirement(captured.connection, older["entry"],
            expected_library_id=env["library_id"], expected_member_id=env["member_id"],
            expected_root_id=env["root_ids"]["checkpoint"])
        assert proof.status == "proven"
    with pytest.raises(Exception) as caught:
        checkpoints._assert_not_retired(env["con"], older["entry"])
    assert caught.value.code == "SYNC_CHECKPOINT_RETIRED"


def test_retired_archive_held_by_backup_has_explicit_retain_item(libraries):
    env = libraries()
    enroll(env)
    older = archive(env, 0)
    archive(env, 1)
    backups.create(env["con"], env["profile"].database.with_name("reno.db.bak-judge-gc"), profile=env["profile"], kind="judge")
    policy = contract.default_policy(keep_publication=1)
    result = apply(env, policy)
    assert result["retired_count"] == 1 and older["path"].exists() and older["media"].exists()
    op = operation(env, result["operation_id"])
    request = contract.decode(op["request_json"], canonical=True)
    retired_file = next(item for item in request["files"] if item["artifact_id"] == older["file"]["artifact_id"])
    assert retired_file["intent"] == "retain"
    assert next(item for item in result["items"] if item["artifact_id"] == retired_file["artifact_id"])["state"] == "retained"


@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError, ValueError, RuntimeError])
def test_cancellation_identity_and_no_mutation_before_apply(libraries, exception_type):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    digest = preview(env)
    stopped = exception_type("synthetic cancellation")
    def check():
        raise stopped
    with pytest.raises(exception_type) as caught:
        gc.apply([env["profile"]], expected_preview_sha256=digest, operation_id=str(uuid.uuid4()), check_cancelled=check)
    assert caught.value is stopped and path.exists()
    assert env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_unknown_uuid_resume_does_not_create_operation(libraries):
    env = libraries()
    enroll(env)
    with pytest.raises(gc.GCError, match="GC_OPERATION_NOT_FOUND"):
        gc.resume([env["profile"]], operation_id=str(uuid.uuid4()))
    assert env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_journal_sql_triggers_are_refused_before_files_change(libraries):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    digest = preview(env)
    env["con"].execute("CREATE TRIGGER unexpected_gc AFTER INSERT ON storage_gc_operation BEGIN DELETE FROM video_asset; END")
    env["con"].commit()
    with pytest.raises(gc.GCError, match="GC_SCHEMA_UNSAFE"):
        apply(env, expected=digest)
    assert path.exists() and env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_retirement_insert_failure_rolls_back_all_three_tables(libraries, monkeypatch):
    from reno import migrations
    env = libraries()
    enroll(env)
    older = archive(env, 0)
    newer = archive(env, 1)
    policy = contract.default_policy(keep_publication=1)
    digest = preview(env, policy)
    stopped = sqlite3.OperationalError("synthetic retirement INSERT failure")
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO sync_checkpoint_retirement"):
                raise stopped
            return super().execute(sql, *args)
    def connected(path):
        con = sqlite3.connect(Path(path).as_uri() + "?mode=rw", uri=True, factory=Connection)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        migrations.validate(con)
        return con
    monkeypatch.setattr(gc.db, "connect", connected)
    monkeypatch.setattr(gc, "_unlink", lambda *a, **kw: pytest.fail("Unlink must follow committed journal"))
    with pytest.raises(sqlite3.OperationalError) as caught:
        apply(env, policy, expected=digest)
    assert caught.value is stopped and older["path"].exists() and newer["path"].exists()
    assert all(env["con"].execute("SELECT count(*) FROM " + table).fetchone()[0] == 0 for table in
               ("storage_gc_operation", "storage_gc_item", "sync_checkpoint_retirement"))


def test_resume_does_not_use_new_unjournaled_retirement_proposals(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    older = archive(env, 0, owned=False)
    archive(env, 1)
    path, file = registered(env, content=older["media"].read_bytes(), video=older["media_key"].split("/")[1])
    publication = dict(env["con"].execute("SELECT * FROM sync_publication WHERE publication_id=?",
        (older["entry"]["publication_id"],)).fetchone())
    env["con"].execute("DELETE FROM sync_checkpoint WHERE checkpoint_id=?", (older["entry"]["checkpoint_id"],))
    env["con"].execute("DELETE FROM sync_publication WHERE publication_id=?", (publication["publication_id"],))
    env["con"].commit()
    policy, identifier = contract.default_policy(keep_publication=1), str(uuid.uuid4())
    original = gc._execute
    monkeypatch.setattr(gc, "_execute", lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic journal crash")))
    with pytest.raises(RuntimeError):
        apply(env, policy, operation_id=identifier)
    for table, row in (("sync_publication", publication), ("sync_checkpoint", older["entry"])):
        env["con"].execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    env["con"].commit()
    current = scan(env, policy=policy)
    assert current.proposed_retirements
    assert roots._referenced(file, current.base_inputs) and not roots._referenced(file, current.effective_inputs)
    monkeypatch.setattr(gc, "_execute", original)
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["items"][0]["state"] == "retained" and path.exists()
    assert env["con"].execute("SELECT count(*) FROM sync_checkpoint_retirement").fetchone()[0] == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX private staging closes the public-name swap gap")
def test_swap_after_last_original_stat_preserves_replacement_bytes(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    saved = path.with_name("saved-original")
    original = gc.safe._rename_new
    replaced = []
    def swap(source, name, destination, target):
        if source.path == path.parent and name == path.name and target == "payload" and not replaced:
            path.rename(saved)
            path.write_bytes(b"substituted private user bytes")
            replaced.append(True)
        return original(source, name, destination, target)
    monkeypatch.setattr(gc.safe, "_rename_new", swap)
    result = apply(env)
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert path.read_bytes() == b"substituted private user bytes" and saved.read_bytes() == b"unused media"
    assert not list(env["profile"].media.rglob("payload"))


@pytest.mark.skipif(os.name != "nt", reason="Windows same DELETE handle pins the exact verified object")
def test_windows_disposition_does_not_reopen_an_unverified_path(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    original = gc._dispose_windows
    attempts = []
    def pinned(fd):
        with pytest.raises(OSError):
            path.rename(path.with_name("saved-original"))
        attempts.append(True)
        return original(fd)
    monkeypatch.setattr(gc, "_dispose_windows", pinned)
    result = apply(env)
    assert result["state"] == "completed" and attempts == [True] and not path.exists()


_CRASH = """
import json, os, sys
from reno import storage_gc as gc, storage_lifecycle as lifecycle
args = json.loads(sys.argv[1])
profile = lifecycle.StorageProfile(**args['profile'])
if args['phase'] == 'journal':
    gc._execute = lambda *a: os._exit(23)
elif args['phase'] in ('staged', 'restore'):
    original = gc.safe._rename_new
    def after_move(source, name, destination, target):
        result = original(source, name, destination, target)
        if target == 'payload' and args['phase'] == 'staged' or name == 'payload' and args['phase'] == 'restore':
            os._exit(23)
        return result
    gc.safe._rename_new = after_move
else:
    original = gc._set_item
    def after_unlink(con, profile, request, file, state, reason, check, **kwargs):
        if state == 'deleted':
            os._exit(23)
        return original(con, profile, request, file, state, reason, check, **kwargs)
    gc._set_item = after_unlink
if args['phase'] == 'restore':
    gc.resume([profile], operation_id=args['operation_id'])
else:
    gc.apply([profile], expected_preview_sha256=args['digest'], operation_id=args['operation_id'])
"""


def _crash_child(env, identifier, phase):
    profile = env["profile"]
    args = {"profile": {key: str(getattr(profile, key)) for key in ("database", "media", "reports", "checkpoints")},
        "operation_id": identifier, "digest": preview(env), "phase": phase}
    child = subprocess.Popen([sys.executable, "-B", "-u", "-c", _CRASH, json.dumps(args)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        output = child.communicate(timeout=45)[0]
        assert child.returncode == 23, output.decode("utf-8", "replace")
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=3)


@pytest.mark.parametrize("phase", ["journal", "staged", "unlinked"])
def test_real_process_exit_releases_ex_and_resumes_original_journal(libraries, phase):
    if phase == "staged" and os.name == "nt":
        pytest.skip("Windows deletes the verified handle without POSIX staging")
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    _crash_child(env, identifier, phase)
    op = operation(env, identifier)
    assert op["state"] == "planned"
    later, _ = registered(env, content=b"later unselected")
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and not path.exists() and later.exists()
    assert result["selected_count"] == 1
    assert result["deleted_bytes"] == (0 if phase == "unlinked" else item["byte_size"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX staged-rename crash recovery")
@pytest.mark.parametrize("original_exists", [False, True])
def test_staged_resume_new_reference_restores_or_preserves_without_overwrite(libraries, original_exists):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    _crash_child(env, identifier, "staged")
    assert not path.exists()
    if original_exists:
        path.write_bytes(b"newer bytes at original path, preserve")
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    env["con"].commit()
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and result["items"][0]["state"] == "retained" and result["deleted_bytes"] == 0
    if original_exists:
        assert path.read_bytes() == b"newer bytes at original path, preserve"
        assert len(list(env["profile"].media.rglob("payload"))) == 1
        assert result["items"][0]["reason_code"] == "GC_CURRENT_REFERENCE_STAGED"
    else:
        assert path.read_bytes() == b"unused media" and not list(env["profile"].media.rglob("payload"))
        current = scan(env)
        assert next(file for file in current.files if file["artifact_id"] == item["artifact_id"])["status"] == "referenced"


@pytest.mark.skipif(os.name == "nt", reason="POSIX post-unlink directory fsync failure")
def test_observed_unlink_bytes_survive_post_delete_fsync_failure(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    unlinked = []
    original_unlink, original_fsync = gc.safe._Directory.unlink, gc.os.fsync
    def observe(directory, name):
        result = original_unlink(directory, name)
        if name == "payload":
            unlinked.append(True)
        return result
    def failure(fd):
        if unlinked:
            unlinked.clear()
            raise OSError("synthetic directory fsync failure after real unlink")
        return original_fsync(fd)
    monkeypatch.setattr(gc.safe._Directory, "unlink", observe)
    monkeypatch.setattr(gc.os, "fsync", failure)
    result = apply(env)
    assert not path.exists() and result["deleted_bytes"] == item["byte_size"]
    assert result["items"][0]["state"] == "deleted"
    assert result["items"][0]["reason_code"] == "GC_DELETE_FINISH_UNCONFIRMED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX cancellation after staged rename")
@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError, ValueError, RuntimeError])
def test_cancel_after_move_keeps_original_stage_and_resume_finishes(libraries, monkeypatch, exception_type):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier, digest = str(uuid.uuid4()), preview(env)
    moved, original = [], gc.safe._rename_new
    def observe(source, name, destination, target):
        result = original(source, name, destination, target)
        if target == "payload":
            moved.append(True)
        return result
    monkeypatch.setattr(gc.safe, "_rename_new", observe)
    stopped = exception_type("synthetic cancellation after successful move")
    def check():
        if moved:
            raise stopped
    with pytest.raises(exception_type) as caught:
        gc.apply([env["profile"]], operation_id=identifier, expected_preview_sha256=digest, check_cancelled=check)
    assert caught.value is stopped and not path.exists()
    assert len(list(env["profile"].media.rglob("payload"))) == 1
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and result["deleted_bytes"] == item["byte_size"]
    assert not list(env["profile"].media.rglob("payload"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX durable restore authorization")
@pytest.mark.parametrize("failure", ["cancel", "process_exit"])
def test_restore_rename_gap_recovers_and_original_uuid_can_delete_after_reference_removed(libraries, monkeypatch, failure):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    _crash_child(env, identifier, "staged")
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    env["con"].commit()
    if failure == "process_exit":
        _crash_child(env, identifier, "restore")
    else:
        restored, original = [], gc.safe._rename_new
        def observe(source, name, destination, target):
            result = original(source, name, destination, target)
            if name == "payload":
                restored.append(True)
            return result
        monkeypatch.setattr(gc.safe, "_rename_new", observe)
        stopped = OSError("synthetic cancellation after restore rename")
        def check():
            if restored:
                raise stopped
        with pytest.raises(OSError) as caught:
            gc.resume([env["profile"]], operation_id=identifier, check_cancelled=check)
        assert caught.value is stopped
    assert path.read_bytes() == b"unused media" and not list(env["profile"].media.rglob("payload"))
    assert len(list(env["profile"].media.rglob("restore.json"))) == 1
    env["con"].execute("DELETE FROM video_asset")
    env["con"].commit()
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "completed" and not path.exists() and result["deleted_bytes"] == item["byte_size"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX stage lineage cannot authorize a replacement inode")
@pytest.mark.parametrize("damage", ["new_inode", "restore_marker"])
def test_restore_lineage_never_adopts_changed_inode_or_unknown_marker(libraries, damage):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    identifier = str(uuid.uuid4())
    _crash_child(env, identifier, "staged")
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('video',?)",
        (json.dumps({"video": "media/" + item["storage_key"]}),))
    env["con"].commit()
    _crash_child(env, identifier, "restore")
    env["con"].execute("DELETE FROM video_asset")
    env["con"].commit()
    if damage == "new_inode":
        previous = path.with_name("saved-original")
        path.rename(previous)
        path.write_bytes(b"unused media")
    else:
        next(env["profile"].media.rglob("restore.json")).write_bytes(b"untrusted restore authorization")
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert path.read_bytes() == b"unused media"


def test_special_database_path_is_excluded_even_with_compatible_artifact_row(libraries, tmp_path):
    checkpoint_root = tmp_path / "special/checkpoints"
    checkpoint_name = str(uuid.uuid4()) + ".sqlite"
    env = libraries("special", checkpoints=checkpoint_root, database=checkpoint_root / checkpoint_name)
    enroll(env)
    profile = env["profile"]
    info, root_info = profile.database.stat(), profile.checkpoints.stat()
    file = {"namespace": "checkpoint", "root_id": env["root_ids"]["checkpoint"],
        "root_device": str(root_info.st_dev), "root_inode": str(root_info.st_ino), "storage_key": checkpoint_name,
        "sha256": hashlib.sha256(profile.database.read_bytes()).hexdigest(), "byte_size": info.st_size,
        "file_device": str(info.st_dev), "file_inode": str(info.st_ino),
        "file_mtime_ns": str(info.st_mtime_ns), "file_ctime_ns": str(info.st_ctime_ns)}
    file["artifact_id"] = "art_" + contract.digest({key: file[key] for key in ("namespace", "root_id", "storage_key")})
    row = {**file, "created_at": "2026-10-01T00:00:00.000Z", "updated_at": "2026-10-01T00:00:00.000Z"}
    env["con"].execute(f"INSERT INTO storage_artifact({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    env["con"].commit()
    report, item = registered(env, "local_report")
    result = apply(env)
    assert profile.database.exists() and not report.exists()
    assert result["selected_count"] == 1 and result["deleted_bytes"] == item["byte_size"]


_SOURCE_WRITER = """
import json, pathlib, sys
from reno import storage_lifecycle as lifecycle, sync_sources as sources
from reno.sync_protocol import SyncError
args = json.loads(sys.argv[1])
profile = lifecycle.StorageProfile(**args['profile'])
store = sources.SourceMap(profile.database.parent, profile=profile,
    source_library_id=args['library_id'], source_node_id=args['node_id'])
pathlib.Path(args['started']).write_text('started', encoding='ascii')
try:
    store.record(args['resolution'], extracted_video_id=args['video_id'], asset=args['asset'])
    print('published')
except SyncError as exc:
    print(exc.code)
"""


def test_real_source_writer_waits_for_gc_ex_and_cannot_publish_deleted_media(libraries, tmp_path, monkeypatch):
    env = libraries()
    enroll(env)
    path, item = registered(env, content=CONTENT, video="file_canonical")
    digest = preview(env)
    frozen, release = threading.Event(), threading.Event()
    original = gc._journal
    def paused(con, profile, captured, request, check):
        assert lifecycle.require_guard(profile, exclusive=True)
        frozen.set()
        assert release.wait(10)
        return original(con, profile, captured, request, check)
    monkeypatch.setattr(gc, "_journal", paused)
    profile = env["profile"]
    started = tmp_path / "source-started"
    args = {"profile": {key: str(getattr(profile, key)) for key in ("database", "media", "reports", "checkpoints")},
        "library_id": env["library_id"], "node_id": env["node_id"], "resolution": resolution(), "video_id": BV,
        "asset": {"video_id": "file_canonical", "content_sha256": item["sha256"],
                  "files_json": json.dumps({"video": "media/" + item["storage_key"]})}, "started": str(started)}
    child = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gc.apply, [profile], expected_preview_sha256=digest, operation_id=str(uuid.uuid4()))
        try:
            assert frozen.wait(5)
            child = subprocess.Popen([sys.executable, "-B", "-u", "-c", _SOURCE_WRITER, json.dumps(args)],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 10
            while not started.exists():
                if child.poll() is not None:
                    pytest.fail(child.communicate(timeout=3)[0].decode("utf-8", "replace"))
                assert time.monotonic() < deadline
                time.sleep(0.02)
            time.sleep(0.15)
            assert child.poll() is None and path.exists()
            assert not (profile.database.parent / "sync-sources").exists()
        finally:
            release.set()
        result = future.result(timeout=10)
        try:
            output = child.communicate(timeout=10)[0]
            assert child.returncode == 0, output.decode("utf-8", "replace")
            assert output.strip() == b"SYNC_SOURCE_MEDIA_MISMATCH"
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
    assert result["state"] == "completed" and not path.exists()
    assert not (profile.database.parent / "sync-sources").exists()
