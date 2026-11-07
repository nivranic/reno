"""Crash-safe local sync cursors and immutable publication retries.

This store never opens SQLite. A process lock fences syncers while a short
thread lock lets the heartbeat merge current state during long network work.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack, nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid

from . import storage_lifecycle as lifecycle, sync_media as media, sync_protocol as protocol, sync_snapshot
from .sync_client import _base_url, _recovery, normalize_budget_request, normalize_budget_receipt
from .sync_protocol import SyncError
from .sync_transfer import _bindings

MAX_STATE_BYTES = 1024 * 1024
MAX_OUTBOX_BYTES = 65 * 1024 * 1024
MAX_STATE_NODES = 32_768
MAX_SOURCE_HISTORY_LOOKUP = 4096
MAX_SOURCE_HISTORY_LOOKUP_BYTES = 32 * 1024 * 1024
_STATE = "sync-state.json"
_LOCK = "sync-state.lock"
_OUTBOX = "sync-outbox"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_FIELDS = frozenset(("format_version", "binding", "local_epoch", "session_id", "lease",
                     "inbound", "outbound", "seeded", "claims", "work", "pending_publication",
                     "published_version", "published_content_sha256", "published_reports_sha256", "uploaded_media", "status",
                     "recovery", "applied_recovery_generation", "recovery_attempt", "legacy_pending_stream"))
_RECOVERY_DEFAULTS = {"recovery": None, "applied_recovery_generation": 0,
                      "recovery_attempt": None, "legacy_pending_stream": None}
_V2_FIELDS = _FIELDS | {"sender_contract", "publication_media"}
_V3_FIELDS = _V2_FIELDS | {"source_execution", "budget_pending"}
_V4_FIELDS = _V3_FIELDS | {"source_admission", "source_control_pending"}
_V5_FIELDS = _V4_FIELDS | {"source_authority", "source_recovery_pending"}
_SOURCE_EXECUTION_FIELDS = {"format_version", "binding", "item_id", "submitted_url_sha256",
    "root_job_id", "continuation_id", "execution_generation", "local_epoch",
    "recovery_generation", "recovery_stream", "resolution_input_sha256"}
_BUDGET_PENDING_FIELDS = {"defer_request", "defer_receipt", "resume_request", "resume_receipt"}
_HISTORY_FIELDS = {"format_version", "reason", "binding", "local_epoch", "claims", "work", "inbound", "outbound",
                   "pending_publication", "recovery_attempt", "recovery", "legacy_pending_stream",
                   "sender_contract", "publication_media", "uploaded_media", "source_execution",
                   "budget_pending", "source_terminal"}
_TERMINAL_FIELDS = {"format_version", "kind", "core_sha256", "proof_sha256", "proof"}
_HISTORY_V4_FIELDS = _HISTORY_FIELDS | {"source_admission", "source_control_pending"}
_HISTORY_V5_FIELDS = _HISTORY_V4_FIELDS | {"source_authority", "source_recovery_pending"}
_SOURCE_CONTROL_PENDING_FIELDS = {"core_sha256", "request", "receipt"}
_SOURCE_REF_FIELDS = {"format_version", "record_kind", "record_id", "record_sha256"}
_SOURCE_ADMISSION_REF_FIELDS = {"format_version", "admission_id", "core_sha256", "request_sha256", "admission_sha256", "record_sha256"}
_SOURCE_AUTHORITY_FIELDS = {"format_version", "core_sha256", "origin", "defer", "resume", "failure", "publication"}
_SOURCE_RECOVERY_PENDING_FIELDS = {"format_version", "kind", "request_id", "request_sha256", "core_sha256", "intent_sha256"}
_TERMINAL_V2_FIELDS = _TERMINAL_FIELDS | {"authority_ref", "authority_sha256"}


def _error(code="INVALID_SYNC_STATE", status=409):
    messages = {
        "SYNC_BUSY": "Another syncer owns the local state",
        "SYNC_STATE_MISSING": "Sync state has not been initialized",
        "SYNC_STATE_BINDING_MISMATCH": "Sync state belongs to a different binding",
        "SYNC_STATE_EPOCH_MISMATCH": "Sync state epoch cannot move backwards",
        "SYNC_STATE_STORAGE_ERROR": "Sync state storage operation failed",
        "SYNC_STATE_UNSAFE_PATH": "Sync state paths must be ordinary directories and files",
        "SYNC_OUTBOX_MISSING": "Pending sync publication is missing",
        "SYNC_OUTBOX_CORRUPT": "Pending sync publication failed integrity verification",
        "SYNC_OUTBOX_CONFLICT": "Publication identifier already has different content",
    }
    raise SyncError(messages.get(code, "Sync state does not match its bounded contract"), code, status) from None


@contextmanager
def _sanitized(*, cancelled=None):
    try:
        yield
    except SyncError as exc:
        if cancelled is not None and exc is cancelled():
            raise
        if exc.code.startswith(("SYNC_STATE_", "SYNC_OUTBOX_", "SYNC_NO_RESEND_")) or exc.code in {"INVALID_SYNC_STATE", "SYNC_BUSY"}:
            raise
        if exc.code == "SYNC_MEDIA_UNSAFE_PATH":
            _error("SYNC_STATE_UNSAFE_PATH")
        _error()
    except OSError as exc:
        if cancelled is not None and exc is cancelled():
            raise
        _error("SYNC_STATE_STORAGE_ERROR", 503)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError) as exc:
        if cancelled is not None and exc is cancelled():
            raise
        _error()


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _error()


def _integer(value, minimum=1):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        _error()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _error()


def _timestamp(value):
    if type(value) is not str or not _STAMP.fullmatch(value):
        _error()
    datetime.fromisoformat(value.replace("Z", "+00:00"))


def _binding(value, *, normalize=False):
    _exact(value, ("home_library_id", "home_node_id", "target_library_id", "target_node_id", "target_url"))
    result = dict(value)
    for key in ("home_library_id", "home_node_id", "target_library_id", "target_node_id"):
        _uuid(result[key])
    url, http, loopback = _base_url(result["target_url"])
    if http and not loopback or not normalize and url != result["target_url"]:
        _error()
    result["target_url"] = url
    return result


def _claim(value):
    _exact(value, ("item_id", "lease_token", "lease_generation"))
    _integer(value["item_id"])
    _integer(value["lease_generation"])
    _uuid(value["lease_token"])


def _cursor(value):
    _exact(value, ("stream", "after", "through"))
    _integer(value["after"], 0)
    if value["through"] is not None:
        _integer(value["through"], value["after"])
    if value["stream"] is not None:
        _exact(value["stream"], ("library_id", "library_epoch"))
        _uuid(value["stream"]["library_id"])
        _integer(value["stream"]["library_epoch"])
    elif value["after"] != 0 or value["through"] is not None:
        _error()


def _metadata(value):
    _exact(value, ("publication_id", "sha256", "byte_size"))
    _uuid(value["publication_id"])
    if type(value["sha256"]) is not str or not _SHA.fullmatch(value["sha256"]):
        _error()
    _integer(value["byte_size"])
    if value["byte_size"] > MAX_OUTBOX_BYTES:
        _error()


def _bounded(value, *, maximum, nodes=None, depth=24):
    remaining = nodes
    stack = [(value, 0)]
    while stack:
        item, level = stack.pop()
        if remaining is not None:
            remaining -= 1
            if remaining < 0:
                _error()
        if level > depth:
            _error()
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                _error()
            stack.extend((child, level + 1) for pair in item.items() for child in pair)
        elif type(item) is list:
            stack.extend((child, level + 1) for child in item)
        elif type(item) is str:
            if len(item) > maximum:
                _error()
        elif type(item) is float:
            if not math.isfinite(item):
                _error()
        elif item is not None and type(item) not in (bool, int):
            _error()
    parts, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    for part in encoder.iterencode(value):
        chunk = part.encode("utf-8")
        size += len(chunk)
        if size > maximum:
            _error()
        parts.append(chunk)
    return b"".join(parts)


def _source_execution(value, state):
    """Validate persisted facts only; the caller proves the real job/owner."""
    _bounded(value, maximum=16 * 1024, nodes=256, depth=6)
    _exact(value, _SOURCE_EXECUTION_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _error()
    if _binding(value["binding"]) != state["binding"]:
        _error("SYNC_STATE_BINDING_MISMATCH")
    for name in ("item_id", "root_job_id", "execution_generation", "local_epoch"):
        _integer(value[name])
    _integer(value["recovery_generation"], minimum=0)
    _uuid(value["continuation_id"])
    for name in ("submitted_url_sha256", "resolution_input_sha256"):
        if type(value[name]) is not str or not _SHA.fullmatch(value[name]):
            _error()
    _cursor({"stream": value["recovery_stream"], "after": 0, "through": None})
    if (value["recovery_stream"] is None
            or value["recovery_stream"]["library_id"] != state["binding"]["target_library_id"]
            or value["local_epoch"] > state["local_epoch"]):
        _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
    return deepcopy(value)


def _source_sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _error()


def _source_ref(value, *, kind):
    _exact(value, _SOURCE_REF_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 1 or value["record_kind"] != kind:
        _error()
    _uuid(value["record_id"])
    _source_sha(value["record_sha256"])


def _source_readmit_refs(value):
    """Closed State5 representation, never a permanent-record/owner proof."""
    core = value["source_execution"]
    core_sha = _source_digest(core) if core is not None else None
    admission, authority, pending = (value[name] for name in
        ("source_admission", "source_authority", "source_recovery_pending"))
    if core is None and any(item is not None for item in (admission, authority, pending)):
        _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
    if admission is not None:
        _exact(admission, _SOURCE_ADMISSION_REF_FIELDS)
        if type(admission["format_version"]) is not int or admission["format_version"] != 1:
            _error()
        _uuid(admission["admission_id"])
        for name in _SOURCE_ADMISSION_REF_FIELDS - {"format_version", "admission_id"}:
            _source_sha(admission[name])
        if admission["core_sha256"] != core_sha:
            _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
    if authority is not None:
        _exact(authority, _SOURCE_AUTHORITY_FIELDS)
        if type(authority["format_version"]) is not int or authority["format_version"] != 1:
            _error()
        if authority["core_sha256"] != core_sha:
            _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
        for name in ("origin", "defer", "resume", "failure", "publication"):
            if authority[name] is not None:
                _source_ref(authority[name], kind="origin" if name == "origin" else "publication" if name == "publication" else "control")
    if pending is not None:
        _exact(pending, _SOURCE_RECOVERY_PENDING_FIELDS)
        if (type(pending["format_version"]) is not int or pending["format_version"] != 1
                or pending["kind"] not in {"source_register", "source_dispose", "source_readmit"}):
            _error()
        _uuid(pending["request_id"])
        for name in ("request_sha256", "core_sha256", "intent_sha256"):
            _source_sha(pending[name])
        if pending["core_sha256"] != core_sha:
            _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")


def source_readmit_projection(value):
    """Pure public State5 refs; physical bytes and real ownership are separate.

    Permanent readers must resolve every exact ref and pending full-intent SHA.
    Caller DTOs and this projection grant no creation, SQL or source admission.
    """
    with _sanitized():
        _validate(value)
        if value["format_version"] != 5:
            _error("SYNC_STATE_SOURCE_READMIT_REQUIRED")
        refs = {"core_sha256": _source_digest(value["source_execution"]) if value["source_execution"] is not None else None,
            **{name: deepcopy(value[name]) for name in ("source_authority", "source_admission", "source_recovery_pending")}}
        return {"format_version": 1, "state_format_version": 5, **refs, "refset_sha256": _source_digest(refs)}


def _budget_peer(request, state):
    binding = state["binding"]
    if (any(request["source"][key] != binding["home_" + key] for key in ("library_id", "node_id"))
            or any(request["target"][key] != binding["target_" + key] for key in ("library_id", "node_id"))):
        _error("SYNC_STATE_BINDING_MISMATCH")


def _budget_pending(value, state):
    _bounded(value, maximum=256 * 1024, nodes=1024, depth=12)
    _exact(value, _BUDGET_PENDING_FIELDS)
    core = state["source_execution"]
    if core is None:
        _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
    original = normalize_budget_request(value["defer_request"])
    _budget_peer(original, state)
    if (original["kind"] != "budget_defer" or original["claim"]["item_id"] != core["item_id"]
            or state["format_version"] != 5 and (
                original["recovery_generation"] != core["recovery_generation"]
                or original["recovery_stream"] != core["recovery_stream"])):
        _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
    proof = value["defer_receipt"]
    if proof is not None:
        normalize_budget_receipt(original, proof)
    followup, result = value["resume_request"], value["resume_receipt"]
    if followup is not None:
        followup = normalize_budget_request(followup)
        _budget_peer(followup, state)
        if (proof is None or followup["kind"] != "budget_resume" or followup["defer_request_id"] != original["request_id"]
                or state["format_version"] != 5 and (
                    followup["recovery_generation"] != core["recovery_generation"]
                    or followup["recovery_stream"] != core["recovery_stream"])):
            _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
    if result is not None:
        if followup is None:
            _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
        normalize_budget_receipt(followup, result, defer_receipt=proof)
    return deepcopy(value)


def _same_receipt(first, second):
    return {key: value for key, value in first.items() if key != "replayed"} == {
        key: value for key, value in second.items() if key != "replayed"}


def _source_digest(value):
    return hashlib.sha256(_bounded(value, maximum=MAX_STATE_BYTES, nodes=MAX_STATE_NODES, depth=16)).hexdigest()


def _stable_pending(value):
    result = deepcopy(value)
    if result is not None:
        for name in ("defer_receipt", "resume_receipt"):
            if result[name] is not None:
                result[name]["replayed"] = False
    return result


def _source_control_pending(value, state):
    from . import sync_source_control_contract as control
    _bounded(value, maximum=64 * 1024, nodes=1024, depth=12)
    _exact(value, _SOURCE_CONTROL_PENDING_FIELDS)
    core = state["source_execution"]
    if core is None:
        _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
    request = control.normalize_request(value["request"])
    _budget_peer(request, state)
    if (value["core_sha256"] != _source_digest(core) or request["core_sha256"] != value["core_sha256"]
            or request["submitted_url_sha256"] != core["submitted_url_sha256"]
            or request["claim"]["item_id"] != core["item_id"]
            or state["format_version"] != 5 and (
                request["recovery_generation"] != core["recovery_generation"]
                or request["recovery_stream"] != core["recovery_stream"])):
        _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
    if value["receipt"] is not None:
        control.normalize_receipt(request, value["receipt"])
    return deepcopy(value)


def _stable_source_control(value):
    value = deepcopy(value)
    if value is not None and value["receipt"] is not None:
        value["receipt"]["replayed"] = False
    return value


def _source_terminal(kind, proof, state):
    core = state["source_execution"]
    if core is None:
        _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
    if kind == "budget_resumed":
        proof = _stable_pending(_budget_pending(proof, state))
        if proof["resume_receipt"] is None or proof != _stable_pending(state["budget_pending"]):
            _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
    elif kind == "source_published":
        from . import sync_no_resend
        _exact(proof, {"request", "response", "claims"})
        request = proof["request"]
        _exact(request, sync_no_resend._REQUEST)
        contract = sync_no_resend.validate_contract({key: request[key] for key in sync_no_resend._CONTRACT},
                                                    binding=state["binding"])
        if contract["ledger_id"] != state["sender_contract"]["ledger_id"]:
            _error("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        request = sync_no_resend.validate_request(request, contract)
        response = sync_no_resend.normalize_response(request, proof["response"])
        if request["reason"] != "published_receipt" or response["state"] != "published":
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        claims = proof["claims"]
        if type(claims) is not list:
            _error()
        claims = _bindings(claims, {entry.get("video_id") for entry in claims if type(entry) is dict})
        origin = next((entry.get("origin") for entry in claims if entry["claim"]["item_id"] == core["item_id"]), None)
        if (origin is None or hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]
                or origin["source_library_id"] != core["binding"]["home_library_id"]
                or origin["source_node_id"] != core["binding"]["home_node_id"]
                or request["binding"]["source_version"]["library_epoch"] != core["local_epoch"]
                or request["binding"]["recovery_generation"] != core["recovery_generation"]
                or request["binding"]["recovery_stream"] != core["recovery_stream"]):
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        proof = {"request": request, "response": response, "claims": claims}
    elif kind == "source_failed":
        if state["format_version"] != 4:
            _error()
        proof = _stable_source_control(_source_control_pending(proof, state))
        if proof["receipt"] is None or proof != _stable_source_control(state["source_control_pending"]):
            _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
    else:
        _error()
    return {"format_version": 1, "kind": kind, "core_sha256": _source_digest(core),
            "proof_sha256": _source_digest(proof), "proof": proof}


def validate_source_history(value):
    """Pure closed history3/4/5 representation, without permanent authority."""
    with _sanitized():
        _bounded(value, maximum=MAX_STATE_BYTES, nodes=MAX_STATE_NODES, depth=16)
        if type(value) is not dict or type(value.get("format_version")) is not int or value["format_version"] not in (3, 4, 5):
            _error()
        _exact(value, {3: _HISTORY_FIELDS, 4: _HISTORY_V4_FIELDS, 5: _HISTORY_V5_FIELDS}[value["format_version"]])
        result = deepcopy(value)
        reason = result.pop("reason")
        terminal = result.pop("source_terminal")
        if reason not in {"recovery", "claim_changed", "epoch_changed", "budget_resumed", "source_published", "source_failed"}:
            _error()
        if reason == "source_failed" and value["format_version"] not in (4, 5):
            _error()
        result.update(session_id="00000000-0000-4000-8000-000000000001", lease=None, seeded=False,
            published_version=None, published_content_sha256=None, published_reports_sha256=None,
            applied_recovery_generation=0,
            status={"state": "idle", "error_code": None, "updated_at": "2000-01-01T00:00:00Z",
                    "last_success_at": None, "retry_at": None})
        _validate(result)
        if terminal is not None:
            if value["format_version"] == 5:
                _source_terminal2(terminal, result)
                if reason != terminal["kind"]:
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            else:
                _exact(terminal, _TERMINAL_FIELDS)
                if type(terminal["format_version"]) is not int or terminal["format_version"] != 1:
                    _error()
                expected = _source_terminal(terminal["kind"], terminal["proof"], result)
                if terminal != expected or reason != terminal["kind"]:
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        elif reason in {"budget_resumed", "source_published", "source_failed"}:
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        return deepcopy(value)


def validate_source_history4(value):
    """Explicit pure state4 history entry; earlier history contracts stay exact."""
    with _sanitized():
        if type(value) is not dict or type(value.get("format_version")) is not int or value["format_version"] != 4:
            _error()
        return validate_source_history(value)


def _source_terminal2(value, state):
    """Representation only; permanent readers resolve the public-row SHA."""
    _exact(value, _TERMINAL_V2_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _error()
    expected = _source_terminal5_shape(value["kind"], value["proof"], state)
    if any(value[name] != expected[name] for name in ("kind", "core_sha256", "proof_sha256", "proof")):
        _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
    _source_sha(value["authority_sha256"])
    authority = state["source_authority"]
    if authority is None:
        _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
    name = {"budget_resumed": "resume", "source_failed": "failure", "source_published": "publication"}[value["kind"]]
    ref = value["authority_ref"]
    _source_ref(ref, kind="publication" if name == "publication" else "control")
    expected_id = (value["proof"]["resume_request"]["request_id"] if name == "resume" else
        value["proof"]["request"]["request_id"] if name == "failure" else value["proof"]["request"]["binding"]["publication_id"])
    if ref != authority[name] or ref["record_id"] != expected_id:
        _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")


def _source_terminal5_shape(kind, proof, state):
    """Pure current-creation representation; real permanent rows grant effects.

    A preserved core keeps its original identity through targeted readmission.
    Its later control/publication therefore need not use the core's old epoch
    and fence. The lifecycle observer must resolve the creation basis and exact
    committed public-row SHA; this parser cannot supply that authority.
    """
    core = state["source_execution"]
    if state["format_version"] != 5 or core is None:
        _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
    if kind == "budget_resumed":
        proof = _stable_pending(_budget_pending(proof, state))
        if proof["resume_receipt"] is None or proof != _stable_pending(state["budget_pending"]):
            _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
    elif kind == "source_failed":
        proof = _stable_source_control(_source_control_pending(proof, state))
        if proof["receipt"] is None or proof != _stable_source_control(state["source_control_pending"]):
            _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
    elif kind == "source_published":
        from . import sync_no_resend
        _exact(proof, {"request", "response", "claims"})
        request = proof["request"]
        _exact(request, sync_no_resend._REQUEST)
        contract = sync_no_resend.validate_contract({key: request[key] for key in sync_no_resend._CONTRACT},
                                                    binding=state["binding"])
        if contract["ledger_id"] != state["sender_contract"]["ledger_id"]:
            _error("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        request = sync_no_resend.validate_request(request, contract)
        response = sync_no_resend.normalize_response(request, proof["response"])
        if request["reason"] != "published_receipt" or response["state"] != "published":
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        claims = proof["claims"]
        if type(claims) is not list:
            _error()
        claims = _bindings(claims, {entry.get("video_id") for entry in claims if type(entry) is dict})
        origin = next((entry.get("origin") for entry in claims if entry["claim"]["item_id"] == core["item_id"]), None)
        if (origin is None or hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]
                or origin["source_library_id"] != core["binding"]["home_library_id"]
                or origin["source_node_id"] != core["binding"]["home_node_id"]
                or not core["local_epoch"] <= request["binding"]["source_version"]["library_epoch"] <= state["local_epoch"]):
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        proof = {"request": request, "response": response, "claims": claims}
    else:
        _error()
    return {"format_version": 1, "kind": kind, "core_sha256": _source_digest(core),
            "proof_sha256": _source_digest(proof), "proof": proof}


def validate_source_history5(value):
    """Exact history5/terminal2 representation; no actual record/SQL grant."""
    with _sanitized():
        if type(value) is not dict or type(value.get("format_version")) is not int or value["format_version"] != 5:
            _error()
        return validate_source_history(value)


def _source_history_name(value):
    terminal = value["source_terminal"]
    if terminal is None:
        return uuid.uuid4().hex + ".json"
    version = terminal["format_version"]
    identity = "source-terminal:v" + str(version) + ":" + terminal["kind"] + ":" + terminal["core_sha256"] + ":" + terminal["proof_sha256"]
    if version == 2:
        identity += ":" + terminal["authority_sha256"]
    return uuid.uuid5(uuid.UUID(value["source_execution"]["continuation_id"]), identity).hex + ".json"


def _validate_legacy_history(value):
    fields = _HISTORY_FIELDS - {"sender_contract", "publication_media", "uploaded_media",
                                "source_execution", "budget_pending", "source_terminal"}
    allowed = (fields, fields - {"legacy_pending_stream"}) if value["format_version"] == 1 else (
        fields | {"sender_contract", "publication_media", "uploaded_media"},)
    if set(value) not in allowed or value["reason"] not in {"recovery", "claim_changed", "epoch_changed"}:
        _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
    state = deepcopy(value)
    state.pop("reason")
    if "legacy_pending_stream" not in state:
        state["legacy_pending_stream"] = deepcopy(state["inbound"]["stream"]) if state["pending_publication"] is not None else None
    if type(state["recovery"]) is dict and "stream" not in state["recovery"]:
        stream = state["inbound"]["stream"]
        if stream is None:
            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        state["recovery"]["stream"] = deepcopy(stream)
        if state["recovery_attempt"] is not None:
            state["recovery_attempt"]["stream"] = deepcopy(stream)
    state.update(session_id="00000000-0000-4000-8000-000000000001", lease=None, seeded=False,
        published_version=None, published_content_sha256=None, published_reports_sha256=None,
        uploaded_media=state.get("uploaded_media", {}), applied_recovery_generation=0,
        status={"state": "idle", "error_code": None, "updated_at": "2000-01-01T00:00:00Z",
                "last_success_at": None, "retry_at": None})
    _validate(state)


def _validate(value):
    encoded = _bounded(value, maximum=MAX_STATE_BYTES, nodes=MAX_STATE_NODES, depth=16)
    version = value.get("format_version") if type(value) is dict else None
    if type(version) is not int or version not in (1, 2, 3, 4, 5):
        _error()
    _exact(value, {1: _FIELDS, 2: _V2_FIELDS, 3: _V3_FIELDS, 4: _V4_FIELDS, 5: _V5_FIELDS}[version])
    _binding(value["binding"])
    if version >= 2:
        _exact(value["sender_contract"], {"version", "ledger_id"})
        if type(value["sender_contract"]["version"]) is not int or value["sender_contract"]["version"] != 1:
            _error()
        _uuid(value["sender_contract"]["ledger_id"])
        if type(value["publication_media"]) is not dict or len(value["publication_media"]) > 4096:
            _error()
        from .sync_upload_pins import normalize_binding
        for publication_id, item in value["publication_media"].items():
            _uuid(publication_id)
            _exact(item, {"binding", "media"})
            normalized = normalize_binding(item["binding"])
            if (normalized["publication_id"] != publication_id
                    or normalized["source_node_id"] != value["binding"]["home_node_id"]
                    or normalized["source_version"]["library_id"] != value["binding"]["home_library_id"]
                    or normalized["recovery_stream"]["library_id"] != value["binding"]["target_library_id"]
                    or type(item["media"]) is not dict):
                _error()
            for video_id, descriptor in item["media"].items():
                if protocol.validate_media_descriptor(descriptor)["video_id"] != video_id:
                    _error()
    _integer(value["local_epoch"])
    if version >= 3:
        if value["source_execution"] is not None:
            _source_execution(value["source_execution"], value)
        if value["budget_pending"] is not None:
            _budget_pending(value["budget_pending"], value)
    if version == 4:
        # Restore admission has no implemented contract in this batch.
        if value["source_admission"] is not None:
            _error("SYNC_STATE_SOURCE_ADMISSION_UNSUPPORTED")
        if value["source_control_pending"] is not None:
            _source_control_pending(value["source_control_pending"], value)
    elif version == 5:
        _source_readmit_refs(value)
        if value["source_control_pending"] is not None:
            _source_control_pending(value["source_control_pending"], value)
    _integer(value["applied_recovery_generation"], minimum=0)
    _cursor({"stream": value["legacy_pending_stream"], "after": 0, "through": None})
    if (value["legacy_pending_stream"] is not None
            and value["legacy_pending_stream"]["library_id"] != value["binding"]["target_library_id"]):
        _error()
    if value["recovery"] is not None:
        _recovery(value["recovery"], value["binding"]["home_library_id"], value["binding"]["target_library_id"])
        if value["applied_recovery_generation"] > value["recovery"]["generation"]:
            _error()
    attempt = value["recovery_attempt"]
    if attempt is not None:
        _exact(attempt, ("generation", "publication_id", "version", "stream"))
        _integer(attempt["generation"], minimum=0)
        _cursor({"stream": attempt["stream"], "after": 0, "through": None})
        if attempt["stream"] is None or attempt["stream"]["library_id"] != value["binding"]["target_library_id"]:
            _error()
        _uuid(attempt["publication_id"])
        sync_snapshot._version(attempt["version"])
        if attempt["version"]["library_id"] != value["binding"]["home_library_id"]:
            _error()
    _uuid(value["session_id"])
    lease = value["lease"]
    if lease is not None:
        _exact(lease, ("session_id", "lease_token", "generation", "lease_until"))
        _uuid(lease["session_id"])
        _uuid(lease["lease_token"])
        _integer(lease["generation"])
        _timestamp(lease["lease_until"])
    _cursor(value["inbound"])
    _cursor(value["outbound"])
    if type(value["seeded"]) is not bool or type(value["claims"]) is not list or len(value["claims"]) > 50:
        _error()
    seen = set()
    for entry in value["claims"]:
        _exact(entry, ("item_id", "url", "attempts", "lease_until", "claim"))
        _integer(entry["item_id"])
        _integer(entry["attempts"])
        _timestamp(entry["lease_until"])
        protocol.validate_ingest_url(entry["url"])
        _claim(entry["claim"])
        if entry["item_id"] != entry["claim"]["item_id"] or entry["item_id"] in seen:
            _error()
        seen.add(entry["item_id"])
    work = value["work"]
    if work is not None:
        required = {"item_id", "lease_generation", "video_id", "phase"}
        allowed = (required, required | {"source_mapping_id"})
        if version >= 2:
            allowed += (required | {"source_mapping_generation"}, required | {"source_mapping_id", "source_mapping_generation"})
            allowed += (required | {"source_mapping_generation", "source_mapping_inputs"},
                        required | {"source_mapping_id", "source_mapping_generation", "source_mapping_inputs"})
        if type(work) is not dict or set(work) not in allowed:
            _error()
        _integer(work["item_id"])
        _integer(work["lease_generation"])
        protocol.validate_video_id(work["video_id"])
        if work["phase"] not in {"downloaded", "processed", "judged", "reported"}:
            _error()
        if "source_mapping_id" in work and (type(work["source_mapping_id"]) is not str or not _SHA.fullmatch(work["source_mapping_id"])):
            _error()
        if "source_mapping_generation" in work:
            _uuid(work["source_mapping_generation"])
        if "source_mapping_inputs" in work:
            from .sync_sources import _validate_resolution
            inputs = work["source_mapping_inputs"]
            _exact(inputs, {"resolution", "extracted_video_id", "media_sha256", "recorded_at"})
            _validate_resolution(inputs["resolution"])
            if type(inputs["extracted_video_id"]) is not str or not inputs["extracted_video_id"] or len(inputs["extracted_video_id"]) > 256:
                _error()
            if type(inputs["media_sha256"]) is not str or not _SHA.fullmatch(inputs["media_sha256"]):
                _error()
            _timestamp(inputs["recorded_at"])
    if value["pending_publication"] is not None:
        _metadata(value["pending_publication"])
    if value["published_version"] is not None:
        sync_snapshot._version(value["published_version"])
    for key in ("published_content_sha256", "published_reports_sha256"):
        published_digest = value[key]
        if published_digest is not None and (type(published_digest) is not str or not _SHA.fullmatch(published_digest)):
            _error()
    if type(value["uploaded_media"]) is not dict:
        _error()
    for video_id, descriptor in value["uploaded_media"].items():
        protocol.validate_video_id(video_id)
        if protocol.validate_media_descriptor(descriptor)["video_id"] != video_id:
            _error()
    status = value["status"]
    _exact(status, ("state", "error_code", "updated_at", "last_success_at", "retry_at"))
    for key in ("state", "error_code"):
        if key == "error_code" and status[key] is None:
            continue
        if type(status[key]) is not str or not _LABEL.fullmatch(status[key]):
            _error()
    _timestamp(status["updated_at"])
    for key in ("last_success_at", "retry_at"):
        if status[key] is not None:
            _timestamp(status[key])
    return encoded


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            _error()
        value[key] = child
    return value


def _decode(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=lambda _: _error())


def _observation_stream(directory, name):
    """Pin one atomic state image without taking the writer's thread lock."""
    media._entry_name(name)
    if os.name != "nt":
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.fd)
        try:
            info = os.fstat(fd)
            media._safe_stat(info, directory=False)
            if info.st_nlink > 1:
                _error("SYNC_STATE_UNSAFE_PATH")
        except BaseException:
            try:
                os.close(fd)
            except BaseException:
                pass
            raise
        return os.fdopen(fd, "rb")
    handle = media._kernel.CreateFileW(media._native_path(directory.path / name),
        0x80000000, 5, None, 3, 0x00200000, None)  # READ, share READ|DELETE, NO_REPARSE
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = media._HandleInfo()
        if not media._kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (media._REPARSE | 0x10) or info.links > 1:
            _error("SYNC_STATE_UNSAFE_PATH")
        fd = media.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
    except BaseException:
        try:
            media._kernel.CloseHandle(handle)
        except BaseException:
            pass
        raise
    return os.fdopen(fd, "rb")


