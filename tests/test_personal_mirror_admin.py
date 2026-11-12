"""Explicit preview/CAS and original operation administration, actual fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
import uuid

import httpx
import pytest

from tests.asgi_transport import as_httpx_response

from reno import config, personal, personal_mirror_admin as admin
from reno import personal_mirror_transport as sender, storage_lifecycle as lifecycle, tasks
from reno import sync_state
from reno.sync_client import SyncClient
from reno.sync_protocol import SyncError
from tests.test_personal_mirror_transfer import mirror_env, make_prepare
from tests.test_personal_mirror_transport import (
    sender_env, owned, initialize, freeze, deliver, actual_peer, request_path, URL,
)


def uid():
    return str(uuid.uuid4())


def begin(env, *, attachment=False):
    if attachment:
        make_prepare(env, with_attachment=True)
        env["select_home"]()
    admin.initialize(env["source_profile"])
    return admin.preview(env["source_profile"], operation_id=uid(), snapshot_id=uid())


def apply(env, selection, **overrides):
    kwargs = {key: selection[key] for key in
        ("operation_id", "snapshot_id", "expected_head_operation_id")}
    kwargs["expected_snapshot_sha256"] = selection["snapshot_sha256"]
    kwargs.update(overrides)
    return admin.apply(env["source_profile"], **kwargs)


def state_raw(env):
    return (env["source_data"] / sync_state._STATE).read_bytes()


def private_tree(env):
    root = env["source_data"] / sender.ROOT
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def no_private_output(value):
    raw = json.dumps(value, ensure_ascii=False)
    assert "https://" not in raw and "synthetic-personal-mirror-test-token" not in raw
    assert "snapshot_json" not in raw and "request_bytes" not in raw and "prepare" not in value
    assert "source" not in value and "binding" not in value


def test_preview_and_status_require_existing_state_without_creating_locks(mirror_env):
    env = mirror_env
    env["select"](env["source_root"], "home")
    before = sorted(path.name for path in env["source_data"].iterdir())
    with pytest.raises(SyncError) as caught:
        admin.preview(env["source_profile"], operation_id=uid(), snapshot_id=uid())
    assert caught.value.code == "PERSONAL_MIRROR_ADMIN_STATE_NOT_INITIALIZED"
    with pytest.raises(SyncError):
        admin.status(env["source_profile"], operation_id=uid(), expected_request_sha256="1" * 64)
    assert sorted(path.name for path in env["source_data"].iterdir()) == before
    assert not (env["source_data"] / sync_state._LOCK).exists()
    assert not (env["source_data"] / "worker.lock").exists()
    assert not (env["source_data"] / sender.ROOT).exists()


def test_preview_does_not_initialize_sender_or_write_worker_lock(sender_env):
    env = sender_env
    before = state_raw(env)
    names = sorted(path.name for path in env["source_data"].iterdir())
    with pytest.raises(SyncError) as caught:
        admin.preview(env["source_profile"], operation_id=uid(), snapshot_id=uid())
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_NOT_INITIALIZED"
    assert state_raw(env) == before
    assert sorted(path.name for path in env["source_data"].iterdir()) == names
    assert not (env["source_data"] / sender.ROOT).exists()


def test_initialize_is_explicit_and_preserves_original_state_pair_and_primary(sender_env):
    env = sender_env
    before = state_raw(env)
    assert admin.initialize(env["source_profile"]) == {"state": "initialized"}
    raw = private_tree(env)
    assert set(raw) == {sender.MARKER, sender.LOCK, sender.CATALOG}
    assert state_raw(env) == before
    assert env["calls"] == []
    assert admin.initialize(env["source_profile"]) == {"state": "initialized"}
    assert private_tree(env) == raw


def test_preview_is_actual_deterministic_capture_without_new_request_or_rpc(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
    # This fixture initialized State and sender, but never acquired worker.lock.
    before, state = private_tree(env), state_raw(env)
    operation, snapshot = uid(), uid()
    first = admin.preview(env["source_profile"], operation_id=operation, snapshot_id=snapshot)
    second = admin.preview(env["source_profile"], operation_id=operation, snapshot_id=snapshot)
    assert first == second and first["state"] == "preview"
    assert first["counts"]["source_record"] == 1
    no_private_output(first)
    assert private_tree(env) == before and state_raw(env) == state
    assert not (env["source_data"] / "worker.lock").exists()
    assert env["calls"] == []


def test_apply_exact_preview_publishes_original_request_without_delivery(sender_env):
    env = sender_env
    selection = begin(env, attachment=True)
    state = state_raw(env)
    result = apply(env, selection)
    assert result["state"] == "pending"
    assert result["snapshot_sha256"] == selection["snapshot_sha256"]
    assert result["request_sha256"] == selection["request_sha256"]
    assert result["attachment_count"] == 1 and result["attachment_bytes"] > 0
    assert hashlib.sha256(request_path(env, result["operation_id"]).read_bytes()).hexdigest() == result["request_sha256"]
    assert state_raw(env) == state and env["calls"] == []
    no_private_output(result)


@pytest.mark.parametrize("stale", ["wrong_sha", "actual_fact_change"])
def test_stale_preview_cas_fails_before_immutable_request_publication(sender_env, stale):
    env = sender_env
    selection = begin(env)
    before = private_tree(env)
    if stale == "actual_fact_change":
        personal.create_source(env["project_id"], {"operation_id": uid(), "primary_epoch": env["primary_epoch"],
            "changes": {"source_type": "manual_note", "title": "actual newer personal fact"}})
        kwargs = {}
    else:
        kwargs = {"expected_snapshot_sha256": "f" * 64}
    with pytest.raises(SyncError) as caught:
        apply(env, selection, **kwargs)
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_STALE_PREVIEW"
    assert not request_path(env, selection["operation_id"]).exists()
    assert private_tree(env) == before and env["calls"] == []


def test_apply_new_batch_holds_actual_process_state_and_profile_ex(sender_env, monkeypatch):
    env = sender_env
    selection = begin(env)
    original = sender.capture.capture_snapshot
    checked = []
    def captured(**kwargs):
        assert tasks.holds_process_lock(profile=env["source_profile"])
        assert lifecycle.require_guard(env["source_profile"], exclusive=True).exclusive
        checked.append(kwargs["snapshot_id"])
        return original(**kwargs)
    monkeypatch.setattr(sender.capture, "capture_snapshot", captured)
    apply(env, selection)
    assert checked == [selection["snapshot_id"]]


def test_original_operation_apply_and_preview_never_recapture_new_personal_facts(sender_env, monkeypatch):
    env = sender_env
    selection = begin(env)
    original = apply(env, selection)
    raw = request_path(env, original["operation_id"]).read_bytes()
    personal.create_source(env["project_id"], {"operation_id": uid(), "primary_epoch": env["primary_epoch"],
        "changes": {"source_type": "manual_note", "title": "new facts after original frozen request"}})
    def forbidden(**kwargs):
        pytest.fail("An existing original operation recaptured a new snapshot")
    monkeypatch.setattr(sender.capture, "capture_snapshot", forbidden)
    assert apply(env, selection)["snapshot_sha256"] == original["snapshot_sha256"]
    assert admin.preview(env["source_profile"], operation_id=selection["operation_id"],
        snapshot_id=selection["snapshot_id"])["snapshot_sha256"] == original["snapshot_sha256"]
    with pytest.raises(SyncError) as caught:
        apply(env, selection, expected_snapshot_sha256="f" * 64)
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_STALE_PREVIEW"
    assert request_path(env, original["operation_id"]).read_bytes() == raw


def test_pending_original_request_blocks_preview_of_a_new_batch_before_capture(sender_env, monkeypatch):
    env = sender_env
    apply(env, begin(env))
    def forbidden(**kwargs):
        pytest.fail("A pending original selected a new capture")
    monkeypatch.setattr(sender.capture, "capture_snapshot", forbidden)
    with pytest.raises(SyncError) as caught:
        admin.preview(env["source_profile"], operation_id=uid(), snapshot_id=uid())
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_PENDING_OPERATION_REQUIRED"


def test_status_reads_actual_request_publish_gap_without_catalog_repair(sender_env, monkeypatch):
    env = sender_env
    selection = begin(env)
    failure = RuntimeError("controlled request publication gap")
    def interrupted(*args, **kwargs):
        raise failure
    with monkeypatch.context() as patch:
        patch.setattr(sender, "_register_request", interrupted)
        with pytest.raises(RuntimeError) as caught:
            apply(env, selection)
        assert caught.value is failure
    before = private_tree(env)
    raw = request_path(env, selection["operation_id"]).read_bytes()
    result = admin.status(env["source_profile"], operation_id=selection["operation_id"],
        expected_request_sha256=hashlib.sha256(raw).hexdigest())
    assert result["state"] == "request_published"
    assert json.loads(before[sender.CATALOG])["entries"] == []
    assert private_tree(env) == before and env["calls"] == []
    no_private_output(result)


def test_status_requires_original_uuid_sha_and_does_not_select_latest(sender_env):
    env = sender_env
    original = apply(env, begin(env))
    before = private_tree(env)
    with pytest.raises(SyncError) as caught:
        admin.status(env["source_profile"], operation_id=original["operation_id"], expected_request_sha256="f" * 64)
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_REQUEST_CHANGED"
    with pytest.raises(SyncError):
        admin.status(env["source_profile"], operation_id=uid(), expected_request_sha256=original["request_sha256"])
    assert private_tree(env) == before and env["calls"] == []


def test_worker_delivery_requires_actual_process_ownership_before_network(sender_env):
    env = sender_env
    original = apply(env, begin(env))
    with pytest.raises(SyncError) as caught:
        admin.deliver_worker(env["source_profile"], operation_id=original["operation_id"],
            expected_request_sha256=original["request_sha256"], client=actual_peer(env))
    assert caught.value.code == "PERSONAL_MIRROR_ADMIN_WORKER_LOCK_REQUIRED"
    assert env["calls"] == []


def test_worker_delivers_only_original_actual_request_and_receiver_commit(sender_env):
    env = sender_env
    original = apply(env, begin(env, attachment=True))
    peer = actual_peer(env)
    # The actual receiver runs in another thread, with its own real guard.
    # Only HTTP transport is substituted; no source owner context is borrowed.
    with ThreadPoolExecutor(max_workers=1) as remote:
        def handle(request):
            # Drain the sender's held-body generator on its original owner
            # thread before the remote receiver consumes these HTTP bytes.
            request.read()
            def receiver_request():
                with lifecycle.shared(env["profile"]):
                    return as_httpx_response(peer._transport.handle_request(request))
            return remote.submit(receiver_request).result()
        client = SyncClient(URL, token_provider=lambda: "synthetic-personal-mirror-test-token",
                            transport=httpx.MockTransport(handle))
        with tasks.process_lock(profile=env["source_profile"]) as acquired:
            assert acquired
            result = admin.deliver_worker(env["source_profile"], operation_id=original["operation_id"],
                expected_request_sha256=original["request_sha256"], client=client)
    assert result["state"] == "committed" and result["request_sha256"] == original["request_sha256"]
    assert any(endpoint == "commit" for endpoint, *_ in env["calls"])
    no_private_output(result)


def test_original_stored_receipt_recovers_catalog_with_zero_rpc_and_no_capture(sender_env, monkeypatch):
    env = sender_env
    failure = RuntimeError("controlled receipt-to-head gap")
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        with monkeypatch.context() as patch:
            def interrupted(*args, **kwargs):
                raise failure
            patch.setattr(sender, "_advance", interrupted)
            with pytest.raises(RuntimeError):
                deliver(env, frozen)
    env["calls"].clear()
    before = private_tree(env)
    status = admin.status(env["source_profile"], operation_id=frozen["operation_id"],
        expected_request_sha256=frozen["request_sha256"])
    assert status["state"] == "committed" and private_tree(env) == before
    def forbidden(*args, **kwargs):
        pytest.fail("A stored original receipt recaptured or called the network")
    monkeypatch.setattr(SyncClient, "_request", forbidden)
    monkeypatch.setattr(sender.capture, "capture_snapshot", forbidden)
    with tasks.process_lock(profile=env["source_profile"]) as acquired:
        assert acquired
        result = admin.deliver_worker(env["source_profile"], operation_id=frozen["operation_id"],
            expected_request_sha256=frozen["request_sha256"])
    assert result["state"] == "committed" and env["calls"] == []


def test_disabled_and_cloud_roles_do_not_create_facilities(sender_env):
    env = sender_env
    local = config.local_path()
    value = json.loads(local.read_text(encoding="utf-8"))
    value["personal_mirror_enabled"] = False
    local.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SyncError) as caught:
        admin.initialize(env["source_profile"])
    assert caught.value.code == "PERSONAL_MIRROR_ADMIN_DISABLED"
    assert not (env["source_data"] / sender.ROOT).exists()
    env["select"](env["target_root"], "cloud")
    with pytest.raises(SyncError) as caught:
        admin.initialize(env["profile"])
    assert caught.value.code == "PERSONAL_MIRROR_ADMIN_DISABLED"
    assert not (env["data"] / sender.ROOT).exists()


def test_state_existing_only_refuses_missing_directory_and_lock_without_initialization(tmp_path):
    missing = tmp_path / "missing-state-root"
    with pytest.raises(SyncError):
        with sync_state.StateStore(missing).exclusive(existing_only=True):
            pytest.fail("Existing-only State ownership created a missing directory")
    assert not missing.exists()
    existing = tmp_path / "existing-state-root"
    existing.mkdir()
    with pytest.raises(SyncError):
        with sync_state.StateStore(existing).exclusive(existing_only=True):
            pytest.fail("Existing-only State ownership created a missing lock")
    assert list(existing.iterdir()) == []
    # The released explicit initialization behavior remains available.
    with sync_state.StateStore(missing).exclusive():
        assert (missing / sync_state._LOCK).is_file()


def test_state_existing_only_retains_actual_kernel_contender_policy(sender_env):
    env = sender_env
    first = sync_state.StateStore(env["source_data"], profile=env["source_profile"])
    second = sync_state.StateStore(env["source_data"], profile=env["source_profile"])
    before = state_raw(env)
    with first.exclusive(existing_only=True):
        for existing_only in (False, True):
            with pytest.raises(SyncError) as caught:
                with second.exclusive(existing_only=existing_only):
                    pytest.fail("A second opener acquired the same real State OS lock")
            assert caught.value.code == "SYNC_BUSY"
    assert state_raw(env) == before


@pytest.mark.skipif(os.name == "nt", reason="Windows pinned lock already forbids unlink")
@pytest.mark.parametrize("operation", ["preview", "status"])
def test_posix_unlink_after_preflight_never_recreates_readonly_state_lock(sender_env, monkeypatch, operation):
    env = sender_env
    original = apply(env, begin(env))
    original_exclusive = sync_state.StateStore.exclusive
    state_lock = env["source_data"] / sync_state._LOCK
    @contextmanager
    def delete_in_gap(store, *, existing_only=False):
        assert existing_only is True
        # The actual preflight has pinned this old lock, but POSIX unlink
        # remains possible. Only OPEN_EXISTING prevents hidden reconstruction.
        state_lock.unlink()
        with original_exclusive(store, existing_only=existing_only) as held:
            yield held
    monkeypatch.setattr(sync_state.StateStore, "exclusive", delete_in_gap)
    with pytest.raises(SyncError):
        if operation == "preview":
            admin.preview(env["source_profile"], operation_id=original["operation_id"], snapshot_id=original["snapshot_id"])
        else:
            admin.status(env["source_profile"], operation_id=original["operation_id"], expected_request_sha256=original["request_sha256"])
    assert not state_lock.exists()
    assert request_path(env, original["operation_id"]).exists()


@pytest.mark.parametrize("guard_kind", ["shared", "exclusive"])
@pytest.mark.parametrize("operation", ["initialize", "preview", "apply", "status"])
def test_outer_lifecycle_guard_is_rejected_before_state_ownership(sender_env, monkeypatch, guard_kind, operation):
    env = sender_env
    original = apply(env, begin(env))
    before, state = private_tree(env), state_raw(env)
    def forbidden(*args, **kwargs):
        pytest.fail("State ownership was attempted after an existing lifecycle guard")
    monkeypatch.setattr(sync_state.StateStore, "exclusive", forbidden)
    with getattr(lifecycle, guard_kind)(env["source_profile"]), pytest.raises(SyncError) as caught:
        if operation == "initialize":
            admin.initialize(env["source_profile"])
        elif operation == "preview":
            admin.preview(env["source_profile"], operation_id=original["operation_id"], snapshot_id=original["snapshot_id"])
        elif operation == "apply":
            apply(env, original)
        else:
            admin.status(env["source_profile"], operation_id=original["operation_id"], expected_request_sha256=original["request_sha256"])
    assert caught.value.code == "PERSONAL_MIRROR_ADMIN_LOCK_ORDER_INVALID"
    assert private_tree(env) == before and state_raw(env) == state
