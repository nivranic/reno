"""Record terminal r713/r714 evidence without importing product modules."""
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
    assert (args.exit_code, args.session, args.chunk, args.seconds) == (1, 56229, "49b1ec", 51.18)
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r713/r713-candidate-manifest.json"
    assert fact(manifest)["sha256"] == "8ee449b03c90b3b39b2efa643bc9716de45aa13345cc0a87757d9604ca9fbff6"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(entities) == 25
    for row in entities:
        verify(row)

    suite_path = ROOT / "data/h01-source-checksum-suite-terminal-r713.json"
    suite_fact = fact(suite_path)
    assert suite_fact["sha256"] == "a53324ad98791d9ac30d8bf11b02bb82f7534b4e4e74cb3b1e687793fc3f40e8"
    suite_terminal = json.loads(suite_path.read_bytes())
    prior_suite = suite_terminal["suite"]
    assert prior_suite["actual_exit_code"] == 0
    assert (prior_suite["tool_session"], prior_suite["terminal_chunk"], prior_suite["seconds"]) == (86825, "4fd365", 416.27)
    suite = completed("h01-source-checksum-verification-r713", 0,
                      prior_suite["tool_session"], prior_suite["terminal_chunk"],
                      prior_suite["seconds"], already_copied=True)
    assert suite == prior_suite
    assert len(suite["phases"]) == 57 and suite["source_facts_verified"] == 208
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    for name in ("candidate_manifest", "root_source_check", "source_review", "test_review",
                 "previous_full_failure", "recorder_dependency"):
        verify(suite_terminal[name])
    verify(suite_fact)

    timing_path = ROOT / "data/h01-r713-source-checksum-test-review-timing-addendum.json"
    assert fact(timing_path)["sha256"] == "bc64c10b64dc5e66503517cd73dbf66c5015ed570c814a5d27cd9be717f2340c"
    review_path = (ROOT / args.boundary_review).resolve(strict=True)
    review_path.relative_to(ROOT)
    review = json.loads(review_path.read_bytes())
    assert fact(review_path)["sha256"] == "185fc0049b1d5627cfbde4d0d4953d1723ddff93ec61d9aea1470879bd1075e8"
    for row in review["logs"] + review["sources"] + [review["plan"], review["claim"]]:
        verify(row)
    assert review["execution_evidence"]["tool_session"] == args.session
    assert review["execution_evidence"]["terminal_chunk"] == args.chunk
    assert review["execution_evidence"]["actual_exit_code"] == args.exit_code
    consumer = completed("h01-store-verification-r714", args.exit_code,
                         args.session, args.chunk, args.seconds,
                         already_copied=args.consumer_logs_already_copied)
    assert consumer["source_facts_verified"] == 201 and len(consumer["phases"]) == 3
    if args.exit_code == 1:
        assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
            ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    elif args.exit_code == 0:
        assert all(row["outcome"] == "passed" for row in consumer["phases"])
    else:
        raise AssertionError("Unexpected full consumer exit; preserve it separately")
    stdout = (ROOT / "data/h01-store-verification-r714/stdout.log").read_text(encoding="utf-8")
    assert f"1 {'passed' if args.exit_code == 0 else 'failed'} in {args.seconds:.2f}s" in stdout
    assert "NAMESPACE_DEADLINE" in stdout and "STAGE_ORIGINAL_CLEANUP_RETAINED" in stdout
    assert "Namespace cleanup also failed" not in stdout

    source_review_path = ROOT / "data/h01-r713-source-checksum-source-review.json"
    source_review = json.loads(source_review_path.read_bytes())
    for draft in source_review["preserved_drafts"]:
        verify(draft["store"])
        verify(draft["preservation_manifest"])
    preserved_test = ROOT / "data/h01-source-checksum-before-r713/test_source_checksum_r713.py"
    assert fact(preserved_test)["sha256"] == "bda57f05e9323f36c0312ddfa81b22a86c279a862e68fa56091ff5658fbfe931"
    terminal = {
        "state": "source_checksum_19_passed_first_install_header_temporary_writer_post_open_effect_namespace_write_tail_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": fact(ROOT / "data/h01-r713-root-source-check.json"),
        "source_review": fact(source_review_path),
        "test_review": fact(ROOT / "data/h01-r713-source-checksum-test-review.json"),
        "test_review_timing_addendum": fact(timing_path),
        "review_timing": "Zero-blocking independent source and test conclusions preceded execution. The test-review file may have been written during launch; its original before_prepare label is superseded by the separately preserved timing addendum.",
        "static_draft_corrections": {
            "directory_dispatch": "Existing supported Namespace directory single/sweep/paths and Store sweep-dispatch custom behavior must retain the original checksum path",
            "sha_callable_order": "Capture the original _sha callable before evaluating the remaining raw operand; late replacement during its complete second Store stack affects only later checksum expressions",
            "preserved_initial_source_prefix": "5b2a",
            "preserved_intermediate_source_prefix": "efdd",
            "draft_facts_recorded_in_source_review": True,
            "preserved_source_drafts": source_review["preserved_drafts"],
            "runtime_red_tests_claimed": False,
            "preserved_test_draft": fact(preserved_test),
            "final_test": fact(ROOT / "tests/test_source_checksum_r713.py"),
        },
        "implementation_scope": {
            "changed_original_definition": "_source_pin_batch_original",
            "unchanged_original_definitions": 149, "unchanged_support_modules": 15,
            "complete_second_store_stack_retained": True,
            "only_merged_work": "the second raw producer's repeated target physical identity and FileObservation allocation after the complete second Store stack",
            "first_sha_short_circuit_and_checksum_callable_selected_before_raw_retained": True,
            "late_resource_lookup_poll_first_occurs_before_payload_hash": True,
            "real_acquisition_open_read_hash_and_second_filehold_verification_retained": True,
            "source_acquire_and_verify_sources_unchanged": True,
        },
        "suite_terminal": suite_fact, "suite": suite, "consumer": consumer,
        "boundary_review": fact(review_path), "reviewed_boundary": review,
        "failure_boundary": {
            "original_test_line": 117, "install_helper_line": 97,
            "error": "NAMESPACE_DEADLINE",
            "stage": "first install internal resume header temporary writer post-native-open effect; Namespace body writing-tail second stack non-lock directory identity current-scope Budget check",
            "journal_and_five_children_returned": True,
            "header_bytes_constructed": True,
            "header_stage_resource_registered": True,
            "header_native_temporary_created": True,
            "header_native_handle_transferred_to_fd": True,
            "header_initial_native_captured": True,
            "header_metadata_phase": "fresh-empty-before-private",
            "header_private_initialize_reached": False,
            "header_private_fd_check_reached": False,
            "header_graph_attach_and_stream_created": False,
            "header_payload_write_flush_fsync_reached": False,
            "header_writer_close_read_custody_rename_reached": False,
            "header_published": False,
            "namespace_runtime_scope_registered_and_body_entered": True,
            "namespace_body_tail_first_full_stack_returned": True,
            "namespace_writing_prefix_returned": True,
            "namespace_writing_second_stack_returned": False,
            "current_held_branch": "non-lock",
            "current_held_resource_kind_name_index_known": False,
            "current_held_initial_poll_returned": True,
            "current_held_graph_initial_directory_path_handle_checks_returned": True,
            "current_held_path_and_handle_identity_equal": True,
            "current_runtime_directory_identity_returned": False,
            "current_directory_runtime_namespace_budget_check_returned": False,
            "independent_writing_tail_lock_verify_reached": False,
            "current_registered_namespace_error_finish_attempted": True,
            "successful_namespace_finish_proved": False,
            "current_stage_effect_post_native_profile_and_final_scope_reached": False,
            "internal_resume_acquire_v2_reached": False,
            "internal_resume_store_verify_bodies_reached": False,
            "internal_resume_return_dictionary_evaluated": False,
            "first_install_returned": False, "internal_resume_returned": False,
            "explicit_repeat_resume_reached": False,
            "shared_profile_open_walk_prepare_put_reached": False,
            "cleanup_note": "STAGE_ORIGINAL_CLEANUP_RETAINED",
            "namespace_cleanup_failure_notes_in_stdout": 0,
            "performance_or_regression_or_environment_cause_inferred": False,
        },
        "one_next_source_question": review["one_next_source_question"],
        "original_consumer": "test_store_stage_wiring.py::test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard",
        "coverage_limits": {
            "suite_is_full_installation_or_performance_proof": False,
            "publication_is_metadata_component_not_complete_public_install": True,
            "cleanup_teardown_alone_proves_all_native_closure": False,
        },
        "boundaries": {
            "all_execution_handles_terminal": True,
            "new_candidate_after_r713": False,
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
    path = ROOT / "data/h01-store-root-terminal-r714.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
        "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
        "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
