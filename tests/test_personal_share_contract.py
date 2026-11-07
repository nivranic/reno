"""纯摘要合同回归；不创建链接、凭据、存储、运行时授权或撤销证明。"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from reno import personal_share_contract as share


def decision(**changes):
    return {"title": "  防水决定 <b>原文</b>  ", "decision_text": "\n**保留正文** 与 emoji 🙂\n",
            "decision_reason": "  原理由、未脱敏电话属于本人原文  ", "state": "adopted",
            "revision": 7, "updated_at": "2026-10-06T12:00:00.123Z", "needs_recheck": True,
            **changes}


def snapshot(*rows):
    return {"format_version": 1, "kind": "personal_decision_share_snapshot",
            "decisions": list(rows or (decision(),))}


class PrivateSentinel:
    def __deepcopy__(self, memo):
        raise AssertionError("private value must not be copied")

    def __repr__(self):
        raise AssertionError("private value must not be formatted")

    def __str__(self):
        raise AssertionError("private value must not be rendered")

    def __iter__(self):
        raise AssertionError("private value must not be traversed")


def test_projection_only_reads_seven_values_and_keeps_private_sentinels_unvisited():
    private = PrivateSentinel()
    source = {**decision(), "project_id": private, "context_snapshot": private,
              "content_snapshot": private, "evidence_refs": private, "attachments": private,
              "video_id": private, "alternatives": private, "selected_option_id": private,
              "card_id": private, "arbitrary_private_value": private}
    projected = share.project_decision(source)
    assert projected == decision() and tuple(projected) == share.DECISION_FIELDS
    frozen = share.project_snapshot([source])
    assert share.decode_snapshot(share.encode_snapshot(frozen)) == snapshot(decision())
    assert all(source[key] is private for key in source if key not in share.DECISION_FIELDS)


def test_projection_preserves_original_text_and_has_independent_containers():
    source = decision()
    original = deepcopy(source)
    rows = [source]
    projected = share.project_snapshot(rows)
    normalized = share.normalize_snapshot(projected)
    assert source == original and rows == [original]
    assert projected["decisions"][0] == original
    assert normalized == projected and normalized is not projected
    assert normalized["decisions"] is not projected["decisions"]
    assert normalized["decisions"][0] is not projected["decisions"][0]
    source["title"] = "后来的编辑"
    rows.append(decision())
    projected["decisions"][0]["decision_reason"] = "另一次编辑"
    assert normalized == snapshot(original)


def test_original_canonical_bytes_digest_order_and_duplicates_are_stable():
    first, second = decision(title="甲"), decision(title="乙")
    value = snapshot(first, second)
    raw = share.encode_snapshot(value)
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    assert raw == expected and share.decode_snapshot(raw) == value
    assert share.encode_snapshot(share.decode_snapshot(raw)) == raw
    assert share.snapshot_sha256(raw) == hashlib.sha256(raw).hexdigest()
    assert share.snapshot_sha256(share.encode_snapshot(snapshot(second, first))) != share.snapshot_sha256(raw)
    duplicates = snapshot(first, first)
    assert share.decode_snapshot(share.encode_snapshot(duplicates))["decisions"] == [first, first]
    assert duplicates["decisions"][0] is duplicates["decisions"][1]


@pytest.mark.parametrize("change,code", [
    ({"format_version": True}, "SHARE_VERSION_OR_KIND_INVALID"),
    ({"format_version": 2}, "SHARE_VERSION_OR_KIND_INVALID"),
    ({"kind": "future_kind"}, "SHARE_VERSION_OR_KIND_INVALID"),
    ({"decisions": ()}, "SHARE_DECISION_COUNT_INVALID"),
    ({"decisions": []}, "SHARE_DECISION_COUNT_INVALID"),
    ({"decisions": [decision()] * 51}, "SHARE_DECISION_COUNT_INVALID"),
    ({"access_token": "excluded"}, "SHARE_FIELDS_INVALID"),
])
def test_snapshot_envelope_and_count_are_closed(change, code):
    value = snapshot()
    value.update(change)
    with pytest.raises(share.ShareContractError, match=code):
        share.normalize_snapshot(value)


@pytest.mark.parametrize("field,value,code", [
    ("title", "", "SHARE_TITLE_REQUIRED"), ("title", " \n\t", "SHARE_TITLE_REQUIRED"),
    ("title", "字" * 201, "SHARE_TEXT_INVALID"), ("decision_text", "字" * 4001, "SHARE_TEXT_INVALID"),
    ("decision_reason", "字" * 4001, "SHARE_TEXT_INVALID"), ("title", 7, "SHARE_TEXT_INVALID"),
    ("decision_text", None, "SHARE_TEXT_INVALID"), ("decision_reason", "\x00", "SHARE_TEXT_INVALID"),
    ("decision_text", "\ud800", "SHARE_TEXT_INVALID"), ("title", "\udfff", "SHARE_TEXT_INVALID"),
    ("state", True, "SHARE_STATE_INVALID"), ("state", "finished", "SHARE_STATE_INVALID"),
    ("revision", True, "SHARE_REVISION_INVALID"), ("revision", 1.0, "SHARE_REVISION_INVALID"),
    ("revision", 0, "SHARE_REVISION_INVALID"), ("revision", 1 << 63, "SHARE_REVISION_INVALID"),
    ("needs_recheck", 1, "SHARE_RECHECK_INVALID"),
    ("updated_at", "2026-10-06T12:00:00+00:00", "SHARE_TIMESTAMP_INVALID"),
    ("updated_at", "2026-10-06T12:00:00.1234567Z", "SHARE_TIMESTAMP_INVALID"),
    ("updated_at", "2026-02-30T12:00:00Z", "SHARE_TIMESTAMP_INVALID"),
    ("updated_at", "2026-10-06T24:00:00Z", "SHARE_TIMESTAMP_INVALID"),
])
def test_strict_decision_types_text_and_existing_utc_shape(field, value, code):
    row = decision(**{field: value})
    original = deepcopy(row)
    with pytest.raises(share.ShareContractError, match=code):
        share.normalize_snapshot(snapshot(row))
    assert row == original


@pytest.mark.parametrize("stamp", ["2026-10-06T12:00:00Z", "2026-10-06T12:00:00.1Z",
                                   "2026-10-06T12:00:00.123456Z", "2099-12-31T23:59:59.999Z"])
def test_timestamp_is_a_format_declaration_without_current_time_judgment(stamp):
    row = decision(updated_at=stamp, revision=(1 << 63) - 1, decision_text="", decision_reason="")
    assert share.project_decision(row) == row


@pytest.mark.parametrize("field", share.DECISION_FIELDS)
def test_missing_summary_field_is_never_filled(field):
    row = decision()
    row.pop(field)
    with pytest.raises(share.ShareContractError, match="SHARE_FIELDS_INVALID"):
        share.project_decision(row)
    with pytest.raises(share.ShareContractError, match="SHARE_FIELDS_INVALID"):
        share.normalize_snapshot(snapshot(row))


def test_normalization_rejects_extra_private_fields_and_exact_types():
    with pytest.raises(share.ShareContractError, match="SHARE_FIELDS_INVALID"):
        share.normalize_snapshot(snapshot({**decision(), "project_id": "private"}))
    class SourceSubclass(dict):
        def __getitem__(self, key):
            raise AssertionError("custom getter is never admitted")
    with pytest.raises(share.ShareContractError, match="SHARE_SOURCE_INVALID"):
        share.project_decision(SourceSubclass(decision()))
    for value in (None, [], (), SourceSubclass(snapshot())):
        with pytest.raises(share.ShareContractError, match="SHARE_FIELDS_INVALID"):
            share.normalize_snapshot(value)
    value = snapshot()
    value.pop("kind")
    with pytest.raises(share.ShareContractError, match="SHARE_FIELDS_INVALID"):
        share.normalize_snapshot(value)


@pytest.mark.parametrize("fill", ["𠮷", "🙂", "\x01"])
def test_fifty_maximum_length_summaries_fit_the_wire_cap_including_escapes(fill):
    row = decision(title=fill * 200, decision_text=fill * 4000, decision_reason=fill * 4000)
    value = snapshot(*[dict(row) for _ in range(50)])
    raw = share.encode_snapshot(value)
    assert len(raw) < 3 * 1024 * 1024
    if fill == "\x01":
        assert len(raw) > 2 * 1024 * 1024
    assert share.decode_snapshot(raw) == value


@pytest.mark.parametrize("change,code", [
    (lambda raw: b" " + raw, "SHARE_NONCANONICAL"),
    (lambda raw: raw + b"\n", "SHARE_NONCANONICAL"),
    (lambda raw: raw.replace(b'"format_version":1', b'"format_version":1.0'), "SHARE_JSON_INVALID"),
    (lambda raw: raw.replace(b'"format_version":1', b'"format_version":NaN'), "SHARE_JSON_INVALID"),
    (lambda raw: raw.replace(b'"revision":7', b'"revision":12345678901234567890'), "SHARE_INTEGER_INVALID"),
    (lambda raw: raw[:-1] + b',"format_version":1}', "SHARE_DUPLICATE_FIELD"),
    (lambda raw: raw.replace(b'"revision":7', b'"revision":7,"revision":7'), "SHARE_DUPLICATE_FIELD"),
    (lambda raw: b"\xff" + raw, "SHARE_JSON_INVALID"),
    (lambda raw: raw.replace(b'"revision":7', b'"revision":'), "SHARE_JSON_INVALID"),
])
def test_decode_and_sha_refuse_changed_noncanonical_or_malformed_original_bytes(change, code):
    raw = change(share.encode_snapshot(snapshot()))
    for consumer in (share.decode_snapshot, share.snapshot_sha256):
        with pytest.raises(share.ShareContractError, match=code):
            consumer(raw)


@pytest.mark.parametrize("raw,code", [
    (b"", "SHARE_BYTES_LIMIT"), ("{}", "SHARE_BYTES_LIMIT"), (bytearray(b"{}"), "SHARE_BYTES_LIMIT"),
    (b"x" * (3 * 1024 * 1024 + 1), "SHARE_BYTES_LIMIT"),
    (b"[[[[0]]]]", "SHARE_JSON_LIMIT"), (b"[" + b"0," * 352 + b"0]", "SHARE_JSON_LIMIT"),
], ids=["empty", "text", "bytearray", "oversized", "depth", "comma"])
def test_resource_abuse_is_rejected_before_the_json_parser(raw, code, monkeypatch):
    def never_parse(*args, **kwargs):
        raise AssertionError("preflight must reject before parser allocation")
    monkeypatch.setattr(share.json, "loads", never_parse)
    with pytest.raises(share.ShareContractError, match=code):
        share.decode_snapshot(raw)


def test_scalar_brackets_commas_and_escapes_do_not_consume_container_budget():
    row = decision(decision_text=('\\"[,]{}' * 500))
    value = snapshot(row)
    assert share.decode_snapshot(share.encode_snapshot(value)) == value


def test_state_whitelist_matches_current_personal_source_without_importing_it():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "reno/personal.py").read_text(encoding="utf-8"))
    declared = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "CARD_STATES" for target in node.targets)]
    assert declared == [set(share.CARD_STATES)]


def test_snapshot_contains_no_credential_authorization_or_revocation_result_fields():
    value = share.project_snapshot([decision()])
    assert set(value) == {"format_version", "kind", "decisions"}
    assert set(value["decisions"][0]) == set(share.DECISION_FIELDS)
    assert share.normalize_snapshot(value) == value
