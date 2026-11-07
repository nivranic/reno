"""Synthetic canonical declarations only; no authority runtime or live files.

The loader below is executed only when Root later admits these pure fixtures.
It compiles the same held raw bytes in a dedicated synthetic package.
"""
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest


BATCH = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("r807_pure")
PACKAGE.__path__ = []
sys.modules[PACKAGE.__name__] = PACKAGE


def load_held(name, path, *, expected_size=None, expected_sha=None):
    raw = path.read_bytes()
    if expected_size is not None and (
            len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha):
        raise RuntimeError("Pinned pure fixture source changed")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = PACKAGE.__name__
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


AUTHORITY = load_held(
    "r807_pure.personal_authority_contract",
    BATCH / "reno/personal_authority_contract.py",
    expected_size=30427,
    expected_sha="ccaa261cd2091538358ce25e4da98c70b2f513e055337425dba889bb014d1dc5",
)
PACKAGE.personal_authority_contract = AUTHORITY
CONTRACT = load_held(
    "r807_pure.personal_handoff_transfer_contract",
    BATCH / "reno/personal_handoff_transfer_contract.py",
)


def uid(number):
    return f"{number:08x}-1111-4111-8111-111111111111"


def canonical_unchecked(record):
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Chain:
    """Fixed synthetic H01 record builder; it produces no live proof."""

    def __init__(self, *, offset=0):
        self.offset = offset
        self.domain = uid(1 + offset)
        self.source = {"facility_id": uid(2 + offset), "node_id": uid(3 + offset), "profile_sha256": "a" * 64}
        self.target = {"facility_id": uid(4 + offset), "node_id": uid(5 + offset), "profile_sha256": "b" * 64}
        self.rows, self.raw, self.sha = [], {}, {}
        self.add("origin", "origin_init_intent", self.source, 10, {
            "current_epoch": 1, "observed_primary_node_id": self.source["node_id"],
            "observed_personal_meta_sha256": "a" * 64, "observed_personal_sql_sha256": "a" * 64})
        self.add("source_contract", "local_contract", self.source, 10,
                 {"init_kind": "origin", "init_intent_sha256": self.sha["origin"]})
        self.add("source_seal", "init_seal", self.source, 10, {
            "init_intent_sha256": self.sha["origin"], "local_contract_sha256": self.sha["source_contract"]})
        self.add("genesis", "genesis_grant", self.source, 10, {
            "origin_init_intent_sha256": self.sha["origin"], "local_contract_sha256": self.sha["source_contract"],
            "init_seal_sha256": self.sha["source_seal"], "epoch": 1})
        self.add("receiver", "receiver_init_intent", self.target, 20,
                 {"origin": self.source, "domain_genesis_grant_sha256": self.sha["genesis"]})
        self.add("target_contract", "local_contract", self.target, 20,
                 {"init_kind": "receiver", "init_intent_sha256": self.sha["receiver"]})
        self.add("target_seal", "init_seal", self.target, 20, {
            "init_intent_sha256": self.sha["receiver"], "local_contract_sha256": self.sha["target_contract"]})
        self.add("handoff", "handoff_intent", self.source, 30, {
            "predecessor": {"kind": "genesis_grant", "grant_sha256": self.sha["genesis"]},
            "current_epoch": 1, "next_epoch": 2, "target": self.target,
            "promotion_operation_id": uid(40 + offset), "expected_personal_revision": 3,
            "expected_personal_sql_sha256": "a" * 64})
        self.add("fence", "epoch_fence", self.source, 30,
                 {"handoff_intent_sha256": self.sha["handoff"], "current_epoch": 1, "next_epoch": 2})
        self.add("stop", "final_stop_receipt", self.source, 30, {
            "handoff_intent_sha256": self.sha["handoff"], "epoch_fence_sha256": self.sha["fence"],
            "target": self.target, "promotion_operation_id": uid(40 + offset), "current_epoch": 1, "next_epoch": 2,
            "final_snapshot_sha256": "b" * 64, "final_bundle_sha256": "c" * 64,
            "final_personal_sql_sha256": "a" * 64, "final_attachment_manifest_sha256": "d" * 64})
        self.add("reservation", "promotion_reservation", self.target, 40, {
            "handoff_intent_sha256": self.sha["handoff"], "final_stop_receipt_sha256": self.sha["stop"],
            "target_local_contract_sha256": self.sha["target_contract"], "target_init_seal_sha256": self.sha["target_seal"],
            "source": self.source, "current_epoch": 1, "next_epoch": 2, "installed_snapshot_sha256": "b" * 64,
            "target_installation_sha256": "e" * 64, "expected_target_sql_sha256": "a" * 64})

    def add(self, label, kind, issuer, operation, payload):
        raw = AUTHORITY.encode({"format_version": 1, "kind": kind, "domain_id": self.domain,
                                "operation_id": uid(operation + self.offset), "issuer": issuer, "payload": payload})
        self.rows.append(raw)
        self.raw[label] = raw
        self.sha[label] = hashlib.sha256(raw).hexdigest()

    def second_handoff(self):
        self.add("grant", "promotion_grant", self.target, 40, {
            "final_stop_receipt_sha256": self.sha["stop"], "promotion_reservation_sha256": self.sha["reservation"],
            "target_local_contract_sha256": self.sha["target_contract"], "target_init_seal_sha256": self.sha["target_seal"],
            "promotion_journal_sha256": "f" * 64, "epoch": 2})
        self.add("receipt", "promotion_receipt", self.target, 40, {
            "final_stop_receipt_sha256": self.sha["stop"], "promotion_reservation_sha256": self.sha["reservation"],
            "promotion_grant_sha256": self.sha["grant"], "promotion_journal_sha256": "f" * 64, "epoch": 2})
        self.add("handoff2", "handoff_intent", self.target, 50, {
            "predecessor": {"kind": "promotion_grant", "grant_sha256": self.sha["grant"], "receipt_sha256": self.sha["receipt"]},
            "current_epoch": 2, "next_epoch": 3, "target": self.source,
            "promotion_operation_id": uid(60 + self.offset), "expected_personal_revision": 4,
            "expected_personal_sql_sha256": "a" * 64})
        self.add("fence2", "epoch_fence", self.target, 50,
                 {"handoff_intent_sha256": self.sha["handoff2"], "current_epoch": 2, "next_epoch": 3})
        self.add("stop2", "final_stop_receipt", self.target, 50, {
            "handoff_intent_sha256": self.sha["handoff2"], "epoch_fence_sha256": self.sha["fence2"],
            "target": self.source, "promotion_operation_id": uid(60 + self.offset), "current_epoch": 2, "next_epoch": 3,
            "final_snapshot_sha256": "1" * 64, "final_bundle_sha256": "2" * 64,
            "final_personal_sql_sha256": "a" * 64, "final_attachment_manifest_sha256": "3" * 64})
        self.add("reservation2", "promotion_reservation", self.source, 60, {
            "handoff_intent_sha256": self.sha["handoff2"], "final_stop_receipt_sha256": self.sha["stop2"],
            "target_local_contract_sha256": self.sha["source_contract"], "target_init_seal_sha256": self.sha["source_seal"],
            "source": self.target, "current_epoch": 2, "next_epoch": 3, "installed_snapshot_sha256": "1" * 64,
            "target_installation_sha256": "4" * 64, "expected_target_sql_sha256": "a" * 64})

    def extra_receiver(self, number):
        endpoint = {"facility_id": uid(100 + number + self.offset), "node_id": uid(200 + number + self.offset),
                    "profile_sha256": "5" * 64}
        self.add(f"extra{number}", "receiver_init_intent", endpoint, 300 + number,
                 {"origin": self.source, "domain_genesis_grant_sha256": self.sha["genesis"]})


