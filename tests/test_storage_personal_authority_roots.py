"""Permanent authority exclusions on newly created isolated storage profiles.

The raw trees here are synthetic fixtures, never installed H01 grants.  The
collector, lifecycle locks, membership, artifact registration and GC journals
are real.  Run with pre-collection RENO isolation and --noconftest.
"""
from contextlib import contextmanager
from copy import copy
from dataclasses import replace
import errno
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from reno import personal_authority_files as primitives
from reno import personal_preservation_files as private_files
from reno import storage_artifacts as artifacts, storage_gc as gc
from reno import storage_lifecycle as lifecycle, storage_roots as roots
from reno import storage_personal_authority_roots as authority
from reno import sync_media as safe
from tests.test_storage_roots_group import libraries, enroll, registered, scan, statuses  # noqa: F401


NAMESPACE = ".reno-personal-authority-namespace-v2"
ANCHOR = ".reno-personal-authority-namespace-v2-reservation.json"


def _data(env):
    return env["profile"].database.parent


def _write(path, raw=b"synthetic raw evidence"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _private_tree_at(data, name=NAMESPACE, *, lock=True):
    """Only new fixture paths receive the original private-file setup."""
    data.mkdir(parents=True, exist_ok=True)
    with safe._Directory(data, base=data) as parent:
        private_files.mkdir_child(parent, name)
    path = data / name
    if lock:
        with safe._Directory(path, base=data) as directory:
            private_files.publish(directory, "authority.lock", b"\x00")
    return path


def _tree(env, name=NAMESPACE, *, lock=True):
    return _private_tree_at(_data(env), name, lock=lock)


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _identity(path):
    value = path.stat(follow_symlinks=False)
    return str(value.st_dev), str(value.st_ino)


@pytest.fixture
def authority_env(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    with lifecycle.shared(profile):
        ids = {name: artifacts.root_identity(getattr(profile, roots._ROOT_ROLE[name]), profile=profile)["root_id"]
               for name in roots.NAMESPACES}
    return {"profile": profile, "root_ids": ids, "member_id": "mem_" + "a" * 64}


@contextmanager
def _capture(env, **options):
    with lifecycle.shared(env["profile"]):
        with authority.capture_locked(env["profile"], root_ids=env["root_ids"],
                member_id=env["member_id"], **options) as value:
            yield value


def _preview(env):
    return roots.preview([env["profile"]])["preview_sha256"]


def _apply(env, *, identifier=None, digest=None):
    return gc.apply([env["profile"]], operation_id=identifier or str(uuid.uuid4()),
        expected_preview_sha256=digest or _preview(env))


def _op(env, identifier):
    return dict(env["con"].execute("SELECT * FROM storage_gc_operation WHERE operation_id=?", (identifier,)).fetchone())


def _plan_without_execute(env, monkeypatch):
    identifier = str(uuid.uuid4())
    original = gc._execute
    failure = RuntimeError("synthetic stop after durable GC journal")
    with monkeypatch.context() as patch:
        def stop(*_args, **_options):
            raise failure
        patch.setattr(gc, "_execute", stop)
        with pytest.raises(RuntimeError) as raised:
            _apply(env, identifier=identifier)
        assert raised.value is failure
    assert gc._execute is original
    return identifier, _op(env, identifier)["request_json"]


def test_absent_capture_is_read_only_and_does_not_open_sql(authority_env, monkeypatch):
    before = {path.name for path in _data(authority_env).iterdir()}
    def forbidden(*_args, **_options):
        pytest.fail("Authority exclusion observation must not create SQL or initialize a facility")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(primitives, "initialize", forbidden)
    with _capture(authority_env) as value:
        assert value.complete and not value.inputs.blockers
        assert not value.protected_paths and not value.protected_directories
        assert not value.protected_inodes
        assert not value.inputs.references and not value.inputs.selectors
        value.verify_source()
    assert before == {path.name for path in _data(authority_env).iterdir()}


@pytest.mark.skipif(os.name != "nt", reason="Windows native directory handle duplication ownership")
@pytest.mark.parametrize("fail_fstat", [False, True])
def test_windows_directory_identity_closes_only_its_duplicate_fd(authority_env, monkeypatch, fail_fstat):
    data = _data(authority_env)
    original_fstat = os.fstat
    descriptors = []
    failure = OSError(errno.EIO, "synthetic owned duplicate fstat failure")

    def observed_fstat(descriptor):
        descriptors.append(descriptor)
        if fail_fstat:
            raise failure
        return original_fstat(descriptor)

    with safe._Directory(data, base=data) as directory:
        original_handle = directory.handles[-1]
        with monkeypatch.context() as patch:
            patch.setattr(os, "fstat", observed_fstat)
            if fail_fstat:
                with pytest.raises(OSError) as raised:
                    authority._directory_identity(directory)
                assert raised.value is failure
            else:
                assert authority._directory_identity(directory) == _identity(data)
        assert descriptors
        assert directory.handles[-1] == original_handle
        directory.verify()
        native = safe._HandleInfo()
        assert safe._kernel.GetFileInformationByHandle(original_handle, safe.ctypes.byref(native))
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                original_fstat(descriptor)
            assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("name", [ANCHOR, ".RENO-PERSONAL-AUTHORITY-future.json", "NODE-IDENTITY.JSON"])
def test_absence_window_rejects_new_selected_file(authority_env, name):
    with pytest.raises(authority.PersonalAuthorityRootError, match="SOURCE_CHANGED"):
        with _capture(authority_env) as value:
            assert value.complete
            _write(_data(authority_env) / name)
            value.verify_source()


def test_absence_window_rejects_new_empty_selected_directory(authority_env):
    with pytest.raises(authority.PersonalAuthorityRootError, match="SOURCE_CHANGED"):
        with _capture(authority_env):
            (_data(authority_env) / NAMESPACE).mkdir()


def test_unrelated_data_changes_do_not_invalidate_authority_observation(authority_env):
    with _capture(authority_env) as value:
        before = value.source_binding
        _write(_data(authority_env) / "unrelated-gc-journal.json", b"unrelated")
        value.verify_source()
        assert value.source_binding == before and value.complete


def test_bad_node_is_raw_protection_without_json_or_grant_interpretation(authority_env):
    path = _write(_data(authority_env) / "node-identity.json", b"not valid JSON")
    with _capture(authority_env) as value:
        assert value.complete and not value.inputs.blockers
        assert _key(path) in {_key(item) for item in value.protected_paths}
        assert _identity(path) in value.protected_inodes
        assert b"not valid JSON" not in json.dumps(value.source_binding).encode()


@pytest.mark.parametrize("name", [NAMESPACE, ".RENO-PERSONAL-AUTHORITY-unknown"])
def test_full_tree_protects_unknown_stage_journals_and_empty_directories(authority_env, name):
    tree = _tree(authority_env, name)
    directories = [tree, tree / "empty", tree / "facts", tree / "journal-v1",
                   tree / "journal-v3", tree / "journal-enrollment-v1"]
    for directory in directories:
        directory.mkdir(exist_ok=True)
    files = [tree / "authority.lock"]
    for directory in directories[2:]:
        files.append(_write(directory / "unknown.bin", b"PRIVATE_RAW_PAYLOAD"))
    files.append(_write(tree / ".stage-unpublished.tmp", b"incomplete raw body"))
    with _capture(authority_env) as value:
        assert value.complete and not value.inputs.blockers
        assert {_key(path) for path in files} <= {_key(path) for path in value.protected_paths}
        assert {_key(path) for path in directories} <= {_key(path) for path in value.protected_directories}
        assert {_identity(path) for path in files + directories} <= value.protected_inodes
        assert value.file_count >= len(files) and value.directory_count >= len(directories)
        assert "PRIVATE_RAW_PAYLOAD" not in json.dumps(value.source_binding)


def test_selected_tree_name_changes_are_checked_on_normal_close(authority_env):
    tree = _tree(authority_env)
    with pytest.raises(authority.PersonalAuthorityRootError, match="SOURCE_CHANGED"):
        with _capture(authority_env):
            _write(tree / "new-stage")


@pytest.mark.parametrize("subtree", ["journal-v3", ".unpublished-stage"])
def test_nested_authority_lock_name_is_opaque_raw_evidence(authority_env, subtree):
    tree = _tree(authority_env)
    nested = _write(tree / subtree / "authority.lock", b"not a lock, opaque bytes")
    with _capture(authority_env) as value:
        assert value.complete and not value.inputs.blockers
        assert _key(nested) in {_key(path) for path in value.protected_paths}
        assert _identity(nested) in value.protected_inodes
        value.verify_source()
    assert nested.read_bytes() == b"not a lock, opaque bytes"


@pytest.mark.parametrize("change", ["rewrite", "replace"])
def test_held_node_rejects_or_detects_real_file_mutation(authority_env, change):
    path = _write(_data(authority_env) / "node-identity.json", b"original raw node")
    replacement = _write(_data(authority_env) / "ordinary-replacement", b"replacement raw node")
    changed = False
    denied = False
    try:
        with _capture(authority_env) as value:
            try:
                if change == "rewrite":
                    path.write_bytes(b"different raw node with different size")
                else:
                    os.replace(replacement, path)
                changed = True
            except PermissionError:
                # A real Windows pinned handle may deny mutation outright.
                denied = True
                assert path.read_bytes() == b"original raw node"
            value.verify_source()
    except authority.PersonalAuthorityRootError as exc:
        assert changed and exc.code.endswith("SOURCE_CHANGED")
    else:
        assert denied and not changed


@pytest.mark.parametrize("lock", ["missing", "invalid"])
def test_missing_or_bad_authority_lock_is_unknown_but_paths_remain_protected(authority_env, lock):
    tree = _tree(authority_env, lock=False)
    evidence = _write(tree / "actual-evidence", b"retained")
    if lock == "invalid":
        _write(tree / "authority.lock", b"wrong marker")
    with _capture(authority_env) as value:
        assert not value.complete and value.inputs.blockers
        assert _key(tree) in {_key(path) for path in value.protected_directories}
        assert _key(evidence) in {_key(path) for path in value.protected_paths}
        assert _identity(evidence) in value.protected_inodes


def test_original_authority_lock_is_held_shared_for_observation_lifetime(authority_env):
    tree = _tree(authority_env)
    with _capture(authority_env) as value:
        assert value.complete
        with safe._Directory(tree, base=_data(authority_env)) as directory:
            with pytest.raises(primitives.FilePrimitiveError, match="FILES_LOCK_BUSY"):
                with primitives._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
                    pytest.fail("An exclusive authority writer acquired while collector holds SH")
    with safe._Directory(tree, base=_data(authority_env)) as directory:
        with primitives._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
            pass


def test_existing_exclusive_authority_owner_prevents_complete_capture(authority_env):
    tree = _tree(authority_env)
    with safe._Directory(tree, base=_data(authority_env)) as directory:
        with primitives._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
            with _capture(authority_env) as value:
                assert not value.complete and value.inputs.blockers


def test_hardlinked_reserved_file_is_unknown_and_retains_known_inode(authority_env):
    path = _write(_data(authority_env) / ANCHOR)
    alias = _data(authority_env) / "ordinary-alias"
    os.link(path, alias)
    with _capture(authority_env) as value:
        assert not value.complete and value.inputs.blockers
        assert _identity(path) in value.protected_inodes
        assert _key(path) in {_key(item) for item in value.protected_paths}
    assert alias.read_bytes() == path.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture; Windows reparse requires separate platform setup")
def test_symlink_is_not_followed_or_treated_as_absence(authority_env, tmp_path):
    outside = _write(tmp_path / "outside" / "private", b"do not read")
    selected = _data(authority_env) / ANCHOR
    selected.symlink_to(outside)
    with _capture(authority_env) as value:
        assert not value.complete and value.inputs.blockers
        assert _key(selected) in {_key(path) for path in value.protected_paths}
        assert _identity(selected) in value.protected_inodes
        assert _identity(outside) not in value.protected_inodes


@pytest.mark.parametrize("field", ["max_entries", "max_directories", "max_metadata_bytes"])
def test_limits_do_not_produce_false_complete_observation(authority_env, field):
    tree = _tree(authority_env)
    _write(tree / "one")
    _write(tree / "two")
    (tree / "child").mkdir()
    limits = replace(authority.PersonalAuthorityLimits(), **{field: 1})
    try:
        with _capture(authority_env, limits=limits) as value:
            assert not value.complete and value.inputs.blockers
    except authority.PersonalAuthorityRootError as exc:
        assert "LIMIT" in exc.code or "SOURCE_CHANGED" in exc.code


def test_deadline_exhaustion_never_yields_complete_observation(authority_env):
    limits = replace(authority.PersonalAuthorityLimits(), deadline_seconds=1e-12)
    try:
        with _capture(authority_env, limits=limits) as value:
            assert not value.complete and value.inputs.blockers
    except authority.PersonalAuthorityRootError as exc:
        assert "LIMIT" in exc.code or "SOURCE_CHANGED" in exc.code


@pytest.mark.parametrize("field", ["max_entries", "max_directories", "max_metadata_bytes", "deadline_seconds"])
@pytest.mark.parametrize("invalid", [True, 0, -1])
def test_limits_reject_invalid_scalar_types(field, invalid):
    with pytest.raises(ValueError):
        replace(authority.PersonalAuthorityLimits(), **{field: invalid})


@pytest.mark.parametrize("kind", [OSError, ValueError, RuntimeError])
@pytest.mark.parametrize("phase", ["initial", "final"])
def test_cancellation_retains_original_exception_identity(authority_env, kind, phase):
    failure = kind("synthetic callback cancellation")
    armed = [phase == "initial"]
    def stop():
        if armed[0]:
            raise failure
    with pytest.raises(kind) as caught:
        with _capture(authority_env, check_cancelled=stop):
            armed[0] = True
    assert caught.value is failure


def test_closed_and_copied_capture_cannot_be_reused(authority_env):
    with _capture(authority_env) as value:
        try:
            copied = copy(value)
        except (TypeError, authority.PersonalAuthorityRootError):
            copied = None
        if copied is not None:
            assert copied is not value
            with pytest.raises(authority.PersonalAuthorityRootError):
                authority.verify_capture(copied)
    with pytest.raises(authority.PersonalAuthorityRootError):
        authority.verify_capture(value)


def test_capture_cross_thread_and_wrong_profile_are_rejected(authority_env, tmp_path):
    other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
        tmp_path / "other/reports", tmp_path / "other/checkpoints")
    with _capture(authority_env) as value:
        with pytest.raises(authority.PersonalAuthorityRootError):
            authority.verify_capture(value, profile=other)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(authority.verify_capture, value)
            with pytest.raises(authority.PersonalAuthorityRootError):
                result.result(timeout=5)


def test_shared_capture_does_not_satisfy_exclusive_delete_boundary(authority_env):
    with _capture(authority_env) as value:
        with pytest.raises((authority.PersonalAuthorityRootError, lifecycle.StorageLifecycleError)):
            authority.verify_capture(value, exclusive=True)


def test_detached_source_binding_cannot_change_observed_state(authority_env):
    _write(_data(authority_env) / ANCHOR)
    with _capture(authority_env) as value:
        expected = value.source_binding
        changed = value.source_binding
        changed.clear()
        assert value.source_binding == expected
        value.verify_source()


def test_group_requires_real_owner_and_rejects_closed_or_forged_token(authority_env):
    profile = authority_env["profile"]
    ordinary = _write(profile.media / "ordinary")
    with lifecycle.exclusive(profile):
        with authority.capture_group_locked([profile]) as group:
            authority.verify_group(group, profiles=[profile], exclusive=True)
            authority.assert_unprotected(group, profile=profile, path=ordinary, identity=_identity(ordinary))
            with pytest.raises(authority.PersonalAuthorityRootError):
                authority.verify_group(object.__new__(authority.AuthorityProtectionGroup))
        with pytest.raises(authority.PersonalAuthorityRootError):
            authority.assert_unprotected(group, profile=profile, path=ordinary, identity=_identity(ordinary))


def test_group_delete_check_rejects_actual_protected_inode(authority_env):
    profile = authority_env["profile"]
    node = _write(_data(authority_env) / "node-identity.json", b"raw node")
    with lifecycle.exclusive(profile), authority.capture_group_locked([profile]) as group:
        with pytest.raises(authority.PersonalAuthorityRootError, match="PROTECTED"):
            authority.assert_unprotected(group, profile=profile, path=node, identity=_identity(node))
        # An alternative path cannot hide a physical identity already protected.
        with pytest.raises(authority.PersonalAuthorityRootError, match="PROTECTED"):
            authority.assert_unprotected(group, profile=profile, path=profile.media / "alias", identity=_identity(node))


def test_group_refuses_incomplete_profile_coverage(authority_env, tmp_path):
    profile = authority_env["profile"]
    other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
        tmp_path / "other/reports", tmp_path / "other/checkpoints")
    lifecycle.initialize(other)
    ordinary = _write(profile.media / "ordinary")
    with lifecycle.exclusive_many([profile, other]):
        with authority.capture_group_locked([profile]) as group:
            with pytest.raises(authority.PersonalAuthorityRootError, match="GROUP_INCOMPLETE"):
                authority.assert_unprotected(group, profile=profile, path=ordinary, identity=_identity(ordinary))


