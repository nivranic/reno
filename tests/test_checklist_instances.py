"""Field checks preserve observations while enforcing versioned criteria."""
import json
import hashlib
import uuid

import pytest

from reno import config, db, personal
from reno import checklists


def envelope(changes, revision=None, op=None):
    return {"operation_id": op or str(uuid.uuid4()), "primary_epoch": 1,
            **({"base_revision": revision} if revision else {}), "changes": changes}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setenv("RENO_DATA_DIR", str(tmp_path / "data"))
    local = config.local_path()
    local.write_text(json.dumps({"personal_features_enabled": True, "field_checks_enabled": True}), encoding="utf-8")
    config._cache = None
    db.initialize()
    from reno import storage_admin
    storage_admin.setup()
    con = db.connect()
    try:
        return personal.default_project(con)["project"]["project_id"]
    finally:
        con.close()


def requirement(**extra):
    return {"title": "个人约定的照片记录", "basis_kind": "personal_requirement",
            "method_text": "观察并记录施工现场", "conditions": {"空间": "卫生间"},
            "criteria_state": "known", "criteria": {"text": "管根封闭前留存照片",
            "conditions_confirmed": True, "requirement_confirmed": True,
            "required_observations": [{"key": "photo_note", "label": "照片记录情况", "kind": "text"}]}, **extra}


def create(project, item=None):
    return checklists.create(project, envelope({"title": "卫生间现场记录", "space": "卫生间",
                                               "items": [item or requirement()]}))[0]["checklist"]


def observe(item, result="pass", **extra):
    return checklists.append_check(item["item_id"], envelope({"record_kind": "inspection", "result": result,
        "measurements": [{"key": "photo_note", "value": "观察记录完整"}], **extra}, item["revision"]))[0]


def read(instance):
    con = db.connect()
    try:
        return checklists.detail(con, instance["instance_id"])
    finally:
        con.close()


def test_unknown_and_video_criteria_cannot_pass_but_observations_are_kept(project):
    for basis in ("observation_only", "video_suggestion"):
        instance = create(project, requirement(basis_kind=basis))
        item = instance["items"][0]
        with pytest.raises(personal.PersonalError) as err:
            observe(item)
        assert err.value.code == "CRITERIA_INCOMPLETE"
        result = observe(item, "unable_to_verify", note="只有视频经验，未核对标准")
        assert result["item"]["current_result"] == "unable_to_verify"
        assert result["checklist"]["state"] == "active"


def test_fail_remediation_reinspection_and_void_keep_history(project):
    instance = create(project)
    item = instance["items"][0]
    failed = observe(item, "fail", note="封闭前未记录")
    rem = checklists.append_check(item["item_id"], envelope({"record_kind": "remediation", "note": "已补充记录"}, 1))[0]
    assert rem["item"]["current_result"] == "fail"
    passed = observe(item, checked_at="2026-01-02T08:00:00Z")
    # A backdated observation cannot override the newer failed observation.
    assert passed["item"]["current_result"] == "fail"
    latest = observe(item)
    assert latest["checklist"]["state"] == "completed"
    assert len(latest["item"]["checks"]) == 4
    request = envelope({"record_kind": "void", "supersedes_check_id": latest["check"]["check_id"], "note": "录入错误"}, 1)
    voided = checklists.append_check(item["item_id"], request)
    assert voided[0]["item"]["current_result"] == "fail"
    assert checklists.append_check(item["item_id"], request) == voided
    with pytest.raises(personal.PersonalError, match="作废") as err:
        checklists.append_check(item["item_id"], {**request, "operation_id": str(uuid.uuid4())})
    assert err.value.code == "ALREADY_VOIDED"
    assert failed["check"]["check_id"] in {c["check_id"] for c in read(instance)["items"][0]["checks"]}


def test_same_observation_time_uses_commit_sequence_and_item_revision_resets(project):
    instance = create(project)
    item = instance["items"][0]
    first = observe(item, "fail", checked_at="2026-01-01T00:00:00Z")
    last = observe(item, checked_at="2026-01-01T00:00:00Z")
    assert last["check"]["commit_seq"] > first["check"]["commit_seq"]
    assert last["item"]["current_result"] == "pass"
    updated = checklists.update_item(item["item_id"], envelope({"method_text": "观察并与当天照片核对"}, 1))[0]["item"]
    assert updated["revision"] == 2 and updated["current_result"] == "unchecked"
    assert len(updated["checks"]) == 2
    assert updated["criteria_snapshot_hash"] != item["criteria_snapshot_hash"]
    assert read(instance)["state"] == "active"
    with pytest.raises(personal.PersonalError) as err:
        observe(item)
    assert err.value.code == "REVISION_CONFLICT"
    with pytest.raises(personal.PersonalError):
        checklists.append_check(item["item_id"], envelope({"record_kind": "void", "supersedes_check_id": last["check"]["check_id"], "note": "不能作废旧版本"}, 2))


