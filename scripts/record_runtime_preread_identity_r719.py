"""Preserve one terminal runtime preread suite using files and AST only.

No product import, pytest collection, runner execution or QA mutation occurs.
Case counts come from the prepared test source, not a guessed fixed count.
"""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import ast
import json
import re
import runpy

ROOT = Path(__file__).resolve().parents[1]
STEM = "h01-runtime-preread-identity-verification-r719"
TEST = ROOT / "tests/test_runtime_preread_identity_r719.py"
MANIFEST = ROOT / "data/t16-runtime-origin-graph-r719/r719-candidate-manifest.json"


def _param_count(function):
    count = 1
    for decorator in function.decorator_list:
        if (isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"):
            assert len(decorator.args) >= 2, function.name
            values = decorator.args[1]
            assert isinstance(values, (ast.List, ast.Tuple)), function.name
            assert values.elts, function.name
            count *= len(values.elts)
    return count


def _declared_cases(test_path):
    tree = ast.parse(test_path.read_bytes(), filename=str(test_path))
    functions = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith("test_")]
    assert functions
    local = {node.name: _param_count(node) for node in functions}
    assert len(local) == len(functions), "Duplicate local test function definitions"
    assert not any(isinstance(node, ast.ClassDef) and node.name.startswith("Test")
                   for node in tree.body), "Class tests need an explicit AST inventory"
    fixture_path = ROOT / "tests/test_namespace_guard_runtime.py"
    fixture_tree = ast.parse(fixture_path.read_bytes(), filename=str(fixture_path))
    source_functions = {node.name: node for node in fixture_tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    expected_imports = {
        "test_real_effects_get_distinct_plans_and_ordinary_guard_stays_full",
        "test_same_thread_copied_context_cannot_deliver_a_runtime_result",
        "test_body_failure_keeps_first_error_and_completes_remaining_tail_checks",
        "test_real_marker_change_before_body_return_is_rejected_by_full_tail",
        "test_closing_requires_new_successful_physical_observations",
        "test_failed_first_record_does_not_suppress_real_pf_or_other_records",
        "test_registered_closing_observation_keeps_scope_and_cancellation_checks",
        "test_ordinary_stack_check_inside_reading_scope_keeps_full_physical_checks",
        "test_explicit_reading_scope_rejects_another_real_owner_or_retirement",
        "test_custom_stack_check_receives_no_private_kwargs_and_preserves_error",
        "test_existing_custody_failure_precedes_custom_stack_dispatch",
        "test_runtime_writing_uses_namespace_ex_with_profile_shared_and_verifies_real_lock",
        "test_runtime_writing_rejects_a_real_namespace_shared_owner",
        "test_ordinary_write_helper_inside_reading_scope_rejects_real_bad_marker",
        "test_custom_write_helper_keeps_original_arguments_and_recorded_first_error",
    }
    imported_fixtures, imported_tests, imported = [], [], {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        assert all(alias.name != "*" for alias in node.names), "Star imports are not inventoried"
        if node.module == "tests.test_namespace_guard_runtime":
            assert node.level == 0
            for alias in node.names:
                name = alias.asname or alias.name
                assert alias.name in source_functions, alias.name
                if alias.name.startswith("test_"):
                    assert alias.name in expected_imports and name.startswith("test_"), alias.name
                    assert name not in imported and name not in local, name
                    imported[name] = _param_count(source_functions[alias.name])
                    imported_tests.append({"source_name": alias.name, "local_name": name,
                                           "cases": imported[name]})
                else:
                    assert alias.name in {"profile", "initialized"}, alias.name
                    imported_fixtures.append({"name": alias.name, "local_name": name})
        else:
            assert all(not alias.name.startswith("test_")
                       and not (alias.asname or "").startswith("test_")
                       for alias in node.names), "Only the explicitly inventoried runtime tests may be imported"
    assert {row["name"] for row in imported_fixtures} == {"profile", "initialized"}
    assert {row["source_name"] for row in imported_tests} == expected_imports
    assert len(imported_tests) == len(expected_imports) and sum(imported.values()) == 20
    return local, imported, imported_fixtures, imported_tests, fixture_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exit_code", type=int)
    parser.add_argument("session", type=int)
    parser.add_argument("chunk")
    parser.add_argument("seconds", type=float)
    parser.add_argument("--source-review", required=True)
    parser.add_argument("--test-review", required=True)
    parser.add_argument("--root-check", required=True)
    parser.add_argument("--already-copied", action="store_true")
    args = parser.parse_args()
    assert args.seconds >= 0 and args.session > 0 and args.chunk
    helper_path = ROOT / "scripts/record_source_directory_r692.py"
    helpers = runpy.run_path(str(helper_path))
    fact, verify, completed = (helpers[name] for name in ("fact", "verify", "completed"))
    preserved_drafts = {}

    def local_path(value):
        path = (ROOT / value).resolve(strict=True)
        path.relative_to(ROOT)
        return path

    def verify_expected(row, path):
        actual = fact(path)
        assert actual["sha256"] == row["sha256"], row["path"]
        if "bytes" in row:
            assert actual["bytes"] == row["bytes"], row["path"]
        return actual

    def verify_saved_draft(path):
        key = path.relative_to(ROOT).as_posix()
        if key in preserved_drafts:
            return
        draft = json.loads(path.read_bytes())
        if path.name == "original-facts.json":
            inputs = draft["inputs"]
            copy_paths = [Path(row["path"]).name for row in inputs]
        elif "facts" in draft:
            inputs = draft["facts"]
            copy_paths = [row["path"] for row in inputs]
        else:
            assert draft["state"] == "static_builder_draft_reconstructed_by_exact_inverse_delta_no_candidate_or_execution"
            assert draft["inverse_reproduces_current_bytes"] is True
            inputs = [{"path": "build-candidate.py", "bytes": draft["bytes"],
                       "sha256": draft["sha256"]}]
            copy_paths = ["build-candidate.py"]
        assert isinstance(inputs, list) and inputs, key
        assert len(set(copy_paths)) == len(copy_paths), key
        copies = []
        for row, name in zip(inputs, copy_paths):
            # Original paths describe the historical input. The retained copy
            # beside original-facts.json is its current authoritative content.
            saved = (path.parent / name).resolve(strict=True)
            saved.relative_to(path.parent)
            copies.append({"original_path": row["path"],
                           "saved_file": verify_expected(row, saved)})
        preserved_drafts[key] = {"manifest": fact(path), "provenance_state": draft.get("state"),
                                "preserved_files": copies}

    def verify_fact_tree(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                path = local_path(value["path"])
                verify_expected(value, path)
                if (path.name == "original-facts.json"
                        or (path.name == "facts.json" and path.parent.name.startswith("draft-")
                            and path.parent.parent == MANIFEST.parent)):
                    verify_saved_draft(path)
            for item in value.values():
                verify_fact_tree(item)
        elif isinstance(value, list):
            for item in value:
                verify_fact_tree(item)

    manifest = json.loads(MANIFEST.read_bytes())
    assert "frozen" in manifest["state"], manifest["state"]
    entities = manifest["source_files"] + manifest["artifacts"]
    assert entities and len({str(local_path(row["path"])) for row in entities}) == len(entities)
    for row in entities:
        verify(row)
    review_facts = {}
    for name, argument in (("source_review", args.source_review),
                           ("test_review", args.test_review),
                           ("root_source_check", args.root_check)):
        path = local_path(argument)
        review = json.loads(path.read_bytes())
        verify_fact_tree(review)
        if name in {"source_review", "test_review"}:
            finding_keys = {"remaining_findings_in_reviewed_scope", "remaining_blocking_findings"} & review.keys()
            assert finding_keys, name
            assert all(not review[key] for key in finding_keys), name
        review_facts[name] = fact(path)

    suite = completed(STEM, args.exit_code, args.session, args.chunk, args.seconds,
                      already_copied=args.already_copied)
    plan = json.loads(local_path(suite["plan"]["path"]).read_bytes())
    assert suite["source_facts_verified"] == len(plan["source_files"])
    prepared = {str(local_path(row["path"])): row for row in plan["source_files"]}
    assert len(prepared) == len(plan["source_files"])
    test_path = local_path(plan["target"])
    assert test_path == TEST.resolve(strict=True)
    local, imported, imported_fixtures, imported_tests, fixture_path = _declared_cases(test_path)
    declared = local | imported
    for path in (test_path, fixture_path.resolve(strict=True)):
        assert str(path) in prepared, str(path)
        verify_expected(prepared[str(path)], path)
    candidate_prepared = []
    for row in manifest["source_files"]:
        path = local_path(row["path"])
        # Four frozen consumer files are not selected by this local suite.
        # Every actual package module must be one of the prepared inputs.
        if path.parent.name == "authority_runtime_origin_r719":
            assert str(path) in prepared, row["path"]
            assert prepared[str(path)]["sha256"] == row["sha256"]
            assert prepared[str(path)]["bytes"] == row["bytes"]
            candidate_prepared.append(fact(path))
    assert candidate_prepared

    reports = defaultdict(list)
    for phase in suite["phases"]:
        assert phase["phase"] in {"setup", "call", "teardown"}, phase
        nodeid = phase["nodeid"]
        file_part, case_part = nodeid.split("::", 1)
        normalized = file_part.replace("\\", "/")
        target = test_path.relative_to(ROOT).as_posix()
        assert normalized in ("", target) or normalized.endswith("/" + target), nodeid
        name = case_part.split("[", 1)[0]
        assert name in declared, nodeid
        reports[nodeid].append(phase)
    actual = Counter(nodeid.split("::", 1)[1].split("[", 1)[0] for nodeid in reports)
    assert all(actual[name] <= declared[name] for name in actual)
    for nodeid, phases in reports.items():
        counts = Counter(row["phase"] for row in phases)
        assert all(count == 1 for count in counts.values()), nodeid
    call_cases = {nodeid for nodeid, phases in reports.items()
                  if any(row["phase"] == "call" for row in phases)}
    stdout = (ROOT / "data" / STEM / "stdout.log").read_bytes()
    tail_lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert tail_lines
    summary_line = tail_lines[-1]
    assert re.search(rb"\bin " + f"{args.seconds:.2f}".encode("ascii")
                     + rb"s(?:\s+\(\d+:\d{2}:\d{2}\))?(?:\s*=+)?$", summary_line), summary_line
    if args.exit_code == 0:
        assert all(row["outcome"] == "passed" for row in suite["phases"])
        assert actual == Counter(declared), (dict(actual), declared)
        assert len(call_cases) == len(reports)
        assert all(Counter(row["phase"] for row in phases)
                   == Counter({"setup": 1, "call": 1, "teardown": 1})
                   for phases in reports.values())
        assert f"{len(reports)} passed in {args.seconds:.2f}s".encode("ascii") in summary_line
    inventory = {
        "method": "Actual unique pytest nodeids and each phase matched against local functions and the explicit original runtime import list, with literal parametrization counts parsed from both prepared AST sources; no pytest collection or product import",
        "declared_local_cases": sum(local.values()), "declared_imported_cases": sum(imported.values()),
        "declared_total_cases": sum(declared.values()),
        "actual_reported_cases": len(reports), "actual_call_cases": len(call_cases),
        "actual_local_cases": sum(count for name, count in actual.items() if name in local),
        "actual_imported_cases": sum(count for name, count in actual.items() if name in imported),
        "declared_cases_by_function": declared, "actual_cases_by_function": dict(actual),
        "missing_or_extra_case_counts": {
            name: {"declared": declared.get(name, 0), "actual": actual.get(name, 0)}
            for name in sorted(set(declared) | set(actual))
            if declared.get(name, 0) != actual.get(name, 0)},
        "test": fact(test_path), "imported_fixture_module": fact(fixture_path),
        "imported_fixtures": imported_fixtures, "imported_tests": imported_tests,
        "pytest_stdout_summary_checked_as_ascii_bytes": True,
    }
    terminal = {
        "state": "runtime_preread_identity_suite_passed" if args.exit_code == 0 else "runtime_preread_identity_suite_failed",
        "goal": "按照方案推进执行", "goal_status": "active", "goal_complete": False,
        "candidate_manifest": fact(MANIFEST), "candidate_entities_verified": len(entities),
        "candidate_source_count": len(manifest["source_files"]),
        "candidate_artifact_count": len(manifest["artifacts"]),
        "candidate_package_inputs_verified": candidate_prepared,
        **review_facts, "review_fact_trees_verified": True,
        "preserved_draft_copies_verified": list(preserved_drafts.values()),
        "review_timing": "This file-only recorder does not infer review-before-execution timing; retained review artifacts and explicitly recorded coordinator sequence remain authoritative",
        "suite": suite, "case_inventory": inventory,
        "previous_full_failure": fact(ROOT / "data/h01-store-root-terminal-r718.json"),
        "recorder_dependency": fact(helper_path),
        "coverage_limits": {
            "full_store_consumer_executed_by_this_suite": False,
            "stable_performance_proved": False,
            "profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False,
            "acl_or_codex_settings_changed": False, "commit_or_push": False,
            "recorder_product_import_or_execution": False,
        },
    }
    path = ROOT / "data/h01-runtime-preread-identity-suite-terminal-r719.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "exit_code": args.exit_code,
                      "phases": len(suite["phases"]), "actual_cases": len(reports),
                      "declared_cases": sum(declared.values()),
                      "preserved_drafts": len(preserved_drafts)}))


if __name__ == "__main__":
    main()
