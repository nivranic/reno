"""Two immutable origin-reservation declarations; no I/O or writer authority.

The first record binds physical roots/node before any origin SQL observation.
The second retains the exact producer bytes and references that first record.
Namespace2's physical-profile DICT digest and the authority four-path ARRAY
digest have separate names and meanings. Valid bytes never prove ownership,
publication, normal resource closure, enrollment, grant, or a live witness.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import hashlib
import json
import ntpath
import posixpath
import re
import stat

from . import personal_authority_contract as authority
from . import personal_preservation_contract as preservation

FAMILY = "personal_authority_origin_reservation"
FORMAT_VERSION = 1
RESERVATION_NAME = ".reno-personal-authority-origin-v1-reservation.json"
OBSERVATION_NAME = ".reno-personal-authority-origin-v1-observation.json"
MAX_RESERVATION_BYTES = 16 * 1024
MAX_OBSERVATION_BYTES = 64 * 1024
MAX_INTENT_BYTES = 16 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024
MAX_DEPTH = 16
MAX_NODES = 4096
MAX_INTEGER = 2**63 - 1
MAX_NODE_BYTES = 4096
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,38})\Z")
_ROOTS = ("data", "media", "reports", "checkpoints")
_IDENTITY = frozenset({"device", "inode"})
_FILE_IDENTITY = _IDENTITY | {"byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
_WIRE_IDENTITY = _FILE_IDENTITY - {"mode", "nlink"}
_FILE = frozenset({"sha256", "byte_size", "directory_identity", "path_identity", "handle_identity"})
_RESERVATION = frozenset({"format_version", "family", "kind", "init_operation_id",
    "domain_id", "facility_id", "profile", "authority_profile_paths_sha256",
    "physical_profile_sha256", "node"})
_OBSERVATION = frozenset({"format_version", "family", "kind", "init_operation_id",
    "reservation_sha256", "origin_intent_sha256", "origin_intent_base64",
    "origin_evidence_sha256", "origin_evidence_base64"})
_EVIDENCE = frozenset({"format_version", "scope", "profile_paths", "profile_sha256",
    "source_fingerprint", "node", "raw_sql_files", "meta_document", "sql_document", "intent_sha256"})


class OriginReservationContractError(ValueError):
    def __init__(self, suffix):
        self.code = "ORIGIN_RESERVATION_" + suffix
        super().__init__(self.code)


ContractError = OriginReservationContractError
Error = OriginReservationContractError


def _fail(suffix):
    raise OriginReservationContractError(suffix) from None


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _fields(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "FIELDS_INVALID")


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    _need(type(value) is int and minimum <= value <= maximum, "INTEGER_INVALID")
    return value


def _uuid(value):
    try:
        return authority._uuid(value)
    except authority.ContractError:
        _fail("UUID_INVALID")


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None, "SHA_INVALID")
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _tree(value):
    count = size = 0
    def visit(item, depth):
        nonlocal count, size
        count += 1
        _need(count <= MAX_NODES and depth <= MAX_DEPTH, "LIMIT")
        if type(item) is dict:
            for key, child in item.items():
                _need(type(key) is str, "FIELDS_INVALID")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            _need(len(item) <= MAX_OBSERVATION_BYTES, "LIMIT")
            size += len(item.encode("utf-8"))
            _need(size <= MAX_OBSERVATION_BYTES, "LIMIT")
        elif type(item) is int:
            _need(-MAX_INTEGER <= item <= MAX_INTEGER, "INTEGER_INVALID")
        else:
            _need(item is None, "JSON_INVALID")
    visit(value, 0)


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


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _parse_integer(text):
    digits = text[1:] if text.startswith("-") else text
    _need(len(digits) <= 19, "INTEGER_INVALID")
    result = int(text)
    _need(-MAX_INTEGER <= result <= MAX_INTEGER, "INTEGER_INVALID")
    return result


def _reject_number(_text):
    _fail("JSON_INVALID")


def _json(raw, maximum):
    _need(type(raw) is bytes and 0 < len(raw) <= maximum, "LIMIT")
    try:
        text = raw.decode("utf-8", errors="strict")
        _lexical_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_parse_integer,
                           parse_float=_reject_number, parse_constant=_reject_number)
        _tree(value)
        _need(_canonical(value) == raw, "JSON_NONCANONICAL")
        return value
    except OriginReservationContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def _path(value):
    _need(type(value) is str and len(value) <= 4096, "PROFILE_INVALID")
    try:
        _need(preservation.canonical_path(value) == value, "PROFILE_INVALID")
    except preservation.PersonalPreservationError:
        _fail("PROFILE_INVALID")
    return value


def _identity(value, *, file=False):
    _fields(value, _FILE_IDENTITY if file else _IDENTITY)
    for key in ("device", "inode", "mtime_ns", "ctime_ns") if file else ("device", "inode"):
        _need(type(value[key]) is str and _DECIMAL.fullmatch(value[key]) is not None, "IDENTITY_INVALID")
    if file:
        _integer(value["byte_size"])
        _integer(value["mode"], maximum=2**31 - 1)
        _need(stat.S_ISREG(value["mode"]), "IDENTITY_INVALID")
        _need(type(value["nlink"]) is int and value["nlink"] == 1, "IDENTITY_INVALID")


def _file(value):
    _fields(value, _FILE)
    _sha(value["sha256"])
    _integer(value["byte_size"])
    _identity(value["directory_identity"])
    _identity(value["path_identity"], file=True)
    _identity(value["handle_identity"], file=True)
    _need(value["byte_size"] == value["path_identity"]["byte_size"] == value["handle_identity"]["byte_size"],
          "BINDING_MISMATCH")
    _need(all(value["path_identity"][key] == value["handle_identity"][key]
              for key in _FILE_IDENTITY - {"ctime_ns"}), "BINDING_MISMATCH")


def _profile(value):
    _fields(value, {"profile_paths", "roots"})
    paths, roots = value["profile_paths"], value["roots"]
    _need(type(paths) is list and len(paths) == 4, "PROFILE_INVALID")
    for path in paths:
        _path(path)
    _fields(roots, _ROOTS)
    path_module = ntpath if "\\" in paths[0] else posixpath
    expected = [path_module.dirname(paths[0]), *paths[1:]]
    for name, path in zip(_ROOTS, expected):
        _fields(roots[name], {"path", "identity"})
        _need(_path(roots[name]["path"]) == path, "PROFILE_INVALID")
        _identity(roots[name]["identity"])


def _blob(text, maximum):
    _need(type(text) is str and 0 < len(text) <= ((maximum + 2) // 3) * 4, "LIMIT")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        _fail("BASE64_INVALID")
    _need(0 < len(raw) <= maximum and base64.b64encode(raw).decode("ascii") == text, "BASE64_INVALID")
    return raw


def _summary(value, columns):
    _fields(value, {"columns", "count", "sha256", "schema_sha256"})
    _need(type(value["columns"]) is list and value["columns"] == list(columns), "EVIDENCE_INVALID")
    _integer(value["count"], maximum=100_000)
    _sha(value["sha256"])
    _sha(value["schema_sha256"])


def _evidence(raw):
    value = _json(raw, MAX_EVIDENCE_BYTES)
    _fields(value, _EVIDENCE)
    _need(type(value["format_version"]) is int and value["format_version"] == 1
          and value["scope"] == "personal-authority-origin-source-v1", "EVIDENCE_INVALID")
    _need(type(value["profile_paths"]) is list and len(value["profile_paths"]) == 4, "PROFILE_INVALID")
    for path in value["profile_paths"]:
        _path(path)
    _need(_sha(value["profile_sha256"]) == _digest(value["profile_paths"]), "BINDING_MISMATCH")
    _sha(value["intent_sha256"])
    _file(value["node"])
    _need(value["node"]["byte_size"] <= MAX_NODE_BYTES, "EVIDENCE_INVALID")
    fingerprint = value["source_fingerprint"]
    _fields(fingerprint, {"format_version", "profile_identity", "files"})
    _need(type(fingerprint["format_version"]) is int and fingerprint["format_version"] == 1
          and fingerprint["profile_identity"] == value["profile_paths"], "BINDING_MISMATCH")
    _fields(fingerprint["files"], {"", "-wal", "-journal"})
    _fields(value["raw_sql_files"], {"", "-wal", "-journal"})
    total = 0
    for suffix in ("", "-wal", "-journal"):
        expected, observed = fingerprint["files"][suffix], value["raw_sql_files"][suffix]
        if expected is None or observed is None:
            _need(expected is observed and suffix != "", "EVIDENCE_INVALID")
            continue
        _fields(expected, _WIRE_IDENTITY | {"sha256"})
        _file(observed)
        _need({key: observed["path_identity"][key] for key in _WIRE_IDENTITY}
              == {key: expected[key] for key in _WIRE_IDENTITY}
              and observed["sha256"] == expected["sha256"], "BINDING_MISMATCH")
        _need(observed["directory_identity"] == value["node"]["directory_identity"], "BINDING_MISMATCH")
        if suffix == "-journal":
            _need(observed["byte_size"] == 0, "EVIDENCE_INVALID")
        total += observed["byte_size"]
    _need(total <= 2 * 1024**3, "LIMIT")
    meta, sql = value["meta_document"], value["sql_document"]
    _fields(meta, {"format_version", "scope", "schema_version", "meta"})
    _fields(sql, {"format_version", "scope", "schema_version", "meta", "personal_tables"})
    for document, scope in ((meta, "personal-authority-meta-v1"), (sql, "personal-authority-ten-and-meta-v1")):
        _need(type(document["format_version"]) is int and document["format_version"] == 1
              and type(document["schema_version"]) is int and document["schema_version"] == 17
              and document["scope"] == scope, "EVIDENCE_INVALID")
        _summary(document["meta"], ("key", "value"))
        _need(document["meta"]["count"] == 4, "EVIDENCE_INVALID")
    _need(meta["meta"] == sql["meta"], "BINDING_MISMATCH")
    _fields(sql["personal_tables"], preservation.PERSONAL_COLUMNS)
    for name, columns in preservation.PERSONAL_COLUMNS.items():
        _summary(sql["personal_tables"][name], columns)
    _need(sum(table["count"] for table in sql["personal_tables"].values()) <= 100_000, "LIMIT")
    return value


def _normalize(value):
    _tree(value)
    _need(type(value) is dict, "FIELDS_INVALID")
    kind = value.get("kind")
    _need(type(kind) is str and kind in {"origin_reservation", "origin_observation"}, "KIND_INVALID")
    _fields(value, _RESERVATION if kind == "origin_reservation" else _OBSERVATION)
    _need(type(value["format_version"]) is int and value["format_version"] == FORMAT_VERSION
          and value["family"] == FAMILY, "VERSION_INVALID")
    _uuid(value["init_operation_id"])
    if kind == "origin_reservation":
        _uuid(value["domain_id"])
        _uuid(value["facility_id"])
        _profile(value["profile"])
        _need(_sha(value["authority_profile_paths_sha256"]) == _digest(value["profile"]["profile_paths"])
              and _sha(value["physical_profile_sha256"]) == _digest(value["profile"]), "BINDING_MISMATCH")
        _fields(value["node"], _FILE | {"node_id"})
        _uuid(value["node"]["node_id"])
        _file({key: value["node"][key] for key in _FILE})
        _need(value["node"]["byte_size"] <= MAX_NODE_BYTES
              and value["node"]["directory_identity"] == value["profile"]["roots"]["data"]["identity"],
              "BINDING_MISMATCH")
    else:
        _sha(value["reservation_sha256"])
        intent = _blob(value["origin_intent_base64"], MAX_INTENT_BYTES)
        evidence = _blob(value["origin_evidence_base64"], MAX_EVIDENCE_BYTES)
        _need(_sha(value["origin_intent_sha256"]) == hashlib.sha256(intent).hexdigest()
              and _sha(value["origin_evidence_sha256"]) == hashlib.sha256(evidence).hexdigest(), "BINDING_MISMATCH")
        try:
            origin = authority.decode(intent)
        except authority.ContractError:
            _fail("EVIDENCE_INVALID")
        _need(origin["kind"] == "origin_init_intent" and origin["operation_id"] == value["init_operation_id"],
              "BINDING_MISMATCH")
        observed = _evidence(evidence)
        _need(observed["intent_sha256"] == value["origin_intent_sha256"]
              and observed["profile_sha256"] == origin["issuer"]["profile_sha256"]
              and origin["payload"]["observed_primary_node_id"] == origin["issuer"]["node_id"]
              and origin["payload"]["observed_personal_meta_sha256"] == _digest(observed["meta_document"])
              and origin["payload"]["observed_personal_sql_sha256"] == _digest(observed["sql_document"]), "BINDING_MISMATCH")
    maximum = MAX_RESERVATION_BYTES if kind == "origin_reservation" else MAX_OBSERVATION_BYTES
    _need(len(_canonical(value)) <= maximum, "LIMIT")
    return deepcopy(value)


def encode(value) -> bytes:
    try:
        return _canonical(_normalize(value))
    except OriginReservationContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")


def decode(raw: bytes) -> dict:
    return _normalize(_json(raw, MAX_OBSERVATION_BYTES))


def record_sha256(raw: bytes) -> str:
    decode(raw)
    return hashlib.sha256(raw).hexdigest()


def stage_name(name: str, init_operation_id: str) -> str:
    _need(type(name) is str and name in {RESERVATION_NAME, OBSERVATION_NAME}, "KIND_INVALID")
    return name + "." + _uuid(init_operation_id) + ".stage"


def cross_validate(reservation_raw: bytes, observation_raw: bytes) -> None:
    first, second = decode(reservation_raw), decode(observation_raw)
    _need(first["kind"] == "origin_reservation" and second["kind"] == "origin_observation", "KIND_INVALID")
    _need(first["init_operation_id"] == second["init_operation_id"]
          and second["reservation_sha256"] == hashlib.sha256(reservation_raw).hexdigest(), "BINDING_MISMATCH")
    intent = authority.decode(_blob(second["origin_intent_base64"], MAX_INTENT_BYTES))
    evidence = _evidence(_blob(second["origin_evidence_base64"], MAX_EVIDENCE_BYTES))
    _need(intent["domain_id"] == first["domain_id"]
          and intent["issuer"] == {"facility_id": first["facility_id"], "node_id": first["node"]["node_id"],
                                   "profile_sha256": first["authority_profile_paths_sha256"]}
          and evidence["profile_paths"] == first["profile"]["profile_paths"]
          and evidence["node"] == {key: first["node"][key] for key in _FILE}, "BINDING_MISMATCH")


def encode_observation(reservation_raw: bytes, intent_bytes: bytes, evidence_bytes: bytes) -> bytes:
    first = decode(reservation_raw)
    _need(first["kind"] == "origin_reservation", "KIND_INVALID")
    _need(type(intent_bytes) is bytes and 0 < len(intent_bytes) <= MAX_INTENT_BYTES
          and type(evidence_bytes) is bytes and 0 < len(evidence_bytes) <= MAX_EVIDENCE_BYTES, "LIMIT")
    raw = encode({"format_version": FORMAT_VERSION, "family": FAMILY, "kind": "origin_observation",
        "init_operation_id": first["init_operation_id"], "reservation_sha256": hashlib.sha256(reservation_raw).hexdigest(),
        "origin_intent_sha256": hashlib.sha256(intent_bytes).hexdigest(),
        "origin_intent_base64": base64.b64encode(intent_bytes).decode("ascii"),
        "origin_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "origin_evidence_base64": base64.b64encode(evidence_bytes).decode("ascii")})
    cross_validate(reservation_raw, raw)
    return raw
