"""Real fresh profile locks and held filesystem objects; no H01 grant fixture."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3

import pytest

from reno import personal_preservation_files as private
from reno import storage_lifecycle as lifecycle
from reno import storage_personal_authority_roots as census
from reno import sync_media as safe


MODERN = ".reno-personal-authority-namespace-v3"
LEGACY = ".reno-personal-authority-namespace-v2"
ANCHORS = (
    ".reno-personal-authority-origin-v1-reservation.json",
    ".reno-personal-authority-origin-v1-observation.json",
    ".reno-personal-authority-namespace-v3-reservation.json",
    ".reno-personal-authority-namespace-v3-header.json",
)


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value)
    return value


def tree(profile, name=MODERN, lock=None):
    data = profile.database.parent
    with safe._Directory(data, base=data) as directory:
        private.mkdir_child(directory, name)
    root = data / name
    if lock is not None:
        with safe._Directory(root, base=data) as directory:
            private.publish(directory, "authority.lock", lock)
    (root / "opaque-stage.raw").write_bytes(b"synthetic raw bytes; not a grant")
    return root


def anchors(profile):
    for name in ANCHORS:
        (profile.database.parent / name).write_bytes(b"opaque synthetic original")


@contextmanager
def captured(profile, *, exclusive=False, check_cancelled=None):
    owner = lifecycle.exclusive if exclusive else lifecycle.shared
    with owner(profile, timeout=0):
        with census.capture_group_locked((profile,), check_cancelled=check_cancelled) as group:
            yield group, group.capture_for(profile)


def test_modern_root_without_legacy_lock_uses_held_profile_guard(profile, monkeypatch):
    root = tree(profile)
    anchors(profile)
    def no_sql(*_args, **_kwargs):
        pytest.fail("GC raw observation must not open SQL")
    monkeypatch.setattr(sqlite3, "connect", no_sql)
    with captured(profile) as (_group, view):
        assert view.complete and not view.inputs.blockers
        assert view.source_binding["tree"]["algorithm_version"] == 3
        assert view.source_binding["tree"]["profile_locked_roots"] == [MODERN]
        assert root in view.protected_directories
        assert set(profile.database.parent / name for name in ANCHORS) <= set(view.protected_paths)
        assert root / "opaque-stage.raw" in view.protected_paths
        view.verify_source()
    assert not (root / "authority.lock").exists()
    assert not profile.database.exists()
    with pytest.raises(census.PersonalAuthorityRootError):
        view.verify_source()


@pytest.mark.parametrize("name", ANCHORS)
def test_modern_anchor_without_actual_root_remains_unknown(profile, name):
    path = profile.database.parent / name
    path.write_bytes(b"orphan raw original")
    with captured(profile) as (_group, view):
        assert not view.complete
        assert view.inputs.blockers
        assert path in view.protected_paths


@pytest.mark.parametrize("name", [MODERN.upper(), ".reno-personal-authority-namespace-v4"])
def test_alias_and_unknown_root_cannot_use_modern_lock_exception(profile, name):
    root = tree(profile, name)
    with captured(profile) as (_group, view):
        assert not view.complete
        assert root in view.protected_directories


@pytest.mark.parametrize("lock", [None, b"not-the-original-lock-byte"])
def test_legacy_missing_or_bad_lock_keeps_original_unknown_rule(profile, lock):
    root = tree(profile, LEGACY, lock)
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers
        assert root in view.protected_directories


def test_modern_present_bad_lock_is_not_ignored(profile):
    tree(profile, lock=b"invalid")
    anchors(profile)
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers


def test_existing_valid_lock_is_still_held_and_verified(profile):
    root = tree(profile, lock=b"\x00")
    with captured(profile) as (_group, view):
        assert view.complete
        assert root / "authority.lock" in view.protected_paths
        view.verify_source()


def test_legacy_binding_keeps_algorithm2(profile):
    tree(profile, LEGACY, b"\x00")
    with captured(profile) as (_group, view):
        assert view.complete
        binding = view.source_binding["tree"]
        assert binding["algorithm_version"] == 2
        assert "profile_locked_roots" not in binding


def test_unknown_anchor_is_not_adopted_by_held_modern_root(profile):
    tree(profile)
    path = profile.database.parent / ".reno-personal-authority-namespace-v3-unrecognized.json"
    path.write_bytes(b"unknown exact file")
    with captured(profile) as (_group, view):
        assert not view.complete
        assert path in view.protected_paths


def test_exact_anchor_case_is_required_for_new_association(profile):
    tree(profile)
    path = profile.database.parent / ANCHORS[0].upper()
    path.write_bytes(b"noncanonical spelling")
    with captured(profile) as (_group, view):
        assert not view.complete
        assert path in view.protected_paths


def test_new_child_is_detected_on_normal_scope_exit(profile):
    root = tree(profile)
    with pytest.raises(census.PersonalAuthorityRootError):
        with captured(profile) as (_group, view):
            assert view.complete
            (root / "late.raw").write_bytes(b"same-owner mutation")


def test_anchor_replacement_is_detected_or_prevented(profile):
    tree(profile)
    anchors(profile)
    path = profile.database.parent / ANCHORS[0]
    changed = False
    try:
        with captured(profile) as (_group, view):
            assert view.complete
            try:
                path.write_bytes(b"changed raw source bytes")
                changed = True
            except PermissionError:
                view.verify_source()  # Native sharing prevented the change.
    except census.PersonalAuthorityRootError:
        assert changed
    else:
        assert not changed


def test_shared_capture_still_cannot_cross_exclusive_delete_boundary(profile):
    tree(profile)
    path = profile.media / "ordinary.raw"
    path.write_bytes(b"outside authority tree")
    info = path.stat()
    with captured(profile) as (group, view):
        assert view.complete
        with pytest.raises(lifecycle.StorageLifecycleError):
            census.assert_unprotected(group, profile=profile, path=path,
                                      identity=(str(info.st_dev), str(info.st_ino)))


def test_exclusive_boundary_protects_modern_child_and_top_anchor(profile):
    root = tree(profile)
    anchors(profile)
    outside = profile.media / "ordinary.raw"
    outside.write_bytes(b"outside authority tree")
    with captured(profile, exclusive=True) as (group, view):
        assert view.complete
        info = outside.stat()
        assert census.assert_unprotected(group, profile=profile, path=outside,
            identity=(str(info.st_dev), str(info.st_ino))) is None
        for path in [root / "opaque-stage.raw", profile.database.parent / ANCHORS[0]]:
            info = path.stat()
            with pytest.raises(census.PersonalAuthorityRootError) as raised:
                census.assert_unprotected(group, profile=profile, path=path,
                    identity=(str(info.st_dev), str(info.st_ino)))
            assert raised.value.code == "PERSONAL_AUTHORITY_ROOT_PROTECTED"
    assert outside.read_bytes() == b"outside authority tree"  # No delete was requested.


def test_modern_reader_excludes_other_owner_ex_writer(profile):
    tree(profile)
    def writer():
        try:
            with lifecycle.exclusive(profile, timeout=0):
                return "entered"
        except lifecycle.StorageLifecycleError as error:
            return error.code
    with captured(profile) as (_group, view):
        assert view.complete
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(writer).result(timeout=3) == "STORAGE_BUSY"
    with lifecycle.exclusive(profile, timeout=0):
        pass
