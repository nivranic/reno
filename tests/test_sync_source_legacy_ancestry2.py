"""Ancestry projection and real reads of controlled existing history.

The isolated Genesis/restore fixture uses real local producers. Wire1 and
wire2 admission documents below are controlled preexisting reader fixtures;
they are not evidence of an old Cloud producer, migration, HTTP acceptance or
receipt-first State CAS. Pure tests copy State selectors without granting a
seal. Factory tests explicitly install an existing State baseline and then
assert that reading it changes neither original files nor paid SQL rows.
"""
from contextvars import copy_context
from copy import deepcopy
import hashlib

import pytest

from reno import sync_source_admission_history1 as history
from reno import sync_source_authority as permanent
from reno import sync_source_legacy_ancestry2 as ancestry
from reno import sync_source_readmit_contract as wire, sync_source_readmit_local2 as local
from reno import sync_state
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract as sample
from tests.test_sync_source_admission_history1 import (
    actual_history, env, restored_origin17,
    _admit, _before, _control, _id, _negative, _ref, _rehash, _seed, _unchanged,
)


def _entries(value, records):
    """Pure committed DTOs only; this does not manufacture an owner or seal."""
    entries = {folder: {} for folder in permanent.KINDS}
    for folder, record in value.original_records + records:
        entries[folder][record["record_id"]] = {"state": "committed", "record": deepcopy(record)}
    return entries


def _state_for(value, receipt):
    """Select a controlled receipt in a copy, without any State effect."""
    state = deepcopy(value.store.read())
    state["source_admission"] = _ref(receipt)
    for claim in state["claims"]:
        if claim["item_id"] == receipt["result"]["item_id"]:
            claim.update(claim=deepcopy(receipt["result"]["claim"]),
                         attempts=receipt["result"]["attempts"],
                         lease_until=receipt["result"]["session"]["lease_until"])
    return state


def _install_existing_current(value, receipt):
    """Install an isolated existing baseline, not an operational admission CAS."""
    state = _state_for(value, receipt)
    raw = sync_state._validate(state)
    with value.store.reference_operation():
        (value.store.data_dir / "sync-state.json").write_bytes(raw)
    assert value.store.read() == state


def _pair2(value, previous=None):
    """Canonical stored reader documents; provenance DTO is not an HTTP seal."""
    old_intent, _ = _admit(value, previous=previous)
    body = deepcopy(old_intent["request"])
    body.update(format_version=2, resume_basis=body.pop("manual_resume_basis"))
    body = wire.normalize_readmit_request(body)
    result = sample.admission(body, attempts=3 if previous is None else previous["result"]["attempts"])
    result["format_version"] = 2
    result["admission_sha256"] = wire.admission_sha256(result)
    result = wire.normalize_readmit_result(body, result)
    basis = {"format_version": 1, "kind": "original_core" if previous is None else "admission",
        "core_sha256": value.origin["core_sha256"], "home_epoch": body["current_home_epoch"],
        "session": deepcopy(body["lease"]), "claim": deepcopy(body["expected_claim"]),
        "recovery_generation": body["recovery_generation"],
        "recovery_stream": deepcopy(body["recovery_stream"]),
        "control_revision": body["control_revision"], "origin_ref": permanent.record_ref(value.origin),
        "admission_ref": None if previous is None else _ref(previous),
        "continuity": deepcopy(value.remote.payloads["continuity"]["continuity"])}
    intent = {"format_version": 2, "record_kind": "recovery_intent", "record_id": body["request_id"],
        "core_sha256": value.origin["core_sha256"], "request_sha256": wire.request_sha256(body),
        "request": body, "expected_before": _before(value, body, previous), "creation_basis": basis,
        "preservation_provenance": deepcopy(value.preservation_provenance), "resume_provenance": None}
    receipt = {"format_version": 2, "record_kind": "recovery_receipt", "record_id": body["request_id"],
        "core_sha256": value.origin["core_sha256"], "request_sha256": intent["request_sha256"],
        "request_ref": permanent.record_ref(intent), "result": result,
        "result_sha256": permanent.digest(result), "parent_defer": None,
        "transport_provenance": {"format_version": 1, "producer": "sync_http", "endpoint": "source/readmit",
            "target_url_sha256": hashlib.sha256(value.activation["binding"]["target_url"].encode()).hexdigest(),
            "request_sha256": intent["request_sha256"]}}
    assert permanent.validate_record("requests", intent, value.activation) == intent
    assert permanent.validate_record("receipts", receipt, value.activation) == receipt
    return intent, receipt


