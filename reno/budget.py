"""Private operational budget ledger; every SQL unit owns a short SH/SQL scope.

This controls local admission, not provider billing or remote exactly-once.
Only the winner of reserved -> dispatching may enter an external adapter.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import sqlite3
import uuid

from . import budget_contract as contract, budget_writer_witness as writer_scope, config, db, storage_lifecycle
from .budget_schema import KINDS, SOURCE_KINDS

MAX_RESULT_BYTES = contract.MAX_RESULT_BYTES
MAX_OPERATION_RESULT_BYTES = contract.MAX_OPERATION_RESULT_BYTES
_OUTCOMES = frozenset({"full_result", "http_retry", "repair", "terminal_error", "source_step_complete", "source_retry", "resolution_retry"})
_STEP_KINDS = frozenset({"download", "source_metadata", "source_resolution"})
_SOURCE_RETRY_KINDS = frozenset({"download", "source_metadata", "source_enumeration"})
_ERRORS = frozenset({"BUDGET_IDENTITY_INVALID", "BUDGET_REQUEST_INVALID", "BUDGET_LEDGER_INVALID",
    "BUDGET_REQUEST_CONFLICT", "BUDGET_RESERVATION_CONFLICT", "BUDGET_DISPATCH_TOKEN_CONFLICT",
    "BUDGET_SETTLEMENT_CONFLICT", "BUDGET_STATE_CONFLICT", "BUDGET_BUSINESS_TRANSACTION_ACTIVE"})
_SNAPSHOT_KEYS = frozenset({"format_version", "policy", "window", "units", "result_bound",
                          "pacing_slot", "admitted_at", "adapter"})
_SNAPSHOT_V2_KEYS = _SNAPSHOT_KEYS | {"kind", "download_segment"}
_SNAPSHOT_V3_KEYS = _SNAPSHOT_V2_KEYS | {"source_contract_version"}
_SNAPSHOT_V4_KEYS = _SNAPSHOT_V2_KEYS | {"resolver_contract_version", "resolver_phase", "resolver_max_attempts"}
_METER_COLUMNS = ("attempt_id", "operation_id", "ordinal", "request_sha256", "state",
    "download_segment",
    "initial_reservation_json", "initial_reservation_sha256", "current_reservation_json",
    "current_reservation_sha256", "current_policy_sha256", "current_period_key",
    "current_window_start_utc", "current_window_end_utc", "first_dispatch_json", "first_dispatch_sha256",
    "dispatch_token_sha256", "dispatched_at", "response_complete", "outcome", "observed_usage_json",
    "usage_quality", "provider_request_id", "result_sha256", "result_bytes", "result_storage_state", "settlement_sha256")
_METER_PROJECTION = ",".join("a." + name for name in _METER_COLUMNS)
_UTC = timezone.utc


class BudgetLedgerError(ValueError):
    def __init__(self, code="BUDGET_LEDGER_INVALID"):
        self.code = code if type(code) is str and code in _ERRORS else "BUDGET_LEDGER_INVALID"
        super().__init__(self.code)

    def public(self):
        return {"code": self.code}


def _fail(code="BUDGET_LEDGER_INVALID"):
    raise BudgetLedgerError(code) from None


def _uuid(value, code="BUDGET_IDENTITY_INVALID"):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail(code)
    except (ValueError, AttributeError):
        _fail(code)
    return value


def _sha(value, code="BUDGET_REQUEST_INVALID"):
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        _fail(code)
    return value


def _integer(value, *, minimum=0, maximum=contract.MAX_UNITS, code="BUDGET_REQUEST_INVALID"):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)
    return value


def _text(value, maximum, *, nullable=False):
    if nullable and value is None:
        return None
    try:
        if type(value) is not str or not value or "\0" in value or len(value.encode("utf-8")) > maximum:
            _fail("BUDGET_IDENTITY_INVALID")
    except UnicodeError:
        _fail("BUDGET_IDENTITY_INVALID")
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now(value):
    return contract._instant(datetime.now(_UTC) if value is None else value, "now")


def _stamp(value):
    return contract._stamp(value)


def _datetime(value):
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if _stamp(contract._instant(instant, "now")) != value:
            _fail()
        return instant
    except (ValueError, TypeError, AttributeError, contract.BudgetContractError):
        _fail()


def _policy(value):
    return contract.parse_policy(config.budget_settings() if value is None else value)


@dataclass(frozen=True, slots=True)
class OperationSpec:
    continuation_id: str
    execution_generation: int
    kind: str
    suboperation_key: str
    input_sha256: str
    root_job_id: int | None = None

    def __post_init__(self):
        _uuid(self.continuation_id)
        _integer(self.execution_generation, minimum=1, code="BUDGET_IDENTITY_INVALID")
        if type(self.kind) is not str or self.kind not in KINDS:
            _fail("BUDGET_IDENTITY_INVALID")
        _text(self.suboperation_key, 512)
        _sha(self.input_sha256, "BUDGET_IDENTITY_INVALID")
        if self.root_job_id is not None:
            _integer(self.root_job_id, minimum=1, code="BUDGET_IDENTITY_INVALID")


@dataclass(frozen=True, slots=True)
class AttemptSpec:
    request_sha256: str
    ordinal: int = 1
    adapter: str | None = None
    effective_max_tokens: int | None = None
    download_segment: str | None = None
    source_contract_version: int | None = None
    resolver_contract_version: int | None = None
    resolver_phase: str | None = None
    resolver_max_attempts: int | None = None

    def __post_init__(self):
        _sha(self.request_sha256)
        _integer(self.ordinal, minimum=1, maximum=128)
        if self.adapter is not None:
            contract._adapter(self.adapter)
        if self.effective_max_tokens is not None:
            _integer(self.effective_max_tokens, minimum=1)
            if self.adapter is not None and contract.effective_max_tokens(self.adapter, self.effective_max_tokens) != self.effective_max_tokens:
                _fail("BUDGET_REQUEST_INVALID")
        if self.download_segment is not None and (type(self.download_segment) is not str
                or self.download_segment not in {"video", "audio"}):
            _fail("BUDGET_REQUEST_INVALID")
        if self.source_contract_version is not None and (type(self.source_contract_version) is not int
                or self.source_contract_version != 1 or self.adapter is not None or self.effective_max_tokens is not None):
            _fail("BUDGET_REQUEST_INVALID")
        resolver = (self.resolver_contract_version, self.resolver_phase, self.resolver_max_attempts)
        if any(value is not None for value in resolver):
            if (type(self.resolver_contract_version) is not int or self.resolver_contract_version != 1
                    or type(self.resolver_phase) is not str or self.resolver_phase not in {"dns", "headers"}
                    or self.source_contract_version is not None or self.adapter is not None
                    or self.effective_max_tokens is not None or self.download_segment is not None):
                _fail("BUDGET_REQUEST_INVALID")
            _integer(self.resolver_max_attempts, minimum=1, maximum=128)


@dataclass(frozen=True, slots=True)
class Reservation:
    operation_id: str
    attempt_id: str
    ordinal: int
    state: str
    reservation_sha256: str
    revision: int
    reservation_held: bool

    def public(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class Dispatch:
    reservation: Reservation
    won: bool

    def public(self):
        return {"reservation": self.reservation.public(), "won": self.won}


@dataclass(frozen=True, slots=True)
class Result:
    attempt_id: str
    response_complete: bool
    outcome: str | None
    usage: contract.Usage | None
    storage_state: str
    result_sha256: str | None
    result_bytes: int
    text: str | None
    settlement_sha256: str | None = None

    @property
    def usage_quality(self):
        return "missing" if self.usage is None else self.usage.quality

    @property
    def result_storage_state(self):
        return self.storage_state

    def public(self):
        # The body and provider request ID never cross the public projection.
        return {"attempt_id": self.attempt_id, "response_complete": self.response_complete,
                "outcome": self.outcome, "usage_quality": self.usage_quality,
                "usage": None if self.usage is None else self.usage.public(),
                "result_storage_state": self.storage_state, "result_sha256": self.result_sha256,
                "result_bytes": self.result_bytes}


@dataclass(frozen=True, slots=True)
class AttemptView:
    ordinal: int
    attempt_id: str
    request_sha256: str
    state: str
    response_complete: bool
    outcome: str | None
    result_storage_state: str
    usage_quality: str
    download_segment: str | None = None
    source_contract_version: int | None = None
    resolver_contract_version: int | None = None
    resolver_phase: str | None = None
    resolver_max_attempts: int | None = None

    def public(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__
                if not name.startswith("resolver_") or self.resolver_contract_version is not None}


@dataclass(frozen=True, slots=True)
class OperationView:
    operation_id: str
    max_attempts: int
    state: str
    wait_reason: str | None
    resume_at: datetime | None
    revision: int
    latest_ordinal: int
    attempts: tuple[AttemptView, ...]

    def public(self):
        return {"operation_id": self.operation_id, "max_attempts": self.max_attempts, "state": self.state,
                "wait_reason": self.wait_reason, "resume_at": None if self.resume_at is None else _stamp(self.resume_at),
                "revision": self.revision, "latest_ordinal": self.latest_ordinal,
                "attempts": [attempt.public() for attempt in self.attempts]}


@contextmanager
def _connection(profile, *, write=False, business_connection=None):
    if business_connection is not None:
        if type(business_connection) is not sqlite3.Connection or business_connection.in_transaction:
            _fail("BUDGET_BUSINESS_TRANSACTION_ACTIVE")
    if type(profile) is not storage_lifecycle.StorageProfile:
        _fail("BUDGET_IDENTITY_INVALID")
    try:
        # The resource factory owns SH, its original connection/BEGIN and
        # cleanup. It neither borrows this caller's business connection nor
        # supplies Home admission or a paid request opportunity by itself.
        with writer_scope.connection(profile, write=write) as con:
            yield con
    except (db.SchemaVersionError, storage_lifecycle.StorageLifecycleError):
        raise contract.BudgetDeferred("ledger_unavailable") from None
    except sqlite3.DatabaseError:
        _fail()


def _application_guard(con, profile, callback):
    """Run the original callback once within this exact owned transaction."""
    writer = writer_scope.require_writer(con, profile=profile)
    callback(con)
    writer_scope.check(writer, connection=con, profile=profile)


def _reservation(row):
    return Reservation(row["operation_id"], row["attempt_id"], row["ordinal"], row["state"],
                       row["current_reservation_sha256"], row["reservation_revision"], bool(row["reservation_held"]))


def _usage(raw):
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if _json(value) != raw or set(value) != {"adapter", "quality", "input_tokens", "output_tokens",
                                              "total_tokens", "details", "invalid_fields"}:
            _fail()
        return contract.Usage(value["adapter"], value["quality"], value["input_tokens"], value["output_tokens"],
            value["total_tokens"], tuple(sorted(value["details"].items())), tuple(value["invalid_fields"]))
    except (ValueError, TypeError, AttributeError, KeyError):
        _fail()


def _settlement_receipt(request_sha256, outcome, usage, result_sha256, result_bytes, provider_request_id):
    return {"format_version": 1, "request_sha256": request_sha256, "response_complete": True,
            "outcome": outcome, "usage": None if usage is None else usage.public(),
            "result_sha256": result_sha256, "result_bytes": result_bytes, "provider_request_id": provider_request_id}


def _source_retry_proof(row, text, *, code="BUDGET_LEDGER_INVALID"):
    # This parser is pure and owns no configuration, database or transport.
    from .source_contract import SourceContractError, validate_retry_envelope
    try:
        validate_retry_envelope(text, request_sha256=row["request_sha256"], input_sha256=row["input_sha256"],
                                kind=row["kind"], segment=row["download_segment"])
    except SourceContractError:
        _fail(code)


def _resolution_proof(row, text, *, retry=False, outcome=None, code="BUDGET_LEDGER_INVALID"):
    from .source_resolution_contract import ResolutionContractError, decode_envelope, validate_retry_envelope
    snapshot = _snapshot(row)
    try:
        parser = validate_retry_envelope if retry else decode_envelope
        result = parser(text, request_sha256=row["request_sha256"], input_sha256=row["input_sha256"],
                        phase=snapshot["resolver_phase"], max_attempts=snapshot["resolver_max_attempts"])
        outcome = row["outcome"] if outcome is None else outcome
        if not result.response_complete or (outcome == "source_step_complete" and not result.successful):
            _fail(code)
        if outcome == "terminal_error" and result.successful:
            _fail(code)
        return result
    except ResolutionContractError:
        _fail(code)


def _verify_result(row, *, with_body):
    if row["outcome"] == "source_step_complete" and (row["kind"] not in _STEP_KINDS
            or not row["response_complete"] or row["result_sha256"] is None or row["result_bytes"] <= 0):
        _fail()
    snapshot = _snapshot(row)
    if row["outcome"] == "resolution_retry" and (snapshot.get("resolver_contract_version") != 1
            or row["kind"] != "source_resolution" or not row["response_complete"]
            or row["result_sha256"] is None or row["result_bytes"] <= 0):
        _fail()
    if snapshot.get("resolver_contract_version") == 1 and row["response_complete"] and row["outcome"] not in {
            "source_step_complete", "resolution_retry", "terminal_error"}:
        _fail()
    if row["outcome"] == "source_retry" and (row["kind"] not in _SOURCE_RETRY_KINDS
            or snapshot.get("source_contract_version") != 1 or not row["response_complete"]
            or row["result_sha256"] is None or row["result_bytes"] <= 0):
        _fail()
    if snapshot.get("source_contract_version") == 1 and row["outcome"] in {"http_retry", "repair"}:
        _fail()
    usage = _usage(row["observed_usage_json"])
    if row["usage_quality"] != ("missing" if usage is None else usage.quality):
        _fail()
    if row["response_complete"]:
        receipt = _settlement_receipt(row["request_sha256"], row["outcome"], usage, row["result_sha256"],
                                      row["result_bytes"], row["provider_request_id"])
        if _digest(receipt) != row["settlement_sha256"]:
            _fail()
    if with_body and row["result_storage_state"] == "stored":
        try:
            raw = row["result_text"].encode("utf-8")
            if len(raw) != row["result_bytes"] or hashlib.sha256(raw).hexdigest() != row["result_sha256"]:
                _fail()
        except (AttributeError, UnicodeError):
            _fail()
        if row["outcome"] == "source_retry":
            _source_retry_proof(row, row["result_text"])
        if snapshot.get("resolver_contract_version") == 1:
            _resolution_proof(row, row["result_text"], retry=row["outcome"] == "resolution_retry")
    return usage


def _result(row):
    usage = row["_usage"] if type(row) is dict and row.get("_body_verified") else _verify_result(row, with_body=True)
    return Result(row["attempt_id"], bool(row["response_complete"]), row["outcome"],
                  usage, row["result_storage_state"], row["result_sha256"],
                  row["result_bytes"], row["result_text"], row["settlement_sha256"])


def _snapshot(row, prefix="current"):
    if prefix == "current" and type(row) is dict and "_snapshot" in row:
        return row["_snapshot"]
    raw, expected = row[prefix + "_reservation_json"], row[prefix + "_reservation_sha256"]
    try:
        value = json.loads(raw)
        if type(value) is not dict or _json(value) != raw or _digest(value) != expected:
            _fail()
        version = value.get("format_version")
        if (type(version) is not int or version not in (1, 2, 3, 4)
                or set(value) != {1: _SNAPSHOT_KEYS, 2: _SNAPSHOT_V2_KEYS, 3: _SNAPSHOT_V3_KEYS, 4: _SNAPSHOT_V4_KEYS}[version]):
            _fail()
        contract.parse_policy(value["policy"])
        window = value["window"]
        if type(window) is not dict or set(window) != {"timezone", "local_date", "period_key", "start_utc", "end_utc"}:
            _fail()
        start, end = _datetime(window["start_utc"]), _datetime(window["end_utc"])
        if start >= end or window["period_key"] != window["timezone"] + "/" + window["local_date"]:
            _fail()
        _datetime(value["admitted_at"])
        units = value["units"]
        names = {"model_attempts", "reserved_output_tokens", "download_videos"}
        if version >= 2:
            names.add("source_requests")
        if type(units) is not dict or set(units) != names:
            _fail()
        for count in units.values():
            _integer(count)
        _integer(value["result_bound"])
        if value["adapter"] is not None:
            contract._adapter(value["adapter"])
        kind = row["kind"]
        if kind not in KINDS or (version == 1 and kind not in {"model", "download"}):
            _fail()
        if (units["model_attempts"] != int(kind == "model") or units["download_videos"] != int(kind == "download")
                or (kind == "model" and (value["adapter"] is None or units["reserved_output_tokens"] < 1))
                or (kind != "model" and (value["adapter"] is not None or units["reserved_output_tokens"] != 0))):
            _fail()
        if version >= 2:
            segment = value["download_segment"]
            if (value["kind"] != kind or units["source_requests"] != int(kind in SOURCE_KINDS)
                    or segment != row["download_segment"]
                    or (kind == "download" and (type(segment) is not str or segment not in {"video", "audio"}))
                    or (kind != "download" and segment is not None)):
                _fail()
        elif row["download_segment"] is not None:
            _fail()
        if version == 3 and (type(value["source_contract_version"]) is not int
                or value["source_contract_version"] != 1 or kind not in _SOURCE_RETRY_KINDS):
            _fail()
        if version == 4:
            if (kind != "source_resolution" or type(value["resolver_contract_version"]) is not int
                    or value["resolver_contract_version"] != 1 or type(value["resolver_phase"]) is not str
                    or value["resolver_phase"] not in {"dns", "headers"}):
                _fail()
            _integer(value["resolver_max_attempts"], minimum=1, maximum=128, code="BUDGET_LEDGER_INVALID")
            if value["resolver_max_attempts"] != row["operation_max_attempts"]:
                _fail()
        if value["pacing_slot"] is not None:
            _datetime(value["pacing_slot"])
        if prefix == "current" and (row["current_period_key"] != window["period_key"]
            or row["current_window_start_utc"] != window["start_utc"] or row["current_window_end_utc"] != window["end_utc"]
            or row["current_policy_sha256"] != contract.policy_sha256(value["policy"])):
            _fail()
        return value
    except (ValueError, TypeError, AttributeError, KeyError, contract.BudgetContractError):
        _fail()


def _facts(row, *, with_body=False):
    initial = _snapshot(row, "initial")
    snapshot = _snapshot(row)
    if (initial["adapter"] != snapshot["adapter"]
            or initial["units"]["reserved_output_tokens"] != snapshot["units"]["reserved_output_tokens"]
            or initial.get("download_segment") != snapshot.get("download_segment")
            or initial.get("source_contract_version") != snapshot.get("source_contract_version")
            or any(initial.get(key) != snapshot.get(key) for key in ("resolver_contract_version", "resolver_phase", "resolver_max_attempts"))):
        _fail()
    if row["state"] in {"dispatching", "indeterminate", "settled"}:
        try:
            raw = row["first_dispatch_json"]
            first = json.loads(raw)
            if (type(first) is not dict or set(first) != {"format_version", "reservation", "attempt_id",
                    "ordinal", "request_sha256", "dispatch_token_sha256", "dispatched_at"}
                    or _json(first) != raw or _digest(first) != row["first_dispatch_sha256"]
                    or type(first["format_version"]) is not int or first["format_version"] != snapshot["format_version"]
                    or first["reservation"] != snapshot or first["attempt_id"] != row["attempt_id"]
                    or type(first["ordinal"]) is not int or first["ordinal"] != row["ordinal"]
                    or first["request_sha256"] != row["request_sha256"]
                    or first["dispatch_token_sha256"] != row["dispatch_token_sha256"]
                    or first["dispatched_at"] != row["dispatched_at"]):
                _fail()
            _datetime(first["dispatched_at"])
        except (ValueError, TypeError, AttributeError, KeyError):
            _fail()
    result = dict(row)
    result["_snapshot"] = snapshot
    result["_usage"] = _verify_result(row, with_body=with_body)
    result["_body_verified"] = with_body
    return result


def _attempt(con, attempt_id, request_sha256=None):
    _uuid(attempt_id, "BUDGET_REQUEST_INVALID")
    row = con.execute("SELECT a.*,o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts FROM budget_attempt a JOIN budget_operation o USING(operation_id) WHERE a.attempt_id=?", (attempt_id,)).fetchone()
    if row is None:
        _fail("BUDGET_STATE_CONFLICT")
    if request_sha256 is not None and row["request_sha256"] != _sha(request_sha256):
        _fail("BUDGET_REQUEST_CONFLICT")
    return _facts(row, with_body=True)


def _library(con):
    row = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
    if row is None:
        _fail("BUDGET_IDENTITY_INVALID")
    return _uuid(row[0])


def _identity(spec, library_id):
    identity = {"format_version": 1, "library_id": library_id, "continuation_id": spec.continuation_id,
        "execution_generation": spec.execution_generation, "kind": spec.kind,
        "suboperation_key": spec.suboperation_key, "input_sha256": spec.input_sha256}
    identity_sha = _digest(identity)
    operation_id = str(uuid.uuid5(uuid.UUID(library_id), "budget-operation:v1:" + identity_sha))
    return identity, identity_sha, operation_id


def _check_root(con, spec):
    if spec.root_job_id is not None:
        job = con.execute("SELECT continuation_id,execution_generation FROM job WHERE job_id=?", (spec.root_job_id,)).fetchone()
        if job is None or job[0] != spec.continuation_id or job[1] != spec.execution_generation:
            _fail("BUDGET_IDENTITY_INVALID")


def _details(operation):
    try:
        raw = operation["wait_details_json"]
        value = json.loads(raw)
        required = {"download_interval_seconds"} if operation["kind"] == "download" else set()
        if type(value) is not dict or set(value) != required or _json(value) != raw:
            _fail()
        if required:
            _integer(value["download_interval_seconds"], maximum=contract.MAX_INTERVAL_SECONDS)
        return value
    except (ValueError, TypeError, KeyError):
        _fail()


def _operation_facts(row):
    spec = OperationSpec(row["continuation_id"], row["execution_generation"], row["kind"], row["suboperation_key"],
                         row["input_sha256"], row["root_job_id"])
    _, sha, operation_id = _identity(spec, _uuid(row["library_id"]))
    if row["identity_sha256"] != sha or row["operation_id"] != operation_id:
        _fail("BUDGET_IDENTITY_INVALID")
    _details(row)
    if (row["state"] == "waiting" and row["wait_reason"] not in contract.DEFER_REASONS
            or row["state"] != "waiting" and (row["wait_reason"] is not None or row["resume_at"] is not None)):
        _fail()
    if row["resume_at"] is not None:
        _datetime(row["resume_at"])
    return row


def _find_operation(con, spec):
    library_id = _library(con)
    _check_root(con, spec)
    identity, identity_sha, operation_id = _identity(spec, library_id)
    row = con.execute("SELECT * FROM budget_operation WHERE identity_sha256=?", (identity_sha,)).fetchone()
    if row is not None:
        _operation_facts(row)
        if row["operation_id"] != operation_id or any(row[name] != value for name, value in identity.items() if name != "format_version"):
            _fail("BUDGET_IDENTITY_INVALID")
    return row


def _operation(con, spec, settings, now):
    library_id = _library(con)
    _, identity_sha, operation_id = _identity(spec, library_id)
    row = _find_operation(con, spec)
    row = con.execute("SELECT * FROM budget_operation WHERE identity_sha256=?", (identity_sha,)).fetchone()
    if row is None:
        details = {}
        if spec.kind == "download":
            details["download_interval_seconds"] = settings.download_interval_min_seconds + secrets.randbelow(
                settings.download_interval_max_seconds - settings.download_interval_min_seconds + 1)
        con.execute("""INSERT INTO budget_operation
            (operation_id,library_id,identity_sha256,continuation_id,execution_generation,kind,suboperation_key,
             input_sha256,root_job_id,max_attempts,wait_details_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (operation_id, library_id, identity_sha, spec.continuation_id,
            spec.execution_generation, spec.kind, spec.suboperation_key, spec.input_sha256, spec.root_job_id,
            config.source_resolution_attempt_limit() if spec.kind == "source_resolution" else settings.model_operation_max_attempts,
            _json(details), _stamp(now), _stamp(now)))
        row = con.execute("SELECT * FROM budget_operation WHERE operation_id=?", (operation_id,)).fetchone()
    return _operation_facts(row)


