"""New private fixtures, actual resources and guards; no H01 grant or SQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import sqlite3

import pytest

from reno import personal_authority_preservation_source as old
from reno import personal_authority_preservation_source3 as new
from reno import personal_authority_preservation3_contract as capsule
from reno import personal_authority_source_projection2 as projection
from reno import personal_preservation_files as private
from reno import storage_lifecycle as lifecycle, sync_media as safe

ROOT = ".reno-personal-authority-namespace-v3"
OP = "11111111-1111-4111-8111-111111111111"
LIMITS = new.RawCaptureLimits(max_read_bytes=8192, deadline_seconds=12)
ERROR = new.AuthorityRawCaptureError
ORIGINALS = {
    ".reno-personal-authority-origin-v1-reservation.json": b"\xff{invalid original\n",
    ".reno-personal-authority-origin-v1-observation.json": b" preserved \x00",
    ROOT + "-reservation.json": b" raw reservation\n",
    ROOT + "-header.json": b"raw header",
    ROOT + "/" + ROOT + "-root-seal.json": b"{broken root seal",
    ROOT + "/unknown.raw": b"\x00\xffopaque",
    "node-identity.json": b"not JSON; keep exact",
}


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value, timeout=0)
    return value


def root(profile, name=ROOT, lock=None):
    data = profile.database.parent
    with safe._Directory(data, base=data) as directory:
        private.mkdir_child(directory, name)
    path = data / name
    if lock is not None:
        with safe._Directory(path, base=data) as directory:
            private.publish(directory, "authority.lock", lock)
    return path


def populate(profile):
    path = root(profile)
    with safe._Directory(path, base=profile.database.parent) as directory:
        private.mkdir_child(directory, "empty")
    for relative, raw in ORIGINALS.items():
        (profile.database.parent / relative).write_bytes(raw)
    return path


@contextmanager
def capture(profile, **options):
    with lifecycle.exclusive(profile, timeout=0):
        with new.capture_raw3_locked(profile, operation_id=OP, limits=LIMITS, **options) as held:
            yield held


def test_actual_modern_source_preserves_every_original_and_empty_directory(profile, monkeypatch):
    path = populate(profile)
    def no_sql(*_args, **_kwargs):
        pytest.fail("raw capture must not open SQL")
    monkeypatch.setattr(sqlite3, "connect", no_sql)
    with capture(profile) as held:
        document = new.source_metadata(held)
        value = document["capsule"]
        assert document["format_version"] == 2
        assert value["format_version"] == 3 and value["coverage"]["declared_tree"] == "complete"
        assert {row["relative_key"] for row in value["files"]} == set(ORIGINALS)
        assert ROOT + "/empty" in {row["relative_key"] for row in value["directories"]}
        buffers = {row["entry_id"]: ORIGINALS[row["relative_key"]] for row in value["files"]}
        capsule.cross_validate_originals(value, source_bytes=buffers, copy_bytes={}, artifact_bytes={})
        assert capsule.decode(capsule.encode(value)) == value
        projection.cross_validate_capsule(document["source_projection"], value)
        for row in value["files"]:
            raw = ORIGINALS[row["relative_key"]]
            info = (profile.database.parent / row["relative_key"]).stat()
            assert row["sha256"] == hashlib.sha256(raw).hexdigest() and row["byte_size"] == len(raw)
            assert row["source_handle_identity"]["inode"] == str(info.st_ino)
        assert value["source_context"]["node"] is None and document["node_observation"]["state"] == "invalid"
    closed = new.closed_observation(held)
    assert closed["format_version"] == 2 and closed["source_file_count"] == len(ORIGINALS)
    assert closed["source_byte_size"] == sum(map(len, ORIGINALS.values())) and 0 < closed["read_bytes"] <= 8192
    assert not (path / "authority.lock").exists() and not profile.database.exists()
    with pytest.raises(ERROR):
        new.source_metadata(held)


def test_capabilities_are_separate_in_both_directions_even_after_close(profile):
    with lifecycle.exclusive(profile, timeout=0):
        with old.capture_raw_locked(profile, operation_id=OP, limits=LIMITS) as legacy:
            assert old.source_metadata(legacy)["capsule"]["format_version"] == 2
            for reader in (new.source_metadata, new.verify_source, new.closed_observation):
                with pytest.raises(ERROR):
                    reader(legacy)
        with new.capture_raw3_locked(profile, operation_id=OP, limits=LIMITS) as modern:
            assert new.source_metadata(modern)["capsule"]["format_version"] == 3
            for reader in (old.source_metadata, old.verify_source, old.closed_observation):
                with pytest.raises(ERROR):
                    reader(modern)
    assert old.closed_observation(legacy)["format_version"] == 1
    assert new.closed_observation(modern)["format_version"] == 2
    with pytest.raises(ERROR):
        new.closed_observation(legacy)
    with pytest.raises(ERROR):
        old.closed_observation(modern)


@pytest.mark.parametrize("damage", ["modern_bad_lock", "legacy_missing", "modern_alias", "unknown_origin"])
def test_missing_bad_and_unsupported_inputs_fail_without_initializing(profile, damage):
    if damage == "modern_bad_lock":
        path = root(profile, lock=b"invalid")
    elif damage == "legacy_missing":
        path = root(profile, name=".reno-personal-authority-namespace-v2")
    elif damage == "modern_alias":
        path = root(profile, name=ROOT.upper())
    else:
        path = profile.database.parent / ".reno-personal-authority-origin-v2-observation.json"
        path.write_bytes(b"must not vanish from success")
    with pytest.raises(ERROR):
        with capture(profile):
            pytest.fail("unsupported or unheld input cannot yield")
    assert path.exists() and not profile.database.exists()
    if path.is_dir() and damage != "modern_bad_lock":
        assert not (path / "authority.lock").exists()


def test_new_child_is_detected_before_any_closed_success(profile):
    path = root(profile)
    held = None
    with pytest.raises(ERROR):
        with capture(profile) as held:
            (path / "late.raw").write_bytes(b"late source")
    with pytest.raises(ERROR):
        new.closed_observation(held)


def test_present_valid_lock_and_original_read_limit_are_both_enforced(profile):
    path = root(profile, lock=b"\x00")
    with capture(profile) as held:
        assert new.source_metadata(held)["capsule"]["files"][0]["byte_size"] == 1
    assert new.closed_observation(held)["read_bytes"] > 1
    with lifecycle.exclusive(profile, timeout=0):
        with pytest.raises(ERROR) as failure:
            with new.capture_raw3_locked(profile, operation_id=OP,
                    limits=new.RawCaptureLimits(max_read_bytes=1, deadline_seconds=12)):
                pytest.fail("tiny read budget cannot be reset by lock cleanup")
        assert failure.value.code == "AUTHORITY_RAW_CAPTURE_READ_LIMIT"
    assert (path / "authority.lock").read_bytes() == b"\x00"


def test_callback_reentry_keeps_the_original_first_and_never_closes_success(profile):
    root(profile)
    current = [None]
    def callback():
        if current[0] is not None:
            new.source_metadata(current[0])
    with pytest.raises(ERROR) as failure:
        with capture(profile, check_cancelled=callback) as held:
            current[0] = held
            new.verify_source(held)
    assert failure.value.code == "AUTHORITY_RAW_CAPTURE_REENTRANT"
    with pytest.raises(ERROR):
        new.closed_observation(held)


@pytest.mark.parametrize("caller_first", [False, True])
def test_actual_close_then_simulated_failure_cannot_publish_success(profile, monkeypatch, caller_first):
    populate(profile)
    original = old._close_stream_original
    first = RuntimeError("synthetic caller first")
    def close_then_fail(stream):
        original(stream)
        raise RuntimeError("synthetic failure after owned close")
    monkeypatch.setattr(old, "_close_stream_original", close_then_fail)
    with pytest.raises(RuntimeError if caller_first else ERROR) as failure:
        with capture(profile) as held:
            if caller_first:
                raise first
    if caller_first:
        assert failure.value is first
    else:
        assert failure.value.code == "AUTHORITY_RAW_CAPTURE_CLOSE_FAILED"
    with pytest.raises(ERROR):
        new.closed_observation(held)


def test_shared_guard_cannot_upgrade_and_actual_excludes_other_owner(profile):
    root(profile)
    with lifecycle.shared(profile, timeout=0):
        with pytest.raises(lifecycle.StorageLifecycleError):
            with new.capture_raw3_locked(profile, operation_id=OP, limits=LIMITS):
                pytest.fail("SH cannot grant EX capture")
    def competitor():
        try:
            with lifecycle.exclusive(profile, timeout=0):
                return "entered"
        except lifecycle.StorageLifecycleError as failure:
            return failure.code
    with capture(profile) as held:
        assert new.source_metadata(held)["capsule"]["format_version"] == 3
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(competitor).result(timeout=3) == "STORAGE_BUSY"
