"""Synthetic pure contracts cannot manufacture continuity or send authority."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import sync_source_control_contract as old_source
from reno import sync_source_readmit_contract as contract


def identifier():
    return str(uuid.uuid4())


SOURCE = {"library_id": identifier(), "node_id": identifier(), "handshake": {
    "protocol_version": 2, "migration_version": 16, "core_schema_sha256": "a" * 64}}
TARGET = {"library_id": identifier(), "node_id": identifier()}
LEASE = {"session_id": identifier(), "lease_token": identifier(), "generation": 4,
         "lease_until": "2099-01-01T00:00:00.000Z"}
CLAIM = {"item_id": 7, "lease_token": identifier(), "lease_generation": 3}
STREAM = {"library_id": TARGET["library_id"], "library_epoch": 7}
COMMITTED = "2030-01-01T00:00:00.000Z"
CORE = "b" * 64
URL_SHA = "c" * 64


def current(kind):
    return {"kind": kind, "request_id": identifier(), "source": deepcopy(SOURCE),
        "target": deepcopy(TARGET), "lease": deepcopy(LEASE), "recovery_generation": 2,
        "recovery_stream": deepcopy(STREAM), "control_revision": 4}


def old_request(kind="budget_defer", *, legacy=False, defer_request_id=None):
    body = current(kind)
    body.pop("control_revision")
    if legacy:
        body["source"]["handshake"]["migration_version"] = 15
        body["source"]["handshake"]["core_schema_sha256"] = "1" * 64
        body["lease"] = {"session_id": identifier(), "lease_token": identifier(),
            "generation": 1, "lease_until": "2000-01-01T00:00:00.000Z"}
        body["recovery_generation"] = 0
    if kind == "budget_resume":
        body["defer_request_id"] = defer_request_id or identifier()
    else:
        body["claim"] = deepcopy(CLAIM)
        if kind == "budget_defer":
            body.update(reason="daily_limit", retry_at="2099-01-02T00:00:00.000Z",
                        budget_operation_id=identifier())
        else:
            body.update(core_sha256=CORE, submitted_url_sha256=URL_SHA,
                failure={"format_version": 1, "operation_id": identifier(), "attempt_id": identifier(),
                    "ordinal": 4, "input_sha256": "1" * 64, "request_sha256": "2" * 64,
                    "settlement_sha256": "3" * 64, "result_sha256": "4" * 64,
                    "operation_kind": "source_metadata", "segment": None, "source_contract_version": 1,
                    "resolver_contract_version": None, "resolver_phase": None, "error_class": "source_unavailable"})
    return body


def old_receipt(body, *, attempts=2, parent=None):
    if body["kind"] == "source_fail":
        return {"format_version": 1, "request_id": body["request_id"],
            "request_sha256": old_source.request_sha256(body), "core_sha256": CORE,
            "submitted_url_sha256": URL_SHA, "failure_sha256": old_source.failure_sha256(body["failure"]),
            "item_id": body["claim"]["item_id"], "lease_generation": body["claim"]["lease_generation"],
            "attempts": attempts, "status": "dead", "error_class": body["failure"]["error_class"],
            "committed_at": COMMITTED, "replayed": False}
    return {"request_id": body["request_id"], "request_sha256": contract.digest(body),
        "item_id": body.get("claim", parent or CLAIM)["item_id"],
        "lease_generation": body.get("claim", parent or CLAIM)["lease_generation"],
        "status": "budget_wait" if body["kind"] == "budget_defer" else "queued",
        "attempts": attempts, "reason": body.get("reason"), "retry_at": body.get("retry_at"),
        "budget_operation_id": body.get("budget_operation_id"), "committed_at": COMMITTED, "replayed": False}


def bound(kind):
    body = current(kind)
    body.update(original={"core_sha256": CORE, "home_epoch": 1, "item_id": CLAIM["item_id"],
        "submitted_url_sha256": URL_SHA, "claim": deepcopy(CLAIM), "recovery_generation": 0,
        "recovery_stream": deepcopy(STREAM)}, current_home_epoch=2,
        preservation={"format_version": 1, "frontier_sha256": "d" * 64,
            "certificate_sha256": "e" * 64, "from_epoch": 1, "to_epoch": 2})
    return body


def register(old=None):
    old = old or old_request()
    body = current("source_register")
    body.update(continuity={"format_version": 1, "continuity_id": identifier(),
        "contract_sha256": "f" * 64, "target_epoch": STREAM["library_epoch"]},
        core_sha256=CORE, item_id=CLAIM["item_id"], original_request=deepcopy(old))
    return body


def control_result(body):
    old = body["original_request"]
    return {"format_version": 1, "request_id": body["request_id"],
        "request_sha256": contract.digest(body), "original_request_id": old["request_id"],
        "original_request_sha256": contract.digest(old), "original_kind": old["kind"],
        "core_sha256": CORE, "item_id": CLAIM["item_id"], "committed_at": COMMITTED, "replayed": False}


def registration(body):
    value = control_result(body)
    value.update(continuity_id=body["continuity"]["continuity_id"], registration_sha256="0" * 64)
    value["registration_sha256"] = contract.registration_sha256(value)
    return value


def dispose(old=None, *, registered=False):
    old = old or old_request(legacy=not registered)
    body = bound("source_dispose")
    body.update(original_request=deepcopy(old), registration=registration(register(old)) if registered else None)
    return body


def disposition(body, *, applied=True, parent=None):
    value = control_result(body)
    registration = body["registration"]
    value.update(disposition="applied" if applied else "not_applied",
        original_receipt=old_receipt(body["original_request"], parent=parent) if applied else None,
        continuity_id=None if registration is None else registration["continuity_id"],
        registration_sha256=None if registration is None else registration["registration_sha256"])
    return value


def readmit(*, refund=False):
    body = bound("source_readmit")
    body.update(expected_claim=deepcopy(CLAIM), previous_admission_id=None, dispositions=[],
                defer_receipt=None, resume_receipt=None, manual_resume_basis=None)
    if refund:
        old = old_request(legacy=True)
        parent = old_receipt(old)
        resume = old_request("budget_resume", defer_request_id=old["request_id"])
        body.update(defer_receipt=parent, resume_receipt=old_receipt(resume, parent=parent),
            manual_resume_basis={"authorization_id": identifier(), "authorization_sha256": "5" * 64,
                                 "defer_request_id": parent["request_id"]})
    return body


def admission(body, *, attempts=3):
    original = body["original"]
    value = {"format_version": 1, "admission_id": body["request_id"],
        "request_sha256": contract.digest(body), "admission_sha256": "0" * 64,
        "core_sha256": original["core_sha256"], "item_id": original["item_id"],
        "submitted_url_sha256": original["submitted_url_sha256"],
        "current_home_epoch": body["current_home_epoch"], "preservation": deepcopy(body["preservation"]),
        "source_identity": {key: body["source"][key] for key in ("library_id", "node_id")},
        "target_identity": deepcopy(body["target"]), "session": deepcopy(body["lease"]),
        "claim": {"item_id": original["item_id"], "lease_token": identifier(),
                  "lease_generation": body["expected_claim"]["lease_generation"] + 1},
        "recovery_generation": body["recovery_generation"], "recovery_stream": deepcopy(body["recovery_stream"]),
        "control_revision": body["control_revision"], "attempts": attempts,
        "attempt_basis": "original_defer_refund" if body["defer_receipt"] is not None else "held_claim",
        "previous_admission_id": body["previous_admission_id"],
        "disposition_sha256s": [contract.disposition_sha256(item) for item in body["dispositions"]],
        "committed_at": COMMITTED, "replayed": False}
    value["admission_sha256"] = contract.admission_sha256(value)
    return value


def set_path(value, path, replacement):
    parent = value
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = replacement


def invalid(function, *args, **kwargs):
    with pytest.raises(contract.SourceReadmitContractError) as caught:
        function(*args, **kwargs)
    assert str(caught.value) == "SOURCE_READMIT_CONTRACT_INVALID"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_registration_binds_exact_current_control_body_and_explicit_item(kind):
    body = register(old_request(kind))
    original = deepcopy(body)
    result = registration(body)
    assert contract.normalize_request(body) == original == body
    assert contract.normalize_result(body, result) == result
    assert contract.request_sha256(body) == contract.digest(original)
    copied = contract.normalize_request(body)
    copied["original_request"]["source"]["handshake"]["migration_version"] = 99
    assert body == original


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_positive_legacy_disposition_preserves_old_credentials(kind):
    parent_body = old_request(legacy=True)
    parent = old_receipt(parent_body)
    old = old_request(kind, legacy=True, defer_request_id=parent["request_id"])
    body = dispose(old)
    original = deepcopy(body)
    result = disposition(body, parent=parent)
    assert contract.normalize_dispose_result(body, result,
        defer_receipt=parent if kind == "budget_resume" else None) == result
    assert body == original
    assert body["original_request"]["lease"]["lease_until"].startswith("2000-")
    result["replayed"] = True
    assert contract.normalize_result(body, result,
        defer_receipt=parent if kind == "budget_resume" else None)["replayed"] is True


def test_resume_disposition_requires_original_parent_defer_receipt():
    parent_body = old_request()
    parent = old_receipt(parent_body)
    old = old_request("budget_resume", defer_request_id=parent["request_id"])
    body = dispose(old)
    result = disposition(body, parent=parent)
    invalid(contract.normalize_dispose_result, body, result)
    assert contract.normalize_dispose_result(body, result, defer_receipt=parent) == result
    for changes in ({"request_id": identifier()}, {"item_id": 8}, {"lease_generation": 4},
                    {"attempts": 1}, {"status": "queued"}):
        invalid(contract.normalize_dispose_result, body, result, defer_receipt={**parent, **changes})


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_missing_legacy_receipt_never_proves_not_applied(kind):
    body = dispose(old_request(kind, legacy=True))
    invalid(contract.normalize_dispose_result, body, disposition(body, applied=False))
    # Supplying well-shaped continuity strings without a registration in the
    # exact request still cannot produce a negative result.
    result = disposition(body, applied=False)
    result.update(continuity_id=identifier(), registration_sha256="f" * 64)
    invalid(contract.normalize_dispose_result, body, result)


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_registered_negative_has_closed_registration_references(kind):
    body = dispose(old_request(kind), registered=True)
    result = disposition(body, applied=False)
    assert contract.normalize_result(body, result) == result
    for key, foreign in (("continuity_id", identifier()), ("registration_sha256", "a" * 64)):
        invalid(contract.normalize_dispose_result, body, {**result, key: foreign})


@pytest.mark.parametrize("path,replacement", [
    (("kind",), "source_fail"), (("request_id",), "private-input"),
    (("item_id",), True), (("core_sha256",), "A" * 64),
    (("source", "handshake", "migration_version"), 15),
    (("source", "handshake", "protocol_version"), 3),
    (("lease", "generation"), True), (("lease", "lease_until"), "2099-01-01T00:00:00Z"),
    (("recovery_generation",), True), (("control_revision",), -1),
    (("continuity", "format_version"), True), (("continuity", "continuity_id"), "private-token"),
    (("continuity", "contract_sha256"), "f" * 63), (("continuity", "target_epoch"), 8),
    (("original_request", "request_id"), "not-a-uuid"),
    (("original_request", "source", "handshake", "migration_version"), 15),
    (("original_request", "lease", "lease_token"), identifier()),
    (("original_request", "recovery_generation"), 1),
    (("original_request", "claim", "item_id"), 8),
])
def test_registration_rejects_changed_capability_peer_lease_and_item(path, replacement):
    body = register()
    set_path(body, path, replacement)
    invalid(contract.normalize_register_request, body)


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume", "source_fail"])
def test_legacy_body_cannot_be_retroactively_registered(kind):
    invalid(contract.normalize_register_request, register(old_request(kind, legacy=True)))


@pytest.mark.parametrize("path", [("source",), ("target",), ("lease",), ("continuity",),
    ("source", "handshake"), ("original_request",), ("original_request", "claim")])
def test_register_is_closed_at_every_nested_level(path):
    body = register()
    parent = body
    for key in path:
        parent = parent[key]
    parent["private_body"] = "synthetic-secret"
    invalid(contract.normalize_request, body)


@pytest.mark.parametrize("key,replacement", [
    ("request_id", identifier()), ("request_sha256", "f" * 64), ("original_request_id", identifier()),
    ("original_request_sha256", "f" * 64), ("original_kind", "source_fail"),
    ("core_sha256", "f" * 64), ("item_id", 8), ("continuity_id", identifier()),
    ("committed_at", "2030-01-01T00:00:00Z"), ("format_version", True), ("replayed", 1),
])
def test_registration_result_foreign_bindings_fail_even_with_recomputed_semantic_sha(key, replacement):
    body = register()
    result = {**registration(body), key: replacement}
    if key not in ("format_version", "replayed", "committed_at"):
        result["registration_sha256"] = contract.registration_sha256(result)
    invalid(contract.normalize_register_result, body, result)


@pytest.mark.parametrize("path,replacement", [
    (("original", "claim", "item_id"), 8), (("original", "home_epoch"), 3),
    (("original", "recovery_generation"), 3), (("current_home_epoch",), True),
    (("preservation", "from_epoch"), 2), (("preservation", "to_epoch"), 3),
    (("preservation", "frontier_sha256"), "\ud800"), (("preservation", "certificate_sha256"), "e" * 63),
    (("preservation", "format_version"), True), (("preservation", "to_epoch"), True),
    (("recovery_stream", "library_epoch"), 8), (("original", "recovery_stream", "library_epoch"), 6),
    (("original_request", "recovery_stream", "library_epoch"), 8),
    (("original_request", "recovery_generation"), 3),
    (("original_request", "source", "node_id"), identifier()),
    (("original_request", "target", "library_id"), identifier()),
    (("original_request", "claim", "item_id"), 8),
])
def test_disposition_rejects_foreign_epoch_frontier_peer_and_item(path, replacement):
    body = dispose()
    set_path(body, path, replacement)
    invalid(contract.normalize_dispose_request, body)


def test_later_admission_control_keeps_core_but_has_new_claim_credentials():
    old = old_request()
    old["claim"].update(lease_token=identifier(), lease_generation=4)
    body = dispose(old, registered=True)
    result = disposition(body)
    assert contract.normalize_dispose_result(body, result) == result
    # Actual lineage, not these caller strings, must be checked by future SQL.
    assert body["original_request"]["claim"] != body["original"]["claim"]


def test_foreign_source_core_or_url_cannot_be_wrapped_as_disposition():
    for key in ("core_sha256", "submitted_url_sha256"):
        old = old_request("source_fail", legacy=True)
        old[key] = "f" * 64
        invalid(contract.normalize_dispose_request, dispose(old))


@pytest.mark.parametrize("changes", [
    {"disposition": "unknown"}, {"original_kind": "source_fail"}, {"item_id": 8},
    {"original_request_id": identifier()}, {"original_request_sha256": "f" * 64},
    {"request_sha256": "f" * 64}, {"core_sha256": "f" * 64},
    {"continuity_id": identifier()}, {"registration_sha256": "f" * 64}, {"replayed": 1},
    {"committed_at": "private-path"}, {"private_body": "synthetic-secret"},
])
def test_disposition_result_cannot_substitute_body_core_item_or_registration(changes):
    body = dispose()
    invalid(contract.normalize_dispose_result, body, {**disposition(body), **changes})


@pytest.mark.parametrize("changes", [
    {"request_sha256": "f" * 64}, {"request_id": identifier()}, {"lease_generation": 4},
    {"item_id": 8}, {"attempts": True}, {"status": "queued"}, {"reason": "manual_pause"},
    {"retry_at": None}, {"budget_operation_id": identifier()}, {"replayed": True},
])
def test_applied_disposition_requires_exact_original_stored_receipt(changes):
    body = dispose()
    result = disposition(body)
    result["original_receipt"].update(changes)
    invalid(contract.normalize_dispose_result, body, result)


def test_embedded_registration_must_be_stored_and_bound_to_exact_old_body():
    body = dispose(registered=True)
    for changes in ({"replayed": True}, {"item_id": 8}, {"original_request_sha256": "f" * 64},
                    {"core_sha256": "f" * 64}, {"registration_sha256": "f" * 64}):
        edited = deepcopy(body)
        edited["registration"].update(changes)
        invalid(contract.normalize_dispose_request, edited)
    edited = deepcopy(body)
    edited["original_request"]["source"]["handshake"]["migration_version"] = 15
    edited["registration"]["original_request_sha256"] = contract.digest(edited["original_request"])
    edited["registration"]["registration_sha256"] = contract.registration_sha256(edited["registration"])
    invalid(contract.normalize_dispose_request, edited)


@pytest.mark.parametrize("refund", [False, True])
def test_targeted_admission_fences_old_claim_without_changing_original_facts(refund):
    body = readmit(refund=refund)
    original = deepcopy(body)
    result = admission(body)
    assert contract.normalize_request(body) == body
    assert contract.normalize_result(body, result) == result
    assert body == original
    assert result["claim"]["lease_generation"] == CLAIM["lease_generation"] + 1
    assert result["claim"]["lease_token"] != CLAIM["lease_token"]
    assert result["core_sha256"] == body["original"]["core_sha256"]
    if refund:
        assert result["attempts"] == body["defer_receipt"]["attempts"] + 1


def test_previous_admission_is_separate_from_immutable_original_claim():
    body = readmit()
    body["previous_admission_id"] = identifier()
    body["expected_claim"].update(lease_generation=4, lease_token=identifier())
    result = admission(body)
    assert contract.normalize_readmit_result(body, result) == result
    assert body["original"]["claim"] == CLAIM
    body["previous_admission_id"] = None
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("path,replacement", [
    (("expected_claim", "item_id"), 8), (("expected_claim", "lease_generation"), 2),
    (("expected_claim", "lease_generation"), 2**63 - 1), (("expected_claim", "lease_generation"), True),
    (("expected_claim", "lease_token"), identifier()), (("previous_admission_id",), "private-token"),
    (("dispositions",), {}), (("defer_receipt",), {}), (("resume_receipt",), {}),
    (("manual_resume_basis",), {"authorization_id": identifier()}),
])
def test_held_admission_closed_basis_and_claim_gates(path, replacement):
    body = readmit()
    set_path(body, path, replacement)
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("path,replacement", [
    (("defer_receipt",), None), (("resume_receipt",), None), (("manual_resume_basis",), None),
    (("manual_resume_basis", "authorization_id"), "private-input"),
    (("manual_resume_basis", "authorization_sha256"), "F" * 64),
    (("manual_resume_basis", "defer_request_id"), identifier()),
    (("defer_receipt", "item_id"), 8), (("resume_receipt", "lease_generation"), 4),
    (("defer_receipt", "attempts"), 5), (("resume_receipt", "attempts"), 1),
    (("defer_receipt", "replayed"), True), (("resume_receipt", "replayed"), True),
    (("defer_receipt", "status"), "queued"), (("resume_receipt", "status"), "budget_wait"),
])
def test_refund_requires_real_resume_representation_and_exact_parent_counts(path, replacement):
    body = readmit(refund=True)
    set_path(body, path, replacement)
    invalid(contract.normalize_readmit_request, body)


def test_refund_and_held_attempt_basis_cannot_be_swapped():
    for refund in (False, True):
        body = readmit(refund=refund)
        result = admission(body)
        result["attempt_basis"] = "held_claim" if refund else "original_defer_refund"
        result["admission_sha256"] = contract.admission_sha256(result)
        invalid(contract.normalize_readmit_result, body, result)
    body = readmit(refund=True)
    result = admission(body, attempts=body["defer_receipt"]["attempts"] + 2)
    invalid(contract.normalize_readmit_result, body, result)


def test_maximum_refund_restores_one_slot_and_never_resets_attempts():
    body = readmit(refund=True)
    for key in ("defer_receipt", "resume_receipt"):
        body[key]["attempts"] = 4
    result = admission(body, attempts=5)
    assert contract.normalize_readmit_result(body, result) == result
    invalid(contract.normalize_readmit_result, body, admission(body, attempts=1))


def test_readmit_binds_dispositions_by_permanent_semantic_hash_and_exact_receipt():
    body = readmit(refund=True)
    old = old_request(legacy=True)
    parent = old_receipt(old)
    resume = old_request("budget_resume", defer_request_id=old["request_id"])
    d1, d2 = dispose(old), dispose(resume)
    r1, r2 = disposition(d1), disposition(d2, parent=parent)
    body.update(dispositions=[r1, r2], defer_receipt=parent, resume_receipt=r2["original_receipt"],
        manual_resume_basis={"authorization_id": identifier(), "authorization_sha256": "5" * 64,
                             "defer_request_id": parent["request_id"]})
    result = admission(body)
    assert contract.normalize_readmit_result(body, result) == result
    result["disposition_sha256s"].reverse()
    result["admission_sha256"] = contract.admission_sha256(result)
    invalid(contract.normalize_readmit_result, body, result)
    body["dispositions"][0]["original_receipt"]["attempts"] -= 1
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("change", ["duplicate", "same-original", "too-many", "foreign-core",
                                      "foreign-item", "replayed", "applied-failure"])
def test_foreign_duplicate_or_terminal_disposition_cannot_readmit(change):
    body = readmit()
    proof = disposition(dispose(registered=True), applied=False)
    body["dispositions"] = [proof]
    if change == "duplicate":
        body["dispositions"].append(deepcopy(proof))
    elif change == "same-original":
        body["dispositions"].append({**proof, "request_id": identifier()})
    elif change == "too-many":
        body["dispositions"] *= 3
    elif change == "foreign-core":
        proof["core_sha256"] = "f" * 64
    elif change == "foreign-item":
        proof["item_id"] = 8
    elif change == "replayed":
        proof["replayed"] = True
    else:
        body["dispositions"] = [disposition(dispose(old_request("source_fail", legacy=True)))]
    invalid(contract.normalize_readmit_request, body)


def test_applied_defer_cannot_be_treated_as_a_still_held_claim():
    body = readmit()
    body["dispositions"] = [disposition(dispose())]
    invalid(contract.normalize_readmit_request, body)


@pytest.mark.parametrize("path,replacement", [
    (("admission_id",), identifier()), (("request_sha256",), "f" * 64),
    (("core_sha256",), "f" * 64), (("item_id",), 8), (("submitted_url_sha256",), "f" * 64),
    (("current_home_epoch",), 3), (("preservation", "frontier_sha256"), "f" * 64),
    (("source_identity", "node_id"), identifier()), (("target_identity", "node_id"), identifier()),
    (("session", "lease_token"), identifier()), (("session", "generation"), 5),
    (("claim", "item_id"), 8), (("claim", "lease_generation"), 5),
    (("claim", "lease_token"), CLAIM["lease_token"]), (("recovery_generation",), 3),
    (("recovery_stream", "library_epoch"), 8), (("control_revision",), 5),
    (("attempts",), True), (("attempts",), 0), (("attempts",), 6),
    (("previous_admission_id",), identifier()), (("disposition_sha256s",), ["f" * 64]),
    (("committed_at",), "private-path"), (("format_version",), True), (("replayed",), 1),
])
def test_admission_result_rejects_forged_fence_even_with_recomputed_digest(path, replacement):
    body = readmit()
    result = admission(body)
    set_path(result, path, replacement)
    if path != ("replayed",):
        result["admission_sha256"] = contract.admission_sha256(result)
    invalid(contract.normalize_readmit_result, body, result)


def test_replay_presentation_never_changes_stored_semantic_digests():
    body = register()
    result = registration(body)
    replay = {**result, "replayed": True}
    assert contract.registration_sha256(replay) == contract.registration_sha256(result)
    assert contract.normalize_register_result(body, replay) == replay
    dbody = dispose(registered=True)
    dresult = disposition(dbody, applied=False)
    assert contract.disposition_sha256({**dresult, "replayed": True}) == contract.disposition_sha256(dresult)
    abody = readmit()
    aresult = admission(abody)
    areplay = {**aresult, "replayed": True}
    assert contract.admission_sha256(areplay) == contract.admission_sha256(aresult)
    assert contract.normalize_readmit_result(abody, areplay) == areplay
    altered = deepcopy(aresult)
    altered["admission_sha256"] = "f" * 64
    invalid(contract.normalize_readmit_result, abody, altered)


@pytest.mark.parametrize("payload", [float("nan"), float("inf"), float("-inf"), "\ud800", b"bytes", (), object()])
def test_non_json_non_finite_and_surrogate_values_fail_with_sanitized_error(payload):
    invalid(contract.canonical_json, payload)
    body = readmit()
    body["private_body"] = payload
    invalid(contract.normalize_request, body)


def test_bytes_depth_nodes_cycles_and_multibyte_limits_are_checked_before_copy():
    invalid(contract.canonical_json, "x" * contract.MAX_CONTROL_BYTES)
    invalid(contract.canonical_json, "汉" * (contract.MAX_CONTROL_BYTES // 3 + 1))
    assert len(contract.canonical_json("汉" * (contract.MAX_CONTROL_BYTES // 3)).encode()) == contract.MAX_CONTROL_BYTES
    assert len(contract.canonical_json("x" * (contract.MAX_CONTROL_BYTES - 2)).encode()) == contract.MAX_CONTROL_BYTES
    deep = None
    for _ in range(contract.MAX_JSON_DEPTH + 1):
        deep = [deep]
    invalid(contract.canonical_json, deep)
    invalid(contract.canonical_json, [None] * contract.MAX_JSON_NODES)
    invalid(contract.canonical_json, ["x" * 1024] * 33)
    cyclic = []
    cyclic.append(cyclic)
    invalid(contract.canonical_json, cyclic)
    invalid(contract.canonical_json, {1: "not-a-string-key"})
    invalid(contract.canonical_json, "small", maximum=True)
    invalid(contract.canonical_json, "small", maximum=contract.MAX_CONTROL_BYTES + 1)
    # Readmit remains smaller than register/dispose; canonical encoding counts
    # actual UTF-8 bytes, independently of nested closed-field rejection.
    payload = "x" * contract.MAX_READMIT_BYTES
    assert len(contract.canonical_json(payload).encode()) > contract.MAX_READMIT_BYTES
    invalid(contract.canonical_json, payload, maximum=contract.MAX_READMIT_BYTES)


def test_canonical_digest_is_key_order_independent_and_never_mutates_input():
    body = register()
    original = deepcopy(body)
    reordered = dict(reversed(list(body.items())))
    assert contract.request_sha256(reordered) == contract.request_sha256(body)
    assert json.loads(contract.canonical_json(body)) == original == body


@pytest.mark.parametrize("payload", [None, [], {}, {"kind": []}, {"kind": "budget_defer"}, {"kind": "unknown"}])
def test_unknown_top_level_contract_never_falls_through_to_old_effects(payload):
    invalid(contract.normalize_request, payload)


def test_pure_normalization_does_not_open_db_provider_or_files(monkeypatch):
    from reno import db, llm, storage_admin
    body = register(old_request("source_fail"))
    result = registration(body)

    def forbidden(*args, **kwargs):
        pytest.fail("Pure readmit contract attempted an effect")

    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(llm, "_post", forbidden)
    monkeypatch.setattr(llm, "_post_anthropic", forbidden)
    monkeypatch.setattr(storage_admin, "setup", forbidden)
    assert contract.normalize_register_result(body, result) == result
    abody = readmit(refund=True)
    assert contract.normalize_readmit_result(abody, admission(abody))
