"""Published17 readonly continuity dispatch preserves three exact variants.

The joint image uses ordinary current initialization. Historical v1 starts as
a real released16 empty image, then receives its first additive historical17
declaration. Admission2 comes only from the actual lossless forward upgrader.
No default17 image is downgraded/reinstalled, and no paid/core/receipt fact or
runtime schema/handshake/producer capability is manufactured by these tests.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, migrations, storage_admin, storage_lifecycle as lifecycle
from reno import sync_access, sync_protocol as protocol, sync_queue as queue
from reno import sync_source_continuity as permanent, sync_source_continuity_admin as admin
from reno import sync_source_continuity_service as service
from reno import sync_source_readmit_schema as schema1, sync_source_readmit_schema2 as schema2
from reno import sync_source_readmit_upgrade2 as upgrade
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime, initialize_current17
from tests.schema_fixtures import initialize_released16, released16


VARIANTS = ("v1", "admission2", "admission2_dispose2")


def sql_snapshot(profile):
    """Raw independent rows/DDL/metadata; even a refused damaged DB is readable."""
    with closing(sqlite3.connect(profile.database)) as connection:
        tables = tuple(row[0] for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
        values = {table: tuple(sorted((tuple(row) for row in connection.execute(
            'SELECT * FROM main."' + table.replace('"', '""') + '"')), key=repr)) for table in tables}
        ddl = tuple(tuple(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        return values, ddl


def original_files(profile):
    """Original identity/permanent bytes and exact physical identities, not locks."""
    result = {}
    ignored = {profile.database.name, profile.database.name + "-wal", profile.database.name + "-shm"}
    for root in (profile.database.parent, profile.media, profile.reports, profile.checkpoints):
        for path in root.rglob("*"):
            if path.is_file() and path.name not in ignored and not path.name.endswith(".lock"):
                observed = path.stat()
                raw = path.read_bytes()
                result[str(path)] = (raw, hashlib.sha256(raw).hexdigest(),
                    (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_size, observed.st_mtime_ns))
    return result


def historical_v1(profile, monkeypatch):
    """Declare an actual empty historical17-v1 image before current scope.

    This is the original additive historical schema constructor, not the
    production16->17 migration or a current runtime capability. The old16
    database has never contained any future namespace before this install.
    All existing rows/DDL survive; only four known tables and the explicit
    historical version declaration are added. No owner crosses the scope.
    """
    with released16(monkeypatch):
        initialize_released16(profile.database)
        lifecycle.initialize(profile)
        before_values, before_ddl = sql_snapshot(profile)
        with storage_admin.connection(profile) as connection:
            assert schema1.preflight(connection, require_absent=True) is False
            assert not connection.execute("SELECT * FROM sync_pair").fetchall()
            assert not connection.execute("SELECT * FROM sync_session").fetchall()
            assert not connection.execute("SELECT * FROM sync_queue").fetchall()
            connection.execute("BEGIN IMMEDIATE")
            schema1.install(connection)
            connection.execute("UPDATE main.meta SET value='17' WHERE key='migration_version'")
            connection.commit()
    assert_current17_runtime()
    with closing(db.connect(profile.database)) as connection:
        assert schema2.detect(connection) == "v1"
        assert protocol.schema_handshake(connection)["migration_version"] == 17
        assert all(connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] == 0
            for table in (*schema1.TABLES, "budget_operation", "budget_attempt", "job", "task_request"))
    after_values, after_ddl = sql_snapshot(profile)
    assert {table: after_values.pop(table) for table in schema1.TABLES} == {table: () for table in schema1.TABLES}
    original_meta, historical_meta = dict(before_values.pop("meta")), dict(after_values.pop("meta"))
    assert original_meta.pop("migration_version") == "16" and historical_meta.pop("migration_version") == "17"
    assert historical_meta == original_meta and after_values == before_values
    assert set(before_ddl) <= set(after_ddl)
    assert {item[1] for item in set(after_ddl) - set(before_ddl)} == set(schema1.RESERVED_NAMES)


def upgrade_admission2(profile):
    """Use the original guarded migration owner and actual lossless upgrader."""
    before_values, before_ddl = sql_snapshot(profile)
    before_files = original_files(profile)
    with lifecycle.shared(profile):
        connection = migrations._open(profile.database)
        try:
            # The explicit isolated caller retains a real original SQLite
            # backup before the FK-off migration transaction. This isolated
            # snapshot is never claimed as source/receipt or registered-backup
            # evidence for production maintenance.
            backup = profile.database.parent / "historical-v1-before-admission2.sqlite"
            with closing(sqlite3.connect(backup)) as target:
                connection.backup(target)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            result = upgrade.upgrade_locked(connection, profile=profile, target_variant="admission2")
            assert result == {"from_variant": "v1", "to_variant": "admission2", "changed": True,
                "row_counts": {table: 0 for table in schema1.TABLES}}
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.close()
    after_values, after_ddl = sql_snapshot(profile)
    assert after_values == before_values
    assert all(item in after_ddl for item in before_ddl if item[2] != schema1.ADMISSION)
    assert all(original_files(profile)[path] == value for path, value in before_files.items())
    with closing(sqlite3.connect(backup)) as connection:
        assert schema2.detect(connection) == "v1"
    with closing(db.connect(profile.database)) as connection:
        assert schema2.detect(connection) == "admission2"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture(params=VARIANTS)
def cloud_variant17(tmp_path, monkeypatch, request):
    assert_current17_runtime()
    variant = request.param
    profile = lifecycle.StorageProfile(tmp_path / "cloud/data/reno.db", tmp_path / "cloud/media",
        tmp_path / "cloud/reports", tmp_path / "cloud/checkpoints")
    if variant == "admission2_dispose2":
        initialize_current17(profile.database)
        lifecycle.initialize(profile)
    else:
        historical_v1(profile, monkeypatch)
        if variant == "admission2":
            upgrade_admission2(profile)
    source_profile = lifecycle.StorageProfile(tmp_path / "home/data/reno.db", tmp_path / "home/media",
        tmp_path / "home/reports", tmp_path / "home/checkpoints")
    initialize_current17(source_profile.database)
    lifecycle.initialize(source_profile)
    with storage_admin.connection(source_profile) as connection:
        source = sync_access.identity(connection)
    with storage_admin.connection(profile) as connection:
        assert schema2.detect(connection) == variant
        target = sync_access.identity(connection)
        assert source["handshake"] == target["handshake"]
        queue.bind_peer(connection, source)
        connection.commit()
        assert not connection.execute("SELECT * FROM sync_session").fetchall()
        assert not connection.execute("SELECT * FROM sync_queue").fetchall()
    config.set_local({"node_role": "cloud"})
    value = SimpleNamespace(profile=profile, variant=variant, source=source, target=target)
    yield value
    assert_current17_runtime()


def query(value):
    return {"kind": "source_continuity", "source": deepcopy(value.source),
        "target": {key: value.target[key] for key in ("library_id", "node_id")}}


def activate(value):
    return service.activate(profile=value.profile, continuity_id=str(uuid.uuid4()),
        activated_at=queue._format_time(datetime.now(timezone.utc)))


def test_published17_absent_and_activated_probes_accept_only_the_actual_declared_variant(cloud_variant17):
    value = cloud_variant17
    before_sql, before_files = sql_snapshot(value.profile), original_files(value.profile)
    observed = admin.status(profile=value.profile, request=query(value))
    assert observed["state"] == "absent" and observed["continuity"] is observed["head"] is None
    preview = admin.preview(profile=value.profile)
    assert {key: item for key, item in preview.items() if key != "activation_basis_sha256"} == observed
    assert len(preview["activation_basis_sha256"]) == 64
    assert sql_snapshot(value.profile) == before_sql and original_files(value.profile) == before_files
    actual = activate(value)
    frozen_sql, frozen_files = sql_snapshot(value.profile), original_files(value.profile)
    current = admin.status(profile=value.profile, request=query(value))
    assert current["state"] == "current" and current["continuity"] == {
        "format_version": 1, "continuity_id": actual["continuity_id"],
        "contract_sha256": permanent._digest(actual), "target_epoch": actual["target_epoch"]}
    assert current["head"]["ordinal"] == 0
    assert current["handshake"] == value.source["handshake"]
    assert (current["handshake"]["protocol_version"], current["handshake"]["migration_version"]) == (3, 17)
    assert current["source_identity"] == {key: value.source[key] for key in ("library_id", "node_id")}
    assert current["target_identity"] == query(value)["target"]
    again = admin.preview(profile=value.profile)
    assert {key: item for key, item in again.items() if key != "activation_basis_sha256"} == current
    assert sql_snapshot(value.profile) == frozen_sql and original_files(value.profile) == frozen_files
    with closing(db.connect(value.profile.database)) as connection:
        assert schema2.detect(connection) == value.variant
        assert all(connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] == 0
            for table in (schema1.REGISTRATION, schema1.DISPOSITION, schema1.ADMISSION,
                "budget_operation", "budget_attempt", "job", "task_request"))
    assert not (value.profile.database.parent / "sync-state.json").exists()


def test_exact_private_image_authorizer_keeps_bodies_writes_and_temp_names_closed(cloud_variant17, monkeypatch):
    value = cloud_variant17
    activate(value)
    original = admin._projection
    checked = []

    def observe_projection(connection):
        statements = [*('SELECT "' + column + '" FROM main."' + table + '"'
            for table, column in ((schema1.ACTIVATION, "contract_json"), (schema1.REGISTRATION, "request_json"),
                (schema1.DISPOSITION, "original_request_json"), (schema1.ADMISSION, "result_json"))),
            "SELECT root_job_id FROM main.budget_operation",
            "UPDATE main.meta SET value='99' WHERE key='library_epoch'",
            'CREATE TEMP TABLE "' + schema1.ADMISSION + '"(unknown_original BLOB)']
        for statement in statements:
            with pytest.raises(sqlite3.DatabaseError) as denied:
                connection.execute(statement).fetchall()
            assert denied.value.sqlite_errorcode == sqlite3.SQLITE_AUTH
            checked.append(statement)
        return original(connection)

    monkeypatch.setattr(admin, "_projection", observe_projection)
    before_sql, before_files = sql_snapshot(value.profile), original_files(value.profile)
    result = admin.status(profile=value.profile, request=query(value))
    assert result["state"] == "current" and len(checked) == 7
    assert sql_snapshot(value.profile) == before_sql and original_files(value.profile) == before_files


@pytest.mark.parametrize("damage", ["missing_trigger", "extra_trigger", "extra_column"])
def test_unrecognized_or_damaged_contract_remains_refused_without_repair(cloud_variant17, damage):
    value = cloud_variant17
    activate(value)
    with storage_admin.connection(value.profile) as connection:
        if damage == "missing_trigger":
            connection.execute("DROP TRIGGER sync_source_control_registration_no_delete")
        elif damage == "extra_trigger":
            connection.execute('CREATE TRIGGER foreign_source_contract BEFORE UPDATE ON "' + schema1.ADMISSION +
                '" BEGIN SELECT 1; END')
        else:
            connection.execute('ALTER TABLE "' + schema1.ADMISSION + '" ADD COLUMN unknown_private TEXT')
        connection.commit()
    before_sql, before_files = sql_snapshot(value.profile), original_files(value.profile)
    for operation in (lambda: admin.status(profile=value.profile, request=query(value)),
                      lambda: admin.preview(profile=value.profile)):
        with pytest.raises(SyncError) as refused:
            operation()
        assert refused.value.code == "SYNC_SOURCE_READMIT_SCHEMA_REQUIRED"
    assert sql_snapshot(value.profile) == before_sql and original_files(value.profile) == before_files


def test_exact_historical_dispatch_rejects_a_retained_temporary_shadow(cloud_variant17):
    value = cloud_variant17
    before_sql, before_files = sql_snapshot(value.profile), original_files(value.profile)
    # TEMP is connection-local and not part of the committed-image public
    # probe. Verify the very same exact dispatcher refuses it on that actual
    # connection, while the public image authorizer above denies its creation.
    with closing(db.connect(value.profile.database)) as connection:
        connection.execute('CREATE TEMP TABLE "' + schema1.ADMISSION + '"(unknown_original BLOB)')
        connection.execute('INSERT INTO temp."' + schema1.ADMISSION + '" VALUES(?)', (b"\x00\xfforiginal",))
        connection.commit()
        with pytest.raises(schema1.SourceReadmitSchemaError):
            schema2.validate_historical(connection)
        assert connection.execute('SELECT * FROM temp."' + schema1.ADMISSION + '"').fetchall()[0][0] == b"\x00\xfforiginal"
    assert sql_snapshot(value.profile) == before_sql and original_files(value.profile) == before_files
