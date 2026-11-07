"""Current relation inference stays separate from historical model output."""
import hashlib
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from reno import ask, config, db, derivation, report, storage_admin, storage_lifecycle


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "all"})
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    with storage_lifecycle.shared(storage_admin.profile()):
        con = db.connect()
        for number, name in ((1, "甲"), (2, "乙")):
            video, atom = f"v{number}", f"a{number}"
            db.upsert_asset(con, {"video_id": video, "title": f"人工来源{name}", "content_sha256": video})
            con.execute("INSERT INTO knowledge_atom(id,video_id,category,space,subject,claim,polarity,"
                        "conditions_json,parameters_json,status) VALUES(?,?,'防水','卫生间',?,?,'recommend','{}','[]','candidate')",
                        (atom, video, f"方案{name}", f"独有{name}词 方案{name} 防水建议"))
            con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                        "VALUES(?,?,'asr',?,0,1000,?)", (atom, video, atom, f"人工证据{name}"))
        atoms = {a["id"]: a for a in db.all_atoms(con)}
        binding = derivation.bind_inputs(atoms, ["a1", "a2"])
        db.replace_clusters(con, [{"cluster_id": "clu_fixture", "canonical_topic": "人工关系主题", "relation": "conflicting",
                                  "members": ["a1", "a2"], "linked_conflict": "cfl_fixture", "derivation_json": binding}])
        db.replace_conflicts(con, [{"conflict_id": "cfl_fixture", "type": "numeric_conflict",
                                   "members": {"side_a": {"atoms": ["a1"], "claim": "历史甲说法", "video": "v1"},
                                               "side_b": {"atoms": ["a2"], "claim": "历史乙说法", "video": "v2"}},
                                   "judge_note": "历史判定独有分析", "recommended_action": "历史判定独有动作",
                                   "derivation_json": binding}])
        db.fts_sync_atoms(con, ["a1", "a2"])
        con.commit()
    ask._answer_cache.clear()
    from app import api, pages, task_api
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(task_api.router)
    app.include_router(pages.router)
    yield con, TestClient(app)
    con.close()
    ask._answer_cache.clear()


def change_basis(con, state):
    if state == "stale":
        # Classification is part of judge input but not the personal-review
        # content identity. This deliberately exercises the independent axes.
        con.execute("UPDATE knowledge_atom SET category='水电' WHERE id='a1'")
    elif state == "unknown":
        for table in ("knowledge_cluster", "conflict_case"):
            con.execute(f"UPDATE {table} SET derivation_json=NULL")
    elif state == "missing":
        con.execute("DELETE FROM evidence_ref WHERE atom_id='a2'")
        con.execute("DELETE FROM knowledge_atom WHERE id='a2'")
    con.commit()


@pytest.mark.parametrize("state", ["active", "stale", "missing", "unknown"])
def test_ask_only_augments_from_current_relations_and_qualifies_history(library, monkeypatch, state):
    con, _ = library
    change_basis(con, state)
    messages = []
    monkeypatch.setattr(ask.llm, "chat", lambda value, **_: (messages.extend(value) or "人工回答 [1]", "stub"))
    result = ask.ask("独有甲词", k=1)
    assert result["conflicts"][0]["derivation"]["state"] == state
    assert {ref["atom_id"] for ref in result["refs"]} == ({"a1", "a2"} if state == "active" else {"a1"})
    material = messages[-1]["content"]
    if state == "active":
        assert "本次材料包含存在分歧的聚类" in material
    else:
        assert "历史关联判定待更新" in material
        assert "本次材料包含存在分歧的聚类" not in material
        assert result["refs"][0]["has_outdated_conflict"] is True


