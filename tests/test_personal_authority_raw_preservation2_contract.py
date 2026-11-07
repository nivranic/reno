"""Independent synthetic Q1 history DAGs, never native claims or A/B/C owners.

Expected JSON, hashes, inventories and quotas are assembled without product
normalizers.  Payload and opaque bytes stay outside the codec fact-byte map.
"""
from copy import deepcopy
import builtins
import hashlib
import json
import os
import sqlite3

import pytest

from reno import personal_authority_raw_preservation2_contract as contract
from tests.test_personal_authority_preservation_contract import (
    LIMITS as CAPSULE_LIMITS, OP, PIN, ROOT, directory, file_identity, synthetic_capsule,
)
from tests.test_personal_authority_source_projection import expected_source


ERROR = contract.AuthorityRawPreservation2ContractError
RUNS = {1: "33333333-3333-4333-8333-333333333333", 2: "55555555-5555-4555-8555-555555555555"}
NS = ".reno-authority-raw-preservation-v2"
CLAIM = "resume-slot-02.claim"
LATE_NAMES = ("source-verified.json", "manifest.json", "receipt.json")
Q1 = {"profile": "q1", "max_attempts": 2, "max_read_bytes": 512 * 1024**3,
    "max_write_bytes": 128 * 1024**3, "source_max_read_bytes": 384 * 1024**3,
    "active_ms": 120000, "slot_read_bytes": 256 * 1024**3,
    "slot_write_bytes": 64 * 1024**3, "slot_source_read_bytes": 192 * 1024**3,
    "slot_active_ms": 60000, "max_request_bytes": 1048576}


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def h0(kind):
    return {"format_version": 2, "kind": "authority_raw_preservation_" + kind, "operation_id": OP}


def hr(kind, slot):
    return {**h0(kind), "slot": slot, "run_id": RUNS[slot]}


def run_path(slot, tail):
    return f"runs/{slot:02d}/" + tail


def slot_limits():
    return {"capsule_limits": dict(CAPSULE_LIMITS), "source_max_read_bytes": 192 * 1024**3,
        "max_read_bytes": 256 * 1024**3, "max_write_bytes": 64 * 1024**3,
        "deadline_ms": 60000, "node_json_bytes": 4096, "chunk_bytes": 131072,
        "max_control_files": 300032, "max_target_entries": 400064, "max_target_depth": 8,
        "max_fact_bytes": 134217728, "max_failure_bytes": 16384, "max_attempts": 1}


def reservation(slot):
    return {"profile": "q1", "slot": slot, "read_bytes": 256 * 1024**3,
        "write_bytes": 64 * 1024**3, "source_read_bytes": 192 * 1024**3, "active_ms": 60000}


def counters():
    return {"read_bytes": 2 * 1024**2, "read_calls": 100, "seek_calls": 100,
        "confirmed_write_bytes": 1024**2, "write_calls": 100,
        "uncertain_read_calls": 0, "uncertain_write_bytes": 0, "elapsed_ns": 1000000}


def add_raw(bundle, path, raw, *, inode=None):
    assert path not in bundle["raw_files"]
    bundle["raw_files"][path] = raw
    inode = 50000 + len(bundle["raw_files"]) if inode is None else inode
    observed = file_identity(inode, len(raw), ctime="60")
    bundle["identities"][path] = (observed, {**observed, "ctime_ns": "61"})


def add_fact(bundle, path, value):
    raw = wire(value)
    bundle["documents"][path] = deepcopy(value)
    bundle["facts"][path] = raw
    add_raw(bundle, path, raw)


def add_run_directories(bundle, slot):
    root = f"runs/{slot:02d}"
    bundle["directories"][root] = directory(9100 + slot * 10)
    for offset, name in enumerate(("copy-attempts", "payload", "copy-records", "failures"), 1):
        bundle["directories"][root + "/" + name] = directory(9100 + slot * 10 + offset)


def inventory(bundle):
    dirs, raw_files = bundle["directories"], bundle["raw_files"]
    paths = set(dirs) | set(raw_files)
    rows = []
    for path in sorted(dirs, key=lambda key: key.encode("utf-8")):
        children = sorted((key.rpartition("/")[2] for key in paths
            if key and key.rpartition("/")[0] == path), key=lambda key: key.encode("utf-8"))
        rows.append({"relative_key": path, "identity": deepcopy(dirs[path]), "children": children})
    files = []
    for path in sorted(raw_files, key=lambda key: key.encode("utf-8")):
        path_identity, handle_identity = bundle["identities"][path]
        files.append({"relative_key": path, "parent_identity": deepcopy(dirs[path.rpartition("/")[0]]),
            "path_identity": deepcopy(path_identity), "handle_identity": deepcopy(handle_identity),
            "sha256": sha(raw_files[path]), "byte_size": len(raw_files[path])})
    return {"directories": rows, "files": files}


