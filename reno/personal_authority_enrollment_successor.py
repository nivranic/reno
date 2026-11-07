"""Task2 prepare/reservation content subset; full SQL successor is not implemented.

This module handles only the new pure prepare payload and reservation codecs.
All eleven original bytes participate in complete content cross-validation.
No packet/carrier/capture objects, physical/native owners, SQL, Store I/O,
clocks, random operation IDs, callback admission or writable state are used.
Valid bytes prove neither durable preparation nor COMMIT nor source closure.
Task3 must supply the separate complete joint schema/observation contract before
a full successor can be implemented. No unavailable successor/catalog stub is
exposed here.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_contract as enrollment
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_origin_reservation_contract as origin

FAMILY = "personal_authority_enrollment_sql_prepare"
FORMAT_VERSION = 1
SCOPE = "durable_prepare_content_only"
PREPARE_KIND = "sql_registration_prepare"
RESERVATION_KIND = "sql_registration_prepare_reservation"
MAX_PREPARE_BYTES = 32 * 1024
MAX_RESERVATION_BYTES = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 8192
MAX_INTEGER = authority.MAX_INTEGER
MAX_ORIGINAL_BYTES = enrollment.MAX_INPUT_BYTES + 6 * enrollment.MAX_RECORD_BYTES
MAX_INPUT_BYTES = MAX_ORIGINAL_BYTES + MAX_PREPARE_BYTES + MAX_RESERVATION_BYTES
PUBLICATION_ORDER = ("origin_intent", "local_contract", "init_seal",
                     "genesis_grant", "binding", "prefix")
ORIGINAL_FIELDS = ("first_bytes", "second_bytes", "namespace_reservation_bytes",
                   "root_seal_bytes", "header_bytes", "binding_bytes",
                   "prefix_bytes", "origin_intent_bytes", "local_contract_bytes",
                   "init_seal_bytes", "genesis_grant_bytes")
_ORIGINAL_MAXIMA = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
                    namespace.MAX_RESERVATION_BYTES, namespace.MAX_ROOT_SEAL_BYTES,
                    namespace.MAX_HEADER_BYTES) + (enrollment.MAX_RECORD_BYTES,) * 6
_REFERENCE_SPECS = (
    ("first", "original_origin_reservation", origin.FAMILY, "origin_reservation"),
    ("second", "original_origin_observation", origin.FAMILY, "origin_observation"),
    ("namespace_reservation", "original_namespace3_reservation", namespace.FAMILY,
     "namespace_reservation"),
    ("root_seal", "original_namespace3_root_seal", namespace.FAMILY, "namespace_root_seal"),
    ("header", "original_namespace3_header", namespace.FAMILY, "namespace_header"),
    ("binding", "original_enrollment_binding", enrollment.FAMILY, "enrollment_binding"),
    ("prefix", "original_authority_prefix", enrollment.FAMILY, "authority_prefix"),
    ("origin_intent", "original_sealed_origin_intent", "authority_format1_no_family_envelope",
     "origin_init_intent"),
    ("local_contract", "original_local_contract", "authority_format1_no_family_envelope",
     "local_contract"),
    ("init_seal", "original_init_seal", "authority_format1_no_family_envelope", "init_seal"),
    ("genesis_grant", "original_genesis_grant", "authority_format1_no_family_envelope",
     "genesis_grant"),
)
_COMMON = frozenset({"format_version", "family", "kind", "scope", "context",
                     "logical_operation_id", "slot_identity"})
_PREPARE = _COMMON | {"profiles", "namespace", "original_refs", "publication_order"}
_RESERVATION = _COMMON | {"prepare_payload_base64", "prepare_payload_byte_size",
                         "prepare_payload_sha256"}
_CONTEXT = frozenset({"domain_id", "init_operation_id", "issuer"})
_PROFILES = frozenset({"authority_profile_paths_role", "authority_profile_paths_sha256",
                      "physical_profile_role", "physical_profile_sha256"})
_NAMESPACE = frozenset({"family", "format_version", "root_name",
                       "root_native_identity", "root_path_identity"})
_SLOT = frozenset({"slot", "namespace_version", "domain_id", "facility_id",
                  "owner_node_id", "init_operation_id"})
_REFERENCE = frozenset({"role", "codec", "record_kind", "format_version",
                       "raw_byte_size", "raw_sha256", "identity"})
_REFERENCE_KEYS = frozenset(spec[0] for spec in _REFERENCE_SPECS)


class EnrollmentSqlSuccessorError(ValueError):
    """Finite pure-content codes; never copy foreign payload/error text."""

    def __init__(self, suffix):
        self.code = "ENROLLMENT_SQL_SUCCESSOR_" + suffix
        super().__init__(self.code)


ContractError = EnrollmentSqlSuccessorError
Error = EnrollmentSqlSuccessorError


def _fail(suffix):
    raise EnrollmentSqlSuccessorError(suffix) from None


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "FIELDS_INVALID")


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    _need(type(value) is int and minimum <= value <= maximum, "INTEGER_INVALID")


def _version(value, expected=FORMAT_VERSION):
    _need(type(value) is int and value == expected, "VERSION_INVALID")


def _uuid(value):
    try:
        authority._uuid(value)
    except authority.ContractError:
        _fail("UUID_INVALID")


def _sha(value):
    try:
        authority._sha(value)
    except authority.ContractError:
        _fail("SHA_INVALID")


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(raw, maximum):
    _need(type(raw) is bytes, "RAW_TYPE_INVALID")
    _need(0 < len(raw) <= maximum, "LIMIT")


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _snapshot(value):
    """Copy one child at a time, bounded before growth; reject cycles/subclasses."""
    nodes = text_bytes = 0

    def visit(item, depth, ancestors):
        nonlocal nodes, text_bytes
        nodes += 1
        _need(nodes <= MAX_NODES and depth <= MAX_DEPTH, "LIMIT")
        if type(item) in (dict, list):
            _need(id(item) not in ancestors, "JSON_INVALID")
            chain = ancestors + (id(item),)
            if type(item) is list:
                result = []
                for child in item:
                    result.append(visit(child, depth + 1, chain))
                return result
            result = {}
            for key, child in item.items():
                _need(type(key) is str, "FIELDS_INVALID")
                copied_key = visit(key, depth + 1, chain)
                result[copied_key] = visit(child, depth + 1, chain)
            return result
        if type(item) is str:
            _need(len(item) <= MAX_RESERVATION_BYTES, "LIMIT")
            text_bytes += len(item.encode("utf-8", errors="strict"))
            _need(text_bytes <= MAX_RESERVATION_BYTES, "LIMIT")
            return item
        if type(item) is int:
            _integer(item)
            return item
        _need(item is None, "JSON_INVALID")
        return None

    try:
        return visit(value, 0, ())
    except EnrollmentSqlSuccessorError:
        raise
    except (RuntimeError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _lexical_depth(text):
    quoted = escaped = False
    depth = 0
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _need(depth <= MAX_DEPTH, "LIMIT")
        elif char in "]}":
            depth -= 1
            _need(depth >= 0, "JSON_INVALID")


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _parse_integer(text):
    digits = text[1:] if text.startswith("-") else text
    _need(len(digits) <= 19, "INTEGER_INVALID")
    value = int(text)
    _integer(value)
    return value


def _reject_number(_text):
    _fail("JSON_INVALID")


def _json(raw, maximum):
    _raw(raw, maximum)
    try:
        text = raw.decode("utf-8", errors="strict")
        _lexical_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_parse_integer,
                           parse_float=_reject_number, parse_constant=_reject_number)
        owned = _snapshot(value)
        _need(_canonical(owned) == raw, "JSON_NONCANONICAL")
        return owned
    except EnrollmentSqlSuccessorError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _context(value):
    _fields(value, _CONTEXT)
    _uuid(value["domain_id"])
    _uuid(value["init_operation_id"])
    _fields(value["issuer"], authority.ENDPOINT_FIELDS)
    _uuid(value["issuer"]["facility_id"])
    _uuid(value["issuer"]["node_id"])
    _sha(value["issuer"]["profile_sha256"])


def _profiles(value, context):
    _fields(value, _PROFILES)
    _need(value["authority_profile_paths_role"] == namespace.AUTHORITY_ROLE
          and value["physical_profile_role"] == namespace.PHYSICAL_ROLE, "ROLE_INVALID")
    _sha(value["authority_profile_paths_sha256"])
    _sha(value["physical_profile_sha256"])
    _need(value["authority_profile_paths_sha256"] == context["issuer"]["profile_sha256"],
          "PREPARE_BINDING_INVALID")


def _namespace(value):
    _fields(value, _NAMESPACE)
    _version(value["format_version"], namespace.FORMAT_VERSION)
    _need(value["family"] == namespace.FAMILY and value["root_name"] == namespace.ROOT_NAME,
          "VERSION_INVALID")
    try:
        namespace._native_identity(value["root_native_identity"])
        namespace._path_identity(value["root_path_identity"])
    except namespace.ContractError:
        _fail("PREPARE_BINDING_INVALID")


def _slot(value, context):
    _fields(value, _SLOT)
    _integer(value["slot"], 1, 1)
    _version(value["namespace_version"], 3)
    for name in ("domain_id", "facility_id", "owner_node_id", "init_operation_id"):
        _uuid(value[name])
    expected = {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
                "facility_id": context["issuer"]["facility_id"],
                "owner_node_id": context["issuer"]["node_id"],
                "init_operation_id": context["init_operation_id"]}
    _need(value == expected, "PREPARE_BINDING_INVALID")


def _references(value, context):
    _fields(value, _REFERENCE_KEYS)
    for spec, maximum in zip(_REFERENCE_SPECS, _ORIGINAL_MAXIMA):
        key, role, codec, kind = spec
        item = value[key]
        _fields(item, _REFERENCE)
        _need(item["role"] == role and item["codec"] == codec
              and item["record_kind"] == kind, "ROLE_INVALID")
        _version(item["format_version"])
        _integer(item["raw_byte_size"], 1, maximum)
        _sha(item["raw_sha256"])
        _context(item["identity"])
        _need(item["identity"] == context, "PREPARE_BINDING_INVALID")


def _blob(text):
    _need(type(text) is str and 0 < len(text) <= ((MAX_PREPARE_BYTES + 2) // 3) * 4,
          "LIMIT")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        _fail("PREPARE_BINDING_INVALID")
    _raw(raw, MAX_PREPARE_BYTES)
    _need(base64.b64encode(raw).decode("ascii") == text, "PREPARE_BINDING_INVALID")
    return raw


def _normalize(owned, *, expected_kind=None):
    _need(type(owned) is dict, "FIELDS_INVALID")
    kind = owned.get("kind")
    _need(type(kind) is str and kind in (PREPARE_KIND, RESERVATION_KIND), "VERSION_INVALID")
    if expected_kind is not None:
        _need(kind == expected_kind, "PREPARE_BINDING_INVALID")
    _fields(owned, _PREPARE if kind == PREPARE_KIND else _RESERVATION)
    _version(owned["format_version"])
    _need(owned["family"] == FAMILY and owned["scope"] == SCOPE, "VERSION_INVALID")
    context = owned["context"]
    _context(context)
    _uuid(owned["logical_operation_id"])
    _need(owned["logical_operation_id"] == context["init_operation_id"], "PREPARE_BINDING_INVALID")
    _slot(owned["slot_identity"], context)
    if kind == PREPARE_KIND:
        _profiles(owned["profiles"], context)
        _namespace(owned["namespace"])
        _references(owned["original_refs"], context)
        _need(type(owned["publication_order"]) is list
              and owned["publication_order"] == list(PUBLICATION_ORDER), "ROLE_INVALID")
    else:
        _integer(owned["prepare_payload_byte_size"], 1, MAX_PREPARE_BYTES)
        _sha(owned["prepare_payload_sha256"])
        original = _blob(owned["prepare_payload_base64"])
        # Refuse a reservation-as-payload before recursive content processing.
        payload = _decode(original, expected_kind=PREPARE_KIND)
        _need(owned["prepare_payload_byte_size"] == len(original)
              and owned["prepare_payload_sha256"] == _hash(original)
              and payload["context"] == context
              and payload["logical_operation_id"] == owned["logical_operation_id"]
              and payload["slot_identity"] == owned["slot_identity"], "PREPARE_BINDING_INVALID")
    maximum = MAX_PREPARE_BYTES if kind == PREPARE_KIND else MAX_RESERVATION_BYTES
    _need(len(_canonical(owned)) <= maximum, "LIMIT")
    return owned


def _decode(raw, *, expected_kind=None):
    maximum = MAX_PREPARE_BYTES if expected_kind == PREPARE_KIND else MAX_RESERVATION_BYTES
    return _normalize(_json(raw, maximum), expected_kind=expected_kind)


def encode(value: dict) -> bytes:
    """Encode a new bounded declaration only; no original-byte reconstruction."""
    return _canonical(_normalize(_snapshot(value)))


def decode(raw: bytes) -> dict:
    """Decode a detached declaration; this does not validate selected originals."""
    return _decode(raw)


def _original_inputs(originals, *extra):
    _need(type(originals) is tuple and len(originals) == len(ORIGINAL_FIELDS),
          "ORIGINAL_BINDING_INVALID")
    for raw, maximum in zip(originals, _ORIGINAL_MAXIMA):
        _raw(raw, maximum)
    _need(sum(map(len, originals)) <= MAX_ORIGINAL_BYTES, "LIMIT")
    for raw in extra:
        _raw(raw, MAX_RESERVATION_BYTES)
    _need(sum(map(len, originals)) + sum(map(len, extra)) <= MAX_INPUT_BYTES, "LIMIT")


def _original_materials(originals):
    """Use all eleven selected immutable originals; no regenerated substitute."""
    try:
        content = enrollment.AuthorityPrefixContent(originals[6], *originals[7:])
        enrollment.cross_validate_authority_prefix(content, originals[5], *originals[:5])
        decoders = ((origin.decode,) * 2 + (namespace.decode,) * 3
                    + (enrollment.decode,) * 2 + (authority.decode,) * 4)
        records = tuple(decoder(raw) for decoder, raw in zip(decoders, originals))
        for record, (_, _role, codec, kind) in zip(records, _REFERENCE_SPECS):
            _need(record["kind"] == kind and record["format_version"] == FORMAT_VERSION,
                  "ORIGINAL_BINDING_INVALID")
            if codec != "authority_format1_no_family_envelope":
                _need(record["family"] == codec, "ORIGINAL_BINDING_INVALID")
        sealed_intent = origin._blob(records[1]["origin_intent_base64"], origin.MAX_INTENT_BYTES)
        _need(originals[7] == sealed_intent, "ORIGINAL_BINDING_INVALID")
    except (enrollment.ContractError, origin.ContractError, namespace.ContractError,
            authority.ContractError):
        _fail("ORIGINAL_BINDING_INVALID")
    first, header, intent = records[0], records[4], records[7]
    context = {"domain_id": intent["domain_id"], "init_operation_id": intent["operation_id"],
               "issuer": intent["issuer"]}
    profiles = {"authority_profile_paths_role": namespace.AUTHORITY_ROLE,
                "authority_profile_paths_sha256": first["authority_profile_paths_sha256"],
                "physical_profile_role": namespace.PHYSICAL_ROLE,
                "physical_profile_sha256": first["physical_profile_sha256"]}
    root = {"family": namespace.FAMILY, "format_version": namespace.FORMAT_VERSION,
            "root_name": header["root_name"], "root_native_identity": header["root_native_identity"],
            "root_path_identity": header["root_path_identity"]}
    slot = {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
            "facility_id": context["issuer"]["facility_id"],
            "owner_node_id": context["issuer"]["node_id"],
            "init_operation_id": context["init_operation_id"]}
    references = {
        key: {"role": role, "codec": codec, "record_kind": kind, "format_version": FORMAT_VERSION,
              "raw_byte_size": len(raw), "raw_sha256": _hash(raw), "identity": context}
        for raw, (key, role, codec, kind) in zip(originals, _REFERENCE_SPECS)
    }
    return {"format_version": FORMAT_VERSION, "family": FAMILY, "kind": PREPARE_KIND,
            "scope": SCOPE, "context": context, "profiles": profiles, "namespace": root,
            "logical_operation_id": context["init_operation_id"], "slot_identity": slot,
            "original_refs": references, "publication_order": list(PUBLICATION_ORDER)}


def _prepared(prepare_raw, originals):
    payload = _decode(prepare_raw, expected_kind=PREPARE_KIND)
    expected = _original_materials(originals)
    _need(payload == expected, "PREPARE_BINDING_INVALID")
    return payload


def build_prepare(*, originals: tuple[bytes, ...]) -> bytes:
    """Prepare content from full original11 bytes; no new operation is allocated."""
    _original_inputs(originals)
    return encode(_original_materials(originals))


def cross_validate_prepare(prepare_raw: bytes, *, originals: tuple[bytes, ...]) -> None:
    """Recheck every original and reference; return no persistence/grant object."""
    _original_inputs(originals, prepare_raw)
    _prepared(prepare_raw, originals)


def build_reservation(prepare_raw: bytes, *, originals: tuple[bytes, ...]) -> bytes:
    """Retain the exact provided prepare payload raw bytes in canonical base64."""
    _original_inputs(originals, prepare_raw)
    payload = _prepared(prepare_raw, originals)
    return encode({"format_version": FORMAT_VERSION, "family": FAMILY, "kind": RESERVATION_KIND,
                   "scope": SCOPE, "context": payload["context"],
                   "logical_operation_id": payload["logical_operation_id"],
                   "slot_identity": payload["slot_identity"],
                   "prepare_payload_base64": base64.b64encode(prepare_raw).decode("ascii"),
                   "prepare_payload_byte_size": len(prepare_raw),
                   "prepare_payload_sha256": _hash(prepare_raw)})


def cross_validate_reservation(reservation_raw: bytes, prepare_raw: bytes, *,
                               originals: tuple[bytes, ...]) -> None:
    """Match the stored declaration to exact original payload and all original11."""
    _original_inputs(originals, prepare_raw, reservation_raw)
    payload = _prepared(prepare_raw, originals)
    reservation = _decode(reservation_raw, expected_kind=RESERVATION_KIND)
    _need(_blob(reservation["prepare_payload_base64"]) == prepare_raw
          and reservation["context"] == payload["context"]
          and reservation["logical_operation_id"] == payload["logical_operation_id"]
          and reservation["slot_identity"] == payload["slot_identity"], "PREPARE_BINDING_INVALID")
