"""Private subprocess entry points for run_sync_scenario.py; synthetic I/O only."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO))


def write_json(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def load_manifest(path):
    if sys.flags.optimize:
        raise RuntimeError("Sync scenario nodes require assertions; remove -O/PYTHONOPTIMIZE")
    path = Path(path).resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    root = Path(value["root"]).resolve(strict=True)
    temp = Path(tempfile.gettempdir()).resolve()
    if (path != root / "qa-manifest.json" or not root.is_relative_to(temp) or root == temp
            or root.is_relative_to(REPO) or REPO.is_relative_to(root)
            or value.get("fixture_version") != "sync-chain-v1"
            or len(value.get("qa_instance_id", "")) != 32
            or value.get("auth_token") != "reno-qa-" + value["qa_instance_id"]):
        raise ValueError("Invalid isolated sync-chain manifest")
    uuid.UUID(hex=value["qa_instance_id"])
    port = value.get("port")
    if type(port) is not int or port != 0 and (not 1024 <= port <= 65535 or port == 8765):
        raise ValueError("Invalid QA port")
    if value.get("base_url") != (f"http://127.0.0.1:{port}" if port else ""):
        raise ValueError("Invalid QA origin")
    videos = value.get("videos")
    if type(videos) is not list or len(videos) != 3:
        raise ValueError("Expected three synthetic source videos")
    for index, item in enumerate(videos):
        expected_name = ("normal.mp4", "lost.mp4", "restart.mp4")[index]
        expected_id = f"BVSyncQA{index + 1:04d}"
        target = root / "synthetic-input" / expected_name
        if (item.get("filename") != expected_name or item.get("video_id") != expected_id
                or item.get("short_url") != f"https://b23.tv/syncSynthetic{index + 1}"
                or item.get("final_url") != f"https://www.bilibili.com/video/{expected_id}"
                or target.resolve(strict=True) != target
                or hashlib.sha256(target.read_bytes()).hexdigest() != item.get("media_sha256")):
            raise ValueError("Synthetic source manifest changed")
    for node in ("home", "cloud"):
        for relative in (node, node + "/config.test.json", node + "/data", node + "/media",
                         node + "/frames_cache", node + "/data/reno.db"):
            target = root / relative
            if target.resolve() != target or not target.resolve().is_relative_to(root):
                raise ValueError("QA paths must not contain filesystem links")
        local = json.loads((root / node / "config.test.json").read_text(encoding="utf-8"))
        if local.get("node_role") != node or local.get("zhipu_api_key") != "" or local.get("ytdlp_cookies") != "":
            raise ValueError("QA node configuration must disable provider credentials")
        if (local.get("sync_token") != "synthetic-sync-" + value["qa_instance_id"]
                or local.get("auth_token") != (value["auth_token"] if node == "cloud" else "")
                or local.get("sync_allow_insecure_test_transport") is not True
                or node == "home" and port and local.get("sync_cloud_url") != value["base_url"]):
            raise ValueError("QA node credentials or transport do not match manifest")
        database = root / node / "data/reno.db"
        if database.exists():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as con:
                marker = con.execute("SELECT value FROM meta WHERE key='qa_instance_id'").fetchone()
            if marker is None or marker[0] != value["qa_instance_id"]:
                raise ValueError("Database does not belong to this synthetic scenario")
    return value


def configure(value, node):
    if "reno.config" in sys.modules:
        raise RuntimeError("Node helper requires a fresh process")
    for key in list(os.environ):
        if key.startswith("RENO_") or key in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"}:
            os.environ.pop(key)
    root = Path(value["root"])
    os.environ.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(root / node / "config.test.json"),
                      RENO_DATA_DIR=str(root / node / "data"), RENO_LLM_TESTS="0")
    from reno import config
    assert config.is_isolated_qa(), "Complete QA isolation is required"
    # Even the optional legacy ingest cookie lookup stays out of the repository.
    config.ROOT = root / node / "synthetic-source-root"
    from reno import llm
    def refuse(*args, **kwargs):
        raise RuntimeError("synthetic fixture forbids all external model calls")
    llm.chat = llm.chat_json = llm._post = llm._post_anthropic = refuse


def initialize(value, node):
    from reno import config, db, storage_admin, sync_access
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    con = db.connect()
    try:
        ident = sync_access.identity(con)
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('qa_instance_id',?)", (value["qa_instance_id"],))
        # Personal sentinels are intentionally synthetic preconditions. No sync
        # queue, video visibility or publication outcome is manufactured here.
        project, source, attachment, card = [str(uuid.uuid4()) for _ in range(4)]
        stamp = "2026-10-01T00:00:00Z"
        data = b"%PDF-1.4\n% synthetic personal sentinel\n%%EOF\n"
        storage = f"synthetic/{attachment}.pdf"
        path = config.DATA / "personal-attachments" / storage
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                    (project, "Synthetic personal sentinel", stamp, stamp))
        con.execute("INSERT INTO source_record(source_id,project_id,source_type,title,created_at,updated_at) VALUES(?,?,'manual_note',?,?,?)",
                    (source, project, "Synthetic source", stamp, stamp))
        con.execute("INSERT INTO decision_card(card_id,project_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (card, project, "Synthetic decision", stamp, stamp))
        con.execute("INSERT INTO source_attachment(attachment_id,source_id,project_id,sha256,byte_size,media_type,original_name,storage_key,state,created_at,operation_id) VALUES(?,?,?,?,?,'application/pdf','synthetic.pdf',?,'ready',?,?)",
                    (attachment, source, project, hashlib.sha256(data).hexdigest(), len(data), storage, stamp, str(uuid.uuid4())))
        con.commit()
        write_json(Path(value["root"]) / node / "identity.json", ident)
    finally:
        con.close()


def pair_and_short_lease(value):
    from reno import db, storage_admin, sync_queue, sync_source_continuity_admin, tasks
    root = Path(value["root"])
    source = json.loads((root / "home/identity.json").read_text(encoding="utf-8"))
    con = db.connect()
    try:
        sync_queue.bind_peer(con, source)
        lease = sync_queue.heartbeat(con, source, str(uuid.uuid4()), lease_seconds=1)["lease"]
        con.commit()
        write_json(root / "cloud/expired-lease.private.json", lease)
    finally:
        con.close()
    profile = storage_admin.profile()
    preview = sync_source_continuity_admin.preview(profile=profile)
    assert preview["state"] == "absent"
    sync_source_continuity_admin.activate(profile=profile, continuity_id=str(uuid.uuid4()),
        activated_at=tasks.utc_now(), expected_basis_sha256=preview["activation_basis_sha256"])


def serve(value):
    import uvicorn
    from app.main import app
    root = Path(value["root"])
    controls = json.loads((root / "control.private.json").read_text(encoding="utf-8"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # Restart the same origin after a deliberate outage despite TIME_WAIT.
        # Do not use SO_REUSEPORT: simultaneous listeners must remain forbidden.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", value.get("port", 0)))
    listener.listen(128)
    port = listener.getsockname()[1]
    if port == 8765:
        listener.close()
        raise RuntimeError("Production port is forbidden")
    server = None

    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/__reno_sync_qa_stop":
            headers = dict(scope["headers"])
            allowed = (scope["method"] == "POST" and hmac.compare_digest(
                headers.get(b"x-reno-qa-control", b""), controls["stop_token"].encode()))
            await send({"type": "http.response.start", "status": 200 if allowed else 403,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"stopping":true}' if allowed else b'{}'})
            if allowed:
                server.should_exit = True
            return
        async def identified(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []),
                    (b"x-reno-qa-instance", value["qa_instance_id"].encode()),
                    (b"x-reno-qa-fixture", value["fixture_version"].encode())]
            await send(message)
        await app(scope, receive, identified)

    server = uvicorn.Server(uvicorn.Config(wrapped, host="127.0.0.1", port=port, log_level="warning"))
    write_json(root / "cloud/runtime.json", {"pid": os.getpid(), "port": port, "qa_instance_id": value["qa_instance_id"]})
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


def work(value, fault, label, *, resume_wait=False):
    import httpx
    from reno import config, db, pipeline, report, review, tasks, worker
    from reno.sync_state import StateStore
    root = Path(value["root"])
    trace_path = root / "home" / f"trace-{label}.json"
    trace = {"synthetic": True, "pid": os.getpid(), "steps": [], "publications": [], "fault": fault,
             "synthetic_worker_lease_ms": tasks.LEASE_MS}
    def save():
        write_json(trace_path, trace)
    def step(name, vid):
        trace["steps"].append({"step": name, "video_id": vid})
        save()
    from scripts.sync_scenario_source import install_sources
    install_sources(value, step)
    def media(con, vid):
        step("media", vid)
        return {"synthetic": True}
    def asr(con, vid):
        step("asr", vid)
        db.replace_segments(con, vid, [{"id": "seg_" + vid, "start_ms": 0, "end_ms": 1500,
                                       "text": "卫生间防水施工前检查基层，人工同步测试样本"}])
        return {"segments": 1}
    def ocr(con, vid):
        step("ocr", vid)
        return {"spans": 0}
    def atomize(con, vid):
        step("atomize", vid)
        atom = "atom_" + vid
        db.replace_atoms(con, vid, [{"id": atom, "claim": "卫生间防水施工前检查基层 " + vid,
            "polarity": "recommend", "category": "防水", "space": "卫生间", "conditions": {}, "parameters": [],
            "evidence_refs": [{"modality": "asr", "source_item_id": "seg_" + vid, "start_ms": 0,
                               "end_ms": 1500, "evidence_text": "人工同步测试样本"}]}])
        review.record_decision(con, atom_id=atom, action="confirm", event_id=str(uuid.uuid4()),
                               client_id="synthetic-home", note="Synthetic review event")
        return {"n_atoms": 1}
    for name, replacement in (("media", media), ("asr", asr), ("ocr", ocr), ("atomize", atomize)):
        getattr(pipeline, name).run = replacement
    pipeline.judge._judge_llm = lambda candidates, **kwargs: (
        [{"pair_id": row["pair_id"], "relation": "different"} for row in candidates], "synthetic-judge")
    def forbidden(*args, **kwargs):
        raise RuntimeError("Synthetic sync must not request AI summary")
    report.summary_report = forbidden
    real_judge = pipeline.judge_all
    def judge():
        step("judge", "all")
        return real_judge()
    pipeline.judge_all = judge
    send = httpx.Client.send
    def fault_send(client, request, *args, **kwargs):
        payload = json.loads(request.content) if request.method == "POST" and request.url.path == "/api/sync/push" else None
        entry = None
        if payload and payload.get("kind") == "snapshot":
            # No headers, leases, claims or raw snapshots enter public evidence.
            canonical = json.dumps({"snapshot": payload["snapshot"], "claims": payload["claims"]},
                                   ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            envelope = json.dumps({key: payload[key] for key in ("snapshot", "claims", "recovery_stream", "recovery_generation")},
                                  ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            entry = {"publication_id": payload["snapshot"]["publication_id"],
                     "snapshot_claims_sha256": hashlib.sha256(canonical).hexdigest(),
                     "publication_envelope_sha256": hashlib.sha256(envelope).hexdigest(), "sent": False}
            trace["publications"].append(entry)
            save()
            if fault == "restart":
                os._exit(73)
        response = send(client, request, *args, **kwargs)
        if entry is not None:
            entry.update(sent=True, response_status=response.status_code)
            save()
            if fault == "lost-response" and response.status_code == 200:
                response.read()
                response.close()
                entry["reply_lost"] = True
                save()
                raise httpx.ReadError("synthetic lost publication response", request=request)
        return response
    httpx.Client.send = fault_send
    job_id = tasks.submit_sync()
    trace["job_id"] = job_id
    trace["resume_requested"] = resume_wait
    if resume_wait:
        tasks.resume_sync(job_id)
    save()
    trace["processed_jobs"] = worker.process_pending(max_jobs=1)
    job = tasks.get(job_id, include_internal=True)
    state = StateStore(config.DATA).read()
    trace.update(job_status=job["status"], result=job["result"],
                 wait_mode=job["wait_mode"], blocked_reason=job["blocked_reason"],
                 pending_publication=state["pending_publication"], recovery_attempt=state["recovery_attempt"],
                 published_version=state["published_version"])
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--node", choices=("home", "cloud"), required=True)
    parser.add_argument("--action", choices=("initialize", "pair", "serve", "work"), required=True)
    parser.add_argument("--fault", choices=("normal", "lost-response", "restart"), default="normal")
    parser.add_argument("--label", default="normal")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        raise ValueError("Invalid trace label")
    value = load_manifest(args.manifest)
    database = Path(value["root"]) / args.node / "data/reno.db"
    if args.action == "initialize" and database.exists():
        raise ValueError("Initialization refuses an existing node database")
    if args.action != "initialize" and not database.is_file():
        raise ValueError("Node must be initialized before running it")
    configure(value, args.node)
    if args.action == "initialize":
        initialize(value, args.node)
    elif args.action == "pair" and args.node == "cloud":
        pair_and_short_lease(value)
    elif args.action == "serve" and args.node == "cloud":
        serve(value)
    elif args.action == "work" and args.node == "home":
        work(value, args.fault, args.label, resume_wait=args.resume)
    else:
        raise ValueError("Action does not belong to node")


if __name__ == "__main__":
    main()
