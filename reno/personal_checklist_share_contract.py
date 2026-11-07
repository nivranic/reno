"""本人核对单摘要的纯白名单合同；不声明工程验收、发布或访问权限。

投影仅取声明字段；调用方仍须先按原业务规则计算结果和待复核状态。
标题保留本人原文，可能含私人信息，需要本人预览确认。
"""
from datetime import datetime
import hashlib
import json
import re


FORMAT_VERSION = 1
SNAPSHOT_KIND = "personal_checklist_share_snapshot"
CHECKLIST_FIELDS = ("title", "state", "lifecycle_state", "revision", "updated_at", "progress", "items")
ITEM_FIELDS = ("title", "revision", "current_result", "needs_recheck", "basis_kind")
MAX_ITEMS = 50
MAX_REVISION = (1 << 63) - 1
MAX_SNAPSHOT_BYTES = 128 * 1024
RESULTS = frozenset({"unchecked", "pass", "fail", "unable_to_verify", "not_applicable"})
BASIS = frozenset({"standard_clause", "manufacturer_instruction", "personal_requirement",
                   "video_suggestion", "observation_only"})
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")


class ShareContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise ShareContractError(code)


def _closed(value, fields):
    _need(type(value) is dict and all(type(key) is str for key in value)
          and value.keys() == set(fields), "SHARE_FIELDS_INVALID")


def _select(source, fields):
    _need(type(source) is dict, "SHARE_SOURCE_INVALID")
    try:
        return {field: dict.__getitem__(source, field) for field in fields}
    except KeyError:
        raise ShareContractError("SHARE_FIELDS_INVALID") from None


def _title(value):
    _need(type(value) is str and len(value) <= 200 and "\x00" not in value, "SHARE_TEXT_INVALID")
    _need(bool(value.strip()), "SHARE_TITLE_REQUIRED")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ShareContractError("SHARE_TEXT_INVALID") from None


def _revision(value):
    _need(type(value) is int and 1 <= value <= MAX_REVISION, "SHARE_REVISION_INVALID")


def _item(value):
    _closed(value, ITEM_FIELDS)
    _title(value["title"])
    _revision(value["revision"])
    _need(type(value["current_result"]) is str and value["current_result"] in RESULTS, "SHARE_RESULT_INVALID")
    _need(type(value["needs_recheck"]) is bool, "SHARE_RECHECK_INVALID")
    _need(type(value["basis_kind"]) is str and value["basis_kind"] in BASIS, "SHARE_BASIS_INVALID")
    return {field: value[field] for field in ITEM_FIELDS}


def _checklist(value):
    _closed(value, CHECKLIST_FIELDS)
    _title(value["title"])
    _revision(value["revision"])
    stamp = value["updated_at"]
    _need(type(stamp) is str and _STAMP.fullmatch(stamp) is not None, "SHARE_TIMESTAMP_INVALID")
    try:
        datetime.fromisoformat(stamp[:-1] + "+00:00")
    except ValueError:
        raise ShareContractError("SHARE_TIMESTAMP_INVALID") from None
    _need(type(value["lifecycle_state"]) is str and value["lifecycle_state"] in {"active", "archived"},
          "SHARE_STATE_INVALID")
    _need(type(value["items"]) is list and 1 <= len(value["items"]) <= MAX_ITEMS, "SHARE_ITEM_COUNT_INVALID")
    items = [_item(item) for item in value["items"]]
    progress = value["progress"]
    _closed(progress, ("total", "complete"))
    complete = sum(item["current_result"] in {"pass", "not_applicable"} and not item["needs_recheck"]
                   for item in items)
    _need(type(progress["total"]) is int and type(progress["complete"]) is int
          and progress == {"total": len(items), "complete": complete}, "SHARE_PROGRESS_INVALID")
    state = "archived" if value["lifecycle_state"] == "archived" else "completed" if complete == len(items) else "active"
    _need(type(value["state"]) is str and value["state"] == state, "SHARE_STATE_INVALID")
    return {**{field: value[field] for field in CHECKLIST_FIELDS if field not in {"progress", "items"}},
            "progress": dict(progress), "items": items}


def project_checklist(source):
    """纯投影仅读取白名单；不遍历、复制或解析被排除的值。"""
    selected = _select(source, CHECKLIST_FIELDS)
    _need(type(selected["items"]) is list and 1 <= len(selected["items"]) <= MAX_ITEMS,
          "SHARE_ITEM_COUNT_INVALID")
    selected["items"] = [_select(item, ITEM_FIELDS) for item in selected["items"]]
    selected["progress"] = _select(selected["progress"], ("total", "complete"))
    return _checklist(selected)


def project_snapshot(sources):
    _need(type(sources) in (list, tuple) and len(sources) == 1, "SHARE_CHECKLIST_COUNT_INVALID")
    return {"format_version": FORMAT_VERSION, "kind": SNAPSHOT_KIND,
            "checklists": [project_checklist(sources[0])]}


def normalize_snapshot(value):
    _closed(value, ("format_version", "kind", "checklists"))
    _need(type(value["format_version"]) is int and value["format_version"] == FORMAT_VERSION
          and type(value["kind"]) is str and value["kind"] == SNAPSHOT_KIND, "SHARE_VERSION_OR_KIND_INVALID")
    _need(type(value["checklists"]) is list and len(value["checklists"]) == 1, "SHARE_CHECKLIST_COUNT_INVALID")
    return {"format_version": FORMAT_VERSION, "kind": SNAPSHOT_KIND,
            "checklists": [_checklist(value["checklists"][0])]}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def encode_snapshot(value):
    raw = _canonical(normalize_snapshot(value))
    _need(len(raw) <= MAX_SNAPSHOT_BYTES, "SHARE_BYTES_LIMIT")
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "SHARE_DUPLICATE_FIELD")
        result[key] = value
    return result


def _integer(text):
    _need(len(text) <= 19, "SHARE_INTEGER_INVALID")
    return int(text)


def _no_number(_text):
    raise ShareContractError("SHARE_JSON_INVALID")


def _preflight(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_SNAPSHOT_BYTES, "SHARE_BYTES_LIMIT")
    depth = commas = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (91, 123):
            depth += 1
            _need(depth <= 5, "SHARE_JSON_LIMIT")
        elif byte in (93, 125):
            depth -= 1
        elif byte == 44:
            commas += 1
            _need(commas <= MAX_ITEMS * len(ITEM_FIELDS) + 10, "SHARE_JSON_LIMIT")


def decode_snapshot(raw):
    _preflight(raw)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=_no_number, parse_constant=_no_number)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError):
        raise ShareContractError("SHARE_JSON_INVALID") from None
    normalized = normalize_snapshot(value)
    _need(_canonical(normalized) == raw, "SHARE_NONCANONICAL")
    return normalized


def snapshot_sha256(raw):
    decode_snapshot(raw)
    return hashlib.sha256(raw).hexdigest()
