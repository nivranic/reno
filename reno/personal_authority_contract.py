"""H01 纯合同候选，尚未接入生产。

边界
----
本模块只校验不可信声明的结构与所选原字节的引用关系。没有文件/SQL、
时钟、随机数、网络、设施初始化或 runtime writer/factory 许可。
normalize/decode 返回的仍是声明；cross_validate 返回 None。成功不证明
真实 stop、安装、journal、profile、节点、签名、附件、fsync 或当前写权。
本模块不产 active/writable/verified 等运行时结论。

closed envelope
---------------
{format_version: 1, kind: KIND, domain_id: UUID, operation_id: UUID,
 issuer: {facility_id: UUID, node_id: UUID, profile_sha256: SHA},
 payload: PAYLOAD}
UUID 必须是调用者原有的非 nil canonical 小写连字符形式，不改写。
SHA 为 64 位小写 hex。epoch 是 1..2**63-1 的严格整数，revision 可为 0；
bool、float、NaN/Infinity、未来 epoch 溢出都拒绝。没有自动补字段。
endpoint = {facility_id, node_id, profile_sha256}，仅是声明身份 slot。

KIND 与 PAYLOAD（所有字段均必填；不接受额外字段）
------------------------------------------------
origin_init_intent:
  current_epoch, observed_primary_node_id, observed_personal_meta_sha256,
  observed_personal_sql_sha256
receiver_init_intent:
  origin: endpoint, domain_genesis_grant_sha256
local_contract:
  init_kind: "origin"|"receiver", init_intent_sha256
init_seal:
  init_intent_sha256, local_contract_sha256
genesis_grant:
  origin_init_intent_sha256, local_contract_sha256, init_seal_sha256, epoch
handoff_intent:
  predecessor, current_epoch, next_epoch, target: endpoint,
  promotion_operation_id, expected_personal_revision,
  expected_personal_sql_sha256
  predecessor = {kind: "genesis_grant", grant_sha256}
             or {kind: "promotion_grant", grant_sha256, receipt_sha256}
epoch_fence:
  handoff_intent_sha256, current_epoch, next_epoch
final_stop_receipt:
  handoff_intent_sha256, epoch_fence_sha256, target: endpoint,
  promotion_operation_id, current_epoch, next_epoch,
  final_snapshot_sha256, final_bundle_sha256,
  final_personal_sql_sha256, final_attachment_manifest_sha256
promotion_reservation:
  handoff_intent_sha256, final_stop_receipt_sha256,
  target_local_contract_sha256, target_init_seal_sha256, source: endpoint,
  current_epoch, next_epoch, installed_snapshot_sha256,
  target_installation_sha256, expected_target_sql_sha256
promotion_grant:
  final_stop_receipt_sha256, promotion_reservation_sha256,
  target_local_contract_sha256, target_init_seal_sha256,
  promotion_journal_sha256, epoch
promotion_receipt:
  final_stop_receipt_sha256, promotion_reservation_sha256,
  promotion_grant_sha256, promotion_journal_sha256, epoch

原 init、contract、seal（origin 时还有 genesis grant）共用原 init UUID。
handoff、fence、stop 共用另一 handoff UUID。target reservation/grant/receipt
共用 intent 中预先选定的 promotion UUID；三种操作族不得混用 UUID。
receiver init 不含 current epoch 或 primary 字段，不能产 genesis grant。
初次 origin 登记不含 target、next epoch 或尚未产生的 final SHA。
intent -> fence -> 已有 final package -> stop receipt 是无环顺序；
final_bundle_sha256 是外部声明，不在本合同中反向指向 stop receipt。

expected_personal_sql_sha256 / final_personal_sql_sha256 的声明范围是个人
十表的类型明确投影和个人 meta，不含新增 authority journal，也不含
knowledge library/worker 表。本候选强制两摘要相等；实际 snapshot revision、
原 committed SQL、附件完整性和 stop producer 退出由后续运行层核验。
其余 opaque SQL/journal/附件摘要也只声明字节，没有真实 proof 时不授权。

API
---
Limits: 调用者只可缩小每记录 32 KiB、64 条、合计 512 KiB、
        JSON container depth 12、每记录 512 个 JSON value nodes 的硬界限。
        depth 的根容器为 1；nodes 包含容器和 scalar，字典 key 不计。
normalize(dict, limits=...) -> 新 dict，不修改输入。
encode(dict, limits=...) -> sort_keys / UTF-8 / 无空白 canonical bytes。
decode(bytes, limits=...) -> 新 dict；拒绝重复 key、非 canonical 原字节。
record_sha256(bytes, limits=...) -> 已校验原 bytes 的 SHA，不重编码替换。
cross_validate(list|tuple[bytes], limits=...) -> None。
  可校验有界闭合前缀；每条显式 record 引用必须出现在所选原 bytes 中。
  无 grant 的 init 前缀也可结构闭合，绝不表示 active。
  还核 kind/domain/endpoint/operation/epoch、唯一 genesis、单 grant 单
  handoff、target 自身 contract/seal、promotion journal 声明一致及前驱谱系。
  此函数不接受 caller DTO 代替原 canonical bytes，也不判断真实时序。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import uuid

FORMAT_VERSION = 1
MAX_INTEGER = (1 << 63) - 1
HARD_LIMITS = {
    "max_record_bytes": 32 * 1024,
    "max_records": 64,
    "max_total_bytes": 512 * 1024,
    "max_json_depth": 12,
    "max_json_nodes": 512,
}
ENVELOPE_FIELDS = frozenset({
    "format_version", "kind", "domain_id", "operation_id", "issuer", "payload",
})
ENDPOINT_FIELDS = frozenset({"facility_id", "node_id", "profile_sha256"})
PAYLOAD_FIELDS = {
    "origin_init_intent": frozenset({
        "current_epoch", "observed_primary_node_id",
        "observed_personal_meta_sha256", "observed_personal_sql_sha256",
    }),
    "receiver_init_intent": frozenset({"origin", "domain_genesis_grant_sha256"}),
    "local_contract": frozenset({"init_kind", "init_intent_sha256"}),
    "init_seal": frozenset({"init_intent_sha256", "local_contract_sha256"}),
    "genesis_grant": frozenset({
        "origin_init_intent_sha256", "local_contract_sha256",
        "init_seal_sha256", "epoch",
    }),
    "handoff_intent": frozenset({
        "predecessor", "current_epoch", "next_epoch", "target",
        "promotion_operation_id", "expected_personal_revision",
        "expected_personal_sql_sha256",
    }),
    "epoch_fence": frozenset({
        "handoff_intent_sha256", "current_epoch", "next_epoch",
    }),
    "final_stop_receipt": frozenset({
        "handoff_intent_sha256", "epoch_fence_sha256", "target",
        "promotion_operation_id", "current_epoch", "next_epoch",
        "final_snapshot_sha256", "final_bundle_sha256",
        "final_personal_sql_sha256", "final_attachment_manifest_sha256",
    }),
    "promotion_reservation": frozenset({
        "handoff_intent_sha256", "final_stop_receipt_sha256",
        "target_local_contract_sha256", "target_init_seal_sha256", "source",
        "current_epoch", "next_epoch", "installed_snapshot_sha256",
        "target_installation_sha256", "expected_target_sql_sha256",
    }),
    "promotion_grant": frozenset({
        "final_stop_receipt_sha256", "promotion_reservation_sha256",
        "target_local_contract_sha256", "target_init_seal_sha256",
        "promotion_journal_sha256", "epoch",
    }),
    "promotion_receipt": frozenset({
        "final_stop_receipt_sha256", "promotion_reservation_sha256",
        "promotion_grant_sha256", "promotion_journal_sha256", "epoch",
    }),
}
KINDS = frozenset(PAYLOAD_FIELDS)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_INIT_KINDS = frozenset({"origin_init_intent", "receiver_init_intent"})
_GRANT_KINDS = frozenset({"genesis_grant", "promotion_grant"})


class ContractError(ValueError):
    """仅返回固定 code，永不回显 caller payload。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


