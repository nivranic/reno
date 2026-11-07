"""Frozen GC requests and proofs read from the original committed DB image.

This module does not choose files, retire checkpoints, or delete anything.
Its caller supplies a private committed snapshot (or verified frozen backup),
never a live connection containing the caller's uncommitted writes. The reader
keeps that connection's transaction intact and leaves a read-only authorizer.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import re
import sqlite3
import time
import uuid

from . import (storage_artifacts, storage_schema, sync_checkpoint_schema,
               sync_recovery_schema, sync_transfer_schema)
from .sync_protocol import SyncError

FORMAT_VERSION = 1
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILES = 100_000
MAX_RETIREMENTS = 4096
MAX_JSON_NODES = 2_000_000
MAX_JSON_DEPTH = 24
MAX_QUERY_STEPS = 200_000
MAX_QUERY_SECONDS = 5
NAMESPACES = tuple(sorted(storage_artifacts.NAMESPACES))
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,23})\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_REQUEST_KEYS = {"format_version", "action", "operation_id", "owner", "expected_preview_sha256",
                 "expected_roots_sha256", "members_sha256", "policy_sha256", "retirements", "files"}
_POLICY_KEYS = {"format_version", "keep_publication", "keep_recovery", "retain_latest_recoverable",
                "protect_active_recovery", "namespaces", "limits_version"}
_FILE_KEYS = {"artifact_id", "namespace", "root_id", "root_device", "root_inode", "storage_key",
              "sha256", "byte_size", "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns", "intent"}
_RETIREMENT_KEYS = {"origin_kind", "checkpoint_id", "origin_id", "receiver_version", "root_id",
                    "storage_key", "sha256", "byte_size", "format_version", "schema_version",
                    "created_at", "archive_item_id", "archive_disposition"}
_ITEM_IDENTITY = ("artifact_id", "namespace", "root_id", "storage_key", "sha256", "byte_size",
                  "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns")
_ARTIFACT_IMMUTABLE = ("artifact_id", "namespace", "root_id", "root_device", "root_inode",
                       "storage_key", "sha256", "byte_size")


class GCContractError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code="GC_CONTRACT_INVALID"):
    raise GCContractError(code)


def canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _fail("GC_JSON_INVALID")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _tree(value):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _fail("GC_JSON_LIMIT")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                _fail("GC_JSON_INVALID")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, bool):
            _fail("GC_JSON_INVALID")


def decode(raw, *, canonical=False):
    if type(raw) is str:
        try:
            raw = raw.encode("utf-8")
        except UnicodeError:
            _fail("GC_JSON_INVALID")
    if type(raw) is not bytes or len(raw) > MAX_JSON_BYTES:
        _fail("GC_JSON_LIMIT")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("GC_JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: _fail("GC_JSON_INVALID"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, GCContractError):
            raise
        _fail("GC_JSON_INVALID")
    _tree(value)
    if canonical and canonical_bytes(value) != raw:
        _fail("GC_JSON_NOT_CANONICAL")
    return value


def _exact(value, keys):
    if type(value) is not dict or set(value) != keys:
        _fail()


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail()
    except ValueError:
        _fail()
    return value


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()
    return value


def _decimal(value):
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        _fail()


def _integer(value, minimum, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _stamp(value):
    if type(value) is not str or not _UTC.fullmatch(value):
        _fail()
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.utcoffset() != timezone.utc.utcoffset(instant):
            _fail()
    except ValueError:
        _fail()


def _identifier(value, prefix):
    if type(value) is not str or not value.startswith(prefix):
        _fail()
    _sha(value[len(prefix):])


def _version(value):
    _exact(value, {"library_id", "library_epoch", "kb_revision"})
    _uuid(value["library_id"])
    _integer(value["library_epoch"], 1)
    _integer(value["kb_revision"], 0)


def validate_policy(value):
    _tree(value)
    _exact(value, _POLICY_KEYS)
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    _integer(value["keep_publication"], 1, MAX_RETIREMENTS)
    _integer(value["keep_recovery"], 1, MAX_RETIREMENTS)
    if (value["retain_latest_recoverable"] is not True or value["protect_active_recovery"] is not True
            or type(value["limits_version"]) is not int or value["limits_version"] != 1):
        _fail()
    namespaces = value["namespaces"]
    if (type(namespaces) is not list or not namespaces or any(type(x) is not str for x in namespaces)
            or namespaces != sorted(set(namespaces)) or set(namespaces) - set(NAMESPACES)):
        _fail()
    return decode(canonical_bytes(value))


def default_policy(*, keep_publication=3, keep_recovery=3, namespaces=NAMESPACES):
    return validate_policy({"format_version": 1, "keep_publication": keep_publication,
                            "keep_recovery": keep_recovery, "retain_latest_recoverable": True,
                            "protect_active_recovery": True, "namespaces": sorted(namespaces), "limits_version": 1})


def _file(value):
    _exact(value, _FILE_KEYS)
    _identifier(value["artifact_id"], "art_")
    for key in ("root_id", "sha256"):
        _sha(value[key])
    for key in ("root_device", "root_inode", "file_device", "file_inode", "file_mtime_ns", "file_ctime_ns"):
        _decimal(value[key])
    namespace = value["namespace"]
    if namespace not in NAMESPACES:
        _fail()
    try:
        storage_artifacts.validate_storage_key(namespace, value["storage_key"])
    except SyncError:
        _fail()
    _integer(value["byte_size"], 0 if namespace in ("local_report", "sync_report") else 1,
             storage_artifacts._LIMITS[namespace])
    if value["root_id"] != digest({key: value[key] for key in ("root_device", "root_inode")}):
        _fail()
    if value["artifact_id"] != "art_" + digest({key: value[key] for key in ("namespace", "root_id", "storage_key")}):
        _fail()
    if namespace == "sync_media" and value["storage_key"].rsplit("/", 1)[1].split(".", 1)[0] != value["sha256"]:
        _fail()
    if namespace == "sync_report" and value["storage_key"].rsplit("-", 1)[1][:-3] != value["sha256"]:
        _fail()
    if value["intent"] not in ("delete_if_unreferenced", "retain"):
        _fail()


def _retirement(value):
    _exact(value, _RETIREMENT_KEYS)
    if value["origin_kind"] not in ("publication", "recovery"):
        _fail()
    for key in ("checkpoint_id", "origin_id"):
        _uuid(value[key])
    for key in ("root_id", "sha256"):
        _sha(value[key])
    _version(value["receiver_version"])
    _integer(value["byte_size"], 1, 256 * 1024 * 1024)
    if (type(value["format_version"]) is not int or value["format_version"] !=
            (2 if value["origin_kind"] == "recovery" else 1)
            or type(value["schema_version"]) is not int or value["schema_version"] not in (7, 9)
            or value["storage_key"] != value["checkpoint_id"] + ".sqlite"):
        _fail()
    _stamp(value["created_at"])
    if value["archive_disposition"] == "preserve_unowned":
        if value["archive_item_id"] is not None:
            _fail()
    elif value["archive_disposition"] == "journaled":
        _identifier(value["archive_item_id"], "art_")
    else:
        _fail()


def validate_request(value, *, policy=None):
    _tree(value)
    _exact(value, _REQUEST_KEYS)
    if type(value["format_version"]) is not int or value["format_version"] != 1 or value["action"] != "storage_gc":
        _fail()
    _uuid(value["operation_id"])
    owner = value["owner"]
    _exact(owner, {"member_id", "library_id", "database_identity"})
    _identifier(owner["member_id"], "mem_")
    _uuid(owner["library_id"])
    _exact(owner["database_identity"], {"device", "inode"})
    for part in owner["database_identity"].values():
        _decimal(part)
    for key in ("expected_preview_sha256", "expected_roots_sha256", "members_sha256", "policy_sha256"):
        _sha(value[key])
    files, retirements = value["files"], value["retirements"]
    if (type(files) is not list or len(files) > MAX_FILES or type(retirements) is not list
            or len(retirements) > MAX_RETIREMENTS):
        _fail()
    for file in files:
        _file(file)
    file_keys = [(file["namespace"], file["root_id"], file["storage_key"], file["artifact_id"]) for file in files]
    if (file_keys != sorted(set(file_keys)) or len({file["artifact_id"] for file in files}) != len(files)
            or len({key[:3] for key in file_keys}) != len(files)):
        _fail()
    by_artifact = {file["artifact_id"]: file for file in files}
    by_position = {key[:3]: file for key, file in zip(file_keys, files)}
    seen_items = set()
    for retirement in retirements:
        _retirement(retirement)
        if retirement["receiver_version"]["library_id"] != owner["library_id"]:
            _fail()
        positioned = by_position.get(("checkpoint", retirement["root_id"], retirement["storage_key"]))
        if retirement["archive_disposition"] == "preserve_unowned":
            if positioned is not None:
                _fail()
        else:
            file = by_artifact.get(retirement["archive_item_id"])
            if file is None or positioned != file or retirement["archive_item_id"] in seen_items:
                _fail()
            if any(file[key] != retirement[key] for key in ("root_id", "storage_key", "sha256", "byte_size")):
                _fail()
            seen_items.add(retirement["archive_item_id"])
    retirement_keys = [(item["origin_kind"], item["checkpoint_id"]) for item in retirements]
    if retirement_keys != sorted(set(retirement_keys)):
        _fail()
    if policy is not None:
        policy = validate_policy(policy)
        if value["policy_sha256"] != digest(policy) or any(file["namespace"] not in policy["namespaces"] for file in files):
            _fail()
        if retirements and "checkpoint" not in policy["namespaces"]:
            _fail()
    # Return a detached, bounded value, never a caller-owned mutable receipt.
    return decode(canonical_bytes(value))


def checkpoint_descriptor(entry, *, root_id, archive_item_id=None):
    """Convert a SQL checkpoint row without consulting today's live state."""
    recovery = type(entry) is dict and entry.get("format_version") == 2
    keys = {"checkpoint_id", "receiver_version_json", "format_version", "schema_version", "storage_key",
            "sha256", "byte_size", "created_at"} | ({"operation_id", "origin_kind"} if recovery else {"publication_id"})
    _exact(entry, keys)
    if recovery and entry["origin_kind"] != "recovery":
        _fail()
    value = {key: entry[key] for key in ("checkpoint_id", "format_version", "schema_version", "storage_key",
                                       "sha256", "byte_size", "created_at")}
    value.update(origin_kind="recovery" if recovery else "publication",
                 origin_id=entry["operation_id"] if recovery else entry["publication_id"],
                 receiver_version=decode(entry["receiver_version_json"]), root_id=root_id,
                 archive_item_id=archive_item_id,
                 archive_disposition="journaled" if archive_item_id is not None else "preserve_unowned")
    _retirement(value)
    return value


