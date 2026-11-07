"""Ordinary migrate-db schema17 transition in isolated maintenance profiles.

Historical16 and Cloud17-v1 producers own explicit complete version scopes.
The destination uses the actual published schema17/protocol3 runtime. No
alternate migration entry point, State DTO, fake writer permission, live
service or provider is used. Released16 origin and paid facts stay original;
same17 CHECK variants use their guarded/lossless migration implementation.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from reno import cli, db, home_recovery as home16, home_recovery17 as home17
from reno import migrations, storage_lifecycle as lifecycle, sync_protocol as protocol
from reno import sync_source_authority as authority
from reno import sync_source_readmit_schema as readmit, sync_source_readmit_schema2 as readmit2
from reno import sync_source_readmit_upgrade2 as upgrade
from tests import test_home_recovery_home as released_home
from tests import test_home_recovery17 as existing_home17
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_readmit_upgrade2 as cloud_upgrade
from tests.migration_helpers import migration_arguments, migration_profile
from tests.schema_fixtures import released16 as released16_scope, assert_current17_runtime


def snapshot(path):
    """All stored SQLite values and exact noninternal DDL, without mutation."""
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        values = {table: tuple(sorted((tuple(row) for row in connection.execute(
            'SELECT * FROM main."' + table.replace('"', '""') + '"' +
            (" WHERE key!='migration_version'" if table == "meta" else ""))), key=repr))
            for table in tables}
        ddl = {row[1]: tuple(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        version = connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0]
        return values, ddl, version


def original_files(profile):
    """Pin originals, excluding mutable SQL/locks and maintenance catalogs."""
    result = {}
    for root in (profile.database.parent, profile.media, profile.reports, profile.checkpoints):
        for path in root.rglob("*"):
            if (not path.is_file() or path.name in {profile.database.name,
                    profile.database.name + "-wal", profile.database.name + "-shm"}
                    or path.name.endswith(".lock")
                    or ".reno-storage-backups-v1" in path.parts):
                continue
            result[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    return result


def assert_original_files(values):
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == frozen
        for path, frozen in values.items())


@pytest.fixture
def released16(tmp_path, monkeypatch):
    # The actual old pipeline/State fixture creates genuine released16 SQL,
    # original paid reservations and the closed historic origin files first.
    with released16_scope(monkeypatch):
        value = existing_home17.complete_home16(released_home.build_home16(tmp_path))
        with closing(sqlite3.connect(value.profile.database)) as connection:
            assert connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
            assert not connection.execute("SELECT name FROM main.sqlite_master WHERE lower(name) IN (" +
                ",".join("?" for _ in readmit.RESERVED_NAMES) + ")", readmit.RESERVED_NAMES).fetchall()
            assert home16._actual_schema(connection)
    assert_current17_runtime()
    return value


@pytest.fixture
def legacy17(tmp_path, monkeypatch, request):
    # Establish real old16 receipts before the original additive17 Cloud
    # fixture. Its known v1 producers then persist real immutable records.
    with released16_scope(monkeypatch):
        # This factory first creates released16 effects, then its explicit
        # historical17-v1 DDL/rules. Close those temporary17 globals before
        # leaving the original16 producer scope; only original files/rows and
        # their already generated handshakes survive into current production.
        with monkeypatch.context() as historical17:
            value = cloud_fixture.env.__wrapped__(tmp_path, historical17, request)
            cloud_upgrade.legacy_admission(value, "negative")
    assert_current17_runtime()
    return value


def test_current16_initialization_and_explicit_reference_do_not_gain_future_tables(tmp_path, monkeypatch):
    path = tmp_path / "released.db"
    with released16_scope(monkeypatch):
        assert db.initialize(path).version == 16
        with closing(db.connect(path)) as connection:
            assert not set(readmit.TABLES) & migrations._tables(connection)
        # A cached16 reference must not mask the current17 column requirements.
        assert not set(readmit.TABLES) & set(migrations._contract(None))
    assert_current17_runtime()
    assert db.initialize(tmp_path / "current.db").version == 17
    with closing(db.connect(tmp_path / "current.db")) as connection:
        assert readmit2.detect(connection) == "admission2_dispose2"
        assert all(connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] == 0
            for table in readmit.TABLES)
    assert set(readmit.TABLES) <= set(migrations._contract("admission2_dispose2"))
    assert not set(readmit.TABLES) & set(migrations._contract(None))


@pytest.mark.parametrize("variant", [None, "v1", "admission2", "admission2_dispose2"])
def test_explicit_reference_keeps_each_exact_declared_variant(variant):
    assert_current17_runtime()
    with closing(sqlite3.connect(":memory:")) as connection:
        migrations._create_schema(connection, readmit_variant=variant)
        if variant is None:
            assert not set(readmit.TABLES) & migrations._tables(connection)
        else:
            assert readmit2.detect(connection) == variant


@pytest.mark.parametrize("variant", [True, 1, "unknown", "dispose2", []])
def test_unknown_reference_variant_fails_before_any_ddl(variant):
    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(migrations.SchemaVersionError, match="schema variant"):
            migrations._create_schema(connection, readmit_variant=variant)
        assert connection.execute("SELECT * FROM sqlite_master").fetchall() == []


def test_released16_original_origin_paid_personal_and_worker_facts_survive_real_migration(released16, monkeypatch):
    value = released16
    before_rows, before_ddl, before_version = snapshot(value.profile.database)
    before_state = value.store.read()
    files = original_files(value.profile)
    assert before_version == "16" and value.origin["record_kind"] == "origin"
    assert before_rows["budget_attempt"] and before_rows["renovation_project"]
    before_sha = authority.digest(value.origin)
    original_install = readmit2.install_dispose2

    def checked_install(connection):
        actual_database = next(row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main")
        if actual_database:
            assert Path(actual_database) == value.profile.database
            assert connection.in_transaction and connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
            lifecycle.require_guard(value.profile)
            assert connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
        original_install(connection)

    monkeypatch.setattr(readmit2, "install_dispose2", checked_install)
    result = db.migrate(value.profile.database, profile=value.profile)
    assert result.changed and result.version == 17 and result.backup_path.is_file()
    after_rows, after_ddl, after_version = snapshot(value.profile.database)
    assert after_version == "17"
    assert {table: after_rows.pop(table) for table in readmit.TABLES} == {table: () for table in readmit.TABLES}
    assert after_rows == before_rows
    assert {name: after_ddl[name] for name in before_ddl} == before_ddl
    assert set(after_ddl) - set(before_ddl) == set(readmit.RESERVED_NAMES)
    assert snapshot(result.backup_path) == (before_rows, before_ddl, "16")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (16, 17)
    assert manifest["row_counts"] == {table: len(rows) + (1 if table == "meta" else 0)
        for table, rows in before_rows.items()}
    assert value.store.read() == before_state
    assert authority.digest(value.origin) == before_sha
    assert_original_files(files)
    with closing(db.connect(value.profile.database)) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert readmit2.detect(connection) == "admission2_dispose2"
        assert home17._actual_schema(connection)


def test_ordinary_migrate_db_cli_reaches_same_guarded_transition(tmp_path, monkeypatch, capsys):
    path = tmp_path / "released.db"
    with released16_scope(monkeypatch):
        db.initialize(path)
        profile = migration_profile(path)
        lifecycle.initialize(profile)
        before = snapshot(path)
    assert_current17_runtime()
    assert cli.main(["migrate-db", *migration_arguments(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] and result["schema_version"] == 17 and result["backup_path"]
    assert snapshot(Path(result["backup_path"])) == before
    with closing(db.connect(path)) as connection:
        assert readmit2.detect(connection) == "admission2_dispose2"


def minimal_older(tmp_path, version):
    path = tmp_path / "original.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE job(job_id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO meta VALUES('migration_version',?)", (str(version),))
    profile = lifecycle.StorageProfile(path, tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    return profile


@pytest.mark.parametrize("version", [0, 16])
@pytest.mark.parametrize("reserved", readmit.RESERVED_NAMES)
@pytest.mark.parametrize("uppercase", [False, True])
@pytest.mark.parametrize("temporary", [False, True])
def test_older_reserved_names_are_refused_in_main_or_temp_before_backup(
        tmp_path, monkeypatch, version, reserved, uppercase, temporary):
    profile = minimal_older(tmp_path, version)
    connection = sqlite3.connect(profile.database)
    connection.row_factory = sqlite3.Row
    name = reserved.upper() if uppercase else reserved
    connection.execute('CREATE ' + ("TEMP " if temporary else "") + 'TABLE "' + name + '"(original BLOB)')
    connection.execute('INSERT INTO "' + name + '" VALUES(?)', (b"\x00\xfforiginal-unknown",))
    connection.commit()
    before = snapshot(profile.database)
    assert_current17_runtime()
    monkeypatch.setattr(migrations, "_open", lambda path: connection)
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("Reserved namespace reached backup"))
    with pytest.raises(migrations.SchemaVersionError, match="Schema17 source readmission name collision"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before
    assert not list(tmp_path.glob("*.bak-migrate-*"))


def test_same_shape_future_namespace_is_not_adopted_as_released16(released16, monkeypatch):
    with sqlite3.connect(released16.profile.database) as connection:
        readmit2.install_dispose2(connection)
    before = snapshot(released16.profile.database)
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("Unknown same-shape DDL reached backup"))
    with pytest.raises(migrations.SchemaVersionError, match="Schema17 source readmission name collision"):
        db.migrate(released16.profile.database, profile=released16.profile)
    assert snapshot(released16.profile.database) == before


def test_partial_install_failure_rolls_back_joint_ddl_metadata_and_original_facts(released16, monkeypatch):
    before = snapshot(released16.profile.database)
    files = original_files(released16.profile)
    state = released16.store.read()
    original = readmit2.install_dispose2

    def interrupted(connection):
        original(connection)
        assert readmit2.detect(connection) == "admission2_dispose2"
        raise sqlite3.DatabaseError("synthetic schema17 interruption")

    monkeypatch.setattr(readmit2, "install_dispose2", interrupted)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic schema17"):
        db.migrate(released16.profile.database, profile=released16.profile)
    assert snapshot(released16.profile.database) == before
    assert released16.store.read() == state
    assert_original_files(files)
    backups = list(released16.profile.database.parent.glob("reno.db.bak-migrate-*"))
    images = [path for path in backups if not path.name.endswith(".json")]
    assert len(images) == 1 and snapshot(images[0]) == before


@pytest.mark.parametrize("variant", ["v1", "admission2", "admission2_dispose2"])
def test_existing17_variants_use_real_upgrade_then_exact_replay_preserving_original_rows(legacy17, monkeypatch, variant):
    if variant != "v1":
        with cloud_upgrade.migration(legacy17.profile) as connection:
            upgrade.upgrade_locked(connection, profile=legacy17.profile, target_variant=variant)
    before_rows, before_ddl, before_version = snapshot(legacy17.profile.database)
    files = original_files(legacy17.profile)
    original = upgrade.upgrade_locked
    observed = []

    def checked_upgrade(connection, **values):
        assert connection.in_transaction and connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        lifecycle.require_guard(legacy17.profile)
        result = original(connection, **values)
        observed.append(result)
        return result

    monkeypatch.setattr(upgrade, "upgrade_locked", checked_upgrade)
    result = db.migrate(legacy17.profile.database, profile=legacy17.profile)
    assert result.changed is (variant != "admission2_dispose2")
    assert observed[-1]["from_variant"] == variant and observed[-1]["to_variant"] == "admission2_dispose2"
    after_rows, after_ddl, after_version = snapshot(legacy17.profile.database)
    assert after_rows == before_rows and before_version == after_version == "17"
    assert set(after_ddl) == set(before_ddl)
    assert_original_files(files)
    if result.changed:
        assert snapshot(result.backup_path) == (before_rows, before_ddl, "17")
    else:
        assert result.backup_path is result.manifest_path is None
    with closing(db.connect(legacy17.profile.database)) as connection:
        assert readmit2.detect(connection) == "admission2_dispose2"
        assert home17._actual_schema(connection)
    # Repeating ordinary migrate-db delegates the exact record/FK replay;
    # it does not produce an additional maintenance backup or rewrite rows.
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("Exact replay reached backup"))
    replay = db.migrate(legacy17.profile.database, profile=legacy17.profile)
    assert not replay.changed and observed[-1]["changed"] is False
    assert snapshot(legacy17.profile.database) == (after_rows, after_ddl, "17")
    assert_original_files(files)


def test_same17_rebuild_failure_rolls_back_all_original_request_json_and_uuid(legacy17, monkeypatch):
    before = snapshot(legacy17.profile.database)
    files = original_files(legacy17.profile)
    original = upgrade._rebuild

    def interrupted(connection, rebuilt, declarations):
        original(connection, rebuilt, declarations)
        raise sqlite3.DatabaseError("synthetic same17 interruption")

    monkeypatch.setattr(upgrade, "_rebuild", interrupted)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic same17"):
        db.migrate(legacy17.profile.database, profile=legacy17.profile)
    assert snapshot(legacy17.profile.database) == before
    assert_original_files(files)


@pytest.mark.parametrize("damage", ["missing-trigger", "extra-trigger", "temp-shadow", "widened-format"])
def test_unknown17_contract_is_rejected_before_backup_or_ddl_repair(legacy17, monkeypatch, damage):
    connection = sqlite3.connect(legacy17.profile.database)
    connection.row_factory = sqlite3.Row
    if damage == "missing-trigger":
        trigger = connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (readmit.ADMISSION,)).fetchone()[0]
        connection.execute('DROP TRIGGER "' + trigger + '"')
    elif damage == "extra-trigger":
        connection.execute('CREATE TRIGGER unknown17 BEFORE INSERT ON "' + readmit.ADMISSION + '" BEGIN SELECT 1; END')
    elif damage == "temp-shadow":
        connection.execute('CREATE TEMP TABLE "' + readmit.ADMISSION.upper() + '"(original BLOB)')
    else:
        connection.execute("PRAGMA writable_schema=ON")
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (readmit.ADMISSION,)).fetchone()[0]
        assert row.count(readmit._integer_sql("format_version", maximum=1)) == 1
        connection.execute("UPDATE sqlite_master SET sql=? WHERE name=?", (
            row.replace(readmit._integer_sql("format_version", maximum=1),
                readmit._integer_sql("format_version", maximum=3), 1), readmit.ADMISSION))
        connection.execute("PRAGMA writable_schema=OFF")
    connection.commit()
    before = snapshot(legacy17.profile.database)
    monkeypatch.setattr(migrations, "_open", lambda path: connection)
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("Unknown17 contract reached backup"))
    with pytest.raises(migrations.SchemaVersionError, match="Schema17 source readmission contract is invalid"):
        db.migrate(legacy17.profile.database, profile=legacy17.profile)
    assert snapshot(legacy17.profile.database) == before
