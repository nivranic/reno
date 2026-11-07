"""Private source opportunities: paid recovery before gates, one CAS runner.

No URL, cookie, metadata or artifact path is exposed through diagnostics. The
normal runner receipt is settled before callers register files or children.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from . import budget, budget_contract as contract, config, db, storage_admin, storage_lifecycle, tasks, worker


@dataclass(frozen=True, slots=True, repr=False)
class SourceScope:
    profile: storage_lifecycle.StorageProfile
    continuation_id: str
    execution_generation: int
    root_job_id: int
    business_connection: sqlite3.Connection | None = None
    home_execution: object | None = None
    check_cancelled: object | None = None
    home_client: object | None = None

    def __post_init__(self):
        if type(self.profile) is not storage_lifecycle.StorageProfile or self.root_job_id is None:
            _fail("BUDGET_IDENTITY_INVALID")
        budget.OperationSpec(self.continuation_id, self.execution_generation, "source_metadata",
                             "ingest:metadata:v1", "0" * 64, self.root_job_id)
        if self.business_connection is not None and type(self.business_connection) is not sqlite3.Connection:
            _fail("BUDGET_BUSINESS_TRANSACTION_ACTIVE")
        if self.check_cancelled is not None and not callable(self.check_cancelled):
            _fail("BUDGET_IDENTITY_INVALID")
        if self.home_execution is not None:
            from .sync_source_execution import HomeExecutionBinding
            from .sync_source_binding17 import HomeBindingFacts17, require_binding
            binding = self.home_execution
            if type(binding) not in (HomeExecutionBinding, HomeBindingFacts17) or binding.profile.identity != self.profile.identity:
                _fail("BUDGET_IDENTITY_INVALID")
            if type(binding) is HomeBindingFacts17:
                from .sync_client import SyncClient
                require_binding(binding, profile=self.profile)
                if type(self.home_client) is not SyncClient:
                    _fail("BUDGET_IDENTITY_INVALID")
            core = binding.core
            if ((core["root_job_id"], core["continuation_id"], core["execution_generation"]) !=
                    (self.root_job_id, self.continuation_id, self.execution_generation)
                    or hashlib.sha256(binding.submitted_url.encode()).hexdigest() != core["submitted_url_sha256"]):
                _fail("BUDGET_IDENTITY_INVALID")


@dataclass(slots=True)
class _Session:
    scope: SourceScope
    tracked: bool = False


_SCOPE = ContextVar("reno_source_scope", default=None)
_LEGACY = ContextVar("reno_source_legacy_window", default=False)


class SourceProcessError(ValueError):
    code = "SOURCE_PROCESS_FAILED"

    def __init__(self):
        super().__init__(self.code)


def _fail(code="BUDGET_REQUEST_INVALID"):
    raise budget.BudgetLedgerError(code) from None


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _info(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _settings():
    return contract.parse_policy(config.budget_settings())


def current_scope():
    value = _SCOPE.get()
    return None if value is None else value.scope


@contextmanager
def scope(value):
    if type(value) is not SourceScope:
        _fail("BUDGET_IDENTITY_INVALID")
    job, profile = worker.execution_context(), worker._execution_profile()
    if (job is None or profile is None or profile.identity != value.profile.identity
            or (job["job_id"], job["continuation_id"], job["execution_generation"]) !=
            (value.root_job_id, value.continuation_id, value.execution_generation)):
        raise contract.BudgetDeferred("unsupported_capability")
    parent = _SCOPE.get()
    session = _Session(value, value.home_execution is not None or (parent is not None and parent.tracked))
    token = _SCOPE.set(session)
    try:
        yield value
    finally:
        if parent is not None and session.tracked:
            parent.tracked = True
        _SCOPE.reset(token)


@contextmanager
def source_scope(profile, *, business_connection=None):
    job = worker.execution_context()
    if job is None or worker._execution_profile() is None or worker._execution_profile().identity != profile.identity:
        raise contract.BudgetDeferred("unsupported_capability")
    with scope(SourceScope(profile, job["continuation_id"], job["execution_generation"],
            job["job_id"], business_connection)) as value:
        yield value


@contextmanager
def home_source_scope(binding, *, client=None, check_cancelled=None, business_connection=None):
    """Keep original Home logical identity while approved transport URLs change."""
    from .sync_source_execution import HomeExecutionBinding
    from .sync_source_binding17 import HomeBindingFacts17
    if type(binding) not in (HomeExecutionBinding, HomeBindingFacts17):
        _fail("BUDGET_IDENTITY_INVALID")
    core = binding.core
    with scope(SourceScope(binding.profile, core["continuation_id"], core["execution_generation"],
            core["root_job_id"], business_connection, binding, check_cancelled, client)) as value:
        yield value


def _components():
    from . import source_contract, source_runner
    return source_contract, source_runner


def _business_ready():
    value = current_scope()
    if value is not None and value.business_connection is not None and value.business_connection.in_transaction:
        _fail("BUDGET_BUSINESS_TRANSACTION_ACTIVE")


def _gate(connection=None):
    value = current_scope()
    if config.is_cloud():
        raise contract.BudgetDeferred("unsupported_capability")
    if value is not None:
        job, profile = worker.execution_context(), worker._execution_profile()
        if (job is None or profile is None or profile.identity != value.profile.identity
                or storage_admin.profile().identity != value.profile.identity
                or (job["job_id"], job["continuation_id"], job["execution_generation"]) !=
                (value.root_job_id, value.continuation_id, value.execution_generation)):
            raise contract.BudgetDeferred("ledger_unavailable")
    try:
        worker.check_admission(**({} if connection is None else {"connection": connection}))
        if value is not None and value.home_execution is not None:
            from .sync_source_binding17 import HomeBindingFacts17, require_binding
            if type(value.home_execution) is HomeBindingFacts17:
                # Stable facts never masquerade as current permission. The
                # actual reserve/dispatch guards consume their lexical view.
                require_binding(value.home_execution, profile=value.profile)
            else:
                value.home_execution.check(connection=connection)
        if value is not None and value.check_cancelled is not None:
            value.check_cancelled()
    except tasks.TaskError as error:
        if error.code == "TASK_OWNER_LOST":
            raise contract.BudgetDeferred("ledger_unavailable") from None
        raise


def ensure_legacy_dispatch():
    """Every naked subprocess checks fresh policy immediately before spawn."""
    _business_ready()
    session = _SCOPE.get()
    if _settings().budget_enabled or (session is not None and (session.tracked or not _LEGACY.get())):
        _fail("BUDGET_REQUEST_CONFLICT")
    _gate()
    if _settings().budget_enabled:
        _fail("BUDGET_REQUEST_CONFLICT")


def _legacy(callback):
    if not callable(callback):
        raise contract.BudgetDeferred("unsupported_capability")
    token = _LEGACY.set(True)
    try:
        ensure_legacy_dispatch()
        return callback()
    finally:
        _LEGACY.reset(token)


def _operation(plan, *, input_sha256=None):
    value = current_scope()
    if value is None:
        return None
    if plan.kind == "source_metadata":
        key = "ingest:metadata:v1"
        digest = _sha({"kind": plan.kind, "source": _logical_source(plan.source), "runner_contract": "yt_dlp_v1"})
    elif plan.kind == "source_enumeration":
        key = "favlist:enumeration:v1"
        if value.home_execution is not None:
            _fail("BUDGET_REQUEST_CONFLICT")
        digest = _sha({"kind": plan.kind, "source": plan.source, "runner_contract": "yt_dlp_v1"})
    elif plan.kind == "download":
        from .sync_protocol import validate_video_id
        vid = validate_video_id(json.loads(plan.info_json)["id"])
        key = "ingest:media:v1:" + vid
        digest = _sha({"kind": plan.kind, "source": _logical_source(plan.source), "runner_contract": "yt_dlp_v1",
                       "genesis_metadata_sha256": hashlib.sha256(plan.info_json.encode()).hexdigest()})
    else:
        _fail()
    if value.home_execution is not None:
        return value.home_execution.operation(plan.kind, key, digest if input_sha256 is None else input_sha256)
    return budget.OperationSpec(value.continuation_id, value.execution_generation, plan.kind,
        key, digest if input_sha256 is None else input_sha256, value.root_job_id)


def _logical_source(source):
    value = current_scope()
    return source if value is None or value.home_execution is None else value.home_execution.submitted_url


class _HomeStop:
    """Cancellation observation never rechecks manual admission of a paid call."""
    def __init__(self, requested, check_cancelled):
        self.requested, self.check_cancelled, self.error = requested, check_cancelled, None

    def is_set(self):
        if self.requested is not None and self.requested.is_set():
            return True
        if self.error is not None:
            return True
        if self.check_cancelled is not None:
            try:
                self.check_cancelled()
            except BaseException as error:
                self.error = error
                return True
        return False


def _restore(attempt, operation):
    value = current_scope()
    codec, runner = _components()
    if attempt.source_contract_version != 1:
        raise contract.BudgetDeferred("unsupported_capability", operation_id=_view(operation).operation_id)
    receipt = budget.get_result(attempt.attempt_id, attempt.request_sha256, profile=value.profile)
    if not receipt.response_complete:
        raise contract.BudgetDeferred("indeterminate", operation_id=_view(operation).operation_id)
    if receipt.storage_state != "stored" or receipt.text is None:
        raise contract.BudgetDeferred("result_capacity", operation_id=_view(operation).operation_id)
    result = codec.decode_envelope(receipt.text, request_sha256=attempt.request_sha256,
        input_sha256=operation.input_sha256, kind=operation.kind, segment=attempt.download_segment)
    if attempt.outcome == "source_retry":
        codec.validate_retry_envelope(receipt.text, request_sha256=attempt.request_sha256,
            input_sha256=operation.input_sha256, kind=operation.kind, segment=attempt.download_segment)
    if result.artifact is not None:
        with storage_lifecycle.shared(value.profile):
            runner.verify_artifact(result.artifact, profile=value.profile)
    return result, receipt


def _view(operation):
    result = budget.get_operation(operation, profile=current_scope().profile)
    if result is not None:
        _SCOPE.get().tracked = True
    return result


def _limit(plan):
    return 1 if plan.phase == "refresh" and plan.kind == "source_metadata" or plan.segment == "audio" else 4


def _phase_attempts(view, plan):
    if view is None:
        return ()
    if plan.kind == "download":
        return tuple(item for item in view.attempts if item.download_segment == plan.segment)
    return tuple(item for item in view.attempts if item.request_sha256 == plan.request_sha256)


def _cookies(plan, explicit):
    if explicit is not None:
        return explicit
    if plan.kind == "download" and "bilibili" in plan.source:
        compatibility = config.ROOT.parent / "reno-feas/cache/bili_cookies.txt"
        if compatibility.is_file():
            return compatibility
    return config.get("ytdlp_cookies") or None


def _prepare(plan, policy, cookies, resume, verified_files=None):
    _, runner = _components()
    return runner.prepare(plan, profile=current_scope().profile, policy=policy,
        cookies=_cookies(plan, cookies), extra_args=config.get("ytdlp_extra_args") or (), resume_artifacts=resume,
        **({} if verified_files is None else {"verified_files": verified_files}))


def _unknown(reservation, request_sha):
    try:
        budget.settle(reservation.attempt_id, request_sha, profile=current_scope().profile, response_complete=False)
    except BaseException:
        pass  # The committed dispatching state already blocks a resend.


def _validate_refresh_result(plan, result):
    if plan.kind == "source_metadata" and plan.phase == "refresh" and result.successful:
        genesis = _genesis(plan.source)
        if genesis is None:
            raise contract.BudgetDeferred("result_capacity")
        if json.loads(result.metadata_json)["id"] != json.loads(genesis.metadata_json)["id"]:
            _fail("BUDGET_REQUEST_CONFLICT")
    return result


def run_source(plan, *, legacy=None, cookies=None, input_sha256=None):
    """One physical opportunity; facade loops only complete retry receipts."""
    _business_ready()
    value = current_scope()
    codec, runner = _components()
    if type(plan) is not codec.SourcePlan:
        _fail()
    if value is not None and (value.home_execution is not None or plan.kind == "download" or plan.phase == "refresh"):
        input_sha256 = _authorize_plan(plan, input_sha256)
    operation = _operation(plan, input_sha256=input_sha256)
    view = None if operation is None else _view(operation)
    if view is not None:
        _SCOPE.get().tracked = True
    matching = () if view is None else tuple(item for item in view.attempts if item.request_sha256 == plan.request_sha256)
    resume = ()
    if matching:
        latest = matching[-1]
        if latest.state == "settled" and latest.response_complete:
            result, _ = _restore(latest, operation)
            if latest.outcome != "source_retry":
                return _validate_refresh_result(plan, result)
            resume = result.partials
        elif latest.state in {"dispatching", "indeterminate"}:
            raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
    if view is not None and any(item.state in {"dispatching", "indeterminate"} for item in view.attempts):
        raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
    from .sync_source_binding17 import require_home17_effect_scope
    require_home17_effect_scope(None if value is None else value.home_execution,
        profile=None if value is None else value.profile,
        root_job_id=None if value is None else value.root_job_id)
    settings = _settings()
    if not settings.budget_enabled and view is None and (_SCOPE.get() is None or not _SCOPE.get().tracked):
        _gate()
        settings = _settings()
        if not settings.budget_enabled:
            return _legacy(legacy)
    if value is None:
        raise contract.BudgetDeferred("unsupported_capability")
    _SCOPE.get().tracked = True
    phase_count = len(_phase_attempts(view, plan))
    ordinal = matching[-1].ordinal if matching and matching[-1].state == "reserved" else 1 if view is None else view.latest_ordinal + 1
    if phase_count >= _limit(plan) and not (matching and matching[-1].state == "reserved"):
        raise contract.BudgetDeferred("attempt_limit", operation_id=None if view is None else view.operation_id)
    _gate()
    preflight = _prepare(plan, settings, cookies, resume)
    verifier = getattr(runner, "verify_prepared_files", None)
    if not callable(verifier) and getattr(runner, "__name__", None) == "reno.source_runner":
        raise contract.BudgetDeferred("unsupported_capability")
    # Full resume hashes stay outside both budget SQL writer transactions.
    # Explicit synthetic runner fixtures may omit the physical-file helper.
    verified_files = None if not callable(verifier) else verifier(preflight)
    admission = nullcontext(None)
    if value.home_execution is not None:
        from .sync_source_binding17 import HomeBindingFacts17
        if type(value.home_execution) is HomeBindingFacts17:
            from .sync_source_admission17 import capture_current_admission_locked
            admission = capture_current_admission_locked(profile=value.profile, store=value.home_execution.store,
                client=value.home_client, item_id=value.home_execution.core["item_id"], binding=value.home_execution)
    with admission as current_admission:
        def guard(connection):
            _gate(connection)
            _prepare(plan, _settings(), cookies, resume, verified_files)  # Fast capability/stat checks only.
            if current_admission is not None:
                from . import budget_writer_witness
                writer = budget_writer_witness.require_writer(connection, profile=value.profile)
                current_admission.verify_for_commit(connection, writer_permit=writer)
        reservation = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, ordinal,
            download_segment=plan.segment, source_contract_version=1), profile=value.profile,
            business_connection=value.business_connection, reservation_guard=guard)
        try:
            _gate()
            dispatched = budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
                str(uuid.uuid4()), expected_revision=reservation.revision, profile=value.profile,
                business_connection=value.business_connection, dispatch_guard=guard)
        except contract.BudgetDeferred as waiting:
            if waiting.operation_id is None:
                raise contract.BudgetDeferred(waiting.reason, resume_at=waiting.resume_at,
                                              operation_id=reservation.operation_id) from None
            raise
    stopped = _HomeStop(getattr(worker._current, "stop", None), value.check_cancelled) if value.home_execution is not None else None
    if not dispatched.won:
        current = _view(operation)
        attempt = next(item for item in current.attempts if item.attempt_id == reservation.attempt_id)
        return _validate_refresh_result(plan, _restore(attempt, operation)[0])
    try:
        frozen_policy = budget.get_dispatch_policy(reservation.attempt_id, plan.request_sha256, profile=value.profile)
        prepared = _prepare(plan, frozen_policy, cookies, resume, verified_files)
        result = runner.run(prepared, stop_event=stopped if stopped is not None else getattr(worker._current, "stop", None))
        if type(result) is not codec.SourceProcessResult:
            _fail()
    except BaseException as error:
        _unknown(reservation, plan.request_sha256)
        if not isinstance(error, Exception):
            raise
        if stopped is not None and stopped.error is not None:
            raise stopped.error
        raise contract.BudgetDeferred("indeterminate", operation_id=reservation.operation_id) from None
    if not result.response_complete:
        _unknown(reservation, plan.request_sha256)
        if stopped is not None and stopped.error is not None:
            raise stopped.error
        raise contract.BudgetDeferred("indeterminate", operation_id=reservation.operation_id)
    try:
        text = codec.encode_envelope(plan, result, input_sha256=operation.input_sha256)
    except codec.SourceContractError:
        # Preserve a complete normal process observation without copying an
        # unsafe/oversized projection into storage or granting source retry.
        safe = codec.SourceProcessResult(plan.request_sha256, result.process_exited, result.drain_complete,
            result.returncode, result.termination, stdout_bytes=result.stdout_bytes,
            stdout_sha256=result.stdout_sha256, stderr_bytes=result.stderr_bytes,
            stderr_sha256=result.stderr_sha256, failure_class="invalid_output")
        try:
            budget.settle(reservation.attempt_id, plan.request_sha256, profile=value.profile,
                outcome="terminal_error", result_text=codec.encode_envelope(plan, safe, input_sha256=operation.input_sha256))
        except Exception:
            raise contract.BudgetDeferred("ledger_unavailable", operation_id=reservation.operation_id) from None
        raise contract.BudgetDeferred("result_capacity", operation_id=reservation.operation_id) from None
    if result.successful:
        outcome = "source_step_complete" if plan.kind == "source_metadata" and plan.phase == "initial" or plan.segment == "video" else "full_result"
    else:
        outcome = "terminal_error"
        if (plan.segment != "audio" and not (plan.kind == "source_metadata" and plan.phase == "refresh")
                and phase_count + 1 < _limit(plan) and reservation.ordinal < _view(operation).max_attempts):
            try:
                codec.validate_retry_envelope(text, request_sha256=plan.request_sha256,
                    input_sha256=operation.input_sha256, kind=plan.kind, segment=plan.segment)
                outcome = "source_retry"
            except codec.SourceContractError:
                pass
    try:
        budget.settle(reservation.attempt_id, plan.request_sha256, profile=value.profile,
                      outcome=outcome, result_text=text)
    except contract.BudgetDeferred:
        raise
    except budget.BudgetLedgerError:
        raise contract.BudgetDeferred("ledger_unavailable", operation_id=reservation.operation_id) from None
    if stopped is not None and stopped.error is not None:
        raise stopped.error
    return _validate_refresh_result(plan, result)


def _bounded(plan, *, legacy=None, cookies=None, input_sha256=None):
    while True:
        result = run_source(plan, legacy=legacy, cookies=cookies, input_sha256=input_sha256)
        codec, _ = _components()
        if type(result) is not codec.SourceProcessResult:
            return result
        if result.successful:
            return result
        view = _view(_operation(plan, input_sha256=input_sha256))
        matching = tuple(item for item in view.attempts if item.request_sha256 == plan.request_sha256)
        if not matching or matching[-1].outcome != "source_retry":
            raise SourceProcessError()


def _initial_plan(url):
    codec, _ = _components()
    value = current_scope()
    if value is not None and value.home_execution is not None:
        initial, refreshed = _home_resolutions()
        candidates = [codec.SourcePlan("source_metadata", item.resolution["resolved_url"])
                      for item in (initial, refreshed) if item is not None]
        if url not in {plan.source for plan in candidates}:
            _fail("BUDGET_REQUEST_CONFLICT")
        view = _view(_operation(candidates[-1]))
        if view is not None and view.attempts:
            # The first frozen request remains the genesis even after a URL
            # refresh. A missing/corrupt paid body is never a new operation.
            for plan in candidates:
                if plan.request_sha256 == view.attempts[0].request_sha256:
                    return plan
            _fail("BUDGET_REQUEST_CONFLICT")
        return candidates[-1]
    return codec.SourcePlan("source_metadata", url)


def _home_resolutions():
    from .source_resolution_budget import completed_resolution
    binding = current_scope().home_execution
    initial = completed_resolution(binding)
    if initial is None:
        raise contract.BudgetDeferred("result_capacity")
    return initial, completed_resolution(binding, 1)


def _home_age_refresh_consumed():
    value = current_scope()
    if value is None or value.home_execution is None:
        return False
    initial, refreshed = _home_resolutions()
    return refreshed is not None and (refreshed.refresh_attempt_id, refreshed.refresh_settlement_sha256) == (
        initial.attempt_id, initial.settlement_sha256)


def _genesis(url):
    plan = _initial_plan(url)
    operation = _operation(plan)
    view = None if operation is None else _view(operation)
    if view is None:
        return None
    matching = tuple(item for item in view.attempts if item.request_sha256 == plan.request_sha256)
    for item in reversed(matching):
        if item.state == "settled" and item.response_complete:
            result, _ = _restore(item, operation)
            if result.successful:
                return result
            if item.outcome == "terminal_error":
                raise SourceProcessError()
    return None


def _failure_authorization(url, genesis):
    codec, _ = _components()
    info = json.loads(genesis.metadata_json)
    vid = info["id"]
    plan = codec.SourcePlan("download", url, segment="video", info_json=genesis.metadata_json,
        artifact_relpath=f"originals/{vid}/source-{hashlib.sha256(genesis.metadata_json.encode()).hexdigest()}/video.mp4")
    operation = _operation(plan)
    view = _view(operation)
    if view is None:
        return None
    for item in view.attempts:
        if item.download_segment == "video" and item.outcome == "source_retry" and item.response_complete:
            result, receipt = _restore(item, operation)
            if result.successful or receipt.settlement_sha256 is None:
                _fail("BUDGET_LEDGER_INVALID")
            return item.attempt_id, receipt.settlement_sha256
    return None


def _refresh_plan(url, genesis, *, resolve=False):
    codec, _ = _components()
    authorization = _failure_authorization(url, genesis)
    if authorization is None:
        return None
    value = current_scope()
    if value is not None and value.home_execution is not None:
        from . import source_resolution_budget as resolver
        initial, refreshed = _home_resolutions()
        if _home_age_refresh_consumed():
            return None  # One generation already used before metadata dispatch.
        if refreshed is None:
            if not resolve:
                return None  # A metadata lookup never starts incidental DNS.
            resolver.resolve_url(value.home_execution, client=value.home_client, check_cancelled=value.check_cancelled,
                refresh_authorization=resolver.RefreshAuthorization(*authorization))
            refreshed = resolver.completed_resolution(value.home_execution, 1)
        if (refreshed is None or (refreshed.refresh_attempt_id, refreshed.refresh_settlement_sha256) != authorization):
            _fail("BUDGET_REQUEST_CONFLICT")
        url = refreshed.resolution["resolved_url"]
    return codec.SourcePlan("source_metadata", url, phase="refresh",
        refresh_attempt_id=authorization[0], refresh_settlement_sha256=authorization[1])


def _authorize_plan(plan, requested_input):
    """Refresh authority is an original retained failed-process fact."""
    value = current_scope()
    if value.home_execution is not None and plan.kind == "source_metadata" and plan.phase == "initial":
        expected_plan = _initial_plan(plan.source)
        if plan.request_sha256 != expected_plan.request_sha256:
            _fail("BUDGET_REQUEST_CONFLICT")
        expected = _operation(plan).input_sha256
        if requested_input is not None and requested_input != expected:
            _fail("BUDGET_REQUEST_CONFLICT")
        return expected
    genesis = _genesis(plan.source)
    if genesis is None:
        raise contract.BudgetDeferred("result_capacity")
    if plan.phase == "refresh":
        refresh = _refresh_plan(plan.source, genesis)
        if (refresh is None or plan.source != refresh.source or plan.refresh_attempt_id != refresh.refresh_attempt_id
                or plan.refresh_settlement_sha256 != refresh.refresh_settlement_sha256):
            _fail("BUDGET_REQUEST_CONFLICT")
        metadata_view = _view(_operation(refresh))
        initial_sha = _initial_plan(plan.source).request_sha256
        refresh_attempts = () if metadata_view is None else tuple(
            item for item in metadata_view.attempts if item.request_sha256 != initial_sha)
        if any(item.request_sha256 != refresh.request_sha256 for item in refresh_attempts):
            _fail("BUDGET_REQUEST_CONFLICT")
        if plan.kind == "download":
            view = _view(_operation(refresh))
            matching = () if view is None else tuple(item for item in view.attempts if item.request_sha256 == refresh.request_sha256)
            if not matching:
                raise contract.BudgetDeferred("result_capacity")
            latest = matching[-1]
            if latest.state in {"dispatching", "indeterminate"}:
                raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
            if latest.state != "settled":
                raise contract.BudgetDeferred("result_capacity", operation_id=view.operation_id)
            current, _ = _restore(latest, _operation(refresh))
            if not current.successful:
                raise SourceProcessError()
            _validate_refresh_result(refresh, current)
            if plan.info_json != current.metadata_json:
                _fail("BUDGET_REQUEST_CONFLICT")
    elif plan.kind == "download":
        if (plan.info_json != genesis.metadata_json or value.home_execution is not None
                and plan.source != _initial_plan(plan.source).source):
            _fail("BUDGET_REQUEST_CONFLICT")
    if plan.kind != "download":
        expected = _operation(plan).input_sha256
    else:
        expected = _sha({"kind": "download", "source": _logical_source(_initial_plan(plan.source).source),
            "runner_contract": "yt_dlp_v1", "genesis_metadata_sha256": hashlib.sha256(genesis.metadata_json.encode()).hexdigest()})
    if requested_input is not None and requested_input != expected:
        _fail("BUDGET_REQUEST_CONFLICT")
    return expected


def _paid_segment(operation, segment):
    view = _view(operation)
    if view is None:
        return None
    selected = tuple(item for item in view.attempts if item.download_segment == segment)
    for item in reversed(selected):
        if item.state == "settled" and item.response_complete:
            result, _ = _restore(item, operation)
            if result.successful or item.outcome == "terminal_error":
                return result
        elif item.state in {"dispatching", "indeterminate"}:
            raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
    if any(item.state in {"dispatching", "indeterminate"} for item in view.attempts):
        raise contract.BudgetDeferred("indeterminate", operation_id=view.operation_id)
    return None


def _audio_fallback(result):
    return (result.response_complete and type(result.returncode) is int and result.returncode > 0
            and result.failure_class == "process_failure")


def metadata(url, *, legacy=None):
    """Reuse current paid metadata without launching an incidental refresh."""
    codec, _ = _components()
    plan = _initial_plan(url)
    result = _bounded(plan, legacy=legacy)
    if type(result) is not codec.SourceProcessResult:
        return result
    refresh = _refresh_plan(url, result)
    if refresh is not None:
        view = _view(_operation(refresh))
        matching = () if view is None else tuple(item for item in view.attempts if item.request_sha256 == refresh.request_sha256)
        if matching:
            refreshed = _bounded(refresh)
            return json.loads(refreshed.metadata_json)
    return json.loads(result.metadata_json)


def enumeration(url, *, legacy=None, cookies=None):
    codec, _ = _components()
    result = _bounded(codec.SourcePlan("source_enumeration", url), legacy=legacy, cookies=cookies)
    return list(result.items) if type(result) is codec.SourceProcessResult else result


def _registered(url, vid, segment):
    """Only exact source/content-bound registered media may bypass a new send."""
    value = current_scope()
    if value is None:
        return None
    with storage_admin.connection(value.profile) as con:
        asset = db.get_asset(con, vid)
        if asset is None or asset["source_url"] != url:
            return None
        files = db.uj(asset["files_json"], {})
        raw = files.get(segment)
        digest = asset["content_sha256"] if segment == "video" else asset["audio_sha256"]
        if not raw or not digest:
            return None
        path = Path(raw)
        path = path if path.is_absolute() else value.profile.media.parent / path
        try:
            relative = path.absolute().relative_to(value.profile.media.absolute()).as_posix()
            stat = path.stat()
            codec, runner = _components()
            artifact = codec.SourceArtifact(segment, relative, stat.st_size, digest,
                stat.st_dev, stat.st_ino, stat.st_mtime_ns)
            return runner.verify_artifact(artifact, profile=value.profile)
        except (ValueError, OSError):
            _fail("BUDGET_REQUEST_CONFLICT")


def media(url, info, vid, segment, *, legacy=None):
    if segment not in ("video", "audio"):
        _fail()
    value = current_scope()
    codec, runner = _components()
    genesis = _genesis(url) if value is not None else None
    if genesis is None:
        if not _settings().budget_enabled and (_SCOPE.get() is None or not _SCOPE.get().tracked):
            return _legacy(legacy)
        raise contract.BudgetDeferred("result_capacity")
    if json.loads(genesis.metadata_json)["id"] != vid:
        _fail("BUDGET_REQUEST_CONFLICT")
    genesis_plan = codec.SourcePlan("download", _initial_plan(url).source, segment=segment, info_json=genesis.metadata_json,
        artifact_relpath=f"originals/{vid}/source-{hashlib.sha256(genesis.metadata_json.encode()).hexdigest()}/{'video.mp4' if segment == 'video' else 'audio.m4a'}")
    paid = _paid_segment(_operation(genesis_plan), segment)
    if paid is not None:
        if paid.successful:
            with storage_lifecycle.shared(value.profile):
                return runner.verify_artifact(paid.artifact, profile=value.profile)
        if segment == "audio":
            if _audio_fallback(paid):
                return None
            raise SourceProcessError()
        raise SourceProcessError()
    registered = _registered(url, vid, segment)
    if registered is not None:
        return registered
    original_sha = hashlib.sha256(genesis.metadata_json.encode()).hexdigest()
    op_input = _sha({"kind": "download", "source": _logical_source(_initial_plan(url).source),
                    "runner_contract": "yt_dlp_v1", "genesis_metadata_sha256": original_sha})
    current = genesis
    refresh = _refresh_plan(url, genesis, resolve=True)
    if refresh is not None:
        current = _bounded(refresh)  # Exactly one phase opportunity, original op.
    if _info(info) not in (genesis.metadata_json, current.metadata_json):
        _fail("BUDGET_REQUEST_CONFLICT")
    while True:
        current_sha = hashlib.sha256(current.metadata_json.encode()).hexdigest()
        authorization = {} if refresh is None else {"phase": "refresh",
            "refresh_attempt_id": refresh.refresh_attempt_id,
            "refresh_settlement_sha256": refresh.refresh_settlement_sha256}
        plan = codec.SourcePlan("download", _initial_plan(url).source if refresh is None else refresh.source,
            segment=segment, info_json=current.metadata_json,
            artifact_relpath=f"originals/{vid}/source-{current_sha}/{'video.mp4' if segment == 'video' else 'audio.m4a'}",
            **authorization)
        result = run_source(plan, input_sha256=op_input)
        if result.successful:
            with storage_lifecycle.shared(value.profile):
                return runner.verify_artifact(result.artifact, profile=value.profile)
        operation = _operation(plan, input_sha256=op_input)
        view = _view(operation)
        latest = tuple(item for item in view.attempts if item.request_sha256 == plan.request_sha256)[-1]
        if segment == "audio":
            if latest.outcome == "terminal_error" and _audio_fallback(result):
                return None  # Only a complete normal failure gets the fallback.
            raise SourceProcessError()
        if latest.outcome != "source_retry":
            raise SourceProcessError()
        if refresh is None:
            refresh = _refresh_plan(url, genesis, resolve=True)
            if refresh is None:
                if _home_age_refresh_consumed():
                    continue  # Bounded video retries retain the original paid metadata.
                _fail("BUDGET_LEDGER_INVALID")
            current = _bounded(refresh)
