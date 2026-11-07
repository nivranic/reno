"""Genuine isolated17 source production/readers; no caller-granted authority."""
from contextlib import contextmanager
from importlib import import_module
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import db, home_recovery_source as legacy_sources, migrations
from reno import source_preservation_witness as witness, storage_admin, storage_backups
from reno import sync_protocol, tasks
from tests.test_home_recovery17 import old16_environment, actual_home16, current17_environment
from tests.test_home_recovery17 import env as historical17_environment
from tests.test_home_recovery17 import canonical, read_sql, receipt, assert_preserved_operational


PUBLIC_FIELDS = {"source_kind", "source_id", "source_version", "source_sha256",
                 "source_binding_sha256", "knowledge_sha256", "row_counts"}
SQL17_FIELDS = {"format_version", "action", "source_id", "intent_sha256", "profile_sha256",
                "library_id", "node_id", "source_version", "schema_version", "sql_image_sha256", "created_at"}


@pytest.fixture
def sdk():
    # The module must be real once execution is authorized. No importorskip or
    # fixture producer substitutes can promote unavailable functionality.
    return import_module("reno.home_recovery_source17")


def create(sdk, value, source_id=None):
    identifier = source_id or str(uuid.uuid4())
    return identifier, sdk.create_checkpoint(profile=value.profile, source_id=identifier)


@contextmanager
def opened(sdk, value, source_id):
    with value.store.reference_operation():
        with witness.sql_window_locked(profile=value.profile, store=value.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=value.profile, store=value.store) as actual:
                with sdk.open_source(con, source_id, profile=value.profile, actual_witness=actual) as source:
                    yield con, source, actual


def paths(value, identifier):
    declaration = legacy_sources._source_path(value.profile, identifier)
    return declaration, declaration.with_name(identifier + ".intent.json"), declaration.with_name(identifier + ".sql17.json")


def image_facts(value, identifier):
    declaration = json.loads(paths(value, identifier)[0].read_bytes())
    catalog = storage_backups._catalog(value.profile) / (declaration["backup_id"] + ".json")
    registry = json.loads(catalog.read_bytes())
    image = Path(registry["destination"])
    descriptor = image.with_name(image.name + storage_backups.SUFFIX)
    return declaration, catalog, image, descriptor


def old_source_files(value):
    declaration, intent, _ = paths(value, value.source_id)
    body = json.loads(declaration.read_bytes())
    catalog = storage_backups._catalog(value.profile) / (body["backup_id"] + ".json")
    image = Path(json.loads(catalog.read_bytes())["destination"])
    names = (declaration, intent, catalog, image, image.with_name(image.name + storage_backups.SUFFIX))
    return {path: path.read_bytes() for path in names}


def assert_old_bytes(original):
    assert all(path.read_bytes() == body for path, body in original.items())


