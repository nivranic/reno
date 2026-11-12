"""Only original, complete normal source failures may close a Home execution."""
from dataclasses import replace
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import budget, source_contract, source_resolution as adapter
from reno import source_resolution_budget as resolver, source_resolution_contract as codec
from reno import source_terminal
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.test_source_resolution_budget import bound, network, LEAF, STAMP  # noqa: F401
from tests.conftest import released16_env as env


def logical_sha(binding, kind, metadata=None):
    value = {"kind": kind, "source": binding.submitted_url, "runner_contract": "yt_dlp_v1"}
    if metadata is not None:
        value["genesis_metadata_sha256"] = hashlib.sha256(metadata.encode()).hexdigest()
    return hashlib.sha256(source_contract.canonical_json(value).encode()).hexdigest()


def genesis(binding):
    plan = source_contract.SourcePlan("source_metadata", LEAF)
    operation = binding.operation("source_metadata", "ingest:metadata:v1", logical_sha(binding, "source_metadata"))
    if budget.get_operation(operation, profile=binding.profile) is not None:
        return source_contract.canonical_json({"id": "BVfake123"})
    attempted = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, source_contract_version=1), profile=binding.profile)
    assert budget.mark_dispatching(attempted.attempt_id, attempted.reservation_sha256, str(uuid.uuid4()),
        expected_revision=attempted.revision, profile=binding.profile).won
    info = source_contract.canonical_json({"id": "BVfake123"})
    result = source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited", metadata_json=info)
    budget.settle(attempted.attempt_id, plan.request_sha256, outcome="source_step_complete",
        result_text=source_contract.encode_envelope(plan, result, input_sha256=operation.input_sha256), profile=binding.profile)
    return info


def failed_source(binding, *, kind="source_metadata", segment=None, outcome="terminal_error", complete=True,
                  failure_class="process_failure", foreign=False):
    plan = source_contract.SourcePlan(kind, LEAF, segment=segment,
        info_json=source_contract.canonical_json({"id": "BVfake123"}) if kind == "download" else None,
        artifact_relpath="originals/BVfake123/test.mp4" if kind == "download" else None)
    original = genesis(binding) if kind == "download" else None
    operation = binding.operation(kind, "ingest:media:v1:BVfake123" if kind == "download" else "ingest:metadata:v1",
                                  logical_sha(binding, kind, original))
    if foreign:
        operation = replace(operation, suboperation_key="home:" + "e" * 64 + ":metadata:v1")
    reserved = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, source_contract_version=1,
        download_segment=segment), profile=binding.profile)
    assert budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256, str(uuid.uuid4()),
        expected_revision=reserved.revision, profile=binding.profile).won
    if complete:
        result = source_contract.SourceProcessResult(plan.request_sha256, True, True,
            0 if failure_class == "artifact_invalid" else 1, "exited", failure_class=failure_class)
        budget.settle(reserved.attempt_id, plan.request_sha256, outcome=outcome,
            result_text=source_contract.encode_envelope(plan, result, input_sha256=operation.input_sha256),
            profile=binding.profile)
    else:
        budget.settle(reserved.attempt_id, plan.request_sha256, response_complete=False, profile=binding.profile)
    return operation, reserved, plan


def test_complete_original_metadata_failure_produces_closed_private_free_proof(bound):
    operation, attempted, _ = failed_source(bound)
    proof = source_terminal.known_failure(bound)
    public = proof.public()
    assert public["format_version"] == 1 and public["operation_kind"] == "source_metadata"
    assert public["attempt_id"] == attempted.attempt_id and public["operation_id"] == attempted.operation_id
    assert proof.core_sha256 == hashlib.sha256(bound.core_json.encode()).hexdigest()
    assert public["error_class"] == "source_unavailable"
    assert public["source_contract_version"] == 1 and public["resolver_contract_version"] is None
    assert bound.submitted_url not in json.dumps(public) and LEAF not in json.dumps(public)
    public["attempt_id"] = str(uuid.uuid4())
    assert proof.public()["attempt_id"] == attempted.attempt_id
    assert source_terminal.revalidate_failure(bound, proof) == proof
    assert budget.get_operation(operation, profile=bound.profile).latest_ordinal == 1


def test_complete_dns_rejection_has_original_resolver_settlement_proof(bound, monkeypatch):
    def rejected(prepared, **kwargs):
        return codec.ResolutionResult(prepared.plan.request_sha256, "dns", True, "completed",
            dns_error="non_public", failure_class="dns_rejected", observed_at=STAMP)
    monkeypatch.setattr(adapter, "run", rejected)
    with pytest.raises(SyncError):
        resolver.resolve_url(bound)
    proof = source_terminal.known_failure(bound)
    assert proof.public()["operation_kind"] == "source_resolution"
    assert proof.public()["resolver_contract_version"] == 1
    assert proof.public()["error_class"] == "invalid_url"
    assert source_terminal.revalidate_failure(bound, proof) == proof


@pytest.mark.parametrize("outcome", ["source_retry", "full_result"])
def test_retry_or_nonterminal_outcome_does_not_supply_terminal_authority(bound, outcome):
    failed_source(bound, outcome=outcome)
    assert source_terminal.known_failure(bound) is None


def test_any_unknown_original_source_op_blocks_terminal_even_after_known_failure(bound):
    failed_source(bound)
    failed_source(bound, kind="download", segment="video", complete=False)
    with pytest.raises(BudgetDeferred) as caught:
        source_terminal.known_failure(bound)
    assert caught.value.reason == "indeterminate"


