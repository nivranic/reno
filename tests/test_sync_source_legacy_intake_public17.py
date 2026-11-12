"""Original wire1 admissions through the actual public Home recovery path.

The prospective17 fixture holds actual origin, restore, SQL and State owners;
only its external HTTP peer is controlled. These cases require the production
exact-type dispatch to be integrated. Static parsing is not passing evidence.
They do not prove a real Cloud SQL producer or the global16-to17 migration.
"""
from copy import deepcopy
import uuid

import pytest

from reno import budget_schema, budget_writer_witness as sql
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects
from reno import sync_source_readmit_contract as wire
from reno import sync_source_transport17 as transport
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_lifecycle17 import waiting_control
from tests.test_sync_source_restore_cycle17 import restored_origin17
from tests.test_sync_source_legacy_intake17 import original_intent, pending
from tests import test_sync_source_readmit_contract as sample


def _paid_rows(value):
    with sql.connection(value.profile) as con:
        return {table: sorted((tuple(row) for row in con.execute('SELECT * FROM "' + table + '"')), key=repr)
                for table in budget_schema.TABLES}


def _install_original_pending(value):
    core, intent = original_intent(value)
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=intent["record"]["record_id"])
    return core, intent


def _assert_adopted(value, before, core, saved):
    result, record = saved["record"]["result"], saved["record"]
    after = value.store.read()
    assert after["source_execution"] == before["source_execution"] == core
    assert after["source_recovery_pending"] is None
    assert after["source_admission"] == {
        "format_version": 1, "admission_id": result["admission_id"],
        "core_sha256": result["core_sha256"], "request_sha256": result["request_sha256"],
        "admission_sha256": result["admission_sha256"], "record_sha256": permanent.digest(record)}
    old = [entry for entry in before["claims"] if entry["item_id"] == core["item_id"]]
    assert len(old) == 1
    expected = deepcopy(before)
    expected.update(source_recovery_pending=None, source_admission=deepcopy(after["source_admission"]))
    replacement = {**deepcopy(old[0]), "attempts": result["attempts"],
        "claim": deepcopy(result["claim"]), "lease_until": result["session"]["lease_until"]}
    expected["claims"] = [replacement if entry["item_id"] == core["item_id"] else deepcopy(entry)
                          for entry in before["claims"]]
    assert after == expected
    return after


def test_public_first_http_saves_original_record1_before_original_pending_cas(restored_origin17):
    value = restored_origin17
    core, intent = _install_original_pending(value)
    body = intent["record"]["request"]
    value.remote.payloads["readmit"] = sample.admission(body)
    before_state, before_paid = deepcopy(value.store.read()), _paid_rows(value)
    before_seen = deepcopy(value.remote.seen)
    saved = effects.deliver_recovery17(profile=value.profile, store=value.store,
        client=value.client, request_id=body["request_id"])
    assert value.store.read() == before_state
    assert _paid_rows(value) == before_paid
    assert saved["record"]["format_version"] == saved["record"]["result"]["format_version"] == 1
    assert saved["record"]["record_id"] == body["request_id"]
    assert saved["record"]["request_sha256"] == intent["record"]["request_sha256"]
    assert len(value.remote.seen) == len(before_seen) + 1
    before_cas_files = permanent_files(value)
    after = effects.reconcile_recovery17_locked(profile=value.profile, store=value.store,
        request_id=body["request_id"])
    assert after == _assert_adopted(value, before_state, core, saved)
    assert permanent_files(value) == before_cas_files
    assert _paid_rows(value) == before_paid
    assert len(value.remote.seen) == len(before_seen) + 1