def test_group_unknown_foreign_tree_refuses_other_profile_delete(authority_env, tmp_path):
    profile = authority_env["profile"]
    other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
        tmp_path / "other/reports", tmp_path / "other/checkpoints")
    lifecycle.initialize(other)
    _private_tree_at(other.database.parent, lock=False)
    ordinary = _write(profile.media / "ordinary")
    with lifecycle.exclusive_many([profile, other]):
        with authority.capture_group_locked([profile, other]) as group:
            with pytest.raises(authority.PersonalAuthorityRootError, match="UNKNOWN"):
                authority.assert_unprotected(group, profile=profile, path=ordinary, identity=_identity(ordinary))


def test_two_profiles_sharing_data_keep_one_consistent_authority_observation(authority_env):
    profile = authority_env["profile"]
    other = lifecycle.StorageProfile(profile.database.with_name("second.db"), profile.media,
        profile.reports, profile.checkpoints)
    lifecycle.initialize(other)
    tree = _tree(authority_env)
    raw = _write(tree / "shared-original", b"one physical DATA tree")
    with lifecycle.exclusive_many([profile, other]):
        with authority.capture_group_locked([profile, other]) as group:
            first, second = group.capture_for(profile), group.capture_for(other)
            assert first.complete and second.complete
            assert first.source_binding["tree"] == second.source_binding["tree"]
            assert first.protected_inodes == second.protected_inodes
            assert _identity(raw) in first.protected_inodes
            authority.verify_group(group, profiles=[profile, other], exclusive=True)
            with safe._Directory(tree, base=_data(authority_env)) as directory:
                with pytest.raises(primitives.FilePrimitiveError, match="FILES_LOCK_BUSY"):
                    with primitives._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
                        pytest.fail("Shared DATA observation released its actual authority SH lock")
    with safe._Directory(tree, base=_data(authority_env)) as directory:
        with primitives._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
            pass
    assert raw.read_bytes() == b"one physical DATA tree"


