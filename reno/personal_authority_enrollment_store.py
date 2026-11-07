"""Bounded S5A Store declarations; no filesystem, SQL or runtime authority.

The five public functions authenticate content only. A file receipt is a JSON
declaration, not an actual resource close receipt or evidence of persistence.
Original11 and both pure prepare blobs retain their exact selected bytes.
No continuation, owner, callback, clock, runtime registry or future SQL
successor/complete stub is exposed by this content subset.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_contract as enrollment
from . import personal_authority_enrollment_successor as prepare
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_origin_reservation_contract as origin

FAMILY = "personal_authority_enrollment_store"
FORMAT_VERSION = 1
SCOPE = "namespace3_initial_registration_filesystem_only"
KINDS = ("header", "operation", "slot", "envelope", "record_reservation",
         "published", "receipt")
RECORD_ROLES = ("origin_intent", "local_contract", "init_seal", "genesis_grant", "prepare")
MAX_DEPTH = 16
MAX_NODES = 8192
MAX_INTEGER = authority.MAX_INTEGER
MAX_HEADER_BYTES = 16 * 1024
MAX_OPERATION_BYTES = MAX_SLOT_BYTES = MAX_PUBLISHED_BYTES = MAX_RECEIPT_BYTES = 4 * 1024
MAX_ENVELOPE_BYTES = 16 * 1024
MAX_RECORD_RESERVATION_BYTES = 64 * 1024
MAX_ORIGINAL_BYTES = enrollment.MAX_INPUT_BYTES + 6 * enrollment.MAX_RECORD_BYTES
MAX_BUILD_INPUT_BYTES = MAX_ORIGINAL_BYTES + prepare.MAX_PREPARE_BYTES + prepare.MAX_RESERVATION_BYTES
MAX_WRAPPER_BYTES = 480 * 1024
MAX_FILE_BYTES = 576 * 1024
MAX_CROSS_INPUT_BYTES = 912 * 1024

_PREFIX = ".reno-personal-authority-sql-store-v1-"
_STEMS = ("intent", "contract", "seal", "genesis", "prepare")
_SUFFIXES = ("slot.json", "reservation.json", "envelope.json", "published.json", "receipt.json")
_LAYER_KINDS = ("slot", "record_reservation", "envelope", "published", "receipt")
WRAPPER_NAMES = (_PREFIX + "header.json", _PREFIX + "operation.json") + tuple(
    _PREFIX + stem + "-" + suffix for stem in _STEMS for suffix in _SUFFIXES)
PREPARE_PAYLOAD_NAME = _PREFIX + "prepare-payload.json"
PREPARE_CONTENT_RESERVATION_NAME = _PREFIX + "prepare-content-reservation.json"
FILE_NAMES = (WRAPPER_NAMES[:24] + (PREPARE_PAYLOAD_NAME, PREPARE_CONTENT_RESERVATION_NAME)
              + WRAPPER_NAMES[24:])
_FILE_KINDS = (("header", "operation") + _LAYER_KINDS * 4
               + ("slot", "record_reservation", prepare.PREPARE_KIND,
                  prepare.RESERVATION_KIND, "envelope", "published", "receipt"))
_KIND_MAXIMA = (MAX_HEADER_BYTES, MAX_OPERATION_BYTES, MAX_SLOT_BYTES,
                MAX_ENVELOPE_BYTES, MAX_RECORD_RESERVATION_BYTES,
                MAX_PUBLISHED_BYTES, MAX_RECEIPT_BYTES)
_FILE_MAXIMA = ((MAX_HEADER_BYTES, MAX_OPERATION_BYTES)
                + (MAX_SLOT_BYTES, MAX_RECORD_RESERVATION_BYTES, MAX_ENVELOPE_BYTES,
                   MAX_PUBLISHED_BYTES, MAX_RECEIPT_BYTES) * 4
                + (MAX_SLOT_BYTES, MAX_RECORD_RESERVATION_BYTES, prepare.MAX_PREPARE_BYTES,
                   prepare.MAX_RESERVATION_BYTES, MAX_ENVELOPE_BYTES,
                   MAX_PUBLISHED_BYTES, MAX_RECEIPT_BYTES))
_BASE = frozenset({"format_version", "family", "kind", "scope",
                   "logical_operation_id", "slot_identity"})
_FIELDS = (
    _BASE | {"context", "profiles", "namespace", "namespace_header_sha256",
             "original_refs", "prepare_payload_sha256", "prepare_content_reservation_sha256",
             "layout", "record_order"},
    _BASE | {"header_sha256", "context"},
    _BASE | {"header_sha256", "record_role", "sequence", "parent_record_sha256",
             "envelope_sha256", "payload_sha256", "payload_byte_size"},
    _BASE | {"header_sha256", "context", "record_role", "sequence", "parent_record_sha256",
             "payload_role", "payload_location", "payload_sha256", "payload_byte_size", "refs"},
    _BASE | {"header_sha256", "record_role", "sequence", "parent_record_sha256",
             "envelope_raw_base64", "envelope_byte_size", "envelope_sha256",
             "payload_raw_base64", "payload_byte_size", "payload_sha256"},
    _BASE | {"header_sha256", "record_role", "operation_sha256", "slot_sha256",
             "record_reservation_sha256", "envelope_sha256", "payload_sha256",
             "prepare_content_reservation_sha256"},
    _BASE | {"header_sha256", "record_role", "sequence", "parent_record_sha256",
             "published_sha256", "record_reservation_sha256", "envelope_sha256", "payload_sha256"},
)
_SLOT_FIELDS = frozenset({"slot", "namespace_version", "domain_id", "facility_id",
                          "owner_node_id", "init_operation_id"})
_REFERENCE_FIELDS = frozenset({"role", "codec", "record_kind", "format_version",
                               "raw_byte_size", "raw_sha256", "identity"})
_REFERENCE_SPECS = (
    ("first", "original_origin_reservation", origin.FAMILY, "origin_reservation"),
    ("second", "original_origin_observation", origin.FAMILY, "origin_observation"),
    ("namespace_reservation", "original_namespace3_reservation", namespace.FAMILY, "namespace_reservation"),
    ("root_seal", "original_namespace3_root_seal", namespace.FAMILY, "namespace_root_seal"),
    ("header", "original_namespace3_header", namespace.FAMILY, "namespace_header"),
    ("binding", "original_enrollment_binding", enrollment.FAMILY, "enrollment_binding"),
    ("prefix", "original_authority_prefix", enrollment.FAMILY, "authority_prefix"),
    ("origin_intent", "original_sealed_origin_intent", "authority_format1_no_family_envelope", "origin_init_intent"),
    ("local_contract", "original_local_contract", "authority_format1_no_family_envelope", "local_contract"),
    ("init_seal", "original_init_seal", "authority_format1_no_family_envelope", "init_seal"),
    ("genesis_grant", "original_genesis_grant", "authority_format1_no_family_envelope", "genesis_grant"),
)
_ORIGINAL_KEYS = tuple(spec[0] for spec in _REFERENCE_SPECS)
_ORIGINAL_MAXIMA = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
                    namespace.MAX_RESERVATION_BYTES, namespace.MAX_ROOT_SEAL_BYTES,
                    namespace.MAX_HEADER_BYTES) + (enrollment.MAX_RECORD_BYTES,) * 6
_ORIGINAL_LOCATIONS = (
    ("data_parent", origin.RESERVATION_NAME), ("data_parent", origin.OBSERVATION_NAME),
    ("data_parent", namespace.RESERVATION_NAME), ("namespace_root", namespace.ROOT_SEAL_NAME),
    ("data_parent", namespace.HEADER_NAME),
    ("namespace_root", ".reno-personal-authority-enrollment-binding.json"),
    ("namespace_root", ".reno-personal-authority-enrollment-prefix.json"),
    ("namespace_root", ".reno-personal-authority-origin-init-intent.json"),
    ("namespace_root", ".reno-personal-authority-local-contract.json"),
    ("namespace_root", ".reno-personal-authority-init-seal.json"),
    ("namespace_root", ".reno-personal-authority-genesis-grant.json"),
)
_PREDECESSOR_FIELDS = frozenset({"record_role", "sequence", "slot_sha256",
                                 "record_reservation_sha256", "envelope_sha256", "payload_sha256",
                                 "published_sha256", "receipt_sha256"})
_FACT_SHA_FIELDS = ("slot_sha256", "record_reservation_sha256", "envelope_sha256",
                    "payload_sha256", "published_sha256", "receipt_sha256")
# Derived from the exact closed roles/cardinalities and bounded size scalars,
# not arbitrary strings fitting the nominal 16KiB envelope cap.
_ENVELOPE_STRUCTURAL_MAXIMA = (1970, 3120, 4251, 5416, 10798)


class EnrollmentStoreContractError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_STORE_CONTRACT_" + suffix
        super().__init__(self.code)


ContractError = EnrollmentStoreContractError
Error = EnrollmentStoreContractError


def _fail(suffix):
    raise EnrollmentStoreContractError(suffix) from None


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, fields):
    _need(type(value) is dict and set(value) == set(fields), "FIELDS_INVALID")


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    _need(type(value) is int and minimum <= value <= maximum, "INTEGER_INVALID")


def _sha(value):
    try:
        authority._sha(value)
    except authority.ContractError:
        _fail("SHA_INVALID")


def _uuid(value):
    try:
        authority._uuid(value)
    except authority.ContractError:
        _fail("UUID_INVALID")


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(raw, maximum):
    _need(type(raw) is bytes, "RAW_TYPE_INVALID")
    _need(0 < len(raw) <= maximum, "LIMIT")


def _parser_failure(error):
    # Only finite local codec codes propagate; no original bytes/error text.
    suffix = error.code.removeprefix("ENROLLMENT_SQL_SUCCESSOR_")
    allowed = {"FIELDS_INVALID", "INTEGER_INVALID", "LIMIT", "RAW_TYPE_INVALID",
               "JSON_INVALID", "JSON_DUPLICATE_KEY", "JSON_NONCANONICAL"}
    _fail(suffix if suffix in allowed else "JSON_INVALID")


def _canonical(value):
    try:
        return prepare._canonical(value)
    except prepare.ContractError as error:
        _parser_failure(error)


def _snapshot(value):
    try:
        return prepare._snapshot(value)
    except prepare.ContractError as error:
        _parser_failure(error)


def _json(raw, maximum):
    try:
        return prepare._json(raw, maximum)
    except prepare.ContractError as error:
        _parser_failure(error)


def _context(value):
    try:
        prepare._context(value)
    except prepare.ContractError:
        _fail("BINDING_INVALID")


def _identity(value):
    _uuid(value["logical_operation_id"])
    slot = value["slot_identity"]
    _fields(slot, _SLOT_FIELDS)
    _integer(slot["slot"], 1, 1)
    _integer(slot["namespace_version"], 3, 3)
    for name in ("domain_id", "facility_id", "owner_node_id", "init_operation_id"):
        _uuid(slot[name])
    _need(value["logical_operation_id"] == slot["init_operation_id"], "BINDING_INVALID")
    if "context" in value:
        _context(value["context"])
        context = value["context"]
        expected = {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
                    "facility_id": context["issuer"]["facility_id"],
                    "owner_node_id": context["issuer"]["node_id"],
                    "init_operation_id": context["init_operation_id"]}
        _need(slot == expected and value["logical_operation_id"] == context["init_operation_id"],
              "BINDING_INVALID")


def _layout_value():
    return {"root_name": namespace.ROOT_NAME,
            "original_locations": {key: {"directory": location[0], "name": location[1]}
                                   for key, location in zip(_ORIGINAL_KEYS, _ORIGINAL_LOCATIONS)},
            "wrapper_names": list(WRAPPER_NAMES), "prepare_payload_name": PREPARE_PAYLOAD_NAME,
            "prepare_content_reservation_name": PREPARE_CONTENT_RESERVATION_NAME,
            "publication_order": list(prepare.PUBLICATION_ORDER), "history_gc": "forbidden"}


def _reference(value, spec, maximum, context):
    _fields(value, _REFERENCE_FIELDS)
    _need(value["role"] == spec[1] and value["codec"] == spec[2]
          and value["record_kind"] == spec[3], "ROLE_INVALID")
    _integer(value["format_version"], 1, 1)
    _integer(value["raw_byte_size"], 1, maximum)
    _sha(value["raw_sha256"])
    _context(value["identity"])
    _need(value["identity"] == context, "BINDING_INVALID")


def _header(value):
    context = value["context"]
    try:
        prepare._profiles(value["profiles"], context)
        prepare._namespace(value["namespace"])
    except prepare.ContractError:
        _fail("BINDING_INVALID")
    _fields(value["original_refs"], _ORIGINAL_KEYS)
    for spec, maximum in zip(_REFERENCE_SPECS, _ORIGINAL_MAXIMA):
        _reference(value["original_refs"][spec[0]], spec, maximum, context)
    for name in ("namespace_header_sha256", "prepare_payload_sha256",
                 "prepare_content_reservation_sha256"):
        _sha(value[name])
    _need(value["namespace_header_sha256"] == value["original_refs"]["header"]["raw_sha256"],
          "BINDING_INVALID")
    _need(value["layout"] == _layout_value() and type(value["layout"]) is dict,
          "ROLE_INVALID")
    _need(type(value["record_order"]) is list and value["record_order"] == list(RECORD_ROLES),
          "ROLE_INVALID")


def _record_index(value):
    role = value["record_role"]
    _need(type(role) is str and role in RECORD_ROLES, "ROLE_INVALID")
    index = RECORD_ROLES.index(role)
    if "sequence" in value:
        _integer(value["sequence"], index, index)
    if "parent_record_sha256" in value:
        _sha(value["parent_record_sha256"])
        if index == 0:
            _need(value["parent_record_sha256"] == value["header_sha256"], "BINDING_INVALID")
    return index


def _payload_maximum(index):
    return prepare.MAX_PREPARE_BYTES if index == 4 else enrollment.MAX_RECORD_BYTES


def _envelope(value, index):
    context = value["context"]
    spec = ("prepare", "selected_sql_registration_prepare", prepare.FAMILY, prepare.PREPARE_KIND)
    location = {"directory": "namespace_root", "name": PREPARE_PAYLOAD_NAME}
    if index < 4:
        spec = _REFERENCE_SPECS[7 + index]
        original_location = _ORIGINAL_LOCATIONS[7 + index]
        location = {"directory": original_location[0], "name": original_location[1]}
    _need(value["payload_role"] == spec[1] and value["payload_location"] == location,
          "ROLE_INVALID")
    _integer(value["payload_byte_size"], 1, _payload_maximum(index))
    _sha(value["payload_sha256"])
    refs = value["refs"]
    _fields(refs, {"original_refs", "prepare_content_reservation_ref", "predecessor_facts"})
    keys = _ORIGINAL_KEYS if index == 4 else _ORIGINAL_KEYS[7:8 + index]
    entries = refs["original_refs"]
    _need(type(entries) is list and len(entries) == len(keys), "ROLE_INVALID")
    selected = {}
    for entry, key in zip(entries, keys):
        _fields(entry, {"original_key", "reference"})
        _need(entry["original_key"] == key, "ROLE_INVALID")
        original_index = _ORIGINAL_KEYS.index(key)
        _reference(entry["reference"], _REFERENCE_SPECS[original_index],
                   _ORIGINAL_MAXIMA[original_index], context)
        selected[key] = entry["reference"]
    if index < 4:
        payload_ref = selected[RECORD_ROLES[index]]
        _need(value["payload_byte_size"] == payload_ref["raw_byte_size"]
              and value["payload_sha256"] == payload_ref["raw_sha256"], "BINDING_INVALID")
        _need(refs["prepare_content_reservation_ref"] is None, "ROLE_INVALID")
    else:
        _reference(refs["prepare_content_reservation_ref"],
                   ("prepare_content_reservation", "selected_sql_registration_prepare_reservation",
                    prepare.FAMILY, prepare.RESERVATION_KIND), prepare.MAX_RESERVATION_BYTES, context)
    predecessors = refs["predecessor_facts"]
    _need(type(predecessors) is list and len(predecessors) == index, "ROLE_INVALID")
    for ordinal, fact in enumerate(predecessors):
        _fields(fact, _PREDECESSOR_FIELDS)
        _need(fact["record_role"] == RECORD_ROLES[ordinal], "ROLE_INVALID")
        _integer(fact["sequence"], ordinal, ordinal)
        for name in _FACT_SHA_FIELDS:
            _sha(fact[name])
        _need(fact["payload_sha256"] == selected[RECORD_ROLES[ordinal]]["raw_sha256"],
              "BINDING_INVALID")
    if index:
        _need(value["parent_record_sha256"] == predecessors[-1]["envelope_sha256"],
              "BINDING_INVALID")
    # Local validation enforces the complete fixed domain before nominal-cap
    # acceptance; this also protects the unchanged reservation64KiB ceiling.
    _need(len(_canonical(value)) <= _ENVELOPE_STRUCTURAL_MAXIMA[index], "LIMIT")


def _blob(text, maximum):
    _need(type(text) is str and 0 < len(text) <= 4 * ((maximum + 2) // 3), "LIMIT")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        _fail("BASE64_INVALID")
    _raw(raw, maximum)
    _need(base64.b64encode(raw).decode("ascii") == text, "BASE64_INVALID")
    return raw


def _reservation(value, index):
    _integer(value["envelope_byte_size"], 1, MAX_ENVELOPE_BYTES)
    _integer(value["payload_byte_size"], 1, _payload_maximum(index))
    _sha(value["envelope_sha256"])
    _sha(value["payload_sha256"])
    envelope_raw = _blob(value["envelope_raw_base64"], MAX_ENVELOPE_BYTES)
    payload_raw = _blob(value["payload_raw_base64"], _payload_maximum(index))
    envelope = _normalize(_json(envelope_raw, MAX_ENVELOPE_BYTES), expected_kind="envelope")
    _need(len(envelope_raw) == value["envelope_byte_size"]
          and _hash(envelope_raw) == value["envelope_sha256"]
          and len(payload_raw) == value["payload_byte_size"]
          and _hash(payload_raw) == value["payload_sha256"], "BINDING_INVALID")
    for name in ("header_sha256", "logical_operation_id", "slot_identity", "record_role",
                 "sequence", "parent_record_sha256", "payload_sha256", "payload_byte_size"):
        _need(envelope[name] == value[name], "BINDING_INVALID")


def _normalize(value, *, expected_kind=None):
    _need(type(value) is dict, "FIELDS_INVALID")
    kind = value.get("kind")
    _need(type(kind) is str and kind in KINDS, "VERSION_INVALID")
    if expected_kind is not None:
        _need(kind == expected_kind, "ROLE_INVALID")
    index = KINDS.index(kind)
    _fields(value, _FIELDS[index])
    _integer(value["format_version"], 1, 1)
    _need(value["family"] == FAMILY and value["scope"] == SCOPE, "VERSION_INVALID")
    # Bound the outer canonical reservation before any base64 expansion.
    _need(len(_canonical(value)) <= _KIND_MAXIMA[index], "LIMIT")
    _identity(value)
    if kind == "header":
        _header(value)
    else:
        _sha(value["header_sha256"])
        if kind != "operation":
            ordinal = _record_index(value)
            if kind == "envelope":
                _envelope(value, ordinal)
            elif kind == "record_reservation":
                _reservation(value, ordinal)
            else:
                for name in _FIELDS[index]:
                    if name.endswith("_sha256"):
                        if name == "prepare_content_reservation_sha256" and ordinal < 4:
                            _need(value[name] is None, "ROLE_INVALID")
                        else:
                            _sha(value[name])
                if kind == "slot":
                    _integer(value["payload_byte_size"], 1, _payload_maximum(ordinal))
    return value


def encode(value: dict) -> bytes:
    """Encode one detached wrapper; never reconstruct selected original bytes."""
    return _canonical(_normalize(_snapshot(value)))


def decode(raw: bytes) -> dict:
    """Decode a detached wrapper under local fixed domains; no raw11 closure."""
    return _normalize(_json(raw, MAX_RECORD_RESERVATION_BYTES))


def _preflight(originals, prepare_raw, reservation_raw, file_raws=None, *, prefix=False):
    _need(type(originals) is tuple and len(originals) == 11, "ORIGINAL_BINDING_INVALID")
    for raw, maximum in zip(originals, _ORIGINAL_MAXIMA):
        _raw(raw, maximum)
    _need(sum(map(len, originals)) <= MAX_ORIGINAL_BYTES, "LIMIT")
    _raw(prepare_raw, prepare.MAX_PREPARE_BYTES)
    _raw(reservation_raw, prepare.MAX_RESERVATION_BYTES)
    total = sum(map(len, originals)) + len(prepare_raw) + len(reservation_raw)
    _need(total <= MAX_BUILD_INPUT_BYTES, "LIMIT")
    if file_raws is None:
        return None
    _need(type(file_raws) is tuple and len(file_raws) == 29, "FILE_BINDING_INVALID")
    gap = False
    count = 0
    file_bytes = 0
    # Finish every outer input/shape/budget check before any JSON decoding.
    # A bad late raw type cannot be hidden by an earlier malformed document.
    for raw, maximum in zip(file_raws, _FILE_MAXIMA):
        if raw is None:
            _need(prefix, "FILE_BINDING_INVALID")
            gap = True
            continue
        _need(not gap, "PREFIX_INVALID")
        _raw(raw, maximum)
        total += len(raw)
        file_bytes += len(raw)
        count += 1
    _need(file_bytes <= MAX_FILE_BYTES and total <= MAX_CROSS_INPUT_BYTES, "LIMIT")
    parsed = []
    for raw, maximum, expected_kind in zip(file_raws, _FILE_MAXIMA, _FILE_KINDS):
        if raw is None:
            parsed.append(None)
            continue
        owned = _json(raw, maximum)
        _need(type(owned) is dict, "FIELDS_INVALID")
        _need(owned.get("kind") == expected_kind, "ROLE_INVALID")
        parsed.append(owned)
    return tuple(parsed), count


def _prepared(originals, prepare_raw, reservation_raw):
    try:
        prepare.cross_validate_prepare(prepare_raw, originals=originals)
        prepare.cross_validate_reservation(reservation_raw, prepare_raw, originals=originals)
        payload = prepare.decode(prepare_raw)
    except prepare.ContractError:
        _fail("ORIGINAL_BINDING_INVALID")
    return payload


def _base(kind, payload, header_sha=None):
    value = {"format_version": FORMAT_VERSION, "family": FAMILY, "kind": kind, "scope": SCOPE,
             "logical_operation_id": payload["logical_operation_id"],
             "slot_identity": payload["slot_identity"]}
    if header_sha is not None:
        value["header_sha256"] = header_sha
    return value


def _expected_files(originals, prepare_raw, reservation_raw, payload):
    header = _base("header", payload)
    header.update(context=payload["context"], profiles=payload["profiles"], namespace=payload["namespace"],
                  namespace_header_sha256=_hash(originals[4]), original_refs=payload["original_refs"],
                  prepare_payload_sha256=_hash(prepare_raw),
                  prepare_content_reservation_sha256=_hash(reservation_raw),
                  layout=_layout_value(), record_order=list(RECORD_ROLES))
    header_raw = encode(header)
    header_sha = _hash(header_raw)
    operation = _base("operation", payload, header_sha)
    operation["context"] = payload["context"]
    operation_raw = encode(operation)
    raws = [header_raw, operation_raw]
    facts = []
    parent = header_sha
    for ordinal, role in enumerate(RECORD_ROLES):
        payload_raw = originals[7 + ordinal] if ordinal < 4 else prepare_raw
        original_keys = _ORIGINAL_KEYS[7:8 + ordinal] if ordinal < 4 else _ORIGINAL_KEYS
        location = ({"directory": _ORIGINAL_LOCATIONS[7 + ordinal][0],
                     "name": _ORIGINAL_LOCATIONS[7 + ordinal][1]} if ordinal < 4
                    else {"directory": "namespace_root", "name": PREPARE_PAYLOAD_NAME})
        content_ref = None
        if ordinal == 4:
            content_ref = {"role": "selected_sql_registration_prepare_reservation",
                           "codec": prepare.FAMILY, "record_kind": prepare.RESERVATION_KIND,
                           "format_version": 1, "raw_byte_size": len(reservation_raw),
                           "raw_sha256": _hash(reservation_raw), "identity": payload["context"]}
        envelope = _base("envelope", payload, header_sha)
        envelope.update(context=payload["context"], record_role=role, sequence=ordinal,
                        parent_record_sha256=parent,
                        payload_role=(_REFERENCE_SPECS[7 + ordinal][1] if ordinal < 4
                                      else "selected_sql_registration_prepare"),
                        payload_location=location, payload_sha256=_hash(payload_raw),
                        payload_byte_size=len(payload_raw),
                        refs={"original_refs": [{"original_key": key,
                                                 "reference": payload["original_refs"][key]}
                                                for key in original_keys],
                              "prepare_content_reservation_ref": content_ref,
                              "predecessor_facts": list(facts)})
        envelope_raw = encode(envelope)
        envelope_sha = _hash(envelope_raw)
        slot = _base("slot", payload, header_sha)
        slot.update(record_role=role, sequence=ordinal, parent_record_sha256=parent,
                    envelope_sha256=envelope_sha, payload_sha256=_hash(payload_raw),
                    payload_byte_size=len(payload_raw))
        slot_raw = encode(slot)
        reservation = _base("record_reservation", payload, header_sha)
        reservation.update(record_role=role, sequence=ordinal, parent_record_sha256=parent,
                           envelope_raw_base64=base64.b64encode(envelope_raw).decode("ascii"),
                           envelope_byte_size=len(envelope_raw), envelope_sha256=envelope_sha,
                           payload_raw_base64=base64.b64encode(payload_raw).decode("ascii"),
                           payload_byte_size=len(payload_raw), payload_sha256=_hash(payload_raw))
        record_reservation_raw = encode(reservation)
        published = _base("published", payload, header_sha)
        published.update(record_role=role, operation_sha256=_hash(operation_raw), slot_sha256=_hash(slot_raw),
                         record_reservation_sha256=_hash(record_reservation_raw),
                         envelope_sha256=envelope_sha, payload_sha256=_hash(payload_raw),
                         prepare_content_reservation_sha256=_hash(reservation_raw) if ordinal == 4 else None)
        published_raw = encode(published)
        receipt = _base("receipt", payload, header_sha)
        receipt.update(record_role=role, sequence=ordinal, parent_record_sha256=parent,
                       published_sha256=_hash(published_raw),
                       record_reservation_sha256=_hash(record_reservation_raw),
                       envelope_sha256=envelope_sha, payload_sha256=_hash(payload_raw))
        receipt_raw = encode(receipt)
        layer = [slot_raw, record_reservation_raw]
        if ordinal == 4:
            layer += [prepare_raw, reservation_raw]
        layer += [envelope_raw, published_raw, receipt_raw]
        raws.extend(layer)
        facts.append({"record_role": role, "sequence": ordinal, "slot_sha256": _hash(slot_raw),
                      "record_reservation_sha256": _hash(record_reservation_raw),
                      "envelope_sha256": envelope_sha, "payload_sha256": _hash(payload_raw),
                      "published_sha256": _hash(published_raw), "receipt_sha256": _hash(receipt_raw)})
        parent = envelope_sha
    result = tuple(raws)
    _need(len(result) == 29 and sum(map(len, result)) <= MAX_FILE_BYTES, "LIMIT")
    return result


def build_prepare_files(*, originals: tuple[bytes, ...], prepare_payload_raw: bytes,
                        prepare_content_reservation_raw: bytes) -> tuple[bytes, ...]:
    """Build29 immutable declarations; no persistent file or runtime object."""
    _preflight(originals, prepare_payload_raw, prepare_content_reservation_raw)
    payload = _prepared(originals, prepare_payload_raw, prepare_content_reservation_raw)
    return _expected_files(originals, prepare_payload_raw, prepare_content_reservation_raw, payload)


def _cross(file_raws, originals, prepare_raw, reservation_raw, *, prefix):
    _need(type(file_raws) is tuple and len(file_raws) == 29, "FILE_BINDING_INVALID")
    parsed, count = _preflight(originals, prepare_raw, reservation_raw, file_raws, prefix=prefix)
    payload = _prepared(originals, prepare_raw, reservation_raw)
    expected = _expected_files(originals, prepare_raw, reservation_raw, payload)
    # Fixed semantic validation order differs from publication order: each
    # envelope is validated before its slot/reservation and closing statements.
    order = [0, 1]
    for ordinal in range(4):
        base = 2 + ordinal * 5
        order += [base + 2, base, base + 1, base + 3, base + 4]
    order += [26, 22, 23, 24, 25, 27, 28]
    for index in order:
        if parsed[index] is None:
            continue
        if index in (24, 25):
            _need(file_raws[index] == expected[index], "FILE_BINDING_INVALID")
            try:
                prepare.cross_validate_prepare(file_raws[24], originals=originals)
                if index == 25:
                    prepare.cross_validate_reservation(file_raws[25], file_raws[24], originals=originals)
            except prepare.ContractError:
                _fail("ORIGINAL_BINDING_INVALID")
        else:
            _normalize(parsed[index], expected_kind=_FILE_KINDS[index])
            _need(file_raws[index] == expected[index], "FILE_BINDING_INVALID")
    # Count every selected argument occurrence, including duplicate pure2 raw
    # parameters, regardless of Python buffer sharing; never a runtime budget.
    file_bytes = sum(len(raw) for raw in file_raws if raw is not None)
    _need(file_bytes <= MAX_FILE_BYTES and sum(map(len, originals))
          + len(prepare_raw) + len(reservation_raw) + file_bytes <= MAX_CROSS_INPUT_BYTES, "LIMIT")
    return "unstarted" if count == 0 else "prepare_content_complete" if count == 29 else "prepare_partial"


def cross_validate_prepare_files(file_raws: tuple[bytes, ...], *, originals: tuple[bytes, ...],
                                 prepare_payload_raw: bytes,
                                 prepare_content_reservation_raw: bytes) -> None:
    """Authenticate full29 raw closure; no SQL/persistence/close qualification."""
    _cross(file_raws, originals, prepare_payload_raw, prepare_content_reservation_raw, prefix=False)


def cross_validate_prepare_prefix(file_raws: tuple[bytes | None, ...], *,
                                  originals: tuple[bytes, ...], prepare_payload_raw: bytes,
                                  prepare_content_reservation_raw: bytes) -> str:
    """Classify one of30 detached prefix shapes; this never admits resume I/O."""
    return _cross(file_raws, originals, prepare_payload_raw, prepare_content_reservation_raw, prefix=True)