def _wait(con, operation, reason, now, resume_at=None, attempt_row=None, snapshot=None):
    con.execute("""UPDATE budget_operation SET state='waiting',wait_reason=?,resume_at=?,
        revision=revision+1,updated_at=? WHERE operation_id=?""", (reason, None if resume_at is None else _stamp(resume_at),
        _stamp(now), operation["operation_id"]))
    if attempt_row is not None and attempt_row["state"] == "reserved":
        if snapshot is not None:
            _replace_reservation(con, attempt_row, snapshot, held=False)
        elif attempt_row["reservation_held"]:
            con.execute("""UPDATE budget_attempt SET reservation_held=0,reservation_revision=reservation_revision+1,
                updated_at=? WHERE attempt_id=?""", (_stamp(now), attempt_row["attempt_id"]))
    return contract.BudgetDeferred(reason, resume_at=resume_at, operation_id=operation["operation_id"])


def _open_operation(con, operation_id, now):
    con.execute("""UPDATE budget_operation SET state='open',wait_reason=NULL,resume_at=NULL,
        revision=revision+1,updated_at=? WHERE operation_id=?""", (_stamp(now), operation_id))


def _make_snapshot(settings, window, request, kind, now, pacing_slot):
    # Only an exact existing v1 media request may reach here without a segment.
    legacy = kind == "download" and request.download_segment is None
    snapshot = {"format_version": 4 if request.resolver_contract_version == 1 else 3 if request.source_contract_version == 1 else 1 if legacy else 2,
        "policy": settings.public(), "window": window.public(),
        "units": {"model_attempts": int(kind == "model"),
                  "reserved_output_tokens": request.effective_max_tokens or 0, "download_videos": int(kind == "download")},
        "result_bound": MAX_RESULT_BYTES, "pacing_slot": None if pacing_slot is None else _stamp(pacing_slot),
        "admitted_at": _stamp(now), "adapter": request.adapter}
    if not legacy:
        snapshot.update(kind=kind, download_segment=request.download_segment)
        snapshot["units"]["source_requests"] = int(kind in SOURCE_KINDS)
    if request.source_contract_version == 1:
        snapshot["source_contract_version"] = 1
    if request.resolver_contract_version == 1:
        snapshot.update(resolver_contract_version=1, resolver_phase=request.resolver_phase,
                        resolver_max_attempts=request.resolver_max_attempts)
    return snapshot


