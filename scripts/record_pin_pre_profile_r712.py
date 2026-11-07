"""Preserve terminal r711/r712 evidence; never import or execute product code."""
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
    parser.add_argument("--consumer-logs-already-copied", action="store_true")
    args = parser.parse_args()
    assert (args.exit_code, args.session, args.chunk, args.seconds) == (1, 66378, "aa0c99", 50.48)
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r711/r711-candidate-manifest.json"
    assert fact(manifest)["sha256"] == "a011b8faca62cbc4d6fcfdef8d2c4819212c80155ba3a928d5c88b77e3df3731"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(entities) == 25
    for row in entities:
        verify(row)

    suite_path = ROOT / "data/h01-pin-pre-profile-suite-terminal-r711.json"
    suite_fact = fact(suite_path)
    assert suite_fact["sha256"] == "394f2ce3108942128e51a7e8a0845aec7c4e54229620c93fe3a86ff21bbca97e"
    suite_terminal = json.loads(suite_path.read_bytes())
    prior_suite = suite_terminal["suite"]
    assert prior_suite["actual_exit_code"] == 0
    assert (prior_suite["tool_session"], prior_suite["terminal_chunk"], prior_suite["seconds"]) == (57789, "a8e9cf", 360.86)
    suite = completed("h01-pin-pre-profile-verification-r711", 0,
                      prior_suite["tool_session"], prior_suite["terminal_chunk"],
                      prior_suite["seconds"], already_copied=True)
    assert suite == prior_suite
    assert len(suite["phases"]) == 48 and suite["source_facts_verified"] == 207
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    for name in ("candidate_manifest", "root_source_check", "source_review", "test_review",
                 "previous_full_failure", "recorder_dependency"):
        verify(suite_terminal[name])
    verify(suite_fact)

    review_path = (ROOT / args.boundary_review).resolve(strict=True)
    review_path.relative_to(ROOT)
    review = json.loads(review_path.read_bytes())
    assert fact(review_path)["sha256"] == "5d4fdb8e98f949818227e57be9179bd387a08428f2cb6a96f2b63cf312d0b81e"
    for row in review["logs"] + review["sources"] + [review["plan"], review["claim"]]:
        verify(row)
    assert review["execution_evidence"]["tool_session"] == args.session
    assert review["execution_evidence"]["terminal_chunk"] == args.chunk
    assert review["execution_evidence"]["actual_exit_code"] == args.exit_code
    consumer = completed("h01-store-verification-r712", args.exit_code,
                         args.session, args.chunk, args.seconds,
                         already_copied=args.consumer_logs_already_copied)
    assert consumer["source_facts_verified"] == 201
    assert len(consumer["phases"]) == 3
    if args.exit_code == 1:
        assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
            ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    elif args.exit_code == 0:
        assert all(row["outcome"] == "passed" for row in consumer["phases"])
    else:
        raise AssertionError("Unexpected full consumer exit; preserve it separately")
    stdout = (ROOT / "data/h01-store-verification-r712/stdout.log").read_text(encoding="utf-8")
    assert f"1 {'passed' if args.exit_code == 0 else 'failed'} in {args.seconds:.2f}s" in stdout
    assert "OWNED_IO_NAMESPACE_DEADLINE" in stdout
    assert "Namespace cleanup also failed" not in stdout

    static_test_before = ROOT / "data/h01-pin-pre-profile-before-r711/test_pin_pre_profile_r711.py"
    assert fact(static_test_before)["sha256"] == "bc72816e9855cbb660af21a32a92f959f4dce07d9bf4ce31d8ce6370853743cb"
    final_test = ROOT / "tests/test_pin_pre_profile_r711.py"
    assert fact(final_test)["sha256"] == "1c7fc2fa0d00310f7d29cf82280f642bacdfda824e9baab03234e3e559e96bea"
    terminal = {
        "state": "pin_pre_profile_16_passed_first_install_internal_resume_second_body_namespace_begin_held_nonlock_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": fact(ROOT / "data/h01-r711-root-source-check-final.json"),
        "source_review": fact(ROOT / "data/h01-r711-pin-pre-profile-source-review.json"),
        "test_review": fact(ROOT / "data/h01-r711-pin-pre-profile-test-review.json"),
        "review_timing": "Source review artifact preceded freeze; final test oral zero findings preceded execution and its artifact may have been written during launch",
        "static_test_count_correction": {
            "before": fact(static_test_before), "after": fact(final_test),
            "source_charge_path": "source pin charge calls the actual Store Budget.check and therefore retains one additional complete Namespace profile and identity sweep per verify",
            "per_source_verify_marker_passes": 4, "per_source_verify_namespace_identity_sweeps": 2,
            "per_business_verify_marker_passes": 2, "per_business_verify_namespace_identity_sweeps": 1,
            "disabled_new_pre_helper_additional_marker_passes": 1,
            "runtime_red_test_claimed": False, "candidate_changed_for_test_correction": False,
        },
        "suite_terminal": suite_fact, "suite": suite, "consumer": consumer,
        "boundary_review": fact(review_path), "reviewed_boundary": review,
        "failure_boundary": {
            "original_test_line": 117, "install_helper_line": 97,
            "error": "OWNED_IO_NAMESPACE_DEADLINE",
            "stage": "first install internal resume writing verify second native body begins Namespace verification; original runtime observation begin initial stack check fails in a non-lock held identity initial poll before yielding context",
            "internal_resume_acquire_v2_returned": True,
            "internal_resume_first_full_verify_body_returned": True,
            "internal_resume_second_full_verify_body_started": True,
            "second_body_initial_namespace_verification_returned": False,
            "current_namespace_runtime_observation_yielded": False,
            "current_namespace_runtime_observation_registered": False,
            "current_namespace_runtime_body_reached": False,
            "current_unregistered_namespace_scope_finish_reached": False,
            "current_namespace_held_branch": "non-lock identity",
            "current_held_resource_kind_name_and_index_known": False,
            "current_held_identity_initial_poll_returned": False,
            "current_held_identity_physical_checks_after_poll_reached": False,
            "second_body_store_state_verification_reached": False,
            "second_body_store_pin_reread_reached": False,
            "second_body_store_node_and_journal_scan_reached": False,
            "internal_resume_return_dictionary_evaluated": False,
            "normal_store_exit_full_verifier_reached": False,
            "first_install_returned": False, "internal_resume_returned": False,
            "explicit_repeat_resume_reached": False,
            "install_helper_equality_and_final_db_assertions_reached": False,
            "shared_profile_open_walk_prepare_put_reached": False,
            "namespace_cleanup_failure_notes_in_stdout": 0,
            "all_native_resources_closed_inferred_from_teardown": False,
            "performance_improvement_regression_or_environment_cause_inferred": False,
        },
        "original_consumer": "test_store_stage_wiring.py::test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard",
        "one_next_source_question": review["one_next_source_question"],
        "coverage_limits": {
            "suite_is_full_installation_or_performance_proof": False,
            "no_new_profile_or_deadline_permission_created": True,
            "publication_is_real_metadata_component_not_public_complete_install": True,
            "cleanup_teardown_alone_proves_all_native_closure": False,
        },
        "boundaries": {
            "all_execution_handles_terminal": True,
            "new_candidate_after_r711": False,
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
    path = ROOT / "data/h01-store-root-terminal-r712.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
        "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
        "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
