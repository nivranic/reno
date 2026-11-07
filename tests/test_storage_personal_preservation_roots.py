"""Actual independent packages and bounded pinned group-root observations."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from reno import config, personal_preservation as producer
from reno import personal_preservation_contract as contract
from reno import personal_preservation_files as files
from reno import personal_preservation_source as source
from reno import storage_artifacts as artifacts, storage_lifecycle as lifecycle
from reno import storage_personal_preservation_roots as roots, storage_roots as group_roots
from reno import sync_media as safe
from tests.test_personal_preservation import library, facts, read_selection, uid
from tests.test_personal_mirror_transport import mirror_env, sender_env


def options(profile):
    with lifecycle.shared(profile):
        root_ids = {namespace: artifacts.root_identity(profile.media if namespace == "sync_media" else
            profile.checkpoints if namespace == "checkpoint" else profile.reports, profile=profile)["root_id"]
            for namespace in artifacts.NAMESPACES}
    with closing(sqlite3.connect(profile.database)) as connection:
        library_id = connection.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    node = json.loads((profile.database.parent / "node-identity.json").read_text(encoding="utf-8"))
    return {"root_ids": root_ids, "member_id": "mem_" + "c" * 64,
            "source_identity": {"library_id": library_id, "node_id": node["instance_id"]}}


def scan(profile, **changes):
    arguments = options(profile)
    arguments.update(changes)
    with lifecycle.shared(profile):
        return roots.scan_locked(profile, **arguments)


def known_media(profile):
    body = b"actual historically referenced knowledge bytes"
    digest = hashlib.sha256(body).hexdigest()
    storage_key = f"sync/remembered-video/{digest}.mp4"
    target = profile.media / storage_key
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    with lifecycle.shared(profile), closing(sqlite3.connect(profile.database)) as connection, connection:
        connection.execute("INSERT INTO video_asset(video_id,files_json,content_sha256) VALUES (?,?,?)",
            ("remembered-video", json.dumps({"video": "media/" + storage_key}), digest))
    return storage_key


def controls(profile, operation_id):
    catalog, request, target = facts(profile, operation_id)
    return catalog, request, target, read_selection(profile, operation_id)


def all_blocked(result):
    assert {value.namespace for value in result.blockers} == set(artifacts.NAMESPACES)


def test_absent_observer_is_source_zero_write_without_config_or_producer(library, monkeypatch):
    profile, _project, _local = library
    arguments = options(profile)
    before = {path: path.read_bytes() for path in (profile.database, profile.database.parent / "node-identity.json")}
    def forbidden(*_args, **_kwargs):
        pytest.fail("The read-only root observer consulted a live producer/configuration")
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "local_path", forbidden)
    monkeypatch.setattr(source, "capture_locked", forbidden)
    monkeypatch.setattr(producer, "create_locked", forbidden)
    with lifecycle.shared(profile), roots.capture_locked(profile, **arguments) as captured:
        assert not captured.scan.blockers and not captured.scan.references
        assert captured.snapshot_bytes == 0 and captured.file_count == 0
        captured.verify_source()
    assert {path: path.read_bytes() for path in before} == before
    assert not (profile.database.parent / roots.CATALOG).exists()
    assert not (profile.database.parent / roots.BUNDLES).exists()
    with pytest.raises(roots.PersonalPreservationRootError, match="CAPTURE_CLOSED"):
        captured.verify_source()


def test_exact_existing_guard_and_physical_roots_are_required(library):
    profile, _project, _local = library
    arguments = options(profile)
    with pytest.raises(lifecycle.StorageLifecycleError):
        with roots.capture_locked(profile, **arguments):
            pytest.fail("A source observer created or borrowed a guard")
    wrong = dict(arguments["root_ids"])
    wrong["sync_media"] = "f" * 64
    with lifecycle.shared(profile), pytest.raises(roots.PersonalPreservationRootError, match="ROOT_BINDING_MISMATCH"):
        with roots.capture_locked(profile, **{**arguments, "root_ids": wrong}):
            pytest.fail("The supplied namespace replaced actual physical identity")


def test_ready_package_projects_historical_sql_without_current_source_queries(library, monkeypatch):
    profile, _project, _local = library
    storage_key = known_media(profile)
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    arguments = options(profile)
    with lifecycle.shared(profile), closing(sqlite3.connect(profile.database)) as connection, connection:
        connection.execute("DELETE FROM video_asset WHERE video_id='remembered-video'")
    def forbidden(*_args, **_kwargs):
        pytest.fail("Historical package roots were supplemented from today's producer/config")
    monkeypatch.setattr(source, "capture_locked", forbidden)
    monkeypatch.setattr(producer, "verify_ready_locked", forbidden)
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "local_path", forbidden)
    with lifecycle.shared(profile), roots.capture_locked(profile, **arguments) as captured:
        result = captured.scan
        assert any(value.namespace == "sync_media" and value.storage_key == storage_key for value in result.references)
        assert {value.namespace for value in result.blockers} == {"local_report"}
        assert {value.code for value in result.blockers} == {"PERSONAL_PRESERVATION_REPORT_MANIFESTS_UNKNOWN"}
        assert all(value.source_kind == "personal_preservation" for value in result.source_bindings)
        captured.verify_source()
        public = json.dumps(result.public_summary())
        assert "synthetic-private-config-only" not in public
        assert str(profile.database.parent) not in public
        assert arguments["source_identity"]["node_id"] not in public


@pytest.mark.parametrize("phase", ["reserve", "selection", "copy", "first_copy_record", "source_verified", "manifest", "receipt"])
def test_interrupted_packages_block_four_namespaces_and_keep_private_protection(library, monkeypatch, phase):
    profile, _project, _local = library
    operation_id = uid()
    def interrupted(actual):
        if actual == phase:
            raise RuntimeError("synthetic private failure")
    monkeypatch.setattr(producer, "_checkpoint", interrupted)
    with pytest.raises(RuntimeError):
        producer.create(profile, operation_id=operation_id)
    result = scan(profile)
    all_blocked(result)
    exact, directories = group_roots._protection((profile,))
    catalog, _request, target = facts(profile, operation_id)
    assert group_roots._protected_path(catalog / (operation_id + ".request.json"), exact, directories)
    assert group_roots._protected_path(target / "payload/unknown/raw.bin", exact, directories)


@pytest.mark.parametrize("changed", ["missing_copy", "same_sha_new_inode", "corrupt_manifest", "corrupt_record", "undeclared_raw"])
def test_damaged_terminal_package_keeps_known_sql_refs_and_blocks_all(library, changed):
    profile, _project, _local = library
    storage_key = known_media(profile)
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    _catalog, _request, target, selection = controls(profile, operation_id)
    entry = next(row for row in selection["files"] if row["role"] == "local_config_raw")
    record = contract.decode((target / "copy-records" / (entry["entry_id"] + ".json")).read_bytes())
    copied = target / record["target_relative_key"]
    if changed == "missing_copy":
        copied.unlink()
    elif changed == "same_sha_new_inode":
        raw = copied.read_bytes()
        previous = copied.stat()
        replacement = copied.with_name("same-bytes-new-physical-copy.raw")
        replacement.write_bytes(raw)
        if os.name != "nt":
            replacement.chmod(0o600)
        os.replace(replacement, copied)
        current = copied.stat()
        assert (previous.st_dev, previous.st_ino) != (current.st_dev, current.st_ino)
    elif changed == "corrupt_manifest":
        (target / "manifest.json").write_bytes(b'{"private":"not a valid terminal manifest"}')
    elif changed == "corrupt_record":
        (target / "copy-records" / (entry["entry_id"] + ".json")).write_bytes(b'["not a closed record"]')
    else:
        unknown = target / "payload" / record["attempt_id"] / "undeclared.raw"
        unknown.write_bytes(b"unregistered original bytes")
        if os.name != "nt":
            unknown.chmod(0o600)
    result = scan(profile)
    all_blocked(result)
    assert any(value.storage_key == storage_key for value in result.references)


def test_corrupt_request_and_orphan_bundle_cannot_return_an_empty_graph(library):
    profile, _project, _local = library
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    catalog, _request, _target = facts(profile, operation_id)
    (catalog / (operation_id + ".request.json")).write_bytes(b'{"operation_id":"private malformed record"}')
    all_blocked(scan(profile))


@pytest.mark.parametrize("change", ["anchor_missing", "record_missing", "record_changed", "extra_anchor"])
def test_first_copy_anchor_is_required_and_cannot_be_replaced_by_bundle_record(library, change):
    profile, _project, _local = library
    storage_key = known_media(profile)
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    catalog, _request, target, selection = controls(profile, operation_id)
    entry = next(row for row in selection["files"] if row["role"] == "database_raw")
    index = catalog / (operation_id + ".copy-index")
    anchor_path = index / (entry["entry_id"] + ".json")
    record_path = target / "copy-records" / (entry["entry_id"] + ".json")
    original_anchor = anchor_path.read_bytes()
    assert original_anchor == record_path.read_bytes()
    if change == "anchor_missing":
        anchor_path.unlink()
        code = "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_MISSING"
    elif change == "record_missing":
        record_path.unlink()
        code = "PERSONAL_PRESERVATION_FIRST_COPY_RECORD_MISSING"
    elif change == "record_changed":
        changed = contract.decode(record_path.read_bytes())
        changed["copy_path_identity"]["ctime_ns"] = str(int(changed["copy_path_identity"]["ctime_ns"]) + 1)
        record_path.write_bytes(contract.canonical_bytes(changed))
        code = "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_CHANGED"
    else:
        extra = index / "f99999999.json"
        extra.write_bytes(original_anchor)
        if os.name != "nt":
            extra.chmod(0o600)
        code = "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_UNDECLARED"
    result = scan(profile)
    all_blocked(result)
    assert code in {value.code for value in result.blockers}
    assert any(value.storage_key == storage_key for value in result.references)
    assert any(value.section == "package" and value.state == "incomplete" for value in result.source_bindings)
    if change != "anchor_missing":
        assert anchor_path.read_bytes() == original_anchor


def test_real_anchor_before_record_gap_preserves_available_original_sql_refs(library, monkeypatch):
    profile, _project, _local = library
    storage_key = known_media(profile)
    operation_id = uid()
    def interrupted(phase):
        if phase != "first_copy_record":
            return
        catalog, _request, _target = facts(profile, operation_id)
        selection = read_selection(profile, operation_id)
        needed = [row["entry_id"] for row in selection["files"] if row["role"] in
                  {"database_raw", "database_wal_raw", "database_journal_raw"}]
        index = catalog / (operation_id + ".copy-index")
        if all((index / (entry_id + ".json")).exists() for entry_id in needed):
            raise RuntimeError("interrupted after original SQL first identity")
    monkeypatch.setattr(producer, "_checkpoint", interrupted)
    with pytest.raises(RuntimeError):
        producer.create(profile, operation_id=operation_id)
    result = scan(profile)
    all_blocked(result)
    assert "PERSONAL_PRESERVATION_FIRST_COPY_RECORD_MISSING" in {value.code for value in result.blockers}
    assert any(value.storage_key == storage_key for value in result.references)


def test_catalog_first_identity_pin_prevents_or_detects_same_bytes_replacement(library):
    profile, _project, _local = library
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    catalog, _request, _target, selection = controls(profile, operation_id)
    index = catalog / (operation_id + ".copy-index")
    anchor = index / (selection["files"][0]["entry_id"] + ".json")
    arguments = options(profile)
    if os.name == "nt":
        with lifecycle.shared(profile), roots.capture_locked(profile, **arguments) as captured:
            with pytest.raises(OSError):
                anchor.unlink()
            captured.verify_source()
    else:
        with lifecycle.shared(profile), pytest.raises(roots.PersonalPreservationRootError, match="SOURCE_CHANGED"):
            with roots.capture_locked(profile, **arguments) as captured:
                replacement = index / "same-bytes-private-anchor"
                replacement.write_bytes(anchor.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, anchor)
                captured.verify_source()


@pytest.mark.parametrize("entry", ["catalog_empty_directory", "bundles_direct_raw"])
def test_unknown_catalog_or_bundle_root_entry_blocks_scope(library, entry):
    profile, _project, _local = library
    producer.create(profile, operation_id=uid())
    if entry == "catalog_empty_directory":
        with safe._Directory(profile.database.parent / roots.CATALOG) as directory:
            files.mkdir_child(directory, "unknown-empty-capture")
    else:
        path = profile.database.parent / roots.BUNDLES / "unclassified-database.raw"
        path.write_bytes(b"unknown historical SQL scope")
        if os.name != "nt":
            path.chmod(0o600)
    all_blocked(scan(profile))


def test_oversized_original_package_body_is_not_an_empty_root_graph(library):
    profile, _project, _local = library
    producer.create(profile, operation_id=uid())
    result = scan(profile, limits=roots.PersonalPreservationLimits(max_file_bytes=1))
    all_blocked(result)


def test_caller_cancellation_is_preserved_instead_of_becoming_a_package_blocker(library):
    profile, _project, _local = library
    arguments = options(profile)
    original = ValueError("caller-controlled cancellation")
    def cancelled():
        raise original
    with lifecycle.shared(profile), pytest.raises(ValueError) as caught:
        with roots.capture_locked(profile, **arguments, check_cancelled=cancelled):
            pytest.fail("Cancelled scan was returned as a completed observation")
    assert caught.value is original


def test_original_knowledge_state_never_borrows_live_state_or_lock(sender_env, monkeypatch):
    env = sender_env
    profile = env["source_profile"]
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    assert any(row["role"] == "knowledge_state_raw" for row in read_selection(profile, operation_id)["files"])
    def forbidden(*_args, **_kwargs):
        pytest.fail("Root capture borrowed current Home State or a producer")
    monkeypatch.setattr(type(env["store"]), "exclusive", forbidden)
    monkeypatch.setattr(source, "capture_locked", forbidden)
    result = scan(profile)
    all_blocked(result)
    assert "PERSONAL_PRESERVATION_KNOWLEDGE_DEPENDENCIES_UNKNOWN" in {value.code for value in result.blockers}


def test_observer_holds_actual_group_sh_against_other_process_ex(library):
    profile, _project, _local = library
    arguments = options(profile)
    paths = {key: str(getattr(profile, key)) for key in ("database", "media", "reports", "checkpoints")}
    script = """import json,sys
