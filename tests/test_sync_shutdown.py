"""Service shutdown signals the existing worker and waits for ownership release.

All work is synthetic, event-controlled and uses the isolated pytest database;
no HTTP server, platform, model, or cloud request is started.
"""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from reno import cli, config, db, tasks, worker


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "ORIG", tmp_path / "media" / "originals")
    db.initialize()
    from app.main import create_app
    return create_app()


def test_lifespan_stops_existing_worker_and_waits_for_inflight_release(service, monkeypatch):
    started, ready, leave = threading.Event(), threading.Event(), threading.Event()
    stop_seen, release, returned = threading.Event(), threading.Event(), threading.Event()
    join_pending = threading.Event()
    event_loop_progress = threading.Event()
    errors, stop_calls = [], []
    real_stop = worker.stop_running

    def controlled_dispatch(job):
        assert job["kind"] == "report" and tasks.holds_process_lock()
        stop = worker._current.stop
        started.set()
        if not stop.requested.wait(4):
            raise RuntimeError("Shutdown did not notify the worker")
        stop_seen.set()
        if not release.wait(4):
            raise RuntimeError("Test did not release the in-flight step")
        assert tasks.holds_process_lock()
        return {"stopped": True}

    def brief_join(timeout=5):
        stop_calls.append(timeout)
        result = real_stop(timeout=0.02)
        if not result:
            join_pending.set()
        return result

    def serve():
        try:
            with TestClient(service) as client:
                async def observe_shutdown():
                    while not stop_seen.is_set():
                        await asyncio.sleep(0.005)
                    event_loop_progress.set()
                client.portal.start_task_soon(observe_shutdown)
                tasks.submit("report")
                worker.ensure_running()
                if not started.wait(3):
                    raise RuntimeError("Controlled worker did not start")
                ready.set()
                if not leave.wait(4):
                    raise RuntimeError("Test did not request shutdown")
            returned.set()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(worker, "dispatch", controlled_dispatch)
    monkeypatch.setattr(worker, "stop_running", brief_join)
    thread = threading.Thread(target=serve, name="test-reno-lifespan", daemon=True)
    thread.start()
    try:
        assert ready.wait(3), errors
        leave.set()
        assert stop_seen.wait(3), errors
        assert join_pending.wait(3), errors
        assert event_loop_progress.wait(1), "Waiting for the worker blocked the ASGI event loop"
        assert not returned.is_set(), "Lifespan returned while its worker still held the lock"
        release.set()
        thread.join(timeout=4)
        assert not thread.is_alive() and returned.is_set(), errors
        assert not errors and len(stop_calls) >= 2
        assert all(not entry[0].is_alive() for entry in worker._threads.values())
        with tasks.process_lock() as acquired:
            assert acquired
    finally:
        leave.set()
        release.set()
        real_stop(timeout=4)
        thread.join(timeout=4)


def test_lifespan_startup_failure_still_stops_existing_worker(service, monkeypatch):
    calls = []

    def unavailable_database(*args, **kwargs):
        raise RuntimeError("controlled startup failure")

    monkeypatch.setattr(db, "connect", unavailable_database)
    monkeypatch.setattr(worker, "stop_running", lambda timeout=5: calls.append(timeout) or True)
    with pytest.raises(RuntimeError, match="controlled startup failure"):
        with TestClient(service):
            pytest.fail("Application startup must fail")
    assert calls


@pytest.mark.parametrize("fails", [False, True])
def test_cli_serve_always_waits_for_worker_shutdown_after_uvicorn(service, monkeypatch, fails):
    import uvicorn
    events = []
    stops = [False, True]
    monkeypatch.setattr(worker, "ensure_running", lambda: events.append("start") or True)

    def shutdown(timeout=5):
        events.append("stop")
        return stops.pop(0)

    def run(*args, **kwargs):
        events.append("server")
        if fails:
            raise RuntimeError("controlled server failure")

    monkeypatch.setattr(worker, "stop_running", shutdown)
    monkeypatch.setattr(uvicorn, "run", run)
    if fails:
        with pytest.raises(RuntimeError, match="controlled server failure"):
            cli.main(["serve"])
    else:
        assert cli.main(["serve"]) == 0
    assert events == ["start", "server", "stop", "stop"]


def test_cli_worker_start_failure_also_runs_shutdown(service, monkeypatch):
    events = []
    def fail_start():
        events.append("start")
        raise RuntimeError("controlled worker start failure")
    monkeypatch.setattr(worker, "ensure_running", fail_start)
    monkeypatch.setattr(worker, "stop_running", lambda timeout=5: events.append("stop") or True)
    with pytest.raises(RuntimeError, match="controlled worker start failure"):
        cli.main(["serve"])
    assert events == ["start", "stop"]
