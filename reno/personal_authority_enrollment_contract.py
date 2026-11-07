"""Closed enrollment content bindings; no I/O, ownership, or runtime grant.

The original origin observation owns the intent bytes and SQL observation.
Namespace3 owns its distinct ARRAY/DICT profile roles and root declarations.
This family links those original bytes without extending authority format1.
``content_only`` is the sole scope: neither a binding nor a prepared genesis
record proves enrollment, source closure, owner retirement, or writable state.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from . import personal_authority_contract as authority
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_origin_reservation_contract as origin

FAMILY = "personal_authority_enrollment_binding"
FORMAT_VERSION = 1
SCOPE = "content_only"
MAX_RECORD_BYTES = 16 * 1024
MAX_DEPTH = origin.MAX_DEPTH
MAX_NODES = origin.MAX_NODES
MAX_INPUT_BYTES = (origin.MAX_RESERVATION_BYTES + origin.MAX_OBSERVATION_BYTES
                   + namespace.MAX_RESERVATION_BYTES + namespace.MAX_ROOT_SEAL_BYTES
                   + namespace.MAX_HEADER_BYTES)
_COMMON = frozenset({"format_version", "family", "kind", "scope", "init_operation_id",
                     "domain_id", "issuer"})
_EXTRA = {
    "enrollment_binding": {"profiles", "origin", "namespace"},
    "authority_prefix": {"binding_sha256", "authority"},
}
_PROFILES = frozenset({"authority_profile_paths_role", "authority_profile_paths_sha256",
                       "physical_profile_role", "physical_profile_sha256"})
_ORIGIN = frozenset({"family", "format_version", "reservation_sha256", "observation_sha256",
                     "intent_sha256", "evidence_sha256", "intent_observation"})
_NAMESPACE = frozenset({"family", "format_version", "root_name", "reservation_sha256",
                        "root_seal_sha256", "header_sha256", "root_native_identity",
                        "root_path_identity"})
_AUTHORITY = frozenset({"format_version", "origin_init_intent_sha256", "local_contract_sha256",
                        "init_seal_sha256", "genesis_grant_sha256"})
_JSON_ERRORS = {"ORIGIN_RESERVATION_" + suffix: suffix for suffix in (
    "JSON_INVALID", "JSON_DUPLICATE_KEY", "JSON_NONCANONICAL", "INTEGER_INVALID",
    "FIELDS_INVALID", "LIMIT",
)}


class EnrollmentContractError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_CONTRACT_" + suffix
        super().__init__(self.code)


ContractError = EnrollmentContractError
Error = EnrollmentContractError


@dataclass(frozen=True, slots=True)
class AuthorityPrefixContent:
    """Immutable bytes to publish only after separate runtime admission.

    This DTO contains no close receipt, witness, success flag, or permission.
    Validators recheck every byte field; frozen fields alone are not evidence.
    """

    prefix_bytes: bytes
    origin_intent_bytes: bytes
    local_contract_bytes: bytes
    init_seal_bytes: bytes
    genesis_grant_bytes: bytes


def _fail(suffix):
    raise EnrollmentContractError(suffix) from None


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "FIELDS_INVALID")


def _uuid(value):
    try:
        return authority._uuid(value)
    except authority.ContractError:
        _fail("UUID_INVALID")


def _sha(value):
    try:
        return origin._sha(value)
    except origin.ContractError:
        _fail("SHA_INVALID")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _version(value, expected):
    _need(type(value) is int and value == expected, "VERSION_INVALID")


def _endpoint(value):
    _fields(value, authority.ENDPOINT_FIELDS)
    _uuid(value["facility_id"])
    _uuid(value["node_id"])
    _sha(value["profile_sha256"])


def _normalize(value):
    try:
        origin._tree(value)
    except origin.ContractError as error:
        _fail(_JSON_ERRORS.get(error.code, "JSON_INVALID"))
    _need(type(value) is dict, "FIELDS_INVALID")
    kind = value.get("kind")
    _need(type(kind) is str and kind in _EXTRA, "KIND_INVALID")
    _fields(value, _COMMON | _EXTRA[kind])
    _version(value["format_version"], FORMAT_VERSION)
    _need(value["family"] == FAMILY and value["scope"] == SCOPE, "VERSION_INVALID")
    _uuid(value["init_operation_id"])
    _uuid(value["domain_id"])
    _endpoint(value["issuer"])
    if kind == "enrollment_binding":
        profiles, original, root = value["profiles"], value["origin"], value["namespace"]
        _fields(profiles, _PROFILES)
        _need(profiles["authority_profile_paths_role"] == namespace.AUTHORITY_ROLE
              and profiles["physical_profile_role"] == namespace.PHYSICAL_ROLE, "ROLE_INVALID")
        _sha(profiles["authority_profile_paths_sha256"])
        _sha(profiles["physical_profile_sha256"])
        _need(profiles["authority_profile_paths_sha256"] == value["issuer"]["profile_sha256"],
              "BINDING_MISMATCH")
        _fields(original, _ORIGIN)
        _version(original["format_version"], origin.FORMAT_VERSION)
        _need(original["family"] == origin.FAMILY, "VERSION_INVALID")
        for key in ("reservation_sha256", "observation_sha256", "intent_sha256", "evidence_sha256"):
            _sha(original[key])
        observation = original["intent_observation"]
        _fields(observation, authority.PAYLOAD_FIELDS["origin_init_intent"])
        _need(type(observation["current_epoch"]) is int
              and 1 <= observation["current_epoch"] <= authority.MAX_INTEGER, "INTEGER_INVALID")
        _uuid(observation["observed_primary_node_id"])
        _sha(observation["observed_personal_meta_sha256"])
        _sha(observation["observed_personal_sql_sha256"])
        _need(observation["observed_primary_node_id"] == value["issuer"]["node_id"],
              "BINDING_MISMATCH")
        _fields(root, _NAMESPACE)
        _version(root["format_version"], namespace.FORMAT_VERSION)
        _need(root["family"] == namespace.FAMILY and root["root_name"] == namespace.ROOT_NAME,
              "VERSION_INVALID")
        for key in ("reservation_sha256", "root_seal_sha256", "header_sha256"):
            _sha(root[key])
        try:
            namespace._native_identity(root["root_native_identity"])
            namespace._path_identity(root["root_path_identity"])
        except namespace.ContractError:
            _fail("IDENTITY_INVALID")
    else:
        _sha(value["binding_sha256"])
        chain = value["authority"]
        _fields(chain, _AUTHORITY)
        _version(chain["format_version"], authority.FORMAT_VERSION)
        for key in _AUTHORITY - {"format_version"}:
            _sha(chain[key])
    _need(len(_canonical(value)) <= MAX_RECORD_BYTES, "LIMIT")
    return deepcopy(value)


def encode(value: dict) -> bytes:
    """Encode one bounded, closed content declaration, without live effects."""
    try:
        return _canonical(_normalize(value))
    except EnrollmentContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def decode(raw: bytes) -> dict:
    try:
        value = origin._json(raw, MAX_RECORD_BYTES)
    except origin.ContractError as error:
        _fail(_JSON_ERRORS.get(error.code, "JSON_INVALID"))
    return _normalize(value)


def record_sha256(raw: bytes) -> str:
    decode(raw)
    return _hash(raw)


def _originals(first_raw, second_raw, reservation_raw, seal_raw, header_raw):
    selected = (first_raw, second_raw, reservation_raw, seal_raw, header_raw)
    maxima = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
              namespace.MAX_RESERVATION_BYTES, namespace.MAX_ROOT_SEAL_BYTES,
              namespace.MAX_HEADER_BYTES)
    for raw, maximum in zip(selected, maxima):
        _need(type(raw) is bytes and 0 < len(raw) <= maximum, "LIMIT")
    _need(sum(map(len, selected)) <= MAX_INPUT_BYTES, "LIMIT")
    try:
        origin.cross_validate(first_raw, second_raw)
        first, second = origin.decode(first_raw), origin.decode(second_raw)
        intent_raw = origin._blob(second["origin_intent_base64"], origin.MAX_INTENT_BYTES)
    except origin.ContractError:
        _fail("ORIGIN_BINDING_INVALID")
    try:
        namespace.cross_validate_complete(*selected)
        header = namespace.decode(header_raw)
    except namespace.ContractError:
        _fail("NAMESPACE_BINDING_INVALID")
    try:
        intent = authority.decode(intent_raw)
    except authority.ContractError:
        _fail("AUTHORITY_BINDING_INVALID")
    # Keep this explicit even though both dependency cross validators check
    # it: the issuer's hash always means the ARRAY role in this family.
    expected_issuer = {"facility_id": first["facility_id"], "node_id": first["node"]["node_id"],
                       "profile_sha256": first["authority_profile_paths_sha256"]}
    _need(intent["kind"] == "origin_init_intent" and intent["issuer"] == expected_issuer
          and intent["domain_id"] == first["domain_id"]
          and intent["operation_id"] == first["init_operation_id"]
          and intent["payload"]["observed_primary_node_id"] == expected_issuer["node_id"],
          "BINDING_MISMATCH")
    return first, second, header, intent_raw, intent


def _binding_value(first_raw, second_raw, reservation_raw, seal_raw, header_raw):
    first, second, header, _intent_raw, intent = _originals(
        first_raw, second_raw, reservation_raw, seal_raw, header_raw)
    return {"format_version": FORMAT_VERSION, "family": FAMILY, "kind": "enrollment_binding",
        "scope": SCOPE, "init_operation_id": first["init_operation_id"],
        "domain_id": first["domain_id"], "issuer": intent["issuer"],
        "profiles": {"authority_profile_paths_role": namespace.AUTHORITY_ROLE,
            "authority_profile_paths_sha256": first["authority_profile_paths_sha256"],
            "physical_profile_role": namespace.PHYSICAL_ROLE,
            "physical_profile_sha256": first["physical_profile_sha256"]},
        "origin": {"format_version": origin.FORMAT_VERSION, "family": origin.FAMILY,
            "reservation_sha256": _hash(first_raw), "observation_sha256": _hash(second_raw),
            "intent_sha256": second["origin_intent_sha256"],
            "evidence_sha256": second["origin_evidence_sha256"],
            "intent_observation": intent["payload"]},
        "namespace": {"format_version": namespace.FORMAT_VERSION, "family": namespace.FAMILY,
            "root_name": header["root_name"], "reservation_sha256": _hash(reservation_raw),
            "root_seal_sha256": _hash(seal_raw), "header_sha256": _hash(header_raw),
            "root_native_identity": header["root_native_identity"],
            "root_path_identity": header["root_path_identity"]}}


def build_binding(first_raw: bytes, second_raw: bytes, ns_reservation_raw: bytes,
                  ns_seal_raw: bytes, ns_header_raw: bytes) -> bytes:
    """Bind five original records, taking SQL observations only from second."""
    return encode(_binding_value(first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw))


def cross_validate(binding_raw: bytes, first_raw: bytes, second_raw: bytes,
                   ns_reservation_raw: bytes, ns_seal_raw: bytes, ns_header_raw: bytes) -> None:
    """Require the exact original content, without returning a live verdict."""
    binding = decode(binding_raw)
    _need(binding["kind"] == "enrollment_binding", "KIND_INVALID")
    expected = _binding_value(first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw)
    _need(binding == expected, "BINDING_MISMATCH")


def _authority_record(kind, intent, payload):
    try:
        return authority.encode({"format_version": authority.FORMAT_VERSION, "kind": kind,
            "domain_id": intent["domain_id"], "operation_id": intent["operation_id"],
            "issuer": intent["issuer"], "payload": payload})
    except authority.ContractError:
        _fail("AUTHORITY_BINDING_INVALID")


def _prefix_value(binding_raw, binding, records):
    return {"format_version": FORMAT_VERSION, "family": FAMILY, "kind": "authority_prefix",
        "scope": SCOPE, "init_operation_id": binding["init_operation_id"],
        "domain_id": binding["domain_id"], "issuer": binding["issuer"],
        "binding_sha256": _hash(binding_raw),
        "authority": {"format_version": authority.FORMAT_VERSION,
            **dict(zip(("origin_init_intent_sha256", "local_contract_sha256", "init_seal_sha256",
                        "genesis_grant_sha256"), map(_hash, records)))}}


def prepare_authority_prefix(binding_raw: bytes, first_raw: bytes, second_raw: bytes,
                             ns_reservation_raw: bytes, ns_seal_raw: bytes,
                             ns_header_raw: bytes) -> AuthorityPrefixContent:
    """Prepare format1 bytes while retaining original intent verbatim.

    The genesis *content* participates in an immutable declaration chain.
    Its presence does not admit a Store, close a source, or activate a writer.
    """
    cross_validate(binding_raw, first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw)
    binding = decode(binding_raw)
    _first, _second, _header, intent_raw, intent = _originals(
        first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw)
    intent_sha = _hash(intent_raw)
    local_raw = _authority_record("local_contract", intent,
        {"init_kind": "origin", "init_intent_sha256": intent_sha})
    local_sha = _hash(local_raw)
    seal_raw = _authority_record("init_seal", intent,
        {"init_intent_sha256": intent_sha, "local_contract_sha256": local_sha})
    genesis_raw = _authority_record("genesis_grant", intent,
        {"origin_init_intent_sha256": intent_sha, "local_contract_sha256": local_sha,
         "init_seal_sha256": _hash(seal_raw), "epoch": intent["payload"]["current_epoch"]})
    records = (intent_raw, local_raw, seal_raw, genesis_raw)
    try:
        authority.cross_validate(records)
    except authority.ContractError:
        _fail("AUTHORITY_BINDING_INVALID")
    prefix_raw = encode(_prefix_value(binding_raw, binding, records))
    return AuthorityPrefixContent(prefix_raw, *records)


def cross_validate_authority_prefix(content: AuthorityPrefixContent, binding_raw: bytes,
                                    first_raw: bytes, second_raw: bytes, ns_reservation_raw: bytes,
                                    ns_seal_raw: bytes, ns_header_raw: bytes) -> None:
    """Recheck every DTO byte against originals and the closed four-record chain."""
    _need(type(content) is AuthorityPrefixContent, "CONTENT_TYPE_INVALID")
    prefix = decode(content.prefix_bytes)
    _need(prefix["kind"] == "authority_prefix", "KIND_INVALID")
    cross_validate(binding_raw, first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw)
    binding = decode(binding_raw)
    _first, _second, _header, intent_raw, _intent = _originals(
        first_raw, second_raw, ns_reservation_raw, ns_seal_raw, ns_header_raw)
    records = (content.origin_intent_bytes, content.local_contract_bytes,
               content.init_seal_bytes, content.genesis_grant_bytes)
    maxima = (origin.MAX_INTENT_BYTES,) + (authority.HARD_LIMITS["max_record_bytes"],) * 3
    for raw, maximum in zip(records, maxima):
        _need(type(raw) is bytes and 0 < len(raw) <= maximum, "LIMIT")
    _need(content.origin_intent_bytes == intent_raw, "BINDING_MISMATCH")
    try:
        authority.cross_validate(records)
        _need([authority.decode(raw)["kind"] for raw in records]
              == ["origin_init_intent", "local_contract", "init_seal", "genesis_grant"], "KIND_INVALID")
    except authority.ContractError:
        _fail("AUTHORITY_BINDING_INVALID")
    _need(prefix == _prefix_value(binding_raw, binding, records), "BINDING_MISMATCH")
