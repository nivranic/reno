"""Permanent source-failure outcomes, separate from budget waits/publications.

First application needs the current session, recovery fence and exact claim.
An original receipt replays by peer identity and full body SHA before those
live gates. A receipt can never authorize a newer claim or resource retirement.
"""
from __future__ import annotations

import hashlib
import json

from . import sync_budget_wait, sync_protocol, sync_queue as queue, sync_recovery
from . import sync_source_control_contract as contract


def _fail(code="INVALID_SYNC_REQUEST", status=400):
    queue._error("Invalid source failure control", code, status)


def _request(body):
    try:
        return contract.normalize_request(body)
    except ValueError:
        _fail()


def _receipt(row, encoded=None):
    try:
        original = contract.normalize_request(json.loads(row["request_json"]))
        result = contract.normalize_receipt(original, json.loads(row["result_json"]))
        if (contract.canonical_json(original) != row["request_json"]
                or contract.request_sha256(original) != row["request_sha256"]
                or contract.canonical_json(result) != row["result_json"]
                or contract.digest(result) != row["result_sha256"]
                or row["action"] != "source_fail" or result["replayed"] is not False
                or row["request_id"] != original["request_id"]
                or row["committed_at"] != result["committed_at"]
                or row["recovery_generation"] != original["recovery_generation"]
                or row["receiver_library_epoch"] != original["recovery_stream"]["library_epoch"]):
            _fail("SYNC_SOURCE_RECEIPT_INVALID", 409)
        for prefix in ("source", "target"):
            for key in ("library_id", "node_id"):
                if row[prefix + "_" + key] != original[prefix][key]:
                    _fail("SYNC_SOURCE_RECEIPT_INVALID", 409)
        for key in ("item_id", "lease_generation", "core_sha256", "submitted_url_sha256", "failure_sha256"):
            if row[key] != result[key]:
                _fail("SYNC_SOURCE_RECEIPT_INVALID", 409)
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        _fail("SYNC_SOURCE_RECEIPT_INVALID", 409)
    if encoded is not None and encoded != row["request_json"]:
        _fail("SYNC_SOURCE_REQUEST_MISMATCH", 409)
    return original, result


def apply_request(con, body, *, now=None):
    body = _request(body)
    encoded = contract.canonical_json(body)
    from .sync_source_effect_gate import require_if_enabled
    require_if_enabled(con, body)
    with queue._operation(con):
        current, stamp = queue._clock(now)
        sync_budget_wait._pair_identity(con, body)
        existing = con.execute("SELECT * FROM sync_queue_source_receipt WHERE request_id=?",
                               (body["request_id"],)).fetchone()
        if existing is not None:
            _, result = _receipt(existing, encoded)
            return {**result, "replayed": True}
        # One terminal for this item. A fresh UUID cannot select a different
        # failure/core or replace the original permanent outcome.
        if con.execute("SELECT 1 FROM sync_queue_source_receipt WHERE item_id=?",
                       (body["claim"]["item_id"],)).fetchone() is not None:
            _fail("SYNC_SOURCE_REQUEST_MISMATCH", 409)
        session, _ = queue._session(con, body["source"], body["lease"], current)
        sync_recovery.assert_recovery_fence(con, body["recovery_generation"], body["recovery_stream"])
        row = queue._claim_record(con, session, body["claim"], current)
        if row["status"] != "claimed":
            _fail("SYNC_OUTCOME_MISMATCH", 409)
        url = sync_protocol.validate_ingest_url(row["url"])
        if hashlib.sha256(url.encode("utf-8")).hexdigest() != body["submitted_url_sha256"]:
            _fail("SYNC_SOURCE_REQUEST_MISMATCH", 409)
        if con.execute("SELECT 1 FROM sync_queue_publication WHERE item_id=? LIMIT 1",
                       (row["item_id"],)).fetchone() is not None:
            _fail("SYNC_OUTCOME_MISMATCH", 409)
        result = {"format_version": 1, "request_id": body["request_id"],
            "request_sha256": contract.digest(body), "core_sha256": body["core_sha256"],
            "submitted_url_sha256": body["submitted_url_sha256"],
            "failure_sha256": contract.failure_sha256(body["failure"]), "item_id": row["item_id"],
            "lease_generation": row["lease_generation"], "attempts": row["attempts"], "status": "dead",
            "error_class": body["failure"]["error_class"], "committed_at": stamp, "replayed": False}
        result = contract.normalize_receipt(body, result)
        con.execute("UPDATE sync_queue SET status='dead',last_error_class=?,updated_at=?,retry_at=NULL,"
            "owner_id=NULL,owner_generation=0,lease_token=NULL,lease_until=NULL,"
            "budget_wait_reason=NULL,budget_operation_id=NULL WHERE item_id=?",
            (result["error_class"], stamp, row["item_id"]))
        result_json = contract.canonical_json(result)
        con.execute("INSERT INTO sync_queue_source_receipt(request_id,request_sha256,request_json,action,"
            "source_library_id,source_node_id,target_library_id,target_node_id,receiver_library_epoch,"
            "recovery_generation,item_id,lease_generation,core_sha256,submitted_url_sha256,failure_sha256,"
            "result_json,result_sha256,committed_at) VALUES(?,?,?,'source_fail',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body["request_id"], result["request_sha256"], encoded,
             body["source"]["library_id"], body["source"]["node_id"], body["target"]["library_id"], body["target"]["node_id"],
             body["recovery_stream"]["library_epoch"], body["recovery_generation"], row["item_id"],
             row["lease_generation"], body["core_sha256"], body["submitted_url_sha256"], result["failure_sha256"],
             result_json, contract.digest(result), stamp))
        return result
