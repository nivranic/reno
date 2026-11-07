from tests.test_sync_client import media_intent
"""Real socket deadline/cancellation faults; no external DNS, cloud or model."""
from contextlib import contextmanager
import hashlib
import json
import socket
import socketserver
import ssl
import threading
import time

import httpx
import pytest

from reno import config, sync_client, sync_client_transport as transport
from reno.sync_client import SyncClient, SyncClientError
from tests.test_sync_client import SOURCE, TARGET, LEASE, TOKEN


@contextmanager
def server(handler):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.server.accepted.set()
            self.request.settimeout(0.2)
            try:
                handler(self.server, self.request)
            except (OSError, ssl.SSLError):
                pass  # The tested client is expected to close mid-response.
            finally:
                self.server.finished.set()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = False
        block_on_close = True

    instance = Server(("127.0.0.1", 0), Handler)
    instance.stop, instance.accepted, instance.finished = threading.Event(), threading.Event(), threading.Event()
    instance.port = instance.server_address[1]
    instance.url = f"http://127.0.0.1:{instance.port}"
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.05}, name="qa-sync-socket-server")
    thread.start()
    try:
        yield instance
    finally:
        instance.stop.set()
        instance.shutdown()
        instance.server_close()
        thread.join(5)
        assert not thread.is_alive()


def read_headers(instance, sock):
    value = b""
    until = time.monotonic() + 5
    while b"\r\n\r\n" not in value and not instance.stop.is_set() and time.monotonic() < until:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            value += chunk
            assert len(value) <= 65536
        except socket.timeout:
            pass
    return value


