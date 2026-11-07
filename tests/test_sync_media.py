"""Immutable media files and actual process/quota contention in disposable roots."""
import asyncio
from contextlib import contextmanager
import errno
import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import uuid

import pytest

from reno import sync_media
from reno.sync_media import MediaStore
from reno.sync_protocol import SyncError


def descriptor(data=b"synthetic-video", video_id="BVmedia", **changes):
    return {"video_id": video_id, "sha256": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data), "extension": "mp4", **changes}


def parts(root):
    return list((root / ".sync-tmp").glob("*.part"))


def directory_link(link, target):
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            pytest.fail("Could not create the isolated test junction")
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_directory_link(link):
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def _upload_process(root, media, data, entered, release, result):
    try:
        store = MediaStore(Path(root), max_temp_bytes=len(data) + 1)

        def chunks():
            middle = len(data) // 2
            yield data[:middle]
            entered.set()
            if release is not None:
                release.recv()
            yield data[middle:]

        result.send({"key": store.receive(media, chunks())})
    except SyncError as exc:
        result.send({"error": exc.code})
    finally:
        result.close()
        if release is not None:
            release.close()


def stop_process(process):
    if process.pid is None:
        return
    process.join(timeout=3)
    if process.is_alive():
        process.terminate()
        process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join(timeout=3)


def _lock_holder(root, entered, release):
    path = Path(root) / ".sync-tmp"
    with sync_media._Directory(path, create=True, base=Path(root)) as directory:
        with sync_media._upload_lock(directory):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release its artificial upload lock")


@contextmanager
def held_upload_lock(root, kind):
    """Hold the real platform lock in a separate thread or spawned process."""
    if kind == "process":
        context = multiprocessing.get_context("spawn")
        entered, release = context.Event(), context.Event()
        owner = context.Process(target=_lock_holder, args=(str(root), entered, release))
    else:
        entered, release = threading.Event(), threading.Event()
        owner = threading.Thread(target=_lock_holder, args=(str(root), entered, release), daemon=True)
    owner.start()
    try:
        assert entered.wait(8), "Real upload lock was not acquired"
        yield owner
    finally:
        release.set()
        owner.join(8)
        if kind == "process":
            if owner.is_alive():
                stop_process(owner)
            assert owner.exitcode == 0
        assert not owner.is_alive()


def test_streaming_publish_is_immutable_and_not_visible_until_complete(tmp_path):
    store = MediaStore(tmp_path / "media")
    old, new = b"old-valid-content", b"new-valid-content"
    old_desc, new_desc = descriptor(old), descriptor(new)
    old_key = store.receive(old_desc, [old])
    new_path = store.root / store.storage_key(new_desc)

    def chunks():
        yield new[:4]
        assert not new_path.exists()
        assert store.verified_path(old_desc).read_bytes() == old
        yield new[4:]
        assert not new_path.exists()

    key = store.receive(new_desc, chunks())
    assert key == f"sync/BVmedia/{hashlib.sha256(new).hexdigest()}.mp4"
    assert "\\" not in key and not Path(key).is_absolute()
    assert store.verified_path(new_desc).read_bytes() == new
    assert (store.root / old_key).read_bytes() == old
    assert not parts(store.root)


def test_retransmission_reads_existing_bytes_and_checks_new_stream(tmp_path):
    store = MediaStore(tmp_path / "media")
    data = b"same-content"
    media = descriptor(data)
    key = store.receive(media, [data])
    before = (store.root / key).stat()
    assert store.receive(media, [data[:2], b"", data[2:]]) == key
    after = (store.root / key).stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    with pytest.raises(SyncError) as caught:
        store.receive(media, [b"wrongcontent"])
    assert caught.value.code == "SYNC_MEDIA_HASH_MISMATCH"
    assert (store.root / key).read_bytes() == data
    assert not parts(store.root)


