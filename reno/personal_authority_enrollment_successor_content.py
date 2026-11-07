"""Full SQL-successor content without COMMIT, Store or live authority.

The original prepare module retains its six APIs and separate family. This
module depends downward on prepare/observation/schema, never the reverse.
Private candidate APIs authenticate selected raw content; public APIs enforce
schema publication only after complete content closure. Publication is still
unavailable, and no runtime owner/installer/observer or ready setter is exposed.
"""
from __future__ import annotations

import hashlib
import json

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_contract as enrollment
from . import personal_authority_enrollment_successor as prepare
from . import personal_authority_joint_sql_observation as observation
from . import personal_authority_joint_sql_schema as schema
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_origin_reservation_contract as origin


__all__ = ("EnrollmentSqlSuccessorContentError", "encode", "decode", "build", "cross_validate")
FAMILY = "personal_authority_enrollment_sql_successor"
FORMAT_VERSION = 1
KIND = "origin_sql_successor"
SCOPE = "committed_sql_content_only"
MAX_SUCCESSOR_BYTES = 32768
MAX_NODES = 4096
MAX_DEPTH = 16
MAX_INPUT_BYTES = 901120
MAX_ORIGINAL_BYTES = 245760
MAX_INTEGER = 2**63 - 1
ORIGINAL_FIELDS = prepare.ORIGINAL_FIELDS
_ORIGINAL_MAXIMA = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
                    namespace.MAX_RESERVATION_BYTES, namespace.MAX_ROOT_SEAL_BYTES,
                    namespace.MAX_HEADER_BYTES) + (enrollment.MAX_RECORD_BYTES,) * 6
_REFERENCE_SPECS = (
    ("first", "original_origin_reservation", "personal_authority_origin_reservation", "origin_reservation", 1),
    ("second", "original_origin_observation", "personal_authority_origin_reservation", "origin_observation", 1),
    ("namespace_reservation", "original_namespace3_reservation", "personal_authority_namespace3", "namespace_reservation", 1),
    ("root_seal", "original_namespace3_root_seal", "personal_authority_namespace3", "namespace_root_seal", 1),
    ("header", "original_namespace3_header", "personal_authority_namespace3", "namespace_header", 1),
    ("binding", "original_enrollment_binding", "personal_authority_enrollment_binding", "enrollment_binding", 1),
    ("prefix", "original_authority_prefix", "personal_authority_enrollment_binding", "authority_prefix", 1),
    ("origin_intent", "original_sealed_origin_intent", "authority_format1_no_family_envelope", "origin_init_intent", 1),
    ("local_contract", "original_local_contract", "authority_format1_no_family_envelope", "local_contract", 1),
    ("init_seal", "original_init_seal", "authority_format1_no_family_envelope", "init_seal", 1),
    ("genesis_grant", "original_genesis_grant", "authority_format1_no_family_envelope", "genesis_grant", 1),
    ("activation_prepare_payload", "original_sql_registration_prepare_payload", "personal_authority_enrollment_sql_prepare", "sql_registration_prepare", 1),
    ("activation_prepare_reservation", "original_sql_registration_prepare_reservation", "personal_authority_enrollment_sql_prepare", "sql_registration_prepare_reservation", 1),
    ("precommit_observation", "declared_precommit_joint_observation", "personal_authority_joint_sql_observation", "precommit_joint_state", 1),
    ("postcommit_observation", "declared_postcommit_joint_observation", "personal_authority_joint_sql_observation", "postcommit_joint_state", 1),
)
_REFERENCE_MAXIMA = _ORIGINAL_MAXIMA + (prepare.MAX_PREPARE_BYTES, prepare.MAX_RESERVATION_BYTES,
                                     observation.MAX_OBSERVATION_BYTES, observation.MAX_OBSERVATION_BYTES)
_TOP = ("format_version", "family", "kind", "scope", "transition", "context", "profiles",
        "namespace", "refs", "initial_state", "unchanged_personal")
