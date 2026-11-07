"""Home-sync source resolution and immutable, content-bound provenance.

Only this resolver performs pinned-IP HTTP; yt-dlp still owns its platform/CDN
transport. It receives the verified final URL and its metadata is checked before
download. Cloud origins are authenticated paired-node assertions, not remotely
reproducible redirect proofs. No cookies, proxy environment or HTML/JS redirects
are used by this resolver.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import socket
import ssl
import threading
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid

import httpx
import httpcore
import certifi

from . import config, storage_lifecycle
from .sync_claims import (_origin_digest, _platform, _stamp, _video_reference, validate_origin,
                          validate_resource_identity)
from .sync_media import _Directory, _rename_new
from .sync_protocol import MAX_MEDIA_BYTES, SyncError, _json_bytes, validate_ingest_url

MAX_REDIRECTS = 5
DNS_TIMEOUT_SECONDS = 5.0
RESOLVE_TIMEOUT_SECONDS = 30.0
MAX_HEADER_BYTES = 16 * 1024
MAX_RESOLUTION_BYTES = 64 * 1024
MAX_RECORD_BYTES = 96 * 1024
_REDIRECTS = frozenset((301, 302, 303, 307, 308))
_DNS_SLOTS = threading.BoundedSemaphore(2)


class _DeadlineStream(httpcore.NetworkStream):
    """Recheck the shared deadline at every socket operation, including slow headers."""
    def __init__(self, sock, deadline, check):
        self.sock, self.deadline, self.check = sock, deadline, check
        self.received = 0

    def _timeout(self, timeout):
        return min(timeout or 5.0, _remaining(self.deadline, self.check))

    def read(self, max_bytes, timeout=None):
        read_deadline = min(self.deadline, monotonic() + self._timeout(timeout))
        while True:
            remaining = _remaining(read_deadline, self.check)
            self.sock.settimeout(min(remaining, 0.25))
            try:
                data = self.sock.recv(min(max_bytes, 1024))
                self.received += len(data)
                if self.received > MAX_HEADER_BYTES + 1024:
                    _error("Source response headers exceed the limit")
                return data
            except socket.timeout:
                continue
            except OSError:
                _error("Source response failed", "SYNC_SOURCE_NETWORK", 503)

    def write(self, buffer, timeout=None):
        while buffer:
            self.sock.settimeout(self._timeout(timeout))
            try:
                sent = self.sock.send(buffer)
                if sent == 0:
                    _error("Source connection closed", "SYNC_SOURCE_NETWORK", 503)
                buffer = buffer[sent:]
            except socket.timeout:
                _error("Source request timed out", "SYNC_SOURCE_TIMEOUT", 503)
            except OSError:
                _error("Source request failed", "SYNC_SOURCE_NETWORK", 503)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            self.sock.settimeout(self._timeout(timeout))
            # CPython's TLS handshake timeout bounds the entire handshake.
            self.sock = ssl_context.wrap_socket(self.sock, server_hostname=server_hostname)
            _remaining(self.deadline, self.check)
            return self
        except socket.timeout:
            self.close()
            _error("Source TLS timed out", "SYNC_SOURCE_TIMEOUT", 503)
        except OSError:
            self.close()
            _error("Source TLS verification failed", "SYNC_SOURCE_NETWORK", 503)
        except BaseException:
            self.close()
            raise

    def get_extra_info(self, info):
        if info == "ssl_object" and isinstance(self.sock, ssl.SSLSocket):
            return self.sock._sslobj
        if info == "socket":
            return self.sock
        return None

    def close(self):
        self.sock.close()


class _DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, deadline, check):
        self.deadline, self.check = deadline, check

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # No socket.create_connection(): even a literal must never pass through
        # a second getaddrinfo hook. Connect directly with its address family.
        address = _public_ip(host)
        sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(min(timeout or 5.0, _remaining(self.deadline, self.check)))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect((address, port))
            _remaining(self.deadline, self.check)
            return _DeadlineStream(sock, self.deadline, self.check)
        except socket.timeout:
            sock.close()
            _error("Source connection timed out", "SYNC_SOURCE_TIMEOUT", 503)
        except BaseException as exc:
            sock.close()
            if isinstance(exc, OSError):
                _error("Source connection failed", "SYNC_SOURCE_NETWORK", 503)
            raise


class _HeaderTransport(httpx.BaseTransport):
    """HTTPX adapter using public httpcore APIs; bodies are closed, never read."""
    def __init__(self, deadline, check):
        self.pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
            network_backend=_DeadlineBackend(deadline, check),
            max_connections=1, max_keepalive_connections=0, http2=False, retries=0)

    def handle_request(self, request):
        core_request = httpcore.Request(method=request.method, url=httpcore.URL(
            scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port,
            target=request.url.raw_path), headers=request.headers.raw, content=request.stream,
            extensions=request.extensions)
        try:
            response = self.pool.handle_request(core_request)
            try:
                return httpx.Response(response.status, headers=response.headers, content=b"")
            finally:
                response.close()
        except httpcore.TimeoutException:
            _error("Source response timed out", "SYNC_SOURCE_TIMEOUT", 503)
        except (httpcore.NetworkError, httpcore.ProtocolError):
            _error("Source response failed", "SYNC_SOURCE_NETWORK", 503)

    def close(self):
        self.pool.close()


def _error(message="Source resolution could not be verified", code="SYNC_SOURCE_INVALID", status=409):
    raise SyncError(message, code, status)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _remaining(deadline, check):
    check()
    remaining = deadline - monotonic()
    if remaining <= 0:
        _error("Source resolution timed out", "SYNC_SOURCE_TIMEOUT", 503)
    return remaining


def _public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        _error()
    # Reject scoped addresses and transition mechanisms as well as private,
    # multicast and unspecified answers. A mixed DNS answer fails as a whole.
    if ("%" in value or not ip.is_global or ip.is_multicast or ip.is_reserved
            or (ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
        _error("Source DNS destination is not permitted", "SYNC_SOURCE_DNS_REJECTED")
    return str(ip)


def _resolve_dns(host, port, deadline, check):
    """Bound waiting AND background DNS work; stuck OS DNS cannot spawn more threads."""
    _remaining(deadline, check)
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        _error("Source DNS resolver is busy", "SYNC_SOURCE_TIMEOUT", 503)
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except Exception:
            result.put((False, None))
        finally:
            slots.release()

    try:
        threading.Thread(target=lookup, name="reno-source-dns", daemon=True).start()
    except BaseException:
        slots.release()
        raise
    dns_deadline = min(deadline, monotonic() + DNS_TIMEOUT_SECONDS)
    while True:
        remaining = _remaining(dns_deadline, check)
        try:
            okay, answers = result.get(timeout=min(0.05, remaining))
            break
        except queue.Empty:
            pass
    if not okay or not answers or len(answers) > 64:
        _error("Source DNS resolution failed", "SYNC_SOURCE_NETWORK", 503)
    addresses = []
    for family, kind, _proto, _canonical, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
            _error()
        address = _public_ip(sockaddr[0])
        if sockaddr[1] != port or (family == socket.AF_INET6 and sockaddr[3] != 0):
            _error()
        if address not in addresses:
            addresses.append(address)
    return addresses


def resolve_url(url, *, check_cancelled=None):
    """Resolve allowed HTTP redirects with public DNS and a pinned TLS destination.

    Response bodies are never consumed. A final page requiring client-side
    redirects, authentication or an unsupported video route fails closed.
    Caller cancellation exceptions retain their original identity.
    """
    check = check_cancelled or (lambda: None)
    if config.is_cloud():
        _error("Source resolution requires the home node", "SYNC_ROLE_REQUIRED", 403)
    submitted = validate_ingest_url(url)
    deadline, current, hops, visited = monotonic() + RESOLVE_TIMEOUT_SECONDS, submitted, [], set()
    while True:
        _remaining(deadline, check)
        validate_ingest_url(current)
        parsed = urlsplit(current)
        if _platform(parsed.hostname) != _platform(urlsplit(submitted).hostname):
            _error("Source redirects must remain on the same platform")
        if current in visited:
            _error("Source redirect loop rejected")
        visited.add(current)
        port = 443 if parsed.scheme.lower() == "https" else 80
        addresses = _resolve_dns(parsed.hostname, port, deadline, check)
        # Connect to the checked literal. DNS cannot be consulted a second
        # time by HTTPX, while Host and SNI retain the original verified domain.
        peer = addresses[0]
        authority = f"[{peer}]" if ":" in peer else peer
        pinned = urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", parsed.query, ""))
        timeout = min(5.0, _remaining(deadline, check))
        try:
            with httpx.Client(trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(timeout), verify=True,
                              transport=_HeaderTransport(deadline, check)) as client:
                with client.stream("GET", pinned,
                                   headers={"Host": parsed.hostname, "Accept-Encoding": "identity",
                                            "User-Agent": "Reno-Sync/1"},
                                   extensions={"sni_hostname": parsed.hostname}) as response:
                    _remaining(deadline, check)
                    if sum(len(k) + len(v) + 4 for k, v in response.headers.raw) > MAX_HEADER_BYTES:
                        _error("Source response headers exceed the limit")
                    status = response.status_code
                    next_url = None
                    if status in _REDIRECTS:
                        locations = response.headers.get_list("location")
                        if len(locations) != 1 or not locations[0] or len(locations[0]) > 4096:
                            _error("Source redirect has no unique bounded destination")
                        next_url = urljoin(current, locations[0])
                        validate_ingest_url(next_url)
                        if parsed.scheme.lower() == "https" and urlsplit(next_url).scheme.lower() != "https":
                            _error("Source HTTPS downgrade rejected")
                    elif not 200 <= status < 300:
                        if status == 429:
                            _error("Source request was rate limited", "SYNC_SOURCE_RATE_LIMITED", 503)
                        if status == 408 or status >= 500:
                            _error("Source is temporarily unavailable", "SYNC_SOURCE_NETWORK", 503)
                        _error("Source returned an unsuccessful response", "SYNC_SOURCE_UNAVAILABLE", 503)
                    hops.append({"url": current, "peer_ip": peer, "status": status, "next_url": next_url})
        except httpx.TimeoutException:
            _error("Source resolution timed out", "SYNC_SOURCE_TIMEOUT", 503)
        except httpx.HTTPError:
            _error("Source request failed", "SYNC_SOURCE_NETWORK", 503)
        if next_url is None:
            if _video_reference(parsed, _platform(parsed.hostname)) is None:
                _error("Source did not resolve to a supported video resource")
            result = {"resolution_version": 1, "submitted_url": submitted, "resolved_url": current,
                      "resolved_at": _now(), "hops": hops}
            return _validate_resolution(result)
        if len(hops) > MAX_REDIRECTS:
            _error("Source redirect limit exceeded")
        current = next_url


def _validate_resolution(value):
    if (type(value) is not dict or set(value) != {"resolution_version", "submitted_url", "resolved_url", "resolved_at", "hops"}
            or type(value["resolution_version"]) is not int or value["resolution_version"] != 1):
        _error()
    validate_ingest_url(value["submitted_url"])
    validate_ingest_url(value["resolved_url"])
    _stamp(value["resolved_at"])
    hops = value["hops"]
    if type(hops) is not list or not 1 <= len(hops) <= MAX_REDIRECTS + 1:
        _error()
    expected, visited = value["submitted_url"], set()
    platform = _platform(urlsplit(expected).hostname)
    for index, hop in enumerate(hops):
        if (type(hop) is not dict or set(hop) != {"url", "peer_ip", "status", "next_url"}
                or hop["url"] != expected or type(hop["status"]) is not int
                or not isinstance(hop["peer_ip"], str)):
            _error()
        validate_ingest_url(hop["url"])
        if hop["url"] in visited or _platform(urlsplit(hop["url"]).hostname) != platform:
            _error()
        visited.add(hop["url"])
        _public_ip(hop["peer_ip"])
        final = index == len(hops) - 1
        if final:
            if not 200 <= hop["status"] < 300 or hop["next_url"] is not None or hop["url"] != value["resolved_url"]:
                _error()
        else:
            if hop["status"] not in _REDIRECTS:
                _error()
            validate_ingest_url(hop["next_url"])
            if urlsplit(hop["url"]).scheme == "https" and urlsplit(hop["next_url"]).scheme != "https":
                _error()
        expected = hop["next_url"]
    if len(_json_bytes(value)) > MAX_RESOLUTION_BYTES:
        _error()
    return json.loads(_json_bytes(value))


def validate_metadata(resolution, info):
    """Run before ingest creates paths/downloads; reject contradictory identities."""
    verified = _validate_resolution(resolution)
    if type(info) is not dict:
        _error()
    vid = info.get("id")
    platform = validate_resource_identity(verified["resolved_url"], vid)
    if validate_resource_identity(info.get("webpage_url"), vid) != platform:
        _error()
    declared = info.get("extractor")
    if declared is not None and (not isinstance(declared, str) or declared.lower().split(":")[0] != platform):
        _error()


def _generation(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _error("Source mapping generation is invalid", "SYNC_SOURCE_MAP_CORRUPT")


def validate_source_record(record, *, mapping_id=None, mapping_generation=None,
                           source_library_id=None, source_node_id=None):
    """Pure v1/v2 wrapper validation; the returned origin stays wire v1."""
    if type(record) is not dict:
        _error("Source mapping is invalid", "SYNC_SOURCE_MAP_CORRUPT")
    if mapping_generation is None:
        if set(record) != {"origin", "resolution"}:
            _error("Legacy source mapping is invalid", "SYNC_SOURCE_MAP_CORRUPT")
    else:
        _generation(mapping_generation)
        if (set(record) != {"format_version", "mapping_generation", "origin", "resolution"}
                or type(record["format_version"]) is not int or record["format_version"] != 2
                or record["mapping_generation"] != mapping_generation):
            _error("Source mapping generation does not match", "SYNC_SOURCE_MAP_CORRUPT")
    origin = validate_origin(record["origin"])
    resolution = _validate_resolution(record["resolution"])
    if ((mapping_id is not None and origin["mapping_id"] != mapping_id)
            or (source_library_id is not None and origin["source_library_id"] != source_library_id)
            or (source_node_id is not None and origin["source_node_id"] != source_node_id)
            or origin["resolution_sha256"] != hashlib.sha256(_json_bytes(resolution)).hexdigest()
            or any(origin[key] != resolution[key] for key in ("submitted_url", "resolved_url", "resolved_at"))):
        _error("Source mapping identity does not match", "SYNC_SOURCE_MAP_CORRUPT")
    return origin


class _CancellationCheck:
    """Keep caller exceptions distinct from storage/JSON failures."""
    def __init__(self, callback):
        if callback is not None and not callable(callback):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_ARGUMENT", 400)
        self.callback, self.exception = callback or (lambda: None), None

    def __call__(self):
        try:
            self.callback()
        except BaseException as exc:
            self.exception = exc
            raise


def _asset_sha(asset, *, profile, check_cancelled=None):
    """Prove explicit-profile media under the caller's existing lifetime guard."""
    guard = storage_lifecycle.require_guard(profile)
    check = check_cancelled if isinstance(check_cancelled, _CancellationCheck) else _CancellationCheck(check_cancelled)
    check()
    if not isinstance(asset, Mapping):
        _error("Source media asset is invalid", "SYNC_SOURCE_MEDIA_MISMATCH")
    try:
        files = asset.get("files")
        if files is None:
            files = json.loads(asset.get("files_json") or "{}")
        if type(files) is not dict or not isinstance(files.get("video"), str) or not files["video"]:
            _error("Source media is unavailable", "SYNC_SOURCE_MEDIA_MISMATCH")
        raw = files["video"]
        parts = raw.replace("\\", "/").split("/")
        if any(part == ".." or (":" in part and not (index == 0 and re.fullmatch(r"[A-Za-z]:", part)))
               for index, part in enumerate(parts)):
            _error("Source media path is outside its allowed root", "SYNC_SOURCE_MEDIA_MISMATCH")
        normalized = raw.replace("\\", "/")
        submitted = Path(normalized)
        media_root = profile.media
        path = submitted if submitted.is_absolute() else media_root.parent / PurePosixPath(normalized)
        path = path.absolute()
        try:
            path.relative_to(media_root)
        except ValueError:
            _error("Source media path is outside its allowed root", "SYNC_SOURCE_MEDIA_MISMATCH")
        # Keep the lexical path: resolving first would hide a substituted
        # symlink/junction from the handle-based ancestor checks.
        with _Directory(path.parent, base=media_root) as directory, directory.open_file(path.name) as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= MAX_MEDIA_BYTES:
                _error("Source media size is invalid", "SYNC_SOURCE_MEDIA_MISMATCH")
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                check()
                size += len(chunk)
                if size > before.st_size:
                    _error("Source media changed during verification", "SYNC_SOURCE_MEDIA_MISMATCH")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            current = directory.entry_stat(path.name)
            directory.verify()
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            # Windows entry_stat and fstat can expose different ctime metadata
            # views; content/mtime and the physical inode must agree in both.
            if size != before.st_size or identity(before) != identity(after) or identity(before)[:4] != identity(current)[:4]:
                _error("Source media changed during verification", "SYNC_SOURCE_MEDIA_MISMATCH")
            check()
            guard.verify()
        if digest.hexdigest() != asset.get("content_sha256"):
            _error("Source media does not match its asset", "SYNC_SOURCE_MEDIA_MISMATCH")
        return digest.hexdigest()
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, (SyncError, storage_lifecycle.StorageLifecycleError)) or exc is check.exception:
            raise
        _error("Source media is unavailable", "SYNC_SOURCE_MEDIA_MISMATCH")


