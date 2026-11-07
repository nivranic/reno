"""Independent synthetic raw-package DAGs: metadata closure is not actual I/O.

No payload buffers, OS resources, successful close capabilities, or grants are
supplied to the codec.  All expected bytes use the specified JSON wire rule.
"""
from copy import deepcopy
import builtins
import hashlib
import json
import os
import sqlite3

import pytest

from reno import personal_authority_raw_preservation_contract as contract
from tests.test_personal_authority_preservation_contract import (
    LIMITS as CAPSULE_LIMITS, OP, PIN, ROOT, directory, file_identity, synthetic_capsule,
)
from tests.test_personal_authority_source_projection import expected_source


RUN = "33333333-3333-4333-8333-333333333333"
KINDS = ("request", "run_attempt", "selection", "copy_attempt", "copy_record",
         "source_verified", "manifest", "receipt", "failure")
ERROR = contract.AuthorityRawPreservationContractError
NS = ".reno-authority-raw-preservation-v1"


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def limits():
    return {"capsule_limits": dict(CAPSULE_LIMITS), "source_max_read_bytes": 384 * 1024**3,
        "max_read_bytes": 512 * 1024**3, "max_write_bytes": 128 * 1024**3, "deadline_ms": 120000,
        "node_json_bytes": 4096, "chunk_bytes": 131072, "max_control_files": 300032,
        "max_target_entries": 400064, "max_target_depth": 8, "max_fact_bytes": 128 * 1024**2,
        "max_failure_bytes": 16384, "max_attempts": 1}


def header(kind):
    return {"format_version": 1, "kind": "authority_raw_preservation_" + kind,
            "operation_id": OP, "run_id": RUN}


def counters():
    return {"read_bytes": 2 * 1024**2, "read_calls": 100, "seek_calls": 100,
        "confirmed_write_bytes": 1024**2, "write_calls": 100, "uncertain_read_calls": 0,
        "uncertain_write_bytes": 0, "elapsed_ns": 1000000}