def _rehash2(intent, receipt):
    _rehash(intent, receipt)
    receipt["request_ref"] = permanent.record_ref(intent)
    receipt["transport_provenance"]["request_sha256"] = intent["request_sha256"]


def _closed_wire(intent, receipt):
    """Bad ancestry must not rely on a request/result normalizer failure."""
    assert wire.normalize_readmit_request(intent["request"]) == intent["request"]
    assert wire.normalize_readmit_result(intent["request"], receipt["result"]) == receipt["result"]
    assert intent["request_sha256"] == receipt["request_sha256"] == wire.request_sha256(intent["request"])
    assert receipt["result_sha256"] == permanent.digest(receipt["result"])


def test_current_wire1_selects_only_its_exact_consumed_prefix(actual_history):
    value = actual_history
    control = _control(value)
    consumed_records, negative = _negative(value, control)
    intent, receipt = _admit(value, dispositions=(negative,))
    unconsumed = _control(value)
    records = consumed_records + [("requests", intent), ("receipts", receipt), ("controls", unconsumed)]
    entries, state = _entries(value, records), _state_for(value, receipt)
    before = deepcopy((entries, state))
    facts = ancestry.legacy_consumption2(entries, state)
    assert facts["selector_ref"] == _ref(receipt)
    assert facts["admission_refs"] == (_ref(receipt),)
    assert facts["control_refs"] == (permanent.record_ref(control),)
    assert facts["consumed_controls"][0]["disposition"] == "not_applied"
    assert permanent.record_ref(unconsumed) not in facts["control_refs"]
    assert (entries, state) == before


def test_current_wire2_chain_reaches_exact_wire1_ancestors_and_ignores_pure_side_branch(actual_history):
    value = actual_history
    first_intent, first = _admit(value)
    second_intent, second = _admit(value, previous=first)
    third_intent, third = _pair2(value, second)
    fourth_intent, fourth = _pair2(value, third)
    side_intent, side = _admit(value)
    # This deliberately extra pure DTO is not seeded as a valid facility.
    # A same-core sibling cannot replace the State-selected ancestry.
    records = [(folder, record) for intent, receipt in (
        (first_intent, first), (second_intent, second), (third_intent, third),
        (fourth_intent, fourth), (side_intent, side))
        for folder, record in (("requests", intent), ("receipts", receipt))]
    entries, state = _entries(value, records), _state_for(value, fourth)
    before = deepcopy((entries, state))
    assert ancestry.legacy_prefix2(entries, state) == _ref(second)
    facts = ancestry.legacy_consumption2(entries, state)
    assert facts["admission_refs"] == (_ref(first), _ref(second))
    assert facts["selector_ref"] == _ref(second)
    assert facts["control_refs"] == ()
    assert _ref(side) not in facts["admission_refs"]
    assert third["result"]["attempts"] == fourth["result"]["attempts"] == second["result"]["attempts"]
    assert (entries, state) == before


def test_wrong_state_record_sha_cannot_select_a_same_id_admission(actual_history):
    value = actual_history
    intent, receipt = _admit(value)
    entries, state = _entries(value, [("requests", intent), ("receipts", receipt)]), _state_for(value, receipt)
    state["source_admission"]["record_sha256"] = "f" * 64
    with pytest.raises(SyncError):
        ancestry.legacy_consumption2(entries, state)


@pytest.mark.parametrize("change", ("original_url", "original_claim", "future_epoch"))
def test_first_legacy_selector_checks_the_actual_root_before_returning(actual_history, change):
    value = actual_history
    intent, receipt = _admit(value)
    body, result = intent["request"], receipt["result"]
    if change == "original_url":
        body["original"]["submitted_url_sha256"] = "f" * 64
        result["submitted_url_sha256"] = "f" * 64
    elif change == "original_claim":
        body["original"]["claim"]["lease_token"] = _id()
        body["expected_claim"] = deepcopy(body["original"]["claim"])
        intent["expected_before"]["claim"] = deepcopy(body["expected_claim"])
    else:
        body["current_home_epoch"] += 1
        body["preservation"]["to_epoch"] = body["current_home_epoch"]
        result.update(current_home_epoch=body["current_home_epoch"], preservation=deepcopy(body["preservation"]))
        intent["expected_before"]["local_epoch"] = body["current_home_epoch"]
    _rehash(intent, receipt)
    _closed_wire(intent, receipt)
    entries = _entries(value, [("requests", intent), ("receipts", receipt)])
    with pytest.raises(SyncError):
        ancestry.legacy_prefix2(entries, _state_for(value, receipt))


