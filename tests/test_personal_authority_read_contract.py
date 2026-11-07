"""Synthetic reports only; no Reno imports, filesystem status, DB or native I/O."""
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "reno/personal_authority_read_contract.py"
SPEC = importlib.util.spec_from_file_location("r806_read_contract", MODULE_PATH)
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)

UUID = "11111111-1111-4111-8111-111111111111"
SHA = "a" * 64


def ready():
    return {"state": "ready", "filesystem_only": True, "init_operation_id": UUID,
            "declared_binding_sha256": SHA, "windows_directory_fsync_claimed": False,
            "fact_count": 1, "total_bytes": 64, "head_state": "matches"}


class HookDictionary(dict):
    def __iter__(self):
        raise AssertionError("dict subclass hook dispatched")


class HookText(str):
    def __hash__(self):
        raise AssertionError("str subclass hash dispatched")


class CollisionKey:
    def __init__(self):
        self.calls = 0

    def __hash__(self):
        self.calls += 1
        return hash("state")

    def __eq__(self, other):
        self.calls += 1
        return False


class TestAuthorityReadContract(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(CONTRACT.AuthorityReadContractError) as caught:
            CONTRACT.project_filesystem_status(value)
        self.assertEqual(caught.exception.code, "INVALID_OBSERVATION")
        self.assertEqual(str(caught.exception), "INVALID_OBSERVATION")

    def test_five_states_remain_reports_without_authority(self):
        reports = [{"state": state, "filesystem_only": True} for state in ("absent", "retained_unproven")]
        reports += [{"state": "reserved", "filesystem_only": True, "init_operation_id": UUID},
                    {"state": "unavailable", "filesystem_only": True, "code": "FILES_IO_UNAVAILABLE"}, ready()]
        for report in reports:
            result = CONTRACT.project_filesystem_status(report)
            self.assertEqual(result["reported_filesystem_state"], report["state"])
            self.assertEqual(result["effective_authority_state"], "unknown")
            self.assertEqual(result["write_authority"], "not_established")
            self.assertEqual(result["handoff_authority"], "not_established")
            self.assertNotIn("legacy", str(result))

    def test_ready_heads_never_establish_a_grant(self):
        for head in ("missing", "matches", "stale"):
            report = ready()
            report["head_state"] = head
            result = CONTRACT.project_filesystem_status(report)
            self.assertEqual(result["reported_head_state"], head)
            self.assertEqual(result["write_authority"], "not_established")

    def test_unknown_missing_and_extra_fields_are_rejected(self):
        self.reject({"state": "active", "filesystem_only": True})
        self.reject({"state": "x" * 18, "filesystem_only": True})
        self.reject({"state": "absent"})
        self.reject({"state": "absent", "filesystem_only": True, "grant": True})
        report = ready()
        del report["declared_binding_sha256"]
        self.reject(report)

    def test_summary_only_ready_is_rejected(self):
        report = ready()
        for key in ("fact_count", "total_bytes", "head_state"):
            del report[key]
        self.reject(report)

    def test_nonexact_dict_and_state_do_not_dispatch_hooks(self):
        self.reject(HookDictionary(state="absent", filesystem_only=True))
        self.reject({"state": HookText("absent"), "filesystem_only": True})
        self.reject({"state": "absent", "filesystem_only": 1})

    def test_collision_key_is_rejected_before_keyed_lookup(self):
        key = CollisionKey()
        report = {"state": "absent", "filesystem_only": True, key: "not evidence"}
        key.calls = 0
        self.reject(report)
        self.assertEqual(key.calls, 0)

    def test_string_subclass_key_is_rejected_before_hashing(self):
        class Key(str):
            calls = 0

            def __hash__(self):
                self.calls += 1
                return str.__hash__(self)

        key = Key("state")
        report = {key: "absent", "filesystem_only": True}
        key.calls = 0
        self.reject(report)
        self.assertEqual(key.calls, 0)

    def test_caller_mutation_after_snapshot_cannot_trigger_lookup_hooks(self):
        # Explicit synthetic fault at the snapshot boundary, not a live probe
        # or claim that snapshots observe concurrent changes atomically.
        report = {"state": "absent", "filesystem_only": True}
        key = CollisionKey()
        original = CONTRACT._snapshot

        def capture_then_mutate(value):
            owned = original(value)
            value.clear()
            value[key] = "not evidence"
            key.calls = 0
            return owned

        CONTRACT._snapshot = capture_then_mutate
        try:
            result = CONTRACT.project_filesystem_status(report)
        finally:
            CONTRACT._snapshot = original
        self.assertEqual(result["reported_filesystem_state"], "absent")
        self.assertEqual(key.calls, 0)

    def test_size_change_during_snapshot_is_a_fixed_rejection(self):
        report = {"state": "absent", "filesystem_only": True}
        original_next, calls = next, 0

        def mutate_then_next(iterator):
            nonlocal calls
            calls += 1
            if calls == 2:
                report["extra"] = "not evidence"
            return original_next(iterator)

        CONTRACT.next = mutate_then_next
        try:
            self.reject(report)
        finally:
            del CONTRACT.next

    def test_uuid_requires_canonical_non_nil_exact_text(self):
        for invalid in ("00000000-0000-0000-0000-000000000000", UUID.upper().replace("111", "AAA", 1),
                        UUID.replace("-", ""), HookText(UUID), None):
            self.reject({"state": "reserved", "filesystem_only": True, "init_operation_id": invalid})

    def test_sha_and_windows_claim_are_validated(self):
        for invalid in ("A" * 64, "a" * 63, HookText(SHA), None):
            report = ready()
            report["declared_binding_sha256"] = invalid
            self.reject(report)
        for invalid in (True, 0, None):
            report = ready()
            report["windows_directory_fsync_claimed"] = invalid
            self.reject(report)
        for invalid in ("active", HookText("matches"), None):
            report = ready()
            report["head_state"] = invalid
            self.reject(report)

    def test_counter_edges_and_boolean_integer_rejection(self):
        report = ready()
        report.update(fact_count=0, total_bytes=0)
        CONTRACT.project_filesystem_status(report)
        report.update(fact_count=64, total_bytes=524288)
        CONTRACT.project_filesystem_status(report)
        for field, invalid in (("fact_count", -1), ("fact_count", 65), ("fact_count", True),
                               ("total_bytes", -1), ("total_bytes", 524289), ("total_bytes", False)):
            report = ready()
            report[field] = invalid
            self.reject(report)

    def test_invalid_error_codes_are_not_echoed(self):
        for code in ("private /path", "secret@example", "", "A" * 97, HookText("FILES_IO"), None):
            self.reject({"state": "unavailable", "filesystem_only": True, "code": code})

    def test_input_preserved_output_fresh_and_private_fields_omitted(self):
        report = ready()
        before = dict(report)
        first = CONTRACT.project_filesystem_status(report)
        second = CONTRACT.project_filesystem_status(report)
        self.assertEqual(report, before)
        self.assertIsNot(first, second)
        first["reported_filesystem_state"] = "tampered"
        self.assertEqual(second["reported_filesystem_state"], "ready")
        self.assertEqual(set(second), {"format_version", "kind", "observation_scope", "reported_filesystem_state",
                                      "reported_head_state", "effective_authority_state", "write_authority", "handoff_authority"})
        self.assertNotIn(UUID, str(second))
        self.assertNotIn(SHA, str(second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
