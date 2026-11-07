"""State4 source failure facts retain original requests and reference roots."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import sync_source_control_contract as contract, sync_state
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_sync_source_execution_state import ready, request, receipt
from tests.test_sync_state import claim, snapshot, store


def failure():
    return {"format_version": 1, "operation_id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()),
        "ordinal": 1, "input_sha256": "a" * 64, "request_sha256": "b" * 64,
        "settlement_sha256": "c" * 64, "result_sha256": "d" * 64,
        "operation_kind": "source_resolution", "segment": None, "source_contract_version": None,
        "resolver_contract_version": 1, "resolver_phase": "headers", "error_class": "source_unavailable"}


def control_request(core):
    previous = request(core)
    value = {key: deepcopy(previous[key]) for key in (
        "source", "target", "lease", "claim", "recovery_generation", "recovery_stream")}
    value["source"]["handshake"]["migration_version"] = 15
    value.update(kind="source_fail", request_id=str(uuid.uuid4()), core_sha256=sync_state._source_digest(core),
                 submitted_url_sha256=core["submitted_url_sha256"], failure=failure())
    return value


def control_receipt(body):
    return {"format_version": 1, "request_id": body["request_id"], "request_sha256": contract.request_sha256(body),
        "core_sha256": body["core_sha256"], "submitted_url_sha256": body["submitted_url_sha256"],
        "failure_sha256": contract.failure_sha256(body["failure"]), "item_id": body["claim"]["item_id"],
        "lease_generation": body["claim"]["lease_generation"], "attempts": 4, "status": "dead",
        "error_class": body["failure"]["error_class"], "committed_at": "2026-10-02T00:00:00.000Z", "replayed": False}


def controlled(store):
    core = ready(store)
    store.enable_source_control()
    body = control_request(core)
    store.install_source_control_request(body)
    store.record_source_control_receipt(body["request_id"], control_receipt(body))
    return core, store.assert_source_control_pending()


def histories(store):
    return {path.name: path.read_bytes() for path in (store.data_dir / "sync-work-history").glob("*.json")}


def test_explicit_state4_promotion_preserves_v3_and_read_never_promotes(store):
    core = ready(store)
    before = store.read()
    assert StateStore(store.data_dir).read() == before
    after = store.enable_source_control()
    assert after == {**before, "format_version": 4, "source_control_pending": None, "source_admission": None}
    assert store.enable_source_execution() == store.enable_source_control() == after
    assert after["source_execution"] == core
    raw = (store.data_dir / "sync-state.json").read_bytes()
    for changes in ({"source_admission": {}}, {"source_control_pending": None}):
        with pytest.raises(SyncError):
            store.update(changes)
    assert (store.data_dir / "sync-state.json").read_bytes() == raw


def test_state4_parser_has_closed_control_and_rejects_unimplemented_admission(store):
    core, pending = controlled(store)
    state = store.read()
    assert json.loads(sync_state._validate(state)) == state
    for changes in ({"format_version": True}, {"source_admission": {}},
                    {"source_control_pending": {**pending, "url": "https://private.invalid"}}):
        with pytest.raises(SyncError):
            sync_state._validate({**state, **changes})


@pytest.mark.parametrize("field", ["core", "item", "peer", "submitted", "fence"])
def test_control_request_cannot_change_original_execution_binding(store, field):
    core = ready(store)
    store.enable_source_control()
    body = control_request(core)
    if field == "core":
        body["core_sha256"] = "f" * 64
    elif field == "item":
        body["claim"]["item_id"] += 1
    elif field == "peer":
        body["target"]["node_id"] = str(uuid.uuid4())
    elif field == "submitted":
        body["submitted_url_sha256"] = "f" * 64
    else:
        body["recovery_generation"] += 1
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.install_source_control_request(body)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_failure_receipt_history4_precedes_exact_core_control_clear_and_replays(store):
    core, pending = controlled(store)
    before = store.read()
    after = store.complete_source_failure(core, pending)
    assert after == {**before, "source_execution": None, "source_control_pending": None}
    values = histories(store)
    assert len(values) == 1
    history = json.loads(next(iter(values.values())))
    assert history["format_version"] == 4 and history["reason"] == "source_failed"
    assert sync_state.validate_source_history4(history) == history
    assert history["source_execution"] == core and history["source_control_pending"] == pending
    assert store.complete_source_failure(core, pending) == after
    assert histories(store) == values


def test_failure_history_survives_state_write_crash_and_reuses_original_bytes(store, monkeypatch):
    core, pending = controlled(store)
    before = (store.data_dir / "sync-state.json").read_bytes()
    original = sync_state._write
    def interrupt(directory, name, data, **options):
        if name == "sync-state.json":
            raise RuntimeError("synthetic failure clear interruption")
        return original(directory, name, data, **options)
    monkeypatch.setattr(sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError):
        store.complete_source_failure(core, pending)
    assert (store.data_dir / "sync-state.json").read_bytes() == before
    saved = histories(store)
    assert len(saved) == 1
    monkeypatch.setattr(sync_state, "_write", original)
    store.complete_source_failure(core, pending)
    assert histories(store) == saved


@pytest.mark.parametrize("field", ["receipt", "core", "fence"])
def test_stale_or_missing_source_failure_proof_cannot_clear_current_execution(store, field):
    core, pending = controlled(store)
    if field == "receipt":
        pending["receipt"] = None
    elif field == "core":
        core = {**core, "root_job_id": 9}
    else:
        recovery = store.read()["recovery"]
        store.observe_recovery({**recovery, "generation": 1, "revision": 1})
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.complete_source_failure(core, pending)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_unproved_budget_wait_is_not_discarded_by_source_failure_receipt(store):
    core, pending = controlled(store)
    body = request(core)
    store.install_budget_defer_request(body)
    before = store.read()
    after = store.complete_source_failure(core, pending)
    assert after == before
    assert after["budget_pending"]["defer_request"] == body
    assert len(histories(store)) == 1


@pytest.mark.parametrize("field", ["request_id", "lease", "failure"])
def test_installed_failure_intent_only_replays_its_original_body(store, field):
    core = ready(store)
    store.enable_source_control()
    body = control_request(core)
    pending = store.install_source_control_request(body)
    assert store.install_source_control_request(body) == pending
    replacement = deepcopy(body)
    if field == "request_id":
        replacement[field] = str(uuid.uuid4())
    elif field == "lease":
        replacement[field]["lease_token"] = str(uuid.uuid4())
    else:
        replacement[field]["settlement_sha256"] = "f" * 64
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.install_source_control_request(replacement)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


@pytest.mark.parametrize("field", ["request_id", "attempts", "failure_sha256", "item_id"])
def test_failure_receipt_changes_never_replace_original_permanent_proof(store, field):
    core, pending = controlled(store)
    receipt = deepcopy(pending["receipt"])
    receipt[field] = (str(uuid.uuid4()) if field == "request_id" else "f" * 64 if field == "failure_sha256"
                      else receipt[field] + 1)
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises((SyncError, contract.SourceControlContractError)):
        store.record_source_control_receipt(pending["request"]["request_id"], receipt)
    assert (store.data_dir / "sync-state.json").read_bytes() == before
    replayed = {**pending["receipt"], "replayed": True}
    refreshed = store.record_source_control_receipt(pending["request"]["request_id"], replayed)
    after = store.complete_source_failure(core, refreshed)
    history = json.loads(next(iter(histories(store).values())))
    assert history["source_control_pending"]["receipt"]["replayed"] is False
    assert after["source_execution"] is None


@pytest.mark.parametrize("mutation", ["unknown", "bool", "sha", "kind", "reason", "proof", "missing"])
def test_history4_requires_closed_exact_source_failure_terminal(store, mutation):
    core, pending = controlled(store)
    store.complete_source_failure(core, pending)
    history = json.loads(next(iter(histories(store).values())))
    if mutation == "unknown":
        history["private_url"] = "https://private.invalid"
    elif mutation == "bool":
        history["source_terminal"]["format_version"] = True
    elif mutation == "sha":
        history["source_terminal"]["core_sha256"] = "f" * 64
    elif mutation == "kind":
        history["source_terminal"]["kind"] = "source_published"
    elif mutation == "reason":
        history["reason"] = "recovery"
    elif mutation == "proof":
        history["source_terminal"]["proof"]["receipt"]["failure_sha256"] = "f" * 64
    else:
        history["source_terminal"] = None
    with pytest.raises(SyncError):
        sync_state.validate_source_history4(history)


def test_pending_publication_remains_original_root_after_source_failure_is_recorded(store):
    core, pending = controlled(store)
    payload = snapshot()
    payload["version"]["library_id"] = core["binding"]["home_library_id"]
    metadata = store.write_publication(payload, [])
    store.update({"pending_publication": metadata})
    original_bytes = (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes()
    before = store.read()
    assert store.complete_source_failure(core, pending) == before
    assert (store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")).read_bytes() == original_bytes
    history = json.loads(next(iter(histories(store).values())))
    assert history["pending_publication"] == metadata and history["source_execution"] == core


def test_source_failure_keeps_saved_claim_work_and_uploaded_cache_in_state_and_history(store):
    descriptor = {"video_id": "BVtest", "sha256": "e" * 64, "byte_size": 20, "extension": "mp4"}
    store.update({"uploaded_media": {"BVtest": descriptor}})
    core, pending = controlled(store)
    store.update({"claims": [claim(7)], "work": {"item_id": 7, "lease_generation": 3,
        "video_id": "BVtest", "phase": "downloaded"}})
    before = store.read()
    after = store.complete_source_failure(core, pending)
    history = json.loads(next(iter(histories(store).values())))
    for key in ("claims", "work", "uploaded_media", "publication_media"):
        assert after[key] == history[key] == before[key]


def test_cleared_failure_without_its_original_history_cannot_recreate_terminal_proof(store):
    core, pending = controlled(store)
    store.complete_source_failure(core, pending)
    original = next((store.data_dir / "sync-work-history").glob("*.json"))
    original.unlink()
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.complete_source_failure(core, pending)
    assert histories(store) == {} and (store.data_dir / "sync-state.json").read_bytes() == before


def test_completed_failure_lookup_is_read_only_and_only_matches_original_job_lineage(store):
    core, pending = controlled(store)
    assert store.completed_source_failure(root_job_id=core["root_job_id"],
        continuation_id=core["continuation_id"], execution_generation=core["execution_generation"]) is None
    store.complete_source_failure(core, pending)
    original = (store.data_dir / "sync-state.json").read_bytes()
    saved = histories(store)
    options = {key: core[key] for key in ("root_job_id", "continuation_id", "execution_generation")}
    history = store.completed_source_failure(**options)
    assert history == json.loads(next(iter(saved.values())))
    for field in options:
        changed = {**options, field: str(uuid.uuid4()) if field == "continuation_id" else options[field] + 1}
        assert store.completed_source_failure(**changed) is None
    assert (store.data_dir / "sync-state.json").read_bytes() == original and histories(store) == saved


@pytest.mark.parametrize("mutation", ["renamed", "damaged", "multiple", "overbound", "bytes", "vanished"])
def test_completed_failure_lookup_rejects_unproved_or_ambiguous_history(store, monkeypatch, mutation):
    core, pending = controlled(store)
    store.complete_source_failure(core, pending)
    original = next((store.data_dir / "sync-work-history").glob("*.json"))
    if mutation == "renamed":
        original.rename(original.with_name(uuid.uuid4().hex + ".json"))
    elif mutation == "damaged":
        history = json.loads(original.read_bytes())
        history["source_terminal"]["proof"]["receipt"]["failure_sha256"] = "f" * 64
        original.write_text(json.dumps(history), encoding="utf-8")
    elif mutation == "multiple":
        store.install_source_execution(core)
        another = control_request(core)
        store.install_source_control_request(another)
        store.record_source_control_receipt(another["request_id"], control_receipt(another))
        store.complete_source_failure(core, store.assert_source_control_pending())
    elif mutation == "overbound":
        original.with_name("synthetic-temporary").write_bytes(b"")
        monkeypatch.setattr(sync_state, "MAX_SOURCE_HISTORY_LOOKUP", 1)
    elif mutation == "bytes":
        monkeypatch.setattr(sync_state, "MAX_SOURCE_HISTORY_LOOKUP_BYTES", 1)
    else:
        read = sync_state._read_bytes
        def lost(directory, name, maximum, **kwargs):
            if name == original.name:
                raise FileNotFoundError("synthetic disappeared committed history")
            return read(directory, name, maximum, **kwargs)
        monkeypatch.setattr(sync_state, "_read_bytes", lost)
    with pytest.raises(SyncError):
        store.completed_source_failure(root_job_id=core["root_job_id"],
            continuation_id=core["continuation_id"], execution_generation=core["execution_generation"])


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legal_old_history_contracts_remain_ignored_as_job_completion_proof(store, version):
    core, pending = controlled(store)
    store.complete_source_failure(core, pending)
    history = json.loads(next(iter(histories(store).values())))
    old = {key: deepcopy(value) for key, value in history.items() if key not in {
        "source_admission", "source_control_pending"}}
    old.update(format_version=version, reason="claim_changed")
    old["source_terminal"] = None
    if version < 3:
        for key in ("source_execution", "budget_pending", "source_terminal"):
            old.pop(key)
    if version == 1:
        for key in ("sender_contract", "publication_media", "uploaded_media"):
            old.pop(key)
    old_path = store.data_dir / "sync-work-history" / (uuid.uuid4().hex + ".json")
    old_path.write_text(json.dumps(old), encoding="utf-8")
    old_path.chmod(0o600)
    found = store.completed_source_failure(root_job_id=core["root_job_id"],
        continuation_id=core["continuation_id"], execution_generation=core["execution_generation"])
    assert found == history and len(histories(store)) == 2


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_version_cannot_hide_unknown_terminal_or_private_history_fields(store, version):
    core, pending = controlled(store)
    store.complete_source_failure(core, pending)
    (store.data_dir / "sync-work-history" / (uuid.uuid4().hex + ".json")).write_text(
        json.dumps({"format_version": version, "source_terminal": {"kind": "source_failed"}}), encoding="utf-8")
    with pytest.raises(SyncError):
        store.completed_source_failure(root_job_id=core["root_job_id"],
            continuation_id=core["continuation_id"], execution_generation=core["execution_generation"])


@pytest.mark.parametrize("version", [3, 4])
def test_completed_publication_lookup_keeps_original_success_distinct_from_failure(store, version):
    from tests.test_sync_budget_agent import published_source
    core, request = published_source(store)
    if version == 4:
        store.enable_source_control()
    store.complete_published_source(core, request)
    options = {key: core[key] for key in ("root_job_id", "continuation_id", "execution_generation")}
    assert store.completed_source_failure(**options) is None
    history = store.completed_source_publication(**options)
    assert history["reason"] == "source_published" and history["format_version"] == version
    assert history["source_terminal"]["proof"]["request"] == request


def test_conflicting_failure_and_publication_facts_cannot_choose_an_arbitrary_job_terminal(store):
    from tests.test_sync_budget_agent import published_source
    core, request = published_source(store)
    store.enable_source_control()
    store.complete_published_source(core, request)
    envelope = store.read_publication(request["metadata"], proved_stream=core["recovery_stream"])
    store.acknowledge_publication(request["metadata"], recovery_generation=core["recovery_generation"],
        recovery_stream=core["recovery_stream"], version=envelope["snapshot"]["version"],
        content_sha256="a" * 64, reports_sha256="b" * 64, preserve_work=False)
    store.install_source_execution(core)
    body = control_request(core)
    store.install_source_control_request(body)
    store.record_source_control_receipt(body["request_id"], control_receipt(body))
    store.complete_source_failure(core, store.assert_source_control_pending())
    options = {key: core[key] for key in ("root_job_id", "continuation_id", "execution_generation")}
    for lookup in (store.completed_source_failure, store.completed_source_publication):
        with pytest.raises(SyncError):
            lookup(**options)