def package(*, populated=True, node_id=None, node_directory=None, zero_copy=False, limit_overrides=None,
            counter_overrides=None):
    """Build a complete declared DAG without any product normalization."""
    specs = [(ROOT + "/original.raw", "unclassified_authority_raw", b"original source"),
             (ROOT + "/zero.raw", "unclassified_authority_raw", b"")] if populated else []
    if node_id is not None:
        specs.append(("node-identity.json", "node_identity_raw", wire({"instance_id": node_id})))
    original, buffers, _copies, _artifacts = synthetic_capsule(specs,
        empty_dirs=() if node_directory is None else (node_directory,))
    original["operation_id"] = OP
    node_observation = {"state": "missing", "entry_id": None}
    if node_id is not None:
        node = next(row for row in original["files"] if row["relative_key"] == "node-identity.json")
        original["source_context"]["node"] = {"node_id": node_id, "sha256": node["sha256"],
            "path_identity": deepcopy(node["source_path_identity"]),
            "handle_identity": deepcopy(node["source_handle_identity"])}
        node_observation = {"state": "parsed", "entry_id": node["entry_id"]}
    projected = expected_source(original)
    documents, facts = {}, {}
    def add(path, value):
        documents[path] = deepcopy(value)
        facts[path] = wire(value)
    requested_limits = limits()
    requested_limits.update(limit_overrides or {})
    request = {**header("request"), "scope": "raw-byte-preservation",
        "layout_profile": "namespace2-store2-extension3-enrollment1-r729", "layout_source_sha256": PIN,
        "profile": deepcopy(original["source_context"]["profile"]),
        "target": {"namespace": NS, "namespace_identity": directory(9000),
            "operation_relative_key": OP, "operation_identity": directory(9001)}, "limits": requested_limits}
    add("request.json", request)
    request_sha = sha(facts["request.json"])
    add("attempts/" + RUN + ".json", {**header("run_attempt"), "request_sha256": request_sha,
        "operation_identity": directory(9001), "reserved_read_bytes": request["limits"]["max_read_bytes"],
        "reserved_write_bytes": request["limits"]["max_write_bytes"],
        "failure_reserve_bytes": request["limits"]["max_failure_bytes"], "deadline_ms": request["limits"]["deadline_ms"]})
    metadata = {"format_version": 1, "kind": "personal_authority_raw_source_observation",
        "operation_id": OP, "capsule": deepcopy(original), "node_observation": node_observation,
        "source_projection": deepcopy(projected)}
    add("source-metadata.json", metadata)
    add("source-projection.json", projected)
    add("selection.json", {**header("selection"), "request_sha256": request_sha,
        "source_metadata_sha256": sha(facts["source-metadata.json"]),
        "source_projection_sha256": sha(facts["source-projection.json"]),
        "source_file_ids": [row["entry_id"] for row in original["files"]]})
    selection_sha = sha(facts["selection.json"])
    copies, payloads, copy_dirs = [], {}, {}
    for index, entry in enumerate(original["files"]):
        copy_id = "44444444-4444-4444-8444-" + f"{index + 1:012d}"
        if zero_copy and index == 0:
            copy_id = "00000000-0000-0000-0000-000000000000"
        target = f"payload/{copy_id}/{entry['entry_id']}.raw"
        parent = directory(10000 + index)
        copy_dirs["payload/" + copy_id] = parent
        attempt = {**header("copy_attempt"), "request_sha256": request_sha, "selection_sha256": selection_sha,
            "copy_id": copy_id, "source_entry_id": entry["entry_id"], "target_relative_key": target,
            "target_parent_identity": parent, "sha256": entry["sha256"], "byte_size": entry["byte_size"]}
        attempt_path = f"copy-attempts/{copy_id}.json"
        add(attempt_path, attempt)
        observed = file_identity(50000 + index, entry["byte_size"], ctime="50")
        copied = {"source_entry_id": entry["entry_id"], "target_relative_key": target,
            "target_parent_identity": deepcopy(parent), "copy_path_identity": observed,
            "copy_handle_identity": {**observed, "ctime_ns": "51"},
            "sha256": entry["sha256"], "byte_size": entry["byte_size"]}
        copies.append(copied)
        payloads[target] = buffers[entry["entry_id"]]
        add(f"copy-records/{entry['entry_id']}.json", {**header("copy_record"), "request_sha256": request_sha,
            "selection_sha256": selection_sha, "copy_attempt_sha256": sha(facts[attempt_path]), "copy": copied})
    completed_capsule = deepcopy(original)
    completed_capsule["copies"] = deepcopy(copies)
    completed_capsule["coverage"]["declared_copies"] = "complete" if copies else "none"
    add("capsule.json", completed_capsule)

    dir_ids = {"": directory(9001), **{key: directory(9100 + index) for index, key in enumerate(
        ("attempts", "copy-attempts", "copy-records", "failures", "payload"))}, **copy_dirs}
    file_rows = []
    for index, path in enumerate(sorted([*facts, *payloads], key=lambda value: value.encode("utf-8"))):
        raw = facts[path] if path in facts else payloads[path]
        parent = path.rpartition("/")[0]
        copied = next((row for row in copies if row["target_relative_key"] == path), None)
        if copied is None:
            path_id = file_identity(20000 + index, len(raw), ctime="60")
            handle_id = {**path_id, "ctime_ns": "61"}
        else:
            path_id, handle_id = deepcopy(copied["copy_path_identity"]), deepcopy(copied["copy_handle_identity"])
        file_rows.append({"relative_key": path, "parent_identity": deepcopy(dir_ids[parent]),
            "path_identity": path_id, "handle_identity": handle_id, "sha256": sha(raw), "byte_size": len(raw)})
    directories = []
    all_keys = [*dir_ids, *(row["relative_key"] for row in file_rows)]
    for path in sorted(dir_ids, key=lambda value: value.encode("utf-8")):
        children = sorted([key.rpartition("/")[2] for key in all_keys
            if key and key.rpartition("/")[0] == path], key=lambda value: value.encode("utf-8"))
        directories.append({"relative_key": path, "identity": deepcopy(dir_ids[path]), "children": children})
    amount = sum(row["byte_size"] for row in original["files"])
    source_closed = {"format_version": 1, "kind": "personal_authority_raw_census_closed", "operation_id": OP,
        "source_projection_sha256": sha(facts["source-projection.json"]),
        "source_metadata_sha256": sha(facts["source-metadata.json"]),
        "read_bytes": amount * 10, "read_calls": 20 if populated else 0,
        "seek_calls": 10 if populated else 0, "source_file_count": len(original["files"]), "source_byte_size": amount}
    target_counters = counters()
    target_counters.update(counter_overrides or {})
    target_closed = {**header("target_closed"), "request_sha256": request_sha, "selection_sha256": selection_sha,
        "operation_identity": directory(9001), "directories": directories, "files": file_rows, "counters": target_counters}
    copy_refs = [{"source_entry_id": row["source_entry_id"],
        "sha256": sha(facts[f"copy-records/{row['source_entry_id']}.json"]),
        "byte_size": len(facts[f"copy-records/{row['source_entry_id']}.json"])} for row in copies]
    add("source-verified.json", {**header("source_verified"), "request_sha256": request_sha,
        "selection_sha256": selection_sha, "capsule_sha256": sha(facts["capsule.json"]),
        "source_closed": source_closed, "target_closed": target_closed, "copy_records": copy_refs})
    outcome = "copied" if populated else "empty_source"
    add("manifest.json", {**header("manifest"), "scope": "raw-byte-preservation", "request_sha256": request_sha,
        "selection_sha256": selection_sha, "source_verified_sha256": sha(facts["source-verified.json"]),
        "outcome": outcome, "facts": [{"relative_key": path, "sha256": sha(raw), "byte_size": len(raw)}
            for path, raw in sorted(facts.items(), key=lambda item: item[0].encode("utf-8"))],
        "payloads": [{"source_entry_id": row["source_entry_id"], "relative_key": row["target_relative_key"],
            "sha256": row["sha256"], "byte_size": row["byte_size"]} for row in copies]})
    add("receipt.json", {**header("receipt"), "scope": "raw-byte-preservation", "request_sha256": request_sha,
        "source_verified_sha256": sha(facts["source-verified.json"]), "manifest_sha256": sha(facts["manifest.json"]),
        "outcome": outcome, "publication": "published_observed"})
    return {"documents": documents, "facts": facts, "payloads": payloads}


