"""Isolated real HTTP/pair/session/files; no external server or model calls."""
import asyncio
import base64
from copy import deepcopy
from datetime import timedelta
import json
import threading
import time

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import personal_mirror_api as api
from app.personal_api import router as personal_router
from reno import config, db, personal, sync_queue as queue
from reno import personal_mirror_transfer as runtime
from reno import personal_mirror_transfer_contract as wire, personal_mirror_transfer_files as files
from reno import personal_mirror_transfer_schema as schema
from tests.test_personal_mirror_transfer import (BODY, NOW, commit_selector, make_prepare,
    mirror_env, table_state, uid)  # noqa: F401

PREFIX = "/api/sync/personal-mirror"
TOKEN = "synthetic-personal-mirror-sync-token"


def configure(env, **changes):
    local = env["target_root"] / "config.synthetic.json"
    value = json.loads(local.read_text(encoding="utf-8"))
    value.update({"sync_token": TOKEN, **changes})
    local.write_text(json.dumps(value), encoding="utf-8")
    config._cache = None


@pytest.fixture
def http_env(mirror_env, monkeypatch):
    env = mirror_env
    configure(env)
    clock = queue._clock
    monkeypatch.setattr(queue, "_clock", lambda value: clock(NOW if value is None else value))
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(personal_router)
    env["app"] = app
    with TestClient(app, base_url="https://mirror.example.test") as client:
        env["client"] = client
        yield env


def base(env):
    return {"source": deepcopy(env["source"]), "lease": deepcopy(env["lease"])}


def post(env, path, payload, *, headers=None):
    return env["client"].post(PREFIX + "/" + path, json=payload,
        headers={"X-Sync-Token": TOKEN, **(headers or {})})


def selected(env, prepare=None):
    return {**base(env), "commit": commit_selector(prepare or env["prepare"])}


def prepare(env, value=None):
    result = post(env, "prepare", {**base(env), "prepare": value or env["prepare"]})
    assert result.status_code == 200, result.text
    return result


def attachment_prepare(env):
    value = make_prepare(env, with_attachment=True)
    configure(env)
    prepare(env, value)
    return value


def attachment_headers(env, value, *, selector=None):
    descriptor = wire.attachment_manifest(value)[0]
    body = selector or {**base(env), "operation_id": value["operation_id"],
        "prepare_sha256": wire.prepare_sha256(value), "attachment_id": descriptor["attachment_id"]}
    encoded = base64.urlsafe_b64encode(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    return {"X-Sync-Token": TOKEN, "Content-Type": "application/octet-stream", "X-Reno-Personal-Mirror": encoded}


def assert_capacity_restored():
    held = []
    try:
        held = [api._requests.acquire(False), api._requests.acquire(False)]
        assert held == [True, True]
        assert not api._requests.acquire(False)
    finally:
        for acquired in held:
            if acquired:
                api._requests.release()


def test_receipt_absence_and_prepared_absence_are_actual_read_only_null(http_env):
    env = http_env
    before = table_state(env)
    response = post(env, "receipt", selected(env))
    assert response.status_code == 200 and response.json() == {"receipt": None}
    assert table_state(env) == before
    first = prepare(env)
    frozen = table_state(env)
    assert first.json()["state"] == "prepared"
    assert post(env, "receipt", selected(env)).json() == {"receipt": None}
    assert table_state(env) == frozen


def test_zero_attachment_http_commit_and_original_receipt_replay_keep_cloud_reading_closed(http_env):
    env = http_env
    node_before = (env["data"] / "node-identity.json").read_bytes()
    first = prepare(env)
    assert prepare(env).json() == first.json()
    result = post(env, "commit", selected(env))
    assert result.status_code == 200 and result.json()["replayed"] is False
    frozen = table_state(env)
    receipt = post(env, "receipt", selected(env))
    replay = post(env, "commit", selected(env))
    assert receipt.json() == {"receipt": {**result.json(), "replayed": True}}
    assert replay.json() == receipt.json()["receipt"]
    assert table_state(env) == frozen
    assert (env["data"] / "node-identity.json").read_bytes() == node_before
    con = db.connect(env["profile"].database)
    try:
        assert db.knowledge_version(con)["library_id"] == env["receiver"]["library_id"]
        assert personal.capabilities(con)["personal_read"] is False
        assert personal.capabilities(con)["personal_write"] is False
    finally:
        con.close()
    assert env["client"].get(f'/api/projects/{env["project_id"]}/sources').status_code == 403
    assert result.headers["cache-control"] == "private, no-store"


def test_actual_ready_attachment_is_streamed_recoverable_and_only_commit_installs(http_env):
    env = http_env
    value = attachment_prepare(env)
    descriptor = wire.attachment_manifest(value)[0]
    response = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value),
        content=iter([BODY[:7], BODY[7:]]))
    assert response.status_code == 200 and response.json()["state"] == "stored"
    path = env["data"] / "personal-attachments" / descriptor["storage_key"]
    original_inode = path.stat().st_ino
    assert path.read_bytes() == BODY and table_state(env)[schema.HEAD] == []
    recovery = env["client"].post(PREFIX + "/attachment",
        headers={**attachment_headers(env, value), "Content-Length": "0"}, content=b"")
    assert recovery.status_code == 200 and recovery.json() == response.json()
    assert path.stat().st_ino == original_inode
    result = post(env, "commit", selected(env, value))
    assert result.status_code == 200 and result.json()["counts"]["attachment_manifest"] == 1
    assert post(env, "receipt", selected(env, value)).json()["receipt"]["receipt_sha256"] == result.json()["receipt_sha256"]


