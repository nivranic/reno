"""Root membership on synthetic TEMP databases and files, no real library."""
from contextlib import contextmanager
import json
import mmap
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import db, storage_admin, storage_members as members, storage_lifecycle as lifecycle


@pytest.fixture
def api():
    return members, lifecycle


def create(api, tmp_path, name, *, media=None, initialize=True):
    _, lifecycle = api
    base = tmp_path / name
    base.mkdir()
    database = base / "reno.db"
    con = sqlite3.connect(database)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    con.execute("INSERT INTO meta VALUES('library_id',?)", (str(uuid.uuid4()),))
    con.commit()
    con.close()
    profile = lifecycle.StorageProfile(database, media or base / "media", base / "reports", base / "checkpoints")
    if initialize:
        lifecycle.initialize(profile)
    return profile


def declarations(members, profiles):
    roots = members._root_map(profiles)
    return [{"root": root, "databases": [str(p.database) for p in profiles if root in members._root_map((p,))]} for root in roots]


def enroll(members, profiles, *, complete=True):
    declared = declarations(members, profiles) if complete else ()
    preview = members.membership_preview(profiles, declarations=declared)
    assert not preview["blocked"], preview
    result = members.enroll(profiles, expected_digest=preview["digest"], declarations=declared, operation_id=str(uuid.uuid4()))
    return result


def files(root):
    def contents(path):
        try:
            return path.read_bytes()
        except PermissionError:
            if os.name != "nt" or not path.name.endswith("-shm"):
                raise
            # SQLite's live byte-range WAL lock rejects ReadFile over its SHM
            # lock region on Windows. A read-only map still captures every byte.
            with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                return mapped[:]
    return {str(p.relative_to(root)): (contents(p), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_preview_is_pure_read_unknown_existing_roots_and_locks_before_db(api, tmp_path, monkeypatch):
    members, lifecycle = api
    profile = create(api, tmp_path, "a")
    original = members.sqlite3.connect
    observed = []
    def connect(*args, **kwargs):
        assert lifecycle.current_guard().allows(profile)
        assert "mode=ro" in args[0] and kwargs["uri"]
        observed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(members.sqlite3, "connect", connect)
    before = files(tmp_path)
    preview = members.membership_preview([profile])
    assert not preview["blocked"] and observed
    assert {root["state"] for root in preview["roots"]} == {"membership_unknown"}
    assert files(tmp_path) == before


def test_unknown_enrollment_is_usable_and_explicit_declaration_completes(api, tmp_path):
    members, lifecycle = api
    profile = create(api, tmp_path, "a")
    enroll(members, [profile], complete=False)
    with lifecycle.shared_many([profile]):
        assert members.require_enrolled(profile)["library_id"]
    assert {r["state"] for r in members.membership_preview([profile])["roots"]} == {"membership_unknown"}
    enroll(members, [profile])
    assert {r["state"] for r in members.membership_preview([profile])["roots"]} == {"complete"}


def test_join_shared_root_expands_all_existing_members_and_never_overwrites(api, tmp_path):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a])
    # New B's preview discovers A from the shared root and binds both DBs.
    second = members.membership_preview([b])
    assert len(second["members"]) == 2
    enroll(members, [a, b])
    only_a = members.membership_preview([a])
    assert len(only_a["members"]) == 2
    shared = next(r for r in only_a["roots"] if r["root"]["path"] == members._key(a.media))
    assert len(shared["existing_members"]) == 2
    members.enroll([a], expected_digest=only_a["digest"], operation_id=str(uuid.uuid4()))
    assert len(members.membership_preview([b])["members"]) == 2
    with lifecycle.shared_many([a, b]):
        members.require_enrolled(a)
        members.require_enrolled(b)
    for profile in (a, b):
        with lifecycle.shared(profile):
            members.require_enrolled(profile)


def test_full_declaration_cannot_drop_a_known_member(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a, b])
    preview = members.membership_preview([a], declarations=declarations(members, [a]))
    assert preview["blocked"] and preview["code"] == "MEMBERSHIP_DECLARATION_OMITS_MEMBER"


