"""Stream a pre-opened immutable file; no delayed reopen after GC unlocks."""
from __future__ import annotations

import os
from pathlib import Path
import re
import weakref

from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from reno import storage_lifecycle
from reno.sync_media import _Directory
from reno.sync_protocol import SyncError

RANGE_MAX_BYTES = 4 * 1024 * 1024


def protected_media_path(path, *, profile):
    """A guard for one media tree cannot protect pointers into another tree."""
    storage_lifecycle.require_guard(profile)
    path = Path(path)
    if ".." in path.parts or not path.absolute().is_relative_to(profile.media):
        raise HTTPException(409, "媒体路径不属于当前知识库，请先核对存储配置。")
    return path.absolute()


def _chunks(stream, start, length):
    stream.seek(start)
    remaining = length
    while remaining:
        chunk = stream.read(min(256 * 1024, remaining))
        if not chunk:
            raise OSError("Media changed during the response")
        remaining -= len(chunk)
        yield chunk


class OpenFileResponse(StreamingResponse):
    """Own a file through completion, disconnect, cancellation and send errors."""
    def __init__(self, stream, *, start, length, status_code, headers):
        self._closer = weakref.finalize(self, stream.close)
        try:
            super().__init__(_chunks(stream, start, length), status_code=status_code,
                             media_type="video/mp4", headers=headers)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._closer()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.close()


def media_response(path, range_header=None, *, profile):
    """Validate and open while the caller still holds its DB/file read guard."""
    guard = storage_lifecycle.require_guard(profile)
    path = protected_media_path(path, profile=profile)  # Preserve links for no-follow validation.
    stream = None
    try:
        with _Directory(path.parent, base=profile.media) as directory:
            stream = directory.open_file(path.name)
            actual = os.fstat(stream.fileno())
            entry = directory.entry_stat(path.name)
            if (actual.st_dev, actual.st_ino, actual.st_size) != (entry.st_dev, entry.st_ino, entry.st_size):
                raise HTTPException(409, "媒体文件已变化，请重试。")
            directory.verify()
            guard.verify()
        size = actual.st_size
        start, length, status = 0, size, 200
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
        if range_header is not None:
            invalid = lambda: HTTPException(416, "invalid Range header", headers={"Content-Range": f"bytes */{size}"})
            if len(range_header) > 160:
                raise invalid()
            match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header.strip())
            if not match or not any(match.groups()) or size == 0:
                raise invalid()
            left, right = match.groups()
            if not left:
                suffix = int(right)
                if suffix == 0:
                    raise invalid()
                start, end = max(0, size - min(suffix, RANGE_MAX_BYTES)), size - 1
            else:
                start = int(left)
                if start >= size:
                    raise invalid()
                end = min(start + RANGE_MAX_BYTES - 1, size - 1)
                if right:
                    end = min(end, int(right))
                if end < start:
                    raise invalid()
            length, status = end - start + 1, 206
            headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
            stream.seek(start)
            data = stream.read(length)
            if len(data) != length:
                raise HTTPException(409, "媒体文件已变化，请重试。")
            guard.verify()
            return Response(data, status_code=status, media_type="video/mp4", headers=headers)
        response = OpenFileResponse(stream, start=start, length=length, status_code=status, headers=headers)
        stream = None  # Response now owns the exact already-open file object.
        return response
    except FileNotFoundError:
        raise HTTPException(404, "no media file") from None
    except SyncError:
        raise HTTPException(409, "媒体文件状态已变化，暂时无法读取。") from None
    except OSError:
        raise HTTPException(503, "媒体文件暂时无法读取，请稍后重试。") from None
    finally:
        if stream is not None:
            stream.close()
