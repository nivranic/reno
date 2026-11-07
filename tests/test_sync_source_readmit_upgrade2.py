"""Actual Cloud v1 producers followed by a guarded same17 variant upgrade.

The prospective Cloud fixture supplies isolated schema17/capability scope.
This is not a production global17 activation or a schema16-to17 migration.
Requests are Cloud contract inputs; no Home capture/creation seal is replaced.
Every old row is produced by the original SQL/file service, not inserted here.
"""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import sqlite3

import pytest

from reno import budget_writer_witness as budget_sql, config, migrations, storage_admin
from reno import storage_lifecycle as lifecycle, sync_protocol as protocol
from reno import sync_source_readmission as v1, sync_source_readmit_sql2 as v2
from reno import sync_source_continuity_service as continuity
from reno import sync_source_readmit_schema as old, sync_source_readmit_schema2 as new
from reno import sync_source_readmit_upgrade2 as upgrade
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_readmission as producer
from tests.test_storage_backups import no_real_config


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # Explicit initialization asks only for its role. All other configuration
    # reads remain forbidden, including the repository's local config file.
    def fixture_setting(key, *args):
        if key == "node_role":
            return "cloud"
        raise AssertionError("No real configuration is allowed in upgrade tests")
    monkeypatch.setattr(config, "get", fixture_setting)
    value = cloud_fixture.env.__wrapped__(tmp_path, monkeypatch, request)
    assert migrations.SCHEMA_VERSION == 17  # Isolated prospective fixture only.
    return value


def legacy_admission(value, basis="held"):
    active = cloud_fixture.activated(value)
    body = producer.request(value)
    if basis == "refund":
        body = producer.refund(value, active)
    elif basis == "negative":
        control = producer.old(value)
        registered = producer.register(value, active, control)
        body["dispositions"] = [producer.disposed(value, body, control, registered)]
    result = v1.readmit(profile=value.profile, request=body, now=cloud_fixture.NOW)
    assert result["format_version"] == 1
    assert result["attempt_basis"] == ("original_defer_refund" if basis == "refund" else "held_claim")
    assert result["attempts"] == producer.item(value)["attempts"] == 3
    return body, result


@contextmanager
def migration(profile):
    """The actual caller-owned migration pattern; no runtime SQL gate bypass."""
    with lifecycle.shared(profile):
        connection = migrations._open(profile.database)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.execute("PRAGMA foreign_keys=ON")
            connection.close()


def rows(connection):
    return {table: tuple(tuple(row) for row in connection.execute(
        'SELECT * FROM main."' + table + '" ORDER BY 1')) for table in old.TABLES}


def objects(connection):
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name"))


def enable_prospective_v2(value, monkeypatch):
    # Explicit future peer refresh, separate from the migration helper. It
    # changes neither old source handshakes nor original stored wire bodies.
    monkeypatch.setattr(protocol, "schema_rules", lambda: {**cloud_fixture.rules(),
        "source_readmit_version": 2, "source_dispose_version": 2})
    monkeypatch.setattr(protocol, "SOURCE_READMIT_VERSION", 2, raising=False)
    monkeypatch.setattr(protocol, "SOURCE_DISPOSE_VERSION", 2, raising=False)
    with storage_admin.connection(value.profile) as connection:
        value.source["handshake"] = protocol.schema_handshake(connection)
        connection.execute("UPDATE sync_pair SET schema_fingerprint=? WHERE slot=1",
            (value.source["handshake"]["core_schema_sha256"],))
        connection.commit()


def next_readmit2(value, body, result):
    request = producer.next_request(body, result)
    request.pop("manual_resume_basis")
    request.update(format_version=2, source=deepcopy(value.source), resume_basis=None,
        dispositions=[], defer_receipt=None, resume_receipt=None)
    return request


