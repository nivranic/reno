"""Pure source proofs contain no configuration, provider text, or processes."""
from dataclasses import replace
import json
import uuid

import pytest

from reno import source_contract as contract


def plan():
    return contract.SourcePlan("metadata", "https://synthetic.invalid/private-source")


def failure(**changes):
    value = contract.SourceProcessResult(plan().request_sha256, True, True, 1, "exited", failure_class="process_failure")
    return replace(value, **changes)


def validate(text):
    value = plan()
    return contract.validate_retry_envelope(text, request_sha256=value.request_sha256,
        input_sha256=value.input_sha256, kind=value.kind, segment=value.segment)


def test_normal_complete_positive_failure_roundtrips_as_retry_proof():
    value = failure()
    assert validate(contract.encode_envelope(plan(), value)) == value


@pytest.mark.parametrize("changes", [{"returncode": 0, "failure_class": "invalid_output"},
    {"termination": "timeout"}, {"drain_complete": False}, {"returncode": -1},
    {"stdout_bytes": contract.STDOUT_LIMIT + 1, "stdout_sha256": "a" * 64}])
def test_incomplete_killed_overflow_or_zero_exit_cannot_authorize_retry(changes):
    with pytest.raises(contract.SourceContractError):
        validate(contract.encode_envelope(plan(), failure(**changes)))


@pytest.mark.parametrize("mutation", [{"returncode": True}, {"format_version": True},
                                      {"termination": "stderr_503"}, {"private_stderr": "credential"}])
def test_unknown_fields_and_boolean_integer_proofs_are_rejected(mutation):
    raw = json.loads(contract.encode_envelope(plan(), failure()))
    raw["result"].update(mutation)
    with pytest.raises(contract.SourceContractError):
        validate(contract.canonical_json(raw))


def test_envelope_checks_canonical_identity_and_genesis_override():
    value = plan()
    text = contract.encode_envelope(value, failure(), input_sha256="b" * 64)
    assert contract.decode_envelope(text, request_sha256=value.request_sha256,
        input_sha256="b" * 64, kind=value.kind, segment=None).returncode == 1
    with pytest.raises(contract.SourceContractError):
        validate(text)
    with pytest.raises(contract.SourceContractError):
        validate(contract.encode_envelope(value, failure()) + "\n")


def test_refresh_request_changes_but_genesis_source_input_does_not():
    initial = plan()
    refreshed = replace(initial, phase="refresh", refresh_attempt_id=str(uuid.uuid4()), refresh_settlement_sha256="c" * 64)
    assert refreshed.input_sha256 == initial.input_sha256
    assert refreshed.request_sha256 != initial.request_sha256
    assert "private-source" not in repr(initial)


@pytest.mark.parametrize("value", ["../escape", "C:/private", "a\\b", "a//b", "a/./b", "a/b."])
def test_artifacts_only_accept_canonical_private_relative_paths(value):
    with pytest.raises(contract.SourceContractError):
        contract.SourceArtifact("video", value, 1, "d" * 64, 1, 2, 3)


def test_complete_artifact_rejects_zero_but_owned_partial_can_record_zero():
    with pytest.raises(contract.SourceContractError):
        contract.SourceArtifact("video", "originals/a/video.mp4", 0, contract.EMPTY_SHA256, 1, 2, 3)
    assert contract.SourcePartial("video", "originals/a/video.mp4.part", 0, contract.EMPTY_SHA256, 1, 2, 3, "e" * 64).size_bytes == 0


def test_empty_enumeration_is_a_complete_success():
    value = contract.SourcePlan("enumeration", "https://synthetic.invalid/list")
    result = contract.SourceProcessResult(value.request_sha256, True, True, 0, "exited", items=())
    assert contract.decode_envelope(contract.encode_envelope(value, result), request_sha256=value.request_sha256,
        input_sha256=value.input_sha256, kind=value.kind, segment=None).successful


@pytest.mark.parametrize("key", ["Cookie", "Authorization", "X-Api-Key"])
def test_nested_credentials_are_not_copied_into_metadata(key):
    metadata = {"id": "synthetic", "formats": [{"url": "https://synthetic.invalid/media?signature=private",
        "http_headers": {key: "must-not-be-stored"}}]}
    with pytest.raises(contract.SourceContractError):
        contract.sanitize_metadata(metadata)


def test_signed_urls_are_private_and_safe_headers_remain_usable():
    metadata = {"id": "synthetic", "formats": [{"url": "https://synthetic.invalid/media?signature=private",
        "http_headers": {"User-Agent": "synthetic", "Referer": "https://synthetic.invalid"}}], "unneeded": "discard"}
    cleaned = contract.sanitize_metadata(metadata)
    assert "unneeded" not in cleaned and cleaned["formats"][0]["http_headers"]["User-Agent"] == "synthetic"


def test_deep_or_non_json_proof_is_a_controlled_contract_error():
    for text in ("[" * 4000 + "0" + "]" * 4000, "not json"):
        with pytest.raises(contract.SourceContractError):
            validate(text)


@pytest.mark.parametrize("build", [
    lambda: contract.SourcePlan("metadata", "https://synthetic.invalid", phase=[]),
    lambda: contract.SourcePlan("download", "https://synthetic.invalid", segment=[]),
    lambda: contract.SourceArtifact([], "originals/a/video.mp4", 1, "a" * 64, 1, 2, 3),
    lambda: replace(failure(), termination=[]),
    lambda: replace(failure(), failure_class={}),
])
def test_unhashable_closed_enums_fail_with_controlled_contract_error(build):
    with pytest.raises(contract.SourceContractError):
        build()


@pytest.mark.parametrize("metadata", [{"id": "synthetic", "title": "\ud800"},
    {"id": "synthetic", "http_headers": {"User-Agent": "\ud800"}}])
def test_unpaired_surrogates_in_metadata_and_headers_are_controlled(metadata):
    with pytest.raises(contract.SourceContractError):
        contract.sanitize_metadata(metadata)


def test_unpaired_surrogate_in_enumeration_title_is_controlled():
    with pytest.raises(contract.SourceContractError):
        replace(failure(), items=(("synthetic", "\ud800"),))


@pytest.mark.parametrize("kind", ["source_metadata", "download"])
@pytest.mark.parametrize("phase", ["initial", "refresh"])
def test_refresh_phase_and_failure_authorization_are_a_closed_pair(kind, phase):
    extra = {} if phase == "refresh" else {"refresh_attempt_id": str(uuid.uuid4()), "refresh_settlement_sha256": "f" * 64}
    if kind == "download":
        extra.update(segment="video", artifact_relpath="originals/synthetic/source-a/video.mp4",
                     info_json=contract.canonical_json({"id": "synthetic", "url": "https://synthetic.invalid/media"}))
    with pytest.raises(contract.SourceContractError):
        contract.SourcePlan(kind, "https://synthetic.invalid/source", phase=phase, **extra)
