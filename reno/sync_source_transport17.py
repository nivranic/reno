"""Closed original SyncClient HTTP receipts, independent of current admission.

This module never creates an HTTP frame or accepts a public response DTO as
a producer. The original engine owns the only frame, transport, decoding and
cleanup boundary. Endpoint parsing activates that exact decoded object once.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
import threading

from . import sync_source_claim as engine
from .sync_protocol import SyncError

_SEAL = object()
_CURRENT = ContextVar("reno_source_receipt17_capture", default=None)
_ENDPOINTS = {"budget_defer": "push", "budget_resume": "push", "source_fail": "push",
    "source_register": "source/register", "source_dispose": "source/dispose", "source_readmit": "source/readmit"}


def _fail():
    raise SyncError("Original closed HTTP receipt is required", "SYNC_SOURCE_TRANSPORT17_PROOF_REQUIRED", 409) from None


def _encoded(value):
    return engine._encoded(value)


def _normalize_request(value):
    from . import sync_client, sync_source_control_contract as control, sync_source_readmit_contract as wire
    kind = value.get("kind") if type(value) is dict else None
    if kind in {"budget_defer", "budget_resume"}:
        return sync_client.normalize_budget_request(value)
    if kind == "source_fail":
        return control.normalize_request(value)
    if kind in {"source_register", "source_dispose", "source_readmit"}:
        return wire.normalize_request(value)
    _fail()


def _normalize_result(request, value, defer_receipt=None):
    from . import sync_client, sync_source_control_contract as control, sync_source_readmit_contract as wire
    if request["kind"] in {"budget_defer", "budget_resume"}:
        return sync_client.normalize_budget_receipt(request, value, defer_receipt=defer_receipt)
    if request["kind"] == "source_fail":
        return control.normalize_receipt(request, value)
    return wire.normalize_result(request, value, defer_receipt=defer_receipt)


@dataclass(frozen=True, slots=True, repr=False)
class _Call:
    client: object
    sequence: int
    endpoint: str
    target_url: str
    transport: object
    frame: object
    request_json: str
    owner: tuple


@dataclass(frozen=True, slots=True, repr=False)
class _Receipt:
    call: _Call
    response_json: str
    result_json: str
    parent_json: str


class _Broker:
    def __init__(self, client):
        self.client, self.process = client, os.getpid()
        self.lock, self.sequence = threading.RLock(), 0
        self.pending, self.raw, self.receipts = {}, {}, {}


def _broker(client):
    from .sync_client import SyncClient
    broker = getattr(client, "_source_transport17_broker", None)
    if (type(client) is not SyncClient or type(broker) is not _Broker
            or broker.client is not client or broker.process != os.getpid()):
        _fail()
    return broker


def _begin(client, endpoint, content, *, target_url):
    """Consume the original engine-created frame before any callback/HTTP."""
    if content is None or endpoint not in set(_ENDPOINTS.values()):
        return None
    raw = json.loads(content)
    if type(raw) is not dict or raw.get("kind") not in _ENDPOINTS:
        return None
    request = _normalize_request(raw)
    if _ENDPOINTS[request["kind"]] != endpoint:
        return None
    migration_version = request["source"]["handshake"]["migration_version"]
    if migration_version != 17:
        if migration_version != 16:
            return None
        from . import sync_source_legacy_pending_intake17 as pending
        if pending._HTTP.get() is None:
            return None
        pending.require_original_legacy_http_call17(client, endpoint, content,
                                                   target_url=target_url)
    frame = engine._HTTP_FRAME.get()
    if (type(frame) is not engine._RequestFrame or frame._seal is not engine._FRAME_SEAL
            or not frame.active or frame.completed or frame.phase != "created" or frame.call is not None
            or frame.owner != engine._owner() or frame.client is not client
            or frame.endpoint != endpoint or frame.target_url != target_url or frame.content != content):
        _fail()
    broker = _broker(client)
    with broker.lock:
        if len(broker.pending) >= 8:
            _fail()
        broker.sequence += 1
        call = _Call(client, broker.sequence, endpoint, target_url, client._transport,
            frame, _encoded(request), engine._owner())
        broker.pending[call.sequence] = call
        frame.call, frame.phase = call, "transport"
        return call


def _complete(client, call, result):
    """Called by engine after both HTTP contexts and final deadline checks."""
    if call is None:
        return
    broker = _broker(client)
    with broker.lock:
        frame = call.frame
        if (type(call) is not _Call or broker.pending.get(call.sequence) is not call
                or call.owner != engine._owner() or engine._HTTP_FRAME.get() is not frame
                or not frame.active or frame.completed or frame._seal is not engine._FRAME_SEAL
                or frame.call is not call or frame.phase != "parsed_closed" or frame.decoded is not result):
            _fail()
        broker.pending.pop(call.sequence)
        broker.raw[id(result)] = (result, call, _encoded(result))
        frame.completed, frame.phase = True, "completed"
        while len(broker.raw) > 8:
            broker.raw.pop(next(iter(broker.raw)))


def _abandon(client, call):
    if call is not None:
        broker = _broker(client)
        with broker.lock:
            if broker.pending.get(call.sequence) is call:
                broker.pending.pop(call.sequence)


def _validated(client, endpoint, raw, request, normalized, *, defer_receipt=None):
    """Original parser hook; a replacement _request/public method has no raw."""
    broker = _broker(client)
    with broker.lock:
        found = broker.raw.pop(id(raw), None)
        if found is None:
            return  # DTO paths stay readable, without a publisher capability.
        actual, call, response_json = found
        if (actual is not raw or call.endpoint != endpoint or call.owner != engine._owner()
                or call.frame.active or not call.frame.completed or call.frame.phase != "closed"
                or call.frame.call is not call or response_json != _encoded(raw)
                or _encoded(_normalize_request(request)) != call.request_json):
            _fail()
        exact = _normalize_result(json.loads(call.request_json), raw, defer_receipt)
        if _encoded(exact) != _encoded(normalized):
            _fail()
        key = request["request_id"], hashlib.sha256(call.request_json.encode("utf-8")).hexdigest()
        receipt = _Receipt(call, response_json, _encoded(exact), _encoded(defer_receipt))
        previous = broker.receipts.get(key)
        if previous is None or previous.call.sequence < call.sequence:
            broker.receipts[key] = receipt
        while len(broker.receipts) > 32:
            broker.receipts.pop(next(iter(broker.receipts)))


class HttpReceiptCapture17:
    __slots__ = ("_seal", "_client", "_broker", "_receipt", "_owner", "_active")

    def __init__(self, client, receipt, *, seal):
        if seal is not _SEAL or _CURRENT.get() is not None:
            _fail()
        self._seal, self._client, self._broker = seal, client, _broker(client)
        self._receipt, self._owner, self._active = receipt, engine._owner(), True

    def check(self):
        if (type(self) is not HttpReceiptCapture17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or self._owner != engine._owner()
                or _broker(self._client) is not self._broker):
            _fail()
        call = self._receipt.call
        if (call.owner != self._owner or call.client is not self._client
                or call.target_url != self._client._url or call.transport is not self._client._transport
                or call.frame.active or not call.frame.completed or call.frame.phase != "closed"):
            _fail()
        # Saving a completed remote fact is not a new RPC/paid opportunity.
        # Do not lose the fact merely because a user paused after HTTP cleanup.

    @property
    def request(self):
        self.check()
        return json.loads(self._receipt.call.request_json)

    @property
    def result(self):
        self.check()
        result = json.loads(self._receipt.result_json)
        result["replayed"] = False  # immutable original effect, including server replay
        return result

    @property
    def defer_receipt(self):
        self.check()
        return json.loads(self._receipt.parent_json)

    @property
    def provenance(self):
        self.check()
        call = self._receipt.call
        return {"format_version": 1, "producer": "sync_http", "endpoint": call.endpoint,
            "target_url_sha256": hashlib.sha256(call.target_url.encode("utf-8")).hexdigest(),
            "request_sha256": hashlib.sha256(call.request_json.encode("utf-8")).hexdigest()}


@contextmanager
def capture_receipt(client, *, request_id, request_sha256):
    broker = _broker(client)
    with broker.lock:
        receipt = broker.receipts.get((request_id, request_sha256))
        if receipt is None:
            _fail()
    value = HttpReceiptCapture17(client, receipt, seal=_SEAL)
    token = _CURRENT.set(value)
    try:
        value.check()
        yield value
        value.check()
    finally:
        value._active = False
        _CURRENT.reset(token)
