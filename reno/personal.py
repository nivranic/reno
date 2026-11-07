"""Personal choices and sources: single primary, CAS revisions, immutable history.

Mutation callbacks participate in one short transaction. They never commit and
never modify knowledge_atom or user_decision: adopting a choice is personal.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from . import config, db, evidence_identity as identity, storage_admin
from . import storage_lifecycle as lifecycle
from .personal_schema import node_identity
from .review import utc_now

ENTITIES = {
    "project": ("renovation_project", "project_id"), "card": ("decision_card", "card_id"),
    "source": ("source_record", "source_id"), "attachment": ("source_attachment", "attachment_id"),
    "checklist": ("checklist_instance", "instance_id"), "checklist_item": ("checklist_item", "item_id"),
    "check": ("checklist_check", "check_id"),
}
SOURCE_TYPES = {"manual_note", "standard_record", "product_document", "quote", "site_photo"}
CARD_STATES = {"considering", "adopted", "not_adopted", "archived"}


class PersonalError(ValueError):
    def __init__(self, detail, code="INVALID_REQUEST", status=400, **meta):
        super().__init__(detail)
        self.code, self.status, self.meta = code, status, meta

    def body(self):
        return {"detail": str(self), "code": self.code, **self.meta}


def _enabled(key):
    return config.get(key, False) is True or str(config.get(key, False)).lower() in {"true", "1"}


def _metadata(con):
    return dict(con.execute("SELECT key,value FROM meta WHERE key IN "
                            "('library_id','personal_primary_id','primary_epoch','default_project_id')"))


def capabilities(con):
    if config.is_cloud():
        from . import personal_mirror_read
        observed = personal_mirror_read.capabilities_for(con) if _enabled("personal_cloud_read_enabled") else None
        readable = observed is not None
        fields = readable and _enabled("field_checks_enabled")
        return {"library_id": None, "instance_id": None, "personal_primary_id": None, "primary_epoch": 0,
                **(observed or {}), "personal_read": readable, "personal_write": False, "personal_upload": False,
                "personal_integrity_ok": readable, "field_checks": fields, "field_checks_read": fields,
                "field_checks_write": False, "personal_cloud_read_enabled": _enabled("personal_cloud_read_enabled"),
                "personal_mode": "mirror" if readable else "disabled",
                "personal_read_version": observed["personal_read_version"] if readable else None,
                "personal_reason": "云端个人镜像仅供只读，请在家庭主实例修改" if readable else
                    "云端个人镜像读取尚未启用" if not _enabled("personal_cloud_read_enabled") else
                    "尚未安装通过完整校验的个人镜像"}
    meta = _metadata(con)
    try:
        instance = node_identity(config.DATA)
    except (OSError, sqlite3.DatabaseError):
        instance = None
    enabled = _enabled("personal_features_enabled") and not config.is_cloud()
    primary = bool(instance and instance == meta.get("personal_primary_id"))
    initialized = bool(meta.get("default_project_id") and meta.get("primary_epoch"))
    readable = enabled and initialized
    intact = True
    if readable and primary:
        from .personal_attachments import file_is_intact
        for attachment in con.execute("SELECT storage_key,byte_size,sha256 FROM source_attachment WHERE state='ready'"):
            if not file_is_intact(attachment["storage_key"], attachment["byte_size"], attachment["sha256"]):
                intact = False
                break
    reason = ("当前云节点尚未开通个人数据镜像" if config.is_cloud() else
              "个人装修功能尚未启用" if not enabled else
              "个人数据主实例尚未初始化" if not initialized else
              "本节点是只读副本" if not primary else
              "个人附件不完整，保留只读记录；请完成附件恢复后再写入" if not intact else "")
    writable = readable and primary and intact
    if writable:
        from .personal_authority_legacy_fence import (
            LegacyAuthorityFenceError, REQUIRED, STATE_UNAVAILABLE, assert_legacy_write_allowed_locked,
        )
        try:
            profile = storage_admin.from_connection(con)
            assert_legacy_write_allowed_locked(profile)
        except LegacyAuthorityFenceError as error:
            if error.code == REQUIRED:
                reason = "个人数据写入权限待确认，暂时仅可读取"
            elif error.code == STATE_UNAVAILABLE:
                reason = "个人数据写入权限暂不可用，请稍后重试"
            else:
                raise
            writable = False
        except lifecycle.StorageLifecycleError:
            writable = False
            reason = "个人数据写入权限暂不可用，请稍后重试"
    return {"library_id": meta.get("library_id"), "instance_id": instance,
            "personal_primary_id": meta.get("personal_primary_id"),
            "primary_epoch": int(meta.get("primary_epoch") or 0),
            "personal_read": readable, "personal_write": writable,
            "personal_upload": writable, "personal_integrity_ok": intact,
            "field_checks": readable and _enabled("field_checks_enabled"),
            "field_checks_read": readable and _enabled("field_checks_enabled"),
            "field_checks_write": writable and _enabled("field_checks_enabled"),
            "personal_cloud_read_enabled": _enabled("personal_cloud_read_enabled"),
            "personal_read_version": None, "personal_mode": "local" if readable else "disabled",
            "personal_reason": reason}


def can_read(con):
    return capabilities(con)["personal_read"]


def can_write(con, epoch=None, *, upload=False, field=False):
    if config.is_cloud():
        return False
    cap = capabilities(con)
    return bool(cap["personal_upload" if upload else "personal_write"]
                and (epoch is None or epoch == cap["primary_epoch"])
                and (not field or cap["field_checks"]))


def require_read(con, *, field=False):
    cap = capabilities(con)
    if not cap["personal_read"] or (field and not cap["field_checks"]):
        raise PersonalError(cap["personal_reason"] or "现场核对尚未启用", "CAPABILITY_DISABLED", 403)
    return cap


def require_write(con, epoch, *, upload=False, field=False):
    require_local_write()
    profile = storage_admin.from_connection(con)
    require_legacy_write_locked(profile)
    cap = require_read(con, field=field)
    if not cap["instance_id"] or not cap["personal_primary_id"]:
        raise PersonalError("个人数据主实例不可用", "PERSONAL_PRIMARY_UNAVAILABLE", 503)
    if cap["instance_id"] != cap["personal_primary_id"]:
        raise PersonalError("本节点是只读副本", "READ_ONLY_REPLICA", 403)
    if type(epoch) is not int or epoch != cap["primary_epoch"]:
        raise PersonalError("主实例任期已变化，请重新读取能力", "PRIMARY_CHANGED", 409,
                            primary_epoch=cap["primary_epoch"])
    if not cap["personal_integrity_ok"]:
        raise PersonalError(cap["personal_reason"], "PERSONAL_PRIMARY_UNAVAILABLE", 503)
    return cap


def require_local_write():
    """Reject every cloud personal write before SQL, receipt or filesystem work."""
    if config.is_cloud():
        raise PersonalError("云端个人镜像仅供只读，请在家庭主实例修改", "PERSONAL_CLOUD_READ_ONLY", 403)


def require_legacy_write_locked(profile):
    """Map the finite presence fence; this helper does not grant write access."""
    from .personal_authority_legacy_fence import (
        LegacyAuthorityFenceError, REQUIRED, assert_legacy_write_allowed_locked,
    )
    try:
        assert_legacy_write_allowed_locked(profile)
    except LegacyAuthorityFenceError as error:
        detail = ("个人数据写入权限待确认，暂时仅可读取" if error.code == REQUIRED
                  else "个人数据写入权限暂不可用，请稍后重试")
        raise PersonalError(detail, error.code, 503) from None


def valid_uuid(value, field="id"):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise PersonalError(f"{field} 必须是 UUID") from None


def text(value, field, limit=2000, *, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise PersonalError(f"{field} 必须是最多 {limit} 字的文本")
    if required and not value.strip():
        raise PersonalError(f"{field} 不能为空")
    return value.strip()


def object_strings(value, field, *, limit=40):
    if not isinstance(value, dict) or len(value) > limit:
        raise PersonalError(f"{field} 必须是最多 {limit} 个字段的对象")
    return {text(k, field, 80, required=True): text(v, field, 1000) for k, v in value.items()}


def _json(value):
    try:
        result = identity.canonical_json(value)
        if len(result.encode("utf-8")) > 256 * 1024:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise PersonalError("请求 JSON 无效或超过 256 KiB") from None


def parse_envelope(envelope):
    if not isinstance(envelope, dict) or set(envelope) - {"operation_id", "primary_epoch", "base_revision", "changes"}:
        raise PersonalError("写入信封字段无效")
    operation_id = valid_uuid(envelope.get("operation_id"), "operation_id")
    if not isinstance(envelope.get("changes"), dict):
        raise PersonalError("changes 必须是对象")
    return {**envelope, "operation_id": operation_id}


def receipt(con, operation_id):
    require_local_write()
    row = con.execute("SELECT * FROM mutation_receipt WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    return {"operation_id": row["operation_id"], "scope": row["scope"],
            "response_status": row["response_status"], "response": json.loads(row["response_json"]),
            "created_at": row["created_at"]}


def mutate(scope, envelope, callback, *, upload=False, field=False):
    """Gate → receipt/hash → callback/CAS → history/receipt → commit, in order.

    callback(con, changes, base_revision, operation_id) returns a response dict
    or (dict, HTTP status). Replays return the original payload, even if later
    entity edits have advanced its revision. Failed operations have no receipt.
    """
    require_local_write()
    envelope = parse_envelope(envelope)
    op = envelope["operation_id"]
    request_hash = hashlib.sha256(_json({"scope": scope, "payload": envelope}).encode()).hexdigest()
    try:
        profile = storage_admin.profile()
        # Existing facilities are mandatory. The exact source SH begins before
        # SQLite opens and survives every commit/rollback and connection close.
        with storage_admin.connection(profile) as con:
            try:
                con.execute("BEGIN IMMEDIATE")
                require_write(con, envelope.get("primary_epoch"), upload=upload, field=field)
                prior = con.execute("SELECT * FROM mutation_receipt WHERE operation_id=?", (op,)).fetchone()
                if prior:
                    if prior["scope"] != scope or prior["request_hash"] != request_hash:
                        raise PersonalError("操作编号已用于不同请求", "IDEMPOTENCY_KEY_REUSED", 409)
                    con.rollback()
                    return json.loads(prior["response_json"]), prior["response_status"]
                result = callback(con, envelope["changes"], envelope.get("base_revision"), op)
                body, status = result if isinstance(result, tuple) else (result, 200)
                body = {**body, "operation_id": op}
                con.execute("INSERT INTO mutation_receipt VALUES(?,?,?,?,?,?)",
                            (op, scope, request_hash, status, _json(body), utc_now()))
                con.commit()
                return body, status
            except BaseException:
                # Rollback/close cleanup must not replace cancellation or the
                # original callback/CAS/commit failure. connection owns closing.
                try:
                    con.rollback()
                except BaseException:
                    pass
                raise
    except lifecycle.StorageLifecycleError as exc:
        raise PersonalError("个人数据存储保护不可用，请检查本实例的存储设施。",
                            exc.code, exc.status) from None


def require_entity(con, kind, entity_id, project_id=None):
    if kind not in ENTITIES:
        raise PersonalError("未知对象类型")
    table, pk = ENTITIES[kind]
    row = con.execute(f"SELECT * FROM {table} WHERE {pk}=?", (entity_id,)).fetchone()
    if row is None:
        raise PersonalError("记录不存在", "NOT_FOUND", 404)
    item = dict(row)
    owner = item.get("project_id")
    if kind == "project":
        owner = item["project_id"]
    elif kind == "checklist_item":
        owner = require_entity(con, "checklist", item["instance_id"])["project_id"]
    elif kind == "check":
        owner = require_entity(con, "checklist_item", item["item_id"])["project_id"]
    if project_id is not None and owner != project_id:
        raise PersonalError("引用必须属于同一项目", "INVALID_REQUEST", 400)
    item["project_id"] = owner
    return item


def cas_update(con, kind, entity_id, base_revision, updates):
    if base_revision is None:
        raise PersonalError("修改必须提供 base_revision", "REVISION_REQUIRED", 428)
    if type(base_revision) is not int or base_revision < 1:
        raise PersonalError("base_revision 必须是正整数")
    table, pk = ENTITIES[kind]
    columns = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    if not updates or not set(updates) <= columns - {pk, "project_id", "revision", "created_at"}:
        raise PersonalError("更新字段无效")
    if "updated_at" in columns:
        updates = {**updates, "updated_at": utc_now()}
    setters = ",".join(f"{key}=?" for key in updates)
    cur = con.execute(f"UPDATE {table} SET {setters},revision=revision+1 WHERE {pk}=? AND revision=?",
                      (*updates.values(), entity_id, base_revision))
    if cur.rowcount != 1:
        current = require_entity(con, kind, entity_id)
        raise PersonalError("记录已被修改，请保留草稿并核对最新版本", "REVISION_CONFLICT", 409,
                            current_revision=current["revision"])
    return base_revision + 1


def record_revision(con, kind, entity_id, revision, snapshot, operation_id):
    con.execute("INSERT INTO personal_revision VALUES(?,?,?,?,?,?)",
                (kind, entity_id, revision, _json(snapshot), operation_id, utc_now()))


def revisions(con, kind, entity_id):
    require_read(con)
    require_entity(con, kind, entity_id)
    return [{"entity_kind": r["entity_kind"], "entity_id": r["entity_id"], "revision": r["revision"],
             "snapshot": json.loads(r["snapshot_json"]), "operation_id": r["operation_id"], "created_at": r["created_at"]}
            for r in con.execute("SELECT * FROM personal_revision WHERE entity_kind=? AND entity_id=? ORDER BY revision DESC",
                                 (kind, entity_id))]


def project_view(row):
    row = dict(row)
    row["profile"] = json.loads(row.pop("profile_json"))
    return row


def default_project(con):
    cap = capabilities(con)
    if not cap["personal_read"]:
        return {"enabled": False, "project": None, "reason": cap["personal_reason"]}
    return {"enabled": True, "project": project_view(require_entity(con, "project", _metadata(con)["default_project_id"]))}


def update_project(project_id, envelope):
    def apply(con, changes, base, op):
        require_entity(con, "project", project_id)
        if not changes or set(changes) - {"name", "profile"}:
            raise PersonalError("项目可修改名称和资料")
        updates = {}
        if "name" in changes:
            updates["name"] = text(changes["name"], "name", 120, required=True)
        if "profile" in changes:
            profile = changes["profile"]
            allowed = {"city", "house_type", "area", "renovation_mode", "budget_range", "current_stage", "conditions"}
            if not isinstance(profile, dict) or set(profile) - allowed:
                raise PersonalError("项目资料字段无效")
            checked = {k: object_strings(v, k) if k == "conditions" else text(v, k, 200)
                       for k, v in profile.items()}
            updates["profile_json"] = _json(checked)
        rev = cas_update(con, "project", project_id, base, updates)
        project = project_view(require_entity(con, "project", project_id))
        record_revision(con, "project", project_id, rev, project, op)
        return {"project": project}
    return mutate(f"project:{project_id}", envelope, apply)


def _source_snapshot(row):
    return {key: value for key, value in row.items()
            if key not in {"revision", "created_at", "updated_at", "attachments", "target_fingerprint"}}


def source_view(con, source_id, *, with_attachments=True):
    row = require_entity(con, "source", source_id)
    row["scope"] = json.loads(row.pop("scope_json"))
    row["target_fingerprint"] = identity.fingerprint(_source_snapshot(row))
    if with_attachments:
        from .personal_attachments import attachment_view
        row["attachments"] = [attachment_view(con, r[0]) for r in con.execute(
            "SELECT attachment_id FROM source_attachment WHERE source_id=? ORDER BY created_at,attachment_id", (source_id,))]
    return row


def resolve_target(con, project_id, kind, target_id):
    if kind in {"atom", "conflict"}:
        from . import sync_visibility
        if not sync_visibility.target_visible(con, kind, target_id):
            return None
        target = identity.load_target(con, kind, target_id)
        if target is None:
            return None
        snap, fingerprint = target
        if kind == "atom":
            source = con.execute("SELECT source_url,title,source_published_at FROM video_asset WHERE video_id=?", (snap.get("video_id"),)).fetchone()
            if source:
                snap = {**snap, "source_url": source["source_url"], "video_title": source["title"],
                        "source_published_at": source["source_published_at"]}
        return snap, fingerprint
    if kind == "source":
        row = con.execute("SELECT project_id FROM source_record WHERE source_id=?", (target_id,)).fetchone()
        if row is None:
            return None
        require_entity(con, "source", target_id, project_id)
        snap = source_view(con, target_id, with_attachments=False)
        return _source_snapshot(snap), snap["target_fingerprint"]
    if kind == "attachment":
        if con.execute("SELECT 1 FROM source_attachment WHERE attachment_id=?", (target_id,)).fetchone() is None:
            return None
        require_entity(con, "attachment", target_id, project_id)
        from .personal_attachments import attachment_view
        snap = attachment_view(con, target_id)
        if snap["state"] != "ready" or snap.get("missing") or snap.get("readable") is False:
            return None
        return snap, snap["target_fingerprint"]
    raise PersonalError("引用目标类型无效")


def bind_refs(con, project_id, owner_kind, owner_id, refs):
    if owner_kind not in {"card", "checklist", "checklist_item", "check"}:
        raise PersonalError("引用所属对象类型无效")
    require_entity(con, owner_kind, owner_id, project_id)
    if not isinstance(refs, list) or len(refs) > 50:
        raise PersonalError("最多保存 50 个引用")
    prepared, seen = [], set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) - {"target_kind", "target_id", "target_fingerprint"}:
            raise PersonalError("引用字段无效")
        kind, target_id, expected = ref.get("target_kind"), ref.get("target_id"), ref.get("target_fingerprint")
        if not isinstance(kind, str) or not isinstance(target_id, str) or not isinstance(expected, str):
            raise PersonalError("引用必须包含目标类型、ID 和当前指纹")
        if (kind, target_id) in seen:
            continue
        target = resolve_target(con, project_id, kind, target_id)
        if target is None or target[1] != expected:
            raise PersonalError("引用内容已变化或不可用，请重新核对", "EVIDENCE_CHANGED", 409,
                                changed_ref_ids=[target_id])
        seen.add((kind, target_id))
        prepared.append((str(uuid.uuid4()), project_id, owner_kind, owner_id, kind, target_id,
                         identity.FINGERPRINT_VERSION, target[1], _json(target[0]), utc_now()))
    con.execute("DELETE FROM personal_evidence_ref WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
    con.executemany("INSERT INTO personal_evidence_ref VALUES(?,?,?,?,?,?,?,?,?,?)", prepared)


def list_refs(con, owner_kind, owner_id):
    refs = []
    for raw in con.execute("SELECT * FROM personal_evidence_ref WHERE owner_kind=? AND owner_id=? ORDER BY created_at,ref_id", (owner_kind, owner_id)):
        ref = dict(raw)
        ref["snapshot"] = json.loads(ref.pop("snapshot_json"))
        current = resolve_target(con, ref["project_id"], ref["target_kind"], ref["target_id"])
        status = ("legacy_unbound" if not ref["target_fingerprint"] else "missing" if current is None
                  else "active" if current[1] == ref["target_fingerprint"] else "stale")
        ref.update(binding_status=status, status=status,
                   current_fingerprint=current[1] if current else None,
                   current_snapshot=current[0] if current else None)
        refs.append(ref)
    return refs


def card_view(con, card_id):
    row = require_entity(con, "card", card_id)
    for src, dst in (("context_json", "context_snapshot"), ("alternatives_json", "alternatives"),
                     ("open_questions_json", "open_questions"), ("content_snapshot_json", "content_snapshot")):
        row[dst] = json.loads(row.pop(src))
    row["evidence_refs"] = list_refs(con, "card", card_id)
    row["needs_recheck"] = any(r["binding_status"] != "active" for r in row["evidence_refs"])
    return row


def _card_fields(changes, previous=None, *, create=False):
    allowed = {"title", "question", "context_snapshot", "space", "stage", "alternatives", "selected_option_id", "decision_text",
               "decision_reason", "open_questions", "content_snapshot", "state", "evidence_refs"}
    if not changes or set(changes) - allowed or (not create and "evidence_refs" in changes):
        raise PersonalError("卡片字段无效；更新依据请使用独立确认操作")
    defaults = {"title": "", "question": "", "context_snapshot": {}, "space": "", "stage": "", "alternatives": [],
                "selected_option_id": None, "decision_text": "", "decision_reason": "", "open_questions": [],
                "content_snapshot": {"kind": "note", "text": ""}, "state": "considering"}
    merged = {**defaults, **(previous or {}), **changes}
    result = {}
    for key, limit in {"title": 200, "question": 3000, "decision_text": 4000, "decision_reason": 4000, "space": 80, "stage": 80}.items():
        result[key] = text(merged[key], key, limit, required=key == "title")
    result["context_json"] = _json(object_strings(merged["context_snapshot"], "context_snapshot"))
    options = merged["alternatives"]
    if not isinstance(options, list) or len(options) > 20:
        raise PersonalError("最多保存 20 个候选方案")
    checked, ids = [], set()
    for option in options:
        if not isinstance(option, dict) or set(option) - {"option_id", "label", "description"}:
            raise PersonalError("候选方案字段无效")
        oid = valid_uuid(option.get("option_id"), "option_id")
        if oid in ids:
            raise PersonalError("候选方案 ID 不得重复")
        ids.add(oid)
        checked.append({"option_id": oid, "label": text(option.get("label"), "label", 200, required=True),
                        "description": text(option.get("description", ""), "description", 2000)})
    selected = merged["selected_option_id"]
    if selected is not None and (not isinstance(selected, str) or selected not in ids):
        raise PersonalError("选中方案必须使用本卡片的 option_id")
    result.update(alternatives_json=_json(checked), selected_option_id=selected)
    questions = merged["open_questions"]
    if not isinstance(questions, list) or len(questions) > 30:
        raise PersonalError("最多保存 30 个待确认问题")
    result["open_questions_json"] = _json([text(q, "open_questions", 1000) for q in questions])
    content = merged["content_snapshot"]
    if not isinstance(content, dict) or set(content) - {"kind", "text"} or not isinstance(content.get("kind"), str) or content.get("kind") not in {"answer", "comparison", "atom", "note"}:
        raise PersonalError("content_snapshot 必须包含 kind 和 text")
    result["content_snapshot_json"] = _json({"kind": content["kind"], "text": text(content.get("text", ""), "content_snapshot.text", 16000)})
    state = merged["state"]
    if not isinstance(state, str) or state not in CARD_STATES:
        raise PersonalError("个人决定状态无效")
    if state == "adopted" and selected is None and not result["decision_text"]:
        raise PersonalError("采纳时至少选择一个方案或填写明确的个人决定")
    result["state"] = state
    result["archived_at"] = (previous or {}).get("archived_at") or utc_now() if state == "archived" else None
    return result


def create_card(project_id, envelope):
    def apply(con, changes, base, op):
        require_entity(con, "project", project_id)
        fields = _card_fields(changes, create=True)
        card_id, now = str(uuid.uuid4()), utc_now()
        row = {"card_id": card_id, "project_id": project_id, **fields, "revision": 1, "created_at": now, "updated_at": now}
        con.execute(f"INSERT INTO decision_card({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
        bind_refs(con, project_id, "card", card_id, changes.get("evidence_refs", []))
        card = card_view(con, card_id)
        record_revision(con, "card", card_id, 1, card, op)
        return {"card": card}, 201
    return mutate(f"project:{project_id}:cards:create", envelope, apply)


def update_card(card_id, envelope):
    def apply(con, changes, base, op):
        old = card_view(con, card_id)
        fields = _card_fields(changes, old)
        rev = cas_update(con, "card", card_id, base, fields)
        card = card_view(con, card_id)
        record_revision(con, "card", card_id, rev, card, op)
        return {"card": card}
    return mutate(f"card:{card_id}", envelope, apply)


def update_card_evidence(card_id, envelope):
    def apply(con, changes, base, op):
        if set(changes) != {"confirm", "evidence_refs"} or changes["confirm"] is not True:
            raise PersonalError("更新依据必须明确确认新旧变化")
        old = card_view(con, card_id)
        rev = cas_update(con, "card", card_id, base, {"updated_at": utc_now()})
        bind_refs(con, old["project_id"], "card", card_id, changes["evidence_refs"])
        card = card_view(con, card_id)
        record_revision(con, "card", card_id, rev, card, op)
        return {"card": card}
    return mutate(f"card:{card_id}:evidence", envelope, apply)


def list_entities(con, project_id, kind, *, offset=0, limit=30, state=None, space=None, stage=None):
    require_read(con)
    require_entity(con, "project", project_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise PersonalError("分页参数无效")
    table, pk = ENTITIES[kind]
    if kind not in {"card", "source"}:
        raise PersonalError("列表类型无效")
    where, params = "project_id=?", [project_id]
    for key, value in (("space", space), ("stage", stage)):
        if value is not None:
            if kind != "card":
                raise PersonalError("该列表不支持此筛选")
            value = text(value, key, 80)
            if value:
                where += f" AND {key}=?"
                params.append(value)
    if state is not None:
        if kind != "card" or state not in CARD_STATES:
            raise PersonalError("筛选状态无效")
        where += " AND state=?"
        params.append(state)
    total = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
    rows = con.execute(f"SELECT {pk} FROM {table} WHERE {where} ORDER BY updated_at DESC,{pk} LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    view = card_view if kind == "card" else source_view
    return {"cards" if kind == "card" else "sources": [view(con, row[0]) for row in rows],
            "total": total, "offset": offset, "limit": limit}


SOURCE_TEXT_FIELDS = {"title": 200, "publisher": 300, "document_no": 100, "edition": 100,
                      "published_on": 40, "valid_from": 40, "valid_to": 40, "source_url": 2000,
                      "locator": 1000, "excerpt": 16000, "check_note": 4000}


def _source_fields(changes, previous=None):
    allowed = set(SOURCE_TEXT_FIELDS) | {"source_type", "scope", "verification_state", "validity", "checked_at"}
    if not changes or set(changes) - allowed:
        raise PersonalError("来源字段无效")
    values = {**{key: "" for key in SOURCE_TEXT_FIELDS}, "source_type": "manual_note", "scope": {},
              "verification_state": "unverified", "validity": "unknown", "checked_at": None,
              **(previous or {}), **changes}
    checked = {key: text(values[key], key, limit, required=key == "title") for key, limit in SOURCE_TEXT_FIELDS.items()}
    from datetime import date
    for key in ("published_on", "valid_from", "valid_to"):
        if checked[key]:
            try:
                if date.fromisoformat(checked[key]).isoformat() != checked[key]:
                    raise ValueError()
            except ValueError:
                raise PersonalError(f"{key} 必须是 YYYY-MM-DD 格式的有效日期") from None
    if checked["valid_from"] and checked["valid_to"] and checked["valid_from"] > checked["valid_to"]:
        raise PersonalError("有效期结束日期不得早于开始日期")
    if not isinstance(values["source_type"], str) or values["source_type"] not in SOURCE_TYPES:
        raise PersonalError("来源类型无效")
    if (not isinstance(values["verification_state"], str) or values["verification_state"] not in {"unverified", "user_checked"}
            or not isinstance(values["validity"], str) or values["validity"] not in {"unknown", "current", "superseded", "withdrawn"}):
        raise PersonalError("来源核对状态无效")
    if checked["source_url"]:
        try:
            parsed = urlsplit(checked["source_url"])
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                    or any(ord(char) < 32 for char in checked["source_url"])):
                raise ValueError()
        except ValueError:
            raise PersonalError("来源链接只接受不含登录凭据的 HTTP(S) 地址") from None
    checked_at = values["checked_at"]
    if checked_at is not None:
        from datetime import datetime, timezone, timedelta
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError()
            checked_at = parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (ValueError, TypeError, AttributeError):
            raise PersonalError("核对日期必须带时区且不能在未来") from None
    if values["verification_state"] == "user_checked" or values["validity"] == "current":
        if not checked_at or not checked["check_note"]:
            raise PersonalError("用户核对必须记录原文核对说明和日期")
    if values["validity"] == "current" and values["verification_state"] != "user_checked":
        raise PersonalError("标记当前有效前必须先保存用户核对记录")
    checked.update(source_type=values["source_type"], scope_json=_json(object_strings(values["scope"], "scope")),
                   verification_state=values["verification_state"], validity=values["validity"], checked_at=checked_at)
    return checked


def create_source(project_id, envelope):
    def apply(con, changes, base, op):
        require_entity(con, "project", project_id)
        fields = _source_fields(changes)
        source_id, now = str(uuid.uuid4()), utc_now()
        row = {"source_id": source_id, "project_id": project_id, **fields, "revision": 1, "created_at": now, "updated_at": now}
        con.execute(f"INSERT INTO source_record({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
        source = source_view(con, source_id)
        record_revision(con, "source", source_id, 1, source, op)
        return {"source": source}, 201
    return mutate(f"project:{project_id}:sources:create", envelope, apply)


def update_source(source_id, envelope):
    def apply(con, changes, base, op):
        old = source_view(con, source_id, with_attachments=False)
        fields = _source_fields(changes, old)
        rev = cas_update(con, "source", source_id, base, fields)
        source = source_view(con, source_id)
        record_revision(con, "source", source_id, rev, source, op)
        return {"source": source}
    return mutate(f"source:{source_id}", envelope, apply)


def safe_public_url(value):
    try:
        parsed = urlsplit(value or "")
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if host.lower() == "localhost" or "." not in host or host.endswith((".local", ".localhost", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host):
                return ""
        # Export public provenance only; query strings can carry shared tokens.
        clean_query = [(k, v) for k, v in parse_qsl(parsed.query) if k in {"p", "t", "page"} and v.isdigit()]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(clean_query), ""))
    except (ValueError, TypeError):
        return ""


def _export_text(value):
    value = str(value or "")
    for key in ("auth_token", "sync_token", "zhipu_api_key"):
        secret = config.get(key, "")
        if isinstance(secret, str) and secret:
            value = value.replace(secret, "[已移除凭据]")
    value = re.sub(r"https?://[^\s<>\[\]()]+", lambda m: safe_public_url(m[0]) or "[内部链接已移除]", value)
    value = re.sub(r"(?i)(?:[a-z]:[\\/]|file://|\\\\)[^\s]+", "[本地路径已移除]", value)
    value = re.sub(r"(?i)(token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[已移除凭据]", value)
    def private_ip(match):
        try:
            return match[0] if ipaddress.ip_address(match[0]).is_global else "[内网地址已移除]"
        except ValueError:
            return match[0]
    value = re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", private_ip, value)
    # Escape raw HTML and Markdown image/link control characters in user prose.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")


def export_cards(con, project_id, card_ids):
    require_read(con)
    require_entity(con, "project", project_id)
    if (not isinstance(card_ids, list) or not 1 <= len(card_ids) <= 50
            or not all(isinstance(item, str) for item in card_ids) or len(set(card_ids)) != len(card_ids)):
        raise PersonalError("请选择 1–50 张不同卡片")
    documents = []
    for card_id in card_ids:
        require_entity(con, "card", card_id, project_id)
        card = card_view(con, card_id)
        choice = next((o["label"] for o in card["alternatives"] if o["option_id"] == card["selected_option_id"]), "")
        lines = [f"# {_export_text(card['title'])}", "", f"记录日期：{card['updated_at']}",
                 f"个人状态：{card['state']}",
                 f"个人分类：空间 {_export_text(card['space']) or '未指定'}；阶段 {_export_text(card['stage']) or '未指定'}",
                 f"个人结论：{_export_text(card['decision_text'] or choice) or '尚未决定'}",
                 f"理由：{_export_text(card['decision_reason']) or '未填写'}", "", "## 本次条件", ""]
        lines += [f"- {_export_text(k)}：{_export_text(v)}" for k, v in card["context_snapshot"].items()] or ["未注明"]
        lines += ["", "## 当时内容", "", _export_text(card["content_snapshot"].get("text")), "", "## 依据", ""]
        for ref in card["evidence_refs"]:
            snap = ref["snapshot"]
            lines.append(f"- {_export_text(snap.get('claim') or snap.get('title') or snap.get('original_name') or ref['target_id'])}")
            lines.append(f"  性质：{_export_text(snap.get('evidence_nature') or snap.get('source_type') or ref['target_kind'])}；引用状态：{ref['binding_status']}")
            link = safe_public_url(snap.get("source_url"))
            if link:
                lines.append(f"  原链接：<{link}>")
            for ev in snap.get("evidence_refs", [])[:5]:
                lines.append(f"  视频时间点：{int(ev.get('start_ms') or 0) / 1000:g} 秒")
            for note in snap.get("exceptions") or []:
                lines.append(f"  例外：{_export_text(note)}")
        lines += ["", "## 待核实", ""] + [f"- {_export_text(q)}" for q in card["open_questions"]]
        if card["needs_recheck"]:
            lines.append("- 来源已变化或不可用，旧快照保留，需重新核对。")
        lines.append("\n个人采纳不代表知识已经核实，也不构成工程合格证明。")
        documents.append("\n".join(lines))
    return "\n\n---\n\n".join(documents) + "\n"