def test_existing_corrupt_hash_filename_is_never_accepted_or_overwritten(tmp_path):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"right")
    path = store.root / store.storage_key(media)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"wrong")
    for operation in (lambda: store.verified_path(media), lambda: store.receive(media, [b"right"])):
        with pytest.raises(SyncError) as caught:
            operation()
        assert caught.value.code == "SYNC_MEDIA_CORRUPT"
        assert path.read_bytes() == b"wrong"


@pytest.mark.parametrize("chunks,code", [
    ([], "SYNC_MEDIA_SIZE_MISMATCH"), ([b"ab"], "SYNC_MEDIA_SIZE_MISMATCH"),
    ([b"abcdef"], "SYNC_MEDIA_TOO_LARGE"), ([b"wrong"], "SYNC_MEDIA_HASH_MISMATCH"),
    ([b"a", "text"], "SYNC_MEDIA_STREAM_ERROR"), ([bytearray(b"right")], "SYNC_MEDIA_STREAM_ERROR"),
    ([memoryview(b"right")], "SYNC_MEDIA_STREAM_ERROR"), ([None], "SYNC_MEDIA_STREAM_ERROR"),
    (None, "SYNC_MEDIA_STREAM_ERROR"), (b"right", "SYNC_MEDIA_STREAM_ERROR"),
])
def test_invalid_streams_are_controlled_and_leave_no_temporary_or_published_file(tmp_path, chunks, code):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"right")
    with pytest.raises(SyncError) as caught:
        store.receive(media, chunks)
    assert caught.value.code == code
    assert not (store.root / store.storage_key(media)).exists()
    assert not parts(store.root)


def test_interrupted_stream_is_sanitized_and_old_versions_remain_readable(tmp_path):
    store = MediaStore(tmp_path / "media")
    old, new = b"previous", b"next-version"
    store.receive(descriptor(old), [old])

    def broken():
        yield new[:3]
        raise OSError("private-token at C:/private/media")

    with pytest.raises(SyncError) as caught:
        store.receive(descriptor(new), broken())
    assert caught.value.code == "SYNC_MEDIA_STREAM_ERROR"
    assert "private" not in str(caught.value)
    assert store.verified_path(descriptor(old)).read_bytes() == old
    assert not parts(store.root)


def test_failing_iterator_creation_is_controlled(tmp_path):
    class Broken:
        def __iter__(self):
            raise RuntimeError("private initialization details")

    with pytest.raises(SyncError) as caught:
        MediaStore(tmp_path / "media").receive(descriptor(), Broken())
    assert caught.value.code == "SYNC_MEDIA_STREAM_ERROR" and "private" not in str(caught.value)


@pytest.mark.parametrize("changes", [
    {"video_id": "../escape"}, {"video_id": "a/b"}, {"video_id": "a\\b"}, {"video_id": "C:escape"},
    {"video_id": "CON"}, {"extension": "../mp4"}, {"sha256": "0" * 63},
    {"byte_size": True}, {"byte_size": -1}, {"byte_size": 2**40}, {"storage_key": "/arbitrary"},
])
def test_descriptor_paths_and_sizes_remain_protocol_whitelisted(tmp_path, changes):
    store = MediaStore(tmp_path / "media")
    for operation in (store.storage_key, store.verified_path, lambda media: store.receive(media, [b"synthetic-video"])):
        with pytest.raises(SyncError):
            operation(descriptor(**changes))
    assert not list(store.root.iterdir())


@pytest.mark.parametrize("bad", [True, 0, -1, "10", None, pytest.param(10**10000, id="huge-integer")])
def test_invalid_quota_does_not_create_storage(tmp_path, bad):
    root = tmp_path / "media"
    with pytest.raises(SyncError):
        MediaStore(root, max_temp_bytes=bad)
    assert not root.exists()


def test_quota_is_reserved_before_consuming_stream_and_unknown_files_count(tmp_path):
    store = MediaStore(tmp_path / "media", max_temp_bytes=6)
    temporary = store.root / ".sync-tmp"
    temporary.mkdir()
    foreign = temporary / "keep.bin"
    foreign.write_bytes(b"keep")

    def never_consume():
        pytest.fail("quota must be reserved before reading the input")
        yield b"right"

    with pytest.raises(SyncError) as caught:
        store.receive(descriptor(b"right"), never_consume())
    assert caught.value.code == "SYNC_MEDIA_TEMP_QUOTA"
    assert foreign.read_bytes() == b"keep"
    assert not parts(store.root)


