"""Independent RP1 outer declarations assembled without the new codec.

Historical raw2 bytes come from sealed pure fixtures. New physical trees,
references, domain-separated commitments, scopes and event-cost declarations
are constructed here. None is a native observation, close owner or authority.
Only the root runner executes this file; authoring uses static checks only.
"""
from copy import deepcopy
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import socket
import sqlite3
import time
from types import MappingProxyType
import uuid

import pytest

from reno import personal_authority_replica_runtime_contract as contract
from tests import test_personal_authority_replica_contract as historical
from tests import test_personal_authority_replica_policy as policy_oracle


ERROR = contract.AuthorityReplicaRuntimeContractError
PREFIX = "AUTHORITY_REPLICA_RUNTIME1_"
SCOPE = "raw2-replica-runtime-v1"
DIRECTORY = ".reno-authority-raw-preservation-replicas-v1"
KINDS = ("request", "attempt", "source_selection", "copy_attempt", "copy_record",
         "replica", "source_closed", "target_closed", "manifest", "receipt", "failure")
SNAPSHOT_KEYS = (
    "read_bytes", "source_read_bytes", "read_calls", "source_read_calls", "seek_calls",
    "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes", "elapsed_ns",
    "current_resources", "peak_resources", "pending_resources", "uncertain_resource_acquires",
)
ADDITIVE = SNAPSHOT_KEYS[:10]
DECLARATION_FLAGS = {
    "native_observation": False, "historical_factory_return": "unknown",
    "data_restored": False, "runtime_write": False, "sender_authority": False,
}
STAGES = ("admitted", "attempt_declared", "selection_declared", "copy_partial",
          "replica_declared", "source_closed_declared", "target_closed_declared",
          "manifest_declared", "publication_unknown", "receipt_declared")


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def domain_hash(domain, value):
    return sha(b"reno-authority-replica-runtime-v1\0" + domain.encode("ascii") + b"\0" + wire(value))


def uid(number):
    return str(uuid.UUID(int=number))


def ordered(values):
    return sorted(values, key=lambda value: value.encode("utf-8"))


def directory(inode, device=7000):
    return {"device": str(device), "inode": str(inode)}


def identity(inode, size, *, device=7000, ctime="500"):
    return {**directory(inode, device), "byte_size": size, "mtime_ns": "400",
            "ctime_ns": ctime, "mode": 0o100600, "nlink": 1}


def file_row(key, parent, inode, raw, *, device=7000):
    observed = identity(inode, len(raw), device=device)
    return {"relative_key": key, "parent_identity": deepcopy(parent),
            "path_identity": observed, "handle_identity": {**observed, "ctime_ns": "501"},
            "sha256": sha(raw), "byte_size": len(raw)}


def ref(key, raw):
    return {"relative_key": key, "sha256": sha(raw), "byte_size": len(raw)}


def slot_key(slot, tail):
    return f"attempts/{slot:02d}/" + tail


def header(kind, ctx, slot=None):
    value = {"format_version": 1, "kind": "authority_raw_replica_runtime_" + kind,
             "scope": SCOPE, "operation_id": ctx["operation_id"]}
    if slot is not None:
        value.update(slot=slot, run_id=ctx["run_ids"][slot], request_sha256=sha(ctx["outer"]["request.json"]))
    return value


def profile(base, *, device=7000, first_inode=1000):
    paths = [base + "/data/reno.db", base + "/media", base + "/reports", base + "/checkpoints"]
    return {"profile_paths": paths, "profile_sha256": sha(wire(paths)),
            "roots": {name: directory(first_inode + index, device)
                      for index, name in enumerate(("data", "media", "reports", "checkpoints"))}}


def ancestor_chain(path, root_identity):
    parts = path.strip("/").split("/")
    paths = ["/"] + ["/" + "/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    rows = [{"absolute_path": current, "identity": directory(90000 + index, 901)}
            for index, current in enumerate(paths)]
    rows[-1]["identity"] = deepcopy(root_identity)
    return rows


def snapshot(**changes):
    value = {key: 0 for key in SNAPSHOT_KEYS}
    value.update(changes)
    return value


def ceiling_chunks(size):
    return (size + 131071) // 131072


def attributable(package_bytes, *, source=False, outer_bytes=0, node_bytes=0, write_bytes=0):
    result = {key: 0 for key in ADDITIVE}
    total = sum((package_bytes, outer_bytes, node_bytes))
    result.update(read_bytes=total, source_read_bytes=package_bytes if source else 0,
                  read_calls=ceiling_chunks(total) + 1,
                  source_read_calls=ceiling_chunks(package_bytes) + 1 if source else 0,
                  seek_calls=1, confirmed_write_bytes=write_bytes,
                  write_calls=ceiling_chunks(write_bytes), package_read_bytes=package_bytes,
                  outer_control_read_bytes=outer_bytes, observer_node_read_bytes=node_bytes,
                  other_native_control_read_bytes=0)
    return result


def scope_summary(scope, declaration):
    """Commit one illustrative resource pair; no event-body API is claimed."""
    root = declaration["current"]["directories"][0]["identity"]
    commitment = sha(wire(root))
    events = [
        {"sequence": 1, "resource_id": "r00000000", "kind": "directory_handle",
         "action": "acquired", "identity_sha256": commitment},
        {"sequence": 2, "resource_id": "r00000000", "kind": "directory_handle",
         "action": "normal_closed", "identity_sha256": commitment},
    ]
    return {"scope": scope, "scope_id": declaration["current"]["observation_scope_id"],
            "bound_inventory_sha256": declaration["current"]["inventory_sha256"],
            "resource_acquires": 1, "resource_normal_closes": 1, "peak_owned_resources": 1,
            "remaining_owned_resources": 0, "pending_resource_acquires": 0,
            "unknown_resource_acquires": 0, "unknown_closes": 0,
            "resource_events_sha256": domain_hash("resource-events", events), "normal_closed": True}


def inventory(ctx):
    keys = set(ctx["directories"]) | set(ctx["files"])
    return {
        "directories": [{"relative_key": key, "identity": deepcopy(ctx["directories"][key]),
                         "children": ordered(path.rpartition("/")[2] for path in keys
                                             if path and path.rpartition("/")[0] == key)}
                        for key in ordered(ctx["directories"])],
        "files": [deepcopy(ctx["files"][key]) for key in ordered(ctx["files"])],
    }


def add_directory(ctx, key, observed=None):
    if key not in ctx["directories"]:
        ctx["directories"][key] = deepcopy(observed or directory(10000 + len(ctx["directories"])))


def add_file(ctx, key, raw, *, row=None):
    ctx["file_ids"].setdefault(key, 800000 + len(ctx["file_ids"]))
    ctx["objects"][key] = raw
    ctx["files"][key] = deepcopy(row) if row is not None else file_row(
        key, ctx["directories"][posixpath.dirname(key)], ctx["file_ids"][key], raw)


def add_fact(ctx, kind, key, value):
    value = deepcopy(value)
    mutation = ctx["mutations"].get((kind, value.get("slot", 1)), ctx["mutations"].get(kind))
    if mutation is not None:
        mutation(value, ctx)
    raw = wire(value)
    ctx["documents"][key] = value
    ctx["outer"][key] = raw
    add_file(ctx, key, raw)
    return value


def actual_ref(ctx, key):
    return ref(key, ctx["outer"][key])


def base_fixture(*, old_kind="g1", source_kind="detached_raw2", mutations=None, padding=False,
                 populated=True, same_identity_values=False):
    if padding:
        old = historical.old
        bundle = old.prefix(populated=True)
        old.add_raw(bundle, "runs/01/.write-" + uid(901) + ".tmp", b"p" * 262144)
        for entry in bundle["original_capsule"]["files"]:
            old.add_copy(bundle, 1, entry)
        old.finish_generation(bundle, 1, minimum_write=True)
        source = historical.declaration(bundle, 1)
    else:
        bundle, source = historical.specimen(old_kind, populated=populated)
    observer = profile("/observed")
    node_raw = wire({"instance_id": uid(110)})
    node_file = file_row("node-identity.json", observer["roots"]["data"], 1010, node_raw)
    node = {"node_id": uid(110), **{key: deepcopy(node_file[key]) for key in (
        "sha256", "byte_size", "parent_identity", "path_identity", "handle_identity")}}
    source["replica_id"] = uid(100)
    source["current"].update(observation_scope_id=uid(201), profile_sha256=observer["profile_sha256"],
                              node_id=node["node_id"])
    source_profile = profile("/original", device=901, first_inode=6000) if source_kind == "profile_raw2" else None
    package_root = ("/original/data/.reno-authority-raw-preservation-v2/" + source["origin"]["operation_id"]
                    if source_profile else "/detached/raw2")
    source_root = source["current"]["directories"][0]["identity"]
    ancestors = ancestor_chain(package_root, source_root)
    if source_profile:
        next(row for row in ancestors if row["absolute_path"] == "/original/data")["identity"] = deepcopy(source_profile["roots"]["data"])
    location = {"source_kind": source_kind, "package_root": package_root,
                "generation": source["origin"]["generation"],
                "expected_request_sha256": source["origin"]["request_sha256"],
                "root_identity": deepcopy(source_root), "ancestors": ancestors,
                "profile": source_profile,
                "original_operation_id": source["origin"]["operation_id"] if source_profile else None}
    ctx = {"operation_id": uid(100), "run_ids": {1: uid(101), 2: uid(102)},
           "old": bundle, "source_template": source, "sources": {}, "targets": {},
           "node_raw": node_raw, "node": node, "outer": {}, "documents": {}, "raw2": {},
           "directories": {"": directory(1006)}, "files": {}, "file_ids": {}, "objects": {},
           "mutations": mutations or {}, "stages": {}, "scopes": {},
           "same_identity_values": same_identity_values}
    ctx["request_value"] = {**header("request", ctx), "creation_run_id": ctx["run_ids"][1],
        "policy": deepcopy(policy_oracle.RP1),
        "target": {"profile": observer, "node": deepcopy(node),
                   "namespace_identity": directory(1005), "operation_identity": directory(1006)},
        "source": location, "expected_content_set_sha256": source["package_map"]["content_set_sha256"]}
    bootstrap_directories(ctx, 1)
    return ctx


def bootstrap_directories(ctx, slot):
    add_directory(ctx, "attempts")
    add_directory(ctx, f"attempts/{slot:02d}")
    for name in ("copy-attempts", "copy-records", "failures"):
        add_directory(ctx, slot_key(slot, name))


def add_request(ctx, *, anchor=True):
    request = add_fact(ctx, "request", "request.json", ctx["request_value"])
    if anchor:
        add_directory(ctx, "admission-rp1-sha256-" + sha(ctx["outer"]["request.json"]))
    return request


def add_attempt(ctx, slot):
    bootstrap_directories(ctx, slot)
    if slot == 2 and "resume-attempt-02.claim" not in ctx["files"]:
        add_file(ctx, "resume-attempt-02.claim", b"")
    request = ctx["documents"]["request.json"]
    anchor_key = "admission-rp1-sha256-" + sha(ctx["outer"]["request.json"])
    occupied = list(range(1, slot + 1))
    predecessor_key = slot_key(1, "attempt.json")
    predecessor = ({"state": "none"} if slot == 1 else
                   {"state": "published", "attempt_ref": actual_ref(ctx, predecessor_key)}
                   if predecessor_key in ctx["outer"] else {"state": "not_published", "slot": 1})
    value = {**header("attempt", ctx, slot),
             "admission": {"method": "new_operation" if slot == 1 else "fresh_second_attempt",
                           "occupied_slots": occupied,
                           "allocation": {"profile": "replica-rp1", "occupied_slots": occupied,
                                          "reserved": {key: amount * slot for key, amount in policy_oracle.SLOT.items()},
                                          "max_resources": 1000000},
                           "resume_claim": None if slot == 1 else deepcopy(ctx["files"]["resume-attempt-02.claim"])},
             "anchor": {"relative_key": anchor_key, "parent_identity": deepcopy(request["target"]["operation_identity"]),
                        "identity": deepcopy(ctx["directories"][anchor_key]), "children": []},
             "reservation": {"profile": "replica-rp1", "slot": slot, **policy_oracle.SLOT},
             "predecessor": predecessor}
    ctx["stages"][slot] = "attempt_declared"
    return add_fact(ctx, "attempt", slot_key(slot, "attempt.json"), value)


