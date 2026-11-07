"""Streaming, immutable personal attachments. No public filesystem mounts."""
from __future__ import annotations

from datetime import datetime, timezone
from contextvars import Context
import asyncio
import threading
import sys
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import os
from pathlib import Path, PurePosixPath
import time
import uuid

from . import config, db, evidence_identity as identity, personal
from . import storage_admin, storage_lifecycle as lifecycle
from .review import utc_now

TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def root_path():
    path = config.DATA / "personal-attachments"
    if not path.resolve().is_relative_to(config.DATA.resolve()):
        raise personal.PersonalError("个人附件目录不得链接到数据目录之外")
    return path


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=256)
def _cached_sha(path, size, mtime_ns, ctime_ns):
    return file_sha256(Path(path))


def clear_integrity_cache():
    """Call after explicit offline restore; the manifest audit never uses cache."""
    _cached_sha.cache_clear()


def file_is_intact(storage_key, byte_size, sha256):
    try:
        path = resolve_storage(storage_key)
        stat = path.stat()
        return (path.is_file() and stat.st_size == byte_size
                and _cached_sha(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) == sha256)
    except (OSError, personal.PersonalError):
        return False


@contextmanager
def _attachment_lifetime():
    """Acquire this exact profile before SQL/files; never initialize facilities."""
    try:
        profile = storage_admin.profile()
        with lifecycle.shared(profile):
            yield profile
    except lifecycle.StorageLifecycleError as error:
        raise personal.PersonalError("个人附件存储保护暂不可用，请稍后重试", error.code, error.status) from None


@contextmanager
def upload_lock(operation_id):
    """Keep lock-file creation and the original upload owner inside SH."""
    personal.require_local_write()
    with _attachment_lifetime(), _upload_lock_locked(operation_id) as acquired:
        yield acquired


@contextmanager
def _upload_lock_locked(operation_id):
    """OS-backed per-operation lock: process death releases it automatically.

    Upload retries and staging cleanup use the same lock, so elapsed age alone
    never authorizes deleting a living upload's temporary or destination file.
    """
    personal.require_local_write()
    profile = storage_admin.profile()
    lifecycle.require_guard(profile)
    personal.require_legacy_write_locked(profile)
    operation_id = personal.valid_uuid(operation_id, "operation_id")
    path = resolve_storage(f".locks/{operation_id}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    acquired = False
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError):
            pass
        yield acquired
    finally:
        primary, failure = sys.exc_info()[1], None
        try:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except BaseException as error:
            failure = error
        try:
            stream.close()
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None and primary is None:
            raise failure


def resolve_storage(storage_key):
    root = root_path().resolve()
    key = PurePosixPath(storage_key)
    if key.is_absolute() or ".." in key.parts or "\\" in storage_key or ":" in storage_key:
        raise personal.PersonalError("附件存储路径无效")
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise personal.PersonalError("附件存储路径越界")
    return path


def attachment_view(con, attachment_id):
    if config.is_cloud():
        personal.require_read(con)
        from .personal_mirror_read import attachment_view as mirror_attachment_view
        return mirror_attachment_view(con, attachment_id)
    raw = personal.require_entity(con, "attachment", attachment_id)
    out = {key: raw[key] for key in ("attachment_id", "source_id", "project_id", "sha256", "byte_size",
                                     "media_type", "original_name", "state", "created_at")}
    out["missing"] = raw["state"] == "ready" and not file_is_intact(raw["storage_key"], raw["byte_size"], raw["sha256"])
    out["readable"] = raw["state"] == "ready" and not out["missing"]
    out["read_state"] = "ready" if out["readable"] else "missing" if out["missing"] else "not_ready"
    out["reason"] = "附件文件缺失或已变化，文字记录仍保留" if out["missing"] else "附件尚未就绪" if not out["readable"] else ""
    out["target_fingerprint"] = identity.fingerprint({key: out[key] for key in
        ("attachment_id", "source_id", "project_id", "sha256", "byte_size", "media_type", "original_name")})
    return out