def test_locked_cleanup_removes_only_recognized_crash_files(tmp_path):
    store = MediaStore(tmp_path / "media", max_temp_bytes=6)
    temporary = store.root / ".sync-tmp"
    temporary.mkdir()
    orphan = temporary / (uuid.uuid4().hex + ".part")
    orphan.write_bytes(b"orphan larger than quota")
    preserved = temporary / "user.part"
    preserved.write_bytes(b"!")
    store.receive(descriptor(b"right"), [b"right"])
    assert not orphan.exists()
    assert preserved.read_bytes() == b"!"
    assert list(temporary.glob("*.part")) == [preserved]


def test_two_process_uploads_share_one_temporary_quota_without_deleting_active_file(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    ctx = multiprocessing.get_context("spawn")
    first_entered, second_entered = ctx.Event(), ctx.Event()
    release_reader, release_writer = ctx.Pipe(duplex=False)
    result_a, send_a = ctx.Pipe(duplex=False)
    result_b, send_b = ctx.Pipe(duplex=False)
    first_data, second_data = b"a" * 20000, b"b" * 20000
    first = ctx.Process(target=_upload_process, args=(str(root), descriptor(first_data), first_data, first_entered, release_reader, send_a))
    second = ctx.Process(target=_upload_process, args=(str(root), descriptor(second_data), second_data, second_entered, None, send_b))
    try:
        first.start()
        release_reader.close()
        send_a.close()
        assert first_entered.wait(timeout=8)
        active = parts(root)
        assert len(active) == 1 and active[0].stat().st_size > 0
        second.start()
        send_b.close()
        assert not second_entered.wait(timeout=0.4)
        assert active[0].exists() and len(parts(root)) == 1
        assert sum(path.stat().st_size for path in (root / ".sync-tmp").iterdir()) <= len(first_data) + 1
        release_writer.send(True)
        assert result_a.poll(8) and "key" in result_a.recv()
        assert result_b.poll(8) and "key" in result_b.recv()
        first.join(timeout=3)
        second.join(timeout=3)
        assert first.exitcode == 0 and second.exitcode == 0
        assert not parts(root)
        store = MediaStore(root)
        assert store.verified_path(descriptor(first_data)).read_bytes() == first_data
        assert store.verified_path(descriptor(second_data)).read_bytes() == second_data
    finally:
        try:
            release_writer.send(True)
        except (BrokenPipeError, OSError):
            pass
        stop_process(first)
        stop_process(second)
        for endpoint in (release_reader, release_writer, result_a, send_a, result_b, send_b):
            endpoint.close()


def test_killed_process_orphan_is_reclaimed_under_os_lock(tmp_path):
    root = tmp_path / "media"
    ctx = multiprocessing.get_context("spawn")
    entered = ctx.Event()
    release_reader, release_writer = ctx.Pipe(duplex=False)
    result, sender = ctx.Pipe(duplex=False)
    data = b"x" * 20000
    process = ctx.Process(target=_upload_process, args=(str(root), descriptor(data), data, entered, release_reader, sender))
    try:
        process.start()
        release_reader.close()
        sender.close()
        assert entered.wait(timeout=8)
        orphan = parts(root)[0]
        assert orphan.stat().st_size > 0
        process.terminate()
        process.join(timeout=3)
        assert not process.is_alive()
        store = MediaStore(root, max_temp_bytes=5)
        store.receive(descriptor(b"right"), [b"right"])
        assert not orphan.exists() and not parts(root)
        assert store.verified_path(descriptor(b"right")).read_bytes() == b"right"
    finally:
        stop_process(process)
        for endpoint in (release_reader, release_writer, result, sender):
            endpoint.close()


@pytest.mark.parametrize("location", ["root", "temporary", "sync", "video"])
def test_directory_symlinks_and_windows_junctions_cannot_redirect_uploads(tmp_path, location):
    root, outside = tmp_path / "media", tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"untouched")
    store = MediaStore(root)
    link = {"root": root, "temporary": root / ".sync-tmp", "sync": root / "sync", "video": root / "sync/BVmedia"}[location]
    if location == "root":
        root.rmdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    directory_link(link, outside)
    try:
        with pytest.raises(SyncError) as caught:
            store.receive(descriptor(), [b"synthetic-video"])
        assert caught.value.code == "SYNC_MEDIA_UNSAFE_PATH"
        assert list(outside.iterdir()) == [marker]
        assert marker.read_bytes() == b"untouched"
    finally:
        remove_directory_link(link)


