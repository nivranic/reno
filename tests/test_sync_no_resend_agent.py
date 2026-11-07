"""Durable sender gates and explicit terminal delivery in fresh artificial homes."""
from copy import deepcopy
import json
import threading
import uuid

import httpx
import pytest

from reno import config, storage_admin, storage_lifecycle, sync_agent as agent, tasks
from reno.sync_client import SyncClientError
from reno.sync_client import SyncClient
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_sync_agent import env, add_video, add_event, cycle, install_import


@pytest.fixture
def pending(env):
    add_video()
    env.fail_publish = True
    assert cycle(env)["error_code"] == "SYNC_CLIENT_NETWORK"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    metadata = store.read()["pending_publication"]
    env.calls.clear()
    return env, store, metadata


def authorize(store, metadata):
    operation_id = str(uuid.uuid4())
    preview = store.preview_abandon(metadata["publication_id"])
    request = store.commit_no_resend(metadata["publication_id"], expected_digest=preview["digest"], operation_id=operation_id)
    return operation_id, request


def local_cycle(remote, store):
    source, _ = agent._local()
    value = agent._Cycle(store, remote, source, agent.settings(delivery_only=True), threading.Event())
    recovery = store.read()["recovery"]
    value.knowledge_generation, value.knowledge_stream = recovery["generation"], recovery["stream"]
    return value


def install_abandon(remote, store, monkeypatch, *, fault=None):
    requests = []

    def abandon(source, lease, *, binding, operation_id):
        assert storage_lifecycle.current_guard() is None
        # The very StateStore used by this cycle remains available to heartbeat.
        assert not store._guard._is_owned()
        assert store.read()["pending_publication"] is None
        requests.append((deepcopy(binding), operation_id))
        if fault:
            fault()
        return {"binding": deepcopy(binding), "operation_id": operation_id, "state": "abandoned"}

    monkeypatch.setattr(remote, "abandon_uploads", abandon, raising=False)
    return requests


@pytest.mark.parametrize("entry", ["publish", "freeze", "resume_attempt"])
def test_restored_v1_state_cannot_reinstall_or_send_marked_uuid(pending, monkeypatch, entry):
    remote, store, metadata = pending
    old = store.read()
    if entry == "resume_attempt":
        envelope = store.read_publication(metadata)
        old["pending_publication"] = None
        old["recovery_attempt"] = {"generation": old["recovery"]["generation"], "stream": old["recovery"]["stream"],
            "publication_id": metadata["publication_id"], "version": envelope["snapshot"]["version"]}
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        authorize(store, metadata)
        # Supported restoration can bring back an older state/outbox, never the
        # permanent evidence. This restores exactly the artificial old bytes.
        (config.DATA / "sync-state.json").write_text(json.dumps(old), encoding="utf-8")
        value = local_cycle(remote, store)
        monkeypatch.setattr(value, "snapshot", lambda *a, **k: pytest.fail("marked freeze must stop before media proof"))
        with pytest.raises(SyncError) as caught:
            if entry == "publish":
                value.publish()
            elif entry == "freeze":
                value.freeze([], publication_id=metadata["publication_id"])
            else:
                value.resume_publication()
        assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    assert not remote.calls


