"""Closed personal-preservation facts; no I/O, SQL, configuration or authority.

All validators return detached dictionaries. Physical observations and durable
publication belong to the producer/readers, never to a successfully validated
DTO. Source and copy identities stay separate, including Windows' distinct
path-stat and handle-stat ctime observations. Released backup/wire contracts
and the four artifact namespaces are deliberately not imported or changed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from functools import wraps
import hashlib
import json
import math
import ntpath
import posixpath
import re
import uuid

FORMAT_VERSION = 1
CATALOG = ".reno-personal-preservation-catalog-v1"
DIRECTORY = ".reno-personal-preservation-bundles-v1"
SCOPE = "personal-facts-and-mirror-raw-v1"
MAX_FILES = 100_000
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 ** 3
MAX_SQL_BYTES = 2 * 1024 ** 3
MAX_ROWS = 1_000_000
MAX_CELL_BYTES = 32 * 1024 * 1024
DEFAULT_LIMITS = {"max_files": MAX_FILES, "max_bytes": 16 * 1024 ** 3, "max_seconds": 120}
ROOT_KINDS = frozenset({"DATA", "WORKSPACE", "CONFIG", "MEDIA", "REPORTS", "CHECKPOINTS"})
ROLES = frozenset({"database_raw", "database_wal_raw", "database_journal_raw", "node_identity_raw",
    "local_config_raw", "knowledge_state_raw", "current_personal_attachment", "historical_home_attachment",
    "receiver_stored_attachment", "sender_facility_raw", "sender_catalog_raw", "sender_request_raw",
    "sender_blob_manifest_raw", "sender_frozen_attachment", "sender_receipt_raw", "sender_lock_raw", "unclassified_raw"})
PERSONAL_COLUMNS = {
    "renovation_project": tuple("project_id name profile_json revision created_at updated_at archived_at".split()),
    "decision_card": tuple("card_id project_id title question context_json space stage alternatives_json selected_option_id decision_text decision_reason open_questions_json content_snapshot_json state revision created_at updated_at archived_at".split()),
    "source_record": tuple("source_id project_id source_type title publisher document_no edition published_on valid_from valid_to source_url locator excerpt scope_json verification_state validity checked_at check_note revision created_at updated_at".split()),
    "source_attachment": tuple("attachment_id source_id project_id sha256 byte_size media_type original_name storage_key state created_at operation_id error".split()),
    "personal_evidence_ref": tuple("ref_id project_id owner_kind owner_id target_kind target_id fingerprint_version target_fingerprint snapshot_json created_at".split()),
    "checklist_instance": tuple("instance_id project_id title space stage basis_kb_revision lifecycle_state revision created_at updated_at archived_at".split()),
    "checklist_item": tuple("item_id instance_id title conditions_json method_text basis_kind criteria_json criteria_state position revision created_at updated_at".split()),
    "checklist_check": tuple("check_id item_id item_revision criteria_snapshot_hash operation_id commit_seq record_kind result measurements_json note attachment_refs_json checked_at created_at supersedes_check_id".split()),
    "personal_revision": tuple("entity_kind entity_id revision snapshot_json operation_id created_at".split()),
    "mutation_receipt": tuple("operation_id scope request_hash response_status response_json created_at".split()),
}
RECEIVER_COLUMNS = {
    "personal_mirror_transfer_intent": tuple("operation_id format_version prepare_json prepare_sha256 snapshot_id snapshot_sha256 source_library_id source_node_id source_primary_id source_primary_epoch receiver_library_id receiver_node_id previous_snapshot_id previous_snapshot_sha256 expected_revision state created_at updated_at".split()),
    "personal_mirror_transfer_attachment": tuple("operation_id attachment_id storage_key sha256 byte_size descriptor_json descriptor_sha256 state local_file_device local_file_inode local_file_mtime_ns local_file_ctime_ns stored_at".split()),
    "personal_mirror_transfer_receipt": tuple("operation_id format_version prepare_sha256 snapshot_id snapshot_sha256 installed_revision receipt_json receipt_sha256 committed_at".split()),
    "personal_mirror_transfer_head": tuple("slot format_version operation_id source_library_id source_node_id source_primary_id source_primary_epoch receiver_library_id receiver_node_id revision snapshot_id snapshot_sha256 receipt_sha256 updated_at".split()),
}
PHASES = ("reserved", "selected", "source_verified", "manifest", "ready")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_ENTRY = re.compile(r"f[0-9]{8}\Z")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_PROFILE_FIELDS = ("database", "media", "reports", "checkpoints")
_IDENTITY_FIELDS = frozenset({"device", "inode", "byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"})
_WIRE_IDENTITY_FIELDS = _IDENTITY_FIELDS - {"mode", "nlink"}
_FILE_FIELDS = frozenset({"entry_id", "root_kind", "relative_key", "role", "sha256", "byte_size",
    "source_directory_identity", "source_path_identity", "source_handle_identity", "binding_refs"})
_COPY_FIELDS = frozenset({"format_version", "kind", "operation_id", "selection_sha256", "entry_id", "attempt_id",
    "target_relative_key", "target_parent_identity", "sha256", "byte_size"})
_BODY_ROLES = frozenset({"current_personal_attachment", "historical_home_attachment", "receiver_stored_attachment"})
_HASH_FIELDS = ("selection_sha256", "source_verified_sha256", "manifest_sha256", "receipt_sha256")


class PersonalPreservationError(ValueError):
    def __init__(self, code, *, retriable=False):
        self.code = "PERSONAL_PRESERVATION_" + code
        self.retriable = bool(retriable)
        super().__init__(self.code)


Error = PersonalPreservationError
PersonalPreservationContractError = PersonalPreservationError


def _fail(code="INVALID", *, retriable=False):
    raise PersonalPreservationError(code, retriable=retriable) from None


def _checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PersonalPreservationError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            _fail()
    return wrapped


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail("FIELDS_INVALID")


def _same(value, expected, code="BINDING_CHANGED"):
    if type(value) is not type(expected) or value != expected:
        _fail(code)
    if type(value) is dict:
        for key in value:
            _same(value[key], expected[key], code)
    elif type(value) in (list, tuple):
        for item, original in zip(value, expected):
            _same(item, original, code)


def _integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("INTEGER_INVALID")


def _text(value, *, maximum=4096, empty=False):
    if type(value) is not str or (not empty and not value) or len(value.encode("utf-8")) > maximum or "\x00" in value:
        _fail("TEXT_INVALID")


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("SHA256_INVALID")


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail("UUID_INVALID")


def _decimal(value, *, positive=False):
    if type(value) is not str or len(value) > 40 or not _DECIMAL.fullmatch(value) or (positive and value == "0"):
        _fail("IDENTITY_INVALID")


def _stamp(value):
    if type(value) is not str or not _STAMP.fullmatch(value):
        _fail("TIMESTAMP_INVALID")
    datetime.fromisoformat(value[:-1] + "+00:00")


def _header(value, kind, fields):
    _exact(value, fields)
    if type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION or value["kind"] != kind:
        _fail("VERSION_OR_KIND_INVALID")
    _uuid(value["operation_id"])


def _json_tree(value, *, depth=0, budget=None, active=None):
    budget = [0, 0] if budget is None else budget
    active = set() if active is None else active
    budget[0] += 1
    if depth > 64 or budget[0] > 4_000_000:
        _fail("JSON_LIMIT")
    kind = type(value)
    if kind is str:
        budget[1] += len(value.encode("utf-8"))
        if budget[1] > MAX_JSON_BYTES:
            _fail("JSON_LIMIT")
    elif kind is float:
        if not math.isfinite(value):
            _fail("JSON_NONFINITE")
    elif value is None or kind in (bool, int):
        pass
    elif kind in (list, dict):
        if id(value) in active:
            _fail("JSON_INVALID")
        active.add(id(value))
        if kind is dict:
            for key, item in value.items():
                if type(key) is not str:
                    _fail("JSON_INVALID")
                _json_tree(key, depth=depth + 1, budget=budget, active=active)
                _json_tree(item, depth=depth + 1, budget=budget, active=active)
        else:
            for item in value:
                _json_tree(item, depth=depth + 1, budget=budget, active=active)
        active.remove(id(value))
    else:
        _fail("JSON_INVALID")


@_checked
def canonical_bytes(value):
    _json_tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        _fail("JSON_LIMIT")
    return raw


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@_checked
def decode(raw):
    if type(raw) is str:
        raw = raw.encode("utf-8")
    if type(raw) is not bytes or len(raw) > MAX_JSON_BYTES:
        _fail("JSON_LIMIT")
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                _fail("JSON_DUPLICATE_KEY")
            result[key] = item
        return result
    def constant(_value):
        _fail("JSON_NONFINITE")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict:
        _fail("JSON_OBJECT_REQUIRED")
    _json_tree(value)
    return value


@_checked
def canonical_path(value):
    """Normalize an absolute physical key lexically, without touching disk."""
    _text(value)
    if ".." in value.replace("\\", "/").split("/"):
        _fail("PATH_INVALID")
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        if value.startswith(("\\\\?\\", "\\\\.\\")):
            _fail("PATH_INVALID")
        result = ntpath.normcase(ntpath.normpath(value))
        drive, tail = ntpath.splitdrive(result)
        if not drive or not tail.startswith("\\") or ":" in tail:
            _fail("PATH_INVALID")
        parts = tail.split("\\")
    else:
        if not value.startswith("/") or value.startswith("//") or "\\" in value:
            _fail("PATH_INVALID")
        result = posixpath.normpath(value)
        parts = value.split("/")
    if ".." in parts or any(ord(char) < 32 for char in value):
        _fail("PATH_INVALID")
    return result


def _path(value):
    _same(value, canonical_path(value), "PATH_NOT_CANONICAL")


def _path_module(value):
    return ntpath if "\\" in value else posixpath


def _parent(value):
    return _path_module(value).dirname(value)


def _join(value, *parts):
    return canonical_path(_path_module(value).join(value, *parts))


@_checked
def bundle_id(operation_id):
    _uuid(operation_id)
    return str(uuid.uuid5(uuid.UUID(operation_id), "reno.personal.preservation.bundle.v1"))


def _name(value):
    _text(value, maximum=255)
    if value in {".", ".."} or any(char in value for char in "/\\:") or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail("RELATIVE_PATH_INVALID")


def _relative(value, *, directory=False):
    _text(value, empty=directory)
    if value == "" and directory:
        return
    for part in value.split("/"):
        _name(part)


def _directory(value):
    _exact(value, {"device", "inode"})
    _decimal(value["device"])
    _decimal(value["inode"], positive=True)


@_checked
def validate_directory_identity(value):
    _directory(value)
    return deepcopy(value)


def _identity(value, *, wire=False):
    _exact(value, _WIRE_IDENTITY_FIELDS if wire else _IDENTITY_FIELDS)
    for key in ("device", "inode", "mtime_ns", "ctime_ns"):
        _decimal(value[key], positive=key == "inode")
    _integer(value["byte_size"], maximum=MAX_TOTAL_BYTES)
    if not wire:
        _integer(value["mode"], maximum=2**32 - 1)
        if value["nlink"] != 1 or type(value["nlink"]) is not int or value["mode"] & 0o170000 != 0o100000:
            _fail("FILE_NOT_ORDINARY")


@_checked
def validate_file_identity(value):
    _identity(value)
    return deepcopy(value)


def _identity_pair(path, handle):
    _identity(path)
    _identity(handle)
    for key in _IDENTITY_FIELDS - {"ctime_ns"}:
        _same(path[key], handle[key], "PATH_HANDLE_IDENTITY_CHANGED")


@_checked
def typed_cell(value):
    if value is None:
        return ["null"]
    if type(value) is int:
        return ["int", value]
    if type(value) is float:
        if not math.isfinite(value):
            _fail("SQL_NONFINITE")
        return ["float", value.hex()]
    if type(value) is bytes:
        if len(value) > MAX_CELL_BYTES:
            _fail("SQL_CELL_LIMIT")
        return ["blob", value.hex()]
    if type(value) is str and len(value.encode("utf-8")) <= MAX_CELL_BYTES:
        return ["text", value]
    _fail("SQL_CELL_INVALID")


@_checked
def typed_table_digest(columns, rows):
    if type(columns) not in (list, tuple) or not columns or len(columns) > 64 or len(set(columns)) != len(columns):
        _fail("SQL_COLUMNS_INVALID")
    for column in columns:
        _text(column, maximum=128)
    encoded = []
    total = 0
    for row in rows:
        if type(row) not in (tuple, list) or len(row) != len(columns) or len(encoded) >= MAX_ROWS:
            _fail("SQL_ROWS_INVALID")
        raw = canonical_bytes([typed_cell(item) for item in row])
        total += len(raw)
        if total > MAX_JSON_BYTES:
            _fail("SQL_ROWS_LIMIT")
        encoded.append(raw)
    result = hashlib.sha256(canonical_bytes(list(columns)))
    for raw in sorted(encoded):
        result.update(len(raw).to_bytes(8, "big"))
        result.update(raw)
    return {"columns": list(columns), "count": len(encoded), "sha256": result.hexdigest()}


def _limits(value):
    _exact(value, DEFAULT_LIMITS)
    _integer(value["max_files"], 1, MAX_FILES)
    _integer(value["max_bytes"], 1, MAX_TOTAL_BYTES)
    _integer(value["max_seconds"], 1, 3600)


@_checked
def validate_limits(value):
    _limits(value)
    return deepcopy(value)


def _profile(value):
    _exact(value, _PROFILE_FIELDS)
    for path in value.values():
        _path(path)
    if _path_module(value["database"]).basename(value["database"]) in {"", ".", ".."}:
        _fail("PROFILE_INVALID")


@_checked
def validate_profile(value):
    _profile(value)
    return deepcopy(value)


def _request(value):
    _header(value, "personal_preservation_request", {"format_version", "kind", "operation_id", "bundle_id",
        "source_profile", "source_root_identity", "target_parent_identity", "target", "scope", "limits"})
    _same(value["bundle_id"], bundle_id(value["operation_id"]), "BUNDLE_ID_CHANGED")
    _profile(value["source_profile"])
    _directory(value["source_root_identity"])
    _directory(value["target_parent_identity"])
    _path(value["target"])
    _same(value["target"], _join(_parent(value["source_profile"]["database"]), DIRECTORY, value["bundle_id"]), "TARGET_INVALID")
    _same(value["scope"], SCOPE, "SCOPE_INVALID")
    _limits(value["limits"])
    return value


@_checked
def validate_request(value):
    return deepcopy(_request(value))


def _summary(value, columns):
    _exact(value, {"columns", "count", "sha256", "schema_sha256"})
    if type(value["columns"]) is not list:
        _fail("SQL_COLUMNS_INVALID")
    _same(value["columns"], list(columns), "SQL_COLUMNS_CHANGED")
    _integer(value["count"], maximum=MAX_ROWS)
    _sha(value["sha256"])
    _sha(value["schema_sha256"])


def _fingerprint(value):
    _exact(value, {"format_version", "profile_identity", "files"})
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail("SQL_FINGERPRINT_INVALID")
    if type(value["profile_identity"]) is not list or len(value["profile_identity"]) != 4:
        _fail("SQL_FINGERPRINT_INVALID")
    for path in value["profile_identity"]:
        _path(path)
    _exact(value["files"], {"", "-wal", "-journal"})
    for suffix, item in value["files"].items():
        if item is None:
            if suffix == "":
                _fail("SQL_DATABASE_REQUIRED")
            continue
        _exact(item, _WIRE_IDENTITY_FIELDS | {"sha256"})
        _identity({key: item[key] for key in _WIRE_IDENTITY_FIELDS}, wire=True)
        _sha(item["sha256"])
        if suffix == "" and item["byte_size"] == 0:
            _fail("SQL_DATABASE_REQUIRED")
        if suffix == "-journal" and item["byte_size"] != 0:
            _fail("ROLLBACK_JOURNAL_PRESENT", retriable=True)
    if sum(item["byte_size"] for item in value["files"].values() if item is not None) > MAX_SQL_BYTES:
        _fail("SQL_BYTES_LIMIT")


def _sql(value):
    _exact(value, {"schema_version", "source_fingerprint", "meta", "personal_tables", "receiver_facility",
                   "receiver_tables", "integrity", "foreign_keys"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 17:
        _fail("SCHEMA_REQUIRED")
    _fingerprint(value["source_fingerprint"])
    _summary(value["meta"], ("key", "value"))
    _exact(value["personal_tables"], PERSONAL_COLUMNS)
    for table, columns in PERSONAL_COLUMNS.items():
        _summary(value["personal_tables"][table], columns)
    if value["receiver_facility"] == "absent":
        _exact(value["receiver_tables"], set())
    elif value["receiver_facility"] == "installed":
        _exact(value["receiver_tables"], RECEIVER_COLUMNS)
        for table, columns in RECEIVER_COLUMNS.items():
            _summary(value["receiver_tables"][table], columns)
    else:
        _fail("RECEIVER_FACILITY_INVALID")
    if value["integrity"] != "ok" or value["foreign_keys"] != "ok":
        _fail("SQL_INTEGRITY_UNPROVEN")


@_checked
def validate_sql(value):
    _sql(value)
    return deepcopy(value)


def _observation(value, sql, request):
    _exact(value, {"library_id", "node_id", "personal_primary_id", "primary_epoch", "profile_sha256", "node_sha256",
                  "roots", "sender_facility", "receiver_facility"})
    _uuid(value["library_id"])
    _uuid(value["node_id"])
    if value["personal_primary_id"] is not None:
        _uuid(value["personal_primary_id"])
    _integer(value["primary_epoch"], 1)
    _sha(value["profile_sha256"])
    _sha(value["node_sha256"])
    _exact(value["roots"], ROOT_KINDS)
    for item in value["roots"].values():
        _exact(item, {"path", "device", "inode", "membership"})
        _path(item["path"])
        _directory({key: item[key] for key in ("device", "inode")})
        if item["membership"] not in {"unknown", "complete"}:
            _fail("MEMBERSHIP_INVALID")
    for name in ("sender_facility", "receiver_facility"):
        if value[name] not in {"absent", "installed", "unknown"}:
            _fail("FACILITY_INVALID")
    _same(value["receiver_facility"], sql["receiver_facility"], "RECEIVER_FACILITY_CHANGED")
    profile = dict(zip(_PROFILE_FIELDS, sql["source_fingerprint"]["profile_identity"]))
    _same(value["profile_sha256"], digest(profile), "PROFILE_CHANGED")
    for kind, path in {"DATA": _parent(profile["database"]), "MEDIA": profile["media"],
                       "REPORTS": profile["reports"], "CHECKPOINTS": profile["checkpoints"]}.items():
        _same(value["roots"][kind]["path"], path, "ROOT_PATH_CHANGED")
    if request is not None:
        _same(profile, request["source_profile"], "PROFILE_CHANGED")
        _same({key: value["roots"]["DATA"][key] for key in ("device", "inode")}, request["source_root_identity"], "ROOT_IDENTITY_CHANGED")


def _inventory(items):
    if type(items) is not list or len(items) > MAX_FILES:
        _fail("INVENTORY_LIMIT")
    positions, result = [], {}
    for item in items:
        _exact(item, {"root_kind", "relative_key", "present", "identity", "names", "names_sha256"})
        if item["root_kind"] not in ROOT_KINDS or type(item["present"]) is not bool:
            _fail("INVENTORY_INVALID")
        _relative(item["relative_key"], directory=True)
        names = item["names"]
        if type(names) is not list or len(names) > MAX_FILES:
            _fail("INVENTORY_LIMIT")
        for name in names:
            _name(name)
        _same(names, sorted(set(names)), "INVENTORY_NAMES_CHANGED")
        _same(item["names_sha256"], digest(names), "INVENTORY_NAMES_CHANGED")
        if item["present"]:
            _directory(item["identity"])
        elif item["identity"] is not None or names:
            _fail("INVENTORY_ABSENCE_INVALID")
        position = (item["root_kind"], item["relative_key"])
        positions.append(position)
        result[position] = item
    _same(positions, sorted(set(positions)), "INVENTORY_SET_CHANGED")
    return result


def _role(file, profile):
    role, root, key = file["role"], file["root_kind"], file["relative_key"]
    database = _path_module(profile["database"]).basename(profile["database"])
    fixed = {"database_raw": database, "database_wal_raw": database + "-wal", "database_journal_raw": database + "-journal",
        "node_identity_raw": "node-identity.json", "knowledge_state_raw": "sync-state.json",
        "sender_facility_raw": "personal-mirror-outbox/facility.json", "sender_catalog_raw": "personal-mirror-outbox/catalog.json",
        "sender_lock_raw": "personal-mirror-outbox/operation.lock"}
    if role in fixed:
        if root != "DATA" or key != fixed[role]:
            _fail("FILE_ROLE_PATH_INVALID")
    elif role in _BODY_ROLES:
        parts = key.split("/")
        if root != "DATA" or len(parts) != 3 or parts[:2] != ["personal-attachments", "files"]:
            _fail("FILE_ROLE_PATH_INVALID")
        _attachment_name(parts[2])
    elif role in {"sender_request_raw", "sender_blob_manifest_raw", "sender_receipt_raw"}:
        suffix = {"sender_request_raw": ".prepare.json", "sender_blob_manifest_raw": ".blobs.json", "sender_receipt_raw": ".receipt.json"}[role]
        if root != "DATA" or not key.startswith("personal-mirror-outbox/") or key.count("/") != 1 or not key.endswith(suffix):
            _fail("FILE_ROLE_PATH_INVALID")
        _uuid(key.split("/")[1][:-len(suffix)])
    elif role == "sender_frozen_attachment":
        parts = key.split("/")
        if root != "DATA" or len(parts) != 3 or parts[0] != "personal-mirror-outbox" or not parts[1].endswith(".files"):
            _fail("FILE_ROLE_PATH_INVALID")
        _uuid(parts[1][:-len(".files")])
        _attachment_name(parts[2])
    elif role == "local_config_raw":
        if root != "CONFIG" or "/" in key:
            _fail("FILE_ROLE_PATH_INVALID")


def _attachment_name(value):
    stem, suffix = posixpath.splitext(value)
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        _fail("ATTACHMENT_PATH_INVALID")
    _uuid(stem)


def _files(items, inventories, sql, observation, request):
    if type(items) is not list or not 0 < len(items) <= MAX_FILES:
        _fail("FILE_SET_INVALID")
    if request is not None and len(items) > request["limits"]["max_files"]:
        _fail("FILES_LIMIT")
    profile = dict(zip(_PROFILE_FIELDS, sql["source_fingerprint"]["profile_identity"]))
    positions, result, physical = [], {}, set()
    total = 0
    for index, item in enumerate(items):
        _exact(item, _FILE_FIELDS)
        _same(item["entry_id"], f"f{index:08d}", "ENTRY_ORDER_INVALID")
        if item["root_kind"] not in ROOT_KINDS or item["role"] not in ROLES:
            _fail("FILE_ROLE_INVALID")
        _relative(item["relative_key"])
        _sha(item["sha256"])
        _integer(item["byte_size"], maximum=MAX_TOTAL_BYTES)
        _identity_pair(item["source_path_identity"], item["source_handle_identity"])
        _same(item["byte_size"], item["source_path_identity"]["byte_size"], "FILE_SIZE_CHANGED")
        identity = item["source_handle_identity"]
        key = (identity["device"], identity["inode"])
        if key in physical:
            _fail("SOURCE_ALIAS")
        physical.add(key)
        _exact(item["source_directory_identity"], {"device", "inode", "names_sha256"})
        directory = inventories.get((item["root_kind"], posixpath.dirname(item["relative_key"])))
        if directory is None or not directory["present"] or posixpath.basename(item["relative_key"]) not in directory["names"]:
            _fail("FILE_INVENTORY_REQUIRED")
        _same(item["source_directory_identity"], {**directory["identity"], "names_sha256": directory["names_sha256"]}, "DIRECTORY_IDENTITY_CHANGED")
        if type(item["binding_refs"]) is not list:
            _fail("FILE_BINDINGS_INVALID")
        for binding in item["binding_refs"]:
            _sha(binding)
        _same(item["binding_refs"], sorted(set(item["binding_refs"])), "FILE_BINDINGS_INVALID")
        _role(item, profile)
        positions.append((item["root_kind"], item["relative_key"]))
        result[item["entry_id"]] = item
        total += item["byte_size"]
    _same(positions, sorted(set(positions)), "FILE_SET_ORDER_INVALID")
    if total > MAX_TOTAL_BYTES or request is not None and total > request["limits"]["max_bytes"]:
        _fail("BYTES_LIMIT")
    by_role = {role: [file for file in items if file["role"] == role] for role in ROLES}
    for role, suffix in (("database_raw", ""), ("database_wal_raw", "-wal"), ("database_journal_raw", "-journal")):
        expected = sql["source_fingerprint"]["files"][suffix]
        actual = by_role[role]
        if len(actual) != int(expected is not None):
            _fail("SQL_FILE_SET_CHANGED")
        if expected is not None:
            file = actual[0]
            _same((file["sha256"], file["byte_size"]), (expected["sha256"], expected["byte_size"]), "SQL_FILE_CHANGED")
            _same({key: file["source_path_identity"][key] for key in _WIRE_IDENTITY_FIELDS},
                  {key: expected[key] for key in _WIRE_IDENTITY_FIELDS}, "SQL_SOURCE_IDENTITY_CHANGED")
    if len(by_role["node_identity_raw"]) != 1 or by_role["node_identity_raw"][0]["sha256"] != observation["node_sha256"]:
        _fail("NODE_FILE_REQUIRED")
    if observation["sender_facility"] == "installed":
        for role in ("sender_facility_raw", "sender_catalog_raw", "sender_lock_raw"):
            if len(by_role[role]) != 1:
                _fail("SENDER_FACILITY_REQUIRED")
    elif observation["sender_facility"] == "absent" and any(file["role"].startswith("sender_") for file in items):
        _fail("SENDER_FACILITY_CHANGED")
    return result


def _requirements(items, files):
    if type(items) is not list or len(items) > MAX_FILES:
        _fail("REQUIREMENTS_INVALID")
    positions = []
    for index, item in enumerate(items):
        _exact(item, {"requirement_id", "kind", "entry_id", "operation_id", "attachment_id", "sha256", "byte_size",
                      "binding_sha256", "expected_file_identity"})
        _same(item["requirement_id"], f"r{index:08d}", "REQUIREMENT_ORDER_INVALID")
        if item["kind"] not in {"current_ready", "historical_home", "receiver_stored", "sender_frozen"}:
            _fail("REQUIREMENT_KIND_INVALID")
        _uuid(item["operation_id"])
        _uuid(item["attachment_id"])
        _sha(item["sha256"])
        _sha(item["binding_sha256"])
        _integer(item["byte_size"], 1, MAX_TOTAL_BYTES)
        file = files.get(item["entry_id"])
        if file is None:
            _fail("REQUIRED_BODY_MISSING")
        allowed = {"sender_frozen_attachment"} if item["kind"] == "sender_frozen" else _BODY_ROLES
        if file["role"] not in allowed:
            _fail("REQUIRED_BODY_ROLE_CHANGED")
        _same((item["sha256"], item["byte_size"]), (file["sha256"], file["byte_size"]), "REQUIRED_BODY_CHANGED")
        if item["binding_sha256"] not in file["binding_refs"]:
            _fail("REQUIRED_BINDING_MISSING")
        if item["kind"] == "sender_frozen":
            _same(file["relative_key"].split("/")[1], item["operation_id"] + ".files", "REQUIRED_BODY_ORIGIN_CHANGED")
        if item["expected_file_identity"] is not None:
            _identity(item["expected_file_identity"], wire=True)
            _same(item["expected_file_identity"], {key: file["source_handle_identity"][key] for key in _WIRE_IDENTITY_FIELDS}, "REQUIRED_BODY_IDENTITY_CHANGED")
        elif item["kind"] != "current_ready":
            _fail("REQUIRED_IDENTITY_MISSING")
        positions.append((item["kind"], item["operation_id"], item["attachment_id"], item["entry_id"]))
    _same(positions, sorted(set(positions)), "REQUIREMENT_SET_INVALID")
    referenced = {item["entry_id"] for item in items}
    if any(file["role"] in _BODY_ROLES | {"sender_frozen_attachment"} and entry not in referenced for entry, file in files.items()):
        _fail("BODY_REQUIREMENT_MISSING")


def _selection(value, request=None):
    _header(value, "personal_preservation_selection", {"format_version", "kind", "operation_id", "request_sha256",
        "source_observation", "sql", "inventories", "files", "requirements", "unknowns", "limits_sha256"})
    _sha(value["request_sha256"])
    _sha(value["limits_sha256"])
    if request is not None:
        _request(request)
        _same(value["operation_id"], request["operation_id"], "OPERATION_CONFLICT")
        _same(value["request_sha256"], digest(request), "REQUEST_CHANGED")
        _same(value["limits_sha256"], digest(request["limits"]), "LIMITS_CHANGED")
    _sql(value["sql"])
    _observation(value["source_observation"], value["sql"], request)
    inventories = _inventory(value["inventories"])
    files = _files(value["files"], inventories, value["sql"], value["source_observation"], request)
    _requirements(value["requirements"], files)
    _same(value["unknowns"], sorted(entry for entry, file in files.items() if file["role"] == "unclassified_raw"), "UNKNOWN_SET_CHANGED")
    return value


@_checked
def validate_selection(value, *, request=None):
    return deepcopy(_selection(value, request))


def _copy(value, selection, entry, *, record=False, attempt=None, selection_sha=None):
    fields = _COPY_FIELDS | ({"copy_path_identity", "copy_handle_identity"} if record else set())
    _header(value, "personal_preservation_copy_record" if record else "personal_preservation_copy_attempt", fields)
    _same(value["operation_id"], selection["operation_id"], "OPERATION_CONFLICT")
    _same(value["selection_sha256"], digest(selection) if selection_sha is None else selection_sha, "SELECTION_CHANGED")
    _same(value["entry_id"], entry["entry_id"], "COPY_ENTRY_CHANGED")
    _uuid(value["attempt_id"])
    _directory(value["target_parent_identity"])
    _same(value["target_relative_key"], "payload/" + value["attempt_id"] + "/" + value["entry_id"] + ".raw", "COPY_PATH_CHANGED")
    _same((value["sha256"], value["byte_size"]), (entry["sha256"], entry["byte_size"]), "COPY_CONTENT_CHANGED")
    if record:
        _identity_pair(value["copy_path_identity"], value["copy_handle_identity"])
        _same(value["byte_size"], value["copy_path_identity"]["byte_size"], "COPY_SIZE_CHANGED")
        source, copied = entry["source_handle_identity"], value["copy_handle_identity"]
        if (source["device"], source["inode"]) == (copied["device"], copied["inode"]):
            _fail("COPY_SOURCE_ALIAS")
        if attempt is not None:
            _copy(attempt, selection, entry)
            _same({key: value[key] for key in _COPY_FIELDS - {"kind"}},
                  {key: attempt[key] for key in _COPY_FIELDS - {"kind"}}, "COPY_ATTEMPT_CHANGED")
    return value


def _entry(selection, value):
    if type(value) is not str or not _ENTRY.fullmatch(value):
        _fail("ENTRY_INVALID")
    index = int(value[1:])
    if index >= len(selection["files"]) or selection["files"][index]["entry_id"] != value:
        _fail("ENTRY_MISSING")
    return selection["files"][index]


@_checked
def validate_copy_attempt(value, *, selection, request=None):
    _selection(selection, request)
    return deepcopy(_copy(value, selection, _entry(selection, value["entry_id"])))


@_checked
def validate_copy_record(value, *, selection, attempt=None, request=None):
    _selection(selection, request)
    return deepcopy(_copy(value, selection, _entry(selection, value["entry_id"]), record=True, attempt=attempt))


def _records(values, selection):
    if type(values) is not list or len(values) != len(selection["files"]):
        _fail("COPY_SET_INCOMPLETE")
    identities, targets, hashes = set(), set(), []
    selection_sha = digest(selection)
    source_identities = {(item["source_handle_identity"]["device"], item["source_handle_identity"]["inode"])
                         for item in selection["files"]}
    for record, entry in zip(values, selection["files"]):
        # Compute the large immutable selection digest once for the set.
        _copy(record, selection, entry, record=True, selection_sha=selection_sha)
        identity = record["copy_handle_identity"]
        key = (identity["device"], identity["inode"])
        if key in source_identities:
            _fail("COPY_SOURCE_ALIAS")
        if key in identities or record["target_relative_key"] in targets:
            _fail("COPY_ALIAS")
        identities.add(key)
        targets.add(record["target_relative_key"])
        hashes.append({"entry_id": entry["entry_id"], "sha256": digest(record)})
    return hashes


def _verified(value, selection, hashes):
    _header(value, "personal_preservation_source_verified", {"format_version", "kind", "operation_id", "request_sha256",
        "selection_sha256", "attempt_id", "sql_fingerprint_sha256", "copy_record_sha256s", "completed_at"})
    _same(value["operation_id"], selection["operation_id"], "OPERATION_CONFLICT")
    _same(value["request_sha256"], selection["request_sha256"], "REQUEST_CHANGED")
    _same(value["selection_sha256"], digest(selection), "SELECTION_CHANGED")
    _uuid(value["attempt_id"])
    _same(value["sql_fingerprint_sha256"], digest(selection["sql"]["source_fingerprint"]), "SQL_FINGERPRINT_CHANGED")
    _same(value["copy_record_sha256s"], hashes, "COPY_SET_CHANGED")
    _stamp(value["completed_at"])
    return value


def _hash_index(values, selection):
    if type(values) is not list or len(values) != len(selection["files"]):
        _fail("COPY_SET_INCOMPLETE")
    for value, entry in zip(values, selection["files"]):
        _exact(value, {"entry_id", "sha256"})
        _same(value["entry_id"], entry["entry_id"], "COPY_SET_CHANGED")
        _sha(value["sha256"])
    return values


@_checked
def validate_source_verified(value, *, selection, copy_records, request=None):
    _selection(selection, request)
    return deepcopy(_verified(value, selection, _records(copy_records, selection)))


def _manifest(value, selection, source_verified, copy_records, request):
    _header(value, "personal_preservation_manifest", {"format_version", "kind", "operation_id", "bundle_id", "request_sha256",
        "selection_sha256", "source_verified_sha256", "source_observation", "sql", "files", "copy_record_sha256s",
        "requirements", "coverage", "created_at"})
    _same(value["operation_id"], selection["operation_id"], "OPERATION_CONFLICT")
    _same(value["bundle_id"], bundle_id(value["operation_id"]), "BUNDLE_ID_CHANGED")
    for key, expected in (("request_sha256", selection["request_sha256"]), ("selection_sha256", digest(selection)),
                          ("source_verified_sha256", digest(source_verified)), ("source_observation", selection["source_observation"]),
                          ("sql", selection["sql"]), ("requirements", selection["requirements"])):
        _same(value[key], expected, "MANIFEST_CHANGED")
    hashes = _records(copy_records, selection)
    _verified(source_verified, selection, hashes)
    _same(value["copy_record_sha256s"], hashes, "COPY_SET_CHANGED")
    _same(value["files"], [{"source": source, "copy": copy} for source, copy in zip(selection["files"], copy_records)], "MANIFEST_FILES_CHANGED")
    expected = {"personal_original_facts": "complete", "sender_raw_history": "complete", "receiver_original_facts": "complete",
        "unknowns_present": bool(selection["unknowns"]), "knowledge_restore_dependencies": "unknown",
        "runtime_restore_authority": False, "primary_handoff_authority": False}
    _exact(value["coverage"], expected)
    if any(type(value["coverage"][key]) is not type(item) or value["coverage"][key] != item for key, item in expected.items()):
        _fail("COVERAGE_AUTHORITY_INVALID")
    if selection["source_observation"]["sender_facility"] == "unknown":
        _fail("FACILITY_UNPROVEN")
    _stamp(value["created_at"])
    if datetime.fromisoformat(value["created_at"][:-1] + "+00:00") < datetime.fromisoformat(source_verified["completed_at"][:-1] + "+00:00"):
        _fail("TIMESTAMP_ORDER_INVALID")
    return value


@_checked
def validate_manifest(value, *, selection, source_verified, copy_records, request=None):
    _selection(selection, request)
    return deepcopy(_manifest(value, selection, source_verified, copy_records, request))


@_checked
def validate_receipt(value, *, manifest, selection, source_verified, request=None):
    _selection(selection, request)
    if type(manifest) is not dict or type(manifest.get("files")) is not list:
        _fail("MANIFEST_REQUIRED")
    records = [item["copy"] for item in manifest["files"]]
    _manifest(manifest, selection, source_verified, records, request)
    _header(value, "personal_preservation_receipt", {"format_version", "kind", "operation_id", "request_sha256",
        "selection_sha256", "manifest_sha256", "source_verified_sha256", "target_directory_identity", "completed_at"})
    for key, expected in (("operation_id", manifest["operation_id"]), ("request_sha256", manifest["request_sha256"]),
                          ("selection_sha256", digest(selection)), ("manifest_sha256", digest(manifest)),
                          ("source_verified_sha256", digest(source_verified))):
        _same(value[key], expected, "RECEIPT_CHANGED")
    _directory(value["target_directory_identity"])
    _stamp(value["completed_at"])
    if datetime.fromisoformat(value["completed_at"][:-1] + "+00:00") < datetime.fromisoformat(manifest["created_at"][:-1] + "+00:00"):
        _fail("TIMESTAMP_ORDER_INVALID")
    return deepcopy(value)


@_checked
def validate_state(value, *, request=None, selection=None, source_verified=None, manifest=None, receipt=None):
    _header(value, "personal_preservation_state", {"format_version", "kind", "operation_id", "request_sha256", "phase",
        *_HASH_FIELDS, "updated_at", "failure"})
    _sha(value["request_sha256"])
    _stamp(value["updated_at"])
    phase = value["phase"]
    if phase not in (*PHASES, "failed"):
        _fail("STATE_PHASE_INVALID")
    failure = value["failure"]
    effective_phase = phase
    if phase == "failed":
        _exact(failure, {"code", "phase", "attempt_id", "original_digests"})
        if type(failure["code"]) is not str or not _CODE.fullmatch(failure["code"]) or failure["phase"] not in PHASES:
            _fail("FAILURE_INVALID")
        _uuid(failure["attempt_id"])
        _exact(failure["original_digests"], {"request_sha256", *_HASH_FIELDS})
        _same(failure["original_digests"], {key: value[key] for key in ("request_sha256", *_HASH_FIELDS)}, "FAILURE_DIGEST_CHANGED")
        effective_phase = failure["phase"]
    elif failure is not None:
        _fail("FAILURE_INVALID")
    threshold = PHASES.index(effective_phase)
    if phase == "failed":
        # A next immutable fact may have published before the progress update.
        # Preserve that original prefix in failure evidence; state alone is
        # never publication proof. Missing real artifacts remain blockers.
        seen_missing = False
        present = 0
        for key in _HASH_FIELDS:
            if value[key] is None:
                seen_missing = True
            elif seen_missing:
                _fail("STATE_HASH_INVALID")
            else:
                present += 1
        if present < threshold:
            _fail("STATE_HASH_INVALID")
        threshold = present
    for index, key in enumerate(_HASH_FIELDS, 1):
        if index <= threshold:
            _sha(value[key])
        elif value[key] is not None:
            _fail("STATE_HASH_INVALID")
    if request is not None:
        _request(request)
        _same(value["operation_id"], request["operation_id"], "OPERATION_CONFLICT")
        _same(value["request_sha256"], digest(request), "REQUEST_CHANGED")
    for artifact, key in ((selection, "selection_sha256"), (source_verified, "source_verified_sha256"),
                          (manifest, "manifest_sha256"), (receipt, "receipt_sha256")):
        if artifact is not None:
            _same(artifact["operation_id"], value["operation_id"], "OPERATION_CONFLICT")
            _same(value[key], digest(artifact), "STATE_ARTIFACT_CHANGED")
    # Supplied artifacts are facts to validate, not live publication authority.
    if selection is not None:
        _selection(selection, request)
    if source_verified is not None:
        if selection is None:
            _fail("STATE_ARTIFACT_REQUIRED")
        hashes = (_records([item["copy"] for item in manifest["files"]], selection) if manifest is not None else
                  _hash_index(source_verified["copy_record_sha256s"], selection))
        _verified(source_verified, selection, hashes)
    if manifest is not None:
        if selection is None or source_verified is None:
            _fail("STATE_ARTIFACT_REQUIRED")
        _manifest(manifest, selection, source_verified, [item["copy"] for item in manifest["files"]], request)
    if receipt is not None:
        if manifest is None or selection is None or source_verified is None:
            _fail("STATE_ARTIFACT_REQUIRED")
        validate_receipt(receipt, manifest=manifest, selection=selection, source_verified=source_verified, request=request)
    return deepcopy(value)
