"""Artificial wire receipts must bind exact persistent budget requests."""
from copy import deepcopy
import hashlib
import json
import uuid

import httpx
import pytest

from reno.sync_client import SyncClient, SyncClientError


def identifier():
    return str(uuid.uuid4())


SOURCE = {"library_id": identifier(), "node_id": identifier(), "handshake": {
    "protocol_version": 2, "migration_version": 14, "core_schema_sha256": "a" * 64}}
TARGET = {"library_id": identifier(), "node_id": identifier()}
LEASE = {"session_id": identifier(), "lease_token": identifier(), "generation": 1,
         "lease_until": "2099-01-01T00:00:00.000Z"}
CLAIM = {"item_id": 7, "lease_token": identifier(), "lease_generation": 3}
STREAM = {"library_id": TARGET["library_id"], "library_epoch": 2}


def request():
    return {"kind": "budget_defer", "request_id": identifier(), "source": deepcopy(SOURCE),
            "target": deepcopy(TARGET), "lease": deepcopy(LEASE), "claim": deepcopy(CLAIM),
            "recovery_generation": 0, "recovery_stream": deepcopy(STREAM),
            "reason": "daily_limit", "retry_at": "2099-01-01T00:00:00.001Z",
            "budget_operation_id": identifier()}


def receipt(body):
    return {"request_id": body["request_id"], "request_sha256": hashlib.sha256(json.dumps(
        body, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "item_id": CLAIM["item_id"], "lease_generation": CLAIM["lease_generation"],
        "status": "budget_wait" if body["kind"] == "budget_defer" else "queued", "attempts": 4,
        "reason": body.get("reason"), "retry_at": body.get("retry_at"),
        "budget_operation_id": body.get("budget_operation_id"),
        "committed_at": "2030-01-01T00:00:00.000Z", "replayed": False}


def client(handler):
    return SyncClient("https://cloud.example.test", token_provider=lambda: "artificial-credential",
                      transport=httpx.MockTransport(handler))


def test_lost_reply_replays_exact_frozen_body_and_does_not_refresh_lease():
    body, sent = request(), []
    def handle(wire):
        sent.append(wire.content)
        if len(sent) == 1:
            raise httpx.ReadError("offline synthetic lost receipt")
        result = receipt(body)
        result["replayed"] = True
        return httpx.Response(200, json=result)
    item = client(handle)
    with pytest.raises(SyncClientError) as caught:
        item.budget_request(body)
    assert caught.value.code == "SYNC_CLIENT_NETWORK"
    assert item.budget_request(body)["replayed"] is True
    assert len(sent) == 2 and sent[0] == sent[1]
    assert json.loads(sent[1]) == body


@pytest.mark.parametrize("changes", [
    {"request_id": identifier()}, {"request_sha256": "f" * 64}, {"item_id": 8},
    {"lease_generation": 4}, {"status": "queued"}, {"attempts": True}, {"attempts": 5},
    {"reason": "manual_pause"}, {"retry_at": None}, {"budget_operation_id": identifier()},
    {"replayed": 1}, {"committed_at": "secret-path"}, {"private_path": "do-not-export"},
])
def test_unbound_or_open_receipt_is_rejected(changes):
    body = request()
    result = {**receipt(body), **changes}
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, json=result)).budget_request(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"


@pytest.mark.parametrize("changes", [
    {"reason": "arbitrary"}, {"reason": True}, {"retry_at": "2099-01-01T00:00:00Z"},
    {"budget_operation_id": "private-credential"}, {"recovery_generation": True},
    {"extra": "unknown"}, {"target": {**TARGET, "handshake": {}}},
    {"recovery_stream": {"library_id": identifier(), "library_epoch": 2}},
])
def test_invalid_request_stops_before_transport(changes):
    body = {**request(), **changes}
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: pytest.fail("Invalid request must not send")).budget_request(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_REQUEST"


def test_resume_requires_original_defer_receipt_and_checks_its_item():
    original = request()
    proof = receipt(original)
    body = {key: deepcopy(original[key]) for key in (
        "source", "target", "lease", "recovery_generation", "recovery_stream")}
    body.update(kind="budget_resume", request_id=identifier(), defer_request_id=original["request_id"])
    good = receipt(body)
    assert client(lambda _: httpx.Response(200, json=good)).budget_request(body, defer_receipt=proof) == good
    with pytest.raises(SyncClientError):
        client(lambda _: pytest.fail("Resume needs original receipt")).budget_request(body)
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, json={**good, "item_id": 8})).budget_request(body, defer_receipt=proof)
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"


@pytest.mark.parametrize("changes", [
    {"attempts": True}, {"replayed": 1}, {"committed_at": "invalid-private-value"},
    {"retry_at": "invalid-private-value"}, {"budget_operation_id": "invalid-private-value"},
    {"status": "queued"}, {"request_sha256": "invalid-private-value"},
])
def test_resume_rejects_damaged_original_proof_before_transport(changes):
    original = request()
    proof = {**receipt(original), **changes}
    body = {key: deepcopy(original[key]) for key in (
        "source", "target", "lease", "recovery_generation", "recovery_stream")}
    body.update(kind="budget_resume", request_id=identifier(), defer_request_id=original["request_id"])
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: pytest.fail("Damaged proof must not send")).budget_request(body, defer_receipt=proof)
    assert caught.value.code == "SYNC_CLIENT_INVALID_REQUEST"


def test_resume_cannot_change_refunded_attempt_count():
    original = request()
    body = {key: deepcopy(original[key]) for key in (
        "source", "target", "lease", "recovery_generation", "recovery_stream")}
    body.update(kind="budget_resume", request_id=identifier(), defer_request_id=original["request_id"])
    result = {**receipt(body), "attempts": 0}
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, json=result)).budget_request(body, defer_receipt=receipt(original))
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"