def _replace_reservation(con, row, snapshot, *, held=True):
    raw, sha = _json(snapshot), _digest(snapshot)
    if raw != row["current_reservation_json"] or bool(row["reservation_held"]) != held:
        window = snapshot["window"]
        con.execute("""UPDATE budget_attempt SET current_reservation_json=?,current_reservation_sha256=?,
            current_policy_sha256=?,current_period_key=?,current_window_start_utc=?,current_window_end_utc=?,
            reservation_revision=reservation_revision+1,reservation_held=?,updated_at=? WHERE attempt_id=?""",
            (raw, sha, contract.policy_sha256(snapshot["policy"]), window["period_key"], window["start_utc"],
             window["end_utc"], int(held), snapshot["admitted_at"], row["attempt_id"]))


def _counts(con, window, *, exclude_attempt=None):
    rows = con.execute(f"""SELECT {_METER_PROJECTION},o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts FROM budget_attempt a JOIN budget_operation o USING(operation_id)
        WHERE a.state!='released' AND (a.state!='reserved' OR a.reservation_held=1)
        AND a.current_window_start_utc<? AND a.current_window_end_utc>?""",
        (_stamp(window.end_utc), _stamp(window.start_utc))).fetchall()
    models, processes, logical, validated = [], [], set(), {}
    sources = {kind: [] for kind in SOURCE_KINDS}
    for raw in rows:
        if raw["attempt_id"] == exclude_attempt:
            continue
        row = _facts(raw)
        validated[row["attempt_id"]] = row
        if row["kind"] == "model":
            models.append(row)
        elif row["kind"] == "download":
            processes.append(row)
        elif row["kind"] in sources:
            sources[row["kind"]].append(row)
        else:
            _fail()
    # First media dispatch is the logical admission even if a later retry moved
    # into another day. Include its original window only, never a carry chain.
    first_downloads = con.execute(f"""SELECT {_METER_PROJECTION},o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts
        FROM budget_attempt a JOIN budget_operation o USING(operation_id) WHERE o.kind='download'
        AND a.dispatched_at IS NOT NULL AND a.ordinal=(SELECT MIN(b.ordinal) FROM budget_attempt b
        WHERE b.operation_id=a.operation_id AND b.dispatched_at IS NOT NULL)""").fetchall()
    dispatched_operations = set()
    for raw in first_downloads:
        row = validated.get(raw["attempt_id"])
        if row is None:
            row = _facts(raw)
        dispatched_operations.add(row["operation_id"])
        if row["current_window_start_utc"] < _stamp(window.end_utc) and row["current_window_end_utc"] > _stamp(window.start_utc):
            logical.add(row["operation_id"])
    for row in processes:
        if row["operation_id"] not in dispatched_operations:
            logical.add(row["operation_id"])
    return models, processes, logical, sources


