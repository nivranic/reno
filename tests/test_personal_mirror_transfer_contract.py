"""Personal mirror fact/DDL contracts only, never transfer or installation.

Snapshots are actual committed captures from isolated synthetic roots. These
tests neither enable cloud reading nor publish a mirror, promote a primary,
send HTTP, run paid models, or write an installation/mutation receipt.
"""
from copy import deepcopy
import hashlib
import sqlite3

import pytest

from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_contract as wire
from reno import personal_mirror_transfer_schema as schema
from reno import storage_lifecycle as lifecycle
from tests.test_personal_mirror_snapshot import source_env, take, uid, encoded

NOW = "2026-10-03T00:00:00Z"


@pytest.fixture
def prepared(source_env):
    captured = take(source_env).snapshot
    return {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": uid(301),
        "snapshot_id": captured.snapshot_id, "snapshot_sha256": captured.snapshot_sha256,
        "snapshot_json": captured.snapshot_json,
        "source": {"library_id": source_env["library"], "node_id": source_env["primary"],
            "personal_primary_id": source_env["primary"], "primary_epoch": 1},
        "receiver": {"library_id": uid(401), "node_id": uid(402)},
        "previous": None, "expected_revision": 0}


def commit(value):
    return {"format_version": 1, "kind": "personal_mirror_commit", "operation_id": value["operation_id"],
        "prepare_sha256": wire.prepare_sha256(value)}


def attachment(value):
    return {**commit(value), "kind": "personal_mirror_attachment",
        "attachment": wire.attachment_manifest(value)[0]}


def receipt(value):
    snapshot = facts.validate_snapshot(value["snapshot_json"])
    result = {"format_version": 1, "kind": "personal_mirror_receipt", "operation_id": value["operation_id"],
        "prepare_sha256": wire.prepare_sha256(value), "snapshot_id": value["snapshot_id"],
        "snapshot_sha256": value["snapshot_sha256"], "source": deepcopy(value["source"]),
        "receiver": deepcopy(value["receiver"]), "installed_revision": value["expected_revision"] + 1,
        "counts": dict(snapshot.counts), "attachment_manifest_sha256": wire.attachment_manifest_sha256(value),
        "committed_at": NOW, "receipt_sha256": "0" * 64, "replayed": False}
    result["receipt_sha256"] = wire.receipt_sha256(result)
    return result


def changed_snapshot(value, change):
    result = deepcopy(value)
    document = facts.validate_snapshot(result["snapshot_json"]).document
    change(document)
    result["snapshot_json"] = encoded(document)
    result["snapshot_sha256"] = hashlib.sha256(result["snapshot_json"].encode("utf-8")).hexdigest()
    result["snapshot_id"] = document["snapshot_id"]
    return result


def minimal_snapshot(document):
    for table in document["tables"]:
        if table == "personal_revision":
            document["tables"][table] = [row for row in document["tables"][table] if row["entity_kind"] == "project"]
        elif table != "renovation_project":
            document["tables"][table] = []
    document["attachment_manifest"] = []
    document["counts"] = {name: len(rows) for name, rows in document["tables"].items()}
    document["counts"].update(meta=4, attachment_manifest=0)


def test_prepare_keeps_original_document_cells_manifest_and_separate_identities(prepared):
    original = deepcopy(prepared)
    normalized = wire.normalize_prepare(prepared)
    assert normalized == original and prepared == original
    assert normalized is not prepared and normalized["source"] is not prepared["source"]
    assert normalized["snapshot_json"].encode("utf-8") == prepared["snapshot_json"].encode("utf-8")
    document = facts.validate_snapshot(normalized["snapshot_json"]).document
    assert wire.attachment_manifest(normalized) == document["attachment_manifest"]
    assert len(document["tables"]) == 10
    assert document["meta"][1]["value"] == prepared["source"]["library_id"]
    assert document["meta"][1]["value"] != prepared["receiver"]["library_id"]
    assert len(document["tables"]["source_attachment"]) == 2
    assert len(document["attachment_manifest"]) == 1  # staging row stays a fact
    assert "instance_id" not in normalized and "personal_write" not in normalized


