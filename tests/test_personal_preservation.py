"""Actual independent packages, original selection and interrupted publication."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from reno import config, db, personal, storage_admin
from reno import personal_preservation as preservation
from reno import personal_preservation_contract as contract
from reno import personal_preservation_source as source
from reno import storage_lifecycle as lifecycle
from reno import personal_preservation_files as package_files, storage_source as original_source


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "home"
    data = root / "data"
    data.mkdir(parents=True)
    local = root / "config.private.json"
    local.write_text(json.dumps({"node_role": "all", "personal_features_enabled": True,
        "field_checks_enabled": True, "personal_mirror_enabled": False,
        "zhipu_api_key": "synthetic-private-config-only"}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
            "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
            "FRAME_CACHE": root / "frames-cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    profile = storage_admin.profile()
    assert db.initialize(profile.database).version == 17
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as con:
        project = personal.default_project(con)["project"]
    return profile, project, local


def facts(profile, operation_id):
    catalog = profile.database.parent / contract.CATALOG
    request = contract.decode((catalog / (operation_id + ".request.json")).read_bytes())
    target = Path(request["target"])
    return catalog, request, target


def read_selection(profile, operation_id):
    catalog, _request, _target = facts(profile, operation_id)
    return contract.decode((catalog / (operation_id + ".selection.json")).read_bytes())


def immutable_bytes(profile, operation_id):
    catalog, _request, target = facts(profile, operation_id)
    paths = [p for p in (*catalog.rglob("*"), *target.rglob("*")) if p.is_file()
             and p.name != operation_id + ".state.json"]
    return {str(p): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in paths}


def test_normal17_package_preserves_raw_and_has_no_runtime_authority(library):
    profile, _project, local = library
    operation_id = uid()
    original = {path: path.read_bytes() for path in (profile.database,
        profile.database.parent / "node-identity.json", local)}
    result = preservation.create(profile, operation_id=operation_id)
    assert set(result) == {"operation_id", "phase", "file_count", "byte_size"}
    assert result["phase"] == "ready" and result["file_count"] >= 3
    catalog, request, target = facts(profile, operation_id)
    selection = read_selection(profile, operation_id)
    manifest = contract.decode((target / "manifest.json").read_bytes())
    assert manifest["coverage"]["runtime_restore_authority"] is False
    assert manifest["coverage"]["primary_handoff_authority"] is False
    assert manifest["coverage"]["knowledge_restore_dependencies"] == "unknown"
    assert not (profile.database.parent / "personal-mirror-outbox").exists()
    assert {path: path.read_bytes() for path in original} == original
    assert selection["sql"]["receiver_facility"] == "absent"
    for item in manifest["files"]:
        original_file, copied = item["source"], item["copy"]
        path = target / copied["target_relative_key"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original_file["sha256"]
        assert (original_file["source_path_identity"]["device"], original_file["source_path_identity"]["inode"]) != (
                copied["copy_path_identity"]["device"], copied["copy_path_identity"]["inode"])
    assert "synthetic-private-config-only" not in json.dumps(result)
    assert (catalog / (operation_id + ".source-verified.json")).exists()
    with lifecycle.shared(profile):
        assert preservation.verify_ready_locked(profile, operation_id=operation_id) == result


def test_committed_sql_temp_is_private_before_first_original_byte_copy(library, monkeypatch):
    profile, _project, _local = library
    seen = []
    original_copy = original_source.copy_file
    def inspect_privacy(directory, name, destination, expected, **kwargs):
        package_files.require_private(Path(destination).parent, directory=True)
        assert not Path(destination).exists()
        seen.append(name)
        return original_copy(directory, name, destination, expected, **kwargs)
    monkeypatch.setattr(original_source, "copy_file", inspect_privacy)
    assert preservation.create(profile, operation_id=uid())["phase"] == "ready"
    assert profile.database.name in seen


@pytest.mark.parametrize("phase", ["reserve", "selection", "copy_attempt", "copy", "first_copy_record", "copy_record",
                                    "source_check", "source_verified", "manifest", "receipt", "ready"])
def test_interrupted_original_operation_resumes_only_published_selection(library, monkeypatch, phase):
    profile, _project, _local = library
    operation_id = uid()
    class Interruption(BaseException):
        pass
    def interrupt(actual):
        if actual == phase:
            raise Interruption("synthetic-sensitive-exception-body")
    monkeypatch.setattr(preservation, "_checkpoint", interrupt)
    with pytest.raises(Interruption):
        preservation.create(profile, operation_id=operation_id)
    catalog, request, target = facts(profile, operation_id)
    sha = contract.digest(request)
    failures = list((catalog / (operation_id + ".failures")).glob("*.json"))
    assert len(failures) == 1
    assert b"synthetic-sensitive-exception-body" not in failures[0].read_bytes()
    before_selection = (catalog / (operation_id + ".selection.json")).read_bytes() if phase != "reserve" else None
    monkeypatch.setattr(preservation, "_checkpoint", lambda _phase: None)
    if phase == "reserve":
        with pytest.raises(preservation.PersonalPreservationError) as caught:
            preservation.resume(profile, operation_id=operation_id, expected_request_sha256=sha)
        assert caught.value.code == "PERSONAL_PRESERVATION_CAPTURE_INCOMPLETE"
        assert not (catalog / (operation_id + ".selection.json")).exists()
    else:
        result = preservation.resume(profile, operation_id=operation_id, expected_request_sha256=sha)
        assert result["phase"] == "ready"
        assert (catalog / (operation_id + ".selection.json")).read_bytes() == before_selection


def test_selection_resume_never_adds_new_source_files(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    def stop(phase):
        if phase == "selection":
            raise RuntimeError("interrupted")
    monkeypatch.setattr(preservation, "_checkpoint", stop)
    with pytest.raises(RuntimeError):
        preservation.create(profile, operation_id=operation_id)
    catalog, request, _target = facts(profile, operation_id)
    original_selection = (catalog / (operation_id + ".selection.json")).read_bytes()
    attachments = profile.database.parent / "personal-attachments"
    attachments.mkdir(exist_ok=True)
    (attachments / "new-unclassified.raw").write_bytes(b"new data")
    monkeypatch.setattr(preservation, "_checkpoint", lambda _phase: None)
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.resume(profile, operation_id=operation_id, expected_request_sha256=contract.digest(request))
    assert caught.value.code == "PERSONAL_PRESERVATION_SOURCE_CHANGED"
    assert (catalog / (operation_id + ".selection.json")).read_bytes() == original_selection
    assert not (catalog / (operation_id + ".source-verified.json")).exists()


def test_source_context_exit_failure_cannot_leave_source_verified(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    original_capture = source.capture_locked
    @contextmanager
    def rejected_exit(*args, **kwargs):
        with original_capture(*args, **kwargs) as captured:
            yield captured
        raise RuntimeError("synthetic original observer exit failure")
    monkeypatch.setattr(source, "capture_locked", rejected_exit)
    with pytest.raises(RuntimeError):
        preservation.create(profile, operation_id=operation_id)
    catalog, _request, target = facts(profile, operation_id)
    assert (catalog / (operation_id + ".selection.json")).exists()
    assert not (catalog / (operation_id + ".source-verified.json")).exists()
    assert not (target / "manifest.json").exists()
    assert not (catalog / (operation_id + ".receipt.json")).exists()


def test_historical_verified_capture_can_finish_after_current_personal_sql_changes(library, monkeypatch):
    profile, project, _local = library
    operation_id = uid()
    def stop(phase):
        if phase == "source_verified":
            raise RuntimeError("interrupted")
    monkeypatch.setattr(preservation, "_checkpoint", stop)
    with pytest.raises(RuntimeError):
        preservation.create(profile, operation_id=operation_id)
    catalog, request, _target = facts(profile, operation_id)
    verified_before = (catalog / (operation_id + ".source-verified.json")).read_bytes()
    updated, _replayed = personal.update_project(project["project_id"], {
        "operation_id": uid(), "primary_epoch": 1, "base_revision": project["revision"],
        "changes": {"name": "current source changed after original capture"}})
    assert updated["project"]["name"] == "current source changed after original capture"
    def prohibit_recapture(*_args, **_kwargs):
        pytest.fail("A verified historical capture must not borrow today's source")
    monkeypatch.setattr(source, "capture_locked", prohibit_recapture)
    monkeypatch.setattr(preservation, "_checkpoint", lambda _phase: None)
    result = preservation.resume(profile, operation_id=operation_id, expected_request_sha256=contract.digest(request))
    assert result["phase"] == "ready"
    assert (catalog / (operation_id + ".source-verified.json")).read_bytes() == verified_before


def test_ready_replay_keeps_all_original_facts_and_rejects_wrong_request_sha(library):
    profile, _project, _local = library
    operation_id = uid()
    expected = preservation.create(profile, operation_id=operation_id)
    _catalog, request, _target = facts(profile, operation_id)
    before = immutable_bytes(profile, operation_id)
    assert preservation.resume(profile, operation_id=operation_id,
        expected_request_sha256=contract.digest(request)) == expected
    assert immutable_bytes(profile, operation_id) == before
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.resume(profile, operation_id=operation_id, expected_request_sha256="0" * 64)
    assert caught.value.code == "PERSONAL_PRESERVATION_OPERATION_CONFLICT"
    assert immutable_bytes(profile, operation_id) == before


def test_no_facility_is_initialized_by_preservation(library, tmp_path):
    _profile, _project, _local = library
    root = tmp_path / "bare"
    root.mkdir()
    profile = lifecycle.StorageProfile(root / "reno.db", root / "media", root / "reports", root / "checkpoints")
    with pytest.raises(lifecycle.StorageLifecycleError):
        preservation.create(profile, operation_id=uid())
    assert list(root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows pinned file denies replacement")
def test_same_bytes_replaced_copy_and_unregistered_payload_cannot_verify(library):
    profile, _project, _local = library
    operation_id = uid()
    preservation.create(profile, operation_id=operation_id)
    _catalog, _request, target = facts(profile, operation_id)
    manifest = contract.decode((target / "manifest.json").read_bytes())
    copied = target / manifest["files"][0]["copy"]["target_relative_key"]
    replacement = copied.parent / "replacement.raw"
    replacement.write_bytes(copied.read_bytes())
    replacement.chmod(0o600)
    replacement.replace(copied)
    with lifecycle.shared(profile), pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.verify_ready_locked(profile, operation_id=operation_id)
    assert caught.value.code == "PERSONAL_PRESERVATION_COPY_CHANGED"


def test_extra_unregistered_copy_rejects_ready_package(library):
    profile, _project, _local = library
    operation_id = uid()
    preservation.create(profile, operation_id=operation_id)
    _catalog, _request, target = facts(profile, operation_id)
    payload = next((target / "payload").iterdir())
    (payload / "unregistered.raw").write_bytes(b"unregistered bytes")
    with lifecycle.shared(profile), pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.verify_ready_locked(profile, operation_id=operation_id)
    assert caught.value.code == "PERSONAL_PRESERVATION_COPY_UNDECLARED"


def test_missing_bundle_record_uses_original_independent_first_identity(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    def stop(phase):
        if phase == "copy_record":
            raise RuntimeError("original record was published")
    monkeypatch.setattr(preservation, "_checkpoint", stop)
    with pytest.raises(RuntimeError):
        preservation.create(profile, operation_id=operation_id)
    catalog, request, target = facts(profile, operation_id)
    record_path = next((target / "copy-records").glob("*.json"))
    anchor_path = catalog / (operation_id + ".copy-index") / record_path.name
    original = anchor_path.read_bytes()
    record = contract.decode(original)
    copied = target / record["target_relative_key"]
    before = (copied.stat().st_ino, copied.stat().st_mtime_ns, copied.read_bytes())
    record_path.unlink()
    monkeypatch.setattr(preservation, "_checkpoint", lambda _phase: None)
    result = preservation.resume(profile, operation_id=operation_id,
                                 expected_request_sha256=contract.digest(request))
    assert result["phase"] == "ready"
    assert record_path.read_bytes() == anchor_path.read_bytes() == original
    assert (copied.stat().st_ino, copied.stat().st_mtime_ns, copied.read_bytes()) == before


def test_existing_record_without_first_identity_anchor_cannot_be_reissued(library):
    profile, _project, _local = library
    operation_id = uid()
    preservation.create(profile, operation_id=operation_id)
    catalog, request, target = facts(profile, operation_id)
    record_path = next((target / "copy-records").glob("*.json"))
    original = record_path.read_bytes()
    (catalog / (operation_id + ".copy-index") / record_path.name).unlink()
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.resume(profile, operation_id=operation_id,
                             expected_request_sha256=contract.digest(request))
    assert caught.value.code == "PERSONAL_PRESERVATION_FIRST_COPY_IDENTITY_MISSING"
    assert record_path.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="Linux tests identity replacement separately")
def test_missing_record_same_sha_replaced_payload_never_gets_new_first_identity(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    def stop(phase):
        if phase == "copy_record":
            raise RuntimeError("original record was published")
    monkeypatch.setattr(preservation, "_checkpoint", stop)
    with pytest.raises(RuntimeError):
        preservation.create(profile, operation_id=operation_id)
    catalog, request, target = facts(profile, operation_id)
    record_path = next((target / "copy-records").glob("*.json"))
    anchor_path = catalog / (operation_id + ".copy-index") / record_path.name
    anchor_before = anchor_path.read_bytes()
    record = contract.decode(anchor_before)
    copied = target / record["target_relative_key"]
    record_path.unlink()
    replacement = copied.parent / "replacement.raw"
    replacement.write_bytes(copied.read_bytes())
    replacement.chmod(0o600)
    replacement.replace(copied)
    monkeypatch.setattr(preservation, "_checkpoint", lambda _phase: None)
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        preservation.resume(profile, operation_id=operation_id,
                             expected_request_sha256=contract.digest(request))
    assert caught.value.code == "PERSONAL_PRESERVATION_COPY_CHANGED"
    assert anchor_path.read_bytes() == anchor_before
    assert not record_path.exists()
