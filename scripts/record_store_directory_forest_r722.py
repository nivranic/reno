"""File-only terminal recorder scaffold for the r721 suite and r722 consumer.

Final manifest and suite hashes are explicit inputs after those artifacts exist.
This script never imports product modules or runs a test/runner.
"""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
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
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--suite-sha256", required=True)
    parser.add_argument("--boundary-review", required=True)
    parser.add_argument("--consumer-logs-already-copied", action="store_true")
    args = parser.parse_args()
    assert args.session > 0 and args.chunk and args.seconds >= 0
    assert all(re.fullmatch(r"[0-9a-f]{64}", value)
               for value in (args.manifest_sha256, args.suite_sha256))
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

    manifest = ROOT / "data/t16-runtime-origin-graph-r721/r721-candidate-manifest.json"
    assert fact(manifest)["sha256"] == args.manifest_sha256
    candidate = json.loads(manifest.read_bytes())
    assert "frozen" in candidate["state"]
    entities = candidate["source_files"] + candidate["artifacts"]
    assert len({row["path"] for row in entities}) == len(entities) and entities
    for row in entities:
        verify(row)

    suite_path = ROOT / "data/h01-store-directory-forest-suite-terminal-r721.json"
    assert fact(suite_path)["sha256"] == args.suite_sha256
    suite_terminal = json.loads(suite_path.read_bytes())
    assert suite_terminal["state"] == "store_directory_forest_suite_passed"
    assert suite_terminal["candidate_manifest"] == fact(manifest)
    prior_suite = suite_terminal["suite"]
    assert prior_suite["actual_exit_code"] == 0
    suite = completed("h01-store-directory-forest-verification-r721", 0,
                      prior_suite["tool_session"], prior_suite["terminal_chunk"],
                      prior_suite["seconds"], already_copied=True)
    assert suite == prior_suite
    verify_fact_tree(suite_terminal)
    inventory = suite_terminal["case_inventory"]
    assert not inventory["missing_or_extra_case_counts"]
    suite_recorder = ROOT / "scripts/record_store_directory_forest_r721.py"
    # This retained helper parses two source files as AST. It performs no
    # product/test imports or collection; runpy does not enter its main().
    inventory_helpers = runpy.run_path(str(suite_recorder))
    local, fixtures, fixture_path = inventory_helpers["_declared_cases"](
        ROOT / inventory["test"]["path"])
    declared = local
    count = sum(declared.values())
    assert inventory["declared_local_cases"] == inventory["actual_local_cases"] == sum(local.values())
    assert inventory["declared_imported_cases"] == inventory["actual_imported_cases"] == 0
    assert inventory["declared_total_cases"] == count
    assert inventory["imported_fixtures"] == fixtures
    assert inventory["imported_fixture_module"] == fact(fixture_path)
    reports = defaultdict(list)
    for phase in suite["phases"]:
        reports[phase["nodeid"]].append(phase)
    actual = Counter(nodeid.split("::", 1)[1].split("[", 1)[0] for nodeid in reports)
    assert actual == Counter(declared) == Counter(inventory["declared_cases_by_function"])
    assert dict(actual) == inventory["actual_cases_by_function"]
    assert len(reports) == inventory["actual_reported_cases"] == inventory["actual_call_cases"] == count
    assert len(suite["phases"]) == count * 3 and all(row["outcome"] == "passed" for row in suite["phases"])
    assert all(Counter(row["phase"] for row in rows) == Counter({"setup": 1, "call": 1, "teardown": 1})
               for rows in reports.values())
    expected_summary = f"{count} passed in {suite['seconds']:.2f}s".encode("ascii")
    assert expected_summary in (ROOT / "data/h01-store-directory-forest-verification-r721/stdout.log").read_bytes()
    for name in ("root_source_check", "source_review", "test_review"):
        review = json.loads((ROOT / suite_terminal[name]["path"]).read_bytes())
        verify_fact_tree(review)
        if name in {"source_review", "test_review"}:
            keys = {"remaining_findings_in_reviewed_scope", "remaining_blocking_findings"} & review.keys()
            assert keys and all(not review[key] for key in keys), name

    consumer = completed("h01-store-verification-r722", args.exit_code,
                         args.session, args.chunk, args.seconds,
                         already_copied=args.consumer_logs_already_copied)
    assert consumer["source_facts_verified"] == 201
    assert len({row["nodeid"] for row in consumer["phases"]}) == 1
    assert all(row["nodeid"].rsplit("::", 1)[-1] == CASE for row in consumer["phases"])
    outcome = "passed" if args.exit_code == 0 else "failed"
    assert [(row["phase"], row["outcome"]) for row in consumer["phases"]] == [
        ("setup", "passed"), ("call", outcome), ("teardown", "passed")]
    stdout = (ROOT / "data/h01-store-verification-r722/stdout.log").read_bytes()
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
        "state": f"store_directory_forest_{count}_passed_original_store_consumer_{outcome}",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": len(entities),
        "root_source_check": suite_terminal["root_source_check"],
        "source_review": suite_terminal["source_review"], "test_review": suite_terminal["test_review"],
        "review_timing": "No before-execution timing is inferred by this file-only recorder; retained evidence and explicitly recorded coordinator order remain authoritative.",
        "implementation_scope": candidate["evidence"],
        "preserved_draft_copies_verified": suite_terminal["preserved_draft_copies_verified"],
        "suite_terminal": fact(suite_path), "suite": suite, "case_inventory": inventory,
        "consumer": consumer, "boundary_review": fact(review_path), "reviewed_boundary": review,
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
        "recorder_dependency": fact(helper), "suite_recorder": fact(suite_recorder),
    }
    path = ROOT / "data/h01-store-root-terminal-r722.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "candidate_entities": len(entities),
                      "suite_phases": len(suite["phases"]), "consumer_logs": len(consumer["logs"]),
                      "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
