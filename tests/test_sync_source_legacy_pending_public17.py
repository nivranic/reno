"""Public lifecycle coverage with released16 origins and actual Cloud SQL.

These tests reuse the real worker/checkpoint/restore fixture and its actual
SDK -> FastAPI ASGI -> Cloud SQLite transport. Public lifecycle functions
select and consume the immutable original intent; no capability, HTTP ticket,
admission or disposition is supplied by a test payload.
"""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from reno import budget_writer_witness as sql, storage_admin
from reno import storage_lifecycle as lifecycle, sync_source_authority as permanent
from reno import sync_source_claim as engine, sync_source_lifecycle17 as effects
from reno import sync_source_readmit_schema as readmit, sync_source_transport17 as transport, sync_state, sync_queue
from reno import sync_source_legacy_pending_state17 as capsules
from reno.sync_protocol import SyncError
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_legacy_pending_intake17 import real_cycle, original_intent, sql_rows
from tests.test_sync_source_restore_cycle17 import actual_call


def public_selectors(value, intent):
    return {"profile": value.profile, "store": value.store,
        "request_id": intent["record"]["record_id"]}


def stage(value, intent):
    return actual_call(effects.install_recovery_pending17_locked, **public_selectors(value, intent))


def deliver(value, intent):
    return actual_call(effects.deliver_recovery17, client=value.client, **public_selectors(value, intent))


def reconcile(value, intent):
    return actual_call(effects.reconcile_recovery17_locked, **public_selectors(value, intent))


def cloud_result(value, intent):
    table, field = ((readmit.DISPOSITION, "request_id") if value.legacy_dispose
        else (readmit.ADMISSION, "admission_id"))
    with storage_admin.connection(value.cloud) as connection:
        row = connection.execute("SELECT request_json,result_json FROM " + table + " WHERE " + field + "=?",
            (intent["record"]["record_id"],)).fetchone()
    assert row is not None
    assert json.loads(row[0]) == intent["record"]["request"]
    return json.loads(row[1])


def state_file(value):
    path = value.store.data_dir / sync_state._STATE
    return path.read_bytes(), path.stat().st_mtime_ns


def replay_files(value, intent):
    capsule = capsules.path(value.profile, intent["record"]["record_id"])
    marker = capsule.with_name(intent["record"]["record_id"] + ".applied.json")
    return (state_file(value), capsule.read_bytes(), capsule.stat().st_mtime_ns,
        marker.read_bytes(), marker.stat().st_mtime_ns, permanent_files(value), sql_rows(value.profile))


def forbid_new_http(value, monkeypatch):
    monkeypatch.delattr(value.client, "_source_transport17_broker", raising=False)
    monkeypatch.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Original UUID replay repeated HTTP"))
    monkeypatch.setattr(transport, "capture_receipt", lambda *a, **kw: pytest.fail("Original UUID replay requested a new ticket"))


