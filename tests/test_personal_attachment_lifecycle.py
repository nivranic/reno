"""Actual streamed personal uploads retain one synchronous lifecycle owner.

Independent ordinary17 fixtures produce the source through the real personal
mutation API. Request.stream is consumed incrementally on the event loop;
all parser, file and SQLite effects execute under the worker's actual SH.
No copied guard, successful receipt, ready body or attachment row is seeded.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
import uuid

import anyio
import pytest
from starlette.requests import Request

from reno import config, db, personal, personal_attachments as files
from reno import storage_admin, storage_lifecycle as lifecycle
from tests.schema_fixtures import assert_current17_runtime, initialize_current17


BOUNDARY = "synthetic-attachment-lifecycle"
BODY = b"%PDF-1.4\noriginal synthetic streamed attachment\n%%EOF\n"
HEADER = ("--" + BOUNDARY + '\r\nContent-Disposition: form-data; name="file"; filename="original.pdf"\r\n'
    "Content-Type: application/pdf\r\n\r\n").encode()
TAIL = ("\r\n--" + BOUNDARY + "--\r\n").encode()


@pytest.fixture
def attachment_env(tmp_path, monkeypatch):
    assert_current17_runtime()
    root = tmp_path / "actual-personal"
    local = root / "config.synthetic.json"
    root.mkdir()
    local.write_text(json.dumps({"node_role": "home", "personal_features_enabled": True,
        "field_checks_enabled": True, "auth_token": ""}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "ORIG": root / "media/originals",
            "FRAMES": root / "media/frames", "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    profile = storage_admin.profile()
    initialize_current17(profile.database)
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as connection:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        project = personal.default_project(connection)["project"]
    with lifecycle.shared(profile):
        created, status = personal.create_source(project["project_id"], {"operation_id": str(uuid.uuid4()),
            "primary_epoch": int(meta["primary_epoch"]),
            "changes": {"source_type": "manual_note", "title": "实际上传来源"}})
    assert status in (200, 201)
    return SimpleNamespace(profile=profile, source=created["source"], epoch=int(meta["primary_epoch"]),
        root=root, identity=(profile.database.parent / "node-identity.json").read_bytes())


def request(receive):
    return Request({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "method": "POST", "path": "/synthetic-upload", "raw_path": b"/synthetic-upload",
        "query_string": b"", "root_path": "", "headers": [
            (b"content-type", ("multipart/form-data; boundary=" + BOUNDARY).encode())],
        "client": ("127.0.0.1", 12345), "server": ("synthetic.invalid", 80)}, receive)


def rows(value):
    with storage_admin.connection(value.profile) as connection:
        return {table: [dict(row) for row in connection.execute('SELECT * FROM "' + table + '" ORDER BY 1')]
            for table in ("source_attachment", "mutation_receipt", "meta")}


def incomplete(value, operation):
    stored = rows(value)
    selected = [row for row in stored["source_attachment"] if row["operation_id"] == operation]
    assert len(selected) == 1 and selected[0]["state"] == "failed"
    assert not any(row["operation_id"] == operation for row in stored["mutation_receipt"])
    assert not list((value.profile.database.parent / "personal-attachments/.staging").glob("*.part"))
    assert (value.profile.database.parent / "node-identity.json").read_bytes() == value.identity
    with lifecycle.exclusive(value.profile, timeout=0):
        pass
    with files.upload_lock(operation) as acquired:
        assert acquired


async def upload(value, operation, chunks=None):
    pieces = iter([HEADER, BODY[:11], BODY[11:], TAIL] if chunks is None else chunks)
    async def receive():
        # The upload worker's strict owner guard must not travel over the
        # AnyIO bridge into this HTTP task, even when the caller had a guard.
        assert lifecycle.current_guard() is None
        try:
            chunk = next(pieces)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}
    return await files.receive_upload(request(receive), value.source["source_id"], operation, str(value.epoch))


def test_success_and_exact_retry_keep_one_worker_guard_and_the_original_ready_body(attachment_env, monkeypatch):
    value, operation = attachment_env, str(uuid.uuid4())
    before = rows(value)
    seen, loop_threads = [], []
    original_stage, original_validate = files._stage, files._validate_file
    def observed(function, name):
        def wrapper(*args, **kwargs):
            guard = lifecycle.require_guard(value.profile)
            seen.append((name, threading.get_ident(), guard))
            return function(*args, **kwargs)
        return wrapper
    monkeypatch.setattr(files, "_stage", observed(original_stage, "stage"))
    monkeypatch.setattr(files, "_validate_file", observed(original_validate, "validate"))
    async def run():
        loop_threads.append(threading.get_ident())
        first = await upload(value, operation)
        second = await upload(value, operation)
        return first, second
    first, second = anyio.run(run)
    assert first == second and first[1] == 201
    assert {name for name, _, _ in seen} == {"stage", "validate"}
    assert all(thread != loop_threads[0] for _, thread, _ in seen)
    assert all(seen[index][2] is seen[index + 1][2] for index in (0, 2))
    after = rows(value)
    assert after["meta"] == before["meta"]
    attachments = after["source_attachment"]
    assert len(attachments) == 1 and attachments[0]["state"] == "ready"
    assert attachments[0]["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert attachments[0]["byte_size"] == len(BODY)
    assert len([row for row in after["mutation_receipt"] if row["operation_id"] == operation]) == 1
    assert files.resolve_storage(attachments[0]["storage_key"]).read_bytes() == BODY
    assert not list((value.profile.database.parent / "personal-attachments/.staging").glob("*.part"))
    with lifecycle.exclusive(value.profile, timeout=0):
        pass


def test_async_and_worker_bridges_do_not_borrow_the_callers_synchronous_guard(attachment_env):
    value, operation = attachment_env, str(uuid.uuid4())
    # TestClient portals can originate inside a synchronous Home owner scope.
    # Actual stream callbacks assert no foreign guard; the upload must acquire
    # a separate same-profile SH owner rather than weakening owner validation.
    with lifecycle.shared(value.profile) as caller:
        result, status = anyio.run(upload, value, operation)
        assert lifecycle.current_guard() is caller and caller.verify() is caller
    assert status == 201 and result["attachment"]["state"] == "ready"
    with lifecycle.exclusive(value.profile, timeout=0):
        pass


@pytest.mark.parametrize("method", ["asyncio_task", "anyio_scope"])
def test_cancellation_waits_for_original_upload_cleanup_and_preserves_retriable_stage(attachment_env, method):
    value, operation = attachment_env, str(uuid.uuid4())
    async def run():
        entered, completed = anyio.Event(), anyio.Event()
        waiting = anyio.Event()
        first, active_scope = True, None
        async def receive():
            nonlocal first
            assert lifecycle.current_guard() is None
            if first:
                first = False
                return {"type": "http.request", "body": HEADER + BODY[:12], "more_body": True}
            entered.set()
            await waiting.wait()
            pytest.fail("Cancelled upload continued its original request stream")
        async def runner():
            nonlocal active_scope
            try:
                with anyio.CancelScope() as scope:
                    active_scope = scope
                    await files.receive_upload(request(receive), value.source["source_id"], operation, str(value.epoch))
            finally:
                completed.set()
        with anyio.fail_after(10):
            task = asyncio.create_task(runner())
            await entered.wait()
            if method == "asyncio_task":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                active_scope.cancel()
                await task
            await completed.wait()
    anyio.run(run)
    incomplete(value, operation)
    result, status = anyio.run(upload, value, operation)
    assert status == 201 and result["attachment"]["state"] == "ready"


def test_disconnect_has_no_ready_receipt_or_live_owner_and_same_uuid_can_resume(attachment_env):
    value, operation = attachment_env, str(uuid.uuid4())
    async def run():
        first = True
        async def receive():
            nonlocal first
            assert lifecycle.current_guard() is None
            if first:
                first = False
                return {"type": "http.request", "body": HEADER + BODY[:12], "more_body": True}
            return {"type": "http.disconnect"}
        with pytest.raises(personal.PersonalError):
            await files.receive_upload(request(receive), value.source["source_id"], operation, str(value.epoch))
    anyio.run(run)
    incomplete(value, operation)
    result, status = anyio.run(upload, value, operation)
    assert status == 201 and result["attachment"]["state"] == "ready"


def test_live_stream_blocks_exclusive_and_housekeeping_preserves_its_original_files(attachment_env):
    value, operation = attachment_env, str(uuid.uuid4())
    async def run():
        entered, release = anyio.Event(), anyio.Event()
        first = True
        async def receive():
            nonlocal first
            assert lifecycle.current_guard() is None
            if first:
                first = False
                return {"type": "http.request", "body": HEADER + BODY, "more_body": True}
            entered.set()
            await release.wait()
            return {"type": "http.request", "body": TAIL, "more_body": False}
        task = asyncio.create_task(files.receive_upload(request(receive), value.source["source_id"], operation, str(value.epoch)))
        try:
            with anyio.fail_after(10):
                await entered.wait()
                before = rows(value)
                with pytest.raises(lifecycle.StorageLifecycleError) as blocked:
                    with lifecycle.exclusive(value.profile, timeout=0):
                        pytest.fail("EX entered during an actual protected upload stream")
                assert blocked.value.code == "STORAGE_BUSY"
                staged = list((value.profile.database.parent / "personal-attachments/.staging").glob("*.part"))
                assert len(staged) == 1 and staged[0].name.startswith(operation)
                original = staged[0].read_bytes()
                removed = await anyio.to_thread.run_sync(lambda: files.cleanup_staging(now=time.time() + 2 * 86400))
                assert removed == 0 and staged[0].read_bytes() == original and rows(value) == before
                consumed = False
                async def collision_receive():
                    nonlocal consumed
                    consumed = True
                    pytest.fail("Busy operation consumed a replacement stream")
                with pytest.raises(personal.PersonalError) as busy:
                    await files.receive_upload(request(collision_receive), value.source["source_id"], operation, str(value.epoch))
                assert busy.value.code == "OPERATION_IN_PROGRESS" and consumed is False
                release.set()
                return await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
    result, status = anyio.run(run)
    assert status == 201 and result["attachment"]["state"] == "ready"
    with lifecycle.exclusive(value.profile, timeout=0):
        pass


@pytest.mark.parametrize("operation", ["stage", "cleanup"])
def test_direct_stage_and_cleanup_acquire_shared_before_opening_any_database(attachment_env, monkeypatch, operation):
    value, selected = attachment_env, str(uuid.uuid4())
    entered, connected = threading.Event(), threading.Event()
    original = files.db.connect
    worker_thread = None
    def observed_connect(*args, **kwargs):
        if threading.get_ident() == worker_thread:
            lifecycle.require_guard(value.profile)
            connected.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(files.db, "connect", observed_connect)
    def execute():
        nonlocal worker_thread
        worker_thread = threading.get_ident()
        entered.set()
        if operation == "cleanup":
            return files.cleanup_staging()
        key = "files/" + str(uuid.uuid5(uuid.UUID(selected), "personal-attachment")) + ".pdf"
        return files._stage(value.source["source_id"], selected, value.epoch, "original.pdf", "application/pdf", key)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lifecycle.exclusive(value.profile):
            future = pool.submit(execute)
            assert entered.wait(2)
            assert not connected.wait(0.05) and not future.done()
        result = future.result(timeout=10)
    assert connected.is_set()
    if operation == "cleanup":
        assert result == 0
    else:
        assert result[0] == str(uuid.uuid5(uuid.UUID(selected), "personal-attachment"))


def test_missing_facilities_fail_before_sql_or_stream_without_automatic_initialization(attachment_env, tmp_path, monkeypatch):
    root = tmp_path / "never-initialized"
    monkeypatch.setattr(config, "DATA", root / "data")
    monkeypatch.setattr(config, "MEDIA", root / "media")
    initialize_current17(storage_admin.profile().database)
    profile = storage_admin.profile()
    existing = {str(path) for path in root.rglob("*")}
    def forbidden(*args, **kwargs):
        pytest.fail("Uninitialized attachment operation opened SQLite before lifecycle admission")
    monkeypatch.setattr(files.db, "connect", forbidden)
    for action in (lambda: files.cleanup_staging(), lambda: files._stage(str(uuid.uuid4()), str(uuid.uuid4()), 1,
            "original.pdf", "application/pdf", "files/original.pdf"),
            lambda: anyio.run(upload, attachment_env, str(uuid.uuid4()))):
        with pytest.raises(personal.PersonalError) as refused:
            action()
        assert refused.value.code == "STORAGE_NOT_INITIALIZED" and refused.value.status == 503
    assert {str(path) for path in root.rglob("*")} == existing
    assert not (profile.database.parent / "personal-attachments").exists()


def test_denied_sql_rollback_keeps_original_stage_rejection_and_closes_the_writer(attachment_env, monkeypatch):
    value, operation = attachment_env, str(uuid.uuid4())
    before = rows(value)
    original = files.db.connect
    denied = []
    def actual_connection(*args, **kwargs):
        connection = original(*args, **kwargs)
        def authorizer(action, first, second, database, origin):
            if action == sqlite3.SQLITE_TRANSACTION and first == "ROLLBACK":
                denied.append(first)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorizer)
        return connection
    with monkeypatch.context() as scoped:
        scoped.setattr(files.db, "connect", actual_connection)
        with pytest.raises(personal.PersonalError) as rejected:
            files._stage(value.source["source_id"], operation, value.epoch + 1,
                "original.pdf", "application/pdf", "files/original.pdf")
    assert rejected.value.code == "PRIMARY_CHANGED" and denied == ["ROLLBACK"]
    assert rows(value) == before
    with lifecycle.exclusive(value.profile, timeout=0):
        pass