def test_restored_marked_source_pointer_is_preserved_by_an_independent_bootstrap(pending, monkeypatch):
    remote, store, metadata = pending
    old = store.read()
    outbox = (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        operation_id, original = authorize(store, metadata)
        (config.DATA / "sync-state.json").write_text(json.dumps(old), encoding="utf-8")
        value = local_cycle(remote, store)
        monkeypatch.setattr(value, "advance_work", lambda *a: pytest.fail("bootstrap cannot advance the original source"))
        monkeypatch.setattr(value, "process_claim", lambda *a: pytest.fail("bootstrap cannot process the original source"))
        value.resume_publication()
        assert store.read()["pending_publication"] == metadata
        assert store.read()["recovery_attempt"] is None
        assert store.no_resend_request(operation_id) == original
        assert store.no_resend_receipt(operation_id) is None
        assert (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == outbox
        with pytest.raises(SyncError) as caught:
            store.assert_sendable(metadata["publication_id"])
        assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    assert remote.publications[-1]["snapshot"]["publication_id"] != metadata["publication_id"]
    assert remote.publications[-1]["claims"] == []
    assert all(call[1]["publication_id"] != metadata["publication_id"] for call in remote.calls
               if isinstance(call, tuple) and call[0] in {"prepare", "verify"})


@pytest.mark.parametrize("edge", ["after_prepare", "verify_callback", "upload_callback", "before_push"])
def test_publication_checks_permanent_gate_at_each_transfer_boundary(pending, monkeypatch, edge):
    remote, store, metadata = pending
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        value = local_cycle(remote, store)
        original_prepare, original_verify, original_upload = remote.prepare_uploads, remote.verify_upload, remote.upload_media

        def prepare(*args, **kwargs):
            result = original_prepare(*args, **kwargs)
            if edge == "after_prepare":
                authorize(store, metadata)
            return result

        def verify(*args, **kwargs):
            if edge == "verify_callback":
                authorize(store, metadata)
                kwargs["check_cancelled"]()
            if edge == "before_push":
                result = original_verify(*args, **kwargs)
                authorize(store, metadata)
                return {**result, "stored": True}
            # Force upload, even though the first artificial lost response left
            # remote bytes, to exercise the actual SH-protected callback.
            return {**original_verify(*args, **kwargs), "stored": False}

        def upload(*args, **kwargs):
            if edge == "upload_callback":
                assert storage_lifecycle.current_guard() is not None
                authorize(store, metadata)
                kwargs["check_cancelled"]()
            return original_upload(*args, **kwargs)

        monkeypatch.setattr(remote, "prepare_uploads", prepare)
        monkeypatch.setattr(remote, "verify_upload", verify)
        monkeypatch.setattr(remote, "upload_media", upload)
        with pytest.raises(SyncError) as caught:
            value.publish()
        assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    assert "publish" not in remote.calls
    if edge in {"after_prepare", "verify_callback"}:
        assert not any(isinstance(call, tuple) and call[0] == "media" for call in remote.calls)


def test_explicit_resume_requires_worker_lock_without_network(pending):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata)
    with pytest.raises(SyncError) as caught:
        agent.resume_no_resend(operation_id, client=remote)
    assert caught.value.code == "SYNC_WORKER_LOCK_REQUIRED"
    assert not remote.calls


def test_explicit_delivery_works_disabled_and_never_pulls_or_publishes(pending, monkeypatch):
    remote, store, metadata = pending
    with tasks.process_lock() as acquired:
        assert acquired
        operation_id, request = authorize(store, metadata)
        requests = install_abandon(remote, store, monkeypatch)
        config.set_local({"sync_enabled": False})
        before = store.read()
        remote.epoch += 1  # A terminal retry must not archive old work for this.
        assert agent.resume_no_resend(operation_id, client=remote) == {"operation_id": operation_id, "state": "abandoned"}
        after = store.read()
        assert after["recovery"] == before["recovery"]
        assert after["applied_recovery_generation"] == before["applied_recovery_generation"]
        assert after["claims"] == before["claims"] and after["work"] == before["work"]
        assert requests == [(request["binding"], operation_id)]
        assert store.no_resend_receipt(operation_id)["state"] == "abandoned"
        remote.calls.clear()
        assert agent.resume_no_resend(operation_id, client=remote) == {"operation_id": operation_id, "state": "abandoned"}
        assert not remote.calls  # Stable original receipt replay is entirely local.
    assert not (config.DATA / "sync-work-history").exists()


def test_lost_abandon_reply_retries_same_authorization_without_resending(pending, monkeypatch):
    remote, store, metadata = pending
    with tasks.process_lock() as acquired:
        assert acquired
        operation_id, request = authorize(store, metadata)
        fault = iter((True, False))

        def lose_once():
            if next(fault):
                raise SyncClientError("NETWORK")

        requests = install_abandon(remote, store, monkeypatch, fault=lose_once)
        with pytest.raises(SyncError) as caught:
            agent.resume_no_resend(operation_id, client=remote)
        assert caught.value.code == "SYNC_CLIENT_NETWORK"
        assert store.no_resend_receipt(operation_id) is None
        with pytest.raises(SyncError) as caught:
            store.assert_sendable(metadata["publication_id"])
        assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"
        assert agent.resume_no_resend(operation_id, client=remote)["state"] == "abandoned"
        assert requests == [(request["binding"], operation_id)] * 2
    assert all(call == "handshake" or isinstance(call, tuple) and call[0] == "heartbeat" for call in remote.calls)


def test_delivery_expired_lease_keeps_actual_claim_work_and_old_recovery(env, monkeypatch):
    install_import(env, monkeypatch)
    env.fail_publish = True
    assert cycle(env)["error_code"] == "SYNC_CLIENT_NETWORK"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    metadata = store.read()["pending_publication"]
    with tasks.process_lock() as acquired:
        assert acquired
        operation_id, _ = authorize(store, metadata)
        before = store.read()
        assert before["claims"] and before["work"] and before["work"]["phase"] == "reported"
        install_abandon(env, store, monkeypatch)
        heartbeat = env.heartbeat
        renewed = []

        def expired(source, session_id, lease=None):
            renewed.append(deepcopy(lease))
            if lease is not None:
                raise SyncError("expired", "SYNC_LEASE_EXPIRED", 409)
            return heartbeat(source, session_id, lease=None)

        monkeypatch.setattr(env, "heartbeat", expired)
        config.set_local({"sync_enabled": False})
        env.epoch += 1
        assert agent.resume_no_resend(operation_id, client=env)["state"] == "abandoned"
        after = store.read()
        assert renewed == [before["lease"], None]
        assert after["claims"] == before["claims"] and after["work"] == before["work"]
        assert after["recovery"] == before["recovery"]
        assert after["applied_recovery_generation"] == before["applied_recovery_generation"]
    assert not (config.DATA / "sync-work-history").exists()


@pytest.mark.parametrize("interrupted_kind", ["publications", "receipts"])
def test_explicit_resume_repairs_only_original_reserved_local_evidence(pending, monkeypatch, interrupted_kind):
    remote, store, metadata = pending
    from reno import sync_state
    original_write = sync_state._write
    operation_id = str(uuid.uuid4())

    def crash(directory, name, value, **kwargs):
        if directory.path.name == interrupted_kind and name == metadata["publication_id"] + ".json":
            raise RuntimeError("controlled interruption after reservation")
        return original_write(directory, name, value, **kwargs)

    with tasks.process_lock() as acquired:
        assert acquired
        if interrupted_kind == "publications":
            preview = store.preview_abandon(metadata["publication_id"])
            monkeypatch.setattr(sync_state, "_write", crash)
            with pytest.raises(RuntimeError):
                store.commit_no_resend(metadata["publication_id"], expected_digest=preview["digest"], operation_id=operation_id)
            with pytest.raises(SyncError) as caught:
                store.assert_sendable(metadata["publication_id"])
            assert caught.value.code == "SYNC_NO_RESEND_RECORD_MISSING"
        else:
            operation_id, _ = authorize(store, metadata)
        requests = install_abandon(remote, store, monkeypatch)
        if interrupted_kind == "receipts":
            monkeypatch.setattr(sync_state, "_write", crash)
            with pytest.raises(RuntimeError):
                agent.resume_no_resend(operation_id, client=remote)
        monkeypatch.setattr(sync_state, "_write", original_write)
        remote.calls.clear()
        assert agent.resume_no_resend(operation_id, client=remote)["state"] == "abandoned"
        assert store.no_resend_receipt(operation_id)["state"] == "abandoned"
        assert len(requests) == 1
        if interrupted_kind == "receipts":
            assert not remote.calls  # The exact remote response was in its reservation.


def test_published_error_requires_exact_historical_receipt(pending, monkeypatch):
    remote, store, metadata = pending
    with tasks.process_lock() as acquired:
        assert acquired
        operation_id, request = authorize(store, metadata)

        def published(*a, **k):
            raise SyncError("controlled published", "SYNC_UPLOAD_PUBLISHED", 409)

        queries = []

        def receipt(source, lease, *, binding, payload_sha256):
            assert storage_lifecycle.current_guard() is None
            queries.append((deepcopy(binding), payload_sha256))
            return {"binding": binding, "payload_sha256": payload_sha256, "state": "published",
                    "receipt": {"publication_id": binding["publication_id"], "source_version": binding["source_version"],
                                "local_version": {**binding["recovery_stream"], "kb_revision": 1},
                                "committed_at": agent._stamp(agent._now()), "replayed": True}}

        monkeypatch.setattr(remote, "abandon_uploads", published, raising=False)
        monkeypatch.setattr(remote, "publication_receipt", receipt, raising=False)
        before = store.read()["applied_recovery_generation"]
        assert agent.resume_no_resend(operation_id, client=remote)["state"] == "published"
        assert queries == [(request["binding"], request["payload_sha256"])]
        assert store.no_resend_receipt(operation_id)["state"] == "published"
        assert store.read()["applied_recovery_generation"] == before


def test_delivery_network_allows_multiple_heartbeat_state_merges(pending, monkeypatch):
    remote, store, metadata = pending
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        operation_id, _ = authorize(store, metadata)
        value = local_cycle(remote, store)
        value.acquire(delivery_only=True)
        monkeypatch.setattr(agent, "HEARTBEAT_SECONDS", 0.01)
        before = sum(isinstance(call, tuple) and call[0] == "heartbeat" for call in remote.calls)
        install_abandon(remote, store, monkeypatch, fault=lambda: threading.Event().wait(0.12))
        with value.heartbeat(delivery_only=True):
            assert value.deliver_no_resend(operation_id=operation_id)[0]["state"] == "abandoned"
        after = sum(isinstance(call, tuple) and call[0] == "heartbeat" for call in remote.calls)
        assert after >= before + 2


def test_explicit_delivery_rejects_changed_peer_without_network(pending, monkeypatch):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata)
    config.set_local({"sync_enabled": False, "sync_target_node_id": str(uuid.uuid4())})
    with tasks.process_lock() as acquired:
        assert acquired
        with pytest.raises(SyncError) as caught:
            agent.resume_no_resend(operation_id, client=remote)
        assert caught.value.code == "SYNC_PEER_MISMATCH"
    assert not remote.calls and store.no_resend_receipt(operation_id) is None


