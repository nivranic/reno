"""Independent raw2 relocation declarations; no native observation or restore.

Sealed old pure fixtures supply historical bytes. New inventories and both
maps are assembled here without the replica codec or any runtime factory.
"""
from copy import deepcopy
import builtins
import hashlib
import json
import os
import socket
import sqlite3
import time
import uuid

import pytest

from reno import personal_authority_replica_contract as replica
from tests import test_personal_authority_raw_preservation2_contract as old


ERROR = replica.AuthorityReplicaContractError
REPLICA_ID = "77777777-7777-4777-8777-777777777777"
SCOPE_ID = "88888888-8888-4888-8888-888888888888"
NODE_ID = "99999999-9999-4999-8999-999999999999"
TEMP2 = "runs/02/.write-66666666-6666-4666-8666-666666666666.tmp"
EMPTY2 = "runs/02/payload/77777777-7777-4777-8777-777777777777"
CONCLUSIONS = {
    "evidence_kind": "declaration", "historical_factory_return": "unknown",
    "unrecorded_history_completeness": "not_established", "native_observation": False,
    "data_restored": False, "runtime_write": False, "sender_authority": False,
}


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordered(values):
    return sorted(values, key=lambda value: value.encode("utf-8"))


def role(path):
    if path == old.CLAIM:
        return "marker"
    if "/payload/" in path:
        return "payload"
    if "/failures/" in path or path.rsplit("/", 1)[-1].startswith(".write-"):
        return "retained"
    return "control"


def history(kind="g1", *, populated=True):
    if kind == "resumed":
        return old.resumed_partial(orphan_complete=True), 2
    bundle = old.package(populated=populated)
    if kind == "g1":
        return bundle, 1
    if kind == "claim":
        old.add_claim(bundle)
        return bundle, 1
    if kind == "unpublished":
        old.add_claim(bundle)
        old.add_run_directories(bundle, 2)
        old.add_raw(bundle, TEMP2, b"opaque unpublished reservation\x00\xff")
        return bundle, 1
    old.add_second_reservation(bundle)
    old.add_raw(bundle, TEMP2, b"opaque retained current-slot publication")
    bundle["directories"][EMPTY2] = old.directory(88888)
    if kind == "reserved":
        old.add_raw(bundle, "runs/02/failures/" + old.RUNS[2] + ".json", b"\xff{truncated")
        return bundle, 1
    old.finish_generation(bundle, 2)
    if kind in {"partial_B", "partial_manifest"}:
        names = ("receipt.json",) if kind == "partial_manifest" else ("manifest.json", "receipt.json")
        for name in names:
            key = "runs/02/" + name
            for field in ("facts", "documents", "raw_files", "identities"):
                bundle[field].pop(key)
        old.add_raw(bundle, "runs/02/.write-55555555-5555-4555-8555-555555555555.tmp", b"later unpublished bytes")
        return bundle, 1
    assert kind in {"g2", "g1_with_g2"}
    return bundle, 2 if kind == "g2" else 1


def two_source_history():
    """Two real declaration entries: run01 completes one, run02 the other."""
    bundle = old.prefix(populated=True)
    capsule, buffers, _copies, _artifacts = old.synthetic_capsule([
        (old.ROOT + "/first.raw", "unclassified_authority_raw", b"first source"),
        (old.ROOT + "/second.raw", "unclassified_authority_raw", b"second source"),
    ])
    metadata = deepcopy(bundle["documents"]["source-metadata.json"])
    metadata["capsule"] = deepcopy(capsule)
    metadata["source_projection"] = old.expected_source(capsule)
    selection = deepcopy(bundle["documents"]["selection.json"])
    for key in ("source-metadata.json", "source-projection.json", "selection.json"):
        for field in ("facts", "documents", "raw_files", "identities"):
            bundle[field].pop(key)
    bundle["original_capsule"], bundle["source_buffers"] = deepcopy(capsule), buffers
    old.add_fact(bundle, "source-metadata.json", metadata)
    old.add_fact(bundle, "source-projection.json", metadata["source_projection"])
    selection["source_metadata_sha256"] = sha(bundle["facts"]["source-metadata.json"])
    selection["source_projection_sha256"] = sha(bundle["facts"]["source-projection.json"])
    selection["source_file_ids"] = [row["entry_id"] for row in capsule["files"]]
    old.add_fact(bundle, "selection.json", selection)
    old.add_copy(bundle, 1, capsule["files"][0])
    old.add_second_reservation(bundle)
    old.add_copy(bundle, 2, capsule["files"][1])
    old.finish_generation(bundle, 2)
    return bundle


