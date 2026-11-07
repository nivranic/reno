"""Historical capsule outboxes after the actual worker rejects an old epoch.

The original worker, pending CAS, old-epoch publication branch, bundle writer,
restore, SQL provenance, copied roots and runtime consumers remain production.
Only the existing mapped fixture's physical source/model seams are synthetic.
"""
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
import uuid

import pytest

from reno import home_recovery17 as restore, home_recovery_source17 as sources17
from reno import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
from reno import llm, source_preservation_bundle as bundles, storage_admin, sync_access
from reno import storage_home_recovery_dependency17 as recovery_dependencies
from reno import storage_home_recovery_roots as recovery_roots, storage_home_roots as home_roots
from reno import storage_legacy_pending_dependencies17 as dependencies
from reno import storage_legacy_pending_state17 as pending, storage_lifecycle as lifecycle
from reno import sync_agent as agent, sync_state
from tests import test_sync_source_legacy_pending_intake17 as actual_pending
from tests.test_home_legacy_pending_dependencies17 import (
    mapped_cycle, mapped_completed, observed_dependencies, original_dependency_files,
)
from tests.test_storage_legacy_pending_state17 import original_inputs
from tests.test_home_recovery_migrations import root_ids


@pytest.fixture
def historical_completed(mapped_completed, monkeypatch):
    value, intent, receipt, path, marker = mapped_completed
    original_core = deepcopy(value.core)
    # Acquire/claim/renew/release belong to the real worker's lifetime. Compare
    # its SQL only within this scope, across the actual publish operation.
    with actual_pending.original_worker(value), value.store.exclusive():
        state = value.store.read()
        metadata = state["pending_publication"]
        assert metadata == value.original_pending_metadata
        assert state["recovery_attempt"] is None
        envelope = value.store.read_publication(metadata)
        recovery = state["recovery"]
        assert recovery["state"] == "running"
        assert envelope["recovery_generation"] == recovery["generation"]
        assert envelope["recovery_stream"] == recovery["stream"]
        assert envelope["snapshot"]["version"]["library_epoch"] < state["local_epoch"]
        assert value.store.publication_no_resend_request(metadata["publication_id"], allow_incomplete=True) is None
        before = original_inputs(value, path, marker)
        original_files = original_dependency_files(value)
        writes = []
        original_write = sync_state._write

        def write(directory, name, data, **kwargs):
            if directory.path == value.store.data_dir and name == sync_state._STATE:
                writes.append(bytes(data))
            return original_write(directory, name, data, **kwargs)

        def forbidden(*args, **kwargs):
            pytest.fail("The actual old-epoch publication branch attempted HTTP or a model")

        with monkeypatch.context() as scoped:
            scoped.setattr(value.client, "_request", forbidden)
            scoped.setattr(llm, "chat", forbidden)
            scoped.setattr(llm, "vision", forbidden)
            scoped.setattr(sync_state, "_write", write)
            cycle = agent._Cycle(value.store, value.client, value.source, agent.settings(), threading.Event())
            cycle.knowledge_generation = recovery["generation"]
            cycle.knowledge_stream = deepcopy(recovery["stream"])
            cycle.publish()
        assert len(writes) == 1
        expected = {**state, "pending_publication": None, "published_version": None,
            "published_content_sha256": None, "published_reports_sha256": None}
        assert value.store.read() == expected
        assert expected["source_execution"] == original_core
        assert original_inputs(value, path, marker)[2:] == before[2:]
        assert original_dependency_files(value) == original_files
        assert value.core == original_core
        capsule = json.loads(path.read_bytes())
        for phase in ("before", "after"):
            original = json.loads(capsule[phase + "_json"])
            assert original["pending_publication"] == metadata
            assert original["work"] == value.original_mapped_work
    return value, intent, receipt, path, marker


