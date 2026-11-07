"""Artificial complete generations: reference closure, privacy and read atomicity."""
import copy
import json
import sqlite3
import uuid

import pytest

from reno import db, storage_lifecycle, sync_protocol, sync_snapshot as snapshot
from reno.sync_protocol import SyncError

PUBLICATION = "b77fdbeb-c8fb-421d-b482-f6d72f8de25b"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.db"
    db.initialize(path)
    profile = storage_lifecycle.StorageProfile(path, tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    storage_lifecycle.initialize(profile)
    with storage_lifecycle.shared(profile):
        con = db.connect(path)
        media = {}
        for video_id, digest in (("v_1", "a" * 64), ("v_2", "b" * 64)):
            local = video_id == "v_1"
            con.execute("INSERT INTO video_asset(video_id,title,source_type,source_url,content_sha256,files_json,status) "
                        "VALUES(?,?,?,?,?,?,'processed')", (video_id, "原视频", "local_file" if local else "share_url",
                        "C:\\owner-private\\source.mp4" if local else "https://b23.tv/fixture",
                        digest, '{"video":"C:\\\\owner-private\\\\video.mp4","audio":"private-audio"}'))
            media[video_id] = {"video_id": video_id, "sha256": digest, "byte_size": 128, "extension": "mp4"}
            db.replace_frames(con, video_id, [
                {"frame_id": "f0000000", "pts_ms": 0, "origin": "scene", "dhash": "0x00", "path": "C:\\private\\frame.jpg"},
                {"frame_id": "unused_frame", "pts_ms": 100, "origin": "safety", "dhash": "0x01", "path": "/home/private/frame.jpg"},
            ])
            db.replace_segments(con, video_id, [
                {"id": "asr_0", "start_ms": 0, "end_ms": 100, "text": "原文"},
                {"id": "asr_1", "start_ms": 100, "end_ms": 200, "text": "补充原文"},
            ])
            db.replace_ocr(con, video_id, [{"id": "ocr_0", "start_ms": 0, "end_ms": 100,
                                          "text": "画面字幕", "conf": 0.8, "frame_ids": ["f0000000"]}])
            db.replace_visual(con, video_id, [{"id": "vis_0", "frame_id": "f0000000", "pts_ms": 0,
                                             "scene_description": "施工画面", "materials": ["砂浆"], "tools": []}])
            evidence = [{"modality": modality, "source_item_id": source_id, "start_ms": 0, "end_ms": 100,
                         "evidence_text": "保留证据", "weight": 0.8}
                        for modality, source_id in (("asr", "asr_0"), ("ocr", "ocr_0"), ("vision", "vis_0"))]
            atom = {"id": f"atom_{video_id}", "claim": "有证据的施工建议", "polarity": "recommend",
                    "conditions": {"空间": "卫生间", "基层": ["混凝土"]}, "parameters": [],
                    "evidence_refs": evidence}
            extra = {**atom, "id": f"extra_{video_id}", "claim": "另一项施工建议",
                     "evidence_refs": [{**evidence[0], "source_item_id": "asr_1"}]}
            db.replace_atoms(con, video_id, [atom, extra])
            for step in ("media", "asr", "ocr", "atomize"):
                con.execute("INSERT INTO meta VALUES(?,?)", (f"step:{video_id}:{step}", "2026-09-30T12:00:00+08:00"))
        db.replace_clusters(con, [
            {"cluster_id": "cluster_main", "canonical_topic": "施工建议", "relation": "conflicting",
             "members": ["atom_v_1", "atom_v_2"], "linked_conflict": "conflict_main"},
            {"cluster_id": "cluster_extra", "canonical_topic": "补充建议", "relation": "related",
             "members": ["extra_v_1", "extra_v_2"]},
        ])
        db.replace_conflicts(con, [{"conflict_id": "conflict_main", "type": "polarity_conflict",
                                   "members": {"side_a": {"atoms": ["atom_v_1"], "video": "v_1", "claim": "A"},
                                               "side_b": {"atoms": ["atom_v_2"], "video": "v_2", "claim": "B"}},
                                   "conditional_conclusion": {"authority_gap": False}}])
        db.add_decision(con, atom_id="atom_v_1", action="confirm")
        project_id = con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone()[0]
        con.execute("INSERT INTO source_record(source_id,project_id,source_type,title,created_at,updated_at) "
                    "VALUES('private-source',?,'manual_note','private-personal-secret','old','old')", (project_id,))
        con.execute("INSERT INTO meta VALUES('private_config','private-config-secret')")
        con.commit()
    try:
        yield con, media, path
    finally:
        con.close()


def export(source, **changes):
    con, media, _path = source
    if not con.in_transaction:
        con.execute("BEGIN")
    return snapshot.export_snapshot(con, publication_id=changes.pop("publication_id", PUBLICATION),
                                    media=changes.pop("media", media), **changes)


@pytest.fixture
def payload(source):
    return export(source)


def video(payload, video_id="v_1"):
    return next(row for row in payload["videos"] if row["video_id"] == video_id)


def main_atom(payload, video_id="v_1"):
    return next(row for row in video(payload, video_id)["tables"]["knowledge_atom"] if row["id"] == f"atom_{video_id}")


def main_cluster(payload):
    return next(row for row in payload["globals"]["knowledge_cluster"] if row["cluster_id"] == "cluster_main")


def test_export_preserves_processed_knowledge_but_excludes_paths_status_and_private_data(source):
    con, _media, _path = source
    before = con.total_changes
    original = dict(con.execute("SELECT key,value FROM meta"))
    result = export(source)
    assert con.in_transaction and con.total_changes == before
    assert dict(con.execute("SELECT key,value FROM meta")) == original
    encoded = json.dumps(result)
    assert all(secret not in encoded for secret in ("owner-private", "private-personal-secret", "private-config-secret", "private-audio"))
    assert set(result) == {"publication_id", "version", "videos", "globals", "reports"}
    assert len(result["videos"]) == 2
    for closure in result["videos"]:
        tables = closure["tables"]
        assert set(tables) == set(snapshot.VIDEO_TABLES)
        assert tables["video_asset"][0]["status"] == "processed"
        assert "files_json" not in tables["video_asset"][0]
        assert all(row["path"] is None for row in tables["frame"])
        assert all("status" not in row for row in tables["knowledge_atom"])
        assert all("id" not in row for row in tables["evidence_ref"])
        assert all(row["key"].startswith(f"step:{closure['video_id']}:") for row in tables["meta"])
    assert video(result)["tables"]["video_asset"][0]["source_url"] is None
    assert video(result, "v_2")["tables"]["video_asset"][0]["source_url"] == "https://b23.tv/fixture"
    assert all("status" not in row for row in result["globals"]["conflict_case"])
    assert con.execute("SELECT status FROM knowledge_atom WHERE id='atom_v_1'").fetchone()[0] == "verified"


def test_export_requires_existing_transaction_and_never_commits_caller_changes(source):
    con, media, path = source
    assert not con.in_transaction
    with pytest.raises(SyncError) as error:
        snapshot.export_snapshot(con, publication_id=PUBLICATION, media=media)
    assert error.value.code == "SYNC_READ_TRANSACTION_REQUIRED"
    assert not con.in_transaction
    con.execute("BEGIN")
    con.execute("UPDATE knowledge_atom SET claim='调用方尚未提交的知识' WHERE id='atom_v_1'")
    result = snapshot.export_snapshot(con, publication_id=PUBLICATION, media=media)
    assert main_atom(result)["claim"] == "调用方尚未提交的知识"
    assert con.in_transaction
    with sqlite3.connect(path) as reader:
        assert reader.execute("SELECT claim FROM knowledge_atom WHERE id='atom_v_1'").fetchone()[0] == "有证据的施工建议"
    con.rollback()


def test_wal_writer_cannot_mix_revision_or_rows_during_export(source):
    con, media, path = source
    con.execute("BEGIN")
    original_version = db.knowledge_version(con)
    changed = []
    writer = sqlite3.connect(path)

    def change_between_selects(statement):
        if not changed and 'FROM "knowledge_atom"' in statement:
            writer.execute("UPDATE knowledge_atom SET claim='并发新版本' WHERE id='atom_v_1'")
            writer.commit()
            changed.append(True)

    con.set_trace_callback(change_between_selects)
    try:
        result = snapshot.export_snapshot(con, publication_id=PUBLICATION, media=media)
        assert changed and result["version"] == original_version
        assert main_atom(result)["claim"] == "有证据的施工建议"
        assert writer.execute("SELECT claim FROM knowledge_atom WHERE id='atom_v_1'").fetchone()[0] == "并发新版本"
    finally:
        con.set_trace_callback(None)
        writer.close()


def test_unprocessed_empty_video_is_ignored_but_old_atoms_in_rerun_are_not(source):
    con, _media, _path = source
    con.execute("INSERT INTO video_asset(video_id,status) VALUES('pending','imported')")
    result = export(source)
    assert {row["video_id"] for row in result["videos"]} == {"v_1", "v_2"}
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    with pytest.raises(SyncError) as error:
        export(source)
    assert error.value.code == "SYNC_INCOMPLETE_SNAPSHOT"


def test_missing_media_or_orphan_source_atoms_cannot_silently_shrink_a_generation(source):
    con, media, _path = source
    with pytest.raises(SyncError) as error:
        export(source, media={"v_1": media["v_1"]})
    assert error.value.code == "SYNC_INCOMPLETE_SNAPSHOT"
    con.execute("DELETE FROM video_asset WHERE video_id='v_2'")
    with pytest.raises(SyncError, match="knowledge atoms"):
        export(source)


def test_source_global_dependencies_are_not_filtered_to_the_processed_subset(source):
    con, _media, _path = source
    con.execute("INSERT INTO video_asset(video_id,status) VALUES('pending','imported')")
    con.execute("INSERT INTO conflict_case(conflict_id,side_a_json) VALUES('other',?)",
                ('{"atoms":[],"video":"pending"}',))
    with pytest.raises(SyncError, match="outside the snapshot"):
        export(source)


def test_removed_unreferenced_outputs_disappear_but_referenced_removals_block_export(source):
    con, _media, _path = source
    first = export(source)
    con.execute("DELETE FROM frame WHERE video_id='v_1' AND frame_id='unused_frame'")
    second = export(source)
    assert len(video(first)["tables"]["frame"]) == 2
    assert len(video(second)["tables"]["frame"]) == 1
    # asr_0 still exists in v_2, which cannot repair a v_1 evidence pointer.
    con.execute("DELETE FROM transcript_segment WHERE video_id='v_1' AND id='asr_0'")
    with pytest.raises(SyncError, match="within its video"):
        export(source)


@pytest.mark.parametrize("damage", [
    "ocr_frame", "visual_frame", "evidence_atom", "evidence_other_atom", "evidence_source",
    "evidence_modality", "source_other_video", "step_other_video", "ocr_reference_shape",
])
def test_local_reference_graph_cannot_cross_video_or_source_kind(payload, damage):
    tables = video(payload)["tables"]
    evidence = next(row for row in tables["evidence_ref"] if row["modality"] == "asr")
    if damage == "ocr_frame":
        tables["ocr_span"][0]["frame_ids_json"] = '["missing-frame"]'
    elif damage == "visual_frame":
        tables["visual_observation"][0]["frame_id"] = "missing-frame"
    elif damage == "evidence_atom":
        evidence["atom_id"] = "ghost"
    elif damage == "evidence_other_atom":
        evidence["atom_id"] = "atom_v_2"
    elif damage == "evidence_source":
        evidence["source_item_id"] = "ghost"
    elif damage == "evidence_modality":
        evidence["modality"] = "vision"
    elif damage == "source_other_video":
        tables["transcript_segment"] = [row for row in tables["transcript_segment"] if row["id"] != evidence["source_item_id"]]
    elif damage == "step_other_video":
        tables["meta"][0]["key"] = "step:v_2:atomize"
    else:
        tables["ocr_span"][0]["frame_ids_json"] = '[{"id":"f0000000"}]'
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


@pytest.mark.parametrize("damage", [
    "cluster_member", "atom_pointer", "membership_reverse", "linked_conflict", "linked_unrelated",
    "conflict_atom", "conflict_video", "conflict_representative", "conflict_ref_shape", "duplicate_atom",
])
def test_global_references_and_two_way_memberships_must_close(payload, damage):
    cluster = main_cluster(payload)
    conflict = payload["globals"]["conflict_case"][0]
    if damage == "cluster_member":
        cluster["members_json"] = '["atom_v_1","missing"]'
    elif damage == "atom_pointer":
        main_atom(payload)["cluster_id"] = "missing"
    elif damage == "membership_reverse":
        main_atom(payload)["cluster_id"] = None
    elif damage == "linked_conflict":
        cluster["linked_conflict"] = "missing"
    elif damage == "linked_unrelated":
        conflict["side_a_json"] = '{"atoms":["extra_v_1"],"video":"v_1"}'
    elif damage == "conflict_atom":
        conflict["side_a_json"] = '{"atoms":["missing"]}'
    elif damage == "conflict_video":
        conflict["side_a_json"] = '{"atoms":["atom_v_1"],"video":"missing"}'
    elif damage == "conflict_representative":
        conflict["side_a_json"] = '{"atoms":["atom_v_1"],"video":"v_2"}'
    elif damage == "conflict_ref_shape":
        conflict["side_a_json"] = '{"atoms":[{"id":"atom_v_1"}]}'
    else:
        main_atom(payload)["id"] = "atom_v_2"
        for row in video(payload)["tables"]["evidence_ref"]:
            if row["atom_id"] == "atom_v_1":
                row["atom_id"] = "atom_v_2"
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


def test_nullable_legacy_pointers_and_representative_video_are_supported(payload):
    video(payload)["tables"]["visual_observation"][0]["frame_id"] = None
    video(payload)["tables"]["evidence_ref"][0]["source_item_id"] = None
    payload["globals"]["conflict_case"][0]["side_a_json"] = '{"atoms":["atom_v_1","atom_v_2"],"video":"v_1"}'
    result = snapshot.validate_snapshot(payload)
    assert result["videos"]
    payload["globals"]["conflict_case"][0]["side_a_json"] = '{"atoms":["atom_v_1"],"video":null}'
    assert snapshot.validate_snapshot(payload)


@pytest.mark.parametrize("damage", [
    "unknown_top", "unknown_video", "personal_table", "missing_table", "event_global", "asset_count",
    "asset_state", "asset_digest", "media_video", "frame_path", "atom_status", "conflict_status",
    "local_source_url", "duplicate_video", "publication_id", "version_id", "version_epoch", "version_revision",
    "version_extra", "wrong_video_scope",
])
def test_exact_envelope_and_media_identity_reject_authority_expansion(payload, damage):
    closure = video(payload)
    tables = closure["tables"]
    if damage == "unknown_top":
        payload["personal"] = {}
    elif damage == "unknown_video":
        closure["path"] = "C:\\private"
    elif damage == "personal_table":
        tables["decision_card"] = []
    elif damage == "missing_table":
        tables.pop("ocr_span")
    elif damage == "event_global":
        payload["globals"]["user_decision"] = []
    elif damage == "asset_count":
        tables["video_asset"] = []
    elif damage == "asset_state":
        tables["video_asset"][0]["status"] = "done_atomize"
    elif damage == "asset_digest":
        tables["video_asset"][0]["content_sha256"] = "f" * 64
    elif damage == "media_video":
        closure["media"]["video_id"] = "v_2"
    elif damage == "frame_path":
        tables["frame"][0]["path"] = "media/private.jpg"
    elif damage == "atom_status":
        main_atom(payload)["status"] = "verified"
    elif damage == "conflict_status":
        payload["globals"]["conflict_case"][0]["status"] = "decided:accept_a"
    elif damage == "local_source_url":
        tables["video_asset"][0]["source_url"] = "https://b23.tv/not-a-local-source"
    elif damage == "duplicate_video":
        payload["videos"].append(copy.deepcopy(closure))
    elif damage == "publication_id":
        payload["publication_id"] = "not-uuid"
    elif damage == "version_id":
        payload["version"]["library_id"] = "not-uuid"
    elif damage == "version_epoch":
        payload["version"]["library_epoch"] = True
    elif damage == "version_revision":
        payload["version"]["kb_revision"] = -1
    elif damage == "version_extra":
        payload["version"]["primary_epoch"] = 99
    else:
        main_atom(payload)["video_id"] = "v_2"
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


def test_normalization_is_detached_and_stable_across_payload_order(payload):
    payload["reports"] = [{"name": "z.md", "opaque": {"keep": [1, 2]}}, {"name": "a.md", "opaque": "pass through"}]
    original = copy.deepcopy(payload)
    reordered = copy.deepcopy(payload)
    reordered["videos"].reverse()
    reordered["reports"].reverse()
    for closure in reordered["videos"]:
        for rows in closure["tables"].values():
            rows.reverse()
    for rows in reordered["globals"].values():
        rows.reverse()
    main_atom(reordered)["conditions_json"] = json.dumps(json.loads(main_atom(reordered)["conditions_json"]), ensure_ascii=False, indent=4)
    normal = snapshot.validate_snapshot(payload)
    assert normal == snapshot.validate_snapshot(reordered)
    assert payload == original
    normal["reports"][1]["opaque"]["keep"].append(3)
    assert payload == original


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1: "non-string key"}, ("tuple",), "\ud800"])
def test_report_passthrough_still_requires_finite_strict_json(payload, bad):
    payload["reports"] = [{"opaque": bad}]
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