class TestHandoffTransferContract(unittest.TestCase):
    def reject(self, rows, selector):
        with self.assertRaises(CONTRACT.HandoffTransferContractError) as caught:
            CONTRACT.diagnose_handoff_transfer(rows, promotion_reservation_sha256=selector)
        self.assertEqual(caught.exception.code, "INVALID_DECLARATION")
        self.assertEqual(str(caught.exception), "INVALID_DECLARATION")

    def replace_reservation(self, chain, edit):
        record = AUTHORITY.decode(chain.raw["reservation"])
        edit(record)
        replacement = canonical_unchecked(record)
        rows = [replacement if raw == chain.raw["reservation"] else raw for raw in chain.rows]
        return rows, hashlib.sha256(replacement).hexdigest()

    def test_complete_list_and_tuple_only_return_declaration_facts(self):
        chain = Chain()
        before = tuple(chain.rows)
        results = [CONTRACT.diagnose_handoff_transfer(rows, promotion_reservation_sha256=chain.sha["reservation"])
                   for rows in (chain.rows, tuple(chain.rows))]
        self.assertEqual(results[0], results[1])
        self.assertIsNot(results[0], results[1])
        self.assertEqual(results[0]["declaration_bindings"], "consistent")
        self.assertEqual(tuple(chain.rows), before)

    def test_historical_selector_and_promotion_predecessor_are_not_current_authority(self):
        chain = Chain()
        chain.second_handoff()
        self.assertEqual(len(chain.rows), 17)
        for name in ("reservation", "reservation2"):
            result = CONTRACT.diagnose_handoff_transfer(chain.rows, promotion_reservation_sha256=chain.sha[name])
            self.assertEqual(result["selector_scope"], "historical_or_current_declaration")
            self.assertEqual(result["effective_authority_state"], "unknown")
            self.assertEqual(result["promotion_authority"], "not_established")

    def test_missing_references_and_predecessor_receipt_are_rejected(self):
        chain = Chain()
        for name in ("genesis", "handoff", "fence", "stop", "target_contract", "target_seal"):
            self.reject([raw for raw in chain.rows if raw != chain.raw[name]], chain.sha["reservation"])
        chain.second_handoff()
        self.reject([raw for raw in chain.rows if raw != chain.raw["receipt"]], chain.sha["reservation2"])

    def test_changed_original_stop_bytes_do_not_satisfy_old_hash(self):
        chain = Chain()
        stop = AUTHORITY.decode(chain.raw["stop"])
        stop["payload"]["final_bundle_sha256"] = "9" * 64
        changed = AUTHORITY.encode(stop)
        self.reject([changed if raw == chain.raw["stop"] else raw for raw in chain.rows], chain.sha["reservation"])

    def test_source_target_and_target_contract_bindings_are_checked(self):
        chain = Chain()
        edits = (
            lambda record: record["payload"].update(source=chain.target),
            lambda record: record.update(issuer=chain.source),
            lambda record: record["payload"].update(target_local_contract_sha256=chain.sha["source_contract"]),
            lambda record: record["payload"].update(target_init_seal_sha256=chain.sha["source_seal"]),
        )
        for edit in edits:
            self.reject(*self.replace_reservation(chain, edit))

    def test_epoch_and_promotion_uuid_bindings_are_checked(self):
        chain = Chain()
        edits = (
            lambda record: record["payload"].update(current_epoch=7, next_epoch=8),
            lambda record: record.update(operation_id=uid(90)),
            lambda record: record["payload"].update(installed_snapshot_sha256="9" * 64),
        )
        for edit in edits:
            self.reject(*self.replace_reservation(chain, edit))

    def test_selector_requires_exact_canonical_sha_and_reservation_kind(self):
        chain = Chain()

        class Text(str):
            def __hash__(self):
                raise AssertionError("selector subclass hashing dispatched")

        for selector in (None, b"a" * 64, "A" * 64, "a" * 63, "f" * 64,
                         Text(chain.sha["reservation"]), chain.sha["genesis"]):
            self.reject(chain.rows, selector)

    def test_duplicate_records_and_mixed_domains_are_rejected(self):
        chain = Chain()
        self.reject(chain.rows + [chain.raw["origin"]], chain.sha["reservation"])
        other = Chain(offset=1000)
        self.reject(chain.rows + other.rows, chain.sha["reservation"])

    def test_nil_uuid_boolean_epoch_and_noncanonical_bytes_are_rejected(self):
        chain = Chain()
        for edit in (lambda record: record.update(domain_id="00000000-0000-0000-0000-000000000000"),
                     lambda record: record["payload"].update(current_epoch=True)):
            self.reject(*self.replace_reservation(chain, edit))
        padded = chain.raw["origin"] + b"\n"
        self.reject([padded] + chain.rows[1:], chain.sha["reservation"])

    def test_exact_container_and_bytes_domain_precede_user_hooks(self):
        chain = Chain()

        class Meta(type):
            def __eq__(self, other):
                raise AssertionError("container metaclass equality dispatched")

        class List(list, metaclass=Meta):
            def __getitem__(self, item):
                raise AssertionError("list subclass slicing dispatched")

        class Bytes(bytes):
            def __hash__(self):
                raise AssertionError("bytes subclass hashing dispatched")

        self.reject(List(chain.rows), chain.sha["reservation"])
        self.reject([Bytes(chain.raw["origin"])] + chain.rows[1:], chain.sha["reservation"])
        self.reject([bytearray(chain.raw["origin"])] + chain.rows[1:], chain.sha["reservation"])
        self.reject([AUTHORITY.decode(chain.raw["origin"])] + chain.rows[1:], chain.sha["reservation"])

    def test_caller_change_after_owned_snapshot_does_not_change_checked_dag(self):
        chain = Chain()
        original = CONTRACT._snapshot

        def capture_then_change(rows):
            owned = original(rows)
            rows.clear()
            rows.append({"not": "original bytes"})
            return owned

        CONTRACT._snapshot = capture_then_change
        try:
            result = CONTRACT.diagnose_handoff_transfer(chain.rows, promotion_reservation_sha256=chain.sha["reservation"])
        finally:
            CONTRACT._snapshot = original
        self.assertEqual(result["declaration_bindings"], "consistent")

    def test_exact_64_record_prefix_and_65_record_rejection(self):
        chain = Chain()
        for number in range(53):
            chain.extra_receiver(number)
        self.assertEqual(len(chain.rows), 64)
        CONTRACT.diagnose_handoff_transfer(chain.rows, promotion_reservation_sha256=chain.sha["reservation"])
        chain.extra_receiver(53)
        self.reject(chain.rows, chain.sha["reservation"])

    def test_byte_limits_reject_before_codec_or_hashing_dispatch(self):
        chain = Chain()
        original = AUTHORITY.cross_validate
        original_sha = CONTRACT.hashlib.sha256

        def unexpected(*args, **kwargs):
            raise AssertionError("codec dispatched before bounded bytes gate")

        AUTHORITY.cross_validate = unexpected
        CONTRACT.hashlib.sha256 = unexpected
        try:
            self.reject([], chain.sha["reservation"])
            self.reject([b""], chain.sha["reservation"])
            self.reject([b"x" * 32769], chain.sha["reservation"])
            self.reject([b"x" * 32768] * 17, chain.sha["reservation"])
        finally:
            AUTHORITY.cross_validate = original
            CONTRACT.hashlib.sha256 = original_sha

    def test_output_is_fresh_private_and_never_grants_install_or_write(self):
        chain = Chain()
        first = CONTRACT.diagnose_handoff_transfer(chain.rows, promotion_reservation_sha256=chain.sha["reservation"])
        second = CONTRACT.diagnose_handoff_transfer(chain.rows, promotion_reservation_sha256=chain.sha["reservation"])
        self.assertEqual(set(first), {"diagnostic_kind", "observation_scope", "declared_stage", "selector_scope",
                                      "declaration_bindings", "effective_authority_state", "install_authority",
                                      "promotion_authority", "write_authority"})
        for name in ("install_authority", "promotion_authority", "write_authority"):
            self.assertEqual(first[name], "not_established")
        self.assertEqual(first["effective_authority_state"], "unknown")
        self.assertNotIn(chain.domain, str(first))
        self.assertNotIn(chain.sha["reservation"], str(first))
        self.assertNotIn(chain.source["node_id"], str(first))
        first["declaration_bindings"] = "changed"
        self.assertEqual(second["declaration_bindings"], "consistent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