def test_public_saved_original_record1_delivers_and_adopts_without_rpc(restored_origin17, monkeypatch):
    value = restored_origin17
    core, intent, saved, _ = pending(value)
    before_state, before_files = deepcopy(value.store.read()), permanent_files(value)
    before_paid, before_seen = _paid_rows(value), deepcopy(value.remote.seen)

    def forbidden(*args, **kwargs):
        pytest.fail("Public recovery required HTTP or a ticket for its original committed receipt")

    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    delivered = effects.deliver_recovery17(profile=value.profile, store=value.store,
        client=value.client, request_id=intent["record"]["record_id"])
    assert delivered == saved
    assert value.store.read() == before_state and permanent_files(value) == before_files
    after = effects.reconcile_recovery17_locked(profile=value.profile, store=value.store,
        request_id=intent["record"]["record_id"])
    assert after == _assert_adopted(value, before_state, core, saved)
    assert _paid_rows(value) == before_paid and value.remote.seen == before_seen
    assert permanent_files(value) == before_files


def test_public_replaced_http_method_has_no_original_record1_authority(restored_origin17, monkeypatch):
    value = restored_origin17
    _, intent = _install_original_pending(value)
    body = intent["record"]["request"]
    before_state, before_files = deepcopy(value.store.read()), permanent_files(value)
    before_paid, before_seen = _paid_rows(value), deepcopy(value.remote.seen)
    monkeypatch.setattr(value.client, "source_readmit", lambda request: sample.admission(request))
    with pytest.raises(SyncError):
        effects.deliver_recovery17(profile=value.profile, store=value.store,
            client=value.client, request_id=body["request_id"])
    assert value.store.read() == before_state and permanent_files(value) == before_files
    assert _paid_rows(value) == before_paid and value.remote.seen == before_seen


def test_public_unselected_original_uuid_cannot_install_saved_admission(restored_origin17, monkeypatch):
    value = restored_origin17
    pending(value)
    before_state, before_files = deepcopy(value.store.read()), permanent_files(value)
    before_paid = _paid_rows(value)

    def forbidden(*args, **kwargs):
        pytest.fail("An unselected original UUID sent RPC")

    monkeypatch.setattr(value.client, "_request", forbidden)
    other = str(uuid.uuid4())
    with pytest.raises(SyncError):
        effects.deliver_recovery17(profile=value.profile, store=value.store,
            client=value.client, request_id=other)
    with pytest.raises(SyncError):
        effects.reconcile_recovery17_locked(profile=value.profile, store=value.store, request_id=other)
    assert value.store.read() == before_state and permanent_files(value) == before_files
    assert _paid_rows(value) == before_paid


def test_public_native_registration_outer1_still_saves_receipt2(live17):
    """Legacy readmit routing must not capture ordinary source_register1."""
    value = live17
    _, _, reference, _ = waiting_control(value)
    effects.install_control17_locked(profile=value.profile, store=value.store,
        request_id=reference["record_id"])
    intent = effects.create_registration17_locked(profile=value.profile, store=value.store,
        client=value.client, control_request_id=reference["record_id"])
    body = intent["record"]["request"]
    assert intent["record"]["format_version"] == body.get("format_version", 1) == 1
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=body["request_id"])
    before_state, before_paid = deepcopy(value.store.read()), _paid_rows(value)
    saved = effects.deliver_recovery17(profile=value.profile, store=value.store,
        client=value.client, request_id=body["request_id"])
    result = value.remote.payloads["register"]
    assert result["registration_sha256"] == wire.registration_sha256(result)
    assert result["core_sha256"] == body["core_sha256"] and result["item_id"] == body["item_id"]
    assert saved["record"]["format_version"] == 2
    assert saved["record"]["result"] == result and saved["record"]["record_id"] == body["request_id"]
    assert value.store.read() == before_state and _paid_rows(value) == before_paid
    after = effects.reconcile_recovery17_locked(profile=value.profile, store=value.store,
        request_id=body["request_id"])
    assert after["source_recovery_pending"] is None
    assert after["source_execution"] == before_state["source_execution"]
    assert after["source_admission"] == before_state["source_admission"]
    assert after["claims"] == before_state["claims"]
    assert _paid_rows(value) == before_paid