def test_linked_ancestor_is_rejected_before_creating_root(tmp_path):
    outside, alias = tmp_path / "outside", tmp_path / "alias"
    outside.mkdir()
    directory_link(alias, outside)
    try:
        with pytest.raises(SyncError):
            MediaStore(alias / "new-media")
        assert not list(outside.iterdir())
    finally:
        remove_directory_link(alias)


def test_final_file_hard_link_is_rejected_and_outside_bytes_preserved(tmp_path):
    store = MediaStore(tmp_path / "media")
    data = b"private-data"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(data)
    media = descriptor(data)
    destination = store.root / store.storage_key(media)
    destination.parent.mkdir(parents=True)
    os.link(outside, destination)
    for operation in (lambda: store.receive(media, [data]), lambda: store.verified_path(media)):
        with pytest.raises(SyncError) as caught:
            operation()
        assert caught.value.code == "SYNC_MEDIA_UNSAFE_PATH"
    assert outside.read_bytes() == data


def test_final_file_symlink_is_never_followed(tmp_path):
    store = MediaStore(tmp_path / "media")
    data = b"private-data"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(data)
    media = descriptor(data)
    destination = store.root / store.storage_key(media)
    destination.parent.mkdir(parents=True)
    try:
        destination.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable; real junction tests still run")
        raise
    try:
        for operation in (lambda: store.receive(media, [data]), lambda: store.verified_path(media)):
            with pytest.raises(SyncError):
                operation()
        assert outside.read_bytes() == data
    finally:
        destination.unlink()


def test_directory_substitution_during_stream_never_writes_outside_root(tmp_path):
    store = MediaStore(tmp_path / "media")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim, moved = store.root / "sync/BVmedia", store.root / "moved"
    blocked = []

    def chunks():
        yield b"syn"
        try:
            victim.rename(moved)
        except PermissionError:
            blocked.append(True)
        else:
            directory_link(victim, outside)
        yield b"thetic-video"

    try:
        if os.name == "nt":
            key = store.receive(descriptor(), chunks())
            assert blocked and (store.root / key).read_bytes() == b"synthetic-video"
        else:
            with pytest.raises(SyncError):
                store.receive(descriptor(), chunks())
        assert not list(outside.iterdir())
        assert not parts(store.root)
    finally:
        if moved.exists() and (victim.is_symlink() or (os.name == "nt" and victim.is_junction())):
            remove_directory_link(victim)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle permission boundary")
