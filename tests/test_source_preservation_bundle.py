"""Actual isolated Home17 preservation producer; no replacement authority DTOs.

Tests call the real maintenance entry point and inspect its registered immutable
artifacts. Stored historical admission fixtures are preservation inputs, never
fresh admission or transport permissions. No real provider/model is invoked.
"""
from copy import deepcopy
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import config, db, source_preservation as preservation, storage_backups as backups, tasks
from reno import storage_admin, storage_lifecycle as lifecycle, sync_state
from reno import sync_source_authority as authority, sync_source_continuity as continuity
from reno import sync_source_continuity_service as cloud_service, sync_source_readmit_schema as readmit_schema
from tests.test_source_preservation import env as released16_home_environment
from tests.test_source_preservation import build_released16_environment, seed, PRIVATE, install_outbox
from tests.test_source_preservation_v17 import env as home_environment, historical17_paid_environment
from tests.schema_fixtures import assert_current17_runtime, released16
from tests.test_source_preservation_witness import install_core, seed_origin, historical_state, authority_state, prepare17
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_readmit_contract as sample, test_sync_source_readmit_schema as sql_records
from tests import test_storage_source_authority_roots as home_files


@pytest.fixture
def bundle_module():
    # The actual production interface must exist before execution. Collection
    # does not invent a stand-in or skip an unavailable producer.
    return importlib.import_module("reno.source_preservation_bundle")


@pytest.fixture
def populated_home(historical17_paid_environment):
    value = historical17_paid_environment
    value.paid, value.unknown = value.historical_paid_unknown
    value.core = install_core(value)
    value.origin = seed_origin(value, value.core)
    historical_state(value, {"source_authority": authority_state(value.core, origin=authority.record_ref(value.origin))})
    return value


@pytest.fixture
def bundle_env(populated_home):
    prepare17(populated_home)
    return populated_home


@pytest.fixture
def combined_env(tmp_path, request, monkeypatch):
    # One cold original16 image: genuine Home paid/unknown facts and genuine
    # Cloud defer/resume receipts are created before the FIRST17 installation.
    # No dynamically requested yielding Cloud fixture crosses Home's lifetime.
    with released16(monkeypatch):
        value = build_released16_environment(tmp_path)
        value.paid, value.unknown = seed(value)
        with monkeypatch.context() as old_cloud:
            old_cloud.setattr(config, "is_cloud", lambda: True)
            cloud = cloud_fixture.build_released16_cloud_environment(value.profile, request)
    assert_current17_runtime()
    cloud_fixture.transition_historical17_cloud_environment(cloud)
    cloud_fixture.refresh_historical17_cloud_handshake(cloud)
    value.core = install_core(value)
    value.origin = seed_origin(value, value.core)
    historical_state(value, {"source_authority": authority_state(value.core, origin=authority.record_ref(value.origin))})
    assert cloud.profile.identity == value.profile.identity
    state = value.store.read()
    assert cloud.target == {"library_id": state["binding"]["home_library_id"], "node_id": state["binding"]["home_node_id"]}
    # Cloud role applies only to its actual existing-file/SQL producer phase.
    # Its monkeypatch is gone before any Home State write or bundle consumer.
    with monkeypatch.context() as cloud_scope:
        cloud_scope.setattr(config, "is_cloud", lambda: True)
        active = cloud_fixture.activated(cloud)
        original = cloud_fixture.register_request(cloud, active)
        registered = cloud_service.register(profile=cloud.profile, request=original, now=cloud_fixture.NOW)
        disposed = cloud_service.dispose(profile=cloud.profile,
            request=cloud_fixture.dispose_request(cloud, original["original_request"], registered), now=cloud_fixture.NOW)
        assert disposed["disposition"] == "not_applied"
        # Exact historical admission bytes test preservation, not a new seal.
        admitted = sql_records.admission(body=cloud_fixture.bound(cloud, sample.readmit()))
        with storage_admin.connection(value.profile) as con:
            sql_records.insert(con, readmit_schema.ADMISSION, readmit_schema.validate_record(readmit_schema.ADMISSION, admitted))
            con.commit()
            epoch = db.knowledge_version(con)["library_epoch"]
    assert_current17_runtime()
    value.store.update({"local_epoch": epoch})
    assert value.store.read()["source_execution"]["local_epoch"] == 1
    value.cloud = cloud
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sql_rows(path):
    """Typed complete operational/knowledge rows, including private BLOB bytes."""
    # A live fixture may have committed pages in WAL. Ordinary read-only
    # SQLite observes them; immutable would incorrectly inspect an old main
    # image and is therefore reserved for already materialized backups.
    con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        projections = {**preservation.PROJECTIONS_V17,
            **{name: tuple(columns.split()) for name, columns in preservation._KNOWLEDGE.items()}}
        values = {}
        for table, columns in sorted(projections.items()):
            names = ",".join('"' + name + '"' for name in columns)
            values[table] = sorted(con.execute('SELECT ' + names + ' FROM main."' + table + '"').fetchall(), key=repr)
        return values
    finally:
        con.close()


