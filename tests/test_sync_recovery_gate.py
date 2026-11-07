"""Cloud publication/claim fences on real SQLite, independent of restore CLI."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import threading
import uuid

import pytest

from app import sync_api
from reno import db, review, sync_events, sync_protocol, sync_queue, sync_recovery, sync_transfer, storage_lifecycle
from reno.sync_protocol import SyncError
from tests.test_sync_api import base, http_env, post, pulling, recovery_stream  # noqa: F401
from tests.test_sync_transfer import env, NOW, URL, VIDEO, claim as claim_item, next_payload, publish as publish_raw, receiver_guard  # noqa: F401


def publish(env, payload=None, **kwargs):
    return publish_raw(env, payload, recovery_stream=recovery_stream(env), **kwargs)


def control(con, mode, floor=None, *, generation=1, revision=1, commit=True):
    """Artificial controller precondition; all sync actions below are real."""
    con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,updated_at) "
                "VALUES(1,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET state=excluded.state,generation=excluded.generation,"
                "revision=excluded.revision,source_floor_json=excluded.source_floor_json,updated_at=excluded.updated_at",
                (mode, generation, revision, json.dumps(floor) if floor else None, review.utc_now()))
    if commit:
        con.commit()


def queue_rows(con):
    return [tuple(row) for row in con.execute("SELECT * FROM sync_queue ORDER BY item_id")]


def snapshot_body(env, *, value=None, generation=0, claims=()):
    return {**base(env), "kind": "snapshot", "snapshot": value or env["payload"],
            "claims": list(claims), "recovery_generation": generation, "recovery_stream": recovery_stream(env)}


@pytest.mark.parametrize("mode", ["held", "resume_pending"])
def test_claim_hold_precedes_expiry_cleanup_while_heartbeat_renews_without_attempts(env, mode):
    with receiver_guard(env):
        con = env["con"]
        sync_queue.enqueue(con, [URL] * 3, now=NOW)
        claims = sync_queue.claim(con, env["identity"], env["lease"], limit=2, now=NOW)
        con.execute("UPDATE sync_queue SET lease_until=? WHERE item_id=?",
                    (sync_queue._format_time(NOW - timedelta(seconds=1)), claims[0]["item_id"]))
        control(con, mode, env["payload"]["version"])
        pulse = sync_queue.heartbeat(con, env["identity"], env["lease"]["session_id"], lease=env["lease"],
                                     now=NOW + timedelta(seconds=2))
        assert pulse["renewed_claims"] == 1 and pulse["recovery"]["state"] == mode
        before = queue_rows(con)
        assert sync_queue.claim(con, env["identity"], pulse["lease"], limit=3, now=NOW + timedelta(seconds=2)) == []
        assert queue_rows(con) == before
        assert [tuple(row) for row in con.execute("SELECT status,attempts FROM sync_queue ORDER BY item_id")] == [
            ("claimed", 1), ("claimed", 1), ("queued", 0)]
        con.commit()


@pytest.mark.parametrize("mode", ["held", "resume_pending"])
def test_http_pull_suppresses_owned_claims_but_review_flow_heartbeat_and_public_status_continue(http_env, mode):
    env, con = http_env, http_env["con"]
    assert post(env, "push", snapshot_body(env)).status_code == 200
    sync_queue.enqueue(con, [URL, URL + "?p=2"])
    con.commit()
    claimed = post(env, "pull", pulling(env, 1)).json()["claims"]
    assert len(claimed) == 1
    control(con, mode, env["payload"]["version"])
    before = queue_rows(con)
    page = post(env, "pull", pulling(env, 2))
    assert page.status_code == 200 and page.json()["claims"] == []
    assert page.json()["recovery"] == sync_recovery.state(con)
    assert queue_rows(con) == before
    review.record_decision(env["source"], atom_id="atom", action="confirm", client_id="synthetic-home")
    env["source"].commit()
    events = sync_events.export_events(env["source"])["events"]
    sent = post(env, "push", {**base(env), "kind": "events", "events": events})
    assert sent.status_code == 200 and sent.json()["inserted"] == 1
    assert post(env, "pull", pulling(env)).json()["events"]["events"] == events
    pulse = post(env, "heartbeat", {**base(env), "session_id": env["lease"]["session_id"]})
    assert pulse.status_code == 200 and pulse.json()["renewed_claims"] == 1
    public = env["client"].get("/api/sync-status", headers={"X-Reno-Token": "test-browser-secret"})
    assert public.status_code == 200 and public.json()["recovery"] == {"state": mode, "generation": 1, "revision": 1}
    assert "source_floor" not in public.text and "lease_token" not in public.text
    assert [row[0] for row in con.execute("SELECT attempts FROM sync_queue ORDER BY item_id")] == [1, 0]


def test_generation_zero_keeps_legacy_payload_hash_and_upgrade_lost_reply_replay(env):
    with receiver_guard(env):
        first = publish(env)
        env["con"].commit()
        original = hashlib.sha256(sync_protocol._json_bytes({"snapshot": env["payload"], "claims": []})).hexdigest()
        row = env["con"].execute("SELECT payload_sha256 FROM sync_publication").fetchone()
        assert row[0] == original
        # Schema upgrade deliberately does not backfill historical publication rows.
        env["con"].execute("DELETE FROM sync_publication_fence")
        env["con"].commit()
        assert publish(env) == {**first, "replayed": True}
        assert env["con"].execute("SELECT count(*) FROM sync_publication_fence").fetchone()[0] == 0


def test_held_rejects_old_and_current_generation_before_replay_or_new_checkpoint(env):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        control(env["con"], "held", env["payload"]["version"])
        count = env["con"].execute("SELECT count(*) FROM sync_checkpoint").fetchone()[0]
        for generation, code in ((0, "SYNC_RECOVERY_GENERATION_MISMATCH"), (1, "SYNC_RECOVERY_HELD")):
            with pytest.raises(SyncError) as caught:
                publish(env, recovery_generation=generation)
            assert caught.value.code == code
        assert env["con"].execute("SELECT count(*) FROM sync_checkpoint").fetchone()[0] == count
        assert sync_recovery.state(env["con"])["state"] == "held"


def test_resume_requires_fresh_revision_and_uuid_then_transitions_with_publication_commit(env):
    with receiver_guard(env):
        con = env["con"]
        first = publish(env)
        con.commit()
        control(con, "resume_pending", env["payload"]["version"], revision=2)
        unchanged = deepcopy(env["payload"])
        unchanged["publication_id"] = str(uuid.uuid4())
        with pytest.raises(SyncError) as caught:
            publish(env, unchanged, recovery_generation=1)
        assert caught.value.code == "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED"
        fresh = next_payload(env)
        old_uuid = deepcopy(fresh)
        old_uuid["publication_id"] = first["publication_id"]
        with pytest.raises(SyncError) as caught:
            publish(env, old_uuid, recovery_generation=1)
        assert caught.value.code == "SYNC_RECOVERY_GENERATION_MISMATCH"
        receipt = publish(env, fresh, recovery_generation=1)
        assert sync_recovery.state(con)["state"] == "running" and sync_recovery.state(con)["revision"] == 3
        reader = db.connect(env["target_path"])
        try:
            assert sync_recovery.state(reader)["state"] == "resume_pending"
            con.rollback()
            assert sync_recovery.state(con)["state"] == "resume_pending"
            assert con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (fresh["publication_id"],)).fetchone() is None
            receipt = publish(env, fresh, recovery_generation=1)
            con.commit()
            assert sync_recovery.state(reader)["state"] == "running"
        finally:
            reader.close()
        digest = hashlib.sha256(sync_protocol._json_bytes({"snapshot": fresh, "claims": [], "recovery_generation": 1,
                                                         "recovery_stream": recovery_stream(env)})).hexdigest()
        assert con.execute("SELECT payload_sha256 FROM sync_publication WHERE publication_id=?", (fresh["publication_id"],)).fetchone()[0] == digest
        assert publish(env, fresh, recovery_generation=1) == {**receipt, "replayed": True}
        assert sync_recovery.state(con)["revision"] == 3
        con.rollback()
        # Re-tagging an older publication cannot resurrect its receipt after resume.
        with pytest.raises(SyncError) as caught:
            publish(env, recovery_generation=1)
        assert caught.value.code == "SYNC_RECOVERY_GENERATION_MISMATCH"


def test_resume_omitting_zero_atom_ready_video_is_not_a_full_publication(env):
    with receiver_guard(env):
        con = env["con"]
        publish(env)
        con.execute("INSERT INTO video_asset(video_id,title,status,sync_state) VALUES('retained_empty','旧零原子视频','processed','ready')")
        sync_queue.enqueue(con, [URL], now=NOW)
        control(con, "resume_pending", env["payload"]["version"], revision=2)
        before_queue = queue_rows(con)
        with pytest.raises(SyncError) as caught:
            publish(env, next_payload(env), recovery_generation=1)
        assert caught.value.code == "SYNC_INCOMPLETE_SNAPSHOT"
        assert sync_recovery.state(con)["state"] == "resume_pending" and queue_rows(con) == before_queue
        assert con.execute("SELECT sync_state FROM video_asset WHERE video_id='retained_empty'").fetchone()[0] == "ready"
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1


def test_direct_queue_completion_cannot_reuse_pre_recovery_publication_evidence(env):
    with receiver_guard(env):
        binding = claim_item(env)
        receipt = publish(env, claims=[binding])
        env["con"].commit()
        control(env["con"], "resume_pending", env["payload"]["version"], revision=2)
        publish(env, next_payload(env), recovery_generation=1)
        env["con"].commit()
        before = queue_rows(env["con"])
        for generation in (0, 1):
            with pytest.raises(SyncError) as caught:
                sync_queue.complete(env["con"], env["identity"], env["lease"], binding["claim"],
                    publication_id=receipt["publication_id"], video_id=VIDEO, now=NOW, recovery_generation=generation,
                    recovery_stream=recovery_stream(env))
            assert caught.value.code == "SYNC_RECOVERY_GENERATION_MISMATCH"
        assert queue_rows(env["con"]) == before


@pytest.mark.parametrize("generation", ["0", -1, True, None, 0.0])
def test_http_snapshot_generation_is_a_strict_nonnegative_integer(http_env, generation):
    response = post(http_env, "push", snapshot_body(http_env, generation=generation))
    assert response.status_code == 409 and response.json()["code"] == "SYNC_RECOVERY_INVALID"
    assert http_env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
    assert http_env["con"].execute("SELECT count(*) FROM sync_recovery_control").fetchone()[0] == 0


def test_publication_waiting_for_write_lock_rechecks_new_recovery_generation(env):
    with receiver_guard(env):
        con = env["con"]
        frozen_stream = recovery_stream(env)
        con.execute("BEGIN IMMEDIATE")
        control(con, "held", env["payload"]["version"], commit=False)
        waiting = threading.Event()

        def delayed_publication():
            with storage_lifecycle.shared(env["profile"]):
                receiver = db.connect(env["target_path"])
                receiver.set_trace_callback(lambda sql: waiting.set() if sql == "BEGIN IMMEDIATE" else None)
                try:
                    sync_transfer.publish_snapshot(receiver, env["identity"], env["lease"], env["payload"],
                        media_store=env["store"], now=NOW, recovery_generation=0, recovery_stream=frozen_stream, profile=env["profile"])
                except SyncError as exc:
                    return exc.code
                finally:
                    receiver.close()
            return "unexpected_success"

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(delayed_publication)
            try:
                assert waiting.wait(10)
            finally:
                con.commit()
            assert result.result(timeout=10) == "SYNC_RECOVERY_GENERATION_MISMATCH"
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0


def test_http_snapshot_requires_generation_and_commit_failure_keeps_resume_pending(http_env, monkeypatch):
    env, con = http_env, http_env["con"]
    omitted = snapshot_body(env)
    omitted.pop("recovery_generation")
    assert post(env, "push", omitted).status_code == 400
    assert post(env, "push", snapshot_body(env)).status_code == 200
    control(con, "resume_pending", env["payload"]["version"], revision=2)
    fresh = next_payload(env)
    from tests.test_sync_transfer import upload_binding
    binding = upload_binding(env, fresh, recovery_generation=1)
    assert post(env, "uploads/prepare", {**base(env), "binding": binding, "descriptors": [env["media"]]}).status_code == 200
    assert post(env, "uploads/verify", {**base(env), "intent": {**binding, "descriptor": env["media"]}}).json()["stored"] is True
    def failed_commit(*args, **kwargs):
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(sync_api, "_authorized_commit", failed_commit)
    response = post(env, "push", snapshot_body(env, value=fresh, generation=1))
    assert response.status_code == 503
    assert sync_recovery.state(con)["state"] == "resume_pending" and sync_recovery.state(con)["revision"] == 2
    assert con.execute("SELECT 1 FROM sync_publication_fence WHERE publication_id=?", (fresh["publication_id"],)).fetchone() is None
    assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1


@pytest.mark.parametrize("mode,code", [("held", "SYNC_RECOVERY_HELD"),
                                      ("resume_pending", "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED")])
def test_failure_cannot_consume_held_claim_or_replay_an_old_failure(env, mode, code):
    with receiver_guard(env):
        con = env["con"]
        binding = claim_item(env)
        control(con, mode, env["payload"]["version"])
        before = queue_rows(con)
        for generation, expected in ((0, "SYNC_RECOVERY_GENERATION_MISMATCH"), (1, code)):
            with pytest.raises(SyncError) as caught:
                sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                                now=NOW, recovery_generation=generation, recovery_stream=recovery_stream(env))
            assert caught.value.code == expected and queue_rows(con) == before
        control(con, "running", env["payload"]["version"], revision=3)
        with pytest.raises(SyncError) as caught:
            sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                            now=NOW, recovery_generation=0, recovery_stream=recovery_stream(env))
        assert caught.value.code == "SYNC_RECOVERY_GENERATION_MISMATCH" and queue_rows(con) == before
        result = sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                                 now=NOW, recovery_generation=1, recovery_stream=recovery_stream(env))
        assert result["status"] == "retry_wait"
        con.commit()
        control(con, mode, env["payload"]["version"], revision=4)
        before = queue_rows(con)
        with pytest.raises(SyncError) as caught:
            sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                            now=NOW, recovery_generation=1, recovery_stream=recovery_stream(env))
        assert caught.value.code == code and queue_rows(con) == before


def test_inflight_failure_rechecks_restore_fence_after_obtaining_real_write_lock(env):
    with receiver_guard(env):
        con = env["con"]
        binding, frozen_stream = claim_item(env), recovery_stream(env)
        con.execute("BEGIN IMMEDIATE")
        control(con, "held", env["payload"]["version"], commit=False)
        waiting = threading.Event()

        def delayed_failure():
            receiver = db.connect(env["target_path"])
            receiver.set_trace_callback(lambda sql: waiting.set() if sql == "BEGIN IMMEDIATE" else None)
            try:
                sync_queue.fail(receiver, env["identity"], env["lease"], binding["claim"], "processing_failed",
                                now=NOW, recovery_generation=0, recovery_stream=frozen_stream)
            except SyncError as exc:
                return exc.code
            finally:
                receiver.close()
            return "unexpected_success"

        before = queue_rows(con)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(delayed_failure)
            try:
                assert waiting.wait(10)
            finally:
                con.commit()
            assert future.result(timeout=10) == "SYNC_RECOVERY_GENERATION_MISMATCH"
        assert queue_rows(con) == before


def test_receiver_epoch_change_rejects_old_failure_even_when_generation_returns_to_zero(env):
    with receiver_guard(env):
        con = env["con"]
        binding, frozen_stream = claim_item(env), recovery_stream(env)
        # Physical restore must advance receiver epoch; its old control counters
        # may legitimately be absent/zero. They cannot authorize in-flight work.
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
        con.commit()
        before = queue_rows(con)
        assert sync_recovery.state(con)["generation"] == 0
        with pytest.raises(SyncError) as caught:
            sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                            now=NOW, recovery_generation=0, recovery_stream=frozen_stream)
        assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH" and queue_rows(con) == before
        assert sync_queue.fail(con, env["identity"], env["lease"], binding["claim"], "processing_failed",
                               now=NOW, recovery_generation=0, recovery_stream=recovery_stream(env))["status"] == "retry_wait"


def test_receiver_epoch_change_rejects_retagged_old_receipt_and_direct_queue_completion(env):
    with receiver_guard(env):
        con = env["con"]
        binding, frozen_stream = claim_item(env), recovery_stream(env)
        receipt = publish(env, claims=[binding])
        con.commit()
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
        con.commit()
        before = queue_rows(con)
        # Check frozen requests, malicious/current retagging, and legacy Python
        # default callers. Receiver epoch on the stored receipt fences all three.
        for stream in (frozen_stream, recovery_stream(env), None):
            with pytest.raises(SyncError) as caught:
                publish_raw(env, claims=[binding], recovery_stream=stream)
            assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"
            with pytest.raises(SyncError) as caught:
                sync_queue.complete(con, env["identity"], env["lease"], binding["claim"],
                    publication_id=receipt["publication_id"], video_id=VIDEO, now=NOW, recovery_stream=stream)
            assert caught.value.code == "SYNC_RECOVERY_STREAM_MISMATCH"
        assert queue_rows(con) == before
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM sync_checkpoint").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["snapshot", "fail"])
@pytest.mark.parametrize("missing", ["recovery_stream", "recovery_generation"])
def test_http_requires_both_frozen_recovery_fields_even_in_generation_zero(http_env, kind, missing):
    env, con = http_env, http_env["con"]
    binding = claim_item(env)
    body = (snapshot_body(env, claims=[binding]) if kind == "snapshot" else
            {**base(env), "kind": "fail", "claim": binding["claim"], "error_class": "processing_failed",
             "recovery_generation": 0, "recovery_stream": recovery_stream(env)})
    body.pop(missing)
    before = queue_rows(con)
    assert post(env, "push", body).status_code == 400
    assert queue_rows(con) == before and con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["snapshot", "fail"])
@pytest.mark.parametrize("changed", ["epoch", "library_id", "integer_type"])
def test_http_recovery_stream_must_match_the_actual_receiver(http_env, kind, changed):
    env, con = http_env, http_env["con"]
    binding = claim_item(env)
    body = (snapshot_body(env, claims=[binding]) if kind == "snapshot" else
            {**base(env), "kind": "fail", "claim": binding["claim"], "error_class": "processing_failed",
             "recovery_generation": 0, "recovery_stream": recovery_stream(env)})
    if changed == "epoch":
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
        con.commit()
    elif changed == "library_id":
        body["recovery_stream"]["library_id"] = str(uuid.uuid4())
    else:
        body["recovery_stream"]["library_epoch"] = True
    before = queue_rows(con)
    response = post(env, "push", body)
    assert response.status_code == 409 and response.json()["code"] == "SYNC_RECOVERY_STREAM_MISMATCH"
    assert queue_rows(con) == before and con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0


@pytest.mark.parametrize("generation", ["0", -1, True, None, 0.0])
def test_http_failure_generation_is_also_a_strict_nonnegative_integer(http_env, generation):
    env, con = http_env, http_env["con"]
    binding = claim_item(env)
    before = queue_rows(con)
    response = post(env, "push", {**base(env), "kind": "fail", "claim": binding["claim"],
        "error_class": "processing_failed", "recovery_generation": generation, "recovery_stream": recovery_stream(env)})
    assert response.status_code == 409 and response.json()["code"] == "SYNC_RECOVERY_INVALID"
    assert queue_rows(con) == before


def test_http_failure_remains_transactional_and_cannot_cross_restore_or_resume(http_env, monkeypatch):
    env, con = http_env, http_env["con"]
    sync_queue.enqueue(con, [URL])
    con.commit()
    binding = post(env, "pull", pulling(env, 1)).json()["claims"][0]
    body = {**base(env), "kind": "fail", "claim": binding["claim"], "error_class": "processing_failed",
            "recovery_generation": 0, "recovery_stream": recovery_stream(env)}
    before = queue_rows(con)
    for mode, revision in (("held", 1), ("resume_pending", 2), ("running", 3)):
        control(con, mode, env["payload"]["version"], revision=revision)
        response = post(env, "push", body)
        assert response.status_code == 409 and response.json()["code"] == "SYNC_RECOVERY_GENERATION_MISMATCH"
        assert queue_rows(con) == before
    body["recovery_generation"] = 1
    def failed_commit(*args, **kwargs):
        raise OSError("synthetic failure receipt commit error")
    monkeypatch.setattr(sync_api, "_authorized_commit", failed_commit)
    assert post(env, "push", body).status_code == 503
    assert queue_rows(con) == before
