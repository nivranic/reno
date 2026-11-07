"""Preserve terminal r695/r696 evidence without importing any product module."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r695/r695-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-pin-verify-verification-r695", 0, 74824, "78d1db", 479.01, already_copied=True)
    assert len(suite["phases"]) == 81 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r696", 1, 80885, "0223cd", 46.78)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    terminal = {"state": "pin_verify_27_cases_passed_repeat_resume_source_directory_component_budget_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_review": fact(ROOT / "data/h01-r695-root-source-check.json"),
        "test_review": fact(ROOT / "data/h01-r695-test-review.json"),
        "suite_terminal": fact(ROOT / "data/h01-pin-verify-suite-terminal-r695.json"),
        "file_only_recorder_dependency": fact(helper),
        "suite": suite, "consumer": consumer,
        "failure_boundary": {"original_test_line": 101,
            "stage": "explicit repeat resume source batch directory entry; a path component's leading Budget.check enters original Namespace stack identity sweep and fails its held namespace clock",
            "error": "OWNED_IO_NAMESPACE_DEADLINE", "first_install_returned": True,
            "repeat_require_install_profile_returned": True,
            "source_directory_enter_called": True, "source_directory_enter_returned": False,
            "directory_component_index_known": False,
            "failing_component_stat_or_native_open_reached": False,
            "earlier_directory_component_work_known": False,
            "repeat_source_filehold_acquired": False, "repeat_resume_source_pins_returned": False,
            "repeat_resume_acquire_v2_reached": False,
            "explicit_resume_returned": False, "shared_profile_open_prepare_put_reached": False,
            "performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "independent_static_review": True,
            "new_approval_rejection": False, "whole_append_or_sql_policy_tests_run": False},
        "next_source_question": "Inspect whether the added tail state helper calls ns._require_profile and repeats full physical guard verification. Preserve original pre-checks and logical late state, but do not infer a hotspot or stable slowdown from this failed run. No further candidate prepared or run.",
        "next_full_consumers_after_store_pass": [
            "data/t16-runtime-origin-graph-r695/test_whole_append.py::test_authority_whole_append_duplicate_resume_and_real_slot_page_readback",
            "data/t16-runtime-origin-graph-r695/test_whole_append.py::test_authority_conflicting_original_parent_rejected_before_any_new_fact"],
        "remaining": ["original complete Store consumer and whole append", "families caller currently explicitly not admitted",
            "H01 owner SQL Store receipt adapter enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r696.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"],
        "unchanged_original_definitions": candidate["evidence"]["unchanged_original_definitions"]}))


if __name__ == "__main__":
    main()