def _validate_file(path, filename, media_type):
    if Path(filename).suffix.lower() not in TYPES or TYPES[Path(filename).suffix.lower()] != media_type:
        raise personal.PersonalError("仅支持 JPEG、PNG 和 PDF，文件类型需与扩展名一致", "UNSUPPORTED_MEDIA_TYPE", 415)
    with path.open("rb") as stream:
        signature = stream.read(16)
        if media_type == "application/pdf":
            if not signature.startswith(b"%PDF-"):
                raise personal.PersonalError("PDF 文件签名无效", "UNSUPPORTED_MEDIA_TYPE", 415)
            stream.seek(max(0, path.stat().st_size - 1024))
            if b"%%EOF" not in stream.read():
                raise personal.PersonalError("PDF 文件不完整", "UNSUPPORTED_MEDIA_TYPE", 415)
        elif media_type == "image/png" and not signature.startswith(b"\x89PNG\r\n\x1a\n"):
            raise personal.PersonalError("PNG 文件签名无效", "UNSUPPORTED_MEDIA_TYPE", 415)
        elif media_type == "image/jpeg" and not signature.startswith(b"\xff\xd8\xff"):
            raise personal.PersonalError("JPEG 文件签名无效", "UNSUPPORTED_MEDIA_TYPE", 415)
    if media_type.startswith("image/"):
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(path) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError("image too large")
                image.verify()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise personal.PersonalError("图片数据无效或像素尺寸过大", "UNSUPPORTED_MEDIA_TYPE", 415) from None


def _stage(source_id, operation_id, epoch, filename, media_type, storage_key):
    personal.require_local_write()
    with _attachment_lifetime() as profile:
        return _stage_locked(profile, source_id, operation_id, epoch, filename, media_type, storage_key)


def _stage_locked(profile, source_id, operation_id, epoch, filename, media_type, storage_key):
    personal.require_local_write()
    lifecycle.require_guard(profile)
    with storage_admin.connection(profile) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            personal.require_write(con, epoch, upload=True)
            source = personal.require_entity(con, "source", source_id)
            prior = con.execute("SELECT * FROM source_attachment WHERE operation_id=?", (operation_id,)).fetchone()
            if prior:
                if prior["source_id"] != source_id or prior["original_name"] != filename or prior["media_type"] != media_type:
                    raise personal.PersonalError("上传操作编号已用于不同附件", "IDEMPOTENCY_KEY_REUSED", 409)
                if prior["state"] == "failed":
                    count = con.execute("SELECT COUNT(*) FROM source_attachment WHERE source_id=? AND state IN ('staging','ready')", (source_id,)).fetchone()[0]
                    if count >= int(config.get("attachment_per_source_limit")):
                        raise personal.PersonalError("该来源已达到附件数量上限")
                if prior["state"] != "ready":
                    con.execute("UPDATE source_attachment SET state='staging',created_at=?,error=NULL WHERE attachment_id=?", (utc_now(), prior["attachment_id"]))
                    con.commit()
                else:
                    con.rollback()
                return prior["attachment_id"], source["project_id"]
            count = con.execute("SELECT COUNT(*) FROM source_attachment WHERE source_id=? AND state IN ('staging','ready')", (source_id,)).fetchone()[0]
            if count >= int(config.get("attachment_per_source_limit")):
                raise personal.PersonalError("该来源已达到附件数量上限", "INVALID_REQUEST", 400)
            aid = str(uuid.uuid5(uuid.UUID(operation_id), "personal-attachment"))
            con.execute("INSERT INTO source_attachment(attachment_id,source_id,project_id,media_type,original_name,storage_key,state,created_at,operation_id) "
                        "VALUES(?,?,?,?,?,?,'staging',?,?)", (aid, source_id, source["project_id"], media_type, filename, storage_key, utc_now(), operation_id))
            con.commit()
            return aid, source["project_id"]
        except BaseException:
            try:
                con.rollback()
            except BaseException:
                pass
            raise


