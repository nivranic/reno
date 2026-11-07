"""Released16 State3/4 review-only idle work preserves original source waits.

The original producer owns the whole initialization/worker/State/SQL lifetime.
These historical cases do not prove current17 genesis or source admission;
test_worker_sqlite_busy.py retains its separate current17 worker fixture.
"""
from datetime import datetime, timezone
from copy import deepcopy
import threading
import uuid

import pytest

from reno import config, db, sync_agent, tasks, worker
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env as original_env, install_import
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import bind, executing
from tests.schema_fixtures import released16


@pytest.fixture(autouse=True)
def released_home16(monkeypatch):
    # Enter before original_env initializes its historical SQLite image and
    # retain the real released16 versions through every owner and teardown.
    # No current17 producer, validator or admission capability is weakened.
    with released16(monkeypatch):
        yield


@pytest.fixture
def env(released_home16, tmp_path, monkeypatch):
    yield from original_env.__wrapped__(tmp_path, monkeypatch)


def raw(profile, job_id):
    with db.connect(profile.database) as con:
        return dict(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())


@pytest.mark.parametrize("timed", [True, False])
def test_waiting_review_observation_is_pure_and_original_lineage_stays_pending(env, monkeypatch, timed):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        binding = bind(cycle)
        profile = binding.profile
        tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit",
            resume_at=datetime(2099, 1, 1, tzinfo=timezone.utc) if timed else None), profile=profile)
        before = raw(profile, job["job_id"])
        state = cycle.store.read()
        observed = tasks.sync_waiting_job(owner, profile=profile)
        assert observed == before and observed["status"] == "pending"
        assert raw(profile, job["job_id"]) == before and cycle.store.read() == state
        assert tasks.sync_waiting_job(str(uuid.uuid4()), profile=profile) is None


def test_idle_worker_reviews_wait_without_new_job_claim_or_dispatch(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        binding = bind(cycle)
        profile = binding.profile
        tasks.defer(job["job_id"], owner, BudgetDeferred("indeterminate"), profile=profile)
        before = raw(profile, job["job_id"])
        saved = deepcopy(binding.core)
    called, requested = [], threading.Event()
    def reviews(*, review_only=False, **kwargs):
        assert review_only is True
        current = worker.execution_context()
        assert (current["job_id"], current["continuation_id"], current["execution_generation"]) == (
            saved["root_job_id"], saved["continuation_id"], saved["execution_generation"])
        assert raw(profile, current["job_id"]) == before
        called.append(current)
        requested.set()
        return {"status": "reviews_synced"}
    monkeypatch.setattr(sync_agent, "run_cycle", reviews)
    monkeypatch.setattr(worker, "dispatch", lambda *a, **k: pytest.fail("waiting review cannot dispatch source job"))
    monkeypatch.setattr(worker._wake, "wait", lambda timeout: requested.set())
    assert worker.process_pending(follow=True, stop_event=requested) == 0
    assert len(called) == 1 and raw(profile, job["job_id"]) == before
    with db.connect(profile.database) as con:
        assert con.execute("SELECT count(*) FROM job").fetchone()[0] == 1


def test_disabled_sync_never_reviews_an_idle_waiting_job(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        bind(cycle)
        tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit"), profile=cycle.storage_profile)
    config.set_local({"sync_enabled": False})
    requested = threading.Event()
    def idle(timeout):
        requested.set()
    monkeypatch.setattr(worker._wake, "wait", idle)
    monkeypatch.setattr(sync_agent, "run_cycle", lambda *a, **k: pytest.fail("disabled review"))
    assert worker.process_pending(follow=True, stop_event=requested) == 0
