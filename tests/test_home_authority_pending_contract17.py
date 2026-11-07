"""Detached metadata codec tests; synthetic DTOs never stand for live pins."""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import home_authority_pending_contract17 as contract
from reno import storage_legacy_pending_state17 as component

OWNER = "a" * 64
CORE = "c" * 64
UUIDS = ["00000000-0000-4000-8000-" + str(index).zfill(12) for index in range(1, 7)]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def base_summary(*, present=False, references=None):
    references = [] if references is None else references
    return {"format_version": 1, "kind": "home_authority", "presence": "present" if present else "absent",
        "absence_sha256": None if present else digest({"format_version": 1, "kind": "home_authority", "presence": "absent"}),
        "owner_profile_sha256": OWNER, "identity": {"facility_id": UUIDS[0]} if present else None,
        "binding": dict(zip(("home_library_id", "home_node_id", "target_library_id", "target_node_id"), UUIDS[1:5])) if present else None,
        "contract_sha256": "b" * 64 if present else None,
        "head_ordinal": 2 * len(references), "head_sha256": "d" * 64 if present else None,
        "journal_sha256": "e" * 64 if present else None,
        "record_set_sha256": digest(references) if present else None,
        "reference_set_sha256": digest(references), "record_count": len(references), "journal_count": 2 * len(references)}


def component_summary(*, present=False, populated=False):
    refs = component_refs() if populated else []
    counts = {"file_count": 2, "capsule_count": 1, "marker_count": 1, "applied_count": 1,
        "total_bytes": 1000, "unknown_count": 0} if populated else {
        name: 0 for name in ("file_count", "capsule_count", "marker_count", "applied_count", "total_bytes", "unknown_count")}
    names = [UUIDS[5] + ".applied.json", UUIDS[5] + ".json"] if populated else []
    absence = {"format_version": 1, "kind": component.KIND, "directory": component.DIRECTORY,
        "owner_profile_sha256": OWNER, "presence": "absent"}
    return {"format_version": 1, "kind": component.KIND, "owner_profile_sha256": OWNER,
        "presence": "present" if present else "absent", "absence_sha256": None if present else digest(absence),
        "namespace_sha256": digest({"presence": "present" if present else "absent", "names": names}),
        "file_set_sha256": "f" * 64 if populated else digest([]),
        "state_set_sha256": "1" * 64 if populated else digest([]),
        "reference_set_sha256": digest(refs), "unknown_sha256": digest([]), **counts}


def component_refs():
    result = [{"format_version": 1, "record_kind": kind, "record_id": UUIDS[5],
        "record_sha256": sha * 64, "core_sha256": CORE}
        for kind, sha in (("recovery_intent", "8"), ("recovery_receipt", "9"))]
    return sorted(result, key=digest)


def component_projection(*, populated=False):
    return {"summary": component_summary(present=populated, populated=populated),
        "references": component_refs() if populated else [], "unknown": []}


def base_projection(*, populated=False):
    rows = [{"kind": kind, "record_id": UUIDS[5], "record_sha256": sha * 64,
        "core_sha256": CORE, "state": "committed", "refs": [], "publication_metadata": None}
        for kind, sha in (("requests", "8"), ("receipts", "9"))] if populated else []
    return {"summary": base_summary(present=populated, references=rows), "references": rows, "unknown": []}


@pytest.mark.parametrize("present", [False, True])
def test_home1_exact_fields_original_absence_hash_and_bytes_are_preserved(present):
    original = base_summary(present=present)
    raw = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert contract.normalize_summary(original, expected_version=1) == original
    assert set(original) == contract.SUMMARY1_FIELDS
    assert json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(",", ":")) == raw
    if not present:
        assert original["absence_sha256"] == digest({"format_version": 1, "kind": "home_authority", "presence": "absent"})
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.normalize_summary(original, expected_version=2)


def test_build2_binds_explicit_absent_component_with_new_combination_without_rewriting1():
    base, pending = base_summary(), component_summary()
    before = deepcopy(base), deepcopy(pending)
    result = contract.build_summary(base, legacy_pending_state=pending)
    assert set(result) == contract.SUMMARY2_FIELDS
    assert result["absence_sha256"] == digest({"format_version": 2, "kind": "home_authority", "presence": "absent",
        "legacy_pending_state": pending})
    assert result["absence_sha256"] != base["absence_sha256"]
    assert result["legacy_pending_state"] == pending
    assert (base, pending) == before
    for field in contract.SUMMARY1_FIELDS - {"format_version", "absence_sha256"}:
        assert result[field] == base[field]
    with pytest.raises(TypeError):
        contract.build_summary(base)


def test_actual_namespace_absence_and_empty_present_component_produce_distinct_home2_digests():
    absent = contract.build_summary(base_summary(), legacy_pending_state=component_summary())
    empty = contract.build_summary(base_summary(), legacy_pending_state=component_summary(present=True))
    assert absent["absence_sha256"] != empty["absence_sha256"]
    assert contract.normalize_summary(empty, expected_version=2) == empty
    forged = deepcopy(empty)
    forged["legacy_pending_state"] = deepcopy(absent["legacy_pending_state"])
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.normalize_summary(forged)


def test_public2_detaches_component_refs_without_mixing_authority_hashes_or_private_state():
    base, pending = base_projection(populated=True), component_projection(populated=True)
    before = deepcopy(base), deepcopy(pending)
    result = contract.build_projection(base, legacy_pending_state=pending)
    assert set(result) == contract.PROJECTION2_FIELDS
    assert result["references"] == base["references"]
    for field in ("record_count", "record_set_sha256", "reference_set_sha256", "head_ordinal", "journal_count"):
        assert result["summary"][field] == base["summary"][field]
    assert result["legacy_pending_state"]["references"] == pending["references"]
    assert set(result["legacy_pending_state"]) == contract.COMPONENT_PROJECTION_FIELDS
    assert (base, pending) == before
    result["legacy_pending_state"]["references"][0]["core_sha256"] = "2" * 64
    assert pending == before[1]
    assert type(result) is dict
    assert not any(hasattr(result, name) for name in ("writer", "verify_source", "verify_for_commit", "paid", "ticket", "admit"))


