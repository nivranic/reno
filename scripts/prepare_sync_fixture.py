"""Prepare synthetic cloud UI states in a NEW external QA root.

Reuses the product fixture's test-pattern video and guarded manifest. Queue
outcomes below are deliberately seeded UI examples, not sync acceptance proof.
Run in a fresh process; never point --root at an existing or repository path.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def prepare(root: Path, port: int = 18766) -> Path:
    root = root.expanduser().resolve()
    if root.exists() or root.is_relative_to(REPO) or REPO.is_relative_to(root):
        raise ValueError("Sync UI QA requires a new directory outside the repository")
    if "reno.config" in sys.modules:
        raise RuntimeError("Prepare the sync UI fixture in a fresh process")
    from scripts.prepare_product_fixture import prepare as prepare_product

    manifest_path = prepare_product(root, port)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from reno import config, db, sync_protocol, sync_queue

    # Both credentials are disposable and unique to this generated QA root.
    sync_token = "reno-sync-ui-qa-" + manifest["qa_instance_id"]
    config.set_local({"node_role": "cloud", "sync_token": sync_token, "sync_enabled": False})
    if not config.is_isolated_qa():
        raise RuntimeError("Sync UI fixture did not retain complete QA path isolation")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = lambda value: value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    source = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4())}
    sentinel = "RENO_SYNC_UI_QUERY_SENTINEL_" + manifest["qa_instance_id"]
    ready_video = manifest["video_ids"][0]
    missing_video = "BVQAMissing0001"
    rows = []
    con = db.connect()
    try:
        source["handshake"] = sync_protocol.schema_handshake(con)
        sync_queue.bind_peer(con, source, now=now)
        lease = sync_queue.heartbeat(con, source, str(uuid.uuid4()), now=now, lease_seconds=3600,
                                     metrics={"cookie_state": "unknown", "queue_depth": 35, "active_jobs": 1})["lease"]
        # Public visibility is an explicit synthetic UI condition. We do not
        # manufacture publication receipts or run a real download/model here.
        con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id=?", (ready_video,))
        for index in range(1, 29):
            rows.append({"label": f"queued_page_{index}", "status": "queued", "attempts": 0})
        rows.extend([
            {"label": "queued", "status": "queued", "attempts": 0},
            {"label": "claimed_active", "status": "claimed", "attempts": 1, "lease_until": lease["lease_until"]},
            {"label": "claimed_expired", "status": "claimed", "attempts": 2,
             "lease_until": stamp(now - timedelta(minutes=5)), "last_error_class": "lease_expired"},
            {"label": "retry_wait", "status": "retry_wait", "attempts": 2,
             "retry_at": stamp(now + timedelta(minutes=5)), "last_error_class": "network"},
            {"label": "dead", "status": "dead", "attempts": 5, "last_error_class": "invalid_url"},
            {"label": "done_missing", "status": "done", "attempts": 1, "video_id": missing_video},
            {"label": "done_ready", "status": "done", "attempts": 1, "video_id": ready_video},
        ])
        row_ids = {}
        for index, row in enumerate(rows, 1):
            # A long allowed source path exercises truncation at phone width.
            path = f"/video/BVQASYNC{index:08d}" + ("/" + "synthetic-segment-" * 24 if row["label"] == "dead" else "")
            url = f"https://www.bilibili.com{path}?qa_secret={sentinel}&p=1"
            sync_protocol.validate_ingest_url(url)
            owner = lease["session_id"] if row["status"] == "claimed" else None
            token = str(uuid.uuid4()) if owner else None
            cur = con.execute("""INSERT INTO sync_queue
                (url,status,attempts,lease_until,last_error_class,created_at,updated_at,
                 owner_id,owner_generation,lease_token,lease_generation,retry_at,video_id,publication_id,result_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                url, row["status"], row["attempts"], row.get("lease_until"), row.get("last_error_class"),
                stamp(now - timedelta(minutes=40 - index)), stamp(now - timedelta(seconds=36 - index)),
                owner, lease["generation"] if owner else 0, token, 1 if owner else 0,
                row.get("retry_at"), row.get("video_id"),
                str(uuid.uuid4()) if row["status"] == "done" else None,
                hashlib.sha256(f"synthetic-ui-row:{index}".encode()).hexdigest() if row["status"] == "done" else None))
            row_ids[row["label"]] = cur.lastrowid
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('qa_sync_fixture','sync-status-v1')")
        con.commit()
        summary = sync_queue.summary(con, now=now)
        page = sync_queue.list_items(con, offset=0, limit=10, now=now)
        if (not summary["online"] or summary["expired_claims"] != 1 or page["total"] != 35
                or sum(item["can_view_video"] for item in page["items"]) != 1
                or sentinel in json.dumps(page)):
            raise RuntimeError("Prepared queue does not match the synthetic UI contract")
    finally:
        con.close()
    manifest["sync_fixture"] = {
        "version": "sync-status-v1", "role": "cloud", "row_count": len(rows), "page_size": 10,
        "row_ids": row_ids, "ready_video_id": ready_video, "missing_video_id": missing_video,
        "query_sentinel": sentinel, "synthetic_sync_token": sync_token,
        "lease_until": lease["lease_until"], "expected_counts": summary["counts"],
        "evidence_scope": "人工队列状态与测试图样，仅用于真实页面展示验证，不代表实际同步链路通过",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    path = prepare(args.root, args.port)
    print(json.dumps({"manifest": str(path), "port": args.port, "role": "cloud", "synthetic_queue_rows": 35}, ensure_ascii=False))


if __name__ == "__main__":
    main()
