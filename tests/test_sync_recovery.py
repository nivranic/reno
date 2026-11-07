"""Logical restores only use isolated synthetic SQLite libraries and media."""
import hashlib
import json
import os
import sqlite3
import uuid

import pytest

from reno import db, personal_schema, review, sync_checkpoints as checkpoints, sync_recovery as recovery, sync_reports
from reno.sync_protocol import SyncError
from tests.test_sync_checkpoints import receiver, seed, publication


@pytest.fixture()
def generations(receiver):
    seed(receiver)
    con = receiver["con"]
    con.execute("UPDATE knowledge_atom SET status='candidate'")
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    checkpoint = checkpoints.stage(con, str(uuid.uuid4()), media_store=receiver["store"])
    # This current event is deliberately newer than the saved materialized
    # status and is not inside the archive; restore must replay it.
    operation = review.record_decision(con, atom_id="atom1", action="confirm", client_id="current-review")
    con.execute("UPDATE knowledge_atom SET claim='新版施工建议',status='candidate' WHERE id='atom1'")
    content = b"new video absent in old image"
    descriptor = {"video_id": "new-video", "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content), "extension": "mp4"}
    key = receiver["store"].receive(descriptor, [content])
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json,status) VALUES('new-video','新来源',?,?,'processed')",
                (descriptor["sha256"], json.dumps({"video": "media/" + key})))
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim) VALUES('new-atom','new-video','只属于新代')")
    con.execute("INSERT INTO meta VALUES('step:new-video:atomize','new marker')")
    db.fts_sync_atoms(con, ["atom1", "new-atom"])
    publication(con, checkpoint["publication_id"])
    checkpoints.register(con, checkpoint)
    con.commit()
    receiver.update(checkpoint=checkpoint, review_event=operation["event_id"])
    return receiver


def apply(env, **overrides):
    con = env["con"]
    args = {"expected_version": db.knowledge_version(con), "expected_control_revision": recovery.state(con)["revision"],
            "operation_id": str(uuid.uuid4()), "media_store": env["store"], "report_root": env["reports"]}
    args.update(overrides)
    return recovery.restore(con, env["checkpoint"]["checkpoint_id"], **args)


def preserved(con):
    tables = tuple(personal_schema.TABLES) + ("user_decision", "job", "task_request", "sync_queue", "sync_pair", "sync_session",
            "sync_publication", "sync_published_video", "sync_queue_publication", "sync_publication_fence")
    return {table: [tuple(row) for row in con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}


def test_full_restore_removes_new_members_replays_current_reviews_and_preserves_private_domains(generations):
    env, con = generations, generations["con"]
    before, version = preserved(con), db.knowledge_version(con)
    node_path = env["path"].parent / "node-identity.json"
    identity = node_path.read_bytes()
    private_files = [env["path"].parent / "sync-state.json", env["path"].parent / "private-config.json",
                     env["path"].parent / "personal-attachments" / "unchanged.pdf"]
    for path in private_files:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"private sentinel must remain")
    result = apply(env)
    assert result["version"]["library_epoch"] == version["library_epoch"]
    assert result["version"]["kb_revision"] > version["kb_revision"]
    assert result["control"]["state"] == "held" and result["control"]["generation"] == result["control"]["revision"] == 1
    assert con.execute("SELECT video_id FROM video_asset").fetchall()[0][0] == "video1"
    assert con.execute("SELECT COUNT(*) FROM video_asset").fetchone()[0] == 1
    assert con.execute("SELECT claim,status FROM knowledge_atom WHERE id='atom1'").fetchone()[:] == ("原建议", "verified")
    assert con.execute("SELECT atom_id FROM atom_fts").fetchall()[0][0] == "atom1"
    assert con.execute("SELECT COUNT(*) FROM atom_fts").fetchone()[0] == 1
    assert not con.execute("SELECT 1 FROM meta WHERE key='step:new-video:atomize'").fetchall()
    assert preserved(con) == before and node_path.read_bytes() == identity
    assert all(path.read_bytes() == b"private sentinel must remain" for path in private_files)
    assert not con.in_transaction
    protect = recovery.list_recovery_checkpoints(con, media_store=env["store"])
    assert len(protect) == 1 and protect[0]["format_version"] == 2 and protect[0]["origin_kind"] == "recovery"
    assert protect[0]["operation_id"] == result["operation_id"] and "publication_id" not in protect[0]
    assert protect[0]["row_counts"]["video_asset"] == 2


