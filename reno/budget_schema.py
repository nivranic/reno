"""Local operational budget DDL; no admission, configuration or transport.

Fresh installation belongs to the caller's transaction. Frozen schemas 11/12 are
rebuilt only by the explicit, backed-up migration. Historical job lineage is
seeded separately, only by the explicit schema migration.
"""
from __future__ import annotations

import sqlite3
import uuid

from .budget_contract import DEFER_REASONS, MAX_RESULT_BYTES, MAX_UNITS
from .storage_schema import _contract

MAX_SNAPSHOT_BYTES = 64 * 1024


def _uuid(name, *, nullable=False):
    check = (f"typeof({name})='text' AND length({name})=36 AND "
             f"substr({name},9,1)='-' AND substr({name},14,1)='-' AND "
             f"substr({name},19,1)='-' AND substr({name},24,1)='-' AND "
             f"length(replace({name},'-',''))=32 AND replace({name},'-','') NOT GLOB '*[^0-9a-f]*'")
    return f"TEXT {'NULL' if nullable else 'NOT NULL'} CHECK({name} IS NULL OR ({check}))" if nullable else f"TEXT NOT NULL CHECK({check})"


def _sha(name, *, nullable=False):
    check = f"typeof({name})='text' AND length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'"
    return f"TEXT CHECK({name} IS NULL OR ({check}))" if nullable else f"TEXT NOT NULL CHECK({check})"


def _integer(name, *, minimum=0, maximum=MAX_UNITS, default=None, nullable=False):
    clause = f"typeof({name})='integer' AND {name}>={minimum} AND {name}<={maximum}"
    return ("INTEGER" + ("" if nullable else " NOT NULL") +
            (f" DEFAULT {default}" if default is not None else "") +
            f" CHECK({name} IS NULL OR ({clause}))" if nullable else
            "INTEGER NOT NULL" + (f" DEFAULT {default}" if default is not None else "") + f" CHECK({clause})")


def _text(name, maximum, *, nullable=False, default=None, minimum=0):
    clause = f"typeof({name})='text' AND length(CAST({name} AS BLOB)) BETWEEN {minimum} AND {maximum}"
    return ("TEXT" + ("" if nullable else " NOT NULL") +
            (f" DEFAULT '{default}'" if default is not None else "") +
            f" CHECK({name} IS NULL OR ({clause}))" if nullable else
            "TEXT NOT NULL" + (f" DEFAULT '{default}'" if default is not None else "") + f" CHECK({clause})")


def _stamp(name, *, nullable=False):
    clause = f"typeof({name})='text' AND length({name})=27 AND {name} GLOB '????-??-??T??:??:??.??????Z'"
    return f"TEXT CHECK({name} IS NULL OR ({clause}))" if nullable else f"TEXT NOT NULL CHECK({clause})"


def _enum(name, values, *, default=None, nullable=False):
    choices = ",".join("'" + value + "'" for value in sorted(values))
    return ("TEXT" + ("" if nullable else " NOT NULL") +
            (f" DEFAULT '{default}'" if default is not None else "") +
            f" CHECK({name} IS NULL OR ({name} IN ({choices})))" if nullable else
            "TEXT NOT NULL" + (f" DEFAULT '{default}'" if default is not None else "") + f" CHECK({name} IN ({choices}))")


JOB_COLUMNS = {
    "continuation_id": _uuid("continuation_id", nullable=True),
    "execution_generation": _integer("execution_generation", minimum=1, default=1),
    "not_before": _integer("not_before", nullable=True),
    "blocked_reason": _enum("blocked_reason", DEFER_REASONS, nullable=True),
    "wait_mode": "TEXT NOT NULL DEFAULT 'none' CHECK(wait_mode IN ('none','until','blocked')) "
                 "CHECK((wait_mode='none' AND not_before IS NULL AND blocked_reason IS NULL) OR "
                 "(wait_mode='until' AND not_before IS NOT NULL AND blocked_reason IS NOT NULL) OR "
                 "(wait_mode='blocked' AND not_before IS NULL AND blocked_reason IS NOT NULL))",
    "manual_paused": _integer("manual_paused", maximum=1, default=0),
    "budget_operation_id": _uuid("budget_operation_id", nullable=True) + " REFERENCES budget_operation(operation_id)",
}

