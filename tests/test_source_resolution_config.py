"""Resolver cap changes never rewrite historical policy identities."""
import json

import pytest

from reno import budget_contract, config


def test_resolver_cap_reads_fresh_without_changing_old_policy(tmp_path, monkeypatch):
    local = tmp_path / "isolated-config.json"
    local.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.delenv("RENO_SOURCE_RESOLUTION_MAX_ATTEMPTS", raising=False)
    policy = config.budget_settings()
    original_sha = budget_contract.policy_sha256(policy)
    assert config.source_resolution_attempt_limit() == 24
    assert "source_resolution_max_attempts" not in policy
    local.write_text(json.dumps({"source_resolution_max_attempts": 12}), encoding="utf-8")
    assert config.source_resolution_attempt_limit() == 12
    monkeypatch.setenv("RENO_SOURCE_RESOLUTION_MAX_ATTEMPTS", "17")
    assert config.source_resolution_attempt_limit() == 17
    assert budget_contract.policy_sha256(config.budget_settings()) == original_sha


@pytest.mark.parametrize("value", [True, False, 0, -1, 129, 1.5, "12", None, {}, []])
def test_invalid_private_cap_stops_before_admission(tmp_path, monkeypatch, value):
    local = tmp_path / "isolated-config.json"
    local.write_text(json.dumps({"source_resolution_max_attempts": value}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.delenv("RENO_SOURCE_RESOLUTION_MAX_ATTEMPTS", raising=False)
    with pytest.raises(budget_contract.BudgetContractError) as caught:
        config.source_resolution_attempt_limit()
    assert caught.value.code == "BUDGET_CONFIG_INVALID"


def test_invalid_environment_does_not_fall_back_to_local(tmp_path, monkeypatch):
    local = tmp_path / "isolated-config.json"
    local.write_text('{"source_resolution_max_attempts":24}', encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_SOURCE_RESOLUTION_MAX_ATTEMPTS", "invalid")
    with pytest.raises(budget_contract.BudgetContractError):
        config.source_resolution_attempt_limit()