@pytest.mark.parametrize("change", [
    lambda value: value.update(format_version=True),
    lambda value: value.update(expected_revision=False),
    lambda value: value.update(kind="knowledge_snapshot"),
    lambda value: value.update(extra="private"),
    lambda value: value.update(snapshot_sha256="a" * 64),
    lambda value: value.update(snapshot_id=uid(999)),
    lambda value: value["source"].update(primary_epoch=True),
    lambda value: value["source"].update(primary_epoch=2),
    lambda value: value["source"].update(node_id=uid(998)),
    lambda value: value["receiver"].update(node_id=value["source"]["node_id"]),
    lambda value: value["receiver"].update(library_id=value["source"]["library_id"]),
    lambda value: value.update(expected_revision=1),
    lambda value: value.update(previous={"snapshot_id": uid(500), "snapshot_sha256": "a" * 64}),
])
def test_prepare_rejects_incomplete_binding_versions_boolean_numbers_and_snapshot_drift(prepared, change):
    value = deepcopy(prepared)
    change(value)
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_prepare(value)


def test_same_operation_cannot_change_receiver_even_when_snapshot_is_unchanged(prepared):
    assert wire.require_same_prepare(prepared, deepcopy(prepared)) == prepared
    changed = deepcopy(prepared)
    changed["receiver"]["node_id"] = uid(900)
    with pytest.raises(wire.PersonalMirrorTransferError, match="IDEMPOTENCY_KEY_REUSED"):
        wire.require_same_prepare(prepared, changed)


def test_prior_is_the_exact_prepared_predecessor_and_keeps_immutable_history(prepared):
    value = changed_snapshot(prepared, lambda document: document.update(snapshot_id=uid(202)))
    value.update(operation_id=uid(302), expected_revision=1,
        previous={"snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"]})
    assert wire.normalize_prepare(value, prior_snapshot_json=prepared["snapshot_json"]) == value
    bad = deepcopy(value)
    bad["previous"]["snapshot_sha256"] = "a" * 64
    with pytest.raises(wire.PersonalMirrorTransferError, match="PREVIOUS_SNAPSHOT_MISMATCH"):
        wire.normalize_prepare(bad, prior_snapshot_json=prepared["snapshot_json"])


def test_zero_ready_attachments_still_has_a_parent_preparation(prepared):
    value = changed_snapshot(prepared, minimal_snapshot)
    assert wire.normalize_prepare(value) == value
    assert wire.attachment_manifest(value) == []
    assert wire.normalize_commit(commit(value), prepare=value) == commit(value)
    assert wire.normalize_receipt(value, receipt(value))["counts"]["attachment_manifest"] == 0


def test_attachment_retains_original_identity_and_requires_prepared_ready_membership(prepared):
    value = attachment(prepared)
    assert wire.normalize_attachment(value, prepare=prepared) == value
    changed = deepcopy(value)
    changed["attachment"]["file_identity"]["inode"] = "99999"
    with pytest.raises(wire.PersonalMirrorTransferError, match="ATTACHMENT_NOT_PREPARED"):
        wire.normalize_attachment(changed, prepare=prepared)
    changed = deepcopy(value)
    changed["attachment"]["attachment_id"] = uid(9)  # actual staging row
    with pytest.raises(wire.PersonalMirrorTransferError, match="ATTACHMENT_NOT_PREPARED"):
        wire.normalize_attachment(changed, prepare=prepared)


@pytest.mark.parametrize("change", [
    lambda value: value.update(prepare_sha256="a" * 64),
    lambda value: value.update(operation_id=uid(404)),
    lambda value: value["attachment"].update(byte_size=True),
    lambda value: value["attachment"].update(storage_key="../private"),
    lambda value: value["attachment"].pop("file_identity"),
])
def test_attachment_rejects_changed_parent_path_or_manifest_shape(prepared, change):
    value = attachment(prepared)
    change(value)
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_attachment(value, prepare=prepared)