async def receive_upload(request, source_id, operation_id, primary_epoch):
    """Stream through one actual SH-owning worker, with no request buffering.

    The event loop owns only Request.stream. All authorization, parser/file
    effects, staging/ready/failed SQL, original upload lock and cleanup run on
    one synchronous worker. Neither bridge carries an ambient StorageGuard.
    Cancellation waits for that original worker and its cleanup to terminate.
    """
    personal.require_local_write()
    import anyio
    from starlette.concurrency import run_in_threadpool

    iterator = request.stream().__aiter__()
    stopped = threading.Event()
    chunk_task = None

    async def next_async_chunk():
        nonlocal chunk_task
        if stopped.is_set():
            raise asyncio.CancelledError()
        chunk_task = asyncio.current_task()
        try:
            try:
                return await anext(iterator)
            except StopAsyncIteration:
                return None
        finally:
            chunk_task = None

    def next_chunk():
        # AnyIO's worker token is thread-local. A fresh context drops this
        # worker's SH capability before the stream coroutine enters the loop.
        return Context().run(anyio.from_thread.run, next_async_chunk)

    def execute():
        def owned():
            def check():
                if stopped.is_set():
                    raise personal.PersonalError("附件上传已中断，请保留原操作编号重试")
                anyio.from_thread.check_cancelled()
                lifecycle.require_guard(profile)
                if storage_admin.profile().identity != profile.identity:
                    raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
            with _attachment_lifetime() as profile:
                return _receive_upload_locked(profile, request.headers.get("content-type", ""),
                    source_id, operation_id, primary_epoch, next_chunk, check)
        # Threadpool normally copies ContextVars; acquire a new physical SH
        # owner here instead of borrowing a guard from the HTTP event loop.
        return Context().run(owned)

    task = asyncio.create_task(run_in_threadpool(execute))
    try:
        return await asyncio.shield(task)
    except BaseException:
        stopped.set()
        if chunk_task is not None:
            chunk_task.cancel()
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
        raise


def _release_upload_resources(sink, temp, lock_manager, primary):
    """Attempt every owned cleanup; retain the actual primary failure."""
    failure = None
    cleanups = []
    if sink is not None:
        cleanups.append(sink.close)
    if temp is not None:
        cleanups.append(lambda: temp.unlink(missing_ok=True))
    if lock_manager is not None:
        cleanups.append(lambda: lock_manager.__exit__(None, None, None))
    for cleanup in cleanups:
        try:
            cleanup()
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None and primary is None:
        raise failure


