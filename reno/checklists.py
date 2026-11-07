"""Versioned field criteria and append-only observations for a personal project.

The result is a user's record against ONE criterion, never certification of an
entire construction project. Old checks always retain their original version.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import uuid

from . import db, evidence_identity as identity, personal
from .review import utc_now

Error = personal.PersonalError
BASIS = {"standard_clause", "manufacturer_instruction", "personal_requirement", "video_suggestion", "observation_only"}
RESULTS = {"pass", "fail", "unable_to_verify", "not_applicable"}
PASS_LABELS = {"standard_clause": "符合所记录的本项判据", "manufacturer_instruction": "符合本说明书中的该项要求",
               "personal_requirement": "符合我的这项要求"}


def _time(value, field="checked_at"):
    if value is None:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError):
        raise Error(f"{field} 必须带时区，不能超过当前时间 5 分钟") from None


def _criteria(value):
    allowed = {"text", "conditions_confirmed", "requirement_confirmed", "required_observations", "product_model", "unresolved_standard_conflict"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise Error("criteria 字段无效")
    out = {"text": personal.text(value.get("text", ""), "判据", 8000),
           "product_model": personal.text(value.get("product_model", ""), "材料设备型号", 200)}
    for key in ("conditions_confirmed", "requirement_confirmed", "unresolved_standard_conflict"):
        if type(value.get(key, False)) is not bool:
            raise Error(f"{key} 必须是明确的布尔值")
        out[key] = value.get(key, False)
    required = value.get("required_observations", [])
    if not isinstance(required, list) or len(required) > 30:
        raise Error("最多设置 30 项必要观察")
    fields, seen = [], set()
    for row in required:
        if not isinstance(row, dict) or set(row) - {"key", "label", "kind", "unit", "method"}:
            raise Error("必要观察字段无效")
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in {"number", "text"}:
            raise Error("必要观察 kind 只接受 number/text")
        field = {"key": personal.text(row.get("key"), "观察字段 ID", 80, required=True),
                 "label": personal.text(row.get("label"), "观察名称", 200, required=True), "kind": kind,
                 "unit": personal.text(row.get("unit", ""), "单位", 80),
                 "method": personal.text(row.get("method", ""), "测量方法", 1000)}
        if field["key"] in seen:
            raise Error("必要观察 ID 不可重复")
        if kind == "number" and (not field["unit"] or not field["method"]):
            raise Error("数值观察必须注明单位和测量方法")
        seen.add(field["key"])
        fields.append(field)
    out["required_observations"] = fields
    return out


def _fields(changes, previous=None):
    allowed = {"title", "conditions", "method_text", "basis_kind", "criteria", "criteria_state", "position", "refs"}
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise Error("核对条目字段无效")
    values = {"conditions": {}, "method_text": "", "basis_kind": "observation_only", "criteria": {},
              "criteria_state": "needs_verification", "position": 0, **(previous or {}), **changes}
    if not isinstance(values["basis_kind"], str) or values["basis_kind"] not in BASIS:
        raise Error("判据类型无效")
    if not isinstance(values["criteria_state"], str) or values["criteria_state"] not in {"known", "needs_verification"}:
        raise Error("判据状态无效")
    if type(values["position"]) is not int or not 0 <= values["position"] <= 10000:
        raise Error("条目位置无效")
    return {"title": personal.text(values.get("title"), "标题", 200, required=True),
            "conditions_json": personal._json(personal.object_strings(values["conditions"], "适用条件")),
            "method_text": personal.text(values["method_text"], "检查方法", 8000), "basis_kind": values["basis_kind"],
            "criteria_json": personal._json(_criteria(values["criteria"])), "criteria_state": values["criteria_state"],
            "position": values["position"]}


def _item_snapshot(con, item_id):
    item = personal.require_entity(con, "checklist_item", item_id)
    item["conditions"] = json.loads(item.pop("conditions_json"))
    item["criteria"] = json.loads(item.pop("criteria_json"))
    # Freeze only the reference seen when the revision was written, not a
    # dynamically resolved current_snapshot that may change tomorrow.
    item["refs"] = [{k: v for k, v in ref.items() if k not in {"current_snapshot", "current_fingerprint", "binding_status", "status"}}
                    for ref in personal.list_refs(con, "checklist_item", item_id)]
    return item


def criteria_hash(item):
    return identity.fingerprint({key: item[key] for key in
        ("title", "conditions", "method_text", "basis_kind", "criteria", "criteria_state")} |
        {"refs": sorted(({"kind": r["target_kind"], "id": r["target_id"], "fingerprint": r["target_fingerprint"]}
                         for r in item["refs"]), key=lambda r: (r["kind"], r["id"]))})


def _measurements(value):
    if not isinstance(value, list) or len(value) > 30:
        raise Error("最多提交 30 个观察或实测值")
    out, seen = [], set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - {"key", "value", "unit", "method", "measured_at"}:
            raise Error("观察字段无效")
        key = personal.text(raw.get("key"), "观察 ID", 80, required=True)
        val = raw.get("value")
        if key in seen:
            raise Error("观察 ID 不可重复")
        if isinstance(val, str):
            val = personal.text(val, "观察内容", 4000, required=True)
        elif type(val) not in {int, float} or not math.isfinite(val):
            raise Error("实测值必须为有限数值或明确的文字观察")
        unit = personal.text(raw.get("unit", ""), "单位", 80)
        method = personal.text(raw.get("method", ""), "测量方法", 1000)
        if type(val) in {int, float} and (not unit or not method or not raw.get("measured_at")):
            raise Error("数值实测必须提供单位、测量方法和测量时间")
        out.append({"key": key, "value": val, "unit": unit, "method": method,
                    "measured_at": _time(raw["measured_at"], "measured_at") if raw.get("measured_at") else None})
        seen.add(key)
    return out


def can_mark_pass(item_version, current_sources=None, observations=None):
    """One rule used by response eligibility AND write validation.

    observations=None checks prerequisites for presenting the form. A submitted
    inspection always passes its actual observation list, including [].
    """
    item, reasons = item_version, []
    criteria = item["criteria"]
    refs = current_sources if current_sources is not None else item.get("refs", [])
    if item["basis_kind"] not in PASS_LABELS:
        reasons.append("视频建议和单纯观察不能直接作为符合判据")
    if item["criteria_state"] != "known" or not criteria.get("text") or not item["method_text"]:
        reasons.append("需要明确判据和检查方法")
    if not criteria.get("conditions_confirmed") or not item["conditions"]:
        reasons.append("需要填写并确认本项适用条件")
    if criteria.get("unresolved_standard_conflict"):
        reasons.append("与规范依据的分歧尚未核实")
    if any(ref.get("binding_status") != "active" for ref in refs):
        reasons.append("有引用已变化、缺失或未绑定，须先更新条目依据")
    if item["basis_kind"] == "personal_requirement" and not criteria.get("requirement_confirmed"):
        reasons.append("个人要求须由用户明确确认")
    if item["basis_kind"] in {"standard_clause", "manufacturer_instruction"}:
        expected_type = "standard_record" if item["basis_kind"] == "standard_clause" else "product_document"
        sources = [r.get("current_snapshot") or r.get("snapshot", {}) for r in refs if r["target_kind"] == "source"]
        basis_sources = [s for s in sources if s.get("source_type") == expected_type]
        if not basis_sources:
            reasons.append("需要相应的已核对原始资料引用")
        elif not any(" ".join(criteria.get("text", "").split()) == " ".join(source.get("excerpt", "").split()) for source in basis_sources):
            reasons.append("规范或说明书判据须使用已保存的原文摘录；自行改写的要求应另建个人要求")
        for source in basis_sources:
            if (source.get("verification_state") != "user_checked" or source.get("validity") != "current"
                    or not all(source.get(k) for k in ("checked_at", "excerpt", "locator", "edition"))):
                reasons.append("资料须有摘录、定位、版本、核对日期且当前有效")
            scope = source.get("scope", {})
            if not scope or any(str(item["conditions"].get(k, "")) != str(v) for k, v in scope.items()
                                if k != "model" or item["basis_kind"] != "manufacturer_instruction"):
                reasons.append("资料适用范围与本项条件未匹配")
            if item["basis_kind"] == "manufacturer_instruction":
                if not criteria.get("product_model") or criteria["product_model"] != scope.get("model"):
                    reasons.append("说明书型号必须与本项材料设备型号一致")
            today = datetime.now(timezone.utc).date().isoformat()
            if (source.get("valid_from") and source["valid_from"] > today) or (source.get("valid_to") and source["valid_to"] < today):
                reasons.append("资料不在有效日期范围内")
    required = criteria.get("required_observations", [])
    if not required:
        reasons.append("需要明确本项必需记录的观察或实测内容")
    if observations is not None:
        by_key = {o["key"]: o for o in observations}
        for field in required:
            value = by_key.get(field["key"])
            if value is None:
                reasons.append(f"缺少观察：{field['label']}")
            elif field["kind"] == "number" and (type(value["value"]) not in {int, float}
                    or value["unit"] != field["unit"] or value["method"] != field["method"] or not value["measured_at"]):
                reasons.append(f"实测的数值、单位或方法未满足：{field['label']}")
            elif field["kind"] == "text" and (not isinstance(value["value"], str) or not value["value"].strip()):
                reasons.append(f"需要文字观察：{field['label']}")
    return {"allowed": not reasons, "reasons": list(dict.fromkeys(reasons)),
            "label": PASS_LABELS.get(item["basis_kind"], "待核实")}


def _check_view(raw):
    row = dict(raw)
    row["measurements"] = json.loads(row.pop("measurements_json"))
    row["attachment_refs"] = json.loads(row.pop("attachment_refs_json"))
    return row


def item_detail(con, item_id):
    item = _item_snapshot(con, item_id)
    item["refs"] = personal.list_refs(con, "checklist_item", item_id)
    checks = [_check_view(r) for r in con.execute("SELECT * FROM checklist_check WHERE item_id=? ORDER BY checked_at,commit_seq", (item_id,))]
    voided = {row["supersedes_check_id"] for row in checks if row["record_kind"] == "void"}
    current = [row for row in checks if row["item_revision"] == item["revision"] and row["record_kind"] == "inspection"
               and row["check_id"] not in voided]
    latest = current[-1] if current else None
    for row in checks:
        row["voided"] = row["check_id"] in voided
        row["current_version"] = row["item_revision"] == item["revision"]
        row["attachment_issues"] = [aid for aid in row["attachment_refs"]
            if personal.resolve_target(con, item["project_id"], "attachment", aid) is None]
    eligibility = can_mark_pass(item)
    stale = any(ref["binding_status"] != "active" for ref in item["refs"]) or bool(latest and latest["attachment_issues"])
    item.update(checks=checks, current_result=latest["result"] if latest else "unchecked", current_check=latest,
                criteria_snapshot_hash=criteria_hash(item), pass_eligibility=eligibility,
                needs_recheck=stale or bool(latest and latest["result"] == "pass" and not eligibility["allowed"]))
    return item


def detail(con, instance_id):
    personal.require_read(con, field=True)
    instance = personal.require_entity(con, "checklist", instance_id)
    items = [item_detail(con, row[0]) for row in con.execute(
        "SELECT item_id FROM checklist_item WHERE instance_id=? ORDER BY position,item_id", (instance_id,))]
    complete = sum(i["current_result"] in {"pass", "not_applicable"} and not i["needs_recheck"] for i in items)
    state = ("archived" if instance["lifecycle_state"] == "archived" else
             "completed" if items and complete == len(items) else "active")
    return {**instance, "items": items, "state": state, "progress": {"total": len(items), "complete": complete}}


def list_instances(con, project_id, offset=0, limit=30):
    personal.require_read(con, field=True)
    personal.require_entity(con, "project", project_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise Error("分页参数无效")
    total = con.execute("SELECT count(*) FROM checklist_instance WHERE project_id=?", (project_id,)).fetchone()[0]
    instances = [detail(con, row[0]) for row in con.execute(
        "SELECT instance_id FROM checklist_instance WHERE project_id=? ORDER BY created_at DESC,instance_id LIMIT ? OFFSET ?",
        (project_id, limit, offset))]
    return {"checklists": instances, "total": total, "offset": offset, "limit": limit}


def _active(con, instance_id):
    instance = personal.require_entity(con, "checklist", instance_id)
    project = personal.require_entity(con, "project", instance["project_id"])
    if instance["lifecycle_state"] == "archived" or project.get("archived_at"):
        raise Error("已归档，需先显式恢复后再填写", "CHECKLIST_ARCHIVED", 409)
    return instance


def create(project_id, envelope):
    def apply(con, changes, base, op):
        project = personal.require_entity(con, "project", project_id)
        if project.get("archived_at"):
            raise Error("项目已归档", "CHECKLIST_ARCHIVED", 409)
        if set(changes) - {"title", "space", "stage", "items"}:
            raise Error("清单字段无效")
        items = changes.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise Error("请选择 1 至 50 个核对项")
        instance_id, now = str(uuid.uuid4()), utc_now()
        con.execute("INSERT INTO checklist_instance(instance_id,project_id,title,space,stage,basis_kb_revision,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)", (instance_id, project_id, personal.text(changes.get("title"), "清单标题", 200, required=True),
                    personal.text(changes.get("space", ""), "空间", 80), personal.text(changes.get("stage", ""), "阶段", 80),
                    db.knowledge_version(con)["kb_revision"], now, now))
        for index, raw in enumerate(items):
            fields = _fields(raw)
            fields["position"] = index
            item_id = str(uuid.uuid4())
            row = {"item_id": item_id, "instance_id": instance_id, **fields, "revision": 1, "created_at": now, "updated_at": now}
            con.execute(f"INSERT INTO checklist_item({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
            personal.bind_refs(con, project_id, "checklist_item", item_id, raw.get("refs", []))
            personal.record_revision(con, "checklist_item", item_id, 1, _item_snapshot(con, item_id), op)
        personal.record_revision(con, "checklist", instance_id, 1, personal.require_entity(con, "checklist", instance_id), op)
        return {"checklist": detail(con, instance_id)}, 201
    return personal.mutate(f"project:{project_id}:checklists:create", envelope, apply, field=True)


def update(instance_id, envelope):
    def apply(con, changes, base, op):
        current = personal.require_entity(con, "checklist", instance_id)
        if current["lifecycle_state"] == "archived" and changes != {"lifecycle_state": "active"}:
            raise Error("已归档，需先显式恢复后再修改", "CHECKLIST_ARCHIVED", 409)
        if not changes or set(changes) - {"title", "lifecycle_state"}:
            raise Error("只可修改标题或归档状态；完成度自动计算")
        updates = {}
        if "title" in changes:
            updates["title"] = personal.text(changes["title"], "标题", 200, required=True)
        if "lifecycle_state" in changes:
            if changes["lifecycle_state"] not in ("active", "archived"):
                raise Error("归档状态无效")
            updates.update(lifecycle_state=changes["lifecycle_state"], archived_at=utc_now() if changes["lifecycle_state"] == "archived" else None)
        rev = personal.cas_update(con, "checklist", instance_id, base, updates)
        personal.record_revision(con, "checklist", instance_id, rev, personal.require_entity(con, "checklist", instance_id), op)
        return {"checklist": detail(con, instance_id)}
    return personal.mutate(f"checklist:{instance_id}", envelope, apply, field=True)


def update_item(item_id, envelope):
    def apply(con, changes, base, op):
        old = _item_snapshot(con, item_id)
        _active(con, old["instance_id"])
        fields = _fields(changes, old)
        rev = personal.cas_update(con, "checklist_item", item_id, base, fields)
        if "refs" in changes:
            personal.bind_refs(con, old["project_id"], "checklist_item", item_id, changes["refs"])
        personal.record_revision(con, "checklist_item", item_id, rev, _item_snapshot(con, item_id), op)
        return {"item": item_detail(con, item_id)}
    return personal.mutate(f"checklist-item:{item_id}", envelope, apply, field=True)


def append_check(item_id, envelope):
    def apply(con, changes, base, op):
        item = item_detail(con, item_id)
        _active(con, item["instance_id"])
        if base is None:
            raise Error("必须提供条目版本", "REVISION_REQUIRED", 428)
        if type(base) is not int or base != item["revision"]:
            raise Error("条目已修订，请保留观察并核对新版本", "REVISION_CONFLICT", 409, current_revision=item["revision"])
        allowed = {"record_kind", "result", "measurements", "note", "attachment_refs", "checked_at", "supersedes_check_id", "criteria_snapshot_hash"}
        if set(changes) - allowed:
            raise Error("核对记录字段无效")
        if "criteria_snapshot_hash" in changes and changes["criteria_snapshot_hash"] != item["criteria_snapshot_hash"]:
            raise Error("判据已变化，请保留观察并重新核对", "EVIDENCE_CHANGED", 409)
        kind, result = changes.get("record_kind", "inspection"), changes.get("result")
        if not isinstance(kind, str) or kind not in {"inspection", "remediation", "void"}:
            raise Error("核对记录类型无效")
        note = personal.text(changes.get("note", ""), "记录说明", 8000)
        measurements = _measurements(changes.get("measurements", []))
        numeric_keys = {f["key"] for f in item["criteria"].get("required_observations", []) if f["kind"] == "number"}
        if any(m["key"] in numeric_keys and type(m["value"]) not in {int, float} for m in measurements):
            raise Error("数值观察必须提交实际数值，不能用文字或空串代替")
        checked = _time(changes.get("checked_at"))
        target = changes.get("supersedes_check_id")
        if kind == "inspection":
            if not isinstance(result, str) or result not in RESULTS or target:
                raise Error("检查结果无效")
            if result == "not_applicable" and not note:
                raise Error("不适用必须填写理由")
            if result == "pass":
                eligibility = can_mark_pass(item, observations=measurements)
                if not eligibility["allowed"]:
                    raise Error("；".join(eligibility["reasons"]), "CRITERIA_INCOMPLETE", 422)
        else:
            if result is not None or not note:
                raise Error("整改或作废须填写说明，不能携带检查结果")
            if kind == "remediation" and target:
                raise Error("整改不能作废检查")
        if kind == "void":
            target_row = personal.require_entity(con, "check", personal.valid_uuid(target, "作废目标"), item["project_id"])
            if target_row["item_id"] != item_id or target_row["item_revision"] != base or target_row["record_kind"] == "void":
                raise Error("只能作废同一项、同一版本的检查或整改")
            if con.execute("SELECT 1 FROM checklist_check WHERE supersedes_check_id=? AND record_kind='void'", (target,)).fetchone():
                raise Error("目标已经作废", "ALREADY_VOIDED", 409)
        attachments = changes.get("attachment_refs", [])
        if not isinstance(attachments, list) or len(attachments) > 20 or any(not isinstance(a, str) for a in attachments):
            raise Error("最多关联 20 个已上传附件")
        for aid in attachments:
            if personal.resolve_target(con, item["project_id"], "attachment", aid) is None:
                raise Error("附件未就绪或文件缺失", "EVIDENCE_CHANGED", 409, changed_ref_ids=[aid])
        check_id, now = str(uuid.uuid4()), utc_now()
        seq = con.execute("SELECT coalesce(max(commit_seq),0)+1 FROM checklist_check WHERE item_id=?", (item_id,)).fetchone()[0]
        con.execute("INSERT INTO checklist_check VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            check_id, item_id, base, item["criteria_snapshot_hash"], op, seq, kind, result,
            personal._json(measurements), note, personal._json(list(dict.fromkeys(attachments))), checked, now, target))
        check = _check_view(con.execute("SELECT * FROM checklist_check WHERE check_id=?", (check_id,)).fetchone())
        return {"check": check, "item": item_detail(con, item_id), "checklist": detail(con, item["instance_id"])}, 201
    return personal.mutate(f"checklist-item:{item_id}:check", envelope, apply, field=True)