def add_selection(ctx, slot):
    source = deepcopy(ctx["source_template"])
    source["current"]["observation_scope_id"] = uid(200 + slot)
    ctx["sources"][slot] = source
    entry_set = {"directories": [[f"d{index:08d}", row] for index, row in enumerate(source["current"]["directories"])],
                 "files": [[f"p{index:08d}", row] for index, row in enumerate(source["current"]["files"])]}
    value = {**header("source_selection", ctx, slot), "source_location": deepcopy(ctx["documents"]["request.json"]["source"]),
             "source_declaration": source, "source_declaration_sha256": sha(wire(source)),
             "raw_control_refs": [{key: row[key] for key in ("relative_key", "sha256", "byte_size")}
                                  for row in source["package_map"]["files"] if row["role"] == "control"],
             "package_entry_set_sha256": domain_hash("package-entry-set", entry_set)}
    ctx["stages"][slot] = "selection_declared"
    return add_fact(ctx, "source_selection", slot_key(slot, "source-selection.json"), value)


def relocated_target(ctx, slot):
    target = deepcopy(ctx["sources"][slot])
    target["current"]["observation_scope_id"] = uid(300 + slot)
    if ctx["same_identity_values"]:
        return target
    dirs = {}
    for index, row in enumerate(target["current"]["directories"]):
        row["identity"] = directory(300000 + index, 7100 + slot)
        dirs[row["relative_key"]] = row["identity"]
    for index, row in enumerate(target["current"]["files"]):
        row["parent_identity"] = deepcopy(dirs[posixpath.dirname(row["relative_key"])])
        row["path_identity"] = identity(400000 + index, row["byte_size"], device=7100 + slot)
        row["handle_identity"] = {**row["path_identity"], "ctime_ns": "501"}
    historical.sync_declaration(target)
    return target


def add_copies(ctx, slot, *, count=None, record=True, provide_raw=True):
    target = ctx["targets"].setdefault(slot, relocated_target(ctx, slot))
    package = slot_key(slot, "package")
    for row in target["current"]["directories"]:
        add_directory(ctx, package + ("/" + row["relative_key"] if row["relative_key"] else ""), row["identity"])
    source_rows = ctx["sources"][slot]["current"]["files"]
    target_rows = target["current"]["files"]
    limit = len(source_rows) if count is None else count
    for index, (source_row, target_row) in enumerate(zip(source_rows[:limit], target_rows[:limit])):
        key, entry_id, copy_id = source_row["relative_key"], f"p{index:08d}", uid(10000 * slot + index + 1)
        attempt_key = slot_key(slot, "copy-attempts/" + copy_id + ".json")
        selection_ref = actual_ref(ctx, slot_key(slot, "source-selection.json"))
        value = {**header("copy_attempt", ctx, slot), "selection_ref": selection_ref,
                 "package_entry_id": entry_id, "copy_id": copy_id, "relative_key": key,
                 "source_file_sha256": source_row["sha256"], "source_byte_size": source_row["byte_size"],
                 "target_parent_identity": deepcopy(target_row["parent_identity"])}
        add_fact(ctx, "copy_attempt", attempt_key, value)
        physical = {**deepcopy(target_row), "relative_key": package + "/" + key}
        add_file(ctx, physical["relative_key"], ctx["old"]["raw_files"][key], row=physical)
        if record:
            value = {**header("copy_record", ctx, slot), "copy_attempt_ref": actual_ref(ctx, attempt_key),
                     "selection_ref": selection_ref, "package_entry_id": entry_id,
                     **{field: deepcopy(target_row[field]) for field in ("relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size")}}
            add_fact(ctx, "copy_record", slot_key(slot, "copy-records/" + entry_id + ".json"), value)
        if provide_raw and key in ctx["old"]["facts"]:
            ctx["raw2"].setdefault(f"{slot:02d}", {})[key] = ctx["old"]["facts"][key]
    ctx["stages"][slot] = "copy_partial"


def add_replica(ctx, slot):
    ctx["stages"][slot] = "replica_declared"
    return add_fact(ctx, "replica", slot_key(slot, "replica.json"), ctx["targets"][slot])


def policy_selection(ctx, slot):
    source = ctx["sources"][slot]
    predecessor = ctx["documents"][slot_key(slot, "attempt.json")]["predecessor"]
    return {"package_file_count": len(source["current"]["files"]),
            "package_directory_count": len(source["current"]["directories"]),
            "package_bytes": sum(row["byte_size"] for row in source["current"]["files"]),
            "request_bytes": len(ctx["outer"]["request.json"]),
            "attempt_bytes": len(ctx["outer"][slot_key(slot, "attempt.json")]),
            "source_selection_bytes": len(ctx["outer"][slot_key(slot, "source-selection.json")]),
            "predecessor_attempt_bytes": predecessor["attempt_ref"]["byte_size"] if predecessor["state"] == "published" else 0,
            "observer_node_bytes": len(ctx["node_raw"])}


def prepare_scope_counters(ctx, slot):
    selected = policy_selection(ctx, slot)
    size = selected["package_bytes"]
    controls = sum(selected[field] for field in ("request_bytes", "attempt_bytes", "source_selection_bytes", "predecessor_attempt_bytes"))
    copy_control_size = sum(len(raw) for key, raw in ctx["outer"].items()
                            if key.startswith(slot_key(slot, "copy-attempts/")) or key.startswith(slot_key(slot, "copy-records/")))
    bootstrap_size = selected["request_bytes"] + selected["attempt_bytes"] + selected["predecessor_attempt_bytes"]
    source_begin = snapshot(read_bytes=bootstrap_size * 2 + len(ctx["node_raw"]),
                            read_calls=100, seek_calls=1, confirmed_write_bytes=bootstrap_size,
                            write_calls=10, elapsed_ns=10, current_resources=5, peak_resources=5)
    gate = snapshot(read_bytes=size + controls * 2 + len(ctx["node_raw"]), source_read_bytes=size,
                    read_calls=1000 + ceiling_chunks(size), source_read_calls=ceiling_chunks(size) + 1,
                    seek_calls=10, confirmed_write_bytes=controls, write_calls=100,
                    elapsed_ns=20, current_resources=selected["package_file_count"] + selected["package_directory_count"] + 10,
                    peak_resources=1000)
    source_q = attributable(sum([size] * 4), source=True)
    target_q = attributable(sum([size] * 4), outer_bytes=controls * 2 + copy_control_size * 4,
                             node_bytes=len(ctx["node_raw"]) * 2,
                             write_bytes=size + copy_control_size)
    source_end = snapshot(read_bytes=size * 6 + controls * 2 + copy_control_size * 2 + len(ctx["node_raw"]),
                          source_read_bytes=size * 4, read_calls=10000 + ceiling_chunks(size * 8),
                          source_read_calls=source_q["source_read_calls"], seek_calls=100,
                          confirmed_write_bytes=controls + size + copy_control_size, write_calls=10000,
                          elapsed_ns=30, current_resources=10, peak_resources=1000)
    target_tail = size * 2 + controls * 2 + copy_control_size * 2 + len(ctx["node_raw"]) * 2
    target_end = {**source_end, "read_bytes": source_end["read_bytes"] + target_tail,
                  "read_calls": source_end["read_calls"] + ceiling_chunks(target_tail) + 1,
                  "elapsed_ns": 40, "current_resources": 2}
    source_scope = {"scope": "source", "scope_id": ctx["sources"][slot]["current"]["observation_scope_id"],
                    "begin": source_begin, "end": source_end, "attributable": source_q}
    target_scope = {"scope": "target", "scope_id": ctx["targets"][slot]["current"]["observation_scope_id"],
                    "begin": snapshot(), "end": target_end, "attributable": target_q}
    ctx["scopes"][slot] = {"source": source_scope, "target": target_scope,
                           "payload_gate": {"selection": selected,
                                            "demand": policy_oracle.expected_payload(selected), "snapshot": gate}}


def copy_record_refs(ctx, slot):
    return [actual_ref(ctx, key) for key in ordered(ctx["outer"])
            if key.startswith(slot_key(slot, "copy-records/"))]


def add_source_closed(ctx, slot):
    prepare_scope_counters(ctx, slot)
    value = {**header("source_closed", ctx, slot), "selection_ref": actual_ref(ctx, slot_key(slot, "source-selection.json")),
             "content_set_sha256": ctx["documents"]["request.json"]["expected_content_set_sha256"],
             "scope_counters": ctx["scopes"][slot]["source"],
             "owned_close_summary": scope_summary("source", ctx["sources"][slot])}
    ctx["stages"][slot] = "source_closed_declared"
    return add_fact(ctx, "source_closed", slot_key(slot, "source-closed.json"), value)


def add_target_closed(ctx, slot):
    value = {**header("target_closed", ctx, slot), "selection_ref": actual_ref(ctx, slot_key(slot, "source-selection.json")),
             "replica_ref": actual_ref(ctx, slot_key(slot, "replica.json")),
             "source_closed_ref": actual_ref(ctx, slot_key(slot, "source-closed.json")),
             "copy_record_set_sha256": domain_hash("copy-record-set", copy_record_refs(ctx, slot)),
             "scope_counters": ctx["scopes"][slot]["target"],
             "owned_close_summary": scope_summary("target", ctx["targets"][slot])}
    ctx["stages"][slot] = "target_closed_declared"
    return add_fact(ctx, "target_closed", slot_key(slot, "target-closed.json"), value)


def add_manifest(ctx, slot):
    wanted = {"request.json"} | {key for key in ctx["outer"] if key.startswith(slot_key(slot, ""))}
    predecessor = ctx["documents"][slot_key(slot, "attempt.json")]["predecessor"]
    if predecessor["state"] == "published":
        wanted.add(predecessor["attempt_ref"]["relative_key"])
    refs = [actual_ref(ctx, key) for key in ordered(wanted)]
    size = policy_selection(ctx, slot)["package_bytes"]
    prior, node_size = sum(row["byte_size"] for row in refs), len(ctx["node_raw"])
    late = sum(len(ctx["outer"][slot_key(slot, name)]) for name in ("replica.json", "source-closed.json", "target-closed.json"))
    publisher_reopen_bytes = size + (prior - late) + node_size
    checkpoint = deepcopy(ctx["documents"][slot_key(slot, "target-closed.json")]["scope_counters"]["end"])
    checkpoint.update(read_bytes=checkpoint["read_bytes"] + publisher_reopen_bytes + late * 2,
                      read_calls=checkpoint["read_calls"] + ceiling_chunks(publisher_reopen_bytes + late * 2) + 1,
                      confirmed_write_bytes=checkpoint["confirmed_write_bytes"] + late,
                      write_calls=checkpoint["write_calls"] + 20, elapsed_ns=50, current_resources=10)
    checkpoint["peak_resources"] = max(checkpoint["peak_resources"], checkpoint["current_resources"])
    future_reads = [("package_final_1", size), ("package_final_2", size),
                    ("prior_final_1", prior), ("prior_final_2", prior),
                    ("node_final_1", node_size), ("node_final_2", node_size), ("inflight", 131072)]
    future_reads.extend((kind + ":" + phase, cap) for kind, cap in (("manifest", 134217728), ("receipt", 16384))
                        for phase in ("temp", "formal", "final_1", "final_2"))
    value = {**header("manifest", ctx, slot),
             **{name + "_ref": actual_ref(ctx, slot_key(slot, path)) for name, path in (
                 ("selection", "source-selection.json"), ("replica", "replica.json"),
                 ("source_closed", "source-closed.json"), ("target_closed", "target-closed.json"))},
             "control_refs": refs, "copy_record_refs": copy_record_refs(ctx, slot),
             "copy_record_set_sha256": domain_hash("copy-record-set", copy_record_refs(ctx, slot)),
             "content_set_sha256": ctx["documents"]["request.json"]["expected_content_set_sha256"],
             "dependency_set_sha256": domain_hash("control-dependencies", refs),
             "payload_gate": deepcopy(ctx["scopes"][slot]["payload_gate"]),
             "publication_budget": {"checkpoint": checkpoint,
                 "inputs": {"package_bytes": size, "prior_control_bytes": prior, "observer_node_bytes": node_size},
                 "remaining_capacity": {"read_bytes": sum(amount for _, amount in future_reads),
                                        "source_read_bytes": 0, "write_bytes": sum([134217728, 16384, 16384])}}}
    ctx["stages"][slot] = "manifest_declared"
    return add_fact(ctx, "manifest", slot_key(slot, "manifest.json"), value)


def add_receipt_marker(ctx, slot):
    key = slot_key(slot, "receipt-started.claim")
    add_file(ctx, key, b"")
    ctx["stages"][slot] = "publication_unknown"


