"""Actual sealed Home17 scheduling gaps; no DTO becomes fresh permission."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy, deepcopy
from dataclasses import replace
import uuid

import pytest

from reno import budget_writer_witness as sql, storage_lifecycle
from reno import sync_source_admission17 as admissions, sync_source_binding17 as bindings
from reno import sync_source_opportunity17 as opportunity, sync_source_claim as claims, tasks
from reno.sync_protocol import SyncError
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, create, sql_change
from tests.test_sync_source_admission17 import admitted
from tests.test_sync_source_restore_cycle17 import restored_origin17


def facts(value):
    create(value)
    return bindings.capture_home_binding_locked(profile=value.profile, store=value.store,
        item_id=value.remote.entry["item_id"])


def current(value, binding):
    return admissions.capture_current_admission_locked(profile=value.profile, store=value.store,
        client=value.client, item_id=binding.core["item_id"], binding=binding)


def signal(value, binding, action):
    with pytest.raises(opportunity.ReadmissionRequired17) as raised:
        with current(value, binding):
            pytest.fail("A missing current admission granted permission")
    assert type(raised.value) is opportunity.ReadmissionRequired17
    assert raised.value.action == action
    assert value.store._directory is None and not sql.in_write_window()
    assert storage_lifecycle.current_guard() is None
    return raised.value


def consume(value, binding, required, **changes):
    selectors = dict(profile=value.profile, store=value.store, client=value.client,
        item_id=binding.core["item_id"], binding=binding)
    selectors.update(changes)
    return opportunity.consume_required17(required, **selectors)


def empty_pull(value):
    value.remote.payloads["pull"]["claims"] = []
    value.client.pull(value.source, value.store.read()["lease"], claim_limit=0)


def expired_initial(value):
    changed = deepcopy(value.store.read()["claims"])
    changed[0]["lease_until"] = "2020-01-01T00:00:00.000Z"
    value.remote.payloads["pull"]["claims"] = changed
    value.client.pull(value.source, value.store.read()["lease"], claim_limit=1)
    value.store.update({"claims": changed})


@pytest.mark.parametrize("missing", ["source/continuity", "pull", "all"])
def test_missing_actual_observation_only_requests_locked_out_refresh(live17, missing):
    binding = facts(live17)
    broker = live17.client._source_claim_broker
    broker.latest.clear() if missing == "all" else broker.latest.pop(missing)
    before, calls = live17.store.read(), list(live17.remote.seen)
    required = signal(live17, binding, "observe")
    assert consume(live17, binding, required) == "observe"
    assert live17.store.read() == before and live17.remote.seen == calls
    with pytest.raises(SyncError):
        consume(live17, binding, required)


def test_real_zero_claim_pull_requires_targeted_readmit_without_fresh_grant(live17):
    binding = facts(live17)
    empty_pull(live17)
    required = signal(live17, binding, "readmit")
    assert consume(live17, binding, required) == "readmit"
    assert live17.store.read()["source_execution"] == binding.core
    assert live17.store.read()["source_admission"] is None


def test_genuinely_expired_complete_original_entry_requests_readmit(live17):
    binding = facts(live17)
    expired_initial(live17)
    assert live17.remote.payloads["heartbeat"]["renewed_claims"] == 1
    required = signal(live17, binding, "readmit")
    assert consume(live17, binding, required) == "readmit"


@pytest.mark.parametrize("change", ["claim", "url", "deadline", "attempts"])
def test_actual_wrong_pull_item_cannot_be_reclassified_as_expiry(live17, change):
    binding = facts(live17)
    changed = deepcopy(live17.store.read()["claims"])
    if change == "claim":
        changed[0]["claim"]["lease_token"] = str(uuid.uuid4())
        changed[0]["claim"]["lease_generation"] += 1
    elif change == "url":
        changed[0]["url"] = "https://www.bilibili.com/video/BV1WrongClaim"
    elif change == "deadline":
        changed[0]["lease_until"] = "2020-01-01T00:00:00.000Z"
    else:
        changed[0]["attempts"] += 1
    value = changed if change == "claim" else live17.store.read()["claims"]
    live17.remote.payloads["pull"]["claims"] = changed
    live17.client.pull(live17.source, live17.store.read()["lease"], claim_limit=1)
    if change == "claim":
        live17.store.update({"claims": value})
    with pytest.raises(SyncError) as raised:
        with current(live17, binding):
            pytest.fail("Wrong item became a scheduling signal")
    assert type(raised.value) is not opportunity.ReadmissionRequired17


def test_expired_rewindable_state_time_without_matching_http_entry_is_hard_rejected(live17):
    binding = facts(live17)
    changed = deepcopy(live17.store.read()["claims"])
    changed[0]["lease_until"] = "2020-01-01T00:00:00.000Z"
    live17.store.update({"claims": changed})
    with pytest.raises(SyncError) as raised:
        with current(live17, binding):
            pytest.fail("State time alone proved expiration")
    assert type(raised.value) is not opportunity.ReadmissionRequired17


@pytest.mark.parametrize("change", ["shallow_observation", "changed_snapshot", "copied_frame", "copied_client", "url"])
def test_closed_actual_frame_and_frozen_snapshot_are_required(live17, change):
    binding = facts(live17)
    broker = live17.client._source_claim_broker
    observation = broker.latest["pull"]
    if change == "shallow_observation":
        broker.latest["pull"] = copy(observation)
    elif change == "changed_snapshot":
        changed = observation.response
        changed["claims"][0]["lease_until"] = "2020-01-01T00:00:00.000Z"
        object.__setattr__(observation, "response_json", claims._encoded(changed))
    elif change == "copied_frame":
        object.__setattr__(observation.call, "frame", copy(observation.call.frame))
    elif change == "copied_client":
        live17.client = copy(live17.client)
    else:
        live17.client._url += "/changed"
    with pytest.raises(SyncError) as raised:
        with current(live17, binding):
            pytest.fail("Copied/changed provenance became a scheduling signal")
    assert type(raised.value) is not opportunity.ReadmissionRequired17


def test_public_response_mutation_does_not_change_the_frozen_actual_observation(live17):
    binding = facts(live17)
    latest = live17.client._source_claim_broker.latest
    latest["pull"].call.frame.decoded["claims"][0]["lease_until"] = "2020-01-01T00:00:00.000Z"
    with current(live17, binding) as view:
        assert view.kind == "original_core"


def test_signal_rejects_dto_constructor_copied_context_thread_and_wrong_consumer(live17):
    binding = facts(live17)
    with pytest.raises(SyncError):
        opportunity.ReadmissionRequired17(action="readmit", prepared={}, client=live17.client,
            fingerprint="", observations=(), seal=object())
    empty_pull(live17)
    required = signal(live17, binding, "readmit")
    with pytest.raises(SyncError):
        copy_context().run(consume, live17, binding, required)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(SyncError):
            executor.submit(consume, live17, binding, required).result()
    with pytest.raises(SyncError):
        consume(live17, binding, required, item_id=binding.core["item_id"] + 1)
    with pytest.raises(SyncError):
        consume(live17, binding, required, profile=replace(live17.profile,
            database=live17.profile.database.with_name("different-profile.db")))
    with live17.store.reference_operation():
        with pytest.raises(SyncError):
            consume(live17, binding, required)
    selected_profile = copy(live17.profile)
    assert selected_profile is not live17.profile and selected_profile.identity == live17.profile.identity
    assert consume(live17, binding, required, profile=selected_profile) == "readmit"


@pytest.mark.parametrize("change", ["state", "observation", "worker"])
def test_consume_reopens_and_strictly_rechecks_the_original_actual_binding(live17, change):
    binding = facts(live17)
    empty_pull(live17)
    required = signal(live17, binding, "readmit")
    if change == "state":
        live17.store.update({"seeded": not live17.store.read()["seeded"]})
    elif change == "observation":
        live17.client.heartbeat(live17.source, live17.remote.lease["session_id"])
    else:
        sql_change(live17, "UPDATE worker_lease SET lease_until=0")
    with pytest.raises((SyncError, tasks.TaskError)):
        consume(live17, binding, required)
    with pytest.raises(SyncError):
        consume(live17, binding, required)


@pytest.mark.parametrize("change", ["worker_expiry", "manual_pause"])
def test_missing_observations_do_not_hide_invalid_worker_or_pause(live17, change):
    binding = facts(live17)
    live17.client._source_claim_broker.latest.clear()
    if change == "worker_expiry":
        sql_change(live17, "UPDATE worker_lease SET lease_until=0")
    else:
        sql_change(live17, "UPDATE job SET manual_paused=1 WHERE job_id=?", (binding.core["root_job_id"],))
    with pytest.raises((SyncError, tasks.TaskError, BudgetDeferred)) as raised:
        with current(live17, binding):
            pytest.fail("Missing observation bypassed actual active worker")
    assert type(raised.value) is not opportunity.ReadmissionRequired17


@pytest.mark.parametrize("change", ["session", "fence"])
def test_old_admission_session_or_fence_needs_new_actual_readmit(live17, change):
    binding, _, _, prior = admitted(live17)
    if change == "session":
        lease = deepcopy(live17.remote.lease)
        lease.update(session_id=str(uuid.uuid4()), lease_token=str(uuid.uuid4()), generation=lease["generation"] + 1)
        live17.remote.payloads["heartbeat"]["lease"] = lease
        live17.client.heartbeat(live17.source, lease["session_id"])
        live17.store.update({"session_id": lease["session_id"], "lease": lease})
    else:
        recovery = deepcopy(live17.remote.recovery)
        recovery["revision"] += 1
        live17.remote.payloads["heartbeat"]["recovery"] = recovery
        live17.remote.payloads["pull"]["recovery"] = recovery
        live17.client.heartbeat(live17.source, live17.store.read()["session_id"])
        live17.store.update({"recovery": recovery})
    empty_pull(live17)
    required = signal(live17, binding, "readmit")
    assert consume(live17, binding, required) == "readmit"
    assert live17.store.read()["claims"][0]["claim"] == prior["claim"]


def test_actual_restore_without_admission_requires_readmit_not_fake_original_basis(restored_origin17):
    value = restored_origin17
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store, item_id=value.core["item_id"])
    required = signal(value, binding, "readmit")
    assert consume(value, binding, required) == "readmit"
    assert value.store.read()["source_execution"] == value.core
    assert value.store.read()["local_epoch"] > value.core["local_epoch"]


@pytest.mark.parametrize("restored_origin17", ["admitted_double"], indirect=True)
def test_prior_cross_epoch_admission_then_real_restore_requires_new_readmit(restored_origin17):
    value = restored_origin17
    intent, prior = value.admission_intent, value.admission_receipt["record"]["result"]
    empty_pull(value)
    before = value.store.read()
    assert before["local_epoch"] == value.core["local_epoch"] + 2
    assert before["source_admission"] == value.oldadm_ref
    binding = bindings.capture_home_binding_locked(profile=value.profile, store=value.store, item_id=value.core["item_id"])
    required = signal(value, binding, "readmit")
    assert consume(value, binding, required) == "readmit"
    assert value.store.read()["source_execution"] == value.core
    assert value.store.read()["source_admission"]["admission_id"] == prior["admission_id"]
    assert intent["record"]["request"]["current_home_epoch"] == before["local_epoch"] - 1
