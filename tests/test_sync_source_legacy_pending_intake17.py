"""Actual old origin -> restored Home17 -> Cloud SQLite/ASGI -> sole bridge.

Original creation uses the real released16 worker/genesis producer and real
Cloud queue/session SQL. Recovery responses come from actual ASGI business
and immutable Cloud rows, not a supplied admission/disposition payload. Only
time and isolated role/profile routing are controlled. Prospective17 is local
to these disposable databases; no production global activation is claimed.
"""
from contextlib import contextmanager
from contextvars import Context
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import httpx
import pytest

from tests.asgi_transport import as_httpx_response

from reno import config, db, home_recovery17 as restore, home_recovery_runtime17 as runtime
from reno import home_recovery as legacy_restore, tasks, worker
from reno import home_recovery_source as sources, migrations, storage_admin
from reno import storage_lifecycle as lifecycle, sync_access, sync_protocol as protocol, sync_queue as queue
from reno import sync_budget_wait as old_budget
from reno import sync_source_authority as permanent, sync_source_execution as execution
from reno import sync_source_continuity_service as service, sync_source_readmit_contract as wire
from reno import sync_source_readmit_schema as readmit, sync_source_transport17 as transport, sync_state
from reno import sync_source_legacy_pending_intake17 as intake
from reno import sync_source_legacy_pending_receipt17 as completion
from reno import sync_source_legacy_pending_state17 as capsules
from reno.sync_client import SyncClient, normalize_budget_request
from reno.sync_protocol import SyncError
from reno.budget_contract import BudgetDeferred
from tests import test_sync_agent as old_agent, test_sync_source_continuity_service as cloud_fixture
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing, bind
from tests.test_sync_source_genesis17 import origin_records, permanent_files
from tests.test_sync_source_restore_cycle17 import actual_call

TOKEN = "synthetic-legacy-pending-api-token"


def sql_rows(profile):
    with sqlite3.connect(profile.database) as connection:
        return {name: tuple(tuple(row) for row in connection.execute('SELECT * FROM "' + name + '" ORDER BY 1'))
            for name in ("job", "task_request", "budget_operation", "budget_attempt", "worker_lease")}


@contextmanager
def original_worker(value):
    """Own the real original SQL job only while producing its local intent."""
    assert worker.execution_context() is None
    owner = value.job["owner_id"]
    with tasks.process_lock(profile=value.profile) as acquired:
        assert acquired
        assert tasks.acquire_worker(owner, profile=value.profile)
        try:
            with storage_admin.connection(value.profile) as connection:
                current = dict(connection.execute("SELECT * FROM job WHERE job_id=?",
                    (value.core["root_job_id"],)).fetchone())
                if current["status"] == "pending":
                    current = tasks.claim(connection, owner, profile=value.profile)
            assert current is not None and current["status"] == "running" and current["owner_id"] == owner
            assert (current["job_id"], current["continuation_id"], current["execution_generation"]) == (
                value.core["root_job_id"], value.core["continuation_id"], value.core["execution_generation"])
            assert tasks.renew_worker(owner, profile=value.profile)
            with storage_admin.connection(value.profile) as connection:
                current = dict(connection.execute("SELECT * FROM job WHERE job_id=?",
                    (value.core["root_job_id"],)).fetchone())
            with worker._execution(current, owner, threading.Event(), profile=value.profile):
                yield
        finally:
            tasks.release_worker(owner, path=value.profile.database)
    assert worker.execution_context() is None


