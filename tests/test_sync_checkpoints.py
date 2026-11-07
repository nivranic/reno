"""Knowledge checkpoints on artificial libraries, never on the user's data."""
import hashlib
import json
import os
import sqlite3
import uuid

import pytest

from reno import config, db, sync_checkpoints as checkpoints, sync_reports, storage_lifecycle
from reno.sync_media import MediaStore
from reno.sync_protocol import SyncError

CONTENT = b"synthetic immutable video"
STAMP = "2026-10-01T00:00:00.000Z"


@pytest.fixture()
def receiver(tmp_path, monkeypatch):
    path = tmp_path / "cloud" / "data" / "reno.db"
    db.initialize(path)
    con = db.connect(path)
    media = path.parent.parent / "media"
    monkeypatch.setattr(config, "DATA", path.parent)
    monkeypatch.setattr(config, "MEDIA", media)
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "cloud"})
    store = MediaStore(media)
    profile = storage_lifecycle.StorageProfile(path, media, path.parent / "generated", path.parent / "sync-checkpoints")
    storage_lifecycle.initialize(profile)
    with storage_lifecycle.shared(profile):
        try:
            yield {"con": con, "path": path, "store": store, "profile": profile,
                   "root": profile.checkpoints, "reports": profile.reports}
        finally:
            con.close()


def seed(receiver):
    con, store = receiver["con"], receiver["store"]
    media = {"video_id": "video1", "sha256": hashlib.sha256(CONTENT).hexdigest(),
             "byte_size": len(CONTENT), "extension": "mp4"}
    key = store.receive(media, [CONTENT])
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json,status) VALUES(?,?,?,?,?)",
                ("video1", "人工知识", media["sha256"], json.dumps({"video": "media/" + key}), "processed"))
    con.execute("INSERT INTO transcript_segment(id,video_id,text) VALUES('seg','video1','原证据')")
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim,status,cluster_id) VALUES('atom1','video1','原建议','verified','cluster1')")
    con.execute("INSERT INTO evidence_ref(id,atom_id,video_id,modality,source_item_id,evidence_text) VALUES(73,'atom1','video1','asr','seg','原证据')")
    con.execute("INSERT INTO knowledge_cluster(cluster_id,members_json) VALUES('cluster1','[\"atom1\"]')")
    con.execute("INSERT INTO meta VALUES('step:video1:atomize','done')")
    con.execute("INSERT INTO meta VALUES('auth_token','NEVER-ARCHIVE-CREDENTIAL')")
    con.execute("INSERT INTO user_decision(atom_id,action,note,event_id) VALUES('atom1','confirm','NEVER-ARCHIVE-REVIEW',?)", (str(uuid.uuid4()),))
    con.execute("INSERT INTO sync_queue(url,status) VALUES('https://b23.tv/NEVER-ARCHIVE-QUEUE','queued')")
    con.execute("INSERT INTO job(kind,error) VALUES('process','NEVER-ARCHIVE-JOB')")
    project = con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone()[0]
    con.execute("INSERT INTO decision_card(card_id,project_id,title,created_at,updated_at) VALUES('private',?,'NEVER-ARCHIVE-PERSONAL',?,?)", (project, STAMP, STAMP))
    con.commit()
    receiver["media"] = media


def publication(con, publication_id):
    con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)",
                (publication_id, str(uuid.uuid4()), str(uuid.uuid4()), 1, 1, "a" * 64, "b" * 64, STAMP,
                 json.dumps(db.knowledge_version(con))))


def stage(receiver):
    con = receiver["con"]
    con.execute("BEGIN IMMEDIATE")
    return checkpoints.stage(con, str(uuid.uuid4()), media_store=receiver["store"], report_root=receiver["reports"])


def commit(receiver, entry):
    publication(receiver["con"], entry["publication_id"])
    checkpoints.register(receiver["con"], entry)
    receiver["con"].commit()


def preview(receiver, entry):
    return checkpoints.preview(receiver["con"], entry["checkpoint_id"], media_store=receiver["store"], report_root=receiver["reports"])