def test_genuine17_source_is_knowledge_only_uses_original_v1_backup_and_keeps_all_live_facts(sdk, historical17_environment):
    # This historical positive case deliberately retains global16/cap0 and
    # the v1 schema seam; the modern cases below select their own current17.
    env = historical17_environment
    before, state, old_bytes = read_sql(env), env.store.read(), old_source_files(env)
    source_id, result = create(sdk, env)
    assert set(result) == PUBLIC_FIELDS | {"replayed"} and result["replayed"] is False
    assert read_sql(env) == before and env.store.read() == state
    assert_old_bytes(old_bytes)
    declaration_path, intent_path, sql17_path = paths(env, source_id)
    declaration, intent, sql17 = [json.loads(path.read_bytes()) for path in (declaration_path, intent_path, sql17_path)]
    assert set(declaration) == legacy_sources._FIELDS
    assert declaration["format_version"] == 1 and type(declaration["schema_version"]) is int and declaration["schema_version"] == 17
    assert declaration["backup_id"] == str(uuid.uuid5(uuid.UUID(source_id), "reno.home.knowledge.checkpoint.v1"))
    assert set(sql17) == SQL17_FIELDS and sql17["schema_version"] == 17 and sql17["format_version"] == 1
    assert sql17["source_id"] == source_id and sql17["action"] == "produce_home_checkpoint17"
    assert sql17["intent_sha256"] == hashlib.sha256(intent_path.read_bytes()).hexdigest()
    assert sql17["source_version"] == intent["source_version"] == declaration["source_version"]
    with pytest.raises(legacy_sources.HomeRecoverySourceError, match="SOURCE_SCHEMA_UNSUPPORTED"):
        legacy_sources._declaration(declaration_path.read_bytes())
    with opened(sdk, env, source_id) as (_con, source, _actual):
        assert set(source.public()) == PUBLIC_FIELDS
        assert source.public() == {key: value for key, value in result.items() if key != "replayed"}
        assert set(source.rows) == set(legacy_sources.COLUMNS)
        assert source.rows["meta"] == [{"key": "step:home_video:atomize", "value": "changed-step"}]
        assert source.rows["knowledge_atom"][0]["claim"] == "后来的知识"
        encoded = canonical(source.public())
        assert str(env.profile.database) not in encoded and "private-stays-local" not in encoded
    assert sdk.read_checkpoint(profile=env.profile, source_id=source_id) == {key: value for key, value in result.items() if key != "replayed"}
    again = sdk.create_checkpoint(profile=env.profile, source_id=source_id)
    assert again == {**result, "replayed": True}
    assert read_sql(env) == before and env.store.read() == state
    assert_old_bytes(old_bytes)
    assert migrations.SCHEMA_VERSION == 16 and getattr(sync_protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 0) == 0
    with pytest.raises(db.SchemaVersionError):
        db.connect(env.profile.database)


def test_genuine17_source_restores_knowledge_and_preserves_current_paid_unknown_private_and_state(sdk, current17_environment):
    env = current17_environment
    source_id, _result = create(sdk, env)
    old_bytes = old_source_files(env)
    with sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE knowledge_atom SET claim='真正更晚知识' WHERE id='home_atom'")
        con.execute("UPDATE meta SET value=? WHERE key='private-test-blob'", (b"\x00\xffnew-current-private",))
    before, state = read_sql(env), env.store.read()
    recovery = import_module("reno.home_recovery17")
    observed = recovery.preview(profile=env.profile, source_id=source_id)
    operation = str(uuid.uuid4())
    result = recovery.apply(profile=env.profile, source_id=source_id, expected_digest=observed["digest"], operation_id=operation)
    after = read_sql(env)
    assert_preserved_operational(before, after)
    assert env.store.read() == {**state, "local_epoch": result["after_version"]["library_epoch"]}
    row = receipt(env, operation)
    assert json.loads(row["checkpoint_json"])["format_version"] == 2
    with sqlite3.connect(env.profile.database) as con:
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='home_atom'").fetchone() == ("后来的知识",)
        assert con.execute("SELECT value FROM meta WHERE key='private-test-blob'").fetchone() == (b"\x00\xffnew-current-private",)
    replay = recovery.resume(profile=env.profile, operation_id=operation)
    assert replay == {**result, "replayed": True} and read_sql(env) == after
    assert_old_bytes(old_bytes)


