"""Schema 8: local recovery receipts, protection images and persistent fences."""
from __future__ import annotations

import sqlite3

TABLES = {
    "sync_recovery_operation": """operation_id TEXT PRIMARY KEY,
        action TEXT NOT NULL CHECK(action IN ('restore','resume')),
        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
        result_json TEXT NOT NULL, created_at TEXT NOT NULL""",
    "sync_recovery_control": """slot INTEGER PRIMARY KEY CHECK(slot=1),
        state TEXT NOT NULL CHECK(state IN ('running','held','resume_pending')),
        generation INTEGER NOT NULL CHECK(generation>=0),
        revision INTEGER NOT NULL CHECK(revision>=0), source_floor_json TEXT,
        restore_operation_id TEXT REFERENCES sync_recovery_operation(operation_id),
        updated_at TEXT NOT NULL""",
    "sync_recovery_checkpoint": """checkpoint_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL UNIQUE REFERENCES sync_recovery_operation(operation_id),
        origin_kind TEXT NOT NULL CHECK(origin_kind='recovery'),
        receiver_version_json TEXT NOT NULL,
        format_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,
        storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL, created_at TEXT NOT NULL""",
    "sync_publication_fence": """publication_id TEXT PRIMARY KEY REFERENCES sync_publication(publication_id),
        generation INTEGER NOT NULL CHECK(generation>=0)""",
}
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_sync_recovery_created ON sync_recovery_operation(created_at,operation_id)",
    "CREATE INDEX IF NOT EXISTS idx_sync_recovery_checkpoint_created ON sync_recovery_checkpoint(created_at,checkpoint_id)",
)


def install(con):
    """Additive, empty DDL only: no implicit holds or publication backfill."""
    for table, declaration in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({declaration})")
    for sql in INDEXES:
        con.execute(sql)


def _contract(con, table):
    columns = [(row[1], *tuple(row)[2:]) for row in con.execute(f"PRAGMA table_info({table})")]
    refs = [tuple(row) for row in con.execute(f"PRAGMA foreign_key_list({table})")]
    indexes = []
    for row in con.execute(f"PRAGMA index_list({table})"):
        quoted = row[1].replace('"', '""')
        indexes.append((tuple(row)[2:], tuple(tuple(c)[2:] for c in con.execute(f'PRAGMA index_xinfo("{quoted}")'))))
    # CHECK constraints also enforce the singleton and finite state machine.
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return columns, refs, sorted(indexes), " ".join((sql[0] if sql else "").split()).replace('"', '')


def validate(con):
    reference = sqlite3.connect(":memory:")
    try:
        install(reference)
        for table in TABLES:
            if _contract(con, table) != _contract(reference, table):
                raise sqlite3.DatabaseError(f"Recovery schema is missing or incompatible: {table}")
    finally:
        reference.close()