def test_preview_is_read_only_and_exposes_both_compare_and_swap_values(generations):
    env, con = generations, generations["con"]
    con.execute("PRAGMA query_only=ON")
    changes, files = con.total_changes, set(env["root"].iterdir())
    value = recovery.preview_restore(con, env["checkpoint"]["checkpoint_id"], media_store=env["store"])
    assert value["recoverable"] and value["control_revision"] == 0
    assert value["current_version"] == db.knowledge_version(con)
    assert value["restore_action"]["current_row_counts"]["video_asset"] == 2
    assert value["restore_action"]["restored_row_counts"]["video_asset"] == 1
    assert con.total_changes == changes and set(env["root"].iterdir()) == files
    assert not con.in_transaction


def test_exact_operation_retry_does_not_restore_twice_or_make_more_protection_files(generations):
    con = generations["con"]
    expected, identifier = db.knowledge_version(con), str(uuid.uuid4())
    first = apply(generations, expected_version=expected, expected_control_revision=0, operation_id=identifier)
    files, version = set(generations["root"].iterdir()), db.knowledge_version(con)
    again = apply(generations, expected_version=expected, expected_control_revision=0, operation_id=identifier)
    assert again == {**first, "replayed": True}
    assert db.knowledge_version(con) == version and set(generations["root"].iterdir()) == files
    with pytest.raises(SyncError) as caught:
        apply(generations, operation_id=identifier)
    assert caught.value.code == "SYNC_RECOVERY_OPERATION_CONFLICT"


@pytest.mark.parametrize("changed", ["knowledge", "control"])
def test_stale_preview_fails_before_any_archive_or_mutation(generations, changed):
    env, con = generations, generations["con"]
    version = db.knowledge_version(con)
    if changed == "knowledge":
        con.execute("UPDATE knowledge_atom SET claim='新的未预览内容' WHERE id='atom1'")
        con.commit()
    expected_control = 0 if changed == "knowledge" else 1
    before, files = preserved(con), set(env["root"].iterdir())
    with pytest.raises(SyncError) as caught:
        apply(env, expected_version=version, expected_control_revision=expected_control)
    assert caught.value.code == ("SYNC_RECOVERY_VERSION_CHANGED" if changed == "knowledge" else "SYNC_RECOVERY_CONTROL_CHANGED")
    assert preserved(con) == before and set(env["root"].iterdir()) == files
    assert not con.in_transaction


def test_resume_remains_fenced_until_a_fresh_publication_commits(generations):
    con = generations["con"]
    applied = apply(generations)
    floor = applied["control"]["source_floor"]
    stream = applied["control"]["stream"]
    with pytest.raises(SyncError) as caught:
        recovery.assert_publish_allowed(con, 1, floor, stream)
    assert caught.value.code == "SYNC_RECOVERY_HELD"
    resume_args = {"expected_version": db.knowledge_version(con), "expected_control_revision": 1, "operation_id": str(uuid.uuid4())}
    resumed = recovery.resume(con, **resume_args)
    assert resumed["control"]["state"] == "resume_pending" and resumed["control"]["generation"] == 1
    assert resumed["control"]["revision"] == 2
    assert recovery.resume(con, **resume_args)["replayed"]
    for generation, source, code in [(0, floor, "SYNC_RECOVERY_GENERATION_MISMATCH"),
                                      (1, floor, "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED")]:
        with pytest.raises(SyncError) as caught:
            recovery.assert_publish_allowed(con, generation, source, stream)
        assert caught.value.code == code
    fresh = {**floor, "kb_revision": floor["kb_revision"] + 1}
    con.execute("BEGIN IMMEDIATE")
    assert recovery.mark_published(con, 1, fresh, stream)["state"] == "running"
    con.rollback()
    assert recovery.state(con)["state"] == "resume_pending"
    con.execute("BEGIN IMMEDIATE")
    assert recovery.mark_published(con, 1, fresh, stream)["revision"] == 3
    con.commit()
    assert recovery.state(con) == {**resumed["control"], "state": "running", "revision": 3}


