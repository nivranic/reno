"""Independent capsule roots from actual receipt-first pending producers."""
from dataclasses import replace
import hashlib
import json
import os
import sqlite3

import pytest

from reno import storage_legacy_pending_state17 as roots, storage_lifecycle as lifecycle
from reno import sync_source_legacy_pending_intake17 as intake
from reno import sync_source_legacy_pending_state17 as capsules, sync_state
from tests.test_sync_source_legacy_pending_intake17 import real_cycle, received, sql_rows
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_restore_cycle17 import actual_call


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value)
    return value


@pytest.fixture
def completed(real_cycle):
    value = real_cycle
    intent, arguments, receipt, _ = received(value)
    with value.store.reference_operation():
        actual_call(intake.reconcile_original_pending17_locked, **arguments)
    path = capsules.path(value.profile, intent["record"]["record_id"])
    marker = path.with_name(intent["record"]["record_id"] + ".applied.json")
    assert path.is_file() and marker.is_file()
    return value, intent, receipt, path, marker


def captured(profile, **kwargs):
    with lifecycle.shared(profile), roots.capture_locked(profile, **kwargs) as value:
        value.verify_source()
        return value.public_projection(), value.records


def private_images(profile):
    with lifecycle.shared(profile), roots.capture_locked(profile) as value:
        return value.state_images


def write(path, value):
    path.write_bytes(roots._json(value))
    if os.name != "nt":
        path.chmod(0o600)


def original_inputs(value, path, marker):
    state = value.store.data_dir / sync_state._STATE
    return (state.read_bytes(), state.stat().st_mtime_ns,
        path.read_bytes(), path.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns,
        permanent_files(value), sql_rows(value.profile), list(value.transport.seen))


def test_actual_absence_and_present_empty_have_distinct_namespace_proofs_without_creation(profile):
    directory = profile.database.parent / roots.DIRECTORY
    absent, files = captured(profile)
    assert not directory.exists() and files == ()
    assert absent["summary"]["presence"] == "absent" and absent["summary"]["absence_sha256"]
    directory.mkdir(mode=0o700)
    present, files = captured(profile)
    assert files == () and present["summary"]["presence"] == "present"
    assert present["summary"]["absence_sha256"] is None
    assert present["summary"]["namespace_sha256"] != absent["summary"]["namespace_sha256"]
    assert roots.normalize_summary(absent["summary"]) == absent["summary"]
    assert roots.normalize_summary(present["summary"]) == present["summary"]


def test_real_capsule_marker_and_both_complete_state_images_are_read_only_inputs(completed):
    value, intent, receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    projection, records = captured(value.profile)
    summary = projection["summary"]
    assert summary["file_count"] == 2 and summary["capsule_count"] == summary["marker_count"] == summary["applied_count"] == 1
    assert summary["unknown_count"] == 0 and not projection["unknown"]
    assert [record.name for record in records] == sorted([path.name, marker.name])
    assert {row["role"] for row in projection["files"]} == {"legacy_pending_state", "legacy_pending_applied"}
    document = json.loads(path.read_bytes())
    states = {row["phase"]: json.loads(row["state_json"]) for row in private_images(value.profile)}
    assert states == {"before": json.loads(document["before_json"]), "after": json.loads(document["after_json"])}
    assert states["before"]["source_recovery_pending"]["intent_sha256"] == intent["ref"]["record_sha256"]
    assert any(row.get("record_kind") == "recovery_receipt"
        and row["record_sha256"] == receipt["ref"]["record_sha256"] for row in projection["references"])
    assert "state_images" not in projection and len(projection["states"]) == 2
    assert "state_json" not in json.dumps(projection)
    assert value.core["binding"]["target_url"] not in json.dumps(projection)
    assert original_inputs(value, path, marker) == before


def test_original_copied_bytes_project_identically_without_live_file_or_sql_lookups(completed, monkeypatch):
    value, _intent, _receipt, _path, _marker = completed
    actual, records = captured(value.profile)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Pure projection opened SQL"))
    monkeypatch.setattr(roots.safe, "_Directory", lambda *a, **kw: pytest.fail("Pure projection opened a live directory"))
    assert roots.pure_projection(value.profile, presence="present", records=records) == actual


def test_capture_never_opens_sql_or_state_writer_and_keeps_original_native_pins(completed, monkeypatch):
    value, _intent, _receipt, _path, _marker = completed
    with lifecycle.shared(value.profile):
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Capture opened SQL"))
        monkeypatch.setattr(sync_state, "_write", lambda *a, **kw: pytest.fail("Capture wrote State or a marker"))
        with roots.capture_locked(value.profile) as held:
            assert len(held._reader.files) == 2 and all(row[2] is not None for row in held._reader.files)
            held.verify_for_commit()
            held.verify_source()


