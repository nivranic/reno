"""Closed row facts, actual legacy receipts and actual isolated17 captures."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import uuid

import pytest

from reno import home_recovery_checkpoint as checkpoint, home_recovery_schema as home
from reno import home_preservation_contract17 as contract, storage_admin
from tests.test_home_recovery_home import env as restore_env, state, changed, apply
from tests.test_source_preservation_v17 import historical17_paid_environment as capture_env
from tests.test_source_preservation_certificate17 import captured
from tests.test_source_preservation_witness import install_core


IDS = ["00000000-0000-4000-8000-" + str(n).zfill(12) for n in range(1, 12)]
SHA = "a" * 64
CORE = "c" * 64


def digest(value):
    return hashlib.sha256(home.canonical_json(value).encode("utf-8")).hexdigest()


def set_json(row, name, value):
    row[name + "_json"] = home.canonical_json(value)
    row[name + "_sha256"] = digest(value)


def capsule(kind, library, node):
    value = {"format_version": 1, "kind": kind, "presence": "absent",
        "absence_sha256": digest({"format_version": 1, "kind": kind, "presence": "absent"}),
        "owner_profile_sha256": SHA, "identity": None, "contract_sha256": None,
        "head_ordinal": 0, "head_sha256": None, "journal_sha256": None,
        "record_set_sha256": None, "reference_set_sha256": digest([]),
        "record_count": 0, "journal_count": 0}
    if kind == "home_authority":
        value["binding"] = {"home_library_id": library, "home_node_id": node,
            "target_library_id": IDS[8], "target_node_id": IDS[9]}
    return value


def record(*, version=None, wrapper=False, core=False):
    value = {name: SHA for name in home.COLUMNS if name.endswith("sha256")}
    value.update(dict(zip(("operation_id", "library_id", "node_id", "source_id", "protection_backup_id"), IDS)))
    value.update(format_version=1, action="knowledge_restore", source_kind="home_checkpoint",
        before_library_epoch=3, before_kb_revision=12, after_library_epoch=4, after_kb_revision=17,
        original_core_sha256=CORE if core else None, root_job_id=7 if core else None,
        continuation_id=IDS[5] if core else None, execution_generation=3 if core else None,
        committed_at="2026-10-02T01:02:03.123456Z")
    if wrapper:
        value["protection_backup_id"] = str(uuid.uuid5(uuid.UUID(value["operation_id"]),
            "reno.home.preservation.backup.v1"))
    before, after = ({"library_id": value["library_id"],
        "library_epoch": value[prefix + "_library_epoch"], "kb_revision": value[prefix + "_kb_revision"]}
        for prefix in ("before", "after"))
    frontier = {"root_job_id": value["root_job_id"], "root_job_row_sha256": SHA if core else None,
                "operations": []}
    inner = {"state_present": version is not None,
        "absence_sha256": digest({"format_version": 1, "state": "absent"}) if version is None else None,
        "state_sha256": SHA if version is not None else None, "state_bytes": 24 if version is not None else 0,
        "canonical_state_sha256": SHA if version is not None else None, "state_format_version": version,
        "source_execution_sha256": value["original_core_sha256"],
        "source_execution_lineage": {name: value[name] for name in (
            "root_job_id", "continuation_id", "execution_generation")} if core else None, "outboxes": []}
    metadata = inner
    if wrapper:
        authority = {"format_version": 1, "state_present": inner["state_present"],
            "source_core_sha256": value["original_core_sha256"], "source_authority": None,
            "source_admission": None, "source_recovery_pending": None}
        authority["authority_ref_set_sha256"] = digest({"core_sha256": authority["source_core_sha256"],
            **{name: authority[name] for name in ("source_authority", "source_admission", "source_recovery_pending")}})
        metadata = {"format_version": 2, "checkpoint": inner,
            "certificate": {"format_version": 2, "certificate_sha256": "b" * 64,
                "byte_size": 2048, "preserved_sha256": value["before_operational_sha256"],
                "frontier_sha256": digest(frontier)},
            "bundle": {"format_version": 1, "bundle_id": str(uuid.uuid5(uuid.UUID(value["operation_id"]),
                "reno.home.preservation.bundle.v1")), "manifest_sha256": SHA, "file_set_sha256": SHA},
            "state_authority": authority, "permanent_witnesses": {
                kind: capsule(kind, value["library_id"], value["node_id"])
                for kind in ("cloud_continuity", "home_authority")}}
    for name, body in {"before_version": before, "after_version": after, "frontier": frontier,
            "checkpoint": metadata, "request": {"action": "knowledge_restore", "source_id": value["source_id"],
                "expected_digest": SHA}, "result": {"format_version": 1, "action": "knowledge_restore",
                "operation_id": value["operation_id"], "source_id": value["source_id"],
                "before_version": before, "after_version": after, "knowledge_sha256": SHA,
                "protection_backup_id": value["protection_backup_id"]}}.items():
        set_json(value, name, body)
    return value


def modify(row, name, mutation):
    body = json.loads(row[name + "_json"])
    mutation(body)
    set_json(row, name, body)


@pytest.mark.parametrize("schema_version", [1, 15, 16, 17])
@pytest.mark.parametrize("version", [None, 1, 2, 3, 4])
def test_original_bare_checkpoint_keeps_bytes_and_old_interpretation(schema_version, version):
    row = record(version=version, core=version in {3, 4})
    original = deepcopy(row)
    facts = checkpoint.validate(row, schema_version=schema_version)
    assert facts.checkpoint == json.loads(row["checkpoint_json"])
    assert facts.wrapper is None and facts.certificate_sha256 is None
    assert row == original
    assert home.canonical_json(facts.checkpoint) == row["checkpoint_json"]


@pytest.mark.parametrize("version", [None, 1, 2, 3, 4, 5])
def test_only17_wrapper_returns_detached_closed_facts(version):
    row = record(version=version, wrapper=True, core=version in {3, 4, 5})
    facts = checkpoint.validate(row, schema_version=17)
    assert facts.wrapper == json.loads(row["checkpoint_json"])
    assert facts.checkpoint == facts.wrapper["checkpoint"]
    assert facts.certificate_sha256 == "b" * 64
    original = deepcopy(row)
    facts.wrapper["checkpoint"]["outboxes"].append({"caller": "change"})
    assert facts.checkpoint["outboxes"] == [] and row == original
    with pytest.raises(FrozenInstanceError):
        facts.certificate_sha256 = SHA
    assert not any(hasattr(facts, name) for name in ("owner", "admit", "dispatch", "verify_source", "ticket"))


@pytest.mark.parametrize("schema_version", [True, False, 0, -1, 18, "17", "16", 17.0, None, {}])
def test_schema_gate_rejects_coercions_and_future_versions(schema_version):
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError, match="SCHEMA_UNSUPPORTED"):
        checkpoint.validate(record(), schema_version=schema_version)


@pytest.mark.parametrize("schema_version", [1, 15, 16])
def test_wrapper2_cannot_reinterpret_an_old_image(schema_version):
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(record(version=4, wrapper=True, core=True), schema_version=schema_version)


@pytest.mark.parametrize("schema_version", [16, 17])
def test_bare_state5_never_bypasses_wrapper_provenance_shape(schema_version):
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(record(version=5, core=True), schema_version=schema_version)


@pytest.mark.parametrize("name,mutation", [
    ("request", lambda body: body.update(action="different")),
    ("request", lambda body: body.update(source_id=IDS[10])),
    ("request", lambda body: body.update(operation_id=IDS[0])),
    ("request", lambda body: body.update(expected_digest=True)),
    ("result", lambda body: body.update(format_version=True)),
    ("result", lambda body: body.update(action="different")),
    ("result", lambda body: body.update(operation_id=IDS[10])),
    ("result", lambda body: body.update(source_id=IDS[10])),
    ("result", lambda body: body.update(protection_backup_id=IDS[10])),
    ("result", lambda body: body.update(knowledge_sha256=0)),
    ("result", lambda body: body.update(extra="value")),
    ("result", lambda body: body["before_version"].update(kb_revision=13)),
    ("result", lambda body: body["after_version"].update(library_epoch=5)),
])
def test_business_mismatch_is_rejected_even_after_rehash_with_absent_state(name, mutation):
    row = record(wrapper=True)
    modify(row, name, mutation)
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=17)


def test_result_boolean_version_numbers_cannot_compare_equal_to_integer_columns():
    row = record()
    row.update(before_library_epoch=1, after_library_epoch=2, before_kb_revision=0, after_kb_revision=1)
    for prefix in ("before", "after"):
        set_json(row, prefix + "_version", {"library_id": row["library_id"],
            "library_epoch": row[prefix + "_library_epoch"], "kb_revision": row[prefix + "_kb_revision"]})
    result = json.loads(row["result_json"])
    result.update(before_version=json.loads(row["before_version_json"]), after_version=json.loads(row["after_version_json"]))
    result["before_version"]["library_epoch"] = True
    result["after_version"]["kb_revision"] = True
    set_json(row, "result", result)
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=16)


@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize("key,replacement", [("root_job_id", 8), ("continuation_id", IDS[10]),
                                           ("execution_generation", 4)])
def test_inner_original_lineage_must_match_all_row_columns(wrapper, key, replacement):
    row = record(version=4, wrapper=wrapper, core=True)
    def mutation(body):
        inner = body["checkpoint"] if wrapper else body
        inner["source_execution_lineage"][key] = replacement
    modify(row, "checkpoint", mutation)
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=17)


@pytest.mark.parametrize("mutation", [
    lambda body: body["certificate"].update(preserved_sha256="e" * 64),
    lambda body: body["certificate"].update(frontier_sha256="e" * 64),
    lambda body: body["bundle"].update(bundle_id=IDS[10]),
    lambda body: body["checkpoint"].update(source_execution_sha256="e" * 64),
    lambda body: body["permanent_witnesses"]["home_authority"]["binding"].update(home_library_id=IDS[10]),
    lambda body: body["permanent_witnesses"]["home_authority"]["binding"].update(home_node_id=IDS[10]),
    lambda body: body.update(format_version=True),
    lambda body: body.update(extra="value"),
])
def test_wrapper_cross_fields_are_not_proved_by_recomputed_whole_wrapper_hash(mutation):
    row = record(version=5, wrapper=True, core=True)
    modify(row, "checkpoint", mutation)
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=17)


def test_wrapper_requires_original_new_protection_uuid_not_old_domain():
    row = record(version=4, wrapper=True, core=True)
    row["protection_backup_id"] = str(uuid.uuid5(uuid.UUID(row["operation_id"]),
        "reno.home.knowledge.restore.protection.v1"))
    modify(row, "result", lambda body: body.update(protection_backup_id=row["protection_backup_id"]))
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=17)


@pytest.mark.parametrize("name", ["checkpoint", "request", "result", "frontier", "before_version", "after_version"])
def test_all_original_row_hashes_are_checked(name):
    row = record()
    row[name + "_sha256"] = "f" * 64
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=16)


@pytest.mark.parametrize("mutation", [
    lambda body: body.update(root_job_id=8),
    lambda body: body.update(root_job_row_sha256=None),
    lambda body: body.update(operations=[{"unknown": "shape"}]),
    lambda body: body.update(extra="value"),
])
def test_frontier_cannot_disagree_with_original_root_or_closed_shape(mutation):
    row = record(version=4, core=True)
    modify(row, "frontier", mutation)
    home.validate_record(row)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=16)


def test_complete35_columns_and_whole_wrapper_limit_remain_enforced():
    row = record(wrapper=True)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate({**row, "caller_claim": SHA}, schema_version=17)
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate({name: value for name, value in row.items() if name != "node_id"}, schema_version=17)
    modify(row, "checkpoint", lambda body: body.update(unrecognized="x" * home.MAX_CHECKPOINT_BYTES))
    with pytest.raises(checkpoint.HomeRecoveryCheckpointError):
        checkpoint.validate(row, schema_version=17)


@pytest.mark.parametrize("version", [None, 1, 2, 3, 4])
def test_actual_legacy_restore_receipt_remains_exactly_unchanged(restore_env, version):
    if version is not None:
        store = state(restore_env)
        if version > 1:
            getattr(store, {2: "enable_no_resend", 3: "enable_source_execution", 4: "enable_source_control"}[version])()
    changed(restore_env)
    identifier, _ = apply(restore_env)
    with storage_admin.connection(restore_env[0]) as con:
        row = dict(con.execute('SELECT * FROM ' + home.TABLE + ' WHERE operation_id=?', (identifier,)).fetchone())
    original = deepcopy(row)
    for schema_version in (16, 17):
        facts = checkpoint.validate(row, schema_version=schema_version)
        assert facts.checkpoint["state_format_version"] == version
        assert facts.wrapper is None and row == original


def test_actual17_complete_certificate_and_multigeneration_frontier_match_row_facts(capture_env):
    # Paid/unknown and multigeneration facts were produced before17.
    _paid, _unknown = capture_env.historical_paid_unknown
    install_core(capture_env)
    with captured(capture_env) as (_con, _witness, before):
        document = before.public()
        row = record(version=5, wrapper=True, core=True)
        row.update(library_id=document["library_id"], node_id=document["node_id"],
            before_library_epoch=document["knowledge_version"]["library_epoch"],
            before_kb_revision=document["knowledge_version"]["kb_revision"],
            after_library_epoch=document["knowledge_version"]["library_epoch"] + 1,
            after_kb_revision=document["knowledge_version"]["kb_revision"] + 1,
            original_core_sha256=document["checkpoint"]["source_execution_sha256"],
            **document["checkpoint"]["source_execution_lineage"],
            before_operational_sha256=document["preserved_sha256"], after_operational_sha256=document["preserved_sha256"])
        wrapper = json.loads(row["checkpoint_json"])
        wrapper.update(checkpoint=document["checkpoint"], state_authority=document["state_authority"],
            permanent_witnesses=document["permanent_witnesses"])
        wrapper["certificate"].update(certificate_sha256=before.sha256,
            byte_size=len(before.document_json.encode("utf-8")), preserved_sha256=document["preserved_sha256"],
            frontier_sha256=digest(document["frontier"]))
        before_version = document["knowledge_version"]
        after_version = {"library_id": row["library_id"], "library_epoch": row["after_library_epoch"],
                         "kb_revision": row["after_kb_revision"]}
        result = json.loads(row["result_json"])
        result.update(before_version=before_version, after_version=after_version)
        for name, body in {"before_version": before_version, "after_version": after_version,
                "frontier": document["frontier"], "checkpoint": wrapper, "result": result}.items():
            set_json(row, name, body)
        facts = checkpoint.validate(row, schema_version=17)
        assert facts.certificate_sha256 == before.sha256
        assert len(document["frontier"]["operations"]) > 1
        assert facts.checkpoint == document["checkpoint"]
        assert contract.normalize_checkpoint(facts.wrapper) == wrapper
