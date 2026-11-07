"""Home-bound DNS/header opportunities: paid replay, one CAS, exact settlement.

No SQL writer crosses a transport call. A late DNS observation can settle only
its original attempt; it never acquires a worker or starts a following request.
"""
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
import uuid

from . import budget, config, db, source_resolution as adapter
from . import source_resolution_contract as codec, storage_lifecycle, sync_sources
from .budget_contract import BudgetDeferred
from .sync_protocol import SyncError


RESOLUTION_REUSE_SECONDS = 3600


@dataclass(frozen=True, slots=True, repr=False)
class RefreshAuthorization:
    attempt_id: str
    settlement_sha256: str

    def __post_init__(self):
        budget._uuid(self.attempt_id, "BUDGET_REQUEST_INVALID")
        budget._sha(self.settlement_sha256)


@dataclass(frozen=True, slots=True, repr=False)
class CompletedResolution:
    resolution_json: str = field(repr=False)
    attempt_id: str
    request_sha256: str
    settlement_sha256: str
    generation: int
    refresh_attempt_id: str | None = None
    refresh_settlement_sha256: str | None = None

    @property
    def resolution(self):
        return json.loads(self.resolution_json)


@dataclass(frozen=True, slots=True)
class _Trace:
    plan: codec.ResolutionPlan | None
    ordinal: int
    state: str | None = None
    completed: CompletedResolution | None = None
    retry_result: codec.ResolutionResult | None = None


def _now():
    return datetime.now(timezone.utc)


def _fail(code="BUDGET_REQUEST_CONFLICT"):
    raise budget.BudgetLedgerError(code) from None


def _binding(value):
    from .sync_source_execution import HomeExecutionBinding
    from .sync_source_binding17 import HomeBindingFacts17, require_binding
    if type(value) not in (HomeExecutionBinding, HomeBindingFacts17):
        _fail("BUDGET_IDENTITY_INVALID")
    if type(value) is HomeBindingFacts17:
        require_binding(value)
    core = value.core
    submitted = codec.validate_url(value.submitted_url)
    if hashlib.sha256(submitted.encode("utf-8")).hexdigest() != core["submitted_url_sha256"]:
        _fail("BUDGET_IDENTITY_INVALID")
    return value


def _authority(value):
    if type(value) is RefreshAuthorization:
        return value
    if type(value) is dict and set(value) == {"attempt_id", "settlement_sha256"}:
        return RefreshAuthorization(**value)
    _fail("BUDGET_REQUEST_INVALID")


def _view(binding):
    return budget.get_operation(binding.resolution_operation, profile=binding.profile)


def _read(binding, view, item):
    if (item.resolver_contract_version != 1 or item.resolver_max_attempts != view.max_attempts
            or item.resolver_phase not in {"dns", "headers"}):
        raise BudgetDeferred("unsupported_capability", operation_id=view.operation_id)
    receipt = budget.get_result(item.attempt_id, item.request_sha256, profile=binding.profile)
    if not receipt.response_complete:
        raise BudgetDeferred("indeterminate", operation_id=view.operation_id)
    if receipt.storage_state != "stored" or receipt.text is None:
        raise BudgetDeferred("result_capacity", operation_id=view.operation_id)
    keys = dict(request_sha256=item.request_sha256, input_sha256=binding.resolution_operation.input_sha256,
                phase=item.resolver_phase, max_attempts=view.max_attempts)
    plan = codec.decode_plan(receipt.text, **keys)
    result = codec.decode_envelope(receipt.text, **keys)
    if receipt.settlement_sha256 is None:
        _fail("BUDGET_LEDGER_INVALID")
    return plan, result, receipt


def _terminal(result):
    code = "SYNC_SOURCE_UNAVAILABLE"
    if result.failure_class in {"dns_rejected", "redirect_invalid", "resource_invalid", "header_invalid"}:
        code = "SYNC_SOURCE_INVALID"
    elif result.phase == "headers" and result.status == 429:
        code = "SYNC_SOURCE_RATE_LIMITED"
    elif result.retryable:
        code = "SYNC_SOURCE_NETWORK"
    raise SyncError("Source resolution failed", code, 503)