def test_only_committed_preimage_is_visible_and_private_domains_never_enter_file(receiver):
    seed(receiver)
    con = receiver["con"]
    before = db.knowledge_version(con)
    entry = stage(receiver)
    reader = db.connect(receiver["path"])
    try:
        assert checkpoints.list_checkpoints(reader) == []
        con.execute("UPDATE knowledge_atom SET claim='发布后的建议'")
        publication(con, entry["publication_id"])
        checkpoints.register(con, entry)
        assert checkpoints.list_checkpoints(reader) == []
        assert con.in_transaction
        con.commit()
        result = preview(receiver, entry)
        assert result["recoverable"] and result["receiver_version"] == before
        assert result["row_counts"]["knowledge_atom"] == 1
        with checkpoints.open_verified(con, entry["checkpoint_id"], media_store=receiver["store"], report_root=receiver["reports"]) as (archive, manifest):
            assert archive.execute("SELECT claim,status FROM knowledge_atom").fetchone()[:] == ("原建议", "verified")
            assert archive.execute("SELECT id FROM evidence_ref").fetchone()[0] == 73
            assert archive.execute("SELECT key,value FROM meta").fetchall()[0][:] == ("step:video1:atomize", "done")
            assert manifest["receiver_version"] == before
            for sql in ("DELETE FROM knowledge_atom", "PRAGMA query_only=OFF", "ATTACH ':memory:' AS extra", "CREATE TABLE unsafe(x)"):
                with pytest.raises(sqlite3.DatabaseError):
                    archive.execute(sql)
        content = (receiver["root"] / entry["storage_key"]).read_bytes()
        assert b"NEVER-ARCHIVE" not in content
        with sqlite3.connect(receiver["root"] / entry["storage_key"]) as archive:
            names = {row[0] for row in archive.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names == set(checkpoints.KNOWLEDGE_COLUMNS) | {"checkpoint_manifest", "checkpoint_media"}
    finally:
        reader.close()


def test_failed_publication_leaves_only_unlisted_orphan_and_retry_can_capture_again(receiver):
    seed(receiver)
    entry = stage(receiver)
    publication(receiver["con"], entry["publication_id"])
    checkpoints.register(receiver["con"], entry)
    receiver["con"].rollback()
    assert (receiver["root"] / entry["storage_key"]).is_file()
    assert checkpoints.list_checkpoints(receiver["con"]) == []
    receiver["con"].execute("BEGIN IMMEDIATE")
    retry = checkpoints.stage(receiver["con"], entry["publication_id"], media_store=receiver["store"])
    assert retry["checkpoint_id"] != entry["checkpoint_id"]
    commit(receiver, retry)
    assert len(checkpoints.list_checkpoints(receiver["con"], media_store=receiver["store"])) == 1


def test_empty_preimage_is_complete_and_listing_has_no_filesystem_writes(receiver):
    before = set(receiver["path"].parent.iterdir())
    assert checkpoints.list_checkpoints(receiver["con"]) == []
    assert set(receiver["path"].parent.iterdir()) == before
    entry = stage(receiver)
    commit(receiver, entry)
    file = receiver["root"] / entry["storage_key"]
    timestamp = file.stat().st_mtime_ns
    version = db.knowledge_version(receiver["con"])
    changes = receiver["con"].total_changes
    result = preview(receiver, entry)
    assert result["recoverable"] and all(count == 0 for count in result["row_counts"].values())
    assert file.stat().st_mtime_ns == timestamp
    assert db.knowledge_version(receiver["con"]) == version
    assert receiver["con"].total_changes == changes and not receiver["con"].in_transaction


def test_transaction_guards_never_commit_callers_work(receiver):
    con = receiver["con"]
    with pytest.raises(SyncError):
        checkpoints.stage(con, str(uuid.uuid4()))
    entry = stage(receiver)
    with pytest.raises(SyncError):
        checkpoints.register(con, entry)
    # A caller-protected read snapshot is now valid; it must stay caller-owned.
    assert checkpoints.list_checkpoints(con) == []
    with pytest.raises(SyncError):
        checkpoints.preview(con, entry["checkpoint_id"])
    assert con.in_transaction
    con.rollback()


@pytest.mark.parametrize("damage", ["truncate", "same_size", "missing", "media", "identity", "node_identity"])
def test_damage_is_never_presented_as_recoverable(receiver, damage):
    seed(receiver)
    entry = stage(receiver)
    commit(receiver, entry)
    file = receiver["root"] / entry["storage_key"]
    if damage == "truncate":
        file.write_bytes(b"truncated")
    elif damage == "same_size":
        content = bytearray(file.read_bytes()); content[-20] ^= 1
        file.write_bytes(content)
    elif damage == "missing":
        file.unlink()
    elif damage == "media":
        path = receiver["store"].root / receiver["store"].storage_key(receiver["media"])
        path.write_bytes(b"x" * len(CONTENT))
    elif damage == "identity":
        receiver["con"].execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        receiver["con"].commit()
    else:
        (receiver["path"].parent / "node-identity.json").write_text(json.dumps({"instance_id": str(uuid.uuid4())}))
    result = preview(receiver, entry)
    assert not result["recoverable"] and result["issues"]
    with pytest.raises(SyncError):
        with checkpoints.open_verified(receiver["con"], entry["checkpoint_id"], media_store=receiver["store"]):
            pytest.fail("damaged checkpoint opened")


def test_nonready_is_preserved_and_missing_legacy_media_does_not_block_capture(receiver):
    con = receiver["con"]
    con.execute("INSERT INTO video_asset(video_id,sync_state) VALUES('pending','staging')")
    con.execute("INSERT INTO video_asset(video_id,files_json) VALUES('legacy','{\"video\":\"../../private\"}')")
    con.commit()
    entry = stage(receiver)
    commit(receiver, entry)
    result = preview(receiver, entry)
    assert not result["recoverable"]
    assert {issue["target"] for issue in result["issues"]} == {"legacy"}
    assert result["warnings"] == [{"code": "video_not_ready", "target": "pending"}]
    assert con.execute("SELECT sync_state FROM video_asset WHERE video_id='pending'").fetchone()[0] == "staging"


def test_report_pointers_are_captured_and_files_are_rechecked(receiver):
    con = receiver["con"]
    old = str(uuid.uuid4())
    publication(con, old)
    version = db.knowledge_version(con)
    body = "# 原报告\n"
    values = [{"name": "checklist", "content": body, "sha256": hashlib.sha256(body.encode()).hexdigest(),
               "byte_size": len(body.encode()), "version": version, "policy_version": "v1", "generated_at": STAMP,
               "review_events_sha256": "c" * 64}]
    entries = sync_reports.prepare_files(receiver["reports"], values)
    sync_reports.install_pointers(con, entries, old, version)
    con.commit()
    entry = stage(receiver)
    commit(receiver, entry)
    assert preview(receiver, entry)["recoverable"]
    report_file = receiver["reports"] / entries[0]["storage_key"]
    report_file.write_text("damaged", encoding="utf-8")
    assert not preview(receiver, entry)["recoverable"]
    report_file.write_bytes(body.encode("utf-8"))
    con.execute("DELETE FROM sync_report")
    con.execute("DELETE FROM sync_publication WHERE publication_id=?", (old,))
    con.commit()
    result = preview(receiver, entry)
    assert not result["recoverable"]
    assert {"code": "report_publication_missing", "target": "checklist"} in result["issues"]


def test_archive_file_symlink_and_hardlink_are_rejected(receiver, tmp_path):
    entry = stage(receiver)
    commit(receiver, entry)
    file = receiver["root"] / entry["storage_key"]
    other = tmp_path / "external.sqlite"
    other.write_bytes(file.read_bytes())
    file.unlink()
    try:
        file.symlink_to(other)
    except OSError:
        file.hardlink_to(other)
    assert not preview(receiver, entry)["recoverable"]
    assert other.read_bytes()[:16] == b"SQLite format 3\x00"


def test_stage_failure_does_not_register_or_remove_source_data(receiver, monkeypatch):
    seed(receiver)
    con = receiver["con"]
    before = db.knowledge_version(con)
    def fail(*_args):
        raise OSError("simulated disk-full/rename failure")
    monkeypatch.setattr(checkpoints, "_rename_new", fail)
    with pytest.raises(SyncError, match="storage"):
        stage(receiver)
    assert con.in_transaction and db.knowledge_version(con) == before
    assert not con.execute("SELECT 1 FROM sync_checkpoint").fetchall()
    assert {path.name for path in receiver["root"].iterdir()} == {storage_lifecycle.LOCK_NAME}
    con.rollback()


@pytest.mark.parametrize("damage", ["ocr_frame", "vision_frame", "evidence_source", "cluster_member", "conflict_side", "linked_conflict"])
def test_incomplete_knowledge_graph_is_captured_but_not_recoverable(receiver, damage):
    seed(receiver)
    statements = {
        "ocr_frame": "INSERT INTO ocr_span(id,video_id,frame_ids_json) VALUES('ocr','video1','[\"missing\"]')",
        "vision_frame": "INSERT INTO visual_observation(id,video_id,frame_id) VALUES('vision','video1','missing')",
        "evidence_source": "UPDATE evidence_ref SET source_item_id='missing'",
        "cluster_member": "UPDATE knowledge_cluster SET members_json='[\"missing\"]'",
        "conflict_side": "INSERT INTO conflict_case(conflict_id,side_a_json) VALUES('conflict','{\"atoms\":[\"missing\"]}')",
        "linked_conflict": "UPDATE knowledge_cluster SET linked_conflict='missing'",
    }
    receiver["con"].execute(statements[damage])
    receiver["con"].commit()
    entry = stage(receiver)
    commit(receiver, entry)
    result = preview(receiver, entry)
    assert not result["recoverable"]
    assert any("knowledge_graph" in issue["code"] for issue in result["issues"])


def test_archive_contract_survives_later_live_schema_version(receiver, monkeypatch):
    entry = stage(receiver)
    commit(receiver, entry)
    from reno import migrations
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", migrations.SCHEMA_VERSION + 1)
    assert preview(receiver, entry)["recoverable"]
    assert entry["schema_version"] == 9


def test_registration_cannot_be_added_after_publication_commit(receiver):
    entry = stage(receiver)
    publication(receiver["con"], entry["publication_id"])
    receiver["con"].commit()
    receiver["con"].execute("BEGIN IMMEDIATE")
    with pytest.raises(SyncError, match="transaction"):
        checkpoints.register(receiver["con"], entry)
    receiver["con"].rollback()
    assert checkpoints.list_checkpoints(receiver["con"]) == []


def test_existing_publication_cannot_be_recaptured(receiver):
    entry = stage(receiver)
    commit(receiver, entry)
    receiver["con"].execute("BEGIN IMMEDIATE")
    with pytest.raises(SyncError, match="existing publication"):
        checkpoints.stage(receiver["con"], entry["publication_id"])
    receiver["con"].rollback()
    assert len(list(receiver["root"].glob("*.sqlite"))) == 1


def test_even_rehashed_archive_with_extra_domain_is_not_accepted(receiver):
    entry = stage(receiver)
    commit(receiver, entry)
    file = receiver["root"] / entry["storage_key"]
    with sqlite3.connect(file) as archive:
        archive.execute("CREATE TABLE private_domain(secret TEXT)")
    content = file.read_bytes()
    receiver["con"].execute("UPDATE sync_checkpoint SET sha256=?,byte_size=? WHERE checkpoint_id=?",
                            (hashlib.sha256(content).hexdigest(), len(content), entry["checkpoint_id"]))
    receiver["con"].commit()
    assert not preview(receiver, entry)["recoverable"]


@pytest.mark.skipif(os.name == "nt", reason="Windows held directory handles prevent this POSIX rename race")
def test_substituted_checkpoint_directory_never_receives_external_writes(receiver, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_bytes(b"not ours")
    original = checkpoints._rename_new
    displaced = receiver["root"].with_name("displaced-checkpoints")
    def replace_directory(source, name, destination, target):
        receiver["root"].rename(displaced)
        receiver["root"].symlink_to(outside, target_is_directory=True)
        original(source, name, destination, target)
    monkeypatch.setattr(checkpoints, "_rename_new", replace_directory)
    try:
        with pytest.raises(SyncError):
            stage(receiver)
        assert list(outside.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"not ours"
        assert not receiver["con"].execute("SELECT 1 FROM sync_checkpoint").fetchall()
    finally:
        receiver["con"].rollback()
        if receiver["root"].is_symlink():
            receiver["root"].unlink()
            displaced.rename(receiver["root"])
