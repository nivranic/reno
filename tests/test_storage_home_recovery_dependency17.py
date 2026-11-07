"""Actual original file/image closure; public SQL never grants a producer."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import db, home_recovery17 as recovery, home_recovery_source as sources
from reno import home_recovery_source17 as sources17, home_recovery_schema as schema
from reno import source_preservation_bundle as bundles, storage_admin, storage_backups as backups
from reno import storage_home_recovery_dependency17 as reader, storage_home_recovery_roots as old
from reno import storage_lifecycle as lifecycle, sync_source_readmit_schema as readmit, sync_state
from tests.test_home_recovery_home import env as legacy_env
from tests.test_home_recovery_migrations import root_ids
from tests.test_storage_home_roots import outbox
from tests.test_home_recovery17 import old16_environment, actual_home16, cloud_environment, dual_env


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def env(legacy_env):
    profile, _old_source, library, node = legacy_env
    raw = b"synthetic-managed-source"
    key = "sync/home_video/" + sha(raw) + ".mp4"
    managed = profile.media / key
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(raw)
    if os.name != "nt":
        managed.chmod(0o600)
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE video_asset SET files_json=?,content_sha256=?,audio_sha256=NULL WHERE video_id='home_video'",
            (encoded({"video": profile.media.name + "/" + key}).decode(), sha(raw)))
        con.commit()
    source_id = str(uuid.uuid4())
    sources.create_checkpoint(profile=profile, source_id=source_id)
    with storage_admin.connection(profile) as con:
        readmit.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    return SimpleNamespace(profile=profile, source_id=source_id, library=library, node=node, key=key)


def restored(value):
    operation_id = str(uuid.uuid4())
    preview = recovery.preview(profile=value.profile, source_id=value.source_id)
    recovery.apply(profile=value.profile, source_id=value.source_id, expected_digest=preview["digest"], operation_id=operation_id)
    with sqlite3.connect(value.profile.database) as con:
        con.row_factory = sqlite3.Row
        row = dict(con.execute('SELECT ' + ','.join(old.COLUMNS) + ' FROM main."' + schema.TABLE + '" WHERE operation_id=?',
            (operation_id,)).fetchone())
        result = con.execute('SELECT result_sha256 FROM main."' + schema.TABLE + '" WHERE operation_id=?', (operation_id,)).fetchone()[0]
    return row, result


@contextmanager
def capture(value, row, **kwargs):
    with lifecycle.shared(value.profile):
        with reader.capture_locked([row], profile=value.profile, root_ids=root_ids(value.profile),
                member_id="mem_home17_files", library_id=value.library, node_id=value.node,
                schema_version=17, **kwargs) as observed:
            yield observed


def closed(observed):
    return any(binding.section == "home17_dependency_files" and binding.state == "closed"
        for binding in observed.inputs.source_bindings)


def test_original_files_close_twelve_columns_without_any_state_worker_or_producer_lock(env, monkeypatch):
    row, result = restored(env)
    def forbidden(*_args, **_kwargs):
        pytest.fail("dependency reader must not enter an execution/State producer")
    monkeypatch.setattr(recovery, "resolve_producer_locked", forbidden)
    monkeypatch.setattr(bundles, "open_completed_locked", forbidden)
    monkeypatch.setattr(sources, "open_source", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    before = env.profile.database.read_bytes()
    with capture(env, row, provenance={row["operation_id"]: result}) as observed:
        assert closed(observed)
        assert not observed.inputs.blockers
        assert env.key in {ref.storage_key for ref in observed.inputs.references}
        observed.verify_source()
        assert type(observed) is old.RecoveryRootCapture
    assert env.profile.database.read_bytes() == before


def test_retired_source_declaration_intent_keeps_media_from_original_image(env):
    row, _ = restored(env)
    path = sources._source_path(env.profile, env.source_id)
    path.unlink()
    path.with_name(env.source_id + ".intent.json").unlink()
    with capture(env, row) as observed:
        assert closed(observed)
        assert not observed.inputs.blockers
        assert env.key in {ref.storage_key for ref in observed.inputs.references}


def test_original_source_image_loss_has_precise_media_unknown_and_keeps_public_backup_edges(env):
    row, _ = restored(env)
    backup_id = str(uuid.uuid5(uuid.UUID(env.source_id), "reno.home.knowledge.checkpoint.v1"))
    record, _ = backups._record(backups._catalog(env.profile) / (backup_id + ".json"))
    Path(record["destination"]).unlink()
    with capture(env, row) as observed:
        assert {block.namespace for block in observed.inputs.blockers} == {"sync_media"}
        assert {block.code for block in observed.inputs.blockers} == {"HOME17_SOURCE_IMAGE_UNPROVEN"}
        assert old.backup_ids([row], schema_version=17) == {backup_id, row["protection_backup_id"]}


@pytest.mark.parametrize("field", ["source_binding_sha256", "source_sha256", "protection_descriptor_sha256", "protection_image_sha256"])
def test_public_projection_file_binding_change_is_not_closed(env, field):
    row, _ = restored(env)
    row[field] = "f" * 64
    with capture(env, row) as observed:
        assert not closed(observed)


@pytest.mark.parametrize("field", ["operation_id", "library_id", "node_id", "source_kind", "source_id",
    "protection_backup_id", "checkpoint_json", "checkpoint_sha256"])
def test_remaining_public_columns_cannot_change_without_original_material_binding(env, field):
    row, _ = restored(env)
    if field == "source_kind":
        row[field] = "registered_backup"
    elif field == "checkpoint_json":
        wrapper = json.loads(row[field])
        wrapper["certificate"]["preserved_sha256"] = "f" * 64
        row[field] = encoded(wrapper).decode()
        row["checkpoint_sha256"] = sha(encoded(wrapper))
    elif field == "checkpoint_sha256":
        row[field] = "f" * 64
    else:
        row[field] = str(uuid.uuid4())
    try:
        with capture(env, row) as observed:
            assert not closed(observed)
    except old.HomeRecoveryRootError:
        # Canonical shape/derived UUID/SHA violations are rejected even before
        # file capture; they cannot become a closed dependency observation.
        pass


def test_same_image_provenance_mismatch_never_closes_original_producer_materials(env):
    row, _ = restored(env)
    with capture(env, row, provenance={row["operation_id"]: "f" * 64}) as observed:
        assert not closed(observed)
        assert env.key in {ref.storage_key for ref in observed.inputs.references}


def test_same_image_provenance_missing_operation_keeps_dependencies_without_closure(env):
    row, _ = restored(env)
    with capture(env, row, provenance={}) as observed:
        assert not closed(observed)
        assert env.key in {ref.storage_key for ref in observed.inputs.references}


@pytest.mark.parametrize("suffix", ["request", "intent", "proposal", "receipt"])
def test_missing_producer_metadata_keeps_all_observable_media_roots(env, suffix):
    row, _ = restored(env)
    path = env.profile.database.parent / reader.DIRECTORY / (row["operation_id"] + "." + suffix + ".json")
    path.unlink()
    with capture(env, row) as observed:
        assert not closed(observed)
        assert not observed.inputs.blockers
        assert env.key in {ref.storage_key for ref in observed.inputs.references}


def test_required_missing_producer_file_appearance_changes_original_final_cas(env):
    row, _ = restored(env)
    path = env.profile.database.parent / reader.DIRECTORY / (row["operation_id"] + ".request.json")
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(old.HomeRecoveryRootError, match="HOME17_DEPENDENCY_CHANGED"):
        with capture(env, row) as observed:
            assert not closed(observed)
            assert not observed.inputs.blockers
            path.write_bytes(original)
            if os.name != "nt":
                path.chmod(0o600)
            observed.verify_source()


def test_coordinated_typed_row_file_claims_are_observations_and_not_sql_authority(env):
    row, result = restored(env)
    directory = env.profile.database.parent / reader.DIRECTORY
    proposal_path = directory / (row["operation_id"] + ".proposal.json")
    receipt_path = directory / (row["operation_id"] + ".receipt.json")
    proposal, receipt = json.loads(proposal_path.read_bytes()), json.loads(receipt_path.read_bytes())
    proposal["row_sha256"] = receipt["row_sha256"] = "f" * 64
    proposal_path.write_bytes(encoded(proposal))
    receipt["proposal_sha256"] = sha(proposal_path.read_bytes())
    receipt_path.write_bytes(encoded(receipt))
    with capture(env, row, provenance={row["operation_id"]: result}) as observed:
        # Agreement of the file declarations is enough for dependency closure,
        # never a substitute for a sealed actual35 SQL execution producer.
        assert closed(observed)
        assert type(observed) is old.RecoveryRootCapture
        assert not hasattr(observed, "before") and not hasattr(observed, "checkpoint_wrapper")


def test_capture_keeps_copy_directory_namespace_to_final_cas(env):
    row, _ = restored(env)
    wrapper = json.loads(row["checkpoint_json"])
    copies = env.profile.database.parent / bundles.DIRECTORY / wrapper["bundle"]["bundle_id"] / "files"
    with pytest.raises(old.HomeRecoveryRootError, match="HOME17_DEPENDENCY_CHANGED"):
        with capture(env, row) as observed:
            assert closed(observed)
            path = copies / "unexpected-copy"
            path.write_bytes(b"new")
            observed.verify_source()


def test_original_outbox_copy_survives_live_outbox_retirement(env):
    store = sync_state.StateStore(env.profile.database.parent, profile=env.profile)
    store.initialize({"home_library_id": env.library, "home_node_id": env.node,
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()), "target_url": "https://synthetic.example.test"}, 1)
    path, metadata, envelope = outbox((env.profile, {"library_id": env.library, "node_id": env.node}), video="BV1OriginalCopy", legacy=True)
    store.update({"pending_publication": metadata})
    row, _ = restored(env)
    path.unlink()
    with capture(env, row) as observed:
        assert closed(observed)
        assert not observed.inputs.blockers
        media = envelope["snapshot"]["videos"][0]["media"]
        assert f"sync/{media['video_id']}/{media['sha256']}.mp4" in {ref.storage_key for ref in observed.inputs.references}


def test_registered_public_image_reader_denies_private_home_and_knowledge_cells(env, monkeypatch):
    row, _ = restored(env)
    original = reader._Image.__init__
    def observing(self, con, descriptor):
        original(self, con, descriptor)
        for sql in ('SELECT frontier_json FROM main."' + schema.TABLE + '"',
                'SELECT request_json FROM main."' + schema.TABLE + '"', 'SELECT claim FROM main.knowledge_atom'):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute(sql).fetchall()
    monkeypatch.setattr(reader._Image, "__init__", observing)
    with capture(env, row) as observed:
        assert closed(observed)


@pytest.mark.parametrize("schema_version", [16, True, "17", 18])
def test_actual17_explicit_parameter_gate_rejects_other_formats_before_read(env, schema_version):
    with lifecycle.shared(env.profile):
        with pytest.raises(old.HomeRecoveryRootError, match="SCHEMA_REQUIRED"):
            with reader.capture_locked([], profile=env.profile, root_ids=root_ids(env.profile), member_id="mem_home17_files",
                    library_id=env.library, schema_version=schema_version):
                pytest.fail("schema gate was bypassed")


def test_actual17_source_companion_is_pinned_and_media_remains_when_declaration_retires(env):
    source_id = str(uuid.uuid4())
    sources17.create_checkpoint(profile=env.profile, source_id=source_id)
    env.source_id = source_id
    row, _ = restored(env)
    source = sources._source_path(env.profile, source_id)
    source.unlink()
    source.with_name(source_id + ".intent.json").unlink()
    with capture(env, row) as observed:
        assert closed(observed)
        assert not observed.inputs.blockers
        assert env.key in {ref.storage_key for ref in observed.inputs.references}


def test_source17_original_sql_companion_stays_pinned_to_final_cas(env):
    source_id = str(uuid.uuid4())
    sources17.create_checkpoint(profile=env.profile, source_id=source_id)
    env.source_id = source_id
    row, _ = restored(env)
    companion = sources._source_path(env.profile, source_id).with_name(source_id + ".sql17.json")
    with pytest.raises((old.HomeRecoveryRootError, reader.safe.SyncError, PermissionError)):
        with capture(env, row) as observed:
            assert closed(observed)
            companion.write_bytes(companion.read_bytes() + b" ")
            observed.verify_source()


def test_actual_state5_original_home_and_cloud_capsules_use_copies_after_live_origin_removed(dual_env, monkeypatch):
    value = dual_env
    with storage_admin.connection(value.profile) as con:
        readmit.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    value.node = value.store.read()["binding"]["home_node_id"]
    row, result = restored(value)
    origin_path = value.profile.database.parent / reader.authority.DIRECTORY / reader.authority.VERSION_DIRECTORY / "origins" / (value.origin["record_id"] + ".json")
    origin_path.unlink()
    def forbidden(*_args, **_kwargs):
        pytest.fail("historic bundle must not be reconstructed by a current live facility capture")
    monkeypatch.setattr(reader.authority_roots, "capture_locked", forbidden)
    monkeypatch.setattr(reader.cloud_roots, "capture_locked", forbidden)
    with capture(value, row, provenance={row["operation_id"]: result}) as observed:
        assert closed(observed)
        assert not observed.inputs.blockers


def test_original_outbox_copy_damage_has_precise_media_and_report_unknown(env):
    store = sync_state.StateStore(env.profile.database.parent, profile=env.profile)
    store.initialize({"home_library_id": env.library, "home_node_id": env.node,
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()), "target_url": "https://synthetic.example.test"}, 1)
    _path, metadata, _envelope = outbox((env.profile, {"library_id": env.library, "node_id": env.node}), legacy=True)
    store.update({"pending_publication": metadata})
    row, _ = restored(env)
    wrapper = json.loads(row["checkpoint_json"])
    root = env.profile.database.parent / bundles.DIRECTORY / wrapper["bundle"]["bundle_id"]
    manifest = json.loads((root / "manifest.json").read_bytes())
    entry = next(item for item in manifest["files"] if item["role"] == "checkpoint_outbox")
    copied = root / "files" / bundles._copy_name(entry)
    copied.write_bytes(b"damaged-original-outbox")
    with capture(env, row) as observed:
        assert {block.namespace for block in observed.inputs.blockers} == {"sync_media", "sync_report"}
        assert {block.code for block in observed.inputs.blockers} == {"HOME17_BUNDLE_DEPENDENCY_UNPROVEN"}


def test_mutable_catalog_change_during_capture_fails_original_final_cas(env):
    row, _ = restored(env)
    path = backups._catalog(env.profile) / (row["protection_backup_id"] + ".json")
    with pytest.raises(old.HomeRecoveryRootError, match="HOME17_DEPENDENCY_CHANGED"):
        with capture(env, row) as observed:
            assert closed(observed)
            # Mutable catalog reads are released; the caller can observe a
            # writer replacement and must then reject its original CAS.
            replacement = path.with_name("synthetic-catalog-replacement")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
            observed.verify_source()


def test_read_uses_original_source_image_and_never_newer_live_media(env):
    row, _ = restored(env)
    changed = "sync/home_video/" + "f" * 64 + ".mp4"
    with lifecycle.shared(env.profile), sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE video_asset SET files_json=? WHERE video_id='home_video'",
            (encoded({"video": env.profile.media.name + "/" + changed}).decode(),))
        con.commit()
    with capture(env, row) as observed:
        refs = {ref.storage_key for ref in observed.inputs.references}
        assert closed(observed)
        assert env.key in refs and changed not in refs


def test_cancellation_original_exception_propagates(env):
    class UserCancelled(ValueError):
        pass
    error = UserCancelled("actual cancellation")
    def cancelled():
        raise error
    with lifecycle.shared(env.profile):
        with pytest.raises(UserCancelled) as caught:
            with reader.capture_locked([], profile=env.profile, root_ids=root_ids(env.profile), member_id="mem_home17_files",
                    library_id=env.library, schema_version=17, check_cancelled=cancelled):
                pass
    assert caught.value is error