def test_receiver_epoch_namespaces_recovery_counters_after_physical_restore(generations):
    con = generations["con"]
    first = apply(generations)
    old_stream = first["control"]["stream"]
    floor = first["control"]["source_floor"]
    assert old_stream["library_id"] == db.knowledge_version(con)["library_id"]
    assert old_stream["library_id"] != floor["library_id"]
    # Artificially model the identity/counter consequences of restoring an
    # earlier physical DB, including its required receiver epoch increment.
    con.execute("UPDATE sync_recovery_control SET generation=4,revision=8 WHERE slot=1")
    con.commit()
    previous = recovery.state(con)
    con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    con.execute("UPDATE sync_recovery_control SET state='resume_pending',generation=1,revision=2 WHERE slot=1")
    con.commit()
    current = recovery.state(con)
    assert current["generation"] < previous["generation"] and current["revision"] < previous["revision"]
    assert current["stream"] == {**old_stream, "library_epoch": old_stream["library_epoch"] + 1}
    assert current["source_floor"] == floor
    fresh = {**floor, "kb_revision": floor["kb_revision"] + 1}
    con.execute("BEGIN IMMEDIATE")
    for generation in (1, previous["generation"]):
        with pytest.raises(SyncError) as caught:
            recovery.mark_published(con, generation, fresh, old_stream)
        assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"
        assert recovery.state(con) == current
    assert recovery.mark_published(con, 1, fresh, current["stream"])["state"] == "running"
    con.commit()
    assert recovery.state(con)["stream"] == current["stream"]


def test_failure_fence_requires_running_and_retains_the_original_receiver_stream(generations):
    con = generations["con"]
    control = apply(generations)["control"]
    stream = control["stream"]
    with pytest.raises(SyncError) as caught:
        recovery.assert_recovery_fence(con, 1, stream)
    assert caught.value.code == "SYNC_RECOVERY_HELD"
    recovery.resume(con, expected_version=db.knowledge_version(con), expected_control_revision=1,
                    operation_id=str(uuid.uuid4()))
    with pytest.raises(SyncError) as caught:
        recovery.assert_recovery_fence(con, 1, stream)
    assert caught.value.code == "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED"
    assert recovery.assert_recovery_fence(con, 1, stream, allow_resume=True)["state"] == "resume_pending"
    floor = control["source_floor"]
    con.execute("BEGIN IMMEDIATE")
    recovery.mark_published(con, 1, {**floor, "kb_revision": floor["kb_revision"] + 1}, stream)
    assert recovery.assert_recovery_fence(con, 1, stream)["state"] == "running"
    con.commit()


@pytest.mark.parametrize("damage", ["missing", "partial", "extra", "boolean_epoch", "other_library", "old_epoch"])
def test_malformed_or_missing_stream_cannot_bypass_nonzero_generation_fence(generations, damage):
    con = generations["con"]
    control = apply(generations)["control"]
    stream = dict(control["stream"])
    if damage == "missing":
        stream = None
    elif damage == "partial":
        del stream["library_epoch"]
    elif damage == "extra":
        stream["kb_revision"] = 0
    elif damage == "boolean_epoch":
        stream["library_epoch"] = True
    elif damage == "other_library":
        stream["library_id"] = str(uuid.uuid4())
    else:
        stream["library_epoch"] -= 1
    with pytest.raises(SyncError) as caught:
        recovery.assert_publish_allowed(con, 1, control["source_floor"], stream)
    assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"
    assert recovery.state(con) == control


def test_only_legacy_zero_generation_python_calls_may_omit_stream(generations):
    con = generations["con"]
    control = recovery.state(con)
    assert recovery.assert_recovery_fence(con, 0) == control
    assert recovery.assert_publish_allowed(con, 0, db.knowledge_version(con)) == control
    con.execute("BEGIN IMMEDIATE")
    assert recovery.mark_published(con, 0, db.knowledge_version(con)) == control
    con.rollback()
    wrong = {**control["stream"], "library_epoch": control["stream"]["library_epoch"] + 1}
    with pytest.raises(SyncError) as caught:
        recovery.assert_recovery_fence(con, 0, wrong)
    assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"
    apply(generations)
    with pytest.raises(SyncError) as caught:
        recovery.assert_recovery_fence(con, 0)
    assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"


def test_recovery_protection_image_is_a_real_undo_target(generations):
    env, con = generations, generations["con"]
    first = apply(env)
    protection = first["protection_checkpoint_id"]
    second = recovery.restore(con, protection, checkpoint_kind="recovery", expected_version=db.knowledge_version(con),
        expected_control_revision=1, operation_id=str(uuid.uuid4()), media_store=env["store"])
    assert con.execute("SELECT COUNT(*) FROM video_asset").fetchone()[0] == 2
    assert con.execute("SELECT claim FROM knowledge_atom WHERE id='atom1'").fetchone()[0] == "新版施工建议"
    assert second["control"]["generation"] == second["control"]["revision"] == 2
    assert con.execute("SELECT COUNT(*) FROM sync_publication").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM sync_recovery_checkpoint").fetchone()[0] == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows read handles deny writes/deletes while recovery holds files")
