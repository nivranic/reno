"""P03 extension to the isolated two-process scenario; no production data paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def exercise(root, manifest, identities, client, work, restart_cloud):
    if __package__:
        from .run_sync_scenario import REPO, child_env, rows
    else:
        from run_sync_scenario import REPO, child_env, rows
    root = Path(root).resolve()
    cloud = root / "cloud/data/reno.db"
    home = root / "home/data/reno.db"
    python = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    admin_env = {**child_env(), "RENO_QA_ROOT": str(root), "RENO_CONFIG_FILE": str(root / "cloud/config.test.json"),
                 "RENO_DATA_DIR": str(root / "cloud/data"), "RENO_LLM_TESTS": "0"}

    def admin(*arguments):
        result = subprocess.run([python, "-m", "reno", *arguments], cwd=REPO, env=admin_env,
                                capture_output=True, text=True, encoding="utf-8", timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        log = root / "cloud" / ("recovery-cli-" + uuid.uuid4().hex + ".log")
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Isolated recovery CLI failed; inspect {log}")
        return json.loads(result.stdout)

    def apply_args(value, operation):
        version = value["current_version"]
        return ["--apply", "--operation-id", operation, "--expected-library-id", version["library_id"],
                "--expected-epoch", str(version["library_epoch"]), "--expected-revision", str(version["kb_revision"]),
                "--expected-control-revision", str(value["control_revision"])]

    def control():
        result = client().get("/api/sync-status")
        assert result.status_code == 200
        return result.json()["recovery"]

    def version(path):
        value = {row["key"]: row["value"] for row in rows(path, "SELECT key,value FROM meta WHERE key IN ('library_id','library_epoch','kb_revision')")}
        return {"library_id": value["library_id"], "library_epoch": int(value["library_epoch"]), "kb_revision": int(value["kb_revision"])}

    checkpoints = admin("sync-checkpoints")
    checkpoint = next(item for item in checkpoints if item["recoverable"] and item["row_counts"]["video_asset"] == 1)
    old_publications = rows(cloud, "SELECT * FROM sync_publication ORDER BY publication_id")
    old_events = rows(cloud, "SELECT * FROM user_decision ORDER BY event_id")
    old_queue = rows(cloud, "SELECT * FROM sync_queue ORDER BY item_id")
    old_version = version(cloud)
    old_outboxes = sorted((root / "home/data/sync-outbox").glob("*.json"))
    assert old_outboxes
    outbox = json.loads(old_outboxes[-1].read_text(encoding="utf-8"))

    preview = admin("sync-restore", checkpoint["checkpoint_id"])
    arguments = ["sync-restore", checkpoint["checkpoint_id"], *apply_args(preview, str(uuid.uuid4()))]
    restored = admin(*arguments)
    assert restored["replayed"] is False
    restored_version = version(cloud)
    assert restored_version["library_epoch"] == old_version["library_epoch"]
    assert restored_version["kb_revision"] > old_version["kb_revision"]
    assert control()["state"] == "held"
    assert admin(*arguments)["replayed"] is True
    assert version(cloud) == restored_version
    assert rows(cloud, "SELECT * FROM sync_publication ORDER BY publication_id") == old_publications
    assert rows(cloud, "SELECT * FROM user_decision ORDER BY event_id") == old_events
    assert rows(cloud, "SELECT * FROM sync_queue ORDER BY item_id") == old_queue
    retained = rows(cloud, "SELECT video_id FROM video_asset")
    assert len(retained) == 1
    retained_id = retained[0]["video_id"]
    for item in manifest["videos"]:
        if item["video_id"] != retained_id:
            assert client().get(f"/api/video/{item['video_id']}/file").status_code == 404
    assert len(admin("sync-recovery-checkpoints")) == 1

    # The control state must survive a real cloud process restart. A queued
    # duplicate URL is new work; it must not be silently consumed during hold.
    restart_cloud()
    assert control()["state"] == "held"
    queued = client().post("/api/import", json={"url": manifest["videos"][0]["short_url"]})
    assert queued.status_code == 200
    item_id = rows(cloud, "SELECT MAX(item_id) AS id FROM sync_queue")[0]["id"]
    found = client().get("/api/search", params={"q": "防水基层"}).json()["results"]
    atom = next(item for item in found if item["id"] == "atom_" + retained_id)
    event_id = str(uuid.uuid4())
    decision = client().post("/api/decision", json={"atom_id": atom["id"], "action": "reject",
        "event_id": event_id, "target_fingerprint": atom["target_fingerprint"], "note": "Synthetic review during recovery hold"})
    assert decision.status_code == 200
    held = work("recovery-held")
    assert held["result"]["state"] == "recovery_held" and not held["publications"] and not held["steps"]
    assert rows(home, "SELECT event_id FROM user_decision WHERE event_id=?", (event_id,)) == [{"event_id": event_id}]
    assert rows(cloud, "SELECT status,attempts FROM sync_queue WHERE item_id=?", (item_id,)) == [{"status": "queued", "attempts": 0}]

    sync_token = json.loads((root / "cloud/config.test.json").read_text())["sync_token"]
    def reject_old_outbox():
        local = json.loads((root / "home/data/sync-state.json").read_text())
        rejected = client().post("/api/sync/push", headers={"X-Sync-Token": sync_token}, json={
            "kind": "snapshot", "source": identities["home"], "lease": local["lease"],
            "snapshot": outbox["snapshot"], "claims": outbox["claims"], "recovery_generation": 0,
            "recovery_stream": {key: old_version[key] for key in ("library_id", "library_epoch")}})
        assert rejected.status_code == 409 and rejected.json()["code"] == "SYNC_RECOVERY_GENERATION_MISMATCH"
    reject_old_outbox()
    state = admin("sync-resume")
    floor = state["control"]["source_floor"]
    admin("sync-resume", *apply_args(state, str(uuid.uuid4())))
    assert control()["state"] == "resume_pending"
    reject_old_outbox()

    # The receiver commits a fresh full generation, then its reply is lost.
    # Repeating the home process must retry the exact immutable new batch.
    lost = work("recovery-resume-lost", "lost-response")
    assert lost["result"]["error_code"] == "SYNC_CLIENT_NETWORK" and lost["recovery_attempt"]
    assert lost["publications"][0]["reply_lost"] is True
    assert lost["recovery_attempt"]["publication_id"] == lost["publications"][0]["publication_id"]
    assert control()["state"] == "running"
    published = lost["publications"][0]["publication_id"]
    assert published not in {row["publication_id"] for row in old_publications}
    current = rows(cloud, "SELECT source_epoch,source_revision FROM sync_publication WHERE publication_id=?", (published,))[0]
    assert (current["source_epoch"], current["source_revision"]) > (floor["library_epoch"], floor["kb_revision"])
    assert rows(cloud, "SELECT generation FROM sync_publication_fence WHERE publication_id=?", (published,))[0]["generation"] == control()["generation"]
    retry = work("recovery-resume-retry")
    assert retry["publications"][0]["publication_id"] == published
    assert retry["publications"][0]["snapshot_claims_sha256"] == lost["publications"][0]["snapshot_claims_sha256"]
    assert retry["publications"][0]["publication_envelope_sha256"] == lost["publications"][0]["publication_envelope_sha256"]
    assert retry["pending_publication"] is None and retry["recovery_attempt"] is None
    assert len(rows(cloud, "SELECT publication_id FROM sync_publication WHERE publication_id=?", (published,))) == 1
    assert len(rows(cloud, "SELECT video_id FROM video_asset")) == len(manifest["videos"])
    work("recovery-queue-resume")
    assert rows(cloud, "SELECT status,attempts FROM sync_queue WHERE item_id=?", (item_id,)) == [{"status": "done", "attempts": 1}]
    current_events = {row["event_id"]: row for row in rows(cloud, "SELECT * FROM user_decision")}
    assert all(current_events[row["event_id"]] == row for row in old_events)
    for item in manifest["videos"]:
        assert client().get(f"/api/video/{item['video_id']}/file", headers={"Range": "bytes=0-31"}).status_code == 206
    return {"restored_and_removed_newer_members": True, "restore_idempotent": True,
            "publication_and_review_history_preserved": True, "hold_survived_process_restart": True,
            "reviews_continue_while_queue_attempts_unchanged": True, "old_outbox_fenced": True,
            "resume_fresh_full_generation": True, "lost_resume_reply_replayed_once": True,
            "queue_resumed_after_publication": True, "restored_video_count": 1,
            "republished_video_count": len(manifest["videos"]), "final_control": control()}