def test_original_ready_image_and_sql17_proof_finish_after_later_kb_edit_with_same_source_uuid(sdk, current17_environment, monkeypatch):
    env = current17_environment
    source_id = str(uuid.uuid4())
    declaration_path, intent_path, sql17_path = paths(env, source_id)
    original_persist = sdk._persist
    marker = OSError("after original ready17 image before declaration")
    stopped_at_declaration = False

    def stop_declaration(path, *args, **kwargs):
        nonlocal stopped_at_declaration
        if path == declaration_path:
            stopped_at_declaration = True
            raise marker
        return original_persist(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(sdk, "_persist", stop_declaration)
        with pytest.raises(legacy_sources.HomeRecoverySourceError, match="SOURCE_INVALID"):
            create(sdk, env, source_id)
    assert stopped_at_declaration and not declaration_path.exists()
    originals = {path: path.read_bytes() for path in (intent_path, sql17_path)}
    intent = json.loads(originals[intent_path])
    catalog_path = storage_backups._catalog(env.profile) / (intent["backup_id"] + ".json")
    registry = json.loads(catalog_path.read_bytes())
    assert registry["state"] == "ready"
    image_path = Path(registry["destination"])
    originals.update({path: path.read_bytes() for path in (catalog_path, image_path, image_path.with_name(image_path.name + storage_backups.SUFFIX))})
    with sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE knowledge_atom SET claim='晚于原ready image的知识' WHERE id='home_atom'")
    current_sql, current_state = read_sql(env), env.store.read()
    same_id, result = create(sdk, env, source_id)
    assert same_id == source_id and result["source_version"] == intent["source_version"]
    assert all(path.read_bytes() == body for path, body in originals.items())
    assert read_sql(env) == current_sql and env.store.read() == current_state
    with opened(sdk, env, source_id) as (_con, source, _actual):
        assert source.rows["knowledge_atom"][0]["claim"] == "后来的知识"
    with sqlite3.connect(env.profile.database) as con:
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='home_atom'").fetchone() == ("晚于原ready image的知识",)
    declaration = json.loads(declaration_path.read_bytes())
    assert declaration["backup_id"] == intent["backup_id"] and declaration["image_sha256"] == hashlib.sha256(originals[image_path]).hexdigest()


@pytest.mark.parametrize("target", ["sql17", "catalog", "descriptor", "image", "video", "audio", "frame"])
def test_actual_registered_source_provenance_and_media_tamper_are_rejected(sdk, current17_environment, target):
    env = current17_environment
    source_id, _ = create(sdk, env)
    _declaration, catalog, image, descriptor = image_facts(env, source_id)
    if target == "sql17":
        path = paths(env, source_id)[2]
        body = json.loads(path.read_bytes())
        body["sql_image_sha256"] = "8" * 64
        path.write_bytes(canonical(body).encode())
    elif target == "catalog":
        body = json.loads(catalog.read_bytes())
        body["backup_id"] = str(uuid.uuid4())
        catalog.write_bytes(canonical(body).encode())
    elif target == "descriptor":
        body = json.loads(descriptor.read_bytes())
        body["database_file"]["sha256"] = "8" * 64
        descriptor.write_bytes(canonical(body).encode())
    else:
        path = image if target == "image" else env.profile.media / {
            "video": "originals/video.mp4", "audio": "audio/audio.m4a", "frame": "frames/frame.jpg"}[target]
        body = path.read_bytes()
        path.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        sdk.read_checkpoint(profile=env.profile, source_id=source_id)
    assert read_sql(env) == before and env.store.read() == state


@pytest.mark.parametrize("part", ["intent", "sql17"])
def test_ready_source_cannot_gain_missing_original_provenance(sdk, current17_environment, part):
    env = current17_environment
    source_id, _ = create(sdk, env)
    declaration, intent, sql17 = paths(env, source_id)
    declaration.unlink()
    (intent if part == "intent" else sql17).unlink()
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        create(sdk, env, source_id)
    assert not declaration.exists() and read_sql(env) == before and env.store.read() == state


@pytest.mark.parametrize("fake", [None, {}, {"readonly": True, "schema_version": 17}])
def test_plain_reader_or_caller_dto_cannot_open_genuine17_source(sdk, current17_environment, fake):
    env = current17_environment
    source_id, _ = create(sdk, env)
    with env.store.reference_operation():
        con = sqlite3.connect(env.profile.database.as_uri() + "?mode=ro", uri=True)
        try:
            storage_admin.from_connection(con, profile=env.profile)
            con.execute("BEGIN")
            con.execute("PRAGMA query_only=ON")
            with pytest.raises(witness.WitnessCaptureError):
                with sdk.open_source(con, source_id, profile=env.profile, actual_witness=fake):
                    pytest.fail("A query_only pragma or caller DTO is not a factory capability")
        finally:
            con.close()


def test_original_writable_factory_window_remains_disallowed_when_query_only_is_flipped_on(sdk, current17_environment):
    env = current17_environment
    source_id, _ = create(sdk, env)
    with env.store.reference_operation():
        with witness.sql_window_locked(profile=env.profile, store=env.store, writable=True) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=env.profile, store=env.store) as actual:
                with pytest.raises(witness.WitnessCaptureError, match="READ_WINDOW_REQUIRED"):
                    with sdk.open_source(con, source_id, profile=env.profile, actual_witness=actual):
                        pytest.fail("An original SQL writer must never perform long source reads")


