"""Synchronous HTTP transport with one monotonic deadline and cancellable I/O.

Only bounded DNS workers can outlive a cancelled call. They resolve a hostname
and return an address; they cannot connect, send credentials or mutate state.
All sockets and HTTP work stay on the caller's thread and are closed on exit.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import ipaddress
import queue
import select
import socket
import ssl
import threading
from time import monotonic

import certifi
import httpcore
import httpx

DNS_TIMEOUT_SECONDS = 5.0
CANCEL_POLL_SECONDS = 0.1
_DNS_SLOTS = threading.BoundedSemaphore(2)


class SyncTransportError(Exception):
    def __init__(self, kind):
        super().__init__("Sync transport could not complete")
        self.kind = kind


def _error(kind):
    raise SyncTransportError(kind) from None


class Deadline:
    def __init__(self, seconds, check_cancelled=None):
        self.expires = monotonic() + seconds
        self.check_cancelled = check_cancelled

    def remaining(self, until=None):
        if self.check_cancelled is not None:
            try:
                self.check_cancelled()
            except Exception:
                _error("CANCELLED")
        value = min(self.expires, until if until is not None else self.expires) - monotonic()
        if value <= 0:
            _error("TIMEOUT")
        return value

    def operation_end(self, timeout):
        remaining = self.remaining()
        return monotonic() + min(remaining, timeout if timeout is not None else remaining)


def _wait(sock, *, read=False, write=False, deadline, until):
    while True:
        timeout = min(CANCEL_POLL_SECONDS, deadline.remaining(until))
        try:
            readable, writable, exceptional = select.select([sock] if read else [], [sock] if write else [], [sock], timeout)
        except InterruptedError:
            continue
        except (OSError, ValueError):
            _error("NETWORK")
        deadline.remaining(until)
        if exceptional:
            _error("NETWORK")
        if readable or writable:
            return


def _addresses(host, port, deadline, until):
    """Resolve the configured endpoint, including intentional private networks."""
    deadline.remaining(until)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return [(socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                 (str(address), port, 0, 0) if address.version == 6 else (str(address), port))]
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        _error("TIMEOUT")
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            try:
                result.put_nowait((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
            except Exception:
                result.put_nowait((False, None))
        finally:
            slots.release()

    try:
        threading.Thread(target=lookup, name="reno-sync-client-dns", daemon=True).start()
    except BaseException:
        slots.release()
        raise
    dns_until = min(until, monotonic() + DNS_TIMEOUT_SECONDS)
    while True:
        remaining = deadline.remaining(dns_until)
        try:
            okay, records = result.get(timeout=min(CANCEL_POLL_SECONDS, remaining))
            break
        except queue.Empty:
            pass
    deadline.remaining(dns_until)
    if not okay or not records or len(records) > 64:
        _error("NETWORK")
    addresses = []
    for family, kind, _protocol, _canonical, sockaddr in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            continue
        try:
            address = ipaddress.ip_address(sockaddr[0])
            if sockaddr[1] != port or address.version != (6 if family == socket.AF_INET6 else 4):
                _error("NETWORK")
        except (ValueError, IndexError, TypeError):
            _error("NETWORK")
        candidate = (family, sockaddr)
        if candidate not in addresses:
            addresses.append(candidate)
    if not addresses:
        _error("NETWORK")
    return addresses


class _DeadlineStream(httpcore.NetworkStream):
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        self.sock.setblocking(False)

    def read(self, max_bytes, timeout=None):
        until = self.deadline.operation_end(timeout)
        while True:
            self.deadline.remaining(until)
            try:
                return self.sock.recv(max_bytes)
            except (ssl.SSLWantReadError, BlockingIOError):
                _wait(self.sock, read=True, deadline=self.deadline, until=until)
            except ssl.SSLWantWriteError:
                _wait(self.sock, write=True, deadline=self.deadline, until=until)
            except InterruptedError:
                continue
            except OSError:
                _error("NETWORK")

    def write(self, buffer, timeout=None):
        until, pending = self.deadline.operation_end(timeout), memoryview(buffer)
        while pending:
            self.deadline.remaining(until)
            try:
                size = self.sock.send(pending)
                if size <= 0:
                    _error("NETWORK")
                pending = pending[size:]
            except ssl.SSLWantReadError:
                _wait(self.sock, read=True, deadline=self.deadline, until=until)
            except (ssl.SSLWantWriteError, BlockingIOError):
                _wait(self.sock, write=True, deadline=self.deadline, until=until)
            except InterruptedError:
                continue
            except OSError:
                _error("NETWORK")

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        until = self.deadline.operation_end(timeout)
        try:
            self.sock = ssl_context.wrap_socket(self.sock, server_hostname=server_hostname, do_handshake_on_connect=False)
            self.sock.setblocking(False)
            while True:
                self.deadline.remaining(until)
                try:
                    self.sock.do_handshake()
                    self.deadline.remaining(until)
                    return self
                except ssl.SSLWantReadError:
                    _wait(self.sock, read=True, deadline=self.deadline, until=until)
                except ssl.SSLWantWriteError:
                    _wait(self.sock, write=True, deadline=self.deadline, until=until)
        except ssl.SSLError:
            self.close()
            _error("TLS")
        except OSError:
            self.close()
            _error("NETWORK")
        except BaseException:
            self.close()
            raise

    def get_extra_info(self, info):
        if info == "ssl_object" and isinstance(self.sock, ssl.SSLSocket):
            return self.sock
        if info == "socket":
            return self.sock
        return None

    def close(self):
        self.sock.close()


class _DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, deadline):
        self.deadline = deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        until = self.deadline.operation_end(timeout)
        addresses = _addresses(host, port, self.deadline, until)
        for family, address in addresses:
            self.deadline.remaining(until)
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.setblocking(False)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for option in socket_options or ():
                    sock.setsockopt(*option)
                if local_address is not None:
                    sock.bind((local_address, 0))
                result = sock.connect_ex(address)
                pending = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                           getattr(errno, "WSAEWOULDBLOCK", 10035), getattr(errno, "WSAEINPROGRESS", 10036)}
                if result not in pending:
                    sock.close()
                    continue
                if result:
                    _wait(sock, write=True, deadline=self.deadline, until=until)
                    if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                        sock.close()
                        continue
                self.deadline.remaining(until)
                return _DeadlineStream(sock, self.deadline)
            except SyncTransportError as exc:
                sock.close()
                if exc.kind in {"TIMEOUT", "CANCELLED"}:
                    raise
            except OSError:
                sock.close()
            except BaseException:
                sock.close()
                raise
        _error("NETWORK")


@contextmanager
def _core_errors():
    try:
        yield
    except httpcore.TimeoutException:
        _error("TIMEOUT")
    except httpcore.RemoteProtocolError:
        _error("INVALID_RESPONSE")
    except httpcore.LocalProtocolError:
        _error("INVALID_REQUEST")
    except httpcore.NetworkError:
        _error("NETWORK")


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, response, deadline):
        self.response, self.deadline = response, deadline

    def __iter__(self):
        with _core_errors():
            self.deadline.remaining()
            for chunk in self.response.iter_stream():
                self.deadline.remaining()
                yield chunk
            self.deadline.remaining()

    def close(self):
        self.response.close()


class DeadlineTransport(httpx.BaseTransport):
    """A request-owned pool; no proxy, implicit redirects or environment CAs."""
    def __init__(self, deadline):
        self.deadline = deadline
        self.pool = httpcore.ConnectionPool(ssl_context=ssl.create_default_context(cafile=certifi.where()),
            network_backend=_DeadlineBackend(deadline), max_connections=1, max_keepalive_connections=0,
            http2=False, retries=0)

    def handle_request(self, request):
        self.deadline.remaining()
        core_request = httpcore.Request(method=request.method, url=httpcore.URL(
            scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions)
        with _core_errors():
            response = self.pool.handle_request(core_request)
            try:
                self.deadline.remaining()
                return httpx.Response(response.status, headers=response.headers,
                    stream=_ResponseStream(response, self.deadline), extensions=response.extensions)
            except BaseException:
                response.close()
                raise

    def close(self):
        self.pool.close()