def test_marker_binds_actual_capsule_file_sha_and_bad_marker_keeps_both_state_roots(completed):
    value, _intent, _receipt, path, marker = completed
    original = json.loads(marker.read_bytes())
    assert original["capsule_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    original["capsule_sha256"] = "f" * 64
    write(marker, original)
    projection, records = captured(value.profile)
    assert len(records) == 2 and len(projection["states"]) == 2
    assert projection["summary"]["presence"] == "present"
    assert projection["summary"]["applied_count"] == 0
    assert any(row["code"] == "APPLIED_ORIGINAL_UNPROVEN" for row in projection["unknown"])


def test_orphan_applied_marker_is_unknown_and_cannot_prove_completion_or_absence(completed):
    value, _intent, _receipt, path, marker = completed
    path.unlink()
    projection, records = captured(value.profile)
    assert len(records) == 1 and records[0].name == marker.name
    assert projection["summary"]["presence"] == "present" and projection["summary"]["absence_sha256"] is None
    assert projection["summary"]["applied_count"] == 0 and projection["unknown"]


def test_damaged_after_image_keeps_original_before_state_and_physical_files(completed):
    value, _intent, _receipt, path, _marker = completed
    document = json.loads(path.read_bytes())
    original_before = json.loads(document["before_json"])
    document["after_json"] = "{}"
    write(path, document)
    projection, records = captured(value.profile)
    assert len(records) == 2 and projection["unknown"]
    assert len(projection["states"]) == 1
    assert json.loads(private_images(value.profile)[0]["state_json"]) == original_before


def test_unknown_filename_remains_a_physical_input_and_never_becomes_absence(completed):
    value, _intent, _receipt, path, _marker = completed
    unexpected = path.parent / "unknown.json"
    unexpected.write_bytes(b"{}")
    if os.name != "nt":
        unexpected.chmod(0o600)
    projection, records = captured(value.profile)
    assert len(records) == 3 and len(projection["states"]) == 2
    assert projection["summary"]["presence"] == "present" and projection["unknown"]
    assert any(row["relative_key"].endswith("/unknown.json") and row["role"] == "legacy_pending_unknown"
        for row in projection["files"])


@pytest.mark.parametrize("limits,code", [
    (roots.CaptureLimits(max_files=1), "COUNT_LIMIT"),
    (roots.CaptureLimits(max_capsule_bytes=1), "FILE_LIMIT"),
    (roots.CaptureLimits(max_applied_bytes=1), "FILE_LIMIT"),
    (roots.CaptureLimits(max_total_bytes=1), "TOTAL_LIMIT"),
])
def test_actual_file_count_size_and_total_budgets_are_enforced(completed, limits, code):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    with pytest.raises(roots.PendingStateRootError) as raised:
        captured(value.profile, limits=limits)
    assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_" + code
    assert original_inputs(value, path, marker) == before


def test_absence_is_pinned_against_new_namespace_and_present_names_are_pinned(profile):
    directory = profile.database.parent / roots.DIRECTORY
    with lifecycle.shared(profile), pytest.raises(roots.PendingStateRootError) as raised:
        with roots.capture_locked(profile) as held:
            directory.mkdir(mode=0o700)
            held.verify_source()
    assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_SOURCE_CHANGED"
    with lifecycle.shared(profile), pytest.raises(roots.PendingStateRootError) as raised:
        with roots.capture_locked(profile) as held:
            (directory / "unexpected").write_bytes(b"x")
            held.verify_source()
    assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_SOURCE_CHANGED"


def test_summary_isolated_from_caller_mutation_and_closed_capture_cannot_be_reused(profile):
    with lifecycle.shared(profile), roots.capture_locked(profile) as held:
        original = held.summary
        borrowed = held.summary
        borrowed["presence"] = "present"
        assert held.summary == original
    with pytest.raises(roots.PendingStateRootError) as raised:
        _ = held.summary
    assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_CAPTURE_CLOSED"


def test_missing_sh_and_sql_write_frame_never_create_a_capture(profile, monkeypatch):
    with pytest.raises(lifecycle.StorageLifecycleError):
        with roots.capture_locked(profile):
            pytest.fail("Capture accepted a missing SH guard")
    with lifecycle.shared(profile):
        monkeypatch.setattr(roots.sql, "in_write_window", lambda: True)
        with pytest.raises(roots.PendingStateRootError) as raised:
            with roots.capture_locked(profile):
                pytest.fail("Capture performed long reads in a SQL writer")
        assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_READ_WINDOW_REQUIRED"


def test_short_commit_check_does_not_rehash_or_export_state_in_a_writer(completed, monkeypatch):
    value, _intent, _receipt, _path, _marker = completed
    with lifecycle.shared(value.profile), roots.capture_locked(value.profile) as held:
        with monkeypatch.context() as scoped:
            scoped.setattr(roots.sql, "in_write_window", lambda: True)
            scoped.setattr(held._reader, "read", lambda *a, **kw: pytest.fail("Short proof rehashed a file"))
            held.verify_for_commit()
            assert held.summary["file_count"] == 2
            with pytest.raises(roots.PendingStateRootError):
                _ = held.state_images


def test_copied_raw_sha_mismatch_is_rejected_instead_of_trusting_a_descriptor(completed):
    value, _intent, _receipt, _path, _marker = completed
    _projection, records = captured(value.profile)
    changed = (replace(records[0], sha256="f" * 64), *records[1:])
    with pytest.raises(roots.PendingStateRootError) as raised:
        roots.pure_projection(value.profile, presence="present", records=changed)
    assert raised.value.code == "LEGACY_PENDING_STATE_ROOT_FILE_LIMIT"
