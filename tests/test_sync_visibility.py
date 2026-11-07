"""Real FastAPI/SQLite reads with artificial ready/staging/error knowledge."""
import hashlib
import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from reno import ask, config, db, llm, report, review, sync_events, sync_reports, sync_visibility, storage_admin, storage_lifecycle

STAMP = "2026-09-30T12:00:00.000Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "FRAME_CACHE", tmp_path / "frames")
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "all", "web_ui": "classic",
                                         "ask_rate_per_min": 0, "ask_model": "controlled-model"})
    ask._answer_cache.clear()
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    con = db.connect()
    media = {}
    for video_id, state, text in (("staging", "staging", "未发布私密甲"), ("error", "error", "未发布私密乙"),
                                  ("ready_a", "ready", "公开甲"), ("ready_b", "ready", "公开乙")):
        data = ("media-" + video_id).encode()
        path = tmp_path / "media" / "originals" / video_id / "video.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        media[video_id] = path
        con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json,status,sync_state,"
                    "duration_ms,imported_at) VALUES(?,?,?,?,'processed',?,1000,?)",
                    (video_id, text, hashlib.sha256(data).hexdigest(), json.dumps({"video": str(path)}), state, STAMP))
        con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) VALUES('seg',?,0,100,?)", (video_id, text))
        atom = {"id": "atom_" + video_id, "claim": "防水材料甲 材料乙 " + text, "polarity": "recommend",
                "category": text, "space": text, "stage": text, "dimension": text,
                "conditions": {}, "parameters": [], "evidence_refs": [{"modality": "asr", "source_item_id": "seg",
                "video_id": video_id, "start_ms": 0, "end_ms": 100, "evidence_text": text}]}
        db.replace_atoms(con, video_id, [atom])
    for conflict_id, a, b in (("visible_conflict", "ready_a", "ready_b"), ("mixed_conflict", "ready_a", "staging"),
                              ("hidden_conflict", "staging", "error")):
        con.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json,analysis_json) "
                    "VALUES(?,'contrast','needs_review',?,?,'{}')", (conflict_id,
                    json.dumps({"atoms": ["atom_" + a], "video": a, "claim": a}),
                    json.dumps({"atoms": ["atom_" + b], "video": b, "claim": b})))
    for cid, members, linked in (("visible_cluster", ["atom_ready_a", "atom_ready_b"], "visible_conflict"),
                                  ("mixed_cluster", ["atom_ready_a", "atom_ready_b", "atom_staging"], "mixed_conflict"),
                                  ("hidden_cluster", ["atom_staging", "atom_error"], "hidden_conflict")):
        con.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json,linked_conflict) "
                    "VALUES(?,?,'conflicting',?,?)", (cid, cid, json.dumps(members), linked))
    con.execute("UPDATE knowledge_atom SET cluster_id='visible_cluster' WHERE video_id IN ('ready_a','ready_b')")
    con.execute("UPDATE knowledge_atom SET cluster_id='hidden_cluster' WHERE video_id IN ('staging','error')")
    review.record_decision(con, atom_id="atom_staging", action="confirm", note="未发布复核备注")
    review.record_decision(con, atom_id="atom_ready_a", action="confirm", note="公开复核备注")
    con.commit()
    config._cache["node_role"] = "cloud"
    from app.main import create_app
    client = TestClient(create_app())
    try:
        yield {"con": con, "client": client, "media": media, "tmp": tmp_path, "monkeypatch": monkeypatch}
    finally:
        client.close()
        con.close()
        ask._answer_cache.clear()


def install_reports(env, *, members=("ready_a", "ready_b"), contents=None):
    with storage_lifecycle.shared(storage_admin.profile()):
        return _install_reports(env, members=members, contents=contents)


def _install_reports(env, *, members, contents):
    con = env["con"]
    version = db.knowledge_version(con)
    publication_id = str(uuid.uuid4())
    source = {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 4}
    entries = []
    for name, content in (contents or {"summary": "报告正文"}).items():
        entries.append({"name": name, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "policy_version": report.POLICY_VERSION, "version": source, "generated_at": STAMP,
                        "review_events_sha256": sync_reports.event_fingerprint(con)})
    entries = sync_reports.prepare_files(report.out_dir(), sync_reports.validate_reports(entries, source),
                                         profile=storage_admin.profile())
    con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", (publication_id, source["library_id"],
                "source-node", 1, 4, "a" * 64, "b" * 64, STAMP, json.dumps(version)))
    for vid in members:
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)",
                    (publication_id, vid, source["library_id"], "source-node", 1, 4, "c" * 64, STAMP))
    sync_reports.install_pointers(con, entries, publication_id, version)
    con.commit()
    return publication_id


