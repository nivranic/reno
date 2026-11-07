"""Authenticated initial-claim observations from the real sync HTTP path.

These short-lived capabilities establish where an initial Home claim came
from. They grant neither a SQL writer nor current/paid execution admission.
Public response dictionaries, persisted State and a renewed-claim count are
not capabilities. Raw HTTP completion and endpoint validation are separate
steps; a replaced public client method cannot manufacture this evidence.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import threading

from .sync_protocol import SyncError


_SEAL = object()
_FRAME_SEAL = object()
_HTTP_FRAME = ContextVar("reno_initial_source_http_frame", default=None)
_CURRENT = ContextVar("reno_initial_source_claim_capture", default=None)
_ENDPOINTS = frozenset(("heartbeat", "pull", "source/continuity"))


def _fail():
    raise SyncError("Initial source claim proof is required", "SYNC_SOURCE_CLAIM_PROOF_REQUIRED", 409)


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _lease_key(value):
    return value["session_id"], value["lease_token"], value["generation"]


def _live(value):
    from .sync_queue import _stored_time
    time = _stored_time(value)
    return time is not None and time > datetime.now(timezone.utc)


class _RequestFrame:
    """Only the original client request body constructs this sealed scope."""
    __slots__ = ("client", "endpoint", "target_url", "content", "owner", "active", "completed",
                 "phase", "call", "decoded", "_seal", "_observation", "_observation_json")

    def __init__(self, client, endpoint, target_url, content, *, seal):
        if seal is not _FRAME_SEAL:
            _fail()
        self.client, self.endpoint, self.target_url, self.content = client, endpoint, target_url, content
        self.owner, self.active, self.completed, self._seal = _owner(), True, False, seal
        self.phase, self.call, self.decoded = "created", None, None
        self._observation = None
        self._observation_json = None


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
class _Observation:
    call: _Call
    response_json: str
    _frame: object

    @property
    def request(self):
        return json.loads(self.call.request_json)

    @property
    def response(self):
        return json.loads(self.response_json)


class _Broker:
    def __init__(self, client):
        self.client = client
        self.lock = threading.RLock()
        self.process = os.getpid()
        self.sequence = 0
        self.pending = {}
        self.raw = {}
        self.latest = {}
        self.remote_epochs = {}
        self.continuity_heads = {}
        self.continuity_conflicts = set()


def _begin(client, endpoint, content, *, target_url):
    """Private hook after actual outgoing bytes are fixed, before transport."""
    if endpoint not in _ENDPOINTS or content is None:
        return None
    frame = _HTTP_FRAME.get()
    if (type(frame) is not _RequestFrame or frame._seal is not _FRAME_SEAL or not frame.active
            or frame.completed or frame.phase != "created" or frame.call is not None
            or frame.owner != _owner() or frame.client is not client
            or frame.endpoint != endpoint or frame.target_url != target_url or frame.content != content):
        _fail()
    request = json.loads(content)
    if request.get("source", {}).get("handshake", {}).get("migration_version") != 17:
        frame.phase = "transport"
        return None
    broker = client._source_claim_broker
    with broker.lock:
        if broker.process != os.getpid() or len(broker.pending) >= 8:
            _fail()
        broker.sequence += 1
        call = _Call(client, broker.sequence, endpoint, target_url, client._transport, frame, _encoded(request), _owner())
        broker.pending[call.sequence] = call
        frame.call, frame.phase = call, "transport"
        return call


def _complete(client, call, result):
    """Only called after real HTTP/JSON/size/cancellation checks completed."""
    if call is None:
        return
    broker = client._source_claim_broker
    with broker.lock:
        if (broker.pending.get(call.sequence) is not call or call.owner != _owner()
                or _HTTP_FRAME.get() is not call.frame or not call.frame.active
                or call.frame.completed or call.frame._seal is not _FRAME_SEAL
                or call.frame.call is not call or call.frame.phase != "parsed_closed"
                or call.frame.decoded is not result):
            _fail()
        broker.pending.pop(call.sequence)
        # Keep identity as well as frozen bytes; DTO copies cannot be activated.
        broker.raw[id(result)] = (result, _Observation(call, _encoded(result), call.frame))
        call.frame.completed = True
        call.frame.phase = "completed"
        while len(broker.raw) > 8:
            broker.raw.pop(next(iter(broker.raw)))


def _abandon(client, call):
    if call is not None:
        broker = client._source_claim_broker
        with broker.lock:
            if broker.pending.get(call.sequence) is call:
                broker.pending.pop(call.sequence)


def _validated(client, endpoint, result):
    """Endpoint parsers activate only their exact completed raw response."""
    broker = client._source_claim_broker
    with broker.lock:
        raw = broker.raw.pop(id(result), None)
        if raw is None:
            return  # Legacy/custom DTO paths remain readable, without evidence.
        actual, observation = raw
        if (actual is not result or observation.call.endpoint != endpoint
                or not observation.call.frame.completed or observation.call.frame.active
                or observation.call.frame.phase != "closed"
                or observation.call.frame._seal is not _FRAME_SEAL
                or observation.call.frame.call is not observation.call
                or observation.call.frame.decoded is not result
                or observation.call.frame._observation is not None
                or observation.call.owner != _owner() or observation.response_json != _encoded(result)):
            _fail()
        previous = broker.latest.get(endpoint)
        if previous is not None and observation.call.sequence < previous.call.sequence:
            return  # A delayed old call cannot replace a newer observation.
        response = observation.response
        source_key = _encoded(observation.request["source"])
        remote = response["receiver_version"] if endpoint == "source/continuity" else response["recovery"]["stream"]
        remote_key = source_key, remote["library_id"]
        epoch = remote["library_epoch"]
        if epoch < broker.remote_epochs.get(remote_key, epoch):
            return  # Epoch floors are shared by all three endpoint observations.
        if previous is not None and endpoint in {"heartbeat", "pull"}:
            old, new = previous.response["recovery"], observation.response["recovery"]
            old_order = (old["stream"]["library_epoch"], old["generation"], old["revision"])
            new_order = (new["stream"]["library_epoch"], new["generation"], new["revision"])
            if (old["stream"]["library_id"] == new["stream"]["library_id"] and new_order < old_order):
                return
            if (endpoint == "heartbeat" and old["stream"] == new["stream"]
                    and observation.response["lease"]["generation"] < previous.response["lease"]["generation"]):
                return
        if endpoint == "source/continuity" and response["state"] == "current":
            head_key = remote_key, epoch
            old = broker.continuity_heads.get(head_key)
            if old is not None and (old["continuity"] != response["continuity"]
                    or response["head"]["ordinal"] < old["head"]["ordinal"]
                    or response["head"]["ordinal"] == old["head"]["ordinal"] and response["head"]["sha256"] != old["head"]["sha256"]):
                # Contradictory current facilities/heads invalidate snapshots
                # permanently in this client epoch, even if a later reply is
                # shaped like the old one again.
                broker.continuity_conflicts.add(head_key)
                broker.latest.pop(endpoint, None)
                return
            broker.continuity_heads[head_key] = response
        broker.remote_epochs[remote_key] = epoch
        observation.call.frame._observation = observation
        observation.call.frame._observation_json = (observation.call.request_json, observation.response_json)
        broker.latest[endpoint] = observation


class InitialClaimCapture:
    __slots__ = ("_seal", "_client", "_broker", "_source_json", "_pull", "_continuity",
                 "_entry_json", "_owner", "_active")

    def __init__(self, *, client, source, item_id, seal):
        if seal is not _SEAL or _CURRENT.get() is not None or type(item_id) is not int or item_id < 1:
            _fail()
        self._seal, self._client, self._broker = seal, client, client._source_claim_broker
        self._source_json, self._owner, self._active = _encoded(source), _owner(), True
        with self._broker.lock:
            self._pull = self._broker.latest.get("pull")
            self._continuity = self._broker.latest.get("source/continuity")
            if self._pull is None or self._continuity is None:
                _fail()
            claims = [entry for entry in self._pull.response["claims"] if entry["item_id"] == item_id]
            if len(claims) != 1:
                _fail()
            self._entry_json = _encoded(claims[0])

    @property
    def entry(self):
        self.check()
        return json.loads(self._entry_json)

    @property
    def continuity(self):
        self.check()
        return self._continuity.response

    def check(self, *, state=None):
        from .sync_client import SyncClient
        if (self._seal is not _SEAL or not self._active or _CURRENT.get() is not self
                or self._owner != _owner() or type(self._client) is not SyncClient
                or self._client._source_claim_broker is not self._broker
                or self._broker.process != os.getpid()):
            _fail()
        self._client._check_transport()
        if self._client._check_cancelled is not None:
            self._client._check_cancelled()
        with self._broker.lock:
            heartbeat = self._broker.latest.get("heartbeat")
            if (heartbeat is None or self._broker.latest.get("pull") is not self._pull
                    or self._broker.latest.get("source/continuity") is not self._continuity):
                _fail()
            source = json.loads(self._source_json)
            pull_request, pull = self._pull.request, self._pull.response
            heart_request, heart = heartbeat.request, heartbeat.response
            continuity_request, continuity = self._continuity.request, self._continuity.response
            entry = json.loads(self._entry_json)
            head_key = ((self._source_json, continuity["receiver_version"]["library_id"]),
                        continuity["receiver_version"]["library_epoch"])
            if (head_key in self._broker.continuity_conflicts
                    or any(observation.call.target_url != self._client._url
                           or observation.call.transport is not self._client._transport
                           for observation in (self._pull, heartbeat, self._continuity))
                    or any(_encoded(request["source"]) != self._source_json for request in
                    (pull_request, heart_request, continuity_request))
                    or continuity["state"] != "current"
                    or continuity["source_identity"] != {name: source[name] for name in ("library_id", "node_id")}
                    or continuity["target_identity"] != {name: continuity_request["target"][name] for name in ("library_id", "node_id")}
                    or _lease_key(pull_request["lease"]) != _lease_key(heart["lease"])
                    or heart["lease"]["lease_until"] < pull_request["lease"]["lease_until"]
                    or heart["recovery"] != pull["recovery"] or pull["recovery"]["state"] != "running"
                    or pull["recovery"]["stream"] != {name: continuity["receiver_version"][name] for name in ("library_id", "library_epoch")}
                    or not _live(heart["lease"]["lease_until"]) or not _live(entry["lease_until"])):
                _fail()
            if state is not None:
                binding = state["binding"]
                expected = {"target_url": self._client._url,
                    "home_library_id": source["library_id"], "home_node_id": source["node_id"],
                    "target_library_id": continuity["target_identity"]["library_id"],
                    "target_node_id": continuity["target_identity"]["node_id"]}
                if (binding != expected or state["session_id"] != heart["lease"]["session_id"]
                        or state["lease"] is None or _lease_key(state["lease"]) != _lease_key(heart["lease"])
                        or not pull_request["lease"]["lease_until"] <= state["lease"]["lease_until"] <= heart["lease"]["lease_until"]
                        or not _live(state["lease"]["lease_until"]) or state["recovery"] != pull["recovery"]
                        or not any(_encoded(saved) == self._entry_json for saved in state["claims"])):
                    _fail()


@contextmanager
def capture_initial_claim(client, *, source, item_id):
    """Consume an exact client's latest real observations within this scope."""
    from .sync_client import SyncClient
    if type(client) is not SyncClient:
        _fail()
    value = InitialClaimCapture(client=client, source=source, item_id=item_id, seal=_SEAL)
    token = _CURRENT.set(value)
    try:
        value.check()
        yield value
        value.check()
    finally:
        value._active = False
        _CURRENT.reset(token)