def _admission(con, operation, request, settings, now, existing=None):
    window = contract.period_for(settings, now)
    pacing_slot = None
    details = _details(operation)
    if operation["kind"] == "download":
        sampled = details["download_interval_seconds"]
        latest = con.execute("""SELECT MAX(a.dispatched_at) FROM budget_attempt a JOIN budget_operation o
            USING(operation_id) WHERE o.kind='download'""").fetchone()[0]
        if latest is not None:
            pacing_slot = _datetime(latest) + timedelta(seconds=sampled)
    # A rejected migration has no new admission time; its previous successful
    # admission remains available to the global rollback fence.
    stamp_time = now if existing is None else _datetime(existing["last_admitted_at"])
    snapshot = _make_snapshot(settings, window, request, operation["kind"], stamp_time, pacing_slot)
    latest = con.execute("SELECT MAX(last_admitted_at) FROM budget_attempt").fetchone()[0]
    if latest is not None and now < _datetime(latest):
        return snapshot, _wait(con, operation, "clock_rollback", now, attempt_row=existing, snapshot=snapshot)
    if operation["kind"] == "model" and settings.budget_enabled and settings.model_daily_output_tokens is not None:
        return snapshot, _wait(con, operation, "unsupported_capability", now, attempt_row=existing, snapshot=snapshot)
    models, _, logical, _ = _counts(con, window, exclude_attempt=None if existing is None else existing["attempt_id"])
    if settings.budget_enabled:
        if operation["kind"] == "model":
            if settings.model_daily_attempts is not None and len(models) >= settings.model_daily_attempts:
                return snapshot, _wait(con, operation, "daily_limit", now, window.end_utc, existing, snapshot)
            reported = sum(usage.total_tokens for row in models
                if (usage := row["_usage"]) is not None and usage.total_tokens is not None)
            if settings.model_reported_tokens_stop_at is not None and reported >= settings.model_reported_tokens_stop_at:
                return snapshot, _wait(con, operation, "reported_tokens_limit", now, window.end_utc, existing, snapshot)
        elif operation["kind"] == "download":
            already_dispatched = con.execute("SELECT 1 FROM budget_attempt WHERE operation_id=? AND dispatched_at IS NOT NULL",
                (operation["operation_id"],)).fetchone() is not None
            if (not already_dispatched and operation["operation_id"] not in logical
                    and settings.download_daily_videos is not None and len(logical) >= settings.download_daily_videos):
                return snapshot, _wait(con, operation, "daily_limit", now, window.end_utc, existing, snapshot)
    paid_missing = con.execute("""SELECT 1 FROM budget_attempt WHERE operation_id=?
        AND result_storage_state IN ('oversize','capacity_blocked','retired') LIMIT 1""", (operation["operation_id"],)).fetchone()
    stored = con.execute("""SELECT COALESCE(SUM(result_bytes),0) FROM budget_attempt
        WHERE operation_id=? AND result_storage_state='stored'""", (operation["operation_id"],)).fetchone()[0]
    pending = con.execute("""SELECT COUNT(*) FROM budget_attempt WHERE operation_id=?
        AND state IN ('reserved','dispatching','indeterminate') AND reservation_held=1 AND attempt_id!=?""",
        (operation["operation_id"], "" if existing is None else existing["attempt_id"])).fetchone()[0]
    if paid_missing or stored + (pending + 1) * MAX_RESULT_BYTES > MAX_OPERATION_RESULT_BYTES:
        return snapshot, _wait(con, operation, "result_capacity", now, attempt_row=existing, snapshot=snapshot)
    if settings.budget_enabled and pacing_slot is not None and now < pacing_slot:
        return snapshot, _wait(con, operation, "download_pacing", now, pacing_slot, existing, snapshot)
    snapshot["admitted_at"] = _stamp(now)
    return snapshot, None