def test_cas_binds_registry_generation_and_content(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a")
    old = members.membership_preview([a])
    enroll(members, [a])
    before = files(tmp_path)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_PREVIEW_STALE"):
        members.enroll([a], expected_digest=old["digest"], operation_id=str(uuid.uuid4()))
    assert files(tmp_path) == before


def test_missing_member_never_erased_or_claimed_complete(api, tmp_path):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a, b])
    before = (a.media / members.REGISTRY).read_bytes()
    b.database.unlink()
    preview = members.membership_preview([a])
    assert preview["blocked"] and len(preview["profiles"]) == 2
    roots = {r["root"]["path"]: r for r in preview["roots"]}
    assert roots[members._key(a.media)]["blockers"] == ["member_unavailable"]
    assert roots[members._key(a.reports)]["blockers"] == []
    assert roots[members._key(a.checkpoints)]["blockers"] == []
    assert (a.media / members.REGISTRY).read_bytes() == before
    with lifecycle.shared(a):
        members.require_enrolled(a)


@pytest.mark.parametrize("damage", ["schema", "invalid", "symlink", "permissions"])
def test_unsafe_registry_fails_closed(api, tmp_path, damage):
    members, _ = api
    a = create(api, tmp_path, "a")
    enroll(members, [a])
    target = a.media / members.REGISTRY
    if damage == "schema":
        value = json.loads(target.read_text(encoding="utf-8"))
        value["schema"] = 99
        target.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "invalid":
        target.write_bytes(b"not-json")
    elif damage == "permissions":
        if os.name == "nt":
            pytest.skip("POSIX mode contract")
        target.chmod(0o644)
    else:
        outside = tmp_path / "private.txt"
        outside.write_text("never read this", encoding="utf-8")
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            pytest.skip("symlink permission unavailable")
    preview = members.membership_preview([a])
    assert preview["blocked"] and preview["digest"] is None
    assert "never read this" not in json.dumps(preview)


def test_new_directory_receipts_complete_only_roots_actually_created(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a", initialize=False)
    a.media.mkdir()  # An existing empty root still needs an explicit assertion.
    result = members.initialize_member(a)
    states = {r["root"]["path"]: r["proposed_state"] for r in result["roots"]}
    assert states[members._key(a.media)] == "membership_unknown"
    assert states[members._key(a.reports)] == states[members._key(a.checkpoints)] == "complete"
    actual = members.membership_preview([a])
    assert not actual["blocked"]


def test_initialize_registered_member_is_pure_idempotence_without_generation_changes(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a", initialize=False)
    assert members.initialize_member(a)["unchanged"] is False
    before = files(tmp_path)
    result = members.initialize_member(a)
    assert result["unchanged"] is True and result["changed_roots"] == []
    assert files(tmp_path) == before


def test_initialize_shared_member_is_idempotent_but_new_member_needs_cas_before_mkdir(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a, b])
    before = files(tmp_path)
    assert members.initialize_member(a)["unchanged"] is True
    assert members.initialize_member(b)["unchanged"] is True
    assert files(tmp_path) == before
    c = create(api, tmp_path, "c", media=a.media, initialize=False)
    assert not c.reports.exists() and not c.checkpoints.exists()
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_ENROLLMENT_REQUIRED"):
        members.initialize_member(c)
    assert not c.reports.exists() and not c.checkpoints.exists()


def test_initialize_refuses_identity_drift_without_replacing_membership(api, tmp_path):
    members, _ = api
    a = create(api, tmp_path, "a")
    enroll(members, [a])
    con = sqlite3.connect(a.database)
    con.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
    con.commit()
    con.close()
    before = files(tmp_path)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_ENROLLMENT_REQUIRED"):
        members.initialize_member(a)
    assert files(tmp_path) == before


def test_creation_receipt_rejects_unrecognized_files(api, tmp_path, monkeypatch):
    members, lifecycle = api
    a = create(api, tmp_path, "a", initialize=False)
    original = lifecycle.initialize
    def initialize(profile):
        result = original(profile)
        (profile.media / "unexpected.mp4").write_bytes(b"untouched")
        return result
    monkeypatch.setattr(lifecycle, "initialize", initialize)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_FRESH_ROOT_NOT_EMPTY"):
        members.initialize_member(a)
    assert (a.media / "unexpected.mp4").read_bytes() == b"untouched"
    assert not (a.media / members.REGISTRY).exists()


def test_partial_write_invalidates_whole_commit_and_can_be_reviewed_then_repaired(api, tmp_path, monkeypatch):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    enroll(members, [a])
    before = members.membership_preview([a])
    original, calls = members._write, []
    def fail_after_one(root, name, payload, **kwargs):
        calls.append(name)
        if len(calls) == 2:
            raise OSError("synthetic interruption")
        return original(root, name, payload, **kwargs)
    monkeypatch.setattr(members, "_write", fail_after_one)
    with pytest.raises(OSError):
        members.enroll([a], expected_digest=before["digest"], operation_id=str(uuid.uuid4()))
    monkeypatch.setattr(members, "_write", original)
    partial = members.membership_preview([a])
    assert not partial["blocked"]
    assert {r["state"] for r in partial["roots"]} == {"enrollment_incomplete"}
    partial_bytes = files(tmp_path)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_ENROLLMENT_REQUIRED"):
        members.initialize_member(a)
    assert files(tmp_path) == partial_bytes
    with lifecycle.shared_many([a]):
        with pytest.raises(members.MembershipError, match="MEMBERSHIP_NOT_ENROLLED"):
            members.require_enrolled(a)
    enroll(members, [a])
    assert {r["state"] for r in members.membership_preview([a])["roots"]} == {"complete"}


def test_snapshot_locked_never_recursively_acquires_missing_profiles(api, tmp_path):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a, b])
    with lifecycle.shared_many([a]):
        with pytest.raises(members.MembershipError, match="MEMBERSHIP_GROUP_INCOMPLETE"):
            members.snapshot_locked([a])


