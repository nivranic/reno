"""Successful exact publication receipts close immutable retries permanently."""
from copy import deepcopy

import pytest

from reno import sync_no_resend as ledger, sync_state
from reno.sync_protocol import SyncError
from tests.test_sync_no_resend import bundle, files


def receipt(metadata, snapshot, stream):
    return {"publication_id": metadata["publication_id"], "source_version": snapshot["version"],
        "local_version": {**stream, "kb_revision": 9}, "committed_at": "2026-10-01T01:00:00Z", "replayed": False}


def record(bundle, value=None):
    _, store, metadata, snapshot, stream = bundle
    return store.record_published_terminal(metadata, value or receipt(metadata, snapshot, stream),
                                          recovery_generation=0, recovery_stream=stream)


def test_exact_success_persists_terminal_without_detach_or_recovery_advance(bundle):
    _, store, metadata, snapshot, stream = bundle
    before = store.read()
    raw = (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    request = record(bundle)
    assert request["reason"] == "published_receipt"
    assert store.read()["pending_publication"] == metadata
    for key in ("recovery_attempt", "applied_recovery_generation", "claims", "work", "published_version"):
        assert store.read()[key] == before[key]
    assert store.no_resend_receipt(request["operation_id"])["state"] == "published"
    assert record(bundle) == request
    assert (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == raw
    with pytest.raises(SyncError) as caught:
        store.assert_sendable(metadata["publication_id"])
    assert caught.value.code == "SYNC_NO_RESEND_FORBIDDEN"


def test_new_terminal_marker_requires_exact_current_pending_publication(bundle):
    _, store, *_ = bundle
    store.update({"pending_publication": None, "recovery_attempt": None})
    before = files(store.data_dir)
    with pytest.raises(SyncError) as caught:
        record(bundle)
    assert caught.value.code == "SYNC_NO_RESEND_BINDING_UNPROVEN"
    assert files(store.data_dir) == before


@pytest.mark.parametrize("field", ["source_epoch", "target_epoch", "publication", "typed_epoch"])
def test_bad_terminal_evidence_fails_before_any_capability_or_marker(bundle, field):
    _, store, metadata, snapshot, stream = bundle
    value = deepcopy(receipt(metadata, snapshot, stream))
    if field == "source_epoch":
        value["source_version"]["library_epoch"] += 1
    elif field == "target_epoch":
        value["local_version"]["library_epoch"] += 1
    elif field == "typed_epoch":
        value["local_version"]["library_epoch"] = True
    else:
        value["publication_id"] = "00000000-0000-4000-8000-000000000099"
    before = files(store.data_dir)
    with pytest.raises(SyncError):
        record(bundle, value)
    assert files(store.data_dir) == before
    assert not (store.data_dir / ledger.WITNESS).exists()


@pytest.mark.parametrize("directory", ["publications", "receipts"])
def test_exact_success_repairs_original_reserved_uuid_without_detach(bundle, monkeypatch, directory):
    _, store, metadata, *_ = bundle
    original = sync_state._write
    def interrupt(folder, name, data, **options):
        if folder.path.name == directory:
            raise RuntimeError("synthetic durable terminal interruption")
        return original(folder, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError):
        record(bundle)
    request = store.publication_no_resend_request(metadata["publication_id"], allow_incomplete=True)
    assert request["reason"] == "published_receipt"
    assert store.read()["pending_publication"] == metadata
    if directory == "receipts":
        assert store.no_resend_receipt(request["operation_id"], allow_incomplete=True)["state"] == "published"
    monkeypatch.setattr(sync_state, "_write", original)
    assert store.resume_no_resend_commit(request["operation_id"]) == request
    assert store.read()["pending_publication"] == metadata
    assert record(bundle) == request
    assert store.read()["pending_publication"] == metadata
    assert store.pending_no_resend_requests() == []


def test_known_published_pending_ack_gap_is_exposed_only_to_authorized_repair_collection(bundle):
    _, store, *_ = bundle
    request = record(bundle)
    assert store.pending_no_resend_requests() == []
    assert store.pending_no_resend_requests(allow_incomplete=True) == [request]


def test_published_terminal_keeps_caller_cancellation_identity(bundle):
    _, store, *_ = bundle
    sentinel = SyncError("synthetic callback cancellation", "SYNC_CANCELLED", 409)
    calls = [0]
    def cancel():
        calls[0] += 1
        if calls[0] >= 2:
            raise sentinel
    store.check_cancelled = cancel
    with pytest.raises(SyncError) as caught:
        record(bundle)
    assert caught.value is sentinel
    assert not (store.data_dir / ledger.WITNESS).exists()
