"""Run a real two-process sync chain in a NEW system TEMP directory.

Platform resolution, downloaded source, ASR/OCR and model responses are
synthetic. HTTP, persistent tasks, ingest, SourceMap, media transfer, snapshots,
event replay and crash recovery use production code. No user library is read.
Default cleanup stops only exact child processes; all evidence is retained.
--keep-server retains the isolated cloud for Chromium; --stop MANIFEST stops it
through an instance-checked private QA endpoint, never a saved PID.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
PERSONAL_TABLES = ("renovation_project", "decision_card", "source_record", "source_attachment",
    "personal_evidence_ref", "checklist_instance", "checklist_item", "checklist_check", "personal_revision", "mutation_receipt")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rows(path, sql, values=()):
    with sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, values)]


def validate_new_root(root):
    root = Path(root).expanduser().resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if (root.exists() or root == temp or not root.is_relative_to(temp)
            or root.is_relative_to(REPO) or REPO.is_relative_to(root)):
        raise ValueError("Sync scenario requires a NEW directory under system TEMP, outside the repository")
    return root


def child_env():
    return {**{k: v for k, v in os.environ.items() if not k.startswith("RENO_") and k not in {
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "PYTHONOPTIMIZE"}},
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(REPO), sysconfig.get_path("purelib")))}


def child_command(manifest, node, action, *extra):
    # Windows venv python.exe redirects to another interpreter process. Start
    # that interpreter directly with this venv's site-packages so Popen owns the
    # actual node PID, including on startup failure before the control route.
    python = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    return [python, str(REPO / "scripts/sync_scenario_node.py"), "--manifest", str(manifest),
            "--node", node, "--action", action, *extra]


def child_run(manifest, node, action, *extra, expected=0):
    log = Path(manifest).parent / node / (f"{action}-" + (extra[-1] if extra else "init") + ".log")
    with log.open("wb") as stream:
        result = subprocess.run(child_command(manifest, node, action, *extra), cwd=REPO, env=child_env(),
            stdout=stream, stderr=subprocess.STDOUT, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode != expected:
        raise RuntimeError(f"Isolated {node} {action} failed (exit {result.returncode}); see {log}")


def personal_snapshot(root, node):
    database = root / node / "data/reno.db"
    tables = {name: sorted(rows(database, f"SELECT * FROM {name}"), key=lambda row: json.dumps(row, sort_keys=True))
              for name in PERSONAL_TABLES}
    files = {path.relative_to(root / node).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (root / node / "data/personal-attachments").rglob("*") if path.is_file()}
    digest = hashlib.sha256(json.dumps({"tables": tables, "files": files}, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "table_counts": {name: len(value) for name, value in tables.items()}, "files": files}


def wait_until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise TimeoutError("Isolated scenario checkpoint did not complete before its deadline")


def stop_child(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def prepare(root):
    if sys.flags.optimize:
        raise RuntimeError("Sync scenarios require Python assertions; remove -O/PYTHONOPTIMIZE")
    import imageio_ffmpeg
    root = validate_new_root(root)
    root.mkdir(parents=True)
    instance = uuid.uuid4().hex
    value = {"root": str(root), "qa_instance_id": instance, "fixture_version": "sync-chain-v1",
             "auth_token": "reno-qa-" + instance, "port": 0, "base_url": "", "videos": []}
    for node in ("home", "cloud"):
        (root / node / "data").mkdir(parents=True)
        write_json(root / node / "config.test.json", {"node_role": node, "sync_token": "synthetic-sync-" + instance,
            "auth_token": value["auth_token"] if node == "cloud" else "", "zhipu_api_key": "", "ytdlp_cookies": "",
            "sync_enabled": node == "home", "sync_allow_insecure_test_transport": True,
            "vlm_enabled": False, "web_ui": "react", "safety_interval_s": 0})
    write_json(root / "control.private.json", {"stop_token": uuid.uuid4().hex + uuid.uuid4().hex})
    (root / "synthetic-input").mkdir()
    for number, label in enumerate(("normal", "lost", "restart")):
        video_id = f"BVSyncQA{number + 1:04d}"
        filename = label + ".mp4"
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc=size=320x240:rate=10", "-t", "6", "-vf", f"hue=h={number * 90}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / "synthetic-input" / filename)]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError("Unable to generate synthetic H264 test-pattern video")
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i",
            str(root / "synthetic-input" / filename), "-f", "null", "-"], stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if decoded.returncode:
            raise RuntimeError("Synthetic MP4 did not decode successfully")
        content = (root / "synthetic-input" / filename).read_bytes()
        value["videos"].append({"video_id": video_id, "filename": filename,
            "title": "人工同步场景 " + label, "short_url": f"https://b23.tv/syncSynthetic{number + 1}",
            "final_url": f"https://www.bilibili.com/video/{video_id}", "media_sha256": hashlib.sha256(content).hexdigest()})
    assert len({v["media_sha256"] for v in value["videos"]}) == 3
    manifest = root / "qa-manifest.json"
    write_json(manifest, value)
    for node in ("home", "cloud"):
        child_run(manifest, node, "initialize")
    child_run(manifest, "cloud", "pair")
    return manifest


def run(root, *, keep_server=False, recovery=False):
    import httpx
    manifest = prepare(root)
    root = manifest.parent
    value = json.loads(manifest.read_text(encoding="utf-8"))
    evidence = {"fixture_version": "sync-chain-v1", "qa_instance_id": value["qa_instance_id"], "status": "running",
        "synthetic": ["test-pattern H264 videos", "platform URL resolution and metadata", "download source",
            "ASR/OCR/atomize/judge responses", "personal sentinel rows and attachments",
            "initial cloud lease: 1 second", "home worker lease: 12000 ms (heartbeat remains 10000 ms)"],
        "real": ["HTTP cloud process", "independent home worker processes", "persistent tasks", "ingest and SourceMap",
            "media upload SHA validation", "snapshot publication transaction", "review events", "Range responses"],
        "checks": {}, "home_runs": []}
    evidence_path = root / "evidence.json"
    before = {node: personal_snapshot(root, node) for node in ("home", "cloud")}
    identities = {node: json.loads((root / node / "identity.json").read_text(encoding="utf-8")) for node in ("home", "cloud")}
    assert identities["home"]["library_id"] != identities["cloud"]["library_id"]
    assert identities["home"]["node_id"] != identities["cloud"]["node_id"]
    evidence["checks"]["independent_identities"] = True
    process, server_log, browser = None, None, None
    succeeded = False

    def start_cloud():
        nonlocal process, server_log, browser
        runtime = root / "cloud/runtime.json"
        if runtime.exists():
            runtime.unlink()
        server_log = (root / "cloud/server.log").open("ab")
        process = subprocess.Popen(child_command(manifest, "cloud", "serve"), cwd=REPO, env=child_env(),
            stdout=server_log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        wait_until(lambda: runtime.exists() or process.poll() is not None)
        if process.poll() is not None:
            raise RuntimeError("Isolated cloud exited at startup; see cloud/server.log")
        state = json.loads(runtime.read_text(encoding="utf-8"))
        assert state["pid"] == process.pid and state["qa_instance_id"] == value["qa_instance_id"]
        value.update(port=state["port"], base_url=f"http://127.0.0.1:{state['port']}")
        write_json(manifest, value)
        local = root / "home/config.test.json"
        settings = json.loads(local.read_text(encoding="utf-8"))
        settings.update(sync_cloud_url=value["base_url"], sync_target_library_id=identities["cloud"]["library_id"],
                        sync_target_node_id=identities["cloud"]["node_id"])
        write_json(local, settings)
        browser = httpx.Client(base_url=value["base_url"], headers={"X-Reno-Token": value["auth_token"]},
                               trust_env=False, timeout=10)
        def healthy():
            try:
                response = browser.get("/api/health")
                return response.status_code == 200 and response.headers.get("x-reno-qa-instance") == value["qa_instance_id"]
            except httpx.RequestError:
                return False
        wait_until(healthy)

    def work(label, fault="normal"):
        child_run(manifest, "home", "work", "--fault", fault, "--label", label, expected=73 if fault == "restart" else 0)
        trace = json.loads((root / "home" / f"trace-{label}.json").read_text(encoding="utf-8"))
        evidence["home_runs"].append(trace)
        write_json(evidence_path, evidence)
        return trace

    def restart_cloud():
        browser.close()
        stop_child(process)
        server_log.close()
        start_cloud()

    def verify_visible(item):
        listing = browser.get("/api/videos")
        assert listing.status_code == 200 and item["video_id"] in {v["video_id"] for v in listing.json()["videos"]}
        result = browser.get("/api/search", params={"q": "防水基层"})
        assert result.status_code == 200 and "atom_" + item["video_id"] in {a["id"] for a in result.json()["results"]}
        response = browser.get(f"/api/video/{item['video_id']}/file", headers={"Range": "bytes=10-49"})
        content = (root / "synthetic-input" / item["filename"]).read_bytes()
        assert response.status_code == 206 and response.content == content[10:50]
        assert response.headers["content-range"] == f"bytes 10-49/{len(content)}"

    try:
        start_cloud()
        sync_token = json.loads((root / "cloud/config.test.json").read_text(encoding="utf-8"))["sync_token"]
        lease = json.loads((root / "cloud/expired-lease.private.json").read_text(encoding="utf-8"))
        time.sleep(1.1)
        denied = browser.post("/api/sync/push", headers={"X-Sync-Token": sync_token},
            json={"kind": "events", "source": identities["home"], "lease": lease, "events": []})
        assert denied.status_code == 409 and denied.json()["code"] == "SYNC_LEASE_EXPIRED"
        evidence["checks"]["expired_lease_rejected"] = denied.json()["code"]
        for item, label, fault in zip(value["videos"], ("normal", "lost", "restart"), ("normal", "lost-response", "restart")):
            response = browser.post("/api/import", json={"url": item["short_url"]})
            assert response.status_code == 200 and response.json()["status"] == "queued"
            queued = rows(root / "cloud/data/reno.db", "SELECT item_id,status FROM sync_queue WHERE url=?", (item["short_url"],))
            assert len(queued) == 1 and queued[0]["status"] == "queued"
            assert browser.get(f"/api/video/{item['video_id']}/file").status_code == 404
            first = work(label, fault)
            if fault == "lost-response":
                assert first["result"]["error_code"] == "SYNC_CLIENT_NETWORK" and first["pending_publication"]
                verify_visible(item)
                count = len(rows(root / "cloud/data/reno.db", "SELECT publication_id FROM sync_publication"))
                replay = work("lost-retry")
                assert replay["result"]["state"] == "idle" and replay["pending_publication"] is None
                assert first["publications"][0]["snapshot_claims_sha256"] == replay["publications"][0]["snapshot_claims_sha256"]
                assert first["publications"][0]["publication_id"] == replay["publications"][0]["publication_id"]
                assert len(rows(root / "cloud/data/reno.db", "SELECT publication_id FROM sync_publication")) == count
                assert not replay["steps"]
                evidence["checks"]["lost_reply_same_publication_replayed_once"] = True
            elif fault == "restart":
                assert not first["publications"][0]["sent"]
                assert browser.get(f"/api/video/{item['video_id']}/file").status_code == 404
                assert item["video_id"] not in {v["video_id"] for v in browser.get("/api/videos").json()["videos"]}
                assert "atom_" + item["video_id"] not in {a["id"] for a in browser.get("/api/search", params={"q": "防水基层"}).json()["results"]}
                assert list((root / "cloud/media/sync" / item["video_id"]).glob("*.mp4"))
                claimed = rows(root / "cloud/data/reno.db", "SELECT status,publication_id FROM sync_queue WHERE item_id=?", (queued[0]["item_id"],))
                assert claimed == [{"status": "claimed", "publication_id": None}]
                public_claim = next(row for row in browser.get("/api/sync-status").json()["queue"]["items"]
                                    if row["item_id"] == queued[0]["item_id"])
                assert public_claim["status"] == "claimed" and public_claim["can_view_video"] is False
                evidence["checks"]["uploaded_unpublished_media_invisible"] = True
                evidence["checks"]["uploaded_unpublished_queue_remains_claimed"] = True
                # Let the actual persisted worker lease expire, without edits.
                expiry = rows(root / "home/data/reno.db", "SELECT lease_until FROM worker_lease")[0]["lease_until"]
                wait_until(lambda: time.time() * 1000 > expiry + 50, timeout=20)
                browser.close()
                stop_child(process)
                server_log.close()
                offline = work("offline")
                assert offline["result"]["state"] == "retry_wait" and offline["pending_publication"]
                start_cloud()
                recovered = work("restart-recovered")
                assert recovered["result"]["state"] == "idle" and recovered["pending_publication"] is None
                assert first["publications"][0]["snapshot_claims_sha256"] == recovered["publications"][0]["snapshot_claims_sha256"]
                assert first["publications"][0]["publication_id"] == recovered["publications"][0]["publication_id"]
                assert first["job_id"] == offline["job_id"] and not recovered["steps"] and not offline["steps"]
                evidence["checks"]["process_crash_network_outage_durable_recovery"] = True
            else:
                assert first["result"]["state"] == "idle" and first["pending_publication"] is None
                evidence["checks"]["normal_ingest_publish_search_range"] = True
            verify_visible(item)
            assert rows(root / "cloud/data/reno.db", "SELECT status FROM sync_queue WHERE item_id=?", (queued[0]["item_id"],)) == [{"status": "done"}]

        # An actual raw HTTP body ends prematurely after the receiver opens its
        # temporary file. No synthetic upload result or ready row is inserted.
        home_state = json.loads((root / "home/data/sync-state.json").read_text(encoding="utf-8"))
        partial_id = "BVSyncQAPartial"
        content = (root / "synthetic-input/normal.mp4").read_bytes()
        descriptor = {"video_id": partial_id, "sha256": hashlib.sha256(content).hexdigest(),
                      "byte_size": len(content), "extension": "mp4"}
        binding = {"publication_id": str(uuid.uuid4()), "source_node_id": identities["home"]["node_id"],
                   "source_version": home_state["published_version"],
                   "recovery_generation": home_state["recovery"]["generation"],
                   "recovery_stream": home_state["recovery"]["stream"]}
        prepared = browser.post("/api/sync/uploads/prepare", headers={"X-Sync-Token": sync_token},
            json={"source": identities["home"], "lease": home_state["lease"],
                  "binding": binding, "descriptors": [descriptor]})
        assert prepared.status_code == 200, prepared.text
        prepared = prepared.json()
        assert prepared["binding"] == binding and prepared["state"] == "reserved"
        assert len(prepared["media"]) == 1 and prepared["media"][0]["descriptor"] == descriptor
        partial_pin = prepared["media"][0]["pin_id"]
        pending_pin = [{"state": "reserved", "artifact_id": None}]
        assert rows(root / "cloud/data/reno.db", "SELECT state,artifact_id FROM sync_upload_pin WHERE pin_id=?",
                    (partial_pin,)) == pending_pin
        evidence["checks"]["interrupted_upload_intent_reserved_before_body"] = True
        metadata = {"source": identities["home"], "lease": home_state["lease"],
                    "intent": {**binding, "descriptor": descriptor}}
        header = base64.urlsafe_b64encode(json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode()).decode()
        with socket.create_connection(("127.0.0.1", value["port"]), timeout=5) as stream:
            request = (f"POST /api/sync/media HTTP/1.1\r\nHost: 127.0.0.1:{value['port']}\r\n"
                       f"X-Sync-Token: {sync_token}\r\nX-Sync-Media: {header}\r\n"
                       f"Content-Type: application/octet-stream\r\nContent-Length: {len(content)}\r\nConnection: close\r\n\r\n")
            stream.sendall(request.encode() + content[:32])
            wait_until(lambda: bool(list((root / "cloud/media").rglob("*.part"))), timeout=5)
        wait_until(lambda: not list((root / "cloud/media").rglob("*.part")))
        assert rows(root / "cloud/data/reno.db", "SELECT state,artifact_id FROM sync_upload_pin WHERE pin_id=?",
                    (partial_pin,)) == pending_pin
        assert not list((root / "cloud/media/sync" / partial_id).glob("*.mp4"))
        evidence["checks"]["interrupted_upload_pin_retained_after_disconnect"] = True
        assert browser.get(f"/api/video/{partial_id}/file").status_code == 404
        assert not rows(root / "cloud/data/reno.db", "SELECT video_id FROM video_asset WHERE video_id=?", (partial_id,))
        evidence["checks"]["interrupted_upload_cleaned_and_invisible"] = True

        # Real cloud decision travels back to a new home worker and converges.
        home_events = rows(root / "home/data/reno.db", "SELECT event_id FROM user_decision WHERE client_id='synthetic-home' ORDER BY event_id")
        assert len(home_events) == 3
        assert rows(root / "cloud/data/reno.db", "SELECT event_id FROM user_decision WHERE client_id='synthetic-home' ORDER BY event_id") == home_events
        atom = browser.get("/api/search", params={"q": "防水基层"}).json()["results"][0]
        event = str(uuid.uuid4())
        changed = browser.post("/api/decision", json={"atom_id": atom["id"], "action": "clear_review",
            "event_id": event, "client_id": "synthetic-cloud", "target_fingerprint": atom["target_fingerprint"], "note": "Synthetic cloud event"})
        assert changed.status_code == 200
        work("event-convergence")
        for node in ("home", "cloud"):
            assert rows(root / node / "data/reno.db", "SELECT event_id FROM user_decision WHERE event_id=?", (event,)) == [{"event_id": event}]
        evidence["checks"]["bidirectional_review_events"] = True
        evidence["review_event_counts"] = {"home_to_cloud": len(home_events), "cloud_to_home": 1}
        after = {node: personal_snapshot(root, node) for node in ("home", "cloud")}
        assert before == after
        evidence["personal_domain"] = {"before": before, "after": after}
        evidence["checks"]["personal_tables_and_attachments_unchanged"] = True
        queue = rows(root / "cloud/data/reno.db", "SELECT status,attempts FROM sync_queue ORDER BY item_id")
        assert queue == [{"status": "done", "attempts": 1}] * 3
        assert not rows(root / "cloud/data/reno.db", "SELECT kind FROM job WHERE kind IN ('process','judge','report')")
        assert not rows(root / "home/data/reno.db", "SELECT kind FROM job WHERE kind='process'")
        evidence["checks"]["queue_done_only_after_publication"] = queue
        mapping_paths = sorted((root / "home/data/sync-sources/v2").glob("*.json"))
        evidence["source_mapping_count"] = len(mapping_paths)
        assert evidence["source_mapping_count"] == 3
        origins, generations = [], set()
        for path in mapping_paths:
            record = json.loads(path.read_text(encoding="utf-8"))
            assert type(record) is dict and set(record) == {"format_version", "mapping_generation", "origin", "resolution"}
            assert type(record["format_version"]) is int and record["format_version"] == 2
            generation = record["mapping_generation"]
            assert type(generation) is str and str(uuid.UUID(generation)) == generation
            assert path.name == generation + ".json" and generation not in generations
            generations.add(generation)
            origins.append(record["origin"])
        assert len(generations) == 3
        evidence["checks"]["source_mapping_generations_match_immutable_v2_records"] = True
        for item in value["videos"]:
            origin = next(row for row in origins if row["video_id"] == item["video_id"])
            assert (origin["submitted_url"] == item["short_url"] and origin["resolved_url"] == item["final_url"]
                    and origin["media_sha256"] == item["media_sha256"]
                    and origin["source_library_id"] == identities["home"]["library_id"]
                    and origin["source_node_id"] == identities["home"]["node_id"])
        evidence["checks"]["source_map_bound_to_identity_url_and_actual_media_sha"] = True
        if recovery:
            if __package__:
                from .sync_recovery_scenario import exercise
            else:
                from sync_recovery_scenario import exercise
            evidence["recovery"] = exercise(root, value, identities, lambda: browser, work, restart_cloud)
            assert {node: personal_snapshot(root, node) for node in ("home", "cloud")} == before
            value["sync_recovery"] = {"exercised": True, "synthetic": True}
        evidence["status"] = "passed"
        evidence["server_retained"] = keep_server
        value["sync_chain"] = {"evidence_file": str(evidence_path), "video_ids": [item["video_id"] for item in value["videos"]],
                               "partial_video_id": partial_id, "synthetic": True}
        write_json(manifest, value)
        succeeded = True
        return manifest
    except BaseException as exc:
        evidence["status"] = "failed"
        # The exception class is safe; response bodies/config values stay local.
        evidence["failure_type"] = type(exc).__name__
        raise
    finally:
        write_json(evidence_path, evidence)
        if browser is not None:
            browser.close()
        if process is not None and not (succeeded and keep_server):
            stop_child(process)
        if server_log is not None:
            server_log.close()


def stop(manifest):
    if sys.flags.optimize:
        raise RuntimeError("Sync scenarios require Python assertions; remove -O/PYTHONOPTIMIZE")
    import httpx
    # Reuse strict validation without importing any production Reno module.
    sys.path.insert(0, str(REPO / "scripts"))
    from sync_scenario_node import load_manifest
    value = load_manifest(manifest)
    if type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535 or value["port"] == 8765:
        raise ValueError("Invalid QA port")
    if value["base_url"] != f"http://127.0.0.1:{value['port']}":
        raise ValueError("Invalid QA origin")
    control = json.loads((Path(value["root"]) / "control.private.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=value["base_url"], trust_env=False, timeout=5) as client:
        identity = client.get("/api/health", headers={"X-Reno-Token": value["auth_token"]})
        if identity.headers.get("x-reno-qa-instance") != value["qa_instance_id"]:
            raise ValueError("Refusing to stop a server that does not match this QA instance")
        response = client.post("/__reno_sync_qa_stop", headers={"X-Reno-QA-Control": control["stop_token"]})
        response.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--root", type=Path)
    group.add_argument("--stop", type=Path)
    parser.add_argument("--keep-server", action="store_true")
    parser.add_argument("--recovery", action="store_true", help="also exercise real CLI restore, hold and complete republication")
    args = parser.parse_args()
    if args.stop:
        stop(args.stop)
        print(json.dumps({"stopped": True, "manifest": str(args.stop)}))
        return
    root = args.root or Path(tempfile.gettempdir()) / ("reno-sync-chain-" + uuid.uuid4().hex)
    manifest = run(root, keep_server=args.keep_server, recovery=args.recovery)
    print(json.dumps({"manifest": str(manifest), "evidence": str(manifest.parent / "evidence.json"),
                      "status": "passed", "server_retained": args.keep_server}, ensure_ascii=False))


if __name__ == "__main__":
    main()
