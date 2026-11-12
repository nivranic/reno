"""Actual Home job identity with offline resolver and synthetic yt-dlp results."""
from datetime import timedelta
from types import SimpleNamespace
import hashlib
import json

import pytest

from reno import budget, config, source_budget, source_contract, source_resolution as adapter
from reno import source_resolution_budget as resolver, tasks
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_sync_agent import install_import
from tests.conftest import released16_env as env
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import bind, executing
from tests.test_source_budget import success, failure, table
from tests.test_source_resolution_budget import NOW, success as resolution_success


FIRST = "https://www.bilibili.com/video/BVfake123?p=2"
SECOND = "https://www.bilibili.com/video/BVfake123?p=2&fresh=1"


@pytest.fixture
def home_source(env, monkeypatch):
    install_import(env, monkeypatch)
    config.set_local({"budget_enabled": True})
    calls, requests = [], []
    def network(prepared, **kwargs):
        requests.append(prepared.plan)
        return resolution_success(prepared.plan, leaf=SECOND if prepared.plan.generation else FIRST)
    monkeypatch.setattr(adapter, "run", network)
    monkeypatch.setattr(resolver, "_now", lambda: NOW)
    original_now = budget._now
    monkeypatch.setattr(budget, "_now", lambda value=None: resolver._now() if value is None else original_now(value))
    with home_cycle(env) as cycle, executing(cycle):
        binding = bind(cycle)
        resolver.resolve_url(binding)
        def run(plan, **kwargs):
            calls.append(plan)
            return success(plan, binding.profile, metadata={"id": "BVfake123", "title": plan.phase})
        def verify(artifact, **kwargs):
            path = binding.profile.media / artifact.relpath
            stat = path.stat()
            assert (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) == (
                artifact.device, artifact.inode, artifact.size_bytes, artifact.mtime_ns)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
            return path
        runner = SimpleNamespace(prepare=lambda plan, **kwargs: plan, run=run, verify_artifact=verify)
        monkeypatch.setattr(source_budget, "_components", lambda: (source_contract, runner))
        yield binding, calls, requests, runner


def test_home_scope_keeps_paid_source_when_budget_disabled(home_source):
    binding, calls, requests, _ = home_source
    config.set_local({"budget_enabled": False})
    with source_budget.home_source_scope(binding):
        info = source_budget.metadata(FIRST, legacy=lambda: pytest.fail("tracked Home cannot use raw transport"))
        path = source_budget.media(FIRST, info, "BVfake123", "video")
        tasks.pause_sync(binding.core["root_job_id"], profile=binding.profile)
        assert source_budget.metadata(FIRST) == info
        assert source_budget.media(FIRST, info, "BVfake123", "video") == path
    operations = table(binding.profile, "budget_operation")
    assert len(calls) == 2 and len(requests) == 4
    assert len(operations) == 3
    assert all(row["suboperation_key"].startswith("home:" + binding.namespace + ":") for row in operations)


def test_failed_video_refresh_preserves_logical_metadata_and_media_operations(home_source):
    binding, calls, requests, runner = home_source
    def run(plan, **kwargs):
        calls.append(plan)
        if plan.segment == "video" and sum(item.segment == "video" for item in calls) == 1:
            return failure(plan)
        return success(plan, binding.profile, metadata={"id": "BVfake123", "title": plan.phase})
    runner.run = run
    with source_budget.home_source_scope(binding):
        original = source_budget.metadata(FIRST)
        path = source_budget.media(FIRST, original, "BVfake123", "video")
        current = source_budget.metadata(SECOND)
        assert current["title"] == "refresh"
        assert source_budget.media(SECOND, current, "BVfake123", "video") == path
        assert source_budget.media(SECOND, current, "BVfake123", "audio").is_file()
    assert [plan.source for plan in calls] == [FIRST, FIRST, SECOND, SECOND, SECOND]
    operations = table(binding.profile, "budget_operation")
    assert len(operations) == 3 and len(requests) == 8
    attempts = table(binding.profile, "budget_attempt")
    assert len({row["operation_id"] for row in attempts if row["download_segment"] is not None}) == 1
    metadata_id = next(row["operation_id"] for row in operations if row["kind"] == "source_metadata")
    metadata = [row for row in attempts if row["operation_id"] == metadata_id]
    assert len(metadata) == 2 and metadata[0]["operation_id"] == metadata[1]["operation_id"]


