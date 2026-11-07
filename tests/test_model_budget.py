"""Model admission and paid-response reuse with synthetic provider packets."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import sqlite3
import threading
import uuid

import pytest

from reno import budget, budget_contract as contract, config, db, llm, model_budget, storage_lifecycle

_REAL_POST = llm._post
_REAL_POST_ANTHROPIC = llm._post_anthropic

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
BODY = {"model": "synthetic-paas", "messages": [{"role": "user", "content": "frozen request"}],
        "temperature": 0.3, "max_tokens": 100}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                             tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    storage_lifecycle.initialize(profile)
    current = {"policy": contract.parse_policy({"budget_enabled": True})}
    monkeypatch.setattr(config, "budget_settings", lambda: current["policy"].public())
    monkeypatch.setattr(budget, "_now", lambda value: NOW if value is None else value)
    return profile, current


def scope(profile, continuation=None, **values):
    return model_budget.ModelScope(profile, continuation or str(uuid.uuid4()), 1, "synthetic:stage:0",
        version_tags=(("prompt_version", "v2"), ("taxonomy_version", "v2")), **values)


def packet(*, status=200, content="paid answer", usage=None):
    body = {"id": "synthetic-provider-id", "choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return model_budget.ProviderResponse(status, json.dumps(body, ensure_ascii=False).encode("utf-8"))


def classify(response):
    return "http_retry" if response.status in {429, 500, 502, 503} else "full_result"


def rows(profile, table):
    with sqlite3.connect(profile.database) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM " + table)]


def request(send, body=BODY, adapter="paas_v4", classifier=classify):
    return model_budget.request_once(adapter, body, send, classify=classifier)


def test_enabled_missing_scope_blocks_before_external_send(environment):
    calls = []
    with pytest.raises(contract.BudgetDeferred, match="unsupported_capability"):
        request(lambda payload: calls.append(payload) or packet())
    assert calls == []


def test_disabled_without_scope_keeps_legacy_send_without_ledger_creation(environment):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": False})
    calls = []
    response = request(lambda payload: calls.append(payload) or packet())
    assert response.status == 200 and len(calls) == 1
    assert rows(profile, "budget_operation") == [] and rows(profile, "budget_attempt") == []


def test_disabled_new_scope_keeps_legacy_without_new_tracked_operation(environment):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": False})
    with model_budget.scope(scope(profile)):
        assert request(lambda _: packet()).status == 200
    assert rows(profile, "budget_operation") == []


def test_paid_cache_is_reused_before_current_quota_zero_or_manual_pause(environment):
    profile, current = environment
    identity = scope(profile)
    calls = []
    response = packet()
    with model_budget.scope(identity):
        assert request(lambda payload: calls.append(payload) or response) == response
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0})
    def paused():
        pytest.fail("paid cache must be reusable before manual admission check")
    with model_budget.scope(identity), model_budget.admission_check(paused):
        restored = request(lambda _: pytest.fail("paid response cannot be sent again"))
    assert restored == response and len(calls) == 1
    stored = rows(profile, "budget_attempt")[0]
    assert stored["response_complete"] == 1 and stored["usage_quality"] == "missing"
    assert stored["state"] == "settled" and stored["result_storage_state"] == "stored"


def test_every_new_send_checks_fresh_policy_and_keeps_old_complete_http_receipt(environment):
    profile, current = environment
    identity = scope(profile)
    calls = []
    with model_budget.scope(identity):
        request(lambda payload: calls.append(payload) or packet(status=503))
        current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0})
        with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
            request(lambda payload: calls.append(payload) or packet())
    assert len(calls) == 1 and len(rows(profile, "budget_attempt")) == 1
    assert rows(profile, "budget_attempt")[0]["outcome"] == "http_retry"


def test_unknown_transport_is_persistent_and_disable_cannot_bypass_it(environment):
    profile, current = environment
    identity = scope(profile)
    calls = []
    def timed_out(payload):
        calls.append(payload)
        raise TimeoutError("synthetic private transport diagnostic")
    with model_budget.scope(identity):
        with pytest.raises(contract.BudgetDeferred, match="indeterminate"):
            request(timed_out)
    current["policy"] = contract.parse_policy({"budget_enabled": False})
    with model_budget.scope(identity):
        with pytest.raises(contract.BudgetDeferred, match="indeterminate") as caught:
            request(lambda _: pytest.fail("unknown attempt must never be resent"))
    assert len(calls) == 1 and caught.value.operation_id
    assert rows(profile, "budget_attempt")[0]["state"] == "indeterminate"


def test_http_phase_attempt_limit_is_persistent_across_scope_restart(environment):
    profile, _ = environment
    identity = scope(profile)
    calls = []
    with model_budget.scope(identity):
        for _ in range(3):
            request(lambda payload: calls.append(payload) or packet(status=503))
    with model_budget.scope(identity):
        request(lambda payload: calls.append(payload) or packet(status=503))
        with pytest.raises(model_budget.ModelPhaseExhausted):
            request(lambda _: pytest.fail("the fifth HTTP request is forbidden"))
    assert len(calls) == 4 and [row["ordinal"] for row in rows(profile, "budget_attempt")] == [1, 2, 3, 4]


def test_repair_reuses_paid_initial_response_and_continues_same_operation_ordinal(environment):
    profile, _ = environment
    identity = scope(profile, expects_json=True)
    calls = []
    def initial_classification(response):
        return "repair"
    with model_budget.scope(identity):
        request(lambda payload: calls.append(payload) or packet(content="not JSON"), classifier=initial_classification)
    repair_body = {**BODY, "messages": BODY["messages"] + [{"role": "user", "content": "repair JSON"}]}
    with model_budget.scope(identity):
        initial = request(lambda _: pytest.fail("initial paid answer must be restored"), classifier=initial_classification)
        assert b"not JSON" in initial.body
        with model_budget.phase("repair"):
            request(lambda payload: calls.append(payload) or packet(content='{"ok":true}'), body=repair_body)
    attempts = rows(profile, "budget_attempt")
    assert len(calls) == 2 and len(rows(profile, "budget_operation")) == 1
    assert [row["ordinal"] for row in attempts] == [1, 2]
    assert attempts[0]["request_sha256"] != attempts[1]["request_sha256"]
    assert attempts[0]["outcome"] == "repair" and attempts[1]["outcome"] == "full_result"


def test_initial_and_repair_http_attempts_share_initial_total_limit(environment):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_operation_max_attempts": 5})
    identity = scope(profile, expects_json=True)
    calls = []
    with model_budget.scope(identity):
        for _ in range(3):
            request(lambda payload: calls.append(payload) or packet(status=503))
        request(lambda payload: calls.append(payload) or packet(content="not JSON"), classifier=lambda _: "repair")
        with model_budget.phase("repair"):
            changed = {**BODY, "messages": [{"role": "user", "content": "repair"}]}
            request(lambda payload: calls.append(payload) or packet(status=503), body=changed)
            current["policy"] = contract.parse_policy({"budget_enabled": True, "model_operation_max_attempts": 128})
            with pytest.raises(contract.BudgetDeferred, match="attempt_limit"):
                request(lambda _: pytest.fail("configuration must not reset initial maximum"), body=changed)
    assert len(calls) == 5 and len(rows(profile, "budget_attempt")) == 5


def test_actual_frozen_payload_and_two_provider_usage_dialects(environment):
    profile, _ = environment
    captured = []
    with model_budget.scope(scope(profile)):
        request(lambda payload: captured.append(payload) or packet(usage={"prompt_tokens": 2,
            "completion_tokens": 3, "total_tokens": 5}))
    anthropic_body = {"model": "glm-5.3-synthetic", "messages": BODY["messages"], "max_tokens": 128000}
    anthropic_response = model_budget.ProviderResponse(200, json.dumps({"id": "anthropic-synthetic",
        "content": [{"type": "text", "text": "answer"}], "usage": {"input_tokens": 4, "output_tokens": 6}}).encode())
    with model_budget.scope(scope(profile)):
        request(lambda payload: captured.append(payload) or anthropic_response, body=anthropic_body, adapter="glm53_anthropic")
    assert json.loads(captured[0]) == BODY and json.loads(captured[1])["max_tokens"] == 128000
    envelopes = [json.loads(row["result_text"]) for row in rows(profile, "budget_attempt")]
    assert [item["payload_sha256"] for item in envelopes] == [hashlib.sha256(raw).hexdigest() for raw in captured]
    observations = [json.loads(row["observed_usage_json"]) for row in rows(profile, "budget_attempt")]
    assert observations[0]["total_tokens"] == 5
    assert observations[1]["input_tokens"] == 4 and observations[1]["total_tokens"] is None


def test_bad_usage_does_not_trigger_fallback_or_second_external_request(environment):
    profile, _ = environment
    calls = []
    with model_budget.scope(scope(profile)):
        request(lambda payload: calls.append(payload) or packet(usage={"prompt_tokens": True, "completion_tokens": 3}))
    assert len(calls) == 1 and rows(profile, "budget_attempt")[0]["usage_quality"] == "invalid"


def test_known_business_write_transaction_is_rejected_before_new_send(environment):
    profile, _ = environment
    con = db.connect(profile.database)
    con.execute("BEGIN IMMEDIATE")
    try:
        with model_budget.scope(scope(profile, business_connection=con)):
            with pytest.raises(budget.BudgetLedgerError, match="BUDGET_BUSINESS_TRANSACTION_ACTIVE"):
                request(lambda _: pytest.fail("write transaction must not span a new model send"))
        assert con.in_transaction and rows(profile, "budget_attempt") == []
    finally:
        con.rollback()
        con.close()


def test_scope_phase_classification_and_admission_contexts_restore_nested_values(environment):
    profile, _ = environment
    outer, inner = scope(profile), scope(profile, expects_json=True)
    assert model_budget.current_scope() is None and model_budget.current_phase() == "initial"
    with model_budget.scope(outer):
        assert model_budget.current_scope() == outer and not model_budget.expects_json()
        with model_budget.phase("repair"), model_budget.classification(expects_json=True):
            assert model_budget.current_phase() == "repair" and model_budget.expects_json()
            with model_budget.scope(inner):
                assert model_budget.current_scope() == inner and model_budget.current_phase() == "initial"
        assert model_budget.current_scope() == outer and model_budget.current_phase() == "initial"
        assert not model_budget.expects_json()
    assert model_budget.current_scope() is None


@pytest.mark.parametrize("tags", [(("secret", "private-only-value"),), (("prompt_version", "v2"), ("prompt_version", "v1")),
                                  [("prompt_version", "v2")], (("prompt_version", True),)])
def test_scope_version_tags_are_closed_and_deeply_immutable(environment, tags):
    profile, _ = environment
    with pytest.raises(budget.BudgetLedgerError):
        model_budget.ModelScope(profile, str(uuid.uuid4()), 1, "stage", version_tags=tags)


def test_new_send_runs_admission_hook_after_paid_lookup(environment):
    profile, _ = environment
    calls, guards = [], []
    with model_budget.scope(scope(profile)), model_budget.admission_check(lambda: guards.append("checked")):
        request(lambda payload: calls.append(payload) or packet())
    assert len(calls) == 1 and guards == ["checked", "checked"]


@pytest.fixture
def fake_llm(monkeypatch):
    values = {"node_role": "all", "zhipu_base_url": "https://synthetic.invalid/api",
              "zhipu_api_key": "synthetic-header-only-secret", "atomize_model": "synthetic-paas",
              "vlm_model": "synthetic-paas", "judge_models": ["synthetic-paas"], "llm_thinking_enabled": False}
    monkeypatch.setattr(config, "get", lambda key="", default=None: values.get(key, default))
    # Restore only this module's transport integration entrypoints. Every test
    # supplies a synthetic urlopen below; the suite-wide network block stays on.
    monkeypatch.setattr(llm, "_post", _REAL_POST)
    monkeypatch.setattr(llm, "_post_anthropic", _REAL_POST_ANTHROPIC)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    return values


def install_transport(monkeypatch, responses):
    sent = []
    class Response:
        def __init__(self, value):
            self.status, self.body = value.status, value.body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return self.body
    def urlopen(req, timeout=None):
        sent.append(req.data)
        assert responses, "unexpected synthetic HTTP send"
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Response(value)
    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    return sent


def test_llm_json_restart_reuses_paid_nonjson_and_only_sends_repair(environment, fake_llm, monkeypatch):
    profile, current = environment
    identity = scope(profile)
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 1})
    sent = install_transport(monkeypatch, [packet(content="not JSON"), packet(content='{"ok":true}')])
    with model_budget.scope(identity), pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        llm.chat_json(BODY["messages"], model="synthetic-paas")
    assert len(sent) == 1 and rows(profile, "budget_attempt")[0]["outcome"] == "repair"
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 2})
    with model_budget.scope(identity):
        answer, model = llm.chat_json(BODY["messages"], model="synthetic-paas")
    assert answer == {"ok": True} and model == "synthetic-paas" and len(sent) == 2
    assert len(rows(profile, "budget_operation")) == 1
    assert [row["ordinal"] for row in rows(profile, "budget_attempt")] == [1, 2]
    assert all(b"synthetic-header-only-secret" not in payload for payload in sent)


def test_llm_uncertain_timeout_is_not_http_retried_json_repaired_or_model_fallback(environment, fake_llm, monkeypatch):
    profile, _ = environment
    sent = install_transport(monkeypatch, [TimeoutError("synthetic private diagnostic")])
    with model_budget.scope(scope(profile)), pytest.raises(contract.BudgetDeferred, match="indeterminate"):
        llm.chat_json(BODY["messages"], model="synthetic-paas")
    assert len(sent) == 1 and rows(profile, "budget_attempt")[0]["state"] == "indeterminate"


def test_llm_empty_paid_json_response_is_classified_before_settlement_and_repaired(environment, fake_llm, monkeypatch):
    profile, _ = environment
    sent = install_transport(monkeypatch, [packet(content=""), packet(content='{"ok":true}')])
    with model_budget.scope(scope(profile)):
        value, _ = llm.chat_json(BODY["messages"], model="synthetic-paas")
    assert value == {"ok": True} and len(sent) == 2
    assert [row["outcome"] for row in rows(profile, "budget_attempt")] == ["repair", "full_result"]


def test_llm_anthropic_uses_actual_128k_payload_and_never_invents_total(environment, fake_llm, monkeypatch):
    profile, _ = environment
    response = model_budget.ProviderResponse(200, json.dumps({"content": [{"type": "text", "text": "answer"}],
        "usage": {"input_tokens": 2, "output_tokens": 3}}).encode())
    sent = install_transport(monkeypatch, [response])
    with model_budget.scope(scope(profile)):
        answer, _ = llm.chat(BODY["messages"], model="glm-5.3-synthetic", max_tokens=100)
    assert answer == "answer" and json.loads(sent[0])["max_tokens"] == 128000
    row = rows(profile, "budget_attempt")[0]
    assert json.loads(row["current_reservation_json"])["units"]["reserved_output_tokens"] == 128000
    assert json.loads(row["observed_usage_json"])["total_tokens"] is None


def test_llm_vision_paid_response_is_restored_before_quota_zero(environment, fake_llm, monkeypatch, tmp_path):
    profile, current = environment
    image = tmp_path / "synthetic-frame.jpg"
    image.write_bytes(b"synthetic frame bytes")
    sent = install_transport(monkeypatch, [packet(content='{"scene_description":"frame"}')])
    identity = scope(profile, expects_json=True)
    with model_budget.scope(identity):
        first = llm.vision(str(image), "observe")
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0})
    with model_budget.scope(identity):
        assert llm.vision(str(image), "observe") == first
    assert len(sent) == 1


def test_llm_cloud_guard_blocks_glm_before_ledger_or_transport(environment, fake_llm, monkeypatch):
    profile, _ = environment
    fake_llm["node_role"] = "cloud"
    sent = install_transport(monkeypatch, [])
    with model_budget.scope(scope(profile)), pytest.raises(RuntimeError, match="node_role=cloud"):
        llm.chat(BODY["messages"], model="glm-5.3-synthetic")
    assert sent == [] and rows(profile, "budget_operation") == []


def test_sql_dispatch_guard_blocks_send_and_keeps_original_reserved_attempt(environment):
    profile, _ = environment
    calls, guards = [], []
    def sql_guard(con):
        assert con.in_transaction
        count = con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0]
        guards.append(count)
        if count:
            raise contract.BudgetDeferred("manual_pause")
    with model_budget.scope(scope(profile)), model_budget.admission_check(lambda: None, dispatch_guard=sql_guard):
        with pytest.raises(contract.BudgetDeferred, match="manual_pause") as caught:
            request(lambda payload: calls.append(payload) or packet())
    assert calls == [] and guards == [0, 1]
    attempt = rows(profile, "budget_attempt")[0]
    assert attempt["state"] == "reserved" and caught.value.operation_id == attempt["operation_id"]


def test_paid_envelope_payload_digest_mismatch_blocks_reuse_without_resend(environment, monkeypatch):
    profile, _ = environment
    identity = scope(profile)
    with model_budget.scope(identity):
        request(lambda _: packet())
    real_get_result = budget.get_result
    def wrong_payload(*args, **kwargs):
        result = real_get_result(*args, **kwargs)
        envelope = json.loads(result.text)
        envelope["payload_sha256"] = "0" * 64
        return replace(result, text=json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    monkeypatch.setattr(budget, "get_result", wrong_payload)
    with model_budget.scope(identity), pytest.raises(budget.BudgetLedgerError):
        request(lambda _: pytest.fail("wrong paid payload provenance cannot authorize resend"))
    assert len(rows(profile, "budget_attempt")) == 1


def test_llm_complete_http_error_is_settled_before_retry_and_paid_success_is_reused(environment, fake_llm, monkeypatch):
    profile, current = environment
    identity = scope(profile)
    error_body = json.dumps({"usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
                             "error": {"message": "synthetic transient receipt"}}).encode()
    error = llm.urllib.error.HTTPError("https://synthetic.invalid", 503, "synthetic", None, io.BytesIO(error_body))
    sent = install_transport(monkeypatch, [error, packet(content='{"ok":true}')])
    with model_budget.scope(identity):
        assert llm.chat_json(BODY["messages"], model="synthetic-paas")[0] == {"ok": True}
    attempts = rows(profile, "budget_attempt")
    assert [row["outcome"] for row in attempts] == ["http_retry", "full_result"]
    assert all(row["response_complete"] == 1 for row in attempts)
    assert json.loads(attempts[0]["observed_usage_json"])["total_tokens"] == 2
    current["policy"] = contract.parse_policy({"budget_enabled": True, "model_daily_attempts": 0})
    with model_budget.scope(identity):
        assert llm.chat_json(BODY["messages"], model="synthetic-paas")[0] == {"ok": True}
    assert len(sent) == 2


def test_llm_incomplete_http_error_body_is_unknown_and_private_diagnostic_is_hidden(environment, fake_llm, monkeypatch):
    profile, _ = environment
    class UnreadableBody:
        def read(self, *args):
            raise TimeoutError("synthetic private provider credential diagnostic")
        def close(self):
            pass
    error = llm.urllib.error.HTTPError("https://synthetic.invalid", 503, "synthetic", None, UnreadableBody())
    sent = install_transport(monkeypatch, [error])
    identity = scope(profile)
    for _ in range(2):
        with model_budget.scope(identity), pytest.raises(contract.BudgetDeferred, match="indeterminate") as caught:
            llm.chat_json(BODY["messages"], model="synthetic-paas")
        assert "credential" not in str(caught.value)
    assert len(sent) == 1 and rows(profile, "budget_attempt")[0]["state"] == "indeterminate"


def test_paid_result_without_retained_body_stops_before_transport_or_admission(environment, monkeypatch):
    profile, _ = environment
    identity = scope(profile)
    with model_budget.scope(identity):
        request(lambda _: packet())
    real_get_result = budget.get_result
    monkeypatch.setattr(budget, "get_result", lambda *args, **kwargs:
                        replace(real_get_result(*args, **kwargs), text=None, storage_state="discarded"))
    with model_budget.scope(identity), model_budget.admission_check(lambda: pytest.fail("no new admission")):
        with pytest.raises(contract.BudgetDeferred, match="result_capacity"):
            request(lambda _: pytest.fail("paid result retention failure cannot authorize resend"))
    assert len(rows(profile, "budget_attempt")) == 1


def test_transport_cancellation_keeps_persistent_no_resend_fence(environment):
    profile, _ = environment
    identity = scope(profile)
    def cancelled(payload):
        raise KeyboardInterrupt()
    with model_budget.scope(identity), pytest.raises(KeyboardInterrupt):
        request(cancelled)
    with model_budget.scope(identity), pytest.raises(contract.BudgetDeferred, match="indeterminate"):
        request(lambda _: pytest.fail("cancelled physical send cannot be assumed free"))
    assert rows(profile, "budget_attempt")[0]["state"] == "indeterminate"


def test_disabled_manual_hook_that_enables_budget_cannot_bypass_new_ledger(environment):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": False})
    def enabled():
        current["policy"] = contract.parse_policy({"budget_enabled": True})
    with model_budget.scope(scope(profile)), model_budget.admission_check(enabled):
        request(lambda _: packet())
    assert len(rows(profile, "budget_operation")) == len(rows(profile, "budget_attempt")) == 1


def test_unscoped_hook_enabling_budget_cannot_use_legacy_send(environment):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": False})
    calls = []
    def enabled():
        current["policy"] = contract.parse_policy({"budget_enabled": True})
    with model_budget.admission_check(enabled), pytest.raises(contract.BudgetDeferred, match="unsupported_capability"):
        request(lambda payload: calls.append(payload) or packet())
    assert calls == [] and rows(profile, "budget_attempt") == []


def test_complete_deep_provider_json_is_stored_before_controlled_error_and_never_resent(environment):
    profile, _ = environment
    identity = scope(profile)
    response = model_budget.ProviderResponse(200, ("[" * 5000 + "0" + "]" * 5000).encode())
    with model_budget.scope(identity), pytest.raises(budget.BudgetLedgerError):
        request(lambda _: response, classifier=lambda value: llm._provider_outcome("paas_v4", value))
    attempt = rows(profile, "budget_attempt")[0]
    assert attempt["state"] == "settled" and attempt["response_complete"] == 1
    assert attempt["outcome"] == "terminal_error" and attempt["usage_quality"] == "missing"
    assert attempt["result_storage_state"] == "stored"
    with model_budget.scope(identity):
        restored = request(lambda _: pytest.fail("known complete deep response cannot authorize resend"))
    assert restored == response and len(rows(profile, "budget_attempt")) == 1


@pytest.mark.parametrize("tracked", [None, False, True])
def test_anthropic_role_change_during_backoff_blocks_next_physical_send(environment, fake_llm, monkeypatch, tracked):
    profile, current = environment
    current["policy"] = contract.parse_policy({"budget_enabled": tracked is True})
    answer = model_budget.ProviderResponse(200, b'{"content":[{"type":"text","text":"answer"}]}')
    sent = install_transport(monkeypatch, [packet(status=503), answer])
    role_flags = []
    real_send = llm._send_response
    def observed_send(*args, **kwargs):
        role_flags.append(config.is_cloud())
        return real_send(*args, **kwargs)
    monkeypatch.setattr(llm, "_send_response", observed_send)
    monkeypatch.setattr(llm.time, "sleep", lambda _: fake_llm.update(node_role="cloud"))
    with (nullcontext() if tracked is None else model_budget.scope(scope(profile))):
        with pytest.raises(contract.BudgetDeferred, match="unsupported_capability"):
            llm.chat(BODY["messages"], model="glm-5.3-synthetic")
    assert len(sent) == 1 and role_flags == [False]
    attempts = rows(profile, "budget_attempt")
    assert len(attempts) == int(tracked is True)
    if tracked:
        assert attempts[0]["state"] == "settled" and attempts[0]["outcome"] == "http_retry"


def test_anthropic_role_change_while_sql_writer_waits_blocks_reservation(environment, fake_llm, monkeypatch):
    profile, _ = environment
    sent = install_transport(monkeypatch, [model_budget.ProviderResponse(200, b'{"content":[{"type":"text","text":"answer"}]}')])
    writer_attempted = threading.Event()
    original_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        # Observe the attempted BEGIN without replacing the owned factory type.
        con = original_connect(*args, **kwargs)
        con.set_trace_callback(lambda statement: writer_attempted.set()
                               if statement == "BEGIN IMMEDIATE" else None)
        return con
    blocker = original_connect(profile.database)
    blocker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    observed = []
    def invoke():
        try:
            with model_budget.scope(scope(profile)):
                llm.chat(BODY["messages"], model="glm-5.3-synthetic")
        except BaseException as error:
            observed.append(error)
    runner = threading.Thread(target=invoke)
    runner.start()
    try:
        assert writer_attempted.wait(5), "model did not reach the held SQL writer"
        fake_llm["node_role"] = "cloud"
        blocker.rollback()
        runner.join(5)
        assert not runner.is_alive()
    finally:
        blocker.rollback()
        blocker.close()
        runner.join(5)
    assert len(observed) == 1 and isinstance(observed[0], contract.BudgetDeferred)
    assert observed[0].reason == "unsupported_capability" and sent == []
    assert rows(profile, "budget_operation") == rows(profile, "budget_attempt") == []


def test_anthropic_role_guard_composes_with_consumer_sql_guard_and_retains_reserved_attempt(environment, fake_llm, monkeypatch):
    profile, _ = environment
    sent = install_transport(monkeypatch, [model_budget.ProviderResponse(200, b'{"content":[{"type":"text","text":"answer"}]}')])
    witnesses = []
    def consumer_guard(con):
        assert con.in_transaction
        count = con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0]
        witnesses.append(count)
        if count:
            fake_llm["node_role"] = "cloud"
    with model_budget.scope(scope(profile)), model_budget.admission_check(lambda: None, dispatch_guard=consumer_guard):
        with pytest.raises(contract.BudgetDeferred, match="unsupported_capability") as caught:
            llm.chat(BODY["messages"], model="glm-5.3-synthetic")
    assert witnesses == [0, 1] and sent == []
    attempt = rows(profile, "budget_attempt")[0]
    assert attempt["state"] == "reserved" and caught.value.operation_id == attempt["operation_id"]


@pytest.mark.parametrize("uncertain", [False, True])
def test_transport_admission_never_precedes_paid_or_unknown_lookup(environment, uncertain):
    profile, _ = environment
    identity = scope(profile)
    response = packet()
    with model_budget.scope(identity):
        if uncertain:
            with pytest.raises(contract.BudgetDeferred, match="indeterminate"):
                request(lambda _: (_ for _ in ()).throw(TimeoutError("synthetic")))
        else:
            request(lambda _: response)
    def stopped():
        pytest.fail("transport admission must follow exact paid/unknown lookup")
    with model_budget.scope(identity):
        if uncertain:
            with pytest.raises(contract.BudgetDeferred, match="indeterminate"):
                model_budget.request_once("paas_v4", BODY, lambda _: pytest.fail("resend"),
                    classify=classify, transport_admission=stopped)
        else:
            assert model_budget.request_once("paas_v4", BODY, lambda _: pytest.fail("resend"),
                classify=classify, transport_admission=stopped) == response


def test_role_change_after_dispatch_keeps_complete_response_settlement(environment, fake_llm, monkeypatch):
    profile, _ = environment
    response = model_budget.ProviderResponse(200, b'{"content":[{"type":"text","text":"paid answer"}]}')
    sent = install_transport(monkeypatch, [response])
    original_send = llm._send_response
    def changed_after_dispatch(*args, **kwargs):
        result = original_send(*args, **kwargs)
        fake_llm["node_role"] = "cloud"
        return result
    monkeypatch.setattr(llm, "_send_response", changed_after_dispatch)
    with model_budget.scope(scope(profile)):
        answer, _ = llm.chat(BODY["messages"], model="glm-5.3-synthetic")
    assert answer == "paid answer" and len(sent) == 1
    attempt = rows(profile, "budget_attempt")[0]
    assert attempt["state"] == "settled" and attempt["response_complete"] == 1
    assert attempt["outcome"] == "full_result" and attempt["result_storage_state"] == "stored"