@pytest.mark.parametrize("cross_profile", [False, True])
def test_callback_after_data_listing_cannot_add_an_unchecked_authority_root(authority_env, tmp_path, monkeypatch, cross_profile):
    profile = authority_env["profile"]
    _write(profile.database.parent / "node-identity.json", b"raw node ensures a later file poll")
    ordinary = _write(profile.media / "ordinary")
    profiles = [profile]
    trigger_data = profile.database.parent
    if cross_profile:
        other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
            tmp_path / "other/reports", tmp_path / "other/checkpoints")
        lifecycle.initialize(other)
        _write(other.database.parent / "node-identity.json", b"second-profile file poll")
        profiles.append(other)
        trigger_data = other.database.parent
    trigger_identity = _identity(trigger_data)
    original_scandir = os.scandir
    in_assert, after_listing, mutations = [False], [False], []

    @contextmanager
    def watched_scandir(path):
        info = os.fstat(path) if type(path) is int else os.stat(path, follow_symlinks=False)
        selected = (str(info.st_dev), str(info.st_ino)) == trigger_identity
        with original_scandir(path) as entries:
            yield entries
        if in_assert[0] and selected:
            after_listing[0] = True

    def late_mutation():
        if after_listing[0] and not mutations:
            mutations.append(_write(profile.database.parent / ANCHOR, b"introduced by the last-profile callback"))

    with lifecycle.exclusive_many(profiles):
        with pytest.raises(authority.PersonalAuthorityRootError, match="SOURCE_CHANGED"):
            with authority.capture_group_locked(profiles, check_cancelled=late_mutation) as group:
                monkeypatch.setattr(os, "scandir", watched_scandir)
                in_assert[0] = True
                authority.assert_unprotected(group, profile=profile, path=ordinary, identity=_identity(ordinary))
                pytest.fail("A late callback introduced a reserved root after its earlier listing")
    assert mutations and mutations[0].read_bytes() == b"introduced by the last-profile callback"
    assert ordinary.exists()


