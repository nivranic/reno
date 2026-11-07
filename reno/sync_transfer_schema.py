"""Atomic sync publication metadata, installed only by explicit migration.

The default keeps pre-sync local videos available. Remote publication controls
its own readiness; neither migration nor ordinary connections publish data,
rebind a peer, or create report receipts.
"""
from __future__ import annotations

import sqlite3


VIDEO_ADDITIONS = {"sync_state": "TEXT NOT NULL DEFAULT 'ready'"}

TABLES = {
    "sync_publication": """publication_id TEXT PRIMARY KEY,
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        source_epoch INTEGER NOT NULL, source_revision INTEGER NOT NULL,
        payload_sha256 TEXT NOT NULL, knowledge_sha256 TEXT NOT NULL,
        committed_at TEXT NOT NULL, local_version_json TEXT NOT NULL""",
    "sync_report": """name TEXT PRIMARY KEY,
        publication_id TEXT NOT NULL REFERENCES sync_publication(publication_id),
        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
        policy_version TEXT NOT NULL, source_version_json TEXT NOT NULL,
        local_version_json TEXT NOT NULL, generated_at TEXT NOT NULL,
        review_events_sha256 TEXT NOT NULL""",
}

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_sync_publication_source ON sync_publication"
    "(source_library_id,source_node_id,source_epoch,source_revision)",
)


def install(con):
    """Idempotent additive DDL; never commit, seed identities, or rewrite rows."""
    actual = {row[1] for row in con.execute("PRAGMA table_info(video_asset)")}
    if not actual:
        raise sqlite3.DatabaseError("Sync transfer schema requires the base video_asset table")
    # v0/v1 migration runs the whole installer before reaching its final step.
    # Add missing base columns first, so indexes never depend on absent fields.
    for name, declaration in VIDEO_ADDITIONS.items():
        if name not in actual:
            con.execute(f"ALTER TABLE video_asset ADD COLUMN {name} {declaration}")
    for name, declaration in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({declaration})")
    for statement in INDEXES:
        con.execute(statement)


def _columns(con, table):
    return {row[1]: (row[2].upper(), row[3], row[4], row[5])
            for row in con.execute(f"PRAGMA table_info({table})")}


def validate(con):
    """Read-only checks of publication types, defaults, keys and references."""
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute("CREATE TABLE video_asset(video_id TEXT PRIMARY KEY)")
        install(reference)
        for table in ("video_asset", *TABLES):
            expected = _columns(reference, table)
            actual = _columns(con, table)
            if table == "video_asset":
                expected = {name: expected[name] for name in VIDEO_ADDITIONS}
            if any(actual.get(name) != spec for name, spec in expected.items()):
                raise sqlite3.DatabaseError(f"Sync transfer schema has incompatible columns: {table}")
            if table in TABLES:
                if {name: spec[3] for name, spec in actual.items() if spec[3]} != {
                        name: spec[3] for name, spec in expected.items() if spec[3]}:
                    raise sqlite3.DatabaseError(f"Sync transfer schema has incompatible primary key: {table}")
                expected_refs = [tuple(row) for row in reference.execute(f"PRAGMA foreign_key_list({table})")]
                actual_refs = [tuple(row) for row in con.execute(f"PRAGMA foreign_key_list({table})")]
                if actual_refs != expected_refs:
                    raise sqlite3.DatabaseError(f"Sync transfer schema has incompatible references: {table}")
        for statement in INDEXES:
            name = statement.split()[5]
            expected = [tuple(row)[2:] for row in reference.execute(f"PRAGMA index_xinfo({name})")]
            actual = [tuple(row)[2:] for row in con.execute(f"PRAGMA index_xinfo({name})")]
            # Ignore physical column positions; migrated tables can differ.
            index = next((tuple(row)[2:] for row in con.execute("PRAGMA index_list(sync_publication)")
                          if row[1] == name), None)
            if actual != expected or index != (0, "c", 0):
                raise sqlite3.DatabaseError(f"Sync transfer schema index is missing or incompatible: {name}")
    finally:
        reference.close()
