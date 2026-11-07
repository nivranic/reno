"""Independent selected reference retirements in fresh artificial profiles."""
from copy import deepcopy
import hashlib
import os
import uuid

import pytest

from reno import sync_no_resend as ledger, sync_local_retirement as retire, sync_state, sync_sources
from reno.sync_protocol import SyncError
from tests.test_sync_no_resend import bundle, files
from tests.test_sync_sources import resolution, BV, STAMP
from tests.test_sync_no_resend_agent import pending, env


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def selection(*items):
    return {"format_version": 1, "selected": list(items)}


def commit(store, selected):
    preview = store.preview_retirement(selected)
    return store.commit_retirement(selected, expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))


def cache_item(store, video_id="BV1Cache", publication_id=None):
    path = store.data_dir / "sync-state.json"
    state = store.read()
    item = {"kind": "legacy_cache", "holder_kind": "state", "holder_name": path.name,
        "holder_raw_sha256": sha(path.read_bytes()), "video_id": video_id,
        "descriptor": state["uploaded_media"].get(video_id)}
    if publication_id is not None:
        record = state["publication_media"][publication_id]
        item.update(kind="publication_cache", publication_id=publication_id, binding=record["binding"],
                    descriptor=record["media"][video_id])
    return item


def history(store, work):
    store.update({"work": work})
    store.archive_progress("claim_changed")
    path = list((store.data_dir / "sync-work-history").glob("*.json"))[-1]
    store.update({"work": None})
    return {"kind": "history_work", "history_name": path.name, "raw_sha256": sha(path.read_bytes()),
            "work_key": retire.work_key(work)}


def descriptor(video_id):
    return {"video_id": video_id, "sha256": "a" * 64, "extension": "mp4", "byte_size": 3}


def work(mapping_id=None, generation=None):
    result = {"item_id": 1, "lease_generation": 1, "video_id": BV, "phase": "downloaded"}
    if mapping_id is not None:
        result["source_mapping_id"] = mapping_id
    if generation is not None:
        result["source_mapping_generation"] = generation
    return result


def map_file(store, generation=None):
    observed = resolution()
    binding = store.read()["binding"]
    origin = {"origin_version": 1, "source_library_id": binding["home_library_id"], "source_node_id": binding["home_node_id"],
        "submitted_url": observed["submitted_url"], "resolved_url": observed["resolved_url"],
        "resolution_sha256": sha(sync_state._bounded(observed, maximum=sync_sources.MAX_RESOLUTION_BYTES)),
        "extracted_video_id": BV, "video_id": BV, "media_sha256": "a" * 64,
        "resolved_at": STAMP, "recorded_at": STAMP}
    origin["mapping_id"] = sync_sources._origin_digest(origin)
    sync_sources.validate_origin(origin)
    wrapper = {"origin": origin, "resolution": observed}
    if generation is not None:
        wrapper.update(format_version=2, mapping_generation=generation)
    folder = store.data_dir / "sync-sources" / ("v1" if generation is None else "v2")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ((origin["mapping_id"] if generation is None else generation) + ".json")
    raw = sync_state._bounded(wrapper, maximum=sync_sources.MAX_RECORD_BYTES)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return {"kind": "source_map", "mapping_generation": generation, "origin_mapping_id": origin["mapping_id"], "raw_sha256": sha(raw)}


def test_history_preview_is_pure_and_commit_bans_phase_or_claim_replay(bundle):
    profile, store, *_ = bundle
    original = work()
    selected = selection(history(store, original))
    before = files(profile.database.parent.parent)
    store.preview_retirement(selected)
    assert files(profile.database.parent.parent) == before
    request = commit(store, selected)
    assert request["selected"] == selected["selected"]
    path = store.data_dir / "sync-work-history" / selected["selected"][0]["history_name"]
    assert sha(path.read_bytes()) == selected["selected"][0]["raw_sha256"]
    for incoming in (original, {**original, "phase": "judged", "lease_generation": 7}):
        with pytest.raises(SyncError) as caught:
            store.update({"work": incoming})
        assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    store.update({"work": {**original, "item_id": 2}})


def test_current_work_dependency_rejects_before_activation(bundle):
    _, store, *_ = bundle
    original = work()
    item = history(store, original)
    store.update({"work": original})
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"
    assert not (store.data_dir / ledger.WITNESS).exists()