@pytest.mark.parametrize("change", (
    "original_url", "original_claim", "receipt_core", "future_epoch", "before_binding", "before_core",
    "before_epoch", "basis_epoch", "before_origin", "before_authority_core", "before_authority_none",
    "basis_origin", "parent_core", "parent_claim", "held_attempts",
))
def test_self_consistent_wire2_hashes_cannot_replace_the_actual_root_or_parent(actual_history, change):
    value = actual_history
    old_intent, old = _admit(value)
    intent, receipt = _pair2(value, old)
    body, result = intent["request"], receipt["result"]
    if change == "original_url":
        body["original"]["submitted_url_sha256"] = "f" * 64
        result["submitted_url_sha256"] = "f" * 64
    elif change == "original_claim":
        body["original"]["claim"]["lease_token"] = _id()
    elif change == "receipt_core":
        # Keep request/result root, canonical wire and State-selected SHA
        # consistent; only the stored receipt wrapper names another core.
        receipt["core_sha256"] = "f" * 64
    elif change == "future_epoch":
        body["current_home_epoch"] += 1
        body["preservation"]["to_epoch"] = body["current_home_epoch"]
        result.update(current_home_epoch=body["current_home_epoch"], preservation=deepcopy(body["preservation"]))
        intent["creation_basis"]["home_epoch"] = body["current_home_epoch"]
        intent["expected_before"]["local_epoch"] = body["current_home_epoch"]
    elif change == "before_binding":
        intent["expected_before"]["binding"]["target_url"] = "https://other.example.test/reno"
    elif change == "before_core":
        intent["expected_before"]["core_sha256"] = "f" * 64
    elif change == "before_epoch":
        intent["expected_before"]["local_epoch"] = body["current_home_epoch"] + 1
    elif change == "basis_epoch":
        intent["creation_basis"]["home_epoch"] = body["current_home_epoch"] + 1
    elif change == "before_origin":
        intent["expected_before"]["source_authority"]["origin"]["record_sha256"] = "f" * 64
    elif change == "before_authority_core":
        intent["expected_before"]["source_authority"]["core_sha256"] = "f" * 64
    elif change == "before_authority_none":
        intent["expected_before"]["source_authority"] = None
    elif change == "basis_origin":
        intent["creation_basis"]["origin_ref"]["record_sha256"] = "f" * 64
    elif change == "parent_core":
        old_intent["request"]["original"]["core_sha256"] = "f" * 64
        old_intent["core_sha256"] = "f" * 64
        old_intent["expected_before"]["core_sha256"] = "f" * 64
        old["result"]["core_sha256"] = "f" * 64
        _rehash(old_intent, old)
        intent["creation_basis"]["admission_ref"] = _ref(old)
        intent["expected_before"]["admission_ref"] = _ref(old)
    elif change == "parent_claim":
        wrong = {**body["expected_claim"], "lease_token": _id()}
        body["expected_claim"] = deepcopy(wrong)
        intent["creation_basis"]["claim"] = deepcopy(wrong)
        intent["expected_before"]["claim"] = deepcopy(wrong)
    else:
        result["attempts"] = old["result"]["attempts"] + 1
    _rehash2(intent, receipt)
    _closed_wire(old_intent, old)
    _closed_wire(intent, receipt)
    entries = _entries(value, [("requests", old_intent), ("receipts", old),
        ("requests", intent), ("receipts", receipt)])
    state = _state_for(value, receipt)
    with pytest.raises(SyncError):
        ancestry.legacy_consumption2(entries, state)


@pytest.mark.parametrize("missing", ("old_ancestor", "consumed_control", "registration"))
def test_missing_wire1_ancestry_or_consumption_is_not_a_smaller_valid_prefix(actual_history, missing):
    value = actual_history
    control = _control(value)
    records, negative = _negative(value, control)
    first_intent, first = _admit(value, dispositions=(negative,))
    second_intent, second = _admit(value, previous=first)
    current_intent, current = _pair2(value, second)
    entries = _entries(value, records + [("requests", first_intent), ("receipts", first),
        ("requests", second_intent), ("receipts", second),
        ("requests", current_intent), ("receipts", current)])
    if missing == "old_ancestor":
        del entries["receipts"][first["record_id"]]
    elif missing == "consumed_control":
        del entries["controls"][control["record_id"]]
    else:
        del entries["requests"][records[1][1]["record_id"]]
    with pytest.raises(SyncError):
        ancestry.legacy_consumption2(entries, _state_for(value, current))


