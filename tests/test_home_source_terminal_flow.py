"""Original Home paid failure reaches real Cloud SQL through the wire client.

The HTTP transport is synthetic; local jobs, ledgers, state/history, Cloud
queue/session and permanent receipt transactions are actual isolated SQLite.
"""
from copy import deepcopy
from datetime import timedelta
import json
import uuid

import httpx
import pytest

from reno import config, db, source_terminal, sync_access, sync_agent, sync_queue, sync_source_control, tasks
from reno.budget_contract import BudgetDeferred
from reno.sync_client import SyncClient, SyncClientError
from reno.sync_source_execution import historical_binding
from tests.test_sync_agent import env  # noqa: F401
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import bind, executing
from tests.test_source_terminal import failed_source


@pytest.mark.parametrize("lost_reply", [False, True])
def test_original_paid_failure_commits_once_and_replays_before_exact_local_clear(env, monkeypatch, tmp_path, lost_reply):
    cloud_path = tmp_path / "cloud" / "reno.db"
    db.initialize(cloud_path)
    con = db.connect(cloud_path)
    source, _ = sync_agent._local()
    target = sync_access.identity(con)
    env.target = target
    env.recovery["stream"]["library_id"] = target["library_id"]
    config.set_local({"budget_enabled": True, "sync_target_library_id": target["library_id"],
                      "sync_target_node_id": target["node_id"]})
    sync_queue.bind_peer(con, source)
    sync_queue.enqueue(con, ["https://b23.tv/original-fixture"])
    con.commit()

    def heartbeat(actual_source, session_id, lease=None):
        result = sync_queue.heartbeat(con, actual_source, session_id, lease=lease)
        if lease is None:
            env.claims = sync_queue.claim(con, actual_source, result["lease"])
        con.commit()
        return result

    monkeypatch.setattr(env, "heartbeat", heartbeat)
    sent = []
    def transport(wire):
        assert wire.url.path == "/api/sync/push"
        sent.append(wire.content)
        result = sync_source_control.apply_request(con, json.loads(wire.content))
        con.commit()
        if lost_reply and len(sent) == 1:
            raise httpx.ReadError("synthetic reply lost after real Cloud commit")
        return httpx.Response(200, json=result)

    client = SyncClient("https://cloud.example.test", token_provider=lambda: "artificial-credential",
                        transport=httpx.MockTransport(transport))
    try:
        with home_cycle(env) as cycle:
            profile = cycle.storage_profile
            with executing(cycle) as (job, owner):
                binding = bind(cycle)
                _, attempted, _ = failed_source(binding)
                proof = source_terminal.known_failure(binding)
                core = binding.core
                before = cycle.store.read()
                body = {"kind": "source_fail", "request_id": str(uuid.uuid4()), "source": source,
                    "target": {key: target[key] for key in ("library_id", "node_id")},
                    "lease": deepcopy(cycle.lease()), "claim": deepcopy(before["claims"][0]["claim"]),
                    "recovery_generation": core["recovery_generation"], "recovery_stream": core["recovery_stream"],
                    "core_sha256": proof.core_sha256, "submitted_url_sha256": core["submitted_url_sha256"],
                    "failure": proof.public()}
                cycle.store.enable_source_control()
                cycle.store.install_source_control_request(body)
                if lost_reply:
                    with pytest.raises(SyncClientError) as caught:
                        client.source_control(body)
                    assert caught.value.code == "SYNC_CLIENT_NETWORK"
                    assert cycle.store.assert_source_control_pending()["receipt"] is None
                    assert tasks.defer(job["job_id"], owner, BudgetDeferred("indeterminate",
                        resume_at=sync_agent._now() + timedelta(seconds=30)), profile=profile)
                else:
                    response = client.source_control(body)
                    cycle.store.record_source_control_receipt(body["request_id"], response)
                    tasks.pause_sync(job["job_id"], profile=profile)
                    # Pause changes fresh admission, not an already paid normal
                    # failure's reader authority or its permanent outcome.
                    restored = historical_binding(cycle.store, profile)
                    source_terminal.revalidate_failure(restored, proof)
                    cycle.store.complete_source_failure(core, cycle.store.assert_source_control_pending(),
                        effect_guard=lambda: source_terminal.failure_clear_guard(restored, proof))
                    assert tasks.finish(job["job_id"], owner, result={"state": "source_failed"}, profile=profile)
            assert con.execute("SELECT status,attempts FROM sync_queue").fetchone()[:] == ("dead", 1)
            assert con.execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 1
            if lost_reply:
                tasks.resume_sync(job["job_id"], profile=profile)
                # Expiry must not force replacement of the original wire body.
                con.execute("UPDATE sync_session SET lease_until='2000-01-01T00:00:00.000Z'")
                con.commit()
                with executing(cycle) as (again, owner):
                    assert again["job_id"] == job["job_id"]
                    restored = historical_binding(cycle.store, profile)
                    source_terminal.revalidate_failure(restored, proof)
                    pending = cycle.store.assert_source_control_pending()
                    response = client.source_control(pending["request"])
                    assert response["replayed"] is True
                    cycle.store.record_source_control_receipt(body["request_id"], response)
                    source_terminal.revalidate_failure(restored, proof)
                    cycle.store.complete_source_failure(core, cycle.store.assert_source_control_pending(),
                        effect_guard=lambda: source_terminal.failure_clear_guard(restored, proof))
                    assert tasks.finish(again["job_id"], owner, result={"state": "source_failed"}, profile=profile)
                assert sent == [sent[0], sent[0]]
            assert all(json.loads(value) == body for value in sent)
            after = cycle.store.read()
            assert after["source_execution"] is after["source_control_pending"] is None
            assert {key: after[key] for key in ("claims", "work", "pending_publication", "uploaded_media")} == {
                key: before[key] for key in ("claims", "work", "pending_publication", "uploaded_media")}
            history = list((profile.database.parent / "sync-work-history").glob("*.json"))
            assert len(history) == 1
            recorded = json.loads(history[0].read_bytes())
            assert recorded["format_version"] == 4 and recorded["reason"] == "source_failed"
            assert recorded["source_terminal"]["proof"]["request"] == body
            assert recorded["source_terminal"]["proof"]["receipt"]["status"] == "dead"
            assert source_terminal.revalidate_failure(restored, proof).public()["attempt_id"] == attempted.attempt_id
            assert tasks.submit_sync(profile=profile) != job["job_id"]
            assert con.execute("SELECT COUNT(*) FROM sync_queue_source_receipt").fetchone()[0] == 1
            assert not env.publications
    finally:
        con.close()