def test_recovery_does_not_create_an_absent_body_and_missing_attachment_blocks_install(http_env):
    env = http_env
    value = attachment_prepare(env)
    before = table_state(env)
    missing = env["client"].post(PREFIX + "/attachment",
        headers={**attachment_headers(env, value), "Content-Length": "0"}, content=b"")
    assert missing.status_code == 400
    rejected = post(env, "commit", selected(env, value))
    assert rejected.status_code == 409 and table_state(env) == before


@pytest.mark.parametrize("damage", ["unpaired", "wrong-peer", "wrong-lease", "expired"])
def test_original_receipt_absence_never_converts_real_admission_failure_into_null(http_env, damage):
    env = http_env
    body = selected(env)
    con = db.connect(env["profile"].database)
    try:
        if damage == "unpaired":
            con.execute("DELETE FROM sync_session")
            con.execute("DELETE FROM sync_pair")
        elif damage == "wrong-peer":
            body["source"]["node_id"] = uid()
        elif damage == "wrong-lease":
            body["lease"]["lease_token"] = uid()
        else:
            con.execute("UPDATE sync_session SET lease_until=?", (queue._format_time(NOW),))
        con.commit()
    finally:
        con.close()
    before = table_state(env)
    response = post(env, "receipt", body)
    assert response.status_code == 409 and "receipt" not in response.json()
    assert table_state(env) == before


@pytest.mark.parametrize("gate,expected", [("missing-token", 401), ("query-token", 401),
    ("browser-token", 401), ("duplicate-token", 401), ("disabled", 403), ("home", 403), ("all", 403)])
def test_router_self_gates_before_json_or_facility_work_without_main_middleware(http_env, gate, expected):
    env = http_env
    headers = {"Content-Type": "application/json", "X-Sync-Token": TOKEN}
    path = PREFIX + "/prepare"
    if gate == "missing-token":
        headers.pop("X-Sync-Token")
    elif gate == "query-token":
        headers.pop("X-Sync-Token")
        path += "?token=" + TOKEN
    elif gate == "browser-token":
        headers.pop("X-Sync-Token")
        headers["X-Reno-Token"] = TOKEN
    elif gate == "duplicate-token":
        headers = [*headers.items(), ("X-Sync-Token", TOKEN)]
    elif gate == "disabled":
        configure(env, personal_mirror_enabled=False)
    else:
        configure(env, node_role=gate)
    before = table_state(env)
    response = env["client"].post(path, headers=headers, content=b"{invalid")
    assert response.status_code == expected and TOKEN not in response.text
    assert table_state(env) == before


def test_transport_requires_actual_https_and_ignores_forwarded_proto(http_env):
    env = http_env
    with TestClient(env["app"], base_url="http://mirror.example.test") as client:
        response = client.post(PREFIX + "/receipt", json=selected(env),
            headers={"X-Sync-Token": TOKEN, "X-Forwarded-Proto": "https"})
    assert response.status_code == 403 and response.json()["code"] == "SYNC_HTTPS_REQUIRED"


