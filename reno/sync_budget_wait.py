"""Durable queue budget controls; receipts never authorize a newer claim.

All changes and their exact receipts share the caller's short SQL transaction.
Historical receipt replay requires the same peer identities, not a live lease.
"""
from __future__ import annotations

import hashlib
import json

from . import budget_contract, sync_queue as queue, sync_recovery

MAX_REQUEST_BYTES = 16 * 1024
_COMMON = {"kind", "request_id", "source", "target", "lease", "recovery_generation", "recovery_stream"}
_DEFER = _COMMON | {"claim", "reason", "retry_at", "budget_operation_id"}
_RESUME = _COMMON | {"defer_request_id"}
_RESULT = {"request_id", "request_sha256", "item_id", "lease_generation", "status", "attempts",
           "reason", "retry_at", "budget_operation_id", "committed_at", "replayed"}


def _fail(code="INVALID_SYNC_REQUEST", status=400):
    queue._error("Invalid queue budget control", code, status)


def _fields(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail()


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            _fail()
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail()


def _sha(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate(body):
    if type(body) is not dict:
        _fail()
    if type(body.get("kind")) is not str or body["kind"] not in {"budget_defer", "budget_resume"}:
        _fail()
    _fields(body, _DEFER if body.get("kind") == "budget_defer" else _RESUME)
    if body["kind"] not in {"budget_defer", "budget_resume"}:
        _fail()
    queue._uuid(body["request_id"], "request_id")
    _fields(body["source"], {"library_id", "node_id", "handshake"})
    _fields(body["target"], {"library_id", "node_id"})
    for identity in (body["source"], body["target"]):
        for name in ("library_id", "node_id"):
            queue._uuid(identity[name], name)
    handshake = body["source"]["handshake"]
    _fields(handshake, {"protocol_version", "migration_version", "core_schema_sha256"})
    for key in ("protocol_version", "migration_version"):
        queue._integer(handshake[key], key)
    if type(handshake["core_schema_sha256"]) is not str or not queue._SHA256.fullmatch(handshake["core_schema_sha256"]):
        _fail()
    queue._lease(body["lease"])
    queue._integer(body["recovery_generation"], "recovery_generation", minimum=0)
    _fields(body["recovery_stream"], {"library_id", "library_epoch"})
    queue._uuid(body["recovery_stream"]["library_id"], "recovery library_id")
    queue._integer(body["recovery_stream"]["library_epoch"], "library_epoch")
    if body["recovery_stream"]["library_id"] != body["target"]["library_id"]:
        _fail()
    if body["kind"] == "budget_defer":
        _fields(body["claim"], {"item_id", "lease_token", "lease_generation"})
        queue._integer(body["claim"]["item_id"], "item_id")
        queue._integer(body["claim"]["lease_generation"], "lease_generation")
        queue._uuid(body["claim"]["lease_token"], "claim lease_token")
        if type(body["reason"]) is not str or body["reason"] not in budget_contract.DEFER_REASONS:
            _fail()
        if body["retry_at"] is not None and queue._stored_time(body["retry_at"]) is None:
            _fail()
        if body["budget_operation_id"] is not None:
            queue._uuid(body["budget_operation_id"], "budget_operation_id")
    else:
        queue._uuid(body["defer_request_id"], "defer_request_id")
    return _json(body)


def _pair_identity(con, body):
    """Historical handshakes are private receipt facts, never new admission."""
    library, node = queue._local_identity(con)
    if body["target"] != {"library_id": library, "node_id": node}:
        _fail("SYNC_PEER_MISMATCH", 409)
    row = con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone()
    if row is None:
        _fail("SYNC_NOT_PAIRED", 409)
    if any(row[prefix + "_" + key] != body[prefix][key]
           for prefix in ("source", "target") for key in ("library_id", "node_id")):
        _fail("SYNC_PEER_MISMATCH", 409)


def _receipt(row, encoded=None, *, con=None):
    try:
        original = json.loads(row["request_json"])
        result = json.loads(row["result_json"])
        if (_validate(original) != row["request_json"] or _sha(row["request_json"]) != row["request_sha256"]
                or _json(result) != row["result_json"] or _sha(row["result_json"]) != row["result_sha256"]):
            _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        _fields(result, _RESULT)
        if (original["request_id"] != row["request_id"] or result["request_id"] != row["request_id"]
                or result["request_sha256"] != row["request_sha256"]
                or result["replayed"] is not False or result["item_id"] != row["item_id"]
                or result["lease_generation"] != row["lease_generation"]
                or result["committed_at"] != row["committed_at"] or row["action"] != original["kind"]):
            _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        for prefix in ("source", "target"):
            for key in ("library_id", "node_id"):
                if row[prefix + "_" + key] != original[prefix][key]:
                    _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        if (row["recovery_generation"] != original["recovery_generation"]
                or row["receiver_library_epoch"] != original["recovery_stream"]["library_epoch"]):
            _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        queue._integer(result["attempts"], "attempts", minimum=0, maximum=queue.MAX_ATTEMPTS - 1)
        queue._integer(result["item_id"], "item_id")
        queue._integer(result["lease_generation"], "lease_generation")
        if queue._stored_time(result["committed_at"]) is None:
            _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        if original["kind"] == "budget_defer":
            if (original["claim"]["item_id"] != result["item_id"]
                    or original["claim"]["lease_generation"] != result["lease_generation"]
                    or result["status"] != "budget_wait"
                    or any(result[key] != original[key] for key in ("reason", "retry_at", "budget_operation_id"))):
                _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
        else:
            if result["status"] != "queued" or any(result[key] is not None for key in ("reason", "retry_at", "budget_operation_id")):
                _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
            parent = None if con is None else con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
                (original["defer_request_id"],)).fetchone()
            if parent is None or parent["action"] != "budget_defer":
                _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
            previous, parent_result = _receipt(parent, con=con)
            if (any(result[key] != parent_result[key] for key in ("item_id", "lease_generation", "attempts"))
                    or any(original[key][name] != previous[key][name]
                           for key in ("source", "target") for name in ("library_id", "node_id"))):
                _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
    except (ValueError, TypeError, KeyError, UnicodeError):
        _fail("SYNC_BUDGET_RECEIPT_INVALID", 409)
    if encoded is not None and encoded != row["request_json"]:
        _fail("SYNC_BUDGET_REQUEST_MISMATCH", 409)
    return original, result


def apply_request(con, body, *, now=None):
    if type(body) is not dict:
        _fail()
    from .sync_source_effect_gate import require_if_enabled
    require_if_enabled(con, body)
    encoded = _validate(body)
    with queue._operation(con):
        current, stamp = queue._clock(now)
        _pair_identity(con, body)
        existing = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (body["request_id"],)).fetchone()
        if existing is not None:
            _, result = _receipt(existing, encoded, con=con)
            return {**result, "replayed": True}
        if body["kind"] == "budget_defer":
            duplicate = con.execute("SELECT 1 FROM sync_queue_budget_receipt WHERE action='budget_defer' "
                "AND item_id=? AND lease_generation=?", (body["claim"]["item_id"], body["claim"]["lease_generation"])).fetchone()
            if duplicate is not None:
                _fail("SYNC_BUDGET_REQUEST_MISMATCH", 409)
        session, _ = queue._session(con, body["source"], body["lease"], current)
        sync_recovery.assert_recovery_fence(con, body["recovery_generation"], body["recovery_stream"])
        if body["kind"] == "budget_defer":
            row = queue._claim_record(con, session, body["claim"], current)
            if (row["status"] != "claimed" or type(row["attempts"]) is not int
                    or not 1 <= row["attempts"] <= queue.MAX_ATTEMPTS):
                _fail("SYNC_OUTCOME_MISMATCH", 409)
            attempts = row["attempts"] - 1
            con.execute("UPDATE sync_queue SET status='budget_wait',attempts=?,retry_at=?,budget_wait_reason=?,"
                "budget_operation_id=?,owner_id=NULL,owner_generation=0,lease_token=NULL,lease_until=NULL,"
                "last_error_class=NULL,updated_at=? WHERE item_id=?", (attempts, body["retry_at"], body["reason"],
                body["budget_operation_id"], stamp, row["item_id"]))
            status, reason, retry_at, operation = "budget_wait", body["reason"], body["retry_at"], body["budget_operation_id"]
        else:
            parent = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (body["defer_request_id"],)).fetchone()
            if parent is None or parent["action"] != "budget_defer":
                _fail("SYNC_BUDGET_DEFER_REQUIRED", 409)
            original, receipt = _receipt(parent, con=con)
            if any(original[key][name] != body[key][name] for key in ("source", "target") for name in ("library_id", "node_id")):
                _fail("SYNC_PEER_MISMATCH", 409)
            row = con.execute("SELECT * FROM sync_queue WHERE item_id=?", (receipt["item_id"],)).fetchone()
            if (row is None or row["status"] != "budget_wait" or row["lease_generation"] != receipt["lease_generation"]
                    or row["attempts"] != receipt["attempts"] or row["budget_wait_reason"] != receipt["reason"]
                    or row["retry_at"] != receipt["retry_at"] or row["budget_operation_id"] != receipt["budget_operation_id"]
                    or any(row[key] is not None for key in ("owner_id", "lease_token", "lease_until"))
                    or row["owner_generation"] != 0):
                _fail("SYNC_OUTCOME_MISMATCH", 409)
            attempts = row["attempts"]
            con.execute("UPDATE sync_queue SET status='queued',retry_at=NULL,budget_wait_reason=NULL,"
                "budget_operation_id=NULL,updated_at=? WHERE item_id=?", (stamp, row["item_id"]))
            status, reason, retry_at, operation = "queued", None, None, None
        result = {"request_id": body["request_id"], "request_sha256": _sha(encoded), "item_id": row["item_id"],
            "lease_generation": row["lease_generation"], "status": status, "attempts": attempts,
            "reason": reason, "retry_at": retry_at, "budget_operation_id": operation,
            "committed_at": stamp, "replayed": False}
        raw_result = _json(result)
        con.execute("INSERT INTO sync_queue_budget_receipt(request_id,request_sha256,request_json,action,"
            "source_library_id,source_node_id,target_library_id,target_node_id,receiver_library_epoch,"
            "recovery_generation,item_id,lease_generation,result_json,result_sha256,committed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (body["request_id"], _sha(encoded), encoded, body["kind"],
            body["source"]["library_id"], body["source"]["node_id"], body["target"]["library_id"], body["target"]["node_id"],
            body["recovery_stream"]["library_epoch"], body["recovery_generation"], row["item_id"],
            row["lease_generation"], raw_result, _sha(raw_result), stamp))
        return result


def defer(con, source, lease, claim, *, request_id, target, recovery_generation, recovery_stream,
          reason, retry_at, budget_operation_id=None, now=None):
    return apply_request(con, {"kind": "budget_defer", "request_id": request_id, "source": source,
        "target": target, "lease": lease, "claim": claim, "recovery_generation": recovery_generation,
        "recovery_stream": recovery_stream, "reason": reason, "retry_at": retry_at,
        "budget_operation_id": budget_operation_id}, now=now)


def resume(con, source, lease, *, request_id, target, defer_request_id, recovery_generation,
           recovery_stream, now=None):
    return apply_request(con, {"kind": "budget_resume", "request_id": request_id, "source": source,
        "target": target, "lease": lease, "defer_request_id": defer_request_id,
        "recovery_generation": recovery_generation, "recovery_stream": recovery_stream}, now=now)
