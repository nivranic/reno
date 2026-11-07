"""Exact historical terminal receipts, using only artificial HTTP transport."""
from copy import deepcopy
import json
import uuid

import httpx
import pytest

from tests.test_sync_client import SOURCE, TARGET, LEASE, TOKEN, assert_error, client, ok
from reno import config, sync_agent as agent
from reno.sync_client import SyncClient


PAYLOAD_SHA = "a" * 64
BINDING = {"publication_id": str(uuid.uuid4()), "source_node_id": SOURCE["node_id"],
           "source_version": {"library_id": SOURCE["library_id"], "library_epoch": 2, "kb_revision": 8},
           "recovery_generation": 0,
           "recovery_stream": {"library_id": TARGET["library_id"], "library_epoch": 3}}


def response():
    return {"binding": deepcopy(BINDING), "payload_sha256": PAYLOAD_SHA, "state": "published",
            "receipt": {"publication_id": BINDING["publication_id"],
                        "source_version": deepcopy(BINDING["source_version"]),
                        "local_version": {"library_id": TARGET["library_id"], "library_epoch": 3, "kb_revision": 12},
                        "committed_at": "2026-10-01T01:00:00.000Z", "replayed": True}}


def test_receipt_posts_only_original_binding_hash_and_current_session():
    seen = []

    def handler(request):
        assert request.url.path == "/reno/api/sync/uploads/receipt"
        seen.append(json.loads(request.content))
        return ok(response())

    result = client(handler).publication_receipt(SOURCE, LEASE, binding=BINDING, payload_sha256=PAYLOAD_SHA)
    assert result == response()
    assert seen == [{"source": SOURCE, "lease": LEASE, "binding": BINDING, "payload_sha256": PAYLOAD_SHA}]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(extra=TOKEN),
    lambda value: value.pop("binding"),
    lambda value: value.update(state="abandoned"),
    lambda value: value.update(payload_sha256="b" * 64),
    lambda value: value["binding"].update(publication_id=str(uuid.uuid4())),
    lambda value: value["binding"].update(recovery_generation=1),
    lambda value: value["binding"]["recovery_stream"].update(library_epoch=4),
    lambda value: value["receipt"].update(extra=TOKEN),
    lambda value: value["receipt"].pop("committed_at"),
    lambda value: value["receipt"].update(publication_id=str(uuid.uuid4())),
    lambda value: value["receipt"]["source_version"].update(kb_revision=9),
    lambda value: value["receipt"]["local_version"].update(library_id=str(uuid.uuid4())),
    lambda value: value["receipt"]["local_version"].update(library_epoch=4),
    lambda value: value["receipt"]["local_version"].update(kb_revision=True),
    lambda value: value["receipt"].update(replayed=1),
    lambda value: value["receipt"].update(committed_at="invalid"),
])
def test_receipt_rejects_wrong_or_open_terminal_evidence(mutation):
    value = response()
    mutation(value)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE",
        lambda: client(lambda _: ok(value)).publication_receipt(SOURCE, LEASE, binding=BINDING, payload_sha256=PAYLOAD_SHA))


@pytest.mark.parametrize("payload_sha", [None, "", "a" * 63, "A" * 64, True, TOKEN])
def test_invalid_payload_hash_never_reaches_network(payload_sha):
    item = client(lambda _: pytest.fail("invalid request cannot be transmitted"))
    assert_error("SYNC_CLIENT_INVALID_REQUEST",
                 lambda: item.publication_receipt(SOURCE, LEASE, binding=BINDING, payload_sha256=payload_sha))


@pytest.mark.parametrize("field", ["source_node_id", "source_version"])
def test_other_source_receipt_request_never_reaches_network(field):
    binding = deepcopy(BINDING)
    if field == "source_node_id":
        binding[field] = str(uuid.uuid4())
    else:
        binding[field]["library_id"] = str(uuid.uuid4())
    item = client(lambda _: pytest.fail("foreign request cannot be transmitted"))
    assert_error("SYNC_CLIENT_INVALID_REQUEST",
                 lambda: item.publication_receipt(SOURCE, LEASE, binding=binding, payload_sha256=PAYLOAD_SHA))


def test_published_remote_code_alone_remains_error_not_receipt():
    item = client(lambda _: httpx.Response(409, json={"code": "SYNC_UPLOAD_PUBLISHED", "detail": TOKEN}))
    assert_error("SYNC_UPLOAD_PUBLISHED",
                 lambda: item.publication_receipt(SOURCE, LEASE, binding=BINDING, payload_sha256=PAYLOAD_SHA))


