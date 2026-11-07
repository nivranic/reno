"""Save the completed immutable-file attempts; no product imports or QA mutation."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def verify(row):
    actual = fact(ROOT / row["path"])
    assert actual["bytes"] == row["bytes"] and actual["sha256"] == row["sha256"]


def completed(stem, expected_exit, session, chunk, seconds, *, already_copied=False):
    plan = ROOT / "data" / (stem + ".json")
    claim = plan.with_name(stem + "-started.json")
    selected = json.loads(plan.read_text(encoding="utf-8"))
    for row in selected["source_files"]:
        verify(row)
    reserved = json.loads(claim.read_text(encoding="utf-8"))
    assert reserved["plan_sha256"] == fact(plan)["sha256"]
    qa = Path(reserved["root"])
    saved = plan.with_suffix("")
    if not already_copied:
        saved.mkdir()
    logs = []
    for name in ("stdout.log", "stderr.log", "pytest.log", "pytest-result.json", "execution-result.json"):
        raw = (qa / name).read_bytes()
        path = saved / name
        if not already_copied:
            with path.open("xb") as stream:
                stream.write(raw)
        assert path.read_bytes() == raw
        logs.append(fact(path))
    execution = json.loads((saved / "execution-result.json").read_text(encoding="utf-8"))
    result = json.loads((saved / "pytest-result.json").read_text(encoding="utf-8"))
    assert execution["child_started"] and execution["child_exit_code"] == expected_exit
    assert execution["prepared_sources_unchanged"] and execution["profiling_enabled"] is False
    assert result["pytest_exit_code"] == expected_exit and result["profiling_enabled"] is False
    return {"tool_session": session, "terminal_chunk": chunk, "actual_exit_code": expected_exit,
            "seconds": seconds, "source_facts_verified": len(selected["source_files"]),
            "sources_unchanged": True, "plan": fact(plan), "claim": fact(claim),
            "qa": str(qa), "logs": logs, "phases": result["phases"]}


def main():
    manifest = ROOT / "data/t16-runtime-origin-graph-r687/r687-candidate-manifest.json"
    candidate = json.loads(manifest.read_text(encoding="utf-8"))
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-immutable-pin-verification-r689", 0, 45384, "571b27", 344.49, already_copied=True)
    assert len(suite["phases"]) == 60 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r690", 1, 7922, "e60cb1", 44.13)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    prior = ROOT / "data/h01-immutable-pin-failed-terminal-r687.json"
    assert fact(prior)["sha256"] == "1754a98d3a86552deae598fbfb55eaf8302ca279bbffe8568addecebe2ac90ea"
    saved = json.loads(prior.read_text(encoding="utf-8"))
    for row in [saved["manifest"], saved["source_review"], saved["suite"]["plan"], saved["suite"]["claim"]] + saved["suite"]["logs"]:
        verify(row)
    assert not (ROOT / "data/h01-store-verification-r688.json").exists()
    terminal = {"state": "immutable_file_observation_passed_explicit_resume_source_directory_final_native_time_gate_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_reviews": [fact(ROOT / ("data/" + name)) for name in
            ("h01-r687-root-source-check.json", "h01-r689-root-source-check.json")],
        "suite": suite, "consumer": consumer, "prior_failed_terminal_preserved": fact(prior),
        "unused_full_runner": "r688",
        "failure_boundary": {"original_test_line": 101,
            "stage": "explicit repeat resume; source pin directory acquisition completed native opens/identity check; final budget.check failed at Store native deadline gate before directory return",
            "error": "PERSONAL_STORE_TIME_LIMIT", "first_install_returned": True,
            "repeat_install_profile_requirement_returned": True,
            "repeat_resume_closing_stack_created": True,
            "current_source_directory_native_open_and_identity_returned": True,
            "current_source_directory_acquisition_returned": False,
            "current_source_filehold_acquisition_started": False, "source_module_index_known": False,
            "which_native_time_comparison_failed_known": False,
            "explicit_resume_acquisition_started": False, "explicit_resume_returned": False,
            "shared_profile_open_prepare_put_reached": False, "performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "new_independent_review": False,
            "origin_observer_sql_or_activation_verified": False},
        "next_source_question": "The three fixed source modules have the same package parent, but source acquisition opens three independent original directories. Assess sharing one genuinely acquired directory inside one source_pins batch while preserving each real file lease/read/SHA, budgets, owner binding and original close; path equality alone is not authority.",
        "remaining": ["original complete Store consumer and whole append", "H01 owner SQL enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r690.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