def _initial(binding, cap, generation=0, authority=None, previous=None):
    values = {}
    if generation:
        values = dict(refresh_attempt_id=authority.attempt_id,
                      refresh_settlement_sha256=authority.settlement_sha256,
                      previous_attempt_id=previous.attempt_id,
                      previous_settlement_sha256=previous.settlement_sha256)
    return codec.ResolutionPlan("dns", binding.submitted_url, binding.submitted_url,
        binding.resolution_operation.input_sha256, cap, generation=generation, **values)


def _trace(binding, view, plan, *, start=1):
    """Replay the whole ordered chain, including every paid failed opportunity."""
    hops, retry_result = [], None
    for item in () if view is None else view.attempts[start - 1:]:
        if item.request_sha256 != plan.request_sha256:
            _fail("BUDGET_LEDGER_INVALID")
        if item.state in {"reserved", "dispatching", "indeterminate"}:
            return _Trace(plan, item.ordinal, item.state, retry_result=retry_result)
        if item.state == "released":
            continue
        if item.state != "settled" or not item.response_complete:
            _fail("BUDGET_LEDGER_INVALID")
        stored_plan, result, receipt = _read(binding, view, item)
        if stored_plan != plan:
            _fail("BUDGET_LEDGER_INVALID")
        if item.outcome == "resolution_retry":
            if not result.retryable:
                _fail("BUDGET_LEDGER_INVALID")
            retry_result = result
            continue
        if item.outcome == "terminal_error":
            _terminal(result)
        if item.outcome != "source_step_complete" or not result.successful:
            _fail("BUDGET_LEDGER_INVALID")
        retry_result = None
        common = dict(hop_index=plan.hop_index, generation=plan.generation,
            visited_urls=plan.visited_urls, refresh_attempt_id=plan.refresh_attempt_id,
            refresh_settlement_sha256=plan.refresh_settlement_sha256)
        if plan.phase == "dns":
            plan = codec.ResolutionPlan("headers", plan.submitted_url, plan.current_url,
                plan.input_sha256, plan.max_attempts, dns_attempt_id=item.attempt_id,
                dns_settlement_sha256=receipt.settlement_sha256, peer_ip=result.addresses[0], **common)
            continue
        hops.append({"url": plan.current_url, "peer_ip": plan.peer_ip,
                     "status": result.status, "next_url": result.next_url})
        if result.next_url is None:
            resolution = sync_sources._validate_resolution({"resolution_version": 1,
                "submitted_url": plan.submitted_url, "resolved_url": plan.current_url,
                "resolved_at": result.observed_at, "hops": hops})
            completed = CompletedResolution(codec.canonical_json(resolution), item.attempt_id,
                item.request_sha256, receipt.settlement_sha256, plan.generation,
                plan.refresh_attempt_id, plan.refresh_settlement_sha256)
            return _Trace(None, item.ordinal + 1, completed=completed)
        common.update(hop_index=plan.hop_index + 1,
                      visited_urls=(*plan.visited_urls, plan.current_url))
        plan = codec.ResolutionPlan("dns", plan.submitted_url, result.next_url, plan.input_sha256,
            plan.max_attempts, previous_attempt_id=item.attempt_id,
            previous_settlement_sha256=receipt.settlement_sha256, **common)
    return _Trace(plan, start if view is None else view.latest_ordinal + 1, retry_result=retry_result)


def _saved_authority(binding, view, start):
    for item in view.attempts[start - 1:]:
        if item.state in {"reserved", "dispatching", "indeterminate"}:
            return None
        if item.state == "released":
            continue
        plan, _, _ = _read(binding, view, item)
        if plan.generation != 1 or plan.hop_index != 0 or plan.phase != "dns":
            _fail("BUDGET_LEDGER_INVALID")
        return RefreshAuthorization(plan.refresh_attempt_id, plan.refresh_settlement_sha256)
    return None


def _same_resource(initial, refreshed):
    if codec.resource_reference(initial.resolution["resolved_url"]) != codec.resource_reference(
            refreshed.resolution["resolved_url"]):
        _fail("BUDGET_REQUEST_CONFLICT")


