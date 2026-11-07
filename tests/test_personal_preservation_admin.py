"""Real public preservation locks, immutable continuation and private summaries."""
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from reno import config, db, personal, storage_admin, tasks
from reno import personal_preservation as producer
from reno import personal_preservation_admin as admin
from reno import personal_preservation_contract as contract
from reno import personal_preservation_files as files
from reno import storage_lifecycle as lifecycle


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "home"
    data = root / "data"
    data.mkdir(parents=True)
    local = root / "private-config.json"
    local.write_text(json.dumps({"node_role": "all", "personal_features_enabled": True,
        "field_checks_enabled": True, "personal_mirror_enabled": False,
        "zhipu_api_key": "synthetic-private-key"}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    for name, path in {"ROOT": root, "DATA": data, "MEDIA": root / "media",
        "ORIG": root / "media/originals", "FRAMES": root / "media/frames",
        "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames-cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    profile = storage_admin.profile()
    assert db.initialize(profile.database).version == 17
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as con:
        project = personal.default_project(con)["project"]
    return profile, project, local


def package(profile, operation_id):
    catalog = profile.database.parent / contract.CATALOG
    request = contract.decode((catalog / (operation_id + ".request.json")).read_bytes())
    return catalog, request, Path(request["target"])


def snapshot(profile):
    root = profile.database.parent.parent
    return {str(path.relative_to(root)): (path.stat().st_ino, path.stat().st_size,
        path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*") if path.is_file()}


def forbidden(*args, **kwargs):
    pytest.fail("This read or refused admission must not initialize or acquire a worker lock")


def test_absent_status_does_not_create_any_directory_facility_or_worker_marker(tmp_path, monkeypatch):
    profile = lifecycle.StorageProfile(tmp_path / "absent/data/reno.db", tmp_path / "absent/media",
        tmp_path / "absent/reports", tmp_path / "absent/checkpoints")
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    monkeypatch.setattr(db, "connect", forbidden)
    assert admin.status(profile) == {"kind": "personal_preservation_status", "present": False,
        "operation_count": 0, "operations": [], "verification": "not_performed", "verified": False}
    selected = admin.status(profile, uid())
    assert selected["phase"] == "absent" and selected["state_recorded"] is False
    assert selected["request_sha256"] is None and not (tmp_path / "absent").exists()


def test_missing_facilities_refuse_create_before_worker_marker_or_catalog(tmp_path, monkeypatch):
    root = tmp_path / "uninitialized"
    for name in ("data", "media", "reports", "checkpoints"):
        (root / name).mkdir(parents=True)
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media",
        root / "reports", root / "checkpoints")
    before = sorted(str(path) for path in root.rglob("*"))
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        admin.create(profile, uid())
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert sorted(str(path) for path in root.rglob("*")) == before


@pytest.mark.parametrize("populated", [False, True])
def test_orphan_bundle_namespace_is_not_reported_absent_without_catalog(tmp_path, monkeypatch, populated):
    root = tmp_path / "orphan"
    bundles = root / "data" / contract.DIRECTORY
    bundles.mkdir(parents=True)
    if populated:
        target = bundles / uid()
        target.mkdir()
        (target / "manifest.json").write_bytes(b"original unresolved raw evidence")
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media",
        root / "reports", root / "checkpoints")
    before = sorted(str(path) for path in root.rglob("*"))
    monkeypatch.setattr(lifecycle, "shared", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    for operation_id in (None, uid()):
        with pytest.raises(producer.PersonalPreservationError) as caught:
            admin.status(profile, operation_id)
        assert caught.value.code == "PERSONAL_PRESERVATION_CATALOG_MISSING"
    assert sorted(str(path) for path in root.rglob("*")) == before


def test_existing_guard_refuses_write_before_worker_lock_inversion(library, monkeypatch):
    profile, _project, _local = library
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    with lifecycle.shared(profile) as guard:
        with pytest.raises(producer.PersonalPreservationError) as caught:
            admin.create(profile, uid())
        assert caught.value.code == "PERSONAL_PRESERVATION_LOCK_CONTEXT_ACTIVE"
        assert lifecycle.require_guard(profile) is guard
    assert not (profile.database.parent / contract.CATALOG).exists()


def test_create_holds_real_worker_then_ex_and_closes_both_before_return(library, monkeypatch):
    profile, _project, _local = library
    original, seen = producer.create_locked, []
    def guarded(value, **kwargs):
        assert tasks.holds_process_lock(profile=value)
        assert lifecycle.require_guard(value, exclusive=True).exclusive
        seen.append(kwargs["operation_id"])
        return original(value, **kwargs)
    monkeypatch.setattr(producer, "create_locked", guarded)
    operation_id = uid()
    result = admin.create(profile, operation_id)
    catalog, request, _target = package(profile, operation_id)
    assert seen == [operation_id] and result["phase"] == "ready" and result["verified"] is True
    assert result["request_sha256"] == contract.digest(request)
    assert not tasks.holds_process_lock(profile=profile) and lifecycle.current_guard() is None
    assert "synthetic-private-key" not in json.dumps(result) and str(catalog) not in json.dumps(result)
    with tasks.process_lock(profile=profile) as acquired:
        assert acquired
    with lifecycle.exclusive(profile, timeout=0) as held:
        held.verify()


def test_busy_real_worker_refuses_without_reserving_operation(library, monkeypatch):
    profile, _project, _local = library
    monkeypatch.setattr(producer, "create_locked", forbidden)
    operation_id = uid()
    with tasks.process_lock(profile=profile) as acquired:
        assert acquired
        with pytest.raises(producer.PersonalPreservationError) as caught:
            admin.create(profile, operation_id)
        assert caught.value.code == "PERSONAL_PRESERVATION_WORKER_BUSY" and caught.value.retriable
        assert tasks.holds_process_lock(profile=profile)
    assert not (profile.database.parent / contract.CATALOG).exists()


def test_status_ready_is_recorded_only_and_verify_detects_missing_original_body(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    created = admin.create(profile, operation_id)
    catalog, _request, target = package(profile, operation_id)
    manifest = contract.decode((target / "manifest.json").read_bytes())
    body = target / manifest["files"][0]["copy"]["target_relative_key"]
    body.unlink()
    before = snapshot(profile)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    summary = admin.status(profile, operation_id)
    assert summary["phase"] == "ready" and summary["state_recorded"] is True
    assert summary["request_sha256"] == created["request_sha256"]
    assert summary["verified"] is False and summary["verification"] == "not_performed"
    assert admin.status(profile)["operations"] == [summary]
    with pytest.raises(producer.PersonalPreservationError):
        admin.verify(profile, operation_id, created["request_sha256"])
    assert snapshot(profile) == before and lifecycle.current_guard() is None
    assert (catalog / (operation_id + ".state.json")).exists()


def test_verify_is_pure_read_and_valid_after_today_personal_facts_change(library, monkeypatch):
    profile, project, _local = library
    operation_id = uid()
    created = admin.create(profile, operation_id)
    personal.update_project(project["project_id"], {"operation_id": uid(), "primary_epoch": 1,
        "base_revision": 1, "changes": {"name": "Changed after the historical package"}})
    before = snapshot(profile)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    assert admin.verify(profile, operation_id, created["request_sha256"]) == created
    assert snapshot(profile) == before and lifecycle.current_guard() is None


def test_reserved_uuid_without_selection_is_never_recaptured(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    class Interrupted(BaseException):
        pass
    def interrupt(phase):
        if phase == "reserve":
            raise Interrupted("private interruption details")
    monkeypatch.setattr(producer, "_checkpoint", interrupt)
    with pytest.raises(Interrupted):
        admin.create(profile, operation_id)
    catalog, request, _target = package(profile, operation_id)
    summary = admin.status(profile, operation_id)
    assert summary["phase"] == "failed" and summary["request_sha256"] == contract.digest(request)
    monkeypatch.setattr(producer.source, "capture_locked", forbidden)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        admin.resume(profile, operation_id, summary["request_sha256"])
    assert caught.value.code == "PERSONAL_PRESERVATION_CAPTURE_INCOMPLETE"
    assert not (catalog / (operation_id + ".selection.json")).exists()


def test_resume_after_first_copy_anchor_keeps_original_payload_identity(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    class Interrupted(BaseException):
        pass
    def interrupt(phase):
        if phase == "first_copy_record":
            raise Interrupted()
    monkeypatch.setattr(producer, "_checkpoint", interrupt)
    with pytest.raises(Interrupted):
        admin.create(profile, operation_id)
    catalog, request, target = package(profile, operation_id)
    original_request = (catalog / (operation_id + ".request.json")).read_bytes()
    original_selection = (catalog / (operation_id + ".selection.json")).read_bytes()
    anchor_path = next((catalog / (operation_id + ".copy-index")).glob("*.json"))
    anchor_raw = anchor_path.read_bytes()
    anchor = contract.decode(anchor_raw)
    payload = target / anchor["target_relative_key"]
    original = (payload.stat().st_ino, payload.read_bytes())
    monkeypatch.setattr(producer, "_checkpoint", lambda phase: None)
    result = admin.resume(profile, operation_id, contract.digest(request))
    assert result["verified"] is True and result["phase"] == "ready"
    assert (payload.stat().st_ino, payload.read_bytes()) == original and anchor_path.read_bytes() == anchor_raw
    assert (target / "copy-records" / anchor_path.name).read_bytes() == anchor_raw
    assert (catalog / (operation_id + ".request.json")).read_bytes() == original_request
    assert (catalog / (operation_id + ".selection.json")).read_bytes() == original_selection


@pytest.mark.parametrize(("method", "invalid"), [
    (method, invalid) for method in ("status", "create", "resume", "verify")
    for invalid in (None, 4, True, "not-a-uuid", "ABCDEFAB-1234-4234-8234-ABCDEFABCDEF")
    if method != "status" or invalid is not None])
def test_invalid_operation_is_rejected_before_lock_or_filesystem(tmp_path, monkeypatch, method, invalid):
    profile = lifecycle.StorageProfile(tmp_path / "absent/data/reno.db", tmp_path / "absent/media",
        tmp_path / "absent/reports", tmp_path / "absent/checkpoints")
    monkeypatch.setattr(lifecycle, "shared", forbidden)
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    arguments = (profile, invalid, "0" * 64) if method == "resume" else (profile, invalid)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        getattr(admin, method)(*arguments)
    assert caught.value.code == "PERSONAL_PRESERVATION_OPERATION_INVALID"
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("sha", [None, True, "x" * 64, "A" * 64, "0" * 63])
def test_resume_requires_canonical_original_sha_before_worker_lock(library, monkeypatch, sha):
    profile, _project, _local = library
    monkeypatch.setattr(tasks, "process_lock", forbidden)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        admin.resume(profile, uid(), sha)
    expected = "REQUEST_SHA_REQUIRED" if sha is None else "REQUEST_SHA_INVALID"
    assert caught.value.code == "PERSONAL_PRESERVATION_" + expected
    assert not (profile.database.parent / contract.CATALOG).exists()


@pytest.mark.parametrize("damage", ["request_noncanonical", "state_noncanonical", "state_conflict",
                                   "orphan_state", "unknown_name", "temporary_evidence"])
def test_catalog_damage_or_unknown_evidence_never_returns_an_empty_success(library, damage):
    profile, _project, _local = library
    operation_id = uid()
    admin.create(profile, operation_id)
    catalog, _request, _target = package(profile, operation_id)
    request_path, state_path = (catalog / (operation_id + suffix) for suffix in (".request.json", ".state.json"))
    if damage == "request_noncanonical":
        request_path.write_bytes(request_path.read_bytes() + b"\n")
    elif damage == "state_noncanonical":
        state_path.write_bytes(state_path.read_bytes() + b"\n")
    elif damage == "state_conflict":
        state = contract.decode(state_path.read_bytes())
        state["request_sha256"] = "0" * 64
        state_path.write_bytes(contract.canonical_bytes(state))
    elif damage == "orphan_state":
        request_path.unlink()
    else:
        name = "private-unknown-name" if damage == "unknown_name" else ".preservation-write-" + uid() + ".tmp"
        path = catalog / name
        path.write_bytes(b"private-unknown-content")
        if os.name == "nt":
            files._windows_acl(path, directory=False, initialize=True)
        else:
            path.chmod(0o600)
    before = snapshot(profile)
    with pytest.raises((producer.PersonalPreservationError, contract.PersonalPreservationError)):
        admin.status(profile)
    assert snapshot(profile) == before


def test_status_file_final_checks_are_inside_original_sh(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    admin.create(profile, operation_id)
    original, seen = files.FileHold.__exit__, []
    def finish(held, *args):
        assert lifecycle.require_guard(profile).exclusive is False
        seen.append(held.path.name)
        return original(held, *args)
    monkeypatch.setattr(files.FileHold, "__exit__", finish)
    assert admin.status(profile, operation_id)["verified"] is False
    assert operation_id + ".request.json" in seen and operation_id + ".state.json" in seen
    assert lifecycle.current_guard() is None


def test_status_exit_time_namespace_change_cannot_escape_as_a_recorded_summary(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    admin.create(profile, operation_id)
    catalog, _request, _target = package(profile, operation_id)
    original, changed = files.FileHold.__exit__, []
    def change_after_reader(held, *args):
        result = original(held, *args)
        if not changed:
            assert lifecycle.require_guard(profile).exclusive is False
            (catalog / "unexpected-at-final-exit").write_bytes(b"private raw")
            changed.append(True)
        return result
    monkeypatch.setattr(files.FileHold, "__exit__", change_after_reader)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        admin.status(profile, operation_id)
    assert caught.value.code == "PERSONAL_PRESERVATION_CATALOG_CHANGED"
    assert lifecycle.current_guard() is None


def test_wrong_original_sha_cannot_continue_or_verify_another_request(library):
    profile, _project, _local = library
    operation_id = uid()
    admin.create(profile, operation_id)
    before = snapshot(profile)
    for method in (admin.resume, admin.verify):
        with pytest.raises(producer.PersonalPreservationError) as caught:
            method(profile, operation_id, "0" * 64)
        assert caught.value.code == "PERSONAL_PRESERVATION_OPERATION_CONFLICT"
    assert snapshot(profile) == before


def test_status_metadata_budget_refuses_without_payload_reads_or_mutation(library, monkeypatch):
    profile, _project, _local = library
    operation_id = uid()
    admin.create(profile, operation_id)
    before = snapshot(profile)
    monkeypatch.setattr(admin, "MAX_STATUS_BYTES", 1)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        admin.status(profile, operation_id)
    assert caught.value.code == "PERSONAL_PRESERVATION_STATUS_LIMIT"
    assert snapshot(profile) == before


def test_unknown_exception_is_fixed_code_without_private_error_text(library, monkeypatch):
    profile, _project, _local = library
    def crash(*args, **kwargs):
        raise RuntimeError("synthetic-private-key C:/private/address")
    monkeypatch.setattr(producer, "create_locked", crash)
    with pytest.raises(producer.PersonalPreservationError) as caught:
        admin.create(profile, uid())
    assert caught.value.code == "PERSONAL_PRESERVATION_ADMIN_FAILED"
    assert "synthetic-private-key" not in str(caught.value) and "C:/private/address" not in str(caught.value)
    assert lifecycle.current_guard() is None and not tasks.holds_process_lock(profile=profile)