def add_receipt(ctx, slot):
    manifest_key = slot_key(slot, "manifest.json")
    amount = len(ctx["outer"][manifest_key])
    before = deepcopy(ctx["documents"][manifest_key]["publication_budget"]["checkpoint"])
    before.update(read_bytes=before["read_bytes"] + amount * 2,
                  read_calls=before["read_calls"] + ceiling_chunks(amount * 2) + 1,
                  confirmed_write_bytes=before["confirmed_write_bytes"] + amount,
                  write_calls=before["write_calls"] + ceiling_chunks(amount), elapsed_ns=60)
    value = {**header("receipt", ctx, slot), "manifest_ref": actual_ref(ctx, manifest_key),
             "receipt_start_marker": deepcopy(ctx["files"][slot_key(slot, "receipt-started.claim")]),
             "publication": "published_observed", "before_receipt": before}
    ctx["stages"][slot] = "receipt_declared"
    return add_fact(ctx, "receipt", slot_key(slot, "receipt.json"), value)


def extend_attempt(ctx, slot, stage="receipt_declared"):
    steps = [("attempt_declared", add_attempt), ("selection_declared", add_selection),
             ("copy_partial", add_copies), ("replica_declared", add_replica),
             ("source_closed_declared", add_source_closed), ("target_closed_declared", add_target_closed),
             ("manifest_declared", add_manifest), ("publication_unknown", add_receipt_marker),
             ("receipt_declared", add_receipt)]
    for name, function in steps:
        function(ctx, slot)
        if name == stage:
            break
    return ctx


def specimen(*, stage="receipt_declared", second=False, first_stage="selection_declared", **options):
    ctx = base_fixture(**options)
    add_request(ctx)
    if second:
        if first_stage is not None:
            extend_attempt(ctx, 1, first_stage)
        extend_attempt(ctx, 2, stage)
    else:
        extend_attempt(ctx, 1, stage)
    return ctx


def prefix_args(ctx):
    return {"outer_fact_bytes": deepcopy(ctx["outer"]),
            "package_raw2_fact_bytes_by_attempt": deepcopy(ctx["raw2"]),
            "operation_inventory": inventory(ctx)}


def complete_args(ctx, slot=1):
    return {**prefix_args(ctx), "attempt": slot,
            "observer_node_bytes": ctx["node_raw"], "observer_node_observation": deepcopy(ctx["node"])}


def reject(function, *args, suffix=None, **kwargs):
    with pytest.raises(ERROR) as raised:
        function(*args, **kwargs)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith(PREFIX)
    if suffix is not None:
        assert raised.value.code == PREFIX + suffix
    return raised.value


def assert_plain(value):
    if type(value) is dict:
        assert all(type(key) is str for key in value)
        for child in value.values():
            assert_plain(child)
    elif type(value) is list:
        for child in value:
            assert_plain(child)
    else:
        assert value is None or type(value) in (str, int, bool)


def attempt_report(ctx, slot, stage=None):
    selected = slot_key(slot, "source-selection.json") in ctx["outer"]
    if selected:
        required = [row["relative_key"] for row in ctx["documents"][slot_key(slot, "source-selection.json")]["raw_control_refs"]]
        present = ctx["raw2"].get(f"{slot:02d}", {})
        missing = [key for key in required if key not in present]
        all_raw = not missing
    else:
        missing, all_raw = [], False
    return {"slot": slot, "stage": stage or ctx["stages"].get(slot, "admitted"),
            "source_selection_status": "shape_and_links_checked" if selected else "not_established",
            "embedded_source_r01_status": "validated" if all_raw else "not_established",
            "embedded_target_r01_status": "validated" if all_raw and slot_key(slot, "replica.json") in ctx["outer"] else "not_established",
            "raw2_dependency_status": ("complete" if all_raw else "incomplete") if selected else "not_established",
            "missing_declared_raw2_fact_keys": missing, "package_completion": "not_established"}


def expected_prefix(ctx):
    occupied = [1, 2] if "resume-attempt-02.claim" in ctx["files"] else [1]
    known = "request.json" in ctx["outer"]
    return {"format_version": 1, "evidence_kind": "supplied_bytes_declaration",
            "outer_status": "valid_prefix", "request_status": "validated" if known else "not_established",
            "request_sha256": sha(ctx["outer"]["request.json"]) if known else None,
            "occupied_slots": occupied, "attempts": [attempt_report(ctx, slot) for slot in occupied],
            "package_completion": "not_established", **DECLARATION_FLAGS}


def expected_complete(ctx, slot=1):
    source = ctx["sources"][slot]
    return {"format_version": 1, "evidence_kind": "supplied_bytes_declaration",
            "outer_status": "valid_complete_selected_attempt", "attempt": slot,
            "request_sha256": sha(ctx["outer"]["request.json"]),
            "source_selection_sha256": sha(ctx["outer"][slot_key(slot, "source-selection.json")]),
            "replica_sha256": sha(ctx["outer"][slot_key(slot, "replica.json")]),
            "manifest_sha256": sha(ctx["outer"][slot_key(slot, "manifest.json")]),
            "receipt_sha256": sha(ctx["outer"][slot_key(slot, "receipt.json")]),
            "content_set_sha256": source["package_map"]["content_set_sha256"],
            "package_file_count": len(source["current"]["files"]),
            "package_directory_count": len(source["current"]["directories"]),
            "package_bytes": sum(row["byte_size"] for row in source["current"]["files"]),
            "selected_package_completion": "validated_declaration",
            "other_attempts": [attempt_report(ctx, other) for other in (1, 2)
                               if other != slot and (other == 1 or "resume-attempt-02.claim" in ctx["files"])],
            **DECLARATION_FLAGS}


@pytest.mark.parametrize("old_kind", ["g1", "g2", "g1_with_g2", "resumed", "partial_B", "partial_manifest"])
@pytest.mark.parametrize("source_kind", ["detached_raw2", "profile_raw2"])
def test_independent_complete_tree_and_both_r01_observation_domains(old_kind, source_kind):
    ctx = specimen(old_kind=old_kind, source_kind=source_kind)
    before = deepcopy(prefix_args(ctx))
    result = contract.cross_validate_package(**complete_args(ctx))
    assert result == expected_complete(ctx)
    assert_plain(result)
    assert prefix_args(ctx) == before
    assert contract.cross_validate_prefix(**before) == expected_prefix(ctx)
    assert ctx["sources"][1]["current"] != ctx["targets"][1]["current"]
    for name in ("origin", "package_map", "source_map", "conclusions"):
        assert ctx["sources"][1][name] == ctx["targets"][1][name]


@pytest.mark.parametrize("stage", STAGES[1:])
def test_each_formal_prefix_has_exact_report_without_promoting_completion(stage):
    ctx = specimen(stage=stage)
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
    if stage != "receipt_declared":
        reject(contract.cross_validate_package, **complete_args(ctx))


@pytest.mark.parametrize("first_stage", [None, "attempt_declared", "selection_declared", "copy_partial", "target_closed_declared"])
def test_second_attempt_preserves_first_prefix_and_uses_separate_raw_namespace(first_stage):
    ctx = specimen(second=True, first_stage=first_stage)
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
    assert contract.cross_validate_package(**complete_args(ctx, 2)) == expected_complete(ctx, 2)
    assert ctx["documents"][slot_key(2, "attempt.json")]["predecessor"]["state"] == ("not_published" if first_stage is None else "published")


def local_specimens():
    ctx = specimen()
    result = {"request": ctx["documents"]["request.json"]}
    for kind in KINDS:
        if kind in {"request", "failure"}:
            continue
        if kind == "copy_attempt":
            key = next(key for key in ctx["documents"] if "/copy-attempts/" in key)
        elif kind == "copy_record":
            key = next(key for key in ctx["documents"] if "/copy-records/" in key)
        else:
            key = slot_key(1, kind.replace("_", "-") + ".json")
        result[kind] = ctx["documents"][key]
    result["failure"] = {**header("failure", ctx, 1), "phase": "copying", "code": "IO_ERROR",
        "counters": snapshot(uncertain_write_bytes=7, write_calls=1, elapsed_ns=8),
        "effects": [{"sequence": 1, "operation": "write", "relative_key": "attempts/01/package/request.json",
                     "outcome": "unknown", "confirmed_byte_count": None, "resource_count": None}],
        "effects_complete": False}
    return result


def replace_argument_bytes(args, path, raw, *, outer=True, slot=1):
    if outer:
        args["outer_fact_bytes"][path] = raw
        physical_key = path
    else:
        args["package_raw2_fact_bytes_by_attempt"].setdefault(f"{slot:02d}", {})[path] = raw
        physical_key = slot_key(slot, "package/" + path)
    row = next(row for row in args["operation_inventory"]["files"] if row["relative_key"] == physical_key)
    row.update(sha256=sha(raw), byte_size=len(raw))
    row["path_identity"]["byte_size"] = row["handle_identity"]["byte_size"] = len(raw)


def remove_object(ctx, key):
    for mapping in ("outer", "documents", "objects", "files"):
        ctx[mapping].pop(key, None)


def changed_old_partial(key, change, *, raw_transform=None):
    """Rebind only declarations to a bad old object; other old deps stay absent."""
    ctx = base_fixture()
    old = ctx["old"]
    value = json.loads(old["facts"][key])
    change(value)
    raw = wire(value)
    if raw_transform is not None:
        raw = raw_transform(raw)
    old["facts"][key] = old["raw_files"][key] = raw
    old["documents"][key] = value
    original = ctx["source_template"]
    source = historical.declaration(old, original["origin"]["generation"])
    source["replica_id"] = ctx["operation_id"]
    for field in ("profile_sha256", "node_id", "observation_scope_id"):
        source["current"][field] = original["current"][field]
    ctx["source_template"] = source
    ctx["request_value"]["source"]["expected_request_sha256"] = source["origin"]["request_sha256"]
    ctx["request_value"]["expected_content_set_sha256"] = source["package_map"]["content_set_sha256"]
    add_request(ctx)
    add_attempt(ctx, 1)
    add_selection(ctx, 1)
    index = next(index for index, row in enumerate(source["current"]["files"]) if row["relative_key"] == key)
    add_copies(ctx, 1, count=index + 1, record=False, provide_raw=False)
    ctx["raw2"] = {"01": {key: raw}}
    return ctx


@pytest.mark.parametrize("kind", KINDS)
def test_every_local_kind_matches_independent_canonical_bytes_and_detaches(kind):
    value = deepcopy(local_specimens()[kind])
    before = deepcopy(value)
    raw = wire(value)
    assert contract.encode(kind, value) == raw
    decoded = contract.decode(kind, raw)
    assert decoded == before == value and decoded is not value
    assert_plain(decoded)
    decoded.clear()
    assert value == before and contract.decode(kind, raw) == before


@pytest.mark.parametrize("kind", KINDS)
def test_all_kind_top_fields_are_closed(kind):
    original = local_specimens()[kind]
    for key in original:
        value = deepcopy(original)
        value.pop(key)
        reject(contract.encode, kind, value)
    reject(contract.encode, kind, {**deepcopy(original), "factory_returned": True})


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("alter", ["duplicate", "space", "bom", "float", "nan", "invalid_utf8"])
def test_all_decoders_reject_noncanonical_or_ambiguous_bytes(kind, alter):
    raw = wire(local_specimens()[kind])
    if alter == "duplicate":
        raw = b'{"format_version":1,' + raw[1:]
    elif alter == "space":
        raw += b"\n"
    elif alter == "bom":
        raw = b"\xef\xbb\xbf" + raw
    elif alter == "float":
        raw = raw.replace(b'"format_version":1', b'"format_version":1.0', 1)
    elif alter == "nan":
        raw = raw.replace(b'"format_version":1', b'"format_version":NaN', 1)
    else:
        raw = b"\xff" + raw
    reject(contract.decode, kind, raw)


@pytest.mark.parametrize("kind,cap", [("request", 1048576), ("attempt", 16384),
    ("source_selection", 134217728), ("copy_attempt", 8192), ("copy_record", 16384),
    ("replica", 134217728), ("source_closed", 16384), ("target_closed", 16384),
    ("manifest", 134217728), ("receipt", 16384), ("failure", 16384)])
def test_each_kind_decoder_enforces_its_own_raw_byte_cap(kind, cap):
    reject(contract.decode, kind, b"x" * (cap + 1), suffix="LIMIT")


@pytest.mark.parametrize("kind", ["", "source-selection", "request ", "REQUEST", "raw2", "target"])
def test_unknown_kind_is_not_normalized_or_forwarded(kind):
    reject(contract.encode, kind, {})
    reject(contract.decode, kind, b"{}")


