"""Exact schema17 admission/disposition-v2 variants; historic v1 is preserved.

Only the declared format CHECKs change. No read upgrades a database, seeds
capabilities, repairs objects, or relaxes the original contract validator.
"""
from __future__ import annotations

from collections.abc import Mapping
import sqlite3

from . import sync_source_readmit_schema as old
from .storage_schema import _contract

SCHEMA_VERSION = old.SCHEMA_VERSION
ACTIVATION, REGISTRATION, DISPOSITION, ADMISSION = (
    old.ACTIVATION, old.REGISTRATION, old.DISPOSITION, old.ADMISSION)
COLUMNS = old.COLUMNS
INDEXES, TRIGGERS, RESERVED_NAMES = old.INDEXES, old.TRIGGERS, old.RESERVED_NAMES
TABLES = dict(old.TABLES)
_OLD_FORMAT = old._integer_sql("format_version", maximum=1)
_NEW_FORMAT = old._integer_sql("format_version", maximum=2)
if TABLES[ADMISSION].count(_OLD_FORMAT) != 1:
    raise RuntimeError("SOURCE_READMIT_SCHEMA_VARIANT_INVALID")
TABLES[ADMISSION] = TABLES[ADMISSION].replace(_OLD_FORMAT, _NEW_FORMAT, 1)
TABLES_DISPOSE2 = dict(TABLES)
if TABLES_DISPOSE2[DISPOSITION].count(_OLD_FORMAT) != 1:
    raise RuntimeError("SOURCE_DISPOSE_SCHEMA_VARIANT_INVALID")
TABLES_DISPOSE2[DISPOSITION] = TABLES_DISPOSE2[DISPOSITION].replace(_OLD_FORMAT, _NEW_FORMAT, 1)
SourceReadmitSchemaError = old.SourceReadmitSchemaError
SourceReadmitRecordError = old.SourceReadmitRecordError


def _install(con, *, tables=TABLES):
    old._require_queue(con)
    for name, declaration in tables.items():
        con.execute(f"CREATE TABLE {name} ({declaration})")
    for statement in INDEXES + TRIGGERS:
        con.execute(statement)


def _validate(con, tables):
    """Require this exact variant, including all names, FKs and triggers."""
    old._require_queue(con)
    if any(row[0].lower() in RESERVED_NAMES for row in con.execute("SELECT name FROM sqlite_temp_master")):
        old._shape_fail()
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute("CREATE TABLE sync_queue(item_id INTEGER PRIMARY KEY)")
        _install(reference, tables=tables)
        if (any(_contract(con, table) != _contract(reference, table) for table in tables)
                or old._triggers(con) != old._triggers(reference)):
            old._shape_fail()
        query = ("SELECT type,name,tbl_name FROM main.sqlite_master WHERE lower(name) IN ("
            + ",".join("?" for _ in RESERVED_NAMES) + ") ORDER BY name")
        if ([tuple(row) for row in con.execute(query, RESERVED_NAMES)]
                != [tuple(row) for row in reference.execute(query, RESERVED_NAMES)]):
            old._shape_fail()
    finally:
        reference.close()


def validate(con):
    """Require exactly admission2; this never adopts a disposition2 schema."""
    _validate(con, TABLES)


def validate_dispose2(con):
    """Require exactly the joint admission2/disposition2 declaration."""
    _validate(con, TABLES_DISPOSE2)


def detect(con):
    """Return the exact declared variant; never accept a third schema."""
    actual = {}
    for table in (ADMISSION, DISPOSITION):
        row = con.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        actual[table] = old._normalized(None if row is None else row[0])
    for variant, tables, validator in (("v1", old.TABLES, old.validate),
            ("admission2", TABLES, validate), ("admission2_dispose2", TABLES_DISPOSE2, validate_dispose2)):
        if all(actual[table] == old._normalized(f"CREATE TABLE {table} ({tables[table]})")
                for table in actual):
            validator(con)
            return variant
    old._shape_fail()


def validate_historical(con):
    """Explicit reader dispatch; the returned variant grants no write right."""
    return detect(con)


def preflight(con, *, require_absent=True):
    if type(require_absent) is not bool:
        old._shape_fail()
    for master in ("main.sqlite_master", "sqlite_temp_master"):
        found = con.execute("SELECT 1 FROM " + master + " WHERE lower(name) IN ("
            + ",".join("?" for _ in RESERVED_NAMES) + ") LIMIT 1", RESERVED_NAMES).fetchone()
        if found:
            if require_absent:
                old._shape_fail("SOURCE_READMIT_SCHEMA_COLLISION")
            validate(con)  # v1 is not implicitly upgraded or writable here.
            return True
    return False


def install(con):
    """DDL only, on absence; never rebuild an existing immutable table."""
    if not preflight(con, require_absent=False):
        _install(con)


def install_dispose2(con):
    """Install the explicit joint variant only on absence; never rebuild v1/2."""
    for master in ("main.sqlite_master", "sqlite_temp_master"):
        found = con.execute("SELECT 1 FROM " + master + " WHERE lower(name) IN ("
            + ",".join("?" for _ in RESERVED_NAMES) + ") LIMIT 1", RESERVED_NAMES).fetchone()
        if found:
            validate_dispose2(con)
            return
    _install(con, tables=TABLES_DISPOSE2)


def validate_record(table, mapping):
    if table not in {ADMISSION, DISPOSITION}:
        return old.validate_record(table, mapping)
    try:
        if not isinstance(mapping, (Mapping, sqlite3.Row)):
            old._record_fail()
        row = dict(mapping)
        if set(row) != set(COLUMNS[table]):
            old._record_fail()
        old._integer(row["format_version"], maximum=2)
        if row["format_version"] == 1:
            return old.validate_record(table, mapping)
        if table == ADMISSION:
            old._admission(row)
        else:
            old._control(row, disposition=True)
        return row
    except SourceReadmitRecordError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        old._record_fail()


def validate_admission_record(mapping):
    return validate_record(ADMISSION, mapping)


def validate_disposition_record(mapping):
    return validate_record(DISPOSITION, mapping)