def specimen(bundle, kind):
    paths = {"request": "request.json", "run_attempt": "attempts/" + RUN + ".json", "selection": "selection.json",
             "source_verified": "source-verified.json", "manifest": "manifest.json", "receipt": "receipt.json"}
    if kind == "failure":
        return {**header("failure"), "request_sha256": sha(bundle["facts"]["request.json"]),
            "selection_sha256": sha(bundle["facts"]["selection.json"]), "phase": "copy",
            "code": "AUTHORITY_RAW_PRESERVATION_WRITE_UNCERTAIN", "counters": {**counters(), "uncertain_write_bytes": 5},
            "effects": [{"relative_key": "payload", "kind": "directory", "identity": directory(9104),
                         "sha256": None, "byte_size": None}], "effects_complete": True}
    if kind in {"copy_attempt", "copy_record"}:
        prefix = "copy-attempts/" if kind == "copy_attempt" else "copy-records/"
        path = next(path for path in bundle["documents"] if path.startswith(prefix))
    else:
        path = paths[kind]
    return deepcopy(bundle["documents"][path])


def normalize(kind, value, facts=None, **options):
    return getattr(contract, "normalize_" + kind)(value, fact_bytes=facts, **options)


def reject(function, *args, **options):
    with pytest.raises(ERROR) as raised:
        function(*args, **options)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith("AUTHORITY_RAW_PRESERVATION1_")
    return raised.value.code


@pytest.mark.parametrize("populated", [False, True])
def test_independent_complete_empty_and_nonempty_dag_closes_without_payload_buffers(populated):
    bundle = package(populated=populated)
    before = deepcopy(bundle)
    assert not any(key.startswith("payload/") for key in bundle["facts"])
    assert contract.cross_validate_package(fact_bytes=bundle["facts"]) is None
    assert bundle == before
    assert bundle["documents"]["receipt.json"]["outcome"] == ("copied" if populated else "empty_source")
    assert bundle["documents"]["capsule.json"]["coverage"]["declared_copies"] == ("complete" if populated else "none")


@pytest.mark.parametrize("kind", KINDS)
def test_all_fact_apis_preserve_independent_canonical_bytes_and_detach(kind):
    bundle = package()
    value = specimen(bundle, kind)
    expected = deepcopy(value)
    assert normalize(kind, value, bundle["facts"]) == expected
    assert contract.encode(kind, value, fact_bytes=bundle["facts"]) == wire(expected)
    result = contract.decode(kind, wire(expected), fact_bytes=bundle["facts"])
    assert result == expected and result is not value
    result.clear()
    assert value == expected


