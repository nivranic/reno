"""Pure RP1 outer records and byte graphs, never native completion capabilities.

The two byte namespaces are intentionally separate. Partial target packages
establish only the supplied local declarations; complete R01 history is checked
only with the actual complete raw2 fact set for that same target attempt.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import ntpath
import posixpath
import re

from . import personal_authority_preservation_contract as census
from . import personal_authority_replica_contract as replica
from . import personal_authority_replica_policy as policy
from . import personal_authority_raw_preservation2_contract as raw2
from . import personal_authority_resume_policy as old_policy
from . import personal_authority_source_projection as projection


FORMAT_VERSION = 1
SCOPE = "raw2-replica-runtime-v1"
DIRECTORY = ".reno-authority-raw-preservation-replicas-v1"
_GIB = 1024**3
_MAX_JSON = 128 * 1024**2
_MAX_REQUEST = 1024**2
_PACKAGE_BYTES = 128 * _GIB
_ORIGINAL_FILE_BYTES = 64 * _GIB
_WRITE_BYTES = 256 * _GIB
_READ_BYTES = 2 * 1024**4
_SOURCE_READ_BYTES = 1024**4
_MAX_ENTRIES = 400_064
_MAX_FACTS = 300_032
_MAX_OUTER_FACTS = 2 * (2 * _MAX_ENTRIES + 8) + 1
_MAX_OPERATION_ENTRIES = 6 * _MAX_ENTRIES + 256
_U64 = 2**63 - 1
_CHUNK = 131_072
_FAILURE_BYTES = 16_384
_DEADLINE_NS = 1_800_000_000_000
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID = re.compile(_UUID_TEXT + r"\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_PID = re.compile(r"p[0-9]{8}\Z")
_FID = re.compile(r"f[0-9]{8}\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)
_COPY_ATTEMPT = re.compile(rf"attempts/(01|02)/copy-attempts/({_UUID_TEXT})\.json\Z")
_COPY_RECORD = re.compile(r"attempts/(01|02)/copy-records/(p[0-9]{8})\.json\Z")
_TEMP_NAME = re.compile(rf"\.write-({_UUID_TEXT})\.tmp\Z")
_FAILURE = re.compile(rf"attempts/(01|02)/failures/({_UUID_TEXT})\.json\Z")
_OLD_COPY_ATTEMPT = re.compile(rf"runs/(01|02)/copy-attempts/({_UUID_TEXT})\.json\Z")
_OLD_COPY_RECORD = re.compile(r"runs/(01|02)/copy-records/(f[0-9]{8})\.json\Z")
_OLD_PAYLOAD = re.compile(rf"runs/(01|02)/payload/({_UUID_TEXT})/(f[0-9]{{8}})\.raw\Z")
_CAPS = (("request", _MAX_REQUEST), ("attempt", 16_384),
    ("source_selection", _MAX_JSON), ("copy_attempt", 8192),
    ("copy_record", 16_384), ("replica", _MAX_JSON),
    ("source_closed", 16_384), ("target_closed", 16_384),
    ("manifest", _MAX_JSON), ("receipt", 16_384), ("failure", 16_384))
_LOCAL_FILES = (("attempt.json", "attempt"), ("source-selection.json", "source_selection"),
    ("replica.json", "replica"), ("source-closed.json", "source_closed"),
    ("target-closed.json", "target_closed"), ("manifest.json", "manifest"), ("receipt.json", "receipt"))
_H0 = frozenset({"format_version", "kind", "scope", "operation_id"})
_H = _H0 | {"slot", "run_id", "request_sha256"}
_D = frozenset({"device", "inode"})
_F = _D | {"byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
_DIR = frozenset({"relative_key", "identity", "children"})
_FILE = frozenset({"relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"})
_REF = frozenset({"relative_key", "sha256", "byte_size"})
_ORIGIN_HASHES = frozenset({"request_sha256", "selection_sha256", "source_metadata_sha256",
    "source_projection_sha256", "capsule_sha256", "source_verified_sha256", "manifest_sha256",
    "receipt_sha256", "fact_set_sha256"})
_SOURCE_MAP = frozenset({"source_entry_id", "origin_slot", "origin_run_id", "copy_record_relative_key",
    "copy_record_sha256", "payload_relative_key", "sha256", "byte_size"})
_CONCLUSIONS = (("evidence_kind", "declaration"), ("historical_factory_return", "unknown"),
    ("unrecorded_history_completeness", "not_established"), ("native_observation", False),
    ("data_restored", False), ("runtime_write", False), ("sender_authority", False))
_IO_KEYS = ("read_bytes", "source_read_bytes", "read_calls", "source_read_calls", "seek_calls",
    "confirmed_write_bytes", "write_calls", "uncertain_read_calls", "uncertain_source_read_calls", "uncertain_write_bytes")
_SNAPSHOT_KEYS = frozenset(_IO_KEYS) | {"elapsed_ns", "current_resources", "peak_resources",
    "pending_resources", "uncertain_resource_acquires"}
_CUMULATIVE_KEYS = _SNAPSHOT_KEYS - {"current_resources", "pending_resources"}
_SOURCE_COUNTERS = ("source_read_bytes", "source_read_calls", "uncertain_source_read_calls")
_READ_PARTS = ("package_read_bytes", "outer_control_read_bytes", "observer_node_read_bytes", "other_native_control_read_bytes")
_UNCERTAIN = ("uncertain_read_calls", "uncertain_source_read_calls", "uncertain_write_bytes", "uncertain_resource_acquires")
_PHASES = frozenset({"opening", "admission", "bootstrap_request", "previous_attempt", "request_bound",
    "source_metadata", "source_captured", "selection_published", "copying", "package_readback",
    "source_closing", "target_closing", "publishing", "publisher_closing", "operation_closing", "failed_cleanup"})
_FAILURE_CODES = frozenset({"CANCELLED", "IO_ERROR", "DEADLINE", "READ_LIMIT", "SOURCE_READ_LIMIT",
    "WRITE_LIMIT", "RESOURCE_LIMIT", "IDENTITY_CHANGED", "UNSAFE_PATH", "PRIVATE_REQUIRED",
    "CONTENT_CHANGED", "REENTRANT", "TICKET_INVALID", "UNSETTLED", "CONTRACT_INVALID", "OTHER_ORIGINAL_FAILURE"})


class AuthorityReplicaRuntimeContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_RUNTIME1_" + suffix
        super().__init__(self.code)


Error = AuthorityReplicaRuntimeContractError


def _fail(suffix):
    raise Error(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Error:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError):
            _fail("TYPE_INVALID")
    return checked


def _exact(value, fields):
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if len(value) != len(fields):
        _fail("FIELDS_INVALID")
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    if set(value) != fields:
        _fail("FIELDS_INVALID")


def _same(value, expected, suffix="LINK_INVALID"):
    if type(value) is not type(expected):
        _fail("TYPE_INVALID")
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail("TYPE_INVALID")
        if set(value) != set(expected):
            _fail(suffix)
        for key in expected:
            _same(value[key], expected[key], suffix)
        return
    if type(value) in (list, tuple):
        if len(value) != len(expected):
            _fail(suffix)
        for item, wanted in zip(value, expected):
            _same(item, wanted, suffix)
        return
    if value != expected:
        _fail(suffix)


def _u(value, maximum=_U64, *, positive=False):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if not int(positive) <= value <= maximum:
        _fail("LIMIT")
    return value


def _enum(value, choices, suffix="FIELDS_INVALID"):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    if value not in choices:
        _fail(suffix)
    return value


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("TYPE_INVALID")
    return value


def _uuid(value):
    if type(value) is not str or not _UUID.fullmatch(value) or value == "00000000-0000-0000-0000-000000000000":
        _fail("TYPE_INVALID")
    return value


def _slot(value):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if value not in (1, 2):
        _fail("ADMISSION_INVALID")
    return value


def _identifier(value, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        _fail("TYPE_INVALID")
    return value


def _cap(kind):
    if type(kind) is not str:
        _fail("TYPE_INVALID")
    for name, maximum in _CAPS:
        if name == kind:
            return maximum
    _fail("KIND_INVALID")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _dump(value, maximum=_MAX_JSON):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, OverflowError):
        _fail("JSON_INVALID")
    if len(raw) > maximum:
        _fail("LIMIT")
    return raw


def _json_snapshot(value, maximum=_MAX_JSON):
    nodes = 0
    text_bytes = 0
    active = set()

    def visit(item, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        if nodes > 4_000_000 or depth > 64:
            _fail("LIMIT")
        kind = type(item)
        if item is None or kind is bool:
            return item
        if kind is str:
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeError:
                _fail("JSON_INVALID")
            if text_bytes > maximum:
                _fail("LIMIT")
            return item
        if kind is int:
            if item.bit_length() > 14_285:
                _fail("LIMIT")
            try:
                digits = len(str(item).lstrip("-"))
            except ValueError:
                _fail("LIMIT")
            if digits > 4300:
                _fail("LIMIT")
            return item
        if kind not in (list, dict):
            _fail("TYPE_INVALID")
        if id(item) in active:
            _fail("JSON_INVALID")
        active.add(id(item))
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in item]
            if any(type(key) is not str for key in item):
                _fail("TYPE_INVALID")
            return {visit(key, depth + 1): visit(child, depth + 1) for key, child in item.items()}
        finally:
            active.remove(id(item))

    copied = visit(value, 0)
    _dump(copied, maximum)
    return copied


def _prepare_raw(raw, maximum):
    """Bound bytes and JSON structure before allocating the parsed graph."""
    if type(raw) is not bytes:
        _fail("TYPE_INVALID")
    if len(raw) > maximum:
        _fail("LIMIT")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail("JSON_INVALID")
    stack, nodes, index, length = [], 0, 0, len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n:,":
            index += 1
            continue
        if char in "}]":
            if not stack or stack.pop() != ("{" if char == "}" else "["):
                _fail("JSON_INVALID")
            index += 1
            continue
        nodes += 1
        if nodes > 4_000_000 or len(stack) > 64:
            _fail("LIMIT")
        if char in "{[":
            stack.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            while index < length:
                char = text[index]
                if char == '"':
                    index += 1
                    break
                if ord(char) < 32:
                    _fail("JSON_INVALID")
                if char == "\\":
                    index += 1
                    if index >= length:
                        _fail("JSON_INVALID")
                    escape = text[index]
                    if escape == "u":
                        digits = text[index + 1:index + 5]
                        if len(digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                            _fail("JSON_INVALID")
                        index += 5
                        continue
                    if escape not in '\\"/bfnrt':
                        _fail("JSON_INVALID")
                index += 1
            else:
                _fail("JSON_INVALID")
            continue
        if char in "tfn":
            token = "true" if char == "t" else "false" if char == "f" else "null"
            if not text.startswith(token, index):
                _fail("JSON_INVALID")
            index += len(token)
            continue
        if char == "-" or "0" <= char <= "9":
            start, digits_seen = index, 0
            while index < length and text[index] in "-+0123456789.eE":
                if "0" <= text[index] <= "9":
                    digits_seen += 1
                    if digits_seen > 4300:
                        _fail("LIMIT")
                index += 1
            token = text[start:index]
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", token):
                _fail("JSON_INVALID")
            if len(token.lstrip("-")) > 4300:
                _fail("LIMIT")
            continue
        _fail("JSON_INVALID")
    if stack:
        _fail("JSON_INVALID")
    return text


def _parse_prepared(raw, text, maximum):

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _fail("JSON_INVALID")
            result[key] = value
        return result

    def integer(text):
        if len(text.lstrip("-")) > 4300:
            _fail("LIMIT")
        return int(text)

    def invalid(_value):
        _fail("JSON_INVALID")

    try:
        parsed = json.loads(text, object_pairs_hook=pairs,
            parse_int=integer, parse_float=invalid, parse_constant=invalid)
    except Error:
        raise
    except (UnicodeError, ValueError, RecursionError):
        _fail("JSON_INVALID")
    copied = _json_snapshot(parsed, maximum)
    if _dump(copied, maximum) != raw:
        _fail("NOT_CANONICAL")
    return copied


def _parse(raw, maximum):
    return _parse_prepared(raw, _prepare_raw(raw, maximum), maximum)


def _dh(domain, value):
    if domain not in ("package-entry-set", "copy-record-set", "control-dependencies", "resource-events"):
        _fail("FIELDS_INVALID")
    return _digest(b"reno-authority-replica-runtime-v1\0" + domain.encode("ascii") + b"\0" + _dump(value))


def _name(value):
    if type(value) is not str or not value or value in (".", ".."):
        _fail("PATH_INVALID")
    if any(char in '/\\<>:"|?*' or ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        _fail("PATH_INVALID")
    if value.endswith((".", " ")) or _RESERVED.fullmatch(value):
        _fail("PATH_INVALID")
    if len(value.encode("utf-8")) > 255:
        _fail("LIMIT")
    return value


def _key(value, *, root=False):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    if root and value == "":
        return value
    if not value or len(value.encode("utf-8")) > 32768:
        _fail("PATH_INVALID")
    parts = value.split("/")
    if len(parts) > 128:
        _fail("LIMIT")
    for part in parts:
        _name(part)
    return value


def _absolute(value):
    if type(value) is not str or len(value.encode("utf-8")) > 32768:
        _fail("PATH_INVALID")
    if len(value) >= 3 and value[1:3] == ":\\" and "a" <= value[0] <= "z":
        module, anchor, tail = ntpath, value[:3], value[3:]
        if ntpath.normcase(ntpath.normpath(value)) != value:
            _fail("PATH_INVALID")
        parts = [] if not tail else tail.split("\\")
    elif value.startswith("/") and not value.startswith("//"):
        module, anchor, tail = posixpath, "/", value[1:]
        if posixpath.normpath(value) != value:
            _fail("PATH_INVALID")
        parts = [] if not tail else tail.split("/")
    else:
        _fail("PATH_INVALID")
    if len(parts) > 128:
        _fail("LIMIT")
    for part in parts:
        _name(part)
    return module, anchor, parts


def _decimal(value, *, positive=False):
    if type(value) is not str or len(value) > 40 or not _DECIMAL.fullmatch(value) or positive and value == "0":
        _fail("IDENTITY_INVALID")


def _directory(value):
    _exact(value, _D)
    _decimal(value["device"])
    _decimal(value["inode"], positive=True)
    return value["device"], value["inode"]


def _file_identity(value, maximum=_WRITE_BYTES):
    _exact(value, _F)
    for key in ("device", "inode", "mtime_ns", "ctime_ns"):
        _decimal(value[key], positive=key == "inode")
    _u(value["byte_size"], maximum)
    _u(value["mode"], 2**32 - 1)
    _same(value["nlink"], 1, "IDENTITY_INVALID")
    if value["mode"] & 0o170000 != 0o100000:
        _fail("IDENTITY_INVALID")
    return value["device"], value["inode"]


def _file_pair(path, handle, size, maximum=_WRITE_BYTES):
    identity = _file_identity(path, maximum)
    _file_identity(handle, maximum)
    for key in _F - {"ctime_ns"}:
        _same(path[key], handle[key], "IDENTITY_INVALID")
    _same(path["byte_size"], size, "IDENTITY_INVALID")
    return identity


def _ordered(rows, fields, *, key="relative_key", maximum=_MAX_ENTRIES):
    if type(rows) is not list:
        _fail("TYPE_INVALID")
    if len(rows) > maximum:
        _fail("LIMIT")
    previous = None
    for row in rows:
        _exact(row, fields)
        if type(row[key]) is not str:
            _fail("TYPE_INVALID")
        encoded = row[key].encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail("ORDER_INVALID")
        previous = encoded
    return rows


def _file_row(row, maximum=_WRITE_BYTES):
    _exact(row, _FILE)
    _key(row["relative_key"])
    _directory(row["parent_identity"])
    _u(row["byte_size"], maximum)
    _sha(row["sha256"])
    return _file_pair(row["path_identity"], row["handle_identity"], row["byte_size"], maximum)


def _inventory(value, *, maximum=_MAX_ENTRIES, file_maximum=_ORIGINAL_FILE_BYTES, total_maximum=_PACKAGE_BYTES):
    _exact(value, {"directories", "files"})
    dirs = _ordered(value["directories"], _DIR, maximum=maximum)
    files = _ordered(value["files"], _FILE, maximum=maximum)
    if len(dirs) + len(files) > maximum or not dirs:
        _fail("LIMIT")
    by_dir, by_file, identities = {}, {}, set()
    for row in dirs:
        key = _key(row["relative_key"], root=True)
        identity = _directory(row["identity"])
        if identity in identities:
            _fail("DUPLICATE_IDENTITY")
        identities.add(identity)
        if type(row["children"]) is not list or len(row["children"]) > maximum:
            _fail("TYPE_INVALID")
        previous, folded = None, set()
        for name in row["children"]:
            _name(name)
            encoded = name.encode("utf-8")
            if previous is not None and encoded <= previous or name.casefold() in folded:
                _fail("ORDER_INVALID")
            previous = encoded
            folded.add(name.casefold())
        by_dir[key] = row
    if "" not in by_dir:
        _fail("INVENTORY_INVALID")
    total = 0
    for row in files:
        identity = _file_row(row, file_maximum)
        if identity in identities:
            _fail("DUPLICATE_IDENTITY")
        identities.add(identity)
        if row["relative_key"] in by_dir:
            _fail("INVENTORY_INVALID")
        by_file[row["relative_key"]] = row
        total += row["byte_size"]
        if total > total_maximum:
            _fail("LIMIT")
    children = {key: [] for key in by_dir}
    for key in list(by_dir) + list(by_file):
        if key == "":
            continue
        parent = posixpath.dirname(key)
        if parent not in by_dir:
            _fail("INVENTORY_INVALID")
        children[parent].append(posixpath.basename(key))
        if key in by_file:
            _same(by_file[key]["parent_identity"], by_dir[parent]["identity"], "IDENTITY_INVALID")
    for key, row in by_dir.items():
        _same(row["children"], sorted(children[key], key=lambda item: item.encode("utf-8")), "INVENTORY_INVALID")
    return by_dir, by_file


def _ref(value, *, zero=False, maximum=_WRITE_BYTES):
    _exact(value, _REF)
    _key(value["relative_key"])
    _sha(value["sha256"])
    _u(value["byte_size"], maximum, positive=not zero)


def _refs(value, *, maximum=_MAX_OUTER_FACTS, zero=False, byte_maximum=_WRITE_BYTES):
    for row in _ordered(value, _REF, maximum=maximum):
        _ref(row, zero=zero, maximum=byte_maximum)


def _profile(value):
    _exact(value, {"profile_paths", "profile_sha256", "roots"})
    paths = value["profile_paths"]
    if type(paths) is not list or len(paths) != 4:
        _fail("FIELDS_INVALID")
    parsed = [_absolute(path) for path in paths]
    if any(item[0] is not parsed[0][0] for item in parsed):
        _fail("PATH_INVALID")
    _sha(value["profile_sha256"])
    _same(value["profile_sha256"], _digest(_dump(paths)), "CONTENT_CHANGED")
    _exact(value["roots"], {"data", "media", "reports", "checkpoints"})
    actual_paths = [parsed[0][0].dirname(paths[0]), *paths[1:]]
    seen = {}
    for name, path in zip(("data", "media", "reports", "checkpoints"), actual_paths):
        identity = _directory(value["roots"][name])
        if path in seen and seen[path] != identity:
            _fail("IDENTITY_INVALID")
        seen[path] = identity


def _node(value):
    _exact(value, {"node_id", "sha256", "byte_size", "parent_identity", "path_identity", "handle_identity"})
    _uuid(value["node_id"])
    _sha(value["sha256"])
    _u(value["byte_size"], 4096, positive=True)
    parent = _directory(value["parent_identity"])
    identity = _file_pair(value["path_identity"], value["handle_identity"], value["byte_size"], 4096)
    if identity == parent:
        _fail("DUPLICATE_IDENTITY")
    return identity


def _target(value):
    _exact(value, {"profile", "node", "namespace_identity", "operation_identity"})
    _profile(value["profile"])
    node = _node(value["node"])
    namespace = _directory(value["namespace_identity"])
    operation = _directory(value["operation_identity"])
    roots = {_directory(row) for row in value["profile"]["roots"].values()}
    if namespace == operation or namespace in roots or operation in roots or node in {namespace, operation}:
        _fail("DUPLICATE_IDENTITY")
    _same(value["node"]["parent_identity"], value["profile"]["roots"]["data"], "IDENTITY_INVALID")


def _source(value):
    _exact(value, {"source_kind", "package_root", "generation", "expected_request_sha256", "root_identity",
        "ancestors", "profile", "original_operation_id"})
    _enum(value["source_kind"], ("profile_raw2", "detached_raw2"), "SOURCE_INVALID")
    module, anchor, parts = _absolute(value["package_root"])
    _slot(value["generation"])
    _sha(value["expected_request_sha256"])
    _directory(value["root_identity"])
    rows = value["ancestors"]
    if type(rows) is not list or len(rows) != len(parts) + 1:
        _fail("SOURCE_INVALID")
    expected, identities = anchor, set()
    for index, row in enumerate(rows):
        _exact(row, {"absolute_path", "identity"})
        if index:
            expected = module.join(expected, parts[index - 1])
        _same(row["absolute_path"], expected, "PATH_INVALID")
        identity = _directory(row["identity"])
        if identity in identities:
            _fail("DUPLICATE_IDENTITY")
        identities.add(identity)
    _same(rows[-1]["identity"], value["root_identity"], "IDENTITY_INVALID")
    if value["source_kind"] == "detached_raw2":
        _same(value["profile"], None, "SOURCE_INVALID")
        _same(value["original_operation_id"], None, "SOURCE_INVALID")
    else:
        _profile(value["profile"])
        _uuid(value["original_operation_id"])
        if _absolute(value["profile"]["profile_paths"][0])[0] is not module:
            _fail("PATH_INVALID")
        wanted = module.join(module.dirname(value["profile"]["profile_paths"][0]),
            ".reno-authority-raw-preservation-v2", value["original_operation_id"])
        _same(value["package_root"], wanted, "SOURCE_INVALID")


def _header(value, kind, fields):
    base = _H0 if kind == "request" else _H
    _exact(value, base | set(fields))
    _same(value["format_version"], 1, "VERSION_INVALID")
    _same(value["kind"], "authority_raw_replica_runtime_" + kind, "KIND_INVALID")
    _same(value["scope"], SCOPE, "VERSION_INVALID")
    _uuid(value["operation_id"])
    if kind != "request":
        _slot(value["slot"])
        _uuid(value["run_id"])
        _sha(value["request_sha256"])


def _r01_local(value):
    _exact(value, {"format_version", "kind", "scope", "replica_id", "origin", "current", "package_map", "source_map", "conclusions"})
    _same(value["format_version"], 1, "VERSION_INVALID")
    _same(value["kind"], "personal_authority_raw_replica", "KIND_INVALID")
    _same(value["scope"], "raw2-replica-declaration-v1", "VERSION_INVALID")
    _uuid(value["replica_id"])
    origin = value["origin"]
    _exact(origin, _ORIGIN_HASHES | {"format_version", "family", "operation_id", "generation"})
    _same(origin["format_version"], 2, "VERSION_INVALID")
    _same(origin["family"], ".reno-authority-raw-preservation-v2", "VERSION_INVALID")
    _uuid(origin["operation_id"])
    _slot(origin["generation"])
    for key in _ORIGIN_HASHES:
        _sha(origin[key])
    current = value["current"]
    _exact(current, {"observation_scope_id", "profile_sha256", "node_id", "directories", "files", "inventory_sha256"})
    _uuid(current["observation_scope_id"])
    _sha(current["profile_sha256"])
    _uuid(current["node_id"])
    inventory = {key: current[key] for key in ("directories", "files")}
    _inventory(inventory)
    _sha(current["inventory_sha256"])
    _same(current["inventory_sha256"], _digest(_dump(inventory)), "CONTENT_CHANGED")
    package = value["package_map"]
    _exact(package, {"mapping", "directories", "files", "content_set_sha256"})
    _same(package["mapping"], "same-relative-key", "FIELDS_INVALID")
    _same(package["directories"], [row["relative_key"] for row in current["directories"]], "INVENTORY_INVALID")
    rows = _ordered(package["files"], {"relative_key", "role", "sha256", "byte_size"})
    if len(rows) != len(current["files"]):
        _fail("INVENTORY_INVALID")
    for row, actual in zip(rows, current["files"]):
        _key(row["relative_key"])
        _enum(row["role"], ("control", "marker", "payload", "retained"))
        _sha(row["sha256"])
        _u(row["byte_size"], _ORIGINAL_FILE_BYTES)
        for key in ("relative_key", "sha256", "byte_size"):
            _same(row[key], actual[key], "INVENTORY_INVALID")
    _sha(package["content_set_sha256"])
    _same(package["content_set_sha256"], _digest(_dump({key: package[key] for key in ("mapping", "directories", "files")})), "CONTENT_CHANGED")
    for row in _ordered(value["source_map"], _SOURCE_MAP, key="source_entry_id", maximum=100_000):
        _identifier(row["source_entry_id"], _FID)
        _slot(row["origin_slot"])
        _uuid(row["origin_run_id"])
        _key(row["copy_record_relative_key"])
        _key(row["payload_relative_key"])
        _sha(row["copy_record_sha256"])
        _sha(row["sha256"])
        _u(row["byte_size"], _ORIGINAL_FILE_BYTES)
    _exact(value["conclusions"], {key for key, _ in _CONCLUSIONS})
    for key, expected in _CONCLUSIONS:
        _same(value["conclusions"][key], expected, "FIELDS_INVALID")


def _control_refs(declaration):
    return [{key: row[key] for key in ("relative_key", "sha256", "byte_size")}
        for row in declaration["package_map"]["files"] if row["role"] == "control"]


def _entry_digest(declaration):
    current = declaration["current"]
    return _dh("package-entry-set", {"directories": [[f"d{index:08d}", row] for index, row in enumerate(current["directories"])],
        "files": [[f"p{index:08d}", row] for index, row in enumerate(current["files"])]})


def _ledger_snapshot(value, *, normal=True):
    _exact(value, _SNAPSHOT_KEYS)
    for count in value.values():
        _u(count)
    for child, parent in (("source_read_bytes", "read_bytes"), ("source_read_calls", "read_calls"),
                          ("uncertain_source_read_calls", "uncertain_read_calls")):
        if value[child] > value[parent]:
            _fail("COUNTERS_INVALID")
    if value["current_resources"] > value["peak_resources"]:
        _fail("COUNTERS_INVALID")
    if normal and (any(value[key] for key in _UNCERTAIN) or value["pending_resources"]
            or value["peak_resources"] > 1_000_000 or value["read_bytes"] > _READ_BYTES
            or value["source_read_bytes"] > _SOURCE_READ_BYTES or value["confirmed_write_bytes"] > _WRITE_BYTES
            or value["elapsed_ns"] >= _DEADLINE_NS):
        _fail("COUNTERS_INVALID")


def _monotone(before, after):
    if any(before[key] > after[key] for key in _CUMULATIVE_KEYS):
        _fail("COUNTERS_INVALID")


def _scope_counters(value):
    _exact(value, {"scope", "scope_id", "begin", "end", "attributable"})
    _enum(value["scope"], ("source", "target"))
    _uuid(value["scope_id"])
    _ledger_snapshot(value["begin"])
    _ledger_snapshot(value["end"])
    _monotone(value["begin"], value["end"])
    counts = value["attributable"]
    _exact(counts, set(_IO_KEYS) | set(_READ_PARTS))
    for count in counts.values():
        _u(count)
    if counts["read_bytes"] != sum(counts[key] for key in _READ_PARTS):
        _fail("COUNTERS_INVALID")
    for key in _IO_KEYS:
        if counts[key] > value["end"][key] - value["begin"][key]:
            _fail("COUNTERS_INVALID")
    if any(counts[key] for key in _UNCERTAIN if key in counts):
        _fail("COUNTERS_INVALID")
    if counts["source_read_bytes"] > counts["read_bytes"] or counts["source_read_calls"] > counts["read_calls"]:
        _fail("COUNTERS_INVALID")
    for calls, size in (("read_calls", "read_bytes"), ("source_read_calls", "source_read_bytes"), ("write_calls", "confirmed_write_bytes")):
        if counts[calls] < (counts[size] + _CHUNK - 1) // _CHUNK:
            _fail("COUNTERS_INVALID")
    if value["scope"] == "source":
        _same(counts["source_read_bytes"], counts["package_read_bytes"], "COUNTERS_INVALID")
        for key in _SOURCE_COUNTERS:
            _same(value["begin"][key], 0, "COUNTERS_INVALID")
            _same(value["end"][key], counts[key], "COUNTERS_INVALID")
    else:
        _same(counts["source_read_bytes"], 0, "COUNTERS_INVALID")


def _close_summary(value):
    _exact(value, {"scope", "scope_id", "bound_inventory_sha256", "resource_acquires", "resource_normal_closes",
        "peak_owned_resources", "remaining_owned_resources", "pending_resource_acquires", "unknown_resource_acquires",
        "unknown_closes", "resource_events_sha256", "normal_closed"})
    _enum(value["scope"], ("source", "target"))
    _uuid(value["scope_id"])
    _sha(value["bound_inventory_sha256"])
    _sha(value["resource_events_sha256"])
    _u(value["resource_acquires"], 100_000_000)
    _same(value["resource_normal_closes"], value["resource_acquires"], "COUNTERS_INVALID")
    _u(value["peak_owned_resources"], min(value["resource_acquires"], 1_000_000))
    for key in ("remaining_owned_resources", "pending_resource_acquires", "unknown_resource_acquires", "unknown_closes"):
        _same(value[key], 0, "COUNTERS_INVALID")
    _same(value["normal_closed"], True, "COUNTERS_INVALID")


def _payload_gate(value):
    _exact(value, {"selection", "demand", "snapshot"})
    try:
        expected = policy.payload_demand(value["selection"])
        policy.normalize_demand(value["demand"])
    except policy.AuthorityReplicaPolicyError:
        _fail("BUDGET_INVALID")
    _same(value["demand"], expected, "BUDGET_INVALID")
    _ledger_snapshot(value["snapshot"])
    snap = value["snapshot"]
    if (snap["source_read_bytes"] < value["selection"]["package_bytes"]
            or snap["read_bytes"] + expected["read_bytes"] > _READ_BYTES
            or snap["source_read_bytes"] + expected["source_read_bytes"] > _SOURCE_READ_BYTES
            or snap["confirmed_write_bytes"] + expected["write_bytes"] > _WRITE_BYTES
            or snap["current_resources"] + expected["peak_additional_resources"] > 1_000_000):
        _fail("BUDGET_INVALID")


def _publication_budget(value):
    _exact(value, {"checkpoint", "inputs", "remaining_capacity"})
    _ledger_snapshot(value["checkpoint"])
    values = value["inputs"]
    _exact(values, {"package_bytes", "prior_control_bytes", "observer_node_bytes"})
    _u(values["package_bytes"], _PACKAGE_BYTES)
    _u(values["prior_control_bytes"])
    _u(values["observer_node_bytes"], 4096, positive=True)
    remaining = {"read_bytes": 2 * values["package_bytes"] + 2 * values["prior_control_bytes"]
        + 4 * _MAX_JSON + 4 * 16_384 + 2 * values["observer_node_bytes"] + _CHUNK,
        "source_read_bytes": 0, "write_bytes": _MAX_JSON + 16_384 + _FAILURE_BYTES}
    _exact(value["remaining_capacity"], set(remaining))
    for count in value["remaining_capacity"].values():
        _u(count)
    _same(value["remaining_capacity"], remaining, "BUDGET_INVALID")
    snap = value["checkpoint"]
    if (snap["read_bytes"] + remaining["read_bytes"] > _READ_BYTES
            or snap["confirmed_write_bytes"] + remaining["write_bytes"] > _WRITE_BYTES):
        _fail("BUDGET_INVALID")


def _marker(value, path):
    _file_row(value)
    _same(value["relative_key"], path, "CLAIM_INVALID")
    _same(value["byte_size"], 0, "CLAIM_INVALID")
    _same(value["sha256"], _EMPTY_SHA, "CLAIM_INVALID")


def _local(kind, value):
    if kind == "replica":
        _r01_local(value)
        return
    if kind == "request":
        _header(value, kind, {"creation_run_id", "policy", "target", "source", "expected_content_set_sha256"})
        _uuid(value["creation_run_id"])
        try:
            policy.normalize_rp1(value["policy"])
        except policy.AuthorityReplicaPolicyError:
            _fail("BUDGET_INVALID")
        _target(value["target"])
        _source(value["source"])
        _sha(value["expected_content_set_sha256"])
    elif kind == "attempt":
        _header(value, kind, {"admission", "anchor", "reservation", "predecessor"})
        slot = value["slot"]
        _same(value["reservation"], policy.slot_reservation(slot), "BUDGET_INVALID")
        anchor = value["anchor"]
        _exact(anchor, {"relative_key", "parent_identity", "identity", "children"})
        _same(anchor["relative_key"], "admission-rp1-sha256-" + value["request_sha256"], "ANCHOR_INVALID")
        _directory(anchor["parent_identity"])
        _directory(anchor["identity"])
        _same(anchor["children"], [], "ANCHOR_INVALID")
        admission = value["admission"]
        _exact(admission, {"method", "occupied_slots", "allocation", "resume_claim"})
        _same(admission["occupied_slots"], list(range(1, slot + 1)), "ADMISSION_INVALID")
        _same(admission["allocation"], policy.allocation(tuple(range(1, slot + 1))), "BUDGET_INVALID")
        if slot == 1:
            _same(admission["method"], "new_operation", "ADMISSION_INVALID")
            _same(admission["resume_claim"], None, "ADMISSION_INVALID")
            _same(value["predecessor"], {"state": "none"}, "ADMISSION_INVALID")
        else:
            _same(admission["method"], "fresh_second_attempt", "ADMISSION_INVALID")
            _marker(admission["resume_claim"], "resume-attempt-02.claim")
            predecessor = value["predecessor"]
            if type(predecessor) is not dict or any(type(key) is not str for key in predecessor):
                _fail("TYPE_INVALID")
            state = predecessor.get("state")
            if state == "not_published":
                _same(predecessor, {"state": "not_published", "slot": 1}, "ADMISSION_INVALID")
            elif state == "published":
                _exact(predecessor, {"state", "attempt_ref"})
                _ref(predecessor["attempt_ref"], maximum=16_384)
                _same(predecessor["attempt_ref"]["relative_key"], "attempts/01/attempt.json", "ADMISSION_INVALID")
            else:
                _fail("ADMISSION_INVALID")
    elif kind == "source_selection":
        _header(value, kind, {"source_location", "source_declaration", "source_declaration_sha256", "raw_control_refs", "package_entry_set_sha256"})
        _source(value["source_location"])
        _r01_local(value["source_declaration"])
        source, declaration = value["source_location"], value["source_declaration"]
        _same(declaration["replica_id"], value["operation_id"], "SOURCE_INVALID")
        _same(declaration["origin"]["generation"], source["generation"], "SOURCE_INVALID")
        _same(declaration["origin"]["request_sha256"], source["expected_request_sha256"], "SOURCE_INVALID")
        _same(declaration["current"]["directories"][0]["identity"], source["root_identity"], "IDENTITY_INVALID")
        if source["source_kind"] == "profile_raw2":
            _same(declaration["origin"]["operation_id"], source["original_operation_id"], "SOURCE_INVALID")
        _sha(value["source_declaration_sha256"])
        _same(value["source_declaration_sha256"], _digest(_dump(value["source_declaration"])), "CONTENT_CHANGED")
        _refs(value["raw_control_refs"], maximum=_MAX_FACTS, byte_maximum=_MAX_JSON)
        _same(value["raw_control_refs"], _control_refs(value["source_declaration"]), "FACT_SET_INVALID")
        _sha(value["package_entry_set_sha256"])
        _same(value["package_entry_set_sha256"], _entry_digest(value["source_declaration"]), "CONTENT_CHANGED")
    elif kind == "copy_attempt":
        _header(value, kind, {"selection_ref", "package_entry_id", "copy_id", "relative_key", "source_file_sha256", "source_byte_size", "target_parent_identity"})
        _ref(value["selection_ref"], maximum=_MAX_JSON)
        _identifier(value["package_entry_id"], _PID)
        _uuid(value["copy_id"])
        _key(value["relative_key"])
        _sha(value["source_file_sha256"])
        _u(value["source_byte_size"], _ORIGINAL_FILE_BYTES)
        _directory(value["target_parent_identity"])
    elif kind == "copy_record":
        _header(value, kind, {"copy_attempt_ref", "selection_ref", "package_entry_id", "relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"})
        _ref(value["copy_attempt_ref"], maximum=8192)
        _ref(value["selection_ref"], maximum=_MAX_JSON)
        _identifier(value["package_entry_id"], _PID)
        _file_row({key: value[key] for key in _FILE}, _ORIGINAL_FILE_BYTES)
    elif kind in ("source_closed", "target_closed"):
        fields = {"selection_ref", "scope_counters", "owned_close_summary"}
        fields |= {"content_set_sha256"} if kind == "source_closed" else {"replica_ref", "source_closed_ref", "copy_record_set_sha256"}
        _header(value, kind, fields)
        _ref(value["selection_ref"], maximum=_MAX_JSON)
        _scope_counters(value["scope_counters"])
        _close_summary(value["owned_close_summary"])
        scope = "source" if kind == "source_closed" else "target"
        for nested in (value["scope_counters"], value["owned_close_summary"]):
            _same(nested["scope"], scope, "COUNTERS_INVALID")
        _same(value["scope_counters"]["scope_id"], value["owned_close_summary"]["scope_id"], "COUNTERS_INVALID")
        if kind == "source_closed":
            _sha(value["content_set_sha256"])
        else:
            _ref(value["replica_ref"], maximum=_MAX_JSON)
            _ref(value["source_closed_ref"], maximum=16_384)
            _sha(value["copy_record_set_sha256"])
    elif kind == "manifest":
        _header(value, kind, {"selection_ref", "replica_ref", "source_closed_ref", "target_closed_ref", "control_refs",
            "copy_record_refs", "copy_record_set_sha256", "content_set_sha256", "dependency_set_sha256", "payload_gate", "publication_budget"})
        for key in ("selection_ref", "replica_ref", "source_closed_ref", "target_closed_ref"):
            _ref(value[key], maximum=_MAX_JSON if key in ("selection_ref", "replica_ref") else 16_384)
        _refs(value["control_refs"])
        _refs(value["copy_record_refs"])
        for ref in value["control_refs"]:
            _formal_ref(ref)
        for ref in value["copy_record_refs"]:
            _formal_ref(ref, kind="copy_record")
        for key in ("copy_record_set_sha256", "content_set_sha256", "dependency_set_sha256"):
            _sha(value[key])
        _same(value["copy_record_set_sha256"], _dh("copy-record-set", value["copy_record_refs"]), "CONTENT_CHANGED")
        _same(value["dependency_set_sha256"], _dh("control-dependencies", value["control_refs"]), "CONTENT_CHANGED")
        _payload_gate(value["payload_gate"])
        _publication_budget(value["publication_budget"])
    elif kind == "receipt":
        _header(value, kind, {"manifest_ref", "receipt_start_marker", "publication", "before_receipt"})
        _ref(value["manifest_ref"], maximum=_MAX_JSON)
        _marker(value["receipt_start_marker"], f"attempts/{value['slot']:02d}/receipt-started.claim")
        _same(value["publication"], "published_observed", "FIELDS_INVALID")
        _ledger_snapshot(value["before_receipt"])
    elif kind == "failure":
        _header(value, kind, {"phase", "code", "counters", "effects", "effects_complete"})
        _enum(value["phase"], _PHASES)
        _enum(value["code"], _FAILURE_CODES)
        _ledger_snapshot(value["counters"], normal=False)
        if type(value["effects_complete"]) is not bool or type(value["effects"]) is not list:
            _fail("TYPE_INVALID")
        if len(value["effects"]) > 16:
            _fail("LIMIT")
        for index, effect in enumerate(value["effects"]):
            _exact(effect, {"sequence", "operation", "relative_key", "outcome", "confirmed_byte_count", "resource_count"})
            _same(effect["sequence"], index + 1, "ORDER_INVALID")
            _enum(effect["operation"], ("mkdir", "open", "read", "write", "flush", "fsync", "rename", "close"))
            _enum(effect["outcome"], ("returned", "raised", "unknown"))
            if effect["relative_key"] is not None:
                _key(effect["relative_key"])
            diagnostic_path = f"attempts/{value['slot']:02d}/failures/{value['run_id']}.json"
            if effect["relative_key"] == diagnostic_path and effect["operation"] in ("write", "flush", "fsync", "close"):
                _fail("DAG_INVALID")
            for key in ("confirmed_byte_count", "resource_count"):
                if effect[key] is not None:
                    _u(effect[key])
    else:
        _fail("KIND_INVALID")


@_checked
def encode(kind: str, value: dict) -> bytes:
    """Encode local declarations only; no graph or native capability is returned."""
    maximum = _cap(kind)
    copied = _json_snapshot(value, maximum)
    _local(kind, copied)
    return _dump(copied, maximum)


@_checked
def decode(kind: str, raw: bytes) -> dict:
    """Decode a bounded canonical local declaration, not a completion fact."""
    value = _parse(raw, _cap(kind))
    _local(kind, value)
    return value


def _original(function, *args, **kwargs):
    """Use only fixed, existing pure validators; missing inputs are not hidden."""
    try:
        return function(*args, **kwargs)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError,
            UnicodeError, RecursionError, OverflowError):
        _fail("R01_INVALID")


def _old_header(value, kind, fields, origin, *, slot=None):
    header = {"format_version", "kind", "operation_id"}
    if slot is not None:
        header |= {"slot", "run_id"}
    _exact(value, header | set(fields))
    _same(value["format_version"], 2, "VERSION_INVALID")
    _same(value["kind"], "authority_raw_preservation_" + kind, "KIND_INVALID")
    _uuid(value["operation_id"])
    _same(value["operation_id"], origin["operation_id"], "SOURCE_INVALID")
    if slot is not None:
        _same(value["slot"], slot, "SOURCE_INVALID")
        _uuid(value["run_id"])
    for key in fields:
        if key.endswith("_sha256"):
            if key == "frozen_selection_sha256" and slot == 1 and value[key] is None:
                continue
            _sha(value[key])
    for key in ("request_sha256", "selection_sha256", "source_metadata_sha256", "source_projection_sha256"):
        if key in value:
            _sha(value[key])
            _same(value[key], origin[key], "SOURCE_INVALID")


def _old_selected(rows):
    fields = {"source_entry_id", "origin_slot", "origin_run_id", "copy_record_relative_key", "copy_record_sha256", "copy_record_byte_size"}
    for row in _ordered(rows, fields, key="source_entry_id", maximum=100_000):
        _identifier(row["source_entry_id"], _FID)
        _slot(row["origin_slot"])
        _uuid(row["origin_run_id"])
        _same(row["copy_record_relative_key"], f"runs/{row['origin_slot']:02d}/copy-records/{row['source_entry_id']}.json", "SOURCE_INVALID")
        _sha(row["copy_record_sha256"])
        _u(row["copy_record_byte_size"], _MAX_JSON, positive=True)


def _old_source_closed(value, origin):
    _exact(value, {"format_version", "kind", "operation_id", "source_projection_sha256", "source_metadata_sha256",
        "read_bytes", "read_calls", "seek_calls", "source_file_count", "source_byte_size"})
    _same(value["format_version"], 1, "VERSION_INVALID")
    _same(value["kind"], "personal_authority_raw_census_closed", "KIND_INVALID")
    _same(value["operation_id"], origin["operation_id"], "SOURCE_INVALID")
    for key in ("source_projection_sha256", "source_metadata_sha256"):
        _sha(value[key])
        _same(value[key], origin[key], "SOURCE_INVALID")
    _u(value["read_bytes"], 192 * _GIB)
    _u(value["read_calls"])
    _u(value["seek_calls"])
    _u(value["source_file_count"], 100_000)
    _u(value["source_byte_size"], _ORIGINAL_FILE_BYTES)
    if value["read_bytes"] < value["source_byte_size"] or value["source_file_count"] and (not value["read_calls"] or not value["seek_calls"]):
        _fail("COUNTERS_INVALID")


def _old_target_closed(value, origin, slot):
    _old_header(value, "target_closed", {"request_sha256", "selection_sha256", "run_attempt_sha256", "operation_identity",
        "reserved_slots", "directories", "files", "selected_copies", "counters"}, origin, slot=slot)
    _directory(value["operation_identity"])
    _same(value["reserved_slots"], list(range(1, slot + 1)), "ADMISSION_INVALID")
    _original(old_policy.allocation, tuple(value["reserved_slots"]))
    dirs, _files = _inventory({key: value[key] for key in ("directories", "files")})
    _same(dirs[""]["identity"], value["operation_identity"], "IDENTITY_INVALID")
    _old_selected(value["selected_copies"])
    counts = value["counters"]
    _exact(counts, {"read_bytes", "read_calls", "seek_calls", "confirmed_write_bytes", "write_calls", "uncertain_read_calls", "uncertain_write_bytes", "elapsed_ns"})
    for count in counts.values():
        _u(count)
    if (counts["read_bytes"] > 256 * _GIB
            or counts["confirmed_write_bytes"] + counts["uncertain_write_bytes"] > 64 * _GIB
            or counts["uncertain_read_calls"] or counts["uncertain_write_bytes"]
            or counts["elapsed_ns"] > 60_000_000_000
            or counts["confirmed_write_bytes"] + 16_384 > 64 * _GIB):
        _fail("COUNTERS_INVALID")


def _old_source_metadata(value, origin):
    _exact(value, {"format_version", "kind", "operation_id", "capsule", "node_observation", "source_projection"})
    _same(value["format_version"], 1, "VERSION_INVALID")
    _same(value["kind"], "personal_authority_raw_source_observation", "KIND_INVALID")
    _same(value["operation_id"], origin["operation_id"], "SOURCE_INVALID")
    capsule = _original(census.normalize, value["capsule"])
    _original(projection.normalize, value["source_projection"])
    _original(projection.cross_validate_capsule, value["source_projection"], capsule)
    _same(capsule["operation_id"], value["operation_id"], "SOURCE_INVALID")
    _same(capsule["coverage"], {"declared_tree": "complete", "declared_copies": "none", "reason": None}, "SOURCE_INVALID")
    for key in ("copies", "artifacts", "links"):
        _same(capsule[key], [], "SOURCE_INVALID")
    if type(capsule["source_context"]) is not dict:
        _fail("SOURCE_INVALID")
    observation = value["node_observation"]
    _exact(observation, {"state", "entry_id"})
    _enum(observation["state"], ("missing", "noncanonical_name", "json_limit", "parsed", "invalid"), "SOURCE_INVALID")
    if observation["entry_id"] is not None:
        _identifier(observation["entry_id"], _FID)
    for row in capsule["directories"]:
        key = row["relative_key"]
        if "/" not in key and key.rstrip(" .").casefold() == "node-identity.json":
            _fail("SOURCE_INVALID")
    variants = [row for row in capsule["files"] if "/" not in row["relative_key"]
        and row["relative_key"].rstrip(" .").casefold() == "node-identity.json"]
    if not variants:
        _same(observation, {"state": "missing", "entry_id": None}, "SOURCE_INVALID")
    else:
        if len(variants) != 1:
            _fail("SOURCE_INVALID")
        row = variants[0]
        _same(observation["entry_id"], row["entry_id"], "SOURCE_INVALID")
        if row["relative_key"] != "node-identity.json":
            _same(observation["state"], "noncanonical_name", "SOURCE_INVALID")
        elif row["byte_size"] > 4096:
            _same(observation["state"], "json_limit", "SOURCE_INVALID")
        elif observation["state"] not in ("parsed", "invalid"):
            _fail("SOURCE_INVALID")
    if (observation["state"] == "parsed") != (capsule["source_context"]["node"] is not None):
        _fail("SOURCE_INVALID")


def _old_local(path, raw, declaration, *, prepared_text, actual_sha):
    origin = declaration["origin"]
    origin_paths = {"request.json": "request_sha256", "source-metadata.json": "source_metadata_sha256",
        "source-projection.json": "source_projection_sha256", "selection.json": "selection_sha256"}
    for name, field in (("capsule", "capsule_sha256"), ("source-verified", "source_verified_sha256"),
                        ("manifest", "manifest_sha256"), ("receipt", "receipt_sha256")):
        origin_paths[f"runs/{origin['generation']:02d}/{name}.json"] = field
    if path in origin_paths:
        _same(actual_sha, origin[origin_paths[path]], "SOURCE_INVALID")
    value = _parse_prepared(raw, prepared_text, _MAX_REQUEST if path == "request.json" else _MAX_JSON)
    if path == "request.json":
        _original(raw2.decode, "request", raw)
        _same(value["operation_id"], origin["operation_id"], "SOURCE_INVALID")
        return
    if path == "source-metadata.json":
        _old_source_metadata(value, origin)
        return
    if path == "source-projection.json":
        _original(projection.decode, raw)
        return
    if path == "selection.json":
        _old_header(value, "selection", {"request_sha256", "initial_run_attempt_sha256", "source_metadata_sha256",
            "source_projection_sha256", "source_file_ids"}, origin)
        identifiers = value["source_file_ids"]
        if type(identifiers) is not list or len(identifiers) > 100_000:
            _fail("TYPE_INVALID")
        for identifier in identifiers:
            _identifier(identifier, _FID)
        _same(identifiers, sorted(set(identifiers)), "ORDER_INVALID")
        return
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "runs" or parts[1] not in ("01", "02"):
        _fail("FACT_SET_INVALID")
    slot = int(parts[1])
    name = parts[2]
    if len(parts) == 3 and name == "capsule.json":
        capsule = _original(census.decode, raw)
        _same(capsule["operation_id"], origin["operation_id"], "SOURCE_INVALID")
    elif len(parts) == 3 and name == "run-attempt.json":
        _old_header(value, "run_attempt", {"request_sha256", "frozen_selection_sha256", "reservation", "admission", "anchor", "predecessor"}, origin, slot=slot)
        _original(old_policy.normalize_reservation, value["reservation"])
        _same(value["reservation"], old_policy.slot_reservation(slot), "BUDGET_INVALID")
        _exact(value["anchor"], {"relative_key", "identity"})
        _same(value["anchor"]["relative_key"], "admission-q1-sha256-" + origin["request_sha256"], "ANCHOR_INVALID")
        _directory(value["anchor"]["identity"])
        admission = value["admission"]
        if slot == 1:
            _same(value["frozen_selection_sha256"], None, "SOURCE_INVALID")
            _same(value["predecessor"], None, "SOURCE_INVALID")
            _exact(admission, {"kind", "relative_key", "identity"})
            _same(admission["kind"], "operation_directory", "SOURCE_INVALID")
            _same(admission["relative_key"], "", "SOURCE_INVALID")
            _directory(admission["identity"])
        else:
            _sha(value["frozen_selection_sha256"])
            _exact(value["predecessor"], {"slot", "run_id", "run_attempt_sha256", "run_attempt_byte_size"})
            _same(value["predecessor"]["slot"], 1, "SOURCE_INVALID")
            _uuid(value["predecessor"]["run_id"])
            _sha(value["predecessor"]["run_attempt_sha256"])
            _u(value["predecessor"]["run_attempt_byte_size"], _MAX_JSON, positive=True)
            _exact(admission, set(_FILE) | {"kind"})
            _same(admission["kind"], "resume_claim", "SOURCE_INVALID")
            _marker({key: admission[key] for key in _FILE}, "resume-slot-02.claim")
    elif (match := _OLD_COPY_ATTEMPT.fullmatch(path)) is not None:
        _old_header(value, "copy_attempt", {"request_sha256", "selection_sha256", "run_attempt_sha256", "copy_id",
            "source_entry_id", "target_relative_key", "target_parent_identity", "sha256", "byte_size"}, origin, slot=slot)
        _uuid(value["copy_id"])
        _same(value["copy_id"], match[2], "SOURCE_INVALID")
        _identifier(value["source_entry_id"], _FID)
        _same(value["target_relative_key"], f"runs/{slot:02d}/payload/{value['copy_id']}/{value['source_entry_id']}.raw", "SOURCE_INVALID")
        _directory(value["target_parent_identity"])
        _sha(value["sha256"])
        _u(value["byte_size"], _ORIGINAL_FILE_BYTES)
    elif (match := _OLD_COPY_RECORD.fullmatch(path)) is not None:
        _old_header(value, "copy_record", {"request_sha256", "selection_sha256", "copy_attempt_sha256", "copy"}, origin, slot=slot)
        copied = value["copy"]
        _exact(copied, {"source_entry_id", "target_relative_key", "target_parent_identity", "copy_path_identity", "copy_handle_identity", "sha256", "byte_size"})
        _identifier(copied["source_entry_id"], _FID)
        _same(copied["source_entry_id"], match[2], "SOURCE_INVALID")
        _key(copied["target_relative_key"])
        payload = _OLD_PAYLOAD.fullmatch(copied["target_relative_key"])
        if payload is None or int(payload[1]) != slot or payload[3] != copied["source_entry_id"]:
            _fail("SOURCE_INVALID")
        _uuid(payload[2])
        _directory(copied["target_parent_identity"])
        _sha(copied["sha256"])
        _u(copied["byte_size"], _ORIGINAL_FILE_BYTES)
        _file_pair(copied["copy_path_identity"], copied["copy_handle_identity"], copied["byte_size"], _ORIGINAL_FILE_BYTES)
    elif len(parts) == 3 and name == "source-verified.json":
        _old_header(value, "source_verified", {"request_sha256", "selection_sha256", "run_attempt_sha256", "capsule_sha256",
            "source_closed", "target_closed", "selected_copies"}, origin, slot=slot)
        _old_source_closed(value["source_closed"], origin)
        _old_target_closed(value["target_closed"], origin, slot)
        _same(value["target_closed"]["run_id"], value["run_id"], "SOURCE_INVALID")
        _old_selected(value["selected_copies"])
        _same(value["selected_copies"], value["target_closed"]["selected_copies"], "SOURCE_INVALID")
    elif len(parts) == 3 and name == "manifest.json":
        _old_header(value, "manifest", {"scope", "request_sha256", "selection_sha256", "source_verified_sha256",
            "outcome", "facts", "payloads", "markers", "retained"}, origin, slot=slot)
        _same(value["scope"], "raw-byte-preservation", "VERSION_INVALID")
        _enum(value["outcome"], ("copied", "empty_source"))
        for key in ("facts", "markers", "retained"):
            _refs(value[key], maximum=_MAX_FACTS, zero=True, byte_maximum=_ORIGINAL_FILE_BYTES)
        for row in _ordered(value["payloads"], {"source_entry_id", "relative_key", "sha256", "byte_size"}, key="source_entry_id", maximum=100_000):
            _identifier(row["source_entry_id"], _FID)
            _key(row["relative_key"])
            _sha(row["sha256"])
            _u(row["byte_size"], _ORIGINAL_FILE_BYTES)
    elif len(parts) == 3 and name == "receipt.json":
        _old_header(value, "receipt", {"scope", "request_sha256", "source_verified_sha256", "manifest_sha256", "outcome", "publication"}, origin, slot=slot)
        _same(value["scope"], "raw-byte-preservation", "VERSION_INVALID")
        _enum(value["outcome"], ("copied", "empty_source"))
        _same(value["publication"], "published_observed", "FIELDS_INVALID")
    else:
        _fail("FACT_SET_INVALID")


def _attempt_path(slot, name):
    return f"attempts/{slot:02d}/{name}"


def _formal(path):
    if path == "request.json":
        return "request", None, None
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "attempts" and parts[1] in ("01", "02"):
        for name, kind in _LOCAL_FILES:
            if parts[2] == name:
                return kind, int(parts[1]), None
    match = _COPY_ATTEMPT.fullmatch(path)
    if match is not None:
        _uuid(match[2])
        return "copy_attempt", int(match[1]), match[2]
    match = _COPY_RECORD.fullmatch(path)
    if match is not None:
        return "copy_record", int(match[1]), match[2]
    return None


def _formal_ref(value, *, kind=None):
    formal = _formal(value["relative_key"])
    if formal is None or kind is not None and formal[0] != kind:
        _fail("LINK_INVALID")
    _ref(value, maximum=_cap(formal[0]))


def _raw_mapping(value, maximum, *, outer=False):
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if len(value) > maximum:
        _fail("LIMIT")
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    copied = {}
    for key, raw in value.items():
        _key(key)
        if type(raw) is not bytes:
            _fail("TYPE_INVALID")
        if outer:
            formal = _formal(key)
            if formal is None:
                _fail("FACT_SET_INVALID")
            cap = _cap(formal[0])
        else:
            cap = _MAX_REQUEST if key == "request.json" else _MAX_JSON
        if len(raw) > cap:
            _fail("LIMIT")
        copied[key] = raw
    return copied


class _Graph:
    def __init__(self, outer_fact_bytes, package_raw2_fact_bytes_by_attempt, operation_inventory):
        self.raw = _raw_mapping(outer_fact_bytes, _MAX_OUTER_FACTS, outer=True)
        if type(package_raw2_fact_bytes_by_attempt) is not dict:
            _fail("TYPE_INVALID")
        if any(type(key) is not str for key in package_raw2_fact_bytes_by_attempt):
            _fail("TYPE_INVALID")
        if not set(package_raw2_fact_bytes_by_attempt) <= {"01", "02"}:
            _fail("FACT_SET_INVALID")
        self.package_raw = {int(key): _raw_mapping(value, _MAX_FACTS)
            for key, value in package_raw2_fact_bytes_by_attempt.items()}
        self.inventory = _json_snapshot(operation_inventory)
        self.dirs, self.files = _inventory(self.inventory, maximum=_MAX_OPERATION_ENTRIES,
            file_maximum=_WRITE_BYTES, total_maximum=2 * _WRITE_BYTES)
        self.formals, self.temporaries, self.failures = {}, {None: [], 1: [], 2: []}, {1: [], 2: []}
        self.packages = {1: ({}, {}), 2: ({}, {})}
        self._physical()
        if set(self.raw) != set(self.formals):
            _fail("FACT_SET_INVALID")
        self.docs, self.raw_refs = {}, {}
        for path, raw in self.raw.items():
            kind, _slot_value, _identifier_value = self.formals[path]
            maximum = _cap(kind)
            prepared = _prepare_raw(raw, maximum)
            row = self.files[path]
            digest, size = _digest(raw), len(raw)
            if digest != row["sha256"] or size != row["byte_size"]:
                _fail("FACT_CHANGED")
            self.raw_refs[path] = {"relative_key": path, "sha256": digest, "byte_size": size}
            value = _parse_prepared(raw, prepared, maximum)
            _local(kind, value)
            self.docs[path] = value
        self.request = self.docs.get("request.json")
        self.request_sha = self.raw_refs["request.json"]["sha256"] if self.request is not None else None
        self.occupied = [1, 2] if "resume-attempt-02.claim" in self.files else [1]
        self.states = {}

    def _physical(self):
        for key, row in self.dirs.items():
            if key in ("", "attempts") or re.fullmatch(r"admission-rp1-sha256-[0-9a-f]{64}", key):
                continue
            parts = key.split("/")
            if len(parts) >= 2 and parts[0] == "attempts" and parts[1] in ("01", "02"):
                slot = int(parts[1])
                if len(parts) == 2 or len(parts) == 3 and parts[2] in ("copy-attempts", "copy-records", "failures"):
                    continue
                if len(parts) >= 3 and parts[2] == "package":
                    copied = dict(row)
                    copied["relative_key"] = "/".join(parts[3:])
                    self.packages[slot][0][copied["relative_key"]] = copied
                    continue
            _fail("INVENTORY_INVALID")
        for key, row in self.files.items():
            parts = key.split("/")
            if len(parts) >= 4 and parts[0] == "attempts" and parts[1] in ("01", "02") and parts[2] == "package":
                copied = dict(row)
                copied["relative_key"] = "/".join(parts[3:])
                self.packages[int(parts[1])][1][copied["relative_key"]] = copied
                continue
            formal = _formal(key)
            if formal is not None:
                self.formals[key] = formal
                continue
            if key == "resume-attempt-02.claim":
                _marker(row, key)
                continue
            if len(parts) == 3 and parts[0] == "attempts" and parts[1] in ("01", "02") and parts[2] == "receipt-started.claim":
                _marker(row, key)
                continue
            match = _FAILURE.fullmatch(key)
            if match is not None:
                _uuid(match[2])
                _u(row["byte_size"], _FAILURE_BYTES)
                self.failures[int(match[1])].append(key)
                continue
            match = _TEMP_NAME.fullmatch(parts[-1])
            if match is not None:
                _uuid(match[1])
                if len(parts) == 1:
                    _u(row["byte_size"], _MAX_REQUEST)
                    self.temporaries[None].append(key)
                    continue
                if parts[0] == "attempts" and parts[1] in ("01", "02"):
                    if len(parts) == 3:
                        _u(row["byte_size"], _MAX_JSON)
                    elif len(parts) == 4 and parts[2] in ("copy-attempts", "copy-records"):
                        _u(row["byte_size"], 8192 if parts[2] == "copy-attempts" else 16_384)
                    else:
                        _fail("INVENTORY_INVALID")
                    self.temporaries[int(parts[1])].append(key)
                    continue
            _fail("INVENTORY_INVALID")
        if any(len(paths) > 1 for paths in self.temporaries.values()) or any(len(paths) > 1 for paths in self.failures.values()):
            _fail("INVENTORY_INVALID")
        for dirs, files in self.packages.values():
            if dirs or files:
                _inventory({"directories": list(dirs.values()), "files": list(files.values())})

    def _ref_for(self, path):
        if path not in self.raw_refs:
            _fail("FACT_REQUIRED")
        return dict(self.raw_refs[path])

    def _refs_for(self, paths):
        return [self._ref_for(path) for path in sorted(paths, key=lambda item: item.encode("utf-8"))]

    def _match_ref(self, value, path):
        _same(value, self._ref_for(path), "LINK_INVALID")

    def _doc(self, slot, name, *, required=False):
        value = self.docs.get(_attempt_path(slot, name))
        if value is None and required:
            _fail("FACT_REQUIRED")
        return value

    def _has_slot(self, slot):
        prefix = f"attempts/{slot:02d}"
        return any(key == prefix or key.startswith(prefix + "/") for key in self.dirs) or any(key.startswith(prefix + "/") for key in self.files)

    def _initial(self):
        root_identity = self.dirs[""]["identity"]
        anchors = [key for key in self.dirs if key.startswith("admission-rp1-sha256-")]
        if len(anchors) > 1:
            _fail("ANCHOR_INVALID")
        claim = self.files.get("resume-attempt-02.claim")
        if self._has_slot(2) and claim is None:
            _fail("CLAIM_INVALID")
        if claim is not None and "attempts/01" not in self.dirs:
            _fail("ADMISSION_INVALID")
        if self.request is None:
            if self.raw or anchors or claim is not None or self._has_slot(2):
                _fail("DAG_INVALID")
            allowed_dirs = {"", "attempts", "attempts/01", "attempts/01/copy-attempts", "attempts/01/copy-records", "attempts/01/failures"}
            if not set(self.dirs) <= allowed_dirs or set(self.files) != set(self.temporaries[None]):
                _fail("DAG_INVALID")
            if self.package_raw:
                _fail("FACT_SET_INVALID")
            return False
        _same(root_identity, self.request["target"]["operation_identity"], "IDENTITY_INVALID")
        if self.temporaries[None]:
            _fail("DAG_INVALID")
        expected_anchor = "admission-rp1-sha256-" + self.request_sha
        if anchors:
            _same(anchors, [expected_anchor], "ANCHOR_INVALID")
            _same(self.dirs[expected_anchor]["children"], [], "ANCHOR_INVALID")
        else:
            if set(self.raw) != {"request.json"} or claim is not None or any(self.temporaries[slot] or self.failures[slot] for slot in (1, 2)):
                _fail("ANCHOR_INVALID")
            if self._has_slot(2) or any(dirs or files for dirs, files in self.packages.values()):
                _fail("DAG_INVALID")
        return True

    def _headers(self):
        copy_ids = set()
        for path, value in self.docs.items():
            kind, slot, identifier = self.formals[path]
            if kind == "request":
                continue
            if kind == "replica":
                _same(value["replica_id"], self.request["operation_id"], "LINK_INVALID")
                if self._doc(slot, "attempt.json") is None:
                    _fail("DAG_INVALID")
                continue
            _same(value["operation_id"], self.request["operation_id"], "LINK_INVALID")
            _same(value["slot"], slot, "LINK_INVALID")
            _same(value["request_sha256"], self.request_sha, "LINK_INVALID")
            attempt = self._doc(slot, "attempt.json", required=True)
            _same(value["run_id"], attempt["run_id"], "LINK_INVALID")
            if slot == 1:
                _same(value["run_id"], self.request["creation_run_id"], "LINK_INVALID")
            elif value["run_id"] == self.request["creation_run_id"]:
                _fail("LINK_INVALID")
            if kind == "copy_attempt":
                _same(value["copy_id"], identifier, "LINK_INVALID")
                if identifier in copy_ids:
                    _fail("DAG_INVALID")
                copy_ids.add(identifier)
            elif kind == "copy_record":
                _same(value["package_entry_id"], identifier, "LINK_INVALID")

    def _attempt(self, slot, attempt):
        prefix = f"attempts/{slot:02d}"
        for directory in (prefix, prefix + "/copy-attempts", prefix + "/copy-records", prefix + "/failures"):
            if directory not in self.dirs:
                _fail("INVENTORY_INVALID")
        anchor_path = "admission-rp1-sha256-" + self.request_sha
        if anchor_path not in self.dirs:
            _fail("ANCHOR_INVALID")
        anchor = self.dirs[anchor_path]
        _same(attempt["anchor"], {"relative_key": anchor_path, "parent_identity": self.dirs[""]["identity"],
            "identity": anchor["identity"], "children": []}, "ANCHOR_INVALID")
        if slot == 2:
            claim = self.files.get("resume-attempt-02.claim")
            if claim is None:
                _fail("CLAIM_INVALID")
            _same(attempt["admission"]["resume_claim"], claim, "CLAIM_INVALID")
            if self._doc(1, "attempt.json") is not None:
                _same(attempt["predecessor"], {"state": "published", "attempt_ref": self._ref_for("attempts/01/attempt.json")}, "ADMISSION_INVALID")
            else:
                _same(attempt["predecessor"], {"state": "not_published", "slot": 1}, "ADMISSION_INVALID")
            if "attempts/01/receipt-started.claim" in self.files or "attempts/01/receipt.json" in self.files:
                _fail("ADMISSION_INVALID")
        for path in self.failures[slot]:
            _same(posixpath.basename(path), attempt["run_id"] + ".json", "LINK_INVALID")

    def _bind_r01(self, value, *, source):
        request = self.request
        _same(value["replica_id"], request["operation_id"], "SOURCE_INVALID")
        _same(value["origin"]["generation"], request["source"]["generation"], "SOURCE_INVALID")
        _same(value["origin"]["request_sha256"], request["source"]["expected_request_sha256"], "SOURCE_INVALID")
        if request["source"]["source_kind"] == "profile_raw2":
            _same(value["origin"]["operation_id"], request["source"]["original_operation_id"], "SOURCE_INVALID")
        _same(value["current"]["profile_sha256"], request["target"]["profile"]["profile_sha256"], "SOURCE_INVALID")
        _same(value["current"]["node_id"], request["target"]["node"]["node_id"], "SOURCE_INVALID")
        _same(value["package_map"]["content_set_sha256"], request["expected_content_set_sha256"], "CONTENT_CHANGED")
        if source:
            _same(value["current"]["directories"][0]["identity"], request["source"]["root_identity"], "IDENTITY_INVALID")

    def _selection(self, slot, selection):
        _same(selection["source_location"], self.request["source"], "SOURCE_INVALID")
        source = selection["source_declaration"]
        self._bind_r01(source, source=True)
        source_dirs = {row["relative_key"]: row for row in source["current"]["directories"]}
        source_files = {row["relative_key"]: row for row in source["current"]["files"]}
        entries = {f"p{index:08d}": row for index, row in enumerate(source["current"]["files"])}
        dirs, files = self.packages[slot]
        if not set(dirs) <= set(source_dirs) or not set(files) <= set(source_files):
            _fail("INVENTORY_INVALID")
        attempts, records = {}, {}
        selection_path = _attempt_path(slot, "source-selection.json")
        for path, (kind, actual_slot, _identifier_value) in self.formals.items():
            if actual_slot != slot or kind not in ("copy_attempt", "copy_record"):
                continue
            document = self.docs[path]
            entry_id = document["package_entry_id"]
            if entry_id not in entries:
                _fail("LINK_INVALID")
            entry = entries[entry_id]
            _same(document["relative_key"], entry["relative_key"], "LINK_INVALID")
            self._match_ref(document["selection_ref"], selection_path)
            if kind == "copy_attempt":
                if entry_id in attempts:
                    _fail("DAG_INVALID")
                attempts[entry_id] = path
                _same(document["source_file_sha256"], entry["sha256"], "CONTENT_CHANGED")
                _same(document["source_byte_size"], entry["byte_size"], "CONTENT_CHANGED")
                parent = posixpath.dirname(entry["relative_key"])
                if parent not in dirs:
                    _fail("INVENTORY_INVALID")
                _same(document["target_parent_identity"], dirs[parent]["identity"], "IDENTITY_INVALID")
            else:
                if entry_id in records:
                    _fail("DAG_INVALID")
                records[entry_id] = path
        for entry_id, path in records.items():
            if entry_id not in attempts:
                _fail("DAG_INVALID")
            record, copy_attempt = self.docs[path], self.docs[attempts[entry_id]]
            self._match_ref(record["copy_attempt_ref"], attempts[entry_id])
            entry = entries[entry_id]
            _same(record["sha256"], entry["sha256"], "CONTENT_CHANGED")
            _same(record["byte_size"], entry["byte_size"], "CONTENT_CHANGED")
            _same(record["parent_identity"], copy_attempt["target_parent_identity"], "IDENTITY_INVALID")
            if entry["relative_key"] not in files:
                _fail("INVENTORY_INVALID")
            _same({key: record[key] for key in _FILE}, files[entry["relative_key"]], "IDENTITY_INVALID")
        attempted_paths = {entries[entry_id]["relative_key"] for entry_id in attempts}
        if not set(files) <= attempted_paths:
            _fail("DAG_INVALID")
        target = self._doc(slot, "replica.json")
        if target is not None:
            if set(records) != set(entries) or set(attempts) != set(entries) or set(dirs) != set(source_dirs) or set(files) != set(source_files):
                _fail("PACKAGE_INCOMPLETE")
            self._bind_r01(target, source=False)
            for key in ("origin", "package_map", "source_map"):
                _same(target[key], source[key], "CONTENT_CHANGED")
            if target["current"]["observation_scope_id"] == source["current"]["observation_scope_id"]:
                _fail("IDENTITY_INVALID")
            _same(target["current"]["directories"], list(dirs.values()), "IDENTITY_INVALID")
            _same(target["current"]["files"], list(files.values()), "IDENTITY_INVALID")
        return {"selection": selection, "source": source, "target": target, "entries": entries,
            "copy_attempts": attempts, "copy_records": records, "bytes": sum(row["byte_size"] for row in source_files.values())}

    def _raw2(self, slot, state):
        supplied = self.package_raw.get(slot, {})
        wanted = {row["relative_key"]: row for row in state["selection"]["raw_control_refs"]}
        if not set(supplied) <= set(wanted):
            _fail("FACT_SET_INVALID")
        files = self.packages[slot][1]
        for path, raw in supplied.items():
            if path not in files:
                _fail("FACT_SET_INVALID")
            prepared = _prepare_raw(raw, _MAX_REQUEST if path == "request.json" else _MAX_JSON)
            digest, size = _digest(raw), len(raw)
            for ref in (wanted[path], files[path]):
                if size != ref["byte_size"] or digest != ref["sha256"]:
                    _fail("FACT_CHANGED")
            _old_local(path, raw, state["source"], prepared_text=prepared, actual_sha=digest)
        for entry_id in state["copy_records"]:
            path = state["entries"][entry_id]["relative_key"]
            if path in wanted and path not in supplied:
                _fail("FACT_REQUIRED")
        missing = sorted(set(wanted) - set(supplied), key=lambda item: item.encode("utf-8"))
        state["missing"] = missing
        state["source_validated"] = False
        state["target_validated"] = False
        if not missing:
            _original(replica.normalize, state["source"], fact_bytes=supplied)
            state["source_validated"] = True
            if state["target"] is not None:
                _original(replica.decode, self.raw[_attempt_path(slot, "replica.json")], fact_bytes=supplied)
                state["target_validated"] = True

    def _closed(self, slot, state):
        source_closed = self._doc(slot, "source-closed.json")
        target_closed = self._doc(slot, "target-closed.json")
        selection_path = _attempt_path(slot, "source-selection.json")
        if source_closed is not None:
            if state["target"] is None:
                _fail("DAG_INVALID")
            self._match_ref(source_closed["selection_ref"], selection_path)
            _same(source_closed["content_set_sha256"], self.request["expected_content_set_sha256"], "CONTENT_CHANGED")
            source_counts = source_closed["scope_counters"]
            _same(source_counts["scope_id"], state["source"]["current"]["observation_scope_id"], "IDENTITY_INVALID")
            _same(source_closed["owned_close_summary"]["bound_inventory_sha256"], state["source"]["current"]["inventory_sha256"], "CONTENT_CHANGED")
            if source_counts["attributable"]["package_read_bytes"] < 4 * state["bytes"] or source_counts["end"]["source_read_bytes"] < 4 * state["bytes"]:
                _fail("COUNTERS_INVALID")
        if target_closed is not None:
            if source_closed is None or state["target"] is None:
                _fail("DAG_INVALID")
            self._match_ref(target_closed["selection_ref"], selection_path)
            self._match_ref(target_closed["replica_ref"], _attempt_path(slot, "replica.json"))
            self._match_ref(target_closed["source_closed_ref"], _attempt_path(slot, "source-closed.json"))
            record_refs = self._refs_for(state["copy_records"].values())
            _same(target_closed["copy_record_set_sha256"], _dh("copy-record-set", record_refs), "CONTENT_CHANGED")
            target_counts = target_closed["scope_counters"]
            _same(target_counts["scope_id"], state["target"]["current"]["observation_scope_id"], "IDENTITY_INVALID")
            _same(target_closed["owned_close_summary"]["bound_inventory_sha256"], state["target"]["current"]["inventory_sha256"], "CONTENT_CHANGED")
            source_counts = source_closed["scope_counters"]
            _monotone(target_counts["begin"], source_counts["begin"])
            _monotone(source_counts["end"], target_counts["end"])
            for key in _IO_KEYS:
                if source_counts["attributable"][key] + target_counts["attributable"][key] > target_counts["end"][key] - target_counts["begin"][key]:
                    _fail("COUNTERS_INVALID")
            for key in _SOURCE_COUNTERS:
                _same(target_counts["end"][key], source_counts["end"][key], "COUNTERS_INVALID")
            if (target_counts["attributable"]["package_read_bytes"] < 4 * state["bytes"]
                    or target_counts["end"]["read_bytes"] < 8 * state["bytes"]
                    or target_counts["end"]["source_read_bytes"] < 4 * state["bytes"]):
                _fail("COUNTERS_INVALID")

    def _manifest(self, slot, state):
        manifest = self._doc(slot, "manifest.json")
        receipt = self._doc(slot, "receipt.json")
        marker_path = _attempt_path(slot, "receipt-started.claim")
        marker = self.files.get(marker_path)
        if marker is not None and (manifest is None or self.failures[slot]):
            _fail("DAG_INVALID")
        if receipt is not None and (manifest is None or marker is None or self.failures[slot] or self.temporaries[slot]):
            _fail("DAG_INVALID")
        if manifest is None:
            return
        source_closed = self._doc(slot, "source-closed.json", required=True)
        target_closed = self._doc(slot, "target-closed.json", required=True)
        attempt = self._doc(slot, "attempt.json", required=True)
        names = {"selection_ref": "source-selection.json", "replica_ref": "replica.json",
            "source_closed_ref": "source-closed.json", "target_closed_ref": "target-closed.json"}
        for key, name in names.items():
            self._match_ref(manifest[key], _attempt_path(slot, name))
        control_paths = {"request.json", _attempt_path(slot, "attempt.json"),
            *(_attempt_path(slot, name) for name in names.values()),
            *state["copy_attempts"].values(), *state["copy_records"].values()}
        predecessor_bytes = 0
        if slot == 2 and attempt["predecessor"]["state"] == "published":
            control_paths.add("attempts/01/attempt.json")
            predecessor_bytes = len(self.raw["attempts/01/attempt.json"])
        controls = self._refs_for(control_paths)
        records = self._refs_for(state["copy_records"].values())
        _same(manifest["control_refs"], controls, "FACT_SET_INVALID")
        _same(manifest["copy_record_refs"], records, "FACT_SET_INVALID")
        _same(manifest["copy_record_set_sha256"], target_closed["copy_record_set_sha256"], "CONTENT_CHANGED")
        _same(manifest["content_set_sha256"], self.request["expected_content_set_sha256"], "CONTENT_CHANGED")
        selection = {"package_file_count": len(state["entries"]), "package_directory_count": len(state["source"]["current"]["directories"]),
            "package_bytes": state["bytes"], "request_bytes": len(self.raw["request.json"]),
            "attempt_bytes": len(self.raw[_attempt_path(slot, "attempt.json")]),
            "source_selection_bytes": len(self.raw[_attempt_path(slot, "source-selection.json")]),
            "predecessor_attempt_bytes": predecessor_bytes, "observer_node_bytes": self.request["target"]["node"]["byte_size"]}
        _same(manifest["payload_gate"]["selection"], selection, "BUDGET_INVALID")
        for later in (source_closed["scope_counters"]["end"], target_closed["scope_counters"]["end"], manifest["publication_budget"]["checkpoint"]):
            _monotone(manifest["payload_gate"]["snapshot"], later)
        budget = manifest["publication_budget"]
        _same(budget["inputs"], {"package_bytes": state["bytes"], "prior_control_bytes": sum(row["byte_size"] for row in controls),
            "observer_node_bytes": self.request["target"]["node"]["byte_size"]}, "BUDGET_INVALID")
        _monotone(target_closed["scope_counters"]["end"], budget["checkpoint"])
        if budget["checkpoint"]["read_bytes"] < 9 * state["bytes"] or budget["checkpoint"]["source_read_bytes"] < 4 * state["bytes"]:
            _fail("COUNTERS_INVALID")
        for key in _SOURCE_COUNTERS:
            _same(budget["checkpoint"][key], source_closed["scope_counters"]["end"][key], "COUNTERS_INVALID")
        if receipt is not None:
            self._match_ref(receipt["manifest_ref"], _attempt_path(slot, "manifest.json"))
            _same(receipt["receipt_start_marker"], marker, "CLAIM_INVALID")
            before = receipt["before_receipt"]
            _monotone(budget["checkpoint"], before)
            size = len(self.raw[_attempt_path(slot, "manifest.json")])
            if (before["confirmed_write_bytes"] < budget["checkpoint"]["confirmed_write_bytes"] + size
                    or before["read_bytes"] < budget["checkpoint"]["read_bytes"] + 2 * size):
                _fail("COUNTERS_INVALID")
            for key in _SOURCE_COUNTERS:
                _same(before[key], source_closed["scope_counters"]["end"][key], "COUNTERS_INVALID")

    def _slot_graph(self, slot):
        attempt = self._doc(slot, "attempt.json")
        selection = self._doc(slot, "source-selection.json")
        formals = {path for path, (_kind, actual_slot, _id) in self.formals.items() if actual_slot == slot}
        dirs, files = self.packages[slot]
        marker = _attempt_path(slot, "receipt-started.claim") in self.files
        if attempt is None:
            if formals or dirs or files or marker or self.failures[slot]:
                _fail("DAG_INVALID")
            if any(len(path.split("/")) != 3 for path in self.temporaries[slot]):
                _fail("DAG_INVALID")
            if slot in self.package_raw:
                _fail("FACT_SET_INVALID")
            return None
        self._attempt(slot, attempt)
        if selection is None:
            if formals != {_attempt_path(slot, "attempt.json")} or dirs or files or marker:
                _fail("DAG_INVALID")
            if any(len(path.split("/")) != 3 for path in self.temporaries[slot]) or slot in self.package_raw:
                _fail("DAG_INVALID")
            return None
        state = self._selection(slot, selection)
        self._raw2(slot, state)
        self._closed(slot, state)
        self._manifest(slot, state)
        return state

    def _attempt_report(self, slot):
        state = self.states.get(slot)
        stage = "admitted"
        if self._doc(slot, "attempt.json") is not None:
            stage = "attempt_declared"
        if state is not None:
            stage = "selection_declared"
            if state["copy_attempts"] or any(self.packages[slot]):
                stage = "copy_partial"
        for name, label in (("replica.json", "replica_declared"), ("source-closed.json", "source_closed_declared"),
                ("target-closed.json", "target_closed_declared"), ("manifest.json", "manifest_declared")):
            if self._doc(slot, name) is not None:
                stage = label
        if _attempt_path(slot, "receipt-started.claim") in self.files:
            stage = "publication_unknown"
        if self._doc(slot, "receipt.json") is not None:
            stage = "receipt_declared"
        return {"slot": slot, "stage": stage,
            "source_selection_status": "not_established" if state is None else "shape_and_links_checked",
            "embedded_source_r01_status": "validated" if state is not None and state["source_validated"] else "not_established",
            "embedded_target_r01_status": "validated" if state is not None and state["target_validated"] else "not_established",
            "raw2_dependency_status": "not_established" if state is None else "incomplete" if state["missing"] else "complete",
            "missing_declared_raw2_fact_keys": [] if state is None else list(state["missing"]),
            "package_completion": "not_established"}

    def prefix(self):
        has_request = self._initial()
        if has_request:
            self._headers()
            for slot in (1, 2):
                self.states[slot] = self._slot_graph(slot)
        return {"format_version": 1, "evidence_kind": "supplied_bytes_declaration",
            "outer_status": "valid_prefix", "request_status": "validated" if has_request else "not_established",
            "request_sha256": self.request_sha, "occupied_slots": list(self.occupied),
            "attempts": [self._attempt_report(slot) for slot in self.occupied], "package_completion": "not_established",
            "native_observation": False, "historical_factory_return": "unknown", "data_restored": False,
            "runtime_write": False, "sender_authority": False}

    def complete(self, slot, observer_node_bytes, observer_node_observation):
        self.prefix()
        state = self.states.get(slot)
        if state is None or not state["source_validated"] or not state["target_validated"]:
            _fail("PACKAGE_INCOMPLETE")
        for name, _kind in _LOCAL_FILES:
            if self._doc(slot, name) is None:
                _fail("PACKAGE_INCOMPLETE")
        if _attempt_path(slot, "receipt-started.claim") not in self.files or self.failures[slot] or self.temporaries[slot]:
            _fail("PACKAGE_INCOMPLETE")
        if len(state["entries"]) != len(state["copy_records"]) or len(state["entries"]) != len(state["copy_attempts"]):
            _fail("PACKAGE_INCOMPLETE")
        for other, previous in self.states.items():
            if other == slot or previous is None:
                continue
            has_late = any(self._doc(other, name) is not None for name in
                ("replica.json", "source-closed.json", "target-closed.json", "manifest.json", "receipt.json"))
            if has_late and (not previous["source_validated"] or not previous["target_validated"]):
                _fail("PACKAGE_INCOMPLETE")
        observation = _json_snapshot(observer_node_observation)
        _node(observation)
        _same(observation, self.request["target"]["node"], "NODE_CHANGED")
        node = _parse(observer_node_bytes, 4096)
        _exact(node, {"instance_id"})
        _uuid(node["instance_id"])
        if (node["instance_id"] != observation["node_id"] or len(observer_node_bytes) != observation["byte_size"]
                or _digest(observer_node_bytes) != observation["sha256"]):
            _fail("NODE_CHANGED")
        dirs, files = self.packages[slot]
        return {"format_version": 1, "evidence_kind": "supplied_bytes_declaration",
            "outer_status": "valid_complete_selected_attempt", "attempt": slot, "request_sha256": self.request_sha,
            "source_selection_sha256": self.raw_refs[_attempt_path(slot, "source-selection.json")]["sha256"],
            "replica_sha256": self.raw_refs[_attempt_path(slot, "replica.json")]["sha256"],
            "manifest_sha256": self.raw_refs[_attempt_path(slot, "manifest.json")]["sha256"],
            "receipt_sha256": self.raw_refs[_attempt_path(slot, "receipt.json")]["sha256"],
            "content_set_sha256": self.request["expected_content_set_sha256"],
            "package_file_count": len(files), "package_directory_count": len(dirs),
            "package_bytes": sum(row["byte_size"] for row in files.values()),
            "selected_package_completion": "validated_declaration",
            "other_attempts": [self._attempt_report(other) for other in self.occupied if other != slot],
            "native_observation": False, "historical_factory_return": "unknown", "data_restored": False,
            "runtime_write": False, "sender_authority": False}


@_checked
def cross_validate_prefix(*, outer_fact_bytes: dict, package_raw2_fact_bytes_by_attempt: dict,
                          operation_inventory: dict) -> dict:
    """Check a declared current prefix without promoting absent raw2 history."""
    return _Graph(outer_fact_bytes, package_raw2_fact_bytes_by_attempt, operation_inventory).prefix()


@_checked
def cross_validate_package(*, outer_fact_bytes: dict, package_raw2_fact_bytes_by_attempt: dict,
                           operation_inventory: dict, attempt: int, observer_node_bytes: bytes,
                           observer_node_observation: dict) -> dict:
    """Check complete supplied declarations; no bytes are read and no owner exists."""
    _slot(attempt)
    return _Graph(outer_fact_bytes, package_raw2_fact_bytes_by_attempt, operation_inventory).complete(
        attempt, observer_node_bytes, observer_node_observation)