def freeze_legacy_dispose_intent(value):
    """Freeze released16 bytes from its actual completed old restore producer."""
    with original_worker(value), value.store.reference_operation():
        binding = execution.historical_binding(value.store, value.profile)
        state = value.store.read()
        assert state["format_version"] == 5 and state["source_authority"] is None
        assert state["source_recovery_pending"] is None
        with storage_admin.connection(value.profile) as connection:
            restored = legacy_restore._record(connection, value.restore_operation)
            assert legacy_restore._validate_business(restored) == {
                key: item for key, item in value.restore_result.items() if key != "replayed"}
        original_restore = legacy_restore._read_intent(value.profile, value.restore_operation,
            json.loads(restored["request_json"]))
        assert original_restore["before_capture_sha256"] == value.restore_view["before_capture_sha256"]
        assert restored["original_core_sha256"] == permanent.digest(value.core)
        assert (restored["root_job_id"], restored["continuation_id"], restored["execution_generation"]) == (
            value.core["root_job_id"], value.core["continuation_id"], value.core["execution_generation"])
        assert (restored["before_library_epoch"], restored["after_library_epoch"]) == (
            value.core["local_epoch"], state["local_epoch"])
        original = value.legacy_budget_request
        before = intake.effects._before(state, value.core)
        body = {"kind": "source_dispose", "request_id": str(uuid.uuid4()),
            "source": deepcopy(value.source), "target": deepcopy(original["target"]),
            "lease": deepcopy(state["lease"]), "current_home_epoch": state["local_epoch"],
            "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
            "control_revision": state["recovery"]["revision"], "original": {
                "core_sha256": value.origin["core_sha256"], "home_epoch": value.core["local_epoch"],
                "item_id": value.core["item_id"], "submitted_url_sha256": value.core["submitted_url_sha256"],
                "claim": deepcopy(value.origin["claim"]), "recovery_generation": value.core["recovery_generation"],
                "recovery_stream": deepcopy(value.core["recovery_stream"])},
            "preservation": {"format_version": 1, "frontier_sha256": restored["frontier_sha256"],
                "certificate_sha256": original_restore["before_capture_sha256"],
                "from_epoch": restored["before_library_epoch"], "to_epoch": restored["after_library_epoch"]},
            "original_request": deepcopy(original), "registration": None}
        assert body["source"]["handshake"]["migration_version"] == 16
        assert wire.normalize_request(body) == body
        record = {"format_version": 1, "record_kind": "recovery_intent", "record_id": body["request_id"],
            "core_sha256": value.origin["core_sha256"], "request_sha256": wire.request_sha256(body),
            "request": body, "expected_before": before, "preservation_basis": {
                "format_version": 1, "producer": "home_knowledge_restore", "operation_id": value.restore_operation,
                "receipt_sha256": restored["result_sha256"]}, "manual_resume_basis": None}
        with permanent.owned(value.store) as owner:
            with storage_admin.connection(value.profile) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with permanent.producer(owner, binding, connection, historical=True) as producer:
                    result = owner.publish("requests", record, producer=producer)
                connection.commit()
        return {"record": result, "ref": permanent.record_ref(result)}


class ActualCloudTransport(httpx.BaseTransport):
    """Route the real SDK bytes to the actual app and business SQL."""
    def __init__(self, http, profile, monkeypatch, now):
        self.http, self.profile, self.monkeypatch, self.now = http, profile, monkeypatch, now
        self.seen = []

    def handle_request(self, request):
        self.seen.append((request.url.path, bytes(request.content)))
        old_profile, old_get, old_clock = storage_admin.profile, config.get, queue._clock
        with self.monkeypatch.context() as scoped:
            scoped.setattr(config, "is_cloud", lambda: True)
            scoped.setattr(config, "get", lambda key, *args: "cloud" if key == "node_role" else old_get(key, *args))
            scoped.setattr(storage_admin, "profile", lambda **kwargs: old_profile(**kwargs) if kwargs else self.profile)
            # Ordinary heartbeat opens the default DB directly. It must use
            # this actual Cloud database, just as the profile-aware endpoints.
            scoped.setattr(config, "db_path", lambda: self.profile.database)
            scoped.setattr(queue, "_clock", lambda now: old_clock(self.now if now is None else now))
            # The Cloud is a different service process. It inherits neither
            # the client's Home SH guard nor its worker/HTTP capability frame.
            return as_httpx_response(Context().run(self.http._transport.handle_request, request))


