"""Header-authenticated transport for the original personal-mirror operation.

This router is deliberately self-gated even when mounted without app.main.
It never pairs a peer, installs facilities, grants personal reading, changes
primary, or accepts a caller's attachment descriptor as authorization. Only
the persisted prepare selects a body. Runtime methods own their real SQL
transactions; a cancelled request drains its worker before returning capacity.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
import sqlite3
import threading
import time

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer as runtime
from reno import personal_mirror_transfer_contract as wire
from reno import personal_mirror_transfer_files as files
from reno import storage_admin, storage_lifecycle as lifecycle
from reno.migrations import SchemaVersionError
from reno.sync_access import require_sync_access
from reno.sync_protocol import SyncError

MAX_PREPARE_BYTES = 2 * facts.MAX_SNAPSHOT_BYTES + 64 * 1024
MAX_CONTROL_BYTES = 64 * 1024
MAX_ATTACHMENT_HEADER = 8192
JSON_TOTAL_SECONDS = 60
ATTACHMENT_IDLE_SECONDS = 30
ATTACHMENT_TOTAL_SECONDS = 3600
CONTROL_TOTAL_SECONDS = 300
ATTACHMENT_HEADER = "x-reno-personal-mirror"
_requests = threading.BoundedSemaphore(2)
_NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
_ERROR_CODE = re.compile(r"(?:SYNC_|STORAGE_|PERSONAL_MIRROR_|SNAPSHOT_)[A-Z0-9_]{1,96}\Z")
_OTHER_CODES = {"INCOMPATIBLE_SYNC_SCHEMA", "INCOMPATIBLE_SYNC_PROTOCOL", "INVALID_SYNC_REQUEST", "SCHEMA_UPGRADE_REQUIRED"}


def _invalid(status=400):
    raise SyncError("Invalid personal mirror request", "PERSONAL_MIRROR_INVALID_REQUEST", status)


def _error(exc):
    code = getattr(exc, "code", None)
    if type(code) is not str or not (_ERROR_CODE.fullmatch(code) or code in _OTHER_CODES):
        code = "PERSONAL_MIRROR_REQUEST_FAILED"
    status = getattr(exc, "status", 409)
    if type(status) is not int or not 400 <= status <= 599:
        status = 500
    # Neither exceptions, request bytes, tokens, nor local storage paths enter
    # error detail. Machine codes retain the underlying stable domain failure.
    return JSONResponse({"detail": "Personal mirror request could not be verified", "code": code},
                        status_code=status, headers=_NO_STORE)


class PersonalMirrorRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            acquired = False
            try:
                require_sync_access(request)
                if not runtime._enabled():
                    raise SyncError("Personal mirror transport is disabled", "PERSONAL_MIRROR_DISABLED", 403)
                if request.query_params:
                    _invalid()
                if not _requests.acquire(blocking=False):
                    raise SyncError("Personal mirror receiver is busy", "PERSONAL_MIRROR_BUSY", 429)
                acquired = True
                response = await original(request)
                response.headers.update(_NO_STORE)
                return response
            except (SyncError, lifecycle.StorageLifecycleError) as exc:
                return _error(exc)
            except (TimeoutError, ClientDisconnect):
                return _error(SyncError("Request body was interrupted", "PERSONAL_MIRROR_BODY_INTERRUPTED", 408))
            except SchemaVersionError:
                return _error(SyncError("Explicit database initialization or upgrade is required", "SCHEMA_UPGRADE_REQUIRED", 503))
            except (sqlite3.Error, OSError):
                return _error(SyncError("Storage is unavailable", "PERSONAL_MIRROR_STORAGE_FAILED", 503))
            except Exception:
                return _error(SyncError("Request failed", "PERSONAL_MIRROR_INTERNAL_ERROR", 500))
            finally:
                if acquired:
                    _requests.release()
        return handler


router = APIRouter(prefix="/api/sync/personal-mirror", route_class=PersonalMirrorRoute)


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        _invalid()
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        _invalid()
    return value


def _decode(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_float,
                           parse_constant=lambda _value: _invalid())
        if type(value) is not dict:
            _invalid()
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _invalid()


def _header(request, name, *, required=False):
    values = request.headers.getlist(name)
    if len(values) > 1 or required and len(values) != 1:
        _invalid()
    return values[0] if values else None


def _content_length(request, maximum):
    value = _header(request, "content-length")
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 20:
        _invalid()
    size = int(value)
    if size > maximum:
        _invalid(413)
    return size


def _encoding(request, media_type):
    value = _header(request, "content-type", required=True)
    if value.split(";", 1)[0].strip().lower() != media_type:
        _invalid(415)
    if _header(request, "content-encoding") not in (None, "identity"):
        _invalid(415)


async def _body(request, *, maximum, media_type="application/json"):
    _encoding(request, media_type)
    expected = _content_length(request, maximum)
    content = bytearray()
    with anyio.fail_after(JSON_TOTAL_SECONDS):
        async for chunk in request.stream():
            if len(content) + len(chunk) > maximum:
                _invalid(413)
            content.extend(chunk)
    if expected is not None and len(content) != expected:
        _invalid()
    return bytes(content)


async def _worker(request, function, *, total_seconds=None):
    """Complete/stop this actual worker before releasing the request slot.

    asyncio.shield also covers raw Task.cancel(), which otherwise can abandon
    a thread despite AnyIO's default cancellation shield. Request cancellation
    never implies SQL rollback after an already committed original operation.
    """
    stopped = threading.Event()
    started = time.monotonic()
    if total_seconds is None:
        total_seconds = CONTROL_TOTAL_SECONDS

    def check():
        if stopped.is_set():
            raise SyncError("Request was interrupted", "PERSONAL_MIRROR_BODY_INTERRUPTED", 408)
        anyio.from_thread.check_cancelled()
        if time.monotonic() - started >= total_seconds:
            raise SyncError("Request timed out", "PERSONAL_MIRROR_TIMEOUT", 408)
        # Fresh rotating header authorization also runs in the runtime's last
        # cancellation check, before its final admission/file stat and COMMIT.
        require_sync_access(request)

    def execute():
        check()
        profile = storage_admin.profile()
        with lifecycle.shared(profile):
            check()
            result = function(profile, check)
            check()
            return result

    task = asyncio.create_task(run_in_threadpool(execute))
    try:
        return await asyncio.shield(task)
    except BaseException:
        stopped.set()
        with anyio.CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
        # Consume a finished exception without replacing the caller's original
        # cancellation or proof failure. The task is never left live here.
        try:
            task.result()
        except BaseException:
            pass
        raise


async def _control(request, kind, *, maximum=MAX_CONTROL_BYTES):
    raw = await _body(request, maximum=maximum)
    def execute(profile, check):
        body = _fields(_decode(raw), ("source", "lease", kind))
        check()
        arguments = {"profile": profile, "source": body["source"], "lease": body["lease"]}
        if kind == "prepare":
            return runtime.prepare_mirror_locked(**arguments, prepare=body["prepare"], check_cancelled=check)
        if kind == "commit":
            return runtime.commit_mirror_locked(**arguments, commit=body["commit"], check_cancelled=check)
        _invalid()
    return await _worker(request, execute)


@router.post("/prepare")
async def prepare(request: Request):
    return await _control(request, "prepare", maximum=MAX_PREPARE_BYTES)


@router.post("/commit")
async def commit(request: Request):
    return await _control(request, "commit")


@router.post("/receipt")
async def receipt(request: Request):
    raw = await _body(request, maximum=MAX_CONTROL_BYTES)
    def execute(profile, check):
        body = _fields(_decode(raw), ("source", "lease", "commit"))
        check()
        try:
            result = runtime.mirror_receipt_locked(profile=profile, source=body["source"],
                lease=body["lease"], commit=body["commit"])
        except SyncError as exc:
            # Runtime reaches this exact absence only after real access,
            # schema, pair and live-session admission. No other rejection is
            # converted into permission to begin/replay an operation.
            if exc.code != "PERSONAL_MIRROR_PREPARE_REQUIRED" or exc.status != 404:
                raise
            result = None
        return {"receipt": result}
    return await _worker(request, execute)


def _attachment_metadata(request):
    value = _header(request, ATTACHMENT_HEADER, required=True)
    if not value.isascii() or len(value) > MAX_ATTACHMENT_HEADER or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        _invalid()
    try:
        raw = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        _invalid()
    if base64.urlsafe_b64encode(raw).decode("ascii") != value:
        _invalid()
    body = _fields(_decode(raw), ("source", "lease", "operation_id", "prepare_sha256", "attachment_id"))
    try:
        facts._uuid(body["operation_id"])
        facts._sha(body["prepare_sha256"])
        facts._uuid(body["attachment_id"])
    except facts.PersonalSnapshotError:
        _invalid()
    return body


@router.post("/attachment")
async def attachment(request: Request):
    _encoding(request, "application/octet-stream")
    body = _attachment_metadata(request)
    declared = _content_length(request, wire.MAX_ATTACHMENT_BYTES)
    if declared == 0:
        # An explicit, actually empty body asks to recover the original stored
        # file. Only the file layer can prove it exists; no empty upload is made.
        await _body(request, maximum=0, media_type="application/octet-stream")
    iterator = request.stream().__aiter__() if declared != 0 else None
    started, received = time.monotonic(), 0

    def execute(profile, check):
        original = runtime.original_prepare_locked(profile=profile, source=body["source"], lease=body["lease"],
            operation_id=body["operation_id"], expected_prepare_sha256=body["prepare_sha256"])
        descriptors = [item for item in wire.attachment_manifest(original) if item["attachment_id"] == body["attachment_id"]]
        if len(descriptors) != 1:
            raise SyncError("Attachment was not prepared", "PERSONAL_MIRROR_ATTACHMENT_NOT_PREPARED", 404)
        size = descriptors[0]["byte_size"]
        digest = hashlib.sha256()
        if declared is not None and declared not in (0, size):
            _invalid()

        async def next_chunk():
            nonlocal received
            remaining = ATTACHMENT_TOTAL_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise SyncError("Upload timed out", "PERSONAL_MIRROR_TIMEOUT", 408)
            with anyio.fail_after(min(ATTACHMENT_IDLE_SECONDS, remaining)):
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    return None
            received += len(chunk)
            if received > size:
                _invalid(413)
            return chunk

        def chunks():
            while True:
                check()
                chunk = anyio.from_thread.run(next_chunk)
                if chunk is None:
                    return
                for offset in range(0, len(chunk), 128 * 1024):
                    piece = chunk[offset:offset + 128 * 1024]
                    digest.update(piece)
                    yield piece

        check()
        stream = None if declared == 0 else chunks()
        result = files.store_attachment_locked(profile=profile, source=body["source"], lease=body["lease"],
            operation_id=body["operation_id"], expected_prepare_sha256=body["prepare_sha256"],
            attachment_id=body["attachment_id"], chunks=stream, check_cancelled=check)
        if stream is not None:
            # An already stored original file need not consume its input in the
            # file layer. Drain this very generator outside every SQL writer,
            # so ordinary retransmission still obeys the actual parent size,
            # SHA, interruption and deadline contract. This does not certify
            # files: only the file layer's held original body can do that.
            if runtime.in_write_window() or files.in_write_window():
                raise SyncError("Upload cannot drain inside a writer", "PERSONAL_MIRROR_READ_WINDOW_REQUIRED", 409)
            for _piece in stream:
                check()
            if received != size:
                raise SyncError("Upload size does not match the original body", "PERSONAL_MIRROR_BODY_SIZE_MISMATCH", 422)
            if digest.hexdigest() != descriptors[0]["sha256"]:
                raise SyncError("Upload hash does not match the original body", "PERSONAL_MIRROR_BODY_HASH_MISMATCH", 422)
        check()
        return result
    return await _worker(request, execute, total_seconds=ATTACHMENT_TOTAL_SECONDS)
