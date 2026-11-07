"""DRAFT: combined physical-root protection; not yet applied or executed."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
import itertools
import os
import sqlite3
import time
import uuid

import pytest

from reno import db, storage_admin, storage_artifacts
from reno import storage_lifecycle as lifecycle
from reno.sync_protocol import SyncError
from tests.test_storage_lifecycle import error, holder, one_root, profile


@pytest.fixture()
def members(tmp_path):
    values = (profile(tmp_path / "a", media=tmp_path / "shared-media"),
              profile(tmp_path / "b", media=tmp_path / "shared-media"))
    for value in values:
        db.initialize(value.database)
        lifecycle.initialize(value)
    return values


def test_group_uses_one_global_root_order_and_deduplicates_exact_profiles(members, monkeypatch):
    a, b = members
    observed = []
    attempt = lifecycle._FileLock.attempt
    def record(lock, exclusive):
        observed.append(lifecycle._key(lock.directory.path))
        return attempt(lock, exclusive)
    monkeypatch.setattr(lifecycle._FileLock, "attempt", record)
    with lifecycle.shared_many([b, a, b], timeout=0) as guard:
        assert guard.profiles == (b, a) and guard.profile is b
        assert guard.allows(a) and guard.allows(b)
        assert lifecycle.require_guard(a) is lifecycle.require_guard(b) is guard
        with pytest.raises(AttributeError):
            guard.profiles = (a,)
    assert observed == sorted({lifecycle._key(root) for value in members for root in value.roots})
    observed.clear()
    with lifecycle.exclusive_many([a, b], timeout=0):
        pass
    assert observed == sorted({lifecycle._key(root) for value in members for root in value.roots})


@pytest.mark.parametrize("mode", ["shared_many", "exclusive_many"])
def test_subset_nested_borrows_same_guard_without_new_os_locks(members, monkeypatch, mode):
    a, b = members
    with getattr(lifecycle, mode)(members, timeout=0) as guard:
        monkeypatch.setattr(lifecycle._FileLock, "attempt", lambda *a: pytest.fail("Nested group acquired a new lock"))
        with lifecycle.shared(b, timeout=0) as nested:
            assert nested is guard and nested.profile is a
        with lifecycle.shared_many([b, a], timeout=0) as nested:
            assert nested is guard and lifecycle.current_guard() is guard
        if mode == "exclusive_many":
            with lifecycle.exclusive_many([b], timeout=0) as nested:
                assert lifecycle.require_guard(b, exclusive=True) is nested is guard
        else:
            with error("STORAGE_LOCK_UPGRADE"), lifecycle.exclusive_many([b], timeout=0):
                pass
        assert lifecycle.current_guard() is guard


def test_root_coverage_does_not_authorize_an_unlisted_complete_profile(members, monkeypatch):
    a, b = members
    unlisted = lifecycle.StorageProfile(b.database, b.media, b.checkpoints, b.reports)
    assert unlisted.roots == b.roots
    with lifecycle.shared_many(members) as guard:
        monkeypatch.setattr(lifecycle._FileLock, "attempt", lambda *a: pytest.fail("Subset failure tried to acquire locks"))
        assert not guard.allows(unlisted)
        with error("STORAGE_PROFILE_MISMATCH"):
            lifecycle.require_guard(unlisted)
        with error("STORAGE_PROFILE_MISMATCH"), lifecycle.shared_many([a, unlisted]):
            pass
    with error("STORAGE_GUARD_REQUIRED"):
        guard.allows(a)


def test_single_profile_guard_still_cannot_expand_into_a_group(members):
    a, b = members
    with lifecycle.shared(a) as guard:
        assert guard.profiles == (a,)
        with lifecycle.shared_many([a, a]) as nested:
            assert nested is guard
        with error("STORAGE_PROFILE_MISMATCH"), lifecycle.shared_many(members):
            pass
        with error("STORAGE_PROFILE_MISMATCH"), lifecycle.shared(b):
            pass


@pytest.mark.parametrize("submitted", [None, [], ()])
def test_invalid_or_empty_group_never_opens_a_lock(submitted, monkeypatch):
    monkeypatch.setattr(lifecycle, "_open_lock", lambda *_: pytest.fail("Invalid group opened storage"))
    with error("STORAGE_INVALID_PROFILES"), lifecycle.shared_many(submitted):
        pass


def test_bad_member_and_overlong_iterable_are_rejected_before_filesystem_access(tmp_path, monkeypatch):
    value = profile(tmp_path / "absent")
    monkeypatch.setattr(lifecycle, "_open_lock", lambda *_: pytest.fail("Invalid group opened storage"))
    with error("STORAGE_INVALID_PROFILE"), lifecycle.shared_many([value, None]):
        pass
    consumed = []
    def infinite():
        for item in itertools.repeat(value):
            consumed.append(item)
            yield item
    with error("STORAGE_TOO_MANY_PROFILES"), lifecycle.shared_many(infinite()):
        pass
    assert len(consumed) == lifecycle.MAX_PROFILES + 1 == 257
    assert not value.database.parent.parent.exists()


def test_maximum_group_can_share_the_same_four_roots(tmp_path):
    base = profile(tmp_path)
    values = tuple(lifecycle.StorageProfile(base.database.with_name(f"member-{i}.db"),
        base.media, base.reports, base.checkpoints) for i in range(256))
    lifecycle.initialize(base)
    with lifecycle.shared_many(values, timeout=0) as guard:
        assert len(guard.profiles) == 256
        assert lifecycle.require_guard(values[-1]) is guard
    assert not any(value.database.exists() for value in values)


def test_missing_member_facilities_fail_read_only_and_release_prior_roots(tmp_path):
    a, b = profile(tmp_path / "a"), profile(tmp_path / "z-missing")
    lifecycle.initialize(a)
    with error("STORAGE_NOT_INITIALIZED"), lifecycle.shared_many([a, b], timeout=0):
        pass
    assert not b.database.parent.parent.exists()
    with lifecycle.exclusive(a, timeout=0):
        pass


def test_real_member_process_shared_lock_overlaps_group_shared_and_blocks_group_exclusive(members, tmp_path):
    _, b = members
    with holder(b, tmp_path):
        with lifecycle.shared_many(members, timeout=0):
            pass
        with error("STORAGE_BUSY"), lifecycle.exclusive_many(members, timeout=0):
            pass
    with lifecycle.exclusive_many(reversed(members), timeout=0):
        pass


@pytest.mark.parametrize("cancel", [False, True])
def test_group_wait_or_cancellation_releases_all_earlier_physical_roots(members, tmp_path, cancel):
    roots = sorted({root for value in members for root in value.roots}, key=lifecycle._key)
    early, late = one_root(roots[0]), one_root(roots[-1])
    cancelled = OSError("synthetic cancellation")
    with holder(late, tmp_path, "exclusive"):
        started = time.monotonic()
        def check():
            if time.monotonic() - started >= 0.12:
                raise cancelled
        if cancel:
            with pytest.raises(OSError) as caught, lifecycle.exclusive_many(members, timeout=2, check_cancelled=check):
                pass
            assert caught.value is cancelled
        else:
            with error("STORAGE_BUSY"), lifecycle.exclusive_many(members, timeout=0.12):
                pass
        with lifecycle.exclusive(early, timeout=0):
            pass


def test_verify_checks_secondary_database_identity_not_only_primary(members, tmp_path):
    _, b = members
    with error("STORAGE_UNSAFE_PATH"):
        with lifecycle.shared_many(members) as guard:
            os.link(b.database, tmp_path / "secondary-external-alias.db")
            guard.verify()


def test_copied_context_cannot_borrow_group_guard_in_another_thread(members):
    def attempt():
        with error("STORAGE_GUARD_CONTEXT"):
            lifecycle.require_guard(members[1])
    with lifecycle.shared_many(members) as guard, ThreadPoolExecutor(max_workers=1) as pool:
        context = copy_context()
        pool.submit(context.run, attempt).result(timeout=5)
        assert guard.verify() is guard


def test_from_connection_selects_secondary_main_database_without_changing_transaction(members):
    a, b = members
    con = db.connect(b.database)
    try:
        with lifecycle.shared_many(members) as guard:
            con.execute("BEGIN")
            assert storage_admin.from_connection(con) is b
            assert lifecycle.require_guard(storage_admin.from_connection(con)) is guard
            with error("STORAGE_PROFILE_MISMATCH"):
                storage_admin.from_connection(con, reports=a.reports)
            assert con.in_transaction
            con.rollback()
    finally:
        con.close()


def test_same_database_with_distinct_profiles_requires_explicit_disambiguation(members):
    a, b = members
    alternate = lifecycle.StorageProfile(a.database, a.media, b.reports, b.checkpoints)
    con = db.connect(a.database)
    try:
        with lifecycle.shared_many([a, alternate]):
            con.execute("BEGIN")
            with error("STORAGE_PROFILE_AMBIGUOUS"):
                storage_admin.from_connection(con)
            assert storage_admin.from_connection(con, profile=a) is a
            assert storage_admin.from_connection(con, profile=alternate) is alternate
            con.rollback()
    finally:
        con.close()


def test_foreign_main_database_cannot_borrow_an_attached_group_member(members, tmp_path):
    con = sqlite3.connect(tmp_path / "foreign-main.db")
    try:
        con.execute("ATTACH DATABASE ? AS held_member", (str(members[1].database),))
        with lifecycle.shared_many(members):
            with error("STORAGE_PROFILE_MISMATCH"):
                storage_admin.from_connection(con)
    finally:
        con.close()


def test_existing_sha_proof_keeps_same_group_guard_and_cannot_escape_its_lifetime(members):
    _, b = members
    key = ".versions/checklist-" + uuid.uuid4().hex + ".md"
    path = b.reports / key
    content = b"synthetic immutable report\n"
    path.parent.mkdir()
    path.write_bytes(content)
    con = db.connect(b.database)
    try:
        with lifecycle.shared_many(members) as guard:
            proof = storage_artifacts._inspect_for_registration(con, profile=b, namespace="local_report",
                root=b.reports, storage_key=key, sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content))
            assert proof.guard is guard and proof.profile_identity == b.identity
            with lifecycle.shared(b) as nested:
                assert nested is guard
                con.execute("BEGIN IMMEDIATE")
                registered = storage_artifacts._register_verified(con, proof, profile=b)
                assert storage_artifacts._validate_verified(con, proof, profile=b)["artifact_id"] == registered["artifact_id"]
                con.commit()
        with lifecycle.shared_many(members) as later:
            assert later is not proof.guard
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises(SyncError) as caught:
                storage_artifacts._validate_verified(con, proof, profile=b)
            assert caught.value.code == "STORAGE_PROOF_INVALID"
            con.rollback()
    finally:
        con.close()


@pytest.mark.parametrize("same_database", [False, True])
def test_sha_proof_cannot_be_reused_by_another_profile_even_when_root_and_guard_match(members, same_database):
    a, original_b = members
    b = lifecycle.StorageProfile(a.database if same_database else original_b.database,
        a.media, a.reports, original_b.checkpoints)
    connections = [db.connect(value.database) for value in (a, b)]
    first, second = connections
    content = b"one physical file, separate member authority\n"
    key = ".versions/checklist-" + uuid.uuid4().hex + ".md"
    try:
        with lifecycle.shared_many([a, b]) as guard:
            path = a.reports / key
            path.parent.mkdir()
            path.write_bytes(content)
            proof = storage_artifacts._inspect_for_registration(first, profile=a, namespace="local_report",
                root=a.reports, storage_key=key, sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content))
            assert proof.guard is guard and guard.allows(b) and proof.root == b.reports
            second.execute("BEGIN IMMEDIATE")
            with pytest.raises(SyncError) as caught:
                storage_artifacts._register_verified(second, proof, profile=b)
            assert caught.value.code == "STORAGE_PROOF_INVALID"
            assert second.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
            second.rollback()
            first.execute("BEGIN IMMEDIATE")
            storage_artifacts._register_verified(first, proof, profile=a)
            first.commit()
            verified = storage_artifacts._verify_registered(first, proof.record["artifact_id"], profile=a, root=a.reports)
            assert verified.profile_identity == a.identity
            second.execute("BEGIN IMMEDIATE")
            with pytest.raises(SyncError) as caught:
                storage_artifacts._validate_verified(second, verified, profile=b)
            assert caught.value.code == "STORAGE_PROOF_INVALID"
            second.rollback()
    finally:
        for con in connections:
            con.close()


def test_media_hold_can_issue_each_members_own_proof_only_after_its_database_registration(members):
    from reno.sync_media import MediaStore
    a, b = members
    assert a.media == b.media
    content = b"synthetic shared immutable media"
    descriptor = {"video_id": "BVGroupProof001", "sha256": hashlib.sha256(content).hexdigest(),
                  "byte_size": len(content), "extension": "mp4"}
    first, second = db.connect(a.database), db.connect(b.database)
    try:
        with lifecycle.shared_many(members) as guard:
            media = MediaStore(a.media)
            key = media.receive(descriptor, [content])
            first.execute("BEGIN IMMEDIATE")
            storage_artifacts.register(first, profile=a, namespace="sync_media", root=a.media,
                storage_key=key, sha256=descriptor["sha256"], byte_size=len(content))
            first.commit()
            with media.hold_verified([descriptor]) as hold:
                first.execute("BEGIN IMMEDIATE")
                proof_a = storage_artifacts._from_media_hold(first, hold, descriptor, profile=a)
                assert proof_a.guard is guard and proof_a.profile_identity == a.identity
                first.rollback()
                second.execute("BEGIN IMMEDIATE")
                with pytest.raises(SyncError) as caught:
                    storage_artifacts._from_media_hold(second, hold, descriptor, profile=b)
                assert caught.value.code == "STORAGE_ARTIFACT_CHANGED"
                assert second.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
                storage_artifacts.register(second, profile=b, namespace="sync_media", root=b.media,
                    storage_key=key, sha256=descriptor["sha256"], byte_size=len(content))
                proof_b = storage_artifacts._from_media_hold(second, hold, descriptor, profile=b)
                assert proof_b.guard is guard and proof_b.profile_identity == b.identity
                assert storage_artifacts._validate_verified(second, proof_b, profile=b)
                with pytest.raises(SyncError) as caught:
                    storage_artifacts._validate_verified(second, proof_a, profile=b)
                assert caught.value.code == "STORAGE_PROOF_INVALID"
                second.commit()
            second.execute("BEGIN IMMEDIATE")
            with pytest.raises(SyncError) as caught:
                storage_artifacts._validate_verified(second, proof_b, profile=b)
            assert caught.value.code == "STORAGE_PROOF_INVALID"
            second.rollback()
    finally:
        first.close()
        second.close()