def test_incomplete_local_witness_copy_blocks_gc_and_unwitnessed_admission(api, tmp_path, monkeypatch):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    old = members.membership_preview([a])
    original, commits = members._write, []
    def fail_witness(root, name, payload, **kwargs):
        if name.startswith(members.COMMIT_PREFIX):
            commits.append(name)
            if len(commits) == 2:
                raise OSError("synthetic witness copy failure")
        return original(root, name, payload, **kwargs)
    monkeypatch.setattr(members, "_write", fail_witness)
    with pytest.raises(OSError):
        members.enroll([a], expected_digest=old["digest"], operation_id=str(uuid.uuid4()))
    monkeypatch.setattr(members, "_write", original)
    with lifecycle.shared(a):
        with pytest.raises(members.MembershipError, match="MEMBERSHIP_NOT_ENROLLED"):
            members.require_enrolled(a)
    assert {r["state"] for r in members.membership_preview([a])["roots"]} == {"enrollment_incomplete"}
    enroll(members, [a])
    with lifecycle.shared(a):
        members.require_enrolled(a)


def test_join_between_discovery_and_lock_releases_then_reacquires_complete_group(api, tmp_path, monkeypatch):
    members, lifecycle = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b", media=a.media)
    enroll(members, [a])
    original = lifecycle.shared_many
    acquisitions = []
    joined = False
    @contextmanager
    def shared(profiles, **kwargs):
        nonlocal joined
        acquisitions.append(tuple(p.identity for p in profiles))
        if not joined:
            joined = True
            declared = declarations(members, [a, b])
            # Build this competing preview without recursively calling our hook.
            with lifecycle.exclusive_many([a, b]) as guard:
                expanded, roots, errors = members._discover([a, b])
                assert not errors
                members._complete_roots(roots, guard)
                _, candidates = members._plan(expanded, roots, declared)
                members._apply(candidates, str(uuid.uuid4()))
        with original(profiles, **kwargs) as guard:
            yield guard
    monkeypatch.setattr(lifecycle, "shared_many", shared)
    result = members.membership_preview([a])
    assert not result["blocked"] and len(result["members"]) == 2
    assert [len(group) for group in acquisitions] == [1, 2]


