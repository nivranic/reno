"""Real Home17 identity and admission around synthetic provider effects.

Only the provider effect is replaced. Origin, HTTP claim observations, actual
schema17, worker ownership and both budget writers use their real algorithms.
"""
from copy import deepcopy
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
import json
import sqlite3

import pytest

from reno import budget, budget_contract as contract, budget_writer_witness
from reno import config, model_budget, model_scopes, source_budget, storage_lifecycle
from reno import sync_source_binding17 as binding17
from reno.sync_protocol import SyncError
from tests.test_sync_source_genesis17 import create, env, home17, live17


BODY = {"model": "synthetic-paas", "messages": [{"role": "user", "content": "Home17 original request"}],
        "temperature": 0.3, "max_tokens": 100}
KEY = "atomize:BVhome17"


def response():
    return model_budget.ProviderResponse(200, b'{"choices":[{"message":{"content":"paid answer"}}]}')


def facts(value):
    create(value)
    return binding17.capture_home_binding_locked(profile=value.profile, store=value.store,
                                                item_id=value.remote.entry["item_id"])


def model_scope(value, binding):
    core = binding.core
    return model_budget.ModelScope(value.profile, core["continuation_id"], core["execution_generation"], KEY,
        root_job_id=core["root_job_id"], home_execution=binding, home_client=value.client)


def request(send, *, transport_admission=None):
    return model_budget.request_once("paas_v4", BODY, send, classify=lambda result: "full_result",
                                     transport_admission=transport_admission)


