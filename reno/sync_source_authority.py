"""Permanent private Home records, separate from rewindable sync-state images.

This component grants no source, budget, transport or admission permission.
Creation records need a live, sealed producer; recovery receipts preserve facts
under actual StateStore ownership. Existing ledgers are observed regardless of
the state format/pointers. No ordinary read initializes or repairs facilities.
"""
from __future__ import annotations

from collections import namedtuple
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from functools import wraps
import asyncio
import hashlib
import os
import re
import sqlite3
import stat
import threading
import time
import uuid

from . import storage_lifecycle as lifecycle, sync_media as media, sync_state
from . import sync_source_readmit_contract as contract, sync_source_execution as execution
from . import sync_queue as queue, sync_client, sync_access, storage_admin, db
from .sync_protocol import SyncError

DIRECTORY = "sync-source-authority"
VERSION_DIRECTORY = "v1"
INTENT_NAME = ".reno-home-source-authority-v1.intent.json"
SEAL_NAME = ".reno-home-source-authority-v1.seal.json"
DB_HEAD_NAME = ".reno-home-source-authority-v1.head.json"
CHECKPOINT_HEAD_NAME = ".reno-home-source-authority-v1.checkpoint-head.json"
KINDS = ("origins", "controls", "requests", "receipts", "publications")
MAX_RECORDS = 4096
MAX_INTERNAL_PARTS = 32
MAX_RECORD_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEADLINE_SECONDS = 10.0
_CURRENT = ContextVar("reno_home_source_authority_owner", default=None)
_PRODUCER = ContextVar("reno_home_source_authority_producer", default=None)
_HEAD_REPAIR_CONTEXT = ContextVar("reno_home_creation_head_repair_receipt", default=None)
_HEAD_REPAIR_SEAL = object()
_SEAL = object()
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ORDINAL = re.compile(r"[0-9]{8}\.json\Z")
_PART = re.compile(r"\.sync-[0-9a-f]{32}\.part\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.\d{3}Z\Z")
_CONTRACT_FIELDS = frozenset(("format_version", "facility_id", "owner_profile_sha256", "binding", "activated_at"))
_HEAD_FIELDS = frozenset(("format_version", "facility_id", "ordinal", "sha256"))
_JOURNAL_FIELDS = frozenset(("format_version", "facility_id", "ordinal", "previous_sha256", "kind", "key", "record_sha256", "record", "state"))
_REF_FIELDS = frozenset(("format_version", "record_kind", "record_id", "record_sha256"))
_ADMISSION_REF_FIELDS = frozenset(("format_version", "admission_id", "core_sha256", "request_sha256", "admission_sha256", "record_sha256"))
_BASIS_FIELDS = frozenset(("format_version", "kind", "core_sha256", "home_epoch", "session", "claim", "recovery_generation", "recovery_stream", "control_revision", "origin_ref", "admission_ref", "continuity"))
_BEFORE_FIELDS = frozenset(("binding", "local_epoch", "core_sha256", "admission_ref", "recovery_generation", "recovery_stream", "control_revision", "claim", "budget_pending_sha256", "source_control_pending_sha256", "pending_publication", "source_authority"))
_PROJECTION_FIELDS = frozenset(("format_version", "contract", "intent", "seal", "db_head", "checkpoint_head", "journal", "records"))
_PUBLIC_BINDING_FIELDS = ("home_library_id", "home_node_id", "target_library_id", "target_node_id")


def _fail(code="INVALID", status=409):
    raise SyncError("Home source authority records could not be verified", "SYNC_SOURCE_AUTHORITY_" + code, status) from None


@contextmanager
def _errors():
    try:
        yield
    except SyncError as exc:
        if exc.code.startswith("SYNC_SOURCE_AUTHORITY_"):
            raise
        _fail("UNSAFE_PATH" if "UNSAFE_PATH" in exc.code else "INVALID")
    except FileNotFoundError:
        _fail("MISSING")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        owner = args[0] if args and isinstance(args[0], AuthorityGuard) else None
        with _errors():
            if owner is not None:
                if owner.operation_depth == 0:
                    owner.started = time.monotonic()
                    owner.total_bytes = 0
                    owner._verify_ownership()
                owner.operation_depth += 1
            try:
                result = function(*args, **kwargs)
                if owner is not None and owner.operation_depth == 1:
                    owner._verify_ownership()
                return result
            finally:
                if owner is not None:
                    owner.operation_depth -= 1
    return checked


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _version(value):
    if type(value) is not int or value != 1:
        _fail()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail()


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()


def _integer(value, *, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _time(value):
    if type(value) is not str or not _TIME.fullmatch(value):
        _fail()
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def _encoded(value):
    return sync_state._bounded(value, maximum=MAX_RECORD_BYTES, nodes=4096, depth=24)


def digest(value):
    with _errors():
        return hashlib.sha256(_encoded(value)).hexdigest()


_digest = digest


def profile_sha256(profile):
    lifecycle._profile(profile)
    return digest({key: lifecycle._key(getattr(profile, key)) for key in ("database", "media", "reports", "checkpoints")})


def validate_contract(value, *, profile=None):
    """Representation only. Epoch is deliberately absent from facility identity."""
    with _errors():
        _exact(value, _CONTRACT_FIELDS)
        _version(value["format_version"])
        _uuid(value["facility_id"])
        _sha(value["owner_profile_sha256"])
        sync_state._binding(value["binding"])
        _time(value["activated_at"])
        if profile is not None and value["owner_profile_sha256"] != profile_sha256(profile):
            _fail("OWNER_MISMATCH")
        _encoded(value)
        return deepcopy(value)


def _ref(value, *, kind=None):
    _exact(value, _REF_FIELDS)
    _version(value["format_version"])
    _uuid(value["record_id"])
    _sha(value["record_sha256"])
    if value["record_kind"] not in {"origin", "control", "recovery_intent", "recovery_receipt", "publication"} or kind is not None and value["record_kind"] != kind:
        _fail()


def _admission_ref(value):
    _exact(value, _ADMISSION_REF_FIELDS)
    _version(value["format_version"])
    _uuid(value["admission_id"])
    for field in _ADMISSION_REF_FIELDS - {"format_version", "admission_id"}:
        _sha(value[field])


def _continuity(value):
    if value is None:
        return
    _exact(value, ("format_version", "continuity_id", "contract_sha256", "target_epoch"))
    _version(value["format_version"])
    _uuid(value["continuity_id"])
    _sha(value["contract_sha256"])
    _integer(value["target_epoch"], minimum=1)


def _basis(value, core_sha, binding):
    _exact(value, _BASIS_FIELDS)
    _version(value["format_version"])
    if value["kind"] not in {"original_core", "admission"} or value["core_sha256"] != core_sha:
        _fail()
    _integer(value["home_epoch"], minimum=1)
    _integer(value["recovery_generation"])
    _integer(value["control_revision"])
    queue._lease(value["session"])
    sync_state._claim(value["claim"])
    contract._stream(value["recovery_stream"], {"library_id": binding["target_library_id"]})
    _continuity(value["continuity"])
    if value["continuity"] is not None and value["continuity"]["target_epoch"] != value["recovery_stream"]["library_epoch"]:
        _fail("CREATION_MISMATCH")
    if value["origin_ref"] is not None:
        _ref(value["origin_ref"], kind="origin")
    if value["kind"] == "original_core":
        if value["admission_ref"] is not None:
            _fail()
    else:
        _admission_ref(value["admission_ref"])
        if value["admission_ref"]["core_sha256"] != core_sha:
            _fail()


def _before(value, binding, core_sha):
    _exact(value, _BEFORE_FIELDS)
    if value["binding"] != binding or value["core_sha256"] != core_sha:
        _fail("CREATION_MISMATCH")
    _integer(value["local_epoch"], minimum=1)
    _integer(value["recovery_generation"])
    _integer(value["control_revision"])
    contract._stream(value["recovery_stream"], {"library_id": binding["target_library_id"]})
    sync_state._claim(value["claim"])
    if value["admission_ref"] is not None:
        _admission_ref(value["admission_ref"])
    for name in ("budget_pending_sha256", "source_control_pending_sha256"):
        if value[name] is not None:
            _sha(value[name])
    if value["pending_publication"] is not None:
        sync_state._metadata(value["pending_publication"])
    authority = value["source_authority"]
    if authority is not None:
        _exact(authority, ("format_version", "core_sha256", "origin", "defer", "resume", "failure", "publication"))
        _version(authority["format_version"])
        if authority["core_sha256"] != core_sha:
            _fail()
        for name in ("origin", "defer", "resume", "failure", "publication"):
            if authority[name] is not None:
                _ref(authority[name], kind="origin" if name == "origin" else "publication" if name == "publication" else "control")


def _peer(request, binding):
    if any(request["source"][key] != binding["home_" + key] or request["target"][key] != binding["target_" + key] for key in ("library_id", "node_id")):
        _fail("PEER_MISMATCH")


def validate_record(kind, value, activation):
    """Pure closed record validation, never source or continuity authority."""
    with _errors():
        activation = validate_contract(activation)
        return _record(kind, value, activation)


def _record(kind, value, activation):
    if type(value) is dict and type(value.get("format_version")) is int and value["format_version"] == 2:
        from .sync_source_readmit_local2 import validate_record2
        return validate_record2(kind, value, activation)
    binding = activation["binding"]
    fields = {"origins": {"binding", "core", "claim", "core_sha256", "created_at"},
        "controls": {"core_sha256", "request_sha256", "request", "creation_basis"},
        "requests": {"core_sha256", "request_sha256", "request", "expected_before", "preservation_basis", "manual_resume_basis"},
        "receipts": {"request_sha256", "result", "result_sha256", "parent_defer"},
        "publications": {"metadata", "core_sha256", "claims_sha256", "creation_basis"}}
    if kind not in fields:
        _fail()
    tags = dict(zip(KINDS, ("origin", "control", "recovery_intent", "recovery_receipt", "publication")))
    _exact(value, fields[kind] | {"format_version", "record_kind", "record_id"})
    _version(value["format_version"])
    _uuid(value["record_id"])
    if value["record_kind"] != tags[kind]:
        _fail()
    if "core_sha256" in value:
        _sha(value["core_sha256"])
    if kind == "origins":
        if value["binding"] != binding:
            _fail("PEER_MISMATCH")
        core = sync_state._source_execution(value["core"], {"binding": binding, "local_epoch": value["core"].get("local_epoch")})
        if digest(core) != value["core_sha256"] or value["record_id"] != str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + value["core_sha256"])):
            _fail()
        sync_state._claim(value["claim"])
        if value["claim"]["item_id"] != core["item_id"]:
            _fail()
        _time(value["created_at"])
    elif kind == "controls":
        request = contract._old_request(value["request"])
        _peer(request, binding)
        _basis(value["creation_basis"], value["core_sha256"], binding)
        basis = value["creation_basis"]
        if request["source"]["handshake"]["migration_version"] >= 17 and basis["continuity"] is None:
            _fail("CONTINUITY_REQUIRED")
        if (request["request_id"] != value["record_id"] or contract.digest(request) != value["request_sha256"]
                or request["lease"] != basis["session"] or request["recovery_generation"] != basis["recovery_generation"]
                or request["recovery_stream"] != basis["recovery_stream"]
                or "claim" in request and request["claim"] != basis["claim"]
                or "core_sha256" in request and request["core_sha256"] != value["core_sha256"]):
            _fail("CREATION_MISMATCH")
    elif kind == "requests":
        request = contract.normalize_request(value["request"])
        # The local record v1 has only preservation/manual provenance. A new
        # readmit wire must wait for its own explicit permanent-record branch.
        if request.get("format_version", 1) != 1:
            _fail()
        _peer(request, binding)
        if request["request_id"] != value["record_id"] or contract.request_sha256(request) != value["request_sha256"]:
            _fail()
        request_core = request.get("core_sha256", request.get("original", {}).get("core_sha256"))
        if request_core != value["core_sha256"]:
            _fail()
        _before(value["expected_before"], binding, value["core_sha256"])
        # Local provenance is recorded exactly; the effect consumer must resolve
        # its actual producer. A caller dictionary never becomes a sealed proof.
        for name, producer in (("preservation_basis", "home_knowledge_restore"), ("manual_resume_basis", "sync_budget_resume")):
            if value[name] is not None:
                _exact(value[name], ("format_version", "producer", "operation_id", "receipt_sha256"))
                _version(value[name]["format_version"])
                if value[name]["producer"] != producer:
                    _fail()
                _uuid(value[name]["operation_id"])
                _sha(value[name]["receipt_sha256"])
    elif kind == "receipts":
        _sha(value["request_sha256"])
        _sha(value["result_sha256"])
        result = value["result"]
        if type(result) is not dict or result.get("replayed") is not False or digest(result) != value["result_sha256"]:
            _fail()
        if result.get("request_id", result.get("admission_id")) != value["record_id"] or result.get("request_sha256") != value["request_sha256"]:
            _fail()
        parent = value["parent_defer"]
        if result.get("original_kind") == "budget_resume" and result.get("disposition") == "applied":
            _exact(parent, ("request", "receipt"))
            old = contract._old_request(parent["request"])
            _peer(old, binding)
            if old["kind"] != "budget_defer" or parent["receipt"].get("replayed") is not False:
                _fail()
            sync_client.normalize_budget_receipt(old, parent["receipt"])
        elif parent is not None:
            _fail()
    else:
        sync_state._metadata(value["metadata"])
        if value["metadata"]["publication_id"] != value["record_id"]:
            _fail()
        _sha(value["claims_sha256"])
        _basis(value["creation_basis"], value["core_sha256"], binding)
    _encoded(value)
    return deepcopy(value)