def test_v1_selected_state_cache_detaches_only_exact_entry_and_restoration_cannot_reinstall(bundle):
    _, store, *_ = bundle
    a, b = descriptor("BV1Cache"), descriptor("BV1Other")
    store.update({"uploaded_media": {a["video_id"]: a, b["video_id"]: b}})
    original = (store.data_dir / "sync-state.json").read_bytes()
    item = cache_item(store)
    request = commit(store, selection(item))
    assert store.read()["uploaded_media"] == {b["video_id"]: b}
    (store.data_dir / "sync-state.json").write_bytes(original)
    store.update({"seeded": True})
    assert store.read()["uploaded_media"] == {b["video_id"]: b}
    with pytest.raises(SyncError) as caught:
        store.update({"uploaded_media": {a["video_id"]: a, b["video_id"]: b}})
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    assert store.resume_retirement(request["operation_id"]) == request


@pytest.mark.parametrize("change", ["raw_sha", "owner", "stale_digest"])
def test_wrong_proof_or_cas_never_activates(bundle, change):
    _, store, *_ = bundle
    store.update({"uploaded_media": {"BV1Cache": descriptor("BV1Cache")}})
    item = cache_item(store)
    selected = selection(item)
    preview = store.preview_retirement(selected)
    if change == "raw_sha":
        item["holder_raw_sha256"] = "f" * 64
    elif change == "owner":
        path = store.data_dir / "sync-state.json"
        state = store.read()
        state["binding"]["home_node_id"] = str(uuid.uuid4())
        path.write_bytes(sync_state._validate(state))
    with pytest.raises(SyncError):
        store.commit_retirement(selected, expected_digest="f" * 64 if change == "stale_digest" else preview["digest"], operation_id=str(uuid.uuid4()))
    assert not (store.data_dir / ledger.WITNESS).exists()


@pytest.mark.parametrize("generation", [None, "uuid"])
def test_exact_mapping_retirement_bans_instance_after_raw_or_origin_rewrite(bundle, generation):
    _, store, *_ = bundle
    generation = str(uuid.uuid4()) if generation else None
    item = map_file(store, generation)
    commit(store, selection(item))
    with pytest.raises(SyncError) as caught:
        retire.assert_mapping_reusable(store.profile, item["origin_mapping_id"], mapping_generation=generation, state=store.read())
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    if generation is not None:
        with pytest.raises(SyncError):
            retire.assert_mapping_reusable(store.profile, "b" * 64, mapping_generation=generation, state=store.read())
        retire.assert_mapping_reusable(store.profile, item["origin_mapping_id"], mapping_generation=str(uuid.uuid4()), state=store.read())


def test_selected_history_can_close_mapping_dependency_but_other_history_cannot(bundle):
    _, store, *_ = bundle
    item = map_file(store)
    first = history(store, work(item["origin_mapping_id"]))
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"
    commit(store, selection(item, first))
    with pytest.raises(SyncError):
        store.assert_work_reusable(work(item["origin_mapping_id"]))


@pytest.mark.parametrize("generation", [None, "uuid"])
@pytest.mark.parametrize("holder", ["current", "history"])
def test_unlinked_same_video_work_blocks_mapping_preview_and_apply(bundle, generation, holder):
    _, store, *_ = bundle
    item = map_file(store, str(uuid.uuid4()) if generation else None)
    selected = selection(item)
    prior = store.preview_retirement(selected)
    if holder == "current":
        store.update({"work": work()})
    else:
        history(store, work())
    before = files(store.data_dir)
    for action in (lambda: store.preview_retirement(selected),
                   lambda: store.commit_retirement(selected, expected_digest=prior["digest"], operation_id=str(uuid.uuid4()))):
        with pytest.raises(SyncError) as caught:
            action()
        assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"
    assert not (store.data_dir / ledger.WITNESS).exists()
    assert files(store.data_dir) == before
    if holder == "current":
        # The rejection never invents a mapping link for this active work.
        assert store.read()["work"] == work()


