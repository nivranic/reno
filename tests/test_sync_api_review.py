"""Independent HTTP commit, cancellation and boundary probes on isolated data."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import threading

import anyio
import pytest

from app import sync_api
from reno import config, db, sync_media, sync_queue
from tests.test_sync_api import base, http_env, media_headers, post, pulling  # noqa: F401
from tests.test_sync_media import held_upload_lock
from tests.test_sync_transfer import CONTENT, VIDEO, URL, env  # noqa: F401


def snapshot_push(env, claims=()):
    version = db.knowledge_version(env["con"])
    return {**base(env), "kind": "snapshot", "snapshot": env["payload"], "claims": list(claims), "recovery_generation": 0,
            "recovery_stream": {key: version[key] for key in ("library_id", "library_epoch")}}


@pytest.mark.parametrize("expires", ["session", "claim"])
def test_authorization_expiring_after_domain_success_blocks_actual_http_commit(http_env, monkeypatch, expires):
    env = http_env
    sync_queue.enqueue(env["con"], [URL])
    env["con"].commit()
    item = post(env, "pull", pulling(env, 1)).json()["claims"][0]
    binding = {"video_id": VIDEO, "claim": item["claim"]}
    current = datetime.now(timezone.utc)
    if expires == "claim":
        until = current + timedelta(seconds=2)
        env["con"].execute("UPDATE sync_queue SET lease_until=?", (sync_queue._format_time(until),))
        env["con"].commit()
    else:
        until = datetime.fromisoformat(env["lease"]["lease_until"].replace("Z", "+00:00"))
    clock = {"now": current}
    original_clock, original_commit = sync_queue._clock, sync_api._authorized_commit
    monkeypatch.setattr(sync_queue, "_clock", lambda value: original_clock(clock["now"] if value is None else value))

    def expire_before_commit(con, source, lease, claims=()):
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1
        assert con.execute("SELECT status FROM sync_queue").fetchone()[0] == "done"
        clock["now"] = until
        return original_commit(con, source, lease, claims)

    monkeypatch.setattr(sync_api, "_authorized_commit", expire_before_commit)
    response = post(env, "push", snapshot_push(env, [binding]))
    assert response.status_code == 409
    assert response.json()["code"] == ("SYNC_LEASE_EXPIRED" if expires == "session" else "SYNC_CLAIM_EXPIRED")
    assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
    assert env["con"].execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0
    assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "claimed"


@pytest.mark.parametrize("operation", ["snapshot", "heartbeat"])
def test_actual_sqlite_commit_failure_rolls_back_and_closes_connection(http_env, monkeypatch, operation):
    env = http_env
    before = [tuple(row) for row in env["con"].execute("SELECT * FROM sync_session")]
    real_connect = db.connect
    calls = []

    class FailingCommit:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            calls.append("commit")
            raise sqlite3.OperationalError("controlled private path and secret commit failure")

        def rollback(self):
            calls.append("rollback")
            self.connection.rollback()

        def close(self):
            calls.append("close")
            self.connection.close()

    monkeypatch.setattr(db, "connect", lambda *args, **kwargs: FailingCommit(real_connect(*args, **kwargs)))
    if operation == "snapshot":
        response = post(env, "push", snapshot_push(env))
    else:
        response = post(env, "heartbeat", {**base(env), "session_id": env["lease"]["session_id"],
                                            "metrics": {"queue_depth": 19}})
    assert response.status_code == 503 and response.json()["code"] == "SYNC_STORAGE_FAILED"
    assert "private" not in response.text and "secret" not in response.text and "publication_id" not in response.text
    assert calls == ["commit", "rollback", "close"]
    assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
    assert env["con"].execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0
    assert [tuple(row) for row in env["con"].execute("SELECT * FROM sync_session")] == before


def media_scope(headers):
    pairs = list(headers.items()) if isinstance(headers, dict) else headers
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "https", "path": "/api/sync/media", "raw_path": b"/api/sync/media", "query_string": b"",
            "root_path": "", "headers": [(name.lower().encode(), value.encode()) for name, value in pairs],
            "client": ("127.0.0.1", 43210), "server": ("review.test", 443)}


def assert_upload_capacity_is_restored():
    acquired = []
    try:
        for _ in range(2):
            acquired.append(sync_api._uploads.acquire(False))
        assert acquired == [True, True]
        assert not sync_api._uploads.acquire(False)
    finally:
        for held in acquired:
            if held:
                sync_api._uploads.release()


@pytest.mark.parametrize("method", ["anyio_scope", "asyncio_task"])
def test_cancelled_asgi_request_while_os_lock_is_held_finishes_worker_before_releasing_slot(http_env, monkeypatch, method):
    env = http_env
    contended, completed, emergency_stop = threading.Event(), threading.Event(), threading.Event()
    original_sleep, original_receive = sync_media.sleep, sync_media.MediaStore.receive

    def observed_sleep(seconds):
        contended.set()
        original_sleep(seconds)

    def observed_receive(store, descriptor, chunks, *, check_cancelled=None):
        def checked():
            if emergency_stop.is_set():
                raise asyncio.CancelledError("Review test cleanup")
            check_cancelled()
        try:
            return original_receive(store, descriptor, chunks, check_cancelled=checked)
        finally:
            completed.set()

    monkeypatch.setattr(sync_media, "sleep", observed_sleep)
    monkeypatch.setattr(sync_media.MediaStore, "receive", observed_receive)
    headers = media_headers(env)
    premature = []

    async def request():
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": CONTENT, "more_body": False}
            await anyio.sleep_forever()

        async def send(_message):
            pass

        await env["app"](media_scope(headers), receive, send)

    async def wait_until_contended():
        with anyio.fail_after(3):
            while not contended.is_set():
                await anyio.sleep(0.01)

    async def run():
        try:
            if method == "anyio_scope":
                async with anyio.create_task_group() as group:
                    group.start_soon(request)
                    await wait_until_contended()
                    group.cancel_scope.cancel()
            else:
                task = asyncio.create_task(request())
                await wait_until_contended()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            premature.append(not completed.is_set())
        finally:
            emergency_stop.set()
            with anyio.fail_after(3):
                while not completed.is_set():
                    await anyio.sleep(0.01)

    with held_upload_lock(env["store"].root, "thread"):
        anyio.run(run)
        assert completed.is_set()
        assert premature == [False], "Request returned/released capacity while its upload worker remained alive"
        assert not list((config.MEDIA / ".sync-tmp").glob("*.part"))
        assert_upload_capacity_is_restored()


def test_media_idle_timeout_returns_retryable_408_and_cleans_partial_bytes(http_env, monkeypatch):
    env = http_env
    content = b"synthetic slow interrupted media"
    descriptor = {**env["media"], "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content)}
    intent = json.loads(base64.urlsafe_b64decode(media_headers(env, descriptor)["X-Sync-Media"]))["intent"]
    from reno.sync_upload_pins import BINDING_FIELDS
    assert post(env, "uploads/prepare", {**base(env), "binding": {k: intent[k] for k in BINDING_FIELDS}, "descriptors": [descriptor]}).status_code == 200
    monkeypatch.setattr(sync_api, "MEDIA_IDLE_SECONDS", 0.03)
    sent = []

    async def run():
        first = True
        response_done = anyio.Event()

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": content[:5], "more_body": True}
            await response_done.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_done.set()

        with anyio.fail_after(3):
            await env["app"](media_scope(media_headers(env, descriptor)), receive, send)

    anyio.run(run)
    response = next(message for message in sent if message["type"] == "http.response.start")
    body = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
    assert response["status"] == 408 and body["code"] in {"SYNC_BODY_INTERRUPTED", "SYNC_UPLOAD_TIMEOUT"}
    assert not list((config.MEDIA / ".sync-tmp").glob("*.part"))
    assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
    assert_upload_capacity_is_restored()


@pytest.mark.parametrize("attack", ["duplicate_header", "duplicate_json", "extra_root", "descriptor_path",
                                      "unpadded", "noncanonical_pad_bits", "gzip", "duplicate_length"])
def test_ambiguous_or_privileged_media_headers_are_rejected_before_domain_work(http_env, monkeypatch, attack):
    env = http_env
    value = json.loads(base64.urlsafe_b64decode(media_headers(env)["X-Sync-Media"]))
    if attack == "extra_root":
        value["bind_peer"] = True
    if attack == "descriptor_path":
        value["intent"]["descriptor"] = {**env["media"], "storage_key": "../personal-attachments/private.pdf"}
    raw = json.dumps(value).encode()
    if attack == "duplicate_json":
        raw = raw[:-1] + b',"intent":{}}'
    if attack in ("unpadded", "noncanonical_pad_bits"):
        while len(raw) % 3 != 1:
            raw += b" "
    encoded = base64.urlsafe_b64encode(raw).decode()
    if attack == "unpadded":
        encoded = encoded.rstrip("=")
    if attack == "noncanonical_pad_bits":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        encoded = encoded[:-3] + alphabet[alphabet.index(encoded[-3]) + 1] + "=="
    headers = [("X-Sync-Token", "test-sync-secret"), ("Content-Type", "application/octet-stream"),
               ("X-Sync-Media", encoded)]
    if attack == "duplicate_header":
        headers.append(("X-Sync-Media", encoded))
    if attack == "duplicate_length":
        headers.extend([("Content-Length", str(len(CONTENT)))] * 2)
    if attack == "gzip":
        headers.append(("Content-Encoding", "gzip"))
    invoked = []

    def forbidden(*args, **kwargs):
        invoked.append(True)
        raise AssertionError("Rejected media headers must not reach storage or publication")

    monkeypatch.setattr(sync_api.sync_transfer, "receive_media", forbidden)
    response = env["client"].post("/api/sync/media", headers=headers, content=CONTENT)
    assert response.status_code == (415 if attack == "gzip" else 400)
    assert response.json()["code"] == "INVALID_SYNC_REQUEST" and not invoked
    assert "test-sync-secret" not in response.text and "private.pdf" not in response.text
    assert_upload_capacity_is_restored()


@pytest.mark.parametrize("setting,value,expected_status", [
    ("sync_token", "", 401), ("sync_token", 123, 401), ("node_role", "home", 403),
])
def test_browser_status_matches_disabled_receiver_access_even_with_previous_token(http_env, monkeypatch, setting, value, expected_status):
    env = http_env
    # Token rotation reads this test's isolated file on every request, even
    # while the ordinary config cache still holds the previous valid token.
    local = config.local_path()
    settings = json.loads(local.read_text(encoding="utf-8"))
    settings.update(sync_previous_token="previous-synthetic-secret",
                    sync_previous_token_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    settings[setting] = value
    local.write_text(json.dumps(settings), encoding="utf-8")
    if setting == "node_role":
        monkeypatch.setitem(config._cache, setting, value)
    status = env["client"].get("/api/sync-status", headers={"X-Reno-Token": "test-browser-secret"})
    assert status.status_code == 200
    assert not status.json()["configured"] and not status.json()["online"]
    for token in ("test-sync-secret", "previous-synthetic-secret"):
        rejected = env["client"].get("/api/sync/handshake", headers={"X-Sync-Token": token})
        assert rejected.status_code == expected_status
        assert token not in rejected.text and token not in status.text
