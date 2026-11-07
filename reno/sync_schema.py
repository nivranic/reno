"""Sync ownership schema, installed only inside explicit migration transactions.

No pair, session or publication is seeded here. In particular, publication
receipts are written only by the future media/closure publication transaction;
queue completion may read them but cannot manufacture them.
"""
from __future__ import annotations

import re
import sqlite3

from .storage_schema import _contract


QUEUE_ADDITIONS = {
    "owner_id": "TEXT",
    "owner_generation": "INTEGER NOT NULL DEFAULT 0",
    "lease_token": "TEXT",
    "lease_generation": "INTEGER NOT NULL DEFAULT 0",
    "retry_at": "TEXT",
    "video_id": "TEXT",
    "publication_id": "TEXT",
    "result_hash": "TEXT",
    "budget_wait_reason": "TEXT",
    "budget_operation_id": "TEXT",
}

V14_TABLES = {
    "sync_pair": """slot INTEGER PRIMARY KEY CHECK(slot=1),
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
        protocol_version INTEGER NOT NULL, schema_fingerprint TEXT NOT NULL,
        paired_at TEXT NOT NULL""",
    "sync_session": """slot INTEGER PRIMARY KEY REFERENCES sync_pair(slot),
        session_id TEXT NOT NULL, lease_token TEXT NOT NULL, generation INTEGER NOT NULL,
        lease_until TEXT NOT NULL, updated_at TEXT NOT NULL,
        metrics_json TEXT NOT NULL DEFAULT '{}'""",
    "sync_published_video": """publication_id TEXT NOT NULL, video_id TEXT NOT NULL,
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        source_epoch INTEGER NOT NULL, source_revision INTEGER NOT NULL,
        media_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,
        PRIMARY KEY(publication_id,video_id)""",
    "sync_queue_publication": """item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),
        lease_generation INTEGER NOT NULL, lease_token TEXT NOT NULL,
        owner_id TEXT NOT NULL, owner_generation INTEGER NOT NULL,
        publication_id TEXT NOT NULL, video_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(item_id,lease_generation),
        FOREIGN KEY(publication_id,video_id)
            REFERENCES sync_published_video(publication_id,video_id)""",
    "sync_queue_budget_receipt": """request_id TEXT PRIMARY KEY,
        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('budget_defer','budget_resume')),
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
        receiver_library_epoch INTEGER NOT NULL CHECK(receiver_library_epoch>=1),
        recovery_generation INTEGER NOT NULL CHECK(recovery_generation>=0),
        item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),
        lease_generation INTEGER NOT NULL CHECK(lease_generation>=1),
        result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,
        CHECK(length(CAST(request_json AS BLOB))<=16384),
        CHECK(length(CAST(result_json AS BLOB))<=16384)""",
}

# Keep the exact schema14 table declarations, including budget receipt CHECKs,
# available for genuine historical fixtures and lossless migration review.
TABLES = {**V14_TABLES,
    "sync_queue_source_receipt": """request_id TEXT PRIMARY KEY,
        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action='source_fail'),
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
        receiver_library_epoch INTEGER NOT NULL CHECK(receiver_library_epoch>=1),
        recovery_generation INTEGER NOT NULL CHECK(recovery_generation>=0),
        item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),
        lease_generation INTEGER NOT NULL CHECK(lease_generation>=1),
        core_sha256 TEXT NOT NULL, submitted_url_sha256 TEXT NOT NULL, failure_sha256 TEXT NOT NULL,
        result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,
        CHECK(length(CAST(request_json AS BLOB))<=16384),
        CHECK(length(CAST(result_json AS BLOB))<=16384)""",
}

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_syncq_claim ON sync_queue(status,retry_at,item_id)",
    "CREATE INDEX IF NOT EXISTS idx_syncq_owner ON sync_queue(owner_id,owner_generation,status)",
    "CREATE INDEX IF NOT EXISTS idx_syncq_lease ON sync_queue(status,lease_until)",
    "CREATE INDEX IF NOT EXISTS idx_sync_publication_video ON sync_published_video(video_id,committed_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_budget_claim_defer ON sync_queue_budget_receipt(item_id,lease_generation) WHERE action='budget_defer'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_source_terminal_item ON sync_queue_source_receipt(item_id)",
)


