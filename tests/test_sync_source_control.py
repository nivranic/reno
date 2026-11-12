"""Real isolated SQL/HTTP source terminal receipts; no source transport."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import uuid

import pytest

from reno import db, sync_queue as queue, sync_source_control as control
from reno import sync_source_control_contract as contract
from reno.sync_protocol import SyncError
from tests.test_sync_queue import env, NOW, URL, start, add_claim, publication, stamp  # noqa: F401
from tests.test_sync_transfer import env as api_env  # noqa: F401
from tests.test_sync_budget_wait import request as budget_request, rows
from tests.test_sync_recovery import generations, apply as restore_knowledge  # noqa: F401
from tests.test_sync_checkpoints import receiver  # noqa: F401


@pytest.fixture(autouse=True)
def historical_schema16_contract(monkeypatch):
    """Keep original controls and all their owners in the released16 lifetime."""
    from tests.schema_fixtures import released16

    with released16(monkeypatch):
        yield


def failure(**changes):
    value = {"format_version": 1, "operation_id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()),
        "ordinal": 4, "input_sha256": "1" * 64, "request_sha256": "2" * 64,
        "settlement_sha256": "3" * 64, "result_sha256": "4" * 64,
        "operation_kind": "source_metadata", "segment": None, "source_contract_version": 1,
        "resolver_contract_version": None, "resolver_phase": None, "error_class": "source_unavailable"}
    return {**value, **changes}


def request(env, lease, item, **changes):
    value = budget_request(env, lease, item)
    for key in ("reason", "retry_at", "budget_operation_id"):
        value.pop(key)
    value.update(kind="source_fail", core_sha256="a" * 64,
        submitted_url_sha256=hashlib.sha256(item["url"].encode("utf-8")).hexdigest(), failure=failure())
    return {**value, **changes}


def apply(env, body, now=NOW):
    try:
        result = control.apply_request(env["con"], body, now=now)
        env["con"].commit()
        return result
    except BaseException:
        env["con"].rollback()
        raise


def test_first_terminal_commits_exact_receipt_without_refund_or_publication(env):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    original_attempts = rows(env)[0]["attempts"]
    first = apply(env, body)
    assert first["status"] == rows(env)[0]["status"] == "dead"
    assert first["attempts"] == rows(env)[0]["attempts"] == original_attempts
    assert first == contract.normalize_receipt(body, first)
    row = rows(env)[0]
    assert row["owner_id"] is row["lease_token"] is row["lease_until"] is row["retry_at"] is None
    assert row["owner_generation"] == 0 and row["lease_generation"] == item["claim"]["lease_generation"]
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 1
    for table in ("budget_operation", "budget_attempt", "sync_queue_budget_receipt", "sync_queue_publication"):
        assert env["con"].execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert queue.claim(env["con"], env["source"], lease, now=NOW) == []


def test_receipt_replays_before_expired_session_and_changed_recovery_without_new_effect(env):
    lease = start(env, seconds=60)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    first = apply(env, body)
    newer = queue.heartbeat(env["con"], env["source"], str(uuid.uuid4()), now=NOW + timedelta(seconds=61))["lease"]
    # Deliberately simulate a later restored/requeued operational image. Even
    # then the original terminal receipt cannot touch this newer claim.
    env["con"].execute("UPDATE sync_queue SET status='queued'")
    queue.claim(env["con"], env["source"], newer, now=NOW + timedelta(seconds=61))
    env["con"].execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,updated_at) "
                       "VALUES(1,'held',1,1,NULL,?)", (stamp(NOW),))
    env["con"].execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    env["con"].commit()
    before = rows(env)
    assert apply(env, body, NOW + timedelta(seconds=62)) == {**first, "replayed": True}
    assert rows(env) == before and before[0]["lease_generation"] == 2
    with pytest.raises(SyncError) as caught:
        apply(env, {**body, "request_id": str(uuid.uuid4())})
    assert caught.value.code == "SYNC_SOURCE_REQUEST_MISMATCH" and rows(env) == before


@pytest.mark.parametrize("change", ["expired_claim", "later_claim", "held_fence", "wrong_url"])
def test_first_application_needs_exact_current_claim_and_running_original_fence(env, change):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    if change in {"expired_claim", "later_claim"}:
        env["con"].execute("UPDATE sync_queue SET lease_until=?", (stamp(NOW - timedelta(seconds=1)),))
        if change == "later_claim":
            queue.claim(env["con"], env["source"], lease, now=NOW)
    elif change == "held_fence":
        env["con"].execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,updated_at) "
                           "VALUES(1,'held',1,1,NULL,?)", (stamp(NOW),))
    else:
        body["submitted_url_sha256"] = "f" * 64
    env["con"].commit()
    before = rows(env)
    with pytest.raises(SyncError):
        apply(env, body)
    assert rows(env) == before
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"ordinal": True}, {"ordinal": 129}, {"operation_kind": "source_enumeration"},
    {"response_complete": False}, {"result_sha256": "z" * 64},
    {"operation_kind": "download", "segment": "audio", "error_class": "source_unavailable"},
    {"resolver_contract_version": 1}, {"resolver_phase": "dns"},
])
def test_closed_public_failure_rejects_unknown_or_unsupported_terminal_authority(env, changes):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease), failure=failure(**changes))
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, body)
    assert caught.value.code == "INVALID_SYNC_REQUEST" and rows(env) == before


def test_audio_artifact_invalid_can_terminal_but_normal_audio_failure_cannot(env):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease), failure=failure(
        operation_kind="download", segment="audio", error_class="media_invalid"))
    assert apply(env, body)["error_class"] == "media_invalid"


@pytest.mark.parametrize("field", ["core_sha256", "failure_sha256", "source_node_id", "result_sha256", "committed_at"])
def test_corrupt_receipt_cannot_authorize_replay_or_touch_queue(env, field):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    apply(env, body)
    env["con"].execute(f"UPDATE sync_queue_source_receipt SET {field}=?", ("f" * 64,))
    env["con"].commit()
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, body)
    assert caught.value.code == "SYNC_SOURCE_RECEIPT_INVALID" and rows(env) == before


def test_two_actual_sql_writers_have_one_terminal_receipt(env):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    barrier = threading.Barrier(2)
    def writer():
        con = db.connect(env["path"])
        try:
            barrier.wait(timeout=5)
            result = control.apply_request(con, deepcopy(body), now=NOW)
            con.commit()
            return result
        finally:
            con.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: writer(), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len(list(env["con"].execute("SELECT * FROM sync_queue_source_receipt"))) == 1
    assert rows(env)[0]["attempts"] == 1 and rows(env)[0]["status"] == "dead"


def test_real_http_source_control_commits_before_response_and_replays_expired_lease(api_env):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from reno import config

    config.set_local({"node_role": "cloud", "sync_token": "source-control-fixture-token"})
    env = {"con": api_env["con"], "source": api_env["identity"], "path": api_env["target_path"]}
    current = datetime.now(timezone.utc)
    lease = start(env, now=current)
    body = request(env, lease, add_claim(env, lease, now=current))
    headers = {"X-Sync-Token": "source-control-fixture-token"}
    with TestClient(create_app(), base_url="https://sync.example.test") as client:
        assert client.post("/api/sync/push", json=body).status_code == 401
        response = client.post("/api/sync/push", json=body, headers=headers)
        assert response.status_code == 200, response.text
        first = response.json()
        assert rows(env)[0]["status"] == "dead"
        env["con"].execute("UPDATE sync_session SET lease_until=?", (stamp(NOW),))
        env["con"].commit()
        replay = client.post("/api/sync/push", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == {**first, "replayed": True}
        changed = client.post("/api/sync/push", json={**body, "core_sha256": "b" * 64}, headers=headers)
        assert changed.status_code == 409 and changed.json()["code"] == "SYNC_SOURCE_REQUEST_MISMATCH"
        invalid = json.dumps({**body, "core_sha256": "\ud800"}).encode("utf-8")
        rejected = client.post("/api/sync/push", content=invalid,
            headers={**headers, "Content-Type": "application/json"})
        assert rejected.status_code == 400 and rejected.json()["code"] == "INVALID_SYNC_REQUEST"


def test_terminal_receipt_never_replays_after_pair_identity_rotation(env):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    apply(env, body)
    env["con"].execute("UPDATE sync_pair SET source_node_id=?", (str(uuid.uuid4()),))
    env["con"].commit()
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, body)
    assert caught.value.code == "SYNC_PEER_MISMATCH" and rows(env) == before
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 1


def test_first_terminal_cannot_overwrite_an_existing_publication_edge(env):
    lease = start(env)
    item = add_claim(env, lease)
    publication(env, item=item, lease=lease)
    body = request(env, lease, item)
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, body)
    assert caught.value.code == "SYNC_OUTCOME_MISMATCH" and rows(env) == before
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 0
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_publication").fetchone()[0] == 1


def test_actual_logical_restore_keeps_exact_terminal_receipt_and_queue_image(generations, tmp_path):
    from reno import personal_schema, sync_protocol
    source_path = tmp_path / "independent-home" / "reno.db"
    db.initialize(source_path)
    con = db.connect(source_path)
    try:
        source = {"library_id": db.knowledge_version(con)["library_id"],
                  "node_id": personal_schema.node_identity(source_path.parent),
                  "handshake": sync_protocol.schema_handshake(con)}
    finally:
        con.close()
    env = {"con": generations["con"], "source": source, "path": generations["path"]}
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    first = apply(env, body)
    before = rows(env)
    receipts = [tuple(row) for row in env["con"].execute("SELECT * FROM sync_queue_source_receipt")]
    restored = restore_knowledge(generations)
    assert restored["control"]["state"] == "held" and rows(env) == before
    assert [tuple(row) for row in env["con"].execute("SELECT * FROM sync_queue_source_receipt")] == receipts
    assert apply(env, body) == {**first, "replayed": True}


def test_http_last_session_check_rolls_back_terminal_receipt_and_queue_effect(api_env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from reno import config

    config.set_local({"node_role": "cloud", "sync_token": "source-control-fence-fixture"})
    env = {"con": api_env["con"], "source": api_env["identity"], "path": api_env["target_path"]}
    current = datetime.now(timezone.utc)
    lease = start(env, now=current)
    body = request(env, lease, add_claim(env, lease, now=current))
    original = control.apply_request
    def expiry_before_commit(con, value):
        result = original(con, value)
        con.execute("UPDATE sync_session SET lease_until=?", (stamp(NOW),))
        return result
    monkeypatch.setattr(control, "apply_request", expiry_before_commit)
    before = rows(env)
    with TestClient(create_app(), base_url="https://sync.example.test") as client:
        response = client.post("/api/sync/push", json=body,
            headers={"X-Sync-Token": "source-control-fence-fixture"})
    assert response.status_code == 409, response.text
    assert rows(env) == before
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 0