def test_coordinator_cannot_make_discovery_read_outside_declared_group(api, tmp_path, monkeypatch):
    members, _ = api
    a = create(api, tmp_path, "a")
    enroll(members, [a])
    target = a.media / members.REGISTRY
    value = json.loads(target.read_text(encoding="utf-8"))
    outside = tmp_path / "outside"
    outside.mkdir()
    value["coordinator"] = members._root_identity(outside)
    target.write_bytes(members._json(value))
    original = members._read_bytes
    def read(root, name):
        assert members._key(root) != members._key(outside)
        return original(root, name)
    monkeypatch.setattr(members, "_read_bytes", read)
    assert members.membership_preview([a])["blocked"]


@pytest.mark.parametrize("damage", ["recursive_view", "duplicate_id", "unbounded_scan"])
def test_library_identity_read_rejects_unsafe_or_ambiguous_meta_with_bounded_work(api, tmp_path, damage):
    members, _ = api
    a = create(api, tmp_path, "a")
    con = sqlite3.connect(a.database)
    con.execute("DROP TABLE meta")
    if damage == "recursive_view":
        value = str(uuid.uuid4())
        con.execute("CREATE VIEW meta AS WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM numbers) "
                    f"SELECT 'library_id' AS key,'{value}' AS value FROM numbers")
    else:
        con.execute("CREATE TABLE meta(key,value)")
        if damage == "duplicate_id":
            con.executemany("INSERT INTO meta VALUES('library_id',?)", [(str(uuid.uuid4()),), (str(uuid.uuid4()),)])
        else:
            con.executemany("INSERT INTO meta VALUES('unrelated',?)", [(str(i),) for i in range(30_000)])
            con.execute("INSERT INTO meta VALUES('library_id',?)", (str(uuid.uuid4()),))
    con.commit()
    con.close()
    preview = members.membership_preview([a])
    assert preview["blocked"] and preview["unavailable_members"]
    if damage == "recursive_view":
        assert preview["unavailable_members"][0]["code"] == "MEMBERSHIP_DATABASE_META_UNSAFE"
    elif damage == "duplicate_id":
        assert preview["unavailable_members"][0]["code"] == "MEMBERSHIP_DATABASE_IDENTITY_MISSING"


def test_discovery_member_limit_is_global_and_stops_before_expanding_next_member(api, tmp_path, monkeypatch):
    members, _ = api
    a = create(api, tmp_path, "a")
    b = create(api, tmp_path, "b")
    c = create(api, tmp_path, "c")
    monkeypatch.setattr(members, "MAX_MEMBERS", 2)
    visited = []
    def registry(root):
        key = members._key(root)
        visited.append(key)
        assert key not in members._root_map((c,)), "Discovery continued beyond its hard limit"
        profile = b if key in members._root_map((a,)) else c
        return {"value": {"group_profiles": [members._profile_json(profile)]}}
    monkeypatch.setattr(members, "_registry", registry)
    preview = members.membership_preview([a])
    assert preview["blocked"] and preview["code"] == "MEMBERSHIP_TOO_MANY_MEMBERS"
    assert len(visited) <= 8  # B's first root aborts each of the two bounded attempts.


