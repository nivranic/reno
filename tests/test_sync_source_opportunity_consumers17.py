"""Real scheduling signals cross consumers without a second paid effect.

Only the external step/transport effects are replaced. The original Home17
core, worker, ledger, HTTP observations and scheduling signal use real code.
"""
from contextlib import nullcontext
import sqlite3

import pytest

from reno import llm, model_budget, pipeline, storage_admin, sync_agent, vlm
from reno.budget_contract import BudgetDeferred
from reno import sync_source_admission17 as admission, sync_source_binding17 as binding17
from reno.sync_source_opportunity17 import ReadmissionRequired17, consume_required17
from tests.test_sync_source_genesis17 import create, env, home17, live17
from tests.test_home17_model_admission import model_scope, request, response


def original(value):
    create(value)
    return binding17.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.remote.entry["item_id"])


def required(value, binding):
    with value.client._source_claim_broker.lock:
        value.client._source_claim_broker.latest.pop("source/continuity")
    with pytest.raises(ReadmissionRequired17) as caught:
        with admission.capture_current_admission_locked(profile=value.profile, store=value.store,
                client=value.client, item_id=binding.core["item_id"], binding=binding):
            pytest.fail("Missing real observation created a current execution view")
    assert caught.value.action == "observe"
    assert value.store._directory is None
    return caught.value


def consume(value, binding, signal):
    assert consume_required17(signal, profile=value.profile, store=value.store, client=value.client,
        item_id=binding.core["item_id"], binding=binding) == "observe"


def test_chat_preserves_real_signal_instead_of_wrapping_a_provider_failure(live17, monkeypatch):
    binding = original(live17)
    signal = required(live17, binding)
    called = []
    def transport(*args, **kwargs):
        called.append(args)
        raise signal
    monkeypatch.setattr(llm, "_post", transport)
    with pytest.raises(ReadmissionRequired17) as caught:
        llm.chat([{"role": "user", "content": "isolated scheduling flow"}], model="synthetic-paas")
    assert caught.value is signal and len(called) == 1
    consume(live17, binding, signal)


@pytest.mark.parametrize("stage", ["vision", "repair"])
def test_vision_preserves_real_signal_without_fallback_or_fake_observation(live17, monkeypatch, stage):
    binding = original(live17)
    signal = required(live17, binding)
    called = []
    def transport(*args, **kwargs):
        called.append(args)
        if stage == "repair" and len(called) == 1:
            return "not a JSON observation"
        raise signal
    monkeypatch.setattr(llm, "vision", transport)
    with pytest.raises(ReadmissionRequired17) as caught:
        vlm._observe_frame("isolated-frame.jpg")
    assert caught.value is signal and len(called) == (2 if stage == "repair" else 1)
    consume(live17, binding, signal)


def test_pipeline_keeps_the_video_step_incomplete_and_does_not_write_error_history(live17, monkeypatch):
    binding = original(live17)
    video_id = "opportunity17-video"
    with storage_admin.connection(live17.profile) as con:
        con.execute("INSERT INTO video_asset(video_id,title,status) VALUES(?,?,'imported')",
            (video_id, "isolated scheduling fixture"))
        con.commit()
    signal = required(live17, binding)
    def step(*args, **kwargs):
        raise signal
    monkeypatch.setattr(pipeline, "_active_steps", lambda: ["atomize"])
    monkeypatch.setattr(pipeline.atomize, "run", step)
    with pytest.raises(ReadmissionRequired17) as caught:
        pipeline.run_video(video_id)
    assert caught.value is signal
    with storage_admin.connection(live17.profile) as con:
        assert con.execute("SELECT status FROM video_asset WHERE video_id=?", (video_id,)).fetchone()[0] == "imported"
        assert con.execute("SELECT 1 FROM processing_run WHERE video_id=?", (video_id,)).fetchone() is None
        assert con.execute("SELECT 1 FROM meta WHERE key=?", ("step:" + video_id + ":atomize",)).fetchone() is None
    consume(live17, binding, signal)


