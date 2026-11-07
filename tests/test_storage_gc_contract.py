"""Logical retirement proofs on disposable committed images, never live data."""
import copy
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import migrations, storage_gc_contract as contract
from reno import storage_schema, sync_checkpoint_schema, sync_recovery_schema, sync_transfer_schema

STAMP = "2026-10-01T00:00:00.000Z"


def insert(con, table, value):
    con.execute(f"INSERT INTO {table}({','.join(value)}) VALUES({','.join('?' for _ in value)})", tuple(value.values()))


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "frozen.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    con.execute("CREATE TABLE video_asset(video_id TEXT PRIMARY KEY)")
    sync_transfer_schema.install(con)
    sync_checkpoint_schema.install(con)
    sync_recovery_schema.install(con)
    storage_schema.install(con)
    library_id = str(uuid.uuid4())
    con.executemany("INSERT INTO meta VALUES(?,?)", [("migration_version", "10"), ("library_id", library_id),
                                                   ("library_epoch", "1"), ("kb_revision", "0")])
    con.commit()
    value = {"con": con, "path": path, "library_id": library_id,
             "member_id": "mem_" + "a" * 64, "root_id": contract.digest({"root_device": "1", "root_inode": "2"})}
    yield value
    con.close()


def seed(image, *, kind="publication", disposition="journaled", operation_state="planned",
         item_state="pending", intent="delete_if_unreferenced"):
    con = image["con"]
    checkpoint_id, origin_id, operation_id = (str(uuid.uuid4()) for _ in range(3))
    version = {"library_id": image["library_id"], "library_epoch": 1, "kb_revision": 0}
    entry = {"checkpoint_id": checkpoint_id, "receiver_version_json": json.dumps(version),
             "format_version": 2 if kind == "recovery" else 1, "schema_version": 9,
             "storage_key": checkpoint_id + ".sqlite", "sha256": hashlib.sha256(b"artificial archive").hexdigest(),
             "byte_size": 18, "created_at": STAMP}
    if kind == "recovery":
        entry.update(operation_id=origin_id, origin_kind="recovery")
        insert(con, "sync_recovery_operation", {"operation_id": origin_id, "action": "restore",
               "request_sha256": "b" * 64, "request_json": "{}", "result_json": "{}", "created_at": STAMP})
        insert(con, "sync_recovery_checkpoint", entry)
    else:
        entry["publication_id"] = origin_id
        insert(con, "sync_publication", {"publication_id": origin_id, "source_library_id": str(uuid.uuid4()),
               "source_node_id": str(uuid.uuid4()), "source_epoch": 1, "source_revision": 0,
               "payload_sha256": "b" * 64, "knowledge_sha256": "c" * 64,
               "committed_at": STAMP, "local_version_json": json.dumps(version)})
        insert(con, "sync_checkpoint", entry)
    file = {"namespace": "checkpoint", "root_id": image["root_id"], "root_device": "1", "root_inode": "2",
            "storage_key": entry["storage_key"], "sha256": entry["sha256"], "byte_size": entry["byte_size"],
            "file_device": "1", "file_inode": "20", "file_mtime_ns": "42", "file_ctime_ns": "43", "intent": intent}
    file["artifact_id"] = "art_" + contract.digest({key: file[key] for key in ("namespace", "root_id", "storage_key")})
    selected = contract.checkpoint_descriptor(entry, root_id=image["root_id"],
                   archive_item_id=file["artifact_id"] if disposition == "journaled" else None)
    policy = contract.default_policy()
    request = {"format_version": 1, "action": "storage_gc", "operation_id": operation_id,
               "owner": {"member_id": image["member_id"], "library_id": image["library_id"],
                         "database_identity": {"device": "1", "inode": "10"}},
               "expected_preview_sha256": "d" * 64, "expected_roots_sha256": "e" * 64,
               "members_sha256": "f" * 64, "policy_sha256": contract.digest(policy),
               "retirements": [selected], "files": [file] if disposition == "journaled" else []}
    contract.validate_request(request, policy=policy)
    insert(con, "storage_gc_operation", {"operation_id": operation_id, "request_sha256": contract.digest(request),
           "preview_sha256": request["expected_preview_sha256"], "roots_sha256": request["expected_roots_sha256"],
           "policy_json": contract.canonical_bytes(policy).decode(), "request_json": contract.canonical_bytes(request).decode(),
           "result_json": None, "state": operation_state, "created_at": STAMP, "updated_at": STAMP})
    if disposition == "journaled":
        insert(con, "storage_artifact", {**{key: value for key, value in file.items() if key != "intent"},
                                         "created_at": STAMP, "updated_at": STAMP})
        insert(con, "storage_gc_item", {"operation_id": operation_id, **{key: file[key] for key in contract._ITEM_IDENTITY},
               "state": item_state, "deleted_bytes": file["byte_size"] if item_state == "deleted" else 0,
               "reason_code": None, "updated_at": STAMP})
    insert(con, "sync_checkpoint_retirement", {"origin_kind": kind, "checkpoint_id": checkpoint_id,
           "operation_id": operation_id, "storage_key": entry["storage_key"], "sha256": entry["sha256"],
           "byte_size": entry["byte_size"], "retired_at": STAMP})
    con.commit()
    image.update(entry=entry, request=request, policy=policy, file=file, operation_id=operation_id, kind=kind)
    return image


