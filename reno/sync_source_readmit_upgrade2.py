"""Lossless Cloud readmit/dispose CHECK upgrade within a schema17 migration.

This is a table-variant migration, not a schema16-to17 migration or a runtime
capability. The caller owns the maintenance window, backup, outer transaction
and commit. As in migrations._migrate_locked, FK enforcement must be disabled
before BEGIN; every reference is checked here before and after the rebuild.
No metadata, queue item, control body, receipt or external anchor is rewritten.
"""
from __future__ import annotations

import sqlite3
import uuid

from . import budget_writer_witness as budget_sql
from . import storage_admin, storage_lifecycle as lifecycle
from . import sync_source_readmit_schema as old, sync_source_readmit_schema2 as new


_VARIANTS = {"v1": 0, "admission2": 1, "admission2_dispose2": 2}
_STAGING = {
    old.DISPOSITION: "_reno_source_disposition_upgrade2",
    old.ADMISSION: "_reno_source_admission_upgrade2",
}


class SourceReadmitUpgradeError(sqlite3.DatabaseError):
    def __init__(self, code):
        self.code = "SOURCE_READMIT_UPGRADE_" + code
        super().__init__(self.code)


def _fail(code):
    raise SourceReadmitUpgradeError(code) from None


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _cursor(connection, statement, parameters=()):
    # Observe actual SQLite values, without accepting a caller's row DTO.
    cursor = connection.cursor()
    cursor.row_factory = None
    try:
        cursor.execute(statement, parameters)
    except BaseException:
        try:
            cursor.close()
        except BaseException:
            pass
        raise
    return cursor


def _values(connection, statement, parameters=()):
    cursor = _cursor(connection, statement, parameters)
    try:
        values = tuple(cursor)
    except BaseException:
        try:
            cursor.close()
        except BaseException:
            pass
        raise
    else:
        cursor.close()
        return values


def _check(connection, profile, expected_guard=None):
    if budget_sql.in_write_window():
        _fail("MIGRATION_WINDOW_REQUIRED")
    if (type(connection) is not sqlite3.Connection or connection.text_factory is not str
            or connection.row_factory is not None and connection.row_factory is not sqlite3.Row):
        _fail("CONNECTION_REQUIRED")
    guard = lifecycle.require_guard(profile)
    if expected_guard is not None and guard is not expected_guard:
        _fail("GUARD_CHANGED")
    storage_admin.from_connection(connection, profile=profile)
    if (not connection.in_transaction
            or _values(connection, "PRAGMA foreign_keys") != ((0,),)):
        _fail("TRANSACTION_REQUIRED")
    if _values(connection,
            "SELECT value FROM main.meta WHERE key='migration_version'") != (("17",),):
        _fail("SCHEMA17_REQUIRED")
    return guard


def _rows(connection):
    result = {}
    for table, columns in old.COLUMNS.items():
        names = ",".join(_q(name) for name in columns)
        rows = _values(connection, f"SELECT {names} FROM main.{_q(table)} ORDER BY {_q(columns[0])}")
        for values in rows:
            # Also validate original JSON text/digests and every nullable pair.
            # This checks copies; it does not canonicalize stored text.
            try:
                new.validate_record(table, dict(zip(columns, values)))
            except old.SourceReadmitRecordError:
                _fail("RECORD_INVALID")
        result[table] = rows
    return result


def _foreign_keys(connection):
    if _values(connection, "PRAGMA main.foreign_key_check"):
        _fail("FOREIGN_KEY_INVALID")


def _staging_absent(connection):
    wanted = tuple(_STAGING.values())
    placeholders = ",".join("?" for _ in wanted)
    for master in ("main.sqlite_master", "sqlite_temp_master"):
        if _values(connection, f"SELECT name FROM {master} WHERE lower(name) IN ({placeholders})", wanted):
            _fail("STAGING_COLLISION")


def _other_objects(connection, rebuilt):
    return tuple(row for row in _values(connection,
        "SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name")
        if row[2] not in rebuilt)


def _temporary_objects(connection):
    return _values(connection,
        "SELECT type,name,tbl_name,sql FROM sqlite_temp_master ORDER BY type,name")


