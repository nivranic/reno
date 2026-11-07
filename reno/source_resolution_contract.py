"""Pure private DNS and headers-only source opportunity contracts.

No configuration, database, network, or process is consulted here. Receipt
references are validated for shape only; the budget bridge authorizes them.
"""
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit
import uuid

FORMAT_VERSION = 1
RESOLVER_CONTRACT = "public_dns_pinned_headers_v1"
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_HEADER_BYTES = 16 * 1024
MAX_REDIRECTS = 5
MAX_DNS_ADDRESSES = 64
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_PHASES = frozenset({"dns", "headers"})
_TERMINATIONS = frozenset({"completed", "timeout", "cancelled", "launch_unknown", "network_unknown", "header_limit", "cleanup_unknown"})
_FAILURES = frozenset({"dns_failure", "dns_rejected", "http_status", "redirect_invalid", "resource_invalid", "header_invalid"})
_DNS_ERRORS = frozenset({"temporary", "not_found", "system_failure", "empty", "answer_limit", "invalid_answer", "non_public"})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")


class ResolutionContractError(ValueError):
    def __init__(self, code="SOURCE_RESOLUTION_CONTRACT_INVALID"):
        self.code = code
        super().__init__(code)

    def public(self):
        return {"code": self.code, "detail": "来源解析证明无效，请核对原任务。"}


def _fail(code="SOURCE_RESOLUTION_CONTRACT_INVALID"):
    raise ResolutionContractError(code) from None


def canonical_json(value):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        text.encode("utf-8")
        return text
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _fail()


def _integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _digest(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _pair(identifier, digest):
    if (identifier is None) != (digest is None):
        _fail()
    if identifier is not None:
        try:
            if type(identifier) is not str or str(uuid.UUID(identifier)) != identifier:
                _fail()
        except (ValueError, TypeError, AttributeError):
            _fail()
        _digest(digest)


def platform(url):
    try:
        host = urlsplit(url).hostname
        if type(host) is not str:
            _fail()
    except (ValueError, TypeError, AttributeError):
        _fail()
    if host == "b23.tv" or host == "bilibili.com" or host.endswith(".bilibili.com"):
        return "bilibili"
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return "douyin"
    _fail()


def validate_url(value):
    # Mirrors the existing pure validate_ingest_url boundary, without importing
    # sync_protocol's migrations/configuration dependency into this pure module.
    if type(value) is not str or not value or len(value) > 4096:
        _fail()
    decoded = value
    for _ in range(16):
        if any(ord(c) <= 32 or 127 <= ord(c) <= 159 or c.isspace() or c == "\\" for c in decoded):
            _fail()
        following = unquote(decoded)
        if following == decoded:
            break
        decoded = following
    else:
        _fail()
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment or not parsed.netloc or parsed.netloc.endswith(":")):
            _fail()
        if port not in {None, 80 if parsed.scheme == "http" else 443}:
            _fail()
        if len(host) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
            _fail()
        platform(value)
        value.encode("utf-8")
        return value
    except (ValueError, UnicodeError):
        _fail()


def public_ip(value):
    try:
        if type(value) is not str:
            _fail()
        address = ipaddress.ip_address(value)
        if ("%" in value or not address.is_global or address.is_multicast or address.is_reserved
                or address.version == 6 and (address.ipv4_mapped or address.sixtofour or address.teredo)):
            _fail()
        return str(address)
    except ValueError:
        _fail()


def _timestamp(value):
    try:
        if type(value) is not str or _STAMP.fullmatch(value) is None:
            _fail()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
            _fail()
        return value
    except (ValueError, TypeError):
        _fail()


def resource_reference(url):
    validate_url(url)
    parsed, family = urlsplit(url), platform(url)
    allowed = {"bilibili": {"bilibili.com", "www.bilibili.com"}, "douyin": {"douyin.com", "www.douyin.com"}}
    if parsed.hostname not in allowed[family]:
        return None
    match = re.fullmatch(r"/video/([bB][vV][A-Za-z0-9]+)/?" if family == "bilibili" else r"/video/([0-9]+)/?", parsed.path)
    if match is None:
        return None
    if family == "douyin":
        return family, match[1], None
    try:
        pages = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=200) if key == "p"]
        if len(pages) > 1 or pages and re.fullmatch(r"[1-9][0-9]{0,8}", pages[0]) is None:
            _fail()
        return family, "BV" + match[1][2:], pages[0] if pages else None
    except ValueError:
        _fail()