@pytest.fixture
def real_cycle(tmp_path, monkeypatch, request):
    # Create the real legacy Home before changing either schema gate/rules.
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 16)
    monkeypatch.setattr(protocol, "PROTOCOL_VERSION", 2)
    generated = old_agent.env.__wrapped__(tmp_path, monkeypatch)
    remote = next(generated)
    cloud = lifecycle.StorageProfile(tmp_path / "cloud/data/reno.db", tmp_path / "cloud/media",
        tmp_path / "cloud/reports", tmp_path / "cloud/checkpoints")
    db.initialize(cloud.database)
    lifecycle.initialize(cloud)
    old_agent.install_import(remote, monkeypatch)
    remote.claims = []
    with storage_admin.connection(cloud) as connection:
        target = sync_access.identity(connection)
        assert queue.enqueue(connection, ["https://b23.tv/fixtureAlias"]) == 1
        connection.commit()
    remote.target = deepcopy(target)
    config.set_local({"sync_target_library_id": target["library_id"], "sync_target_node_id": target["node_id"]})

    def cloud_heartbeat(source, session_id, lease=None):
        with storage_admin.connection(cloud) as connection:
            queue.bind_peer(connection, source)
            result = queue.heartbeat(connection, source, session_id, lease=lease)
            if not remote.claims:
                remote.claims = queue.claim(connection, source, result["lease"], limit=1)
            connection.commit()
            return result

    def real_heartbeat(source, session_id, lease=None):
        return Context().run(cloud_heartbeat, source, session_id, lease)

    monkeypatch.setattr(remote, "heartbeat", real_heartbeat)
    with home_cycle(remote) as cycle, executing(cycle) as (job, _):
        binding = bind(cycle)
        value = SimpleNamespace(profile=cycle.storage_profile, store=cycle.store, core=binding.core,
            job=deepcopy(job), cloud=cloud, source=deepcopy(cycle.source), target=deepcopy(target))
        records = origin_records(value)
        assert len(records) == 1
        value.origin = deepcopy(records[0][1])
        assert value.store.read()["format_version"] == 3 and "source_authority" not in value.store.read()
        assert value.origin["claim"] == remote.claims[0]["claim"]
        value.legacy_mode = getattr(request, "param", None)
        value.legacy_dispose = value.legacy_mode in {
            "legacy_dispose", "legacy_resume", "legacy_undispatched"}
        if value.legacy_dispose:
            # The released public State producer freezes this original wait.
            # The actual Cloud16 writer applies it, while Home deliberately
            # retains its genuine request/receipt gap for later disposition.
            value.store.enable_source_control()
            state = value.store.read()
            original = {"kind": "budget_defer", "request_id": str(uuid.uuid4()),
                "source": deepcopy(value.source), "target": {key: value.target[key] for key in ("library_id", "node_id")},
                "lease": deepcopy(state["lease"]), "claim": deepcopy(value.origin["claim"]),
                "recovery_generation": state["recovery"]["generation"],
                "recovery_stream": deepcopy(state["recovery"]["stream"]), "reason": "daily_limit",
                "retry_at": queue._format_time(datetime.now(timezone.utc) + timedelta(days=1)),
                "budget_operation_id": None}
            # Check the complete frozen wire and State body before the public
            # writer's sanitizer, so malformed fixture facts fail at their cause.
            assert normalize_budget_request(original) == original
            expected_pending = {"defer_request": original, "defer_receipt": None,
                "resume_request": None, "resume_receipt": None}
            assert sync_state._budget_pending(expected_pending, state) == expected_pending
            assert value.store.install_budget_defer_request(original) == expected_pending
            def apply_original():
                with storage_admin.connection(cloud) as connection:
                    result = old_budget.apply_request(connection, original)
                    connection.commit()
                    return result
            value.legacy_budget_request = deepcopy(original)
            if value.legacy_mode == "legacy_undispatched":
                # Freeze the real old request without ever delivering it. An
                # absent Cloud outcome cannot authorize a negative disposition.
                value.legacy_budget_receipt = None
            else:
                value.legacy_budget_receipt = Context().run(apply_original)
            if value.legacy_mode == "legacy_resume":
                cycle.store.record_budget_defer_receipt(original["request_id"], value.legacy_budget_receipt)
                value.legacy_parent = {"request": deepcopy(original),
                    "receipt": deepcopy(value.legacy_budget_receipt)}
                assert not tasks.sync_resume_authorized(job["job_id"], original["request_id"],
                    profile=value.profile)
                tasks.resume_sync(job["job_id"], profile=value.profile)
                assert tasks.sync_resume_authorized(job["job_id"], original["request_id"],
                    profile=value.profile)
                applied = []

                def original_resume_committed_response_lost(body, *, defer_receipt=None):
                    assert body["kind"] == "budget_resume"
                    assert defer_receipt == value.legacy_parent["receipt"]
                    def commit_original_resume():
                        with storage_admin.connection(cloud) as connection:
                            result = old_budget.apply_request(connection, body)
                            connection.commit()
                            return result
                    applied.append((deepcopy(body), Context().run(commit_original_resume)))
                    # The original Cloud receipt exists. Home has not observed
                    # its response, so the real resume UUID remains pending.
                    raise RuntimeError("synthetic response loss after original Cloud commit")

                monkeypatch.setattr(remote, "budget_request", original_resume_committed_response_lost,
                    raising=False)
                with pytest.raises(BudgetDeferred) as stopped:
                    cycle.resume_budget()
                assert stopped.value.reason == "indeterminate" and len(applied) == 1
                value.legacy_budget_request, value.legacy_budget_receipt = applied[0]
                pending = value.store.read()["budget_pending"]
                assert pending["defer_request"] == value.legacy_parent["request"]
                assert pending["defer_receipt"] == value.legacy_parent["receipt"]
                assert pending["resume_request"] == value.legacy_budget_request
                assert pending["resume_receipt"] is None
                assert value.store.read()["source_execution"] == value.core
    # Both variants use a real checkpoint and subsequent knowledge mutation.
    # Released16 freezes its old restore proof before the schema transition;
    # the held variant later uses the actual modern restore producer.
    source_id = str(uuid.uuid4())
    sources.create_checkpoint(profile=value.profile, source_id=source_id)
    old_agent.add_video("legacy_pending_later")
    if value.legacy_dispose:
        value.restore_view = legacy_restore.preview(profile=value.profile, source_id=source_id)
        value.restore_operation = str(uuid.uuid4())
        value.restore_result = legacy_restore.apply(profile=value.profile, source_id=source_id,
            expected_digest=value.restore_view["digest"], operation_id=value.restore_operation)
        # The immutable bare16 protection image remains State4. Promote only
        # today's representation after restore, without adopting an origin.
        before_promotion = value.store.read()
        assert before_promotion["format_version"] == 4
        promoted = value.store.enable_source_readmit()
        assert promoted == {**before_promotion, "format_version": 5,
            "source_authority": None, "source_recovery_pending": None}
        assert migrations.SCHEMA_VERSION == 16
        value.legacy_intent = freeze_legacy_dispose_intent(value)
    for profile in (value.profile, cloud):
        with storage_admin.connection(profile) as connection:
            readmit.install(connection)
            connection.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
            connection.commit()
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 17)
    monkeypatch.setattr(protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 1, raising=False)
    monkeypatch.setattr(protocol, "schema_rules", cloud_fixture.rules)
    value.store.enable_source_readmit()
    with storage_admin.connection(value.profile) as connection:
        value.source = sync_access.identity(connection)
    original_state = value.store.read()
    old_lease = original_state["lease"]
    # Peer refresh requires expired sessions/claims. The held variant acquires
    # a real next lease; historical16 keeps its frozen original lease/body and
    # relies on the actual old positive receipt for disposition.
    value.now = datetime.fromisoformat(old_lease["lease_until"].replace("Z", "+00:00")) + timedelta(seconds=1)
    if not value.legacy_dispose:
        def refresh_expired_peer():
            with storage_admin.connection(cloud) as connection:
                queue.refresh_peer_schema(connection, value.source, now=value.now)
                with pytest.raises(SyncError) as expired:
                    queue.heartbeat(connection, value.source, original_state["session_id"],
                        lease=old_lease, now=value.now)
                assert expired.value.code == "SYNC_LEASE_EXPIRED"
                connection.commit()

        Context().run(refresh_expired_peer)
        # Follow _Cycle.acquire: archive the original references before clearing
        # only the expired lease, then install the actual new Cloud heartbeat.
        value.store.archive_progress("claim_changed")
        assert value.store.read() == original_state
        cleared = value.store.update({"lease": None})
        assert cleared == {**original_state, "lease": None}

        def acquire_current_lease():
            with storage_admin.connection(cloud) as connection:
                result = queue.heartbeat(connection, value.source, original_state["session_id"], now=value.now)
                connection.commit()
                return result

        renewed = Context().run(acquire_current_lease)
        observed = value.store.observe_recovery(renewed["recovery"], lease=renewed["lease"])
        assert observed["source_execution"] == original_state["source_execution"] == value.core
        assert observed["claims"] == original_state["claims"]
        assert observed["lease"] == renewed["lease"]
    else:
        def refresh_historical_peer():
            with storage_admin.connection(cloud) as connection:
                queue.refresh_peer_schema(connection, value.source, now=value.now)
                connection.commit()

        Context().run(refresh_historical_peer)
        assert value.store.read() == original_state
    with monkeypatch.context() as scoped:
        scoped.setattr(config, "is_cloud", lambda: True)
        service.activate(profile=cloud, continuity_id=str(uuid.uuid4()),
            activated_at=queue._format_time(value.now))
    if not value.legacy_dispose:
        view = restore.preview(profile=value.profile, source_id=source_id)
        value.restore_operation = str(uuid.uuid4())
        restore.apply(profile=value.profile, source_id=source_id, expected_digest=view["digest"],
            operation_id=value.restore_operation)
    assert value.store.read()["source_authority"] is None
    from fastapi.testclient import TestClient
    from app.main import create_app
    config.set_local({"sync_token": TOKEN})
    http = TestClient(create_app(), base_url="https://sync.example.test/reno", root_path="/reno")
    value.transport = ActualCloudTransport(http, cloud, monkeypatch, value.now)
    value.client = SyncClient(value.core["binding"]["target_url"], token_provider=lambda: TOKEN, transport=value.transport)
    try:
        yield value
    finally:
        http.close()
        try:
            next(generated)
        except StopIteration:
            pass


