"""Initial-origin facts do not grant a legacy source or paid execution path."""
from copy import deepcopy

import pytest

from reno import config, ingest, sync_source_genesis17 as genesis, sync_sources
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env, install_import
from tests.test_sync_budget_propagation import home_cycle


@pytest.mark.parametrize("budget_enabled", [False, True])
def test_new17_cycle_rejects_non_sdk_client_before_creating_initial_facts(env, monkeypatch, budget_enabled):
    install_import(env, monkeypatch)
    invoked = []
    monkeypatch.setattr(config, "budget_settings", lambda: {"budget_enabled": budget_enabled})
    def initial(**selectors):
        invoked.append(selectors)
        return {"format_version": 1, "core_sha256": "f" * 64}
    monkeypatch.setattr(genesis, "create_initial_core", initial)
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("New17 reached legacy DNS"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("Initial facts granted source execution"))
    with home_cycle(env) as cycle:
        cycle.source = deepcopy(cycle.source)
        cycle.source["handshake"]["migration_version"] = 17
        before = cycle.store.read()
        assert before["claims"]
        monkeypatch.setattr(cycle, "defer_budget", lambda *a, **k: pytest.fail("New17 entered legacy defer delivery"))
        with pytest.raises(BudgetDeferred) as caught:
            cycle.process_claim()
        assert caught.value.reason == "unsupported_capability"
        assert invoked == []
        after = cycle.store.read()
        assert after["claims"] == before["claims"] and after["work"] == before["work"]
        assert after["pending_publication"] == before["pending_publication"]
        assert after.get("source_execution") is None  # A returned DTO is not installed as a core.
        assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)


def test_future_unknown_version_does_not_fall_back_to_legacy_or_genesis17(env, monkeypatch):
    install_import(env, monkeypatch)
    monkeypatch.setattr(config, "budget_settings", lambda: {"budget_enabled": False})
    monkeypatch.setattr(genesis, "create_initial_core", lambda **k: pytest.fail("Unknown version used Genesis17"))
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("Unknown version reached legacy DNS"))
    with home_cycle(env) as cycle:
        cycle.source = deepcopy(cycle.source)
        cycle.source["handshake"]["migration_version"] = 18
        with pytest.raises(BudgetDeferred) as caught:
            cycle.process_claim()
        assert caught.value.reason == "unsupported_capability"
