"""Successful RPCs permanently stop the UUID before its local ack is advanced."""
from copy import deepcopy
import threading

import pytest

from reno import config, db, storage_admin, sync_agent as agent, sync_state, tasks
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_sync_agent import env, add_video, cycle, install_import


def local_cycle(remote, store):
    source, _ = agent._local()
    value = agent._Cycle(store, remote, source, agent.settings(), threading.Event())
    control = store.read()["recovery"]
    value.knowledge_generation, value.knowledge_stream = control["generation"], control["stream"]
    return value


def assert_no_content(calls):
    assert not any(call == "publish" or isinstance(call, tuple) and call[0] in {"prepare", "verify", "media"}
                   for call in calls)


def interrupt_ack(monkeypatch):
    original = StateStore.acknowledge_publication
    failed = False

    def ack(store, *args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("controlled interruption after permanent terminal")
        return original(store, *args, **kwargs)

    monkeypatch.setattr(StateStore, "acknowledge_publication", ack)


def test_successful_publication_records_terminal_and_cannot_reinstall_uuid(env):
    add_video()
    assert cycle(env)["state"] == "idle"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    publication_id = env.publications[0]["snapshot"]["publication_id"]
    request = store.publication_no_resend_request(publication_id)
    assert request["reason"] == "published_receipt"
    assert store.no_resend_receipt(request["operation_id"])["state"] == "published"
    assert store.read()["pending_publication"] is None
    with pytest.raises(SyncError) as caught:
        store.assert_sendable(publication_id)
    assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    metadata = store.find_publication(publication_id, audit=True)
    with pytest.raises(SyncError) as caught:
        store.install_publication(metadata, recovery_generation=0, recovery_stream=request["binding"]["recovery_stream"])
    assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"


@pytest.mark.parametrize("held", [False, True])
def test_terminal_ack_gap_finishes_from_stored_receipt_before_held_branch(env, monkeypatch, held):
    install_import(env, monkeypatch)
    interrupt_ack(monkeypatch)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    old = store.read()
    request = store.publication_no_resend_request(old["pending_publication"]["publication_id"])
    assert store.no_resend_receipt(request["operation_id"])["state"] == "published"
    assert old["work"] and old["work"]["source_mapping_generation"]
    monkeypatch.setattr(env, "publication_receipt", lambda *a, **k: pytest.fail("committed exact receipt needs no network query"), raising=False)
    monkeypatch.setattr(env, "abandon_uploads", lambda *a, **k: pytest.fail("published batch cannot be abandoned"), raising=False)
    env.calls.clear()
    if held:
        env.recovery.update(state="held", generation=1, revision=1)
    assert cycle(env)["state"] == ("recovery_held" if held else "idle")
    assert_no_content(env.calls)
    current = store.read()
    assert current["pending_publication"] is None
    assert current["applied_recovery_generation"] == 0
    assert current["work"] == old["work"] if held else current["work"] is None
    assert store.publication_no_resend_request(request["metadata"]["publication_id"])["operation_id"] == request["operation_id"]


@pytest.mark.parametrize("kind", ["publications", "receipts"])
def test_reserved_terminal_repair_queries_only_original_receipt_and_never_abandons(env, monkeypatch, kind):
    add_video()
    original_write = sync_state._write
    original_push = env.push_snapshot
    receipts = []

    def push(*args, **kwargs):
        value = original_push(*args, **kwargs)
        receipts.append(deepcopy(value))
        return value

    def crash(directory, name, value, **kwargs):
        if directory.path.name == kind and name.endswith(".json"):
            raise OSError("controlled reserved terminal interruption")
        return original_write(directory, name, value, **kwargs)

    monkeypatch.setattr(env, "push_snapshot", push)
    monkeypatch.setattr(sync_state, "_write", crash)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    metadata = store.read()["pending_publication"]
    request = store.publication_no_resend_request(metadata["publication_id"], allow_incomplete=True)
    operation_id = request["operation_id"]
    monkeypatch.setattr(sync_state, "_write", original_write)
    queries = []

    def query(source, lease, *, binding, payload_sha256):
        queries.append((binding, payload_sha256))
        return {"binding": binding, "payload_sha256": payload_sha256, "state": "published",
                "receipt": {**receipts[0], "replayed": True}}

    monkeypatch.setattr(env, "publication_receipt", query, raising=False)
    monkeypatch.setattr(env, "abandon_uploads", lambda *a, **k: pytest.fail("successful publication cannot be abandoned"), raising=False)
    env.calls.clear()
    assert cycle(env)["state"] == "idle"
    assert_no_content(env.calls)
    assert queries == ([(request["binding"], request["payload_sha256"])] if kind == "publications" else [])
    assert store.publication_no_resend_request(metadata["publication_id"])["operation_id"] == operation_id
    assert store.read()["pending_publication"] is None
    assert store.no_resend_receipt(operation_id)["state"] == "published"


@pytest.mark.parametrize("actual_epoch_change", [False, True])
def test_original_terminal_cannot_advance_another_stream_or_actual_source_epoch(env, monkeypatch, actual_epoch_change):
    add_video()
    interrupt_ack(monkeypatch)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    request = store.publication_no_resend_request(store.read()["pending_publication"]["publication_id"])
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        if actual_epoch_change:
            con = db.connect()
            con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
            con.commit()
            con.close()
            assert store.read()["local_epoch"] == 1  # Old restored state cannot stand in for source SQL.
        else:
            control = store.read()["recovery"]
            store.update({"recovery": {**control, "stream": {**control["stream"], "library_epoch": 2},
                                        "generation": 8, "revision": 8}, "applied_recovery_generation": 7})
        before = store.read()["applied_recovery_generation"]
        value = local_cycle(env, store)
        assert value._complete_published(request) is False
        assert store.read()["pending_publication"] is None
        assert store.read()["applied_recovery_generation"] == before
        assert store.read()["published_version"] is None


def test_claimless_recovery_terminal_keeps_existing_generation_work(env, monkeypatch):
    install_import(env, monkeypatch)
    env.fail_publish = True
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    old_work = deepcopy(store.read()["work"])
    env.recovery.update(state="resume_pending", generation=1, revision=1)
    assert cycle(env)["state"] == "idle"
    latest = env.publications[-1]
    assert latest["claims"] == []
    assert store.read()["work"]["source_mapping_generation"] == old_work["source_mapping_generation"]
    request = store.publication_no_resend_request(latest["snapshot"]["publication_id"])
    assert request["reason"] == "published_receipt" and store.no_resend_receipt(request["operation_id"])["state"] == "published"


def test_explicit_completed_terminal_resume_closes_original_ack_without_network(env, monkeypatch):
    add_video()
    interrupt_ack(monkeypatch)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    request = store.publication_no_resend_request(store.read()["pending_publication"]["publication_id"])
    config.set_local({"sync_enabled": False})
    env.calls.clear()
    monkeypatch.setattr(env, "publication_receipt", lambda *a, **k: pytest.fail("saved receipt needs no query"), raising=False)
    with tasks.process_lock() as acquired:
        assert acquired
        assert agent.resume_no_resend(request["operation_id"], client=env) == {
            "operation_id": request["operation_id"], "state": "published"}
    assert env.calls == []
    state = store.read()
    assert state["pending_publication"] is None
    assert state["published_version"] == request["binding"]["source_version"]
    assert state["applied_recovery_generation"] == request["binding"]["recovery_generation"]