def record_ref(record):
    with _errors():
        return {"format_version": 1, "record_kind": record["record_kind"], "record_id": record["record_id"], "record_sha256": digest(record)}


def _resolve_record(entries, ref):
    _ref(ref)
    folders = {"origin": "origins", "control": "controls", "recovery_intent": "requests", "recovery_receipt": "receipts", "publication": "publications"}
    item = entries[folders[ref["record_kind"]]].get(ref["record_id"])
    if item is None or item["state"] != "committed" or digest(item["record"]) != ref["record_sha256"]:
        _fail("REFERENCE_MISSING")
    return item["record"]

def _record_relations(entries, *, check=None):
    check = check or (lambda: None)
    outcomes = {}
    admissions = {}
    originals = {}
    for folder in ("controls", "requests"):
        check()
        for item in entries[folder].values():
            check()
            request = item["record"]["request"]
            old = request if folder == "controls" else request.get("original_request")
            if old is None:
                continue
            key, sha = old["request_id"], contract.digest(old)
            if key in originals and originals[key] != sha:
                _fail("REQUEST_MISMATCH")
            originals[key] = sha
    for folder in ("controls", "publications"):
        check()
        for item in entries[folder].values():
            check()
            value = item["record"]
            basis = value["creation_basis"]
            if basis["origin_ref"] is not None:
                origin = _resolve_record(entries, basis["origin_ref"])
                if origin["core_sha256"] != value["core_sha256"]:
                    _fail("CREATION_MISMATCH")
            if basis["admission_ref"] is not None:
                ref = basis["admission_ref"]
                receipt = entries["receipts"].get(ref["admission_id"])
                if receipt is None or receipt["state"] != "committed" or digest(receipt["record"]) != ref["record_sha256"]:
                    _fail("REFERENCE_MISSING")
                result = receipt["record"]["result"]
                if any(result.get(key) != ref[key] for key in ("admission_id", "core_sha256", "request_sha256", "admission_sha256")):
                    _fail("CREATION_MISMATCH")
                if any(result[key] != basis[key] for key in ("session", "claim", "recovery_generation", "recovery_stream", "control_revision")) or result["current_home_epoch"] != basis["home_epoch"]:
                    _fail("CREATION_MISMATCH")
    for item in entries["receipts"].values():
        check()
        value = item["record"]
        if value["format_version"] == 2:
            continue  # Explicit new direct/timer/readmit closure follows below.
        intent = entries["requests"].get(value["record_id"])
        if intent is None or intent["state"] != "committed" or intent["record"]["request_sha256"] != value["request_sha256"]:
            _fail("INTENT_REQUIRED")
        request = intent["record"]["request"]
        parent = value["parent_defer"]
        if parent is not None and parent["request"]["request_id"] != request["original_request"]["defer_request_id"]:
            _fail("CREATION_MISMATCH")
        normalized = contract.normalize_result(request, value["result"], defer_receipt=None if parent is None else parent["receipt"])
        if normalized != value["result"]:
            _fail("RECEIPT_MISMATCH")
        if request["kind"] == "source_dispose":
            old_id = normalized["original_request_id"]
            disposition = normalized["disposition"]
            if old_id in outcomes and outcomes[old_id] != disposition:
                _fail("OUTCOME_MISMATCH")
            outcomes[old_id] = disposition
        elif request["kind"] == "source_readmit":
            previous = normalized["previous_admission_id"]
            if previous is not None:
                before = entries["receipts"].get(previous)
                if before is None or before["state"] != "committed" or before["record"]["result"].get("core_sha256") != normalized["core_sha256"]:
                    _fail("ADMISSION_PARENT_REQUIRED")
                parent_result = before["record"]["result"]
                if parent_result.get("admission_id") != previous or request["expected_claim"] != parent_result["claim"]:
                    _fail("CREATION_MISMATCH")
            branch = (normalized["core_sha256"], previous)
            if branch in admissions and admissions[branch] != normalized["admission_id"]:
                _fail("ADMISSION_BRANCH")
            admissions[branch] = normalized["admission_id"]
    if any(item["record"]["format_version"] == 2 for folder in entries.values() for item in folder.values()):
        from .sync_source_readmit_local2 import record_relations2
        record_relations2(entries, check=check)

def _public_result_shape(value, activation):
    """Validate standalone result types; original request binding is separate."""
    if "admission_id" not in value:
        if "disposition" in value:
            contract._dispose_shape(value)
        else:
            contract._registration_shape(value)
        return
    _exact(value, contract.READMIT_RESULT_FIELDS)
    _version(value["format_version"])
    for name in ("admission_id", "previous_admission_id"):
        if value[name] is not None:
            _uuid(value[name])
    for name in ("request_sha256", "admission_sha256", "core_sha256", "submitted_url_sha256"):
        _sha(value[name])
    for name, prefix in (("source_identity", "home_"), ("target_identity", "target_")):
        contract._identity(value[name])
        if any(value[name][key] != activation["binding"][prefix + key] for key in ("library_id", "node_id")):
            _fail("PEER_MISMATCH")
    queue._lease(value["session"])
    sync_state._claim(value["claim"])
    for name in ("item_id", "current_home_epoch"):
        _integer(value[name], minimum=1)
    for name in ("recovery_generation", "control_revision"):
        _integer(value[name])
    contract._stream(value["recovery_stream"], value["target_identity"])
    preservation = value["preservation"]
    _exact(preservation, contract._PRESERVATION)
    _version(preservation["format_version"])
    _sha(preservation["frontier_sha256"])
    _sha(preservation["certificate_sha256"])
    _integer(preservation["from_epoch"], minimum=1)
    _integer(preservation["to_epoch"], minimum=preservation["from_epoch"])
    if preservation["to_epoch"] != value["current_home_epoch"] or value["claim"]["item_id"] != value["item_id"]:
        _fail()
    _integer(value["attempts"], minimum=1, maximum=queue.MAX_ATTEMPTS)
    if value["attempt_basis"] not in {"held_claim", "original_defer_refund"}:
        _fail()
    _time(value["committed_at"])
    if type(value["disposition_sha256s"]) is not list or len(value["disposition_sha256s"]) > 2:
        _fail()
    for sha in value["disposition_sha256s"]:
        _sha(sha)
    if contract.admission_sha256(value) != value["admission_sha256"]:
        _fail()


def _projection_dependencies(entries, check):
    """Resolve local before-image refs; remote/SQL refs remain observations."""
    for item in entries["requests"].values():
        check()
        value = item["record"]
        before = value["expected_before"]
        authority = before["source_authority"]
        if authority is not None:
            for name in ("origin", "defer", "resume", "failure", "publication"):
                ref = authority[name]
                if ref is not None:
                    record = _resolve_record(entries, ref)
                    if record["core_sha256"] != value["core_sha256"]:
                        _fail("CREATION_MISMATCH")
        ref = before["admission_ref"]
        if ref is not None:
            record = entries["receipts"].get(ref["admission_id"])
            if (record is None or record["state"] != "committed"
                    or digest(record["record"]) != ref["record_sha256"]):
                _fail("REFERENCE_MISSING")
            result = record["record"]["result"]
            if (ref["core_sha256"] != value["core_sha256"]
                    or any(result.get(name) != ref[name] for name in
                        ("admission_id", "core_sha256", "request_sha256", "admission_sha256"))):
                _fail("CREATION_MISMATCH")