@dataclass(frozen=True)
class RetirementProof:
    status: str
    code: str
    operation_id: str | None = None


_READ_TABLES = {"sqlite_master", "sqlite_schema", "meta", "sync_checkpoint", "sync_recovery_checkpoint",
                "sync_publication", "sync_recovery_operation", "storage_gc_operation",
                "sync_checkpoint_retirement", "storage_gc_item", "storage_artifact"}


def _authorize(action, first, second, schema, _source):
    from . import storage_root_inputs as inputs
    continuity = inputs.continuity_authorize(action, first, second, schema, _source)
    if continuity is not None:
        return continuity
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and schema == "main" and first in _READ_TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and schema == "main" and _source is None:
        from . import storage_home_recovery_roots as home_roots
        if first == home_roots.TABLE and second in home_roots.COLUMNS:
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {"length", "typeof"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def _query_budget(con):
    # Dedicated snapshot connections allow each parser to install its own gate.
    # There is no sqlite3 get_authorizer/get_progress_handler API to restore an
    # unknown predecessor, so leave our restrictive authorizer in place.
    before = con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, min(before, MAX_JSON_BYTES))
    con.set_authorizer(_authorize)
    calls, started = 0, time.monotonic()

    def progress():
        nonlocal calls
        calls += 1
        return int(calls * 1000 > MAX_QUERY_STEPS or time.monotonic() - started > MAX_QUERY_SECONDS)

    con.set_progress_handler(progress, 1000)
    try:
        yield
    finally:
        con.set_progress_handler(None, 0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, before)