def _receive_upload_locked(profile, content_type_header, source_id, operation_id, primary_epoch, next_chunk, check):
    """Parse one file part incrementally; neither Content-Length nor MIME is trusted."""
    personal.require_local_write()
    from python_multipart.multipart import MultipartParser, parse_options_header
    lifecycle.require_guard(profile)
    check()
    operation_id = personal.valid_uuid(operation_id, "X-Operation-Id")
    try:
        epoch = int(primary_epoch)
    except (ValueError, TypeError):
        raise personal.PersonalError("X-Primary-Epoch 必须是整数") from None
    with storage_admin.connection(profile) as con:
        personal.require_write(con, epoch, upload=True)
        personal.require_entity(con, "source", source_id)
    content_type, options = parse_options_header(content_type_header)
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise personal.PersonalError("附件请求必须是 multipart/form-data")
    root = root_path()
    (root / ".staging").mkdir(parents=True, exist_ok=True)
    lock_manager = upload_lock(operation_id)
    lock_entered = False
    temp, sink = None, None
    try:
        acquired = lock_manager.__enter__()
        lock_entered = True
        if not acquired:
            raise personal.PersonalError("同一次上传仍在进行，请保留操作编号稍后重试", "OPERATION_IN_PROGRESS", 409)
        temp = resolve_storage(f".staging/{operation_id}-{uuid.uuid4()}.part")
        state = {"headers": {}, "field": bytearray(), "value": bytearray(), "parts": 0,
                 "size": 0, "filename": None, "media_type": None, "ended": False,
                 "part_ended": False, "attachment_id": None, "limit": 0}
        digest = hashlib.sha256()
        try:
            sink = temp.open("xb")
        except OSError:
            raise personal.PersonalError("附件临时文件无法创建，请稍后重试") from None

        def part_begin():
            state["parts"] += 1
            if state["parts"] != 1:
                raise personal.PersonalError("一次只接受一个 file 附件")

        def header_field(data, start, end):
            state["field"].extend(data[start:end])
            if len(state["field"]) > 200:
                raise personal.PersonalError("附件请求头过长")

        def header_value(data, start, end):
            state["value"].extend(data[start:end])
            if len(state["value"]) > 2000:
                raise personal.PersonalError("附件请求头过长")

        def header_end():
            key = bytes(state["field"]).lower()
            if key in state["headers"] or len(state["headers"]) > 5:
                raise personal.PersonalError("重复或过多附件请求头")
            state["headers"][key] = bytes(state["value"])
            state["field"].clear()
            state["value"].clear()

        def headers_finished():
            _, disposition = parse_options_header(state["headers"].get(b"content-disposition", b""))
            if disposition.get(b"name") != b"file" or not disposition.get(b"filename"):
                raise personal.PersonalError("附件字段必须是 file")
            try:
                filename = disposition[b"filename"].decode("utf-8")
                media_type = state["headers"].get(b"content-type", b"").decode("ascii").lower()
            except UnicodeError:
                raise personal.PersonalError("文件名或媒体类型编码无效") from None
            if (any(c in filename for c in ("/", "\\", ":")) or any(ord(c) < 32 or ord(c) == 127 for c in filename)
                    or filename in {".", ".."} or len(filename) > 200):
                raise personal.PersonalError("附件文件名不得包含路径")
            extension = Path(filename).suffix.lower()
            if extension not in TYPES or TYPES[extension] != media_type:
                raise personal.PersonalError("仅支持 JPEG、PNG 和 PDF；HEIC 请先转为 JPEG/PNG", "UNSUPPORTED_MEDIA_TYPE", 415)
            storage_key = f"files/{uuid.uuid5(uuid.UUID(operation_id), 'personal-attachment')}{extension}"
            aid, project_id = _stage(source_id, operation_id, epoch, filename, media_type, storage_key)
            state.update(filename=filename, media_type=media_type, attachment_id=aid, project_id=project_id,
                         storage_key=storage_key, limit=int(config.get("attachment_pdf_max_bytes" if media_type == "application/pdf" else "attachment_image_max_bytes")))

        def part_data(data, start, end):
            if not state["filename"]:
                raise personal.PersonalError("附件头尚未完成")
            state["size"] += end - start
            if state["size"] > state["limit"]:
                raise personal.PersonalError(f"附件超过上限 {state['limit']} 字节", "ATTACHMENT_TOO_LARGE", 413)
            chunk = data[start:end]
            digest.update(chunk)
            sink.write(chunk)

        callbacks = {"on_part_begin": part_begin, "on_header_field": header_field,
                     "on_header_value": header_value, "on_header_end": header_end,
                     "on_headers_finished": headers_finished, "on_part_data": part_data,
                     "on_part_end": lambda: state.update(part_ended=True), "on_end": lambda: state.update(ended=True)}
        try:
            parser = MultipartParser(boundary, callbacks)
            raw_size = 0
            max_raw = max(int(config.get("attachment_pdf_max_bytes")), int(config.get("attachment_image_max_bytes"))) + 16384
            while True:
                check()
                chunk = next_chunk()
                check()
                if chunk is None:
                    break
                raw_size += len(chunk)
                if raw_size > max_raw:
                    raise personal.PersonalError("上传请求超过附件大小上限", "ATTACHMENT_TOO_LARGE", 413)
                # Parser callbacks, SQLite and disk stay on this same SH owner.
                parser.write(chunk)
            parser.finalize()
            sink.flush()
            os.fsync(sink.fileno())
            sink.close()
            if not state["ended"] or not state["part_ended"] or state["size"] == 0:
                raise personal.PersonalError("附件上传中断或文件为空")
            check()
            _validate_file(temp, state["filename"], state["media_type"])
            check()
            sha256 = digest.hexdigest()
            envelope = {"operation_id": operation_id, "primary_epoch": epoch, "changes": {
                "source_id": source_id, "original_name": state["filename"], "media_type": state["media_type"],
                "byte_size": state["size"], "sha256": sha256}}

            def finish(con, changes, base, op):
                check()
                storage_admin.from_connection(con, profile=profile)
                attachment = personal.require_entity(con, "attachment", state["attachment_id"], state["project_id"])
                if attachment["state"] == "ready":
                    raise personal.PersonalError("已完成附件不可替换", "IDEMPOTENCY_KEY_REUSED", 409)
                path = resolve_storage(attachment["storage_key"])
                path.parent.mkdir(parents=True, exist_ok=True)
                temp.replace(path)
                con.execute("UPDATE source_attachment SET state='ready',sha256=?,byte_size=?,error=NULL WHERE attachment_id=? AND state!='ready'",
                            (sha256, state["size"], state["attachment_id"]))
                return {"attachment": attachment_view(con, state["attachment_id"])}, 201
            check()
            return personal.mutate(f"source:{source_id}:attachment", envelope, finish, upload=True)
        except BaseException as exc:
            if state["attachment_id"]:
                try:
                    with storage_admin.connection(profile) as con:
                        con.execute("BEGIN IMMEDIATE")
                        personal.require_write(con, epoch, upload=True)
                        con.execute("UPDATE source_attachment SET state='failed',error='上传未完成，可重试' WHERE attachment_id=? AND state!='ready'",
                                    (state["attachment_id"],))
                        con.commit()
                except BaseException:
                    # The old stage is still retained if this diagnostic write
                    # fails. Never mask cancellation or the original failure.
                    pass
            if isinstance(exc, lifecycle.StorageLifecycleError):
                raise personal.PersonalError("个人附件存储保护暂不可用，请稍后重试", exc.code, exc.status) from None
            if isinstance(exc, personal.PersonalError) or not isinstance(exc, Exception):
                raise
            raise personal.PersonalError("附件上传未完成，请重新选择文件重试") from None
    finally:
        _release_upload_resources(sink, temp, lock_manager if lock_entered else None, sys.exc_info()[1])


