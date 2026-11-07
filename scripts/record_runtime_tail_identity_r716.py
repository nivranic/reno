"""Record terminal r715/r716 evidence using files only; never import product code."""
from collections import Counter, defaultdict
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
    assert (args.exit_code, args.session, args.chunk, args.seconds) == (1, 30834, "2f740d", 69.15)
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))

    def verify_fact_tree(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                path = (ROOT / value["path"]).resolve(strict=True)
                path.relative_to(ROOT)
                actual = fact(path)
                assert actual["sha256"] == value["sha256"], value["path"]
                if "bytes" in value:
                    assert actual["bytes"] == value["bytes"], value["path"]
            for item in value.values():
                verify_fact_tree(item)
        elif isinstance(value, list):
            for item in value:
                verify_fact_tree(item)

    manifest = ROOT / "data/t16-runtime-origin-graph-r715/r715-candidate-manifest.json"
    assert fact(manifest)["sha256"] == "005ae33a7aabf736685930441a334aa49c4738550909dfa0fed865e793cc57c3"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(candidate["source_files"]) == 20 and len(candidate["artifacts"]) == 7
    for row in entities:
        verify(row)
    evidence = candidate["evidence"]
    extractions = evidence["mechanical_extractions"]
    assert len(extractions) == 6 and all(row["inverse_ast_equal"] is True for row in extractions)
    assert evidence["namespace_inverse_bytes_equal"] and evidence["consumer_inverse_bytes_equal"]
    assert evidence["unchanged_namespace_definitions"] == 216
    assert evidence["unchanged_support_modules"] == 13

    suite_path = ROOT / "data/h01-runtime-tail-identity-suite-terminal-r715.json"
    suite_fact = fact(suite_path)
    assert suite_fact["sha256"] == "fe5394b344100588430af7f2a57d8e51d1882b544a198e47cd444016ec2b526c"
    suite_terminal = json.loads(suite_path.read_bytes())
    assert suite_terminal["state"] == "runtime_tail_identity_suite_passed"
    prior_suite = suite_terminal["suite"]
    assert (prior_suite["actual_exit_code"], prior_suite["tool_session"],
            prior_suite["terminal_chunk"], prior_suite["seconds"]) == (0, 36145, "9e68e8", 537.46)
    suite = completed("h01-runtime-tail-identity-verification-r715", 0,
                      prior_suite["tool_session"], prior_suite["terminal_chunk"],
                      prior_suite["seconds"], already_copied=True)
    assert suite == prior_suite
    assert len(suite["phases"]) == 111 and suite["source_facts_verified"] == 202
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    inventory = suite_terminal["case_inventory"]
    assert (inventory["declared_local_cases"], inventory["declared_imported_cases"],
            inventory["declared_total_cases"], inventory["actual_reported_cases"],
            inventory["actual_call_cases"], inventory["actual_local_cases"],
            inventory["actual_imported_cases"]) == (17, 20, 37, 37, 37, 17, 20)
    assert not inventory["missing_or_extra_case_counts"]
    reports = defaultdict(list)
    for phase in suite["phases"]:
        reports[phase["nodeid"]].append(phase)
    assert len(reports) == 37
    assert all(Counter(row["phase"] for row in rows) == Counter({"setup": 1, "call": 1, "teardown": 1})
               for rows in reports.values())
    actual = Counter(nodeid.rsplit("::", 1)[-1].split("[", 1)[0] for nodeid in reports)
    assert actual == Counter(inventory["declared_cases_by_function"])
    assert actual == Counter(inventory["actual_cases_by_function"])
    verify_fact_tree(suite_terminal)
    assert b"37 passed in 537.46s" in (ROOT / "data/h01-runtime-tail-identity-verification-r715/stdout.log").read_bytes()

    reviews = {}
    for name in ("root_source_check", "source_review", "test_review"):
        path = ROOT / suite_terminal[name]["path"]
        reviews[name] = json.loads(path.read_bytes())
        verify_fact_tree(reviews[name])
    source_review = reviews["source_review"]
    assert not source_review["remaining_findings_in_reviewed_scope"]
    assert not reviews["test_review"]["remaining_findings_in_reviewed_scope"]
    independent = source_review["independent_static_checks"]
    assert independent["whole_runtime_body_inverse_ast_equal"]
    assert len(independent["six_mechanical_extractions"]) == 6
    assert all(row["inverse_ast_equal"] is True for row in independent["six_mechanical_extractions"])
    preserved_draft_copies = []
    for draft in source_review["preserved_drafts"]:
        draft_manifest = ROOT / draft["path"]
        inputs = json.loads(draft_manifest.read_bytes())["inputs"]
        assert len({Path(row["path"]).name for row in inputs}) == len(inputs)
        copies = []
        for row in inputs:
            saved = draft_manifest.parent / Path(row["path"]).name
            saved_fact = fact(saved)
            assert saved_fact["bytes"] == row["bytes"] and saved_fact["sha256"] == row["sha256"]
            copies.append(saved_fact)
        preserved_draft_copies.append({"manifest": draft, "preserved_files": copies})
    first_attempt_path = ROOT / "data/h01-r716-recorder-first-attempt.json"
    first_attempt = json.loads(first_attempt_path.read_bytes())
    verify_fact_tree(first_attempt)
    second_attempt_path = ROOT / "data/h01-r716-recorder-second-attempt.json"
    verify_fact_tree(json.loads(second_attempt_path.read_bytes()))

    review_path = (ROOT / args.boundary_review).resolve(strict=True)
    review_path.relative_to(ROOT)
    assert fact(review_path)["sha256"] == "b88de75f8ae3f31aba207d6140795cff25456d868886a12f7d873f5785b77d5c"
    review = json.loads(review_path.read_bytes())
    verify_fact_tree(review)
    for row in review["logs"] + review["sources"] + [review["plan"], review["claim"]]:
        verify(row)
    execution_evidence = review["execution_evidence"]
    assert execution_evidence["tool_session"] == args.session
    assert execution_evidence["terminal_chunk"] == args.chunk
    assert execution_evidence["actual_exit_code"] == args.exit_code
    if "seconds" in execution_evidence:
        assert execution_evidence["seconds"] == args.seconds
    consumer = completed("h01-store-verification-r716", args.exit_code,
                         args.session, args.chunk, args.seconds,
                         already_copied=args.consumer_logs_already_copied)
    assert consumer["source_facts_verified"] == 201 and len(consumer["phases"]) == 3
    assert execution_evidence["source_count"] == consumer["source_facts_verified"]
    assert execution_evidence["phases"] == consumer["phases"]
    assert review["plan"] == consumer["plan"] and review["claim"] == consumer["claim"]
    assert len(review["logs"]) == 5 and len(consumer["logs"]) == 5
    assert {row["path"]: row for row in review["logs"]} == {row["path"]: row for row in consumer["logs"]}
    outcome = "passed" if args.exit_code == 0 else "failed"
    assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
        ("setup", "passed"), ("call", outcome), ("teardown", "passed")]
    assert len({row["nodeid"] for row in consumer["phases"]}) == 1
    stdout = (ROOT / "data/h01-store-verification-r716/stdout.log").read_bytes()
    assert f"1 {outcome} in {args.seconds:.2f}s".encode("ascii") in stdout
    assert b"OWNED_IO_NAMESPACE_DEADLINE" in stdout
    assert b"STAGE_ORIGINAL_CLEANUP_RETAINED" in stdout
    assert b"AUTHORITY_STORE_EXIT_VERIFICATION_FAILED" in stdout

    terminal = {
        "state": "runtime_tail_identity_37_passed_shared_operations_thirteenth_node_pre_fsync_namespace_begin_deadline",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": suite_terminal["root_source_check"],
        "source_review": suite_terminal["source_review"],
        "test_review": suite_terminal["test_review"],
        "review_timing": "Both r715 independent source and test review files were delivered before Root prepared and launched the r715 suite; this is the recorded coordinator sequence, not inferred from filesystem timestamps.",
        "static_draft_corrections": source_review["resolved_static_findings"],
        "preserved_source_drafts": source_review["preserved_drafts"],
        "preserved_draft_copies_verified": preserved_draft_copies,
        "recorder_first_attempt": fact(first_attempt_path),
        "recorder_second_attempt": fact(second_attempt_path),
        "recorder_correction": "Two file-only attempts exited before terminal creation: historical draft facts were initially compared against final active paths, then original Windows stdout failed strict UTF-8 decoding. Both failed scripts and notes are preserved. Historical hashes are checked against all 36 saved draft files; ASCII result/error markers are checked as raw bytes. No source, test, review or log changed.",
        "implementation_scope": {
            "changed_original_definition": "_verify_closed_namespace_owner_runtime_body_original",
            "unchanged_namespace_definitions": 216, "unchanged_support_modules": 13,
            "six_mechanical_extractions": extractions,
            "independent_six_ast_inverse_checks": independent["six_mechanical_extractions"],
            "only_merged_work": "Second same-body writing-tail traversal original._identities path/handle sample for an admitted non-lock filehold already checked by the complete first traversal",
            "complete_first_stack_retained": True,
            "second_stack_logical_owner_registry_first_poll_graph_directory_budget_checks_retained": True,
            "three_runtime_directory_context_budget_chains_per_filehold_retained": True,
            "lock_full_physical_checks_and_independent_content_read_retained": True,
            "payload_reads_hash_charge_and_normal_or_error_finish_retained": True,
            "namespace_sha_only_adapters": evidence["namespace_sha_only_adapters"],
            "new_scope_token_caller_proof_or_cross_effect_cache": False,
        },
        "suite_terminal": suite_fact, "suite": suite, "case_inventory": inventory,
        "consumer": consumer, "boundary_review": fact(review_path),
        "reviewed_boundary": review,
        "failure_boundary": review["independently_confirmed_boundary"],
        "one_next_source_question": review["one_next_source_question"],
        "previous_full_failure": suite_terminal["previous_full_failure"],
        "original_consumer": "test_store_stage_wiring.py::test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard",
        "coverage_limits": {
            "suite_is_full_installation_or_performance_proof": False,
            "stable_performance_or_hotspot_or_regression_cause_inferred": False,
            "cleanup_teardown_alone_proves_all_native_closure": False,
            "whole_goal_completion_inferred_from_full_consumer": False,
        },
        "boundaries": {
            "all_execution_handles_terminal": True,
            "profiling": False, "deadline_changed": False,
            "complete_verifier_body_deleted": False, "fixture_reduced": False,
            "production_data_config_identity_lease_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "native_recovery_or_alias_diagnostics": False,
            "full_qa_census_or_cleanup": False, "commit_or_push": False,
            "new_approval_rejection": False, "original_pauses_preserved": True,
            "last_normal_service_evidence_section": "17.126",
        },
        "remaining": [
            "complete Store install/repeat normal exit and shared walk/prepare/put" if args.exit_code else "original whole-append consumers after this Store result",
            "original whole append duplicate resume real slot page readback and conflicting original parent tests",
            "CLOSED_FAMILIES_WHOLECALLER_NOT_ADMITTED and ORIGIN_STORE_RECEIPT_ADAPTER_NOT_ADMITTED",
            "H01 continuous owner, owned SQL, Store receipt adapter, enrollment, activation/completion, live witness/frontier, 11 mutations and 8 attachment phases",
            "H02-H06 and permanent share revocation",
            "full P06 SourceMap/work and exact released16 missing tasks/worker and six resources",
            "T15 real materials/quote/change inputs and actual production cloud target; user city Shaoxing, current construction stage and waterproofing material brand/model undecided",
            "normal H06 deployment; isolated frontend results are not deployment evidence",
        ],
        "recorder_dependency": fact(helper),
        "suite_recorder": fact(ROOT / "scripts/record_runtime_tail_identity_r715.py"),
    }
    path = ROOT / "data/h01-store-root-terminal-r716.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
        "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
        "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