@pytest.mark.parametrize("edge", ["detach", "published_error", "after_abandon"])
def test_delivery_rechecks_current_pairing_at_every_network_edge(pending, monkeypatch, edge):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata)

    def change_peer():
        config.set_local({"sync_target_node_id": str(uuid.uuid4())})

    original_detach = StateStore.detach_no_resend

    def detach(current, selected):
        result = original_detach(current, selected)
        if edge == "detach":
            change_peer()
        return result

    def abandon(source, lease, *, binding, operation_id):
        assert edge != "detach", "changed peer cannot receive abandon"
        change_peer()
        if edge == "published_error":
            raise SyncError("controlled published", "SYNC_UPLOAD_PUBLISHED", 409)
        return {"binding": binding, "operation_id": operation_id, "state": "abandoned"}

    monkeypatch.setattr(StateStore, "detach_no_resend", detach)
    monkeypatch.setattr(remote, "abandon_uploads", abandon, raising=False)
    monkeypatch.setattr(remote, "publication_receipt", lambda *a, **k: pytest.fail("changed peer cannot receive receipt query"), raising=False)
    with tasks.process_lock() as acquired:
        assert acquired
        with pytest.raises(SyncError) as caught:
            agent.resume_no_resend(operation_id, client=remote)
        assert caught.value.code == "SYNC_PEER_MISMATCH"
    assert store.no_resend_receipt(operation_id) is None