def _public_refs(kind, record):
    """Closed tagged edges. Never return leases, claims, bodies, URLs or paths."""
    if record["format_version"] == 2:
        from .sync_source_readmit_local2 import public_refs2
        return public_refs2(kind, record)
    refs = []

    def add(tag, value):
        if value is not None:
            refs.append({"kind": tag, **deepcopy(value)})

    def authority_ref(value):
        add("authority_record", value)

    def admission_ref(value):
        add("admission", value)

    def original_control(request, core_sha):
        claim = request.get("claim")
        add("original_control", {"request_id": request["request_id"], "request_sha256": contract.digest(request),
            "original_kind": request["kind"], "core_sha256": core_sha,
            "item_id": None if claim is None else claim["item_id"],
            "lease_generation": None if claim is None else claim["lease_generation"],
            "budget_operation_id": request.get("budget_operation_id"), "defer_request_id": request.get("defer_request_id")})
        if request["kind"] == "source_fail":
            failure = request["failure"]
            add("source_failure", {name: failure[name] for name in ("format_version", "operation_id", "attempt_id",
                "input_sha256", "request_sha256", "settlement_sha256", "result_sha256")})

    def old_receipt(value, original_kind):
        if value is not None:
            add("control_receipt", {"original_kind": original_kind, **{name: value[name]
                for name in ("request_id", "request_sha256", "item_id", "lease_generation")},
                "budget_operation_id": value.get("budget_operation_id"), "core_sha256": value.get("core_sha256"),
                "failure_sha256": value.get("failure_sha256")})

    def remote_result(value):
        if value is None:
            return
        if "admission_id" in value:
            add("admission", {"format_version": 1, **{name: value[name] for name in
                ("admission_id", "core_sha256", "request_sha256", "admission_sha256")}, "record_sha256": digest(record)})
            if value["previous_admission_id"] is not None:
                add("admission_parent", {"admission_id": value["previous_admission_id"], "core_sha256": value["core_sha256"]})
            add("preservation", value["preservation"])
        else:
            is_disposition = "disposition" in value
            add("remote_disposition" if is_disposition else "remote_registration", {name: value[name] for name in
                ("format_version", "request_id", "request_sha256", "original_request_id", "original_request_sha256",
                 "original_kind", "core_sha256", "item_id", "continuity_id", "registration_sha256")})
            if is_disposition:
                old_receipt(value["original_receipt"], value["original_kind"])

    def basis(value):
        authority_ref(value["origin_ref"])
        admission_ref(value["admission_ref"])
        add("continuity", value["continuity"])

    if kind == "origins":
        core = record["core"]
        add("source_core", {name: core[name] for name in ("format_version", "item_id", "submitted_url_sha256",
            "root_job_id", "continuation_id", "execution_generation", "local_epoch", "resolution_input_sha256")})
    elif kind in {"controls", "publications"}:
        basis(record["creation_basis"])
        if kind == "controls":
            original_control(record["request"], record["core_sha256"])
    elif kind == "requests":
        request = record["request"]
        add("recovery_request", {"request_id": record["record_id"], "request_sha256": record["request_sha256"],
            "request_kind": request["kind"], "core_sha256": record["core_sha256"]})
        before = record["expected_before"]
        admission_ref(before["admission_ref"])
        if before["source_authority"] is not None:
            for name in ("origin", "defer", "resume", "failure", "publication"):
                authority_ref(before["source_authority"][name])
        add("publication", before["pending_publication"])
        for name in ("preservation_basis", "manual_resume_basis"):
            add("provenance", record[name])
        if "original_request" in request:
            original_control(request["original_request"], record["core_sha256"])
        if request["kind"] == "source_register":
            add("continuity", request["continuity"])
        else:
            add("preservation", request["preservation"])
        if request["kind"] == "source_dispose":
            remote_result(request["registration"])
        elif request["kind"] == "source_readmit":
            if request["previous_admission_id"] is not None:
                add("admission_parent", {"admission_id": request["previous_admission_id"], "core_sha256": record["core_sha256"]})
            for value in request["dispositions"]:
                remote_result(value)
            for name, old_kind in (("defer_receipt", "budget_defer"), ("resume_receipt", "budget_resume")):
                old_receipt(request[name], old_kind)
            add("manual_resume", request["manual_resume_basis"])
    else:
        # Same-ID intent is a permanent edge; its SHA is bound in the journal
        # closure, while a single-item observation can only retain this ID/SHA.
        add("recovery_intent", {"record_id": record["record_id"], "request_sha256": record["request_sha256"]})
        remote_result(record["result"])
        if record["parent_defer"] is not None:
            original_control(record["parent_defer"]["request"], record["result"]["core_sha256"])
            old_receipt(record["parent_defer"]["receipt"], "budget_defer")
    unique = {}
    for ref in refs:
        unique.setdefault(_encoded(ref), ref)
    return list(unique.values())


def public_record_projection(kind, record, *, state, activation):
    """Typed public edges only; this does not prove a whole journal is closed.

    The physical reader must keep scoped unknown when its full capture fails.
    This detached DTO grants no creation, admission, dispatch or repair right.
    """
    with _errors():
        if state not in {"reserved", "committed"}:
            _fail()
        activation = validate_contract(activation)
        record = _record(kind, record, activation)
        if kind == "receipts" and record["format_version"] == 1:
            _public_result_shape(record["result"], activation)
        return {"kind": kind, "record_id": record["record_id"], "record_sha256": digest(record),
            "core_sha256": record.get("core_sha256", record.get("result", {}).get("core_sha256")),
            "state": state, "refs": _public_refs(kind, record),
            "publication_metadata": deepcopy(record["metadata"]) if kind == "publications" else None}


def pure_projection(payload, *, binding, owner_profile_sha256):
    """Bounded decoded closure, with no I/O, locks, StateStore or repair.

    Byte canonicality and physical filename closure belong to the read-only
    capture. Every decoded object here is canonically encoded with fixed limits,
    and anchors, journal order, records and committed dependencies must agree.
    An exact present reserved file is structurally projected, not terminally
    complete: readers must retain its refs and a scoped unknown, never treat
    this representation as a healthy facility or executable source authority.
    """
    with _errors():
        started, total = time.monotonic(), 0

        def check(value=None):
            nonlocal total
            if time.monotonic() - started > DEADLINE_SECONDS:
                _fail("DEADLINE_LIMIT")
            if value is not None:
                total += len(_encoded(value))
                if total > MAX_TOTAL_BYTES:
                    _fail("BYTES_LIMIT")

        _exact(payload, _PROJECTION_FIELDS)
        _version(payload["format_version"])
        binding = sync_state._binding(binding)
        _sha(owner_profile_sha256)
        activation = validate_contract(payload["contract"])
        if activation["binding"] != binding:
            _fail("PEER_MISMATCH")
        if activation["owner_profile_sha256"] != owner_profile_sha256:
            _fail("OWNER_MISMATCH")
        for name in ("contract", "intent", "seal"):
            if validate_contract(payload[name]) != activation:
                _fail("CONTRACT_MISMATCH")
            check(payload[name])
        for name in ("db_head", "checkpoint_head"):
            head = payload[name]
            _exact(head, _HEAD_FIELDS)
            _version(head["format_version"])
            _integer(head["ordinal"], maximum=MAX_RECORDS * 2)
            _sha(head["sha256"])
            if head["facility_id"] != activation["facility_id"]:
                _fail("HEAD_INVALID")
            check(head)
        journal = payload["journal"]
        if type(journal) is not list or len(journal) > MAX_RECORDS * 2:
            _fail("COUNT_LIMIT")
        _exact(payload["records"], KINDS)
        if any(type(values) is not dict for values in payload["records"].values()):
            _fail()
        if sum(len(values) for values in payload["records"].values()) > MAX_RECORDS:
            _fail("COUNT_LIMIT")
        entries = {kind: {} for kind in KINDS}
        previous_sha, count = digest(activation), 0
        for ordinal, event in enumerate(journal, 1):
            check(event)
            _exact(event, _JOURNAL_FIELDS)
            _version(event["format_version"])
            if (type(event["ordinal"]) is not int or event["ordinal"] != ordinal
                    or event["facility_id"] != activation["facility_id"] or event["previous_sha256"] != previous_sha
                    or event["kind"] not in KINDS or event["state"] not in {"reserved", "committed"}):
                _fail("JOURNAL_INVALID")
            _uuid(event["key"])
            record = _record(event["kind"], event["record"], activation)
            if record["record_id"] != event["key"] or digest(record) != event["record_sha256"]:
                _fail("JOURNAL_INVALID")
            previous = entries[event["kind"]].get(event["key"])
            if previous is None:
                if event["state"] != "reserved":
                    _fail("JOURNAL_INVALID")
                count += 1
                if count > MAX_RECORDS:
                    _fail("COUNT_LIMIT")
            elif previous["state"] != "reserved" or event["state"] != "committed" or previous["record"] != record:
                _fail("JOURNAL_INVALID")
            entries[event["kind"]][event["key"]] = {"state": event["state"], "record": record}
            previous_sha = digest(event)
        latest = {"format_version": 1, "facility_id": activation["facility_id"], "ordinal": len(journal), "sha256": previous_sha}
        if payload["db_head"] != latest or payload["checkpoint_head"] != latest:
            _fail("HEAD_INCOMPLETE")
        rows = []
        for kind in KINDS:
            actual = payload["records"][kind]
            if set(actual) != set(entries[kind]):
                _fail("RECORD_MISSING" if set(entries[kind]) - set(actual) else "UNEXPECTED_FILE")
            for key in sorted(actual):
                check(actual[key])
                item = entries[kind][key]
                if actual[key] != item["record"]:
                    _fail("RECORD_INVALID")
                rows.append(public_record_projection(kind, actual[key], state=item["state"], activation=activation))
        _record_relations(entries, check=check)
        _projection_dependencies(entries, check)
        check()
        return {"format_version": 1, "facility_id": activation["facility_id"],
            "binding": {key: binding[key] for key in _PUBLIC_BINDING_FIELDS},
            "owner_profile_sha256": owner_profile_sha256, "head_sha256": previous_sha, "records": rows}


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


_OBSERVATION_CURRENT = ContextVar("reno_authority_directory_observation", default=None)
_OBSERVATION_CONTEXT = ContextVar("reno_authority_directory_observation_context", default=None)
_OBSERVATION_LOCK = threading.RLock()
_OBSERVATION_SCOPES = {}
_DirectoryAnchor = namedtuple("_DirectoryAnchor", (
    "directory", "path", "base", "parent", "create", "handles", "identities", "fd", "entered"))
_ObservationBinding = namedtuple("_ObservationBinding", (
    "owner", "lock", "store", "profile", "storage_guard", "state_guard", "state_directory",
    "reference_frame", "root", "checkpoint_root", "stack", "actor", "started", "depth",
    "known", "captured", "phase", "tail_completed", "current_token", "context_token",
    "store_profile", "profile_identity", "storage_locks", "storage_profiles", "storage_ids",
    "namespaces", "directory_index", "pins", "head_pins", "namespace_items", "directory_items"))


def _observation_actor():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _directory_anchor(directory):
    if type(directory) is not media._Directory:
        _fail("READ_SCOPE_REQUIRED")
    return _DirectoryAnchor(directory, directory.path, directory.base, directory.parent,
        directory.create, tuple(directory.handles), tuple(directory.identities), directory.fd, True)


def _directory_anchors(owner):
    directories = [owner.root, owner.checkpoint_root,
        *(item for item in owner.directory_index.values()),
        *(item[0] for item in owner.namespaces.values()),
        *(item[0] for item in owner.pins), *(item[0] for item in owner.head_pins.values())]
    result, seen = [], set()
    for directory in directories:
        while directory is not None and id(directory) not in seen:
            seen.add(id(directory))
            result.append(_directory_anchor(directory))
            directory = directory.parent
    return tuple(result)


class _ObservationFailures:
    def __init__(self, first=None):
        self.first, self.notes = first, 0

    def capture(self, error):
        if self.first is None:
            self.first = error, BaseException.__dict__["__traceback__"].__get__(error, type(error))
        elif error is not self.first[0] and self.notes < 4:
            self.notes += 1
            try:
                BaseException.add_note(self.first[0],
                    "Authority observation cleanup also failed: " + type(error).__name__)
            except BaseException:
                pass

    def run(self, function, *args):
        try:
            return function(*args)
        except BaseException as error:
            self.capture(error)