def proof(image, *, path=None, entry=None, **expected):
    con = sqlite3.connect((path or image["path"]).as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        options = {"expected_library_id": image["library_id"], "expected_member_id": image["member_id"],
                   "expected_root_id": image["root_id"], **expected}
        value = contract.verify_retirement(con, entry or image["entry"], **options)
        assert con.in_transaction and con.total_changes == 0
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("DELETE FROM meta")
        return value
    finally:
        con.close()


@pytest.mark.parametrize("kind", ["publication", "recovery"])
@pytest.mark.parametrize("operation_state", ["planned", "running", "blocked", "completed"])
@pytest.mark.parametrize("item_state", ["pending", "blocked", "retained", "deleted", "missing"])
def test_committed_logical_retirement_does_not_depend_on_physical_completion(image, kind, operation_state, item_state):
    seed(image, kind=kind, operation_state=operation_state, item_state=item_state)
    assert proof(image) == contract.RetirementProof("proven", "RETIREMENT_PROVEN", image["operation_id"])


def test_explicit_unowned_archive_can_retire_logically_without_a_delete_item(image):
    seed(image, disposition="preserve_unowned")
    assert image["request"]["files"] == []
    assert image["con"].execute("SELECT count(*) FROM storage_gc_item").fetchone()[0] == 0
    assert proof(image).status == "proven"


def test_retain_intent_records_retained_physical_item(image):
    seed(image, intent="retain", item_state="retained")
    assert proof(image).status == "proven"


def test_caller_writes_and_current_epoch_do_not_change_original_proof(image):
    seed(image)
    con = image["con"]
    con.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
    con.execute("UPDATE storage_artifact SET file_inode='21',file_mtime_ns='99',file_ctime_ns='100'")
    con.execute("UPDATE storage_gc_operation SET result_json='not a proof',updated_at='display only'")
    con.commit()
    assert proof(image).status == "proven"


def test_a_new_inode_for_the_backup_image_does_not_replace_signing_db_identity(image, tmp_path):
    seed(image)
    backup = tmp_path / "another-inode.sqlite"
    with sqlite3.connect(backup) as target:
        image["con"].backup(target)
    assert backup.stat().st_ino != image["path"].stat().st_ino
    assert proof(image, path=backup).status == "proven"


@pytest.mark.parametrize("size", [16, contract.MAX_JSON_BYTES + 1], ids=["binary", "oversize"])
def test_non_proof_mutable_fields_are_not_read_or_part_of_the_proof(image, size):
    seed(image)
    mutable_value = b"x" * size
    con = image["con"]
    con.execute("UPDATE storage_gc_operation SET result_json=?,updated_at=?", (mutable_value, mutable_value))
    con.execute("UPDATE storage_gc_item SET reason_code=?,updated_at=?", (mutable_value, mutable_value))
    con.execute("UPDATE storage_artifact SET file_device=?,file_inode=?,file_mtime_ns=?,file_ctime_ns=?,updated_at=?",
                (mutable_value,) * 5)
    con.commit()
    assert proof(image).status == "proven"


def test_current_retirement_cannot_release_original_backup_roots(image, tmp_path):
    seed(image)
    image["con"].execute("DELETE FROM sync_checkpoint_retirement")
    image["con"].commit()
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as target:
        image["con"].backup(target)
    selected = image["request"]["retirements"][0]
    insert(image["con"], "sync_checkpoint_retirement", {"origin_kind": image["kind"],
           "checkpoint_id": selected["checkpoint_id"], "operation_id": image["operation_id"],
           "storage_key": selected["storage_key"], "sha256": selected["sha256"],
           "byte_size": selected["byte_size"], "retired_at": STAMP})
    image["con"].commit()
    assert proof(image).status == "proven"
    assert proof(image, path=old).status == "not_retired"


@pytest.mark.parametrize("damage", ["operation_missing", "request_hash", "policy_hash", "preview_hash", "roots_hash",
                                      "row_sha", "item_identity", "item_missing", "artifact_content", "artifact_root",
                                      "origin_missing", "entry_changed", "retired_time", "noncanonical_request",
                                      "duplicate_json", "request_extra", "wrong_library", "wrong_member", "wrong_root",
                                      "schema_future", "retain_then_deleted", "policy_disable_protection"])
def test_contradictory_or_missing_proof_stays_unproven(image, damage):
    seed(image)
    con = image["con"]
    expected = {}
    if damage == "operation_missing":
        con.execute("DELETE FROM storage_gc_operation")
    elif damage == "request_hash":
        con.execute("UPDATE storage_gc_operation SET request_sha256=?", ("0" * 64,))
    elif damage == "policy_hash":
        image["request"]["policy_sha256"] = "0" * 64
    elif damage == "preview_hash":
        con.execute("UPDATE storage_gc_operation SET preview_sha256=?", ("0" * 64,))
    elif damage == "roots_hash":
        con.execute("UPDATE storage_gc_operation SET roots_sha256=?", ("0" * 64,))
    elif damage == "row_sha":
        con.execute("UPDATE sync_checkpoint_retirement SET sha256=?", ("0" * 64,))
    elif damage == "item_identity":
        con.execute("UPDATE storage_gc_item SET file_inode='999'")
    elif damage == "item_missing":
        con.execute("DELETE FROM storage_gc_item")
    elif damage == "artifact_content":
        con.execute("UPDATE storage_artifact SET sha256=?", ("0" * 64,))
    elif damage == "artifact_root":
        con.execute("UPDATE storage_artifact SET root_inode='999'")
    elif damage == "origin_missing":
        con.execute("DELETE FROM sync_publication")
    elif damage == "entry_changed":
        image["entry"]["byte_size"] += 1
    elif damage == "retired_time":
        con.execute("UPDATE sync_checkpoint_retirement SET retired_at='yesterday'")
    elif damage == "noncanonical_request":
        con.execute("UPDATE storage_gc_operation SET request_json=?", (json.dumps(image["request"]),))
    elif damage == "duplicate_json":
        con.execute("UPDATE storage_gc_operation SET request_json=?", ('{"format_version":1,"format_version":1}',))
    elif damage == "request_extra":
        image["request"]["unexpected"] = True
    elif damage == "wrong_library":
        expected["expected_library_id"] = str(uuid.uuid4())
    elif damage == "wrong_member":
        expected["expected_member_id"] = "mem_" + "b" * 64
    elif damage == "wrong_root":
        expected["expected_root_id"] = "0" * 64
    elif damage == "schema_future":
        con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(migrations.SCHEMA_VERSION + 1),))
    elif damage == "retain_then_deleted":
        image["request"]["files"][0]["intent"] = "retain"
        con.execute("UPDATE storage_gc_item SET state='deleted',deleted_bytes=byte_size")
    elif damage == "policy_disable_protection":
        image["policy"]["protect_active_recovery"] = False
        con.execute("UPDATE storage_gc_operation SET policy_json=?", (contract.canonical_bytes(image["policy"]).decode(),))
    if damage in ("policy_hash", "request_extra", "retain_then_deleted"):
        con.execute("UPDATE storage_gc_operation SET request_json=?,request_sha256=?",
                    (contract.canonical_bytes(image["request"]).decode(), contract.digest(image["request"])))
    con.commit()
    assert proof(image, **expected).status == "unproven"


