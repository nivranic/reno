"""Prospective schema17 SQL preservation, without a complete node certificate.

Only the committed/caller-consistent SQL representation is covered here.  The
actual State5, Cloud/Home permanent witnesses and restore/bundle producer
binding are deliberately not claimed.  A SqlCapture cannot grant admission,
dispatch, cross-restore continuity or a certificate17 preservation basis.
Released 15/16 certificates remain in source_preservation, with their original
version1 bytes and hashes; that ordinary capture still refuses schema17.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3

from . import budget, home_recovery_schema, source_preservation as base
from . import storage_admin, storage_lifecycle, sync_source_readmit_schema as schema


FORMAT_VERSION = 1
SCHEMA_VERSION = "17"
PROJECTIONS = base.PROJECTIONS_V17
_SQL_FIELDS = ("profile_sha256", "library_id", "node_id", "schema_sha256", "tables", "frontier")
_DOCUMENT_FIELDS = frozenset({"format_version", "schema_version", "scope", "continuity",
                              "knowledge_version", "sql_sha256", *_SQL_FIELDS})


@dataclass(frozen=True, slots=True, repr=False)
class SqlCapture:
    document_json: str
    sha256: str

    def public(self):
        return json.loads(self.document_json)


@dataclass(frozen=True, slots=True)
class SqlComparison:
    before_sha256: str
    after_sha256: str
    sql_sha256: str
    unchanged: bool = True


def capture_sql(con, *, profile, root_job_id):
    """Read all static private SQL columns from an already guarded transaction.

    No transaction, StateStore, permanent facility, schema installation or file
    is created.  in_transaction does not prove an IMMEDIATE writer or a durable
    commit.  Every core/item and every old paid/unknown fact is hashed, while
    the public frontier selects only the explicit original root_job_id.
    """
    if type(con) is not sqlite3.Connection or not con.in_transaction:
        base._fail("TRANSACTION_REQUIRED")
    if (type(profile) is not storage_lifecycle.StorageProfile
            or root_job_id is not None and (type(root_job_id) is not int or root_job_id < 1)):
        base._fail("PROFILE_MISMATCH")
    try:
        guard = storage_lifecycle.require_guard(profile)
        storage_admin.from_connection(con, profile=profile)
        version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
        if version is None or version[0] != SCHEMA_VERSION:
            base._fail("SCHEMA_UNSUPPORTED")
        schema_version, knowledge_version, sql = base._sql_capture(
            con, profile=profile, root_job_id=root_job_id, prospective_schema17=True)
        document = {"format_version": FORMAT_VERSION, "schema_version": schema_version,
            "scope": "sql_capture_comparison", "continuity": "not_established",
            "knowledge_version": knowledge_version, "sql_sha256": base._sha(sql), **sql}
        encoded = base._encoded(document)
        if len(encoded) > base.MAX_CERTIFICATE_BYTES:
            base._fail("LIMIT")
        guard.verify()
        return SqlCapture(encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest())
    except base.PreservationError:
        raise
    except storage_lifecycle.StorageLifecycleError:
        base._fail("PROFILE_MISMATCH")
    except (home_recovery_schema.HomeRecoverySchemaError, schema.SourceReadmitSchemaError):
        base._fail("SCHEMA_UNSUPPORTED")
    except (budget.BudgetLedgerError, schema.SourceReadmitRecordError):
        base._fail("LEDGER_INVALID")
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, OverflowError, RecursionError):
        base._fail("LEDGER_INVALID")


def _document(before):
    try:
        if type(before) is not SqlCapture or len(before.document_json.encode()) > base.MAX_CERTIFICATE_BYTES:
            base._fail("CERTIFICATE_INVALID")
        value = before.public()
        encoded = base._encoded(value)
        if (type(value) is not dict or set(value) != _DOCUMENT_FIELDS
                or encoded.decode() != before.document_json or hashlib.sha256(encoded).hexdigest() != before.sha256
                or type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION
                or value["schema_version"] != SCHEMA_VERSION or value["scope"] != "sql_capture_comparison"
                or value["continuity"] != "not_established"
                or base._sha({key: value[key] for key in _SQL_FIELDS}) != value["sql_sha256"]):
            base._fail("CERTIFICATE_INVALID")
        return value
    except base.PreservationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        base._fail("CERTIFICATE_INVALID")


def verify_sql_unchanged(con, before, *, profile):
    """Compare SQL only; State and external witness preservation remain unknown."""
    document = _document(before)
    after = capture_sql(con, profile=profile, root_job_id=document["frontier"]["root_job_id"])
    if after.public()["sql_sha256"] != document["sql_sha256"]:
        base._fail("CHANGED")
    return SqlComparison(before.sha256, after.sha256, document["sql_sha256"])
