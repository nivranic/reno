"""Preserve r697/r699/r700 terminal evidence; no product imports or QA writes."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r697/r697-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    failed = completed("h01-pin-tail-guard-verification-r697", 1, 12589, "9db0f1", 402.19, already_copied=True)
    assert len(failed["phases"]) == 87
    suite = completed("h01-pin-tail-guard-verification-r699", 0, 78049, "170406", 471.99, already_copied=True)
    assert len(suite["phases"]) == 114 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r700", 1, 83272, "544c28", 67.27)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    assert not (ROOT / "data/h01-store-verification-r698.json").exists()
    terminal = {"state": "pin_tail_38_cases_passed_install_resume_exit_complete_shared_operation_node_stage_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_review": fact(ROOT / "data/h01-r697-root-source-check.json"),
        "prior_failed_terminal": fact(ROOT / "data/h01-pin-tail-guard-failed-terminal-r697.json"),
        "test_correction": fact(ROOT / "data/h01-r699-test-correction.json"),
        "suite_terminal": fact(ROOT / "data/h01-pin-tail-guard-suite-terminal-r699.json"),
        "file_only_recorder_dependency": fact(helper),
        "failed_suite": failed, "suite": suite, "consumer": consumer,
        "failure_boundary": {"original_test_line": 131,
            "stage": "shared profile guard with real exclusive runtime Namespace; operations authority walk, fresh node metadata publication before current writer resource construction, Stage scope final clock gate",
            "error": "STAGE_ORIGINAL_DEADLINE", "cleanup_note": "AUTHORITY_STORE_EXIT_VERIFICATION_FAILED",
            "first_install_returned": True, "explicit_repeat_resume_returned": True,
            "repeat_result_equality_and_format_checks_passed": True,
            "install_namespace_and_profile_normal_exit_returned": True,
            "install_phase_database_bytes_preserved": True,
            "shared_profile_guard_admitted": True, "runtime_namespace_exclusive_asserted": True,
            "open_authority_v1_context_entered": True, "closed_store2_state_and_no_raw_stream_fd_assertions_passed": True,
            "io_stack_context_entered": True, "authority_walk_returned": False,
            "current_stage_writer_resource_constructed": False, "current_stage_writer_native_open_reached": False,
            "which_stage_clock_comparison_failed_known": False,
            "prepare_operation_reached": False, "put_operation_reached": False,
            "shared_final_database_bytes_check_reached": False,
            "shared_normal_exit_and_empty_registry_assertions_reached": False,
            "winerror2_is_expected_fresh_branch": True, "stable_performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "independent_static_review": True,
            "new_approval_rejection": False, "whole_append_or_sql_policy_tests_run": False,
            "real_second_guard_switch_test": False, "r698_prepared_or_run": False},
        "next_source_question": "Stage _new_resource has no leading scope_live; do not invent this duplicate. Inspect the real _directory_original chain: its scope_live validates Store stack, then the Store directory lookup may immediately validate that same stack again. Preserve input ordering, actual owner/clock/directory gates and unknown dispatch; no subsequent candidate yet.",
        "remaining": ["complete shared Store walk prepare put and normal exit", "original whole append and non-admitted families caller",
            "H01 continuous owner SQL Store receipt adapter enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r700.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"],
        "unchanged_original_definitions": candidate["evidence"]["unchanged_original_definitions"]}))


if __name__ == "__main__":
    main()