def full_profile(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    storage_admin.initialize(profile)
    return profile


def test_full_schema_closed_wal_database_preview_never_creates_source_sidecars(tmp_path):
    profile = full_profile(tmp_path)
    assert not Path(str(profile.database) + "-wal").exists()
    assert not Path(str(profile.database) + "-shm").exists()
    before = files(tmp_path)
    result = members.membership_preview([profile])
    assert not result["blocked"], result
    assert files(tmp_path) == before


@pytest.mark.parametrize("uncommitted", [False, True])
def test_active_wal_identity_includes_commits_without_touching_source_db_wal_or_shm(tmp_path, uncommitted):
    profile = full_profile(tmp_path)
    writer = db.connect(profile.database)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    committed_id = str(uuid.uuid4())
    try:
        writer.execute("UPDATE meta SET value=? WHERE key='library_id'", (committed_id,))
        writer.commit()
        assert Path(str(profile.database) + "-wal").stat().st_size > 0
        # Prove immutable source reads would return the wrong identity.
        stale = sqlite3.connect(profile.database.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            assert stale.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0] != committed_id
        finally:
            stale.close()
        if uncommitted:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        before = files(tmp_path)
        result = members.membership_preview([profile])
        assert not result["blocked"], result
        assert result["members"][0]["library_id"] == committed_id
        assert files(tmp_path) == before
    finally:
        writer.rollback()
        writer.close()


def test_wal_appearing_during_immutable_read_invalidates_that_result_and_retries(tmp_path, monkeypatch):
    profile = full_profile(tmp_path)
    original, writer, modes = members._query_identity, [], []
    committed_id = str(uuid.uuid4())
    def query(path, *, immutable):
        modes.append(immutable)
        result = original(path, immutable=immutable)
        if len(modes) == 1:
            con = db.connect(profile.database)
            con.execute("UPDATE meta SET value=? WHERE key='library_id'", (committed_id,))
            con.commit()
            writer.append(con)
        return result
    monkeypatch.setattr(members, "_query_identity", query)
    try:
        result = members.membership_preview([profile])
        assert not result["blocked"] and result["members"][0]["library_id"] == committed_id
        assert modes == [True, False]
    finally:
        for con in writer:
            con.close()


def test_active_wal_copy_is_bounded_and_source_remains_unchanged(tmp_path, monkeypatch):
    profile = full_profile(tmp_path)
    writer = db.connect(profile.database)
    try:
        writer.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        writer.commit()
        before = files(tmp_path)
        monkeypatch.setattr(members, "MAX_IDENTITY_COPY_BYTES", 1)
        result = members.membership_preview([profile])
        assert result["blocked"]
        assert result["unavailable_members"][0]["code"] == "MEMBERSHIP_SNAPSHOT_TOO_LARGE"
        assert files(tmp_path) == before
    finally:
        writer.close()


def test_repeated_source_changes_exhaust_bounded_attempts_without_accepting_stale_identity(tmp_path, monkeypatch):
    profile = full_profile(tmp_path)
    original, connections, calls = members._query_identity, [], []
    def query(path, *, immutable):
        result = original(path, immutable=immutable)
        calls.append(immutable)
        if not connections:
            connections.append(db.connect(profile.database))
        connections[0].execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        connections[0].commit()
        return result
    monkeypatch.setattr(members, "_query_identity", query)
    try:
        result = members.membership_preview([profile])
        assert result["blocked"] and result["members"] == []
        assert result["unavailable_members"][0]["code"] == "MEMBERSHIP_SOURCE_CHANGED"
        assert len(calls) == members.IDENTITY_READ_ATTEMPTS == 2
        assert all(not (getattr(profile, role) / members.REGISTRY).exists() for role in members.ROLES)
    finally:
        for con in connections:
            con.close()


def test_common_source_copy_keeps_the_existing_member_reader_spy_boundary(tmp_path, monkeypatch):
    profile = full_profile(tmp_path)
    writer = db.connect(profile.database)
    original, observed = members._source_reader, []
    @contextmanager
    def spy(directory, name):
        observed.append(name)
        with original(directory, name) as stream:
            yield stream
    monkeypatch.setattr(members, "_source_reader", spy)
    try:
        writer.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        writer.commit()
        before = files(tmp_path)
        result = members.membership_preview([profile])
        assert not result["blocked"], result
        assert set(observed) == {profile.database.name, profile.database.name + "-wal"}
        assert files(tmp_path) == before
    finally:
        writer.close()


@pytest.mark.parametrize("source_code,member_code", [
    ("SOURCE_CHANGED", "MEMBERSHIP_SOURCE_CHANGED"),
    ("SOURCE_UNSAFE", "MEMBERSHIP_DATABASE_UNSAFE"),
])
def test_common_source_errors_keep_member_public_error_codes(tmp_path, monkeypatch, source_code, member_code):
    from reno import storage_source
    profile = full_profile(tmp_path)
    before = files(tmp_path)
    def rejected(*_args):
        raise storage_source.SourceReadError(source_code)
    monkeypatch.setattr(storage_source, "source_state", rejected)
    result = members.membership_preview([profile])
    assert result["blocked"] and not result["members"]
    assert result["unavailable_members"][0]["code"] == member_code
    assert files(tmp_path) == before
