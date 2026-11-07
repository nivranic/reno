"""Source proofs and immutable map publication use real isolated lifetimes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import pytest

from reno import config, storage_lifecycle as lifecycle, sync_sources as sources
from reno.sync_protocol import SyncError
from tests.test_storage_lifecycle import holder
from tests.test_sync_sources import BV, CONTENT, STAMP, resolution


@pytest.fixture()
def local(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "custom-media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    digest = hashlib.sha256(CONTENT).hexdigest()
    media = profile.media / "sync/file_canonical" / (digest + ".mp4")
    media.parent.mkdir(parents=True)
    media.write_bytes(CONTENT)
    asset = {"video_id": "file_canonical", "content_sha256": digest,
        "files_json": json.dumps({"video": str(media.relative_to(profile.media.parent))})}
    identity = {"source_library_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4())}
    store = sources.SourceMap(profile.database.parent, profile=profile, **identity)
    return profile, store, asset, media, identity


def _profile_values(profile):
    return {name: str(getattr(profile, name)) for name in ("database", "media", "reports", "checkpoints")}


def _wait_path(child, path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if child.poll() is not None:
            pytest.fail(child.communicate(timeout=3)[0].decode("utf-8", "replace"))
        assert time.monotonic() < deadline, "Isolated source writer did not become ready"
        time.sleep(0.02)


_WRITER = """
import json, pathlib, sys
from reno import storage_lifecycle as lifecycle, sync_sources as sources
args = json.loads(sys.argv[1])
profile = lifecycle.StorageProfile(**args['profile'])
store = sources.SourceMap(profile.database.parent, profile=profile, **args['identity'])
pathlib.Path(args['started']).write_text('started', encoding='ascii')
origin = store.record(args['resolution'], extracted_video_id=args['extracted_video_id'], asset=args['asset'])
pathlib.Path(args['done']).write_text(json.dumps(origin), encoding='utf-8')
"""


_EX = """
import json, sys
from reno import storage_lifecycle as lifecycle
profile = lifecycle.StorageProfile(**json.loads(sys.argv[1]))
try:
    with lifecycle.exclusive(profile, timeout=0.2):
        print('acquired')
except lifecycle.StorageLifecycleError as exc:
    print(exc.code)