def test_commit_selector_is_exact_and_does_not_claim_installation(prepared):
    value = commit(prepared)
    assert wire.normalize_commit(value, prepare=prepared) == value
    value["files_ready"] = True
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_commit(value, prepare=prepared)


def test_receipt_replay_retains_stable_identity_without_changing_original_facts(prepared, monkeypatch):
    original = receipt(prepared)
    original_prepare = deepcopy(prepared)
    replayed = {**deepcopy(original), "replayed": True}
    assert wire.receipt_sha256(replayed) == original["receipt_sha256"]
    validate = facts.validate_snapshot
    calls = []

    def observe(snapshot_json, **kwargs):
        calls.append(snapshot_json)
        return validate(snapshot_json, **kwargs)

    monkeypatch.setattr(facts, "validate_snapshot", observe)
    normalized = wire.normalize_receipt(prepared, original)
    assert normalized == original
    assert normalized is not original and normalized["source"] is not original["source"]
    assert wire.receipt_sha256(normalized) == original["receipt_sha256"]
    assert calls == [original_prepare["snapshot_json"]]
    assert wire.normalize_receipt(prepared, replayed) == replayed
    assert calls == [original_prepare["snapshot_json"]] * 2
    assert prepared == original_prepare


def test_receipt_legacy_copy_cannot_reuse_facts_from_a_replaced_snapshot(prepared):
    replacement = changed_snapshot(prepared, minimal_snapshot)
    claimed = receipt(replacement)
    original_claim = receipt(prepared)
    claimed["counts"] = original_claim["counts"]
    claimed["attachment_manifest_sha256"] = original_claim["attachment_manifest_sha256"]
    claimed["receipt_sha256"] = wire.receipt_sha256(claimed)
    malicious = dict(prepared)
    copied = []

    class MutatingKind(str):
        def __deepcopy__(self, memo):
            copied.append(True)
            for name in ("snapshot_id", "snapshot_sha256", "snapshot_json"):
                malicious[name] = replacement[name]
            return "personal_mirror_prepare"

    malicious["kind"] = MutatingKind("personal_mirror_prepare")
    with pytest.raises(wire.PersonalMirrorTransferError, match="RECEIPT_BINDING_MISMATCH"):
        wire.normalize_receipt(malicious, claimed)
    assert copied == [True]


def test_receipt_prepare_error_precedes_invalid_receipt_fields(prepared):
    invalid_prepare = {**prepared, "format_version": 2}
    with pytest.raises(wire.PersonalMirrorTransferError, match="VERSION_INVALID"):
        wire.normalize_receipt(invalid_prepare, {})


def test_receipt_copy_miss_does_not_call_custom_type_equality_before_prepare_validation(prepared):
    class CallbackType(type):
        def __eq__(cls, other):
            raise AssertionError("copier invoked caller type equality")

    class Unsupported(metaclass=CallbackType):
        pass

    invalid_prepare = {**prepared, "format_version": 2, "kind": Unsupported()}
    with pytest.raises(wire.PersonalMirrorTransferError, match="VERSION_INVALID"):
        wire.normalize_receipt(invalid_prepare, {})


def test_receipt_binding_error_precedes_boolean_count_error(prepared):
    claimed = receipt(prepared)
    claimed["operation_id"] = uid(404)
    claimed["counts"]["attachment_manifest"] = True
    with pytest.raises(wire.PersonalMirrorTransferError, match="RECEIPT_BINDING_MISMATCH"):
        wire.normalize_receipt(prepared, claimed)


def test_receipt_boolean_count_equal_to_one_is_still_invalid(prepared):
    claimed = receipt(prepared)
    assert claimed["counts"]["attachment_manifest"] == 1
    claimed["counts"]["attachment_manifest"] = True
    with pytest.raises(wire.PersonalMirrorTransferError, match="COUNTS_INVALID"):
        wire.normalize_receipt(prepared, claimed)


