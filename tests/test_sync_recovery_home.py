"""Recovery fences preserve real local progress using disposable home libraries."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import threading
import uuid

import pytest

from reno import budget, config, storage_admin, storage_lifecycle, sync_agent as agent, sync_sources, tasks
from reno.sync_client import SyncClientError
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_sync_agent import env, cycle, add_video, add_event, install_import
from tests.test_sync_state import store, snapshot, claim


def control(state="held", *, generation=1, revision=1, floor=None, stream=None):
    return {"generation": generation, "revision": revision, "state": state, "source_floor": floor,
            "stream": stream or {"library_id": config.get("sync_target_library_id"), "library_epoch": 1}}


def active_cycles(monkeypatch):
    active = []
    original = agent._Cycle.__init__

    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        active.append(self)

    monkeypatch.setattr(agent._Cycle, "__init__", capture)
    return active


def outbox_bytes(state):
    return (config.DATA / "sync-outbox" / (state["pending_publication"]["publication_id"] + ".json")).read_bytes()


def test_hold_preserves_pending_work_and_keeps_review_channel_alive(env, monkeypatch):
    steps = install_import(env, monkeypatch, duplicate=True)
    env.fail_publish = True
    assert cycle(env)["error_code"] == "SYNC_CLIENT_NETWORK"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    before = store.read()
    frozen = outbox_bytes(before)
    incoming = add_event(remove=True)
    env.events = [incoming]
    env.recovery = control(floor=env.publications[0]["snapshot"]["version"])
    env.calls.clear()
    assert cycle(env) == {"state": "recovery_held"}
    after = store.read()
    assert after["work"] == before["work"] and after["claims"] == before["claims"]
    assert after["pending_publication"] == before["pending_publication"] and outbox_bytes(after) == frozen
    assert after["inbound"]["after"] == 1 and after["outbound"]["after"] > before["outbound"]["after"]
    assert "push_events" in env.calls and "publish" not in env.calls
    assert not any(isinstance(c, tuple) and c[0] in {"media", "fail"} for c in env.calls)
    assert steps == ["metadata", "download", "process", "judge"]


def test_resume_uses_new_uuid_higher_version_full_snapshot_and_exact_retry(env):
    add_video()
    env.fail_publish = True
    cycle(env)
    store = StateStore(config.DATA, profile=storage_admin.profile())
    old = store.read()
    old_bytes = outbox_bytes(old)
    floor = deepcopy(env.publications[0]["snapshot"]["version"])
    floor["kb_revision"] += 10
    env.recovery = control(floor=floor)
    assert cycle(env)["state"] == "recovery_held"
    env.recovery = control("resume_pending", revision=2, floor=floor)
    env.fail_publish = True
    assert cycle(env)["state"] == "recovery_resuming"
    attempted = deepcopy(env.publications[-1])
    assert attempted["recovery_generation"] == 1 and attempted["claims"] == []
    assert attempted["snapshot"]["publication_id"] != old["pending_publication"]["publication_id"]
    assert attempted["snapshot"]["version"]["kb_revision"] > floor["kb_revision"]
    assert len(attempted["snapshot"]["videos"]) == 1 and attempted["snapshot"]["reports"]
    assert outbox_bytes(old) == old_bytes
    assert store.read()["recovery_attempt"] is not None
    assert cycle(env)["state"] == "idle"
    assert env.publications[-1] == attempted
    after = store.read()
    assert after["pending_publication"] == old["pending_publication"] and after["recovery_attempt"] is None
    assert after["applied_recovery_generation"] == 1
    histories = [json.loads(path.read_bytes()) for path in (config.DATA / "sync-work-history").glob("*.json")]
    assert any(value["pending_publication"] == old["pending_publication"] for value in histories)
    assert any(value["recovery_attempt"] is not None and value["recovery_attempt"]["publication_id"] == attempted["snapshot"]["publication_id"] for value in histories)
    assert cycle(env)["state"] == "recovery_blocked"
    assert outbox_bytes(old) == old_bytes and len(env.publications) == 3


def test_crash_after_outbox_creation_reuses_uuid_and_does_not_regenerate_reports(env, monkeypatch):
    add_video()
    env.recovery = control("resume_pending", revision=2)
    original_update = StateStore.install_recovery_publication
    original_report = agent.report.run_all
    reports = []
    crash = [True]

    def update(self, metadata, **kwargs):
        if crash[0]:
            crash[0] = False
            raise OSError("controlled file/state crash gap")
        return original_update(self, metadata, **kwargs)

    def report(*args, **kwargs):
        reports.append(True)
        return original_report(*args, **kwargs)

    monkeypatch.setattr(StateStore, "install_recovery_publication", update)
    monkeypatch.setattr(agent.report, "run_all", report)
    assert cycle(env)["state"] == "recovery_blocked"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    state = store.read()
    attempt = state["recovery_attempt"]
    metadata = store.find_publication(attempt["publication_id"])
    expected = store.read_publication(metadata)
    assert state["pending_publication"] is None and not env.publications
    assert cycle(env)["state"] == "idle"
    assert env.publications == [expected] and reports == [True]


def test_resume_cannot_invent_a_higher_source_epoch(env):
    add_video()
    _, version = agent._local()
    floor = {**version, "library_epoch": version["library_epoch"] + 1}
    env.recovery = control("resume_pending", revision=2, floor=floor)
    assert cycle(env) == {"state": "recovery_blocked", "error_code": "SYNC_RECOVERY_SOURCE_EPOCH_BEHIND"}
    assert agent._local()[1] == version and not env.publications
    assert StateStore(config.DATA).read()["recovery_attempt"] is None


def test_hold_during_pipeline_preserves_download_then_resume_rebinds_authoritative_claim(env, monkeypatch):
    steps = install_import(env, monkeypatch)
    original_process = agent.pipeline.run_video

    def hold(video_id, *, check_cancelled):
        env.recovery = control()
        check_cancelled.__self__.store.observe_recovery(env.recovery)
        check_cancelled()

    monkeypatch.setattr(agent.pipeline, "run_video", hold)
    assert cycle(env)["state"] == "recovery_held"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = store.read()
    assert saved["work"]["phase"] == "downloaded" and steps == ["metadata", "download"]
    assert not any(isinstance(c, tuple) and c[0] == "fail" for c in env.calls)
    original_heartbeat = env.heartbeat

    def heartbeat(source, session_id, lease=None):
        if lease is not None and lease["generation"] == 1:
            raise SyncError("expired", "SYNC_LEASE_EXPIRED", 409)
        return original_heartbeat(source, session_id, lease)

    env.generation = 2
    monkeypatch.setattr(env, "heartbeat", heartbeat)
    assert cycle(env)["state"] == "recovery_held"
    assert store.read()["work"] == saved["work"]
    monkeypatch.setattr(agent.pipeline, "run_video", original_process)
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("saved recovery must not resolve again"))
    env.recovery = control("resume_pending", revision=2)
    assert cycle(env)["state"] == "idle"
    assert env.publications[0]["claims"] == [] and env.claims
    assert store.read()["work"]["phase"] == "downloaded"
    assert steps == ["metadata", "download"]
    env.claims[0]["claim"]["lease_generation"] = 2
    env.claims[0]["claim"]["lease_token"] = str(uuid.uuid4())
    assert cycle(env)["state"] == "idle"
    assert env.publications[-1]["claims"][0]["claim"]["lease_generation"] == 2
    assert store.read()["work"] is None and env.claims == []
    assert steps == ["metadata", "download", "process", "judge"]


def test_saved_work_media_identity_failure_blocks_without_failing_cloud_claim(env, monkeypatch):
    install_import(env, monkeypatch)
    active = active_cycles(monkeypatch)
    original = agent.pipeline.run_video

    def hold(*args, **kwargs):
        env.recovery = control()
        active[-1].store.observe_recovery(env.recovery)
        kwargs["check_cancelled"]()

    monkeypatch.setattr(agent.pipeline, "run_video", hold)
    assert cycle(env)["state"] == "recovery_held"
    work = StateStore(config.DATA).read()["work"]
    mapping_path = config.DATA / "sync-sources/v2" / (work["source_mapping_generation"] + ".json")
    original_mapping = mapping_path.read_bytes()
    (config.ORIG / work["video_id"] / "video.mp4").write_bytes(b"tampered local media")
    env.recovery = control("resume_pending", revision=2)
    monkeypatch.setattr(agent.pipeline, "run_video", lambda *a, **k: pytest.fail("damaged saved work cannot process"))
    monkeypatch.setattr(agent.pipeline, "judge_all", lambda *a, **k: pytest.fail("damaged saved work cannot judge"))
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("damaged saved work cannot resolve"))
    monkeypatch.setattr(budget, "reserve", lambda *a, **k: pytest.fail("damaged saved work cannot reserve paid work"))
    monkeypatch.setattr(budget, "mark_dispatching", lambda *a, **k: pytest.fail("damaged saved work cannot dispatch paid work"))
    bootstrapped = cycle(env)
    assert bootstrapped["recovery_bootstrapped"] is True
    assert env.publications[0]["snapshot"]["videos"] == [] and env.publications[0]["claims"] == []
    result = cycle(env)
    assert result["state"] == "retry_wait"
    assert result["error_code"] == "SYNC_SOURCE_MEDIA_MISMATCH"
    assert len(env.publications) == 1 and StateStore(config.DATA).read()["work"] == work
    assert mapping_path.read_bytes() == original_mapping
    assert not any(isinstance(c, tuple) and c[0] == "fail" for c in env.calls)


def test_late_old_ack_keeps_new_hold_and_work(env, monkeypatch):
    install_import(env, monkeypatch)
    active = active_cycles(monkeypatch)
    original = env.push_snapshot

    def acknowledge(*args, **kwargs):
        receipt = original(*args, **kwargs)
        env.recovery = control(floor=receipt["source_version"])
        active[-1].store.observe_recovery(env.recovery)
        return receipt

    monkeypatch.setattr(env, "push_snapshot", acknowledge)
    assert cycle(env)["state"] == "recovery_held"
    state = StateStore(config.DATA).read()
    assert state["recovery"]["state"] == "held" and state["status"]["state"] == "recovery_held"
    assert state["work"] is not None and state["claims"]
    assert state["applied_recovery_generation"] == 0


def test_hold_between_receipt_and_atomic_ack_commit_retains_progress(env, monkeypatch):
    install_import(env, monkeypatch)
    original = StateStore.acknowledge_publication

    def delayed(self, *args, **kwargs):
        pulse = threading.Thread(target=lambda: self.observe_recovery(control()))
        pulse.start()
        pulse.join(5)
        assert not pulse.is_alive()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StateStore, "acknowledge_publication", delayed)
    assert cycle(env)["state"] == "recovery_held"
    state = StateStore(config.DATA).read()
    assert state["work"] is not None and state["claims"]
    assert state["recovery"]["state"] == "held"
    assert list((config.DATA / "sync-work-history").glob("*.json"))


def test_hold_between_review_merge_and_claim_commit_keeps_work(env, monkeypatch):
    install_import(env, monkeypatch)
    env.fail_publish = True
    cycle(env)
    env.recovery = control("resume_pending", revision=2)
    assert cycle(env)["state"] == "idle"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = store.read()
    # Bootstrap cannot silently dispose of this original failed publication.
    # Retire only its sending authorization via the public P05 control path
    # before arranging a race at the subsequent legitimate claim commit.
    from tests.test_sync_no_resend_agent import authorize
    metadata = saved["pending_publication"]
    old_bytes = outbox_bytes(saved)
    def abandon(source, lease, *, binding, operation_id):
        assert storage_lifecycle.current_guard() is None
        return {"binding": deepcopy(binding), "operation_id": operation_id, "state": "abandoned"}
    monkeypatch.setattr(env, "abandon_uploads", abandon, raising=False)
    with tasks.process_lock(profile=store.profile) as acquired:
        assert acquired
        with store.exclusive():
            operation_id, _ = authorize(store, metadata)
        assert agent.resume_no_resend(operation_id, client=env)["state"] == "abandoned"
    assert outbox_bytes(saved) == old_bytes
    assert store.read()["claims"] == saved["claims"] and store.read()["work"] == saved["work"]
    # A missing claim page now retains mapped work and has no claim mutation.
    # Renew a matching claim so the pause still races a real pending commit.
    env.claims = deepcopy(saved["claims"])
    env.claims[0]["claim"].update(lease_generation=saved["work"]["lease_generation"] + 1,
                                 lease_token=str(uuid.uuid4()))
    map_path = config.DATA / "sync-sources/v2" / (saved["work"]["source_mapping_generation"] + ".json")
    original_mapping = map_path.read_bytes()
    original = StateStore.apply_review_page

    def delayed(self, updates, **kwargs):
        if kwargs.get("claim_changes") is not None:
            pulse = threading.Thread(target=lambda: self.observe_recovery(control(generation=2, revision=4)))
            pulse.start()
            pulse.join(5)
            assert not pulse.is_alive()
        return original(self, updates, **kwargs)

    monkeypatch.setattr(StateStore, "apply_review_page", delayed)
    assert cycle(env)["state"] == "recovery_held"
    after = store.read()
    assert after["claims"] == saved["claims"] and after["work"] == saved["work"]
    assert after["recovery"] == control(generation=2, revision=4)
    assert map_path.read_bytes() == original_mapping


def test_acknowledged_generation_does_not_republish_delayed_resume_observation(env, monkeypatch):
    add_video()
    env.recovery = control("resume_pending", revision=2)
    original = env.push_snapshot

    def stale(*args, **kwargs):
        reply = original(*args, **kwargs)
        env.recovery = control("resume_pending", revision=2)
        return reply

    monkeypatch.setattr(env, "push_snapshot", stale)
    assert cycle(env)["state"] == "recovery_resuming"
    assert cycle(env)["state"] == "recovery_resuming"
    assert len(env.publications) == 1
    env.recovery = control("running", revision=3)
    assert cycle(env)["state"] == "idle" and len(env.publications) == 2
    assert env.publications[0]["snapshot"]["publication_id"] != env.publications[1]["snapshot"]["publication_id"]
    assert all(value["claims"] == [] for value in env.publications)
    assert all("恢复同步资料" not in entry["content"] for entry in env.publications[-1]["snapshot"]["reports"])


@pytest.mark.parametrize("rejected", [False, True])
def test_late_fail_response_cannot_clear_new_hold_or_relabel_original_generation(env, monkeypatch, rejected):
    install_import(env, monkeypatch)
    active = active_cycles(monkeypatch)

    def bad(*args, **kwargs):
        raise SyncError("controlled failure", "SYNC_VERSION_CONFLICT", 409)

    monkeypatch.setattr(env, "push_snapshot", bad)
    failures = []

    def fail(source, lease, claim, error_class, *, recovery_generation, recovery_stream):
        failures.append((recovery_generation, recovery_stream))
        env.recovery = control()
        active[-1].store.observe_recovery(env.recovery)
        if rejected:
            raise SyncError("held", "SYNC_RECOVERY_HELD", 409)
        return {"status": "retry_wait"}

    monkeypatch.setattr(env, "fail", fail)
    assert cycle(env)["state"] == "recovery_held"
    state = StateStore(config.DATA).read()
    assert state["work"] is not None and state["claims"]
    assert failures == [(0, {"library_id": env.target["library_id"], "library_epoch": 1})]


@pytest.mark.parametrize("stage", ["pipeline", "report"])
@pytest.mark.parametrize("held", [False, True])
def test_mapped_local_failure_retains_instance_and_recovers_without_failing_claim(env, monkeypatch, stage, held):
    from reno import ingest
    install_import(env, monkeypatch)
    active = active_cycles(monkeypatch)
    original_pipeline, original_report = agent.pipeline.run_video, agent.report.run_all

    def bad(*args, **kwargs):
        if held:
            env.recovery = control()
            active[-1].store.observe_recovery(env.recovery)
        raise SyncError("controlled local failure", "SYNC_PROCESSING_FAILED", 409)

    monkeypatch.setattr(agent.pipeline if stage == "pipeline" else agent.report,
                        "run_video" if stage == "pipeline" else "run_all", bad)
    monkeypatch.setattr(env, "fail", lambda *a, **k: pytest.fail("saved mapping work must not be automatically failed"))
    assert cycle(env)["state"] == ("recovery_held" if held else "retry_wait")
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = store.read()
    work = deepcopy(saved["work"])
    assert work["source_mapping_generation"] and saved["claims"]
    assert saved["pending_publication"] is None and not env.publications
    mapping_path = config.DATA / "sync-sources/v2" / (work["source_mapping_generation"] + ".json")
    original_mapping = mapping_path.read_bytes()
    monkeypatch.setattr(agent.pipeline, "run_video", original_pipeline)
    monkeypatch.setattr(agent.report, "run_all", original_report)
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("local retry cannot choose a new resolution"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("local retry cannot download a new instance"))
    monkeypatch.setattr(sync_sources, "_now", lambda: pytest.fail("local retry must keep the original mapping timestamp"))
    if held:
        env.recovery = control("resume_pending", revision=2)
    assert cycle(env)["state"] == "idle"
    if held:
        assert env.publications[-1]["claims"] == []
        assert store.read()["work"]["source_mapping_generation"] == work["source_mapping_generation"]
        assert cycle(env)["state"] == "idle"
    assert env.publications[-1]["claims"][0]["origin"] == json.loads(original_mapping)["origin"]
    assert mapping_path.read_bytes() == original_mapping
    assert len(list(mapping_path.parent.glob("*.json"))) == 1
    assert store.read()["work"] is None and env.claims == []


def test_physical_receiver_restore_lower_control_counters_republishes_and_rescans_reviews(env):
    add_video()
    event = add_event()
    env.recovery = control("resume_pending", generation=4, revision=8)
    assert cycle(env)["state"] == "idle"
    old_publication = deepcopy(env.publications[-1])
    env.epoch = 2
    env.recovery = control("running", generation=0, revision=0,
                           stream={"library_id": env.target["library_id"], "library_epoch": 2})
    env.received_events.clear()
    assert cycle(env)["state"] == "idle"
    new_publication = env.publications[-1]
    assert new_publication["recovery_generation"] == 0
    assert new_publication["recovery_stream"]["library_epoch"] == 2
    assert new_publication["snapshot"]["publication_id"] != old_publication["snapshot"]["publication_id"]
    assert env.received_events == [[event]]
    state = StateStore(config.DATA).read()
    assert state["recovery"]["generation"] == state["applied_recovery_generation"] == 0
    assert state["inbound"]["stream"]["library_epoch"] == 2


def test_higher_receiver_epoch_atomically_resets_namespace_and_ignores_all_old_replies(store):
    initial = store.read()
    stream = {"library_id": initial["binding"]["target_library_id"], "library_epoch": 1}
    old_control = control("running", generation=4, revision=9, stream=stream)
    store.observe_recovery(old_control)
    payload = snapshot()
    metadata = store.write_publication(payload, [], recovery_generation=4, recovery_stream=stream)
    work = {"item_id": 1, "lease_generation": 3, "video_id": "BV1Test", "phase": "downloaded"}
    saved_claim = claim()
    cursor = {"stream": stream, "after": 5, "through": None}
    store.update({"pending_publication": metadata, "applied_recovery_generation": 4,
        "claims": [saved_claim], "work": work, "inbound": cursor, "outbound": cursor, "seeded": True})
    new_control = control("running", generation=0, revision=0, stream={**stream, "library_epoch": 2})
    current = store.observe_recovery(new_control)
    assert current["recovery"] == new_control and current["applied_recovery_generation"] == 0
    assert current["pending_publication"] is None and current["work"] == work and current["claims"] == [saved_claim]
    assert current["inbound"] == current["outbound"] == {"stream": None, "after": 0, "through": None}
    assert not current["seeded"] and list((store.data_dir / "sync-work-history").glob("*.json"))
    stale_lease = {"session_id": initial["session_id"], "lease_token": str(uuid.uuid4()), "generation": 5,
                   "lease_until": "2099-01-01T00:00:00.000Z"}
    assert store.observe_recovery({**old_control, "revision": 100}, lease=stale_lease) == current
    assert store.acknowledge_publication(metadata, recovery_generation=4, recovery_stream=stream,
        version=payload["version"], content_sha256="a" * 64, reports_sha256="b" * 64, preserve_work=False) == current
    assert store.retire_progress(recovery_generation=4, recovery_stream=stream) == current
    assert not store.install_publication(metadata, recovery_generation=4, recovery_stream=stream)
    assert store.apply_review_page({"inbound": cursor, "seeded": True}, claim_changes={"claims": [], "work": None},
                                   recovery=old_control) == current
    assert store.advance_outbound(cursor, recovery_stream=stream) == current
    assert store.read() == current


def test_recovery_observations_ignore_delayed_revision_without_losing_other_progress(store):
    state = store.read()
    stream = {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}
    lease = {"session_id": state["session_id"], "lease_token": str(uuid.uuid4()), "generation": 1,
             "lease_until": "2099-01-01T00:00:00.000Z"}
    latest = control("running", revision=3, stream=stream)
    store.observe_recovery(latest, lease=lease)
    cursor = {"stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1},
              "after": 5, "through": None}
    store.update({"inbound": cursor, "seeded": True})
    stale = {**lease, "lease_until": "2098-01-01T00:00:00.000Z"}
    result = store.observe_recovery(control("resume_pending", revision=2, stream=stream), lease=stale)
    assert result["recovery"] == latest and result["lease"] == lease
    assert result["inbound"] == cursor and result["seeded"]
    for conflicting in (control("held", revision=3, stream=stream), control("held", generation=0, revision=4, stream=stream)):
        with pytest.raises(SyncError) as exc:
            store.observe_recovery(conflicting)
        assert exc.value.code == "SYNC_STATE_RECOVERY_CONFLICT" and store.read() == result


def test_pre_recovery_state_and_outbox_are_read_only_compatible(store):
    old = store.read()
    for key in ("recovery", "applied_recovery_generation", "recovery_attempt", "legacy_pending_stream"):
        old.pop(key)
    state_path = store.data_dir / "sync-state.json"
    state_path.write_text(json.dumps(old), encoding="utf-8")
    state_bytes = state_path.read_bytes()
    value = {"snapshot": snapshot(), "claims": []}
    content = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    publication_id = value["snapshot"]["publication_id"]
    path = store.data_dir / "sync-outbox" / (publication_id + ".json")
    path.parent.mkdir()
    path.write_bytes(content)
    # Legacy state/outbox files were private on POSIX too; manual fixture
    # creation must preserve that contract instead of inheriting umask 022.
    path.chmod(0o600)
    metadata = {"publication_id": publication_id, "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content)}
    assert store.read()["recovery"] is None and state_path.read_bytes() == state_bytes
    assert store.read_publication(metadata) == {**value, "recovery_generation": 0, "recovery_stream": None}
    assert store.find_publication(publication_id) == metadata and path.read_bytes() == content


@pytest.mark.parametrize("has_original_stream", [True, False])
def test_legacy_pending_outbox_never_uses_a_new_pull_as_its_receiver_proof(env, has_original_stream):
    add_video()
    env.fail_publish = True
    cycle(env)
    store = StateStore(config.DATA, profile=storage_admin.profile())
    state = store.read()
    metadata = state["pending_publication"]
    envelope = store.read_publication(metadata)
    legacy = {key: envelope[key] for key in ("snapshot", "claims")}
    content = json.dumps(legacy, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    path = config.DATA / "sync-outbox" / (metadata["publication_id"] + ".json")
    path.write_bytes(content)
    state["pending_publication"] = {**metadata, "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    if not has_original_stream:
        state["inbound"] = {"stream": None, "after": 0, "through": None}
    for key in ("recovery", "applied_recovery_generation", "recovery_attempt", "legacy_pending_stream"):
        state.pop(key)
    state_path = config.DATA / "sync-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()
    assert store.read()["legacy_pending_stream"] == state["inbound"]["stream"]
    assert state_path.read_bytes() == before
    assert cycle(env)["state"] == "idle"
    new_id = env.publications[-1]["snapshot"]["publication_id"]
    assert (new_id == metadata["publication_id"]) is has_original_stream
    assert path.read_bytes() == content
    assert StateStore(config.DATA).read()["pending_publication"] is None


def test_outbox_receiver_namespace_is_immutable(store):
    state = store.read()
    payload = snapshot()
    stream = {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}
    metadata = store.write_publication(payload, [], recovery_generation=1, recovery_stream=stream)
    before = store.read_publication(metadata)
    with pytest.raises(SyncError) as error:
        store.write_publication(payload, [], recovery_generation=1, recovery_stream={**stream, "library_epoch": 2})
    assert error.value.code == "SYNC_OUTBOX_CONFLICT" and store.read_publication(metadata) == before