def test_held_cycle_exchanges_reviews_before_delivering_only_original_committed_controls(pending, monkeypatch):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata)
    requests = install_abandon(remote, store, monkeypatch)
    remote.events = [add_event(remove=True)]
    original = remote.abandon_uploads
    def after_reviews(*args, **kwargs):
        assert any(isinstance(call, tuple) and call[0] == "pull" and call[2] == 0 for call in remote.calls)
        assert "push_events" in remote.calls
        return original(*args, **kwargs)
    monkeypatch.setattr(remote, "abandon_uploads", after_reviews)
    remote.recovery.update(state="held", generation=1, revision=1)
    assert cycle(remote) == {"state": "recovery_held"}
    assert requests[0][1] == operation_id
    assert not any(call == "publish" or isinstance(call, tuple) and call[0] in {"media", "prepare", "verify"}
                   for call in remote.calls)


@pytest.mark.parametrize("restore_v1", [False, True])
def test_v2_legacy_cache_survives_sender_epoch_reset(pending, restore_v1):
    remote, store, metadata = pending
    envelope = store.read_publication(metadata)
    descriptor = envelope["snapshot"]["videos"][0]["media"]
    with store.exclusive():
        store.update({"uploaded_media": {descriptor["video_id"]: descriptor}})
        original = (config.DATA / "sync-state.json").read_bytes()
        store.enable_no_resend()
        if restore_v1:
            (config.DATA / "sync-state.json").write_bytes(original)
    from reno import db
    con = db.connect()
    con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    con.commit()
    con.close()
    assert cycle(remote)["state"] == "idle"
    assert store.read()["uploaded_media"] == {descriptor["video_id"]: descriptor}