@pytest.mark.parametrize("state", ["empty", "request_temp", "request_only", "anchor_only", "claim_only"])
def test_bootstrap_prefix_reports_only_the_evidence_that_is_present(state):
    ctx = base_fixture()
    if state == "request_temp":
        add_file(ctx, ".write-" + uid(800) + ".tmp", b"partial request")
    elif state != "empty":
        add_request(ctx, anchor=state != "request_only")
        if state == "claim_only":
            add_file(ctx, "resume-attempt-02.claim", b"")
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)


@pytest.mark.parametrize("alter", ["sha", "size", "missing_formal", "missing_row", "extra_formal"])
def test_request_only_formal_bytes_are_bound_to_inventory_before_any_reference(alter):
    ctx = base_fixture()
    add_request(ctx, anchor=False)
    args = prefix_args(ctx)
    row = next(row for row in args["operation_inventory"]["files"] if row["relative_key"] == "request.json")
    if alter == "sha":
        row["sha256"] = "f" * 64
    elif alter == "size":
        row["byte_size"] += 1
        row["path_identity"]["byte_size"] += 1
        row["handle_identity"]["byte_size"] += 1
    elif alter == "missing_formal":
        args["outer_fact_bytes"].clear()
    elif alter == "missing_row":
        args["operation_inventory"]["files"].clear()
        args["operation_inventory"]["directories"][0]["children"].remove("request.json")
    else:
        args["outer_fact_bytes"]["attempts/01/attempt.json"] = b"{}"
    reject(contract.cross_validate_prefix, **args)


def test_namespaces_and_generations_are_independent_and_cannot_be_flattened():
    ctx = specimen(second=True, old_kind="g1")
    assert ctx["documents"][slot_key(2, "source-selection.json")]["source_declaration"]["origin"]["generation"] == 1
    assert ctx["outer"]["request.json"] != ctx["raw2"]["02"]["request.json"]
    assert contract.cross_validate_package(**complete_args(ctx, 2)) == expected_complete(ctx, 2)
    args = complete_args(ctx, 2)
    args["package_raw2_fact_bytes_by_attempt"]["02"]["request.json"] = args["outer_fact_bytes"]["request.json"]
    reject(contract.cross_validate_package, **args)


@pytest.mark.parametrize("alter", ["outer_as_raw", "nonexistent_slot", "numeric_slot", "payload_as_fact", "raw_as_outer"])
def test_raw_namespace_cannot_borrow_or_add_unrelated_objects(alter):
    ctx = specimen()
    args = prefix_args(ctx)
    if alter == "outer_as_raw":
        args["package_raw2_fact_bytes_by_attempt"]["01"] = args["outer_fact_bytes"]
    elif alter == "nonexistent_slot":
        args["package_raw2_fact_bytes_by_attempt"]["02"] = deepcopy(args["package_raw2_fact_bytes_by_attempt"]["01"])
    elif alter == "numeric_slot":
        args["package_raw2_fact_bytes_by_attempt"][1] = args["package_raw2_fact_bytes_by_attempt"].pop("01")
    elif alter == "payload_as_fact":
        key = next(row["relative_key"] for row in ctx["sources"][1]["package_map"]["files"] if row["role"] == "payload")
        args["package_raw2_fact_bytes_by_attempt"]["01"][key] = ctx["old"]["raw_files"][key]
    else:
        args["outer_fact_bytes"]["runs/01/receipt.json"] = ctx["old"]["facts"]["runs/01/receipt.json"]
    reject(contract.cross_validate_prefix, **args)


def test_missing_raw_bytes_are_reported_only_for_an_unrecorded_partial_copy():
    ctx = specimen(stage="selection_declared")
    add_copies(ctx, 1, count=2, record=False, provide_raw=False)
    expected = expected_prefix(ctx)
    assert expected["attempts"][0]["raw2_dependency_status"] == "incomplete"
    assert expected["attempts"][0]["missing_declared_raw2_fact_keys"]
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected
    args = prefix_args(ctx)
    key = ctx["sources"][1]["current"]["files"][0]["relative_key"]
    assert key in ctx["old"]["facts"]
    args["package_raw2_fact_bytes_by_attempt"] = {"01": {key: ctx["old"]["facts"][key]}}
    expected["attempts"][0]["missing_declared_raw2_fact_keys"].remove(key)
    assert contract.cross_validate_prefix(**args) == expected


def test_recorded_control_cannot_hide_missing_bytes_behind_partial_status():
    ctx = specimen(stage="selection_declared")
    add_copies(ctx, 1, count=1, record=True, provide_raw=False)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("key", ["request.json", "selection.json", "source-metadata.json", "source-projection.json",
    "runs/01/run-attempt.json", "runs/01/capsule.json", "runs/01/source-verified.json",
    "runs/01/manifest.json", "runs/01/receipt.json"])
def test_partial_original_control_extra_top_field_is_invalid_despite_other_missing_facts(key):
    ctx = changed_old_partial(key, lambda value: value.update(invented_field=True))
    assert len(ctx["raw2"]["01"]) == 1
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("case", ["source_context_missing", "old_counter_bool", "old_source_bad_shape",
                                  "old_target_bad_identity", "old_selected_copy_bad_slot", "old_run_bad_reservation"])
def test_provided_old_nested_errors_are_not_downgraded_to_missing_dependencies(case):
    def change(value):
        if case == "source_context_missing":
            value["capsule"]["source_context"] = None
        elif case == "old_counter_bool":
            value["target_closed"]["counters"]["read_bytes"] = True
        elif case == "old_source_bad_shape":
            value["source_closed"]["extra"] = 0
        elif case == "old_target_bad_identity":
            value["target_closed"]["operation_identity"]["inode"] = "0"
        elif case == "old_selected_copy_bad_slot":
            value["selected_copies"][0]["origin_slot"] = 3
        else:
            value["reservation"]["read_bytes"] -= 1
    key = "source-metadata.json" if case == "source_context_missing" else "runs/01/run-attempt.json" if case == "old_run_bad_reservation" else "runs/01/source-verified.json"
    reject(contract.cross_validate_prefix, **prefix_args(changed_old_partial(key, change)))


def test_empty_historical_selection_still_requires_copying_the_entire_control_package():
    ctx = specimen(populated=False)
    assert ctx["sources"][1]["source_map"] == []
    assert len(ctx["sources"][1]["current"]["files"]) > 0
    assert contract.cross_validate_package(**complete_args(ctx)) == expected_complete(ctx)
    assert expected_complete(ctx)["package_bytes"] > 0


def test_equal_identity_numbers_across_source_and_target_domains_do_not_invent_native_alias_proof():
    ctx = specimen(same_identity_values=True)
    assert ctx["sources"][1]["current"]["directories"] == ctx["targets"][1]["current"]["directories"]
    assert contract.cross_validate_package(**complete_args(ctx)) == expected_complete(ctx)


@pytest.mark.parametrize("alter", ["request_missing", "anchor_missing", "selection_missing",
                                  "copy_attempt_missing", "record_missing", "replica_missing",
                                  "source_closed_missing", "target_closed_missing", "manifest_missing", "marker_missing"])
def test_downstream_objects_cannot_survive_missing_formal_predecessors(alter):
    ctx = specimen()
    if alter == "request_missing":
        remove_object(ctx, "request.json")
    elif alter == "anchor_missing":
        ctx["directories"].pop(next(key for key in ctx["directories"] if key.startswith("admission-rp1-")))
    elif alter == "copy_attempt_missing":
        remove_object(ctx, next(key for key in ctx["outer"] if "/copy-attempts/" in key))
    elif alter == "record_missing":
        remove_object(ctx, next(key for key in ctx["outer"] if "/copy-records/" in key))
    else:
        name = {"selection_missing": "source-selection.json", "replica_missing": "replica.json",
                "source_closed_missing": "source-closed.json", "target_closed_missing": "target-closed.json",
                "manifest_missing": "manifest.json", "marker_missing": "receipt-started.claim"}[alter]
        remove_object(ctx, slot_key(1, name))
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("alter", ["wrong_sha_anchor", "extra_anchor", "nonempty_anchor", "bad_claim_bytes",
                                  "claim_sha", "third_slot", "slot2_without_claim", "wrong_predecessor_variant"])
def test_anchor_and_admission_objects_are_exact_not_filename_hints(alter):
    ctx = specimen(stage="attempt_declared", second=True, first_stage="attempt_declared")
    if alter == "wrong_sha_anchor":
        anchor = next(key for key in ctx["directories"] if key.startswith("admission-rp1-"))
        ctx["directories"]["admission-rp1-sha256-" + "f" * 64] = ctx["directories"].pop(anchor)
    elif alter == "extra_anchor":
        add_directory(ctx, "admission-rp1-sha256-" + "f" * 64)
    elif alter == "nonempty_anchor":
        anchor = next(key for key in ctx["directories"] if key.startswith("admission-rp1-"))
        add_file(ctx, anchor + "/extra.raw", b"x")
    elif alter == "bad_claim_bytes":
        add_file(ctx, "resume-attempt-02.claim", b"not empty")
    elif alter == "claim_sha":
        ctx["files"]["resume-attempt-02.claim"]["sha256"] = "f" * 64
    elif alter == "third_slot":
        add_directory(ctx, "attempts/03")
    elif alter == "slot2_without_claim":
        remove_object(ctx, "resume-attempt-02.claim")
    else:
        value = deepcopy(ctx["documents"][slot_key(2, "attempt.json")])
        value["predecessor"] = {"state": "not_published", "slot": 1}
        add_fact(ctx, "attempt", slot_key(2, "attempt.json"), value)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("stage", ["publication_unknown", "receipt_declared"])
def test_prior_receipt_start_blocks_a_new_attempt_but_an_already_burned_claim_can_remain(stage):
    ctx = specimen(stage=stage)
    add_file(ctx, "resume-attempt-02.claim", b"")
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
    add_attempt(ctx, 2)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("stage", ["attempt_declared", "selection_declared", "manifest_declared"])
def test_short_retained_failure_is_not_parsed_and_cannot_promote_the_stage(stage):
    ctx = specimen(stage=stage)
    key = slot_key(1, "failures/" + ctx["run_ids"][1] + ".json")
    add_file(ctx, key, b'\xff{"truncated":')
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)


@pytest.mark.parametrize("alter", ["failure_before_attempt", "wrong_failure_run", "oversized_failure",
                                  "two_failures", "failure_plus_marker", "failure_plus_receipt",
                                  "formal_failure_map", "root_temp_after_request", "two_temps"])
def test_failure_marker_and_temporary_rules_are_not_used_as_resume_authority(alter):
    stage = "publication_unknown" if alter == "failure_plus_marker" else "receipt_declared" if alter == "failure_plus_receipt" else "attempt_declared"
    ctx = specimen(stage=stage)
    key = slot_key(1, "failures/" + ctx["run_ids"][1] + ".json")
    if alter == "wrong_failure_run":
        key = slot_key(1, "failures/" + uid(999) + ".json")
    add_file(ctx, key, b"x" * 16385 if alter == "oversized_failure" else b"partial")
    if alter == "failure_before_attempt":
        remove_object(ctx, slot_key(1, "attempt.json"))
    elif alter == "two_failures":
        add_file(ctx, slot_key(1, "failures/" + uid(998) + ".json"), b"another")
    elif alter == "formal_failure_map":
        ctx["outer"][key] = ctx["objects"][key]
    elif alter == "root_temp_after_request":
        add_file(ctx, ".write-" + uid(997) + ".tmp", b"x")
    elif alter == "two_temps":
        add_file(ctx, slot_key(1, ".write-" + uid(996) + ".tmp"), b"x")
        add_file(ctx, slot_key(1, ".write-" + uid(995) + ".tmp"), b"x")
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


def test_unrecorded_package_payload_can_be_partial_but_recorded_bytes_cannot_change():
    ctx = specimen(stage="selection_declared")
    add_copies(ctx, 1, count=1, record=False, provide_raw=False)
    key = slot_key(1, "package/" + ctx["sources"][1]["current"]["files"][0]["relative_key"])
    add_file(ctx, key, b"partial")
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
    complete = specimen(stage="copy_partial")
    add_file(complete, key, b"partial")
    reject(contract.cross_validate_prefix, **prefix_args(complete))


@pytest.mark.parametrize("alter", ["directory_alias", "file_alias", "file_directory_alias", "parent_identity",
                                  "path_handle_inode", "nlink", "mode", "duplicate_path", "casefold_child",
                                  "missing_empty_directory", "extra_package_file", "unknown_outer_name", "wrong_root"])