# Keep the exact frozen declarations for validation and lossless v11 copying.
LEGACY_TABLES = {
    "budget_operation": ",\n".join((
        "operation_id " + _uuid("operation_id") + " PRIMARY KEY",
        "library_id " + _uuid("library_id"),
        "identity_sha256 " + _sha("identity_sha256"),
        "continuation_id " + _uuid("continuation_id"),
        "execution_generation " + _integer("execution_generation", minimum=1),
        "kind " + _enum("kind", ("model", "download")),
        "suboperation_key " + _text("suboperation_key", 512, minimum=1),
        "input_sha256 " + _sha("input_sha256"),
        "root_job_id " + _integer("root_job_id", minimum=1, nullable=True) + " REFERENCES job(job_id)",
        "max_attempts " + _integer("max_attempts", minimum=1, maximum=128, default=8),
        "state " + _enum("state", ("open", "waiting", "completed", "exhausted"), default="open"),
        "wait_reason " + _enum("wait_reason", DEFER_REASONS, nullable=True),
        "resume_at " + _stamp("resume_at", nullable=True),
        "wait_details_json " + _text("wait_details_json", MAX_SNAPSHOT_BYTES, default="{}"),
        "revision " + _integer("revision", default=0),
        "created_at " + _stamp("created_at"),
        "updated_at " + _stamp("updated_at"),
        "CHECK(resume_at IS NULL OR wait_reason IS NOT NULL)",
    )),
    "budget_attempt": ",\n".join((
        "attempt_id " + _uuid("attempt_id") + " PRIMARY KEY",
        "operation_id " + _uuid("operation_id") + " REFERENCES budget_operation(operation_id)",
        "ordinal " + _integer("ordinal", minimum=1, maximum=128),
        "request_sha256 " + _sha("request_sha256"),
        "state " + _enum("state", ("reserved", "dispatching", "indeterminate", "settled", "released")),
        "initial_reservation_json " + _text("initial_reservation_json", MAX_SNAPSHOT_BYTES),
        "initial_reservation_sha256 " + _sha("initial_reservation_sha256"),
        "current_reservation_json " + _text("current_reservation_json", MAX_SNAPSHOT_BYTES),
        "current_reservation_sha256 " + _sha("current_reservation_sha256"),
        "reservation_revision " + _integer("reservation_revision", default=0),
        "reservation_held " + _integer("reservation_held", maximum=1, default=1),
        "current_policy_sha256 " + _sha("current_policy_sha256"),
        "current_period_key " + _text("current_period_key", 256, minimum=1),
        "current_window_start_utc " + _stamp("current_window_start_utc"),
        "current_window_end_utc " + _stamp("current_window_end_utc"),
        "first_dispatch_json " + _text("first_dispatch_json", MAX_SNAPSHOT_BYTES, nullable=True),
        "first_dispatch_sha256 " + _sha("first_dispatch_sha256", nullable=True),
        "dispatch_token_sha256 " + _sha("dispatch_token_sha256", nullable=True),
        "dispatched_at " + _stamp("dispatched_at", nullable=True),
        "last_admitted_at " + _stamp("last_admitted_at"),
        "response_complete " + _integer("response_complete", maximum=1, default=0),
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error"), nullable=True),
        "observed_usage_json " + _text("observed_usage_json", MAX_SNAPSHOT_BYTES, nullable=True),
        "usage_quality " + _enum("usage_quality", ("reported", "partial", "missing", "invalid"), default="missing"),
        "provider_request_id " + _text("provider_request_id", 256, nullable=True),
        "result_text " + _text("result_text", MAX_RESULT_BYTES, nullable=True),
        "result_sha256 " + _sha("result_sha256", nullable=True),
        "result_bytes " + _integer("result_bytes", default=0),
        "result_storage_state " + _enum("result_storage_state", ("none", "stored", "oversize", "capacity_blocked", "retired"), default="none"),
        "settlement_sha256 " + _sha("settlement_sha256", nullable=True),
        "settled_at " + _stamp("settled_at", nullable=True),
        "indeterminate_at " + _stamp("indeterminate_at", nullable=True),
        "created_at " + _stamp("created_at"),
        "updated_at " + _stamp("updated_at"),
        "CHECK(current_window_start_utc<current_window_end_utc)",
        "CHECK((state IN ('reserved','released') AND first_dispatch_json IS NULL AND first_dispatch_sha256 IS NULL "
        "AND dispatch_token_sha256 IS NULL AND dispatched_at IS NULL) OR "
        "(state IN ('dispatching','indeterminate','settled') AND first_dispatch_json IS NOT NULL AND first_dispatch_sha256 IS NOT NULL "
        "AND dispatch_token_sha256 IS NOT NULL AND dispatched_at IS NOT NULL))",
        "CHECK((state='settled' AND response_complete=1 AND outcome IS NOT NULL AND settlement_sha256 IS NOT NULL AND settled_at IS NOT NULL) "
        "OR (state!='settled' AND response_complete=0 AND outcome IS NULL AND settlement_sha256 IS NULL AND settled_at IS NULL))",
        "CHECK((result_storage_state='stored' AND result_text IS NOT NULL AND result_sha256 IS NOT NULL "
        "AND result_bytes=length(CAST(result_text AS BLOB))) OR (result_storage_state!='stored' AND result_text IS NULL))",
    )),
}

