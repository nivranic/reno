"""Actual historical bridges cannot authorize a reserve or a paid dispatch."""
from copy import deepcopy
from contextvars import copy_context

import pytest

from reno import home_recovery_runtime17 as runtime, migrations
from reno import sync_source_authority as permanent, sync_source_readmit_local2 as local
from reno import sync_source_lifecycle17 as effects
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_readmit_local2 import produce_held_receipt
from tests.test_sync_source_restore_cycle17 import restored_origin17


def test_actual_control_basis_uses_admission_result_claim_and_has_no_generic_paid_api(live17):
    core, intent, receipt, result = produce_held_receipt(live17)
    effects.install_recovery_pending17_locked(profile=live17.profile, store=live17.store,
        request_id=intent["record"]["record_id"])
    effects.reconcile_recovery17_locked(profile=live17.profile, store=live17.store,
        request_id=intent["record"]["record_id"])
    before, files, seen = deepcopy(live17.store.read()), permanent_files(live17), deepcopy(live17.remote.seen)
    with live17.store.reference_operation(), local.capture_historical_control_basis_locked(profile=live17.profile,
            store=live17.store, admission_id=intent["record"]["record_id"],
            expected_record_sha256=receipt["ref"]["record_sha256"]) as captured:
        assert type(captured) is local.HistoricalControlBasisCapture2
        assert not isinstance(captured, local.AdmissionReceiptCapture2)
        assert not hasattr(captured, "verify_for_commit")
        assert not hasattr(captured, "preservation_capture")
        assert captured.core == core and captured.creation_basis["claim"] == result["claim"]
        assert captured.creation_basis["claim"] != intent["record"]["creation_basis"]["claim"]
        assert captured.creation_basis["session"] == result["session"]
        assert captured.admission_ref() == before["source_admission"]
        with pytest.raises(SyncError):
            copy_context().run(captured.verify_source)
        captured.verify_source()
    assert live17.store.read() == before and permanent_files(live17) == files and live17.remote.seen == seen
    assert migrations.SCHEMA_VERSION == 16
    with pytest.raises(SyncError):
        captured.verify_source()


@pytest.mark.parametrize("restored_origin17", ["admitted_double"], indirect=True)
def test_history_prefix_ends_before_the_latest_actual_restore_capture(restored_origin17):
    value = restored_origin17
    before = deepcopy(value.store.read())
    with value.store.reference_operation(), local.capture_historical_control_basis_locked(profile=value.profile,
            store=value.store, admission_id=value.admission_intent["record"]["record_id"],
            expected_record_sha256=value.admission_receipt["ref"]["record_sha256"]) as historical:
        assert not runtime._CURRENT.get()
        assert historical.creation_basis["home_epoch"] == before["local_epoch"] - 1
        with runtime.capture_completed_locked(profile=value.profile, store=value.store,
                operation_id=value.current_restore_operation) as current:
            assert type(current) is runtime.CompletedRestoreCapture17
            observation = current.preservation_observation(from_epoch=value.core["local_epoch"])
            assert observation["preservation"]["to_epoch"] == before["local_epoch"]
            historical.verify_source()
    assert value.store.read() == before


def test_control_bridge_refuses_a_wrong_original_sha_without_recapturing_a_new_admission(live17):
    _, intent, _, _ = produce_held_receipt(live17)
    before, files, seen = deepcopy(live17.store.read()), permanent_files(live17), deepcopy(live17.remote.seen)
    with live17.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_historical_control_basis_locked(profile=live17.profile, store=live17.store,
                admission_id=intent["record"]["record_id"], expected_record_sha256="f" * 64):
            pytest.fail("A wrong original historical hash acquired a control bridge")
    assert live17.store.read() == before and permanent_files(live17) == files and live17.remote.seen == seen
