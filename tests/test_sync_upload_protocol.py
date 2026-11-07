"""Real HTTP upload preparation and publication fences on artificial data."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from reno import db, storage_admin, storage_lifecycle, sync_transfer, sync_upload_pins
from tests.test_sync_api import http_env, base, post, media_headers, recovery_stream  # noqa: F401
from tests.test_sync_transfer import env, CONTENT, upload_binding  # noqa: F401


def fresh(env, content=b"new artificial upload"):
    descriptor = {**env["media"], "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content)}
    import base64
    intent = json.loads(base64.urlsafe_b64decode(media_headers(env, descriptor)["X-Sync-Media"]))["intent"]
    binding = {k: intent[k] for k in sync_upload_pins.BINDING_FIELDS}
    return descriptor, binding, content


def prepare(env, binding, descriptors):
    return post(env, "uploads/prepare", {**base(env), "binding": binding, "descriptors": descriptors})


def push(env, payload=None):
    return post(env, "push", {**base(env), "kind": "snapshot", "snapshot": payload or env["payload"], "claims": [],
                              "recovery_generation": 0, "recovery_stream": recovery_stream(env)})


def test_prepare_covers_cache_hits_and_repeated_manifest_cannot_expand(http_env):
    env = http_env
    result = prepare(env, upload_binding(env), [env["media"]])
    assert result.status_code == 200 and result.json()["media"][0]["state"] == "reserved"
    verified = post(env, "uploads/verify", {**base(env), "intent": {**upload_binding(env), "descriptor": env["media"]}})
    assert verified.status_code == 200 and verified.json()["stored"] is True
    changed = {**env["media"], "video_id": "UnseenVideo"}
    response = prepare(env, upload_binding(env), [env["media"], changed])
    assert response.status_code == 409 and response.json()["code"] == "SYNC_UPLOAD_SET_MISMATCH"
    assert len(sync_upload_pins.held_descriptors(env["con"])) == 1


def test_empty_prepare_freezes_a_real_zero_media_sentinel(http_env):
    env = http_env
    binding = {**upload_binding(env), "publication_id": str(uuid.uuid4())}
    assert prepare(env, binding, []).json()["media"] == []
    rejected = prepare(env, binding, [env["media"]])
    assert rejected.status_code == 409 and rejected.json()["code"] == "SYNC_UPLOAD_SET_MISMATCH"


def test_individual_media_cannot_expand_frozen_set(http_env, monkeypatch):
    from reno.sync_media import MediaStore
    env = http_env
    descriptor, _, content = fresh(env)
    called = []
    monkeypatch.setattr(MediaStore, "receive", lambda *a, **k: called.append(True))
    response = env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content)
    assert response.status_code == 409 and response.json()["code"] == "SYNC_UPLOAD_NOT_RESERVED"
    assert not called


def test_completed_prepare_and_payload_replay_survive_retired_media(http_env):
    env = http_env
    first = push(env)
    assert first.status_code == 200, first.text
    env["store"].verified_path(env["media"]).unlink()
    prepared = prepare(env, upload_binding(env), [env["media"]])
    assert prepared.status_code == 200 and prepared.json()["state"] == "published" and prepared.json()["media"] == []
    again = push(env)
    assert again.status_code == 200 and again.json()["replayed"] is True
    altered = deepcopy(env["payload"])
    altered["videos"][0]["tables"]["video_asset"][0]["title"] = "changed frozen payload"
    conflict = push(env, altered)
    assert conflict.status_code == 409 and conflict.json()["code"] == "SYNC_PUBLICATION_CONFLICT"


def test_legacy_generation_zero_receipt_without_pins_or_fence_replays_after_media_retirement(http_env):
    env = http_env
    assert push(env).status_code == 200
    con = env["con"]
    con.execute("DELETE FROM sync_upload_pin WHERE kind='media'")
    con.execute("DELETE FROM sync_upload_pin WHERE kind='publication'")
    con.execute("DELETE FROM sync_publication_fence")
    con.commit()
    env["store"].verified_path(env["media"]).unlink()
    response = prepare(env, upload_binding(env), [env["media"]])
    assert response.status_code == 200 and response.json()["state"] == "published"
    assert response.json()["media"] == []
    replay = push(env)
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0


def test_media_network_iteration_has_no_sql_writer_and_guard_covers_both_commits(http_env, monkeypatch):
    from app import sync_api
    env = http_env
    descriptor, binding, content = fresh(env)
    assert prepare(env, binding, [descriptor]).status_code == 200
    original_receive, original_commit = sync_transfer.receive_media, sync_api._authorized_commit
    observations = []

    def receive(con, source, lease, descriptor, chunks, **kwargs):
        def checked():
            for chunk in chunks:
                assert not con.in_transaction
                storage_lifecycle.require_guard(kwargs["profile"])
                other = db.connect(env["target_path"])
                try:
                    other.execute("BEGIN IMMEDIATE")
                    row = other.execute("SELECT state FROM sync_upload_pin WHERE pin_id=?", (sync_upload_pins.intent_id(kwargs["intent"]),)).fetchone()
                    assert row[0] == "reserved"
                    observations.append("network-no-writer")
                finally:
                    other.rollback()
                    other.close()
                yield chunk
        return original_receive(con, source, lease, descriptor, checked(), **kwargs)

    def commit(con, *args, **kwargs):
        storage_lifecycle.require_guard(storage_admin.profile())
        observations.append("guarded-commit")
        return original_commit(con, *args, **kwargs)

    monkeypatch.setattr(sync_transfer, "receive_media", receive)
    monkeypatch.setattr(sync_api, "_authorized_commit", commit)
    response = env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content)
    assert response.status_code == 200, response.text
    assert observations[0] == observations[-1] == "guarded-commit"
    assert "network-no-writer" in observations
    assert post(env, "uploads/verify", {**base(env), "intent": {**binding, "descriptor": descriptor}}).json()["stored"] is True


def test_prepare_never_scans_media_and_verify_is_one_bounded_file(http_env, monkeypatch):
    from reno import storage_artifacts
    from app import sync_api
    env = http_env
    observed = []
    original = storage_artifacts.verify

    def checked(con, artifact_id, **kwargs):
        observed.append(artifact_id)
        return original(con, artifact_id, **kwargs)

    monkeypatch.setattr(storage_artifacts, "verify", checked)
    assert prepare(env, upload_binding(env), [env["media"]]).status_code == 200
    assert observed == []
    body = {**base(env), "intent": {**upload_binding(env), "descriptor": env["media"]}}
    assert post(env, "uploads/verify", body).json()["stored"] is True
    assert len(observed) == 1
    monkeypatch.setattr(sync_api, "VERIFY_TOTAL_SECONDS", 0)
    timeout = post(env, "uploads/verify", body)
    assert timeout.status_code == 408 and timeout.json()["code"] == "SYNC_UPLOAD_TIMEOUT"


def test_failed_final_commit_retains_reserved_intent_and_retry_can_finish(http_env, monkeypatch):
    from app import sync_api
    env = http_env
    descriptor, binding, content = fresh(env)
    assert prepare(env, binding, [descriptor]).status_code == 200
    pin_id = sync_upload_pins.intent_id({**binding, "descriptor": descriptor})
    original = sync_api._authorized_commit

    def failing(con, *args, **kwargs):
        if sync_upload_pins.get(con, pin_id)["state"] == "stored":
            raise sqlite3.OperationalError("artificial commit failure")
        return original(con, *args, **kwargs)

    monkeypatch.setattr(sync_api, "_authorized_commit", failing)
    response = env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content)
    assert response.status_code == 503
    assert sync_upload_pins.get(env["con"], pin_id)["state"] == "reserved"
    assert env["store"].verified_path(descriptor).read_bytes() == content
    monkeypatch.setattr(sync_api, "_authorized_commit", original)
    assert env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content).status_code == 200
    assert sync_upload_pins.get(env["con"], pin_id)["state"] == "stored"


def test_explicit_abandon_tombstone_rejects_late_prepare_and_media(http_env):
    env = http_env
    descriptor, binding, content = fresh(env)
    body = {**base(env), "binding": binding, "operation_id": str(uuid.uuid4())}
    first = post(env, "uploads/abandon", body)
    assert first.status_code == 200 and first.json()["state"] == "abandoned"
    assert post(env, "uploads/abandon", body).json() == first.json()
    assert prepare(env, binding, [descriptor]).json()["code"] == "SYNC_UPLOAD_ABANDONED"
    assert env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content).json()["code"] == "SYNC_UPLOAD_ABANDONED"


def test_abandon_arriving_during_body_is_rechecked_before_stored_commit(http_env, monkeypatch):
    env = http_env
    descriptor, binding, content = fresh(env)
    assert prepare(env, binding, [descriptor]).status_code == 200
    original = sync_transfer.receive_media

    def receive(con, source, lease, descriptor, chunks, **kwargs):
        def abandon_then_read():
            other = db.connect(env["target_path"])
            try:
                sync_transfer.abandon_uploads(other, source, lease, binding=binding, operation_id=str(uuid.uuid4()), profile=kwargs["profile"])
                other.commit()
            finally:
                other.close()
            yield from chunks
        return original(con, source, lease, descriptor, abandon_then_read(), **kwargs)

    monkeypatch.setattr(sync_transfer, "receive_media", receive)
    response = env["client"].post("/api/sync/media", headers=media_headers(env, descriptor), content=content)
    assert response.status_code == 409 and response.json()["code"] == "SYNC_UPLOAD_ABANDONED"
    row = sync_upload_pins.get(env["con"], sync_upload_pins.intent_id({**binding, "descriptor": descriptor}))
    assert row["state"] == "abandoned" and row["artifact_id"] is None


def test_upload_binding_cannot_impersonate_another_source_node(http_env):
    env = http_env
    binding = {**upload_binding(env), "source_node_id": str(uuid.uuid4())}
    response = prepare(env, binding, [env["media"]])
    assert response.status_code == 409 and response.json()["code"] == "SYNC_PEER_MISMATCH"


def test_old_wire_handshake_cannot_silently_upload_without_intents(http_env):
    env = http_env
    source = deepcopy(env["identity"])
    source["handshake"]["protocol_version"] = 1
    response = post(env, "uploads/prepare", {"source": source, "lease": env["lease"], "binding": upload_binding(env), "descriptors": [env["media"]]})
    assert response.status_code == 409 and response.json()["code"] == "INCOMPATIBLE_SYNC_SCHEMA"


@pytest.mark.parametrize("operation", ["verify", "upload"])
@pytest.mark.parametrize("concurrent", ["heartbeat", "abandon"])
def test_sha_phase_releases_sql_writer_and_rechecks_concurrent_terminal_state(http_env, monkeypatch, operation, concurrent):
    from reno import storage_artifacts, sync_queue
    env = http_env
    if operation == "verify":
        descriptor, binding, content = env["media"], upload_binding(env), CONTENT
        helper = "_verify_registered"
    else:
        descriptor, binding, content = fresh(env)
        assert prepare(env, binding, [descriptor]).status_code == 200
        helper = "_inspect_for_registration"
    intent = {**binding, "descriptor": descriptor}
    original = getattr(storage_artifacts, helper)
    completed = []

    def competing_writer(profile):
        with storage_lifecycle.shared(profile):
            other = db.connect(env["target_path"])
            try:
                other.execute("PRAGMA busy_timeout=100")
                sync_queue.heartbeat(other, env["identity"], env["lease"]["session_id"], lease=env["lease"])
                other.commit()
                if concurrent == "abandon":
                    sync_transfer.abandon_uploads(other, env["identity"], env["lease"], binding=binding,
                                                  operation_id=str(uuid.uuid4()), profile=profile)
                    other.commit()
                completed.append(concurrent)
            finally:
                other.close()

    def instrument(con, *args, **kwargs):
        check, calls = kwargs.get("check_cancelled"), 0
        def barrier():
            nonlocal calls
            if check:
                check()
            assert not con.in_transaction
            calls += 1
            if calls == 2:  # The actual SHA loop has consumed a file chunk.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(competing_writer, kwargs["profile"]).result(timeout=3)
        kwargs["check_cancelled"] = barrier
        return original(con, *args, **kwargs)

    monkeypatch.setattr(storage_artifacts, helper, instrument)
    response = post(env, "uploads/verify", {**base(env), "intent": intent}) if operation == "verify" else env["client"].post(
        "/api/sync/media", headers=media_headers(env, descriptor), content=content)
    assert completed == [concurrent]
    row = sync_upload_pins.get(env["con"], sync_upload_pins.intent_id(intent))
    if concurrent == "abandon":
        assert response.status_code == 409 and response.json()["code"] == "SYNC_UPLOAD_ABANDONED"
        assert row["state"] == "abandoned"
    else:
        assert response.status_code == 200, response.text
        assert row["state"] == "stored"


def test_new_publication_reuses_live_media_sha_without_extra_pin_scan(http_env, monkeypatch):
    from reno import storage_artifacts, sync_media
    env = http_env
    original, verify_stream = storage_artifacts._inspect, sync_media._verify_stream
    scans = []
    def forbid_extra(**kwargs):
        assert kwargs["namespace"] != "sync_media", "P05 completion must reuse the actual live media proof"
        return original(**kwargs)
    def count(stream, descriptor, **kwargs):
        scans.append(descriptor)
        return verify_stream(stream, descriptor, **kwargs)
    monkeypatch.setattr(storage_artifacts, "_inspect", forbid_extra)
    monkeypatch.setattr(sync_media, "_verify_stream", count)
    result = push(env)
    assert result.status_code == 200, result.text
    assert scans == [env["media"], env["media"]]  # Existing hold entry and exit only.