@pytest.mark.parametrize("damage", ["archive", "media"])
def test_same_size_modification_after_sql_replace_is_caught_before_commit(generations, monkeypatch, damage):
    env, con = generations, generations["con"]
    before, version = preserved(con), db.knowledge_version(con)
    original = recovery._replace_knowledge
    def modify(con, archive):
        original(con, archive)
        path = (env["root"] / env["checkpoint"]["storage_key"]) if damage == "archive" else env["store"].root / env["store"].storage_key(env["media"])
        contents = bytearray(path.read_bytes()); contents[-1] ^= 1
        prior = path.stat()
        path.write_bytes(contents)
        os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))
    monkeypatch.setattr(recovery, "_replace_knowledge", modify)
    # Isolate the byte-integrity guarantee from stat timestamps; production
    # also performs the unmodified identity checks on every held file.
    monkeypatch.setattr(recovery._Pins, "_identity", staticmethod(lambda _item: None))
    with pytest.raises(SyncError):
        apply(env)
    assert db.knowledge_version(con) == version and preserved(con) == before
    assert con.execute("SELECT COUNT(*) FROM video_asset").fetchone()[0] == 2
    assert recovery.state(con)["generation"] == 0
    assert recovery.list_recovery_checkpoints(con, media_store=env["store"]) == []


def test_failure_rolls_back_knowledge_hold_receipt_and_protection_registration(generations, monkeypatch):
    env, con = generations, generations["con"]
    before, version = preserved(con), db.knowledge_version(con)
    original = recovery._replace_knowledge
    def fail(con, archive):
        original(con, archive)
        raise RuntimeError("controlled recovery failure")
    monkeypatch.setattr(recovery, "_replace_knowledge", fail)
    with pytest.raises(RuntimeError, match="controlled"):
        apply(env)
    assert preserved(con) == before and db.knowledge_version(con) == version
    assert con.execute("SELECT COUNT(*) FROM video_asset").fetchone()[0] == 2
    assert not con.execute("SELECT 1 FROM sync_recovery_operation").fetchall()
    assert recovery.state(con)["state"] == "running"
    assert recovery.list_recovery_checkpoints(con) == []


def test_caller_transaction_is_rejected_without_rollback(generations):
    con = generations["con"]
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT INTO meta VALUES('caller-work','kept')")
    with pytest.raises(SyncError) as caught:
        apply(generations)
    assert caught.value.code == "SYNC_RECOVERY_TRANSACTION_OPEN"
    assert con.in_transaction and con.execute("SELECT value FROM meta WHERE key='caller-work'").fetchone()[0] == "kept"
    con.rollback()


@pytest.mark.parametrize("damage", ["archive", "media"])
def test_missing_restore_dependency_is_controlled_and_never_changes_knowledge(generations, damage):
    env, con = generations, generations["con"]
    file = (env["root"] / env["checkpoint"]["storage_key"]) if damage == "archive" else env["store"].root / env["store"].storage_key(env["media"])
    file.unlink()
    version, before = db.knowledge_version(con), preserved(con)
    with pytest.raises(SyncError):
        apply(env)
    assert db.knowledge_version(con) == version and preserved(con) == before
    assert recovery.state(con)["generation"] == 0 and not con.in_transaction


def test_durable_hold_and_control_cas_survive_reopen_without_changing_knowledge(generations):
    env = generations
    applied = apply(env)
    reader = db.connect(env["path"])
    try:
        version = db.knowledge_version(reader)
        assert recovery.state(reader) == applied["control"]
        recovery.resume(reader, expected_version=version, expected_control_revision=1, operation_id=str(uuid.uuid4()))
        with pytest.raises(SyncError) as caught:
            recovery.restore(reader, env["checkpoint"]["checkpoint_id"], expected_version=version,
                expected_control_revision=1, operation_id=str(uuid.uuid4()), media_store=env["store"])
        assert caught.value.code == "SYNC_RECOVERY_CONTROL_CHANGED"
        assert db.knowledge_version(reader) == version
    finally:
        reader.close()


