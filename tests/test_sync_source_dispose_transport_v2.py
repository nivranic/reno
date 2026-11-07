"""Dispose2 uses the actual original HTTP engine and endpoint parser."""
from copy import deepcopy
import json

import httpx
import pytest

from reno import sync_source_readmit_contract as wire, sync_source_transport17 as transport
from reno.sync_client import SyncClient, SyncClientError
from reno.sync_protocol import SyncError
from tests import test_sync_source_dispose_contract_v2 as sample
from tests import test_sync_source_readmit_contract as old


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
@pytest.mark.parametrize("applied", [False, True])
def test_closed_dispose2_actual_http_ticket_retains_original_body_and_fact(kind, applied):
    parent_body = old.old_request()
    parent = old.old_receipt(parent_body)
    body = sample.request(kind, registered=True, defer_request_id=parent_body["request_id"])
    answer = sample.result(body, applied=applied, parent=parent)
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answer)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: "isolated-token",
        transport=httpx.MockTransport(handler))
    assert client.source_dispose(body, defer_receipt=parent) == answer
    with transport.capture_receipt(client, request_id=body["request_id"],
            request_sha256=wire.request_sha256(body)) as proof:
        assert proof.request == body and proof.result == answer
        assert proof.provenance["endpoint"] == "source/dispose"
        assert proof.defer_receipt == parent
    assert seen == [body]


def test_public_replacement_dto_has_no_dispose2_ticket(monkeypatch):
    body = sample.request()
    answer = sample.result(body)
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: "isolated-token",
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected RPC")))
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: deepcopy(answer))
    assert client.source_dispose(body) == answer
    with pytest.raises(SyncError), transport.capture_receipt(client,
            request_id=body["request_id"], request_sha256=wire.request_sha256(body)):
        pass


def test_wrong_result_version_is_not_activated_into_a_dispose2_ticket():
    body = sample.request()
    answer = sample.result(body)
    answer["format_version"] = 1
    client = SyncClient("https://sync.example.test/reno", token_provider=lambda: "isolated-token",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answer)))
    with pytest.raises(SyncClientError):
        client.source_dispose(body)
    with pytest.raises(SyncError), transport.capture_receipt(client,
            request_id=body["request_id"], request_sha256=wire.request_sha256(body)):
        pass