def _one(con, sql, args=()):
    cursor = con.execute(sql, args)
    rows = cursor.fetchmany(2)
    if len(rows) > 1:
        _fail("RETIREMENT_DUPLICATE_ROW")
    if not rows:
        return None
    names = [item[0] for item in cursor.description]
    result = dict(zip(names, tuple(rows[0])))
    if len(canonical_bytes(result)) > MAX_JSON_BYTES:
        _fail("RETIREMENT_QUERY_LIMIT")
    return result


def _ordinary(con, table):
    row = _one(con, "SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (table,))
    if (row is None or row["type"] != "table" or type(row["sql"]) is not str
            or len(row["sql"].encode("utf-8")) > 32 * 1024
            or "CREATE VIRTUAL TABLE" in " ".join(row["sql"].upper().split())):
        _fail("RETIREMENT_SCHEMA_UNSAFE")
    return row


@lru_cache(maxsize=1)
def _storage_contracts():
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE TABLE video_asset(video_id TEXT PRIMARY KEY)")
        sync_transfer_schema.install(con)
        sync_checkpoint_schema.install(con)
        sync_recovery_schema.install(con)
        storage_schema.install(con)
        return {name: storage_schema._contract(con, name) for name in
                ("storage_gc_operation", "sync_checkpoint_retirement", "storage_gc_item", "storage_artifact",
                 "sync_checkpoint", "sync_recovery_checkpoint", "sync_publication", "sync_recovery_operation")}
    finally:
        con.close()


def _verify(con, entry, expected_library_id, expected_member_id, expected_root_id):
    _ordinary(con, "meta")
    meta_columns = {row[1] for row in con.execute("PRAGMA table_info(meta)")}
    if meta_columns != {"key", "value"}:
        _fail("RETIREMENT_SCHEMA_UNSAFE")
    schema = _one(con, "SELECT value FROM meta WHERE key='migration_version' LIMIT 2")
    if schema is None or type(schema["value"]) is not str or schema["value"] not in tuple(str(x) for x in range(18)):
        _fail("RETIREMENT_SCHEMA_UNKNOWN")
    # Home recovery receipts add permanent roots, never a retirement grant.
    # Validate only their closed root projection, before even an absent-proof
    # response; private frontier/request/result bodies remain denied below.
    from . import storage_home_recovery_roots as home_roots
    from . import storage_root_inputs as inputs
    try:
        if int(schema["value"]) >= 17:
            inputs.read_continuity_projection(con)
        elif inputs.continuity_reserved_projection(con):
            _fail("RETIREMENT_SOURCE_CONTINUITY_UNKNOWN")
    except (inputs._Invalid, sqlite3.Error):
        _fail("RETIREMENT_SOURCE_CONTINUITY_UNKNOWN")
    if int(schema["value"]) >= 16:
        try:
            home_rows = home_roots.read_projection(con)
        except home_roots.HomeRecoveryRootError:
            _fail("RETIREMENT_HOME_RECOVERY_INVALID")
        library = _one(con, "SELECT value FROM meta WHERE key='library_id' LIMIT 2")
        if library is None:
            _fail("RETIREMENT_OWNER_MISMATCH")
        _uuid(library["value"])
        if ((expected_library_id is not None and library["value"] != expected_library_id)
                or any(row["library_id"] != library["value"] for row in home_rows)):
            _fail("RETIREMENT_OWNER_MISMATCH")
    elif _one(con, "SELECT name FROM sqlite_master WHERE name=? COLLATE NOCASE LIMIT 2", (home_roots.TABLE,)) is not None:
        _fail("RETIREMENT_SCHEMA_UNKNOWN")
    if int(schema["value"]) < 10:
        if _one(con, "SELECT name FROM sqlite_master WHERE name='sync_checkpoint_retirement' LIMIT 2") is not None:
            _fail("RETIREMENT_SCHEMA_UNKNOWN")
        return RetirementProof("not_retired", "RETIREMENT_ABSENT")
    for table in ("storage_gc_operation", "sync_checkpoint_retirement", "storage_gc_item", "storage_artifact"):
        _ordinary(con, table)
        if storage_schema._contract(con, table) != _storage_contracts()[table]:
            _fail("RETIREMENT_SCHEMA_UNSAFE")
    # Validate the supplied historical entry even when there is no retirement.
    candidate = checkpoint_descriptor(entry, root_id="0" * 64)
    kind, checkpoint_id = candidate["origin_kind"], candidate["checkpoint_id"]
    retired = _one(con, "SELECT * FROM sync_checkpoint_retirement WHERE origin_kind=? AND checkpoint_id=? LIMIT 2",
                   (kind, checkpoint_id))
    if retired is None:
        return RetirementProof("not_retired", "RETIREMENT_ABSENT")
    library = _one(con, "SELECT value FROM meta WHERE key='library_id' LIMIT 2")
    if library is None:
        _fail("RETIREMENT_OWNER_MISMATCH")
    _uuid(library["value"])
    if expected_library_id is not None:
        _uuid(expected_library_id)
        if library["value"] != expected_library_id:
            _fail("RETIREMENT_OWNER_MISMATCH")
    operation_id = _uuid(retired["operation_id"])
    operation = _one(con, "SELECT operation_id,request_sha256,preview_sha256,roots_sha256,policy_json,request_json,state "
                     "FROM storage_gc_operation WHERE operation_id=? LIMIT 2", (operation_id,))
    if operation is None:
        _fail("RETIREMENT_OPERATION_MISSING")
    if operation["state"] not in ("planned", "running", "blocked", "completed"):
        _fail("RETIREMENT_OPERATION_INVALID")
    policy = validate_policy(decode(operation["policy_json"], canonical=True))
    request = validate_request(decode(operation["request_json"], canonical=True), policy=policy)
    if (request["operation_id"] != operation_id or digest(request) != operation["request_sha256"]
            or request["expected_preview_sha256"] != operation["preview_sha256"]
            or request["expected_roots_sha256"] != operation["roots_sha256"]):
        _fail("RETIREMENT_REQUEST_MISMATCH")
    owner = request["owner"]
    if owner["library_id"] != library["value"] or candidate["receiver_version"]["library_id"] != library["value"]:
        _fail("RETIREMENT_OWNER_MISMATCH")
    if expected_member_id is not None:
        _identifier(expected_member_id, "mem_")
        if owner["member_id"] != expected_member_id:
            _fail("RETIREMENT_OWNER_MISMATCH")
    matches = [item for item in request["retirements"] if (item["origin_kind"], item["checkpoint_id"]) == (kind, checkpoint_id)]
    if len(matches) != 1:
        _fail("RETIREMENT_DESCRIPTOR_MISMATCH")
    selected = matches[0]
    if expected_root_id is not None and selected["root_id"] != _sha(expected_root_id):
        _fail("RETIREMENT_OWNER_MISMATCH")
    expected = checkpoint_descriptor(entry, root_id=selected["root_id"], archive_item_id=selected["archive_item_id"])
    if selected != expected or any(retired[key] != selected[key] for key in
                                   ("origin_kind", "checkpoint_id", "storage_key", "sha256", "byte_size")):
        _fail("RETIREMENT_DESCRIPTOR_MISMATCH")
    _stamp(retired["retired_at"])
    table = "sync_recovery_checkpoint" if kind == "recovery" else "sync_checkpoint"
    _ordinary(con, table)
    if storage_schema._contract(con, table) != _storage_contracts()[table]:
        _fail("RETIREMENT_SCHEMA_UNSAFE")
    actual = _one(con, f"SELECT * FROM {table} WHERE checkpoint_id=? LIMIT 2", (checkpoint_id,))
    if actual != entry:
        _fail("RETIREMENT_DESCRIPTOR_MISMATCH")
    origin_table = "sync_recovery_operation" if kind == "recovery" else "sync_publication"
    _ordinary(con, origin_table)
    if storage_schema._contract(con, origin_table) != _storage_contracts()[origin_table]:
        _fail("RETIREMENT_SCHEMA_UNSAFE")
    if kind == "recovery":
        origin = _one(con, "SELECT operation_id,action,created_at FROM sync_recovery_operation WHERE operation_id=? LIMIT 2",
                      (selected["origin_id"],))
        if origin is None or origin["action"] != "restore":
            _fail("RETIREMENT_ORIGIN_UNCOMMITTED")
        _stamp(origin["created_at"])
    else:
        origin = _one(con, "SELECT publication_id,committed_at FROM sync_publication WHERE publication_id=? LIMIT 2",
                      (selected["origin_id"],))
        if origin is None:
            _fail("RETIREMENT_ORIGIN_UNCOMMITTED")
        _stamp(origin["committed_at"])
    if selected["archive_disposition"] == "journaled":
        artifact_id = selected["archive_item_id"]
        file = next(item for item in request["files"] if item["artifact_id"] == artifact_id)
        item_fields = ",".join(("operation_id", *_ITEM_IDENTITY, "state", "deleted_bytes"))
        item = _one(con, f"SELECT {item_fields} FROM storage_gc_item WHERE operation_id=? AND artifact_id=? LIMIT 2",
                    (operation_id, artifact_id))
        artifact = _one(con, f"SELECT {','.join(_ARTIFACT_IMMUTABLE)} FROM storage_artifact WHERE artifact_id=? LIMIT 2",
                        (artifact_id,))
        if item is None or artifact is None:
            _fail("RETIREMENT_ITEM_MISSING")
        if (any(item[key] != file[key] for key in _ITEM_IDENTITY)
                or any(artifact[key] != file[key] for key in _ARTIFACT_IMMUTABLE)
                or item["operation_id"] != operation_id):
            _fail("RETIREMENT_ITEM_MISMATCH")
        if item["state"] not in ("pending", "blocked", "retained", "deleted", "missing"):
            _fail("RETIREMENT_ITEM_MISMATCH")
        _integer(item["deleted_bytes"], 0, file["byte_size"])
        if item["state"] != "deleted" and item["deleted_bytes"] != 0:
            _fail("RETIREMENT_ITEM_MISMATCH")
        if file["intent"] == "retain" and item["state"] != "retained":
            _fail("RETIREMENT_ITEM_MISMATCH")
    else:
        # An explicit unowned archive must have no hidden physical-delete row.
        hidden = _one(con, "SELECT artifact_id FROM storage_gc_item WHERE operation_id=? AND namespace='checkpoint' "
                      "AND root_id=? AND storage_key=? LIMIT 2", (operation_id, selected["root_id"], selected["storage_key"]))
        if hidden is not None:
            _fail("RETIREMENT_ITEM_MISMATCH")
    return RetirementProof("proven", "RETIREMENT_PROVEN", operation_id)


def verify_retirement(con, entry, *, expected_library_id=None, expected_member_id=None, expected_root_id=None):
    """Verify logical retirement using only this original, committed image.

    A blocked/pending physical unlink does not revoke a committed retirement.
    The backup's new inode and today's epoch/policy/result_json are irrelevant.
    Unsupported or contradictory evidence remains unproven, never an empty root.
    """
    try:
        with _query_budget(con):
            return _verify(con, entry, expected_library_id, expected_member_id, expected_root_id)
    except GCContractError as exc:
        return RetirementProof("unproven", exc.code)
    except (sqlite3.Error, TypeError, KeyError, ValueError, StopIteration, RecursionError):
        return RetirementProof("unproven", "RETIREMENT_IMAGE_INVALID")
