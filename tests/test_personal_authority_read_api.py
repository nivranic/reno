"""Isolated H06 transport checks; runtime fixtures do not claim native authority.

The module overrides the general DB/setup fixtures. Its worker and disconnect
checks use actual threads with synthetic observations, never the normal DB,
service, facilities, or a live grant.
"""
import asyncio
from contextvars import ContextVar
import json
import threading

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
import pytest

from app import personal_authority_api as api
from reno import personal_authority_read as runtime
from reno.personal_authority_read_contract import project_filesystem_status

PATH = "/api/personal/authority/status"
UUID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(scope="module", autouse=True)
def isolated_test_config():
    # Deliberately override tests/conftest.py's DB-initializing fixture.
    yield None


@pytest.fixture(autouse=True)
def reset_test_config(monkeypatch):
    # Deliberately override the general fixture's worker/config cleanup too.
    from reno import db, storage_admin

    def forbidden(*args, **kwargs):
        raise AssertionError("H06 transport must not open SQL or initialize storage")

    for module, name in ((db, "connect"), (db, "initialize"),
                         (storage_admin, "connection"), (storage_admin, "setup")):
        monkeypatch.setattr(module, name, forbidden)
    yield


def diagnostic(state="ready"):
    report = {"state": state, "filesystem_only": True}
    if state == "reserved":
        report["init_operation_id"] = UUID
    elif state == "unavailable":
        report["code"] = "FILES_IO_UNAVAILABLE"
    elif state == "ready":
        report.update(init_operation_id=UUID, declared_binding_sha256="a" * 64,
                      windows_directory_fsync_claimed=False, fact_count=1,
                      total_bytes=64, head_state="matches")
    result = project_filesystem_status(report)
    result["observation_error_code"] = report.get("code")
    return result


def local_app():
    app = FastAPI()
    app.include_router(api.router)
    return app


def scope():
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": PATH, "raw_path": PATH.encode(),
            "root_path": "", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}


def assert_private(response):
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("state", ["absent", "retained_unproven", "reserved", "unavailable", "ready"])
def test_five_runtime_reports_are_private_diagnostics_without_a_grant(monkeypatch, state):
    result = diagnostic(state)
    calls = []

    def observe(*, check_cancelled):
        check_cancelled()
        calls.append("observed")
        return result

    monkeypatch.setattr(runtime, "observe", observe)
    with TestClient(local_app()) as client:
        response = client.get(PATH)
    assert response.status_code == 200 and response.json() == result
    assert calls == ["observed"]
    assert_private(response)
    assert response.json()["effective_authority_state"] == "unknown"
    assert response.json()["write_authority"] == "not_established"
    assert response.json()["handoff_authority"] == "not_established"
    assert UUID not in response.text and "a" * 64 not in response.text


@pytest.mark.parametrize("query", ["?root=selected", "?profile=selected", "?node_id=selected", "?token=test&root=selected"])
def test_client_target_queries_are_rejected_without_observing(monkeypatch, query):
    def forbidden(*, check_cancelled):
        raise AssertionError("Client-selected target reached the observer")

    monkeypatch.setattr(runtime, "observe", forbidden)
    with TestClient(local_app()) as client:
        response = client.get(PATH + query)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_AUTHORITY_REQUEST"
    assert_private(response)


def test_existing_token_query_is_not_used_as_a_target(monkeypatch):
    monkeypatch.setattr(runtime, "observe", lambda *, check_cancelled: diagnostic("absent"))
    with TestClient(local_app()) as client:
        response = client.get(PATH + "?token=test")
    assert response.status_code == 200
    assert response.json()["reported_filesystem_state"] == "absent"
    assert_private(response)


@pytest.mark.parametrize("headers, content", [([], b"{}"), ([("Content-Length", "0"), ("Content-Length", "0")], b""),
                                               ([("Transfer-Encoding", "chunked")], b"")])
def test_request_body_headers_are_rejected_before_the_worker(monkeypatch, headers, content):
    calls = []
    monkeypatch.setattr(runtime, "observe", lambda *, check_cancelled: calls.append("unexpected"))
    with TestClient(local_app()) as client:
        response = client.request("GET", PATH, headers=headers, content=content)
    assert response.status_code == 400 and calls == []
    assert response.json()["code"] == "INVALID_AUTHORITY_REQUEST"
    assert_private(response)


