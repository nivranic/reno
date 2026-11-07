"""Transport, rotating credentials and local pairing on isolated configuration."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from reno import cli, config, db, sync_access as access, sync_queue
from reno.sync_protocol import SyncError
from tests.test_sync_events import personal_snapshot

TOKEN = "artificial-primary-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "access-qa"
    root.mkdir()
    local = root / "config.test.json"
    local.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RENO_QA_ROOT", str(root))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "FRAMES": root / "media/frames",
                       "AUDIO": root / "media/audio", "ORIG": root / "media/originals", "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    config.set_local({"node_role": "all", "sync_token": TOKEN, "web_ui": "classic", "auth_token": ""})
    db.initialize()
    config.set_local({"node_role": "cloud"})
    yield {"root": root, "local": local, "db": config.db_path()}


def request(*, scheme="https", client="127.0.0.1", token=TOKEN, headers=(), query=b""):
    values = [(b"host", b"testserver"), *headers]
    if token is not None:
        values.append((b"x-sync-token", token.encode("latin-1") if isinstance(token, str) else token))
    return Request({"type": "http", "http_version": "1.1", "method": "GET", "path": "/api/sync/handshake",
                    "raw_path": b"/api/sync/handshake", "query_string": query, "root_path": "", "headers": values,
                    "scheme": scheme, "server": ("testserver", 443), "client": (client, 50000)})


def source_identity(env):
    path = env["root"] / "source/data/reno.db"
    db.initialize(path)
    con = db.connect(path)
    try:
        return access.identity(con)
    finally:
        con.close()


def utc(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.mark.parametrize("token", [None, "", "wrong", b"\xff", b"a" * 5000])
def test_authentication_rejects_missing_wrong_nonascii_or_oversized_header(env, token):
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request(token=token, scheme="http"))
    assert caught.value.status == 401 and caught.value.code == "SYNC_UNAUTHORIZED"


def test_query_and_duplicate_headers_never_authenticate(env):
    for req in (request(token=None, query=f"token={TOKEN}".encode()),
                request(token="wrong", query=f"token={TOKEN}".encode()),
                request(headers=[(b"x-sync-token", TOKEN.encode())])):
        with pytest.raises(SyncError) as caught:
            access.require_sync_access(req)
        assert caught.value.code == "SYNC_UNAUTHORIZED"


@pytest.mark.parametrize("role", ["all", "home", "unknown"])
def test_authenticated_sync_is_only_available_on_cloud(env, role):
    config.set_local({"node_role": role})
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request())
    assert caught.value.code == "SYNC_CLOUD_REQUIRED"


def test_production_requires_scope_https_and_ignores_forwarded_headers(env):
    access.require_sync_access(request())
    for headers in ([], [(b"x-forwarded-proto", b"https"), (b"x-forwarded-host", b"secure.invalid")],
                    [(b"forwarded", b"for=127.0.0.1;proto=https"), (b"host", b"https://secure.invalid")]):
        with pytest.raises(SyncError) as caught:
            access.require_sync_access(request(scheme="http", headers=headers))
        assert caught.value.code == "SYNC_HTTPS_REQUIRED"


@pytest.mark.parametrize("client", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"])
def test_explicit_http_exception_needs_real_qa_isolation_and_local_client(env, client):
    config.set_local({"sync_allow_insecure_test_transport": True})
    assert config.is_isolated_qa()
    access.require_sync_access(request(scheme="http", client=client))


@pytest.mark.parametrize("client", ["203.0.113.1", "192.168.1.2", "localhost", "not-an-address"])
def test_qa_flag_does_not_allow_external_or_forged_host_clients(env, client):
    config.set_local({"sync_allow_insecure_test_transport": True})
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request(scheme="http", client=client,
                                           headers=[(b"host", b"testclient"), (b"x-forwarded-for", b"127.0.0.1")]))
    assert caught.value.code == "SYNC_HTTPS_REQUIRED"


@pytest.mark.parametrize("failure", ["no-root", "data-mismatch", "config-outside", "media-outside", "missing-config", "malformed-config"])
def test_qa_http_exception_fails_closed_when_any_isolation_proof_is_missing(env, monkeypatch, failure):
    config.set_local({"sync_allow_insecure_test_transport": True})
    config.get()  # Exercise the stale-cache case; path proof must be per request.
    if failure == "no-root":
        monkeypatch.delenv("RENO_QA_ROOT")
    elif failure == "data-mismatch":
        monkeypatch.setenv("RENO_DATA_DIR", str(env["root"] / "different"))
    elif failure == "config-outside":
        monkeypatch.setenv("RENO_CONFIG_FILE", str(env["root"].parent / "outside.json"))
    elif failure == "media-outside":
        monkeypatch.setattr(config, "MEDIA", env["root"].parent / "outside-media")
    elif failure == "missing-config":
        env["local"].unlink()
    else:
        env["local"].write_text("{broken", encoding="utf-8")
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request(scheme="http"))
    expected = "SYNC_ACCESS_UNAVAILABLE" if failure in {"config-outside", "media-outside", "missing-config", "malformed-config"} else "SYNC_HTTPS_REQUIRED"
    assert caught.value.code == expected


@pytest.mark.parametrize("flag", [False, "false", "0", 1, {}, None])
def test_flag_values_do_not_gain_truthiness_based_http_access(env, flag):
    config.set_local({"sync_allow_insecure_test_transport": flag})
    with pytest.raises(SyncError):
        access.require_sync_access(request(scheme="http"))


def test_current_and_previous_tokens_rotate_without_restarting_the_app(env):
    from app.main import create_app
    client = TestClient(create_app(), base_url="https://testserver")
    assert client.get("/api/sync/handshake", headers={"X-Sync-Token": TOKEN}).status_code == 200
    config.set_local({"sync_token": "new-primary", "sync_previous_token": TOKEN,
                      "sync_previous_token_until": utc(datetime.now(timezone.utc) + timedelta(hours=1))})
    for token in (TOKEN, "new-primary"):
        assert client.get("/api/sync/handshake", headers={"X-Sync-Token": token}).status_code == 200
    config.set_local({"sync_previous_token_until": utc(datetime.now(timezone.utc) - timedelta(seconds=1))})
    assert client.get("/api/sync/handshake", headers={"X-Sync-Token": TOKEN}).status_code == 401
    assert client.get("/api/sync/handshake", headers={"X-Sync-Token": "new-primary"}).status_code == 200


@pytest.mark.parametrize("until", ["", "not-time", "2026-01-01T00:00:00", "2026-01-01T08:00:00+08:00",
                                    "2999-01-01T00:00:00Z", "2026-02-30T00:00:00Z"])
def test_previous_token_requires_a_bounded_utc_expiry(env, until):
    config.set_local({"sync_previous_token": "old-token", "sync_previous_token_until": until})
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request(token="old-token"))
    assert caught.value.code == "SYNC_UNAUTHORIZED"


def test_expiry_boundary_and_24_hour_maximum_are_strict():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not access._previous_active(utc(now), now)
    assert access._previous_active(utc(now + timedelta(milliseconds=1)), now)
    assert access._previous_active(utc(now + timedelta(hours=24)), now)
    assert not access._previous_active(utc(now + timedelta(hours=24, milliseconds=1)), now)


@pytest.mark.parametrize("current", ["", None, False, [], 42])
def test_previous_token_never_opens_a_disabled_primary(env, current):
    config.set_local({"sync_token": current, "sync_previous_token": "old-token",
                      "sync_previous_token_until": utc(datetime.now(timezone.utc) + timedelta(hours=1))})
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request(token="old-token"))
    assert caught.value.code == "SYNC_UNAUTHORIZED"


def test_both_token_comparisons_use_bytes_and_constant_time_primitive(env, monkeypatch):
    calls = []
    original = access.hmac.compare_digest

    def compare(a, b):
        calls.append((a, b))
        assert isinstance(a, bytes) and isinstance(b, bytes)
        return original(a, b)

    monkeypatch.setattr(access.hmac, "compare_digest", compare)
    access.require_sync_access(request())
    assert len(calls) == 2


def test_configuration_errors_do_not_expose_paths_or_secrets(env, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("private-config C:/secret token=hidden")

    monkeypatch.setattr(config, "sync_access_settings", broken)
    with pytest.raises(SyncError) as caught:
        access.require_sync_access(request())
    assert caught.value.code == "SYNC_ACCESS_UNAVAILABLE"
    assert all(value not in str(caught.value) for value in ("private", "secret", "hidden"))


def test_external_atomic_config_rotation_is_visible_without_clearing_process_cache(env):
    from app.main import create_app
    client = TestClient(create_app(), base_url="https://testserver")
    assert client.get("/api/sync/handshake", headers={"X-Sync-Token": TOKEN}).status_code == 200
    assert config.get("sync_token") == TOKEN
    values = json.loads(env["local"].read_text(encoding="utf-8"))
    values.update(sync_token="external-new", sync_previous_token=TOKEN,
                  sync_previous_token_until=utc(datetime.now(timezone.utc) + timedelta(minutes=15)))
    replacement = env["local"].with_suffix(".replacement")
    replacement.write_text(json.dumps(values), encoding="utf-8")
    replacement.replace(env["local"])
    assert config.get("sync_token") == TOKEN  # General cache intentionally stays unchanged.
    for token in (TOKEN, "external-new"):
        assert client.get("/api/sync/handshake", headers={"X-Sync-Token": token}).status_code == 200
    values["sync_token"] = ""
    replacement.write_text(json.dumps(values), encoding="utf-8")
    replacement.replace(env["local"])
    for token in (TOKEN, "external-new"):
        assert client.get("/api/sync/handshake", headers={"X-Sync-Token": token}).status_code == 401


@pytest.mark.parametrize("bad", ["*", "0.0.0.0", "::", "0.0.0.0/0", "127.0.0.0/8", ["*"],
                                  [True], "localhost", None, ["127.0.0.1"] * 33, "224.0.0.1"])
def test_proxy_allowlist_rejects_wildcards_networks_and_non_ip_values(env, bad):
    config.set_local({"trusted_proxy_ips": bad})
    with pytest.raises(SyncError) as caught:
        access.trusted_proxy_ips()
    assert caught.value.code == "INVALID_SYNC_PROXY_CONFIGURATION"


def test_explicit_proxy_ips_normalize_and_empty_list_disables_proxy_trust(env):
    assert access.trusted_proxy_ips() == "127.0.0.1,::1"
    config.set_local({"trusted_proxy_ips": "127.0.0.1, ::1, 203.0.113.7,127.0.0.1"})
    assert access.trusted_proxy_ips() == "127.0.0.1,::1,203.0.113.7"
    config.set_local({"trusted_proxy_ips": []})
    assert access.trusted_proxy_ips() == ""


@pytest.mark.parametrize("peer,expected", [("127.0.0.1", 200), ("::1", 200), ("203.0.113.8", 403)])
def test_uvicorn_only_rewrites_scheme_for_configured_proxy_addresses(env, peer, expected):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    responses = []

    async def app(scope, receive, send):
        try:
            access.require_sync_access(Request(scope))
            status = 200
        except SyncError as exc:
            status = exc.status
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        responses.append(message)

    req = request(scheme="http", client=peer,
                  headers=[(b"x-forwarded-proto", b"https"), (b"x-forwarded-for", b"127.0.0.1")])
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=access.trusted_proxy_ips())
    asyncio.run(wrapped(req.scope, receive, send))
    assert responses[0]["status"] == expected


def test_serve_passes_explicit_proxy_ips_and_ignores_wildcard_environment(env, monkeypatch):
    import uvicorn
    seen = []
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: seen.append(kwargs))
    assert cli.main(["serve"]) == 0
    assert seen[0]["proxy_headers"] is True
    assert seen[0]["forwarded_allow_ips"] == "127.0.0.1,::1"


def test_serve_rejects_bad_proxy_configuration_before_starting_worker(env, monkeypatch, capsys):
    import uvicorn
    config.set_local({"trusted_proxy_ips": "*"})
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("invalid proxy configuration must not serve"))
    assert cli.main(["serve"]) == 2
    assert "trusted_proxy_ips" in capsys.readouterr().err


def test_main_separates_user_status_namespace_from_sync_credentials(env):
    from app.main import create_app
    config.set_local({"auth_token": "user-token"})
    client = TestClient(create_app(), base_url="https://testserver")
    assert client.get("/api/sync-status", headers={"X-Sync-Token": TOKEN}).status_code == 401
    assert client.get("/api/sync-status", headers={"X-Reno-Token": "user-token"}).status_code == 200
    assert client.get("/api/sync/handshake", headers={"X-Reno-Token": "user-token"}).status_code == 401
    assert client.get("/api/sync/handshake", headers={"X-Sync-Token": TOKEN}).status_code == 200
    assert client.get("/api/sync", headers={"X-Reno-Token": "user-token"}).status_code == 401
    assert client.get("/api/sync/anything", headers=[(b"x-sync-token", b"\xff")]).status_code == 401


@pytest.mark.parametrize("deployment", ["mount", "asgi-root-path"])
def test_prefixed_api_routes_keep_sync_and_browser_credentials_separate(env, deployment):
    from fastapi import FastAPI
    from app.main import create_app

    config.set_local({"auth_token": "user-token"})
    if deployment == "mount":
        outer = FastAPI()
        outer.mount("/reno", create_app())
        client = TestClient(outer, base_url="https://testserver")
    else:
        client = TestClient(create_app(), base_url="https://testserver", root_path="/reno")

    for headers in ({}, {"X-Reno-Token": "user-token"}):
        response = client.get("/reno/api/sync/handshake", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "SYNC_UNAUTHORIZED"
    assert client.get("/reno/api/sync/handshake", headers={"X-Sync-Token": TOKEN}).status_code == 200

    for endpoint in ("sync-status", "health"):
        for headers in ({}, {"X-Sync-Token": TOKEN}):
            assert client.get(f"/reno/api/{endpoint}", headers=headers).status_code == 401
        assert client.get(f"/reno/api/{endpoint}", headers={"X-Reno-Token": "user-token"}).status_code == 200


def test_identity_cli_exports_only_public_binding_fields_and_never_initializes(env, capsys, monkeypatch):
    assert cli.main(["sync-identity"]) == 0
    result = capsys.readouterr()
    identity = json.loads(result.out)
    assert set(identity) == {"library_id", "node_id", "handshake"}
    assert TOKEN not in result.out and not result.err
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM sync_pair").fetchone()[0] == 0
        assert identity == access.identity(con)
    finally:
        con.close()
    missing = env["root"] / "missing-data"
    monkeypatch.setattr(config, "DATA", missing)
    monkeypatch.setenv("RENO_DATA_DIR", str(missing))
    assert cli.main(["sync-identity"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert not missing.exists()


def test_pair_cli_is_explicit_committed_and_idempotent(env, capsys):
    source = source_identity(env)
    path = env["root"] / "source-identity.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert cli.main(["sync-pair", "--source", str(path)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["source_library_id"] == source["library_id"]
    assert cli.main(["sync-pair", "--source", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == first
    con = db.connect()
    try:
        assert dict(con.execute("SELECT * FROM sync_pair").fetchone()) == first
        assert con.execute("SELECT COUNT(*) FROM sync_session").fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize("content", [None, '{"secret":"must-not-echo"}', "{broken must-not-echo", "[]",
                                      '{"library_id":"a","library_id":"b","node_id":"x","handshake":{}}',
                                      "x" * (access.MAX_SOURCE_BYTES + 1)])
def test_pair_cli_errors_are_sanitized_and_do_not_bind(env, capsys, content):
    path = env["root"] / "must-not-echo-source.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert cli.main(["sync-pair", "--source", str(path)]) == 2
    captured = capsys.readouterr()
    assert "must-not-echo" not in captured.out + captured.err and TOKEN not in captured.out + captured.err
    assert json.loads(captured.err)["code"] == "INVALID_SYNC_SOURCE_FILE"
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM sync_pair").fetchone()[0] == 0
    finally:
        con.close()


def test_pair_cli_rejects_home_target_and_different_peer(env, capsys):
    source = source_identity(env)
    path = env["root"] / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    config.set_local({"node_role": "home"})
    assert cli.main(["sync-pair", "--source", str(path)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SYNC_CLOUD_REQUIRED"
    config.set_local({"node_role": "cloud"})
    assert cli.main(["sync-pair", "--source", str(path)]) == 0
    capsys.readouterr()
    path.write_text(json.dumps({**source, "node_id": str(uuid.uuid4())}), encoding="utf-8")
    assert cli.main(["sync-pair", "--source", str(path), "--refresh-schema"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SYNC_PEER_MISMATCH"


def test_refresh_is_transactional_and_preserves_queue_history_personal_data(env):
    source = source_identity(env)
    con = db.connect()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        sync_queue.bind_peer(con, source, now=now)
        sync_queue.enqueue(con, ["https://b23.tv/synthetic"], now=now)
        con.execute("UPDATE sync_pair SET schema_fingerprint=?", ("0" * 64,))
        con.commit()
        old = dict(con.execute("SELECT * FROM sync_pair").fetchone())
        preserved = personal_snapshot(con)
        queue_rows = [tuple(row) for row in con.execute("SELECT * FROM sync_queue")]
        refreshed = sync_queue.refresh_peer_schema(con, source, now=now)
        assert refreshed["schema_fingerprint"] == source["handshake"]["core_schema_sha256"]
        assert refreshed["paired_at"] == old["paired_at"]
        assert personal_snapshot(con) == preserved
        assert [tuple(row) for row in con.execute("SELECT * FROM sync_queue")] == queue_rows
        con.rollback()
        assert dict(con.execute("SELECT * FROM sync_pair").fetchone()) == old
        con.execute("UPDATE renovation_project SET name='prior caller write'")
        with pytest.raises(SyncError):
            sync_queue.refresh_peer_schema(con, {**source, "library_id": str(uuid.uuid4())}, now=now)
        assert con.execute("SELECT name FROM renovation_project").fetchone()[0] == "prior caller write"
        assert con.in_transaction
    finally:
        con.close()


def test_refresh_requires_no_live_session_or_claim_and_expiry_is_exact(env):
    source = source_identity(env)
    con = db.connect()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        sync_queue.bind_peer(con, source, now=now)
        lease = sync_queue.heartbeat(con, source, str(uuid.uuid4()), now=now, lease_seconds=10)["lease"]
        sync_queue.enqueue(con, ["https://b23.tv/synthetic"], now=now)
        sync_queue.claim(con, source, lease, now=now)
        con.execute("UPDATE sync_pair SET schema_fingerprint=?", ("0" * 64,))
        con.commit()
        for session_expired in (False, True):
            if session_expired:
                con.execute("UPDATE sync_session SET lease_until=?", (utc(now),))
                con.commit()
            with pytest.raises(SyncError) as caught:
                sync_queue.refresh_peer_schema(con, source, now=now)
            assert caught.value.code == "SYNC_MAINTENANCE_BUSY"
            assert con.execute("SELECT schema_fingerprint FROM sync_pair").fetchone()[0] == "0" * 64
        assert sync_queue.refresh_peer_schema(con, source, now=now + timedelta(seconds=10))["schema_fingerprint"] != "0" * 64
        assert con.execute("SELECT COUNT(*) FROM sync_session").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sync_queue WHERE status='claimed'").fetchone()[0] == 1
    finally:
        con.close()


def test_refresh_does_not_create_a_pair(env):
    source = source_identity(env)
    con = db.connect()
    try:
        with pytest.raises(SyncError) as caught:
            sync_queue.refresh_peer_schema(con, source)
        assert caught.value.code == "SYNC_NOT_PAIRED"
        assert not con.in_transaction
    finally:
        con.close()
