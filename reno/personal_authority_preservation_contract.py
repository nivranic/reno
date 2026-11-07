"""Pure, declarative authority raw-census format 2.

Metadata validation is not capture, a disk observation, a complete-tree proof,
or authority. Byte checks compare caller-supplied immutable buffers only. No
runtime, filesystem, SQL, clock, configuration, or permission objects are used.
Released preservation/certificate/checkpoint formats are not imported or changed.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import math
import ntpath
import posixpath
import re


FORMAT_VERSION = 2
KIND = "personal_authority_preservation_capsule"
SCOPE = "personal-authority-raw-census-v2"
LAYOUT_PROFILE = "namespace2-store2-extension3-enrollment1-r729"
LAYOUT_SOURCE_SHA256 = "7965e1b9d8e4b3a3336e88ec6545f4448ad6cb208f16e653cb81310b01f71aeb"
_DEFAULT_ITEMS = (
    ("max_files", 100_000), ("max_directories", 1_000_000),
    ("max_entries", 64_000_000), ("max_artifacts", 100_000),
    ("max_links", 64_000_000), ("max_json_bytes", 128 * 1024**2),
    ("max_total_bytes", 64 * 1024**3),
    ("max_raw_json_bytes", 128 * 1024**2), ("max_json_depth", 64),
    ("max_json_nodes", 4_000_000), ("max_json_string_bytes", 128 * 1024**2),
    ("max_json_integer_digits", 4300),
)
# Public convenience value; validation ceilings use the immutable tuple above.
DEFAULT_LIMITS = dict(_DEFAULT_ITEMS)
_PREFIX = ".reno-personal-authority-namespace-"
_ROOT = _PREFIX + "v2"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID = re.compile(_UUID_TEXT + r"\Z")
_STAGE = re.compile(re.escape(_ROOT) + r"-(?:reserve|header)-" + _UUID_TEXT + r"\.stage\Z")
_FACT = re.compile(re.escape(_ROOT) + r"/facts/[a-z][a-z0-9_-]{0,79}\.json\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)
_TOP = frozenset({"format_version", "kind", "scope", "operation_id", "layout_profile",
    "layout_source_sha256", "source_context", "coverage", "directories", "files",
    "unresolved", "copies", "artifacts", "links"})
_DIR = frozenset({"entry_id", "relative_key", "source_identity", "listing", "children"})
_FILE = frozenset({"entry_id", "relative_key", "role", "sha256", "byte_size",
    "source_directory_identity", "source_path_identity", "source_handle_identity"})
_COPY = frozenset({"source_entry_id", "target_relative_key", "target_parent_identity",
    "copy_path_identity", "copy_handle_identity", "sha256", "byte_size"})
_IDENTITY = frozenset({"device", "inode"})
_FILE_IDENTITY = _IDENTITY | {"byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
_TREE_STATES = frozenset({"complete", "partial", "unknown"})
_REASONS = frozenset({"SELECTION_ONLY", "ENUMERATION_INCOMPLETE", "CONTENT_UNREADABLE",
    "UNSAFE_ENTRY", "SOURCE_CHANGED", "LIMIT_REACHED", "CAPTURE_UNAVAILABLE"})
_UNRESOLVED_REASONS = frozenset({"MISSING_DURING_CAPTURE", "UNREADABLE", "UNSAFE_TYPE",
    "UNSAFE_ALIAS", "UNKNOWN_ENTRY_TYPE"})
_ARTIFACT_VERSIONS = {"personal_preservation_manifest": 1,
    "source_preservation_certificate17": 2, "home_preservation_checkpoint17": 2,
    "home_private_bundle_manifest": 1}
_EXACT_ROLES = {"node-identity.json": "node_identity_raw",
    _ROOT + "-reservation.json": "namespace_anchor_raw",
    _ROOT + "-header.json": "namespace_anchor_raw"}
for _names, _role_name in (
    (("init-reservation.json", "declared-binding.json", "files-seal.json", "head.json"), "primitive_metadata_raw"),
    (("authority.lock",), "authority_lock_raw"),
    (("store-init-reservation.json", "store-header.json"), "store2_metadata_raw"),
    (("store-extension-reservation-v3.json", "store-extension-header-v3.json"), "extension3_metadata_raw"),
    (("store-enrollment-reservation-v1.json", "store-enrollment-header-v1.json"), "enrollment1_metadata_raw"),
):
    _EXACT_ROLES.update({_ROOT + "/" + name: _role_name for name in _names})
del _names, _role_name


class AuthorityPreservationContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_PRESERVATION2_" + suffix
        super().__init__(self.code)


Error = AuthorityPreservationContractError


def _fail(suffix):
    raise AuthorityPreservationContractError(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AuthorityPreservationContractError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


def _exact(value, keys, code="FIELDS_INVALID"):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != set(keys)):
        _fail(code)


def _int(value, minimum=0, maximum=2**63 - 1, code="TYPE_INVALID"):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)


def _enum(value, values, code="TYPE_INVALID"):
    if type(value) is not str or value not in values:
        _fail(code)


def _text(value, maximum=4096, *, empty=False, code="TYPE_INVALID"):
    if (type(value) is not str or (not empty and not value) or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail(code)
    try:
        if len(value.encode("utf-8")) > maximum:
            _fail(code)
    except UnicodeError:
        _fail(code)


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("TYPE_INVALID")


def _uuid(value):
    if type(value) is not str or not _UUID.fullmatch(value):
        _fail("TYPE_INVALID")


def _limits(value):
    defaults = dict(_DEFAULT_ITEMS)
    if value is None:
        return defaults
    _exact(value, defaults)
    copied = value.copy()
    for name, ceiling in _DEFAULT_ITEMS:
        _int(copied[name], 1, ceiling, "LIMIT")
    return copied


def _snapshot(value, limits, *, original=False):
    """Bounded builtin-only copy. This is not a concurrent capture protocol."""
    active = set()
    nodes = strings = wire = 0

    def charge_wire(count):
        nonlocal wire
        if not original:
            wire += count
            if wire > limits["max_json_bytes"]:
                _fail("LIMIT")

    def visit(item, depth):
        nonlocal nodes, strings
        nodes += 1
        if depth > limits["max_json_depth"] or nodes > limits["max_json_nodes"]:
            _fail("LIMIT")
        kind = type(item)
        if kind is str:
            # Every codepoint needs at least one UTF-8 byte. These lower bounds
            # stop oversized strings before any encode or escaped-string copy.
            if (len(item) > limits["max_json_string_bytes"] - strings
                    or not original and len(item) + 2 > limits["max_json_bytes"] - wire):
                _fail("LIMIT")
            charge_wire(2)  # JSON string quotes.
            for char in item:
                point = ord(char)
                if 0xD800 <= point <= 0xDFFF:
                    _fail("JSON_INVALID")
                if not original and (point < 32 or point == 127):
                    _fail("JSON_INVALID")
                width = 1 if point < 0x80 else 2 if point < 0x800 else 3 if point < 0x10000 else 4
                strings += width
                if strings > limits["max_json_string_bytes"]:
                    _fail("LIMIT")
                charge_wire(width + (char in {'"', "\\"}))
            return item
        if item is None:
            charge_wire(4)
            return None
        if kind is bool:
            if not original:
                _fail("TYPE_INVALID")
            return item
        if kind is int:
            # A cheap upper bound before decimal conversion; all wire fields
            # have smaller, explicit numeric bounds checked afterwards.
            if item.bit_length() > limits["max_json_integer_digits"] * 4:
                _fail("LIMIT")
            try:
                digits = len(str(abs(item)))
                if digits > limits["max_json_integer_digits"]:
                    _fail("LIMIT")
            except ValueError:
                _fail("LIMIT")
            charge_wire(digits + (item < 0))
            return item
        if kind is float:
            if not math.isfinite(item):
                _fail("JSON_INVALID")
            if not original:
                _fail("TYPE_INVALID")
            return item
        if kind not in (dict, list):
            _fail("JSON_INVALID" if original else "TYPE_INVALID")
        if id(item) in active:
            _fail("JSON_INVALID")
        active.add(id(item))
        try:
            # Charge brackets, separators and object colons before constructing
            # another container. Keys/values charge their own canonical bytes.
            charge_wire(2 + max(0, len(item) - 1) + (len(item) if kind is dict else 0))
            if kind is list:
                if len(item) > limits["max_json_nodes"] - nodes:
                    _fail("LIMIT")
                return [visit(child, depth + 1) for child in item]
            if len(item) > (limits["max_json_nodes"] - nodes) // 2:
                _fail("LIMIT")
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail("JSON_INVALID")
                result[visit(key, depth + 1)] = visit(child, depth + 1)
            return result
        finally:
            active.remove(id(item))

    return visit(value, 0)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_preflight(raw, limits):
    """Bound nesting, tokens and decoded string bytes before JSON allocation."""
    index = depth = nodes = strings = 0
    length = len(raw)
    whitespace = b" \t\r\n"
    delimiters = whitespace + b"{}[],:"

    def hex_unit(start):
        if start + 4 > length:
            _fail("JSON_INVALID")
        part = raw[start:start + 4]
        if any(byte not in b"0123456789abcdefABCDEF" for byte in part):
            _fail("JSON_INVALID")
        return int(part, 16)

    while index < length:
        byte = raw[index]
        if byte in whitespace or byte in (44, 58):
            index += 1
            continue
        if byte in (93, 125):
            depth -= 1
            index += 1
            continue
        nodes += 1
        if nodes > limits["max_json_nodes"]:
            _fail("LIMIT")
        if byte in (91, 123):
            depth += 1
            if depth > limits["max_json_depth"] + 1:
                _fail("LIMIT")
            index += 1
            continue
        if byte != 34:
            start = index
            while index < length and raw[index] not in delimiters:
                index += 1
            token_size = index - start
            if (byte in b"-0123456789" and token_size
                    and not any(raw[pos] in b".eE" for pos in range(start, index))
                    and token_size - (byte == 45) > limits["max_json_integer_digits"]):
                _fail("LIMIT")
            # Invalid tokens and punctuation are rejected by the real parser.
            if index == start:
                index += 1
            continue
        index += 1
        while index < length and raw[index] != 34:
            byte = raw[index]
            if byte < 32:
                _fail("JSON_INVALID")
            if byte != 92:
                strings += 1  # Unescaped UTF-8 bytes, checked strictly below.
                index += 1
            else:
                index += 1
                if index >= length:
                    _fail("JSON_INVALID")
                escaped = raw[index]
                if escaped in b'"\\/bfnrt':
                    strings += 1
                    index += 1
                elif escaped == 117:
                    unit = hex_unit(index + 1)
                    index += 5
                    if 0xD800 <= unit <= 0xDBFF:
                        if raw[index:index + 2] != b"\\u":
                            _fail("JSON_INVALID")
                        low = hex_unit(index + 2)
                        if not 0xDC00 <= low <= 0xDFFF:
                            _fail("JSON_INVALID")
                        strings += 4
                        index += 6
                    elif 0xDC00 <= unit <= 0xDFFF:
                        _fail("JSON_INVALID")
                    else:
                        strings += 1 if unit < 0x80 else 2 if unit < 0x800 else 3
                else:
                    _fail("JSON_INVALID")
            if strings > limits["max_json_string_bytes"]:
                _fail("LIMIT")
        if index >= length:
            _fail("JSON_INVALID")
        index += 1


def _json(raw, limits, *, original=False):
    cap = limits["max_raw_json_bytes" if original else "max_json_bytes"]
    if type(raw) is not bytes:
        _fail("TYPE_INVALID")
    if len(raw) > cap:
        _fail("LIMIT")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("JSON_INVALID")
    _json_preflight(raw, limits)

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                _fail("JSON_INVALID")
            result[key] = item
        return result

    def integer(token):
        if len(token.lstrip("-")) > limits["max_json_integer_digits"]:
            _fail("LIMIT")
        try:
            return int(token)
        except ValueError:
            _fail("LIMIT")

    def floating(token):
        value = float(token)
        if not math.isfinite(value):
            _fail("JSON_INVALID")
        if not original:
            _fail("TYPE_INVALID")
        return value

    def constant(_token):
        _fail("JSON_INVALID")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_int=integer, parse_float=floating, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError):
        _fail("JSON_INVALID")
    return _snapshot(value, limits, original=original)


def _component(value):
    _text(value, 255, code="PATH_INVALID")
    if (value in {".", ".."} or any(char in value for char in '/\\:<>"|?*')
            or value.endswith((".", " ")) or _RESERVED.fullmatch(value)):
        _fail("PATH_INVALID")


def _relative(value, *, root=False):
    _text(value, empty=root, code="PATH_INVALID")
    if value == "" and root:
        return ""
    parts = value.split("/")
    if len(parts) > 128:
        _fail("PATH_INVALID")
    for part in parts:
        _component(part)
    return ntpath.normcase(value).replace("\\", "/")


def _absolute(value):
    _text(value, code="PATH_INVALID")
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        _fail("PATH_INVALID")
    if re.match(r"^[A-Za-z]:[\\/]", value):
        tail = value[3:]
        parts = [] if not tail else tail.replace("\\", "/").split("/")
        for part in parts:
            _component(part)
        return "windows", ntpath.normcase(ntpath.normpath(value))
    if value.startswith("\\\\"):
        parts = value[2:].replace("\\", "/").split("/")
        if len(parts) < 2:
            _fail("PATH_INVALID")
        for part in parts:
            _component(part)
        return "windows", ntpath.normcase(ntpath.normpath(value))
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        _fail("PATH_INVALID")
    for part in ([] if value == "/" else value[1:].split("/")):
        _component(part)
    return "posix", posixpath.normpath(value)


def _directory_identity(value):
    _exact(value, _IDENTITY, "IDENTITY_INVALID")
    for key in ("device", "inode"):
        _decimal(value[key], positive=key == "inode")
    return value["device"], value["inode"]


def _decimal(value, *, positive=False):
    if (type(value) is not str or len(value) > 40 or not _DECIMAL.fullmatch(value)
            or positive and value == "0"):
        _fail("IDENTITY_INVALID")


def _file_identity(value, limits):
    _exact(value, _FILE_IDENTITY, "IDENTITY_INVALID")
    for key in ("device", "inode", "mtime_ns", "ctime_ns"):
        _decimal(value[key], positive=key == "inode")
    _int(value["byte_size"], maximum=limits["max_total_bytes"], code="IDENTITY_INVALID")
    _int(value["mode"], maximum=2**32 - 1, code="IDENTITY_INVALID")
    _int(value["nlink"], 1, 1, "IDENTITY_INVALID")
    if value["mode"] & 0o170000 != 0o100000:
        _fail("IDENTITY_INVALID")
    return value["device"], value["inode"]


def _file_pair(path, handle, size, limits):
    key = _file_identity(path, limits)
    _file_identity(handle, limits)
    if (any(path[field] != handle[field] for field in _FILE_IDENTITY - {"ctime_ns"})
            or path["byte_size"] != size):
        _fail("IDENTITY_INVALID")
    return key


def _role(path):
    if path in _EXACT_ROLES:
        return _EXACT_ROLES[path]
    if _STAGE.fullmatch(path):
        return "namespace_staging_raw"
    if _FACT.fullmatch(path):
        return "primitive_fact_raw"
    for name, role in (("journal-v1", "store2_journal_raw"),
                       ("journal-v3", "extension3_journal_raw"),
                       ("journal-enrollment-v1", "enrollment1_journal_raw")):
        if path.startswith(_ROOT + "/" + name + "/"):
            return role
    return "unclassified_authority_raw"


def _ordered_entries(items, prefix, fields, maximum, *, key="relative_key", root=False):
    if type(items) is not list:
        _fail("TYPE_INVALID")
    if len(items) > maximum:
        _fail("LIMIT")
    previous = None
    for index, item in enumerate(items):
        _exact(item, fields)
        if item["entry_id" if prefix != "a" else "artifact_id"] != f"{prefix}{index:08d}":
            _fail("ORDER_INVALID")
        if key == "relative_key":
            _relative(item[key], root=root)
        else:
            _text(item[key])
        current = item[key].encode("utf-8")
        if previous is not None and current <= previous:
            _fail("ORDER_INVALID")
        previous = current


def _context(context, files_by_path, limits):
    if context is None:
        return
    _exact(context, {"profile", "node"})
    profile = context["profile"]
    _exact(profile, {"profile_paths", "roots"})
    _exact(profile["roots"], {"data", "media", "reports", "checkpoints"})
    paths = profile["profile_paths"]
    if type(paths) is not list or len(paths) != 4:
        _fail("FIELDS_INVALID")
    selected = [_absolute(path) for path in paths]
    root_keys, identities, physical_paths = {}, {}, {}
    for name, row in profile["roots"].items():
        _exact(row, {"path", "identity"})
        key, identity = _absolute(row["path"]), _directory_identity(row["identity"])
        if key in identities and identities[key] != identity:
            _fail("IDENTITY_INVALID")
        if identity in physical_paths and physical_paths[identity] != key:
            _fail("IDENTITY_ALIAS")
        identities[key], physical_paths[identity], root_keys[name] = identity, key, key
    module = ntpath if selected[0][0] == "windows" else posixpath
    if ((selected[0][0], module.dirname(selected[0][1])) != root_keys["data"]
            or selected[1:] != [root_keys[name] for name in ("media", "reports", "checkpoints")]):
        _fail("PATH_INVALID")
    node = context["node"]
    if node is None:
        return
    _exact(node, {"node_id", "sha256", "path_identity", "handle_identity"})
    _uuid(node["node_id"])
    _sha(node["sha256"])
    _file_identity(node["path_identity"], limits)
    _file_identity(node["handle_identity"], limits)
    entry = files_by_path.get("node-identity.json")
    if (entry is None or node["sha256"] != entry["sha256"]
            or node["path_identity"] != entry["source_path_identity"]
            or node["handle_identity"] != entry["source_handle_identity"]):
        _fail("IDENTITY_INVALID")


def _tree(value, limits):
    directories, files, unresolved = (value[key] for key in ("directories", "files", "unresolved"))
    _ordered_entries(directories, "d", _DIR, limits["max_directories"], root=True)
    _ordered_entries(files, "f", _FILE, limits["max_files"])
    _ordered_entries(unresolved, "u", {"entry_id", "relative_key", "reason"}, limits["max_entries"])
    context = value["source_context"]
    if context is None and (directories or files or unresolved):
        _fail("TREE_INVALID")
    if context is not None and (not directories or directories[0]["relative_key"] != ""):
        _fail("TREE_INVALID")
    folded, children = set(), {}
    directory_ids, file_ids = set(), set()
    directories_by_path = {row["relative_key"]: row for row in directories}
    files_by_path = {row["relative_key"]: row for row in files}
    for rows in (directories, files, unresolved):
        for row in rows:
            path = row["relative_key"]
            comparison = _relative(path, root=row["entry_id"].startswith("d"))
            if comparison in folded:
                _fail("PATH_ALIAS")
            folded.add(comparison)
            if not path:
                continue
            first = comparison.split("/", 1)[0]
            if first != "node-identity.json" and not first.startswith(_PREFIX):
                _fail("TREE_INVALID")
            parent = posixpath.dirname(path)
            if parent not in directories_by_path:
                _fail("TREE_INVALID")
            children.setdefault(parent, []).append(row)
    entries = 0
    for row in directories:
        identity = _directory_identity(row["source_identity"])
        if identity in directory_ids:
            _fail("IDENTITY_ALIAS")
        directory_ids.add(identity)
        _enum(row["listing"], _TREE_STATES, "COVERAGE_INVALID")
        if type(row["children"]) is not list:
            _fail("TREE_INVALID")
        entries += len(row["children"])
        if entries > limits["max_entries"]:
            _fail("LIMIT")
        expected = [child["entry_id"] for child in sorted(children.get(row["relative_key"], []),
                    key=lambda child: posixpath.basename(child["relative_key"]).encode("utf-8"))]
        if row["children"] != expected:
            _fail("TREE_INVALID")
    source_bytes = 0
    for row in files:
        _sha(row["sha256"])
        _int(row["byte_size"], maximum=limits["max_total_bytes"], code="LIMIT")
        source_bytes += row["byte_size"]
        if source_bytes > limits["max_total_bytes"]:
            _fail("LIMIT")
        parent = directories_by_path[posixpath.dirname(row["relative_key"])]
        _directory_identity(row["source_directory_identity"])
        if row["source_directory_identity"] != parent["source_identity"]:
            _fail("IDENTITY_INVALID")
        identity = _file_pair(row["source_path_identity"], row["source_handle_identity"], row["byte_size"], limits)
        if identity in file_ids or identity in directory_ids:
            _fail("IDENTITY_ALIAS")
        file_ids.add(identity)
        if row["role"] != _role(row["relative_key"]):
            _fail("ROLE_INVALID")
    for row in unresolved:
        _enum(row["reason"], _UNRESOLVED_REASONS, "COVERAGE_INVALID")
    _context(context, files_by_path, limits)
    if context is not None and directories[0]["source_identity"] != context["profile"]["roots"]["data"]["identity"]:
        _fail("IDENTITY_INVALID")
    return file_ids, directory_ids, source_bytes


def _copies(value, file_ids, directory_ids, limits):
    copies = value["copies"]
    files = {row["entry_id"]: row for row in value["files"]}
    if type(copies) is not list:
        _fail("TYPE_INVALID")
    if len(copies) > len(files):
        _fail("COPY_INVALID")
    previous = None
    copy_ids, paths = set(), set()
    parents, parent_identities = {}, {}
    total = 0
    for row in copies:
        _exact(row, _COPY)
        entry_id = row["source_entry_id"]
        if type(entry_id) is not str or entry_id not in files:
            _fail("COPY_INVALID")
        if previous is not None and entry_id <= previous:
            _fail("ORDER_INVALID")
        previous = entry_id
        source = files[entry_id]
        _sha(row["sha256"])
        _int(row["byte_size"], maximum=limits["max_total_bytes"], code="LIMIT")
        if row["sha256"] != source["sha256"] or row["byte_size"] != source["byte_size"]:
            _fail("COPY_INVALID")
        total += row["byte_size"]
        if total > limits["max_total_bytes"]:
            _fail("LIMIT")
        path = _relative(row["target_relative_key"])
        if path in paths:
            _fail("PATH_ALIAS")
        paths.add(path)
        identity = _file_pair(row["copy_path_identity"], row["copy_handle_identity"], row["byte_size"], limits)
        if identity in file_ids or identity in directory_ids or identity in copy_ids:
            _fail("IDENTITY_ALIAS")
        copy_ids.add(identity)
        parent = posixpath.dirname(path)
        parent_identity = _directory_identity(row["target_parent_identity"])
        if parent in parents and parents[parent] != parent_identity:
            _fail("IDENTITY_INVALID")
        if parent_identity in parent_identities and parent_identities[parent_identity] != parent:
            _fail("IDENTITY_ALIAS")
        parents[parent], parent_identities[parent_identity] = parent_identity, parent
    if set(parent_identities) & (file_ids | copy_ids):
        _fail("IDENTITY_ALIAS")
    for path in paths:
        parent = posixpath.dirname(path)
        while parent:
            if parent in paths:
                _fail("PATH_ALIAS")
            parent = posixpath.dirname(parent)


def _artifacts(value, source_size, limits):
    artifacts = value["artifacts"]
    _ordered_entries(artifacts, "a", {"artifact_id", "contract", "format_version", "sha256", "byte_size"},
                     limits["max_artifacts"], key="artifact_id")
    seen = set()
    for row in artifacts:
        _enum(row["contract"], _ARTIFACT_VERSIONS, "ARTIFACT_INVALID")
        _int(row["format_version"], _ARTIFACT_VERSIONS[row["contract"]],
             _ARTIFACT_VERSIONS[row["contract"]], "ARTIFACT_INVALID")
        _sha(row["sha256"])
        _int(row["byte_size"], maximum=limits["max_total_bytes"], code="LIMIT")
        source_size += row["byte_size"]
        if source_size > limits["max_total_bytes"]:
            _fail("LIMIT")
        key = row["contract"], row["format_version"], row["sha256"]
        if key in seen:
            _fail("ARTIFACT_INVALID")
        seen.add(key)


def _links(value, limits):
    links = value["links"]
    if type(links) is not list:
        _fail("TYPE_INVALID")
    if len(links) > limits["max_links"]:
        _fail("LIMIT")
    references = {row["entry_id"] for row in value["files"]} | {row["artifact_id"] for row in value["artifacts"]}
    previous, seen = None, set()
    for row in links:
        _exact(row, {"from_id", "field_path", "to_id"})
        if (type(row["from_id"]) is not str or type(row["to_id"]) is not str
                or row["from_id"] not in references or row["to_id"] not in references
                or row["from_id"] == row["to_id"]):
            _fail("LINK_INVALID")
        path = row["field_path"]
        if type(path) is not list or not 1 <= len(path) <= 64:
            _fail("LINK_INVALID")
        for item in path:
            if type(item) is str:
                _text(item, code="LINK_INVALID")
            else:
                _int(item, maximum=2**31 - 1, code="LINK_INVALID")
        encoded = _canonical(path)
        key = row["from_id"], encoded, row["to_id"]
        field = row["from_id"], encoded
        if field in seen:
            _fail("LINK_INVALID")
        if previous is not None and key <= previous:
            _fail("ORDER_INVALID")
        seen.add(field)
        previous = key


def _coverage(value):
    coverage = value["coverage"]
    _exact(coverage, {"declared_tree", "declared_copies", "reason"})
    tree, copies = coverage["declared_tree"], coverage["declared_copies"]
    _enum(tree, _TREE_STATES, "COVERAGE_INVALID")
    _enum(copies, {"complete", "partial", "none"}, "COVERAGE_INVALID")
    if tree == "complete":
        if (coverage["reason"] is not None or value["source_context"] is None
                or value["unresolved"] or any(row["listing"] != "complete" for row in value["directories"])):
            _fail("COVERAGE_INVALID")
    else:
        _enum(coverage["reason"], _REASONS, "COVERAGE_INVALID")
    if value["source_context"] is None and tree != "unknown":
        _fail("COVERAGE_INVALID")
    if tree == "partial" and len(value["directories"]) + len(value["files"]) + len(value["unresolved"]) <= 1:
        _fail("COVERAGE_INVALID")
    count, total = len(value["copies"]), len(value["files"])
    if (copies == "none" and count != 0
            or copies == "partial" and not 0 < count < total
            or copies == "complete" and not 0 < count == total):
        _fail("COVERAGE_INVALID")


def _normalize(value, limits):
    copied = _snapshot(value, limits)
    _exact(copied, _TOP)
    if (type(copied["format_version"]) is not int or copied["format_version"] != FORMAT_VERSION
            or copied["kind"] != KIND or copied["scope"] != SCOPE
            or copied["layout_profile"] != LAYOUT_PROFILE
            or copied["layout_source_sha256"] != LAYOUT_SOURCE_SHA256):
        _fail("VERSION_INVALID")
    _uuid(copied["operation_id"])
    file_ids, directory_ids, source_size = _tree(copied, limits)
    _copies(copied, file_ids, directory_ids, limits)
    _artifacts(copied, source_size, limits)
    _links(copied, limits)
    _coverage(copied)
    return copied


@_checked
def normalize(value, *, limits=None):
    """Return detached metadata; no supplied bytes or physical proof required."""
    return _normalize(value, _limits(limits))


@_checked
def encode(value, *, limits=None):
    """Encode new capsule metadata only; never re-encode a legacy artifact."""
    return _canonical(_normalize(value, _limits(limits)))


@_checked
def decode(raw, *, limits=None):
    """Decode canonical capsule bytes into detached, untrusted declarations."""
    budget = _limits(limits)
    value = _json(raw, budget)
    normalized = _normalize(value, budget)
    if _canonical(normalized) != raw:
        _fail("JSON_NONCANONICAL")
    return normalized


def _original(raw, sha256, byte_size, limits):
    _sha(sha256)
    _int(byte_size, maximum=limits["max_total_bytes"], code="LIMIT")
    if type(raw) is not bytes:
        _fail("TYPE_INVALID")
    if len(raw) > limits["max_total_bytes"]:
        _fail("LIMIT")
    if len(raw) != byte_size or hashlib.sha256(raw).hexdigest() != sha256:
        _fail("RAW_DIGEST_CHANGED")


@_checked
def verify_original_bytes(raw, *, expected_sha256, expected_byte_size, limits=None):
    """Compare one immutable supplied buffer; no authority or completion token."""
    _original(raw, expected_sha256, expected_byte_size, _limits(limits))


def _buffer_map(value, rows, id_key, limits):
    if type(value) is not dict or len(value) != len(rows):
        _fail("RAW_SET_CHANGED")
    # A shallow copy captures immutable buffer references, not their origin.
    copied = value.copy()
    expected = {row[id_key] for row in rows}
    if any(type(key) is not str for key in copied) or set(copied) != expected:
        _fail("RAW_SET_CHANGED")
    for row in rows:
        _original(copied[row[id_key]], row["sha256"], row["byte_size"], limits)
    return copied


def _node_original(parsed, node):
    _exact(parsed, {"instance_id"}, "LINK_INVALID")
    _uuid(parsed["instance_id"])
    if parsed["instance_id"] != node["node_id"]:
        _fail("LINK_INVALID")


@_checked
def cross_validate_originals(value, *, source_bytes, copy_bytes, artifact_bytes, limits=None):
    """Optional all-map comparison. It does not establish historical originality.

    Large captures can verify individual buffers instead. Their actual fd reads,
    bounded semantic checks, full selection and final custody remain producer work.
    """
    budget = _limits(limits)
    document = _normalize(value, budget)
    source = _buffer_map(source_bytes, document["files"], "entry_id", budget)
    _buffer_map(copy_bytes, document["copies"], "source_entry_id", budget)
    artifacts = _buffer_map(artifact_bytes, document["artifacts"], "artifact_id", budget)
    raw_by_id = dict(source)
    raw_by_id.update(artifacts)
    sha_by_id = {row["entry_id"]: row["sha256"] for row in document["files"]}
    sha_by_id.update({row["artifact_id"]: row["sha256"] for row in document["artifacts"]})
    context = document["source_context"]
    node = context["node"] if context is not None else None
    node_id = next((row["entry_id"] for row in document["files"]
                    if node is not None and row["relative_key"] == "node-identity.json"), None)
    current_id = None
    parsed = None
    for link in document["links"]:
        from_id = link["from_id"]
        if from_id != current_id:
            # Release the previous parsed tree before decoding the next group.
            parsed = None
            parsed = _json(raw_by_id[from_id], budget, original=True)
            current_id = from_id
            if from_id == node_id:
                _node_original(parsed, node)
                node_id = None
        leaf = parsed
        for part in link["field_path"]:
            if type(part) is str:
                if type(leaf) is not dict or part not in leaf:
                    _fail("LINK_INVALID")
            elif type(leaf) is not list or part >= len(leaf):
                _fail("LINK_INVALID")
            leaf = leaf[part]
        if type(leaf) is not str or leaf != sha_by_id[link["to_id"]]:
            _fail("LINK_INVALID")
    parsed = None
    if node_id is not None:
        _node_original(_json(raw_by_id[node_id], budget, original=True), node)