def test_fixed_limits_convenience_has_independent_dicts_and_never_enlarges_ceilings():
    first = contract.default_limits()
    assert first == limits()
    first["capsule_limits"]["max_files"] = 1
    assert contract.default_limits() == limits()
    small = {**CAPSULE_LIMITS, "max_files": 2}
    result = contract.default_limits(capsule_limits=small)
    assert result["max_control_files"] == 38 and result["max_target_entries"] == 72
    small["max_files"] = 0
    assert result["capsule_limits"]["max_files"] == 2


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,invalid", [("format_version", True), ("format_version", 2),
    ("operation_id", "00000000-0000-0000-0000-000000000000"), ("run_id", "not-a-uuid"),
    ("kind", "future_resume_fact"), ("caller_ready", True)])
def test_fact_headers_and_extra_fields_are_closed(kind, field, invalid):
    bundle = package()
    value = specimen(bundle, kind)
    value[field] = invalid
    reject(normalize, kind, value, bundle["facts"])


@pytest.mark.parametrize("kind", KINDS)
def test_missing_header_field_is_not_invented(kind):
    bundle = package()
    value = specimen(bundle, kind)
    del value["run_id"]
    reject(normalize, kind, value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["namespace", "operation_path", "target_root_alias", "target_alias",
    "profile_path", "scope", "layout_pin", "max_attempts", "chunk_size", "limit_bool", "failure_overflow"])
def test_request_target_profile_and_budget_constraints_are_not_hints(mutation):
    value = specimen(package(), "request")
    if mutation == "namespace": value["target"]["namespace"] = ".arbitrary"
    elif mutation == "operation_path": value["target"]["operation_relative_key"] = "../" + OP
    elif mutation == "target_root_alias": value["target"]["namespace_identity"] = directory(10)
    elif mutation == "target_alias": value["target"]["operation_identity"] = deepcopy(value["target"]["namespace_identity"])
    elif mutation == "profile_path": value["profile"]["profile_paths"][1] = "/different/media"
    elif mutation == "scope": value["scope"] = "restore-authority"
    elif mutation == "layout_pin": value["layout_source_sha256"] = "0" * 64
    elif mutation == "max_attempts": value["limits"]["max_attempts"] = 2
    elif mutation == "chunk_size": value["limits"]["chunk_bytes"] = 262144
    elif mutation == "limit_bool": value["limits"]["max_read_bytes"] = True
    else: value["limits"]["max_failure_bytes"] = value["limits"]["max_write_bytes"] + 1
    reject(normalize, "request", value)


@pytest.mark.parametrize("field", ["reserved_read_bytes", "reserved_write_bytes", "failure_reserve_bytes", "deadline_ms"])
def test_run_attempt_cannot_reset_or_shrink_the_original_reservation(field):
    bundle = package()
    value = specimen(bundle, "run_attempt")
    value[field] -= 1
    reject(normalize, "run_attempt", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["subset", "duplicate", "reverse", "unknown"])
def test_selection_is_exact_source_file_set_not_a_caller_choice(mutation):
    bundle = package()
    value = specimen(bundle, "selection")
    rows = value["source_file_ids"]
    if mutation == "subset": rows.pop()
    elif mutation == "duplicate": rows.append(rows[0])
    elif mutation == "reverse": rows.reverse()
    else: rows[0] = "f99999999"
    reject(normalize, "selection", value, bundle["facts"])


@pytest.mark.parametrize("kind,field", [("run_attempt", "request_sha256"), ("selection", "source_metadata_sha256"),
    ("selection", "source_projection_sha256"), ("copy_attempt", "selection_sha256"),
    ("copy_record", "copy_attempt_sha256"), ("source_verified", "capsule_sha256"),
    ("manifest", "source_verified_sha256"), ("receipt", "manifest_sha256")])
def test_each_link_must_match_the_complete_actual_predecessor_raw(kind, field):
    bundle = package()
    value = specimen(bundle, kind)
    value[field] = "e" * 64
    reject(normalize, kind, value, bundle["facts"])


@pytest.mark.parametrize("kind", ["run_attempt", "selection", "copy_attempt", "copy_record", "source_verified", "manifest", "receipt"])
def test_required_predecessors_cannot_be_omitted(kind):
    bundle = package()
    reject(normalize, kind, specimen(bundle, kind), {})


@pytest.mark.parametrize("mutation", ["wrong_path", "wrong_entry", "wrong_sha", "wrong_size", "planned_inode"])
def test_copy_attempt_is_bound_to_original_selection_without_planned_file_identity(mutation):
    bundle = package()
    value = specimen(bundle, "copy_attempt")
    if mutation == "wrong_path": value["target_relative_key"] = "payload/elsewhere.raw"
    elif mutation == "wrong_entry": value["source_entry_id"] = "f99999999"
    elif mutation == "wrong_sha": value["sha256"] = "a" * 64
    elif mutation == "wrong_size": value["byte_size"] += 1
    else: value["copy_path_identity"] = file_identity(999, value["byte_size"])
    reject(normalize, "copy_attempt", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["source_alias", "parent_mismatch", "handle_mismatch", "same_sha_wrong_size", "reader_closed"])