def original_intent(value):
    """Real historical producer saves wire1 with untouched missing-origin before."""
    if value.legacy_dispose:
        return deepcopy(value.legacy_intent)
    core = value.core
    with original_worker(value), value.store.reference_operation():
        binding = execution.historical_binding(value.store, value.profile)
        with permanent.owned(value.store) as owner:
            state = value.store.read()
            before = intake.effects._before(state, core)
            assert before["source_authority"] is None
            with runtime.capture_completed_locked(profile=value.profile, store=value.store,
                    operation_id=value.restore_operation) as restored:
                preservation = restored.preservation_observation(from_epoch=core["local_epoch"])
                provenance = restored.provenance_ref()
            request = {"kind": "source_readmit", "request_id": str(uuid.uuid4()),
                "source": deepcopy(value.source), "target": {key: value.target[key] for key in ("library_id", "node_id")},
                "lease": deepcopy(state["lease"]), "current_home_epoch": state["local_epoch"],
                "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
                "control_revision": state["recovery"]["revision"], "original": {
                    "core_sha256": value.origin["core_sha256"], "home_epoch": core["local_epoch"],
                    "item_id": core["item_id"], "submitted_url_sha256": core["submitted_url_sha256"],
                    "claim": deepcopy(value.origin["claim"]), "recovery_generation": core["recovery_generation"],
                    "recovery_stream": deepcopy(core["recovery_stream"])},
                "preservation": preservation["preservation"], "expected_claim": deepcopy(before["claim"]),
                "previous_admission_id": None, "dispositions": [], "defer_receipt": None,
                "resume_receipt": None, "manual_resume_basis": None}
            assert wire.normalize_request(request) == request
            record = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
                "core_sha256": value.origin["core_sha256"], "request_sha256": wire.request_sha256(request),
                "request": request, "expected_before": before, "preservation_basis": provenance, "manual_resume_basis": None}
            with storage_admin.connection(value.profile) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with permanent.producer(owner, binding, connection, historical=True) as producer:
                    result = owner.publish("requests", record, producer=producer)
                connection.commit()
    return {"record": result, "ref": permanent.record_ref(result)}


