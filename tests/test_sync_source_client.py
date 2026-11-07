"""Source outcomes send only the persisted body and accept its exact receipt."""
from copy import deepcopy
import json
import uuid

import httpx
import pytest

from reno import sync_source_control_contract as contract
from reno.sync_client import SyncClient, SyncClientError
from tests.test_sync_budget_client import SOURCE, TARGET, LEASE, CLAIM, STREAM


def request():
    return {"kind": "source_fail", "request_id": str(uuid.uuid4()),
        "source": deepcopy(SOURCE), "target": deepcopy(TARGET), "lease": deepcopy(LEASE),
        "claim": deepcopy(CLAIM), "recovery_generation": 0, "recovery_stream": deepcopy(STREAM),
        "core_sha256": "a" * 64, "submitted_url_sha256": "b" * 64,
        "failure": {"format_version": 1, "operation_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()), "ordinal": 4, "input_sha256": "1" * 64,
            "request_sha256": "2" * 64, "settlement_sha256": "3" * 64, "result_sha256": "4" * 64,
            "operation_kind": "source_metadata", "segment": None, "source_contract_version": 1,
            "resolver_contract_version": None, "resolver_phase": None, "error_class": "source_unavailable"}}


def receipt(body):
    return {"format_version": 1, "request_id": body["request_id"],
        "request_sha256": contract.request_sha256(body), "core_sha256": body["core_sha256"],
        "submitted_url_sha256": body["submitted_url_sha256"],
        "failure_sha256": contract.failure_sha256(body["failure"]),
        "item_id": body["claim"]["item_id"], "lease_generation": body["claim"]["lease_generation"],
        "attempts": 1, "status": "dead", "error_class": body["failure"]["error_class"],
        "committed_at": "2030-01-01T00:00:00.000Z", "replayed": False}


def client(handler):
    return SyncClient("https://cloud.example.test", token_provider=lambda: "artificial-credential",
                      transport=httpx.MockTransport(handler))


def test_lost_reply_reuses_original_body_uuid_and_historical_lease():
    body, sent = request(), []
    # Historical replay is permitted with the saved lease; the client must not
    # refresh credentials or replace an expired session with a current one.
    body["lease"]["lease_until"] = "2000-01-01T00:00:00.000Z"
    original = deepcopy(body)

    def handle(wire):
        assert wire.url.path == "/api/sync/push"
        sent.append(wire.content)
        if len(sent) == 1:
            raise httpx.ReadError("synthetic lost receipt with private detail")
        return httpx.Response(200, json={**receipt(body), "replayed": True})

    item = client(handle)
    with pytest.raises(SyncClientError) as caught:
        item.source_control(body)
    assert caught.value.code == "SYNC_CLIENT_NETWORK"
    assert "private detail" not in str(caught.value)
    assert item.source_control(body)["replayed"] is True
    assert len(sent) == 2 and sent[0] == sent[1]
    assert json.loads(sent[1]) == original == body


@pytest.mark.parametrize("changes", [
    {"request_id": str(uuid.uuid4())}, {"request_sha256": "f" * 64},
    {"core_sha256": "f" * 64}, {"submitted_url_sha256": "f" * 64}, {"failure_sha256": "f" * 64},
    {"item_id": CLAIM["item_id"] + 1}, {"lease_generation": CLAIM["lease_generation"] + 1},
    {"status": "queued"}, {"attempts": True}, {"attempts": 0}, {"attempts": 6},
    {"format_version": True}, {"error_class": "rate_limited"}, {"replayed": 1},
    {"committed_at": "private-path"}, {"private_body": "not-exportable"},
])
def test_foreign_or_open_receipt_cannot_authorize_terminal(changes):
    body = request()
    result = {**receipt(body), **changes}
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, json=result)).source_control(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"


@pytest.mark.parametrize("changes", [
    {"kind": "fail"}, {"request_id": "private-input"}, {"core_sha256": "\ud800"},
    {"recovery_generation": True}, {"private_body": "not-exportable"},
    {"target": {**TARGET, "handshake": {}}},
    {"recovery_stream": {"library_id": str(uuid.uuid4()), "library_epoch": 2}},
])
def test_invalid_frozen_request_is_rejected_without_any_send(changes):
    body = {**request(), **changes}
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: pytest.fail("Invalid source request must not send")).source_control(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_REQUEST"


@pytest.mark.parametrize("remote_code", ["SYNC_SOURCE_REQUEST_MISMATCH", "SYNC_SOURCE_RECEIPT_INVALID"])
def test_closed_source_error_preserves_code_but_never_remote_detail(remote_code):
    body = request()
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(409, json={"code": remote_code,
            "detail": "synthetic cookie or private source"})).source_control(body)
    assert caught.value.code == remote_code and caught.value.status == 409
    assert "cookie" not in str(caught.value) and "source" not in str(caught.value)


def test_oversized_source_reply_is_bounded_before_receipt_validation():
    body = request()
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, content=b" " * (contract.MAX_CONTROL_BYTES + 1),
            headers={"Content-Type": "application/json"})).source_control(body)
    assert caught.value.code == "SYNC_CLIENT_TOO_LARGE"
