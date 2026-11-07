"""Schema 10 storage ownership, durable publication pins and GC receipts.

Ownership is not permission to delete. Installers neither discover files nor
adopt legacy uploads, retire checkpoints, create identities, or commit.
"""
from __future__ import annotations

import sqlite3


TABLES = {
    "storage_artifact": """artifact_id TEXT PRIMARY KEY NOT NULL,
        namespace TEXT NOT NULL CHECK(namespace IN ('sync_media','local_report','sync_report','checkpoint')),
        root_id TEXT NOT NULL, root_device TEXT NOT NULL, root_inode TEXT NOT NULL,
        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>=0),
        file_device TEXT NOT NULL, file_inode TEXT NOT NULL,
        file_mtime_ns TEXT NOT NULL, file_ctime_ns TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(namespace,root_id,storage_key)""",
    "sync_upload_pin": """pin_id TEXT PRIMARY KEY NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('publication','media','legacy')),
        publication_id TEXT, parent_pin_id TEXT REFERENCES sync_upload_pin(pin_id),
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        source_epoch INTEGER, source_revision INTEGER,
        target_library_id TEXT, target_library_epoch INTEGER, recovery_generation INTEGER,
        descriptor_json TEXT, intent_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved','stored','published','abandoned','legacy_hold')),
        artifact_id TEXT REFERENCES storage_artifact(artifact_id),
        abandon_operation_id TEXT, abandon_request_sha256 TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK((abandon_operation_id IS NULL AND abandon_request_sha256 IS NULL)
           OR (kind='publication' AND state='abandoned' AND abandon_operation_id IS NOT NULL
               AND abandon_request_sha256 IS NOT NULL)),
        CHECK((kind='publication' AND publication_id IS NOT NULL AND parent_pin_id IS NULL
                AND descriptor_json IS NULL AND artifact_id IS NULL
                AND state IN ('reserved','published','abandoned'))
           OR (kind='media' AND publication_id IS NOT NULL AND parent_pin_id IS NOT NULL
                AND descriptor_json IS NOT NULL AND state IN ('reserved','stored','published','abandoned')
                AND (state NOT IN ('stored','published') OR artifact_id IS NOT NULL))
           OR (kind='legacy' AND publication_id IS NULL AND parent_pin_id IS NULL
                AND descriptor_json IS NOT NULL AND state='legacy_hold')),
        CHECK((kind='legacy' AND source_epoch IS NULL AND source_revision IS NULL
                AND target_library_id IS NULL AND target_library_epoch IS NULL AND recovery_generation IS NULL)
           OR (kind IN ('publication','media') AND typeof(source_epoch)='integer' AND source_epoch>=1
                AND typeof(source_revision)='integer' AND source_revision>=0
                AND target_library_id IS NOT NULL AND typeof(target_library_epoch)='integer'
                AND target_library_epoch>=1 AND typeof(recovery_generation)='integer' AND recovery_generation>=0))""",
    "storage_gc_operation": """operation_id TEXT PRIMARY KEY NOT NULL,
        request_sha256 TEXT NOT NULL, preview_sha256 TEXT NOT NULL, roots_sha256 TEXT NOT NULL,
        policy_json TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT,
        state TEXT NOT NULL CHECK(state IN ('planned','running','completed','blocked')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "sync_checkpoint_retirement": """origin_kind TEXT NOT NULL CHECK(origin_kind IN ('publication','recovery')),
        checkpoint_id TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES storage_gc_operation(operation_id),
        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>0),
        retired_at TEXT NOT NULL, PRIMARY KEY(origin_kind,checkpoint_id)""",
    "storage_gc_item": """operation_id TEXT NOT NULL REFERENCES storage_gc_operation(operation_id),
        artifact_id TEXT NOT NULL REFERENCES storage_artifact(artifact_id),
        namespace TEXT NOT NULL CHECK(namespace IN ('sync_media','local_report','sync_report','checkpoint')),
        root_id TEXT NOT NULL, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>=0),
        file_device TEXT NOT NULL, file_inode TEXT NOT NULL,
        file_mtime_ns TEXT NOT NULL, file_ctime_ns TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','deleted','missing','retained','blocked')),
        deleted_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(deleted_bytes)='integer' AND deleted_bytes>=0
            AND deleted_bytes<=byte_size AND (state='deleted' OR deleted_bytes=0)),
        reason_code TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(operation_id,artifact_id)""",
}

INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_publication ON sync_upload_pin(publication_id) WHERE kind='publication'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_descriptor ON sync_upload_pin(parent_pin_id,descriptor_json) WHERE kind='media'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_abandon ON sync_upload_pin(abandon_operation_id) WHERE abandon_operation_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_upload_hold ON sync_upload_pin(state,artifact_id)",
    "CREATE INDEX IF NOT EXISTS idx_gc_item_state ON storage_gc_item(operation_id,state)",
)


def install(con):
    """Create only empty additive tables/indexes in the caller's transaction."""
    for table, declaration in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({declaration})")
    for sql in INDEXES:
        con.execute(sql)


def _contract(con, table):
    columns = [tuple(row)[1:] for row in con.execute(f"PRAGMA table_info({table})")]
    refs = [tuple(row) for row in con.execute(f"PRAGMA foreign_key_list({table})")]
    indexes = []
    for row in con.execute(f"PRAGMA index_list({table})"):
        quoted = row[1].replace('"', '""')
        sql = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (row[1],)).fetchone()
        indexes.append((tuple(row)[2:], tuple(tuple(c)[2:] for c in con.execute(f'PRAGMA index_xinfo("{quoted}")')),
                        " ".join((sql[0] if sql and sql[0] else "").split()).replace('"', '')))
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return columns, refs, sorted(indexes), " ".join((sql[0] if sql else "").split()).replace('"', '')


def validate(con):
    """Read-only validation, including partial predicates, CHECKs and FKs."""
    reference = sqlite3.connect(":memory:")
    try:
        install(reference)
        for table in TABLES:
            if _contract(con, table) != _contract(reference, table):
                raise sqlite3.DatabaseError(f"Storage schema is missing or incompatible: {table}")
    finally:
        reference.close()
