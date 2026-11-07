"""PROPOSAL: one new r727 direct-call-edge profile of the frozen r725 Store.

No permission is granted by this script or its proposal. NEW explicit user
approval bound to the exact proposal SHA is required before prepare/run/child.
The previous r723 single authorization is consumed and cannot be reused.
Verify-only reads named metadata and parses AST; it starts no product/profiler.
The original full fixture/selector, audit, -I/-B and 30-second limits remain.
Export only code positions/counts/times and direct getstats entry.calls edges;
no args, locals, native/path events, raw data or dump_stats. Edge times overlap.
One fresh TEMP attempt, preserved failures/insufficient coverage, no retry.
An instrumented result is diagnostic, not normal deadline or H01 acceptance.
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
GRAPH = ROOT / "data/t16-runtime-enrollment-graph-r725"
PACKAGE = GRAPH / "authority_runtime_enrollment_r725"
TEST = GRAPH / "test_store_stage_wiring.py"
CASE = "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"
PLAN = ROOT / "data/h01-store-caller-profile-verification-r727.json"
PROPOSAL = ROOT / "data/h01-r727-caller-profile-proposal.json"
AUTHORIZATION = ROOT / "data/h01-r727-caller-profile-authorization.json"
MANIFEST = GRAPH / "r725-candidate-manifest.json"
MANIFEST_SHA256 = "d006d07812126af97d1a93046c148eadd42abae455b135f4c5e8b758c7c90be8"
PARENT_TERMINAL = GRAPH / "r725-terminal.json"
PARENT_TERMINAL_SHA256 = "dadb0350ba28504811086da0e91a75a49447b430b166c31bbc423f00606a23f6"
EVIDENCE_INPUTS = {
    "scripts/verify_authority_store_profile_r723.py": "7aff18bb912397338e3ba1874e34a7a71b60352aa152723aa61ea629c9a4b303",
    "data/h01-r723-profile-authorization.json": "0cb708442f836f19e8496fd48cb52db889f962f5983520c1091a9808d77336bc",
    "data/h01-store-profile-terminal-r723.json": "16db2678b653035a48510bf3a82541a27371481cc232251b6f2aaf6b2aaa6b08",
    "data/h01-store-profile-verification-r723/call-timing.json": "8b6dd6c5e3915c4d2a8401c1f09014f4a59d355dac4c31937259f11fdea6d5c8",
    "data/t16-runtime-scan-seam-r726/r726-rejection.json": "3f95412de9c9b8b6fd950c5110e5a2b4b27231e051539cb2bd28778ae4d68084",
}
AUTHORIZED_SCOPE = {
    "target": "data/t16-runtime-enrollment-graph-r725/test_store_stage_wiring.py::" + CASE,
    "attempts": 1,
    "namespace_deadline_seconds": 30.0,
    "measurement": "cProfile_getstats_functions_and_direct_entry_calls_edges",
    "phase": "original_complete_pytest_call",
    "fresh_TEMP_only": True,
    "retry_allowed": False,
    "arguments_or_locals_or_path_events": False,
    "native_or_alias_diagnostics": False,
    "real_database_or_service_operations": False,
    "other_paused_diagnostics_resumed": False,
}
STATIC_ASSET_INPUTS = [
    {
        "byte_size": 1283,
        "path": "reno/dict/param_dictionary.yaml",
        "sha256": "90129a1b3f6e579aea47cf972c8df0f26f66a0a6e02919ffe2875c79b4bbaec5"
    },
    {
        "byte_size": 476,
        "path": "reno/dict/synonyms.yaml",
        "sha256": "f7a16bc9fa9934af0b71dee94395fabc119fb9874d09b01c97377dbccdcefed8"
    },
    {
        "byte_size": 2759,
        "path": "reno/dict/taxonomy.yaml",
        "sha256": "4cf0ebb1137f8abe1b106595620acce769fbdcf7d9ebe59eba89e9b2b9bd1e03"
    },
    {
        "byte_size": 2272,
        "path": "reno/prompts/atomize_v1.txt",
        "sha256": "2a21b1129d56e77e8d94b0929eda540e15df55e510c43c317cba638fc690f19c"
    },
    {
        "byte_size": 4379,
        "path": "reno/prompts/atomize_v2.txt",
        "sha256": "b1ebe299805ed1ec6548942e92b25adf1f61e87d92cc9d8fa77cf152f313cbb3"
    },
    {
        "byte_size": 1509,
        "path": "reno/prompts/judge_v1.txt",
        "sha256": "cf853da3a995ceb94289227cd3809b80b449df6ce66f496d404e723a4affe62c"
    },
    {
        "byte_size": 1596,
        "path": "reno/prompts/judge_v2.txt",
        "sha256": "aea7fb06a99a534e5c213e6d1b59f8d59d784b7f4cf958df4a6c983119eb5d1e"
    },
    {
        "byte_size": 9719,
        "path": "reno/schema/schema.sql",
        "sha256": "de5930eb4d96e6921c73e97cad6d11feab759479e016003c8469d2252b49f7a9"
    }
]



def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def source_paths():
    # Manifest-selected named inputs only; never enumerate DATA or old QA.
    return [ROOT / row["path"] for row in source_snapshot()]


def source_snapshot():
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("Reviewed r725 manifest changed")
    document = json.loads(raw)
    before, copied = document["original_inputs_before"], document["source_files"]
    if before != document["original_inputs_after"] or len(before) != 249 or len(copied) != 29:
        raise RuntimeError("Reviewed complete r725 source graph changed")
    expected = {}
    for row in [*before, *copied, document["builder"], document["source_evidence"], *STATIC_ASSET_INPUTS]:
        if type(row) is not dict or set(row) != {"path", "byte_size", "sha256"}:
            raise RuntimeError("Unexpected named source fact shape")
        if type(row["path"]) is not str:
            raise RuntimeError("Named source path must be a string")
        name = PurePosixPath(row["path"])
        if (name.is_absolute() or row["path"] != name.as_posix()
                or not name.parts or any(part in {".", ".."} or ":" in part or "\\" in part for part in name.parts)):
            raise RuntimeError("Named source escaped workspace")
        path = ROOT.joinpath(*name.parts)
        if not path.resolve(strict=True).is_relative_to(ROOT.resolve()) or fact(path) != row:
            raise RuntimeError("Reviewed source changed: " + row["path"])
        if row["path"] in expected and expected[row["path"]] != row:
            raise RuntimeError("Duplicate source facts disagree")
        expected[row["path"]] = row
    for path in (MANIFEST, PARENT_TERMINAL, ROOT / "tests/__init__.py", ROOT / "tests/conftest.py",
                 ROOT / "tests/schema_fixtures.py", Path(__file__).resolve()):
        row = fact(path)
        if row["path"] in expected and expected[row["path"]] != row:
            raise RuntimeError("Required source fact changed")
        expected[row["path"]] = row
    if fact(PARENT_TERMINAL)["sha256"] != PARENT_TERMINAL_SHA256:
        raise RuntimeError("Reviewed parent r725 terminal changed")
    if sum(row["path"].startswith(PACKAGE.relative_to(ROOT).as_posix() + "/") for row in copied) != 22:
        raise RuntimeError("The complete r725 package must retain its 22 modules")
    return [expected[name] for name in sorted(expected)]


def verify_proposal():
    raw = PROPOSAL.read_bytes()
    proposal = json.loads(raw)
    if (proposal.get("state") != "awaiting_new_explicit_user_authorization"
            or proposal.get("authorized_scope") != AUTHORIZED_SCOPE
            or proposal.get("runner") != fact(Path(__file__).resolve())
            or proposal.get("source_files_before") != source_snapshot()
            or proposal.get("source_files_current") != proposal.get("source_files_before")):
        raise RuntimeError("Proposal or its reviewed source facts changed")
    evidence = [fact(ROOT / name) for name in sorted(EVIDENCE_INPUTS)]
    if (proposal.get("evidence_files") != evidence
            or any(row["sha256"] != EVIDENCE_INPUTS[row["path"]] for row in evidence)):
        raise RuntimeError("Original authorization/profile/rejection evidence changed")
    return proposal, hashlib.sha256(raw).hexdigest()


def verify_authorization():
    # No artifact is written and no TEMP is created before this independent gate.
    if not AUTHORIZATION.is_file():
        raise RuntimeError("R727_NEW_EXPLICIT_USER_AUTHORIZATION_REQUIRED")
    raw = AUTHORIZATION.read_bytes()
    approval = json.loads(raw)
    fields = {"state", "proposal_sha256", "explicit_new_user_approval", "user_reply", "approval_context", "scope"}
    if (type(approval) is not dict or set(approval) != fields
            or approval["state"] != "one_r727_caller_profile_authorized_by_user"
            or approval["explicit_new_user_approval"] is not True
            or type(approval["user_reply"]) is not str or not approval["user_reply"].strip()
            or type(approval["approval_context"]) is not str
            or "r727" not in approval["approval_context"] or "one" not in approval["approval_context"]
            or approval["scope"] != AUTHORIZED_SCOPE):
        raise RuntimeError("R727_APPROVAL_SCOPE_MISMATCH")
    proposal, proposal_sha256 = verify_proposal()
    if approval["proposal_sha256"] != proposal_sha256:
        raise RuntimeError("R727_APPROVAL_PROPOSAL_SHA_MISMATCH")
    return {"path": AUTHORIZATION.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
            "proposal_sha256": proposal_sha256}


def check_selection(paths):
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


def verify_only():
    proposal, proposal_sha256 = verify_proposal()
    paths = source_paths()
    check_selection(paths)
    if source_snapshot() != proposal["source_files_before"]:
        raise RuntimeError("Sources changed during metadata verification")
    print(json.dumps({"state": "proposal_verified_not_prepared_not_executed",
        "proposal_sha256": proposal_sha256, "sources": len(paths),
        "new_authorization_present": AUTHORIZATION.exists(), "product_or_profiler_or_QA_started": False}))


def prepare():
    authorization = verify_authorization()
    paths = source_paths()
    check_selection(paths)
    if source_snapshot() != verify_proposal()[0]["source_files_before"]:
        raise RuntimeError("Reviewed proposal sources changed during prepare")
    plan = {"state": "prepared_not_executed", "approval_required": True,
            "authorization": authorization,
            "target": f"{TEST.relative_to(ROOT).as_posix()}::{CASE}",
            "manifest_sha256": MANIFEST_SHA256, "parent_terminal_sha256": PARENT_TERMINAL_SHA256,
            "namespace_deadline_seconds": 30.0, "source_files": [fact(path) for path in paths],
            "measurement": "cProfile around the original complete pytest call phase; direct entry.calls edges",
            "coverage": "No Store coverage guarantee: overhead may cause an earlier original deadline; no retry, extended limit or omitted initialization",
            "values_recorded": "caller/callee code positions, call counts, recursive counts, self/total times and original function rows; no arguments/locals/path events",
            "limits": "New TEMP only; original fixture and checks; no native tracing, alias diagnosis, existing ACL changes, production data or service operations",
            "interpretation": "Edge times overlap and cannot be summed; diagnostic only, no normal deadline or full H01 proof"}
    write_new(PLAN, plan)
    print(json.dumps({"state": plan["state"], "plan": str(PLAN), "sources": len(paths)}))


def verify_plan():
    authorization = verify_authorization()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    current = [fact(path) for path in source_paths()]
    if (current != plan["source_files"] or plan["target"] != f"{TEST.relative_to(ROOT).as_posix()}::{CASE}"
            or plan.get("authorization") != authorization
            or plan.get("manifest_sha256") != MANIFEST_SHA256
            or plan.get("parent_terminal_sha256") != PARENT_TERMINAL_SHA256
            or plan.get("namespace_deadline_seconds") != 30.0):
        raise RuntimeError("Prepared sources, approval or target changed; do not run a stale diagnostic")
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
    verify_authorization()
    plan = verify_plan()
    qa = qa.resolve(strict=True)
    if not qa.name.startswith("reno-h01-store-caller-profile-r727-") or qa.parent != Path(tempfile.gettempdir()).resolve():
        raise RuntimeError("Expected a fresh diagnostic TEMP root")
    if (qa / "fixtures").exists():
        raise RuntimeError("Refusing an existing pytest basetemp; keep the previous attempt intact")
    # The private child belongs to this exact single-use parent reservation.
    # A direct _child invocation cannot profile another fresh TEMP with an old
    # plan, and a previously entered child cannot be restarted in the same QA.
    claim_path = PLAN.with_name(PLAN.stem + "-started.json")
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    plan_sha256 = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    if (claim.get("state") != "one_attempt_reserved" or claim.get("root") != str(qa)
            or claim.get("plan_sha256") != plan_sha256):
        raise RuntimeError("DIAGNOSTIC_PARENT_RESERVATION_MISMATCH")
    write_new(qa / "child-started.json", {"state": "single_child_entered",
        "plan_sha256": plan_sha256, "diagnostic_only": True})
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
    import cProfile
    import pytest

    class Reports:
        def __init__(self):
            self.records = []

        def pytest_runtest_logreport(self, report):
            self.records.append({"nodeid": report.nodeid, "phase": report.when,
                                 "outcome": report.outcome, "duration": report.duration})

    reports = Reports()

    class Timing:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            write_new(qa / "call-started.json", {"nodeid": item.nodeid, "diagnostic_only": True})
            profiler = cProfile.Profile()
            try:
                profiler.enable()
                yield
            finally:
                profiler.disable()
                rows, edges = [], []

                def position(code):
                    if isinstance(code, str):
                        return {"builtin": code}
                    return {"filename": code.co_filename, "first_line": code.co_firstlineno,
                            "function": code.co_name}

                for entry in profiler.getstats():
                    code = entry.code
                    label = code if isinstance(code, str) else f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
                    rows.append({"function": label, "calls": entry.callcount,
                                 "recursive_calls": entry.reccallcount,
                                 "self_seconds": entry.inlinetime, "total_seconds": entry.totaltime})
                    for direct in entry.calls or ():
                        edges.append({"caller": position(entry.code), "callee": position(direct.code),
                            "calls": direct.callcount, "recursive_calls": direct.reccallcount,
                            "self_seconds": direct.inlinetime, "total_seconds": direct.totaltime})
                write_new(qa / "call-timing.json", {"diagnostic_only": True,
                    "instrumentation_overhead_included": True, "nodeid": item.nodeid,
                    "edge_scope": "direct cProfile getstats entry.calls only; no chronological/path trace",
                    "edge_times_overlap_not_additive": True,
                    "functions": sorted(rows, key=lambda row: row["self_seconds"], reverse=True),
                    "direct_callee_edges": sorted(edges, key=lambda row: row["total_seconds"], reverse=True)})


    code = int(pytest.main(["-q", "--tb=short", "--capture=sys", "--log-file", str(qa / "pytest.log"),
                           "-p", "tests.conftest", "-p", "no:cacheprovider",
                           "-c", str(qa / "pytest.ini"), "--confcutdir", str(GRAPH),
                           "--basetemp", str(qa / "fixtures"), f"{TEST}::{CASE}"], plugins=[Timing(), reports]))
    write_new(qa / "pytest-result.json", {"pytest_exit_code": code, "diagnostic_only": True, "phases": reports.records})
    return code


def run():
    verify_authorization()
    verify_plan()
    # A prepared selection is single-use, including a failed launch. Retain the
    # claim and original attempt; a later diagnostic needs a reviewed new plan.
    with PLAN.with_name(PLAN.stem + "-started.json").open("x", encoding="utf-8") as claim:
        qa = Path(tempfile.mkdtemp(prefix="reno-h01-store-caller-profile-r727-")).resolve(strict=True)
        json.dump({"state": "one_attempt_reserved", "root": str(qa),
                   "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest()}, claim, indent=2)
    (qa / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RENO_", "PYTEST_", "PYTHON"))}
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_child", str(qa)]
    print(json.dumps({"state": "starting_one_diagnostic", "root": str(qa)}), flush=True)
    with (qa / "stdout.log").open("xb") as out, (qa / "stderr.log").open("xb") as err:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            write_new(qa / "execution-result.json", {"child_started": False,
                "launch_error_type": type(error).__name__, "diagnostic_only": True,
                "pytest_result_written": False, "call_timing_written": False})
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
            "prepared_sources_unchanged": unchanged, "diagnostic_only": True,
            "pytest_result_written": (qa / "pytest-result.json").is_file(),
            "call_timing_written": (qa / "call-timing.json").is_file()})
    print(json.dumps({"root": str(qa), "child_exit_code": code, "sources_unchanged": unchanged}))
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify-only", "prepare", "run", "_child"))
    parser.add_argument("qa", nargs="?")
    options = parser.parse_args()
    if options.mode == "verify-only":
        verify_only()
    elif options.mode == "prepare":
        prepare()
    elif options.mode == "run":
        raise SystemExit(run())
    else:
        if not options.qa:
            parser.error("_child requires its new TEMP root")
        raise SystemExit(child(Path(options.qa)))