@dataclass(frozen=True, slots=True, repr=False)
class ResolutionPlan:
    phase: str
    submitted_url: str
    current_url: str
    input_sha256: str
    max_attempts: int
    hop_index: int = 0
    generation: int = 0
    visited_urls: tuple[str, ...] = ()
    previous_attempt_id: str | None = None
    previous_settlement_sha256: str | None = None
    dns_attempt_id: str | None = None
    dns_settlement_sha256: str | None = None
    peer_ip: str | None = None
    refresh_attempt_id: str | None = None
    refresh_settlement_sha256: str | None = None

    def __post_init__(self):
        if type(self.phase) is not str or self.phase not in _PHASES:
            _fail()
        validate_url(self.submitted_url)
        validate_url(self.current_url)
        if platform(self.submitted_url) != platform(self.current_url):
            _fail()
        _digest(self.input_sha256)
        _integer(self.max_attempts, 1, 128)
        _integer(self.hop_index, 0, MAX_REDIRECTS)
        _integer(self.generation, 0, 1)
        if type(self.visited_urls) is not tuple or len(self.visited_urls) != self.hop_index:
            _fail()
        if self.hop_index == 0 and self.current_url != self.submitted_url or self.visited_urls and self.visited_urls[0] != self.submitted_url:
            _fail()
        for url in self.visited_urls:
            validate_url(url)
            if platform(url) != platform(self.submitted_url):
                _fail()
        if len(set(self.visited_urls)) != len(self.visited_urls) or self.current_url in self.visited_urls:
            _fail()
        if (any(urlsplit(url).scheme == "https" for url in (self.submitted_url, *self.visited_urls))
                and urlsplit(self.current_url).scheme != "https"):
            _fail()
        for pair in ((self.previous_attempt_id, self.previous_settlement_sha256),
                     (self.dns_attempt_id, self.dns_settlement_sha256),
                     (self.refresh_attempt_id, self.refresh_settlement_sha256)):
            _pair(*pair)
        if bool(self.generation) != (self.refresh_attempt_id is not None):
            _fail()
        if self.phase == "headers":
            if self.dns_attempt_id is None or self.peer_ip is None or self.peer_ip != public_ip(self.peer_ip):
                _fail()
            if self.previous_attempt_id is None:
                object.__setattr__(self, "previous_attempt_id", self.dns_attempt_id)
                object.__setattr__(self, "previous_settlement_sha256", self.dns_settlement_sha256)
            if (self.previous_attempt_id, self.previous_settlement_sha256) != (self.dns_attempt_id, self.dns_settlement_sha256):
                _fail()
        elif any(value is not None for value in (self.dns_attempt_id, self.dns_settlement_sha256, self.peer_ip)):
            _fail()
        if (self.hop_index or self.generation) and self.previous_attempt_id is None:
            _fail()
        # Reserve space for the bounded result and envelope before a physical
        # opportunity. Long UTF-8 URL paths must not make every paid result
        # intrinsically impossible to retain in the 64 KiB private envelope.
        if len(canonical_json(_plan_json(self)).encode("utf-8")) > MAX_ENVELOPE_BYTES - 8 * 1024:
            _fail()

    @property
    def kind(self):
        return "source_resolution"

    @property
    def request_sha256(self):
        return hashlib.sha256(canonical_json(_plan_json(self)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class ResolutionResult:
    request_sha256: str
    phase: str
    event_complete: bool
    termination: str
    addresses: tuple[str, ...] | None = None
    dns_error: str | None = None
    headers_complete: bool = False
    transport_closed: bool = False
    status: int | None = None
    next_url: str | None = None
    header_bytes: int = 0
    header_sha256: str = EMPTY_SHA256
    retry_after_seconds: int | None = None
    observed_at: str | None = None
    failure_class: str | None = None

    def __post_init__(self):
        _digest(self.request_sha256)
        if type(self.phase) is not str or self.phase not in _PHASES:
            _fail()
        if type(self.termination) is not str or self.termination not in _TERMINATIONS:
            _fail()
        if any(type(value) is not bool for value in (self.event_complete, self.headers_complete, self.transport_closed)):
            _fail()
        if self.failure_class is not None and (type(self.failure_class) is not str or self.failure_class not in _FAILURES):
            _fail()
        if self.dns_error is not None and (type(self.dns_error) is not str or self.dns_error not in _DNS_ERRORS):
            _fail()
        _integer(self.header_bytes, maximum=MAX_HEADER_BYTES)
        _digest(self.header_sha256)
        if self.header_bytes == 0 and self.header_sha256 != EMPTY_SHA256:
            _fail()
        if self.observed_at is not None:
            _timestamp(self.observed_at)
        if self.event_complete and (self.termination != "completed" or self.observed_at is None):
            _fail()
        if self.termination == "completed" and not self.event_complete:
            _fail()
        if self.phase == "dns":
            if (self.headers_complete or self.transport_closed or self.status is not None or self.next_url is not None
                    or self.header_bytes or self.retry_after_seconds is not None
                    or self.failure_class not in {None, "dns_failure", "dns_rejected"}):
                _fail()
            if self.addresses is not None:
                if (type(self.addresses) is not tuple or not 1 <= len(self.addresses) <= MAX_DNS_ADDRESSES
                        or any(type(value) is not str or public_ip(value) != value for value in self.addresses)
                        or len(set(self.addresses)) != len(self.addresses)):
                    _fail()
            if self.event_complete and ((self.failure_class is None) != (self.addresses is not None)
                    or (self.failure_class is not None) != (self.dns_error is not None)):
                _fail()
            if (self.failure_class == "dns_failure" and self.dns_error not in {"temporary", "not_found", "system_failure"}
                    or self.failure_class == "dns_rejected" and self.dns_error not in {"empty", "answer_limit", "invalid_answer", "non_public"}):
                _fail()
            if not self.event_complete and any(value is not None for value in (self.addresses, self.dns_error, self.failure_class)):
                _fail()
        else:
            if self.addresses is not None or self.dns_error is not None or self.failure_class in {"dns_failure", "dns_rejected"}:
                _fail()
            if self.status is not None:
                _integer(self.status, 100, 599)
            if self.next_url is not None:
                validate_url(self.next_url)
            if self.retry_after_seconds is not None:
                _integer(self.retry_after_seconds, maximum=2**31 - 1)
            if self.event_complete:
                if not self.headers_complete or not self.transport_closed or self.status is None:
                    _fail()
                if self.failure_class is None:
                    if self.status in REDIRECT_STATUSES:
                        if self.next_url is None:
                            _fail()
                    elif not 200 <= self.status < 300 or self.next_url is not None:
                        _fail()
                elif self.next_url is not None:
                    _fail()
                if self.failure_class == "http_status" and (200 <= self.status < 300 or self.status in REDIRECT_STATUSES):
                    _fail()

    @property
    def response_complete(self):
        return self.event_complete and self.termination == "completed" and (self.phase == "dns" or self.headers_complete and self.transport_closed)

    @property
    def successful(self):
        return self.response_complete and self.failure_class is None

    @property
    def unknown(self):
        return not self.response_complete

    @property
    def retryable(self):
        return self.response_complete and (self.phase == "dns" and self.failure_class == "dns_failure"
            or self.phase == "headers" and self.failure_class == "http_status" and (self.status in {408, 429} or self.status >= 500))


def _plan_json(plan):
    return {field.name: list(plan.visited_urls) if field.name == "visited_urls" else getattr(plan, field.name) for field in fields(plan)}


def _result_json(result):
    return {field.name: list(result.addresses) if field.name == "addresses" and result.addresses is not None else getattr(result, field.name) for field in fields(result)}


def _check_result(plan, result):
    if type(plan) is not ResolutionPlan or type(result) is not ResolutionResult or (result.request_sha256, result.phase) != (plan.request_sha256, plan.phase):
        _fail()
    if result.successful and plan.phase == "headers":
        if result.next_url is not None:
            if (platform(result.next_url) != platform(plan.submitted_url) or result.next_url in (*plan.visited_urls, plan.current_url)
                    or plan.hop_index >= MAX_REDIRECTS or urlsplit(plan.current_url).scheme == "https" and urlsplit(result.next_url).scheme != "https"):
                _fail()
        elif resource_reference(plan.current_url) is None:
            _fail()


def encode_envelope(plan, result, *, input_sha256=None):
    _check_result(plan, result)
    if input_sha256 is not None and input_sha256 != plan.input_sha256:
        _fail()
    value = {"format_version": FORMAT_VERSION, "resolver_contract": RESOLVER_CONTRACT, "kind": "source_resolution",
        "phase": plan.phase, "max_attempts": plan.max_attempts, "input_sha256": plan.input_sha256,
        "request_sha256": plan.request_sha256, "plan": _plan_json(plan), "result": _result_json(result)}
    text = canonical_json(value)
    if len(text.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        _fail()
    return text


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail()
        value[key] = item
    return value


def _decode(text, *, request_sha256, input_sha256, phase, max_attempts):
    _digest(request_sha256)
    _digest(input_sha256)
    _integer(max_attempts, 1, 128)
    if type(phase) is not str or phase not in _PHASES:
        _fail()
    try:
        if type(text) is not str or len(text.encode("utf-8")) > MAX_ENVELOPE_BYTES:
            _fail()
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=lambda _: _fail())
        if (type(value) is not dict or set(value) != {"format_version", "resolver_contract", "kind", "phase", "max_attempts", "input_sha256", "request_sha256", "plan", "result"}
                or type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION
                or value["resolver_contract"] != RESOLVER_CONTRACT or value["kind"] != "source_resolution"
                or value["phase"] != phase or type(value["max_attempts"]) is not int or value["max_attempts"] != max_attempts
                or value["input_sha256"] != input_sha256 or value["request_sha256"] != request_sha256
                or canonical_json(value) != text):
            _fail()
        plan_data, result_data = value["plan"], value["result"]
        if (type(plan_data) is not dict or set(plan_data) != {field.name for field in fields(ResolutionPlan)}
                or type(result_data) is not dict or set(result_data) != {field.name for field in fields(ResolutionResult)}
                or type(plan_data["visited_urls"]) is not list
                or result_data["addresses"] is not None and type(result_data["addresses"]) is not list):
            _fail()
        plan_data = {**plan_data, "visited_urls": tuple(plan_data["visited_urls"])}
        if result_data["addresses"] is not None:
            result_data = {**result_data, "addresses": tuple(result_data["addresses"])}
        plan, result = ResolutionPlan(**plan_data), ResolutionResult(**result_data)
        if (plan.input_sha256, plan.phase, plan.max_attempts, plan.request_sha256) != (input_sha256, phase, max_attempts, request_sha256):
            _fail()
        _check_result(plan, result)
        return plan, result
    except ResolutionContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, KeyError):
        _fail()


def decode_envelope(text, *, request_sha256, input_sha256, phase, max_attempts):
    return _decode(text, request_sha256=request_sha256, input_sha256=input_sha256, phase=phase, max_attempts=max_attempts)[1]


def decode_plan(text, *, request_sha256, input_sha256, phase, max_attempts):
    return _decode(text, request_sha256=request_sha256, input_sha256=input_sha256, phase=phase, max_attempts=max_attempts)[0]


def validate_retry_envelope(text, *, request_sha256, input_sha256, phase, max_attempts):
    result = decode_envelope(text, request_sha256=request_sha256, input_sha256=input_sha256, phase=phase, max_attempts=max_attempts)
    if not result.retryable:
        _fail()
    return result