def test_copy_record_requires_consistent_distinct_actual_identity_declarations(mutation):
    bundle = package()
    value = specimen(bundle, "copy_record")
    row = value["copy"]
    if mutation == "source_alias":
        source = bundle["documents"]["source-metadata.json"]["capsule"]["files"][0]
        for key in ("copy_path_identity", "copy_handle_identity"):
            row[key]["device"] = source["source_path_identity"]["device"]
            row[key]["inode"] = source["source_path_identity"]["inode"]
    elif mutation == "parent_mismatch": row["target_parent_identity"] = directory(99999)
    elif mutation == "handle_mismatch": row["copy_handle_identity"]["inode"] = "99999"
    elif mutation == "same_sha_wrong_size": row["byte_size"] += 1
    else: value["reader_closed"] = True
    reject(normalize, "copy_record", value, bundle["facts"])


@pytest.mark.parametrize("kind", ["copy_attempt", "copy_record"])
@pytest.mark.parametrize("alias", ["source_file", "source_directory", "profile_root", "target_namespace", "target_operation"])
def test_individual_copy_facts_reject_aliases_before_capsule_or_B_exists(kind, alias):
    bundle = package()
    value = specimen(bundle, kind)
    original = bundle["documents"]["source-metadata.json"]["capsule"]
    observed = {"source_file": {key: original["files"][0]["source_path_identity"][key] for key in ("device", "inode")},
        "source_directory": original["directories"][1]["source_identity"],
        "profile_root": original["source_context"]["profile"]["roots"]["media"]["identity"],
        "target_namespace": directory(9000), "target_operation": directory(9001)}[alias]
    if kind == "copy_attempt":
        value["target_parent_identity"] = deepcopy(observed)
    else:
        for field in ("copy_path_identity", "copy_handle_identity"):
            value["copy"][field].update(observed)
    # Only preceding facts are available; a later capsule/B cannot accidentally
    # reject the alias on behalf of the individual attempt/record validator.
    predecessors = {path: raw for path, raw in bundle["facts"].items() if path in {
        "request.json", "source-metadata.json", "source-projection.json", "selection.json"}
        or path.startswith("attempts/") or path.startswith("copy-attempts/")}
    reject(normalize, kind, value, predecessors)


@pytest.mark.parametrize("mutation", ["source_hash", "source_count", "source_bytes", "A_authority", "B_identity",
    "B_extra_file", "B_missing_file", "B_missing_empty_directory", "B_children", "B_duplicate_child",
    "B_alias", "B_parent", "B_order", "copy_refs", "uncertain_read", "uncertain_write",
    "below_source_read", "elapsed", "bool_counter", "failure_reserve_overspend"])
def test_source_verified_closes_exact_A_B_sets_and_success_counter_invariants(mutation):
    bundle = package()
    value = specimen(bundle, "source_verified")
    a, b = value["source_closed"], value["target_closed"]
    if mutation == "source_hash": a["source_projection_sha256"] = "f" * 64
    elif mutation == "source_count": a["source_file_count"] += 1
    elif mutation == "source_bytes": a["source_byte_size"] += 1
    elif mutation == "A_authority": a["ready"] = True
    elif mutation == "B_identity": b["operation_identity"] = directory(99999)
    elif mutation == "B_extra_file":
        row = deepcopy(b["files"][0]); row["relative_key"] = "unexpected.raw"; b["files"].append(row)
    elif mutation == "B_missing_file": b["files"].pop()
    elif mutation == "B_missing_empty_directory": b["directories"] = [row for row in b["directories"] if row["relative_key"] != "failures"]
    elif mutation == "B_children": b["directories"][0]["children"].pop()
    elif mutation == "B_duplicate_child": b["directories"][0]["children"].append(b["directories"][0]["children"][0])
    elif mutation == "B_alias": b["directories"][1]["identity"] = deepcopy(b["directories"][0]["identity"])
    elif mutation == "B_parent": b["files"][0]["parent_identity"] = directory(99999)
    elif mutation == "B_order": b["files"].reverse()
    elif mutation == "copy_refs": value["copy_records"].pop()
    elif mutation == "uncertain_read": b["counters"]["uncertain_read_calls"] = 1
    elif mutation == "uncertain_write": b["counters"]["uncertain_write_bytes"] = 1
    elif mutation == "below_source_read": b["counters"]["read_bytes"] = a["read_bytes"] - 1
    elif mutation == "elapsed": b["counters"]["elapsed_ns"] = 120000 * 1000000 + 1
    elif mutation == "bool_counter": b["counters"]["write_calls"] = True
    else: b["counters"]["confirmed_write_bytes"] = bundle["documents"]["request.json"]["limits"]["max_write_bytes"]
    reject(normalize, "source_verified", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["A_read_below_source", "A_zero_reads", "A_zero_calls", "A_zero_seeks",
    "B_write_below_inventory", "B_zero_write_calls"])
