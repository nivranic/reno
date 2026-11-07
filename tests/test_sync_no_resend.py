"""Permanent decisions survive independent state restoration and crash gaps."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_no_resend as ledger, sync_state
from reno.sync_protocol import SyncError


def peer():
    return {**{key: str(uuid.uuid4()) for key in (
        "home_library_id", "home_node_id", "target_library_id", "target_node_id")},
        "target_url": "https://sync.example.test/private-prefix"}


@pytest.fixture
def bundle(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    binding = peer()
    store.initialize(binding, 1)
    snapshot = {"publication_id": str(uuid.uuid4()),
                "version": {"library_id": binding["home_library_id"], "library_epoch": 1, "kb_revision": 4},
                "videos": [], "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    stream = {"library_id": binding["target_library_id"], "library_epoch": 1}
    metadata = store.write_publication(snapshot, [], recovery_stream=stream)
    attempt = {"publication_id": metadata["publication_id"], "version": snapshot["version"], "generation": 0, "stream": stream}
    store.update({"pending_publication": metadata, "recovery_attempt": attempt})
    return profile, store, metadata, snapshot, stream


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def apply(bundle, operation_id=None):
    _, store, metadata, _, _ = bundle
    operation_id = operation_id or str(uuid.uuid4())
    preview = store.preview_abandon(metadata["publication_id"])
    request = store.commit_no_resend(metadata["publication_id"], expected_digest=preview["digest"], operation_id=operation_id)
    return request


def response(request):
    return {"binding": request["binding"], "operation_id": request["operation_id"], "state": "abandoned"}


def test_preview_is_readonly_and_does_not_initialize_ledger(bundle):
    profile, store, metadata, _, _ = bundle
    before = files(profile.database.parent.parent)
    preview = store.preview_abandon(metadata["publication_id"])
    assert preview["stage"] == "sendable" and len(preview["digest"]) == 64
    assert files(profile.database.parent.parent) == before
    assert not (profile.database.parent / ledger.WITNESS).exists()
    assert "private-prefix" not in json.dumps(preview)


def test_stale_cas_rejects_before_activation_or_marker(bundle):
    profile, store, metadata, _, _ = bundle
    before = files(profile.database.parent.parent)
    with pytest.raises(SyncError) as exc:
        store.commit_no_resend(metadata["publication_id"], expected_digest="0" * 64, operation_id=str(uuid.uuid4()))
    assert exc.value.code == "SYNC_NO_RESEND_STALE_PREVIEW"
    # Ownership may create its existing lock, but no activation/evidence writes.
    assert not (profile.database.parent / ledger.WITNESS).exists()
    assert store.read()["format_version"] == 1
    assert all(files(profile.database.parent.parent)[key] == value for key, value in before.items())


def test_commit_preserves_outbox_and_detaches_only_exact_pointers(bundle):
    profile, store, metadata, _, _ = bundle
    original = (profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    before = store.read()
    request = apply(bundle)
    state = store.read()
    assert state["format_version"] == 2
    assert state["pending_publication"] is None and state["recovery_attempt"] is None
    for key in ("work", "claims", "uploaded_media", "applied_recovery_generation", "published_version"):
        assert state[key] == before[key]
    assert (profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == original
    assert request["metadata"] == metadata
    assert "private-prefix" not in json.dumps(request)
    assert store.pending_no_resend_requests() == [request]
    with pytest.raises(SyncError, match="Permanent sender") as exc:
        store.assert_sendable(metadata["publication_id"])
    assert exc.value.code == "SYNC_NO_RESEND_FORBIDDEN"


def test_marker_detach_crash_preserves_readability_and_all_install_gates(bundle, monkeypatch):
    _, store, metadata, snapshot, stream = bundle
    sentinel = RuntimeError("synthetic interruption")
    original_detach = store.detach_no_resend
    monkeypatch.setattr(store, "detach_no_resend", lambda operation_id: (_ for _ in ()).throw(sentinel))
    with pytest.raises(RuntimeError) as exc:
        apply(bundle)
    assert exc.value is sentinel
    assert store.read()["pending_publication"] == metadata
    operation_id = store.pending_no_resend_requests()[0]["operation_id"]
    store.update({"seeded": True})  # Reading/heartbeat-like changes must not self-lock.
    operations = [lambda: store.write_publication(snapshot, [], recovery_stream=stream),
                  lambda: store.find_publication(metadata["publication_id"]),
                  lambda: store.install_publication(metadata, recovery_generation=0, recovery_stream=stream),
                  lambda: store.update({"pending_publication": {**metadata, "byte_size": metadata["byte_size"] + 1}})]
    for call in operations:
        with pytest.raises(SyncError) as exc:
            call()
        assert exc.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    monkeypatch.setattr(store, "detach_no_resend", original_detach)
    store.detach_no_resend(operation_id)
    assert store.read()["pending_publication"] is None


def test_restored_v1_state_cannot_resurrect_marker(bundle):
    profile, store, metadata, _, _ = bundle
    old = (profile.database.parent / "sync-state.json").read_bytes()
    apply(bundle)
    (profile.database.parent / "sync-state.json").write_bytes(old)
    assert store.read()["format_version"] == 1
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(metadata["publication_id"])
    assert exc.value.code == "SYNC_NO_RESEND_FORBIDDEN"
    # Explicit detach is safe even when independent state was restored.
    store.detach_no_resend(store.pending_no_resend_requests()[0]["operation_id"])
    assert store.read()["pending_publication"] is None


def test_orphan_outbox_cannot_borrow_current_node_binding(bundle):
    profile, store, metadata, _, _ = bundle
    store.update({"pending_publication": None, "recovery_attempt": None})
    with pytest.raises(SyncError) as exc:
        store.preview_abandon(metadata["publication_id"])
    assert exc.value.code == "SYNC_NO_RESEND_BINDING_UNPROVEN"
    assert not (profile.database.parent / ledger.WITNESS).exists()


@pytest.mark.parametrize("target", ["catalog", "seal", "state"])
def test_activation_crash_resumes_same_contract_without_rewriting_outbox(bundle, monkeypatch, target):
    profile, store, metadata, _, _ = bundle
    original = sync_state._write
    sentinel = RuntimeError("synthetic activation interruption")
    def fail(directory, name, data, **options):
        if name == {"catalog": "catalog.json", "seal": ledger.SEAL, "state": "sync-state.json"}[target]:
            raise sentinel
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    outbox = (profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    with pytest.raises(RuntimeError) as exc:
        store.enable_no_resend()
    assert exc.value is sentinel
    contract = json.loads((profile.database.parent / ledger.WITNESS).read_bytes())
    monkeypatch.setattr(sync_state, "_write", original)
    state = store.enable_no_resend()
    assert state["sender_contract"]["ledger_id"] == contract["ledger_id"]
    assert ledger.observe(profile, state=state)["catalog"]["publications"] == {}
    assert (profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == outbox


def test_restored_v1_cannot_drop_legacy_cache_after_permanent_activation(bundle):
    profile, store, _, _, _ = bundle
    descriptor = {"video_id": "BV1Legacy", "sha256": "a" * 64, "extension": "mp4", "byte_size": 3}
    store.update({"uploaded_media": {"BV1Legacy": descriptor}})
    old = (profile.database.parent / "sync-state.json").read_bytes()
    apply(bundle)
    (profile.database.parent / "sync-state.json").write_bytes(old)
    assert store.read()["format_version"] == 1 and store.has_sender_contract()
    with pytest.raises(SyncError) as exc:
        store.update({"uploaded_media": {}})
    assert exc.value.code == "SYNC_NO_RESEND_LEGACY_CACHE_PROTECTED"
    assert store.read()["uploaded_media"] == {"BV1Legacy": descriptor}


def test_permanent_evidence_requires_raw_canonical_sha_and_real_bytes_budget(bundle, monkeypatch):
    profile, store, metadata, _, _ = bundle
    apply(bundle)
    marker = profile.database.parent / ledger.DIRECTORY / "v1/publications" / (metadata["publication_id"] + ".json")
    original = marker.read_bytes()
    marker.write_bytes(original + b" " * 1024)
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(str(uuid.uuid4()))
    assert exc.value.code == "SYNC_NO_RESEND_RECORD_MISMATCH"
    marker.write_bytes(original)
    monkeypatch.setattr(ledger, "MAX_TOTAL_BYTES", 256)
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(str(uuid.uuid4()))
    assert exc.value.code == "SYNC_NO_RESEND_BYTES_LIMIT"


def test_external_hardlink_to_marker_blocks_sender(bundle, tmp_path):
    profile, store, metadata, _, _ = bundle
    apply(bundle)
    marker = profile.database.parent / ledger.DIRECTORY / "v1/publications" / (metadata["publication_id"] + ".json")
    os.link(marker, tmp_path / "external-marker.json")
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(str(uuid.uuid4()))
    assert exc.value.code == "SYNC_NO_RESEND_UNSAFE_PATH"


def test_ledger_and_activation_anchors_are_gc_special_paths(bundle):
    from reno import storage_roots
    profile, _, _, _, _ = bundle
    exact, directories = storage_roots._protection((profile,))
    for name in (ledger.WITNESS, ledger.SEAL):
        assert storage_roots._protected_path(profile.database.parent / name, exact, directories)
    assert storage_roots._protected_path(profile.database.parent / ledger.DIRECTORY / "v1/publications/future.json", exact, directories)


def test_v2_history_and_publication_cache_keep_independent_roots(bundle):
    from reno import storage_home_roots as home
    profile, store, metadata, snapshot, stream = bundle
    descriptor = {"video_id": "BV1Legacy", "sha256": "a" * 64, "extension": "mp4", "byte_size": 3}
    store.update({"uploaded_media": {"BV1Legacy": descriptor}})
    store.enable_no_resend()
    generation = str(uuid.uuid4())
    fresh = {"video_id": "BV1NewHint", "sha256": "b" * 64, "extension": "mp4", "byte_size": 4}
    binding = {"publication_id": metadata["publication_id"], "source_node_id": store.read()["binding"]["home_node_id"],
               "source_version": snapshot["version"], "recovery_generation": 0, "recovery_stream": stream}
    store.update({"work": {"item_id": 1, "lease_generation": 1, "video_id": "BV1NewWork", "phase": "downloaded",
                           "source_mapping_generation": generation},
                  "publication_media": {metadata["publication_id"]: {"binding": binding, "media": {fresh["video_id"]: fresh}}}})
    store.archive_progress("recovery")
    history = list((profile.database.parent / "sync-work-history").glob("*.json"))
    assert len(history) == 1
    saved = json.loads(history[0].read_bytes())
    assert saved["format_version"] == 2 and saved["work"]["source_mapping_generation"] == generation
    roots = {"sync_media": "1" * 64, "local_report": "2" * 64, "sync_report": "2" * 64, "checkpoint": "3" * 64}
    identity = {"library_id": store.read()["binding"]["home_library_id"], "node_id": store.read()["binding"]["home_node_id"]}
    with lifecycle.shared(profile):
        scan = home.scan_locked(profile, root_ids=roots, member_id="owner", source_identity=identity)
    assert not scan.blockers
    assert {s.video_id for s in scan.selectors} >= {"BV1Legacy", "BV1NewHint", "BV1NewWork"}
    # A publication stop does not implicitly retire its independent work/cache.
    apply(bundle)
    with lifecycle.shared(profile):
        scan = home.scan_locked(profile, root_ids=roots, member_id="owner", source_identity=identity)
    assert not scan.blockers
    assert {s.video_id for s in scan.selectors} >= {"BV1Legacy", "BV1NewHint", "BV1NewWork"}


def test_upload_hint_rejects_descriptor_not_in_original_frozen_batch(bundle):
    _, store, metadata, snapshot, stream = bundle
    store.enable_no_resend()
    binding = {"publication_id": metadata["publication_id"], "source_node_id": store.read()["binding"]["home_node_id"],
               "source_version": snapshot["version"], "recovery_generation": 0, "recovery_stream": stream}
    descriptor = {"video_id": "BV1Other", "sha256": "c" * 64, "extension": "mp4", "byte_size": 3}
    with pytest.raises(SyncError) as exc:
        store.record_uploaded(binding, descriptor)
    assert exc.value.code == "SYNC_NO_RESEND_BINDING_MISMATCH"
    assert store.read()["publication_media"] == {}


def test_legacy_original_stream_survives_detach_and_receipt_crash(bundle, monkeypatch):
    profile, store, metadata, snapshot, stream = bundle
    raw = sync_state._bounded({"snapshot": snapshot, "claims": []}, maximum=sync_state.MAX_OUTBOX_BYTES)
    path = profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")
    path.write_bytes(raw)
    legacy = {"publication_id": metadata["publication_id"], "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}
    store.update({"pending_publication": legacy, "legacy_pending_stream": stream})
    current = (profile, store, legacy, snapshot, stream)
    request = apply(current)
    assert store.read()["pending_publication"] is None and store.read()["legacy_pending_stream"] is None
    assert store.preview_abandon(metadata["publication_id"])["stage"] == "no_resend_pending"
    original = sync_state._write
    sentinel = RuntimeError("synthetic legacy receipt interruption")
    def fail(directory, name, data, **options):
        if directory.path.name == "receipts":
            raise sentinel
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    with pytest.raises(RuntimeError) as exc:
        store.record_no_resend_receipt(request["operation_id"], response(request))
    assert exc.value is sentinel
    monkeypatch.setattr(sync_state, "_write", original)
    assert store.resume_no_resend_commit(request["operation_id"]) == request
    assert store.no_resend_receipt(request["operation_id"]) == response(request)
    assert path.read_bytes() == raw


def test_request_version_and_terminal_binding_reject_equal_bool_or_float(bundle):
    profile, store, _, _, _ = bundle
    request = apply(bundle)
    observed = ledger.observe(profile, state=store.read())
    malformed = {**request, "format_version": True}
    with pytest.raises(SyncError):
        ledger.validate_request(malformed, observed["contract"])
    for value in (True, 1.0):
        proof = deepcopy(response(request))
        proof["binding"]["source_version"]["library_epoch"] = value
        with pytest.raises(SyncError):
            store.record_no_resend_receipt(request["operation_id"], proof)
    assert store.no_resend_receipt(request["operation_id"]) is None


@pytest.mark.parametrize("damage", ["missing", "corrupt", "old_catalog", "missing_witness"])
def test_missing_or_corrupt_evidence_never_fails_open(bundle, damage):
    profile, store, metadata, _, _ = bundle
    apply(bundle)
    root = profile.database.parent
    marker = root / ledger.DIRECTORY / "v1/publications" / (metadata["publication_id"] + ".json")
    catalog = root / ledger.DIRECTORY / "v1/catalog.json"
    if damage == "missing":
        marker.unlink()
    elif damage == "corrupt":
        marker.write_bytes(b"{}")
    elif damage == "missing_witness":
        (root / ledger.WITNESS).unlink()
    else:
        value = json.loads(catalog.read_bytes())
        value["publications"] = {}
        catalog.write_bytes(sync_state._bounded(value, maximum=ledger.MAX_CATALOG_BYTES))
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(str(uuid.uuid4()))
    assert exc.value.code.startswith("SYNC_NO_RESEND_")


def test_receipt_is_exact_immutable_and_completed_resume_returns_same_fact(bundle):
    _, store, _, _, _ = bundle
    request = apply(bundle)
    terminal = response(request)
    assert store.record_no_resend_receipt(request["operation_id"], terminal) == terminal
    assert store.pending_no_resend_requests() == []
    assert store.no_resend_receipt(request["operation_id"]) == terminal
    assert store.record_no_resend_receipt(request["operation_id"], terminal) == terminal
    wrong = {**terminal, "operation_id": str(uuid.uuid4())}
    with pytest.raises(SyncError) as exc:
        store.record_no_resend_receipt(request["operation_id"], wrong)
    assert exc.value.code == "SYNC_NO_RESEND_RECEIPT_MISMATCH"


def test_published_proof_preserves_original_epoch_and_stable_replay(bundle):
    _, store, metadata, snapshot, stream = bundle
    request = apply(bundle)
    proof = {"binding": request["binding"], "payload_sha256": request["payload_sha256"], "state": "published",
             "receipt": {"publication_id": metadata["publication_id"], "source_version": snapshot["version"],
                         "local_version": {**stream, "kb_revision": 8}, "committed_at": "2026-10-01T01:00:00.000Z", "replayed": True}}
    bad = deepcopy(proof)
    bad["receipt"]["local_version"]["library_epoch"] += 1
    with pytest.raises(SyncError) as exc:
        store.record_no_resend_receipt(request["operation_id"], bad)
    assert exc.value.code == "SYNC_NO_RESEND_RECEIPT_MISMATCH"
    stored = store.record_no_resend_receipt(request["operation_id"], proof)
    assert stored["state"] == "published" and stored["receipt"]["replayed"] is False
    proof["receipt"]["replayed"] = False
    assert store.record_no_resend_receipt(request["operation_id"], proof) == stored


def test_changed_attempt_binding_blocks_detach_without_changing_state(bundle, monkeypatch):
    _, store, metadata, _, _ = bundle
    original = store.detach_no_resend
    monkeypatch.setattr(store, "detach_no_resend", lambda _: None)
    request = apply(bundle)
    monkeypatch.setattr(store, "detach_no_resend", original)
    path = store.data_dir / "sync-state.json"
    value = json.loads(path.read_bytes())
    value["recovery_attempt"]["generation"] = 1
    path.write_bytes(sync_state._validate(value))
    before = path.read_bytes()
    with pytest.raises(SyncError) as exc:
        store.detach_no_resend(request["operation_id"])
    assert exc.value.code == "SYNC_NO_RESEND_BINDING_MISMATCH"
    assert path.read_bytes() == before


def test_partial_catalog_reservation_blocks_and_exact_file_retry_completes(bundle, monkeypatch):
    profile, store, metadata, _, _ = bundle
    store.enable_no_resend()
    state = store.read()
    envelope = store.read_publication(metadata)
    request = ledger.make_request(profile, state, metadata, envelope, str(uuid.uuid4()))
    original = sync_state._write
    sentinel = RuntimeError("synthetic marker interruption")
    def fail(directory, name, data, **options):
        if directory.path.name == "publications":
            raise sentinel
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    with store.reference_operation(), pytest.raises(RuntimeError) as exc:
        ledger.commit_request(profile, request)
    assert exc.value is sentinel
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(metadata["publication_id"])
    assert exc.value.code == "SYNC_NO_RESEND_RECORD_MISSING"
    monkeypatch.setattr(sync_state, "_write", original)
    with store.reference_operation():
        assert ledger.commit_request(profile, request) == request
    assert store.pending_no_resend_requests() == [request]


def test_v2_capability_and_legacy_cache_cannot_be_downgraded_or_cleared(bundle):
    _, store, _, _, _ = bundle
    descriptor = {"video_id": "BV1Legacy", "sha256": "a" * 64, "extension": "mp4", "byte_size": 3}
    store.update({"uploaded_media": {"BV1Legacy": descriptor}})
    apply(bundle)
    with pytest.raises(SyncError):
        store.update({"format_version": 1})
    with pytest.raises(SyncError) as exc:
        store.update({"sender_contract": {"version": 1, "ledger_id": str(uuid.uuid4())}})
    assert exc.value.code == "SYNC_NO_RESEND_CONTRACT_MISMATCH"
    with pytest.raises(SyncError) as exc:
        store.update({"uploaded_media": {}})
    assert exc.value.code == "SYNC_NO_RESEND_LEGACY_CACHE_PROTECTED"


@pytest.mark.parametrize("gap", ["marker", "receipt"])
def test_explicit_same_operation_repairs_reserved_local_record_only(bundle, monkeypatch, gap):
    profile, store, metadata, _, _ = bundle
    request = None
    if gap == "receipt":
        request = apply(bundle)
    else:
        store.enable_no_resend()
        request = ledger.make_request(profile, store.read(), metadata, store.read_publication(metadata), str(uuid.uuid4()))
    original = sync_state._write
    sentinel = RuntimeError("synthetic reserved record crash")
    target = "publications" if gap == "marker" else "receipts"
    def fail(directory, name, data, **options):
        if directory.path.name == target:
            raise sentinel
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    with pytest.raises(RuntimeError) as exc:
        if gap == "marker":
            with store.reference_operation():
                ledger.commit_request(profile, request)
        else:
            store.record_no_resend_receipt(request["operation_id"], response(request))
    assert exc.value is sentinel
    with pytest.raises(SyncError) as exc:
        store.assert_sendable(metadata["publication_id"])
    assert exc.value.code == "SYNC_NO_RESEND_RECORD_MISSING"
    monkeypatch.setattr(sync_state, "_write", original)
    assert store.resume_no_resend_commit(request["operation_id"]) == request
    assert store.read()["pending_publication"] is None
    if gap == "receipt":
        assert store.no_resend_receipt(request["operation_id"]) == response(request)
    else:
        assert store.pending_no_resend_requests() == [request]
