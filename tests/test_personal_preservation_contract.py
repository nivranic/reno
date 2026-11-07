"""Synthetic fact documents test pure contracts, never real capture authority."""
from copy import deepcopy
import builtins
import math
import os
import uuid

import pytest

from reno import personal_preservation_contract as contract

OP = "11111111-1111-4111-8111-111111111111"
NODE = "22222222-2222-4222-8222-222222222222"
LIBRARY = "33333333-3333-4333-8333-333333333333"
ATTEMPT = "44444444-4444-4444-8444-444444444444"
ATTACHMENT = "55555555-5555-4555-8555-555555555555"
STAMP = "2026-10-04T05:00:00.000000Z"


def sha(value):
    return contract.digest(value)


def identity(inode, size, *, ctime="40"):
    return {"device": "1", "inode": str(inode), "byte_size": size,
            "mtime_ns": "30", "ctime_ns": ctime, "mode": 0o100600, "nlink": 1}


def directory(inode):
    return {"device": "1", "inode": str(inode)}


def summary(columns):
    return {**contract.typed_table_digest(columns, []), "schema_sha256": sha(["synthetic schema", list(columns)])}


def selection_facts(*, body_kind=None, receiver=False, wal=False, unknown=True):
    profile = {"database": "/library/data/reno.db", "media": "/library/media",
               "reports": "/library/data/generated", "checkpoints": "/library/data/sync-checkpoints"}
    request = {"format_version": 1, "kind": "personal_preservation_request", "operation_id": OP,
        "bundle_id": contract.bundle_id(OP), "source_profile": profile, "source_root_identity": directory(10),
        "target_parent_identity": directory(20),
        "target": "/library/data/" + contract.DIRECTORY + "/" + contract.bundle_id(OP),
        "scope": contract.SCOPE, "limits": dict(contract.DEFAULT_LIMITS)}
    roots = {"DATA": {"path": "/library/data", **directory(10), "membership": "unknown"},
             "WORKSPACE": {"path": "/library", **directory(11), "membership": "unknown"},
             "CONFIG": {"path": "/external-config", **directory(12), "membership": "unknown"},
             "MEDIA": {"path": profile["media"], **directory(13), "membership": "unknown"},
             "REPORTS": {"path": profile["reports"], **directory(14), "membership": "unknown"},
             "CHECKPOINTS": {"path": profile["checkpoints"], **directory(15), "membership": "unknown"}}
    definitions = [("CONFIG", "config.synthetic.json", "local_config_raw", 20),
                   ("DATA", "node-identity.json", "node_identity_raw", 40),
                   ("DATA", "reno.db", "database_raw", 4096)]
    if wal:
        definitions.append(("DATA", "reno.db-wal", "database_wal_raw", 100))
    if unknown:
        definitions.append(("DATA", "personal-attachments/.mirror-staging/raw.tmp", "unclassified_raw", 5))
    if body_kind is not None:
        role = {"current_ready": "current_personal_attachment", "historical_home": "historical_home_attachment",
                "receiver_stored": "receiver_stored_attachment", "sender_frozen": "sender_frozen_attachment"}[body_kind]
        path = ("personal-mirror-outbox/" + OP + ".files/" if body_kind == "sender_frozen" else
                "personal-attachments/files/") + ATTACHMENT + ".pdf"
        definitions.append(("DATA", path, role, 200))
    sender = body_kind == "sender_frozen"
    if sender:
        definitions.extend(("DATA", "personal-mirror-outbox/" + name, role, 30) for name, role in
            (("facility.json", "sender_facility_raw"), ("catalog.json", "sender_catalog_raw"),
             ("operation.lock", "sender_lock_raw"), (OP + ".prepare.json", "sender_request_raw"),
             (OP + ".blobs.json", "sender_blob_manifest_raw"), (OP + ".receipt.json", "sender_receipt_raw")))
    definitions.sort()
    inventory_names = {}
    for root, path, _role, _size in definitions:
        parent, _, name = path.rpartition("/")
        inventory_names.setdefault((root, parent), []).append(name)
    inventories = [{"root_kind": root, "relative_key": parent, "present": True,
                    "identity": directory(1000 + index), "names": sorted(names), "names_sha256": sha(sorted(names))}
                   for index, ((root, parent), names) in enumerate(sorted(inventory_names.items()))]
    inventory_map = {(item["root_kind"], item["relative_key"]): item for item in inventories}
    files = []
    binding = sha("synthetic original attachment descriptor")
    for index, (root, path, role, size) in enumerate(definitions):
        parent = path.rpartition("/")[0]
        observed = identity(100 + index, size)
        inventory = inventory_map[(root, parent)]
        files.append({"entry_id": f"f{index:08d}", "root_kind": root, "relative_key": path, "role": role,
            "sha256": sha(["synthetic original bytes", path]), "byte_size": size,
            "source_directory_identity": {**inventory["identity"], "names_sha256": inventory["names_sha256"]},
            "source_path_identity": observed, "source_handle_identity": {**observed, "ctime_ns": "41"},
            "binding_refs": [binding] if role in {"current_personal_attachment", "historical_home_attachment",
                "receiver_stored_attachment", "sender_frozen_attachment"} else []})
    by_role = {file["role"]: file for file in files}
    wire_fields = {"device", "inode", "byte_size", "mtime_ns", "ctime_ns"}
    fingerprint = {"format_version": 1, "profile_identity": list(profile.values()), "files": {"-journal": None}}
    for suffix, role in (("", "database_raw"), ("-wal", "database_wal_raw")):
        file = by_role.get(role)
        fingerprint["files"][suffix] = ({**{key: file["source_path_identity"][key] for key in wire_fields},
                                        "sha256": file["sha256"]} if file else None)
    sql = {"schema_version": 17, "source_fingerprint": fingerprint, "meta": summary(("key", "value")),
           "personal_tables": {table: summary(columns) for table, columns in contract.PERSONAL_COLUMNS.items()},
           "receiver_facility": "installed" if receiver else "absent",
           "receiver_tables": {table: summary(columns) for table, columns in contract.RECEIVER_COLUMNS.items()} if receiver else {},
           "integrity": "ok", "foreign_keys": "ok"}
    requirements = []
    if body_kind is not None:
        body = next(file for file in files if file["binding_refs"])
        requirements.append({"requirement_id": "r00000000", "kind": body_kind, "entry_id": body["entry_id"],
            "operation_id": OP, "attachment_id": ATTACHMENT, "sha256": body["sha256"], "byte_size": body["byte_size"],
            "binding_sha256": binding,
            "expected_file_identity": ({key: body["source_handle_identity"][key] for key in wire_fields}
                                       if body_kind != "current_ready" else None)})
    selection = {"format_version": 1, "kind": "personal_preservation_selection", "operation_id": OP,
        "request_sha256": sha(request), "source_observation": {"library_id": LIBRARY, "node_id": NODE,
            "personal_primary_id": NODE, "primary_epoch": 1, "profile_sha256": sha(profile),
            "node_sha256": by_role["node_identity_raw"]["sha256"], "roots": roots,
            "sender_facility": "installed" if sender else "absent", "receiver_facility": sql["receiver_facility"]},
        "sql": sql, "inventories": inventories, "files": files, "requirements": requirements,
        "unknowns": [file["entry_id"] for file in files if file["role"] == "unclassified_raw"],
        "limits_sha256": sha(request["limits"])}
    return request, selection


