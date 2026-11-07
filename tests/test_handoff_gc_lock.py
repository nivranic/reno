"""Real fresh GC captures; raw fixture bytes never represent a valid grant."""
import sqlite3

import pytest

from reno import personal_authority_files as primitives
from reno import personal_preservation_files as private
from reno import storage_personal_authority_roots as census
from reno import sync_media as safe
from tests.test_namespace3_gc_census import profile, captured, tree as old_tree, MODERN, LEGACY  # noqa: F401


HANDOFF = '.reno-personal-authority-namespace-handoff-v1'


def handoff_tree(profile, *, name=HANDOFF, lock=b'\0', old_lock=False):
    data = profile.database.parent
    with safe._Directory(data, base=data) as parent:
        private.mkdir_child(parent, name)
    path = data / name
    with safe._Directory(path, base=data) as directory:
        if lock is not None:
            private.publish(directory, 'handoff.lock', lock)
        if old_lock:
            private.publish(directory, 'authority.lock', b'\0')
    # GC preserves opaque files and must never infer an authority declaration.
    (path / 'format.json').write_bytes(b'opaque original; not validated authority')
    slot = path / ('handoff-pending-' + 'a' * 64)
    slot.mkdir()
    (slot / 'pending.json').write_bytes(b'opaque pending fixture; not a grant')
    return path


def identity(path):
    info = path.stat(follow_symlinks=False)
    return str(info.st_dev), str(info.st_ino)


def test_handoff_capture_holds_real_named_lock_and_protects_originals(profile, monkeypatch):
    root = handoff_tree(profile)
    def no_sql(*_args, **_options):
        pytest.fail('Raw GC observation must not open SQL or establish authority')
    monkeypatch.setattr(sqlite3, 'connect', no_sql)
    with captured(profile) as (_group, view):
        assert view.complete and not view.inputs.blockers
        binding = view.source_binding['tree']
        assert binding['algorithm_version'] == 4
        assert binding['required_lock_names'] == [[HANDOFF, 'handoff.lock']]
        assert 'profile_locked_roots' not in binding
        assert root in view.protected_directories
        assert root / 'handoff.lock' in view.protected_paths
        assert root / 'format.json' in view.protected_paths
        assert root / ('handoff-pending-' + 'a' * 64) / 'pending.json' in view.protected_paths
        assert identity(root / 'handoff.lock') in view.protected_inodes
        view.verify_source()
    assert not (root / 'authority.lock').exists()
    assert not profile.database.exists()
    with pytest.raises(census.PersonalAuthorityRootError):
        view.verify_source()


@pytest.mark.parametrize('lock', [None, b'x'])
def test_handoff_missing_or_bad_named_lock_blocks_entire_group(profile, lock):
    root = handoff_tree(profile, lock=lock)
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers
        assert root in view.protected_directories
        assert view.source_binding['tree']['algorithm_version'] == 4
    if lock is None:
        assert not (root / 'handoff.lock').exists()
    assert not (root / 'authority.lock').exists()


def test_old_authority_lock_cannot_replace_required_handoff_lock(profile):
    root = handoff_tree(profile, lock=None, old_lock=True)
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers
        assert root in view.protected_directories
    assert not (root / 'handoff.lock').exists()


def test_case_alias_does_not_receive_new_root_lock_rule(profile):
    root = handoff_tree(profile, name=HANDOFF.upper())
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers
        assert root in view.protected_directories
        assert view.source_binding['tree']['algorithm_version'] == 2


def test_legacy_root_still_requires_original_authority_lock(profile):
    root = handoff_tree(profile, name=LEGACY)
    with captured(profile) as (_group, view):
        assert not view.complete and view.inputs.blockers
        assert view.source_binding['tree']['algorithm_version'] == 2
        assert root in view.protected_directories
    assert not (root / 'authority.lock').exists()


def test_mixed_modern_and_handoff_binding_keeps_each_lock_obligation(profile):
    old_tree(profile)
    handoff_tree(profile)
    with captured(profile) as (_group, view):
        assert view.complete
        binding = view.source_binding['tree']
        assert binding['algorithm_version'] == 4
        assert binding['profile_locked_roots'] == [MODERN]
        assert binding['required_lock_names'] == [[HANDOFF, 'handoff.lock']]
        view.verify_source()


def test_busy_named_lock_is_unknown_then_fresh_capture_can_succeed(profile):
    root = handoff_tree(profile)
    with safe._Directory(root, base=profile.database.parent) as directory:
        with primitives._ExistingLock(directory, exclusive=True, timeout=0,
                check=lambda: None, lock_name='handoff.lock'):
            with captured(profile) as (_group, view):
                assert not view.complete and view.inputs.blockers
                assert root in view.protected_directories
    with captured(profile) as (_group, fresh):
        assert fresh.complete and not fresh.inputs.blockers
        fresh.verify_source()


def test_actual_ex_delete_boundary_protects_handoff_originals(profile):
    root = handoff_tree(profile)
    outside = profile.media / 'ordinary.raw'
    outside.write_bytes(b'ordinary raw data')
    pending = root / ('handoff-pending-' + 'a' * 64) / 'pending.json'
    with captured(profile, exclusive=True) as (group, view):
        assert view.complete
        for path in [root / 'handoff.lock', pending]:
            with pytest.raises(census.PersonalAuthorityRootError) as caught:
                census.assert_unprotected(group, profile=profile, path=path, identity=identity(path))
            assert caught.value.code == 'PERSONAL_AUTHORITY_ROOT_PROTECTED'
        assert census.assert_unprotected(group, profile=profile, path=outside, identity=identity(outside)) is None
    assert outside.read_bytes() == b'ordinary raw data'


def test_lock_name_accepts_only_two_exact_names(profile):
    root = handoff_tree(profile)
    with safe._Directory(root, base=profile.database.parent) as directory:
        for name in ['../authority.lock', 'HANDOFF.LOCK', 'other.lock', b'handoff.lock']:
            with pytest.raises(primitives.FilePrimitiveError) as caught:
                primitives._ExistingLock(directory, exclusive=False, timeout=0,
                    check=lambda: None, lock_name=name)
            assert caught.value.code == 'FILES_LOCK_OPTIONS_INVALID'
