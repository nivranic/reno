"""Preserve the completed r711 suite, without importing product modules."""
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
    fact, verify, completed = (functions[key] for key in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r711/r711-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    rows = candidate["source_files"] + candidate["artifacts"]
    assert len(rows) == 25
    for row in rows:
        verify(row)
    suite = completed("h01-pin-pre-profile-verification-r711", args.exit_code,
                      args.session, args.chunk, args.seconds)
    if args.exit_code == 0:
        assert len(suite["phases"]) == 48
        assert all(row["outcome"] == "passed" for row in suite["phases"])
    terminal = {"state": "pin_pre_profile_16_cases_passed" if args.exit_code == 0 else "pin_pre_profile_suite_failed",
        "goal_complete": False, "candidate_manifest": fact(manifest), "candidate_entities_verified": len(rows),
        "root_source_check": fact(ROOT / "data/h01-r711-root-source-check-final.json"),
        "source_review": fact(ROOT / "data/h01-r711-pin-pre-profile-source-review.json"),
        "test_review": fact(ROOT / "data/h01-r711-pin-pre-profile-test-review.json"),
        "review_timing": "Source review preceded freeze; oral final test review preceded execution, with report-file write possibly concurrent with launch. Static source charge count correction is not a runtime failure.",
        "suite": suite, "previous_full_failure": fact(ROOT / "data/h01-store-root-terminal-r710.json"),
        "recorder_dependency": fact(helper),
        "coverage_limits": {"full_store_consumer_run": False, "stable_performance_proved": False,
            "profiling": False, "deadline_changed": False, "production_data_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "commit_or_push": False,
            "r712_prepared_or_run": False}}
    path = ROOT / "data/h01-pin-pre-profile-suite-terminal-r711.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "phases": len(suite["phases"])}))


if __name__ == "__main__":
    main()
