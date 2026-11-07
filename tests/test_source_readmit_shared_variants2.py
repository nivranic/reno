"""Actual prospective17 v2 producers consumed by shared public readers.

All libraries are isolated. The existing fixture opts into the future schema
and peer rules; this does not activate production global17. No format2 row is
inserted by a test, and no reader is allowed to upgrade or repair its source.
"""
import hashlib
import json
import sqlite3

import pytest

from reno import config, storage_admin, storage_artifacts as artifacts
from reno import storage_lifecycle as lifecycle, storage_readonly as readonly
from reno import storage_root_inputs as inputs, storage_source_continuity_roots as roots
from reno import sync_source_readmit_contract as wire, sync_source_readmit_schema as old_schema
from reno import sync_source_readmit_schema2 as schema, sync_source_readmit_sql2 as sql2
from reno import sync_source_readmit_upgrade2 as upgrade
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_dispose_sql2 as joint_fixture
from tests import test_sync_source_readmit_upgrade2 as upgrade_fixture
from tests.test_sync_source_readmission import old, register


@pytest.fixture(autouse=True)
def no_repository_configuration(monkeypatch):
    def setting(key, *_args):
        if key == "node_role":
            return "cloud"
        raise AssertionError("Shared variant tests cannot read real configuration")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Shared variant tests cannot locate real configuration")

    monkeypatch.setattr(config, "get", setting)
    monkeypatch.setattr(config, "local_path", forbidden)


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # No indirect parameters: this is the actual joint-v2 setup and producer.
    return joint_fixture.env.__wrapped__(tmp_path, monkeypatch, request)


@pytest.fixture
def joint(env):
    original = old(env)
    registered = register(env, env.active, original)
    disposition = sql2.dispose(profile=env.profile,
        request=joint_fixture.body(env, original, registered), now=cloud_fixture.NOW)
    admission = sql2.readmit(profile=env.profile,
        request=joint_fixture.readmit2(env, disposition), now=cloud_fixture.NOW)
    assert disposition["format_version"] == admission["format_version"] == 2
    with storage_admin.connection(env.profile) as connection:
        assert schema.detect(connection) == "admission2_dispose2"
        projection = public_rows(connection)
    return env, projection, disposition, admission