def prefix(*, populated=False):
    """Small original request -> run01 -> frozen source/selection prefix."""
    specs = [(ROOT + "/original.raw", "unclassified_authority_raw", b"original raw bytes")] if populated else []
    capsule, buffers, _copies, _artifacts = synthetic_capsule(specs)
    projected = expected_source(capsule)
    bundle = {"documents": {}, "facts": {}, "raw_files": {}, "identities": {},
        "directories": {"": directory(9001), "runs": directory(9003)},
        "original_capsule": deepcopy(capsule), "source_buffers": buffers}
    add_run_directories(bundle, 1)
    request = {**h0("request"), "scope": "raw-byte-preservation",
        "layout_profile": "namespace2-store2-extension3-enrollment1-r729", "layout_source_sha256": PIN,
        "creation_run_id": RUNS[1], "profile": deepcopy(capsule["source_context"]["profile"]),
        "target": {"namespace": NS, "namespace_identity": directory(9000),
            "operation_relative_key": OP, "operation_identity": directory(9001)},
        "budget_policy": dict(Q1), "slot_limits": slot_limits()}
    add_fact(bundle, "request.json", request)
    request_sha = sha(bundle["facts"]["request.json"])
    anchor = {"relative_key": "admission-q1-sha256-" + request_sha, "identity": directory(9002)}
    bundle["directories"][anchor["relative_key"]] = directory(9002)
    add_fact(bundle, run_path(1, "run-attempt.json"), {**hr("run_attempt", 1),
        "request_sha256": request_sha, "frozen_selection_sha256": None,
        "reservation": reservation(1), "anchor": anchor, "predecessor": None,
        "admission": {"kind": "operation_directory", "relative_key": "", "identity": directory(9001)}})
    metadata = {"format_version": 1, "kind": "personal_authority_raw_source_observation",
        "operation_id": OP, "capsule": deepcopy(capsule),
        "node_observation": {"state": "missing", "entry_id": None}, "source_projection": projected}
    add_fact(bundle, "source-metadata.json", metadata)
    add_fact(bundle, "source-projection.json", projected)
    add_fact(bundle, "selection.json", {**h0("selection"), "request_sha256": request_sha,
        "initial_run_attempt_sha256": sha(bundle["facts"][run_path(1, "run-attempt.json")]),
        "source_metadata_sha256": sha(bundle["facts"]["source-metadata.json"]),
        "source_projection_sha256": sha(bundle["facts"]["source-projection.json"]),
        "source_file_ids": [row["entry_id"] for row in capsule["files"]]})
    return bundle


def add_claim(bundle):
    add_raw(bundle, CLAIM, b"", inode=45000)


def add_second_reservation(bundle):
    if CLAIM not in bundle["raw_files"]:
        add_claim(bundle)
    if "runs/02" not in bundle["directories"]:
        add_run_directories(bundle, 2)
    path_identity, handle_identity = bundle["identities"][CLAIM]
    first_path = run_path(1, "run-attempt.json")
    add_fact(bundle, run_path(2, "run-attempt.json"), {**hr("run_attempt", 2),
        "request_sha256": sha(bundle["facts"]["request.json"]),
        "frozen_selection_sha256": sha(bundle["facts"]["selection.json"]),
        "reservation": reservation(2), "anchor": deepcopy(bundle["documents"][first_path]["anchor"]),
        "predecessor": {"slot": 1, "run_id": RUNS[1], "run_attempt_sha256": sha(bundle["facts"][first_path]),
            "run_attempt_byte_size": len(bundle["facts"][first_path])},
        "admission": {"kind": "resume_claim", "relative_key": CLAIM, "parent_identity": directory(9001),
            "path_identity": deepcopy(path_identity), "handle_identity": deepcopy(handle_identity),
            "sha256": sha(b""), "byte_size": 0}})


def add_copy(bundle, slot, entry, *, complete=True, payload_raw=None):
    copy_id = f"44444444-4444-4444-8444-{slot:012d}"
    parent_key = run_path(slot, "payload/" + copy_id)
    parent = directory(10000 + slot)
    bundle["directories"][parent_key] = parent
    payload = parent_key + "/" + entry["entry_id"] + ".raw"
    request_sha, selected_sha = (sha(bundle["facts"][path]) for path in ("request.json", "selection.json"))
    attempt_path = run_path(slot, "copy-attempts/" + copy_id + ".json")
    add_fact(bundle, attempt_path, {**hr("copy_attempt", slot), "request_sha256": request_sha,
        "selection_sha256": selected_sha, "run_attempt_sha256": sha(bundle["facts"][run_path(slot, "run-attempt.json")]),
        "copy_id": copy_id, "source_entry_id": entry["entry_id"], "target_relative_key": payload,
        "target_parent_identity": parent, "sha256": entry["sha256"], "byte_size": entry["byte_size"]})
    add_raw(bundle, payload, bundle["source_buffers"][entry["entry_id"]] if payload_raw is None else payload_raw,
        inode=30000 + slot)
    if not complete:
        return
    path_identity, handle_identity = bundle["identities"][payload]
    copied = {"source_entry_id": entry["entry_id"], "target_relative_key": payload,
        "target_parent_identity": deepcopy(parent), "copy_path_identity": deepcopy(path_identity),
        "copy_handle_identity": deepcopy(handle_identity), "sha256": entry["sha256"], "byte_size": entry["byte_size"]}
    add_fact(bundle, run_path(slot, "copy-records/" + entry["entry_id"] + ".json"), {**hr("copy_record", slot),
        "request_sha256": request_sha, "selection_sha256": selected_sha,
        "copy_attempt_sha256": sha(bundle["facts"][attempt_path]), "copy": copied})


