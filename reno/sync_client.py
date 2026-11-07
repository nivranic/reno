"""Bounded, credential-isolated sync HTTP client; no local database writes.

Each operation owns its HTTP client, so a heartbeat can run while media is being
uploaded. Injected transports remain caller-owned. No response URL, header,
remote error detail or underlying exception is included in public errors.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import config, sync_events, sync_protocol as protocol, sync_queue as queue, sync_snapshot
from . import sync_upload_pins as pins
from .sync_protocol import SyncError
from .sync_client_transport import Deadline, DeadlineTransport, SyncTransportError

MAX_CONTROL_BYTES = 64 * 1024
MAX_PULL_BYTES = sync_events.MAX_BATCH_BYTES + MAX_CONTROL_BYTES
MAX_REQUEST_BYTES = sync_snapshot.MAX_SNAPSHOT_BYTES + MAX_CONTROL_BYTES
MAX_INTENT_BYTES = 4 * 1024 * 1024
MAX_VERIFY_TOTAL_SECONDS = 300
CHUNK_BYTES = 128 * 1024
MAX_MEDIA_TOTAL_SECONDS = 3600
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_PATH_PART = re.compile(r"[A-Za-z0-9_~-][A-Za-z0-9._~-]*\Z")

# Closed wire codes, not arbitrary strings supplied by a server.
_REMOTE_CODES = frozenset("""
INVALID_SYNC_REQUEST INVALID_SYNC_CURSOR INVALID_SYNC_EVENT INVALID_SYNC_SNAPSHOT
INCOMPATIBLE_SYNC_SCHEMA UNSUPPORTED_EVENT_SCHEMA SYNC_UNAUTHORIZED
SYNC_ACCESS_UNAVAILABLE SYNC_CLOUD_REQUIRED SYNC_HTTPS_REQUIRED SYNC_INTERNAL_ERROR
SYNC_BODY_INTERRUPTED SYNC_EVENT_STREAM_CHANGED SYNC_STORAGE_FAILED
SYNC_UPLOAD_BUSY SYNC_UPLOAD_TIMEOUT SYNC_BUSY SYNC_CLAIM_EXPIRED SYNC_CLAIM_SOURCE_MISMATCH
SYNC_CLAIM_MISMATCH SYNC_IDENTITY_UNAVAILABLE SYNC_ITEM_NOT_FOUND SYNC_LEASE_EXPIRED
SYNC_LEASE_MISMATCH SYNC_MAINTENANCE_BUSY SYNC_NOT_PAIRED SYNC_OUTCOME_MISMATCH
SYNC_PEER_MISMATCH SYNC_PUBLICATION_REQUIRED SYNC_SELF_PAIRING SYNC_SESSION_BUSY
SYNC_CURSOR_OUT_OF_RANGE SYNC_INCOMPLETE_SNAPSHOT SYNC_ATOM_OWNER_MISMATCH
SYNC_CONTENT_CONFLICT SYNC_PUBLICATION_CONFLICT SYNC_ROLE_REQUIRED
SYNC_STALE_PUBLICATION SYNC_VERSION_CONFLICT SYNC_VIDEO_OWNER_MISMATCH
SYNC_MEDIA_BUSY SYNC_MEDIA_CORRUPT SYNC_MEDIA_HASH_MISMATCH SYNC_MEDIA_NOT_FOUND
SYNC_MEDIA_SIZE_MISMATCH SYNC_MEDIA_STORAGE_ERROR SYNC_MEDIA_STORAGE_FULL
SYNC_MEDIA_STREAM_ERROR SYNC_MEDIA_TEMP_QUOTA SYNC_MEDIA_TOO_LARGE
SYNC_MEDIA_UNSAFE_PATH INVALID_SYNC_REPORT SYNC_REPORT_CONFLICT
SYNC_RECOVERY_GENERATION_MISMATCH SYNC_RECOVERY_STREAM_MISMATCH SYNC_RECOVERY_HELD SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED
SYNC_UPLOAD_INTENT_INVALID SYNC_UPLOAD_INTENT_CONFLICT SYNC_UPLOAD_ABANDONED SYNC_UPLOAD_PUBLISHED
SYNC_UPLOAD_TERMINAL SYNC_UPLOAD_NOT_RESERVED SYNC_UPLOAD_NOT_STORED SYNC_UPLOAD_SET_MISMATCH
SYNC_UPLOAD_ARTIFACT_MISMATCH SYNC_UPLOAD_PUBLICATION_REQUIRED SYNC_UPLOAD_ABANDON_CONFLICT
SYNC_PUBLICATION_BINDING_UNPROVEN
SYNC_BUDGET_REQUEST_MISMATCH SYNC_BUDGET_RECEIPT_INVALID SYNC_BUDGET_DEFER_REQUIRED
SYNC_SOURCE_REQUEST_MISMATCH SYNC_SOURCE_RECEIPT_INVALID
SYNC_SOURCE_READMIT_BUSY SYNC_SOURCE_READMIT_ROLE_REQUIRED SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED
SYNC_SOURCE_READMIT_SCHEMA_REQUIRED SYNC_SOURCE_READMIT_WRITER_REQUIRED SYNC_SOURCE_READMIT_PROFILE_MISMATCH
SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED SYNC_SOURCE_READMIT_PEER_MISMATCH SYNC_SOURCE_READMIT_OPERATION_CONFLICT
SYNC_SOURCE_READMIT_RECORD_INVALID SYNC_SOURCE_READMIT_ORIGINAL_RECEIPT_INVALID SYNC_SOURCE_READMIT_DEFER_REQUIRED
SYNC_SOURCE_READMIT_CONTROL_CHANGED SYNC_SOURCE_READMIT_OUTCOME_MISMATCH SYNC_SOURCE_READMIT_EFFECT_UNKNOWN
SYNC_SOURCE_READMIT_EFFECT_RECEIPT_REQUIRED SYNC_SOURCE_READMIT_EFFECT_REQUIRED SYNC_SOURCE_READMIT_EFFECT_BODY_MISMATCH
SYNC_SOURCE_READMIT_DISPOSITION_REQUIRED SYNC_SOURCE_READMIT_PREVIOUS_REQUIRED SYNC_SOURCE_READMIT_LINEAGE_MISMATCH
SYNC_SOURCE_READMIT_CLAIM_MISMATCH SYNC_SOURCE_READMIT_ITEM_NOT_FOUND
SYNC_SOURCE_CONTINUITY_ACTIVATION_INCOMPLETE SYNC_SOURCE_CONTINUITY_ACTIVATION_REQUIRED
SYNC_SOURCE_CONTINUITY_BUSY SYNC_SOURCE_CONTINUITY_BYTES_LIMIT SYNC_SOURCE_CONTINUITY_CHANGED
SYNC_SOURCE_CONTINUITY_CLOSURE_REQUIRED SYNC_SOURCE_CONTINUITY_CONTRACT_MISMATCH
SYNC_SOURCE_CONTINUITY_COUNT_LIMIT SYNC_SOURCE_CONTINUITY_DEADLINE_LIMIT SYNC_SOURCE_CONTINUITY_HEAD_CHANGED
SYNC_SOURCE_CONTINUITY_HEAD_INCOMPLETE SYNC_SOURCE_CONTINUITY_HEAD_INVALID SYNC_SOURCE_CONTINUITY_INVALID
SYNC_SOURCE_CONTINUITY_JOURNAL_INVALID SYNC_SOURCE_CONTINUITY_LOCK_INVALID SYNC_SOURCE_CONTINUITY_LOCK_REQUIRED
SYNC_SOURCE_CONTINUITY_MISSING SYNC_SOURCE_CONTINUITY_OUTCOME_MISMATCH SYNC_SOURCE_CONTINUITY_OWNER_MISMATCH
SYNC_SOURCE_CONTINUITY_RECORD_INCOMPLETE SYNC_SOURCE_CONTINUITY_RECORD_INVALID SYNC_SOURCE_CONTINUITY_RECORD_MISSING
SYNC_SOURCE_CONTINUITY_REGISTRATION_REQUIRED SYNC_SOURCE_CONTINUITY_REQUEST_MISMATCH
SYNC_SOURCE_CONTINUITY_UNEXPECTED_FILE SYNC_SOURCE_CONTINUITY_UNSAFE_PATH
STORAGE_NOT_INITIALIZED STORAGE_BUSY STORAGE_PROFILE_MISMATCH STORAGE_GUARD_REQUIRED
STORAGE_ARTIFACT_CHANGED STORAGE_ARTIFACT_MISSING STORAGE_ARTIFACT_CONFLICT STORAGE_ARTIFACT_UNSAFE
""".split())
_ERRORS = {
    "INVALID_URL": (400, "Sync server URL is invalid"),
    "INVALID_REQUEST": (400, "Sync client input is invalid"),
    "CREDENTIAL": (401, "Sync credential is unavailable or invalid"),
    "INSECURE_TRANSPORT": (403, "Sync client requires HTTPS"),
    "NETWORK": (503, "Sync network request failed"),
    "TIMEOUT": (504, "Sync network request timed out"),
    "TLS": (503, "Sync TLS verification failed"),
    "REDIRECT": (502, "Sync redirects are not permitted"),
    "TOO_LARGE": (502, "Sync response exceeds its size limit"),
    "INVALID_RESPONSE": (502, "Sync server response is invalid"),
    "AUTH": (401, "Sync server rejected the credential"),
    "REMOTE": (502, "Sync server rejected the request"),
    "MEDIA": (409, "Sync upload file is unavailable or changed"),
    "CANCELLED": (409, "Sync operation was cancelled"),
}


class SyncClientError(SyncError):
    """A sanitized transport or closed-contract remote error."""

    def __init__(self, kind, *, remote_code=None, remote_status=None):
        status, message = _ERRORS[kind]
        code = "SYNC_CLIENT_" + kind
        if kind == "REMOTE" and remote_code in _REMOTE_CODES and type(remote_status) is int and 400 <= remote_status <= 599:
            code, status = remote_code, remote_status
        super().__init__(message, code, status)


def _error(kind):
    raise SyncClientError(kind) from None


@contextmanager
def _checking(kind="INVALID_REQUEST"):
    try:
        yield
    except SyncClientError:
        raise
    except (SyncError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        _error(kind)


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError


def _identity(value):
    _exact(value, ("library_id", "node_id", "handshake"))
    queue._uuid(value["library_id"], "library_id")
    queue._uuid(value["node_id"], "node_id")
    handshake = value["handshake"]
    _exact(handshake, ("protocol_version", "migration_version", "core_schema_sha256"))
    for key in ("protocol_version", "migration_version"):
        queue._integer(handshake[key], key)
    if not isinstance(handshake["core_schema_sha256"], str) or not _SHA256.fullmatch(handshake["core_schema_sha256"]):
        raise ValueError


def _claim(value):
    _exact(value, ("item_id", "lease_token", "lease_generation"))
    queue._integer(value["item_id"], "item_id")
    queue._uuid(value["lease_token"], "lease_token")
    queue._integer(value["lease_generation"], "lease_generation")


def _stream(value):
    _exact(value, ("library_id", "library_epoch"))
    queue._uuid(value["library_id"], "library_id")
    queue._integer(value["library_epoch"], "library_epoch")


def _recovery(value, source_library_id=None, target_library_id=None):
    _exact(value, ("generation", "revision", "state", "source_floor", "stream"))
    _stream(value["stream"])
    if target_library_id is not None and value["stream"]["library_id"] != target_library_id:
        raise ValueError
    queue._integer(value["generation"], "recovery generation", minimum=0)
    queue._integer(value["revision"], "recovery revision", minimum=0)
    if value["state"] not in ("running", "held", "resume_pending"):
        raise ValueError
    floor = value["source_floor"]
    if floor is not None:
        sync_snapshot._version(floor)
        if source_library_id is not None and floor["library_id"] != source_library_id:
            raise ValueError


def _events(values):
    if type(values) is not list or len(values) > sync_events.MAX_EVENTS:
        raise ValueError
    result, seen, total = [], set(), 0
    for value in values:
        event, size = sync_events._validate_event(value)
        if event["event_id"] in seen:
            raise ValueError
        seen.add(event["event_id"])
        total += size
        if total > sync_events.MAX_BATCH_BYTES:
            raise ValueError
        result.append(event)
    return result


def _json(value, maximum):
    sync_snapshot._check_json(value)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(data) > maximum:
        raise ValueError
    return data


_BUDGET_RESULT_FIELDS = frozenset(("request_id", "request_sha256", "item_id", "lease_generation",
    "status", "attempts", "reason", "retry_at", "budget_operation_id", "committed_at", "replayed"))


def normalize_budget_request(value):
    """Validate and copy a frozen request; never replace its UUID or lease."""
    from .budget_contract import DEFER_REASONS
    base = {"kind", "request_id", "source", "target", "lease", "recovery_generation", "recovery_stream"}
    if type(value) is not dict or value.get("kind") not in ("budget_defer", "budget_resume"):
        raise ValueError
    fields = base | ({"claim", "reason", "retry_at", "budget_operation_id"}
        if value["kind"] == "budget_defer" else {"defer_request_id"})
    _exact(value, fields)
    _identity(value["source"])
    queue._lease(value["lease"])
    queue._uuid(value["request_id"], "request_id")
    _exact(value["target"], ("library_id", "node_id"))
    for name in ("library_id", "node_id"):
        queue._uuid(value["target"][name], name)
        if value["target"][name] == value["source"][name]:
            raise ValueError
    queue._integer(value["recovery_generation"], "recovery_generation", minimum=0)
    _stream(value["recovery_stream"])
    if value["recovery_stream"]["library_id"] != value["target"]["library_id"]:
        raise ValueError
    if value["kind"] == "budget_defer":
        _claim(value["claim"])
        if type(value["reason"]) is not str or value["reason"] not in DEFER_REASONS:
            raise ValueError
        if value["retry_at"] is not None and queue._stored_time(value["retry_at"]) is None:
            raise ValueError
        if value["budget_operation_id"] is not None:
            queue._uuid(value["budget_operation_id"], "budget_operation_id")
    else:
        queue._uuid(value["defer_request_id"], "defer_request_id")
    return json.loads(_json(value, MAX_CONTROL_BYTES))


def _budget_receipt_shape(value):
    from .budget_contract import DEFER_REASONS
    _exact(value, _BUDGET_RESULT_FIELDS)
    queue._uuid(value["request_id"], "request_id")
    queue._integer(value["item_id"], "item_id")
    queue._integer(value["lease_generation"], "lease_generation")
    queue._integer(value["attempts"], "attempts", minimum=0, maximum=queue.MAX_ATTEMPTS - 1)
    if (type(value["request_sha256"]) is not str or not _SHA256.fullmatch(value["request_sha256"])
            or queue._stored_time(value["committed_at"]) is None or type(value["replayed"]) is not bool):
        raise ValueError
    if value["status"] == "budget_wait":
        if type(value["reason"]) is not str or value["reason"] not in DEFER_REASONS:
            raise ValueError
        if value["retry_at"] is not None and queue._stored_time(value["retry_at"]) is None:
            raise ValueError
        if value["budget_operation_id"] is not None:
            queue._uuid(value["budget_operation_id"], "budget_operation_id")
    elif value["status"] != "queued" or any(value[name] is not None for name in ("reason", "retry_at", "budget_operation_id")):
        raise ValueError


def normalize_budget_receipt(request, value, *, defer_receipt=None):
    """Check the exact saved request and, for resume, its original item."""
    request = normalize_budget_request(request)
    _budget_receipt_shape(value)
    if (value["request_id"] != request["request_id"]
            or value["request_sha256"] != hashlib.sha256(_json(request, MAX_CONTROL_BYTES)).hexdigest()
            ):
        raise ValueError
    if request["kind"] == "budget_defer":
        if (value["status"] != "budget_wait" or value["item_id"] != request["claim"]["item_id"]
                or value["lease_generation"] != request["claim"]["lease_generation"]
                or any(value[name] != request[name] for name in ("reason", "retry_at", "budget_operation_id"))):
            raise ValueError
    else:
        _budget_receipt_shape(defer_receipt)
        if (defer_receipt["request_id"] != request["defer_request_id"] or defer_receipt["status"] != "budget_wait"
                or value["status"] != "queued" or value["item_id"] != defer_receipt["item_id"]
                or value["lease_generation"] != defer_receipt["lease_generation"]
                or value["attempts"] != defer_receipt["attempts"]
                or any(value[name] is not None for name in ("reason", "retry_at", "budget_operation_id"))):
            raise ValueError
    return json.loads(_json(value, MAX_CONTROL_BYTES))


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError
        value[key] = child
    return value


def _nonfinite(_):
    raise ValueError


def _base_url(value):
    with _checking("INVALID_URL"):
        if (type(value) is not str or not value or len(value) > 2048 or not value.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or any(c in value for c in "\\?#%")):
            raise ValueError
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in ("https", "http") or not host or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.netloc.endswith(":") or port is not None and not 1 <= port <= 65535):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if len(host) > 253 or not all(_HOST_LABEL.fullmatch(part) for part in host.split(".")):
                raise ValueError
        path = parsed.path.rstrip("/")
        if parsed.path.endswith("//") or path and (not path.startswith("/") or any(
                not _PATH_PART.fullmatch(part) for part in path[1:].split("/"))):
            raise ValueError
        local = host == "localhost" or address is not None and (address.is_loopback or bool(
            getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback))
        # Avoid even a hosts-file/DNS override of localhost for the QA exception.
        if parsed.scheme == "http" and host == "localhost":
            host = "127.0.0.1"
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        return urlunsplit((parsed.scheme, netloc, path, "", "")), parsed.scheme == "http", bool(local)


class _BorrowedTransport(httpx.BaseTransport):
    def __init__(self, transport, deadline):
        self.transport, self.deadline = transport, deadline

    def handle_request(self, request):
        self.deadline.remaining()
        # Blocking injected transports are trusted test/custom infrastructure:
        # expose the deadline so they can cooperate without background requests.
        request.extensions["reno_sync_deadline"] = self.deadline
        response = self.transport.handle_request(request)
        try:
            self.deadline.remaining()
            return response
        except BaseException:
            response.close()
            raise

    def close(self):
        pass


class SyncClient:
    def __init__(self, base_url, *, token_provider, transport=None, timeout=30, check_cancelled=None):
        self._url, self._http, self._loopback = _base_url(base_url)
        if not callable(token_provider) or transport is not None and not isinstance(transport, httpx.BaseTransport):
            _error("INVALID_REQUEST")
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600 or not math.isfinite(timeout):
            _error("INVALID_REQUEST")
        if check_cancelled is not None and not callable(check_cancelled):
            _error("INVALID_REQUEST")
        self._token_provider, self._transport, self._timeout = token_provider, transport, timeout
        self._check_cancelled = check_cancelled
        from . import sync_source_claim, sync_source_transport17
        self._source_claim_broker = sync_source_claim._Broker(self)
        self._source_transport17_broker = sync_source_transport17._Broker(self)
        self._check_transport()

    def _deadline(self, seconds, extra_check=None):
        def check():
            if self._check_cancelled is not None:
                self._check_cancelled()
            if extra_check is not None and extra_check is not self._check_cancelled:
                extra_check()
        return Deadline(seconds, check)

    @staticmethod
    def _check_deadline(deadline):
        try:
            deadline.remaining()
        except SyncTransportError as exc:
            _error(exc.kind)

    def _check_transport(self):
        if not self._http:
            return
        try:
            flag = config.sync_access_settings()["sync_allow_insecure_test_transport"]
            enabled = flag is True or isinstance(flag, str) and flag.lower() in {"true", "1"}
            if self._loopback and enabled and config.is_isolated_qa():
                return
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass
        _error("INSECURE_TRANSPORT")

    def _token(self):
        if self._check_cancelled is not None:
            try:
                self._check_cancelled()
            except Exception:
                _error("CANCELLED")
        try:
            token = self._token_provider()
            if type(token) is str and 0 < len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token):
                return token
        except Exception:
            pass
        _error("CREDENTIAL")

    def _request(self, endpoint, *, body=None, content=None, headers=None, maximum=MAX_CONTROL_BYTES, _deadline=None, _read_timeout=None):
        deadline = _deadline or self._deadline(self._timeout)
        self._check_deadline(deadline)
        self._check_transport()
        request_headers = {"X-Sync-Token": self._token(), "Accept": "application/json", "Accept-Encoding": "identity"}
        if headers:
            request_headers.update(headers)
        if body is not None:
            with _checking():
                maximum_request = MAX_REQUEST_BYTES if endpoint == "push" else (
                    MAX_INTENT_BYTES if endpoint == "uploads/prepare" else MAX_CONTROL_BYTES)
                content = _json(body, maximum_request)
            request_headers["Content-Type"] = "application/json"
        self._check_deadline(deadline)
        from . import sync_source_claim, sync_source_transport17
        request_base = self._url
        claim_frame = sync_source_claim._RequestFrame(self, endpoint, request_base, content,
            seal=sync_source_claim._FRAME_SEAL)
        claim_frame_token = sync_source_claim._HTTP_FRAME.set(claim_frame)
        claim_call = None
        source_call17 = None
        try:
            claim_call = sync_source_claim._begin(self, endpoint, content, target_url=request_base)
            if claim_call is None:
                source_call17 = sync_source_transport17._begin(self, endpoint, content, target_url=request_base)
            transport = (_BorrowedTransport(self._transport, deadline) if self._transport is not None
                         else DeadlineTransport(deadline))
            with httpx.Client(verify=True, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(self._timeout, read=_read_timeout or self._timeout), transport=transport) as client:
                with client.stream("GET" if endpoint == "handshake" else "POST",
                                   f"{request_base}/api/sync/{endpoint}", headers=request_headers, content=content) as response:
                    self._check_deadline(deadline)
                    if 300 <= response.status_code <= 399:
                        _error("REDIRECT")
                    if response.status_code != 200 and not 400 <= response.status_code <= 599:
                        _error("INVALID_RESPONSE")
                    raw = self._read_response(response, maximum if response.status_code == 200 else MAX_CONTROL_BYTES,
                                              check=lambda: self._check_deadline(deadline))
                    if response.status_code != 200:
                        self._remote_error(response.status_code, raw)
                    with _checking("INVALID_RESPONSE"):
                        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
                        if type(decoded) is not dict:
                            raise ValueError
                        sync_snapshot._check_json(decoded)
                    self._check_deadline(deadline)
            # Successful evidence includes response/client cleanup. A close
            # failure or final cancellation never leaves an activatable ticket.
            self._check_deadline(deadline)
            # No user callback runs between binding the actual decoded object
            # and consuming its original request ticket.
            claim_frame.decoded = decoded
            claim_frame.phase = "parsed_closed"
            sync_source_claim._complete(self, claim_call, decoded)
            sync_source_transport17._complete(self, source_call17, decoded)
            return decoded
        except SyncClientError:
            raise
        except SyncTransportError as exc:
            _error(exc.kind)
        except httpx.TimeoutException:
            _error("TIMEOUT")
        except (httpx.HTTPError, OSError) as exc:
            current, seen = exc, set()
            while current is not None and id(current) not in seen:
                if isinstance(current, ssl.SSLError):
                    _error("TLS")
                seen.add(id(current))
                current = current.__cause__ or current.__context__
            _error("NETWORK")
        except (UnicodeError, ValueError, TypeError):
            _error("INVALID_REQUEST")
        except Exception:
            # Custom transports and stream implementations must not leak a
            # credential-bearing exception through this public boundary.
            _error("NETWORK")
        finally:
            try:
                sync_source_claim._abandon(self, claim_call)
                sync_source_transport17._abandon(self, source_call17)
            finally:
                claim_frame.active = False
                claim_frame.phase = "closed"
                sync_source_claim._HTTP_FRAME.reset(claim_frame_token)

    @staticmethod
    def _read_response(response, maximum, *, check=lambda: None):
        check()
        for header in ("content-length", "content-type", "content-encoding"):
            if len(response.headers.get_list(header)) > 1:
                _error("INVALID_RESPONSE")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json" or response.headers.get("content-encoding") not in (None, "identity"):
            _error("INVALID_RESPONSE")
        length = response.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit() or len(length) > 20:
                _error("INVALID_RESPONSE")
            if int(length) > maximum:
                _error("TOO_LARGE")
        chunks, size = [], 0
        # Real responses remain streamed. MockTransport may return preloaded
        # content, which httpx deliberately marks consumed before send().
        iterator = (response.content,) if response.is_stream_consumed else response.iter_raw()
        for chunk in iterator:
            check()
            size += len(chunk)
            if size > maximum:
                _error("TOO_LARGE")
            chunks.append(chunk)
        if length is not None and size != int(length):
            _error("INVALID_RESPONSE")
        check()
        return b"".join(chunks)

    @staticmethod
    def _remote_error(status, raw):
        with _checking("INVALID_RESPONSE"):
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
            if type(payload) is not dict or set(payload) - {"code", "detail"}:
                raise ValueError
            code = payload.get("code")
            if code is not None and type(code) is not str:
                raise ValueError
        if code in _REMOTE_CODES:
            raise SyncClientError("REMOTE", remote_code=code, remote_status=status) from None
        _error("AUTH" if status in (401, 403) else "REMOTE")

    @staticmethod
    def _base(source, lease):
        _identity(source)
        queue._lease(lease)
        return {"source": source, "lease": lease}

    def handshake(self):
        result = self._request("handshake")
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("target",))
            _identity(result["target"])
        return result["target"]

    def heartbeat(self, source, session_id, lease=None, metrics=None):
        with _checking():
            _identity(source)
            queue._uuid(session_id, "session_id")
            body = {"source": source, "session_id": session_id}
            if lease is not None:
                queue._lease(lease)
                if lease["session_id"] != session_id:
                    raise ValueError
                body["lease"] = lease
            if metrics is not None:
                body["metrics"] = queue._metrics(metrics)
        result = self._request("heartbeat", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("lease", "renewed_claims", "recovery"))
            _recovery(result["recovery"], source["library_id"])
            queue._lease(result["lease"])
            queue._integer(result["renewed_claims"], "renewed_claims", minimum=0)
            if result["lease"]["session_id"] != session_id:
                raise ValueError
            if lease is not None and (any(result["lease"][key] != lease[key] for key in (
                    "session_id", "lease_token", "generation")) or result["lease"]["lease_until"] < lease["lease_until"]):
                raise ValueError
        from . import sync_source_claim
        sync_source_claim._validated(self, "heartbeat", result)
        return result

    def pull(self, source, lease, *, after=0, limit=200, through=None, stream=None, claim_limit=0):
        with _checking():
            body = self._base(source, lease)
            queue._integer(after, "after", minimum=0)
            queue._integer(limit, "limit", maximum=sync_events.MAX_EVENTS)
            queue._integer(claim_limit, "claim_limit", minimum=0, maximum=queue.MAX_CLAIM_BATCH)
            if through is not None:
                queue._integer(through, "through", minimum=after)
            if stream is not None:
                _stream(stream)
            elif after != 0 or through is not None:
                raise ValueError
            body.update(events={"after": after, "limit": limit, "through": through, "stream": stream}, claim_limit=claim_limit)
        result = self._request("pull", body=body, maximum=MAX_PULL_BYTES)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("events", "claims", "recovery"))
            _recovery(result["recovery"], source["library_id"])
            page = result["events"]
            _exact(page, ("events", "next_cursor", "high_watermark", "has_more", "stream"))
            _stream(page["stream"])
            if result["recovery"]["stream"] != page["stream"]:
                raise ValueError
            if stream is not None and stream != page["stream"]:
                raise ValueError
            high, cursor = page["high_watermark"], page["next_cursor"]
            queue._integer(high, "high_watermark", minimum=after)
            queue._integer(cursor, "next_cursor", minimum=after, maximum=high)
            if through is not None and high != through or type(page["has_more"]) is not bool:
                raise ValueError
            events = _events(page["events"])
            if len(events) > limit or len(events) > cursor - after:
                raise ValueError
            if page["has_more"]:
                if not events or not after < cursor < high:
                    raise ValueError
            elif cursor != high:
                raise ValueError
            claims = result["claims"]
            if type(claims) is not list or len(claims) > claim_limit:
                raise ValueError
            seen = set()
            for entry in claims:
                _exact(entry, ("item_id", "url", "attempts", "lease_until", "claim"))
                _claim(entry["claim"])
                queue._integer(entry["item_id"], "item_id")
                queue._integer(entry["attempts"], "attempts", maximum=queue.MAX_ATTEMPTS)
                protocol.validate_ingest_url(entry["url"])
                if (entry["item_id"] in seen or entry["item_id"] != entry["claim"]["item_id"]
                        or queue._stored_time(entry["lease_until"]) is None):
                    raise ValueError
                seen.add(entry["item_id"])
        from . import sync_source_claim
        sync_source_claim._validated(self, "pull", result)
        return result

    def push_events(self, source, lease, events):
        with _checking():
            body = {**self._base(source, lease), "kind": "events", "events": _events(events)}
        result = self._request("push", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("received", "inserted", "duplicates", "projection_changed"))
            for key in ("received", "inserted", "duplicates"):
                queue._integer(result[key], key, minimum=0, maximum=len(events))
            if (result["received"] != len(events) or result["inserted"] + result["duplicates"] != len(events)
                    or type(result["projection_changed"]) is not bool):
                raise ValueError
        return result

    def push_snapshot(self, source, lease, snapshot, claims, *, recovery_generation=0, recovery_stream=None):
        with _checking():
            from .sync_reports import validate_reports
            from .sync_transfer import _bindings
            body = self._base(source, lease)
            checked = sync_snapshot.validate_snapshot(snapshot)
            if checked["version"]["library_id"] != source["library_id"]:
                raise ValueError
            validate_reports(checked["reports"], checked["version"])
            bindings = _bindings(claims, {video["video_id"] for video in checked["videos"]})
            queue._integer(recovery_generation, "recovery generation", minimum=0)
            _stream(recovery_stream)
            body.update(kind="snapshot", snapshot=checked, claims=bindings,
                        recovery_generation=recovery_generation, recovery_stream=recovery_stream)
        result = self._request("push", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("publication_id", "source_version", "local_version", "committed_at", "replayed"))
            sync_snapshot._version(result["source_version"])
            sync_snapshot._version(result["local_version"])
            if (result["publication_id"] != checked["publication_id"] or result["source_version"] != checked["version"]
                    or result["local_version"]["library_id"] == source["library_id"]
                    or queue._stored_time(result["committed_at"]) is None or type(result["replayed"]) is not bool):
                raise ValueError
        return result

    def prepare_uploads(self, source, lease, *, binding, descriptors):
        """Declare every frozen descriptor, including locally cached uploads."""
        with _checking():
            body = self._base(source, lease)
            binding = pins.normalize_binding(binding)
            descriptors = pins._descriptors(descriptors)
            if binding["source_node_id"] != source["node_id"] or binding["source_version"]["library_id"] != source["library_id"]:
                raise ValueError
            body.update(binding=binding, descriptors=descriptors)
        result = self._request("uploads/prepare", body=body, maximum=MAX_INTENT_BYTES)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("binding", "state", "media"))
            if (pins.normalize_binding(result["binding"]) != binding or result["state"] not in ("reserved", "published")
                    or type(result["media"]) is not list):
                raise ValueError
            if result["state"] == "published":
                if result["media"]:
                    raise ValueError
            else:
                expected = {pins.intent_id({**binding, "descriptor": item}) for item in descriptors}
                seen = set()
                for item in result["media"]:
                    _exact(item, ("descriptor", "pin_id", "state"))
                    expected_id = pins.intent_id({**binding, "descriptor": item["descriptor"]})
                    if (item["pin_id"] != expected_id or expected_id not in expected or expected_id in seen
                            or item["state"] not in ("reserved", "stored")):
                        raise ValueError
                    seen.add(expected_id)
                if seen != expected:
                    raise ValueError
        return result

    def abandon_uploads(self, source, lease, *, binding, operation_id):
        """Explicit primitive; the sender must first persist its no-resend fact."""
        with _checking():
            body = self._base(source, lease)
            binding = pins.normalize_binding(binding)
            queue._uuid(operation_id, "operation_id")
            if binding["source_node_id"] != source["node_id"] or binding["source_version"]["library_id"] != source["library_id"]:
                raise ValueError
            body.update(binding=binding, operation_id=operation_id)
        result = self._request("uploads/abandon", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("binding", "operation_id", "state"))
            if (pins.normalize_binding(result["binding"]) != binding
                    or result != {"binding": binding, "operation_id": operation_id, "state": "abandoned"}):
                raise ValueError
        return result

    def publication_receipt(self, source, lease, *, binding, payload_sha256):
        """Read one exact historical publication, without changing its fence."""
        with _checking():
            body = self._base(source, lease)
            binding = pins.normalize_binding(binding)
            if (binding["source_node_id"] != source["node_id"]
                    or binding["source_version"]["library_id"] != source["library_id"]
                    or type(payload_sha256) is not str or not _SHA256.fullmatch(payload_sha256)):
                raise ValueError
            body.update(binding=binding, payload_sha256=payload_sha256)
        result = self._request("uploads/receipt", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("binding", "payload_sha256", "state", "receipt"))
            if (pins.normalize_binding(result["binding"]) != binding
                    or result["payload_sha256"] != payload_sha256 or result["state"] != "published"):
                raise ValueError
            receipt = result["receipt"]
            _exact(receipt, ("publication_id", "source_version", "local_version", "committed_at", "replayed"))
            sync_snapshot._version(receipt["source_version"])
            sync_snapshot._version(receipt["local_version"])
            if (receipt["publication_id"] != binding["publication_id"]
                    or receipt["source_version"] != binding["source_version"]
                    or receipt["local_version"]["library_id"] != binding["recovery_stream"]["library_id"]
                    or receipt["local_version"]["library_epoch"] != binding["recovery_stream"]["library_epoch"]
                    or receipt["local_version"]["library_id"] == source["library_id"]
                    or queue._stored_time(receipt["committed_at"]) is None
                    or type(receipt["replayed"]) is not bool):
                raise ValueError
        return result

    def verify_upload(self, source, lease, *, intent, check_cancelled=None):
        """Bound one cache check independently of the full-library prepare."""
        with _checking():
            body = self._base(source, lease)
            intent = pins.normalize_intent(intent)
            if intent["source_node_id"] != source["node_id"] or intent["source_version"]["library_id"] != source["library_id"]:
                raise ValueError
            body["intent"] = intent
        result = self._request("uploads/verify", body=body,
            _deadline=self._deadline(MAX_VERIFY_TOTAL_SECONDS, check_cancelled), _read_timeout=MAX_VERIFY_TOTAL_SECONDS)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("pin_id", "stored"))
            if result["pin_id"] != pins.intent_id(intent) or type(result["stored"]) is not bool:
                raise ValueError
        return result

    def fail(self, source, lease, claim, error_class, *, recovery_generation=0, recovery_stream=None):
        with _checking():
            body = self._base(source, lease)
            _claim(claim)
            queue._integer(recovery_generation, "recovery generation", minimum=0)
            _stream(recovery_stream)
            if not isinstance(error_class, str) or error_class not in queue.ERROR_CLASSES:
                raise ValueError
            body.update(kind="fail", claim=claim, error_class=error_class,
                        recovery_generation=recovery_generation, recovery_stream=recovery_stream)
        result = self._request("push", body=body)
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("item_id", "status", "attempts", "retry_at", "error_class", "replayed"))
            queue._integer(result["item_id"], "item_id")
            queue._integer(result["attempts"], "attempts", maximum=queue.MAX_ATTEMPTS)
            if (result["item_id"] != claim["item_id"] or result["error_class"] != error_class
                    or type(result["replayed"]) is not bool or result["status"] not in ("retry_wait", "dead")):
                raise ValueError
            if result["status"] == "retry_wait":
                if queue._stored_time(result["retry_at"]) is None:
                    raise ValueError
            elif result["retry_at"] is not None:
                raise ValueError
        return result

    def budget_request(self, request, *, defer_receipt=None):
        """Send the caller's persisted body unchanged, including historical leases."""
        with _checking():
            body = normalize_budget_request(request)
            if body["kind"] == "budget_resume":
                _budget_receipt_shape(defer_receipt)
                if (defer_receipt["request_id"] != body["defer_request_id"]
                        or defer_receipt["status"] != "budget_wait"):
                    raise ValueError
                defer_receipt = json.loads(_json(defer_receipt, MAX_CONTROL_BYTES))
        result = self._request("push", body=body, maximum=MAX_CONTROL_BYTES)
        with _checking("INVALID_RESPONSE"):
            normalized = normalize_budget_receipt(body, result, defer_receipt=defer_receipt)
        from . import sync_source_transport17
        sync_source_transport17._validated(self, "push", result, body, normalized, defer_receipt=defer_receipt)
        return normalized

    def source_control(self, request):
        """Deliver an original persisted source outcome without renewing it."""
        from . import sync_source_control_contract as contract
        with _checking():
            body = contract.normalize_request(request)
        result = self._request("push", body=body, maximum=contract.MAX_CONTROL_BYTES)
        with _checking("INVALID_RESPONSE"):
            normalized = contract.normalize_receipt(body, result)
        from . import sync_source_transport17
        sync_source_transport17._validated(self, "push", result, body, normalized)
        return normalized

    def source_register(self, request):
        return self._source_recovery_control(request, "source_register", "source/register")

    def source_continuity(self, source, target):
        """Observe actual paired continuity; this never requests activation."""
        from . import sync_source_continuity_status_contract as contract
        with _checking():
            body = contract.normalize_request({"kind": "source_continuity", "source": source, "target": target})
        result = self._request("source/continuity", body=body, maximum=contract.MAX_BYTES)
        with _checking("INVALID_RESPONSE"):
            normalized = contract.normalize_result(body, result)
        from . import sync_source_claim
        sync_source_claim._validated(self, "source/continuity", result)
        return normalized

    def source_dispose(self, request, *, defer_receipt=None):
        return self._source_recovery_control(request, "source_dispose", "source/dispose", defer_receipt=defer_receipt)

    def source_readmit(self, request):
        return self._source_recovery_control(request, "source_readmit", "source/readmit")

    def _source_recovery_control(self, request, kind, endpoint, *, defer_receipt=None):
        """Deliver the exact persisted request; never synthesize local authority."""
        from . import sync_source_readmit_contract as contract
        with _checking():
            body = contract.normalize_request(request)
            if body["kind"] != kind:
                raise ValueError
            if kind == "source_dispose" and body["original_request"]["kind"] == "budget_resume" and defer_receipt is not None:
                _budget_receipt_shape(defer_receipt)
                defer_receipt = json.loads(_json(defer_receipt, MAX_CONTROL_BYTES))
        maximum = contract.MAX_READMIT_BYTES if kind == "source_readmit" else contract.MAX_CONTROL_BYTES
        result = self._request(endpoint, body=body, maximum=maximum)
        with _checking("INVALID_RESPONSE"):
            normalized = contract.normalize_result(body, result, defer_receipt=defer_receipt)
        from . import sync_source_transport17
        sync_source_transport17._validated(self, endpoint, result, body, normalized, defer_receipt=defer_receipt)
        return normalized

    def upload_media(self, source, lease, descriptor, path, *, intent, check_cancelled=None):
        deadline = self._deadline(MAX_MEDIA_TOTAL_SECONDS, check_cancelled)
        with _checking():
            body = self._base(source, lease)
            descriptor = protocol.validate_media_descriptor(descriptor)
            value = pins.normalize_intent(intent)
            if (value["descriptor"] != descriptor or value["source_node_id"] != source["node_id"]
                    or value["source_version"]["library_id"] != source["library_id"]):
                raise ValueError
            body["intent"] = value
            header = base64.urlsafe_b64encode(_json(body, 6144)).decode("ascii")
            if len(header) > 8192 or check_cancelled is not None and not callable(check_cancelled):
                raise ValueError
        headers = {"Content-Type": "application/octet-stream", "Content-Length": str(descriptor["byte_size"]),
                   "X-Sync-Media": header}

        def cancelled():
            self._check_deadline(deadline)

        def signature(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        try:
            cancelled()
            file_path = Path(path)
            before = file_path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size != descriptor["byte_size"]:
                _error("MEDIA")
            # Re-reading after a response must reach the file, including tiny
            # files that a BufferedReader could otherwise serve from its cache.
            with file_path.open("rb", buffering=0) as handle:
                expected_path = signature(before)
                opened = os.fstat(handle.fileno())
                expected_handle = signature(opened)
                if not stat.S_ISREG(opened.st_mode) or expected_handle[:3] != expected_path[:3]:
                    _error("MEDIA")

                def unchanged():
                    try:
                        # Python 3.12 Windows lstat/fstat disagree on ctime's
                        # meaning. Compare each source with its own baseline.
                        changed = (signature(os.fstat(handle.fileno())) != expected_handle
                                   or signature(file_path.lstat()) != expected_path)
                    except OSError:
                        _error("MEDIA")
                    if changed:
                        _error("MEDIA")

                def read_chunk():
                    try:
                        return handle.read(CHUNK_BYTES)
                    except OSError:
                        _error("MEDIA")

                def verify_contents():
                    cancelled()
                    unchanged()
                    handle.seek(0)
                    digest, size = hashlib.sha256(), 0
                    while True:
                        cancelled()
                        chunk = read_chunk()
                        cancelled()
                        unchanged()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > descriptor["byte_size"]:
                            _error("MEDIA")
                        digest.update(chunk)
                    cancelled()
                    unchanged()
                    if size != descriptor["byte_size"] or digest.hexdigest() != descriptor["sha256"]:
                        _error("MEDIA")

                verify_contents()
                handle.seek(0)
                completed = False

                def chunks():
                    nonlocal completed
                    size, digest = 0, hashlib.sha256()
                    while True:
                        cancelled()
                        unchanged()
                        chunk = read_chunk()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > descriptor["byte_size"]:
                            _error("MEDIA")
                        digest.update(chunk)
                        yield chunk
                    unchanged()
                    if size != descriptor["byte_size"] or digest.hexdigest() != descriptor["sha256"]:
                        _error("MEDIA")
                    completed = True

                result = self._request("media", content=chunks(), headers=headers, _deadline=deadline)
                cancelled()
                unchanged()
                if not completed:
                    _error("MEDIA")
                # The streamed digest proves what was sent, not what remains
                # locally after the response. Same-size rewrites can preserve
                # both stat timestamps (e.g. writes in one filesystem tick).
                # Keep this final scan inside the original upload deadline.
                verify_contents()
        except SyncClientError:
            raise
        except (OSError, ValueError, TypeError):
            _error("MEDIA")
        with _checking("INVALID_RESPONSE"):
            _exact(result, ("storage_key", "sha256", "published"))
            key = f"sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
            if result["storage_key"] != key or result["sha256"] != descriptor["sha256"] or result["published"] is not False:
                raise ValueError
        return result