def selectors(value, intent):
    return {"profile": value.profile, "store": value.store, "request_id": intent["record"]["record_id"],
        "request_sha256": intent["record"]["request_sha256"], "expected_intent_sha256": intent["ref"]["record_sha256"]}


def received(value):
    intent = original_intent(value)
    args = selectors(value, intent)
    with value.store.reference_operation():
        stage = actual_call(intake.install_original_pending17_locked, **args)
    assert stage["source_authority"] is None and stage["source_recovery_pending"] == intake._pending(intent["record"])
    body = intent["record"]["request"]
    if value.legacy_dispose:
        with intake.original_legacy_http_call17(**args, client=value.client):
            answer = value.client.source_dispose(body)
    else:
        answer = value.client.source_readmit(body)
    with storage_admin.connection(value.cloud) as connection:
        table, field = (readmit.DISPOSITION, "request_id") if value.legacy_dispose else (readmit.ADMISSION, "admission_id")
        row = connection.execute("SELECT request_json,result_json FROM " + table + " WHERE " + field + "=?",
            (intent["record"]["record_id"],)).fetchone()
        assert json.loads(row[0]) == intent["record"]["request"] and json.loads(row[1]) == answer
    with value.store.reference_operation():
        saved = actual_call(intake.publish_original_pending_receipt17_locked, client=value.client, **args)
    assert value.store.read() == stage
    return intent, args, saved, answer


def test_actual_cloud_sql_http_and_original_missing_origin_are_consumed_by_one_cas(real_cycle, monkeypatch):
    value = real_cycle
    original = deepcopy(value.origin)
    intent, args, saved, answer = received(value)
    before, paid, files = deepcopy(value.store.read()), sql_rows(value.profile), permanent_files(value)
    seen = deepcopy(value.transport.seen)
    writes = []
    actual_write = sync_state._write

    def counted(directory, name, data, **kwargs):
        if directory.path == value.store.data_dir and name == sync_state._STATE:
            writes.append(bytes(data))
        return actual_write(directory, name, data, **kwargs)

    monkeypatch.setattr(sync_state, "_write", counted)
    with value.store.reference_operation():
        after = actual_call(intake.reconcile_original_pending17_locked, **args)
    assert len(writes) == 1
    assert after["source_recovery_pending"] is None
    assert after["source_authority"]["origin"] == permanent.record_ref(original)
    assert after["source_admission"]["record_sha256"] == saved["ref"]["record_sha256"]
    assert after["claims"][0]["claim"] == answer["claim"]
    for field in ("source_execution", "lease", "recovery", "local_epoch", "budget_pending", "source_control_pending"):
        assert after[field] == before[field]
    assert sql_rows(value.profile) == paid and permanent_files(value) == files
    assert value.transport.seen == seen
    assert intent["record"]["expected_before"]["source_authority"] is None
    assert capsules.path(value.profile, intent["record"]["record_id"]).is_file()