def finish_generation(bundle, slot, *, minimum_write=False, counter_overrides=None):
    selected, copies = [], []
    for entry in bundle["original_capsule"]["files"]:
        origin = next(candidate for candidate in range(1, slot + 1)
            if run_path(candidate, "copy-records/" + entry["entry_id"] + ".json") in bundle["facts"])
        record_path = run_path(origin, "copy-records/" + entry["entry_id"] + ".json")
        selected.append({"source_entry_id": entry["entry_id"], "origin_slot": origin,
            "origin_run_id": RUNS[origin], "copy_record_relative_key": record_path,
            "copy_record_sha256": sha(bundle["facts"][record_path]), "copy_record_byte_size": len(bundle["facts"][record_path])})
        copies.append(deepcopy(bundle["documents"][record_path]["copy"]))
    capsule = deepcopy(bundle["original_capsule"])
    capsule["copies"] = copies
    capsule["coverage"]["declared_copies"] = "complete" if copies else "none"
    add_fact(bundle, run_path(slot, "capsule.json"), capsule)
    request_sha, selected_sha = (sha(bundle["facts"][path]) for path in ("request.json", "selection.json"))
    inventory_before = inventory(bundle)
    counts = counters()
    counts.update(counter_overrides or {})
    if minimum_write:
        counts["confirmed_write_bytes"] = sum(row["byte_size"] for row in inventory_before["files"]
            if slot == 1 or row["relative_key"].startswith("runs/02/") or row["relative_key"] == CLAIM)
    amount = sum(row["byte_size"] for row in bundle["original_capsule"]["files"])
    a = {"format_version": 1, "kind": "personal_authority_raw_census_closed", "operation_id": OP,
        "source_projection_sha256": sha(bundle["facts"]["source-projection.json"]),
        "source_metadata_sha256": sha(bundle["facts"]["source-metadata.json"]),
        "read_bytes": amount * 10, "read_calls": 20 if copies else 0, "seek_calls": 10 if copies else 0,
        "source_file_count": len(copies), "source_byte_size": amount}
    b = {**hr("target_closed", slot), "request_sha256": request_sha, "selection_sha256": selected_sha,
        "run_attempt_sha256": sha(bundle["facts"][run_path(slot, "run-attempt.json")]),
        "operation_identity": directory(9001), "reserved_slots": list(range(1, slot + 1)),
        **inventory_before, "selected_copies": deepcopy(selected), "counters": counts}
    verified_path = run_path(slot, "source-verified.json")
    add_fact(bundle, verified_path, {**hr("source_verified", slot), "request_sha256": request_sha,
        "selection_sha256": selected_sha, "run_attempt_sha256": b["run_attempt_sha256"],
        "capsule_sha256": sha(bundle["facts"][run_path(slot, "capsule.json")]),
        "source_closed": a, "target_closed": b, "selected_copies": selected})
    outcome = "copied" if copies else "empty_source"
    markers = [{"relative_key": CLAIM, "sha256": sha(b""), "byte_size": 0}] if CLAIM in bundle["raw_files"] else []
    selected_paths = {row["target_relative_key"] for row in copies}
    opaque_paths = set(bundle["raw_files"]) - set(bundle["facts"]) - selected_paths - {CLAIM}
    manifest_path = run_path(slot, "manifest.json")
    add_fact(bundle, manifest_path, {**hr("manifest", slot), "scope": "raw-byte-preservation",
        "request_sha256": request_sha, "selection_sha256": selected_sha,
        "source_verified_sha256": sha(bundle["facts"][verified_path]), "outcome": outcome,
        "facts": [{"relative_key": path, "sha256": sha(raw), "byte_size": len(raw)}
            for path, raw in sorted(bundle["facts"].items(), key=lambda item: item[0].encode("utf-8"))],
        "payloads": [{"source_entry_id": row["source_entry_id"], "relative_key": row["target_relative_key"],
            "sha256": row["sha256"], "byte_size": row["byte_size"]} for row in copies], "markers": markers,
        "retained": [{"relative_key": path, "sha256": sha(bundle["raw_files"][path]),
            "byte_size": len(bundle["raw_files"][path])} for path in sorted(opaque_paths)]})
    add_fact(bundle, run_path(slot, "receipt.json"), {**hr("receipt", slot), "scope": "raw-byte-preservation",
        "request_sha256": request_sha, "source_verified_sha256": sha(bundle["facts"][verified_path]),
        "manifest_sha256": sha(bundle["facts"][manifest_path]), "outcome": outcome,
        "publication": "published_observed"})


