"""Closed namespace3 filesystem declarations; bytes are never live authority.

Origin ARRAY and physical DICT roles remain distinct. Content validation does
not establish creation custody, publication, owner closure, enrollment, grant,
source_closed, or the caller's later EX exit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from . import personal_authority_origin_reservation_contract as origin

FAMILY = "personal_authority_namespace3"
FORMAT_VERSION = 1
SCOPE = "filesystem_namespace3_facts_only"
CREATION_KIND = "windows_ntcreate_directory_handle_v1"
PRIVATE_POLICY = "current_user_and_system_full_control_protected_v1"
AUTHORITY_ROLE = "canonical_four_paths_array_v1"
PHYSICAL_ROLE = "canonical_physical_profile_dict_v1"
RESERVATION_NAME = ".reno-personal-authority-namespace-v3-reservation.json"
ROOT_NAME = ".reno-personal-authority-namespace-v3"
ROOT_SEAL_NAME = ".reno-personal-authority-namespace-v3-root-seal.json"
HEADER_NAME = ".reno-personal-authority-namespace-v3-header.json"
MAX_RESERVATION_BYTES = 32 * 1024
MAX_ROOT_SEAL_BYTES = 16 * 1024
MAX_HEADER_BYTES = 16 * 1024
MAX_RECORD_BYTES = MAX_RESERVATION_BYTES
MAX_DEPTH = 16
MAX_NODES = 4096
_COMMON = frozenset({"format_version", "family", "kind", "scope",
                     "init_operation_id", "domain_id", "facility_id"})
_EXTRA = {
    "namespace_reservation": {"origin", "profile", "node", "layout", "creation_kind"},
    "namespace_root_seal": {"namespace_reservation_sha256", "root_name", "creator"},
    "namespace_header": {"namespace_reservation_sha256", "root_seal_sha256", "root_name",
                         "creation_kind", "root_native_identity", "root_path_identity"},
}
_ORIGIN = frozenset({"reservation_sha256", "observation_sha256", "intent_sha256",
    "evidence_sha256", "authority_profile_paths_sha256", "physical_profile_sha256",
    "authority_profile_paths_role", "physical_profile_role"})
_CREATOR = frozenset({"creation_kind", "private_policy", "parent_native_identity",
    "parent_path_identity", "root_native_identity", "root_path_identity"})
_NAMES = frozenset({RESERVATION_NAME, ROOT_SEAL_NAME, HEADER_NAME})
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
LAYOUT = {"reservation_name": RESERVATION_NAME, "root_name": ROOT_NAME,
          "root_seal_name": ROOT_SEAL_NAME, "header_name": HEADER_NAME}
_JSON_ERRORS = {"ORIGIN_RESERVATION_" + suffix: suffix for suffix in (
    "JSON_INVALID", "JSON_DUPLICATE_KEY", "JSON_NONCANONICAL", "INTEGER_INVALID",
    "FIELDS_INVALID", "LIMIT",
)}


class Namespace3ContractError(ValueError):
    def __init__(self, suffix):
        self.code = "NAMESPACE3_CONTRACT_" + suffix
        super().__init__(self.code)


ContractError = Namespace3ContractError
Error = Namespace3ContractError


def _fail(suffix):
    raise Namespace3ContractError(suffix) from None


def _json_error(error):
    # Preserve bounded parser diagnostics under this new family's prefix;
    # arbitrary foreign error text is never copied into a public code.
    _fail(_JSON_ERRORS.get(error.code, "JSON_INVALID"))


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, fields):
    _need(type(value) is dict and set(value) == set(fields), "FIELDS_INVALID")


def _uuid(value):
    try:
        return origin._uuid(value)
    except origin.ContractError:
        _fail("UUID_INVALID")


def _sha(value):
    try:
        return origin._sha(value)
    except origin.ContractError:
        _fail("SHA_INVALID")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _path_identity(value):
    try:
        origin._identity(value)
    except origin.ContractError:
        _fail("IDENTITY_INVALID")


def _native_identity(value):
    _fields(value, {"volume_serial", "file_index"})
    for key, maximum in (("volume_serial", 2**32 - 1), ("file_index", 2**64 - 1)):
        text = value[key]
        _need(type(text) is str and _DECIMAL.fullmatch(text) is not None
              and int(text) <= maximum, "IDENTITY_INVALID")


def _normalize(value):
    try:
        origin._tree(value)
    except origin.ContractError as error:
        _json_error(error)
    _need(type(value) is dict, "FIELDS_INVALID")
    kind = value.get("kind")
    _need(type(kind) is str and kind in _EXTRA, "KIND_INVALID")
    _fields(value, _COMMON | _EXTRA[kind])
    _need(type(value["format_version"]) is int and value["format_version"] == FORMAT_VERSION
          and value["family"] == FAMILY and value["scope"] == SCOPE, "VERSION_INVALID")
    for key in ("init_operation_id", "domain_id", "facility_id"):
        _uuid(value[key])
    if kind == "namespace_reservation":
        binding = value["origin"]
        _fields(binding, _ORIGIN)
        for key in _ORIGIN - {"authority_profile_paths_role", "physical_profile_role"}:
            _sha(binding[key])
        _need(binding["authority_profile_paths_role"] == AUTHORITY_ROLE
              and binding["physical_profile_role"] == PHYSICAL_ROLE, "ROLE_INVALID")
        # Validate exactly the retained original profile/node semantics, without
        # changing either original digest's meaning or inventing physical facts.
        original = {"format_version": origin.FORMAT_VERSION, "family": origin.FAMILY,
            "kind": "origin_reservation", "init_operation_id": value["init_operation_id"],
            "domain_id": value["domain_id"], "facility_id": value["facility_id"],
            "profile": value["profile"], "node": value["node"],
            "authority_profile_paths_sha256": binding["authority_profile_paths_sha256"],
            "physical_profile_sha256": binding["physical_profile_sha256"]}
        try:
            origin.encode(original)
        except origin.ContractError:
            _fail("ORIGIN_BINDING_INVALID")
        _fields(value["layout"], LAYOUT)
        _need(value["layout"] == LAYOUT and value["creation_kind"] == CREATION_KIND,
              "LAYOUT_INVALID")
    else:
        _sha(value["namespace_reservation_sha256"])
        _need(value["root_name"] == ROOT_NAME, "LAYOUT_INVALID")
        if kind == "namespace_root_seal":
            creator = value["creator"]
            _fields(creator, _CREATOR)
            _need(creator["creation_kind"] == CREATION_KIND
                  and creator["private_policy"] == PRIVATE_POLICY, "CREATION_INVALID")
            for key in ("parent_native_identity", "root_native_identity"):
                _native_identity(creator[key])
            for key in ("parent_path_identity", "root_path_identity"):
                _path_identity(creator[key])
            _need(creator["parent_native_identity"]["volume_serial"]
                  == creator["root_native_identity"]["volume_serial"], "IDENTITY_INVALID")
        else:
            _sha(value["root_seal_sha256"])
            _need(value["creation_kind"] == CREATION_KIND, "CREATION_INVALID")
            _native_identity(value["root_native_identity"])
            _path_identity(value["root_path_identity"])
    maximum = {"namespace_reservation": MAX_RESERVATION_BYTES,
               "namespace_root_seal": MAX_ROOT_SEAL_BYTES,
               "namespace_header": MAX_HEADER_BYTES}[kind]
    _need(len(_canonical(value)) <= maximum, "LIMIT")
    return deepcopy(value)


def encode(value) -> bytes:
    try:
        return _canonical(_normalize(value))
    except Namespace3ContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def decode(raw: bytes) -> dict:
    try:
        value = origin._json(raw, MAX_RECORD_BYTES)
    except origin.ContractError as error:
        _json_error(error)
    return _normalize(value)


def record_sha256(raw: bytes) -> str:
    decode(raw)
    return hashlib.sha256(raw).hexdigest()


def stage_name(final_name: str, init_operation_id: str) -> str:
    _need(type(final_name) is str and final_name in _NAMES, "KIND_INVALID")
    return final_name + "." + _uuid(init_operation_id) + ".stage"


def cross_validate_reservation(first_raw: bytes, second_raw: bytes, reservation_raw: bytes) -> None:
    try:
        origin.cross_validate(first_raw, second_raw)
        first, second = origin.decode(first_raw), origin.decode(second_raw)
    except origin.ContractError:
        _fail("ORIGIN_BINDING_INVALID")
    reservation = decode(reservation_raw)
    _need(reservation["kind"] == "namespace_reservation", "KIND_INVALID")
    for key in ("init_operation_id", "domain_id", "facility_id", "profile", "node"):
        _need(reservation[key] == first[key], "BINDING_MISMATCH")
    expected = {"reservation_sha256": hashlib.sha256(first_raw).hexdigest(),
        "observation_sha256": hashlib.sha256(second_raw).hexdigest(),
        "intent_sha256": second["origin_intent_sha256"],
        "evidence_sha256": second["origin_evidence_sha256"],
        "authority_profile_paths_sha256": first["authority_profile_paths_sha256"],
        "physical_profile_sha256": first["physical_profile_sha256"],
        "authority_profile_paths_role": AUTHORITY_ROLE, "physical_profile_role": PHYSICAL_ROLE}
    _need(reservation["origin"] == expected, "BINDING_MISMATCH")


def cross_validate_complete(first_raw: bytes, second_raw: bytes, reservation_raw: bytes,
                            seal_raw: bytes, header_raw: bytes) -> None:
    cross_validate_reservation(first_raw, second_raw, reservation_raw)
    reservation, seal, header = decode(reservation_raw), decode(seal_raw), decode(header_raw)
    _need(seal["kind"] == "namespace_root_seal" and header["kind"] == "namespace_header",
          "KIND_INVALID")
    for document in (seal, header):
        _need(all(document[key] == reservation[key]
                  for key in ("init_operation_id", "domain_id", "facility_id", "scope")),
              "BINDING_MISMATCH")
        _need(document["namespace_reservation_sha256"] == hashlib.sha256(reservation_raw).hexdigest(),
              "BINDING_MISMATCH")
    _need(header["root_seal_sha256"] == hashlib.sha256(seal_raw).hexdigest()
          and seal["creator"]["parent_path_identity"] == reservation["profile"]["roots"]["data"]["identity"]
          and header["root_native_identity"] == seal["creator"]["root_native_identity"]
          and header["root_path_identity"] == seal["creator"]["root_path_identity"], "BINDING_MISMATCH")
