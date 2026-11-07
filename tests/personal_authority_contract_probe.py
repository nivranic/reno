"""只 import 同目录纯候选的合成 direct assert；不使用 pytest 或真实资料。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reno import personal_authority_contract as contract
MODULE_PATH = Path(contract.__file__).resolve()
assert MODULE_PATH == Path(__file__).resolve().parents[1] / "reno/personal_authority_contract.py"

CHECKS = 0


def positive(label, action):
    global CHECKS
    assert action() is not False, label
    CHECKS += 1


def rejected(label, action, code=None):
    global CHECKS
    try:
        action()
    except contract.ContractError as error:
        assert type(error.code) is str and str(error) == error.code, label
        if code is not None:
            assert error.code == code, (label, error.code, code)
    else:
        raise AssertionError(label)
    CHECKS += 1


def uid(number):
    return "00000000-0000-4000-8000-" + format(number, "012x")


def digest(label):
    return hashlib.sha256(("synthetic:" + label).encode()).hexdigest()


DOMAIN = uid(900)
SOURCE = {"facility_id": uid(101), "node_id": uid(11), "profile_sha256": digest("source-profile")}
TARGET = {"facility_id": uid(102), "node_id": uid(12), "profile_sha256": digest("target-profile")}
THIRD = {"facility_id": uid(103), "node_id": uid(13), "profile_sha256": digest("third-profile")}


def record(kind, issuer, operation, payload):
    return {"format_version": 1, "kind": kind, "domain_id": DOMAIN,
            "operation_id": uid(operation), "issuer": deepcopy(issuer),
            "payload": deepcopy(payload)}


def raw_sha(value):
    return contract.record_sha256(contract.encode(value))


def make_chain(*, second=False, return_to_origin=False):
    items = {}

    def put(name, kind, issuer, operation, payload):
        items[name] = record(kind, issuer, operation, payload)
        return raw_sha(items[name])

    init_sha = put("origin_init", "origin_init_intent", SOURCE, 1, {
        "current_epoch": 7, "observed_primary_node_id": SOURCE["node_id"],
        "observed_personal_meta_sha256": digest("origin-meta"),
        "observed_personal_sql_sha256": digest("origin-ten-tables-and-personal-meta"),
    })
    source_contract = put("origin_contract", "local_contract", SOURCE, 1, {
        "init_kind": "origin", "init_intent_sha256": init_sha,
    })
    source_seal = put("origin_seal", "init_seal", SOURCE, 1, {
        "init_intent_sha256": init_sha, "local_contract_sha256": source_contract,
    })
    genesis_sha = put("genesis", "genesis_grant", SOURCE, 1, {
        "origin_init_intent_sha256": init_sha, "local_contract_sha256": source_contract,
        "init_seal_sha256": source_seal, "epoch": 7,
    })

    def receiver(prefix, endpoint, operation):
        receiver_init = put(prefix + "_init", "receiver_init_intent", endpoint, operation, {
            "origin": SOURCE, "domain_genesis_grant_sha256": genesis_sha,
        })
        local = put(prefix + "_contract", "local_contract", endpoint, operation, {
            "init_kind": "receiver", "init_intent_sha256": receiver_init,
        })
        seal = put(prefix + "_seal", "init_seal", endpoint, operation, {
            "init_intent_sha256": receiver_init, "local_contract_sha256": local,
        })
        return local, seal

    target_contract, target_seal = receiver("receiver", TARGET, 2)

    def handoff(prefix, source, target, current_epoch, operation, promotion_operation,
                predecessor, local_contract_sha, seal_sha):
        expected_sql = digest(prefix + "-ten-tables-and-personal-meta-no-authority-journal")
        handoff_sha = put(prefix + "_intent", "handoff_intent", source, operation, {
            "predecessor": predecessor, "current_epoch": current_epoch,
            "next_epoch": current_epoch + 1, "target": target,
            "promotion_operation_id": uid(promotion_operation),
            "expected_personal_revision": 23, "expected_personal_sql_sha256": expected_sql,
        })
        fence_sha = put(prefix + "_fence", "epoch_fence", source, operation, {
            "handoff_intent_sha256": handoff_sha, "current_epoch": current_epoch,
            "next_epoch": current_epoch + 1,
        })
        snapshot = digest(prefix + "-snapshot")
        stop_sha = put(prefix + "_stop", "final_stop_receipt", source, operation, {
            "handoff_intent_sha256": handoff_sha, "epoch_fence_sha256": fence_sha,
            "target": target, "promotion_operation_id": uid(promotion_operation),
            "current_epoch": current_epoch, "next_epoch": current_epoch + 1,
            "final_snapshot_sha256": snapshot,
            "final_bundle_sha256": digest(prefix + "-package-before-stop-receipt"),
            "final_personal_sql_sha256": expected_sql,
            "final_attachment_manifest_sha256": digest(prefix + "-attachment-manifest"),
        })
        reservation_sha = put(prefix + "_reservation", "promotion_reservation", target, promotion_operation, {
            "handoff_intent_sha256": handoff_sha, "final_stop_receipt_sha256": stop_sha,
            "target_local_contract_sha256": local_contract_sha, "target_init_seal_sha256": seal_sha,
            "source": source, "current_epoch": current_epoch, "next_epoch": current_epoch + 1,
            "installed_snapshot_sha256": snapshot,
            "target_installation_sha256": digest(prefix + "-claimed-installation"),
            "expected_target_sql_sha256": digest(prefix + "-claimed-target-sql-before"),
        })
        journal = digest(prefix + "-claimed-target-promotion-journal")
        grant_sha = put(prefix + "_grant", "promotion_grant", target, promotion_operation, {
            "final_stop_receipt_sha256": stop_sha, "promotion_reservation_sha256": reservation_sha,
            "target_local_contract_sha256": local_contract_sha, "target_init_seal_sha256": seal_sha,
            "promotion_journal_sha256": journal, "epoch": current_epoch + 1,
        })
        receipt_sha = put(prefix + "_receipt", "promotion_receipt", target, promotion_operation, {
            "final_stop_receipt_sha256": stop_sha, "promotion_reservation_sha256": reservation_sha,
            "promotion_grant_sha256": grant_sha, "promotion_journal_sha256": journal,
            "epoch": current_epoch + 1,
        })
        return grant_sha, receipt_sha

    grant, receipt = handoff("first", SOURCE, TARGET, 7, 3, 4,
        {"kind": "genesis_grant", "grant_sha256": genesis_sha}, target_contract, target_seal)
    if second:
        if return_to_origin:
            next_target, next_contract, next_seal = SOURCE, source_contract, source_seal
        else:
            next_target = THIRD
            next_contract, next_seal = receiver("third", THIRD, 5)
        handoff("second", TARGET, next_target, 8, 6, 7,
            {"kind": "promotion_grant", "grant_sha256": grant, "receipt_sha256": receipt},
            next_contract, next_seal)
    return items


def raws(items):
    return [contract.encode(item) for item in items.values()]


def rewrite(items, name, action):
    """合成 fixture 重新锚定后继 raw SHA；生产候选没有此便利函数。"""
    rewritten, replacements = {}, {}

    def replace(value):
        if type(value) is dict:
            return {key: replace(item) for key, item in value.items()}
        if type(value) is list:
            return [replace(item) for item in value]
        if type(value) is str:
            return replacements.get(value, value)
        return value

    for key, original in items.items():
        changed = deepcopy(original)
        if key == name:
            action(changed)
        changed = replace(changed)
        old_sha, new_sha = raw_sha(original), raw_sha(changed)
        rewritten[key] = changed
        if old_sha != new_sha:
            replacements[old_sha] = new_sha
    return rewritten


def binding_negative(label, name, action, code=None, *, items=None):
    original = BASE if items is None else items
    rejected(label, lambda: contract.cross_validate(raws(rewrite(original, name, action))), code)


BASE = make_chain()
SECOND = make_chain(second=True)
RETURN = make_chain(second=True, return_to_origin=True)
INTENT_PREFIX = dict(list(BASE.items())[:8])
assert {item["kind"] for item in BASE.values()} == contract.KINDS

for name, item in BASE.items():
    encoded = contract.encode(item)
    before = deepcopy(item)
    positive(name + "-roundtrip", lambda item=item, encoded=encoded: contract.decode(encoded) == item)
    positive(name + "-raw-hash", lambda encoded=encoded: contract.record_sha256(encoded) == hashlib.sha256(encoded).hexdigest())
    normalized = contract.normalize(item)
    normalized["issuer"]["node_id"] = uid(999)
    positive(name + "-no-input-mutation", lambda item=item, before=before: item == before)
    positive(name + "-no-runtime-output", lambda encoded=encoded:
             set(contract.decode(encoded)) == contract.ENVELOPE_FIELDS)

positive("full-first-declaration-chain", lambda: contract.cross_validate(raws(BASE)) is None)
positive("out-of-order-explicit-links", lambda: contract.cross_validate(list(reversed(raws(BASE)))) is None)
positive("second-handoff-lineage", lambda: contract.cross_validate(raws(SECOND)) is None)
positive("future-higher-epoch-origin-return-no-new-genesis", lambda: contract.cross_validate(raws(RETURN)) is None)
for length in (1, 3, 4, 7, 8, 9, 10, 11, 12):
    positive("closed-prefix-" + str(length),
             lambda length=length: contract.cross_validate(tuple(raws(BASE)[:length])) is None)

INIT = BASE["origin_init"]
INIT_RAW = contract.encode(INIT)

rejected("duplicate-root-key",
         lambda: contract.decode(INIT_RAW.replace(b'"format_version":1', b'"format_version":1,"format_version":1')),
         "JSON_DUPLICATE_KEY")
rejected("duplicate-nested-key",
         lambda: contract.decode(INIT_RAW.replace(b'"facility_id":', b'"facility_id":"duplicate","facility_id":', 1)),
         "JSON_DUPLICATE_KEY")
rejected("pretty-noncanonical", lambda: contract.decode(json.dumps(INIT, indent=2).encode()), "JSON_NONCANONICAL")
rejected("trailing-newline", lambda: contract.decode(INIT_RAW + b"\n"), "JSON_NONCANONICAL")
rejected("different-key-order", lambda: contract.decode(json.dumps(INIT, separators=(",", ":")).encode()),
         "JSON_NONCANONICAL")
rejected("escaped-equivalent-character",
         lambda: contract.decode(INIT_RAW.replace(b'"origin_init_intent"', b'"origin_init_inten\\u0074"')),
         "JSON_NONCANONICAL")
rejected("utf8-bom", lambda: contract.decode(b"\xef\xbb\xbf" + INIT_RAW), "JSON_INVALID")
rejected("bad-utf8", lambda: contract.decode(b"\xff" + INIT_RAW), "JSON_INVALID")
rejected("raw-str-not-bytes", lambda: contract.decode(INIT_RAW.decode()), "RAW_TYPE_INVALID")
rejected("raw-bytearray-not-bytes", lambda: contract.decode(bytearray(INIT_RAW)), "RAW_TYPE_INVALID")
rejected("empty-raw", lambda: contract.decode(b""), "RECORD_BYTES_LIMIT")
for literal, expected in ((b"7.0", "JSON_FLOAT_UNSUPPORTED"),
                          (b"NaN", "JSON_CONSTANT_UNSUPPORTED"),
                          (b"Infinity", "JSON_CONSTANT_UNSUPPORTED"),
                          (b"true", "INTEGER_INVALID"),
                          (b"9" * 1000, "JSON_INTEGER_LIMIT"),
                          (str(contract.MAX_INTEGER + 1).encode(), "JSON_INTEGER_LIMIT")):
    rejected("raw-number-" + literal[:12].decode(),
             lambda literal=literal: contract.decode(INIT_RAW.replace(b'"current_epoch":7', b'"current_epoch":' + literal)),
             expected)
rejected("raw-depth-before-parser", lambda: contract.decode(b"[" * 100 + b"0" + b"]" * 100),
         "JSON_DEPTH_LIMIT")
rejected("raw-node-budget", lambda: contract.decode(b'{"a":[' + b",".join([b"0"] * 513) + b"]}"),
         "JSON_NODE_LIMIT")

for field in ("current_epoch",):
    for bad in (True, False, 0, -1, 7.0, "7", None, contract.MAX_INTEGER + 1):
        changed = deepcopy(INIT)
        changed["payload"][field] = bad
        rejected("epoch-type-" + repr(bad), lambda changed=changed: contract.encode(changed))

for change, expected in (
    (lambda item: item.update(format_version=True), "VERSION_INVALID"),
    (lambda item: item.update(format_version=2), "VERSION_INVALID"),
    (lambda item: item.update(kind="unknown"), "KIND_INVALID"),
    (lambda item: item.update(extra="field"), "CLOSED_FIELDS_INVALID"),
    (lambda item: item["payload"].update(active=True), "CLOSED_FIELDS_INVALID"),
    (lambda item: item["issuer"].update(actual_profile=True), "CLOSED_FIELDS_INVALID"),
    (lambda item: item.pop("operation_id"), "CLOSED_FIELDS_INVALID"),
    (lambda item: item.update(operation_id=uid(0xABC).upper()), "UUID_INVALID"),
    (lambda item: item.update(operation_id=uid(1).replace("-", "")), "UUID_INVALID"),
    (lambda item: item.update(operation_id=" " + uid(1)), "UUID_INVALID"),
    (lambda item: item.update(domain_id="00000000-0000-0000-0000-000000000000"), "UUID_INVALID"),
    (lambda item: item["payload"].update(observed_personal_sql_sha256=digest("sql").upper()), "SHA_INVALID"),
):
    changed = deepcopy(INIT)
    change(changed)
    rejected("closed-or-type-" + expected, lambda changed=changed: contract.encode(changed), expected)

for kind, item in BASE.items():
    changed = deepcopy(item)
    changed["payload"]["extra"] = "undeclared"
    rejected(kind + "-closed-payload", lambda changed=changed: contract.encode(changed), "CLOSED_FIELDS_INVALID")

for field, maximum in contract.HARD_LIMITS.items():
    for bad in (True, False, 0, -1, 1.0, maximum + 1):
        rejected("limit-" + field + "-" + repr(bad),
                 lambda field=field, bad=bad: contract.Limits(**{field: bad}), "LIMIT_INVALID")
rejected("foreign-limit-object", lambda: contract.decode(INIT_RAW, limits={}), "LIMIT_INVALID")
tampered_limit = contract.Limits()
object.__setattr__(tampered_limit, "max_records", contract.HARD_LIMITS["max_records"] + 1)
rejected("tampered-frozen-limits", lambda: contract.cross_validate(raws(BASE), limits=tampered_limit), "LIMIT_INVALID")
rejected("record-byte-limit", lambda: contract.decode(INIT_RAW,
         limits=contract.Limits(max_record_bytes=len(INIT_RAW) - 1)), "RECORD_BYTES_LIMIT")
rejected("encode-byte-limit", lambda: contract.encode(INIT,
         limits=contract.Limits(max_record_bytes=len(INIT_RAW) - 1)), "RECORD_BYTES_LIMIT")
rejected("container-depth-limit", lambda: contract.encode(INIT,
         limits=contract.Limits(max_json_depth=1)), "JSON_DEPTH_LIMIT")
rejected("node-limit", lambda: contract.encode(INIT,
         limits=contract.Limits(max_json_nodes=1)), "JSON_NODE_LIMIT")
rejected("count-limit", lambda: contract.cross_validate(raws(BASE),
         limits=contract.Limits(max_records=len(BASE) - 1)), "RECORD_COUNT_LIMIT")
rejected("aggregate-byte-limit", lambda: contract.cross_validate(raws(BASE),
         limits=contract.Limits(max_total_bytes=sum(map(len, raws(BASE))) - 1)), "TOTAL_BYTES_LIMIT")
rejected("empty-set", lambda: contract.cross_validate([]), "RECORD_COUNT_LIMIT")
rejected("generator-set", lambda: contract.cross_validate(iter(raws(BASE))), "RECORD_SET_TYPE_INVALID")
rejected("dto-set", lambda: contract.cross_validate(list(BASE.values())), "RAW_TYPE_INVALID")
cyclic = {}
cyclic["again"] = cyclic
rejected("cyclic-structured-input", lambda: contract.normalize(cyclic), "CYCLIC_INPUT")
rejected("wide-structured-input", lambda: contract.normalize({"a": [0] * 10000}), "JSON_NODE_LIMIT")

for missing in ("origin_contract", "origin_seal", "genesis", "receiver_contract", "receiver_seal",
                "first_intent", "first_fence", "first_stop", "first_reservation", "first_grant"):
    selected = {key: item for key, item in BASE.items() if key != missing}
    rejected("missing-" + missing, lambda selected=selected: contract.cross_validate(raws(selected)),
             "REFERENCE_MISSING")
selected = {key: item for key, item in SECOND.items() if key != "first_receipt"}
rejected("successor-without-target-receipt", lambda: contract.cross_validate(raws(selected)), "REFERENCE_MISSING")
rejected("duplicate-exact-record", lambda: contract.cross_validate(raws(BASE) + [INIT_RAW]), "DUPLICATE_RECORD")

binding_negative("origin-copied-primary", "origin_init",
                 lambda item: item["payload"].update(observed_primary_node_id=TARGET["node_id"]),
                 "ORIGIN_PRIMARY_BINDING_INVALID")
binding_negative("receiver-origin-endpoint-change", "receiver_init",
                 lambda item: item["payload"]["origin"].update(node_id=THIRD["node_id"]), "ORIGIN_BINDING_INVALID")
binding_negative("receiver-contract-relabelled-origin", "receiver_contract",
                 lambda item: item["payload"].update(init_kind="origin"), "REFERENCE_KIND_INVALID")
binding_negative("origin-grant-via-receiver-contract", "genesis",
                 lambda item: item["payload"].update(local_contract_sha256=raw_sha(BASE["receiver_contract"])))
binding_negative("source-contract-as-target-contract", "first_reservation",
                 lambda item: item["payload"].update(target_local_contract_sha256=raw_sha(BASE["origin_contract"])),
                 "ISSUER_BINDING_INVALID")
binding_negative("source-seal-as-target-seal", "first_reservation",
                 lambda item: item["payload"].update(target_init_seal_sha256=raw_sha(BASE["origin_seal"])),
                 "ISSUER_BINDING_INVALID")
binding_negative("handoff-source-issuer-change", "first_intent",
                 lambda item: item["issuer"].update(facility_id=uid(999)), "ISSUER_BINDING_INVALID",
                 items=INTENT_PREFIX)
binding_negative("handoff-stale-source-epoch", "first_intent",
                 lambda item: item["payload"].update(current_epoch=6, next_epoch=7), "EPOCH_BINDING_INVALID")
binding_negative("handoff-to-same-source", "first_intent",
                 lambda item: item["payload"].update(target=deepcopy(SOURCE)), "TARGET_NOT_DISTINCT")
binding_negative("stop-target-change", "first_stop",
                 lambda item: item["payload"].update(target=deepcopy(THIRD)), "TARGET_BINDING_INVALID")
binding_negative("stop-promotion-operation-change", "first_stop",
                 lambda item: item["payload"].update(promotion_operation_id=uid(888)), "TARGET_BINDING_INVALID")
binding_negative("stop-preview-sql-change", "first_stop",
                 lambda item: item["payload"].update(final_personal_sql_sha256=digest("changed-after-preview")),
                 "PREVIEW_SQL_CHANGED")
binding_negative("reservation-snapshot-change", "first_reservation",
                 lambda item: item["payload"].update(installed_snapshot_sha256=digest("wrong-snapshot")),
                 "SNAPSHOT_BINDING_INVALID")
binding_negative("reservation-source-change", "first_reservation",
                 lambda item: item["payload"].update(source=deepcopy(THIRD)), "TARGET_BINDING_INVALID")
binding_negative("reservation-operation-change", "first_reservation",
                 lambda item: item.update(operation_id=uid(888)), "OPERATION_BINDING_INVALID")
binding_negative("target-grant-epoch-change", "first_grant",
                 lambda item: item["payload"].update(epoch=9), "EPOCH_BINDING_INVALID")
binding_negative("receipt-journal-change", "first_receipt",
                 lambda item: item["payload"].update(promotion_journal_sha256=digest("other-journal")),
                 "PROMOTION_RECEIPT_BINDING_INVALID")
binding_negative("receipt-epoch-change", "first_receipt",
                 lambda item: item["payload"].update(epoch=9), "PROMOTION_RECEIPT_BINDING_INVALID")
binding_negative("second-handoff-old-epoch", "second_intent",
                 lambda item: item["payload"].update(current_epoch=7, next_epoch=8),
                 "EPOCH_BINDING_INVALID", items=SECOND)

for change in (
    lambda item: item["payload"].update(next_epoch=9),
    lambda item: item["payload"].update(current_epoch=contract.MAX_INTEGER, next_epoch=contract.MAX_INTEGER),
    lambda item: item["payload"].update(next_epoch=True),
    lambda item: item["payload"].update(expected_personal_revision=-1),
    lambda item: item["payload"]["predecessor"].update(receipt_sha256=digest("not-allowed-for-genesis")),
    lambda item: item["payload"].update(final_snapshot_sha256=digest("premature-final")),
):
    changed = deepcopy(BASE["first_intent"])
    change(changed)
    rejected("intent-timing-type-or-closed", lambda changed=changed: contract.encode(changed))

for name, required in (
    ("first_stop", "epoch_fence_sha256"),
    ("first_grant", "final_stop_receipt_sha256"),
    ("first_grant", "promotion_reservation_sha256"),
    ("first_grant", "promotion_journal_sha256"),
    ("first_grant", "target_local_contract_sha256"),
    ("first_grant", "target_init_seal_sha256"),
):
    changed = deepcopy(BASE[name])
    changed["payload"].pop(required)
    rejected("mandatory-link-" + required, lambda changed=changed: contract.encode(changed), "CLOSED_FIELDS_INVALID")

fork = deepcopy(BASE["first_intent"])
fork["operation_id"] = uid(88)
fork["payload"]["promotion_operation_id"] = uid(89)
rejected("same-grant-handoff-fork", lambda: contract.cross_validate(raws(BASE) + [contract.encode(fork)]),
         "GRANT_HANDOFF_CONFLICT")

second_genesis = deepcopy(BASE["genesis"])
second_genesis["operation_id"] = uid(88)
rejected("second-genesis-for-domain", lambda: contract.cross_validate(raws(BASE) + [contract.encode(second_genesis)]),
         "DOMAIN_GENESIS_CONFLICT")

conflict = deepcopy(BASE["first_stop"])
conflict["payload"]["final_bundle_sha256"] = digest("other-package")
rejected("same-tag-operation-different-body", lambda: contract.cross_validate(raws(BASE) + [contract.encode(conflict)]),
         "RECORD_IDENTITY_CONFLICT")

binding_negative("init-uuid-used-as-handoff", "first_intent",
                 lambda item: item.update(operation_id=uid(1)), "OPERATION_PURPOSE_CONFLICT")
binding_negative("init-uuid-chosen-as-promotion", "first_intent",
                 lambda item: item["payload"].update(promotion_operation_id=uid(2)))

for forbidden in ("epoch", "current_epoch", "observed_primary_node_id"):
    changed = deepcopy(BASE["receiver_init"])
    changed["payload"][forbidden] = 7 if forbidden != "observed_primary_node_id" else SOURCE["node_id"]
    rejected("receiver-no-origin-field-" + forbidden, lambda changed=changed: contract.encode(changed),
             "CLOSED_FIELDS_INVALID")

print(json.dumps({
    "ok": True, "scope": "synthetic_canonical_structure_and_explicit_links_only",
    "checks": CHECKS, "first_chain_records": len(BASE), "second_chain_records": len(SECOND),
    "origin_return_records": len(RETURN), "record_kinds": sorted(contract.KINDS),
    "candidate_sha256": hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest(),
    "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "module_integrated": True, "runtime_proofs_examined": False,
}, sort_keys=True))