def reserve(spec, attempt, *, profile, policy=None, now=None, business_connection=None,
            _allow_indeterminate_retry=False, reservation_guard=None):
    if type(spec) is not OperationSpec or type(attempt) is not AttemptSpec or type(_allow_indeterminate_retry) is not bool:
        _fail("BUDGET_REQUEST_INVALID")
    if reservation_guard is not None and not callable(reservation_guard):
        _fail("BUDGET_REQUEST_INVALID")
    if (spec.kind == "model" and (attempt.adapter is None or attempt.effective_max_tokens is None)
            or spec.kind != "model" and (attempt.adapter is not None or attempt.effective_max_tokens is not None)
            or spec.kind != "download" and attempt.download_segment is not None
            or attempt.source_contract_version is not None and spec.kind not in _SOURCE_RETRY_KINDS
            or attempt.resolver_contract_version is not None and spec.kind != "source_resolution"):
        _fail("BUDGET_REQUEST_INVALID")
    settings = None if policy is None else _policy(policy)
    if now is not None:
        _now(now)
    deferred, result = None, None
    with _connection(profile, write=True, business_connection=business_connection) as con:
        # Admission may have waited for SH or the SQL writer across a policy
        # edit/day boundary. Only this acquired transaction's snapshot applies.
        settings = _policy(None) if settings is None else settings
        instant = _now(now)
        operation = _find_operation(con, spec)
        old = None if operation is None else con.execute(
            "SELECT * FROM budget_attempt WHERE operation_id=? AND ordinal=?",
            (operation["operation_id"], attempt.ordinal)).fetchone()
        if old is not None:
            old = _attempt(con, old["attempt_id"], attempt.request_sha256)
            previous = _snapshot(old)
            if (previous["adapter"] != attempt.adapter or previous["units"]["reserved_output_tokens"] != (attempt.effective_max_tokens or 0)
                    or old["download_segment"] != attempt.download_segment
                    or previous.get("source_contract_version") != attempt.source_contract_version
                    or any(previous.get(key) != getattr(attempt, key) for key in ("resolver_contract_version", "resolver_phase", "resolver_max_attempts"))):
                _fail("BUDGET_REQUEST_CONFLICT")
            if old["state"] != "reserved":
                result = _reservation(old)
            else:
                if reservation_guard is not None:
                    _application_guard(con, profile, reservation_guard)
                snapshot, deferred = _admission(con, operation, attempt, settings, instant, old)
                if deferred is None:
                    # A held replay in the same policy/window is not a new slot.
                    if (not old["reservation_held"] or previous["policy"] != snapshot["policy"]
                            or previous["window"] != snapshot["window"] or previous["pacing_slot"] != snapshot["pacing_slot"]):
                        _replace_reservation(con, old, snapshot)
                        con.execute("UPDATE budget_attempt SET last_admitted_at=? WHERE attempt_id=?",
                                    (_stamp(instant), old["attempt_id"]))
                    _open_operation(con, operation["operation_id"], instant)
                    result = _reservation(_attempt(con, old["attempt_id"]))
        else:
            if reservation_guard is not None:
                _application_guard(con, profile, reservation_guard)
            if operation is None:
                operation = _operation(con, spec, settings, instant)
            if attempt.resolver_contract_version == 1 and attempt.resolver_max_attempts != operation["max_attempts"]:
                _fail("BUDGET_REQUEST_CONFLICT")
            if spec.kind == "download" and attempt.download_segment is None:
                _fail("BUDGET_REQUEST_INVALID")
            latest = con.execute("SELECT * FROM budget_attempt WHERE operation_id=? ORDER BY ordinal DESC LIMIT 1",
                                 (operation["operation_id"],)).fetchone()
            if latest is not None:
                latest = _attempt(con, latest["attempt_id"])
            if latest is not None and _snapshot(latest).get("source_contract_version") != attempt.source_contract_version:
                _fail("BUDGET_REQUEST_CONFLICT")
            if latest is not None and _snapshot(latest).get("resolver_contract_version") != attempt.resolver_contract_version:
                _fail("BUDGET_REQUEST_CONFLICT")
            if attempt.ordinal > operation["max_attempts"]:
                deferred = _wait(con, operation, "attempt_limit", instant)
            elif attempt.ordinal != (1 if latest is None else latest["ordinal"] + 1):
                _fail("BUDGET_REQUEST_CONFLICT")
            elif latest is not None and latest["state"] in {"dispatching", "indeterminate"} and not _allow_indeterminate_retry:
                deferred = _wait(con, operation, "indeterminate", instant)
            elif latest is not None and latest["outcome"] in {"source_retry", "resolution_retry"} and latest["result_storage_state"] != "stored":
                deferred = _wait(con, operation, "result_capacity", instant)
            elif latest is not None and not (latest["state"] == "released" or
                latest["state"] == "settled" and latest["response_complete"] and latest["outcome"] in {"http_retry", "repair"}
                or latest["state"] == "settled" and latest["response_complete"] and latest["outcome"] == "source_step_complete"
                    and latest["result_storage_state"] == "stored"
                or latest["state"] == "settled" and latest["response_complete"] and latest["outcome"] in {"source_retry", "resolution_retry"}
                    and latest["result_storage_state"] == "stored"
                or _allow_indeterminate_retry and latest["state"] in {"dispatching", "indeterminate"}):
                _fail("BUDGET_STATE_CONFLICT")
            else:
                snapshot, deferred = _admission(con, operation, attempt, settings, instant)
                if deferred is None:
                    attempt_id, window = str(uuid.uuid4()), snapshot["window"]
                    raw, sha = _json(snapshot), _digest(snapshot)
                    con.execute("""INSERT INTO budget_attempt (attempt_id,operation_id,ordinal,request_sha256,download_segment,state,
                        initial_reservation_json,initial_reservation_sha256,current_reservation_json,current_reservation_sha256,
                        current_policy_sha256,current_period_key,current_window_start_utc,current_window_end_utc,
                        last_admitted_at,created_at,updated_at) VALUES (?,?,?,?,?, 'reserved',?,?,?,?,?,?,?,?,?,?,?)""",
                        (attempt_id, operation["operation_id"], attempt.ordinal, attempt.request_sha256, attempt.download_segment, raw, sha, raw, sha,
                         contract.policy_sha256(settings), window["period_key"], window["start_utc"], window["end_utc"],
                         _stamp(instant), _stamp(instant), _stamp(instant)))
                    _open_operation(con, operation["operation_id"], instant)
                    result = _reservation(_attempt(con, attempt_id))
    if deferred is not None:
        raise deferred
    return result


