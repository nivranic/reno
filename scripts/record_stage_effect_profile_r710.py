"""Record terminal r709/r710 evidence only; no product imports or execution."""
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
    parser.add_argument("--boundary-review", required=True)
    args = parser.parse_args()
    assert (args.exit_code, args.session, args.chunk, args.seconds) == (1, 95423, "8603d8", 51.92)
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r709/r709-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(entities) == 25
    for row in entities:
        verify(row)

    suite_path = ROOT / "data/h01-stage-effect-profile-suite-terminal-r709.json"
    suite_fact = fact(suite_path)
    assert suite_fact["sha256"] == "2379ee76a286cace29accb32376218956b89fec5f85f6d63a0f236af84a31776"
    original_suite = json.loads(suite_path.read_bytes())
    suite = completed("h01-stage-effect-profile-verification-r709", 0, 27673,
                      "008437", 363.86, already_copied=True)
    assert suite == original_suite["suite"]
    assert len(suite["phases"]) == 42 and suite["source_facts_verified"] == 206
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    verify(suite_fact)

    review_path = (ROOT / args.boundary_review).resolve(strict=True)
    review_path.relative_to(ROOT)
    boundary_review = json.loads(review_path.read_bytes())
    assert fact(review_path)["sha256"] == "6ee1568dc5e6d6af4bf79e5702ac1c8e5013bbfea1a2ef64e9b317796dd41624"
    for row in boundary_review["logs"] + boundary_review["sources"] + [boundary_review["plan"], boundary_review["claim"]]:
        verify(row)
    assert boundary_review["execution_evidence"]["tool_session"] == args.session
    assert boundary_review["execution_evidence"]["terminal_chunk"] == args.chunk
    consumer = completed("h01-store-verification-r710", args.exit_code,
                         args.session, args.chunk, args.seconds, already_copied=True)
    assert consumer["source_facts_verified"] == 201
    assert len(consumer["phases"]) == 3
    if args.exit_code == 1:
        assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
            ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    elif args.exit_code == 0:
        assert all(row["outcome"] == "passed" for row in consumer["phases"])
    else:
        raise AssertionError("Unexpected full consumer exit; preserve it separately")
    stdout = (ROOT / "data/h01-store-verification-r710/stdout.log").read_text(encoding="utf-8")
    assert f"1 {'passed' if args.exit_code == 0 else 'failed'} in {args.seconds:.2f}s" in stdout
    assert "OWNED_IO_NAMESPACE_DEADLINE" in stdout
    assert "Namespace cleanup also failed" not in stdout
    terminal = {
        "state": "stage_effect_profile_14_passed_first_install_internal_resume_store_close_pin_budget_held_lock_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": fact(ROOT / "data/h01-r709-root-source-check.json"),
        "source_review": fact(ROOT / "data/h01-r709-effect-profile-source-review.json"),
        "test_review": fact(ROOT / "data/h01-r709-effect-profile-test-review.json"),
        "review_timing": "Oral zero-blocking findings before freeze and run; source/test artifact writes may overlap suite launch",
        "source_draft_history": {
            "initial_stage_prefix": "3d595",
            "final_admission_adjustment": "module identity checked before new symbol reads; new callable selection uses module dictionaries",
            "intermediate_stage_prefix": "edacc",
            "withdrawn_suggestion": "additional arbitrary same-process class shape/init-new sealing was outside the selected compatibility scope and is absent from final delta",
            "runtime_red_defect_claimed": False,
            "final_stage_sha256": "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611",
        },
        "suite_terminal": suite_fact, "suite": suite, "consumer": consumer,
        "boundary_review": fact(review_path),
        "reviewed_boundary": boundary_review,
        "failure_boundary": {
            "original_test_line": 117, "install_helper_line": 97,
            "error": "OWNED_IO_NAMESPACE_DEADLINE",
            "stage": "first install internal resume normal Store exit Pin loop; current Pin Budget check enters Namespace held lock identity verification and fails at its initial poll",
            "internal_resume_acquire_v2_returned": True,
            "internal_resume_writing_both_full_bodies_returned": True,
            "internal_resume_return_value_evaluated": True,
            "store_normal_close_initial_stack_returned": True,
            "store_normal_close_all_pin_verifications_returned": False,
            "current_pin_record_full_stack_returned": True,
            "current_pin_budget_check_returned": False,
            "current_pin_pre_read_state_and_observation_reached": False,
            "current_pin_payload_reread_reached": False,
            "current_pin_index_known": False,
            "current_namespace_held_resource_kind": "lock",
            "current_lock_content_verification_requested": False,
            "current_lock_identity_initial_poll_returned": False,
            "current_lock_identity_physical_check_after_initial_poll_reached": False,
            "store_normal_close_two_full_verifier_bodies_reached": False,
            "stage_normal_close_verifier_reached": False,
            "first_install_returned": False, "internal_resume_returned": False,
            "explicit_repeat_resume_reached": False,
            "install_helper_equality_and_final_db_assertions_reached": False,
            "shared_profile_open_walk_prepare_put_reached": False,
            "all_native_resources_closed_inferred_from_teardown": False,
            "namespace_cleanup_failure_notes_in_stdout": 0,
            "performance_improvement_regression_or_environment_cause_inferred": False,
        },
        "original_consumer": "test_store_stage_wiring.py::test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard",
        "next_source_question": "Read only: after successful original _Budget.check in Pin.verify, the fixed _pin_verify_stack_state_original path again requires the same guard. Assess combining only that second physical profile observation under the same invocation's genuine Namespace acquisition/profile/guard, selected Pin and Store budget association, fixed dispatch and r705 immutable directory shape restrictions. Preserve the entire original Budget/Namespace stack and lock poll, original state/first/registry ordering, real directory and file read/hash/charge, and all close behavior. Unknown or ambiguous rebinding keeps the full path. This later step was not reached in r710; no r711 candidate, execution or hotspot conclusion.",
        "publication_coverage": {
            "original_integration_wrapped_inside_after_uses_full_fallback": True,
            "new_original_dispatch_publication_has_real_factory_registry_and_readback_cleanup": True,
            "two_publication_cases_passed": True,
            "complete_install_or_stable_performance_inferred_from_suite": False,
        },
        "boundaries": {
            "all_execution_handles_terminal": True,
            "new_candidate_after_r709": False,
            "profiling": False, "deadline_changed": False,
            "complete_verifier_body_deleted": False, "fixture_reduced": False,
            "production_data_config_identity_lease_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "native_recovery_or_alias_diagnostics": False,
            "full_qa_census_or_cleanup": False, "commit_or_push": False,
            "new_approval_rejection": False, "original_pauses_preserved": True,
            "last_normal_service_evidence_section": "17.126",
            "recording_discovery_mistake": "A recursive rg filename search under data hit old denied QA directories with OS5; no retry, escalation or ACL changes; subsequent reads used named evidence paths only",
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
    path = ROOT / "data/h01-store-root-terminal-r710.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
        "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
        "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
