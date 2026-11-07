"""Preserve one terminal runtime-tail suite using files only; never import product code."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import ast
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def _param_count(function):
    count = 1
    for decorator in function.decorator_list:
        if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"):
            assert len(decorator.args) >= 2
            values = decorator.args[1]
            assert isinstance(values, (ast.List, ast.Tuple)), function.name
            count *= len(values.elts)
    return count


def _declared_cases(test_path):
    tree = ast.parse(test_path.read_bytes(), filename=str(test_path))
    local = {node.name: _param_count(node) for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    imported = {}
    fixture_path = ROOT / "tests/test_namespace_guard_runtime.py"
    fixture_tree = ast.parse(fixture_path.read_bytes(), filename=str(fixture_path))
    fixture_cases = {node.name: _param_count(node) for node in fixture_tree.body
                     if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "tests.test_namespace_guard_runtime":
            for alias in node.names:
                if alias.name.startswith("test_"):
                    assert alias.name in fixture_cases
                    name = alias.asname or alias.name
                    assert name not in imported and name not in local
                    imported[name] = fixture_cases[alias.name]
    assert local and imported
    return local, imported, fixture_path


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
    helper_path = ROOT / "scripts/record_source_directory_r692.py"
    helpers = runpy.run_path(str(helper_path))
    fact, verify, completed = (helpers[name] for name in ("fact", "verify", "completed"))

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

    manifest_path = ROOT / "data/t16-runtime-origin-graph-r715/r715-candidate-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    entities = manifest["source_files"] + manifest["artifacts"]
    for row in entities:
        verify(row)
    review_facts = {}
    for name, argument in (("source_review", args.source_review),
                           ("test_review", args.test_review),
                           ("root_source_check", args.root_check)):
        path = (ROOT / argument).resolve(strict=True)
        path.relative_to(ROOT)
        verify_fact_tree(json.loads(path.read_bytes()))
        review_facts[name] = fact(path)

    suite = completed("h01-runtime-tail-identity-verification-r715", args.exit_code,
                      args.session, args.chunk, args.seconds, already_copied=args.already_copied)
    plan = json.loads((ROOT / suite["plan"]["path"]).read_bytes())
    assert suite["source_facts_verified"] == len(plan["source_files"])
    test_path = (ROOT / plan["target"]).resolve(strict=True)
    assert test_path == (ROOT / "tests/test_runtime_tail_identity_r715.py").resolve()
    local, imported, fixture_path = _declared_cases(test_path)
    declared = local | imported
    reports = defaultdict(list)
    for phase in suite["phases"]:
        reports[phase["nodeid"]].append(phase)

    def test_name(nodeid):
        return nodeid.rsplit("::", 1)[-1].split("[", 1)[0]

    actual = Counter(test_name(nodeid) for nodeid in reports)
    call_cases = {nodeid for nodeid, phases in reports.items()
                  if any(row["phase"] == "call" for row in phases)}
    if args.exit_code == 0:
        assert all(row["outcome"] == "passed" for row in suite["phases"])
        assert actual == Counter(declared), (dict(actual), declared)
        assert len(call_cases) == len(reports)
        assert all(Counter(row["phase"] for row in phases) == Counter({"setup": 1, "call": 1, "teardown": 1})
                   for phases in reports.values())
    inventory = {
        "method": "Unique actual pytest nodeids matched against local and explicitly imported function names and literal parametrizations parsed from the prepared source; no pytest collection or imports performed by recorder",
        "declared_local_cases": sum(local.values()),
        "declared_imported_cases": sum(imported.values()),
        "declared_total_cases": sum(declared.values()),
        "actual_reported_cases": len(reports), "actual_call_cases": len(call_cases),
        "actual_local_cases": sum(count for name, count in actual.items() if name in local),
        "actual_imported_cases": sum(count for name, count in actual.items() if name in imported),
        "declared_cases_by_function": declared, "actual_cases_by_function": dict(actual),
        "missing_or_extra_case_counts": {name: {"declared": declared.get(name, 0), "actual": actual.get(name, 0)}
            for name in sorted(set(declared) | set(actual)) if declared.get(name, 0) != actual.get(name, 0)},
        "test": fact(test_path), "imported_fixture_module": fact(fixture_path),
    }
    terminal = {
        "state": "runtime_tail_identity_suite_passed" if args.exit_code == 0 else "runtime_tail_identity_suite_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest_path), "candidate_entities_verified": len(entities),
        "candidate_source_count": len(manifest["source_files"]),
        "candidate_artifact_count": len(manifest["artifacts"]),
        **review_facts, "review_fact_trees_verified": True,
        "review_timing": "No review-before-execution timing is inferred from this recorder; retained review artifacts and any explicit timing correction remain authoritative",
        "suite": suite, "case_inventory": inventory,
        "previous_full_failure": fact(ROOT / "data/h01-store-root-terminal-r714.json"),
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
    path = ROOT / "data/h01-runtime-tail-identity-suite-terminal-r715.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "exit_code": args.exit_code,
        "phases": len(suite["phases"]), "actual_cases": len(reports),
        "actual_local_cases": inventory["actual_local_cases"],
        "actual_imported_cases": inventory["actual_imported_cases"]}))


if __name__ == "__main__":
    main()