def test_node_only_library_can_still_collect_unrelated_candidate(libraries):
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    node = _data(env) / "node-identity.json"
    before = node.read_bytes()
    result = _apply(env)
    assert result["state"] == "completed" and result["deleted_bytes"] == item["byte_size"]
    assert not candidate.exists() and node.read_bytes() == before


def test_complete_unknown_raw_tree_does_not_block_unrelated_gc(libraries):
    env = libraries()
    enroll(env)
    tree = _tree(env)
    raw = _write(tree / "unknown-raw", b"permanent evidence")
    candidate, item = registered(env)
    result = _apply(env)
    assert result["state"] == "completed" and result["deleted_bytes"] == item["byte_size"]
    assert not candidate.exists() and raw.read_bytes() == b"permanent evidence"


def test_actual_registered_file_beneath_authority_tree_is_protected(libraries, tmp_path):
    tree = _private_tree_at(tmp_path / "a/data")
    checkpoints = tree / "journal-v3"
    checkpoints.mkdir()
    env = libraries(checkpoints=checkpoints)
    enroll(env)
    candidate, item = registered(env, "checkpoint")
    before = candidate.read_bytes()
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "protected"
    assert not result.eligible_artifacts
    receipt = _apply(env)
    assert receipt["selected_count"] == 0 and receipt["deleted_bytes"] == 0
    assert candidate.read_bytes() == before


