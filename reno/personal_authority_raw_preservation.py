"""Create-only raw preservation and independent read-only package verification.

These factories preserve opaque original bytes. They do not install authority,
grant a writer, restore a database, or infer success from a receipt's presence.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from weakref import WeakKeyDictionary, ref
import uuid

from . import personal_authority_preservation_budget as accounting
from . import personal_authority_preservation_source as source
from . import personal_authority_failure_cleanup as _failure_cleanup
from . import personal_authority_raw_preservation_contract as contract
from . import storage_lifecycle as lifecycle


_LIVE = {}
_OWNERS = {}
_RESULT_TOKEN = object()
_COMPLETED = WeakKeyDictionary()


class AuthorityRawPreservationError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise AuthorityRawPreservationError(suffix) from None


def _checkpoint(phase):
    """Internal fault-injection boundary; never a caller-supplied callback."""


@dataclass(frozen=True, eq=False, init=False)
class RawPreservationResult:
    kind: str
    operation_id: str
    run_id: str
    package_path: Path
    request_sha256: str
    manifest_sha256: str
    receipt_sha256: str
    capsule_sha256: str
    source_projection_sha256: str
    outcome: str
    counters: MappingProxyType

    def __init__(self, token, **values):
        if token is not _RESULT_TOKEN or set(values) != set(self.__dataclass_fields__):
            _fail("COMPLETION_REQUIRED")
        for key, value in values.items():
            object.__setattr__(self, key, MappingProxyType(dict(value)) if key == "counters" else value)


def _limits_copy(value):
    if value is None:
        return contract.default_limits()
    if (type(value) is not dict or len(value) > 32 or any(type(key) is not str for key in value)
            or type(value.get("capsule_limits")) is not dict):
        _fail("LIMIT_INVALID")
    # Fixed pure validation rejects extensions and non-exact nested types.
    try:
        copied = {**value, "capsule_limits": dict(value["capsule_limits"])}
        contract._limits(copied)
    except (contract.Error, contract.c2.AuthorityPreservationContractError):
        _fail("LIMIT_INVALID")
    return copied


def _uuid(value):
    try:
        contract._uuid(value)
    except (contract.Error, contract.c2.AuthorityPreservationContractError):
        _fail("INVALID_ARGUMENT")
    return value


class _Run:
    def __init__(self, profile, guard, operation_id, limits, callback, expected, readonly):
        self.token = object()
        self.owner = lifecycle._owner()
        self.profile, self.guard = profile, guard
        self.operation_id = operation_id
        self.run_id = str(uuid.uuid4())
        self.callback, self.callback_active, self.callback_exception = callback, False, None
        self.budget = accounting.OperationBudget(limits, self.call_user if callback is not None else None,
                                                readonly=readonly)
        self.limits = self.budget.limits
        self.graph = contract._Graph(None, self.limits["max_fact_bytes"])
        self.readonly = readonly
        self.phase = "opening_target"
        self.expected_request_sha256 = expected
        self.target_closed = self.source_closed = self.failure_target = None

    def call_user(self):
        if self.callback_active:
            raise self.budget.remember(AuthorityRawPreservationError("REENTRANT"))
        self.callback_active = True
        try:
            self.callback()
        except BaseException as exc:
            self.callback_exception = exc
            raise
        finally:
            self.callback_active = False


def _original(token):
    if type(token) is not object:
        _fail("OWNER_MISMATCH")
    run = _LIVE.get(token)
    if run is None or run.token is not token:
        _fail("OWNER_MISMATCH")
    if lifecycle._owner() != run.owner:
        _fail("OWNER_MISMATCH")
    if run.callback_active:
        raise run.budget.remember(AuthorityRawPreservationError("REENTRANT"))
    return run


def _operation_record(token):
    run = _original(token)
    run.budget.check(callback=False)
    try:
        if lifecycle.require_guard(run.profile, exclusive=True) is not run.guard:
            _fail("OWNER_MISMATCH")
    except BaseException as exc:
        raise run.budget.remember(exc)
    return run


def _failure_operation_record(token):
    """Original owner/guard only; the budget keeps its first and original clock."""
    run = _original(token)
    if run.readonly or run.budget.first is None or run.budget.remaining_seconds <= 0:
        raise run.budget.remember(AuthorityRawPreservationError("PUBLICATION_FAILED"))
    if lifecycle.require_guard(run.profile, exclusive=True) is not run.guard:
        raise run.budget.remember(AuthorityRawPreservationError("OWNER_MISMATCH"))
    return run


def _stable_failure(run, exc):
    from . import personal_authority_preservation_target as target
    first = run.budget.first if run.budget.first is not None else exc
    if first is run.callback_exception or isinstance(first, AuthorityRawPreservationError):
        return first
    if isinstance(first, (accounting.AuthorityRawPreservationBudgetError, target.AuthorityRawTargetError)):
        return AuthorityRawPreservationError(first.code.removeprefix("AUTHORITY_RAW_PRESERVATION_"))
    if run.budget.snapshot()["uncertain_write_bytes"]:
        return AuthorityRawPreservationError("WRITE_UNCERTAIN")
    if isinstance(first, source.AuthorityRawCaptureError):
        return AuthorityRawPreservationError("CLOSE_FAILED" if first.code.endswith("_CLOSE_FAILED") else "SOURCE_FAILED")
    if run.phase in {"target_close", "publisher_close"}:
        return AuthorityRawPreservationError("CLOSE_FAILED")
    if run.phase in {"source_open", "source_close"}:
        return AuthorityRawPreservationError("SOURCE_FAILED")
    if isinstance(first, contract.Error):
        return AuthorityRawPreservationError("TARGET_UNSAFE")
    return AuthorityRawPreservationError("PUBLICATION_FAILED")


@contextmanager
def _operation(profile, operation_id, limits, callback, *, expected=None, readonly=False):
    # A nested public call is rejected before profile/guard inspection or I/O,
    # including a different operation ID or a verify call from a callback.
    owner = lifecycle._owner()
    from . import personal_authority_preservation_routing as routing
    routing.require_no_reentry(owner)
    if type(profile) is not lifecycle.StorageProfile or callback is not None and not callable(callback):
        _fail("INVALID_ARGUMENT")
    _uuid(operation_id)
    if readonly:
        try:
            contract._sha(expected)
        except (contract.Error, contract.c2.AuthorityPreservationContractError):
            _fail("INVALID_ARGUMENT")
    selected = _limits_copy(limits)
    try:
        guard = lifecycle.require_guard(profile, exclusive=True)
    except lifecycle.StorageLifecycleError:
        _fail("OWNER_MISMATCH")
    run = _Run(profile, guard, operation_id, selected, callback, expected, readonly)
    _LIVE[run.token] = run
    _OWNERS[owner] = run
    try:
        yield run
    except BaseException as exc:
        failure = _stable_failure(run, exc)
        run.budget.remember(exc)
        if not readonly:
            from . import personal_authority_preservation_target as target
            try:
                target.write_failure_once(run.token, phase=run.phase, exc=failure)
            except BaseException:
                _failure_cleanup._note_original(failure, "AUTHORITY_RAW_PRESERVATION_FAILURE_RECORD_UNAVAILABLE")
        raise failure from None
    finally:
        _LIVE.pop(run.token, None)
        if _OWNERS.get(owner) is run:
            del _OWNERS[owner]


def _header(run, kind, **fields):
    return {"format_version": 1, "kind": "authority_raw_preservation_" + kind,
            "operation_id": run.operation_id, "run_id": run.run_id, **fields}


def _fact_row(run, path):
    return {"relative_key": path, "sha256": run.graph.sha(path), "byte_size": len(run.graph.bytes(path))}


def _result(run, kind):
    _operation_record(run.token)
    run.budget.assert_settled()
    request = run.graph.request()
    receipt = run.graph.load("receipt.json", "receipt")
    result = RawPreservationResult(_RESULT_TOKEN, kind=kind, operation_id=run.operation_id,
        run_id=request["run_id"], package_path=run.profile.database.parent / contract.DIRECTORY / run.operation_id,
        request_sha256=run.graph.sha("request.json"), manifest_sha256=run.graph.sha("manifest.json"),
        receipt_sha256=run.graph.sha("receipt.json"), capsule_sha256=run.graph.sha("capsule.json"),
        source_projection_sha256=run.graph.sha("source-projection.json"), outcome=receipt["outcome"],
        counters=run.budget.snapshot())
    # A finished asyncio task can hold this result itself. Its weak-registry
    # value must not keep that task alive and thereby retain its own weak key.
    owner = (run.owner[0], run.owner[1], None if run.owner[2] is None else ref(run.owner[2]))
    _COMPLETED[result] = (owner, run.profile.identity, run.operation_id, kind)
    return result


def create_raw_preservation_locked(profile, *, operation_id, limits=None, check_cancelled=None):
    from . import personal_authority_preservation_target as target
    with _operation(profile, operation_id, limits, check_cancelled) as run:
        with target.new_target_locked(run.token) as held:
            run.phase = "request"
            wire_limits = dict(run.limits)
            wire_limits["capsule_limits"] = dict(run.limits["capsule_limits"])
            request = _header(run, "request", scope=contract.SCOPE,
                layout_profile=contract.c2.LAYOUT_PROFILE, layout_source_sha256=contract.c2.LAYOUT_SOURCE_SHA256,
                profile=target.profile_declaration(held), target=target.target_declaration(held), limits=wire_limits)
            target.publish_control(held, "request.json", "request", request)
            run.expected_request_sha256 = run.graph.sha("request.json")
            run.phase = "run_attempt"
            target.publish_control(held, f"attempts/{run.run_id}.json", "run_attempt", _header(run, "run_attempt",
                request_sha256=run.expected_request_sha256, operation_identity=request["target"]["operation_identity"],
                reserved_read_bytes=run.limits["max_read_bytes"], reserved_write_bytes=run.limits["max_write_bytes"],
                failure_reserve_bytes=run.limits["max_failure_bytes"], deadline_ms=run.limits["deadline_ms"]))
            run.phase = "source_open"
            with source._capture_for_preservation_locked(run.token) as captured:
                metadata = source.source_metadata(captured)
                target.publish_control(held, "source-metadata.json", "source_metadata", metadata)
                target.publish_control(held, "source-projection.json", "source_projection", metadata["source_projection"])
                run.phase = "selection"
                target.publish_control(held, "selection.json", "selection", _header(run, "selection",
                    request_sha256=run.expected_request_sha256,
                    source_metadata_sha256=run.graph.sha("source-metadata.json"),
                    source_projection_sha256=run.graph.sha("source-projection.json"),
                    source_file_ids=sorted(row["entry_id"] for row in metadata["capsule"]["files"])))
                source._bind_preservation_selection_original(captured, run.token)
                run.phase = "copy"
                for entry_id in run.graph.selection()["source_file_ids"]:
                    target.copy_entry(held, captured, entry_id)
                run.phase = "source_close"
            run.source_closed = source._consume_closed_for_preservation(captured, run.token)
            _checkpoint("source_closed")
            capsule = json.loads(run.graph.bytes("source-metadata.json"))["capsule"]
            capsule["copies"] = [row["copy"] for row in run.graph.records()]
            capsule["coverage"]["declared_copies"] = "complete" if capsule["files"] else "none"
            target.publish_control(held, "capsule.json", "capsule", capsule)
            run.phase = "target_close"
        run.target_closed = target.consume_closed(held, run.token)
        run.budget.assert_settled()
        _checkpoint("target_closed")
        run.phase = "publication"
        with target.existing_target_locked(run.token, mode="publisher") as publisher:
            copy_records = [{"source_entry_id": entry_id,
                             "sha256": run.graph.sha(f"copy-records/{entry_id}.json"),
                             "byte_size": len(run.graph.bytes(f"copy-records/{entry_id}.json"))}
                            for entry_id in run.graph.selection()["source_file_ids"]]
            target.publish_control(publisher, "source-verified.json", "source_verified", _header(run, "source_verified",
                request_sha256=run.expected_request_sha256, selection_sha256=run.graph.sha("selection.json"),
                capsule_sha256=run.graph.sha("capsule.json"), source_closed=run.source_closed,
                target_closed=run.target_closed, copy_records=copy_records))
            fact_names = {row["relative_key"] for row in run.target_closed["files"]
                          if not row["relative_key"].startswith("payload/")}
            fact_names.add("source-verified.json")
            copies = run.graph.load("capsule.json", "capsule")["copies"]
            outcome = "copied" if copies else "empty_source"
            target.publish_control(publisher, "manifest.json", "manifest", _header(run, "manifest",
                scope=contract.SCOPE, request_sha256=run.expected_request_sha256,
                selection_sha256=run.graph.sha("selection.json"),
                source_verified_sha256=run.graph.sha("source-verified.json"), outcome=outcome,
                facts=[_fact_row(run, name) for name in sorted(fact_names, key=lambda text: text.encode("utf-8"))],
                payloads=[{"source_entry_id": row["source_entry_id"], "relative_key": row["target_relative_key"],
                           "sha256": row["sha256"], "byte_size": row["byte_size"]} for row in copies]))
            run.budget.mark_receipt_started()
            target.publish_control(publisher, "receipt.json", "receipt", _header(run, "receipt", scope=contract.SCOPE,
                request_sha256=run.expected_request_sha256,
                source_verified_sha256=run.graph.sha("source-verified.json"),
                manifest_sha256=run.graph.sha("manifest.json"), outcome=outcome, publication="published_observed"))
            contract.cross_validate_package(fact_bytes=run.graph.raw, max_fact_bytes=run.limits["max_fact_bytes"])
            _checkpoint("receipt_published")
            run.phase = "publisher_close"
        target.consume_closed(publisher, run.token)
        run.budget.assert_settled()
        _checkpoint("publisher_closed")
        return _result(run, "raw_preservation_created")


def verify_raw_preservation_locked(profile, *, operation_id, expected_request_sha256,
                                   limits=None, check_cancelled=None):
    from . import personal_authority_preservation_target as target
    with _operation(profile, operation_id, limits, check_cancelled,
                    expected=expected_request_sha256, readonly=True) as run:
        with target.existing_target_locked(run.token, mode="verify") as held:
            # Target acquisition reads the full current package and all payloads;
            # this pure check only binds those actual immutable control bytes.
            contract.cross_validate_package(fact_bytes=run.graph.raw, max_fact_bytes=run.limits["max_fact_bytes"])
            run.phase = "publisher_close"
        target.consume_closed(held, run.token)
        return _result(run, "raw_preservation_verified")
