# -*- coding: utf-8 -*-
"""Multidimensional knowledge model v2: stage derivation, evidence nature as
single source of truth, price entries, replace_atoms field carry-over,
retrieval fallback via /api/search, compare grouping + gating, checklist v2
acceptance structure, prices report honesty, backfill deterministic pass."""
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import reno.config as config
from reno import db
from reno.atomize import _validate_atom, composite_confidence
from reno.dict import expand_synonyms, stage_for, taxonomy

VIDEO_ID = "BVmultidim00001"
VIDEO_B = "BVmultidim00002"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("reno-multidim")
    real = config.ROOT
    mp.setattr(config, "ROOT", tmp)
    if (real / "config.local.json").exists():
        (tmp / "config.local.json").write_text(
            (real / "config.local.json").read_text(encoding="utf-8"),
            encoding="utf-8")
    mp.setattr(config, "DATA", tmp / "data")
    mp.setattr(config, "FRAME_CACHE", tmp / "frames")
    mp.setattr(config, "ORIG", tmp / "media" / "originals")

    con = db.connect()
    for vid, sha in ((VIDEO_ID, "md-sha-a"), (VIDEO_B, "md-sha-b")):
        con.execute(
            "INSERT INTO video_asset (video_id, title, duration_ms, content_sha256,"
            " files_json, status) VALUES (?,?,?,?,?,?)",
            (vid, "多维测试", 1200, sha, json.dumps({"video": None}), "processed"))
        con.execute(
            "INSERT INTO transcript_segment (id, video_id, idx, start_ms, end_ms, text)"
            " VALUES (?,?,?,?,?,?)",
            (f"asr_{vid[-4:]}_0", vid, 0, 0, 500, "卫生间防水高度要做到两米"))
    # atom with legacy authority_level + free-text stage (v1 shape), same id on both sides
    con.execute(
        """INSERT INTO knowledge_atom (id, video_id, category, stage, claim, polarity,
              conditions_json, status) VALUES (?,?,?,?,?,?,?,?)""",
        ("atom_md_000", VIDEO_ID, "防水", "随便写的阶段", "卫生间墙面防水应刷到1.8米",
         "require", json.dumps({"authority_level": "cited_standard", "材料": "JS防水涂料"}),
         "candidate"))
    con.execute(
        """INSERT INTO knowledge_atom (id, video_id, category, stage, claim, polarity,
              conditions_json, status) VALUES (?,?,?,?,?,?,?,?)""",
        ("atom_md_001", VIDEO_B, "防水", "基层处理", "淋浴区墙面防水应刷到1.8米",
         "require", "{}", "candidate"))
    con.execute(
        """INSERT INTO knowledge_atom (id, video_id, category, stage, claim, polarity,
              conditions_json, status) VALUES (?,?,?,?,?,?,?,?)""",
        ("atom_md_002", VIDEO_B, "泥瓦", "泥瓦", "瓷砖铺贴要留缝并做美缝防止渗污",
         "recommend", "{}", "candidate"))
    con.executemany(
        "INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,"
        " start_ms, end_ms, evidence_text, weight) VALUES (?,?,?,?,?,?,?,?)",
        [("atom_md_000", VIDEO_ID, "asr", f"asr_{VIDEO_ID[-4:]}_0", 0, 500, "两米", 1.0),
         ("atom_md_001", VIDEO_B, "asr", f"asr_{VIDEO_B[-4:]}_0", 0, 500, "两米", 1.0),
         ("atom_md_002", VIDEO_B, "asr", f"asr_{VIDEO_B[-4:]}_0", 0, 500, "美缝", 1.0)])
    from reno.db import fts_sync_atoms
    fts_sync_atoms(con, ["atom_md_000", "atom_md_001", "atom_md_002"])
    con.commit()
    yield {"con": con}
    con.close()


# ---------- stage derivation ----------

def test_stage_derived_from_category(env):
    assert stage_for("防水") == "泥瓦"
    assert stage_for("设计规划") == "前期准备"
    assert stage_for("入住维护") == "入住维护"
    assert stage_for("不存在的分类") is None


