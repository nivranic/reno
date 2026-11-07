"""Actual version routing and public CLI; no replaced recovery producers."""
from importlib import import_module
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import cli, db, home_recovery_source as legacy_sources, migrations, storage_admin, storage_backups
from reno import source_preservation_witness as witness, sync_protocol
from tests import test_home_recovery_home as old_home
from tests.test_home_recovery17 import old16_environment, current17_environment, completed_released16_home
from tests.test_home_recovery17 import read_sql, receipt
from tests.schema_fixtures import assert_current17_runtime, migrate_released16_to_current
from tests.test_home_recovery_source import sql_rows
from tests.test_home_recovery_source17 import PUBLIC_FIELDS, create, opened, paths, old_source_files, assert_old_bytes


@pytest.fixture
def modules():
    return SimpleNamespace(source=import_module("reno.home_recovery_source_dispatch"),
                           recovery=import_module("reno.home_recovery_dispatch"),
                           sdk=import_module("reno.home_recovery_source17"))


def profile_args(profile):
    return ["--db", str(profile.database), "--media", str(profile.media),
            "--reports", str(profile.reports), "--checkpoints", str(profile.checkpoints)]


def cli_call(capsys, profile, *args):
    capsys.readouterr()
    status = cli.main([*args, *profile_args(profile)])
    captured = capsys.readouterr()
    if status == 0:
        assert not captured.err
        body = captured.out
    else:
        assert not captured.out
        body = captured.err
    return status, json.loads(body)


def test_actual16_dispatch_keeps_legacy_source_and_nine_key_recovery_without_global_promotion(modules, old16_environment):
    value = old16_environment
    profile, original_id, _library, _node = value
    store = old_home.state(value)
    old_home.changed(value)
    before = sql_rows(profile)
    new_id = str(uuid.uuid4())
    result = modules.source.create_checkpoint(profile=profile, source_id=new_id)
    declaration = json.loads(legacy_sources._source_path(profile, new_id).read_bytes())
    assert declaration["schema_version"] == 16 and declaration["format_version"] == 1
    assert sql_rows(profile) == before
    assert set(modules.source.read_checkpoint(profile=profile, source_id=new_id)) == PUBLIC_FIELDS
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN")
        # Actual16 public read/create dispatch retains the original SDK. The
        # new facade's borrowed open is exclusively a genuine17 read window.
        with legacy_sources.open_source(con, new_id, profile=profile) as source:
            assert source.public() == {key: item for key, item in result.items() if key != "replayed"}
        con.rollback()
    observed = modules.recovery.preview(profile=profile, source_id=original_id)
    operation = str(uuid.uuid4())
    restored = modules.recovery.apply(profile=profile, source_id=original_id, expected_digest=observed["digest"], operation_id=operation)
    record = receipt(SimpleNamespace(profile=profile), operation)
    assert len(json.loads(record["checkpoint_json"])) == 9
    assert store.read()["local_epoch"] == restored["after_version"]["library_epoch"]
    assert modules.recovery.resume(profile=profile, operation_id=operation) == {**restored, "replayed": True}
    assert migrations.SCHEMA_VERSION == 16 and getattr(sync_protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 0) == 0


def test_actual17_dispatch_reads_old_source_with_real_readonly_proof_and_recovers_wrapper2(modules, current17_environment):
    value = current17_environment
    old_bytes = old_source_files(value)
    before, state = read_sql(value), value.store.read()
    with opened(modules.source, value, value.source_id) as (_con, source, _actual):
        assert source.declaration["schema_version"] == 16
        assert set(source.public()) == PUBLIC_FIELDS
    with value.store.reference_operation():
        with witness.sql_window_locked(profile=value.profile, store=value.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with pytest.raises(witness.WitnessCaptureError, match="FACTORY_REQUIRED"):
                with modules.source.open_source(con, value.source_id, profile=value.profile):
                    pytest.fail("Even an old declaration on actual17 requires the original read window proof")
    assert read_sql(value) == before and value.store.read() == state
    observed = modules.recovery.preview(profile=value.profile, source_id=value.source_id)
    operation = str(uuid.uuid4())
    result = modules.recovery.apply(profile=value.profile, source_id=value.source_id, expected_digest=observed["digest"], operation_id=operation)
    assert json.loads(receipt(value, operation)["checkpoint_json"])["format_version"] == 2
    assert modules.recovery.resume(profile=value.profile, operation_id=operation) == {**result, "replayed": True}
    assert_old_bytes(old_bytes)


def test_source_dispatch_does_not_accept_a_writable_factory_window_for_old16_declaration_on17(modules, current17_environment):
    value = current17_environment
    with value.store.reference_operation():
        with witness.sql_window_locked(profile=value.profile, store=value.store, writable=True) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=value.profile, store=value.store) as actual:
                with pytest.raises(witness.WitnessCaptureError, match="READ_WINDOW_REQUIRED"):
                    with modules.source.open_source(con, value.source_id, profile=value.profile, actual_witness=actual):
                        pytest.fail("Declaration version must not erase the original writer mode")