def test_outdated_cluster_is_found_even_when_atomize_clears_pointer(library, monkeypatch):
    con, _ = library
    change_basis(con, "stale")
    con.execute("UPDATE knowledge_atom SET cluster_id=NULL WHERE id='a1'")
    con.commit()
    monkeypatch.setattr(ask.llm, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    value = ask.ask("独有甲词", k=1)
    assert value["degraded"] is True
    assert value["conflicts"][0]["derivation"]["state"] == "stale"
    assert "历史关联判定待更新" in value["answer"]


def test_standalone_old_conflict_qualifies_model_material_without_a_cluster(library, monkeypatch):
    con, _ = library
    change_basis(con, "stale")
    con.execute("UPDATE knowledge_atom SET cluster_id=NULL")
    con.execute("DELETE FROM knowledge_cluster")
    con.commit()
    messages = []
    monkeypatch.setattr(ask.llm, "chat", lambda value, **_: (messages.extend(value) or "人工回答 [1]", "stub"))
    value = ask.ask("独有甲词", k=1)
    assert value["conflicts"] == []
    assert value["refs"][0]["has_outdated_conflict"] is True
    assert "历史关联判定待更新" in messages[-1]["content"]


@pytest.mark.parametrize("state", ["active", "stale", "unknown"])
def test_api_and_classic_expose_separate_validity_and_keep_review_writable(library, state):
    con, client = library
    change_basis(con, state)
    value = client.get("/api/conflicts").json()["conflicts"][0]
    assert value["derivation"]["state"] == state and "derivation_json" not in value
    compared = client.get("/api/compare", params={"items": "方案甲,方案乙"}).json()
    assert compared["conflicts"][0]["derivation"]["state"] == state
    atom = client.get("/api/search", params={"q": "独有甲词", "page_size": 1}).json()["results"][0]
    assert atom["has_conflict"] is (state == "active")
    assert atom["has_outdated_conflict"] is (state != "active")
    body = {"conflict_id": value["conflict_id"], "event_id": str(uuid.uuid4()),
            "target_fingerprint": value["target_fingerprint"], "action": "both"}
    assert client.post("/api/decision", json=body).status_code == 200
    assert client.post("/api/decision", json=body).status_code == 200
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
    html = client.get("/conflicts").text
    assert "重新判定（调用模型）" in html
    if state != "active":
        assert "仅记录个人意见，不会更新判定依据" in html
    assert client.get("/api/conflicts").json()["conflicts"][0]["derivation"]["state"] == state


def test_cloud_classic_and_api_do_not_pretend_to_schedule_local_judge(library, monkeypatch):
    _, client = library
    monkeypatch.setattr(config, "is_cloud", lambda: True)
    html = client.get("/conflicts").text
    assert "请在家庭节点更新争议" in html and 'id="rejudge"' not in html
    assert client.post("/api/tasks", json={"kind": "judge", "request_id": str(uuid.uuid4())}).status_code == 403


def test_status_filter_uses_current_projection_when_migration_keeps_old_raw_state(library):
    con, client = library
    change_basis(con, "unknown")
    con.execute("UPDATE knowledge_atom SET status='disputed'")
    con.commit()
    assert not client.get("/api/search", params={"q": "防水", "status": "disputed"}).json()["results"]
    current = client.get("/api/search", params={"q": "防水", "status": "candidate", "page_size": 1}).json()
    assert current["total"] == 2 and len(current["results"]) == 1
    assert current["results"][0]["status"] == "candidate"
    assert all(row[0] == "disputed" for row in con.execute("SELECT status FROM knowledge_atom"))


@pytest.mark.parametrize("state", ["active", "stale", "unknown"])
def test_reports_exclude_outdated_inference_but_preserve_qualified_history(library, monkeypatch, state):
    con, client = library
    change_basis(con, state)
    result = report.diff_report()
    assert result["clusters"] == (1 if state == "active" else 0)
    assert result["cluster_coverage"] == (1 if state == "active" else 0)
    diff = report.read_report("incremental_diff")
    assert "全新观点" not in diff
    if state != "active":
        assert diff.count("尚未确认关联 1") == 2
        assert "首次出现的已判定观点 1" not in diff
    report.conflict_report()
    body = report.read_report("conflicts")
    assert "历史判定独有分析" in body
    if state != "active":
        assert "生成时的关联判定依据：需要更新" in body
        assert "以下分析保留供回看" in body
    messages = []
    monkeypatch.setattr(ask.llm, "chat", lambda value, **_: (messages.extend(value) or "合成总结", "stub"))
    report.summary_report()
    assert ("历史判定独有动作" in messages[-1]["content"]) is (state == "active")
    metadata = client.get("/api/reports/meta").json()["reports"]["conflicts"]
    assert metadata["status"] == "latest"
    assert metadata["derivation_summary"]["conflicts"][state] == 1
    manifest = json.loads((report.out_dir() / "conflicts.manifest.json").read_text(encoding="utf-8"))
    assert manifest["derivation_summary"]["conflicts"][state] == 1
    assert hashlib.sha256(body.encode()).hexdigest() == manifest["content_sha256"]
    shown = client.get("/api/reports").json()["reports"]["conflicts"]
    if state != "active":
        assert "当前关联判定依据：需要更新" in shown
        assert "当前关联判定依据：需要更新" in client.get("/reports").text


def test_old_report_download_is_qualified_without_rewriting_immutable_source(library):
    con, client = library
    change_basis(con, "unknown")
    report.conflict_report()
    path = report.out_dir() / "conflicts.manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["policy_version"] = "reports-v2"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    raw = report.read_report("conflicts")
    shown = client.get("/api/reports").json()["reports"]["conflicts"]
    assert "历史报告：" in shown and "当前关联判定依据" in shown
    assert report.read_report("conflicts") == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == manifest["content_sha256"]