from reno import storage_lifecycle as lifecycle
profile = lifecycle.StorageProfile(**json.loads(sys.argv[1]))
try:
    with lifecycle.exclusive(profile, timeout=0.1):
        print('unexpected-ex')
except lifecycle.StorageLifecycleError as error:
    print(error.code)
"""
    with lifecycle.shared(profile), roots.capture_locked(profile, **arguments) as captured:
        child = subprocess.run([sys.executable, "-B", "-c", script, json.dumps(paths)],
                               capture_output=True, text=True, timeout=20)
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "STORAGE_BUSY"
        captured.verify_source()


def test_package_file_pin_prevents_or_detects_same_bytes_replacement(library):
    profile, _project, _local = library
    operation_id = uid()
    producer.create(profile, operation_id=operation_id)
    catalog, _request, _target = facts(profile, operation_id)
    request_path = catalog / (operation_id + ".request.json")
    arguments = options(profile)
    if os.name == "nt":
        with lifecycle.shared(profile), roots.capture_locked(profile, **arguments) as captured:
            with pytest.raises(OSError):
                request_path.unlink()
            captured.verify_source()
    else:
        with lifecycle.shared(profile), pytest.raises(roots.PersonalPreservationRootError, match="SOURCE_CHANGED"):
            with roots.capture_locked(profile, **arguments) as captured:
                replacement = catalog / "same-bytes-private-replacement"
                replacement.write_bytes(request_path.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, request_path)
                captured.verify_source()


def test_private_trees_are_protected_independently_of_claimed_artifact_namespace(library):
    profile, _project, _local = library
    exact, directories = group_roots._protection((profile,))
    for name in (roots.CATALOG, roots.BUNDLES):
        assert group_roots._protected_path(profile.database.parent / name / "unfinished/arbitrary.raw", exact, directories)
    assert group_roots._protected_path(profile.database.parent / "personal-mirror-outbox/operation.lock", exact, directories)