def slow_response(phase, *, on_request=None, payload=None):
    raw = json.dumps({"target": TARGET} if payload is None else payload).encode()
    def handle(instance, sock):
        received = read_headers(instance, sock)
        assert b"X-Sync-Token: " + TOKEN.encode() in received
        if on_request:
            on_request()
        if phase == "headers":
            sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: ")
            for _ in range(100):
                if instance.stop.wait(0.04):
                    return
                sock.sendall(b"x")
            sock.sendall(f"\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
        else:
            sock.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n".encode())
            for byte in raw:
                if instance.stop.wait(0.04):
                    return
                sock.sendall(bytes((byte,)))
    return handle


def expect(code, operation, *, maximum=1.5):
    started = time.monotonic()
    with pytest.raises(SyncClientError) as caught:
        operation()
    elapsed = time.monotonic() - started
    assert caught.value.code == code
    assert TOKEN not in str(caught.value)
    assert elapsed < maximum, f"operation exceeded bounded deadline: {elapsed:.2f}s"
    return elapsed


def callback(stop):
    def check():
        if stop.is_set():
            raise RuntimeError("synthetic cancellation containing " + TOKEN)
    return check


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_real_continuous_drip_cannot_reset_total_request_deadline(phase):
    config.set_local({"sync_allow_insecure_test_transport": True})
    with server(slow_response(phase)) as peer:
        item = SyncClient(peer.url, token_provider=lambda: TOKEN, timeout=0.35)
        elapsed = expect("SYNC_CLIENT_TIMEOUT", item.handshake)
        assert elapsed >= 0.25 and peer.accepted.is_set()
        assert peer.finished.wait(1), "timed-out client left its response socket active"


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_real_header_and_body_reads_observe_constructor_cancellation(phase):
    config.set_local({"sync_allow_insecure_test_transport": True})
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    with server(slow_response(phase, on_request=timer.start)) as peer:
        try:
            item = SyncClient(peer.url, token_provider=lambda: TOKEN, timeout=10, check_cancelled=callback(stop))
            expect("SYNC_CLIENT_CANCELLED", item.handshake)
            assert peer.accepted.is_set() and peer.finished.wait(1)
        finally:
            timer.cancel()
            timer.join(1)


@pytest.mark.parametrize("cancel", [False, True])
def test_tls_handshake_stall_is_bounded_and_cancellable(cancel):
    stop, hellos, timers = threading.Event(), [], []
    def stall(instance, sock):
        hellos.append(sock.recv(4096))
        if cancel:
            timer = threading.Timer(0.2, stop.set)
            timers.append(timer)
            timer.start()
        instance.stop.wait(5)
    with server(stall) as peer:
        try:
            item = SyncClient(peer.url.replace("http:", "https:"), token_provider=lambda: TOKEN,
                              timeout=10 if cancel else 0.35, check_cancelled=callback(stop))
            expect("SYNC_CLIENT_CANCELLED" if cancel else "SYNC_CLIENT_TIMEOUT", item.handshake)
            assert hellos and hellos[0].startswith(b"\x16\x03")
            assert TOKEN.encode() not in hellos[0]
        finally:
            for timer in timers:
                timer.cancel()
                timer.join(1)


@pytest.mark.parametrize("cancel", [False, True])
def test_dns_wait_is_bounded_and_late_result_never_starts_a_connection(monkeypatch, cancel):
    release, finished, stop = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def dns(host, port, **kwargs):
        assert host == "configured.private.test"
        calls.append(host)
        if cancel:
            timer.start()
        try:
            assert release.wait(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
        finally:
            finished.set()
    monkeypatch.setattr(transport.socket, "getaddrinfo", dns)
    monkeypatch.setattr(transport, "_DNS_SLOTS", threading.BoundedSemaphore(2))
    timer = threading.Timer(0.2, stop.set)
    with server(lambda instance, sock: pytest.fail("cancelled DNS result must not connect later")) as peer:
        try:
            item = SyncClient(f"https://configured.private.test:{peer.port}", token_provider=lambda: TOKEN,
                              timeout=10 if cancel else 0.35, check_cancelled=callback(stop))
            expect("SYNC_CLIENT_CANCELLED" if cancel else "SYNC_CLIENT_TIMEOUT", item.handshake)
            assert calls == ["configured.private.test"]
            release.set()
            assert finished.wait(1)
            assert not peer.accepted.wait(0.2)
        finally:
            release.set()
            if cancel and timer.ident is not None:
                timer.cancel()
                timer.join(1)
            assert finished.wait(1)


def test_dns_slot_limit_bounds_resolvers_even_after_callers_timeout(monkeypatch):
    release = threading.Event()
    entered = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]
    lock, calls, results = threading.Lock(), [], []
    def dns(host, port, **kwargs):
        with lock:
            index = len(calls)
            calls.append(host)
        assert index < 2, "DNS slot exhaustion started an additional resolver"
        entered[index].set()
        try:
            assert release.wait(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
        finally:
            finished[index].set()
    monkeypatch.setattr(transport.socket, "getaddrinfo", dns)
    monkeypatch.setattr(transport, "_DNS_SLOTS", threading.BoundedSemaphore(2))

    def caller():
        # Exercise the actual backend/DNS deadline without unrelated CA-file
        # loading consuming the short deadline before lookup has even begun.
        try:
            stream = transport._DeadlineBackend(transport.Deadline(0.3)).connect_tcp(
                "configured.private.test", 443)
        except transport.SyncTransportError as exc:
            with lock:
                results.append(exc.kind)
        else:
            stream.close()
            with lock:
                results.append("unexpected-connection")

    callers = [threading.Thread(target=caller, name=f"qa-sync-dns-caller-{index}") for index in range(2)]
    try:
        for thread in callers:
            thread.start()
        for event in entered:
            assert event.wait(2), "the slot-limit test did not establish two occupied DNS slots"
        assert not release.is_set() and not any(event.is_set() for event in finished)
        for thread in callers:
            thread.join(2)
            assert not thread.is_alive(), "DNS caller did not honor its total deadline"
        assert sorted(results) == ["TIMEOUT", "TIMEOUT"]
        assert len(calls) == 2 and not any(event.is_set() for event in finished)
        for _ in range(3):
            started = time.monotonic()
            with pytest.raises(transport.SyncTransportError) as caught:
                transport._DeadlineBackend(transport.Deadline(10)).connect_tcp("configured.private.test", 443)
            assert caught.value.kind == "TIMEOUT"
            assert time.monotonic() - started < 0.5
        assert len(calls) == 2
    finally:
        release.set()
        for thread in callers:
            if thread.ident is not None:
                thread.join(2)
                assert not thread.is_alive()
        for started, complete in zip(entered, finished):
            if started.is_set():
                assert complete.wait(1)


def test_configured_private_dns_is_allowed_and_preserves_original_tls_hostname(monkeypatch, artificial_tls_certificate):
    certificate = artificial_tls_certificate
    names, lookups = [], []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate)
    context.set_servername_callback(lambda sock, name, ctx: names.append(name))
    def tls_peer(instance, sock):
        with context.wrap_socket(sock, server_side=True):
            pytest.fail("untrusted certificate must prevent credential transmission")
    with server(tls_peer) as peer:
        def dns(host, port, **kwargs):
            lookups.append(host)
            assert host == "configured.private.test"
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", peer.port))]
        monkeypatch.setattr(transport.socket, "getaddrinfo", dns)
        item = SyncClient(f"https://configured.private.test:{peer.port}", token_provider=lambda: TOKEN, timeout=2)
        expect("SYNC_CLIENT_TLS", item.handshake)
        assert lookups == ["configured.private.test"] and names == ["configured.private.test"]
        assert peer.accepted.is_set()


def test_media_uses_shared_longer_deadline_and_still_has_a_total_cap(tmp_path, monkeypatch):
    config.set_local({"sync_allow_insecure_test_transport": True})
    monkeypatch.setattr(sync_client, "MAX_MEDIA_TOTAL_SECONDS", 0.45)
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"artificial media")
    descriptor = {"video_id": "BV1Deadline", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "byte_size": path.stat().st_size, "extension": "mp4"}
    payload = {"storage_key": f"sync/BV1Deadline/{descriptor['sha256']}.mp4",
               "sha256": descriptor["sha256"], "published": False}
    with server(slow_response("body", payload=payload)) as peer:
        item = SyncClient(peer.url, token_provider=lambda: TOKEN, timeout=0.2)
        elapsed = expect("SYNC_CLIENT_TIMEOUT", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)))
        assert elapsed >= 0.3
        assert peer.finished.wait(1)