def test_age_refresh_is_initial_metadata_transport_and_does_not_authorize_second_refresh(home_source, monkeypatch):
    binding, calls, requests, runner = home_source
    completed = resolver.completed_resolution(binding)
    monkeypatch.setattr(resolver, "_now", lambda: NOW + timedelta(seconds=3601))
    resolver.resolve_url(binding, refresh_authorization=resolver.RefreshAuthorization(
        completed.attempt_id, completed.settlement_sha256))
    # Preserve a provably later first metadata dispatch; identical frozen
    # instants cannot prove it happened after the refresh admission.
    monkeypatch.setattr(resolver, "_now", lambda: NOW + timedelta(seconds=3602))
    def run(plan, **kwargs):
        calls.append(plan)
        return failure(plan) if plan.segment == "video" and sum(item.segment == "video" for item in calls) < 3 else success(
            plan, binding.profile, metadata={"id": "BVfake123", "title": plan.phase})
    runner.run = run
    with source_budget.home_source_scope(binding):
        info = source_budget.metadata(SECOND)
        source_budget.media(SECOND, info, "BVfake123", "video")
        assert source_budget.metadata(FIRST) == info
    assert all(plan.source == SECOND for plan in calls)
    assert len([plan for plan in calls if plan.kind == "source_metadata"]) == 1
    assert len(requests) == 8 and len(table(binding.profile, "budget_operation")) == 3


def test_home_low_level_plan_cannot_change_transport_or_forge_refresh(home_source):
    binding, calls, _, _ = home_source
    with source_budget.home_source_scope(binding):
        original = source_budget.metadata(FIRST)
        with pytest.raises(budget.BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            source_budget.run_source(source_contract.SourcePlan("source_metadata", "https://www.bilibili.com/video/BVother"))
        with pytest.raises(budget.BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            source_budget.media(FIRST, {**original, "title": "forged"}, "BVfake123", "video")
    assert len(calls) == 1


@pytest.mark.parametrize("cleanup_raises", [False, True])
def test_remote_cancellation_during_runner_is_settled_unknown_and_original_error_survives(home_source, cleanup_raises):
    binding, calls, _, runner = home_source
    requested = [False]
    original = SyncError("private cancellation text", "SYNC_HEARTBEAT_LOST", 409)
    def check():
        if requested[0]:
            raise original
    def run(plan, *, stop_event):
        calls.append(plan)
        requested[0] = True
        assert stop_event.is_set()
        if cleanup_raises:
            raise RuntimeError("private cleanup diagnostic")
        return source_contract.SourceProcessResult(plan.request_sha256, True, True, -1, "cancelled")
    runner.run = run
    with source_budget.home_source_scope(binding, check_cancelled=check):
        with pytest.raises(SyncError) as caught:
            source_budget.metadata(FIRST)
        assert caught.value is original
        with pytest.raises(BudgetDeferred, match="indeterminate"):
            source_budget.metadata(FIRST)
    assert len(calls) == 1
    operation_id = next(row["operation_id"] for row in table(binding.profile, "budget_operation")
                        if row["kind"] == "source_metadata")
    attempts = [row for row in table(binding.profile, "budget_attempt") if row["operation_id"] == operation_id]
    assert len(attempts) == 1 and attempts[0]["state"] == "indeterminate"
    assert "private cancellation text" not in json.dumps(attempts)


def test_manual_policy_pause_does_not_cancel_already_dispatched_home_runner(home_source):
    binding, calls, _, runner = home_source
    def run(plan, *, stop_event):
        calls.append(plan)
        tasks.pause_sync(binding.core["root_job_id"], profile=binding.profile)
        assert not stop_event.is_set()
        return success(plan, binding.profile, metadata={"id": "BVfake123"})
    runner.run = run
    with source_budget.home_source_scope(binding):
        assert source_budget.metadata(FIRST)["id"] == "BVfake123"
        with pytest.raises(BudgetDeferred, match="manual_pause"):
            source_budget.media(FIRST, {"id": "BVfake123"}, "BVfake123", "video")
    assert len(calls) == 1