SOURCE_KINDS = ("source_metadata", "source_enumeration", "source_resolution")
KINDS = ("model", "download", *SOURCE_KINDS)
V12_TABLES = {
    "budget_operation": LEGACY_TABLES["budget_operation"].replace(
        "kind " + _enum("kind", ("model", "download")), "kind " + _enum("kind", KINDS), 1),
    "budget_attempt": LEGACY_TABLES["budget_attempt"].replace(
        "request_sha256 " + _sha("request_sha256") + ",\n",
        "request_sha256 " + _sha("request_sha256") + ",\n" +
        "download_segment " + _enum("download_segment", ("video", "audio"), nullable=True) + ",\n", 1).replace(
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error"), nullable=True),
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete"), nullable=True), 1),
}

V13_TABLES = {
    **V12_TABLES,
    "budget_attempt": V12_TABLES["budget_attempt"].replace(
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete"), nullable=True),
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete", "source_retry"), nullable=True), 1),
}

TABLES = {
    **V13_TABLES,
    "budget_attempt": V13_TABLES["budget_attempt"].replace(
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete", "source_retry"), nullable=True),
        "outcome " + _enum("outcome", ("full_result", "http_retry", "repair", "terminal_error", "source_step_complete", "source_retry", "resolution_retry"), nullable=True), 1),
}

LEGACY_COLUMNS = {
    "budget_operation": ("operation_id", "library_id", "identity_sha256", "continuation_id",
        "execution_generation", "kind", "suboperation_key", "input_sha256", "root_job_id", "max_attempts",
        "state", "wait_reason", "resume_at", "wait_details_json", "revision", "created_at", "updated_at"),
    "budget_attempt": ("attempt_id", "operation_id", "ordinal", "request_sha256", "state",
        "initial_reservation_json", "initial_reservation_sha256", "current_reservation_json",
        "current_reservation_sha256", "reservation_revision", "reservation_held", "current_policy_sha256",
        "current_period_key", "current_window_start_utc", "current_window_end_utc", "first_dispatch_json",
        "first_dispatch_sha256", "dispatch_token_sha256", "dispatched_at", "last_admitted_at", "response_complete",
        "outcome", "observed_usage_json", "usage_quality", "provider_request_id", "result_text", "result_sha256",
        "result_bytes", "result_storage_state", "settlement_sha256", "settled_at", "indeterminate_at", "created_at", "updated_at"),
}

V12_COLUMNS = {
    "budget_operation": LEGACY_COLUMNS["budget_operation"],
    "budget_attempt": ("attempt_id", "operation_id", "ordinal", "request_sha256", "download_segment", "state",
        "initial_reservation_json", "initial_reservation_sha256", "current_reservation_json",
        "current_reservation_sha256", "reservation_revision", "reservation_held", "current_policy_sha256",
        "current_period_key", "current_window_start_utc", "current_window_end_utc", "first_dispatch_json",
        "first_dispatch_sha256", "dispatch_token_sha256", "dispatched_at", "last_admitted_at", "response_complete",
        "outcome", "observed_usage_json", "usage_quality", "provider_request_id", "result_text", "result_sha256",
        "result_bytes", "result_storage_state", "settlement_sha256", "settled_at", "indeterminate_at", "created_at", "updated_at"),
}

INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_operation_identity ON budget_operation(identity_sha256)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_operation_scope ON budget_operation(library_id,continuation_id,execution_generation,kind,suboperation_key,input_sha256)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_attempt_ordinal ON budget_attempt(operation_id,ordinal)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_dispatch_token ON budget_attempt(dispatch_token_sha256) WHERE dispatch_token_sha256 IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_budget_attempt_window ON budget_attempt(state,current_window_start_utc,current_window_end_utc)",
    "CREATE INDEX IF NOT EXISTS idx_budget_attempt_operation ON budget_attempt(operation_id,state)",
    "CREATE INDEX IF NOT EXISTS idx_budget_job_lineage ON job(continuation_id,execution_generation)",
)


def _install(con, tables):
    for table, declaration in tables.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({declaration})")
    columns = {row[1] for row in con.execute("PRAGMA table_info(job)")}
    if not columns:
        raise sqlite3.DatabaseError("Budget schema needs the existing task table")
    for name, declaration in JOB_COLUMNS.items():
        if name not in columns:
            con.execute(f"ALTER TABLE job ADD COLUMN {name} {declaration}")
    for sql in INDEXES:
        con.execute(sql)


def install(con):
    """Fresh DDL only; never silently upgrade existing frozen budget tables."""
    _install(con, TABLES)


def _normalized(sql):
    return " ".join(sql.split()).replace('"', '')


def validate(con, *, version=14):
    """Strict read-only budget structure and additive job column contract."""
    if type(version) is not int or version not in (11, 12, 13, 14):
        raise sqlite3.DatabaseError("Unsupported budget schema contract")
    tables = {11: LEGACY_TABLES, 12: V12_TABLES, 13: V13_TABLES, 14: TABLES}[version]
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute("CREATE TABLE job(job_id INTEGER PRIMARY KEY)")
        _install(reference, tables)
        for table in tables:
            if _contract(con, table) != _contract(reference, table):
                raise sqlite3.DatabaseError("Budget schema is missing or incompatible: " + table)
        expected = {row[1]: tuple(row)[2:] for row in reference.execute("PRAGMA table_info(job)") if row[1] in JOB_COLUMNS}
        actual = {row[1]: tuple(row)[2:] for row in con.execute("PRAGMA table_info(job)") if row[1] in JOB_COLUMNS}
        raw = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'").fetchone()
        sql = "" if raw is None else _normalized(raw[0])
        if actual != expected or any(_normalized(name + " " + declaration) not in sql for name, declaration in JOB_COLUMNS.items()):
            raise sqlite3.DatabaseError("Budget task columns are missing or incompatible")
        expected_refs = [tuple(row)[2:] for row in reference.execute("PRAGMA foreign_key_list(job)")]
        actual_refs = [tuple(row)[2:] for row in con.execute("PRAGMA foreign_key_list(job)") if row[3] == "budget_operation_id"]
        if actual_refs != expected_refs:
            raise sqlite3.DatabaseError("Budget task operation reference is incompatible")
        expected_index = reference.execute("SELECT sql FROM sqlite_master WHERE name='idx_budget_job_lineage'").fetchone()[0]
        actual_index = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_budget_job_lineage'").fetchone()
        if actual_index is None or _normalized(actual_index[0]) != _normalized(expected_index):
            raise sqlite3.DatabaseError("Budget task lineage index is incompatible")
    finally:
        reference.close()


def upgrade_v11(con):
    """Lossless two-table rebuild inside the explicit migration transaction.

    SQLite's standard table rebuild needs FK enforcement disabled before BEGIN;
    the caller must check every FK before commit and restore enforcement after.
    No old identity, snapshot, receipt, private body or task reference is edited.
    """
    if not con.in_transaction or con.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise sqlite3.DatabaseError("Budget rebuild requires the private migration transaction")
    validate(con, version=11)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('budget_operation','budget_attempt')").fetchone():
        raise sqlite3.DatabaseError("Unknown budget trigger prevents lossless migration")
    temporary = {table: "_" + table + "_v12" for table in V12_TABLES}
    for name in temporary.values():
        if con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
            raise sqlite3.DatabaseError("Budget rebuild name is already in use")
    for table, declaration in V12_TABLES.items():
        declaration = declaration.replace("REFERENCES budget_operation(operation_id)",
                                          "REFERENCES _budget_operation_v12(operation_id)")
        con.execute(f"CREATE TABLE {temporary[table]} ({declaration})")
        names = ",".join(LEGACY_COLUMNS[table])
        con.execute(f"INSERT INTO {temporary[table]}({names}) SELECT {names} FROM {table}")
    con.execute("DROP TABLE budget_attempt")
    con.execute("DROP TABLE budget_operation")
    for table in V12_TABLES:
        con.execute(f"ALTER TABLE {temporary[table]} RENAME TO {table}")
    _install(con, V12_TABLES)
    validate(con, version=12)


def upgrade_v12(con):
    """Preserve every v12 fact while adding the explicit source-retry outcome."""
    if not con.in_transaction or con.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise sqlite3.DatabaseError("Budget rebuild requires the private migration transaction")
    validate(con, version=12)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('budget_operation','budget_attempt')").fetchone():
        raise sqlite3.DatabaseError("Unknown budget trigger prevents lossless migration")
    temporary = "_budget_attempt_v13"
    if con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (temporary,)).fetchone():
        raise sqlite3.DatabaseError("Budget rebuild name is already in use")
    con.execute(f"CREATE TABLE {temporary} ({V13_TABLES['budget_attempt']})")
    names = ",".join(V12_COLUMNS["budget_attempt"])
    con.execute(f"INSERT INTO {temporary}({names}) SELECT {names} FROM budget_attempt")
    con.execute("DROP TABLE budget_attempt")
    con.execute(f"ALTER TABLE {temporary} RENAME TO budget_attempt")
    _install(con, V13_TABLES)
    validate(con, version=13)