@pytest.mark.parametrize("change", [
    lambda value: value.update(installed_revision=True),
    lambda value: value.update(installed_revision=2),
    lambda value: value.update(replayed=1),
    lambda value: value.update(snapshot_sha256="a" * 64),
    lambda value: value["receiver"].update(node_id=uid(999)),
    lambda value: value["counts"].update(meta=True),
    lambda value: value.update(attachment_manifest_sha256="a" * 64),
    lambda value: value.update(committed_at="2026-02-30T00:00:00Z"),
    lambda value: value.update(receipt_sha256="a" * 64),
])
def test_receipt_rejects_partial_counters_changed_commit_or_foreign_receipt(prepared, change):
    value = receipt(prepared)
    change(value)
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_receipt(prepared, value)


def test_oversized_decimal_identity_has_a_controlled_domain_error(prepared):
    value = attachment(prepared)
    value["attachment"]["file_identity"]["inode"] = "1" * 5000
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_attachment(value, prepare=prepared)
    changed = changed_snapshot(prepared, lambda document:
        document["attachment_manifest"][0]["file_identity"].update(inode="1" * 5000))
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.normalize_prepare(changed)


@pytest.mark.parametrize("value", [float("nan"), object()])
def test_receipt_hash_rejects_non_json_values_with_a_controlled_error(prepared, value):
    result = receipt(prepared)
    result["committed_at"] = value
    with pytest.raises(wire.PersonalMirrorTransferError):
        wire.receipt_sha256(result)


@pytest.fixture
def receiver(tmp_path):
    root = tmp_path / "receiver"
    data = root / "data"
    data.mkdir(parents=True)
    profile = lifecycle.StorageProfile(data / "reno.db", root / "media", root / "reports", root / "checkpoints")
    con = sqlite3.connect(profile.database)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    con.executemany("INSERT INTO meta VALUES(?,?)", [("library_id", uid(401)), ("migration_version", "16"), ("sentinel", "unchanged")])
    con.execute("CREATE TABLE mutation_receipt(operation_id TEXT PRIMARY KEY, original TEXT)")
    con.execute("INSERT INTO mutation_receipt VALUES(?,?)", (uid(99), "original user receipt"))
    con.commit()
    lifecycle.initialize(profile)
    try:
        yield con, profile
    finally:
        con.close()