def test_wire1_ancestor_cycle_is_rejected_even_with_closed_individual_wire_bodies(actual_history):
    value = actual_history
    first_intent, first = _admit(value)
    second_intent, second = _admit(value, previous=first)
    # Individual wire documents remain canonical. The ancestry itself is
    # intentionally impossible, and no producer or live facility is claimed.
    first_intent["request"].update(previous_admission_id=second["record_id"],
        expected_claim=deepcopy(second["result"]["claim"]))
    first["result"].update(previous_admission_id=second["record_id"],
        claim={**first["result"]["claim"], "lease_generation": second["result"]["claim"]["lease_generation"] + 1})
    first_intent["expected_before"].update(admission_ref=_ref(second), claim=deepcopy(second["result"]["claim"]))
    _rehash(first_intent, first)
    second_intent["expected_before"]["admission_ref"] = _ref(first)
    _closed_wire(first_intent, first)
    _closed_wire(second_intent, second)
    entries = _entries(value, [("requests", first_intent), ("receipts", first),
        ("requests", second_intent), ("receipts", second)])
    with pytest.raises(SyncError, match="Historical source admission1") as caught:
        ancestry.legacy_consumption2(entries, _state_for(value, first))
    assert caught.value.code == "SYNC_SOURCE_HISTORY1_ADMISSION_CYCLE"


@pytest.mark.parametrize("current_kind", ("wire1", "wire2", "no_admission", "wire2_no_legacy"))
def test_selector_only_factory_reads_controlled_current_baseline_without_writes(actual_history, current_kind):
    value = actual_history
    if current_kind == "no_admission":
        assert value.store.read()["source_admission"] is None
        selected = None
    elif current_kind == "wire2_no_legacy":
        intent, current = _pair2(value)
        _seed(value, [("requests", intent), ("receipts", current)])
        _install_existing_current(value, current)
        selected = None
    else:
        intent, selected = _admit(value)
        records = [("requests", intent), ("receipts", selected)]
        current = selected
        if current_kind == "wire2":
            current_intent, current = _pair2(value, selected)
            records.extend((("requests", current_intent), ("receipts", current)))
        _seed(value, records)
        _install_existing_current(value, current)
    before = _unchanged(value)
    with ancestry.capture_legacy_ancestry_locked(profile=value.profile, store=value.store) as captured:
        if selected is None:
            assert captured is None
        else:
            assert type(captured) is history.LegacyAdmissionHistoryCapture1
            assert captured.admission_ref() == _ref(selected)
            assert captured.result == selected["result"]
            assert captured.consumed_control_refs == ()
            for name in ("verify_for_commit", "verify_for_readmit_creation", "_advance_creation", "creation_basis"):
                assert not hasattr(captured, name)
            with pytest.raises(SyncError):
                copy_context().run(lambda: captured.result)
            assert captured.result == selected["result"]
    if selected is not None:
        for getter in (lambda: captured.result, captured.admission_ref, captured.verify_source):
            with pytest.raises(SyncError):
                getter()
    for extra in ({"admission_id": _id()}, {"state": value.store.read()},
                  {"consumed_controls": []}, {"owner": object()}):
        with pytest.raises(TypeError):
            with ancestry.capture_legacy_ancestry_locked(profile=value.profile, store=value.store, **extra):
                pass
    assert _unchanged(value) == before


def test_factory_rejects_wrong_existing_state_sha_without_mutating_files_or_sql(actual_history):
    value = actual_history
    intent, receipt = _admit(value)
    _seed(value, [("requests", intent), ("receipts", receipt)])
    state = _state_for(value, receipt)
    state["source_admission"]["record_sha256"] = "f" * 64
    # An explicitly corrupt existing selector, not an admitted State effect.
    with value.store.reference_operation():
        (value.store.data_dir / "sync-state.json").write_bytes(sync_state._validate(state))
    before = _unchanged(value)
    with pytest.raises(SyncError):
        with ancestry.capture_legacy_ancestry_locked(profile=value.profile, store=value.store):
            pass
    assert _unchanged(value) == before


def test_none_branch_rechecks_real_state_instead_of_leaving_a_reusable_absence_seal(actual_history):
    value = actual_history
    assert value.store.read()["source_admission"] is None
    with pytest.raises(SyncError):
        with ancestry.capture_legacy_ancestry_locked(profile=value.profile, store=value.store) as captured:
            assert captured is None
            status = deepcopy(value.store.read()["status"])
            status["error_code"] = "ancestry_probe"
            value.store.update({"status": status})
    assert value.store.read()["status"]["error_code"] == "ancestry_probe"