def package(*, generation=1, populated=False, minimum_write=False, counter_overrides=None):
    bundle = prefix(populated=populated)
    for entry in bundle["original_capsule"]["files"]:
        add_copy(bundle, 1, entry)
    finish_generation(bundle, 1, minimum_write=minimum_write,
        counter_overrides=counter_overrides if generation == 1 else None)
    if generation == 2:
        add_second_reservation(bundle)
        finish_generation(bundle, 2, minimum_write=minimum_write, counter_overrides=counter_overrides)
    return bundle


def resumed_partial(*, orphan_complete=False):
    bundle = prefix(populated=True)
    entry = bundle["original_capsule"]["files"][0]
    raw = bundle["source_buffers"][entry["entry_id"]] if orphan_complete else b"old"
    add_copy(bundle, 1, entry, complete=False, payload_raw=raw)
    add_raw(bundle, run_path(1, ".write-66666666-6666-4666-8666-666666666666.tmp"), b"partial old publication")
    add_raw(bundle, run_path(1, "failures/" + RUNS[1] + ".json"), b'{"truncated":')
    add_second_reservation(bundle)
    add_copy(bundle, 2, entry)
    finish_generation(bundle, 2, minimum_write=True)
    return bundle


def reject(function, *args, **options):
    with pytest.raises(ERROR) as raised:
        function(*args, **options)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith("AUTHORITY_RAW_PRESERVATION2_")
    return raised.value.code


def normalize(kind, value, facts):
    return getattr(contract, "normalize_" + kind)(value, fact_bytes=facts)


def specimen(bundle, kind):
    if kind in {"request", "selection"}:
        return deepcopy(bundle["documents"][kind + ".json"])
    if kind == "copy_attempt":
        path = next(path for path in bundle["documents"] if "/copy-attempts/" in path)
    elif kind == "copy_record":
        path = next(path for path in bundle["documents"] if "/copy-records/" in path)
    else:
        path = run_path(2, kind.replace("_", "-") + ".json")
    return deepcopy(bundle["documents"][path])


@pytest.mark.parametrize("populated", [False, True])
def test_small_original_prefix_is_complete_without_copy_or_completion_claim(populated):
    bundle = prefix(populated=populated)
    before = deepcopy(bundle)
    assert contract.cross_validate_prefix(fact_bytes=bundle["facts"]) is None
    assert contract.cross_validate_prefix(fact_bytes=bundle["facts"], inventory=inventory(bundle)) is None
    assert bundle == before
    assert not any(path.endswith("receipt.json") for path in bundle["facts"])


@pytest.mark.parametrize("generation", [1, 2])
@pytest.mark.parametrize("populated", [False, True])
def test_independent_small_complete_generation_closes_with_explicit_current_inventory(generation, populated):
    bundle = package(generation=generation, populated=populated)
    before = deepcopy(bundle)
    assert contract.cross_validate_package(fact_bytes=bundle["facts"], generation=generation,
        inventory=inventory(bundle)) is None
    assert bundle == before
    receipt = bundle["documents"][run_path(generation, "receipt.json")]
    assert receipt["outcome"] == ("copied" if populated else "empty_source")
    assert set(receipt).isdisjoint({"factory_returned", "publisher_closed", "ready", "owner"})


def test_complete_package_requires_explicit_inventory_instead_of_assuming_no_later_history():
    bundle = package()
    code = reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=1)
    assert code == "AUTHORITY_RAW_PRESERVATION2_INVENTORY_REQUIRED"


@pytest.mark.parametrize("populated", [False, True])
def test_generation_two_keeps_old_B_history_and_uses_only_current_new_write_lower_bound(populated):
    bundle = package(generation=2, populated=populated, minimum_write=True)
    verified = bundle["documents"][run_path(2, "source-verified.json")]
    b = verified["target_closed"]
    assert b["counters"]["confirmed_write_bytes"] < sum(row["byte_size"] for row in b["files"])
    assert {run_path(1, name) for name in LATE_NAMES} <= {row["relative_key"] for row in b["files"]}
    assert {run_path(2, name) for name in LATE_NAMES}.isdisjoint(row["relative_key"] for row in b["files"])
    assert contract.cross_validate_package(fact_bytes=bundle["facts"], generation=2,
        inventory=inventory(bundle)) is None
    if populated:
        assert {row["origin_slot"] for row in verified["selected_copies"]} == {1}
        assert not any(path.startswith("runs/02/copy-attempts/") for path in bundle["facts"])


