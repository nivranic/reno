"""Synthetic DNS and transports; the production pinned backend stays unchanged."""
from dataclasses import replace
import hashlib
import socket
import threading
from time import monotonic
from urllib.parse import urlsplit

import httpx
import pytest

from reno import source_resolution as adapter, source_resolution_contract as codec, sync_sources

URL = "https://www.bilibili.com/video/BV13x41117TL?p=2"
SHORT = "https://b23.tv/short?x=1&x=2"
INPUT = "1" * 64
ATTEMPT = "00000000-0000-4000-8000-000000000001"
SETTLEMENT = "2" * 64


def plan(phase="dns", url=URL, **kwargs):
    values = dict(phase=phase, submitted_url=url, current_url=url, input_sha256=INPUT, max_attempts=24)
    if phase == "headers":
        values.update(dns_attempt_id=ATTEMPT, dns_settlement_sha256=SETTLEMENT, peer_ip="8.8.8.8")
    return codec.ResolutionPlan(**{**values, **kwargs})


def prepared(value, **kwargs):
    return adapter.prepare(value, deadline=monotonic() + 30, **kwargs)


def assert_codec(value, result):
    text = codec.encode_envelope(value, result)
    assert codec.decode_envelope(text, request_sha256=value.request_sha256, input_sha256=INPUT,
                                phase=value.phase, max_attempts=24) == result
    return text


@pytest.fixture
def network(monkeypatch):
    observed, options = [], []
    handler = [lambda request: httpx.Response(200)]
    actual_client = httpx.Client
    def client(**kwargs):
        options.append(kwargs.copy())
        kwargs.pop("transport").close()
        return actual_client(transport=httpx.MockTransport(lambda request: observed.append(request) or handler[0](request)), **kwargs)
    monkeypatch.setattr(adapter.httpx, "Client", client)
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("headers tried another DNS lookup"))
    return observed, options, handler


def test_prepare_does_not_call_check_dns_socket_or_start_thread(monkeypatch):
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: pytest.fail("prepare DNS"))
    monkeypatch.setattr(adapter.socket, "socket", lambda *a, **k: pytest.fail("prepare socket"))
    monkeypatch.setattr(adapter.threading.Thread, "start", lambda *a, **k: pytest.fail("prepare thread"))
    assert prepared(plan(), check_cancelled=lambda: pytest.fail("prepare called cancellation/config check"))


def test_one_dns_lookup_keeps_unique_public_order_and_no_http(monkeypatch):
    calls = []
    def lookup(host, port, **kwargs):
        calls.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    monkeypatch.setattr(adapter.httpx, "Client", lambda *a, **k: pytest.fail("DNS started HTTP"))
    value = plan()
    result = adapter.run_dns(prepared(value))
    assert result.successful and result.addresses == ("8.8.8.8", "2606:4700:4700::1111")
    assert calls == [("www.bilibili.com", 443, {"type": socket.SOCK_STREAM})]
    assert_codec(value, result)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "::1", "fc00::1",
                                     "::ffff:8.8.8.8", "2002:0808:0808::", "224.0.0.1", "240.0.0.1"])
def test_mixed_dns_answers_fail_entirely_as_closed_non_retryable_fact(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    bad = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)), (family, socket.SOCK_STREAM, 6, "", bad)])
    value = plan()
    result = adapter.run_dns(prepared(value))
    assert result.response_complete and not result.successful and not result.retryable
    assert result.dns_error == "non_public" and result.addresses is None
    assert address not in assert_codec(value, result)


@pytest.mark.parametrize("answer", [[], [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))],
    [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 1))],
    [(socket.AF_INET, True, 6, "", ("8.8.8.8", 443))],
    [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))] * 65])
def test_dns_empty_malformed_scoped_or_unbounded_answers_are_terminal(monkeypatch, answer):
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: answer)
    result = adapter.run_dns(prepared(plan()))
    assert result.response_complete and result.failure_class == "dns_rejected" and not result.retryable


def test_known_dns_api_error_is_complete_and_does_not_copy_raw_exception(monkeypatch):
    def lookup(*args, **kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "private URL cookie diagnostic")
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    value = plan()
    result = adapter.run_dns(prepared(value))
    assert result.response_complete and result.retryable and result.dns_error == "temporary"
    assert "private" not in assert_codec(value, result)


def test_dns_timeout_retains_slot_and_delivers_one_late_original_result(monkeypatch):
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    slots, results, calls = threading.BoundedSemaphore(1), [], []
    monkeypatch.setattr(sync_sources, "_DNS_SLOTS", slots)
    def lookup(*args, **kwargs):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    def sink(result):
        results.append(result)
        delivered.set()
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    value = plan()
    result = adapter.run_dns(prepared(value, dns_timeout=0.02), late_sink=sink)
    try:
        assert entered.is_set() and result.termination == "timeout" and result.unknown
        assert not slots.acquire(blocking=False)
        blocked = adapter.run_dns(prepared(value))
        assert blocked.termination == "launch_unknown" and len(calls) == 1
    finally:
        release.set()
        assert delivered.wait(2)
    assert len(results) == 1 and results[0].successful and results[0].request_sha256 == value.request_sha256
    assert slots.acquire(blocking=False)
    slots.release()