@pytest.mark.parametrize("basis", ["held", "refund", "negative"])
def test_actual_v1_rows_upgrade_losslessly_and_readmit2_keeps_the_original_previous(env, monkeypatch, basis):
    body, first = legacy_admission(env, basis)
    before = producer.snapshot(env)
    queue_before = producer.item(env)
    with migration(env.profile) as connection:
        raw_before = rows(connection)
        changed = upgrade.upgrade_locked(connection, profile=env.profile)
        assert changed["changed"] is True and changed["from_variant"] == "v1"
        assert changed["to_variant"] == new.detect(connection) == "admission2_dispose2"
        assert changed["row_counts"] == {table: len(values) for table, values in raw_before.items()}
        assert rows(connection) == raw_before
        assert connection.execute("PRAGMA main.foreign_key_check").fetchall() == []
        assert connection.in_transaction
        assert connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
    assert producer.snapshot(env) == before
    assert producer.item(env) == queue_before

    enable_prospective_v2(env, monkeypatch)
    next_body = next_readmit2(env, body, first)
    assert next_body["previous_admission_id"] == first["admission_id"]
    assert next_body["expected_claim"] == first["claim"] != body["expected_claim"]
    assert next_body["original"] == body["original"]
    second = v2.readmit(profile=env.profile, request=next_body, now=cloud_fixture.NOW)
    assert second["format_version"] == 2 and second["attempt_basis"] == "held_claim"
    assert second["attempts"] == first["attempts"] == producer.item(env)["attempts"]
    assert second["previous_admission_id"] == first["admission_id"]
    assert second["core_sha256"] == first["core_sha256"]
    assert second["claim"]["lease_generation"] == first["claim"]["lease_generation"] + 1
    with storage_admin.connection(env.profile) as connection:
        current = rows(connection)
        for table, values in raw_before.items():
            actual = {row[0]: row for row in current[table]}
            assert all(actual.get(row[0]) == row for row in values)
    replay_before = producer.snapshot(env)
    assert v2.readmit(profile=env.profile, request=next_body, now=cloud_fixture.NOW) == {**second, "replayed": True}
    assert producer.snapshot(env) == replay_before


def test_admission_only_then_joint_upgrade_and_noop_keep_every_old_row(env):
    legacy_admission(env)
    before = producer.snapshot(env)
    with migration(env.profile) as connection:
        connection.execute("CREATE TEMP TABLE caller_notes(value TEXT)")
        connection.execute("INSERT INTO caller_notes VALUES('kept')")
        temporary = tuple(tuple(row) for row in connection.execute("SELECT * FROM sqlite_temp_master ORDER BY name"))
        first = upgrade.upgrade_locked(connection, profile=env.profile, target_variant="admission2")
        assert first["changed"] is True and new.detect(connection) == "admission2"
        second = upgrade.upgrade_locked(connection, profile=env.profile)
        assert second["changed"] is True and new.detect(connection) == "admission2_dispose2"
        raw, ddl = rows(connection), objects(connection)
        replay = upgrade.upgrade_locked(connection, profile=env.profile)
        assert replay["changed"] is False and replay["from_variant"] == replay["to_variant"]
        assert rows(connection) == raw and objects(connection) == ddl
        with pytest.raises(upgrade.SourceReadmitUpgradeError, match="DOWNGRADE_FORBIDDEN"):
            upgrade.upgrade_locked(connection, profile=env.profile, target_variant="admission2")
        assert rows(connection) == raw and objects(connection) == ddl
        assert tuple(tuple(row) for row in connection.execute("SELECT * FROM sqlite_temp_master ORDER BY name")) == temporary
        assert [tuple(row) for row in connection.execute("SELECT value FROM caller_notes")] == [("kept",)]
    assert producer.snapshot(env) == before


def test_actual_legacy_positive_dispositions_keep_nullable_registration_and_original_resume_parent(env):
    cloud_fixture.activated(env)
    for original in (env.legacy, env.legacy_resume):
        body = cloud_fixture.dispose_request(env, original)
        result = continuity.dispose(profile=env.profile, request=body, now=cloud_fixture.NOW)
        assert result["disposition"] == "applied"
        assert result["continuity_id"] is result["registration_sha256"] is None
    before = producer.snapshot(env)
    with migration(env.profile) as connection:
        raw = rows(connection)
        columns = old.COLUMNS[old.DISPOSITION]
        stored = {row[columns.index("original_request_id")]: dict(zip(columns, row))
            for row in raw[old.DISPOSITION]}
        assert stored[env.legacy["request_id"]]["defer_receipt_json"] is None
        assert stored[env.legacy_resume["request_id"]]["defer_receipt_json"] is not None
        assert stored[env.legacy_resume["request_id"]]["defer_receipt_sha256"] is not None
        upgrade.upgrade_locked(connection, profile=env.profile)
        assert rows(connection) == raw
        assert connection.execute("PRAGMA main.foreign_key_check").fetchall() == []
    assert producer.snapshot(env) == before


