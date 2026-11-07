"""Historical admission facts stay distinct from current paid admission."""
from copy import deepcopy

import pytest

from reno import sync_source_readmit_local2 as local, sync_source_authority as permanent
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_readmit_local2 import produce_held_receipt
from tests.test_sync_source_restore_cycle17 import restored_origin17


def test_same_epoch_history_reads_exact_actual_receipt_without_current_effect_api(live17, monkeypatch):
    core, intent, receipt, result = produce_held_receipt(live17)
    before, files = deepcopy(live17.store.read()), permanent_files(live17)
    def no_rpc(*args, **kwargs):
        pytest.fail("A historical admission fact dispatched a new RPC")
    monkeypatch.setattr(live17.client, "_request", no_rpc)
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        with local.capture_admission_history_locked(profile=live17.profile, store=live17.store,
                admission_id=intent["record"]["record_id"], expected_record_sha256=receipt["ref"]["record_sha256"]) as captured:
            assert type(captured) is local.HistoricalAdmissionReceiptCapture2 and captured._owner is owner
            assert not isinstance(captured, local.AdmissionReceiptCapture2)
            assert captured.request == intent["record"]["request"] and captured.result == result
            assert captured.core == core and captured.admission_ref()["record_sha256"] == receipt["ref"]["record_sha256"]
            assert captured.preservation_provenance is None
            assert not hasattr(captured, "verify_for_commit")
            assert not hasattr(captured, "_advance_creation")
            assert not hasattr(captured, "preservation_capture")
            detached = captured.result
            detached["session"]["lease_token"] = "changed"
            assert captured.result == result
            captured.verify_source()
    assert live17.store.read() == before and permanent_files(live17) == files
    with pytest.raises(SyncError):
        captured.verify_source()


def test_history_receipt_sha_selector_cannot_fill_missing_original_fact(live17):
    _, intent, _, _ = produce_held_receipt(live17)
    before, files = deepcopy(live17.store.read()), permanent_files(live17)
    with live17.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_admission_history_locked(profile=live17.profile, store=live17.store,
                admission_id=intent["record"]["record_id"], expected_record_sha256="f" * 64):
            pytest.fail("A wrong original receipt SHA acquired a historical capture")
    assert live17.store.read() == before and permanent_files(live17) == files


@pytest.mark.parametrize("restored_origin17", ["admitted_double"], indirect=True)
def test_admission_at_previous_epoch_reads_its_real_historical_prefix_after_next_restore(restored_origin17):
    value = restored_origin17
    before = deepcopy(value.store.read())
    with value.store.reference_operation(), local.capture_admission_history_locked(profile=value.profile,
            store=value.store, admission_id=value.admission_intent["record"]["record_id"],
            expected_record_sha256=value.admission_receipt["ref"]["record_sha256"]) as captured:
        assert type(captured) is local.HistoricalAdmissionReceiptCapture2
        assert captured.request == value.admission_intent["record"]["request"]
        assert captured.result == value.admission_receipt["record"]["result"]
        assert captured.core == value.core
        assert captured.result["current_home_epoch"] == value.core["local_epoch"] + 1 < before["local_epoch"]
        assert captured.preservation_provenance == value.original_restore_ref
        captured.verify_source()
    assert value.store.read() == before
    # The existing current capture must not reinterpret this old admission as
    # a preservation certificate for the later actual epoch.
    with value.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_admission_locked(profile=value.profile, store=value.store,
                admission_id=value.admission_intent["record"]["record_id"]):
            pytest.fail("A historical epoch admission became a current paid seal")
