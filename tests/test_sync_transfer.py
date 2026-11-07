"""Atomic publication on independent real SQLite files and synthetic media."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import config, db, personal_schema, report, review, sync_protocol, sync_queue
from reno import sync_reports, sync_transfer, storage_lifecycle, storage_artifacts, sync_upload_pins
from reno.sync_media import MediaStore
from reno.sync_snapshot import export_snapshot
from reno.sync_protocol import SyncError


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
VIDEO = "BV1SyncExample"
URL = f"https://www.bilibili.com/video/{VIDEO}"
CONTENT = b"synthetic media bytes, never downloaded"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    source_path, target_path = tmp_path / "home" / "data" / "reno.db", tmp_path / "cloud" / "data" / "reno.db"
    db.initialize(source_path)
    db.initialize(target_path)
    source, target = db.connect(source_path), db.connect(target_path)
    monkeypatch.setattr(config, "DATA", target_path.parent)
    monkeypatch.setattr(config, "MEDIA", target_path.parent.parent / "media")
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "cloud"})
    media = {"video_id": VIDEO, "sha256": hashlib.sha256(CONTENT).hexdigest(),
             "byte_size": len(CONTENT), "extension": "mp4"}
    source.execute("INSERT INTO video_asset(video_id,source_platform,source_type,source_url,title,content_sha256,status) "
                   "VALUES(?,'bilibili','url',?,'人工样本',?,'processed')", (VIDEO, URL, media["sha256"]))
    source.execute("INSERT INTO transcript_segment(id,video_id,idx,start_ms,end_ms,text) VALUES('seg',?,0,1,20,'证据')", (VIDEO,))
    source.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json,exceptions,prices_json) "
                   "VALUES('atom',?,'施工前检查基层','{}','[]','[]','[]')", (VIDEO,))
    source.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms) "
                   "VALUES('atom',?,'asr','seg',1,20)", (VIDEO,))
    source.execute("INSERT INTO meta VALUES(?,'done')", (f"step:{VIDEO}:atomize",))
    source.commit()
    source.execute("BEGIN")
    payload = export_snapshot(source, publication_id=str(uuid.uuid4()), media={VIDEO: media})
    source.rollback()
    identity = {"library_id": db.knowledge_version(source)["library_id"],
                "node_id": personal_schema.node_identity(source_path.parent),
                "handshake": sync_protocol.schema_handshake(source)}
    sync_queue.bind_peer(target, identity, now=NOW)
    lease = sync_queue.heartbeat(target, identity, str(uuid.uuid4()), now=NOW)["lease"]
    target.commit()
    store = MediaStore(config.MEDIA)
    store.receive(media, [CONTENT])
    profile = storage_lifecycle.StorageProfile(target_path, store.root, target_path.parent / "generated", target_path.parent / "sync-checkpoints")
    storage_lifecycle.initialize(profile)
    yield {"source": source, "con": target, "identity": identity, "lease": lease,
           "payload": payload, "media": media, "store": store, "target_path": target_path, "profile": profile}
    source.close()
    target.close()


@contextmanager
def receiver_guard(env, *, report_root=None):
    original = env["profile"]
    profile = original if report_root is None else storage_lifecycle.StorageProfile(
        original.database, original.media, report_root, original.checkpoints)
    if report_root is not None:
        storage_lifecycle.initialize(profile)
    env["profile"] = profile
    with storage_lifecycle.shared(profile):
        try:
            yield profile
        finally:
            env["con"].rollback()
            env["profile"] = original


def upload_binding(env, payload=None, *, recovery_generation=0, recovery_stream=None):
    value = env["payload"] if payload is None else payload
    local = db.knowledge_version(env["con"])
    return {"publication_id": value["publication_id"], "source_node_id": env["identity"]["node_id"],
        "source_version": value["version"], "recovery_generation": recovery_generation,
        "recovery_stream": recovery_stream or {k: local[k] for k in ("library_id", "library_epoch")}}


def prepare_fixture(env, payload=None, **fence):
    """Explicit test setup for already-created synthetic receiver files."""
    value = env["payload"] if payload is None else payload
    binding = upload_binding(env, value, **fence)
    result = sync_transfer.prepare_uploads(env["con"], env["identity"], env["lease"], binding=binding,
        descriptors=[video["media"] for video in value["videos"]], profile=env["profile"], now=NOW)
    if result["state"] != "published":
        for video in value["videos"]:
            descriptor = video["media"]
            record = storage_artifacts.register(env["con"], profile=env["profile"], namespace="sync_media",
                root=env["store"].root, storage_key=env["store"].storage_key(descriptor),
                sha256=descriptor["sha256"], byte_size=descriptor["byte_size"])
            sync_upload_pins.mark_stored(env["con"], {**binding, "descriptor": descriptor}, record["artifact_id"], profile=env["profile"])
    return binding


def publish(env, payload=None, **kwargs):
    value = env["payload"] if payload is None else payload
    # No implicit guard: each caller owns it through its eventual COMMIT.
    storage_lifecycle.require_guard(env["profile"])
    fence = {k: kwargs[k] for k in ("recovery_generation", "recovery_stream") if k in kwargs}
    binding = upload_binding(env, value, **fence)
    if env["con"].execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (value["publication_id"],)).fetchone() is None:
        prepare_fixture(env, value, **fence)
    kwargs.setdefault("recovery_stream", binding["recovery_stream"])
    return sync_transfer.publish_snapshot(env["con"], env["identity"], env["lease"], value,
        media_store=env["store"], now=NOW, profile=env["profile"], **kwargs)


def next_payload(env):
    value = deepcopy(env["payload"])
    value["publication_id"] = str(uuid.uuid4())
    value["version"]["kb_revision"] += 1
    return value


def report_entry(env, content="# 同步报告\n\n先核实依据。\n"):
    return {"name": "checklist", "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(), "policy_version": report.POLICY_VERSION,
            "version": deepcopy(env["payload"]["version"]), "generated_at": "2026-01-01T00:00:00.000Z",
            "review_events_sha256": sync_reports.event_fingerprint(env["source"])}


def claim(env):
    sync_queue.enqueue(env["con"], [URL], now=NOW)
    item = sync_queue.claim(env["con"], env["identity"], env["lease"], now=NOW)[0]
    env["con"].commit()
    return {"video_id": VIDEO, "claim": item["claim"]}


def state(con):
    tables = ("video_asset", "knowledge_atom", "evidence_ref", "transcript_segment", "knowledge_cluster",
              "conflict_case", "sync_publication", "sync_report", "sync_published_video", "sync_queue_publication")
    return {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name} ORDER BY rowid")] for name in tables}


def test_publication_is_caller_owned_and_old_readers_keep_previous_generation(env):
    with receiver_guard(env):
        con = env["con"]
        before = state(con)
        receipt = publish(env)
        assert receipt["replayed"] is False and con.in_transaction
        assert con.execute("SELECT sync_state FROM video_asset").fetchone()[0] == "ready"
        with sqlite3.connect(env["target_path"]) as observer:
            assert observer.execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0
        con.rollback()
        assert state(con) == before
        publish(env)
        con.commit()
        with sqlite3.connect(env["target_path"]) as observer:
            assert observer.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "施工前检查基层"
        files = json.loads(con.execute("SELECT files_json FROM video_asset").fetchone()[0])
        assert set(files) == {"video"}
        assert config.resolve_media(files["video"]).read_bytes() == CONTENT


def test_replacement_removes_disappeared_atoms_steps_evidence_and_fts(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        value = next_payload(env)
        tables = value["videos"][0]["tables"]
        tables["knowledge_atom"] = []
        tables["evidence_ref"] = []
        tables["transcript_segment"] = []
        tables["meta"] = []
        publish(env, value)
        for table in ("knowledge_atom", "evidence_ref", "transcript_segment", "atom_fts"):
            assert env["con"].execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert env["con"].execute("SELECT count(*) FROM meta WHERE key=?", (f"step:{VIDEO}:atomize",)).fetchone()[0] == 0


def test_exact_receipt_and_claim_binding_are_created_together(env):
    with receiver_guard(env):
        binding = claim(env)
        first = publish(env, claims=[binding])
        env["con"].commit()
        assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "done"
        assert env["con"].execute("SELECT publication_id FROM sync_queue_publication").fetchone()[0] == first["publication_id"]
        revision = db.knowledge_version(env["con"])
        second = publish(env, claims=[binding])
        assert second == {**first, "replayed": True}
        assert db.knowledge_version(env["con"]) == revision
        env["con"].rollback()
        # Same ID cannot acquire additional claims after initial publication.
        with pytest.raises(SyncError, match="another payload"):
            publish(env)


def test_new_claim_requires_new_publication_even_for_same_video(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        binding = claim(env)
        with pytest.raises(SyncError) as error:
            sync_queue.complete(env["con"], env["identity"], env["lease"], binding["claim"],
                                publication_id=env["payload"]["publication_id"], video_id=VIDEO, now=NOW)
        assert error.value.code == "SYNC_PUBLICATION_REQUIRED"
        value = deepcopy(env["payload"])
        value["publication_id"] = str(uuid.uuid4())
        publish(env, value, claims=[binding])
        assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "done"


@pytest.mark.parametrize("mutation,code", [
    ("old", "SYNC_STALE_PUBLICATION"), ("changed", "SYNC_VERSION_CONFLICT"),
    ("same_id", "SYNC_PUBLICATION_CONFLICT"), ("omitted", "SYNC_INCOMPLETE_SNAPSHOT")])
def test_old_conflicting_and_incomplete_batches_keep_previous_data(env, mutation, code):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        before = state(env["con"])
        value = next_payload(env)
        if mutation == "old":
            value["version"]["kb_revision"] = env["payload"]["version"]["kb_revision"] - 1
        elif mutation == "changed":
            value["version"] = deepcopy(env["payload"]["version"])
            value["videos"][0]["tables"]["knowledge_atom"][0]["claim"] = "另一种建议"
        elif mutation == "same_id":
            value["publication_id"] = env["payload"]["publication_id"]
        else:
            value["videos"] = []
        with pytest.raises(SyncError) as error:
            publish(env, value)
        assert error.value.code == code
        assert state(env["con"]) == before


def test_existing_atom_cannot_change_video_owner(env):
    with receiver_guard(env):
        env["con"].execute("INSERT INTO video_asset(video_id) VALUES('other')")
        env["con"].execute("INSERT INTO knowledge_atom(id,video_id,claim) VALUES('atom','other','旧知识')")
        env["con"].commit()
        before = state(env["con"])
        with pytest.raises(SyncError) as error:
            publish(env)
        assert error.value.code == "SYNC_ATOM_OWNER_MISMATCH"
        assert state(env["con"]) == before


def test_failure_after_replacement_rolls_back_all_changes_but_keeps_callers_work(env, monkeypatch):
    with receiver_guard(env):
        env["con"].execute("UPDATE renovation_project SET name='调用方草稿'")
        before = state(env["con"])
        def fail(*args, **kwargs):
            raise OSError("synthetic failure after knowledge replacement")
        monkeypatch.setattr(sync_reports, "install_pointers", fail)
        with pytest.raises(OSError):
            publish(env)
        assert state(env["con"]) == before
        assert env["con"].execute("SELECT name FROM renovation_project").fetchone()[0] == "调用方草稿"
        assert env["con"].in_transaction


def test_final_lease_is_checked_after_expensive_preparation(env, monkeypatch):
    with receiver_guard(env):
        before = state(env["con"])
        calls = iter([NOW, NOW + timedelta(seconds=601)])
        original = sync_queue._clock
        monkeypatch.setattr(sync_queue, "_clock", lambda _: original(next(calls)))
        with pytest.raises(SyncError) as error:
            publish(env)
        assert error.value.code == "SYNC_LEASE_EXPIRED"
        assert state(env["con"]) == before


def test_lease_expiring_during_replacement_prevents_publication(env, monkeypatch):
    with receiver_guard(env):
        original = sync_queue._clock
        moments = iter([NOW, NOW, NOW + timedelta(seconds=601)])
        monkeypatch.setattr(sync_queue, "_clock", lambda _: original(next(moments)))
        with pytest.raises(SyncError) as error:
            publish(env)
        assert error.value.code == "SYNC_LEASE_EXPIRED"
        assert env["con"].execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0


def test_personal_history_attachments_identity_and_review_facts_survive(env, tmp_path):
    with receiver_guard(env):
        con = env["con"]
        publish(env)
        con.commit()
        review.record_decision(con, atom_id="atom", action="reject", client_id="cloud-user")
        con.commit()
        personal = {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")] for name in personal_schema.TABLES}
        history = [tuple(row) for row in con.execute("SELECT * FROM user_decision")]
        identity_file = env["target_path"].parent / "node-identity.json"
        identity = identity_file.read_bytes()
        attachments = env["target_path"].parent / "personal-attachments"
        attachments.mkdir()
        (attachments / "keep.pdf").write_bytes(b"private attachment")
        version = db.knowledge_version(con)
        publish(env, next_payload(env))
        assert con.execute("SELECT status FROM knowledge_atom WHERE id='atom'").fetchone()[0] == "rejected"
        assert [tuple(row) for row in con.execute("SELECT * FROM user_decision")] == history
        assert {name: [tuple(row) for row in con.execute(f"SELECT * FROM {name}")] for name in personal_schema.TABLES} == personal
        assert identity_file.read_bytes() == identity
        assert (attachments / "keep.pdf").read_bytes() == b"private attachment"
        assert db.knowledge_version(con)["library_id"] == version["library_id"]


def test_report_visibility_follows_database_commit_and_retains_old_on_failure(env, monkeypatch):
    with receiver_guard(env):
        env["payload"]["reports"] = [report_entry(env)]
        publish(env)
        assert report.read_report("checklist") is None
        env["con"].commit()
        old_content = report.read_report("checklist")
        assert old_content == env["payload"]["reports"][0]["content"]
        assert report.metadata()["checklist"]["status"] == "latest"
        value = next_payload(env)
        value["reports"] = [{**report_entry(env, "# 新报告\n"), "version": value["version"]}]
        publish(env, value)
        assert report.read_report("checklist") == old_content
        env["con"].rollback()
        assert report.read_report("checklist") == old_content


def test_source_report_is_stale_when_receiver_has_other_review_facts(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        review.record_decision(env["con"], atom_id="atom", action="reject", client_id="cloud-user")
        env["con"].commit()
        value = next_payload(env)
        value["reports"] = [{**report_entry(env), "version": value["version"]}]
        publish(env, value)
        env["con"].commit()
        meta = report.metadata()["checklist"]
        assert meta["has_content"] and meta["stale"] and meta["status"] == "stale"
        assert meta["source_version"] == value["version"]


def test_same_knowledge_version_can_publish_new_report_without_rewriting_knowledge(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        before = db.knowledge_version(env["con"])
        value = deepcopy(env["payload"])
        value["publication_id"] = str(uuid.uuid4())
        value["reports"] = [report_entry(env)]
        publish(env, value)
        env["con"].commit()
        assert db.knowledge_version(env["con"]) == before
        assert report.metadata()["checklist"]["status"] == "latest"


@pytest.mark.parametrize("field,value", [("name", "../checklist"), ("sha256", "0" * 64),
                                        ("version", {}), ("policy_version", "old"),
                                        ("generated_at", "invalid"), ("review_events_sha256", "no")])
def test_report_contract_failure_does_not_replace_knowledge(env, field, value):
    with receiver_guard(env):
        entry = report_entry(env)
        entry[field] = value
        env["payload"]["reports"] = [entry]
        with pytest.raises(SyncError):
            publish(env)
        assert env["con"].execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0


def test_missing_media_prevents_publication(env):
    with receiver_guard(env):
        env["store"].verified_path(env["media"]).unlink()
        with pytest.raises(SyncError):
            publish(env)
        assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0


def test_media_upload_requires_pair_and_live_lease_without_publishing(env):
    with receiver_guard(env):
        binding = upload_binding(env)
        sync_transfer.prepare_uploads(env["con"], env["identity"], env["lease"], binding=binding,
            descriptors=[env["media"]], profile=env["profile"], now=NOW)
        env["con"].commit()
        intent = {**binding, "descriptor": env["media"]}
        result = sync_transfer.receive_media(env["con"], env["identity"], env["lease"], env["media"],
                                             [CONTENT], media_store=env["store"], now=NOW, intent=intent, profile=env["profile"])
        assert result["published"] is False
        assert env["con"].execute("SELECT count(*) FROM video_asset").fetchone()[0] == 0
        env["con"].rollback()
        with pytest.raises(SyncError) as error:
            sync_transfer.receive_media(env["con"], env["identity"], env["lease"], env["media"],
                                        [CONTENT], media_store=env["store"], now=NOW + timedelta(seconds=601), intent=intent, profile=env["profile"])
        assert error.value.code == "SYNC_LEASE_EXPIRED"


def test_role_fence(env, monkeypatch):
    with receiver_guard(env):
        monkeypatch.setitem(config._cache, "node_role", "home")
        with pytest.raises(SyncError) as error:
            publish(env)
        assert error.value.code == "SYNC_ROLE_REQUIRED"


def test_source_report_export_is_version_bound_and_preserves_transaction(env, monkeypatch):
    source = env["source"]
    monkeypatch.setattr(config, "DATA", env["target_path"].parents[2] / "home" / "data")
    monkeypatch.setitem(config._cache, "node_role", "home")
    from reno import storage_admin
    profile = storage_admin.profile()
    storage_lifecycle.initialize(profile)
    report.checklist_report()
    with storage_lifecycle.shared(profile):
        source.execute("BEGIN")
        exported = sync_reports.export_reports(source, profile=profile)
        assert source.in_transaction and len(exported) == 1
        assert exported[0]["name"] == "checklist"
        assert exported[0]["version"] == db.knowledge_version(source)
        assert exported[0]["review_events_sha256"] == sync_reports.event_fingerprint(source)
        source.rollback()
    review.record_decision(source, atom_id="atom", action="reject", client_id="home-user")
    source.commit()
    with storage_lifecycle.shared(profile):
        source.execute("BEGIN")
        assert sync_reports.export_reports(source, profile=profile) == []
        source.rollback()


def test_damaged_report_is_not_returned_and_does_not_fall_back_to_legacy_file(env):
    with receiver_guard(env):
        env["payload"]["reports"] = [report_entry(env)]
        publish(env)
        env["con"].commit()
        manifest = report._manifest("checklist")
        path = report.out_dir() / manifest["content_file"]
        path.write_text("x" * manifest["byte_size"], encoding="utf-8")
        (report.out_dir() / "checklist.md").write_text("legacy content", encoding="utf-8")
        assert report.read_report("checklist") is None
        assert report.metadata()["checklist"]["has_content"] is False


def test_media_changed_between_generations_preserves_old_file(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        old_path = env["store"].verified_path(env["media"])
        value = next_payload(env)
        updated = b"new synthetic media generation"
        descriptor = {**env["media"], "sha256": hashlib.sha256(updated).hexdigest(), "byte_size": len(updated)}
        env["store"].receive(descriptor, [updated])
        value["videos"][0]["media"] = descriptor
        value["videos"][0]["tables"]["video_asset"][0]["content_sha256"] = descriptor["sha256"]
        publish(env, value)
        env["con"].rollback()
        assert old_path.read_bytes() == CONTENT
        with sqlite3.connect(env["target_path"]) as con:
            assert con.execute("SELECT content_sha256 FROM video_asset").fetchone()[0] == env["media"]["sha256"]
        publish(env, value)
        env["con"].commit()
        assert old_path.read_bytes() == CONTENT
        stored = json.loads(env["con"].execute("SELECT files_json FROM video_asset").fetchone()[0])
        assert config.resolve_media(stored["video"]).read_bytes() == updated
