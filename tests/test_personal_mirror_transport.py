"""Durable original sender requests/blobs and real isolated receiver calls.

Only HTTP transport is substituted. Personal source facts, ready uploads,
committed captures, receiver preparation/body storage/commit and receipts use
their actual producers. This neither activates production sync nor establishes
cloud reading, sharing, primary handoff, deployment or browser acceptance.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
import os
import uuid

import httpx
import pytest

from reno import config, db, personal, personal_mirror_contract as facts
from reno import personal_mirror_transfer as receiver, personal_mirror_transfer_contract as wire
from reno import personal_mirror_transfer_files as receiver_files, personal_mirror_transfer_schema as receiver_schema
from reno import personal_mirror_transport as sender, storage_lifecycle as lifecycle, sync_access
from reno.sync_client import SyncClient
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_personal_mirror_transfer import mirror_env, make_prepare, BODY, NOW, table_state

URL = "https://personal-mirror.synthetic.invalid"


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def sender_env(mirror_env, monkeypatch):
    env = mirror_env
    monkeypatch.setattr(sender, "_now", lambda: NOW)
    def select_home():
        env["select"](env["source_root"], "home")
        local = config.local_path()
        values = json.loads(local.read_text(encoding="utf-8"))
        values.update(sync_cloud_url=URL, sync_target_library_id=env["receiver"]["library_id"],
            sync_target_node_id=env["receiver"]["node_id"], sync_token="synthetic-personal-mirror-test-token")
        local.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(config, "_cache", None)
    select_home()
    store = StateStore(env["source_data"], profile=env["source_profile"])
    binding = {"home_library_id": env["source"]["library_id"], "home_node_id": env["source"]["node_id"],
        "target_library_id": env["receiver"]["library_id"], "target_node_id": env["receiver"]["node_id"], "target_url": URL}
    con = db.connect(env["source_path"])
    version = db.knowledge_version(con)
    con.close()
    with store.exclusive():
        store.initialize(binding, version["library_epoch"])
        store.update({"session_id": env["lease"]["session_id"], "lease": deepcopy(env["lease"])})
    env.update(store=store, binding=binding, select_home=select_home, calls=[])
    return env


@contextmanager
def owned(env):
    # Actual receiver calls run in this test process. A real HTTP peer owns
    # its separate guard; here the actual group guard covers both profiles.
    with env["store"].exclusive(), lifecycle.shared_many([env["source_profile"], env["profile"]]):
        yield


def initialize(env):
    return sender.initialize_outbox_locked(store=env["store"])


def freeze(env, *, attachment=False, operation_id=None, snapshot_id=None, previous=None):
    if attachment:
        make_prepare(env, with_attachment=True)
        env["select_home"]()
    return sender.freeze_mirror_locked(store=env["store"], operation_id=operation_id or uid(),
        snapshot_id=snapshot_id or uid(), expected_head_operation_id=previous)


def request_path(env, operation_id):
    return env["source_data"] / sender.ROOT / sender._request_name(operation_id)


def deliver(env, frozen, *, client=None, check_cancelled=None):
    return sender.deliver_mirror_locked(store=env["store"], operation_id=frozen["operation_id"],
        expected_request_sha256=frozen["request_sha256"], client=client or actual_peer(env), check_cancelled=check_cancelled)


def actual_peer(env, *, fail=None, alter=None):
    """Actual HTTP client, bounded MockTransport and actual receiver producers."""
    def handle(request):
        raw = request.read()
        endpoint = request.url.path.rsplit("/", 1)[-1]
        env["calls"].append((endpoint, raw, request.headers.get("X-Reno-Personal-Mirror")))
        if fail is not None and fail(endpoint, "before"):
            raise httpx.ReadError("synthetic pre-response loss")
        if endpoint == "handshake":
            value = {}
        elif endpoint == "attachment":
            value = json.loads(base64.urlsafe_b64decode(request.headers["X-Reno-Personal-Mirror"]))
        else:
            value = json.loads(raw)
        env["select"](env["target_root"], "cloud")
        try:
            kwargs = None if endpoint == "handshake" else {
                "profile": env["profile"], "source": value["source"], "lease": value["lease"], "now": NOW}
            if endpoint == "handshake":
                con = db.connect(env["profile"].database)
                try:
                    result = {"target": sync_access.identity(con)}
                finally:
                    con.close()
            elif endpoint == "receipt":
                try:
                    result = receiver.mirror_receipt_locked(**kwargs, commit=value["commit"])
                except SyncError as exc:
                    if exc.code != "PERSONAL_MIRROR_PREPARE_REQUIRED" or exc.status != 404:
                        raise
                    result = None
                result = {"receipt": result}
            elif endpoint == "prepare":
                result = receiver.prepare_mirror_locked(**kwargs, prepare=value["prepare"])
            elif endpoint == "attachment":
                result = receiver_files.store_attachment_locked(**kwargs, operation_id=value["operation_id"],
                    expected_prepare_sha256=value["prepare_sha256"], attachment_id=value["attachment_id"], chunks=[raw])
            elif endpoint == "commit":
                result = receiver.commit_mirror_locked(**kwargs, commit=value["commit"])
            else:
                pytest.fail("Unexpected personal mirror endpoint")
        except SyncError as exc:
            return httpx.Response(exc.status, json={"code": exc.code, "detail": "synthetic rejected peer operation"})
        finally:
            env["select_home"]()
        if fail is not None and fail(endpoint, "after"):
            raise httpx.ReadError("synthetic response loss after actual peer commit")
        if alter is not None:
            result = alter(endpoint, deepcopy(result))
        return httpx.Response(200, json=result)
    return SyncClient(URL, token_provider=lambda: "synthetic-personal-mirror-test-token",
        transport=httpx.MockTransport(handle))


def test_reads_and_retries_require_explicit_facility_and_preserve_knowledge_state(sender_env):
    env = sender_env
    before = (env["source_data"] / "sync-state.json").read_bytes()
    with owned(env), pytest.raises(SyncError, match="transmission") as caught:
        sender.freeze_mirror_locked(store=env["store"], operation_id=uid(), snapshot_id=uid())
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_NOT_INITIALIZED"
    assert not (env["source_data"] / sender.ROOT).exists()
    assert (env["source_data"] / "sync-state.json").read_bytes() == before
    with owned(env):
        first, repeated = initialize(env), initialize(env)
    assert first == repeated and env["calls"] == []
    assert (env["source_data"] / "sync-state.json").read_bytes() == before


def test_zero_attachment_original_snapshot_and_receipt_are_durable_with_no_attachment_directory(sender_env):
    env = sender_env
    before = (env["source_data"] / "sync-state.json").read_bytes()
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        original = sender.original_operation_locked(store=env["store"], operation_id=frozen["operation_id"],
            expected_request_sha256=frozen["request_sha256"])
        validated = facts.validate_snapshot(original["prepare"]["snapshot_json"])
        assert set(validated.document["tables"]) == set(facts.TABLE_NAMES) and len(facts.TABLE_NAMES) == 10
        assert validated.document["attachment_manifest"] == []
        assert hashlib.sha256(original["request_bytes"]).hexdigest() == frozen["request_sha256"]
        result = deliver(env, frozen)
    assert result["snapshot_id"] == frozen["snapshot_id"]
    assert [call[0] for call in env["calls"]] == ["handshake", "receipt", "prepare", "commit"]
    assert not (env["source_data"] / sender.ROOT / sender._blob_directory(frozen["operation_id"])).exists()
    assert (env["source_data"] / "sync-state.json").read_bytes() == before


def test_frozen_ready_blob_uses_original_bytes_after_home_attachment_is_removed(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
        original = sender.original_operation_locked(store=env["store"], operation_id=frozen["operation_id"],
            expected_request_sha256=frozen["request_sha256"])
        descriptor = wire.attachment_manifest(original["prepare"])[0]
        source_body = env["source_data"] / "personal-attachments" / descriptor["storage_key"]
        source_body.unlink()
        result = deliver(env, frozen)
    uploads = [call for call in env["calls"] if call[0] == "attachment"]
    assert len(uploads) == 1 and uploads[0][1] == BODY
    assert result["counts"]["attachment_manifest"] == 1
    assert (env["data"] / "personal-attachments" / descriptor["storage_key"]).read_bytes() == BODY


@pytest.mark.parametrize("endpoint", ["prepare", "attachment", "commit"])
def test_network_failure_retries_same_original_uuid_request_sha_and_snapshot(sender_env, endpoint, monkeypatch):
    env = sender_env
    failed = False
    def fail(phase, when):
        nonlocal failed
        if phase == endpoint and when == "after" and not failed:
            failed = True
            return True
        return False
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
        original = request_path(env, frozen["operation_id"]).read_bytes()
        with pytest.raises(SyncError):
            deliver(env, frozen, client=actual_peer(env, fail=fail))
        def no_capture(**kwargs):
            pytest.fail("A retry recaptured changed source facts")
        monkeypatch.setattr(sender.capture, "capture_snapshot", no_capture)
        env["calls"].clear()
        result = deliver(env, frozen)
    assert failed and request_path(env, frozen["operation_id"]).read_bytes() == original
    assert result["operation_id"] == frozen["operation_id"] and result["snapshot_sha256"] == frozen["snapshot_sha256"]
    if endpoint == "commit":
        assert [call[0] for call in env["calls"]] == ["handshake", "receipt"]
    else:
        assert [call[0] for call in env["calls"]] == ["handshake", "receipt", "prepare", "attachment", "commit"]


def test_local_receipt_replay_is_zero_rpc_even_after_saved_lease_expires(sender_env, monkeypatch):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        first = deliver(env, frozen)
    monkeypatch.setattr(sender, "_now", lambda: NOW.replace(year=2027))
    env["calls"].clear()
    before = {item.name: item.read_bytes() for item in (env["source_data"] / sender.ROOT).iterdir() if item.is_file()}
    def no_rpc(*args, **kwargs):
        pytest.fail("Stored receipt replay made an RPC")
    monkeypatch.setattr(sender.SyncClient, "_request", no_rpc)
    with owned(env):
        replay = deliver(env, frozen)
    assert replay == {**first, "replayed": True} and env["calls"] == []
    assert {item.name: item.read_bytes() for item in (env["source_data"] / sender.ROOT).iterdir() if item.is_file()} == before


def test_request_publish_before_catalog_failure_recovers_original_bytes_without_recapture(sender_env, monkeypatch):
    env = sender_env
    operation, snapshot_id = uid(), uid()
    failure = RuntimeError("synthetic catalog interruption")
    original_replace = sender._Context.replace_catalog
    interrupted = False
    def stop_before_catalog(self, raw, value):
        nonlocal interrupted
        if value["entries"] and not interrupted:
            interrupted = True
            raise failure
        return original_replace(self, raw, value)
    with owned(env):
        initialize(env)
        monkeypatch.setattr(sender._Context, "replace_catalog", stop_before_catalog)
        with pytest.raises(RuntimeError) as caught:
            freeze(env, operation_id=operation, snapshot_id=snapshot_id)
        assert caught.value is failure
        original = request_path(env, operation).read_bytes()
        monkeypatch.setattr(sender._Context, "replace_catalog", original_replace)
        def no_capture(**kwargs):
            pytest.fail("The original orphan request was recaptured")
        monkeypatch.setattr(sender.capture, "capture_snapshot", no_capture)
        frozen = freeze(env, operation_id=operation, snapshot_id=snapshot_id)
        result = deliver(env, frozen)
    assert result["operation_id"] == operation and request_path(env, operation).read_bytes() == original


def test_cancelled_original_blob_freeze_resumes_same_snapshot_without_new_capture(sender_env, monkeypatch):
    env = sender_env
    make_prepare(env, with_attachment=True)
    env["select_home"]()
    operation, snapshot_id = uid(), uid()
    class Cancelled(Exception):
        pass
    def cancel_owned_temporary():
        directory = env["source_data"] / sender.ROOT / sender._blob_directory(operation)
        if directory.exists() and any(directory.glob("*.part")):
            raise Cancelled("controlled original body freeze cancellation")
    with owned(env):
        initialize(env)
        with pytest.raises(Cancelled):
            sender.freeze_mirror_locked(store=env["store"], operation_id=operation, snapshot_id=snapshot_id,
                check_cancelled=cancel_owned_temporary)
        original = request_path(env, operation).read_bytes()
        assert not (env["source_data"] / sender.ROOT / sender._blob_name(operation)).exists()
        def no_capture(**kwargs):
            pytest.fail("Interrupted original body freezing recaptured its snapshot")
        monkeypatch.setattr(sender.capture, "capture_snapshot", no_capture)
        frozen = freeze(env, operation_id=operation, snapshot_id=snapshot_id)
        result = deliver(env, frozen)
    assert result["snapshot_id"] == snapshot_id and request_path(env, operation).read_bytes() == original


def test_changed_source_after_capture_is_reopened_checked_and_cannot_be_recaptured(sender_env, monkeypatch):
    env = sender_env
    make_prepare(env, with_attachment=True)
    env["select_home"]()
    original_capture = sender.capture.capture_snapshot
    operation, snapshot_id = uid(), uid()
    def capture_then_change_actual_source(**kwargs):
        result = original_capture(**kwargs)
        descriptor = result.snapshot.document["attachment_manifest"][0]
        target = env["source_data"] / "personal-attachments" / descriptor["storage_key"]
        target.write_bytes(b"x" * len(BODY))
        return result
    with owned(env):
        initialize(env)
        monkeypatch.setattr(sender.capture, "capture_snapshot", capture_then_change_actual_source)
        with pytest.raises(SyncError):
            freeze(env, operation_id=operation, snapshot_id=snapshot_id)
        original = request_path(env, operation).read_bytes()
        def no_capture(**kwargs):
            pytest.fail("Changed original ready source was recaptured under the same UUID")
        monkeypatch.setattr(sender.capture, "capture_snapshot", no_capture)
        with pytest.raises(SyncError):
            freeze(env, operation_id=operation, snapshot_id=snapshot_id)
    assert env["calls"] == [] and request_path(env, operation).read_bytes() == original
    assert not (env["source_data"] / sender.ROOT / sender._blob_name(operation)).exists()


def test_receipt_publish_before_catalog_failure_repairs_head_with_zero_rpc(sender_env, monkeypatch):
    env = sender_env
    original_advance = sender._advance
    failure = RuntimeError("synthetic receipt-to-catalog interruption")
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        def interrupted(*args, **kwargs):
            raise failure
        monkeypatch.setattr(sender, "_advance", interrupted)
        with pytest.raises(RuntimeError) as caught:
            deliver(env, frozen)
        assert caught.value is failure
        receipt_file = env["source_data"] / sender.ROOT / sender._receipt_name(frozen["operation_id"])
        before = receipt_file.read_bytes()
        monkeypatch.setattr(sender, "_advance", original_advance)
        env["calls"].clear()
        def no_rpc(*args, **kwargs):
            pytest.fail("Original persisted receipt repair called the peer")
        monkeypatch.setattr(sender.SyncClient, "_request", no_rpc)
        result = deliver(env, frozen)
    assert result["replayed"] is True and env["calls"] == [] and receipt_file.read_bytes() == before


def test_new_snapshot_requires_committed_original_head_and_preserves_raw_fact_changes(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        first = freeze(env)
        with pytest.raises(SyncError):
            freeze(env)
        deliver(env, first)
        with pytest.raises(SyncError):
            freeze(env, previous=uid())
    personal.create_source(env["project_id"], {"operation_id": uid(), "primary_epoch": env["primary_epoch"],
        "changes": {"source_type": "manual_note", "title": "next actual source fact"}})
    with owned(env):
        second = freeze(env, previous=first["operation_id"])
        result = deliver(env, second)
    assert result["installed_revision"] == 2 and result["snapshot_sha256"] != first["snapshot_sha256"]


@pytest.mark.parametrize("change", ["request_sha", "snapshot_id", "source_epoch", "peer_config"])
def test_changed_original_selectors_or_live_primary_peer_fail_before_rpc(sender_env, change):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        if change == "snapshot_id":
            with pytest.raises(SyncError):
                freeze(env, operation_id=frozen["operation_id"], snapshot_id=uid())
        elif change == "request_sha":
            with pytest.raises(SyncError):
                sender.deliver_mirror_locked(store=env["store"], operation_id=frozen["operation_id"],
                    expected_request_sha256="f" * 64, client=actual_peer(env))
        elif change == "source_epoch":
            con = db.connect(env["source_path"])
            con.execute("UPDATE meta SET value='2' WHERE key='primary_epoch'")
            con.commit()
            con.close()
            with pytest.raises(SyncError):
                deliver(env, frozen)
        else:
            local = config.local_path()
            values = json.loads(local.read_text(encoding="utf-8"))
            values["sync_target_node_id"] = uid()
            local.write_text(json.dumps(values), encoding="utf-8")
            with pytest.raises(SyncError):
                deliver(env, frozen)
    assert env["calls"] == []


@pytest.mark.parametrize("change", ["bytes", "replace", "manifest", "missing_manifest"])
def test_frozen_blob_changes_are_preserved_and_never_repaired_from_new_home_bytes(sender_env, change):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
        request = json.loads(request_path(env, frozen["operation_id"]).read_bytes())
        descriptor = wire.attachment_manifest(request["prepare"])[0]
        directory = env["source_data"] / sender.ROOT
        blob = directory / sender._blob_directory(frozen["operation_id"]) / facts.storage_key(descriptor["storage_key"]).name
        manifest_path = directory / sender._blob_name(frozen["operation_id"])
        if change == "bytes":
            blob.write_bytes(b"x" * len(BODY))
        elif change == "replace":
            blob.rename(blob.with_suffix(".original"))
            blob.write_bytes(BODY)
        elif change == "manifest":
            value = json.loads(manifest_path.read_bytes())
            value["entries"][0]["file_identity"]["inode"] = "999999"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
        else:
            manifest_path.unlink()
        after = request_path(env, frozen["operation_id"]).read_bytes()
        with pytest.raises(SyncError):
            deliver(env, frozen)
    assert request_path(env, frozen["operation_id"]).read_bytes() == after
    assert [call[0] for call in env["calls"]] == ["handshake", "receipt"]
    if change == "bytes":
        assert blob.read_bytes() == b"x" * len(BODY)
    if change == "missing_manifest":
        assert not manifest_path.exists()


def test_uncertain_or_wrong_remote_receipt_never_advances_original_catalog(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        original = (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes()
        def change(endpoint, value):
            if endpoint == "commit":
                value["operation_id"] = uid()
            return value
        with pytest.raises(SyncError):
            deliver(env, frozen, client=actual_peer(env, alter=change))
    assert (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes() == original
    assert not (env["source_data"] / sender.ROOT / sender._receipt_name(frozen["operation_id"])).exists()


def test_unknown_outbox_files_are_preserved_and_count_toward_capacity(sender_env, monkeypatch):
    env = sender_env
    with owned(env):
        initialize(env)
        unknown = env["source_data"] / sender.ROOT / "unknown-retained.txt"
        unknown.write_bytes(b"original unknown")
        if os.name != "nt":
            unknown.chmod(0o600)
        monkeypatch.setattr(sender, "MAX_OUTBOX_BYTES", 1)
        with pytest.raises(SyncError):
            freeze(env)
    assert unknown.read_bytes() == b"original unknown" and env["calls"] == []


def test_independent_actual_outbox_lock_blocks_a_second_open_and_missing_lock_is_not_created(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        with sender._context(env["store"]):
            with pytest.raises(SyncError) as caught:
                with sender._context(env["store"]):
                    pytest.fail("Two callers acquired the same independent actual outbox lock")
            assert caught.value.code == "PERSONAL_MIRROR_SENDER_BUSY"
        lock = env["source_data"] / sender.ROOT / sender.LOCK
        lock.unlink()
        with pytest.raises(SyncError):
            freeze(env)
    assert not lock.exists()


def test_a_public_client_dto_cannot_substitute_for_actual_http_delivery(sender_env):
    env = sender_env
    class FakeClient:
        def _request(self, *args, **kwargs):
            pytest.fail("The fake public client became a remote receipt producer")
    with owned(env):
        initialize(env)
        frozen = freeze(env)
        before = (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes()
        with pytest.raises(SyncError) as caught:
            deliver(env, frozen, client=FakeClient())
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_HTTP_CLIENT_REQUIRED"
    assert env["calls"] == [] and (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes() == before


def test_actual_wrong_peer_handshake_is_rejected_before_any_personal_request_is_sent(sender_env):
    env = sender_env
    def change(endpoint, value):
        if endpoint == "handshake":
            value["target"]["node_id"] = uid()
        return value
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
        before = (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes()
        with pytest.raises(SyncError) as caught:
            deliver(env, frozen, client=actual_peer(env, alter=change))
    assert caught.value.code == "PERSONAL_MIRROR_SENDER_PEER_CHANGED"
    assert [call[0] for call in env["calls"]] == ["handshake"] and env["calls"][0][1] == b""
    assert (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes() == before


@pytest.mark.parametrize("phase", ["receipt", "prepare"])
@pytest.mark.parametrize("changed_field", ["_url", "_transport"])
@pytest.mark.parametrize("callback", ["token_provider", "check_cancelled"])
def test_supported_callback_cannot_redirect_a_handshaken_client_or_replace_its_actual_transport(
        sender_env, phase, changed_field, callback):
    env = sender_env
    original_peer = actual_peer(env)
    peer_b_calls = []
    def peer_b(request):
        peer_b_calls.append((str(request.url), request.read()))
        return httpx.Response(200, json={"receipt": None})
    replacement_transport = httpx.MockTransport(peer_b)
    def route(request):
        if request.url.host != httpx.URL(URL).host:
            return peer_b(request)
        return original_peer._transport.handle_request(request)
    original_transport = httpx.MockTransport(route)
    count, changed, client = 0, False, None
    trigger = 2 if phase == "receipt" else 3
    def mutate():
        nonlocal changed
        if changed or count != trigger:
            return
        if changed_field == "_url":
            client._url = "https://unhandshaken-peer-b.synthetic.invalid"
        else:
            client._transport = replacement_transport
        changed = True
    def token_provider():
        nonlocal count
        count += 1
        if callback == "token_provider":
            mutate()
        return "synthetic-personal-mirror-test-token"
    def check_cancelled():
        if callback == "check_cancelled":
            # The existing SyncClient deadline calls this after token
            # retrieval and before capturing request_base/the transport.
            mutate()
    client = SyncClient(URL, token_provider=token_provider, transport=original_transport,
        check_cancelled=check_cancelled)
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
        original_request = request_path(env, frozen["operation_id"]).read_bytes()
        original_mtime = request_path(env, frozen["operation_id"]).stat().st_mtime_ns
        original_catalog = (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes()
        with pytest.raises(SyncError):
            deliver(env, frozen, client=client)
        assert changed and peer_b_calls == []
        assert [call[0] for call in env["calls"]] == (["handshake"] if phase == "receipt" else ["handshake", "receipt"])
        assert (env["source_data"] / sender.ROOT / sender.CATALOG).read_bytes() == original_catalog
        assert request_path(env, frozen["operation_id"]).read_bytes() == original_request
        assert request_path(env, frozen["operation_id"]).stat().st_mtime_ns == original_mtime
        client._url, client._transport = URL, original_transport
        result = deliver(env, frozen, client=client)
    assert result["operation_id"] == frozen["operation_id"] and peer_b_calls == []
    assert request_path(env, frozen["operation_id"]).read_bytes() == original_request
    assert request_path(env, frozen["operation_id"]).stat().st_mtime_ns == original_mtime


def test_unregistered_original_request_blocks_new_capture_until_its_exact_operation_resumes(sender_env, monkeypatch):
    env = sender_env
    operation_a, snapshot_a, operation_b, snapshot_b = uid(), uid(), uid(), uid()
    actual_replace, actual_capture = sender._Context.replace_catalog, sender.capture.capture_snapshot
    failure = RuntimeError("controlled original request-to-catalog gap")
    interrupted = False
    def stop_original_registration(self, raw, value):
        nonlocal interrupted
        if value["entries"] and not interrupted:
            interrupted = True
            raise failure
        return actual_replace(self, raw, value)
    with owned(env):
        initialize(env)
        monkeypatch.setattr(sender._Context, "replace_catalog", stop_original_registration)
        with pytest.raises(RuntimeError) as caught:
            freeze(env, operation_id=operation_a, snapshot_id=snapshot_a)
        assert caught.value is failure
        original_request = request_path(env, operation_a).read_bytes()
        original_sha = hashlib.sha256(original_request).hexdigest()
        original_mtime = request_path(env, operation_a).stat().st_mtime_ns
        monkeypatch.setattr(sender._Context, "replace_catalog", actual_replace)
        def no_capture(**kwargs):
            pytest.fail("A new UUID captured facts while an original request remained unregistered")
        monkeypatch.setattr(sender.capture, "capture_snapshot", no_capture)
        with pytest.raises(SyncError) as blocked:
            freeze(env, operation_id=operation_b, snapshot_id=snapshot_b)
        assert blocked.value.code == "PERSONAL_MIRROR_SENDER_ORIGINAL_OPERATION_REQUIRED"
        assert not request_path(env, operation_b).exists() and env["calls"] == []
        assert request_path(env, operation_a).read_bytes() == original_request
        assert request_path(env, operation_a).stat().st_mtime_ns == original_mtime
        original = freeze(env, operation_id=operation_a, snapshot_id=snapshot_a)
        assert original["request_sha256"] == original_sha
        first = deliver(env, original)
        with pytest.raises(SyncError):
            freeze(env, operation_id=operation_b, snapshot_id=snapshot_b)
        monkeypatch.setattr(sender.capture, "capture_snapshot", actual_capture)
        next_request = freeze(env, operation_id=operation_b, snapshot_id=snapshot_b, previous=operation_a)
        second = deliver(env, next_request)
    assert first["operation_id"] == operation_a and second["operation_id"] == operation_b
    assert second["installed_revision"] == 2
    assert request_path(env, operation_a).read_bytes() == original_request
    assert request_path(env, operation_a).stat().st_mtime_ns == original_mtime