def completed_facts(**kwargs):
    request, selection = selection_facts(**kwargs)
    attempts, copies = [], []
    for index, source in enumerate(selection["files"]):
        attempt_id = str(uuid.uuid5(uuid.UUID(ATTEMPT), source["entry_id"]))
        attempt = {"format_version": 1, "kind": "personal_preservation_copy_attempt", "operation_id": OP,
            "selection_sha256": sha(selection), "entry_id": source["entry_id"], "attempt_id": attempt_id,
            "target_relative_key": f"payload/{attempt_id}/{source['entry_id']}.raw",
            "target_parent_identity": directory(2000 + index), "sha256": source["sha256"], "byte_size": source["byte_size"]}
        copied = identity(3000 + index, source["byte_size"], ctime="50")
        copies.append({**deepcopy(attempt), "kind": "personal_preservation_copy_record", "copy_path_identity": copied,
                       "copy_handle_identity": {**copied, "ctime_ns": "51"}})
        attempts.append(attempt)
    hashes = [{"entry_id": copy["entry_id"], "sha256": sha(copy)} for copy in copies]
    verified = {"format_version": 1, "kind": "personal_preservation_source_verified", "operation_id": OP,
        "request_sha256": sha(request), "selection_sha256": sha(selection), "attempt_id": ATTEMPT,
        "sql_fingerprint_sha256": sha(selection["sql"]["source_fingerprint"]), "copy_record_sha256s": hashes,
        "completed_at": STAMP}
    coverage = {"personal_original_facts": "complete", "sender_raw_history": "complete", "receiver_original_facts": "complete",
        "unknowns_present": bool(selection["unknowns"]), "knowledge_restore_dependencies": "unknown",
        "runtime_restore_authority": False, "primary_handoff_authority": False}
    manifest = {"format_version": 1, "kind": "personal_preservation_manifest", "operation_id": OP,
        "bundle_id": request["bundle_id"], "request_sha256": sha(request), "selection_sha256": sha(selection),
        "source_verified_sha256": sha(verified), "source_observation": selection["source_observation"], "sql": selection["sql"],
        "files": [{"source": source, "copy": copy} for source, copy in zip(selection["files"], copies)],
        "copy_record_sha256s": hashes, "requirements": selection["requirements"], "coverage": coverage, "created_at": STAMP}
    receipt = {"format_version": 1, "kind": "personal_preservation_receipt", "operation_id": OP,
        "request_sha256": sha(request), "selection_sha256": sha(selection), "manifest_sha256": sha(manifest),
        "source_verified_sha256": sha(verified), "target_directory_identity": directory(4000), "completed_at": STAMP}
    return request, selection, attempts, copies, verified, manifest, receipt