def sender_options():
    config.set_local({"sync_enabled": True, "sync_cloud_url": "https://sync.example.test/reno",
                      "sync_target_library_id": TARGET["library_id"], "sync_target_node_id": TARGET["node_id"],
                      "sync_token": "artificial-old-peer-token"})
    return agent.settings()


def external_config_replace(updates):
    path = config.local_path()
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(updates)
    replacement = path.with_suffix(".sender-fixture.tmp")
    replacement.write_text(json.dumps(value), encoding="utf-8")
    replacement.replace(path)


@pytest.mark.parametrize("peer_update", [
    {"sync_cloud_url": "https://other-peer.example.test/reno"},
    {"sync_target_library_id": str(uuid.uuid4())},
    {"sync_target_node_id": str(uuid.uuid4())},
])
def test_fresh_peer_and_token_snapshot_blocks_external_repairing_before_header(peer_update):
    options = sender_options()
    config.get()  # Keep an intentionally stale application cache.
    external_config_replace({**peer_update, "sync_token": "artificial-new-peer-token"})
    requests = []
    item = SyncClient(options["url"], token_provider=lambda: agent._sender_token(options),
                      check_cancelled=lambda: agent._assert_peer_configuration(options),
                      transport=httpx.MockTransport(lambda request: requests.append(request) or ok({"target": TARGET})))
    assert_error("SYNC_CLIENT_CANCELLED", item.handshake)
    assert not requests


def test_same_peer_external_token_rotation_is_fresh_and_config_lock_is_not_held_on_network():
    options = sender_options()
    config.get()
    tokens = []

    def handler(request):
        assert config._config_lock.acquire(blocking=False), "network must not own the configuration lock"
        config._config_lock.release()
        tokens.append(request.headers["x-sync-token"])
        return ok({"target": TARGET})

    item = SyncClient(options["url"], token_provider=lambda: agent._sender_token(options),
                      check_cancelled=lambda: agent._assert_peer_configuration(options), transport=httpx.MockTransport(handler))
    assert item.handshake() == TARGET
    external_config_replace({"sync_token": "artificial-rotated-same-peer-token"})
    assert item.handshake() == TARGET
    assert tokens == ["artificial-old-peer-token", "artificial-rotated-same-peer-token"]


def test_configuration_switch_between_peer_check_and_token_lookup_never_exports_new_credential(monkeypatch):
    options = sender_options()
    checked = agent._assert_peer_configuration
    requests = []
    acquired_tokens = []

    def switch_after_check(value, *, snapshot=None):
        checked(value, snapshot=snapshot)
        if snapshot is not None:
            external_config_replace({"sync_target_node_id": str(uuid.uuid4()), "sync_token": "artificial-new-peer-token"})

    monkeypatch.setattr(agent, "_assert_peer_configuration", switch_after_check)

    def token():
        value = agent._sender_token(options)
        acquired_tokens.append(value)
        return value

    item = SyncClient(options["url"], token_provider=token,
                      check_cancelled=lambda: agent._assert_peer_configuration(options),
                      transport=httpx.MockTransport(lambda request: requests.append(request) or ok({"target": TARGET})))
    assert_error("SYNC_CLIENT_CANCELLED", item.handshake)
    assert not requests
    assert acquired_tokens == ["artificial-old-peer-token"]


@pytest.mark.parametrize("path", [
    ("recovery_generation",), ("recovery_stream", "library_epoch"),
    ("source_version", "library_epoch"), ("source_version", "kb_revision"),
])
@pytest.mark.parametrize("wrong_type", [True, 1.0])
def test_abandon_echo_rejects_boolean_and_float_disguised_as_integer(path, wrong_type):
    binding = deepcopy(BINDING)
    binding["recovery_generation"] = 1
    binding["recovery_stream"]["library_epoch"] = 1
    binding["source_version"].update(library_epoch=1, kb_revision=1)
    operation_id = str(uuid.uuid4())
    result = {"binding": deepcopy(binding), "operation_id": operation_id, "state": "abandoned"}
    parent = result["binding"]
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = wrong_type
    assert result == {"binding": binding, "operation_id": operation_id, "state": "abandoned"}
    assert_error("SYNC_CLIENT_INVALID_RESPONSE",
        lambda: client(lambda _: ok(result)).abandon_uploads(SOURCE, LEASE, binding=binding, operation_id=operation_id))