def test_real_backpressured_media_write_is_cancelled_without_background_sender(tmp_path):
    config.set_local({"sync_allow_insecure_test_transport": True})
    stop, timers = threading.Event(), []
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"bounded artificial payload " * (1024 * 1024))
    descriptor = {"video_id": "BV1Backpressure", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "byte_size": path.stat().st_size, "extension": "mp4"}
    def blocked_reader(instance, sock):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        assert b"X-Sync-Token: " + TOKEN.encode() in read_headers(instance, sock)
        timer = threading.Timer(0.2, stop.set)
        timers.append(timer)
        timer.start()
        instance.stop.wait(5)
    with server(blocked_reader) as peer:
        try:
            item = SyncClient(peer.url, token_provider=lambda: TOKEN, timeout=10, check_cancelled=callback(stop))
            expect("SYNC_CLIENT_CANCELLED", lambda: item.upload_media(SOURCE, LEASE, descriptor, path, intent=media_intent(descriptor, source=SOURCE)), maximum=2)
            assert peer.accepted.is_set()
        finally:
            for timer in timers:
                timer.cancel()
                timer.join(1)


def test_constructor_and_per_upload_cancellation_are_both_enforced(tmp_path):
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"synthetic")
    descriptor = {"video_id": "BV1Cancel", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "byte_size": path.stat().st_size, "extension": "mp4"}
    for use_constructor in (False, True):
        stop = threading.Event()
        stop.set()
        item = SyncClient("https://configured.private.test", token_provider=lambda: TOKEN,
                          check_cancelled=callback(stop) if use_constructor else None,
                          transport=httpx.MockTransport(lambda _: pytest.fail("cancelled upload cannot reach transport")))
        expect("SYNC_CLIENT_CANCELLED", lambda: item.upload_media(SOURCE, LEASE, descriptor, path,
                                                                  check_cancelled=None if use_constructor else callback(stop), intent=media_intent(descriptor, source=SOURCE)))


def test_injected_transports_keep_ownership_and_receive_cooperative_deadline():
    seen = []
    def handler(request):
        deadline = request.extensions["reno_sync_deadline"]
        seen.append(deadline.remaining())
        return httpx.Response(200, json={"target": TARGET})
    item = SyncClient("https://configured.private.test", token_provider=lambda: TOKEN,
                      transport=httpx.MockTransport(handler), timeout=2)
    assert item.handshake() == TARGET and 0 < seen[0] <= 2