def _historical_authority(binding, view, start, authority, initial):
    # Closed generation 1 still proves its original authority. For the age
    # right, compare history at the first refresh dispatch rather than applying
    # today's no-metadata gate to an already paid, subsequently consumed fact.
    first = next((item for item in view.attempts[start - 1:] if item.state != "released"), None)
    if first is None:
        _fail("BUDGET_LEDGER_INVALID")
    with storage_lifecycle.shared(binding.profile), closing(db.connect(binding.profile.database)) as con:
        con.execute("BEGIN")
        row = con.execute("SELECT dispatched_at FROM budget_attempt WHERE attempt_id=?",
                          (first.attempt_id,)).fetchone()
        if row is None or row["dispatched_at"] is None:
            _fail("BUDGET_LEDGER_INVALID")
        _authorize(binding, authority, initial, connection=con,
                   historical_at=budget._datetime(row["dispatched_at"]))


def completed_resolution(binding, generation=0):
    """Only retained paid facts; no worker/configuration/admission/network check."""
    _binding(binding)
    if type(generation) is not int or generation not in (0, 1):
        _fail("BUDGET_REQUEST_INVALID")
    view = _view(binding)
    if view is None:
        return None
    initial = _trace(binding, view, _initial(binding, view.max_attempts))
    if generation == 0 or initial.completed is None:
        return initial.completed
    authority = _saved_authority(binding, view, initial.ordinal)
    if authority is None:
        return None
    refreshed = _trace(binding, view, _initial(binding, view.max_attempts, 1, authority,
        initial.completed), start=initial.ordinal).completed
    if refreshed is not None:
        _historical_authority(binding, view, initial.ordinal, authority, initial.completed)
        _same_resource(initial.completed, refreshed)
    return refreshed


def _authorize(binding, authority, initial, *, connection=None, historical_at=None):
    """A caller's pair is a selector; the original ledger grants refresh rights."""
    with storage_lifecycle.shared(binding.profile):
        con = db.connect(binding.profile.database) if connection is None else connection
        try:
            row = con.execute("SELECT a.*,o.kind,o.input_sha256,o.library_id,o.root_job_id,"
                "o.continuation_id,o.execution_generation,o.suboperation_key FROM budget_attempt a "
                "JOIN budget_operation o USING(operation_id) WHERE a.attempt_id=?", (authority.attempt_id,)).fetchone()
            core, prefix = binding.core, "home:" + binding.namespace + ":"
            if (row is None or row["library_id"] != core["binding"]["home_library_id"]
                    or (row["root_job_id"], row["continuation_id"], row["execution_generation"]) != (
                        core["root_job_id"], core["continuation_id"], core["execution_generation"])
                    or not row["suboperation_key"].startswith(prefix)
                    or row["state"] != "settled" or not row["response_complete"]
                    or row["settlement_sha256"] != authority.settlement_sha256
                    or historical_at is not None and budget._datetime(row["settled_at"]) > historical_at):
                _fail()
            if authority.attempt_id == initial.attempt_id:
                if (row["kind"] != "source_resolution" or row["outcome"] != "source_step_complete"
                        or authority.settlement_sha256 != initial.settlement_sha256
                        or (historical_at if historical_at is not None else _now()) < datetime.fromisoformat(
                            initial.resolution["resolved_at"].replace("Z", "+00:00"))
                            + timedelta(seconds=RESOLUTION_REUSE_SECONDS)):
                    _fail()
                dispatched = con.execute("SELECT 1 FROM budget_attempt a JOIN budget_operation o USING(operation_id) "
                    "WHERE o.library_id=? AND substr(o.suboperation_key,1,?)=? AND o.kind IN ('source_metadata','download') "
                    "AND a.first_dispatch_json IS NOT NULL" +
                    (" AND a.dispatched_at<=?" if historical_at is not None else "") + " LIMIT 1",
                    (row["library_id"], len(prefix), prefix) +
                    ((budget._stamp(historical_at),) if historical_at is not None else ())).fetchone()
                if dispatched is not None:
                    _fail()
                return
            if row["kind"] != "download" or row["download_segment"] != "video" or row["outcome"] != "source_retry":
                _fail()
            from .source_contract import validate_retry_envelope, SourceContractError
            receipt = budget.get_result(row["attempt_id"], row["request_sha256"], profile=binding.profile)
            if receipt.storage_state != "stored" or receipt.text is None:
                raise BudgetDeferred("result_capacity", operation_id=row["operation_id"])
            try:
                validate_retry_envelope(receipt.text, request_sha256=row["request_sha256"],
                    input_sha256=row["input_sha256"], kind="download", segment="video")
            except SourceContractError:
                _fail()
        finally:
            if connection is None:
                con.close()