def test_system_ancestors_are_checked_without_requesting_directory_handles(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    native = sync_media._win_open
    opened = []

    def guard(path, **kwargs):
        if kwargs.get("directory"):
            assert Path(path).is_relative_to(root)
            opened.append(Path(path))
        return native(path, **kwargs)

    monkeypatch.setattr(sync_media, "_win_open", guard)
    store = MediaStore(root)
    store.receive(descriptor(), [b"synthetic-video"])
    assert opened


def test_disk_full_is_sanitized_and_partial_files_removed(tmp_path, monkeypatch):
    store = MediaStore(tmp_path / "media")
    previous = b"old-media"
    store.receive(descriptor(previous), [previous])

    def full(_):
        raise OSError(errno.ENOSPC, "private-device C:/private/full")

    monkeypatch.setattr(sync_media.os, "fsync", full)
    with pytest.raises(SyncError) as caught:
        store.receive(descriptor(), [b"synthetic-video"])
    assert caught.value.code == "SYNC_MEDIA_STORAGE_FULL" and caught.value.status == 507
    assert "private" not in str(caught.value)
    assert not parts(store.root)
    assert not (store.root / store.storage_key(descriptor())).exists()
    assert store.verified_path(descriptor(previous)).read_bytes() == previous


@pytest.mark.parametrize("collision", [b"synthetic-video", b"different-bytes"])
def test_publication_never_overwrites_a_file_created_at_the_last_moment(tmp_path, monkeypatch, collision):
    store = MediaStore(tmp_path / "media")
    real_rename = sync_media._rename_new

    def race(source, name, destination, target):
        (destination.path / target).write_bytes(collision)
        real_rename(source, name, destination, target)

    monkeypatch.setattr(sync_media, "_rename_new", race)
    media = descriptor()
    if collision == b"synthetic-video":
        store.receive(media, [b"synthetic-video"])
    else:
        with pytest.raises(SyncError) as caught:
            store.receive(media, [b"synthetic-video"])
        assert caught.value.code == "SYNC_MEDIA_CORRUPT"
    assert (store.root / store.storage_key(media)).read_bytes() == collision
    assert not parts(store.root)


def test_failed_exclusive_creation_does_not_delete_the_colliding_file(tmp_path, monkeypatch):
    store = MediaStore(tmp_path / "media")
    native = sync_media._Directory.open_file
    colliding = []

    def race(directory, name, **kwargs):
        if kwargs.get("create"):
            path = directory.path / name
            path.write_bytes(b"not-this-upload")
            colliding.append(path)
        return native(directory, name, **kwargs)

    monkeypatch.setattr(sync_media._Directory, "open_file", race)
    with pytest.raises(SyncError):
        store.receive(descriptor(), [b"synthetic-video"])
    assert len(colliding) == 1 and colliding[0].read_bytes() == b"not-this-upload"


def test_hold_verified_pins_multiple_files_and_returns_controlled_paths(tmp_path):
    store = MediaStore(tmp_path / "media")
    a, b = descriptor(b"first", video_id="BVa"), descriptor(b"second", video_id="BVb")
    store.receive(a, [b"first"])
    store.receive(b, [b"second"])
    with store.hold_verified([a, b]) as held:
        assert held == {"BVa": store.root / store.storage_key(a), "BVb": store.root / store.storage_key(b)}
        assert held["BVa"].read_bytes() == b"first"
        assert held["BVb"].read_bytes() == b"second"
    with store.hold_verified([]) as held:
        assert held == {}


@pytest.mark.parametrize("change", ["delete", "replace", "rewrite"])
def test_hold_verified_refuses_mutation_during_publication(tmp_path, change):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"original")
    path = store.root / store.receive(media, [b"original"])
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"modified")

    def mutate():
        if change == "delete":
            path.unlink()
        elif change == "replace":
            os.replace(replacement, path)
        else:
            path.write_bytes(b"modified")

    if os.name == "nt":
        with store.hold_verified([media]):
            with pytest.raises(PermissionError):
                mutate()
        assert path.read_bytes() == b"original"
    else:
        with pytest.raises(SyncError) as caught:
            with store.hold_verified([media]):
                mutate()
        assert caught.value.code == "SYNC_MEDIA_CORRUPT"


def test_hold_verified_bad_media_fails_before_publication_body(tmp_path):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"right")
    with pytest.raises(SyncError) as caught:
        with store.hold_verified([media]):
            pytest.fail("missing media cannot authorize publication")
    assert caught.value.code == "SYNC_MEDIA_NOT_FOUND"
    store.receive(media, [b"right"])
    (store.root / store.storage_key(media)).write_bytes(b"wrong")
    with pytest.raises(SyncError) as caught:
        with store.hold_verified([media]):
            pytest.fail("corrupt media cannot authorize publication")
    assert caught.value.code == "SYNC_MEDIA_CORRUPT"


