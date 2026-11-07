"""One admitted DNS lookup or fixed-IP HTTP headers opportunity.

The bridge owns budget/worker admission and settlement. This transport never
creates a ledger identity, reads configuration, follows redirects, or retries.
The installed pinned socket/TLS implementation is the existing sync_sources
transport, with its public-address checks and shared DNS semaphore retained.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import source_resolution_contract as contract, sync_sources

DNS_TIMEOUT_SECONDS = 5.0
_SEAL = object()


class _Cancelled(Exception):
    pass


class _Deadline(Exception):
    pass


class _Check:
    def __init__(self, callback):
        self.callback, self.exception = callback, None

    def __call__(self):
        if self.exception is not None:
            raise _Cancelled() from None
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as error:
                self.exception = error
                raise _Cancelled() from None


@dataclass(frozen=True, slots=True, repr=False)
class PreparedResolution:
    plan: contract.ResolutionPlan
    deadline: float
    check: _Check
    dns_timeout: float
    _seal: object = field(default=None)

    @property
    def cancel_exception(self):
        return self.check.exception


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def prepare(plan, *, deadline, check_cancelled=None, dns_timeout=DNS_TIMEOUT_SECONDS):
    """Pure plan/capability checks; no callback, DNS, socket, thread or writes."""
    if (type(plan) is not contract.ResolutionPlan or type(deadline) not in {int, float}
            or not math.isfinite(deadline) or deadline <= 0
            or type(dns_timeout) not in {int, float} or not math.isfinite(dns_timeout) or dns_timeout <= 0
            or check_cancelled is not None and not callable(check_cancelled)):
        raise contract.ResolutionContractError()
    return PreparedResolution(plan, float(deadline), _Check(check_cancelled), float(dns_timeout), _SEAL)


def _ready(prepared, phase):
    if type(prepared) is not PreparedResolution or prepared._seal is not _SEAL or prepared.plan.phase != phase:
        raise contract.ResolutionContractError()


def _remaining(prepared, deadline=None):
    prepared.check()
    remaining = (prepared.deadline if deadline is None else deadline) - time.monotonic()
    if remaining <= 0:
        raise _Deadline()
    return remaining


def _unknown(plan, termination):
    return contract.ResolutionResult(plan.request_sha256, plan.phase, False, termination)


def _dns_failure(plan, *, rejected=False, error="system_failure"):
    return contract.ResolutionResult(plan.request_sha256, "dns", True, "completed", observed_at=_now(),
        failure_class="dns_rejected" if rejected else "dns_failure", dns_error=error)


def _lookup(plan):
    parsed = urlsplit(plan.current_url)
    port = 443 if parsed.scheme == "https" else 80
    try:
        answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except Exception as error:
        classification = "temporary" if isinstance(error, socket.gaierror) and error.errno == socket.EAI_AGAIN else (
            "not_found" if isinstance(error, socket.gaierror) and error.errno in {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
            else "system_failure")
        return _dns_failure(plan, error=classification)
    if type(answers) not in {tuple, list} or not answers:
        return _dns_failure(plan, rejected=True, error="empty")
    if len(answers) > contract.MAX_DNS_ADDRESSES:
        return _dns_failure(plan, rejected=True, error="answer_limit")
    addresses = []
    for answer in answers:
        try:
            if type(answer) not in {tuple, list} or len(answer) != 5:
                raise ValueError()
            family, kind, proto, _, sockaddr = answer
            if (not isinstance(family, int) or type(family) is bool or family not in {socket.AF_INET, socket.AF_INET6}
                    or not isinstance(kind, int) or type(kind) is bool or kind != socket.SOCK_STREAM
                    or type(proto) is not int or type(sockaddr) not in {tuple, list}
                    or len(sockaddr) != (2 if family == socket.AF_INET else 4)
                    or type(sockaddr[1]) is not int or sockaddr[1] != port
                    or family == socket.AF_INET6 and (type(sockaddr[3]) is not int or sockaddr[3] != 0)):
                raise ValueError()
            try:
                address = contract.public_ip(sockaddr[0])
            except contract.ResolutionContractError:
                return _dns_failure(plan, rejected=True, error="non_public")
            if (family == socket.AF_INET) != (":" not in address):
                raise ValueError()
            if address not in addresses:
                addresses.append(address)
        except (ValueError, TypeError, IndexError):
            return _dns_failure(plan, rejected=True, error="invalid_answer")
    return contract.ResolutionResult(plan.request_sha256, "dns", True, "completed", addresses=tuple(addresses), observed_at=_now())


def run_dns(prepared, *, late_sink=None):
    """Exactly one getaddrinfo invocation; timeout retains its thread/slot.

    A late complete result is delivered at most once to the supplied original
    attempt's sink. Sink failures are not hidden network retries or new UUIDs.
    """
    _ready(prepared, "dns")
    if late_sink is not None and not callable(late_sink):
        raise contract.ResolutionContractError()
    plan = prepared.plan
    try:
        _remaining(prepared)
    except _Cancelled:
        return _unknown(plan, "cancelled")
    except _Deadline:
        return _unknown(plan, "timeout")
    slots = sync_sources._DNS_SLOTS
    if not slots.acquire(blocking=False):
        return _unknown(plan, "launch_unknown")
    condition, state = threading.Condition(), {"result": None, "waiting": True}

    def lookup():
        try:
            result = _lookup(plan)
        except BaseException:
            result = _unknown(plan, "launch_unknown")
        finally:
            slots.release()  # The actual OS lookup has returned, never at timeout.
        with condition:
            state["result"] = result
            late = not state["waiting"]
            condition.notify_all()
        if late and late_sink is not None:
            try:
                late_sink(result)
            except BaseException:
                # An unavailable ledger keeps the original dispatched attempt
                # indeterminate. No unbounded callback retry or second lookup.
                pass

    try:
        thread = threading.Thread(target=lookup, name="reno-resolution-dns", daemon=True)
        thread.start()
    except BaseException:
        slots.release()
        return _unknown(plan, "launch_unknown")
    deadline = min(prepared.deadline, time.monotonic() + prepared.dns_timeout)
    while True:
        with condition:
            if state["result"] is not None:
                state["waiting"] = False
                return state["result"]
        try:
            remaining = _remaining(prepared, deadline)
        except (_Cancelled, _Deadline) as error:
            with condition:
                state["waiting"] = False
                # A completion racing with cancellation still commits the real
                # known result; it never becomes a cache miss/new lookup.
                if state["result"] is not None:
                    return state["result"]
            return _unknown(plan, "cancelled" if isinstance(error, _Cancelled) else "timeout")
        with condition:
            if state["result"] is not None:
                state["waiting"] = False
                return state["result"]
            condition.wait(timeout=min(0.05, remaining))


def _retry_after(headers):
    values = headers.get_list("retry-after")
    if len(values) == 1 and values[0].isascii() and values[0].isdigit() and len(values[0]) <= 10:
        seconds = int(values[0])
        if seconds <= 2**31 - 1:
            return seconds
    return None  # Unsupported values are not copied or silently clamped.


def _header_result(plan, status, headers):
    raw = headers.raw
    size = sum(len(key) + len(value) + 4 for key, value in raw)
    if size > contract.MAX_HEADER_BYTES:
        return _unknown(plan, "header_limit")
    digest = hashlib.sha256(b"".join(key + b": " + value + b"\r\n" for key, value in raw)).hexdigest()
    values = dict(request_sha256=plan.request_sha256, phase="headers", event_complete=True, termination="completed",
        headers_complete=True, transport_closed=True, status=status, header_bytes=size,
        header_sha256=digest, retry_after_seconds=_retry_after(headers), observed_at=_now())
    if status in contract.REDIRECT_STATUSES:
        locations = headers.get_list("location")
        if len(locations) != 1 or not locations[0] or len(locations[0]) > 4096:
            return contract.ResolutionResult(**values, failure_class="redirect_invalid")
        next_url = urljoin(plan.current_url, locations[0])
        try:
            contract.validate_url(next_url)
            result = contract.ResolutionResult(**values, next_url=next_url)
            contract._check_result(plan, result)
            return result
        except (ValueError, TypeError):
            return contract.ResolutionResult(**values, failure_class="redirect_invalid")
    if not 200 <= status < 300:
        return contract.ResolutionResult(**values, failure_class="http_status")
    try:
        supported = contract.resource_reference(plan.current_url)
    except contract.ResolutionContractError:
        supported = None
    return contract.ResolutionResult(**values, **({} if supported is not None else {"failure_class": "resource_invalid"}))


def run_headers(prepared):
    """One pinned GET, complete headers only; never consume the response body."""
    _ready(prepared, "headers")
    plan = prepared.plan
    try:
        timeout = min(5.0, _remaining(prepared))
        parsed = urlsplit(plan.current_url)
        authority = f"[{plan.peer_ip}]" if ":" in plan.peer_ip else plan.peer_ip
        pinned = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
        # Reuse the original direct-literal socket backend/TLS/header deadline
        # transport; no second DNS call, proxy environment, or loose transport.
        transport = sync_sources._HeaderTransport(prepared.deadline, prepared.check)
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(timeout),
                          verify=True, transport=transport) as client:
            with client.stream("GET", pinned,
                    headers={"Host": parsed.hostname, "Accept-Encoding": "identity", "User-Agent": "Reno-Sync/1"},
                    extensions={"sni_hostname": parsed.hostname}) as response:
                status, headers = response.status_code, response.headers
                # No post-response owner/config/cancellation check here: the
                # bridge settles the paid observation before the next gate.
        return _header_result(plan, status, headers)
    except _Cancelled:
        return _unknown(plan, "cancelled")
    except _Deadline:
        return _unknown(plan, "timeout")
    except Exception as error:
        if isinstance(error, httpx.TimeoutException) or getattr(error, "code", None) == "SYNC_SOURCE_TIMEOUT":
            termination = "timeout"
        elif getattr(error, "code", None) == "SYNC_SOURCE_INVALID":
            termination = "header_limit"
        else:
            termination = "network_unknown"
        return _unknown(plan, termination)


def run(prepared, *, late_sink=None):
    """Convenience facade; the bridge has already won this one phase's CAS."""
    _ready(prepared, prepared.plan.phase)
    return run_dns(prepared, late_sink=late_sink) if prepared.plan.phase == "dns" else run_headers(prepared)
