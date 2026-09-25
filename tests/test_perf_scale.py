# -*- coding: utf-8 -*-
"""Scale/performance probes: 10k atoms, 10k evidence events, 10k timeline
events on an isolated temp DB. Timing assertions are deliberately generous
(CI variance); the measured numbers are printed for the robustness report."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import reno.config as config
from reno import db

VIDEO_ID = "BVscale0000000"
N_ATOMS = 10_000
N_EVENTS = 10_000


@pytest.fixture(scope="module")
def scaled(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("reno-scale")
    mp.setattr(config, "ROOT", tmp)
    mp.setattr(config, "DATA", tmp / "data")
    mp.setattr(config, "FRAME_CACHE", tmp / "frames")
    mp.setattr(config, "ORIG", tmp / "media" / "originals")

    con = db.connect()
    con.execute("PRAGMA synchronous=OFF")
    con.execute(
        "INSERT INTO video_asset (video_id, title, duration_ms, content_sha256,"
        " files_json, status) VALUES (?,?,?,?,?,?)",
        (VIDEO_ID, "压测视频", N_EVENTS * 1000, "scale-sha",
         json.dumps({"video": None, "audio": None}), "processed"))
    con.executemany(
        "INSERT INTO transcript_segment (id, video_id, idx, start_ms, end_ms, text)"
        " VALUES (?,?,?,?,?,?)",
        [(f"asr_{i:05d}", VIDEO_ID, i, i * 400, i * 400 + 380,
          f"第{i}段转写：防水施工要点，包含数值{i % 100}毫米") for i in range(N_EVENTS)])
    atom_rows = []
    for i in range(N_ATOMS):
        cat = ["防水", "水电", "瓦工", "墙面", "验收"][i % 5]
        atom_rows.append((
            f"atom_s{i:06d}", VIDEO_ID, cat, f"阶段{i % 8}", "卫生间", "主题",
            f"原子断言{i}：防水高度{1.5 + (i % 10) * 0.1}米，覆盖率{i % 90}%",
            "recommend", 0.5 + (i % 50) / 100, "candidate", None, "{}", "[]"))
    con.executemany(
        "INSERT INTO knowledge_atom (id, video_id, category, stage, space, subject,"
        " claim, polarity, confidence, status, cluster_id, conditions_json,"
        " parameters_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", atom_rows)
    con.executemany(
        "INSERT INTO evidence_ref (atom_id, video_id, modality, source_item_id,"
        " start_ms, end_ms, evidence_text, weight) VALUES (?,?,?,?,?,?,?,?)",
        [(f"atom_s{i:06d}", VIDEO_ID, "asr", f"asr_{i % N_EVENTS:05d}",
          (i % N_EVENTS) * 400, (i % N_EVENTS) * 400 + 380, "证据文本", 1.0)
         for i in range(N_ATOMS)])
    con.commit()
    # FTS sync in bulk (same semantics as fts_sync_atoms, executemany for speed)
    con.executemany(
        "INSERT INTO atom_fts (atom_id, claim, reason, category, space) VALUES (?,?,?,?,?)",
        [(r["id"], db.fts_prep(r["claim"]), "", db.fts_prep(r["category"] or ""), "")
         for r in con.execute("SELECT id, claim, category FROM knowledge_atom")])
    con.commit()
    con.close()

    from app.main import app
    yield {"app": app}
    mp.undo()


def _timed(fn, repeats=3):
    times = []
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t0) * 1000)
    return out, times


class TestScale:
    def test_videos_endpoint(self, scaled):
        client = TestClient(scaled["app"])
        _, times = _timed(lambda: client.get("/api/videos"))
        assert max(times) < 2000, times
        print(f"\n[videos] p-max {max(times):.0f}ms ({[f'{t:.0f}' for t in times]})")

    def test_search_common_term(self, scaled):
        client = TestClient(scaled["app"])
        r, times = _timed(lambda: client.get("/api/search", params={"q": "防水"}))
        assert r.status_code == 200
        assert len(r.json()["results"]) == 80  # cap honored at scale
        assert max(times) < 2000, times
        print(f"\n[search] p-max {max(times):.0f}ms ({[f'{t:.0f}' for t in times]})")

    def test_events_10k(self, scaled):
        client = TestClient(scaled["app"])
        r, times = _timed(lambda: client.get(f"/api/video/{VIDEO_ID}/events"))
        assert r.status_code == 200
        body = r.json()
        assert len(body["events"]) == N_EVENTS
        assert len(body["atoms"]) == N_ATOMS
        assert max(times) < 4000, times
        print(f"\n[events 10k] p-max {max(times):.0f}ms ({[f'{t:.0f}' for t in times]})")

    def test_ask_retrieval_at_scale(self, scaled):
        """Retrieval (the part before the LLM) must stay fast at 10k atoms."""
        from reno import ask as ask_mod
        con = db.connect()
        try:
            _, times = _timed(lambda: ask_mod.retrieve(con, "防水高度 1.8米", k=24))
        finally:
            con.close()
        assert max(times) < 800, times
        print(f"\n[retrieve 10k] p-max {max(times):.0f}ms ({[f'{t:.0f}' for t in times]})")

    def test_checklist_report_at_scale(self, scaled):
        from reno import report
        _, times = _timed(lambda: report.checklist_report())
        assert max(times) < 5000, times
        print(f"\n[checklist 10k] p-max {max(times):.0f}ms ({[f'{t:.0f}' for t in times]})")
