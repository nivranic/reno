"""Exact historical publication proofs using TEMP SQLite and in-process HTTP."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import uuid

from fastapi.testclient import TestClient
import pytest

from reno import config, db, sync_protocol, sync_queue, sync_recovery, sync_transfer
from reno import storage_lifecycle, sync_reports
from reno.sync_protocol import SyncError
from tests.test_sync_transfer import env, NOW, VIDEO, publish, receiver_guard, upload_binding, claim  # noqa: F401


@pytest.fixture()
def published(env):
    with receiver_guard(env):
        env["binding"] = upload_binding(env)
        env["original_receipt"] = publish(env)
        env["con"].commit()
    env["payload_sha256"] = sync_transfer.publication_payload_sha256(env["payload"], [])
    return env


def lookup(env, **overrides):
    arguments = {"binding": env["binding"], "payload_sha256": env["payload_sha256"],
                 "profile": env["profile"], "now": NOW}
    arguments.update(overrides)
    return sync_transfer.publication_receipt(env["con"], env["identity"], env["lease"], **arguments)


def sql_image(con):
    names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: [tuple(row) for row in con.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
            for name in names}


def fence(env, *, generation, state="held", epoch=None):
    con = env["con"]
    con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,updated_at) "
                "VALUES(1,?,?,?,NULL,?) ON CONFLICT(slot) DO UPDATE SET "
                "state=excluded.state,generation=excluded.generation,revision=excluded.revision",
                (state, generation, generation, "2026-10-01T00:00:00.000Z"))
    if epoch is not None:
        con.execute("UPDATE meta SET value=? WHERE key='library_epoch'", (str(epoch),))
    con.commit()


def test_generation_zero_hash_is_original_normalized_snapshot_and_claims(env):
    value = {"video_id": VIDEO, "claim": {"item_id": 1, "lease_token": str(uuid.uuid4()), "lease_generation": 2}}
    expected = hashlib.sha256(sync_protocol._json_bytes({"snapshot": env["payload"], "claims": [value]})).hexdigest()
    assert sync_transfer.publication_payload_sha256(env["payload"], [value]) == expected
    stream = upload_binding(env)["recovery_stream"]
    assert sync_transfer.publication_payload_sha256(env["payload"], [value], recovery_stream=stream) == expected
    assert sync_transfer.publication_payload_sha256(env["payload"], [value],
        recovery_stream={**stream, "library_epoch": stream["library_epoch"] + 1}) == expected
    assert sync_transfer.publication_payload_sha256(env["payload"], None) == sync_transfer.publication_payload_sha256(env["payload"], [])


def test_nonzero_hash_binds_generation_stream_and_normalizes_claim_order(env):
    claims = [{"video_id": VIDEO, "claim": {"item_id": item, "lease_token": str(uuid.uuid4()), "lease_generation": 1}}
              for item in (2, 1)]
    stream = upload_binding(env)["recovery_stream"]
    original = sync_transfer.publication_payload_sha256(env["payload"], claims, recovery_generation=1, recovery_stream=stream)
    expected = hashlib.sha256(sync_protocol._json_bytes({"snapshot": env["payload"], "claims": list(reversed(claims)),
        "recovery_generation": 1, "recovery_stream": stream})).hexdigest()
    assert original == expected
    assert sync_transfer.publication_payload_sha256(env["payload"], list(reversed(claims)),
        recovery_generation=1, recovery_stream=stream) == original
    assert sync_transfer.publication_payload_sha256(env["payload"], claims,
        recovery_generation=2, recovery_stream=stream) != original
    assert sync_transfer.publication_payload_sha256(env["payload"], claims, recovery_generation=1,
        recovery_stream={**stream, "library_epoch": stream["library_epoch"] + 1}) != original


@pytest.mark.parametrize("generation,stream", [(True, None), (-1, None), (2**63, None), (1, None),
    (1, {"library_id": str(uuid.uuid4()), "library_epoch": True}),
    (0, {"library_id": str(uuid.uuid4()), "library_epoch": 1, "extra": 0})])
def test_hash_rejects_invalid_fences(env, generation, stream):
    with pytest.raises(SyncError):
        sync_transfer.publication_payload_sha256(env["payload"], [], recovery_generation=generation, recovery_stream=stream)


def test_receipt_is_exact_closed_read_only_and_never_opens_media_or_reports(published, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Receipt query must not access media or reports")

    monkeypatch.setattr(published["store"], "hold_verified", forbidden)
    monkeypatch.setattr(sync_reports, "prepare_files", forbidden)
    # Proof must still work when the historical media is no longer present.
    (published["store"].root / published["store"].storage_key(published["media"])).unlink()
    with receiver_guard(published):
        con = published["con"]
        before, changes = sql_image(con), con.total_changes
        con.execute("PRAGMA query_only=ON")
        result = lookup(published)
        assert result == {"binding": published["binding"], "payload_sha256": published["payload_sha256"],
            "state": "published", "receipt": {**published["original_receipt"], "replayed": True}}
        assert set(result["receipt"]) == {"publication_id", "source_version", "local_version", "committed_at", "replayed"}
        assert lookup(published) == result
        assert sql_image(con) == before and con.total_changes == changes and not con.in_transaction


def test_receipt_preserves_callers_existing_transaction(published):
    with receiver_guard(published):
        con = published["con"]
        con.execute("BEGIN")
        assert lookup(published)["state"] == "published"
        assert con.in_transaction
        con.rollback()


def test_failed_receipt_keeps_original_error_if_read_transaction_cleanup_fails(published):
    class FailingRollback:
        def __getattr__(self, name):
            return getattr(published["con"], name)

        def rollback(self):
            raise OSError("synthetic cleanup failure")

    with receiver_guard(published):
        with pytest.raises(SyncError) as error:
            sync_transfer.publication_receipt(FailingRollback(), published["identity"], published["lease"],
                binding=published["binding"], payload_sha256="0" * 64, profile=published["profile"], now=NOW)
        assert error.value.code == "SYNC_PUBLICATION_CONFLICT"


@pytest.mark.parametrize("state", ["held", "resume_pending", "running"])
def test_known_original_receipt_is_queryable_at_later_epoch_and_generation(published, state):
    original = deepcopy(published["binding"])
    fence(published, generation=3, state=state, epoch=original["recovery_stream"]["library_epoch"] + 2)
    with receiver_guard(published):
        with pytest.raises(SyncError):
            sync_recovery.assert_publish_allowed(published["con"], 0, original["source_version"], original["recovery_stream"])
        before = sql_image(published["con"])
        assert lookup(published)["binding"] == original
        assert sql_image(published["con"]) == before


def test_legacy_generation_zero_has_independent_stream_proof_without_parent_or_fence(published):
    con = published["con"]
    con.execute("DELETE FROM sync_upload_pin")
    con.execute("DELETE FROM sync_publication_fence")
    con.commit()
    original = deepcopy(published["binding"])
    fence(published, generation=4, epoch=original["recovery_stream"]["library_epoch"] + 1)
    with receiver_guard(published):
        result = lookup(published)
        assert result["binding"] == original
        assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM sync_publication_fence").fetchone()[0] == 0


def test_nonzero_historical_receipt_uses_its_original_persisted_fence(env):
    fence(env, generation=2, state="running")
    with receiver_guard(env):
        env["binding"] = upload_binding(env, recovery_generation=2)
        env["original_receipt"] = publish(env, recovery_generation=2)
        env["con"].commit()
    env["payload_sha256"] = sync_transfer.publication_payload_sha256(env["payload"], [], recovery_generation=2,
        recovery_stream=env["binding"]["recovery_stream"])
    fence(env, generation=4, epoch=env["binding"]["recovery_stream"]["library_epoch"] + 1)
    with receiver_guard(env):
        assert lookup(env)["receipt"] == {**env["original_receipt"], "replayed": True}


@pytest.mark.parametrize("change", ["source_library", "source_node", "source_epoch", "source_revision",
    "target_library", "target_epoch", "generation", "publication"])
def test_receipt_cannot_change_any_original_binding(published, change):
    value = deepcopy(published["binding"])
    if change == "source_library":
        value["source_version"]["library_id"] = str(uuid.uuid4())
    elif change == "source_node":
        value["source_node_id"] = str(uuid.uuid4())
    elif change in ("source_epoch", "source_revision"):
        value["source_version"]["library_epoch" if change == "source_epoch" else "kb_revision"] += 1
    elif change == "target_library":
        value["recovery_stream"]["library_id"] = str(uuid.uuid4())
    elif change == "target_epoch":
        value["recovery_stream"]["library_epoch"] += 1
    elif change == "generation":
        value["recovery_generation"] += 1
    else:
        value["publication_id"] = str(uuid.uuid4())
    with receiver_guard(published):
        before = sql_image(published["con"])
        with pytest.raises(SyncError):
            lookup(published, binding=value)
        assert sql_image(published["con"]) == before


@pytest.mark.parametrize("kind", ["wrong", "outbox", "knowledge", "media", "changed_claims"])
def test_only_original_network_payload_hash_can_prove_a_receipt(published, kind):
    con = published["con"]
    if kind == "knowledge":
        digest = con.execute("SELECT knowledge_sha256 FROM sync_publication").fetchone()[0]
    elif kind == "media":
        digest = hashlib.sha256(sync_protocol._json_bytes([published["media"]])).hexdigest()
    elif kind == "outbox":
        digest = hashlib.sha256(sync_protocol._json_bytes({"snapshot": published["payload"], "claims": [], "format_version": 1})).hexdigest()
    elif kind == "changed_claims":
        digest = sync_transfer.publication_payload_sha256(published["payload"], [{"video_id": VIDEO,
            "claim": {"item_id": 1, "lease_token": str(uuid.uuid4()), "lease_generation": 1}}])
    else:
        digest = "0" * 64
    assert digest != published["payload_sha256"]
    with receiver_guard(published):
        with pytest.raises(SyncError) as error:
            lookup(published, payload_sha256=digest)
        assert error.value.code == "SYNC_PUBLICATION_CONFLICT"


@pytest.mark.parametrize("invalid", [None, 1, True, "a" * 63, "A" * 64, "a" * 65])
def test_receipt_hash_is_closed_and_canonical(published, invalid):
    with receiver_guard(published):
        with pytest.raises(SyncError) as error:
            lookup(published, payload_sha256=invalid)
        assert error.value.status == 400


@pytest.mark.parametrize("damage", ["no_local", "bad_json", "extra_local", "duplicate_local", "large_local", "bad_time",
    "parent_reserved", "parent_abandoned", "parent_bad_digest", "parent_bad_stream", "future_epoch", "future_generation"])
def test_missing_corrupt_or_future_original_proof_is_not_repaired(published, damage):
    con = published["con"]
    value = deepcopy(published["binding"])
    local = deepcopy(published["original_receipt"]["local_version"])
    if damage in {"no_local", "bad_json", "extra_local", "duplicate_local", "large_local"}:
        bad = {"no_local": "{}", "bad_json": "{", "extra_local": json.dumps({**local, "extra": 1}),
               "duplicate_local": json.dumps(local)[:-1] + ',"library_epoch":1}', "large_local": " " * 1025 + json.dumps(local)}[damage]
        con.execute("UPDATE sync_publication SET local_version_json=?", (bad,))
    elif damage == "bad_time":
        con.execute("UPDATE sync_publication SET committed_at='not-a-time'")
    elif damage.startswith("parent_"):
        if damage in {"parent_reserved", "parent_abandoned"}:
            con.execute("UPDATE sync_upload_pin SET state=?", (damage.removeprefix("parent_"),))
        elif damage == "parent_bad_digest":
            con.execute("UPDATE sync_upload_pin SET intent_sha256=? WHERE kind='publication'", ("0" * 64,))
        else:
            con.execute("UPDATE sync_upload_pin SET target_library_epoch=target_library_epoch+1 WHERE kind='publication'")
    else:
        con.execute("DELETE FROM sync_upload_pin")
        if damage == "future_epoch":
            local["library_epoch"] += 1
            value["recovery_stream"]["library_epoch"] += 1
            con.execute("UPDATE sync_publication SET local_version_json=?", (json.dumps(local),))
        else:
            value["recovery_generation"] = 1
            con.execute("UPDATE sync_publication_fence SET generation=1")
    con.commit()
    with receiver_guard(published):
        before = sql_image(con)
        with pytest.raises(SyncError):
            lookup(published, binding=value)
        assert sql_image(con) == before


def test_published_error_does_not_serve_as_receipt_proof(published):
    with receiver_guard(published):
        with pytest.raises(SyncError) as error:
            sync_transfer.abandon_uploads(published["con"], published["identity"], published["lease"],
                binding=published["binding"], operation_id=str(uuid.uuid4()), profile=published["profile"], now=NOW)
        assert error.value.code == "SYNC_UPLOAD_PUBLISHED"
        result = lookup(published)
        assert result["state"] == "published" and result["receipt"]["publication_id"] == published["binding"]["publication_id"]
        assert not published["con"].execute("SELECT 1 FROM sync_upload_pin WHERE state='abandoned'").fetchall()


def test_abandoned_only_intent_never_returns_a_fake_publication_receipt(env):
    env["binding"] = upload_binding(env)
    env["payload_sha256"] = sync_transfer.publication_payload_sha256(env["payload"], [])
    with receiver_guard(env):
        sync_transfer.prepare_uploads(env["con"], env["identity"], env["lease"], binding=env["binding"],
            descriptors=[env["media"]], profile=env["profile"], now=NOW)
        sync_transfer.abandon_uploads(env["con"], env["identity"], env["lease"], binding=env["binding"],
            operation_id=str(uuid.uuid4()), profile=env["profile"], now=NOW)
        env["con"].commit()
        before = sql_image(env["con"])
        with pytest.raises(SyncError) as error:
            lookup(env)
        assert error.value.code == "SYNC_PUBLICATION_REQUIRED" and error.value.status == 404
        assert sql_image(env["con"]) == before


def test_original_claim_hash_remains_provable_after_old_claim_lease_expires(env):
    with receiver_guard(env):
        original_claim = claim(env)
        env["binding"] = upload_binding(env)
        original = publish(env, claims=[original_claim])
        env["con"].commit()
        env["payload_sha256"] = sync_transfer.publication_payload_sha256(env["payload"], [original_claim])
        later = NOW + timedelta(hours=2)
        env["lease"] = sync_queue.heartbeat(env["con"], env["identity"], str(uuid.uuid4()), now=later)["lease"]
        env["con"].commit()
        session, _ = sync_queue._session(env["con"], env["identity"], env["lease"], later)
        with pytest.raises(SyncError):
            sync_queue._claim_record(env["con"], session, original_claim["claim"], later)
        env["con"].execute("PRAGMA query_only=ON")
        assert lookup(env, now=later)["receipt"] == {**original, "replayed": True}


@pytest.mark.parametrize("problem", ["wrong_lease", "expired", "different_source", "malformed_source", "different_receiver_node"])
def test_receipt_requires_a_current_matching_source_session(published, problem):
    source, lease = deepcopy(published["identity"]), deepcopy(published["lease"])
    now = NOW
    if problem == "wrong_lease":
        lease["lease_token"] = str(uuid.uuid4())
    elif problem == "expired":
        now += timedelta(hours=2)
    elif problem == "different_source":
        source["node_id"] = str(uuid.uuid4())
    elif problem == "malformed_source":
        source.pop("node_id")
    else:
        published["con"].execute("UPDATE sync_pair SET target_node_id=?", (str(uuid.uuid4()),))
        published["con"].commit()
    with receiver_guard(published):
        with pytest.raises(SyncError):
            sync_transfer.publication_receipt(published["con"], source, lease, binding=published["binding"],
                payload_sha256=published["payload_sha256"], profile=published["profile"], now=now)


def test_receipt_requires_an_explicit_correct_profile_and_preexisting_guard(published):
    with pytest.raises(storage_lifecycle.StorageLifecycleError):
        lookup(published)
    with receiver_guard(published):
        with pytest.raises(storage_lifecycle.StorageLifecycleError) as error:
            lookup(published, profile=None)
        assert error.value.code == "STORAGE_PROFILE_REQUIRED"
        wrong = storage_lifecycle.StorageProfile(published["profile"].database.with_name("wrong.db"),
            published["profile"].media, published["profile"].reports, published["profile"].checkpoints)
        with pytest.raises(storage_lifecycle.StorageLifecycleError):
            lookup(published, profile=wrong)


@pytest.fixture()
def receipt_http(published):
    from app.main import create_app
    config.set_local({"node_role": "cloud", "sync_token": "test-sync-secret", "auth_token": "test-browser-secret"})
    published["lease"] = sync_queue.heartbeat(published["con"], published["identity"], str(uuid.uuid4()))["lease"]
    published["con"].commit()
    with TestClient(create_app(), base_url="https://127.0.0.1") as client:
        published["client"] = client
        yield published


def http_body(env):
    return {"source": env["identity"], "lease": env["lease"], "binding": env["binding"], "payload_sha256": env["payload_sha256"]}


def test_http_receipt_held_is_exact_and_commits_no_mutation(receipt_http, monkeypatch):
    from app import sync_api
    env = receipt_http
    fence(env, generation=2, epoch=env["binding"]["recovery_stream"]["library_epoch"] + 1)
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only route must not use a write authorization or commit")
    monkeypatch.setattr(sync_api, "_authorized_commit", forbidden)
    before = sql_image(env["con"])
    response = env["client"].post("/api/sync/uploads/receipt", json=http_body(env), headers={"X-Sync-Token": "test-sync-secret"})
    assert response.status_code == 200, response.text
    assert response.json() == {"binding": env["binding"], "payload_sha256": env["payload_sha256"], "state": "published",
        "receipt": {**env["original_receipt"], "replayed": True}}
    assert sql_image(env["con"]) == before


def test_http_failed_proof_keeps_original_error_when_rollback_and_close_fail(receipt_http, monkeypatch):
    from app import sync_api
    env = receipt_http
    cleanups = []
    connect = sync_api.db.connect
    class FailingCleanup:
        def __init__(self):
            # Each API connection is created in its own worker thread.
            self.connection = connect()

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def rollback(self):
            cleanups.append("rollback")
            raise OSError("synthetic rollback failure")

        def close(self):
            cleanups.append("close")
            self.connection.close()
            raise OSError("synthetic close failure")

    monkeypatch.setattr(sync_api.db, "connect", lambda: FailingCleanup())
    before = sql_image(env["con"])
    body = {**http_body(env), "payload_sha256": "0" * 64}
    response = env["client"].post("/api/sync/uploads/receipt", json=body, headers={"X-Sync-Token": "test-sync-secret"})
    assert response.status_code == 409 and response.json()["code"] == "SYNC_PUBLICATION_CONFLICT", (response.text, cleanups)
    assert cleanups == ["rollback", "rollback", "close"]
    env["con"].rollback()
    assert sql_image(env["con"]) == before


def test_http_successful_proof_does_not_hide_close_storage_failure(receipt_http, monkeypatch):
    from app import sync_api
    env = receipt_http
    connect = sync_api.db.connect
    class FailingClose:
        def __init__(self):
            self.connection = connect()

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.connection.close()
            raise OSError("synthetic close failure")

    monkeypatch.setattr(sync_api.db, "connect", lambda: FailingClose())
    before = sql_image(env["con"])
    response = env["client"].post("/api/sync/uploads/receipt", json=http_body(env), headers={"X-Sync-Token": "test-sync-secret"})
    assert response.status_code == 503 and response.json()["code"] == "SYNC_STORAGE_FAILED"
    assert sql_image(env["con"]) == before


@pytest.mark.parametrize("invalid", ["extra", "missing", "duplicate", "no_sync_token", "wrong_session", "wrong_hash"])
def test_http_receipt_rejects_non_contract_requests_without_mutation(receipt_http, invalid):
    env = receipt_http
    body = deepcopy(http_body(env))
    headers = {"X-Sync-Token": "test-sync-secret"}
    if invalid == "extra":
        body["state"] = "abandoned"
    elif invalid == "missing":
        body.pop("payload_sha256")
    elif invalid == "no_sync_token":
        headers = {"X-Reno-Token": "test-browser-secret"}
    elif invalid == "wrong_session":
        body["lease"]["lease_token"] = str(uuid.uuid4())
    elif invalid == "wrong_hash":
        body["payload_sha256"] = "0" * 64
    before = sql_image(env["con"])
    if invalid == "duplicate":
        raw = json.dumps(body)[:-1] + ',"payload_sha256":"' + env["payload_sha256"] + '"}'
        response = env["client"].post("/api/sync/uploads/receipt", content=raw,
            headers={**headers, "Content-Type": "application/json"})
    else:
        response = env["client"].post("/api/sync/uploads/receipt", json=body, headers=headers)
    assert response.status_code in {400, 401, 409}, response.text
    assert sql_image(env["con"]) == before