@pytest.mark.parametrize("later", ["claim", "unpublished_run2", "reserved", "complete"])
def test_old_generation_verify_accepts_only_explicit_safe_later_prefix(later):
    bundle = package(populated=True)
    old_b = deepcopy(bundle["documents"][run_path(1, "source-verified.json")]["target_closed"])
    add_claim(bundle)
    if later == "unpublished_run2":
        add_run_directories(bundle, 2)
        add_raw(bundle, run_path(2, ".write-66666666-6666-4666-8666-666666666666.tmp"), b"unfinished raw publication")
    elif later in {"reserved", "complete"}:
        add_second_reservation(bundle)
        if later == "complete":
            finish_generation(bundle, 2)
    assert contract.cross_validate_package(fact_bytes=bundle["facts"], generation=1,
        inventory=inventory(bundle)) is None
    assert bundle["documents"][run_path(1, "source-verified.json")]["target_closed"] == old_b


@pytest.mark.parametrize("addition", ["root", "old_temp", "anchor_child", "run2_without_claim"])
def test_old_generation_verify_rejects_unexplained_current_inventory_changes(addition):
    bundle = package()
    if addition == "root":
        add_raw(bundle, "unexpected.raw", b"must not be ignored")
    elif addition == "old_temp":
        add_raw(bundle, run_path(1, ".write-66666666-6666-4666-8666-666666666666.tmp"), b"new after old receipt")
    elif addition == "anchor_child":
        anchor = bundle["documents"][run_path(1, "run-attempt.json")]["anchor"]["relative_key"]
        add_raw(bundle, anchor + "/unexpected.raw", b"anchor must remain empty")
    else:
        add_run_directories(bundle, 2)
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=1, inventory=inventory(bundle))


@pytest.mark.parametrize("field", ["request_sha256", "frozen_selection_sha256"])
def test_second_reservation_cannot_splice_a_different_original_request_or_selection(field):
    bundle = prefix()
    add_second_reservation(bundle)
    value = deepcopy(bundle["documents"][run_path(2, "run-attempt.json")])
    value[field] = "f" * 64
    reject(normalize, "run_attempt", value, bundle["facts"])


@pytest.mark.parametrize("field", ["slot", "run_id", "run_attempt_sha256", "run_attempt_byte_size"])
def test_second_reservation_predecessor_binds_the_original_first_attempt_raw(field):
    bundle = prefix()
    add_second_reservation(bundle)
    value = deepcopy(bundle["documents"][run_path(2, "run-attempt.json")])
    value["predecessor"][field] = {"slot": 2, "run_id": RUNS[2],
        "run_attempt_sha256": "f" * 64, "run_attempt_byte_size": 1}[field]
    reject(normalize, "run_attempt", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["drop", "duplicate", "unknown"])
def test_original_selection_is_the_full_frozen_source_set(mutation):
    bundle = prefix(populated=True)
    value = deepcopy(bundle["documents"]["selection.json"])
    if mutation == "drop":
        value["source_file_ids"] = []
    elif mutation == "duplicate":
        value["source_file_ids"] *= 2
    else:
        value["source_file_ids"] = ["f99999999"]
    reject(normalize, "selection", value, bundle["facts"])


@pytest.mark.parametrize("field", ["slot", "run_id", "copy_attempt_sha256"])
def test_copy_record_keeps_its_original_run_and_attempt_binding(field):
    bundle = package(generation=2, populated=True)
    path = next(path for path in bundle["facts"] if path.startswith("runs/01/copy-records/"))
    value = deepcopy(bundle["documents"][path])
    value[field] = {"slot": 2, "run_id": RUNS[2], "copy_attempt_sha256": "f" * 64}[field]
    reject(normalize, "copy_record", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["parent", "size", "sha", "hardlink"])
def test_claim_declaration_cannot_be_faked_as_another_file_or_nonempty_marker(mutation):
    bundle = prefix()
    add_second_reservation(bundle)
    value = deepcopy(bundle["documents"][run_path(2, "run-attempt.json")])
    claim = value["admission"]
    if mutation == "parent":
        claim["parent_identity"] = directory(9000)
    elif mutation == "size":
        claim["byte_size"] = 1
    elif mutation == "sha":
        claim["sha256"] = sha(b"not empty")
    else:
        claim["path_identity"]["nlink"] = claim["handle_identity"]["nlink"] = 2
    reject(normalize, "run_attempt", value, bundle["facts"])


def test_later_bad_managed_fact_cannot_be_ignored_when_verifying_older_good_receipt():
    bundle = package(generation=2)
    facts = dict(bundle["facts"])
    facts[run_path(2, "run-attempt.json")] = b'{"invalid":"formal fact"}'
    reject(contract.cross_validate_package, fact_bytes=facts, generation=1, inventory=inventory(bundle))