def _cas(row, reservation_sha256, expected_revision):
    _sha(reservation_sha256)
    _integer(expected_revision)
    if row["current_reservation_sha256"] != reservation_sha256 or row["reservation_revision"] != expected_revision:
        _fail("BUDGET_RESERVATION_CONFLICT")


def mark_dispatching(attempt_id, reservation_sha256, caller_token, *, expected_revision,
                     profile, policy=None, now=None, business_connection=None, dispatch_guard=None):
    _uuid(caller_token, "BUDGET_REQUEST_INVALID")
    if dispatch_guard is not None and not callable(dispatch_guard):
        _fail("BUDGET_REQUEST_INVALID")
    settings = None if policy is None else _policy(policy)
    if now is not None:
        _now(now)
    deferred, result = None, None
    token_sha = hashlib.sha256(caller_token.encode("ascii")).hexdigest()
    with _connection(profile, write=True, business_connection=business_connection) as con:
        settings = _policy(None) if settings is None else settings
        instant = _now(now)
        row = _attempt(con, attempt_id)
        if row["state"] != "reserved":
            result = Dispatch(_reservation(row), False)
        else:
            _cas(row, reservation_sha256, expected_revision)
            if con.execute("SELECT 1 FROM budget_attempt WHERE dispatch_token_sha256=?", (token_sha,)).fetchone():
                _fail("BUDGET_DISPATCH_TOKEN_CONFLICT")
            operation = con.execute("SELECT * FROM budget_operation WHERE operation_id=?", (row["operation_id"],)).fetchone()
            snapshot = _snapshot(row)
            request = AttemptSpec(row["request_sha256"], row["ordinal"], snapshot["adapter"],
                                  snapshot["units"]["reserved_output_tokens"] or None, row["download_segment"],
                                  snapshot.get("source_contract_version"), snapshot.get("resolver_contract_version"),
                                  snapshot.get("resolver_phase"), snapshot.get("resolver_max_attempts"))
            current, deferred = _admission(con, operation, request, settings, instant, row)
            if deferred is None:
                # Trusted consumers check owner/pause/lineage against this exact
                # writer snapshot. Exceptions roll back without dispatch rights.
                if dispatch_guard is not None:
                    _application_guard(con, profile, dispatch_guard)
                _replace_reservation(con, row, current)
                first = {"format_version": current["format_version"], "reservation": current, "attempt_id": attempt_id,
                         "ordinal": row["ordinal"], "request_sha256": row["request_sha256"],
                         "dispatch_token_sha256": token_sha, "dispatched_at": _stamp(instant)}
                cursor = con.execute("""UPDATE budget_attempt SET state='dispatching',first_dispatch_json=?,
                    first_dispatch_sha256=?,dispatch_token_sha256=?,dispatched_at=?,last_admitted_at=?,updated_at=?
                    WHERE attempt_id=? AND state='reserved'""", (_json(first), _digest(first), token_sha,
                    _stamp(instant), _stamp(instant), _stamp(instant), attempt_id))
                if cursor.rowcount != 1:
                    _fail("BUDGET_STATE_CONFLICT")
                _open_operation(con, row["operation_id"], instant)
                result = Dispatch(_reservation(_attempt(con, attempt_id)), True)
    if deferred is not None:
        raise deferred
    return result


