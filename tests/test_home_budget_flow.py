"""Offline Home flow uses actual ingest, job queue and durable budget receipts."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import hashlib

import pytest

from reno import config, ingest, source_budget, source_contract, source_resolution as adapter
from reno import source_resolution_budget as resolver, tasks
from reno import sync_sources
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env as original_env, install_import
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing
from tests.test_sync_budget_agent import budget_transport
from tests.test_source_budget import success, table
from tests.test_source_resolution_budget import success as resolution_success
from tests.schema_fixtures import released16


LEAF = "https://www.bilibili.com/video/BVfake123"


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Remote DTO and budget_request substitution are original16 producers.
    # Keep that release selected before cold initialization, through all real
    # worker/State/paid/RPC owners and the donor's worker shutdown teardown.
    with released16(monkeypatch):
        yield from original_env.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("lost_reply", [False, True])
def test_real_ingest_resumes_original_home_job_and_paid_metadata_after_cloud_wait(env, monkeypatch, lost_reply):
    actual_ingest = ingest.ingest_url
    install_import(env, monkeypatch)
    monkeypatch.setattr(ingest, "ingest_url", actual_ingest)
    config.set_local({"budget_enabled": True, "download_daily_videos": 0})
    requests, calls = [], []
    def network(prepared, **kwargs):
        requests.append(prepared.plan)
        return replace(resolution_success(prepared.plan, leaf=LEAF), observed_at=sync_sources._now())
    monkeypatch.setattr(adapter, "run", network)
    def run(plan, **kwargs):
        calls.append(plan)
        return success(plan, profile, metadata={"id": "BVfake123", "title": "Offline Home input",
            "webpage_url": LEAF, "extractor": "Bilibili", "duration": 60})
    def verify(artifact, *, profile):
        path = profile.media / artifact.relpath
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
        return path
    runner = SimpleNamespace(prepare=lambda plan, **kwargs: plan, run=run, verify_artifact=verify)
    monkeypatch.setattr(source_budget, "_components", lambda: (source_contract, runner))
    with home_cycle(env) as cycle:
        profile = cycle.storage_profile
        rpc = budget_transport(cycle, monkeypatch, lose_defer=lost_reply)
        with executing(cycle) as (job, owner):
            with pytest.raises(BudgetDeferred) as caught:
                cycle.process_claim()
            assert caught.value.reason == ("indeterminate" if lost_reply else "daily_limit")
            core = cycle.store.assert_source_execution()
            pending = cycle.store.assert_budget_pending()
            original = deepcopy(pending["defer_request"])
            assert original["reason"] == "daily_limit"
            assert original["budget_operation_id"] is not None
            assert caught.value.operation_id == original["budget_operation_id"]
            if lost_reply:
                assert pending["defer_receipt"] is None
            else:
                assert pending["defer_receipt"] is not None
            assert cycle.store.read()["status"]["state"] == (
                "pending_delivery" if lost_reply else "budget_wait")
            assert len(calls) == 1 and len(requests) == 4
            operations_before = table(profile, "budget_operation")
            assert len(operations_before) == 3
            assert tasks.defer(job["job_id"], owner, caught.value, profile=profile)
        tasks.resume_sync(job["job_id"], profile=profile)
        config.set_local({"download_daily_videos": 1})
        with executing(cycle) as (again, next_owner):
            assert again["job_id"] == job["job_id"] and next_owner != owner
            assert cycle.resume_budget() is True
            assert cycle.store.read()["budget_pending"] is None
            assert cycle.store.assert_source_execution() == core
            renewed = deepcopy(cycle.store.read()["claims"][0])
            renewed["claim"]["lease_generation"] += 1
            cycle.store.update({"claims": [renewed]})
            results = cycle.process_claim()
            assert len(results) == 1 and results[0]["video_id"] == "BVfake123"
            assert results[0]["claim"]["lease_generation"] == renewed["claim"]["lease_generation"]
            assert cycle.store.read()["work"]["source_mapping_inputs"]["resolution"]["submitted_url"] == renewed["url"]
            assert cycle.store.assert_source_execution() == core
        assert [plan.kind for plan in calls] == ["source_metadata", "download", "download"]
        assert len(requests) == 4
        assert {row["operation_id"] for row in operations_before} == {
            row["operation_id"] for row in table(profile, "budget_operation")}
        assert rpc[0] == original
        if lost_reply:
            assert rpc[1] == original
        assert rpc[-1]["kind"] == "budget_resume" and rpc[-1]["defer_request_id"] == original["request_id"]
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