def sync_declaration(value, *, children=False):
    """Recompute only new declaration projections, never historical facts."""
    current = value["current"]
    current["directories"].sort(key=lambda row: row["relative_key"].encode("utf-8"))
    current["files"].sort(key=lambda row: row["relative_key"].encode("utf-8"))
    if children:
        paths = {row["relative_key"] for rows in (current["directories"], current["files"]) for row in rows}
        for row in current["directories"]:
            row["children"] = ordered(path.rpartition("/")[2] for path in paths
                if path and path.rpartition("/")[0] == row["relative_key"])
    current["inventory_sha256"] = sha(wire({key: current[key] for key in ("directories", "files")}))
    package = value["package_map"]
    package["directories"] = [row["relative_key"] for row in current["directories"]]
    package["files"] = [{"relative_key": row["relative_key"], "role": role(row["relative_key"]),
        "sha256": row["sha256"], "byte_size": row["byte_size"]} for row in current["files"]]
    package["content_set_sha256"] = sha(wire({key: package[key] for key in ("mapping", "directories", "files")}))


def declaration(bundle, generation):
    """Current IDs are deliberately unrelated to every original fixture ID."""
    dirs = {path: {"device": "901", "inode": str(100000 + index)}
            for index, path in enumerate(ordered(bundle["directories"]))}
    rows = []
    for index, path in enumerate(ordered(bundle["raw_files"])):
        raw = bundle["raw_files"][path]
        identity = {"device": "901", "inode": str(200000 + index), "byte_size": len(raw),
            "mtime_ns": "400", "ctime_ns": "500", "mode": 0o100600, "nlink": 1}
        rows.append({"relative_key": path, "parent_identity": deepcopy(dirs[path.rpartition("/")[0]]),
            "path_identity": identity, "handle_identity": {**identity, "ctime_ns": "501"},
            "sha256": sha(raw), "byte_size": len(raw)})
    base = f"runs/{generation:02d}/"
    origin = {"format_version": 2, "family": old.NS, "operation_id": old.OP, "generation": generation}
    for name in ("request", "selection", "source-metadata", "source-projection",
                 "capsule", "source-verified", "manifest", "receipt"):
        key = ("" if name in {"request", "selection", "source-metadata", "source-projection"} else base) + name + ".json"
        origin[name.replace("-", "_") + "_sha256"] = sha(bundle["facts"].get(key, b""))
    origin["fact_set_sha256"] = sha(wire([[path, sha(bundle["facts"][path]), len(bundle["facts"][path])]
                                         for path in ordered(bundle["facts"])]))
    selected = bundle["documents"].get(base + "source-verified.json", {}).get("selected_copies", [])
    source_map = []
    for item in selected:
        key = item["copy_record_relative_key"]
        record = json.loads(bundle["facts"][key])
        copied = record["copy"]
        source_map.append({"source_entry_id": item["source_entry_id"], "origin_slot": record["slot"],
            "origin_run_id": record["run_id"], "copy_record_relative_key": key,
            "copy_record_sha256": sha(bundle["facts"][key]), "payload_relative_key": copied["target_relative_key"],
            "sha256": copied["sha256"], "byte_size": copied["byte_size"]})
    value = {"format_version": 1, "kind": "personal_authority_raw_replica",
        "scope": "raw2-replica-declaration-v1", "replica_id": REPLICA_ID, "origin": origin,
        "current": {"observation_scope_id": SCOPE_ID, "profile_sha256": "a" * 64, "node_id": NODE_ID,
            "directories": [{"relative_key": path, "identity": identity, "children": []} for path, identity in dirs.items()],
            "files": rows, "inventory_sha256": "0" * 64},
        "package_map": {"mapping": "same-relative-key", "directories": [], "files": [], "content_set_sha256": "0" * 64},
        "source_map": sorted(source_map, key=lambda row: row["source_entry_id"]), "conclusions": dict(CONCLUSIONS)}
    sync_declaration(value, children=True)
    return value