def _settle_known(profile, operation, reservation, plan, result):
    text = codec.encode_envelope(plan, result, input_sha256=operation.input_sha256)
    outcome = ("source_step_complete" if result.successful else "resolution_retry"
               if result.retryable and reservation.ordinal < plan.max_attempts else "terminal_error")
    return budget.settle(reservation.attempt_id, plan.request_sha256, profile=profile,
                         outcome=outcome, result_text=text)


def _refresh_projection17(connection, binding, authority):
    """One original paid source row and the original dispatch exclusion."""
    row = connection.execute("SELECT a.*,o.kind,o.input_sha256,o.library_id,o.root_job_id,"
        "o.continuation_id,o.execution_generation,o.suboperation_key FROM main.budget_attempt a "
        "JOIN main.budget_operation o USING(operation_id) WHERE a.attempt_id=?", (authority.attempt_id,)).fetchone()
    if row is None or row["result_text"] is None or row["result_bytes"] > 2 * 1024 * 1024:
        _fail()
    core, prefix = binding.core, "home:" + binding.namespace + ":"
    blocked = connection.execute("SELECT 1 FROM main.budget_attempt a JOIN main.budget_operation o "
        "USING(operation_id) WHERE o.library_id=? AND substr(o.suboperation_key,1,?)=? "
        "AND o.kind IN ('source_metadata','download') AND a.first_dispatch_json IS NOT NULL LIMIT 1",
        (core["binding"]["home_library_id"], len(prefix), prefix)).fetchone() is not None
    return tuple(row), blocked


def _run_step(binding, trace, *, check_cancelled, authority=None, initial=None, client=None):
    plan, operation, profile = trace.plan, binding.resolution_operation, binding.profile
    if trace.state in {"dispatching", "indeterminate"}:
        raise BudgetDeferred("indeterminate", operation_id=_view(binding).operation_id)
    from .sync_source_binding17 import HomeBindingFacts17
    home17 = type(binding) is HomeBindingFacts17
    def check():
        # Once dispatched, DNS/header waiting and late settlement use only
        # cancellation. A following hop captures its own current admission.
        if not home17:
            binding.check()
        if check_cancelled is not None:
            check_cancelled()
    check()
    prepared = adapter.prepare(plan, deadline=time.monotonic() + sync_sources.RESOLVE_TIMEOUT_SECONDS,
                               check_cancelled=check)
    refresh_projection = None
    refresh_not_before = None
    if home17 and authority is not None:
        # Decode/SHA/retained-envelope work stays before both original writers.
        _authorize(binding, authority, initial)
        from . import budget_writer_witness
        with budget_writer_witness.connection(profile) as connection:
            refresh_projection = _refresh_projection17(connection, binding, authority)
        if authority.attempt_id == initial.attempt_id:
            refresh_not_before = datetime.fromisoformat(initial.resolution["resolved_at"].replace("Z", "+00:00")) + timedelta(
                seconds=RESOLUTION_REUSE_SECONDS)
    admission = nullcontext(None)
    if home17:
        from .sync_source_admission17 import capture_current_admission_locked
        admission = capture_current_admission_locked(profile=profile, store=binding.store,
            client=client, item_id=binding.core["item_id"], binding=binding)
    with admission as current:
        def guard(connection):
            if current is None:
                binding.check(connection=connection)
            if authority is not None:
                if current is None:
                    _authorize(binding, authority, initial, connection=connection)
                elif (_refresh_projection17(connection, binding, authority) != refresh_projection
                        or refresh_not_before is not None and _now() < refresh_not_before):
                    _fail()
            if check_cancelled is not None:
                check_cancelled()
            if current is not None:
                from . import budget_writer_witness
                writer = budget_writer_witness.require_writer(connection, profile=profile)
                current.verify_for_commit(connection, writer_permit=writer)
        reserved = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, trace.ordinal,
            resolver_contract_version=1, resolver_phase=plan.phase, resolver_max_attempts=plan.max_attempts),
            profile=profile, reservation_guard=guard)
        dispatched = budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256,
            str(uuid.uuid4()), expected_revision=reserved.revision, profile=profile, dispatch_guard=guard)
    if not dispatched.won:
        return
    # Capture original immutable facts. The lookup thread must not consult any
    # current worker context, source scope, owner, configuration or newer claim.
    def late_sink(result):
        if type(result) is codec.ResolutionResult and result.response_complete:
            _settle_known(profile, operation, reserved, plan, result)
    try:
        result = adapter.run(prepared, late_sink=late_sink if plan.phase == "dns" else None)
        if type(result) is not codec.ResolutionResult:
            _fail("BUDGET_REQUEST_INVALID")
        codec.encode_envelope(plan, result, input_sha256=operation.input_sha256)
    except BaseException as error:
        try:
            budget.settle(reserved.attempt_id, plan.request_sha256, profile=profile, response_complete=False)
        except Exception:
            raise BudgetDeferred("ledger_unavailable", operation_id=reserved.operation_id) from None
        if not isinstance(error, Exception):
            raise
        if prepared.cancel_exception is not None:
            raise prepared.cancel_exception
        raise BudgetDeferred("indeterminate", operation_id=reserved.operation_id) from None
    try:
        if result.response_complete:
            _settle_known(profile, operation, reserved, plan, result)
        else:
            budget.settle(reserved.attempt_id, plan.request_sha256, profile=profile, response_complete=False)
    except Exception:
        raise BudgetDeferred("ledger_unavailable", operation_id=reserved.operation_id) from None
    if prepared.cancel_exception is not None:
        raise prepared.cancel_exception
    if not result.response_complete:
        # Even if a racing late callback already settled, this timed-out caller
        # starts no next hop. A later owned invocation can recover that fact.
        raise BudgetDeferred("indeterminate", operation_id=reserved.operation_id)