def test_reserved_metadata_changes_invalidate_preview_before_journal(libraries):
    env = libraries()
    enroll(env)
    candidate, _ = registered(env)
    digest = _preview(env)
    _write(_data(env) / ANCHOR)
    with pytest.raises(gc.GCError, match="GC_PREVIEW_CHANGED"):
        _apply(env, digest=digest)
    assert candidate.exists()
    assert env["con"].execute("SELECT count(*) FROM storage_gc_operation").fetchone()[0] == 0


def test_unknown_foreign_authority_blocks_other_members_independent_root(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    candidate, item = registered(a, "local_report")
    tree = _tree(b, lock=False)
    _write(tree / "PRIVATE_UNKNOWN_FOREIGN_SOURCE")
    result = scan(a)
    assert statuses(result)[item["artifact_id"]] == "blocked"
    assert not result.eligible_artifacts
    public = json.dumps(roots.public_preview(result))
    assert "PRIVATE_UNKNOWN_FOREIGN_SOURCE" not in public and str(tree) not in public
    receipt = _apply(a)
    assert receipt["deleted_bytes"] == 0 and candidate.exists()


def test_hardlink_from_candidate_into_authority_is_never_deleted(libraries):
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    alias = _data(env) / ANCHOR
    os.link(candidate, alias)
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] in {"protected", "blocked"}
    assert not result.eligible_artifacts
    receipt = _apply(env)
    assert receipt["deleted_bytes"] == 0 and candidate.exists() and alias.exists()


