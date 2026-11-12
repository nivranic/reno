"""Real loopback cloud process + home worker, with synthetic platform/model I/O.

Both libraries, identities, credentials and media live under tmp_path. The only
server terminated by these tests is the exact Popen child created by the fixture.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from reno import config, db, ingest, pipeline, report, review, sync_access, sync_sources, tasks, worker, storage_admin
from reno.sync_state import StateStore


REPOSITORY = Path(__file__).resolve().parents[1]
SYNC_TOKEN = "qa-http-sync-artificial-token"
BROWSER_TOKEN = "qa-http-browser-artificial-token"
VIDEO = "BV1HttpSyncA"
ATOM = "atom_http_fixture"
SHORT_URL = "https://b23.tv/httpFixture"
FINAL_URL = f"https://www.bilibili.com/video/{VIDEO}"
CONTENT = b"synthetic HTTP media, not a playable production video\n" * 8192


def _child_env(local, root, data):
    env = {key: value for key, value in os.environ.items() if not key.startswith("RENO_")}
    env.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(local), RENO_DATA_DIR=str(data),
               RENO_LLM_TESTS="0", PYTHONIOENCODING="utf-8")
    return env


def _cli(env, *arguments):
    result = subprocess.run([sys.executable, "-m", "reno", *arguments], cwd=REPOSITORY,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, f"isolated cloud CLI failed: {arguments[0]} (exit {result.returncode})"
    return json.loads(result.stdout)


def _rows(path, sql, values=()):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, values)]


@pytest.fixture()
def two_nodes(tmp_path, monkeypatch):
    home, cloud = tmp_path / "home", tmp_path / "cloud"
    home.mkdir()
    cloud.mkdir()
    home_config, cloud_config = home / "config.test.json", cloud / "config.test.json"
    home_config.write_text(json.dumps({"node_role": "home", "sync_token": SYNC_TOKEN,
        "sync_enabled": True, "sync_allow_insecure_test_transport": True, "vlm_enabled": False,
        "zhipu_api_key": "", "auth_token": ""}), encoding="utf-8")
    cloud_config.write_text(json.dumps({"node_role": "cloud", "sync_token": SYNC_TOKEN,
        "auth_token": BROWSER_TOKEN, "sync_allow_insecure_test_transport": True,
        "zhipu_api_key": "", "web_ui": "classic"}), encoding="utf-8")
    monkeypatch.setenv("RENO_QA_ROOT", str(tmp_path))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(home_config))
    monkeypatch.setenv("RENO_DATA_DIR", str(home / "data"))
    for name, value in {"DATA": home / "data", "MEDIA": home / "media", "ORIG": home / "media/originals",
                        "AUDIO": home / "media/audio", "FRAMES": home / "media/frames",
                        "FRAME_CACHE": home / "frames_cache"}.items():
        monkeypatch.setattr(config, name, value)
    # Ingest's optional legacy cookie lookup also stays in this synthetic tree.
    monkeypatch.setattr(config, "ROOT", home / "synthetic-source-root")
    monkeypatch.setattr(config, "_cache", None)
    assert config.is_isolated_qa()
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    con = db.connect()
    source = sync_access.identity(con)
    con.close()

    child_env = _child_env(cloud_config, tmp_path, cloud / "data")
    _cli(child_env, "init-db")
    _cli(child_env, "storage-init")
    target = _cli(child_env, "sync-identity")
    assert target["library_id"] != source["library_id"] and target["node_id"] != source["node_id"]
    source_file = cloud / "source-identity.json"
    source_file.write_text(json.dumps(source), encoding="utf-8")
    _cli(child_env, "sync-pair", "--source", str(source_file))
    preview = _cli(child_env, "sync-source-continuity")
    assert preview["state"] == "absent"
    activated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    _cli(child_env, "sync-source-continuity", "--apply",
         "--continuity-id", str(uuid.uuid4()), "--activated-at", activated_at,
         "--expected-digest", preview["activation_basis_sha256"])
    assert _cli(child_env, "sync-source-continuity")["state"] == "current"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    config.set_local({"sync_cloud_url": base_url, "sync_target_library_id": target["library_id"],
                      "sync_target_node_id": target["node_id"]})
    log = (cloud / "server.log").open("wb")
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-m", "reno", "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPOSITORY, env=child_env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with httpx.Client(base_url=base_url, headers={"X-Reno-Token": BROWSER_TOKEN},
                          trust_env=False, follow_redirects=False, timeout=5) as browser:
            until = time.monotonic() + 20
            ready = False
            while time.monotonic() < until:
                assert process.poll() is None, "isolated cloud process exited during startup"
                try:
                    if browser.get("/api/health").status_code == 200:
                        ready = True
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.05)
            assert ready, "isolated cloud did not become ready within its startup deadline"
            yield {"browser": browser, "cloud_db": cloud / "data/reno.db", "cloud_data": cloud / "data",
                   "home_data": home / "data", "source": source, "target": target, "process": process}
    finally:
        # Do not enumerate or kill unrelated Python/uvicorn processes.
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


def _install_synthetic_processing(monkeypatch):
    steps, home_events = [], []
    from scripts.sync_scenario_source import SyntheticSourceRunner
    from reno import source_budget, source_contract, source_resolution

    fixture_root = config.DATA.parent
    inputs = fixture_root / "synthetic-input"
    inputs.mkdir()
    (inputs / "http-source.mp4").write_bytes(CONTENT)
    value = {"root": str(fixture_root), "videos": [{
        "video_id": VIDEO, "title": "隔离 HTTP 同步样本", "filename": "http-source.mp4",
        "short_url": SHORT_URL, "final_url": FINAL_URL,
    }]}
    runner = SyntheticSourceRunner(value, lambda name, video_id: steps.append(name))
    monkeypatch.setattr(source_budget, "_components", lambda: (source_contract, runner))
    monkeypatch.setattr(source_resolution, "run", runner.resolve)

    def unexpected_legacy(*args, **kwargs):
        pytest.fail("The current Home17 HTTP fixture requires its synthetic source runner")

    def media(con, video_id):
        steps.append("media")
        assert video_id == VIDEO
        return {"synthetic": True}

    def asr(con, video_id):
        steps.append("asr")
        db.replace_segments(con, video_id, [{"id": "seg_http", "start_ms": 0, "end_ms": 1000,
                                            "text": "卫生间防水施工前检查基层"}])
        return {"segments": 1}

    def ocr(con, video_id):
        steps.append("ocr")
        return {"spans": 0}

    def atomize(con, video_id):
        steps.append("atomize")
        db.replace_atoms(con, video_id, [{"id": ATOM, "claim": "卫生间防水施工前检查基层", "polarity": "recommend",
            "category": "防水", "space": "卫生间", "conditions": {}, "parameters": [],
            "evidence_refs": [{"modality": "asr", "source_item_id": "seg_http", "start_ms": 0, "end_ms": 1000,
                               "evidence_text": "卫生间防水施工前检查基层"}]}])
        event_id = str(uuid.uuid4())
        review.record_decision(con, atom_id=ATOM, action="confirm", event_id=event_id,
                               client_id="home-http-fixture", note="来自家庭端的人工测试复核")
        home_events.append(event_id)
        return {"n_atoms": 1}

    original_judge = pipeline.judge_all
    def judge():
        steps.append("judge")
        # A single atom has no candidate pair: exercise real judge maintenance
        # and its transaction without ever asking a model.
        return original_judge()

    monkeypatch.setattr(sync_sources, "resolve_url", unexpected_legacy)
    monkeypatch.setattr(ingest, "_ytdlp", unexpected_legacy)
    for name, replacement in (("media", media), ("asr", asr), ("ocr", ocr), ("atomize", atomize)):
        monkeypatch.setattr(getattr(pipeline, name), "run", replacement)
    monkeypatch.setattr(pipeline, "judge_all", judge)
    monkeypatch.setattr(pipeline.judge, "_judge_llm", lambda *a, **k: pytest.fail("HTTP sync fixture must not call a model"))
    monkeypatch.setattr(report, "summary_report", lambda: pytest.fail("Static sync must not request an AI summary"))
    return steps, home_events


def _work_once(*, resume=False):
    job_id = tasks.submit_sync()
    if resume:
        tasks.resume_sync(job_id)
    assert worker.process_pending(max_jobs=1) == 1
    return tasks.get(job_id, include_internal=True)


@pytest.mark.parametrize("drop_publication_reply", [False, True])
def test_real_cloud_http_roundtrip_and_original_publication_retry(two_nodes, monkeypatch, drop_publication_reply):
    node = two_nodes
    browser, cloud_db = node["browser"], node["cloud_db"]
    steps, home_events = _install_synthetic_processing(monkeypatch)
    original_send, sent_publications, dropped = httpx.Client.send, [], False

    def send(client, request, *args, **kwargs):
        nonlocal dropped
        payload = json.loads(request.content) if request.method == "POST" and request.url.path == "/api/sync/push" else None
        response = original_send(client, request, *args, **kwargs)
        if payload and payload.get("kind") == "snapshot":
            sent_publications.append({"snapshot": deepcopy(payload["snapshot"]), "claims": deepcopy(payload["claims"])})
            if drop_publication_reply and not dropped and response.status_code == 200:
                # The server has committed and returned the real response. Lose
                # it at the client transport boundary, not before publication.
                response.read()
                response.close()
                dropped = True
                raise httpx.ReadError("synthetic lost publication response", request=request)
        return response

    monkeypatch.setattr(httpx.Client, "send", send)
    enqueued = browser.post("/api/import", json={"url": SHORT_URL})
    assert enqueued.status_code == 200 and enqueued.json()["status"] == "queued"
    assert _rows(cloud_db, "SELECT status FROM sync_queue") == [{"status": "queued"}]
    assert browser.get("/api/videos").json()["videos"] == []

    first = _work_once()
    store = StateStore(node["home_data"])
    if drop_publication_reply:
        assert first["status"] == "pending"
        assert first["wait_mode"] == "blocked" and first["blocked_reason"] == "indeterminate"
        assert store.read()["published_version"] is None and store.read()["pending_publication"] is not None
        # Local processing and the remote commit are separate facts.
        assert _rows(cloud_db, "SELECT status FROM sync_queue") == [{"status": "done"}]
        recovered = _work_once(resume=True)
        assert recovered["job_id"] == first["job_id"] and recovered["status"] == "done"
        assert recovered["result"]["state"] == "idle"
        assert sent_publications[0] == sent_publications[1]
        assert len(_rows(cloud_db, "SELECT publication_id FROM sync_publication")) == 1
    else:
        assert first["status"] == "done" and first["result"]["state"] == "idle"
    assert steps == ["resolve", "metadata", "download", "media", "asr", "ocr", "atomize", "judge"]
    assert store.read()["pending_publication"] is None and store.read()["published_version"] is not None
    assert _rows(node["home_data"] / "reno.db", "SELECT kind FROM job WHERE kind='process'") == []
    assert _rows(cloud_db, "SELECT kind FROM job WHERE kind IN ('process','judge','report')") == []
    assert _rows(cloud_db, "SELECT status,sync_state FROM video_asset WHERE video_id=?", (VIDEO,)) == [
        {"status": "processed", "sync_state": "ready"}]
    assert _rows(cloud_db, "SELECT status,attempts FROM sync_queue") == [{"status": "done", "attempts": 1}]
    publication = sent_publications[0]
    origin = publication["claims"][0]["origin"]
    assert origin["submitted_url"] == SHORT_URL and origin["resolved_url"] == FINAL_URL
    assert origin["media_sha256"] == hashlib.sha256(CONTENT).hexdigest()
    source_map = sync_sources.SourceMap(node["home_data"], profile=storage_admin.profile(), source_library_id=node["source"]["library_id"],
                                       source_node_id=node["source"]["node_id"])
    mapping_files = list((node["home_data"] / "sync-sources/v2").glob("*.json"))
    assert len(mapping_files) == 1
    assert source_map.get(origin["mapping_id"], mapping_generation=mapping_files[0].stem) == origin
    assert {row["name"] for row in publication["snapshot"]["reports"]} == {
        "incremental_diff", "checklist", "conflicts", "prices"}
    assert browser.get("/api/videos").json()["videos"][0]["video_id"] == VIDEO
    search = browser.get("/api/search", params={"q": "防水基层"})
    assert search.status_code == 200 and search.json()["total"] == 1
    atom = search.json()["results"][0]
    assert atom["id"] == ATOM and atom["review_status"] == "user_confirmed"
    media = browser.get(f"/api/video/{VIDEO}/file", headers={"Range": "bytes=10-49"})
    assert media.status_code == 206 and media.content == CONTENT[10:50]
    assert _rows(cloud_db, "SELECT event_id FROM user_decision") == [{"event_id": home_events[0]}]

    cloud_event = str(uuid.uuid4())
    reviewed = browser.post("/api/decision", json={"atom_id": ATOM, "action": "clear_review",
        "event_id": cloud_event, "client_id": "cloud-http-fixture", "target_fingerprint": atom["target_fingerprint"],
        "note": "来自云端的人工测试复核"})
    assert reviewed.status_code == 200
    idle_job = _work_once()
    assert idle_job["status"] == "done" and idle_job["result"]["state"] == "idle"
    expected_events = {home_events[0], cloud_event}
    assert {r["event_id"] for r in _rows(cloud_db, "SELECT event_id FROM user_decision")} == expected_events
    assert {r["event_id"] for r in _rows(node["home_data"] / "reno.db", "SELECT event_id FROM user_decision")} == expected_events
    assert browser.get("/api/search", params={"q": "防水基层"}).json()["results"][0]["review_status"] == "unreviewed"
    assert _rows(node["home_data"] / "reno.db", "SELECT status FROM knowledge_atom WHERE id=?", (ATOM,)) == [{"status": "candidate"}]
    assert node["process"].poll() is None