def test_recovery_images_cannot_masquerade_as_publication_checkpoints(generations):
    con = generations["con"]
    con.execute("BEGIN IMMEDIATE")
    image = checkpoints.stage_recovery(con, str(uuid.uuid4()), media_store=generations["store"])
    assert image["origin_kind"] == "recovery" and "publication_id" not in image
    with pytest.raises(SyncError, match="cannot be registered"):
        checkpoints.register(con, image)
    con.rollback()
    assert recovery.list_recovery_checkpoints(con) == []


@pytest.mark.parametrize("role", ["all", "home"])
def test_recovery_and_resume_require_cloud_role(generations, monkeypatch, role):
    from reno import config
    con = generations["con"]
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": role})
    for operation in (lambda: apply(generations), lambda: recovery.resume(con, expected_version=db.knowledge_version(con),
            expected_control_revision=0, operation_id=str(uuid.uuid4()))):
        with pytest.raises(SyncError) as caught:
            operation()
        assert caught.value.code == "SYNC_ROLE_REQUIRED" and not con.in_transaction


@pytest.mark.skipif(os.name != "nt", reason="Native Windows sharing denies changes while restore pins files")
@pytest.mark.parametrize("target", ["archive", "media"])
def test_windows_keeps_dependency_file_locked_until_commit(generations, monkeypatch, target):
    env = generations
    original = recovery._replace_knowledge
    observed = []
    def try_write(con, archive):
        path = (env["root"] / env["checkpoint"]["storage_key"]) if target == "archive" else env["store"].root / env["store"].storage_key(env["media"])
        with pytest.raises(PermissionError):
            with path.open("r+b"):
                pytest.fail("Held recovery dependency was writable")
        observed.append(target)
        original(con, archive)
    monkeypatch.setattr(recovery, "_replace_knowledge", try_write)
    assert apply(env)["control"]["state"] == "held"
    assert observed == [target]


def report_checkpoint(env):
    con = env["con"]
    original = dict(con.execute("SELECT * FROM sync_publication").fetchone())
    body = "# 归档报告\n"
    entries = sync_reports.prepare_files(env["reports"], [{"name": "checklist", "content": body,
        "sha256": hashlib.sha256(body.encode()).hexdigest(), "byte_size": len(body.encode()),
        "version": db.knowledge_version(con), "policy_version": "v1", "generated_at": "2026-10-01T00:00:00.000Z",
        "review_events_sha256": sync_reports.event_fingerprint(con)}])
    con.execute("BEGIN IMMEDIATE")
    sync_reports.install_pointers(con, entries, original["publication_id"], db.knowledge_version(con))
    con.commit()
    pointer = dict(con.execute("SELECT * FROM sync_report").fetchone())
    con.execute("BEGIN IMMEDIATE")
    env["checkpoint"] = checkpoints.stage(con, str(uuid.uuid4()), media_store=env["store"], report_root=env["reports"])
    con.execute("DELETE FROM sync_report")
    original["publication_id"] = env["checkpoint"]["publication_id"]
    con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", tuple(original.values()))
    checkpoints.register(con, env["checkpoint"])
    con.commit()
    return pointer, env["reports"] / entries[0]["storage_key"]


def test_old_report_pointer_is_restored_without_relabelling_its_source_version(generations):
    env, con = generations, generations["con"]
    pointer, _ = report_checkpoint(env)
    before = preserved(con)
    result = apply(env)
    assert dict(con.execute("SELECT * FROM sync_report").fetchone()) == pointer
    assert json.loads(pointer["local_version_json"]) != result["version"]
    assert preserved(con) == before


def test_report_dependency_is_pinned_and_rechecked_before_commit(generations, monkeypatch):
    env, con = generations, generations["con"]
    _, path = report_checkpoint(env)
    version = db.knowledge_version(con)
    original = recovery._replace_knowledge
    observed = []
    def intervene(con, archive):
        original(con, archive)
        if os.name == "nt":
            with pytest.raises(PermissionError):
                path.write_bytes(b"x" * path.stat().st_size)
        else:
            path.write_bytes(b"x" * path.stat().st_size)
        observed.append(True)
    monkeypatch.setattr(recovery, "_replace_knowledge", intervene)
    if os.name == "nt":
        assert apply(env)["control"]["state"] == "held"
    else:
        monkeypatch.setattr(recovery._Pins, "_identity", staticmethod(lambda _item: None))
        with pytest.raises(SyncError):
            apply(env)
        assert db.knowledge_version(con) == version and not con.execute("SELECT 1 FROM sync_report").fetchall()
        assert recovery.state(con)["generation"] == 0
    assert observed == [True]