def test_expired_actual_capture_cannot_be_reused_with_the_original_connection(sdk, current17_environment):
    env = current17_environment
    source_id, _ = create(sdk, env)
    with env.store.reference_operation():
        with witness.sql_window_locked(profile=env.profile, store=env.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=env.profile, store=env.store) as actual:
                pass
            with pytest.raises(witness.WitnessCaptureError):
                with sdk.open_source(con, source_id, profile=env.profile, actual_witness=actual):
                    pytest.fail("A detached original capture must not become live again")


def test_actual_capture_before_sql_transaction_change_cannot_authorize_a_later_reader(sdk, current17_environment):
    env = current17_environment
    source_id, _ = create(sdk, env)
    direct_refusal = False
    with pytest.raises(witness.WitnessCaptureError):
        with env.store.reference_operation():
            with witness.sql_window_locked(profile=env.profile, store=env.store, writable=False) as con:
                con.execute("PRAGMA query_only=ON")
                with witness.capture_locked(con, profile=env.profile, store=env.store) as actual:
                    con.rollback()
                    con.execute("BEGIN")
                    with pytest.raises(witness.WitnessCaptureError):
                        with sdk.open_source(con, source_id, profile=env.profile, actual_witness=actual):
                            pytest.fail("Original transaction capability must not survive replacement")
                    direct_refusal = True
    assert direct_refusal


def test_direct17_loader_rejects_a_genuine16_declaration_even_with_a_valid17_read_capture(sdk, current17_environment):
    env = current17_environment
    before, state = read_sql(env), env.store.read()
    with pytest.raises(legacy_sources.HomeRecoverySourceError, match="SOURCE_SCHEMA_UNSUPPORTED"):
        with opened(sdk, env, env.source_id):
            pytest.fail("Only the source dispatcher may select the original16 parser")
    assert read_sql(env) == before and env.store.read() == state


def test_new17_producer_refuses_actual16_without_publishing_source_or_promoting_global_schema(sdk, actual_home16):
    value, source_id = actual_home16, str(uuid.uuid4())
    state = value.store.read()
    with pytest.raises(ValueError):
        sdk.read_checkpoint(profile=value.profile, source_id=value.source_id)
    with pytest.raises(ValueError):
        sdk.create_checkpoint(profile=value.profile, source_id=source_id)
    assert not paths(value, source_id)[0].exists() and value.store.read() == state
    with storage_admin.connection(value.profile) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
    assert migrations.SCHEMA_VERSION == 16 and getattr(sync_protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 0) == 0


def test_busy_worker_refusal_keeps_original_worker_and_all_live_facts(sdk, current17_environment):
    env = current17_environment
    source_id = str(uuid.uuid4())
    before, state = read_sql(env), env.store.read()
    with tasks.process_lock(profile=env.profile) as acquired:
        assert acquired
        with pytest.raises(ValueError, match="WORKER_BUSY"):
            create(sdk, env, source_id)
        assert tasks.holds_process_lock(profile=env.profile)
    assert read_sql(env) == before and env.store.read() == state and not paths(env, source_id)[0].exists()