def test_schema_read_never_installs_or_changes_receiver_meta_and_original_receipts(receiver):
    con, _ = receiver
    before = [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")]
    with pytest.raises(schema.PersonalMirrorSchemaError, match="INCOMPATIBLE"):
        schema.validate(con)
    assert not schema._objects(con, "main.sqlite_master")
    assert [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")] == before
    assert con.execute("SELECT original FROM mutation_receipt").fetchone()[0] == "original user receipt"


def test_schema_install_requires_outer_transaction_and_actual_profile_guard(receiver):
    con, profile = receiver
    with lifecycle.shared(profile), pytest.raises(schema.PersonalMirrorSchemaError, match="OUTER_TRANSACTION_REQUIRED"):
        schema.install(con, profile=profile)
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(lifecycle.StorageLifecycleError):
        schema.install(con, profile=profile)
    con.rollback()
    assert not schema._objects(con, "main.sqlite_master")


def test_schema_install_is_additive_uncommitted_and_exact_replay_is_noop(receiver):
    con, profile = receiver
    before = [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")]
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        assert schema.install(con, profile=profile) is True
        assert con.in_transaction
        assert all(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in schema.TABLES)
        assert schema.install(con, profile=profile) is False
        schema.validate(con)
        assert [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")] == before
        assert con.execute("SELECT original FROM mutation_receipt").fetchone()[0] == "original user receipt"
        con.rollback()
    assert not schema._objects(con, "main.sqlite_master")


@pytest.mark.parametrize("statement", [
    "CREATE TABLE personal_mirror_transfer_unknown(value TEXT)",
    f"CREATE TABLE {schema.INTENT}(operation_id TEXT PRIMARY KEY)",
    f"CREATE TEMP TABLE {schema.INTENT}(operation_id TEXT PRIMARY KEY)",
])
def test_schema_never_adopts_or_repairs_partial_foreign_or_temp_facilities(receiver, statement):
    con, profile = receiver
    con.execute(statement)
    before = [tuple(row) for row in con.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")]
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(schema.PersonalMirrorSchemaError):
            schema.install(con, profile=profile)
        assert [tuple(row) for row in con.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")] == before
        con.rollback()


def test_failed_install_rolls_back_only_its_ddl_and_retains_outer_transaction(receiver, monkeypatch):
    con, profile = receiver
    failure = RuntimeError("synthetic interrupted DDL")
    def partial(connection):
        connection.execute(f"CREATE TABLE {schema.INTENT} ({schema.TABLES[schema.INTENT]})")
        raise failure
    monkeypatch.setattr(schema, "_install_reference", partial)
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE meta SET value='outer change' WHERE key='sentinel'")
        with pytest.raises(RuntimeError) as caught:
            schema.install(con, profile=profile)
        assert caught.value is failure and con.in_transaction
        assert not schema._objects(con, "main.sqlite_master")
        assert con.execute("SELECT value FROM meta WHERE key='sentinel'").fetchone()[0] == "outer change"
        con.rollback()


def test_schema_exact_validator_detects_removed_receipt_immutability(receiver):
    con, profile = receiver
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        con.execute("DROP TRIGGER trg_personal_mirror_receipt_update")
        with pytest.raises(schema.PersonalMirrorSchemaError, match="INCOMPATIBLE"):
            schema.validate(con)
        con.rollback()


@pytest.mark.parametrize("statement", [
    f"CREATE TEMP TRIGGER unrelated_name BEFORE INSERT ON main.{schema.INTENT.upper()} BEGIN SELECT 1; END",
    f"CREATE TRIGGER unrelated_name BEFORE INSERT ON {schema.INTENT.upper()} BEGIN SELECT 1; END",
    f"CREATE INDEX unrelated_name ON {schema.INTENT.upper()}(state)",
])
def test_case_insensitive_attached_objects_cannot_bypass_exact_schema(receiver, statement):
    con, profile = receiver
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        con.execute(statement)
        with pytest.raises(schema.PersonalMirrorSchemaError):
            schema.validate(con)
        with pytest.raises(schema.PersonalMirrorSchemaError):
            schema.install(con, profile=profile)
        con.rollback()


def insert_record(con, table, mapping, *, replacing=False):
    # Synthetic SQL contract records only; no files or accepted mirror exist.
    prefix = "INSERT OR REPLACE" if replacing else "INSERT"
    con.execute(f"{prefix} INTO {table}({','.join(mapping)}) VALUES({','.join('?' for _ in mapping)})",
                tuple(mapping.values()))


def sql_records(prepared):
    answered = receipt(prepared)
    manifest = wire.attachment_manifest(prepared)[0]
    intent = {"operation_id": prepared["operation_id"], "format_version": 1,
        "prepare_json": encoded(prepared), "prepare_sha256": wire.prepare_sha256(prepared),
        "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
        "source_library_id": prepared["source"]["library_id"], "source_node_id": prepared["source"]["node_id"],
        "source_primary_id": prepared["source"]["personal_primary_id"],
        "source_primary_epoch": prepared["source"]["primary_epoch"],
        "receiver_library_id": prepared["receiver"]["library_id"], "receiver_node_id": prepared["receiver"]["node_id"],
        "previous_snapshot_id": None, "previous_snapshot_sha256": None, "expected_revision": 0,
        "state": "prepared", "created_at": NOW, "updated_at": NOW}
    body = {"operation_id": prepared["operation_id"], "attachment_id": manifest["attachment_id"],
        "storage_key": manifest["storage_key"], "sha256": manifest["sha256"], "byte_size": manifest["byte_size"],
        "descriptor_json": encoded(manifest), "descriptor_sha256": hashlib.sha256(encoded(manifest).encode()).hexdigest(),
        "state": "reserved", "local_file_device": None, "local_file_inode": None,
        "local_file_mtime_ns": None, "local_file_ctime_ns": None, "stored_at": None}
    saved = {"operation_id": prepared["operation_id"], "format_version": 1,
        "prepare_sha256": wire.prepare_sha256(prepared), "snapshot_id": prepared["snapshot_id"],
        "snapshot_sha256": prepared["snapshot_sha256"], "installed_revision": 1,
        "receipt_json": encoded(answered), "receipt_sha256": answered["receipt_sha256"], "committed_at": NOW}
    head = {"slot": 1, "format_version": 1, "operation_id": prepared["operation_id"],
        **{key: intent[key] for key in ("source_library_id", "source_node_id", "source_primary_id",
            "source_primary_epoch", "receiver_library_id", "receiver_node_id")},
        "revision": 1, "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
        "receipt_sha256": answered["receipt_sha256"], "updated_at": NOW}
    return {schema.INTENT: intent, schema.ATTACHMENT: body, schema.RECEIPT: saved, schema.HEAD: head}


@pytest.mark.parametrize("table", [schema.INTENT, schema.ATTACHMENT, schema.RECEIPT, schema.HEAD])
def test_replace_cannot_erase_original_operational_record_with_recursive_triggers_off(receiver, prepared, table):
    con, profile = receiver
    con.execute("PRAGMA recursive_triggers=OFF")
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        records = sql_records(prepared)
        for name, mapping in records.items():
            insert_record(con, name, mapping)
        before = [tuple(row) for row in con.execute(f"SELECT * FROM {table}")]
        changed = deepcopy(records[table])
        key = {schema.INTENT: "prepare_json", schema.ATTACHMENT: "sha256",
            schema.RECEIPT: "receipt_json", schema.HEAD: "updated_at"}[table]
        changed[key] = "different original content"
        with pytest.raises(sqlite3.IntegrityError, match="KEY_REUSED"):
            insert_record(con, table, changed, replacing=True)
        assert [tuple(row) for row in con.execute(f"SELECT * FROM {table}")] == before
        con.rollback()


@pytest.mark.parametrize("table,change", [
    (schema.INTENT, {"operation_id": uid(800)}),
    (schema.ATTACHMENT, {"attachment_id": uid(800)}),
    (schema.RECEIPT, {"operation_id": uid(800)}),
])
def test_replace_cannot_use_alternate_unique_key_to_delete_original_row(receiver, prepared, table, change):
    con, profile = receiver
    con.execute("PRAGMA recursive_triggers=OFF")
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        records = sql_records(prepared)
        for name, mapping in records.items():
            insert_record(con, name, mapping)
        before = [tuple(row) for row in con.execute(f"SELECT * FROM {table}")]
        with pytest.raises(sqlite3.IntegrityError, match="KEY_REUSED"):
            insert_record(con, table, {**records[table], **change}, replacing=True)
        assert [tuple(row) for row in con.execute(f"SELECT * FROM {table}")] == before
        con.rollback()


def test_head_foreign_keys_bind_receipt_revision_and_actual_parent_scope(receiver, prepared):
    con, profile = receiver
    with lifecycle.shared(profile):
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        records = sql_records(prepared)
        for name in (schema.INTENT, schema.ATTACHMENT, schema.RECEIPT):
            insert_record(con, name, records[name])
        changed = {**records[schema.HEAD], "source_node_id": uid(850), "source_primary_id": uid(850)}
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_record(con, schema.HEAD, changed)
        changed = {**records[schema.HEAD], "revision": 2}
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_record(con, schema.HEAD, changed)
        assert con.execute(f"SELECT count(*) FROM {schema.HEAD}").fetchone()[0] == 0
        con.rollback()