@pytest.mark.parametrize("generation", [None, "uuid"])
def test_selected_unlinked_history_closes_only_its_mapping_dependency(bundle, generation):
    _, store, *_ = bundle
    item = map_file(store, str(uuid.uuid4()) if generation else None)
    retired_history = history(store, work())
    selected = selection(item, retired_history)
    request = commit(store, selected)
    assert request["selected"] == retire.validate_selection(selected)["selected"]
    assert store.resume_retirement(request["operation_id"]) == request
    path = store.data_dir / "sync-work-history" / retired_history["history_name"]
    assert sha(path.read_bytes()) == retired_history["raw_sha256"]


@pytest.mark.parametrize("generation", [None, "uuid"])
@pytest.mark.parametrize("holder", ["current", "history"])
def test_mapping_resume_rechecks_restored_unlinked_same_video_dependency(bundle, monkeypatch, generation, holder):
    _, store, *_ = bundle
    item = map_file(store, str(uuid.uuid4()) if generation else None)
    # An unrelated surviving history is valid before the authorization. A
    # restored same-video version must be noticed when resuming that UUID.
    old_history = history(store, {**work(), "video_id": "BV1Independent"}) if holder == "history" else None
    selected = selection(item)
    preview = store.preview_retirement(selected)
    operation_id = str(uuid.uuid4())
    original_write = sync_state._write
    sentinel = RuntimeError("synthetic mapping retirement interruption")
    def interrupt(directory, name, data, **options):
        if directory.path.name == "references":
            raise sentinel
        return original_write(directory, name, data, **options)
    with monkeypatch.context() as patch:
        patch.setattr(sync_state, "_write", interrupt)
        with pytest.raises(RuntimeError) as caught:
            store.commit_retirement(selected, expected_digest=preview["digest"], operation_id=operation_id)
        assert caught.value is sentinel
    if holder == "current":
        path = store.data_dir / "sync-state.json"
        restored = store.read()
        restored["work"] = work()
        path.write_bytes(sync_state._validate(restored))
    else:
        path = store.data_dir / "sync-work-history" / old_history["history_name"]
        restored = sync_state._decode(path.read_bytes())
        restored["work"] = work()
        path.write_bytes(sync_state._bounded(restored, maximum=sync_state.MAX_STATE_BYTES))
    before = files(store.data_dir)
    with pytest.raises(SyncError) as caught:
        store.resume_retirement(operation_id)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"
    assert files(store.data_dir) == before
    observed = ledger.observe(store.profile, state=store.read(), allow_incomplete=True)
    assert observed["catalog"]["references"][operation_id]["state"] == "reserved"


def test_current_linked_instance_blocks_retiring_its_unlinked_history(bundle):
    _, store, *_ = bundle
    store.enable_no_resend()
    generation = str(uuid.uuid4())
    item = history(store, work(generation=generation))
    store.update({"work": work("a" * 64, generation)})
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"


def test_publication_cache_needs_exact_terminal_marker_and_outbox_descriptor(pending):
    _, store, metadata = pending
    envelope = store.read_publication(metadata)
    cache = envelope["snapshot"]["videos"][0]["media"]
    store.enable_no_resend()
    binding = {"publication_id": metadata["publication_id"], "source_node_id": store.read()["binding"]["home_node_id"],
        "source_version": envelope["snapshot"]["version"], "recovery_generation": envelope["recovery_generation"],
        "recovery_stream": envelope["recovery_stream"]}
    store.record_uploaded(binding, cache)
    item = cache_item(store, cache["video_id"], metadata["publication_id"])
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_ACTIVE"
    preview = store.preview_abandon(metadata["publication_id"])
    store.commit_no_resend(metadata["publication_id"], expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))
    item = cache_item(store, cache["video_id"], metadata["publication_id"])
    commit(store, selection(item))
    assert store.read()["publication_media"] == {}
    with pytest.raises(SyncError) as caught:
        store.update({"publication_media": {metadata["publication_id"]: {"binding": binding, "media": {cache["video_id"]: cache}}}})
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"


def test_reservation_blocks_reuse_and_exact_uuid_resume_keeps_selected_history(bundle, monkeypatch):
    _, store, *_ = bundle
    item = history(store, work())
    selected = selection(item)
    operation_id = str(uuid.uuid4())
    preview = store.preview_retirement(selected)
    original = sync_state._write
    sentinel = RuntimeError("synthetic reference publication interruption")
    def fail(directory, name, data, **options):
        if directory.path.name == "references":
            raise sentinel
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    with pytest.raises(RuntimeError) as caught:
        store.commit_retirement(selected, expected_digest=preview["digest"], operation_id=operation_id)
    assert caught.value is sentinel
    with pytest.raises(SyncError):
        store.update({"work": work()})
    monkeypatch.setattr(sync_state, "_write", original)
    result = store.resume_retirement(operation_id)
    assert result["selected"] == selected["selected"]
    assert sha((store.data_dir / "sync-work-history" / item["history_name"]).read_bytes()) == item["raw_sha256"]
    assert store.resume_retirement(operation_id) == result


