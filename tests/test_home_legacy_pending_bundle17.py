"""Public complete bundles from genuine offline old16 mapped capsules.

All positive State, original UUID, receipt, source map and outbox facts come
from the already verified original worker and physical local-child fixture.
The bundle producer and copied dependency consumer remain production code.
"""
import hashlib
from copy import deepcopy
import json
import sqlite3
import uuid

import pytest

from reno import source_preservation_bundle as bundles
from reno import home_recovery17 as restore, home_recovery_source17 as sources17
from reno import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
from reno import storage_admin, sync_access
from reno import storage_home_recovery_dependency17 as recovery_dependencies
from reno import storage_home_recovery_roots as recovery_roots
from reno import storage_home_roots as home_roots
from reno import storage_lifecycle as lifecycle, sync_state
from reno import storage_legacy_pending_dependencies17 as dependencies
from reno import storage_legacy_pending_state17 as pending
from reno import storage_source_authority_roots as authority_roots
from tests.test_home_legacy_pending_dependencies17 import mapped_cycle, mapped_completed, original_dependency_files
from tests.test_storage_legacy_pending_state17 import original_inputs
from tests.test_home_recovery_migrations import root_ids


def test_actual_mapped_capsule_bundle_copies_original_dependencies(mapped_completed, monkeypatch):
    value, intent, _receipt, path, marker = mapped_completed
    before = original_inputs(value, path, marker)
    original_files = original_dependency_files(value)
    operation_id = str(uuid.uuid4())
    result = bundles.create(profile=value.profile, store=value.store, operation_id=operation_id,
        root_job_id=value.core["root_job_id"])
    assert not result.replayed
    manifest, document = json.loads(result.manifest_json), json.loads(result.certificate_json)
    assert manifest["operation_id"] == operation_id
    keys = [(entry["root_kind"], entry["relative_key"]) for entry in manifest["files"]]
    assert len(keys) == len(set(keys))
    data = value.profile.database.parent
    selected_keys = [selected.relative_to(data).as_posix() for selected in value.pending_dependency_files]
    mapping = next(entry for entry in manifest["files"] if (entry["root_kind"], entry["relative_key"]) == ("DATA", selected_keys[0]))
    outbox = [entry for entry in manifest["files"] if (entry["root_kind"], entry["relative_key"]) == ("DATA", selected_keys[1])]
    assert mapping["role"] == dependencies.ROLE_MAPPING_V2
    # The one physical current checkpoint file independently supplies each
    # original capsule image's dependency proof; its manifest role is retained.
    assert len(outbox) == 1 and outbox[0]["role"] == "checkpoint_outbox"
    root = data / bundles.DIRECTORY / manifest["bundle_id"]
    copies = { (entry["root_kind"], entry["relative_key"]):
        (root / "files" / bundles._copy_name(entry)).read_bytes() for entry in manifest["files"]}
    for entry, selected in ((mapping, value.pending_dependency_files[0]), (outbox[0], value.pending_dependency_files[1])):
        raw = copies[entry["root_kind"], entry["relative_key"]]
        assert raw == selected.read_bytes()
        assert (entry["sha256"], entry["byte_size"]) == (hashlib.sha256(raw).hexdigest(), len(raw))
    assert len(list((root / "files").iterdir())) == len(manifest["files"])
    with lifecycle.shared(value.profile), pending.capture_locked(value.profile) as original:
        with dependencies.capture_locked(value.profile, pending_capture=original) as physical:
            expected_requirements, expected_records = physical.requirements, physical.records
            assert physical.summary["coverage"] == "complete" and not physical.unknown
            physical.verify_source()
    with monkeypatch.context() as scoped:
        def forbidden(*args, **kwargs):
            pytest.fail("Copied dependency parser opened live SQL/files or rewrote State")
        scoped.setattr(sqlite3, "connect", forbidden)
        scoped.setattr(pending.safe, "_Directory", forbidden)
        scoped.setattr(sync_state, "_write", forbidden)
        requirements, records, unknown = bundles._copied_pending_dependencies(
            value.profile, document, copies, entries=manifest["files"])
    assert requirements == expected_requirements and records == expected_records and unknown == ()
    assert len(requirements) == 4 and len(records) == 2
    assert {requirement.request_id for requirement in requirements} == {intent["record"]["record_id"]}
    assert {(requirement.phase, requirement.role) for requirement in requirements} == {
        (phase, role) for phase in ("before", "after") for role in
        (dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX)}
    capsule = json.loads(path.read_bytes())
    for requirement in requirements:
        state = json.loads(capsule[requirement.phase + "_json"])
        assert requirement.state_sha256 == hashlib.sha256(capsule[requirement.phase + "_json"].encode()).hexdigest()
        if requirement.role == dependencies.ROLE_MAPPING_V2:
            assert requirement.expectation["inputs"] == state["work"]["source_mapping_inputs"]
        else:
            assert requirement.expectation["original"] == state["pending_publication"]
    assert original_inputs(value, path, marker) == before
    assert original_dependency_files(value) == original_files


