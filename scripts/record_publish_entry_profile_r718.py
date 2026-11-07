"""Seal terminal r717/r718 evidence with file reads/copies and AST only."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import ast
import json
import re
import runpy

ROOT = Path(__file__).resolve().parents[1]
CASE = "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exit_code", type=int)
    parser.add_argument("session", type=int)
    parser.add_argument("chunk")
    parser.add_argument("seconds", type=float)
    parser.add_argument("--boundary-review", required=True)
    parser.add_argument("--consumer-logs-already-copied", action="store_true")
    args = parser.parse_args()
    assert args.session == 74424 and args.chunk and args.seconds >= 0
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

    manifest = ROOT / "data/t16-runtime-origin-graph-r717/r717-candidate-manifest.json"
    assert fact(manifest)["sha256"] == "2636ea3697481ec0f4b60f7467c851657a8f259f126c4a893479bd7e7c387de4"
    candidate = json.loads(manifest.read_bytes())
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len(candidate["source_files"]) == 20 and len(candidate["artifacts"]) == 5
    for row in entities:
        verify(row)
    evidence = candidate["evidence"]
    assert evidence["changed_definitions"] == ["_publish_store_metadata_original"]
    assert evidence["unchanged_original_definitions"] == 152
    assert evidence["unchanged_support_modules"] == 15
    for key in ("consumer_inverse_bytes_equal", "store_inverse_bytes_equal", "lookup_suffix_ast_equal",
                "publish_suffix_ast_equal", "second_held_and_directories_ast_equal",
                "first_need_extraction_inverse_ast_equal"):
        assert evidence[key] is True, key

    suite_path = ROOT / "data/h01-publish-entry-profile-suite-terminal-r717.json"
    assert fact(suite_path)["sha256"] == "fbc670b3ef4b5e67ea3dbca9802303071d70d2d930ade649d4d1957c17230960"
    suite_terminal = json.loads(suite_path.read_bytes())
    assert suite_terminal["state"] == "publish_entry_profile_suite_passed"
    suite = completed("h01-publish-entry-profile-verification-r717", 0, 61786, "67fc6c", 300.57,
                      already_copied=True)
    assert suite == suite_terminal["suite"]
    assert suite["source_facts_verified"] == 202 and len(suite["phases"]) == 75
    assert all(row["outcome"] == "passed" for row in suite["phases"])
    inventory = suite_terminal["case_inventory"]
    assert (inventory["declared_local_cases"], inventory["declared_imported_cases"],
            inventory["declared_total_cases"], inventory["actual_reported_cases"],
            inventory["actual_call_cases"]) == (25, 0, 25, 25, 25)
    assert not inventory["missing_or_extra_case_counts"]
    test = ROOT / inventory["test"]["path"]
    tree = ast.parse(test.read_bytes(), filename=str(test))
    declared = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        count = 1
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "parametrize"):
                assert isinstance(decorator.args[1], (ast.List, ast.Tuple)), node.name
                count *= len(decorator.args[1].elts)
        assert node.name not in declared
        declared[node.name] = count
    reports = defaultdict(list)
    for phase in suite["phases"]:
        reports[phase["nodeid"]].append(phase)
    assert len(reports) == 25
    assert all(Counter(row["phase"] for row in rows) == Counter({"setup": 1, "call": 1, "teardown": 1})
               for rows in reports.values())
    actual = Counter(nodeid.split("::", 1)[1].split("[", 1)[0] for nodeid in reports)
    assert actual == Counter(declared) == Counter(inventory["declared_cases_by_function"])
    assert dict(actual) == inventory["actual_cases_by_function"]
    verify_fact_tree(suite_terminal)
    assert b"25 passed in 300.57s" in (ROOT / "data/h01-publish-entry-profile-verification-r717/stdout.log").read_bytes()
    assert len(suite_terminal["preserved_draft_copies_verified"]) == 3
    reviews = {}
    for name in ("root_source_check", "source_review", "test_review"):
        path = ROOT / suite_terminal[name]["path"]
        reviews[name] = json.loads(path.read_bytes())
        verify_fact_tree(reviews[name])
    for name in ("source_review", "test_review"):
        assert not reviews[name]["remaining_blocking_findings"]
    first_attempt_path = ROOT / suite_terminal["recorder_first_attempt"]["path"]
    first_attempt = json.loads(first_attempt_path.read_bytes())
    # The failed active-script fact is historical. Verify its retained copy,
    # not the subsequently corrected active script against the old hash.
    verify(first_attempt["failed_script"])
    assert all(first_attempt["failed_script"][key] == first_attempt["active_script_at_failure"][key]
               for key in ("bytes", "sha256"))

    consumer = completed("h01-store-verification-r718", args.exit_code,
                         args.session, args.chunk, args.seconds,
                         already_copied=args.consumer_logs_already_copied)
    assert consumer["source_facts_verified"] == 201
    assert len({row["nodeid"] for row in consumer["phases"]}) == 1
    for row in consumer["phases"]:
        assert row["nodeid"].rsplit("::", 1)[-1] == CASE, row["nodeid"]
    assert Counter(row["phase"] for row in consumer["phases"]) == Counter({"setup": 1, "call": 1, "teardown": 1})
    outcome = "passed" if args.exit_code == 0 else "failed"
    assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
        ("setup", "passed"), ("call", outcome), ("teardown", "passed")]
    stdout = (ROOT / "data/h01-store-verification-r718/stdout.log").read_bytes()
    tail = [line.strip() for line in stdout.splitlines() if line.strip()][-1]
    assert f"1 {outcome} in {args.seconds:.2f}s".encode("ascii") in tail
    assert re.search(rb"\bin " + f"{args.seconds:.2f}".encode("ascii")
                     + rb"s(?:\s+\(\d+:\d{2}:\d{2}\))?(?:\s*=+)?$", tail), tail

    review_path = (ROOT / args.boundary_review).resolve(strict=True)
    review_path.relative_to(ROOT)
    review = json.loads(review_path.read_bytes())
    verify_fact_tree(review)
    execution = review["execution_evidence"]
    for key in ("tool_session", "terminal_chunk", "actual_exit_code", "seconds", "phases"):
        assert execution[key] == consumer[key], key
    assert execution["source_count"] == consumer["source_facts_verified"]
    assert review["plan"] == consumer["plan"] and review["claim"] == consumer["claim"]
    assert len(review["logs"]) == len(consumer["logs"]) == 5
    assert {row["path"]: row for row in review["logs"]} == {row["path"]: row for row in consumer["logs"]}
    assert review["independently_confirmed_boundary"]
    terminal = {
        "state": "publish_entry_profile_25_passed_original_store_consumer_" + outcome,
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": suite_terminal["root_source_check"],
        "source_review": suite_terminal["source_review"], "test_review": suite_terminal["test_review"],
        "review_timing": "The r717 source and test review artifacts were delivered before Root prepared and launched the suite; recorded coordinator sequence, not inferred from timestamps.",
        "implementation_scope": evidence,
        "static_findings_closed": reviews["source_review"]["static_findings_closed"],
        "preserved_draft_copies_verified": suite_terminal["preserved_draft_copies_verified"],
        "recorder_first_attempt": suite_terminal["recorder_first_attempt"],
        "preserved_failed_recorder": suite_terminal["preserved_failed_recorder"],
        "suite_terminal": fact(suite_path), "suite": suite, "case_inventory": inventory,
        "consumer": consumer, "boundary_review": fact(review_path),
        "reviewed_boundary": review,
        "failure_or_completion_boundary": review["independently_confirmed_boundary"],
        "one_next_source_question": review.get("one_next_source_question"),
        "previous_full_failure": suite_terminal["previous_full_failure"],
        "original_consumer": "test_store_stage_wiring.py::" + CASE,
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
        "suite_recorder": fact(ROOT / "scripts/record_publish_entry_profile_r717.py"),
    }
    path = ROOT / "data/h01-store-root-terminal-r718.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
                      "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
                      "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
