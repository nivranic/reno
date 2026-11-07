"""Public sender commands use artificial, explicitly enrolled storage only."""
from copy import deepcopy
from contextlib import closing
import json
import hashlib
import os
import sqlite3
import uuid

import pytest
import httpx

from reno import cli, config, db, storage_lifecycle as lifecycle, storage_members as members
from reno import sync_sender_admin as admin, sync_state, sync_no_resend as ledger
from reno import tasks
from reno.sync_protocol import SyncError
from tests.test_storage_roots_cli import arguments, contents
from tests.test_sync_no_resend import peer


@pytest.fixture
def home(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    declarations = [{"root": str(root), "databases": [str(profile.database)]}
                    for root in (profile.media, profile.reports, profile.checkpoints)]
    preview = members.membership_preview([profile], declarations=declarations)
    members.enroll([profile], expected_digest=preview["digest"], declarations=declarations,
                   operation_id=str(uuid.uuid4()))
    with closing(sqlite3.connect(profile.database)) as con:
        version = db.knowledge_version(con)
    node_id = json.loads((profile.database.parent / "node-identity.json").read_bytes())["instance_id"]
    binding = {**peer(), "home_library_id": version["library_id"], "home_node_id": node_id}
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    store.initialize(binding, version["library_epoch"])
    snapshot = {"publication_id": str(uuid.uuid4()), "version": version,
                "videos": [], "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    stream = {"library_id": binding["target_library_id"], "library_epoch": 1}
    metadata = store.write_publication(snapshot, [], recovery_stream=stream)
    store.update({"pending_publication": metadata})
    return profile, store, metadata


def forbid_configuration(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Explicit sender administration consulted configured storage or initialization")
    for name in ("get", "db_path", "local_path"):
        monkeypatch.setattr(config, name, forbidden)
    for name in ("connect", "initialize", "migrate"):
        monkeypatch.setattr(db, name, forbidden)


def commit(home):
    profile, _store, metadata = home
    preview = admin.preview_abandon(profile, metadata["publication_id"])
    operation_id = str(uuid.uuid4())
    result = admin.abandon(profile, metadata["publication_id"], expected_digest=preview["digest"], operation_id=operation_id)
    assert result["state"] == "no_resend_remote_pending"
    return operation_id


def sender_file(home):
    profile, store, _metadata = home
    binding = store.read()["binding"]
    path = profile.database.parent.parent / "EXPLICIT_PRIVATE_CONFIG.json"
    path.write_text(json.dumps({"node_role": "home", "sync_enabled": False,
        "sync_cloud_url": binding["target_url"], "sync_target_library_id": binding["target_library_id"],
        "sync_target_node_id": binding["target_node_id"], "sync_token": "PRIVATE_TOKEN"}), encoding="utf-8")
    return path


class Remote:
    def __init__(self, home):
        self.profile, self.store, _metadata = home
        self.calls = []
    def check_network(self, name):
        assert lifecycle.current_guard() is None
        self.calls.append(name)
    def handshake(self):
        self.check_network("handshake")
        with admin._proof(self.profile, self.store) as (_proof, source, _verify):
            handshake = source["handshake"]
        binding = self.store.read()["binding"]
        return {"library_id": binding["target_library_id"], "node_id": binding["target_node_id"], "handshake": handshake}
    def heartbeat(self, source, session_id, *, lease=None):
        self.check_network("heartbeat")
        return {"lease": {"session_id": session_id, "lease_token": str(uuid.uuid4()) if lease is None else lease["lease_token"],
                          "generation": 1, "lease_until": "2099-01-01T00:00:00.000Z"}}
    def abandon_uploads(self, source, lease, *, binding, operation_id):
        self.check_network("abandon")
        return {"binding": deepcopy(binding), "operation_id": operation_id, "state": "abandoned"}


def test_preview_cli_is_private_pure_and_configuration_independent(home, tmp_path, monkeypatch, capsys):
    profile, _store, metadata = home
    before = contents(tmp_path)
    forbid_configuration(monkeypatch)
    assert cli.main(["sync-abandon", metadata["publication_id"], *arguments(profile)]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["stage"] == "sendable" and len(result["digest"]) == 64
    assert not captured.err and str(tmp_path) not in captured.out and "private-prefix" not in captured.out
    assert contents(tmp_path) == before
    assert not (profile.database.parent / ledger.WITNESS).exists()


def test_stale_public_digest_rejects_before_activation(home, monkeypatch):
    profile, store, metadata = home
    forbid_configuration(monkeypatch)
    with pytest.raises(SyncError) as caught:
        admin.abandon(profile, metadata["publication_id"], expected_digest="0" * 64, operation_id=str(uuid.uuid4()))
    assert caught.value.code == "SYNC_NO_RESEND_STALE_PREVIEW"
    assert store.read()["pending_publication"] == metadata
    assert not (profile.database.parent / ledger.WITNESS).exists()


def test_owner_mismatch_cannot_write_sender_capability(home):
    profile, _store, metadata = home
    path = profile.database.parent / "node-identity.json"
    path.write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    with pytest.raises(SyncError) as caught:
        admin.abandon(profile, metadata["publication_id"], expected_digest="0" * 64, operation_id=str(uuid.uuid4()))
    assert caught.value.code == "SYNC_SENDER_OWNER_MISMATCH"
    assert not (profile.database.parent / ledger.WITNESS).exists()


def test_local_apply_does_not_need_credentials_or_network(home, monkeypatch):
    forbid_configuration(monkeypatch)
    operation_id = commit(home)
    profile, store, metadata = home
    request = store.no_resend_request(operation_id)
    assert request["metadata"] == metadata and store.read()["pending_publication"] is None
    assert store.no_resend_receipt(operation_id) is None


def test_explicit_remote_resume_and_completed_replay_ignore_default_configuration(home, monkeypatch):
    operation_id = commit(home)
    credential = sender_file(home)
    remote = Remote(home)
    forbid_configuration(monkeypatch)
    profile, _store, _metadata = home
    assert admin.resume_abandon(profile, operation_id, sender_config=credential, client=remote) == {
        "operation_id": operation_id, "state": "abandoned"}
    assert remote.calls == ["handshake", "heartbeat", "abandon"]
    remote.calls.clear()
    credential.unlink()
    assert admin.resume_abandon(profile, operation_id, client=remote)["state"] == "abandoned"
    assert not remote.calls


def test_changed_credential_peer_is_rejected_before_network(home):
    operation_id = commit(home)
    credential = sender_file(home)
    value = json.loads(credential.read_bytes())
    value["sync_target_node_id"] = str(uuid.uuid4())
    credential.write_text(json.dumps(value), encoding="utf-8")
    remote = Remote(home)
    with pytest.raises(SyncError) as caught:
        admin.resume_abandon(home[0], operation_id, sender_config=credential, client=remote)
    assert caught.value.code == "SYNC_PEER_MISMATCH" and not remote.calls


def test_unknown_resume_never_selects_a_new_publication(home):
    profile, store, metadata = home
    remote = Remote(home)
    with pytest.raises(SyncError):
        admin.resume_abandon(profile, str(uuid.uuid4()), client=remote)
    assert not remote.calls and store.read()["pending_publication"] == metadata


def test_independent_state_cache_retirement_detaches_only_selected_entry(home, monkeypatch, tmp_path):
    profile, store, _metadata = home
    first = {"video_id": "BV1OldHint", "sha256": "a" * 64, "byte_size": 3, "extension": "mp4"}
    second = {"video_id": "BV1OtherHint", "sha256": "b" * 64, "byte_size": 4, "extension": "mp4"}
    store.update({"uploaded_media": {first["video_id"]: first, second["video_id"]: second}})
    raw = (profile.database.parent / "sync-state.json").read_bytes()
    selection = {"format_version": 1, "selected": [{"kind": "legacy_cache", "holder_kind": "state",
        "holder_name": "sync-state.json", "holder_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "video_id": first["video_id"], "descriptor": first}]}
    before = contents(tmp_path)
    forbid_configuration(monkeypatch)
    preview = admin.preview_retire_local(profile, selection)
    assert contents(tmp_path) == before
    operation_id = str(uuid.uuid4())
    assert admin.retire_local(profile, selection, expected_digest=preview["digest"], operation_id=operation_id) == {
        "operation_id": operation_id, "state": "local_references_retired", "selected_count": 1}
    assert store.read()["uploaded_media"] == {second["video_id"]: second}
    assert admin.resume_retire_local(profile, selection, operation_id=operation_id)["selected_count"] == 1
    widened = deepcopy(selection)
    widened["selected"][0]["video_id"] = second["video_id"]
    widened["selected"][0]["descriptor"] = second
    with pytest.raises(SyncError) as caught:
        admin.resume_retire_local(profile, widened, operation_id=operation_id)
    assert caught.value.code == "SYNC_NO_RESEND_OPERATION_CONFLICT"
    assert store.read()["uploaded_media"] == {second["video_id"]: second}


def test_worker_lock_alias_rejected_before_sender_writes(home, tmp_path):
    profile, store, metadata = home
    preview = admin.preview_abandon(profile, metadata["publication_id"])
    lock = profile.database.parent / "worker.lock"
    target = tmp_path / "PRIVATE_EXTERNAL_TARGET"
    target.write_bytes(b"PRIVATE_UNCHANGED_BYTES")
    os.link(target, lock)
    with pytest.raises((SyncError, OSError)):
        admin.abandon(profile, metadata["publication_id"], expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))
    assert target.read_bytes() == b"PRIVATE_UNCHANGED_BYTES"
    assert store.read()["pending_publication"] == metadata
    assert not (profile.database.parent / ledger.WITNESS).exists()


def test_peer_change_after_handshake_stops_before_session(home):
    operation_id = commit(home)
    credential = sender_file(home)
    remote = Remote(home)
    handshake = remote.handshake
    def change_peer():
        result = handshake()
        values = json.loads(credential.read_bytes())
        values["sync_target_node_id"] = str(uuid.uuid4())
        credential.write_text(json.dumps(values), encoding="utf-8")
        return result
    remote.handshake = change_peer
    with pytest.raises(SyncError) as caught:
        admin.resume_abandon(home[0], operation_id, sender_config=credential, client=remote)
    assert caught.value.code == "SYNC_PEER_MISMATCH" and remote.calls == ["handshake"]


def test_state_peer_change_after_handshake_stops_before_session(home):
    operation_id = commit(home)
    credential = sender_file(home)
    remote = Remote(home)
    handshake = remote.handshake
    def change_peer():
        result = handshake()
        value = home[1].read()
        value["binding"]["target_node_id"] = str(uuid.uuid4())
        (home[0].database.parent / "sync-state.json").write_text(json.dumps(value), encoding="utf-8")
        return result
    remote.handshake = change_peer
    with pytest.raises(SyncError) as caught:
        admin.resume_abandon(home[0], operation_id, sender_config=credential, client=remote)
    assert caught.value.code == "SYNC_PEER_MISMATCH" and remote.calls == ["handshake"]


def test_worker_resource_cleanup_does_not_replace_caller_error_or_keep_lock(home, monkeypatch):
    original_close = tasks.ExitStack.close
    caller = RuntimeError("synthetic caller cancellation")
    def noisy_close(resources):
        original_close(resources)
        raise RuntimeError("synthetic secondary cleanup")
    with monkeypatch.context() as patch:
        patch.setattr(tasks.ExitStack, "close", noisy_close)
        with pytest.raises(RuntimeError) as caught:
            with tasks.process_lock(profile=home[0]) as acquired:
                assert acquired
                raise caller
        assert caught.value is caller and "WORKER_LOCK_CLEANUP_FAILED" in caller.__notes__
        assert not tasks.holds_process_lock(profile=home[0])
    with tasks.process_lock(profile=home[0]) as acquired:
        assert acquired


def test_local_retirement_cli_previews_applies_and_resumes_original_request(home, tmp_path, capsys, monkeypatch):
    profile, store, _metadata = home
    descriptor = {"video_id": "BV1CliHint", "sha256": "c" * 64, "byte_size": 7, "extension": "mp4"}
    store.update({"uploaded_media": {descriptor["video_id"]: descriptor}})
    raw = (profile.database.parent / "sync-state.json").read_bytes()
    selection = {"format_version": 1, "selected": [{"kind": "legacy_cache", "holder_kind": "state",
        "holder_name": "sync-state.json", "holder_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "video_id": descriptor["video_id"], "descriptor": descriptor}]}
    request = tmp_path / "PRIVATE_SELECTED_REQUEST.json"
    request.write_text(json.dumps(selection), encoding="utf-8")
    command = ["sync-retire-local", *arguments(profile), "--request-file", str(request)]
    before = contents(tmp_path)
    forbid_configuration(monkeypatch)
    assert cli.main(command) == 0
    output = capsys.readouterr()
    preview = json.loads(output.out)
    assert preview["selected_count"] == 1 and "BV1CliHint" not in output.out and str(tmp_path) not in output.out
    assert contents(tmp_path) == before
    operation_id = str(uuid.uuid4())
    assert cli.main([*command, "--apply", "--expected-digest", preview["digest"], "--operation-id", operation_id]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "local_references_retired"
    assert store.read()["uploaded_media"] == {}
    assert cli.main([*command, "--resume", "--operation-id", operation_id]) == 0
    assert json.loads(capsys.readouterr().out)["selected_count"] == 1


def test_explicit_apply_with_missing_facilities_does_not_create_data_or_roots(tmp_path, capsys):
    root = tmp_path / "PRIVATE_MISSING_PROFILE"
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media", root / "reports", root / "checkpoints")
    assert cli.main(["sync-abandon", str(uuid.uuid4()), *arguments(profile), "--apply",
                     "--expected-digest", "a" * 64, "--operation-id", str(uuid.uuid4())]) == 2
    output = capsys.readouterr()
    assert not root.exists() and str(tmp_path) not in output.err
    assert json.loads(output.err)["code"] == "STORAGE_NOT_INITIALIZED"


def test_uncommitted_sql_does_not_change_public_preview_or_touch_source(home, tmp_path):
    profile, _store, metadata = home
    original = admin.preview_abandon(profile, metadata["publication_id"])
    with closing(sqlite3.connect(profile.database)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'")
        # Windows reserves parts of live SHM; production never reads/stat's it.
        # Compare the inspectable source bytes while keeping the writer open.
        def source_bytes():
            return {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*")
                    if not path.name.endswith("-shm") and path.is_file()}
        before = source_bytes()
        assert admin.preview_abandon(profile, metadata["publication_id"]) == original
        assert source_bytes() == before
        writer.rollback()


def test_committed_owner_version_change_invalidates_public_cas(home):
    profile, _store, metadata = home
    original = admin.preview_abandon(profile, metadata["publication_id"])
    with closing(sqlite3.connect(profile.database)) as writer:
        writer.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'")
        writer.commit()
    with pytest.raises(SyncError) as caught:
        admin.abandon(profile, metadata["publication_id"], expected_digest=original["digest"], operation_id=str(uuid.uuid4()))
    assert caught.value.code == "SYNC_NO_RESEND_STALE_PREVIEW"
    assert not (profile.database.parent / ledger.WITNESS).exists()


def test_real_client_rotates_explicit_token_for_same_peer_without_config_fallback(home, monkeypatch):
    from reno import sync_client
    operation_id = commit(home)
    profile, store, _metadata = home
    credential = sender_file(home)
    with admin._proof(profile, store) as (_proof, source, _verify):
        source = deepcopy(source)
    binding = store.read()["binding"]
    paths = []
    def handler(request):
        assert lifecycle.current_guard() is None
        paths.append(request.url.path.rsplit("/", 1)[-1])
        body = json.loads(request.content) if request.content else {}
        if paths[-1] == "handshake":
            assert request.headers["X-Sync-Token"] == "PRIVATE_TOKEN"
            value = json.loads(credential.read_bytes())
            value.update(sync_token="PRIVATE_ROTATED_TOKEN", sync_interval_seconds=90)
            credential.write_text(json.dumps(value), encoding="utf-8")
            return httpx.Response(200, json={"target": {**source,
                "library_id": binding["target_library_id"], "node_id": binding["target_node_id"]}})
        assert request.headers["X-Sync-Token"] == "PRIVATE_ROTATED_TOKEN"
        if paths[-1] == "heartbeat":
            return httpx.Response(200, json={"lease": {"session_id": body["session_id"], "lease_token": str(uuid.uuid4()),
                "generation": 1, "lease_until": "2099-01-01T00:00:00.000Z"}, "renewed_claims": 0,
                "recovery": {"generation": 0, "revision": 0, "state": "held", "source_floor": None,
                             "stream": {"library_id": binding["target_library_id"], "library_epoch": 1}}})
        assert paths[-1] == "abandon"
        return httpx.Response(200, json={"binding": body["binding"], "operation_id": body["operation_id"], "state": "abandoned"})
    original = sync_client.SyncClient
    monkeypatch.setattr(sync_client, "SyncClient", lambda *args, **kwargs: original(
        *args, transport=httpx.MockTransport(handler), **kwargs))
    forbid_configuration(monkeypatch)
    assert admin.resume_abandon(profile, operation_id, sender_config=credential)["state"] == "abandoned"
    assert paths == ["handshake", "heartbeat", "abandon"]


@pytest.mark.parametrize("changed_stream,changed_source_epoch", [(False, False), (True, False), (False, True)])
def test_published_ack_gap_resumes_locally_and_cannot_advance_new_stream(home, monkeypatch, changed_stream, changed_source_epoch):
    profile, store, metadata = home
    envelope = store.read_publication(metadata)
    stream = envelope["recovery_stream"]
    store.observe_recovery({"generation": 0, "revision": 0, "state": "running", "source_floor": None, "stream": stream})
    value = {"publication_id": metadata["publication_id"], "source_version": envelope["snapshot"]["version"],
             "local_version": {**stream, "kb_revision": 9}, "committed_at": "2026-10-01T01:00:00Z", "replayed": False}
    request = store.record_published_terminal(metadata, value, recovery_generation=0, recovery_stream=stream)
    assert store.read()["pending_publication"] == metadata
    if changed_stream:
        restored = store.read()
        restored["recovery"] = {"generation": 2, "revision": 2, "state": "held", "source_floor": None,
                                 "stream": {**stream, "library_epoch": stream["library_epoch"] + 1}}
        restored["applied_recovery_generation"] = 1
        (profile.database.parent / "sync-state.json").write_text(json.dumps(restored), encoding="utf-8")
    if changed_source_epoch:
        with closing(sqlite3.connect(profile.database)) as writer:
            writer.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
            writer.commit()
    remote = Remote(home)
    forbid_configuration(monkeypatch)
    assert admin.resume_abandon(profile, request["operation_id"], client=remote)["state"] == "published"
    assert not remote.calls and store.read()["pending_publication"] is None
    state = store.read()
    if changed_stream:
        assert state["published_version"] is None and state["applied_recovery_generation"] == 1
        assert state["recovery"] == restored["recovery"]
    elif changed_source_epoch:
        assert state["published_version"] is None and state["applied_recovery_generation"] == 0
    else:
        assert state["published_version"] == envelope["snapshot"]["version"]
        assert state["applied_recovery_generation"] == 0


def test_published_marker_gap_queries_only_receipt_then_acks_with_private_source_proof(home, monkeypatch):
    profile, store, metadata = home
    envelope = store.read_publication(metadata)
    stream = envelope["recovery_stream"]
    store.observe_recovery({"generation": 0, "revision": 0, "state": "running", "source_floor": None, "stream": stream})
    value = {"publication_id": metadata["publication_id"], "source_version": envelope["snapshot"]["version"],
             "local_version": {**stream, "kb_revision": 9}, "committed_at": "2026-10-01T01:00:00Z", "replayed": False}
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "commit_receipt", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic receipt gap")))
        with pytest.raises(RuntimeError):
            store.record_published_terminal(metadata, value, recovery_generation=0, recovery_stream=stream)
    request = store.publication_no_resend_request(metadata["publication_id"])
    credential = sender_file(home)
    remote = Remote(home)
    def queried(source, lease, *, binding, payload_sha256):
        remote.check_network("receipt")
        return {"state": "published", "binding": deepcopy(binding), "payload_sha256": payload_sha256, "receipt": deepcopy(value)}
    remote.publication_receipt = queried
    remote.abandon_uploads = lambda *args, **kwargs: pytest.fail("Published evidence cannot authorize abandonment")
    forbid_configuration(monkeypatch)
    assert admin.resume_abandon(profile, request["operation_id"], sender_config=credential, client=remote)["state"] == "published"
    assert remote.calls == ["handshake", "heartbeat", "receipt"]
    assert store.read()["pending_publication"] is None and store.read()["published_version"] == value["source_version"]


@pytest.mark.parametrize("command", ["sync-abandon", "sync-abandon-resume", "sync-retire-local"])
def test_missing_explicit_profiles_do_not_echo_private_arguments(command, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main([command, "--db", "PRIVATE_PATH_OR_SECRET"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "PRIVATE_PATH_OR_SECRET" not in captured.err
    assert json.loads(captured.err)["code"] == "SYNC_SENDER_ARGUMENT_INVALID"