def test_original_uuid_state_replay_is_zero_rpc_and_zero_state_write(real_cycle, monkeypatch):
    value = real_cycle
    intent, args, saved, _ = received(value)
    with value.store.reference_operation():
        first = actual_call(intake.reconcile_original_pending17_locked, **args)
    state_path = value.store.data_dir / sync_state._STATE
    capsule_path = capsules.path(value.profile, intent["record"]["record_id"])
    before = (state_path.read_bytes(), state_path.stat().st_mtime_ns,
        capsule_path.read_bytes(), capsule_path.stat().st_mtime_ns, permanent_files(value), sql_rows(value.profile))
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("State replay invoked RPC"))
    monkeypatch.setattr(transport, "capture_receipt", lambda *a, **kw: pytest.fail("State replay requested a ticket"))
    with value.store.reference_operation():
        assert actual_call(intake.reconcile_original_pending17_locked, **args) == first
        assert actual_call(intake.publish_original_pending_receipt17_locked, client=None, **args) == saved
    assert (state_path.read_bytes(), state_path.stat().st_mtime_ns,
        capsule_path.read_bytes(), capsule_path.stat().st_mtime_ns, permanent_files(value), sql_rows(value.profile)) == before


def test_completed_original_reads_after_real_heartbeat_without_overwriting_current_state(real_cycle, monkeypatch):
    value = real_cycle
    intent, args, _, _ = received(value)
    with value.store.reference_operation():
        first = actual_call(intake.reconcile_original_pending17_locked, **args)
    marker = capsules.path(value.profile, intent["record"]["record_id"]).with_name(
        intent["record"]["record_id"] + ".applied.json")
    assert marker.is_file()
    value.transport.now += timedelta(seconds=60)
    heartbeat = value.client.heartbeat(value.source, first["session_id"], lease=first["lease"])
    value.store.observe_recovery(heartbeat["recovery"], lease=heartbeat["lease"])
    changed = value.store.read()
    value.store.update({"status": {**changed["status"], "state": "waiting_lease",
        "updated_at": queue._format_time(value.transport.now)}})
    current = deepcopy(value.store.read())
    assert current["lease"] != first["lease"] and current["status"] != first["status"]
    state_path = value.store.data_dir / sync_state._STATE
    before = state_path.read_bytes(), state_path.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Completed original replay performed HTTP"))
    with value.store.reference_operation():
        assert actual_call(intake.reconcile_original_pending17_locked, **args) == current
    assert (state_path.read_bytes(), state_path.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns) == before


def test_prepared_capsule_is_not_completed_state_evidence(real_cycle):
    value = real_cycle
    intent, args, _, _ = received(value)
    with value.store.reference_operation(), intake.capture_legacy_pending17_locked(**args) as captured:
        capsules.prepare_locked(captured)
        with pytest.raises(SyncError, match="preimage"):
            capsules.finish_locked(captured)
    marker = capsules.path(value.profile, intent["record"]["record_id"]).with_name(
        intent["record"]["record_id"] + ".applied.json")
    assert not marker.exists()
    before = value.store.read()
    value.store.update({"status": {**before["status"], "state": "waiting_lease"}})
    current = deepcopy(value.store.read())
    with value.store.reference_operation(), pytest.raises(SyncError):
        actual_call(intake.reconcile_original_pending17_locked, **args)
    assert value.store.read() == current and current["source_recovery_pending"] is not None
    assert not marker.exists()


