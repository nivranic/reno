"""Explicit artificial profiles; no production root inference or patched gates."""
from pathlib import Path

from reno import storage_backups, storage_lifecycle


def migration_profile(path):
    path = Path(path).absolute()
    root = path.parent / (path.name + ".test-storage")
    return storage_lifecycle.StorageProfile(path, root / "media", root / "reports", root / "checkpoints")


def initialize_migration_storage(path):
    """Call after creating/closing the fixture DB, before opening test readers."""
    value = migration_profile(path)
    storage_backups.initialize_offline(value)
    return value


def migration_notes(path):
    """Released attachment explanations, excluding new dependency descriptors."""
    path = Path(path)
    return sorted(item for item in path.parent.glob(path.name + ".bak-migrate-*.json")
                  if not item.name.endswith(storage_backups.SUFFIX))


def migration_arguments(path):
    value = migration_profile(path)
    return ["--db", str(value.database), "--media", str(value.media),
            "--reports", str(value.reports), "--checkpoints", str(value.checkpoints)]


def drop_future_home_recovery(con):
    """Remove schema16 DDL when building a genuinely earlier test image."""
    from reno import home_recovery_schema
    con.execute('DROP TABLE IF EXISTS "' + home_recovery_schema.TABLE + '"')
    assert con.execute("SELECT name FROM sqlite_master WHERE tbl_name=? COLLATE NOCASE",
                       (home_recovery_schema.TABLE,)).fetchall() == []
