"""Actual Home origin/intent/HTTP/permanent receipt; no DTO capture fixture."""
from copy import deepcopy
import uuid

import pytest

from reno import budget_writer_witness as sql, sync_source_authority as permanent
from reno import sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create
from tests import test_sync_source_readmit_contract as wire_sample


def produce_held_receipt(value):
    """Shared genuine Home producer fixture, with one isolated HTTP peer."""
    core = create(value)
    with value.store.reference_operation():
        saved = local.create_readmit_intent_locked(profile=value.profile, store=value.store,
            client=value.client, request_id=str(uuid.uuid4()))
    request = saved["record"]["request"]
    # Real Cloud readmit2 creates this claim/receipt, independently of Home.
    answer = value.client.source_readmit(request)
    with value.store.reference_operation():
        receipt = local.publish_http_receipt_locked(profile=value.profile, store=value.store,
            client=value.client, request_id=request["request_id"], request_sha256=saved["record"]["request_sha256"])
    return core, saved, receipt, answer


def test_same_epoch_actual_creator_receipt_and_sealed_reader(live17):
    core, intent, receipt, answer = produce_held_receipt(live17)
    assert_current17_runtime()
    request = intent["record"]["request"]
    assert request["preservation"] is request["resume_basis"] is None
    assert request["previous_admission_id"] is None
    assert receipt["record"]["result"] == answer
    before = deepcopy(live17.store.read())
    with live17.store.reference_operation():
        with local.capture_admission_locked(profile=live17.profile, store=live17.store,
                admission_id=request["request_id"], expected_record_sha256=receipt["ref"]["record_sha256"]) as captured:
            assert type(captured) is local.AdmissionReceiptCapture2
            assert captured.core == core and captured.result == answer
            assert captured.preservation_capture is None
            assert captured.admission_ref()["record_sha256"] == receipt["ref"]["record_sha256"]
            with sql.connection(live17.profile, write=True) as con:
                token = sql.require_writer(con, profile=live17.profile)
                captured.verify_for_commit(con, writer_permit=token)
    assert live17.store.read() == before
    with pytest.raises(SyncError):
        captured.verify_source()


def test_permanent_receipt_restores_zero_rpc_under_original_active_owner(live17, monkeypatch):
    _, intent, receipt, _ = produce_held_receipt(live17)
    def no_rpc(*args, **kwargs):
        pytest.fail("saved receipt recovery dispatched an RPC")
    monkeypatch.setattr(live17.client, "_request", no_rpc)
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        with local.capture_admission_locked(profile=live17.profile, store=live17.store,
                admission_id=intent["record"]["record_id"]) as captured:
            assert captured._owner is owner
            assert captured.result == receipt["record"]["result"]


def test_replaced_public_readmit_dto_cannot_publish_a_permanent_receipt(live17, monkeypatch):
    create(live17)
    with live17.store.reference_operation():
        intent = local.create_readmit_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, request_id=str(uuid.uuid4()))
    monkeypatch.setattr(live17.client, "source_readmit", lambda request: wire_sample.admission(request))
    live17.client.source_readmit(intent["record"]["request"])
    with live17.store.reference_operation(), pytest.raises(SyncError):
        local.publish_http_receipt_locked(profile=live17.profile, store=live17.store,
            client=live17.client, request_id=intent["record"]["record_id"], request_sha256=intent["record"]["request_sha256"])
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert intent["record"]["record_id"] not in owner.observe()["entries"]["receipts"]
