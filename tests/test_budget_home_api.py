"""Released16 Home controls retain the original State3/4 sync continuation.

These checks exercise the actual old producer for its complete owned lifetime.
Current17 genesis/admission controls have separate ordinary-release coverage.
"""
from copy import deepcopy
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from reno import budget, config, db, storage_admin, tasks, worker
from reno.budget_contract import BudgetDeferred
from tests.test_sync_agent import env as original_env, install_import
from tests.test_sync_budget_propagation import home_cycle, raw_job
from tests.test_sync_source_execution_binding import bind, executing
from tests.schema_fixtures import released16


@pytest.fixture(autouse=True)
def released_home16(monkeypatch):
    # State3/4 bind_execution is the original released16 producer, rather than
    # a permitted way to create a new source identity on a current17 node.
    # Keep versions/descriptor fixed through initialization, every real owner,
    # effect, and teardown; no current17 capability or validator is weakened.
    with released16(monkeypatch):
        yield


@pytest.fixture
def env(released_home16, tmp_path, monkeypatch):
    yield from original_env.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def client(released_home16, monkeypatch):
    from app.main import create_app
    monkeypatch.setattr(worker, "ensure_running", lambda: None)
    value = TestClient(create_app())
    yield value
    value.close()


def test_pause_preserves_running_paid_owner_and_resume_uses_original_wait(client, env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle:
        profile = cycle.storage_profile
        with executing(cycle) as (job, owner):
            proof = bind(cycle)
            original = deepcopy(cycle.store.read()["source_execution"])
            settings = client.get("/api/budget")
            assert settings.status_code == 200
            home = settings.json()["home"]
            assert home["available"] is True
            assert home["task"]["job_id"] == job["job_id"]
            assert home["task"]["can_pause"] is True
            before = raw_job(profile, job["job_id"])
            paused = client.post("/api/budget/home/pause", json={"expected_control_revision": home["control_revision"]})
            assert paused.status_code == 202
            after = raw_job(profile, job["job_id"])
            assert after["manual_paused"] == 1
            assert (after["owner_id"], after["lease_until"], after["continuation_id"], after["execution_generation"]) == (
                before["owner_id"], before["lease_until"], before["continuation_id"], before["execution_generation"])
            assert cycle.store.read()["source_execution"] == original
            assert "binding" not in paused.text and "lease_token" not in paused.text
            assert "synthetic" not in paused.text
            assert tasks.defer(job["job_id"], owner, BudgetDeferred("manual_pause"), profile=profile)
        current = client.get("/api/budget").json()["home"]
        resumed = client.post("/api/budget/home/resume", json={"expected_control_revision": current["control_revision"]})
        assert resumed.status_code == 202
        restored = raw_job(profile, job["job_id"])
        assert restored["manual_paused"] == 0 and restored["wait_mode"] == "none"
        assert (restored["continuation_id"], restored["execution_generation"]) == (
            job["continuation_id"], job["execution_generation"])
        assert cycle.store.read()["source_execution"] == original
        assert cycle.store.assert_source_execution() == proof.core


def test_stale_control_revision_does_not_unpause_or_change_the_original_task(client, env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        bind(cycle)
        original = client.get("/api/budget").json()["home"]
        tasks.pause_sync(job["job_id"], profile=cycle.storage_profile)
        before = raw_job(cycle.storage_profile, job["job_id"])
        response = client.post("/api/budget/home/resume", json={"expected_control_revision": original["control_revision"]})
        assert response.status_code == 409
        assert response.json()["code"] == "BUDGET_HOME_CONTROL_CHANGED"
        assert raw_job(cycle.storage_profile, job["job_id"]) == before


def test_guard_checks_again_inside_effect_transaction_before_changing_pause(client, env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        bind(cycle)
        original = client.get("/api/budget").json()["home"]
        actual = tasks.pause_sync
        def raced(job_id, **kwargs):
            # A peer change after selector but before SQL effect must be seen
            # by the dedicated writer guard, not accepted as a new task choice.
            config.set_local({"sync_target_node_id": str(uuid.uuid4())})
            return actual(job_id, **kwargs)
        monkeypatch.setattr(tasks, "pause_sync", raced)
        before = raw_job(cycle.storage_profile, job["job_id"])
        response = client.post("/api/budget/home/pause", json={"expected_control_revision": original["control_revision"]})
        assert response.status_code == 409
        assert raw_job(cycle.storage_profile, job["job_id"]) == before


def test_unknown_ledger_attempt_is_preserved_by_manual_resume(client, env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle:
        profile = cycle.storage_profile
        with executing(cycle) as (job, owner):
            bind(cycle)
            operation = budget.OperationSpec(job["continuation_id"], job["execution_generation"],
                "model", "synthetic-budget-home-api", "a" * 64, job["job_id"])
            reservation = budget.reserve(operation, budget.AttemptSpec("b" * 64, 1, "paas_v4", 100), profile=profile)
            budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256, str(uuid.uuid4()),
                expected_revision=reservation.revision, profile=profile)
            budget.settle(reservation.attempt_id, "b" * 64, profile=profile, response_complete=False)
            assert tasks.defer(job["job_id"], owner, BudgetDeferred("indeterminate", operation_id=reservation.operation_id), profile=profile)
        with db.connect(profile.database) as con:
            before = con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?", (reservation.attempt_id,)).fetchone()
        home = client.get("/api/budget").json()["home"]
        response = client.post("/api/budget/home/resume", json={"expected_control_revision": home["control_revision"]})
        assert response.status_code == 202
        with db.connect(profile.database) as con:
            after = con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?", (reservation.attempt_id,)).fetchone()
        assert tuple(after) == tuple(before) and after["state"] == "indeterminate"


@pytest.mark.parametrize("change", ["peer", "node", "lineage"])
def test_unmatched_current_identity_blocks_home_controls_without_task_effect(client, env, monkeypatch, change):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        bind(cycle)
        original = client.get("/api/budget").json()["home"]
        if change == "peer":
            config.set_local({"sync_target_node_id": str(uuid.uuid4())})
        elif change == "node":
            path = cycle.storage_profile.database.parent / "node-identity.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["instance_id"] = str(uuid.uuid4())
            path.write_text(json.dumps(value), encoding="utf-8")
        else:
            with db.connect(cycle.storage_profile.database) as con:
                con.execute("UPDATE job SET continuation_id=? WHERE job_id=?", (str(uuid.uuid4()), job["job_id"]))
        before = raw_job(cycle.storage_profile, job["job_id"])
        response = client.post("/api/budget/home/pause", json={"expected_control_revision": original["control_revision"]})
        assert response.status_code == 409
        assert raw_job(cycle.storage_profile, job["job_id"]) == before
        assert "source_execution" not in response.text and "lease_token" not in response.text


@pytest.mark.parametrize("body", [{}, {"job_id": 1}, {"expected_control_revision": "0" * 64, "core": {}},
    {"expected_control_revision": True}])
def test_control_body_is_closed_and_cannot_submit_a_job_or_private_body(client, body):
    response = client.post("/api/budget/home/pause", json=body)
    assert response.status_code == 400
    assert response.json()["code"] == "BUDGET_HOME_CONTROL_INVALID"


def test_cloud_cannot_operate_home_control_with_even_a_valid_looking_revision(client):
    config.set_local({"node_role": "cloud"})
    response = client.post("/api/budget/home/resume", json={"expected_control_revision": "0" * 64})
    assert response.status_code == 403
    assert response.json()["code"] == "BUDGET_HOME_CONTROL_ROLE_FORBIDDEN"


def test_state4_pending_failure_controls_keep_original_request_and_receipt_delivery_state(client, env, monkeypatch):
    from tests.test_sync_source_control_agent import original_failure, failure_transport
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        binding, failure, _ = original_failure(cycle)
        calls = failure_transport(cycle, monkeypatch, lose_reply=True)
        with pytest.raises(BudgetDeferred):
            cycle.fail_source(binding, failure)
        before = cycle.store.read()
        assert before["format_version"] == 4 and before["source_control_pending"] is not None
        snapshot = client.get("/api/budget")
        assert snapshot.status_code == 200
        assert snapshot.json()["capabilities"]["home_control"] is True
        assert not any(value in snapshot.text for value in ("source_control_pending", "lease_token", "settlement_sha256", binding.submitted_url))
        home = snapshot.json()["home"]
        paused = client.post("/api/budget/home/pause", json={"expected_control_revision": home["control_revision"]})
        assert paused.status_code == 202
        resumed = client.post("/api/budget/home/resume", json={"expected_control_revision": paused.json()["control_revision"]})
        assert resumed.status_code == 202
        after = cycle.store.read()
        assert after["source_execution"] == before["source_execution"]
        assert after["source_control_pending"] == before["source_control_pending"]
        assert after["budget_pending"] == before["budget_pending"]
        assert len(calls) == 1
