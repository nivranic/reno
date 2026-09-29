# -*- coding: utf-8 -*-
"""Phase 0 (cloud/home split prerequisites, docs/sync-plan.md section 3):
node_role switch, llm route guard, sync queue enqueue, sync-token gate,
/ask degradation + rate limit + error masking, files_json normalization,
report output location."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import reno.config as config
from reno import db

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # app/ importable


@pytest.fixture()
def env(tmp_path, monkeypatch):
    mp = monkeypatch
    tmp = tmp_path / "reno-p0"
    mp.setattr(config, "ROOT", tmp)
    mp.setattr(config, "DATA", tmp / "data")
    mp.setattr(config, "MEDIA", tmp / "media")
    mp.setattr(config, "FRAMES", tmp / "media" / "frames")
    mp.setattr(config, "AUDIO", tmp / "media" / "audio")
    mp.setattr(config, "ORIG", tmp / "media" / "originals")
    mp.setattr(config, "FRAME_CACHE", tmp / "frames_cache")
    tmp.mkdir(parents=True, exist_ok=True)
    # isolated config: reload from the patched ROOT (no local file = defaults)
    mp.setattr(config, "_cache", None)
    yield tmp
    # set_local persisted into tmp - nothing to restore (tmp is discarded)


@pytest.fixture()
def client(env):
    from app.main import app
    return TestClient(app)


def _set_role(role):
    config.set_local({"node_role": role})


# ---------------------------------------------------------------- node_role

def test_cli_pipeline_cmds_refused_on_cloud(env, monkeypatch, capsys):
    from reno import cli
    _set_role("cloud")
    for cmd in (["run"], ["judge"], ["report"], ["import", "http://x/y"]):
        rc = cli.main(cmd)
        assert rc == 2
        assert "cloud" in capsys.readouterr().out


def test_cli_all_role_still_dispatches(env):
    from reno import cli
    # status is not gated and does not need a real pipeline
    rc = cli.main(["status"])
    assert rc == 0


def test_import_enqueues_on_cloud(client, env):
    _set_role("cloud")
    res = client.post("/api/import", json={"url": "https://www.bilibili.com/video/BVp0x"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "queued"
    con = db.connect()
    try:
        rows = con.execute("SELECT url, status FROM sync_queue").fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "queued"
        assert rows[0]["url"].endswith("BVp0x")
        # nothing was ingested
        assert con.execute("SELECT COUNT(*) FROM video_asset").fetchone()[0] == 0
    finally:
        con.close()


def test_import_batch_enqueues_on_cloud(client, env):
    _set_role("cloud")
    res = client.post("/api/import-batch", json={
        "urls": ["https://www.bilibili.com/video/BVok1",
                 "https://www.douyin.com/video/7xxx",
                 "not-a-url"]})
    assert res.status_code == 200
    body = res.json()
    assert body["queued"] == 2 and body["skipped"] == 1


def test_model_switch_forbidden_on_cloud(client, env):
    _set_role("cloud")
    res = client.post("/api/config/model", json={"model": "GLM-5.3-Flash"})
    assert res.status_code == 403


# ---------------------------------------------------------------- llm guard

def test_llm_guard_blocks_coding_plan_models_on_cloud(env, monkeypatch):
    from reno import llm
    _set_role("cloud")
    with pytest.raises(RuntimeError, match="cloud"):
        llm.chat([{"role": "user", "content": "hi"}],
                 model="GLM-5.3-FlashX")  # must fail before any network I/O


def test_llm_guard_inactive_on_all_role(env, monkeypatch):
    from reno import llm
    _set_role("all")
    called = {}

    def fake_post(path, body, timeout=120):
        called["path"] = path
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    out = llm.chat([{"role": "user", "content": "hi"}],
                   model="some-paas-model")
    assert out[0] == "ok"


# ---------------------------------------------------------------- sync gate

def test_sync_surface_closed_without_token(client, env):
    _set_role("cloud")
    assert client.get("/api/sync/anything").status_code == 401
    assert client.get("/api/sync/anything",
                      headers={"X-Sync-Token": "nope"}).status_code == 401


def test_sync_surface_opens_with_header_token(client, env):
    _set_role("cloud")
    config.set_local({"sync_token": "unit-test-sync-token"})
    # gate passes; unknown route beneath it 404s (sync endpoints are Phase 1)
    res = client.get("/api/sync/anything",
                     headers={"X-Sync-Token": "unit-test-sync-token"})
    assert res.status_code == 404
    # query-string form must NOT authenticate the sync surface
    assert client.get("/api/sync/anything?token=unit-test-sync-token").status_code == 401


# ---------------------------------------------------------------- /ask

def test_ask_degrades_to_retrieval_without_llm(client, env, monkeypatch):
    from reno import ask as ask_mod
    con = db.connect()
    con.execute(
        "INSERT INTO video_asset (video_id, title, duration_ms, content_sha256,"
        " files_json, status) VALUES (?,?,?,?,?,?)",
        ("BVp0ask", "问答降级测试", 5000, "sha-p0",
         json.dumps({"video": None, "audio": None}), "processed"))
    con.execute(
        "INSERT INTO knowledge_atom (id, video_id, category, stage, claim,"
        " polarity, status, conditions_json, parameters_json)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("atom_p0_1", "BVp0ask", "防水", "施工",
         "淋浴区墙面防水应刷到1.8米", "recommend", "candidate", "{}", "[]"))
    con.execute(
        "INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,"
        " start_ms, end_ms, evidence_text) VALUES (?,?,?,?,?,?,?)",
        ("atom_p0_1", "BVp0ask", "asr", "asr_0000", 1000, 2000, "防水段落"))
    db.fts_sync_atoms(con, ["atom_p0_1"])  # index via fts_prep, not raw SQL
    con.commit()
    con.close()
    # this test does not exercise the rate limiter - keep its bucket empty
    config.set_local({"ask_rate_per_min": 0})

    def boom(*a, **kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(ask_mod.llm, "chat", boom)
    ask_mod._answer_cache.clear()
    res = client.post("/api/ask", json={"question": "防水多高"})
    assert res.status_code == 200
    body = res.json()
    assert body.get("degraded") is True
    assert "检索" in body["answer"]
    # degraded answers must not enter the LRU cache
    assert len(ask_mod._answer_cache) == 0


def test_ask_rate_limit(client, env, monkeypatch):
    import app.api as api_mod
    with api_mod._ASK_LOCK:
        api_mod._ASK_BUCKETS.clear()  # module-level bucket shared across tests
    config.set_local({"ask_rate_per_min": 2})
    from reno import ask as ask_mod
    monkeypatch.setattr(
        ask_mod, "ask",
        lambda q, history=None, k=24: {"answer": "a", "refs": [], "conflicts": []})
    r1 = client.post("/api/ask", json={"question": "q1"})
    r2 = client.post("/api/ask", json={"question": "q2"})
    r3 = client.post("/api/ask", json={"question": "q3"})
    assert r1.status_code == r2.status_code == 200
    assert r3.status_code == 429


def test_ask_error_masked(client, env, monkeypatch):
    from reno import ask as ask_mod

    def boom(*a, **kw):
        raise ValueError("secret-key-detail-xyz")

    monkeypatch.setattr(ask_mod, "ask", boom)
    res = client.post("/api/ask", json={"question": "q"})
    assert res.status_code == 500
    assert "secret-key-detail-xyz" not in res.text


# ------------------------------------------------------- files_json paths

def test_resolve_media_variants(env):
    base = env  # MEDIA.parent == env root
    assert config.resolve_media("media/originals/x.mp4") == \
        env / "media" / "originals" / "x.mp4"
    # legacy backslash relative form resolves to the same file
    assert config.resolve_media("media\\originals\\x.mp4") == \
        env / "media" / "originals" / "x.mp4"
    # absolute values pass through
    assert config.resolve_media(str(env / "media" / "y.mp4")) == \
        env / "media" / "y.mp4"
    assert config.resolve_media(None) is None


def test_store_path_posix_relative(env):
    from reno import ingest
    p = env / "media" / "originals" / "BV.mp4"
    assert ingest._store_path(p) == "media/originals/BV.mp4"


def test_files_json_normalization_roundtrip(env):
    from reno import files_json
    v = env / "media" / "originals" / "BV.mp4"
    v.parent.mkdir(parents=True, exist_ok=True)
    v.write_bytes(b"x")
    con = db.connect()
    con.execute(
        "INSERT INTO video_asset (video_id, files_json, status)"
        " VALUES (?,?,?)",
        ("BVnorm1", json.dumps({"video": "media\\originals\\BV.mp4",
                                "audio": None}), "processed"))
    con.commit()
    con.close()

    stats = files_json.run()
    assert stats["rewritten"] == 1
    con = db.connect()
    fj = json.loads(con.execute(
        "SELECT files_json FROM video_asset WHERE video_id='BVnorm1'"
    ).fetchone()[0])
    con.close()
    assert fj["video"] == "media/originals/BV.mp4"
    # idempotent
    assert files_json.run()["rewritten"] == 0


def test_files_json_missing_file_left_alone(env):
    from reno import files_json
    con = db.connect()
    con.execute(
        "INSERT INTO video_asset (video_id, files_json, status)"
        " VALUES (?,?,?)",
        ("BVnorm2", json.dumps({"video": "media/originals/gone.mp4",
                                "audio": None}), "processed"))
    con.commit()
    con.close()
    stats = files_json.run()
    assert stats["rewritten"] == 0
    assert any("BVnorm2" in m for m in stats["missing"])


# ---------------------------------------------------------------- reports

def test_report_out_dir_is_data_relative(env):
    from reno import report
    assert report.out_dir() == config.DATA / "generated"
    assert "docs" not in report.out_dir().parts


def test_reports_endpoint_reads_data_dir(client, env):
    gen = config.DATA / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "summary.md").write_text("# 单元测试摘要", encoding="utf-8")
    res = client.get("/api/reports")
    assert res.status_code == 200
    assert res.json()["reports"]["summary"] == "# 单元测试摘要"
