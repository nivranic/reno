"""Pure closed source control bodies and exact permanent receipts.

The paired Home validates its original paid ledger. These wire projections
contain no source URL, response body, metadata, cookie, or artifact path.
"""
from __future__ import annotations

import hashlib
import json

from . import sync_queue as queue


MAX_CONTROL_BYTES = 16 * 1024
REQUEST_FIELDS = frozenset(("kind", "request_id", "source", "target", "lease",
    "recovery_generation", "recovery_stream", "claim", "core_sha256",
    "submitted_url_sha256", "failure"))
RECEIPT_FIELDS = frozenset(("format_version", "request_id", "request_sha256", "core_sha256",
    "submitted_url_sha256", "failure_sha256", "item_id", "lease_generation", "attempts",
    "status", "error_class", "committed_at", "replayed"))


class SourceControlContractError(ValueError):
    def __init__(self):
        super().__init__("SOURCE_CONTROL_CONTRACT_INVALID")


def _fail():
    raise SourceControlContractError() from None


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail()


def _sha(value):
    if type(value) is not str or not queue._SHA256.fullmatch(value):
        _fail()


def canonical_json(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_CONTROL_BYTES:
            _fail()
        return encoded
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_request(value):
    """Copy the complete original body, including its historical credentials."""
    try:
        _exact(value, REQUEST_FIELDS)
        if value["kind"] != "source_fail":
            _fail()
        queue._uuid(value["request_id"], "request_id")
        _exact(value["source"], ("library_id", "node_id", "handshake"))
        _exact(value["target"], ("library_id", "node_id"))
        for prefix in ("source", "target"):
            for key in ("library_id", "node_id"):
                queue._uuid(value[prefix][key], key)
        if any(value["source"][key] == value["target"][key] for key in ("library_id", "node_id")):
            _fail()
        handshake = value["source"]["handshake"]
        _exact(handshake, ("protocol_version", "migration_version", "core_schema_sha256"))
        for key in ("protocol_version", "migration_version"):
            queue._integer(handshake[key], key)
        _sha(handshake["core_schema_sha256"])
        queue._lease(value["lease"])
        queue._integer(value["recovery_generation"], "recovery_generation", minimum=0)
        _exact(value["recovery_stream"], ("library_id", "library_epoch"))
        queue._uuid(value["recovery_stream"]["library_id"], "library_id")
        queue._integer(value["recovery_stream"]["library_epoch"], "library_epoch")
        if value["recovery_stream"]["library_id"] != value["target"]["library_id"]:
            _fail()
        _exact(value["claim"], ("item_id", "lease_token", "lease_generation"))
        queue._integer(value["claim"]["item_id"], "item_id")
        queue._integer(value["claim"]["lease_generation"], "lease_generation")
        queue._uuid(value["claim"]["lease_token"], "lease_token")
        _sha(value["core_sha256"])
        _sha(value["submitted_url_sha256"])
        # Lazy dependency: source_terminal owns this pure normalizer and the
        # separate Home ledger authority. There is no sync_client import cycle.
        from .source_terminal import normalize_failure
        failure = normalize_failure(value["failure"])
        if failure != value["failure"]:
            _fail()
        return json.loads(canonical_json(value))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def request_sha256(value):
    return digest(normalize_request(value))


def failure_sha256(value):
    from .source_terminal import normalize_failure
    return digest(normalize_failure(value))


def normalize_receipt(request, value):
    """A receipt proves exactly one original body, core, failure and claim."""
    try:
        request = normalize_request(request)
        _exact(value, RECEIPT_FIELDS)
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail()
        for key in ("request_sha256", "core_sha256", "submitted_url_sha256", "failure_sha256"):
            _sha(value[key])
        queue._uuid(value["request_id"], "request_id")
        queue._integer(value["item_id"], "item_id")
        queue._integer(value["lease_generation"], "lease_generation")
        queue._integer(value["attempts"], "attempts", minimum=1, maximum=queue.MAX_ATTEMPTS)
        if (value["request_id"] != request["request_id"]
                or value["request_sha256"] != digest(request)
                or any(value[key] != request[key] for key in ("core_sha256", "submitted_url_sha256"))
                or value["failure_sha256"] != failure_sha256(request["failure"])
                or any(value[key] != request["claim"][key] for key in ("item_id", "lease_generation"))
                or value["status"] != "dead" or value["error_class"] != request["failure"]["error_class"]
                or queue._stored_time(value["committed_at"]) is None or type(value["replayed"]) is not bool):
            _fail()
        return json.loads(canonical_json(value))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()