def test_payload_and_opaque_buffers_are_not_members_of_the_control_fact_map():
    bundle = package(populated=True)
    payload = next(path for path in bundle["raw_files"] if "/payload/" in path)
    assert payload not in bundle["facts"]
    facts = {**bundle["facts"], payload: bundle["raw_files"][payload]}
    reject(contract.cross_validate_package, fact_bytes=facts, generation=1, inventory=inventory(bundle))


@pytest.mark.parametrize("orphan_complete", [False, True])
def test_second_generation_keeps_partial_history_without_adopting_unrecorded_old_payload(orphan_complete):
    bundle = resumed_partial(orphan_complete=orphan_complete)
    assert run_path(1, "receipt.json") not in bundle["facts"]
    assert contract.cross_validate_prefix(fact_bytes=bundle["facts"], inventory=inventory(bundle)) is None
    assert contract.cross_validate_package(fact_bytes=bundle["facts"], generation=2,
        inventory=inventory(bundle)) is None
    manifest = bundle["documents"][run_path(2, "manifest.json")]
    chosen = bundle["documents"][run_path(2, "source-verified.json")]["selected_copies"]
    assert len(chosen) == 1 and chosen[0]["origin_slot"] == 2
    assert len(manifest["retained"]) == 3
    assert any("/payload/" in row["relative_key"] for row in manifest["retained"])
    assert all(row["relative_key"].startswith("runs/01/") for row in manifest["retained"])
    assert len(manifest["payloads"]) == 1 and manifest["payloads"][0]["relative_key"].startswith("runs/02/payload/")


def test_later_attempt_cannot_replace_or_duplicate_an_earlier_complete_copy():
    bundle = package(populated=True)
    add_second_reservation(bundle)
    add_copy(bundle, 2, bundle["original_capsule"]["files"][0])
    finish_generation(bundle, 2)
    chosen = bundle["documents"][run_path(2, "source-verified.json")]["selected_copies"]
    assert chosen[0]["origin_slot"] == 1
    assert len([path for path in bundle["facts"] if "/copy-records/" in path]) == 2
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=2, inventory=inventory(bundle))


@pytest.mark.parametrize("kind", ["request", "run_attempt", "selection", "copy_attempt", "copy_record",
    "source_verified", "manifest", "receipt"])
def test_public_fact_apis_preserve_independent_canonical_bytes_and_detach_results(kind):
    bundle = package(generation=2, populated=True)
    value = specimen(bundle, kind)
    before = deepcopy(value)
    assert normalize(kind, value, bundle["facts"]) == before
    assert contract.encode(kind, value, fact_bytes=bundle["facts"]) == wire(before)
    decoded = contract.decode(kind, wire(before), fact_bytes=bundle["facts"])
    assert type(decoded) is dict and decoded == before and decoded is not value
    decoded.clear()
    assert value == before


def test_complete_failure_fact_is_separate_from_opaque_truncated_retained_bytes():
    bundle = prefix()
    value = {**hr("failure", 1), "request_sha256": sha(bundle["facts"]["request.json"]),
        "selection_sha256": sha(bundle["facts"]["selection.json"]), "phase": "prefix",
        "code": "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED", "counters": counters(),
        "effects": [], "effects_complete": False}
    assert normalize("failure", value, bundle["facts"]) == value
    assert contract.encode("failure", value, fact_bytes=bundle["facts"]) == wire(value)
    assert contract.decode("failure", wire(value), fact_bytes=bundle["facts"]) == value
    value["code"] = "ARBITRARY_EXCEPTION_WITH_CONTENT"
    reject(normalize, "failure", value, bundle["facts"])


@pytest.mark.parametrize("kind,field,bad", [
    ("request", "format_version", 1), ("request", "creation_run_id", "00000000-0000-0000-0000-000000000000"),
    ("selection", "run_id", RUNS[1]), ("run_attempt", "slot", True),
    ("copy_attempt", "format_version", 1), ("source_verified", "publisher_closed", True),
    ("manifest", "scope", "database-restore"), ("receipt", "factory_returned", True)])
def test_v2_headers_fields_and_scope_cannot_be_relabelled_as_old_format_or_authority(kind, field, bad):
    bundle = package(generation=2, populated=True)
    value = specimen(bundle, kind)
    value[field] = bad
    reject(normalize, kind, value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["new_write_below_min", "read_below_source_plus_inventory",
    "source_read_cap", "uncertain_write", "refund_old_slot"])
def test_current_B_closes_per_slot_accounting_and_history_without_refund(mutation):
    bundle = package(generation=2, populated=True)
    value = specimen(bundle, "source_verified")
    a, b = value["source_closed"], value["target_closed"]
    if mutation == "new_write_below_min":
        b["counters"]["confirmed_write_bytes"] = sum(row["byte_size"] for row in b["files"]
            if row["relative_key"].startswith("runs/02/") or row["relative_key"] == CLAIM) - 1
    elif mutation == "read_below_source_plus_inventory":
        b["counters"]["read_bytes"] = a["read_bytes"] + sum(row["byte_size"] for row in b["files"]) - 1
    elif mutation == "source_read_cap":
        a["read_bytes"] = 192 * 1024**3 + 1
    elif mutation == "uncertain_write":
        b["counters"]["uncertain_write_bytes"] = 1
    else:
        b["reserved_slots"] = [2]
    reject(normalize, "source_verified", value, bundle["facts"])