@pytest.mark.parametrize("raw", [b"[]", b"{bad", b'{"source":{},"source":{}}',
    b'{"value":NaN}', b'{"value":1e999}', b'{"value":"\\ud800"}'])
def test_malformed_json_is_bounded_and_does_not_expose_input(http_env, raw):
    env = http_env
    before = table_state(env)
    response = env["client"].post(PREFIX + "/prepare", headers={"X-Sync-Token": TOKEN,
        "Content-Type": "application/json"}, content=raw)
    assert response.status_code == 400 and TOKEN not in response.text
    assert table_state(env) == before


@pytest.mark.parametrize("extra", [{"now": "2099-01-01"}, {"bind_peer": True},
    {"token": TOKEN}, {"profile": {"database": "private.db"}}, {"capability": True}])
def test_control_rejects_caller_clocks_credentials_and_permission_dtos(http_env, extra):
    env = http_env
    response = post(env, "prepare", {**base(env), "prepare": env["prepare"], **extra})
    assert response.status_code == 400 and table_state(env)[schema.INTENT] == []


def test_selector_change_and_missing_parent_cannot_upload_or_replay(http_env):
    env = http_env
    value = make_prepare(env, with_attachment=True)
    configure(env)
    missing = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value), content=BODY)
    assert missing.status_code == 404
    prepare(env, value)
    before = table_state(env)
    bad = selected(env, value)
    bad["commit"]["prepare_sha256"] = "f" * 64
    for endpoint in ("receipt", "commit"):
        response = post(env, endpoint, bad)
        assert response.status_code == 409 and "receipt" not in response.json()
    assert table_state(env) == before


@pytest.mark.parametrize("attack", ["duplicate-header", "duplicate-length", "descriptor-dto", "noncanonical", "gzip", "oversized"])
def test_attachment_ambiguity_and_descriptor_permission_dtos_are_rejected(http_env, attack):
    env = http_env
    value = attachment_prepare(env)
    headers = attachment_headers(env, value)
    if attack == "duplicate-header":
        headers = [*headers.items(), ("X-Reno-Personal-Mirror", headers["X-Reno-Personal-Mirror"])]
    elif attack == "duplicate-length":
        headers = [*headers.items(), ("Content-Length", str(len(BODY))), ("Content-Length", str(len(BODY)))]
    elif attack == "descriptor-dto":
        body = json.loads(base64.urlsafe_b64decode(headers["X-Reno-Personal-Mirror"]))
        body["descriptor"] = wire.attachment_manifest(value)[0]
        headers["X-Reno-Personal-Mirror"] = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    elif attack == "noncanonical":
        raw = base64.urlsafe_b64decode(headers["X-Reno-Personal-Mirror"])
        raw += b" " * ((1 - len(raw) % 3) % 3)
        headers["X-Reno-Personal-Mirror"] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    elif attack == "gzip":
        headers["Content-Encoding"] = "gzip"
    else:
        headers["X-Reno-Personal-Mirror"] = "a" * (api.MAX_ATTACHMENT_HEADER + 1)
    before = table_state(env)
    response = env["client"].post(PREFIX + "/attachment", headers=headers, content=BODY)
    assert response.status_code in {400, 415} and table_state(env) == before


@pytest.mark.parametrize("body,expected", [(BODY[:-1], 400), (BODY + b"x", 400), (b"x" * len(BODY), 422)])
def test_bad_attachment_body_cannot_install_or_leave_partial_bytes(http_env, body, expected):
    env = http_env
    value = attachment_prepare(env)
    before = table_state(env)
    response = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value), content=body)
    assert response.status_code == expected and table_state(env) == before
    assert not list((env["data"] / "personal-attachments" / ".mirror-staging").glob("*.part"))


@pytest.mark.parametrize("body,expected", [(BODY[:-1], 422), (BODY + b"x", 413)])
def test_chunked_body_is_bounded_by_actual_persisted_descriptor(http_env, body, expected):
    env = http_env
    value = attachment_prepare(env)
    before = table_state(env)
    response = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value),
        content=iter([body[:5], body[5:]]))
    assert response.status_code == expected and table_state(env) == before
    assert not list((env["data"] / "personal-attachments" / ".mirror-staging").glob("*.part"))


