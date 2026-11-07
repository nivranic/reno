"""只读决定摘要预览；不创建分享链接、凭据、回执或访问/撤销权限。

读取回调仅供现有 read_result 工厂使用：同一事务核项目和选集后产生独立
七字段声明。调用者必须在工厂完整成功退出后才冻结原字节并发布 HTTP 响应。
"""
import json
import uuid

from . import config, personal, personal_share_contract as contract


def require_enabled():
    """读取开关关闭时先拒绝；实际 installed/read-version 准入仍由原工厂核验。"""
    if config.is_cloud():
        if not personal._enabled("personal_cloud_read_enabled"):
            raise personal.PersonalError("云端个人镜像读取尚未启用", "PERSONAL_MIRROR_READ_DISABLED", 403)
    elif not personal._enabled("personal_features_enabled"):
        raise personal.PersonalError("个人装修功能尚未启用", "CAPABILITY_DISABLED", 403)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def decode_body(raw):
    """仅此只读 POST 拒绝重复 JSON 字段；原请求流的 256 KiB 上限保持。"""
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise personal.PersonalError("预览请求必须是无重复字段的 JSON 对象") from None


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise personal.PersonalError("预览选择必须使用原规范 UUID") from None
    return value


def normalize_selection(value):
    if type(value) is not dict or set(value) != {"selection"}:
        raise personal.PersonalError("预览只接受 selection")
    selection = value["selection"]
    if type(selection) is not list or not 1 <= len(selection) <= contract.MAX_DECISIONS:
        raise personal.PersonalError("请选择 1 至 50 条决定预览")
    selected, seen = [], set()
    for item in selection:
        if type(item) is not dict or set(item) != {"card_id", "revision"}:
            raise personal.PersonalError("选择只接受 card_id 和 revision")
        card_id = _uuid(item["card_id"])
        revision = item["revision"]
        if type(revision) is not int or not 1 <= revision <= contract.MAX_REVISION or card_id in seen:
            raise personal.PersonalError("选择需要不同的决定和有效原版本")
        seen.add(card_id)
        selected.append((card_id, revision))
    return tuple(selected)


def _changed():
    return personal.PersonalError("所选决定已变化或不可用，请重新读取后选择", "SHARE_SELECTION_CHANGED", 409)


def _needs_recheck(con, card_id):
    needs_recheck = False
    for ref in con.execute(
            "SELECT project_id,target_kind,target_id,target_fingerprint FROM personal_evidence_ref "
            "WHERE owner_kind='card' AND owner_id=? ORDER BY created_at,ref_id", (card_id,)):
        # Resolve every target in the original order, including legacy refs.
        # A later target error must not disappear behind an earlier stale ref.
        current = personal.resolve_target(con, ref["project_id"], ref["target_kind"], ref["target_id"])
        if not ref["target_fingerprint"] or current is None or current[1] != ref["target_fingerprint"]:
            needs_recheck = True
    return needs_recheck


def _card_summary(con, card_id):
    row = con.execute(
        "SELECT project_id,title,decision_text,decision_reason,state,revision,updated_at "
        "FROM decision_card WHERE card_id=?", (card_id,)).fetchone()
    if row is None:
        raise _changed()
    summary = dict(row)
    summary["needs_recheck"] = _needs_recheck(con, card_id)
    return summary


def snapshot_locked(con, project_id, data):
    """原 read_result 的真实只读事务回调；不接收 caller 卡片或摘要 DTO。"""
    project_id = _uuid(project_id)
    selection = normalize_selection(data)
    cards = []
    try:
        if con.execute("SELECT 1 FROM renovation_project WHERE project_id=?", (project_id,)).fetchone() is None:
            raise _changed()
        for card_id, revision in selection:
            current = con.execute("SELECT project_id,revision FROM decision_card WHERE card_id=?",
                (card_id,)).fetchone()
            if current is None or current["project_id"] != project_id or current["revision"] != revision:
                raise _changed()
            card = _card_summary(con, card_id)
            if card["project_id"] != project_id or card["revision"] != revision:
                raise _changed()
            cards.append(card)
    except personal.PersonalError as error:
        if error.code == "NOT_FOUND":
            raise _changed() from None
        raise
    try:
        return contract.project_snapshot(cards)
    except contract.ShareContractError:
        raise _changed() from None


def finalize_snapshot(snapshot):
    """只在原读取工厂成功退出后调用；纯字节摘要不声明发布或授权成功。"""
    try:
        raw = contract.encode_snapshot(snapshot)
        return {"snapshot": snapshot, "snapshot_sha256": contract.snapshot_sha256(raw)}
    except contract.ShareContractError:
        raise _changed() from None
