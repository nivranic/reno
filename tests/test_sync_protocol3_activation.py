"""Released schema17/protocol3 must work through ordinary production factories.

There is no prospective fixture, altered migration version, substituted
handshake or provider in these checks. They are scheduled at actual release.
"""
from contextlib import closing
import sqlite3

import pytest

from reno import db, migrations, storage_lifecycle as lifecycle, sync_protocol as protocol
from reno import sync_checkpoints, sync_source_continuity_service as continuity
from reno import sync_source_readmit_schema2 as schema, sync_source_readmit_sql2 as sql2


def test_production_constants_and_closed_rules_publish_all_three_implemented_capabilities():
    assert migrations.SCHEMA_VERSION == 17 and protocol.PROTOCOL_VERSION == 3
    for symbol, key, version in (
        ("SOURCE_CONTROL_CONTINUITY_VERSION", "source_control_continuity_version", 1),
        ("SOURCE_READMIT_VERSION", "source_readmit_version", 2),
        ("SOURCE_DISPOSE_VERSION", "source_dispose_version", 2),
    ):
        assert type(getattr(protocol, symbol)) is int and getattr(protocol, symbol) == version
        assert protocol.schema_rules()[key] == version
    continuity._runtime(capability=True)
    sql2._runtime()
    sql2._dispose_runtime()


def test_new_database_uses_actual_current_joint17_without_creating_source_or_paid_authority(tmp_path):
    path = tmp_path / "data" / "reno.db"
    result = db.initialize(path)
    assert result.version == 17 and result.changed
    profile = lifecycle.StorageProfile(path, tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    with closing(db.connect(path)) as con:
        assert schema.detect(con) == "admission2_dispose2"
        migrations.validate(con)
        handshake = protocol.schema_handshake(con)
        assert set(handshake) == {"protocol_version", "migration_version", "core_schema_sha256"}
        assert handshake["protocol_version"] == 3 and handshake["migration_version"] == 17
        for table in (*schema.COLUMNS, "budget_operation", "budget_attempt", "task_request"):
            assert con.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not (path.parent / "sync-state.json").exists()
    assert sync_checkpoints.ARCHIVE_SCHEMA_VERSION == 9
    assert set(sync_checkpoints.SUPPORTED_SCHEMA_VERSIONS) == {7, 9}


def test_original_protocol2_peer_needs_explicit_refresh_without_rewriting_existing_state(tmp_path):
    path = tmp_path / "current.db"
    db.initialize(path)
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        current = protocol.schema_handshake(con)
        metadata = [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")]
        prior = {**current, "protocol_version": 2, "migration_version": 16}
        with pytest.raises(protocol.SyncError) as error:
            protocol.validate_handshake(prior, current)
        assert error.value.code == "INCOMPATIBLE_SYNC_SCHEMA"
        assert [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")] == metadata
        assert protocol.schema_handshake(con) == current