def qualified_report(content, *, unknown, historical=False):
    """Exact presentation contract; counts must exclude hidden relations."""
    prefix = ("> **历史报告：**正文使用的知识版本、复核或生成规则已变化；"
              "请更新报告后再用于当前判断。\n\n") if historical else ""
    return prefix + (
        "> **当前关联判定依据：需要更新。**\n"
        f"> 聚类：依据匹配 0；已变化 0；成员缺失 0；旧记录无法确认 {unknown}。\n"
        f"> 冲突：依据匹配 0；已变化 0；成员缺失 0；旧记录无法确认 {unknown}。\n"
        "> 下方正文保留生成时内容，未因本提示重算；请仅将依据匹配的判定用于当前归纳。"
        "请显式更新争议后再生成报告，更新争议会调用模型；尚无匹配判定不表示观点一致。\n\n"
    ) + content


def assert_raw_report(con, name, content):
    with storage_lifecycle.shared(storage_admin.profile()):
        manifest = report._manifest(name, con=con)
        raw = report.read_report(name, manifest=manifest, con=con)
    assert raw == content
    assert hashlib.sha256(raw.encode()).hexdigest() == manifest["content_sha256"]


def test_cloud_lists_stats_search_facets_compare_and_conflicts_are_ready_only(env):
    client = env["client"]
    data = client.get("/api/videos").json()
    assert {row["video_id"] for row in data["videos"]} == {"ready_a", "ready_b"}
    assert {key: data["stats"][key] for key in ("videos", "atoms", "clusters", "conflicts")} == {
        "videos": 2, "atoms": 2, "clusters": 1, "conflicts": 1}
    facets = client.get("/api/facets").json()
    assert all("未发布" not in value for values in facets.values() for value in values)
    search = client.get("/api/search", params={"q": "防水", "page": 1, "page_size": 1}).json()
    assert search["total"] == 2 and search["has_more"]
    assert search["results"][0]["video"].startswith("ready")
    assert client.get("/api/search", params={"category": "未发布私密甲"}).json()["total"] == 0
    conflicts = client.get("/api/conflicts").json()["conflicts"]
    assert [row["conflict_id"] for row in conflicts] == ["visible_conflict"]
    compare = client.get("/api/compare", params={"items": "材料甲,材料乙", "per_dimension_limit": 1}).json()
    assert all(side["total"] == 2 for side in compare["sides"])
    assert {row["cluster_id"] for row in compare["conflicts"]} == {"visible_cluster"}
    assert "未发布" not in json.dumps([data, facets, search, conflicts, compare], ensure_ascii=False)


@pytest.mark.parametrize("video_id", ["staging", "error", "guess_missing"])
def test_guessed_video_routes_and_ready_cached_media_are_not_readable(env, video_id):
    client = env["client"]
    for url in (f"/api/videos/{video_id}/meta", f"/api/video/{video_id}/events", f"/api/video/{video_id}/file",
                f"/api/frame/{video_id}/0", f"/videos/{video_id}"):
        assert client.get(url).status_code == 404, url
    assert client.get("/api/video/ready_a/file", headers={"Range": "bytes=0-4"}).content == b"media"
    assert client.get("/api/videos/ready_a/meta").json()["atoms"] == 1
    assert client.get("/api/video/ready_a/events").json()["events"][0]["text"] == "公开甲"


def test_frame_cache_is_keyed_by_media_hash_and_rechecks_visibility_and_file(env):
    from app import api
    from io import BytesIO
    from PIL import Image
    calls = []

    def jpeg(data):
        output = BytesIO()
        Image.new("RGB", (16, 16), tuple(hashlib.sha256(data).digest()[:3])).save(output, "JPEG")
        return output.getvalue()

    def frame(path, ms, output):
        data = path.read_bytes()
        calls.append(data)
        output.write_bytes(jpeg(data))
        return True

    env["monkeypatch"].setattr(api, "extract_frame", frame)
    client, con, media = env["client"], env["con"], env["media"]["ready_a"]
    assert client.get("/api/frame/ready_a/0").content == jpeg(b"media-ready_a")
    assert client.get("/api/frame/ready_a/0").status_code == 200 and len(calls) == 1
    media.write_bytes(b"new media version")
    con.execute("UPDATE video_asset SET content_sha256=? WHERE video_id='ready_a'", (hashlib.sha256(media.read_bytes()).hexdigest(),))
    con.commit()
    assert client.get("/api/frame/ready_a/0").content == jpeg(b"new media version") and len(calls) == 2
    con.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='ready_a'")
    con.commit()
    assert client.get("/api/frame/ready_a/0").status_code == 404
    con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id='ready_a'")
    con.commit()
    media.unlink()
    assert client.get("/api/frame/ready_a/0").status_code == 404 and len(calls) == 2


