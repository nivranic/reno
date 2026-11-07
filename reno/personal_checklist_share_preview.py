"""只读本人核对单摘要；原事务内核选集，原读取工厂成功退出后才冻结字节。

沿 checklists.detail 读取业务所需的判据、依据和观察，响应仅投影摘要白名单。
不创建分享链接、凭据、持久化副本、访问权限或撤销权限。
"""
import uuid

from . import checklists, personal, personal_share_preview
from . import personal_checklist_share_contract as contract


def require_enabled():
    personal_share_preview.require_enabled()
    if not personal._enabled("field_checks_enabled"):
        raise personal.PersonalError("现场核对尚未启用", "CAPABILITY_DISABLED", 403)


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise personal.PersonalError("预览选择必须使用原规范 UUID") from None
    return value


def _integer(value, minimum=1):
    if type(value) is not int or not minimum <= value <= contract.MAX_REVISION:
        raise personal.PersonalError("选择需要有效的原版本和记录序号")
    return value


def normalize_selection(data):
    if type(data) is not dict or set(data) != {"selection"}:
        raise personal.PersonalError("预览只接受 selection")
    selection = data["selection"]
    if type(selection) is not list or len(selection) != 1:
        raise personal.PersonalError("请选择一份完整核对单预览")
    selected = selection[0]
    if type(selected) is not dict or set(selected) != {"instance_id", "revision", "items"}:
        raise personal.PersonalError("选择只接受 instance_id、revision 和 items")
    instance_id, revision = _uuid(selected["instance_id"]), _integer(selected["revision"])
    items = selected["items"]
    if type(items) is not list or not 1 <= len(items) <= contract.MAX_ITEMS:
        raise personal.PersonalError("请选择完整的 1 至 50 个核对项")
    expected, seen = [], set()
    for item in items:
        if type(item) is not dict or set(item) != {"item_id", "revision", "last_commit_seq"}:
            raise personal.PersonalError("核对项只接受 item_id、revision 和 last_commit_seq")
        item_id = _uuid(item["item_id"])
        if item_id in seen:
            raise personal.PersonalError("核对项不能重复")
        seen.add(item_id)
        expected.append((item_id, _integer(item["revision"]), _integer(item["last_commit_seq"], 0)))
    return instance_id, revision, tuple(expected)


def _changed():
    return personal.PersonalError("所选核对单或记录已变化或不可用，请重新读取后预览", "SHARE_SELECTION_CHANGED", 409)


def snapshot_locked(con, project_id, data):
    """只接原选择标记；记录追加/整改/作废不会递增 revision，须另核全部 commit_seq。"""
    project_id = _uuid(project_id)
    instance_id, revision, expected = normalize_selection(data)
    if con.execute("SELECT 1 FROM renovation_project WHERE project_id=?", (project_id,)).fetchone() is None:
        raise _changed()
    current = con.execute("SELECT project_id,revision FROM checklist_instance WHERE instance_id=?", (instance_id,)).fetchone()
    if current is None or current["project_id"] != project_id or current["revision"] != revision:
        raise _changed()
    markers = tuple(tuple(row) for row in con.execute(
        "SELECT i.item_id,i.revision,coalesce((SELECT max(c.commit_seq) FROM checklist_check c "
        "WHERE c.item_id=i.item_id),0) FROM checklist_item i WHERE i.instance_id=? ORDER BY i.position,i.item_id",
        (instance_id,)))
    if markers != expected:
        raise _changed()
    try:
        # External references/attachments can change without a checklist revision.
        # Recompute every original result, recheck flag and progress in this read.
        summary = checklists.detail(con, instance_id)
        return contract.project_snapshot([summary])
    except personal.PersonalError as error:
        if error.code == "NOT_FOUND":
            raise _changed() from None
        raise
    except contract.ShareContractError:
        raise _changed() from None


def finalize_snapshot(snapshot):
    """仅在原 read_result 完整成功退出之后调用。"""
    try:
        raw = contract.encode_snapshot(snapshot)
        return {"snapshot": snapshot, "snapshot_sha256": contract.snapshot_sha256(raw)}
    except contract.ShareContractError:
        raise _changed() from None