def state_for(request, selection, verified=None, manifest=None, receipt=None, *, phase="selected"):
    return {"format_version": 1, "kind": "personal_preservation_state", "operation_id": OP,
        "request_sha256": sha(request), "phase": phase, "selection_sha256": sha(selection) if selection else None,
        "source_verified_sha256": sha(verified) if verified else None, "manifest_sha256": sha(manifest) if manifest else None,
        "receipt_sha256": sha(receipt) if receipt else None, "updated_at": STAMP, "failure": None}


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'[]'])
def test_decode_refuses_ambiguous_or_nonfinite_json(raw):
    with pytest.raises(contract.Error):
        contract.decode(raw)


def test_canonical_json_is_unicode_stable_and_type_strict():
    assert contract.canonical_bytes({"乙": 2, "甲": 1}) == contract.canonical_bytes({"甲": 1, "乙": 2})
    assert b"\\u" not in contract.canonical_bytes({"city": "绍兴"})
    assert sha({"x": True}) != sha({"x": 1})
    for value in (math.nan, math.inf, {1: "not a JSON string key"}, {"x": b"raw"}):
        with pytest.raises(contract.Error):
            contract.canonical_bytes(value)
    loop = []
    loop.append(loop)
    with pytest.raises(contract.Error):
        contract.canonical_bytes(loop)


@pytest.mark.parametrize("body_kind", [None, "current_ready", "historical_home", "receiver_stored", "sender_frozen"])
def test_complete_document_chain_keeps_original_and_copy_identities(body_kind):
    request, selection, attempts, copies, verified, manifest, receipt = completed_facts(body_kind=body_kind, receiver=True, wal=True)
    assert contract.validate_request(request) == request
    assert contract.validate_selection(selection, request=request) == selection
    for attempt, copy in zip(attempts, copies):
        assert contract.validate_copy_attempt(attempt, selection=selection, request=request) == attempt
        assert contract.validate_copy_record(copy, selection=selection, attempt=attempt, request=request) == copy
    assert contract.validate_source_verified(verified, selection=selection, copy_records=copies, request=request) == verified
    assert contract.validate_manifest(manifest, selection=selection, source_verified=verified, copy_records=copies, request=request) == manifest
    assert contract.validate_receipt(receipt, manifest=manifest, selection=selection, source_verified=verified, request=request) == receipt
    returned = contract.validate_selection(selection, request=request)
    returned["source_observation"]["roots"]["CONFIG"]["path"] = "/different"
    assert selection["source_observation"]["roots"]["CONFIG"]["path"] == "/external-config"
    assert manifest["files"][0]["source"]["source_handle_identity"] != manifest["files"][0]["copy"]["copy_handle_identity"]


@pytest.mark.parametrize("mutation", ["extra", "format_bool", "uuid", "bundle", "target", "limit_bool", "root"])
def test_request_rejects_changed_operation_target_or_unclosed_fields(mutation):
    request, _ = selection_facts()
    if mutation == "extra":
        request["dispatch_permission"] = True
    elif mutation == "format_bool":
        request["format_version"] = True
    elif mutation == "uuid":
        request["operation_id"] = "{" + OP + "}"
    elif mutation == "bundle":
        request["bundle_id"] = NODE
    elif mutation == "target":
        request["target"] = "/library/data/sync-state.json"
    elif mutation == "limit_bool":
        request["limits"]["max_files"] = True
    else:
        request["source_root_identity"]["inode"] = "01"
    with pytest.raises(contract.Error):
        contract.validate_request(request)


