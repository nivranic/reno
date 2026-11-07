"""Real original HTTP/frame/parser cleanup; replacement DTOs have no seal."""
from contextvars import copy_context
from copy import deepcopy
import json

import httpx
import pytest

from reno import sync_source_transport17 as transport, sync_source_claim as engine
from reno.sync_client import SyncClient, SyncClientError
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract_v2 as sample


def peer(handler=None):
    body = sample.request()
    answer = sample.result(body)
    seen = []
    def send(request):
        seen.append(json.loads(request.content))
        return handler(request, answer) if handler is not None else httpx.Response(200, json=answer)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: "isolated-token",
        transport=httpx.MockTransport(send))
    return client, body, answer, seen


def capture(client, body):
    from reno.sync_source_readmit_contract import request_sha256
    return transport.capture_receipt(client, request_id=body["request_id"], request_sha256=request_sha256(body))


def test_real_readmit2_parser_mints_only_closed_http_receipt_and_copies():
    client, body, answer, seen = peer()
    assert client.source_readmit(body) == answer
    with capture(client, body) as proof:
        assert proof.request == body and proof.result == answer
        copied = proof.result
        copied["claim"]["lease_token"] = "changed"
        assert proof.result == answer
        assert len(seen) == 1 and seen[0] == body
    with pytest.raises(SyncError):
        proof.check()


def test_replaced_request_and_public_parser_dto_cannot_mint(monkeypatch):
    client, body, answer, seen = peer()
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: deepcopy(answer))
    assert client.source_readmit(body) == answer
    with pytest.raises(SyncError):
        with capture(client, body):
            pass
    assert seen == []


def test_hooks_without_original_engine_scope_cannot_begin():
    client, body, _, seen = peer()
    with pytest.raises(SyncError):
        transport._begin(client, "source/readmit", json.dumps(body), target_url=client._url)
    assert seen == []


def test_response_cleanup_failure_leaves_no_receipt():
    class Broken(httpx.SyncByteStream):
        def __init__(self, answer):
            self.answer = answer
        def __iter__(self):
            yield json.dumps(self.answer).encode()
        def close(self):
            raise OSError("isolated-close-failure")
    client, body, _, _ = peer(lambda request, answer: httpx.Response(200, stream=Broken(answer)))
    with pytest.raises(SyncClientError):
        client.source_readmit(body)
    with pytest.raises(SyncError):
        with capture(client, body):
            pass


def test_direct_raw_completion_is_not_activated_before_endpoint_validation():
    client, body, answer, _ = peer()
    actual = client._request("source/readmit", body=body)
    assert actual == answer
    with pytest.raises(SyncError):
        with capture(client, body):
            pass


@pytest.mark.parametrize("change", ["url", "transport"])
def test_receipt_cannot_follow_changed_destination(change):
    client, body, _, _ = peer()
    client.source_readmit(body)
    if change == "url":
        client._url = "https://other.example.test/reno"
    else:
        client._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with pytest.raises(SyncError):
        with capture(client, body):
            pass


def test_server_replay_keeps_original_fact_and_original_claim_deadline():
    client, body, answer, _ = peer()
    answer["replayed"] = True
    public = client.source_readmit(body)
    assert public["replayed"] is True
    with capture(client, body) as proof:
        assert proof.result["replayed"] is False
        assert proof.result["session"] == body["lease"]


def test_cancellation_hook_cannot_complete_call_before_transport_cleanup():
    client, body, answer, seen = peer()
    attempted = []
    def cancel():
        frame = engine._HTTP_FRAME.get()
        if frame is None or frame.call is None:
            return
        try:
            transport._complete(client, frame.call, deepcopy(answer))
        except SyncError:
            attempted.append(True)
        else:
            pytest.fail("a callback created a receipt before HTTP cleanup")
    client._check_cancelled = cancel
    assert client.source_readmit(body) == answer
    assert attempted and len(seen) == 1


def test_receipt_cannot_cross_thread_owner():
    from concurrent.futures import ThreadPoolExecutor
    client, body, _, _ = peer()
    client.source_readmit(body)
    with capture(client, body) as proof:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, proof.check)
            with pytest.raises(SyncError):
                future.result()


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_direct_control_original_parser_has_the_same_closed_transport_ticket(kind):
    from tests import test_sync_source_readmit_contract as old
    from reno.sync_source_readmit_contract import digest
    parent_body = old.old_request()
    parent_body["source"]["handshake"]["migration_version"] = 17
    parent = old.old_receipt(parent_body)
    body = old.old_request(kind, defer_request_id=parent_body["request_id"])
    body["source"]["handshake"]["migration_version"] = 17
    answer = old.old_receipt(body, parent=parent)
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answer)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: "isolated-token",
        transport=httpx.MockTransport(handler))
    public = client.source_control(body) if kind == "source_fail" else client.budget_request(body,
        defer_receipt=parent if kind == "budget_resume" else None)
    with transport.capture_receipt(client, request_id=body["request_id"], request_sha256=digest(body)) as proof:
        assert proof.request == body and proof.result == public == answer
        assert proof.defer_receipt == (parent if kind == "budget_resume" else None)
        assert proof.provenance["endpoint"] == "push"
    assert seen == [body]
