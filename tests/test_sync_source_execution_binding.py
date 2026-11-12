"""Source authority requires an actual owned sync job and the saved item."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import threading
import uuid

import pytest

from reno import budget, config, db, storage_admin, sync_source_execution as authority, tasks, worker
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_sync_agent import install_import
from tests.conftest import released16_env as env
from tests.test_sync_budget_propagation import home_cycle


@contextmanager
def executing(value, *, owner=None):
    profile = value.storage_profile
    owner = owner or str(uuid.uuid4())
    job_id = tasks.submit_sync(profile=profile)
    assert tasks.acquire_worker(owner, profile=profile)
    con = db.connect(profile.database)
    try:
        job = tasks.claim(con, owner, profile=profile)
    finally:
        con.close()
    assert job["job_id"] == job_id
    try:
        with worker._execution(job, owner, threading.Event(), profile=profile):
            yield job, owner
    finally:
        tasks.release_worker(owner, path=profile.database)


def bind(value, entry=None):
    return authority.bind_execution(value.store, value.storage_profile,
        value.store.read()["claims"][0] if entry is None else entry,
        recovery_generation=value.knowledge_generation, recovery_stream=value.knowledge_stream)


def test_process_lock_without_original_job_cannot_create_source_authority(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        before = value.store.read()
        with pytest.raises(BudgetDeferred) as caught:
            bind(value)
        assert caught.value.reason == "unsupported_capability"
        assert value.store.read() == before


def test_binding_is_persisted_before_first_op_and_reentry_keeps_it(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        proof = bind(value)
        core = value.store.read()["source_execution"]
        assert core == proof.core
        assert (core["root_job_id"], core["continuation_id"], core["execution_generation"]) == (
            job["job_id"], job["continuation_id"], job["execution_generation"])
        assert bind(value).resolution_operation == proof.resolution_operation
        assert proof.resolution_operation.suboperation_key.startswith("home:" + proof.namespace + ":")
        assert value.store.read()["format_version"] == 3


def test_unsaved_item_cannot_be_installed_by_a_real_owner(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        before = value.store.read()
        entry = deepcopy(before["claims"][0])
        entry["url"] = "https://www.bilibili.com/video/BVchanged"
        with pytest.raises(budget.BudgetLedgerError):
            bind(value, entry)
        assert value.store.read() == before


def test_new_owner_and_new_cloud_claim_reuse_original_execution(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        profile = value.storage_profile
        with executing(value) as (job, owner):
            proof = bind(value)
            original = proof.resolution_operation
            assert tasks.defer(job["job_id"], owner, BudgetDeferred("daily_limit"), profile=profile)
        with pytest.raises(tasks.TaskError) as caught:
            tasks.resume(job["job_id"], profile=profile)
        assert caught.value.code == "NOT_FOUND"
        tasks.resume_sync(job["job_id"], profile=profile)
        entry = deepcopy(value.store.read()["claims"][0])
        entry["claim"]["lease_generation"] += 1
        entry["claim"]["lease_token"] = str(uuid.uuid4())
        value.store.update({"claims": [entry]})
        with executing(value) as (again, next_owner):
            assert again["job_id"] == job["job_id"] and next_owner != owner
            restored = bind(value)
            restored.check()
            assert restored.core == proof.core and restored.resolution_operation == original


def test_thread_context_cannot_forge_persisted_worker_lineage(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        proof = bind(value)
        before = value.store.read()["source_execution"]
        false_job = {**job, "continuation_id": str(uuid.uuid4())}
        with worker._execution(false_job, owner, threading.Event(), profile=value.storage_profile):
            with pytest.raises(tasks.TaskError) as caught:
                proof.check()
        assert caught.value.code == "TASK_OWNER_LOST"
        assert value.store.read()["source_execution"] == before


def test_changed_submitted_url_cannot_use_original_operation_identity(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        proof = bind(value)
        changed = replace(proof, submitted_url="https://www.bilibili.com/video/BVchanged")
        with pytest.raises(budget.BudgetLedgerError):
            changed.check()
        proof.check()


def test_receiver_restore_does_not_rebind_original_source_proof(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        proof = bind(value)
        before = proof.core
        control = deepcopy(value.store.read()["recovery"])
        control["generation"] += 1
        control["revision"] += 1
        value.store.observe_recovery(control)
        with pytest.raises(budget.BudgetLedgerError):
            proof.check()
        assert value.store.read()["source_execution"] == before


def test_changed_peer_cannot_borrow_source_namespace(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        proof = bind(value)
        before = proof.core
        peer = deepcopy(value.store.read()["binding"])
        peer["target_node_id"] = str(uuid.uuid4())
        with pytest.raises(SyncError) as caught:
            value.store.update({"binding": peer})
        assert caught.value.code == "SYNC_STATE_BINDING_MISMATCH"
        proof.check()
        assert proof.core == before


def test_stopped_worker_cannot_start_source_with_saved_binding(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        proof = bind(value)
        worker._current.stop.set()
        with pytest.raises(BudgetDeferred) as caught:
            proof.check()
        assert caught.value.reason == "manual_pause"


def test_fresh_peer_configuration_cannot_borrow_saved_binding(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        proof = bind(value)
        config.set_local({"sync_target_node_id": str(uuid.uuid4())})
        with pytest.raises(budget.BudgetLedgerError):
            proof.check()


def test_changed_configuration_cannot_install_a_new_source_authority(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        before = value.store.read()
        config.set_local({"sync_target_node_id": str(uuid.uuid4())})
        with pytest.raises(budget.BudgetLedgerError):
            bind(value)
        assert value.store.read() == before


def test_thread_kind_cannot_turn_a_real_judge_job_into_sync_authority(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value:
        profile = value.storage_profile
        tasks.submit("judge", profile=profile)
        owner = str(uuid.uuid4())
        assert tasks.acquire_worker(owner, profile=profile)
        con = db.connect(profile.database)
        try:
            job = tasks.claim(con, owner, profile=profile)
        finally:
            con.close()
        assert job["kind"] == "judge"
        before = value.store.read()
        try:
            with worker._execution({**job, "kind": "sync"}, owner, threading.Event(), profile=profile):
                with pytest.raises(tasks.TaskError) as caught:
                    bind(value)
            assert caught.value.code == "TASK_LINEAGE_UNAVAILABLE"
            assert value.store.read() == before
        finally:
            tasks.release_worker(owner, path=profile.database)


def test_sync_pause_and_resume_check_actual_same_owner(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, owner):
        proof = bind(value)
        tasks.pause_sync(job["job_id"], profile=value.storage_profile)
        with pytest.raises(BudgetDeferred) as caught:
            proof.check()
        assert caught.value.reason == "manual_pause"
        tasks.resume_sync(job["job_id"], profile=value.storage_profile)
        proof.check()
        assert worker.execution_context()["owner_id"] == owner


@pytest.mark.parametrize("payload", ["not-json", "null", "[]", '{"unsupported":true}'])
def test_corrupt_internal_sync_payload_cannot_be_resumed(env, monkeypatch, payload):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        profile = value.storage_profile
        with storage_admin.connection(profile) as con:
            con.execute("UPDATE job SET payload_json=? WHERE job_id=?", (payload, job["job_id"]))
            con.commit()
            before = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job["job_id"],)).fetchone())
        with pytest.raises(tasks.TaskError) as caught:
            tasks.resume_sync(job["job_id"], profile=profile)
        assert caught.value.code == "TASK_LINEAGE_UNAVAILABLE"
        with storage_admin.connection(profile) as con:
            assert dict(con.execute("SELECT * FROM job WHERE job_id=?", (job["job_id"],)).fetchone()) == before


def test_historical_binding_reads_original_facts_after_receiver_restore_but_cannot_dispatch(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        original = bind(value)
        control = deepcopy(value.store.read()["recovery"])
        control["generation"] += 1
        control["revision"] += 1
        value.store.observe_recovery(control)
        tasks.pause_sync(job["job_id"], profile=value.storage_profile)
        before = value.store.read()
        historical = authority.historical_binding(value.store, value.storage_profile)
        assert historical.core_json == original.core_json and historical.submitted_url == original.submitted_url
        assert historical.resolution_operation == original.resolution_operation
        assert value.store.read() == before
        with pytest.raises(BudgetDeferred) as caught:
            historical.check()
        assert caught.value.reason == "manual_pause"
        tasks.resume_sync(job["job_id"], profile=value.storage_profile)
        with pytest.raises(budget.BudgetLedgerError):
            historical.check()


@pytest.mark.parametrize("change", ["submitted_url", "peer", "owner", "stop", "profile"])
def test_historical_binding_requires_actual_original_owner_profile_and_source(env, monkeypatch, change):
    install_import(env, monkeypatch)
    # Undo the thread-local mutation before executing() restores its outer state.
    with home_cycle(env) as value, executing(value) as (job, _), monkeypatch.context() as patch:
        bind(value)
        if change == "submitted_url":
            claims = deepcopy(value.store.read()["claims"])
            claims[0]["url"] = "https://www.bilibili.com/video/BVchanged"
            value.store.update({"claims": claims})
        elif change == "peer":
            config.set_local({"sync_target_node_id": str(uuid.uuid4())})
        elif change == "owner":
            with storage_admin.connection(value.storage_profile) as con:
                con.execute("UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), job["job_id"]))
                con.commit()
        elif change == "stop":
            worker._current.stop.set()
        else:
            patch.setattr(worker._current, "profile", None)
        before = value.store.read()
        with pytest.raises((budget.BudgetLedgerError, tasks.TaskError, BudgetDeferred)):
            authority.historical_binding(value.store, value.storage_profile)
        assert value.store.read() == before


def test_historical_binding_cannot_borrow_state_store_without_original_profile(env, monkeypatch):
    from reno.sync_state import StateStore
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        bind(value)
        reader = StateStore(value.store.data_dir)
        with pytest.raises(budget.BudgetLedgerError):
            authority.historical_binding(reader, value.storage_profile)