@pytest.mark.parametrize("explicit", [False, True])
def test_peer_switch_after_handshake_cannot_acquire_old_peer_lease(pending, monkeypatch, explicit):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata) if explicit else (None, None)
    original = remote.handshake

    def handshake():
        result = original()
        config.set_local({"sync_cloud_url": "https://new-peer.example.test/reno",
                          "sync_target_node_id": str(uuid.uuid4()), "sync_token": "artificial-new-peer-token"})
        return result

    monkeypatch.setattr(remote, "handshake", handshake)
    with tasks.process_lock() as acquired:
        assert acquired
        if explicit:
            config.set_local({"sync_enabled": False})
            with pytest.raises(SyncError) as caught:
                agent.resume_no_resend(operation_id, client=remote)
            assert caught.value.code == "SYNC_PEER_MISMATCH"
            assert store.no_resend_receipt(operation_id) is None
        else:
            assert agent.run_cycle(client=remote)["error_code"] == "SYNC_PEER_MISMATCH"
    assert remote.calls == ["handshake"]


@pytest.mark.parametrize("explicit", [False, True])
def test_real_entry_client_does_not_send_repaired_peer_token_to_frozen_old_url(pending, monkeypatch, explicit):
    remote, store, metadata = pending
    operation_id, _ = authorize(store, metadata) if explicit else (None, None)
    config.set_local({"sync_token": "artificial-old-peer-token"})
    requests = []

    def handler(request):
        requests.append((str(request.url), request.headers["x-sync-token"]))
        assert request.url.path.endswith("/handshake")
        config.set_local({"sync_cloud_url": "https://new-peer.example.test/reno",
                          "sync_target_library_id": str(uuid.uuid4()), "sync_target_node_id": str(uuid.uuid4()),
                          "sync_token": "artificial-new-peer-token"})
        return httpx.Response(200, json={"target": remote.target})

    def real_client(base_url, **kwargs):
        return SyncClient(base_url, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(agent, "SyncClient", real_client)
    with tasks.process_lock() as acquired:
        assert acquired
        if explicit:
            config.set_local({"sync_enabled": False})
            with pytest.raises(SyncError) as caught:
                agent.resume_no_resend(operation_id)
            assert caught.value.code == "SYNC_CLIENT_CANCELLED"
        else:
            assert agent.run_cycle()["error_code"] == "SYNC_CLIENT_CANCELLED"
    assert requests == [("https://sync.example.test/reno/api/sync/handshake", "artificial-old-peer-token")]


@pytest.mark.parametrize("edge", ["after_prepare", "after_verify", "upload_callback", "before_push"])
def test_ordinary_publication_stops_following_requests_after_peer_switch(pending, monkeypatch, edge):
    remote, store, _ = pending
    original_prepare, original_verify, original_upload = remote.prepare_uploads, remote.verify_upload, remote.upload_media

    def change_peer():
        config.set_local({"sync_target_node_id": str(uuid.uuid4()), "sync_token": "artificial-new-peer-token"})

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        if edge == "after_prepare":
            change_peer()
        return result

    def verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        if edge in {"after_verify", "before_push"}:
            change_peer()
        return {**result, "stored": edge == "before_push"}

    def upload(*args, **kwargs):
        if edge == "upload_callback":
            change_peer()
            kwargs["check_cancelled"]()
        return original_upload(*args, **kwargs)

    monkeypatch.setattr(remote, "prepare_uploads", prepare)
    monkeypatch.setattr(remote, "verify_upload", verify)
    monkeypatch.setattr(remote, "upload_media", upload)
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        value = local_cycle(remote, store)
        with pytest.raises(SyncError) as caught:
            value.publish()
        assert caught.value.code == "SYNC_PEER_MISMATCH"
    assert "publish" not in remote.calls
    assert not any(isinstance(call, tuple) and call[0] == "media" for call in remote.calls)
    if edge == "after_prepare":
        assert not any(isinstance(call, tuple) and call[0] == "verify" for call in remote.calls)