def ready_file(con, attachment_id):
    # Returning a pathname cannot carry a cloud factory's exact-fd proof.
    # HTTP uses open_owned_attachment for both local and installed mirrors.
    if config.is_cloud():
        raise personal.PersonalError("个人镜像附件必须通过受保护的下载入口读取", "PERSONAL_MIRROR_READ_FACTORY_REQUIRED", 403)
    personal.require_read(con)
    raw = personal.require_entity(con, "attachment", attachment_id)
    view = attachment_view(con, attachment_id)
    if raw["state"] != "ready" or view["missing"]:
        raise personal.PersonalError("附件尚未就绪或文件缺失，文字记录仍保留", "NOT_FOUND", 404)
    path = resolve_storage(raw["storage_key"])
    return path, view


class _LocalOwnedStream:
    """Only the already-opened local fd crosses the response boundary."""
    def __init__(self, holder):
        self.holder = holder

    @property
    def closed(self):
        return self.holder.closed

    def _verify(self):
        from .personal_preservation_files import identity as file_identity, directory_identity, PreservationFileError
        from .sync_protocol import SyncError
        held = self.holder._held
        if self.holder.closed:
            raise personal.PersonalError("附件下载已关闭", "PERSONAL_ATTACHMENT_CHANGED", 409)
        try:
            if (directory_identity(held.directory) != held.directory_identity
                    or file_identity(held.directory.entry_stat(held.path.name)) != held.path_identity
                    or file_identity(os.fstat(held.stream.fileno())) != held.handle_identity):
                raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
            held.directory.verify()
        except (OSError, SyncError, PreservationFileError):
            raise personal.PersonalError("附件已变化，请重新读取记录后下载", "PERSONAL_ATTACHMENT_CHANGED", 409) from None
        return held.stream

    def read(self, size=-1):
        stream = self._verify()
        value = stream.read(size)
        self._verify()
        return value

    def seek(self, offset, whence=0):
        return self._verify().seek(offset, whence)

    def tell(self):
        return self._verify().tell()

    def fileno(self):
        return self._verify().fileno()

    def close(self):
        self.holder.close()


class _LocalOwnedAttachment:
    def __init__(self, held, view):
        self._held, self.closed = held, False
        self.view, self.personal_read_version = view, None
        self.stream = _LocalOwnedStream(self)

    def close(self):
        if not self.closed:
            self.closed = True
            # Full SHA was checked before the original SQL/SH window closed.
            # Each response read checks the original handle and entry stat;
            # close only releases these handles, never reopens a pathname.
            self._held.__exit__(RuntimeError, None, None)