@pytest.mark.parametrize("kind", ["source_enumeration", "model"])
def test_unknown_original_namespace_blocks_terminal_across_operation_kinds(bound, kind):
    failed_source(bound)
    operation = bound.operation(kind, "original-unknown", "a" * 64)
    options = {"adapter": "paas_v4", "effective_max_tokens": 1024} if kind == "model" else {"source_contract_version": 1}
    reserved = budget.reserve(operation, budget.AttemptSpec("b" * 64, **options), profile=bound.profile)
    assert budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256, str(uuid.uuid4()),
        expected_revision=reserved.revision, profile=bound.profile).won
    with pytest.raises(BudgetDeferred) as caught:
        source_terminal.known_failure(bound)
    assert caught.value.reason == "indeterminate" and caught.value.operation_id == reserved.operation_id


def test_foreign_namespace_failure_cannot_close_original_core(bound):
    _, attempted, _ = failed_source(bound, foreign=True)
    assert source_terminal.known_failure(bound) is None
    with pytest.raises(SyncError):
        source_terminal.known_failure(bound, attempt_id=attempted.attempt_id)


def test_retained_failure_body_loss_is_capacity_wait_and_never_terminal(bound):
    _, attempted, _ = failed_source(bound)
    with budget._connection(bound.profile, write=True) as con:
        con.execute("UPDATE budget_attempt SET result_text=NULL,result_storage_state='retired' WHERE attempt_id=?",
                    (attempted.attempt_id,))
    with pytest.raises(BudgetDeferred) as caught:
        source_terminal.known_failure(bound)
    assert caught.value.reason == "result_capacity"


@pytest.mark.parametrize("failure_class,expected", [("process_failure", None), ("artifact_invalid", "media_invalid")])
def test_audio_container_fallback_failure_is_not_whole_source_failure(bound, failure_class, expected):
    failed_source(bound, kind="download", segment="audio", failure_class=failure_class)
    proof = source_terminal.known_failure(bound)
    assert proof is None if expected is None else proof.public()["error_class"] == expected


def test_stale_settlement_and_mutable_public_proof_cannot_grant_clear(bound):
    _, attempted, _ = failed_source(bound)
    proof = source_terminal.known_failure(bound)
    raw = proof.public()
    raw["settlement_sha256"] = "e" * 64
    with pytest.raises(SyncError):
        source_terminal.revalidate_failure(bound, raw)
    with budget._connection(bound.profile, write=True) as con:
        con.execute("UPDATE budget_attempt SET settlement_sha256=? WHERE attempt_id=?", ("e" * 64, attempted.attempt_id))
    with pytest.raises(budget.BudgetLedgerError):
        source_terminal.revalidate_failure(bound, proof)


def test_same_namespace_with_foreign_logical_input_is_not_original_failure(bound):
    plan = source_contract.SourcePlan("source_metadata", LEAF)
    operation = bound.operation("source_metadata", "ingest:metadata:v1", "e" * 64)
    attempted = budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, source_contract_version=1),
                               profile=bound.profile)
    assert budget.mark_dispatching(attempted.attempt_id, attempted.reservation_sha256, str(uuid.uuid4()),
        expected_revision=attempted.revision, profile=bound.profile).won
    result = source_contract.SourceProcessResult(plan.request_sha256, True, True, 1, "exited",
                                                  failure_class="process_failure")
    budget.settle(attempted.attempt_id, plan.request_sha256, outcome="terminal_error",
        result_text=source_contract.encode_envelope(plan, result, input_sha256=operation.input_sha256),
        profile=bound.profile)
    with pytest.raises(SyncError) as caught:
        source_terminal.known_failure(bound)
    assert caught.value.code == "SYNC_SOURCE_FAILURE_UNPROVED"


def test_terminal_source_cannot_reserve_next_ordinal_or_replace_original_failure(bound):
    operation, attempted, plan = failed_source(bound)
    proof = source_terminal.known_failure(bound)
    with pytest.raises(budget.BudgetLedgerError) as caught:
        budget.reserve(operation, budget.AttemptSpec(plan.request_sha256, ordinal=2, source_contract_version=1),
                       profile=bound.profile)
    assert caught.value.code == "BUDGET_STATE_CONFLICT"
    assert source_terminal.revalidate_failure(bound, proof) == proof
    assert budget.get_operation(operation, profile=bound.profile).latest_ordinal == attempted.ordinal


def test_failure_clear_guard_checks_actual_database_epoch_with_old_state(bound):
    failed_source(bound)
    proof = source_terminal.known_failure(bound)
    before = bound.store.read()
    with budget._connection(bound.profile, write=True) as con:
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    assert bound.store.read() == before
    with pytest.raises(SyncError) as caught:
        with source_terminal.failure_clear_guard(bound, proof):
            pytest.fail("Actual restored DB cannot clear an old core")
    assert caught.value.code == "SYNC_SOURCE_FAILURE_UNPROVED"
    assert bound.store.read() == before


def test_failure_clear_guard_pins_epoch_and_paid_ledger_without_mutating_database(bound):
    from reno import db, tasks
    failed_source(bound)
    proof = source_terminal.known_failure(bound)
    tasks.pause_sync(bound.core["root_job_id"], profile=bound.profile)
    before = bound.store.read()
    with source_terminal.failure_clear_guard(bound, proof):
        other = db.connect(bound.profile.database)
        try:
            other.execute("PRAGMA busy_timeout=0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
        assert source_terminal.revalidate_failure(bound, proof) == proof
    other = db.connect(bound.profile.database)
    try:
        other.execute("BEGIN IMMEDIATE")
        assert db.knowledge_version(other)["library_epoch"] == bound.core["local_epoch"]
        other.rollback()
    finally:
        other.close()
    assert bound.store.read() == before
