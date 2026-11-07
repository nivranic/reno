"""Pure raw2 replica declarations, never native observations or restore grants.

Original control bytes retain their historical identities and interpretation.
The separate current inventory declares new identities at the same relative
paths. No supplied dictionary proves a real census, a historical factory
return, normal resource closure, restored data, or current write/send authority.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import posixpath
import re

from . import personal_authority_preservation_contract as census
from . import personal_authority_raw_preservation_contract as raw1
from . import personal_authority_raw_preservation2_contract as raw2


FORMAT_VERSION = 1
KIND = "personal_authority_raw_replica"
SCOPE = "raw2-replica-declaration-v1"

_MAX_JSON = 128 * 1024**2
_MAX_REQUEST = 1024**2
_MAX_FACTS = 300_032
_MAX_ENTRIES = 400_064
_SLOT_BYTES = 64 * 1024**3
_TOTAL_BYTES = 128 * 1024**3
_FAILURE_BYTES = 16_384
_FAMILY = ".reno-authority-raw-preservation-v2"
_CLAIM = "resume-slot-02.claim"
_ANCHOR = "admission-q1-sha256-"
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY = re.compile(r"f[0-9]{8}\Z")
_COPY_ATTEMPT = re.compile(rf"runs/(01|02)/copy-attempts/({_UUID_TEXT})\.json\Z")
_COPY_RECORD = re.compile(r"runs/(01|02)/copy-records/(f[0-9]{8})\.json\Z")
_PAYLOAD = re.compile(rf"runs/(01|02)/payload/({_UUID_TEXT})/(f[0-9]{{8}})\.raw\Z")
_FAILURE = re.compile(rf"runs/(01|02)/failures/({_UUID_TEXT})\.json\Z")
_TEMP = re.compile(rf"runs/(01|02)/\.write-({_UUID_TEXT})\.tmp\Z")
_ROOT_CONTROLS = frozenset({"request.json", "source-metadata.json", "source-projection.json", "selection.json"})
_LOCAL_CONTROLS = frozenset({"run-attempt.json", "capsule.json", "source-verified.json", "manifest.json", "receipt.json"})
_FIXED_DIRS = frozenset({"copy-attempts", "copy-records", "payload", "failures"})
_TOP = frozenset({"format_version", "kind", "scope", "replica_id", "origin", "current", "package_map", "source_map", "conclusions"})
_ORIGIN_HASHES = frozenset({"request_sha256", "selection_sha256", "source_metadata_sha256", "source_projection_sha256",
    "capsule_sha256", "source_verified_sha256", "manifest_sha256", "receipt_sha256", "fact_set_sha256"})
_ORIGIN = _ORIGIN_HASHES | {"format_version", "family", "operation_id", "generation"}
_CURRENT = frozenset({"observation_scope_id", "profile_sha256", "node_id", "directories", "files", "inventory_sha256"})
_DIRECTORY_ROW = frozenset({"relative_key", "identity", "children"})
_FILE_ROW = frozenset({"relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"})
_PACKAGE = frozenset({"mapping", "directories", "files", "content_set_sha256"})
_PACKAGE_FILE = frozenset({"relative_key", "role", "sha256", "byte_size"})
_SOURCE_ROW = frozenset({"source_entry_id", "origin_slot", "origin_run_id", "copy_record_relative_key",
    "copy_record_sha256", "payload_relative_key", "sha256", "byte_size"})
_CONCLUSIONS = (
    ("evidence_kind", "declaration"), ("historical_factory_return", "unknown"),
    ("unrecorded_history_completeness", "not_established"),
    ("native_observation", False), ("data_restored", False),
    ("runtime_write", False), ("sender_authority", False),
)


class AuthorityReplicaContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA1_" + suffix
        super().__init__(self.code)


Error = AuthorityReplicaContractError


def _fail(suffix):
    raise Error(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AuthorityReplicaContractError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError) as exc:
            raise Error("TYPE_INVALID") from exc
    return checked


def _local_error(exc):
    suffix = exc.code.rsplit("_", 1)[-1]
    if suffix == "LIMIT":
        return "LIMIT"
    if exc.code.endswith("JSON_NONCANONICAL"):
        return "NOT_CANONICAL"
    if exc.code.endswith(("JSON_INVALID", "JSON_DUPLICATE_KEY")):
        return "JSON_INVALID"
    return "TYPE_INVALID"


def _snapshot(value):
    try:
        return raw1._snapshot(value, _MAX_JSON)
    except (raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error(_local_error(exc)) from exc


def _canonical(value):
    try:
        return raw1._canonical(value, _MAX_JSON)
    except (raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error(_local_error(exc)) from exc


def _parse(raw, maximum):
    try:
        return raw1._parse(raw, maximum)
    except (raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error(_local_error(exc)) from exc


def _exact(value, fields):
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    if set(value) != fields:
        _fail("FIELDS_INVALID")


def _fixed(actual, expected, suffix="FIELDS_INVALID"):
    if type(actual) is not type(expected):
        _fail("TYPE_INVALID")
    if actual != expected:
        _fail(suffix)


def _uint(value, maximum=_TOTAL_BYTES):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if not 0 <= value <= maximum:
        _fail("LIMIT")
    return value


def _slot(value):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if value not in (1, 2):
        _fail("ORIGIN_INVALID")
    return value


def _sha(value):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    if not _SHA.fullmatch(value):
        _fail("ORIGIN_INVALID")
    return value


def _uuid(value):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    try:
        return raw1._uuid(value)
    except (raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error("ORIGIN_INVALID") from exc


def _path(value, *, root=False):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    try:
        return raw1._key(value, root=root)
    except (raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error("PATH_INVALID") from exc


def _run(slot):
    return f"runs/{slot:02d}"


def _late(slot):
    return {_run(slot) + "/" + name for name in ("source-verified.json", "manifest.json", "receipt.json")}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_digest(value):
    return _digest(_canonical(value))


def _role(key):
    _path(key)
    if key == _CLAIM:
        return "marker"
    if key in _ROOT_CONTROLS:
        return "control"
    parts = key.split("/")
    if len(parts) == 3 and parts[0] == "runs" and parts[1] in ("01", "02") and parts[2] in _LOCAL_CONTROLS:
        return "control"
    for pattern, role in ((_COPY_ATTEMPT, "control"), (_TEMP, "retained"), (_FAILURE, "retained"), (_PAYLOAD, "payload")):
        match = pattern.fullmatch(key)
        if match:
            _uuid(match[2])
            return role
    if _COPY_RECORD.fullmatch(key):
        return "control"
    _fail("PATH_INVALID")


def _directory_path(key, anchor):
    _path(key, root=True)
    if key in {"", "runs", anchor}:
        return
    parts = key.split("/")
    if len(parts) >= 2 and parts[0] == "runs" and parts[1] in ("01", "02"):
        if len(parts) == 2 or len(parts) == 3 and parts[2] in _FIXED_DIRS:
            return
        if len(parts) == 4 and parts[2] == "payload":
            _uuid(parts[3])
            return
    _fail("PATH_INVALID")


def _ordered(rows, fields, *, key="relative_key", maximum=_MAX_ENTRIES):
    if type(rows) is not list:
        _fail("TYPE_INVALID")
    if len(rows) > maximum:
        _fail("LIMIT")
    previous = None
    for row in rows:
        _exact(row, fields)
        value = row[key]
        if type(value) is not str:
            _fail("TYPE_INVALID")
        encoded = value.encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail("INVENTORY_INVALID")
        previous = encoded
    return rows


def _origin(value):
    _exact(value, _ORIGIN)
    _fixed(value["format_version"], 2, "VERSION_INVALID")
    _fixed(value["family"], _FAMILY, "VERSION_INVALID")
    _uuid(value["operation_id"])
    _slot(value["generation"])
    for key in _ORIGIN_HASHES:
        _sha(value[key])


def _facts(value, origin):
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if len(value) > _MAX_FACTS:
        _fail("LIMIT")
    # Reject every outer key type before any lookup or comparison can invoke
    # the equality hook of a colliding str subclass.
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    raw, total = {}, 0
    for key, content in value.items():
        if type(content) is not bytes:
            _fail("TYPE_INVALID")
        if _role(key) != "control":
            _fail("FACT_SET_INVALID")
        if len(content) > (_MAX_REQUEST if key == "request.json" else _MAX_JSON):
            _fail("LIMIT")
        total += len(content)
        if total > _TOTAL_BYTES:
            _fail("LIMIT")
        raw[key] = content
    required = _ROOT_CONTROLS | {_run(origin["generation"]) + "/" + name for name in
                               ("capsule.json", "source-verified.json", "manifest.json", "receipt.json")}
    if not required <= set(raw):
        _fail("PACKAGE_INCOMPLETE")
    try:
        # Historical identities are checked only against original historical
        # records. No fabricated old inventory and no current D/F enters here.
        raw2.cross_validate_prefix(fact_bytes=raw, inventory=None)
    except (raw2.AuthorityRawPreservation2ContractError,
            raw1.AuthorityRawPreservationContractError, census.AuthorityPreservationContractError) as exc:
        raise Error("HISTORY_INVALID") from exc
    # Prefix already parsed and validated every control. A separate linear
    # decode supplies data, without repeatedly rebuilding an O(n) graph per
    # copy record and without borrowing any runtime owner.
    documents = {key: _parse(content, _MAX_REQUEST if key == "request.json" else _MAX_JSON)
                 for key, content in raw.items()}
    hashes = {key: _digest(content) for key, content in raw.items()}
    if documents["request.json"]["operation_id"] != origin["operation_id"]:
        _fail("ORIGIN_INVALID")
    paths = {"request_sha256": "request.json", "selection_sha256": "selection.json",
        "source_metadata_sha256": "source-metadata.json", "source_projection_sha256": "source-projection.json"}
    for stem in ("capsule", "source_verified", "manifest", "receipt"):
        paths[stem + "_sha256"] = _run(origin["generation"]) + "/" + stem.replace("_", "-") + ".json"
    for field, key in paths.items():
        if origin[field] != hashes[key]:
            _fail("ORIGIN_INVALID")
    fact_set = [[key, hashes[key], len(raw[key])] for key in sorted(raw, key=lambda item: item.encode("utf-8"))]
    if origin["fact_set_sha256"] != _json_digest(fact_set):
        _fail("FACT_SET_INVALID")
    return raw, documents, hashes


def _directory_identity(value):
    try:
        return census._directory_identity(value)
    except census.AuthorityPreservationContractError as exc:
        raise Error("INVENTORY_INVALID") from exc


def _file_identity(row):
    try:
        # Preserve census2's intentional allowance for different ctime_ns.
        return census._file_pair(row["path_identity"], row["handle_identity"], row["byte_size"],
                                 {"max_total_bytes": _SLOT_BYTES})
    except census.AuthorityPreservationContractError as exc:
        raise Error("INVENTORY_INVALID") from exc


def _inventory(value, documents, hashes):
    _exact(value, _CURRENT)
    _uuid(value["observation_scope_id"])
    _uuid(value["node_id"])
    _sha(value["profile_sha256"])
    _sha(value["inventory_sha256"])
    directory_rows = _ordered(value["directories"], _DIRECTORY_ROW)
    file_rows = _ordered(value["files"], _FILE_ROW)
    limits = documents["request.json"]["slot_limits"]
    if len(directory_rows) + len(file_rows) > min(_MAX_ENTRIES, limits["max_target_entries"]):
        _fail("LIMIT")
    directories = {row["relative_key"]: row for row in directory_rows}
    files = {row["relative_key"]: row for row in file_rows}
    if "" not in directories or set(directories) & set(files):
        _fail("INVENTORY_INVALID")
    anchor = _ANCHOR + hashes["request.json"]
    roles, identities = {}, set()
    for key, row in directories.items():
        _directory_path(key, anchor)
        identity = _directory_identity(row["identity"])
        if identity in identities:
            _fail("IDENTITY_ALIAS")
        identities.add(identity)
        if type(row["children"]) is not list or any(type(child) is not str for child in row["children"]):
            _fail("TYPE_INVALID")
    children = {key: [] for key in directories}
    for key in set(directories) | set(files):
        if key:
            parent = posixpath.dirname(key)
            if parent not in children:
                _fail("INVENTORY_INVALID")
            children[parent].append(posixpath.basename(key))
    for key, expected in children.items():
        if len({child.casefold() for child in expected}) != len(expected):
            _fail("PATH_INVALID")
        if directories[key]["children"] != sorted(expected, key=lambda item: item.encode("utf-8")):
            _fail("INVENTORY_INVALID")
    slot_bytes, nonpayload = {1: 0, 2: 0}, 0
    for key, row in files.items():
        role = roles[key] = _role(key)
        _sha(row["sha256"])
        _uint(row["byte_size"], _SLOT_BYTES)
        _directory_identity(row["parent_identity"])
        if row["parent_identity"] != directories[posixpath.dirname(key)]["identity"]:
            _fail("INVENTORY_INVALID")
        identity = _file_identity(row)
        if identity in identities:
            _fail("IDENTITY_ALIAS")
        identities.add(identity)
        if role == "marker" and (row["byte_size"] != 0 or row["sha256"] != _EMPTY_SHA):
            _fail("CLAIM_INVALID")
        if role != "payload":
            nonpayload += 1
        slot_bytes[2 if key == _CLAIM or key.startswith("runs/02/") else 1] += row["byte_size"]
    if nonpayload > min(_MAX_FACTS, limits["max_control_files"]) or any(amount > _SLOT_BYTES for amount in slot_bytes.values()):
        _fail("LIMIT")
    if sum(slot_bytes.values()) > (_TOTAL_BYTES if _CLAIM in files else _SLOT_BYTES):
        _fail("LIMIT")
    if value["inventory_sha256"] != _json_digest({"directories": directory_rows, "files": file_rows}):
        _fail("INVENTORY_INVALID")
    return directories, files, roles, anchor


def _content(row, expected):
    if row["sha256"] != expected["sha256"] or row["byte_size"] != expected["byte_size"]:
        _fail("CONTENT_CHANGED")


def _logical(directories, files, roles, anchor, raw, documents, hashes):
    if {key for key, role in roles.items() if role == "control"} != set(raw):
        _fail("FACT_SET_INVALID")
    for key in raw:
        _content(files[key], {"sha256": hashes[key], "byte_size": len(raw[key])})
    initial = {"runs", "runs/01"} | {"runs/01/" + name for name in _FIXED_DIRS}
    if not initial <= set(directories):
        _fail("INVENTORY_INVALID")
    for slot in (1, 2):
        run = _run(slot)
        attempt_key = run + "/run-attempt.json"
        current_keys = set(directories) | set(files)
        if slot == 2 and any(key == run or key.startswith(run + "/") for key in current_keys) and _CLAIM not in files:
            _fail("CLAIM_INVALID")
        if attempt_key in documents:
            if anchor not in directories or directories[anchor]["children"]:
                _fail("INVENTORY_INVALID")
            if not {run + "/" + name for name in _FIXED_DIRS} <= set(directories):
                _fail("INVENTORY_INVALID")
        else:
            if any(key.startswith(run + "/") and not _TEMP.fullmatch(key) for key in files):
                _fail("INVENTORY_INVALID")
            if any(key.startswith(run + "/payload/") for key in directories):
                _fail("INVENTORY_INVALID")
    for key, row in files.items():
        payload = _PAYLOAD.fullmatch(key)
        failure = _FAILURE.fullmatch(key)
        if payload:
            attempt_key = f"runs/{payload[1]}/copy-attempts/{payload[2]}.json"
            attempt = documents.get(attempt_key)
            if attempt is None or attempt["target_relative_key"] != key or attempt["source_entry_id"] != payload[3]:
                _fail("INVENTORY_INVALID")
            record_key = f"runs/{payload[1]}/copy-records/{payload[3]}.json"
            if record_key in documents:
                _content(row, documents[record_key]["copy"])
        elif failure:
            attempt = documents.get(f"runs/{failure[1]}/run-attempt.json")
            if attempt is None or failure[2] != attempt["run_id"]:
                _fail("INVENTORY_INVALID")
            if row["byte_size"] > _FAILURE_BYTES:
                _fail("LIMIT")
    for key, document in documents.items():
        if _COPY_ATTEMPT.fullmatch(key):
            if posixpath.dirname(document["target_relative_key"]) not in directories:
                _fail("INVENTORY_INVALID")
        elif _COPY_RECORD.fullmatch(key):
            if document["copy"]["target_relative_key"] not in files:
                _fail("INVENTORY_INVALID")


def _historical_inventory(directories, files, documents):
    for slot in (1, 2):
        run = _run(slot)
        verified = documents.get(run + "/source-verified.json")
        if verified is None:
            continue
        before = verified["target_closed"]
        old_dirs = {row["relative_key"]: row for row in before["directories"]}
        old_files = {row["relative_key"]: row for row in before["files"]}
        if not set(old_dirs) <= set(directories) or not set(old_files) <= set(files):
            _fail("INVENTORY_INVALID")
        for key, original in old_files.items():
            _content(files[key], original)
        if run + "/receipt.json" not in documents:
            # A declared B remains history even before receipt publication.
            # It is a preservation lower bound, never a completed generation.
            for key, original in old_dirs.items():
                if not set(original["children"]) <= set(directories[key]["children"]):
                    _fail("INVENTORY_INVALID")
            continue
        late = _late(slot)
        allowed_files = set(old_files) | late
        allowed_dirs = set(old_dirs)
        if slot == 1:
            allowed_files.update(key for key in files if key == _CLAIM or key.startswith("runs/02/"))
            allowed_dirs.update(key for key in directories if key == "runs/02" or key.startswith("runs/02/"))
        if set(files) != allowed_files or set(directories) != allowed_dirs:
            _fail("INVENTORY_INVALID")
        for key, original in old_dirs.items():
            children = list(original["children"])
            if key == run:
                children.extend(posixpath.basename(path) for path in late)
            if slot == 1 and key == "" and _CLAIM in files:
                children.append(_CLAIM)
            if slot == 1 and key == "runs" and "runs/02" in directories:
                children.append("02")
            if directories[key]["children"] != sorted(children, key=lambda item: item.encode("utf-8")):
                _fail("INVENTORY_INVALID")


def _package(value, directories, files, roles):
    _exact(value, _PACKAGE)
    _fixed(value["mapping"], "same-relative-key", "MAPPING_INVALID")
    _sha(value["content_set_sha256"])
    if type(value["directories"]) is not list or any(type(key) is not str for key in value["directories"]):
        _fail("TYPE_INVALID")
    if value["directories"] != list(directories):
        _fail("MAPPING_INVALID")
    rows = _ordered(value["files"], _PACKAGE_FILE)
    for row in rows:
        if type(row["role"]) is not str:
            _fail("TYPE_INVALID")
        _sha(row["sha256"])
        _uint(row["byte_size"], _SLOT_BYTES)
    expected = [{"relative_key": key, "role": roles[key], "sha256": row["sha256"], "byte_size": row["byte_size"]}
                for key, row in files.items()]
    if rows != expected or value["content_set_sha256"] != _json_digest({
            "mapping": value["mapping"], "directories": value["directories"], "files": rows}):
        _fail("MAPPING_INVALID")


def _source_map(value, origin, files, documents, hashes):
    rows = _ordered(value, _SOURCE_ROW, key="source_entry_id", maximum=100_000)
    for row in rows:
        if not _ENTRY.fullmatch(row["source_entry_id"]):
            _fail("MAPPING_INVALID")
        _slot(row["origin_slot"])
        _uuid(row["origin_run_id"])
        for key in ("copy_record_relative_key", "payload_relative_key"):
            _path(row[key])
        for key in ("copy_record_sha256", "sha256"):
            _sha(row[key])
        _uint(row["byte_size"], _SLOT_BYTES)
    run = _run(origin["generation"])
    selected = documents[run + "/source-verified.json"]["selected_copies"]
    expected = []
    for item in selected:
        record_key = item["copy_record_relative_key"]
        record = documents[record_key]
        copy = record["copy"]
        payload_key = copy["target_relative_key"]
        if payload_key not in files:
            _fail("MAPPING_INVALID")
        _content(files[payload_key], copy)
        expected.append({"source_entry_id": copy["source_entry_id"], "origin_slot": record["slot"],
            "origin_run_id": record["run_id"], "copy_record_relative_key": record_key,
            "copy_record_sha256": hashes[record_key], "payload_relative_key": payload_key,
            "sha256": copy["sha256"], "byte_size": copy["byte_size"]})
    if [row["source_entry_id"] for row in expected] != documents["selection.json"]["source_file_ids"] or rows != expected:
        _fail("MAPPING_INVALID")


def _normalize(value, fact_bytes):
    copied = _snapshot(value)
    _exact(copied, _TOP)
    _fixed(copied["format_version"], FORMAT_VERSION, "VERSION_INVALID")
    _fixed(copied["kind"], KIND, "VERSION_INVALID")
    _fixed(copied["scope"], SCOPE, "VERSION_INVALID")
    _uuid(copied["replica_id"])
    _origin(copied["origin"])
    _exact(copied["conclusions"], {key for key, _ in _CONCLUSIONS})
    for key, expected in _CONCLUSIONS:
        _fixed(copied["conclusions"][key], expected)
    raw, documents, hashes = _facts(fact_bytes, copied["origin"])
    directories, files, roles, anchor = _inventory(copied["current"], documents, hashes)
    _logical(directories, files, roles, anchor, raw, documents, hashes)
    _historical_inventory(directories, files, documents)
    _package(copied["package_map"], directories, files, roles)
    _source_map(copied["source_map"], copied["origin"], files, documents, hashes)
    return copied


@_checked
def normalize(value: dict, *, fact_bytes: dict[str, bytes]) -> dict:
    """Validate detached declarations; return no live or historical capability."""
    return _normalize(value, fact_bytes)


@_checked
def encode(value: dict, *, fact_bytes: dict[str, bytes]) -> bytes:
    return _canonical(_normalize(value, fact_bytes))


@_checked
def decode(raw: bytes, *, fact_bytes: dict[str, bytes]) -> dict:
    return _normalize(_parse(raw, _MAX_JSON), fact_bytes)


@_checked
def compare_file_bytes(value: dict, *, fact_bytes: dict[str, bytes], file_bytes: dict[str, bytes]) -> None:
    """Compare all supplied complete buffers, not paths, fds or actual files."""
    normalized = _normalize(value, fact_bytes)
    if type(file_bytes) is not dict:
        _fail("TYPE_INVALID")
    if len(file_bytes) > _MAX_ENTRIES:
        _fail("LIMIT")
    if any(type(key) is not str for key in file_bytes):
        _fail("TYPE_INVALID")
    expected = {row["relative_key"]: row for row in normalized["current"]["files"]}
    if set(file_bytes) != set(expected):
        _fail("FACT_SET_INVALID")
    for key, content in file_bytes.items():
        if type(content) is not bytes:
            _fail("TYPE_INVALID")
        row = expected[key]
        if len(content) != row["byte_size"] or _digest(content) != row["sha256"]:
            _fail("CONTENT_CHANGED")
        if key in fact_bytes and content != fact_bytes[key]:
            _fail("CONTENT_CHANGED")
    return None