@pytest.mark.parametrize("source_schema", [16, 17])
def test_uncommitted_version_shadow_cannot_skip_facade_gate_before_original_declaration_io(modules, current17_environment, monkeypatch, source_schema):
    value = current17_environment
    source_id = value.source_id if source_schema == 16 else create(modules.sdk, value)[0]
    before, state = read_sql(value), value.store.read()

    def forbidden_long_read(*_args, **_kwargs):
        pytest.fail("A caller's uncommitted version change must not authorize declaration SHA or source loading")

    with value.store.reference_operation():
        con = sqlite3.connect(value.profile.database)
        try:
            storage_admin.from_connection(con, profile=value.profile)
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE main.meta SET value='16' WHERE key='migration_version'")
            assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
            with monkeypatch.context() as scoped:
                scoped.setattr(legacy_sources, "_pin_file", forbidden_long_read)
                with pytest.raises(witness.WitnessCaptureError, match="FACTORY_REQUIRED"):
                    with modules.source.open_source(con, source_id, profile=value.profile):
                        pytest.fail("A mutable SQL version is not a read capability")
            assert con.in_transaction
        finally:
            con.rollback()
            con.close()
    assert read_sql(value) == before and value.store.read() == state


@pytest.mark.parametrize("version", ["18", "017", "not-an-integer"])
def test_actual_unknown_or_noncanonical_version_is_rejected_without_migration_or_files(modules, current17_environment, version):
    value = current17_environment
    with sqlite3.connect(value.profile.database) as con:
        con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (version,))
    before, state = read_sql(value), value.store.read()
    identifier = str(uuid.uuid4())
    with pytest.raises(db.SchemaVersionError):
        modules.source.create_checkpoint(profile=value.profile, source_id=identifier)
    with pytest.raises(db.SchemaVersionError):
        modules.recovery.preview(profile=value.profile, source_id=value.source_id)
    assert read_sql(value) == before and value.store.read() == state
    assert not paths(value, identifier)[0].exists()
    assert_current17_runtime()


@pytest.mark.parametrize("schema", [16, 17])
def test_cli_real_source_create_read_preview_apply_and_resume_routes_actual_version(modules, request, capsys, schema):
    if schema == 17:
        value = request.getfixturevalue("current17_environment")
        profile, store = value.profile, value.store
        old_bytes = old_source_files(value)
    else:
        old = request.getfixturevalue("old16_environment")
        profile, store = old[0], old_home.state(old)
        value = SimpleNamespace(profile=profile, store=store)
        old_bytes = {}
    source_id = str(uuid.uuid4())
    status, created = cli_call(capsys, profile, "home-checkpoint", source_id, "--create", "--apply")
    assert status == 0 and created["source_id"] == source_id and set(created) == PUBLIC_FIELDS | {"replayed"}
    status, public = cli_call(capsys, profile, "home-checkpoint", source_id)
    assert status == 0 and public == {key: item for key, item in created.items() if key != "replayed"}
    declaration = json.loads(paths(value, source_id)[0].read_bytes())
    assert declaration["schema_version"] == schema
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE knowledge_atom SET claim='CLI之后的新知识' WHERE id='home_atom'")
    state = store.read()
    status, observed = cli_call(capsys, profile, "home-restore", source_id)
    assert status == 0 and observed["source"]["source_id"] == source_id
    operation = str(uuid.uuid4())
    status, result = cli_call(capsys, profile, "home-restore", source_id, "--apply", "--expected-digest", observed["digest"], "--operation-id", operation)
    assert status == 0 and result["operation_id"] == operation
    checkpoint = json.loads(receipt(value, operation)["checkpoint_json"])
    assert (checkpoint.get("format_version") == 2) if schema == 17 else (len(checkpoint) == 9)
    assert store.read() == {**state, "local_epoch": result["after_version"]["library_epoch"]}
    status, replay = cli_call(capsys, profile, "home-restore", "--resume", "--operation-id", operation)
    assert status == 0 and replay == {**result, "replayed": True}
    assert_old_bytes(old_bytes)
    if schema == 16:
        assert migrations.SCHEMA_VERSION == 16 and getattr(sync_protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 0) == 0
    else:
        assert_current17_runtime()


