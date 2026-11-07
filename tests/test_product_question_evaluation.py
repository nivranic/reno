"""20 synthetic renovation cases: retrieval/material QA, NOT live-model quality."""
import json
from pathlib import Path

import pytest

from reno import ask, config, db, storage_admin
from reno.dict import stage_for

FIXTURE = json.loads((Path(__file__).parent / "fixtures/product_questions.json").read_text(encoding="utf-8"))


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("RENO_DATA_DIR", str(tmp_path / "data"))
    for name, path in {"DATA": tmp_path / "data", "MEDIA": tmp_path / "media",
                       "ORIG": tmp_path / "media" / "originals", "AUDIO": tmp_path / "media" / "audio",
                       "FRAMES": tmp_path / "media" / "frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    db.initialize()
    storage_admin.setup()
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,title,files_json,content_sha256) VALUES('qa','人工样本','{}','qa')")
    for atom in FIXTURE["atoms"]:
        con.execute("INSERT INTO knowledge_atom(id,video_id,category,space,stage,subject,claim,polarity,status,dimension,"
                    "conditions_json,parameters_json,exceptions,prices_json,evidence_nature) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (atom["id"], "qa", atom["category"], atom["space"], stage_for(atom["category"]), atom["subject"], atom["claim"],
                     "recommend", "candidate", atom["dimension"], db.j(atom["conditions"]), "[]", db.j(atom["exceptions"]),
                     db.j(atom.get("prices", [])), atom["evidence_nature"]))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                    "VALUES(?,'qa','asr',?,0,1000,?)", (atom["id"], atom["id"], atom["claim"]))
    db.fts_sync_atoms(con, [a["id"] for a in FIXTURE["atoms"]])
    con.commit()
    con.close()
    ask._answer_cache.clear()
    yield
    ask._answer_cache.clear()


@pytest.mark.parametrize("case", FIXTURE["questions"], ids=lambda c: c["id"])
def test_expected_evidence_and_complete_qualification_material(library, monkeypatch, case):
    prompts = []
    monkeypatch.setattr(ask.llm, "chat", lambda messages, **_: (prompts.extend(messages) or "受控回答 [1]", "qa"))
    response = ask.ask(case["question"], history=case.get("history"), k=8)
    ids = {r["atom_id"] for r in response["refs"]}
    assert set(case["expected_atom_ids"]) <= ids, {"question": case["id"], "missing": set(case["expected_atom_ids"]) - ids}
    assert prompts, "a non-empty material set must reach the controlled model"
    material = prompts[-1]["content"]
    for atom in FIXTURE["atoms"]:
        if atom["id"] in case["expected_atom_ids"]:
            for exception in atom["exceptions"]:
                assert exception in material
            assert atom["claim"] in material
    assert response["degraded"] is False
    assert "budget_wait" not in response


def test_follow_up_uses_previous_user_object_not_assistant_assertions(library, monkeypatch):
    prompts = []
    monkeypatch.setattr(ask.llm, "chat", lambda messages, **_: (prompts.extend(messages) or "受控回答 [1]", "qa"))
    response = ask.ask("那地暖呢？", history=[{"role": "user", "content": "我在比较卧室木地板和瓷砖。"},
                      {"role": "assistant", "content": "以前模型可能误述的其他事实"}], k=8)
    assert "qa_floor_heat" in {r["atom_id"] for r in response["refs"]}
    assert response["retrieval_scope"] == "follow_up_user_context"
    assert prompts
    assert response["degraded"] is False
    assert "budget_wait" not in response