def source_files(value):
    """Expected actual source set from the frozen namespaces and owned State."""
    roots = {"DATA": value.profile.database.parent, "CHECKPOINTS": value.profile.checkpoints}
    selected = {}
    for root_kind, root in roots.items():
        for namespace in (authority.DIRECTORY, continuity.DIRECTORY):
            directory = root / namespace
            if directory.exists():
                for path in directory.rglob("*"):
                    if path.is_file():
                        selected[root_kind, path.relative_to(root).as_posix()] = path.read_bytes()
        anchors = (authority.INTENT_NAME, authority.DB_HEAD_NAME, continuity.INTENT_NAME,
            continuity.DB_HEAD_NAME, continuity.LOCK_NAME) if root_kind == "DATA" else (
            authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME, continuity.SEAL_NAME, continuity.CHECKPOINT_HEAD_NAME)
        for name in anchors:
            path = root / name
            if path.exists():
                selected[root_kind, name] = path.read_bytes()
    state_path = roots["DATA"] / sync_state._STATE
    if state_path.exists():
        selected["DATA", sync_state._STATE] = state_path.read_bytes()
        state = value.store.read()
        for name in ("pending_publication", "recovery_attempt"):
            item = state.get(name)
            if item is not None:
                key = sync_state._OUTBOX + "/" + item["publication_id"] + ".json"
                selected["DATA", key] = (roots["DATA"] / key).read_bytes()
    return selected


def produce(module, value, operation_id=None):
    state = value.store.read()
    root = value.root if state.get("source_execution") is not None else None
    return module.create(profile=value.profile, store=value.store,
        operation_id=operation_id or str(uuid.uuid4()), root_job_id=root)


