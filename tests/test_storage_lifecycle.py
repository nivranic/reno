"""Physical-root lifetime locking, using only isolated synthetic storage."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

from reno import storage_lifecycle as lifecycle


def profile(root, *, media=None):
    return lifecycle.StorageProfile(root / "db/reno.db", media or root / "media",
                                    root / "reports", root / "checkpoints")


def one_root(root):
    return lifecycle.StorageProfile(root / "unused.db", root, root, root)


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns,
             path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}


@contextmanager
def error(code):
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        yield
    assert caught.value.code == code


_CHILD = """
import json, pathlib, sys
from reno import storage_lifecycle as lifecycle
args = json.loads(sys.argv[1])
profile = lifecycle.StorageProfile(**args['profile'])
if args['mode'] == 'initialize':
    sys.stdin.readline()
    print(json.dumps(lifecycle.initialize(profile)))
    sys.exit(0)
with getattr(lifecycle, args['mode'])(profile, timeout=3):
    temporary = pathlib.Path(args['ready'] + '.tmp')
    temporary.write_text('held', encoding='ascii')
    temporary.rename(args['ready'])
    sys.stdin.readline()
"""


@contextmanager
def holder(value, tmp_path, mode="shared"):
    ready = tmp_path / ("child-ready-" + uuid.uuid4().hex)
    args = {"profile": {key: str(getattr(value, key)) for key in
            ("database", "media", "reports", "checkpoints")}, "mode": mode, "ready": str(ready)}
    child = subprocess.Popen([sys.executable, "-u", "-c", _CHILD, json.dumps(args)],
        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if child.poll() is not None:
                pytest.fail(child.communicate()[0].decode("utf-8", "replace"))
            assert time.monotonic() < deadline, "Isolated lifecycle child did not become ready"
            time.sleep(0.02)
        yield child
    finally:
        if child.poll() is None:
            try:
                output, _ = child.communicate(b"release\n", timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)
                pytest.fail("Isolated lifecycle child did not release its lock")
            assert child.returncode == 0, output.decode("utf-8", "replace")
        else:
            child.communicate(timeout=5)


def test_profile_uses_canonical_sorted_distinct_physical_roots(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "z/reno.db", tmp_path / "a", tmp_path / "z", tmp_path / "a")
    assert value.roots == (tmp_path / "a", tmp_path / "z")
    assert lifecycle.StorageProfile(*(str(getattr(value, name)) for name in
        ("database", "media", "reports", "checkpoints"))).identity == value.identity
    with error("STORAGE_INVALID_PROFILE"):
        profile(Path("relative"))
    with error("STORAGE_INVALID_PROFILE"):
        profile(tmp_path / "nested/../escape")
    with error("STORAGE_INVALID_PROFILE"):
        lifecycle.initialize({})


def test_uninitialized_read_paths_do_not_create_any_facility(tmp_path):
    value = profile(tmp_path / "missing")
    for mode in (lifecycle.shared, lifecycle.exclusive):
        with error("STORAGE_NOT_INITIALIZED"), mode(value, timeout=0):
            pass
    with error("STORAGE_GUARD_REQUIRED"):
        lifecycle.require_guard(value)
    assert not (tmp_path / "missing").exists()


def test_initialize_is_explicit_idempotent_and_preserves_existing_bytes(tmp_path):
    value = profile(tmp_path)
    value.media.mkdir()
    sentinel = value.media / "original.bin"
    sentinel.write_bytes(b"untouched synthetic media")
    first = lifecycle.initialize(value)
    assert first == {"roots": [str(path) for path in value.roots], "created_locks": 4}
    assert not value.database.exists()
    before = snapshot(tmp_path)
    assert lifecycle.initialize(value)["created_locks"] == 0
    assert snapshot(tmp_path) == before
    for mode in (lifecycle.shared, lifecycle.exclusive):
        with mode(value, timeout=0) as guard:
            assert guard.verify() is guard
    assert snapshot(tmp_path) == before


def test_concurrent_initialize_never_exposes_an_incomplete_final_lock(tmp_path, monkeypatch):
    value = profile(tmp_path)
    opened, release = threading.Event(), threading.Event()
    original = lifecycle.safe._Directory.open_file
    worker_id = []
    def paused_open(directory, name, **kwargs):
        stream = original(directory, name, **kwargs)
        if (threading.get_ident() in worker_id and name.startswith(".reno-storage-initialize-")
                and not opened.is_set()):
            opened.set()
            assert release.wait(5)
        return stream
    def first():
        worker_id.append(threading.get_ident())
        return lifecycle.initialize(value)
    monkeypatch.setattr(lifecycle.safe._Directory, "open_file", paused_open)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first)
        try:
            assert opened.wait(5)
            second = lifecycle.initialize(value)
        finally:
            release.set()
        initial = future.result(timeout=5)
    assert initial["created_locks"] + second["created_locks"] == 4
    assert not list(tmp_path.rglob(".reno-storage-initialize-*.tmp"))
    with lifecycle.exclusive(value, timeout=0):
        pass


def test_initialize_during_exclusive_use_is_controlled_busy_without_mutation(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    with holder(value, tmp_path, "exclusive"):
        # Read only metadata here: Windows enforces the held byte lock on reads.
        before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}
        with error("STORAGE_BUSY"):
            lifecycle.initialize(value)
        assert {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")} == before


def test_multiple_processes_can_initialize_shared_roots_at_once(tmp_path):
    value = profile(tmp_path / "storage")
    args = {"profile": {key: str(getattr(value, key)) for key in
            ("database", "media", "reports", "checkpoints")}, "mode": "initialize"}
    children = []
    try:
        for _ in range(4):
            children.append(subprocess.Popen([sys.executable, "-u", "-c", _CHILD, json.dumps(args)],
                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        for child in children:
            child.stdin.write(b"initialize\n")
            child.stdin.flush()
        results = []
        for child in children:
            output, _ = child.communicate(timeout=10)
            assert child.returncode == 0, output.decode("utf-8", "replace")
            results.append(json.loads(output))
        assert sum(result["created_locks"] for result in results) == 4
        with lifecycle.exclusive(value, timeout=0):
            pass
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("contents", [b"unknown", b"x" * len(lifecycle._MARKER), b""])
def test_initialize_refuses_unknown_existing_locks_before_creating_any_root(tmp_path, contents):
    value = profile(tmp_path)
    value.reports.mkdir()
    lock = value.reports / lifecycle.LOCK_NAME
    lock.write_bytes(contents)
    lock.chmod(0o600)
    before = snapshot(tmp_path)
    with pytest.raises(lifecycle.StorageLifecycleError):
        lifecycle.initialize(value)
    assert snapshot(tmp_path) == before


def test_shared_processes_overlap_but_exclusive_waits(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    with holder(value, tmp_path), holder(value, tmp_path):
        with lifecycle.shared(value, timeout=0):
            pass
        with error("STORAGE_BUSY"), lifecycle.exclusive(value, timeout=0):
            pass
    with lifecycle.exclusive(value, timeout=0):
        pass


@pytest.mark.parametrize("requested", ["shared", "exclusive"])
def test_exclusive_process_blocks_both_modes_with_bounded_wait(tmp_path, requested):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    with holder(value, tmp_path, "exclusive"):
        start = time.monotonic()
        with error("STORAGE_BUSY"), getattr(lifecycle, requested)(value, timeout=0.15):
            pass
        assert 0.10 <= time.monotonic() - start < 1.5
    with getattr(lifecycle, requested)(value, timeout=0):
        pass


def test_different_database_roots_coordinate_on_shared_media(tmp_path):
    first = profile(tmp_path / "a", media=tmp_path / "common-media")
    second = profile(tmp_path / "b", media=tmp_path / "common-media")
    lifecycle.initialize(first)
    lifecycle.initialize(second)
    assert first.database.parent != second.database.parent
    with holder(first, tmp_path):
        with lifecycle.shared(second, timeout=0):
            pass
        with error("STORAGE_BUSY"), lifecycle.exclusive(second, timeout=0):
            pass
    with lifecycle.exclusive(second, timeout=0):
        pass


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancellation_releases_already_acquired_roots(tmp_path, cancel):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    early, late = one_root(value.roots[0]), one_root(value.roots[-1])
    cancelled = OSError("synthetic business cancellation must retain identity")
    start = time.monotonic()
    def check():
        if time.monotonic() - start >= 0.12:
            raise cancelled
    with holder(late, tmp_path, "exclusive"):
        start = time.monotonic()
        if cancel:
            with pytest.raises(OSError) as caught, lifecycle.exclusive(value, timeout=2, check_cancelled=check):
                pass
            assert caught.value is cancelled
        else:
            with error("STORAGE_BUSY"), lifecycle.exclusive(value, timeout=0.1):
                pass
        with lifecycle.exclusive(early, timeout=0):
            pass


def test_process_death_releases_all_locks_without_removing_facilities(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    before = snapshot(tmp_path)
    with holder(value, tmp_path, "exclusive") as child:
        child.kill()
        child.wait(timeout=5)
        with lifecycle.exclusive(value, timeout=1):
            pass
    # The only additional file is the synthetic child readiness marker.
    after = snapshot(tmp_path)
    assert all(after[name] == content for name, content in before.items())


@pytest.mark.parametrize("outer", ["shared", "exclusive"])
def test_nested_guards_reuse_only_same_profile_without_upgrade(tmp_path, outer):
    value, other = profile(tmp_path / "a"), profile(tmp_path / "b")
    lifecycle.initialize(value)
    with getattr(lifecycle, outer)(value, timeout=0) as guard:
        with lifecycle.shared(value, timeout=0) as nested:
            assert nested is guard is lifecycle.require_guard(value)
        if outer == "exclusive":
            with lifecycle.exclusive(value, timeout=0) as nested:
                assert nested is guard
        else:
            with error("STORAGE_LOCK_UPGRADE"), lifecycle.exclusive(value, timeout=0):
                pass
            with pytest.raises(AttributeError):
                guard.exclusive = True
        with error("STORAGE_PROFILE_MISMATCH"), lifecycle.shared(other):
            pass
        with error("STORAGE_INITIALIZE_WHILE_GUARDED"):
            lifecycle.initialize(value)
        assert guard.verify() is guard
    with error("STORAGE_GUARD_REQUIRED"):
        guard.verify()
    assert not other.database.parent.exists()


def test_thread_and_copied_context_cannot_borrow_an_active_guard(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    def expect(code, operation):
        with error(code):
            operation()
    with lifecycle.shared(value) as guard, ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(expect, "STORAGE_GUARD_REQUIRED", lambda: lifecycle.require_guard(value)).result(timeout=5)
        context = copy_context()
        pool.submit(context.run, expect, "STORAGE_GUARD_CONTEXT", guard.verify).result(timeout=5)
        assert guard.verify() is guard


def test_async_child_task_cannot_borrow_inherited_guard(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    async def attempt():
        with error("STORAGE_GUARD_CONTEXT"):
            lifecycle.require_guard(value)
    async def run():
        with lifecycle.shared(value) as guard:
            await asyncio.create_task(attempt())
            assert guard.verify() is guard
    asyncio.run(run())


def test_business_exception_releases_locks_and_retains_original_exception(tmp_path):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    failure = FileNotFoundError("business failure")
    with pytest.raises(FileNotFoundError) as caught:
        with lifecycle.shared(value):
            raise failure
    assert caught.value is failure
    with lifecycle.exclusive(value, timeout=0):
        pass


def test_require_guard_inside_sqlite_transaction_never_locks_or_commits(tmp_path, monkeypatch):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    with sqlite3.connect(value.database) as con:
        con.execute("CREATE TABLE synthetic (value TEXT)")
        with lifecycle.shared(value) as guard:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO synthetic VALUES ('pending')")
            monkeypatch.setattr(lifecycle._FileLock, "attempt", lambda *a: pytest.fail("Nested OS lock attempt"))
            assert lifecycle.require_guard(value) is guard
            assert con.in_transaction
            con.rollback()
            assert con.execute("SELECT count(*) FROM synthetic").fetchone()[0] == 0
        con.execute("BEGIN IMMEDIATE")
        with error("STORAGE_GUARD_REQUIRED"):
            lifecycle.require_guard(value)
        assert con.in_transaction
        con.rollback()


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), True, "1", 3601])
def test_invalid_timeout_is_rejected_without_mutation(tmp_path, timeout):
    value = profile(tmp_path / "missing")
    with error("STORAGE_INVALID_TIMEOUT"), lifecycle.shared(value, timeout=timeout):
        pass
    assert not (tmp_path / "missing").exists()


def test_hardlinked_lock_and_database_are_rejected_without_mutation(tmp_path):
    value = profile(tmp_path / "storage")
    lifecycle.initialize(value)
    lock = value.media / lifecycle.LOCK_NAME
    alias = tmp_path / "lock-alias"
    os.link(lock, alias)
    with pytest.raises(lifecycle.StorageLifecycleError), lifecycle.shared(value):
        pass
    alias.unlink()
    external = tmp_path / "external.db"
    external.write_bytes(b"synthetic database")
    os.link(external, value.database)
    before = snapshot(tmp_path)
    with error("STORAGE_UNSAFE_PATH"):
        lifecycle.initialize(value)
    assert snapshot(tmp_path) == before


def test_root_junction_or_symlink_is_rejected_before_initialization(tmp_path):
    value = profile(tmp_path / "storage")
    value.media.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"preserved")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(value.media))
    else:
        value.media.symlink_to(outside, target_is_directory=True)
    try:
        with error("STORAGE_UNSAFE_PATH"):
            lifecycle.initialize(value)
        assert not value.database.parent.exists()
        assert list(outside.iterdir()) == [outside / "keep"]
        assert (outside / "keep").read_bytes() == b"preserved"
    finally:
        value.media.rmdir() if os.name == "nt" else value.media.unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow and private mode boundary")
def test_final_lock_symlink_and_nonprivate_permissions_are_rejected(tmp_path):
    value = profile(tmp_path / "storage")
    lifecycle.initialize(value)
    lock = value.media / lifecycle.LOCK_NAME
    lock.chmod(0o644)
    with error("STORAGE_UNSAFE_PATH"), lifecycle.shared(value):
        pass
    lock.chmod(0o600)
    moved = tmp_path / "external-lock"
    lock.rename(moved)
    lock.symlink_to(moved)
    with pytest.raises(lifecycle.StorageLifecycleError), lifecycle.shared(value):
        pass
    assert moved.read_bytes() == lifecycle._MARKER


@pytest.mark.parametrize("replace", ["lock", "directory"])
def test_observable_identity_changes_are_rejected_or_prevented(tmp_path, replace):
    value = profile(tmp_path)
    lifecycle.initialize(value)
    victim = value.media / lifecycle.LOCK_NAME if replace == "lock" else value.media
    moved = tmp_path / "moved"
    if os.name == "nt":
        with lifecycle.shared(value) as guard:
            with pytest.raises(OSError):
                victim.rename(moved)
            assert guard.verify() is guard
    else:
        with error("STORAGE_UNSAFE_PATH"):
            with lifecycle.shared(value) as guard:
                victim.rename(moved)
                if replace == "lock":
                    victim.write_bytes(lifecycle._MARKER)
                    victim.chmod(0o600)
                else:
                    victim.mkdir()
                    (victim / lifecycle.LOCK_NAME).write_bytes(lifecycle._MARKER)
                    (victim / lifecycle.LOCK_NAME).chmod(0o600)
                guard.verify()