class _AuthorityDirectoryObservation:
    """One closed readonly traversal, retaining the original real resource owner.

    Registry anchors contain exact live objects and tokens, never a successful
    verification result. Newly opened directories are retained only for final
    checking; they cannot join the initial set eligible for cheap checks.
    """
    __slots__ = ()

    def __init__(self, owner):
        if (type(owner) is not AuthorityGuard or _CURRENT.get() is not owner
                or _OBSERVATION_CURRENT.get() is not None or owner.operation_depth < 1):
            _fail("READ_SCOPE_REQUIRED")
        owner._check()
        lock, store = owner.lock, owner.lock.store
        known = _directory_anchors(owner)
        binding = _ObservationBinding(owner, lock, store, owner.profile, lock.storage_guard,
            store._guard, store._directory, store._reference_frame, owner.root,
            owner.checkpoint_root, owner.stack, _observation_actor(), owner.started,
            owner.operation_depth, known, known, "opening", False, None, None,
            store.profile, owner.profile.identity, lock.storage_guard._locks,
            lock.storage_guard._profiles, lock.storage_guard._profile_ids,
            owner.namespaces, owner.directory_index, owner.pins, owner.head_pins,
            tuple(owner.namespaces.items()), tuple(owner.directory_index.items()))
        with _OBSERVATION_LOCK:
            _OBSERVATION_SCOPES[self] = binding

    def _binding(self):
        with _OBSERVATION_LOCK:
            binding = _OBSERVATION_SCOPES.get(self)
        if type(self) is not _AuthorityDirectoryObservation or binding is None:
            _fail("READ_SCOPE_REQUIRED")
        return binding

    def _replace(self, binding, **changes):
        with _OBSERVATION_LOCK:
            if _OBSERVATION_SCOPES.get(self) is not binding:
                _fail("READ_SCOPE_REQUIRED")
            updated = binding._replace(**changes)
            _OBSERVATION_SCOPES[self] = updated
        return updated

    def _context(self):
        binding = self._binding()
        owner, lock, store = binding.owner, binding.lock, binding.store
        if (_OBSERVATION_CURRENT.get() is not self or _OBSERVATION_CONTEXT.get() is not self
                or _CURRENT.get() is not owner or _observation_actor() != binding.actor
                or owner.lock is not lock or lock.store is not store
                or owner.profile is not binding.profile or owner.root is not binding.root
                or owner.checkpoint_root is not binding.checkpoint_root or owner.stack is not binding.stack
                or lock.storage_guard is not binding.storage_guard
                or store._guard is not binding.state_guard or store._directory is not binding.state_directory
                or store._reference_frame is not binding.reference_frame
                or store.profile is not binding.store_profile or owner.profile.identity != binding.profile_identity
                or binding.storage_guard._locks is not binding.storage_locks
                or binding.storage_guard._profiles is not binding.storage_profiles
                or binding.storage_guard._profile_ids is not binding.storage_ids
                or owner.namespaces is not binding.namespaces or owner.directory_index is not binding.directory_index
                or owner.pins is not binding.pins or owner.head_pins is not binding.head_pins
                or owner.started != binding.started
                or not binding.depth <= owner.operation_depth <= binding.depth + 1):
            _fail("READ_SCOPE_REQUIRED")
        for anchor in binding.known:
            if _directory_anchor(anchor.directory) != anchor:
                _fail("READ_SCOPE_CHANGED")
        for path, expected in binding.namespace_items:
            current = owner.namespaces.get(path)
            if (type(current) is not tuple or len(current) != 2 or current[0] is not expected[0]
                    or type(current[1]) is not frozenset or current[1] != expected[1]):
                _fail("READ_SCOPE_CHANGED")
        for key, directory in binding.directory_items:
            if owner.directory_index.get(key) is not directory:
                _fail("READ_SCOPE_CHANGED")
        # copy_context() copies values, but cannot reset this original token.
        try:
            _OBSERVATION_CONTEXT.reset(binding.context_token)
        except (ValueError, RuntimeError):
            _fail("READ_SCOPE_REQUIRED")
        return self._replace(binding, context_token=_OBSERVATION_CONTEXT.set(self))

    def check(self):
        binding = self._context()
        binding.owner._check()  # Original State/SH/context and original ten-second clock.
        return binding

    def enter(self):
        binding = self._binding()
        binding = self._replace(binding, current_token=_OBSERVATION_CURRENT.set(self))
        binding = self._replace(binding, context_token=_OBSERVATION_CONTEXT.set(self))
        binding.owner._verify_ownership()

    def activate(self, owner):
        binding = self.check()
        if binding.owner is not owner or binding.phase != "opening":
            _fail("READ_SCOPE_REQUIRED")
        self._replace(binding, phase="reading")

    def verify_directory(self, owner, directory):
        binding = self.check()
        if binding.owner is not owner:
            _fail("READ_SCOPE_REQUIRED")
        anchor = next((item for item in binding.known if item.directory is directory), None)
        if binding.phase == "reading" and anchor is not None:
            return
        directory.verify()

    def note_directory(self, owner, directory, entered):
        binding = self.check()
        if binding.owner is not owner or type(directory) is not media._Directory:
            _fail("READ_SCOPE_REQUIRED")
        if any(item.directory is directory for item in binding.known):
            _fail("READ_SCOPE_REQUIRED")
        value = _directory_anchor(directory) if entered else _DirectoryAnchor(
            directory, directory.path, directory.base, directory.parent, directory.create, (), (), None, False)
        found = any(item.directory is directory for item in binding.captured)
        captured = tuple(value if item.directory is directory else item for item in binding.captured)
        if not found:
            captured += (value,)
        self._replace(binding, captured=captured)

    def mark_tail(self, owner):
        binding = self.check()
        if binding.owner is not owner or binding.phase != "reading":
            _fail("READ_SCOPE_REQUIRED")
        # Local control-flow coverage only: original verify already scanned the
        # complete namespace and checked every pinned file/FD/SHA and head.
        # Every actual directory and lock is still freshly checked at exit.
        self._replace(binding, tail_completed=True)

    @contextmanager
    def suspended(self, owner):
        binding = self.check()
        if binding.owner is not owner:
            _fail("READ_SCOPE_REQUIRED")
        previous = binding.phase
        self._replace(binding, phase="suspended")
        try:
            yield
        finally:
            binding = self._binding()
            self._replace(binding, phase=previous)

    def finish(self, first):
        failures = _ObservationFailures(first)
        binding = self._binding()
        failures.run(self._context)
        binding = self._binding()
        binding = self._replace(binding, phase="closing")
        owner = binding.owner
        failures.run(owner._check)
        # Try physical checks even when the deadline/context check just failed;
        # these are cleanup observations, never a new writer/effect permission.
        failures.run(binding.lock.verify)
        failures.run(binding.checkpoint_root.verify)
        for anchor in binding.captured:
            failures.run(anchor.directory.verify)
            if not anchor.entered and first is None:
                try:
                    _fail("READ_SCOPE_REQUIRED")
                except BaseException as error:
                    failures.capture(error)
        if first is not None or not binding.tail_completed:
            failures.run(owner._verify_names)
        failures.run(owner._check)
        if binding.context_token is not None:
            failures.run(_OBSERVATION_CONTEXT.reset, self._binding().context_token)
        if binding.current_token is not None:
            failures.run(_OBSERVATION_CURRENT.reset, binding.current_token)
        with _OBSERVATION_LOCK:
            _OBSERVATION_SCOPES.pop(self, None)
        return failures.first


def _observation_scope_for(owner):
    scope = _OBSERVATION_CURRENT.get()
    if scope is None:
        return None
    if type(scope) is not _AuthorityDirectoryObservation or scope.check().owner is not owner:
        _fail("READ_SCOPE_REQUIRED")
    return scope


def _observation_scope_raw():
    scope = _OBSERVATION_CURRENT.get()
    if scope is not None and type(scope) is not _AuthorityDirectoryObservation:
        _fail("READ_SCOPE_REQUIRED")
    return scope


@contextmanager
def _authority_directory_observation(owner):
    scope = _AuthorityDirectoryObservation(owner)
    first = None
    try:
        scope.enter()
        yield
    except BaseException as error:
        first = error, BaseException.__dict__["__traceback__"].__get__(error, type(error))
    finally:
        first = scope.finish(first)
    if first is not None:
        raise BaseException.with_traceback(first[0], first[1])


def _observation_eligible(owner, resume, resume_origin, readonly):
    from .budget_writer_witness import in_write_window
    if (resume is not None or resume_origin is not None or type(readonly) is not bool
            or type(owner) is not AuthorityGuard or owner.operation_depth < 1
            or _OBSERVATION_CURRENT.get() is not None or in_write_window()):
        return False
    try:
        if (type(owner.lock) is not _Ownership or type(owner.profile) is not lifecycle.StorageProfile
                or type(owner.lock.store) is not sync_state.StateStore
                or type(owner.lock.storage_guard) is not lifecycle.StorageGuard
                or type(owner.lock.store._directory) is not media._Directory):
            return False
        _directory_anchors(owner)
    except (SyncError, TypeError, AttributeError):
        # This is only optimization admission. The full original reader owns
        # the existing error contract for unknown resource implementations.
        return False
    return True

class _Ownership:
    def __init__(self, store):
        if (not store._guard._is_owned() or store._reference_depth < 1
                or store._directory is None or store._reference_frame is None):
            _fail("LOCK_REQUIRED")
        self.store = store
        self.thread = threading.get_ident()
        self.held = True
        self.directory = store._directory
        self.state_guard = store._guard
        self.reference_frame = store._reference_frame
        self.storage_guard = lifecycle.require_guard(store.profile)
        self.profile_identity = store.profile.identity

    def check(self):
        store = self.store
        if (type(store) is not sync_state.StateStore or threading.get_ident() != self.thread
                or not self.held or store._guard is not self.state_guard
                or not self.state_guard._is_owned() or store._reference_depth < 1
                or store._directory is not self.directory or self.directory is None
                or self.reference_frame is None or store._reference_frame is not self.reference_frame):
            _fail("LOCK_REQUIRED")
        guard = self.storage_guard
        if (type(guard) is not lifecycle.StorageGuard or lifecycle._CURRENT.get() is not guard
                or not guard._active or guard._owner != lifecycle._owner()
                or store.profile.identity != self.profile_identity
                or self.profile_identity not in guard._profile_ids):
            _fail("LOCK_REQUIRED")

    def verify(self):
        self.check()
        store = self.store
        if lifecycle._key(store.data_dir) != lifecycle._key(store.profile.database.parent):
            _fail("OWNER_MISMATCH")
        if lifecycle.require_guard(store.profile) is not self.storage_guard:
            _fail("LOCK_REQUIRED")
        store._directory.verify()



