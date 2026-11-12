"""Actual ASGI/SDK/SQLite prospective17 controls, in isolated TEMP only."""
from copy import deepcopy
import sqlite3
import uuid

import httpx
import pytest

from tests.asgi_transport import as_httpx_response

from reno import config, migrations, storage_admin, sync_queue as queue
from reno import sync_source_continuity as external
from reno import sync_source_continuity_service as service, sync_source_readmit_contract as wire
from reno.sync_client import SyncClient, SyncClientError
from tests.test_sync_source_continuity_service import env, activated, NOW, register_request
from tests.test_sync_source_readmission import request, snapshot, item


TOKEN = "artificial-source-recovery-api-token"


@pytest.fixture
def http(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    config.set_local({"node_role": "cloud", "sync_token": TOKEN})
    original_profile = storage_admin.profile
    monkeypatch.setattr(storage_admin, "profile", lambda **kwargs: original_profile(**kwargs) if kwargs else env.profile)
    original_clock = queue._clock
    monkeypatch.setattr(queue, "_clock", lambda now: original_clock(NOW if now is None else now))
    # Each request uses the real app/router/middleware. Server startup is
    # separately owned; this harness never starts a background worker.
    value = TestClient(create_app(), base_url="https://sync.example.test")
    yield value
    value.close()


class _SDKTransport(httpx.BaseTransport):
    """Borrow the real ASGI peer while adapting its response stream domain."""
    def __init__(self, http):
        self.http = http

    def handle_request(self, request):
        return as_httpx_response(self.http._transport.handle_request(request))


def sdk(http):
    return SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=_SDKTransport(http))


def test_three_endpoints_require_independent_sync_token_and_closed_path_kind(env, http):
    activated(env)
    before = snapshot(env)
    for path in ("register", "dispose", "readmit"):
        assert http.post("/api/sync/source/" + path, json=request(env)).status_code == 401
    bad = http.post("/api/sync/source/register", json=request(env), headers={"X-Sync-Token": TOKEN})
    assert bad.status_code == 400 and bad.json()["code"] == "INVALID_SYNC_REQUEST"
    assert snapshot(env) == before


@pytest.mark.parametrize("path,maximum", [("register", 32 * 1024), ("dispose", 32 * 1024), ("readmit", 16 * 1024)])
def test_endpoint_rejects_raw_oversize_before_decode_or_business(env, http, path, maximum):
    activated(env)
    before = snapshot(env)
    response = http.post("/api/sync/source/" + path, content=b" " * (maximum + 1),
                         headers={"X-Sync-Token": TOKEN, "Content-Type": "application/json"})
    assert response.status_code == 413 and snapshot(env) == before


def test_actual_sdk_registration_disposition_and_targeted_readmission(env, http):
    active = activated(env)
    body = request(env)
    registration_request = register_request(env, active)
    instance = sdk(http)
    registered = instance.source_register(registration_request)
    assert registered["replayed"] is False and item(env)["attempts"] == 3
    before = snapshot(env)
    assert instance.source_register(registration_request) == {**registered, "replayed": True}
    assert snapshot(env) == before
    dispose = {key: deepcopy(body[key]) for key in wire._BOUND}
    # This is a real distinct request UUID, not a replacement of the original.
    dispose.update(kind="source_dispose", request_id=str(uuid.uuid4()), original_request=deepcopy(registration_request["original_request"]),
                   registration=deepcopy(registered))
    negative = instance.source_dispose(dispose)
    assert negative["disposition"] == "not_applied"
    body["dispositions"] = [negative]
    admitted = instance.source_readmit(body)
    assert admitted["attempts"] == 3 and item(env)["lease_token"] == admitted["claim"]["lease_token"]
    before = snapshot(env)
    assert instance.source_readmit(body) == {**admitted, "replayed": True}
    assert snapshot(env) == before
    # Ordinary push uses the registered effect seam. The permanently disposed
    # original cannot run even though all its other credentials were valid.
    with pytest.raises(SyncClientError) as caught:
        instance.budget_request(registration_request["original_request"])
    assert caught.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH" and snapshot(env) == before