def documents(result):
    manifest, certificate = json.loads(result.manifest_json), json.loads(result.certificate_json)
    assert canonical(manifest).decode() == result.manifest_json
    assert canonical(certificate).decode() == result.certificate_json
    assert manifest["certificate"] == {"format_version": 2, "sha256": sha(result.certificate_json.encode()),
                                      "byte_size": len(result.certificate_json.encode())}
    assert manifest["file_set_sha256"] == sha(canonical(manifest["files"]))
    assert manifest["manifest_sha256"] == sha(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"}))
    assert manifest["permanent_witnesses"] == certificate["permanent_witnesses"]
    assert manifest["knowledge_version"] == certificate["knowledge_version"]
    return manifest, certificate


def registered_image(value, manifest):
    binding = manifest["database_backup"]
    catalog = backups._catalog(value.profile) / (binding["backup_id"] + ".json")
    record, _ = backups._record(catalog)
    assert record["state"] == "ready"
    descriptor, descriptor_sha = backups._verify_ready(record, value.profile)
    assert descriptor_sha == binding["descriptor_sha256"]
    assert descriptor["database_file"]["sha256"] == binding["image_sha256"]
    return Path(record["destination"]), descriptor


def assert_complete_original_set(value, manifest, before):
    keys = [(entry["root_kind"], entry["relative_key"]) for entry in manifest["files"]]
    assert keys == sorted(set(keys))
    assert set(keys) == set(before)
    assert source_files(value) == before
    for entry in manifest["files"]:
        raw = before[entry["root_kind"], entry["relative_key"]]
        assert entry["sha256"] == sha(raw)
        assert entry["byte_size"] == len(raw)
        assert entry["role"]


def bundle_path(module, value, manifest):
    return value.profile.database.parent / module.DIRECTORY / manifest["bundle_id"]


def assert_real_copies(module, value, manifest, before):
    copies = bundle_path(module, value, manifest) / "files"
    # The storage names are opaque. Compare the complete raw-byte multiset,
    # including identical double-head files, without mirroring its name hash.
    assert Counter(path.read_bytes() for path in copies.iterdir()) == Counter(before.values())


class SimulatedInterruption(RuntimeError):
    pass


def test_actual_registered_bundle_keeps_paid_unknown_private_sql_and_state5_originals(bundle_module, bundle_env):
    value = bundle_env
    before_files, before_sql = source_files(value), sql_rows(value.profile.database)
    result = produce(bundle_module, value)
    manifest, certificate = documents(result)
    image, descriptor = registered_image(value, manifest)
    assert sql_rows(image) == before_sql == sql_rows(value.profile.database)
    assert descriptor["format"] == 1 and descriptor["schema_version"] == 17
    assert_complete_original_set(value, manifest, before_files)
    assert_real_copies(bundle_module, value, manifest, before_files)
    assert certificate["checkpoint"]["state_format_version"] == 5
    assert certificate["state_authority"]["source_authority"]["origin"] == authority.record_ref(value.origin)
    assert certificate["scope"] == "local_capture_comparison" and certificate["continuity"] == "not_established"
    assert PRIVATE not in result.manifest_json and PRIVATE not in result.certificate_json
    public = json.dumps(result.public(), ensure_ascii=False)
    assert PRIVATE not in public and "synthetic.example.test" not in public
    assert str(value.profile.database.parent.parent) not in public
    assert "files" not in result.public() and "tables" not in result.public()


def test_both_actual_permanent_facilities_all_four_sql_tables_and_unpublished_part_are_saved(bundle_module, combined_env):
    value = combined_env
    part = home_files.ledger(value.store) / "controls" / (".sync-" + uuid.uuid4().hex + ".part")
    home_files.write(part, b"unpublished private bytes\x00\xff", raw=True)
    before_files, before_sql = source_files(value), sql_rows(value.profile.database)
    result = produce(bundle_module, value)
    manifest, certificate = documents(result)
    image, _descriptor = registered_image(value, manifest)
    assert sql_rows(image) == before_sql
    assert_complete_original_set(value, manifest, before_files)
    assert_real_copies(bundle_module, value, manifest, before_files)
    assert all(item["presence"] == "present" for item in certificate["permanent_witnesses"].values())
    assert {row["name"]: row["rows"] for row in certificate["tables"] if row["name"] in readmit_schema.COLUMNS} == dict.fromkeys(readmit_schema.COLUMNS, 1)
    assert any(entry["relative_key"].endswith(part.name) for entry in manifest["files"])
    assert certificate["state_authority"]["source_core_sha256"] == authority.digest(value.core)


def test_current_and_independent_recovery_outboxes_are_both_original_bytes(bundle_module, home_environment):
    value = home_environment
    recovery = install_outbox(value)
    value.store.update({"pending_publication": None, "recovery_attempt": {
        "generation": 1, "publication_id": recovery["publication_id"],
        "version": {"library_id": value.library, "library_epoch": 1, "kb_revision": 0},
        "stream": {"library_id": value.store.read()["binding"]["target_library_id"], "library_epoch": 1}}})
    current = install_outbox(value)
    prepare17(value)
    before = source_files(value)
    result = produce(bundle_module, value)
    manifest, certificate = documents(result)
    assert_complete_original_set(value, manifest, before)
    assert {item["publication_id"] for item in certificate["checkpoint"]["outboxes"]} == {recovery["publication_id"], current["publication_id"]}


def test_operation_replay_keeps_original_uuid_certificate_manifest_and_single_backup(bundle_module, bundle_env):
    value, operation = bundle_env, str(uuid.uuid4())
    first = produce(bundle_module, value, operation)
    before = source_files(value)
    second = produce(bundle_module, value, operation)
    assert second.manifest_json == first.manifest_json
    assert second.certificate_json == first.certificate_json
    assert documents(second)[0]["bundle_id"] == documents(first)[0]["bundle_id"]
    assert source_files(value) == before
    catalog_entries = list(backups._catalog(value.profile).glob("*.json"))
    assert len(catalog_entries) == 1


def test_unrelated_personal_file_is_not_absorbed_or_modified(bundle_module, bundle_env):
    value = bundle_env
    unrelated = value.profile.database.parent / "unrelated-personal-note.txt"
    unrelated.write_bytes(b"personal fixture\x00\xff")
    before = source_files(value)
    result = produce(bundle_module, value)
    manifest, _certificate = documents(result)
    assert_complete_original_set(value, manifest, before)
    assert all(entry["relative_key"] != unrelated.name for entry in manifest["files"])
    assert unrelated.read_bytes() == b"personal fixture\x00\xff"


def test_released16_is_rejected_without_initializing_a_bundle(bundle_module, released16_home_environment):
    value = released16_home_environment
    before = source_files(value)
    with pytest.raises(ValueError):
        produce(bundle_module, value)
    assert source_files(value) == before


def test_dto_state_cannot_substitute_actual_store_or_create_artifacts(bundle_module, bundle_env):
    value = bundle_env
    before = source_files(value)
    with pytest.raises(ValueError):
        bundle_module.create(profile=value.profile, store=value.store.read(), operation_id=str(uuid.uuid4()), root_job_id=value.root)
    assert source_files(value) == before


def test_partial_facility_is_rejected_and_never_repaired(bundle_module, bundle_env):
    value = bundle_env
    missing = value.profile.checkpoints / authority.CHECKPOINT_HEAD_NAME
    missing.unlink()
    before = source_files(value)
    with pytest.raises(ValueError):
        produce(bundle_module, value)
    assert source_files(value) == before
    assert not missing.exists()


def test_existing_state_shared_sql_owner_cannot_upgrade_into_the_maintenance_producer(bundle_module, bundle_env):
    value = bundle_env
    before = source_files(value)
    with value.store.reference_operation():
        con = sqlite3.connect(value.profile.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO meta(key,value) VALUES('uncommitted-private-bundle',?)", (b"not-committed",))
            with pytest.raises(ValueError):
                produce(bundle_module, value)
        finally:
            con.rollback()
            con.close()
    assert source_files(value) == before
    assert not backups._catalog(value.profile).exists()


def test_foreign_sql_writer_only_committed_image_is_preserved_and_pending_blob_untouched(bundle_module, bundle_env):
    value = bundle_env
    before = sql_rows(value.profile.database)
    writer = sqlite3.connect(value.profile.database, timeout=0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO meta(key,value) VALUES('outside-uncommitted-private',?)", (b"outside-pending\x00\xff",))
        result = produce(bundle_module, value)
        manifest, _certificate = documents(result)
        image, _descriptor = registered_image(value, manifest)
        assert sql_rows(image) == before == sql_rows(value.profile.database)
        assert writer.in_transaction
        assert writer.execute("SELECT value FROM meta WHERE key='outside-uncommitted-private'").fetchone() == (b"outside-pending\x00\xff",)
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize("stage", ["first_copy", "certificate", "manifest", "receipt"])
def test_interruption_reuses_same_operation_frozen_bytes_and_single_backup(bundle_module, bundle_env, monkeypatch, stage):
    value, operation = bundle_env, str(uuid.uuid4())
    original_copy, original_json = bundle_module._copy_source, bundle_module._write_json
    before = source_files(value)

    def interrupted_copy(item, destination, name, store, started):
        original_copy(item, destination, name, store, started)
        if stage == "first_copy":
            raise SimulatedInterruption(stage)

    def interrupted_json(directory, name, document):
        if ((stage == "certificate" and name == "certificate.json")
                or (stage == "manifest" and name == "manifest.json")
                or (stage == "receipt" and name.endswith(".receipt.json"))):
            raise SimulatedInterruption(stage)
        return original_json(directory, name, document)

    with monkeypatch.context() as scoped:
        scoped.setattr(bundle_module, "_copy_source", interrupted_copy)
        scoped.setattr(bundle_module, "_write_json", interrupted_json)
        with pytest.raises(SimulatedInterruption):
            produce(bundle_module, value, operation)
    reservation_path = value.profile.database.parent / bundle_module.OPERATIONS / (operation + ".json")
    reservation_bytes = reservation_path.read_bytes()
    reservation = json.loads(reservation_bytes)
    assert not reservation_path.with_name(operation + ".receipt.json").exists()
    result = produce(bundle_module, value, operation)
    manifest, _certificate = documents(result)
    assert reservation_path.read_bytes() == reservation_bytes
    assert manifest["operation_id"] == operation and manifest["bundle_id"] == reservation["bundle_id"]
    assert manifest["database_backup"]["backup_id"] == reservation["backup_id"]
    assert source_files(value) == before
    assert_real_copies(bundle_module, value, manifest, before)
    assert len(list(backups._catalog(value.profile).glob("*.json"))) == 1


def append_actual_origin(value, core):
    record = {**deepcopy(value.origin), "core": deepcopy(core), "core_sha256": authority.digest(core),
        "record_id": str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + authority.digest(core)))}
    # Actual ownership, reserved/committed journal append and immutable record
    # bytes; this historical fixture never yields an execution/admission ticket.
    home_files.seed(value.store, [("origins", record)])
    return record


def test_successful_original_bundle_replay_keeps_later_legal_heads(bundle_module, bundle_env):
    value, operation = bundle_env, str(uuid.uuid4())
    first = produce(bundle_module, value, operation)
    original_heads = {key: raw for key, raw in source_files(value).items() if "head.json" in key[1]}
    newer_core = {**deepcopy(value.core), "execution_generation": 2}
    append_actual_origin(value, newer_core)
    current = source_files(value)
    assert {key: raw for key, raw in current.items() if "head.json" in key[1]} != original_heads
    second = produce(bundle_module, value, operation)
    assert second.replayed and second.manifest_json == first.manifest_json
    assert second.certificate_json == first.certificate_json
    assert source_files(value) == current


def test_completed_original_root_replays_when_current_state_uses_another_actual_root(bundle_module, historical17_paid_environment):
    value = historical17_paid_environment
    value.paid, value.unknown = value.historical_paid_unknown
    value.core = install_core(value)
    value.origin = seed_origin(value, value.core)
    historical_state(value, {"source_authority": authority_state(value.core, origin=authority.record_ref(value.origin))})
    later_root = tasks.submit_sync(profile=value.profile)
    with storage_admin.connection(value.profile) as con:
        later_job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (later_root,)).fetchone())
    prepare17(value)
    operation = str(uuid.uuid4())
    first = produce(bundle_module, value, operation)
    later_core = {**deepcopy(value.core), "root_job_id": later_root,
        "continuation_id": later_job["continuation_id"], "execution_generation": later_job["execution_generation"]}
    later_origin = append_actual_origin(value, later_core)
    historical_state(value, {"source_execution": later_core,
        "source_authority": authority_state(later_core, origin=authority.record_ref(later_origin))})
    current = source_files(value)
    second = bundle_module.create(profile=value.profile, store=value.store, operation_id=operation, root_job_id=value.root)
    assert second.replayed and second.manifest_json == first.manifest_json
    assert second.certificate_json == first.certificate_json
    assert source_files(value) == current
    assert value.store.read()["source_execution"]["root_job_id"] == later_root