def _names(directory, allowed, maximum, check, *, verify_directory=None):
    ordinary_count = part_count = 0
    captured = set()
    with os.scandir(directory.fd if os.name != "nt" else media._native_path(directory.path)) as entries:
        for entry in entries:
            check()
            info = directory.entry_stat(entry.name)
            captured.add(entry.name)
            media._safe_stat(info, directory=entry.name in {"journal", VERSION_DIRECTORY, *KINDS})
            if _PART.fullmatch(entry.name):
                # A killed atomic publisher may leave its unpublished private
                # part. Keep it, bound it, and never treat it as a reservation
                # or effect. Published journal/file loss remains fail-closed.
                part_count += 1
                if part_count > MAX_INTERNAL_PARTS:
                    _fail("COUNT_LIMIT")
                if info.st_nlink != 1 or (os.name != "nt" and
                        (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
                    _fail("UNSAFE_PATH")
                if info.st_size > MAX_RECORD_BYTES:
                    _fail("BYTES_LIMIT")
                continue
            if not allowed(entry.name):
                _fail("UNEXPECTED_FILE")
            ordinary_count += 1
            if ordinary_count > maximum:
                _fail("COUNT_LIMIT")
    if verify_directory is None:
        directory.verify()
    else:
        verify_directory(directory)
    return frozenset(captured)


class AuthorityGuard:
    """A live lock/handle owner, not a caller-supplied admission certificate."""
    def __init__(self, profile, root, checkpoint_root, lock, stack):
        self.profile, self.root, self.checkpoint_root = profile, root, checkpoint_root
        self.lock, self.stack = lock, stack
        self.thread = threading.get_ident()
        self.started = time.monotonic()
        self.total_bytes = 0
        self.pins = []
        self.pin_index = {}
        self.head_pins = {}
        self.allow_initialize = False
        self.operation_depth = 0
        self.namespaces = {}
        self.absent_paths = None
        self.directory_index = {}
        self._head_repair_receipt = None

    def _verify_directory(self, directory):
        scope = _observation_scope_raw()
        if scope is None or scope._binding().phase != "reading":
            directory.verify()
        else:
            scope.verify_directory(self, directory)

    def _open_directory(self, path, *, parent, create):
        directory = media._Directory(path, parent=parent, create=create)
        scope = _observation_scope_raw()
        if scope is not None:
            scope.note_directory(self, directory, False)
        result = self.stack.enter_context(directory)
        if scope is not None:
            if result is not directory:
                _fail("READ_SCOPE_REQUIRED")
            scope.note_directory(self, result, True)
        return result

    def _check(self):
        if _CURRENT.get() is not self or threading.get_ident() != self.thread or not self.lock.held:
            _fail("LOCK_REQUIRED")
        self.lock.check()
        if time.monotonic() - self.started > DEADLINE_SECONDS:
            _fail("DEADLINE_LIMIT")

    def _verify_ownership(self):
        # Each item keeps the exact live ownership/deadline check. The same
        # pinned SH marker and ancestors are fully checked at operation edges,
        # complete observations and before publication, rather than twice for
        # every name, file read and byte in the same bounded closure.
        self._check()
        self.lock.verify()
        self.checkpoint_root.verify()
        self._check()

    def _write(self, directory, name, raw, *, immutable=False):
        self._verify_ownership()
        sync_state._write(directory, name, raw, immutable=immutable)
        self._check()

    def _read(self, directory, name, *, pin=True):
        self._check()
        key = (directory.path, name)
        existing = self.pin_index.get(key) if pin else None
        if existing is not None:
            _, _, stream, signature, path_signature, _ = existing
            if (_signature(directory.entry_stat(name)) != path_signature
                    or _signature(os.fstat(stream.fileno())) != signature):
                _fail("CHANGED")
        else:
            stream = directory.open_file(name)
            if pin:
                self.stack.enter_context(stream)
        try:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_RECORD_BYTES or (os.name != "nt" and before.st_mode & 0o077):
                _fail("BYTES_LIMIT" if before.st_size > MAX_RECORD_BYTES else "UNSAFE_PATH")
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            raw = os.read(stream.fileno(), MAX_RECORD_BYTES + 1)
            after = os.fstat(stream.fileno())
            entry = directory.entry_stat(name)
            if (len(raw) > MAX_RECORD_BYTES or entry.st_nlink != 1 or before.st_nlink != 1
                    or _signature(before) != _signature(after) or _signature(entry)[:4] != _signature(after)[:4]):
                _fail("CHANGED")
            self.total_bytes += len(raw)
            if self.total_bytes > MAX_TOTAL_BYTES:
                _fail("BYTES_LIMIT")
            value = sync_state._decode(raw)
            if _encoded(value) != raw:
                _fail("RECORD_INVALID")
            if pin and existing is None:
                evidence = (directory, name, stream, _signature(after), _signature(entry), hashlib.sha256(raw).digest())
                self.pins.append(evidence)
                self.pin_index[key] = evidence
            elif existing is not None and hashlib.sha256(raw).digest() != existing[-1]:
                _fail("CHANGED")
            self._check()
            return value
        finally:
            if not pin:
                stream.close()

    @_checked
    def verify(self):
        self._verify_ownership()
        self._verify_names()
        for directory, name, stream, signature, path_signature, sha in self.pins:
            self._verify_directory(directory)
            entry = directory.entry_stat(name)
            actual = os.fstat(stream.fileno())
            if entry.st_nlink != 1 or actual.st_nlink != 1 or _signature(entry) != path_signature or _signature(actual) != signature:
                _fail("CHANGED")
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            if hashlib.sha256(os.read(stream.fileno(), MAX_RECORD_BYTES + 1)).digest() != sha:
                _fail("CHANGED")
        for directory, name, signature, expected in self.head_pins.values():
            if _signature(directory.entry_stat(name)) != signature or self._read(directory, name, pin=False) != expected:
                _fail("HEAD_CHANGED")
        scope = _observation_scope_raw()
        if scope is not None and scope._binding().phase == "reading":
            scope.mark_tail(self)

    def _capture_heads(self, head):
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            if self._read(directory, name, pin=False) != head:
                _fail("HEAD_CHANGED")
            self.head_pins[(directory.path, name)] = (directory, name,
                _signature(directory.entry_stat(name)), deepcopy(head))

    def _capture_observed_heads(self, heads):
        """Pin both actual values; a computed tail does not repair either file."""
        for (directory, name), head in zip(
                ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)), heads):
            if self._read(directory, name, pin=False) != head:
                _fail("HEAD_CHANGED")
            self.head_pins[(directory.path, name)] = (directory, name,
                _signature(directory.entry_stat(name)), deepcopy(head))

    def _capture_namespace(self, directory):
        names = _names(directory, lambda _: True, MAX_RECORDS * 2 + len(KINDS) + 2, self._check, verify_directory=self._verify_directory)
        previous = self.namespaces.get(directory.path)
        if previous is not None and names != previous[1]:
            _fail("NAMESPACE_CHANGED")
        self.namespaces[directory.path] = (directory, names)

    def _expect_name(self, directory, name):
        previous = self.namespaces.get(directory.path)
        if previous is None:
            _fail("NAMESPACE_CHANGED")
        self.namespaces[directory.path] = (directory, previous[1] | {name})

    def _verify_names(self):
        # Re-scan only the authority namespaces, never the mutable whole DATA
        # root. Own writes advance an exact expected set, not a blind re-capture.
        for directory, expected in tuple(self.namespaces.values()):
            actual = _names(directory, lambda _: True, MAX_RECORDS * 2 + len(KINDS) + 2, self._check, verify_directory=self._verify_directory)
            if actual != expected:
                _fail("NAMESPACE_CHANGED")
        for directory, name in self.absent_paths or ():
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("NAMESPACE_CHANGED")

    def _directory(self, parent, name, *, create=False):
        if create:
            self._verify_ownership()
        else:
            self._check()
        key = (parent.path, name)
        existing = self.directory_index.get(key)
        if existing is not None:
            # The same live owner keeps these exact ancestor handles. Entry
            # and exit namespace scans still compare the complete expected set;
            # re-opening every directory within each observe adds no witness.
            self._verify_directory(existing)
            return existing
        if parent is self.root and name == DIRECTORY:
            outer = self._open_directory(parent.path / name, parent=parent, create=create)
            self._capture_namespace(outer)
            _names(outer, lambda item: item == VERSION_DIRECTORY, 1, self._check, verify_directory=self._verify_directory)
            result = self._open_directory(outer.path / VERSION_DIRECTORY, parent=outer, create=create)
            if create:
                self._expect_name(outer, VERSION_DIRECTORY)
            self._capture_namespace(outer)
        else:
            result = self._open_directory(parent.path / name, parent=parent, create=create)
            if create:
                self._expect_name(parent, name)
        self._capture_namespace(result)
        self.directory_index[key] = result
        return result

    def _head(self, activation, ordinal, sha):
        return {"format_version": 1, "facility_id": activation["facility_id"],
                "ordinal": ordinal, "sha256": sha}

    @_checked
    def activate_files(self, activation):
        """Explicit file setup only. No source or SQL effect is activated by this operation.

        An interrupted unsealed setup can resume only while its ledger is empty.
        No old controls gain negative proof through this operation.
        """
        self._check()
        self._verify_names()
        activation = validate_contract(activation, profile=self.profile)
        if not self.allow_initialize:
            _fail("INITIALIZATION_REQUIRED")
        if activation["binding"] != self.lock.store.read()["binding"]:
            _fail("PEER_MISMATCH")
        try:
            original = self._read(self.root, INTENT_NAME)
        except FileNotFoundError:
            for directory, name in ((self.root, DIRECTORY), (self.root, DB_HEAD_NAME),
                    (self.checkpoint_root, SEAL_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                try:
                    directory.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("ACTIVATION_INCOMPLETE")
            self.absent_paths = None  # This exact explicit initialization owns the transition.
            self._write(self.root, INTENT_NAME, _encoded(activation), immutable=True)
        else:
            if original != activation:
                _fail("CONTRACT_MISMATCH")
        try:
            seal = self._read(self.checkpoint_root, SEAL_NAME)
        except FileNotFoundError:
            seal = None
        if seal is not None:
            if seal != activation:
                _fail("CONTRACT_MISMATCH")
            if self.observe()["contract"] != activation:
                _fail("CONTRACT_MISMATCH")
            return activation
        ledger = self._directory(self.root, DIRECTORY, create=True)
        _names(ledger, lambda name: name in {"contract.json", "journal", *KINDS}, 7, self._check, verify_directory=self._verify_directory)
        try:
            existing = self._read(ledger, "contract.json")
        except FileNotFoundError:
            self._write(ledger, "contract.json", _encoded(activation), immutable=True)
            self._expect_name(ledger, "contract.json")
        else:
            if existing != activation:
                _fail("CONTRACT_MISMATCH")
        for name in ("journal", *KINDS):
            directory = self._directory(ledger, name, create=True)
            _names(directory, lambda _: False, 0, self._check, verify_directory=self._verify_directory)
        head = self._head(activation, 0, _digest(activation))
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            try:
                existing = self._read(directory, name, pin=False)
            except FileNotFoundError:
                self._write(directory, name, _encoded(head), immutable=True)
            else:
                if existing != head:
                    _fail("ACTIVATION_INCOMPLETE")
        self._write(self.checkpoint_root, SEAL_NAME, _encoded(activation), immutable=True)
        self._capture_heads(head)
        return activation

    @_checked
    def observe(self, *, resume=None, resume_origin=None):
        """Read a bounded journal closure. Optional resume is one exact record.

        A reservation with a missing file is reported incomplete, not absent.
        Interrupted head publication may only resume the same kind/key/SHA.
        """
        return self._observe(resume=resume, resume_origin=resume_origin)

    @_checked
    def observe_readonly(self):
        """Read actual creation gaps without repairing heads or missing records.

        This returns observations only. Original-creation recovery must capture
        them itself and bind a separate sealed completion producer.
        """
        from .budget_writer_witness import in_write_window
        if in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        return self._observe(readonly=True)

    def _observe(self, *, resume=None, resume_origin=None, readonly=False):
        existing = _observation_scope_raw()
        if existing is not None:
            # Reentrant legacy or future callers receive the full original path.
            with existing.suspended(self):
                return self._observe_read(resume=resume, resume_origin=resume_origin, readonly=readonly)
        if not _observation_eligible(self, resume, resume_origin, readonly):
            return self._observe_read(resume=resume, resume_origin=resume_origin, readonly=readonly)
        with _authority_directory_observation(self):
            return self._observe_read(resume=resume, resume_origin=resume_origin, readonly=readonly)

    def _observe_read(self, *, resume=None, resume_origin=None, readonly=False):
        self._check()
        self._verify_names()
        scope = _observation_scope_raw()
        if scope is not None and scope._binding().phase == "opening":
            scope.activate(self)
        try:
            activation = self._read(self.root, INTENT_NAME)
        except FileNotFoundError:
            for directory, name in ((self.root, DIRECTORY), (self.root, DB_HEAD_NAME),
                    (self.checkpoint_root, SEAL_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                try:
                    directory.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("ACTIVATION_INCOMPLETE")
            self.absent_paths = ((self.root, INTENT_NAME), (self.root, DIRECTORY), (self.root, DB_HEAD_NAME),
                (self.checkpoint_root, SEAL_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME))
            absent = {"contract": None, "entries": {kind: {} for kind in KINDS}, "incomplete": []}
            if readonly:
                absent.update(ledger=None, head=None, journal_head=None, actual_heads=None,
                              head_status="absent")
            return absent
        activation = validate_contract(activation, profile=self.profile)
        if activation["binding"] != self.lock.store.read()["binding"]:
            _fail("PEER_MISMATCH")
        if self._read(self.checkpoint_root, SEAL_NAME) != activation:
            _fail("CONTRACT_MISMATCH")
        ledger = self._directory(self.root, DIRECTORY)
        _names(ledger, lambda name: name in {"contract.json", "journal", *KINDS}, 7, self._check, verify_directory=self._verify_directory)
        if self._read(ledger, "contract.json") != activation:
            _fail("CONTRACT_MISMATCH")
        heads = [self._read(directory, name, pin=False) for directory, name in
            ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME))]
        for head in heads:
            _exact(head, _HEAD_FIELDS)
            if type(head["format_version"]) is not int or head["format_version"] != 1 or head["facility_id"] != activation["facility_id"]:
                _fail("HEAD_INVALID")
            _integer(head["ordinal"], maximum=MAX_RECORDS * 2)
            _sha(head["sha256"])
        journal = self._directory(ledger, "journal")
        _names(journal, lambda name: bool(_ORDINAL.fullmatch(name)), MAX_RECORDS * 2, self._check, verify_directory=self._verify_directory)
        # List after the bounded scandir; all entries must form this exact chain.
        names = sorted(name for name in journal.names() if not _PART.fullmatch(name))
        entries = {kind: {} for kind in KINDS}
        hashes = [_digest(activation)]
        events = []
        for ordinal, name in enumerate(names, 1):
            if name != f"{ordinal:08d}.json":
                _fail("JOURNAL_INVALID")
            event = self._read(journal, name)
            _exact(event, _JOURNAL_FIELDS)
            if (type(event["format_version"]) is not int or event["format_version"] != 1
                    or type(event["ordinal"]) is not int or event["ordinal"] != ordinal
                    or event["facility_id"] != activation["facility_id"]
                    or event["previous_sha256"] != hashes[-1] or event["kind"] not in KINDS
                    or event["state"] not in {"reserved", "committed"}):
                _fail("JOURNAL_INVALID")
            _uuid(event["key"])
            record = _record(event["kind"], event["record"], activation)
            if record["record_id"] != event["key"] or event["record_sha256"] != _digest(record):
                _fail("JOURNAL_INVALID")
            previous = entries[event["kind"]].get(event["key"])
            if previous is None:
                if event["state"] != "reserved":
                    _fail("JOURNAL_INVALID")
            elif previous["state"] != "reserved" or event["state"] != "committed" or previous["record"] != record:
                _fail("JOURNAL_INVALID")
            entries[event["kind"]][event["key"]] = {"state": event["state"], "record": record}
            if sum(len(values) for values in entries.values()) > MAX_RECORDS:
                _fail("COUNT_LIMIT")
            events.append(event)
            hashes.append(_digest(event))
        latest = self._head(activation, len(events), hashes[-1])
        for head in heads:
            if head["ordinal"] > len(events) or hashes[head["ordinal"]] != head["sha256"]:
                _fail("HEAD_INVALID")
        lagging = any(head != latest for head in heads)
        if lagging and readonly:
            if not events or any(head["ordinal"] < len(events) - 1 for head in heads):
                _fail("HEAD_INCOMPLETE")
        elif lagging:
            if resume_origin is not None:
                if type(resume_origin) is not tuple or len(resume_origin) != 3 or not events:
                    _fail("HEAD_INCOMPLETE")
                origin_id, core_sha, claim_sha = resume_origin
                _uuid(origin_id)
                _sha(core_sha)
                _sha(claim_sha)
                tail = events[-1]
                origin = tail["record"]
                if (tail["kind"] != "origins" or tail["key"] != origin_id
                        or origin["core_sha256"] != core_sha or digest(origin["claim"]) != claim_sha
                        or origin_id != str(uuid.uuid5(uuid.UUID(origin["core"]["continuation_id"]), "source-origin:v1:" + core_sha))):
                    _fail("HEAD_INCOMPLETE")
                # Preserve the exact typed immutable tail, including its first
                # created_at and SHA. This only finishes file publication; it
                # supplies no genesis producer, State binding or source grant.
                resume = ("origins", origin_id, tail["record_sha256"])
            if resume is None or not events or any(head["ordinal"] < len(events) - 1 for head in heads):
                _fail("HEAD_INCOMPLETE")
            kind, key, sha = resume
            if any(events[-1][field] != expected for field, expected in
                   (("kind", kind), ("key", key), ("record_sha256", sha))):
                _fail("HEAD_INCOMPLETE")
            for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
                self._write(directory, name, _encoded(latest))
        if readonly:
            self._capture_observed_heads(heads)
        else:
            self._capture_heads(latest)
        incomplete = []
        for kind in KINDS:
            directory = self._directory(ledger, kind)
            allowed = {key + ".json" for key in entries[kind]}
            _names(directory, lambda name: name in allowed, MAX_RECORDS, self._check, verify_directory=self._verify_directory)
            for key, entry in entries[kind].items():
                try:
                    actual = self._read(directory, key + ".json")
                except FileNotFoundError:
                    if entry["state"] == "committed":
                        _fail("RECORD_MISSING")
                    incomplete.append((kind, key))
                    continue
                if actual != entry["record"]:
                    _fail("RECORD_INVALID")
                if entry["state"] != "committed":
                    incomplete.append((kind, key))
        self._relations(entries)
        self.verify()
        observed = {"contract": activation, "entries": entries, "incomplete": incomplete,
                    "head": None if readonly and lagging else latest, "ledger": ledger}
        if readonly:
            observed.update(journal_head=deepcopy(latest),
                            actual_heads={"db": deepcopy(heads[0]), "checkpoint": deepcopy(heads[1])},
                            head_status="lagging" if lagging else "complete")
        return observed

    def _append(self, observed, kind, key, record, state):
        self._verify_ownership()
        self._verify_names()
        head = observed["head"]
        event = {"format_version": 1, "facility_id": observed["contract"]["facility_id"],
            "ordinal": head["ordinal"] + 1, "previous_sha256": head["sha256"],
            "kind": kind, "key": key, "record_sha256": _digest(record), "record": record, "state": state}
        if event["ordinal"] > MAX_RECORDS * 2:
            _fail("COUNT_LIMIT")
        journal = self._directory(observed["ledger"], "journal")
        self._write(journal, f"{event['ordinal']:08d}.json", _encoded(event), immutable=True)
        self._expect_name(journal, f"{event['ordinal']:08d}.json")
        latest = self._head(observed["contract"], event["ordinal"], _digest(event))
        for directory, name in ((self.root, DB_HEAD_NAME), (self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            self._write(directory, name, _encoded(latest))
        self._capture_heads(latest)

    @_checked
    def creation_head_repair_receipt(self, *, producer):
        """Expose only this owner's just-executed repair to its original guard."""
        receipt = self._head_repair_receipt
        if (type(receipt) is not _CreationHeadRepairReceipt or receipt._owner is not self
                or receipt._producer is not producer):
            _fail("HEAD_REPAIR_REQUIRED")
        receipt._context()
        return receipt

    @_checked
    def repair_creation_heads(self, *, producer):
        """Repair one original tail only after its closed producer's proof.

        The producer supplies no observation, tuple or replacement head. Both
        observations and every actual write belong to this live owner.
        """
        _require_creation_head_producer(self, producer)
        before = self.observe_readonly()
        if before["contract"] is None:
            _fail("ACTIVATION_REQUIRED")
        if before["head_status"] == "complete":
            return False
        record = producer.record
        kind = {"control": "controls", "publication": "publications"}.get(record["record_kind"])
        if record["record_kind"] == "recovery_receipt":
            from .sync_source_legacy_receipt_completion17 import ReceiptCompletionGuard17
            from .sync_source_legacy_pending_receipt17 import PendingReceiptCompletionGuard17
            if type(producer) not in {ReceiptCompletionGuard17, PendingReceiptCompletionGuard17}:
                _fail("PRODUCER_REQUIRED")
            kind = "receipts"
        if kind is None:
            _fail("PRODUCER_REQUIRED")
        latest = before["journal_head"]
        journal = self._directory(before["ledger"], "journal")
        tail = self._read(journal, f"{latest['ordinal']:08d}.json")
        original = {"record_kind": record["record_kind"], "record_id": record["record_id"],
                    "record_sha256": digest(record)}
        if (tail["kind"] != kind or tail["key"] != original["record_id"]
                or tail["record_sha256"] != original["record_sha256"] or tail["record"] != record
                or any(item != (kind, record["record_id"]) for item in before["incomplete"])):
            _fail("HEAD_REPAIR_MISMATCH")
        signatures = {key: _signature(directory.entry_stat(name)) for key, directory, name in
            (("db", self.root, DB_HEAD_NAME), ("checkpoint", self.checkpoint_root, CHECKPOINT_HEAD_NAME))}
        producer.check_head_repair(self)
        after_check = self.observe_readonly()
        after_signatures = {key: _signature(directory.entry_stat(name)) for key, directory, name in
            (("db", self.root, DB_HEAD_NAME), ("checkpoint", self.checkpoint_root, CHECKPOINT_HEAD_NAME))}
        if (after_check["actual_heads"] != before["actual_heads"] or after_signatures != signatures
                or after_check["journal_head"] != latest or after_check["entries"] != before["entries"]
                or after_check["incomplete"] != before["incomplete"]):
            _fail("HEAD_REPAIR_MISMATCH")
        for key, directory, name in (("db", self.root, DB_HEAD_NAME),
                                      ("checkpoint", self.checkpoint_root, CHECKPOINT_HEAD_NAME)):
            if before["actual_heads"][key] != latest:
                self._write(directory, name, _encoded(latest))
        self._capture_heads(latest)
        receipt = _CreationHeadRepairReceipt(self, producer, before["actual_heads"],
            {"db": latest, "checkpoint": latest}, latest, original, seal=_HEAD_REPAIR_SEAL)
        self._head_repair_receipt = receipt
        receipt._token = _HEAD_REPAIR_CONTEXT.set(receipt)
        try:
            producer.adopt_head_repair(self)
            self.verify()
        finally:
            _HEAD_REPAIR_CONTEXT.reset(receipt._token)
            self._head_repair_receipt = None
        return True

    def _publish(self, kind, record, *, producer=None):
        self._check()
        key = record["record_id"]
        original_completion = False
        if kind in {"controls", "publications"}:
            from .sync_source_lifecycle17 import ControlCompletionGuard17
            from .sync_source_publication_local2 import PublicationCompletionGuard2
            original_completion = type(producer) in {ControlCompletionGuard17, PublicationCompletionGuard2}
        receipt_observation, protected_receipt = None, False
        if kind == "receipts" and record["format_version"] == 1:
            # Classify from the actual permanent intent before any resume/head
            # write. An unknown producer cannot route a protected receipt into
            # the legacy tuple-repair branch by omitting its capability.
            receipt_observation = self.observe_readonly()
            protected_receipt = self._receipt_route17(record,
                entries=receipt_observation["entries"]) != "legacy16"
        modern_creation = kind in {"controls", "publications"} and (
            record["format_version"] == 2
            or original_completion
            or kind == "controls" and record["request"]["source"]["handshake"]["migration_version"] >= 17
            or kind == "publications" and record["creation_basis"]["continuity"] is not None)
        modern_creation = modern_creation or protected_receipt
        observed = ((receipt_observation or self.observe_readonly()) if modern_creation
                    else self.observe(resume=(kind, key, _digest(record))))
        if observed["contract"] is None:
            _fail("ACTIVATION_REQUIRED")
        record = _record(kind, record, observed["contract"])
        if any(item != (kind, key) for item in observed["incomplete"]):
            _fail("RECORD_INCOMPLETE")
        existing = observed["entries"][kind].get(key)
        if existing is not None:
            if existing["record"] != record:
                _fail("REQUEST_MISMATCH")
            if existing["state"] == "committed":
                if protected_receipt:
                    # Publication remains a closed producer operation even
                    # for an already saved fact. Pure historical lookups use
                    # read()/the receipt reader and need no new capability.
                    self._producer(kind, record, producer, entries=observed["entries"])
                if modern_creation and observed["head_status"] == "lagging":
                    if not protected_receipt:
                        self._producer(kind, record, producer, entries=observed["entries"])
                    self.repair_creation_heads(producer=producer)
                    self.observe()
                return deepcopy(record)
        if modern_creation:
            # Original-creation proof precedes any head write. A UUID/SHA tuple
            # alone cannot repair a modern control or publication journal.
            self._producer(kind, record, producer, entries=observed["entries"])
            if observed["head_status"] == "lagging":
                self.repair_creation_heads(producer=producer)
            observed = self.observe()
        if existing is None:
            if not modern_creation:
                self._producer(kind, record, producer, entries=observed["entries"])
            self._relations({**observed["entries"], kind: {**observed["entries"][kind], key: {"state": "reserved", "record": record}}})
            if sum(len(values) for values in observed["entries"].values()) >= MAX_RECORDS:
                _fail("COUNT_LIMIT")
            self._append(observed, kind, key, record, "reserved")
            observed = self.observe(resume=(kind, key, _digest(record)))
        if kind == "publications" and record["format_version"] == 2:
            from .sync_source_publication_local2 import materialize_publication2
            # Materialization checks the exact producer against its fresh
            # observed selection before the first outbox effect.
            materialize_publication2(self, record, producer)
        else:
            self._producer(kind, record, producer, entries=observed["entries"])
        directory = self._directory(observed["ledger"], kind)
        try:
            actual = self._read(directory, key + ".json")
        except FileNotFoundError:
            self._write(directory, key + ".json", _encoded(record), immutable=True)
            self._expect_name(directory, key + ".json")
        else:
            if actual != record:
                _fail("REQUEST_MISMATCH")
        self._append(observed, kind, key, record, "committed")
        self.observe()
        return deepcopy(record)

    def _resolve(self, entries, ref):
        return _resolve_record(entries, ref)

    def _parent_defer(self, entries, old, core_sha):
        if old["kind"] != "budget_resume":
            return None
        parent_id = old["defer_request_id"]
        for item in entries["receipts"].values():
            result = item["record"]["result"]
            if (item["state"] == "committed" and result.get("original_kind") == "budget_defer"
                    and result.get("original_request_id") == parent_id and result.get("disposition") == "applied" and result["core_sha256"] == core_sha):
                request = entries["requests"][item["record"]["record_id"]]["record"]["request"]["original_request"]
                return {"request": deepcopy(request), "receipt": deepcopy(result["original_receipt"])}
        state = self.lock.store.read()
        pending = state.get("budget_pending")
        if (state.get("source_execution") is not None and digest(state["source_execution"]) == core_sha
                and pending is not None and pending["defer_request"]["request_id"] == parent_id and pending["defer_receipt"] is not None):
            receipt = sync_client.normalize_budget_receipt(pending["defer_request"], pending["defer_receipt"])
            receipt["replayed"] = False
            return {"request": deepcopy(pending["defer_request"]), "receipt": receipt}
        _fail("PARENT_RECEIPT_REQUIRED")

    def _relations(self, entries):
        return _record_relations(entries)

    def _receipt_route17(self, record, *, entries):
        """Classify original wire1 facts before any journal/head mutation.

        Frozen request handshakes and supplied producer objects cannot select
        the legacy repair path. The original committed before-image chooses
        the independent missing-origin bridge only in an actual schema17 DB.
        A released16 database retains its historical publication semantics.
        """
        self._check()
        item = entries["requests"].get(record["record_id"])
        if item is None or item["state"] != "committed":
            _fail("INTENT_REQUIRED")
        intent, request = item["record"], item["record"]["request"]
        if (intent["format_version"] != 1 or request.get("format_version", 1) != 1
                or request["kind"] not in {"source_register", "source_dispose", "source_readmit"}
                or intent["record_id"] != record["record_id"]
                or intent["request_sha256"] != record["request_sha256"]
                or contract.request_sha256(request) != record["request_sha256"]):
            _fail("REQUEST_MISMATCH")
        from . import budget_writer_witness as sql, migrations, source_preservation
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        guard = lifecycle.require_guard(self.profile)
        original_file = sql._file_identity(self.profile.database)
        con = migrations._open(self.profile.database, readonly=True)
        try:
            storage_admin.from_connection(con, profile=self.profile)
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
            if version is None or version[0] not in {"16", "17"}:
                _fail("SCHEMA_REQUIRED")
            if version[0] == "17":
                from . import home_recovery17
                home_recovery17._actual_schema(con)
            else:
                # In particular, a schema17 namespace with a forged 16 label
                # must not restore the tuple-only historical repair branch.
                source_preservation._schema(con)
            if sql._file_identity(self.profile.database) != original_file:
                _fail("SCHEMA_CHANGED")
            guard.verify()
            self._check()
            local_version = version[0]
        finally:
            con.close()
        if local_version == "16":
            return "legacy16"
        original = intent["expected_before"]["source_authority"]
        if original is None or original["origin"] is None:
            if request["kind"] == "source_register":
                # No released missing-reference registration producer exists.
                _fail("ORIGINAL_REFERENCE_REQUIRED")
            return "pending17"
        return "known17"

    def _producer(self, kind, record, producer, *, entries):
        if record["format_version"] == 2:
            from .sync_source_readmit_local2 import require_producer2
            require_producer2(self, kind, record, producer, entries=entries)
            return
        if kind in {"controls", "publications"}:
            from .sync_source_lifecycle17 import ControlCreationGuard17, PublicationCreationGuard17
            from .sync_source_lifecycle17 import ControlCompletionGuard17
            from .sync_source_publication_local2 import PublicationCompletionGuard2
            completion = ControlCompletionGuard17 if kind == "controls" else PublicationCompletionGuard2
            if type(producer) is completion:
                if producer.owner is not self:
                    _fail("PRODUCER_REQUIRED")
                producer.check(record)
                return
            expected = ControlCreationGuard17 if kind == "controls" else PublicationCreationGuard17
            if type(producer) is expected:
                if producer.owner is not self:
                    _fail("PRODUCER_REQUIRED")
                producer.check(record)
                return
        if kind == "requests" and record["request"]["kind"] == "source_register":
            from .sync_source_lifecycle17 import RegistrationCreationGuard17
            if type(producer) is RegistrationCreationGuard17:
                if producer.owner is not self:
                    _fail("PRODUCER_REQUIRED")
                producer.check(record)
                return
        if kind == "origins" and type(producer) is execution.OriginGenesisGuard:
            if producer.owner is not self:
                _fail("PRODUCER_REQUIRED")
            producer.check(record)
            return
        if kind == "origins":
            from .sync_source_genesis17 import Genesis17Guard
            if type(producer) is Genesis17Guard:
                if producer.owner is not self:
                    _fail("PRODUCER_REQUIRED")
                producer.check(record)
                return
        if kind == "receipts":
            from .sync_source_legacy_receipt_completion17 import ReceiptCompletionGuard17, require_receipt_completion17
            route = self._receipt_route17(record, entries=entries)
            if route == "pending17":
                from .sync_source_legacy_pending_intake17 import require_legacy_pending_http17
                from .sync_source_legacy_pending_receipt17 import PendingReceiptCompletionGuard17, require_pending_receipt_completion17
                if type(producer) is PendingReceiptCompletionGuard17:
                    require_pending_receipt_completion17(self, kind, record, producer, entries=entries)
                else:
                    require_legacy_pending_http17(self, kind, record, producer, entries=entries)
            elif type(producer) is ReceiptCompletionGuard17:
                require_receipt_completion17(self, kind, record, producer, entries=entries)
            elif route == "known17":
                from .sync_source_legacy_intake17 import require_legacy_http_producer17
                require_legacy_http_producer17(self, kind, record, producer, entries=entries)
            if record["parent_defer"] is not None:
                intent = entries["requests"].get(record["record_id"])
                if intent is None or intent["state"] != "committed":
                    _fail("INTENT_REQUIRED")
                value = intent["record"]
                actual = self._parent_defer(entries, value["request"]["original_request"], value["core_sha256"])
                if actual != record["parent_defer"]:
                    _fail("PARENT_RECEIPT_MISMATCH")
            return  # Exact permanent intent/typed result is required separately.
        if type(producer) is not ProducerGuard or producer._seal is not _SEAL:
            _fail("PRODUCER_REQUIRED")
        producer.check(record, historical=kind == "requests")
        if producer.owner is not self:
            _fail("PRODUCER_REQUIRED")

    @_checked
    def publish(self, kind, record, *, producer=None):
        """Save one exact immutable record; never dispatch or issue permission."""
        self._check()
        activation = validate_contract(self._read(self.root, INTENT_NAME), profile=self.profile)
        record = _record(kind, record, activation)
        return self._publish(kind, record, producer=producer)

    @_checked
    def read(self, ref):
        """Exact permanent lookup, including ledgers behind old state formats."""
        observed = self.observe()
        if observed["contract"] is None:
            _fail("ACTIVATION_REQUIRED")
        return deepcopy(self._resolve(observed["entries"], ref))


def _require_creation_head_producer(owner, producer):
    from .sync_source_lifecycle17 import ControlCreationGuard17, ControlCompletionGuard17
    from .sync_source_publication_local2 import PublicationCreationGuard2, PublicationCompletionGuard2
    from .sync_source_legacy_receipt_completion17 import ReceiptCompletionGuard17
    from .sync_source_legacy_pending_receipt17 import PendingReceiptCompletionGuard17
    if (type(producer) not in {ControlCreationGuard17, ControlCompletionGuard17,
                              PublicationCreationGuard2, PublicationCompletionGuard2,
                              ReceiptCompletionGuard17, PendingReceiptCompletionGuard17}
            or producer.owner is not owner):
        _fail("PRODUCER_REQUIRED")


class _CreationHeadRepairReceipt:
    """Lexical receipt for exact writes already made by the real authority owner."""
    __slots__ = ("_owner", "_producer", "_before", "_after", "_head", "_original", "_seal", "_token")

    def __init__(self, owner, producer, before, after, head, original, *, seal):
        if seal is not _HEAD_REPAIR_SEAL or type(owner) is not AuthorityGuard:
            _fail("HEAD_REPAIR_REQUIRED")
        self._owner, self._producer = owner, producer
        self._before, self._after = deepcopy(before), deepcopy(after)
        self._head, self._original = deepcopy(head), deepcopy(original)
        self._seal, self._token = seal, None

    def _context(self):
        if (type(self) is not _CreationHeadRepairReceipt or self._seal is not _HEAD_REPAIR_SEAL
                or _HEAD_REPAIR_CONTEXT.get() is not self
                or self._owner._head_repair_receipt is not self):
            _fail("HEAD_REPAIR_REQUIRED")
        self._owner._check()
        try:
            _HEAD_REPAIR_CONTEXT.reset(self._token)
        except (ValueError, RuntimeError):
            _fail("HEAD_REPAIR_REQUIRED")
        self._token = _HEAD_REPAIR_CONTEXT.set(self)

    @property
    def owner(self):
        self._context()
        return self._owner

    @property
    def producer(self):
        self._context()
        return self._producer

    @property
    def before_heads(self):
        self._context()
        return deepcopy(self._before)

    @property
    def after_heads(self):
        self._context()
        return deepcopy(self._after)

    @property
    def journal_head(self):
        self._context()
        return deepcopy(self._head)

    @property
    def original(self):
        self._context()
        return deepcopy(self._original)


class ProducerGuard:
    """Actual live SQL/process owner, not a serializable admission proof.

    The initial factory supports the old sealed Home binding only. A current
    admission/Cloud-activation factory must be implemented separately before
    writing new schema17 control/publication creation authority.
    This factory also cannot establish an origin genesis. A preexisting core
    and live owner do not prove its creation point; missing origins stay unknown.
    """
    __slots__ = ("owner", "binding", "connection", "historical", "thread", "_seal")

    def __init__(self, owner, binding, connection, *, historical, seal):
        if seal is not _SEAL:
            _fail("PRODUCER_REQUIRED")
        self.owner, self.binding, self.connection = owner, binding, connection
        self.historical, self.thread, self._seal = historical, threading.get_ident(), seal

    def check(self, record=None, *, historical=False):
        if (self._seal is not _SEAL or _PRODUCER.get() is not self or threading.get_ident() != self.thread
                or historical != self.historical or _CURRENT.get() is not self.owner):
            _fail("PRODUCER_REQUIRED")
        self.owner._check()
        if not self.connection.in_transaction:
            _fail("SQL_GUARD_REQUIRED")
        storage_admin.from_connection(self.connection, profile=self.owner.profile)
        core = self.binding.core
        state = self.owner.lock.store.read()
        if historical:
            current = execution._job(self.owner.profile, connection=self.connection, fresh_admission=False)
            execution._peer(core["binding"])
            if (current["job_id"], current["continuation_id"], current["execution_generation"]) != (core["root_job_id"], core["continuation_id"], core["execution_generation"]) or state.get("source_execution") != core:
                _fail("CREATION_MISMATCH")
        else:
            self.binding.check(connection=self.connection)
        local, version = sync_access.identity(self.connection), db.knowledge_version(self.connection)
        if (local["library_id"], local["node_id"]) != (core["binding"]["home_library_id"], core["binding"]["home_node_id"]) or version["library_epoch"] != state["local_epoch"]:
            _fail("CREATION_MISMATCH")
        if record is None:
            return
        if record["core_sha256"] != digest(core):
            _fail("CREATION_MISMATCH")
        if record["record_kind"] == "origin":
            # The real source creation/transaction path must provide a distinct
            # sealed genesis ticket. There is deliberately no first-origin
            # factory yet, and no caller SQL hook is replaced to guess one.
            _fail("ORIGIN_CREATION_REQUIRED")
        elif record["record_kind"] in {"control", "publication"}:
            basis = record["creation_basis"]
            # No existing old _SEAL can authenticate a schema17 activation or
            # admission. Fail closed until the distinct real factory exists.
            if basis["kind"] != "original_core" or basis["continuity"] is not None or record.get("request", {}).get("source", {}).get("handshake", {}).get("migration_version", 0) >= 17:
                _fail("CONTINUITY_PRODUCER_REQUIRED")
            if record["record_kind"] == "control" and record["request"]["source"] != local:
                _fail("CREATION_MISMATCH")
            if record["record_kind"] == "publication" and local["handshake"]["migration_version"] >= 17:
                _fail("CONTINUITY_PRODUCER_REQUIRED")
            entry = next((entry for entry in state["claims"] if entry["item_id"] == core["item_id"]), None)
            recovery = state["recovery"]
            if (entry is None or basis["claim"] != entry["claim"] or basis["session"] != state["lease"]
                    or basis["home_epoch"] != state["local_epoch"]
                    or basis["recovery_generation"] != recovery["generation"] or basis["recovery_stream"] != recovery["stream"]
                    or basis["control_revision"] != recovery["revision"]):
                _fail("CREATION_MISMATCH")
            if record["record_kind"] == "publication":
                envelope = self.owner.lock.store.read_publication(record["metadata"])
                if digest(envelope["claims"]) != record["claims_sha256"] or not any(item["claim"] == basis["claim"] for item in envelope["claims"]):
                    _fail("CREATION_MISMATCH")
        else:
            before = record["expected_before"]
            recovery = state["recovery"]
            entry = next((entry for entry in state["claims"] if entry["item_id"] == core["item_id"]), None)
            expected = {"binding": state["binding"], "local_epoch": state["local_epoch"], "core_sha256": digest(core),
                "admission_ref": state.get("source_admission"), "recovery_generation": recovery["generation"],
                "recovery_stream": recovery["stream"], "control_revision": recovery["revision"], "claim": None if entry is None else entry["claim"],
                "budget_pending_sha256": None if state.get("budget_pending") is None else digest(sync_state._stable_pending(state["budget_pending"])),
                "source_control_pending_sha256": None if state.get("source_control_pending") is None else digest(sync_state._stable_source_control(state["source_control_pending"])),
                "pending_publication": state["pending_publication"], "source_authority": state.get("source_authority")}
            if before != expected:
                _fail("CREATION_MISMATCH")
            request = record["request"]
            if (request["source"] != local or request["lease"] != state["lease"]
                    or request["recovery_generation"] != recovery["generation"]
                    or request["recovery_stream"] != recovery["stream"] or request["control_revision"] != recovery["revision"]):
                _fail("CREATION_MISMATCH")
            if request["kind"] == "source_register":
                # No authenticated activation producer is installed in this
                # component. A caller's continuity DTO cannot register a body.
                _fail("CONTINUITY_PRODUCER_REQUIRED")
            original = request["original"]
            if (request["current_home_epoch"] != state["local_epoch"]
                    or any(original[key] != core[key] for key in ("item_id", "submitted_url_sha256", "recovery_generation", "recovery_stream"))
                    or original["home_epoch"] != core["local_epoch"]):
                _fail("CREATION_MISMATCH")


@contextmanager
def producer(owner, binding, connection, *, historical=False):
    """Bind the actual existing Home _SEAL and live SQL owner to this lifetime."""
    with _errors():
        owner._check()
        if (type(historical) is not bool or type(connection) is not sqlite3.Connection
                or type(binding) is not execution.HomeExecutionBinding or binding._seal is not execution._SEAL
                or binding.store is not owner.lock.store or binding.profile.identity != owner.profile.identity):
            _fail("PRODUCER_REQUIRED")
        value = ProducerGuard(owner, binding, connection, historical=historical, seal=_SEAL)
        token = _PRODUCER.set(value)
        try:
            value.check(historical=historical)
            yield value
            value.check(historical=historical)
        finally:
            _PRODUCER.reset(token)


@contextmanager
def owned(store, *, initialize=False):
    """Require caller-owned State OS/thread lock plus SH; never acquire/create it."""
    with _errors(), ExitStack() as stack:
        if type(initialize) is not bool or type(store) is not sync_state.StateStore or store.profile is None:
            _fail("LOCK_REQUIRED")
        lock = _Ownership(store)
        lock.verify()
        if _CURRENT.get() is not None:
            _fail("LOCK_REQUIRED")
        checkpoint_root = stack.enter_context(media._Directory(store.profile.checkpoints))
        guard = AuthorityGuard(store.profile, store._directory, checkpoint_root, lock, stack)
        guard.allow_initialize = initialize
        token = _CURRENT.set(guard)
        try:
            guard._check()
            yield guard
            guard.verify()
        finally:
            lock.held = False
            _CURRENT.reset(token)