"""


def _child(code, value):
    return subprocess.Popen([sys.executable, "-B", "-u", "-c", code, json.dumps(value)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_exclusive_process_blocks_map_writer_before_sha_and_publish(local, tmp_path):
    profile, store, asset, media, identity = local
    started, done = tmp_path / "started", tmp_path / "done"
    args = {"profile": _profile_values(profile), "identity": identity, "asset": asset,
        "resolution": resolution(), "extracted_video_id": BV, "started": str(started), "done": str(done)}
    child = None
    try:
        with holder(profile, tmp_path, "exclusive"):
            child = _child(_WRITER, args)
            _wait_path(child, started)
            time.sleep(0.15)
            assert child.poll() is None
            assert not done.exists() and not store.root.exists()
            assert media.read_bytes() == CONTENT
        output = child.communicate(timeout=10)[0]
        assert child.returncode == 0, output.decode("utf-8", "replace")
        origin = json.loads(done.read_bytes())
        assert store.get(origin["mapping_id"]) == origin
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)


def test_sha_to_atomic_publication_keeps_old_media_protected_from_ex(local, monkeypatch):
    profile, store, asset, media, _ = local
    before_rename, release = threading.Event(), threading.Event()
    original = sources._rename_new
    def paused(*args):
        lifecycle.require_guard(profile)
        before_rename.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(sources, "_rename_new", paused)
    child = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.record, resolution(), extracted_video_id=BV, asset=asset)
        try:
            assert before_rename.wait(5)
            child = _child(_EX, _profile_values(profile))
            output = child.communicate(timeout=5)[0]
            assert child.returncode == 0, output.decode("utf-8", "replace")
            assert output.strip() == b"STORAGE_BUSY"
            assert media.read_bytes() == CONTENT and not list(store.root.glob("*.json"))
        finally:
            release.set()
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
        origin = future.result(timeout=5)
    assert store.get(origin["mapping_id"]) == origin
    with lifecycle.exclusive(profile, timeout=0):
        pass


def test_constructor_and_get_are_read_only_without_facilities(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "missing/data/reno.db", tmp_path / "missing/media",
        tmp_path / "missing/reports", tmp_path / "missing/checkpoints")
    store = sources.SourceMap(profile.database.parent, profile=profile,
        source_library_id=str(uuid.uuid4()), source_node_id=str(uuid.uuid4()))
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        store.get("a" * 64)
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not (tmp_path / "missing").exists()
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset={})
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("kind", ["none", "missing", "data", "relative", "traversal"])
def test_explicit_profile_and_exact_data_binding_are_required(local, kind):
    profile, _, _, _, identity = local
    options, root = {"profile": profile}, profile.database.parent
    if kind == "none":
        options["profile"] = None
    elif kind == "missing":
        options = {}
    elif kind == "data":
        root = profile.database.parent.parent / "other-data"
    elif kind == "relative":
        root = Path("data")
    else:
        root = profile.database.parent / "nested/.."
    with pytest.raises((TypeError, lifecycle.StorageLifecycleError)):
        sources.SourceMap(root, **options, **identity)


@pytest.mark.parametrize("changed", ["database", "media", "reports", "checkpoints"])
def test_existing_guard_cannot_be_borrowed_for_profile_alias(local, changed):
    profile, _, asset, _, identity = local
    values = {name: getattr(profile, name) for name in ("database", "media", "reports", "checkpoints")}
    values[changed] = values[changed].with_name("different-" + values[changed].name)
    other = lifecycle.StorageProfile(**values)
    store = sources.SourceMap(other.database.parent, profile=other, **identity)
    with lifecycle.shared(profile), pytest.raises(lifecycle.StorageLifecycleError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset)
    assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    assert not store.root.exists()


def test_record_borrows_existing_guard_without_acquiring_or_changing_transaction(local, monkeypatch):
    import sqlite3
    profile, store, asset, _, _ = local
    con = sqlite3.connect(profile.database)
    try:
        with lifecycle.shared(profile) as guard:
            con.execute("CREATE TABLE sentinel(value TEXT)")
            con.execute("INSERT INTO sentinel VALUES('pending')")
            with monkeypatch.context() as borrowed:
                borrowed.setattr(lifecycle, "shared", lambda *a, **k: pytest.fail("Existing guard must be borrowed"))
                borrowed.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Source proof must not open SQL under the existing transaction"))
                origin = store.record(resolution(), extracted_video_id=BV, asset=asset)
                assert store.get(origin["mapping_id"]) == origin
                assert lifecycle.current_guard() is guard and con.in_transaction
                assert con.execute("SELECT value FROM sentinel").fetchone()[0] == "pending"
        con.rollback()
        assert con.execute("SELECT count(*) FROM sentinel").fetchone()[0] == 0
        assert store.get(origin["mapping_id"]) == origin
    finally:
        con.close()


@pytest.mark.parametrize("absolute", [False, True])
def test_media_resolution_uses_only_explicit_profile(local, monkeypatch, absolute):
    _, store, asset, media, _ = local
    if absolute:
        asset["files_json"] = json.dumps({"video": str(media)})
    monkeypatch.setattr(config, "resolve_media", lambda *a: pytest.fail("Global resolver is forbidden"))
    monkeypatch.setattr(config, "MEDIA", Path("unrelated-media"))
    monkeypatch.setattr(config, "DATA", Path("unrelated-data"))
    monkeypatch.setattr(sources, "_now", lambda: STAMP)
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset)
    assert origin["media_sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert store.get(origin["mapping_id"]) == origin


@pytest.mark.parametrize("stage", ["before", "hash", "publication"])
@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError, ValueError, TypeError, RuntimeError])
def test_callback_exception_identity_survives_hash_and_publication(local, monkeypatch, stage, exception_type):
    profile, store, asset, media, _ = local
    current = ["before"]
    original = sources._Directory.open_file
    def observe(directory, name, **kwargs):
        stream = original(directory, name, **kwargs)
        if directory.path == media.parent and name == media.name:
            current[0] = "hash"
        if name.endswith(".part"):
            current[0] = "publication"
        return stream
    monkeypatch.setattr(sources._Directory, "open_file", observe)
    stopped = exception_type("synthetic cancellation")
    def check():
        if current[0] == stage:
            raise stopped
    with pytest.raises(exception_type) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset, check_cancelled=check)
    assert caught.value is stopped
    assert media.read_bytes() == CONTENT and not list(store.root.glob("*.json"))
    assert not list(store.root.glob("*.part"))
    with lifecycle.exclusive(profile, timeout=0):
        pass


def test_callback_identity_is_preserved_even_when_temporary_cleanup_fails(local, monkeypatch):
    _, store, asset, _, _ = local
    publishing = [False]
    original = sources._Directory.open_file
    def observe(directory, name, **kwargs):
        stream = original(directory, name, **kwargs)
        if name.endswith(".part"):
            publishing[0] = True
        return stream
    monkeypatch.setattr(sources._Directory, "open_file", observe)
    monkeypatch.setattr(sources._Directory, "unlink", lambda *a: (_ for _ in ()).throw(OSError("synthetic cleanup failure")))
    stopped = FileNotFoundError("synthetic caller cancellation")
    def check():
        if publishing[0]:
            raise stopped
    with pytest.raises(FileNotFoundError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset, check_cancelled=check)
    assert caught.value is stopped and not list(store.root.glob("*.json"))


def test_asset_sha_requires_existing_exact_guard(local):
    profile, _, asset, _, _ = local
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        sources._asset_sha(asset, profile=profile)
    assert caught.value.code == "STORAGE_GUARD_REQUIRED"


@pytest.mark.parametrize("field", ["data_root", "root"])
@pytest.mark.parametrize("method", ["get", "record"])
def test_path_drift_cannot_read_or_publish_under_original_profile(local, tmp_path, field, method):
    profile, store, asset, _, _ = local
    unrelated = tmp_path / "unrelated"
    setattr(store, field, unrelated)
    with lifecycle.shared(profile), pytest.raises(lifecycle.StorageLifecycleError) as caught:
        if method == "get":
            store.get("a" * 64)
        else:
            store.record(resolution(), extracted_video_id=BV, asset=asset)
    assert caught.value.code == "STORAGE_PROFILE_MISMATCH" and not unrelated.exists()


def test_media_io_failure_stays_sanitized_and_releases_lifetime(local):
    profile, store, asset, media, _ = local
    media.unlink()
    with pytest.raises(SyncError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset)
    assert caught.value.code == "SYNC_SOURCE_MEDIA_MISMATCH" and str(media) not in str(caught.value)
    assert not store.root.exists()
    with lifecycle.exclusive(profile, timeout=0):
        pass
