"""Preserve completed r708 evidence without importing or executing product code."""
from pathlib import Path
import argparse
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary-review")
    args = parser.parse_args()
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r705/r705-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(entities) == 26
    for row in entities:
        verify(row)

    interrupted_path = ROOT / "data/h01-stage-install-guard-interrupted-terminal-r705.json"
    interrupted = json.loads(interrupted_path.read_bytes())
    for key in ("plan", "claim", "candidate_manifest"):
        verify(interrupted[key])
    for row in interrupted["saved_logs"]:
        verify(row)
    assert len(interrupted["saved_logs"]) == 3
    assert interrupted["actual_exit_code"] is None
    assert interrupted["test_summary_available"] is False
    old_plan = json.loads((ROOT / interrupted["plan"]["path"]).read_bytes())
    assert len(old_plan["source_files"]) == 206
    for row in old_plan["source_files"]:
        verify(row)

    suite_terminal_path = ROOT / "data/h01-stage-install-guard-suite-terminal-r707.json"
    suite_terminal_fact = fact(suite_terminal_path)
    suite_terminal = json.loads(suite_terminal_path.read_bytes())
    suite = completed("h01-stage-install-guard-verification-r707", 0, 62010,
                      "752f24", 431.99, already_copied=True)
    assert suite == suite_terminal["suite"]
    assert len(suite["phases"]) == 72
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    assert suite["source_facts_verified"] == 206
    verify(suite_terminal_fact)

    consumer = completed("h01-store-verification-r708", 1, 1907, "372f71", 47.59)
    assert consumer["source_facts_verified"] == 201
    assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    stdout = (ROOT / "data/h01-store-verification-r708/stdout.log").read_text(encoding="utf-8")
    for marker in ("1 failed in 47.59s", "NAMESPACE_DEADLINE", "_close_stage_scope_original",
                   "_verify_stage_scope_original", "_anchor_names"):
        assert marker in stdout, marker
    assert not (ROOT / "data/h01-store-verification-r706.json").exists()
    assert not (ROOT / "data/h01-store-verification-r706-started.json").exists()

    reviews = {
        "intermediate_source_review": "data/h01-r705-install-guard-source-review.json",
        "final_source_review": "data/h01-r705-install-guard-source-review-final.json",
        "final_test_review": "data/h01-r705-install-guard-test-review.json",
        "root_source_check": "data/h01-r705-root-source-check.json",
    }
    terminal = {
        "state": "stage_install_guard_24_passed_first_install_internal_resume_stage_close_native_body_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "reviews": {name: fact(ROOT / path) for name, path in reviews.items()},
        "interrupted_r705": {
            "terminal": fact(interrupted_path), "source_facts_verified": 206,
            "saved_log_count": 3, "actual_exit_code": None,
            "test_summary_available": False, "interruption_cause_known": False,
            "not_restarted": True,
            "recovery_basis": "original execution handle missing and authoritative Get-Process PythonProcessCount=0; not log age or failed CIM query",
        },
        "suite_terminal_r707": suite_terminal_fact, "suite": suite,
        "consumer": consumer,
        "original_consumer": "test_store_stage_wiring.py::test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard",
        "r706_prepared_or_run": False,
        "failure_boundary": {
            "original_test_line": 117, "install_helper_line": 97,
            "stage": "first install internal resume normal close; Stage initial scope verification effect; original native Namespace body anchor scan budget before current entry prefix/name/stat processing",
            "error": "NAMESPACE_DEADLINE",
            "first_install_returned": False,
            "internal_resume_acquire_v2_returned": True,
            "internal_resume_writing_both_full_bodies_returned": True,
            "internal_resume_return_value_evaluated": True,
            "store_normal_close_initial_stack_returned": True,
            "store_normal_close_all_pin_verifications_returned": True,
            "store_normal_close_both_full_bodies_returned": True,
            "stage_close_entered_without_prior_first": True,
            "stage_verify_initial_scope_live_returned": True,
            "stage_effect_initial_scope_live_returned": True,
            "stage_effect_native_namespace_body_returned": False,
            "stage_effect_post_native_profile_call_reached": False,
            "stage_effect_final_scope_live_reached": False,
            "stage_verify_resource_loop_reached": False,
            "anchor_scan_current_entry_index_known": False,
            "anchor_scan_current_entry_prefix_name_stat_processing_started": False,
            "complete_anchor_scan_returned": False,
            "internal_resume_returned": False,
            "explicit_repeat_resume_reached": False,
            "install_helper_equality_and_final_db_assertions_reached": False,
            "shared_profile_open_walk_prepare_put_reached": False,
            "full_normal_exit_and_final_assertions_reached": False,
            "namespace_cleanup_failure_notes": 2,
            "performance_improvement_regression_or_environment_cause_inferred": False,
        },
        "boundary_source_facts": [fact(ROOT / "data/t16-runtime-origin-graph-r705/authority_runtime_origin_r705" / name)
            for name in ("personal_authority_store.py", "owned_stage_io_r251.py", "personal_authority_namespace.py")],
        "next_source_question": "Read only: Stage._effect calls original _N2_EFFECT then immediately ns._require_profile(actual.profile). Assess whether this same invocation has a valid duplicate physical profile observation after native return, while preserving complete native begin/body/finish, current actual owner/guard, cancellation, both clock layers, first/error ordering, final scope checks, every effect and cleanup. This later call was not reached in r708; no hotspot or next candidate is claimed.",
        "boundaries": {
            "new_candidate_after_r705": False, "all_execution_handles_terminal": True,
            "profiling": False, "deadline_changed": False,
            "complete_verifier_body_deleted": False, "fixture_reduced": False,
            "production_data_config_identity_lease_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "native_recovery_or_alias_diagnostics": False,
            "full_qa_census_or_cleanup": False, "commit_or_push": False,
            "new_approval_rejection": False, "original_pauses_preserved": True,
            "last_normal_service_evidence_section": "17.126",
        },
        "remaining": [
            "complete Store install/repeat normal exit and shared walk/prepare/put",
            "original whole append duplicate resume real slot page readback and conflicting original parent tests",
            "CLOSED_FAMILIES_WHOLECALLER_NOT_ADMITTED and ORIGIN_STORE_RECEIPT_ADAPTER_NOT_ADMITTED",
            "H01 continuous owner, owned SQL, Store receipt adapter, enrollment, activation/completion, live witness/frontier, 11 mutations and 8 attachment phases",
            "H02-H06 and permanent share revocation",
            "full P06 SourceMap/work and exact released16 missing tasks/worker and six resources",
            "T15 real materials/quote/change inputs and actual production cloud target; city Shaoxing and other materials undecided",
            "normal H06 deployment; isolated frontend results are not deployment evidence",
        ],
        "recorder_dependency": fact(helper),
    }
    if args.boundary_review:
        review_path = (ROOT / args.boundary_review).resolve()
        review_path.relative_to(ROOT)
        terminal["independent_boundary_review"] = fact(review_path)
    path = ROOT / "data/h01-store-root-terminal-r708.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
        "r705_logs": 3, "r707_phases": len(suite["phases"]),
        "r708_logs": len(consumer["logs"]), "r708_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
