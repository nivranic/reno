"""Verify the original isolated H01 initialization consumer without profiling.

Uses a freshly pinned source plan, new TEMP and the original pytest fixture,
assertions and 30-second gate. No cProfile or function timing is enabled.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data/t16-runtime-origin-graph-r668"
PACKAGE = GRAPH / "authority_runtime_origin_r668"
TEST = GRAPH / "test_store_stage_wiring.py"
CASE = "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"
PLAN = ROOT / "data/h01-store-verification-r669.json"


def fact(path):
    raw = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def source_paths():
    # Explicit source roots only: never enumerate mutable DATA or old QA trees.
    return sorted({*PACKAGE.glob("*.py"), *ROOT.joinpath("reno").rglob("*.py"),
                   *ROOT.joinpath("reno/prompts").glob("*.txt"),
                   *ROOT.joinpath("reno/dict").glob("*.yaml"),
                   ROOT / "reno/schema/schema.sql",
                   TEST, ROOT / "tests/__init__.py", ROOT / "tests/conftest.py", Path(__file__).resolve()})


def prepare():
    paths = source_paths()
    for path in paths:
        if path.suffix == ".py":
            ast.parse(path.read_bytes(), filename=str(path))
    namespace = ast.parse((PACKAGE / "personal_authority_namespace.py").read_bytes())
    deadlines = [node.value.value for node in namespace.body
                 if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                 and any(isinstance(target, ast.Name) and target.id == "DEADLINE_SECONDS"
                         for target in node.targets)]
    if deadlines != [30.0]:
        raise RuntimeError("Original namespace deadline no longer matches")
    test_tree = ast.parse(TEST.read_bytes())
    if sum(isinstance(node, ast.FunctionDef) and node.name == CASE for node in test_tree.body) != 1:
        raise RuntimeError("Original test selection no longer matches")
    plan = {"state": "prepared_not_executed", "approval_required": False,
            "target": f"{TEST.relative_to(ROOT).as_posix()}::{CASE}",
            "namespace_deadline_seconds": 30.0, "source_files": [fact(path) for path in paths],
            "measurement": "original pytest consumer without profiling",
            "values_recorded": "pytest phase, outcome and duration; no arguments or locals",
            "limits": "New TEMP only; original fixture and checks; no native tracing, alias diagnosis, existing ACL changes, production data or service operations",
            "interpretation": "The original selected consumer only; not complete H01 enrollment or live writers"}
    write_new(PLAN, plan)
    print(json.dumps({"state": plan["state"], "plan": str(PLAN), "sources": len(paths)}))


def verify_plan():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    current = [fact(path) for path in source_paths()]
    if current != plan["source_files"] or plan["target"] != f"{TEST.relative_to(ROOT).as_posix()}::{CASE}":
        raise RuntimeError("Prepared sources or target changed; do not run a stale diagnostic")
    return plan


def lexical(path):
    if path is None:
        path = os.curdir
    text = os.fsdecode(path)
    if text.startswith("file:"):
        uri = urlsplit(text)
        if uri.netloc not in ("", "localhost"):
            raise RuntimeError("DIAGNOSTIC_REMOTE_FILE_URI_REFUSED")
        text = unquote(uri.path)
        if os.name == "nt" and len(text) > 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(text))


def inside(path, root):
    return path == root or path.startswith(root + os.sep)


def child(qa):
    plan = verify_plan()
    qa = qa.resolve(strict=True)
    if not qa.name.startswith("reno-h01-store-") or qa.parent != Path(tempfile.gettempdir()).resolve():
        raise RuntimeError("Expected a fresh diagnostic TEMP root")
    if (qa / "fixtures").exists():
        raise RuntimeError("Refusing an existing pytest basetemp; keep the previous attempt intact")
    qa_key = lexical(qa)
    source_keys = {lexical(ROOT / row["path"]) for row in plan["source_files"]}
    import_roots = [lexical(Path(sys.prefix)), lexical(Path(sys.base_prefix))]
    source_dirs = {lexical(path.parent) for path in source_paths()}
    source_dirs.update(map(lexical, [ROOT, GRAPH, PACKAGE, ROOT / "reno", ROOT / "tests"]))

    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            raise RuntimeError("DIAGNOSTIC_EXTERNAL_IO_REFUSED")
        if event == "sqlite3.connect":
            name = os.fspath(args[0])
            if name != ":memory:" and not inside(lexical(name), qa_key):
                raise RuntimeError("DIAGNOSTIC_DATABASE_OUTSIDE_QA")
        if event == "open" and not isinstance(args[0], int):
            path = lexical(args[0]); mode, flags = args[1:3]
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            writing = writing or isinstance(mode, str) and any(mark in mode for mark in "wax+")
            if writing and not inside(path, qa_key):
                raise RuntimeError("DIAGNOSTIC_WRITE_OUTSIDE_QA")
            if not writing and not (inside(path, qa_key) or path in source_keys
                                    or any(inside(path, base) for base in import_roots)):
                raise RuntimeError("DIAGNOSTIC_UNSELECTED_FILE_READ")
        if event in {"os.listdir", "os.scandir"} and args and not isinstance(args[0], int):
            path = lexical(args[0])
            if not (inside(path, qa_key) or path in source_dirs
                    or any(inside(path, base) for base in import_roots)):
                raise RuntimeError("DIAGNOSTIC_UNSELECTED_DIRECTORY_READ")
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"}:
            if not inside(lexical(args[0]), qa_key):
                raise RuntimeError("DIAGNOSTIC_MUTATION_OUTSIDE_QA")
        if event in {"os.rename", "os.link", "os.symlink"}:
            if any(not inside(lexical(path), qa_key) for path in args[:2]):
                raise RuntimeError("DIAGNOSTIC_MUTATION_OUTSIDE_QA")

    # Explicit relocation precedes all product and pytest imports. The normal
    # tests.conftest still owns fixture setup and teardown, with the same test.
    local = qa / "config.test.json"
    write_new(local, {"auth_token": "", "sync_token": "", "zhipu_api_key": ""})
    os.environ.update(RENO_QA_ROOT=str(qa), RENO_CONFIG_FILE=str(local),
                      RENO_DATA_DIR=str(qa / "data"), RENO_LLM_TESTS="0",
                      PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(qa / "unused-bytecode-cache")
    sys.path[:0] = [str(ROOT), str(GRAPH)]
    sys.addaudithook(audit)
    import pytest

    class Reports:
        def __init__(self):
            self.records = []

        def pytest_runtest_logreport(self, report):
            self.records.append({"nodeid": report.nodeid, "phase": report.when,
                                 "outcome": report.outcome, "duration": report.duration})

    reports = Reports()

    code = int(pytest.main(["-q", "--tb=short", "--capture=sys", "--log-file", str(qa / "pytest.log"),
                           "-p", "tests.conftest", "-p", "no:cacheprovider",
                           "-c", str(qa / "pytest.ini"), "--confcutdir", str(GRAPH),
                           "--basetemp", str(qa / "fixtures"), f"{TEST}::{CASE}"], plugins=[reports]))
    write_new(qa / "pytest-result.json", {"pytest_exit_code": code, "profiling_enabled": False, "phases": reports.records})
    return code


def run():
    verify_plan()
    # A prepared selection is single-use, including a failed launch. Retain the
    # claim and original attempt; a later diagnostic needs a reviewed new plan.
    with PLAN.with_name(PLAN.stem + "-started.json").open("x", encoding="utf-8") as claim:
        qa = Path(tempfile.mkdtemp(prefix="reno-h01-store-"))
        json.dump({"state": "one_attempt_reserved", "root": str(qa),
                   "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest()}, claim, indent=2)
    (qa / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RENO_", "PYTEST_", "PYTHON"))}
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_child", str(qa)]
    print(json.dumps({"state": "starting_one_verification", "root": str(qa)}), flush=True)
    with (qa / "stdout.log").open("xb") as out, (qa / "stderr.log").open("xb") as err:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            write_new(qa / "execution-result.json", {"child_started": False,
                "launch_error_type": type(error).__name__, "profiling_enabled": False,
                "pytest_result_written": False})
            raise
        # No timeout/retry or cleanup that could turn an observation failure
        # into a restarted test; callers poll this same live command handle.
        code = process.wait()
    unchanged = False
    try:
        verify_plan()
        unchanged = True
    finally:
        write_new(qa / "execution-result.json", {"child_started": True, "child_exit_code": code,
            "prepared_sources_unchanged": unchanged, "profiling_enabled": False,
            "pytest_result_written": (qa / "pytest-result.json").is_file()})
    print(json.dumps({"root": str(qa), "child_exit_code": code, "sources_unchanged": unchanged}))
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "_child"))
    parser.add_argument("qa", nargs="?")
    options = parser.parse_args()
    if options.mode == "prepare":
        prepare()
    elif options.mode == "run":
        raise SystemExit(run())
    else:
        if not options.qa:
            parser.error("_child requires its new TEMP root")
        raise SystemExit(child(Path(options.qa)))
