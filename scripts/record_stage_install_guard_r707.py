"""Preserve r707 results after the confirmed missing r705 execution handle."""
from pathlib import Path
import argparse
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exit_code", type=int)
    parser.add_argument("session", type=int)
    parser.add_argument("chunk")
    parser.add_argument("seconds", type=float)
    args = parser.parse_args()
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r705/r705-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-stage-install-guard-verification-r707", args.exit_code,
                      args.session, args.chunk, args.seconds)
    if args.exit_code == 0:
        assert len(suite["phases"]) == 72
        assert all(row["outcome"] == "passed" for row in suite["phases"])
    terminal = {
        "state": "stage_install_guard_24_cases_passed" if args.exit_code == 0 else "stage_install_guard_suite_failed",
        "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 26,
        "root_source_review": fact(ROOT / "data/h01-r705-root-source-check.json"),
        "independent_source_review": fact(ROOT / "data/h01-r705-install-guard-source-review-final.json"),
        "independent_test_review": fact(ROOT / "data/h01-r705-install-guard-test-review.json"),
        "interrupted_prior_attempt": fact(ROOT / "data/h01-stage-install-guard-interrupted-terminal-r705.json"),
        "prior_attempt_actual_exit_known": False,
        "same_frozen_product_and_tests_as_prior_attempt": True,
        "runner_only_plan_name_changed": True,
        "original_qa_unchanged": True,
        "recorder_dependency": fact(helper), "suite": suite,
        "coverage_limits": {"full_store_consumer_run": False, "stable_performance_proved": False,
            "profiling": False, "deadline_changed": False, "production_data_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "commit_or_push": False,
            "r706_prepared_or_run": False},
    }
    path = ROOT / "data/h01-stage-install-guard-suite-terminal-r707.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "exit_code": args.exit_code,
                      "phases": len(suite["phases"])}))


if __name__ == "__main__":
    main()
