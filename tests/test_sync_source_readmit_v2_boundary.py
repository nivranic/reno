"""Pure v2 must not silently enter the still-v1 operational consumers."""
import pytest

from reno import sync_source_authority as authority, sync_source_readmission as service
from reno import sync_source_continuity_service as resources, sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests.test_sync_source_readmit_contract_v2 import request


@pytest.mark.parametrize("basis", [None, "manual", "timer"])
def test_old_sql_consumer_rejects_v2_before_opening_any_resource(basis, monkeypatch):
    body = request(basis=basis)
    assert wire.normalize_request(body) == body
    monkeypatch.setattr(resources, "writer", lambda **kwargs: pytest.fail("V2 reached a v1 writer"))
    with pytest.raises(SyncError) as caught:
        service.readmit(profile=None, request=body)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"


def test_local_v1_intent_cannot_embed_v2_timer_provenance():
    body = request(basis="timer")
    value = {"format_version": 1, "record_kind": "recovery_intent", "record_id": body["request_id"],
        "core_sha256": body["original"]["core_sha256"], "request_sha256": wire.request_sha256(body),
        "request": body, "expected_before": None, "preservation_basis": None, "manual_resume_basis": None}
    active = {"binding": {"home_library_id": body["source"]["library_id"], "home_node_id": body["source"]["node_id"],
        "target_library_id": body["target"]["library_id"], "target_node_id": body["target"]["node_id"],
        "target_url": "https://synthetic.example.test"}}
    with pytest.raises(SyncError):
        authority.validate_record("requests", value, active)