def test_successful_close_counter_declarations_respect_mathematical_minimums(mutation):
    bundle = package()
    value = specimen(bundle, "source_verified")
    a, b = value["source_closed"], value["target_closed"]
    if mutation == "A_read_below_source": a["read_bytes"] = a["source_byte_size"] - 1
    elif mutation == "A_zero_reads": a.update(read_bytes=0, read_calls=0, seek_calls=0)
    elif mutation == "A_zero_calls": a["read_calls"] = 0
    elif mutation == "A_zero_seeks": a["seek_calls"] = 0
    elif mutation == "B_write_below_inventory":
        b["counters"]["confirmed_write_bytes"] = sum(row["byte_size"] for row in b["files"]) - 1
    else: b["counters"]["write_calls"] = 0
    reject(normalize, "source_verified", value, bundle["facts"])


@pytest.mark.parametrize("changed", [{"max_control_files": 1}, {"max_write_bytes": 20000}])
def test_self_consistent_request_hashes_do_not_excuse_smaller_B_budgets(changed):
    bundle = package(limit_overrides=changed)
    value = specimen(bundle, "source_verified")
    assert value["request_sha256"] == sha(bundle["facts"]["request.json"])
    assert value["target_closed"]["request_sha256"] == value["request_sha256"]
    reject(normalize, "source_verified", value, bundle["facts"])


@pytest.mark.parametrize("ledger", ["read", "write"])
def test_full_package_budget_includes_post_B_facts_and_unspent_failure_reserve(ledger):
    cap, reserve = 2 * 1024**2, 16384
    selected_counters = {"confirmed_write_bytes": cap - reserve - 1} if ledger == "write" else {"read_bytes": cap - 1}
    bundle = package(limit_overrides={f"max_{ledger}_bytes": cap}, counter_overrides=selected_counters)
    facts = bundle["facts"]
    verified = specimen(bundle, "source_verified")
    target = verified["target_closed"]
    confirmed = target["counters"]["confirmed_write_bytes"]
    late_bytes = sum(len(facts[path]) for path in ("source-verified.json", "manifest.json", "receipt.json"))
    assert confirmed >= sum(row["byte_size"] for row in target["files"])
    assert sum(map(len, facts.values())) <= cap
    if ledger == "write":
        assert confirmed + reserve <= cap
        assert confirmed + late_bytes + reserve > cap
    else:
        assert verified["source_closed"]["read_bytes"] <= target["counters"]["read_bytes"] <= cap
        assert target["counters"]["read_bytes"] + late_bytes > cap
    assert verified["request_sha256"] == sha(facts["request.json"])
    assert bundle["documents"]["manifest.json"]["source_verified_sha256"] == sha(facts["source-verified.json"])
    assert bundle["documents"]["receipt.json"]["manifest_sha256"] == sha(facts["manifest.json"])
    assert normalize("source_verified", verified, facts) == verified
    reject(contract.cross_validate_package, fact_bytes=facts)


@pytest.mark.parametrize("mutation", ["missing_fact", "extra_fact", "self_manifest", "receipt", "payload_missing",
    "payload_subset", "payload_wrong_sha", "outcome"])
def test_manifest_is_the_required_fact_and_payload_set_without_self_reference(mutation):
    bundle = package()
    value = specimen(bundle, "manifest")
    if mutation == "missing_fact": value["facts"].pop()
    elif mutation in {"extra_fact", "self_manifest", "receipt"}:
        path = {"extra_fact": "unknown.json", "self_manifest": "manifest.json", "receipt": "receipt.json"}[mutation]
        value["facts"].append({"relative_key": path, "sha256": "a" * 64, "byte_size": 1})
    elif mutation == "payload_missing": value["payloads"] = []
    elif mutation == "payload_subset": value["payloads"].pop()
    elif mutation == "payload_wrong_sha": value["payloads"][0]["sha256"] = "f" * 64
    else: value["outcome"] = "empty_source"
    reject(normalize, "manifest", value, bundle["facts"])