def test_public_missing_origin_readmit_stages_delivers_then_consumes_one_state_cas(real_cycle, monkeypatch):
    value = real_cycle
    intent = original_intent(value)
    original_intent_bytes = engine._encoded(intent["record"])
    initial = deepcopy(value.store.read())
    staged = stage(value, intent)
    assert staged["source_authority"] is None
    assert staged["source_recovery_pending"] == {"format_version": 1, "kind": "source_readmit",
        "request_id": intent["record"]["record_id"], "request_sha256": intent["record"]["request_sha256"],
        "core_sha256": intent["record"]["core_sha256"], "intent_sha256": permanent.digest(intent["record"])}
    assert {key: data for key, data in staged.items() if key != "source_recovery_pending"} == {
        key: data for key, data in initial.items() if key != "source_recovery_pending"}

    actual_transport, entered = value.transport.handle_request, []

    def checked_http(request):
        assert value.store._reference_frame is None and value.store._directory is None
        assert permanent._CURRENT.get() is None and lifecycle.current_guard() is None
        assert not sql.in_write_window()
        entered.append(bytes(request.content))
        return actual_transport(request)

    monkeypatch.setattr(value.transport, "handle_request", checked_http)
    saved = deliver(value, intent)
    assert entered == [engine._encoded(intent["record"]["request"]).encode("utf-8")]
    answer = cloud_result(value, intent)
    assert saved["record"]["result"] == answer
    assert saved["ref"] == permanent.record_ref(saved["record"])
    assert value.store.read() == staged
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        assert owner.read(saved["ref"]) == saved["record"]
        assert owner.read(intent["ref"]) == intent["record"]

    paid, files, seen = sql_rows(value.profile), permanent_files(value), deepcopy(value.transport.seen)
    actual_write, writes = sync_state._write, []

    def counted(directory, name, data, **kwargs):
        if directory.path == value.store.data_dir and name == sync_state._STATE:
            writes.append(name)
        return actual_write(directory, name, data, **kwargs)

    monkeypatch.setattr(sync_state, "_write", counted)
    after = reconcile(value, intent)
    assert writes == [sync_state._STATE]
    assert after["source_authority"]["origin"] == permanent.record_ref(value.origin)
    assert after["source_recovery_pending"] is None
    assert after["source_admission"]["record_sha256"] == saved["ref"]["record_sha256"]
    assert after["claims"][0]["claim"] == answer["claim"]
    for field in ("source_execution", "lease", "recovery", "local_epoch", "budget_pending", "source_control_pending"):
        assert after[field] == staged[field]
    assert sql_rows(value.profile) == paid and permanent_files(value) == files and value.transport.seen == seen
    assert engine._encoded(intent["record"]) == original_intent_bytes
    assert intent["record"]["expected_before"]["source_authority"] is None


@pytest.mark.parametrize("real_cycle", [None, "legacy_dispose"], indirect=True)
def test_public_stored_original_receipt_is_read_without_http_ticket_or_state_write(real_cycle, monkeypatch):
    value = real_cycle
    intent = original_intent(value)
    stage(value, intent)
    saved = deliver(value, intent)
    assert saved["record"]["result"] == cloud_result(value, intent)
    before = state_file(value), permanent_files(value), sql_rows(value.profile), deepcopy(value.transport.seen)
    forbid_new_http(value, monkeypatch)
    assert deliver(value, intent) == saved
    assert (state_file(value), permanent_files(value), sql_rows(value.profile), value.transport.seen) == before


def test_public_completed_uuid_reads_current_state_after_actual_heartbeat(real_cycle, monkeypatch):
    value = real_cycle
    intent = original_intent(value)
    stage(value, intent)
    saved = deliver(value, intent)
    first = reconcile(value, intent)
    value.transport.now += timedelta(seconds=60)
    heartbeat = value.client.heartbeat(value.source, first["session_id"], lease=first["lease"])
    value.store.observe_recovery(heartbeat["recovery"], lease=heartbeat["lease"])
    changed = value.store.read()
    value.store.update({"status": {**changed["status"], "state": "waiting_lease",
        "updated_at": sync_queue._format_time(value.transport.now)}})
    current = deepcopy(value.store.read())
    assert current["lease"] != first["lease"] and current["status"] != first["status"]
    assert current["source_recovery_pending"] is None
    before, seen = replay_files(value, intent), deepcopy(value.transport.seen)
    forbid_new_http(value, monkeypatch)
    assert deliver(value, intent) == saved
    assert reconcile(value, intent) == current
    assert reconcile(value, intent) == current
    assert replay_files(value, intent) == before and value.transport.seen == seen