def test_cancel_during_dns_preserves_original_exception_and_late_result(monkeypatch):
    release, entered, delivered = threading.Event(), threading.Event(), threading.Event()
    cancelled = RuntimeError("private lease identity")
    original = []
    def check():
        if entered.is_set():
            raise cancelled
    def lookup(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    def sink(result):
        original.append(result)
        delivered.set()
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    value = prepared(plan(), check_cancelled=check)
    try:
        result = adapter.run_dns(value, late_sink=sink)
        assert result.termination == "cancelled" and result.unknown and value.cancel_exception is cancelled
    finally:
        release.set()
        assert delivered.wait(2)
    assert original[0].successful


def test_headers_use_checked_literal_host_sni_no_proxy_cookies_or_body(network, monkeypatch):
    class RefuseBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("must never read HTML")
        def close(self):
            closed.append(True)
    closed = []
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    network[2][0] = lambda request: httpx.Response(200, headers={"set-cookie": "private=secret"}, stream=RefuseBody())
    value = plan("headers")
    result = adapter.run_headers(prepared(value))
    assert result.successful and result.headers_complete and result.transport_closed and closed
    request = network[0][0]
    assert str(request.url.host) == "8.8.8.8" and request.headers["host"] == "www.bilibili.com"
    assert request.extensions["sni_hostname"] == "www.bilibili.com"
    assert "cookie" not in request.headers and "authorization" not in request.headers
    assert all(options["trust_env"] is False and options["follow_redirects"] is False and options["verify"] is True for options in network[1])
    assert "private=secret" not in assert_codec(value, result)


def test_ipv6_bracketed_peer_does_not_replace_original_sni(network):
    value = plan("headers", peer_ip="2606:4700:4700::1111")
    result = adapter.run_headers(prepared(value))
    assert result.successful
    assert str(network[0][0].url).startswith("https://[2606:4700:4700::1111]/")
    assert network[0][0].headers["host"] == network[0][0].extensions["sni_hostname"] == "www.bilibili.com"


@pytest.mark.parametrize("target", [SHORT, "http://www.bilibili.com/video/BV13x41117TL", "https://www.douyin.com/video/123",
    "https://user:secret@www.bilibili.com/video/BV13x41117TL", "https://127.0.0.1/private", "file:///private"])
def test_redirect_validation_has_no_second_request_and_rejected_raw_location_is_not_saved(network, target):
    value = plan("headers", url=SHORT)
    network[2][0] = lambda request: httpx.Response(302, headers={"location": target})
    result = adapter.run_headers(prepared(value))
    assert result.response_complete and result.failure_class == "redirect_invalid" and result.next_url is None
    assert len(network[0]) == 1
    assert "secret" not in assert_codec(value, result)


def test_relative_redirect_is_resolved_from_domain_not_the_pinned_ip(network):
    value = plan("headers", url="https://www.bilibili.com/start")
    network[2][0] = lambda request: httpx.Response(302, headers={"location": "/video/BV13x41117TL?p=2"})
    result = adapter.run_headers(prepared(value))
    assert result.successful and result.next_url == URL and len(network[0]) == 1
    assert_codec(value, result)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "limit", "header_limit", "unresolved"])
def test_bounded_header_only_terminal_shapes_and_redirect_cap(network, kind):
    value = plan("headers", url=SHORT)
    if kind == "limit":
        visited = tuple(f"https://b23.tv/hop{i}" for i in range(5))
        value = plan("headers", url=visited[0], current_url=SHORT, hop_index=5, visited_urls=visited)
    def handler(request):
        if kind == "missing":
            return httpx.Response(302)
        if kind == "duplicate":
            return httpx.Response(302, headers=[("location", URL), ("location", URL)])
        if kind == "limit":
            return httpx.Response(302, headers={"location": URL})
        if kind == "header_limit":
            return httpx.Response(200, headers={"oversized": "x" * codec.MAX_HEADER_BYTES})
        return httpx.Response(200)
    network[2][0] = handler
    result = adapter.run_headers(prepared(value))
    assert not result.successful and not result.retryable and len(network[0]) == 1
    assert (result.termination == "header_limit" and result.unknown) if kind == "header_limit" else result.response_complete
    assert_codec(value, result)


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_normal_http_errors_grant_only_complete_headers_retry(network, status):
    network[2][0] = lambda request: httpx.Response(status, headers={"retry-after": "10"})
    value = plan("headers")
    result = adapter.run_headers(prepared(value))
    assert result.response_complete and result.retryable and result.retry_after_seconds == 10
    text = assert_codec(value, result)
    assert codec.validate_retry_envelope(text, request_sha256=value.request_sha256, input_sha256=INPUT, phase="headers", max_attempts=24) == result


