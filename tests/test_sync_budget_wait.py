"""Budget queue control uses isolated SQLite; no source/model/platform calls."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
import uuid

import pytest

from reno import db, sync_budget_wait as wait, sync_queue as queue, sync_recovery
from reno.sync_protocol import SyncError
from tests.test_sync_queue import env, NOW, URL, start, add_claim, stamp
from tests.test_sync_transfer import env as api_env
from tests.test_sync_recovery import generations, apply as restore_knowledge
from tests.test_sync_checkpoints import receiver


@pytest.fixture(autouse=True)
def historical_schema16_contract(monkeypatch):
    """Keep original controls and all their owners in the released16 lifetime."""
    from tests.schema_fixtures import released16

    with released16(monkeypatch):
        yield


def request(env, lease, item, **changes):
    version = db.knowledge_version(env["con"])
    target_library, target_node = queue._local_identity(env["con"])
    result = {"kind": "budget_defer", "request_id": str(uuid.uuid4()), "source": deepcopy(env["source"]),
        "target": {"library_id": target_library, "node_id": target_node}, "lease": dict(lease),
        "claim": dict(item["claim"]), "recovery_generation": 0,
        "recovery_stream": {k: version[k] for k in ("library_id", "library_epoch")},
        "reason": "daily_limit", "retry_at": stamp(NOW + timedelta(seconds=60)),
        "budget_operation_id": str(uuid.uuid4())}
    result.update(changes)
    return result


def apply(env, body, now=NOW):
    result = wait.apply_request(env["con"], body, now=now)
    env["con"].commit()
    return result


def rows(env):
    return [dict(row) for row in env["con"].execute("SELECT * FROM sync_queue ORDER BY item_id")]


def test_defer_releases_slot_and_refunds_only_current_admission(env):
    lease = start(env)
    item = add_claim(env, lease)
    env["con"].execute("UPDATE sync_queue SET attempts=5")
    env["con"].commit()
    body = request(env, lease, item)
    first = apply(env, body)
    row = rows(env)[0]
    assert first["status"] == row["status"] == "budget_wait"
    assert first["attempts"] == row["attempts"] == 4
    assert row["lease_generation"] == item["claim"]["lease_generation"]
    assert row["owner_id"] is row["lease_token"] is row["lease_until"] is None
    assert row["owner_generation"] == 0 and row["last_error_class"] is None
    before = rows(env)
    assert apply(env, body)["replayed"] is True
    assert rows(env) == before
    renewed = queue.heartbeat(env["con"], env["source"], lease["session_id"], lease=lease, now=NOW)
    assert renewed["renewed_claims"] == 0
    assert queue.claim(env["con"], env["source"], lease, now=NOW + timedelta(seconds=59)) == []
    fresh = queue.claim(env["con"], env["source"], lease, now=NOW + timedelta(seconds=60))[0]
    assert fresh["attempts"] == 5 and fresh["claim"]["lease_generation"] == 2


def test_blocked_null_never_due_and_does_not_starve_other_queue_item(env):
    lease = start(env)
    item = add_claim(env, lease)
    apply(env, request(env, lease, item, retry_at=None, reason="indeterminate"))
    queue.enqueue(env["con"], [URL], now=NOW)
    claimed = queue.claim(env["con"], env["source"], lease, limit=2, now=NOW + timedelta(seconds=100))
    assert len(claimed) == 1 and claimed[0]["item_id"] != item["item_id"]
    assert queue.summary(env["con"], now=NOW)["budget_due"] == 0


def test_historical_receipt_precedes_expired_replaced_session_and_new_claim(env):
    lease = start(env, seconds=60)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    first = apply(env, body)
    newer = queue.heartbeat(env["con"], env["source"], str(uuid.uuid4()), now=NOW + timedelta(seconds=61))["lease"]
    queue.claim(env["con"], env["source"], newer, now=NOW + timedelta(seconds=61))
    env["con"].commit()
    before = rows(env)
    replay = apply(env, body, NOW + timedelta(seconds=62))
    assert replay == {**first, "replayed": True}
    assert rows(env) == before


@pytest.mark.parametrize("change", [
    {"reason": "output_limit"}, {"retry_at": None}, {"budget_operation_id": None},
    {"request_id": str(uuid.uuid4())},
])
def test_original_claim_cannot_change_defer_or_issue_another_uuid(env, change):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    apply(env, body)
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, {**body, **change}, NOW + timedelta(seconds=601))
    assert caught.value.code == "SYNC_BUDGET_REQUEST_MISMATCH"
    assert rows(env) == before


def test_fresh_expired_claim_cannot_refund_and_failure_race_preserves_winner(env):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    with pytest.raises(SyncError):
        apply(env, body, NOW + timedelta(seconds=601))
    assert rows(env)[0]["attempts"] == 1
    queue.fail(env["con"], env["source"], lease, item["claim"], "network", now=NOW,
               recovery_stream=body["recovery_stream"])
    env["con"].commit()
    with pytest.raises(SyncError):
        apply(env, body)
    assert rows(env)[0]["status"] == "retry_wait" and rows(env)[0]["attempts"] == 1
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_budget_receipt").fetchone()[0] == 0


def test_resume_is_explicit_idempotent_and_old_receipt_cannot_touch_new_claim(env):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item, retry_at=None)
    apply(env, body)
    resume = {k: deepcopy(body[k]) for k in ("source", "target", "lease", "recovery_generation", "recovery_stream")}
    resume.update(kind="budget_resume", request_id=str(uuid.uuid4()), defer_request_id=body["request_id"])
    first = apply(env, resume)
    assert first["status"] == "queued" and first["attempts"] == 0
    fresh = queue.claim(env["con"], env["source"], lease, now=NOW)[0]
    env["con"].commit()
    before = rows(env)
    assert apply(env, resume, NOW + timedelta(seconds=601)) == {**first, "replayed": True}
    assert rows(env) == before and fresh["claim"]["lease_generation"] == 2
    with pytest.raises(SyncError):
        apply(env, {**resume, "request_id": str(uuid.uuid4())})
    assert rows(env) == before


def test_recovery_progress_preserves_historical_receipt_but_rejects_fresh_old_fence(env):
    lease = start(env)
    one = add_claim(env, lease)
    two = add_claim(env, lease)
    body = request(env, lease, one)
    first = apply(env, body)
    env["con"].execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,updated_at) "
                       "VALUES(1,'held',1,1,NULL,?)", (stamp(NOW),))
    env["con"].execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    env["con"].commit()
    assert apply(env, body) == {**first, "replayed": True}
    with pytest.raises(SyncError):
        apply(env, request(env, lease, two))
    assert rows(env)[1]["status"] == "claimed" and rows(env)[1]["attempts"] == 1


@pytest.mark.parametrize("field", ["source", "target", "lease", "claim", "recovery_stream"])
def test_closed_binding_change_never_mutates_receipted_work(env, field):
    lease = start(env)
    item = add_claim(env, lease)
    body = request(env, lease, item)
    apply(env, body)
    changed = deepcopy(body)
    key = next(iter(changed[field]))
    changed[field][key] = str(uuid.uuid4()) if "id" in key or "token" in key else 999
    before = rows(env)
    with pytest.raises(SyncError):
        apply(env, changed)
    assert rows(env) == before


def test_two_real_sql_writers_share_one_receipt_and_one_refund(env):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    barrier = threading.Barrier(2)
    def writer():
        con = db.connect(env["path"])
        try:
            barrier.wait(timeout=5)
            result = wait.apply_request(con, deepcopy(body), now=NOW)
            con.commit()
            return result
        finally:
            con.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer) for _ in range(2)]
        results = [value.result(timeout=10) for value in futures]
    assert sorted(item["replayed"] for item in results) == [False, True]
    assert rows(env)[0]["attempts"] == 0
    assert env["con"].execute("SELECT COUNT(*) FROM sync_queue_budget_receipt").fetchone()[0] == 1


@pytest.mark.parametrize("corruption", ["request_list", "result_attempts"])
def test_corrupt_receipt_never_escapes_controlled_error_or_authorizes_work(env, corruption):
    lease = start(env)
    body = request(env, lease, add_claim(env, lease))
    apply(env, body)
    if corruption == "request_list":
        raw = "[]"
        env["con"].execute("UPDATE sync_queue_budget_receipt SET request_json=?,request_sha256=?", (raw, wait._sha(raw)))
    else:
        row = env["con"].execute("SELECT result_json FROM sync_queue_budget_receipt").fetchone()
        value = json.loads(row[0])
        value["attempts"] = 5
        raw = wait._json(value)
        env["con"].execute("UPDATE sync_queue_budget_receipt SET result_json=?,result_sha256=?", (raw, wait._sha(raw)))
    env["con"].commit()
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        apply(env, body)
    assert caught.value.code == "SYNC_BUDGET_RECEIPT_INVALID" and rows(env) == before


def test_actual_logical_restore_preserves_queue_and_exact_defer_receipt(generations, tmp_path):
    target = generations
    source_path = tmp_path / "independent-home/reno.db"
    db.initialize(source_path)
    from reno import personal_schema, sync_protocol
    with db.connect(source_path) as con:
        source = {"library_id": db.knowledge_version(con)["library_id"],
                  "node_id": personal_schema.node_identity(source_path.parent),
                  "handshake": sync_protocol.schema_handshake(con)}
    env = {"con": target["con"], "source": source, "path": target["path"]}
    lease = start(env)
    body = request(env, lease, add_claim(env, lease), retry_at=None)
    first = apply(env, body)
    before = rows(env)
    receipts = [tuple(row) for row in env["con"].execute("SELECT * FROM sync_queue_budget_receipt")]
    restored = restore_knowledge(target)
    assert restored["control"]["state"] == "held" and rows(env) == before
    assert [tuple(row) for row in env["con"].execute("SELECT * FROM sync_queue_budget_receipt")] == receipts
    assert apply(env, body) == {**first, "replayed": True}


def test_real_http_endpoint_commits_before_response_and_replays_expired_lease(api_env):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app.main import create_app
    from reno import config
    value = api_env
    config.set_local({"node_role": "cloud", "sync_token": "test-budget-sync"})
    env = {"con": value["con"], "source": value["identity"], "path": value["target_path"]}
    current = datetime.now(timezone.utc)
    lease = start(env, now=current)
    item = add_claim(env, lease, now=current)
    body = request(env, lease, item, retry_at=stamp(current + timedelta(seconds=60)))
    with TestClient(create_app(), base_url="https://sync.example.test") as client:
        assert client.post("/api/sync/push", json=body).status_code == 401
        headers = {"X-Sync-Token": "test-budget-sync"}
        response = client.post("/api/sync/push", json=body, headers=headers)
        assert response.status_code == 200, response.text
        first = response.json()
        assert rows(env)[0]["status"] == "budget_wait" and rows(env)[0]["attempts"] == 0
        env["con"].execute("UPDATE sync_session SET lease_until=?", (stamp(NOW),))
        env["con"].commit()
        replay = client.post("/api/sync/push", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == {**first, "replayed": True}
        altered = client.post("/api/sync/push", json={**body, "reason": "output_limit"}, headers=headers)
        assert altered.status_code == 409 and altered.json()["code"] == "SYNC_BUDGET_REQUEST_MISMATCH"
        # JSON can legally carry an isolated escaped surrogate. It must become
        # a controlled request rejection, never an uncaught UTF-8 exception.
        invalid = json.dumps({**body, "reason": "\ud800"}).encode("utf-8")
        rejected = client.post("/api/sync/push", content=invalid,
                               headers={**headers, "Content-Type": "application/json"})
        assert rejected.status_code == 400 and rejected.json()["code"] == "INVALID_SYNC_REQUEST"


@pytest.mark.parametrize("removed", ["CHECK(action IN ('budget_defer','budget_resume'))",
    "CHECK(receiver_library_epoch>=1)", "CHECK(recovery_generation>=0)",
    "CHECK(length(CAST(request_json AS BLOB))<=16384)",
    "CHECK(length(CAST(result_json AS BLOB))<=16384)"])
def test_missing_receipt_check_constraint_is_incompatible_even_when_columns_and_indexes_match(env, removed):
    from reno import migrations, sync_schema
    con = env["con"]
    declaration = sync_schema.TABLES["sync_queue_budget_receipt"]
    if removed.startswith("CHECK(length"):
        declaration = declaration.replace(",\n        " + removed, "")
    else:
        declaration = declaration.replace(removed, "")
    con.execute("DROP TABLE sync_queue_budget_receipt")
    con.execute(f"CREATE TABLE sync_queue_budget_receipt ({declaration})")
    for sql in sync_schema.INDEXES:
        con.execute(sql)
    con.commit()
    with pytest.raises(migrations.SchemaVersionError, match="incompatible"):
        migrations.validate(con)