@dataclass(frozen=True, slots=True)
class Limits:
    max_record_bytes: int = HARD_LIMITS["max_record_bytes"]
    max_records: int = HARD_LIMITS["max_records"]
    max_total_bytes: int = HARD_LIMITS["max_total_bytes"]
    max_json_depth: int = HARD_LIMITS["max_json_depth"]
    max_json_nodes: int = HARD_LIMITS["max_json_nodes"]

    def __post_init__(self):
        for name, maximum in HARD_LIMITS.items():
            value = getattr(self, name)
            _require(type(value) is int and 1 <= value <= maximum, "LIMIT_INVALID")


DEFAULT_LIMITS = Limits()


def _limits(value: Limits) -> Limits:
    _require(type(value) is Limits, "LIMIT_INVALID")
    # A caller cannot bypass validation via object.__setattr__ on a frozen DTO.
    value.__post_init__()
    return value


def _closed(value, fields: frozenset) -> dict:
    _require(type(value) is dict, "OBJECT_TYPE_INVALID")
    _require(all(type(key) is str for key in value), "KEY_TYPE_INVALID")
    _require(frozenset(value) == fields, "CLOSED_FIELDS_INVALID")
    return value


def _uuid(value) -> str:
    _require(type(value) is str and _UUID.fullmatch(value) is not None, "UUID_INVALID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ContractError("UUID_INVALID") from None
    _require(parsed.int != 0 and str(parsed) == value, "UUID_INVALID")
    return value


def _sha(value) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None, "SHA_INVALID")
    return value


