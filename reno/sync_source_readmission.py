"""Targeted Cloud readmission of an existing Home source lineage.

The caller is the paired Home; its preservation and manual authorization
assertions must additionally be resolved by the Home sealed consumer. This
module owns no downloads, budgets, ordinary queue claims or publication edits.
Prospective17 SQL and the real continuity writer are mandatory. The production
capability stays disabled until all effect and Home consumers are connected.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3
import uuid

from . import sync_budget_wait as budget, sync_protocol as protocol, sync_queue as queue
from . import sync_source_continuity as external, sync_source_continuity_service as service
from . import sync_source_readmit_contract as wire, sync_source_readmit_schema as schema
from .sync_protocol import SyncError


def _fail(code, status=409):
    raise SyncError("Source readmission conditions could not be verified",
                    "SYNC_SOURCE_READMIT_" + code, status) from None


def _json(value):
    return wire.canonical_json(value, maximum=wire.MAX_READMIT_BYTES)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _same_root(stored, request):
    if (stored["original"] != request["original"]
            or stored["current_home_epoch"] > request["current_home_epoch"]
            or any(stored[prefix][key] != request[prefix][key]
                   for prefix in ("source", "target") for key in ("library_id", "node_id"))):
        _fail("LINEAGE_MISMATCH")


def _disposition(con, owner, request, supplied):
    row = service._lookup(con, schema.DISPOSITION, "request_id", supplied["request_id"])
    if row is None or json.loads(row["result_json"]) != supplied:
        _fail("DISPOSITION_REQUIRED")
    stored = json.loads(row["request_json"])
    _same_root(stored, request)
    original = stored["original_request"]
    receipt = service._original(con, original)
    if supplied["disposition"] == "applied":
        if receipt != supplied["original_receipt"]:
            _fail("OPERATION_CONFLICT")
        service._external_positive(owner, original, core_sha256=row["core_sha256"], item_id=row["item_id"])
    else:
        registered = service._lookup(con, schema.REGISTRATION, "registration_sha256", row["registration_sha256"])
        if registered is None:
            _fail("CONTINUITY_REQUIRED")
        observed = service._registered(con, owner, registered)
        tombstone = observed["entries"]["tombstones"].get(original["request_id"])
        if (receipt is not None or tombstone is None or tombstone["state"] != "committed"
                or tombstone["record"] != supplied):
            _fail("OPERATION_CONFLICT")
    return row


def _related_controls(con, owner, request, observed):
    """No caller may hide a known item lineage by selecting another core.

    Consume the already captured external closure; do not reread every old
    journal or reacquire a file lock inside the SQL writer.
    """
    original = request["original"]
    related = {key: entry for key, entry in observed["entries"]["registrations"].items()
               if entry["record"]["item_id"] == original["item_id"]}
    rows = con.execute("SELECT original_request_id FROM " + schema.REGISTRATION
        + " WHERE item_id=? LIMIT ?", (original["item_id"], external.MAX_RECORDS + 1)).fetchall()
    if len(rows) > external.MAX_RECORDS or {row[0] for row in rows} != set(related):
        _fail("CONTINUITY_REQUIRED")
    # A legacy applied disposition also binds the core even when it has no
    # retrospective registration. A new caller-selected core cannot hide it.
    if con.execute("SELECT 1 FROM " + schema.DISPOSITION + " WHERE item_id=? AND core_sha256!=? LIMIT 1",
                   (original["item_id"], original["core_sha256"])).fetchone():
        _fail("LINEAGE_MISMATCH")
    supplied_ids = {result["original_request_id"] for result in request["dispositions"]}
    for key, entry in related.items():
        owner.check()
        row = service._lookup(con, schema.REGISTRATION, "original_request_id", key)
        if row is None or entry["state"] != "committed" or json.loads(row["result_json"]) != entry["record"]:
            _fail("CONTINUITY_REQUIRED")
        if row["core_sha256"] != original["core_sha256"]:
            _fail("LINEAGE_MISMATCH")
        control = json.loads(row["original_request_json"])
        if any(control[prefix][name] != request[prefix][name]
               for prefix in ("source", "target") for name in ("library_id", "node_id")):
            _fail("LINEAGE_MISMATCH")
        receipt = service._original(con, control)
        effect = observed["entries"]["effects"].get(key)
        negative = observed["entries"]["tombstones"].get(key)
        if receipt is not None:
            if effect is None or effect["state"] != "committed" or negative is not None:
                _fail("OPERATION_CONFLICT")
            # Applied refunds for the current held generation cannot be
            # hidden behind a caller assertion that the claim is untouched.
            if (control["kind"] == "budget_defer" and control["claim"] == request["expected_claim"]
                    and request["defer_receipt"] != receipt):
                _fail("DEFER_REQUIRED")
        elif effect is not None:
            _fail("EFFECT_UNKNOWN")
        else:
            disposition = service._lookup(con, schema.DISPOSITION, "original_request_id", key)
            if (negative is None or negative["state"] != "committed" or disposition is None
                    or disposition["disposition"] != "not_applied"
                    or json.loads(disposition["result_json"]) != negative["record"]):
                _fail("DISPOSITION_REQUIRED")
            current_control = control.get("claim") == request["expected_claim"]
            if control["kind"] == "budget_resume":
                parent = service._parent(con, control)
                current_control = parent["lease_generation"] == request["expected_claim"]["lease_generation"]
            if current_control and key not in supplied_ids:
                _fail("DISPOSITION_REQUIRED")


def _previous(con, request):
    previous_id = request["previous_admission_id"]
    if previous_id is None:
        if con.execute("SELECT 1 FROM " + schema.ADMISSION
                + " WHERE item_id=? LIMIT 1", (request["original"]["item_id"],)).fetchone():
            _fail("PREVIOUS_REQUIRED")
        return None
    row = service._lookup(con, schema.ADMISSION, "admission_id", previous_id)
    if row is None:
        _fail("PREVIOUS_REQUIRED")
    stored, result = json.loads(row["request_json"]), json.loads(row["result_json"])
    _same_root(stored, request)
    if (result["claim"] != request["expected_claim"]
            or con.execute("SELECT 1 FROM " + schema.ADMISSION
                + " WHERE previous_admission_id=? LIMIT 1", (previous_id,)).fetchone()):
        _fail("LINEAGE_MISMATCH")
    return result


def _budget_receipts(con, request):
    values = []
    bodies = []
    for supplied, kind in ((request["defer_receipt"], "budget_defer"),
                           (request["resume_receipt"], "budget_resume")):
        row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
                          (supplied["request_id"],)).fetchone()
        if row is None or row["action"] != kind:
            _fail("DEFER_REQUIRED")
        body, receipt = budget._receipt(row, con=con)
        if (receipt != supplied or any(body[prefix][key] != request[prefix][key]
                for prefix in ("source", "target") for key in ("library_id", "node_id"))
                or body["recovery_stream"] != request["original"]["recovery_stream"]
                or body["recovery_generation"] > request["recovery_generation"]):
            _fail("LINEAGE_MISMATCH")
        values.append(receipt)
        bodies.append(body)
    if (bodies[0]["claim"] != request["expected_claim"]
            or bodies[1]["defer_request_id"] != bodies[0]["request_id"]):
        _fail("LINEAGE_MISMATCH")
    return values[0]


def _claim_basis(con, request, previous):
    original, expected = request["original"], request["expected_claim"]
    row = con.execute("SELECT * FROM sync_queue WHERE item_id=?", (original["item_id"],)).fetchone()
    if row is None:
        _fail("ITEM_NOT_FOUND", 404)
    if (any(row[key] is not None for key in ("video_id", "publication_id", "result_hash"))
            or con.execute("SELECT 1 FROM sync_queue_publication WHERE item_id=? LIMIT 1", (row["item_id"],)).fetchone()
            or con.execute("SELECT 1 FROM sync_queue_source_receipt WHERE item_id=? LIMIT 1", (row["item_id"],)).fetchone()):
        _fail("OUTCOME_MISMATCH")
    url = protocol.validate_ingest_url(row["url"])
    if hashlib.sha256(url.encode("utf-8")).hexdigest() != original["submitted_url_sha256"]:
        _fail("LINEAGE_MISMATCH")
    if row["status"] == "budget_wait":
        raise SyncError("Original budget wait requires explicit resolution", "SYNC_BUDGET_DEFER_REQUIRED", 409)
    if row["lease_generation"] != expected["lease_generation"]:
        _fail("CLAIM_MISMATCH")
    if row["status"] == "claimed" and request["defer_receipt"] is None:
        if (row["lease_token"] != expected["lease_token"] or row["owner_id"] is None
                or type(row["owner_generation"]) is not int or row["owner_generation"] < 1
                or queue._stored_time(row["lease_until"]) is None
                or type(row["attempts"]) is not int or not 1 <= row["attempts"] <= queue.MAX_ATTEMPTS
                or any(row[key] is not None for key in ("retry_at", "budget_wait_reason", "budget_operation_id", "last_error_class"))):
            _fail("CLAIM_MISMATCH")
        if previous is not None and (row["owner_id"] != previous["session"]["session_id"]
                or row["owner_generation"] != previous["session"]["generation"]
                or row["attempts"] != previous["attempts"]):
            _fail("LINEAGE_MISMATCH")
        return row, row["attempts"], "held_claim"
    if row["status"] != "queued" or request["defer_receipt"] is None:
        _fail("OUTCOME_MISMATCH")
    parent = _budget_receipts(con, request)
    if (row["attempts"] != parent["attempts"] or row["owner_generation"] != 0
            or any(row[key] is not None for key in ("owner_id", "lease_token", "lease_until", "retry_at",
                                                   "budget_wait_reason", "budget_operation_id", "last_error_class"))):
        _fail("CLAIM_MISMATCH")
    return row, parent["attempts"] + 1, "original_defer_refund"


def _record(request, result):
    row = {key: result[key] for key in ("admission_id", "format_version", "core_sha256", "item_id",
        "submitted_url_sha256", "current_home_epoch", "recovery_generation", "control_revision",
        "previous_admission_id", "attempts", "attempt_basis", "admission_sha256", "committed_at")}
    row.update(service._identities(request["source"], request["target"]),
        receiver_library_epoch=result["recovery_stream"]["library_epoch"],
        lease_generation=result["claim"]["lease_generation"], request_json=_json(request),
        request_sha256=_sha(request), result_json=_json(result), result_sha256=_sha(result))
    return row


def readmit(*, profile, request, now=None):
    """Transfer one proven held/refunded claim and append its exact receipt."""
    try:
        request = wire.normalize_readmit_request(request)
    except wire.SourceReadmitContractError:
        raise SyncError("Invalid source readmission request", "INVALID_SYNC_REQUEST", 400) from None
    if request.get("format_version", 1) != 1:
        # Wire v2 is represented separately; this SQL consumer still owns the
        # original v1 admission rows and must reject before opening a writer.
        _fail("CAPABILITY_REQUIRED")
    try:
        with service.writer(profile=profile) as con:
            owner = service._owner(con, profile)
            service._pair(con, request)
            existing = service._lookup(con, schema.ADMISSION, "admission_id", request["request_id"])
            if existing is not None:
                if existing["request_json"] != _json(request):
                    _fail("OPERATION_CONFLICT")
                # Historical receipts never transfer a current queue row.
                return {**json.loads(existing["result_json"]), "replayed": True}
            if owner.schema_variant != "v1":
                # Original v1 session expiry was an unchecked caller echo.
                # New variant business uses the independent explicit2 gate.
                _fail("CAPABILITY_REQUIRED")
            observed = owner.guard.view()
            service._activation(con, owner, observed["contract"]["continuity_id"])
            service._live(con, request, now)
            for supplied in request["dispositions"]:
                _disposition(con, owner, request, supplied)
            _related_controls(con, owner, request, observed)
            previous = _previous(con, request)
            row, attempts, basis = _claim_basis(con, request, previous)
            session, _ = service._live(con, request, now)
            owner.check()
            _, stamp = queue._clock(now)
            claim = {"item_id": row["item_id"], "lease_token": str(uuid.uuid4()),
                     "lease_generation": row["lease_generation"] + 1}
            result = {"format_version": 1, "admission_id": request["request_id"],
                "request_sha256": _sha(request), "admission_sha256": "0" * 64,
                **{key: request["original"][key] for key in ("core_sha256", "item_id", "submitted_url_sha256")},
                **{key: deepcopy(request[key]) for key in ("current_home_epoch", "preservation",
                    "recovery_generation", "recovery_stream", "control_revision", "previous_admission_id")},
                "source_identity": {key: request["source"][key] for key in ("library_id", "node_id")},
                "target_identity": deepcopy(request["target"]), "session": deepcopy(request["lease"]),
                "claim": claim, "attempts": attempts, "attempt_basis": basis,
                "disposition_sha256s": [wire.disposition_sha256(value) for value in request["dispositions"]],
                "committed_at": stamp, "replayed": False}
            result["admission_sha256"] = wire.admission_sha256(result)
            result = wire.normalize_readmit_result(request, result)
            changed = con.execute("UPDATE sync_queue SET status='claimed',attempts=?,owner_id=?,owner_generation=?,"
                "lease_token=?,lease_generation=?,lease_until=?,updated_at=? WHERE item_id=? AND status=? "
                "AND attempts=? AND lease_generation=? AND lease_token IS ? AND owner_id IS ? AND owner_generation=?",
                (attempts, session["session_id"], session["generation"], claim["lease_token"], claim["lease_generation"],
                 session["lease_until"], stamp, row["item_id"], row["status"], row["attempts"], row["lease_generation"],
                 row["lease_token"], row["owner_id"], row["owner_generation"])).rowcount
            if changed != 1:
                _fail("CLAIM_MISMATCH")
            service._insert(con, schema.ADMISSION, _record(request, result))
            service._live(con, request, now)
            owner.check()
            return deepcopy(result)
    except sqlite3.IntegrityError:
        _fail("OPERATION_CONFLICT")
