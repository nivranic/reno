# -*- coding: utf-8 -*-
"""Tests for the enhancement round: shared-token auth gate, judge
content-derived stable IDs with decision carry-over, ask result cache,
events time-window filter, judge pre-rebuild snapshot."""
import json

import pytest
from fastapi.testclient import TestClient

import reno.config as config
from reno import db

VIDEO_ID = "BVenhance00000"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("reno-enh")
    # keep the real config.local.json (API key) visible under the patched ROOT
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
    con.execute(
        "INSERT INTO video_asset (video_id, title, duration_ms, content_sha256,"
        " files_json, status) VALUES (?,?,?,?,?,?)",
        (VIDEO_ID, "增强功能测试", 1200, "enh-sha",
         json.dumps({"video": None, "audio": None}), "processed"))
    for i, (s0, e0) in enumerate(((0, 90), (100, 250), (300, 500))):
        con.execute(
            "INSERT INTO transcript_segment (id, video_id, idx, start_ms, end_ms, text)"
            " VALUES (?,?,?,?,?,?)",
            (f"asr_{i:04d}", VIDEO_ID, i, s0, e0, f"防水高度段落{i}"))
    con.execute(
        "INSERT INTO video_asset (video_id, title, duration_ms, content_sha256,"
        " files_json, status) VALUES (?,?,?,?,?,?)",
        (VIDEO_ID + "B", "增强功能测试·第二来源", 1200, "enh-sha-b",
         json.dumps({"video": None, "audio": None}), "processed"))
    con.execute(
        "INSERT INTO knowledge_atom (id, video_id, category, stage, claim,"
        " polarity, status, cluster_id, conditions_json, parameters_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("atom_enh_a", VIDEO_ID, "防水", "施工", "淋浴区墙面防水应刷到1.8米",
         "recommend", "candidate", "clu_enh_001", "{}", "[]"))
    con.execute(
        "INSERT INTO knowledge_atom (id, video_id, category, stage, claim,"
        " polarity, status, cluster_id, conditions_json, parameters_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("atom_enh_b", VIDEO_ID + "B", "防水", "施工",
         "淋浴区墙面防水刷到1米就足够了",
         "recommend", "candidate", "clu_enh_001", "{}", "[]"))
    con.execute(
        "INSERT INTO knowledge_cluster (cluster_id, canonical_topic, relation,"
        " members_json, linked_conflict, created_at) VALUES (?,?,?,?,?,?)",
        ("clu_enh_001", "防水·高度", "conflicting",
         json.dumps(["atom_enh_a", "atom_enh_b"]), "conflict_enh_001", db.now()))
    con.execute(
        "INSERT INTO conflict_case (conflict_id, ctype, status, side_a_json,"
        " side_b_json, analysis_json, recommended_action, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("conflict_enh_001", "numeric_conflict", "needs_review",
         json.dumps({"claim": "1.8米", "video": VIDEO_ID, "conditions": {}}),
         json.dumps({"claim": "2米", "video": VIDEO_ID, "conditions": {}}),
         json.dumps({"judge_note": "测试"}), "结合场景", db.now()))
    con.executemany(
        "INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,"
        " start_ms, end_ms, evidence_text, weight) VALUES (?,?,?,?,?,?,?,?)",
        [("atom_enh_a", VIDEO_ID, "asr", "asr_0000", 0, 500, "转写1.8米", 1.0),
         ("atom_enh_b", VIDEO_ID + "B", "asr", "asr_b000", 0, 500, "转写1米", 1.0)])
    from reno.db import fts_sync_atoms
    fts_sync_atoms(con, ["atom_enh_a", "atom_enh_b"])
    con.commit()
    con.close()
    from app.main import app
    yield {"app": app, "tmp": tmp}
    mp.undo()


@pytest.fixture()
def client(env):
    return TestClient(env["app"])


def stub_judge_conflicting(monkeypatch):
    """Deterministic verdicts: every candidate pair is a numeric conflict.
    The real-model relation is non-deterministic (same/related/conflicting
    all plausible for near-identical claims), so ID-stability and
    carry-over tests pin the verdict instead."""
    from reno import judge, dedup

    def fake_llm(cands, batch_size=15):
        out = []
        for c in cands:
            out.append({"a": c["a"], "b": c["b"], "relation": "conflicting",
                        "conflict_type": "numeric_conflict",
                        "reason": "测试判定:数值不一致",
                        "condition_overlap": True, "scope_split": "",
                        "authority_gap": False})
        return out, "stub"

    monkeypatch.setattr(judge, "_judge_llm", fake_llm)


class TestAuthGate:
    def test_open_when_no_token(self, client):
        assert client.get("/api/videos").status_code == 200

    def test_token_required_when_configured(self, env, client):
        import reno.config as cfg
        cfg.set_local({"auth_token": "sekrit-123"})
        try:
            # without token
            assert client.get("/api/videos").status_code == 401
            # with header
            assert client.get("/api/videos",
                              headers={"X-Reno-Token": "sekrit-123"}).status_code == 200
            # with query param (media tags cannot send headers)
            assert client.get("/api/videos?token=sekrit-123").status_code == 200
            # wrong token
            assert client.get("/api/videos?token=nope").status_code == 401
            # non-API routes stay open (SPA shell carries no data)
            assert client.get("/").status_code == 200
        finally:
            cfg.set_local({"auth_token": ""})

    def test_open_again_after_clearing(self, client):
        assert client.get("/api/videos").status_code == 200