@pytest.mark.parametrize("ledger", ["read", "write"])
@pytest.mark.parametrize("generation", [1, 2])
def test_current_B_alone_cannot_hide_late_publication_budget_overflow(ledger, generation):
    cap = (256 if ledger == "read" else 64) * 1024**3
    changed = {"read_bytes": cap - 1} if ledger == "read" else {"confirmed_write_bytes": cap - 16384 - 1}
    bundle = package(generation=2, counter_overrides=changed)
    value = specimen(bundle, "source_verified")
    assert normalize("source_verified", value, bundle["facts"]) == value
    b = value["target_closed"]
    late = sum(len(bundle["facts"][run_path(2, name)]) for name in LATE_NAMES)
    if ledger == "write":
        assert b["counters"]["confirmed_write_bytes"] + 16384 <= cap
        assert b["counters"]["confirmed_write_bytes"] + late + 16384 > cap
    else:
        assert b["counters"]["read_bytes"] <= cap
        assert b["counters"]["read_bytes"] + sum(row["byte_size"] for row in b["files"]) + late > cap
    reject(normalize, "receipt", specimen(bundle, "receipt"), bundle["facts"])
    reject(contract.decode, "receipt", bundle["facts"][run_path(2, "receipt.json")], fact_bytes=bundle["facts"])
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=generation, inventory=inventory(bundle))


@pytest.mark.parametrize("mutation", ["missing_fact", "missing_marker", "self_manifest", "receipt", "payload_duplicate"])
def test_manifest_exact_categories_cannot_omit_history_or_include_its_own_future_bytes(mutation):
    bundle = package(generation=2, populated=True)
    value = specimen(bundle, "manifest")
    if mutation == "missing_fact":
        value["facts"] = [row for row in value["facts"] if row["relative_key"] != run_path(1, "receipt.json")]
    elif mutation == "missing_marker":
        value["markers"] = []
    elif mutation in {"self_manifest", "receipt"}:
        path = run_path(2, "manifest.json" if mutation == "self_manifest" else "receipt.json")
        value["facts"].append({"relative_key": path, "sha256": "a" * 64, "byte_size": 1})
    else:
        value["retained"].append({key: value["payloads"][0][key] for key in ("relative_key", "sha256", "byte_size")})
    reject(normalize, "manifest", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["drop", "promote", "sha"])
def test_opaque_retained_rows_remain_complete_without_becoming_selected_copies(mutation):
    bundle = resumed_partial(orphan_complete=True)
    value = specimen(bundle, "manifest")
    if mutation == "drop":
        value["retained"].pop()
    elif mutation == "sha":
        value["retained"][0]["sha256"] = "a" * 64
    else:
        old_payload = next(row for row in value["retained"] if "/payload/" in row["relative_key"])
        value["payloads"] = [{"source_entry_id": bundle["original_capsule"]["files"][0]["entry_id"], **old_payload}]
    reject(normalize, "manifest", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["missing_file", "missing_empty_dir", "directory_identity", "file_identity",
    "children", "file_order", "invented_capability"])
def test_current_inventory_is_exact_and_preserves_original_physical_declarations(mutation):
    bundle = package(generation=2, populated=True)
    value = inventory(bundle)
    if mutation == "missing_file":
        value["files"].pop()
    elif mutation == "missing_empty_dir":
        value["directories"] = [row for row in value["directories"] if row["relative_key"] != "runs/01/failures"]
    elif mutation == "directory_identity":
        next(row for row in value["directories"] if row["relative_key"] == "runs/01")["identity"] = directory(888888)
    elif mutation == "file_identity":
        row = next(row for row in value["files"] if "/payload/" in row["relative_key"])
        row["path_identity"]["inode"] = row["handle_identity"]["inode"] = "888888"
    elif mutation == "children":
        value["directories"][0]["children"].append("not-present")
    elif mutation == "file_order":
        value["files"].reverse()
    else:
        value["trusted"] = True
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=2, inventory=value)


@pytest.mark.parametrize("generation", [True, False, 0, 3, 1.0, "1", None])
def test_package_generation_is_an_explicit_exact_valid_slot(generation):
    bundle = package(generation=2)
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=generation, inventory=inventory(bundle))


@pytest.mark.parametrize("alter", ["pretty", "newline", "bom", "duplicate"])
def test_decoder_requires_original_canonical_bytes_with_no_duplicate_fields(alter):
    bundle = prefix()
    value = bundle["documents"]["request.json"]
    raw = bundle["facts"]["request.json"]
    if alter == "pretty":
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    elif alter == "newline":
        raw += b"\n"
    elif alter == "bom":
        raw = b"\xef\xbb\xbf" + raw
    else:
        raw = b'{"format_version":2,' + raw[1:]
    reject(contract.decode, "request", raw)


