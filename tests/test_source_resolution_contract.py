"""Pure resolver facts; no sockets, configuration, database or subprocess."""
from dataclasses import replace
import json
import uuid

import pytest

from reno import source_resolution_contract as codec

URL = "https://www.bilibili.com/video/BV13x41117TL?p=2"
SHORT = "https://b23.tv/short?x=1&x=2"
STAMP = "2026-10-02T00:00:00.000000Z"
INPUT = "1" * 64
SETTLEMENT = "2" * 64
ATTEMPT = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))


def plan(phase="dns", **kwargs):
    values = dict(phase=phase, submitted_url=URL, current_url=URL, input_sha256=INPUT, max_attempts=24)
    if phase == "headers":
        values.update(dns_attempt_id=ATTEMPT, dns_settlement_sha256=SETTLEMENT, peer_ip="8.8.8.8")
    return codec.ResolutionPlan(**{**values, **kwargs})


def dns_result(value, **kwargs):
    return codec.ResolutionResult(value.request_sha256, "dns", True, "completed", addresses=("8.8.8.8",), observed_at=STAMP, **kwargs)


def header_result(value, status=200, **kwargs):
    return codec.ResolutionResult(value.request_sha256, "headers", True, "completed", headers_complete=True,
        transport_closed=True, status=status, observed_at=STAMP, **kwargs)


def restore(value, result, *, retry=False):
    text = codec.encode_envelope(value, result)
    call = codec.validate_retry_envelope if retry else codec.decode_envelope
    return call(text, request_sha256=value.request_sha256, input_sha256=INPUT, phase=value.phase, max_attempts=value.max_attempts)


def test_pure_dns_and_headers_roundtrip_and_plan_identity():
    for value, result in ((plan(), dns_result(plan())), (plan("headers"), header_result(plan("headers")))):
        text = codec.encode_envelope(value, result)
        assert restore(value, result) == result
        assert codec.decode_plan(text, request_sha256=value.request_sha256, input_sha256=INPUT,
            phase=value.phase, max_attempts=24) == value
        assert value.kind == "source_resolution" and result.response_complete and result.successful
        assert URL not in repr(value) and URL not in repr(result)


@pytest.mark.parametrize("case", ["success", "404", "timeout", "cancelled", "dns_rejected"])
def test_only_closed_normal_failure_can_authorize_resolution_retry(case):
    value = plan("headers") if case in {"success", "404"} else plan()
    if case == "success":
        result = header_result(value)
    elif case == "404":
        result = header_result(value, 404, failure_class="http_status")
    elif case == "dns_rejected":
        result = codec.ResolutionResult(value.request_sha256, "dns", True, "completed", observed_at=STAMP,
            failure_class="dns_rejected", dns_error="non_public")
    else:
        result = codec.ResolutionResult(value.request_sha256, "dns", False, case)
    with pytest.raises(codec.ResolutionContractError):
        restore(value, result, retry=True)


@pytest.mark.parametrize("status", [408, 429, 500, 503, 599])
def test_complete_http_retry_is_headers_only_not_a_full_body_claim(status):
    value = plan("headers")
    result = header_result(value, status, failure_class="http_status", retry_after_seconds=10)
    assert restore(value, result, retry=True) == result
    raw = json.loads(codec.encode_envelope(value, result))
    assert "body" not in raw["result"] and "headers" not in raw["result"]


def test_complete_dns_lookup_failure_is_independent_of_http_retry():
    value = plan()
    result = codec.ResolutionResult(value.request_sha256, "dns", True, "completed", failure_class="dns_failure",
                                   dns_error="temporary", observed_at=STAMP)
    assert restore(value, result, retry=True) == result and result.status is None


@pytest.mark.parametrize("failure,error", [("dns_failure", "non_public"), ("dns_rejected", "temporary")])
def test_dns_safety_rejection_cannot_be_relabelled_as_a_normal_lookup_failure(failure, error):
    with pytest.raises(codec.ResolutionContractError):
        codec.ResolutionResult(plan().request_sha256, "dns", True, "completed", failure_class=failure,
                               dns_error=error, observed_at=STAMP)