def create_and_check_historical_bundle(completed, monkeypatch):
    value, intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    original_files = original_dependency_files(value)
    assert value.store.read()["pending_publication"] is None
    assert value.store.read()["recovery_attempt"] is None
    result = bundles.create(profile=value.profile, store=value.store, operation_id=str(uuid.uuid4()),
        root_job_id=value.core["root_job_id"])
    assert not result.replayed
    manifest, document = json.loads(result.manifest_json), json.loads(result.certificate_json)
    assert document["checkpoint"]["outboxes"] == []
    assert not any(entry["role"] == "checkpoint_outbox" for entry in manifest["files"])
    identities = [(entry["root_kind"], entry["relative_key"]) for entry in manifest["files"]]
    assert len(identities) == len(set(identities))
    root = value.profile.database.parent / bundles.DIRECTORY / manifest["bundle_id"]
    copies = {(entry["root_kind"], entry["relative_key"]):
        (root / "files" / bundles._copy_name(entry)).read_bytes() for entry in manifest["files"]}
    assert len(list((root / "files").iterdir())) == len(manifest["files"])
    for selected, role in zip(value.pending_dependency_files, (dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX)):
        key = selected.relative_to(value.profile.database.parent).as_posix()
        rows = [entry for entry in manifest["files"]
            if (entry["root_kind"], entry["relative_key"]) == ("DATA", key)]
        assert len(rows) == 1 and rows[0]["role"] == role
        raw = copies["DATA", key]
        assert raw == selected.read_bytes()
        assert (rows[0]["sha256"], rows[0]["byte_size"]) == (hashlib.sha256(raw).hexdigest(), len(raw))
    with observed_dependencies(value) as (_original, physical):
        assert physical.summary["coverage"] == "complete" and not physical.unknown
        expected_requirements, expected_records = physical.requirements, physical.records
        physical.verify_source()
    with monkeypatch.context() as scoped:
        def forbidden(*args, **kwargs):
            pytest.fail("The copied historical dependency consumer accessed live SQL/files or wrote State")
        scoped.setattr(sqlite3, "connect", forbidden)
        scoped.setattr(pending.safe, "_Directory", forbidden)
        scoped.setattr(sync_state, "_write", forbidden)
        requirements, records, unknown = bundles._copied_pending_dependencies(
            value.profile, document, copies, entries=manifest["files"])
    assert requirements == expected_requirements and records == expected_records and unknown == ()
    assert len(requirements) == 4 and len(records) == 2
    assert {record.role for record in records} == {dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX}
    assert {(requirement.request_id, requirement.phase, requirement.role) for requirement in requirements} == {
        (intent["record"]["record_id"], phase, role) for phase in ("before", "after")
        for role in (dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX)}
    capsule = json.loads(path.read_bytes())
    for requirement in requirements:
        original = json.loads(capsule[requirement.phase + "_json"])
        assert requirement.state_sha256 == hashlib.sha256(capsule[requirement.phase + "_json"].encode()).hexdigest()
        if requirement.role == dependencies.ROLE_MAPPING_V2:
            assert requirement.expectation["mapping_id"] == original["work"]["source_mapping_id"]
            assert requirement.expectation["mapping_generation"] == original["work"]["source_mapping_generation"]
            assert requirement.expectation["inputs"] == original["work"]["source_mapping_inputs"]
        else:
            assert requirement.expectation["original"] == original["pending_publication"]
    assert original_inputs(value, path, marker) == before
    assert original_dependency_files(value) == original_files
    return manifest, document, copies


def test_actual_old_epoch_rejection_copies_historical_outbox_independently_of_current_checkpoint(
        historical_completed, monkeypatch):
    create_and_check_historical_bundle(historical_completed, monkeypatch)