@pytest.mark.parametrize("real_cycle", ["legacy_dispose"], indirect=True)
def test_public_source16_dispose_preserves_original_body_and_actual_budget_receipt(real_cycle, monkeypatch):
    value = real_cycle
    intent = original_intent(value)
    original = engine._encoded(intent["record"])
    assert intent["record"]["request"]["source"]["handshake"]["migration_version"] == 16
    staged = stage(value, intent)
    assert staged["source_authority"] is None
    saved = deliver(value, intent)
    answer = cloud_result(value, intent)
    assert saved["record"]["format_version"] == 1
    assert saved["record"]["result"] == answer
    assert answer["disposition"] == "applied"
    assert answer["original_receipt"] == value.legacy_budget_receipt
    assert value.transport.seen == [("/reno/api/sync/source/dispose",
        engine._encoded(intent["record"]["request"]).encode("utf-8"))]
    assert value.store.read() == staged
    paid, files, seen = sql_rows(value.profile), permanent_files(value), deepcopy(value.transport.seen)
    after = reconcile(value, intent)
    assert after["source_authority"]["origin"] == permanent.record_ref(value.origin)
    assert after["budget_pending"]["defer_receipt"] == value.legacy_budget_receipt
    assert after["source_recovery_pending"] is None
    for field in ("source_execution", "lease", "recovery", "local_epoch", "claims"):
        assert after[field] == staged[field]
    assert engine._encoded(intent["record"]) == original
    assert paid == sql_rows(value.profile) and files == permanent_files(value) and seen == value.transport.seen
    before = replay_files(value, intent)
    forbid_new_http(value, monkeypatch)
    assert deliver(value, intent) == saved
    assert reconcile(value, intent) == after
    assert replay_files(value, intent) == before and value.transport.seen == seen


@pytest.mark.parametrize("point", ["before_body", "after_body", "reserved_db_head",
    "committed_db_head", "committed_checkpoint_head"])
def test_public_saved_receipt_tail_completes_without_another_http_call(real_cycle, monkeypatch, point):
    value = real_cycle
    intent = original_intent(value)
    stage(value, intent)
    actual_write, events, hit = permanent.AuthorityGuard._write, [], []
    stopped = RuntimeError("synthetic public pending receipt interruption")

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
        result = actual_write(owner, directory, name, raw, **kwargs)
        if directory.path.name == "journal":
            event = json.loads(raw)
            if event["kind"] == "receipts" and event["key"] == intent["record"]["record_id"]:
                events.append(event)
        if point == "after_body" and selected and not hit:
            hit.append(name)
            raise stopped
        return result

    before, paid = deepcopy(value.store.read()), sql_rows(value.profile)
    with monkeypatch.context() as scoped:
        scoped.setattr(permanent.AuthorityGuard, "_write", interrupted)
        with pytest.raises(RuntimeError) as raised:
            deliver(value, intent)
        assert raised.value is stopped and len(hit) == 1
    answer, seen = cloud_result(value, intent), deepcopy(value.transport.seen)
    assert len(seen) == 1
    forbid_new_http(value, monkeypatch)
    saved = deliver(value, intent)
    assert saved["record"]["result"] == answer
    assert value.store.read() == before and sql_rows(value.profile) == paid and value.transport.seen == seen
    files = permanent_files(value)
    assert deliver(value, intent) == saved
    assert permanent_files(value) == files and value.store.read() == before and value.transport.seen == seen


@pytest.mark.parametrize("real_cycle", ["legacy_dispose"], indirect=True)
@pytest.mark.parametrize("resource", ["state", "reference", "shared"])
def test_public_source16_http_rejects_resources_retained_by_caller(real_cycle, monkeypatch, resource):
    value = real_cycle
    intent = original_intent(value)
    stage(value, intent)
    before = state_file(value), permanent_files(value), sql_rows(value.profile), deepcopy(value.transport.seen)
    def forbidden_http(request):
        pytest.fail("Public source16 delivery entered HTTP while caller retained " + resource)

    monkeypatch.setattr(value.transport, "handle_request", forbidden_http)
    held = {"state": value.store.exclusive, "reference": value.store.reference_operation,
        "shared": lambda: lifecycle.shared(value.profile)}[resource]
    with held(), pytest.raises(SyncError):
        effects.deliver_recovery17(client=value.client, **public_selectors(value, intent))
    assert (state_file(value), permanent_files(value), sql_rows(value.profile), value.transport.seen) == before