def test_full_physical_inventory_covers_all_paths_parents_and_identity_declarations(alter):
    ctx = specimen()
    args = complete_args(ctx)
    dirs, files = args["operation_inventory"]["directories"], args["operation_inventory"]["files"]
    if alter == "directory_alias":
        dirs[1]["identity"] = deepcopy(dirs[0]["identity"])
    elif alter == "file_alias":
        for field in ("path_identity", "handle_identity"):
            files[1][field].update({key: files[0][field][key] for key in ("device", "inode")})
    elif alter == "file_directory_alias":
        for field in ("path_identity", "handle_identity"):
            files[0][field].update(dirs[0]["identity"])
    elif alter == "parent_identity":
        files[0]["parent_identity"] = directory(919191)
    elif alter == "path_handle_inode":
        files[0]["handle_identity"]["inode"] = "919191"
    elif alter == "nlink":
        files[0]["path_identity"]["nlink"] = files[0]["handle_identity"]["nlink"] = 2
    elif alter == "mode":
        files[0]["path_identity"]["mode"] = files[0]["handle_identity"]["mode"] = 0o040700
    elif alter == "duplicate_path":
        files.insert(1, deepcopy(files[0]))
    elif alter == "casefold_child":
        dirs[0]["children"].append("REQUEST.JSON")
    elif alter == "missing_empty_directory":
        empty = next(row for row in dirs if row["relative_key"].startswith("attempts/01/package/") and not row["children"])
        dirs.remove(empty)
        parent = next(row for row in dirs if row["relative_key"] == posixpath.dirname(empty["relative_key"]))
        parent["children"].remove(posixpath.basename(empty["relative_key"]))
    elif alter == "extra_package_file":
        add_file(ctx, "attempts/01/package/unknown.raw", b"x")
        args["operation_inventory"] = inventory(ctx)
    elif alter == "unknown_outer_name":
        add_file(ctx, "unknown.raw", b"x")
        args["operation_inventory"] = inventory(ctx)
    else:
        dirs[0]["identity"] = directory(919191)
    reject(contract.cross_validate_package, **args)


@pytest.mark.parametrize("kind,field", [("copy_attempt", "selection_ref"), ("copy_record", "copy_attempt_ref"),
    ("copy_record", "selection_ref"), ("source_closed", "selection_ref"), ("target_closed", "replica_ref"),
    ("target_closed", "source_closed_ref"), ("manifest", "target_closed_ref"), ("receipt", "manifest_ref")])
def test_each_dag_reference_binds_actual_given_bytes_even_after_downstream_rebinding(kind, field):
    def change(value, _ctx):
        value[field]["sha256"] = "f" * 64
    ctx = specimen(mutations={kind: change})
    reject(contract.cross_validate_package, **complete_args(ctx))


@pytest.mark.parametrize("kind", ["attempt", "source_selection", "copy_attempt", "copy_record", "source_closed", "target_closed", "manifest", "receipt"])
def test_every_outer_fact_binds_same_operation_slot_run_and_original_request(kind):
    for field, wrong in (("operation_id", uid(991)), ("run_id", uid(992)), ("slot", 2), ("request_sha256", "f" * 64)):
        def change(value, _ctx):
            value[field] = wrong
        ctx = specimen(mutations={kind: change})
        reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("field", ["package_entry_id", "relative_key", "source_file_sha256", "source_byte_size", "target_parent_identity"])
def test_copy_attempt_uses_the_complete_package_entry_not_historical_source_id(field):
    wrong = {"package_entry_id": "f00000000", "relative_key": "other.raw", "source_file_sha256": "f" * 64,
             "source_byte_size": 0, "target_parent_identity": directory(991991)}[field]
    ctx = specimen(mutations={"copy_attempt": lambda value, _ctx: value.update({field: wrong})})
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


def test_two_copy_attempts_for_one_entry_cannot_adopt_or_retry_payload():
    ctx = specimen(stage="copy_partial")
    original = next(value for key, value in ctx["documents"].items() if "/copy-attempts/" in key)
    changed = {**deepcopy(original), "copy_id": uid(887766)}
    add_fact(ctx, "copy_attempt", slot_key(1, "copy-attempts/" + changed["copy_id"] + ".json"), changed)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


def test_copy_uuid_is_unique_across_outer_slots_even_when_both_local_paths_match():
    ctx = specimen(stage="selection_declared")
    add_copies(ctx, 1, count=1, record=False, provide_raw=False)
    add_attempt(ctx, 2)
    add_selection(ctx, 2)
    add_copies(ctx, 2, count=1, record=False, provide_raw=False)
    first = next(value for key, value in ctx["documents"].items() if key.startswith("attempts/01/copy-attempts/"))
    second_key = next(key for key in ctx["documents"] if key.startswith("attempts/02/copy-attempts/"))
    second = deepcopy(ctx["documents"][second_key])
    second["copy_id"] = first["copy_id"]
    remove_object(ctx, second_key)
    add_fact(ctx, "copy_attempt", slot_key(2, "copy-attempts/" + second["copy_id"] + ".json"), second)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("alter", ["duplicate", "noncanonical", "invalid_utf8", "wrong_top_type"])
def test_already_provided_bad_raw2_bytes_do_not_become_missing(alter):
    def transform(raw):
        if alter == "duplicate":
            return b'{"format_version":2,' + raw[1:]
        if alter == "noncanonical":
            return raw + b"\n"
        if alter == "invalid_utf8":
            return b"\xff" + raw
        return b"[]"
    ctx = changed_old_partial("request.json", lambda _value: None, raw_transform=transform)
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


def test_missing_history_is_not_validated_and_unequal_source_target_maps_fail_before_old_r01():
    def change(value, _ctx):
        value["source_declaration"]["source_map"][0]["copy_record_sha256"] = "f" * 64
        value["source_declaration_sha256"] = sha(wire(value["source_declaration"]))
    partial = specimen(stage="selection_declared", mutations={"source_selection": change})
    assert contract.cross_validate_prefix(**prefix_args(partial)) == expected_prefix(partial)
    complete = specimen(mutations={"source_selection": change})
    reject(contract.cross_validate_package, **complete_args(complete), suffix="CONTENT_CHANGED")


@pytest.mark.parametrize("stage", ["copy_partial", "receipt_declared"])
def test_complete_original_dependencies_reject_the_same_bad_r01_map_without_downgrading_to_partial(stage):
    def change_source(value, _ctx):
        value["source_declaration"]["source_map"][0]["copy_record_sha256"] = "f" * 64
        value["source_declaration_sha256"] = sha(wire(value["source_declaration"]))

    def change_target(value, _ctx):
        value["source_map"][0]["copy_record_sha256"] = "f" * 64

    ctx = specimen(stage=stage, mutations={"source_selection": change_source, "replica": change_target})
    selected = ctx["documents"][slot_key(1, "source-selection.json")]
    source_map = selected["source_declaration"]["source_map"]
    assert set(ctx["raw2"]["01"]) == {row["relative_key"] for row in selected["raw_control_refs"]}
    record = source_map[0]
    assert record["copy_record_sha256"] != sha(ctx["raw2"]["01"][record["copy_record_relative_key"]])
    if stage == "receipt_declared":
        assert source_map == ctx["documents"][slot_key(1, "replica.json")]["source_map"]
        reject(contract.cross_validate_package, **complete_args(ctx), suffix="R01_INVALID")
    else:
        assert slot_key(1, "replica.json") not in ctx["documents"]
        reject(contract.cross_validate_prefix, **prefix_args(ctx), suffix="R01_INVALID")


def test_complete_second_attempt_keeps_unrecorded_first_attempt_raw_missing():
    ctx = specimen(second=True, first_stage="selection_declared")
    add_copies(ctx, 1, count=1, record=False, provide_raw=False)
    result = contract.cross_validate_package(**complete_args(ctx, 2))
    assert result == expected_complete(ctx, 2)
    assert result["other_attempts"][0]["raw2_dependency_status"] == "incomplete"
    assert result["other_attempts"][0]["embedded_source_r01_status"] == "not_established"


def test_complete_selected_attempt_cannot_supply_another_late_attempts_missing_control():
    ctx = specimen(second=True, first_stage="target_closed_declared")
    assert ctx["raw2"]["01"] == ctx["raw2"]["02"]
    args = complete_args(ctx, 2)
    args["package_raw2_fact_bytes_by_attempt"]["01"].pop("request.json")
    reject(contract.cross_validate_package, **args)


@pytest.mark.parametrize("kind", ["source_closed", "target_closed"])
@pytest.mark.parametrize("alter", ["partition", "source_subset", "read_calls", "write_calls",
                                  "uncertain", "scope_id", "end_before_begin", "pending", "deadline"])
def test_scope_counters_reject_invalid_local_accounting(kind, alter):
    value = deepcopy(local_specimens()[kind])
    counts = value["scope_counters"]
    q = counts["attributable"]
    if alter == "partition":
        q["other_native_control_read_bytes"] += 1
    elif alter == "source_subset":
        q["source_read_bytes"] = q["read_bytes"] + 1
    elif alter == "read_calls":
        q["read_calls"] = 0
    elif alter == "write_calls":
        q["confirmed_write_bytes"] = 131073
        q["write_calls"] = 1
    elif alter == "uncertain":
        q["uncertain_read_calls"] = 1
    elif alter == "scope_id":
        counts["scope_id"] = uid(989)
    elif alter == "end_before_begin":
        counts["begin"]["read_bytes"] = counts["end"]["read_bytes"] + 1
    elif alter == "pending":
        counts["end"]["pending_resources"] = 1
    else:
        counts["end"]["elapsed_ns"] = 1800000000000
    reject(contract.encode, kind, value)


def test_overlapping_scopes_cannot_each_attribute_the_same_global_interval():
    def fill_interval(value, _ctx):
        counts = value["scope_counters"]
        q = counts["attributable"]
        delta = counts["end"]["read_bytes"] - counts["begin"]["read_bytes"]
        q["other_native_control_read_bytes"] += delta - q["read_bytes"]
        q["read_bytes"] = delta
        q["read_calls"] = ceiling_chunks(delta) + 1
    ctx = specimen(mutations={"source_closed": fill_interval, "target_closed": fill_interval})
    source = ctx["documents"][slot_key(1, "source-closed.json")]["scope_counters"]
    target = ctx["documents"][slot_key(1, "target-closed.json")]["scope_counters"]
    assert source["attributable"]["read_bytes"] <= source["end"]["read_bytes"] - source["begin"]["read_bytes"]
    assert target["attributable"]["read_bytes"] <= target["end"]["read_bytes"] - target["begin"]["read_bytes"]
    assert source["attributable"]["read_bytes"] + target["attributable"]["read_bytes"] > target["end"]["read_bytes"] - target["begin"]["read_bytes"]
    reject(contract.cross_validate_package, **complete_args(ctx))


@pytest.mark.parametrize("kind,location", [("target_closed", "scope_end"), ("manifest", "checkpoint"), ("receipt", "before_receipt")])
@pytest.mark.parametrize("field", ["source_read_bytes", "source_read_calls"])
def test_source_counters_are_frozen_after_its_normal_close(kind, location, field):
    def change(value, _ctx):
        counters = (value["scope_counters"]["end"] if location == "scope_end" else
                    value["publication_budget"]["checkpoint"] if location == "checkpoint" else value["before_receipt"])
        counters[field] += 1
    ctx = specimen(mutations={kind: change})
    reject(contract.cross_validate_package, **complete_args(ctx))


def test_scope_end_may_still_include_other_scopes_resources_without_claiming_global_settled():
    ctx = specimen()
    source_end = ctx["documents"][slot_key(1, "source-closed.json")]["scope_counters"]["end"]
    target_end = ctx["documents"][slot_key(1, "target-closed.json")]["scope_counters"]["end"]
    assert source_end["current_resources"] > 0 and target_end["current_resources"] > 0
    assert contract.cross_validate_package(**complete_args(ctx)) == expected_complete(ctx)


@pytest.mark.parametrize("kind", ["source_closed", "target_closed"])
@pytest.mark.parametrize("field,bad", [("resource_acquires", 100000001), ("resource_normal_closes", 0),
    ("peak_owned_resources", 1000001), ("remaining_owned_resources", 1), ("pending_resource_acquires", 1),
    ("unknown_resource_acquires", 1), ("unknown_closes", 1), ("normal_closed", 1),
    ("resource_acquires", True), ("bound_inventory_sha256", "F" * 64)])
def test_close_summary_exact_bounds_do_not_turn_unknown_or_unsettled_into_normal(kind, field, bad):
    value = deepcopy(local_specimens()[kind])
    value["owned_close_summary"][field] = bad
    reject(contract.encode, kind, value)