_CONTEXT = ("domain_id", "init_operation_id", "issuer")
_ISSUER = ("facility_id", "node_id", "profile_sha256")
_PROFILES = ("authority_profile_paths_role", "authority_profile_paths_sha256",
             "physical_profile_role", "physical_profile_sha256")
_NAMESPACE = ("family", "format_version", "root_name", "root_native_identity", "root_path_identity")
_REFERENCE = ("role", "codec", "record_kind", "format_version", "raw_byte_size", "raw_sha256", "identity")
_INITIAL = ("binding", "journal_rows", "logical_head")
_HEAD = ("kind", "binding_slot", "binding_revision", "epoch", "grant_sha256", "init_operation_id",
         "activation_prepare_sha256", "journal_sequence", "previous_commit_sha256")
_PERSONAL = ("personal_meta_sha256", "personal_sql_sha256")
_ERROR_SUFFIXES = ("RAW_TYPE_INVALID", "LIMIT", "JSON_INVALID", "JSON_DUPLICATE_KEY",
                  "JSON_NONCANONICAL", "FIELDS_INVALID", "INTEGER_INVALID", "VERSION_INVALID",
                  "ROLE_INVALID", "UUID_INVALID", "SHA_INVALID", "ORIGINAL_BINDING_INVALID",
                  "PREPARE_BINDING_INVALID", "OBSERVATION_BINDING_INVALID", "CATALOG_INVALID",
                  "INITIAL_BINDING_INVALID", "INITIAL_JOURNAL_INVALID", "INITIAL_HEAD_INVALID",
                  "TRANSITION_INVALID")


class EnrollmentSqlSuccessorContentError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_SQL_SUCCESSOR_" + suffix
        super().__init__(self.code)


ContractError = EnrollmentSqlSuccessorContentError


def _fail(suffix):
    raise EnrollmentSqlSuccessorContentError(suffix) from None


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "FIELDS_INVALID")


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    _need(type(value) is int and minimum <= value <= maximum, "INTEGER_INVALID")


