"""Complete joint-observation declarations, never live SQL/Store authority.

The codec and private candidate checks prove only bounded declared content.
The public cross validator additionally requires schema publication, which is
currently unavailable after full content checking. No runtime owner, observer,
installer, resource receipt, I/O, SQL or publication setter is exposed.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_contract as enrollment
from . import personal_authority_enrollment_successor as prepare
from . import personal_authority_joint_sql_schema as schema
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_origin_reservation_contract as origin
from . import personal_preservation_contract as preservation


__all__ = ("JointSqlObservationError", "encode", "decode", "cross_validate")
FAMILY = "personal_authority_joint_sql_observation"
FORMAT_VERSION = 1
SCOPE = "joint_sql_declared_observation_content_only"
PRE_KIND = "precommit_joint_state"
POST_KIND = "postcommit_joint_state"
MAX_OBSERVATION_BYTES = 262144
MAX_NODES = 32768
MAX_DEPTH = 16
MAX_INPUT_BYTES = 901120
MAX_ORIGINAL_BYTES = 245760
MAX_INTEGER = 2**63 - 1
ORIGINAL_FIELDS = prepare.ORIGINAL_FIELDS
_ORIGINAL_MAXIMA = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
                    namespace.MAX_RESERVATION_BYTES, namespace.MAX_ROOT_SEAL_BYTES,
                    namespace.MAX_HEADER_BYTES) + (enrollment.MAX_RECORD_BYTES,) * 6
_TOP = ("format_version", "family", "kind", "scope", "transition", "context",
        "profiles", "namespace", "original_intent_sha256", "activation_prepare_sha256",
        "personal", "source", "authority", "catalog")
_CONTEXT = ("domain_id", "init_operation_id", "issuer")
_ISSUER = ("facility_id", "node_id", "profile_sha256")
_PROFILES = ("authority_profile_paths_role", "authority_profile_paths_sha256",
             "physical_profile_role", "physical_profile_sha256")
_NAMESPACE = ("family", "format_version", "root_name", "root_native_identity", "root_path_identity")
_PERSONAL = ("meta_document", "sql_document", "meta_values")
_META_DOCUMENT = ("format_version", "scope", "schema_version", "meta")
_SQL_DOCUMENT = _META_DOCUMENT + ("personal_tables",)
_SUMMARY = ("columns", "count", "sha256", "schema_sha256")
_META_KEYS = ("default_project_id", "library_id", "personal_primary_id", "primary_epoch")
_PERSONAL_COLUMNS = tuple((name, tuple(columns))
                          for name, columns in preservation.PERSONAL_COLUMNS.items())
_SOURCE = ("profile_paths", "source_fingerprint", "raw_sql_files", "node")
_FINGERPRINT = ("format_version", "profile_identity", "files")
_SUFFIXES = ("", "-wal", "-journal")
_FINGER_FILE = ("device", "inode", "byte_size", "mtime_ns", "ctime_ns", "sha256")
_RAW_FILE = ("sha256", "byte_size", "directory_identity", "path_identity", "handle_identity")
_WIRE_IDENTITY = ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")
_AUTHORITY = ("schema_family", "facility_version", "namespace_version", "journal_version",
              "mutation_codec_version", "binding_rows", "journal_rows", "logical_head")
_HEAD = ("kind", "binding_slot", "binding_revision", "epoch", "grant_sha256",
         "init_operation_id", "activation_prepare_sha256", "journal_sequence", "previous_commit_sha256")
_EPOCH = re.compile(r"[1-9][0-9]{0,18}\Z")
# Exact original personal_mirror_contract token grammar, used only to reproduce
# the old schema SHA descriptor after full candidate SQL/metadata matching.
_SQL_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]", re.S)
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class JointSqlObservationError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_SQL_SUCCESSOR_" + suffix
        super().__init__(self.code)


ContractError = JointSqlObservationError


def _fail(suffix):
    raise JointSqlObservationError(suffix) from None


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
            _need(len(item) <= MAX_OBSERVATION_BYTES, "LIMIT")
            text_bytes += len(item.encode("utf-8", errors="strict"))
            _need(text_bytes <= MAX_OBSERVATION_BYTES, "LIMIT")
            return item
        if type(item) is int:
            _integer(item, -2**63)
            return item
        _need(item is None, "JSON_INVALID")
        return None

    try:
        return visit(value, 0, ())
    except JointSqlObservationError:
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
    _raw(raw, MAX_OBSERVATION_BYTES)
    try:
        text = raw.decode("utf-8", errors="strict")
        _lexical_depth(text)
        parsed = json.loads(text, object_pairs_hook=_pairs, parse_int=_parse_integer,
                            parse_float=_reject_number, parse_constant=_reject_number)
        owned = _snapshot(parsed)
        _need(_canonical(owned) == raw, "JSON_NONCANONICAL")
        return owned
    except JointSqlObservationError:
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


def _summary(value, columns):
    _fields(value, _SUMMARY)
    _need(type(value["columns"]) is list and value["columns"] == list(columns),
          "OBSERVATION_BINDING_INVALID")
    _integer(value["count"], maximum=100000)
    _sha(value["sha256"])
    _sha(value["schema_sha256"])


def _personal(value, context):
    _fields(value, _PERSONAL)
    meta, sql = value["meta_document"], value["sql_document"]
    _fields(meta, _META_DOCUMENT)
    _fields(sql, _SQL_DOCUMENT)
    for document, scope in ((meta, "personal-authority-meta-v1"),
                            (sql, "personal-authority-ten-and-meta-v1")):
        _version(document["format_version"], 1)
        _version(document["schema_version"], 17)
        _need(document["scope"] == scope, "ROLE_INVALID")
        _summary(document["meta"], ("key", "value"))
        _need(document["meta"]["count"] == 4, "OBSERVATION_BINDING_INVALID")
    _need(meta["meta"] == sql["meta"], "OBSERVATION_BINDING_INVALID")
    _fields(sql["personal_tables"], tuple(name for name, _ in _PERSONAL_COLUMNS))
    for name, columns in _PERSONAL_COLUMNS:
        _summary(sql["personal_tables"][name], columns)
    _need(sum(row["count"] for row in sql["personal_tables"].values()) <= 100000, "LIMIT")
    values = value["meta_values"]
    _fields(values, _META_KEYS)
    for key in _META_KEYS[:-1]:
        _uuid(values[key])
    epoch = values["primary_epoch"]
    _need(type(epoch) is str and _EPOCH.fullmatch(epoch) is not None, "INTEGER_INVALID")
    _integer(int(epoch), 1)
    _need(values["personal_primary_id"] == context["issuer"]["node_id"],
          "OBSERVATION_BINDING_INVALID")


def _origin_error(error):
    suffix = error.code.removeprefix("ORIGIN_RESERVATION_")
    _fail(suffix if suffix in ("FIELDS_INVALID", "INTEGER_INVALID", "LIMIT")
          else "OBSERVATION_BINDING_INVALID")


def _origin_path(value):
    try:
        origin._path(value)
    except origin.ContractError as error:
        _origin_error(error)


def _origin_file(value):
    try:
        origin._file(value)
    except origin.ContractError as error:
        _origin_error(error)


def _source(value):
    _fields(value, _SOURCE)
    paths = value["profile_paths"]
    _need(type(paths) is list and len(paths) == 4, "FIELDS_INVALID")
    for path in paths:
        _origin_path(path)
    fingerprint = value["source_fingerprint"]
    _fields(fingerprint, _FINGERPRINT)
    _version(fingerprint["format_version"], 1)
    _need(fingerprint["profile_identity"] == paths, "OBSERVATION_BINDING_INVALID")
    _fields(fingerprint["files"], _SUFFIXES)
    _fields(value["raw_sql_files"], _SUFFIXES)
    node = value["node"]
    _origin_file(node)
    _need(node["byte_size"] <= origin.MAX_NODE_BYTES, "LIMIT")
    total = 0
    for suffix in _SUFFIXES:
        expected, observed = fingerprint["files"][suffix], value["raw_sql_files"][suffix]
        if expected is None or observed is None:
            _need(expected is None and observed is None and suffix != "",
                  "OBSERVATION_BINDING_INVALID")
            continue
        _fields(expected, _FINGER_FILE)
        _origin_file(observed)
        wire = {key: observed["path_identity"][key] for key in _WIRE_IDENTITY}
        wire["sha256"] = observed["sha256"]
        _need(expected == wire, "OBSERVATION_BINDING_INVALID")
        _need(observed["directory_identity"] == node["directory_identity"],
              "OBSERVATION_BINDING_INVALID")
        if suffix == "-journal":
            _need(observed["byte_size"] == 0 and observed["sha256"] == _EMPTY_SHA,
                  "OBSERVATION_BINDING_INVALID")
        total += observed["byte_size"]
    _need(total <= 2 * 1024**3, "LIMIT")


def _head_from_row(row):
    return {"kind": "derived_initial_head", "binding_slot": row[0], "binding_revision": row[9],
            "epoch": row[5], "grant_sha256": row[6], "init_operation_id": row[7],
            "activation_prepare_sha256": row[8], "journal_sequence": 0,
            "previous_commit_sha256": None}


def _authority(value, context, epoch, prepare_sha, state):
    _fields(value, _AUTHORITY)
    _need(value["schema_family"] == "personal_authority_joint_sql", "VERSION_INVALID")
    for field, version in (("facility_version", 2), ("namespace_version", 3),
                           ("journal_version", 2), ("mutation_codec_version", 3)):
        _version(value[field], version)
    rows = value["binding_rows"]
    _need(type(rows) is list and len(rows) in (0, 1), "INITIAL_BINDING_INVALID")
    _need(type(value["journal_rows"]) is list and not value["journal_rows"],
          "INITIAL_JOURNAL_INVALID")
    if not rows:
        _need(state in ("base_only", "joint_unbound"), "INITIAL_BINDING_INVALID")
        _need(value["logical_head"] is None, "INITIAL_HEAD_INVALID")
        return
    _need(state == "joint_bound", "INITIAL_BINDING_INVALID")
    row = rows[0]
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
          row[5] == epoch and row[7] == context["init_operation_id"] and row[8] == prepare_sha,
          "INITIAL_BINDING_INVALID")
    head = value["logical_head"]
    _fields(head, _HEAD)
    _need(head == _head_from_row(row), "INITIAL_HEAD_INVALID")
    # Explicit exact numeric validation prevents bool==int aliases in equality.
    for field, exact in (("binding_slot", 1), ("binding_revision", 1), ("journal_sequence", 0)):
        _integer(head[field], exact, exact)
    _integer(head["epoch"], 1)


def _schema_structure(catalog):
    try:
        schema._validate_catalog_structure(catalog)
    except schema.JointSqlSchemaError as error:
        suffix = error.code.removeprefix("JOINT_SQL_SCHEMA_")
        _fail(suffix if suffix in ("FIELDS_INVALID", "INTEGER_INVALID", "LIMIT") else "CATALOG_INVALID")


def _normalize(owned):
    _fields(owned, _TOP)
    _version(owned["format_version"], FORMAT_VERSION)
    _need(owned["family"] == FAMILY and owned["scope"] == SCOPE, "VERSION_INVALID")
    _need(type(owned["kind"]) is str and owned["kind"] in (PRE_KIND, POST_KIND), "ROLE_INVALID")
    _need(type(owned["transition"]) is str and owned["transition"] in
          ("initial_install", "same_binding_recovery"), "TRANSITION_INVALID")
    _context(owned["context"])
    _profiles(owned["profiles"], owned["context"])
    _namespace(owned["namespace"])
    _sha(owned["original_intent_sha256"])
    _sha(owned["activation_prepare_sha256"])
    _personal(owned["personal"], owned["context"])
    _source(owned["source"])
    _need(_hash(_canonical(owned["source"]["profile_paths"])) ==
          owned["profiles"]["authority_profile_paths_sha256"], "OBSERVATION_BINDING_INVALID")
    _schema_structure(owned["catalog"])
    state = owned["catalog"]["state"]
    allowed = (("joint_bound",) if owned["kind"] == POST_KIND or
               owned["transition"] == "same_binding_recovery" else ("base_only", "joint_unbound"))
    _need(state in allowed, "TRANSITION_INVALID")
    _authority(owned["authority"], owned["context"],
               int(owned["personal"]["meta_values"]["primary_epoch"]),
               owned["activation_prepare_sha256"], state)
    return owned


def encode(value):
    """Encode bounded local declaration content, not an observed producer fact."""
    raw = _canonical(_normalize(_snapshot(value)))
    _need(len(raw) <= MAX_OBSERVATION_BYTES, "LIMIT")
    return raw


def decode(raw):
    """Decode a fresh complete local declaration; originals remain unverified."""
    return _normalize(_json(raw))


def _preflight_inputs(originals, prepare_raw, reservation_raw, observations):
    _need(type(originals) is tuple and len(originals) == 11, "ORIGINAL_BINDING_INVALID")
    _need(type(observations) is tuple and len(observations) in (1, 2), "FIELDS_INVALID")
    selected = originals + (prepare_raw, reservation_raw) + observations
    # All type checks precede any length, parsing or selected dependency call.
    for raw in selected:
        _need(type(raw) is bytes, "RAW_TYPE_INVALID")
    maxima = _ORIGINAL_MAXIMA + (prepare.MAX_PREPARE_BYTES, prepare.MAX_RESERVATION_BYTES) + \
             (MAX_OBSERVATION_BYTES,) * len(observations)
    for raw, maximum in zip(selected, maxima):
        _need(0 < len(raw) <= maximum, "LIMIT")
    _need(sum(map(len, originals)) <= MAX_ORIGINAL_BYTES, "LIMIT")
    _need(sum(map(len, selected)) <= MAX_INPUT_BYTES, "LIMIT")


def _authenticated_materials(originals, prepare_raw, reservation_raw):
    try:
        prepare.cross_validate_reservation(reservation_raw, prepare_raw, originals=originals)
    except prepare.EnrollmentSqlSuccessorError as error:
        suffix = error.code.removeprefix("ENROLLMENT_SQL_SUCCESSOR_")
        _fail("ORIGINAL_BINDING_INVALID" if suffix == "ORIGINAL_BINDING_INVALID"
              else "PREPARE_BINDING_INVALID")
    try:
        decoders = (origin.decode, origin.decode, namespace.decode, namespace.decode,
                    namespace.decode, enrollment.decode, enrollment.decode,
                    authority.decode, authority.decode, authority.decode, authority.decode)
        records = tuple(decoder(raw) for decoder, raw in zip(decoders, originals))
        sealed_intent = origin._blob(records[1]["origin_intent_base64"], origin.MAX_INTENT_BYTES)
        _need(sealed_intent == originals[7], "ORIGINAL_BINDING_INVALID")
        evidence_raw = origin._blob(records[1]["origin_evidence_base64"], origin.MAX_EVIDENCE_BYTES)
        evidence = origin._evidence(evidence_raw)
        payload = prepare.decode(prepare_raw)
    except (origin.ContractError, namespace.ContractError, enrollment.ContractError,
            authority.ContractError, prepare.EnrollmentSqlSuccessorError):
        _fail("ORIGINAL_BINDING_INVALID")
    first, intent, genesis = records[0], records[7], records[10]
    row = [1, 3, intent["domain_id"], intent["issuer"]["facility_id"], intent["issuer"]["node_id"],
           genesis["payload"]["epoch"], _hash(originals[10]), intent["operation_id"],
           _hash(prepare_raw), 1]
    return {"first": first, "intent": intent, "genesis": genesis, "evidence": evidence,
            "prepare": payload, "intent_sha": _hash(originals[7]),
            "prepare_sha": _hash(prepare_raw), "desired_row": row}


def _legacy_schema_descriptor(catalog, table_name):
    """Reproduce the original descriptor hash projection, not acceptance rules.

    Only fixed meta/personal tables and a fully matched candidate reach here.
    The original catalog and all auxiliary parts/SQL are retained unchanged.
    """
    _need(table_name == "meta" or table_name in tuple(name for name, _ in _PERSONAL_COLUMNS),
          "ROLE_INVALID")
    contracts = [row for row in catalog["base_table_contracts"] if row["table_name"] == table_name]
    objects = [row for row in catalog["base_objects"]
               if row["type"] == "table" and row["name"] == table_name]
    _need(len(contracts) == len(objects) == 1, "CATALOG_INVALID")
    contract = contracts[0]
    columns = [{"name": row["name"], "type": row["declared_type"], "not_null": row["not_null"],
                "default": row["default_sql"], "pk": row["primary_key_position"], "hidden": row["hidden"]}
               for row in contract["columns"]]
    primary = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
    unique = []
    for index in contract["indexes"]:
        if index["unique"]:
            _need(index["partial"] == 0, "CATALOG_INVALID")
            unique.append([part["name"] for part in index["parts"] if part["key"] == 1])
    foreign = [[row[key] for key in ("target_table", "from_column", "to_column",
                                     "on_update", "on_delete", "match")]
               for row in contract["foreign_keys"]]
    tokens = [token if token.startswith(("'", '"')) else token.upper()
              for token in _SQL_TOKEN.findall(objects[0]["sql"])]
    return {"columns": columns, "primary_key": primary, "order_by": primary,
            "unique_keys": sorted(unique), "foreign_keys": sorted(foreign), "sql_tokens": tokens}


def _validate_content(value, materials):
    payload = materials["prepare"]
    for field in ("context", "profiles", "namespace"):
        _need(value[field] == payload[field], "OBSERVATION_BINDING_INVALID")
    _need(value["original_intent_sha256"] == materials["intent_sha"] and
          value["activation_prepare_sha256"] == materials["prepare_sha"], "OBSERVATION_BINDING_INVALID")
    try:
        schema._match_catalog_candidate(value["catalog"])
    except schema.JointSqlSchemaError:
        _fail("CATALOG_INVALID")
    personal = value["personal"]
    evidence, first, intent = materials["evidence"], materials["first"], materials["intent"]
    for field, observed_sha in (("meta_document", "observed_personal_meta_sha256"),
                               ("sql_document", "observed_personal_sql_sha256")):
        _need(personal[field] == evidence[field] and
              _hash(_canonical(personal[field])) == intent["payload"][observed_sha],
              "OBSERVATION_BINDING_INVALID")
    meta_values = personal["meta_values"]
    _need(meta_values["personal_primary_id"] == intent["payload"]["observed_primary_node_id"] and
          int(meta_values["primary_epoch"]) == intent["payload"]["current_epoch"] ==
          materials["genesis"]["payload"]["epoch"], "OBSERVATION_BINDING_INVALID")
    try:
        expected_meta = preservation.typed_table_digest(("key", "value"),
                         [(key, meta_values[key]) for key in _META_KEYS])
    except preservation.PersonalPreservationError:
        _fail("OBSERVATION_BINDING_INVALID")
    expected_meta["schema_sha256"] = _hash(_canonical(_legacy_schema_descriptor(value["catalog"], "meta")))
    _need(expected_meta == personal["meta_document"]["meta"], "OBSERVATION_BINDING_INVALID")
    for name, _columns in _PERSONAL_COLUMNS:
        expected_schema = _hash(_canonical(_legacy_schema_descriptor(value["catalog"], name)))
        _need(personal["sql_document"]["personal_tables"][name]["schema_sha256"] == expected_schema,
              "OBSERVATION_BINDING_INVALID")
    source = value["source"]
    _need(source["profile_paths"] == first["profile"]["profile_paths"], "OBSERVATION_BINDING_INVALID")
    _need(_hash(_canonical(first["profile"])) == value["profiles"]["physical_profile_sha256"],
          "OBSERVATION_BINDING_INVALID")
    _need(source["node"] == {key: first["node"][key] for key in _RAW_FILE} and
          source["node"]["directory_identity"] == first["profile"]["roots"]["data"]["identity"],
          "OBSERVATION_BINDING_INVALID")
    original_db = evidence["raw_sql_files"][""]["path_identity"]
    current_db = source["raw_sql_files"][""]["path_identity"]
    _need(all(current_db[key] == original_db[key] for key in ("device", "inode")),
          "OBSERVATION_BINDING_INVALID")
    if value["kind"] == PRE_KIND and value["transition"] == "initial_install":
        for field in ("source_fingerprint", "raw_sql_files", "node"):
            _need(source[field] == evidence[field], "OBSERVATION_BINDING_INVALID")
    rows = value["authority"]["binding_rows"]
    if rows:
        desired = materials["desired_row"]
        _need(all(type(actual) is type(expected) and actual == expected
                  for actual, expected in zip(rows[0], desired)), "INITIAL_BINDING_INVALID")
        _need(value["authority"]["logical_head"] == _head_from_row(desired), "INITIAL_HEAD_INVALID")


def _validate_pair(pre, post):
    _need(pre["kind"] == PRE_KIND and post["kind"] == POST_KIND, "TRANSITION_INVALID")
    for field in ("transition", "context", "profiles", "namespace", "original_intent_sha256",
                  "activation_prepare_sha256", "personal"):
        _need(pre[field] == post[field], "TRANSITION_INVALID")
    for field in ("base_objects", "base_table_contracts", "sqlite_user_version"):
        _need(pre["catalog"][field] == post["catalog"][field], "TRANSITION_INVALID")
    for field in ("profile_paths", "node"):
        _need(pre["source"][field] == post["source"][field], "TRANSITION_INVALID")
    pre_db = pre["source"]["raw_sql_files"][""]["path_identity"]
    post_db = post["source"]["raw_sql_files"][""]["path_identity"]
    _need(all(pre_db[key] == post_db[key] for key in ("device", "inode")), "TRANSITION_INVALID")
    if pre["transition"] == "same_binding_recovery":
        _need(pre["authority"] == post["authority"], "TRANSITION_INVALID")


def _checked_content(raw, *, originals, prepare_raw, reservation_raw):
    _preflight_inputs(originals, prepare_raw, reservation_raw, (raw,))
    materials = _authenticated_materials(originals, prepare_raw, reservation_raw)
    value = decode(raw)
    _validate_content(value, materials)
    return value


def _cross_validate_candidate_content(raw, *, originals, prepare_raw, reservation_raw):
    """Full selected-original and candidate content closure; return None."""
    _checked_content(raw, originals=originals, prepare_raw=prepare_raw, reservation_raw=reservation_raw)


def _cross_validate_candidate_pair(*, originals, prepare_raw, reservation_raw, pre_raw, post_raw):
    """Full pair declaration closure, not COMMIT/recovery/producer evidence."""
    _preflight_inputs(originals, prepare_raw, reservation_raw, (pre_raw, post_raw))
    materials = _authenticated_materials(originals, prepare_raw, reservation_raw)
    pre, post = decode(pre_raw), decode(post_raw)
    _validate_content(pre, materials)
    _validate_content(post, materials)
    _validate_pair(pre, post)


def cross_validate(raw, *, originals, prepare_raw, reservation_raw):
    """Complete content first, then enforce real schema publication policy."""
    value = _checked_content(raw, originals=originals, prepare_raw=prepare_raw, reservation_raw=reservation_raw)
    # This actual public schema gate rejects the static empty published table.
    # Do not catch it into a content pass or create a ready/runtime substitute.
    schema.validate_catalog_declaration(value["catalog"])
