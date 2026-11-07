"""Ordinary released17 Home controls with real owned genesis and admission.

Both isolated databases use production17/protocol3 and their default joint
readmit2/dispose2 DDL. SDK bytes reach the real Cloud ASGI/SQLite service; no
claim, State/core, origin, receipt, or successful budget row is fabricated.
Only the provider's external response/failure and separate-process routing
are controlled. These tests do not invoke a real model or video platform.
"""
from contextvars import Context
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import threading
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import httpx
import pytest

from reno import budget_writer_witness as sql, config, model_budget, storage_admin
from reno import storage_lifecycle as lifecycle, sync_access, sync_agent as agent
from reno import sync_queue as queue, sync_source_authority as authority
from reno import sync_source_binding17 as bindings, sync_source_continuity_service as continuity
from reno import sync_source_genesis17 as genesis, sync_source_readmit_schema2 as schema2
from reno import sync_state, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_client import SyncClient, SyncClientError
from tests.schema_fixtures import assert_current17_runtime, initialize_current17
from tests.test_sync_agent import env  # noqa: F401
from tests.test_sync_budget_propagation import raw_job
from tests.test_sync_source_execution_binding import executing


TOKEN = "synthetic-budget-home17-api-token"
URL = "https://www.bilibili.com/video/BV1xx411c7mD"
BODY = {"model": "synthetic-paas", "messages": [{"role": "user", "content": "Home budget controls"}],
    "temperature": 0.3, "max_tokens": 100}
KEY = "atomize:BVbudget_home17"


class CloudASGITransport(httpx.BaseTransport):
    """A separate Cloud process owns actual API/SQL, without Home capabilities."""
    def __init__(self, http, profile, monkeypatch):
        self.http, self.profile, self.monkeypatch = http, profile, monkeypatch
        self.seen = []
        self.responses = []

    def handle_request(self, request):
        self.seen.append((request.url.path, bytes(request.content)))
        original_profile, original_get = storage_admin.profile, config.get
        with self.monkeypatch.context() as scoped:
            # Only route this isolated service process and its real default DB.
            # No schema declaration, producer, worker, or admission is patched.
            scoped.setattr(config, "is_cloud", lambda: True)
            scoped.setattr(config, "get", lambda key, *args:
                "cloud" if key == "node_role" else original_get(key, *args))
            scoped.setattr(storage_admin, "profile", lambda **kwargs:
                original_profile(**kwargs) if kwargs else self.profile)
            scoped.setattr(config, "db_path", lambda: self.profile.database)
            response = Context().run(self.http._transport.handle_request, request)
            # Retain only public synthetic endpoint/status/error-code evidence.
            # Preserve the original response for the real SDK parser; never
            # print tokens, request bytes, private facts, or response detail.
            code = None
            if response.status_code >= 400:
                try:
                    payload = json.loads(response.read())
                    candidate = payload.get("code") if type(payload) is dict else None
                    if type(candidate) is str and re.fullmatch(r"[A-Z0-9_]{1,100}", candidate):
                        code = candidate
                except (ValueError, TypeError, UnicodeError):
                    pass
            self.responses.append((request.url.path, response.status_code, code))
            return response


def rows(profile, *tables):
    with sqlite3.connect(profile.database) as connection:
        connection.row_factory = sqlite3.Row
        return {table: [dict(row) for row in connection.execute('SELECT * FROM "' + table + '" ORDER BY 1')]
            for table in tables}