def _install_tables(con):
    for name, columns in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")


def install(con):
    """Idempotent additive DDL only: no commit, seeds or old-row updates.

    Old v0/v1 migrations call the full schema installer early. Add queue columns
    here before indexes so those paths cannot hit an index on a missing column.
    The base sync_queue is supplied by schema.sql, including in new databases.
    """
    actual = {row[1] for row in con.execute("PRAGMA table_info(sync_queue)")}
    if not actual:
        raise sqlite3.DatabaseError("Sync schema requires the base sync_queue table")
    for name, declaration in QUEUE_ADDITIONS.items():
        if name not in actual:
            con.execute(f"ALTER TABLE sync_queue ADD COLUMN {name} {declaration}")
    _install_tables(con)
    for statement in INDEXES:
        con.execute(statement)


def _columns(con, table):
    return {row[1]: (row[2].upper(), row[3], row[4], row[5])
            for row in con.execute(f"PRAGMA table_info({table})")}


def validate(con):
    """Read-only checks for ownership columns, singleton and reference fences."""
    reference = sqlite3.connect(":memory:")
    try:
        additions = ",".join(f"{name} {declaration}" for name, declaration in QUEUE_ADDITIONS.items())
        reference.execute("CREATE TABLE sync_queue(item_id INTEGER PRIMARY KEY,status TEXT,lease_until TEXT,"
                          + additions + ")")
        install(reference)
        for table in (*TABLES, "sync_queue"):
            expected = _columns(reference, table)
            if table == "sync_queue":
                expected = {name: expected[name] for name in QUEUE_ADDITIONS}
            actual = _columns(con, table)
            if any(actual.get(name) != spec for name, spec in expected.items()):
                raise sqlite3.DatabaseError(f"Sync schema has incompatible columns: {table}")
            if table != "sync_queue":
                expected_refs = [tuple(row) for row in reference.execute(f"PRAGMA foreign_key_list({table})")]
                actual_refs = [tuple(row) for row in con.execute(f"PRAGMA foreign_key_list({table})")]
                if actual_refs != expected_refs:
                    raise sqlite3.DatabaseError(f"Sync schema has incompatible references: {table}")
        pair_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_pair'").fetchone()
        if not pair_sql or not re.search(r"CHECK\s*\(\s*slot\s*=\s*1\s*\)", pair_sql[0], re.IGNORECASE):
            raise sqlite3.DatabaseError("Sync pair singleton constraint is missing")
        for receipt in ("sync_queue_budget_receipt", "sync_queue_source_receipt"):
            if _contract(con, receipt) != _contract(reference, receipt):
                raise sqlite3.DatabaseError("Sync receipt schema is missing or incompatible")
        for statement in INDEXES:
            name = re.search(r"INDEX IF NOT EXISTS (\w+)", statement).group(1)
            expected = [tuple(row)[1:] for row in reference.execute(f"PRAGMA index_info({name})")]
            actual = [tuple(row)[1:] for row in con.execute(f"PRAGMA index_info({name})")]
            # Physical column positions can differ on migrated databases.
            if [row[1] for row in actual] != [row[1] for row in expected]:
                raise sqlite3.DatabaseError(f"Sync schema index is missing or incompatible: {name}")
            expected_sql = reference.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()[0]
            actual_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
            if actual_sql is None or " ".join(actual_sql[0].split()) != " ".join(expected_sql.split()):
                raise sqlite3.DatabaseError(f"Sync schema index declaration is incompatible: {name}")
    finally:
        reference.close()