def test_hold_verified_releases_handles_after_body_exception(tmp_path):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"right")
    path = store.root / store.receive(media, [b"right"])
    with pytest.raises(RuntimeError, match="synthetic publication failure"):
        with store.hold_verified([media]):
            raise RuntimeError("synthetic publication failure")
    path.unlink()
    assert not path.exists()


@pytest.mark.parametrize("error", [
    OSError("synthetic caller I/O failure"),
    FileNotFoundError("synthetic caller missing file"),
    sqlite3.OperationalError("synthetic caller database failure"),
    SyncError("synthetic caller sync failure", "CALLER_FAILURE", 409),
])
def test_hold_verified_preserves_caller_error_object_and_releases_handles(tmp_path, error):
    store = MediaStore(tmp_path / "media")
    media = descriptor(b"right")
    path = store.root / store.receive(media, [b"right"])
    with pytest.raises(type(error)) as caught:
        with store.hold_verified([media]):
            raise error
    assert caught.value is error
    path.unlink()
    assert not path.exists()


@pytest.mark.parametrize("values", [None, {}, [descriptor(), descriptor()], [descriptor()] * 201])
def test_hold_verified_input_is_bounded_and_unambiguous(tmp_path, values):
    with pytest.raises(SyncError):
        with MediaStore(tmp_path / "media").hold_verified(values):
            pytest.fail("invalid media must not enter publication")


def test_deep_storage_paths_preserve_streaming_and_publication(tmp_path):
    root = tmp_path / ("long-" + "a" * 75) / ("nested-" + "b" * 75) / "media"
    store = MediaStore(root)
    media = descriptor()
    final = store.root / store.receive(media, [b"synthetic-video"])
    assert len(str(final)) > 260
    with store.hold_verified([media]) as held:
        assert held[media["video_id"]].read_bytes() == b"synthetic-video"
    assert store.verified_path(media).read_bytes() == b"synthetic-video"


def test_hold_verified_uses_snapshot_contract_instead_of_a_private_200_video_limit(tmp_path):
    store = MediaStore(tmp_path / "media")
    many = [descriptor(video_id=f"BV{index}") for index in range(201)]
    with pytest.raises(SyncError) as caught:
        with store.hold_verified(many):
            pytest.fail("artificial files were not created")
    assert caught.value.code == "SYNC_MEDIA_NOT_FOUND"


@pytest.mark.parametrize("name", ["../outside", "a/b", "a\\b", "C:outside", ".", "..", "", "nul\x00name"])
def test_private_directory_helpers_cannot_be_used_for_path_escape(tmp_path, name):
    with sync_media._Directory(tmp_path) as directory:
        for operation in (directory.entry_stat, directory.unlink, directory.open_file):
            with pytest.raises(SyncError):
                operation(name)


@pytest.mark.parametrize("kind", ["thread", "process"])
def test_real_upload_lock_times_out_without_consuming_stream(tmp_path, monkeypatch, kind):
    store = MediaStore(tmp_path / "media")
    calls = {"clock": 0.0, "sleeps": 0, "checks": 0}

    def accelerated_sleep(seconds):
        assert 0 < seconds <= 0.05
        calls["sleeps"] += 1
        calls["clock"] += 5.0

    def check():
        calls["checks"] += 1

    def unread():
        pytest.fail("A contender without the OS lock must not consume the stream")
        yield b"synthetic-video"

    with held_upload_lock(store.root, kind) as owner:
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(sync_media, "monotonic", lambda: calls["clock"])
            clock_patch.setattr(sync_media, "sleep", accelerated_sleep)
            with pytest.raises(SyncError) as rejected:
                store.receive(descriptor(), unread(), check_cancelled=check)
        assert rejected.value.code == "SYNC_MEDIA_BUSY" and rejected.value.status == 409
        assert calls["clock"] == 30 and calls["sleeps"] == 6 and calls["checks"] >= 6
        assert owner.is_alive() and not parts(store.root)
    store.receive(descriptor(), [b"synthetic-video"])
    assert store.verified_path(descriptor()).read_bytes() == b"synthetic-video"