def test_actual_two_generation_v1_chain_keeps_self_fk_and_readmit2_uses_its_latest_parent(env, monkeypatch):
    body, first = legacy_admission(env)
    second_body = producer.next_request(body, first)
    second = v1.readmit(profile=env.profile, request=second_body, now=cloud_fixture.NOW)
    assert second["previous_admission_id"] == first["admission_id"]
    assert second["attempts"] == first["attempts"]
    before = producer.snapshot(env)
    with migration(env.profile) as connection:
        raw = rows(connection)
        upgrade.upgrade_locked(connection, profile=env.profile)
        assert rows(connection) == raw
        refs = [tuple(row) for row in connection.execute(f"PRAGMA main.foreign_key_list({old.ADMISSION})")]
        assert any(row[2:5] == (old.ADMISSION, "previous_admission_id", "admission_id") for row in refs)
        assert connection.execute("PRAGMA main.foreign_key_check").fetchall() == []
    assert producer.snapshot(env) == before
    enable_prospective_v2(env, monkeypatch)
    third_body = next_readmit2(env, second_body, second)
    third = v2.readmit(profile=env.profile, request=third_body, now=cloud_fixture.NOW)
    assert third["previous_admission_id"] == second["admission_id"]
    assert third["attempts"] == first["attempts"] == second["attempts"]
    with storage_admin.connection(env.profile) as connection:
        actual = {row[0]: row for row in rows(connection)[old.ADMISSION]}
        assert all(actual.get(row[0]) == row for row in raw[old.ADMISSION])


def test_success_does_not_commit_the_callers_outer_migration(env):
    legacy_admission(env)
    before = producer.snapshot(env)
    with migration(env.profile) as connection:
        ddl = objects(connection)
        upgrade.upgrade_locked(connection, profile=env.profile)
        assert connection.in_transaction and new.detect(connection) == "admission2_dispose2"
        connection.rollback()
        assert new.detect(connection) == "v1" and objects(connection) == ddl
    assert producer.snapshot(env) == before


@pytest.mark.parametrize("denial", ["drop", "index"])
def test_actual_sql_failure_rolls_back_all_rebuilds_and_retry_uses_the_same_old_rows(env, denial):
    legacy_admission(env, "negative")
    with migration(env.profile) as connection:
        raw, ddl = rows(connection), objects(connection)
        action, name = ((sqlite3.SQLITE_DROP_TABLE, old.ADMISSION) if denial == "drop" else
            (sqlite3.SQLITE_CREATE_INDEX, "idx_source_admission_core"))
        connection.set_authorizer(lambda operation, first, *_:
            sqlite3.SQLITE_DENY if operation == action and first == name else sqlite3.SQLITE_OK)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                upgrade.upgrade_locked(connection, profile=env.profile)
        finally:
            connection.set_authorizer(None)
        assert connection.in_transaction and new.detect(connection) == "v1"
        assert rows(connection) == raw and objects(connection) == ddl
        assert upgrade.upgrade_locked(connection, profile=env.profile)["changed"] is True
        assert rows(connection) == raw


def test_cancelled_rebuild_preserves_original_exception_and_prior_caller_write(env, monkeypatch):
    legacy_admission(env)
    class Cancelled(BaseException):
        pass
    marker = Cancelled("isolated migration cancellation")
    rebuild = upgrade._rebuild
    def cancelled(*args):
        rebuild(*args)
        raise marker
    with migration(env.profile) as connection:
        connection.execute("INSERT INTO meta(key,value) VALUES('upgrade_caller_probe','kept')")
        raw, ddl = rows(connection), objects(connection)
        with monkeypatch.context() as scoped:
            scoped.setattr(upgrade, "_rebuild", cancelled)
            with pytest.raises(Cancelled) as caught:
                upgrade.upgrade_locked(connection, profile=env.profile)
        assert caught.value is marker
        assert connection.in_transaction and new.detect(connection) == "v1"
        assert rows(connection) == raw and objects(connection) == ddl
        assert connection.execute("SELECT value FROM meta WHERE key='upgrade_caller_probe'").fetchone()[0] == "kept"