def test_classic_pages_use_the_same_visibility_scope(env):
    client = env["client"]
    for url in ("/", "/conflicts", "/search?q=防水"):
        response = client.get(url)
        assert response.status_code == 200
        assert "未发布" not in response.text and "mixed_conflict" not in response.text
        assert "mixed_cluster" not in response.text
    assert client.get("/videos/ready_a").status_code == 200


def test_invisible_reviews_are_rejected_but_internal_facts_and_replay_are_unfiltered(env):
    client, con = env["client"], env["con"]
    before = con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0]
    for payload in ({"atom_id": "atom_staging", "action": "confirm"},
                    {"conflict_id": "mixed_conflict", "action": "accept_a"}):
        response = client.post("/api/decision", json=payload)
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == before
    history = client.get("/api/reviews").json()["reviews"]
    assert [row["atom_id"] for row in history] == ["atom_ready_a"]
    assert client.get("/api/reviews", params={"kind": "atom", "target_id": "atom_staging"}).json()["reviews"] == []
    assert len(review.review_history(con)) == 2
    assert len(db.all_atoms(con)) == 4 and len(db.all_atoms(con, public=True)) == 2
    assert "atom_staging" in review.replay(con)["atoms"]
    assert con.execute("SELECT status FROM knowledge_atom WHERE id='atom_staging'").fetchone()[0] == "verified"
    con.rollback()
    assert len(sync_events.export_events(con)["events"]) == 2


def test_historical_conflict_snapshot_cannot_leak_now_hidden_sources(env):
    con, client = env["con"], env["client"]
    review.record_decision(con, conflict_id="mixed_conflict", action="both", note="旧快照含未发布内容")
    con.execute("UPDATE conflict_case SET side_b_json=? WHERE conflict_id='mixed_conflict'",
                (json.dumps({"atoms": ["atom_ready_b"], "video": "ready_b", "claim": "公开乙"}),))
    con.commit()
    assert sync_visibility.target_visible(con, "conflict", "mixed_conflict")
    assert review.review_history(con, "conflict", "mixed_conflict")
    assert client.get("/api/reviews", params={"kind": "conflict", "target_id": "mixed_conflict"}).json()["reviews"] == []


def test_ask_filters_model_materials_and_invalidates_cache_on_readiness_without_revision(env):
    client, con, seen = env["client"], env["con"], []

    def controlled(messages, **_kwargs):
        seen.append(messages)
        return "根据当前可见材料回答。[1]", "controlled-model"

    env["monkeypatch"].setattr(llm, "chat", controlled)
    first = client.post("/api/ask", json={"question": "防水", "k": 10}).json()
    assert {row["video"] for row in first["refs"]} == {"ready_a", "ready_b"}
    assert "未发布" not in json.dumps(seen, ensure_ascii=False)
    assert {row["cluster_id"] for row in first["conflicts"]} == {"visible_cluster"}
    version = db.knowledge_version(con)
    con.execute("UPDATE video_asset SET sync_state='error' WHERE video_id='ready_a'")
    con.commit()
    assert db.knowledge_version(con) == version
    second = client.post("/api/ask", json={"question": "防水", "k": 10}).json()
    assert {row["video"] for row in second["refs"]} == {"ready_b"}
    assert second["conflicts"] == [] and len(seen) == 2


def test_local_answer_cache_cannot_be_reused_on_cloud(env):
    seen = []
    env["monkeypatch"].setattr(llm, "chat", lambda messages, **_kwargs: (seen.append(messages) or "本地材料回答[1]", "controlled"))
    config._cache["node_role"] = "all"
    local = ask.ask("未发布私密甲")
    assert any(row["video"] == "staging" for row in local["refs"])
    config._cache["node_role"] = "cloud"
    cloud = ask.ask("未发布私密甲")
    assert all(row["video"].startswith("ready") for row in cloud["refs"])
    assert "未发布私密甲" not in json.dumps(cloud, ensure_ascii=False)