def test_actual_mapped_capsule_completed_bundle_replays_original_uuid_without_changes(mapped_completed):
    value, _intent, _receipt, path, marker = mapped_completed
    before, original_files = original_inputs(value, path, marker), original_dependency_files(value)
    operation_id = str(uuid.uuid4())
    selectors = {"profile": value.profile, "store": value.store, "operation_id": operation_id,
        "root_job_id": value.core["root_job_id"]}
    first = bundles.create(**selectors)
    manifest = json.loads(first.manifest_json)
    root = value.profile.database.parent / bundles.DIRECTORY / manifest["bundle_id"]
    originals = {str(selected.relative_to(root)): (selected.read_bytes(), selected.stat().st_mtime_ns)
        for selected in root.rglob("*") if selected.is_file()}
    second = bundles.create(**selectors)
    assert not first.replayed and second.replayed
    assert (second.manifest_json, second.certificate_json, second.receipt_sha256) == (
        first.manifest_json, first.certificate_json, first.receipt_sha256)
    assert second.public()["manifest_sha256"] == manifest["manifest_sha256"]
    assert {str(selected.relative_to(root)): (selected.read_bytes(), selected.stat().st_mtime_ns)
        for selected in root.rglob("*") if selected.is_file()} == originals
    assert original_inputs(value, path, marker) == before
    assert original_dependency_files(value) == original_files