def release_reserved(attempt_id, reservation_sha256, *, expected_revision, profile, now=None, business_connection=None):
    instant = _now(now)
    with _connection(profile, write=True, business_connection=business_connection) as con:
        row = _attempt(con, attempt_id)
        _cas(row, reservation_sha256, expected_revision)
        if row["state"] not in {"reserved", "released"} or row["dispatched_at"] is not None:
            _fail("BUDGET_STATE_CONFLICT")
        if row["state"] == "reserved":
            con.execute("UPDATE budget_attempt SET state='released',reservation_held=0,updated_at=? WHERE attempt_id=?",
                        (_stamp(instant), attempt_id))
        return _reservation(_attempt(con, attempt_id))


def settle(attempt_id, request_sha256, *, profile, response_complete=True, outcome="full_result", usage=None,
           result_text=None, provider_request_id=None, now=None, business_connection=None):
    if type(response_complete) is not bool or type(outcome) is not str or outcome not in _OUTCOMES:
        _fail("BUDGET_REQUEST_INVALID")
    if result_text is not None and type(result_text) is not str:
        _fail("BUDGET_REQUEST_INVALID")
    if outcome in {"source_step_complete", "source_retry", "resolution_retry"} and (not response_complete or not result_text):
        _fail("BUDGET_REQUEST_INVALID")
    _text(provider_request_id, 256, nullable=True)
    if usage is not None and type(usage) is not contract.Usage:
        _fail("BUDGET_REQUEST_INVALID")
    instant = _now(now)
    try:
        result_raw = None if result_text is None else result_text.encode("utf-8")
    except UnicodeError:
        _fail("BUDGET_REQUEST_INVALID")
    result_sha = None if result_raw is None else hashlib.sha256(result_raw).hexdigest()
    result_bytes = 0 if result_raw is None else len(result_raw)
    with _connection(profile, write=True, business_connection=business_connection) as con:
        row = _attempt(con, attempt_id, request_sha256)
        if outcome == "source_step_complete" and row["kind"] not in _STEP_KINDS:
            _fail("BUDGET_REQUEST_INVALID")
        if outcome == "repair" and row["kind"] in SOURCE_KINDS:
            _fail("BUDGET_REQUEST_INVALID")
        snapshot = _snapshot(row)
        if snapshot.get("resolver_contract_version") == 1 and response_complete:
            if outcome not in {"source_step_complete", "resolution_retry", "terminal_error"} or not result_text:
                _fail("BUDGET_REQUEST_INVALID")
            _resolution_proof(row, result_text, retry=outcome == "resolution_retry", outcome=outcome, code="BUDGET_REQUEST_INVALID")
        elif outcome == "resolution_retry":
            _fail("BUDGET_REQUEST_INVALID")
        if snapshot.get("source_contract_version") == 1 and outcome in {"http_retry", "repair"}:
            _fail("BUDGET_REQUEST_INVALID")
        if outcome == "source_retry":
            if row["kind"] not in _SOURCE_RETRY_KINDS or snapshot.get("source_contract_version") != 1:
                _fail("BUDGET_REQUEST_INVALID")
            _source_retry_proof(row, result_text, code="BUDGET_REQUEST_INVALID")
        adapter = snapshot["adapter"]
        if usage is not None and usage.adapter != adapter:
            _fail("BUDGET_REQUEST_CONFLICT")
        actual_usage = (contract.Usage(adapter, "missing") if usage is None and adapter is not None else usage)
        usage_json = None if actual_usage is None else _json(actual_usage.public())
        if not response_complete:
            if result_text is not None or provider_request_id is not None or usage is not None:
                _fail("BUDGET_REQUEST_INVALID")
            if row["state"] == "settled":
                return _result(row)
            if row["state"] not in {"dispatching", "indeterminate"}:
                _fail("BUDGET_STATE_CONFLICT")
            con.execute("""UPDATE budget_attempt SET state='indeterminate',indeterminate_at=COALESCE(indeterminate_at,?),
                updated_at=? WHERE attempt_id=?""", (_stamp(instant), _stamp(instant), attempt_id))
            operation = con.execute("SELECT * FROM budget_operation WHERE operation_id=?", (row["operation_id"],)).fetchone()
            _wait(con, operation, "indeterminate", instant)
            return _result(_attempt(con, attempt_id))
        receipt = _settlement_receipt(request_sha256, outcome, actual_usage, result_sha, result_bytes, provider_request_id)
        settlement_sha = _digest(receipt)
        if row["settlement_sha256"] is not None:
            if row["settlement_sha256"] != settlement_sha:
                _fail("BUDGET_SETTLEMENT_CONFLICT")
            return _result(row)
        if row["state"] not in {"dispatching", "indeterminate"}:
            _fail("BUDGET_STATE_CONFLICT")
        stored = con.execute("""SELECT COALESCE(SUM(result_bytes),0) FROM budget_attempt
            WHERE operation_id=? AND result_storage_state='stored'""", (row["operation_id"],)).fetchone()[0]
        storage_state = ("none" if result_raw is None else "oversize" if result_bytes > MAX_RESULT_BYTES
                         else "capacity_blocked" if stored + result_bytes > MAX_OPERATION_RESULT_BYTES else "stored")
        con.execute("""UPDATE budget_attempt SET state='settled',response_complete=1,outcome=?,observed_usage_json=?,
            usage_quality=?,provider_request_id=?,result_text=?,result_sha256=?,result_bytes=?,result_storage_state=?,
            settlement_sha256=?,settled_at=?,updated_at=? WHERE attempt_id=?""", (outcome, usage_json,
            "missing" if actual_usage is None else actual_usage.quality, provider_request_id,
            result_text if storage_state == "stored" else None, result_sha, result_bytes, storage_state,
            settlement_sha, _stamp(instant), _stamp(instant), attempt_id))
        if storage_state in {"oversize", "capacity_blocked"}:
            operation = con.execute("SELECT * FROM budget_operation WHERE operation_id=?", (row["operation_id"],)).fetchone()
            _wait(con, operation, "result_capacity", instant)
        else:
            con.execute("""UPDATE budget_operation SET state=?,wait_reason=NULL,resume_at=NULL,
                revision=revision+1,updated_at=? WHERE operation_id=?""", ("open" if outcome in {"http_retry", "repair", "source_step_complete", "source_retry", "resolution_retry"}
                else "completed", _stamp(instant), row["operation_id"]))
        return _result(_attempt(con, attempt_id))