@pytest.mark.parametrize("arguments", [
    ("home-restore", "--apply"),
    ("home-restore", "--resume"),
    ("home-checkpoint", "--create"),
])
def test_cli_invalid_parameters_keep_old_sanitized_error_contract(current17_environment, capsys, arguments):
    value = current17_environment
    source_id = str(uuid.uuid4())
    args = [arguments[0], source_id, *arguments[1:]] if arguments[0] != "home-restore" or "--resume" not in arguments else list(arguments)
    before, state = read_sql(value), value.store.read()
    status, error = cli_call(capsys, value.profile, *args)
    assert status == 2 and set(error) == {"code", "detail"}
    assert error["code"] in {"HOME_RECOVERY_EXPECTATION_REQUIRED", "HOME_CHECKPOINT_EXPECTATION_REQUIRED"}
    assert read_sql(value) == before and value.store.read() == state


def test_cli_unmigrated_image_uses_old_schema_error_without_exposing_path(current17_environment, capsys):
    value = current17_environment
    with sqlite3.connect(value.profile.database) as con:
        con.execute("UPDATE meta SET value='18' WHERE key='migration_version'")
    before, state = read_sql(value), value.store.read()
    status, error = cli_call(capsys, value.profile, "home-restore", value.source_id)
    assert status != 0 and error["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert str(value.profile.database) not in json.dumps(error)
    assert read_sql(value) == before and value.store.read() == state


def test_cli_parser_rejection_does_not_continue_into_valid_restore_preview(old16_environment, capsys):
    profile, source_id, _library, _node = old16_environment
    before = sql_rows(profile)
    capsys.readouterr()
    with pytest.raises(SystemExit) as rejected:
        cli.main(["home-restore", source_id, "--not-a-real-restore-option", *profile_args(profile)])
    captured = capsys.readouterr()
    assert rejected.value.code == 2
    assert not captured.out
    error = json.loads(captured.err)
    assert set(error) == {"code", "detail"} and error["code"] == "HOME_RECOVERY_ARGUMENT_INVALID"
    assert sql_rows(profile) == before


@pytest.mark.parametrize("missing_image", [False, True])
def test_original_ready16_image_finalizes_after17_upgrade_without_legacy_create_or_global_connect(modules, tmp_path, monkeypatch, missing_image):
    # Every original16 producer/owner closes before the ordinary production17
    # migration. The original intent, UUID and ready image stay unchanged.
    with completed_released16_home(tmp_path, monkeypatch) as value:
        identifier = str(uuid.uuid4())
        declaration, intent_path, sql17_path = paths(value, identifier)
        original_write = storage_backups._write_json
        marker = OSError("original16 ready image before its declaration")

        def stop_original_declaration(path, *args, **kwargs):
            if path == declaration:
                raise marker
            return original_write(path, *args, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(storage_backups, "_write_json", stop_original_declaration)
            with pytest.raises(OSError) as caught:
                legacy_sources.create_checkpoint(profile=value.profile, source_id=identifier)
        assert caught.value is marker and not declaration.exists()
        intent = json.loads(intent_path.read_bytes())
        assert intent["schema_version"] == 16 and intent["source_id"] == identifier
        catalog = storage_backups._catalog(value.profile) / (intent["backup_id"] + ".json")
        registry = json.loads(catalog.read_bytes())
        assert registry["state"] == "ready"
        image = Path(registry["destination"])
        descriptor = image.with_name(image.name + storage_backups.SUFFIX)
        original_files = {path: path.read_bytes() for path in (intent_path, catalog, image, descriptor)}
        if missing_image:
            image.unlink()
        assert migrations.SCHEMA_VERSION == 16 and getattr(sync_protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 0) == 0
    migrate_released16_to_current(value.profile)
    before, state = read_sql(value), value.store.read()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Finishing an original16 image on17 must not call legacy.create or global db.connect")

    with monkeypatch.context() as scoped:
        scoped.setattr(legacy_sources, "create_checkpoint", forbidden)
        scoped.setattr(db, "connect", forbidden)
        if missing_image:
            with pytest.raises(ValueError):
                modules.source.create_checkpoint(profile=value.profile, source_id=identifier)
            assert not image.exists() and not declaration.exists()
        else:
            result = modules.source.create_checkpoint(profile=value.profile, source_id=identifier)
            assert result["source_id"] == identifier and result["source_version"] == intent["source_version"]
            body = legacy_sources._declaration(declaration.read_bytes())
            assert body["schema_version"] == 16 and body["backup_id"] == intent["backup_id"]
            public = modules.source.read_checkpoint(profile=value.profile, source_id=identifier)
            assert public == {key: item for key, item in result.items() if key != "replayed"}
    assert not sql17_path.exists() and read_sql(value) == before and value.store.read() == state
    assert all(path.read_bytes() == raw for path, raw in original_files.items() if path != image or not missing_image)
    assert_current17_runtime()