def test_actual_mapped_capsule_full_restore_and_public_roots_close_original_dependencies(mapped_completed):
    value, intent, _receipt, path, marker = mapped_completed
    initial_inputs = original_inputs(value, path, marker)
    initial_state = value.store.read()
    initial_files = original_dependency_files(value)
    permanent_capsule = (path.read_bytes(), path.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns)
    initial_sql = initial_inputs[-2]
    initial_http = list(value.transport.seen)
    source_id, operation_id = str(uuid.uuid4()), str(uuid.uuid4())
    sources17.create_checkpoint(profile=value.profile, source_id=source_id)
    preview = restore.preview(profile=value.profile, source_id=source_id)
    restore.apply(profile=value.profile, source_id=source_id, expected_digest=preview["digest"], operation_id=operation_id)
    with storage_admin.connection(value.profile) as connection:
        connection.execute("BEGIN")
        identity = sync_access.identity(connection)
        row = dict(connection.execute('SELECT ' + ','.join(recovery_roots.COLUMNS) + ' FROM main."'
            + restore_schema.TABLE + '" WHERE operation_id=?', (operation_id,)).fetchone())
        provenance = {item["operation_id"]: item["result_sha256"] for item in connection.execute(
            'SELECT operation_id,result_sha256 FROM main."' + restore_schema.TABLE + '" ORDER BY operation_id')}
    current = value.store.read()
    assert current == {**initial_state, "local_epoch": initial_state["local_epoch"] + 1}
    before = original_inputs(value, path, marker)
    expected_work = value.original_mapped_work
    with lifecycle.shared(value.profile):
        roots = root_ids(value.profile)
        with recovery_dependencies.capture_locked([row], profile=value.profile, root_ids=roots,
                member_id="actual_mapped_bundle_home", library_id=identity["library_id"], node_id=identity["node_id"],
                schema_version=17, provenance=provenance) as observed:
            assert not observed.inputs.blockers
            assert any(binding.section == "home17_dependency_files" and binding.state == "closed"
                for binding in observed.inputs.source_bindings)
            phase_sources = {home_roots._hash(("legacy_pending_state", intent["record"]["record_id"] + ":" + phase))
                for phase in ("before", "after")}
            video, sha = expected_work["video_id"], expected_work["source_mapping_inputs"]["media_sha256"]
            locator = home_roots._hash((video, sha, "source_map"))
            original_outbox = json.loads(value.pending_dependency_files[1].read_bytes())
            for source_id in phase_sources:
                assert any(selector.namespace == "sync_media" and selector.root_id == roots["sync_media"]
                    and selector.member_id == "actual_mapped_bundle_home" and selector.source_kind == "home"
                    and selector.source_id == source_id and selector.locator == locator
                    and selector.video_id == video and selector.sha256 == sha for selector in observed.inputs.selectors)
                for item in original_outbox["snapshot"]["videos"]:
                    media = item["media"]
                    key = f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}"
                    assert any(reference.namespace == "sync_media" and reference.root_id == roots["sync_media"]
                        and reference.member_id == "actual_mapped_bundle_home" and reference.source_kind == "home"
                        and reference.source_id == source_id and reference.locator == home_roots._hash(key)
                        and reference.storage_key == key and reference.sha256 == media["sha256"]
                        and reference.byte_size == media["byte_size"] for reference in observed.inputs.references)
            pending_bindings = [binding for binding in observed.inputs.source_bindings
                if binding.section == "legacy_pending_state" and binding.state == "valid"]
            assert len(pending_bindings) == 2
            assert {binding.source_id for binding in pending_bindings} == phase_sources
            observed.verify_source()
    with value.store.reference_operation():
        with runtime.capture_completed_locked(profile=value.profile, store=value.store, operation_id=operation_id,
                expected_result_sha256=provenance[operation_id]) as observed:
            assert operation_id in {entry["operation_id"] for entry in observed.chain_observation()}
            assert observed.provenance_ref()["operation_id"] == operation_id
            observed.verify_source()
    assert original_inputs(value, path, marker) == before
    assert original_dependency_files(value) == initial_files
    assert (path.read_bytes(), path.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns) == permanent_capsule
    assert original_inputs(value, path, marker)[-2] == initial_sql
    assert original_inputs(value, path, marker)[6] == initial_inputs[6]
    assert value.transport.seen == initial_http


@pytest.fixture
def actual_bundle(mapped_completed):
    value, intent, receipt, path, marker = mapped_completed
    operation_id = str(uuid.uuid4())
    selectors = {"profile": value.profile, "store": value.store, "operation_id": operation_id,
        "root_job_id": value.core["root_job_id"]}
    result = bundles.create(**selectors)
    manifest, document = json.loads(result.manifest_json), json.loads(result.certificate_json)
    root = value.profile.database.parent / bundles.DIRECTORY / manifest["bundle_id"]
    copies = {(entry["root_kind"], entry["relative_key"]):
        (root / "files" / bundles._copy_name(entry)).read_bytes() for entry in manifest["files"]}
    return value, intent, receipt, path, marker, selectors, result, manifest, document, root, copies


