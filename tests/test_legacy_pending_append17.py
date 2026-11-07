"""Actual prepare/CAS/finish boundaries around the real caller callback.

Only negative cases deliberately corrupt the original disposable State file.
Capsules, request UUIDs, receipts and the positive complete after-image all
come from the existing old16 worker/Cloud SQLite/ASGI producer chain.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json

import pytest

from reno import source_preservation_witness as witness, sync_state
from reno import sync_source_legacy_pending_intake17 as intake
from reno import sync_source_legacy_pending_state17 as capsules
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_legacy_pending_intake17 import real_cycle, received, sql_rows


@contextmanager
def actual_prepared_after(value):
    intent, arguments, receipt, _answer = received(value)
    with value.store.reference_operation(), intake.capture_legacy_pending17_locked(**arguments) as captured:
        before, after = captured.expected_state_effect()
        saved = capsules.prepare_locked(captured)
        with intake.effects._cas(captured.owner, captured._schema, captured._selected_pins,
                before, after, control=captured) as guard:
            with guard.writer():
                guard.replace()
        state_path = value.store.data_dir / sync_state._STATE
        after_raw = state_path.read_bytes()
        assert value.store.read() == after
        assert after_raw == sync_state._validate(after)
        assert hashlib.sha256(after_raw).hexdigest() == saved["value"]["after_sha256"]
        capsule_path = capsules.path(value.profile, intent["record"]["record_id"])
        marker = capsule_path.with_name(intent["record"]["record_id"] + ".applied.json")
        assert capsule_path == saved["path"] and not marker.exists()
        yield {"captured": captured, "intent": intent, "receipt": receipt, "before": before, "after": after,
            "before_raw": base64.b64decode(saved["value"]["before_raw_base64"], validate=True),
            "state_path": state_path, "after_raw": after_raw, "marker": marker, "capsule_path": capsule_path,
            "capsule_original": (capsule_path.read_bytes(), capsule_path.stat().st_mtime_ns),
            "permanent_original": permanent_files(value), "sql_original": sql_rows(value.profile),
            "http_original": deepcopy(value.transport.seen)}


def assert_originals_unchanged(value, actual):
    path = actual["capsule_path"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == actual["capsule_original"]
    original = json.loads(path.read_bytes())
    assert original["request_id"] == actual["intent"]["record"]["record_id"]
    assert original["intent_sha256"] == actual["intent"]["ref"]["record_sha256"]
    assert original["receipt_sha256"] == actual["receipt"]["ref"]["record_sha256"]
    assert permanent_files(value) == actual["permanent_original"]
    assert sql_rows(value.profile) == actual["sql_original"]
    assert value.transport.seen == actual["http_original"]


def test_actual_complete_after_and_real_append_callback_create_only_original_applied_marker(real_cycle, monkeypatch):
    value, callbacks = real_cycle, []
    with actual_prepared_after(value) as actual, monkeypatch.context() as scoped:
        original_callback = value.store.check_cancelled

        def checked():
            if original_callback is not None:
                original_callback()
            writer = capsules._APPEND_CURRENT.get()
            if writer is not None and writer._role == "legacy_pending_applied" and not callbacks:
                assert writer._producer is actual["captured"]
                assert writer._request_id == actual["intent"]["record"]["record_id"]
                assert actual["state_path"].read_bytes() == actual["after_raw"]
                assert not actual["marker"].exists()
                callbacks.append(writer._request_id)

        scoped.setattr(value.store, "check_cancelled", checked)
        scoped.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Finish repeated HTTP"))
        result = capsules.finish_locked(actual["captured"])
        assert callbacks == [actual["intent"]["record"]["record_id"]]
        assert result["path"] == actual["marker"] and actual["marker"].is_file()
        assert result["value"]["observed_after_sha256"] == hashlib.sha256(actual["after_raw"]).hexdigest()
        assert actual["state_path"].read_bytes() == actual["after_raw"]
        assert_originals_unchanged(value, actual)


@pytest.mark.parametrize("damage", ["original_before", "partial_after"])
def test_actual_append_callback_cannot_publish_completion_after_corrupting_original_state(real_cycle, monkeypatch, damage):
    value, callbacks = real_cycle, []
    with pytest.raises(intake.SyncError) as raised, \
            actual_prepared_after(value) as actual, monkeypatch.context() as scoped:
        if damage == "original_before":
            damaged_raw = actual["before_raw"]
        else:
            partial = deepcopy(actual["after"])
            # Explicit negative corruption: only the actual original pending
            # selector is put back; no origin or authority positive is forged.
            partial["source_recovery_pending"] = deepcopy(actual["before"]["source_recovery_pending"])
            damaged_raw = sync_state._validate(partial)
        assert damaged_raw != actual["after_raw"]
        original_callback = value.store.check_cancelled

        def corrupt_at_real_callback():
            if original_callback is not None:
                original_callback()
            writer = capsules._APPEND_CURRENT.get()
            if writer is not None and writer._role == "legacy_pending_applied" and not callbacks:
                assert writer._producer is actual["captured"]
                assert writer._request_id == actual["intent"]["record"]["record_id"]
                assert actual["state_path"].read_bytes() == actual["after_raw"]
                assert not actual["marker"].exists()
                callbacks.append(writer._request_id)
                # Intentionally bypass cooperative State writers, solely as a
                # negative tampering case at the genuine check_cancelled seam.
                actual["state_path"].write_bytes(damaged_raw)

        scoped.setattr(value.store, "check_cancelled", corrupt_at_real_callback)
        scoped.setattr(value.client, "_request", lambda *a, **kw: pytest.fail("Finish repeated HTTP"))
        capsules.finish_locked(actual["captured"])
    assert raised.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    cause, seen, causes = raised.value, set(), []
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        causes.append(cause)
        cause = cause.__context__
    # The missing-marker probe is the original FileNotFoundError at the end
    # of this real chain. Require the exact actual State-proof failure within
    # the chain, rather than mistaking that probe for the rejecting boundary.
    assert any(type(item) is witness.WitnessCaptureError
        and item.code == "SOURCE_WITNESS_STATE_CHANGED" for item in causes)
    assert callbacks == [actual["intent"]["record"]["record_id"]]
    assert actual["state_path"].read_bytes() == damaged_raw
    assert not actual["marker"].exists()
    assert_originals_unchanged(value, actual)
