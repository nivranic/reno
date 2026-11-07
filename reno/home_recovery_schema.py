"""Schema16: append-only Home knowledge recovery receipts, without authority.

Installation is DDL only and never seeds a restore, source provenance or new
admission. The caller owns migration/transactions and the trusted local writer.
Record validation is pure; actual checkpoint production, request/result
business keys, operation lineage, image pins and replay belong to that writer.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import re
import sqlite3
import uuid

from .storage_schema import _contract


SCHEMA_VERSION = 16
FORMAT_VERSION = 1
TABLE = "home_knowledge_recovery_operation"
MAX_INTEGER = 2**63 - 1
MAX_VERSION_BYTES = 512
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESULT_BYTES = 16 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024
MAX_FRONTIER_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 1_000_000
SOURCE_KINDS = frozenset({"registered_backup", "home_checkpoint"})


def _uuid_column(name, *, nullable=False):
    valid = (f"typeof({name})='text' AND length({name})=36 AND substr({name},9,1)='-' "
        f"AND substr({name},14,1)='-' AND substr({name},19,1)='-' AND substr({name},24,1)='-' "
        f"AND length(replace({name},'-',''))=32 AND replace({name},'-','') NOT GLOB '*[^0-9a-f]*'")
    return "TEXT" + ("" if nullable else " NOT NULL") + f" CHECK({name} IS NULL OR ({valid}))" if nullable else "TEXT NOT NULL CHECK(" + valid + ")"


def _sha_column(name, *, nullable=False):
    valid = f"typeof({name})='text' AND length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'"
    return "TEXT" + ("" if nullable else " NOT NULL") + f" CHECK({name} IS NULL OR ({valid}))" if nullable else "TEXT NOT NULL CHECK(" + valid + ")"


def _integer_column(name, *, minimum=0, maximum=MAX_INTEGER, nullable=False):
    valid = f"typeof({name})='integer' AND {name} BETWEEN {minimum} AND {maximum}"
    return "INTEGER" + ("" if nullable else " NOT NULL") + f" CHECK({name} IS NULL OR ({valid}))" if nullable else "INTEGER NOT NULL CHECK(" + valid + ")"


def _json_column(name, maximum):
    return (f"TEXT NOT NULL CHECK(typeof({name})='text' AND length(CAST({name} AS BLOB)) BETWEEN 2 AND {maximum}) "
            f"CHECK(json_valid({name})) CHECK(CASE WHEN json_valid({name}) THEN json_type({name})='object' ELSE 0 END)")


COLUMNS = (
    "operation_id", "format_version", "action", "library_id", "node_id", "source_kind", "source_id",
    "source_sha256", "source_binding_sha256", "before_library_epoch", "before_kb_revision",
    "after_library_epoch", "after_kb_revision", "before_version_json", "before_version_sha256",
    "after_version_json", "after_version_sha256", "protection_backup_id", "protection_descriptor_sha256",
    "protection_image_sha256", "original_core_sha256", "root_job_id", "continuation_id",
    "execution_generation", "frontier_json", "frontier_sha256", "before_operational_sha256",
    "after_operational_sha256", "checkpoint_json", "checkpoint_sha256", "request_json", "request_sha256",
    "result_json", "result_sha256", "committed_at",
)
TABLES = {TABLE: ",\n".join((
    "operation_id " + _uuid_column("operation_id") + " PRIMARY KEY",
    "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version=1)",
    "action TEXT NOT NULL CHECK(action='knowledge_restore')",
    "library_id " + _uuid_column("library_id"), "node_id " + _uuid_column("node_id"),
    "source_kind TEXT NOT NULL CHECK(source_kind IN ('registered_backup','home_checkpoint'))",
    "source_id " + _uuid_column("source_id"),
    "source_sha256 " + _sha_column("source_sha256"),
    "source_binding_sha256 " + _sha_column("source_binding_sha256"),
    "before_library_epoch " + _integer_column("before_library_epoch", minimum=1, maximum=MAX_INTEGER - 1),
    "before_kb_revision " + _integer_column("before_kb_revision", maximum=MAX_INTEGER - 1),
    "after_library_epoch " + _integer_column("after_library_epoch", minimum=2),
    "after_kb_revision " + _integer_column("after_kb_revision", minimum=1),
    "before_version_json " + _json_column("before_version_json", MAX_VERSION_BYTES),
    "before_version_sha256 " + _sha_column("before_version_sha256"),
    "after_version_json " + _json_column("after_version_json", MAX_VERSION_BYTES),
    "after_version_sha256 " + _sha_column("after_version_sha256"),
    "protection_backup_id " + _uuid_column("protection_backup_id"),
    "protection_descriptor_sha256 " + _sha_column("protection_descriptor_sha256"),
    "protection_image_sha256 " + _sha_column("protection_image_sha256"),
    "original_core_sha256 " + _sha_column("original_core_sha256", nullable=True),
    "root_job_id " + _integer_column("root_job_id", minimum=1, nullable=True) + " REFERENCES job(job_id)",
    "continuation_id " + _uuid_column("continuation_id", nullable=True),
    "execution_generation " + _integer_column("execution_generation", minimum=1, nullable=True),
    "frontier_json " + _json_column("frontier_json", MAX_FRONTIER_BYTES),
    "frontier_sha256 " + _sha_column("frontier_sha256"),
    "before_operational_sha256 " + _sha_column("before_operational_sha256"),
    "after_operational_sha256 " + _sha_column("after_operational_sha256"),
    "checkpoint_json " + _json_column("checkpoint_json", MAX_CHECKPOINT_BYTES),
    "checkpoint_sha256 " + _sha_column("checkpoint_sha256"),
    "request_json " + _json_column("request_json", MAX_REQUEST_BYTES),
    "request_sha256 " + _sha_column("request_sha256"),
    "result_json " + _json_column("result_json", MAX_RESULT_BYTES),
    "result_sha256 " + _sha_column("result_sha256"),
    "committed_at TEXT NOT NULL CHECK(typeof(committed_at)='text' AND length(committed_at)=27 "
    "AND committed_at GLOB '????-??-??T??:??:??.??????Z')",
    "CHECK(after_library_epoch=before_library_epoch+1)",
    "CHECK(after_kb_revision>before_kb_revision)",
    "CHECK(before_operational_sha256=after_operational_sha256)",
    "CHECK((original_core_sha256 IS NULL AND root_job_id IS NULL AND continuation_id IS NULL AND execution_generation IS NULL) "
    "OR (original_core_sha256 IS NOT NULL AND root_job_id IS NOT NULL AND continuation_id IS NOT NULL AND execution_generation IS NOT NULL))",
))}
INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_home_recovery_committed ON {TABLE}(committed_at,operation_id)",
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_home_recovery_epoch ON {TABLE}(library_id,after_library_epoch)",
    f"CREATE INDEX IF NOT EXISTS idx_home_recovery_root ON {TABLE}(root_job_id,execution_generation,committed_at,operation_id) WHERE root_job_id IS NOT NULL",
)
TRIGGERS = (
    f"CREATE TRIGGER IF NOT EXISTS home_recovery_no_update BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT,'HOME_RECOVERY_IMMUTABLE'); END",
    f"CREATE TRIGGER IF NOT EXISTS home_recovery_no_delete BEFORE DELETE ON {TABLE} BEGIN SELECT RAISE(ABORT,'HOME_RECOVERY_IMMUTABLE'); END",
)
RESERVED_NAMES = (TABLE, "idx_home_recovery_committed", "idx_home_recovery_epoch", "idx_home_recovery_root",
                  "home_recovery_no_update", "home_recovery_no_delete")


class HomeRecoverySchemaError(sqlite3.DatabaseError):
    def __init__(self, code="HOME_RECOVERY_SCHEMA_INVALID"):
        self.code = code
        super().__init__(code)


class HomeRecoveryRecordError(ValueError):
    def __init__(self, code="HOME_RECOVERY_RECORD_INVALID"):
        self.code = code
        super().__init__(code)


def _shape_fail(code="HOME_RECOVERY_SCHEMA_INVALID"):
    raise HomeRecoverySchemaError(code) from None


def _record_fail(code="HOME_RECOVERY_RECORD_INVALID"):
    raise HomeRecoveryRecordError(code) from None


def _normalized(sql):
    return " ".join((sql or "").split()).replace('"', '')


def _require_job(con):
    primary = [(row[1], row[2].upper(), row[5]) for row in con.execute("PRAGMA main.table_info(job)") if row[5]]
    if primary != [("job_id", "INTEGER", 1)]:
        _shape_fail()


def _install(con):
    _require_job(con)
    for name, declaration in TABLES.items():
        con.execute(f"CREATE TABLE {name} ({declaration})")
    for sql in INDEXES + TRIGGERS:
        con.execute(sql)


def _trigger_contract(con):
    return [(row[0], row[1], _normalized(row[2])) for row in con.execute(
        "SELECT name,tbl_name,sql FROM main.sqlite_master WHERE type='trigger' AND lower(tbl_name)=? ORDER BY name",
        (TABLE,))]


def validate(con):
    """Strict read-only DDL shape, including FK/actions, predicates and triggers."""
    _require_job(con)
    if any(row[0].lower() in RESERVED_NAMES for row in con.execute("SELECT name FROM sqlite_temp_master")):
        _shape_fail()
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute("CREATE TABLE job(job_id INTEGER PRIMARY KEY)")
        _install(reference)
        if _contract(con, TABLE) != _contract(reference, TABLE) or _trigger_contract(con) != _trigger_contract(reference):
            _shape_fail()
        actual = [(row[0], row[1], row[2]) for row in con.execute(
            "SELECT type,name,tbl_name FROM main.sqlite_master WHERE lower(name) IN (" +
            ",".join("?" for _ in RESERVED_NAMES) + ") ORDER BY name", RESERVED_NAMES)]
        expected = [(row[0], row[1], row[2]) for row in reference.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE lower(name) IN (" +
            ",".join("?" for _ in RESERVED_NAMES) + ") ORDER BY name", RESERVED_NAMES)]
        if actual != expected:
            _shape_fail()
    finally:
        reference.close()


def preflight(con, *, require_absent=True):
    """Reject all future-name collisions before the migration's backup/writer.

    Default absence is mandatory for a genuine v15 -> v16 migration, even when
    an unowned earlier table has the same DDL. ``False`` accepts only a complete
    already-installed exact shape and returns whether that shape is present.
    """
    if type(require_absent) is not bool:
        _shape_fail()
    found = []
    for master in ("main.sqlite_master", "sqlite_temp_master"):
        found.extend(con.execute("SELECT name FROM " + master + " WHERE lower(name) IN (" +
                                 ",".join("?" for _ in RESERVED_NAMES) + ")", RESERVED_NAMES))
    if found:
        if require_absent:
            _shape_fail("HOME_RECOVERY_SCHEMA_COLLISION")
        validate(con)
        return True
    return False


def install(con):
    """DDL only, no commit/rollback or proof seeding; never repair a partial DDL."""
    if preflight(con, require_absent=False):
        return
    _install(con)


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\|/)")
_PRIVATE_KEYS = frozenset({"api_key", "auth_token", "sync_token", "access_token", "refresh_token",
    "token", "lease_token", "dispatch_token", "session_token", "authorization", "cookie", "cookies", "password", "secret", "credential", "credentials", "headers",
    "body", "private_body", "request_body", "response_body", "result_text", "raw_result",
    "metadata_json", "path", "file_path", "filepath", "relative_path", "absolute_path", "file_name", "filename", "storage_key", "artifact_relpath", "source_url", "target_url", "url", "uri",
    "stdout", "stderr", "payload", "payload_json"})
_PRIVATE_KEYS_COMPACT = frozenset(key.replace("_", "") for key in _PRIVATE_KEYS)
_JSON_LIMITS = {"before_version_json": MAX_VERSION_BYTES, "after_version_json": MAX_VERSION_BYTES,
    "frontier_json": MAX_FRONTIER_BYTES, "checkpoint_json": MAX_CHECKPOINT_BYTES,
    "request_json": MAX_REQUEST_BYTES, "result_json": MAX_RESULT_BYTES}
_UUID_FIELDS = frozenset({"operation_id", "library_id", "node_id", "source_id", "protection_backup_id", "continuation_id"})
_INTEGER_FIELDS = {"before_library_epoch": (1, MAX_INTEGER - 1), "before_kb_revision": (0, MAX_INTEGER - 1),
    "after_library_epoch": (2, MAX_INTEGER), "after_kb_revision": (1, MAX_INTEGER),
    "root_job_id": (1, MAX_INTEGER), "execution_generation": (1, MAX_INTEGER)}
_LINEAGE = ("original_core_sha256", "root_job_id", "continuation_id", "execution_generation")


def canonical_json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        result.encode("utf-8")
        return result
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        _record_fail()


def _decode(raw, maximum, *, result=False):
    if type(raw) is not str or len(raw.encode("utf-8")) > maximum:
        _record_fail("HOME_RECOVERY_JSON_LIMIT")
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                _record_fail()
            value[key] = child
        return value
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: _record_fail())
    if type(value) is not dict or canonical_json(value) != raw:
        _record_fail()
    pending, nodes = [(value, 0)], 0
    while pending:
        child, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _record_fail("HOME_RECOVERY_JSON_LIMIT")
        if type(child) is dict:
            if any(key.lower().replace("-", "").replace("_", "") in _PRIVATE_KEYS_COMPACT for key in child):
                _record_fail("HOME_RECOVERY_PRIVATE_RECEIPT")
            pending.extend((item, depth + 1) for item in child.values())
        elif type(child) is list:
            pending.extend((item, depth + 1) for item in child)
        elif result and type(child) is str and ("/" in child or "\\" in child or _ABSOLUTE_PATH.search(child)):
            _record_fail("HOME_RECOVERY_PRIVATE_RECEIPT")
    return value


def validate_record(mapping):
    """Detached closed columns/canonical fingerprints; never an admission proof.

    Request/result business keys are enforced by the entrypoint; this module
    rejects obvious credential/private body/path fields but cannot infer the
    meaning of arbitrary caller text. The supported producer verifies actual
    image/node provenance and writer context separately.
    """
    try:
        if not isinstance(mapping, (Mapping, sqlite3.Row)):
            _record_fail()
        record = dict(mapping)
        if set(record) != set(COLUMNS):
            _record_fail()
        lineage = [record[name] is not None for name in _LINEAGE]
        if any(lineage) and not all(lineage):
            _record_fail()
        if type(record["format_version"]) is not int or record["format_version"] != FORMAT_VERSION:
            _record_fail()
        if record["action"] != "knowledge_restore" or record["source_kind"] not in SOURCE_KINDS:
            _record_fail()
        for name in _UUID_FIELDS:
            value = record[name]
            if name in _LINEAGE and value is None:
                continue
            if type(value) is not str or str(uuid.UUID(value)) != value:
                _record_fail()
        for name in (name for name in COLUMNS if name.endswith("sha256")):
            value = record[name]
            if name in _LINEAGE and value is None:
                continue
            if type(value) is not str or not _SHA.fullmatch(value):
                _record_fail()
        for name, (minimum, maximum) in _INTEGER_FIELDS.items():
            value = record[name]
            if name in _LINEAGE and value is None:
                continue
            if type(value) is not int or not minimum <= value <= maximum:
                _record_fail()
        if (record["after_library_epoch"] != record["before_library_epoch"] + 1
                or record["after_kb_revision"] <= record["before_kb_revision"]
                or record["before_operational_sha256"] != record["after_operational_sha256"]):
            _record_fail()
        decoded = {}
        for name, maximum in _JSON_LIMITS.items():
            decoded[name] = _decode(record[name], maximum, result=name == "result_json")
            sha_name = name[:-5] + "_sha256"
            if hashlib.sha256(record[name].encode("utf-8")).hexdigest() != record[sha_name]:
                _record_fail()
        for prefix in ("before", "after"):
            version = decoded[prefix + "_version_json"]
            if (set(version) != {"library_id", "library_epoch", "kb_revision"}
                    or type(version["library_id"]) is not str
                    or type(version["library_epoch"]) is not int or type(version["kb_revision"]) is not int
                    or version != {"library_id": record["library_id"],
                        "library_epoch": record[prefix + "_library_epoch"], "kb_revision": record[prefix + "_kb_revision"]}):
                _record_fail()
        stamp = record["committed_at"]
        if type(stamp) is not str or not _STAMP.fullmatch(stamp):
            _record_fail()
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return record
    except HomeRecoveryRecordError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _record_fail()