class TestEventsWindow:
    def test_window_filters_events_keeps_atoms(self, client):
        full = client.get(f"/api/video/{VIDEO_ID}/events").json()
        assert full["events"]
        win = client.get(f"/api/video/{VIDEO_ID}/events",
                         params={"from_ms": 100, "to_ms": 400}).json()
        assert win["events"]
        assert all(100 <= e["ms"] <= 400 for e in win["events"])
        assert len(win["atoms"]) == len(full["atoms"])  # atoms stay complete
        assert len(win["events"]) < len(full["events"])

    def test_invalid_window_clamped(self, client):
        r = client.get(f"/api/video/{VIDEO_ID}/events",
                       params={"from_ms": -500, "to_ms": -1})
        assert r.status_code == 200


class TestJudgeStableIds:
    def test_ids_are_content_derived_and_stable(self, env, monkeypatch):
        from reno import judge
        stub_judge_conflicting(monkeypatch)
        con = db.connect()
        try:
            s1 = judge.run(con)
            ids1 = [r["conflict_id"] for r in con.execute(
                "SELECT conflict_id FROM conflict_case")]
            cids1 = [r["cluster_id"] for r in con.execute(
                "SELECT cluster_id FROM knowledge_cluster")]
            s2 = judge.run(con)
            ids2 = [r["conflict_id"] for r in con.execute(
                "SELECT conflict_id FROM conflict_case")]
            cids2 = [r["cluster_id"] for r in con.execute(
                "SELECT cluster_id FROM knowledge_cluster")]
        finally:
            con.close()
        assert s1["conflicts"] == s2["conflicts"]
        assert ids1 == ids2 and ids1 and all(i.startswith("cfl_") for i in ids1)
        assert cids1 == cids2 and all(i.startswith("clu_") for i in cids1)

    def test_decision_survives_judge_rerun(self, env, monkeypatch):
        client = TestClient(env["app"])
        from reno import judge
        stub_judge_conflicting(monkeypatch)
        con = db.connect()
        try:
            # first run assigns the stable content-derived id
            judge.run(con)
            cid = con.execute("SELECT conflict_id FROM conflict_case"
                              " ORDER BY conflict_id LIMIT 1").fetchone()["conflict_id"]
        finally:
            con.close()
        assert cid.startswith("cfl_")
        r = client.post("/api/decision", json={
            "conflict_id": cid, "action": "accept_a", "note": "跨重跑存活"})
        assert r.status_code == 200
        # rerun: same conflicting pair regenerates under the SAME id and the
        # recorded decision is restored into status
        con = db.connect()
        try:
            judge.run(con)
            row = con.execute("SELECT status FROM conflict_case WHERE conflict_id=?",
                              (cid,)).fetchone()
            assert row is not None and row["status"] == "decided:accept_a"
        finally:
            con.close()

    def test_judge_leaves_a_snapshot(self, env, monkeypatch):
        from reno import judge
        stub_judge_conflicting(monkeypatch)
        con = db.connect()
        try:
            judge.run(con)
        finally:
            con.close()
        baks = list(config.DATA.glob("reno.db.bak-judge-*"))
        assert baks, "judge should leave a rollback snapshot"
        assert len(baks) <= 3


class TestAskCache:
    def test_identical_question_cached_until_kb_changes(self, env, monkeypatch):
        from reno import ask as ask_mod
        calls = {"n": 0}

        def fake_chat(messages, **kw):
            calls["n"] += 1
            return "回答 [1]。", "mock"

        monkeypatch.setattr(ask_mod.llm, "chat", fake_chat)
        ask_mod._answer_cache.clear()
        r1 = ask_mod.ask("防水高度是多少")
        r2 = ask_mod.ask("防水高度是多少")
        assert calls["n"] == 1 and r1["answer"] == r2["answer"]
        # a new atom bumps the KB version stamp -> cache misses
        con = db.connect()
        con.execute(
            "INSERT INTO knowledge_atom (id, video_id, category, stage, claim,"
            " polarity, status, conditions_json, parameters_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("atom_new_x", VIDEO_ID, "防水", "施工", "新断言", "recommend",
             "candidate", "{}", "[]"))
        con.execute("INSERT INTO atom_fts (atom_id, claim, reason, category, space)"
                    " VALUES ('atom_new_x', '新断言', '', '防水', '')")
        con.commit()
        con.close()
        r3 = ask_mod.ask("防水高度是多少")
        assert calls["n"] == 2
        assert "PWNED" not in r3["answer"]

    def test_history_turns_bypass_cache(self, env, monkeypatch):
        from reno import ask as ask_mod
        calls = {"n": 0}

        def fake_chat(messages, **kw):
            calls["n"] += 1
            return "追问回答 [1]。", "mock"

        monkeypatch.setattr(ask_mod.llm, "chat", fake_chat)
        ask_mod._answer_cache.clear()
        ask_mod.ask("防水", history=[{"role": "user", "content": "上一问"}])
        ask_mod.ask("防水", history=[{"role": "user", "content": "上一问"}])
        assert calls["n"] == 2  # with-history turns are never served from cache
