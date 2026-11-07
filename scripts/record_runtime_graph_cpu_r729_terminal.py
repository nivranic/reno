"""File-only preservation of r729's actual reports; no execution or retries."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/t16-runtime-graph-cpu-r729"
QA_NAMES = {
    "focused": "reno-r729-focused-fc6xx5bo",
    "store": "reno-r729-store-lcqj8do6",
}
LOG_NAMES = (
    "stdout.log", "stderr.log", "pytest.log", "pytest-result.json",
    "execution-result.json", "pytest.ini", "child-started.json",
)


def fact(path):
    raw = path.read_bytes()
    return {"path": str(path), "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def write_new(path, document):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    assert json.loads(path.read_bytes()) == document


plans = {suite: json.loads((BASE / f"r729-verification-{suite}.json").read_bytes())
         for suite in QA_NAMES}
sources = plans["focused"]["source_files"]
assert sources == plans["store"]["source_files"] and len(sources) == 365
for row in sources:
    actual = fact(ROOT / row["path"])
    assert actual["byte_size"] == row["byte_size"] and actual["sha256"] == row["sha256"], row["path"]
review_path = ROOT / "data/h01-r729-graph-cpu-result-independent-review.json"
assert fact(review_path)["sha256"] == "19616a2cb9fd341f8a963c04139f060f32e75595956aaf0d1edd5ded1f6aa762"
review = json.loads(review_path.read_bytes())
assert review["unresolved_result_evidence_findings"] == []
audit_path = ROOT / "data/p06-public-source-audit-r729/result.json"
assert fact(audit_path)["sha256"] == "1ffed9700da3a4b6a19bcba5dba059639ac6ae4533f6cb6cc7bc932b7283dcac"
all_logs, results = [], {}
for suite, qa_name in QA_NAMES.items():
    claim_path = BASE / f"r729-verification-{suite}-started.json"
    claim = json.loads(claim_path.read_bytes())
    qa = Path(claim["root"])
    assert qa.name == qa_name and qa.is_dir()
    assert claim["plan_sha256"] == fact(BASE / f"r729-verification-{suite}.json")["sha256"]
    execution = json.loads((qa / "execution-result.json").read_bytes())
    result = json.loads((qa / "pytest-result.json").read_bytes())
    assert execution["prepared_sources_unchanged"] is True
    assert execution["profiling_enabled"] is False
    phases = result["phases"]
    if suite == "focused":
        assert result["pytest_exit_code"] == 0 and len(phases) == 168
        assert all(row["outcome"] == "passed" for row in phases)
    else:
        assert result["pytest_exit_code"] == 1 and len(phases) == 3
        assert [(row["phase"], row["outcome"]) for row in phases] == [
            ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    folder = BASE / f"r729-{suite}-actual"
    folder.mkdir()
    rows = []
    for name in LOG_NAMES:
        raw = (qa / name).read_bytes()
        with (folder / name).open("xb") as stream:
            stream.write(raw)
        assert (folder / name).read_bytes() == raw
        rows.append({"original": fact(qa / name), "preserved": fact(folder / name), "bytes_equal": True})
    all_logs.extend(rows)
    results[suite] = {"report": result, "execution": execution, "logs": rows,
                      "source_count": 365, "attempts": 1, "retry_allowed": False}
    write_new(BASE / f"r729-{suite}-actual.json", results[suite])
manifest_path = BASE / "r729-candidate-manifest.json"
manifest = json.loads(manifest_path.read_bytes())
binding_names = [
    "scripts/build_runtime_graph_cpu_r729.py", "scripts/verify_runtime_graph_cpu_r729.py",
    "scripts/format_runtime_graph_cpu_r729.py", "scripts/record_runtime_graph_cpu_r729_terminal.py",
    "data/h01-r729-graph-cpu-source-independent-review.json",
    "data/h01-r729-graph-cpu-result-independent-review.json",
    "data/t16-runtime-graph-cpu-r729/r729-candidate-manifest.json",
    "data/t16-runtime-graph-cpu-r729/r729-source-evidence.json",
    "data/t16-runtime-graph-cpu-r729/r729-formatting-terminal.json",
    "data/t16-runtime-graph-cpu-r729/r729-verification-focused.json",
    "data/t16-runtime-graph-cpu-r729/r729-verification-focused-started.json",
    "data/t16-runtime-graph-cpu-r729/r729-verification-store.json",
    "data/t16-runtime-graph-cpu-r729/r729-verification-store-started.json",
    "data/t16-runtime-graph-cpu-r729/r729-focused-actual.json",
    "data/t16-runtime-graph-cpu-r729/r729-store-actual.json",
    "data/p06-public-source-audit-r729/result.json", "data/p06-public-source-audit-r729/report.md",
    manifest["design"]["path"], manifest["static_draft_preservation"]["path"],
]
value = {
    "state": "r729_chain_identity_CPU_candidate_focused_passed_original_Store_failed",
    "goal_status": "active", "goal_complete": False, "H01_complete": False,
    "bindings": [fact(ROOT / name) for name in binding_names],
    "source_files_before": sources, "source_files_after": sources,
    "source_count": 365, "log_copies": all_logs,
    "actual_results": results,
    "actual_execution": {
        "focused_prepare_chunk": "3fc53c", "focused_run_chunk": "fd7e78",
        "focused_summary": "56 passed in 1.50s",
        "store_prepare_chunk": "b36196", "store_start_chunk": "63bd13",
        "store_handle": 38615, "store_terminal_chunk": "5b0734",
        "store_summary": "1 failed in 68.82s (0:01:08)",
        "all_current_test_handles_terminal": True,
    },
    "failure_boundary": review["Store_failure_boundary"],
    "performance_limits": review["performance_limits"],
    "P06_public_source_audit": {
        "request_count": 11, "scope": "public default main path history only",
        "state": "no_exact_wanted_source_admitted",
        "original_source_path_question_pending": True,
    },
    "preserved_boundaries": {
        "namespace_deadline_seconds": 30.0, "original_complete_fixture_and_selector": True,
        "physical_native_cancellation_first_real_read_hash_charging_and_close": True,
        "new_profile_or_other_paused_diagnostics": False,
        "real_database_or_service_or_dist_operations": False,
        "old_ACL_Codex_setting_or_native_alias_recovery_changes": False,
        "new_auto_review_rejection": False, "commit_push": False,
        "all_original_remaining_and_other_paused_work_preserved": True,
        "downstream_whole_enrollment_prepared_or_run": False,
    },
}
write_new(BASE / "r729-terminal.json", value)
for row in sources:
    raw = (ROOT / row["path"]).read_bytes()
    assert len(raw) == row["byte_size"] and hashlib.sha256(raw).hexdigest() == row["sha256"]
print(json.dumps({"terminal": fact(BASE / "r729-terminal.json"),
                  "sources_unchanged": 365, "logs_equal": len(all_logs)}))
