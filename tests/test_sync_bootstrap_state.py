"""Independent bootstrap facts cannot finish or replace an original source job."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import sync_state
from reno.sync_protocol import SyncError
from tests.test_sync_source_control_state import controlled, histories
from tests.test_sync_source_execution_state import request, receipt
from tests.test_sync_state import claim, snapshot, store
from tests.test_sync_snapshot import payload, source


def bootstrap(store, *, content=None, bindings=None, freeze=True):
    core, _ = controlled(store)
    old = snapshot()
    old["version"]["library_id"] = core["binding"]["home_library_id"]
    original = store.write_publication(old, [], recovery_stream=core["recovery_stream"])
    body = request(core)
    store.install_budget_defer_request(body)
    store.record_budget_defer_receipt(body["request_id"], receipt(body))
    store.update({"pending_publication": original, "claims": [claim(core["item_id"])],
        "work": {"item_id": core["item_id"], "lease_generation": 3, "video_id": "unfinished", "phase": "downloaded"}})
    before = store.read()
    recovery = {**before["recovery"], "generation": 1, "revision": 1, "state": "resume_pending"}
    store.observe_recovery(recovery)
    value = deepcopy(content) if content is not None else snapshot()
    value["publication_id"] = str(uuid.uuid4())
    value["version"] = {"library_id": core["binding"]["home_library_id"], "library_epoch": 1, "kb_revision": 5}
    attempt = {"generation": 1, "publication_id": value["publication_id"], "version": value["version"], "stream": recovery["stream"]}
    store.update({"recovery_attempt": attempt})
    metadata = store.write_publication(value, bindings or [], recovery_generation=1, recovery_stream=attempt["stream"]) if freeze else None
    return core, before, value, attempt, metadata


def terminal(store, value, metadata, attempt):
    response = {"publication_id": metadata["publication_id"], "source_version": value["version"],
        "local_version": {**attempt["stream"], "kb_revision": 1}, "committed_at": "2026-10-02T00:00:00.000Z", "replayed": False}
    return store.record_published_terminal(metadata, response, recovery_generation=attempt["generation"],
        recovery_stream=attempt["stream"], recovery_bootstrap=True)


def preserved(store, original):
    state = store.read()
    assert all(state[key] == original[key] for key in ("source_execution", "source_control_pending", "source_admission",
        "budget_pending", "claims", "work", "pending_publication", "uploaded_media", "publication_media"))


def test_bootstrap_ack_requires_exact_marker_and_preserves_all_original_work(store):
    core, before, value, attempt, metadata = bootstrap(store)
    assert store.install_recovery_publication(metadata, attempt=attempt)
    assert store.recovery_publication() == metadata
    frozen = (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    published = terminal(store, value, metadata, attempt)
    assert store.pending_no_resend_requests() == [published]
    after = store.acknowledge_recovery_publication(published, current_version=value["version"])
    assert after["recovery_attempt"] is None and after["applied_recovery_generation"] == 1
    assert after["published_version"] == value["version"]
    preserved(store, before)
    saved = list(histories(store).values())
    assert len(saved) == 1
    history = json.loads(saved[0])
    assert sync_state.validate_source_history4(history) == history
    assert history["reason"] == "recovery" and history["source_terminal"] is None
    assert history["recovery_attempt"] == attempt and history["source_execution"] == core
    assert (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == frozen
    assert store.pending_no_resend_requests() == []


@pytest.mark.parametrize("field", ["publication_id", "version", "generation", "stream"])
def test_bootstrap_marker_mismatch_cannot_record_terminal_or_advance_ack(store, field):
    _, before, value, attempt, metadata = bootstrap(store)
    changed = deepcopy(attempt)
    if field == "publication_id":
        changed[field] = str(uuid.uuid4())
    elif field == "version":
        changed[field]["kb_revision"] += 1
    elif field == "generation":
        changed[field] += 1
    else:
        changed[field]["library_epoch"] += 1
    store.update({"recovery_attempt": changed})
    raw = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        terminal(store, value, metadata, attempt)
    assert (store.data_dir / "sync-state.json").read_bytes() == raw
    preserved(store, before)


def test_bootstrap_rejects_valid_wire_claims_and_cannot_complete_them(store, payload):
    claimed = [{"claim": claim(7)["claim"], "video_id": payload["videos"][0]["video_id"]}]
    _, before, value, attempt, metadata = bootstrap(store, content=payload, bindings=claimed)
    assert store.read_publication(metadata)["claims"] == claimed
    with pytest.raises(SyncError):
        store.install_recovery_publication(metadata, attempt=attempt)
    with pytest.raises(SyncError):
        terminal(store, value, metadata, attempt)
    preserved(store, before)


def test_ordinary_terminal_cannot_borrow_the_independent_bootstrap_marker(store):
    _, before, value, attempt, metadata = bootstrap(store)
    response = {"publication_id": metadata["publication_id"], "source_version": value["version"],
        "local_version": {**attempt["stream"], "kb_revision": 1}, "committed_at": "2026-10-02T00:00:00.000Z", "replayed": False}
    with pytest.raises(SyncError) as error:
        store.record_published_terminal(metadata, response, recovery_generation=1, recovery_stream=attempt["stream"])
    assert error.value.code == "SYNC_NO_RESEND_BINDING_UNPROVEN"
    preserved(store, before)


def test_bootstrap_receipt_cannot_ack_a_forged_request_or_missing_marker(store):
    _, before, value, attempt, metadata = bootstrap(store)
    published = terminal(store, value, metadata, attempt)
    forged = {**published, "payload_sha256": "f" * 64}
    with pytest.raises(SyncError):
        store.acknowledge_recovery_publication(forged, current_version=value["version"])
    store.update({"recovery_attempt": None})
    with pytest.raises(SyncError):
        store.acknowledge_recovery_publication(published, current_version=value["version"])
    assert store.read()["applied_recovery_generation"] == 0
    preserved(store, before)


def test_bootstrap_history_precedes_state_clear_and_interrupted_ack_retains_original_inputs(store, monkeypatch):
    _, before, value, attempt, metadata = bootstrap(store)
    published = terminal(store, value, metadata, attempt)
    raw = (store.data_dir / "sync-state.json").read_bytes()
    original = sync_state._write
    def interrupted(directory, name, data, **options):
        if name == "sync-state.json":
            raise RuntimeError("synthetic bootstrap ack interruption")
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", interrupted)
    with pytest.raises(RuntimeError):
        store.acknowledge_recovery_publication(published, current_version=value["version"])
    assert (store.data_dir / "sync-state.json").read_bytes() == raw
    assert len(histories(store)) == 1
    assert store.pending_no_resend_requests() == [published]
    preserved(store, before)
    monkeypatch.setattr(sync_state, "_write", original)
    store.acknowledge_recovery_publication(published, current_version=value["version"])
    preserved(store, before)


def test_late_bootstrap_receipt_ack_preserves_new_generation_and_hold(store):
    _, before, value, attempt, metadata = bootstrap(store)
    published = terminal(store, value, metadata, attempt)
    held = {**store.read()["recovery"], "generation": 2, "revision": 2, "state": "held"}
    store.observe_recovery(held)
    after = store.acknowledge_recovery_publication(published, current_version=value["version"])
    assert after["recovery"] == held and after["applied_recovery_generation"] == 1
    preserved(store, before)


def test_restored_local_epoch_cannot_apply_a_previous_bootstrap_receipt(store):
    _, before, value, attempt, metadata = bootstrap(store)
    published = terminal(store, value, metadata, attempt)
    store.update({"local_epoch": 2})
    raw = (store.data_dir / "sync-state.json").read_bytes()
    after = store.acknowledge_recovery_publication(published, current_version={**value["version"], "library_epoch": 2})
    assert after["recovery_attempt"] == attempt and after["applied_recovery_generation"] == 0
    assert (store.data_dir / "sync-state.json").read_bytes() == raw
    preserved(store, before)


def test_remote_epoch_change_archives_old_attempt_but_preserves_original_source_pointer_and_wait(store):
    _, before, value, attempt, metadata = bootstrap(store)
    published = terminal(store, value, metadata, attempt)
    changed = {**store.read()["recovery"], "revision": 2, "stream": {**attempt["stream"], "library_epoch": 2}}
    after = store.observe_recovery(changed)
    assert after["recovery_attempt"] is None and after["applied_recovery_generation"] == 0
    preserved(store, before)
    assert store.no_resend_receipt(published["operation_id"]) is not None
    history = json.loads(next(iter(histories(store).values())))
    assert history["recovery_attempt"] == attempt and history["pending_publication"] == before["pending_publication"]


def test_bootstrap_preserves_both_legacy_and_publication_media_cache_roots(store, payload):
    descriptor = payload["videos"][0]["media"]
    store.update({"uploaded_media": {descriptor["video_id"]: descriptor}})
    _, before, value, attempt, metadata = bootstrap(store, content=payload)
    binding = {"publication_id": metadata["publication_id"], "source_node_id": before["binding"]["home_node_id"],
        "source_version": value["version"], "recovery_generation": 1, "recovery_stream": attempt["stream"]}
    store.record_uploaded(binding, descriptor)
    cache = deepcopy(store.read()["publication_media"])
    published = terminal(store, value, metadata, attempt)
    store.acknowledge_recovery_publication(published, current_version=value["version"])
    assert store.read()["publication_media"] == cache
    assert store.read()["uploaded_media"] == before["uploaded_media"]


def test_unfrozen_version_refresh_keeps_original_uuid_work_and_fence(store):
    _, before, value, attempt, metadata = bootstrap(store, freeze=False)
    assert metadata is None
    current = {**value["version"], "kb_revision": 6}
    updated = store.refresh_unfrozen_recovery_attempt(current, attempt=attempt)
    assert updated == {**attempt, "version": current}
    assert store.read()["recovery_attempt"] == updated
    assert store.find_publication(attempt["publication_id"]) is None
    preserved(store, before)


@pytest.mark.parametrize("fact", ["outbox", "receipt", "media_cache", "source_pointer"])
def test_frozen_or_permanent_fact_prevents_same_uuid_version_refresh_even_after_file_loss(store, payload, fact):
    _, before, value, attempt, metadata = bootstrap(store, content=payload)
    if fact == "receipt":
        terminal(store, value, metadata, attempt)
    elif fact == "media_cache":
        binding = {"publication_id": metadata["publication_id"], "source_node_id": before["binding"]["home_node_id"],
            "source_version": value["version"], "recovery_generation": 1, "recovery_stream": attempt["stream"]}
        store.record_uploaded(binding, value["videos"][0]["media"])
    elif fact == "source_pointer":
        store.update({"pending_publication": metadata})
    if fact != "outbox":
        # Simulate a restored/missing file while the independent original
        # evidence survives. Absence of that file cannot grant a new payload.
        (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).unlink()
    raw = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.refresh_unfrozen_recovery_attempt({**value["version"], "kb_revision": 6}, attempt=attempt)
    assert (store.data_dir / "sync-state.json").read_bytes() == raw


@pytest.mark.parametrize("changed", ["marker", "generation", "stream", "hold", "epoch", "backward"])
def test_unfrozen_version_refresh_rechecks_current_control_and_version(store, changed):
    _, before, value, attempt, _ = bootstrap(store, freeze=False)
    current = {**value["version"], "kb_revision": 6}
    if changed == "marker":
        store.update({"recovery_attempt": {**attempt, "publication_id": str(uuid.uuid4())}})
    elif changed in {"generation", "stream", "hold"}:
        recovery = deepcopy(store.read()["recovery"])
        recovery["revision"] += 1
        if changed == "generation":
            recovery["generation"] += 1
        elif changed == "stream":
            recovery["stream"]["library_epoch"] += 1
        else:
            recovery["state"] = "held"
        store.observe_recovery(recovery)
    elif changed == "epoch":
        current["library_epoch"] += 1
    else:
        current["kb_revision"] = 4
    raw = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.refresh_unfrozen_recovery_attempt(current, attempt=attempt)
    assert (store.data_dir / "sync-state.json").read_bytes() == raw
    preserved(store, before)
