"""Attachment responses retain the authorized original fd through cancellation."""
import asyncio
from contextvars import Context
import re
import sys
import threading
from urllib.parse import quote

from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from reno import personal, personal_attachments as attachments, personal_mirror_read
from .personal_read_api import read_result, private_headers, error_response


async def _drain(task):
    import anyio
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
    try:
        task.result()
    except BaseException:
        pass


async def _owned_io(callback, *args, on_cancel=None):
    """Cancellation drains the actual worker before its fd can be closed."""
    task = asyncio.create_task(run_in_threadpool(lambda: Context().run(callback, *args)))
    try:
        return await asyncio.shield(task)
    except BaseException:
        if on_cancel is not None:
            on_cancel()
        await _drain(task)
        raise


def _range(value, size):
    if not value:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size == 0:
        raise personal.PersonalError("附件下载范围无效", "RANGE_NOT_SATISFIABLE", 416)
    first, last = match.groups()
    # Limit integer parsing as well as response bytes.
    if len(first) > 20 or len(last) > 20:
        raise personal.PersonalError("附件下载范围无效", "RANGE_NOT_SATISFIABLE", 416)
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
        if start > end or start >= size:
            raise personal.PersonalError("附件下载范围无效", "RANGE_NOT_SATISFIABLE", 416)
    else:
        suffix = int(last)
        if suffix == 0:
            raise personal.PersonalError("附件下载范围无效", "RANGE_NOT_SATISFIABLE", 416)
        start, end = max(0, size - suffix), size - 1
    return start, end, 206


class HeldAttachmentResponse(Response):
    def __init__(self, holder, request):
        self.holder = holder
        view = holder.view
        size = view["byte_size"]
        etag = '"' + view["sha256"] + '"'
        requested_range = request.headers.get("range")
        if request.headers.get("if-range") not in {None, etag}:
            requested_range = None
        self.start, self.end, status = _range(requested_range, size)
        headers = {**private_headers(holder.personal_read_version),
            "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes", "ETag": etag,
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(view["original_name"], safe=""),
            "Content-Length": str(max(0, self.end - self.start + 1))}
        if status == 206:
            headers["Content-Range"] = f"bytes {self.start}-{self.end}/{size}"
        super().__init__(content=b"", status_code=status, media_type=view["media_type"], headers=headers)

    async def _send(self, scope, receive, send):
        # Check/seek the original authorized fd before any HTTP success is
        # sent. No SQL, path open or ambient StorageGuard reaches this I/O.
        try:
            await _owned_io(self.holder.stream.seek, self.start)
        except (personal.PersonalError, personal_mirror_read.PersonalMirrorReadError) as error:
            if not isinstance(error, personal.PersonalError):
                error = personal.PersonalError(str(error), error.code, error.status)
            await error_response(error)(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        if scope["method"] == "HEAD":
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        remaining = max(0, self.end - self.start + 1)
        while remaining:
            chunk = await _owned_io(self.holder.stream.read, min(64 * 1024, remaining))
            if not chunk:
                raise personal.PersonalError("附件下载中断，请重新下载", "PERSONAL_ATTACHMENT_CHANGED", 409)
            remaining -= len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    async def _disconnect(receive):
        while True:
            if (await receive())["type"] == "http.disconnect":
                return

    async def __call__(self, scope, receive, send):
        sender = asyncio.create_task(self._send(scope, receive, send))
        listener = asyncio.create_task(self._disconnect(receive))
        try:
            done, _ = await asyncio.wait({sender, listener}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                await sender
            else:
                await listener
                sender.cancel()
                await _drain(sender)
        finally:
            primary = sys.exc_info()[1]
            for task in (sender, listener):
                if not task.done():
                    task.cancel()
                await _drain(task)
            # Each worker has already finished, including an interrupted one.
            try:
                self.holder.close()
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note("PERSONAL_ATTACHMENT_CLEANUP_FAILED")


async def attachment_response(request, attachment_id):
    stopped, holders = threading.Event(), []
    delivered = False

    def check():
        import anyio
        if stopped.is_set():
            raise personal.PersonalError("附件读取已中断", "PERSONAL_ATTACHMENT_CANCELLED", 409)
        anyio.from_thread.check_cancelled()

    def opened(con):
        holder = attachments.open_owned_attachment(con, attachment_id)
        holders.append(holder)
        return holder

    async def disconnected():
        # Acquiring the original factory and hashing its exact fd may precede
        # Response.__call__ by many chunks. A disconnect must stop that owned
        # worker here as well, rather than waiting for a response to exist.
        await HeldAttachmentResponse._disconnect(request.receive)
        stopped.set()

    listener = asyncio.create_task(disconnected(), name="reno-personal-acquisition-disconnect")
    worker = asyncio.create_task(_owned_io(
        lambda: read_result(opened, request=request, check_cancelled=check), on_cancel=stopped.set),
        name="reno-personal-acquisition-worker")
    try:
        done, _ = await asyncio.wait({listener, worker}, return_when=asyncio.FIRST_COMPLETED)
        if listener in done:
            # Propagate receive failures; a real disconnect has already set
            # stopped before any worker cancellation can be observed.
            await listener
            raise personal.PersonalError("附件读取已中断", "PERSONAL_ATTACHMENT_CANCELLED", 409)
        result = await worker
        # There can be only one receive owner. Stop and drain this listener
        # before handing the original fd to the response's own listener.
        if not listener.done():
            listener.cancel()
        await _drain(listener)
        if not listener.cancelled():
            listener.result()
        if stopped.is_set():
            raise personal.PersonalError("附件读取已中断", "PERSONAL_ATTACHMENT_CANCELLED", 409)
        response = HeldAttachmentResponse(result.data, request)
        delivered = True
        return response
    finally:
        primary = sys.exc_info()[1]
        if not delivered:
            stopped.set()
        for task in (listener, worker):
            if not task.done():
                task.cancel()
            await _drain(task)
        if not delivered:
            # _owned_io waits for the actual worker, so even cancellation in
            # the initial full SHA cannot race closing its original fd.
            for holder in holders:
                try:
                    holder.close()
                except BaseException as error:
                    if primary is None:
                        raise
                    primary.add_note("PERSONAL_ATTACHMENT_CLEANUP_FAILED")
