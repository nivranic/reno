"""Explicit readmit v2 is representation, never timer or restore authority."""
from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from reno import sync_source_readmit_contract as contract
from tests import test_sync_source_readmit_contract as old


def timer_id(defer_request_id):
    return str(uuid.uuid5(uuid.UUID(defer_request_id),
                         "sync-budget-timer:v2:" + defer_request_id))


def request(*, basis=None, same_epoch=True):
    value = old.readmit(refund=basis is not None)
    manual = value.pop("manual_resume_basis")
    value.update(format_version=2, resume_basis=None)
    value["source"]["handshake"]["migration_version"] = 17
    if same_epoch:
        value["current_home_epoch"] = value["original"]["home_epoch"]
        value["preservation"] = None
    if basis == "manual":
        value["resume_basis"] = {"kind": "manual", **manual}
    elif basis == "timer":
        parent = value["defer_receipt"]
        value["resume_basis"] = {
            "kind": "timer", "timer_operation_id": timer_id(parent["request_id"]),
            "timer_intent_sha256": "6" * 64, "timer_receipt_sha256": "7" * 64,
            "defer_request_id": parent["request_id"],
            "defer_request_sha256": parent["request_sha256"],
            "defer_receipt_sha256": contract.digest(parent), "retry_at": parent["retry_at"]}
    return value


def result(value):
    answer = old.admission(value)
    answer["format_version"] = 2
    answer["admission_sha256"] = contract.admission_sha256(answer)
    return answer


def invalid(function, *args):
    with pytest.raises(contract.SourceReadmitContractError) as caught:
        function(*args)
    assert str(caught.value) == "SOURCE_READMIT_CONTRACT_INVALID"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("basis", [None, "manual", "timer"])
@pytest.mark.parametrize("same_epoch", [False, True])
def test_v2_exact_held_manual_and_timer_bind_result_without_mutation(basis, same_epoch):
    body = request(basis=basis, same_epoch=same_epoch)
    frozen = deepcopy(body)
    answer = result(body)
    assert contract.normalize_request(body) == frozen == body
    assert contract.normalize_result(body, answer) == answer
    assert contract.request_sha256(body) == contract.digest(frozen)
    detached = contract.normalize_readmit_request(body)
    detached["original"]["core_sha256"] = "f" * 64
    assert body == frozen


@pytest.mark.parametrize("refund", [False, True])
def test_implicit_v1_request_result_and_manual_bytes_are_preserved(refund):
    body = old.readmit(refund=refund)
    answer = old.admission(body)
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":"))
    body_bytes, answer_bytes = encode(body).encode("utf-8"), encode(answer).encode("utf-8")
    assert contract.canonical_json(contract.normalize_readmit_request(body)).encode("utf-8") == body_bytes
    assert contract.canonical_json(contract.normalize_readmit_result(body, answer)).encode("utf-8") == answer_bytes
    assert contract.request_sha256(body) == hashlib.sha256(body_bytes).hexdigest()
    assert "format_version" not in body and "resume_basis" not in body
    assert contract.normalize_readmit_request(body)["manual_resume_basis"] == body["manual_resume_basis"]


@pytest.mark.parametrize("version", [None, 0, 1, 3, True, "2", 2.0])
def test_explicit_version_is_closed_and_never_inferred_from_union(version):
    body = request(basis="timer")
    body["format_version"] = version
    invalid(contract.normalize_readmit_request, body)


def test_missing_v2_version_and_mixed_v1_v2_fields_are_rejected():
    body = request(basis="timer")
    body.pop("format_version")
    invalid(contract.normalize_readmit_request, body)
    body = request(basis="manual")
    body["manual_resume_basis"] = {key: value for key, value in body["resume_basis"].items() if key != "kind"}
    invalid(contract.normalize_readmit_request, body)
    body = old.readmit(refund=True)
    body["resume_basis"] = {"kind": "manual", **body["manual_resume_basis"]}
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("basis", ["manual", "timer"])
@pytest.mark.parametrize("change", ["missing", "unknown_kind", "extra", "wrong_parent", "wrong_shape"])
def test_refund_basis_union_is_exact_and_binds_original_parent(basis, change):
    body = request(basis=basis)
    if change == "missing":
        body["resume_basis"] = None
    elif change == "unknown_kind":
        body["resume_basis"]["kind"] = "automatic"
    elif change == "extra":
        body["resume_basis"]["manual_override"] = True
    elif change == "wrong_parent":
        body["resume_basis"]["defer_request_id"] = old.identifier()
    else:
        body["resume_basis"] = []
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("field,replacement", [
    ("timer_operation_id", old.identifier()),
    ("timer_intent_sha256", "A" * 64),
    ("timer_receipt_sha256", None),
    ("defer_request_sha256", "8" * 64),
    ("defer_receipt_sha256", "9" * 64),
    ("retry_at", None),
    ("retry_at", "2099-01-03T00:00:00.000Z"),
    ("retry_at", "private-path"),
])
def test_timer_refuses_forged_namespace_parent_hash_or_original_deadline(field, replacement):
    body = request(basis="timer")
    body["resume_basis"][field] = replacement
    invalid(contract.normalize_readmit_request, body)