def resolve_url(binding, *, client=None, check_cancelled=None, refresh_authorization=None):
    """Return the original v1 resolution shape; generation 1 is explicit only."""
    _binding(binding)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("BUDGET_REQUEST_INVALID")
    authority = None if refresh_authorization is None else _authority(refresh_authorization)
    conflicts = 0
    while True:
        view = _view(binding)
        cap = config.source_resolution_attempt_limit() if view is None else view.max_attempts
        initial = _trace(binding, view, _initial(binding, cap))
        trace = initial
        if authority is not None:
            if initial.completed is None:
                _fail()
            saved = _saved_authority(binding, view, initial.ordinal)
            if saved is not None and saved != authority:
                _fail()
            trace = _trace(binding, view, _initial(binding, cap, 1, authority,
                initial.completed), start=initial.ordinal)
        if trace.completed is not None:
            if authority is not None:
                _historical_authority(binding, view, initial.ordinal, authority, initial.completed)
                _same_resource(initial.completed, trace.completed)
            return trace.completed.resolution
        if trace.state in {"dispatching", "indeterminate"}:
            raise BudgetDeferred("indeterminate", operation_id=view.operation_id)
        if trace.ordinal > cap:
            raise BudgetDeferred("attempt_limit", operation_id=None if view is None else view.operation_id)
        if trace.retry_result is not None and trace.retry_result.retry_after_seconds:
            resume = datetime.fromisoformat(trace.retry_result.observed_at.replace("Z", "+00:00")) + timedelta(
                seconds=trace.retry_result.retry_after_seconds)
            if _now() < resume:
                raise BudgetDeferred("download_pacing", resume_at=resume, operation_id=view.operation_id)
        try:
            _run_step(binding, trace, client=client, check_cancelled=check_cancelled, authority=authority,
                      initial=initial.completed)
        except budget.BudgetLedgerError as error:
            if error.code == "BUDGET_REQUEST_CONFLICT" and view is None and conflicts < 2:
                # A config edit before the writer created the operation rolls
                # back that admission. Rebuild only an undispatched first plan.
                conflicts += 1
                continue
            raise