@pytest.mark.parametrize("shape", ["raw_bytearray", "raw_memoryview", "non_dict", "unknown_path", "unsafe_path"])
def test_control_map_requires_exact_immutable_bytes_and_fixed_safe_names(shape):
    bundle = prefix()
    facts = dict(bundle["facts"])
    if shape == "raw_bytearray":
        facts["request.json"] = bytearray(facts["request.json"])
    elif shape == "raw_memoryview":
        facts["request.json"] = memoryview(facts["request.json"])
    elif shape == "non_dict":
        facts = list(facts.items())
    else:
        facts["unknown.json" if shape == "unknown_path" else "../request.json"] = b"{}"
    reject(contract.cross_validate_prefix, fact_bytes=facts)


def test_pure_generation_validation_never_opens_resources_or_runs_a_factory(monkeypatch):
    bundle = resumed_partial()
    observed = inventory(bundle)
    def forbidden(*_args, **_options):
        pytest.fail("Pure v2 contract attempted actual filesystem or SQL I/O")
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "open", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        assert contract.cross_validate_prefix(fact_bytes=bundle["facts"], inventory=observed) is None
        assert contract.cross_validate_package(fact_bytes=bundle["facts"], generation=2, inventory=observed) is None
        receipt = specimen(bundle, "receipt")
        assert normalize("receipt", receipt, bundle["facts"]) == receipt


def declared_size(observed, path, size):
    """Alter an opaque metadata declaration only; allocate no payload buffer."""
    row = next(row for row in observed["files"] if row["relative_key"] == path)
    row["byte_size"] = row["path_identity"]["byte_size"] = row["handle_identity"]["byte_size"] = size
    row["sha256"] = "a" * 64


@pytest.mark.parametrize("slot", [1, 2])
def test_prefix_aggregates_opaque_physical_bytes_against_each_occupied_slots_write_cap(slot):
    bundle = prefix()
    if slot == 2:
        add_second_reservation(bundle)
    paths = [run_path(slot, ".write-66666666-6666-4666-8666-66666666666" + str(index) + ".tmp")
        for index in (1, 2)]
    for path in paths:
        add_raw(bundle, path, b"shape only; actual opaque bytes are not supplied")
    observed = inventory(bundle)
    for path in paths:
        declared_size(observed, path, 33 * 1024**3)
    assert all(row["byte_size"] <= 64 * 1024**3 for row in observed["files"])
    assert sum(row["byte_size"] for row in observed["files"]
        if row["relative_key"].startswith(f"runs/{slot:02d}/")) > 64 * 1024**3
    assert not any(path.endswith("source-verified.json") for path in bundle["facts"])
    reject(contract.cross_validate_prefix, fact_bytes=bundle["facts"], inventory=observed)


def test_old_generation_verify_rejects_oversized_later_opaque_prefix_without_run2_B():
    bundle = package()
    add_claim(bundle)
    add_run_directories(bundle, 2)
    paths = [run_path(2, ".write-77777777-7777-4777-8777-77777777777" + str(index) + ".tmp")
        for index in (1, 2)]
    for path in paths:
        add_raw(bundle, path, b"declared history only")
    observed = inventory(bundle)
    for path in paths:
        declared_size(observed, path, 33 * 1024**3)
    assert run_path(2, "run-attempt.json") not in bundle["facts"]
    assert run_path(2, "source-verified.json") not in bundle["facts"]
    assert all(row["byte_size"] <= 64 * 1024**3 for row in observed["files"])
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"], generation=1, inventory=observed)


def test_unknown_old_slot_can_use_exact_physical_cap_without_reserving_failure_twice():
    bundle = prefix()
    temporary = run_path(1, ".write-88888888-8888-4888-8888-888888888888.tmp")
    failure = run_path(1, "failures/" + RUNS[1] + ".json")
    add_raw(bundle, temporary, b"opaque size declaration")
    add_raw(bundle, failure, b"opaque failure size declaration")
    observed = inventory(bundle)
    managed_size = sum(row["byte_size"] for row in observed["files"]
        if row["relative_key"] not in {temporary, failure})
    declared_size(observed, failure, 16384)
    declared_size(observed, temporary, 64 * 1024**3 - managed_size - 16384)
    assert sum(row["byte_size"] for row in observed["files"]) == 64 * 1024**3
    assert contract.cross_validate_prefix(fact_bytes=bundle["facts"], inventory=observed) is None
    excessive = deepcopy(observed)
    row = next(row for row in excessive["files"] if row["relative_key"] == temporary)
    declared_size(excessive, temporary, row["byte_size"] + 1)
    reject(contract.cross_validate_prefix, fact_bytes=bundle["facts"], inventory=excessive)
