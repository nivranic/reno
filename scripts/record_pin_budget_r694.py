"""Preserve terminal r693/r694 evidence; file reads only, no product imports."""
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    helper = ROOT / "scripts/record_source_directory_r692.py"
    functions = runpy.run_path(str(helper))
    fact, verify, completed = (functions[name] for name in ("fact", "verify", "completed"))
    manifest = ROOT / "data/t16-runtime-origin-graph-r693/r693-candidate-manifest.json"
    candidate = json.loads(manifest.read_bytes())
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-pin-budget-verification-r693", 0, 3971, "aa6646", 348.78, already_copied=True)
    assert len(suite["phases"]) == 63 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r694", 1, 41437, "ebc674", 42.70)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    terminal = {"state": "pin_budget_21_cases_passed_repeat_resume_closing_native_second_body_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_review": fact(ROOT / "data/h01-r693-root-source-check.json"),
        "suite_terminal": fact(ROOT / "data/h01-pin-budget-suite-terminal-r693.json"),
        "file_only_recorder_dependency": fact(helper),
        "suite": suite, "consumer": consumer,
        "failure_boundary": {"original_test_line": 101,
            "stage": "explicit repeat resume normal ClosingStack exit; all closing pin verifications and closing first full body returned; native second closing body failed in a pin setter's leading original stack identity poll",
            "error": "OWNED_IO_NAMESPACE_DEADLINE", "first_install_returned": True,
            "repeat_resume_source_pins_returned": True, "repeat_resume_acquire_v2_returned": True,
            "repeat_resume_body_both_full_verifiers_returned": True,
            "repeat_resume_return_value_evaluated_before_exit": True,
            "normal_closing_leading_stack_check_returned": True,
            "normal_closing_all_pin_verifications_returned": True,
            "normal_closing_first_full_verify_body_returned": True,
            "normal_closing_native_second_verify_body_returned": False,
            "failing_pin_index_known": False,
            "failing_setter_target_budget_check_reached": False,
            "explicit_resume_returned": False, "shared_profile_open_prepare_put_reached": False,
            "performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "new_independent_review": False,
            "new_approval_rejection": False},
        "next_source_question": "ClosedStorePin.verify performs a complete pin-record/stack check, then Budget.check, then an observation with another complete stack and file identity before original verify_file rereads and hashes the file. Assess a fixed verifier consolidating only adjacent pre-read samples, preserving real read/hash, source byte delta charging, post-read gates, first errors, unknown dispatch and original cleanup. No candidate or new run yet; never remove a full verification body or closing gate.",
        "remaining": ["original complete Store consumer and whole append", "H01 owner SQL enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r694.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"],
        "unchanged_original_definitions": candidate["evidence"]["unchanged_original_definitions"]}))


if __name__ == "__main__":
    main()
