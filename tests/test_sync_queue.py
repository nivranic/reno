"""Leases and queue fencing on isolated SQLite files, never the user's live DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import uuid

import pytest

from reno import db, personal_schema, sync_protocol, sync_queue as queue
from reno.sync_protocol import SyncError
from tests.test_sync_events import personal_snapshot, populate

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
URL = "https://www.bilibili.com/video/BV1SyncExample?token=private-query"


def stamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture()
def env(tmp_path):
    target = tmp_path / "target" / "reno.db"
    source_path = tmp_path / "source" / "reno.db"
    db.initialize(target)
    db.initialize(source_path)
    con = db.connect(target)
    source_con = db.connect(source_path)
    populate(con)
    source = {"library_id": db.knowledge_version(source_con)["library_id"],
              "node_id": personal_schema.node_identity(source_path.parent),
              "handshake": sync_protocol.schema_handshake(source_con)}
    source_con.close()
    yield {"con": con, "source": source, "path": target, "source_path": source_path}
    con.close()


def start(env, *, now=NOW, seconds=600, session_id=None):
    con, source = env["con"], env["source"]
    queue.bind_peer(con, source, now=now)
    lease = queue.heartbeat(con, source, session_id or str(uuid.uuid4()), now=now,
                            lease_seconds=seconds)["lease"]
    con.commit()
    return lease


def add_claim(env, lease, *, now=NOW):
    queue.enqueue(env["con"], [URL], now=now)
    result = queue.claim(env["con"], env["source"], lease, now=now)[0]
    env["con"].commit()
    return result


def publication(env, *, publication_id=None, video_id="BV1SyncExample", item=None, lease=None, **changes):
    proof = {"publication_id": publication_id or str(uuid.uuid4()), "video_id": video_id,
             "source_library_id": env["source"]["library_id"], "source_node_id": env["source"]["node_id"],
             "source_epoch": 1, "source_revision": 3, "media_sha256": "a" * 64, "committed_at": stamp(NOW), **changes}
    receipt = {key: proof[key] for key in ("publication_id", "source_library_id", "source_node_id", "source_epoch", "source_revision", "committed_at")}
    receipt.update(payload_sha256="b" * 64, knowledge_sha256="c" * 64,
                   local_version_json=json.dumps(db.knowledge_version(env["con"])))
    env["con"].execute(f"INSERT OR IGNORE INTO sync_publication ({','.join(receipt)}) VALUES ({','.join('?' for _ in receipt)})",
                       tuple(receipt.values()))
    env["con"].execute(f"INSERT INTO sync_published_video ({','.join(proof)}) VALUES ({','.join('?' for _ in proof)})",
                       tuple(proof.values()))
    if item is not None:
        bind_publication(env, proof, item, lease)
    env["con"].commit()
    return {"publication_id": proof["publication_id"], "video_id": proof["video_id"]}


def bind_publication(env, proof, item, lease, **changes):
    binding = {"item_id": item["item_id"], "lease_generation": item["claim"]["lease_generation"],
               "lease_token": item["claim"]["lease_token"], "owner_id": lease["session_id"],
               "owner_generation": lease["generation"], "publication_id": proof["publication_id"],
               "video_id": proof["video_id"], "created_at": stamp(NOW), **changes}
    env["con"].execute(f"INSERT INTO sync_queue_publication ({','.join(binding)}) VALUES ({','.join('?' for _ in binding)})",
                       tuple(binding.values()))


def sync_state(con):
    return {table: [dict(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("sync_pair", "sync_session", "sync_queue", "sync_published_video", "sync_queue_publication")}


def test_explicit_pairing_is_idempotent_and_preserves_independent_identities(env):
    con, source = env["con"], env["source"]
    before = personal_snapshot(con)
    with pytest.raises(SyncError) as caught:
        queue.heartbeat(con, source, str(uuid.uuid4()), now=NOW)
    assert caught.value.code == "SYNC_NOT_PAIRED"
    assert not con.in_transaction
    pair = queue.bind_peer(con, source, now=NOW)
    assert pair["source_library_id"] == source["library_id"]
    assert pair["target_library_id"] == db.knowledge_version(con)["library_id"]
    assert pair["source_library_id"] != pair["target_library_id"]
    assert pair["source_node_id"] != pair["target_node_id"]
    assert queue.bind_peer(con, source, now=NOW + timedelta(days=1)) == pair
    assert personal_snapshot(con) == before
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM sync_pair").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"library_id": str(uuid.uuid4())}, {"node_id": str(uuid.uuid4())},
    {"handshake": {"protocol_version": 999, "migration_version": 5, "core_schema_sha256": "0" * 64}},
])
def test_existing_pair_cannot_be_replaced_by_handshake_or_token(env, change):
    con, source = env["con"], env["source"]
    lease = start(env)
    before = sync_state(con)
    con.execute("UPDATE renovation_project SET name='先前调用方修改'")
    with pytest.raises(SyncError) as caught:
        queue.bind_peer(con, {**source, **change}, now=NOW)
    assert caught.value.status == 409
    assert sync_state(con) == before
    assert con.execute("SELECT name FROM renovation_project").fetchone()[0] == "先前调用方修改"
    with pytest.raises(SyncError):
        queue.require_session(con, {**source, **change}, lease, now=NOW)
    assert sync_state(con) == before and con.in_transaction


@pytest.mark.parametrize("field", ["library_id", "node_id"])
def test_self_pairing_is_rejected(env, field):
    source = deepcopy(env["source"])
    source[field] = db.knowledge_version(env["con"])["library_id"] if field == "library_id" else personal_schema.node_identity(env["path"].parent)
    with pytest.raises(SyncError) as caught:
        queue.bind_peer(env["con"], source, now=NOW)
    assert caught.value.code == "SYNC_SELF_PAIRING"
    assert not env["con"].in_transaction


def test_copied_database_identity_does_not_silently_rebind(env):
    lease = start(env)
    before = sync_state(env["con"])
    path = env["path"].parent / "node-identity.json"
    path.write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    with pytest.raises(SyncError) as caught:
        queue.require_session(env["con"], env["source"], lease, now=NOW)
    assert caught.value.code == "SYNC_PEER_MISMATCH"
    assert sync_state(env["con"]) == before
    assert queue.summary(env["con"], now=NOW)["online"] is False


def test_missing_identity_is_not_recreated(env):
    path = env["path"].parent / "node-identity.json"
    path.unlink()
    with pytest.raises(SyncError) as caught:
        queue.bind_peer(env["con"], env["source"], now=NOW)
    assert caught.value.code == "SYNC_IDENTITY_UNAVAILABLE"
    assert not path.exists()


def test_heartbeat_renewal_requires_original_token_and_generation(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    item = add_claim(env, lease)
    before = sync_state(con)
    bad_leases = [None, {**lease, "lease_token": str(uuid.uuid4())}, {**lease, "generation": 2}]
    for bad in bad_leases:
        with pytest.raises(SyncError) as caught:
            queue.heartbeat(con, source, lease["session_id"], lease=bad, now=NOW + timedelta(seconds=1))
        assert caught.value.code == "SYNC_LEASE_MISMATCH"
        assert sync_state(con) == before
    with pytest.raises(SyncError) as caught:
        queue.heartbeat(con, source, str(uuid.uuid4()), now=NOW + timedelta(seconds=1))
    assert caught.value.code == "SYNC_SESSION_BUSY"
    result = queue.heartbeat(con, source, lease["session_id"], lease=lease,
                             metrics={"cookie_state": "unknown", "disk_free_bytes": 4000, "queue_depth": 2, "active_jobs": 1},
                             now=NOW + timedelta(seconds=10))
    assert result["renewed_claims"] == 1
    assert result["lease"]["generation"] == lease["generation"]
    assert result["lease"]["lease_token"] == lease["lease_token"]
    row = con.execute("SELECT * FROM sync_queue WHERE item_id=?", (item["item_id"],)).fetchone()
    assert row["lease_until"] == stamp(NOW + timedelta(seconds=610))
    assert row["lease_token"] == item["claim"]["lease_token"]
    assert queue.summary(con, now=NOW)["metrics"]["cookie_state"] == "unknown"


def test_exact_expiry_rejects_old_renewal_and_rotates_reacquired_session(env):
    con, source = env["con"], env["source"]
    old = start(env, seconds=10)
    claimed = add_claim(env, old)
    expired = NOW + timedelta(seconds=10)
    for operation in (
        lambda: queue.heartbeat(con, source, old["session_id"], lease=old, now=expired),
        lambda: queue.require_session(con, source, old, now=expired),
        lambda: queue.fail(con, source, old, claimed["claim"], "network", now=expired),
    ):
        with pytest.raises(SyncError) as caught:
            operation()
        assert caught.value.code == "SYNC_LEASE_EXPIRED"
    fresh = queue.heartbeat(con, source, old["session_id"], now=expired, lease_seconds=20)["lease"]
    assert fresh["generation"] == old["generation"] + 1
    assert fresh["lease_token"] != old["lease_token"]
    new_claim = queue.claim(con, source, fresh, now=expired)[0]
    assert new_claim["item_id"] == claimed["item_id"]
    assert new_claim["attempts"] == 2
    assert new_claim["claim"]["lease_generation"] == 2
    assert new_claim["claim"]["lease_token"] != claimed["claim"]["lease_token"]
    with pytest.raises(SyncError) as caught:
        queue.fail(con, source, old, claimed["claim"], "network", now=expired)
    assert caught.value.code == "SYNC_LEASE_MISMATCH"
    with pytest.raises(SyncError) as caught:
        queue.fail(con, source, fresh, claimed["claim"], "network", now=expired)
    assert caught.value.code == "SYNC_CLAIM_MISMATCH"


def test_heartbeat_never_resurrects_expired_or_other_generation_task_leases(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    queue.enqueue(con, [URL, URL, URL], now=NOW)
    items = queue.claim(con, source, lease, limit=3, now=NOW)
    con.execute("UPDATE sync_queue SET lease_until=? WHERE item_id=?", (stamp(NOW + timedelta(seconds=10)), items[0]["item_id"]))
    con.execute("UPDATE sync_queue SET owner_generation=0 WHERE item_id=?", (items[1]["item_id"],))
    con.commit()
    at = NOW + timedelta(seconds=10)
    result = queue.heartbeat(con, source, lease["session_id"], lease=lease, now=at)
    assert result["renewed_claims"] == 1
    with pytest.raises(SyncError) as caught:
        queue.fail(con, source, result["lease"], items[0]["claim"], "network", now=at)
    assert caught.value.code == "SYNC_CLAIM_EXPIRED"
    reclaimed = queue.claim(con, source, result["lease"], limit=3, now=at)
    assert {item["item_id"] for item in reclaimed} == {item["item_id"] for item in items[:2]}
    assert all(item["attempts"] == 2 for item in reclaimed)


def test_two_connections_compete_without_double_claim(env):
    lease = start(env)
    queue.enqueue(env["con"], [URL] * 7, now=NOW)
    env["con"].commit()
    ready = threading.Barrier(2)

    def worker():
        con = db.connect(env["path"])
        try:
            ready.wait(timeout=10)
            items = queue.claim(con, env["source"], lease, limit=7, now=NOW)
            con.commit()
            return items
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        first, second = [future.result(timeout=15) for future in futures]
    assert len(first) + len(second) == 7
    assert {row["item_id"] for row in first}.isdisjoint(row["item_id"] for row in second)
    assert {row["attempts"] for row in first + second} == {1}


@pytest.mark.parametrize("operation", ["heartbeat", "claim", "fail", "complete", "require_session"])
@pytest.mark.parametrize("deferred", [False, True])
def test_waiting_for_write_lock_resamples_time_before_authorizing(env, monkeypatch, operation, deferred):
    """A real competing connection blocks; a controlled clock crosses expiry."""
    lease = start(env, seconds=10)
    item = add_claim(env, lease)
    proof = publication(env, item=item, lease=lease)
    clock = [NOW]
    original_clock = queue._clock
    monkeypatch.setattr(queue, "_clock", lambda now: original_clock(clock[0] if now is None else now))
    waiting = threading.Event()
    before = sync_state(env["con"])

    def worker():
        con = db.connect(env["path"])
        if deferred:
            con.execute("BEGIN")
        lock_statement = "UPDATE sync_session SET slot=slot WHERE 0" if deferred else "BEGIN IMMEDIATE"
        con.set_trace_callback(lambda statement: waiting.set() if statement == lock_statement else None)
        try:
            if operation == "heartbeat":
                queue.heartbeat(con, env["source"], lease["session_id"], lease=lease)
            elif operation == "claim":
                queue.claim(con, env["source"], lease)
            elif operation == "fail":
                queue.fail(con, env["source"], lease, item["claim"], "network")
            elif operation == "complete":
                queue.complete(con, env["source"], lease, item["claim"], **proof)
            else:
                queue.require_session(con, env["source"], lease)
        except SyncError as exc:
            return exc.code
        finally:
            con.close()
        return "incorrectly-authorized"

    env["con"].execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        try:
            assert waiting.wait(timeout=10)
            clock[0] = NOW + timedelta(seconds=10)
        finally:
            env["con"].commit()
        assert future.result(timeout=10) == "SYNC_LEASE_EXPIRED"
    assert sync_state(env["con"]) == before


def test_deferred_stale_read_snapshot_returns_controlled_busy_without_committing(env):
    lease = start(env)
    queue.enqueue(env["con"], [URL], now=NOW)
    env["con"].commit()
    con = env["con"]
    con.execute("BEGIN")
    con.execute("SELECT COUNT(*) FROM sync_queue").fetchone()
    other = db.connect(env["path"])
    try:
        queue.enqueue(other, [URL], now=NOW)
        other.commit()
    finally:
        other.close()
    with pytest.raises(SyncError) as caught:
        queue.claim(con, env["source"], lease, now=NOW)
    assert caught.value.code == "SYNC_BUSY"
    assert con.in_transaction
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM sync_queue WHERE status='queued'").fetchone()[0] == 2


def test_enqueue_validates_entire_batch_before_inserting_and_never_commits(env):
    con = env["con"]
    with pytest.raises(SyncError):
        queue.enqueue(con, [URL, "http://127.0.0.1/private"], now=NOW)
    assert con.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0] == 0
    assert queue.enqueue(con, [URL, URL], now=NOW) == 2
    with sqlite3.connect(env["path"]) as reader:
        assert reader.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0] == 0
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0] == 0


def test_legacy_ownerless_claims_recover_and_invalid_urls_do_not_block_valid_work(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    con.execute("INSERT INTO sync_queue(url,status,attempts,lease_until) VALUES('http://127.0.0.1/private','queued',0,NULL)")
    con.execute("INSERT INTO sync_queue(url,status,attempts,lease_until) VALUES(?,'claimed',1,'2999-01-01T00:00:00.000Z')", (URL,))
    queue.enqueue(con, [URL], now=NOW)
    con.commit()
    items = queue.claim(con, source, lease, limit=2, now=NOW)
    assert len(items) == 2
    assert [item["attempts"] for item in items] == [2, 1]
    invalid = con.execute("SELECT * FROM sync_queue ORDER BY item_id LIMIT 1").fetchone()
    assert invalid["status"] == "dead" and invalid["last_error_class"] == "invalid_url"
    assert invalid["attempts"] == 0


@pytest.mark.parametrize("error_class", sorted(queue.RETRYABLE_ERRORS))
def test_retries_use_due_time_exponential_backoff_and_five_attempt_limit(env, error_class):
    con, source = env["con"], env["source"]
    lease = start(env, seconds=3600)
    item = add_claim(env, lease)
    at = NOW
    for attempt in range(1, 6):
        assert item["attempts"] == attempt
        result = queue.fail(con, source, lease, item["claim"], error_class, now=at)
        replay = queue.fail(con, source, lease, item["claim"], error_class, now=at)
        assert replay == {**result, "replayed": True}
        if attempt == 5:
            assert result["status"] == "dead" and result["retry_at"] is None
            assert queue.claim(con, source, lease, now=at + timedelta(seconds=1000)) == []
        else:
            seconds = queue.RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            assert result["status"] == "retry_wait" and result["retry_at"] == stamp(at + timedelta(seconds=seconds))
            assert queue.claim(con, source, lease, now=at + timedelta(seconds=seconds - 0.001)) == []
            at += timedelta(seconds=seconds)
            old_claim = item["claim"]
            item = queue.claim(con, source, lease, now=at)[0]
            assert item["claim"]["lease_generation"] == attempt + 1
            with pytest.raises(SyncError) as caught:
                queue.fail(con, source, lease, old_claim, error_class, now=at)
            assert caught.value.code == "SYNC_CLAIM_MISMATCH"


@pytest.mark.parametrize("error_class", sorted(queue.PERMANENT_ERRORS))
def test_permanent_failures_are_dead_without_new_attempts(env, error_class):
    lease = start(env)
    item = add_claim(env, lease)
    result = queue.fail(env["con"], env["source"], lease, item["claim"], error_class, now=NOW)
    assert result["status"] == "dead" and result["retry_at"] is None
    assert queue.claim(env["con"], env["source"], lease, now=NOW) == []
    assert result["attempts"] == 1


def test_expired_fifth_claim_is_dead_without_sixth_dispatch(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    item = add_claim(env, lease)
    con.execute("UPDATE sync_queue SET attempts=5,lease_until=? WHERE item_id=?", (stamp(NOW), item["item_id"]))
    assert queue.claim(con, source, lease, now=NOW) == []
    row = con.execute("SELECT * FROM sync_queue").fetchone()
    assert row["status"] == "dead" and row["attempts"] == 5 and row["last_error_class"] == "lease_expired"


def test_completion_requires_server_publication_then_is_idempotent(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    item = add_claim(env, lease)
    result = {"publication_id": str(uuid.uuid4()), "video_id": "BV1SyncExample"}
    before = sync_state(con)
    with pytest.raises(SyncError) as caught:
        queue.complete(con, source, lease, item["claim"], now=NOW, **result)
    assert caught.value.code == "SYNC_PUBLICATION_REQUIRED"
    assert sync_state(con) == before
    publication(env, item=item, lease=lease, **result)
    done = queue.complete(con, source, lease, item["claim"], now=NOW, **result)
    assert done["status"] == "done" and done["replayed"] is False
    assert queue.complete(con, source, lease, item["claim"], now=NOW, **result) == {**done, "replayed": True}
    for other in ({**result, "publication_id": str(uuid.uuid4())}, {**result, "video_id": "another"}):
        with pytest.raises(SyncError) as caught:
            queue.complete(con, source, lease, item["claim"], now=NOW, **other)
        assert caught.value.code == "SYNC_OUTCOME_MISMATCH"
    with pytest.raises(SyncError):
        queue.fail(con, source, lease, item["claim"], "network", now=NOW)
    con.rollback()
    assert con.execute("SELECT status FROM sync_queue").fetchone()[0] == "claimed"


@pytest.mark.parametrize("change", [
    {"source_library_id": str(uuid.uuid4())}, {"source_node_id": str(uuid.uuid4())},
    {"source_epoch": 0}, {"source_revision": -1}, {"media_sha256": "invalid"},
    {"committed_at": "invalid"}, {"committed_at": "2999-01-01T00:00:00.000Z"},
])
def test_foreign_or_incomplete_publications_never_complete_queue(env, change):
    lease = start(env)
    item = add_claim(env, lease)
    result = publication(env, item=item, lease=lease, **change)
    with pytest.raises(SyncError) as caught:
        queue.complete(env["con"], env["source"], lease, item["claim"], now=NOW, **result)
    assert caught.value.code == "SYNC_PUBLICATION_REQUIRED"
    assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "claimed"


def test_completed_receipt_cannot_bypass_expired_or_replaced_session(env):
    con, source = env["con"], env["source"]
    old = start(env, seconds=10)
    item = add_claim(env, old)
    proof = publication(env, item=item, lease=old)
    queue.complete(con, source, old, item["claim"], now=NOW, **proof)
    con.commit()
    expired = NOW + timedelta(seconds=10)
    with pytest.raises(SyncError) as caught:
        queue.complete(con, source, old, item["claim"], now=expired, **proof)
    assert caught.value.code == "SYNC_LEASE_EXPIRED"
    fresh = queue.heartbeat(con, source, str(uuid.uuid4()), now=expired)["lease"]
    with pytest.raises(SyncError) as caught:
        queue.complete(con, source, fresh, item["claim"], now=expired, **proof)
    assert caught.value.code == "SYNC_CLAIM_MISMATCH"


def test_old_same_peer_publication_cannot_complete_unrelated_new_task(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    old = add_claim(env, lease)
    proof = publication(env, video_id="BVOldVideo", item=old, lease=lease,
                        committed_at=stamp(NOW - timedelta(days=1)))
    fresh = add_claim(env, lease)
    with pytest.raises(SyncError) as caught:
        queue.complete(con, source, lease, fresh["claim"], now=NOW, **proof)
    assert caught.value.code == "SYNC_PUBLICATION_REQUIRED"
    assert con.execute("SELECT status FROM sync_queue WHERE item_id=?", (fresh["item_id"],)).fetchone()[0] == "claimed"
    assert queue.complete(con, source, lease, old["claim"], now=NOW, **proof)["status"] == "done"


@pytest.mark.parametrize("change", [
    {"lease_generation": 2}, {"lease_token": str(uuid.uuid4())}, {"owner_id": str(uuid.uuid4())},
    {"owner_generation": 2}, {"created_at": "2999-01-01T00:00:00.000Z"},
])
def test_wrong_claim_or_session_publication_bindings_are_rejected(env, change):
    lease = start(env)
    item = add_claim(env, lease)
    proof = publication(env)
    bind_publication(env, proof, item, lease, **change)
    env["con"].commit()
    with pytest.raises(SyncError) as caught:
        queue.complete(env["con"], env["source"], lease, item["claim"], now=NOW, **proof)
    assert caught.value.code == "SYNC_PUBLICATION_REQUIRED"


def test_old_claim_publication_binding_cannot_be_used_after_reclaim(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    old = add_claim(env, lease)
    proof = publication(env, item=old, lease=lease)
    con.execute("UPDATE sync_queue SET lease_until=? WHERE item_id=?", (stamp(NOW), old["item_id"]))
    fresh = queue.claim(con, source, lease, now=NOW)[0]
    with pytest.raises(SyncError) as caught:
        queue.complete(con, source, lease, fresh["claim"], now=NOW, **proof)
    assert caught.value.code == "SYNC_PUBLICATION_REQUIRED"
    bind_publication(env, proof, fresh, lease)
    assert queue.complete(con, source, lease, fresh["claim"], now=NOW, **proof)["status"] == "done"


def test_all_mutations_preserve_personal_tables_and_caller_can_rollback_everything(env):
    con, source = env["con"], env["source"]
    personal = personal_snapshot(con)
    queue.bind_peer(con, source, now=NOW)
    lease = queue.heartbeat(con, source, str(uuid.uuid4()), now=NOW)["lease"]
    queue.enqueue(con, [URL], now=NOW)
    item = queue.claim(con, source, lease, now=NOW)[0]
    queue.fail(con, source, lease, item["claim"], "network", now=NOW)
    queue.heartbeat(con, source, lease["session_id"], lease=lease, now=NOW + timedelta(seconds=1))
    assert personal_snapshot(con) == personal
    with sqlite3.connect(env["path"]) as observer:
        assert observer.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM sync_pair").fetchone()[0] == 0
    assert con.in_transaction
    con.rollback()
    assert all(not rows for rows in sync_state(con).values())
    assert personal_snapshot(con) == personal


def test_internal_failure_rolls_back_only_current_operation(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    queue.enqueue(con, [URL, URL], now=NOW)
    con.execute("UPDATE renovation_project SET name='保留前序操作'")
    con.execute("CREATE TEMP TRIGGER fail_second BEFORE UPDATE ON sync_queue WHEN NEW.item_id=2 "
                "AND NEW.status='claimed' BEGIN SELECT RAISE(ABORT,'synthetic claim failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic claim failure"):
        queue.claim(con, source, lease, limit=2, now=NOW)
    assert [row[0] for row in con.execute("SELECT status FROM sync_queue")] == ["queued", "queued"]
    assert con.execute("SELECT name FROM renovation_project").fetchone()[0] == "保留前序操作"
    assert con.in_transaction


def test_summary_and_paginated_items_hide_tokens_queries_paths_and_raw_errors(env):
    con, source = env["con"], env["source"]
    lease = start(env)
    item = add_claim(env, lease)
    con.execute("INSERT INTO sync_queue(url,status,last_error_class,created_at,updated_at) "
                "VALUES('http://user:pass@127.0.0.1/internal?token=hidden','failed','C:/private/error-secret',"
                "'C:/private/date-secret','2026-01-01T08:00:00+08:00')")
    con.execute("INSERT INTO sync_queue(url,status,last_error_class) VALUES(?, 'queued', NULL)",
                ("https://b23.tv/video%3Ftoken%3Dencoded-secret?token=secret",))
    before = sync_state(con)
    info = queue.summary(con, now=NOW)
    assert info["paired"] and info["online"]
    assert info["counts"]["unknown"] == 1 and info["counts"]["claimed"] == 1
    page = queue.list_items(con, limit=2)
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["source_host"] == "b23.tv"
    assert page["items"][0]["source_path"] == "/video"
    assert page["items"][1]["source_host"] is None and page["items"][1]["source_path"] is None
    assert page["items"][1]["last_error_class"] == "unknown" and page["items"][1]["created_at"] is None
    tail = queue.list_items(con, offset=2, limit=2)
    assert tail["items"][0]["item_id"] == item["item_id"]
    text = json.dumps([info, page, tail])
    for secret in ("private-query", "encoded-secret", "error-secret", "date-secret", "hidden", "lease_token", lease["lease_token"], "owner_generation", "result_hash"):
        assert secret not in text
    assert sync_state(con) == before and con.in_transaction
    con.rollback()
    assert queue.summary(con, now=NOW + timedelta(seconds=600))["online"] is False


@pytest.mark.parametrize("bad", [None, [], "x", {"unknown": 1}, {"disk_free_bytes": True},
                                  {"disk_free_bytes": 10**10000}, {"queue_depth": -1},
                                  {"active_jobs": 1025}, {"cookie_state": "raw-cookie-secret"}])
def test_bad_metrics_never_change_lease(env, bad):
    lease = start(env)
    before = sync_state(env["con"])
    if bad is None:
        bad = {"disk_free_bytes": None}
    with pytest.raises(SyncError):
        queue.heartbeat(env["con"], env["source"], lease["session_id"], lease=lease, metrics=bad, now=NOW)
    assert sync_state(env["con"]) == before


@pytest.mark.parametrize("bad", [True, 0, -1, 3601, pytest.param(10**10000, id="huge-integer"), "600", None])
def test_invalid_lease_duration_is_controlled(env, bad):
    with pytest.raises(SyncError):
        queue.heartbeat(env["con"], env["source"], str(uuid.uuid4()), lease_seconds=bad, now=NOW)


@pytest.mark.parametrize("bad", [None, [], {}, True, "not-uuid", "F0000000-0000-0000-0000-000000000001"])
def test_noncanonical_session_uuid_rejected(env, bad):
    with pytest.raises(SyncError):
        queue.heartbeat(env["con"], env["source"], bad, now=NOW)


@pytest.mark.parametrize("change", [{"generation": True}, {"generation": 10**10000}, {"lease_until": "tomorrow"},
                                     {"lease_token": "bad"}, {"owner_id": "forbidden"}])
def test_invalid_session_lease_fields_are_controlled(env, change):
    lease = start(env)
    with pytest.raises(SyncError):
        queue.require_session(env["con"], env["source"], {**lease, **change}, now=NOW)


@pytest.mark.parametrize("change", [{"item_id": True}, {"item_id": 10**10000}, {"lease_generation": 0},
                                     {"lease_generation": True}, {"lease_token": "bad"}, {"status": "done"}])
def test_invalid_claim_fields_cannot_report_failure(env, change):
    lease = start(env)
    item = add_claim(env, lease)
    with pytest.raises(SyncError):
        queue.fail(env["con"], env["source"], lease, {**item["claim"], **change}, "network", now=NOW)


@pytest.mark.parametrize("bad", ["raw token=private", "lease_expired", {}, None, 42])
def test_uncontrolled_failure_text_is_never_accepted(env, bad):
    lease = start(env)
    item = add_claim(env, lease)
    with pytest.raises(SyncError):
        queue.fail(env["con"], env["source"], lease, item["claim"], bad, now=NOW)
    assert env["con"].execute("SELECT last_error_class FROM sync_queue").fetchone()[0] is None


@pytest.mark.parametrize("now", [True, "2026-01-01", datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))])
def test_invalid_clock_inputs_are_controlled(env, now):
    with pytest.raises(SyncError):
        queue.enqueue(env["con"], [URL], now=now)


@pytest.mark.parametrize("offset,limit", [(True, 1), (-1, 1), pytest.param(10**10000, 1, id="huge-offset"), (0, False), (0, 101)])
def test_list_paging_validation_is_controlled(env, offset, limit):
    with pytest.raises(SyncError):
        queue.list_items(env["con"], offset=offset, limit=limit)


def test_bounds_unknown_keys_and_empty_inputs(env):
    con, source = env["con"], env["source"]
    for urls in (URL, [URL] * 201, {}, None):
        with pytest.raises(SyncError):
            queue.enqueue(con, urls)
    assert queue.enqueue(con, [], now=NOW) == 0
    assert not con.in_transaction
    for changed in ({**source, "epoch": 1}, {**source, "node_id": True}, {"library_id": source["library_id"]}):
        with pytest.raises(SyncError):
            queue.bind_peer(con, changed, now=NOW)
    lease = start(env)
    for limit in (True, 0, 51, 10**10000):
        with pytest.raises(SyncError):
            queue.claim(con, source, lease, limit=limit, now=NOW)
    assert queue.summary(con, now=NOW)["counts"] == {key: 0 for key in (*queue.QUEUE_STATES, "unknown")}