def test_unregistered_original_push_has_no_effect_and_registered_push_refunds_once(env, http):
    active = activated(env)
    registration = register_request(env, active)
    original = registration["original_request"]
    instance = sdk(http)
    before = snapshot(env)
    with pytest.raises(SyncClientError) as caught:
        instance.budget_request(original)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED" and snapshot(env) == before
    instance.source_register(registration)
    result = instance.budget_request(original)
    assert result["attempts"] == 2 and item(env)["status"] == "budget_wait"
    before = snapshot(env)
    assert instance.budget_request(original) == {**result, "replayed": True}
    assert snapshot(env) == before


def test_genuine_old_positive_replays_before_explicit_activation_but_missing_and_new_bodies_do_not_run(env, http):
    instance = sdk(http)
    before = snapshot(env)
    assert instance.budget_request(env.legacy) == {**env.legacy_receipt, "replayed": True}
    resume = instance.budget_request(env.legacy_resume, defer_receipt=env.legacy_receipt)
    assert resume["replayed"] is True and resume["status"] == "queued"
    assert snapshot(env) == before
    assert not (env.profile.database.parent / external.DIRECTORY).exists()
    assert not (env.profile.database.parent / external.LOCK_NAME).exists()
    absent = {**env.legacy, "request_id": str(uuid.uuid4())}
    # The old SQL miss is not a negative proof or fresh leaf permission.
    with pytest.raises(SyncClientError) as caught:
        instance.budget_request(absent)
    assert caught.value.code == "SYNC_SOURCE_CONTINUITY_MISSING"
    newer = deepcopy(env.legacy)
    newer.update(request_id=str(uuid.uuid4()), source=deepcopy(env.source), claim=deepcopy(env.fresh_claim))
    with pytest.raises(SyncClientError) as caught:
        instance.budget_request(newer)
    assert caught.value.code == "SYNC_SOURCE_CONTINUITY_MISSING" and snapshot(env) == before


def test_failed_sql_commit_has_no_success_response_and_retains_unknown_marker(env, http, monkeypatch):
    active = activated(env)
    registration = register_request(env, active)
    original = registration["original_request"]
    instance = sdk(http)
    instance.source_register(registration)
    before = snapshot(env)
    original_open = migrations._open
    def denied(path):
        con = original_open(path)
        con.set_authorizer(lambda action, first, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        return con
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_open", denied)
        with pytest.raises(SyncClientError) as caught:
            instance.budget_request(original)
        assert caught.value.code == "SYNC_STORAGE_FAILED" and caught.value.status == 503
    assert snapshot(env) == before
    with pytest.raises(SyncClientError) as caught:
        instance.budget_request(original)
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
    assert snapshot(env) == before


def test_current_global16_rejects_new_control_without_files_or_sql_effect(env, http, monkeypatch):
    active = activated(env)
    body = register_request(env, active)
    before = snapshot(env)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_VERSION", 16)
        response = http.post("/api/sync/source/register", json=body, headers={"X-Sync-Token": TOKEN})
        assert response.status_code == 409 and response.json()["code"] == "SYNC_SOURCE_READMIT_SCHEMA_REQUIRED"
    assert snapshot(env) == before


@pytest.mark.parametrize("content", [b'{"kind":"source_readmit","kind":"source_readmit"}',
    b'{"kind":"source_readmit","value":NaN}', b'{"kind":"source_readmit","value":"\\ud800"}'])
def test_duplicate_nonfinite_and_escaped_surrogate_are_controlled_errors(env, http, content):
    activated(env)
    before = snapshot(env)
    response = http.post("/api/sync/source/readmit", content=content,
                         headers={"X-Sync-Token": TOKEN, "Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == "INVALID_SYNC_REQUEST"
    assert snapshot(env) == before
