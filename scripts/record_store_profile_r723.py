"""Seal the one authorized r723 observation from named files; no product imports."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import re

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "data/h01-store-profile-verification-r723.json"
OUTPUT = ROOT / "data/h01-store-profile-verification-r723"
CASE = "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"
LOGS = ("stdout.log", "stderr.log", "pytest.log", "pytest-result.json",
        "execution-result.json", "child-started.json", "call-started.json", "call-timing.json")


def fact(path):
    raw = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def verify(row):
    path = (ROOT / row["path"]).resolve(strict=True)
    path.relative_to(ROOT)
    assert fact(path) == row, row["path"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preserve", "seal"))
    parser.add_argument("--review")
    args = parser.parse_args()
    plan = json.loads(PLAN.read_bytes())
    for row in plan["source_files"]:
        verify(row)
    assert len(plan["source_files"]) == 201
    assert plan["namespace_deadline_seconds"] == 30.0
    claim_path = PLAN.with_name(PLAN.stem + "-started.json")
    claim = json.loads(claim_path.read_bytes())
    assert claim["state"] == "one_attempt_reserved"
    assert claim["plan_sha256"] == fact(PLAN)["sha256"]
    qa = Path(claim["root"])
    assert qa.name == "reno-h01-store-profile-jnl0tmx3"
    OUTPUT.mkdir(exist_ok=True)
    logs = []
    for name in LOGS:
        original, saved = qa / name, OUTPUT / name
        raw = original.read_bytes()
        if args.mode == "preserve":
            with saved.open("xb") as stream:
                stream.write(raw)
        assert saved.read_bytes() == raw, name
        logs.append(fact(saved))
    execution = json.loads((OUTPUT / "execution-result.json").read_bytes())
    assert execution == {"child_started": True, "child_exit_code": 1,
        "prepared_sources_unchanged": True, "diagnostic_only": True,
        "pytest_result_written": True, "call_timing_written": True}
    result = json.loads((OUTPUT / "pytest-result.json").read_bytes())
    phases = result["phases"]
    assert result["pytest_exit_code"] == 1
    assert [(row["phase"], row["outcome"]) for row in phases] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    assert all(row["nodeid"].rsplit("::", 1)[-1] == CASE for row in phases)
    assert re.search(rb"1 failed in 46\.52s", (OUTPUT / "stdout.log").read_bytes())
    timing = json.loads((OUTPUT / "call-timing.json").read_bytes())
    assert timing["diagnostic_only"] and timing["instrumentation_overhead_included"]
    fields = {"function", "calls", "recursive_calls", "self_seconds", "total_seconds"}
    rows = timing["functions"]
    assert len(rows) == 1098 and all(set(row) == fields for row in rows)
    assert all(math.isfinite(row[key]) and row[key] >= 0
        for row in rows for key in ("self_seconds", "total_seconds"))
    stat = next(row for row in rows if row["function"] == "<built-in method nt.stat>")
    assert stat["calls"] == 283636 and stat["self_seconds"] == 20.1334933
    if args.mode == "preserve":
        print(json.dumps({"preserved_logs": len(logs), "sources_verified": 201,
            "rows": len(rows), "all_original_log_bytes_match": True}))
        return
    assert args.review, "seal requires independent review"
    review_path = (ROOT / args.review).resolve(strict=True)
    review_path.relative_to(ROOT)
    review = json.loads(review_path.read_bytes())
    assert review["actual_exit_code"] == 1 and review["tool_session"] == 79530
    assert review["terminal_chunk"] == "b76119" and review["seconds"] == 46.52
    assert review["source_count"] == 201 and review["phases"] == phases
    assert review["diagnostic_only"] and review["independently_confirmed_boundary"]
    for row in review["source_facts"]:
        verify(row)
    authorization = ROOT / "data/h01-r723-profile-authorization.json"
    assert json.loads(authorization.read_bytes())["state"] == "one_additional_profile_authorized_by_user"
    terminal = {"state": "single_authorized_store_profile_failed_at_original_namespace_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "authorization": fact(authorization), "plan": fact(PLAN), "claim": fact(claim_path),
        "tool_session": 79530, "terminal_chunk": "b76119", "actual_exit_code": 1,
        "seconds": 46.52, "phases": phases, "source_count": 201, "source_files": plan["source_files"],
        "original_qa": str(qa), "logs": logs, "all_original_log_bytes_match": True,
        "diagnostic_only": True, "instrumentation_overhead_included": True,
        "function_rows": len(rows), "largest_self_time": stat,
        "result_review": fact(review_path), "reviewed_boundary": review["independently_confirmed_boundary"],
        "limits": {"normal_deadline_acceptance": False, "caller_graph_recorded": False,
            "cumulative_times_additive": False, "stable_performance_proven": False,
            "all_execution_handles_terminal": True, "attempts": 1, "automatic_retry": False,
            "deadline_changed": False, "complete_fixture_reduced": False,
            "production_database_config_identity_lease_service_dist_changed": False,
            "old_acl_codex_settings_changed": False, "other_paused_diagnostics_resumed": False,
            "commit_or_push": False, "new_approval_rejection": False},
        "recorder": fact(Path(__file__).resolve())}
    path = ROOT / "data/h01-store-profile-terminal-r723.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "preserved_logs": len(logs), "source_count": 201}))


if __name__ == "__main__":
    main()