@pytest.mark.parametrize("role", ["all", "home"])
def test_local_roles_keep_unpublished_data_readable(env, role):
    config._cache["node_role"] = role
    client = env["client"]
    assert len(client.get("/api/videos").json()["videos"]) == 4
    assert client.get("/api/videos/staging/meta").status_code == 200
    assert client.get("/api/video/staging/events").json()["events"][0]["text"] == "未发布私密甲"
    assert len(client.get("/api/conflicts").json()["conflicts"]) == 3
    assert client.get("/api/search", params={"category": "未发布私密甲"}).json()["total"] == 1
    assert len(client.get("/api/reviews").json()["reviews"]) == 2


def test_synced_report_pointer_hides_generations_containing_unready_videos(env):
    install_reports(env, members=("ready_a", "staging"), contents={"summary": "不可见整代报告"})
    client, con = env["client"], env["con"]
    assert client.get("/api/reports").json()["reports"]["summary"] == ""
    meta = client.get("/api/reports/meta").json()["reports"]["summary"]
    assert not meta["has_content"] and meta["status"] != "latest"
    assert "不可见整代报告" not in client.get("/reports").text
    with storage_lifecycle.shared(storage_admin.profile()):
        assert report.read_report("summary", con=con) is None
    con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id='staging'")
    con.commit()
    assert client.get("/api/reports").json()["reports"]["summary"] == qualified_report("不可见整代报告", unknown=2)
    assert_raw_report(con, "summary", "不可见整代报告")
    assert client.get("/api/reports/meta").json()["reports"]["summary"]["status"] == "latest"


def test_report_membership_is_from_publication_not_unrelated_current_pending_videos(env):
    install_reports(env)
    assert env["client"].get("/api/reports").json()["reports"]["summary"] == qualified_report("报告正文", unknown=1)
    assert_raw_report(env["con"], "summary", "报告正文")


