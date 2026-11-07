"""Save the completed r675/r676 attempts; no product imports or QA mutation."""
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
    manifest = ROOT / "data/t16-runtime-origin-graph-r675/r675-candidate-manifest.json"
    candidate = json.loads(manifest.read_text(encoding="utf-8"))
    for row in candidate["source_files"] + candidate["artifacts"]:
        verify(row)
    suite = completed("h01-pin-observation-verification-r675", 0, 5709, "b282d0", 138.57, already_copied=True)
    assert len(suite["phases"]) == 27 and all(p["outcome"] == "passed" for p in suite["phases"])
    consumer = completed("h01-store-verification-r676", 1, 55690, "b53533", 48.27)
    assert [(p["phase"], p["outcome"]) for p in consumer["phases"]] == [
        ("setup", "passed"), ("call", "failed"), ("teardown", "passed")]
    terminal = {"state": "pin_observation_suite_passed_full_consumer_initial_publication_deadline_failed",
        "goal": "按照方案推进执行", "goal_complete": False,
        "candidate_manifest": fact(manifest), "candidate_entities_verified": 25,
        "source_review": fact(ROOT / "data/h01-r675-root-source-check.json"),
        "suite": suite, "consumer": consumer,
        "preparation_failure": {"kind": "static_builder_Assign_missing_name", "product_execution": False,
            "original_builder": fact(ROOT / "data/h01-pin-test-before-r675/build-pin-observation-failed.py"),
            "repair": "Compare every class member using its name or AST kind; preserve __slots__ comparison; finish existing mutable draft only"},
        "failure_boundary": {
            "original_test_line": 97,
            "stage": "first install; init-reservation temporary read custody; _identities -> Store directory lookup -> Store stack held identity poll",
            "error": "OWNED_IO_NAMESPACE_DEADLINE", "cleanup_note": "STAGE_ORIGINAL_CLEANUP_RETAINED",
            "temporary_writer_returned": True, "read_custody_returned": False,
            "reservation_publication_returned": False, "first_install_returned": False,
            "explicit_resume_reached": False, "shared_profile_open_prepare_put_reached": False,
            "performance_or_hotspot_inferred": False},
        "boundaries": {"profiling": False, "deadline_changed": False,
            "production_data_service_dist_changed": False, "acl_or_codex_settings_changed": False,
            "full_qa_census": False, "commit_or_push": False, "new_independent_review": False},
        "next_source_question": "Stage _stage_directory_verify_original calls Store _directory_record_original, which already performs NS closed-directory verification, then repeats it. Fixed dispatch, returned real record binding, original error order and complete effect gates require review before any consolidation.",
        "remaining": ["original complete Store consumer and whole append", "H01 owner SQL enrollment activation live witness writers",
            "H02-H06 and permanent share revocation", "P06 SourceMap work and exact released16 missing sources/resources",
            "T15 real material/quote/change inputs and target production cloud", "normal H06 deployment"]}
    path = ROOT / "data/h01-store-root-terminal-r676.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(terminal, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"terminal": fact(path), "consumer_phases": consumer["phases"]}))


if __name__ == "__main__":
    main()