def specimen(kind="g1", *, populated=True):
    bundle, generation = history(kind, populated=populated)
    return bundle, declaration(bundle, generation)


def reject(function, *args, **kwargs):
    with pytest.raises(ERROR) as raised:
        function(*args, **kwargs)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith("AUTHORITY_REPLICA1_")
    return raised.value


def remove_current(value, key):
    for name in ("directories", "files"):
        value["current"][name] = [row for row in value["current"][name] if row["relative_key"] != key]
    sync_declaration(value, children=True)


def file_row(value, key):
    return next(row for row in value["current"]["files"] if row["relative_key"] == key)


def declared_size(value, key, size):
    row = file_row(value, key)
    row["byte_size"] = row["path_identity"]["byte_size"] = row["handle_identity"]["byte_size"] = size
    row["sha256"] = "b" * 64
    sync_declaration(value)


@pytest.mark.parametrize("kind", ["g1", "g2", "g1_with_g2", "claim", "unpublished", "reserved", "partial_B", "partial_manifest"])
@pytest.mark.parametrize("populated", [False, True])
def test_independent_relocated_complete_or_legal_later_prefix_has_full_package_and_exact_selected_map(kind, populated):
    bundle, value = specimen(kind, populated=populated)
    before, facts_before = deepcopy(value), dict(bundle["facts"])
    normalized = replica.normalize(value, fact_bytes=bundle["facts"])
    assert normalized == before and normalized is not value
    assert replica.compare_file_bytes(value, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None
    assert value == before and bundle["facts"] == facts_before
    assert set(value["package_map"]["directories"]) == set(bundle["directories"])
    assert {row["relative_key"] for row in value["package_map"]["files"]} == set(bundle["raw_files"])
    expected_ids = [row["entry_id"] for row in bundle["original_capsule"]["files"]]
    assert [row["source_entry_id"] for row in normalized["source_map"]] == expected_ids
    assert {row["origin_slot"] for row in normalized["source_map"]} == ({1} if populated else set())
    assert normalized["conclusions"] == CONCLUSIONS


def test_complete_orphan_bytes_stay_in_whole_package_without_becoming_selected_old_copy():
    bundle, value = specimen("resumed")
    old_payload = next(path for path in bundle["raw_files"] if path.startswith("runs/01/payload/"))
    current_payload = next(path for path in bundle["raw_files"] if path.startswith("runs/02/payload/"))
    assert bundle["raw_files"][old_payload] == bundle["raw_files"][current_payload]
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    assert len(value["source_map"]) == 1 and value["source_map"][0]["origin_slot"] == 2
    assert value["source_map"][0]["payload_relative_key"] == current_payload
    assert {row["relative_key"] for row in value["package_map"]["files"] if row["role"] == "payload"} == {old_payload, current_payload}
    retained = {row["relative_key"] for row in value["package_map"]["files"] if row["role"] == "retained"}
    assert retained == {"runs/01/.write-66666666-6666-4666-8666-666666666666.tmp", "runs/01/failures/" + old.RUNS[1] + ".json"}
    assert replica.compare_file_bytes(value, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None


def test_two_selected_sources_keep_mixed_origin_slots_and_strict_entry_order():
    bundle = two_source_history()
    value = declaration(bundle, 2)
    assert [row["source_entry_id"] for row in value["source_map"]] == ["f00000000", "f00000001"]
    assert [row["origin_slot"] for row in value["source_map"]] == [1, 2]
    assert [row["origin_run_id"] for row in value["source_map"]] == [old.RUNS[1], old.RUNS[2]]
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    assert replica.compare_file_bytes(value, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None
    value["source_map"].reverse()
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


def test_current_identity_numbers_can_equal_historical_numbers_without_claiming_same_host():
    bundle, value = specimen("g2")
    historical = old.inventory(bundle)
    value["current"].update(deepcopy(historical))
    sync_declaration(value, children=True)
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    assert value["conclusions"]["native_observation"] is False


def test_path_and_handle_ctime_may_differ_but_content_digest_ignores_only_current_identity():
    bundle, value = specimen()
    content_sha = value["package_map"]["content_set_sha256"]
    inventory_sha = value["current"]["inventory_sha256"]
    row = value["current"]["files"][0]
    row["handle_identity"]["ctime_ns"] = "777"
    sync_declaration(value)
    assert value["current"]["inventory_sha256"] != inventory_sha
    assert value["package_map"]["content_set_sha256"] == content_sha
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value


@pytest.mark.parametrize("kind", ["claim", "unpublished", "reserved", "partial_B", "partial_manifest"])
def test_requested_generation_two_never_falls_back_to_a_complete_first_receipt(kind):
    bundle, _ = specimen(kind)
    value = declaration(bundle, 2)
    error = reject(replica.normalize, value, fact_bytes=bundle["facts"])
    assert error.code == "AUTHORITY_REPLICA1_PACKAGE_INCOMPLETE"


@pytest.mark.parametrize("kind", ["g1_with_g2", "partial_B", "partial_manifest"])
@pytest.mark.parametrize("mutation", ["retained_missing", "empty_directory_missing", "retained_changed"])
def test_every_present_later_B_preserves_its_recorded_objects_even_when_requested_generation_is_one(kind, mutation):
    bundle, value = specimen(kind)
    assert value["origin"]["generation"] == 1
    assert TEMP2 not in {row["relative_key"] for row in bundle["documents"]["runs/01/source-verified.json"]["target_closed"]["files"]}
    if mutation == "retained_missing":
        remove_current(value, TEMP2)
    elif mutation == "empty_directory_missing":
        remove_current(value, EMPTY2)
    else:
        file_row(value, TEMP2)["sha256"] = "f" * 64
        sync_declaration(value)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


def test_unknown_unrecorded_later_residue_absence_is_not_invented_as_detected_history_loss():
    bundle, value = specimen("unpublished")
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    remove_current(value, TEMP2)
    bundle["raw_files"].pop(TEMP2)
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    assert replica.compare_file_bytes(value, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None
    assert value["conclusions"]["unrecorded_history_completeness"] == "not_established"


@pytest.mark.parametrize("field", ["request", "selection", "source_metadata", "source_projection", "capsule", "source_verified", "manifest", "receipt", "fact_set"])
def test_each_origin_digest_binds_actual_original_control_bytes(field):
    bundle, value = specimen("g2")
    value["origin"][field + "_sha256"] = "f" * 64
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("ledger", ["read", "write"])
def test_old_complete_receipt_cannot_hide_later_complete_receipts_late_budget_overflow(ledger):
    changed = {"read_bytes": 256 * 1024**3 - 1} if ledger == "read" else {"confirmed_write_bytes": 64 * 1024**3 - 16385}
    bundle = old.package(generation=2, populated=True, counter_overrides=changed)
    value = declaration(bundle, 1)
    error = reject(replica.normalize, value, fact_bytes=bundle["facts"])
    assert error.code == "AUTHORITY_REPLICA1_HISTORY_INVALID" and error.__cause__ is not None


@pytest.mark.parametrize("mutation", ["missing", "extra", "payload_in_facts", "failure_in_facts", "noncanonical", "old_identity_rewritten"])
def test_formal_fact_set_and_original_bytes_cannot_be_repaired_or_filtered(mutation):
    bundle, value = specimen("resumed")
    facts = dict(bundle["facts"])
    if mutation == "missing":
        facts.pop("source-projection.json")
    elif mutation == "extra":
        facts["unexpected.json"] = b"{}"
    elif mutation == "payload_in_facts":
        key = value["source_map"][0]["payload_relative_key"]
        facts[key] = bundle["raw_files"][key]
    elif mutation == "failure_in_facts":
        key = "runs/01/failures/" + old.RUNS[1] + ".json"
        facts[key] = bundle["raw_files"][key]
    elif mutation == "noncanonical":
        facts["request.json"] += b"\n"
    else:
        request = json.loads(facts["request.json"])
        request["target"]["operation_identity"]["inode"] = "7777777"
        facts["request.json"] = wire(request)
    value["origin"]["fact_set_sha256"] = sha(wire([[key, sha(facts[key]), len(facts[key])] for key in ordered(facts)]))
    reject(replica.normalize, value, fact_bytes=facts)


@pytest.mark.parametrize("mutation", ["dir_alias", "file_alias", "file_dir_alias", "wrong_parent", "handle_inode", "handle_mtime", "nlink", "nonregular", "integer_device", "zero_inode", "negative_ctime", "decimal_leading_zero"])
def test_current_native_identity_declarations_are_independent_but_internally_exact(mutation):
    bundle, value = specimen()
    dirs, files = value["current"]["directories"], value["current"]["files"]
    row = files[0]
    if mutation == "dir_alias":
        dirs[1]["identity"] = deepcopy(dirs[0]["identity"])
    elif mutation in {"file_alias", "file_dir_alias"}:
        identity = files[1]["path_identity"] if mutation == "file_alias" else dirs[0]["identity"]
        for field in ("path_identity", "handle_identity"):
            row[field].update({key: identity[key] for key in ("device", "inode")})
    elif mutation == "wrong_parent":
        row["parent_identity"]["inode"] = "88888888"
    else:
        field, content = {"handle_inode": ("inode", "88888888"), "handle_mtime": ("mtime_ns", "888"),
            "nlink": ("nlink", 2), "nonregular": ("mode", 0o040700), "integer_device": ("device", 901),
            "zero_inode": ("inode", "0"), "negative_ctime": ("ctime_ns", "-1"),
            "decimal_leading_zero": ("ctime_ns", "01")}[mutation]
        row["handle_identity"][field] = content
    sync_declaration(value)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["empty_dir_missing", "missing_child", "extra_child", "duplicate_file", "duplicate_dir", "missing_parent", "root_file", "parent_traversal", "case_collision", "anchor_nonempty", "old_late_addition"])
def test_current_tree_is_complete_and_closed_including_empty_directories(mutation):
    bundle, value = specimen()
    current = value["current"]
    if mutation in {"empty_dir_missing", "missing_parent"}:
        remove_current(value, "runs/01/failures" if mutation == "empty_dir_missing" else "runs/01")
    elif mutation in {"missing_child", "extra_child"}:
        children = current["directories"][0]["children"]
        children.pop() if mutation == "missing_child" else children.append("unexpected")
        sync_declaration(value)
    elif mutation.startswith("duplicate_"):
        collection = current["files"] if mutation == "duplicate_file" else current["directories"]
        collection.append(deepcopy(collection[0]))
        sync_declaration(value)
    else:
        added = deepcopy(current["files"][0])
        anchor = next(row["relative_key"] for row in current["directories"] if row["relative_key"].startswith("admission-"))
        added["relative_key"] = {"root_file": "", "parent_traversal": "../request.json", "case_collision": "Request.json",
            "anchor_nonempty": anchor + "/unexpected.raw", "old_late_addition": "runs/01/.write-55555555-5555-4555-8555-555555555555.tmp"}[mutation]
        added["path_identity"]["inode"] = added["handle_identity"]["inode"] = "9999999"
        current["files"].append(added)
        sync_declaration(value, children=True)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["missing", "nonzero", "wrong_empty_sha", "directory_marker", "run2_without_claim"])
def test_claim_only_and_run2_admission_require_the_actual_empty_marker_declaration(mutation):
    bundle, value = specimen("reserved" if mutation == "run2_without_claim" else "claim")
    if mutation in {"missing", "run2_without_claim"}:
        remove_current(value, old.CLAIM)
        if mutation == "missing":
            # Removing an unrecorded claim from a g1 declaration is not itself
            # detectable. Keep its frozen package selection to expose the loss.
            original = declaration(bundle, 1)
            value["package_map"] = original["package_map"]
    elif mutation == "nonzero":
        declared_size(value, old.CLAIM, 1)
    elif mutation == "wrong_empty_sha":
        file_row(value, old.CLAIM)["sha256"] = "f" * 64
        sync_declaration(value)
    else:
        remove_current(value, old.CLAIM)
        value["current"]["directories"].append({"relative_key": old.CLAIM,
            "identity": {"device": "901", "inode": "9999999"}, "children": []})
        sync_declaration(value, children=True)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["directory_missing", "file_missing", "extra_file", "role", "sha", "size", "mapping", "digest"])
def test_package_map_is_the_entire_current_package_projection(mutation):
    bundle, value = specimen("resumed")
    package = value["package_map"]
    if mutation == "directory_missing":
        package["directories"].pop()
    elif mutation == "file_missing":
        package["files"].pop()
    elif mutation == "extra_file":
        package["files"].append(deepcopy(package["files"][0]))
    elif mutation == "mapping":
        package["mapping"] = "flatten"
    elif mutation == "digest":
        package["content_set_sha256"] = "f" * 64
    else:
        package["files"][0][mutation if mutation != "sha" else "sha256"] = {
            "role": "retained", "sha": "f" * 64, "size": 999}[mutation]
        if mutation == "size":
            package["files"][0]["byte_size"] = package["files"][0].pop("size")
    if mutation != "digest":
        package["content_set_sha256"] = sha(wire({key: package[key] for key in ("mapping", "directories", "files")}))
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "entry", "slot", "run", "record_key", "record_sha", "payload", "sha", "size", "orphan"])
def test_selected_source_map_cannot_splice_origins_or_adopt_an_unrecorded_payload(mutation):
    bundle, value = specimen("resumed")
    mapped = value["source_map"]
    if mutation == "missing":
        mapped.clear()
    elif mutation == "duplicate":
        mapped.append(deepcopy(mapped[0]))
    else:
        row = mapped[0]
        field, changed = {"entry": ("source_entry_id", "f99999999"), "slot": ("origin_slot", 1),
            "run": ("origin_run_id", old.RUNS[1]), "record_key": ("copy_record_relative_key", "runs/01/copy-records/f00000000.json"),
            "record_sha": ("copy_record_sha256", "f" * 64), "payload": ("payload_relative_key", "request.json"),
            "sha": ("sha256", "f" * 64), "size": ("byte_size", row["byte_size"] + 1),
            "orphan": ("payload_relative_key", next(path for path in bundle["raw_files"] if path.startswith("runs/01/payload/")))}[mutation]
        row[field] = changed
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["payload_byte", "opaque_byte", "missing_retained", "extra", "wrong_size", "control_byte"])
def test_supplied_file_bytes_must_cover_every_control_marker_payload_and_retained_object(mutation):
    bundle, value = specimen("resumed")
    supplied = dict(bundle["raw_files"])
    retained = next(path for path in supplied if "/failures/" in path)
    if mutation == "missing_retained":
        supplied.pop(retained)
    elif mutation == "extra":
        supplied["unexpected.raw"] = b"not declared"
    else:
        key = value["source_map"][0]["payload_relative_key"] if mutation == "payload_byte" else retained
        if mutation == "control_byte":
            key = "request.json"
        supplied[key] = supplied[key] + b"x" if mutation == "wrong_size" else bytes([supplied[key][0] ^ 1]) + supplied[key][1:]
    reject(replica.compare_file_bytes, value, fact_bytes=bundle["facts"], file_bytes=supplied)