def test_legacy_report_requires_exact_current_basis_and_complete_readiness(env):
    root = report.out_dir()
    root.mkdir(parents=True, exist_ok=True)
    content = "旧文件报告内容"
    (root / "summary.md").write_text(content, encoding="utf-8")
    con, client = env["con"], env["client"]
    assert client.get("/api/reports").json()["reports"]["summary"] == ""
    manifest = {"report_type": "summary", **db.knowledge_version(con), "policy_version": report.POLICY_VERSION,
                "generated_at": STAMP, "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "status": "latest"}
    (root / "summary.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert client.get("/api/reports").json()["reports"]["summary"] == ""
    con.execute("UPDATE video_asset SET sync_state='ready'")
    con.commit()
    assert client.get("/api/reports").json()["reports"]["summary"] == qualified_report(content, unknown=3)
    assert_raw_report(con, "summary", content)
    con.execute("UPDATE knowledge_atom SET claim='新版知识' WHERE id='atom_ready_a'")
    con.commit()
    assert client.get("/api/reports").json()["reports"]["summary"] == ""
    with storage_lifecycle.shared(storage_admin.profile()):
        assert report.read_report("summary", con=con) is None
    config._cache["node_role"] = "home"
    assert client.get("/api/reports").json()["reports"]["summary"] == qualified_report(content, unknown=3, historical=True)
    assert_raw_report(con, "summary", content)


def test_reports_endpoint_reads_all_pointers_in_one_database_snapshot(env):
    install_reports(env, contents={"summary": "OLD summary", "checklist": "OLD checklist"})
    con = env["con"]
    old_pointers = [tuple(row) for row in con.execute("SELECT * FROM sync_report")]
    with storage_lifecycle.shared(storage_admin.profile()):
        old_manifests = {name: report._manifest(name, con=con) for name in ("summary", "checklist")}
    install_reports(env, contents={"summary": "NEW summary", "checklist": "NEW checklist"})
    new_pointers = [tuple(row) for row in con.execute("SELECT * FROM sync_report")]
    with storage_lifecycle.shared(storage_admin.profile()):
        new_manifests = {name: report._manifest(name, con=con) for name in ("summary", "checklist")}
    con.executemany("INSERT OR REPLACE INTO sync_report VALUES(?,?,?,?,?,?,?,?,?,?)", old_pointers)
    con.commit()
    original = report._read_report_content
    switched = []

    def replace_after_first_read(name, manifest, **kwargs):
        if not switched:
            with sqlite3.connect(config.db_path()) as writer:
                writer.executemany("INSERT OR REPLACE INTO sync_report VALUES(?,?,?,?,?,?,?,?,?,?)", new_pointers)
            switched.append(True)
        return original(name, manifest, **kwargs)

    env["monkeypatch"].setattr(report, "_read_report_content", replace_after_first_read)
    data = env["client"].get("/api/reports").json()["reports"]
    assert switched
    assert data["summary"] == qualified_report("OLD summary", unknown=1)
    assert data["checklist"] == qualified_report("OLD checklist", unknown=1)
    latest = env["client"].get("/api/reports").json()["reports"]
    assert latest["summary"] == qualified_report("NEW summary", unknown=1)
    assert latest["checklist"] == qualified_report("NEW checklist", unknown=1)
    for label, manifests in (("OLD", old_manifests), ("NEW", new_manifests)):
        for name, manifest in manifests.items():
            with storage_lifecycle.shared(storage_admin.profile()):
                raw = original(name, manifest, profile=storage_admin.profile())
            assert raw == f"{label} {name}"
            assert hashlib.sha256(raw.encode()).hexdigest() == manifest["content_sha256"]


def test_global_malformed_references_and_cross_video_evidence_fail_closed(env):
    con, client = env["con"], env["client"]
    con.execute("INSERT INTO conflict_case(conflict_id,side_a_json) VALUES('bad','not-json')")
    con.execute("INSERT INTO knowledge_cluster(cluster_id,members_json) VALUES('bad','{\"unexpected\":1}')")
    con.execute("UPDATE evidence_ref SET video_id='staging' WHERE atom_id='atom_ready_a'")
    con.commit()
    assert client.get("/api/search").json()["total"] == 1
    assert client.get("/api/conflicts").json()["conflicts"] == []
    assert client.get("/api/videos").json()["stats"]["atoms"] == 1


def test_visibility_is_applied_before_fts_limits_and_video_sort_limit(env):
    con, client = env["con"], env["client"]
    hidden_ids = {f"hidden_bulk_{i}" for i in range(230)}
    con.executemany("INSERT INTO knowledge_atom(id,video_id,claim,category) VALUES(?,'staging','防水','隐私分类')",
                    ((aid,) for aid in sorted(hidden_ids)))
    db.fts_sync_atoms(con, hidden_ids)
    # Move ready FTS entries after >200 hidden matches to prove the classic
    # limit is scoped in SQL, not filtered after selecting its first 200 rows.
    db.fts_sync_atoms(con, {"atom_ready_a", "atom_ready_b"})
    con.executemany("INSERT INTO video_asset(video_id,title,imported_at,sync_state) "
                    "VALUES(?, 'hidden late import','9999','staging')", ((f"later_{i}",) for i in range(501)))
    con.commit()
    raw_ids = {row[0] for row in con.execute("SELECT atom_id FROM atom_fts WHERE atom_fts MATCH ? LIMIT 200", ('"防 水"',))}
    assert not raw_ids.intersection({"atom_ready_a", "atom_ready_b"})
    first = client.get("/api/search", params={"q": "防水", "page_size": 1}).json()
    second = client.get("/api/search", params={"q": "防水", "page_size": 1, "page": 2}).json()
    assert first["total"] == second["total"] == 2
    assert first["results"][0]["id"] != second["results"][0]["id"]
    classic = client.get("/search?q=防水").text
    assert "公开甲" in classic and "公开乙" in classic and "隐私分类" not in classic
    listing = client.get("/api/videos").json()
    assert {row["video_id"] for row in listing["videos"]} == {"ready_a", "ready_b"}
    assert listing["stats"]["videos"] == 2


def test_ready_member_cluster_linked_to_hidden_conflict_is_hidden_whole(env):
    con = env["con"]
    con.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json,linked_conflict) "
                "VALUES('linked_hidden','hidden relationship','conflicting','[\"atom_ready_a\",\"atom_ready_b\"]','mixed_conflict')")
    con.execute("UPDATE knowledge_atom SET cluster_id='linked_hidden' WHERE id='atom_ready_a'")
    con.commit()
    assert {row[0] for row in con.execute("SELECT cluster_id FROM knowledge_cluster WHERE " + sync_visibility.cluster_sql())} == {"visible_cluster"}
    atom = next(row for row in db.all_atoms(con, public=True) if row["id"] == "atom_ready_a")
    assert atom["cluster_id"] is None
    assert "linked_hidden" not in env["client"].get("/api/compare?items=材料甲,材料乙").text


