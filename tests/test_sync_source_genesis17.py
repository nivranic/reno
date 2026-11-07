"""Actual first-origin production from HTTP observations and owned worker SQL.

All databases and peers are isolated. Modern fixtures use the normal17 release,
real Cloud ASGI/SQL observations and an actual worker, while preserving a cold
State until the test invokes genesis. Historical future17_gate stays separate.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import json
from pathlib import Path
import sqlite3
import threading
from types import FunctionType, SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import httpx
import pytest

from reno import config, db, home_recovery17 as restore, migrations, storage_admin
from reno import sync_access, sync_protocol, sync_source_authority as authority
from reno import sync_source_execution as execution, sync_state
from reno import tasks, worker
from reno import storage_lifecycle as lifecycle, sync_agent as agent, sync_queue as queue
from reno import sync_source_continuity_service as continuity, sync_source_readmit_schema2 as schema2
from reno.budget_contract import BudgetDeferred
from reno.sync_client import SyncClient
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime, initialize_current17, released16
from tests.test_budget_home_api17 import CloudASGITransport
from tests.test_sync_agent import env
from tests.test_sync_source_claim import environment, observe


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Normal17Peer(httpx.BaseTransport):
    """Real isolated Cloud results; only explicitly negative calls are corruptible.

    Observations are detached, never a permission or a canned successful reply.
    Every success comes from the actual ASGI/SQL producer and the real SDK.
    Request headers, tokens and response bodies are never printed.
    """
    def __init__(self, cloud_http, cloud_profile, monkeypatch):
        self.backend = CloudASGITransport(cloud_http, cloud_profile, monkeypatch)
        self.seen, self._observed, self._negative = [], {}, {}

    @property
    def payloads(self):
        return deepcopy(self._observed)

    def corrupt_next(self, endpoint, transform):
        if endpoint not in {"heartbeat", "pull", "continuity"} or not callable(transform):
            raise AssertionError("Only negative observation tests can corrupt a response")
        if endpoint in self._negative:
            raise AssertionError("An original response fault is already pending")
        self._negative[endpoint] = transform

    def handle_request(self, request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.seen.append((endpoint, json.loads(request.content)))
        response = self.backend.handle_request(request)
        raw = response.read()
        if 200 <= response.status_code < 300:
            decoded = json.loads(raw)
            self._observed[endpoint] = deepcopy(decoded)
        transform = self._negative.pop(endpoint, None)
        if transform is None:
            return response
        if not 200 <= response.status_code < 300:
            response.close()
            raise AssertionError("A negative injection cannot hide a real Cloud refusal")
        damaged = deepcopy(decoded)
        replacement = transform(damaged)
        if replacement is not None:
            damaged = replacement
        response.close()
        return httpx.Response(response.status_code, json=damaged, request=request)


def _target_connection(con, expected):
    rows = [row for row in con.execute("PRAGMA database_list") if row[1] == "main"]
    return len(rows) == 1 and Path(rows[0][2]).resolve() == expected


@contextmanager
def future17_gate(value, monkeypatch):
    """Only this actual17 fixture DB may use the original future algorithm.

    FunctionType retains the actual schema_handshake code and its real core
    SQL inspection. Its own global version object is17; production's constant
    stays16. Every other database retains the ordinary production validators.
    This fixture supplies neither a worker context nor a claim producer seal.
    """
    original_validate, original_handshake = migrations.validate, sync_protocol.schema_handshake
    expected = value.profile.database.resolve()
    handshake_globals = dict(original_handshake.__globals__)
    handshake_globals["migrations"] = SimpleNamespace(SCHEMA_VERSION=17)
    future_handshake = FunctionType(original_handshake.__code__, handshake_globals,
        original_handshake.__name__, original_handshake.__defaults__, original_handshake.__closure__)
    with monkeypatch.context() as scoped:
        def validate(con):
            if _target_connection(con, expected):
                restore._actual_schema(con)
            else:
                original_validate(con)
        def handshake(con):
            if not _target_connection(con, expected):
                return original_handshake(con)
            restore._actual_schema(con)
            return future_handshake(con)
        scoped.setattr(migrations, "validate", validate)
        scoped.setattr(sync_protocol, "schema_handshake", handshake)
        assert migrations.SCHEMA_VERSION == 16
        yield
        assert migrations.SCHEMA_VERSION == 16


@pytest.fixture
def home17(env, tmp_path, monkeypatch):
    """Normal17 databases and a real persisted job, with no first-origin facts."""
    from app.main import create_app
    assert_current17_runtime()
    profile = storage_admin.profile()
    cloud = lifecycle.StorageProfile(tmp_path / "cloud/data/reno.db", tmp_path / "cloud/media",
        tmp_path / "cloud/reports", tmp_path / "cloud/checkpoints")
    initialize_current17(cloud.database)
    lifecycle.initialize(cloud)
    source, version = agent._local()
    with storage_admin.connection(cloud) as con:
        target = sync_access.identity(con)
        queue.bind_peer(con, source)
        assert queue.enqueue(con, ["https://www.bilibili.com/video/BV1xx411c7mD"]) == 1
        con.commit()
    for selected in (profile, cloud):
        with storage_admin.connection(selected) as con:
            assert schema2.detect(con) == "admission2_dispose2"
    token = "synthetic-normal17-genesis-token"
    config.set_local({"node_role": "home", "budget_enabled": True, "sync_token": token,
        "sync_enabled": True, "sync_cloud_url": "https://sync.example.test/reno",
        "sync_target_library_id": target["library_id"], "sync_target_node_id": target["node_id"]})
    # The genuine activation completes before any Home worker/State owner.
    with monkeypatch.context() as scoped:
        scoped.setattr(config, "is_cloud", lambda: True)
        activated = continuity.activate(profile=cloud, continuity_id=str(uuid.uuid4()),
            activated_at=queue._format_time(datetime.now(timezone.utc)))
    assert activated["source_identity"] == {key: source[key] for key in ("library_id", "node_id")}
    monkeypatch.setattr(worker, "ensure_running", lambda: None)
    cloud_http = TestClient(create_app(), base_url="https://sync.example.test/reno", root_path="/reno")
    remote = Normal17Peer(cloud_http, cloud, monkeypatch)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: token, transport=remote)
    options, stop = agent.settings(), threading.Event()
    binding = {"target_url": options["url"], "home_library_id": source["library_id"],
        "home_node_id": source["node_id"], "target_library_id": target["library_id"],
        "target_node_id": target["node_id"]}
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    try:
        with tasks.process_lock(profile=profile) as acquired:
            assert acquired
            with store.exclusive():
                store.initialize(binding, version["library_epoch"])
                # Only upgrade the empty representation; no core or receipt is supplied.
                store.enable_source_readmit()
            job_id, owner = tasks.submit_sync(profile=profile), str(uuid.uuid4())
            assert tasks.acquire_worker(owner, profile=profile)
            try:
                with storage_admin.connection(profile) as con:
                    job = tasks.claim(con, owner, profile=profile)
                assert job["job_id"] == job_id and job["status"] == "running" and job["owner_id"] == owner
                assert 0 < job["lease_until"] - tasks.clock_ms() <= tasks.LEASE_MS == 120_000
                value = SimpleNamespace(profile=profile, cloud=cloud, store=store, source=source,
                    target=target, binding=binding, version=version, job=job, owner=owner,
                    stop=stop, client=client, remote=remote, options=options)
                yield value
            finally:
                tasks.release_worker(owner, path=profile.database)
        assert_current17_runtime()
    finally:
        cloud_http.close()


@pytest.fixture
def live17(home17, monkeypatch):
    """Real typed observations end before yielding; genesis remains test-owned."""
    value = home17
    with worker._execution(value.job, value.owner, value.stop, profile=value.profile):
        with value.store.exclusive():
            cycle = agent._Cycle(value.store, value.client, value.source, value.options, value.stop)
            cycle.acquire()
            current = cycle.recovery()
            cycle.knowledge_generation, cycle.knowledge_stream = current["generation"], current["stream"]
            assert cycle.pull_events(claim_limit=1)
            state = value.store.read()
        assert value.store._directory is None
        assert len(state["claims"]) == 1 and state.get("source_execution") is None
        assert state["source_authority"] is None and state["source_admission"] is None
        assert origin_records(value) == []
        with storage_admin.connection(value.profile) as con:
            assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 0
        value.remote.entry = deepcopy(state["claims"][0])
        value.remote.lease = deepcopy(state["lease"])
        value.remote.recovery = deepcopy(state["recovery"])
        value.remote.source, value.remote.target = deepcopy(value.source), deepcopy(value.target)
        value.remote.client = value.client
        yield value


def create(value):
    return import_module("reno.sync_source_genesis17").create_initial_core(
        profile=value.profile, store=value.store, client=value.client, item_id=value.remote.entry["item_id"])


def candidate(value):
    state = value.store.read()
    return execution._candidate_core(state, value.job, state["claims"][0],
        recovery_generation=state["recovery"]["generation"], recovery_stream=state["recovery"]["stream"])


def permanent_files(value):
    selected = {}
    names = {authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME}
    for root in (value.profile.database.parent, value.profile.checkpoints):
        for path in root.rglob("*"):
            if path.is_file() and (path.name in names or authority.DIRECTORY in path.parts):
                selected[str(path)] = (path.stat().st_mtime_ns, path.read_bytes())
    return selected


def origin_records(value):
    paths = {path for root in (value.profile.database.parent, value.profile.checkpoints)
        for path in root.rglob("*.json") if path.parent.name == "origins"}
    return [(path, json.loads(path.read_bytes())) for path in sorted(paths)]


def sql_change(value, query, parameters=()):
    with storage_admin.connection(value.profile) as con:
        con.execute(query, parameters)
        con.commit()


def test_global16_still_rejects_actual17_without_future_fixture(tmp_path, monkeypatch):
    # The old negative owns no modern worker/State lifetime. Create the real
    # current image first, then select the complete original released gate.
    profile = lifecycle.StorageProfile(tmp_path / "gate/data/reno.db", tmp_path / "gate/media",
        tmp_path / "gate/reports", tmp_path / "gate/checkpoints")
    initialize_current17(profile.database)
    lifecycle.initialize(profile)
    value = SimpleNamespace(profile=profile)
    with released16(monkeypatch):
        assert migrations.SCHEMA_VERSION == 16
        with pytest.raises(migrations.SchemaVersionError, match="supports 16"):
            db.connect(profile.database)
        with sqlite3.connect(profile.database) as con:
            assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
            with pytest.raises(SyncError):
                sync_protocol.schema_handshake(con)
        assert not (profile.database.parent / "sync-state.json").exists()
        assert origin_records(value) == []
    assert_current17_runtime()


def test_original_core_and_origin_are_one_state_write_after_permanent_commit(live17, monkeypatch):
    original_write = sync_state._write
    writes = []
    def observe_write(directory, name, data, **kwargs):
        if name == sync_state._STATE:
            decoded = json.loads(data) if isinstance(data, (bytes, str)) else deepcopy(data)
            if decoded.get("source_execution") is not None:
                records = origin_records(live17)
                assert len(records) == 1
                origin = records[0][1]
                assert decoded["source_authority"]["origin"] == authority.record_ref(origin)
                assert decoded["source_execution"] == origin["core"]
                writes.append(deepcopy(decoded))
        return original_write(directory, name, data, **kwargs)
    monkeypatch.setattr(sync_state, "_write", observe_write)
    core = create(live17)
    state = live17.store.read()
    assert core == candidate(live17) == state["source_execution"]
    assert len(writes) == 1 and writes[0]["source_authority"]["origin"] is not None
    assert state["format_version"] == 5 and state["source_admission"] is None
    assert state["source_recovery_pending"] is state["source_control_pending"] is state["budget_pending"] is None
    assert [endpoint for endpoint, _ in live17.remote.seen] == ["heartbeat", "pull", "continuity"]
    assert_current17_runtime()


def test_installed_original_core_replays_read_only_without_new_producer_or_publish(live17, monkeypatch):
    core = create(live17)
    state, files = live17.store.read(), permanent_files(live17)
    def forbidden(*args, **kwargs):
        pytest.fail("Installed genesis replay attempted a new write or producer")
    module = import_module("reno.sync_source_genesis17")
    monkeypatch.setattr(module.Genesis17Guard, "__init__", forbidden)
    monkeypatch.setattr(authority.AuthorityGuard, "publish", forbidden)
    monkeypatch.setattr(live17.store, "_install_source_genesis17", forbidden)
    returned = create(live17)
    assert returned == core and returned is not core
    returned["item_id"] += 1
    assert live17.store.read() == state and permanent_files(live17) == files


def test_permanent_origin_state_gap_replays_exact_original_body_and_uuid(live17, monkeypatch):
    marker = RuntimeError("isolated origin-State gap")
    setter = live17.store._install_source_genesis17
    with monkeypatch.context() as scoped:
        def stop(core, origin_ref, *, producer):
            assert len(origin_records(live17)) == 1
            raise marker
        scoped.setattr(live17.store, "_install_source_genesis17", stop)
        with pytest.raises(RuntimeError) as caught:
            create(live17)
        assert caught.value is marker
    assert live17.store.read()["source_execution"] is None
    before = permanent_files(live17)
    path, original = origin_records(live17)[0]
    core = create(live17)
    assert core == original["core"]
    assert live17.store.read()["source_authority"]["origin"] == authority.record_ref(original)
    assert path.read_bytes() == encoded(original)
    assert permanent_files(live17) == before
    assert live17.store._install_source_genesis17 == setter


@pytest.mark.parametrize("boundary", ["reserved", "record", "head", "activation"])
def test_permanent_partial_write_recovers_original_genesis_body(live17, monkeypatch, boundary):
    marker = RuntimeError("isolated genesis gap " + boundary)
    write, append = authority.AuthorityGuard._write, authority.AuthorityGuard._append
    stopped, bodies = [], []
    with monkeypatch.context() as scoped:
        def interrupted_write(owner, directory, name, raw, **kwargs):
            decoded = json.loads(raw)
            if isinstance(decoded, dict) and decoded.get("kind") == "origins":
                bodies.append(deepcopy(decoded["record"]))
            result = write(owner, directory, name, raw, **kwargs)
            matched = ((boundary == "record" and decoded.get("record_kind") == "origin")
                or (boundary == "head" and name == authority.DB_HEAD_NAME and decoded.get("ordinal") == 1)
                or (boundary == "activation" and name == authority.INTENT_NAME))
            if matched and not stopped:
                stopped.append(name)
                raise marker
            return result
        def interrupted_append(owner, observed, kind, key, record, state):
            result = append(owner, observed, kind, key, record, state)
            if boundary == "reserved" and kind == "origins" and state == "reserved" and not stopped:
                bodies.append(deepcopy(record))
                stopped.append(state)
                raise marker
            return result
        scoped.setattr(authority.AuthorityGuard, "_write", interrupted_write)
        scoped.setattr(authority.AuthorityGuard, "_append", interrupted_append)
        with pytest.raises(RuntimeError) as caught:
            create(live17)
        assert caught.value is marker and stopped
    assert live17.store.read()["source_execution"] is None
    core = create(live17)
    records = origin_records(live17)
    assert len(records) == 1 and records[0][1]["core"] == core
    if bodies:
        assert all(body == records[0][1] for body in bodies)
    assert live17.store.read()["source_authority"]["origin"] == authority.record_ref(records[0][1])


def test_preexisting_core_with_unknown_origin_is_never_backfilled(live17):
    core = candidate(live17)
    live17.store.install_source_execution(core)
    before = live17.store.read()
    assert before["source_authority"] is None
    with pytest.raises(SyncError):
        create(live17)
    assert live17.store.read() == before and permanent_files(live17) == {}


@pytest.mark.parametrize("mutation", ["claim", "url", "recovery", "local_epoch", "peer_config", "session"])
def test_changed_original_transport_or_state_fence_cannot_create_origin(live17, mutation):
    state = live17.store.read()
    if mutation in {"claim", "url"}:
        claims = deepcopy(state["claims"])
        if mutation == "claim":
            claims[0]["claim"]["lease_generation"] += 1
        else:
            claims[0]["url"] = "https://www.bilibili.com/video/BV1Changed"
        live17.store.update({"claims": claims})
    elif mutation == "recovery":
        recovery = deepcopy(state["recovery"])
        recovery["revision"] += 1
        live17.store.update({"recovery": recovery})
    elif mutation == "local_epoch":
        live17.store.update({"local_epoch": state["local_epoch"] + 1})
    elif mutation == "session":
        lease = deepcopy(state["lease"])
        lease["lease_token"] = str(uuid.uuid4())
        live17.store.update({"lease": lease})
    else:
        config.set_local({"sync_target_node_id": str(uuid.uuid4())})
    before = live17.store.read()
    with pytest.raises((SyncError, BudgetDeferred)):
        create(live17)
    assert live17.store.read() == before and origin_records(live17) == []


@pytest.mark.parametrize("mutation", ["job_owner", "job_lease", "worker_owner", "worker_lease", "job_lineage"])
def test_actual_running_job_and_worker_lease_are_required(live17, mutation):
    if mutation == "job_owner":
        sql_change(live17, "UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), live17.job["job_id"]))
    elif mutation == "job_lease":
        sql_change(live17, "UPDATE job SET lease_until=0 WHERE job_id=?", (live17.job["job_id"],))
    elif mutation == "worker_owner":
        sql_change(live17, "UPDATE worker_lease SET owner_id=? WHERE scope=?", (str(uuid.uuid4()), tasks.WORKER_SCOPE))
    elif mutation == "worker_lease":
        sql_change(live17, "UPDATE worker_lease SET lease_until=0 WHERE scope=?", (tasks.WORKER_SCOPE,))
    else:
        sql_change(live17, "UPDATE job SET continuation_id=? WHERE job_id=?", (str(uuid.uuid4()), live17.job["job_id"]))
    before = live17.store.read()
    with pytest.raises(tasks.TaskError) as caught:
        create(live17)
    assert caught.value.code == "TASK_OWNER_LOST"
    assert live17.store.read() == before and origin_records(live17) == []


def test_stopped_worker_preserves_original_manual_pause(live17):
    live17.stop.set()
    before = live17.store.read()
    with pytest.raises(BudgetDeferred) as caught:
        create(live17)
    assert caught.value.reason == "manual_pause"
    assert live17.store.read() == before and origin_records(live17) == []


def test_copied_response_dtos_do_not_replace_real_http_observations(live17, monkeypatch):
    from reno import sync_source_claim
    fake = environment()
    fake.source.clear()
    fake.source.update(live17.source)
    fake.target.clear()
    fake.target.update({key: live17.target[key] for key in ("library_id", "node_id")})
    fake.lease = deepcopy(live17.remote.lease)
    fake.entry = deepcopy(live17.remote.entry)
    fake.recovery = deepcopy(live17.remote.recovery)
    def dto(endpoint, **kwargs):
        return deepcopy(live17.remote.payloads[endpoint.rsplit("/", 1)[-1]])
    monkeypatch.setattr(fake.client, "_request", dto)
    observe(fake)
    with pytest.raises(SyncError):
        with sync_source_claim.capture_initial_claim(fake.client, source=live17.source,
                item_id=live17.remote.entry["item_id"]):
            pytest.fail("DTOs minted an initial claim")
    before = live17.store.read()
    module = import_module("reno.sync_source_genesis17")
    with pytest.raises(SyncError):
        module.create_initial_core(profile=live17.profile, store=live17.store,
            client=fake.client, item_id=live17.remote.entry["item_id"])
    assert live17.store.read() == before and origin_records(live17) == []


def test_captured_genesis_guard_expires_and_cannot_cross_thread(live17, monkeypatch):
    publish, held = authority.AuthorityGuard.publish, []
    def observed(owner, kind, record, *, producer=None):
        if kind == "origins":
            producer.check(record)
            with pytest.raises(SyncError):
                copy_context().run(producer.check, record)
            producer.check(record)  # Rejecting the copy keeps the original live.
            context = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(SyncError):
                    pool.submit(context.run, producer.check, record).result()
            held.append((producer, deepcopy(record)))
        return publish(owner, kind, record, producer=producer)
    monkeypatch.setattr(authority.AuthorityGuard, "publish", observed)
    core = create(live17)
    assert len(held) == 1 and held[0][1]["core"] == core
    with pytest.raises(SyncError):
        held[0][0].check(held[0][1])
    with pytest.raises(SyncError):
        held[0][0].check_state_install(core, authority.record_ref(held[0][1]), live17.store.read())


def test_late_state_change_keeps_origin_but_refuses_state_install(live17, monkeypatch):
    setter = live17.store._install_source_genesis17
    changed = []
    def mutate(core, origin_ref, *, producer):
        assert len(origin_records(live17)) == 1
        status = deepcopy(live17.store.read()["status"])
        status["state"] = "syncing"
        live17.store.update({"status": status})
        changed.append(live17.store.read())
        return setter(core, origin_ref, producer=producer)
    monkeypatch.setattr(live17.store, "_install_source_genesis17", mutate)
    with pytest.raises(SyncError):
        create(live17)
    assert len(changed) == 1 and live17.store.read() == changed[0]
    assert live17.store.read()["source_execution"] is None
    assert len(origin_records(live17)) == 1


def test_public_initial_core_api_accepts_selectors_only(live17):
    module = import_module("reno.sync_source_genesis17")
    before = live17.store.read()
    for extra in ({"core": candidate(live17)}, {"claim": deepcopy(live17.remote.entry)},
                  {"producer": object()}, {"connection": object()}, {"origin_ref": None}):
        with pytest.raises(TypeError):
            module.create_initial_core(profile=live17.profile, store=live17.store, client=live17.client,
                item_id=live17.remote.entry["item_id"], **extra)
    assert live17.store.read() == before and origin_records(live17) == []


def test_journal_and_full_schema_reads_stay_outside_original_state_writer(live17, monkeypatch):
    from reno import budget_writer_witness as sql
    original_observe, original_full = authority.AuthorityGuard.observe, restore._actual_schema
    observations, schemas = [], []
    def observe_owned(owner, **kwargs):
        assert not sql.in_write_window()
        observations.append(kwargs)
        return original_observe(owner, **kwargs)
    def full(con):
        # Normal schema admission invokes the actual validator before BEGIN
        # inside the resource factory's frame. The production long verifier
        # is checked separately below, without conflating admission setup.
        schemas.append(con.in_transaction)
        return original_full(con)
    module = import_module("reno.sync_source_genesis17")
    original_long = module._SchemaCapture.full
    def long(schema):
        assert not sql.in_write_window()
        return original_long(schema)
    monkeypatch.setattr(authority.AuthorityGuard, "observe", observe_owned)
    monkeypatch.setattr(restore, "_actual_schema", full)
    monkeypatch.setattr(module._SchemaCapture, "full", long)
    core = create(live17)
    assert core == live17.store.read()["source_execution"]
    assert observations and schemas


@pytest.mark.parametrize("replacement", ["commit", "rollback", "commit_without_trace"])
def test_original_state_writer_transaction_cannot_be_replaced(live17, monkeypatch, replacement):
    setter = live17.store._install_source_genesis17
    replaced = []
    def replace(core, origin_ref, *, producer):
        con, token = producer._writer_frame
        assert con.in_transaction and token is not None
        if replacement == "commit_without_trace":
            con.set_trace_callback(None)
        if replacement == "rollback":
            con.rollback()
        else:
            con.commit()
        con.execute("BEGIN IMMEDIATE")
        replaced.append(con.in_transaction)
        return setter(core, origin_ref, producer=producer)
    monkeypatch.setattr(live17.store, "_install_source_genesis17", replace)
    with pytest.raises((SyncError, sqlite3.DatabaseError)):
        create(live17)
    assert replaced == [True]
    assert live17.store.read()["source_execution"] is None
    assert len(origin_records(live17)) == 1


@pytest.mark.parametrize("mutation", ["state", "sql"])
def test_mutation_during_final_commit_prevents_return_and_external_effect(live17, monkeypatch, mutation):
    setter = live17.store._install_source_genesis17
    changed, effects = [], []
    def late_hook(core, origin_ref, *, producer):
        result = setter(core, origin_ref, producer=producer)
        con, _token = producer._writer_frame
        def trace(statement):
            if statement.strip().upper() != "COMMIT" or changed:
                return
            if mutation == "state":
                status = deepcopy(live17.store.read()["status"])
                status["state"] = "syncing"
                live17.store.update({"status": status})
            else:
                con.execute("UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), live17.job["job_id"]))
            changed.append(mutation)
        con.set_trace_callback(trace)
        return result
    monkeypatch.setattr(live17.store, "_install_source_genesis17", late_hook)
    with pytest.raises((SyncError, sqlite3.DatabaseError, tasks.TaskError)):
        create(live17)
        effects.append("source request would begin only after create returned")
    assert changed == [mutation] and effects == []
    assert live17.store.read()["source_execution"] is not None
    assert len(origin_records(live17)) == 1


def test_original_cancellation_after_committed_origin_is_preserved(live17, monkeypatch):
    marker = BudgetDeferred("manual_pause")
    publish = authority.AuthorityGuard.publish
    def interrupted(owner, kind, record, *, producer=None):
        result = publish(owner, kind, record, producer=producer)
        if kind == "origins":
            def cancelled():
                raise marker
            live17.store.check_cancelled = cancelled
        return result
    monkeypatch.setattr(authority.AuthorityGuard, "publish", interrupted)
    with pytest.raises(BudgetDeferred) as caught:
        create(live17)
    assert caught.value is marker
    live17.store.check_cancelled = None
    assert live17.store.read()["source_execution"] is None and len(origin_records(live17)) == 1