def _read_bytes(directory, name, maximum, *, observation=False):
    stream_first = None
    try:
        with (_observation_stream(directory, name) if observation else directory.open_file(name)) as stream:
            try:
                before = os.fstat(stream.fileno())
                if os.name != "nt" and before.st_mode & 0o077:
                    _error("SYNC_STATE_UNSAFE_PATH")
                if before.st_size > maximum:
                    _error()
                data = stream.read(maximum + 1)
                after = os.fstat(stream.fileno())
                # Replacing the pathname can change the old opened inode's link/ctime
                # metadata; its immutable image remains valid. In-place writes are
                # denied by the Windows handle; content size/mtime remain checked.
                signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                           None if observation else info.st_ctime_ns)
                if len(data) > maximum or signature(before) != signature(after):
                    _error()
            except BaseException as error:
                stream_first = error
                raise
    except BaseException as error:
        if stream_first is None or error is stream_first:
            raise
    else:
        stream_first = None
    if stream_first is not None:
        raise stream_first
    directory.verify()
    return data


def _replace(directory, temporary, target, owned):
    """Pinned parent plus no-follow source handle on Windows; fd-relative on POSIX."""
    directory.verify()
    info = directory.entry_stat(temporary)
    media._safe_stat(info, directory=False)
    if info.st_nlink != 1 or (info.st_dev, info.st_ino) != owned:
        _error("SYNC_STATE_UNSAFE_PATH")
    try:
        destination = directory.entry_stat(target)
    except FileNotFoundError:
        _error("SYNC_STATE_MISSING")
    media._safe_stat(destination, directory=False)
    if destination.st_nlink != 1:
        _error("SYNC_STATE_UNSAFE_PATH")
    if os.name == "nt":
        from ctypes import wintypes
        class RenameInfo(ctypes.Structure):
            _fields_ = [("flags", wintypes.DWORD), ("directory", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", wintypes.WCHAR * len(target))]
        # FileRenameInformationEx keeps already-open observation handles on
        # their old immutable image, while atomically replacing the pathname.
        value = RenameInfo(3, directory.handles[-1], len(target.encode("utf-16-le")), target)
        fd = media._win_open(directory.path / temporary, delete=True)
        try:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != owned:
                _error("SYNC_STATE_UNSAFE_PATH")
            result = media._nt.NtSetInformationFile(media.msvcrt.get_osfhandle(fd), ctypes.byref(media._IoStatus()),
                                                     ctypes.byref(value), ctypes.sizeof(value), 65)
            if result < 0:
                raise ctypes.WinError(media._nt.RtlNtStatusToDosError(result))
        except BaseException:
            try:
                # One close attempt; a later cleanup failure cannot replace
                # the active operation error or cancellation.
                os.close(fd)
            except BaseException:
                pass
            raise
        else:
            # After operation success, this sole close failure is the first.
            os.close(fd)
    else:
        os.replace(temporary, target, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)


def _write(directory, name, data, *, immutable=False):
    temporary = f".sync-{uuid.uuid4().hex}.part"
    owned, failure = None, None
    try:
        stream_first = None
        try:
            with directory.open_file(temporary, write=True, create=True) as stream:
                try:
                    info = os.fstat(stream.fileno())
                    owned = (info.st_dev, info.st_ino)
                    if stream.write(data) != len(data):
                        raise OSError(errno.EIO, "Incomplete state write")
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException as error:
                    stream_first = error
                    raise
        except BaseException as error:
            if stream_first is None or error is stream_first:
                raise
        else:
            stream_first = None
        if stream_first is not None:
            raise stream_first
        directory.verify()
        current = directory.entry_stat(temporary)
        media._safe_stat(current, directory=False)
        if current.st_nlink != 1 or (current.st_dev, current.st_ino) != owned:
            _error("SYNC_STATE_UNSAFE_PATH")
        if immutable:
            media._rename_new(directory, temporary, directory, name)
        else:
            _replace(directory, temporary, name, owned)
        if os.name != "nt":
            os.fsync(directory.fd)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            info = directory.entry_stat(temporary)
            media._safe_stat(info, directory=False)
            if info.st_nlink != 1 or (info.st_dev, info.st_ino) != owned:
                _error("SYNC_STATE_UNSAFE_PATH")
            directory.unlink(temporary)
        except FileNotFoundError:
            pass
        except BaseException:
            if failure is None:
                raise


@contextmanager
def _existing_lock_stream(directory):
    """Original State ownership with OPEN_EXISTING, never a replacement lock."""
    from . import storage_source as physical
    before = physical.file_signature(directory.entry_stat(_LOCK))
    if os.name == "nt":
        # Keep the released State lock's share READ|WRITE contender policy;
        # only its creating disposition changes to OPEN_EXISTING here.
        handle = media._kernel.CreateFileW(media._native_path(directory.path / _LOCK),
            0xC0000000, 3, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = media._HandleInfo()
            if not media._kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & (media._REPARSE | 0x10) or info.links != 1:
                _error("SYNC_STATE_UNSAFE_PATH")
            fd = media.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDWR)
        except BaseException:
            try:
                media._kernel.CloseHandle(handle)
            except BaseException:
                pass
            raise
    else:
        fd = os.open(_LOCK, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory.fd)
    stream_first = None
    try:
        with os.fdopen(fd, "r+b", buffering=0) as stream:
            try:
                opened = physical.file_signature(os.fstat(stream.fileno()))
                if before[:4] != opened[:4]:
                    _error("SYNC_STATE_UNSAFE_PATH")
                directory.verify()
                yield stream
                if (physical.file_signature(directory.entry_stat(_LOCK)) != before
                        or physical.file_signature(os.fstat(stream.fileno())) != opened):
                    _error("SYNC_STATE_UNSAFE_PATH")
                directory.verify()
            except BaseException as error:
                stream_first = error
                raise
    except BaseException as error:
        if stream_first is None or error is stream_first:
            raise
    else:
        stream_first = None
    if stream_first is not None:
        raise stream_first


class StateStore:
    def __init__(self, data_dir, *, profile=None, check_cancelled=None):
        self.data_dir = Path(os.path.abspath(os.fspath(data_dir)))
        if profile is not None:
            lifecycle._profile(profile)
            if lifecycle._key(self.data_dir) != lifecycle._key(profile.database.parent):
                raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
        if check_cancelled is not None and not callable(check_cancelled):
            raise lifecycle.StorageLifecycleError("STORAGE_INVALID_ARGUMENT", 400)
        self.profile = profile
        self.check_cancelled = check_cancelled
        self._cancelled_exception = None
        self._reference_depth = 0
        self._reference_frame = None
        self._guard = threading.RLock()
        self._directory = None

    @contextmanager
    def exclusive(self, *, existing_only=False):
        """Nonblocking State OS lock, optionally refusing all creating opens.

        The default retains the released initialization behavior. Read-only
        administration uses existing_only for both DATA and its original lock.
        The thread lock is released during caller work in either mode.
        """
        if type(existing_only) is not bool:
            _error("SYNC_STATE_UNSAFE_PATH")
        stack = ExitStack()
        with _sanitized():
            with self._guard:
                if self._directory is not None:
                    _error("SYNC_BUSY")
                try:
                    directory = stack.enter_context(media._Directory(self.data_dir, create=not existing_only))
                    stream = stack.enter_context(_existing_lock_stream(directory) if existing_only
                        else directory.open_file(_LOCK, write=True, lock=True))
                    try:
                        if os.name == "nt":
                            stream.seek(0)
                            media.msvcrt.locking(stream.fileno(), media.msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(exc, "winerror", None) == 33:
                            _error("SYNC_BUSY")
                        raise
                    self._directory = directory
                except BaseException:
                    try:
                        stack.close()
                    except BaseException:
                        pass  # Setup failure precedes every cleanup failure.
                    raise
        primary = None
        try:
            yield self
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                with self._guard, _sanitized():
                    self._directory = None
                    cleanup_first = None
                    try:
                        if os.name == "nt":
                            stream.seek(0)
                            media.msvcrt.locking(stream.fileno(), media.msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    except BaseException as failure:
                        cleanup_first = failure
                    try:
                        if primary is not None:
                            stack.__exit__(type(primary), primary, BaseException.__dict__["__traceback__"].__get__(primary))
                        else:
                            stack.close()
                    except BaseException as failure:
                        if cleanup_first is None:
                            cleanup_first = failure
                    if cleanup_first is not None:
                        raise cleanup_first
            except BaseException:
                if primary is None:
                    raise
                try:
                    BaseException.add_note(primary, "SYNC_STATE_LOCK_CLEANUP_FAILED")
                except BaseException:
                    pass  # Optional annotation must not replace caller first.

    @contextmanager
    def reference_operation(self):
        """State ownership precedes SH; retain it across a reference publication.

        The process ownership may already cover a synchronization cycle. The
        RLock remains short-lived except for local proof/freeze operations; no
        network or pipeline work belongs in this context. Callback exceptions
        are outside the filesystem sanitizer and retain their identity.
        """
        if self.profile is None:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED")
        lifecycle._profile(self.profile)
        if lifecycle._key(self.data_dir) != lifecycle._key(self.profile.database.parent):
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
        def check():
            try:
                if self.check_cancelled is not None:
                    self.check_cancelled()
            except BaseException as exc:
                self._cancelled_exception = exc
                raise
        with self._guard, ExitStack() as stack:
            if self._reference_depth == 0:
                self._cancelled_exception = None
                self._reference_frame = object()
            self._reference_depth += 1
            try:
                check()
                if self._directory is None:
                    stack.enter_context(self.exclusive())
                stack.enter_context(lifecycle.shared(self.profile, check_cancelled=check))
                yield self
                check()
            finally:
                self._reference_depth -= 1
                if self._reference_depth == 0:
                    self._cancelled_exception = None
                    self._reference_frame = None

    @contextmanager
    def reference_lifetime(self):
        """Hold SH for local proof/publication under existing OS ownership.

        Unlike a single read-modify-write operation, long SHA/export work does
        not hold the thread RLock. Heartbeats can merge recovery observations
        between atomic mutations. Callers must retain exclusive() throughout.
        """
        if self.profile is None:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED")
        lifecycle._profile(self.profile)
        if lifecycle._key(self.data_dir) != lifecycle._key(self.profile.database.parent):
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
        with ExitStack() as stack:
            with self._guard:
                if self._directory is None:
                    _error("SYNC_STATE_OWNERSHIP_REQUIRED")
                stack.enter_context(lifecycle.shared(self.profile, check_cancelled=self.check_cancelled))
            yield self
            if self.check_cancelled is not None:
                self.check_cancelled()

    @contextmanager
    def _mutating(self):
        with self.reference_operation():
            with _sanitized(cancelled=lambda: self._cancelled_exception):
                yield self._directory

    def _read(self, directory, *, observation=False):
        try:
            value = _decode(_read_bytes(directory, _STATE, MAX_STATE_BYTES, observation=observation))
        except FileNotFoundError:
            _error("SYNC_STATE_MISSING")
        # Old state remains readable without a write, including browser status.
        if type(value) is dict and set(value) == _FIELDS - _RECOVERY_DEFAULTS.keys():
            legacy_stream = deepcopy(value.get("inbound", {}).get("stream"))
            value.update(deepcopy(_RECOVERY_DEFAULTS))
            value["legacy_pending_stream"] = legacy_stream if value.get("pending_publication") is not None else None
        elif type(value) is dict and set(value) == _FIELDS - {"legacy_pending_stream"}:
            value["legacy_pending_stream"] = (deepcopy(value.get("inbound", {}).get("stream"))
                                               if value.get("pending_publication") is not None else None)
        # Early recovery state had no receiver epoch namespace. Only its saved
        # inbound stream can prove that namespace; never infer from a new reply.
        if type(value) is dict and type(value.get("recovery")) is dict and "stream" not in value["recovery"]:
            stream = value.get("inbound", {}).get("stream")
            if stream is None:
                value.update(deepcopy(_RECOVERY_DEFAULTS))
            else:
                value["recovery"]["stream"] = deepcopy(stream)
                if value.get("recovery_attempt") is not None:
                    value["recovery_attempt"]["stream"] = deepcopy(stream)
        _validate(value)
        return value

    def initialize(self, binding, local_epoch):
        with _sanitized():
            binding = _binding(binding, normalize=True)
            _integer(local_epoch)
        with self._mutating() as directory:
            try:
                state = self._read(directory)
            except SyncError as exc:
                if exc.code != "SYNC_STATE_MISSING":
                    raise
                state = {**deepcopy(_RECOVERY_DEFAULTS), "format_version": 1, "binding": binding, "local_epoch": local_epoch,
                         "session_id": str(uuid.uuid4()), "lease": None,
                         "inbound": {"stream": None, "after": 0, "through": None},
                         "outbound": {"stream": None, "after": 0, "through": None},
                         "seeded": False, "claims": [], "work": None, "pending_publication": None,
                         "published_version": None, "published_content_sha256": None,
                         "published_reports_sha256": None, "uploaded_media": {},
                         "status": {"state": "idle", "error_code": None,
                                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                                    "last_success_at": None, "retry_at": None}}
                _write(directory, _STATE, _validate(state), immutable=True)
            else:
                if state["binding"] != binding:
                    _error("SYNC_STATE_BINDING_MISMATCH")
                if local_epoch < state["local_epoch"]:
                    _error("SYNC_STATE_EPOCH_MISMATCH")
                if local_epoch > state["local_epoch"]:
                    state["local_epoch"] = local_epoch
                    _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def read(self):
        with self._guard, _sanitized():
            try:
                with media._Directory(self.data_dir) as directory:
                    return deepcopy(self._read(directory))
            except FileNotFoundError:
                _error("SYNC_STATE_MISSING")

    def read_observation(self):
        """One bounded atomic image, without the writer's RLock or mutation.

        File-protected SHA/upload callbacks use this to observe recovery fences
        without reversing state ownership -> SH. The Windows handle permits
        atomic replacement of its image while continuing to deny writes.
        """
        with _sanitized():
            try:
                with media._Directory(self.data_dir) as directory:
                    return deepcopy(self._read(directory, observation=True))
            except FileNotFoundError:
                _error("SYNC_STATE_MISSING")

    def update(self, changes):
        with self._mutating() as directory:
            if type(changes) is not dict or not set(changes) <= _V2_FIELDS:
                _error()
            state = self._read(directory)
            if "binding" in changes and changes["binding"] != state["binding"]:
                _error("SYNC_STATE_BINDING_MISMATCH")
            if "format_version" in changes and (type(changes["format_version"]) is not int or changes["format_version"] != state["format_version"]):
                _error()
            if "sender_contract" in changes and changes["sender_contract"] != state.get("sender_contract"):
                _error("SYNC_NO_RESEND_CONTRACT_MISMATCH")
            if "local_epoch" in changes:
                _integer(changes["local_epoch"])
                if changes["local_epoch"] < state["local_epoch"]:
                    _error("SYNC_STATE_EPOCH_MISMATCH")
            for key in ("pending_publication", "recovery_attempt"):
                incoming = changes.get(key)
                if incoming is not None and incoming != state[key]:
                    self.assert_sendable(incoming["publication_id"])
            if changes.get("work") is not None:
                self._assert_work_transition(state["work"], changes["work"])
            self._assert_cache_transition(state, changes)
            if "uploaded_media" in changes and changes["uploaded_media"] != state["uploaded_media"] and self.has_sender_contract(state=state):
                _error("SYNC_NO_RESEND_LEGACY_CACHE_PROTECTED")
            state.update(changes)
            state = self._detach_retired_caches(state)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def enable_source_execution(self):
        """Explicit local v3 capability; reads never perform this promotion."""
        self.enable_no_resend()
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] >= 3:
                return deepcopy(state)
            state.update(format_version=3, source_execution=None, budget_pending=None)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def install_source_execution(self, core):
        """Save the trusted caller's original core, never fabricate job facts."""
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] < 3:
                _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
            core = _source_execution(core, state)
            existing = state["source_execution"]
            if existing is not None:
                if existing != core:
                    _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
                return deepcopy(existing)
            recovery = state["recovery"]
            if (core["local_epoch"] != state["local_epoch"] or recovery is None
                    or core["recovery_generation"] != recovery["generation"]
                    or core["recovery_stream"] != recovery["stream"]):
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            state["source_execution"] = core
            _write(directory, _STATE, _validate(state))
            return deepcopy(core)

    def assert_source_execution(self, expected=None):
        """Read original persistence proof; caller still checks live ownership."""
        with _sanitized():
            state = self.read_observation()
            core = state.get("source_execution")
            if state["format_version"] < 3 or core is None:
                _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
            if expected is not None and _source_execution(expected, state) != core:
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            return deepcopy(core)

    def _install_source_genesis17(self, core, origin_ref, *, producer):
        """One sealed CAS publishes the original core and permanent origin ref.

        Representation-only State5 promotion and the old public core setter do
        not grant this transition. The actual first-origin factory has already
        committed the precise origin; its live producer checks this same State,
        owned SQL frame and original transport proof before and after the write.
        """
        from .sync_source_genesis17 import Genesis17Guard
        with self._mutating() as directory:
            state = self._read(directory)
            if type(producer) is not Genesis17Guard or producer.store is not self:
                _error("SYNC_STATE_SOURCE_GENESIS_REQUIRED")
            if state["format_version"] != 5:
                _error("SYNC_STATE_SOURCE_READMIT_REQUIRED")
            core = _source_execution(core, state)
            _source_ref(origin_ref, kind="origin")
            producer.check_state_install(core, origin_ref, state)
            authority = {"format_version": 1, "core_sha256": _source_digest(core),
                "origin": deepcopy(origin_ref), "defer": None, "resume": None,
                "failure": None, "publication": None}
            if state["source_execution"] is not None:
                if state["source_execution"] != core or state["source_authority"] != authority:
                    _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
                return deepcopy(core)
            if any(state[name] is not None for name in
                    ("source_authority", "source_admission", "source_recovery_pending", "source_control_pending", "budget_pending")):
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            state.update(source_execution=core, source_authority=authority)
            _write(directory, _STATE, _validate(state))
            producer.check_state_install(core, origin_ref, self._read(directory))
            return deepcopy(core)

    def assert_budget_pending(self):
        with _sanitized():
            state = self.read_observation()
            pending = state.get("budget_pending")
            if state["format_version"] < 3 or pending is None:
                _error("SYNC_STATE_BUDGET_PENDING_REQUIRED")
            return _budget_pending(pending, state)

    def _install_budget_request(self, request, *, resume):
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] < 3 or state["source_execution"] is None:
                _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
            body = normalize_budget_request(request)
            if body["kind"] != ("budget_resume" if resume else "budget_defer"):
                _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
            pending = deepcopy(state["budget_pending"])
            if pending is None:
                if resume:
                    _error("SYNC_STATE_BUDGET_PENDING_REQUIRED")
                pending = {"defer_request": body, "defer_receipt": None,
                           "resume_request": None, "resume_receipt": None}
            else:
                field = "resume_request" if resume else "defer_request"
                if pending[field] is not None and pending[field] != body:
                    _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
                if pending[field] == body:
                    return _budget_pending(pending, state)
                pending[field] = body
            state["budget_pending"] = _budget_pending(pending, state)
            _write(directory, _STATE, _validate(state))
            return deepcopy(pending)

    def install_budget_defer_request(self, request):
        return self._install_budget_request(request, resume=False)

    def install_budget_resume_request(self, request):
        return self._install_budget_request(request, resume=True)

    def _record_budget_receipt(self, request_id, receipt, *, resume):
        with self._mutating() as directory:
            state = self._read(directory)
            pending = deepcopy(state.get("budget_pending"))
            if state["format_version"] < 3 or pending is None:
                _error("SYNC_STATE_BUDGET_PENDING_REQUIRED")
            field = "resume" if resume else "defer"
            body = pending[field + "_request"]
            if body is None or request_id != body["request_id"]:
                _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
            proof = normalize_budget_receipt(body, receipt,
                defer_receipt=pending["defer_receipt"] if resume else None)
            existing = pending[field + "_receipt"]
            if existing is not None and not _same_receipt(existing, proof):
                _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
            if existing == proof:
                return deepcopy(pending)
            pending[field + "_receipt"] = proof
            state["budget_pending"] = _budget_pending(pending, state)
            _write(directory, _STATE, _validate(state))
            return deepcopy(pending)

    def record_budget_defer_receipt(self, request_id, receipt):
        return self._record_budget_receipt(request_id, receipt, resume=False)

    def record_budget_resume_receipt(self, request_id, receipt):
        return self._record_budget_receipt(request_id, receipt, resume=True)

    def enable_source_control(self):
        """Explicit local state4 promotion; current restore admission stays absent."""
        self.enable_source_execution()
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] >= 4:
                return deepcopy(state)
            state.update(format_version=4, source_control_pending=None, source_admission=None)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def enable_source_readmit(self):
        """Explicit representation-only State5 promotion; never fill old refs."""
        self.enable_source_control()
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] == 5:
                return deepcopy(state)
            state.update(format_version=5, source_authority=None, source_recovery_pending=None)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def assert_source_control_pending(self):
        with _sanitized():
            state = self.read_observation()
            pending = state.get("source_control_pending")
            if state["format_version"] != 4 or pending is None:
                _error("SYNC_STATE_SOURCE_CONTROL_REQUIRED")
            return _source_control_pending(pending, state)

    def install_source_control_request(self, request):
        """Save only the caller's exact original authorization before transport."""
        from . import sync_source_control_contract as control
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] != 4 or state["source_execution"] is None:
                _error("SYNC_STATE_SOURCE_EXECUTION_REQUIRED")
            body = control.normalize_request(request)
            pending = {"core_sha256": body["core_sha256"], "request": body, "receipt": None}
            pending = _source_control_pending(pending, state)
            existing = state["source_control_pending"]
            if existing is not None:
                if existing["request"] != body or existing["core_sha256"] != pending["core_sha256"]:
                    _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
                return deepcopy(existing)
            state["source_control_pending"] = pending
            _write(directory, _STATE, _validate(state))
            return deepcopy(pending)

    def record_source_control_receipt(self, request_id, receipt):
        from . import sync_source_control_contract as control
        with self._mutating() as directory:
            state = self._read(directory)
            pending = deepcopy(state.get("source_control_pending"))
            if state["format_version"] != 4 or pending is None:
                _error("SYNC_STATE_SOURCE_CONTROL_REQUIRED")
            if request_id != pending["request"]["request_id"]:
                _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
            proof = control.normalize_receipt(pending["request"], receipt)
            existing = pending["receipt"]
            if existing is not None and not _same_receipt(existing, proof):
                _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
            if existing == proof:
                return deepcopy(pending)
            pending["receipt"] = proof
            state["source_control_pending"] = _source_control_pending(pending, state)
            _write(directory, _STATE, _validate(state))
            return deepcopy(pending)

    def _source_history(self, state, reason, terminal=None):
        fields = {3: _HISTORY_FIELDS, 4: _HISTORY_V4_FIELDS, 5: _HISTORY_V5_FIELDS}[state["format_version"]]
        value = {key: deepcopy(state[key]) for key in fields - {"reason", "source_terminal"}}
        value.update(reason=reason, source_terminal=deepcopy(terminal), budget_pending=_stable_pending(state["budget_pending"]))
        if state["format_version"] >= 4:
            value["source_control_pending"] = _stable_source_control(state["source_control_pending"])
        return validate_source_history(value)

    def _write_source_history(self, root, value, *, allow_create=True):
        """An interruption keeps one immutable original history, never a new UUID."""
        data = _bounded(value, maximum=MAX_STATE_BYTES, nodes=MAX_STATE_NODES, depth=16)
        terminal = value["source_terminal"]
        name = _source_history_name(value)
        with media._Directory(self.data_dir / "sync-work-history", create=True, parent=root) as directory:
            try:
                existing = _read_bytes(directory, name, MAX_STATE_BYTES)
            except FileNotFoundError:
                if not allow_create:
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                _write(directory, name, data, immutable=True)
            else:
                original = validate_source_history(_decode(existing))
                if (terminal is None or original["source_execution"] != value["source_execution"]
                        or original["source_terminal"] != terminal
                        or original["budget_pending"] != value["budget_pending"]
                        or original.get("source_control_pending") != value.get("source_control_pending")
                        or original.get("source_admission") != value.get("source_admission")
                        or original.get("source_authority") != value.get("source_authority")
                        or original.get("source_recovery_pending") != value.get("source_recovery_pending")):
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        return name

    @staticmethod
    def _assert_source_fence(state, core):
        recovery = state["recovery"]
        if (state["local_epoch"] != core["local_epoch"] or recovery is None or recovery["state"] != "running"
                or (recovery["generation"], recovery["stream"]) != (core["recovery_generation"], core["recovery_stream"])):
            _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")

    def complete_budget_resume(self, core, pending):
        """Save original terminal proof before CAS-clearing only its budget wait."""
        with self._mutating() as directory:
            state = self._read(directory)
            if state.get("source_execution") != core or state["format_version"] < 3:
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            _source_execution(core, state)
            self._assert_source_fence(state, core)
            proof_state = {**state, "budget_pending": deepcopy(pending)}
            terminal = _source_terminal("budget_resumed", pending, proof_state)
            if state["budget_pending"] is not None and _stable_pending(state["budget_pending"]) != terminal["proof"]:
                _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
            history = self._source_history(proof_state, "budget_resumed", terminal)
            self._write_source_history(directory, history, allow_create=state["budget_pending"] is not None)
            current = self._read(directory)
            if current["source_execution"] != core or current["budget_pending"] != state["budget_pending"]:
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            self._assert_source_fence(current, core)
            if current["budget_pending"] is not None:
                current["budget_pending"] = None
                _write(directory, _STATE, _validate(current))
            return deepcopy(current)

    def complete_published_source(self, core, request):
        """Only an original permanent published receipt and its claims close core."""
        with self._mutating() as directory:
            state = self._read(directory)
            if state["format_version"] < 3 or state.get("source_execution") not in (None, core):
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            _source_execution(core, state)
            self._assert_source_fence(state, core)
            if self.no_resend_request(request["operation_id"]) != request:
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            response = self.no_resend_receipt(request["operation_id"])
            envelope = self.read_publication(request["metadata"], proved_stream=request["binding"]["recovery_stream"])
            proof = {"request": request, "response": response, "claims": envelope["claims"]}
            proof_state = {**state, "source_execution": deepcopy(core)}
            if proof_state["budget_pending"] is not None:
                _error("SYNC_STATE_BUDGET_PENDING_CONFLICT")
            terminal = _source_terminal("source_published", proof, proof_state)
            history = self._source_history(proof_state, "source_published", terminal)
            self._write_source_history(directory, history, allow_create=state["source_execution"] is not None)
            current = self._read(directory)
            if current["source_execution"] != state["source_execution"] or current["budget_pending"] is not None:
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            self._assert_source_fence(current, core)
            if current["source_execution"] is not None:
                current["source_execution"] = None
                _write(directory, _STATE, _validate(current))
            return deepcopy(current)

    def complete_source_failure(self, core, pending, *, effect_guard=None):
        """Preserve exact failed facts before clearing only an unencumbered core."""
        # State ownership and SH precede the optional short SQL effect guard.
        # It spans immutable history, exact CAS and state replacement, so a
        # database restore cannot race the last paid/owner/epoch observation.
        with self._mutating() as directory, ExitStack() as guards:
            if effect_guard is not None:
                if not callable(effect_guard):
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                guards.enter_context(effect_guard())
            state = self._read(directory)
            if state["format_version"] != 4 or state["source_execution"] not in (None, core):
                _error("SYNC_STATE_SOURCE_EXECUTION_CONFLICT")
            _source_execution(core, state)
            self._assert_source_fence(state, core)
            proof_state = {**state, "source_execution": deepcopy(core), "source_control_pending": deepcopy(pending)}
            terminal = _source_terminal("source_failed", pending, proof_state)
            if state["source_control_pending"] is not None and _stable_source_control(state["source_control_pending"]) != terminal["proof"]:
                _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
            history = self._source_history(proof_state, "source_failed", terminal)
            self._write_source_history(directory, history, allow_create=state["source_control_pending"] is not None)
            current = self._read(directory)
            if (current["source_execution"] != state["source_execution"]
                    or current["source_control_pending"] != state["source_control_pending"]):
                _error("SYNC_STATE_SOURCE_CONTROL_CONFLICT")
            self._assert_source_fence(current, core)
            # A known source failure is not a terminal proof for a separately
            # persisted budget wait or publication. Keep its original binding.
            if current["budget_pending"] is not None or current["pending_publication"] is not None:
                return deepcopy(current)
            if current["source_execution"] is not None:
                current.update(source_execution=None, source_control_pending=None)
                _write(directory, _STATE, _validate(current))
            return deepcopy(current)

    def completed_source_failure(self, *, root_job_id, continuation_id, execution_generation):
        return self._completed_source_terminal("source_failed", root_job_id=root_job_id,
            continuation_id=continuation_id, execution_generation=execution_generation)

    def completed_source_publication(self, *, root_job_id, continuation_id, execution_generation):
        return self._completed_source_terminal("source_published", root_job_id=root_job_id,
            continuation_id=continuation_id, execution_generation=execution_generation)

    def _completed_source_terminal(self, kind, *, root_job_id, continuation_id, execution_generation):
        """Read a bounded permanent terminal for exactly one unfinished job.

        This is completion evidence only. It neither restores a source core
        nor grants dispatch, resource retirement or another job's authority.
        """
        with self.reference_operation(), _sanitized():
            _integer(root_job_id)
            _uuid(continuation_id)
            _integer(execution_generation)
            state = self._read(self._directory)
            if state["format_version"] < 3 or state["source_execution"] is not None or state.get("source_control_pending") is not None:
                return None
            candidates = []
            total_bytes = 0
            with ExitStack() as history_lifetime:
                try:
                    directory = history_lifetime.enter_context(media._Directory(
                        self.data_dir / "sync-work-history", parent=self._directory))
                except FileNotFoundError:
                    return None
                else:
                    names = directory.names()
                    if len(names) > MAX_SOURCE_HISTORY_LOOKUP:
                        _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                    for name in sorted(names):
                        if not name.endswith(".json"):
                            continue
                        if re.fullmatch(r"[0-9a-f]{32}\.json", name) is None:
                            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                        raw = _read_bytes(directory, name, MAX_STATE_BYTES)
                        total_bytes += len(raw)
                        if total_bytes > MAX_SOURCE_HISTORY_LOOKUP_BYTES:
                            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                        value = _decode(raw)
                        if type(value) is not dict or type(value.get("format_version")) is not int:
                            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                        if value["format_version"] in (1, 2):
                            _validate_legacy_history(value)
                            continue  # These contracts contain no source-job completion proof.
                        value = validate_source_history(value)
                        if value["reason"] not in {"source_failed", "source_published"}:
                            continue
                        core = value["source_execution"]
                        if (core["root_job_id"], core["continuation_id"], core["execution_generation"]) != (
                                root_job_id, continuation_id, execution_generation):
                            continue
                        if value["binding"] != state["binding"] or name != _source_history_name(value):
                            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                        candidates.append(value)
                        if len(candidates) > 1:
                            _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                    directory.verify()
            if len(candidates) > 1:
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            return deepcopy(candidates[0]) if candidates and candidates[0]["reason"] == kind else None

    def observe_recovery(self, recovery, *, lease=None):
        """Merge heartbeat observations without reverting main-thread progress."""
        with self._mutating() as directory:
            state = self._read(directory)
            _recovery(recovery, state["binding"]["home_library_id"], state["binding"]["target_library_id"])
            previous = state["recovery"]
            old_stream = previous["stream"] if previous is not None else state["inbound"]["stream"]
            if old_stream is not None and recovery["stream"]["library_epoch"] < old_stream["library_epoch"]:
                return deepcopy(state)
            new_epoch = old_stream is not None and recovery["stream"]["library_epoch"] > old_stream["library_epoch"]
            if new_epoch:
                self.archive_progress("epoch_changed")
                empty = {"stream": None, "after": 0, "through": None}
                legacy_cache = state["uploaded_media"] if self.has_sender_contract(state=state) else {}
                state.update(recovery=deepcopy(recovery), applied_recovery_generation=0, recovery_attempt=None,
                             legacy_pending_stream=state["legacy_pending_stream"] if state.get("source_execution") else None,
                             pending_publication=state["pending_publication"] if state.get("source_execution") else None,
                             published_version=None, published_content_sha256=None,
                             published_reports_sha256=None, uploaded_media=legacy_cache, seeded=False,
                             inbound=dict(empty), outbound=dict(empty))
            elif previous is None or recovery["revision"] > previous["revision"]:
                if previous is not None and recovery["generation"] < previous["generation"]:
                    _error("SYNC_STATE_RECOVERY_CONFLICT")
                state["recovery"] = deepcopy(recovery)
            elif recovery["revision"] == previous["revision"] and recovery != previous:
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            if lease is not None:
                from .sync_queue import _lease
                _lease(lease)
                current = state["lease"]
                if lease["session_id"] != state["session_id"]:
                    _error()
                if current is None or new_epoch:
                    state["lease"] = deepcopy(lease)
                elif any(lease[key] != current[key] for key in ("session_id", "lease_token", "generation")):
                    _error("SYNC_STATE_RECOVERY_CONFLICT")
                elif lease["lease_until"] > current["lease_until"]:
                    state["lease"] = deepcopy(lease)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def archive_progress(self, reason):
        """Keep superseded private work/outbox references before changing them."""
        if reason not in {"recovery", "claim_changed", "epoch_changed"}:
            _error()
        with self._mutating() as root:
            state = self._read(root)
            # Explicit state-cache retirement remains authoritative after an
            # old state restore; do not copy its retired hint into a new,
            # independently rooted history container.
            state = self._detach_retired_caches(state)
            if not (state["claims"] or state["work"] or state["pending_publication"] or state["recovery_attempt"]
                    or state.get("source_execution") or state.get("budget_pending")):
                return
            if state["format_version"] >= 3:
                self._write_source_history(root, self._source_history(state, reason))
                return
            value = {"format_version": 1, "reason": reason,
                     **{key: state[key] for key in ("binding", "local_epoch", "claims", "work", "inbound", "outbound",
                                                    "pending_publication", "recovery_attempt", "recovery", "legacy_pending_stream")}}
            if self.has_sender_contract(state=state):
                from . import sync_no_resend
                contract = sync_no_resend.observe(self.profile, state=state)["contract"]
                value.update(format_version=2, sender_contract={"version": 1, "ledger_id": contract["ledger_id"]},
                             publication_media=state.get("publication_media", {}), uploaded_media=state["uploaded_media"])
            data = _bounded(value, maximum=MAX_STATE_BYTES, nodes=MAX_STATE_NODES, depth=16)
            with media._Directory(self.data_dir / "sync-work-history", create=True, parent=root) as directory:
                _write(directory, uuid.uuid4().hex + ".json", data, immutable=True)

    def acknowledge_publication(self, metadata, *, recovery_generation, recovery_stream, version,
                                content_sha256, reports_sha256, preserve_work):
        """Commit a receipt against the latest control under the heartbeat lock."""
        with self._mutating() as directory:
            state = self._read(directory)
            if state["recovery"] is not None and state["recovery"]["stream"] != recovery_stream:
                return deepcopy(state)
            if state["pending_publication"] != metadata or state["recovery"] is None:
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            _integer(recovery_generation, minimum=0)
            if recovery_generation > state["recovery"]["generation"]:
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            superseded = state["recovery"]["generation"] > recovery_generation
            if superseded:
                self.archive_progress("recovery")
            state.update(pending_publication=None, legacy_pending_stream=None, published_version=version,
                         published_content_sha256=content_sha256,
                         published_reports_sha256=reports_sha256,
                         applied_recovery_generation=max(state["applied_recovery_generation"], recovery_generation))
            attempt = state["recovery_attempt"]
            if attempt is not None and attempt["generation"] == recovery_generation:
                state["recovery_attempt"] = None
            if not preserve_work and not superseded:
                state.update(claims=[], work=None)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def install_publication(self, metadata, *, recovery_generation, recovery_stream):
        """Do not reattach an old outbox after a heartbeat changed its fence."""
        with self._mutating() as directory:
            state = self._read(directory)
            self.assert_sendable(metadata["publication_id"])
            recovery = state["recovery"]
            if (recovery is None or recovery["state"] == "held" or recovery["generation"] != recovery_generation
                    or recovery["stream"] != recovery_stream):
                return False
            state["pending_publication"] = metadata
            state["legacy_pending_stream"] = None
            _write(directory, _STATE, _validate(state))
            return True

    def _recovery_publication(self, state, *, metadata=None, observation=False, allow_claimed=False):
        """Prove the independent bootstrap marker against its immutable outbox."""
        attempt = state["recovery_attempt"]
        if attempt is None:
            return None
        saved = self.find_publication(attempt["publication_id"], audit=True, observation=observation)
        if saved is None:
            if metadata is not None:
                _error("SYNC_OUTBOX_MISSING")
            return None
        envelope = self.read_publication(saved, observation=observation, proved_stream=attempt["stream"])
        if (metadata is not None and saved != metadata or envelope["claims"] and not allow_claimed
                or envelope["snapshot"]["version"] != attempt["version"]
                or envelope["recovery_generation"] != attempt["generation"]
                or envelope["recovery_stream"] != attempt["stream"]):
            _error("SYNC_STATE_RECOVERY_CONFLICT")
        return saved

    def recovery_publication(self, *, allow_claimed=False):
        with self._guard, _sanitized():
            return self._recovery_publication(self.read(), allow_claimed=allow_claimed)

    def is_recovery_publication(self, metadata):
        """Classify a proved marker without relabeling older claimed outboxes."""
        state = self.read_observation()
        attempt = state["recovery_attempt"]
        if attempt is None or attempt["publication_id"] != metadata["publication_id"]:
            return False
        if self._recovery_publication(state, metadata=metadata, observation=True, allow_claimed=True) != metadata:
            return False
        return not self.read_publication(metadata, observation=True, proved_stream=attempt["stream"])["claims"]

    def install_recovery_publication(self, metadata, *, attempt):
        """Verify an already reserved attempt; never replace the source pointer."""
        with self._mutating() as directory:
            state = self._read(directory)
            recovery = state["recovery"]
            if (attempt is None or state["recovery_attempt"] != attempt or recovery is None or recovery["state"] == "held"
                    or (recovery["generation"], recovery["stream"]) != (attempt["generation"], attempt["stream"])):
                return False
            self.assert_sendable(metadata["publication_id"])
            if self._recovery_publication(state, metadata=metadata) != metadata:
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            return True

    def refresh_unfrozen_recovery_attempt(self, version, *, attempt):
        """CAS a reserved version only before this UUID has immutable facts."""
        from . import sync_no_resend
        with self._mutating() as directory:
            state = self._read(directory)
            recovery = state["recovery"]
            sync_snapshot._version(version)
            if (attempt is None or state["recovery_attempt"] != attempt or recovery is None
                    or recovery["state"] == "held"
                    or (recovery["generation"], recovery["stream"]) != (attempt["generation"], attempt["stream"])
                    or any(version[key] != attempt["version"][key] for key in ("library_id", "library_epoch"))
                    or version["kb_revision"] < attempt["version"]["kb_revision"]):
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            if version == attempt["version"]:
                return deepcopy(attempt)
            publication_id = attempt["publication_id"]
            observed = sync_no_resend.observe(self.profile, state=state, allow_incomplete=True)
            catalog = observed["catalog"]
            if (self.find_publication(publication_id, audit=True) is not None
                    or state["pending_publication"] is not None and state["pending_publication"]["publication_id"] == publication_id
                    or publication_id in state.get("publication_media", {})
                    or catalog is not None and (publication_id in catalog["publications"] or publication_id in catalog["receipts"])
                    or any(key == publication_id for _, key in observed["incomplete"])):
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            updated = {**attempt, "version": deepcopy(version)}
            state["recovery_attempt"] = updated
            _write(directory, _STATE, _validate(state))
            return deepcopy(updated)

    def acknowledge_recovery_publication(self, request, *, current_version):
        """Ack only a bootstrap's permanent receipt, preserving original work."""
        from . import sync_no_resend
        from .sync_transfer import publication_payload_sha256
        with self._mutating() as directory:
            state = self._read(directory)
            original = self.no_resend_request(request["operation_id"])
            response = self.no_resend_receipt(request["operation_id"])
            if (original != request or original["reason"] != "published_receipt" or response is None
                    or sync_no_resend.normalize_response(original, response)["state"] != "published"):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            metadata = request["metadata"]
            if self._recovery_publication(state, metadata=metadata) != metadata:
                _error("SYNC_STATE_RECOVERY_CONFLICT")
            envelope = self.read_publication(metadata, proved_stream=request["binding"]["recovery_stream"])
            attempt, recovery = state["recovery_attempt"], state["recovery"]
            sync_snapshot._version(current_version)
            version = envelope["snapshot"]["version"]
            if (request["binding"]["source_version"] != version
                    or request["binding"]["recovery_generation"] != attempt["generation"]
                    or request["binding"]["recovery_stream"] != attempt["stream"]
                    or request["payload_sha256"] != publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                        recovery_generation=attempt["generation"], recovery_stream=attempt["stream"])):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            if (recovery is None or recovery["stream"] != attempt["stream"]
                    or recovery["generation"] < attempt["generation"] or state["local_epoch"] != version["library_epoch"]
                    or any(current_version[key] != version[key] for key in ("library_id", "library_epoch"))):
                return deepcopy(state)
            # Keep the immutable attempt/outbox root before clearing its current
            # pointer. This is recovery history, never a source-job terminal.
            self.archive_progress("recovery")
            snapshot = envelope["snapshot"]
            state.update(recovery_attempt=None, published_version=version,
                published_content_sha256=hashlib.sha256(protocol._json_bytes(
                    {"videos": snapshot["videos"], "globals": snapshot["globals"]})).hexdigest(),
                published_reports_sha256=hashlib.sha256(protocol._json_bytes(
                    sorted(snapshot["reports"], key=lambda value: value["name"]))).hexdigest(),
                applied_recovery_generation=max(state["applied_recovery_generation"], attempt["generation"]))
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def retire_progress(self, *, recovery_generation, recovery_stream, archive=False, preserve_work=False):
        """An old failure/reclaim receipt cannot clear a newly held work item."""
        with self._mutating() as directory:
            state = self._read(directory)
            recovery = state["recovery"]
            if (recovery is None or recovery["state"] != "running" or recovery["generation"] != recovery_generation
                    or recovery["stream"] != recovery_stream):
                return deepcopy(state)
            if archive:
                self.archive_progress("claim_changed")
            state.update(pending_publication=None, legacy_pending_stream=None)
            if not preserve_work:
                state.update(claims=[], work=None)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def apply_review_page(self, updates, *, claim_changes=None, recovery=None, archive_work=False):
        """Advance reviews; only replace work while the observed running fence holds."""
        with self._mutating() as directory:
            state = self._read(directory)
            if not set(updates) <= {"inbound", "seeded"}:
                _error()
            if ("inbound" in updates and state["recovery"] is not None
                    and updates["inbound"]["stream"] != state["recovery"]["stream"]):
                return deepcopy(state)
            if claim_changes is not None:
                if not set(claim_changes) <= {"claims", "work"}:
                    _error()
                if state["recovery"] == recovery and recovery is not None and recovery["state"] == "running":
                    if archive_work:
                        self.archive_progress("claim_changed")
                    state.update(claim_changes)
                    if state["work"] is not None:
                        self._assert_work_transition(self._read(directory)["work"], state["work"])
            state.update(updates)
            state = self._detach_retired_caches(state)
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def advance_outbound(self, cursor, *, recovery_stream):
        with self._mutating() as directory:
            state = self._read(directory)
            if state["recovery"] is not None and state["recovery"]["stream"] == recovery_stream:
                state["outbound"] = cursor
                _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def write_publication(self, snapshot, claims, *, recovery_generation=0, recovery_stream=None):
        with _sanitized():
            checked = sync_snapshot.validate_snapshot(snapshot)
            _integer(recovery_generation, minimum=0)
            _cursor({"stream": recovery_stream, "after": 0, "through": None})
            envelope = {"snapshot": checked, "claims": _bindings(claims, {v["video_id"] for v in checked["videos"]}),
                        "recovery_generation": recovery_generation, "recovery_stream": recovery_stream}
            data = _bounded(envelope, maximum=MAX_OUTBOX_BYTES, depth=sync_snapshot.MAX_ENVELOPE_DEPTH + 1)
            metadata = {"publication_id": checked["publication_id"], "sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
        with self._mutating() as root:
            self.assert_sendable(metadata["publication_id"])
            with media._Directory(self.data_dir / _OUTBOX, create=True, parent=root) as directory:
                name = metadata["publication_id"] + ".json"
                try:
                    existing = _read_bytes(directory, name, MAX_OUTBOX_BYTES)
                except FileNotFoundError:
                    _write(directory, name, data, immutable=True)
                else:
                    if existing != data:
                        _error("SYNC_OUTBOX_CONFLICT")
        return metadata

    def read_publication(self, metadata, *, observation=False, proved_stream=None):
        with (nullcontext() if observation else self._guard), _sanitized():
            _metadata(metadata)
            try:
                with media._Directory(self.data_dir / _OUTBOX, base=self.data_dir) as directory:
                    data = _read_bytes(directory, metadata["publication_id"] + ".json", MAX_OUTBOX_BYTES, observation=observation)
            except FileNotFoundError:
                _error("SYNC_OUTBOX_MISSING")
            if len(data) != metadata["byte_size"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                _error("SYNC_OUTBOX_CORRUPT")
            value = _decode(data)
            if type(value) is dict and set(value) == {"snapshot", "claims"}:
                value["recovery_generation"] = 0
            if type(value) is dict and set(value) == {"snapshot", "claims", "recovery_generation"}:
                state = self.read_observation() if observation else self.read()
                value["recovery_stream"] = (deepcopy(state["legacy_pending_stream"])
                    if state["pending_publication"] == metadata else deepcopy(proved_stream))
            _exact(value, ("snapshot", "claims", "recovery_generation", "recovery_stream"))
            _integer(value["recovery_generation"], minimum=0)
            _cursor({"stream": value["recovery_stream"], "after": 0, "through": None})
            checked = sync_snapshot.validate_snapshot(value["snapshot"])
            bindings = _bindings(value["claims"], {v["video_id"] for v in checked["videos"]})
            if checked["publication_id"] != metadata["publication_id"] or value != {
                    "snapshot": checked, "claims": bindings, "recovery_generation": value["recovery_generation"],
                    "recovery_stream": value["recovery_stream"]}:
                _error("SYNC_OUTBOX_CORRUPT")
            return deepcopy(value)

    def find_publication(self, publication_id, *, audit=False, observation=False):
        """Recover the exact intended outbox after a file/state crash gap."""
        with (nullcontext() if observation else self._guard), _sanitized():
            _uuid(publication_id)
            if not audit:
                self.assert_sendable(publication_id)
            try:
                with media._Directory(self.data_dir / _OUTBOX, base=self.data_dir) as directory:
                    data = _read_bytes(directory, publication_id + ".json", MAX_OUTBOX_BYTES, observation=observation)
            except FileNotFoundError:
                return None
            metadata = {"publication_id": publication_id, "sha256": hashlib.sha256(data).hexdigest(),
                        "byte_size": len(data)}
            self.read_publication(metadata, observation=observation)
            return metadata

    def assert_sendable(self, publication_id):
        """Pure atomic observations, safe in SH-protected upload callbacks."""
        from . import sync_no_resend
        if self.profile is None:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED")
        sync_no_resend.assert_sendable(self.profile, publication_id, state=self.read_observation())

    def enable_no_resend(self):
        """Explicit irreversible capability activation; never invoked by reads."""
        from . import sync_no_resend
        with self._mutating() as directory:
            state = self._read(directory)
            required = state["sender_contract"]["ledger_id"] if state["format_version"] >= 2 else None
            contract = sync_no_resend.ensure_contract(self.profile, state["binding"], required_ledger_id=required)
            if state["format_version"] >= 2:
                sync_no_resend.observe(self.profile, state=state)
                return deepcopy(state)
            state.update(format_version=2, sender_contract={"version": 1, "ledger_id": contract["ledger_id"]},
                         publication_media={})
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def has_sender_contract(self, *, state=None):
        from . import sync_no_resend
        return sync_no_resend.contract_active(self.profile, state=state or self.read_observation())

    def assert_work_reusable(self, work):
        from .sync_local_retirement import assert_work_reusable
        assert_work_reusable(self.profile, work, state=self.read_observation())

    def _assert_work_transition(self, previous, incoming):
        from . import sync_local_retirement as retire
        retire.assert_work_reusable(self.profile, incoming, state=self.read_observation())
        if previous is None:
            return
        generation = previous.get("source_mapping_generation")
        same_work = previous["item_id"] == incoming.get("item_id") and previous["video_id"] == incoming.get("video_id")
        if same_work and previous.get("source_mapping_id") is not None and incoming.get("source_mapping_id") != previous["source_mapping_id"]:
            _error("SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT")
        if same_work and generation is None and incoming.get("source_mapping_generation") is not None:
            # An existing legacy work is an instance too. Only an explicit new
            # work may allocate a generation, never a restore/retry promotion.
            _error("SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT")
        if same_work and generation is not None:
            if incoming.get("source_mapping_generation") != generation or incoming.get("source_mapping_inputs") != previous.get("source_mapping_inputs"):
                _error("SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT")
        elif generation is not None and incoming.get("source_mapping_generation") == generation:
            _error("SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT")

    def _assert_cache_transition(self, state, changes):
        from . import sync_local_retirement as retire, sync_no_resend
        if not ({"uploaded_media", "publication_media"} & set(changes)):
            return
        catalog = sync_no_resend.observe(self.profile, state=state)["catalog"]
        for video_id, descriptor in changes.get("uploaded_media", {}).items():
            if state["uploaded_media"].get(video_id) != descriptor and retire.cache_retired(catalog,
                    kind="legacy_cache", holder_kind="state", holder_name="sync-state.json", video_id=video_id, descriptor=descriptor):
                _error("SYNC_NO_RESEND_REFERENCE_RETIRED")
        for publication_id, record in changes.get("publication_media", {}).items():
            original = state.get("publication_media", {}).get(publication_id, {})
            for video_id, descriptor in record["media"].items():
                if original.get("binding") == record["binding"] and original.get("media", {}).get(video_id) == descriptor:
                    continue
                if retire.cache_retired(catalog, kind="publication_cache", holder_kind="state", holder_name="sync-state.json",
                        video_id=video_id, descriptor=descriptor, publication_id=publication_id, binding=record["binding"]):
                    _error("SYNC_NO_RESEND_REFERENCE_RETIRED")

    def _detach_retired_caches(self, state):
        from . import sync_local_retirement as retire, sync_no_resend
        observed = sync_no_resend.observe(self.profile, state=state, allow_incomplete=True)
        return retire.detach_state_caches(state, observed["catalog"])

    def preview_retirement(self, selection):
        from .sync_local_retirement import preview
        return preview(self, selection)

    def commit_retirement(self, selection, *, expected_digest, operation_id):
        from .sync_local_retirement import apply
        return apply(self, selection, expected_digest=expected_digest, operation_id=operation_id)

    def resume_retirement(self, operation_id):
        from .sync_local_retirement import resume
        return resume(self, operation_id)

    def _abandon_capture(self, publication_id):
        from . import sync_no_resend
        from .sync_transfer import publication_payload_sha256
        state = self.read_observation()
        observed = sync_no_resend.observe(self.profile, state=state)
        known = None if observed["catalog"] is None else observed["catalog"]["publications"].get(publication_id)
        metadata = self.find_publication(publication_id, audit=True, observation=True)
        if metadata is None:
            _error("SYNC_OUTBOX_MISSING")
        proved_stream = None if known is None else known["record"]["binding"]["recovery_stream"]
        envelope = self.read_publication(metadata, observation=True, proved_stream=proved_stream)
        if (envelope["snapshot"]["version"]["library_id"] != state["binding"]["home_library_id"]
                or envelope["recovery_stream"] is None
                or envelope["recovery_stream"]["library_id"] != state["binding"]["target_library_id"]):
            _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
        attempt = state["recovery_attempt"]
        associated = state["pending_publication"] == metadata or (attempt is not None
            and attempt["publication_id"] == publication_id
            and attempt["version"] == envelope["snapshot"]["version"]
            and attempt["generation"] == envelope["recovery_generation"]
            and attempt["stream"] == envelope["recovery_stream"])
        if known is not None:
            original = known["record"]
            if (original["metadata"] != metadata
                    or original["binding"]["source_version"] != envelope["snapshot"]["version"]
                    or original["binding"]["recovery_generation"] != envelope["recovery_generation"]
                    or original["binding"]["recovery_stream"] != envelope["recovery_stream"]):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
        elif not associated:
            # Old envelopes lack node/peer provenance. A current unrelated
            # binding cannot supply the original nodes for an orphan UUID.
            _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
        semantic = {key: state[key] for key in ("binding", "local_epoch", "pending_publication", "recovery_attempt",
                    "legacy_pending_stream", "work", "uploaded_media", "recovery", "applied_recovery_generation")}
        # Credentials/lease/status never become output or semantic noise.
        semantic["claims_sha256"] = hashlib.sha256(_bounded(state["claims"], maximum=MAX_STATE_BYTES)).hexdigest()
        semantic.update(metadata=metadata, ledger=observed,
                        publication_media=state.get("publication_media", {}))
        digest = hashlib.sha256(_bounded(semantic, maximum=sync_no_resend.MAX_CATALOG_BYTES)).hexdigest()
        terminal = None if observed["catalog"] is None else observed["catalog"]["receipts"].get(publication_id)
        request = None if observed["catalog"] is None else observed["catalog"]["publications"].get(publication_id)
        stage = "sendable" if request is None else ("no_resend_pending" if terminal is None else
                    "remote_" + terminal["record"]["response"]["state"])
        payload = publication_payload_sha256(envelope["snapshot"], envelope["claims"],
            recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"])
        return {"publication_id": publication_id, "stage": stage, "digest": digest}, state, metadata, envelope, payload

    def preview_abandon(self, publication_id):
        if self.profile is None:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED")
        with lifecycle.shared(self.profile), _sanitized():
            return self._abandon_capture(publication_id)[0]

    def commit_no_resend(self, publication_id, *, expected_digest, operation_id):
        """Lower-level local commit; public entry must already own worker lock."""
        from . import sync_no_resend
        _uuid(operation_id)
        if type(expected_digest) is not str or not _SHA.fullmatch(expected_digest):
            _error("SYNC_NO_RESEND_STALE_PREVIEW")
        with (self.exclusive() if self._directory is None else nullcontext()), self.reference_lifetime():
            preview, state, metadata, envelope, payload = self._abandon_capture(publication_id)
            if preview["digest"] != expected_digest:
                _error("SYNC_NO_RESEND_STALE_PREVIEW")
            if preview["stage"] != "sendable":
                request = self.no_resend_request(operation_id)
                if request["metadata"] != metadata or request["payload_sha256"] != payload:
                    _error("SYNC_NO_RESEND_OPERATION_CONFLICT")
            else:
                state = self.enable_no_resend()
                request = sync_no_resend.make_request(self.profile, state, metadata, envelope, operation_id)
                sync_no_resend.commit_request(self.profile, request)
            self.detach_no_resend(operation_id)
            return deepcopy(request)

    def detach_no_resend(self, operation_id):
        from . import sync_no_resend
        with self._mutating() as directory:
            state = self._read(directory)
            request = sync_no_resend.request_for_operation(self.profile, operation_id, state=state)
            if state["pending_publication"] == request["metadata"]:
                state["pending_publication"] = None
                state["legacy_pending_stream"] = None
            attempt = state["recovery_attempt"]
            if attempt is not None and attempt["publication_id"] == request["metadata"]["publication_id"]:
                binding = request["binding"]
                if (attempt["version"] != binding["source_version"] or attempt["generation"] != binding["recovery_generation"]
                        or attempt["stream"] != binding["recovery_stream"]):
                    _error("SYNC_NO_RESEND_BINDING_MISMATCH")
                state["recovery_attempt"] = None
            _write(directory, _STATE, _validate(state))
            return deepcopy(state)

    def pending_no_resend_requests(self, *, allow_incomplete=False):
        from . import sync_no_resend
        state = self.read_observation()
        requests = sync_no_resend.pending_requests(self.profile, state=state, allow_incomplete=allow_incomplete)
        attempt = state["recovery_attempt"]
        if attempt is not None:
            original = self.publication_no_resend_request(attempt["publication_id"], allow_incomplete=allow_incomplete)
            if (original is not None and original["reason"] == "published_receipt"
                    and self.is_recovery_publication(original["metadata"])):
                if not any(value["operation_id"] == original["operation_id"] for value in requests):
                    requests.append(original)
        return requests

    def resume_no_resend_commit(self, operation_id):
        """Repair only this authorized operation's exact local crash gaps.

        No remote call or selection expansion occurs. The original outbox is
        re-proven before replacing a missing reserved evidence file. Corrupt
        bytes or unrelated incomplete records remain blocked.
        """
        from . import sync_no_resend
        from .sync_transfer import publication_payload_sha256
        with (self.exclusive() if self._directory is None else nullcontext()), self.reference_lifetime():
            state = self.read_observation()
            observed = sync_no_resend.observe(self.profile, state=state, allow_incomplete=True)
            request = sync_no_resend.request_for_operation(self.profile, operation_id, state=state, allow_incomplete=True)
            publication_id = request["metadata"]["publication_id"]
            if any(key != publication_id for _, key in observed["incomplete"]):
                _error("SYNC_NO_RESEND_RECORD_MISSING")
            metadata = self.find_publication(publication_id, audit=True, observation=True)
            if metadata != request["metadata"]:
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            binding = request["binding"]
            envelope = self.read_publication(metadata, observation=True, proved_stream=binding["recovery_stream"])
            if (envelope["snapshot"]["version"] != binding["source_version"]
                    or envelope["recovery_stream"] != binding["recovery_stream"]
                    or envelope["recovery_generation"] != binding["recovery_generation"]
                    or publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                        recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"])
                        != request["payload_sha256"]):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            if ("publications", publication_id) in observed["incomplete"]:
                sync_no_resend.commit_request(self.profile, request)
            terminal = observed["catalog"]["receipts"].get(publication_id)
            if ("receipts", publication_id) in observed["incomplete"]:
                sync_no_resend.commit_receipt(self.profile, operation_id, terminal["record"]["response"], state=state)
            self.enable_no_resend()
            if request["reason"] != "published_receipt":
                self.detach_no_resend(operation_id)
            return deepcopy(request)

    def no_resend_request(self, operation_id):
        from . import sync_no_resend
        return sync_no_resend.request_for_operation(self.profile, operation_id, state=self.read_observation())

    def publication_no_resend_request(self, publication_id, *, allow_incomplete=False):
        """Read one original authorization; never create one or select work."""
        from . import sync_no_resend
        _uuid(publication_id)
        observed = sync_no_resend.observe(self.profile, state=self.read_observation(), allow_incomplete=allow_incomplete)
        entry = None if observed["catalog"] is None else observed["catalog"]["publications"].get(publication_id)
        return None if entry is None else deepcopy(entry["record"])

    def record_published_terminal(self, metadata, receipt, *, recovery_generation, recovery_stream,
                                  recovery_bootstrap=False):
        """Record only an exact successful RPC receipt, without detaching work.

        The caller performs the original acknowledgement after these permanent
        facts. A response retry uses the original operation and stable receipt;
        no abandon call, claim decision or recovery advancement occurs here.
        """
        from . import sync_no_resend
        from .sync_upload_pins import normalize_binding
        from .sync_transfer import publication_payload_sha256
        # The called readers/writers already sanitize filesystem failures.
        # Do not wrap their caller cancellation in a second generic sanitizer.
        with (self.exclusive() if self._directory is None else nullcontext()), self.reference_lifetime():
            state = self.read_observation()
            if type(recovery_bootstrap) is not bool:
                _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
            if recovery_bootstrap and self._recovery_publication(state, metadata=metadata, observation=True) != metadata:
                _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
            envelope = self.read_publication(metadata, observation=True, proved_stream=recovery_stream)
            binding = normalize_binding({"publication_id": metadata["publication_id"],
                "source_node_id": state["binding"]["home_node_id"], "source_version": envelope["snapshot"]["version"],
                "recovery_generation": recovery_generation, "recovery_stream": recovery_stream})
            if (envelope["recovery_generation"] != recovery_generation or envelope["recovery_stream"] != recovery_stream
                    or binding["source_version"]["library_id"] != state["binding"]["home_library_id"]
                    or recovery_stream["library_id"] != state["binding"]["target_library_id"]):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            payload_sha = publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                recovery_generation=recovery_generation, recovery_stream=recovery_stream)
            response = {"state": "published", "binding": binding, "payload_sha256": payload_sha, "receipt": deepcopy(receipt)}
            # Check every typed terminal field before the irreversible gate.
            sync_no_resend.normalize_response({"metadata": metadata, "binding": binding, "payload_sha256": payload_sha}, response)
            observed = sync_no_resend.observe(self.profile, state=state, allow_incomplete=True)
            entry = None if observed["catalog"] is None else observed["catalog"]["publications"].get(metadata["publication_id"])
            if any(kind == "references" or key != metadata["publication_id"] for kind, key in observed["incomplete"]):
                _error("SYNC_NO_RESEND_RECORD_MISSING")
            if entry is None:
                if state["pending_publication"] != metadata and not recovery_bootstrap:
                    _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
                state = self.enable_no_resend()
                contract = sync_no_resend.observe(self.profile, state=state)["contract"]
                operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "reno.sender.published.v1:" + contract["ledger_id"] + ":"
                    + metadata["publication_id"] + ":" + metadata["sha256"] + ":" + payload_sha))
                request = sync_no_resend.make_request(self.profile, state, metadata, envelope, operation_id, reason="published_receipt")
            else:
                request = entry["record"]
                if request["metadata"] != metadata or request["binding"] != binding or request["payload_sha256"] != payload_sha:
                    _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            if entry is None or entry["state"] != "committed" or ("publications", metadata["publication_id"]) in observed["incomplete"]:
                sync_no_resend.commit_request(self.profile, request)
            sync_no_resend.commit_receipt(self.profile, request["operation_id"], response, state=state)
            # A supported V1 restoration can be upgraded only after the exact
            # reserved facts have been repaired, never by clearing the ledger.
            self.enable_no_resend()
            return deepcopy(request)

    def no_resend_receipt(self, operation_id, *, allow_incomplete=False):
        from . import sync_no_resend
        state = self.read_observation()
        request = sync_no_resend.request_for_operation(self.profile, operation_id, state=state, allow_incomplete=allow_incomplete)
        observed = sync_no_resend.observe(self.profile, state=state, allow_incomplete=allow_incomplete)
        found = observed["catalog"]["receipts"].get(request["metadata"]["publication_id"])
        return None if found is None else deepcopy(found["record"]["response"])

    def record_no_resend_receipt(self, operation_id, response):
        from . import sync_no_resend
        with self._mutating() as directory:
            state = self._read(directory)
            terminal = sync_no_resend.commit_receipt(self.profile, operation_id, response, state=state)
            return deepcopy(terminal["response"])

    def record_uploaded(self, binding, descriptor):
        from .sync_upload_pins import normalize_binding
        binding = normalize_binding(binding)
        descriptor = protocol.validate_media_descriptor(descriptor)
        with (self.exclusive() if self._directory is None else nullcontext()), self.reference_lifetime():
            state = self.read_observation()
            self.assert_sendable(binding["publication_id"])
            if state["format_version"] == 1:
                return deepcopy(state)  # Do not grow unowned legacy hints.
            metadata = self.find_publication(binding["publication_id"], audit=True, observation=True)
            if metadata is None:
                _error("SYNC_NO_RESEND_BINDING_UNPROVEN")
            envelope = self.read_publication(metadata, observation=True)
            if (envelope["snapshot"]["version"] != binding["source_version"]
                    or envelope["recovery_stream"] != binding["recovery_stream"]
                    or envelope["recovery_generation"] != binding["recovery_generation"]
                    or descriptor not in [video["media"] for video in envelope["snapshot"]["videos"]]):
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            with self._mutating() as directory:
                state = self._read(directory)
                self.assert_sendable(binding["publication_id"])
                records = state["publication_media"]
                from . import sync_local_retirement as retire, sync_no_resend
                catalog = sync_no_resend.observe(self.profile, state=state)["catalog"]
                if retire.cache_retired(catalog, kind="publication_cache", holder_kind="state", holder_name="sync-state.json",
                        video_id=descriptor["video_id"], descriptor=descriptor, publication_id=binding["publication_id"], binding=binding):
                    _error("SYNC_NO_RESEND_REFERENCE_RETIRED")
                record = records.setdefault(binding["publication_id"], {"binding": binding, "media": {}})
                if record["binding"] != binding:
                    _error("SYNC_NO_RESEND_BINDING_MISMATCH")
                before = record["media"].get(descriptor["video_id"])
                if before is not None and before != descriptor:
                    _error("SYNC_NO_RESEND_BINDING_MISMATCH")
                record["media"][descriptor["video_id"]] = descriptor
                _write(directory, _STATE, _validate(state))
                return deepcopy(state)