def test_timer_cannot_use_manual_namespace_even_with_valid_uuid():
    body = request(basis="timer")
    parent = body["defer_receipt"]["request_id"]
    body["resume_basis"]["timer_operation_id"] = str(uuid.uuid5(uuid.UUID(parent),
                                                             "sync-budget-resume:v1:" + parent))
    invalid(contract.normalize_readmit_request, body)


def test_timer_cannot_use_another_uuid_namespace_with_the_same_timer_name():
    body = request(basis="timer")
    parent = body["defer_receipt"]["request_id"]
    body["resume_basis"]["timer_operation_id"] = str(uuid.uuid5(uuid.UUID(old.identifier()),
                                                             "sync-budget-timer:v2:" + parent))
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("value", [None, True, 7, "not-a-uuid", "00000000-0000-0000-0000-00000000000A"])
def test_timer_name_helper_rejects_noncanonical_parent_identifiers(value):
    invalid(contract.timer_operation_id, value)


def test_timer_name_helper_is_exact_and_independent_of_receipt_or_clock_bytes():
    parent = "12345678-1234-5678-1234-567812345678"
    assert contract.timer_operation_id(parent) == timer_id(parent)
    assert contract.timer_operation_id(parent) == contract.timer_operation_id(parent)


@pytest.mark.parametrize("field,replacement", [
    ("authorization_id", None), ("authorization_sha256", "F" * 64),
    ("defer_request_id", old.identifier()),
])
def test_v2_manual_still_uses_exact_original_manual_fields(field, replacement):
    body = request(basis="manual")
    body["resume_basis"][field] = replacement
    invalid(contract.normalize_readmit_request, body)


def test_v2_manual_cannot_hide_a_timer_identifier_or_intent():
    body = request(basis="manual")
    body["resume_basis"]["timer_operation_id"] = timer_id(body["defer_receipt"]["request_id"])
    invalid(contract.normalize_readmit_request, body)


def test_timer_never_turns_infinite_wait_into_due_and_manual_can_resume_it():
    body = request(basis="timer")
    body["defer_receipt"]["retry_at"] = None
    body["resume_basis"]["retry_at"] = None
    body["resume_basis"]["defer_receipt_sha256"] = contract.digest(body["defer_receipt"])
    invalid(contract.normalize_readmit_request, body)
    manual = request(basis="manual")
    manual["defer_receipt"]["retry_at"] = None
    answer = result(manual)
    assert contract.normalize_readmit_result(manual, answer) == answer


@pytest.mark.parametrize("change", ["reason", "operation", "committed_at", "attempts"])
def test_timer_parent_receipt_sha_binds_all_positive_receipt_bytes(change):
    body = request(basis="timer")
    parent = body["defer_receipt"]
    if change == "reason":
        parent["reason"] = "download_pacing"
    elif change == "operation":
        parent["budget_operation_id"] = old.identifier()
    elif change == "committed_at":
        parent["committed_at"] = "2030-01-02T00:00:00.000Z"
    else:
        parent["attempts"] -= 1
        body["resume_receipt"]["attempts"] = parent["attempts"]
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("basis", ["manual", "timer"])
def test_held_claim_cannot_carry_a_resume_basis_without_original_receipts(basis):
    body = request(basis=basis)
    body.update(defer_receipt=None, resume_receipt=None)
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("change", ["v1", "cross_epoch", "epoch_regression", "v1_dispose"])
def test_absent_preservation_is_only_v2_and_exact_same_epoch(change):
    if change == "v1":
        body = old.readmit()
        body["current_home_epoch"] = body["original"]["home_epoch"]
        body["preservation"] = None
    elif change == "v1_dispose":
        body = old.dispose()
        body["current_home_epoch"] = body["original"]["home_epoch"]
        body["preservation"] = None
        invalid(contract.normalize_dispose_request, body)
        return
    else:
        body = request()
        body["current_home_epoch"] += 1 if change == "cross_epoch" else -1
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("change", ["wrong_from", "wrong_to", "wrong_certificate", "format2"])
def test_v2_cross_epoch_still_requires_exact_legacy_preservation_shape(change):
    body = request(same_epoch=False)
    if change == "wrong_from":
        body["preservation"]["from_epoch"] += 1
    elif change == "wrong_to":
        body["preservation"]["to_epoch"] += 1
    elif change == "wrong_certificate":
        body["preservation"]["certificate_sha256"] = "X" * 64
    else:
        body["preservation"]["format_version"] = 2
    invalid(contract.normalize_readmit_request, body)