def _version(value, expected):
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


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _snapshot(value):
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
            _need(len(item) <= MAX_SUCCESSOR_BYTES, "LIMIT")
            text_bytes += len(item.encode("utf-8", errors="strict"))
            _need(text_bytes <= MAX_SUCCESSOR_BYTES, "LIMIT")
            return item
        if type(item) is int:
            _integer(item, -2**63)
            return item
        _need(item is None, "JSON_INVALID")
        return None

    try:
        return visit(value, 0, ())
    except EnrollmentSqlSuccessorContentError:
        raise
    except (RuntimeError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _raw(raw, maximum):
    _need(type(raw) is bytes, "RAW_TYPE_INVALID")
    _need(0 < len(raw) <= maximum, "LIMIT")


def _lexical_depth(text):
    quoted = escaped = False
    depth = 0
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            _need(depth <= MAX_DEPTH, "LIMIT")
        elif character in "]}":
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
    _integer(value, -2**63)
    return value


def _reject_number(_text):
    _fail("JSON_INVALID")


def _json(raw):
    _raw(raw, MAX_SUCCESSOR_BYTES)
    try:
        text = raw.decode("utf-8", errors="strict")
        _lexical_depth(text)
        parsed = json.loads(text, object_pairs_hook=_pairs, parse_int=_parse_integer,
                            parse_float=_reject_number, parse_constant=_reject_number)
        owned = _snapshot(parsed)
        _need(_canonical(owned) == raw, "JSON_NONCANONICAL")
        return owned
    except EnrollmentSqlSuccessorContentError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _context(value):
    _fields(value, _CONTEXT)
    _uuid(value["domain_id"])
    _uuid(value["init_operation_id"])
    _fields(value["issuer"], _ISSUER)
    _uuid(value["issuer"]["facility_id"])
    _uuid(value["issuer"]["node_id"])
    _sha(value["issuer"]["profile_sha256"])


def _profiles(value, context):
    _fields(value, _PROFILES)
    _need(value["authority_profile_paths_role"] == namespace.AUTHORITY_ROLE and
          value["physical_profile_role"] == namespace.PHYSICAL_ROLE, "ROLE_INVALID")
    _sha(value["authority_profile_paths_sha256"])
    _sha(value["physical_profile_sha256"])
    _need(value["authority_profile_paths_sha256"] == context["issuer"]["profile_sha256"],
          "OBSERVATION_BINDING_INVALID")


def _namespace(value):
    _fields(value, _NAMESPACE)
    _version(value["format_version"], namespace.FORMAT_VERSION)
    _need(value["family"] == namespace.FAMILY and value["root_name"] == namespace.ROOT_NAME,
          "VERSION_INVALID")
    try:
        namespace._native_identity(value["root_native_identity"])
        namespace._path_identity(value["root_path_identity"])
    except namespace.ContractError:
        _fail("OBSERVATION_BINDING_INVALID")


def _ref_error(index):
    if index < 11:
        return "ORIGINAL_BINDING_INVALID"
    return "PREPARE_BINDING_INVALID" if index < 13 else "OBSERVATION_BINDING_INVALID"


def _references(value, context):
    _fields(value, tuple(spec[0] for spec in _REFERENCE_SPECS))
    for index, (spec, maximum) in enumerate(zip(_REFERENCE_SPECS, _REFERENCE_MAXIMA)):
        key, role, codec, kind, version = spec
        row = value[key]
        _fields(row, _REFERENCE)
        _need(row["role"] == role and row["codec"] == codec and row["record_kind"] == kind,
              "ROLE_INVALID")
        _version(row["format_version"], version)
        _integer(row["raw_byte_size"], 1, maximum)
        _sha(row["raw_sha256"])
        _context(row["identity"])
        _need(row["identity"] == context, _ref_error(index))


def _head_from_row(row):
    return {"kind": "derived_initial_head", "binding_slot": row[0], "binding_revision": row[9],
            "epoch": row[5], "grant_sha256": row[6], "init_operation_id": row[7],
            "activation_prepare_sha256": row[8], "journal_sequence": 0,
            "previous_commit_sha256": None}


def _initial_state(value, context, refs):
    _fields(value, _INITIAL)
    row = value["binding"]
    _need(type(row) is list and len(row) == 10, "INITIAL_BINDING_INVALID")
    for index, exact in ((0, 1), (1, 3), (9, 1)):
        _integer(row[index], exact, exact)
    for index in (2, 3, 4, 7):
        _uuid(row[index])
    _integer(row[5], 1)
    _sha(row[6])
    _sha(row[8])
    _need(row[2:5] == [context["domain_id"], context["issuer"]["facility_id"],
                       context["issuer"]["node_id"]] and
          row[7] == context["init_operation_id"] and
          row[6] == refs["genesis_grant"]["raw_sha256"] and
          row[8] == refs["activation_prepare_payload"]["raw_sha256"], "INITIAL_BINDING_INVALID")
    _integer(value["journal_rows"])
    _need(value["journal_rows"] == 0, "INITIAL_JOURNAL_INVALID")
    _fields(value["logical_head"], _HEAD)
    _need(value["logical_head"] == _head_from_row(row), "INITIAL_HEAD_INVALID")


def _normalize(owned):
    _fields(owned, _TOP)
    _version(owned["format_version"], FORMAT_VERSION)
    _need(owned["family"] == FAMILY and owned["kind"] == KIND and owned["scope"] == SCOPE,
          "VERSION_INVALID")
    _need(type(owned["transition"]) is str and owned["transition"] in
          ("initial_install", "same_binding_recovery"), "TRANSITION_INVALID")
    _context(owned["context"])
    _profiles(owned["profiles"], owned["context"])
    _namespace(owned["namespace"])
    _references(owned["refs"], owned["context"])
    _initial_state(owned["initial_state"], owned["context"], owned["refs"])
    _fields(owned["unchanged_personal"], _PERSONAL)
    for field in _PERSONAL:
        _sha(owned["unchanged_personal"][field])
    return owned


def encode(value):
    """Encode local declaration content; selected raw closure remains separate."""
    raw = _canonical(_normalize(_snapshot(value)))
    _need(len(raw) <= MAX_SUCCESSOR_BYTES, "LIMIT")
    return raw


def decode(successor_raw):
    """Decode a fresh full successor declaration, without selected-original proof."""
    return _normalize(_json(successor_raw))


def _preflight_inputs(originals, prepare_payload_raw, prepare_reservation_raw,
                      precommit_raw, postcommit_raw, successors):
    _need(type(originals) is tuple and len(originals) == 11, "ORIGINAL_BINDING_INVALID")
    _need(type(successors) is tuple and len(successors) in (0, 1), "FIELDS_INVALID")
    selected = originals + (prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw) + successors
    # Type-check the entire outer selection before any length or dependency call.
    for raw in selected:
        _need(type(raw) is bytes, "RAW_TYPE_INVALID")
    maxima = _REFERENCE_MAXIMA + (MAX_SUCCESSOR_BYTES,) * len(successors)
    for raw, maximum in zip(selected, maxima):
        _need(0 < len(raw) <= maximum, "LIMIT")
    _need(sum(map(len, originals)) <= MAX_ORIGINAL_BYTES, "LIMIT")
    _need(sum(map(len, selected)) <= MAX_INPUT_BYTES, "LIMIT")


def _validated_pair(originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    try:
        observation._cross_validate_candidate_pair(originals=originals,
            prepare_raw=prepare_payload_raw, reservation_raw=prepare_reservation_raw,
            pre_raw=precommit_raw, post_raw=postcommit_raw)
        payload = prepare.decode(prepare_payload_raw)
        pre, post = observation.decode(precommit_raw), observation.decode(postcommit_raw)
    except observation.JointSqlObservationError as error:
        suffix = error.code.removeprefix("ENROLLMENT_SQL_SUCCESSOR_")
        _fail(suffix if suffix in _ERROR_SUFFIXES else "OBSERVATION_BINDING_INVALID")
    except prepare.EnrollmentSqlSuccessorError:
        _fail("PREPARE_BINDING_INVALID")
    return payload, pre, post


def _expected_successor(originals, prepare_payload_raw, prepare_reservation_raw,
                        precommit_raw, postcommit_raw, payload, pre, post):
    # Fixed pair authentication already proved these exact selected records.
    # Re-decode only the pure originals needed for independent expected values.
    try:
        intent, genesis = authority.decode(originals[7]), authority.decode(originals[10])
    except authority.ContractError:
        _fail("ORIGINAL_BINDING_INVALID")
    context = payload["context"]
    selected = originals + (prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw)
    refs = {}
    for raw, spec in zip(selected, _REFERENCE_SPECS):
        key, role, codec, kind, version = spec
        refs[key] = {"role": role, "codec": codec, "record_kind": kind,
                     "format_version": version, "raw_byte_size": len(raw),
                     "raw_sha256": _hash(raw), "identity": context}
    row = [1, 3, intent["domain_id"], intent["issuer"]["facility_id"], intent["issuer"]["node_id"],
           genesis["payload"]["epoch"], _hash(originals[10]), intent["operation_id"],
           _hash(prepare_payload_raw), 1]
    head = _head_from_row(row)
    _need(post["authority"]["binding_rows"] == [row], "INITIAL_BINDING_INVALID")
    _need(not post["authority"]["journal_rows"], "INITIAL_JOURNAL_INVALID")
    _need(post["authority"]["logical_head"] == head, "INITIAL_HEAD_INVALID")
    unchanged = {}
    for target, document, field in (("personal_meta_sha256", "meta_document", "observed_personal_meta_sha256"),
                                    ("personal_sql_sha256", "sql_document", "observed_personal_sql_sha256")):
        expected_sha = intent["payload"][field]
        _need(_hash(_canonical(pre["personal"][document])) == expected_sha and
              _hash(_canonical(post["personal"][document])) == expected_sha,
              "OBSERVATION_BINDING_INVALID")
        unchanged[target] = expected_sha
    return {"format_version": FORMAT_VERSION, "family": FAMILY, "kind": KIND, "scope": SCOPE,
            "transition": pre["transition"], "context": context, "profiles": payload["profiles"],
            "namespace": payload["namespace"], "refs": refs,
            "initial_state": {"binding": row, "journal_rows": 0, "logical_head": head},
            "unchanged_personal": unchanged}


def _check_expected(value, expected):
    _need(value["transition"] == expected["transition"], "TRANSITION_INVALID")
    for field in ("context", "profiles", "namespace"):
        _need(value[field] == expected[field], "OBSERVATION_BINDING_INVALID")
    for index, spec in enumerate(_REFERENCE_SPECS):
        key = spec[0]
        _need(value["refs"][key] == expected["refs"][key], _ref_error(index))
    _need(value["initial_state"]["binding"] == expected["initial_state"]["binding"], "INITIAL_BINDING_INVALID")
    _need(value["initial_state"]["journal_rows"] == expected["initial_state"]["journal_rows"], "INITIAL_JOURNAL_INVALID")
    _need(value["initial_state"]["logical_head"] == expected["initial_state"]["logical_head"], "INITIAL_HEAD_INVALID")
    _need(value["unchanged_personal"] == expected["unchanged_personal"], "OBSERVATION_BINDING_INVALID")
    # Covers the complete closed object, including fixed fields validated locally.
    _need(value == expected, "OBSERVATION_BINDING_INVALID")


def _build_checked(*, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    _preflight_inputs(originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw, ())
    payload, pre, post = _validated_pair(originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw)
    expected = _expected_successor(originals, prepare_payload_raw, prepare_reservation_raw,
                                   precommit_raw, postcommit_raw, payload, pre, post)
    successor_raw = encode(expected)
    # The actual generated output joins the same aggregate before any return.
    _preflight_inputs(originals, prepare_payload_raw, prepare_reservation_raw,
                      precommit_raw, postcommit_raw, (successor_raw,))
    return successor_raw, pre, post


def _cross_checked(successor_raw, *, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    _preflight_inputs(originals, prepare_payload_raw, prepare_reservation_raw,
                      precommit_raw, postcommit_raw, (successor_raw,))
    payload, pre, post = _validated_pair(originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw)
    expected = _expected_successor(originals, prepare_payload_raw, prepare_reservation_raw,
                                   precommit_raw, postcommit_raw, payload, pre, post)
    value = decode(successor_raw)
    _check_expected(value, expected)
    return pre, post


def _published_pair_gate(pre, post):
    # Two actual schema policy checks after complete selected content closure.
    # Current empty publication is enforced by the existing public validator.
    schema.validate_catalog_declaration(pre["catalog"])
    schema.validate_catalog_declaration(post["catalog"])


def _build_candidate(*, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    """Build only a complete candidate declaration; no runtime/publication claim."""
    raw, _pre, _post = _build_checked(originals=originals, prepare_payload_raw=prepare_payload_raw,
        prepare_reservation_raw=prepare_reservation_raw, precommit_raw=precommit_raw, postcommit_raw=postcommit_raw)
    return raw


def _cross_validate_candidate(successor_raw, *, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    """Authenticate all raw content and refs; return None, never a receipt."""
    _cross_checked(successor_raw, originals=originals, prepare_payload_raw=prepare_payload_raw,
        prepare_reservation_raw=prepare_reservation_raw, precommit_raw=precommit_raw, postcommit_raw=postcommit_raw)


def build(*, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    """Full content/output budget followed by public schema publication policy."""
    raw, pre, post = _build_checked(originals=originals, prepare_payload_raw=prepare_payload_raw,
        prepare_reservation_raw=prepare_reservation_raw, precommit_raw=precommit_raw, postcommit_raw=postcommit_raw)
    _published_pair_gate(pre, post)
    return raw


def cross_validate(successor_raw, *, originals, prepare_payload_raw, prepare_reservation_raw, precommit_raw, postcommit_raw):
    """Full selected successor closure followed by both public schema gates."""
    pre, post = _cross_checked(successor_raw, originals=originals, prepare_payload_raw=prepare_payload_raw,
        prepare_reservation_raw=prepare_reservation_raw, precommit_raw=precommit_raw, postcommit_raw=postcommit_raw)
    _published_pair_gate(pre, post)