def test_unrecorded_slot2_opaque_declaration_can_reach_its_exact_physical_cap_without_refund():
    bundle, value = specimen("unpublished")
    declared_size(value, TEMP2, 64 * 1024**3)
    assert replica.normalize(value, fact_bytes=bundle["facts"]) == value
    declared_size(value, TEMP2, 64 * 1024**3 + 1)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


def test_two_individually_bounded_opaque_files_cannot_exceed_the_combined_slot_cap():
    bundle, _ = specimen("unpublished")
    second = "runs/02/.write-44444444-4444-4444-8444-444444444444.tmp"
    old.add_raw(bundle, second, b"another opaque declaration")
    value = declaration(bundle, 1)
    for key in (TEMP2, second):
        declared_size(value, key, 33 * 1024**3)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["failure_too_large", "failure_wrong_run", "orphan_without_attempt", "attempt_parent_missing"])
def test_opaque_retention_keeps_original_run_and_copy_attempt_dependencies(mutation):
    bundle, value = specimen("resumed")
    if mutation == "failure_too_large":
        declared_size(value, "runs/01/failures/" + old.RUNS[1] + ".json", 16385)
    elif mutation == "failure_wrong_run":
        row = next(row for row in value["current"]["files"] if "/failures/" in row["relative_key"])
        row["relative_key"] = "runs/01/failures/" + old.RUNS[2] + ".json"
        sync_declaration(value, children=True)
    elif mutation == "attempt_parent_missing":
        parent = next(path for path in bundle["directories"] if path.startswith("runs/01/payload/"))
        remove_current(value, parent)
    else:
        key = next(path for path in bundle["facts"] if path.startswith("runs/01/copy-attempts/"))
        bundle["facts"].pop(key)
        remove_current(value, key)
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("field", list(CONCLUSIONS) + ["owner", "ready", "restore"])
def test_declaration_cannot_claim_native_completion_historical_C_or_current_authority(field):
    bundle, value = specimen()
    value["conclusions"][field] = True
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("mutation", ["format", "scope", "kind", "family", "old_version", "zero_replica", "zero_scope", "zero_node", "bool_generation", "extra"])
def test_top_level_versions_and_context_labels_remain_exact_bounded_declarations(mutation):
    bundle, value = specimen()
    if mutation in {"format", "scope", "kind"}:
        value[{"format": "format_version", "scope": "scope", "kind": "kind"}[mutation]] = 2 if mutation == "format" else "not-this-protocol"
    elif mutation in {"family", "old_version", "bool_generation"}:
        value["origin"][{"family": "family", "old_version": "format_version", "bool_generation": "generation"}[mutation]] = {
            "family": ".reno-authority-raw-preservation-v1", "old_version": 1, "bool_generation": True}[mutation]
    elif mutation.startswith("zero_"):
        owner, key = (value, "replica_id") if mutation == "zero_replica" else (value["current"], "observation_scope_id" if mutation == "zero_scope" else "node_id")
        owner[key] = "00000000-0000-0000-0000-000000000000"
    else:
        value["authority"] = True
    reject(replica.normalize, value, fact_bytes=bundle["facts"])