def stored_original(env, value):
    answer = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value), content=BODY)
    assert answer.status_code == 200, answer.text
    descriptor = wire.attachment_manifest(value)[0]
    path = env["data"] / "personal-attachments" / descriptor["storage_key"]
    info = path.stat()
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return path, signature, table_state(env), answer.json()


def assert_stored_original_kept(env, path, signature, rows):
    info = path.stat()
    assert (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) == signature
    assert path.read_bytes() == BODY and table_state(env) == rows


@pytest.mark.parametrize("body,expected", [(BODY + b"x", 413), (b"x" * len(BODY), 422), (BODY[:-1], 422)])
def test_existing_stored_body_does_not_bypass_stream_size_or_sha_validation(http_env, body, expected):
    env = http_env
    value = attachment_prepare(env)
    path, signature, rows, _result = stored_original(env, value)
    response = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value),
        content=iter([body[:5], body[5:]]))
    assert response.status_code == expected
    assert_stored_original_kept(env, path, signature, rows)
    assert_capacity_restored()


@pytest.mark.parametrize("chunked", [False, True])
def test_correct_existing_body_retransmission_preserves_file_and_sql_facts(http_env, chunked):
    env = http_env
    value = attachment_prepare(env)
    path, signature, rows, result = stored_original(env, value)
    content = iter([BODY[:5], BODY[5:]]) if chunked else BODY
    response = env["client"].post(PREFIX + "/attachment", headers=attachment_headers(env, value), content=content)
    assert response.status_code == 200 and response.json() == result
    assert_stored_original_kept(env, path, signature, rows)


def test_http_request_does_not_install_missing_operational_facilities(http_env):
    env = http_env
    con = db.connect(env["profile"].database)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        triggers = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_personal_mirror_%'")]
        for trigger in triggers:
            con.execute(f'DROP TRIGGER "{trigger}"')
        for table in reversed(tuple(schema.TABLES)):
            con.execute(f'DROP TABLE "{table}"')
        con.commit()
    finally:
        con.close()
    for endpoint, payload in (("prepare", {**base(env), "prepare": env["prepare"]}), ("receipt", selected(env))):
        response = post(env, endpoint, payload)
        assert response.status_code == 409 and "receipt" not in response.json()
    con = db.connect(env["profile"].database)
    try:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (schema.INTENT,)).fetchone() is None
    finally:
        con.close()


def test_request_limits_and_capacity_reject_before_creation(http_env, monkeypatch):
    env = http_env
    monkeypatch.setattr(api, "MAX_PREPARE_BYTES", 128)
    response = post(env, "prepare", {**base(env), "prepare": env["prepare"]})
    assert response.status_code == 413 and table_state(env)[schema.INTENT] == []
    assert api._requests.acquire(False) and api._requests.acquire(False)
    try:
        response = post(env, "receipt", selected(env))
        assert response.status_code == 429 and response.json()["code"] == "PERSONAL_MIRROR_BUSY"
    finally:
        api._requests.release()
        api._requests.release()
    assert_capacity_restored()


@pytest.mark.parametrize("failure", ["token", "lease"])
def test_fresh_authorization_after_installation_prevents_actual_http_commit(http_env, monkeypatch, failure):
    env = http_env
    prepare(env)
    before = table_state(env)
    actual = runtime._install_personal_rows
    clock = queue._clock
    current = [NOW]
    monkeypatch.setattr(queue, "_clock", lambda value: clock(current[0] if value is None else value))
    def revoke(con, document, *, check_cancelled):
        actual(con, document, check_cancelled=check_cancelled)
        if failure == "token":
            configure(env, sync_token="synthetic-rotated-token")
        else:
            current[0] = NOW + timedelta(seconds=600)
    monkeypatch.setattr(runtime, "_install_personal_rows", revoke)
    response = post(env, "commit", selected(env))
    assert response.status_code == (401 if failure == "token" else 409)
    assert table_state(env) == before and not runtime.in_write_window()


