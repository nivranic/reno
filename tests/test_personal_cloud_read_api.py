"""Cloud personal reads use actual installs; writers and paths grant nothing."""
import asyncio
from contextlib import contextmanager
import hashlib
import threading

import anyio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from app.personal_attachment_response import HeldAttachmentResponse
from app.personal_read_api import VERSION_HEADER, read_result
from reno import checklists, config, db, personal, personal_attachments as attachments
from reno import personal_mirror_read as reader, storage_admin, storage_lifecycle as lifecycle
from tests.test_personal_mirror_transfer import mirror_env, BODY, table_state, uid  # noqa: F401
from tests.test_personal_mirror_read import install
from tests.test_personal_projects import personal_env, envelope  # noqa: F401


def client():
    from app.api import router as knowledge_router
    from app.personal_api import router as personal_router
    from app.checklist_api import router as checklist_router
    app = FastAPI()
    app.include_router(knowledge_router)
    app.include_router(personal_router)
    app.include_router(checklist_router)
    return TestClient(app)


def objects(env):
    env["select"](env["source_root"], "home")
    con = db.connect()
    try:
        source = personal.source_view(con, env["source_id"])
    finally:
        con.close()
    ref = {"target_kind": "source", "target_id": env["source_id"], "target_fingerprint": source["target_fingerprint"]}
    def payload(changes, base=None):
        result = {"operation_id": uid(), "primary_epoch": env["primary_epoch"], "changes": changes}
        if base is not None:
            result["base_revision"] = base
        return result
    card = personal.create_card(env["project_id"], payload({"title": "个人防水决定", "evidence_refs": [ref]}))[0]["card"]
    personal.update_card(card["card_id"], payload({"decision_reason": "原家庭主实例的实际修订"}, 1))
    checklist = checklists.create(env["project_id"], payload({"title": "现场原始记录", "items": [{
        "title": "基层待核对", "basis_kind": "observation_only", "refs": [ref]}]}))[0]["checklist"]
    item = checklist["items"][0]
    checklists.append_check(item["item_id"], payload({"result": "unable_to_verify", "note": "型号未定，保留观察"}, 1))
    return {"card_id": card["card_id"], "instance_id": checklist["instance_id"], "item_id": item["item_id"]}


@pytest.fixture
def cloud_read_env(mirror_env, monkeypatch):
    env = mirror_env
    monkeypatch.delenv("RENO_PERSONAL_CLOUD_READ_ENABLED", raising=False)
    env.update(objects(env))
    install(env)
    config.set_local({"personal_cloud_read_enabled": True})
    return env