def test_implicit_v1_same_epoch_still_preserves_original_certificate_bytes():
    body = old.readmit(refund=True)
    body["current_home_epoch"] = body["original"]["home_epoch"]
    body["preservation"]["to_epoch"] = body["current_home_epoch"]
    frozen = contract.canonical_json(body)
    assert contract.canonical_json(contract.normalize_readmit_request(body)) == frozen
    assert contract.normalize_readmit_result(body, old.admission(body))["format_version"] == 1


@pytest.mark.parametrize("migration", [15, 16, True])
def test_v2_representation_requires_prospective17_without_changing_global_version(migration):
    body = request(basis="timer")
    body["source"]["handshake"]["migration_version"] = migration
    invalid(contract.normalize_readmit_request, body)
    from reno import migrations
    assert migrations.SCHEMA_VERSION == 16


@pytest.mark.parametrize("change", ["size", "depth", "cycle", "surrogate"])
def test_v2_copy_remains_bounded_before_union_validation(change):
    body = request(basis="timer")
    if change == "size":
        body["resume_basis"]["private_body"] = "汉" * contract.MAX_READMIT_BYTES
    elif change == "surrogate":
        body["resume_basis"]["timer_receipt_sha256"] = "\ud800"
    else:
        nested = []
        if change == "cycle":
            nested.append(nested)
        else:
            for _ in range(contract.MAX_JSON_DEPTH + 1):
                nested = [nested]
        body["resume_basis"]["private_body"] = nested
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("result_version", [1, 3, True, "2", 2.0])
def test_result_version_matches_explicit_request_even_with_new_digest(result_version):
    body = request(basis="timer")
    answer = result(body)
    answer["format_version"] = result_version
    answer["admission_sha256"] = contract.admission_sha256(answer)
    invalid(contract.normalize_readmit_result, body, answer)


def test_v1_cannot_accept_format2_result_and_v2_result_binds_full_union():
    v1 = old.readmit(refund=True)
    answer = old.admission(v1)
    answer["format_version"] = 2
    answer["admission_sha256"] = contract.admission_sha256(answer)
    invalid(contract.normalize_readmit_result, v1, answer)
    body = request(basis="timer")
    answer = result(body)
    body["resume_basis"]["timer_intent_sha256"] = "a" * 64
    invalid(contract.normalize_readmit_result, body, answer)


@pytest.mark.parametrize("basis", [None, "manual", "timer"])
def test_cross_epoch_result_cannot_drop_preservation_even_with_recomputed_sha(basis):
    body = request(basis=basis, same_epoch=False)
    answer = result(body)
    answer["preservation"] = None
    answer["admission_sha256"] = contract.admission_sha256(answer)
    invalid(contract.normalize_readmit_result, body, answer)


def test_v2_positive_and_replay_have_same_semantic_hash_without_granting_authority():
    body = request(basis="timer")
    answer = result(body)
    replay = {**answer, "replayed": True}
    assert contract.admission_sha256(answer) == contract.admission_sha256(replay)
    assert contract.normalize_readmit_result(body, replay) == replay


def test_timer_normalization_never_reads_real_clock_sql_state_or_model(monkeypatch):
    from reno import db, llm, storage_admin, sync_state, tasks

    def forbidden(*args, **kwargs):
        pytest.fail("Pure v2 readmit contract attempted an authority/effect check")

    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(llm, "_post", forbidden)
    monkeypatch.setattr(llm, "_post_anthropic", forbidden)
    monkeypatch.setattr(storage_admin, "setup", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(tasks, "clock_ms", forbidden)
    monkeypatch.setattr(tasks, "utc_now", forbidden)
    body = request(basis="timer")
    answer = result(body)
    assert contract.normalize_readmit_result(body, answer) == answer
