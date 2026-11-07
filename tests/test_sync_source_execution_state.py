"""Private Home execution proofs and frozen budget intents use disposable state."""
from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from reno import sync_state
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.test_sync_state import store


def execution(value):
    state = value.read()
    return {"format_version": 1, "binding": deepcopy(state["binding"]), "item_id": 7,
            "submitted_url_sha256": "a" * 64, "root_job_id": 3,
            "continuation_id": str(uuid.uuid4()), "execution_generation": 1,
            "local_epoch": state["local_epoch"], "recovery_generation": 0,
            "recovery_stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1},
            "resolution_input_sha256": "b" * 64}


def enable(value):
    state = value.read()
    value.observe_recovery({"generation": 0, "revision": 0, "state": "running", "source_floor": None,
        "stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}})
    return value.enable_source_execution()


def request(core):
    binding = core["binding"]
    return {"kind": "budget_defer", "request_id": str(uuid.uuid4()),
        "source": {"library_id": binding["home_library_id"], "node_id": binding["home_node_id"],
                   "handshake": {"protocol_version": 2, "migration_version": 14, "core_schema_sha256": "c" * 64}},
        "target": {"library_id": binding["target_library_id"], "node_id": binding["target_node_id"]},
        "lease": {"session_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4()), "generation": 1,
                  "lease_until": "2030-01-01T00:00:00.000Z"},
        "claim": {"item_id": core["item_id"], "lease_token": str(uuid.uuid4()), "lease_generation": 2},
        "recovery_generation": core["recovery_generation"], "recovery_stream": deepcopy(core["recovery_stream"]),
        "reason": "daily_limit", "retry_at": "2030-01-02T00:00:00.000Z",
        "budget_operation_id": str(uuid.uuid4())}


def receipt(body, *, item_id=7, lease_generation=2):
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"request_id": body["request_id"], "request_sha256": digest,
        "item_id": item_id, "lease_generation": lease_generation,
        "status": "budget_wait" if body["kind"] == "budget_defer" else "queued", "attempts": 4,
        "reason": body.get("reason"), "retry_at": body.get("retry_at"),
        "budget_operation_id": body.get("budget_operation_id"),
        "committed_at": "2030-01-01T00:00:00.000Z", "replayed": False}


def ready(value):
    enable(value)
    core = execution(value)
    value.install_source_execution(core)
    return core


def resume(body):
    value = {key: deepcopy(body[key]) for key in ("source", "target", "lease", "recovery_generation", "recovery_stream")}
    value.update(kind="budget_resume", request_id=str(uuid.uuid4()), defer_request_id=body["request_id"])
    return value


def test_strict_v3_parser_preserves_sender_contract_and_has_bounded_new_fields(store):
    base = store.enable_no_resend()
    value = {**base, "format_version": 3, "source_execution": None, "budget_pending": None}
    assert json.loads(sync_state._validate(value)) == value
    for changes in ({"format_version": True}, {"private_url": "https://private.invalid"},
                    {"source_execution": {}}, {"budget_pending": {}}):
        with pytest.raises(SyncError):
            sync_state._validate({**value, **changes})


@pytest.mark.parametrize("version", [1, 2])
def test_old_read_and_initialize_never_upgrade_or_add_proofs(store, version):
    if version == 2:
        store.enable_no_resend()
    path = store.data_dir / "sync-state.json"
    before = path.read_bytes()
    observed = StateStore(store.data_dir).read()
    assert observed["format_version"] == version
    assert "source_execution" not in observed and "budget_pending" not in observed
    assert store.initialize(observed["binding"], observed["local_epoch"]) == observed
    assert path.read_bytes() == before
    with pytest.raises(SyncError):
        store.install_source_execution(execution(store))
    assert path.read_bytes() == before


def test_enable_is_explicit_idempotent_and_keeps_prior_state_exact(store):
    before = store.enable_no_resend()
    after = store.enable_source_execution()
    assert after == {**before, "format_version": 3, "source_execution": None, "budget_pending": None}
    frozen = (store.data_dir / "sync-state.json").read_bytes()
    assert store.enable_source_execution() == after
    assert store.enable_no_resend() == after
    assert (store.data_dir / "sync-state.json").read_bytes() == frozen


@pytest.mark.parametrize("changes", [
    {"format_version": True}, {"item_id": True}, {"root_job_id": None}, {"root_job_id": 0},
    {"continuation_id": "private-path"}, {"execution_generation": False}, {"local_epoch": 2},
    {"recovery_generation": -1}, {"submitted_url_sha256": "A" * 64},
    {"resolution_input_sha256": "b" * 63}, {"submitted_url": "https://private.invalid/source"},
    {"recovery_stream": {"library_id": str(uuid.uuid4()), "library_epoch": 1}},
])
def test_invalid_source_execution_never_installs_or_changes_state(store, changes):
    enable(store)
    core = {**execution(store), **changes}
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.install_source_execution(core)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_source_execution_replay_is_exact_detached_and_generic_update_cannot_replace_it(store):
    core = ready(store)
    assert store.assert_source_execution(core) == core
    copy = store.install_source_execution(core)
    copy["binding"]["home_node_id"] = str(uuid.uuid4())
    assert store.assert_source_execution() == core
    before = (store.data_dir / "sync-state.json").read_bytes()
    for bad in ({**core, "item_id": 8}, {**core, "root_job_id": 4},
                {**core, "resolution_input_sha256": "d" * 64}):
        with pytest.raises(SyncError):
            store.install_source_execution(bad)
        with pytest.raises(SyncError):
            store.assert_source_execution(bad)
    for changes in ({"source_execution": core}, {"source_execution": None}, {"budget_pending": None}):
        with pytest.raises(SyncError):
            store.update(changes)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_budget_requests_and_receipts_are_persisted_in_original_order_and_replayed(store):
    core = ready(store)
    original = request(core)
    proof = receipt(original)
    assert store.install_budget_defer_request(original)["defer_request"] == original
    reopened = StateStore(store.data_dir, profile=store.profile)
    assert reopened.assert_budget_pending()["defer_receipt"] is None
    assert reopened.install_budget_defer_request(original)["defer_request"] == original
    assert reopened.record_budget_defer_receipt(original["request_id"], proof)["defer_receipt"] == proof
    replayed = {**proof, "replayed": True}
    assert reopened.record_budget_defer_receipt(original["request_id"], replayed)["defer_receipt"] == replayed
    followup = resume(original)
    assert reopened.install_budget_resume_request(followup)["resume_request"] == followup
    result = receipt(followup)
    assert reopened.record_budget_resume_receipt(followup["request_id"], result)["resume_receipt"] == result
    saved = reopened.assert_budget_pending()
    assert saved["defer_request"] == original and saved["resume_request"] == followup
    saved["defer_request"]["lease"]["generation"] = 9
    assert reopened.assert_budget_pending()["defer_request"] == original
    assert reopened.read()["source_execution"] == core


def test_budget_pending_requires_execution_and_cannot_install_resume_before_defer_receipt(store):
    enable(store)
    core = execution(store)
    original = request(core)
    with pytest.raises(SyncError):
        store.install_budget_defer_request(original)
    store.install_source_execution(core)
    store.install_budget_defer_request(original)
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.install_budget_resume_request(resume(original))
    with pytest.raises(SyncError):
        store.record_budget_resume_receipt(str(uuid.uuid4()), receipt(resume(original)))
    assert (store.data_dir / "sync-state.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["peer", "item", "body", "id", "hash", "attempts", "time", "unknown"])
def test_budget_pending_rejects_foreign_or_changed_requests_and_damaged_receipts(store, mutation):
    core = ready(store)
    original = request(core)
    store.install_budget_defer_request(original)
    before = (store.data_dir / "sync-state.json").read_bytes()
    changed, proof = deepcopy(original), receipt(original)
    if mutation == "peer":
        changed["target"]["node_id"] = str(uuid.uuid4())
    elif mutation == "item":
        changed["claim"]["item_id"] = 8
    elif mutation == "body":
        changed["retry_at"] = "2030-01-03T00:00:00.000Z"
    elif mutation == "id":
        changed["request_id"] = str(uuid.uuid4())
    elif mutation == "hash":
        proof["request_sha256"] = "f" * 64
    elif mutation == "attempts":
        proof["attempts"] = True
    elif mutation == "time":
        proof["committed_at"] = "private-path"
    else:
        proof["raw_url"] = "https://private.invalid/source"
    with pytest.raises(SyncError):
        if mutation in {"peer", "item", "body", "id"}:
            store.install_budget_defer_request(changed)
        else:
            store.record_budget_defer_receipt(original["request_id"], proof)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_recorded_receipt_only_allows_replayed_flag_to_change(store):
    core = ready(store)
    original = request(core)
    proof = receipt(original)
    store.install_budget_defer_request(original)
    store.record_budget_defer_receipt(original["request_id"], proof)
    before = (store.data_dir / "sync-state.json").read_bytes()
    for field, value in (("attempts", 3), ("committed_at", "2030-01-01T00:00:00.001Z")):
        with pytest.raises(SyncError):
            store.record_budget_defer_receipt(original["request_id"], {**proof, field: value})
    assert (store.data_dir / "sync-state.json").read_bytes() == before