def test_report_pointer_version_must_match_its_publication(env):
    install_reports(env)
    env["con"].execute("UPDATE sync_report SET source_version_json=? WHERE name='summary'",
                       (json.dumps({"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 4}),))
    env["con"].commit()
    assert env["client"].get("/api/reports").json()["reports"]["summary"] == ""


def test_missing_atom_history_remains_readable_when_frozen_sources_are_ready(env):
    con, client = env["con"], env["client"]
    con.execute("DELETE FROM evidence_ref WHERE atom_id='atom_ready_a'")
    con.execute("DELETE FROM knowledge_atom WHERE id='atom_ready_a'")
    con.commit()
    response = client.get("/api/reviews", params={"kind": "atom", "target_id": "atom_ready_a"})
    events = response.json()["reviews"]
    assert len(events) == 1 and events[0]["binding_status"] == "missing"
    assert events[0]["target_snapshot"]["video_id"] == "ready_a"
    assert events[0]["target_snapshot"]["claim"].endswith("公开甲")
    con.execute("UPDATE video_asset SET sync_state='error' WHERE video_id='ready_a'")
    con.commit()
    assert client.get("/api/reviews", params={"kind": "atom", "target_id": "atom_ready_a"}).json()["reviews"] == []
    assert review.review_history(con, "atom", "atom_ready_a")[0]["binding_status"] == "missing"


def test_missing_conflict_and_members_use_frozen_ready_source_proof(env):
    con, client = env["con"], env["client"]
    review.record_decision(con, conflict_id="visible_conflict", action="both")
    con.execute("DELETE FROM conflict_case WHERE conflict_id='visible_conflict'")
    con.execute("DELETE FROM evidence_ref WHERE atom_id='atom_ready_a'")
    con.execute("DELETE FROM knowledge_atom WHERE id='atom_ready_a'")
    con.commit()
    events = client.get("/api/reviews", params={"kind": "conflict", "target_id": "visible_conflict"}).json()["reviews"]
    assert len(events) == 1 and events[0]["binding_status"] == "missing"
    assert events[0]["target_snapshot"]["side_a"]["members"][0]["content"]["video_id"] == "ready_a"
    con.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='ready_a'")
    con.commit()
    assert client.get("/api/reviews", params={"kind": "conflict", "target_id": "visible_conflict"}).json()["reviews"] == []


def test_unbound_missing_history_without_source_proof_is_hidden_not_deleted(env):
    con, client = env["con"], env["client"]
    event_id = str(uuid.uuid4())
    con.execute("INSERT INTO user_decision(atom_id,action,note,event_id,client_id,decided_at,created_at,"
                "target_snapshot_json,event_schema_version) VALUES('unknown','confirm','旧无来源记录',?,'legacy',?,?,?,0)",
                (event_id, STAMP, STAMP, '{"binding_status":"legacy_unbound","atom_id":"unknown"}'))
    con.commit()
    assert client.get("/api/reviews", params={"kind": "atom", "target_id": "unknown"}).json()["reviews"] == []
    assert review.review_history(con, "atom", "unknown")[0]["event_id"] == event_id


def test_cloud_cannot_read_legacy_local_task_payload_result_or_error(env):
    con, client = env["con"], env["client"]
    task_id = con.execute("INSERT INTO job(video_id,kind,status,payload_json,result_json,error) "
                          "VALUES('staging','process','failed',?,?,?)",
                          ('{"details":"private-task-payload"}', '{"result":"private-task-result"}',
                           'private-task-error')).lastrowid
    con.commit()
    response = client.get("/api/tasks")
    assert response.status_code == 200 and response.json() == {"tasks": []}
    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 404 and "private-task" not in detail.text
    for role in ("all", "home"):
        config._cache["node_role"] = role
        assert client.get(f"/api/tasks/{task_id}").json()["error"] == "private-task-error"
        assert client.get("/api/tasks").json()["tasks"][0]["result"] == {"result": "private-task-result"}


def test_invalid_event_time_does_not_bypass_missing_target_source_proof(env):
    con = env["con"]
    con.execute("INSERT INTO user_decision(atom_id,action,note,event_id,client_id,decided_at,created_at,"
                "target_fingerprint,target_snapshot_json,event_schema_version) "
                "VALUES('missing-time','confirm','unproven-history',?,'local','2099-01-01T00:00:00.000Z',?,?,'{}',1)",
                (str(uuid.uuid4()), STAMP, "v1:" + "a" * 64))
    con.commit()
    assert review.review_history(con, "atom", "missing-time")[0]["binding_status"] == "invalid_time"
    assert env["client"].get("/api/reviews", params={"kind": "atom", "target_id": "missing-time"}).json()["reviews"] == []
