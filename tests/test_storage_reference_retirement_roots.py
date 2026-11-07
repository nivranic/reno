"""Frozen sender/reference root proofs; TEMP files only, no DB/network/GC."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from reno import storage_home_roots as home, storage_roots as roots
from reno import storage_lifecycle as lifecycle, sync_no_resend as ledger, sync_local_retirement as retirement, sync_state
from reno.sync_protocol import _json_bytes
from tests.test_storage_home_roots import env, state, write, outbox, source_map, scan, ROOTS, SHA, STAMP  # noqa: F401


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def store(env):
    value = sync_state.StateStore(env[0].database.parent, profile=env[0])
    value.initialize(state(env)["binding"], 1)
    return value


def activation(value):
    value.enable_no_resend()
    return ledger.observe(value.profile, state=value.read())["contract"]


def marker(env, value, metadata):
    stream = {"library_id": value.read()["binding"]["target_library_id"], "library_epoch": 1}
    value.update({"pending_publication": metadata, "legacy_pending_stream": stream})
    preview = value.preview_abandon(metadata["publication_id"])
    return value.commit_no_resend(metadata["publication_id"], expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))


def immutable_history(env, value, *, work=None, cache=None, publication_cache=None, binding=None):
    state_value = value.read()
    if work is not None:
        state_value["work"] = work
    if cache is not None:
        state_value["uploaded_media"] = cache
    if publication_cache is not None:
        state_value["publication_media"] = publication_cache
    if binding is not None:
        state_value["binding"] = binding
    fields = home._HISTORY_V2_FIELDS if state_value["format_version"] == 2 else home._HISTORY_FIELDS
    record = {key: deepcopy(state_value[key]) for key in fields if key != "reason"}
    record["reason"] = "claim_changed"
    return write(env, "sync-work-history/" + uuid.uuid4().hex + ".json", record)


def add_reference(env, value, *selected, committed=True):
    contract = ledger.observe(value.profile, state=value.read())["contract"]
    selected = retirement.validate_selection({"format_version": 1, "selected": list(selected)})["selected"]
    request = {**contract, "operation_id": str(uuid.uuid4()), "created_at": STAMP,
               "policy_version": 1, "selected": selected, "preview_sha256": SHA}
    retirement.validate_request(request, contract)
    raw = ledger._encoded(request)
    catalog_path = value.data_dir / "sync-no-resend/v1/catalog.json"
    catalog = json.loads(catalog_path.read_bytes())
    catalog["references"][request["operation_id"]] = {"state": "committed" if committed else "reserved",
                                                       "record": request, "sha256": digest(raw)}
    write(env, "sync-no-resend/v1/references/" + request["operation_id"] + ".json", raw)
    catalog_path.write_bytes(ledger._encoded(catalog, ledger.MAX_CATALOG_BYTES))
    return request


def work(video="BV1Test", mapping_id=None, generation=None):
    result = {"item_id": 1, "lease_generation": 1, "video_id": video, "phase": "downloaded"}
    if mapping_id is not None:
        result["source_mapping_id"] = mapping_id
    if generation is not None:
        result["source_mapping_generation"] = generation
    return result


def work_selector(path):
    raw = path.read_bytes()
    return {"kind": "history_work", "history_name": path.name, "raw_sha256": digest(raw),
            "work_key": retirement.work_key(json.loads(raw)["work"])}


def mapping_v2(env, record, generation=None):
    generation = str(uuid.uuid4()) if generation is None else generation
    wrapper = {"format_version": 2, "mapping_generation": generation, **deepcopy(record)}
    return write(env, "sync-sources/v2/" + generation + ".json", wrapper), wrapper


def map_selector(path, wrapper):
    return {"kind": "source_map", "mapping_generation": wrapper.get("mapping_generation"),
        "origin_mapping_id": wrapper["origin"]["mapping_id"], "raw_sha256": digest(path.read_bytes())}


def cache_selector(path, descriptor, *, publication=None):
    result = {"kind": "legacy_cache", "holder_kind": "state" if path.name == "sync-state.json" else "history",
        "holder_name": path.name, "holder_raw_sha256": digest(path.read_bytes()),
        "video_id": descriptor["video_id"], "descriptor": descriptor}
    if publication is not None:
        result.update(kind="publication_cache", publication_id=publication["binding"]["publication_id"], binding=publication["binding"])
    return result


def test_publication_marker_releases_only_outbox_with_independent_roots_kept(env):
    value = store(env)
    _, metadata, envelope = outbox(env, legacy=True)
    map_path, mapping = source_map(env)
    descriptor = envelope["snapshot"]["videos"][0]["media"]
    original_work = work(mapping_id=mapping["origin"]["mapping_id"])
    value.update({"work": original_work, "uploaded_media": {descriptor["video_id"]: descriptor}})
    immutable_history(env, value, work=original_work)
    request = marker(env, value, metadata)
    result = scan(env)
    assert not result.blockers and not result.references
    assert {item.source_id for item in result.selectors} >= {
        home._hash(("state", "sync-state.json")), home._hash(("sources", map_path.name))}
    assert len(result.selectors) >= 4
    assert value.read()["uploaded_media"] == {descriptor["video_id"]: descriptor}
    assert (value.data_dir / "sync-outbox" / (request["metadata"]["publication_id"] + ".json")).exists()


def test_old_captured_image_cannot_borrow_a_new_live_marker(env, monkeypatch):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            old_bundle = captured.bundle
    marker(env, value, metadata)
    def forbidden(*args, **kwargs):
        pytest.fail("Frozen parser must never discover current live evidence")
    monkeypatch.setattr(ledger, "observe", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    original = home.parse_bundle(old_bundle, profile=env[0], root_ids=ROOTS, member_id="old_image", source_identity=env[1])
    assert original.references and not original.blockers


@pytest.mark.parametrize("damage", ["marker_missing", "catalog_missing", "seal_missing", "orphan", "old_catalog",
    "record_whitespace", "record_sha", "binding_version", "payload_sha", "unknown_version", "reserved", "unknown_directory"])
def test_bad_sender_evidence_never_releases_original_outbox(env, damage):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    request = marker(env, value, metadata)
    folder = value.data_dir / "sync-no-resend/v1"
    path = folder / "publications" / (metadata["publication_id"] + ".json")
    catalog_path = folder / "catalog.json"
    catalog = json.loads(catalog_path.read_bytes())
    if damage == "marker_missing":
        path.unlink()
    elif damage == "catalog_missing":
        catalog_path.unlink()
    elif damage == "seal_missing":
        (value.data_dir / ledger.SEAL).unlink()
    elif damage == "orphan":
        write(env, "sync-no-resend/v1/publications/" + str(uuid.uuid4()) + ".json", request)
    elif damage == "old_catalog":
        catalog["publications"] = {}
        catalog_path.write_bytes(ledger._encoded(catalog, ledger.MAX_CATALOG_BYTES))
    elif damage == "record_whitespace":
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "unknown_version":
        catalog["format_version"] = 2
        catalog_path.write_bytes(_json_bytes(catalog))
    elif damage == "reserved":
        catalog["publications"][metadata["publication_id"]]["state"] = "reserved"
        catalog_path.write_bytes(ledger._encoded(catalog, ledger.MAX_CATALOG_BYTES))
    elif damage == "unknown_directory":
        (folder / "other-version").mkdir()
    else:
        damaged = deepcopy(request)
        if damage == "record_sha":
            damaged["metadata"]["sha256"] = "f" * 64
        elif damage == "binding_version":
            damaged["binding"]["source_version"]["kb_revision"] += 1
        else:
            damaged["payload_sha256"] = "f" * 64
        raw = ledger._encoded(damaged)
        path.write_bytes(raw)
        catalog["publications"][metadata["publication_id"]].update(record=damaged, sha256=digest(raw))
        catalog_path.write_bytes(ledger._encoded(catalog, ledger.MAX_CATALOG_BYTES))
    result = scan(env)
    assert result.blockers and result.references
    assert {r.storage_key for r in result.references} == {f"sync/BV1Test/{SHA}.mp4"}


def test_sender_budget_failure_preserves_media_root_and_changes_semantic_cas(env):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    marker(env, value, metadata)
    normal = scan(env)
    limited = scan(env, limits=home.HomeLimits(max_sender_total_bytes=32))
    assert not normal.references and not normal.blockers
    assert limited.references and limited.blockers and limited.digest != normal.digest


def test_exact_history_work_and_mapping_selection_releases_only_their_edges(env):
    value = store(env)
    activation(value)
    map_path, mapping = source_map(env)
    first = immutable_history(env, value, work=work(mapping_id=mapping["origin"]["mapping_id"]))
    request = add_reference(env, value, work_selector(first), map_selector(map_path, mapping))
    result = scan(env)
    assert not result.blockers and not result.selectors
    assert first.exists() and map_path.exists()
    assert json.loads(first.read_bytes())["work"]["source_mapping_id"] == mapping["origin"]["mapping_id"]
    assert request["selected"]


def test_other_history_dependency_prevents_mapping_release(env):
    value = store(env)
    activation(value)
    map_path, mapping = source_map(env)
    original = work(mapping_id=mapping["origin"]["mapping_id"])
    first = immutable_history(env, value, work=original)
    second = immutable_history(env, value, work=original)
    add_reference(env, value, work_selector(first), map_selector(map_path, mapping))
    result = scan(env)
    assert "HOME_RETIREMENT_DEPENDENCY_ACTIVE" in {item.code for item in result.blockers}
    assert len(result.selectors) == 3 and second.exists()


@pytest.mark.parametrize("active", ["current_work", "outbox_without_claims", "outbox_other_generation"])
def test_active_work_or_unproven_outbox_generation_keeps_selected_mapping(env, active):
    value = store(env)
    activation(value)
    legacy_path, mapping = source_map(env)
    path, wrapper = mapping_v2(env, mapping)
    if active == "current_work":
        value.update({"work": work(mapping_id=mapping["origin"]["mapping_id"], generation=wrapper["mapping_generation"])})
    else:
        outbox(env)  # No local generation provenance, even with no claim origin.
        if active == "outbox_other_generation":
            mapping_v2(env, mapping)
    add_reference(env, value, map_selector(path, wrapper))
    result = scan(env)
    assert result.selectors and result.blockers
    assert "HOME_RETIREMENT_DEPENDENCY_ACTIVE" in {item.code for item in result.blockers}
    assert legacy_path.exists()


def test_same_origin_distinct_generations_are_independent_roots(env):
    value = store(env)
    activation(value)
    legacy, mapping = source_map(env)
    first, a = mapping_v2(env, mapping)
    second, b = mapping_v2(env, mapping)
    original = work(mapping_id=mapping["origin"]["mapping_id"], generation=b["mapping_generation"])
    value.update({"work": original})
    add_reference(env, value, map_selector(first, a))
    result = scan(env)
    assert not result.blockers
    ids = {item.source_id for item in result.selectors}
    assert home._hash(("sources_v2", first.name)) not in ids
    assert ids >= {home._hash(("sources_v2", second.name)), home._hash(("sources", legacy.name)), home._hash(("state", "sync-state.json"))}
    assert a["origin"]["mapping_id"] == b["origin"]["mapping_id"]


@pytest.mark.parametrize("damage", ["wrong_generation", "unknown_fields", "bool_version", "wrong_name", "unknown_tree_version"])
def test_invalid_v2_map_or_unknown_map_tree_never_proves_empty_roots(env, damage):
    _, mapping = source_map(env)
    path, wrapper = mapping_v2(env, mapping)
    if damage == "wrong_name":
        path.rename(path.with_name(str(uuid.uuid4()) + ".json"))
    elif damage == "unknown_tree_version":
        write(env, "sync-sources/v3/unknown.json", {})
    else:
        if damage == "wrong_generation":
            wrapper["mapping_generation"] = str(uuid.uuid4())
        elif damage == "unknown_fields":
            wrapper["private"] = "PRIVATE_DATA"
        else:
            wrapper["format_version"] = True
        path.write_bytes(_json_bytes(wrapper))
    result = scan(env)
    assert result.blockers and result.selectors
    assert {item.namespace for item in result.blockers} == {"sync_media"}


def test_retiring_one_history_cache_does_not_release_identical_other_holders(env):
    value = store(env)
    descriptor = {"video_id": "BV1Cached", "sha256": SHA, "extension": "mp4", "byte_size": 20}
    value.update({"uploaded_media": {descriptor["video_id"]: descriptor}})
    activation(value)
    first = immutable_history(env, value)
    second = immutable_history(env, value)
    add_reference(env, value, cache_selector(first, descriptor))
    result = scan(env)
    assert not result.blockers and len(result.selectors) == 2
    ids = {item.source_id for item in result.selectors}
    assert home._hash(("history", first.name)) not in ids
    assert ids == {home._hash(("history", second.name)), home._hash(("state", "sync-state.json"))}


@pytest.mark.parametrize("damage", ["wrong_raw", "other_peer", "reserved", "file_missing"])
def test_unproven_reference_record_keeps_all_selected_history_edges(env, damage):
    value = store(env)
    activation(value)
    binding = deepcopy(value.read()["binding"])
    if damage == "other_peer":
        binding["target_node_id"] = str(uuid.uuid4())
    path = immutable_history(env, value, work=work(), binding=binding)
    selected = work_selector(path)
    if damage == "wrong_raw":
        selected["raw_sha256"] = "f" * 64
    request = add_reference(env, value, selected, committed=damage != "reserved")
    if damage == "file_missing":
        (value.data_dir / "sync-no-resend/v1/references" / (request["operation_id"] + ".json")).unlink()
    result = scan(env)
    assert result.blockers and result.selectors
    assert home._hash(("history", path.name)) in {item.source_id for item in result.selectors}


def test_frozen_reference_parser_is_pure_and_directory_membership_changes_cas(env, monkeypatch):
    value = store(env)
    activation(value)
    path = immutable_history(env, value, work=work())
    add_reference(env, value, work_selector(path))
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            frozen = captured.bundle
    before = home.parse_bundle(frozen, profile=env[0], root_ids=ROOTS, member_id="same_image", source_identity=env[1])
    assert not before.blockers and not before.selectors
    entries = tuple((kind, (*names, ".sync-" + "a" * 32 + ".part") if kind == "references" else names)
                    for kind, names in frozen.directory_entries)
    def forbidden(*args, **kwargs):
        pytest.fail("Pure roots must use only the captured evidence")
    monkeypatch.setattr(ledger, "observe", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    after = home.parse_bundle(replace(frozen, directory_entries=entries), profile=env[0], root_ids=ROOTS,
                              member_id="same_image", source_identity=env[1])
    assert not after.blockers and not after.selectors and after.digest != before.digest


def test_source_capture_detects_new_sender_record_and_replaced_generation(env):
    value = store(env)
    activation(value)
    _, mapping = source_map(env)
    path, wrapper = mapping_v2(env, mapping)
    with lifecycle.shared(env[0]):
        with pytest.raises(home.HomeRootError) as error:
            with home.capture_locked(env[0]) as captured:
                path.write_bytes(path.read_bytes() + b" ")
                captured.verify_source()
    assert error.value.code == "HOME_SOURCE_CHANGED"
    path.write_bytes(_json_bytes(wrapper))


def test_special_evidence_paths_are_protected_even_when_roots_overlap_data(env):
    data = env[0].database.parent
    profile = lifecycle.StorageProfile(env[0].database, data, data, data)
    exact, directories = roots._protection((profile,))
    for name in (ledger.WITNESS, ledger.SEAL, "sync-no-resend/v1/catalog.json",
                 "sync-no-resend/v1/references/" + str(uuid.uuid4()) + ".json",
                 "sync-sources/v2/" + str(uuid.uuid4()) + ".json", "sync-work-history/" + uuid.uuid4().hex + ".json"):
        assert roots._protected_path(data / name, exact, directories)
    assert not roots._protected_path(data / "unrelated.mp4", exact, directories)


def test_evidence_raw_json_and_private_source_details_do_not_leak_to_public_scan(env):
    value = store(env)
    activation(value)
    path, mapping = source_map(env)
    add_reference(env, value, map_selector(path, mapping))
    result = scan(env)
    assert not result.blockers
    for content in (result.canonical_bytes(), _json_bytes(result.public_summary())):
        assert b"PRIVATE_QUERY" not in content and b"PRIVATE_SIGNATURE" not in content
        assert b"private-sync.example.test" not in content


def publication_cache(env, value):
    activation(value)
    _, metadata, envelope = outbox(env, legacy=True)
    descriptor = envelope["snapshot"]["videos"][0]["media"]
    current = value.read()
    binding = {"publication_id": metadata["publication_id"], "source_node_id": env[1]["node_id"],
        "source_version": envelope["snapshot"]["version"], "recovery_generation": 0,
        "recovery_stream": {"library_id": current["binding"]["target_library_id"], "library_epoch": 1}}
    cached = {"binding": binding, "media": {descriptor["video_id"]: descriptor}}
    current.update(uploaded_media={descriptor["video_id"]: descriptor},
                   publication_media={metadata["publication_id"]: cached})
    write(env, "sync-state.json", sync_state._validate(current))
    return metadata, descriptor, cached


def test_v3_state_keeps_original_sender_and_publication_roots_with_v2_history(env):
    value = store(env)
    metadata, descriptor, cached = publication_cache(env, value)
    historical = immutable_history(env, value)
    before = scan(env)
    assert not before.blockers
    value.enable_source_execution()
    current = value.read()
    assert current["format_version"] == 3
    assert current["source_execution"] is current["budget_pending"] is None
    assert json.loads(historical.read_bytes())["format_version"] == 2
    after = scan(env)
    assert not after.blockers
    assert after.selectors == before.selectors
    assert current["publication_media"][metadata["publication_id"]] == cached
    assert current["uploaded_media"][descriptor["video_id"]] == descriptor


@pytest.mark.parametrize("terminal", [False, True])
def test_history3_keeps_original_work_and_both_cache_roots(env, terminal):
    from tests.test_sync_budget_agent import history_files, resumed

    value = store(env)
    metadata, descriptor, cached = publication_cache(env, value)
    value.update({"work": work()})
    if terminal:
        core, pending = resumed(value)
        value.complete_budget_resume(core, pending)
    else:
        value.enable_source_execution()
        value.archive_progress("claim_changed")
    saved = history_files(value)
    name, raw = next(iter(saved.items()))
    history = json.loads(raw)
    assert sync_state.validate_source_history(history) == history
    assert (history["source_terminal"] is not None) is terminal
    result = scan(env)
    assert not result.blockers
    history_id = home._hash(("history", name))
    state_id = home._hash(("state", "sync-state.json"))
    assert sum(item.source_id == history_id for item in result.selectors) == 3
    assert sum(item.source_id == state_id for item in result.selectors) == 3
    assert history["publication_media"][metadata["publication_id"]] == cached
    assert history["uploaded_media"][descriptor["video_id"]] == descriptor
    assert history_files(value) == saved


@pytest.mark.parametrize("damage", [None, "core_sha", "receipt_binding", "reason", "missing_terminal"])
def test_frozen_history3_terminal_validation_is_pure_and_damage_keeps_outbox(env, monkeypatch, damage):
    from tests.test_sync_budget_agent import history_files, resumed

    value = store(env)
    metadata, _, _ = publication_cache(env, value)
    core, pending = resumed(value)
    value.complete_budget_resume(core, pending)
    marker(env, value, metadata)
    name, raw = next(iter(history_files(value).items()))
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            frozen = captured.bundle
    history = json.loads(raw)
    if damage == "core_sha":
        history["source_terminal"]["core_sha256"] = "f" * 64
    elif damage == "receipt_binding":
        history["source_terminal"]["proof"]["resume_receipt"]["item_id"] += 1
    elif damage == "reason":
        history["reason"] = "claim_changed"
    elif damage == "missing_terminal":
        history.pop("source_terminal")
    if damage is not None:
        frozen = replace(frozen, records=tuple(replace(item, raw=_json_bytes(history))
            if item.kind == "history" and item.name == name else item for item in frozen.records))

    def forbidden(*args, **kwargs):
        pytest.fail("History3 roots must use only the captured evidence")

    monkeypatch.setattr(ledger, "observe", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    result = home.parse_bundle(frozen, profile=env[0], root_ids=ROOTS, member_id="same_image", source_identity=env[1])
    assert bool(result.blockers) is (damage is not None)
    assert bool(result.references) is (damage is not None)
    if damage is not None:
        assert "HOME_RECORD_INVALID" in {item.code for item in result.blockers}


@pytest.mark.parametrize("corrupt", [False, True])
def test_source_published_history3_is_independent_of_permanent_outbox_release(env, monkeypatch, corrupt):
    from tests.test_sync_budget_agent import history_files, published_source

    value = store(env)
    descriptor = {"video_id": "BV1Test", "sha256": "c" * 64, "byte_size": 13, "extension": "mp4"}
    value.update({"work": work(), "uploaded_media": {descriptor["video_id"]: descriptor}})
    core, original = published_source(value)
    assert value.read_publication(original["metadata"])["snapshot"]["videos"][0]["media"] == descriptor
    value.complete_published_source(core, original)
    name, raw = next(iter(history_files(value).items()))
    history = json.loads(raw)
    assert history["reason"] == "source_published"
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            frozen = captured.bundle
    if corrupt:
        history["source_terminal"]["proof"]["response"]["binding"]["publication_id"] = str(uuid.uuid4())
        frozen = replace(frozen, records=tuple(replace(item, raw=_json_bytes(history))
            if item.kind == "history" and item.name == name else item for item in frozen.records))

    def forbidden(*args, **kwargs):
        pytest.fail("Published history roots must use only the captured evidence")

    monkeypatch.setattr(ledger, "observe", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    result = home.parse_bundle(frozen, profile=env[0], root_ids=ROOTS, member_id="same_image", source_identity=env[1])
    assert bool(result.blockers) is corrupt and bool(result.references) is corrupt
    state_id = home._hash(("state", "sync-state.json"))
    assert sum(item.source_id == state_id for item in result.selectors) == 2
    if not corrupt:
        history_id = home._hash(("history", name))
        assert sum(item.source_id == history_id for item in result.selectors) == 2


@pytest.mark.parametrize("terminal", [False, True])
def test_history4_source_failure_keeps_original_work_and_both_cache_roots(env, terminal):
    from tests.test_sync_source_control_state import controlled, histories

    value = store(env)
    metadata, descriptor, cached = publication_cache(env, value)
    value.update({"work": work()})
    core, pending = controlled(value)
    if terminal:
        value.complete_source_failure(core, pending)
    else:
        value.archive_progress("claim_changed")
    saved = histories(value)
    name, raw = next(iter(saved.items()))
    history = json.loads(raw)
    assert sync_state.validate_source_history4(history) == history
    assert (history["source_terminal"] is not None) is terminal
    assert history["source_execution"] == core and history["source_control_pending"] == pending
    result = scan(env)
    assert not result.blockers
    for source_id in (home._hash(("history", name)), home._hash(("state", "sync-state.json"))):
        assert sum(item.source_id == source_id for item in result.selectors) == 3
    assert history["publication_media"][metadata["publication_id"]] == cached
    assert history["uploaded_media"][descriptor["video_id"]] == descriptor
    assert histories(value) == saved


@pytest.mark.parametrize("damage", [None, "core_sha", "failure_sha", "admission", "missing_pending"])
def test_frozen_history4_failure_validation_is_pure_and_damage_keeps_outbox(env, monkeypatch, damage):
    from tests.test_sync_source_control_state import controlled, histories

    value = store(env)
    metadata, _, _ = publication_cache(env, value)
    core, pending = controlled(value)
    value.complete_source_failure(core, pending)
    marker(env, value, metadata)
    name, raw = next(iter(histories(value).items()))
    with lifecycle.shared(env[0]), home.capture_locked(env[0]) as captured:
        frozen = captured.bundle
    history = json.loads(raw)
    if damage == "core_sha":
        history["source_terminal"]["core_sha256"] = "f" * 64
    elif damage == "failure_sha":
        history["source_control_pending"]["receipt"]["failure_sha256"] = "f" * 64
    elif damage == "admission":
        history["source_admission"] = {"unimplemented": True}
    elif damage == "missing_pending":
        history.pop("source_control_pending")
    if damage is not None:
        frozen = replace(frozen, records=tuple(replace(item, raw=_json_bytes(history))
            if item.kind == "history" and item.name == name else item for item in frozen.records))
    def forbidden(*args, **kwargs):
        pytest.fail("History4 root validation must use only captured evidence")
    monkeypatch.setattr(ledger, "observe", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    result = home.parse_bundle(frozen, profile=env[0], root_ids=ROOTS, member_id="same_image", source_identity=env[1])
    assert bool(result.blockers) is (damage is not None)
    assert bool(result.references) is (damage is not None)
    if damage is not None:
        assert "HOME_RECORD_INVALID" in {item.code for item in result.blockers}


def test_publication_cache_selection_is_separate_from_legacy_and_history_cache(env):
    value = store(env)
    metadata, descriptor, cached = publication_cache(env, value)
    historical = immutable_history(env, value)
    marker(env, value, metadata)
    add_reference(env, value, cache_selector(value.data_dir / "sync-state.json", descriptor, publication=cached))
    result = scan(env)
    assert not result.blockers and not result.references and len(result.selectors) == 3
    history_id = home._hash(("history", historical.name))
    assert sum(item.source_id == history_id for item in result.selectors) == 2
    assert sum(item.source_id == home._hash(("state", "sync-state.json")) for item in result.selectors) == 1


@pytest.mark.parametrize("damage", ["metadata", "missing_outbox"])
def test_publication_cache_requires_independent_original_outbox_proof(env, damage):
    value = store(env)
    metadata, descriptor, cached = publication_cache(env, value)
    marker(env, value, metadata)
    if damage == "metadata":
        path = value.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
    add_reference(env, value, cache_selector(value.data_dir / "sync-state.json", descriptor, publication=cached))
    if damage == "missing_outbox":
        (value.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).unlink()
    result = scan(env)
    assert result.blockers and len(result.selectors) == 2


def test_detached_live_cache_is_noop_without_borrowing_a_history_holder(env):
    value = store(env)
    descriptor = {"video_id": "BV1Cached", "sha256": SHA, "extension": "mp4", "byte_size": 20}
    value.update({"uploaded_media": {descriptor["video_id"]: descriptor}})
    activation(value)
    historical = immutable_history(env, value)
    add_reference(env, value, cache_selector(value.data_dir / "sync-state.json", descriptor))
    current = value.read()
    current["uploaded_media"] = {}
    write(env, "sync-state.json", sync_state._validate(current))
    result = scan(env)
    assert not result.blockers and len(result.selectors) == 1
    assert result.selectors[0].source_id == home._hash(("history", historical.name))


@pytest.mark.parametrize("corrupt", [False, True])
def test_exact_terminal_receipt_is_captured_and_bad_receipt_preserves_outbox(env, corrupt):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    request = marker(env, value, metadata)
    value.record_no_resend_receipt(request["operation_id"], {
        "binding": request["binding"], "operation_id": request["operation_id"], "state": "abandoned"})
    if corrupt:
        catalog_path = value.data_dir / "sync-no-resend/v1/catalog.json"
        catalog = json.loads(catalog_path.read_bytes())
        entry = catalog["receipts"][metadata["publication_id"]]
        entry["record"]["response"]["operation_id"] = str(uuid.uuid4())
        raw = ledger._encoded(entry["record"])
        entry["sha256"] = digest(raw)
        write(env, "sync-no-resend/v1/receipts/" + metadata["publication_id"] + ".json", raw)
        catalog_path.write_bytes(ledger._encoded(catalog, ledger.MAX_CATALOG_BYTES))
    result = scan(env)
    assert bool(result.blockers) is corrupt and bool(result.references) is corrupt


def test_foreign_profile_cannot_use_the_live_owners_permanent_marker(env):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    marker(env, value, metadata)
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            frozen = captured.bundle
    foreign = lifecycle.StorageProfile(env[0].database.parent / "other-owner.db", env[0].media,
                                       env[0].reports, env[0].checkpoints)
    result = home.parse_bundle(frozen, profile=foreign, root_ids=ROOTS, member_id="foreign_member", source_identity=env[1])
    assert result.references and result.blockers


def test_hardlinked_marker_is_not_read_as_release_authority(env, tmp_path):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    marker(env, value, metadata)
    path = value.data_dir / "sync-no-resend/v1/publications" / (metadata["publication_id"] + ".json")
    outside = tmp_path / "outside-marker.json"
    os.link(path, outside)
    before = outside.read_bytes()
    result = scan(env)
    assert result.references and result.blockers and outside.read_bytes() == before


def test_missing_capture_membership_and_bad_base_image_keep_all_marker_edges(env):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    marker(env, value, metadata)
    with lifecycle.shared(env[0]):
        with home.capture_locked(env[0]) as captured:
            frozen = captured.bundle
    missing = replace(frozen, directory_entries=tuple(item for item in frozen.directory_entries if item[0] != "publications"))
    result = home.parse_bundle(missing, profile=env[0], root_ids=ROOTS, member_id="same_member", source_identity=env[1])
    assert result.references and result.blockers
    write(env, "sync-work-history/" + uuid.uuid4().hex + ".json", b"{")
    assert scan(env).references and scan(env).blockers


@pytest.mark.parametrize("location,count", [("sync-no-resend", 2), ("sync-no-resend/v1", 13)])
def test_sender_directory_companion_budget_matches_live_observation(env, location, count):
    value = store(env)
    _, metadata, _ = outbox(env, legacy=True)
    marker(env, value, metadata)
    for _ in range(count):
        write(env, location + "/.sync-" + uuid.uuid4().hex + ".part", b"")
    result = scan(env)
    assert result.references and "HOME_FILE_COUNT_LIMIT" in {item.code for item in result.blockers}