def test_actual_historical_only_outbox_restores_with_exact_capsule_roots_and_runtime(
        historical_completed, monkeypatch):
    value, intent, _receipt, path, marker = historical_completed
    create_and_check_historical_bundle(historical_completed, monkeypatch)
    initial_inputs = original_inputs(value, path, marker)
    initial_state = value.store.read()
    initial_files = original_dependency_files(value)
    source_id, operation_id = str(uuid.uuid4()), str(uuid.uuid4())
    sources17.create_checkpoint(profile=value.profile, source_id=source_id)
    preview = restore.preview(profile=value.profile, source_id=source_id)
    restore.apply(profile=value.profile, source_id=source_id, expected_digest=preview["digest"], operation_id=operation_id)
    assert value.store.read() == {**initial_state, "local_epoch": initial_state["local_epoch"] + 1}
    with storage_admin.connection(value.profile) as connection:
        connection.execute("BEGIN")
        identity = sync_access.identity(connection)
        row = dict(connection.execute('SELECT ' + ','.join(recovery_roots.COLUMNS) + ' FROM main."'
            + restore_schema.TABLE + '" WHERE operation_id=?', (operation_id,)).fetchone())
        provenance = {item["operation_id"]: item["result_sha256"] for item in connection.execute(
            'SELECT operation_id,result_sha256 FROM main."' + restore_schema.TABLE + '" ORDER BY operation_id')}
    before = original_inputs(value, path, marker)
    capsule = json.loads(path.read_bytes())
    original_outbox = json.loads(value.pending_dependency_files[1].read_bytes())
    member_id = "actual_historical_only_capsule_home"
    with lifecycle.shared(value.profile):
        roots = root_ids(value.profile)
        with recovery_dependencies.capture_locked([row], profile=value.profile, root_ids=roots,
                member_id=member_id, library_id=identity["library_id"], node_id=identity["node_id"],
                schema_version=17, provenance=provenance) as observed:
            assert not observed.inputs.blockers
            assert any(binding.section == "home17_dependency_files" and binding.state == "closed"
                for binding in observed.inputs.source_bindings)
            phase_sources = set()
            for phase in ("before", "after"):
                phase_source = home_roots._hash(("legacy_pending_state", intent["record"]["record_id"] + ":" + phase))
                phase_sources.add(phase_source)
                original = json.loads(capsule[phase + "_json"])
                work = original["work"]
                assert original["pending_publication"] == value.original_pending_metadata
                video, sha = work["video_id"], work["source_mapping_inputs"]["media_sha256"]
                locator = home_roots._hash((video, sha, "source_map"))
                assert any(selector.namespace == "sync_media" and selector.root_id == roots["sync_media"]
                    and selector.member_id == member_id and selector.source_kind == "home"
                    and selector.source_id == phase_source and selector.locator == locator
                    and selector.video_id == video and selector.sha256 == sha for selector in observed.inputs.selectors)
                for item in original_outbox["snapshot"]["videos"]:
                    media = item["media"]
                    key = f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}"
                    assert any(reference.namespace == "sync_media" and reference.root_id == roots["sync_media"]
                        and reference.member_id == member_id and reference.source_kind == "home"
                        and reference.source_id == phase_source and reference.locator == home_roots._hash(key)
                        and reference.storage_key == key and reference.sha256 == media["sha256"]
                        and reference.byte_size == media["byte_size"] for reference in observed.inputs.references)
            bindings = [binding for binding in observed.inputs.source_bindings
                if binding.section == "legacy_pending_state" and binding.state == "valid"]
            assert len(bindings) == 2 and {binding.source_id for binding in bindings} == phase_sources
            observed.verify_source()
    with value.store.reference_operation():
        with runtime.capture_completed_locked(profile=value.profile, store=value.store, operation_id=operation_id,
                expected_result_sha256=provenance[operation_id]) as observed:
            assert operation_id in {entry["operation_id"] for entry in observed.chain_observation()}
            assert observed.provenance_ref()["operation_id"] == operation_id
            observed.verify_source()
    assert original_inputs(value, path, marker) == before
    assert original_inputs(value, path, marker)[2:] == initial_inputs[2:]
    assert original_dependency_files(value) == initial_files