def upgrade_v13(con):
    """Losslessly add the separately proved resolution retry outcome."""
    if not con.in_transaction or con.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise sqlite3.DatabaseError("Budget rebuild requires the private migration transaction")
    validate(con, version=13)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('budget_operation','budget_attempt')").fetchone():
        raise sqlite3.DatabaseError("Unknown budget trigger prevents lossless migration")
    temporary = "_budget_attempt_v14"
    if con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (temporary,)).fetchone():
        raise sqlite3.DatabaseError("Budget rebuild name is already in use")
    con.execute(f"CREATE TABLE {temporary} ({TABLES['budget_attempt']})")
    names = ",".join(V12_COLUMNS["budget_attempt"])
    con.execute(f"INSERT INTO {temporary}({names}) SELECT {names} FROM budget_attempt")
    con.execute("DROP TABLE budget_attempt")
    con.execute(f"ALTER TABLE {temporary} RENAME TO budget_attempt")
    install(con)
    validate(con)


def seed_legacy_job_lineage(con):
    """Explicit migration only; every old job is its own durable lineage."""
    if not con.in_transaction:
        raise sqlite3.DatabaseError("Budget lineage seed requires the migration transaction")
    row = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
    try:
        library = uuid.UUID(row[0])
    except (TypeError, ValueError, AttributeError, IndexError):
        raise sqlite3.DatabaseError("Budget lineage needs the existing library identity") from None
    for job in con.execute("SELECT job_id FROM job WHERE continuation_id IS NULL ORDER BY job_id").fetchall():
        continuation = str(uuid.uuid5(library, f"budget-lineage:v1:{job[0]}"))
        con.execute("UPDATE job SET continuation_id=?,execution_generation=1 WHERE job_id=? AND continuation_id IS NULL",
                    (continuation, job[0]))
