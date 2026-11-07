"""Preserve the single r701 suite attempt without importing product code."""
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
    manifest = ROOT / "data/t16-runtime-origin-graph-r701/r701-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-stage-directory-verification-r701", args.exit_code,
                      args.session, args.chunk, args.seconds)
    if args.exit_code == 0:
        assert len(suite["phases"]) == 69
        assert all(row["outcome"] == "passed" for row in suite["phases"])
    terminal = {
        "state": "stage_directory_entry_suite_passed" if args.exit_code == 0 else "stage_directory_entry_suite_failed",
        "goal_complete": False,
        "candidate_manifest": fact(manifest),
        "candidate_entities_verified": 25,
        "root_source_review": fact(ROOT / "data/h01-r701-root-source-check.json"),
        "independent_source_review": fact(ROOT / "data/h01-r701-stage-source-review.json"),
        "independent_test_review": fact(ROOT / "data/h01-r701-stage-test-review.json"),
        "recorder_dependency": fact(helper),
        "suite": suite,
        "boundaries": {"full_store_consumer_run": False, "profiling": False,
                       "deadline_changed": False, "production_data_service_dist_changed": False,
                       "acl_or_codex_settings_changed": False, "commit_or_push": False,
                       "native_128_level_test": False},
    }
    path = ROOT / "data/h01-stage-directory-suite-terminal-r701.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "exit_code": args.exit_code,
                      "phases": len(suite["phases"])}))


if __name__ == "__main__":
    main()
