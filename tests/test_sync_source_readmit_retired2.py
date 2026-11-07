"""Actual typed retirement closes an old-control gap; absence is not proof."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from reno import budget, budget_writer_witness as sql
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects
from reno import sync_source_readmit_contract as wire, sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_lifecycle17 import waiting_control


def retire_actual_defer(value):
    core, operation, control_ref, control = waiting_control(value)
    effects.install_control17_locked(profile=value.profile, store=value.store, request_id=control["record_id"])
    registration = effects.create_registration17_locked(profile=value.profile, store=value.store,
        client=value.client, control_request_id=control["record_id"])
    registered = value.client.source_register(registration["record"]["request"])
    assert registered["core_sha256"] == permanent.digest(core) and registered["item_id"] == core["item_id"]
    with value.store.reference_operation():
        local.publish_http_receipt_locked(profile=value.profile, store=value.store, client=value.client,
            request_id=registration["record"]["record_id"], request_sha256=registration["record"]["request_sha256"])
    intent = effects.create_current_dispose17_locked(profile=value.profile, store=value.store,
        client=value.client, control_request_id=control["record_id"])
    body = intent["record"]["request"]
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store, request_id=body["request_id"])
    delivered = effects.deliver_recovery17(profile=value.profile, store=value.store, client=value.client, request_id=body["request_id"])
    result = delivered["record"]["result"]
    assert result["format_version"] == 2 and result["disposition"] == "not_applied"
    after = effects.reconcile_recovery17_locked(profile=value.profile, store=value.store, request_id=body["request_id"])
    assert after["source_authority"]["defer"] is None and after["budget_pending"] is None
    return core, operation, control_ref, control, intent, result


def test_actual_retired_control_capture_is_lexical_and_readmit_keeps_its_negative(live17):
    core, operation, control_ref, control, _, negative = retire_actual_defer(live17)
    paid_before, before_state = budget.get_operation(operation, profile=live17.profile), deepcopy(live17.store.read())
    seen = deepcopy(live17.remote.seen)
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        with local.capture_retired_controls_locked(profile=live17.profile, store=live17.store,
                control_request_ids=(control["record_id"],)) as captured:
            assert type(captured) is local.RetiredControlsCapture2 and captured._owner is owner
            assert type(captured) is not local.AdmissionReceiptCapture2
            assert captured.core == core and captured.dispositions == (negative,)
            detached = captured.dispositions
            detached[0]["disposition"] = "applied"
            assert captured.dispositions == (negative,)
            with sql.connection(live17.profile, write=True) as con:
                captured.verify_for_commit(con, writer_permit=sql.require_writer(con, profile=live17.profile))
    assert live17.remote.seen == seen and live17.store.read() == before_state
    with pytest.raises(SyncError):
        captured.verify_source()
    with live17.store.reference_operation():
        intent = local.create_readmit_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, request_id=str(uuid.uuid4()))
    assert intent["record"]["request"]["dispositions"] == [negative]
    assert intent["record"]["request"]["defer_receipt"] is None
    assert budget.get_operation(operation, profile=live17.profile) == paid_before
    assert live17.store.read() == before_state
    assert_current17_runtime()
    with live17.store.reference_operation(), permanent.owned(live17.store) as owner:
        assert owner.read(control_ref) == control


def test_frozen_control_without_active_pointer_and_without_real_negative_stays_unknown(live17):
    _, _, _, control = waiting_control(live17)
    assert live17.store.read()["source_authority"]["defer"] is None
    before, files, seen = deepcopy(live17.store.read()), permanent_files(live17), deepcopy(live17.remote.seen)
    with live17.store.reference_operation(), pytest.raises(SyncError):
        local.create_readmit_intent_locked(profile=live17.profile, store=live17.store,
            client=live17.client, request_id=str(uuid.uuid4()))
    with live17.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_retired_controls_locked(profile=live17.profile, store=live17.store,
                control_request_ids=(control["record_id"],)):
            pytest.fail("An absent receipt became a retirement proof")
    assert live17.store.read() == before and permanent_files(live17) == files and live17.remote.seen == seen


def test_changed_permanent_negative_cannot_cover_a_cleared_control(live17):
    _, _, _, control, intent, _ = retire_actual_defer(live17)
    files = [Path(name) for name in permanent_files(live17)
        if Path(name).parent.name == "receipts" and Path(name).name == intent["record"]["record_id"] + ".json"]
    assert len(files) == 1
    path, original = files[0], files[0].read_bytes()
    changed = json.loads(original)
    changed["result"]["original_request_sha256"] = "f" * 64
    changed["result_sha256"] = permanent.digest(changed["result"])
    before, seen = deepcopy(live17.store.read()), deepcopy(live17.remote.seen)
    path.write_bytes(wire.canonical_json(changed, maximum=wire.MAX_CONTROL_BYTES).encode("utf-8"))
    try:
        with live17.store.reference_operation(), pytest.raises(SyncError):
            local.create_readmit_intent_locked(profile=live17.profile, store=live17.store,
                client=live17.client, request_id=str(uuid.uuid4()))
        with live17.store.reference_operation(), pytest.raises(SyncError):
            with local.capture_retired_controls_locked(profile=live17.profile, store=live17.store,
                    control_request_ids=(control["record_id"],)):
                pytest.fail("A changed result/body SHA covered a retired control")
        assert live17.store.read() == before and live17.remote.seen == seen
    finally:
        path.write_bytes(original)
