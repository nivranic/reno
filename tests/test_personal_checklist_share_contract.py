"""摘要白名单、原文与确定性字节；这些检查不授予真实分享权限。"""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import personal_checklist_share_contract as share
from tests.test_personal_share_contract import PrivateSentinel


def checklist():
    return {"title": "本人现场记录 <b>仅作原文</b>", "state": "active", "lifecycle_state": "active",
            "revision": 3, "updated_at": "2026-10-09T08:00:00.123Z", "progress": {"total": 2, "complete": 1},
            "items": [{"title": "已记录的本项", "revision": 2, "current_result": "pass",
                       "needs_recheck": False, "basis_kind": "personal_requirement"},
                      {"title": "仍需复核的本项", "revision": 1, "current_result": "pass",
                       "needs_recheck": True, "basis_kind": "standard_clause"}]}


def snapshot():
    return {"format_version": 1, "kind": share.SNAPSHOT_KIND, "checklists": [checklist()]}


def test_whitelist_projection_never_visits_private_values_and_detaches_containers():
    original = checklist()
    source = deepcopy(original)
    private = PrivateSentinel()
    source.update(project_id=private, instance_id=private, space=private, stage=private, created_at=private)
    source["progress"]["private"] = private
    for item in source["items"]:
        item.update(item_id=private, refs=private, checks=private, conditions=private,
                    criteria=private, current_check=private, method_text=private, attachments=private)
    value = share.project_snapshot([source])
    assert value == snapshot()
    source["title"] = "later"
    source["items"][0]["title"] = "later item"
    source["progress"]["complete"] = 0
    assert value["checklists"] == [original]
    normalized = share.normalize_snapshot(value)
    value["checklists"][0]["items"][0]["title"] = "another later edit"
    assert normalized == snapshot()


def test_canonical_bytes_roundtrip_digest_and_full_fifty_item_budget():
    source = checklist()
    source["title"] = "\x01" * 200
    source["items"] = [{**source["items"][0], "title": "\x01" * 200} for _ in range(50)]
    source.update(state="completed", progress={"total": 50, "complete": 50})
    value = share.project_snapshot([source])
    raw = share.encode_snapshot(value)
    assert raw == json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert len(raw) < share.MAX_SNAPSHOT_BYTES
    assert share.decode_snapshot(raw) == value
    assert share.snapshot_sha256(raw) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("change", [
    lambda value: value.update(format_version=True),
    lambda value: value.update(checklists=[]),
    lambda value: value["checklists"].append(checklist()),
    lambda value: value.update(share_token="unwanted"),
    lambda value: value["checklists"][0].update(project_id="unwanted"),
    lambda value: value["checklists"][0].update(revision=True),
    lambda value: value["checklists"][0].update(updated_at="2026-02-30T00:00:00Z"),
    lambda value: value["checklists"][0]["items"][0].update(title="\ud800"),
    lambda value: value["checklists"][0]["items"][0].update(needs_recheck=1),
    lambda value: value["checklists"][0]["items"][0].update(current_result="工程合格"),
    lambda value: value["checklists"][0]["items"][0].update(basis_kind="certified"),
    lambda value: value["checklists"][0]["items"][0].update(note="unwanted"),
    lambda value: value["checklists"][0].update(progress={"total": 2, "complete": 2}),
    lambda value: value["checklists"][0].update(progress={"total": 2, "complete": True}),
    lambda value: value["checklists"][0].update(state="completed"),
    lambda value: value["checklists"][0].update(lifecycle_state="archived"),
])
def test_closed_contract_rejects_unexpected_private_fields_types_and_false_completion(change):
    value = snapshot()
    change(value)
    with pytest.raises(share.ShareContractError):
        share.encode_snapshot(value)


@pytest.mark.parametrize("change", [
    lambda raw: b" " + raw,
    lambda raw: raw.replace(b'"format_version":1', b'"format_version":1,"format_version":1'),
    lambda raw: raw.replace(b'"revision":3', b'"revision":NaN'),
    lambda raw: raw.replace(b'"revision":3', b'"revision":3.0'),
    lambda raw: b"[" * 6 + raw,
    lambda raw: b"\xff" + raw,
])
def test_digest_never_accepts_noncanonical_duplicate_or_malformed_original_bytes(change):
    raw = change(share.encode_snapshot(snapshot()))
    with pytest.raises(share.ShareContractError):
        share.snapshot_sha256(raw)


def test_archived_and_not_applicable_preserve_original_domain_meaning():
    source = checklist()
    source["items"][1].update(current_result="not_applicable", needs_recheck=False)
    source.update(state="archived", lifecycle_state="archived", progress={"total": 2, "complete": 2})
    assert share.project_snapshot([source])["checklists"] == [source]