def _rebuild(connection, rebuilt, declarations):
    # Keep old names in the FK declarations. We never rename an old table,
    # which would rewrite other tables' references to its temporary name.
    for table in rebuilt:
        staging = _STAGING[table]
        connection.execute(f"CREATE TABLE main.{_q(staging)} ({declarations[table]})")
        names = ",".join(_q(name) for name in old.COLUMNS[table])
        connection.execute(f"INSERT INTO main.{_q(staging)}({names}) SELECT {names} FROM main.{_q(table)}")
    for table in rebuilt:
        connection.execute(f"DROP TABLE main.{_q(table)}")
    for table in rebuilt:
        connection.execute(f"ALTER TABLE main.{_q(_STAGING[table])} RENAME TO {_q(table)}")
    for statement in old.INDEXES + old.TRIGGERS:
        if any(f" ON {table}(" in statement or f" ON {table} " in statement for table in rebuilt):
            keyword = " INDEX " if " INDEX " in statement else " TRIGGER "
            connection.execute(statement.replace(keyword, keyword + "main.", 1))


def _rollback(connection, savepoint):
    # Preserve the original failure. Never explicitly rollback the caller's
    # outer transaction. SQLite itself can abort it on interruption/OOM.
    try:
        connection.execute(f"ROLLBACK TO {_q(savepoint)}")
    except BaseException:
        # Do not release a savepoint whose rollback was refused or lost.
        return
    try:
        connection.execute(f"RELEASE {_q(savepoint)}")
    except BaseException:
        pass


def upgrade_locked(connection, *, profile, target_variant="admission2_dispose2"):
    """Upgrade an exact existing17 variant in the caller's guarded transaction.

    Requires a real connection for the exact StorageProfile, its already-held
    lifecycle guard, and an active migration transaction with FK OFF before
    BEGIN. The function never opens files, changes FK mode, commits the outer
    transaction, installs an absent namespace or grants a runtime capability.
    Failure rolls back this function's savepoint when SQLite keeps the caller
    transaction alive. SQLite can itself abort the transaction on interruption
    or OOM. The outer migration must rollback on any error and restore FK
    enforcement after completion; this helper never submits an outer commit.
    """
    if type(target_variant) is not str or target_variant not in {"admission2", "admission2_dispose2"}:
        _fail("TARGET_INVALID")
    profile = lifecycle._profile(profile)
    guard = _check(connection, profile)
    before_variant = new.detect(connection)
    if _VARIANTS[before_variant] > _VARIANTS[target_variant]:
        _fail("DOWNGRADE_FORBIDDEN")
    _staging_absent(connection)
    _foreign_keys(connection)
    before = _rows(connection)
    counts = {table: len(rows) for table, rows in before.items()}
    if before_variant == target_variant:
        _check(connection, profile, guard)
        return {"from_variant": before_variant, "to_variant": target_variant,
            "changed": False, "row_counts": counts}
    rebuilt = tuple(table for table in (old.DISPOSITION, old.ADMISSION)
        if (table == old.DISPOSITION and target_variant == "admission2_dispose2"
            or table == old.ADMISSION and before_variant == "v1"))
    declarations = new.TABLES_DISPOSE2 if target_variant == "admission2_dispose2" else new.TABLES
    untouched = _other_objects(connection, rebuilt)
    temporary = _temporary_objects(connection)
    if any(row[0] == "trigger" and row[2].lower() in rebuilt for row in temporary):
        # DROP TABLE also removes attached TEMP triggers. These are not owned
        # by the source contract and must never be silently discarded.
        _fail("TEMP_DEPENDENCY_UNKNOWN")
    savepoint = "reno_readmit_upgrade2_" + uuid.uuid4().hex
    connection.execute(f"SAVEPOINT {_q(savepoint)}")
    try:
        _rebuild(connection, rebuilt, declarations)
        if new.detect(connection) != target_variant:
            _fail("VARIANT_CHANGED")
        _staging_absent(connection)
        _foreign_keys(connection)
        if _rows(connection) != before:
            _fail("RECORD_CHANGED")
        if _other_objects(connection, rebuilt) != untouched:
            _fail("OTHER_SCHEMA_CHANGED")
        if _temporary_objects(connection) != temporary:
            _fail("TEMP_SCHEMA_CHANGED")
        _check(connection, profile, guard)
        connection.execute(f"RELEASE {_q(savepoint)}")
    except BaseException:
        _rollback(connection, savepoint)
        raise
    return {"from_variant": before_variant, "to_variant": target_variant,
        "changed": True, "row_counts": counts}
