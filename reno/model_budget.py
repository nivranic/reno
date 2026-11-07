"""Model-only budget bridge: private scope, per-send admission, paid reuse.

Transport callbacks receive the exact frozen JSON bytes. Headers and credentials
remain in the transport layer and never form operation or request identities.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import uuid

from . import budget, budget_contract as contract, config, storage_lifecycle

VERSION_TAGS = frozenset({"prompt_version", "taxonomy_version", "frozen_input_sha256", "frame_sha256",
                          "candidate_sha256", "library_epoch", "kb_revision"})
_PHASES = frozenset({"initial", "repair"})
_OUTCOMES = frozenset({"full_result", "http_retry", "repair", "terminal_error"})
_BODY_KEYS = frozenset({"model", "messages", "temperature", "max_tokens", "response_format", "thinking", "system"})
HTTP_ATTEMPTS = 4


def _fail(code="BUDGET_REQUEST_INVALID"):
    raise budget.BudgetLedgerError(code) from None


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError):
        _fail()


def _digest(value):
    try:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    except UnicodeError:
        _fail()


@dataclass(frozen=True, slots=True)
class ModelScope:
    profile: storage_lifecycle.StorageProfile
    continuation_id: str
    execution_generation: int
    suboperation_key: str
    root_job_id: int | None = None
    business_connection: sqlite3.Connection | None = None
    version_tags: tuple[tuple[str, str], ...] = ()
    expects_json: bool = False
    home_execution: object | None = None
    home_client: object | None = None

    def __post_init__(self):
        if type(self.profile) is not storage_lifecycle.StorageProfile or type(self.expects_json) is not bool:
            _fail("BUDGET_IDENTITY_INVALID")
        budget.OperationSpec(self.continuation_id, self.execution_generation, "model", self.suboperation_key,
                             "0" * 64, self.root_job_id)
        if self.home_execution is not None:
            from .sync_client import SyncClient
            from .sync_source_binding17 import require_binding
            binding = require_binding(self.home_execution, profile=self.profile)
            core = binding.core
            if (type(self.home_client) is not SyncClient or
                    (core["continuation_id"], core["execution_generation"], core["root_job_id"]) !=
                    (self.continuation_id, self.execution_generation, self.root_job_id)):
                _fail("BUDGET_IDENTITY_INVALID")
        elif self.home_client is not None:
            _fail("BUDGET_IDENTITY_INVALID")
        if type(self.version_tags) is not tuple:
            _fail("BUDGET_IDENTITY_INVALID")
        seen = set()
        for tag in self.version_tags:
            try:
                if (type(tag) is not tuple or len(tag) != 2 or type(tag[0]) is not str or tag[0] not in VERSION_TAGS
                        or tag[0] in seen or type(tag[1]) is not str or not tag[1]
                        or len(tag[1].encode("utf-8")) > 128):
                    _fail("BUDGET_IDENTITY_INVALID")
            except UnicodeError:
                _fail("BUDGET_IDENTITY_INVALID")
            seen.add(tag[0])
        object.__setattr__(self, "version_tags", tuple(sorted(self.version_tags)))


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status: int
    body: bytes

    def __post_init__(self):
        if type(self.status) is not int or not 100 <= self.status <= 599 or type(self.body) is not bytes:
            _fail()


class ModelPhaseExhausted(RuntimeError):
    """Provider retries are exhausted; this is not a budget clock wait."""
    code = "MODEL_HTTP_PHASE_EXHAUSTED"

    def __init__(self):
        super().__init__(self.code)


@dataclass(slots=True)
class _Session:
    scope: ModelScope
    operation: budget.OperationSpec | None = None
    tracked: bool = False


_SESSION = ContextVar("reno_model_budget_session", default=None)
_PHASE = ContextVar("reno_model_budget_phase", default="initial")
_CLASSIFICATION = ContextVar("reno_model_budget_classification", default=None)
_ADMISSION = ContextVar("reno_model_budget_admission", default=None)
_DISPATCH_GUARD = ContextVar("reno_model_budget_dispatch_guard", default=None)


def current_scope():
    session = _SESSION.get()
    return None if session is None else session.scope


def current_phase():
    return _PHASE.get()


def is_tracked():
    session = _SESSION.get()
    return session is not None and session.tracked


def expects_json():
    value = _CLASSIFICATION.get()
    source = current_scope()
    return (False if source is None else source.expects_json) if value is None else value


@contextmanager
def scope(value):
    if type(value) is not ModelScope:
        _fail("BUDGET_IDENTITY_INVALID")
    session_token = _SESSION.set(_Session(value))
    phase_token = _PHASE.set("initial")
    classification_token = _CLASSIFICATION.set(None)
    try:
        yield value
    finally:
        _CLASSIFICATION.reset(classification_token)
        _PHASE.reset(phase_token)
        _SESSION.reset(session_token)


@contextmanager
def phase(value):
    if type(value) is not str or value not in _PHASES:
        _fail()
    token = _PHASE.set(value)
    try:
        yield
    finally:
        _PHASE.reset(token)


@contextmanager
def classification(*, expects_json):
    if type(expects_json) is not bool:
        _fail()
    token = _CLASSIFICATION.set(expects_json)
    try:
        yield
    finally:
        _CLASSIFICATION.reset(token)


@contextmanager
def admission_check(callback, *, dispatch_guard=None):
    if not callable(callback) or dispatch_guard is not None and not callable(dispatch_guard):
        _fail()
    token = _ADMISSION.set(callback)
    guard_token = _DISPATCH_GUARD.set(dispatch_guard)
    try:
        yield
    finally:
        _DISPATCH_GUARD.reset(guard_token)
        _ADMISSION.reset(token)


def _check_admission():
    callback = _ADMISSION.get()
    if callback is not None:
        callback()


def payload_bytes(body):
    if type(body) is not dict or set(body) - _BODY_KEYS:
        _fail()
    try:
        return _json(body).encode("utf-8")
    except UnicodeError:
        _fail()


def _operation(session, adapter, body):
    if session.operation is None:
        if current_phase() != "initial":
            _fail("BUDGET_STATE_CONFLICT")
        identity = {"format_version": 1, "adapter": adapter, "base_body": body,
                    "version_tags": dict(session.scope.version_tags), "expects_json": expects_json()}
        session.operation = budget.OperationSpec(session.scope.continuation_id, session.scope.execution_generation,
            "model", session.scope.suboperation_key, _digest(identity), session.scope.root_job_id)
    return session.operation


def _envelope(adapter, request_sha, payload_sha, response):
    return _json({"format_version": 1, "adapter": adapter, "request_sha256": request_sha,
                  "payload_sha256": payload_sha, "status": response.status,
                  "body_base64": base64.b64encode(response.body).decode("ascii")})


def _restore(result, adapter, request_sha, payload_sha, operation_id):
    if not result.response_complete:
        raise contract.BudgetDeferred("indeterminate", operation_id=operation_id)
    if result.text is None or result.storage_state != "stored":
        raise contract.BudgetDeferred("result_capacity", operation_id=operation_id)
    try:
        value = json.loads(result.text)
        if (type(value) is not dict or set(value) != {"format_version", "adapter", "request_sha256", "payload_sha256", "status", "body_base64"}
                or type(value["format_version"]) is not int or value["format_version"] != 1
                or _json(value) != result.text or value["adapter"] != adapter or value["request_sha256"] != request_sha
                or value["payload_sha256"] != payload_sha
                or type(value["body_base64"]) is not str):
            _fail()
        raw = base64.b64decode(value["body_base64"], validate=True)
        if base64.b64encode(raw).decode("ascii") != value["body_base64"]:
            _fail()
        return ProviderResponse(value["status"], raw)
    except (ValueError, TypeError, KeyError, UnicodeError):
        _fail()


def _observation(adapter, response):
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        value = None
    usage = contract.parse_usage(adapter, value.get("usage") if type(value) is dict else None)
    provider = value.get("id") if type(value) is dict else None
    try:
        if type(provider) is not str or not provider or "\0" in provider or len(provider.encode("utf-8")) > 256:
            provider = None
    except UnicodeError:
        provider = None
    return usage, provider


def _lookup(view, request_sha, payload_sha, adapter, profile):
    if view is None:
        return None, 1
    matching = tuple(attempt for attempt in view.attempts if attempt.request_sha256 == request_sha)
    if matching:
        latest = matching[-1]
        if latest.state == "settled" and latest.response_complete and latest.outcome != "http_retry":
            result = budget.get_result(latest.attempt_id, request_sha, profile=profile)
            return _restore(result, adapter, request_sha, payload_sha, view.operation_id), latest.ordinal
        if latest.state in {"dispatching", "indeterminate"}:
            raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
        dispatched = sum(attempt.state in {"dispatching", "indeterminate", "settled"} for attempt in matching)
        if dispatched >= HTTP_ATTEMPTS:
            raise ModelPhaseExhausted()
        if latest.state == "reserved":
            return None, latest.ordinal
    if view.attempts and view.attempts[-1].state in {"dispatching", "indeterminate"}:
        raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
    return None, view.latest_ordinal + 1


def _business_ready(source):
    con = source.business_connection
    if con is not None and (type(con) is not sqlite3.Connection or con.in_transaction):
        _fail("BUDGET_BUSINESS_TRANSACTION_ACTIVE")


def _unknown(reservation, request_sha, source):
    try:
        budget.settle(reservation.attempt_id, request_sha, profile=source.profile, response_complete=False)
    except BaseException:
        # Dispatching is already durable and blocks resend even if this final
        # diagnostic write fails. Never mask cancellation with cleanup errors.
        pass


def request_once(adapter, body, send, *, classify, transport_admission=None):
    """One physical HTTP opportunity, with persistent four-attempt phase bounds.

    The existing HTTP loop owns backoff. On restart this hook consumes the old
    ordinal history rather than starting another four calls. Complete paid
    model/repair responses are reused before policy or manual admission gates.
    """
    contract._adapter(adapter)
    if (not callable(send) or not callable(classify)
            or transport_admission is not None and not callable(transport_admission)):
        _fail()
    def check_transport():
        if transport_admission is not None:
            transport_admission()
    consumer_guard = _DISPATCH_GUARD.get()
    def combined_guard(con):
        if consumer_guard is not None:
            consumer_guard(con)
        check_transport()
    sql_guard = combined_guard if consumer_guard is not None or transport_admission is not None else None
    payload = payload_bytes(body)
    payload_sha = hashlib.sha256(payload).hexdigest()
    frozen = json.loads(payload)
    requested = frozen.get("max_tokens")
    effective = contract.effective_max_tokens(adapter, requested)
    if effective != requested:
        _fail()
    # request_sha identifies the phase/provider/canonical request descriptor.
    # payload_sha separately proves the exact bytes supplied to the transport.
    request_sha = _digest({"format_version": 1, "phase": current_phase(), "adapter": adapter, "body": frozen})
    session = _SESSION.get()
    if session is None:
        from .sync_source_binding17 import require_home17_effect_scope
        require_home17_effect_scope()
        settings = contract.parse_policy(config.budget_settings())
        if settings.budget_enabled:
            raise contract.BudgetDeferred("unsupported_capability")
        _check_admission()
        if contract.parse_policy(config.budget_settings()).budget_enabled:
            raise contract.BudgetDeferred("unsupported_capability")
        check_transport()
        return send(payload)
    source = session.scope
    operation = _operation(session, adapter, frozen)
    view = budget.get_operation(operation, profile=source.profile)
    session.tracked = view is not None
    restored, ordinal = _lookup(view, request_sha, payload_sha, adapter, source.profile)
    if restored is not None:
        return restored
    from .sync_source_binding17 import require_home17_effect_scope
    require_home17_effect_scope(source.home_execution, profile=source.profile,
        root_job_id=source.root_job_id)
    settings = contract.parse_policy(config.budget_settings())
    if not settings.budget_enabled and view is None and source.home_execution is None:
        _check_admission()
        # A manual execution hook may promote its UUID if the user enabled
        # metering while this opportunity waited. Re-read before legacy send.
        settings = contract.parse_policy(config.budget_settings())
        if not settings.budget_enabled:
            check_transport()
            return send(payload)
    session.tracked = True
    _business_ready(source)
    _check_admission()
    check_transport()
    admission = nullcontext(None)
    if source.home_execution is not None:
        from .sync_source_admission17 import capture_current_admission_locked
        admission = capture_current_admission_locked(profile=source.profile, store=source.home_execution.store,
            client=source.home_client, item_id=source.home_execution.core["item_id"], binding=source.home_execution)
    # The Home factory owns the short State/SH proof scope. Complete paid
    # responses above need no fresh capture; send and settlement below happen
    # only after this scope and both original SQL writers have exited.
    with admission as current:
        def guarded(connection):
            if sql_guard is not None:
                sql_guard(connection)
            if current is not None:
                from . import budget_writer_witness
                writer = budget_writer_witness.require_writer(connection, profile=source.profile)
                current.verify_for_commit(connection, writer_permit=writer)
        reservation = budget.reserve(operation, budget.AttemptSpec(request_sha, ordinal, adapter, effective),
            profile=source.profile, business_connection=source.business_connection,
            reservation_guard=guarded if current is not None else sql_guard)
        try:
            _check_admission()
            check_transport()
            dispatched = budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
                str(uuid.uuid4()), expected_revision=reservation.revision, profile=source.profile,
                business_connection=source.business_connection, dispatch_guard=guarded if current is not None else sql_guard)
        except contract.BudgetDeferred as waiting:
            if waiting.operation_id is None:
                raise contract.BudgetDeferred(waiting.reason, resume_at=waiting.resume_at,
                                              operation_id=reservation.operation_id) from None
            raise
    if not dispatched.won:
        result = budget.get_result(reservation.attempt_id, request_sha, profile=source.profile)
        return _restore(result, adapter, request_sha, payload_sha, reservation.operation_id)
    try:
        response = send(payload)
        if type(response) is not ProviderResponse:
            _fail()
    except BaseException as error:
        _unknown(reservation, request_sha, source)
        if not isinstance(error, Exception):
            raise
        raise contract.BudgetDeferred("indeterminate", operation_id=reservation.operation_id) from None
    usage, provider = _observation(adapter, response)
    classification_failed = False
    try:
        outcome = classify(response)
        if type(outcome) is not str or outcome not in _OUTCOMES:
            classification_failed = True
            outcome = "terminal_error"
    except Exception:
        classification_failed = True
        outcome = "terminal_error"
    budget.settle(reservation.attempt_id, request_sha, profile=source.profile, outcome=outcome,
        usage=usage, result_text=_envelope(adapter, request_sha, payload_sha, response), provider_request_id=provider)
    if classification_failed:
        _fail()
    return response