def test_actual_cycle_reuses_paid_model_before_any_new_continuity_or_readmission(live17, monkeypatch):
    binding = original(live17)
    identity = model_scope(live17, binding)
    with model_budget.scope(identity):
        assert request(lambda payload: response()) == response()
    with sqlite3.connect(live17.profile.database) as con:
        paid_before = {table: con.execute("SELECT * FROM " + table).fetchall()
            for table in ("budget_operation", "budget_attempt")}
    with live17.client._source_claim_broker.lock:
        live17.client._source_claim_broker.latest.clear()
    cycle = sync_agent._Cycle(live17.store, live17.client, live17.source,
        sync_agent.settings(), live17.stop)
    def forbidden(*args, **kwargs):
        pytest.fail("Paid replay reached a fresh observer/readmit/provider effect")
    monkeypatch.setattr(cycle, "acquire", lambda: None)
    monkeypatch.setattr(cycle, "heartbeat", lambda: nullcontext())
    monkeypatch.setattr(cycle, "pull_events", lambda **kwargs: True)
    monkeypatch.setattr(cycle, "push_events", lambda: True)
    monkeypatch.setattr(cycle, "resume_source_control", lambda: None)
    monkeypatch.setattr(cycle, "resume_budget", lambda: None)
    monkeypatch.setattr(cycle, "deliver_no_resend", lambda: None)
    monkeypatch.setattr(cycle, "publish", lambda: None)
    monkeypatch.setattr(cycle, "needs_publication", lambda: False)
    monkeypatch.setattr(cycle, "_readmit_source17", forbidden)
    monkeypatch.setattr(live17.client, "source_continuity", forbidden)
    def process():
        with model_budget.scope(identity):
            assert request(forbidden) == response()
        return []
    monkeypatch.setattr(cycle, "process_claim", process)
    assert cycle.execute()["state"] == "idle"
    with sqlite3.connect(live17.profile.database) as con:
        assert {table: con.execute("SELECT * FROM " + table).fetchall()
            for table in paid_before} == paid_before
    assert live17.store.read()["source_execution"] == binding.core


def test_observation_refresh_keeps_a_present_pull_and_its_claim_bytes(live17, monkeypatch):
    binding = original(live17)
    cycle = sync_agent._Cycle(live17.store, live17.client, live17.source,
        sync_agent.settings(), live17.stop)
    broker = live17.client._source_claim_broker
    original_pull = broker.latest["pull"]
    original_request, original_response = original_pull.request, original_pull.response
    monkeypatch.setattr(cycle, "acquire", lambda: pytest.fail("Present heartbeat was replaced"))
    monkeypatch.setattr(cycle, "pull_events", lambda **kwargs: pytest.fail("Present pull was replaced"))
    cycle._observe_source17()
    assert broker.latest["pull"] is original_pull
    assert (original_pull.request, original_pull.response) == (original_request, original_response)
    assert live17.store.read()["source_execution"] == binding.core


def test_first_core_lazily_observes_real_continuity_before_original_identity_exists(live17, monkeypatch):
    from reno import source_resolution_budget
    assert live17.store.read()["source_execution"] is None
    broker = live17.client._source_claim_broker
    with broker.lock:
        broker.latest.pop("source/continuity")
    cycle = sync_agent._Cycle(live17.store, live17.client, live17.source,
        sync_agent.settings(), live17.stop)
    waiting = BudgetDeferred("manual_pause")
    def resolution(binding, **kwargs):
        assert live17.store.read()["source_execution"] == binding.core
        assert broker.latest["source/continuity"].call.frame.completed
        raise waiting
    def keep_wait(deferred):
        assert deferred is waiting
        raise deferred
    monkeypatch.setattr(source_resolution_budget, "resolve_url", resolution)
    monkeypatch.setattr(cycle, "defer_budget", keep_wait)
    with pytest.raises(BudgetDeferred) as caught:
        cycle.process_claim()
    assert caught.value is waiting
    state = live17.store.read()
    assert state["source_execution"] is not None
    assert state["source_execution"]["item_id"] == live17.remote.entry["item_id"]
    assert state["source_authority"]["origin"] is not None
    with sqlite3.connect(live17.profile.database) as con:
        assert con.execute("SELECT 1 FROM budget_operation").fetchone() is None