def files(value):
    """Observe original State/identity/permanent bytes and their physical identity."""
    names = {authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME,
        authority.CHECKPOINT_HEAD_NAME, "node-identity.json", "sync-state.json"}
    result = {}
    for root in (value.profile.database.parent, value.profile.checkpoints):
        for path in root.rglob("*"):
            if path.is_file() and (path.name in names or authority.DIRECTORY in path.parts):
                observed = path.stat()
                result[str(path)] = ((observed.st_dev, observed.st_ino, observed.st_mode,
                    observed.st_size, observed.st_mtime_ns), hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def paid_rows(value):
    return rows(value.profile, "budget_operation", "budget_attempt")


def model_scope(value):
    core = value.binding.core
    return model_budget.ModelScope(value.profile, core["continuation_id"], core["execution_generation"], KEY,
        root_job_id=core["root_job_id"], home_execution=value.binding, home_client=value.client)


def request(send):
    return model_budget.request_once("paas_v4", BODY, send, classify=lambda result: "full_result")


def response():
    return model_budget.ProviderResponse(200, b'{"choices":[{"message":{"content":"synthetic paid result"}}]}')


@pytest.fixture
def current_home17(env, tmp_path, monkeypatch):
    from app.main import create_app
    assert_current17_runtime()
    profile = storage_admin.profile()
    cloud = lifecycle.StorageProfile(tmp_path / "cloud/data/reno.db", tmp_path / "cloud/media",
        tmp_path / "cloud/reports", tmp_path / "cloud/checkpoints")
    initialize_current17(cloud.database)
    lifecycle.initialize(cloud)
    source, version = agent._local()
    assert source["handshake"]["protocol_version"] == 3
    assert source["handshake"]["migration_version"] == 17
    with storage_admin.connection(cloud) as connection:
        target = sync_access.identity(connection)
        queue.bind_peer(connection, source)
        assert queue.enqueue(connection, [URL]) == 1
        connection.commit()
    for selected in (profile, cloud):
        with storage_admin.connection(selected) as connection:
            assert schema2.detect(connection) == "admission2_dispose2"
    config.set_local({"node_role": "home", "budget_enabled": True, "sync_token": TOKEN,
        "sync_enabled": True, "sync_cloud_url": "https://sync.example.test/reno",
        "sync_target_library_id": target["library_id"], "sync_target_node_id": target["node_id"]})
    # Explicit maintenance invokes the actual activation producer. It precedes
    # every Home worker/State owner and never manufactures a remote result DTO.
    with monkeypatch.context() as scoped:
        scoped.setattr(config, "is_cloud", lambda: True)
        active = continuity.activate(profile=cloud, continuity_id=str(uuid.uuid4()),
            activated_at=queue._format_time(datetime.now(timezone.utc)))
    assert active["source_identity"] == {key: source[key] for key in ("library_id", "node_id")}
    monkeypatch.setattr(worker, "ensure_running", lambda: None)
    cloud_http = TestClient(create_app(), base_url="https://sync.example.test/reno", root_path="/reno")
    public = TestClient(create_app())
    transport = CloudASGITransport(cloud_http, cloud, monkeypatch)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: TOKEN, transport=transport)
    options = agent.settings()
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    state_binding = {"target_url": options["url"], "home_library_id": source["library_id"],
        "home_node_id": source["node_id"], "target_library_id": target["library_id"],
        "target_node_id": target["node_id"]}
    try:
        with tasks.process_lock(profile=profile) as acquired, store.exclusive():
            assert acquired
            store.initialize(state_binding, version["library_epoch"])
            cycle = agent._Cycle(store, client, source, options, threading.Event())
            with executing(cycle) as (job, owner):
                cycle.acquire()
                current = cycle.recovery()
                cycle.knowledge_generation, cycle.knowledge_stream = current["generation"], current["stream"]
                try:
                    assert cycle.pull_events(claim_limit=1)
                except SyncClientError as error:
                    pytest.fail("Actual Cloud observation rejected: " + repr({
                        "responses": transport.responses,
                        "sdk_code": error.code, "sdk_status": error.status,
                        "remote_code": getattr(error, "remote_code", None),
                        "remote_status": getattr(error, "remote_status", None)}))
                pulled = store.read()
                assert len(pulled["claims"]) == 1 and pulled.get("source_execution") is None
                core = genesis.create_initial_core(profile=profile, store=store, client=client,
                    item_id=pulled["claims"][0]["item_id"])
                binding = bindings.capture_home_binding_locked(profile=profile, store=store,
                    item_id=core["item_id"])
                original = store.read()
                assert original["format_version"] == 5 and original["source_execution"] == core == binding.core
                assert original["source_authority"]["origin"] == binding.origin_ref
                assert original["source_admission"] is None
                assert (core["root_job_id"], core["continuation_id"], core["execution_generation"]) == (
                    job["job_id"], job["continuation_id"], job["execution_generation"])
                assert not paid_rows(SimpleNamespace(profile=profile))["budget_attempt"]
                value = SimpleNamespace(profile=profile, cloud=cloud, source=source, target=target,
                    store=store, cycle=cycle, binding=binding, job=job, owner=owner,
                    client=client, public=public, transport=transport)
                yield value
        assert_current17_runtime()
    finally:
        public.close()
        cloud_http.close()


