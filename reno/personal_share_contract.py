"""个人决定摘要的纯声明合同；原字节冻结不证明访问、发布或撤销权限。

只投影本人预览已有的七字段，不读取被排除字段的值。摘要文本仍可能包含
私人信息，分享前需要本人确认；这里保留原文，不替换候选标签或清理标记。
本模块没有 SQL、文件、令牌、签名、网络、随机数、时钟或运行时授权。
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re


FORMAT_VERSION = 1
SNAPSHOT_KIND = "personal_decision_share_snapshot"
DECISION_FIELDS = ("title", "decision_text", "decision_reason", "state", "revision",
                   "updated_at", "needs_recheck")
CARD_STATES = frozenset({"considering", "adopted", "not_adopted", "archived"})
MAX_DECISIONS = 50
MAX_SNAPSHOT_BYTES = 3 * 1024 * 1024
MAX_REVISION = (1 << 63) - 1
_TEXT_LIMITS = (("title", 200), ("decision_text", 4000), ("decision_reason", 4000))
_SNAPSHOT_FIELDS = frozenset({"format_version", "kind", "decisions"})
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")


class ShareContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise ShareContractError(code)


def _closed(value, fields):
    _need(type(value) is dict and len(value) == len(fields)
          and all(type(key) is str for key in value) and value.keys() == set(fields),
          "SHARE_FIELDS_INVALID")


def _decision(value):
    _closed(value, DECISION_FIELDS)
    for field, maximum in _TEXT_LIMITS:
        text = value[field]
        _need(type(text) is str and len(text) <= maximum and "\x00" not in text,
              "SHARE_TEXT_INVALID")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise ShareContractError("SHARE_TEXT_INVALID") from None
    _need(bool(value["title"].strip()), "SHARE_TITLE_REQUIRED")
    _need(type(value["state"]) is str and value["state"] in CARD_STATES, "SHARE_STATE_INVALID")
    _need(type(value["revision"]) is int and 1 <= value["revision"] <= MAX_REVISION,
          "SHARE_REVISION_INVALID")
    _need(type(value["needs_recheck"]) is bool, "SHARE_RECHECK_INVALID")
    stamp = value["updated_at"]
    _need(type(stamp) is str and _STAMP.fullmatch(stamp) is not None, "SHARE_TIMESTAMP_INVALID")
    try:
        datetime.fromisoformat(stamp[:-1] + "+00:00")
    except ValueError:
        raise ShareContractError("SHARE_TIMESTAMP_INVALID") from None
    return {field: value[field] for field in DECISION_FIELDS}


def project_decision(source):
    """只读取 exact dict 的七个摘要值；其他字段及其值不遍历、不复制。"""
    _need(type(source) is dict, "SHARE_SOURCE_INVALID")
    try:
        selected = {field: dict.__getitem__(source, field) for field in DECISION_FIELDS}
    except KeyError:
        raise ShareContractError("SHARE_FIELDS_INVALID") from None
    return _decision(selected)


def project_snapshot(sources):
    """保持选集顺序及相同内容的重复项，不推断内部身份或去重。"""
    _need(type(sources) in (list, tuple) and 1 <= len(sources) <= MAX_DECISIONS,
          "SHARE_DECISION_COUNT_INVALID")
    return {"format_version": FORMAT_VERSION, "kind": SNAPSHOT_KIND,
            "decisions": [project_decision(source) for source in sources]}


def normalize_snapshot(value):
    """校验闭合声明并返回独立容器；成功不授予任何运行时权限。"""
    _closed(value, _SNAPSHOT_FIELDS)
    _need(type(value["format_version"]) is int and value["format_version"] == FORMAT_VERSION
          and type(value["kind"]) is str and value["kind"] == SNAPSHOT_KIND,
          "SHARE_VERSION_OR_KIND_INVALID")
    decisions = value["decisions"]
    _need(type(decisions) is list and 1 <= len(decisions) <= MAX_DECISIONS,
          "SHARE_DECISION_COUNT_INVALID")
    return {"format_version": FORMAT_VERSION, "kind": SNAPSHOT_KIND,
            "decisions": [_decision(decision) for decision in decisions]}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


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
            _need(depth <= 3, "SHARE_JSON_LIMIT")
        elif byte in (93, 125):
            depth -= 1
        elif byte == 44:
            commas += 1
            _need(commas <= MAX_DECISIONS * len(DECISION_FIELDS) + 1, "SHARE_JSON_LIMIT")


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
    """仅对已校验的原 canonical bytes 求摘要，不重编码替换原字节。"""
    decode_snapshot(raw)
    return hashlib.sha256(raw).hexdigest()