@pytest.mark.parametrize("change", [{"max_attempts": True}, {"hop_index": True}, {"generation": True},
    {"phase": []}, {"current_url": "https://www.douyin.com/video/123"},
    {"current_url": "http://www.bilibili.com/video/BV13x41117TL?p=2"},
    {"current_url": "https://user:private@www.bilibili.com/video/BV13x41117TL"},
    {"current_url": "https://www.bilibili.com/%25255cprivate"},
    {"generation": 1}, {"previous_attempt_id": ATTEMPT}, {"input_sha256": "wrong"}])
def test_invalid_plan_or_authority_shape_has_only_controlled_error(change):
    with pytest.raises(codec.ResolutionContractError) as caught:
        plan(**change)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("change", [{"status": True}, {"event_complete": 1}, {"transport_closed": 0},
    {"termination": []}, {"failure_class": {}}, {"header_bytes": True}, {"retry_after_seconds": True},
    {"observed_at": "2026-10-02T00:00:00Z"}, {"next_url": "https://www.bilibili.com/private\\path"}])
def test_invalid_result_types_do_not_coerce(change):
    value = plan("headers")
    with pytest.raises(codec.ResolutionContractError):
        replace(header_result(value), **change)


@pytest.mark.parametrize("addresses", [("8.8.8.8", "127.0.0.1"), ("::ffff:8.8.8.8",),
    ("2002:0808:0808::",), ("8.8.8.8", "8.8.8.8"), ([],), (), ("224.0.0.1",)])
def test_dns_proof_rejects_non_public_duplicates_or_malformed_addresses(addresses):
    with pytest.raises(codec.ResolutionContractError):
        replace(dns_result(plan()), addresses=addresses)


@pytest.mark.parametrize("mutation", ["extra", "version", "cap", "phase", "request", "utf8", "duplicate"])
def test_outer_envelope_is_closed_canonical_and_bound_to_frozen_operation(mutation):
    value = plan()
    text = codec.encode_envelope(value, dns_result(value))
    raw = json.loads(text)
    if mutation == "extra":
        raw["private"] = "ignored"
    elif mutation == "version":
        raw["format_version"] = True
    elif mutation == "cap":
        raw["max_attempts"] = 25
    elif mutation == "phase":
        raw["phase"] = "headers"
    elif mutation == "request":
        raw["plan"]["current_url"] = SHORT
    elif mutation == "utf8":
        text = "\ud800"
    elif mutation == "duplicate":
        text = '{"format_version":1,' + text[1:]
    if mutation not in {"utf8", "duplicate"}:
        text = codec.canonical_json(raw)
    with pytest.raises(codec.ResolutionContractError):
        codec.decode_envelope(text, request_sha256=value.request_sha256, input_sha256=INPUT, phase="dns", max_attempts=24)


def test_same_genesis_input_changes_only_actual_request_on_authorized_refresh():
    value = plan()
    refreshed = replace(value, generation=1, previous_attempt_id=ATTEMPT, previous_settlement_sha256=SETTLEMENT,
        refresh_attempt_id=ATTEMPT, refresh_settlement_sha256=SETTLEMENT)
    assert value.input_sha256 == refreshed.input_sha256 and value.request_sha256 != refreshed.request_sha256


def test_redirect_loop_platform_downgrade_and_hop_cap_cannot_encode_success():
    initial = plan("headers")
    targets = [URL, "https://www.douyin.com/video/123", URL.replace("https:", "http:")]
    for target in targets:
        result = header_result(initial, 302, next_url=target)
        with pytest.raises(codec.ResolutionContractError):
            codec.encode_envelope(initial, result)
    urls = tuple(f"https://www.bilibili.com/hop{i}" for i in range(5))
    limited = plan("headers", submitted_url=urls[0], current_url=URL, hop_index=5, visited_urls=urls)
    with pytest.raises(codec.ResolutionContractError):
        codec.encode_envelope(limited, header_result(limited, 302, next_url=SHORT))
