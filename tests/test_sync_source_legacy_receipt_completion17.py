"""Actual original receipt journal gaps, without an HTTP/broker replacement.

These prospective17 cases retain genuine Home origin/restore/State/SQL owners.
Only the external peer is controlled. Authority's exact completion dispatch
must be wired before their completion assertions can count as passing.
"""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects
from reno import sync_source_legacy_intake17 as intake
from reno import sync_source_legacy_receipt_completion17 as completion
from reno import sync_source_transport17 as transport, sync_state
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_restore_cycle17 import restored_origin17, actual_call
from tests.test_sync_source_legacy_intake17 import original_intent
from tests.test_sync_source_legacy_intake_public17 import _paid_rows
from tests import test_sync_source_readmit_contract as sample


def partial(value, monkeypatch, point):
    core, intent = original_intent(value)
    body = intent["record"]["request"]
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=body["request_id"])
    value.remote.payloads["readmit"] = sample.admission(body)
    answer = value.client.source_readmit(body)
    before_state, before_paid = deepcopy(value.store.read()), _paid_rows(value)
    original_files = permanent_files(value)
    real_write, events, hit = permanent.AuthorityGuard._write, [], []
    stopped = RuntimeError("isolated original-receipt publication interruption")

    def interrupted(owner, directory, name, raw, *, immutable=False):
        selected = directory.path.name == "receipts" and name == body["request_id"] + ".json"
        state = None if not events else events[-1]["state"]
        db_head = directory.path == value.profile.database.parent and name == permanent.DB_HEAD_NAME
        checkpoint_head = directory.path == value.profile.checkpoints and name == permanent.CHECKPOINT_HEAD_NAME
        before_head = ((point == "reserved_db_head" and state == "reserved" and db_head)
            or (point == "committed_db_head" and state == "committed" and db_head)
            or (point == "committed_checkpoint_head" and state == "committed" and checkpoint_head))
        if not hit and (point == "before_body" and selected or before_head):
            hit.append(name)
            raise stopped
        result = real_write(owner, directory, name, raw, immutable=immutable)
        if directory.path.name == "journal":
            event = json.loads(raw)
            if event["kind"] == "receipts" and event["key"] == body["request_id"]:
                events.append(event)
        if not hit and point == "after_body" and selected:
            hit.append(name)
            raise stopped
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(permanent.AuthorityGuard, "_write", interrupted)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            actual_call(intake.publish_legacy_http_receipt17_locked, profile=value.profile, store=value.store,
                client=value.client, request_id=body["request_id"], request_sha256=intent["record"]["request_sha256"])
        assert raised.value is stopped and len(hit) == 1
    # Reopen the actual local State owner after the original publisher ended.
    value.store = sync_state.StateStore(value.store.data_dir, profile=value.profile)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        frame = owner.observe_readonly()
        original = deepcopy(frame["entries"]["receipts"][body["request_id"]]["record"])
        assert original["record_id"] == body["request_id"] and original["result"] == answer
    assert value.store.read() == before_state and _paid_rows(value) == before_paid
    return core, intent, original, before_state, before_paid, original_files


def forbidden(*args, **kwargs):
    pytest.fail("Original receipt completion requested HTTP, a ticket or another result")


@pytest.mark.parametrize("point", ["before_body", "after_body", "reserved_db_head",
    "committed_db_head", "committed_checkpoint_head"])