def caller_policy(events):
    """Supplement the caller's policy, without granting any private body READ."""
    def authorize(action, first, second, database, origin):
        events.append((action, first, second, database, origin))
        if origin is not None:
            return sqlite3.SQLITE_DENY
        continuity = inputs.continuity_authorize(action, first, second, database, origin)
        if continuity is not None:
            return continuity
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if (action == sqlite3.SQLITE_READ and database == "main"
                and first == "caller_notes" and second == "value"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def public_rows(connection):
    # Direct SQL reads establish the actual saved public values independently
    # of the reader/normalizer under test. Private *_json cells are never read.
    return {table: [dict(zip(columns, row)) for row in connection.execute(
        'SELECT ' + ','.join('"' + column + '"' for column in columns)
        + ' FROM main."' + table + '" ORDER BY "' + columns[0] + '"')]
        for table, columns in roots.SQL_COLUMNS.items()}


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def public_only(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert not any(marker in encoded for marker in (
        '"request_json"', '"result_json"', '"core_json"', '"lease_token"',
        '"original_request"', '"original_receipt"', "https://", "http://"))


def image_options(profile):
    roles = {"sync_media": profile.media, "local_report": profile.reports,
             "sync_report": profile.reports, "checkpoint": profile.checkpoints}
    return {"root_ids": {name: artifacts.root_identity(root, profile=profile)["root_id"]
                         for name, root in roles.items()}, "member_id": "mem_variant2"}


def ddl_digest(connection):
    return digest({database: [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM " + master + " ORDER BY type,name")]
        for database, master in (("main", "main.sqlite_master"), ("temp", "sqlite_temp_master"))})


def source_signature(profile):
    return {suffix: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for suffix in ("", "-wal", "-journal")
        if (path := profile.database.with_name(profile.database.name + suffix)).exists()}


def test_actual_joint_v2_public_projection_keeps_saved_versions_and_record_digests(joint):
    value, expected, disposition, admission = joint
    before = source_signature(value.profile)
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        events = []
        image.connection.set_authorizer(caller_policy(events))
        projected = inputs.read_continuity_projection(image.connection)
        normalized = roots.normalize_sql(projected)
        assert normalized == projected == expected
        assert [row["format_version"] for row in projected[schema.ACTIVATION]] == [1]
        assert [row["format_version"] for row in projected[schema.REGISTRATION]] == [1]
        for table, result in ((schema.DISPOSITION, disposition), (schema.ADMISSION, admission)):
            assert [row["format_version"] for row in projected[table]] == [2]
            assert projected[table][0]["result_sha256"] == wire.digest(result)
        assert events
        public_only(projected)
        image.verify_source()
    assert source_signature(value.profile) == before


def test_actual_committed_image_and_continuity_roots_keep_format2_public_row_hashes(joint):
    value, expected, _disposition, _admission = joint
    before = source_signature(value.profile)
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        scan = inputs.parse_image(image.connection, profile=value.profile, **image_options(value.profile))
        bindings = [binding for binding in scan.source_bindings
                    if binding.section == "source_continuity"]
        assert len(bindings) == 1 and bindings[0].state == "present"
        public = [{"table": table, **row} for table in sorted(expected) for row in expected[table]]
        assert bindings[0].row_count == len(public)
        assert bindings[0].sha256 == digest(public)
        with roots.capture_locked(value.profile, sql_projection=expected) as captured:
            assert captured.summary["presence"] == "present" and not captured.unknown
            for table in (schema.ADMISSION, schema.DISPOSITION):
                row = expected[table][0]
                refs = [reference for reference in captured.references
                        if reference["record_kind"] == table and reference["record_id"] == row[roots.SQL_COLUMNS[table][0]]]
                assert len(refs) == 1 and refs[0]["projection_sha256"] == digest(row)
            public_only(captured.public_summary())
            captured.verify_source()
        public_only(scan.public_summary())
        image.verify_source()
    assert source_signature(value.profile) == before


def test_actual_v1_admission_then_admission_only_upgrade_keeps_the_original_parent(tmp_path, monkeypatch, request):
    value = upgrade_fixture.env.__wrapped__(tmp_path, monkeypatch, request)
    original, first = upgrade_fixture.legacy_admission(value)
    with storage_admin.connection(value.profile) as connection:
        before = public_rows(connection)
        assert schema.detect(connection) == "v1"
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        image.connection.set_authorizer(caller_policy([]))
        assert inputs.read_continuity_projection(image.connection) == before
        image.verify_source()
    with upgrade_fixture.migration(value.profile) as connection:
        changed = upgrade.upgrade_locked(connection, profile=value.profile, target_variant="admission2")
        assert changed["to_variant"] == schema.detect(connection) == "admission2"
    upgrade_fixture.enable_prospective_v2(value, monkeypatch)
    second = sql2.readmit(profile=value.profile,
        request=upgrade_fixture.next_readmit2(value, original, first), now=cloud_fixture.NOW)
    assert second["format_version"] == 2 and second["previous_admission_id"] == first["admission_id"]
    with storage_admin.connection(value.profile) as connection:
        assert schema.detect(connection) == "admission2"
        expected = public_rows(connection)
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        image.connection.set_authorizer(caller_policy([]))
        projected = inputs.read_continuity_projection(image.connection)
        assert roots.normalize_sql(projected) == expected
        chain = {row["admission_id"]: row for row in projected[schema.ADMISSION]}
        assert chain[first["admission_id"]] == before[schema.ADMISSION][0]
        assert chain[first["admission_id"]]["format_version"] == 1
        assert chain[second["admission_id"]]["format_version"] == 2
        assert chain[second["admission_id"]]["previous_admission_id"] == first["admission_id"]
        assert chain[second["admission_id"]]["result_sha256"] == wire.digest(second)
        public_only(projected)
        image.verify_source()


@pytest.mark.parametrize("table", [schema.ACTIVATION, schema.REGISTRATION])
def test_format2_is_not_accepted_for_activation_or_registration(joint, table):
    projection = roots.normalize_sql(joint[1])
    projection[table][0]["format_version"] = 2
    with pytest.raises(roots.ContinuityRootError) as denied:
        roots.normalize_sql(projection)
    assert denied.value.code == "CONTINUITY_ROOT_SQL_PROJECTION_INVALID"


@pytest.mark.parametrize("table", tuple(roots.SQL_COLUMNS))
@pytest.mark.parametrize("version", [True, False, 0, 3])
def test_projection_rejects_boolean_zero_and_unknown_versions(joint, table, version):
    projection = roots.normalize_sql(joint[1])
    projection[table][0]["format_version"] = version
    with pytest.raises(roots.ContinuityRootError) as denied:
        roots.normalize_sql(projection)
    assert denied.value.code == "CONTINUITY_ROOT_SQL_PROJECTION_INVALID"


@pytest.mark.parametrize("field", ["request_json", "lease_token"])
def test_public_format2_projection_rejects_extra_private_fields(joint, field):
    projection = roots.normalize_sql(joint[1])
    projection[schema.ADMISSION][0][field] = "isolated-private-marker"
    with pytest.raises(roots.ContinuityRootError) as denied:
        roots.normalize_sql(projection)
    assert denied.value.code == "CONTINUITY_ROOT_SQL_PROJECTION_INVALID"


@pytest.mark.parametrize("damage", ["unknown_ddl", "temp_shadow"])
def test_shared_reader_rejects_unknown_variant_objects_without_repair(joint, damage):
    value = joint[0]
    with storage_admin.connection(value.profile) as connection:
        assert schema.detect(connection) == "admission2_dispose2"
        connection.set_authorizer(caller_policy([]))
        assert inputs.read_continuity_projection(connection) == joint[1]
        connection.set_authorizer(None)
        if damage == "unknown_ddl":
            connection.execute(f"ALTER TABLE {schema.ADMISSION} ADD COLUMN unknown_private_column TEXT")
        else:
            connection.execute(f"CREATE TEMP TABLE {schema.ADMISSION}(private_value TEXT)")
        connection.commit()
        before_ddl, before_rows = ddl_digest(connection), digest(public_rows(connection))
        connection.set_authorizer(caller_policy([]))
        with pytest.raises(ValueError) as denied:
            inputs.read_continuity_projection(connection)
        assert denied.value.code == "INPUT_SOURCE_CONTINUITY_SCHEMA_INVALID"
        connection.set_authorizer(None)  # Only the caller removes its policy.
        assert ddl_digest(connection) == before_ddl
        assert digest(public_rows(connection)) == before_rows


def test_reader_retains_actual_caller_authorizer_and_progress_and_denies_private_sql(joint):
    value = joint[0]
    with storage_admin.connection(value.profile) as connection:
        connection.execute("CREATE TABLE caller_notes(value TEXT)")
        connection.execute("INSERT INTO caller_notes VALUES('caller-owned')")
        connection.execute(f"CREATE VIEW caller_public_projection AS SELECT admission_id FROM main.{schema.ADMISSION}")
        connection.commit()
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        events, progress = [], []
        image.connection.set_authorizer(caller_policy(events))
        image.connection.set_progress_handler(lambda: progress.append(1) or 0, 1)
        projected = inputs.read_continuity_projection(image.connection)
        assert projected == joint[1]
        previous = len(progress)
        events.clear()
        assert image.connection.execute("SELECT value FROM caller_notes").fetchall() == [("caller-owned",)]
        assert (sqlite3.SQLITE_READ, "caller_notes", "value", "main", None) in events
        assert len(progress) > previous
        for table, columns in old_schema.COLUMNS.items():
            for column in columns:
                if column.endswith("_json"):
                    events.clear()
                    with pytest.raises(sqlite3.DatabaseError):
                        image.connection.execute(f'SELECT "{column}" FROM main."{table}"').fetchall()
                    assert (sqlite3.SQLITE_READ, table, column, "main", None) in events
        events.clear()
        with pytest.raises(sqlite3.DatabaseError):
            image.connection.execute(f"UPDATE main.{schema.ADMISSION} SET attempts=4")
        assert any(event[0] == sqlite3.SQLITE_UPDATE for event in events)
        events.clear()
        with pytest.raises(sqlite3.DatabaseError):
            image.connection.execute("SELECT admission_id FROM caller_public_projection").fetchall()
        assert any(event[0] == sqlite3.SQLITE_READ and event[4] == "caller_public_projection" for event in events)
        image.verify_source()