def _integer(value, *, minimum: int = 1) -> int:
    _require(type(value) is int and minimum <= value <= MAX_INTEGER, "INTEGER_INVALID")
    return value


def _endpoint(value) -> dict:
    value = _closed(value, ENDPOINT_FIELDS)
    return {
        "facility_id": _uuid(value["facility_id"]),
        "node_id": _uuid(value["node_id"]),
        "profile_sha256": _sha(value["profile_sha256"]),
    }


def _predecessor(value) -> dict:
    _require(type(value) is dict and type(value.get("kind")) is str, "PREDECESSOR_INVALID")
    kind = value["kind"]
    _require(kind in _GRANT_KINDS, "PREDECESSOR_INVALID")
    fields = {"kind", "grant_sha256"}
    if kind == "promotion_grant":
        fields.add("receipt_sha256")
    value = _closed(value, frozenset(fields))
    result = {"kind": kind, "grant_sha256": _sha(value["grant_sha256"])}
    if kind == "promotion_grant":
        result["receipt_sha256"] = _sha(value["receipt_sha256"])
    return result


def _tree_limits(value, limits: Limits) -> None:
    # Iterate one child at a time: a wide caller object must not allocate an
    # unbounded intermediate stack before the node limit can reject it.
    stack = [(iter((value,)), 0, ())]
    nodes = 0
    while stack:
        iterator, parent_depth, ancestors = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        _require(nodes <= limits.max_json_nodes, "JSON_NODE_LIMIT")
        item_type = type(item)
        if item_type in (dict, list):
            depth = parent_depth + 1
            _require(depth <= limits.max_json_depth, "JSON_DEPTH_LIMIT")
            _require(id(item) not in ancestors, "CYCLIC_INPUT")
            chain = ancestors + (id(item),)
            children = item.values() if item_type is dict else item
            stack.append((iter(children), depth, chain))
        else:
            _require(item_type in (str, int, bool, type(None)), "JSON_VALUE_TYPE_INVALID")


def _lexical_depth(text: str, limits: Limits) -> None:
    depth, in_string, escaped = 0, False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            _require(depth <= limits.max_json_depth, "JSON_DEPTH_LIMIT")
        elif char in "]}":
            depth -= 1
            _require(depth >= 0, "JSON_INVALID")


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError("JSON_INVALID") from None