def get_result(attempt_id, request_sha256, *, profile):
    with _connection(profile) as con:
        return _result(_attempt(con, attempt_id, request_sha256))


def get_dispatch_policy(attempt_id, request_sha256, *, profile):
    """Private pure read of the verified first-dispatch policy, never config."""
    with _connection(profile) as con:
        row = _attempt(con, attempt_id, request_sha256)
        if row["state"] not in {"dispatching", "indeterminate", "settled"}:
            _fail("BUDGET_STATE_CONFLICT")
        # _facts proved the canonical first-dispatch receipt equals this snapshot.
        return contract.parse_policy(row["_snapshot"]["policy"])


def _attempt_view(row):
    return AttemptView(row["ordinal"], row["attempt_id"], row["request_sha256"], row["state"],
        bool(row["response_complete"]), row["outcome"], row["result_storage_state"], row["usage_quality"], row["download_segment"],
        row["_snapshot"].get("source_contract_version"), row["_snapshot"].get("resolver_contract_version"),
        row["_snapshot"].get("resolver_phase"), row["_snapshot"].get("resolver_max_attempts"))


def get_operation(spec, *, profile):
    """Exact actual-library lookup; no policy gate, identity seed or response body."""
    if type(spec) is not OperationSpec:
        _fail("BUDGET_IDENTITY_INVALID")
    with _connection(profile) as con:
        row = _find_operation(con, spec)
        if row is None:
            return None
        raw_attempts = con.execute(f"""SELECT {_METER_PROJECTION},o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts FROM budget_attempt a JOIN budget_operation o USING(operation_id)
            WHERE a.operation_id=? ORDER BY a.ordinal LIMIT 129""", (row["operation_id"],)).fetchall()
        if len(raw_attempts) > row["max_attempts"] or any(raw["ordinal"] != index for index, raw in enumerate(raw_attempts, 1)):
            _fail()
        attempts = tuple(_attempt_view(_facts(raw)) for raw in raw_attempts)
        return OperationView(row["operation_id"], row["max_attempts"], row["state"], row["wait_reason"],
            None if row["resume_at"] is None else _datetime(row["resume_at"]), row["revision"],
            0 if not attempts else attempts[-1].ordinal, attempts)


def get_attempt(operation_id, ordinal, *, profile):
    """One exact ordinal's metadata; paid body remains private to get_result."""
    _uuid(operation_id, "BUDGET_REQUEST_INVALID")
    _integer(ordinal, minimum=1, maximum=128)
    with _connection(profile) as con:
        operation = con.execute("SELECT * FROM budget_operation WHERE operation_id=?", (operation_id,)).fetchone()
        if operation is None:
            return None
        _operation_facts(operation)
        if operation["library_id"] != _library(con):
            _fail("BUDGET_IDENTITY_INVALID")
        row = con.execute(f"""SELECT {_METER_PROJECTION},o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts FROM budget_attempt a JOIN budget_operation o USING(operation_id)
            WHERE a.operation_id=? AND a.ordinal=?""", (operation_id, ordinal)).fetchone()
        return None if row is None else _attempt_view(_facts(row))


def summarize(*, profile, policy=None, now=None):
    settings, instant = _policy(policy), _now(now)
    window = contract.period_for(settings, instant)
    with _connection(profile) as con:
        models, downloads, logical, sources = _counts(con, window)
        observations = [row["_usage"] for row in models]
        output = {"scope": "local_ledger_only", "history_before_ledger": "unknown",
            "policy": settings.public(), "window": window.public(), "model_attempt_admissions": len(models),
            "download_process_attempts": len(downloads), "download_logical_admissions": len(logical),
            "unknown_usage_attempts": sum(usage is None or usage.quality != "reported" for usage in observations),
            "reserved_output_tokens": sum(_snapshot(row)["units"]["reserved_output_tokens"] for row in models
                if row["state"] in {"reserved", "dispatching", "indeterminate"}
                or (usage := row["_usage"]) is None or usage.output_tokens is None),
            "indeterminate_attempts": con.execute("SELECT COUNT(*) FROM budget_attempt WHERE state='indeterminate'").fetchone()[0],
            "waiting_operations": con.execute("SELECT COUNT(*) FROM budget_operation WHERE state='waiting'").fetchone()[0],
            "stored_results": con.execute("SELECT COUNT(*) FROM budget_attempt WHERE result_storage_state='stored'").fetchone()[0]}
        output.update({kind + "_attempts": len(rows) for kind, rows in sources.items()})
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            values = [getattr(usage, field) for usage in observations if usage is not None and getattr(usage, field) is not None]
            output["reported_" + field] = sum(values) if values or not observations else None
        return output
