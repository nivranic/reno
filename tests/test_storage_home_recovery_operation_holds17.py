"""File-only original-operation holds from genuine isolated SDK producers."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import home_recovery17 as recovery, home_recovery_source17 as sources
from reno import source_preservation_bundle as bundles, storage_backups as backups
from reno import storage_home_recovery_operation_holds17 as holds
from reno import storage_lifecycle as lifecycle
from reno import storage_admin, sync_queue, sync_state, sync_source_readmit_schema as readmit_schema, sync_media as safe
from tests.test_home_recovery_home import env as old16_environment
from tests.test_home_recovery17 import receipt


@pytest.fixture
def env(old16_environment):
    profile, old_source, library, node = old16_environment
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    store.initialize({"home_library_id": library, "home_node_id": node,
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()),
        "target_url": "https://synthetic.example.test"}, 1)
    store.enable_source_readmit()
    # Genuine prospective17 DDL on an isolated existing16 image. Every source,
    # bundle and restore below still uses its real actual factory/SDK entry.
    with storage_admin.connection(profile) as con:
        readmit_schema.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    return SimpleNamespace(profile=profile, store=store, root=None, source_id=old_source)


def canonical(value):
    return backups._json(value)


def records(value):
    return {record["backup_id"]: record for record, _identity in backups._catalog_entries(value.profile)}


@contextmanager
def captured(value, *, callback=None, limits=None):
    with lifecycle.exclusive(value.profile):
        with holds.capture_operation_holds_locked(value.profile, records(value), callback, limits) as capture:
            yield capture


def preserve(value, operation_id=None):
    operation_id = operation_id or str(uuid.uuid4())
    result = bundles.create(profile=value.profile, store=value.store, operation_id=operation_id, root_job_id=value.root)
    return operation_id, json.loads(result.manifest_json)


def checkpoint(value, source_id=None):
    source_id = source_id or str(uuid.uuid4())
    sources.create_checkpoint(profile=value.profile, source_id=source_id)
    path = sources.old._source_path(value.profile, source_id)
    return source_id, json.loads(path.read_bytes())


def unrelated_backup(value):
    identifier = str(uuid.uuid4())
    destination = value.profile.database.with_name(value.profile.database.name + ".bak-unused-" + identifier)
    with lifecycle.shared(value.profile):
        con = sqlite3.connect(value.profile.database.as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            backups.create(con, destination, profile=value.profile, kind="maintenance", backup_id=identifier)
        finally:
            con.close()
    return identifier


def pending_checkpoint(value, monkeypatch):
    identifier = str(uuid.uuid4())
    original = sources._persist
    declaration_path = sources.old._source_path(value.profile, identifier)
    entered = []
    def stop(path, *args, **kwargs):
        if path == declaration_path:
            entered.append(path)
            raise OSError("isolated crash after ready before declaration")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(sources, "_persist", stop)
        with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_INVALID"):
            sources.create_checkpoint(profile=value.profile, source_id=identifier)
    assert entered == [declaration_path] and not declaration_path.exists()
    intent = json.loads(declaration_path.with_name(identifier + ".intent.json").read_bytes())
    assert records(value)[intent["backup_id"]]["state"] == "ready"
    return identifier, intent


def test_standalone_completed_bundle_holds_original_maintenance_selection_and_is_pure(env):
    operation, manifest = preserve(env)
    unrelated = unrelated_backup(env)
    original = manifest["database_backup"]["backup_id"]
    assert receipt(env, operation) is None
    before_state = env.store.read()
    original_files = {path: path.read_bytes() for path in (env.profile.database.parent / bundles.OPERATIONS).glob("*.json")}
    with captured(env) as capture:
        assert not capture.unknown and capture.backup_ids == {original}
        ready = sorted((row for row in records(env).values() if row["kind"] == "maintenance" and row["state"] == "ready"),
                       key=lambda row: (row["created_at"], row["backup_id"]), reverse=True)
        selected = {row["backup_id"] for row in ready[1:]}
        assert original in selected and unrelated not in selected
        assert selected & capture.backup_ids == {original}
        capture.verify_for_commit()
        capture.verify_source()
    assert env.store.read() == before_state
    assert all(path.read_bytes() == raw for path, raw in original_files.items())


@pytest.mark.parametrize("phase", ["before_intent", "after_intent"])
def test_real_home_request_and_ready_bundle_hold_source_and_protection_before_sql_commit(env, monkeypatch, phase):
    source_id, declaration = checkpoint(env)
    operation = str(uuid.uuid4())
    observed = recovery.preview(profile=env.profile, source_id=source_id)
    target = operation + (".intent.json" if phase == "before_intent" else ".proposal.json")
    original = recovery._persist if phase == "before_intent" else recovery._write
    entered = []
    def stop(directory, name, *args, **kwargs):
        if name == target:
            entered.append(name)
            raise OSError("isolated Home request/bundle gap before SQL commit")
        return original(directory, name, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(recovery, "_persist" if phase == "before_intent" else "_write", stop)
        with pytest.raises((OSError, ValueError)):
            recovery.apply(profile=env.profile, source_id=source_id, expected_digest=observed["digest"], operation_id=operation)
    assert entered == [target] and receipt(env, operation) is None
    assert (env.profile.database.parent / bundles.OPERATIONS / (operation + ".receipt.json")).is_file()
    protection = bundles._ids(operation)[1]
    with captured(env) as capture:
        assert not capture.unknown
        assert {declaration["backup_id"], protection} <= capture.backup_ids


def test_pending_bundle_after_ready_before_manifest_holds_original_backup(env, monkeypatch):
    operation = str(uuid.uuid4())
    original = bundles._write_json
    entered = []
    def stop(directory, name, *args, **kwargs):
        if name == "manifest.json":
            entered.append(name)
            raise OSError("isolated bundle gap after original ready image")
        return original(directory, name, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(bundles, "_write_json", stop)
        with pytest.raises(OSError):
            preserve(env, operation)
    assert entered == ["manifest.json"]
    identifier = bundles._ids(operation)[1]
    assert records(env)[identifier]["state"] == "ready"
    with captured(env) as capture:
        assert not capture.unknown and capture.backup_ids == {identifier}


def test_pending_source_hold_retires_only_on_accurate_completed_declaration(env, monkeypatch):
    source_id, intent = pending_checkpoint(env, monkeypatch)
    with captured(env) as capture:
        assert not capture.unknown and capture.backup_ids == {intent["backup_id"]}
    sources.create_checkpoint(profile=env.profile, source_id=source_id)
    assert sources.old._source_path(env.profile, source_id).is_file()
    with captured(env) as capture:
        assert not capture.unknown and intent["backup_id"] not in capture.backup_ids


def test_completed_idle_source_has_no_permanent_hold_or_backup_handle(env):
    source_id, declaration = checkpoint(env)
    with captured(env) as capture:
        assert not capture.unknown and not capture.backup_ids
        original = records(env)[declaration["backup_id"]]
        # Unreferenced complete sources have no held image/catalog handle, so
        # an explicit retention writer is not accidentally blocked on Windows.
        with open(original["destination"], "r+b") as stream:
            assert stream.read(1)
        capture.verify_source()
    assert sources.read_checkpoint(profile=env.profile, source_id=source_id)["source_id"] == source_id


@pytest.mark.parametrize("damage", ["declaration", "companion", "intent"])
def test_corrupt_completed_source_never_retires_conditional_hold(env, damage):
    source_id, declaration = checkpoint(env)
    path = sources.old._source_path(env.profile, source_id)
    if damage == "declaration":
        body = json.loads(path.read_bytes())
        body["knowledge_sha256"] = "f" * 64
        path.write_bytes(canonical(body))
    elif damage == "companion":
        path.with_name(source_id + ".sql17.json").unlink()
    else:
        path.with_name(source_id + ".intent.json").unlink()
    with captured(env) as capture:
        assert declaration["backup_id"] in capture.backup_ids
        assert "SOURCE_PROOF_UNPROVEN" in capture.unknown


@pytest.mark.parametrize("damage", ["node", "certificate", "receipt", "copy", "image"])
def test_damaged_original_bundle_proof_is_unknown_and_keeps_exact_backup(env, damage):
    operation, manifest = preserve(env)
    root = env.profile.database.parent
    bundle_root = root / bundles.DIRECTORY / manifest["bundle_id"]
    if damage == "node":
        path = root / bundles.OPERATIONS / (operation + ".json")
        body = json.loads(path.read_bytes())
        body["node_id"] = str(uuid.uuid4())
        path.write_bytes(canonical(body))
    elif damage == "certificate":
        (bundle_root / "certificate.json").unlink()
    elif damage == "receipt":
        path = root / bundles.OPERATIONS / (operation + ".receipt.json")
        body = json.loads(path.read_bytes())
        body["reservation_sha256"] = "f" * 64
        path.write_bytes(canonical(body))
    elif damage == "copy":
        path = next((bundle_root / "files").iterdir())
        with open(safe._native_path(path), "wb") as stream:
            stream.write(b"isolated damaged witnessed file")
    else:
        path = Path(records(env)[manifest["database_backup"]["backup_id"]]["destination"])
        with path.open("r+b") as stream:
            stream.seek(128)
            stream.write(b"isolated-damaged-image")
    with captured(env) as capture:
        assert manifest["database_backup"]["backup_id"] in capture.backup_ids
        assert "BUNDLE_PROOF_UNPROVEN" in capture.unknown


def test_namespace_change_after_capture_rejects_short_and_full_final_checks(env):
    preserve(env)
    with captured(env) as capture:
        path = env.profile.database.parent / bundles.OPERATIONS / (str(uuid.uuid4()) + ".json")
        path.write_bytes(b"{}")
        try:
            with pytest.raises(holds.OperationHoldError, match="PROOF_CHANGED"):
                capture.verify_for_commit()
            with pytest.raises(holds.OperationHoldError, match="PROOF_CHANGED"):
                capture.verify_source()
        finally:
            path.unlink()


def test_held_metadata_changes_are_denied_or_detected_without_releasing_original_hold(env):
    operation, _manifest = preserve(env)
    path = env.profile.database.parent / bundles.OPERATIONS / (operation + ".json")
    if os.name == "nt":
        with captured(env) as capture:
            with pytest.raises(OSError):
                path.write_bytes(b"{}")
            capture.verify_source()
    else:
        with pytest.raises(holds.OperationHoldError, match="PROOF_CHANGED"):
            with captured(env) as capture:
                before = path.read_bytes()
                path.write_bytes(b"{}")
                with pytest.raises(holds.OperationHoldError, match="PROOF_CHANGED"):
                    capture.verify_source()
                path.write_bytes(before)
                # Restoring bytes does not restore original physical identity.
                with pytest.raises(holds.OperationHoldError, match="PROOF_CHANGED"):
                    capture.verify_for_commit()


def test_callback_cancellation_keeps_original_exception_identity(env):
    preserve(env)
    marker = ValueError("isolated exact cancellation object")
    calls = [0]
    def cancel():
        calls[0] += 1
        if calls[0] == 5:
            raise marker
    with pytest.raises(ValueError) as caught:
        with captured(env, callback=cancel):
            pytest.fail("cancelled capture must not publish a result")
    assert caught.value is marker


@pytest.mark.parametrize("budget", ["max_metadata_bytes", "max_read_bytes", "max_files", "max_operations"])
def test_limits_reject_instead_of_returning_an_exhaustive_empty_hold_set(env, budget):
    preserve(env)
    limits = replace(holds.OperationHoldLimits(), **{budget: 1})
    # max_operations=1 can describe one bundle and one completed old source;
    # the latter still counts as an observed operation for the bounded scan.
    with pytest.raises(holds.OperationHoldError, match="LIMIT"):
        with captured(env, limits=limits):
            pytest.fail("over-budget capture cannot be consumed")


def test_reader_requires_existing_ex_and_never_constructs_state_or_opens_sql(env, monkeypatch):
    _operation, manifest = preserve(env)
    def forbidden(*args, **kwargs):
        pytest.fail("file-only hold reader opened State/SQL/producer")
    from reno import sync_state, migrations
    with monkeypatch.context() as scoped:
        scoped.setattr(sync_state, "StateStore", forbidden)
        scoped.setattr(sqlite3, "connect", forbidden)
        scoped.setattr(migrations, "_open", forbidden)
        scoped.setattr(bundles, "create", forbidden)
        scoped.setattr(sources, "create_checkpoint", forbidden)
        with lifecycle.shared(env.profile):
            with pytest.raises(lifecycle.StorageLifecycleError):
                with holds.capture_operation_holds_locked(env.profile, records(env)):
                    pytest.fail("SH cannot become an EX hold capture")
        with captured(env) as capture:
            assert not capture.unknown and capture.backup_ids == {manifest["database_backup"]["backup_id"]}


def test_released_noncanonical_node_json_and_completed_legacy_source_stay_valid(env):
    node = env.profile.database.parent / "node-identity.json"
    original = node.read_bytes()
    assert original != canonical(json.loads(original))
    with captured(env) as capture:
        assert not capture.unknown and not capture.backup_ids
    assert node.read_bytes() == original


@pytest.mark.parametrize("retired", [("declaration",), ("declaration", "intent", "companion")])
def test_completed_restore_keeps_exact_image_after_fresh_source_metadata_retirement(env, retired):
    source_id, declaration = checkpoint(env)
    operation = str(uuid.uuid4())
    observed = recovery.preview(profile=env.profile, source_id=source_id)
    recovery.apply(profile=env.profile, source_id=source_id, expected_digest=observed["digest"], operation_id=operation)
    assert receipt(env, operation) is not None
    path = sources.old._source_path(env.profile, source_id)
    paths = {"declaration": path, "intent": path.with_name(source_id + ".intent.json"),
             "companion": path.with_name(source_id + ".sql17.json")}
    for kind in retired:
        paths[kind].unlink()
    with captured(env) as capture:
        assert not capture.unknown
        assert {declaration["backup_id"], bundles._ids(operation)[1]} <= capture.backup_ids
    # A maintenance hold never grants fresh source/restore execution rights.
    with pytest.raises(FileNotFoundError):
        sources.read_checkpoint(profile=env.profile, source_id=source_id)