def rows(value, table):
    with sqlite3.connect(value.profile.database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM " + table)]


@pytest.mark.parametrize("enabled", [False, True])
def test_home17_model_uses_original_key_and_ledger_when_budget_switch_is_off(live17, monkeypatch, enabled):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": enabled}).public())
    sent = []
    def send(payload):
        assert value.store._reference_depth == 0
        assert storage_lifecycle.current_guard() is None
        assert not budget_writer_witness.in_write_window()
        sent.append(payload)
        return response()
    with model_budget.scope(model_scope(value, binding)):
        assert request(send) == response()
    operations, attempts = rows(value, "budget_operation"), rows(value, "budget_attempt")
    assert len(sent) == len(operations) == len(attempts) == 1
    operation = operations[0]
    assert operation["suboperation_key"] == KEY
    assert (operation["continuation_id"], operation["execution_generation"], operation["root_job_id"]) == (
        binding.core["continuation_id"], binding.core["execution_generation"], binding.core["root_job_id"])
    assert attempts[0]["state"] == "settled" and attempts[0]["response_complete"] == 1
    assert json.loads(sent[0]) == BODY


def test_home17_paid_result_replays_before_expired_current_claim_and_all_admission_callbacks(live17, monkeypatch):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": True}).public())
    identity = model_scope(value, binding)
    with model_budget.scope(identity):
        assert request(lambda payload: response()) == response()
    claims = deepcopy(value.store.read()["claims"])
    claims[0]["lease_until"] = "2000-01-01T00:00:00.000Z"
    value.store.update({"claims": claims})
    def forbidden(*arguments):
        pytest.fail("paid replay must precede current admission and provider effect")
    with model_budget.scope(identity), model_budget.admission_check(forbidden, dispatch_guard=forbidden):
        assert request(forbidden, transport_admission=forbidden) == response()
    assert len(rows(value, "budget_operation")) == len(rows(value, "budget_attempt")) == 1


def test_home17_expired_claim_rejects_new_model_opportunity_even_when_budget_is_disabled(live17, monkeypatch):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": False}).public())
    claims = deepcopy(value.store.read()["claims"])
    claims[0]["lease_until"] = "2000-01-01T00:00:00.000Z"
    value.store.update({"claims": claims})
    sent = []
    with model_budget.scope(model_scope(value, binding)), pytest.raises((SyncError, contract.BudgetDeferred,
                                                                       budget.BudgetLedgerError)):
        request(lambda payload: sent.append(payload) or response())
    assert sent == []
    assert rows(value, "budget_operation") == rows(value, "budget_attempt") == []


@pytest.mark.parametrize("scope_kind", ["unscoped", "generic"])
def test_actual_home17_worker_cannot_send_without_original_home_scope(live17, monkeypatch, scope_kind):
    binding = facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": False}).public())
    generic = replace(model_scope(live17, binding), home_execution=None, home_client=None)
    sent = []
    context = nullcontext() if scope_kind == "unscoped" else model_budget.scope(generic)
    with context, pytest.raises(contract.BudgetDeferred):
        request(lambda payload: sent.append(payload) or response())
    assert sent == []
    assert rows(live17, "budget_operation") == rows(live17, "budget_attempt") == []


def test_paid_original_model_replays_before_missing_home_scope_rejection(live17, monkeypatch):
    binding = facts(live17)
    identity = model_scope(live17, binding)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": False}).public())
    with model_budget.scope(identity):
        assert request(lambda payload: response()) == response()
    before = rows(live17, "budget_operation"), rows(live17, "budget_attempt")
    def forbidden(*args, **kwargs):
        pytest.fail("An original paid replay acquired a fresh Home scope or provider effect")
    monkeypatch.setattr(binding17, "require_home17_effect_scope", forbidden)
    generic = replace(identity, home_execution=None, home_client=None)
    with model_budget.scope(generic), model_budget.admission_check(forbidden, dispatch_guard=forbidden):
        assert request(forbidden, transport_admission=forbidden) == response()
    assert (rows(live17, "budget_operation"), rows(live17, "budget_attempt")) == before


def test_copied_generic_model_context_cannot_send_for_actual_home17_root(live17, monkeypatch):
    binding = facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": False}).public())
    generic = replace(model_scope(live17, binding), home_execution=None, home_client=None)
    sent = []
    def effect():
        with pytest.raises(contract.BudgetDeferred):
            request(lambda payload: sent.append(payload) or response())
    with model_budget.scope(generic), ThreadPoolExecutor(max_workers=1) as pool:
        context = copy_context()
        pool.submit(context.run, effect).result(timeout=30)
    assert sent == []
    assert rows(live17, "budget_operation") == rows(live17, "budget_attempt") == []


def test_home17_inner_consumer_keeps_candidate_and_transport_guards(live17, monkeypatch):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": False}).public())
    observed = []
    def candidate(connection=None):
        observed.append(("candidate", connection is not None))
    def provider():
        observed.append(("provider", budget_writer_witness.in_write_window()))
    with source_budget.home_source_scope(binding, client=value.client):
        with model_scopes.execution(value.profile, "atomize", {"video_id": "BVhome17"}) as execution:
            with model_scopes.call(execution, KEY, candidate_guard=candidate):
                actual = model_budget.current_scope()
                assert actual.home_execution is binding and actual.home_client is value.client
                assert request(lambda payload: response(), transport_admission=provider) == response()
    assert ("candidate", True) in observed and ("provider", True) in observed
    assert len(rows(value, "budget_attempt")) == 1


def test_home17_inner_candidate_denial_prevents_ledger_and_provider_effect(live17, monkeypatch):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": True}).public())
    refusal = RuntimeError("synthetic candidate denied")
    def candidate(connection=None):
        raise refusal
    with source_budget.home_source_scope(binding, client=value.client):
        with model_scopes.execution(value.profile, "atomize", {"video_id": "BVhome17"}) as execution:
            with model_scopes.call(execution, KEY, candidate_guard=candidate):
                with pytest.raises(RuntimeError) as caught:
                    request(lambda payload: pytest.fail("denied candidate sent"))
                assert caught.value is refusal
    assert rows(value, "budget_operation") == rows(value, "budget_attempt") == []


def test_home17_complete_effect_settles_original_attempt_after_current_claim_changes(live17, monkeypatch):
    value, binding = live17, facts(live17)
    monkeypatch.setattr(config, "budget_settings", lambda: contract.parse_policy({"budget_enabled": True}).public())
    def send(payload):
        claims = deepcopy(value.store.read()["claims"])
        claims[0]["lease_until"] = "2000-01-01T00:00:00.000Z"
        value.store.update({"claims": claims})
        return response()
    with model_budget.scope(model_scope(value, binding)):
        assert request(send) == response()
    attempt = rows(value, "budget_attempt")[0]
    assert attempt["state"] == "settled" and attempt["response_complete"] == 1
    assert attempt["ordinal"] == 1
