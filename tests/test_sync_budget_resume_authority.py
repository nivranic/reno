"""An actual user resume is a persistent fact, not a guessed claim time."""
from copy import deepcopy
from datetime import datetime, timezone
import uuid

import pytest

from reno import db, storage_admin, tasks
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env, install_import
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import bind, executing


def pending(value):
    body = {"kind": "budget_defer", "request_id": str(uuid.uuid4()), "source": deepcopy(value.source),
        "target": {"library_id": value.options["sync_target_library_id"], "node_id": value.options["sync_target_node_id"]},
        "lease": deepcopy(value.lease()), "claim": deepcopy(value.store.read()["claims"][0]["claim"]),
        "recovery_generation": value.knowledge_generation, "recovery_stream": value.knowledge_stream,
        "reason": "daily_limit", "retry_at": "2099-01-01T00:00:00.000Z", "budget_operation_id": None}
    value.store.install_budget_defer_request(body)
    return body


def row(profile, job_id):
    with storage_admin.connection(profile) as con:
        return dict(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())


def test_manual_resume_is_bound_to_original_wait_and_survives_another_reader(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        bind(value)
        request = pending(value)
        profile = value.storage_profile
        assert not tasks.sync_resume_authorized(job["job_id"], request["request_id"], profile=profile)
        assert tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit",
            resume_at=datetime(2099, 1, 1, tzinfo=timezone.utc)), profile=profile)
        tasks.resume_sync(job["job_id"], profile=profile)
        assert tasks.sync_resume_authorized(job["job_id"], request["request_id"], profile=profile)
        assert not tasks.sync_resume_authorized(job["job_id"], str(uuid.uuid4()), profile=profile)
        after = row(profile, job["job_id"])
        assert after["not_before"] is None and after["wait_mode"] == "none"
        assert (after["continuation_id"], after["execution_generation"]) == (job["continuation_id"], job["execution_generation"])


def test_rpc_then_defer_crash_is_not_fabricated_manual_resume(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        bind(value)
        request = pending(value)
        # Still-running work is expected between cloud receipt and tasks.defer.
        assert row(value.storage_profile, job["job_id"])["not_before"] is None
        assert not tasks.sync_resume_authorized(job["job_id"], request["request_id"], profile=value.storage_profile)


def test_manual_resume_wins_before_original_owner_commits_defer(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        bind(value)
        request = pending(value)
        profile = value.storage_profile
        tasks.resume_sync(job["job_id"], profile=profile)
        assert tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit",
            resume_at=datetime(2099, 1, 1, tzinfo=timezone.utc)), profile=profile)
        assert tasks.sync_resume_authorized(job["job_id"], request["request_id"], profile=profile)
        assert row(profile, job["job_id"])["wait_mode"] == "none"


def test_repeat_manual_resume_keeps_one_authorization_receipt(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        bind(value)
        request = pending(value)
        profile = value.storage_profile
        tasks.resume_sync(job["job_id"], profile=profile)
        with storage_admin.connection(profile) as con:
            first = [dict(item) for item in con.execute("SELECT * FROM task_request ORDER BY request_id")]
        tasks.resume_sync(job["job_id"], profile=profile)
        with storage_admin.connection(profile) as con:
            assert [dict(item) for item in con.execute("SELECT * FROM task_request ORDER BY request_id")] == first
        assert tasks.sync_resume_authorized(job["job_id"], request["request_id"], profile=profile)


@pytest.mark.parametrize("action", ["pause_sync", "resume_sync"])
def test_effect_guard_rejects_before_job_or_resume_receipt_changes(env, monkeypatch, action):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        bind(value)
        pending(value)
        profile = value.storage_profile
        before = row(profile, job["job_id"])
        with storage_admin.connection(profile) as con:
            receipts = [dict(item) for item in con.execute("SELECT * FROM task_request ORDER BY request_id")]
        seen = []
        def reject(con, actual):
            assert con.in_transaction
            seen.append(dict(actual))
            raise tasks.TaskError("页面已过期", "TASK_CONTROL_STALE", 409)
        with pytest.raises(tasks.TaskError) as caught:
            getattr(tasks, action)(job["job_id"], profile=profile, effect_guard=reject)
        assert caught.value.code == "TASK_CONTROL_STALE" and seen == [before]
        assert row(profile, job["job_id"]) == before
        with storage_admin.connection(profile) as con:
            assert [dict(item) for item in con.execute("SELECT * FROM task_request ORDER BY request_id")] == receipts


def test_effect_guard_success_preserves_original_resume_authorization(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        bind(value)
        body = pending(value)
        profile = value.storage_profile
        tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit"), profile=profile)
        seen = []
        def check(con, actual):
            assert con.in_transaction and actual["job_id"] == job["job_id"]
            seen.append(actual["continuation_id"])
        tasks.resume_sync(job["job_id"], profile=profile, effect_guard=check)
        assert seen == [job["continuation_id"]]
        assert tasks.sync_resume_authorized(job["job_id"], body["request_id"], profile=profile)