@pytest.mark.parametrize("kind", ["thread", "process"])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, SyncError])
def test_waiting_upload_cancellation_preserves_exception_and_other_owners_lock(tmp_path, monkeypatch, kind, error_type):
    store = MediaStore(tmp_path / "media")
    cancellation = error_type("controlled caller cancellation")
    waited = []

    def check():
        if waited:
            raise cancellation

    with held_upload_lock(store.root, kind) as owner:
        with monkeypatch.context() as waiter_patch:
            waiter_patch.setattr(sync_media, "sleep", lambda seconds: waited.append(seconds))
            with pytest.raises(error_type) as caught:
                store.receive(descriptor(), [b"synthetic-video"], check_cancelled=check)
        assert caught.value is cancellation and len(waited) == 1
        assert owner.is_alive() and not parts(store.root)
    # Both the contender's directory/file handles and its failed lock attempt
    # must be gone, while the other owner's release remains under its control.
    store.receive(descriptor(), [b"synthetic-video"])
    assert not parts(store.root)


@pytest.mark.parametrize("phase", ["stream", "existing_verification", "before_publication"])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, SyncError, OSError, FileNotFoundError, RuntimeError])
def test_cancellation_at_each_upload_phase_keeps_error_identity_and_cleans_resources(tmp_path, monkeypatch, phase, error_type):
    store = MediaStore(tmp_path / "media")
    media, data = descriptor(), b"synthetic-video"
    old = descriptor(b"old-published-media")
    store.receive(old, [b"old-published-media"])
    if phase == "existing_verification":
        store.receive(media, [data])
    cancellation = error_type("controlled caller cancellation")
    cancel = {"active": False}

    def check():
        if cancel["active"]:
            raise cancellation

    def chunks():
        yield data[:3]
        if phase == "stream":
            cancel["active"] = True
        yield data[3:]

    original_open = sync_media._Directory.open_file
    original_verify = sync_media._verify_file

    def open_for_verification(directory, name, **kwargs):
        stream = original_open(directory, name, **kwargs)
        if phase == "existing_verification" and name == media["sha256"] + ".mp4":
            cancel["active"] = True
        return stream

    def verified_temporary(directory, name, descriptor, **kwargs):
        original_verify(directory, name, descriptor, **kwargs)
        if phase == "before_publication" and directory.path.name == ".sync-tmp":
            cancel["active"] = True

    with monkeypatch.context() as phase_patch:
        phase_patch.setattr(sync_media._Directory, "open_file", open_for_verification)
        phase_patch.setattr(sync_media, "_verify_file", verified_temporary)
        with pytest.raises(error_type) as caught:
            store.receive(media, chunks(), check_cancelled=check)
        assert caught.value is cancellation
    assert not parts(store.root)
    assert store.verified_path(old).read_bytes() == b"old-published-media"
    path = store.root / store.storage_key(media)
    if phase == "existing_verification":
        assert path.read_bytes() == data
    else:
        assert not path.exists()
    # A successful retry also proves the OS lock and pinned handles released.
    store.receive(media, [data])
    assert store.verified_path(media).read_bytes() == data


@pytest.mark.parametrize("error_type", [asyncio.CancelledError, SyncError])
def test_transport_iterator_cancellation_is_not_rewritten_as_body_error(tmp_path, error_type):
    store = MediaStore(tmp_path / "media")
    cancellation = error_type("controlled transport cancellation")

    def chunks():
        yield b"syn"
        raise cancellation

    with pytest.raises(error_type) as caught:
        store.receive(descriptor(), chunks())
    assert caught.value is cancellation and not parts(store.root)
    store.receive(descriptor(), [b"synthetic-video"])


@pytest.mark.parametrize("callback", [False, 1, "cancel"])
def test_noncallable_cancellation_hook_is_rejected(tmp_path, callback):
    store = MediaStore(tmp_path / "media")
    with pytest.raises(SyncError) as caught:
        store.receive(descriptor(), [b"synthetic-video"], check_cancelled=callback)
    assert caught.value.code == "INVALID_SYNC_REQUEST"
    assert not (store.root / ".sync-tmp").exists()
