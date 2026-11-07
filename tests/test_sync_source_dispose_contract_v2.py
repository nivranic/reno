"""Explicit dispose2 never turns a shaped continuity DTO into authority."""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import sync_source_readmit_contract as wire
from tests import test_sync_source_readmit_contract as old
from tests import test_sync_source_readmit_contract_v2 as readmit2


def request(kind="budget_defer", *, registered=False, same_epoch=True, defer_request_id=None):
    body = old.dispose(old.old_request(kind, legacy=not registered,
        defer_request_id=defer_request_id), registered=registered)
    body["source"]["handshake"]["migration_version"] = 17
    body["format_version"] = 2
    body["continuity"] = {"format_version": 1,
        "continuity_id": body["registration"]["continuity_id"] if registered else old.identifier(),
        "contract_sha256": "f" * 64, "target_epoch": body["recovery_stream"]["library_epoch"]}
    if same_epoch:
        body["current_home_epoch"] = body["original"]["home_epoch"]
        body["preservation"] = None
    return body


def result(body, *, applied=True, parent=None):
    answer = old.disposition(body, applied=applied, parent=parent)
    answer["format_version"] = 2
    return answer


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
@pytest.mark.parametrize("same_epoch", [False, True])
@pytest.mark.parametrize("registered", [False, True])
def test_exact_dispose2_applied_preserves_original_credentials_and_receipt(kind, same_epoch, registered):
    parent_body = old.old_request()
    parent = old.old_receipt(parent_body)
    body = request(kind, registered=registered, same_epoch=same_epoch,
        defer_request_id=parent_body["request_id"])
    answer = result(body, parent=parent)
    frozen = deepcopy(body)
    assert wire.normalize_request(body) == frozen == body
    assert wire.normalize_result(body, answer, defer_receipt=parent) == answer
    assert body["original_request"]["lease"] != body["lease"] if not registered else True
    assert wire.request_sha256(body) == wire.digest(frozen)
    detached = wire.normalize_dispose_request(body)
    detached["original_request"]["lease"]["lease_token"] = "changed"
    assert body == frozen


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_registered_negative_is_v2_but_does_not_have_a_guessed_original_receipt(kind):
    body = request(kind, registered=True)
    answer = result(body, applied=False)
    assert wire.normalize_dispose_result(body, answer) == answer
    assert answer["original_receipt"] is None
    without_actual_registration = deepcopy(body)
    without_actual_registration["registration"] = None
    guessed = result(without_actual_registration, applied=False)
    old.invalid(wire.normalize_dispose_result, without_actual_registration, guessed)


@pytest.mark.parametrize("version", [None, 0, 1, 3, True, "2", 2.0])
def test_explicit_dispose_version_and_result_version_are_closed(version):
    body = request()
    body["format_version"] = version
    old.invalid(wire.normalize_dispose_request, body)
    body = request()
    answer = result(body)
    answer["format_version"] = version
    old.invalid(wire.normalize_dispose_result, body, answer)


@pytest.mark.parametrize("change", ["missing", "none", "extra", "target_epoch", "version", "registration_id"])
def test_dispose2_requires_current_thin_continuity_and_matching_registration(change):
    body = request(registered=True)
    if change == "missing":
        body.pop("continuity")
    elif change == "none":
        body["continuity"] = None
    elif change == "extra":
        body["continuity"]["caller_proof"] = True
    elif change == "target_epoch":
        body["continuity"]["target_epoch"] += 1
    elif change == "version":
        body["continuity"]["format_version"] = 2
    else:
        body["continuity"]["continuity_id"] = old.identifier()
    old.invalid(wire.normalize_dispose_request, body)


@pytest.mark.parametrize("change", ["cross_epoch_none", "regression", "migration16", "implicit_continuity"])
def test_v2_representation_does_not_relax_restore_or_old_version_contract(change):
    body = request()
    if change == "cross_epoch_none":
        body["current_home_epoch"] += 1
    elif change == "regression":
        body["current_home_epoch"] -= 1
    elif change == "migration16":
        body["source"]["handshake"]["migration_version"] = 16
    else:
        body.pop("format_version")
    old.invalid(wire.normalize_dispose_request, body)


@pytest.mark.parametrize("registered", [False, True])
def test_original_implicit1_dispose_bytes_and_hash_are_unchanged(registered):
    body = old.dispose(registered=registered)
    answer = old.disposition(body)
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    original_body, original_answer = encode(body), encode(answer)
    assert encode(wire.normalize_dispose_request(body)) == original_body
    assert encode(wire.normalize_dispose_result(body, answer)) == original_answer
    assert wire.request_sha256(body) == hashlib.sha256(original_body).hexdigest()
    semantic = {key: value for key, value in answer.items() if key != "replayed"}
    assert wire.disposition_sha256(answer) == hashlib.sha256(encode(semantic)).hexdigest()
    assert "format_version" not in body and "continuity" not in body


def test_readmit2_accepts_closed_mixed_dispositions_but_readmit1_stays_v1():
    original1 = old.dispose(old.old_request("source_fail"), registered=True)
    original2 = request("budget_defer", registered=True)
    dispositions = [old.disposition(original1, applied=False), result(original2, applied=False)]
    body = readmit2.request()
    body["dispositions"] = deepcopy(dispositions)
    assert wire.normalize_readmit_request(body) == body
    assert wire.normalize_readmit_result(body, readmit2.result(body))["disposition_sha256s"] == [
        wire.disposition_sha256(item) for item in dispositions]
    v1 = old.readmit()
    v1["dispositions"] = deepcopy(dispositions)
    old.invalid(wire.normalize_readmit_request, v1)
    with pytest.raises(wire.SourceReadmitContractError):
        wire._dispose_shape(dispositions[1])
    wire._dispose_shape2(dispositions[1])
    wire._dispose_shape_versioned(dispositions[0])
    wire._dispose_shape_versioned(dispositions[1])


def test_disposition_semantic_hash_binds_typed_version_and_ignores_only_replay():
    answer = result(request(registered=True), applied=False)
    same = deepcopy(answer)
    same["replayed"] = True
    assert wire.disposition_sha256(same) == wire.disposition_sha256(answer)
    same["format_version"] = 1
    assert wire.disposition_sha256(same) != wire.disposition_sha256(answer)
