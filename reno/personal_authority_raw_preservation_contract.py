"""Pure format-1 raw-preservation declarations, never runtime capabilities.

All hashes refer to supplied, bounded, immutable complete control-file bytes.
Neither a valid declaration nor a cached metadata context proves live I/O,
normal resource closure, publication success, or any authority permission.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import posixpath
import re

from . import personal_authority_preservation_contract as c2
from . import personal_authority_source_projection as projection


FORMAT_VERSION = 1
SCOPE = "raw-byte-preservation"
DIRECTORY = ".reno-authority-raw-preservation-v1"
_MAX_FACT = 128 * 1024**2
_KINDS = frozenset({"request", "run_attempt", "selection", "copy_attempt", "copy_record",
                    "source_verified", "manifest", "receipt", "failure"})
_HEADER = frozenset({"format_version", "kind", "operation_id", "run_id"})
_ENTRY = re.compile(r"f[0-9]{8}\Z")
_UUID = c2._UUID_TEXT
_DYNAMIC = re.compile(
    rf"(?:attempts|copy-attempts|failures)/{_UUID}\.json\Z|copy-records/f[0-9]{{8}}\.json\Z")
_FIXED_FILES = frozenset({"request.json", "selection.json", "source-metadata.json",
    "source-projection.json", "capsule.json", "source-verified.json", "manifest.json", "receipt.json"})
_COUNTERS = frozenset({"read_bytes", "read_calls", "seek_calls", "confirmed_write_bytes",
    "write_calls", "uncertain_read_calls", "uncertain_write_bytes", "elapsed_ns"})
_SOURCE_CLOSED = frozenset({"format_version", "kind", "operation_id",
    "source_projection_sha256", "source_metadata_sha256", "read_bytes", "read_calls",
    "seek_calls", "source_file_count", "source_byte_size"})
_DEFAULTS = (("source_max_read_bytes", 384 * 1024**3), ("max_read_bytes", 512 * 1024**3),
    ("max_write_bytes", 128 * 1024**3), ("deadline_ms", 120_000), ("node_json_bytes", 4096),
    ("chunk_bytes", 131_072), ("max_target_depth", 8), ("max_fact_bytes", _MAX_FACT),
    ("max_failure_bytes", 16_384), ("max_attempts", 1))
_PHASES = frozenset({"opening_target", "request", "run_attempt", "source_open", "selection",
    "copy", "source_close", "target_close", "publication", "publisher_close"})
_FAILURE_SUFFIXES = frozenset({"OPERATION_EXISTS", "REQUEST_MISMATCH", "SELECTION_MISMATCH",
    "PROTECTION_UNAVAILABLE", "SOURCE_FAILED", "TARGET_UNSAFE", "TARGET_CHANGED",
    "COPY_MISMATCH", "WRITE_LIMIT", "READ_LIMIT", "DEADLINE_LIMIT", "WRITE_UNCERTAIN",
    "PUBLICATION_FAILED", "CLOSE_FAILED", "INCOMPLETE", "REENTRANT", "OWNER_MISMATCH",
    "CALLER_CANCELLED"})


class AuthorityRawPreservationContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION1_" + suffix
        super().__init__(self.code)


Error = AuthorityRawPreservationContractError


def _fail(suffix):
    raise Error(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Error:
            raise
        except (c2.AuthorityPreservationContractError, projection.AuthoritySourceProjectionError) as exc:
            _fail(exc.code.rsplit("_", 1)[-1] if exc.code.endswith("_LIMIT") else "DECLARATION_INVALID")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
                RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail("FIELDS_INVALID")


def _u(value, maximum=2**63 - 1, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= maximum):
        _fail("LIMIT" if type(value) is int else "TYPE_INVALID")
    return value


def _sha(value):
    c2._sha(value)
    return value


def _uuid(value):
    c2._uuid(value)
    if value == "00000000-0000-0000-0000-000000000000":
        _fail("UUID_INVALID")
    return value


def _maximum(value):
    return _MAX_FACT if value is None else _u(value, _MAX_FACT, positive=True)


def _snapshot(value, maximum):
    """Charge canonical bytes before allocating containers/encoded strings."""
    nodes = wire = strings = 0
    active = set()
    hard = dict(c2._DEFAULT_ITEMS)

    def charge(count):
        nonlocal wire
        wire += count
        if wire > maximum:
            _fail("LIMIT")

    def visit(item, depth):
        nonlocal nodes, strings
        nodes += 1
        if depth > hard["max_json_depth"] or nodes > hard["max_json_nodes"]:
            _fail("LIMIT")
        kind = type(item)
        if kind is str:
            if len(item) + 2 > maximum - wire or len(item) > hard["max_json_string_bytes"] - strings:
                _fail("LIMIT")
            charge(2)
            for char in item:
                point = ord(char)
                if 0xD800 <= point <= 0xDFFF:
                    _fail("JSON_INVALID")
                width = 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
                strings += width
                if strings > hard["max_json_string_bytes"]:
                    _fail("LIMIT")
                escaped = 2 if char in '\b\f\n\r\t"\\' else 6 if point < 32 else width
                charge(escaped)
            return item
        if item is None:
            charge(4)
            return None
        if kind is bool:
            charge(4 if item else 5)
            return item
        if kind is int:
            if item.bit_length() > hard["max_json_integer_digits"] * 4:
                _fail("LIMIT")
            digits = str(item)
            if len(digits.lstrip("-")) > hard["max_json_integer_digits"]:
                _fail("LIMIT")
            charge(len(digits))
            return item
        if kind not in (dict, list) or id(item) in active:
            _fail("TYPE_INVALID")
        charge(2 + max(0, len(item) - 1) + (len(item) if kind is dict else 0))
        remaining = hard["max_json_nodes"] - nodes
        if len(item) > remaining // (2 if kind is dict else 1):
            _fail("LIMIT")
        active.add(id(item))
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in item]
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail("TYPE_INVALID")
                result[visit(key, depth + 1)] = visit(child, depth + 1)
            return result
        finally:
            active.remove(id(item))

    return visit(value, 0)


def _canonical(value, maximum):
    copied = _snapshot(value, maximum)
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(copied):
        raw = part.encode("utf-8")
        if len(raw) > maximum - len(output):
            _fail("LIMIT")
        output.extend(raw)
    return bytes(output)


def _parse(raw, maximum):
    if type(raw) is not bytes:
        _fail("TYPE_INVALID")
    if len(raw) > maximum:
        _fail("LIMIT")
    limits = dict(c2._DEFAULT_ITEMS)
    limits["max_raw_json_bytes"] = limits["max_json_bytes"] = maximum
    # Original-mode parsing preserves bool; our own bounded snapshot charges it.
    copied = _snapshot(c2._json(raw, limits, original=True), maximum)
    if _canonical(copied, maximum) != raw:
        _fail("JSON_NONCANONICAL")
    return copied


def _digest(raw):
    if type(raw) is not bytes:
        _fail("TYPE_INVALID")
    return hashlib.sha256(raw).hexdigest()


@_checked
def default_limits(*, capsule_limits=None):
    selected = c2._limits(capsule_limits)
    return {"capsule_limits": selected, **dict(_DEFAULTS),
            "max_control_files": 3 * selected["max_files"] + 32,
            "max_target_entries": 4 * selected["max_files"] + 64}


def _limits(value):
    _exact(value, {"capsule_limits", "max_control_files", "max_target_entries"} | set(dict(_DEFAULTS)))
    selected = c2._limits(value["capsule_limits"])
    for key, ceiling in _DEFAULTS:
        _u(value[key], ceiling, positive=True)
    if value["chunk_bytes"] != 131_072 or value["max_attempts"] != 1:
        _fail("LIMIT")
    _u(value["max_control_files"], 3 * selected["max_files"] + 32, positive=True)
    _u(value["max_target_entries"], 4 * selected["max_files"] + 64, positive=True)
    if value["max_failure_bytes"] > min(value["max_fact_bytes"], value["max_write_bytes"]):
        _fail("LIMIT")
    return value


def _header(value, kind, fields):
    _exact(value, _HEADER | set(fields))
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail("VERSION_INVALID")
    if value["kind"] != "authority_raw_preservation_" + kind:
        _fail("VERSION_INVALID")
    _uuid(value["operation_id"])
    _uuid(value["run_id"])


def _same(actual, expected, code="BINDING_MISMATCH"):
    pending = [(actual, expected)]
    while pending:
        left, right = pending.pop()
        if type(left) is not type(right):
            _fail(code)
        if type(left) is dict:
            if set(left) != set(right):
                _fail(code)
            pending.extend((left[key], right[key]) for key in left)
        elif type(left) in (list, tuple):
            if len(left) != len(right):
                _fail(code)
            pending.extend(zip(left, right))
        elif left != right:
            _fail(code)


def _ordered(rows, fields, key, maximum):
    if type(rows) is not list or len(rows) > maximum:
        _fail("LIMIT")
    previous = None
    for row in rows:
        _exact(row, fields)
        value = row[key]
        if type(value) is not str:
            _fail("TYPE_INVALID")
        current = value.encode("utf-8")
        if previous is not None and current <= previous:
            _fail("ORDER_INVALID")
        previous = current
    return rows


def _entry(value):
    if type(value) is not str or not _ENTRY.fullmatch(value):
        _fail("ENTRY_INVALID")
    return value


def _key(value, *, root=False, depth=8):
    normalized = c2._relative(value, root=root)
    if value and len(value.split("/")) > depth:
        _fail("LIMIT")
    return normalized


def _fact_name(value):
    _key(value)
    if value not in _FIXED_FILES and not _DYNAMIC.fullmatch(value):
        _fail("FACT_PATH_INVALID")
    if value.startswith(("attempts/", "copy-attempts/", "failures/")):
        _uuid(value.split("/")[1][:-5])


class _Graph:
    """Private append-only pure metadata cache, not a physical owner.

    Runtime may retain one instance across immutable fact additions. Stable
    request/source/P/selection are parsed once; source-entry lookup is O(1).
    The raw table is copied once, never once per copy-record in that path.
    Cache dictionaries are internal; public normalization returns fresh data.
    """
    def __init__(self, fact_bytes, maximum):
        self.maximum = _maximum(maximum)
        self.raw, self.cache = {}, {}
        self.source_entries = None
        self.source_identities = None
        self.total_bytes = 0
        if fact_bytes is not None:
            if type(fact_bytes) is not dict or len(fact_bytes) > 300_032:
                _fail("TYPE_INVALID")
            for path, raw in fact_bytes.items():
                self.add(path, raw)

    def add(self, path, raw):
        _fact_name(path)
        if type(raw) is not bytes:
            _fail("TYPE_INVALID")
        if len(raw) > self.maximum:
            _fail("LIMIT")
        if path in self.raw:
            _same(raw, self.raw[path], "FACT_CHANGED")
            return
        if len(self.raw) >= 300_032 or self.total_bytes + len(raw) > 128 * 1024**3:
            _fail("LIMIT")
        self.raw[path] = raw
        self.total_bytes += len(raw)

    def bytes(self, path):
        try:
            return self.raw[path]
        except KeyError:
            _fail("FACT_REQUIRED")

    def sha(self, path):
        return _digest(self.bytes(path))

    def request(self):
        return self.load("request.json", "request")

    def cap(self, kind):
        return self.maximum if kind == "request" else min(self.maximum, self.request()["limits"]["max_fact_bytes"])

    def load(self, path, kind):
        if path in self.cache:
            return self.cache[path]
        value = self.normalize(kind, _parse(self.bytes(path), self.cap(kind)))
        self.cache[path] = value
        return value

    def normalize(self, kind, value):
        if kind not in _KINDS | {"source_metadata", "source_projection", "capsule"}:
            _fail("KIND_INVALID")
        copied = _snapshot(value, self.cap(kind))
        getattr(self, "validate_" + kind)(copied)
        return copied

    def bind(self, value):
        request = self.request()
        _same(value["operation_id"], request["operation_id"])
        _same(value["run_id"], request["run_id"])
        if "request_sha256" in value:
            _same(value["request_sha256"], self.sha("request.json"))
        return request

    def selection(self):
        return self.load("selection.json", "selection")

    def source(self):
        return self.load("source-metadata.json", "source_metadata")

    def source_entry(self, entry_id):
        self.selection()
        if self.source_entries is None:
            self.source_entries = {row["entry_id"]: row for row in self.source()["capsule"]["files"]}
        try:
            return self.source_entries[_entry(entry_id)]
        except KeyError:
            _fail("ENTRY_INVALID")

    def copy_forbidden_identities(self):
        if self.source_identities is None:
            source = self.source()["capsule"]
            request = self.request()
            found = {c2._directory_identity(row["source_identity"]) for row in source["directories"]}
            found.update((row["source_path_identity"]["device"], row["source_path_identity"]["inode"])
                         for row in source["files"])
            found.update(c2._directory_identity(row["identity"]) for row in request["profile"]["roots"].values())
            found.update(c2._directory_identity(request["target"][key])
                         for key in ("namespace_identity", "operation_identity"))
            self.source_identities = frozenset(found)
        return self.source_identities

    def validate_request(self, value):
        _header(value, "request", {"scope", "layout_profile", "layout_source_sha256", "profile", "target", "limits"})
        _same(value["scope"], SCOPE)
        _same(value["layout_profile"], c2.LAYOUT_PROFILE)
        _same(value["layout_source_sha256"], c2.LAYOUT_SOURCE_SHA256)
        limits = _limits(value["limits"])
        c2._context({"profile": value["profile"], "node": None}, {}, limits["capsule_limits"])
        target = value["target"]
        _exact(target, {"namespace", "namespace_identity", "operation_relative_key", "operation_identity"})
        _same(target["namespace"], DIRECTORY)
        _same(target["operation_relative_key"], value["operation_id"])
        identities = [c2._directory_identity(target[key]) for key in ("namespace_identity", "operation_identity")]
        roots = {c2._directory_identity(row["identity"]) for row in value["profile"]["roots"].values()}
        if len(set(identities)) != 2 or set(identities) & roots:
            _fail("IDENTITY_ALIAS")
        _snapshot(value, min(self.maximum, limits["max_fact_bytes"]))

    def validate_run_attempt(self, value):
        _header(value, "run_attempt", {"request_sha256", "operation_identity", "reserved_read_bytes",
            "reserved_write_bytes", "failure_reserve_bytes", "deadline_ms"})
        request = self.bind(value)
        _same(value["operation_identity"], request["target"]["operation_identity"])
        for key, limit in (("reserved_read_bytes", "max_read_bytes"), ("reserved_write_bytes", "max_write_bytes"),
                           ("failure_reserve_bytes", "max_failure_bytes"), ("deadline_ms", "deadline_ms")):
            _u(value[key], positive=True)
            _same(value[key], request["limits"][limit])

    def validate_source_metadata(self, value):
        _exact(value, {"format_version", "kind", "operation_id", "capsule", "node_observation", "source_projection"})
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail("VERSION_INVALID")
        _same(value["kind"], "personal_authority_raw_source_observation")
        request = self.request()
        limits = request["limits"]["capsule_limits"]
        capsule = c2.normalize(value["capsule"], limits=limits)
        _same(value["operation_id"], request["operation_id"])
        _same(capsule["operation_id"], value["operation_id"])
        _same(capsule["source_context"]["profile"], request["profile"])
        _same(capsule["coverage"], {"declared_tree": "complete", "declared_copies": "none", "reason": None})
        if capsule["copies"] or capsule["artifacts"] or capsule["links"]:
            _fail("SOURCE_INVALID")
        projection.cross_validate_capsule(value["source_projection"], capsule, limits=limits)
        observation = value["node_observation"]
        _exact(observation, {"state", "entry_id"})
        if any("/" not in row["relative_key"] and row["relative_key"].rstrip(" .").casefold() == "node-identity.json"
               for row in capsule["directories"]):
            _fail("SOURCE_INVALID")
        variants = [row for row in capsule["files"] if "/" not in row["relative_key"]
                    and row["relative_key"].rstrip(" .").casefold() == "node-identity.json"]
        state = observation["state"]
        if not variants:
            _same(observation, {"state": "missing", "entry_id": None})
        else:
            if len(variants) != 1:
                _fail("SOURCE_INVALID")
            row = variants[0]
            _same(observation["entry_id"], row["entry_id"])
            if row["relative_key"] != "node-identity.json":
                _same(state, "noncanonical_name")
            elif row["byte_size"] > request["limits"]["node_json_bytes"]:
                _same(state, "json_limit")
            elif state not in {"parsed", "invalid"}:
                _fail("SOURCE_INVALID")
        if (state == "parsed") != (capsule["source_context"]["node"] is not None):
            _fail("SOURCE_INVALID")
        if state == "parsed":
            _uuid(capsule["source_context"]["node"]["node_id"])

    def validate_source_projection(self, value):
        limits = self.request()["limits"]["capsule_limits"]
        projection.normalize(value, limits=limits)
        _same(value, self.source()["source_projection"])

    def validate_selection(self, value):
        _header(value, "selection", {"request_sha256", "source_metadata_sha256", "source_projection_sha256", "source_file_ids"})
        request = self.bind(value)
        self.load("attempts/" + request["run_id"] + ".json", "run_attempt")
        source = self.source()
        self.load("source-projection.json", "source_projection")
        _same(value["source_metadata_sha256"], self.sha("source-metadata.json"))
        _same(value["source_projection_sha256"], self.sha("source-projection.json"))
        _same(value["source_file_ids"], [row["entry_id"] for row in source["capsule"]["files"]])

    def validate_copy_attempt(self, value):
        _header(value, "copy_attempt", {"request_sha256", "selection_sha256", "copy_id", "source_entry_id",
            "target_relative_key", "target_parent_identity", "sha256", "byte_size"})
        self.bind(value)
        self.selection()
        _same(value["selection_sha256"], self.sha("selection.json"))
        _uuid(value["copy_id"])
        entry = self.source_entry(value["source_entry_id"])
        _same(value["target_relative_key"], f"payload/{value['copy_id']}/{entry['entry_id']}.raw")
        if c2._directory_identity(value["target_parent_identity"]) in self.copy_forbidden_identities():
            _fail("IDENTITY_ALIAS")
        _sha(value["sha256"])
        _u(value["byte_size"])
        _same(value["sha256"], entry["sha256"])
        _same(value["byte_size"], entry["byte_size"])

    def validate_copy_record(self, value):
        _header(value, "copy_record", {"request_sha256", "selection_sha256", "copy_attempt_sha256", "copy"})
        self.bind(value)
        copy = value["copy"]
        _exact(copy, c2._COPY)
        entry = self.source_entry(copy["source_entry_id"])
        _same(value["selection_sha256"], self.sha("selection.json"))
        _key(copy["target_relative_key"])
        parts = copy["target_relative_key"].split("/")
        if len(parts) != 3 or parts[0] != "payload":
            _fail("COPY_INVALID")
        path = "copy-attempts/" + parts[1] + ".json"
        attempt = self.load(path, "copy_attempt")
        _same(value["copy_attempt_sha256"], self.sha(path))
        for key in ("source_entry_id", "target_relative_key", "target_parent_identity", "sha256", "byte_size"):
            _same(copy[key], attempt[key])
        identity = c2._file_pair(copy["copy_path_identity"], copy["copy_handle_identity"], entry["byte_size"],
                                 self.request()["limits"]["capsule_limits"])
        if (identity in self.copy_forbidden_identities()
                or identity == c2._directory_identity(copy["target_parent_identity"])):
            _fail("IDENTITY_ALIAS")

    def records(self):
        return [self.load("copy-records/" + entry_id + ".json", "copy_record")
                for entry_id in self.selection()["source_file_ids"]]

    def validate_capsule(self, value):
        limits = self.request()["limits"]["capsule_limits"]
        c2.normalize(value, limits=limits)
        source = self.source()["capsule"]
        for key in source.keys() - {"copies", "coverage"}:
            _same(value[key], source[key])
        _same(value["copies"], [row["copy"] for row in self.records()])
        expected = dict(source["coverage"], declared_copies="complete" if source["files"] else "none")
        _same(value["coverage"], expected)
        projection.cross_validate_capsule(self.source()["source_projection"], value, limits=limits)

    def counters(self, value, *, success):
        _exact(value, _COUNTERS)
        for count in value.values():
            _u(count)
        limits = self.request()["limits"]
        if value["read_bytes"] > limits["max_read_bytes"]:
            _fail("LIMIT")
        if value["confirmed_write_bytes"] + value["uncertain_write_bytes"] > limits["max_write_bytes"]:
            _fail("LIMIT")
        if success and (value["uncertain_read_calls"] or value["uncertain_write_bytes"]
                        or value["elapsed_ns"] > limits["deadline_ms"] * 1_000_000
                        or value["confirmed_write_bytes"] + limits["max_failure_bytes"] > limits["max_write_bytes"]):
            _fail("LIMIT")

    def expected_b_paths(self):
        request = self.request()
        paths = {"request.json", f"attempts/{request['run_id']}.json", "source-metadata.json",
                 "source-projection.json", "selection.json", "capsule.json"}
        for record in self.records():
            copy = record["copy"]
            copy_id = copy["target_relative_key"].split("/")[1]
            paths.update({copy["target_relative_key"], f"copy-attempts/{copy_id}.json",
                          f"copy-records/{copy['source_entry_id']}.json"})
        return paths

    def target_closed(self, value):
        _header(value, "target_closed", {"request_sha256", "selection_sha256", "operation_identity",
                                        "directories", "files", "counters"})
        request = self.bind(value)
        _same(value["selection_sha256"], self.sha("selection.json"))
        _same(value["operation_identity"], request["target"]["operation_identity"])
        limits = request["limits"]
        dirs = _ordered(value["directories"], {"relative_key", "identity", "children"}, "relative_key", limits["max_target_entries"])
        files = _ordered(value["files"], {"relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"},
                         "relative_key", limits["max_target_entries"])
        if len(dirs) + len(files) > limits["max_target_entries"]:
            _fail("LIMIT")
        by_directory = {row["relative_key"]: row for row in dirs}
        by_file = {row["relative_key"]: row for row in files}
        expected = self.expected_b_paths()
        expected_dirs = {"", "attempts", "copy-attempts", "payload", "copy-records", "failures"}
        expected_dirs.update(posixpath.dirname(path) for path in expected if path.startswith("payload/"))
        _same(set(by_directory), expected_dirs, "INVENTORY_MISMATCH")
        _same(set(by_file), expected, "INVENTORY_MISMATCH")
        controls = expected - {row["copy"]["target_relative_key"] for row in self.records()}
        if len(controls) > limits["max_control_files"]:
            _fail("LIMIT")
        _same(by_directory[""]["identity"], request["target"]["operation_identity"])
        source = self.source()["capsule"]
        forbidden = {c2._directory_identity(row["source_identity"]) for row in source["directories"]}
        forbidden.update(c2._directory_identity(row["identity"]) for row in request["profile"]["roots"].values())
        forbidden.add(c2._directory_identity(request["target"]["namespace_identity"]))
        forbidden.update((row["source_path_identity"]["device"], row["source_path_identity"]["inode"]) for row in source["files"])
        identities = set()
        all_paths = set(by_directory) | set(by_file)
        children_by_parent = {key: [] for key in by_directory}
        for path in all_paths:
            if path:
                children_by_parent[posixpath.dirname(path)].append(posixpath.basename(path))
        for children in children_by_parent.values():
            children.sort(key=lambda name: name.encode("utf-8"))
        for row in dirs:
            key = row["relative_key"]
            _key(key, root=True, depth=limits["max_target_depth"])
            identity = c2._directory_identity(row["identity"])
            if identity in identities or identity in forbidden:
                _fail("IDENTITY_ALIAS")
            identities.add(identity)
            _same(row["children"], children_by_parent[key], "INVENTORY_MISMATCH")
        copies = {row["copy"]["target_relative_key"]: row["copy"] for row in self.records()}
        physical_limits = dict(limits["capsule_limits"], max_total_bytes=max(limits["capsule_limits"]["max_total_bytes"], limits["max_fact_bytes"]))
        for row in files:
            key = row["relative_key"]
            _key(key, depth=limits["max_target_depth"])
            _same(row["parent_identity"], by_directory[posixpath.dirname(key)]["identity"])
            identity = c2._file_pair(row["path_identity"], row["handle_identity"], row["byte_size"], physical_limits)
            if identity in identities or identity in forbidden:
                _fail("IDENTITY_ALIAS")
            identities.add(identity)
            _sha(row["sha256"])
            if key in copies:
                copy = copies[key]
                for local, original in (("path_identity", "copy_path_identity"), ("handle_identity", "copy_handle_identity"),
                                        ("parent_identity", "target_parent_identity"), ("sha256", "sha256"), ("byte_size", "byte_size")):
                    _same(row[local], copy[original])
            else:
                _same(row["sha256"], self.sha(key))
                _same(row["byte_size"], len(self.bytes(key)))
        self.counters(value["counters"], success=True)
        file_bytes = sum(row["byte_size"] for row in files)
        if file_bytes + limits["max_failure_bytes"] > limits["max_write_bytes"]:
            _fail("LIMIT")
        if (value["counters"]["confirmed_write_bytes"] < file_bytes
                or file_bytes and value["counters"]["write_calls"] == 0):
            _fail("BINDING_MISMATCH")
        return by_file

    def validate_source_verified(self, value):
        _header(value, "source_verified", {"request_sha256", "selection_sha256", "capsule_sha256",
            "source_closed", "target_closed", "copy_records"})
        request = self.bind(value)
        self.selection()
        self.load("capsule.json", "capsule")
        _same(value["selection_sha256"], self.sha("selection.json"))
        _same(value["capsule_sha256"], self.sha("capsule.json"))
        closed = value["source_closed"]
        _exact(closed, _SOURCE_CLOSED)
        if type(closed["format_version"]) is not int or closed["format_version"] != 1:
            _fail("VERSION_INVALID")
        _same(closed["kind"], "personal_authority_raw_census_closed")
        _same(closed["operation_id"], request["operation_id"])
        _same(closed["source_projection_sha256"], self.sha("source-projection.json"))
        _same(closed["source_metadata_sha256"], self.sha("source-metadata.json"))
        for key in ("read_bytes", "read_calls", "seek_calls", "source_file_count", "source_byte_size"):
            _u(closed[key])
        source_files = self.source()["capsule"]["files"]
        _same(closed["source_file_count"], len(source_files))
        _same(closed["source_byte_size"], sum(row["byte_size"] for row in source_files))
        if (closed["read_bytes"] < closed["source_byte_size"]
                or source_files and (closed["read_calls"] == 0 or closed["seek_calls"] == 0)):
            _fail("BINDING_MISMATCH")
        if closed["read_bytes"] > request["limits"]["source_max_read_bytes"]:
            _fail("LIMIT")
        self.target_closed(value["target_closed"])
        for key in ("read_bytes", "read_calls", "seek_calls"):
            if value["target_closed"]["counters"][key] < closed[key]:
                _fail("BINDING_MISMATCH")
        expected = [{"source_entry_id": entry_id, "sha256": self.sha(f"copy-records/{entry_id}.json"),
                     "byte_size": len(self.bytes(f"copy-records/{entry_id}.json"))}
                    for entry_id in self.selection()["source_file_ids"]]
        _same(value["copy_records"], expected)

    def validate_manifest(self, value):
        _header(value, "manifest", {"scope", "request_sha256", "selection_sha256", "source_verified_sha256",
                                   "outcome", "facts", "payloads"})
        self.bind(value)
        verified = self.load("source-verified.json", "source_verified")
        _same(value["scope"], SCOPE)
        _same(value["selection_sha256"], self.sha("selection.json"))
        _same(value["source_verified_sha256"], self.sha("source-verified.json"))
        _same(value["outcome"], "copied" if self.selection()["source_file_ids"] else "empty_source")
        paths = sorted((self.expected_b_paths() - {row["copy"]["target_relative_key"] for row in self.records()}) | {"source-verified.json"})
        expected = [{"relative_key": path, "sha256": self.sha(path), "byte_size": len(self.bytes(path))} for path in paths]
        _same(value["facts"], expected)
        payloads = [{"source_entry_id": row["copy"]["source_entry_id"], "relative_key": row["copy"]["target_relative_key"],
                     "sha256": row["copy"]["sha256"], "byte_size": row["copy"]["byte_size"]} for row in self.records()]
        _same(value["payloads"], payloads)
        # Loading verified above closes every B inventory edge, not just hashes.
        _same(verified["operation_id"], value["operation_id"])

    def validate_receipt(self, value):
        _header(value, "receipt", {"scope", "request_sha256", "source_verified_sha256", "manifest_sha256", "outcome", "publication"})
        self.bind(value)
        manifest = self.load("manifest.json", "manifest")
        _same(value["scope"], SCOPE)
        _same(value["source_verified_sha256"], self.sha("source-verified.json"))
        _same(value["manifest_sha256"], self.sha("manifest.json"))
        _same(value["outcome"], manifest["outcome"])
        _same(value["publication"], "published_observed")

    def validate_failure(self, value):
        _header(value, "failure", {"request_sha256", "selection_sha256", "phase", "code", "counters", "effects", "effects_complete"})
        if value["phase"] not in _PHASES or value["code"] not in {"AUTHORITY_RAW_PRESERVATION_" + suffix for suffix in _FAILURE_SUFFIXES}:
            _fail("FAILURE_INVALID")
        if type(value["effects_complete"]) is not bool:
            _fail("TYPE_INVALID")
        if value["request_sha256"] is not None:
            request = self.bind(value)
            self.counters(value["counters"], success=False)
            maximum = request["limits"]["max_failure_bytes"]
        else:
            if value["selection_sha256"] is not None:
                _fail("BINDING_MISMATCH")
            _exact(value["counters"], _COUNTERS)
            for count in value["counters"].values():
                _u(count)
            counters = value["counters"]
            if (counters["read_bytes"] > 512 * 1024**3
                    or counters["confirmed_write_bytes"] + counters["uncertain_write_bytes"] > 128 * 1024**3):
                _fail("LIMIT")
            maximum = 16_384
        if value["selection_sha256"] is not None:
            self.selection()
            _same(value["selection_sha256"], self.sha("selection.json"))
        rows = _ordered(value["effects"], {"relative_key", "kind", "identity", "sha256", "byte_size"}, "relative_key", 16)
        for row in rows:
            _key(row["relative_key"], root=True)
            if row["kind"] not in {"directory", "file"}:
                _fail("FAILURE_INVALID")
            if row["identity"] is not None:
                c2._directory_identity(row["identity"])
            if row["kind"] == "directory":
                if row["sha256"] is not None or row["byte_size"] is not None:
                    _fail("FAILURE_INVALID")
            else:
                if row["sha256"] is not None:
                    _sha(row["sha256"])
                if row["byte_size"] is not None:
                    _u(row["byte_size"])
        _snapshot(value, min(self.maximum, maximum))


def _normalize(kind, value, fact_bytes, max_fact_bytes):
    graph = _Graph(fact_bytes, max_fact_bytes)
    # A pre-request diagnostic has no request dependency.
    if kind == "failure" and type(value) is dict and value.get("request_sha256") is None:
        copied = _snapshot(value, min(graph.maximum, 16_384))
        graph.validate_failure(copied)
        return copied
    return graph.normalize(kind, value)


@_checked
def normalize_request(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("request", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_run_attempt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("run_attempt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_selection(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("selection", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_copy_attempt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("copy_attempt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_copy_record(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("copy_record", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_source_verified(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("source_verified", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_manifest(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("manifest", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_receipt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("receipt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_failure(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("failure", value, fact_bytes, max_fact_bytes)


@_checked
def encode(kind, value, *, fact_bytes=None, max_fact_bytes=None):
    if type(kind) is not str or kind not in _KINDS:
        _fail("KIND_INVALID")
    copied = _normalize(kind, value, fact_bytes, max_fact_bytes)
    return _canonical(copied, _maximum(max_fact_bytes))


@_checked
def decode(kind, raw, *, fact_bytes=None, max_fact_bytes=None):
    if type(kind) is not str or kind not in _KINDS:
        _fail("KIND_INVALID")
    graph = _Graph(fact_bytes, max_fact_bytes)
    maximum = min(graph.maximum, 16_384) if kind == "failure" else graph.cap(kind)
    value = _parse(raw, maximum)
    if kind == "failure" and value.get("request_sha256") is None:
        graph.validate_failure(value)
        return value
    return graph.normalize(kind, value)


@_checked
def cross_validate_package(*, fact_bytes, max_fact_bytes=None):
    """Validate supplied control bytes only; never produce a completion token."""
    graph = _Graph(fact_bytes, max_fact_bytes)
    graph.load("receipt.json", "receipt")
    expected = (graph.expected_b_paths() - {row["copy"]["target_relative_key"] for row in graph.records()}) | {
        "source-verified.json", "manifest.json", "receipt.json"}
    _same(set(graph.raw), expected, "INVENTORY_MISMATCH")
    limits = graph.request()["limits"]
    if len(graph.raw) > limits["max_control_files"] or graph.total_bytes > limits["max_write_bytes"]:
        _fail("LIMIT")
    counters = graph.load("source-verified.json", "source_verified")["target_closed"]["counters"]
    after_b_bytes = sum(len(graph.bytes(path)) for path in ("source-verified.json", "manifest.json", "receipt.json"))
    # Only an unavoidable lower bound, never a fabricated final C counter:
    # each later fact must be written and independently read at least once.
    if (counters["confirmed_write_bytes"] + after_b_bytes + limits["max_failure_bytes"] > limits["max_write_bytes"]
            or counters["read_bytes"] + after_b_bytes > limits["max_read_bytes"]):
        _fail("LIMIT")
    return None