def test_known_criteria_require_specific_observations_and_finite_numbers(project):
    item = create(project)["items"][0]
    with pytest.raises(personal.PersonalError) as err:
        observe(item, measurements=[], note="不能靠泛泛备注绕过必需观察")
    assert err.value.code == "CRITERIA_INCOMPLETE"
    with pytest.raises(personal.PersonalError):
        observe(item, measurements=[{"key": "photo_note", "value": float("nan")}])
    with pytest.raises(personal.PersonalError):
        observe(item, checked_at="2999-01-01T00:00:00Z")
    with pytest.raises(personal.PersonalError):
        observe(item, checked_at="2026-01-01T00:00:00")
    with pytest.raises(personal.PersonalError):
        observe(item, "not_applicable", note="")


def test_personal_requirement_conflict_blocks_pass_and_resolution_requires_new_check(project):
    instance = create(project)
    item = instance["items"][0]
    passed = observe(item)
    assert passed["checklist"]["state"] == "completed"
    criteria = {**item["criteria"], "unresolved_standard_conflict": True}
    disputed = checklists.update_item(item["item_id"], envelope({"criteria": criteria}, 1))[0]["item"]
    assert disputed["revision"] == 2 and disputed["current_result"] == "unchecked"
    assert read(instance)["state"] == "active"
    with pytest.raises(personal.PersonalError) as error:
        observe(disputed)
    assert error.value.code == "CRITERIA_INCOMPLETE"
    assert "与规范依据的分歧尚未核实" in str(error.value)
    recorded = observe(disputed, "unable_to_verify", note="个人要求与已有规范分歧，待核实")
    assert recorded["item"]["current_result"] == "unable_to_verify"
    resolved = checklists.update_item(item["item_id"], envelope({"criteria": {
        **criteria, "unresolved_standard_conflict": False}}, 2))[0]["item"]
    assert resolved["revision"] == 3 and resolved["current_result"] == "unchecked"
    assert read(instance)["state"] == "active"
    assert observe(resolved)["checklist"]["state"] == "completed"
    history = read(instance)["items"][0]["checks"]
    assert passed["check"]["check_id"] in {entry["check_id"] for entry in history}
    assert len(history) == 3


def test_archived_list_rejects_new_facts_until_explicit_revisioned_restore(project):
    instance = create(project)
    item = instance["items"][0]
    archived = checklists.update(instance["instance_id"], envelope({"lifecycle_state": "archived"}, 1))[0]["checklist"]
    assert archived["state"] == "archived"
    with pytest.raises(personal.PersonalError) as err:
        observe(item)
    assert err.value.code == "CHECKLIST_ARCHIVED"
    with pytest.raises(personal.PersonalError):
        checklists.update_item(item["item_id"], envelope({"title": "改名"}, 1))
    with pytest.raises(personal.PersonalError) as err:
        checklists.update(instance["instance_id"], envelope({"title": "仍不可改名"}, 2))
    assert err.value.code == "CHECKLIST_ARCHIVED"
    with pytest.raises(personal.PersonalError):
        checklists.update(instance["instance_id"], envelope({"state": "completed"}, 2))
    checklists.update(instance["instance_id"], envelope({"lifecycle_state": "active"}, 2))
    assert observe(item)["checklist"]["state"] == "completed"


def standard(project):
    return personal.create_source(project, envelope({"title": "人工验收标准样例", "source_type": "standard_record",
        "publisher": "测试机构", "document_no": "QA-1", "edition": "2026", "locator": "3.1", "excerpt": "管根封闭前留存照片",
        "scope": {"空间": "卫生间"}, "verification_state": "user_checked", "validity": "current",
        "checked_at": "2026-01-01T00:00:00Z", "check_note": "人工样例，不能作为现实工程依据"}))[0]["source"]