def test_partial_original_selection_cannot_absorb_a_later_valid_head(bundle_module, bundle_env, monkeypatch):
    value, operation = bundle_env, str(uuid.uuid4())
    original = bundle_module._copy_source

    def interrupted(item, destination, name, store, started):
        original(item, destination, name, store, started)
        raise SimulatedInterruption("after real copy")

    with monkeypatch.context() as scoped:
        scoped.setattr(bundle_module, "_copy_source", interrupted)
        with pytest.raises(SimulatedInterruption):
            produce(bundle_module, value, operation)
    reservation_path = value.profile.database.parent / bundle_module.OPERATIONS / (operation + ".json")
    reservation = reservation_path.read_bytes()
    append_actual_origin(value, {**deepcopy(value.core), "execution_generation": 2})
    current = source_files(value)
    with pytest.raises(ValueError):
        produce(bundle_module, value, operation)
    assert reservation_path.read_bytes() == reservation
    assert source_files(value) == current
    assert not reservation_path.with_name(operation + ".receipt.json").exists()


@pytest.mark.parametrize("damage", ["certificate", "manifest", "copy", "extra_copy", "backup"])
def test_actual_immutable_artifact_damage_is_rejected_without_replacing_live_sources(bundle_module, bundle_env, damage):
    value, operation = bundle_env, str(uuid.uuid4())
    first = produce(bundle_module, value, operation)
    manifest, _certificate = documents(first)
    directory = bundle_path(bundle_module, value, manifest)
    before = source_files(value)
    if damage in {"certificate", "manifest"}:
        path = directory / (damage + ".json")
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "copy":
        path = next((directory / "files").iterdir())
        path.write_bytes(path.read_bytes() + b"damaged")
    elif damage == "extra_copy":
        (directory / "files" / "unregistered.bin").write_bytes(b"unknown fixture")
    else:
        image, _descriptor = registered_image(value, manifest)
        with image.open("ab") as stream:
            stream.write(b"damaged image")
    with pytest.raises(ValueError):
        produce(bundle_module, value, operation)
    assert source_files(value) == before


def test_same_size_original_state_mutation_after_copy_never_publishes_receipt(bundle_module, bundle_env, monkeypatch):
    value, operation = bundle_env, str(uuid.uuid4())
    original_copy = bundle_module._copy_source
    changed = []

    def change_after_copy(item, destination, name, store, started):
        original_copy(item, destination, name, store, started)
        entry = item[0]
        if entry["role"] == "checkpoint_state" and not changed:
            path = value.profile.database.parent / sync_state._STATE
            before = path.stat()
            raw = path.read_bytes()
            mutated = raw.replace(b'"local_epoch":1', b'"local_epoch":2', 1)
            assert mutated != raw and len(mutated) == len(raw)
            path.write_bytes(mutated)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert path.stat().st_ino == before.st_ino and path.stat().st_size == before.st_size
            changed.append(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(bundle_module, "_copy_source", change_after_copy)
        with pytest.raises(ValueError):
            produce(bundle_module, value, operation)
    assert changed
    receipt = value.profile.database.parent / bundle_module.OPERATIONS / (operation + ".receipt.json")
    assert not receipt.exists()
