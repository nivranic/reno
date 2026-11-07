"""Private, read-only observation of the server's explicit authority target.

This transport never accepts a caller's target or establishes a grant. The
owned synchronous worker has ended before any DTO is serialized or sent.
"""
from __future__ import annotations

import asyncio
from contextvars import Context
from dataclasses import dataclass
import sys
import threading

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from reno import personal_authority_read as runtime

_PRIVATE = {"Cache-Control": "private, no-store"}
_FAILURE = "OBSERVATION_FAILED"
_CANCELLED = "OBSERVATION_CANCELLED"
_INVALID = "INVALID_AUTHORITY_REQUEST"

router = APIRouter(prefix="/api/personal/authority")


@dataclass(frozen=True)
class _Outcome:
    data: object = None
    code: str | None = None
    status: int = 200


def _failure(error):
    # Only bounded machine codes and HTTP statuses cross the transport. Even
    # an unexpected exception value cannot quote source paths or identities.
    code, status = error.code, error.status
    if (type(code) is not str or not 0 < len(code) <= 96 or not all(
            "A" <= item <= "Z" or "0" <= item <= "9" or item == "_" for item in code)):
        code = _FAILURE
    if type(status) is not int or not 400 <= status <= 599:
        status = 503
    return _Outcome(code=code, status=status)


def _note(primary, text):
    try:
        BaseException.add_note(primary, text)
    except BaseException:
        pass


def _start(coroutine, *, name):
    try:
        return asyncio.create_task(coroutine, name=name)
    except BaseException as primary:
        # The coroutine has not been scheduled, so it owns no worker. Closing
        # it also avoids leaving an unawaited object after allocation failure.
        try:
            coroutine.close()
        except BaseException:
            _note(primary, "OBSERVATION_TASK_CLEANUP_FAILED")
        raise


async def _drain(task, *, preserve_cancellation=False):
    """Do not restore request ownership until the actual task has ended."""
    owner = asyncio.current_task() if preserve_cancellation else None
    cancellations = owner.cancelling() if owner is not None else 0
    interruption = None
    failure = None
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                # A deliberately cancelled listener is expected. A new
                # cancellation of its request owner must survive this drain.
                if (owner is not None and owner.cancelling() > cancellations
                        and interruption is None):
                    interruption = error
                continue
            except BaseException as error:
                # A task failure can make shield raise after terminal state.
                # A failure of the await itself cannot establish that state;
                # keep draining, then disclose that cleanup failed.
                if failure is None:
                    failure = error
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except BaseException as error:
        if failure is None:
            failure = error
    if interruption is not None:
        raise interruption
    if failure is not None:
        raise failure


async def _owned_io(callback, stopped):
    # A fresh Context prevents an ambient Home/StorageGuard from being copied
    # into this independent observation window.
    task = _start(
        run_in_threadpool(lambda: Context().run(callback)),
        name="reno-authority-observation-io")
    try:
        return await asyncio.shield(task)
    except BaseException as primary:
        stopped.set()
        try:
            await _drain(task)
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary, "OBSERVATION_TASK_CLEANUP_FAILED")
        raise


async def _listen(receive, stopped):
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            stopped.set()
            return _Outcome(code=_CANCELLED, status=409)
        if message["type"] == "http.request" and (
                message.get("body", b"") or message.get("more_body", False)):
            stopped.set()
            return _Outcome(code=_INVALID, status=400)


async def _observe_owned(request):
    stopped = threading.Event()

    def check():
        if stopped.is_set():
            raise runtime.AuthorityReadError(_CANCELLED, 409)
        anyio.from_thread.check_cancelled()

    def observe():
        # Convert only ordinary worker failures. Receive failures and control
        # exceptions remain the original objects in the async owner below.
        try:
            check()
            result = runtime.observe(check_cancelled=check)
            check()
            return _Outcome(data=result)
        except runtime.AuthorityReadError as error:
            return _failure(error)
        except Exception:
            return _Outcome(code=_FAILURE, status=503)

    listener = worker = None
    try:
        listener = _start(_listen(request.receive, stopped),
                          name="reno-authority-observation-disconnect")
        worker = _start(_owned_io(observe, stopped),
                        name="reno-authority-observation-worker")
        done, _ = await asyncio.wait({listener, worker}, return_when=asyncio.FIRST_COMPLETED)
        # Disconnect/body refusal/receive failure wins if both tasks finished
        # in the same event-loop turn. The finally block drains the worker.
        if listener in done:
            stopped.set()
            return await listener
        result = await worker
        if not listener.done():
            listener.cancel()
        await _drain(listener, preserve_cancellation=True)
        if not listener.cancelled():
            listener_result = listener.result()
            if listener_result is not None:
                return listener_result
        await anyio.lowlevel.checkpoint()
        if stopped.is_set():
            return _Outcome(code=_CANCELLED, status=409)
        return result
    finally:
        primary = sys.exc_info()[1]
        cleanup_primary = None
        stopped.set()
        for task in (listener, worker):
            if task is None:
                continue
            try:
                if not task.done():
                    task.cancel()
            except BaseException as error:
                if primary is not None:
                    if error is not primary:
                        _note(primary, "OBSERVATION_TASK_CLEANUP_FAILED")
                elif cleanup_primary is None:
                    cleanup_primary = error
            try:
                await _drain(task, preserve_cancellation=primary is None)
            except BaseException as error:
                if primary is not None:
                    if error is not primary:
                        _note(primary, "OBSERVATION_TASK_CLEANUP_FAILED")
                elif cleanup_primary is None:
                    cleanup_primary = error
        if primary is None and cleanup_primary is not None:
            raise cleanup_primary


def _request_has_body(request):
    lengths = request.headers.getlist("content-length")
    return (len(lengths) > 1 or bool(lengths and lengths[0] != "0")
            or bool(request.headers.getlist("transfer-encoding")))


def _response(result):
    if result.code is not None:
        return JSONResponse({"detail": "个人状态暂不可用，请稍后重试", "code": result.code},
                            status_code=result.status, headers=_PRIVATE)
    try:
        return JSONResponse(result.data, headers=_PRIVATE)
    except Exception:
        return JSONResponse({"detail": "个人状态暂不可用，请稍后重试", "code": _FAILURE},
                            status_code=503, headers=_PRIVATE)


@router.get("/status")
async def authority_status(request: Request):
    try:
        if any(key != "token" for key, _ in request.query_params.multi_items()) or _request_has_body(request):
            result = _Outcome(code=_INVALID, status=400)
        else:
            result = await _observe_owned(request)
    except Exception:
        # The owned coordinator has drained first. Ordinary receive/setup
        # failures still need a private HTTP error; control exceptions retain
        # their original objects and never become a successful observation.
        result = _Outcome(code=_FAILURE, status=503)
    return _response(result)
