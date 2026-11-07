"""A real partial permanent write resumes only its original control intent."""
from copy import deepcopy
import json

import pytest

from reno import budget, sync_state
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects
from reno import sync_source_admission17 as admissions, sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_lifecycle17 import waiting_control


@pytest.mark.parametrize("point", ["before_body", "after_body", "before_reserved_head"])
def test_original_reserved_control_recaptures_actual_partial_catalog_without_fresh_permission(live17, monkeypatch, point):
    value = live17
    real_write, stop, hit = permanent.AuthorityGuard._write, RuntimeError("isolated original-control crash"), []
    reserved = []
    def interrupted(owner, directory, name, raw, *, immutable=False):
        selected = directory.path.name == "controls"
        head = bool(reserved) and directory.path == value.profile.database.parent and name == permanent.DB_HEAD_NAME
        if not hit and ((point == "before_body" and selected) or (point == "before_reserved_head" and head)):
            hit.append(name)
            raise stop
        answer = real_write(owner, directory, name, raw, immutable=immutable)
        if directory.path.name == "journal":
            event = json.loads(raw)
            if event["kind"] == "controls" and event["state"] == "reserved":
                reserved.append(event["key"])
        if not hit and point == "after_body" and selected:
            hit.append(name)
            raise stop
        return answer
    with monkeypatch.context() as scoped:
        scoped.setattr(permanent.AuthorityGuard, "_write", interrupted)
        with pytest.raises(RuntimeError) as raised:
            waiting_control(value)
        assert raised.value is stop and len(hit) == 1
    # Every previous lexical producer ended. A new StateStore/owner must read
    # the actual journal, optional body and two real heads without a cache DTO.
    store = sync_state.StateStore(value.store.data_dir, profile=value.profile)
    with store.reference_operation(), permanent.owned(store) as owner:
        frame = owner.observe_readonly()
        selected = [item["record"] for item in frame["entries"]["controls"].values() if item["state"] == "reserved"]
        assert len(selected) == 1
        original = selected[0]
    paid_id, before_state = original["request"]["budget_operation_id"], deepcopy(store.read())
    before_files, before_seen = permanent_files(value), deepcopy(value.remote.seen)
    def forbidden(*args, **kwargs):
        pytest.fail("Original completion bought a fresh permission or sent RPC")
    monkeypatch.setattr(admissions, "capture_current_admission_locked", forbidden)
    monkeypatch.setattr(value.client, "_request", forbidden)
    with store.reference_operation(), local.capture_control_intent_locked(profile=value.profile,
            store=store, request_id=original["record_id"], expected_record_sha256=permanent.digest(original)) as captured:
        assert type(captured) is local.ControlIntentCapture2
        assert not hasattr(captured, "verify_for_commit")
        assert captured.record == original and captured.recovery_capture.record_state == "reserved"
        captured.verify_source()
    assert store.read() == before_state and permanent_files(value) == before_files
    reference = effects.complete_control_creation17_locked(profile=value.profile, store=store,
        request_id=original["record_id"], expected_record_sha256=permanent.digest(original))
    assert reference == permanent.record_ref(original)
    with store.reference_operation(), permanent.owned(store) as owner:
        observed = owner.observe()
        assert observed["entries"]["controls"][original["record_id"]] == {"state": "committed", "record": original}
        assert len(observed["entries"]["controls"]) == 1
    assert store.read() == before_state and value.remote.seen == before_seen
    with budget._connection(value.profile) as con:
        assert con.execute("SELECT COUNT(*) FROM budget_operation WHERE operation_id=?", (paid_id,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM budget_attempt WHERE operation_id=?", (paid_id,)).fetchone()[0] == 0
    assert_current17_runtime()
    with pytest.raises(SyncError):
        captured.verify_source()


def test_original_control_selector_rejects_changed_sha_without_any_repair(live17):
    _, _, reference, record = waiting_control(live17)
    before_state, before_files = deepcopy(live17.store.read()), permanent_files(live17)
    with live17.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_control_intent_locked(profile=live17.profile, store=live17.store,
                request_id=reference["record_id"], expected_record_sha256="f" * 64):
            pytest.fail("A wrong original SHA selected or repaired a control")
    assert live17.store.read() == before_state and permanent_files(live17) == before_files
