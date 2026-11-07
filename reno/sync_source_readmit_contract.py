"""Bounded wire contracts for original-control disposition and current admission.

These pure copies validate representation and digest bindings, never authority.
In particular, an embedded registration/disposition is not proof of its origin,
and a missing receipt is not evidence of a never-dispatched request. Consumers
must resolve permanent records, continuity anchors, the original parent defer,
and the Home's sealed preservation/manual/timer authority before any effect.
Nothing here changes the current handshake, schema, queue or send permissions.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import math
import uuid

from . import sync_queue as queue, sync_source_control_contract as source_control


MAX_CONTROL_BYTES = 32 * 1024
MAX_READMIT_BYTES = 16 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 2048
MIN_READMIT_MIGRATION = 16
MIN_READMIT_V2_MIGRATION = 17
TIMER_OPERATION_PREFIX = "sync-budget-timer:v2:"

_CURRENT = frozenset(("kind", "request_id", "source", "target", "lease",
    "recovery_generation", "recovery_stream", "control_revision"))
_ORIGINAL = frozenset(("core_sha256", "home_epoch", "item_id", "submitted_url_sha256",
    "claim", "recovery_generation", "recovery_stream"))
_PRESERVATION = frozenset(("format_version", "frontier_sha256", "certificate_sha256",
    "from_epoch", "to_epoch"))
_BOUND = _CURRENT | {"original", "current_home_epoch", "preservation"}
REGISTER_FIELDS = _CURRENT | {"continuity", "core_sha256", "item_id", "original_request"}
DISPOSE_FIELDS = _BOUND | {"original_request", "registration"}
DISPOSE_FIELDS_V2 = DISPOSE_FIELDS | {"format_version", "continuity"}
READMIT_FIELDS = _BOUND | {"expected_claim", "previous_admission_id", "dispositions",
    "defer_receipt", "resume_receipt", "manual_resume_basis"}
READMIT_FIELDS_V2 = READMIT_FIELDS - {"manual_resume_basis"} | {"format_version", "resume_basis"}
_CONTROL_FIELDS = frozenset(("format_version", "request_id", "request_sha256",
    "original_request_id", "original_request_sha256", "original_kind", "core_sha256",
    "item_id", "committed_at", "replayed"))
REGISTER_RESULT_FIELDS = _CONTROL_FIELDS | {"continuity_id", "registration_sha256"}
DISPOSE_RESULT_FIELDS = _CONTROL_FIELDS | {"disposition", "original_receipt",
    "continuity_id", "registration_sha256"}
READMIT_RESULT_FIELDS = frozenset(("format_version", "admission_id", "request_sha256",
    "admission_sha256", "core_sha256", "item_id", "submitted_url_sha256",
    "current_home_epoch", "preservation", "source_identity", "target_identity",
    "session", "claim", "recovery_generation", "recovery_stream", "control_revision",
    "attempts", "attempt_basis", "previous_admission_id", "disposition_sha256s",
    "committed_at", "replayed"))
_BUDGET_RECEIPT = frozenset(("request_id", "request_sha256", "item_id", "lease_generation",
    "status", "attempts", "reason", "retry_at", "budget_operation_id", "committed_at", "replayed"))


class SourceReadmitContractError(ValueError):
    def __init__(self):
        super().__init__("SOURCE_READMIT_CONTRACT_INVALID")


def _fail():
    raise SourceReadmitContractError() from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
                OverflowError, RecursionError):
            _fail()
    return checked


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _version(value):
    if type(value) is not int or value != 1:
        _fail()


def _sha(value):
    if type(value) is not str or not queue._SHA256.fullmatch(value):
        _fail()


def _time(value):
    if type(value) is not str or queue._stored_time(value) is None:
        _fail()


def _enum(value, choices):
    if type(value) is not str or value not in choices:
        _fail()


def _json_bounds(value, maximum):
    """Check containers before serializing; cycles hit the bounded depth gate."""
    if type(maximum) is not int or not 1 <= maximum <= MAX_CONTROL_BYTES:
        _fail()
    pending, nodes = [(value, 0)], 0
    while pending:
        child, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _fail()
        if type(child) is dict:
            if len(child) > MAX_JSON_NODES - nodes:
                _fail()
            for key, item in child.items():
                if type(key) is not str or len(key) > maximum:
                    _fail()
                key.encode("utf-8")
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif type(child) is list:
            if len(child) > MAX_JSON_NODES - nodes:
                _fail()
            pending.extend((item, depth + 1) for item in child)
        elif type(child) is str:
            if len(child) > maximum:
                _fail()
            child.encode("utf-8")
        elif type(child) is float:
            if not math.isfinite(child):
                _fail()
        elif child is not None and type(child) not in (int, bool):
            _fail()


@_checked
def canonical_json(value, *, maximum=MAX_CONTROL_BYTES):
    _json_bounds(value, maximum)
    # iterencode bounds the aggregate as it is formed, rather than first
    # materializing thousands of individually bounded strings into a huge body.
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                               separators=(",", ":"))
    chunks, total = [], 0
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
        if total > maximum:
            _fail()
        chunks.append(chunk)
    return "".join(chunks)


@_checked
def digest(value, *, maximum=MAX_CONTROL_BYTES):
    return hashlib.sha256(canonical_json(value, maximum=maximum).encode("utf-8")).hexdigest()


def _copy(value, maximum=MAX_CONTROL_BYTES):
    return json.loads(canonical_json(value, maximum=maximum))


def _identity(value, *, source=False):
    _exact(value, ("library_id", "node_id", "handshake") if source else ("library_id", "node_id"))
    for key in ("library_id", "node_id"):
        queue._uuid(value[key], key)
    if source:
        handshake = value["handshake"]
        _exact(handshake, ("protocol_version", "migration_version", "core_schema_sha256"))
        for key in ("protocol_version", "migration_version"):
            queue._integer(handshake[key], key)
        _sha(handshake["core_schema_sha256"])


def _peer(value):
    return {key: value[key] for key in ("library_id", "node_id")}


def _claim(value):
    _exact(value, ("item_id", "lease_token", "lease_generation"))
    queue._integer(value["item_id"], "item_id")
    queue._integer(value["lease_generation"], "lease_generation")
    queue._uuid(value["lease_token"], "lease_token")


def _stream(value, target):
    _exact(value, ("library_id", "library_epoch"))
    queue._uuid(value["library_id"], "library_id")
    queue._integer(value["library_epoch"], "library_epoch")
    if value["library_id"] != target["library_id"]:
        _fail()


def _current(value):
    queue._uuid(value["request_id"], "request_id")
    _identity(value["source"], source=True)
    _identity(value["target"])
    if any(value["source"][key] == value["target"][key] for key in ("library_id", "node_id")):
        _fail()
    handshake = value["source"]["handshake"]
    # Released protocol2 bodies remain byte-exact historical inputs. Current
    # protocol3 representation begins at schema17; accepting its shape here
    # grants no runtime capability and cannot adopt an unknown future peer.
    minimum = {2: MIN_READMIT_MIGRATION, 3: MIN_READMIT_V2_MIGRATION}.get(
        handshake["protocol_version"])
    if minimum is None or handshake["migration_version"] < minimum:
        _fail()
    # Version/shape is a representation gate; only real capability/activation
    # witnesses can authorize registration or admission in a later consumer.
    queue._lease(value["lease"])
    queue._integer(value["recovery_generation"], "recovery_generation", minimum=0)
    queue._integer(value["control_revision"], "control_revision", minimum=0)
    _stream(value["recovery_stream"], value["target"])


def _preservation(value, original_epoch, current_epoch, *, allow_same_epoch=False):
    # Only explicit v2 can express an ordinary same-epoch continuation
    # without pretending that a knowledge-restore certificate was produced.
    if value is None and allow_same_epoch and original_epoch == current_epoch:
        return
    _exact(value, _PRESERVATION)
    _version(value["format_version"])
    for key in ("frontier_sha256", "certificate_sha256"):
        _sha(value[key])
    for key in ("from_epoch", "to_epoch"):
        queue._integer(value[key], key)
    if value["from_epoch"] != original_epoch or value["to_epoch"] != current_epoch or current_epoch < original_epoch:
        _fail()


def _bound(value, *, allow_same_epoch=False):
    _current(value)
    original = value["original"]
    _exact(original, _ORIGINAL)
    for key in ("core_sha256", "submitted_url_sha256"):
        _sha(original[key])
    for key in ("home_epoch", "item_id"):
        queue._integer(original[key], key)
    _claim(original["claim"])
    queue._integer(original["recovery_generation"], "recovery_generation", minimum=0)
    _stream(original["recovery_stream"], value["target"])
    queue._integer(value["current_home_epoch"], "current_home_epoch")
    _preservation(value["preservation"], original["home_epoch"], value["current_home_epoch"],
                  allow_same_epoch=allow_same_epoch)
    if (original["item_id"] != original["claim"]["item_id"]
            or original["recovery_stream"] != value["recovery_stream"]
            or original["recovery_generation"] > value["recovery_generation"]):
        _fail()


def _old_request(value):
    # Both reused validators are pure; no client import or request side effects.
    if type(value) is not dict:
        _fail()
    _json_bounds(value, source_control.MAX_CONTROL_BYTES)
    if value.get("kind") == "source_fail":
        return source_control.normalize_request(value)
    from .sync_budget_wait import _validate
    return json.loads(_validate(value))


def _old_binding(old, request, *, registration=False):
    if (old["request_id"] == request["request_id"]
            or _peer(old["source"]) != _peer(request["source"])
            or old["target"] != request["target"]):
        _fail()
    if registration:
        if any(old[key] != request[key] for key in (
                "source", "target", "lease", "recovery_generation", "recovery_stream")):
            _fail()
        item, core = request["item_id"], request["core_sha256"]
    else:
        original = request["original"]
        item, core = original["item_id"], original["core_sha256"]
        if (old["recovery_stream"] != request["recovery_stream"]
                or old["recovery_generation"] > request["recovery_generation"]):
            _fail()
    if old["kind"] != "budget_resume" and old["claim"]["item_id"] != item:
        _fail()
    if old["kind"] == "source_fail":
        if old["core_sha256"] != core:
            _fail()
        if not registration and old["submitted_url_sha256"] != request["original"]["submitted_url_sha256"]:
            _fail()
    # Controls generated under an earlier current admission may have a newer
    # token than the original core. SQL must check the exact claim lineage.


@_checked
def normalize_register_request(value):
    value = _copy(value)
    _exact(value, REGISTER_FIELDS)
    if value["kind"] != "source_register":
        _fail()
    _current(value)
    _continuity(value["continuity"], value["recovery_stream"])
    _sha(value["core_sha256"])
    queue._integer(value["item_id"], "item_id")
    old = _old_request(value["original_request"])
    _old_binding(old, value, registration=True)
    return value


def _continuity(value, stream):
    _exact(value, ("format_version", "continuity_id", "contract_sha256", "target_epoch"))
    _version(value["format_version"])
    queue._uuid(value["continuity_id"], "continuity_id")
    _sha(value["contract_sha256"])
    queue._integer(value["target_epoch"], "target_epoch")
    if value["target_epoch"] != stream["library_epoch"]:
        _fail()


def _control_result_shape(value, fields, *, version=1):
    _exact(value, fields)
    if type(value["format_version"]) is not int or value["format_version"] != version:
        _fail()
    for key in ("request_id", "original_request_id"):
        queue._uuid(value[key], key)
    if value["request_id"] == value["original_request_id"]:
        _fail()
    for key in ("request_sha256", "original_request_sha256", "core_sha256"):
        _sha(value[key])
    queue._integer(value["item_id"], "item_id")
    _enum(value["original_kind"], ("budget_defer", "budget_resume", "source_fail"))
    _time(value["committed_at"])
    if type(value["replayed"]) is not bool:
        _fail()


def _semantic_digest(value, own=None, maximum=MAX_CONTROL_BYTES):
    return digest({key: child for key, child in value.items() if key not in (own, "replayed")}, maximum=maximum)


def _registration_shape(value):
    _control_result_shape(value, REGISTER_RESULT_FIELDS)
    queue._uuid(value["continuity_id"], "continuity_id")
    _sha(value["registration_sha256"])
    if value["registration_sha256"] != _semantic_digest(value, "registration_sha256"):
        _fail()


def _original_result_binding(request, value):
    old = request["original_request"]
    original = request.get("original")
    if (value["original_request_id"] != old["request_id"]
            or value["original_request_sha256"] != digest(old)
            or value["original_kind"] != old["kind"]
            or value["core_sha256"] != (request["core_sha256"] if original is None else original["core_sha256"])
            or value["item_id"] != (request["item_id"] if original is None else original["item_id"])):
        _fail()


@_checked
def normalize_register_result(request, value):
    request = normalize_register_request(request)
    value = _copy(value)
    _registration_shape(value)
    _original_result_binding(request, value)
    if (value["request_id"] != request["request_id"] or value["request_sha256"] != digest(request)
            or value["continuity_id"] != request["continuity"]["continuity_id"]):
        _fail()
    return value


@_checked
def normalize_dispose_request(value):
    value = _copy(value)
    if type(value) is not dict:
        _fail()
    version = _dispose_request_version(value)
    _exact(value, DISPOSE_FIELDS if version == 1 else DISPOSE_FIELDS_V2)
    if value["kind"] != "source_dispose":
        _fail()
    _bound(value, allow_same_epoch=version == 2)
    if version == 2:
        if value["source"]["handshake"]["migration_version"] < MIN_READMIT_V2_MIGRATION:
            _fail()
        _continuity(value["continuity"], value["recovery_stream"])
    _old_binding(_old_request(value["original_request"]), value)
    registration = value["registration"]
    if registration is not None:
        _registration_shape(registration)
        _original_result_binding(value, registration)
        if (registration["replayed"] is not False
                or value["original_request"]["source"]["handshake"]["migration_version"] < MIN_READMIT_MIGRATION):
            _fail()
        if version == 2 and registration["continuity_id"] != value["continuity"]["continuity_id"]:
            _fail()
    return value


def _dispose_request_version(value):
    # Original implicit1 bytes stay implicit; explicit1 is never an alias.
    if "format_version" not in value:
        return 1
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _fail()
    return 2


def _budget_receipt_shape(value):
    from .budget_contract import DEFER_REASONS
    _exact(value, _BUDGET_RECEIPT)
    queue._uuid(value["request_id"], "request_id")
    _sha(value["request_sha256"])
    for key in ("item_id", "lease_generation"):
        queue._integer(value[key], key)
    queue._integer(value["attempts"], "attempts", minimum=0, maximum=queue.MAX_ATTEMPTS - 1)
    _time(value["committed_at"])
    if type(value["replayed"]) is not bool:
        _fail()
    if value["status"] == "budget_wait":
        _enum(value["reason"], DEFER_REASONS)
        if value["retry_at"] is not None:
            _time(value["retry_at"])
        if value["budget_operation_id"] is not None:
            queue._uuid(value["budget_operation_id"], "budget_operation_id")
    elif value["status"] != "queued" or any(value[key] is not None for key in ("reason", "retry_at", "budget_operation_id")):
        _fail()


def _budget_receipt_for(old, receipt, *, defer_receipt=None):
    _budget_receipt_shape(receipt)
    if receipt["request_id"] != old["request_id"] or receipt["request_sha256"] != digest(old):
        _fail()
    if old["kind"] == "budget_defer":
        if (receipt["status"] != "budget_wait"
                or any(receipt[key] != old["claim"][key] for key in ("item_id", "lease_generation"))
                or any(receipt[key] != old[key] for key in ("reason", "retry_at", "budget_operation_id"))):
            _fail()
    else:
        # The resume body has no item or attempts. The original parent receipt
        # must be supplied from the consumer's permanent ledger, not invented.
        _budget_receipt_shape(defer_receipt)
        if (receipt["status"] != "queued" or defer_receipt["status"] != "budget_wait"
                or defer_receipt["request_id"] != old["defer_request_id"]
                or any(receipt[key] != defer_receipt[key] for key in ("item_id", "lease_generation", "attempts"))):
            _fail()


def _dispose_shape(value):
    _dispose_result_shape(value, version=1)


def _dispose_shape2(value):
    _dispose_result_shape(value, version=2)


def _dispose_shape_versioned(value):
    if type(value) is not dict:
        _fail()
    version = value.get("format_version")
    if type(version) is not int or version not in {1, 2}:
        _fail()
    (_dispose_shape if version == 1 else _dispose_shape2)(value)


def _dispose_result_shape(value, *, version):
    _control_result_shape(value, DISPOSE_RESULT_FIELDS, version=version)
    _enum(value["disposition"], ("applied", "not_applied"))
    if (value["continuity_id"] is None) != (value["registration_sha256"] is None):
        _fail()
    if value["continuity_id"] is not None:
        queue._uuid(value["continuity_id"], "continuity_id")
        _sha(value["registration_sha256"])
    receipt = value["original_receipt"]
    if value["disposition"] == "not_applied":
        if receipt is not None or value["continuity_id"] is None:
            _fail()
        return
    if type(receipt) is not dict or receipt.get("replayed") is not False:
        _fail()
    if value["original_kind"] in ("budget_defer", "budget_resume"):
        _budget_receipt_shape(receipt)
        if receipt["status"] != ("budget_wait" if value["original_kind"] == "budget_defer" else "queued"):
            _fail()
    else:
        _exact(receipt, source_control.RECEIPT_FIELDS)
        _version(receipt["format_version"])
        for key in ("core_sha256", "submitted_url_sha256", "failure_sha256"):
            _sha(receipt[key])
        queue._integer(receipt["lease_generation"], "lease_generation")
        queue._integer(receipt["attempts"], "attempts", minimum=1, maximum=queue.MAX_ATTEMPTS)
        _enum(receipt["error_class"], ("invalid_url", "source_unavailable", "rate_limited", "network", "media_invalid"))
        _time(receipt["committed_at"])
        if receipt["status"] != "dead" or receipt["core_sha256"] != value["core_sha256"]:
            _fail()
    if (receipt["request_id"] != value["original_request_id"]
            or receipt["request_sha256"] != value["original_request_sha256"]
            or type(receipt["item_id"]) is not int or receipt["item_id"] != value["item_id"]):
        _fail()


@_checked
def normalize_dispose_result(request, value, *, defer_receipt=None):
    request = normalize_dispose_request(request)
    value = _copy(value)
    (_dispose_shape if _dispose_request_version(request) == 1 else _dispose_shape2)(value)
    _original_result_binding(request, value)
    if value["request_id"] != request["request_id"] or value["request_sha256"] != digest(request):
        _fail()
    registration = request["registration"]
    if registration is None:
        if value["disposition"] != "applied" or value["continuity_id"] is not None:
            _fail()
    elif any(value[key] != registration[key] for key in ("continuity_id", "registration_sha256")):
        _fail()
    if value["disposition"] == "applied":
        old, receipt = request["original_request"], value["original_receipt"]
        if old["kind"] == "source_fail":
            source_control.normalize_receipt(old, receipt)
        else:
            _budget_receipt_for(old, receipt, defer_receipt=defer_receipt)
    return value


def _resume_basis(value, defer_receipt):
    _exact(value, ("authorization_id", "authorization_sha256", "defer_request_id"))
    for key in ("authorization_id", "defer_request_id"):
        queue._uuid(value[key], key)
    _sha(value["authorization_sha256"])
    if value["defer_request_id"] != defer_receipt["request_id"]:
        _fail()


@_checked
def timer_operation_id(defer_request_id):
    """Stable timer name for one original defer; this identifier grants nothing."""
    queue._uuid(defer_request_id, "defer_request_id")
    return str(uuid.uuid5(uuid.UUID(defer_request_id), TIMER_OPERATION_PREFIX + defer_request_id))


def _resume_basis_v2(value, defer_receipt):
    if type(value) is not dict:
        _fail()
    if value.get("kind") == "manual":
        fields = ("authorization_id", "authorization_sha256", "defer_request_id")
        _exact(value, {"kind", *fields})
        _resume_basis({key: value[key] for key in fields}, defer_receipt)
    elif value.get("kind") == "timer":
        _exact(value, ("kind", "timer_operation_id", "timer_intent_sha256", "timer_receipt_sha256",
                       "defer_request_id", "defer_request_sha256", "defer_receipt_sha256", "retry_at"))
        for key in ("timer_operation_id", "defer_request_id"):
            queue._uuid(value[key], key)
        for key in ("timer_intent_sha256", "timer_receipt_sha256", "defer_request_sha256", "defer_receipt_sha256"):
            _sha(value[key])
        _time(value["retry_at"])
        if (value["defer_request_id"] != defer_receipt["request_id"]
                or value["timer_operation_id"] != timer_operation_id(defer_receipt["request_id"])
                or value["defer_request_sha256"] != defer_receipt["request_sha256"]
                or value["defer_receipt_sha256"] != digest(defer_receipt)
                or value["retry_at"] != defer_receipt["retry_at"]):
            _fail()
        # The receipt IDs/hashes are representation only. A genuine Home
        # producer and Cloud's actual parent SQL/clock must prove that it is due.
    else:
        _fail()


def _readmit_version(value):
    if "format_version" not in value:
        return 1
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _fail()
    return 2


@_checked
def normalize_readmit_request(value):
    value = _copy(value, MAX_READMIT_BYTES)
    if type(value) is not dict:
        _fail()
    version = _readmit_version(value)
    _exact(value, READMIT_FIELDS if version == 1 else READMIT_FIELDS_V2)
    if value["kind"] != "source_readmit":
        _fail()
    _bound(value, allow_same_epoch=version == 2)
    if version == 2 and value["source"]["handshake"]["migration_version"] < MIN_READMIT_V2_MIGRATION:
        _fail()
    resume_basis = value["manual_resume_basis" if version == 1 else "resume_basis"]
    _claim(value["expected_claim"])
    original = value["original"]
    if (value["expected_claim"]["item_id"] != original["item_id"]
            or value["expected_claim"]["lease_generation"] < original["claim"]["lease_generation"]
            or value["expected_claim"]["lease_generation"] == queue.MAX_INTEGER):
        _fail()
    previous = value["previous_admission_id"]
    if previous is None:
        if value["expected_claim"] != original["claim"]:
            _fail()
    else:
        queue._uuid(previous, "previous_admission_id")
        if previous == value["request_id"]:
            _fail()
    dispositions = value["dispositions"]
    if type(dispositions) is not list or len(dispositions) > 2:
        _fail()
    seen, old_ids = set(), set()
    for result in dispositions:
        (_dispose_shape if version == 1 else _dispose_shape_versioned)(result)
        if (result["replayed"] is not False or result["request_id"] in seen
                or result["original_request_id"] in old_ids
                or result["request_id"] == value["request_id"]
                or result["core_sha256"] != original["core_sha256"]
                or result["item_id"] != original["item_id"]
                or result["original_kind"] == "source_fail" and result["disposition"] == "applied"):
            _fail()
        seen.add(result["request_id"])
        old_ids.add(result["original_request_id"])
    defer, resume = value["defer_receipt"], value["resume_receipt"]
    if defer is None and resume is None:
        if (resume_basis is not None
                or any(item["original_kind"] == "budget_defer" and item["disposition"] == "applied" for item in dispositions)):
            _fail()
    else:
        for receipt in (defer, resume):
            _budget_receipt_shape(receipt)
            if (receipt["replayed"] is not False or receipt["item_id"] != original["item_id"]
                    or receipt["lease_generation"] != value["expected_claim"]["lease_generation"]):
                _fail()
        if (defer["status"] != "budget_wait" or resume["status"] != "queued"
                or defer["request_id"] == resume["request_id"]
                or defer["attempts"] != resume["attempts"]):
            _fail()
        (_resume_basis if version == 1 else _resume_basis_v2)(resume_basis, defer)
        for result in dispositions:
            for receipt in (defer, resume):
                if (result["original_request_id"] == receipt["request_id"]
                        and (result["disposition"] != "applied" or result["original_receipt"] != receipt)):
                    _fail()
    return value


@_checked
def normalize_readmit_result(request, value):
    request = normalize_readmit_request(request)
    version = _readmit_version(request)
    value = _copy(value, MAX_READMIT_BYTES)
    _exact(value, READMIT_RESULT_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != version:
        _fail()
    queue._uuid(value["admission_id"], "admission_id")
    for key in ("request_sha256", "admission_sha256", "core_sha256", "submitted_url_sha256"):
        _sha(value[key])
    _identity(value["source_identity"])
    _identity(value["target_identity"])
    queue._lease(value["session"])
    _claim(value["claim"])
    for key in ("item_id", "current_home_epoch"):
        queue._integer(value[key], key)
    for key in ("recovery_generation", "control_revision"):
        queue._integer(value[key], key, minimum=0)
    _stream(value["recovery_stream"], request["target"])
    _preservation(value["preservation"], request["original"]["home_epoch"], request["current_home_epoch"],
                  allow_same_epoch=version == 2)
    queue._integer(value["attempts"], "attempts", minimum=1, maximum=queue.MAX_ATTEMPTS)
    _enum(value["attempt_basis"], ("held_claim", "original_defer_refund"))
    _time(value["committed_at"])
    if type(value["replayed"]) is not bool:
        _fail()
    if (value["admission_id"] != request["request_id"]
            or value["request_sha256"] != digest(request, maximum=MAX_READMIT_BYTES)
            or any(value[key] != request["original"][key] for key in ("core_sha256", "item_id", "submitted_url_sha256"))
            or any(value[key] != request[key] for key in ("current_home_epoch", "preservation",
                "recovery_generation", "recovery_stream", "control_revision", "previous_admission_id"))
            or value["source_identity"] != _peer(request["source"])
            or value["target_identity"] != request["target"] or value["session"] != request["lease"]
            or value["claim"]["item_id"] != request["original"]["item_id"]
            or value["claim"]["lease_generation"] != request["expected_claim"]["lease_generation"] + 1
            or value["claim"]["lease_token"] == request["expected_claim"]["lease_token"]):
        _fail()
    expected_digests = [disposition_sha256(result) for result in request["dispositions"]]
    if type(value["disposition_sha256s"]) is not list or value["disposition_sha256s"] != expected_digests:
        _fail()
    refund = request["defer_receipt"] is not None
    if (value["attempt_basis"] != ("original_defer_refund" if refund else "held_claim")
            or refund and value["attempts"] != request["defer_receipt"]["attempts"] + 1
            or value["admission_sha256"] != _semantic_digest(value, "admission_sha256", MAX_READMIT_BYTES)):
        _fail()
    # held_claim attempts require the original SQL row; no attempt count is
    # present in this wire request from which a pure validator could infer it.
    return value


@_checked
def normalize_request(value):
    if type(value) is not dict:
        _fail()
    function = {"source_register": normalize_register_request,
                "source_dispose": normalize_dispose_request,
                "source_readmit": normalize_readmit_request}.get(value.get("kind"))
    if function is None:
        _fail()
    return function(value)


@_checked
def normalize_result(request, value, *, defer_receipt=None):
    request = normalize_request(request)
    if request["kind"] == "source_register":
        return normalize_register_result(request, value)
    if request["kind"] == "source_dispose":
        return normalize_dispose_result(request, value, defer_receipt=defer_receipt)
    return normalize_readmit_result(request, value)


@_checked
def request_sha256(value):
    value = normalize_request(value)
    return digest(value, maximum=MAX_READMIT_BYTES if value["kind"] == "source_readmit" else MAX_CONTROL_BYTES)


@_checked
def registration_sha256(value):
    """Hash a shaped result without self-reference or presentation replay flag."""
    value = _copy(value)
    _control_result_shape(value, REGISTER_RESULT_FIELDS)
    queue._uuid(value["continuity_id"], "continuity_id")
    _sha(value["registration_sha256"])
    return _semantic_digest(value, "registration_sha256")


@_checked
def disposition_sha256(value):
    value = _copy(value)
    _dispose_shape_versioned(value)
    return _semantic_digest(value)


@_checked
def admission_sha256(value):
    """Compute the semantic digest; normalize_result checks its request binding."""
    value = _copy(value, MAX_READMIT_BYTES)
    _exact(value, READMIT_RESULT_FIELDS)
    _sha(value["admission_sha256"])
    if type(value["replayed"]) is not bool:
        _fail()
    return _semantic_digest(value, "admission_sha256", MAX_READMIT_BYTES)