class SourceMap:
    """Immutable records under DATA, retrieved by mapping ID after restart."""
    def __init__(self, root, *, profile, source_library_id, source_node_id):
        if not isinstance(profile, storage_lifecycle.StorageProfile):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_PROFILE", 400)
        try:
            data_root = Path(root)
            if (not data_root.is_absolute() or ".." in data_root.parts
                    or os.path.normcase(os.path.abspath(data_root)) != os.path.normcase(os.fspath(profile.database.parent))):
                raise ValueError
        except (OSError, TypeError, ValueError):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH", 400)
        for value in (source_library_id, source_node_id):
            try:
                if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                    _error()
            except ValueError:
                _error()
        self.profile = profile
        self.data_root = data_root.absolute()
        self.root = self.data_root / "sync-sources" / "v1"
        self.v2_root = self.data_root / "sync-sources" / "v2"
        self.source_library_id, self.source_node_id = source_library_id, source_node_id

    def _require_binding(self):
        """Recheck explicit paths without resolving aliases or touching storage."""
        if not isinstance(self.profile, storage_lifecycle.StorageProfile):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_PROFILE", 400)
        key = lambda path: os.path.normcase(os.path.abspath(os.fspath(path)))
        try:
            if (key(self.data_root) != key(self.profile.database.parent)
                    or key(self.root) != key(self.profile.database.parent / "sync-sources/v1")
                    or key(self.v2_root) != key(self.profile.database.parent / "sync-sources/v2")):
                raise ValueError
        except (OSError, TypeError, ValueError):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH", 400)

    def _validate_record(self, record, mapping_id=None, *, mapping_generation=None):
        return validate_source_record(record, mapping_id=mapping_id, mapping_generation=mapping_generation,
                                      source_library_id=self.source_library_id, source_node_id=self.source_node_id)

    def _read_record(self, mapping_id=None, *, mapping_generation=None, audit=False, missing_ok=False):
        self._require_binding()
        if mapping_id is not None and (type(mapping_id) is not str or len(mapping_id) != 64
                                       or any(c not in "0123456789abcdef" for c in mapping_id)):
            _error()
        if mapping_generation is not None:
            _generation(mapping_generation)
        elif mapping_id is None:
            _error()
        if not audit:
            self._assert_reusable(mapping_id, mapping_generation)
        root = self.v2_root if mapping_generation is not None else self.root
        name = (mapping_generation if mapping_generation is not None else mapping_id) + ".json"
        try:
            with _Directory(root, base=self.data_root) as directory, directory.open_file(name) as stream:
                before = os.fstat(stream.fileno())
                raw = stream.read(MAX_RECORD_BYTES + 1)
                if len(raw) > MAX_RECORD_BYTES:
                    _error("Source mapping exceeds its limit", "SYNC_SOURCE_MAP_CORRUPT")
                def pairs(values):
                    result = {}
                    for key, value in values:
                        if key in result:
                            _error("Source mapping contains repeated fields", "SYNC_SOURCE_MAP_CORRUPT")
                        result[key] = value
                    return result
                def nonfinite(_):
                    _error("Source mapping contains non-finite data", "SYNC_SOURCE_MAP_CORRUPT")
                record = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
                after, current = os.fstat(stream.fileno()), directory.entry_stat(name)
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if identity(before) != identity(after) or identity(after)[:4] != identity(current)[:4]:
                    _error("Source mapping changed during observation", "SYNC_SOURCE_MAP_CORRUPT")
                directory.verify()
            origin = self._validate_record(record, mapping_id, mapping_generation=mapping_generation)
            if not audit:
                self._assert_reusable(origin["mapping_id"], mapping_generation)
            return record
        except FileNotFoundError:
            if missing_ok:
                return None
            _error("Source mapping is unavailable", "SYNC_SOURCE_MAP_CORRUPT")
        except SyncError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            _error("Source mapping is unavailable or corrupt", "SYNC_SOURCE_MAP_CORRUPT")

    def _assert_reusable(self, mapping_id, mapping_generation):
        from . import sync_local_retirement
        sync_local_retirement.assert_mapping_reusable(self.profile, mapping_id, mapping_generation=mapping_generation)

    def get(self, mapping_id, *, mapping_generation=None, audit=False):
        """Legacy lookup stays explicit; v2 lookup must name its UUID instance."""
        with self._protected(_CancellationCheck(None)):
            return self._read_record(mapping_id, mapping_generation=mapping_generation, audit=audit)["origin"]

    def get_generation(self, mapping_generation, *, audit=False, missing_ok=False):
        with self._protected(_CancellationCheck(None)):
            record = self._read_record(mapping_generation=mapping_generation, audit=audit, missing_ok=missing_ok)
            return None if record is None else record["origin"]

    @contextmanager
    def _protected(self, check):
        """Borrow exact protection; never initialize or extend another guard."""
        self._require_binding()
        if storage_lifecycle.current_guard() is not None:
            guard = storage_lifecycle.require_guard(self.profile)
            check()
            yield guard
            guard.verify()
        else:
            with storage_lifecycle.shared(self.profile, check_cancelled=check) as guard:
                yield guard

    def record(self, resolution, *, extracted_video_id, asset, check_cancelled=None,
               mapping_generation=None, recorded_at=None):
        """Keep SHA proof and immutable map publication in one SH lifetime.

        A caller installing a work reference must already hold its state/worker
        ownership and a continuous guard through both this call and that write.
        No SQL is opened here; callers with a transaction must supply its guard.
        """
        check = _CancellationCheck(check_cancelled)
        with self._protected(check) as guard:
            return self._record(resolution, extracted_video_id=extracted_video_id,
                                asset=asset, check=check, guard=guard,
                                mapping_generation=mapping_generation, recorded_at=recorded_at)

    def _record(self, resolution, *, extracted_video_id, asset, check, guard,
                mapping_generation=None, recorded_at=None):
        check()
        if mapping_generation is not None:
            _generation(mapping_generation)
            if recorded_at is None:
                _error("A mapping generation requires its saved creation time", "SYNC_SOURCE_MAP_CORRUPT")
            _stamp(recorded_at)
        elif recorded_at is not None:
            _error("Legacy mapping cannot carry a generation seed", "SYNC_SOURCE_MAP_CORRUPT")
        resolution = _validate_resolution(resolution)
        validate_resource_identity(resolution["resolved_url"], extracted_video_id)
        existing = (self._read_record(mapping_generation=mapping_generation, missing_ok=True)
                    if mapping_generation is not None else None)
        media_sha = _asset_sha(asset, profile=self.profile, check_cancelled=check)
        if existing is not None:
            origin = existing["origin"]
            if (existing["resolution"] != resolution or origin["extracted_video_id"] != extracted_video_id
                    or origin["video_id"] != asset.get("video_id") or origin["media_sha256"] != media_sha
                    or origin["recorded_at"] != recorded_at):
                _error("A mapping generation cannot change its saved inputs", "SYNC_SOURCE_MAP_CORRUPT")
            check()
            self._assert_reusable(origin["mapping_id"], mapping_generation)
            return origin
        origin = {"origin_version": 1, "source_library_id": self.source_library_id,
                  "source_node_id": self.source_node_id, "submitted_url": resolution["submitted_url"],
                  "resolved_url": resolution["resolved_url"], "resolution_sha256": hashlib.sha256(_json_bytes(resolution)).hexdigest(),
                  "extracted_video_id": extracted_video_id, "video_id": asset.get("video_id"),
                  "media_sha256": media_sha, "resolved_at": resolution["resolved_at"],
                  "recorded_at": _now() if mapping_generation is None else recorded_at}
        origin["mapping_id"] = _origin_digest(origin)
        validate_origin(origin)
        record = {"origin": origin, "resolution": resolution}
        if mapping_generation is not None:
            record.update(format_version=2, mapping_generation=mapping_generation)
        self._assert_reusable(origin["mapping_id"], mapping_generation)
        raw = _json_bytes(record)
        if len(raw) > MAX_RECORD_BYTES:
            _error()
        root = self.root if mapping_generation is None else self.v2_root
        temporary, target = uuid.uuid4().hex + ".part", (origin["mapping_id"] if mapping_generation is None else mapping_generation) + ".json"
        try:
            check()
            guard.verify()
            with _Directory(root, base=self.data_root, create=True) as directory:
                temporary_identity, failure = None, None
                try:
                    with directory.open_file(temporary, write=True, create=True) as stream:
                        info = os.fstat(stream.fileno())
                        temporary_identity = info.st_dev, info.st_ino
                        check()
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    check()
                    guard.verify()
                    directory.verify()
                    try:
                        _rename_new(directory, temporary, directory, target)
                    except FileExistsError:
                        with directory.open_file(target) as stream:
                            if stream.read(MAX_RECORD_BYTES + 1) != raw:
                                _error("Immutable source mapping collision", "SYNC_SOURCE_MAP_CORRUPT")
                    if os.name != "nt":
                        os.fsync(directory.fd)
                    directory.verify()
                    guard.verify()
                except BaseException as exc:
                    failure = exc
                    raise
                finally:
                    try:
                        if temporary_identity is not None:
                            directory.verify()
                            info = directory.entry_stat(temporary)
                            if (info.st_dev, info.st_ino) == temporary_identity and info.st_nlink == 1:
                                directory.unlink(temporary)
                    except FileNotFoundError:
                        pass
                    except BaseException:
                        if failure is None:
                            raise
        except OSError as exc:
            if exc is check.exception:
                raise
            _error("Source mapping could not be saved", "SYNC_SOURCE_MAP_STORAGE", 503)
        check()
        return self.get(origin["mapping_id"], mapping_generation=mapping_generation)