@pytest.mark.parametrize("field", ["publisher_closed", "factory_returned", "ready", "write_enabled", "grant"])
def test_receipt_cannot_claim_factory_return_or_runtime_authority(field):
    bundle = package()
    value = specimen(bundle, "receipt")
    value[field] = True
    reject(normalize, "receipt", value, bundle["facts"])


def test_receipt_publication_is_observed_publication_only():
    bundle = package()
    value = specimen(bundle, "receipt")
    value["publication"] = "factory_returned"
    reject(normalize, "receipt", value, bundle["facts"])


@pytest.mark.parametrize("mutation", ["missing", "unknown", "second_run", "payload_buffer", "failure", "misnamed_copy"])
def test_complete_package_rejects_dangling_or_extra_fact_files(mutation):
    bundle = package()
    facts = dict(bundle["facts"])
    if mutation == "missing": del facts["source-projection.json"]
    elif mutation == "unknown": facts["future.json"] = b"{}"
    elif mutation == "second_run": facts["attempts/55555555-5555-4555-8555-555555555555.json"] = facts["attempts/" + RUN + ".json"]
    elif mutation == "payload_buffer": facts.update(bundle["payloads"])
    elif mutation == "failure": facts["failures/" + RUN + ".json"] = wire(specimen(bundle, "failure"))
    else:
        old = next(path for path in facts if path.startswith("copy-records/"))
        facts["copy-records/f99999999.json"] = facts.pop(old)
    reject(contract.cross_validate_package, fact_bytes=facts)


def test_spliced_other_operation_facts_cannot_be_accepted_by_individual_hashes():
    bundle = package()
    facts = dict(bundle["facts"])
    wrong = deepcopy(bundle["documents"]["source-metadata.json"])
    wrong["operation_id"] = "55555555-5555-4555-8555-555555555555"
    facts["source-metadata.json"] = wire(wrong)
    selection = specimen(bundle, "selection")
    selection["source_metadata_sha256"] = sha(facts["source-metadata.json"])
    reject(normalize, "selection", selection, facts)


def test_valid_parsed_node_is_bound_through_the_complete_independent_dag():
    bundle = package(node_id="22222222-2222-4222-8222-222222222222")
    assert contract.cross_validate_package(fact_bytes=bundle["facts"]) is None
    assert bundle["documents"]["source-metadata.json"]["node_observation"]["state"] == "parsed"


@pytest.mark.parametrize("field", ["operation", "run", "copy", "parsed_node"])
def test_zero_uuid_is_rejected_even_when_filenames_and_entire_hash_graph_agree(monkeypatch, field):
    zero = "00000000-0000-0000-0000-000000000000"
    if field in {"operation", "run"}:
        monkeypatch.setitem(globals(), "OP" if field == "operation" else "RUN", zero)
    bundle = package(zero_copy=field == "copy", node_id=zero if field == "parsed_node" else None)
    assert bundle["documents"]["receipt.json"]["manifest_sha256"] == sha(bundle["facts"]["manifest.json"])
    assert bundle["documents"]["selection.json"]["source_metadata_sha256"] == sha(bundle["facts"]["source-metadata.json"])
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("name", ["node-identity.json", "NODE-IDENTITY.JSON"])
def test_node_directory_cannot_be_relabelled_as_missing_in_a_self_consistent_package(name):
    bundle = package(node_directory=name)
    original = bundle["documents"]["source-metadata.json"]
    assert original["node_observation"] == {"state": "missing", "entry_id": None}
    assert any(row["relative_key"] == name for row in original["capsule"]["directories"])
    reject(contract.cross_validate_package, fact_bytes=bundle["facts"])


def test_failure_prefix_can_have_unknown_refs_and_uncertain_counts_without_becoming_success():
    value = {**header("failure"), "request_sha256": None, "selection_sha256": None,
        "phase": "opening_target", "code": "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED",
        "counters": {key: 0 for key in counters()}, "effects": [], "effects_complete": False}
    assert normalize("failure", value) == value
    assert contract.decode("failure", wire(value)) == value
    bundle = package()
    partial = specimen(bundle, "failure")
    assert normalize("failure", partial, bundle["facts"]) == partial
    assert partial["counters"]["uncertain_write_bytes"] == 5