def test_resource_id_namespace_boundary_and_original_acquisition_commitment_are_declarations_only():
    value = deepcopy(local_specimens()["source_closed"])
    closed = value["owned_close_summary"]
    closed["resource_acquires"] = closed["resource_normal_closes"] = 100000000
    closed["peak_owned_resources"] = 1000000
    assert contract.decode("source_closed", wire(value)) == value
    acquire_d = {"device": "7", "inode": "19"}
    initial_f = identity(19, 0, device=7)
    later_f = {**initial_f, "byte_size": 55, "mtime_ns": "999", "ctime_ns": "1000"}
    original_identity_sha = sha(wire(acquire_d))
    events = [
        {"sequence": 1, "resource_id": "r00000000", "kind": "file_handle", "action": "acquired", "identity_sha256": original_identity_sha},
        {"sequence": 2, "resource_id": "r00000000", "kind": "file_handle", "action": "normal_closed", "identity_sha256": original_identity_sha},
    ]
    assert sha(wire(initial_f)) != sha(wire(later_f))
    assert events[0]["identity_sha256"] == events[1]["identity_sha256"]
    closed.update(resource_acquires=1, resource_normal_closes=1, peak_owned_resources=1,
                  resource_events_sha256=domain_hash("resource-events", events))
    assert contract.encode("source_closed", value) == wire(value)
    # The API receives only the digest: it cannot independently inspect events.
    closed["resource_events_sha256"] = "e" * 64
    assert contract.encode("source_closed", value) == wire(value)


@pytest.mark.parametrize("group,field", [("selection", "package_bytes"), ("selection", "predecessor_attempt_bytes"),
    ("selection", "observer_node_bytes"), ("demand", "write_bytes"), ("demand", "peak_additional_resources")])
def test_payload_gate_cannot_override_actual_lengths_or_remaining_policy(group, field):
    def change(value, _ctx):
        value["payload_gate"][group][field] += 1
    reject(contract.cross_validate_package, **complete_args(specimen(mutations={"manifest": change})))


@pytest.mark.parametrize("field,cap", [("read_bytes", 2199023255552), ("source_read_bytes", 1099511627776),
                                     ("confirmed_write_bytes", 274877906944), ("current_resources", 1000000)])
def test_payload_gate_checks_already_spent_plus_remaining_not_remaining_alone(field, cap):
    def change(value, _ctx):
        snapshot_value = value["payload_gate"]["snapshot"]
        snapshot_value[field] = cap
        if field == "current_resources":
            snapshot_value["peak_resources"] = cap
        if field == "source_read_bytes":
            snapshot_value["read_bytes"] = cap
    reject(contract.cross_validate_package, **complete_args(specimen(mutations={"manifest": change})))


@pytest.mark.parametrize("alter", ["late_read_minus_one", "late_write_minus_one", "future_source", "wrong_control_bytes",
                                  "extra_failure", "too_late_write", "too_late_read"])
def test_publication_budget_binds_independent_future_event_capacity(alter):
    def change(value, _ctx):
        late = value["publication_budget"]
        if alter == "late_read_minus_one":
            late["remaining_capacity"]["read_bytes"] -= 1
        elif alter == "late_write_minus_one":
            late["remaining_capacity"]["write_bytes"] -= 1
        elif alter == "future_source":
            late["remaining_capacity"]["source_read_bytes"] = 1
        elif alter == "wrong_control_bytes":
            late["inputs"]["prior_control_bytes"] += 1
        elif alter == "extra_failure":
            late["remaining_capacity"]["write_bytes"] += 16384
        elif alter == "too_late_write":
            late["checkpoint"]["confirmed_write_bytes"] = 274877906944
        else:
            late["checkpoint"]["read_bytes"] = 2199023255552
    reject(contract.cross_validate_package, **complete_args(specimen(mutations={"manifest": change})))


def test_receipt_is_before_future_publisher_two_passes_and_never_requires_eleven_package_passes():
    ctx = specimen(padding=True)
    size = expected_complete(ctx)["package_bytes"]
    receipt = ctx["documents"][slot_key(1, "receipt.json")]
    before = receipt["before_receipt"]["read_bytes"]
    assert sum([size] * 9) <= before < sum([size] * 11)
    assert contract.cross_validate_package(**complete_args(ctx)) == expected_complete(ctx)
    assert set(receipt).isdisjoint({"factory_returned", "publisher_closed", "outer_exited", "can_write"})


@pytest.mark.parametrize("field", ["confirmed_write_bytes", "read_bytes"])
def test_receipt_snapshot_includes_actual_manifest_publication_cost(field):
    def change(value, ctx):
        manifest = ctx["documents"][slot_key(1, "manifest.json")]
        checkpoint = manifest["publication_budget"]["checkpoint"]
        length = len(ctx["outer"][slot_key(1, "manifest.json")])
        value["before_receipt"][field] = checkpoint[field] + length * (2 if field == "read_bytes" else 1) - 1
    reject(contract.cross_validate_package, **complete_args(specimen(mutations={"receipt": change})))


def test_payload_remaining_write_check_accepts_exact_cap_with_one_failure_reserve():
    def shift_snapshot(value, amount):
        value["confirmed_write_bytes"] += amount
        value["write_calls"] += ceiling_chunks(amount)

    def source_change(value, ctx):
        gate = ctx["scopes"][1]["payload_gate"]
        amount = 274877906944 - gate["demand"]["write_bytes"] - gate["snapshot"]["confirmed_write_bytes"]
        ctx["write_shift"] = amount
        for key in ("begin", "end"):
            shift_snapshot(value["scope_counters"][key], amount)

    def target_change(value, ctx):
        for key in ("begin", "end"):
            shift_snapshot(value["scope_counters"][key], ctx["write_shift"])

    def manifest_change(value, ctx):
        shift_snapshot(value["payload_gate"]["snapshot"], ctx["write_shift"])

    ctx = specimen(mutations={"source_closed": source_change, "target_closed": target_change,
                              "manifest": manifest_change})
    gate = ctx["documents"][slot_key(1, "manifest.json")]["payload_gate"]
    assert gate["snapshot"]["confirmed_write_bytes"] + gate["demand"]["write_bytes"] == 274877906944
    assert gate["demand"]["failure_write_reserve_bytes"] == 16384
    assert contract.cross_validate_package(**complete_args(ctx)) == expected_complete(ctx)


@pytest.mark.parametrize("alter", ["wrong_sha", "wrong_id", "wrong_size", "changed_inode", "different_parent", "noncanonical", "oversize"])
def test_complete_requires_current_observer_node_bytes_and_matching_observation(alter):
    ctx = specimen()
    args = complete_args(ctx)
    observed = args["observer_node_observation"]
    if alter == "wrong_sha":
        observed["sha256"] = "f" * 64
    elif alter == "wrong_id":
        args["observer_node_bytes"] = wire({"instance_id": uid(9991)})
    elif alter == "wrong_size":
        observed["byte_size"] += 1
    elif alter == "changed_inode":
        observed["path_identity"]["inode"] = observed["handle_identity"]["inode"] = "9911"
    elif alter == "different_parent":
        observed["parent_identity"] = directory(9911)
    elif alter == "noncanonical":
        args["observer_node_bytes"] += b"\n"
    else:
        args["observer_node_bytes"] = b"x" * 4097
    reject(contract.cross_validate_package, **args)


@pytest.mark.parametrize("alter", ["namespace_equals_operation", "namespace_equals_root", "operation_equals_root",
                                  "node_equals_parent", "node_equals_namespace", "node_equals_operation"])
def test_request_rejects_aliases_inside_current_target_observation_domain(alter):
    value = deepcopy(local_specimens()["request"])
    target = value["target"]
    if alter == "namespace_equals_operation":
        target["namespace_identity"] = deepcopy(target["operation_identity"])
    elif alter == "namespace_equals_root":
        target["namespace_identity"] = deepcopy(target["profile"]["roots"]["data"])
    elif alter == "operation_equals_root":
        target["operation_identity"] = deepcopy(target["profile"]["roots"]["media"])
    else:
        observed = (target["node"]["parent_identity"] if alter == "node_equals_parent" else
                    target["namespace_identity"] if alter == "node_equals_namespace" else target["operation_identity"])
        target["node"]["path_identity"].update(observed)
        target["node"]["handle_identity"].update(observed)
    reject(contract.encode, "request", value)


def test_profile_repeated_canonical_root_path_requires_the_same_declared_identity():
    value = deepcopy(local_specimens()["request"])
    observed = value["target"]["profile"]
    observed["profile_paths"][2] = observed["profile_paths"][1]
    observed["profile_sha256"] = sha(wire(observed["profile_paths"]))
    reject(contract.encode, "request", value)
    observed["roots"]["reports"] = deepcopy(observed["roots"]["media"])
    assert contract.encode("request", value) == wire(value)


def test_local_request_accepts_canonical_windows_paths_without_touching_them():
    value = deepcopy(local_specimens()["request"])
    observed = value["target"]["profile"]
    observed["profile_paths"] = [r"c:\observed\data\reno.db", r"c:\observed\media", r"c:\observed\reports", r"c:\observed\checkpoints"]
    observed["profile_sha256"] = sha(wire(observed["profile_paths"]))
    source = value["source"]
    source["package_root"] = r"d:\detached\raw2"
    for row, path in zip(source["ancestors"], ["d:\\", r"d:\detached", r"d:\detached\raw2"]):
        row["absolute_path"] = path
    assert contract.decode("request", wire(value)) == value


@pytest.mark.parametrize("path", ["relative/path", "/a/../b", "/a//b", "//server/share", "C:\\Upper",
                                 "\\\\server\\share", "\\\\?\\c:\\device", "file:///tmp/x", "/a/CON", "/a/trailing.", "/a/space "])
def test_source_absolute_paths_are_canonical_local_declarations(path):
    value = deepcopy(local_specimens()["request"])
    value["source"]["package_root"] = path
    reject(contract.encode, "request", value)


@pytest.mark.parametrize("alter", ["missing_ancestor", "duplicate_identity", "wrong_terminal", "wrong_parent_path",
                                  "detached_profile", "detached_operation", "profile_missing", "profile_wrong_package"])
def test_source_variant_and_complete_ancestor_chain_are_closed(alter):
    ctx = base_fixture(source_kind="profile_raw2" if alter.startswith("profile_") else "detached_raw2")
    value = deepcopy(ctx["request_value"])
    source = value["source"]
    if alter == "missing_ancestor":
        source["ancestors"].pop(1)
    elif alter == "duplicate_identity":
        source["ancestors"][0]["identity"] = deepcopy(source["ancestors"][1]["identity"])
    elif alter == "wrong_terminal":
        source["root_identity"] = directory(990099)
    elif alter == "wrong_parent_path":
        source["ancestors"][1]["absolute_path"] = "/other"
    elif alter == "detached_profile":
        source["profile"] = profile("/original")
    elif alter == "detached_operation":
        source["original_operation_id"] = uid(900)
    elif alter == "profile_missing":
        source["profile"] = None
    else:
        source["package_root"] = "/detached/raw2"
        source["ancestors"] = ancestor_chain(source["package_root"], source["root_identity"])
    reject(contract.encode, "request", value)


@pytest.mark.parametrize("key", ["../a", "/absolute", "a//b", "a/./b", "a\\b", "a:stream",
                                "CON", "nul.txt", "trailing.", "trailing ", "a\x00b", "a\nb", "x" * 256])
def test_relative_package_keys_reject_unsafe_or_noncanonical_components(key):
    value = deepcopy(local_specimens()["copy_attempt"])
    value["relative_key"] = key
    reject(contract.encode, "copy_attempt", value)


@pytest.mark.parametrize("place", ["current_file", "package_map_file", "source_map_file"])
def test_local_r01_file_bound_stays_64gib_before_original_dependencies_exist(place):
    value = deepcopy(local_specimens()["source_selection"])
    declaration = value["source_declaration"]
    if place == "current_file":
        row = declaration["current"]["files"][0]
        row["byte_size"] = row["path_identity"]["byte_size"] = row["handle_identity"]["byte_size"] = 68719476737
    elif place == "package_map_file":
        declaration["package_map"]["files"][0]["byte_size"] = 68719476737
    else:
        declaration["source_map"][0]["byte_size"] = 68719476737
    value["source_declaration_sha256"] = sha(wire(declaration))
    reject(contract.encode, "source_selection", value)


