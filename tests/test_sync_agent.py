"""Home orchestration against isolated SQLite and controlled transport faults."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import uuid

import pytest

from reno import config, db, review, sync_agent as agent, sync_events, tasks, worker, storage_admin, storage_lifecycle, sync_upload_pins
from reno.sync_client import SyncClientError
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore


class Remote:
    def __init__(self, target):
        self.target = target
        self.epoch = 1
        self.calls = []
        self.events = []
        self.received_events = []
        self.publications = []
        self.claims = []
        self.fail_publish = False
        self.fail_events = False
        self.fail_heartbeat = False
        self.fail_media = False
        self.after_pull = None
        self.generation = 1
        self.recovery = {"generation": 0, "revision": 0, "state": "running", "source_floor": None,
                         "stream": {"library_id": target["library_id"], "library_epoch": 1}}
        self.uploaded = {}

    def handshake(self):
        self.calls.append("handshake")
        return deepcopy(self.target)

    def heartbeat(self, source, session_id, lease=None):
        self.calls.append(("heartbeat", deepcopy(lease)))
        if self.fail_heartbeat:
            raise SyncError("secret", "SYNC_LEASE_MISMATCH", 409)
        result = dict(lease) if lease else {"session_id": session_id,
            "lease_token": str(uuid.uuid4()), "generation": self.generation}
        result["lease_until"] = agent._stamp(agent._now() + timedelta(minutes=10))
        return {"lease": result, "renewed_claims": len(self.claims),
                "recovery": {**deepcopy(self.recovery), "stream": {"library_id": self.target["library_id"], "library_epoch": self.epoch}}}

    def pull(self, source, lease, *, after=0, through=None, stream=None, claim_limit=0):
        self.calls.append(("pull", after, claim_limit, deepcopy(stream)))
        identity = {"library_id": self.target["library_id"], "library_epoch": self.epoch}
        if stream is not None and stream != identity:
            raise SyncError("restored", "SYNC_EVENT_STREAM_CHANGED", 409)
        high = len(self.events) if through is None else through
        result = {"events": {"events": deepcopy(self.events[after:high]), "next_cursor": high,
                             "high_watermark": high, "has_more": False, "stream": identity},
                  "claims": deepcopy(self.claims[:claim_limit]) if self.recovery["state"] == "running" else [],
                  "recovery": {**deepcopy(self.recovery), "stream": identity}}
        if self.after_pull:
            self.after_pull()
        return result

    def push_events(self, source, lease, events):
        self.calls.append("push_events")
        self.received_events.append(deepcopy(events))
        if self.fail_events:
            self.fail_events = False
            raise SyncClientError("NETWORK")
        return {"received": len(events)}

    def prepare_uploads(self, source, lease, *, binding, descriptors):
        self.calls.append(("prepare", deepcopy(binding), deepcopy(descriptors)))
        return {"binding": deepcopy(binding), "state": "reserved", "media": [
            {"descriptor": deepcopy(item), "pin_id": sync_upload_pins.intent_id({**binding, "descriptor": item}),
             "state": "reserved"}
            for item in descriptors]}

    def verify_upload(self, source, lease, *, intent, check_cancelled):
        check_cancelled()
        self.calls.append(("verify", deepcopy(intent)))
        return {"pin_id": sync_upload_pins.intent_id(intent),
                "stored": self.uploaded.get(intent['descriptor']['video_id']) == (intent['descriptor'], intent['recovery_stream'])}

    def upload_media(self, source, lease, descriptor, path, *, intent, check_cancelled):
        check_cancelled()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == descriptor["sha256"]
        self.calls.append(("media", deepcopy(descriptor)))
        assert intent['descriptor'] == descriptor
        self.uploaded[descriptor['video_id']] = (deepcopy(descriptor), deepcopy(intent['recovery_stream']))

    def push_snapshot(self, source, lease, snapshot, claims, *, recovery_generation=0, recovery_stream=None):
        from reno.sync_transfer import _bindings
        from reno.sync_claims import validate_claim_origin
        _bindings(claims, {v["video_id"] for v in snapshot["videos"]})
        for binding in claims:
            queued = next(entry for entry in self.claims if entry["item_id"] == binding["claim"]["item_id"])
            video = next(v for v in snapshot["videos"] if v["video_id"] == binding["video_id"])
            validate_claim_origin(queued["url"], video["tables"]["video_asset"][0], origin=binding.get("origin"), source=source)
        self.calls.append("publish")
        self.publications.append({"snapshot": deepcopy(snapshot), "claims": deepcopy(claims),
                                  "recovery_generation": recovery_generation, "recovery_stream": recovery_stream})
        if self.recovery["state"] == "resume_pending":
            self.recovery.update(state="running", revision=self.recovery["revision"] + 1)
        if self.fail_publish:
            self.fail_publish = False
            raise SyncClientError("NETWORK")
        if self.fail_media:
            self.fail_media = False
            self.uploaded.clear()
            raise SyncError("missing", "SYNC_MEDIA_NOT_FOUND", 409)
        completed = {binding["claim"]["item_id"] for binding in claims}
        self.claims = [entry for entry in self.claims if entry["item_id"] not in completed]
        return {"publication_id": snapshot["publication_id"], "source_version": snapshot["version"],
                "local_version": {"library_id": self.target["library_id"], "library_epoch": self.epoch,
                                  "kb_revision": len(self.publications)},
                "committed_at": agent._stamp(agent._now()), "replayed": False}

    def fail(self, source, lease, claim, error_class, *, recovery_generation=0, recovery_stream=None):
        self.calls.append(("fail", deepcopy(claim), error_class))
        self.claims = []
        return {"status": "retry_wait" if error_class in {"network", "processing_failed"} else "dead"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, path in {"DATA": tmp_path / "home" / "data", "MEDIA": tmp_path / "home" / "media",
                      "ORIG": tmp_path / "home" / "media" / "originals"}.items():
        monkeypatch.setattr(config, key, path)
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    source, _ = agent._local()
    target = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4()), "handshake": source["handshake"]}
    config.set_local({"sync_enabled": True, "sync_cloud_url": "https://sync.example.test/reno",
                      "sync_target_library_id": target["library_id"], "sync_target_node_id": target["node_id"]})
    remote = Remote(target)
    yield remote
    assert worker.stop_running()


def cycle(remote, **kwargs):
    with tasks.process_lock() as acquired:
        assert acquired
        return agent.run_cycle(client=remote, **kwargs)


def add_video(video_id="video", *, status="processed"):
    path = config.ORIG / video_id / "video.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"controlled artificial media " + video_id.encode()
    path.write_bytes(content)
    con = db.connect()
    try:
        con.execute("INSERT INTO video_asset(video_id,title,content_sha256,status,source_type,files_json) VALUES(?,?,?,?,?,?)",
                    (video_id, "isolated", hashlib.sha256(content).hexdigest(), status, "local_file",
                     json.dumps({"video": path.relative_to(config.MEDIA.parent).as_posix()})))
        if status == "processed":
            db.replace_atoms(con, video_id, [{"id": "atom_" + video_id, "claim": "核对基层", "polarity": "recommend",
                                             "conditions": {}, "parameters": [], "evidence_refs": []}])
        con.commit()
    finally:
        con.close()
    return path


def add_event(*, remove=False):
    con = db.connect()
    try:
        result = review.record_decision(con, atom_id="atom_video", action="confirm")
        con.commit()
        row = con.execute("SELECT * FROM user_decision WHERE event_id=?", (result["event_id"],)).fetchone()
        value = {field: row[field] for field in sync_events.WIRE_FIELDS}
        if remove:
            con.execute("DELETE FROM user_decision")
            review.replay(con)
            con.commit()
        return value
    finally:
        con.close()


def test_sync_requires_actual_worker_lock_and_explicit_enable(env):
    with pytest.raises(SyncError, match="synchronization") as exc:
        agent.run_cycle(client=env)
    assert exc.value.code == "SYNC_WORKER_LOCK_REQUIRED"
    config.set_local({"sync_enabled": False})
    with pytest.raises(SyncError) as exc:
        cycle(env)
    assert exc.value.code == "SYNC_DISABLED"
    assert not (config.DATA / "sync-state.json").exists()
    assert not env.calls


@pytest.mark.parametrize("changes", [{"node_role": "cloud"}, {"sync_target_node_id": "invalid"},
    {"sync_target_library_id": ""}, {"sync_interval_seconds": True}, {"sync_interval_seconds": 1},
    {"sync_interval_seconds": 3601}, {"sync_cloud_url": "https://user:secret@example.test"}])
def test_invalid_configuration_fails_before_io(env, changes):
    config.set_local(changes)
    with pytest.raises(SyncError):
        cycle(env)
    assert not env.calls


def test_seed_then_reviews_then_static_publication_and_no_republish_when_idle(env, monkeypatch):
    add_video()
    incoming = add_event(remove=True)
    env.events = [incoming]
    monkeypatch.setattr(agent.report, "summary_report", lambda: pytest.fail("Summary must be explicit"))
    assert cycle(env)["state"] == "idle"
    assert env.calls[2][0:3] == ("pull", 0, 0)
    assert env.calls.index("push_events") < env.calls.index("publish")
    snapshot = env.publications[0]["snapshot"]
    assert len(snapshot["videos"]) == 1 and snapshot["reports"]
    assert {r["name"] for r in snapshot["reports"]} == {"incremental_diff", "checklist", "conflicts", "prices"}
    state = StateStore(config.DATA).read()
    assert state["seeded"] and state["pending_publication"] is None
    assert state["published_version"] == snapshot["version"]
    assert env.received_events == [[incoming]]
    cycle(env)
    assert len(env.publications) == 1


def test_lost_publication_response_retries_exact_frozen_payload(env):
    add_video()
    env.fail_publish = True
    result = cycle(env)
    state = StateStore(config.DATA).read()
    assert result["error_code"] == "SYNC_CLIENT_NETWORK"
    assert state["published_version"] is None and state["pending_publication"]
    first = deepcopy(env.publications[0])
    assert cycle(env)["state"] == "idle"
    assert env.publications[1] == first
    assert StateStore(config.DATA).read()["pending_publication"] is None
    assert sum(isinstance(call, tuple) and call[0] == "media" for call in env.calls) == 1
    preparations = [call for call in env.calls if isinstance(call, tuple) and call[0] == "prepare"]
    assert len(preparations) == 2 and preparations[0] == preparations[1]
    assert preparations[0][1]["publication_id"] == first["snapshot"]["publication_id"]
    assert preparations[0][2] == [video["media"] for video in first["snapshot"]["videos"]]


def test_missing_acknowledged_media_clears_cache_and_retries_same_batch(env):
    add_video()
    env.fail_media = True
    assert cycle(env)["error_code"] == "SYNC_MEDIA_NOT_FOUND"
    assert StateStore(config.DATA).read()["uploaded_media"] == {}
    assert cycle(env)["state"] == "idle"
    assert env.publications[0] == env.publications[1]
    assert sum(isinstance(call, tuple) and call[0] == "media" for call in env.calls) == 2


def test_wrong_target_identity_is_not_automatically_paired(env):
    env.target["node_id"] = str(uuid.uuid4())
    assert cycle(env)["error_code"] == "SYNC_PEER_MISMATCH"
    assert env.calls == ["handshake"]
    assert StateStore(config.DATA).read()["lease"] is None


def test_first_heartbeat_lost_token_waits_without_forging_lease(env):
    env.fail_heartbeat = True
    assert cycle(env)["state"] == "waiting_lease"
    assert StateStore(config.DATA).read()["lease"] is None
    assert not env.publications
    env.fail_heartbeat = False  # Controlled server expiry allows reacquisition.
    assert cycle(env)["state"] == "idle"
    assert [call[1] for call in env.calls if isinstance(call, tuple) and call[0] == "heartbeat"] == [None, None]


def test_local_restore_rescans_cloud_reviews_and_does_not_publish_old_epoch(env):
    add_video()
    env.events = [add_event(remove=True)]
    env.fail_publish = True
    cycle(env)
    con = db.connect()
    try:
        con.execute("DELETE FROM user_decision")
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
        con.commit()
    finally:
        con.close()
    env.calls.clear()
    assert cycle(env)["state"] == "idle"
    assert next(call for call in env.calls if isinstance(call, tuple) and call[0] == "pull")[1] == 0
    assert env.publications[-1]["snapshot"]["version"]["library_epoch"] == 2
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
    con.close()


def test_cloud_restore_rescans_outbound_and_reuploads_media(env):
    add_video()
    original = add_event()
    assert cycle(env)["state"] == "idle"
    env.epoch += 1
    assert cycle(env)["state"] == "idle"
    assert env.received_events == [[original], [original]]
    assert sum(isinstance(call, tuple) and call[0] == "media" for call in env.calls) == 2


def test_event_receipt_loss_does_not_advance_outbound_cursor(env):
    add_video()
    original = add_event()
    env.fail_events = True
    assert cycle(env)["error_code"] == "SYNC_CLIENT_NETWORK"
    assert StateStore(config.DATA).read()["outbound"]["after"] == 0
    assert not env.publications
    assert cycle(env)["state"] == "idle"
    assert env.received_events == [[original], [original]]


def test_crash_after_merge_commit_replays_uuid_instead_of_losing_or_duplicating_fact(env, monkeypatch):
    add_video()
    env.events = [add_event(remove=True)]
    original = StateStore.apply_review_page
    crashed = False

    def update(store, changes, **kwargs):
        nonlocal crashed
        if not crashed and changes.get("inbound", {}).get("after") == 1:
            crashed = True
            raise OSError("simulated crash before state replacement")
        return original(store, changes, **kwargs)

    monkeypatch.setattr(StateStore, "apply_review_page", update)
    assert cycle(env)["state"] == "retry_wait"
    assert StateStore(config.DATA).read()["inbound"]["after"] == 0
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
    con.close()
    assert cycle(env)["state"] == "idle"
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 1
    con.close()


def test_cancel_after_pull_does_not_commit_remote_events_or_start_processing(env):
    add_video()
    env.events = [add_event(remove=True)]
    stop = threading.Event()
    env.after_pull = stop.set
    assert cycle(env, stop_event=stop)["error_code"] == "SYNC_CANCELLED"
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    con.close()
    assert not env.publications


def test_media_hash_mismatch_never_reaches_remote_upload(env):
    path = add_video()
    path.write_bytes(b"replaced")
    assert cycle(env)["error_code"] == "SYNC_MEDIA_HASH_MISMATCH"
    assert not env.publications and not any(isinstance(c, tuple) and c[0] == "media" for c in env.calls)


def test_sync_tasks_are_internal_and_do_not_hide_user_tasks_by_limit(env):
    with pytest.raises(tasks.TaskError):
        tasks.submit("sync")
    visible = tasks.submit("judge")
    hidden = tasks.submit_sync()
    assert hidden == tasks.submit_sync()
    assert tasks.get(hidden) is None
    assert tasks.list_tasks(1)[0]["task_id"] == visible["task_id"]
    con = db.connect()
    con.execute("UPDATE job SET status='failed' WHERE job_id=?", (hidden,))
    con.commit()
    con.close()
    with pytest.raises(tasks.TaskError) as exc:
        tasks.retry(hidden)
    assert exc.value.code == "NOT_FOUND"


def test_worker_dispatches_sync_under_same_os_lock_and_stop_context(env, monkeypatch):
    seen = []

    def execute(*, stop_event):
        assert tasks.holds_process_lock()
        assert stop_event is not None and not stop_event.is_set()
        seen.append(True)
        return {"state": "idle"}

    monkeypatch.setattr(agent, "run_cycle", execute)
    job_id = tasks.submit_sync()
    assert worker.process_pending() == 1
    assert tasks.get(job_id, include_internal=True)["status"] == "done"
    assert seen == [True] and not tasks.holds_process_lock()


def test_follower_schedules_only_explicitly_enabled_sync(env, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "run_cycle", lambda **kwargs: calls.append(True) or {"state": "idle"})
    assert worker.process_pending(follow=True, max_jobs=1) == 1
    assert calls == [True]
    config.set_local({"sync_enabled": False})
    tasks.submit("report")
    monkeypatch.setattr(agent.report, "run_all", lambda: {})
    assert worker.process_pending(follow=True, max_jobs=1) == 1
    assert calls == [True]


def install_import(env, monkeypatch, *, duplicate=False):
    from reno import ingest, sync_sources
    short = "https://b23.tv/fixtureAlias"
    final = "https://www.bilibili.com/video/BVfake123"
    env.claims = [{"item_id": 1, "url": short, "attempts": 1,
        "lease_until": agent._stamp(agent._now() + timedelta(minutes=10)),
        "claim": {"item_id": 1, "lease_token": str(uuid.uuid4()), "lease_generation": 1}}]
    stamp = sync_sources._now()
    resolution = {"resolution_version": 1, "submitted_url": short, "resolved_url": final, "resolved_at": stamp,
                  "hops": [{"url": short, "peer_ip": "8.8.8.8", "status": 302, "next_url": final},
                           {"url": final, "peer_ip": "8.8.8.8", "status": 200, "next_url": None}]}
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *args, **kwargs: deepcopy(resolution))
    content = add_video().read_bytes() if duplicate else b"controlled downloaded fixture"
    steps = []

    def ytdlp(*args):
        if "--skip-download" in args:
            assert args[-1] == final and "--no-playlist" in args
            steps.append("metadata")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "BVfake123", "title": "import",
                "extractor": "BiliBili", "webpage_url": final, "duration": 1}), stderr="")
        frozen = json.loads(Path(args[args.index("--load-info-json") + 1]).read_text(encoding="utf-8"))
        assert frozen["id"] == "BVfake123" and "webpage_url" not in frozen and "original_url" not in frozen
        assert final not in args and "--no-playlist" in args
        if "ba/bestaudio" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="controlled no separate audio")
        steps.append("download")
        path = Path(args[args.index("-o") + 1])
        path.write_bytes(content)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def process(video_id, *, check_cancelled):
        check_cancelled()
        steps.append("process")
        con = db.connect()
        try:
            db.replace_atoms(con, video_id, [{"id": "atom_" + video_id, "claim": "隔离处理结果", "polarity": "recommend",
                                             "conditions": {}, "parameters": [], "evidence_refs": []}])
            con.execute("UPDATE video_asset SET status='processed' WHERE video_id=?", (video_id,))
            con.commit()
        finally:
            con.close()
        return {}

    monkeypatch.setattr(ingest, "_ytdlp", ytdlp)
    monkeypatch.setattr(agent.pipeline, "run_video", process)
    monkeypatch.setattr(agent.pipeline, "judge_all", lambda: steps.append("judge") or {})
    return steps


@pytest.mark.parametrize("duplicate", [False, True])
def test_queue_processes_with_origin_and_cloud_receipt_without_extra_process_job(env, monkeypatch, duplicate):
    steps = install_import(env, monkeypatch, duplicate=duplicate)
    env.fail_publish = True
    assert cycle(env)["error_code"] == "SYNC_CLIENT_NETWORK"
    assert steps == ["metadata", "download", "process", "judge"]
    frozen = env.publications[0]
    assert frozen["claims"][0]["origin"]["submitted_url"] == "https://b23.tv/fixtureAlias"
    expected_id = "video" if duplicate else "BVfake123"
    assert frozen["claims"][0]["video_id"] == expected_id
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM job WHERE kind='process'").fetchone()[0] == 0
    if duplicate:
        row = con.execute("SELECT source_type,source_url FROM video_asset WHERE video_id='video'").fetchone()
        assert tuple(row) == ("local_file", None)
    con.close()
    state = StateStore(config.DATA).read()
    assert state["work"]["source_mapping_id"] == frozen["claims"][0]["origin"]["mapping_id"]
    assert state["published_version"] is None
    assert cycle(env)["state"] == "idle"
    assert env.publications[1] == frozen
    assert steps == ["metadata", "download", "process", "judge"]
    assert StateStore(config.DATA).read()["work"] is None


@pytest.mark.parametrize("failure,expected", [
    (RuntimeError("yt-dlp failed secret=url?token=do-not-publish"), "processing_failed"),
    (SyncError("dns", "SYNC_SOURCE_NETWORK", 503), "network"),
    (SyncError("timeout", "SYNC_SOURCE_TIMEOUT", 503), "network"),
    (SyncError("private", "SYNC_SOURCE_DNS_REJECTED", 409), "invalid_url")])
def test_download_failures_go_through_queue_retry_contract_without_raw_errors(env, monkeypatch, failure, expected):
    install_import(env, monkeypatch)
    from reno import sync_sources

    def resolve(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sync_sources, "resolve_url", resolve)
    result = cycle(env)
    failed = next(call for call in env.calls if isinstance(call, tuple) and call[0] == "fail")
    assert failed[2] == expected
    assert "secret" not in json.dumps(result) + (config.DATA / "sync-state.json").read_text()
    assert not env.publications and not StateStore(config.DATA).read()["claims"]


def test_report_failure_retains_mapping_instance_for_original_retry(env, monkeypatch):
    install_import(env, monkeypatch)
    original_report = agent.report.run_all

    def fail():
        raise OSError("simulated full disk")

    monkeypatch.setattr(agent.report, "run_all", fail)
    assert cycle(env)["error_code"] == "SYNC_PROCESSING_FAILED"
    state = StateStore(config.DATA).read()
    assert state["claims"] and state["work"]["source_mapping_generation"]
    assert state["pending_publication"] is None
    assert not any(call[0] == "fail" for call in env.calls if isinstance(call, tuple))
    saved = deepcopy(state["work"])
    from reno import sync_sources, ingest
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("report retry cannot choose a new map"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("report retry cannot download another instance"))
    monkeypatch.setattr(agent.report, "run_all", original_report)
    assert cycle(env)["state"] == "idle"
    wrapper = json.loads((config.DATA / "sync-sources/v2" / (saved["source_mapping_generation"] + ".json")).read_bytes())
    assert env.publications[-1]["claims"][0]["origin"] == wrapper["origin"]
    assert wrapper["origin"]["recorded_at"] == saved["source_mapping_inputs"]["recorded_at"]
    assert len(list((config.DATA / "sync-sources/v2").glob("*.json"))) == 1


def test_definitive_publication_rejection_fails_claim_and_keeps_immutable_audit(env, monkeypatch):
    install_import(env, monkeypatch)
    original_push = env.push_snapshot

    def reject(*args, **kwargs):
        raise SyncError("not accepted", "SYNC_CLAIM_SOURCE_MISMATCH", 409)

    monkeypatch.setattr(env, "push_snapshot", reject)
    assert cycle(env)["error_code"] == "SYNC_CLAIM_SOURCE_MISMATCH"
    assert any(call[0] == "fail" and call[2] == "content_invalid" for call in env.calls if isinstance(call, tuple))
    state = StateStore(config.DATA).read()
    assert state["claims"] and state["work"]["source_mapping_generation"]
    assert state["pending_publication"] is None and state["published_version"] is None
    assert len(list((config.DATA / "sync-outbox").glob("*.json"))) == 1
    map_path = config.DATA / "sync-sources/v2" / (state["work"]["source_mapping_generation"] + ".json")
    original_map = map_path.read_bytes()
    wrapper = json.loads(original_map)
    assert wrapper["origin"]["mapping_id"] == state["work"]["source_mapping_id"]
    from reno import sync_sources, ingest
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("claim renewal cannot resolve a new observation"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("claim renewal cannot download a new instance"))
    monkeypatch.setattr(sync_sources, "_now", lambda: pytest.fail("claim renewal must reuse the original map timestamp"))
    monkeypatch.setattr(env, "push_snapshot", original_push)
    env.claims = deepcopy(state["claims"])
    env.claims[0]["claim"].update(lease_token=str(uuid.uuid4()), lease_generation=2)
    assert cycle(env)["state"] == "idle"
    assert env.publications[-1]["claims"][0]["origin"] == wrapper["origin"]
    assert env.publications[-1]["claims"][0]["claim"]["lease_generation"] == 2
    assert map_path.read_bytes() == original_map
    assert len(list(map_path.parent.glob("*.json"))) == 1
    assert len(list((config.DATA / "sync-outbox").glob("*.json"))) == 2


def test_old_recorded_lease_token_is_renewed_after_lost_renewal_reply(env):
    cycle(env)
    store = StateStore(config.DATA, profile=storage_admin.profile())
    lease = store.read()["lease"]
    lease["lease_until"] = agent._stamp(agent._now() - timedelta(seconds=1))
    store.update({"lease": lease})
    assert cycle(env)["state"] == "idle"
    pulses = [call for call in env.calls if isinstance(call, tuple) and call[0] == "heartbeat"]
    assert pulses[-1][1] == lease
    assert store.read()["lease"]["lease_token"] == lease["lease_token"]


def test_explicit_server_expiry_reacquires_before_processing(env, monkeypatch):
    cycle(env)
    previous = deepcopy(StateStore(config.DATA).read()["lease"])
    original = env.heartbeat
    seen = []

    def heartbeat(source, session_id, lease=None):
        seen.append(deepcopy(lease))
        if lease is not None:
            raise SyncError("expired", "SYNC_LEASE_EXPIRED", 409)
        return original(source, session_id)

    monkeypatch.setattr(env, "heartbeat", heartbeat)
    assert cycle(env)["state"] == "idle"
    assert seen == [previous, None]
    assert StateStore(config.DATA).read()["lease"]["lease_token"] != previous["lease_token"]


def test_outside_media_dotdot_is_rejected_before_read(env):
    path = add_video()
    outside = config.MEDIA.parent / "private.mp4"
    outside.write_bytes(path.read_bytes())
    con = db.connect()
    con.execute("UPDATE video_asset SET files_json=? WHERE video_id='video'",
                (json.dumps({"video": "media/../private.mp4"}),))
    con.commit()
    con.close()
    assert cycle(env)["error_code"] == "SYNC_LOCAL_MEDIA_UNSAFE"
    assert not env.publications and not any(isinstance(c, tuple) and c[0] == "media" for c in env.calls)


def test_new_processed_video_without_atoms_gets_a_new_publication_version(env):
    assert cycle(env)["state"] == "idle"
    before = env.publications[-1]["snapshot"]["version"]
    add_video(status="imported")
    con = db.connect()
    con.execute("UPDATE video_asset SET status='processed' WHERE video_id='video'")
    con.commit()
    assert db.knowledge_version(con) == before  # Original triggers miss this.
    con.close()
    assert cycle(env)["state"] == "idle"
    after = env.publications[-1]["snapshot"]
    assert len(after["videos"]) == 1 and not after["videos"][0]["tables"]["knowledge_atom"]
    assert after["version"]["kb_revision"] > before["kb_revision"]


def test_transcript_only_repair_is_not_skipped_by_old_revision(env):
    add_video()
    cycle(env)
    before = env.publications[-1]["snapshot"]["version"]
    con = db.connect()
    db.replace_segments(con, "video", [{"id": "asr1", "start_ms": 0, "end_ms": 100, "text": "修复转写"}])
    con.commit()
    assert db.knowledge_version(con) == before
    con.close()
    assert cycle(env)["state"] == "idle"
    snapshot = env.publications[-1]["snapshot"]
    assert snapshot["videos"][0]["tables"]["transcript_segment"][0]["text"] == "修复转写"
    assert snapshot["version"]["kb_revision"] > before["kb_revision"]


def test_report_only_update_publishes_same_knowledge_version_without_model(env):
    add_video()
    cycle(env)
    before = env.publications[-1]["snapshot"]
    @agent.report._managed("summary")
    def controlled_summary():
        with agent.report._read_connection():
            pass
        return agent.report._publish("summary", "用户显式生成的受控总结\n")

    controlled_summary()
    assert cycle(env)["state"] == "idle"
    after = env.publications[-1]["snapshot"]
    assert after["version"] == before["version"]
    assert after["publication_id"] != before["publication_id"]
    assert "summary" in {item["name"] for item in after["reports"]}


def test_definitive_rejection_with_expired_fail_retains_mapping_for_renewed_claim(env, monkeypatch):
    install_import(env, monkeypatch)
    original_push, original_fail = env.push_snapshot, env.fail

    def reject(*args, **kwargs):
        raise SyncError("rejected", "SYNC_VERSION_CONFLICT", 409)

    def expired(*args, **kwargs):
        raise SyncError("old claim", "SYNC_CLAIM_EXPIRED", 409)

    monkeypatch.setattr(env, "push_snapshot", reject)
    monkeypatch.setattr(env, "fail", expired)
    assert cycle(env)["error_code"] == "SYNC_VERSION_CONFLICT"
    state = StateStore(config.DATA).read()
    assert state["pending_publication"] is None and state["published_version"] is None
    assert state["claims"] and state["work"]["source_mapping_generation"]
    path = config.DATA / "sync-sources/v2" / (state["work"]["source_mapping_generation"] + ".json")
    original_mapping = path.read_bytes()
    audit = next((config.DATA / "sync-outbox").glob("*.json"))
    original_outbox = audit.read_bytes()
    from reno import ingest, sync_sources
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("expired fail reply cannot choose a new resolution"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("expired fail reply cannot download a new instance"))
    monkeypatch.setattr(sync_sources, "_now", lambda: pytest.fail("expired fail reply must keep the original creation time"))
    monkeypatch.setattr(env, "push_snapshot", original_push)
    monkeypatch.setattr(env, "fail", original_fail)
    env.claims = deepcopy(state["claims"])
    env.claims[0]["claim"].update(lease_generation=2, lease_token=str(uuid.uuid4()))
    assert cycle(env)["state"] == "idle"
    assert env.publications[-1]["claims"][0]["origin"] == json.loads(original_mapping)["origin"]
    assert env.publications[-1]["claims"][0]["claim"]["lease_generation"] == 2
    assert path.read_bytes() == original_mapping and len(list(path.parent.glob("*.json"))) == 1
    assert audit.read_bytes() == original_outbox
    assert StateStore(config.DATA).read()["work"] is None


def test_foreground_interrupt_releases_internal_scope_for_next_cycle(env, monkeypatch):
    def interrupt(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent, "run_cycle", interrupt)
    job_id = tasks.submit_sync()
    with pytest.raises(KeyboardInterrupt):
        worker.process_pending()
    assert tasks.get(job_id, include_internal=True)["status"] == "failed"
    assert tasks.submit_sync() != job_id
    assert not tasks.holds_process_lock()


def test_cloud_restore_with_unversioned_local_repair_reserves_new_version(env):
    add_video()
    cycle(env)
    original_version = env.publications[-1]["snapshot"]["version"]
    con = db.connect()
    db.replace_segments(con, "video", [{"id": "s1", "start_ms": 0, "end_ms": 100, "text": "恢复前修复"}])
    con.commit()
    assert db.knowledge_version(con) == original_version
    con.close()
    env.epoch += 1
    assert cycle(env)["state"] == "idle"
    assert env.publications[-1]["snapshot"]["version"]["kb_revision"] > original_version["kb_revision"]


def test_shutdown_during_pipeline_prevents_next_step_and_cloud_publication(env, monkeypatch):
    install_import(env, monkeypatch)
    stop = threading.Event()

    def cancelled_pipeline(video_id, *, check_cancelled):
        stop.set()
        return {"media": "ERROR: interrupted"}

    monkeypatch.setattr(agent.pipeline, "run_video", cancelled_pipeline)
    assert cycle(env, stop_event=stop)["error_code"] == "SYNC_CANCELLED"
    assert not env.publications
    assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


def test_epoch_reset_and_cursor_reset_are_one_durable_write(env, monkeypatch):
    add_video()
    env.events = [add_event(remove=True)]
    cycle(env)
    old = StateStore(config.DATA).read()
    con = db.connect()
    con.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
    con.commit()
    con.close()
    original = StateStore.update

    def crash(store, changes):
        if "local_epoch" in changes:
            assert changes["inbound"] == agent._EMPTY_CURSOR
            assert changes["outbound"] == agent._EMPTY_CURSOR and changes["seeded"] is False
            raise OSError("simulated crash before atomic epoch reset")
        return original(store, changes)

    monkeypatch.setattr(StateStore, "update", crash)
    with pytest.raises(OSError):
        cycle(env)
    assert StateStore(config.DATA).read()["local_epoch"] == old["local_epoch"]
    monkeypatch.setattr(StateStore, "update", original)
    assert cycle(env)["state"] == "idle"
    assert StateStore(config.DATA).read()["local_epoch"] == 2


def test_bounded_initial_review_scan_persists_backlog_and_does_not_publish_early(env, monkeypatch):
    add_video()
    env.events = [add_event(remove=True), add_event(remove=True)]
    original = env.pull

    def one_page(source, lease, **kwargs):
        result = original(source, lease, **kwargs)
        if kwargs["after"] == 0:
            result["events"].update(events=[env.events[0]], next_cursor=1, has_more=True)
        return result

    monkeypatch.setattr(env, "pull", one_page)
    monkeypatch.setattr(agent, "MAX_EVENT_PAGES", 1)
    assert cycle(env) == {"state": "review_backlog"}
    state = StateStore(config.DATA).read()
    assert state["status"]["state"] == "review_backlog" and not state["seeded"]
    assert state["inbound"]["after"] == 1 and state["inbound"]["through"] == 2
    assert not env.publications and not state["claims"]
    assert cycle(env)["state"] == "idle"
    assert StateStore(config.DATA).read()["seeded"]