@pytest.mark.parametrize("table", ["sync_checkpoint_retirement", "storage_gc_operation", "storage_gc_item",
                                    "storage_artifact", "sync_checkpoint", "sync_publication"])
def test_view_cannot_masquerade_as_a_proof_table(image, table):
    seed(image)
    con = image["con"]
    con.execute(f"ALTER TABLE {table} RENAME TO saved_{table}")
    con.execute(f"CREATE VIEW {table} AS SELECT * FROM saved_{table}")
    con.commit()
    assert proof(image).status == "unproven"


@pytest.mark.parametrize("schema_version", ["0", "6", "9"])
def test_legacy_schema_without_operational_tables_does_not_guess_retirement(image, schema_version):
    seed(image)
    con = image["con"]
    con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (schema_version,))
    con.execute("DROP TABLE sync_checkpoint_retirement")
    con.commit()
    assert proof(image).status == "not_retired"


@pytest.mark.parametrize("damage", ["duplicate_file", "duplicate_retirement", "unknown", "boolean_size", "uppercase_sha",
                                      "wrong_artifact", "unowned_with_item", "unowned_hidden_item", "version_bool",
                                      "nan", "disabled_protection", "keep_zero", "unsorted_namespaces"])
def test_request_and_policy_contract_reject_unsafe_encodings(image, damage):
    seed(image)
    request, policy = copy.deepcopy(image["request"]), copy.deepcopy(image["policy"])
    if damage == "duplicate_file":
        request["files"].append(copy.deepcopy(request["files"][0]))
    elif damage == "duplicate_retirement":
        request["retirements"].append(copy.deepcopy(request["retirements"][0]))
    elif damage == "unknown":
        request["extra"] = True
    elif damage == "boolean_size":
        request["files"][0]["byte_size"] = True
    elif damage == "uppercase_sha":
        request["files"][0]["sha256"] = "A" * 64
    elif damage == "wrong_artifact":
        request["files"][0]["artifact_id"] = "art_" + "0" * 64
    elif damage == "unowned_with_item":
        request["retirements"][0]["archive_disposition"] = "preserve_unowned"
    elif damage == "unowned_hidden_item":
        request["retirements"][0].update(archive_disposition="preserve_unowned", archive_item_id=None)
    elif damage == "version_bool":
        request["retirements"][0]["receiver_version"]["library_epoch"] = True
    elif damage == "nan":
        request["files"][0]["byte_size"] = float("nan")
    elif damage == "disabled_protection":
        policy["retain_latest_recoverable"] = False
    elif damage == "keep_zero":
        policy["keep_publication"] = 0
    elif damage == "unsorted_namespaces":
        policy["namespaces"] = list(reversed(policy["namespaces"]))
    with pytest.raises(contract.GCContractError):
        contract.validate_request(request, policy=policy)


