"""Single-use v2 verification of the frozen r729 strict-zip chain source graph.

Prepare and run exactly one explicitly named suite. Every suite owns a fresh
TEMP root and its original pytest fixtures; no suite starts another suite.
Original namespace 30-second gates remain unchanged. No profiling is enabled.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data/t16-runtime-graph-cpu-r729"
PACKAGE = GRAPH / "authority_runtime_graph_cpu_r729"
MANIFEST = GRAPH / "r729-candidate-manifest.json"
MANIFEST_SHA = "7965e1b9d8e4b3a3336e88ec6545f4448ad6cb208f16e653cb81310b01f71aeb"
NAMESPACE_DEADLINE_SECONDS = 30.0
EXTRA_INPUTS = (
    Path(__file__).resolve(),
    ROOT / "tests/__init__.py",
    ROOT / "tests/conftest.py",
    ROOT / "tests/schema_fixtures.py",
)
# Exact static resources already pinned by the original r722 source plan.
# Only these bytes are admitted; no directory or workspace-wide read allow.
STATIC_ASSET_INPUTS = [
    {
        "path": "reno/dict/param_dictionary.yaml",
        "byte_size": 1283,
        "sha256": "90129a1b3f6e579aea47cf972c8df0f26f66a0a6e02919ffe2875c79b4bbaec5"
    },
    {
        "path": "reno/dict/synonyms.yaml",
        "byte_size": 476,
        "sha256": "f7a16bc9fa9934af0b71dee94395fabc119fb9874d09b01c97377dbccdcefed8"
    },
    {
        "path": "reno/dict/taxonomy.yaml",
        "byte_size": 2759,
        "sha256": "4cf0ebb1137f8abe1b106595620acce769fbdcf7d9ebe59eba89e9b2b9bd1e03"
    },
    {
        "path": "reno/prompts/atomize_v1.txt",
        "byte_size": 2272,
        "sha256": "2a21b1129d56e77e8d94b0929eda540e15df55e510c43c317cba638fc690f19c"
    },
    {
        "path": "reno/prompts/atomize_v2.txt",
        "byte_size": 4379,
        "sha256": "b1ebe299805ed1ec6548942e92b25adf1f61e87d92cc9d8fa77cf152f313cbb3"
    },
    {
        "path": "reno/prompts/judge_v1.txt",
        "byte_size": 1509,
        "sha256": "cf853da3a995ceb94289227cd3809b80b449df6ce66f496d404e723a4affe62c"
    },
    {
        "path": "reno/prompts/judge_v2.txt",
        "byte_size": 1596,
        "sha256": "aea7fb06a99a534e5c213e6d1b59f8d59d784b7f4cf958df4a6c983119eb5d1e"
    },
    {
        "path": "reno/schema/schema.sql",
        "byte_size": 9719,
        "sha256": "de5930eb4d96e6921c73e97cad6d11feab759479e016003c8469d2252b49f7a9"
    }
]
SUITES = {
    "focused": (("test_runtime_graph_cpu.py", None),),
    "store": (("test_store_stage_wiring.py",
        "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"),),
}


def need(value, message):
    if not value:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": digest(raw)}


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def named_path(relative):
    need(type(relative) is str, "Manifest path must be a string")
    name = PurePosixPath(relative)
    need(relative == name.as_posix() and not name.is_absolute() and name.parts
         and all(part not in {".", ".."} and ":" not in part and "\\" not in part for part in name.parts),
         "Manifest input must be an exact workspace-relative path")
    path = ROOT.joinpath(*name.parts)
    need(path.resolve(strict=True).is_relative_to(ROOT.resolve()), "Manifest input escaped workspace")
    return path


def source_snapshot():
    # Read only the exact named inputs in the frozen manifest. Never enumerate
    # DATA, old QA directories, or substitute an earlier runtime graph.
    raw = MANIFEST.read_bytes()
    need(digest(raw) == MANIFEST_SHA, "Frozen r729 candidate manifest changed")
    document = json.loads(raw)
    before = document["original_inputs_before"]
    copied = document["source_files"]
    need(before == document["original_inputs_after"], "Candidate construction changed original inputs")
    need(len(copied) == 31 and len(before) == document["original_input_count"], "Frozen named source counts changed")
    need(document["selectors_executed"] == [], "Candidate manifest contains an executed selector")
    expected = {}
    for rows in (before, copied, [document["builder"], document["source_evidence"], document["design"],
        document["formatter"], document["static_draft_preservation"]], STATIC_ASSET_INPUTS):
        need(type(rows) is list, "Frozen source rows must be a list")
        for row in rows:
            need(type(row) is dict and set(row) == {"path", "byte_size", "sha256"},
                 "Frozen source fact has an unexpected shape")
            path = named_path(row["path"])
            if row["path"] in expected:
                need(expected[row["path"]] == row, "Duplicate named source facts disagree")
            expected[row["path"]] = row
            need(fact(path) == row, "Frozen source changed: " + row["path"])
    package_prefix = PACKAGE.relative_to(ROOT).as_posix() + "/"
    need(sum(row["path"].startswith(package_prefix) for row in copied) == 22,
         "The frozen r729 package must retain all 22 modules")
    expected_tests = set(document["copied_test_files"]) | {document["focused_test_file"]}
    need({row["path"] for row in copied if not row["path"].startswith(package_prefix)} == expected_tests,
         "The frozen r729 graph must retain all eight original test files plus the focused chain contract file")
    for path in (MANIFEST, *EXTRA_INPUTS):
        row = fact(path)
        if row["path"] in expected:
            need(expected[row["path"]] == row, "Additional required input disagrees with frozen source")
        expected[row["path"]] = row
    return [expected[name] for name in sorted(expected)]


def plan_path(suite):
    return GRAPH / ("r729-verification-" + suite + ".json")


def claim_path(suite):
    return GRAPH / ("r729-verification-" + suite + "-started.json")


def targets(suite):
    return [str(GRAPH / name) + ("::" + case if case is not None else "")
            for name, case in SUITES[suite]]


def relative_targets(suite):
    return [(GRAPH / name).relative_to(ROOT).as_posix() + ("::" + case if case is not None else "")
            for name, case in SUITES[suite]]


def check_selection(suite, sources):
    for row in sources:
        path = ROOT / row["path"]
        if path.suffix == ".py":
            ast.parse(path.read_bytes(), filename=row["path"])
    namespace = ast.parse((PACKAGE / "personal_authority_namespace.py").read_bytes())
    deadlines = [node.value.value for node in namespace.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and any(isinstance(target, ast.Name) and target.id == "DEADLINE_SECONDS" for target in node.targets)]
    need(deadlines == [NAMESPACE_DEADLINE_SECONDS], "Original namespace 30-second deadline changed")
    for name, case in SUITES[suite]:
        tree = ast.parse((GRAPH / name).read_bytes())
        if case is not None:
            need(sum(isinstance(node, ast.FunctionDef) and node.name == case for node in tree.body) == 1,
                 "Original test selector no longer matches")
        else:
            need(any(isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in tree.body),
                 "Original finite test file has no test definitions")


def prepare(suite):
    sources = source_snapshot()
    check_selection(suite, sources)
    need(source_snapshot() == sources, "Source inputs changed during preparation")
    plan = {"state": "prepared_not_executed", "suite": suite, "approval_required": False,
        "targets": relative_targets(suite), "source_files": sources,
        "candidate_manifest_sha256": MANIFEST_SHA,
        "namespace_deadline_seconds": NAMESPACE_DEADLINE_SECONDS,
        "expected_finite_test_count": None,
        "profiling_enabled": False, "automatic_downstream_execution": False,
        "values_recorded": "pytest nodeid, phase, outcome and duration; no arguments or locals",
        "limits": "New TEMP only; original fixtures and checks; no profiling, retries, deadline changes, native recovery, alias diagnosis, existing ACL changes, production data or service operations",
        "interpretation": "This explicitly selected isolated consumer only; no claim that the full H01 goal or enrollment/live writers passed"}
    write_new(plan_path(suite), plan)
    print(json.dumps({"state": plan["state"], "suite": suite,
        "plan": str(plan_path(suite)), "sources": len(sources)}, sort_keys=True))


def verify_plan(suite):
    plan = json.loads(plan_path(suite).read_bytes())
    need(plan["state"] == "prepared_not_executed" and plan["suite"] == suite
         and plan["targets"] == relative_targets(suite)
         and plan["candidate_manifest_sha256"] == MANIFEST_SHA
         and plan["namespace_deadline_seconds"] == NAMESPACE_DEADLINE_SECONDS
         and plan["profiling_enabled"] is False and plan["automatic_downstream_execution"] is False,
         "Prepared selection or limits changed")
    need(source_snapshot() == plan["source_files"], "Prepared sources changed; refuse a stale verification")
    return plan


def lexical(path):
    if path is None:
        path = os.curdir
    text = os.fsdecode(path)
    if text.startswith("file:"):
        uri = urlsplit(text)
        need(uri.netloc in ("", "localhost"), "VERIFICATION_REMOTE_FILE_URI_REFUSED")
        text = unquote(uri.path)
        if os.name == "nt" and len(text) > 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(text))


def inside(path, root):
    return path == root or path.startswith(root + os.sep)


def child(suite, qa):
    plan = verify_plan(suite)
    qa = qa.resolve(strict=True)
    prefix = "reno-r729-" + suite + "-"
    need(qa.name.startswith(prefix) and qa.parent == Path(tempfile.gettempdir()).resolve(),
         "Expected a fresh suite-specific TEMP root")
    claim = json.loads(claim_path(suite).read_bytes())
    need(claim["suite"] == suite and Path(claim["root"]).resolve() == qa
         and claim["plan_sha256"] == digest(plan_path(suite).read_bytes()),
         "Child root must match its single-use claim")
    need(not (qa / "fixtures").exists(), "Refuse an existing pytest basetemp; retain the prior attempt")
    write_new(qa / "child-started.json", {"state": "single_child_reserved", "suite": suite})
    qa_key = lexical(qa)
    source_keys = {lexical(ROOT / row["path"]) for row in plan["source_files"]}
    import_roots = [lexical(Path(sys.prefix)), lexical(Path(sys.base_prefix))]
    source_dirs = {lexical((ROOT / row["path"]).parent) for row in plan["source_files"]}
    source_dirs.update(map(lexical, [ROOT, GRAPH, PACKAGE, ROOT / "reno", ROOT / "tests"]))

    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            raise RuntimeError("VERIFICATION_EXTERNAL_IO_REFUSED")
        if event == "sqlite3.connect":
            name = os.fspath(args[0])
            if name != ":memory:" and not inside(lexical(name), qa_key):
                raise RuntimeError("VERIFICATION_DATABASE_OUTSIDE_QA")
        if event == "open" and not isinstance(args[0], int):
            path = lexical(args[0])
            mode, flags = args[1:3]
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            writing = writing or isinstance(mode, str) and any(mark in mode for mark in "wax+")
            if writing and not inside(path, qa_key):
                raise RuntimeError("VERIFICATION_WRITE_OUTSIDE_QA")
            if not writing and not (inside(path, qa_key) or path in source_keys
                    or any(inside(path, base) for base in import_roots)):
                raise RuntimeError("VERIFICATION_UNSELECTED_FILE_READ")
        if event in {"os.listdir", "os.scandir"} and args and not isinstance(args[0], int):
            path = lexical(args[0])
            if not (inside(path, qa_key) or path in source_dirs
                    or any(inside(path, base) for base in import_roots)):
                raise RuntimeError("VERIFICATION_UNSELECTED_DIRECTORY_READ")
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"}:
            if not inside(lexical(args[0]), qa_key):
                raise RuntimeError("VERIFICATION_MUTATION_OUTSIDE_QA")
        if event in {"os.rename", "os.link", "os.symlink"}:
            if any(not inside(lexical(path), qa_key) for path in args[:2]):
                raise RuntimeError("VERIFICATION_MUTATION_OUTSIDE_QA")

    # Relocate before all product/pytest imports. The original tests.conftest
    # remains responsible for fixture setup and teardown, including fresh DBs.
    local = qa / "config.test.json"
    write_new(local, {"auth_token": "", "sync_token": "", "zhipu_api_key": ""})
    os.environ.update(RENO_QA_ROOT=str(qa), RENO_CONFIG_FILE=str(local),
        RENO_DATA_DIR=str(qa / "data"), RENO_LLM_TESTS="0", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(qa / "unused-bytecode-cache")
    sys.path[:0] = [str(ROOT), str(GRAPH)]
    sys.addaudithook(audit)
    import pytest

    class Reports:
        def __init__(self):
            self.phases = []
            self.collections = []

        def pytest_runtest_logreport(self, report):
            self.phases.append({"nodeid": report.nodeid, "phase": report.when,
                "outcome": report.outcome, "duration": report.duration})

        def pytest_collectreport(self, report):
            self.collections.append({"nodeid": report.nodeid, "outcome": report.outcome})

    reports = Reports()
    code = int(pytest.main(["-q", "--tb=short", "--capture=sys", "--log-file", str(qa / "pytest.log"),
        "-p", "tests.conftest", "-p", "no:cacheprovider", "-c", str(qa / "pytest.ini"),
        "--confcutdir", str(GRAPH), "--basetemp", str(qa / "fixtures"), *targets(suite)], plugins=[reports]))
    write_new(qa / "pytest-result.json", {"suite": suite, "targets": relative_targets(suite),
        "pytest_exit_code": code, "profiling_enabled": False,
        "phases": reports.phases, "collections": reports.collections})
    return code


def run(suite):
    verify_plan(suite)
    # Reserve even a failed launch. A new attempt requires a separately reviewed
    # candidate/plan; never delete this claim or restart a failed suite.
    with claim_path(suite).open("x", encoding="utf-8", newline="\n") as claim:
        qa = Path(tempfile.mkdtemp(prefix="reno-r729-" + suite + "-"))
        json.dump({"state": "one_attempt_reserved", "suite": suite, "root": str(qa),
            "plan_sha256": digest(plan_path(suite).read_bytes())}, claim, indent=2, sort_keys=True)
        claim.write("\n")
    (qa / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RENO_", "PYTEST_", "PYTHON"))}
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_child", suite, str(qa)]
    print(json.dumps({"state": "starting_one_verification", "suite": suite, "root": str(qa)}, sort_keys=True),
          flush=True)
    with (qa / "stdout.log").open("xb") as out, (qa / "stderr.log").open("xb") as err:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            write_new(qa / "execution-result.json", {"suite": suite, "child_started": False,
                "launch_error_type": type(error).__name__, "profiling_enabled": False,
                "pytest_result_written": False})
            raise
        # No subprocess timeout, automatic retry, extra sample or cleanup.
        # Callers follow this same live tool handle until it terminates.
        code = process.wait()
    unchanged = False
    try:
        verify_plan(suite)
        unchanged = True
    finally:
        write_new(qa / "execution-result.json", {"suite": suite, "child_started": True,
            "child_exit_code": code, "prepared_sources_unchanged": unchanged,
            "profiling_enabled": False, "pytest_result_written": (qa / "pytest-result.json").is_file()})
    print(json.dumps({"suite": suite, "root": str(qa), "child_exit_code": code,
        "sources_unchanged": unchanged}, sort_keys=True))
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "_child"))
    parser.add_argument("suite", choices=tuple(SUITES))
    parser.add_argument("qa", nargs="?")
    options = parser.parse_args()
    if options.mode != "_child" and options.qa is not None:
        parser.error("Only _child accepts a TEMP root")
    if options.mode == "prepare":
        prepare(options.suite)
        return 0
    if options.mode == "run":
        return run(options.suite)
    if not options.qa:
        parser.error("_child requires its single-use suite TEMP root")
    return child(options.suite, Path(options.qa))


if __name__ == "__main__":
    raise SystemExit(main())