def test_resume_rechecks_new_unknown_authority_and_keeps_original_selection(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _ = registered(env)
    identifier, request = _plan_without_execute(env, monkeypatch)
    tree = _tree(env, lock=False)
    _write(tree / "late-evidence")
    later, _ = registered(env, content=b"not in original selection")
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert result["selected_count"] == 1 and candidate.exists() and later.exists()
    assert _op(env, identifier)["request_json"] == request


def test_scan_to_unlink_gap_cannot_use_closed_absence_observation(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _ = registered(env)
    original = gc._unlink
    calls = []
    def appearance(*args, **options):
        calls.append(True)
        _write(_data(env) / ANCHOR, b"late permanent authority fragment")
        return original(*args, **options)
    monkeypatch.setattr(gc, "_unlink", appearance)
    result = _apply(env)
    assert calls and candidate.exists()
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert result["items"][0]["state"] == "blocked"


@pytest.mark.skipif(os.name != "nt", reason="Windows verified DELETE handle final disposition")
def test_authority_appearance_after_final_file_hash_prevents_windows_disposition(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _ = registered(env)
    original_hash, original_dispose = gc._hash_frozen, gc._dispose_windows
    hashes, dispositions = [], []
    def changed_after_hash(*args, **options):
        result = original_hash(*args, **options)
        hashes.append(True)
        if len(hashes) == 1:
            _write(_data(env) / ANCHOR, b"appeared after actual SHA verification")
        return result
    def observed_dispose(*args, **options):
        dispositions.append(True)
        return original_dispose(*args, **options)
    monkeypatch.setattr(gc, "_hash_frozen", changed_after_hash)
    monkeypatch.setattr(gc, "_dispose_windows", observed_dispose)
    result = _apply(env)
    assert hashes and not dispositions
    assert candidate.read_bytes() == b"unused media"
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert result["items"][0]["state"] == "blocked"


def test_actual_delete_bytes_survive_authority_scope_close_failure(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    original = gc._unlink
    observed = []
    def changed_after_real_delete(*args, **options):
        released = original(*args, **options)
        assert not candidate.exists()
        observed.append(released)
        _write(_data(env) / ANCHOR, b"appeared after actual native deletion")
        return released
    monkeypatch.setattr(gc, "_unlink", changed_after_real_delete)
    result = _apply(env)
    assert observed == [item["byte_size"]] and not candidate.exists()
    assert result["state"] == "completed" and result["deleted_bytes"] == item["byte_size"]
    assert result["selected_count"] == 1 and result["items"][0]["state"] == "deleted"
    assert result["items"][0]["deleted_bytes"] == item["byte_size"]
    assert result["items"][0]["reason_code"] == "GC_DELETE_FINISH_UNCONFIRMED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX staged payload callback and final native identity check")
def test_callback_after_staged_hash_cannot_delete_a_replacement_payload(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    identifier, digest = str(uuid.uuid4()), _preview(env)
    stage = (getattr(env["profile"], roots._ROOT_ROLE[item["namespace"]]) /
             gc.STAGE_DIRECTORY / identifier / item["artifact_id"])
    payload, saved = stage / "payload", stage / "saved-original"
    original_hash, original_unlink = gc._hash_frozen, gc.safe._Directory.unlink
    armed, substituted, deletions = [False], [], []

    def after_real_hash(*args, **options):
        result = original_hash(*args, **options)
        if options.get("moved"):
            armed[0] = True
        return result

    def substitute():
        if armed[0] and not substituted:
            os.replace(payload, saved)
            payload.write_bytes(b"new unrelated stage payload")
            substituted.append(True)

    def observed_unlink(directory, name):
        if name == "payload":
            deletions.append(True)
        return original_unlink(directory, name)

    monkeypatch.setattr(gc, "_hash_frozen", after_real_hash)
    monkeypatch.setattr(gc.safe._Directory, "unlink", observed_unlink)
    result = gc.apply([env["profile"]], operation_id=identifier,
        expected_preview_sha256=digest, check_cancelled=substitute)
    assert substituted and not deletions
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert result["items"][0]["reason_code"] == "GC_FILE_IDENTITY_CHANGED"
    assert not candidate.exists() and saved.read_bytes() == b"unused media"
    assert payload.read_bytes() == b"new unrelated stage payload"


@pytest.mark.skipif(os.name == "nt", reason="POSIX already staged payload recovery")
def test_staged_resume_unknown_authority_preserves_payload_and_request(libraries):
    from tests.test_storage_gc import _crash_child
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    identifier = str(uuid.uuid4())
    _crash_child(env, identifier, "staged")
    request = _op(env, identifier)["request_json"]
    payload = (getattr(env["profile"], roots._ROOT_ROLE[item["namespace"]]) /
               gc.STAGE_DIRECTORY / identifier / item["artifact_id"] / "payload")
    # Independently locate the original journal's actual staged payload.
    assert not candidate.exists() and payload.read_bytes() == b"unused media"
    _tree(env, lock=False)
    result = gc.resume([env["profile"]], operation_id=identifier)
    assert result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert not candidate.exists() and payload.read_bytes() == b"unused media"
    assert _op(env, identifier)["request_json"] == request