def test_global_byte_limits_counts_and_deep_or_cyclic_json_are_bounded(payload, monkeypatch):
    with monkeypatch.context() as small:
        small.setattr(snapshot, "MAX_SNAPSHOT_BYTES", 2000)
        with pytest.raises(SyncError) as error:
            snapshot.validate_snapshot(payload)
        assert error.value.status == 413
    with monkeypatch.context() as small:
        small.setattr(snapshot, "MAX_VIDEOS", 1)
        with pytest.raises(SyncError):
            snapshot.validate_snapshot(payload)
    with monkeypatch.context() as small:
        small.setattr(sync_protocol, "MAX_ROWS", 1)
        with pytest.raises(SyncError):
            snapshot.validate_snapshot(payload)
    payload["reports"] = [{}] * 6
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)
    cyclic = []
    cyclic.append(cyclic)
    payload["reports"] = [cyclic]
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


def test_empty_closed_generation_is_valid_but_not_an_implicit_filter_for_globals():
    payload = {"publication_id": PUBLICATION, "version": {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 0},
               "videos": [], "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    assert snapshot.validate_snapshot(payload) == payload
    payload["globals"]["knowledge_cluster"] = [{"cluster_id": "c1", "members_json": '["a1"]'}]
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)


def test_export_step_scope_uses_literal_underscore_and_table_limits(source, monkeypatch):
    con, _media, _path = source
    con.execute("INSERT INTO meta VALUES('step:vX1:asr','must not match v_1')")
    result = export(source)
    assert all(row["key"].startswith("step:v_1:") for row in video(result)["tables"]["meta"])
    monkeypatch.setattr(sync_protocol, "MAX_ROWS", 1)
    with pytest.raises(SyncError):
        export(source)