def test_actual_state_after_closes_the_capsule_applied_marker_gap_without_new_cas(real_cycle, monkeypatch):
    value = real_cycle
    intent, args, _, _ = received(value)
    original_write, hit = sync_state._write, []
    stopped = RuntimeError("synthetic applied marker interruption")

    def interrupted(directory, name, data, **kwargs):
        if directory.path.name == capsules.DIRECTORY and name.endswith(".applied.json") and not hit:
            hit.append(name)
            raise stopped
        return original_write(directory, name, data, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(sync_state, "_write", interrupted)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            actual_call(intake.reconcile_original_pending17_locked, **args)
        assert raised.value is stopped and len(hit) == 1
    actual_after = deepcopy(value.store.read())
    assert actual_after["source_recovery_pending"] is None
    state_path = value.store.data_dir / sync_state._STATE
    before = state_path.read_bytes(), state_path.stat().st_mtime_ns
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Marker recovery repeated HTTP"))
    with value.store.reference_operation():
        assert actual_call(intake.reconcile_original_pending17_locked, **args) == actual_after
    assert (state_path.read_bytes(), state_path.stat().st_mtime_ns) == before
    marker = capsules.path(value.profile, intent["record"]["record_id"]).with_name(
        intent["record"]["record_id"] + ".applied.json")
    assert marker.is_file()


@pytest.mark.parametrize("real_cycle", ["legacy_dispose"], indirect=True)
def test_actual16_frozen_wire_and_positive_budget_sql_disposition_install_without_rewrite(real_cycle, monkeypatch):
    value = real_cycle
    body_before = execution._canonical(value.legacy_intent["record"])
    intent, args, saved, answer = received(value)
    assert answer["disposition"] == "applied" and answer["original_receipt"] == value.legacy_budget_receipt
    assert saved["record"]["format_version"] == 1
    assert intent["record"]["request"]["source"]["handshake"]["migration_version"] == 16
    before, paid = deepcopy(value.store.read()), sql_rows(value.profile)
    seen = deepcopy(value.transport.seen)
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Positive dispose replay requested HTTP"))
    with value.store.reference_operation():
        after = actual_call(intake.reconcile_original_pending17_locked, **args)
        assert actual_call(intake.reconcile_original_pending17_locked, **args) == after
    assert after["source_authority"]["origin"] == permanent.record_ref(value.origin)
    assert after["budget_pending"]["defer_receipt"] == value.legacy_budget_receipt
    assert after["source_recovery_pending"] is None
    for field in ("source_execution", "lease", "recovery", "local_epoch", "claims"):
        assert after[field] == before[field]
    assert execution._canonical(intent["record"]) == body_before
    assert value.transport.seen == seen and sql_rows(value.profile) == paid


@pytest.mark.parametrize("point", ["before_body", "after_body", "reserved_db_head",
    "committed_db_head", "committed_checkpoint_head"])
def test_real_http_saved_receipt_tail_resumes_without_another_ticket_or_state_effect(real_cycle, monkeypatch, point):
    value = real_cycle
    intent = original_intent(value)
    args = selectors(value, intent)
    with value.store.reference_operation():
        actual_call(intake.install_original_pending17_locked, **args)
    answer = value.client.source_readmit(intent["record"]["request"])
    original_write, events, hit = permanent.AuthorityGuard._write, [], []
    stopped = RuntimeError("synthetic original pending receipt interruption")

    def interrupted(owner, directory, name, raw, **kwargs):
        selected = directory.path.name == "receipts" and name == intent["record"]["record_id"] + ".json"
        state = None if not events else events[-1]["state"]
        target = ((point == "before_body" and selected)
            or (point == "reserved_db_head" and state == "reserved" and name == permanent.DB_HEAD_NAME)
            or (point == "committed_db_head" and state == "committed" and name == permanent.DB_HEAD_NAME)
            or (point == "committed_checkpoint_head" and state == "committed" and name == permanent.CHECKPOINT_HEAD_NAME))
        if target and not hit:
            hit.append(name)
            raise stopped
        result = original_write(owner, directory, name, raw, **kwargs)
        if directory.path.name == "journal":
            event = json.loads(raw)
            if event["kind"] == "receipts" and event["key"] == intent["record"]["record_id"]:
                events.append(event)
        if point == "after_body" and selected and not hit:
            hit.append(name)
            raise stopped
        return result

    before, paid, seen = deepcopy(value.store.read()), sql_rows(value.profile), deepcopy(value.transport.seen)
    with monkeypatch.context() as scoped:
        scoped.setattr(permanent.AuthorityGuard, "_write", interrupted)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            actual_call(intake.publish_original_pending_receipt17_locked, client=value.client, **args)
        assert raised.value is stopped and len(hit) == 1
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Reserved completion repeated HTTP"))
    monkeypatch.setattr(transport, "capture_receipt", lambda *a, **kw: pytest.fail("Reserved completion fabricated HTTP ticket"))
    with value.store.reference_operation():
        saved = actual_call(completion.complete_original_pending_receipt17_locked,
            **{name: arg for name, arg in args.items() if name != "expected_intent_sha256"})
    assert saved["record"]["result"] == answer
    assert value.store.read() == before and sql_rows(value.profile) == paid and value.transport.seen == seen


def test_completed_real_http_fact_survives_local_pause_but_cannot_consume_the_old_fence(real_cycle):
    value = real_cycle
    intent = original_intent(value)
    args = selectors(value, intent)
    with value.store.reference_operation():
        actual_call(intake.install_original_pending17_locked, **args)
    answer = value.client.source_readmit(intent["record"]["request"])
    recovery = deepcopy(value.store.read()["recovery"])
    recovery.update(state="held", revision=recovery["revision"] + 1)
    value.store.observe_recovery(recovery)
    before = deepcopy(value.store.read())
    with value.store.reference_operation():
        saved = actual_call(intake.publish_original_pending_receipt17_locked, client=value.client, **args)
        assert saved["record"]["result"] == answer
        with pytest.raises(SyncError):
            intake.reconcile_original_pending17_locked(**args)
    assert value.store.read() == before
    assert before["source_authority"] is None
    assert not capsules.path(value.profile, intent["record"]["record_id"]).exists()


@pytest.mark.parametrize("after_write", [False, True])
def test_capsule_and_original_receipt_resume_the_exact_state_write_gap(real_cycle, monkeypatch, after_write):
    value = real_cycle
    intent, args, saved, _ = received(value)
    original_write = sync_state._write
    stopped = RuntimeError("synthetic original pending State write interruption")
    hit = []

    def interrupted(directory, name, data, **kwargs):
        selected = directory.path == value.store.data_dir and name == sync_state._STATE
        if selected and not hit and not after_write:
            hit.append(name)
            raise stopped
        result = original_write(directory, name, data, **kwargs)
        if selected and not hit and after_write:
            hit.append(name)
            raise stopped
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(sync_state, "_write", interrupted)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            actual_call(intake.reconcile_original_pending17_locked, **args)
        assert raised.value is stopped and len(hit) == 1
    assert capsules.path(value.profile, intent["record"]["record_id"]).is_file()
    value.store = sync_state.StateStore(value.store.data_dir, profile=value.profile)
    args["store"] = value.store
    seen = deepcopy(value.transport.seen)
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Interrupted CAS repeated RPC"))
    with value.store.reference_operation():
        after = actual_call(intake.reconcile_original_pending17_locked, **args)
        assert actual_call(intake.publish_original_pending_receipt17_locked, client=None, **args) == saved
    assert after["source_recovery_pending"] is None and after["source_authority"]["origin"] == permanent.record_ref(value.origin)
    assert value.transport.seen == seen


@pytest.mark.parametrize("damage", ["request", "intent_sha", "receipt_sha", "effect"])
def test_unknown_capsule_collision_cannot_rewrite_original_state_or_receipt(real_cycle, monkeypatch, damage):
    value = real_cycle
    intent, args, _, _ = received(value)
    with value.store.reference_operation(), intake.capture_legacy_pending17_locked(**args) as captured:
        prepared = capsules.prepare_locked(captured)
    path = prepared["path"]
    body = json.loads(path.read_bytes())
    if damage == "request":
        body["request_id"] = str(uuid.uuid4())
    elif damage == "effect":
        after = json.loads(body["after_json"])
        after["claims"][0]["attempts"] += 1
        body["after_json"] = execution._canonical(after)
        body["after_sha256"] = hashlib.sha256(body["after_json"].encode()).hexdigest()
    else:
        body[{"intent_sha": "intent_sha256", "receipt_sha": "receipt_sha256"}[damage]] = "f" * 64
    path.write_text(json.dumps(body), encoding="utf-8")
    before = deepcopy(value.store.read()), permanent_files(value), sql_rows(value.profile), path.read_bytes()
    with value.store.reference_operation(), pytest.raises(SyncError):
        actual_call(intake.reconcile_original_pending17_locked, **args)
    assert (value.store.read(), permanent_files(value), sql_rows(value.profile), path.read_bytes()) == before


@pytest.mark.parametrize("capability", [intake.LegacyPendingIntakeCapture17, intake.LegacyPendingHttpPublisher17,
    intake.LegacyOriginalHttpCall17, completion.OriginalPendingReceiptCapture17, completion.PendingReceiptCompletionGuard17])
def test_bridge_capabilities_cannot_be_publicly_constructed(capability):
    with pytest.raises(SyncError):
        capability()