@pytest.mark.parametrize("mutation", ["invented_code", "message", "unknown_identity", "directory_sha", "overflow_effects",
    "bool_complete", "negative_counter", "planned_file_sha"])
def test_failure_diagnostics_are_bounded_exact_facts_not_arbitrary_logs(mutation):
    bundle = package()
    value = specimen(bundle, "failure")
    if mutation == "invented_code": value["code"] = "ARBITRARY_INTERNAL_TEXT"
    elif mutation == "message": value["traceback"] = "private path or contents"
    elif mutation == "unknown_identity": value["effects"][0]["identity"] = {"device": "1"}
    elif mutation == "directory_sha": value["effects"][0]["sha256"] = "a" * 64
    elif mutation == "overflow_effects": value["effects"] *= 17
    elif mutation == "bool_complete": value["effects_complete"] = 1
    elif mutation == "negative_counter": value["counters"]["read_bytes"] = -1
    else: value["effects"][0]["planned_inode"] = "99"
    reject(normalize, "failure", value, bundle["facts"])


@pytest.mark.parametrize("alter", [lambda raw: b" " + raw, lambda raw: raw + b"\n",
    lambda raw: json.dumps(json.loads(raw), indent=2).encode(),
    lambda raw: raw.replace(b'"format_version":1', b'"format_version":1,"format_version":1', 1)])
def test_decoder_requires_canonical_original_bytes_without_duplicate_keys(alter):
    value = specimen(package(), "request")
    reject(contract.decode, "request", alter(wire(value)))


@pytest.mark.parametrize("raw", [b'[]', b'null', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
    b'{"x":"\\ud800"}', b'\xef\xbb\xbf{}', b'\xff'])
def test_invalid_json_never_becomes_a_fact(raw):
    reject(contract.decode, "request", raw)


@pytest.mark.parametrize("kind", ["dict_subclass", "bytes_subclass", "bytearray", "unsafe_path", "noncanonical_predecessor"])
def test_fact_map_is_exact_bounded_original_bytes_with_safe_fixed_names(kind):
    facts = package()["facts"]
    if kind == "dict_subclass": facts = type("SyntheticMap", (dict,), {})(facts)
    elif kind == "bytes_subclass": facts["request.json"] = type("SyntheticBytes", (bytes,), {})(facts["request.json"])
    elif kind == "bytearray": facts["request.json"] = bytearray(facts["request.json"])
    elif kind == "unsafe_path": facts["../request.json"] = facts.pop("request.json")
    else: facts["request.json"] = b" " + facts["request.json"]
    reject(contract.cross_validate_package, fact_bytes=facts)


def test_exact_single_fact_size_limit_and_one_byte_short_have_distinct_outcomes():
    value = specimen(package(), "request")
    raw = wire(value)
    assert normalize("request", value, max_fact_bytes=len(raw)) == value
    assert contract.encode("request", value, max_fact_bytes=len(raw)) == raw
    assert contract.decode("request", raw, max_fact_bytes=len(raw)) == value
    reject(normalize, "request", value, max_fact_bytes=len(raw) - 1)
    reject(contract.decode, "request", raw, max_fact_bytes=len(raw) - 1)


@pytest.mark.parametrize("invalid", [True, 0, -1, 128 * 1024**2 + 1, 1.5])
def test_caller_fact_byte_limit_cannot_enlarge_or_coerce_the_hard_ceiling(invalid):
    reject(normalize, "request", specimen(package(), "request"), max_fact_bytes=invalid)


def test_successful_metadata_closure_does_not_require_payload_buffers_or_mint_C():
    bundle = package()
    assert contract.cross_validate_package(fact_bytes=bundle["facts"]) is None
    receipt = contract.decode("receipt", bundle["facts"]["receipt.json"], fact_bytes=bundle["facts"])
    assert receipt["publication"] == "published_observed"
    assert set(receipt).isdisjoint({"factory_returned", "publisher_closed", "owner", "verified", "ready"})


def test_pure_fact_validators_do_not_open_paths_or_sql(monkeypatch):
    bundle = package()
    def forbidden(*_args, **_options):
        pytest.fail("Pure raw preservation contract attempted I/O")
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(os, "lstat", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        assert contract.cross_validate_package(fact_bytes=bundle["facts"]) is None
        assert normalize("receipt", specimen(bundle, "receipt"), bundle["facts"]) == specimen(bundle, "receipt")
