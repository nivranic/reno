"""Actual Home dispose2 creator, original HTTP and permanent fact getter."""
from copy import deepcopy
import uuid

import pytest

from reno import budget_writer_witness as sql
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as controls
from reno import sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create
from tests.test_sync_source_timer2 import produce_defer
from tests.test_sync_source_lifecycle17 import waiting_control


def save_dispose(value, original, *, applied, positive=None):
    with value.store.reference_operation():
        intent = local.create_dispose_intent_locked(profile=value.profile, store=value.store,
            client=value.client, control_request_id=original["record_id"], request_id=str(uuid.uuid4()))
    request = intent["record"]["request"]
    answer = value.client.source_dispose(request)
    assert answer["format_version"] == 2
    assert answer["disposition"] == ("applied" if applied else "not_applied")
    assert answer["original_receipt"] == positive
    with value.store.reference_operation():
        receipt = local.publish_http_receipt_locked(profile=value.profile, store=value.store,
            client=value.client, request_id=request["request_id"], request_sha256=intent["record"]["request_sha256"])
    return intent, receipt


def test_actual_same_epoch_dispose2_and_original_selector_zero_rpc_getter(live17, monkeypatch):
    original, old_receipt = produce_defer(live17)
    # A fresh normal17 control was genuinely registered before its first push.
    # Compare the independently observed HTTP result with its committed record.
    registration = deepcopy(live17.remote.payloads["register"])
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        registered = owner.observe()["entries"]["receipts"][registration["request_id"]]
        assert registered["state"] == "committed"
        assert registered["record"]["result"] == registration
        assert registered["record"]["core_sha256"] == original["core_sha256"]
    state_before = deepcopy(live17.store.read())
    intent, receipt = save_dispose(live17, original, applied=True, positive=old_receipt["record"]["result"])
    request = intent["record"]["request"]
    assert request["preservation"] is None
    assert request["registration"] == registration
    assert registration["original_request_id"] == original["record_id"]
    assert registration["original_request_sha256"] == permanent.digest(original["request"])
    assert registration["core_sha256"] == original["core_sha256"]
    assert registration["continuity_id"] == request["continuity"]["continuity_id"]
    assert request["original_request"] == original["request"]
    assert request["continuity"] == intent["record"]["creation_basis"]["continuity"]
    assert live17.store.read() == state_before
    assert_current17_runtime()
    with live17.store.reference_operation(), pytest.raises(SyncError) as caught:
        local.create_dispose_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, control_request_id=original["record_id"], request_id=str(uuid.uuid4()))
    assert caught.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    # The original owner keeps its public sanitized error envelope.
    assert caught.value.__context__.code == "SYNC_SOURCE_LOCAL2_DISPOSITION_RECONCILIATION_REQUIRED"
    def no_rpc(*args, **kwargs):
        pytest.fail("permanent disposition recovery dispatched an RPC")
    monkeypatch.setattr(live17.client, "_request", no_rpc)
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        with local.capture_disposition_locked(profile=live17.profile, store=live17.store,
                control_request_id=original["record_id"], expected_record_sha256=receipt["ref"]["record_sha256"]) as captured:
            assert captured._owner is owner
            assert captured.request == request
            assert captured.result == receipt["record"]["result"]
            assert captured.original_control_request == original["request"]
            assert captured.original_control_receipt == old_receipt["record"]["result"]
            assert captured.disposition == "applied"
            with sql.connection(live17.profile, write=True) as con:
                captured.verify_for_commit(con, writer_permit=sql.require_writer(con, profile=live17.profile))
    assert live17.store.read() == state_before
    with pytest.raises(SyncError):
        captured.verify_source()


def test_actual_registration_supports_negative_dispose2_without_guessing_a_positive(live17):
    core, _, ref, original = waiting_control(live17)
    controls.install_control17_locked(profile=live17.profile, store=live17.store, request_id=ref["record_id"])
    registration_intent = controls.create_registration17_locked(profile=live17.profile, store=live17.store,
        client=live17.client, control_request_id=ref["record_id"])
    registration_body = registration_intent["record"]["request"]
    answer = live17.client.source_register(registration_body)
    assert answer["core_sha256"] == permanent.digest(core) and answer["item_id"] == core["item_id"]
    with live17.store.reference_operation():
        local.publish_http_receipt_locked(profile=live17.profile, store=live17.store, client=live17.client,
            request_id=registration_body["request_id"], request_sha256=registration_intent["record"]["request_sha256"])
    before = deepcopy(live17.store.read())
    intent, receipt = save_dispose(live17, original, applied=False)
    assert intent["record"]["request"]["registration"] == answer
    with live17.store.reference_operation(), local.capture_disposition_locked(profile=live17.profile,
            store=live17.store, control_request_id=original["record_id"]) as captured:
        assert captured.disposition == "not_applied"
        assert captured.original_control_receipt is None
        assert captured.result == receipt["record"]["result"]
    assert live17.store.read() == before
    assert all(endpoint != "push" for endpoint, _ in live17.remote.seen)


def test_missing_original_control_selector_creates_no_intent_or_rpc(live17):
    create(live17)
    seen = deepcopy(live17.remote.seen)
    with live17.store.reference_operation(), pytest.raises(SyncError):
        local.create_dispose_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, control_request_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()))
    assert live17.remote.seen == seen
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert owner.observe()["entries"]["requests"] == {}


def test_a_public_disposition_dto_cannot_be_saved_as_actual_permanent_receipt(live17, monkeypatch):
    original, old_receipt = produce_defer(live17)
    with live17.store.reference_operation():
        intent = local.create_dispose_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, control_request_id=original["record_id"], request_id=str(uuid.uuid4()))
    monkeypatch.setattr(live17.client, "source_dispose", lambda request: deepcopy(old_receipt["record"]["result"]))
    live17.client.source_dispose(intent["record"]["request"])
    with live17.store.reference_operation(), pytest.raises(SyncError):
        local.publish_http_receipt_locked(profile=live17.profile, store=live17.store, client=live17.client,
            request_id=intent["record"]["record_id"], request_sha256=intent["record"]["request_sha256"])
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert intent["record"]["record_id"] not in owner.observe()["entries"]["receipts"]


def test_user_pause_keeps_historical_dispose_possible_without_new_paid_readmission(live17):
    from reno import tasks
    from reno.budget_contract import BudgetDeferred
    original, old_receipt = produce_defer(live17)
    paused = tasks.pause_sync(live17.job["job_id"], profile=live17.profile)
    assert paused["manual_paused"] is True
    _, receipt = save_dispose(live17, original, applied=True, positive=old_receipt["record"]["result"])
    assert receipt["record"]["result"]["disposition"] == "applied"
    with live17.store.reference_operation(), pytest.raises(BudgetDeferred) as caught:
        local.create_readmit_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, request_id=str(uuid.uuid4()))
    assert caught.value.reason == "manual_pause"
