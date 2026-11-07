"""Two fixed producer slots and explicit readonly verification for raw format2.

Native admission and target ownership remain in the fixed target factory.
These functions never resume a v1 package, refund an old slot, infer C from a
receipt, restore data, or grant an authority writer.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from weakref import WeakKeyDictionary, ref

from . import personal_authority_raw_preservation as first
from . import personal_authority_raw_preservation2_contract as contract
from . import personal_authority_preservation_source as source
from . import personal_authority_failure_cleanup as _failure_cleanup
from . import personal_authority_preservation_routing as routing
from . import personal_authority_resume_policy as policy
from . import storage_lifecycle as lifecycle


_LIVE = {}
_OWNERS = {}
_RESULT_TOKEN = object()
_COMPLETED = WeakKeyDictionary()
AuthorityRawPreservationError = first.AuthorityRawPreservationError


def _fail(suffix):
    raise AuthorityRawPreservationError(suffix) from None


def _checkpoint(phase):
    """Internal test fault boundary, never a caller-supplied publication hook."""


@dataclass(frozen=True, eq=False, init=False)
class RawPreservation2Result:
    kind: str
    operation_id: str
    generation: int
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


class _Run(first._Run):
    def __init__(self, profile, operation_id, slot, mode, limits, callback, expected):
        # Only fixed private construction reuses the original callback/ledger
        # mechanics. Start its clock before the first guard inspection.
        super().__init__(profile, None, operation_id, limits, callback, expected, mode == "verify")
        self.graph = contract._Graph(None, limits["max_fact_bytes"])
        self.slot, self.mode = slot, mode
        self.occupied_slots = ()
        self.admission = self.anchor = None


def _original(token):
    if type(token) is not object:
        _fail("OWNER_MISMATCH")
    run = _LIVE.get(token)
    if run is None or run.token is not token or lifecycle._owner() != run.owner:
        _fail("OWNER_MISMATCH")
    if run.callback_active:
        raise run.budget.remember(AuthorityRawPreservationError("REENTRANT"))
    return run


def _operation_record(token):
    run = _original(token)
    run.budget.check(callback=False)
    try:
        if run.guard is None or lifecycle.require_guard(run.profile, exclusive=True) is not run.guard:
            _fail("OWNER_MISMATCH")
    except BaseException as exc:
        raise run.budget.remember(exc)
    return run


def _failure_operation_record(token):
    run = _original(token)
    if run.readonly or run.budget.first is None or run.budget.remaining_seconds <= 0:
        raise run.budget.remember(AuthorityRawPreservationError("PUBLICATION_FAILED"))
    if run.guard is None or lifecycle.require_guard(run.profile, exclusive=True) is not run.guard:
        raise run.budget.remember(AuthorityRawPreservationError("OWNER_MISMATCH"))
    return run


def _stable_failure(run, exc):
    original = run.budget.first if run.budget.first is not None else exc
    if original is run.callback_exception:
        return original
    if isinstance(original, contract.Error):
        return AuthorityRawPreservationError("TARGET_UNSAFE")
    if isinstance(original, source.AuthorityRawCaptureError) and original.code.endswith("_SOURCE_CHANGED"):
        return AuthorityRawPreservationError("SOURCE_CHANGED")
    return first._stable_failure(run, exc)


@contextmanager
def _operation(profile, operation_id, slot, mode, callback, *, expected=None, limits=None):
    owner = lifecycle._owner()
    routing.require_no_reentry(owner)
    if (type(profile) is not lifecycle.StorageProfile
            or callback is not None and not callable(callback)
            or mode not in {"create", "resume", "verify"}):
        _fail("INVALID_ARGUMENT")
    first._uuid(operation_id)
    try:
        policy.slot_reservation(slot)
        if mode != "create":
            policy.request_anchor_name(expected)
    except policy.Error:
        _fail("INVALID_ARGUMENT")
    if mode == "create" and slot != 1 or mode == "resume" and slot != 2:
        _fail("INVALID_ARGUMENT")
    selected = first._limits_copy(limits) if mode == "verify" else policy.slot_limits(slot)
    run = _Run(profile, operation_id, slot, mode, selected, callback, expected)
    _LIVE[run.token], _OWNERS[owner] = run, run
    try:
        run.budget.check(callback=False)
        try:
            run.guard = lifecycle.require_guard(profile, exclusive=True)
        except lifecycle.StorageLifecycleError:
            _fail("OWNER_MISMATCH")
        run.budget.check(callback=False)
        yield run
    except BaseException as exc:
        failure = _stable_failure(run, exc)
        run.budget.remember(exc)
        if not run.readonly:
            from . import personal_authority_preservation_target2 as target
            try:
                target.write_failure_once(run.token, phase=run.phase, exc=failure)
            except BaseException:
                _failure_cleanup._note_original(failure, "AUTHORITY_RAW_PRESERVATION_FAILURE_RECORD_UNAVAILABLE")
        raise failure from None
    finally:
        _LIVE.pop(run.token, None)
        if _OWNERS.get(owner) is run:
            del _OWNERS[owner]


def _header(run, kind, *, per_run=True, **fields):
    value = {"format_version": 2, "kind": "authority_raw_preservation_" + kind,
             "operation_id": run.operation_id}
    if per_run:
        value.update(slot=run.slot, run_id=run.run_id)
    return {**value, **fields}


def _run_path(run):
    return f"runs/{run.slot:02d}"


def _reservation(run):
    predecessor = None
    if run.slot == 2:
        path = "runs/01/run-attempt.json"
        predecessor = {"slot": 1, "run_id": run.graph.attempt(1)["run_id"],
                       "run_attempt_sha256": run.graph.sha(path), "run_attempt_byte_size": len(run.graph.bytes(path))}
    return _header(run, "run_attempt", request_sha256=run.expected_request_sha256,
        frozen_selection_sha256=None if run.slot == 1 else run.graph.sha("selection.json"),
        reservation=policy.slot_reservation(run.slot), admission=run.admission,
        anchor=run.anchor, predecessor=predecessor)


def _capture_and_copy(run, held, *, initial):
    from . import personal_authority_preservation_target2 as target
    run.phase = "source_open"
    with source._capture_for_preservation_locked(run.token) as captured:
        metadata = source.source_metadata(captured)
        if initial:
            target.publish_control(held, "source-metadata.json", "source_metadata", metadata)
            target.publish_control(held, "source-projection.json", "source_projection", metadata["source_projection"])
            run.phase = "selection"
            target.publish_control(held, "selection.json", "selection", _header(run, "selection", per_run=False,
                request_sha256=run.expected_request_sha256,
                initial_run_attempt_sha256=run.graph.sha("runs/01/run-attempt.json"),
                source_metadata_sha256=run.graph.sha("source-metadata.json"),
                source_projection_sha256=run.graph.sha("source-projection.json"),
                source_file_ids=sorted(row["entry_id"] for row in metadata["capsule"]["files"])))
        # This original S1 seam compares the complete metadata raw, then binds
        # the unchanged global selection to this actual run/guard acquisition.
        source._bind_preservation_selection_original(captured, run.token)
        run.phase = "copy"
        for entry_id in run.graph.selection()["source_file_ids"]:
            target.copy_entry(held, captured, entry_id)
        run.phase = "source_close"
    run.source_closed = source._consume_closed_for_preservation(captured, run.token)
    _checkpoint("source_closed")
    capsule = json.loads(run.graph.bytes("source-metadata.json"))["capsule"]
    capsule["copies"] = [row["copy"] for row in run.graph.records(run.slot)]
    capsule["coverage"]["declared_copies"] = "complete" if capsule["files"] else "none"
    target.publish_control(held, _run_path(run) + "/capsule.json", "capsule", capsule)
    run.phase = "target_close"


def _publish_completion(run):
    from . import personal_authority_preservation_target2 as target
    base = _run_path(run)
    run.phase = "publication"
    with target.existing_target_locked(run.token, mode="publisher") as publisher:
        verified = base + "/source-verified.json"
        target.publish_control(publisher, verified, "source_verified", _header(run, "source_verified",
            request_sha256=run.expected_request_sha256, selection_sha256=run.graph.sha("selection.json"),
            run_attempt_sha256=run.graph.sha(base + "/run-attempt.json"),
            capsule_sha256=run.graph.sha(base + "/capsule.json"), source_closed=run.source_closed,
            target_closed=run.target_closed, selected_copies=run.graph.expected_selected(run.slot)))
        physical = {row["relative_key"]: row for row in run.target_closed["files"]}
        copies = [row["copy"] for row in run.graph.records(run.slot)]
        payloads = {row["target_relative_key"] for row in copies}
        facts = {key for key in physical if run.graph._file_class(key) == "fact"}
        markers = {key for key in physical if run.graph._file_class(key) == "marker"}
        retained = set(physical) - facts - markers - payloads
        def rows(keys):
            return [{"relative_key": key,
                     "sha256": run.graph.sha(key) if key == verified else physical[key]["sha256"],
                     "byte_size": len(run.graph.bytes(key)) if key == verified else physical[key]["byte_size"]}
                    for key in sorted(keys, key=lambda value: value.encode("utf-8"))]
        outcome = "copied" if copies else "empty_source"
        target.publish_control(publisher, base + "/manifest.json", "manifest", _header(run, "manifest",
            scope=contract.SCOPE, request_sha256=run.expected_request_sha256,
            selection_sha256=run.graph.sha("selection.json"), source_verified_sha256=run.graph.sha(verified),
            outcome=outcome, facts=rows(facts | {verified}), markers=rows(markers), retained=rows(retained),
            payloads=[{"source_entry_id": row["source_entry_id"], "relative_key": row["target_relative_key"],
                       "sha256": row["sha256"], "byte_size": row["byte_size"]} for row in copies]))
        run.budget.mark_receipt_started()
        target.publish_control(publisher, base + "/receipt.json", "receipt", _header(run, "receipt",
            scope=contract.SCOPE, request_sha256=run.expected_request_sha256,
            source_verified_sha256=run.graph.sha(verified), manifest_sha256=run.graph.sha(base + "/manifest.json"),
            outcome=outcome, publication="published_observed"))
        contract.cross_validate_package(fact_bytes=run.graph.raw, generation=run.slot,
            inventory=target.inventory_declaration(publisher), max_fact_bytes=run.limits["max_fact_bytes"])
        _checkpoint("receipt_published")
        run.phase = "publisher_close"
    target.consume_closed(publisher, run.token)
    run.budget.assert_settled()
    _checkpoint("publisher_closed")


def _result(run, kind):
    _operation_record(run.token)
    run.budget.assert_settled()
    base = _run_path(run)
    receipt = run.graph.load(base + "/receipt.json")
    result = RawPreservation2Result(_RESULT_TOKEN, kind=kind, operation_id=run.operation_id,
        generation=run.slot, run_id=run.graph.attempt(run.slot)["run_id"],
        package_path=run.profile.database.parent / contract.DIRECTORY / run.operation_id,
        request_sha256=run.graph.sha("request.json"), manifest_sha256=run.graph.sha(base + "/manifest.json"),
        receipt_sha256=run.graph.sha(base + "/receipt.json"), capsule_sha256=run.graph.sha(base + "/capsule.json"),
        source_projection_sha256=run.graph.sha("source-projection.json"), outcome=receipt["outcome"],
        counters=run.budget.snapshot())
    owner = (run.owner[0], run.owner[1], None if run.owner[2] is None else ref(run.owner[2]))
    _COMPLETED[result] = owner, run.profile.identity, run.operation_id, run.slot, kind
    return result


def create_raw_preservation_locked(profile, *, operation_id, check_cancelled=None):
    from . import personal_authority_preservation_target2 as target
    with _operation(profile, operation_id, 1, "create", check_cancelled) as run:
        with target.new_target_locked(run.token) as held:
            run.phase = "request"
            target.publish_control(held, "request.json", "request", _header(run, "request", per_run=False,
                scope=contract.SCOPE, layout_profile=contract.c2.LAYOUT_PROFILE,
                layout_source_sha256=contract.c2.LAYOUT_SOURCE_SHA256, creation_run_id=run.run_id,
                profile=target.profile_declaration(held), target=target.target_declaration(held),
                budget_policy=policy.q1_declaration(), slot_limits=policy.slot_limits(1)))
            run.expected_request_sha256 = run.graph.sha("request.json")
            target.establish_request_anchor(held)
            run.phase = "run_attempt"
            target.publish_control(held, "runs/01/run-attempt.json", "run_attempt", _reservation(run))
            _capture_and_copy(run, held, initial=True)
        run.target_closed = target.consume_closed(held, run.token)
        run.budget.assert_settled()
        _checkpoint("target_closed")
        _publish_completion(run)
        return _result(run, "raw_preservation_created")


def resume_raw_preservation_locked(profile, *, operation_id, expected_request_sha256, check_cancelled=None):
    from . import personal_authority_preservation_target2 as target
    with _operation(profile, operation_id, 2, "resume", check_cancelled, expected=expected_request_sha256) as run:
        with target.resume_target_locked(run.token) as held:
            run.phase = "run_attempt"
            target.publish_control(held, "runs/02/run-attempt.json", "run_attempt", _reservation(run))
            run.phase = "prefix"
            target.load_prefix(held)
            _capture_and_copy(run, held, initial=False)
        run.target_closed = target.consume_closed(held, run.token)
        run.budget.assert_settled()
        _checkpoint("target_closed")
        _publish_completion(run)
        return _result(run, "raw_preservation_resumed")


def verify_raw_preservation_locked(profile, *, operation_id, expected_request_sha256,
                                   generation, limits=None, check_cancelled=None):
    from . import personal_authority_preservation_target2 as target
    with _operation(profile, operation_id, generation, "verify", check_cancelled,
                    expected=expected_request_sha256, limits=limits) as run:
        with target.existing_target_locked(run.token, mode="verify") as held:
            contract.cross_validate_package(fact_bytes=run.graph.raw, generation=run.slot,
                inventory=target.inventory_declaration(held), max_fact_bytes=run.limits["max_fact_bytes"])
            run.phase = "publisher_close"
        target.consume_closed(held, run.token)
        return _result(run, "raw_preservation_verified")
