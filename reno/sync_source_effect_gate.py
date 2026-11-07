"""Require the sealed original-control frame for prospective17 writers.

The old schema16 protocol is unchanged. Checking both the running code and the
actual SQL version prevents a raw old-code connection from bypassing the new
write-before facility. A frame grants only its exact original body.
"""
from . import migrations
from .sync_protocol import SyncError


def require_if_enabled(con, original_request):
    row = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()
    value = row[0] if row is not None else None
    if (type(value) is not str or len(value) > 19 or not value.isascii() or not value.isdigit()
            or value != str(int(value)) or int(value) < 1):
        raise SyncError("Source control schema could not be verified", "SYNC_SOURCE_READMIT_SCHEMA_REQUIRED", 409)
    if migrations.SCHEMA_VERSION >= 17 or int(value) >= 17:
        from .sync_source_continuity_service import require_effect
        return require_effect(con, original_request)
    return None
