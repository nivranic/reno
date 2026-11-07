"""Versioned knowledge-source entry points; no admission or execution ticket."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager

from . import home_recovery_source as legacy, home_recovery_dispatch as recovery
from . import source_preservation_witness as witness, storage_admin
from . import storage_backups as backups, storage_lifecycle as lifecycle
from .sync_recovery import _Pins


@contextmanager
def open_source(connection, source_id, *, profile, actual_witness=None):
    """Dispatch the original declaration, keeping its identity pinned.

    This Home17-facing loader always requires the genuine original read-window
    proof before reading declaration bytes. Old declarations keep their old
    parser; actual16 entry points continue using the unchanged legacy loader.
    """
    lifecycle.require_guard(profile)
    storage_admin.from_connection(connection, profile=profile)
    witness.require_read_capture(actual_witness, connection=connection, profile=profile)
    with ExitStack() as stack:
        pins = _Pins(stack)
        raw, declaration_identity = legacy._pin_file(pins, legacy._source_path(profile, source_id),
            legacy.MAX_DECLARATION_BYTES, capture=True)
        declaration = backups._decode(raw)
        if type(declaration) is not dict:
            legacy._fail("SOURCE_INVALID")
        if type(declaration.get("schema_version")) is int and declaration["schema_version"] == 17:
            from . import home_recovery_source17
            source = stack.enter_context(home_recovery_source17.open_source(
                connection, source_id, profile=profile, actual_witness=actual_witness))
        else:
            source = stack.enter_context(legacy.open_source(connection, source_id, profile=profile))
        if source.declaration_sha256 != declaration_identity["sha256"]:
            legacy._fail("SOURCE_CHANGED")
        pins.verify()
        yield source
        source.verify()
        pins.verify()


def create_checkpoint(*, profile, source_id):
    if recovery._actual_version(profile) == 17:
        from . import home_recovery_source17
        return home_recovery_source17.create_checkpoint(profile=profile, source_id=source_id)
    return legacy.create_checkpoint(profile=profile, source_id=source_id)


def read_checkpoint(*, profile, source_id):
    if recovery._actual_version(profile) == 17:
        from . import home_recovery_source17
        return home_recovery_source17.read_checkpoint(profile=profile, source_id=source_id)
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN")
        try:
            with legacy.open_source(con, source_id, profile=profile) as source:
                return source.public()
        finally:
            con.rollback()