@pytest.mark.parametrize("field", ["file_count", "capsule_count", "marker_count", "applied_count", "total_bytes", "unknown_count", "format_version"])
def test_component_boolean_numeric_fields_are_not_integer_metadata(field):
    pending = component_summary()
    pending[field] = False if field != "format_version" else True
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.build_summary(base_summary(), legacy_pending_state=pending)


@pytest.mark.parametrize("damage", ["missing", "extra", "owner", "old_absence", "version_bool", "unknown", "head_bool"])
def test_home2_rejects_missing_extra_wrong_owner_old_digest_and_false_unknown(damage):
    value = contract.build_summary(base_summary(), legacy_pending_state=component_summary())
    if damage == "missing":
        value.pop("legacy_pending_state")
    elif damage == "extra":
        value["capsules"] = []
    elif damage == "owner":
        value["legacy_pending_state"]["owner_profile_sha256"] = "3" * 64
    elif damage == "old_absence":
        value["absence_sha256"] = base_summary()["absence_sha256"]
    elif damage == "version_bool":
        value["format_version"] = True
    elif damage == "head_bool":
        value["head_ordinal"] = False
    else:
        value["presence"] = "unknown"
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.normalize_summary(value, expected_version=2)


@pytest.mark.parametrize("damage", ["unknown_count", "unknown_sha", "empty_files_sha", "empty_states_sha",
    "empty_namespace_sha", "malformed_sha", "positive_absence", "orphan_marker", "false_applied", "zero_bytes", "fake_count"])
def test_component_unknown_counts_empty_hashes_and_marker_claims_cannot_build_complete2(damage):
    pending = component_summary()
    if damage == "unknown_count":
        pending["unknown_count"] = 1
        pending["unknown_sha256"] = digest([{"name": "opaque", "code": "INVALID"}])
    elif damage == "unknown_sha":
        pending["unknown_sha256"] = "4" * 64
    elif damage == "malformed_sha":
        pending["file_set_sha256"] = "not-a-sha"
    elif damage.startswith("empty_"):
        pending[{"empty_files_sha": "file_set_sha256", "empty_states_sha": "state_set_sha256",
            "empty_namespace_sha": "namespace_sha256"}[damage]] = "5" * 64
    else:
        pending = component_summary(present=True, populated=True)
        if damage == "positive_absence":
            pending["absence_sha256"] = "6" * 64
        elif damage == "orphan_marker":
            pending.update(capsule_count=0, marker_count=2, applied_count=0)
        elif damage == "false_applied":
            pending["applied_count"] = 0
        elif damage == "zero_bytes":
            pending["total_bytes"] = 0
        else:
            pending["file_count"] = 3
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.build_summary(base_summary(present=True), legacy_pending_state=pending)


@pytest.mark.parametrize("damage", ["missing_component", "old_projection", "ref_sha", "duplicate_ref", "reverse_refs",
    "reference_bool", "private_state", "raw_records", "scoped_unknown", "base_count", "base_sha", "base_unknown", "extra"])
def test_public2_rejects_aliases_private_images_forged_refsets_and_unknown_downgrade(damage):
    value = contract.build_projection(base_projection(populated=True), legacy_pending_state=component_projection(populated=True))
    pending = value["legacy_pending_state"]
    if damage in {"missing_component", "old_projection"}:
        value.pop("legacy_pending_state")
        if damage == "old_projection":
            value["summary"] = base_projection(populated=True)["summary"]
    elif damage == "ref_sha":
        pending["references"][0]["record_sha256"] = "7" * 64
    elif damage == "duplicate_ref":
        pending["references"].append(deepcopy(pending["references"][0]))
    elif damage == "reverse_refs":
        pending["references"].reverse()
    elif damage == "reference_bool":
        pending["references"][0]["format_version"] = True
    elif damage == "private_state":
        pending["state_images"] = [{"state_json": "{}"}]
    elif damage == "raw_records":
        pending["records"] = [{"raw": "private"}]
    elif damage == "scoped_unknown":
        pending["unknown"] = [{"name": "opaque", "code": "INVALID"}]
    elif damage == "base_count":
        value["summary"]["record_count"] += 1
    elif damage == "base_sha":
        value["summary"]["reference_set_sha256"] = "8" * 64
        value["summary"]["record_set_sha256"] = "8" * 64
    elif damage == "base_unknown":
        value["unknown"] = [{"scope": "home_authority", "code": "UNPROVEN"}]
    else:
        value["format_version"] = 2
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.normalize_projection(value, expected_version=2)


def test_build_never_labels_unknown_component_or_home_as_original_absence():
    base = base_projection()
    base["unknown"] = [{"scope": "home_authority", "code": "UNPROVEN"}]
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.build_projection(base, legacy_pending_state=component_projection())
    assert base["unknown"]
    pending = component_projection(populated=True)
    pending["summary"]["unknown_count"] = 1
    pending["unknown"] = [{"name": UUIDS[5] + ".json", "code": "CAPSULE_INVALID"}]
    pending["summary"]["unknown_sha256"] = digest(pending["unknown"])
    with pytest.raises(contract.HomeAuthorityPendingContractError):
        contract.build_projection(base_projection(populated=True), legacy_pending_state=pending)
    assert pending["summary"]["presence"] == "present" and pending["summary"]["unknown_count"] == 1