@pytest.mark.parametrize("error", [OSError("synthetic private path and secret"), RuntimeError("synthetic private path and secret")])
def test_storage_and_unexpected_errors_are_sanitized_without_receipts(http_env, monkeypatch, error):
    env = http_env
    def fail(**_kwargs):
        raise error
    monkeypatch.setattr(runtime, "prepare_mirror_locked", fail)
    response = post(env, "prepare", {**base(env), "prepare": env["prepare"]})
    assert response.status_code == (503 if isinstance(error, OSError) else 500)
    assert "private" not in response.text and "secret" not in response.text and TOKEN not in response.text
    assert table_state(env)[schema.INTENT] == []
    assert_capacity_restored()


def asgi_scope(headers):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": PREFIX + "/attachment", "raw_path": (PREFIX + "/attachment").encode(),
        "query_string": b"", "root_path": "", "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 43210), "server": ("mirror.example.test", 443)}


@pytest.mark.parametrize("interruption", ["idle", "disconnect"])
def test_interrupted_actual_asgi_upload_finishes_and_keeps_original_reserved(http_env, monkeypatch, interruption):
    env = http_env
    value = attachment_prepare(env)
    before = table_state(env)
    monkeypatch.setattr(api, "ATTACHMENT_IDLE_SECONDS", 0.03)
    sent = []
    async def run():
        first = True
        response_done = anyio.Event()
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": BODY[:5], "more_body": True}
            if interruption == "idle":
                await response_done.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_done.set()
        with anyio.fail_after(30):
            await env["app"](asgi_scope(attachment_headers(env, value)), receive, send)
    anyio.run(run)
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 408
    assert table_state(env) == before
    assert not list((env["data"] / "personal-attachments" / ".mirror-staging").glob("*.part"))
    assert_capacity_restored()


@pytest.mark.parametrize("interruption", ["idle", "disconnect"])
def test_existing_stored_body_retransmission_still_observes_actual_asgi_interruption(http_env, monkeypatch, interruption):
    env = http_env
    value = attachment_prepare(env)
    path, signature, rows, _result = stored_original(env, value)
    monkeypatch.setattr(api, "ATTACHMENT_IDLE_SECONDS", 0.03)
    sent = []
    async def run():
        first = True
        response_done = anyio.Event()
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": BODY[:5], "more_body": True}
            if interruption == "idle":
                await response_done.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_done.set()
        with anyio.fail_after(30):
            await env["app"](asgi_scope(attachment_headers(env, value)), receive, send)
    anyio.run(run)
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 408
    assert_stored_original_kept(env, path, signature, rows)
    assert_capacity_restored()


@pytest.mark.parametrize("method", ["anyio-scope", "asyncio-task"])
def test_cancelled_request_waits_for_its_actual_storage_worker_before_releasing_capacity(http_env, monkeypatch, method):
    env = http_env
    value = attachment_prepare(env)
    before = table_state(env)
    entered, completed, emergency = threading.Event(), threading.Event(), threading.Event()
    watchdog = threading.Timer(5, emergency.set)
    watchdog.daemon = True
    def waiting(_directory, _name, _chunks, _descriptor, check):
        try:
            entered.set()
            while not emergency.is_set():
                check()
                time.sleep(0.005)
            raise RuntimeError("synthetic emergency worker cleanup")
        finally:
            completed.set()
    monkeypatch.setattr(files, "_write_temp", waiting)
    async def request():
        async def receive():
            return {"type": "http.request", "body": BODY, "more_body": False}
        async def send(_message):
            pass
        await env["app"](asgi_scope(attachment_headers(env, value)), receive, send)
    async def wait_entered():
        with anyio.fail_after(30):
            while not entered.is_set():
                await anyio.sleep(0.005)
    async def run():
        if method == "anyio-scope":
            async with anyio.create_task_group() as group:
                group.start_soon(request)
                await wait_entered()
                watchdog.start()
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(request())
            await wait_entered()
            watchdog.start()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert completed.is_set(), "Request returned while its storage writer remained live"
    try:
        anyio.run(run)
        assert not emergency.is_set(), "Cancellation failed to stop the original worker before emergency cleanup"
    finally:
        emergency.set()
        watchdog.cancel()
    assert table_state(env) == before
    assert_capacity_restored()