@pytest.mark.parametrize("shape", ["str_value", "str_key", "int_value", "list_value", "dict_value", "fact_key", "fact_bytes", "buffer_bytes"])
def test_exact_builtin_rejection_never_invokes_user_defined_hooks(shape):
    bundle, value = specimen()
    calls = []
    def hook(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("a custom input hook executed")
    class Text(str):
        __hash__ = str.__hash__
        __eq__ = hook
        __str__ = hook
    class Number(int):
        __eq__ = hook
        __int__ = hook
    class List(list):
        __iter__ = __len__ = __getitem__ = hook
    class Dict(dict):
        __iter__ = __len__ = __getitem__ = items = keys = hook
    class Bytes(bytes):
        __eq__ = hook
    facts, supplied = dict(bundle["facts"]), dict(bundle["raw_files"])
    if shape == "str_value":
        value["kind"] = Text(value["kind"])
    elif shape == "str_key":
        content = value.pop("kind")
        value[Text("kind")] = content
    elif shape == "int_value":
        value["format_version"] = Number(1)
    elif shape == "list_value":
        value["source_map"] = List(value["source_map"])
    elif shape == "dict_value":
        value["current"] = Dict(value["current"])
    elif shape == "fact_key":
        content = facts.pop("request.json")
        facts[Text("request.json")] = content
    elif shape == "fact_bytes":
        facts["request.json"] = Bytes(facts["request.json"])
    else:
        supplied["request.json"] = Bytes(supplied["request.json"])
    if shape == "buffer_bytes":
        reject(replica.compare_file_bytes, value, fact_bytes=facts, file_bytes=supplied)
    else:
        reject(replica.normalize, value, fact_bytes=facts)
    assert calls == []


@pytest.mark.parametrize("alter", [lambda raw: b" " + raw, lambda raw: raw + b"\n",
    lambda raw: raw.replace(b'"format_version":1', b'"format_version":1,"format_version":1', 1),
    lambda raw: raw.replace(b'"format_version":1', b'"format_version":1.0', 1),
    lambda raw: raw.replace(b'"format_version":1', b'"format_version":NaN', 1),
    lambda raw: b"\xef\xbb\xbf" + raw, lambda raw: b"\xff"])
def test_decoder_requires_exact_canonical_utf8_without_duplicate_keys_or_numeric_coercion(alter):
    bundle, value = specimen()
    reject(replica.decode, alter(wire(value)), fact_bytes=bundle["facts"])


@pytest.mark.parametrize("limit", ["depth", "nodes", "raw_bytes"])
def test_hard_declaration_bounds_reject_before_unbounded_parsing_or_copying(limit):
    bundle, value = specimen()
    if limit == "raw_bytes":
        error = reject(replica.decode, b"x" * (128 * 1024**2 + 1), fact_bytes=bundle["facts"])
    elif limit == "nodes":
        value["current"]["directories"] = [None] * 4000001
        error = reject(replica.normalize, value, fact_bytes=bundle["facts"])
    else:
        child = []
        for _index in range(66):
            child = [child]
        value["current"]["directories"] = child
        error = reject(replica.normalize, value, fact_bytes=bundle["facts"])
    assert error.code == "AUTHORITY_REPLICA1_LIMIT"


def test_canonical_roundtrip_detaches_all_mutable_outputs_and_never_opens_runtime_resources(monkeypatch):
    bundle, value = specimen("resumed")
    raw, original = wire(value), deepcopy(value)
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("pure replica contract attempted a runtime effect")
    with monkeypatch.context() as patch:
        for module, name in ((builtins, "open"), (os, "stat"), (os, "lstat"), (sqlite3, "connect"),
                             (socket, "socket"), (time, "time"), (time, "monotonic"), (time, "monotonic_ns"), (uuid, "uuid4")):
            patch.setattr(module, name, forbidden)
        normalized = replica.normalize(value, fact_bytes=bundle["facts"])
        decoded = replica.decode(raw, fact_bytes=bundle["facts"])
        assert replica.encode(value, fact_bytes=bundle["facts"]) == raw
        assert replica.compare_file_bytes(value, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None
    assert calls == [] and normalized == decoded == original
    normalized["current"]["directories"][0]["children"].append("changed-result")
    normalized["source_map"][0]["sha256"] = "f" * 64
    decoded["package_map"]["files"][0]["sha256"] = "e" * 64
    assert value == original