def test_original_tail_finishes_at_zero_rpc_without_state_or_paid_effect(restored_origin17, monkeypatch, point):
    value = restored_origin17
    core, intent, original, before_state, before_paid, prefix = partial(value, monkeypatch, point)
    before_files, before_seen = permanent_files(value), deepcopy(value.remote.seen)
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    with value.store.reference_operation(), completion.capture_original_legacy_receipt17_locked(
            profile=value.profile, store=value.store, request_id=original["record_id"],
            request_sha256=intent["record"]["request_sha256"],
            expected_record_sha256=permanent.digest(original)) as captured:
        assert type(captured) is completion.OriginalReceiptRecoveryCapture17
        assert captured.core == core and captured.record == original
        assert not any(hasattr(captured, name) for name in ("admit_paid", "operation", "writer", "baseline_state_effect"))
        captured.verify_source()
    assert permanent_files(value) == before_files and value.store.read() == before_state
    with value.store.reference_operation():
        saved = completion.complete_original_legacy_receipt17_locked(profile=value.profile, store=value.store,
            request_id=original["record_id"], request_sha256=intent["record"]["request_sha256"],
            expected_record_sha256=permanent.digest(original))
    assert saved == {"record": original, "ref": permanent.record_ref(original)}
    assert value.store.read() == before_state and _paid_rows(value) == before_paid
    assert value.remote.seen == before_seen
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        frame = owner.observe_readonly()
        assert frame["head_status"] == "complete" and frame["incomplete"] == []
        assert frame["entries"]["receipts"][original["record_id"]] == {"state": "committed", "record": original}
    final = permanent_files(value)
    for path, facts in prefix.items():
        if Path(path).name not in {permanent.DB_HEAD_NAME, permanent.CHECKPOINT_HEAD_NAME}:
            assert final[path] == facts
    with value.store.reference_operation():
        assert completion.complete_original_legacy_receipt17_locked(profile=value.profile, store=value.store,
            request_id=original["record_id"], request_sha256=intent["record"]["request_sha256"],
            expected_record_sha256=permanent.digest(original)) == saved
    assert permanent_files(value) == final and value.store.read() == before_state
    with pytest.raises(SyncError):
        captured.verify_source()


@pytest.mark.parametrize("selector", ["uuid", "request_sha256", "record_sha256"])
def test_wrong_original_selector_never_repairs_a_partial_head(restored_origin17, monkeypatch, selector):
    value = restored_origin17
    _, intent, original, before_state, before_paid, _ = partial(value, monkeypatch, "reserved_db_head")
    before_files = permanent_files(value)
    args = {"request_id": original["record_id"], "request_sha256": intent["record"]["request_sha256"],
        "expected_record_sha256": permanent.digest(original)}
    args[{"uuid": "request_id", "request_sha256": "request_sha256", "record_sha256": "expected_record_sha256"}[selector]] = (
        str(uuid.uuid4()) if selector == "uuid" else "f" * 64)
    with value.store.reference_operation(), pytest.raises(SyncError):
        completion.complete_original_legacy_receipt17_locked(profile=value.profile, store=value.store, **args)
    assert permanent_files(value) == before_files and value.store.read() == before_state
    assert _paid_rows(value) == before_paid


def test_intent_without_original_result_cannot_manufacture_completion(restored_origin17, monkeypatch):
    value = restored_origin17
    _, intent = original_intent(value)
    before_files, before_state, before_paid = permanent_files(value), deepcopy(value.store.read()), _paid_rows(value)
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    with value.store.reference_operation(), pytest.raises(SyncError) as raised:
        completion.complete_original_legacy_receipt17_locked(profile=value.profile, store=value.store,
            request_id=intent["record"]["record_id"], request_sha256=intent["record"]["request_sha256"])
    # The actual authority owner deliberately wraps inner domain failures in
    # its stable public code. Prove the precise absence check in the cause
    # chain rather than accepting an unrelated earlier validation failure.
    assert raised.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    cause, codes = raised.value, []
    while cause is not None:
        if isinstance(cause, SyncError):
            codes.append(cause.code)
        cause = cause.__context__
    assert "SYNC_SOURCE_LEGACY_RECEIPT17_ORIGINAL_RESULT_REQUIRED" in codes
    assert permanent_files(value) == before_files and value.store.read() == before_state
    assert _paid_rows(value) == before_paid


@pytest.mark.parametrize("capability", [completion.OriginalReceiptRecoveryCapture17, completion.ReceiptCompletionGuard17])
def test_public_constructors_cannot_supply_an_owner_result_or_foreign_seal(capability):
    with pytest.raises(SyncError) as raised:
        capability(owner=object(), result={}, seal=object())
    assert raised.value.code == "SYNC_SOURCE_LEGACY_RECEIPT17_FACTORY_REQUIRED"
