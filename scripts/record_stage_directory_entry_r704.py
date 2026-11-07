"""Record r701/r703/r704 outcomes; file-only, no product imports or QA writes."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r701/r701-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-stage-directory-verification-r701", 1, 2312, "8fbab5", 352.12, already_copied=True)
    calls = [row for row in suite["phases"] if row["phase"] == "call"]
    assert len(calls) == 23 and sum(row["outcome"] == "passed" for row in calls) == 22
    assert calls[-1]["outcome"] == "failed"
    factory = completed("h01-stage-namespace-factory-verification-r703", 0, 37808, "ae1572", 13.51, already_copied=True)
    assert len(factory["phases"]) == 3 and all(row["outcome"] == "passed" for row in factory["phases"])
    consumer = completed("h01-store-verification-r704", 1, 62550, "1d3a6e", 43.74)
    assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    assert not (ROOT / "data/h01-store-verification-r702.json").exists()
    assert not (ROOT / "data/h01-store-verification-r702-started.json").exists()
    terminal = {
        "state": "stage_entry_22_cases_passed_unreachable_new_test_failed_factory_component_passed_complete_consumer_repeat_normal_exit_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "changed_definitions": ["Stage._directory_original"],
        "new_helpers": 2, "unchanged_stage_definitions": 57, "unchanged_support_modules": 15,
        "root_source_check": fact(ROOT / "data/h01-r701-root-source-check.json"),
        "independent_source_review": fact(ROOT / "data/h01-r701-stage-source-review.json"),
        "original_test_review": fact(ROOT / "data/h01-r701-stage-test-review.json"),
        "failed_suite_terminal": fact(ROOT / "data/h01-stage-directory-suite-terminal-r701.json"),
        "unreachable_test_analysis": fact(ROOT / "data/h01-r701-namespace-test-analysis.json"),
        "factory_terminal": fact(ROOT / "data/h01-stage-namespace-factory-terminal-r703.json"),
        "factory_static_review": fact(ROOT / "data/h01-r703-namespace-factory-test-review.json"),
        "factory_independent_review_known_before_execution": False,
        "recorder_dependency": fact(helper), "suite": suite, "factory": factory, "consumer": consumer,
        "suite_interpretation": {
            "original_22_successful_cases_preserved_without_rerun": True,
            "last_original_case_failed_because_initializer_does_not_call_namespace_stage_entry": True,
            "old_failure_and_reviews_preserved": True,
            "r703_uses_real_original_factories_active_initializer_stack_permit_and_empty_held_scope": True,
            "namespace_stage_factory_component_passed": True,
            "natural_public_initializer_stage_wiring_proved": False,
            "namespace_stage_writer_io_proved": False,
            "native_128_level_fixture_run": False,
        },
        "failure_boundary": {
            "original_test_line": 101,
            "error": "OWNED_IO_NAMESPACE_DEADLINE",
            "stage": "Explicit repeated resume, normal ClosingStack exit, current Pin verifier Budget.check, Namespace stack held scope original clock poll",
            "first_install_returned": True,
            "repeat_body_acquire_and_complete_verifier_returned": True,
            "repeat_return_value_evaluated": True,
            "repeat_normal_exit_initial_stack_check_returned": True,
            "current_pin_record_check_returned": True,
            "current_pin_budget_check_returned": False,
            "current_pin_payload_reread_reached": False,
            "current_pin_index_known": False,
            "repeat_normal_exit_returned": False,
            "repeat_result_equality_and_format_checks_reached": False,
            "install_helper_returned": False,
            "shared_profile_open_prepare_put_reached": False,
            "normal_final_database_bytes_and_registry_assertions_reached": False,
            "failure_earlier_than_r700": True,
            "stable_performance_or_hotspot_or_environment_cause_inferred": False,
        },
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "new_approval_rejection": False, "full_qa_census": False, "commit_or_push": False,
            "r702_prepared_or_run": False, "whole_append_tests_run": False,
            "execution_handles_terminal": True},
        "remaining": ["Complete Store consumer including repeat normal exit and shared walk prepare put",
            "Original whole append and non-admitted families caller",
            "H01 continuous owner SQL Store receipt adapter enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material quote change inputs and actual production cloud target", "Normal H06 deployment"],
    }
    path = ROOT / "data/h01-store-root-terminal-r704.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