def request(headers=None):
    return Request({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"", "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 12345), "headers": [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]})


def assert_private(response, version=None):
    assert response.headers["cache-control"] == "private, no-store"
    if version is not None:
        assert response.headers[VERSION_HEADER] == version


def test_default_disabled_cloud_grants_no_personal_reads_or_primary_authority(mirror_env):
    env = mirror_env
    before = table_state(env)
    with client() as http:
        cap = http.get("/api/capabilities")
        assert cap.status_code == 200
        data = cap.json()
        assert data["library_id"] == env["receiver"]["library_id"]
        assert not data["personal_read"] and not data["personal_write"] and not data["personal_upload"]
        assert not data["field_checks_read"] and not data["field_checks_write"]
        assert data["personal_mode"] == "disabled" and data["personal_read_version"] is None
        assert_private(cap)
        project = http.get("/api/projects/default")
        assert project.json()["project"] is None and not project.json()["enabled"]
        assert project.json()["personal_read_version"] is None
        assert_private(project)
        denied = http.get(f"/api/sources/{env['source_id']}")
        assert denied.status_code == 403 and denied.json()["code"] == "PERSONAL_MIRROR_READ_DISABLED"
        assert_private(denied)
        assert http.get("/api/search", params={"q": "防水"}).status_code == 200
    assert table_state(env) == before


@pytest.mark.parametrize("namespace", ["installed_empty", "absent"])
def test_enabled_without_installed_head_has_explicit_disabled_default(mirror_env, namespace):
    if namespace == "absent":
        root = mirror_env["target_root"] / "fresh-http-reader"
        data = mirror_env["select"](root, "cloud")
        path = data / "reno.db"
        db.initialize(path)
        lifecycle.initialize(lifecycle.StorageProfile(path, root / "media", data / "generated", data / "sync-checkpoints"))
    config.set_local({"personal_cloud_read_enabled": True})
    with client() as http:
        cap = http.get("/api/capabilities")
        assert cap.status_code == 200 and not cap.json()["personal_read"]
        assert cap.json()["personal_mode"] == "disabled"
        default = http.get("/api/projects/default")
        assert default.status_code == 200 and not default.json()["enabled"]
        denied = http.get(f"/api/sources/{mirror_env['source_id']}")
        assert denied.status_code == 403 and denied.json()["code"] == "PERSONAL_MIRROR_READ_NOT_INSTALLED"


def test_actual_install_reads_all_personal_views_history_and_exports(cloud_read_env):
    env = cloud_read_env
    before = table_state(env)
    identity = (env["data"] / "node-identity.json").read_bytes()
    with client() as http:
        cap = http.get("/api/capabilities")
        data = cap.json()
        assert cap.status_code == 200 and data["personal_read"]
        assert data["personal_mode"] == "mirror" and data["personal_cloud_read_enabled"]
        assert not data["personal_write"] and not data["personal_upload"] and not data["field_checks_write"]
        assert data["field_checks_read"]
        assert data["personal_mirror"]["source_library_id"] == env["source"]["library_id"]
        assert data["personal_mirror"]["snapshot_id"] and data["personal_mirror"]["snapshot_sha256"]
        version = data["personal_read_version"]
        assert len(version) == 64
        assert_private(cap, version)
        paths = ["/api/projects/default", f"/api/projects/{env['project_id']}/decision-cards",
            f"/api/decision-cards/{env['card_id']}", f"/api/decision-cards/{env['card_id']}/revisions",
            f"/api/projects/{env['project_id']}/sources", f"/api/sources/{env['source_id']}",
            f"/api/sources/{env['source_id']}/revisions", f"/api/projects/{env['project_id']}/checklists",
            f"/api/checklists/{env['instance_id']}", f"/api/checklist-items/{env['item_id']}/revisions"]
        for path in paths:
            answer = http.get(path, headers={VERSION_HEADER: version})
            assert answer.status_code == 200, answer.text
            assert answer.json()["personal_read_version"] == version
            assert_private(answer, version)
        history = http.get(f"/api/decision-cards/{env['card_id']}/revisions").json()["revisions"]
        assert [row["revision"] for row in history] == [2, 1]
        checklist = http.get(f"/api/checklists/{env['instance_id']}").json()["checklist"]
        assert checklist["items"][0]["checks"][0]["note"] == "型号未定，保留观察"
        exported = http.get(f"/api/decision-cards/{env['card_id']}/export.md", headers={VERSION_HEADER: version})
        assert exported.status_code == 200 and "个人防水决定" in exported.text
        assert_private(exported, version)
        selected = http.post(f"/api/projects/{env['project_id']}/decision-cards/export",
            json={"card_ids": [env["card_id"]]}, headers={VERSION_HEADER: version})
        assert selected.status_code == 200 and "个人防水决定" in selected.text
        assert_private(selected, version)
    assert table_state(env) == before and (env["data"] / "node-identity.json").read_bytes() == identity


def test_read_version_checked_for_cap_default_history_exports_and_download(cloud_read_env):
    env = cloud_read_env
    headers = {VERSION_HEADER: "0" * 64}
    with client() as http:
        for path in ["/api/capabilities", "/api/projects/default", f"/api/sources/{env['source_id']}",
            f"/api/decision-cards/{env['card_id']}/revisions", f"/api/decision-cards/{env['card_id']}/export.md",
            f"/api/checklist-items/{env['item_id']}/revisions", f"/api/attachments/{uid()}/file"]:
            answer = http.get(path, headers=headers)
            assert answer.status_code == 409 and answer.json()["code"] == "PERSONAL_MIRROR_READ_VERSION_CHANGED"
            assert_private(answer)
        answer = http.post(f"/api/projects/{env['project_id']}/decision-cards/export",
            json={"card_ids": [env["card_id"]]}, headers=headers)
        assert answer.status_code == 409


def test_cloud_field_read_switch_is_independent_of_other_personal_reads(cloud_read_env):
    env = cloud_read_env
    config.set_local({"field_checks_enabled": False})
    with client() as http:
        cap = http.get("/api/capabilities").json()
        assert cap["personal_read"] and not cap["field_checks_read"] and not cap["field_checks_write"]
        assert http.get(f"/api/sources/{env['source_id']}").status_code == 200
        assert http.get(f"/api/checklists/{env['instance_id']}").status_code == 403


def test_cloud_hard_rejects_body_sql_receipt_staging_and_matching_primary(cloud_read_env, monkeypatch):
    env = cloud_read_env
    before = table_state(env)
    def forbidden(*args, **kwargs):
        pytest.fail("cloud mutation reached body, SQL, receipt or storage setup")
    with monkeypatch.context() as patch:
        patch.setattr(Request, "stream", forbidden)
        patch.setattr(db, "connect", forbidden)
        patch.setattr(storage_admin, "profile", forbidden)
        patch.setattr(personal, "capabilities", lambda con: {"instance_id": env["source"]["node_id"],
            "personal_primary_id": env["source"]["node_id"], "primary_epoch": env["primary_epoch"],
            "personal_read": True, "personal_write": True, "personal_upload": True, "field_checks": True})
        assert not personal.can_write(None, env["primary_epoch"], field=True, upload=True)
        with client() as http:
            for method, path in [("PATCH", f"/api/projects/{env['project_id']}"),
                ("POST", f"/api/projects/{env['project_id']}/decision-cards"),
                ("PATCH", f"/api/decision-cards/{env['card_id']}"),
                ("POST", f"/api/decision-cards/{env['card_id']}/evidence"),
                ("POST", f"/api/projects/{env['project_id']}/sources"),
                ("PATCH", f"/api/sources/{env['source_id']}"),
                ("POST", f"/api/sources/{env['source_id']}/attachments"),
                ("POST", f"/api/projects/{env['project_id']}/checklists"),
                ("PATCH", f"/api/checklists/{env['instance_id']}"),
                ("PATCH", f"/api/checklist-items/{env['item_id']}"),
                ("POST", f"/api/checklist-items/{env['item_id']}/checks"), ("GET", f"/api/operations/{uid()}")]:
                answer = http.request(method, path, content=b"invalid unparsed body")
                assert answer.status_code == 403 and answer.json()["code"] == "PERSONAL_CLOUD_READ_ONLY"
                assert_private(answer)
        for operation in [lambda: personal.mutate("illegal", None, forbidden),
            lambda: personal.receipt(None, "invalid"), lambda: attachments._stage("bad", "bad", None, "bad", "bad", "bad"),
            lambda: attachments.cleanup_staging(), lambda: attachments._cleanup_staging_locked(None),
            lambda: attachments._stage_locked(None, "bad", "bad", None, "bad", "bad", "bad"),
            lambda: attachments._receive_upload_locked(None, "bad", "bad", "bad", None, forbidden, forbidden)]:
            with pytest.raises(personal.PersonalError) as failure:
                operation()
            assert failure.value.code == "PERSONAL_CLOUD_READ_ONLY"
        async def attempted_upload():
            await attachments.receive_upload(None, "bad", "bad", "bad")
        with pytest.raises(personal.PersonalError) as failure:
            anyio.run(attempted_upload)
        assert failure.value.code == "PERSONAL_CLOUD_READ_ONLY"
        with pytest.raises(personal.PersonalError):
            with attachments.upload_lock("bad"):
                forbidden()
    assert table_state(env) == before


def test_final_factory_failure_never_returns_disabled_or_prior_success(cloud_read_env, monkeypatch):
    original = reader.open_installed_read
    @contextmanager
    def failed_exit(*args, **kwargs):
        with original(*args, **kwargs) as con:
            yield con
        raise reader.PersonalMirrorReadError("PROOF_CHANGED")
    monkeypatch.setattr(reader, "open_installed_read", failed_exit)
    with client() as http:
        for path in ["/api/capabilities", "/api/projects/default", f"/api/sources/{cloud_read_env['source_id']}"]:
            answer = http.get(path)
            assert answer.status_code == 409 and answer.json()["code"] == "PERSONAL_MIRROR_READ_PROOF_CHANGED"
            assert "project" not in answer.json() and "personal_read" not in answer.json()


def test_callback_not_installed_error_does_not_trigger_factory_enter_fallback(cloud_read_env):
    seen = []
    def failed(con):
        seen.append(con)
        raise reader.PersonalMirrorReadError("NOT_INSTALLED", 403)
    with pytest.raises(personal.PersonalError) as failure:
        read_result(failed, gate=False, allow_disabled=True)
    assert failure.value.code == "PERSONAL_MIRROR_READ_NOT_INSTALLED" and len(seen) == 1


def attachment_install(env):
    env.update(objects(env))
    install(env, attachment=True)
    config.set_local({"personal_cloud_read_enabled": True})
    with reader.open_installed_read(env["profile"]) as con:
        aid = con.execute("SELECT attachment_id FROM source_attachment WHERE state='ready'").fetchone()[0]
    return aid


def test_attachment_metadata_full_download_and_range_use_original_fd(mirror_env, monkeypatch):
    env, aid = mirror_env, attachment_install(mirror_env)
    before = table_state(env)
    from reno import sync_media
    original = sync_media._Directory.open_file
    opened = []
    def spy(directory, name, **kwargs):
        if directory.path.name == "files" and directory.path.parent.name == "personal-attachments":
            opened.append(name)
        return original(directory, name, **kwargs)
    monkeypatch.setattr(sync_media._Directory, "open_file", spy)
    with client() as http:
        metadata = http.get(f"/api/sources/{env['source_id']}")
        view = metadata.json()["source"]["attachments"][0]
        assert view["state"] == "ready" and view["readable"] and view["read_state"] == "ready" and not view["missing"]
        assert "storage_key" not in view
        opened.clear()
        version = metadata.json()["personal_read_version"]
        answer = http.get(f"/api/attachments/{aid}/file", headers={VERSION_HEADER: version})
        assert answer.status_code == 200 and answer.content == BODY
        assert hashlib.sha256(answer.content).hexdigest() == view["sha256"]
        assert len(opened) == 1  # No response-time reopen after factory proof.
        assert_private(answer, version)
        answer = http.get(f"/api/attachments/{aid}/file", headers={VERSION_HEADER: version, "Range": "bytes=2-8"})
        assert answer.status_code == 206 and answer.content == BODY[2:9]
        assert answer.headers["content-range"] == f"bytes 2-8/{len(BODY)}"
    assert table_state(env) == before


def test_missing_attachment_keeps_source_text_and_denies_download(mirror_env):
    env, aid = mirror_env, attachment_install(mirror_env)
    con = db.connect()
    key = con.execute("SELECT storage_key FROM source_attachment WHERE attachment_id=?", (aid,)).fetchone()[0]
    con.close()
    (env["data"] / "personal-attachments" / key).unlink()
    with client() as http:
        answer = http.get(f"/api/sources/{env['source_id']}")
        assert answer.status_code == 200
        assert answer.json()["source"]["title"] == "绍兴合成核对资料"
        view = answer.json()["source"]["attachments"][0]
        assert view["state"] == "ready" and view["missing"] and not view["readable"]
        assert view["read_state"] == "missing" and view["reason"]
        assert http.get(f"/api/attachments/{aid}/file").status_code == 409


def test_download_final_factory_failure_closes_original_holder(mirror_env, monkeypatch):
    env, aid = mirror_env, attachment_install(mirror_env)
    original_factory, original_open = reader.open_installed_read, reader.open_owned_attachment
    owned = []
    def opened(*args):
        value = original_open(*args)
        owned.append(value)
        return value
    @contextmanager
    def failed(*args, **kwargs):
        with original_factory(*args, **kwargs) as con:
            yield con
        raise reader.PersonalMirrorReadError("PROOF_CHANGED")
    monkeypatch.setattr(reader, "open_owned_attachment", opened)
    monkeypatch.setattr(reader, "open_installed_read", failed)
    with client() as http:
        answer = http.get(f"/api/attachments/{aid}/file")
        assert answer.status_code == 409
    assert len(owned) == 1 and owned[0].stream.closed


def test_acquisition_disconnect_interrupts_initial_full_sha_and_drains_actual_factory(mirror_env, monkeypatch):
    """The client leaves during real hashing, before any fd result is published."""
    from reno import personal_mirror_transfer_files as files
    from app.personal_api import router as personal_router
    env, aid = mirror_env, attachment_install(mirror_env)
    before = table_state(env)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    observed, published, messages = {}, [], []
    actual_full, actual_factory, actual_publish = files._HeldFile.full, reader.open_installed_read, reader.OwnedAttachment._publish

    def full(held, check):
        if held.descriptor is None:
            return actual_full(held, check)
        observed.update(stream=held.stream, fd=held.stream.fileno(), worker=threading.get_ident())
        checks = 0
        def paused_check():
            nonlocal checks
            check()
            checks += 1
            if checks == 3:
                # The actual full() has already read and hashed the first body
                # chunk. Retain that original fd until disconnect is observed.
                observed["hashed_bytes"] = held.stream.tell()
                entered.set()
                assert release.wait(10)
                try:
                    check()
                except personal.PersonalError as error:
                    observed["cancel_code"] = error.code
                    observed["fd_open_at_cancel"] = not held.stream.closed
                    raise
        return actual_full(held, paused_check)

    @contextmanager
    def factory(*args, **kwargs):
        con = None
        try:
            with actual_factory(*args, **kwargs) as con:
                yield con
        except BaseException as error:
            observed["factory_failure"] = getattr(error, "code", type(error).__name__)
            raise
        finally:
            observed["factory_worker"] = threading.get_ident()
            observed["connection_closed"] = con is not None and con._closed
            observed["guard_released"] = lifecycle.current_guard() is None
            ended.set()

    def publish(holder, seal):
        published.append(holder)
        return actual_publish(holder, seal)

    monkeypatch.setattr(files._HeldFile, "full", full)
    monkeypatch.setattr(reader, "open_installed_read", factory)
    monkeypatch.setattr(reader.OwnedAttachment, "_publish", publish)
    app = FastAPI()
    app.include_router(personal_router)
    scope = {**request().scope, "path": f"/api/attachments/{aid}/file", "raw_path": f"/api/attachments/{aid}/file".encode()}

    async def run():
        disconnected = anyio.Event()
        initial = True
        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {"type": "http.request", "body": b"", "more_body": False}
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            disconnected.set()
            return {"type": "http.disconnect"}
        async def send(message):
            messages.append(message)
        task = asyncio.create_task(app(scope, receive, send))
        with anyio.fail_after(10):
            await disconnected.wait()
            await anyio.sleep(0)
            assert not ended.is_set() and not observed["stream"].closed
            release.set()
            await task
        # Both acquisition tasks and the original actual worker have ended;
        # neither receive ownership nor the fd is handed to a success response.
        assert ended.is_set() and observed["stream"].closed
        assert not any(task.get_name() in {"reno-personal-acquisition-disconnect", "reno-personal-acquisition-worker"}
            for task in asyncio.all_tasks() if not task.done())

    try:
        anyio.run(run)
    finally:
        release.set()
    assert observed["hashed_bytes"] == len(BODY)
    assert observed["cancel_code"] == observed["factory_failure"] == "PERSONAL_ATTACHMENT_CANCELLED"
    assert observed["fd_open_at_cancel"] and observed["connection_closed"] and observed["guard_released"]
    assert observed["worker"] == observed["factory_worker"]
    assert published == []
    starts = [message for message in messages if message["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 409
    assert not any(200 <= message["status"] < 300 for message in starts)
    assert table_state(env) == before


def test_download_response_cancellation_drains_original_io_before_close(mirror_env, monkeypatch):
    env, aid = mirror_env, attachment_install(mirror_env)
    result = read_result(lambda con: attachments.open_owned_attachment(con, aid))
    holder = result.data
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = holder.stream.read
    def blocked(size):
        entered.set()
        assert release.wait(10)
        try:
            assert not holder.stream.closed
            return original(size)
        finally:
            finished.set()
    monkeypatch.setattr(holder.stream, "read", blocked)
    response = HeldAttachmentResponse(holder, request())
    async def run():
        async def send(message):
            pass
        async def receive():
            await anyio.Event().wait()
        task = asyncio.create_task(response(request().scope, receive, send))
        with anyio.fail_after(10):
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            task.cancel()
            await anyio.sleep(0)
            assert not holder.stream.closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    try:
        anyio.run(run)
    finally:
        release.set()
        holder.close()
    assert finished.is_set() and holder.stream.closed


def test_download_disconnect_waits_for_original_io_and_closes(mirror_env, monkeypatch):
    env, aid = mirror_env, attachment_install(mirror_env)
    holder = read_result(lambda con: attachments.open_owned_attachment(con, aid)).data
    entered, release = threading.Event(), threading.Event()
    original = holder.stream.read
    def blocked(size):
        entered.set()
        assert release.wait(10)
        assert not holder.stream.closed
        return original(size)
    monkeypatch.setattr(holder.stream, "read", blocked)
    response = HeldAttachmentResponse(holder, request())
    async def run():
        noticed = anyio.Event()
        async def send(message):
            pass
        async def receive():
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            noticed.set()
            return {"type": "http.disconnect"}
        task = asyncio.create_task(response(request().scope, receive, send))
        with anyio.fail_after(10):
            await noticed.wait()
            await anyio.sleep(0)
            assert not holder.stream.closed
            release.set()
            await task
    try:
        anyio.run(run)
    finally:
        release.set()
        holder.close()
    assert holder.stream.closed


def test_changed_fd_before_success_header_returns_private_error(mirror_env, monkeypatch):
    env, aid = mirror_env, attachment_install(mirror_env)
    holder = read_result(lambda con: attachments.open_owned_attachment(con, aid)).data
    def changed(offset):
        raise reader.PersonalMirrorReadError("ATTACHMENT_CHANGED")
    monkeypatch.setattr(holder.stream, "seek", changed)
    messages = []
    async def run():
        async def send(message):
            messages.append(message)
        async def receive():
            await anyio.Event().wait()
        await HeldAttachmentResponse(holder, request())(request().scope, receive, send)
    anyio.run(run)
    assert holder.stream.closed and messages[0]["status"] == 409
    assert (b"cache-control", b"private, no-store") in messages[0]["headers"]


def test_local_original_reads_and_writes_keep_null_read_version(personal_env):
    env = personal_env
    answer = env["client"].get("/api/capabilities")
    assert answer.json()["personal_mode"] == "local" and answer.json()["personal_write"]
    assert answer.json()["field_checks_write"] and answer.json()["personal_read_version"] is None
    assert_private(answer)
    answer = env["client"].get("/api/projects/default", headers={VERSION_HEADER: "old cloud version"})
    assert answer.status_code == 200 and answer.json()["personal_read_version"] is None
    answer = env["client"].post(f"/api/projects/{env['project']['project_id']}/decision-cards",
        json=envelope({"title": "家庭仍可创建"}))
    assert answer.status_code == 201 and answer.json()["personal_read_version"] is None
    assert_private(answer)