def test_local_r01_source_map_count_cannot_exceed_original_100000_bound():
    value = deepcopy(local_specimens()["replica"])
    template = value["source_map"][0]
    value["source_map"] = [{**template, "source_entry_id": f"f{index:08d}"} for index in range(100001)]
    reject(contract.encode, "replica", value)


@pytest.mark.parametrize("alter", ["inventory_rp1_domain", "content_rp1_domain", "fact_rp1_domain",
                                  "entry_wrong_domain", "copy_wrong_domain", "dependencies_wrong_domain"])
def test_old_and_new_digest_domains_cannot_be_interchanged(alter):
    if alter.startswith("inventory") or alter.startswith("content") or alter.startswith("fact") or alter.startswith("entry"):
        value = deepcopy(local_specimens()["source_selection"])
        declaration = value["source_declaration"]
        if alter == "inventory_rp1_domain":
            declaration["current"]["inventory_sha256"] = domain_hash("package-entry-set", {
                key: declaration["current"][key] for key in ("directories", "files")})
        elif alter == "content_rp1_domain":
            declaration["package_map"]["content_set_sha256"] = domain_hash("package-entry-set", {
                key: declaration["package_map"][key] for key in ("mapping", "directories", "files")})
        elif alter == "fact_rp1_domain":
            # Shape alone accepts an opaque origin digest; full old R01 must reject.
            def change(doc, _ctx):
                doc["source_declaration"]["origin"]["fact_set_sha256"] = domain_hash("control-dependencies", [])
                doc["source_declaration_sha256"] = sha(wire(doc["source_declaration"]))
            reject(contract.cross_validate_package, **complete_args(specimen(mutations={"source_selection": change})))
            return
        else:
            value["package_entry_set_sha256"] = domain_hash("copy-record-set", [])
        value["source_declaration_sha256"] = sha(wire(declaration))
        reject(contract.encode, "source_selection", value)
    else:
        def change(value, _ctx):
            if alter == "copy_wrong_domain":
                value["copy_record_set_sha256"] = domain_hash("control-dependencies", value["copy_record_refs"])
            else:
                value["dependency_set_sha256"] = domain_hash("copy-record-set", value["control_refs"])
        reject(contract.cross_validate_package, **complete_args(specimen(mutations={"manifest": change})))


def test_failure_snapshot_retains_real_unknown_and_over_cap_numbers_without_claiming_success():
    value = deepcopy(local_specimens()["failure"])
    value["counters"].update(read_bytes=2199023255553, source_read_bytes=1099511627777,
        read_calls=3, source_read_calls=2, confirmed_write_bytes=274877906945,
        uncertain_write_bytes=17, uncertain_read_calls=2, uncertain_source_read_calls=1,
        current_resources=1000001, peak_resources=1000001, pending_resources=3,
        uncertain_resource_acquires=1, elapsed_ns=1800000000001)
    assert contract.encode("failure", value) == wire(value)
    assert contract.decode("failure", wire(value)) == value


@pytest.mark.parametrize("alter", ["phase", "code", "sequence", "too_many", "operation", "outcome", "count_type", "complete_type"])
def test_failure_effect_schema_is_bounded_and_does_not_coerce_unknown_counts(alter):
    value = deepcopy(local_specimens()["failure"])
    if alter == "phase":
        value["phase"] = "complete"
    elif alter == "code":
        value["code"] = "SUCCESS"
    elif alter == "sequence":
        value["effects"][0]["sequence"] = 2
    elif alter == "too_many":
        value["effects"] = [{**value["effects"][0], "sequence": index + 1} for index in range(17)]
    elif alter == "operation":
        value["effects"][0]["operation"] = "repair_acl"
    elif alter == "outcome":
        value["effects"][0]["outcome"] = "presumed_success"
    elif alter == "count_type":
        value["effects"][0]["confirmed_byte_count"] = True
    else:
        value["effects_complete"] = 1
    reject(contract.encode, "failure", value)


@pytest.mark.parametrize("place", ["version", "slot", "byte_size", "count", "boolean"])
@pytest.mark.parametrize("bad", [None, "1", 1.0])
def test_scalar_fields_are_exact_and_not_coerced(place, bad):
    kind = "source_closed" if place in {"count", "boolean"} else "copy_record"
    value = deepcopy(local_specimens()[kind])
    if place == "version":
        value["format_version"] = bad
    elif place == "slot":
        value["slot"] = bad
    elif place == "byte_size":
        value["byte_size"] = bad
    elif place == "count":
        value["owned_close_summary"]["resource_acquires"] = bad
    else:
        value["owned_close_summary"]["normal_closed"] = bad
    reject(contract.encode, kind, value)


@pytest.mark.parametrize("place", ["version", "slot", "byte_size", "read_bytes", "nlink"])
def test_boolean_is_never_accepted_as_an_integer(place):
    kind = "source_closed" if place == "read_bytes" else "copy_record"
    value = deepcopy(local_specimens()[kind])
    if place == "read_bytes":
        value["scope_counters"]["end"]["read_bytes"] = True
    elif place == "nlink":
        value["path_identity"]["nlink"] = True
    else:
        value["format_version" if place == "version" else place] = True
    reject(contract.encode, kind, value)


@pytest.mark.parametrize("alter", ["negative_counter", "u64_overflow", "zero_inode", "leading_zero_inode",
                                  "long_decimal", "nonregular_mode", "zero_uuid", "uppercase_uuid", "uppercase_sha"])