def normalize(value: dict, *, limits: Limits = DEFAULT_LIMITS) -> dict:
    limits = _limits(limits)
    _tree_limits(value, limits)
    value = _closed(value, ENVELOPE_FIELDS)
    _require(type(value["format_version"]) is int
             and value["format_version"] == FORMAT_VERSION, "VERSION_INVALID")
    kind = value["kind"]
    _require(type(kind) is str and kind in KINDS, "KIND_INVALID")
    original = _closed(value["payload"], PAYLOAD_FIELDS[kind])
    payload = {}
    for key in sorted(original):
        item = original[key]
        if key in {"origin", "source", "target"}:
            payload[key] = _endpoint(item)
        elif key == "predecessor":
            payload[key] = _predecessor(item)
        elif key == "init_kind":
            _require(type(item) is str and item in {"origin", "receiver"}, "INIT_KIND_INVALID")
            payload[key] = item
        elif key in {"observed_primary_node_id", "promotion_operation_id"}:
            payload[key] = _uuid(item)
        elif key.endswith("_sha256"):
            payload[key] = _sha(item)
        elif key in {"epoch", "current_epoch", "next_epoch"}:
            payload[key] = _integer(item)
        elif key == "expected_personal_revision":
            payload[key] = _integer(item, minimum=0)
        else:
            raise ContractError("PAYLOAD_SPEC_INVALID")
    if "current_epoch" in payload and "next_epoch" in payload:
        _require(payload["current_epoch"] < MAX_INTEGER
                 and payload["next_epoch"] == payload["current_epoch"] + 1, "NEXT_EPOCH_INVALID")
    result = {
        "format_version": FORMAT_VERSION,
        "kind": kind,
        "domain_id": _uuid(value["domain_id"]),
        "operation_id": _uuid(value["operation_id"]),
        "issuer": _endpoint(value["issuer"]),
        "payload": payload,
    }
    _require(len(_canonical(result)) <= limits.max_record_bytes, "RECORD_BYTES_LIMIT")
    return result


def encode(value: dict, *, limits: Limits = DEFAULT_LIMITS) -> bytes:
    return _canonical(normalize(value, limits=limits))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _parse_integer(text):
    digits = text[1:] if text.startswith("-") else text
    _require(len(digits) <= 19, "JSON_INTEGER_LIMIT")
    value = int(text)
    _require(-MAX_INTEGER <= value <= MAX_INTEGER, "JSON_INTEGER_LIMIT")
    return value


def _reject_float(_text):
    raise ContractError("JSON_FLOAT_UNSUPPORTED")


def _reject_constant(_text):
    raise ContractError("JSON_CONSTANT_UNSUPPORTED")


def decode(raw: bytes, *, limits: Limits = DEFAULT_LIMITS) -> dict:
    limits = _limits(limits)
    _require(type(raw) is bytes, "RAW_TYPE_INVALID")
    _require(0 < len(raw) <= limits.max_record_bytes, "RECORD_BYTES_LIMIT")
    try:
        text = raw.decode("utf-8", errors="strict")
        _lexical_depth(text, limits)
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_parse_integer,
                           parse_float=_reject_float, parse_constant=_reject_constant)
    except ContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ContractError("JSON_INVALID") from None
    result = normalize(value, limits=limits)
    _require(_canonical(result) == raw, "JSON_NONCANONICAL")
    return result


def record_sha256(raw: bytes, *, limits: Limits = DEFAULT_LIMITS) -> str:
    decode(raw, limits=limits)
    return hashlib.sha256(raw).hexdigest()


def _same_context(left: dict, right: dict, *, operation: bool = True) -> None:
    _require(left["domain_id"] == right["domain_id"], "DOMAIN_BINDING_INVALID")
    _require(left["issuer"] == right["issuer"], "ISSUER_BINDING_INVALID")
    if operation:
        _require(left["operation_id"] == right["operation_id"], "OPERATION_BINDING_INVALID")


def _same_epochs(left: dict, right: dict) -> None:
    _require(all(left["payload"][key] == right["payload"][key]
                 for key in ("current_epoch", "next_epoch")), "EPOCH_BINDING_INVALID")


def _different_endpoints(source: dict, target: dict) -> None:
    _require(source["facility_id"] != target["facility_id"]
             and source["node_id"] != target["node_id"], "TARGET_NOT_DISTINCT")


def _purpose(kind: str) -> str:
    if kind in _INIT_KINDS or kind in {"local_contract", "init_seal", "genesis_grant"}:
        return "init"
    if kind in {"handoff_intent", "epoch_fence", "final_stop_receipt"}:
        return "handoff"
    return "promotion"