def test_json_decoder_is_bounded_and_rejects_duplicates_and_noncanonical_text(monkeypatch):
    with pytest.raises(contract.GCContractError):
        contract.decode('{"a":1,"a":2}')
    with pytest.raises(contract.GCContractError):
        contract.decode('{"a":1.0}')
    with pytest.raises(contract.GCContractError):
        contract.decode('{ "a":1}', canonical=True)
    monkeypatch.setattr(contract, "MAX_JSON_BYTES", 8)
    with pytest.raises(contract.GCContractError, match="GC_JSON_LIMIT"):
        contract.decode('{"secret":"long value"}')
    monkeypatch.setattr(contract, "MAX_JSON_DEPTH", 2)
    with pytest.raises(contract.GCContractError, match="GC_JSON_LIMIT"):
        contract.decode(b"[[[[]]]]")


def test_policy_and_request_returns_are_detached(image):
    seed(image)
    detached = contract.validate_request(image["request"], policy=image["policy"])
    detached["owner"]["library_id"] = str(uuid.uuid4())
    assert detached["owner"] != image["request"]["owner"]
    assert proof(image).status == "proven"


def test_unowned_receipt_rejects_a_hidden_physical_delete_row(image):
    seed(image, disposition="preserve_unowned")
    file = image["file"]
    insert(image["con"], "storage_artifact", {**{key: value for key, value in file.items() if key != "intent"},
                                              "created_at": STAMP, "updated_at": STAMP})
    insert(image["con"], "storage_gc_item", {"operation_id": image["operation_id"],
           **{key: file[key] for key in contract._ITEM_IDENTITY}, "state": "pending", "deleted_bytes": 0,
           "reason_code": None, "updated_at": STAMP})
    image["con"].commit()
    assert proof(image).status == "unproven"


def test_query_limit_failure_is_unproven_not_a_fake_empty_root(image, monkeypatch):
    seed(image)
    monkeypatch.setattr(contract, "MAX_QUERY_STEPS", 0)
    monkeypatch.setattr(contract, "MAX_QUERY_SECONDS", -1)
    assert proof(image).status == "unproven"
