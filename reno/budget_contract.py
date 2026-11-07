"""Pure, bounded P06 policy/time/meter contracts; no storage or transport.

Reported usage is a provider observation, never an invoice or a strict token
capability proof. Missing and malformed observations cannot manufacture zero.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from types import MappingProxyType
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = 1
MAX_UNITS = 2**63 - 1
MAX_INTERVAL_SECONDS = 86400
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_OPERATION_RESULT_BYTES = 64 * 1024 * 1024
ADAPTERS = frozenset({"paas_v4", "glm53_anthropic"})
DEFER_REASONS = frozenset({"daily_limit", "output_limit", "reported_tokens_limit", "attempt_limit",
                         "download_pacing", "clock_rollback", "indeterminate", "result_capacity",
                         "manual_pause", "ledger_unavailable", "unsupported_capability"})
POLICY_DEFAULTS = MappingProxyType({
    "budget_enabled": False, "budget_timezone": "UTC",
    "download_interval_min_seconds": 0, "download_interval_max_seconds": 0,
    "download_daily_videos": None, "download_rate_bytes_per_second": None,
    "model_daily_attempts": None, "model_reported_tokens_stop_at": None,
    "model_daily_output_tokens": None, "model_total_tokens_mode": "reported_only",
    "model_operation_max_attempts": 8,
})
ERROR_CODES = frozenset({"BUDGET_CONTRACT_INVALID", "BUDGET_CONFIG_INVALID", "BUDGET_POLICY_INVALID",
                        "BUDGET_TIMEZONE_UNAVAILABLE", "BUDGET_TIMESTAMP_INVALID", "BUDGET_WINDOW_INVALID",
                        "BUDGET_REQUEST_INVALID", "BUDGET_USAGE_INVALID", "BUDGET_DEFERRED_INVALID",
                        "BUDGET_CAPABILITY_UNSUPPORTED"})
ERROR_FIELDS = frozenset(POLICY_DEFAULTS) | frozenset({"timezone", "local_date", "start_utc", "end_utc",
    "now", "latest_admitted_at", "requested", "adapter", "reason", "resume_at", "operation_id",
    "usage", "quality", "input_tokens", "output_tokens", "total_tokens", "details", "invalid_fields"})
_DETAIL_FIELDS = MappingProxyType({
    "paas_v4": frozenset({"prompt_tokens_details.cached_tokens", "completion_tokens_details.reasoning_tokens"}),
    "glm53_anthropic": frozenset({"cache_creation_input_tokens", "cache_read_input_tokens"}),
})
_USAGE_FIELDS = MappingProxyType({
    "paas_v4": _DETAIL_FIELDS["paas_v4"] | frozenset({"usage", "prompt_tokens", "completion_tokens",
        "total_tokens", "prompt_tokens_details", "completion_tokens_details"}),
    "glm53_anthropic": _DETAIL_FIELDS["glm53_anthropic"] | frozenset({"usage", "input_tokens",
        "output_tokens", "total_tokens"}),
})
_UTC = timezone.utc


class BudgetContractError(ValueError):
    """Only controlled metadata may appear in an exception or public output."""
    def __init__(self, code="BUDGET_CONTRACT_INVALID", *, field=None):
        if type(code) is not str or code not in ERROR_CODES:
            code = "BUDGET_CONTRACT_INVALID"
        if type(field) is not str or field not in ERROR_FIELDS:
            field = None
        self.code, self.field = code, field
        super().__init__(code + (":" + field if field is not None else ""))

    def public(self):
        return {"code": self.code, "field": self.field}


def _fail(code="BUDGET_POLICY_INVALID", field=None):
    raise BudgetContractError(code, field=field) from None


def _number(value, field, *, nullable=False, minimum=0, maximum=MAX_UNITS, code="BUDGET_POLICY_INVALID"):
    if nullable and value is None:
        return value
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code, field)
    return value


def _zone(value, field="budget_timezone"):
    if (type(value) is not str or not value or len(value) > 128 or value != value.strip()
            or "\\" in value or ":" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail("BUDGET_POLICY_INVALID", field)
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        _fail("BUDGET_TIMEZONE_UNAVAILABLE", field)


def _instant(value, field):
    if type(value) is not datetime:
        _fail("BUDGET_TIMESTAMP_INVALID", field)
    try:
        if value.utcoffset() is None:
            _fail("BUDGET_TIMESTAMP_INVALID", field)
        return value.astimezone(_UTC)
    except BudgetContractError:
        raise
    except (ValueError, OverflowError, TypeError):
        _fail("BUDGET_TIMESTAMP_INVALID", field)


def _stamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class BudgetDeferred(Exception):
    """A controlled wait; None resume_at means no automatic timed release."""
    code = "BUDGET_DEFERRED"

    def __init__(self, reason, *, resume_at=None, operation_id=None):
        if type(reason) is not str or reason not in DEFER_REASONS:
            _fail("BUDGET_DEFERRED_INVALID", "reason")
        if operation_id is not None:
            try:
                if type(operation_id) is not str or str(uuid.UUID(operation_id)) != operation_id:
                    raise ValueError
            except (ValueError, AttributeError):
                _fail("BUDGET_DEFERRED_INVALID", "operation_id")
        self.reason = reason
        self.resume_at = None if resume_at is None else _instant(resume_at, "resume_at")
        self.operation_id = operation_id
        super().__init__(self.code + ":" + reason)

    def public(self):
        return {"code": self.code, "reason": self.reason,
                "resume_at": None if self.resume_at is None else _stamp(self.resume_at),
                "operation_id": self.operation_id}


@dataclass(frozen=True, slots=True)
class Policy:
    budget_enabled: bool = False
    budget_timezone: str = "UTC"
    download_interval_min_seconds: int = 0
    download_interval_max_seconds: int = 0
    download_daily_videos: int | None = None
    download_rate_bytes_per_second: int | None = None
    model_daily_attempts: int | None = None
    model_reported_tokens_stop_at: int | None = None
    model_daily_output_tokens: int | None = None
    model_total_tokens_mode: str = "reported_only"
    model_operation_max_attempts: int = 8

    def __post_init__(self):
        if type(self.budget_enabled) is not bool:
            _fail(field="budget_enabled")
        _zone(self.budget_timezone)
        for name in ("download_interval_min_seconds", "download_interval_max_seconds"):
            _number(getattr(self, name), name, maximum=MAX_INTERVAL_SECONDS)
        if self.download_interval_min_seconds > self.download_interval_max_seconds:
            _fail(field="download_interval_min_seconds")
        for name in ("download_daily_videos", "model_daily_attempts", "model_reported_tokens_stop_at",
                     "model_daily_output_tokens"):
            _number(getattr(self, name), name, nullable=True)
        _number(self.download_rate_bytes_per_second, "download_rate_bytes_per_second", nullable=True, minimum=1)
        _number(self.model_operation_max_attempts, "model_operation_max_attempts", minimum=1, maximum=128)
        if type(self.model_total_tokens_mode) is not str or self.model_total_tokens_mode != "reported_only":
            _fail("BUDGET_CAPABILITY_UNSUPPORTED", "model_total_tokens_mode")

    def public(self):
        return {field.name: getattr(self, field.name) for field in fields(self)}


def parse_policy(values):
    if type(values) is Policy:
        return Policy(**values.public())
    if type(values) is not dict or any(type(key) is not str or key not in POLICY_DEFAULTS for key in values):
        _fail()
    return Policy(**values)


def policy_sha256(policy):
    value = {"format_version": CONTRACT_VERSION, "policy": parse_policy(policy).public()}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class Window:
    timezone: str
    local_date: date
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self):
        _zone(self.timezone, "timezone")
        if type(self.local_date) is not date:
            _fail("BUDGET_WINDOW_INVALID", "local_date")
        start, end = _instant(self.start_utc, "start_utc"), _instant(self.end_utc, "end_utc")
        if start >= end:
            _fail("BUDGET_WINDOW_INVALID")
        object.__setattr__(self, "start_utc", start)
        object.__setattr__(self, "end_utc", end)

    @property
    def period_key(self):
        return self.timezone + "/" + self.local_date.isoformat()

    def public(self):
        return {"timezone": self.timezone, "local_date": self.local_date.isoformat(), "period_key": self.period_key,
                "start_utc": _stamp(self.start_utc), "end_utc": _stamp(self.end_utc)}


def period_for(policy, aware_datetime):
    policy = parse_policy(policy)
    instant = _instant(aware_datetime, "now")
    zone = _zone(policy.budget_timezone)
    try:
        day = instant.astimezone(zone).date()
        following = day + timedelta(days=1)
        start = datetime.combine(day, time.min, tzinfo=zone).astimezone(_UTC)
        end = datetime.combine(following, time.min, tzinfo=zone).astimezone(_UTC)
    except (ValueError, OverflowError):
        _fail("BUDGET_WINDOW_INVALID")
    return Window(policy.budget_timezone, day, start, end)


def windows_overlap(left, right):
    if type(left) is not Window or type(right) is not Window:
        _fail("BUDGET_WINDOW_INVALID")
    # Never rebuild historical windows under a new timezone/policy revision.
    return max(left.start_utc, right.start_utc) < min(left.end_utc, right.end_utc)


def validate_admission_clock(now, latest_admitted_at):
    instant = _instant(now, "now")
    if latest_admitted_at is not None and instant < _instant(latest_admitted_at, "latest_admitted_at"):
        raise BudgetDeferred("clock_rollback")


def _adapter(value):
    if type(value) is not str or value not in ADAPTERS:
        _fail("BUDGET_REQUEST_INVALID", "adapter")


def effective_max_tokens(adapter, requested):
    """Actual current payload ceiling, not a provider capability assertion."""
    _adapter(adapter)
    _number(requested, "requested", minimum=1, code="BUDGET_REQUEST_INVALID")
    return max(128000, requested) if adapter == "glm53_anthropic" else requested


def _usage_quality(adapter, input_tokens, output_tokens, total_tokens, details, invalid_fields):
    if invalid_fields:
        return "invalid"
    required = ((input_tokens, output_tokens, total_tokens) if adapter == "paas_v4"
                else (input_tokens, output_tokens))
    if all(number is not None for number in required):
        return "reported"
    if any(number is not None for number in (input_tokens, output_tokens, total_tokens)) or details:
        return "partial"
    return "missing"


@dataclass(frozen=True, slots=True)
class Usage:
    adapter: str
    quality: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    details: tuple[tuple[str, int], ...] = ()
    invalid_fields: tuple[str, ...] = ()

    def __post_init__(self):
        _adapter(self.adapter)
        if type(self.quality) is not str or self.quality not in {"missing", "partial", "reported", "invalid"}:
            _fail("BUDGET_USAGE_INVALID", "quality")
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            _number(getattr(self, name), name, nullable=True, code="BUDGET_USAGE_INVALID")
        if type(self.details) is not tuple:
            _fail("BUDGET_USAGE_INVALID", "details")
        seen = set()
        for detail in self.details:
            if (type(detail) is not tuple or len(detail) != 2 or type(detail[0]) is not str
                    or detail[0] not in _DETAIL_FIELDS[self.adapter] or detail[0] in seen):
                _fail("BUDGET_USAGE_INVALID", "details")
            seen.add(detail[0])
            _number(detail[1], "details", code="BUDGET_USAGE_INVALID")
        if (type(self.invalid_fields) is not tuple or any(type(value) is not str or value not in _USAGE_FIELDS[self.adapter]
                for value in self.invalid_fields) or len(set(self.invalid_fields)) != len(self.invalid_fields)):
            _fail("BUDGET_USAGE_INVALID", "invalid_fields")
        if self.quality != _usage_quality(self.adapter, self.input_tokens, self.output_tokens, self.total_tokens,
                                         self.details, self.invalid_fields):
            _fail("BUDGET_USAGE_INVALID", "quality")

    def public(self):
        return {"adapter": self.adapter, "quality": self.quality, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "total_tokens": self.total_tokens,
                "details": dict(self.details), "invalid_fields": list(self.invalid_fields)}


def parse_usage(adapter, value):
    """Extract only provider fields; malformed usage never triggers a retry."""
    _adapter(adapter)
    if value is None:
        return Usage(adapter, "missing")
    if type(value) is not dict:
        return Usage(adapter, "invalid", invalid_fields=("usage",))
    invalid, details = [], []
    def count(container, key, path=None):
        raw = container.get(key)
        if raw is None:
            return None
        if type(raw) is not int or not 0 <= raw <= MAX_UNITS:
            invalid.append(path or key)
            return None
        return raw
    inputs, outputs = (("prompt_tokens", "completion_tokens") if adapter == "paas_v4"
                       else ("input_tokens", "output_tokens"))
    input_tokens, output_tokens, total_tokens = count(value, inputs), count(value, outputs), count(value, "total_tokens")
    if adapter == "paas_v4":
        for parent, child in (("prompt_tokens_details", "cached_tokens"), ("completion_tokens_details", "reasoning_tokens")):
            nested = value.get(parent)
            if nested is None:
                continue
            if type(nested) is not dict:
                invalid.append(parent)
                continue
            path = parent + "." + child
            number = count(nested, child, path)
            if number is not None:
                details.append((path, number))
    else:
        for name in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            number = count(value, name)
            if number is not None:
                details.append((name, number))
    quality = _usage_quality(adapter, input_tokens, output_tokens, total_tokens, details, invalid)
    return Usage(adapter, quality, input_tokens, output_tokens, total_tokens,
                 tuple(sorted(details)), tuple(sorted(set(invalid))))
