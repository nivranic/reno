"""State5 representations cannot manufacture permanent or SQL authority."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import sync_state
from reno.sync_protocol import SyncError
from tests.test_sync_state import store
from tests.test_sync_source_execution_state import ready
from tests.test_sync_source_control_state import controlled


def refs(core):
    core_sha = sync_state._source_digest(core)
    ref = lambda kind: {"format_version": 1, "record_kind": kind, "record_id": str(uuid.uuid4()), "record_sha256": "a" * 64}
    return {"source_authority": {"format_version": 1, "core_sha256": core_sha,
        "origin": ref("origin"), "defer": ref("control"), "resume": None, "failure": None, "publication": ref("publication")},
        "source_admission": {"format_version": 1, "admission_id": str(uuid.uuid4()), "core_sha256": core_sha,
            "request_sha256": "b" * 64, "admission_sha256": "c" * 64, "record_sha256": "d" * 64},
        "source_recovery_pending": {"format_version": 1, "kind": "source_dispose", "request_id": str(uuid.uuid4()),
            "core_sha256": core_sha, "request_sha256": "e" * 64, "intent_sha256": "f" * 64}}


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_explicit_promotion_keeps_old_facts_and_does_not_create_origin(store, version):
    if version >= 2:
        store.enable_no_resend()
    if version == 3:
        ready(store)
    if version == 4:
        controlled(store)
    before = store.read()
    raw = (store.data_dir / "sync-state.json").read_bytes()
    assert sync_state.StateStore(store.data_dir).read() == before
    assert (store.data_dir / "sync-state.json").read_bytes() == raw
    after = store.enable_source_readmit()
    for key, value in before.items():
        if key != "format_version":
            assert after[key] == value
    assert after["format_version"] == 5
    assert after["source_authority"] is after["source_recovery_pending"] is after["source_admission"] is None
    assert not (store.data_dir / "sync-source-authority").exists()
    frozen = (store.data_dir / "sync-state.json").read_bytes()
    assert store.enable_source_readmit() == store.enable_source_control() == store.enable_source_execution() == after
    assert (store.data_dir / "sync-state.json").read_bytes() == frozen


def test_state5_public_projection_is_detached_and_has_no_operational_secret(store, monkeypatch):
    core = ready(store)
    value = {**store.enable_source_readmit(), **refs(core)}
    def forbidden(*args, **kwargs):
        pytest.fail("Pure projection acquired operational ownership")
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    projected = sync_state.source_readmit_projection(value)
    assert set(projected) == {"format_version", "state_format_version", "core_sha256", "source_authority", "source_admission", "source_recovery_pending", "refset_sha256"}
    public = {key: projected[key] for key in ("core_sha256", "source_authority", "source_admission", "source_recovery_pending")}
    assert projected["refset_sha256"] == sync_state._source_digest(public)
    assert value["binding"]["target_url"] not in json.dumps(projected)
    projected["source_authority"]["origin"]["record_id"] = str(uuid.uuid4())
    assert projected != sync_state.source_readmit_projection(value)


@pytest.mark.parametrize("field", ["source_authority", "source_admission", "source_recovery_pending"])
def test_state5_cannot_have_authority_without_original_core(store, field):
    core = ready(store)
    value = {**store.enable_source_readmit(), **refs(core)}
    value.update(source_execution=None, source_authority=None, source_admission=None, source_recovery_pending=None)
    value[field] = refs(core)[field]
    with pytest.raises(SyncError):
        sync_state._validate(value)


@pytest.mark.parametrize("change", ["version", "extra", "core", "ref_kind", "ref_sha", "pending_kind", "pending_sha", "admission_sha"])
def test_state5_rejects_unclosed_typed_refs_before_projecting(store, change):
    core = ready(store)
    value = {**store.enable_source_readmit(), **refs(core)}
    if change == "version":
        value["format_version"] = True
    elif change == "extra":
        value["source_authority"]["private_body"] = {"secret": "must-not-leak"}
    elif change == "core":
        value["source_authority"]["core_sha256"] = "b" * 64
    elif change == "ref_kind":
        value["source_authority"]["publication"]["record_kind"] = "control"
    elif change == "ref_sha":
        value["source_authority"]["origin"]["record_sha256"] = "A" * 64
    elif change == "pending_kind":
        value["source_recovery_pending"]["kind"] = "ordinary_claim"
    elif change == "pending_sha":
        value["source_recovery_pending"]["intent_sha256"] = "f" * 63
    else:
        value["source_admission"]["core_sha256"] = "c" * 64
    with pytest.raises(SyncError):
        sync_state.source_readmit_projection(value)


def test_legacy_state_does_not_accept_state5_fields_or_public_projection(store):
    ready(store)
    value = store.enable_source_control()
    with pytest.raises(SyncError):
        sync_state.source_readmit_projection(value)
    with pytest.raises(SyncError):
        sync_state._validate({**value, "source_authority": None, "source_recovery_pending": None})
    promoted = store.enable_source_readmit()
    before = (store.data_dir / "sync-state.json").read_bytes()
    for key in ("source_authority", "source_admission", "source_recovery_pending"):
        with pytest.raises(SyncError):
            store.update({key: deepcopy(refs(promoted["source_execution"])[key])})
    assert (store.data_dir / "sync-state.json").read_bytes() == before


def test_history5_retains_exact_refs_and_terminal2_has_a_distinct_stable_identity(store):
    core, pending = controlled(store)
    value = {**store.enable_source_readmit(), **refs(core)}
    value["source_authority"]["failure"] = {"format_version": 1, "record_kind": "control",
        "record_id": pending["request"]["request_id"], "record_sha256": "b" * 64}
    original = sync_state._source_terminal("source_failed", pending, {**value, "format_version": 4})
    terminal = {**original, "format_version": 2, "authority_ref": deepcopy(value["source_authority"]["failure"]), "authority_sha256": "a" * 64}
    history = store._source_history(value, "source_failed", terminal)
    assert sync_state.validate_source_history5(history) == history
    assert history["source_authority"] == value["source_authority"]
    assert history["source_recovery_pending"] == value["source_recovery_pending"]
    assert sync_state._source_history_name(history) == sync_state._source_history_name(deepcopy(history))
    changed = deepcopy(history)
    changed["source_terminal"]["authority_sha256"] = "c" * 64
    assert sync_state._source_history_name(changed) != sync_state._source_history_name(history)
    with pytest.raises(SyncError):
        sync_state.validate_source_history4(history)


@pytest.mark.parametrize("change", ["old_terminal", "foreign_ref", "ref_kind", "authority_sha", "proof_sha", "missing_authority", "extra"])
def test_history5_cannot_hide_wrong_terminal_or_authority_reference(store, change):
    core, pending = controlled(store)
    value = {**store.enable_source_readmit(), **refs(core)}
    value["source_authority"]["failure"] = {"format_version": 1, "record_kind": "control",
        "record_id": pending["request"]["request_id"], "record_sha256": "b" * 64}
    original = sync_state._source_terminal("source_failed", pending, {**value, "format_version": 4})
    terminal = {**original, "format_version": 2, "authority_ref": deepcopy(value["source_authority"]["failure"]), "authority_sha256": "a" * 64}
    history = store._source_history(value, "source_failed", terminal)
    if change == "old_terminal":
        history["source_terminal"] = original
    elif change == "foreign_ref":
        history["source_terminal"]["authority_ref"]["record_id"] = str(uuid.uuid4())
    elif change == "ref_kind":
        history["source_terminal"]["authority_ref"]["record_kind"] = "publication"
    elif change == "authority_sha":
        history["source_terminal"]["authority_sha256"] = "A" * 64
    elif change == "proof_sha":
        history["source_terminal"]["proof_sha256"] = "c" * 64
    elif change == "missing_authority":
        history["source_authority"] = None
    else:
        history["source_terminal"]["private_body"] = "never exported"
    with pytest.raises(SyncError):
        sync_state.validate_source_history5(history)