def cross_validate(raw_records, *, limits: Limits = DEFAULT_LIMITS) -> None:
    """仅校验闭合的原 bytes 声明集；不返回 runtime 许可或真实 verified 结论。"""
    limits = _limits(limits)
    _require(type(raw_records) in (list, tuple), "RECORD_SET_TYPE_INVALID")
    _require(0 < len(raw_records) <= limits.max_records, "RECORD_COUNT_LIMIT")
    # Snapshot at most one excess item: a caller may change its list after
    # admission, but cannot enlarge this owned traversal or its allocation.
    selected = tuple(raw_records[:limits.max_records + 1])
    _require(0 < len(selected) <= limits.max_records, "RECORD_COUNT_LIMIT")
    records, edges, identities, operations = {}, {}, {}, {}
    endpoints, inits, genesis, successor, promotion_owners = {}, {}, {}, {}, {}
    total = 0
    for number, raw in enumerate(selected, 1):
        _require(number <= limits.max_records, "RECORD_COUNT_LIMIT")
        _require(type(raw) is bytes, "RAW_TYPE_INVALID")
        total += len(raw)
        _require(total <= limits.max_total_bytes, "TOTAL_BYTES_LIMIT")
        record = decode(raw, limits=limits)
        sha = hashlib.sha256(raw).hexdigest()
        _require(sha not in records, "DUPLICATE_RECORD")
        records[sha], edges[sha] = record, set()
        issuer = record["issuer"]
        endpoint_key = (issuer["facility_id"], issuer["node_id"], issuer["profile_sha256"])
        facility = issuer["facility_id"]
        _require(facility not in endpoints or endpoints[facility] == endpoint_key, "FACILITY_BINDING_INVALID")
        endpoints[facility] = endpoint_key
        identity = (record["domain_id"], record["kind"], facility, record["operation_id"])
        _require(identity not in identities, "RECORD_IDENTITY_CONFLICT")
        identities[identity] = sha
        operation = record["operation_id"]
        operation_binding = (_purpose(record["kind"]), record["domain_id"], endpoint_key)
        _require(operation not in operations or operations[operation] == operation_binding,
                 "OPERATION_PURPOSE_CONFLICT")
        operations[operation] = operation_binding
        if record["kind"] in _INIT_KINDS:
            slot = (record["domain_id"], facility)
            _require(slot not in inits, "FACILITY_INIT_CONFLICT")
            inits[slot] = sha
        if record["kind"] == "genesis_grant":
            _require(record["domain_id"] not in genesis, "DOMAIN_GENESIS_CONFLICT")
            genesis[record["domain_id"]] = sha
        if record["kind"] == "handoff_intent":
            payload = record["payload"]
            slot = (record["domain_id"], payload["predecessor"]["grant_sha256"])
            _require(slot not in successor, "GRANT_HANDOFF_CONFLICT")
            successor[slot] = sha
            promotion_id = payload["promotion_operation_id"]
            _require(promotion_id not in promotion_owners, "PROMOTION_OPERATION_CONFLICT")
            promotion_owners[promotion_id] = sha

    def reference(owner_sha, field, kinds, *, container=None):
        owner = records[owner_sha]
        data = owner["payload"] if container is None else container
        target_sha = data[field]
        _require(target_sha in records, "REFERENCE_MISSING")
        target = records[target_sha]
        _require(target["kind"] in kinds, "REFERENCE_KIND_INVALID")
        _require(owner["domain_id"] == target["domain_id"], "DOMAIN_BINDING_INVALID")
        edges[owner_sha].add(target_sha)
        return target

    for sha, record in records.items():
        kind, payload = record["kind"], record["payload"]
        if kind == "origin_init_intent":
            _require(payload["observed_primary_node_id"] == record["issuer"]["node_id"],
                     "ORIGIN_PRIMARY_BINDING_INVALID")
        elif kind == "receiver_init_intent":
            root = reference(sha, "domain_genesis_grant_sha256", {"genesis_grant"})
            _require(payload["origin"] == root["issuer"], "ORIGIN_BINDING_INVALID")
            _different_endpoints(root["issuer"], record["issuer"])
        elif kind == "local_contract":
            expected_kind = payload["init_kind"] + "_init_intent"
            init = reference(sha, "init_intent_sha256", {expected_kind})
            _same_context(record, init)
        elif kind == "init_seal":
            init = reference(sha, "init_intent_sha256", _INIT_KINDS)
            contract = reference(sha, "local_contract_sha256", {"local_contract"})
            _same_context(record, init)
            _same_context(record, contract)
            _require(contract["payload"]["init_intent_sha256"] == payload["init_intent_sha256"],
                     "INIT_LINK_INVALID")
            _require(contract["payload"]["init_kind"] + "_init_intent" == init["kind"], "INIT_LINK_INVALID")
        elif kind == "genesis_grant":
            init = reference(sha, "origin_init_intent_sha256", {"origin_init_intent"})
            contract = reference(sha, "local_contract_sha256", {"local_contract"})
            seal = reference(sha, "init_seal_sha256", {"init_seal"})
            for other in (init, contract, seal):
                _same_context(record, other)
            _require(contract["payload"]["init_kind"] == "origin", "GENESIS_ORIGIN_REQUIRED")
            _require(contract["payload"]["init_intent_sha256"] == payload["origin_init_intent_sha256"]
                     and seal["payload"]["init_intent_sha256"] == payload["origin_init_intent_sha256"]
                     and seal["payload"]["local_contract_sha256"] == payload["local_contract_sha256"],
                     "INIT_LINK_INVALID")
            _require(payload["epoch"] == init["payload"]["current_epoch"], "EPOCH_BINDING_INVALID")
        elif kind == "handoff_intent":
            predecessor = payload["predecessor"]
            grant = reference(sha, "grant_sha256", {predecessor["kind"]}, container=predecessor)
            _same_context(record, grant, operation=False)
            _require(payload["current_epoch"] == grant["payload"]["epoch"], "EPOCH_BINDING_INVALID")
            _different_endpoints(record["issuer"], payload["target"])
            _require(payload["promotion_operation_id"] != record["operation_id"], "OPERATION_PURPOSE_CONFLICT")
            if predecessor["kind"] == "promotion_grant":
                receipt = reference(sha, "receipt_sha256", {"promotion_receipt"}, container=predecessor)
                _same_context(grant, receipt)
                _require(receipt["payload"]["promotion_grant_sha256"] == predecessor["grant_sha256"],
                         "PREDECESSOR_RECEIPT_INVALID")
        elif kind == "epoch_fence":
            handoff = reference(sha, "handoff_intent_sha256", {"handoff_intent"})
            _same_context(record, handoff)
            _same_epochs(record, handoff)
        elif kind == "final_stop_receipt":
            handoff = reference(sha, "handoff_intent_sha256", {"handoff_intent"})
            fence = reference(sha, "epoch_fence_sha256", {"epoch_fence"})
            _same_context(record, handoff)
            _same_context(record, fence)
            _same_epochs(record, handoff)
            _same_epochs(record, fence)
            _require(fence["payload"]["handoff_intent_sha256"] == payload["handoff_intent_sha256"],
                     "FENCE_LINK_INVALID")
            for key in ("target", "promotion_operation_id"):
                _require(payload[key] == handoff["payload"][key], "TARGET_BINDING_INVALID")
            _require(payload["final_personal_sql_sha256"] == handoff["payload"]["expected_personal_sql_sha256"],
                     "PREVIEW_SQL_CHANGED")
        elif kind == "promotion_reservation":
            handoff = reference(sha, "handoff_intent_sha256", {"handoff_intent"})
            stop = reference(sha, "final_stop_receipt_sha256", {"final_stop_receipt"})
            contract = reference(sha, "target_local_contract_sha256", {"local_contract"})
            seal = reference(sha, "target_init_seal_sha256", {"init_seal"})
            _same_context(handoff, stop)
            _same_context(record, contract, operation=False)
            _same_context(record, seal, operation=False)
            _same_context(contract, seal)
            _same_epochs(record, handoff)
            _same_epochs(record, stop)
            _require(stop["payload"]["handoff_intent_sha256"] == payload["handoff_intent_sha256"], "STOP_LINK_INVALID")
            _require(payload["source"] == handoff["issuer"]
                     and record["issuer"] == handoff["payload"]["target"], "TARGET_BINDING_INVALID")
            _different_endpoints(payload["source"], record["issuer"])
            _require(record["operation_id"] == handoff["payload"]["promotion_operation_id"],
                     "OPERATION_BINDING_INVALID")
            _require(seal["payload"]["local_contract_sha256"] == payload["target_local_contract_sha256"],
                     "INIT_LINK_INVALID")
            _require(payload["installed_snapshot_sha256"] == stop["payload"]["final_snapshot_sha256"],
                     "SNAPSHOT_BINDING_INVALID")
        elif kind == "promotion_grant":
            stop = reference(sha, "final_stop_receipt_sha256", {"final_stop_receipt"})
            reservation = reference(sha, "promotion_reservation_sha256", {"promotion_reservation"})
            contract = reference(sha, "target_local_contract_sha256", {"local_contract"})
            seal = reference(sha, "target_init_seal_sha256", {"init_seal"})
            _same_context(record, reservation)
            _same_context(record, contract, operation=False)
            _same_context(record, seal, operation=False)
            for key in ("final_stop_receipt_sha256", "target_local_contract_sha256", "target_init_seal_sha256"):
                _require(payload[key] == reservation["payload"][key], "RESERVATION_LINK_INVALID")
            _require(payload["epoch"] == reservation["payload"]["next_epoch"]
                     and payload["epoch"] == stop["payload"]["next_epoch"], "EPOCH_BINDING_INVALID")
        elif kind == "promotion_receipt":
            stop = reference(sha, "final_stop_receipt_sha256", {"final_stop_receipt"})
            reservation = reference(sha, "promotion_reservation_sha256", {"promotion_reservation"})
            grant = reference(sha, "promotion_grant_sha256", {"promotion_grant"})
            _same_context(record, reservation)
            _same_context(record, grant)
            for key in ("final_stop_receipt_sha256", "promotion_reservation_sha256", "promotion_journal_sha256", "epoch"):
                _require(payload[key] == grant["payload"][key], "PROMOTION_RECEIPT_BINDING_INVALID")
            _require(payload["epoch"] == reservation["payload"]["next_epoch"]
                     and payload["epoch"] == stop["payload"]["next_epoch"], "EPOCH_BINDING_INVALID")

    # Preserve specific reference/link errors first, then require every
    # declared endpoint to agree with any known facility in this prefix.
    for record in records.values():
        for field in ("origin", "source", "target"):
            if field not in record["payload"]:
                continue
            endpoint = record["payload"][field]
            key = (endpoint["facility_id"], endpoint["node_id"], endpoint["profile_sha256"])
            identifier = endpoint["facility_id"]
            _require(identifier not in endpoints or endpoints[identifier] == key, "FACILITY_BINDING_INVALID")
            endpoints[identifier] = key

    for promotion_id, owner_sha in promotion_owners.items():
        handoff = records[owner_sha]
        target = handoff["payload"]["target"]
        if promotion_id in operations:
            target_key = (target["facility_id"], target["node_id"], target["profile_sha256"])
            _require(operations[promotion_id] == ("promotion", handoff["domain_id"], target_key),
                     "OPERATION_PURPOSE_CONFLICT")

    visiting, visited = set(), set()

    def visit(sha):
        _require(sha not in visiting, "REFERENCE_CYCLE")
        if sha in visited:
            return
        visiting.add(sha)
        for target_sha in edges[sha]:
            visit(target_sha)
        visiting.remove(sha)
        visited.add(sha)

    for sha in records:
        visit(sha)

    def root_genesis(grant_sha):
        seen = set()
        while True:
            _require(grant_sha not in seen, "REFERENCE_CYCLE")
            seen.add(grant_sha)
            grant = records[grant_sha]
            if grant["kind"] == "genesis_grant":
                return grant_sha
            reservation = records[grant["payload"]["promotion_reservation_sha256"]]
            handoff = records[reservation["payload"]["handoff_intent_sha256"]]
            grant_sha = handoff["payload"]["predecessor"]["grant_sha256"]

    for record in records.values():
        if record["kind"] != "promotion_reservation":
            continue
        payload = record["payload"]
        contract = records[payload["target_local_contract_sha256"]]
        init = records[contract["payload"]["init_intent_sha256"]]
        handoff = records[payload["handoff_intent_sha256"]]
        lineage_root = root_genesis(handoff["payload"]["predecessor"]["grant_sha256"])
        if init["kind"] == "receiver_init_intent":
            _require(init["payload"]["domain_genesis_grant_sha256"] == lineage_root,
                     "GENESIS_LINEAGE_INVALID")
        else:
            _require(records[lineage_root]["payload"]["origin_init_intent_sha256"]
                     == contract["payload"]["init_intent_sha256"], "GENESIS_LINEAGE_INVALID")
    return None
