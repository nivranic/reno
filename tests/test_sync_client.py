"""Hostile wire input, local sockets and artificial media; no cloud/model calls."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import threading
import uuid

import httpx
import pytest

from reno import config, db, migrations, review, sync_events, sync_queue, sync_protocol
from reno.sync_client import CHUNK_BYTES, MAX_CONTROL_BYTES, SyncClient, SyncClientError
from tests.test_sync_transfer import env, CONTENT, VIDEO, URL


TOKEN = "artificial-client-token"
SOURCE = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4()), "handshake": {
    "protocol_version": sync_protocol.PROTOCOL_VERSION, "migration_version": migrations.SCHEMA_VERSION, "core_schema_sha256": "a" * 64}}
TARGET = {**SOURCE, "library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4())}
LEASE = {"session_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4()), "generation": 1,
         "lease_until": "2099-01-01T00:00:00.000Z"}
CLAIM = {"item_id": 1, "lease_token": str(uuid.uuid4()), "lease_generation": 1}
RECOVERY = {"generation": 0, "revision": 0, "state": "running", "source_floor": None,
            "stream": {"library_id": TARGET["library_id"], "library_epoch": 1}}
_UPLOAD_PUBLICATION_ID = str(uuid.uuid4())


def media_intent(descriptor, *, source=SOURCE):
    return {"publication_id": _UPLOAD_PUBLICATION_ID, "source_node_id": source["node_id"],
            "source_version": {"library_id": source["library_id"], "library_epoch": 1, "kb_revision": 0},
            "recovery_generation": 0, "recovery_stream": dict(RECOVERY["stream"]), "descriptor": dict(descriptor)}


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("revision"), lambda value: value.update(secret=TOKEN),
    lambda value: value.update(generation=True), lambda value: value.update(generation=-1),
    lambda value: value.update(revision=True), lambda value: value.update(revision=2**63),
    lambda value: value.update(state="resumed"),
    lambda value: value.pop("stream"), lambda value: value.update(stream=None),
    lambda value: value["stream"].update(library_epoch=True),
    lambda value: value["stream"].update(library_epoch=0),
    lambda value: value.update(source_floor={"library_id": TARGET["library_id"], "library_epoch": 1, "kb_revision": 1}),
])
@pytest.mark.parametrize("operation", ["heartbeat", "pull"])
def test_recovery_control_is_closed_and_bound_to_source(mutation, operation):
    recovery = deepcopy(RECOVERY)
    mutation(recovery)
    payload = {"lease": LEASE, "renewed_claims": 0, "recovery": recovery} if operation == "heartbeat" else {
        **empty_page(), "recovery": recovery}
    item = client(lambda _: ok(payload))
    action = lambda: item.heartbeat(SOURCE, LEASE["session_id"]) if operation == "heartbeat" else item.pull(SOURCE, LEASE)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", action)


def client(handler, **kwargs):
    return SyncClient("https://sync.example.test/reno", token_provider=lambda: TOKEN,
                      transport=httpx.MockTransport(handler), **kwargs)


def ok(value):
    return httpx.Response(200, json=value)


def assert_error(code, action):
    with pytest.raises(SyncClientError) as caught:
        action()
    assert caught.value.code == code
    assert TOKEN not in str(caught.value)
    assert caught.value.__suppress_context__ or caught.value.__context__ is None
    return caught.value


def test_prefix_port_fresh_token_and_no_database_access(monkeypatch):
    tokens, seen = iter((TOKEN, "rotated-artificial-token")), []

    def handler(request):
        seen.append((str(request.url), request.headers["x-sync-token"]))
        assert request.headers["accept-encoding"] == "identity"
        return ok({"target": TARGET})

    monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("client must not inspect a local binding"))
    item = SyncClient("https://sync.example.test:8443/reno/v1/", token_provider=lambda: next(tokens),
                      transport=httpx.MockTransport(handler))
    assert item.handshake() == TARGET
    assert item.handshake() == TARGET
    assert seen == [("https://sync.example.test:8443/reno/v1/api/sync/handshake", TOKEN),
                    ("https://sync.example.test:8443/reno/v1/api/sync/handshake", "rotated-artificial-token")]
    assert TOKEN not in repr(item) and "token" not in item.__dict__


@pytest.mark.parametrize("url", ["", None, "ftp://host", "https://user:pass@host", "https://host?token=secret",
    "https://host?", "https://host#", "https://host/a/../b", "https://host/./a", "https://host/%2e%2e/a",
    "https://host/%252e/a", "https://host\\evil", "https://host/a//b", "https://host//", "https://host:",
    "https://host:0", "https://host:65536", "https://host:abc", "https://bad_host", "https://host\n",
    " https://host", "https://host/a b", "https://[::1%25x]", "https://host/a\x7f", "https:///no-host"])
def test_invalid_urls_fail_before_credentials_or_transport(url):
    assert_error("SYNC_CLIENT_INVALID_URL", lambda: SyncClient(url, token_provider=lambda: pytest.fail("no token needed")))


@pytest.mark.parametrize("token", [None, "", 1, "contains space", "x\r\ny", "中文", "x" * 4097])
def test_invalid_credentials_never_reach_transport(token):
    item = SyncClient("https://sync.example.test", token_provider=lambda: token,
                      transport=httpx.MockTransport(lambda _: pytest.fail("invalid token must not be sent")))
    assert_error("SYNC_CLIENT_CREDENTIAL", item.handshake)


def test_provider_exception_is_sanitized():
    def provider():
        raise OSError(TOKEN + " /secret/config/path")
    item = SyncClient("https://sync.example.test", token_provider=provider)
    assert_error("SYNC_CLIENT_CREDENTIAL", item.handshake)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9999", "http://localhost:9999", "http://[::1]:9999",
                                "http://192.168.1.1", "http://sync.example.test"])
def test_http_requires_explicit_qa_configuration(url):
    assert_error("SYNC_CLIENT_INSECURE_TRANSPORT", lambda: SyncClient(url, token_provider=lambda: TOKEN))


def test_http_flag_is_rechecked_each_request_and_needs_path_isolation(monkeypatch):
    config.set_local({"sync_allow_insecure_test_transport": True})
    assert config.is_isolated_qa()
    seen = []
    item = SyncClient("http://localhost:9999/reno", token_provider=lambda: TOKEN,
                      transport=httpx.MockTransport(lambda r: (seen.append(str(r.url)) or ok({"target": TARGET}))))
    assert item.handshake() == TARGET
    assert seen == ["http://127.0.0.1:9999/reno/api/sync/handshake"]
    config.set_local({"sync_allow_insecure_test_transport": False})
    assert_error("SYNC_CLIENT_INSECURE_TRANSPORT", item.handshake)
    config.set_local({"sync_allow_insecure_test_transport": True})
    monkeypatch.delenv("RENO_QA_ROOT")
    assert_error("SYNC_CLIENT_INSECURE_TRANSPORT", item.handshake)
    assert len(seen) == 1


@pytest.mark.parametrize("host", ["sync.example.test", "localhost.example.test", "192.168.1.1", "[::]", "0.0.0.0"])
def test_http_qa_flag_never_allows_nonloopback(host):
    config.set_local({"sync_allow_insecure_test_transport": True})
    assert_error("SYNC_CLIENT_INSECURE_TRANSPORT", lambda: SyncClient(f"http://{host}", token_provider=lambda: TOKEN))


@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308, 399])
def test_redirect_is_rejected_without_contacting_location(status):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://attacker.invalid/steal"})
    assert_error("SYNC_CLIENT_REDIRECT", client(handler).handshake)
    assert seen == ["https://sync.example.test/reno/api/sync/handshake"]


@pytest.mark.parametrize("exception, expected", [
    (httpx.ConnectError("secret URL " + TOKEN), "NETWORK"),
    (httpx.ReadError("secret URL " + TOKEN), "NETWORK"),
    (httpx.ReadTimeout("secret URL " + TOKEN), "TIMEOUT"),
    (ssl.SSLCertVerificationError("secret URL " + TOKEN), "TLS"),
    (RuntimeError("secret URL " + TOKEN), "NETWORK"),
])
def test_transport_exceptions_are_sanitized(exception, expected):
    def handler(_):
        raise exception
    assert_error("SYNC_CLIENT_" + expected, client(handler).handshake)


def test_nested_tls_error_is_classified_without_message_matching():
    def handler(_):
        try:
            raise ssl.SSLCertVerificationError("private certificate path")
        except ssl.SSLCertVerificationError as error:
            raise httpx.ConnectError(TOKEN) from error
    assert_error("SYNC_CLIENT_TLS", client(handler).handshake)


@pytest.mark.parametrize("payload", [b'{"target":{},"target":{}}', b'{"target":NaN}', b'[]', b'null',
                                    b'"string"', b'{broken', b'{"target":"\xff"}', b'{"target":{}}'])
def test_invalid_json_and_handshake_shape_are_rejected(payload):
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", client(lambda _: httpx.Response(200, content=payload,
                  headers={"Content-Type": "application/json"})).handshake)


@pytest.mark.parametrize("mutation", [lambda x: x.update(secret=TOKEN),
    lambda x: x.update(library_id="not-uuid"), lambda x: x["handshake"].update(protocol_version=True),
    lambda x: x["handshake"].update(core_schema_sha256="x" * 64),
    lambda x: x["handshake"].update(extra="unknown")])
def test_invalid_identity_fields_are_rejected(mutation):
    target = deepcopy(TARGET)
    mutation(target)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", client(lambda _: ok({"target": target})).handshake)


class ByteStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.read = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("headers, chunks, expected", [
    ({"Content-Length": str(MAX_CONTROL_BYTES + 1)}, [b"{}"], "TOO_LARGE"),
    ({}, [b"x" * (MAX_CONTROL_BYTES // 2), b"x" * MAX_CONTROL_BYTES, b"never read"], "TOO_LARGE"),
    ({"Content-Length": "-1"}, [b"{}"], "INVALID_RESPONSE"),
    ({"Content-Length": "3"}, [b"{}"], "INVALID_RESPONSE"),
    ({"Content-Encoding": "gzip"}, [b"{}"], "INVALID_RESPONSE"),
    ({"Content-Type": "text/html"}, [b"{}"], "INVALID_RESPONSE"),
])
def test_response_headers_stream_budget_and_close(headers, chunks, expected):
    stream = ByteStream(chunks)
    response = httpx.Response(200, headers={"Content-Type": "application/json", **headers}, stream=stream)
    assert_error("SYNC_CLIENT_" + expected, client(lambda _: response).handshake)
    assert stream.closed
    assert stream.read <= 2


@pytest.mark.parametrize("header", ["Content-Type", "Content-Length", "Content-Encoding"])
def test_duplicate_response_headers_are_rejected(header):
    pairs = [("Content-Type", "application/json")]
    value = {"Content-Type": "application/json", "Content-Length": "2", "Content-Encoding": "identity"}[header]
    if header != "Content-Type":
        pairs.append((header, value))
    pairs.append((header, value))
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", client(lambda _: httpx.Response(200, content=b"{}", headers=pairs)).handshake)


@pytest.mark.parametrize("status, remote_code, expected", [(409, "SYNC_LEASE_EXPIRED", "SYNC_LEASE_EXPIRED"),
    (409, "SYNC_CLAIM_SOURCE_MISMATCH", "SYNC_CLAIM_SOURCE_MISMATCH"),
    (401, "SYNC_UNAUTHORIZED", "SYNC_UNAUTHORIZED"), (403, "unknown-secret", "SYNC_CLIENT_AUTH"),
    (500, "unknown-secret", "SYNC_CLIENT_REMOTE")])
def test_only_closed_remote_error_codes_survive(status, remote_code, expected):
    error = assert_error(expected, client(lambda _: httpx.Response(status, json={
        "code": remote_code, "detail": TOKEN + " /secret/config/path"})).handshake)
    assert "unknown-secret" not in str(error) and "config/path" not in str(error)


@pytest.mark.parametrize("raw", [b'{"code":"SYNC_LEASE_EXPIRED","code":"SYNC_UNAUTHORIZED"}',
                                 b'{"code":"SYNC_LEASE_EXPIRED","detail":NaN}', b'[]',
                                 b'{"code":{},"detail":"hidden"}', b'{"code":"SYNC_BUSY","extra":true}'])
def test_remote_error_json_is_still_strict(raw):
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", client(lambda _: httpx.Response(409, content=raw,
                 headers={"Content-Type": "application/json"})).handshake)


@pytest.mark.parametrize("status", [201, 202, 204, 299])
def test_only_expected_success_status_is_accepted(status):
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", client(lambda _: httpx.Response(status, json={"target": TARGET})).handshake)


@pytest.mark.parametrize("timeout", [0, -1, True, "30", None, float("inf"), float("nan"), 3601, 10**1000])
def test_timeout_must_be_a_finite_positive_bound(timeout):
    assert_error("SYNC_CLIENT_INVALID_REQUEST", lambda: client(lambda _: ok({}), timeout=timeout))


def test_certificate_validation_and_hostname_verification_are_enabled(monkeypatch):
    real_client, seen = httpx.Client, []
    def inspect(*args, **kwargs):
        seen.append(kwargs)
        return real_client(*args, **kwargs)
    monkeypatch.setattr(httpx, "Client", inspect)
    assert client(lambda _: ok({"target": TARGET})).handshake() == TARGET
    assert seen[0]["verify"] is True
    assert seen[0]["trust_env"] is False and seen[0]["follow_redirects"] is False


def empty_page():
    return {"events": {"events": [], "next_cursor": 0, "high_watermark": 0, "has_more": False,
                       "stream": {"library_id": TARGET["library_id"], "library_epoch": 1}}, "claims": [],
            "recovery": deepcopy(RECOVERY)}


@pytest.mark.parametrize("mutate", [lambda x: x.update(unknown=1),
    lambda x: x["events"].update(next_cursor=True), lambda x: x["events"].update(next_cursor=1),
    lambda x: x["events"].update(high_watermark=-1), lambda x: x["events"].update(has_more=True),
    lambda x: x["events"]["stream"].update(library_epoch=True),
    lambda x: x["events"].update(events=[{}]), lambda x: x.update(claims=[{}])])
def test_pull_rejects_malicious_cursor_stream_events_and_claims(mutate):
    value = empty_page()
    mutate(value)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(value)).pull(SOURCE, LEASE))


def test_pull_checks_continuation_identity_and_exact_high_watermark():
    value = empty_page()
    value["events"].update(next_cursor=4, high_watermark=4)
    item = client(lambda _: ok(value))
    expected_stream = value["events"]["stream"]
    assert item.pull(SOURCE, LEASE, after=1, through=4, stream=expected_stream) == value
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: item.pull(SOURCE, LEASE, after=1, through=3, stream=expected_stream))
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: item.pull(SOURCE, LEASE, stream={**expected_stream, "library_epoch": 2}))


@pytest.mark.parametrize("url", ["file:///private", "https://attacker.invalid/video", "http://127.0.0.1/video",
                                "https://user:pass@www.bilibili.com/video/test"])
def test_pull_never_accepts_arbitrary_claim_urls(url):
    value = empty_page()
    value["claims"] = [{"item_id": 1, "url": url, "attempts": 1, "lease_until": LEASE["lease_until"], "claim": CLAIM}]
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(value)).pull(SOURCE, LEASE, claim_limit=1))


def test_outgoing_inputs_are_validated_before_network():
    item = client(lambda _: pytest.fail("bad input must not reach transport"))
    actions = [lambda: item.heartbeat({**SOURCE, "private": "x"}, LEASE["session_id"]),
               lambda: item.heartbeat(SOURCE, "not-session"),
               lambda: item.heartbeat(SOURCE, LEASE["session_id"], metrics={"secret": TOKEN}),
               lambda: item.pull(SOURCE, LEASE, after=1), lambda: item.pull(SOURCE, LEASE, limit=True),
               lambda: item.pull(SOURCE, LEASE, claim_limit=51), lambda: item.push_events(SOURCE, LEASE, [{}]),
               lambda: item.fail(SOURCE, LEASE, CLAIM, TOKEN, recovery_stream=RECOVERY["stream"])]
    for action in actions:
        assert_error("SYNC_CLIENT_INVALID_REQUEST", action)


def test_heartbeat_rejects_unrelated_session_or_renewal_generation():
    value = {"lease": deepcopy(LEASE), "renewed_claims": 0, "recovery": deepcopy(RECOVERY)}
    item = client(lambda _: ok(value))
    assert item.heartbeat(SOURCE, LEASE["session_id"]) == value
    value["lease"]["generation"] = 2
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: item.heartbeat(SOURCE, LEASE["session_id"], LEASE))
    value["lease"]["session_id"] = str(uuid.uuid4())
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: item.heartbeat(SOURCE, LEASE["session_id"]))


@pytest.mark.parametrize("result", [{"received": 1, "inserted": 1, "duplicates": 0, "projection_changed": False},
    {"received": 0, "inserted": 0, "duplicates": 0, "projection_changed": 0},
    {"received": False, "inserted": 0, "duplicates": 0, "projection_changed": False}])
def test_event_merge_receipt_rejects_false_counts_and_boolean_coercion(result):
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(result)).push_events(SOURCE, LEASE, []))


@pytest.mark.parametrize("mutation", [lambda x: x.update(item_id=True), lambda x: x.update(item_id=2),
    lambda x: x.update(status="done"), lambda x: x.update(error_class="auth_failed"),
    lambda x: x.update(retry_at=None), lambda x: x.update(replayed="false"), lambda x: x.update(attempts=6)])
def test_failure_receipt_must_correspond_to_claim_and_error(mutation):
    result = {"item_id": 1, "status": "retry_wait", "attempts": 1, "retry_at": "2099-01-01T00:00:00.000Z",
              "error_class": "network", "replayed": False}
    mutation(result)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(result)).fail(SOURCE, LEASE, CLAIM, "network", recovery_stream=RECOVERY["stream"]))


@pytest.mark.parametrize("kind", ["snapshot", "fail"])
def test_knowledge_mutations_send_original_recovery_namespace(kind):
    seen = []
    version = {"library_id": SOURCE["library_id"], "library_epoch": 1, "kb_revision": 0}
    snapshot = {"publication_id": str(uuid.uuid4()), "version": version, "videos": [],
                "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    result = ({"publication_id": snapshot["publication_id"], "source_version": version,
               "local_version": {**version, "library_id": TARGET["library_id"]},
               "committed_at": "2026-01-01T00:00:00.000Z", "replayed": False} if kind == "snapshot" else {
                   "item_id": 1, "status": "retry_wait", "attempts": 1, "retry_at": "2099-01-01T00:00:00.000Z",
                   "error_class": "network", "replayed": False})

    def handler(request):
        seen.append(json.loads(request.content))
        return ok(result)

    item = client(handler)
    arguments = {"recovery_generation": 7, "recovery_stream": {**RECOVERY["stream"], "library_epoch": 3}}
    if kind == "snapshot":
        assert item.push_snapshot(SOURCE, LEASE, snapshot, [], **arguments) == result
    else:
        assert item.fail(SOURCE, LEASE, CLAIM, "network", **arguments) == result
    assert seen[0]["recovery_stream"] == arguments["recovery_stream"] and seen[0]["recovery_generation"] == 7


@pytest.mark.parametrize("mutation", [lambda x: x.update(publication_id=str(uuid.uuid4())),
    lambda x: x["source_version"].update(kb_revision=2), lambda x: x["local_version"].update(library_id=SOURCE["library_id"]),
    lambda x: x.update(committed_at="invalid"), lambda x: x.update(replayed=0), lambda x: x.update(secret="x")])
def test_snapshot_receipt_is_bound_to_exact_publication_and_version(mutation):
    version = {"library_id": SOURCE["library_id"], "library_epoch": 1, "kb_revision": 0}
    snapshot = {"publication_id": str(uuid.uuid4()), "version": version, "videos": [],
                "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    result = {"publication_id": snapshot["publication_id"], "source_version": dict(version),
              "local_version": {**version, "library_id": TARGET["library_id"]},
              "committed_at": "2026-01-01T00:00:00.000Z", "replayed": False}
    mutation(result)
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(result)).push_snapshot(SOURCE, LEASE, snapshot, [], recovery_stream=RECOVERY["stream"]))


@pytest.fixture()
def media_file(tmp_path):
    content = b"synthetic bytes" * (CHUNK_BYTES // 3)
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(content)
    descriptor = {"video_id": VIDEO, "sha256": hashlib.sha256(content).hexdigest(),
                  "byte_size": len(content), "extension": "mp4"}
    return path, descriptor, content


def media_receipt(descriptor):
    return {"storage_key": f"sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}",
            "sha256": descriptor["sha256"], "published": False}


def test_prepare_sends_complete_large_manifest_without_control_body_truncation(media_file):
    from reno import sync_upload_pins
    _, descriptor, _ = media_file
    intent = media_intent(descriptor)
    binding = {k: intent[k] for k in sync_upload_pins.BINDING_FIELDS}
    descriptors = [{**descriptor, "video_id": f"Video{i:04d}" + "x" * 90} for i in range(400)]

    def handler(request):
        assert request.url.path.endswith("/uploads/prepare")
        assert len(request.content) > MAX_CONTROL_BYTES
        body = json.loads(request.content)
        assert body["binding"] == binding and body["descriptors"] == descriptors
        return ok({"binding": binding, "state": "reserved", "media": [
            {"descriptor": entry, "pin_id": sync_upload_pins.intent_id({**binding, "descriptor": entry}), "state": "reserved"}
            for entry in descriptors]})

    result = client(handler).prepare_uploads(SOURCE, LEASE, binding=binding, descriptors=descriptors)
    assert len(result["media"]) == len(descriptors)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "different_pin", "wrong_binding", "unrecognized_state", "published_with_media"])
def test_prepare_response_is_exact_and_bound_to_every_descriptor(media_file, damage):
    from reno import sync_upload_pins
    _, descriptor, _ = media_file
    intent = media_intent(descriptor)
    binding = {k: intent[k] for k in sync_upload_pins.BINDING_FIELDS}
    item = {"descriptor": descriptor, "pin_id": sync_upload_pins.intent_id(intent), "state": "reserved"}
    response = {"binding": deepcopy(binding), "state": "reserved", "media": [deepcopy(item)]}
    if damage == "missing":
        response["media"] = []
    elif damage == "duplicate":
        response["media"].append(deepcopy(item))
    elif damage == "different_pin":
        response["media"][0]["pin_id"] = "pin_" + "0" * 64
    elif damage == "wrong_binding":
        response["binding"]["source_version"]["kb_revision"] += 1
    elif damage == "unrecognized_state":
        response["media"][0]["state"] = "assumed_ready"
    else:
        response["state"] = "published"
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(response)).prepare_uploads(
        SOURCE, LEASE, binding=binding, descriptors=[descriptor]))


def test_per_file_verify_has_separate_bounded_deadline_and_identity_response(media_file):
    from reno import sync_upload_pins
    from reno.sync_client import MAX_VERIFY_TOTAL_SECONDS
    _, descriptor, _ = media_file
    intent = media_intent(descriptor)

    def handler(request):
        assert request.url.path.endswith("/uploads/verify")
        assert json.loads(request.content)["intent"] == intent
        assert request.extensions["timeout"]["read"] == MAX_VERIFY_TOTAL_SECONDS
        assert 0 < request.extensions["reno_sync_deadline"].remaining() <= MAX_VERIFY_TOTAL_SECONDS
        return ok({"pin_id": sync_upload_pins.intent_id(intent), "stored": True})

    assert client(handler).verify_upload(SOURCE, LEASE, intent=intent)["stored"] is True
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok({"pin_id": "other", "stored": True})).verify_upload(
        SOURCE, LEASE, intent=intent))
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok({"pin_id": sync_upload_pins.intent_id(intent), "stored": 1})).verify_upload(
        SOURCE, LEASE, intent=intent))


def test_explicit_abandon_request_is_bound_and_has_no_automatic_sender_side_effect(media_file):
    from reno import sync_upload_pins
    _, descriptor, _ = media_file
    binding = {k: media_intent(descriptor)[k] for k in sync_upload_pins.BINDING_FIELDS}
    operation_id = str(uuid.uuid4())
    def handler(request):
        assert request.url.path.endswith("/uploads/abandon")
        assert json.loads(request.content) == {"source": SOURCE, "lease": LEASE, "binding": binding, "operation_id": operation_id}
        return ok({"binding": binding, "operation_id": operation_id, "state": "abandoned"})
    assert client(handler).abandon_uploads(SOURCE, LEASE, binding=binding, operation_id=operation_id)["state"] == "abandoned"


def test_media_header_is_canonical_file_bytes_are_streamed_and_receipt_checked(media_file):
    path, descriptor, content = media_file
    sizes, metadata = [], []

    class Inspect(httpx.BaseTransport):
        def handle_request(self, request):
            assert request.headers["content-length"] == str(len(content))
            header = request.headers["x-sync-media"]
            raw = base64.b64decode(header, altchars=b"-_", validate=True)
            assert base64.urlsafe_b64encode(raw).decode() == header
            metadata.append(json.loads(raw))
            chunks = []
            for chunk in request.stream:
                sizes.append(len(chunk))
                chunks.append(chunk)
            assert b"".join(chunks) == content
            return ok(media_receipt(descriptor))

    item = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=Inspect())
    assert item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)) == media_receipt(descriptor)
    assert len(sizes) > 1 and max(sizes) <= CHUNK_BYTES
    assert metadata == [{"source": SOURCE, "lease": LEASE, "intent": media_intent(descriptor)}]


@pytest.mark.parametrize("change", ["sha", "size", "missing", "directory"])
def test_media_is_checked_before_upload(media_file, change):
    path, descriptor, _ = media_file
    if change == "sha":
        descriptor["sha256"] = "0" * 64
    elif change == "size":
        descriptor["byte_size"] += 1
    elif change == "missing":
        path = path.with_name("absent.mp4")
    else:
        path = path.parent
    item = client(lambda _: pytest.fail("invalid local media must not upload"))
    assert_error("SYNC_CLIENT_MEDIA", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


def test_media_mutation_during_stream_is_rejected(media_file):
    path, descriptor, _ = media_file

    class Mutating(httpx.BaseTransport):
        def handle_request(self, request):
            iterator = iter(request.stream)
            next(iterator)
            with path.open("r+b") as handle:
                handle.seek(CHUNK_BYTES)
                handle.write(b"changed")
            list(iterator)
            pytest.fail("mutated file must stop upload")

    item = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=Mutating())
    assert_error("SYNC_CLIENT_MEDIA", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


def test_media_mutation_after_last_chunk_is_rejected_before_success(media_file):
    path, descriptor, content = media_file
    def handler(_):
        path.write_bytes(b"changed" + content[7:])
        return ok(media_receipt(descriptor))
    assert_error("SYNC_CLIENT_MEDIA", lambda: client(handler).upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


@pytest.mark.parametrize("offset", [0, -7])
@pytest.mark.parametrize("tiny", [False, True])
def test_media_post_send_rewrite_with_identical_stat_times_is_rejected(media_file, monkeypatch, offset, tiny):
    import os
    from types import SimpleNamespace
    from reno import sync_client

    path, descriptor, content = media_file
    if tiny:
        # Also cover files small enough to fit entirely in an I/O read buffer.
        content = content[:31]
        path.write_bytes(content)
        descriptor = {**descriptor, "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    original_lstat, original_fstat = Path.lstat, os.fstat
    path_stat = path.lstat()
    with path.open("rb") as stream:
        handle_stat = os.fstat(stream.fileno())

    def stable_times(value, original):
        # Model a filesystem where both writes land in the same timestamp tick.
        # Real device, inode, size and bytes remain observable; only timestamps
        # are held equal so this regression cannot pass due to timer precision.
        return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino, st_size=value.st_size,
            st_mode=value.st_mode, st_mtime_ns=original.st_mtime_ns, st_ctime_ns=original.st_ctime_ns)

    monkeypatch.setattr(Path, "lstat", lambda target: stable_times(original_lstat(target), path_stat)
                        if target == path else original_lstat(target))
    monkeypatch.setattr(sync_client, "os", SimpleNamespace(
        fstat=lambda fd: stable_times(original_fstat(fd), handle_stat)))

    def handler(request):
        assert request.content == content  # The server already received the old bytes.
        changed = bytearray(content)
        start = offset if offset >= 0 else len(changed) + offset
        changed[start:start + 7] = b"changed"
        path.write_bytes(changed)
        os.utime(path, ns=(path_stat.st_atime_ns, path_stat.st_mtime_ns))
        return ok(media_receipt(descriptor))

    assert_error("SYNC_CLIENT_MEDIA", lambda: client(handler).upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


@pytest.mark.parametrize("failure", ["TIMEOUT", "CANCELLED"])
def test_media_final_integrity_scan_keeps_original_deadline_and_cancellation(media_file, monkeypatch, failure):
    from reno import sync_client, sync_client_transport

    path, descriptor, _ = media_file
    original_open = Path.open
    clock, response_seen, final_reads, stopped = [1000.0], [False], [0], [False]
    monkeypatch.setattr(sync_client_transport, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sync_client, "MAX_MEDIA_TOTAL_SECONDS", 0.25)

    class ObservedReader:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            chunk = self.stream.read(size)
            if response_seen[0]:
                final_reads[0] += 1
                if failure == "TIMEOUT":
                    clock[0] += 0.15
                else:
                    stopped[0] = True
            return chunk

    def open_file(target, mode="r", *args, **kwargs):
        stream = original_open(target, mode, *args, **kwargs)
        return ObservedReader(stream) if target == path and mode == "rb" else stream

    def cancel():
        if stopped[0]:
            raise RuntimeError("synthetic cancellation during final hash")

    def handler(_):
        # Preflight and network have already spent part of the SAME budget.
        clock[0] += 0.15
        response_seen[0] = True
        return ok(media_receipt(descriptor))

    monkeypatch.setattr(Path, "open", open_file)
    assert_error("SYNC_CLIENT_" + failure,
                 lambda: client(handler).upload_media(SOURCE, LEASE, descriptor, path, check_cancelled=cancel, intent=media_intent(descriptor, source=SOURCE)))
    assert final_reads[0] == 1


def test_media_receipt_requires_transport_to_have_consumed_complete_body(media_file):
    path, descriptor, _ = media_file
    class SkipsUpload(httpx.BaseTransport):
        def handle_request(self, _):
            return ok(media_receipt(descriptor))
    item = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=SkipsUpload())
    assert_error("SYNC_CLIENT_MEDIA", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


def test_media_cancellation_has_controlled_error_and_no_network(media_file):
    path, descriptor, _ = media_file
    def cancelled():
        raise RuntimeError(TOKEN)
    item = client(lambda _: pytest.fail("cancelled upload must not send"))
    assert_error("SYNC_CLIENT_CANCELLED", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, check_cancelled=cancelled, intent=media_intent(descriptor, source=SOURCE)))


def test_media_cancellation_while_awaiting_response_is_observed(media_file):
    path, descriptor, _ = media_file
    stop = threading.Event()
    def cancelled():
        if stop.is_set():
            raise RuntimeError("cancelled")
    def handler(_):
        stop.set()
        return ok(media_receipt(descriptor))
    assert_error("SYNC_CLIENT_CANCELLED", lambda: client(handler).upload_media(SOURCE, LEASE, descriptor, path,
                                                                              check_cancelled=cancelled, intent=media_intent(descriptor, source=SOURCE)))


def test_media_receipt_cannot_return_arbitrary_local_path(media_file):
    path, descriptor, _ = media_file
    receipt = {**media_receipt(descriptor), "storage_key": "../../private/file"}
    assert_error("SYNC_CLIENT_INVALID_RESPONSE", lambda: client(lambda _: ok(receipt)).upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))


def test_heartbeat_runs_while_upload_waits_and_borrowed_transport_is_not_closed(media_file):
    path, descriptor, _ = media_file
    entered, release = threading.Event(), threading.Event()
    errors, result = [], []

    class Concurrent(httpx.BaseTransport):
        closed = 0
        def handle_request(self, request):
            if request.url.path.endswith("/media"):
                entered.set()
                assert release.wait(10)
                for _ in request.stream:
                    pass
                return ok(media_receipt(descriptor))
            return ok({"lease": LEASE, "renewed_claims": 0, "recovery": deepcopy(RECOVERY)})
        def close(self):
            self.closed += 1

    transport = Concurrent()
    item = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=transport)
    def upload():
        try:
            result.append(item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=upload, daemon=True)
    thread.start()
    try:
        assert entered.wait(10)
        assert item.heartbeat(SOURCE, LEASE["session_id"], LEASE)["lease"] == LEASE
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors and len(result) == 1
    assert transport.closed == 0


def test_real_local_socket_ignores_proxy_env_and_does_not_follow_redirect(monkeypatch):
    config.set_local({"sync_allow_insecure_test_transport": True})
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("X-Sync-Token")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/steal")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        item = SyncClient(f"http://127.0.0.1:{server.server_port}/reno", token_provider=lambda: TOKEN)
        assert_error("SYNC_CLIENT_REDIRECT", item.handshake)
        assert requests == [("/reno/api/sync/handshake", TOKEN)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_real_local_tls_rejects_untrusted_certificate_before_sending_credentials(monkeypatch, artificial_tls_certificate):
    # A minimal Linux stdlib need not ship CPython's public test key; the
    # fixture can generate an untrusted synthetic key without operator data.
    certificate = artificial_tls_certificate
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", "must-not-read-external-ca.pem")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        item = SyncClient(f"https://127.0.0.1:{server.server_port}", token_provider=lambda: TOKEN, timeout=5)
        assert_error("SYNC_CLIENT_TLS", item.handshake)
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_all_operations_follow_actual_fastapi_contract_on_isolated_databases(env, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    config.set_local({"node_role": "cloud", "sync_token": TOKEN})
    env["con"].execute("DELETE FROM sync_session")
    env["con"].commit()
    with TestClient(create_app(), base_url="https://sync.example.test") as server:
        def handler(request):
            response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.read())
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        item = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=httpx.MockTransport(handler))
        target = item.handshake()
        assert target["library_id"] == db.knowledge_version(env["con"])["library_id"]
        source = env["identity"]
        lease = item.heartbeat(source, str(uuid.uuid4()), metrics={"cookie_state": "unknown"})["lease"]
        sync_queue.enqueue(env["con"], [URL, URL + "?p=2"])
        env["con"].commit()
        page = item.pull(source, lease, claim_limit=2)
        assert len(page["claims"]) == 2
        failed = item.fail(source, lease, page["claims"][1]["claim"], "network", recovery_stream=page["recovery"]["stream"])
        assert failed["status"] == "retry_wait"
        review.record_decision(env["source"], atom_id="atom", action="confirm", client_id="client-test")
        env["source"].commit()
        events = sync_events.export_events(env["source"])["events"]
        assert item.push_events(source, lease, events)["inserted"] == 1
        assert item.push_events(source, lease, events)["duplicates"] == 1
        assert item.pull(source, lease)["events"]["events"] == events
        path = tmp_path / "artificial.mp4"
        path.write_bytes(CONTENT)
        from tests.test_sync_transfer import upload_binding
        binding = upload_binding(env)
        prepared = item.prepare_uploads(source, lease, binding=binding, descriptors=[env["media"]])
        assert prepared["media"][0]["state"] == "reserved"
        assert item.upload_media(source, lease, env["media"], path, intent={**binding, "descriptor": env["media"]})["published"] is False
        receipt = item.push_snapshot(source, lease, env["payload"], [{"video_id": VIDEO, "claim": page["claims"][0]["claim"]}],
                                     recovery_stream=page["recovery"]["stream"])
        assert receipt["publication_id"] == env["payload"]["publication_id"]
        assert env["con"].execute("SELECT status FROM sync_queue WHERE item_id=?", (page["claims"][0]["item_id"],)).fetchone()[0] == "done"
