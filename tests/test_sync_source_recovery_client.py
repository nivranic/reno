"""Closed source recovery transport and exact frozen-body retries."""
from copy import deepcopy
import json

import httpx
import pytest

from reno.sync_client import SyncClient, SyncClientError
from tests import test_sync_source_readmit_contract as sample


def client(handler):
    return SyncClient("https://cloud.example.test", token_provider=lambda: "artificial-source-token",
                      transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("kind,body,result,endpoint", [
    ("source_register", sample.register, sample.registration, "source/register"),
    ("source_dispose", sample.dispose, sample.disposition, "source/dispose"),
    ("source_readmit", sample.readmit, sample.admission, "source/readmit"),
])
def test_lost_response_reuses_exact_request_and_receipt_binding(kind, body, result, endpoint):
    original = body()
    expected = result(original)
    sent = []
    def handle(request):
        assert request.url.path == "/api/sync/" + endpoint
        assert request.headers["X-Sync-Token"] == "artificial-source-token"
        sent.append(request.content)
        if len(sent) == 1:
            raise httpx.ReadError("synthetic lost response")
        return httpx.Response(200, json={**expected, "replayed": True})
    instance = client(handle)
    with pytest.raises(SyncClientError) as caught:
        getattr(instance, kind)(original)
    assert caught.value.code == "SYNC_CLIENT_NETWORK"
    assert getattr(instance, kind)(original) == {**expected, "replayed": True}
    assert sent[0] == sent[1] and json.loads(sent[1]) == original


@pytest.mark.parametrize("method,body", [
    ("source_register", sample.readmit), ("source_dispose", sample.register), ("source_readmit", sample.dispose),
])
def test_endpoint_kind_mismatch_never_sends(method, body):
    with pytest.raises(SyncClientError) as caught:
        getattr(client(lambda _: pytest.fail("Wrong endpoint must not send")), method)(body())
    assert caught.value.code == "SYNC_CLIENT_INVALID_REQUEST"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(request_sha256="1" * 64),
    lambda value: value.update(core_sha256="1" * 64),
    lambda value: value.update(private_path="artificial-private-path"),
    lambda value: value.update(replayed=1),
])
def test_registration_result_is_closed_and_matches_original(mutate):
    body = sample.register()
    result = sample.registration(body)
    mutate(result)
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(200, json=result)).source_register(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"
    assert "artificial-private-path" not in str(caught.value)


def test_applied_resume_disposition_requires_exact_parent_for_interpretation():
    defer = sample.old_request(legacy=True)
    parent = sample.old_receipt(defer)
    resume = sample.old_request("budget_resume", legacy=True, defer_request_id=defer["request_id"])
    body = sample.dispose(resume)
    expected = sample.disposition(body, applied=True, parent=parent)
    instance = client(lambda _: httpx.Response(200, json=expected))
    with pytest.raises(SyncClientError) as caught:
        instance.source_dispose(body)
    assert caught.value.code == "SYNC_CLIENT_INVALID_RESPONSE"
    assert instance.source_dispose(body, defer_receipt=parent) == expected
    bad_parent = deepcopy(parent)
    bad_parent["attempts"] += 1
    with pytest.raises(SyncClientError):
        instance.source_dispose(body, defer_receipt=bad_parent)


@pytest.mark.parametrize("code", ["SYNC_SOURCE_READMIT_EFFECT_UNKNOWN", "SYNC_SOURCE_CONTINUITY_RECORD_MISSING",
                                  "SYNC_SOURCE_READMIT_LINEAGE_MISMATCH"])
def test_closed_remote_error_code_survives_without_remote_detail(code):
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(409, json={"code": code, "detail": "artificial-private-detail"})).source_readmit(sample.readmit())
    assert caught.value.code == code and caught.value.status == 409
    assert "artificial-private-detail" not in str(caught.value)


def test_unknown_remote_code_and_detail_are_not_exported():
    with pytest.raises(SyncClientError) as caught:
        client(lambda _: httpx.Response(409, json={"code": "arbitrary-private-code", "detail": "private-detail"})).source_readmit(sample.readmit())
    assert caught.value.code == "SYNC_CLIENT_REMOTE"
    assert "arbitrary-private-code" not in str(caught.value)
