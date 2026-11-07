"""Actual isolated Home/SQLite admission with synthetic source opportunities."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import socket
import sqlite3
import threading
import time
import uuid

import pytest

from reno import budget, config, db, source_contract, source_resolution as adapter
from reno import source_resolution_budget as bridge, source_resolution_contract as codec
from reno import sync_source_execution as authority, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env as original_env, install_import
from tests.schema_fixtures import released16
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import bind, executing


@pytest.fixture
def env(tmp_path, monkeypatch):
    # This retained producer predates the actual continuity/genesis contract.
    # Select release16 before cold SQL/State/worker initialization and preserve
    # that lifetime through the donor's complete owner/worker teardown.
    with released16(monkeypatch):
        yield from original_env.__wrapped__(tmp_path, monkeypatch)


NOW = datetime(2026, 10, 2, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
LEAF = "https://www.bilibili.com/video/BVfake123?p=2"


@pytest.fixture
def bound(env, monkeypatch):
    install_import(env, monkeypatch)
    config.set_local({"budget_enabled": True})
    monkeypatch.setattr(bridge, "_now", lambda: NOW)
    original_now = budget._now
    monkeypatch.setattr(budget, "_now", lambda value=None: bridge._now() if value is None else original_now(value))
    with home_cycle(env) as cycle, executing(cycle):
        yield bind(cycle)


def rows(binding):
    with db.connect(binding.profile.database) as con:
        return [dict(row) for row in con.execute("SELECT * FROM budget_attempt ORDER BY ordinal")]


def success(plan, *, leaf=LEAF):
    if plan.phase == "dns":
        return codec.ResolutionResult(plan.request_sha256, "dns", True, "completed",
            addresses=("8.8.8.8",), observed_at=STAMP)
    redirect = plan.current_url == plan.submitted_url
    return codec.ResolutionResult(plan.request_sha256, "headers", True, "completed",
        headers_complete=True, transport_closed=True, status=302 if redirect else 200,
        next_url=leaf if redirect else None, observed_at=STAMP)


@pytest.fixture
def network(monkeypatch):
    calls = []
    def run(prepared, *, late_sink=None):
        calls.append(prepared.plan)
        return success(prepared.plan)
    monkeypatch.setattr(adapter, "run", run)
    monkeypatch.setattr(bridge, "_now", lambda: NOW)
    return calls


def auth(completed):
    return bridge.RefreshAuthorization(completed.attempt_id, completed.settlement_sha256)


def failed_video(binding, *, key="media:v1", segment="video", kind="download"):
    plan = source_contract.SourcePlan(kind, LEAF, segment=segment if kind == "download" else None,
        info_json=source_contract.canonical_json({"id": "BVfake123"}) if kind == "download" else None,
        artifact_relpath="originals/BVfake123/video.mp4" if kind == "download" else None)
    operation = binding.operation(kind, key, plan.input_sha256)
    reserved = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256,
        download_segment=plan.segment, source_contract_version=1), profile=binding.profile)
    assert budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256, str(uuid.uuid4()),
        expected_revision=reserved.revision, profile=binding.profile).won
    result = source_contract.SourceProcessResult(plan.request_sha256, True, True, 1, "exited",
                                                 failure_class="process_failure")
    receipt = budget.settle(reserved.attempt_id, plan.request_sha256, profile=binding.profile,
        outcome="source_retry", result_text=source_contract.encode_envelope(plan, result))
    return bridge.RefreshAuthorization(reserved.attempt_id, receipt.settlement_sha256)


def test_paid_chain_is_four_distinct_opportunities_and_replays_before_gates(bound, network, monkeypatch):
    observed = bridge.resolve_url(bound)
    assert observed["resolved_url"] == LEAF and len(observed["hops"]) == 2
    assert [plan.phase for plan in network] == ["dns", "headers", "dns", "headers"]
    stored = rows(bound)
    assert [row["ordinal"] for row in stored] == [1, 2, 3, 4]
    assert all(row["state"] == "settled" and row["outcome"] == "source_step_complete" for row in stored)
    complete = bridge.completed_resolution(bound)
    assert complete.attempt_id == stored[-1]["attempt_id"]
    assert complete.request_sha256 == stored[-1]["request_sha256"]
    assert complete.settlement_sha256 == stored[-1]["settlement_sha256"]
    assert complete.generation == 0 and complete.refresh_attempt_id is None
    copied = complete.resolution
    copied["hops"].clear()
    assert len(complete.resolution["hops"]) == 2
    before = rows(bound)
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: pytest.fail("paid replay gate"))
    monkeypatch.setattr(config, "source_resolution_attempt_limit", lambda: pytest.fail("paid cap config"))
    assert bridge.resolve_url(bound) == observed
    assert bridge.completed_resolution(bound).resolution == observed
    assert rows(bound) == before and len(network) == 4


def test_sql_writer_is_released_before_every_physical_opportunity(bound, monkeypatch):
    calls = []
    def run(prepared, **kwargs):
        with sqlite3.connect(bound.profile.database, timeout=0) as con:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
        calls.append(prepared.plan.phase)
        return success(prepared.plan)
    monkeypatch.setattr(adapter, "run", run)
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    assert calls == ["dns", "headers", "dns", "headers"]


def test_normal_dns_failure_retries_original_plan_but_each_physical_call_has_own_attempt(bound, monkeypatch):
    calls = []
    def run(prepared, **kwargs):
        plan = prepared.plan
        calls.append(plan)
        if len(calls) == 1:
            return codec.ResolutionResult(plan.request_sha256, "dns", True, "completed",
                dns_error="temporary", failure_class="dns_failure", observed_at=STAMP)
        return success(plan)
    monkeypatch.setattr(adapter, "run", run)
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    assert len(calls) == 5 and calls[0] == calls[1]
    stored = rows(bound)
    assert stored[0]["outcome"] == "resolution_retry"
    assert stored[0]["attempt_id"] != stored[1]["attempt_id"]
    assert stored[0]["request_sha256"] == stored[1]["request_sha256"]


@pytest.mark.parametrize("failure", ["non_public", "empty", "invalid_answer"])
def test_closed_dns_safety_failure_is_terminal_and_never_repeated(bound, monkeypatch, failure):
    calls = []
    def run(prepared, **kwargs):
        calls.append(prepared.plan)
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", True, "completed",
            dns_error=failure, failure_class="dns_rejected", observed_at=STAMP)
    monkeypatch.setattr(adapter, "run", run)
    for _ in range(2):
        with pytest.raises(SyncError):
            bridge.resolve_url(bound)
    assert len(calls) == 1 and rows(bound)[0]["outcome"] == "terminal_error"


@pytest.mark.parametrize("termination", ["timeout", "cancelled", "network_unknown", "launch_unknown"])
def test_unknown_never_creates_another_uuid_or_ordinal(bound, monkeypatch, termination):
    calls = []
    def run(prepared, **kwargs):
        calls.append(prepared.plan)
        return codec.ResolutionResult(prepared.plan.request_sha256, prepared.plan.phase, False, termination)
    monkeypatch.setattr(adapter, "run", run)
    for _ in range(2):
        with pytest.raises(BudgetDeferred) as caught:
            bridge.resolve_url(bound)
        assert caught.value.reason == "indeterminate"
    assert bridge.completed_resolution(bound) is None
    assert len(calls) == 1 and len(rows(bound)) == 1 and rows(bound)[0]["state"] == "indeterminate"


def test_late_sink_is_original_attempt_only_and_unknown_settlement_does_not_overwrite_it(bound, monkeypatch):
    calls, late = [], []
    def run(prepared, *, late_sink=None):
        calls.append(prepared.plan)
        if len(calls) == 1:
            late.append(late_sink)
            # The late result wins SQL first; the caller still observed timeout.
            late_sink(success(prepared.plan))
            return codec.ResolutionResult(prepared.plan.request_sha256, "dns", False, "timeout")
        return success(prepared.plan)
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(BudgetDeferred, match="indeterminate"):
        bridge.resolve_url(bound)
    original = rows(bound)[0]
    assert original["state"] == "settled" and len(calls) == 1
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: pytest.fail("late admission"))
    late[0](success(calls[0]))
    assert rows(bound)[0] == original and len(calls) == 1


def test_late_callback_after_unknown_can_be_recovered_without_another_original_dns(bound, monkeypatch):
    calls, late = [], []
    def run(prepared, *, late_sink=None):
        calls.append(prepared.plan)
        if len(calls) == 1:
            late.append(late_sink)
            return codec.ResolutionResult(prepared.plan.request_sha256, "dns", False, "timeout")
        return success(prepared.plan)
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(BudgetDeferred):
        bridge.resolve_url(bound)
    old_id = rows(bound)[0]["attempt_id"]
    late[0](success(calls[0]))
    assert len(calls) == 1 and rows(bound)[0]["attempt_id"] == old_id
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    assert len(calls) == 4 and rows(bound)[0]["attempt_id"] == old_id


def test_actual_dns_thread_timeout_settles_late_without_worker_context_or_new_http(bound, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    dns_calls, http_calls = [], []
    def lookup(host, port, **kwargs):
        dns_calls.append(host)
        entered.set()
        assert release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
    original_prepare = adapter.prepare
    def prepare(plan, **kwargs):
        return original_prepare(plan, **kwargs, dns_timeout=0.02)
    def headers(prepared):
        http_calls.append(prepared.plan)
        return success(prepared.plan)
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    monkeypatch.setattr(adapter, "prepare", prepare)
    monkeypatch.setattr(adapter, "run_headers", headers)
    try:
        with pytest.raises(BudgetDeferred, match="indeterminate"):
            bridge.resolve_url(bound)
        assert entered.is_set() and dns_calls == ["b23.tv"] and not http_calls
        release.set()
        deadline = time.monotonic() + 5
        while rows(bound)[0]["state"] != "settled":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not http_calls and len(rows(bound)) == 1
        assert bridge.resolve_url(bound)["resolved_url"] == LEAF
        assert dns_calls == ["b23.tv", "www.bilibili.com"] and len(http_calls) == 2
    finally:
        release.set()


def test_reserved_reentry_keeps_original_ordinal_and_cap(bound, network, monkeypatch):
    config.set_local({"source_resolution_max_attempts": 8})
    plan = bridge._initial(bound, 8)
    first = budget.reserve(bound.resolution_operation, budget.AttemptSpec(plan.request_sha256,
        resolver_contract_version=1, resolver_phase="dns", resolver_max_attempts=8), profile=bound.profile)
    config.set_local({"source_resolution_max_attempts": 24})
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    stored = rows(bound)
    assert stored[0]["attempt_id"] == first.attempt_id
    assert len(stored) == 4 and all(plan.max_attempts == 8 for plan in network)


def test_config_cap_race_rolls_back_before_call_and_rebuilds_only_first_plan(bound, network, monkeypatch):
    actual_reserve = budget.reserve
    changed = []
    def reserve(*args, **kwargs):
        if not changed:
            config.set_local({"source_resolution_max_attempts": 9})
            changed.append(True)
        return actual_reserve(*args, **kwargs)
    monkeypatch.setattr(budget, "reserve", reserve)
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    assert len(network) == 4 and all(plan.max_attempts == 9 for plan in network)
    assert len(rows(bound)) == 4


def test_retry_after_is_original_receipt_deadline_and_reentry_does_not_spend(bound, monkeypatch):
    calls = []
    instant = [NOW]
    def run(prepared, **kwargs):
        plan = prepared.plan
        calls.append(plan)
        if len(calls) == 2:
            return codec.ResolutionResult(plan.request_sha256, "headers", True, "completed",
                headers_complete=True, transport_closed=True, status=429,
                failure_class="http_status", retry_after_seconds=120, observed_at=STAMP)
        return success(plan)
    monkeypatch.setattr(adapter, "run", run)
    monkeypatch.setattr(bridge, "_now", lambda: instant[0])
    for _ in range(2):
        with pytest.raises(BudgetDeferred) as caught:
            bridge.resolve_url(bound)
        assert caught.value.reason == "download_pacing" and caught.value.resume_at == NOW + timedelta(seconds=120)
    assert len(calls) == 2 and len(rows(bound)) == 2
    instant[0] += timedelta(seconds=120)
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    assert calls[1] == calls[2] and len(calls) == 5


def test_one_expired_resolution_refresh_retains_original_operation_and_is_paid_replay(bound, network, monkeypatch):
    first = bridge.resolve_url(bound)
    initial = bridge.completed_resolution(bound)
    monkeypatch.setattr(bridge, "_now", lambda: NOW + timedelta(seconds=3600))
    refreshed = bridge.resolve_url(bound, refresh_authorization=auth(initial))
    assert refreshed["resolved_url"] == first["resolved_url"]
    complete = bridge.completed_resolution(bound, generation=1)
    assert complete.generation == 1 and complete.refresh_attempt_id == initial.attempt_id
    assert complete.refresh_settlement_sha256 == initial.settlement_sha256
    assert len(rows(bound)) == 8 and len({row["operation_id"] for row in rows(bound)}) == 1
    assert all(plan.generation == 1 for plan in network[4:])
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: pytest.fail("paid refresh gate"))
    assert bridge.resolve_url(bound, refresh_authorization=auth(initial)) == refreshed
    assert len(network) == 8


@pytest.mark.parametrize("invalid", [True, {}, {"attempt_id": str(uuid.uuid4()), "settlement_sha256": "1" * 64, "extra": True}])
def test_refresh_cannot_be_a_boolean_or_open_shape(bound, network, invalid):
    bridge.resolve_url(bound)
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=invalid)
    assert len(network) == 4


def test_unexpired_resolution_cannot_grant_refresh(bound, network):
    bridge.resolve_url(bound)
    initial = bridge.completed_resolution(bound)
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=auth(initial))
    assert len(network) == 4 and len(rows(bound)) == 4


def test_expired_resolution_cannot_refresh_after_any_metadata_dispatch_even_unknown(bound, network, monkeypatch):
    bridge.resolve_url(bound)
    initial = bridge.completed_resolution(bound)
    plan = source_contract.SourcePlan("source_metadata", LEAF)
    op = bound.operation("source_metadata", "metadata:v1", plan.input_sha256)
    reserved = budget.reserve(op, budget.AttemptSpec(plan.request_sha256, source_contract_version=1), profile=bound.profile)
    assert budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256, str(uuid.uuid4()),
        expected_revision=reserved.revision, profile=bound.profile).won
    budget.settle(reserved.attempt_id, plan.request_sha256, profile=bound.profile, response_complete=False)
    monkeypatch.setattr(bridge, "_now", lambda: NOW + timedelta(days=1))
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=auth(initial))
    assert len(network) == 4 and len(rows(bound)) == 5


def test_closed_original_video_failure_can_grant_exactly_one_refresh(bound, network):
    bridge.resolve_url(bound)
    right = failed_video(bound)
    bridge.resolve_url(bound, refresh_authorization=right)
    complete = bridge.completed_resolution(bound, generation=1)
    assert complete.refresh_attempt_id == right.attempt_id
    different = failed_video(bound, key="media:v1:other")
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=different)
    assert len(network) == 8


@pytest.mark.parametrize("case", ["audio", "metadata", "namespace", "sha"])
def test_refresh_proof_must_be_video_failure_of_original_home_scope(bound, network, case):
    bridge.resolve_url(bound)
    right = failed_video(bound, segment="audio" if case == "audio" else "video",
        kind="source_metadata" if case == "metadata" else "download")
    if case == "namespace":
        with db.connect(bound.profile.database) as con:
            con.execute("UPDATE budget_operation SET suboperation_key='home:foreign:media' WHERE operation_id="
                "(SELECT operation_id FROM budget_attempt WHERE attempt_id=?)", (right.attempt_id,))
            con.commit()
    if case == "sha":
        right = replace(right, settlement_sha256="1" * 64)
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=right)
    assert len(network) == 4


@pytest.mark.parametrize("leaf", ["https://www.bilibili.com/video/BVdifferent?p=2",
                                  "https://www.bilibili.com/video/BVfake123?p=3"])
def test_changed_video_or_page_refresh_saves_paid_observation_then_rejects(bound, network, monkeypatch, leaf):
    bridge.resolve_url(bound)
    right = failed_video(bound)
    def run(prepared, **kwargs):
        network.append(prepared.plan)
        return success(prepared.plan, leaf=leaf)
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=right)
    assert len(network) == 8 and rows(bound)[-1]["state"] == "settled"
    with pytest.raises(budget.BudgetLedgerError):
        bridge.completed_resolution(bound, generation=1)


def test_only_real_sql_cas_winner_calls_adapter_for_same_plan(bound, monkeypatch):
    barrier, calls, lock = threading.Barrier(2), [], threading.Lock()
    actual_reserve = budget.reserve
    def reserve(*args, **kwargs):
        if args[1].ordinal == 1:
            barrier.wait(timeout=5)
        return actual_reserve(*args, **kwargs)
    def run(prepared, **kwargs):
        with lock:
            calls.append(prepared.plan)
        time.sleep(0.03)
        return success(prepared.plan)
    # Worker ownership is covered by actual bound flows above. Competing
    # synthetic threads share one trusted authority to exercise real SQLite CAS.
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: None)
    monkeypatch.setattr(budget, "reserve", reserve)
    monkeypatch.setattr(adapter, "run", run)
    def invoke():
        try:
            return bridge.resolve_url(bound)
        except BudgetDeferred as error:
            assert error.reason == "indeterminate"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=15) for future in [pool.submit(invoke), pool.submit(invoke)]]
    assert any(result is not None for result in outcomes)
    assert len(calls) == 4 and len(rows(bound)) == 4
    assert len([plan for plan in calls if plan.hop_index == 0 and plan.phase == "dns"]) == 1


def test_adapter_exception_is_unknown_and_never_retried(bound, monkeypatch):
    calls = []
    def run(prepared, **kwargs):
        calls.append(prepared.plan)
        raise OSError("synthetic private failure")
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(BudgetDeferred) as caught:
        bridge.resolve_url(bound)
    assert caught.value.reason == "indeterminate"
    with pytest.raises(BudgetDeferred, match="indeterminate"):
        bridge.resolve_url(bound)
    assert len(calls) == 1 and rows(bound)[0]["state"] == "indeterminate"


@pytest.mark.parametrize("guard_number", [1, 2])
def test_fresh_reservation_and_dispatch_gates_refuse_before_external_call(bound, network, monkeypatch, guard_number):
    original_check = authority.HomeExecutionBinding.check
    guards = []
    def check(value, *, connection=None):
        if connection is not None:
            guards.append(connection)
            assert connection.in_transaction
            if len(guards) == guard_number:
                raise BudgetDeferred("manual_pause")
        return original_check(value, connection=connection)
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", check)
    with pytest.raises(BudgetDeferred, match="manual_pause"):
        bridge.resolve_url(bound)
    assert not network
    pending = rows(bound)
    assert len(pending) == guard_number - 1
    if pending:
        assert pending[0]["state"] == "reserved" and pending[0]["first_dispatch_json"] is None
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", original_check)
    assert bridge.resolve_url(bound)["resolved_url"] == LEAF
    if pending:
        assert rows(bound)[0]["attempt_id"] == pending[0]["attempt_id"]


def test_actual_dns_cancel_preserves_original_exception_and_late_fact_does_not_start_http(bound, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    marker, callbacks = RuntimeError("synthetic cancellation"), []
    def lookup(host, port, **kwargs):
        entered.set()
        assert release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
    def cancelled():
        callbacks.append(True)
        if len(callbacks) >= 5:
            raise marker
    monkeypatch.setattr(adapter.socket, "getaddrinfo", lookup)
    monkeypatch.setattr(adapter, "run_headers", lambda *a: pytest.fail("cancelled DNS launched HTTP"))
    try:
        with pytest.raises(RuntimeError) as caught:
            bridge.resolve_url(bound, check_cancelled=cancelled)
        assert caught.value is marker and entered.is_set()
        assert len(rows(bound)) == 1 and rows(bound)[0]["state"] == "indeterminate"
        release.set()
        deadline = time.monotonic() + 5
        while rows(bound)[0]["state"] != "settled":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(rows(bound)) == 1
    finally:
        release.set()


def test_late_unavailable_ledger_keeps_unknown_attempt_without_fresh_lookup(bound, monkeypatch):
    late, calls = [], []
    def run(prepared, *, late_sink=None):
        calls.append(prepared.plan)
        late.append(late_sink)
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", False, "timeout")
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(BudgetDeferred, match="indeterminate"):
        bridge.resolve_url(bound)
    original = rows(bound)[0]
    actual_settle = budget.settle
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic unavailable ledger")
    monkeypatch.setattr(budget, "settle", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        late[0](success(calls[0]))
    monkeypatch.setattr(budget, "settle", actual_settle)
    with pytest.raises(BudgetDeferred, match="indeterminate"):
        bridge.resolve_url(bound)
    assert len(calls) == 1 and rows(bound)[0] == original


def test_late_result_with_wrong_request_is_rejected_without_authorizing_next_hop(bound, monkeypatch):
    late, calls = [], []
    def run(prepared, *, late_sink=None):
        calls.append(prepared.plan)
        late.append(late_sink)
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", False, "timeout")
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(BudgetDeferred):
        bridge.resolve_url(bound)
    old = rows(bound)
    result = replace(success(calls[0]), request_sha256="1" * 64)
    with pytest.raises(codec.ResolutionContractError):
        late[0](result)
    assert rows(bound) == old and len(calls) == 1


@pytest.mark.parametrize("field", ["continuation_id", "execution_generation", "root_job_id"])
def test_video_refresh_cannot_borrow_another_worker_lineage(bound, network, field):
    bridge.resolve_url(bound)
    right = failed_video(bound)
    with db.connect(bound.profile.database) as con:
        if field == "root_job_id":
            continuation = str(uuid.uuid4())
            foreign = con.execute("INSERT INTO job(kind,continuation_id,execution_generation) VALUES ('sync',?,1)",
                                  (continuation,)).lastrowid
        else:
            foreign = str(uuid.uuid4()) if field == "continuation_id" else 99
        con.execute(f"UPDATE budget_operation SET {field}=? WHERE operation_id="
            "(SELECT operation_id FROM budget_attempt WHERE attempt_id=?)", (foreign, right.attempt_id))
        con.commit()
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=right)
    assert len(network) == 4


def test_resolution_attempt_cap_terminates_normal_failure_without_widening(bound, monkeypatch):
    config.set_local({"source_resolution_max_attempts": 2})
    calls = []
    def run(prepared, **kwargs):
        calls.append(prepared.plan)
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", True, "completed",
            dns_error="temporary", failure_class="dns_failure", observed_at=STAMP)
    monkeypatch.setattr(adapter, "run", run)
    with pytest.raises(SyncError):
        bridge.resolve_url(bound)
    config.set_local({"source_resolution_max_attempts": 24})
    with pytest.raises(SyncError):
        bridge.resolve_url(bound)
    assert len(calls) == 2 and len(rows(bound)) == 2
    assert [row["outcome"] for row in rows(bound)] == ["resolution_retry", "terminal_error"]


def test_paid_refresh_revalidates_original_authority_namespace_without_fresh_gates(bound, network, monkeypatch):
    bridge.resolve_url(bound)
    right = failed_video(bound)
    bridge.resolve_url(bound, refresh_authorization=right)
    with db.connect(bound.profile.database) as con:
        con.execute("UPDATE budget_operation SET suboperation_key='home:foreign:media' WHERE operation_id="
            "(SELECT operation_id FROM budget_attempt WHERE attempt_id=?)", (right.attempt_id,))
        con.commit()
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: pytest.fail("paid owner gate"))
    with pytest.raises(budget.BudgetLedgerError):
        bridge.completed_resolution(bound, generation=1)
    with pytest.raises(budget.BudgetLedgerError):
        bridge.resolve_url(bound, refresh_authorization=right)
    assert len(network) == 8


def test_age_refresh_paid_recovery_uses_original_dispatch_time_after_metadata_starts(bound, network, monkeypatch):
    bridge.resolve_url(bound)
    initial = bridge.completed_resolution(bound)
    monkeypatch.setattr(bridge, "_now", lambda: NOW + timedelta(seconds=3600))
    bridge.resolve_url(bound, refresh_authorization=auth(initial))
    saved = bridge.completed_resolution(bound, generation=1)
    monkeypatch.setattr(bridge, "_now", lambda: NOW + timedelta(seconds=3601))
    failed_video(bound, kind="source_metadata", key="metadata:v1")
    monkeypatch.setattr(authority.HomeExecutionBinding, "check", lambda *a, **k: pytest.fail("paid owner gate"))
    assert bridge.completed_resolution(bound, generation=1) == saved
    assert bridge.resolve_url(bound, refresh_authorization=auth(initial)) == saved.resolution
    assert len(network) == 8


def test_unknown_takes_precedence_over_old_backoff_after_clock_moves_back(bound, monkeypatch):
    calls, instant = [], [NOW]
    def run(prepared, **kwargs):
        plan = prepared.plan
        calls.append(plan)
        if len(calls) == 2:
            return codec.ResolutionResult(plan.request_sha256, "headers", True, "completed",
                headers_complete=True, transport_closed=True, status=429, failure_class="http_status",
                retry_after_seconds=120, observed_at=STAMP)
        if len(calls) == 3:
            return codec.ResolutionResult(plan.request_sha256, "headers", False, "network_unknown")
        return success(plan)
    monkeypatch.setattr(adapter, "run", run)
    monkeypatch.setattr(bridge, "_now", lambda: instant[0])
    with pytest.raises(BudgetDeferred, match="download_pacing"):
        bridge.resolve_url(bound)
    instant[0] += timedelta(seconds=120)
    with pytest.raises(BudgetDeferred, match="indeterminate"):
        bridge.resolve_url(bound)
    before = rows(bound)
    instant[0] = NOW
    with pytest.raises(BudgetDeferred) as caught:
        bridge.resolve_url(bound)
    assert caught.value.reason == "indeterminate" and caught.value.resume_at is None
    assert rows(bound) == before and len(calls) == 3
