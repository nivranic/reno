"""Save the completed Pin-record attempts; no product imports or QA mutation."""
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
    manifest = ROOT / "data/t16-runtime-origin-graph-r681/r681-candidate-manifest.json"
    candidate = json.loads(manifest.read_text(encoding="utf-8"))
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-pin-record-verification-r683", 0, 76464, "780b46", 320.34, already_copied=True)
    assert len(suite["phases"]) == 54 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r684", 1, 33460, "88f237", 44.22)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    history = []
    for name, sha in (
        ("h01-pin-record-failed-terminal-r679.json", "4cfa1ff738de1aa910878fe3ddda25496d21fdb8d83e87811e590134d925ce90"),
        ("h01-pin-record-failed-terminal-r681.json", "0b6ba75364d0ae6234c0bb3476c395976cb59c874cfd25305d9f26c62b5d270c")):
        path = ROOT / "data" / name
        assert fact(path)["sha256"] == sha
        saved = json.loads(path.read_text(encoding="utf-8"))
        for row in [saved["manifest"], saved["source_review"], saved["suite"]["plan"], saved["suite"]["claim"]] + saved["suite"]["logs"]:
            verify(row)
        history.append(fact(path))
    assert not (ROOT / "data/h01-store-verification-r680.json").exists()
    assert not (ROOT / "data/h01-store-verification-r682.json").exists()
    terminal = {"state": "pin_record_identity_and_late_gates_passed_explicit_resume_node_pin_acquisition_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_reviews": [fact(ROOT / ("data/" + name)) for name in
            ("h01-r679-root-source-check.json", "h01-r681-root-source-check.json", "h01-r683-root-source-check.json")],
        "suite": suite, "consumer": consumer, "prior_failed_terminals_preserved": history,
        "unused_full_runners": ["r680", "r682"],
        "failure_boundary": {"original_test_line": 101,
            "stage": "explicit repeated resume; _fixed_v2_verify_node returned; stack.enter_context for node pin failed in initial full held-scope identity poll",
            "error": "OWNED_IO_NAMESPACE_DEADLINE", "first_install_returned": True,
            "explicit_resume_acquisition_returned": False, "journal_node_validation_returned": True,
            "node_pin_enter_function_started": False, "explicit_resume_returned": False,
            "shared_profile_open_prepare_put_reached": False, "performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "new_independent_review": False},
        "next_source_question": "Pin budget setter validates Pin record then held rebind validates the same Store stack again; preserve both late polls, owner/budget checks, actual binding update and all byte accounting if consolidation is justified.",
        "remaining": ["original complete Store consumer and whole append", "H01 owner SQL enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r684.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