def test_body_without_a_length_header_is_refused_in_the_listener(monkeypatch):
    monkeypatch.setattr(runtime, "observe", lambda *, check_cancelled: diagnostic())

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}
        response = await api.authority_status(Request(scope(), receive=receive))
        assert response.status_code == 400
        assert json.loads(response.body)["code"] == "INVALID_AUTHORITY_REQUEST"
        assert_private(response)

    anyio.run(run)


@pytest.mark.parametrize("code, status", [("TARGET_NOT_CONFIGURED", 503), ("TARGET_PROFILE_MISMATCH", 409)])
def test_domain_failures_expose_only_bounded_code_and_private_detail(monkeypatch, code, status):
    def observe(*, check_cancelled):
        error = runtime.AuthorityReadError(code, status)
        error.args = ("synthetic-private-source-path",)
        raise error

    monkeypatch.setattr(runtime, "observe", observe)
    with TestClient(local_app()) as client:
        response = client.get(PATH)
    assert response.status_code == status
    assert response.json() == {"detail": "个人状态暂不可用，请稍后重试", "code": code}
    assert "synthetic-private-source-path" not in response.text
    assert_private(response)


def test_unexpected_exception_and_normal_exit_failure_never_send_ready(monkeypatch):
    def observe(*, check_cancelled):
        try:
            return diagnostic()
        finally:
            raise OSError("synthetic-private-source-path")

    monkeypatch.setattr(runtime, "observe", observe)
    with TestClient(local_app()) as client:
        response = client.get(PATH)
    assert response.status_code == 503 and response.json()["code"] == "OBSERVATION_FAILED"
    assert "ready" not in response.text and "synthetic-private-source-path" not in response.text
    assert_private(response)


def test_malformed_domain_metadata_cannot_escape_as_error_fields(monkeypatch):
    def observe(*, check_cancelled):
        error = runtime.AuthorityReadError("TARGET_NOT_CONFIGURED")
        error.code, error.status = "synthetic/private/source", True
        raise error

    monkeypatch.setattr(runtime, "observe", observe)
    with TestClient(local_app()) as client:
        response = client.get(PATH)
    assert response.status_code == 503 and response.json()["code"] == "OBSERVATION_FAILED"
    assert "synthetic/private/source" not in response.text
    assert_private(response)


def test_actual_worker_has_no_ambient_context(monkeypatch):
    ambient = ContextVar("synthetic_h06_ambient", default="empty")
    seen = []

    def observe(*, check_cancelled):
        check_cancelled()
        seen.append(ambient.get())
        return diagnostic("absent")

    monkeypatch.setattr(runtime, "observe", observe)
    token = ambient.set("caller-owned-scope")
    try:
        with TestClient(local_app()) as client:
            response = client.get(PATH)
    finally:
        ambient.reset(token)
    assert response.status_code == 200 and seen == ["empty"]