def test_validate_atom_stage_and_dimension(env):
    index = {f"asr_{VIDEO_ID[-4:]}_0": {"modality": "asr", "start_ms": 0,
                                        "end_ms": 500, "text": "x"}}
    raw = {"claim": "卫生间墙面防水施工前基层必须找平", "category": "防水",
           "stage": "LLM 不该说了算", "space": "卫生间", "polarity": "require",
           "dimension": "施工工艺", "evidence_nature": "author_opinion",
           "conditions": {}, "exceptions": ["仅适用于新建墙体"],
           "evidence": [{"source_item_id": f"asr_{VIDEO_ID[-4:]}_0", "weight": 0.8}]}
    a = _validate_atom(raw, VIDEO_ID, 0, index, 1200)
    assert a["stage"] == "泥瓦"          # derived, LLM value discarded
    assert a["dimension"] == "施工工艺"
    assert json.loads(a["exceptions"]) == ["仅适用于新建墙体"]
    raw_bad = dict(raw, dimension="瞎写的维度", evidence_nature="瞎写")
    b = _validate_atom(raw_bad, VIDEO_ID, 1, index, 1200)
    assert b["dimension"] is None        # unclassifiable stays empty
    assert b["evidence_nature"] is None


def test_authority_level_migrated_to_nature(env):
    index = {f"asr_{VIDEO_ID[-4:]}_0": {"modality": "asr", "start_ms": 0,
                                        "end_ms": 500, "text": "x"}}
    raw = {"claim": "规范要求防水涂层厚度不低于1.5毫米", "category": "防水",
           "polarity": "require",
           "conditions": {"authority_level": "cited_standard", "材料": "JS"},
           "evidence": [{"source_item_id": f"asr_{VIDEO_ID[-4:]}_0", "weight": 0.8}]}
    a = _validate_atom(raw, VIDEO_ID, 2, index, 1200)
    assert a["evidence_nature"] == "cited_standard"
    assert "authority_level" not in a["conditions"]
    assert a["conditions"] == {"材料": "JS"}
    with_nature = composite_confidence(a, a["evidence_refs"])
    without = dict(a, evidence_nature=None)
    assert with_nature > composite_confidence(without, a["evidence_refs"])


# ---------- price entries ----------

def test_price_validation_keeps_numeric_drops_garbage(env):
    index = {f"asr_{VIDEO_ID[-4:]}_0": {"modality": "asr", "start_ms": 0,
                                        "end_ms": 500, "text": "x"}}
    raw = {"claim": "本地瓷砖铺贴人工费报价在每平米60元左右", "category": "泥瓦",
           "polarity": "neutral", "dimension": "报价采购",
           "conditions": {},
           "price": [{"object": "瓷砖铺贴人工", "amount": 60, "unit": "元/平米",
                      "basis": "每平米", "price_kind": "工程报价",
                      "includes": ["含水泥砂浆"], "region": ""},
                     {"object": "缺金额", "amount": "面议"},
                     {"object": "口径乱写", "amount": 1, "price_kind": "底价"},
                     "not-a-dict"],
           "evidence": [{"source_item_id": f"asr_{VIDEO_ID[-4:]}_0", "weight": 0.7}]}
    a = _validate_atom(raw, VIDEO_ID, 3, index, 1200)
    prices = json.loads(a["prices_json"])
    assert len(prices) == 2
    assert prices[0]["price_kind"] == "工程报价"
    assert prices[1]["price_kind"] is None  # unknown kind -> 未注明, not coerced


# ---------- replace_atoms carry-over ----------

def test_replace_atoms_carries_v2_fields(env):
    con = env["con"]
    con.execute("UPDATE knowledge_atom SET dimension='施工工艺', exceptions=? "
                "WHERE id='atom_md_000'", (json.dumps(["例外A"]),))
    atom = dict(db.all_atoms(con, video_id=VIDEO_ID)[0])
    atom["claim"] = atom["claim"]  # rerun re-emits WITHOUT v2 fields
    for k in ("dimension", "evidence_nature", "exceptions", "prices"):
        atom.pop(k, None)
    db.replace_atoms(con, VIDEO_ID, [atom])
    row = con.execute("SELECT dimension, exceptions FROM knowledge_atom "
                      "WHERE id='atom_md_000'").fetchone()
    assert row["dimension"] == "施工工艺"
    assert json.loads(row["exceptions"]) == ["例外A"]
    # explicit new value wins over carry-over
    atom2 = dict(db.all_atoms(con, video_id=VIDEO_ID)[0])
    atom2["dimension"] = "验收质检"
    db.replace_atoms(con, VIDEO_ID, [atom2])
    assert con.execute("SELECT dimension FROM knowledge_atom WHERE id='atom_md_000'"
                       ).fetchone()["dimension"] == "验收质检"


# ---------- retrieval / API ----------

def test_search_multiterm_fallback(env):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = client.get("/api/search", params={"q": "卫生间防水高度"})
    assert r.status_code == 200
    hits = r.json()["results"]
    assert hits, "multi-term query must not zero-hit"
    assert any("防水" in h["claim"] for h in hits)
    row = hits[0]
    for key in ("dimension", "evidence_nature", "stage", "subject",
                "conditions", "parameters", "prices"):
        assert key in row