def test_source_change_invalidates_completion_without_changing_old_facts(project):
    source = standard(project)
    refs = [{"target_kind": "source", "target_id": source["source_id"], "target_fingerprint": source["target_fingerprint"]}]
    instance = create(project, requirement(basis_kind="standard_clause", refs=refs))
    item = instance["items"][0]
    assert observe(item)["checklist"]["state"] == "completed"
    personal.update_source(source["source_id"], envelope({"validity": "superseded"}, 1))
    current = read(instance)
    assert current["state"] == "active"
    assert current["items"][0]["current_result"] == "pass"
    assert current["items"][0]["needs_recheck"] is True
    assert current["items"][0]["checks"][0]["result"] == "pass"
    with pytest.raises(personal.PersonalError) as err:
        observe(item)
    assert err.value.code == "CRITERIA_INCOMPLETE"


def test_source_scope_unknown_or_mismatched_cannot_be_marked_pass(project):
    source = standard(project)
    refs = [{"target_kind": "source", "target_id": source["source_id"], "target_fingerprint": source["target_fingerprint"]}]
    item = create(project, requirement(basis_kind="standard_clause", conditions={"空间": "厨房"}, refs=refs))["items"][0]
    with pytest.raises(personal.PersonalError) as err:
        observe(item)
    assert err.value.code == "CRITERIA_INCOMPLETE"


def test_arbitrary_browser_threshold_cannot_be_labeled_as_a_standard(project):
    source = standard(project)
    refs = [{"target_kind": "source", "target_id": source["source_id"], "target_fingerprint": source["target_fingerprint"]}]
    altered = requirement(basis_kind="standard_clause", refs=refs)
    altered["criteria"]["text"] = "任意浏览器填写的工程合格阈值"
    item = create(project, altered)["items"][0]
    with pytest.raises(personal.PersonalError) as err:
        observe(item)
    assert err.value.code == "CRITERIA_INCOMPLETE"
    assert "原文摘录" in str(err.value)


def test_create_idempotent_and_disabled_field_gate(project):
    request = envelope({"title": "现场记录", "items": [requirement()]})
    first = checklists.create(project, request)
    assert checklists.create(project, request) == first
    local = config.local_path()
    local.write_text(json.dumps({"personal_features_enabled": True, "field_checks_enabled": False}), encoding="utf-8")
    config._cache = None
    with pytest.raises(personal.PersonalError) as err:
        checklists.create(project, request)
    assert err.value.code == "CAPABILITY_DISABLED"


def test_missing_current_inspection_attachment_reopens_completion(project):
    source = personal.create_source(project, envelope({"title": "现场照片", "source_type": "site_photo"}))[0]["source"]
    aid, op = str(uuid.uuid4()), str(uuid.uuid4())
    path = config.DATA / "personal-attachments" / f"{aid}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test attachment content")
    con = db.connect()
    try:
        con.execute("INSERT INTO source_attachment(attachment_id,source_id,project_id,sha256,byte_size,media_type,original_name,storage_key,state,created_at,operation_id) "
                    "VALUES(?,?,?,?,?,'image/png','test.png',?,'ready','2026-01-01T00:00:00Z',?)",
                    (aid, source["source_id"], project, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.name, op))
        con.commit()
    finally:
        con.close()
    instance = create(project)
    item = instance["items"][0]
    assert observe(item, attachment_refs=[aid])["checklist"]["state"] == "completed"
    path.unlink()
    current = read(instance)
    assert current["state"] == "active"
    assert current["items"][0]["current_result"] == "pass"
    assert current["items"][0]["needs_recheck"]
    assert current["items"][0]["checks"][0]["attachment_issues"] == [aid]


def test_expected_criteria_hash_and_numeric_types_are_enforced(project):
    definition = requirement()
    definition["criteria"]["required_observations"] = [{"key": "length", "label": "测试长度", "kind": "number", "unit": "mm", "method": "钢尺"}]
    item = create(project, definition)["items"][0]
    with pytest.raises(personal.PersonalError) as err:
        observe(item, "unable_to_verify", criteria_snapshot_hash="old")
    assert err.value.code == "EVIDENCE_CHANGED"
    with pytest.raises(personal.PersonalError):
        observe(item, "unable_to_verify", measurements=[{"key": "length", "value": "10", "unit": "mm"}])
    observation = {"key": "length", "value": 10, "unit": "mm", "method": "钢尺", "measured_at": "2026-01-01T00:00:00Z"}
    assert observe(item, measurements=[observation])["item"]["current_result"] == "pass"
