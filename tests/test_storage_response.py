"""A response owns its opened file even after the lifecycle read lock ends."""
import asyncio
import gc
from io import BytesIO
import os

from fastapi import HTTPException
import pytest

from app.storage_response import OpenFileResponse, media_response
from reno import storage_admin, storage_lifecycle as lifecycle


@pytest.fixture
def media(tmp_path):
    profile = storage_admin.profile(database=tmp_path / "data/reno.db", media=tmp_path / "media",
                                    reports=tmp_path / "reports", checkpoints=tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    path = profile.media / "video.mp4"
    path.write_bytes(b"0123456789")
    return profile, path


async def consume(response):
    async def receive():
        await asyncio.Future()

    messages = []

    async def send(message):
        messages.append(message)

    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    return b"".join(message.get("body", b"") for message in messages)


@pytest.mark.parametrize(("header", "status", "body", "content_range"), [
    (None, 200, b"0123456789", None),
    ("bytes=2-5", 206, b"2345", "bytes 2-5/10"),
    ("bytes=8-", 206, b"89", "bytes 8-9/10"),
    ("bytes=-3", 206, b"789", "bytes 7-9/10"),
    ("bytes=-999", 206, b"0123456789", "bytes 0-9/10"),
    ("bytes=8-999", 206, b"89", "bytes 8-9/10"),
])
def test_range_reads_exact_preopened_bytes(media, header, status, body, content_range):
    profile, path = media
    with lifecycle.shared(profile):
        response = media_response(path, header, profile=profile)
    assert response.status_code == status
    assert response.headers["content-length"] == str(len(body))
    assert response.headers.get("content-range") == content_range
    assert asyncio.run(consume(response)) == body
    if header is None:
        assert not response._closer.alive


@pytest.mark.parametrize("header", ["bytes=", "bytes=-", "bytes=5-3", "bytes=10-", "bytes=-0",
                                  "bytes=0-2,4-5", "bytes=１-２", "bytes=" + "9" * 161 + "-"])
def test_invalid_range_releases_file(media, header):
    profile, path = media
    with lifecycle.shared(profile), pytest.raises(HTTPException) as caught:
        media_response(path, header, profile=profile)
    assert caught.value.status_code == 416
    assert caught.value.headers["Content-Range"] == "bytes */10"
    path.unlink()  # On Windows this also proves no open file was leaked.


def test_explicit_start_response_has_exact_four_mib_window(media):
    profile, path = media
    path.write_bytes(b"x" * (5 * 1024 * 1024))
    with lifecycle.shared(profile):
        response = media_response(path, "bytes=0-", profile=profile)
    assert response.headers["content-length"] == str(4 * 1024 * 1024)
    assert len(asyncio.run(consume(response))) == 4 * 1024 * 1024


def test_large_suffix_is_bounded_and_range_owns_bytes_before_unlock(media):
    profile, path = media
    path.write_bytes(b"a" * 1024 * 1024 + b"b" * (4 * 1024 * 1024))
    with lifecycle.shared(profile):
        response = media_response(path, "bytes=-99999999", profile=profile)
    with lifecycle.exclusive(profile, timeout=0):
        path.unlink()
    assert response.headers["content-range"] == "bytes 1048576-5242879/5242880"
    assert response.body == b"b" * (4 * 1024 * 1024)


def test_foreign_media_root_is_rejected_before_open(media, tmp_path):
    profile, _ = media
    foreign = tmp_path / "other-tree" / "video.mp4"
    foreign.parent.mkdir()
    foreign.write_bytes(b"private")
    with lifecycle.shared(profile), pytest.raises(HTTPException) as caught:
        media_response(foreign, profile=profile)
    assert caught.value.status_code == 409
    assert str(foreign) not in caught.value.detail


def test_empty_file_and_missing_file(media):
    profile, path = media
    path.write_bytes(b"")
    with lifecycle.shared(profile):
        response = media_response(path, profile=profile)
        with pytest.raises(HTTPException) as caught:
            media_response(path, "bytes=0-", profile=profile)
        assert caught.value.status_code == 416
    assert asyncio.run(consume(response)) == b""
    path.unlink()
    with lifecycle.shared(profile), pytest.raises(HTTPException) as caught:
        media_response(path, profile=profile)
    assert caught.value.status_code == 404


def test_media_requires_guard_before_open(media):
    profile, path = media
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        media_response(path, profile=profile)
    assert caught.value.code == "STORAGE_GUARD_REQUIRED"


def test_open_response_prevents_deletion_or_survives_unlink(media):
    profile, path = media
    with lifecycle.shared(profile):
        response = media_response(path, profile=profile)
    with lifecycle.exclusive(profile, timeout=0):
        if os.name == "nt":
            with pytest.raises(PermissionError):
                path.unlink()
        else:
            path.unlink()
            path.write_bytes(b"different bytes at the old path")
    assert asyncio.run(consume(response)) == b"0123456789"
    path.unlink()


def test_unsafe_hard_link_is_controlled_and_does_not_expose_path(media):
    profile, path = media
    os.link(path, path.with_suffix(".alias"))
    with lifecycle.shared(profile), pytest.raises(HTTPException) as caught:
        media_response(path, profile=profile)
    assert caught.value.status_code in (409, 503)
    assert str(path) not in caught.value.detail


@pytest.mark.parametrize(("failure", "spec"), [("send", "2.0"), ("disconnect", "2.0"),
                                              ("cancel", "2.0"), ("send", "2.4"), ("cancel", "2.4")])
def test_asgi_failure_closes_response_file(failure, spec):
    stream = BytesIO(b"x" * 1024)
    response = OpenFileResponse(stream, start=0, length=1024, status_code=200, headers={})

    async def scenario():
        started = asyncio.Event()

        async def receive():
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            started.set()
            if failure == "send":
                raise RuntimeError("synthetic send failure")
            await asyncio.Future()

        task = asyncio.create_task(response({"type": "http", "asgi": {"spec_version": spec}}, receive, send))
        if failure == "cancel":
            await started.wait()
            task.cancel()
        try:
            await task
        except (RuntimeError, BaseExceptionGroup, asyncio.CancelledError):
            if failure == "disconnect":
                raise

    asyncio.run(scenario())
    assert stream.closed


def test_discarded_response_releases_unconsumed_stream():
    stream = BytesIO(b"x")
    response = OpenFileResponse(stream, start=0, length=1, status_code=200, headers={})
    del response
    gc.collect()
    assert stream.closed