@pytest.mark.parametrize("outcome", ["complete", "unknown"])
def test_current17_pause_preserves_inflight_owner_and_original_paid_fact_then_resume(current_home17, outcome):
    value = current_home17
    original_state, original_files = value.store.read(), files(value)
    before_job = raw_job(value.profile, value.job["job_id"])
    cloud_before = rows(value.cloud, "sync_queue", "sync_session", *schema2.COLUMNS)
    transport_before = deepcopy(value.transport.seen)
    home = value.public.get("/api/budget")
    assert home.status_code == 200 and home.json()["capabilities"]["home_control"] is True
    home = home.json()["home"]
    assert home["task"]["job_id"] == value.job["job_id"] and home["task"]["can_pause"] is True
    sent = []

    def send(payload):
        # Fresh reserve/dispatch consumed the real17 admission factory, and all
        # its local writer/pin scopes have exited before the artificial effect.
        assert value.store._reference_depth == 0
        assert lifecycle.current_guard() is None and not sql.in_write_window()
        sent.append(payload)
        paused = value.public.post("/api/budget/home/pause",
            json={"expected_control_revision": home["control_revision"]})
        assert paused.status_code == 202
        paused_job = raw_job(value.profile, value.job["job_id"])
        assert paused_job["manual_paused"] == 1
        assert tuple(paused_job[key] for key in ("owner_id", "lease_until", "continuation_id", "execution_generation")) == (
            before_job["owner_id"], before_job["lease_until"], before_job["continuation_id"], before_job["execution_generation"])
        assert value.store.read() == original_state and files(value) == original_files
        assert not any(secret in paused.text for secret in ("lease_token", "source_execution", "source_authority"))
        if outcome == "unknown":
            raise RuntimeError("isolated provider response lost")
        return response()

    with model_budget.scope(model_scope(value)):
        if outcome == "unknown":
            with pytest.raises(BudgetDeferred) as waiting:
                request(send)
            assert waiting.value.reason == "indeterminate"
        else:
            assert request(send) == response()
    ledger = paid_rows(value)
    assert len(sent) == len(ledger["budget_operation"]) == len(ledger["budget_attempt"]) == 1
    operation, attempt = ledger["budget_operation"][0], ledger["budget_attempt"][0]
    assert (operation["root_job_id"], operation["continuation_id"], operation["execution_generation"]) == (
        value.job["job_id"], value.job["continuation_id"], value.job["execution_generation"])
    assert operation["suboperation_key"] == KEY and attempt["ordinal"] == 1
    assert attempt["state"] == ("settled" if outcome == "complete" else "indeterminate")
    assert attempt["response_complete"] == (1 if outcome == "complete" else 0)
    deferred = BudgetDeferred("manual_pause" if outcome == "complete" else "indeterminate",
        operation_id=operation["operation_id"])
    assert tasks.defer(value.job["job_id"], value.owner, deferred, profile=value.profile)
    pending = raw_job(value.profile, value.job["job_id"])
    assert pending["status"] == "pending" and pending["owner_id"] is pending["lease_until"] is None
    snapshot = value.public.get("/api/budget").json()["home"]
    assert snapshot["available"] is True and snapshot["task"]["can_resume"] is True
    resumed = value.public.post("/api/budget/home/resume",
        json={"expected_control_revision": snapshot["control_revision"]})
    assert resumed.status_code == 202
    after_job = raw_job(value.profile, value.job["job_id"])
    assert after_job["manual_paused"] == 0 and after_job["wait_mode"] == "none"
    assert after_job["owner_id"] is after_job["lease_until"] is None
    assert tuple(after_job[key] for key in ("job_id", "continuation_id", "execution_generation", "payload_json", "scope_key", "attempts")) == tuple(
        pending[key] for key in ("job_id", "continuation_id", "execution_generation", "payload_json", "scope_key", "attempts"))
    assert paid_rows(value) == ledger
    assert value.store.read() == original_state and files(value) == original_files
    assert rows(value.cloud, "sync_queue", "sync_session", *schema2.COLUMNS) == cloud_before
    assert value.transport.seen == transport_before

    def forbidden(payload):
        pytest.fail("Home controls retried an original settled or indeterminate provider attempt")
    with model_budget.scope(model_scope(value)):
        if outcome == "unknown":
            with pytest.raises(BudgetDeferred) as replay:
                request(forbidden)
            assert replay.value.reason == "indeterminate" and replay.value.operation_id == operation["operation_id"]
        else:
            assert request(forbidden) == response()
    assert paid_rows(value) == ledger and len(sent) == 1
    assert value.store.read() == original_state and files(value) == original_files
    assert value.transport.seen == transport_before


def test_current17_stale_revision_cannot_unpause_or_rewrite_original_source(current_home17):
    value = current_home17
    original_state, original_files = value.store.read(), files(value)
    home = value.public.get("/api/budget").json()["home"]
    paused = value.public.post("/api/budget/home/pause",
        json={"expected_control_revision": home["control_revision"]})
    assert paused.status_code == 202
    before = raw_job(value.profile, value.job["job_id"])
    result = value.public.post("/api/budget/home/resume",
        json={"expected_control_revision": home["control_revision"]})
    assert result.status_code == 409 and result.json()["code"] == "BUDGET_HOME_CONTROL_CHANGED"
    assert raw_job(value.profile, value.job["job_id"]) == before
    assert value.store.read() == original_state and files(value) == original_files
    assert paid_rows(value) == {"budget_operation": [], "budget_attempt": []}


def test_current17_changed_peer_refuses_effect_without_rebinding_core(current_home17):
    value = current_home17
    original_state, original_files = value.store.read(), files(value)
    home = value.public.get("/api/budget").json()["home"]
    before = raw_job(value.profile, value.job["job_id"])
    config.set_local({"sync_target_node_id": str(uuid.uuid4())})
    result = value.public.post("/api/budget/home/pause",
        json={"expected_control_revision": home["control_revision"]})
    assert result.status_code == 409
    assert raw_job(value.profile, value.job["job_id"]) == before
    assert value.store.read() == original_state and files(value) == original_files
    assert paid_rows(value) == {"budget_operation": [], "budget_attempt": []}
    assert not any(secret in result.text for secret in ("source_execution", "lease_token", URL))