def test_typed_sql_hash_preserves_original_cell_bytes_types_and_duplicates():
    columns = ["raw_json", "number", "blob"]
    rows = [(' {"x":1}', 1, b"\x00"), ('{"x":1}', 1.0, b"\x00")]
    first = contract.typed_table_digest(columns, rows)
    assert first == contract.typed_table_digest(columns, list(reversed(rows)))
    assert first != contract.typed_table_digest(columns, [rows[0], rows[0]])
    assert first != contract.typed_table_digest(columns, [(' {"x":1}', 1.0, b"\x00"), rows[1]])
    with pytest.raises(contract.Error):
        contract.typed_table_digest(columns, [("text", math.inf, b"x")])


@pytest.mark.parametrize("mutation", ["missing_body", "sha", "inode", "binding", "unknown_role", "sql_wal", "journal", "receiver", "columns", "inventory", "root", "unknowns"])
def test_selection_refuses_missing_changed_or_unproven_original_facts(mutation):
    request, selection = selection_facts(body_kind="historical_home", wal=True)
    body = next(file for file in selection["files"] if file["binding_refs"])
    if mutation == "missing_body":
        selection["requirements"][0]["entry_id"] = "f99999999"
    elif mutation == "sha":
        body["sha256"] = sha("changed bytes")
    elif mutation == "inode":
        body["source_path_identity"]["inode"] = "999"
        body["source_handle_identity"]["inode"] = "999"
    elif mutation == "binding":
        body["binding_refs"] = []
    elif mutation == "unknown_role":
        body["role"] = "unclassified_raw"
    elif mutation == "sql_wal":
        selection["sql"]["source_fingerprint"]["files"]["-wal"] = None
    elif mutation == "journal":
        selected = selection["sql"]["source_fingerprint"]["files"][""]
        selection["sql"]["source_fingerprint"]["files"]["-journal"] = {**selected, "byte_size": 1}
    elif mutation == "receiver":
        selection["sql"]["receiver_facility"] = "installed"
    elif mutation == "columns":
        selection["sql"]["personal_tables"]["source_attachment"]["columns"].append("invented")
    elif mutation == "inventory":
        selection["inventories"][0]["names"].append("today-new-file")
    elif mutation == "root":
        selection["source_observation"]["roots"]["DATA"]["inode"] = "999"
    else:
        selection["unknowns"] = []
    with pytest.raises(contract.Error):
        contract.validate_selection(selection, request=request)


@pytest.mark.parametrize("mutation", ["source_alias", "handle_change", "same_sha_new_inode", "size_bool", "attempt_change", "copy_path", "extra"])
def test_copy_record_cannot_replace_original_attempt_or_identity(mutation):
    request, selection, attempts, copies, verified, _manifest, _receipt = completed_facts()
    copy = copies[0]
    if mutation == "source_alias":
        copy["copy_path_identity"] = deepcopy(selection["files"][0]["source_path_identity"])
        copy["copy_handle_identity"] = deepcopy(selection["files"][0]["source_handle_identity"])
    elif mutation == "handle_change":
        copy["copy_handle_identity"]["inode"] = "999"
    elif mutation == "same_sha_new_inode":
        copy["copy_path_identity"]["inode"] = "999"
        copy["copy_handle_identity"]["inode"] = "999"
        with pytest.raises(contract.Error):
            contract.validate_source_verified(verified, selection=selection, copy_records=copies, request=request)
        return
    elif mutation == "size_bool":
        copy["byte_size"] = True
    elif mutation == "attempt_change":
        original_attempt = deepcopy(attempts[0])
        copy["target_parent_identity"]["inode"] = "999"
        assert attempts[0] == original_attempt
    elif mutation == "copy_path":
        copy["target_relative_key"] = "personal-mirror-outbox/facility.json"
    else:
        copy["restore_authority"] = True
    with pytest.raises(contract.Error):
        contract.validate_copy_record(copy, selection=selection, attempt=attempts[0], request=request)


