"""Pure complete-tree R01 assembly from bounded RawFacts and declarations.

No input or output of this module establishes native observation or authority.
The fixed source caller separately binds the cache to its original readers and
executes the original public codecs.  Own work is counted at actual steps;
initial captures/sorts and one JSON encoder piece are not preemptible.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sys

from . import personal_authority_replica_path_roles as roles
from . import personal_authority_replica_rawfact as rawfact


_MAX_ENTRIES = 400064
_MAX_FACTS = 300032
_MAX_BYTES = 128 * 1024**3
_MAX_FILE = 64 * 1024**3
_U64 = 2**63 - 1
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FID = re.compile(r"f[0-9]{8}\Z")
_D = frozenset(("device", "inode"))
_F = _D | {"byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
_DIRECTORY = frozenset(("relative_key", "identity", "children"))
_FILE = frozenset(("relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"))
_CONCLUSIONS = (("evidence_kind", "declaration"), ("historical_factory_return", "unknown"),
    ("unrecorded_history_completeness", "not_established"), ("native_observation", False),
    ("data_restored", False), ("runtime_write", False), ("sender_authority", False))


class DeclarationError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_SOURCE_DECLARATION1_" + suffix
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class BuildProgress:
    phase: str
    structural_visits: int
    canonical_output_bytes: int
    pure_hash_input_bytes: int


@dataclass(frozen=True, slots=True)
class BuildResult:
    declaration: dict
    raw_control_refs: list
    directory_index: tuple
    file_index: tuple
    package_entry_set_sha256: str
    structural_visits: int
    canonical_output_bytes: int
    pure_hash_input_bytes: int


def _fail(suffix):
    raise DeclarationError(suffix) from None


def _exact(value, fields):
    if type(value) is not dict or any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    if frozenset(value) != fields:
        _fail("FIELDS_INVALID")


def _text(value):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    return value


def _sha(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail("DIGEST_INVALID")


def _uuid(value):
    if type(value) is not str or _UUID.fullmatch(value) is None or value == "00000000-0000-0000-0000-000000000000":
        _fail("UUID_INVALID")


def _uint(value, maximum=_U64):
    if type(value) is not int or not 0 <= value <= maximum:
        _fail("LIMIT")
    return value


class _Work:
    __slots__ = ("visits", "canonical", "hashed")

    def __init__(self):
        self.visits = self.canonical = self.hashed = 0


def _visit(work):
    work.visits = _uint(work.visits + 1)
    if work.visits % 32768 == 0:
        yield BuildProgress("structural", work.visits, work.canonical, work.hashed)


def _clone_steps(value, work, depth=0):
    if depth > 64:
        _fail("LIMIT")
    yield from _visit(work)
    kind = type(value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            _fail("TYPE_INVALID")
        result = {}
        for key, child in value.items():
            yield from _visit(work)
            result[key] = yield from _clone_steps(child, work, depth + 1)
        return result
    if kind is list:
        result = []
        for child in value:
            result.append((yield from _clone_steps(child, work, depth + 1)))
        return result
    if kind in (str, int, bool) or value is None:
        return value
    _fail("TYPE_INVALID")


def _thaw_steps(value, work, depth=0):
    if depth > 64:
        _fail("LIMIT")
    yield from _visit(work)
    kind = type(value)
    if kind is rawfact.JsonObject:
        pairs = value.items
        if type(pairs) is not tuple:
            _fail("TYPE_INVALID")
        result = {}
        for pair in pairs:
            if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str:
                _fail("TYPE_INVALID")
            if pair[0] in result:
                _fail("FIELDS_INVALID")
            yield from _visit(work)
            result[pair[0]] = yield from _thaw_steps(pair[1], work, depth + 1)
        return result
    if kind is rawfact.JsonArray:
        if type(value.values) is not tuple:
            _fail("TYPE_INVALID")
        result = []
        for child in value.values:
            result.append((yield from _thaw_steps(child, work, depth + 1)))
        return result
    if kind in (str, int, bool) or value is None:
        return value
    _fail("TYPE_INVALID")


def _digest_steps(value, work, *, domain=None):
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    hasher = hashlib.sha256()
    if domain is not None:
        prefix = b"reno-authority-replica-runtime-v1\0" + domain.encode("ascii") + b"\0"
        hasher.update(prefix)
        work.hashed = _uint(work.hashed + len(prefix))
    for piece in encoder.iterencode(value):
        # Production of a single encoder piece is not preemptible.  Its UTF-8
        # encoding/hash is sliced using the same 32768 character bound as r775.
        for offset in range(0, len(piece), 32768):
            raw = piece[offset:offset + 32768].encode("utf-8")
            hasher.update(raw)
            work.canonical = _uint(work.canonical + len(raw))
            work.hashed = _uint(work.hashed + len(raw))
            yield BuildProgress("canonical_hash", work.visits, work.canonical, work.hashed)
    return hasher.hexdigest()


def _identity(value, fields):
    _exact(value, fields)
    for key in ("device", "inode"):
        text = _text(value[key])
        if (not text or len(text) > 40 or not text.isascii() or not text.isdecimal() or str(int(text)) != text
                or key == "inode" and text == "0"):
            _fail("IDENTITY_INVALID")
    return value["device"], value["inode"]


def build_declaration_steps(cache, *, directories, files, generation,
                            replica_id, observation_scope_id, profile_sha256, node_id):
    """Assemble only a declaration; use the public original codecs afterwards."""
    if type(cache) is not rawfact._RawFactCache:
        _fail("TYPE_INVALID")
    if type(directories) is not list or type(files) is not list:
        _fail("TYPE_INVALID")
    if type(generation) is not int or generation not in (1, 2):
        _fail("GENERATION_INVALID")
    for value in (replica_id, observation_scope_id, node_id):
        _uuid(value)
    _sha(profile_sha256)
    if len(directories) + len(files) > _MAX_ENTRIES or len(cache._facts) > _MAX_FACTS:
        _fail("LIMIT")
    work = _Work()
    dirs = yield from _clone_steps(directories, work)
    rows = yield from _clone_steps(files, work)
    yield BuildProgress("snapshot_complete", work.visits, work.canonical, work.hashed)
    for row in dirs:
        _exact(row, _DIRECTORY)
        _text(row["relative_key"])
    for row in rows:
        _exact(row, _FILE)
        _text(row["relative_key"])
    dirs.sort(key=lambda row: _text(row["relative_key"]).encode("utf-8"))
    rows.sort(key=lambda row: _text(row["relative_key"]).encode("utf-8"))
    docs, facts = {}, {}
    for fact in cache._facts:
        if type(fact) is not rawfact.RawFact or type(fact.relative_key) is not str or type(fact.raw) is not bytes:
            _fail("TYPE_INVALID")
        if fact.relative_key in facts:
            _fail("FACT_SET_INVALID")
        _uint(fact.length)
        _sha(fact.sha256)
        if fact.length != len(fact.raw):
            _fail("FACT_SET_INVALID")
        docs[fact.relative_key] = yield from _thaw_steps(fact.document, work)
        facts[fact.relative_key] = fact
    try:
        request_fact = facts["request.json"]
        request = docs["request.json"]
        operation_id = request["operation_id"]
        _uuid(operation_id)
        base = f"runs/{generation:02d}/"
        origin = {"format_version": 2, "family": ".reno-authority-raw-preservation-v2",
            "operation_id": operation_id, "generation": generation}
        for name in ("request", "selection", "source-metadata", "source-projection",
                     "capsule", "source-verified", "manifest", "receipt"):
            path = ("" if name in ("request", "selection", "source-metadata", "source-projection") else base) + name + ".json"
            origin[name.replace("-", "_") + "_sha256"] = facts[path].sha256
    except (KeyError, TypeError):
        _fail("PACKAGE_INCOMPLETE")
    by_dir, by_file, identities, children = {}, {}, set(), {}
    for row in dirs:
        _exact(row, _DIRECTORY)
        key = _text(row["relative_key"])
        roles.classify_directory(key, expected_request_sha256=request_fact.sha256)
        identity = _identity(row["identity"], _D)
        if key in by_dir or identity in identities or type(row["children"]) is not list:
            _fail("INVENTORY_INVALID")
        if any(type(name) is not str for name in row["children"]):
            _fail("TYPE_INVALID")
        by_dir[key], children[key] = row, []
        identities.add(identity)
        yield from _visit(work)
    if "" not in by_dir:
        _fail("INVENTORY_INVALID")
    package_files, total = [], 0
    for row in rows:
        _exact(row, _FILE)
        key = _text(row["relative_key"])
        role = roles.classify_file(key)
        if key in by_file or key in by_dir:
            _fail("INVENTORY_INVALID")
        identity = _identity(row["path_identity"], _F)
        if _identity(row["handle_identity"], _F) != identity or identity in identities:
            _fail("IDENTITY_INVALID")
        _identity(row["parent_identity"], _D)
        size = _uint(row["byte_size"], _MAX_FILE)
        for observed in (row["path_identity"], row["handle_identity"]):
            if (type(observed["byte_size"]) is not int or observed["byte_size"] != size or type(observed["mode"]) is not int
                    or not 0 <= observed["mode"] <= 2**32 - 1
                    or observed["mode"] & 0o170000 != 0o100000 or type(observed["nlink"]) is not int
                    or observed["nlink"] != 1):
                _fail("IDENTITY_INVALID")
            for field in ("mtime_ns", "ctime_ns"):
                text = _text(observed[field])
                if not text or len(text) > 40 or not text.isascii() or not text.isdecimal() or str(int(text)) != text:
                    _fail("IDENTITY_INVALID")
        if any(row["path_identity"][field] != row["handle_identity"][field] for field in _F - {"ctime_ns"}):
            _fail("IDENTITY_INVALID")
        _sha(row["sha256"])
        total = _uint(total + size, _MAX_BYTES)
        if role == "control":
            fact = facts.get(key)
            if fact is None or fact.length != size or fact.sha256 != row["sha256"]:
                _fail("FACT_SET_INVALID")
        elif role == "marker" and (size != 0 or row["sha256"] != hashlib.sha256(b"").hexdigest()):
            _fail("INVENTORY_INVALID")
        elif role == "retained" and "/failures/" in key and size > 16384:
            _fail("LIMIT")
        identities.add(identity)
        by_file[key] = row
        package_files.append({"relative_key": key, "role": role, "sha256": row["sha256"], "byte_size": size})
        yield from _visit(work)
    if set(facts) != {row["relative_key"] for row in package_files if row["role"] == "control"}:
        _fail("FACT_SET_INVALID")
    for key in (*by_dir, *by_file):
        if not key:
            continue
        parent, _, name = key.rpartition("/")
        if parent not in children:
            _fail("INVENTORY_INVALID")
        children[parent].append(name)
        if key in by_file and by_file[key]["parent_identity"] != by_dir[parent]["identity"]:
            _fail("IDENTITY_INVALID")
        yield from _visit(work)
    for key, names in children.items():
        names.sort(key=lambda name: name.encode("utf-8"))
        if by_dir[key]["children"] != names or len({name.casefold() for name in names}) != len(names):
            _fail("INVENTORY_INVALID")
    origin["fact_set_sha256"] = yield from _digest_steps(
        [[key, facts[key].sha256, facts[key].length] for key in sorted(facts, key=lambda key: key.encode("utf-8"))], work)
    current = {"observation_scope_id": observation_scope_id, "profile_sha256": profile_sha256,
        "node_id": node_id, "directories": dirs, "files": rows}
    current["inventory_sha256"] = yield from _digest_steps({"directories": dirs, "files": rows}, work)
    package = {"mapping": "same-relative-key", "directories": list(by_dir), "files": package_files}
    package["content_set_sha256"] = yield from _digest_steps(package, work)
    source_map, seen_records, seen_payloads = [], set(), set()
    try:
        selected = docs[base + "source-verified.json"]["selected_copies"]
        selected_ids = docs["selection.json"]["source_file_ids"]
        if type(selected) is not list or type(selected_ids) is not list or len(selected) > 100000:
            _fail("TYPE_INVALID")
        for item in selected:
            _exact(item, frozenset(("source_entry_id", "origin_slot", "origin_run_id", "copy_record_relative_key", "copy_record_sha256", "copy_record_byte_size")))
            source_id = _text(item["source_entry_id"])
            if _FID.fullmatch(source_id) is None or type(item["origin_slot"]) is not int or item["origin_slot"] not in (1, 2):
                _fail("MAPPING_INVALID")
            key = item["copy_record_relative_key"]
            if type(key) is not str or key != f"runs/{item['origin_slot']:02d}/copy-records/{source_id}.json" or key in seen_records:
                _fail("MAPPING_INVALID")
            fact, record = facts[key], docs[key]
            copied = record["copy"]
            payload_key = copied["target_relative_key"]
            payload = by_file[payload_key]
            if (type(payload_key) is not str or payload_key in seen_payloads or roles.classify_file(payload_key) != "payload"
                    or copied["source_entry_id"] != source_id or record["slot"] != item["origin_slot"]
                    or record["run_id"] != item["origin_run_id"] or record["operation_id"] != operation_id
                    or item["copy_record_sha256"] != fact.sha256 or item["copy_record_byte_size"] != fact.length
                    or payload["sha256"] != copied["sha256"] or payload["byte_size"] != copied["byte_size"]):
                _fail("MAPPING_INVALID")
            source_map.append({"source_entry_id": source_id, "origin_slot": record["slot"],
                "origin_run_id": record["run_id"], "copy_record_relative_key": key, "copy_record_sha256": fact.sha256,
                "payload_relative_key": payload_key, "sha256": payload["sha256"], "byte_size": payload["byte_size"]})
            seen_records.add(key)
            seen_payloads.add(payload_key)
            yield from _visit(work)
        if [row["source_entry_id"] for row in source_map] != selected_ids or selected_ids != sorted(set(selected_ids)):
            _fail("MAPPING_INVALID")
    except (KeyError, TypeError):
        _fail("MAPPING_INVALID")
    declaration = {"format_version": 1, "kind": "personal_authority_raw_replica", "scope": "raw2-replica-declaration-v1",
        "replica_id": replica_id, "origin": origin, "current": current, "package_map": package,
        "source_map": source_map, "conclusions": dict(_CONCLUSIONS)}
    d_index = tuple((f"d{index:08d}", row) for index, row in enumerate(dirs))
    p_index = tuple((f"p{index:08d}", row) for index, row in enumerate(rows))
    entry_sha = yield from _digest_steps({"directories": [list(pair) for pair in d_index],
        "files": [list(pair) for pair in p_index]}, work, domain="package-entry-set")
    refs = [{key: row[key] for key in ("relative_key", "sha256", "byte_size")} for row in package_files if row["role"] == "control"]
    yield BuildProgress("complete", work.visits, work.canonical, work.hashed)
    return BuildResult(declaration, refs, d_index, p_index, entry_sha, work.visits, work.canonical, work.hashed)


def _catalog_extra_original():
    """Fixed bootstrap graph; no caller module or runtime discovery input."""
    constants = tuple(name for name in vars(sys.modules[__name__]) if name.startswith("_") and name.upper() == name)
    role_constants = tuple(name for name in vars(roles) if name.startswith("_") and name.upper() == name)
    return ((sys.modules[__name__], constants + ("roles", "rawfact", "hashlib", "json", "re", "sys", "dataclass")),
        (roles, role_constants + ("classify_file", "classify_directory", "_key", "_uuid", "_entry", "_sha", "_fail", "AuthorityReplicaPathRoleError")),
        (hashlib, ("sha256",)), (json, ("JSONEncoder",)),
        (json.encoder, ("JSONEncoder", "encode_basestring", "encode_basestring_ascii", "c_make_encoder", "_make_iterencode", "ESCAPE_DCT", "ESCAPE", "ESCAPE_ASCII", "INFINITY")),
        (re, ("compile", "fullmatch")))
