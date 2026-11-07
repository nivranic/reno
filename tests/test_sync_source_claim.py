"""Real HTTP parser observations; DTOs and renewal counts never prove a claim."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
from types import SimpleNamespace
import uuid

import httpx
import pytest

from reno import sync_source_claim as claims
from reno.sync_client import SyncClient
from reno.sync_protocol import SyncError
from tests.test_sync_client import SOURCE, TARGET, LEASE, CLAIM, RECOVERY, TOKEN


def environment(handler=None):
    source = deepcopy(SOURCE)
    source["handshake"]["migration_version"] = 17
    target = {key: TARGET[key] for key in ("library_id", "node_id")}
    lease, recovery = deepcopy(LEASE), deepcopy(RECOVERY)
    entry = {"item_id": CLAIM["item_id"], "url": "https://www.bilibili.com/video/BV1xx411c7mD",
             "attempts": 1, "lease_until": lease["lease_until"], "claim": deepcopy(CLAIM)}
    heartbeat = {"lease": lease, "renewed_claims": 1, "recovery": recovery}
    pull = {"events": {"events": [], "next_cursor": 0, "high_watermark": 0, "has_more": False,
                       "stream": recovery["stream"]}, "claims": [entry], "recovery": recovery}
    continuity = {"format_version": 1, "kind": "source_continuity",
        "source_identity": {key: source[key] for key in ("library_id", "node_id")},
        "target_identity": {key: target[key] for key in ("library_id", "node_id")},
        "handshake": source["handshake"], "receiver_version": {**recovery["stream"], "kb_revision": 0},
        "state": "current", "continuity": {"format_version": 1, "continuity_id": str(uuid.uuid4()),
            "contract_sha256": "c" * 64, "target_epoch": recovery["stream"]["library_epoch"]},
        "head": {"ordinal": 0, "sha256": "d" * 64}}
    value = SimpleNamespace(source=source, target=target, lease=lease, entry=entry,
        recovery=recovery, payloads={"heartbeat": heartbeat, "pull": pull, "continuity": continuity}, seen=[])
    def transport(request):
        endpoint = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content)
        value.seen.append((endpoint, body))
        return handler(value, endpoint, body) if handler is not None else httpx.Response(200, json=value.payloads[endpoint])
    value.client = SyncClient("https://sync.example.test/reno", token_provider=lambda: TOKEN,
                              transport=httpx.MockTransport(transport))
    return value


def observe(value):
    heartbeat = value.client.heartbeat(value.source, value.lease["session_id"])
    pull = value.client.pull(value.source, heartbeat["lease"], claim_limit=1)
    continuity = value.client.source_continuity(value.source, value.target)
    return heartbeat, pull, continuity


def state(value):
    return {"binding": {"target_url": value.client._url,
                "home_library_id": value.source["library_id"], "home_node_id": value.source["node_id"],
                "target_library_id": value.target["library_id"], "target_node_id": value.target["node_id"]},
            "session_id": value.lease["session_id"], "lease": deepcopy(value.lease),
            "claims": [deepcopy(value.entry)], "recovery": deepcopy(value.recovery)}


def capture(value):
    return claims.capture_initial_claim(value.client, source=value.source, item_id=value.entry["item_id"])


def test_real_parser_claim_is_closed_and_returns_only_copies():
    value = environment()
    heartbeat, pull, continuity = observe(value)
    with capture(value) as proof:
        proof.check(state=state(value))
        original = proof.entry
        proof.entry["url"] = "https://www.bilibili.com/video/BV1Changed"
        proof.continuity["state"] = "unknown"
        pull["claims"][0]["url"] = "https://www.bilibili.com/video/BV1Changed"
        heartbeat["lease"]["lease_token"] = str(uuid.uuid4())
        continuity["continuity"]["continuity_id"] = str(uuid.uuid4())
        assert proof.entry == original
        assert proof.continuity["state"] == "current"
        proof.check(state=state(value))
    with pytest.raises(SyncError, match="Initial source claim"):
        proof.check()
    assert [endpoint for endpoint, _ in value.seen] == ["heartbeat", "pull", "continuity"]


def test_public_dtos_and_replaced_request_do_not_create_http_evidence(monkeypatch):
    value = environment()
    def dto(endpoint, **kwargs):
        return deepcopy(value.payloads[endpoint.rsplit("/", 1)[-1]])
    monkeypatch.setattr(value.client, "_request", dto)
    observe(value)  # All endpoint parsers accept the shape, without raw evidence.
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("DTO responses supplied a claim capability")
    with pytest.raises(SyncError):
        claims.InitialClaimCapture(client=value.client, source=value.source,
            item_id=value.entry["item_id"], seal=object())


def test_replaced_request_cannot_borrow_the_private_http_hooks(monkeypatch):
    value = environment()
    def dto(endpoint, *, body, **kwargs):
        content = json.dumps(body).encode("utf-8")
        call = claims._begin(value.client, endpoint, content, target_url=value.client._url)
        result = deepcopy(value.payloads[endpoint.rsplit("/", 1)[-1]])
        claims._complete(value.client, call, result)
        return result
    monkeypatch.setattr(value.client, "_request", dto)
    with pytest.raises(SyncError):
        observe(value)
    assert value.seen == []
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("A request replacement borrowed the actual HTTP frame")


def test_response_cleanup_failure_never_leaves_a_completed_claim_ticket():
    class FailedClose(httpx.ByteStream):
        def close(self):
            raise OSError("Controlled response cleanup failure")
    def handler(value, endpoint, body):
        return httpx.Response(200, headers={"content-type": "application/json"},
            stream=FailedClose(json.dumps(value.payloads[endpoint]).encode("utf-8")))
    value = environment(handler)
    with pytest.raises(SyncError):
        value.client.heartbeat(value.source, value.lease["session_id"])
    assert value.client._source_claim_broker.pending == {}
    assert value.client._source_claim_broker.raw == {}
    assert value.client._source_claim_broker.latest == {}


def test_cancel_callback_cannot_stamp_dtos_inside_a_real_request_frame(monkeypatch):
    value = environment()
    original_request = value.client._request
    current = {}
    attempts = []
    def cancelled():
        if not current:
            return
        current["checks"] += 1
        # First three checks precede the actual request scope. The transport's
        # deadline callback runs in the scope, before reaching the test server.
        if current["checks"] == 4:
            attempts.append(current["endpoint"])
            call = claims._begin(value.client, current["endpoint"], current["content"],
                                 target_url=value.client._url)
            claims._complete(value.client, call, current["result"])
            raise RuntimeError("Controlled cancellation after attempted DTO stamp")
    def replaced(endpoint, *, body, **kwargs):
        result = deepcopy(value.payloads[endpoint.rsplit("/", 1)[-1]])
        current.update(endpoint=endpoint, content=json.dumps(body, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8"), result=result, checks=0)
        try:
            original_request(endpoint, body=body, **kwargs)
        except SyncError:
            return result
        finally:
            current.clear()
        pytest.fail("Cancellation did not run before the transport")
    monkeypatch.setattr(value.client, "_check_cancelled", cancelled)
    monkeypatch.setattr(value.client, "_request", replaced)
    observe(value)
    assert value.seen == [] and len(attempts) == 3
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("A cancelled real scope supplied a fake successful response")


@pytest.mark.parametrize("missing", ["heartbeat", "pull", "continuity"])
def test_partial_remote_observations_never_form_initial_proof(missing):
    value = environment()
    if missing != "heartbeat":
        value.client.heartbeat(value.source, value.lease["session_id"])
    if missing != "pull":
        value.client.pull(value.source, value.lease, claim_limit=1)
    if missing != "continuity":
        value.client.source_continuity(value.source, value.target)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("Incomplete remote observations supplied authority")


@pytest.mark.parametrize("remote_state", ["absent", "unknown", "historical"])
def test_continuity_must_be_genuine_current(remote_state):
    value = environment()
    continuity = value.payloads["continuity"]
    continuity["state"] = remote_state
    if remote_state == "historical":
        continuity["continuity"]["target_epoch"] += 1
    else:
        continuity.update(continuity=None, head=None)
    observe(value)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("Noncurrent continuity supplied initial authority")


@pytest.mark.parametrize("mutation", ["peer", "session", "lease", "claim", "url", "revision", "expiry"])
def test_exact_state_fences_must_match_the_real_responses(mutation):
    value = environment()
    observe(value)
    changed = state(value)
    if mutation == "peer":
        changed["binding"]["target_node_id"] = str(uuid.uuid4())
    elif mutation == "session":
        changed["session_id"] = str(uuid.uuid4())
    elif mutation == "lease":
        changed["lease"]["lease_token"] = str(uuid.uuid4())
    elif mutation == "claim":
        changed["claims"][0]["claim"]["lease_generation"] += 1
    elif mutation == "url":
        changed["claims"][0]["url"] = "https://www.bilibili.com/video/BV1Changed"
    elif mutation == "revision":
        changed["recovery"]["revision"] += 1
    else:
        changed["lease"]["lease_until"] = "2000-01-01T00:00:00.000Z"
    with capture(value) as proof:
        with pytest.raises(SyncError):
            proof.check(state=changed)
        proof.check(state=state(value))


def test_counted_claim_renewal_does_not_extend_the_original_claim_expiry(monkeypatch):
    value = environment()
    value.entry["lease_until"] = "2030-01-01T00:00:00.000Z"
    observe(value)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2031, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(claims, "datetime", Later)
    value.client.heartbeat(value.source, value.lease["session_id"], lease=value.lease)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("Renewed count extended an unobserved expired item")


def test_valid_session_renewal_is_accepted_but_new_session_rejects_old_capture():
    value = environment()
    observe(value)
    with capture(value) as proof:
        value.payloads["heartbeat"]["lease"]["lease_until"] = "2099-02-01T00:00:00.000Z"
        value.client.heartbeat(value.source, value.lease["session_id"], lease=deepcopy(LEASE))
        proof.check(state=state(value))
    with pytest.raises(SyncError):
        with capture(value) as proof:
            new_session = str(uuid.uuid4())
            value.payloads["heartbeat"]["lease"] = {**value.lease, "session_id": new_session,
                "lease_token": str(uuid.uuid4()), "generation": value.lease["generation"] + 1}
            value.client.heartbeat(value.source, new_session)
            proof.check()


def test_new_pull_invalidates_held_capture_even_for_the_same_item():
    value = environment()
    observe(value)
    with pytest.raises(SyncError):
        with capture(value) as proof:
            value.client.pull(value.source, value.lease, claim_limit=1)
            proof.check()
    with capture(value) as current:
        assert current.entry == value.entry


def test_capture_cannot_cross_thread_or_async_task():
    value = environment()
    observe(value)
    with capture(value) as proof:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(SyncError):
                pool.submit(context.run, proof.check).result()
    async def scenario():
        with capture(value) as proof:
            async def other():
                proof.check()
            with pytest.raises(SyncError):
                await asyncio.create_task(other())
            proof.check()
    asyncio.run(scenario())


def test_background_heartbeat_has_its_own_parser_receipt_and_main_capture():
    value = environment()
    observe(value)
    with capture(value) as proof:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(value.client.heartbeat, value.source, value.lease["session_id"], value.lease).result()
        proof.check(state=state(value))


def test_late_old_heartbeat_does_not_replace_a_newer_control_reply():
    old_started, release = threading.Event(), threading.Event()
    count = 0
    count_lock = threading.Lock()
    def handler(value, endpoint, body):
        nonlocal count
        payload = deepcopy(value.payloads[endpoint])
        if endpoint == "heartbeat":
            with count_lock:
                count += 1
                ordinal = count
            if ordinal == 2:
                old_started.set()
                assert release.wait(5)
            elif ordinal == 3:
                payload["recovery"]["revision"] = 1
        return httpx.Response(200, json=payload)
    value = environment(handler)
    observe(value)
    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(value.client.heartbeat, value.source, value.lease["session_id"], value.lease)
        assert old_started.wait(5)
        value.client.heartbeat(value.source, value.lease["session_id"], value.lease)
        release.set()
        delayed.result()
    assert value.client._source_claim_broker.latest["heartbeat"].response["recovery"]["revision"] == 1
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("Late old heartbeat revived the old control")


def test_sent_source_bytes_are_frozen_before_caller_mutates_input():
    def handler(value, endpoint, body):
        if endpoint == "heartbeat":
            value.source["node_id"] = str(uuid.uuid4())
        return httpx.Response(200, json=value.payloads[endpoint])
    value = environment(handler)
    original_source = deepcopy(value.source)
    value.client.heartbeat(value.source, value.lease["session_id"])
    assert value.client._source_claim_broker.latest["heartbeat"].request["source"] == original_source
    value.client.pull(value.source, value.lease, claim_limit=1)
    value.payloads["continuity"]["source_identity"]["node_id"] = value.source["node_id"]
    value.client.source_continuity(value.source, value.target)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("Post-send input mutation rewrote the heartbeat source")


def test_old_session_cannot_be_revived_by_a_later_successful_reply():
    value = environment()
    observe(value)
    original_lease = deepcopy(value.lease)
    with pytest.raises(SyncError):
        with capture(value) as proof:
            new_session = str(uuid.uuid4())
            value.payloads["heartbeat"]["lease"] = {**original_lease, "session_id": new_session,
                "lease_token": str(uuid.uuid4()), "generation": original_lease["generation"] + 1}
            value.client.heartbeat(value.source, new_session)
            with pytest.raises(SyncError):
                proof.check()
            value.payloads["heartbeat"]["lease"] = original_lease
            value.client.heartbeat(value.source, original_lease["session_id"])
            proof.check()  # Even a later request must not lower the lease fence.


def test_new_continuity_epoch_cannot_be_replaced_by_a_later_old_reply():
    value = environment()
    observe(value)
    old_continuity = deepcopy(value.payloads["continuity"])
    value.payloads["continuity"]["receiver_version"]["library_epoch"] = 2
    value.payloads["continuity"]["continuity"]["target_epoch"] = 2
    value.client.source_continuity(value.source, value.target)
    value.payloads["continuity"] = old_continuity
    value.client.source_continuity(value.source, value.target)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("A later old continuity reply lowered the receiver epoch")


@pytest.mark.parametrize("change", ["url", "transport"])
def test_peer_location_and_transport_are_bound_to_the_actual_requests(change):
    value = environment()
    observe(value)
    if change == "url":
        value.client._url = "https://other.example.test/reno"
    else:
        value.client._transport = httpx.MockTransport(lambda _: pytest.fail("No new observation was obtained"))
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("A changed peer reused the old transport receipts")


@pytest.mark.parametrize("change", ["head", "contract"])
def test_same_epoch_continuity_conflict_cannot_be_revived(change):
    value = environment()
    observe(value)
    original = deepcopy(value.payloads["continuity"])
    if change == "head":
        value.payloads["continuity"]["head"]["sha256"] = "e" * 64
    else:
        value.payloads["continuity"]["continuity"]["continuity_id"] = str(uuid.uuid4())
    value.client.source_continuity(value.source, value.target)
    value.payloads["continuity"] = original
    value.client.source_continuity(value.source, value.target)
    with pytest.raises(SyncError):
        with capture(value):
            pytest.fail("A contradictory continuity was later revived")