def test_reference_resume_rejects_changed_selected_history(bundle, monkeypatch):
    _, store, *_ = bundle
    item = history(store, work())
    original = sync_state._write
    def fail(directory, name, data, **options):
        if directory.path.name == "references":
            raise RuntimeError("synthetic crash")
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    selected = selection(item)
    operation_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        store.commit_retirement(selected, expected_digest=store.preview_retirement(selected)["digest"], operation_id=operation_id)
    monkeypatch.setattr(sync_state, "_write", original)
    path = store.data_dir / "sync-work-history" / item["history_name"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(SyncError) as caught:
        store.resume_retirement(operation_id)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH"


def test_work_generation_inputs_and_mapping_link_are_immutable(bundle):
    _, store, *_ = bundle
    store.enable_no_resend()
    item = work(generation=str(uuid.uuid4()))
    item["source_mapping_inputs"] = {"resolution": resolution(), "extracted_video_id": BV,
        "media_sha256": "a" * 64, "recorded_at": STAMP}
    store.update({"work": item})
    store.update({"work": {**item, "source_mapping_id": "b" * 64, "phase": "processed"}})
    for bad in ({**item, "source_mapping_generation": str(uuid.uuid4())},
                {**item, "source_mapping_inputs": {**item["source_mapping_inputs"], "recorded_at": "2026-02-01T00:00:00Z"}},
                {**item, "source_mapping_id": "c" * 64}):
        with pytest.raises(SyncError) as caught:
            store.update({"work": bad})
        assert caught.value.code == "SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT"


def test_existing_legacy_work_cannot_gain_generation_or_change_mapping_to_bypass_retirement(bundle):
    _, store, *_ = bundle
    store.enable_no_resend()
    old = work("a" * 64)
    store.update({"work": old})
    for incoming in ({**old, "source_mapping_generation": str(uuid.uuid4())},
                     {**old, "source_mapping_id": "b" * 64}):
        with pytest.raises(SyncError) as caught:
            store.update({"work": incoming})
        assert caught.value.code == "SYNC_NO_RESEND_WORK_INSTANCE_CONFLICT"


@pytest.mark.parametrize("generation", [None, "uuid"])
def test_retired_unlinked_work_cannot_revive_by_linking_mapping_id(bundle, generation):
    _, store, *_ = bundle
    generation = str(uuid.uuid4()) if generation else None
    if generation is not None:
        store.enable_no_resend()
    original = work(generation=generation)
    item = history(store, original)
    commit(store, selection(item))
    incoming = {**original, "source_mapping_id": "a" * 64, "phase": "processed"}
    with pytest.raises(SyncError) as caught:
        store.update({"work": incoming})
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    # Bringing the old unlinked instance back does not open a link-time escape.
    path = store.data_dir / "sync-state.json"
    state = store.read()
    state["work"] = original
    path.write_bytes(sync_state._validate(state))
    with pytest.raises(SyncError) as caught:
        store.update({"work": incoming})
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"


def test_pure_frozen_evidence_has_no_live_reads_and_rejects_noncanonical_or_missing_file(bundle, monkeypatch):
    _, store, *_ = bundle
    request = commit(store, selection(history(store, work())))
    base = store.data_dir / ledger.DIRECTORY / "v1"
    records = {kind: {path.name: path.read_bytes() for path in (base / kind).glob("*.json")}
               for kind in ("publications", "receipts", "references")}
    args = ((store.data_dir / ledger.WITNESS).read_bytes(), (store.data_dir / ledger.SEAL).read_bytes(),
            (base / "catalog.json").read_bytes(), records)
    monkeypatch.setattr(ledger.safe if hasattr(ledger, "safe") else ledger.media, "_Directory", lambda *a, **k: pytest.fail("pure validator must not open storage"))
    observed = ledger.validate_frozen_evidence(*args, profile=store.profile)
    assert observed["catalog"]["references"][request["operation_id"]]["state"] == "committed"
    records["references"][request["operation_id"] + ".json"] += b" "
    with pytest.raises(SyncError):
        ledger.validate_frozen_evidence(*args, profile=store.profile)


def test_selected_history_from_another_target_peer_cannot_borrow_contract(bundle):
    _, store, *_ = bundle
    item = history(store, work())
    path = store.data_dir / "sync-work-history" / item["history_name"]
    value = sync_state._decode(path.read_bytes())
    value["binding"]["target_node_id"] = str(uuid.uuid4())
    raw = sync_state._bounded(value, maximum=sync_state.MAX_STATE_BYTES)
    path.write_bytes(raw)
    item["raw_sha256"] = sha(raw)
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value.code == "SYNC_NO_RESEND_PEER_MISMATCH"


def test_history_cache_retirement_keeps_current_and_other_history_independent(bundle):
    _, store, *_ = bundle
    cache = descriptor("BV1Cache")
    store.update({"uploaded_media": {cache["video_id"]: cache}})
    store.enable_no_resend()
    store.archive_progress("claim_changed")
    store.archive_progress("claim_changed")
    paths = list((store.data_dir / "sync-work-history").glob("*.json"))
    before = {path.name: path.read_bytes() for path in paths}
    item = {"kind": "legacy_cache", "holder_kind": "history", "holder_name": paths[0].name,
        "holder_raw_sha256": sha(before[paths[0].name]), "video_id": cache["video_id"], "descriptor": cache}
    request = commit(store, selection(item))
    assert store.read()["uploaded_media"] == {cache["video_id"]: cache}
    assert {path.name: path.read_bytes() for path in paths} == before
    assert len(request["selected"]) == 1


def test_reserved_cache_keeps_state_hint_until_immutable_evidence_committed(bundle, monkeypatch):
    _, store, *_ = bundle
    store.update({"uploaded_media": {"BV1Cache": descriptor("BV1Cache")}})
    selected = selection(cache_item(store))
    operation_id = str(uuid.uuid4())
    original = sync_state._write
    def fail(directory, name, data, **options):
        if directory.path.name == "references":
            raise RuntimeError("synthetic reference crash")
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", fail)
    with pytest.raises(RuntimeError):
        store.commit_retirement(selected, expected_digest=store.preview_retirement(selected)["digest"], operation_id=operation_id)
    store.update({"seeded": True})
    assert "BV1Cache" in store.read()["uploaded_media"]
    monkeypatch.setattr(sync_state, "_write", original)
    store.resume_retirement(operation_id)
    assert store.read()["uploaded_media"] == {}


def test_restored_retired_state_cache_is_not_copied_into_a_new_history_root(bundle):
    _, store, *_ = bundle
    store.update({"uploaded_media": {"BV1Cache": descriptor("BV1Cache")}})
    old = (store.data_dir / "sync-state.json").read_bytes()
    commit(store, selection(cache_item(store)))
    (store.data_dir / "sync-state.json").write_bytes(old)
    store.archive_progress("epoch_changed")
    path = list((store.data_dir / "sync-work-history").glob("*.json"))[0]
    assert sync_state._decode(path.read_bytes())["uploaded_media"] == {}


def test_cancellation_keeps_original_exception_identity(bundle):
    _, store, *_ = bundle
    item = history(store, work())
    sentinel = SyncError("synthetic cancellation", "SYNC_CANCELLED", 409)
    def cancel():
        raise sentinel
    store.check_cancelled = cancel
    with pytest.raises(SyncError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value is sentinel


def test_oserror_cancellation_inside_capture_keeps_original_exception(bundle, monkeypatch):
    _, store, *_ = bundle
    item = history(store, work())
    sentinel = OSError("synthetic callback cancellation")
    inside = [False]
    original = retire._capture
    def capture(*args, **kwargs):
        inside[0] = True
        try:
            return original(*args, **kwargs)
        finally:
            inside[0] = False
    def cancel():
        if inside[0]:
            raise sentinel
    monkeypatch.setattr(retire, "_capture", capture)
    store.check_cancelled = cancel
    with pytest.raises(OSError) as caught:
        store.preview_retirement(selection(item))
    assert caught.value is sentinel