@pytest.mark.parametrize("damage,code", [("missing", "UNEXPECTED_FILE"), ("changed", "CHANGED")])
def test_actual_completed_replay_rejects_missing_or_changed_original_copied_map(actual_bundle, damage, code):
    value, _intent, _receipt, path, marker, selectors, result, manifest, _document, root, _copies = actual_bundle
    before, originals = original_inputs(value, path, marker), original_dependency_files(value)
    selected = next(entry for entry in manifest["files"] if entry["role"] == dependencies.ROLE_MAPPING_V2)
    copied = root / "files" / bundles._copy_name(selected)
    if damage == "missing":
        copied.unlink()  # Deliberate isolated negative; the live original remains.
    else:
        copied.write_bytes(copied.read_bytes() + b" ")
    with pytest.raises(bundles.PreservationBundleError) as raised:
        bundles.create(**selectors)
    assert raised.value.code == "SOURCE_BUNDLE_" + code
    assert (root / "manifest.json").read_bytes() == result.manifest_json.encode("utf-8")
    assert (root / "certificate.json").read_bytes() == result.certificate_json.encode("utf-8")
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == originals


def test_actual_copied_gate_rejects_extra_undeclared_mapping_path(actual_bundle):
    value, _intent, _receipt, path, marker, _selectors, _result, manifest, document, _root, copies = actual_bundle
    before, originals = original_inputs(value, path, marker), original_dependency_files(value)
    extra = dict(copies)
    key = "sync-sources/v2/" + str(uuid.uuid4()) + ".json"
    mapping = next(entry for entry in manifest["files"] if entry["role"] == dependencies.ROLE_MAPPING_V2)
    # A detached negative map aliases actual raw bytes to a legal undeclared
    # path; neither the actual manifest nor a positive certificate is changed.
    extra["DATA", key] = copies[mapping["root_kind"], mapping["relative_key"]]
    with pytest.raises(dependencies.PendingDependencyError) as raised:
        bundles._copied_pending_dependencies(value.profile, document, extra, entries=manifest["files"])
    assert raised.value.code == "LEGACY_PENDING_DEPENDENCY17_COPIED_UNDECLARED_FILE"
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == originals


def test_actual_copied_gate_rejects_same_path_wrong_checkpoint_alias(actual_bundle):
    value, _intent, _receipt, path, marker, _selectors, _result, manifest, document, _root, copies = actual_bundle
    before, originals = original_inputs(value, path, marker), original_dependency_files(value)
    negative = deepcopy(document)
    publication_id = value.original_pending_metadata["publication_id"]
    aliases = [entry for entry in negative["checkpoint"]["outboxes"] if entry["publication_id"] == publication_id]
    assert len(aliases) == 1
    # Explicit invalid detached alias, with original physical path/raw/hash
    # untouched. No coordinated document hashes or positive receipt is forged.
    aliases[0]["semantic_sha256"] = "f" * 64
    with pytest.raises(dependencies.PendingDependencyError) as raised:
        bundles._copied_pending_dependencies(value.profile, negative, copies, entries=manifest["files"])
    assert raised.value.code == "LEGACY_PENDING_DEPENDENCY17_COPIED_CHECKPOINT_INVALID"
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == originals


def test_actual_home1_factory_keeps_old_scope_and_rejects_actual_new_dependency_roles(actual_bundle):
    value, _intent, _receipt, path, marker, _selectors, _result, manifest, document, _root, copies = actual_bundle
    before, originals = original_inputs(value, path, marker), original_dependency_files(value)
    with lifecycle.shared(value.profile):
        with authority_roots.capture_locked(value.profile, binding=value.store.read()["binding"], format_version=1) as original:
            assert not original.unknown and original.summary["format_version"] == 1
            original.verify_source()
    # The actual Home1 factory still owns only its original scope. Empty
    # dependency inputs preserve that scope; this is not a complete bundle.
    assert dependencies.copied_dependencies(value.profile, home_format_version=1,
        state_images=(), copies={}, entries=[]) == ((), (), ())
    _component, images = bundles._copied_pending_state(value.profile, document, copies, entries=manifest["files"])
    with pytest.raises(dependencies.PendingDependencyError) as raised:
        dependencies.copied_dependencies(value.profile, home_format_version=1, state_images=images,
            copies=copies, entries=manifest["files"], checkpoint_outboxes=document["checkpoint"]["outboxes"])
    assert raised.value.code == "LEGACY_PENDING_DEPENDENCY17_COPIED_HOME1_UNCOVERED"
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == originals
