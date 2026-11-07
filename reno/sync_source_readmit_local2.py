"""Permanent explicit2 readmit/timer/direct receipts in the original facility.

Closed representation and immutable fact capture are independent of current
execution admission. Ordinary reads never create an intent, authorize a timer,
renew a claim, change State, or initialize a second permanent facility.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as identity_pins, storage_lifecycle as lifecycle
from . import sync_access, sync_source_authority as permanent, sync_source_claim as engine
from . import sync_source_readmit_contract as wire, sync_source_transport17 as transport
from . import sync_state
from .sync_protocol import SyncError

_SEAL = object()
_CURRENT = ContextVar("reno_permanent_source_receipt2_capture", default=None)
_RETIRED_CURRENT = ContextVar("reno_permanent_retired_controls2_capture", default=None)
_HISTORY_CURRENT = ContextVar("reno_permanent_admission2_history_capture", default=None)
_CONTROL_BASIS_CURRENT = ContextVar("reno_historical_control2_basis", default=None)
_CONTROL_INTENT_CURRENT = ContextVar("reno_original_control2_intent", default=None)
_SETTLEMENT_CURRENT = ContextVar("reno_current_control2_settlement", default=None)
_CONTROL_BASIS_CONTEXT = ContextVar("reno_historical_control2_basis_context", default=None)
_CONTROL_INTENT_CONTEXT = ContextVar("reno_original_control2_intent_context", default=None)
_SETTLEMENT_CONTEXT = ContextVar("reno_current_control2_settlement_context", default=None)
_PUBLISHER = ContextVar("reno_permanent_source_receipt2_publisher", default=None)
_BASE = {"format_version", "record_kind", "record_id", "core_sha256", "request_sha256"}
REQUEST_FIELDS = _BASE | {"request", "expected_before", "creation_basis", "preservation_provenance", "resume_provenance"}
RECEIPT_FIELDS = _BASE | {"request_ref", "result", "result_sha256", "parent_defer", "transport_provenance"}
CONTROL_FIELDS2 = _BASE | {"request", "creation_basis", "control_scope", "expected_before",
    "preservation_provenance", "resume_provenance", "parent_defer_refs", "retirement_head"}
CONTROL_SCOPE_FIELDS2 = {"format_version", "core_sha256", "home_epoch", "session", "claim",
    "recovery_generation", "recovery_stream", "control_revision", "continuity"}
TIMER_FIELDS = {"format_version", "kind", "timer_operation_id", "origin_ref", "core_sha256",
    "defer_request", "defer_receipt", "defer_request_sha256", "defer_receipt_sha256", "retry_at", "created_at_ms"}
TIMER_RESULT_FIELDS = {"format_version", "kind", "timer_operation_id", "request_sha256", "retry_at",
    "defer_request_sha256", "defer_receipt_sha256", "observed_at_ms", "replayed"}


def _fail(code="INVALID"):
    raise SyncError("Permanent source receipt2 could not be verified", "SYNC_SOURCE_LOCAL2_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("FACT_CHANGED")


def _capture_context2(value, context):
    if context.get() is not value:
        _fail("FACTORY_REQUIRED")
    try:
        context.reset(value._context_token)
    except (ValueError, RuntimeError):
        _fail("FACTORY_REQUIRED")
    value._context_token = context.set(value)


def _provenance(value, producer):
    permanent._exact(value, ("format_version", "producer", "operation_id", "receipt_sha256"))
    permanent._version(value["format_version"])
    if value["producer"] != producer:
        _fail()
    permanent._uuid(value["operation_id"])
    permanent._sha(value["receipt_sha256"])


def control_scope2(value, core_sha, binding):
    """A current control scope, distinct from an immutable admission basis."""
    from . import sync_queue as queue
    permanent._exact(value, CONTROL_SCOPE_FIELDS2)
    permanent._version(value["format_version"])
    _same(value["core_sha256"], core_sha)
    permanent._integer(value["home_epoch"], minimum=1)
    queue._lease(value["session"])
    sync_state._claim(value["claim"])
    permanent._integer(value["recovery_generation"])
    permanent._integer(value["control_revision"])
    wire._stream(value["recovery_stream"], {"library_id": binding["target_library_id"]})
    permanent._continuity(value["continuity"])
    if value["continuity"] is None:
        _fail("CONTINUITY_REQUIRED")
    _same(value["continuity"]["target_epoch"], value["recovery_stream"]["library_epoch"])
    return deepcopy(value)


def logical_control_scope2(record):
    """Expiry renewal alone never provides a new logical control identity."""
    scope = record["control_scope"] if record["format_version"] == 2 else record["creation_basis"]
    value = {key: deepcopy(scope[key]) for key in CONTROL_SCOPE_FIELDS2}
    value["session"].pop("lease_until")
    return value


def _validate_control2(value, activation):
    permanent._exact(value, CONTROL_FIELDS2)
    if type(value["format_version"]) is not int or value["format_version"] != 2 or value["record_kind"] != "control":
        _fail()
    binding = activation["binding"]
    for key in ("core_sha256", "request_sha256"):
        permanent._sha(value[key])
    permanent._uuid(value["record_id"])
    body = wire._old_request(value["request"])
    permanent._peer(body, binding)
    if body["source"]["handshake"]["migration_version"] != 17:
        _fail("SCHEMA_REQUIRED")
    _same((body["request_id"], permanent.digest(body)), (value["record_id"], value["request_sha256"]))
    basis = value["creation_basis"]
    permanent._basis(basis, value["core_sha256"], binding)
    if basis["origin_ref"] is None or basis["continuity"] is None:
        _fail("ORIGIN_REQUIRED")
    scope = control_scope2(value["control_scope"], value["core_sha256"], binding)
    if basis["kind"] == "admission" and scope["home_epoch"] < basis["home_epoch"]:
        _fail("HISTORY_EPOCH_MISMATCH")
    _same(scope["claim"], basis["claim"])
    _same((body["lease"], body["recovery_generation"], body["recovery_stream"]),
          (scope["session"], scope["recovery_generation"], scope["recovery_stream"]))
    if "claim" in body:
        _same(body["claim"], scope["claim"])
    if "core_sha256" in body:
        _same(body["core_sha256"], value["core_sha256"])
    before = value["expected_before"]
    permanent._before(before, binding, value["core_sha256"])
    _same((before["admission_ref"], before["local_epoch"], before["claim"]),
          (basis["admission_ref"], scope["home_epoch"], scope["claim"]))
    for key in ("recovery_generation", "recovery_stream", "control_revision"):
        _same(before[key], scope[key])
    if before["source_authority"] is None:
        _fail("ORIGIN_REQUIRED")
    _same(before["source_authority"]["origin"], basis["origin_ref"])
    if value["preservation_provenance"] is not None:
        _provenance(value["preservation_provenance"], "home_knowledge_restore")
    head = value["retirement_head"]
    if head is not None:
        permanent._ref(head, kind="recovery_receipt")
    if body["kind"] == "budget_resume":
        refs = value["parent_defer_refs"]
        permanent._exact(refs, ("control_ref", "receipt_ref"))
        permanent._ref(refs["control_ref"], kind="control")
        permanent._ref(refs["receipt_ref"], kind="recovery_receipt")
        _same(refs["control_ref"]["record_id"], body["defer_request_id"])
        _same(before["source_authority"]["defer"], refs["control_ref"])
        provenance = value["resume_provenance"]
        if type(provenance) is not dict or provenance.get("producer") not in {"sync_budget_resume", "sync_budget_timer"}:
            _fail("RESUME_REQUIRED")
        _provenance(provenance, provenance["producer"])
        if provenance["producer"] == "sync_budget_timer":
            _same(provenance["operation_id"], wire.timer_operation_id(body["defer_request_id"]))
    elif value["parent_defer_refs"] is not None or value["resume_provenance"] is not None:
        _fail("PARENT_MISMATCH")
    permanent._encoded(value)
    return deepcopy(value)


def _timer(body):
    from . import sync_client, sync_queue as queue
    permanent._exact(body, TIMER_FIELDS)
    if type(body["format_version"]) is not int or body["format_version"] != 2 or body["kind"] != "budget_timer":
        _fail()
    permanent._sha(body["core_sha256"])
    permanent._ref(body["origin_ref"], kind="origin")
    request = sync_client.normalize_budget_request(body["defer_request"])
    result = sync_client.normalize_budget_receipt(request, body["defer_receipt"])
    if (request["kind"] != "budget_defer" or result["replayed"] is not False
            or result["retry_at"] is None or body["retry_at"] != result["retry_at"]
            or body["timer_operation_id"] != wire.timer_operation_id(request["request_id"])
            or permanent.digest(request) != body["defer_request_sha256"]
            or permanent.digest(result) != body["defer_receipt_sha256"]):
        _fail("PARENT_MISMATCH")
    permanent._integer(body["created_at_ms"])
    if queue._stored_time(body["retry_at"]) is None:
        _fail()
    return deepcopy(body)


def public_result_shape2(value, activation):
    """Closed standalone result representation; intent authority is separate."""
    from . import sync_client, sync_source_control_contract as control, source_terminal, sync_queue as queue, tasks
    binding = permanent.validate_contract(activation)["binding"]
    if type(value) is not dict or value.get("replayed") is not False:
        _fail()
    if value.get("kind") == "budget_timer":
        permanent._exact(value, TIMER_RESULT_FIELDS)
        if type(value["format_version"]) is not int or value["format_version"] != 2:
            _fail()
        permanent._uuid(value["timer_operation_id"])
        for key in ("request_sha256", "defer_request_sha256", "defer_receipt_sha256"):
            permanent._sha(value[key])
        permanent._integer(value["observed_at_ms"])
        retry = queue._stored_time(value["retry_at"])
        if retry is None or value["observed_at_ms"] < tasks._deadline_ms(retry):
            _fail("TIMER_NOT_DUE")
    elif "admission_id" in value:
        permanent._exact(value, wire.READMIT_RESULT_FIELDS)
        if type(value["format_version"]) is not int or value["format_version"] not in {1, 2}:
            _fail()
        if value["format_version"] == 1:
            permanent._public_result_shape(value, activation)
            return
        permanent._uuid(value["admission_id"])
        if value["previous_admission_id"] is not None:
            permanent._uuid(value["previous_admission_id"])
        for key in ("request_sha256", "admission_sha256", "core_sha256", "submitted_url_sha256"):
            permanent._sha(value[key])
        for name, prefix in (("source_identity", "home_"), ("target_identity", "target_")):
            wire._identity(value[name])
            _same(value[name], {key: binding[prefix + key] for key in ("library_id", "node_id")})
        queue._lease(value["session"])
        sync_state._claim(value["claim"])
        for key in ("item_id", "current_home_epoch"):
            permanent._integer(value[key], minimum=1)
        for key in ("recovery_generation", "control_revision"):
            permanent._integer(value[key])
        wire._stream(value["recovery_stream"], value["target_identity"])
        if value["preservation"] is not None:
            p = value["preservation"]
            permanent._exact(p, wire._PRESERVATION)
            permanent._version(p["format_version"])
            permanent._sha(p["frontier_sha256"])
            permanent._sha(p["certificate_sha256"])
            permanent._integer(p["from_epoch"], minimum=1)
            permanent._integer(p["to_epoch"], minimum=p["from_epoch"])
            _same(p["to_epoch"], value["current_home_epoch"])
        permanent._integer(value["attempts"], minimum=1, maximum=queue.MAX_ATTEMPTS)
        if value["attempt_basis"] not in {"held_claim", "original_defer_refund"}:
            _fail()
        _same(value["claim"]["item_id"], value["item_id"])
        permanent._time(value["committed_at"])
        if type(value["disposition_sha256s"]) is not list or len(value["disposition_sha256s"]) > 2:
            _fail()
        for sha in value["disposition_sha256s"]:
            permanent._sha(sha)
        _same(wire.admission_sha256(value), value["admission_sha256"])
    elif "original_kind" in value:
        if "disposition" in value:
            wire._dispose_shape_versioned(value)
        else:
            wire._registration_shape(value)
    elif "failure_sha256" in value:
        permanent._exact(value, control.RECEIPT_FIELDS)
        permanent._version(value["format_version"])
        permanent._uuid(value["request_id"])
        for key in ("request_sha256", "core_sha256", "submitted_url_sha256", "failure_sha256"):
            permanent._sha(value[key])
        for key in ("item_id", "lease_generation"):
            permanent._integer(value[key], minimum=1)
        permanent._integer(value["attempts"], minimum=1, maximum=queue.MAX_ATTEMPTS)
        permanent._time(value["committed_at"])
        if value["status"] != "dead" or value["error_class"] not in source_terminal._ERRORS:
            _fail()
    else:
        sync_client._budget_receipt_shape(value)


def validate_record2(kind, value, activation):
    """Pure format2 only; factory producers are checked independently."""
    activation = permanent.validate_contract(activation)
    if kind == "publications":
        from . import sync_source_publication_local2 as publications
        return publications.validate_record2(kind, value, activation)
    if kind == "controls":
        return _validate_control2(value, activation)
    if kind not in {"requests", "receipts"}:
        _fail()
    permanent._exact(value, REQUEST_FIELDS if kind == "requests" else RECEIPT_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _fail()
    if value["record_kind"] != ("recovery_intent" if kind == "requests" else "recovery_receipt"):
        _fail()
    permanent._uuid(value["record_id"])
    permanent._sha(value["core_sha256"])
    permanent._sha(value["request_sha256"])
    binding = activation["binding"]
    if kind == "requests":
        body = value["request"]
        if body.get("kind") == "budget_timer":
            body = _timer(body)
            key = body["timer_operation_id"]
            if value["preservation_provenance"] is not None or value["resume_provenance"] is not None:
                _fail()
        else:
            body = wire.normalize_request(body)
            if body.get("format_version") != 2 or body["kind"] not in {"source_readmit", "source_dispose"}:
                _fail()
            permanent._peer(body, binding)
            key = body["request_id"]
            if body["original"]["core_sha256"] != value["core_sha256"]:
                _fail()
            provenance = value["preservation_provenance"]
            if (body["preservation"] is None) != (provenance is None):
                _fail("RESTORE_REQUIRED")
            if provenance is not None:
                _provenance(provenance, "home_knowledge_restore")
            resume = value["resume_provenance"]
            if (body.get("resume_basis") is None) != (resume is None):
                _fail("RESUME_REQUIRED")
            if resume is not None:
                _provenance(resume, "sync_budget_resume" if body["resume_basis"]["kind"] == "manual" else "sync_budget_timer")
        if key != value["record_id"] or permanent.digest(body) != value["request_sha256"]:
            _fail()
        permanent._before(value["expected_before"], binding, value["core_sha256"])
        permanent._basis(value["creation_basis"], value["core_sha256"], binding)
        if value["creation_basis"]["continuity"] is None:
            _fail("CONTINUITY_REQUIRED")
        if body["kind"] == "source_readmit":
            basis = value["creation_basis"]
            before = value["expected_before"]
            _same((basis["session"], basis["claim"], basis["home_epoch"]),
                (body["lease"], body["expected_claim"], body["current_home_epoch"]))
            for key in ("recovery_generation", "recovery_stream", "control_revision"):
                _same(basis[key], body[key])
            _same(None if basis["admission_ref"] is None else basis["admission_ref"]["admission_id"], body["previous_admission_id"])
            _same((before["admission_ref"], before["claim"], before["local_epoch"]),
                (basis["admission_ref"], basis["claim"], basis["home_epoch"]))
            for key in ("recovery_generation", "recovery_stream", "control_revision"):
                _same(before[key], basis[key])
        elif body["kind"] == "source_dispose":
            basis, before = value["creation_basis"], value["expected_before"]
            _same((basis["session"], basis["home_epoch"], basis["continuity"]),
                (body["lease"], body["current_home_epoch"], body["continuity"]))
            for key in ("recovery_generation", "recovery_stream", "control_revision"):
                _same(basis[key], body[key])
            _same((before["admission_ref"], before["claim"], before["local_epoch"]),
                (basis["admission_ref"], basis["claim"], basis["home_epoch"]))
            for key in ("recovery_generation", "recovery_stream", "control_revision"):
                _same(before[key], basis[key])
            _same(body["original"]["claim"]["item_id"], basis["claim"]["item_id"])
    else:
        permanent._ref(value["request_ref"])
        if value["request_ref"]["record_kind"] not in {"control", "recovery_intent"} or value["request_ref"]["record_id"] != value["record_id"]:
            _fail()
        permanent._sha(value["result_sha256"])
        result = value["result"]
        if type(result) is not dict or result.get("replayed") is not False or permanent.digest(result) != value["result_sha256"]:
            _fail()
        provenance = value["transport_provenance"]
        public_result_shape2(result, activation)
        _same(result.get("admission_id", result.get("timer_operation_id", result.get("request_id"))), value["record_id"])
        _same(result["request_sha256"], value["request_sha256"])
        if "core_sha256" in result:
            _same(result["core_sha256"], value["core_sha256"])
        if result.get("kind") == "budget_timer":
            permanent._exact(result, TIMER_RESULT_FIELDS)
            if result["format_version"] != 2 or type(result["format_version"]) is not int:
                _fail()
            if result["timer_operation_id"] != value["record_id"] or result["request_sha256"] != value["request_sha256"]:
                _fail()
            permanent._integer(result["observed_at_ms"])
            _provenance(provenance, "sync_budget_timer")
        else:
            permanent._exact(provenance, ("format_version", "producer", "endpoint", "target_url_sha256", "request_sha256"))
            permanent._version(provenance["format_version"])
            permanent._sha(provenance["target_url_sha256"])
            if provenance["producer"] != "sync_http" or provenance["request_sha256"] != value["request_sha256"]:
                _fail()
            expected_endpoint = ("source/readmit" if "admission_id" in result else "source/dispose"
                if "disposition" in result else "source/register" if "original_kind" in result else "push")
            _same(provenance["endpoint"], expected_endpoint)
            if provenance["target_url_sha256"] != hashlib.sha256(binding["target_url"].encode("utf-8")).hexdigest():
                _fail("PEER_MISMATCH")
        if value["parent_defer"] is not None:
            permanent._exact(value["parent_defer"], ("request", "receipt"))
    permanent._encoded(value)
    return deepcopy(value)


def _resolve(entries, ref):
    return permanent._resolve_record(entries, ref)


def _control_group2(record):
    body, basis = record["request"], record["creation_basis"]
    logical = (body["defer_request_id"] if body["kind"] == "budget_resume" else
        body["budget_operation_id"] if body["kind"] == "budget_defer" else body["failure"]["attempt_id"])
    return permanent.digest({"core": record["core_sha256"], "kind": body["kind"], "logical": logical,
        "origin": basis["origin_ref"], "admission": basis["admission_ref"], "claim": basis["claim"]})


def _control_outcomes2(entries, control, *, check=None):
    """Every real positive/negative, including disposed original wire facts."""
    check = check or (lambda: None)
    positive, negative = [], []
    for item in entries["receipts"].values():
        check()
        if item["state"] != "committed":
            continue
        receipt, result = item["record"], item["record"]["result"]
        direct = (receipt["format_version"] == 2 and receipt["request_ref"] == permanent.record_ref(control))
        disposed = result.get("original_request_id") == control["record_id"] and "disposition" in result
        if not direct and not disposed:
            continue
        if receipt["format_version"] != 2:
            _fail("CONTROL_RECONCILIATION_REQUIRED")
        intent = _resolve(entries, receipt["request_ref"])
        _same(receipt["core_sha256"], control["core_sha256"])
        _bound_result(intent, receipt)
        if direct:
            _same(intent, control)
            positive.append((receipt, deepcopy(result), None))
            continue
        body = intent["request"]
        _same((body["kind"], body["original_request"], result["original_request_sha256"], result["original_kind"]),
              ("source_dispose", control["request"], control["request_sha256"], control["request"]["kind"]))
        registration = body["registration"]
        if registration is None:
            _fail("REGISTRATION_REQUIRED")
        registered = entries["receipts"].get(registration["request_id"])
        if registered is None or registered["state"] != "committed" or registered["record"]["format_version"] != 2:
            _fail("REGISTRATION_REQUIRED")
        _same(registered["record"]["result"], registration)
        registered_intent = _resolve(entries, registered["record"]["request_ref"])
        _same((registered_intent["request"]["kind"], registered_intent["request"]["original_request"]),
              ("source_register", control["request"]))
        _bound_result(registered_intent, registered["record"])
        if result["disposition"] == "not_applied":
            if result["original_receipt"] is not None:
                _fail("OUTCOME_MISMATCH")
            negative.append(receipt)
        else:
            if result["original_receipt"] is None:
                _fail("POSITIVE_RECEIPT_REQUIRED")
            positive.append((receipt, deepcopy(result["original_receipt"]), deepcopy(result)))
    if len(negative) > 1 or negative and positive:
        _fail("OUTCOME_MISMATCH")
    if positive:
        for _, fact, _ in positive:
            _same(fact, positive[0][1])
    return positive, negative


def _positive_parent_refs2(entries, refs, core_sha):
    control = _resolve(entries, refs["control_ref"])
    receipt = _resolve(entries, refs["receipt_ref"])
    _same((control["core_sha256"], control["request"]["kind"]), (core_sha, "budget_defer"))
    positive, negative = _control_outcomes2(entries, control)
    if negative:
        _fail("PARENT_MISMATCH")
    selected = next((fact for row, fact, _ in positive if permanent.record_ref(row) == refs["receipt_ref"]), None)
    if selected is None:
        _fail("PARENT_RECEIPT_REQUIRED")
    _same(receipt["core_sha256"], core_sha)
    return {"request": deepcopy(control["request"]), "receipt": deepcopy(selected)}


def control_retirement_graph2(entries, control_request_id, *, check=None, allow_reserved=False):
    """Entire same-paid-identity family; a detached graph is never a permit.

    A child cites one real negative predecessor. No bounded historical list,
    timestamp selection or truncated traversal substitutes for a closed chain.
    """
    check = check or (lambda: None)
    permanent._uuid(control_request_id)
    if type(allow_reserved) is not bool:
        _fail()
    if sum(len(values) for values in entries.values()) > permanent.MAX_RECORDS:
        _fail("COUNT_LIMIT")
    selected = entries["controls"].get(control_request_id)
    if selected is None:
        _fail("ORIGINAL_CONTROL_REQUIRED")
    group = _control_group2(selected["record"])
    nodes = {}
    for identifier, item in entries["controls"].items():
        check()
        if _control_group2(item["record"]) == group:
            nodes[identifier] = item
    predecessors, children, outcomes = {}, {}, {}
    for identifier, item in nodes.items():
        check()
        if item["state"] != "committed" and not (allow_reserved and identifier == control_request_id and item["state"] == "reserved"):
            _fail("CONTROL_PENDING")
        record = item["record"]
        outcomes[identifier] = _control_outcomes2(entries, record, check=check)
        head = record.get("retirement_head")
        if head is None:
            continue
        receipt = _resolve(entries, head)
        result = receipt["result"]
        if result.get("disposition") != "not_applied":
            _fail("RETIREMENT_NEGATIVE_REQUIRED")
        previous = result["original_request_id"]
        if previous not in nodes or nodes[previous]["state"] != "committed":
            _fail("RETIREMENT_PARENT_REQUIRED")
        positives, negatives = _control_outcomes2(entries, nodes[previous]["record"], check=check)
        if positives or len(negatives) != 1 or permanent.record_ref(negatives[0]) != head:
            _fail("RETIREMENT_NEGATIVE_REQUIRED")
        if previous in children:
            _fail("CONTROL_FORK")
        predecessors[identifier], children[previous] = previous, identifier
        if record["request"]["kind"] == "budget_resume" and nodes[previous]["record"]["format_version"] == 2:
            _same(record["parent_defer_refs"], nodes[previous]["record"]["parent_defer_refs"])
        if record["request"]["kind"] == "source_fail":
            _same(record["request"]["failure"], nodes[previous]["record"]["request"]["failure"])
    roots = set(nodes) - set(predecessors)
    leaves = set(nodes) - set(children)
    if not roots:
        _fail("CONTROL_CYCLE")
    if len(roots) != 1 or len(leaves) != 1:
        _fail("CONTROL_FORK")
    leaf, path, seen, scopes = next(iter(leaves)), [], set(), set()
    current = leaf
    while current is not None:
        check()
        if current in seen:
            _fail("CONTROL_CYCLE")
        seen.add(current)
        record = nodes[current]["record"]
        scope_sha = permanent.digest(logical_control_scope2(record))
        if scope_sha in scopes:
            _fail("RESUME_FORBIDDEN_SCOPE")
        scopes.add(scope_sha)
        path.append(current)
        current = predecessors.get(current)
    if seen != set(nodes):
        _fail("CONTROL_FORK")
    if any(item["state"] != "committed" for key, item in nodes.items() if key != leaf):
        _fail("CONTROL_PENDING")
    leaf_record = nodes[leaf]["record"]
    positive, negative = outcomes[leaf]
    ancestors = list(reversed(path[1:]))
    retired = [permanent.record_ref(outcomes[key][1][0]) for key in ancestors]
    return {"group": group, "root_ref": permanent.record_ref(nodes[next(iter(roots))]["record"]),
        "leaf_ref": permanent.record_ref(leaf_record), "record_state": nodes[leaf]["state"],
        "closed_head": None if not negative else permanent.record_ref(negative[0]),
        "ancestor_refs": [permanent.record_ref(nodes[key]["record"]) for key in ancestors],
        "retired_disposition_refs": retired, "parent_defer_refs": deepcopy(leaf_record.get("parent_defer_refs"))}


def _control_relations2(entries, *, check):
    checked_groups = set()
    for item in entries["controls"].values():
        check()
        record = item["record"]
        if record["format_version"] != 2:
            continue
        basis, scope = record["creation_basis"], record["control_scope"]
        origin = _resolve(entries, basis["origin_ref"])
        _same(origin["core_sha256"], record["core_sha256"])
        if (scope["home_epoch"] != origin["core"]["local_epoch"]) != (record["preservation_provenance"] is not None):
            _fail("RESTORE_REQUIRED")
        before = record["expected_before"]
        for name in ("origin", "defer", "resume", "failure", "publication"):
            ref = before["source_authority"][name]
            if ref is not None:
                _same(_resolve(entries, ref)["core_sha256"], record["core_sha256"])
        if record["request"]["kind"] == "budget_resume":
            parent = _positive_parent_refs2(entries, record["parent_defer_refs"], record["core_sha256"])
            _same(parent["request"]["claim"], basis["claim"])
            provenance = record["resume_provenance"]
            if provenance["producer"] == "sync_budget_timer":
                timer = entries["receipts"].get(provenance["operation_id"])
                if timer is None or timer["state"] != "committed":
                    _fail("RESUME_REQUIRED")
                _same(permanent.digest(timer["record"]), provenance["receipt_sha256"])
                intent = _resolve(entries, timer["record"]["request_ref"])
                _same((intent["request"]["kind"], intent["request"]["core_sha256"],
                       intent["request"]["defer_request"], intent["request"]["defer_receipt"]),
                      ("budget_timer", record["core_sha256"], parent["request"], parent["receipt"]))
        group = _control_group2(record)
        if group not in checked_groups:
            # A structurally valid reserved leaf remains unknown to every
            # ordinary producer; only its own recovery factory can complete it.
            reserved = [key for key, value in entries["controls"].items()
                        if _control_group2(value["record"]) == group and value["state"] == "reserved"]
            if len(reserved) > 1:
                _fail("CONTROL_PENDING")
            control_retirement_graph2(entries, reserved[0] if reserved else record["record_id"],
                check=check, allow_reserved=bool(reserved))
            checked_groups.add(group)


def _bound_result(intent, receipt):
    body, result = intent["request"], receipt["result"]
    parent = receipt["parent_defer"]
    if body["kind"] == "budget_timer":
        from . import sync_queue as queue, tasks
        _timer(body)
        expected = {"format_version": 2, "kind": "budget_timer", "timer_operation_id": body["timer_operation_id"],
            "request_sha256": permanent.digest(body), "retry_at": body["retry_at"],
            "defer_request_sha256": body["defer_request_sha256"], "defer_receipt_sha256": body["defer_receipt_sha256"],
            "observed_at_ms": result["observed_at_ms"], "replayed": False}
        _same(result, expected)
        if (result["observed_at_ms"] < tasks._deadline_ms(queue._stored_time(body["retry_at"]))
                or result["observed_at_ms"] < body["created_at_ms"] or parent is not None):
            _fail("TIMER_NOT_DUE")
    else:
        normalized = transport._normalize_result(body, result, None if parent is None else parent["receipt"])
        _same(normalized, result)
        if receipt["format_version"] == 2:
            provenance = receipt["transport_provenance"]
            if provenance["endpoint"] != transport._ENDPOINTS[body["kind"]]:
                _fail()
        resumed = body["kind"] == "budget_resume" or (body["kind"] == "source_dispose"
            and body["original_request"]["kind"] == "budget_resume" and result["disposition"] == "applied")
        if resumed:
            if parent is None:
                _fail("PARENT_RECEIPT_REQUIRED")
            child = body if body["kind"] == "budget_resume" else body["original_request"]
            _same(parent["request"]["request_id"], child["defer_request_id"])
        elif parent is not None:
            _fail()


def record_relations2(entries, *, check=None):
    """Full mixed ledger closure, including direct receipt and timer branches."""
    check = check or (lambda: None)
    if any(item["record"]["format_version"] == 2 for item in entries["publications"].values()):
        from . import sync_source_publication_local2 as publications
        publications.record_relations2(entries, check=check)
    admissions, outcomes = {}, {}
    originals = {}
    for folder in ("controls", "requests"):
        for item in entries[folder].values():
            check()
            request = item["record"]["request"]
            old = request if folder == "controls" else request.get("original_request")
            if old is not None:
                key, sha = old["request_id"], wire.digest(old)
                if key in originals and originals[key] != sha:
                    _fail("REQUEST_MISMATCH")
                originals[key] = sha
    for folder in ("controls", "publications", "requests"):
        for item in entries[folder].values():
            check()
            record = item["record"]
            basis = record.get("creation_basis")
            if basis is None:
                continue
            if basis["origin_ref"] is None:
                if record["format_version"] == 2:
                    _fail("ORIGIN_REQUIRED")
            else:
                origin = _resolve(entries, basis["origin_ref"])
                _same(origin["core_sha256"], record["core_sha256"])
            if basis["admission_ref"] is not None:
                ref = basis["admission_ref"]
                old = entries["receipts"].get(ref["admission_id"])
                if old is None or old["state"] != "committed" or permanent.digest(old["record"]) != ref["record_sha256"]:
                    _fail("ADMISSION_PARENT_REQUIRED")
                result = old["record"]["result"]
                for key in ("admission_id", "core_sha256", "request_sha256", "admission_sha256"):
                    _same(result[key], ref[key])
                if folder == "requests" and record["format_version"] == 2 and record["request"]["kind"] in {"source_readmit", "source_dispose"}:
                    _same(result["claim"], basis["claim"])
                    if record["request"]["kind"] == "source_readmit":
                        _same(result["claim"], record["request"]["expected_claim"])
                        _same(result["admission_id"], record["request"]["previous_admission_id"])
                else:
                    for key in ("session", "claim", "recovery_generation", "recovery_stream", "control_revision"):
                        _same(result[key], basis[key])
                    _same(result["current_home_epoch"], basis["home_epoch"])
    for item in entries["requests"].values():
        check()
        value = item["record"]
        if value["format_version"] == 2 and value["request"]["kind"] == "source_dispose":
            body = value["request"]
            old = entries["controls"].get(body["original_request"]["request_id"])
            if old is None or old["state"] != "committed":
                _fail("ORIGINAL_CONTROL_REQUIRED")
            _same(old["record"]["core_sha256"], value["core_sha256"])
            _same(old["record"]["request"], body["original_request"])
            registration = body["registration"]
            if registration is not None:
                actual = entries["receipts"].get(registration["request_id"])
                if actual is None or actual["state"] != "committed":
                    _fail("REGISTRATION_REQUIRED")
                _same(actual["record"]["result"], registration)
        if value["format_version"] == 2 and value["request"]["kind"] == "budget_timer":
            body = value["request"]
            _same(_resolve(entries, body["origin_ref"])["core_sha256"], value["core_sha256"])
            parent = _actual_parent(entries, body["defer_request"]["request_id"], value["core_sha256"])
            _same(parent, {"request": body["defer_request"], "receipt": body["defer_receipt"]})
        before = value["expected_before"]
        if before["source_authority"] is not None:
            for name in ("origin", "defer", "resume", "failure", "publication"):
                ref = before["source_authority"][name]
                if ref is not None:
                    _same(_resolve(entries, ref)["core_sha256"], value["core_sha256"])
        if before["admission_ref"] is not None:
            ref = before["admission_ref"]
            parent = entries["receipts"].get(ref["admission_id"])
            if parent is None or parent["state"] != "committed" or permanent.digest(parent["record"]) != ref["record_sha256"]:
                _fail("ADMISSION_PARENT_REQUIRED")
    for item in entries["receipts"].values():
        check()
        receipt = item["record"]
        if receipt["format_version"] == 2:
            intent = _resolve(entries, receipt["request_ref"])
            _same(intent["core_sha256"], receipt["core_sha256"])
        else:
            selected = entries["requests"].get(receipt["record_id"])
            if selected is None or selected["state"] != "committed":
                _fail("INTENT_REQUIRED")
            intent = selected["record"]
        _same(intent["request_sha256"], receipt["request_sha256"])
        _bound_result(intent, receipt)
        body, result = intent["request"], receipt["result"]
        if body["kind"] == "source_dispose":
            key = result["original_request_id"]
            fact = result["disposition"], result["original_receipt"]
            if key in outcomes and outcomes[key] != fact:
                _fail("OUTCOME_MISMATCH")
            outcomes[key] = deepcopy(fact)
            direct = entries["receipts"].get(key)
            if direct is not None and direct["state"] == "committed" and direct["record"]["format_version"] == 2:
                _same(result["disposition"], "applied")
                _same(result["original_receipt"], direct["record"]["result"])
        if body["kind"] == "source_readmit":
            previous = result["previous_admission_id"]
            if previous is not None:
                parent = entries["receipts"].get(previous)
                if parent is None or parent["state"] != "committed":
                    _fail("ADMISSION_PARENT_REQUIRED")
                _same(parent["record"]["result"]["core_sha256"], result["core_sha256"])
                _same(parent["record"]["result"]["claim"], body["expected_claim"])
            branch = result["core_sha256"], previous
            if branch in admissions and admissions[branch] != result["admission_id"]:
                _fail("ADMISSION_BRANCH")
            admissions[branch] = result["admission_id"]
    _control_relations2(entries, check=check)


def public_refs2(kind, record):
    if kind == "publications":
        from . import sync_source_publication_local2 as publications
        return publications.public_refs2(kind, record)
    refs = []
    def add(value):
        if value is not None:
            refs.append({"kind": "authority_record", **deepcopy(value)})
    def control_edge(body, core_sha):
        claim = body.get("claim")
        refs.append({"kind": "original_control", "request_id": body["request_id"],
            "request_sha256": permanent.digest(body), "original_kind": body["kind"], "core_sha256": core_sha,
            "item_id": None if claim is None else claim["item_id"],
            "lease_generation": None if claim is None else claim["lease_generation"],
            "budget_operation_id": body.get("budget_operation_id"), "defer_request_id": body.get("defer_request_id")})
    if kind == "controls":
        basis = record["creation_basis"]
        add(basis["origin_ref"])
        if basis["admission_ref"] is not None:
            refs.append({"kind": "admission", **deepcopy(basis["admission_ref"])})
        control_edge(record["request"], record["core_sha256"])
        if record["request"]["kind"] == "source_fail":
            failure = record["request"]["failure"]
            refs.append({"kind": "source_failure", **{name: failure[name] for name in (
                "format_version", "operation_id", "attempt_id", "input_sha256", "request_sha256",
                "settlement_sha256", "result_sha256")}})
        for continuity in (basis["continuity"], record["control_scope"]["continuity"]):
            edge = {"kind": "continuity", **deepcopy(continuity)}
            if edge not in refs:
                refs.append(edge)
        if record["parent_defer_refs"] is not None:
            for value in record["parent_defer_refs"].values():
                add(value)
        add(record["retirement_head"])
        for value in (record["preservation_provenance"], record["resume_provenance"]):
            if value is not None:
                refs.append({"kind": "provenance", **deepcopy(value)})
        for value in record["expected_before"]["source_authority"].values():
            if type(value) is dict and "record_kind" in value:
                add(value)
        return refs
    if kind == "receipts":
        add(record["request_ref"])
        if record["result"].get("admission_id") is not None:
            refs.append({"kind": "admission", **admission_ref(record)})
        if record["parent_defer"] is not None:
            control_edge(record["parent_defer"]["request"], record["core_sha256"])
    else:
        basis = record["creation_basis"]
        add(basis["origin_ref"])
        if basis["admission_ref"] is not None:
            refs.append({"kind": "admission", **deepcopy(basis["admission_ref"])})
        before = record["expected_before"]
        if before["source_authority"] is not None:
            for name in ("origin", "defer", "resume", "failure", "publication"):
                add(before["source_authority"][name])
        body = record["request"]
        refs.append({"kind": "recovery_request", "request_id": record["record_id"],
            "request_sha256": record["request_sha256"], "request_kind": body["kind"],
            "core_sha256": record["core_sha256"]})
        if body["kind"] == "budget_timer":
            add(body["origin_ref"])
            control_edge(body["defer_request"], record["core_sha256"])
        elif body["kind"] == "source_dispose":
            control_edge(body["original_request"], record["core_sha256"])
        for value in (record["preservation_provenance"], record["resume_provenance"]):
            if value is not None:
                refs.append({"kind": "provenance", **deepcopy(value)})
    return refs


def admission_ref(record):
    result = record["result"]
    return {"format_version": 1, **{key: result[key] for key in (
        "admission_id", "core_sha256", "request_sha256", "admission_sha256")}, "record_sha256": permanent.digest(record)}


def _creation_folder2(guard):
    from . import sync_source_lifecycle17 as lifecycle17
    if type(guard) is lifecycle17.ControlCreationGuard17:
        return "controls"
    if type(guard) is ReadmitIntentProducer2:
        return "requests"
    if type(guard) is lifecycle17.PublicationCreationGuard17:
        return "publications"
    from . import sync_source_publication_local2 as publications
    if type(guard) is publications.PublicationCreationGuard2:
        return "publications"
    _fail("CREATION_PRODUCER_REQUIRED")


class _PinnedClosure:
    """Full capture before SQL; the writer consumes only bounded identities."""
    def __init__(self, owner, observed, selected):
        self.owner, self.contract = owner, deepcopy(observed["contract"])
        self.records, self.files = {}, []
        # Closure has a hard permanent count limit. Each file was already
        # bounded/no-follow read and hashed by the real authority owner.
        for folder, values in observed["entries"].items():
            for key, item in values.items():
                if item["state"] != "committed":
                    _fail("RECORD_INCOMPLETE")
                self.records[(folder, key)] = deepcopy(item["record"])
                directory = owner._directory(observed["ledger"], folder)
                _same(owner._read(directory, key + ".json"), item["record"])
                self.files.append(owner.pin_index[(directory.path, key + ".json")])
        self.selected = deepcopy(selected)

    def short(self):
        self.owner._check()
        self.owner.lock.verify()
        # Immutable ledger identities, rather than mutable State, allow the
        # caller's receipt-first State CAS inside the original ownership.
        for directory, name, stream, signature, path_signature, sha in self.files:
            directory.verify()
            if (permanent._signature(directory.entry_stat(name)) != path_signature
                    or permanent._signature(os.fstat(stream.fileno())) != signature):
                _fail("RECORD_CHANGED")

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.short()
        observed = self.owner.observe()
        _same(observed["contract"], self.contract)
        _same({(folder, key): item["record"] for folder, values in observed["entries"].items()
            for key, item in values.items() if item["state"] == "committed"}, self.records)
        # observe() verified the original handles and bytes before returning;
        # only the fixed in-memory closure comparisons follow that read.

    def advance(self, guard, capture):
        """Only the original creation guard can attest its exact successor."""
        folder = _creation_folder2(guard)
        if guard.owner is not self.owner or sql.in_write_window():
            _fail("CREATION_PRODUCER_REQUIRED")
        observed = guard._advance_for_capture(capture)
        _same(observed["contract"], self.contract)
        all_records = {(folder, key): item["record"] for folder, values in observed["entries"].items()
            for key, item in values.items() if item["state"] == "committed"}
        for key, record in self.records.items():
            _same(all_records.get(key), record)
        extras = set(all_records) - set(self.records)
        allowed = (folder, guard.record["record_id"])
        if extras - {allowed}:
            _fail("CREATION_PRODUCER_REQUIRED")
        for folder, key in extras:
            _same(all_records[(folder, key)], guard.record)
            directory = self.owner._directory(observed["ledger"], folder)
            _same(self.owner._read(directory, key + ".json"), guard.record)
            self.files.append(self.owner.pin_index[(directory.path, key + ".json")])
        self.records = deepcopy(all_records)


class ControlReceiptCapture2:
    __slots__ = ("_seal", "_owner_context", "_active", "_profile", "_store", "_owner", "_closure",
        "_record", "_intent", "_origin", "_cookie", "_schema_sha", "_identity", "_version", "preservation_capture", "resume_capture")

    def __init__(self, *, profile, store, owner, closure, record, intent, origin, con, stack, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._owner_context, self._active = seal, engine._owner(), True
        self._profile, self._store, self._owner, self._closure = profile, store, owner, closure
        self._record, self._intent, self._origin = deepcopy(record), deepcopy(intent), deepcopy(origin)
        self._cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
        self._schema_sha, self._version = schema17._actual_schema(con), db.knowledge_version(con)
        self._identity = identity_pins._IdentityCapture(store, con, stack)
        self.preservation_capture = None
        self.resume_capture = None

    def _context(self):
        if (type(self) not in {ControlReceiptCapture2, AdmissionReceiptCapture2} or self._seal is not _SEAL
                or not self._active or _CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()

    def _fixed(self, con, token):
        self._context()
        if sql.require_writer(con, profile=self._profile) is not token or token.schema_version != 17:
            _fail("WRITER_REQUIRED")
        self._identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        version = db.knowledge_version(con)
        _same({key: version[key] for key in ("library_id", "library_epoch")},
            {key: self._version[key] for key in ("library_id", "library_epoch")})
        self._closure.short()
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_commit(self, connection, *, writer_permit=None):
        self._fixed(connection, writer_permit)
        if self.preservation_capture is not None:
            self.preservation_capture.verify_for_commit(connection, writer_permit=writer_permit)
        if self.resume_capture is not None:
            self.resume_capture.verify_for_commit(connection, writer_permit=writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self._profile)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()
        self._verify_readonly_sql()
        if self.preservation_capture is not None:
            self.preservation_capture.verify_source()
        if self.resume_capture is not None:
            self.resume_capture.verify_source()

    def _verify_readonly_sql(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema_sha)
            self._identity.verify_for_commit(con)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)

    def _advance_creation(self, guard):
        from . import sync_source_lifecycle17 as lifecycle17
        self._context()
        self._closure.advance(guard, self)
        control_creation = type(guard) is lifecycle17.ControlCreationGuard17
        if control_creation:
            self._closure.short()
            self._verify_readonly_sql()
            if self.preservation_capture is not None:
                self.preservation_capture.verify_source()
        if self.resume_capture is not None:
            self.resume_capture._advance_creation(guard, parent_capture=self)
            if control_creation:
                self.resume_capture.verify_source()

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    origin_core = core

    @property
    def origin_record(self):
        self._context()
        return deepcopy(self._origin)

    @property
    def origin_ref(self):
        return permanent.record_ref(self.origin_record)

    @property
    def request(self):
        self._context()
        return deepcopy(self._intent["request"])

    @property
    def result(self):
        self._context()
        return deepcopy(self._record["result"])

    @property
    def parent_defer(self):
        self._context()
        return deepcopy(self._record["parent_defer"])

    @property
    def request_ref(self):
        self._context()
        return deepcopy(self._record["request_ref"])

    @property
    def receipt_ref(self):
        self._context()
        return permanent.record_ref(self._record)

    @property
    def original_control_request(self):
        request = self.request
        if request["kind"] != "source_dispose":
            _fail("DISPOSITION_REQUIRED")
        return deepcopy(request["original_request"])

    @property
    def original_control_receipt(self):
        request, result = self.request, self.result
        if request["kind"] != "source_dispose":
            _fail("DISPOSITION_REQUIRED")
        return deepcopy(result["original_receipt"])

    @property
    def disposition(self):
        request, result = self.request, self.result
        if request["kind"] != "source_dispose":
            _fail("DISPOSITION_REQUIRED")
        return result["disposition"]

    @property
    def creation_basis(self):
        self._context()
        return deepcopy(self._intent["creation_basis"])

    @property
    def preservation_provenance(self):
        self._context()
        return deepcopy(self._intent.get("preservation_provenance"))

    @property
    def chain_refs(self):
        self._context()
        return tuple(permanent.record_ref(value) for value in self._closure.records.values())


class AdmissionReceiptCapture2(ControlReceiptCapture2):
    def admission_ref(self):
        self._context()
        return admission_ref(self._record)

    @property
    def claim_lease_until(self):
        return self.result["session"]["lease_until"]


class RetiredControlsCapture2(ControlReceiptCapture2):
    """Exact immutable negative facts; never current or paid admission."""
    __slots__ = ("_selected_negatives",)

    def _context(self):
        if (type(self) is not RetiredControlsCapture2 or self._seal is not _SEAL
                or not self._active or _RETIRED_CURRENT.get() is not self
                or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()

    @property
    def dispositions(self):
        self._context()
        return tuple(deepcopy(item["result"]) for item in self._selected_negatives)


@contextmanager
def capture_retired_controls_locked(*, profile, store, control_request_ids):
    """Read actual same-scope controls and their typed permanent negatives."""
    if sql.in_write_window() or _RETIRED_CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    if type(control_request_ids) is not tuple or not 1 <= len(control_request_ids) <= 2:
        _fail("DISPOSITION_SELECTOR_REQUIRED")
    for control_id in control_request_ids:
        permanent._uuid(control_id)
    if len(set(control_request_ids)) != len(control_request_ids):
        _fail("DISPOSITION_SELECTOR_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe()
        entries, state = observed["entries"], store.read()
        core, authority = state.get("source_execution"), state.get("source_authority")
        if core is None or authority is None:
            _fail("ORIGIN_REQUIRED")
        origin = _resolve(entries, authority["origin"])
        _same(origin["core"], core)
        prior = state["source_admission"]
        expected_claim = origin["claim"]
        if prior is not None:
            actual = entries["receipts"].get(prior["admission_id"])
            if actual is None or actual["state"] != "committed" or permanent.digest(actual["record"]) != prior["record_sha256"]:
                _fail("ADMISSION_PARENT_REQUIRED")
            expected_claim = actual["record"]["result"]["claim"]
        selected, intents = [], []
        for control_id in control_request_ids:
            actual = entries["controls"].get(control_id)
            if actual is None or actual["state"] != "committed":
                _fail("CONTROL_RECONCILIATION_REQUIRED")
            control = actual["record"]
            _same(control["core_sha256"], origin["core_sha256"])
            basis = control["creation_basis"]
            _same((basis["origin_ref"], basis["admission_ref"], basis["claim"]),
                (permanent.record_ref(origin), prior, expected_claim))
            matches = []
            for item in entries["receipts"].values():
                if item["state"] != "committed" or item["record"]["format_version"] != 2:
                    continue
                receipt, result = item["record"], item["record"]["result"]
                if "disposition" not in result or result["original_request_id"] != control_id:
                    continue
                intent = _resolve(entries, receipt["request_ref"])
                if intent["request"]["kind"] != "source_dispose":
                    _fail("CONTROL_RECONCILIATION_REQUIRED")
                _same(intent["request"]["original_request"], control["request"])
                _same((receipt["core_sha256"], result["core_sha256"], result["original_request_sha256"]),
                    (origin["core_sha256"], origin["core_sha256"], control["request_sha256"]))
                if result["disposition"] != "not_applied" or result["original_receipt"] is not None:
                    _fail("CONTROL_RECONCILIATION_REQUIRED")
                registration = intent["request"]["registration"]
                if registration is None:
                    _fail("REGISTRATION_REQUIRED")
                registered = entries["receipts"].get(registration["request_id"])
                if registered is None or registered["state"] != "committed" or registered["record"]["format_version"] != 2:
                    _fail("REGISTRATION_REQUIRED")
                _same(registered["record"]["result"], registration)
                _bound_result(intent, receipt)
                matches.append((receipt, intent))
            if len(matches) != 1:
                _fail("CONTROL_RECONCILIATION_REQUIRED")
            selected.append(matches[0][0])
            intents.append(matches[0][1])
        record_relations2(entries)
        closure = _PinnedClosure(owner, observed, selected)
        with sql.connection(profile) as con:
            value = RetiredControlsCapture2(profile=profile, store=store, owner=owner,
                closure=closure, record=selected[0], intent=intents[0], origin=origin,
                con=con, stack=stack, seal=_SEAL)
        value._selected_negatives = tuple(deepcopy(selected))
        token = _RETIRED_CURRENT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _RETIRED_CURRENT.reset(token)


class HistoricalAdmissionReceiptCapture2:
    """Historical immutable admission facts; no current writer/creation API."""
    __slots__ = ("_seal", "_active", "_owner_context", "_owner", "_profile", "_store", "_closure",
        "_record", "_intent", "_origin", "_schema_sha", "_cookie", "_identity", "_version", "_preservation", "_resume")

    def __init__(self, *, owner, profile, store, closure, record, intent, origin, con, stack, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, engine._owner()
        self._owner, self._profile, self._store, self._closure = owner, profile, store, closure
        self._record, self._intent, self._origin = deepcopy(record), deepcopy(intent), deepcopy(origin)
        self._schema_sha = schema17._actual_schema(con)
        self._cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
        self._identity, self._version = identity_pins._IdentityCapture(store, con, stack), db.knowledge_version(con)
        self._preservation = self._resume = None

    def _context(self):
        if (type(self) is not HistoricalAdmissionReceiptCapture2 or self._seal is not _SEAL or not self._active
                or _HISTORY_CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()

    @property
    def request(self):
        self._context()
        return deepcopy(self._intent["request"])

    @property
    def result(self):
        self._context()
        return deepcopy(self._record["result"])

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    @property
    def origin_record(self):
        self._context()
        return deepcopy(self._origin)

    @property
    def preservation_provenance(self):
        self._context()
        return deepcopy(self._intent["preservation_provenance"])

    def admission_ref(self):
        self._context()
        return admission_ref(self._record)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema_sha)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
            _same(db.knowledge_version(con), self._version)
            self._identity.verify_for_commit(con)
        if self._preservation is not None:
            self._preservation.verify_source()
        if self._resume is not None:
            self._resume.verify_source()


@contextmanager
def capture_admission_history_locked(*, profile, store, admission_id, expected_record_sha256=None):
    """Select the original admission and its real historical restore prefix."""
    from . import home_recovery_runtime17 as runtime
    if sql.in_write_window() or _HISTORY_CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    permanent._uuid(admission_id)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe()
        entries = observed["entries"]
        selected = entries["receipts"].get(admission_id)
        if selected is None or selected["state"] != "committed" or selected["record"]["format_version"] != 2:
            _fail("RECEIPT_REQUIRED")
        record = selected["record"]
        if expected_record_sha256 is not None:
            permanent._sha(expected_record_sha256)
            _same(permanent.digest(record), expected_record_sha256)
        intent = _resolve(entries, record["request_ref"])
        if intent["request"]["kind"] != "source_readmit" or intent["request"].get("format_version") != 2:
            _fail("RECEIPT_MISMATCH")
        origin = _origin(entries, record["core_sha256"])
        record_relations2(entries)
        _bound_result(intent, record)
        closure = _PinnedClosure(owner, observed, record)
        with sql.connection(profile) as con:
            value = HistoricalAdmissionReceiptCapture2(owner=owner, profile=profile, store=store,
                closure=closure, record=record, intent=intent, origin=origin, con=con, stack=stack, seal=_SEAL)
        token = _HISTORY_CURRENT.set(value)
        try:
            request, core = value.request, value.core
            if request["current_home_epoch"] > value._version["library_epoch"]:
                _fail("HISTORY_EPOCH_MISMATCH")
            if request["current_home_epoch"] == core["local_epoch"]:
                if request["preservation"] is not None or value.preservation_provenance is not None:
                    _fail("RESTORE_UNEXPECTED")
            else:
                provenance = value.preservation_provenance
                if provenance is None:
                    _fail("RESTORE_REQUIRED")
                actual = stack.enter_context(runtime.capture_historical_prefix_locked(profile=profile, store=store,
                    operation_id=provenance["operation_id"], expected_result_sha256=provenance["receipt_sha256"],
                    to_epoch=request["current_home_epoch"]))
                if type(actual) is not runtime.HistoricalRestorePrefixCapture17:
                    _fail("RESTORE_REQUIRED")
                _same(actual.provenance_ref(), provenance)
                observation = actual.preservation_observation(from_epoch=core["local_epoch"])
                _same(observation["source_core_sha256"], permanent.digest(core))
                _same(observation["preservation"], request["preservation"])
                value._preservation = actual
            resume = request["resume_basis"]
            if resume is not None:
                if resume["kind"] == "manual":
                    from . import sync_source_resume_basis2 as manual
                    actual = stack.enter_context(manual.capture_manual_locked(profile=profile, store=store,
                        defer_request_id=resume["defer_request_id"], origin_ref=permanent.record_ref(origin)))
                else:
                    from . import sync_source_timer2 as timer
                    actual = stack.enter_context(timer.capture_timer_locked(profile=profile, store=store,
                        defer_request_id=resume["defer_request_id"], origin_ref=permanent.record_ref(origin), owner=owner))
                _same(actual.wire_basis, resume)
                _same(actual.provenance_ref(), intent["resume_provenance"])
                value._resume = actual
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _HISTORY_CURRENT.reset(token)


class HistoricalControlBasisCapture2:
    """Immutable history bridge with a control-only original-writer API."""
    def __init__(self, *, profile, store, owner, observed, record, intent, origin, stack, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, engine._owner()
        self._profile, self._store, self._owner = profile, store, owner
        self._context_token = None
        self._record, self._intent, self._origin = deepcopy(record), deepcopy(intent), deepcopy(origin)
        self._closure = _PinnedClosure(owner, observed, record)
        result = record["result"]
        self._basis = {"format_version": 1, "kind": "admission", "core_sha256": origin["core_sha256"],
            "home_epoch": result["current_home_epoch"], "session": deepcopy(result["session"]),
            "claim": deepcopy(result["claim"]), **{key: deepcopy(result[key]) for key in (
                "recovery_generation", "recovery_stream", "control_revision")},
            "origin_ref": permanent.record_ref(origin), "admission_ref": admission_ref(record),
            "continuity": deepcopy(intent["creation_basis"]["continuity"])}
        permanent._basis(self._basis, origin["core_sha256"], observed["contract"]["binding"])
        with sql.connection(profile) as con:
            self._schema_sha, self._version = schema17._actual_schema(con), db.knowledge_version(con)
            self._cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self._identity = identity_pins._IdentityCapture(store, con, stack)

    def _context(self):
        if (type(self) is not HistoricalControlBasisCapture2 or self._seal is not _SEAL or not self._active
                or _CONTROL_BASIS_CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()
        _capture_context2(self, _CONTROL_BASIS_CONTEXT)
        state = self._store.read()
        _same((state["source_execution"], state["source_admission"], state["source_authority"]["origin"]),
              (self._origin["core"], admission_ref(self._record), permanent.record_ref(self._origin)))

    @property
    def creation_basis(self):
        self._context()
        return deepcopy(self._basis)

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    @property
    def origin_ref(self):
        self._context()
        return permanent.record_ref(self._origin)

    def admission_ref(self):
        self._context()
        return admission_ref(self._record)

    def _fixed(self, con, token, producer):
        from . import sync_source_lifecycle17 as lifecycle17
        self._context()
        if (type(producer) is not lifecycle17.ControlCreationGuard17 or producer.owner is not self._owner
                or getattr(producer, "_history_capture", None) is not self
                or producer.record["format_version"] != 2
                or sql.require_writer(con, profile=self._profile) is not token or token.schema_version != 17):
            _fail("CREATION_PRODUCER_REQUIRED")
        self._identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        _same(db.knowledge_version(con), self._version)
        self._closure.short()
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_control_creation(self, connection, *, writer_permit, producer):
        self._fixed(connection, writer_permit, producer)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit, producer),
            connection=connection, profile=self._profile)

    def _advance_creation(self, producer):
        from . import sync_source_lifecycle17 as lifecycle17
        self._context()
        if type(producer) is not lifecycle17.ControlCreationGuard17 or getattr(producer, "_history_capture", None) is not self:
            _fail("CREATION_PRODUCER_REQUIRED")
        self._closure.advance(producer, self)
        self._closure.short()
        self._verify_readonly_sql()

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()
        self._verify_readonly_sql()

    def _verify_readonly_sql(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema_sha)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
            _same(db.knowledge_version(con), self._version)
            self._identity.verify_for_commit(con)


@contextmanager
def capture_historical_control_basis_locked(*, profile, store, admission_id, expected_record_sha256=None):
    """Finish the old history reader before any latest restore is acquired."""
    from . import sync_source_lifecycle17 as lifecycle17
    if sql.in_write_window() or _CONTROL_BASIS_CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    permanent._uuid(admission_id)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack, lifecycle17._owned(store) as owner:
        state = store.read()
        prior = state["source_admission"]
        if prior is None or prior["admission_id"] != admission_id:
            _fail("ADMISSION_PARENT_REQUIRED")
        if expected_record_sha256 is not None:
            _same(prior["record_sha256"], expected_record_sha256)
        with capture_admission_history_locked(profile=profile, store=store, admission_id=admission_id,
                expected_record_sha256=prior["record_sha256"]) as historical:
            record, intent, origin = deepcopy(historical._record), deepcopy(historical._intent), historical.origin_record
        observed = owner.observe()
        _same(_resolve(observed["entries"], state["source_authority"]["origin"]), origin)
        value = HistoricalControlBasisCapture2(profile=profile, store=store, owner=owner, observed=observed,
            record=record, intent=intent, origin=origin, stack=stack, seal=_SEAL)
        token = _CONTROL_BASIS_CURRENT.set(value)
        value._context_token = _CONTROL_BASIS_CONTEXT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CONTROL_BASIS_CONTEXT.reset(value._context_token)
            _CONTROL_BASIS_CURRENT.reset(token)


def _legacy_resume_evidence2(entries, record, core):
    """Old wire omitted a permission choice; retain facts without inventing one."""
    defer_id = record["request"]["defer_request_id"]
    control = entries["controls"].get(defer_id)
    if control is None or control["state"] != "committed":
        _fail("PARENT_RECEIPT_REQUIRED")
    positives, negatives = _control_outcomes2(entries, control["record"])
    if negatives or not positives:
        _fail("PARENT_RECEIPT_REQUIRED")
    parent = {"request": control["record"]["request"], "receipt": positives[0][1]}
    _same((control["record"]["core_sha256"], control["record"]["request"]["claim"]),
          (permanent.digest(core), record["creation_basis"]["claim"]))
    key = wire.timer_operation_id(defer_id)
    timer = entries["receipts"].get(key)
    refs = None
    if timer is not None:
        if timer["state"] != "committed" or timer["record"]["format_version"] != 2:
            _fail("RESUME_REQUIRED")
        intent = _resolve(entries, timer["record"]["request_ref"])
        _timer(intent["request"])
        _bound_result(intent, timer["record"])
        _same((intent["request"]["core_sha256"], intent["request"]["defer_request"], intent["request"]["defer_receipt"]),
              (permanent.digest(core), parent["request"], parent["receipt"]))
        refs = {"intent_ref": permanent.record_ref(intent), "receipt_ref": permanent.record_ref(timer["record"])}
    return {"timer_refs": refs}


def _original_control_sql2(connection, record, core, *, legacy_evidence=None):
    """Bounded original SQL facts, never a fabricated binding or current gate."""
    from . import budget, sync_source_lifecycle17 as lifecycle17, sync_source_resume_basis2 as manual
    body = record["request"]
    if body["kind"] == "budget_resume":
        if record["format_version"] == 1:
            from . import tasks
            root = connection.execute("SELECT * FROM main.job WHERE job_id=?", (core["root_job_id"],)).fetchone()
            tasks._require_sync_job(root)
            request_id, _ = tasks._sync_resume_identity(connection, root, body["defer_request_id"])
            exists = connection.execute("SELECT 1 FROM main.task_request WHERE request_id=?", (request_id,)).fetchone()
            row = None if exists is None else manual._row(connection, core, body["defer_request_id"])
            if row is None and legacy_evidence["timer_refs"] is None:
                _fail("RESUME_REQUIRED")
            return {"kind": "legacy_resume", "manual_row": row, "timer_refs": deepcopy(legacy_evidence["timer_refs"])}
        provenance = record["resume_provenance"]
        if provenance["producer"] == "sync_budget_timer":
            return {"kind": "timer", "provenance": deepcopy(provenance)}
        row = manual._row(connection, core, body["defer_request_id"])
        _same((row["request_id"], permanent.digest(row)), (provenance["operation_id"], provenance["receipt_sha256"]))
        return {"kind": "manual", "row": row}
    operation_id = body["budget_operation_id"] if body["kind"] == "budget_defer" else body["failure"]["operation_id"]
    selected = connection.execute("SELECT * FROM main.budget_operation WHERE operation_id=?", (operation_id,)).fetchone()
    if selected is None:
        _fail("PAID_OPERATION_REQUIRED")
    operation = dict(selected)
    _same((operation["library_id"], operation["root_job_id"], operation["continuation_id"], operation["execution_generation"]),
          (core["binding"]["home_library_id"], core["root_job_id"], core["continuation_id"], core["execution_generation"]))
    namespace = permanent.digest({key: core[key] for key in ("binding", "item_id", "submitted_url_sha256")})
    if operation["kind"] != "model" and not operation["suboperation_key"].startswith("home:" + namespace + ":"):
        _fail("PAID_OPERATION_REQUIRED")
    if body["kind"] == "budget_defer":
        _same((operation["state"], operation["wait_reason"], lifecycle17._wait_deadline(operation["resume_at"])),
              ("waiting", body["reason"], body["retry_at"]))
        return {"kind": "wait", "operation": operation}
    failure = body["failure"]
    columns = tuple(row[1] for row in connection.execute("PRAGMA main.table_info(budget_attempt)") if row[1] != "result_text")
    selected = connection.execute("SELECT " + ",".join(columns) + " FROM main.budget_attempt WHERE attempt_id=?",
        (failure["attempt_id"],)).fetchone()
    if selected is None:
        _fail("PAID_RESULT_REQUIRED")
    attempt = dict(zip(columns, selected))
    _same((attempt["operation_id"], attempt["ordinal"], attempt["request_sha256"], operation["input_sha256"],
           operation["kind"], attempt["download_segment"]),
          (failure["operation_id"], failure["ordinal"], failure["request_sha256"], failure["input_sha256"],
           failure["operation_kind"], failure["segment"]))
    snapshot = budget._snapshot({
        **attempt,
        "kind": operation["kind"],
        "operation_max_attempts": operation["max_attempts"],
    })
    for key in ("source_contract_version", "resolver_contract_version", "resolver_phase"):
        _same(snapshot.get(key), failure[key])
    _same((attempt["state"], bool(attempt["response_complete"]), attempt["outcome"], attempt["result_storage_state"]),
          ("settled", True, "terminal_error", "stored"))
    return {"kind": "failure", "operation": operation, "attempt": attempt}


def _original_failure_body2(profile, record, core):
    from . import budget, source_contract, source_resolution_contract as resolver
    failure = record["request"]["failure"]
    receipt = budget.get_result(failure["attempt_id"], failure["request_sha256"], profile=profile)
    _same((receipt.storage_state, receipt.settlement_sha256, receipt.result_sha256, receipt.outcome),
          ("stored", failure["settlement_sha256"], failure["result_sha256"], "terminal_error"))
    if receipt.text is None:
        _fail("PAID_RESULT_REQUIRED")
    if failure["operation_kind"] == "source_resolution":
        with sql.connection(profile) as con:
            attempts = con.execute("SELECT max_attempts FROM main.budget_operation WHERE operation_id=?",
                (failure["operation_id"],)).fetchone()[0]
        keys = {"request_sha256": failure["request_sha256"], "input_sha256": failure["input_sha256"],
            "phase": failure["resolver_phase"], "max_attempts": attempts}
        plan, result = resolver.decode_plan(receipt.text, **keys), resolver.decode_envelope(receipt.text, **keys)
        _same(hashlib.sha256(plan.submitted_url.encode()).hexdigest(), core["submitted_url_sha256"])
        error = ("invalid_url" if result.failure_class in {"dns_rejected", "redirect_invalid", "resource_invalid", "header_invalid"}
            else "rate_limited" if result.status == 429 else "network" if result.retryable else "source_unavailable")
    else:
        result = source_contract.decode_envelope(receipt.text, request_sha256=failure["request_sha256"],
            input_sha256=failure["input_sha256"], kind=failure["operation_kind"], segment=failure["segment"])
        if failure["segment"] == "audio" and result.returncode > 0 and result.failure_class == "process_failure":
            _fail("PAID_RESULT_REQUIRED")
        error = "media_invalid" if result.failure_class == "artifact_invalid" else (
            "invalid_url" if result.failure_class in {"invalid_output", "unsafe_metadata"} else "source_unavailable")
    if not result.response_complete or result.successful or error != failure["error_class"]:
        _fail("PAID_RESULT_REQUIRED")


class ControlIntentCapture2:
    """Actual original reserved facts; never current or paid admission."""
    def __init__(self, recovery, *, seal):
        from .home_creation_recovery17 import OriginalCreationRecoveryCapture17
        if seal is not _SEAL or type(recovery) is not OriginalCreationRecoveryCapture17:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, engine._owner()
        self._context_token = None
        self._recovery = recovery
        self._record = recovery.record
        if (self._record["record_kind"] != "control" or self._record["format_version"] not in {1, 2}
                or self._record["request"]["source"]["handshake"]["migration_version"] != 17):
            _fail("ORIGINAL_CONTROL_REQUIRED")
        observed = recovery.actual_observation
        permanent.validate_record("controls", self._record, observed["contract"])
        self._graph = control_retirement_graph2(observed["entries"], self._record["record_id"],
            allow_reserved=recovery.record_state == "reserved")
        self._origin = _resolve(observed["entries"], self._record["creation_basis"]["origin_ref"])
        self._legacy_evidence = (_legacy_resume_evidence2(observed["entries"], self._record, self._origin["core"])
            if self._record["format_version"] == 1 and self._record["request"]["kind"] == "budget_resume" else None)
        with sql.connection(recovery.owner.profile) as con:
            self._original_sql = _original_control_sql2(con, self._record, self._origin["core"], legacy_evidence=self._legacy_evidence)

    def _context(self):
        if (type(self) is not ControlIntentCapture2 or self._seal is not _SEAL or not self._active
                or _CONTROL_INTENT_CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        _capture_context2(self, _CONTROL_INTENT_CONTEXT)

    @property
    def recovery_capture(self):
        self._context()
        return self._recovery

    @property
    def record(self):
        self._context()
        return deepcopy(self._record)

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    @property
    def creation_basis(self):
        self._context()
        return deepcopy(self._record["creation_basis"])

    @property
    def retirement_graph(self):
        self._context()
        return deepcopy(self._graph)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._recovery.verify_source()
        observed = self._recovery.actual_observation
        record_relations2(observed["entries"])
        _same(self._recovery.record, self._record)
        graph = control_retirement_graph2(observed["entries"], self._record["record_id"],
            allow_reserved=self._recovery.record_state == "reserved")
        # Completion may advance only this original leaf's durable state.
        before, after = deepcopy(self._graph), deepcopy(graph)
        before.pop("record_state")
        after.pop("record_state")
        _same(before, after)
        with sql.connection(self._recovery.owner.profile) as con:
            _same(_original_control_sql2(con, self._record, self._origin["core"], legacy_evidence=self._legacy_evidence), self._original_sql)
        if self._record["request"]["kind"] == "source_fail":
            _original_failure_body2(self._recovery.owner.profile, self._record, self._origin["core"])

    def verify_for_control_creation(self, connection, *, writer_permit, producer):
        self._context()
        from . import sync_source_lifecycle17 as lifecycle17
        if (type(producer) is not lifecycle17.ControlCompletionGuard17
                or getattr(producer, "_recovery_capture", None) is not self._recovery):
            _fail("CREATION_PRODUCER_REQUIRED")
        self._recovery.verify_for_creation_commit(connection, writer_permit=writer_permit, producer=producer)
        def final():
            self._context()
            _same(_original_control_sql2(connection, self._record, self._origin["core"], legacy_evidence=self._legacy_evidence), self._original_sql)
            sql.check(writer_permit, connection=connection, profile=self._recovery.owner.profile)
        final()
        sql.register_commit_check(writer_permit, final, connection=connection, profile=self._recovery.owner.profile)


@contextmanager
def capture_control_intent_locked(*, profile, store, request_id, expected_record_sha256=None):
    """Selectors only; retain the real unknown reservation instead of filtering."""
    from .home_creation_recovery17 import capture_original_creation_recovery17_locked
    if sql.in_write_window() or _CONTROL_INTENT_CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    with capture_original_creation_recovery17_locked(profile=profile, store=store, folder="controls",
            record_id=request_id, expected_record_sha256=expected_record_sha256) as recovery:
        value = ControlIntentCapture2(recovery, seal=_SEAL)
        token = _CONTROL_INTENT_CURRENT.set(value)
        value._context_token = _CONTROL_INTENT_CONTEXT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CONTROL_INTENT_CONTEXT.reset(value._context_token)
            _CONTROL_INTENT_CURRENT.reset(token)


def current_settlement2(entries, state):
    """Current two slots plus every proved retired ancestor, never all negatives."""
    from . import sync_source_legacy_ancestry2 as legacy
    core, authority, prior = state["source_execution"], state["source_authority"], state["source_admission"]
    if core is None or authority is None:
        _fail("ORIGIN_REQUIRED")
    origin = _resolve(entries, authority["origin"])
    _same(origin["core"], core)
    historical = legacy.legacy_consumption2(entries, state)
    history_only = {ref["record_id"]: ref for ref in historical["control_refs"]}
    consumed_current = legacy.current_consumed_control_ids2(entries, state)
    expected_claim = origin["claim"]
    if prior is not None:
        old = entries["receipts"].get(prior["admission_id"])
        if old is None or old["state"] != "committed" or permanent.digest(old["record"]) != prior["record_sha256"]:
            _fail("ADMISSION_PARENT_REQUIRED")
        expected_claim = old["record"]["result"]["claim"]
    groups, eligible = {}, {}
    for identifier, item in entries["controls"].items():
        record, basis = item["record"], item["record"]["creation_basis"]
        if identifier in history_only:
            _same(permanent.record_ref(record), history_only[identifier])
            continue
        if (record["core_sha256"] == origin["core_sha256"] and basis["origin_ref"] == authority["origin"]
                and basis["admission_ref"] == prior and basis["claim"] == expected_claim):
            if item["state"] != "committed":
                _fail("CONTROL_PENDING")
            eligible[identifier] = record
            groups.setdefault(_control_group2(record), identifier)
        elif record["format_version"] == 1 and record["core_sha256"] == origin["core_sha256"]:
            # A native wire2 admission may legitimately have consumed a
            # control1 retirement leaf. Unknown old controls remain blocking.
            graph = control_retirement_graph2(entries, identifier)
            if graph["leaf_ref"]["record_id"] not in consumed_current:
                _fail("CONTROL_RECONCILIATION_REQUIRED")
    selected, retired, graphs = {}, set(), []
    for identifier in groups.values():
        graph = control_retirement_graph2(entries, identifier)
        leaf = _resolve(entries, graph["leaf_ref"])
        kind = leaf["request"]["kind"]
        if kind in selected:
            _fail("CONTROL_FORK")
        selected[kind] = leaf
        retired.update(ref["record_id"] for ref in graph["ancestor_refs"])
        graphs.append(graph)
    for slot, kind in (("defer", "budget_defer"), ("resume", "budget_resume"), ("failure", "source_fail")):
        ref = authority[slot]
        if ref is not None:
            if ref["record_id"] in history_only:
                _same(ref, history_only[ref["record_id"]])
                _same(_resolve(entries, ref)["request"]["kind"], kind)
                continue
            if kind not in selected or ref != permanent.record_ref(selected[kind]):
                _fail("DISPOSITION_RECONCILIATION_REQUIRED")
    if "source_fail" in selected:
        positive, _ = _control_outcomes2(entries, selected["source_fail"])
        _fail("TERMINAL_OUTCOME" if positive else "CONTROL_RECONCILIATION_REQUIRED")
    defer, resume = selected.get("budget_defer"), selected.get("budget_resume")
    if resume is not None:
        if defer is None or resume["request"]["defer_request_id"] != defer["record_id"]:
            _fail("PARENT_MISMATCH")
    if set(eligible) != retired | {record["record_id"] for record in selected.values()}:
        _fail("CONTROL_RECONCILIATION_REQUIRED")
    facts, dispositions = {}, []
    for kind, record in selected.items():
        positives, negatives = _control_outcomes2(entries, record)
        if positives:
            preferred = None
            if kind == "budget_defer" and resume is not None and resume["format_version"] == 2:
                preferred = resume["parent_defer_refs"]["receipt_ref"]
            candidates = sorted(positives, key=lambda value: (value[0]["record_id"] != record["record_id"], value[0]["record_id"]))
            if preferred is not None:
                candidates = [value for value in positives if permanent.record_ref(value[0]) == preferred]
                if not candidates:
                    _fail("PARENT_RECEIPT_REQUIRED")
            receipt, positive, disposition = candidates[0]
            facts[kind] = {"request": deepcopy(record["request"]), "receipt": deepcopy(positive)}
            if disposition is not None:
                dispositions.append(deepcopy(disposition))
        elif negatives:
            dispositions.append(deepcopy(negatives[0]["result"]))
        else:
            _fail("CONTROL_RECONCILIATION_REQUIRED")
    if len(dispositions) > 2:
        _fail("DISPOSITION_SELECTOR_REQUIRED")
    if "budget_defer" in facts and "budget_resume" not in facts:
        _fail("RESUME_REQUIRED")
    if "budget_resume" in facts:
        _same(facts["budget_resume"]["request"]["defer_request_id"], facts["budget_defer"]["request"]["request_id"])
    return {"defer_fact": deepcopy(facts.get("budget_defer")), "resume_fact": deepcopy(facts.get("budget_resume")),
        "dispositions": dispositions, "current_control_refs": [permanent.record_ref(value) for value in selected.values()],
        "retired_control_refs": [permanent.record_ref(eligible[key]) for key in sorted(retired)],
        "graphs": deepcopy(graphs),
        "history_only_control_refs": list(deepcopy(historical["control_refs"])),
        "legacy_admission_refs": list(deepcopy(historical["admission_refs"]))}


class CurrentSettlementCapture2:
    """Original permanent selection; control settlement never grants paid work."""
    def __init__(self, *, profile, store, owner, observed, state, stack, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, engine._owner()
        self._profile, self._store, self._owner = profile, store, owner
        self._context_token = None
        self._state, self._selection = deepcopy(state), current_settlement2(observed["entries"], state)
        self._closure = _PinnedClosure(owner, observed, self._selection)
        with sql.connection(profile) as con:
            self._schema_sha, self._cookie = schema17._actual_schema(con), con.execute("PRAGMA main.schema_version").fetchone()[0]
            self._version = db.knowledge_version(con)
            self._identity = identity_pins._IdentityCapture(store, con, stack)

    def _context(self):
        if (type(self) is not CurrentSettlementCapture2 or self._seal is not _SEAL or not self._active
                or _SETTLEMENT_CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()
        _capture_context2(self, _SETTLEMENT_CONTEXT)
        _same(self._store.read(), self._state)

    @property
    def defer_fact(self):
        self._context()
        return deepcopy(self._selection["defer_fact"])

    @property
    def resume_fact(self):
        self._context()
        return deepcopy(self._selection["resume_fact"])

    @property
    def dispositions(self):
        self._context()
        return tuple(deepcopy(self._selection["dispositions"]))

    @property
    def current_control_refs(self):
        self._context()
        return tuple(deepcopy(self._selection["current_control_refs"]))

    @property
    def retired_control_refs(self):
        self._context()
        return tuple(deepcopy(self._selection["retired_control_refs"]))

    @property
    def history_only_control_refs(self):
        self._context()
        return tuple(deepcopy(self._selection["history_only_control_refs"]))

    @property
    def legacy_admission_refs(self):
        self._context()
        return tuple(deepcopy(self._selection["legacy_admission_refs"]))

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()
        observed = self._owner.observe()
        _same(current_settlement2(observed["entries"], self._state), self._selection)
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema_sha)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
            _same(db.knowledge_version(con), self._version)
            self._identity.verify_for_commit(con)

    def _advance_creation(self, producer):
        self._context()
        if type(producer) is not ReadmitIntentProducer2 or getattr(producer, "_settlement", None) is not self:
            _fail("CREATION_PRODUCER_REQUIRED")
        self._closure.advance(producer, self)

    def _fixed(self, con, token, producer):
        self._context()
        if (type(producer) is not ReadmitIntentProducer2 or getattr(producer, "_settlement", None) is not self
                or producer.owner is not self._owner or sql.require_writer(con, profile=self._profile) is not token
                or token.schema_version != 17):
            _fail("CREATION_PRODUCER_REQUIRED")
        self._closure.short()
        record = producer.record
        request, basis = record["request"], record["creation_basis"]
        prior = self._state["source_admission"]
        origin = self._closure.records[("origins", self._state["source_authority"]["origin"]["record_id"])]
        claim = (origin["claim"] if prior is None else
            self._closure.records[("receipts", prior["admission_id"])]["result"]["claim"])
        _same((request["previous_admission_id"], request["expected_claim"], basis["admission_ref"], basis["claim"]),
              (None if prior is None else prior["admission_id"], claim, prior, claim))
        _same(request["dispositions"], self._selection["dispositions"])
        for name, key in (("defer_receipt", "defer_fact"), ("resume_receipt", "resume_fact")):
            fact = self._selection[key]
            _same(request[name], None if fact is None else fact["receipt"])
        self._identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        _same(db.knowledge_version(con), self._version)
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_readmit_creation(self, connection, *, writer_permit, producer):
        self._fixed(connection, writer_permit, producer)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit, producer),
            connection=connection, profile=self._profile)


@contextmanager
def capture_current_settlement_locked(*, profile, store):
    from . import sync_source_lifecycle17 as lifecycle17
    from . import sync_source_legacy_ancestry2 as legacy
    if sql.in_write_window() or _SETTLEMENT_CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack, lifecycle17._owned(store) as owner:
        observed = owner.observe()
        record_relations2(observed["entries"])
        # Legacy freezes State and the facility. Close it before the new
        # producer appends; this capture's own closure then pins every original.
        if legacy.legacy_prefix2(observed["entries"], store.read()) is not None:
            with legacy.capture_legacy_ancestry_locked(profile=profile, store=store) as old:
                if old is None:
                    _fail("ADMISSION_PARENT_REQUIRED")
                old.verify_source()
                selected = current_settlement2(observed["entries"], store.read())
                _same(tuple(selected["history_only_control_refs"]), old.consumed_control_refs)
                _same(tuple(selected["legacy_admission_refs"]),
                      (*old.ancestor_admission_refs, old.admission_ref()))
        value = CurrentSettlementCapture2(profile=profile, store=store, owner=owner, observed=observed,
            state=store.read(), stack=stack, seal=_SEAL)
        token = _SETTLEMENT_CURRENT.set(value)
        value._context_token = _SETTLEMENT_CONTEXT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _SETTLEMENT_CONTEXT.reset(value._context_token)
            _SETTLEMENT_CURRENT.reset(token)


def _origin(entries, core_sha):
    values = [item["record"] for item in entries["origins"].values()
        if item["state"] == "committed" and item["record"]["core_sha256"] == core_sha]
    if len(values) != 1:
        _fail("ORIGIN_REQUIRED")
    return values[0]


def _preservation(value, stack):
    from . import home_recovery_runtime17 as runtime
    request, core = value.request, value.core
    provenance = value.preservation_provenance
    if request["current_home_epoch"] == core["local_epoch"]:
        if request["preservation"] is not None or provenance is not None:
            _fail("RESTORE_UNEXPECTED")
        return
    if provenance is None:
        _fail("RESTORE_REQUIRED")
    actual = stack.enter_context(runtime.capture_completed_locked(profile=value._profile, store=value._store,
        operation_id=provenance["operation_id"], expected_result_sha256=provenance["receipt_sha256"]))
    if type(actual) is not runtime.CompletedRestoreCapture17:
        _fail("RESTORE_REQUIRED")
    _same(actual.provenance_ref(), provenance)
    # These original documents/rows were captured and verified by this exact
    # sealed factory; a string provenance ref is never sufficient.
    observation = actual.preservation_observation(from_epoch=core["local_epoch"])
    expected = observation["preservation"]
    _same(observation["source_core_sha256"], permanent.digest(core))
    _same(request["preservation"], expected)
    _same((expected["from_epoch"], expected["to_epoch"]), (core["local_epoch"], request["current_home_epoch"]))
    value.preservation_capture = actual


@contextmanager
def _capture(*, profile, store, request_id, expected_record_sha256, admission):
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    permanent._uuid(request_id)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe()
        item = observed["entries"]["receipts"].get(request_id)
        if item is None or item["state"] != "committed" or item["record"]["format_version"] != 2:
            _fail("RECEIPT_REQUIRED")
        record = item["record"]
        if expected_record_sha256 is not None:
            permanent._sha(expected_record_sha256)
            _same(permanent.digest(record), expected_record_sha256)
        intent = _resolve(observed["entries"], record["request_ref"])
        if admission != (intent["request"]["kind"] == "source_readmit"):
            _fail("RECEIPT_MISMATCH")
        if admission and intent["request"].get("format_version") != 2:
            _fail("RECEIPT_MISMATCH")
        origin = _origin(observed["entries"], record["core_sha256"])
        record_relations2(observed["entries"])
        closure = _PinnedClosure(owner, observed, record)
        with sql.connection(profile) as con:
            cls = AdmissionReceiptCapture2 if admission else ControlReceiptCapture2
            value = cls(profile=profile, store=store, owner=owner, closure=closure,
                record=record, intent=intent, origin=origin, con=con, stack=stack, seal=_SEAL)
        token = _CURRENT.set(value)
        try:
            if admission or intent["request"]["kind"] == "source_dispose" and intent["request"].get("format_version") == 2:
                _preservation(value, stack)
            if admission:
                _verify_resume(value, stack)
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CURRENT.reset(token)


@contextmanager
def capture_admission_locked(*, profile, store, admission_id, expected_record_sha256=None):
    with _capture(profile=profile, store=store, request_id=admission_id,
            expected_record_sha256=expected_record_sha256, admission=True) as value:
        yield value


@contextmanager
def capture_control_receipt_locked(*, profile, store, request_id, expected_record_sha256=None):
    with _capture(profile=profile, store=store, request_id=request_id,
            expected_record_sha256=expected_record_sha256, admission=False) as value:
        yield value


@contextmanager
def capture_disposition_locked(*, profile, store, control_request_id, expected_record_sha256=None):
    """Actual original-control selector; preserve the typed disposition fact."""
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    permanent._uuid(control_request_id)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe()
        entries = observed["entries"]
        original = entries["controls"].get(control_request_id)
        if original is None or original["state"] != "committed":
            _fail("ORIGINAL_CONTROL_REQUIRED")
        selected = []
        for item in entries["receipts"].values():
            if item["state"] != "committed":
                continue
            receipt, result = item["record"], item["record"]["result"]
            if (receipt["format_version"] == 2 and "disposition" in result
                    and result["original_request_id"] == control_request_id):
                intent = _resolve(entries, receipt["request_ref"])
                _same(intent["request"]["original_request"], original["record"]["request"])
                _same(receipt["core_sha256"], original["record"]["core_sha256"])
                selected.append(receipt)
        if len(selected) != 1:
            _fail("DISPOSITION_SELECTOR_REQUIRED")
        receipt = selected[0]
        with _capture(profile=profile, store=store, request_id=receipt["record_id"],
                expected_record_sha256=expected_record_sha256, admission=False) as value:
            _same(value.original_control_request, original["record"]["request"])
            yield value


def _verify_resume(value, stack):
    basis = value.request["resume_basis"]
    if basis is None:
        return
    provenance = value._intent["resume_provenance"]
    if basis["kind"] == "manual":
        from . import sync_source_resume_basis2 as manual
        actual = stack.enter_context(manual.capture_manual_locked(profile=value._profile, store=value._store,
            defer_request_id=basis["defer_request_id"], origin_ref=value.origin_ref))
        _same(actual.wire_basis, basis)
        _same(actual.provenance_ref(), provenance)
        value.resume_capture = actual
    else:
        from . import sync_source_timer2 as timer
        actual = stack.enter_context(timer.capture_timer_locked(profile=value._profile, store=value._store,
            defer_request_id=basis["defer_request_id"], origin_ref=value.origin_ref, owner=value._owner))
        _same(actual.wire_basis, basis)
        _same(actual.provenance_ref(), provenance)
        value.resume_capture = actual


class HttpReceiptPublisher2:
    __slots__ = ("owner", "ticket", "record", "_seal", "_owner_context", "_active")
    def __init__(self, owner, ticket, record, *, seal):
        if seal is not _SEAL or type(ticket) is not transport.HttpReceiptCapture17:
            _fail("PRODUCER_REQUIRED")
        self.owner, self.ticket, self.record = owner, ticket, deepcopy(record)
        self._seal, self._owner_context, self._active = seal, engine._owner(), True

    def check(self, record=None):
        if (type(self) is not HttpReceiptPublisher2 or self._seal is not _SEAL or not self._active
                or _PUBLISHER.get() is not self or permanent._CURRENT.get() is not self.owner
                or self._owner_context != engine._owner()):
            _fail("PRODUCER_REQUIRED")
        self.owner._check()
        self.ticket.check()
        if record is not None:
            _same(record, self.record)
        _same(self.record["result"], self.ticket.result)
        _same(self.record["transport_provenance"], self.ticket.provenance)


def require_producer2(owner, kind, record, producer, *, entries):
    if kind == "publications":
        from . import sync_source_publication_local2 as publications
        return publications.require_producer2(owner, kind, record, producer, entries=entries)
    if kind == "controls":
        from . import sync_source_lifecycle17 as lifecycle17
        completion = getattr(lifecycle17, "ControlCompletionGuard17", None)
        expected = {lifecycle17.ControlCreationGuard17}
        if completion is not None:
            expected.add(completion)
        if type(producer) not in expected or producer.owner is not owner:
            _fail("PRODUCER_REQUIRED")
        producer.check(record)
        return
    if kind == "receipts" and record["result"].get("kind") != "budget_timer":
        if type(producer) is not HttpReceiptPublisher2 or producer.owner is not owner:
            _fail("PRODUCER_REQUIRED")
        producer.check(record)
        return
    if kind == "requests" and record["request"].get("kind") in {"source_readmit", "source_dispose"}:
        if type(producer) is not ReadmitIntentProducer2 or producer.owner is not owner:
            _fail("PRODUCER_REQUIRED")
        producer.check(record)
        return
    from . import sync_source_timer2 as timer
    timer.require_producer(owner, kind, record, producer)


def publish_http_receipt_locked(*, profile, store, client, request_id, request_sha256):
    """Receipt-first original fact publication; never update caller State."""
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    with transport.capture_receipt(client, request_id=request_id, request_sha256=request_sha256) as ticket:
        with permanent.owned(store) as owner:
            observed = owner.observe()
            entries = observed["entries"]
            matches = [(folder, item["record"]) for folder in ("controls", "requests")
                for key, item in entries[folder].items() if key == request_id and item["state"] == "committed"]
            if len(matches) != 1:
                _fail("INTENT_REQUIRED")
            _, intent = matches[0]
            _same((intent["request"], intent["request_sha256"]), (ticket.request, request_sha256))
            parent = None
            request = ticket.request
            if request["kind"] == "budget_resume" or (request["kind"] == "source_dispose"
                    and request["original_request"]["kind"] == "budget_resume" and ticket.result["disposition"] == "applied"):
                old = request if request["kind"] == "budget_resume" else request["original_request"]
                parent = _actual_parent(entries, old["defer_request_id"], intent["core_sha256"])
                _same(parent["receipt"], {**ticket.defer_receipt, "replayed": False})
            record = {"format_version": 2, "record_kind": "recovery_receipt", "record_id": request_id,
                "core_sha256": intent["core_sha256"], "request_ref": permanent.record_ref(intent),
                "request_sha256": request_sha256, "result": ticket.result,
                "result_sha256": permanent.digest(ticket.result), "parent_defer": parent,
                "transport_provenance": ticket.provenance}
            validate_record2("receipts", record, observed["contract"])
            producer = HttpReceiptPublisher2(owner, ticket, record, seal=_SEAL)
            token = _PUBLISHER.set(producer)
            try:
                producer.check(record)
                saved = owner.publish("receipts", record, producer=producer)
                producer.check(record)
            finally:
                producer._active = False
                _PUBLISHER.reset(token)
            return {"record": deepcopy(saved), "ref": permanent.record_ref(saved)}


def _actual_parent(entries, defer_id, core_sha):
    for item in entries["receipts"].values():
        if item["state"] != "committed":
            continue
        record, result = item["record"], item["record"]["result"]
        if record["format_version"] == 2:
            intent = _resolve(entries, record["request_ref"])
            if intent["request"]["kind"] == "budget_defer" and intent["record_id"] == defer_id and intent["core_sha256"] == core_sha:
                return {"request": deepcopy(intent["request"]), "receipt": deepcopy(result)}
        if result.get("original_kind") == "budget_defer" and result.get("original_request_id") == defer_id and result.get("disposition") == "applied" and result.get("core_sha256") == core_sha:
            intent = (_resolve(entries, record["request_ref"]) if record["format_version"] == 2
                else entries["requests"][record["record_id"]]["record"])
            return {"request": deepcopy(intent["request"]["original_request"]), "receipt": deepcopy(result["original_receipt"])}
    _fail("PARENT_RECEIPT_REQUIRED")


class ReadmitIntentProducer2:
    """Lexical actual State/worker/current-HTTP v2 intent creator."""
    __slots__ = ("owner", "_schema", "_transport", "_record", "_state", "_restore", "_resume", "_retired", "_settlement", "_base_head", "_base_entries",
        "_seal", "_active", "_owner_context")
    def __init__(self, owner, schema, current, record, state, restore, resume, observed, *, seal, retired=None, settlement=None):
        if seal is not _SEAL:
            _fail("PRODUCER_REQUIRED")
        self.owner, self._schema, self._transport = owner, schema, current
        self._record, self._state = deepcopy(record), deepcopy(state)
        self._restore, self._resume = restore, resume
        if retired is not None and type(retired) is not RetiredControlsCapture2:
            _fail("PRODUCER_REQUIRED")
        self._retired = retired
        if settlement is not None and type(settlement) is not CurrentSettlementCapture2:
            _fail("PRODUCER_REQUIRED")
        self._settlement = settlement
        self._base_head, self._base_entries = deepcopy(observed["head"]), deepcopy(observed["entries"])
        self._seal, self._active, self._owner_context = seal, True, engine._owner()

    @property
    def profile(self):
        return self.owner.profile

    @property
    def store(self):
        return self.owner.lock.store

    @property
    def record(self):
        return deepcopy(self._record)

    def _advance_for_capture(self, captured):
        from . import sync_source_lifecycle17 as lifecycle17
        if (captured not in (self._restore, self._resume, self._retired, self._settlement) or captured is None
                or type(self) is not ReadmitIntentProducer2 or self._seal is not _SEAL or not self._active
                or _PUBLISHER.get() is not self or permanent._CURRENT.get() is not self.owner
                or self._owner_context != engine._owner() or sql.in_write_window()):
            _fail("CREATION_PRODUCER_REQUIRED")
        self.owner._check()
        return lifecycle17._successor(self.owner, self._base_head, self._base_entries, "requests", self.record)

    def _fixed(self, con, token):
        if (type(self) is not ReadmitIntentProducer2 or self._seal is not _SEAL or not self._active
                or _PUBLISHER.get() is not self or permanent._CURRENT.get() is not self.owner
                or self._owner_context != engine._owner()
                or sql.require_writer(con, profile=self.owner.profile) is not token or token.schema_version != 17):
            _fail("PRODUCER_REQUIRED")
        self.owner._check()
        self._schema.short(con)
        _same(self.owner.lock.store.read(), self._state)
        _same(self._transport.check(state=self._state), self._record["creation_basis"]["continuity"])
        if self._restore is not None:
            self._restore.verify_for_commit(con, writer_permit=token)
        if self._resume is not None:
            self._resume.verify_for_commit(con, writer_permit=token)
        if self._retired is not None:
            self._retired.verify_for_commit(con, writer_permit=token)
        if self._settlement is not None:
            self._settlement.verify_for_readmit_creation(con, writer_permit=token, producer=self)
        sql.check(token, connection=con, profile=self.owner.profile)

    def check(self, record=None):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if record is not None:
            _same(record, self._record)
        if self._restore is not None:
            self._restore._advance_creation(self)
            self._restore.verify_source()
        if self._resume is not None:
            self._resume._advance_creation(self)
            self._resume.verify_source()
        if self._retired is not None:
            self._retired._advance_creation(self)
            self._retired.verify_source()
        if self._settlement is not None:
            self._settlement._advance_creation(self)
            self._settlement.verify_source()
        with sql.connection(self.owner.profile, write=True) as con:
            token = sql.require_writer(con, profile=self.owner.profile)
            self._fixed(con, token)
            # Child producer comparisons have their own fixed final checks.
            sql.register_commit_check(token, lambda: self._final(con, token), connection=con, profile=self.owner.profile)

    def _final(self, con, token):
        # Do not replay child public callbacks or register callbacks at commit.
        self.owner._check()
        self._schema.short(con)
        _same(self.owner.lock.store.read(), self._state)
        _same(self._transport.check(state=self._state), self._record["creation_basis"]["continuity"])
        sql.check(token, connection=con, profile=self.owner.profile)


class _DisposeTransport:
    """Original continuity observation for historical control reconciliation."""
    def __init__(self, client, source, state):
        from . import sync_source_lifecycle17 as lifecycle17
        self._current = lifecycle17._ControlContinuity(client, source, state)
        self._state = deepcopy(state)

    def check(self, *, state):
        _same(state, self._state)
        self._current.check()
        return self._current.value


def create_readmit_intent_locked(*, profile, store, client, request_id):
    """Build from actual selectors/State/ledger, before a recovery-pending CAS."""
    from . import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
    from . import sync_source_admission17 as admission, sync_source_genesis17 as genesis
    from . import sync_source_resume_basis2 as manual, sync_source_timer2 as timer
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(request_id)
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        # A restoration proof captures its own complete permanent prefix. It
        # must be captured before opening the authority owner, avoiding nested
        # authority ownership in the producer's full-reader factory.
        state = store.read()
        if state.get("source_execution") is None or state.get("source_authority") is None:
            _fail("ORIGIN_REQUIRED")
        core = deepcopy(state["source_execution"])
        restored, preservation, preservation_provenance = None, None, None
        if state["local_epoch"] != core["local_epoch"]:
            with sql.connection(profile) as con:
                rows = con.execute("SELECT operation_id,result_sha256 FROM " + restore_schema.TABLE
                    + " WHERE after_library_epoch=? AND library_id=?", (state["local_epoch"], core["binding"]["home_library_id"])).fetchall()
            if len(rows) != 1:
                _fail("RESTORE_REQUIRED")
            restored = stack.enter_context(runtime.capture_completed_locked(profile=profile, store=store,
                operation_id=rows[0][0], expected_result_sha256=rows[0][1]))
            observation = restored.preservation_observation(from_epoch=core["local_epoch"])
            _same(observation["source_core_sha256"], permanent.digest(core))
            preservation, preservation_provenance = observation["preservation"], restored.provenance_ref()
        owner = stack.enter_context(permanent.owned(store))
        observed = owner.observe()
        origin = _resolve(observed["entries"], state["source_authority"]["origin"])
        _same(origin["core"], core)
        schema = genesis._SchemaCapture(profile, store, stack)
        _same((schema.job["job_id"], schema.job["continuation_id"], schema.job["execution_generation"]),
            (core["root_job_id"], core["continuation_id"], core["execution_generation"]))
        current = admission._CurrentTransport(client, schema.source)
        continuity = current.check(state=state)
        prior = state["source_admission"]
        if prior is None:
            expected_claim = deepcopy(origin["claim"])
        else:
            old = observed["entries"]["receipts"].get(prior["admission_id"])
            if old is None or old["state"] != "committed" or permanent.digest(old["record"]) != prior["record_sha256"]:
                _fail("ADMISSION_PARENT_REQUIRED")
            expected_claim = deepcopy(old["record"]["result"]["claim"])
        settlement = stack.enter_context(capture_current_settlement_locked(profile=profile, store=store))
        dispositions = list(settlement.dispositions)
        defer_fact, resume_fact = settlement.defer_fact, settlement.resume_fact
        defer = None if defer_fact is None else defer_fact["receipt"]
        resumed = None if resume_fact is None else resume_fact["receipt"]
        resume_capture = None
        for item in observed["entries"]["requests"].values():
            record = item["record"]
            body = record["request"]
            if (record["core_sha256"] == origin["core_sha256"] and body["kind"] == "source_readmit"
                    and body["previous_admission_id"] == (None if prior is None else prior["admission_id"])
                    and record["record_id"] != request_id):
                _fail("ADMISSION_RECONCILIATION_REQUIRED")
        if defer is not None:
            if resumed is None:
                _fail("RESUME_REQUIRED")
            # Select an already committed timer proof first; do not create it
            # here or reinterpret a not-before clear as a due producer.
            timer_id = wire.timer_operation_id(defer["request_id"])
            has_timer = observed["entries"]["receipts"].get(timer_id)
            if has_timer is not None and has_timer["state"] == "committed":
                resume_capture = stack.enter_context(timer.capture_timer_locked(profile=profile, store=store,
                    defer_request_id=defer["request_id"], origin_ref=permanent.record_ref(origin), owner=owner))
            else:
                resume_capture = stack.enter_context(manual.capture_manual_locked(profile=profile, store=store,
                    defer_request_id=defer["request_id"], origin_ref=permanent.record_ref(origin)))
        request = {"format_version": 2, "kind": "source_readmit", "request_id": request_id,
            "source": deepcopy(schema.source), "target": {"library_id": core["binding"]["target_library_id"], "node_id": core["binding"]["target_node_id"]},
            "lease": deepcopy(state["lease"]), "recovery_generation": state["recovery"]["generation"],
            "recovery_stream": deepcopy(state["recovery"]["stream"]), "control_revision": state["recovery"]["revision"],
            "original": {"core_sha256": permanent.digest(core), "home_epoch": core["local_epoch"],
                "item_id": core["item_id"], "submitted_url_sha256": core["submitted_url_sha256"],
                "claim": deepcopy(origin["claim"]), "recovery_generation": core["recovery_generation"],
                "recovery_stream": deepcopy(core["recovery_stream"])},
            "current_home_epoch": state["local_epoch"], "preservation": deepcopy(preservation),
            "expected_claim": expected_claim, "previous_admission_id": None if prior is None else prior["admission_id"],
            "dispositions": dispositions, "defer_receipt": defer, "resume_receipt": resumed,
            "resume_basis": None if resume_capture is None else resume_capture.wire_basis}
        request = wire.normalize_readmit_request(request)
        basis = {"format_version": 1, "kind": "original_core" if prior is None else "admission",
            "core_sha256": permanent.digest(core), "home_epoch": state["local_epoch"],
            "session": deepcopy(state["lease"]), "claim": deepcopy(expected_claim),
            "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
            "control_revision": state["recovery"]["revision"], "origin_ref": permanent.record_ref(origin),
            "admission_ref": deepcopy(prior), "continuity": continuity}
        record = {"format_version": 2, "record_kind": "recovery_intent", "record_id": request_id,
            "core_sha256": permanent.digest(core), "request_sha256": permanent.digest(request), "request": request,
            "expected_before": timer._before(state, core, expected_claim), "creation_basis": basis,
            "preservation_provenance": preservation_provenance,
            "resume_provenance": None if resume_capture is None else resume_capture.provenance_ref()}
        validate_record2("requests", record, observed["contract"])
        existing = observed["entries"]["requests"].get(request_id)
        if existing is not None:
            _same(existing["record"], record)
        producer = ReadmitIntentProducer2(owner, schema, current, record, state, restored, resume_capture, observed,
            seal=_SEAL, settlement=settlement)
        token = _PUBLISHER.set(producer)
        try:
            saved = owner.publish("requests", record, producer=producer)
            producer.check(record)
        finally:
            producer._active = False
            _PUBLISHER.reset(token)
        return {"record": deepcopy(saved), "ref": permanent.record_ref(saved)}


def create_dispose_intent_locked(*, profile, store, client, control_request_id, request_id):
    """Select an actual original control; no caller body or authority DTO."""
    from . import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
    from . import sync_source_lifecycle17 as lifecycle17
    from . import sync_source_timer2 as timer
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(control_request_id)
    permanent._uuid(request_id)
    if control_request_id == request_id:
        _fail("REQUEST_MISMATCH")
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        state = store.read()
        if state.get("source_execution") is None or state.get("source_authority") is None:
            _fail("ORIGIN_REQUIRED")
        core = deepcopy(state["source_execution"])
        # A spent original is rejected before opening a restoration scope.
        # Close this historical read before any restoration owns the facility.
        if state["source_admission"] is not None:
            from . import sync_source_legacy_ancestry2 as legacy
            with permanent.owned(store) as prior_owner:
                old = prior_owner.observe_readonly()
                consumed = legacy.legacy_consumption2(old["entries"], state)["control_refs"]
                if control_request_id in {ref["record_id"] for ref in consumed}:
                    _fail("CONTROL_ALREADY_CONSUMED")
        restored, preservation, preservation_provenance = None, None, None
        if state["local_epoch"] != core["local_epoch"]:
            with sql.connection(profile) as con:
                rows = con.execute("SELECT operation_id,result_sha256 FROM " + restore_schema.TABLE
                    + " WHERE after_library_epoch=? AND library_id=?", (state["local_epoch"], core["binding"]["home_library_id"])).fetchall()
            if len(rows) != 1:
                _fail("RESTORE_REQUIRED")
            restored = stack.enter_context(runtime.capture_completed_locked(profile=profile, store=store,
                operation_id=rows[0][0], expected_result_sha256=rows[0][1]))
            observation = restored.preservation_observation(from_epoch=core["local_epoch"])
            _same(observation["source_core_sha256"], permanent.digest(core))
            preservation, preservation_provenance = observation["preservation"], restored.provenance_ref()
        owner = stack.enter_context(permanent.owned(store))
        observed = owner.observe()
        entries = observed["entries"]
        origin = _resolve(entries, state["source_authority"]["origin"])
        _same(origin["core"], core)
        selected = entries["controls"].get(control_request_id)
        if selected is None or selected["state"] != "committed":
            _fail("ORIGINAL_CONTROL_REQUIRED")
        control = selected["record"]
        _same(control["core_sha256"], origin["core_sha256"])
        from . import sync_source_legacy_ancestry2 as legacy
        if control_request_id in {ref["record_id"] for ref in legacy.legacy_consumption2(entries, state)["control_refs"]}:
            _fail("CONTROL_ALREADY_CONSUMED")
        old = deepcopy(control["request"])
        for item in entries["requests"].values():
            body = item["record"]["request"]
            if (body["kind"] == "source_dispose" and body["original_request"]["request_id"] == control_request_id
                    and item["record"]["record_id"] != request_id):
                _fail("DISPOSITION_RECONCILIATION_REQUIRED")
        # Original control reconciliation keeps the actual worker/root lease,
        # but does not request fresh paid admission after a user's pause.
        schema = lifecycle17._Schema(profile, store, core, state, stack)
        _same((schema.job["job_id"], schema.job["continuation_id"], schema.job["execution_generation"]),
            (core["root_job_id"], core["continuation_id"], core["execution_generation"]))
        current = _DisposeTransport(client, schema.source, state)
        continuity = current.check(state=state)
        prior = state["source_admission"]
        if prior is None:
            current_claim = deepcopy(origin["claim"])
        else:
            parent = entries["receipts"].get(prior["admission_id"])
            if parent is None or parent["state"] != "committed" or permanent.digest(parent["record"]) != prior["record_sha256"]:
                _fail("ADMISSION_PARENT_REQUIRED")
            current_claim = deepcopy(parent["record"]["result"]["claim"])
        registrations = []
        for item in entries["receipts"].values():
            if item["state"] != "committed":
                continue
            result = item["record"]["result"]
            if (result.get("original_request_id") == control_request_id
                    and "registration_sha256" in result and "disposition" not in result):
                _same(result["original_request_sha256"], permanent.digest(old))
                _same(result["core_sha256"], origin["core_sha256"])
                _same(result["continuity_id"], continuity["continuity_id"])
                registrations.append(deepcopy(result))
        if len(registrations) > 1:
            _fail("REGISTRATION_SELECTOR_REQUIRED")
        request = {"format_version": 2, "kind": "source_dispose", "request_id": request_id,
            "source": deepcopy(schema.source), "target": {"library_id": core["binding"]["target_library_id"], "node_id": core["binding"]["target_node_id"]},
            "lease": deepcopy(state["lease"]), "recovery_generation": state["recovery"]["generation"],
            "recovery_stream": deepcopy(state["recovery"]["stream"]), "control_revision": state["recovery"]["revision"],
            "original": {"core_sha256": permanent.digest(core), "home_epoch": core["local_epoch"],
                "item_id": core["item_id"], "submitted_url_sha256": core["submitted_url_sha256"],
                "claim": deepcopy(origin["claim"]), "recovery_generation": core["recovery_generation"],
                "recovery_stream": deepcopy(core["recovery_stream"])},
            "current_home_epoch": state["local_epoch"], "preservation": deepcopy(preservation),
            "original_request": old, "registration": registrations[0] if registrations else None,
            "continuity": deepcopy(continuity)}
        request = wire.normalize_dispose_request(request)
        basis = {"format_version": 1, "kind": "original_core" if prior is None else "admission",
            "core_sha256": permanent.digest(core), "home_epoch": state["local_epoch"],
            "session": deepcopy(state["lease"]), "claim": current_claim,
            "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
            "control_revision": state["recovery"]["revision"], "origin_ref": permanent.record_ref(origin),
            "admission_ref": deepcopy(prior), "continuity": deepcopy(continuity)}
        record = {"format_version": 2, "record_kind": "recovery_intent", "record_id": request_id,
            "core_sha256": permanent.digest(core), "request_sha256": permanent.digest(request), "request": request,
            "expected_before": timer._before(state, core, current_claim), "creation_basis": basis,
            "preservation_provenance": preservation_provenance, "resume_provenance": None}
        validate_record2("requests", record, observed["contract"])
        existing = entries["requests"].get(request_id)
        if existing is not None:
            _same(existing["record"], record)
        producer = ReadmitIntentProducer2(owner, schema, current, record, state, restored, None, observed, seal=_SEAL)
        token = _PUBLISHER.set(producer)
        try:
            saved = owner.publish("requests", record, producer=producer)
            producer.check(record)
        finally:
            producer._active = False
            _PUBLISHER.reset(token)
        return {"record": deepcopy(saved), "ref": permanent.record_ref(saved)}