def open_owned_attachment(con, attachment_id):
    """Authorize under the read window; give the response the original fd."""
    personal.require_read(con)
    if config.is_cloud():
        from .personal_mirror_read import open_owned_attachment as mirror_open
        return mirror_open(con, attachment_id)
    from .personal_preservation_files import FileHold, PreservationFileError
    from .sync_protocol import SyncError
    lifecycle.require_guard(storage_admin.from_connection(con))
    raw = personal.require_entity(con, "attachment", attachment_id)
    view = attachment_view(con, attachment_id)
    if not view["readable"]:
        raise personal.PersonalError("附件尚未就绪或文件缺失，文字记录仍保留", "NOT_FOUND", 404)
    key = PurePosixPath(raw["storage_key"])
    if (key.is_absolute() or ".." in key.parts or "\\" in raw["storage_key"] or ":" in raw["storage_key"]
            or key.as_posix() != raw["storage_key"] or not key.parts or key.parts[0] != "files"):
        raise personal.PersonalError("附件存储记录无效", "PERSONAL_ATTACHMENT_CHANGED", 409)
    held = FileHold(config.DATA.absolute() / "personal-attachments" / key,
                    maximum=128 * 1024 * 1024, collect=False)
    entered = False
    try:
        held.__enter__()
        entered = True
        if held.handle_identity["byte_size"] != raw["byte_size"] or held.sha256 != raw["sha256"]:
            raise PreservationFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        held.stream.seek(0)
        return _LocalOwnedAttachment(held, view)
    except BaseException as error:
        if entered:
            try:
                held.__exit__(type(error), error, error.__traceback__)
            except BaseException:
                error.add_note("PERSONAL_ATTACHMENT_CLEANUP_FAILED")
        if isinstance(error, (OSError, SyncError, PreservationFileError)):
            raise personal.PersonalError("附件文件缺失或已变化，文字记录仍保留", "NOT_FOUND", 404) from None
        raise


def cleanup_staging(*, now=None):
    """Housekeeping retains the complete profile lifetime before all effects."""
    personal.require_local_write()
    with _attachment_lifetime() as profile:
        return _cleanup_staging_locked(profile, now=now)


def _cleanup_staging_locked(profile, *, now=None):
    """Explicit housekeeping: only old incomplete artifacts, never ready files."""
    personal.require_local_write()
    lifecycle.require_guard(profile)
    now = time.time() if now is None else now
    cutoff = now - int(config.get("attachment_staging_ttl_seconds"))
    removed = 0
    with storage_admin.connection(profile) as con:
        personal.require_write(con, personal.capabilities(con)["primary_epoch"])
    folder = root_path() / ".staging"
    if folder.exists():
        for path in folder.glob("*.part"):
            if path.resolve().is_relative_to(root_path().resolve()) and path.stat().st_mtime < cutoff:
                try:
                    operation_id = str(uuid.UUID(path.name[:36]))
                except ValueError:
                    # Legacy, non-operation-named orphan files cannot belong
                    # to this uploader, but still must satisfy the age rule.
                    path.unlink()
                    removed += 1
                    continue
                with upload_lock(operation_id) as acquired:
                    if acquired and path.exists() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
    with storage_admin.connection(profile) as con:
        for row in con.execute("SELECT * FROM source_attachment WHERE state!='ready'").fetchall():
            with upload_lock(row["operation_id"]) as acquired:
                if not acquired:
                    continue
                con.execute("BEGIN IMMEDIATE")
                personal.require_write(con, personal.capabilities(con)["primary_epoch"])
                current = con.execute("SELECT * FROM source_attachment WHERE attachment_id=?", (row["attachment_id"],)).fetchone()
                created = datetime.fromisoformat(current["created_at"].replace("Z", "+00:00")).timestamp()
                if current["state"] != "ready" and created < cutoff:
                    path = resolve_storage(current["storage_key"])
                    if path.is_file():
                        path.unlink()
                        removed += 1
                    con.execute("UPDATE source_attachment SET state='failed',error='临时上传已过期，请重新选择文件' WHERE attachment_id=? AND state!='ready'", (current["attachment_id"],))
                con.commit()
    return removed


def attachment_manifest(con):
    """Read-only backup/restore audit; missing/corrupt files remain explicit."""
    clear_integrity_cache()
    result = []
    for row in con.execute("SELECT * FROM source_attachment WHERE state='ready' ORDER BY attachment_id"):
        path = resolve_storage(row["storage_key"])
        digest = None
        if path.is_file():
            digest = file_sha256(path)
        result.append({"attachment_id": row["attachment_id"], "storage_key": row["storage_key"],
                       "sha256": row["sha256"], "byte_size": row["byte_size"],
                       "complete": digest == row["sha256"]})
    return result
