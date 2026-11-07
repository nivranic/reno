"""Original Home17 facts from isolated actual SQL/State/permanent origin."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract
from reno import sync_source_binding17 as bindings, sync_source_execution as legacy, sync_state
from reno.sync_protocol import SyncError
from tests.schema_fixtures import assert_current17_runtime
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create, origin_records, permanent_files, sql_change


def capture(value):
    return bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.remote.entry["item_id"])


def test_detached_original_facts_keep_paid_identity_and_return_copies(live17):
    core = create(live17)
    state, files = live17.store.read(), permanent_files(live17)
    facts = capture(live17)
    assert type(facts) is bindings.HomeBindingFacts17
    assert facts.core == core and facts.origin_ref == state["source_authority"]["origin"]
    old = legacy.HomeExecutionBinding(live17.profile, live17.store, facts.core_json,
        facts.submitted_url, legacy._SEAL)
    digest = hashlib.sha256(b"unchanged original input").hexdigest()
    assert facts.operation("model", "atomize:original", digest) == old.operation("model", "atomize:original", digest)
    assert facts.resolution_operation == old.resolution_operation
    assert facts.namespace == old.namespace
    facts.core["item_id"] += 1
    facts.origin_ref["record_id"] = str(uuid.uuid4())
    assert facts.core == core and facts.origin_ref == state["source_authority"]["origin"]
    assert live17.store.read() == state and permanent_files(live17) == files
    assert_current17_runtime()
    # The detached object is a facts carrier, not an alternative legacy check.
    assert not hasattr(facts, "check")


def test_paid_result_remains_readable_after_transport_and_worker_expiry(live17):
    create(live17)
    facts = capture(live17)
    request_sha = hashlib.sha256(b"isolated original paid request").hexdigest()
    spec = facts.operation("model", "atomize:paid", hashlib.sha256(b"input").hexdigest())
    policy = budget_contract.parse_policy({"budget_enabled": True})
    reservation = budget.reserve(spec, budget.AttemptSpec(request_sha, 1, "paas_v4", 32),
        profile=live17.profile, policy=policy)
    dispatched = budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
        str(uuid.uuid4()), expected_revision=reservation.revision, profile=live17.profile, policy=policy)
    assert dispatched.won
    budget.settle(reservation.attempt_id, request_sha, profile=live17.profile,
        result_text="original paid result")
    live17.client._source_claim_broker.latest.clear()
    sql_change(live17, "UPDATE job SET lease_until=0,manual_paused=1 WHERE job_id=?", (facts.core["root_job_id"],))
    sql_change(live17, "UPDATE worker_lease SET lease_until=0")
    reread = capture(live17)
    reread.check_stable()
    assert reread.operation("model", "atomize:paid", hashlib.sha256(b"input").hexdigest()) == spec
    assert budget.get_result(reservation.attempt_id, request_sha, profile=live17.profile).text == "original paid result"


def test_missing_origin_fails_without_initializing_authority(live17):
    before = permanent_files(live17)
    with pytest.raises(SyncError, match="Original Home"):
        capture(live17)
    assert origin_records(live17) == [] and permanent_files(live17) == before


@pytest.mark.parametrize("selector", ["bool_item", "wrong_item", "dto_store", "foreign_store"])
def test_factory_accepts_only_actual_selectors(live17, selector):
    create(live17)
    store, item_id = live17.store, live17.remote.entry["item_id"]
    if selector == "bool_item":
        item_id = True
    elif selector == "wrong_item":
        item_id += 1
    elif selector == "dto_store":
        store = deepcopy(live17.store.read())
    else:
        store = sync_state.StateStore(live17.profile.database.parent)
    with pytest.raises(SyncError):
        bindings.capture_home_binding_locked(profile=live17.profile, store=store, item_id=item_id)


def test_old_binding_seal_or_caller_core_cannot_mint_new_facts(live17):
    create(live17)
    facts = capture(live17)
    old = legacy.HomeExecutionBinding(live17.profile, live17.store, facts.core_json,
        facts.submitted_url, legacy._SEAL)
    for value in (old, facts.core, object()):
        with pytest.raises(SyncError):
            bindings.require_binding(value)
    with pytest.raises(SyncError):
        bindings.HomeBindingFacts17(profile=live17.profile, store=live17.store,
            core=facts.core, submitted_url=facts.submitted_url, origin_ref=facts.origin_ref, seal=object())


@pytest.mark.parametrize("change", ["sql_root", "source_url", "origin_bytes"])
def test_actual_original_identity_drift_is_rejected(live17, change):
    create(live17)
    facts = capture(live17)
    if change == "sql_root":
        sql_change(live17, "UPDATE job SET continuation_id=? WHERE job_id=?",
            (str(uuid.uuid4()), facts.core["root_job_id"]))
    elif change == "source_url":
        state = live17.store.read()
        state["claims"][0]["url"] = "https://www.bilibili.com/video/BV1Changed"
        live17.store.update({"claims": state["claims"]})
    else:
        path, original = origin_records(live17)[0]
        original["created_at"] = "2026-10-02T00:00:00.000Z"
        path.write_text(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises((SyncError, ValueError)):
        capture(live17)


def test_stable_sql_comparison_does_not_check_live_lease(live17):
    create(live17)
    facts = capture(live17)
    sql_change(live17, "UPDATE job SET lease_until=0,manual_paused=1 WHERE job_id=?", (facts.core["root_job_id"],))
    with sqlite3.connect(live17.profile.database) as con:
        con.row_factory = sqlite3.Row
        facts.check_stable(connection=con)


def test_actual_cancellation_exception_survives_fact_capture(live17):
    create(live17)
    marker = RuntimeError("isolated original fact cancellation")
    def cancelled():
        raise marker
    live17.store.check_cancelled = cancelled
    try:
        with pytest.raises(RuntimeError) as caught:
            capture(live17)
        assert caught.value is marker
    finally:
        live17.store.check_cancelled = None


def test_copied_context_root_selector_cannot_replace_private_home17_worker(live17):
    from reno import model_budget, tasks, worker
    core = create(live17)
    original = capture(live17)
    generic = model_budget.ModelScope(live17.profile, core["continuation_id"],
        core["execution_generation"], "atomize:generic-copy", core["root_job_id"])
    other = tasks.submit("judge", profile=live17.profile)
    with pytest.raises(budget_contract.BudgetDeferred) as wrong_root:
        bindings.require_home17_effect_scope(profile=live17.profile, root_job_id=other["job_id"])
    assert wrong_root.value.reason == "unsupported_capability"
    with model_budget.scope(generic):
        copied = copy_context()
    def check_missing_worker():
        assert worker.execution_context() is None
        actual = model_budget.current_scope()
        assert actual is generic
        for selected in (None, original):
            with pytest.raises(budget_contract.BudgetDeferred) as caught:
                bindings.require_home17_effect_scope(selected, profile=actual.profile,
                    root_job_id=actual.root_job_id)
            assert caught.value.reason == "unsupported_capability"
        # The same selector reader observes an actual ordinary job as such;
        # it does not infer a sync role from this copied public ModelScope.
        bindings.require_home17_effect_scope(profile=actual.profile, root_job_id=other["job_id"])
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(copied.run, check_missing_worker).result()
    assert live17.store.read()["source_execution"] == core


@pytest.mark.parametrize("terminal_kind", ["source_failed", "source_published"])
def test_completed_factory_does_not_convert_active_core_to_history(live17, terminal_kind):
    original = create(live17)
    before, files = live17.store.read(), permanent_files(live17)
    with pytest.raises(SyncError):
        bindings.capture_completed_home_binding_locked(profile=live17.profile, store=live17.store,
            root_job_id=original["root_job_id"], continuation_id=original["continuation_id"],
            execution_generation=original["execution_generation"], terminal_kind=terminal_kind)
    assert live17.store.read() == before and permanent_files(live17) == files


@pytest.mark.parametrize("invalid", ["root_dto", "continuation_dto", "bool_generation", "kind_dto", "unknown_kind"])
def test_completed_factory_accepts_only_original_root_selectors(live17, invalid):
    original = live17.job
    selectors = {"root_job_id": original["job_id"], "continuation_id": original["continuation_id"],
        "execution_generation": original["execution_generation"], "terminal_kind": "source_failed"}
    if invalid == "root_dto":
        selectors["root_job_id"] = deepcopy(original)
    elif invalid == "continuation_dto":
        selectors["continuation_id"] = {"continuation_id": original["continuation_id"]}
    elif invalid == "bool_generation":
        selectors["execution_generation"] = True
    elif invalid == "kind_dto":
        selectors["terminal_kind"] = {"kind": "source_failed"}
    else:
        selectors["terminal_kind"] = "budget_resumed"
    before = permanent_files(live17)
    with pytest.raises(SyncError):
        bindings.capture_completed_home_binding_locked(profile=live17.profile, store=live17.store, **selectors)
    assert permanent_files(live17) == before and origin_records(live17) == []
