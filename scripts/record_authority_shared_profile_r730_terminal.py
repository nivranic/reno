"""Preserve/seal the completed single r730 attempt by named file reads only.

No product/runner imports, execution, database access, traversal, cleanup or
retries. Preserve copies the exact nine logs; seal requires the real independent
result review. A failed or unreached sampling window still consumes this permit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "data/h01-store-shared-profile-verification-r730.json"
CLAIM = ROOT / "data/h01-store-shared-profile-verification-r730-started.json"
PROPOSAL = ROOT / "data/h01-r730-shared-profile-proposal.json"
AUTH = ROOT / "data/h01-r730-shared-profile-authorization.json"
OUTPUT = ROOT / "data/h01-r730-record-actual"
PRESERVATION = ROOT / "data/h01-r730-record-preservation.json"
REVIEW = ROOT / "data/h01-r730-shared-profile-result-independent-review.json"
TERMINAL = ROOT / "data/h01-r730-record-terminal.json"
QA_NAME = "reno-h01-store-shared-profile-r730-bdsrmrhk"
CASE = "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"
NODEID = "::" + CASE
LOGS = ("stdout.log", "stderr.log", "pytest.log", "pytest-result.json",
        "execution-result.json", "child-started.json", "call-started.json",
        "call-timing.json", "pytest.ini")
PINS = {
    "scripts/verify_authority_store_shared_profile_r730.py": "594b7e2a78e63102fdbd766eef6eeaaed9112f6674b136bb9ac282c1775012fc",
    "data/h01-r730-shared-profile-proposal.json": "3dbd84b60a7b79128e15e035a04ae87f3e9658a0265ba3e2927e02524286ca7d",
    "data/h01-r730-shared-profile-authorization.json": "3c5e04fe9e8cae83a86db522e3451adef786ce15537052adad36f7719373c307",
    "data/h01-store-shared-profile-verification-r730.json": "af3f6df3ae5bcebc799a566ff5e658465323a78ccb6a0556a67fa1c08ca0e3db",
    "data/h01-store-shared-profile-verification-r730-started.json": "434734bb7daf64a00111ebfe96611351bc9ea9afca84a2f2478416f6b16c7eb0",
    "data/h01-r730-proposal-ready.json": "85ccbab7aec5ab18ca40fd44241f442548a86d02054738cd4b1a516ac88a2b6c",
    "data/h01-r730-shared-profile-independent-review.json": "86357ea1be91196c043cbc172d54fbdb752b37378c002c7ac051b87f09d662be",
}
WINDOW = {"callback_restored_after_preexisting_callback_refusal": False,
          "install_return_boundary_reached": False, "profile_disabled": True,
          "profile_enabled_once": False, "slot_released": True}
TOOL_RESULT = {
    "evidence_origin": "Root actual tool completion supplied to recorder author; recorder does not poll or restart tools",
    "authorization_chunk": "34be99", "prepare_chunk": "c9b9c9",
    "start_chunk": "86a57e", "handle": 64551, "intermediate_chunk": "a96b5e",
    "terminal_chunk": "a49854", "actual_exit_code": 1,
    "actual_summary": "1 failed in 43.81s", "all_current_test_tool_handles_terminal": True,
}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fact(path, *, absolute=False):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    label = str(path) if absolute else path.relative_to(ROOT).as_posix()
    return {"path": label, "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify(row):
    path = (ROOT / row["path"]).resolve(strict=True)
    check(path.is_relative_to(ROOT), "Workspace binding escaped ROOT")
    actual = fact(path)
    check(actual["byte_size"] == row["byte_size"] and actual["sha256"] == row["sha256"],
          "Named workspace binding changed: " + row["path"])


def write_new(path, value):
    path = Path(path)
    check(path.parent == ROOT / "data", "Recorder JSON writes must remain in data")
    check(path.name.startswith("h01-r730-record-"), "Unexpected recorder output name")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        stream.write("\n")
    check(read(path) == value, "JSON write/readback differs")


def controls():
    bindings = []
    for name, expected in PINS.items():
        row = fact(ROOT / name)
        check(row["sha256"] == expected, "Original r730 control changed: " + name)
        bindings.append(row)
    proposal, auth, plan, claim = map(read, (PROPOSAL, AUTH, PLAN, CLAIM))
    check(auth["state"] == "one_r730_shared_profile_authorized_by_user"
          and auth["explicit_new_user_approval"] is True and auth["user_reply"] == "允许"
          and auth["proposal_sha256"] == PINS[PROPOSAL.relative_to(ROOT).as_posix()]
          and auth["scope"] == proposal["authorized_scope"] == plan["authorized_scope"],
          "New exact r730 authorization differs")
    check(plan["authorization"] == {"path": AUTH.relative_to(ROOT).as_posix(),
          "sha256": fact(AUTH)["sha256"], "proposal_sha256": fact(PROPOSAL)["sha256"]},
          "Prepared authorization binding differs")
    check(claim["state"] == "one_attempt_reserved" and claim["plan_sha256"] == fact(PLAN)["sha256"],
          "Single-use claim differs")
    qa = Path(claim["root"])
    check(qa.is_absolute() and qa.name == QA_NAME and qa.parent.name == "Temp"
          and not qa.is_relative_to(ROOT), "Claim does not name the exact reviewed fresh TEMP")
    check(plan["target"] == "data/t16-runtime-graph-cpu-r729/test_store_stage_wiring.py::" + CASE
          and plan["namespace_deadline_seconds"] == 30.0
          and plan["expected_pytest_nodeid"] == NODEID
          and plan["sampling_boundary"] == proposal["sampling_boundary"],
          "Original selector/boundary/deadline changed")
    source_rows = plan["source_files"]
    check(len(source_rows) == 368 and source_rows == proposal["source_files_before"]
          == proposal["source_files_current"], "Complete prepared source list changed")
    check(len({row["path"] for row in source_rows}) == 368, "Duplicate source inputs")
    for row in source_rows:
        name = PurePosixPath(row["path"])
        check(not name.is_absolute() and row["path"] == name.as_posix()
              and not any(part in (".", "..") or ":" in part or "\\" in part for part in name.parts),
              "Source path left the fixed workspace")
        verify(row)
    return bindings, plan, qa


def preserve_logs(qa, *, create):
    if create:
        OUTPUT.mkdir(exist_ok=True)
    check(OUTPUT.resolve(strict=True).is_relative_to(ROOT / "data"), "Archive directory escaped data")
    copies = []
    for name in LOGS:
        original, saved = qa / name, OUTPUT / name
        check(original.resolve(strict=True).parent == qa.resolve(strict=True), "Named original log escaped QA")
        raw = original.read_bytes()
        before = fact(original, absolute=True)
        check(before["byte_size"] == len(raw) and before["sha256"] == hashlib.sha256(raw).hexdigest(),
              "Original log changed during read")
        if create and not saved.exists():
            with saved.open("xb") as stream:
                stream.write(raw)
        check(saved.read_bytes() == raw, "Preserved log bytes differ: " + name)
        check(fact(original, absolute=True) == before, "Original log changed during preservation")
        copies.append({"original": before, "preserved": fact(saved, absolute=True), "bytes_equal": True})
    return copies


def actual_result(plan):
    execution = read(OUTPUT / "execution-result.json")
    check(execution == {"child_started": True, "child_exit_code": 1,
        "prepared_sources_unchanged": True, "diagnostic_only": True,
        "pytest_result_written": True, "call_timing_written": True}, "Actual completed execution differs")
    result, timing = read(OUTPUT / "pytest-result.json"), read(OUTPUT / "call-timing.json")
    check(result["diagnostic_only"] is True and result["pytest_exit_code"] == 1
          and result["monitoring_cleanup_errors"] == [] and result["window_state"] == WINDOW,
          "Actual pytest/instrumentation result differs")
    phases = result["phases"]
    check([(row["phase"], row["outcome"]) for row in phases]
          == [("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
          and all(row["nodeid"] == NODEID for row in phases), "Actual original test phases differ")
    check(read(OUTPUT / "child-started.json") == {"state": "single_child_entered",
          "plan_sha256": fact(PLAN)["sha256"], "diagnostic_only": True}, "Child entered a different plan")
    check(read(OUTPUT / "call-started.json") == {"nodeid": NODEID, "diagnostic_only": True}, "Call selector differs")
    check(timing["nodeid"] == NODEID and timing["phase"] == plan["authorized_scope"]["phase"]
          and timing["sampling_boundary"] == plan["sampling_boundary"] and timing["window_state"] == WINDOW
          and timing["functions"] == [] and timing["direct_callee_edges"] == []
          and timing["monitoring_cleanup_errors"] == [], "Actual unentered profile window differs")
    for key in ("diagnostic_only", "instrumentation_overhead_included", "edge_times_overlap_not_additive",
                "mid_enable_test_root_and_outer_caller_edges_may_be_missing", "missing_roots_or_edges_are_not_synthesized",
                "window_not_reached_consumes_attempt_no_retry"):
        check(timing[key] is True, "Actual measurement limit differs: " + key)
    stdout = (OUTPUT / "stdout.log").read_bytes()
    check(b"1 failed in 43.81s" in stdout and b"OWNED_IO_NAMESPACE_DEADLINE" in stdout,
          "Actual test summary/original error differs")
    check((OUTPUT / "stderr.log").read_bytes() == b"" and (OUTPUT / "pytest.log").read_bytes() == b"",
          "Previously empty actual logs differ")
    check((OUTPUT / "pytest.ini").read_bytes() == b"[pytest]\r\n", "Original pytest ini bytes differ")
    return {"execution": execution, "pytest_result": result, "timing": timing,
        "test_run": {**TOOL_RESULT, "phases": phases}, "function_rows": 0, "direct_edge_rows": 0,
        "profile_window_reached": False, "profile_enabled": False,
        "single_authorization_consumed_despite_unreached_window": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preserve", "seal"))
    args = parser.parse_args()
    bindings, plan, qa = controls()
    copies = preserve_logs(qa, create=args.mode == "preserve")
    actual = actual_result(plan)
    _, after_plan, _ = controls()
    check(plan == after_plan, "Prepared plan changed during file-only recording")
    if args.mode == "preserve":
        preservation = {"kind": "r730_single_attempt_file_only_preservation",
            "created_at": datetime.now(timezone.utc).isoformat(), "recorder": fact(__file__),
            "bindings": bindings, "original_qa": str(qa), "log_copies": copies,
            "all_original_nine_log_bytes_match": True, "source_count": 368,
            "source_files_before": plan["source_files"], "source_files_after": after_plan["source_files"],
            "actual_result": actual, "independent_result_review_complete": False,
            "product_import_SQL_test_profile_restart_or_QA_mutation_executed": False,
            "H01_complete": False, "goal_complete": False, "goal_status": "active"}
        write_new(PRESERVATION, preservation)
        print(json.dumps({"preservation": fact(PRESERVATION), "sources": 368, "logs_equal": len(copies),
            "profile_window_reached": False, "functions": 0, "direct_edges": 0, "terminal_written": False}))
        return
    preserved = read(PRESERVATION)
    check(preserved["recorder"] == fact(__file__) and preserved["bindings"] == bindings
          and preserved["log_copies"] == copies and preserved["actual_result"] == actual
          and preserved["source_files_before"] == preserved["source_files_after"] == plan["source_files"],
          "Preservation changed or no longer describes actual named files")
    review = read(REVIEW)
    check(review["state"] == "review_complete_no_unresolved_result_evidence_blockers"
          and review["unresolved_findings"] == [], "Independent result review not complete")
    review_bindings = {}
    for row in review["bindings"]:
        verify(row)
        review_bindings[(ROOT / row["path"]).resolve(strict=True)] = row
    for path in (Path(__file__).resolve(), PRESERVATION, PROPOSAL, AUTH, PLAN, CLAIM):
        check(path.resolve() in review_bindings, "Result review omits a required actual binding")
    integrity = review["source_integrity"]
    check(integrity["prepared_source_count"] == 368 and integrity["changed_sources"] == []
          and integrity["independent_current_facts_match_all_prepared_sources"] is True,
          "Independent full 368-source check incomplete")
    check(review["qa_exact_nine_file_facts"] == [row["original"] for row in copies],
          "Independent review does not bind the nine original logs")
    observed = review["actual_result"]
    check(observed["child_exit_code"] == 1 and observed["phases"] == actual["pytest_result"]["phases"]
          and observed["pytest_summary"] == TOOL_RESULT["actual_summary"], "Independent review actual outcome differs")
    check(review["measurement"]["function_rows"] == 0 and review["measurement"]["direct_edge_rows"] == 0,
          "Independent review does not preserve the empty unentered window")
    check(type(review["failure_boundary"]) is dict, "Independent failure boundary is missing")
    terminal = {"state": "one_authorized_r730_attempt_failed_before_profile_window_preserved_and_independently_reviewed",
        "created_at": datetime.now(timezone.utc).isoformat(), "bindings": bindings + [fact(PRESERVATION), fact(REVIEW), fact(__file__)],
        "attempts": 1, "authorization_consumed": True, "retry_allowed": False,
        "source_count": 368, "source_files_before": plan["source_files"], "source_files_after": after_plan["source_files"],
        "log_copies": copies, "test_run": actual["test_run"], "actual_result": actual,
        "failure_boundary": review["failure_boundary"], "measurement": review["measurement"],
        "limitations": ["The original _install helper did not return; no profile window was enabled and no function/edge sample exists.",
            "Passing pytest teardown and monitoring slot cleanup do not independently prove every product resource closed normally.",
            "Instrumentation and this failed attempt do not prove normal deadline performance, H01 acceptance, or the complete goal."],
        "preserved_boundaries": {"complete_original_fixture_selector": True, "namespace_deadline_seconds": 30.0,
            "product_import_SQL_test_profile_restart_or_QA_mutation_executed_by_recorder": False,
            "real_database_service_dist_operations": False, "other_paused_diagnostics_resumed": False,
            "old_acl_codex_settings_changed": False, "commit_push": False},
        "H01_complete": False, "goal_complete": False, "goal_status": "active"}
    # Recheck all named facts immediately before the exclusive terminal write.
    controls()
    check(preserve_logs(qa, create=False) == copies, "Original logs changed before terminal")
    write_new(TERMINAL, terminal)
    print(json.dumps({"terminal": fact(TERMINAL), "sources": 368, "logs_equal": len(copies),
        "independent_result_review": fact(REVIEW), "authorization_consumed": True}))


if __name__ == "__main__":
    main()
