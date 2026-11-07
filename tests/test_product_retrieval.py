"""Product retrieval contracts, independent of live models or private data."""
import json

import pytest
from fastapi.testclient import TestClient

from reno import ask, config, db, review, storage_admin


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RENO_DATA_DIR", str(tmp_path / "data"))
    for name, path in {"DATA": tmp_path / "data", "MEDIA": tmp_path / "media",
                       "ORIG": tmp_path / "media" / "originals", "AUDIO": tmp_path / "media" / "audio",
                       "FRAMES": tmp_path / "media" / "frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    db.initialize()
    storage_admin.setup()
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json,imported_at) "
                "VALUES ('v','测试来源','test-sha','{}','2000-01-01')")
    for i in range(160):
        atom_id = f"a{i:03}"
        con.execute(
            """INSERT INTO knowledge_atom(id,video_id,claim,polarity,status,category,space,stage,
               dimension,conditions_json,parameters_json,exceptions,prices_json,evidence_nature)
               VALUES (?, 'v', ?, 'recommend','candidate',?,'卫生间','防水施工',?,'{}','[]',?,?, 'author_opinion')""",
            (atom_id, f"防水材料 方案甲 第{i}项", "特殊工种" if i == 159 else "防水",
             "验收质检" if i == 159 else "施工工艺", json.dumps(["不适用于松散基层"], ensure_ascii=False),
             json.dumps([{"object": "防水材料", "amount": 88, "unit": "元/桶", "includes": ["材料"]}], ensure_ascii=False)))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                    "VALUES (?,'v','asr',?,0,500,'来源原话')", (atom_id, atom_id))
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim,polarity,status,conditions_json,parameters_json) "
                "VALUES ('b','v','防水材料 方案乙','recommend','candidate','{}','[]')")
    db.fts_sync_atoms(con, [r[0] for r in con.execute("SELECT id FROM knowledge_atom")])
    con.commit()
    ask._answer_cache.clear()
    from app.main import create_app
    client = TestClient(create_app())
    yield con, client
    con.close()
    ask._answer_cache.clear()


def test_filter_applies_before_retrieval_cap_and_empty_query_browses(env):
    _, client = env
    body = client.get("/api/search", params={"q": "防水", "category": "特殊工种"}).json()
    assert [a["id"] for a in body["results"]] == ["a159"]
    first = client.get("/api/search", params={"space": "卫生间", "page_size": 100}).json()
    second = client.get("/api/search", params={"space": "卫生间", "page_size": 100, "page": 2}).json()
    assert first["total"] == 160 and first["has_more"] is True
    assert len(second["results"]) == 60 and not second["has_more"]
    assert not ({a["id"] for a in first["results"]} & {a["id"] for a in second["results"]})


def test_compare_coverage_checks_all_filtered_atoms(env):
    _, client = env
    body = client.get("/api/compare", params={"items": "方案甲,方案乙"}).json()
    side = body["sides"][0]
    assert side["coverage_scope"] == "full_filtered_set"
    assert side["total"] == 160 and side["truncated"]
    assert "验收质检" not in side["missing_dimensions"]
    process = next(g for g in side["groups"] if g["dimension"] == "施工工艺")
    assert process["total"] == 159 and len(process["atoms"]) == 5


def test_answer_material_carries_exceptions_prices_and_unknown_dates(env, monkeypatch):
    messages = []
    monkeypatch.setattr(ask.llm, "chat", lambda m, **_: (messages.extend(m) or "测试回答 [1]。", "stub"))
    result = ask.ask("防水", k=1, context_snapshot={"基层": "水泥", "未定义": "应忽略"})
    assert messages, "a non-empty material set must reach the controlled model"
    assert result["degraded"] is False
    assert "budget_wait" not in result
    material = messages[-1]["content"]
    assert "不适用于松散基层" in material and '"amount":88' in material
    assert "来源发布日期:未知" in material and "2000-01-01" not in material
    assert result["context_used"] == {"基层": "水泥"}
    assert result["context_unknown"] == ["未定义"]
    assert result["refs"][0]["exceptions"] and result["refs"][0]["target_fingerprint"]
    assert result["refs"][0]["condition_match"]["status"] == "unknown"


def test_comparison_conditions_in_body_are_assessed_without_hiding_counterevidence(env):
    _, client = env
    result = client.post("/api/compare", json={"items": "方案甲,方案乙", "context_snapshot": {"空间": "厨房"}})
    assert result.status_code == 200
    row = result.json()["sides"][0]["groups"][0]["atoms"][0]
    assert row["condition_match"]["status"] == "mismatch"
    assert result.json()["sides"][0]["total"] == 160


def test_context_and_in_place_changes_invalidate_cached_answers(env, monkeypatch):
    con, _ = env
    calls = []
    monkeypatch.setattr(ask.llm, "chat", lambda messages, **_: (calls.append(messages) or "答 [1]", "stub"))
    responses = [ask.ask("防水", k=1, context_snapshot={"基层": "水泥"}),
                 ask.ask("防水", k=1, context_snapshot={"基层": "水泥"})]
    assert len(calls) == 1
    responses.append(ask.ask("防水", k=1, context_snapshot={"基层": "木板"}))
    assert len(calls) == 2
    con.execute("UPDATE knowledge_atom SET exceptions='[\"新增例外\"]' WHERE id='a000'")
    con.commit()
    responses.append(ask.ask("防水", k=1, context_snapshot={"基层": "水泥"}))
    assert len(calls) == 3
    assert all(result["degraded"] is False and "budget_wait" not in result for result in responses)


def test_rejected_atoms_are_excluded_but_review_history_is_available(env):
    con, client = env
    result = client.post("/api/decision", json={"atom_id": "a159", "action": "reject"})
    assert result.status_code == 200
    assert not client.get("/api/search", params={"category": "特殊工种"}).json()["results"]
    excluded = client.get("/api/search", params={"status": "rejected"}).json()
    assert excluded["results"][0]["id"] == "a159"
    assert client.get("/api/reviews", params={"kind": "atom", "target_id": "a159"}).json()["reviews"]
    assert db.knowledge_version(con)["kb_revision"] > 0


def test_follow_up_uses_user_context_and_never_old_assistant_claims():
    q, scope = ask.retrieval_question("那地暖呢？", [
        {"role": "user", "content": "方案甲和方案乙的防水材料"},
        {"role": "assistant", "content": "不得把这段生成结论当成新事实"}], {})
    assert "方案甲" in q and "地暖" in q and "生成结论" not in q
    assert scope == "follow_up_user_context"


def test_unclear_pronoun_requests_clarification_without_model(env, monkeypatch):
    monkeypatch.setattr(ask.llm, "chat", lambda *a, **k: pytest.fail("must not call a model"))
    result = ask.ask("那个呢？")
    assert result["retrieval_scope"] == "needs_clarification" and result["refs"] == []