@pytest.mark.parametrize("damage", ["extra_column", "extra_trigger", "temp_shadow", "staging_view", "attached_temp_trigger", "staging_reference"])
def test_unknown_objects_are_rejected_without_dropping_or_repairing_them(env, damage):
    legacy_admission(env)
    with migration(env.profile) as connection:
        if damage == "extra_column":
            connection.execute(f"ALTER TABLE {old.ADMISSION} ADD COLUMN unknown_private_column TEXT")
        elif damage == "extra_trigger":
            connection.execute(f"CREATE TRIGGER unknown_old_source BEFORE INSERT ON {old.ADMISSION} BEGIN SELECT 1; END")
        elif damage == "temp_shadow":
            connection.execute(f"CREATE TEMP TABLE {old.ADMISSION}(private_value TEXT)")
        elif damage == "attached_temp_trigger":
            connection.execute(f"CREATE TEMP TRIGGER unknown_source_temp BEFORE INSERT ON main.{old.ADMISSION} BEGIN SELECT 1; END")
        elif damage == "staging_reference":
            connection.execute("CREATE TEMP VIEW unknown_source_tempview AS SELECT * FROM main._reno_source_admission_upgrade2")
        else:
            connection.execute("CREATE TEMP VIEW _RENO_SOURCE_ADMISSION_UPGRADE2 AS SELECT 'owned' AS private_value")
        raw, ddl = rows(connection), objects(connection)
        temporary = tuple(tuple(row) for row in connection.execute("SELECT * FROM sqlite_temp_master ORDER BY name"))
        with pytest.raises(sqlite3.DatabaseError):
            upgrade.upgrade_locked(connection, profile=env.profile)
        assert rows(connection) == raw and objects(connection) == ddl
        assert tuple(tuple(row) for row in connection.execute("SELECT * FROM sqlite_temp_master ORDER BY name")) == temporary


def test_orphaned_original_fk_prevents_any_schema_change(env):
    legacy_admission(env)
    with migration(env.profile) as connection:
        connection.execute("DELETE FROM sync_queue WHERE item_id=7")
        assert connection.execute("PRAGMA main.foreign_key_check").fetchone() is not None
        raw, ddl = rows(connection), objects(connection)
        with pytest.raises(upgrade.SourceReadmitUpgradeError, match="FOREIGN_KEY_INVALID"):
            upgrade.upgrade_locked(connection, profile=env.profile)
        assert rows(connection) == raw and objects(connection) == ddl


def test_changed_original_json_digest_is_rejected_without_rewriting_the_legacy_row(env):
    _, admission = legacy_admission(env)
    with migration(env.profile) as connection:
        trigger = old.ADMISSION + "_no_update"
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(f"UPDATE {old.ADMISSION} SET result_sha256=? WHERE admission_id=?",
            ("0" * 64, admission["admission_id"]))
        connection.execute(next(statement for statement in old.TRIGGERS
            if statement.startswith("CREATE TRIGGER " + trigger + " ")))
        assert new.detect(connection) == "v1"
        raw, ddl = rows(connection), objects(connection)
        with pytest.raises(upgrade.SourceReadmitUpgradeError, match="RECORD_INVALID"):
            upgrade.upgrade_locked(connection, profile=env.profile)
        assert rows(connection) == raw and objects(connection) == ddl


@pytest.mark.parametrize("window", ["no_guard", "no_transaction", "fk_on", "wrong_profile", "metadata16"])
def test_real_profile_guard_and_private_migration_transaction_are_required(env, window):
    connection = migrations._open(env.profile.database)
    try:
        connection.execute("PRAGMA foreign_keys=" + ("ON" if window == "fk_on" else "OFF"))
        if window != "no_transaction":
            connection.execute("BEGIN IMMEDIATE")
        protected = lifecycle.shared(env.profile) if window != "no_guard" else nullcontext()
        with protected:
            profile = env.profile
            if window == "wrong_profile":
                profile = lifecycle.StorageProfile(env.profile.database.with_name("different.db"),
                    env.profile.media, env.profile.reports, env.profile.checkpoints)
            if window == "metadata16":
                connection.execute("UPDATE meta SET value='16' WHERE key='migration_version'")
            raw, ddl = rows(connection), objects(connection)
            with pytest.raises((upgrade.SourceReadmitUpgradeError, lifecycle.StorageLifecycleError)):
                upgrade.upgrade_locked(connection, profile=profile)
            assert rows(connection) == raw and objects(connection) == ddl
    finally:
        connection.rollback()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def test_fake_connection_and_budget_writer_cannot_start_an_upgrade(env):
    with pytest.raises(upgrade.SourceReadmitUpgradeError, match="CONNECTION_REQUIRED"):
        upgrade.upgrade_locked(object(), profile=env.profile)
    with budget_sql.connection(env.profile, write=True) as connection:
        assert budget_sql.in_write_window()
        with pytest.raises(upgrade.SourceReadmitUpgradeError, match="MIGRATION_WINDOW_REQUIRED"):
            upgrade.upgrade_locked(connection, profile=env.profile)