def test_scalar_identity_and_identifier_boundaries_are_fixed(alter):
    kind = "source_closed" if alter in {"negative_counter", "u64_overflow"} else "copy_record"
    value = deepcopy(local_specimens()[kind])
    if alter == "negative_counter":
        value["scope_counters"]["end"]["read_calls"] = -1
    elif alter == "u64_overflow":
        value["scope_counters"]["end"]["read_calls"] = 2**63
    elif alter == "zero_inode":
        value["path_identity"]["inode"] = "0"
    elif alter == "leading_zero_inode":
        value["path_identity"]["inode"] = "01"
    elif alter == "long_decimal":
        value["path_identity"]["inode"] = "1" * 41
    elif alter == "nonregular_mode":
        value["path_identity"]["mode"] = 0o120777
    elif alter == "zero_uuid":
        value["run_id"] = str(uuid.UUID(int=0))
    elif alter == "uppercase_uuid":
        value["run_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    else:
        value["sha256"] = "F" * 64
    reject(contract.encode, kind, value)


@pytest.mark.parametrize("alter", ["profile_nested_extra", "source_nested_missing", "r01_extra", "ref_extra",
                                  "summary_extra", "counter_missing", "manifest_budget_extra"])
def test_nested_closed_shapes_do_not_accept_extensions_or_omissions(alter):
    if alter.startswith("profile") or alter.startswith("source_nested"):
        kind = "request"
    elif alter == "r01_extra":
        kind = "source_selection"
    elif alter == "manifest_budget_extra":
        kind = "manifest"
    else:
        kind = "source_closed"
    value = deepcopy(local_specimens()[kind])
    if alter == "profile_nested_extra":
        value["target"]["profile"]["owner"] = True
    elif alter == "source_nested_missing":
        value["source"].pop("ancestors")
    elif alter == "r01_extra":
        value["source_declaration"]["capability"] = True
    elif alter == "ref_extra":
        value["selection_ref"]["verified"] = True
    elif alter == "summary_extra":
        value["owned_close_summary"]["actual_owner"] = True
    elif alter == "counter_missing":
        value["scope_counters"]["end"].pop("uncertain_resource_acquires")
    else:
        value["publication_budget"]["remaining_capacity"]["refund"] = 0
    reject(contract.encode, kind, value)


@pytest.mark.parametrize("shape", ["kind", "outer", "outer_key", "nested", "nested_key", "integer", "profile_text"])
def test_encode_rejects_hostile_exact_type_violations_before_hooks(shape):
    calls, armed, Text, Integer, Dictionary, _Tuple = policy_oracle.hostile_types()
    kind, value = "request", deepcopy(local_specimens()["request"])
    if shape == "kind":
        kind = Text(kind)
    elif shape == "outer":
        value = Dictionary(value)
    elif shape == "outer_key":
        original = value.pop("format_version")
        value[Text("format_version")] = original
    elif shape == "nested":
        value["target"] = Dictionary(value["target"])
    elif shape == "nested_key":
        original = value["target"].pop("node")
        value["target"][Text("node")] = original
    elif shape == "integer":
        value["format_version"] = Integer(1)
    else:
        value["policy"]["profile"] = Text("replica-rp1")
    armed[0] = True
    reject(contract.encode, kind, value)
    assert calls == []


@pytest.mark.parametrize("shape", ["outer_map", "raw2_map", "slot_key", "raw2_key", "inventory", "children_list"])
def test_graph_inputs_reject_hostile_containers_and_keys_before_dispatch(shape):
    calls, armed, Text, _Integer, Dictionary, _Tuple = policy_oracle.hostile_types()

    class List(list):
        def __iter__(self):
            calls.append("list.iter")
            raise AssertionError("Untrusted list iterator")
        def __len__(self):
            calls.append("list.len")
            raise AssertionError("Untrusted list length")

    args = prefix_args(specimen())
    if shape == "outer_map":
        args["outer_fact_bytes"] = Dictionary(args["outer_fact_bytes"])
    elif shape == "raw2_map":
        args["package_raw2_fact_bytes_by_attempt"]["01"] = Dictionary(args["package_raw2_fact_bytes_by_attempt"]["01"])
    elif shape == "slot_key":
        original = args["package_raw2_fact_bytes_by_attempt"].pop("01")
        args["package_raw2_fact_bytes_by_attempt"][Text("01")] = original
    elif shape == "raw2_key":
        original = args["package_raw2_fact_bytes_by_attempt"]["01"].pop("request.json")
        args["package_raw2_fact_bytes_by_attempt"]["01"][Text("request.json")] = original
    elif shape == "inventory":
        args["operation_inventory"] = Dictionary(args["operation_inventory"])
    else:
        root = args["operation_inventory"]["directories"][0]
        root["children"] = List(root["children"])
    armed[0] = True
    reject(contract.cross_validate_prefix, **args)
    assert calls == []


@pytest.mark.parametrize("raw", [None, "{}", bytearray(b"{}"), memoryview(b"{}"), MappingProxyType({})])
def test_decode_requires_exact_bytes(raw):
    reject(contract.decode, "request", raw)


def test_bytes_subclass_cannot_invoke_length_or_decode_hooks():
    calls = []

    class Bytes(bytes):
        def __len__(self):
            calls.append("len")
            raise AssertionError("Untrusted byte length")
        def decode(self, *args, **kwargs):
            calls.append("decode")
            raise AssertionError("Untrusted byte decode")

    reject(contract.decode, "request", Bytes(b"{}"))
    assert calls == []


@pytest.mark.parametrize("attempt", [True, 1.0, "1", 0, 3, None])
def test_complete_attempt_is_an_exact_existing_ordinal(attempt):
    args = complete_args(specimen())
    args["attempt"] = attempt
    reject(contract.cross_validate_package, **args)


@pytest.mark.parametrize("field", ["native_observation", "data_restored", "runtime_write", "sender_authority"])
def test_r01_capability_flags_cannot_be_promoted_inside_outer_selection(field):
    value = deepcopy(local_specimens()["source_selection"])
    value["source_declaration"]["conclusions"][field] = True
    value["source_declaration_sha256"] = sha(wire(value["source_declaration"]))
    reject(contract.encode, "source_selection", value)


def test_returned_graph_reports_are_detached_and_never_issue_admission_or_close_tokens():
    ctx = specimen(second=True)
    args = complete_args(ctx, 2)
    before = deepcopy(args)
    result = contract.cross_validate_package(**args)
    assert result == expected_complete(ctx, 2)
    assert_plain(result)
    result["other_attempts"][0]["missing_declared_raw2_fact_keys"].append("fake.raw")
    result["native_observation"] = True
    assert args == before
    assert contract.cross_validate_package(**args) == expected_complete(ctx, 2)
    prefix = contract.cross_validate_prefix(**prefix_args(ctx))
    prefix["attempts"].clear()
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
    assert set(result).isdisjoint({"ready", "factory_returned", "can_resume", "can_write", "owner", "token"})


def test_all_public_contract_apis_are_readonly_and_pure_without_io_clock_sql_or_network(monkeypatch):
    ctx = specimen(second=True)
    prefixes, complete = prefix_args(ctx), complete_args(ctx, 2)
    expected_p, expected_c = expected_prefix(ctx), expected_complete(ctx, 2)
    samples = local_specimens()
    encoded = {kind: wire(value) for kind, value in samples.items()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Pure runtime contract attempted an external effect")

    with monkeypatch.context() as patch:
        for module, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat"),
                             (os, "scandir"), (os, "mkdir"), (os, "rename"), (os, "unlink"),
                             (Path, "read_bytes"), (Path, "write_bytes"), (sqlite3, "connect"),
                             (socket, "socket"), (time, "monotonic"), (time, "monotonic_ns")):
            patch.setattr(module, name, forbidden)
        for kind in KINDS:
            assert contract.encode(kind, samples[kind]) == encoded[kind]
            assert contract.decode(kind, encoded[kind]) == samples[kind]
        assert contract.cross_validate_prefix(**prefixes) == expected_p
        assert contract.cross_validate_package(**complete) == expected_c


@pytest.mark.parametrize("operation", ["write", "flush", "fsync", "close"])
def test_failure_cannot_describe_its_own_future_data_fd_effects(operation):
    value = deepcopy(local_specimens()["failure"])
    value["effects"] = [{"sequence": 1, "operation": operation,
        "relative_key": slot_key(value["slot"], "failures/" + value["run_id"] + ".json"),
        "outcome": "returned", "confirmed_byte_count": 0, "resource_count": 0}]
    reject(contract.encode, "failure", value)
    reject(contract.decode, "failure", wire(value))


def test_failure_can_keep_prior_metadata_close_with_null_path_before_its_only_data_write():
    value = deepcopy(local_specimens()["failure"])
    value["effects"] = [{"sequence": 1, "operation": "close", "relative_key": None,
        "outcome": "returned", "confirmed_byte_count": None, "resource_count": 1}]
    assert contract.encode("failure", value) == wire(value)
    assert contract.decode("failure", wire(value)) == value


def test_raw_control_map_preserves_the_original_300032_entry_limit():
    args = prefix_args(specimen(stage="selection_declared"))
    args["package_raw2_fact_bytes_by_attempt"] = {
        "01": {f"runs/01/copy-records/f{index:08d}.json": b"{}" for index in range(300033)}}
    reject(contract.cross_validate_prefix, **args, suffix="LIMIT")


def test_local_r01_combined_inventory_limit_is_not_relaxed_to_outer_tree_limit():
    value = deepcopy(local_specimens()["replica"])
    template = {"relative_key": "", "identity": directory(1), "children": []}
    # A small shared-object declaration exceeds the list bound without allocating
    # payload buffers; exact codec limits must precede successful normalization.
    value["current"]["directories"] = [template] * 400065
    reject(contract.encode, "replica", value, suffix="LIMIT")


@pytest.mark.parametrize("old_kind", ["copy-attempts", "copy-records"])
@pytest.mark.parametrize("alter", ["extra_top", "wrong_path_id", "wrong_slot", "bad_parent", "bad_size"])
def test_partial_original_copy_controls_receive_local_validation_without_other_old_bytes(old_kind, alter):
    original = base_fixture()["old"]
    key = next(key for key in original["facts"] if "/" + old_kind + "/" in key)

    def change(value):
        row = value if old_kind == "copy-attempts" else value["copy"]
        if alter == "extra_top":
            value["retained_is_valid"] = True
        elif alter == "wrong_path_id":
            if old_kind == "copy-attempts":
                value["copy_id"] = uid(9891)
            else:
                row["source_entry_id"] = "f99999999"
        elif alter == "wrong_slot":
            value["slot"] = 2
        elif alter == "bad_parent":
            row["target_parent_identity"]["inode"] = "0"
        else:
            row["byte_size"] = 64 * 1024 ** 3 + 1

    ctx = changed_old_partial(key, change)
    assert set(ctx["raw2"]["01"]) == {key}
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("key,field", [
    ("request.json", "request_sha256"), ("selection.json", "selection_sha256"),
    ("source-metadata.json", "source_metadata_sha256"),
    ("source-projection.json", "source_projection_sha256"),
    ("runs/01/capsule.json", "capsule_sha256"),
    ("runs/01/source-verified.json", "source_verified_sha256"),
    ("runs/01/manifest.json", "manifest_sha256"),
    ("runs/01/receipt.json", "receipt_sha256"),
])
def test_each_provided_origin_control_binds_actual_sha_even_while_other_dependencies_are_missing(key, field):
    ctx = base_fixture()
    ctx["source_template"]["origin"][field] = "f" * 64
    if field == "request_sha256":
        ctx["request_value"]["source"]["expected_request_sha256"] = "f" * 64
    add_request(ctx)
    add_attempt(ctx, 1)
    add_selection(ctx, 1)
    index = next(index for index, row in enumerate(ctx["sources"][1]["current"]["files"])
                 if row["relative_key"] == key)
    add_copies(ctx, 1, count=index + 1, record=False, provide_raw=False)
    ctx["raw2"] = {"01": {key: ctx["old"]["facts"][key]}}
    assert sha(ctx["raw2"]["01"][key]) != ctx["sources"][1]["origin"][field]
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("alter", ["operation", "generation", "request_sha", "root_identity", "original_operation"])
def test_local_source_selection_binds_its_own_location_to_its_r01_without_outer_dependencies(alter):
    ctx = specimen(stage="selection_declared", source_kind="profile_raw2")
    value = deepcopy(ctx["documents"][slot_key(1, "source-selection.json")])
    source, declaration = value["source_location"], value["source_declaration"]
    if alter == "operation":
        value["operation_id"] = uid(9892)
    elif alter == "generation":
        source["generation"] = 2
    elif alter == "request_sha":
        source["expected_request_sha256"] = "f" * 64
    elif alter == "root_identity":
        source["root_identity"] = directory(9893, 901)
        source["ancestors"][-1]["identity"] = deepcopy(source["root_identity"])
    else:
        declaration["origin"]["operation_id"] = uid(9894)
        value["source_declaration_sha256"] = sha(wire(declaration))
    reject(contract.encode, "source_selection", value)
    reject(contract.decode, "source_selection", wire(value))


@pytest.mark.parametrize("path,cap", [
    ("request.json", 1048576), ("attempts/01/attempt.json", 16384),
    ("attempts/01/source-selection.json", 134217728),
    ("copy-attempt", 8192), ("copy-record", 16384),
    ("attempts/01/replica.json", 134217728),
    ("attempts/01/source-closed.json", 16384),
    ("attempts/01/target-closed.json", 16384),
])
def test_local_manifest_refs_use_the_referenced_formal_kind_cap(path, cap):
    value = deepcopy(local_specimens()["manifest"])
    if path in {"copy-attempt", "copy-record"}:
        path = next(row["relative_key"] for row in value["control_refs"] if "/" + path + "s/" in row["relative_key"])
    for rows in (value["control_refs"], value["copy_record_refs"]):
        for row in rows:
            if row["relative_key"] == path:
                row["byte_size"] = cap + 1
    for name in ("selection_ref", "replica_ref", "source_closed_ref", "target_closed_ref"):
        if value[name]["relative_key"] == path:
            value[name]["byte_size"] = cap + 1
    value["copy_record_set_sha256"] = domain_hash("copy-record-set", value["copy_record_refs"])
    value["dependency_set_sha256"] = domain_hash("control-dependencies", value["control_refs"])
    reject(contract.encode, "manifest", value, suffix="LIMIT")


def test_local_manifest_copy_record_subset_cannot_contain_a_different_formal_kind():
    value = deepcopy(local_specimens()["manifest"])
    value["copy_record_refs"] = [deepcopy(next(row for row in value["control_refs"] if row["relative_key"] == "request.json"))]
    value["copy_record_set_sha256"] = domain_hash("copy-record-set", value["copy_record_refs"])
    reject(contract.encode, "manifest", value)


@pytest.mark.parametrize("kind", ["copy_attempt", "copy_record"])
def test_locally_valid_ref_to_another_attempt_is_not_a_current_predecessor(kind):
    ctx = specimen(stage="selection_declared")
    ctx["mutations"][(kind, 2)] = lambda value, current: value.update(
        selection_ref=actual_ref(current, slot_key(1, "source-selection.json")))
    extend_attempt(ctx, 2, "receipt_declared")
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


@pytest.mark.parametrize("case", ["depth", "positive_digits", "negative_digits", "nodes"])
def test_decode_bounds_structure_before_allocating_json_objects(monkeypatch, case):
    if case == "depth":
        raw = b'{"x":' + b"[" * 64 + b"0" + b"]" * 64 + b"}"
    elif case in {"positive_digits", "negative_digits"}:
        raw = b'{"x":' + (b"-" if case == "negative_digits" else b"") + b"1" * 4301 + b"}"
    else:
        # root object, key and array consume three nodes before 3,999,998 scalars.
        raw = b'{"x":[' + b"0," * 3_999_997 + b"0]}"
    calls = []

    def forbidden_parser(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("over-limit input reached json.loads")

    monkeypatch.setattr(json, "loads", forbidden_parser)
    reject(contract.decode, "replica", raw, suffix="LIMIT")
    assert calls == []


@pytest.mark.parametrize("raw", [b'{"x":"\\q"}', b'{"x":"\\u12xz"}',
                                  b'{"x":"\\', b'{"x":"\x01"}', b'{"x":]}'])
def test_decoder_rejects_malformed_lexical_input_before_json_allocation(monkeypatch, raw):
    calls = []

    def forbidden_parser(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("malformed lexical input reached json.loads")

    monkeypatch.setattr(json, "loads", forbidden_parser)
    reject(contract.decode, "request", raw, suffix="JSON_INVALID")
    assert calls == []


@pytest.mark.parametrize("raw", [
    b'{"x":' + b"[" * 63 + b"0" + b"]" * 63 + b"}",
    b'{"x":' + b"[" * 64 + b"]" * 64 + b"}",
    b'{"x":' + b"1" * 4300 + b"}",
    b'{"x":-' + b"1" * 4300 + b"}",
])
def test_exact_json_representation_boundaries_reach_schema_rejection_without_limit_error(raw):
    # These are bounded JSON values, deliberately not complete domain objects.
    error = reject(contract.decode, "replica", raw)
    assert error.code != PREFIX + "LIMIT"


@pytest.mark.parametrize("case", ["depth", "positive_digits", "negative_digits"])
def test_encode_limits_apply_before_serializing_a_supplied_object_graph(case):
    if case == "depth":
        value = 0
        for _ in range(64):
            value = [value]
    else:
        value = 10 ** 4300
        if case == "negative_digits":
            value = -value
    reject(contract.encode, "replica", {"x": value}, suffix="LIMIT")


@pytest.mark.parametrize("path,field", [
    ("selection.json", "request_sha256"),
    ("selection.json", "initial_run_attempt_sha256"),
    ("selection.json", "source_metadata_sha256"),
    ("selection.json", "source_projection_sha256"),
    ("runs/01/run-attempt.json", "request_sha256"),
    ("copy-attempt", "request_sha256"),
    ("copy-attempt", "selection_sha256"),
    ("copy-attempt", "run_attempt_sha256"),
    ("copy-record", "request_sha256"),
    ("copy-record", "selection_sha256"),
    ("copy-record", "copy_attempt_sha256"),
    ("runs/01/source-verified.json", "request_sha256"),
    ("runs/01/source-verified.json", "selection_sha256"),
    ("runs/01/source-verified.json", "run_attempt_sha256"),
    ("runs/01/source-verified.json", "capsule_sha256"),
    ("runs/01/manifest.json", "request_sha256"),
    ("runs/01/manifest.json", "selection_sha256"),
    ("runs/01/manifest.json", "source_verified_sha256"),
    ("runs/01/receipt.json", "request_sha256"),
    ("runs/01/receipt.json", "source_verified_sha256"),
    ("runs/01/receipt.json", "manifest_sha256"),
])
def test_partial_original_required_header_digests_cannot_be_null(path, field):
    if path in {"copy-attempt", "copy-record"}:
        path = next(key for key in base_fixture()["old"]["facts"] if "/" + path + "s/" in key)
    ctx = changed_old_partial(path, lambda value: value.update({field: None}))
    assert set(ctx["raw2"]["01"]) == {path}
    assert json.loads(ctx["raw2"]["01"][path])[field] is None
    reject(contract.cross_validate_prefix, **prefix_args(ctx))


def test_partial_original_first_slot_keeps_its_explicitly_nullable_frozen_selection():
    path = "runs/01/run-attempt.json"
    ctx = changed_old_partial(path, lambda _value: None)
    assert json.loads(ctx["raw2"]["01"][path])["frozen_selection_sha256"] is None
    assert contract.cross_validate_prefix(**prefix_args(ctx)) == expected_prefix(ctx)