def test_disconnect_drains_the_actual_worker_before_any_http_response(monkeypatch):
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    messages, failures = [], []

    def observe(*, check_cancelled):
        entered.set()
        try:
            assert release.wait(10)
            try:
                check_cancelled()
            except runtime.AuthorityReadError as error:
                failures.append(error.code)
                raise
            return diagnostic()
        finally:
            ended.set()

    monkeypatch.setattr(runtime, "observe", observe)

    async def run():
        noticed = anyio.Event()

        async def receive():
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            noticed.set()
            return {"type": "http.disconnect"}

        async def send(message):
            assert ended.is_set(), "HTTP was sent before the actual worker ended"
            messages.append(message)

        task = asyncio.create_task(local_app()(scope(), receive, send))
        with anyio.fail_after(10):
            await noticed.wait()
            await anyio.sleep(0)
            assert not ended.is_set() and messages == [] and not task.done()
            release.set()
            await task

    try:
        anyio.run(run)
    finally:
        release.set()
    assert ended.is_set() and failures == ["OBSERVATION_CANCELLED"]
    starts = [message for message in messages if message["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 409
    assert (b"cache-control", b"private, no-store") in starts[0]["headers"]


def test_request_cancellation_drains_actual_worker_before_preserving_control(monkeypatch):
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()

    def observe(*, check_cancelled):
        entered.set()
        try:
            assert release.wait(10)
            check_cancelled()
            return diagnostic()
        finally:
            ended.set()

    monkeypatch.setattr(runtime, "observe", observe)

    async def run():
        async def receive():
            await anyio.Event().wait()

        task = asyncio.create_task(api.authority_status(Request(scope(), receive=receive)))
        with anyio.fail_after(10):
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            task.cancel("synthetic authority cancellation")
            await anyio.sleep(0)
            assert not ended.is_set() and not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("synthetic authority cancellation",)
            assert ended.is_set()

    try:
        anyio.run(run)
    finally:
        release.set()


class SyntheticControl(BaseException):
    pass


@pytest.mark.parametrize("error_type", [RuntimeError, SyntheticControl])
def test_receive_failure_keeps_original_object_after_worker_drain(monkeypatch, error_type):
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    primary = error_type("synthetic receive failure")

    def observe(*, check_cancelled):
        entered.set()
        try:
            assert release.wait(10)
            check_cancelled()
            return diagnostic()
        finally:
            ended.set()

    monkeypatch.setattr(runtime, "observe", observe)

    async def run():
        noticed = anyio.Event()

        async def receive():
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            noticed.set()
            raise primary

        task = asyncio.create_task(api._observe_owned(Request(scope(), receive=receive)))
        with anyio.fail_after(10):
            await noticed.wait()
            await anyio.sleep(0)
            assert not ended.is_set() and not task.done()
            release.set()
            with pytest.raises(error_type) as caught:
                await task
            assert caught.value is primary and ended.is_set()

    try:
        anyio.run(run)
    finally:
        release.set()


def test_worker_control_exception_keeps_the_original_object(monkeypatch):
    primary = SyntheticControl("synthetic worker control")

    def observe(*, check_cancelled):
        raise primary

    monkeypatch.setattr(runtime, "observe", observe)

    async def run():
        async def receive():
            await anyio.Event().wait()
        with pytest.raises(SyntheticControl) as caught:
            await api.authority_status(Request(scope(), receive=receive))
        assert caught.value is primary

    anyio.run(run)


def test_disconnect_wins_when_both_coordinator_tasks_are_already_done(monkeypatch):
    original_wait = asyncio.wait

    async def complete_both(tasks, *, return_when):
        # An explicit scheduling fixture, not a live task/code inspection.
        assert return_when == asyncio.FIRST_COMPLETED
        return await original_wait(tasks, return_when=asyncio.ALL_COMPLETED)

    async def completed_worker(callback, stopped):
        return api._Outcome(data=diagnostic())

    monkeypatch.setattr(api.asyncio, "wait", complete_both)
    monkeypatch.setattr(api, "_owned_io", completed_worker)

    async def run():
        async def receive():
            return {"type": "http.disconnect"}
        response = await api.authority_status(Request(scope(), receive=receive))
        assert response.status_code == 409
        assert json.loads(response.body)["code"] == "OBSERVATION_CANCELLED"

    anyio.run(run)


def test_cancellation_during_success_listener_cleanup_survives_the_drain(monkeypatch):
    monkeypatch.setattr(runtime, "observe", lambda *, check_cancelled: diagnostic())

    async def run():
        draining, release = anyio.Event(), anyio.Event()

        async def listener(receive, stopped):
            try:
                await anyio.Event().wait()
            finally:
                draining.set()
                await release.wait()

        async def receive():
            await anyio.Event().wait()

        monkeypatch.setattr(api, "_listen", listener)
        task = asyncio.create_task(api.authority_status(Request(scope(), receive=receive)))
        with anyio.fail_after(10):
            await draining.wait()
            task.cancel("synthetic cleanup cancellation")
            await anyio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("synthetic cleanup cancellation",)

    anyio.run(run)


def test_second_task_creation_failure_drains_the_first_task_and_preserves_primary(monkeypatch):
    original_start = asyncio.create_task
    primary = MemoryError("synthetic second task allocation")
    created, calls = [], []

    def create(coroutine, *, name):
        calls.append(name)
        if len(calls) == 2:
            raise primary
        task = original_start(coroutine, name=name)
        created.append(task)
        return task

    monkeypatch.setattr(api.asyncio, "create_task", create)

    async def run():
        async def receive():
            await anyio.Event().wait()
        with pytest.raises(MemoryError) as caught:
            await api._observe_owned(Request(scope(), receive=receive))
        assert caught.value is primary
        assert len(created) == 1 and created[0].done()

    anyio.run(run)
    assert calls == ["reno-authority-observation-disconnect", "reno-authority-observation-worker"]


def test_one_shield_failure_cannot_end_the_drain_before_the_task_is_terminal(monkeypatch):
    original_shield = asyncio.shield
    primary = RuntimeError("synthetic shield failure")

    async def run():
        noticed, release, ended = anyio.Event(), anyio.Event(), anyio.Event()
        calls = []

        async def owned():
            await release.wait()
            ended.set()

        task = asyncio.create_task(owned())

        def shield(target):
            assert target is task
            calls.append("shield")
            if len(calls) == 1:
                assert not task.done()
                noticed.set()
                raise primary
            return original_shield(target)

        monkeypatch.setattr(api.asyncio, "shield", shield)
        draining = asyncio.create_task(api._drain(task))
        with anyio.fail_after(10):
            await noticed.wait()
            await anyio.sleep(0)
            assert not ended.is_set() and not task.done() and not draining.done()
            release.set()
            with pytest.raises(RuntimeError) as caught:
                await draining
            assert caught.value is primary and ended.is_set() and task.done()
        assert len(calls) == 2

    anyio.run(run)


def test_cleanup_failures_after_terminal_drain_do_not_replace_primary_control(monkeypatch):
    original_drain = api._drain
    primary = SyntheticControl("synthetic primary control")
    cleanups = []

    def observe(*, check_cancelled):
        raise primary

    async def drain_then_fail(task, *, preserve_cancellation=False):
        try:
            await original_drain(task, preserve_cancellation=preserve_cancellation)
        finally:
            assert task.done()
            cleanups.append("terminal")
            raise RuntimeError("synthetic terminal cleanup failure")

    monkeypatch.setattr(runtime, "observe", observe)
    monkeypatch.setattr(api, "_drain", drain_then_fail)

    async def run():
        async def receive():
            await anyio.Event().wait()
        with pytest.raises(SyntheticControl) as caught:
            await api.authority_status(Request(scope(), receive=receive))
        assert caught.value is primary

    anyio.run(run)
    assert len(cleanups) == 3
    assert "OBSERVATION_TASK_CLEANUP_FAILED" in primary.__notes__


def test_ordinary_receive_failure_is_private_http_error_after_actual_worker_drain(monkeypatch):
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    messages = []

    def observe(*, check_cancelled):
        entered.set()
        try:
            assert release.wait(10)
            check_cancelled()
            return diagnostic()
        finally:
            ended.set()

    monkeypatch.setattr(runtime, "observe", observe)

    async def run():
        noticed = anyio.Event()

        async def receive():
            assert await anyio.to_thread.run_sync(lambda: entered.wait(10))
            noticed.set()
            raise RuntimeError("synthetic-private-receive-source")

        async def send(message):
            assert ended.is_set(), "HTTP was sent before the actual worker ended"
            messages.append(message)

        task = asyncio.create_task(local_app()(scope(), receive, send))
        with anyio.fail_after(10):
            await noticed.wait()
            await anyio.sleep(0)
            assert not ended.is_set() and not task.done() and messages == []
            release.set()
            await task

    try:
        anyio.run(run)
    finally:
        release.set()
    starts = [message for message in messages if message["type"] == "http.response.start"]
    assert ended.is_set() and len(starts) == 1 and starts[0]["status"] == 503
    assert (b"cache-control", b"private, no-store") in starts[0]["headers"]
    body = b"".join(message.get("body", b"") for message in messages)
    assert json.loads(body)["code"] == "OBSERVATION_FAILED"
    assert b"synthetic-private-receive-source" not in body


def test_normal_app_token_gate_registration_and_method_errors_are_private(monkeypatch):
    # Install synthetic configuration before app.main's first import, and do
    # not enter TestClient as a context manager: no normal lifespan runs.
    from reno import config

    def get(key, default=None):
        return {"auth_token": "synthetic-authority-token", "web_ui": "classic"}.get(key, default)

    monkeypatch.setattr(config, "get", get)
    monkeypatch.setattr(runtime, "observe", lambda *, check_cancelled: diagnostic("absent"))
    from app.main import create_app

    client = TestClient(create_app())
    try:
        refused = client.get(PATH)
        assert refused.status_code == 401
        assert_private(refused)
        accepted = client.get(PATH, headers={"X-Reno-Token": "synthetic-authority-token"})
        assert accepted.status_code == 200
        assert_private(accepted)
        query_token = client.get(PATH + "?token=synthetic-authority-token")
        assert query_token.status_code == 200
        assert_private(query_token)
        invalid = client.get(PATH + "?root=client-selected", headers={"X-Reno-Token": "synthetic-authority-token"})
        assert invalid.status_code == 400
        assert_private(invalid)
        method = client.post(PATH, headers={"X-Reno-Token": "synthetic-authority-token"})
        assert method.status_code == 405
        assert_private(method)
        redirect = client.get(PATH + "/", headers={"X-Reno-Token": "synthetic-authority-token"}, follow_redirects=False)
        assert redirect.status_code == 307
        assert_private(redirect)
    finally:
        client.close()