def test_legal_new_copy_attempt_cannot_be_bound_to_the_original_attempt():
    request, selection, attempts, copies, _verified, _manifest, _receipt = completed_facts()
    original = deepcopy(attempts[0])
    new_attempt = deepcopy(original)
    new_attempt["attempt_id"] = str(uuid.uuid5(uuid.UUID(ATTEMPT), "a new explicit copy attempt"))
    new_attempt["target_relative_key"] = f"payload/{new_attempt['attempt_id']}/{new_attempt['entry_id']}.raw"
    new_attempt["target_parent_identity"] = directory(5000)
    observed_copy = identity(6000, new_attempt["byte_size"], ctime="60")
    new_record = {**deepcopy(new_attempt), "kind": "personal_preservation_copy_record",
                  "copy_path_identity": observed_copy, "copy_handle_identity": {**observed_copy, "ctime_ns": "61"}}
    assert contract.validate_copy_attempt(new_attempt, selection=selection, request=request) == new_attempt
    assert contract.validate_copy_record(new_record, selection=selection, attempt=new_attempt, request=request) == new_record
    with pytest.raises(contract.Error) as caught:
        contract.validate_copy_record(new_record, selection=selection, attempt=original, request=request)
    assert caught.value.code == "PERSONAL_PRESERVATION_COPY_ATTEMPT_CHANGED"
    assert attempts[0] == original and copies[0]["target_parent_identity"] == original["target_parent_identity"]


@pytest.mark.parametrize("field,value", [("runtime_restore_authority", True), ("runtime_restore_authority", 0),
    ("primary_handoff_authority", True), ("knowledge_restore_dependencies", "complete"), ("unknowns_present", False)])
def test_completed_manifest_never_grants_runtime_or_handoff_authority(field, value):
    request, selection, _attempts, copies, verified, manifest, _receipt = completed_facts()
    manifest["coverage"][field] = value
    with pytest.raises(contract.Error):
        contract.validate_manifest(manifest, selection=selection, source_verified=verified, copy_records=copies, request=request)


def test_state_supports_source_verified_before_manifest_and_published_receipt_gap():
    request, selection, _attempts, _copies, verified, manifest, receipt = completed_facts()
    state = state_for(request, selection, verified, phase="source_verified")
    assert contract.validate_state(state, request=request, selection=selection, source_verified=verified) == state
    failed = state_for(request, selection, verified, manifest, receipt, phase="failed")
    failed["failure"] = {"code": "PERSONAL_PRESERVATION_CANCELLED", "phase": "manifest", "attempt_id": ATTEMPT,
                         "original_digests": {key: failed[key] for key in ("request_sha256", "selection_sha256", "source_verified_sha256", "manifest_sha256", "receipt_sha256")}}
    assert contract.validate_state(failed, request=request, selection=selection, source_verified=verified, manifest=manifest, receipt=receipt) == failed
    failed["selection_sha256"] = None
    failed["failure"]["original_digests"]["selection_sha256"] = None
    with pytest.raises(contract.Error):
        contract.validate_state(failed)


def test_receipt_and_completion_require_precise_copy_set_and_original_hashes():
    request, selection, _attempts, copies, verified, manifest, receipt = completed_facts()
    with pytest.raises(contract.Error):
        contract.validate_source_verified(verified, selection=selection, copy_records=copies[:-1], request=request)
    receipt["manifest_sha256"] = sha("another completed manifest")
    with pytest.raises(contract.Error):
        contract.validate_receipt(receipt, manifest=manifest, selection=selection, source_verified=verified, request=request)
    manifest["sql"]["personal_tables"]["source_attachment"]["count"] = True
    with pytest.raises(contract.Error):
        contract.validate_manifest(manifest, selection=selection, source_verified=verified, copy_records=copies, request=request)


def test_validation_performs_no_filesystem_io(monkeypatch):
    request, selection, _attempts, copies, verified, manifest, receipt = completed_facts()
    def forbidden(*args, **kwargs):
        pytest.fail("Pure contract attempted filesystem I/O")
    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "open", forbidden)
        scoped.setattr(os, "stat", forbidden)
        contract.validate_request(request)
        contract.validate_selection(selection, request=request)
        contract.validate_manifest(manifest, selection=selection, source_verified=verified, copy_records=copies, request=request)
        contract.validate_receipt(receipt, manifest=manifest, selection=selection, source_verified=verified, request=request)


def test_windows_profile_keys_and_unregistered_authority_fields_are_rejected():
    assert contract.canonical_path("X:/Example/Project/data") == "x:\\example\\project\\data"
    request, _selection = selection_facts()
    request["source_profile"]["database"] = "/library/data/../other/reno.db"
    with pytest.raises(contract.Error):
        contract.validate_request(request)
    request, selection = selection_facts()
    selection["source_observation"]["dispatch_authority"] = True
    with pytest.raises(contract.Error) as caught:
        contract.validate_selection(selection, request=request)
    assert caught.value.code.startswith("PERSONAL_PRESERVATION_") and caught.value.retriable is False