def test_search_dimension_filter(env):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = client.get("/api/search", params={"q": "防水", "dimension": "施工工艺"})
    assert r.status_code == 200
    assert all(h["dimension"] == "施工工艺" for h in r.json()["results"])


def test_expand_synonyms(env):
    out = expand_synonyms("磁砖缝隙要留多大")
    assert any("瓷砖" in v for v in out)


def test_facets_has_new_axes(env):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    d = client.get("/api/facets").json()
    assert set(d) >= {"categories", "spaces", "stages", "dimensions"}


def test_compare_groups_and_gates(env):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = client.get("/api/compare", params={"items": "防水高度,瓷砖美缝"})
    assert r.status_code == 200
    d = r.json()
    assert len(d["sides"]) == 2
    for s in d["sides"]:
        assert s["total"] >= 1
        for g in s["groups"]:
            assert g["dimension"] in taxonomy()["dimensions"] + ["其他"]
    # unrelated item is gated out entirely
    r2 = client.get("/api/compare", params={"items": "防水,量子物理"})
    s2 = {s["item"]: s for s in r2.json()["sides"]}
    assert s2["量子物理"]["total"] == 0
    assert client.get("/api/compare", params={"items": "只有一个"}).status_code == 400


# ---------- reports ----------

def test_prices_report_honest_empty(env):
    from reno import report
    p = report.prices_report()
    text = p.read_text(encoding="utf-8")
    assert "暂无可核验价格数据" in text


def test_prices_report_shows_entries_and_gaps(env):
    from reno import report
    con = env["con"]
    con.execute("UPDATE knowledge_atom SET prices_json=? WHERE id='atom_md_000'",
                (json.dumps([{"object": "防水人工", "amount": 60, "unit": "元/平米",
                              "price_kind": None, "spec": "", "region": ""}]),))
    con.commit()
    p = report.prices_report()
    text = p.read_text(encoding="utf-8")
    assert "防水人工" in text and "未注明口径" in text
    con.execute("UPDATE knowledge_atom SET prices_json=NULL WHERE id='atom_md_000'")
    con.commit()


def test_checklist_acceptance_pending_criteria(env):
    from reno import report
    con = env["con"]
    con.execute("""UPDATE knowledge_atom SET dimension='验收质检', confidence=0.8,
                   stage='验收', parameters_json='[]' WHERE id='atom_md_000'""")
    con.commit()
    p = report.checklist_report()
    text = p.read_text(encoding="utf-8")
    assert "待核实" in text
    con.execute("UPDATE knowledge_atom SET dimension=NULL, stage='泥瓦' "
                "WHERE id='atom_md_000'")
    con.commit()


# ---------- backfill deterministic pass ----------

def test_backfill_deterministic_pass(env):
    bf = importlib.import_module("backfill_atoms_v2")
    con = env["con"]
    con.execute("UPDATE knowledge_atom SET stage='随便写的阶段', dimension=NULL,"
                " evidence_nature=NULL, conditions_json=? WHERE id='atom_md_001'",
                (json.dumps({"authority_level": "cited_standard"}),))
    stats = bf.deterministic_pass(con)
    assert stats["stage_fixed"] >= 1
    row = con.execute("SELECT stage, evidence_nature, conditions_json "
                      "FROM knowledge_atom WHERE id='atom_md_001'").fetchone()
    assert row["stage"] == "泥瓦"
    assert row["evidence_nature"] == "cited_standard"
    assert "authority_level" not in json.loads(row["conditions_json"])


def test_backfill_llm_labels_validation(env, monkeypatch):
    bf = importlib.import_module("backfill_atoms_v2")
    rows = [{"id": "x1", "category": "防水", "subject": "s", "claim": "防水高度",
             "reason": "", "risk_if_ignored": "", "polarity": "require",
             "conditions_json": "{}", "evidence_nature": None}]
    fake = {"labels": [
        {"id": "x1", "dimension": "施工工艺", "evidence_nature": "author_opinion",
         "exceptions": ["仅砖墙"]},
        {"id": "x2", "dimension": "瞎写", "evidence_nature": "瞎写",
         "exceptions": []},  # unknown id dropped; invalid values -> None
    ]}
    monkeypatch.setattr(bf.llm, "chat_json", lambda *a, **k: (fake, "test"))
    labels = bf.llm_batch(rows)
    assert labels["x1"]["dimension"] == "施工工艺"
    assert labels["x1"]["exceptions"] == ["仅砖墙"]
    assert "x2" not in labels
