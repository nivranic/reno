"""Schema 7: committed knowledge preimages, installed by explicit migration."""
from __future__ import annotations

import sqlite3


TABLES = {
    "sync_checkpoint": """checkpoint_id TEXT PRIMARY KEY,
        publication_id TEXT NOT NULL UNIQUE REFERENCES sync_publication(publication_id),
        receiver_version_json TEXT NOT NULL,
        format_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,
        storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL, created_at TEXT NOT NULL""",
}
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_sync_checkpoint_created ON sync_checkpoint(created_at,checkpoint_id)",
)


def install(con):
    """Add only empty metadata tables; never commit, inspect files or seed rows."""
    for name, declaration in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({declaration})")
    for statement in INDEXES:
        con.execute(statement)


def _columns(con):
    return {row[1]: (row[2].upper(), row[3], row[4], row[5])
            for row in con.execute("PRAGMA table_info(sync_checkpoint)")}


def validate(con):
    reference = sqlite3.connect(":memory:")
    try:
        install(reference)
        if _columns(con) != _columns(reference):
            raise sqlite3.DatabaseError("Checkpoint schema has incompatible columns")
        if [tuple(row) for row in con.execute("PRAGMA foreign_key_list(sync_checkpoint)")] != [
                tuple(row) for row in reference.execute("PRAGMA foreign_key_list(sync_checkpoint)")]:
            raise sqlite3.DatabaseError("Checkpoint schema has incompatible publication references")
        unique = []
        for row in con.execute("PRAGMA index_list(sync_checkpoint)"):
            if row[2] and not row[4]:
                name = row[1].replace('"', '""')
                unique.append(tuple(item[2] for item in con.execute(f'PRAGMA index_info("{name}")')))
        if not all(key in unique for key in (("checkpoint_id",), ("publication_id",), ("storage_key",))):
            raise sqlite3.DatabaseError("Checkpoint schema lacks an immutable identity constraint")
        name = "idx_sync_checkpoint_created"
        actual = [tuple(row)[2:] for row in con.execute(f"PRAGMA index_xinfo({name})")]
        expected = [tuple(row)[2:] for row in reference.execute(f"PRAGMA index_xinfo({name})")]
        metadata = next((tuple(row)[2:] for row in con.execute("PRAGMA index_list(sync_checkpoint)")
                         if row[1] == name), None)
        if actual != expected or metadata != (0, "c", 0):
            raise sqlite3.DatabaseError("Checkpoint ordering index is missing or incompatible")
    finally:
        reference.close()
