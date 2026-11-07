"""Explicit original-request delivery through the real persistent local queue."""
import json
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reno import config, db, storage_admin, tasks, worker


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "home"})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    from reno import personal_mirror_admin
    seen = []

    def original_status(selected_profile, *, operation_id, expected_request_sha256):
        assert selected_profile.identity == profile.identity
        seen.append((operation_id, expected_request_sha256))
        return {"operation_id": operation_id, "request_sha256": expected_request_sha256, "state": "pending"}

    monkeypatch.setattr(personal_mirror_admin, "status", original_status)
    return profile, personal_mirror_admin, seen


@pytest.mark.parametrize("payload", [None, {}, {"operation_id": "wrong", "request_sha256": "a" * 64},
    {"operation_id": uid(), "request_sha256": "A" * 64},
    {"operation_id": uid(), "request_sha256": "a" * 64, "snapshot_id": uid()}])
def test_internal_delivery_has_closed_original_selector(payload):
    with pytest.raises(tasks.TaskError) as caught:
        tasks.normalize("personal_mirror", payload=payload, internal=True)
    assert caught.value.code == "PERSONAL_MIRROR_TASK_INVALID"


def test_generic_submit_and_http_cannot_select_personal_batches(queue_env):
    profile, _admin, seen = queue_env
    selector = {"operation_id": uid(), "request_sha256": "a" * 64}
    with pytest.raises(tasks.TaskError):
        tasks.submit("personal_mirror", payload=selector, profile=profile)
    from app.task_api import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        result = client.post("/api/tasks", json={"kind": "personal_mirror", "payload": selector})
    assert result.status_code == 400
    assert seen == [] and tasks.list_tasks() == []


def test_explicit_queue_keeps_original_selector_and_independent_task_receipt(queue_env):
    profile, _admin, seen = queue_env
    operation, first_request, second_request = uid(), uid(), uid()
    first = tasks.submit_personal_mirror(operation, "a" * 64, first_request, profile=profile)
    second = tasks.submit_personal_mirror(operation, "a" * 64, second_request, profile=profile)
    assert first["task_id"] == second["task_id"]
    assert first["request_id"] == first_request
    assert first["payload"] == {"operation_id": operation, "request_sha256": "a" * 64}
    assert first["kind"] == "personal_mirror" and first["video_id"] is None
    assert seen == [(operation, "a" * 64)] * 2
    assert tasks.get(first["task_id"], profile=profile) is None
    assert tasks.get(first["task_id"], profile=profile, include_internal=True)["payload"] == first["payload"]
    assert tasks.list_tasks() == []
    with pytest.raises(tasks.TaskError) as mismatch:
        tasks.submit_personal_mirror(operation, "b" * 64, uid(), profile=profile)
    assert mismatch.value.code == "TASK_SCOPE_BUSY"


def test_task_request_uuid_cannot_reuse_the_operation_uuid(queue_env):
    profile, _admin, seen = queue_env
    operation = uid()
    with pytest.raises(tasks.TaskError) as caught:
        tasks.submit_personal_mirror(operation, "a" * 64, operation, profile=profile)
    assert caught.value.code == "PERSONAL_MIRROR_TASK_REQUEST_CONFLICT" and seen == []


def test_missing_original_request_cannot_leave_a_job_or_receipt(queue_env, monkeypatch):
    profile, admin, _seen = queue_env

    def missing(*args, **kwargs):
        raise tasks.TaskError("原请求不存在", "PERSONAL_MIRROR_REQUEST_MISSING", 409)

    monkeypatch.setattr(admin, "status", missing)
    with pytest.raises(tasks.TaskError):
        tasks.submit_personal_mirror(uid(), "a" * 64, uid(), profile=profile)
    con = db.connect(profile.database)
    try:
        assert con.execute("SELECT count(*) FROM job").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM task_request").fetchone()[0] == 0
    finally:
        con.close()


def test_worker_resumes_only_original_request_under_real_process_ownership(queue_env, monkeypatch):
    profile, admin, _seen = queue_env
    operation, request = uid(), uid()
    task = tasks.submit_personal_mirror(operation, "a" * 64, request, profile=profile)
    delivered = []

    def deliver(selected_profile, *, operation_id, expected_request_sha256, check_cancelled):
        assert selected_profile.identity == profile.identity
        assert tasks.holds_process_lock(profile=profile)
        check_cancelled()
        assert worker.execution_context()["kind"] == "personal_mirror"
        delivered.append((operation_id, expected_request_sha256))
        return {"operation_id": operation_id, "state": "committed", "replayed": True}

    monkeypatch.setattr(admin, "deliver_worker", deliver)
    assert worker.process_pending() == 1
    final = tasks.get(task["task_id"], include_internal=True, profile=profile)
    assert final["status"] == "done" and delivered == [(operation, "a" * 64)]
    assert tasks.submit_personal_mirror(operation, "a" * 64, request, profile=profile)["task_id"] == final["task_id"]
    assert worker.process_pending() == 0 and len(delivered) == 1


def test_failed_delivery_requires_explicit_new_task_request_but_keeps_original_operation(queue_env, monkeypatch):
    profile, admin, _seen = queue_env
    operation = uid()
    first = tasks.submit_personal_mirror(operation, "a" * 64, uid(), profile=profile)

    def fail(*args, **kwargs):
        raise ValueError("PRIVATE_DETAIL_MUST_NOT_BE_EXPOSED")

    monkeypatch.setattr(admin, "deliver_worker", fail)
    assert worker.process_pending() == 1
    failed = tasks.get(first["task_id"], include_internal=True, profile=profile)
    assert failed["status"] == "failed" and failed["can_retry"] is False
    assert "PRIVATE_DETAIL" not in json.dumps(failed)
    with pytest.raises(tasks.TaskError):
        tasks.retry(first["task_id"], uid(), profile=profile)
    second = tasks.submit_personal_mirror(operation, "a" * 64, uid(), profile=profile)
    assert second["task_id"] != first["task_id"] and second["payload"] == first["payload"]


def test_ordinary_sync_timer_and_empty_startup_do_not_select_personal_batches(queue_env, monkeypatch):
    _profile, admin, seen = queue_env
    assert worker.process_pending() == 0 and seen == []
    config.set_local({"sync_enabled": True})
    from reno import sync_agent, personal_mirror_transport
    monkeypatch.setattr(sync_agent, "settings", lambda: {"interval": 60})
    stop = threading.Event()
    cycles = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Timer must not capture/freeze or deliver a personal batch")

    def cycle(**kwargs):
        cycles.append(True)
        stop.set()
        return {"state": "idle"}

    monkeypatch.setattr(admin, "deliver_worker", forbidden)
    monkeypatch.setattr(personal_mirror_transport, "freeze_mirror_locked", forbidden)
    monkeypatch.setattr(sync_agent, "run_cycle", cycle)
    assert worker.process_pending(max_jobs=1, follow=True, stop_event=stop) == 1
    assert cycles == [True] and seen == []
