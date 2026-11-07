"""File-only seal of actual r728 results; never starts products or tests."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/t16-runtime-row-cpu-r728"


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


plan = json.loads((BASE / "r728-verification-store.json").read_bytes())
focused_plan = json.loads((BASE / "r728-verification-focused.json").read_bytes())
assert plan["source_files"] == focused_plan["source_files"]
assert len(plan["source_files"]) == 326
for row in plan["source_files"]:
    assert fact(ROOT / row["path"]) == row, row["path"]
focused = json.loads((BASE / "r728-focused-actual.json").read_bytes())
store = json.loads((BASE / "r728-store-actual.json").read_bytes())
logs = focused["logs"] + store["logs"]
assert len(logs) == 14
for row in logs:
    assert Path(row["original"]["path"]).read_bytes() == Path(row["preserved"]["path"]).read_bytes()
review_path = ROOT / "data/h01-r728-row-cpu-result-independent-review.json"
assert fact(review_path)["sha256"] == "584ec88198616439afcfbb8c03370b2b520a54b77d8d1a3e1c7bc60f87253098"
review = json.loads(review_path.read_bytes())
assert review["unresolved_result_evidence_findings"] == []
manifest = json.loads((BASE / "r728-candidate-manifest.json").read_bytes())
names = [
    "scripts/build_runtime_row_r728.py", "scripts/verify_runtime_row_r728.py",
    "scripts/record_runtime_row_r728_terminal.py",
    "data/t16-runtime-row-cpu-r728/r728-candidate-manifest.json",
    "data/t16-runtime-row-cpu-r728/r728-source-evidence.json",
    "data/t16-runtime-row-cpu-r728/r728-focused-actual.json",
    "data/t16-runtime-row-cpu-r728/r728-store-actual.json",
    "data/h01-r728-row-cpu-source-independent-review.json",
    "data/h01-r728-row-cpu-result-independent-review.json",
    "data/h01-r728-row-cpu-result-independent-review-draft-private-wording.json",
    "data/t16-runtime-row-cpu-r728/r728-verification-focused.json",
    "data/t16-runtime-row-cpu-r728/r728-verification-focused-started.json",
    "data/t16-runtime-row-cpu-r728/r728-verification-store.json",
    "data/t16-runtime-row-cpu-r728/r728-verification-store-started.json",
    "data/t16-runtime-row-cpu-r728/r728-terminal-recording-first-error.json",
    "data/t16-runtime-row-cpu-r728/r728-terminal-recording-second-error.json",
    manifest["design"]["path"],
]
value = {
    "state": "r728_exact_tuple_cpu_candidate_focused_passed_original_complete_store_failed",
    "goal_status": "active", "goal_complete": False, "H01_complete": False,
    "bindings": [fact(ROOT / name) for name in names],
    "source_files_before": plan["source_files"], "source_files_after": plan["source_files"],
    "source_count": 326, "module_count": 22, "original_test_copy_count": 7,
    "focused_tests": {"actual_summary": "55 passed in 1.23s", "actual_exit_code": 0,
        "passed_phases": 165, "prepare_chunk": "67614a", "run_chunk": "41305f",
        "profiling_enabled": False},
    "original_complete_store": {"actual_summary": "1 failed in 62.96s (0:01:02)",
        "actual_exit_code": 1, "prepare_chunk": "3a4cb4", "run_start_chunk": "5974f2",
        "live_handle": 10245, "terminal_chunk": "6013bf", "phase_count": 3,
        "phases": store["phases"], "attempts": 1, "retry_allowed": False,
        "profiling_enabled": False},
    "log_copies": logs, "failure_boundary": review["Store_failure_boundary"],
    "performance_interpretation": review["performance_interpretation"],
    "evidence_limitations": [
        "Focused compatibility tests create no authority/activation/live-writer positive acceptance.",
        "Original complete Store still failed; no downstream whole/enrollment suite prepared or run.",
    ],
    "preserved_boundaries": {
        "namespace_deadline_seconds": 30.0, "original_complete_fixture_and_selector": True,
        "physical_native_cancellation_first_real_read_hash_charging_and_close": True,
        "new_profile_or_paused_diagnostics": False,
        "real_database_or_service_or_dist_operations": False,
        "old_ACL_Codex_setting_or_native_alias_recovery_changes": False,
        "commit_push": False, "new_auto_review_rejection": False,
        "all_original_remaining_and_other_paused_work_preserved": True,
        "all_current_test_tool_handles_terminal": True,
    },
    "two_terminal_recording_file_only_errors_preserved": True,
}
output = BASE / "r728-terminal.json"
with output.open("x", encoding="utf-8", newline="\n") as stream:
    json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
    stream.write("\n")
assert json.loads(output.read_bytes()) == value
print(json.dumps({"terminal": fact(output), "sources_unchanged": 326, "logs_equal": 14}))
