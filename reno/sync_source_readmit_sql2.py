"""Explicit readmit2 Cloud CAS, preserving the original v1 SQL consumer.

The same real continuity writer owns locks and transaction boundaries. A
new variant and explicit published capability are both mandatory. Receipt
replay never changes a queue row. Timer and manual bases are distinct.
"""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
import uuid

from . import sync_protocol as protocol, sync_queue as queue
from . import sync_source_readmission as old, sync_source_continuity_service as service
from . import sync_source_readmit_contract as wire, sync_source_readmit_schema2 as schema

_fail, _json, _sha = old._fail, old._json, old._sha


def _runtime():
    rules = protocol.schema_rules()
    if (type(getattr(protocol, "SOURCE_READMIT_VERSION", None)) is not int
            or protocol.SOURCE_READMIT_VERSION != 2 or type(rules) is not dict
            or type(rules.get("source_readmit_version")) is not int or rules["source_readmit_version"] != 2):
        _fail("CAPABILITY_REQUIRED")


def _dispose_runtime():
    rules = protocol.schema_rules()
    if (type(getattr(protocol, "SOURCE_DISPOSE_VERSION", None)) is not int
            or protocol.SOURCE_DISPOSE_VERSION != 2 or type(rules) is not dict
            or type(rules.get("source_dispose_version")) is not int or rules["source_dispose_version"] != 2):
        _fail("CAPABILITY_REQUIRED")


def dispose(*, profile, request, now=None):
    """Consume explicit dispose2; original permanent v1 bytes remain distinct."""
    try:
        request = wire.normalize_dispose_request(request)
    except wire.SourceReadmitContractError:
        _fail("INVALID_REQUEST", 400)
    if request.get("format_version") != 2:
        _fail("CAPABILITY_REQUIRED")
    return service._dispose(profile=profile, request=request, now=now, version=2)


def _lookup(con, admission_id):
    row = con.execute("SELECT " + ",".join(schema.COLUMNS[schema.ADMISSION]) + " FROM "
        + schema.ADMISSION + " WHERE admission_id=?", (admission_id,)).fetchone()
    if row is None:
        return None
    try:
        return schema.validate_admission_record(row)
    except schema.SourceReadmitRecordError:
        _fail("RECORD_INVALID")


def _previous(con, request):
    previous = request["previous_admission_id"]
    if previous is None:
        if con.execute("SELECT 1 FROM " + schema.ADMISSION + " WHERE item_id=? LIMIT 1",
                (request["original"]["item_id"],)).fetchone():
            _fail("PREVIOUS_REQUIRED")
        return None
    row = _lookup(con, previous)
    if row is None:
        _fail("PREVIOUS_REQUIRED")
    stored, result = json.loads(row["request_json"]), json.loads(row["result_json"])
    old._same_root(stored, request)
    if (result["claim"] != request["expected_claim"] or con.execute("SELECT 1 FROM "
            + schema.ADMISSION + " WHERE previous_admission_id=? LIMIT 1", (previous,)).fetchone()):
        _fail("LINEAGE_MISMATCH")
    return result


def _timer_gate(con, request, now):
    basis = request["resume_basis"]
    if basis is None or basis["kind"] != "timer":
        return
    # Original SQL receipts/body hashes, parent and refund are consumed by
    # _claim_basis. The additional timer fact must use the original deadline.
    parent = old._budget_receipts(con, request)
    row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
        (basis["defer_request_id"],)).fetchone()
    if row is None:
        _fail("DEFER_REQUIRED")
    from . import sync_budget_wait as budget
    body, receipt = budget._receipt(row, con=con)
    current, _ = queue._clock(now)
    retry = queue._stored_time(parent["retry_at"])
    if (retry is None or current < retry or receipt != parent
            or _sha(body) != basis["defer_request_sha256"]
            or _sha(parent) != basis["defer_receipt_sha256"]
            or basis["retry_at"] != parent["retry_at"]
            or basis["timer_operation_id"] != wire.timer_operation_id(body["request_id"])):
        _fail("TIMER_NOT_DUE")


def _lease_gate(request, session, now):
    """Bound the echoed receipt expiry by the actual SQL session expiry."""
    current, _ = queue._clock(now)
    submitted = queue._stored_time(request["lease"]["lease_until"])
    actual = queue._stored_time(session["lease_until"])
    if submitted is None or actual is None or not current < submitted <= actual:
        _fail("LEASE_MISMATCH")


def readmit(*, profile, request, now=None):
    try:
        request = wire.normalize_readmit_request(request)
    except wire.SourceReadmitContractError:
        _fail("INVALID_REQUEST", 400)
    if request.get("format_version") != 2:
        _fail("CAPABILITY_REQUIRED")
    _runtime()
    try:
        with service.writer(profile=profile) as con:
            # Writer itself must explicitly support this exact variant; an
            # old writer rejects it before BEGIN rather than being bypassed.
            owner = service._owner(con, profile)
            if owner.schema_variant not in {"admission2", "admission2_dispose2"}:
                _fail("SCHEMA_REQUIRED")
            service._pair(con, request)
            existing = _lookup(con, request["request_id"])
            if existing is not None:
                if existing["request_json"] != _json(request):
                    _fail("OPERATION_CONFLICT")
                return {**json.loads(existing["result_json"]), "replayed": True}
            observed = owner.guard.view()
            service._activation(con, owner, observed["contract"]["continuity_id"])
            session, _ = service._live(con, request, now)
            _lease_gate(request, session, now)
            for supplied in request["dispositions"]:
                old._disposition(con, owner, request, supplied)
            old._related_controls(con, owner, request, observed)
            previous = _previous(con, request)
            _timer_gate(con, request, now)
            row, attempts, basis = old._claim_basis(con, request, previous)
            session, _ = service._live(con, request, now)
            _lease_gate(request, session, now)
            owner.check()
            _, stamp = queue._clock(now)
            claim = {"item_id": row["item_id"], "lease_token": str(uuid.uuid4()),
                "lease_generation": row["lease_generation"] + 1}
            result = {"format_version": 2, "admission_id": request["request_id"],
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
            record = schema.validate_admission_record(old._record(request, result))
            columns = schema.COLUMNS[schema.ADMISSION]
            con.execute("INSERT INTO " + schema.ADMISSION + "(" + ",".join(columns) + ") VALUES("
                + ",".join("?" for _ in columns) + ")", tuple(record[key] for key in columns))
            session, _ = service._live(con, request, now)
            _lease_gate(request, session, now)
            owner.check()
            return deepcopy(result)
    except sqlite3.IntegrityError:
        _fail("OPERATION_CONFLICT")