def test_timeout_and_cancellation_before_socket_do_not_create_normal_failure(network):
    expired = adapter.prepare(plan("headers"), deadline=monotonic() - 1)
    result = adapter.run_headers(expired)
    assert result.unknown and result.termination == "timeout" and not network[0]
    cancelled = RuntimeError("private lease error")
    def stop():
        raise cancelled
    value = prepared(plan("headers"), check_cancelled=stop)
    result = adapter.run_headers(value)
    assert result.unknown and result.termination == "cancelled" and value.cancel_exception is cancelled and not network[0]


def test_header_response_complete_before_cancel_still_returns_paid_known_result(network):
    completed = threading.Event()
    cancelled = RuntimeError("private lost owner after response")
    def check():
        if completed.is_set():
            raise cancelled
    def handler(request):
        completed.set()
        return httpx.Response(200)
    network[2][0] = handler
    result = adapter.run_headers(prepared(plan("headers"), check_cancelled=check))
    assert result.successful and len(network[0]) == 1


def test_network_or_tls_exception_is_unknown_and_has_no_raw_error(network):
    def handler(request):
        raise httpx.ConnectError("private token path URL")
    network[2][0] = handler
    value = plan("headers")
    result = adapter.run_headers(prepared(value))
    assert result.unknown and result.termination == "network_unknown" and len(network[0]) == 1
    assert "private" not in assert_codec(value, result)


def test_original_transport_still_has_single_pinned_connection_no_internal_retry(monkeypatch):
    observed = []
    original = sync_sources.httpcore.ConnectionPool
    def pool(**kwargs):
        observed.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(sync_sources.httpcore, "ConnectionPool", pool)
    value = prepared(plan("headers"))
    transport = sync_sources._HeaderTransport(value.deadline, value.check)
    try:
        assert observed[0]["retries"] == 0 and observed[0]["max_connections"] == 1
        assert observed[0]["max_keepalive_connections"] == 0 and observed[0]["http2"] is False
        assert observed[0]["ssl_context"].check_hostname is True
        assert observed[0]["ssl_context"].verify_mode == adapter.sync_sources.ssl.CERT_REQUIRED
    finally:
        transport.close()


def test_actual_original_http_parser_and_literal_socket_backend_without_a_second_dns(monkeypatch):
    connected, sent, received, closed = [], [], [], []
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\nSet-Cookie: private=secret\r\n\r\n" + b"x" * 10000
    class SyntheticSocket:
        def __init__(self):
            self.position = 0
        def settimeout(self, value):
            assert value > 0
        def setsockopt(self, *args):
            pass
        def connect(self, address):
            connected.append(address)
        def send(self, content):
            sent.append(bytes(content))
            return len(content)
        def recv(self, maximum):
            received.append(maximum)
            data = wire[self.position:self.position + maximum]
            self.position += len(data)
            return data
        def close(self):
            closed.append(True)
    def create(family, kind):
        assert (family, kind) == (socket.AF_INET, socket.SOCK_STREAM)
        return SyntheticSocket()
    monkeypatch.setattr(adapter.socket, "socket", create)
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: pytest.fail("literal backend resolved again"))
    value = plan("headers", url=URL.replace("https:", "http:"))
    result = adapter.run_headers(prepared(value))
    assert result.successful and result.response_complete and result.transport_closed
    assert connected == [("8.8.8.8", 80)]
    request = b"".join(sent).lower()
    assert b"host: www.bilibili.com\r\n" in request and b"cookie:" not in request
    assert len(received) == 1 and max(received) <= 1024 and closed
    assert "private=secret" not in assert_codec(value, result)


def test_dns_thread_start_failure_is_unknown_and_does_not_leak_slot(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(sync_sources, "_DNS_SLOTS", slots)
    monkeypatch.setattr(adapter.threading.Thread, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private start failure")))
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: pytest.fail("failed thread made lookup"))
    value = plan()
    result = adapter.run_dns(prepared(value))
    assert result.unknown and result.termination == "launch_unknown"
    assert slots.acquire(blocking=False)
    slots.release()
    assert "private" not in assert_codec(value, result)


def test_pre_cancelled_dns_never_acquires_thread_or_lookup(monkeypatch):
    cancelled = RuntimeError("original cancellation")
    def check():
        raise cancelled
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lambda *a, **k: pytest.fail("pre-cancelled DNS"))
    monkeypatch.setattr(adapter.threading.Thread, "start", lambda *a, **k: pytest.fail("pre-cancelled thread"))
    value = prepared(plan(), check_cancelled=check)
    result = adapter.run_dns(value)
    assert result.unknown and result.termination == "cancelled" and value.cancel_exception is cancelled


def test_complete_dns_racing_with_cancellation_keeps_paid_result(monkeypatch):
    finished = threading.Event()
    cancelled = RuntimeError("cancelled after DNS completion")
    def lookup(*args, **kwargs):
        finished.set()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    def check():
        if finished.is_set():
            raise cancelled
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    value = prepared(plan(), check_cancelled=check)
    result = adapter.run_dns(value)
    assert result.successful and result.response_complete
