"""File-only construction of the complete current runtime/enrollment graph.

No module import, test execution, profiling, real database or service operation.
Old candidates are immutable named inputs; only the fresh r725 directory is written.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/t16-runtime-enrollment-graph-r725"
PACKAGE_NAME = "authority_runtime_enrollment_r725"
PACKAGE = DEST / PACKAGE_NAME
B_BASE = "data/t16-enrollment-contract-completion-r724/"
A_BASE = "data/t16-runtime-origin-graph-r721/"
B_PACKAGE = B_BASE + "authority_enrollment_r478/"
A_PACKAGE = A_BASE + "authority_runtime_origin_r721/"
B_MANIFEST = ROOT / (B_BASE + "r724-green-frozen-manifest.json")
A_MANIFEST = ROOT / (A_BASE + "r721-candidate-manifest.json")
CURRENT = ROOT / "data/t16-public-checklist-summary-preflight-20261009.json"
PINNED = {
    B_BASE + "r724-green-frozen-manifest.json": "75e3a361172b74f347083888e285ccc11d0d19147a971baf88c9f6a1feded4f4",
    B_BASE + "r724-terminal.json": "8d710e82f0912ed4cd0d6f2c94fc7bb6aa58e03cfcded3ce1b2236666ed9de8a",
    A_BASE + "r721-candidate-manifest.json": "1b17b9698af0505fb8fa4fb2dbbcad6414f0ab0ada4de32c9815f59df7f54956",
    "data/t16-public-checklist-summary-preflight-20261009.json": "a4efb443a352514fbfd8a666a2feb9bea71d5f77056fbd5ad1a83131b248521c",
    "data/t16-enrollment-family-r478/test_enrollment_store_whole.py": "84b26c5b7baf9ad551739035d43ddb36efa122c16d4ccd643e08bb945fabc4d4",
    A_BASE + "test_store_stage_wiring.py": "a4c8dee8723589db1c636d583b6ee106208f288e43388c5bccdefd2114621e99",
    A_BASE + "test_whole_append.py": "6001da2a0bd22c827e4e180b3b7b44e0690c3a48955d07642749b9d17acbef3a",
}
RUNTIME_NAMES = ("personal_authority_namespace.py", "personal_authority_store.py",
    "owned_stage_io_r251.py", "shared_held_io_r250.py")
FINITE_TESTS = ("test_enrollment_contract.py", "test_enrollment_owner_boundary.py",
    "test_activation_sql_engine.py", "test_enrollment_completion_links.py")
LATEST_NS_SHA = "1cf99b186b8193725abd4a6fda2bfa4a3c7062efaf11730f9ec38cc072933cbd"
LATEST_HELD_SHA = "f3ab31d5a4f51f212a0d15b18944b0fd8b6398d6491f9cc5aa3c8e710d318e6c"
LATEST_STAGE_SHA = "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
FIXED_CODEC_SHA = "91d3bd48f0c9187fce660e5d1eb11a54dff061481c6ebecb50388caf2782d87e"


def need(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": sha(raw)}


def write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path, document):
    write(path, (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def replace_once(raw, old, new):
    need(raw.count(old) == 1, "replacement not exactly one: " + old.decode("utf-8"))
    result = raw.replace(old, new)
    need(result.replace(new, old) == raw, "replacement inverse failed")
    return result


def checked_inputs():
    for relative, expected in PINNED.items():
        need(fact(ROOT / relative)["sha256"] == expected, "named input byte pin changed: " + relative)
    b = json.loads(B_MANIFEST.read_bytes())
    a = json.loads(A_MANIFEST.read_bytes())
    current = json.loads(CURRENT.read_bytes())
    need(current["source_file_count"] == 193 and current["source_set_sha256"] ==
        "84e1fa07d0cf7e64b3ef5822e02dcddaf84e1934703b5159b74f743cac4eb003", "current source baseline mismatch")
    b_sources = [row for row in b["sources"] if row["path"].startswith(B_PACKAGE)
        or row["path"] in {B_BASE + name for name in FINITE_TESTS}]
    a_sources = [{"path": row["path"], "byte_size": row["bytes"], "sha256": row["sha256"]}
        for row in a["source_files"] if row["path"].startswith(A_PACKAGE)
        or row["path"] in {A_BASE + "test_store_stage_wiring.py", A_BASE + "test_whole_append.py"}]
    need(len(b_sources) == 26 and len(a_sources) == 18, "fixed package/test selection incomplete")
    rows = {row["path"]: row for row in (*b_sources, *a_sources, *current["production_sources"])}
    rows.update((relative, fact(ROOT / relative)) for relative in PINNED)
    for name in ("personal_authority_store_v2.py", "personal_authority_store_v2_bridge.py",
            "personal_mutation_contract.py", "personal_authority_schema.py", "personal_authority_namespace.py"):
        relative = "data/t16-owner-commit-readfacts-r434/authority_owner_commit_r434/" + name
        rows[relative] = next(row for row in b["sources"] if row["path"] == relative)
    for name in ("conftest.py", "schema_fixtures.py"):
        relative = "tests/" + name
        rows[relative] = next(row for row in b["sources"] if row["path"] == relative)
    for row in rows.values():
        path = ROOT / row["path"]
        need(path.resolve().is_relative_to(ROOT.resolve()) and fact(path) == row,
            "bound named source changed: " + row["path"])
    return sorted(rows.values(), key=lambda row: row["path"]), b_sources, a_sources


def definitions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def assignment(raw, name):
    tree = ast.parse(raw)
    node = next(node for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)
    return b"".join(raw.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]), node


def declaration_value(raw, name):
    _, node = assignment(raw, name)
    value = node.value
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
        return frozenset(ast.literal_eval(value.args[0]))
    return ast.literal_eval(value)


def inherit_closed_names(latest, enrollment):
    result = latest
    delta = {}
    newline = b"\r\n" if b"\r\n" in latest else b"\n"
    expected_additions = {
        "_STORE_FILES": {"store-enrollment-reservation-v1.json", "store-enrollment-header-v1.json"},
        "_STORE_DIRECTORIES": {"journal-enrollment-v1"},
    }
    for name, added in expected_additions.items():
        old_span, _ = assignment(latest, name)
        inherited_span, _ = assignment(enrollment, name)
        inherited_span = inherited_span.replace(b"\r\n", b"\n").replace(b"\n", newline)
        old_value, new_value = declaration_value(latest, name), declaration_value(enrollment, name)
        need(new_value == old_value | added and new_value - old_value == added, "enrollment closed names delta mismatch")
        result = replace_once(result, old_span, inherited_span)
        delta[name] = {"added": sorted(added), "old": sorted(old_value), "new": sorted(new_value)}
    need(declaration_value(result, "_STORE_REQUIRED") == declaration_value(latest, "_STORE_REQUIRED"),
        "required namespace physical entries changed")
    need(definitions(result) == definitions(latest), "a namespace function or class changed")
    return result, delta


def patch_bindings(old, files):
    result = old
    pins = declaration_value(old, "SOURCES")
    sizes = declaration_value(old, "SOURCE_SIZES")
    need(len(pins) == 20 and len(dict(pins)) == 20 and set(dict(pins)) == set(dict(sizes)), "20 source graph members required")
    new_pins = tuple((name, sha(files[name])) for name, _ in pins)
    new_sizes = tuple((name, len(files[name])) for name, _ in sizes)
    for field, before, after in (("SOURCES", pins, new_pins), ("SOURCE_SIZES", sizes, new_sizes)):
        span, _ = assignment(old, field)
        old_literal = repr(before).encode("utf-8")
        new_literal = repr(after).encode("utf-8")
        need(span.count(old_literal) == 1, "source graph literal differs from original serialization")
        result = replace_once(result, span, span.replace(old_literal, new_literal))
    need(declaration_value(result, "FROZEN") is True and definitions(result) == definitions(old), "bindings shape changed")
    for name, expected in new_pins:
        need(sha(files[name]) == expected and len(files[name]) == dict(new_sizes)[name]
            and len(files[name]) <= 1024 * 1024, "actual source graph closure mismatch")
    return result, new_pins, new_sizes


def adapt_owner_boundary(raw):
    raw = replace_once(raw, b"import inspect\n", b"import inspect\nimport hashlib\n")
    old = b'        "personal_mutation_contract.py", "owned_stage_io_r251.py", "personal_authority_schema.py"):\n'
    new = b'        "personal_mutation_contract.py", "personal_authority_schema.py"):\n'
    raw = replace_once(raw, old, new)
    old_assert = b'        assert (candidate / name).read_bytes() == (baseline / name).read_bytes()\n'
    new_assert = old_assert + (
        '    stage_reference = root / "data/t16-runtime-origin-graph-r721/authority_runtime_origin_r721/owned_stage_io_r251.py"\n'
        '    stage_raw = (candidate / "owned_stage_io_r251.py").read_bytes()\n'
        '    assert stage_raw == stage_reference.read_bytes()\n'
        f'    assert hashlib.sha256(stage_raw).hexdigest() == "{LATEST_STAGE_SHA}"\n'
    ).encode("utf-8")
    return replace_once(raw, old_assert, new_assert)


def make_tests(files, namespace_sha):
    tests, deltas = {}, {}
    sources = {name: ROOT / (B_BASE + name) for name in FINITE_TESTS}
    sources.update({name: ROOT / (A_BASE + name) for name in ("test_store_stage_wiring.py", "test_whole_append.py")})
    sources["test_enrollment_store_whole.py"] = ROOT / "data/t16-enrollment-family-r478/test_enrollment_store_whole.py"
    for name, path in sources.items():
        original = path.read_bytes()
        old_package = b"authority_runtime_origin_r721" if path.as_posix().find("t16-runtime-origin-graph-r721") >= 0 else b"authority_enrollment_r478"
        need(old_package in original, "original test package import missing")
        raw = original.replace(old_package, PACKAGE_NAME.encode("utf-8"))
        need(raw.replace(PACKAGE_NAME.encode("utf-8"), old_package) == original, "test loader inverse failed")
        changes = ["ordinary package imports/HERE"]
        if name in {"test_store_stage_wiring.py", "test_whole_append.py"}:
            raw = replace_once(raw, LATEST_NS_SHA.encode(), namespace_sha.encode())
            changes.append("actual inherited Namespace SHA")
        if name == "test_enrollment_owner_boundary.py":
            beforedefs = definitions(raw)
            raw = adapt_owner_boundary(raw)
            afterdefs = definitions(raw)
            changed = [key for key in beforedefs if beforedefs[key] != afterdefs[key]]
            need(changed == ["test_old_v2_v3_source_bytes_and_namespace_header_codec_are_preserved"],
                "owner boundary behavior changed beyond Stage source assertion")
            changes.append("Stage source from r434 to latest r721 exact bytes/SHA; four other support-byte assertions kept")
        else:
            need(definitions(raw) == definitions(original), "a fixture/helper/test body changed: " + name)
        ast.parse(raw)
        tests[name] = raw
        deltas[name] = {"source": fact(path), "permitted_changes": changes,
            "fixture_helper_and_test_definitions_exact_except_declared_stage_source_assertion": True}
    return tests, deltas


def main():
    before, b_rows, a_rows = checked_inputs()
    need(not DEST.exists(), "fresh r725 directory required; no implicit rebuild")
    b_files = {Path(row["path"]).name: (ROOT / row["path"]).read_bytes() for row in b_rows if row["path"].startswith(B_PACKAGE)}
    a_files = {Path(row["path"]).name: (ROOT / row["path"]).read_bytes() for row in a_rows if row["path"].startswith(A_PACKAGE)}
    need(len(b_files) == 22 and len(a_files) == 16, "complete named package sets required")
    files = dict(b_files)
    for name in RUNTIME_NAMES:
        files[name] = a_files[name]
    for name in set(a_files) - set(RUNTIME_NAMES) - {"personal_origin_observer.py"}:
        need(a_files[name] == b_files[name], "a shared support module differs: " + name)
    files["personal_authority_namespace.py"], closed_delta = inherit_closed_names(
        a_files["personal_authority_namespace.py"], b_files["personal_authority_namespace.py"])
    namespace_sha = sha(files["personal_authority_namespace.py"])
    files["personal_authority_store.py"] = replace_once(files["personal_authority_store.py"],
        LATEST_NS_SHA.encode(), namespace_sha.encode())
    need(definitions(files["personal_authority_store.py"]) == definitions(a_files["personal_authority_store.py"]),
        "latest Store definitions changed")
    old_ns_sha = sha(b_files["personal_authority_namespace.py"])
    old_held_sha = sha(b_files["shared_held_io_r250.py"])
    observer = replace_once(b_files["personal_origin_observer.py"], old_ns_sha.encode(), namespace_sha.encode())
    observer = replace_once(observer, old_held_sha.encode(), LATEST_HELD_SHA.encode())
    need(definitions(observer) == definitions(b_files["personal_origin_observer.py"]), "borrowed observer definitions lost or changed")
    files["personal_origin_observer.py"] = observer
    files["personal_committed_readfacts.py"] = replace_once(b_files["personal_committed_readfacts.py"],
        sha(b_files["personal_origin_observer.py"]).encode(), sha(observer).encode())
    need(definitions(files["personal_committed_readfacts.py"]) == definitions(b_files["personal_committed_readfacts.py"]),
        "committed reader definitions changed")
    files["personal_enrollment_bindings.py"], pins, sizes = patch_bindings(b_files["personal_enrollment_bindings.py"], files)
    need(sha(files["personal_enrollment_contract.py"]) == FIXED_CODEC_SHA and len(files["personal_enrollment_contract.py"]) == 10232,
        "r724 completion codec regressed")
    tests, test_deltas = make_tests(files, namespace_sha)
    for raw in files.values():
        ast.parse(raw)
    DEST.mkdir()
    PACKAGE.mkdir()
    for name, raw in files.items():
        write(PACKAGE / name, raw)
    for name, raw in tests.items():
        write(DEST / name, raw)
    changed_from_b = sorted(name for name in files if files[name] != b_files[name])
    changed_from_a_runtime = sorted(name for name in RUNTIME_NAMES if files[name] != a_files[name])
    evidence = {"kind": "r725_complete_runtime_enrollment_source_integration", "state": "constructed-not-executed",
        "package": PACKAGE.relative_to(ROOT).as_posix(), "module_count": len(files),
        "changed_from_r724": changed_from_b, "latest_runtime_narrow_changes": changed_from_a_runtime,
        "namespace_closed_sets_inherited": closed_delta, "namespace_required_unchanged": True,
        "namespace_and_store_definitions_latest_unchanged": True,
        "Stage_and_Held_latest_exact_bytes": True, "r724_observer_all_definitions_preserved": True,
        "observer_pin_changes_only": ["Namespace", "Held"], "readfacts_pin_change_only": "observer",
        "r724_corrected_codec_preserved": True, "enrollment_facade_and_CAS_engine_exact_bytes": True,
        "binding_graph_member_count": len(pins), "binding_graph_member_order_preserved": True,
        "binding_graph_all_actual_bytes_and_sizes_matched": True, "source_sizes": sizes,
        "final_source_sha256": {name: sha(raw) for name, raw in files.items()},
        "tests": test_deltas, "test_files_copied_not_executed": len(tests),
        "store_source_cap_unchanged": 524288, "enrollment_source_cap_unchanged": 1048576,
        "namespace_seconds_unchanged": 30, "runtime_or_SQL_or_activation_permission_created": False,
        "tests_or_profiles_executed_or_real_database_or_services_or_existing_permissions_mutated": False,
        "fresh_facility_requirement": "Changed complete source graph must use original fresh QA initializer; no old facility/header compatibility is inferred."}
    write_json(DEST / "r725-source-evidence.json", evidence)
    after, _, _ = checked_inputs()
    need(before == after, "an original named input changed while constructing r725")
    new_sources = [fact(PACKAGE / name) for name in sorted(files)] + [fact(DEST / name) for name in sorted(tests)]
    manifest = {"kind": "r725_complete_runtime_enrollment_candidate", "state": "frozen-source-not-executed",
        "builder": fact(Path(__file__)), "original_inputs_before": before, "original_inputs_after": after,
        "source_files": new_sources, "source_evidence": fact(DEST / "r725-source-evidence.json"),
        "copied_positive_test_files": [str((DEST / name).relative_to(ROOT).as_posix()) for name in
            ("test_store_stage_wiring.py", "test_whole_append.py", "test_enrollment_store_whole.py")],
        "copied_finite_test_files": [str((DEST / name).relative_to(ROOT).as_posix()) for name in FINITE_TESTS],
        "selectors_executed": [], "goal_complete": False, "H01_complete": False}
    write_json(DEST / "r725-candidate-manifest.json", manifest)
    print(json.dumps({"state": manifest["state"], "module_count": len(files), "copied_test_count": len(tests),
        "original_bound_inputs_preserved": len(after), "manifest": fact(DEST / "r725-candidate-manifest.json"),
        "final_namespace_sha256": namespace_sha, "final_store_sha256": sha(files["personal_authority_store.py"]),
        "final_observer_sha256": sha(observer), "final_readfacts_sha256": sha(files["personal_committed_readfacts.py"]),
        "tests_executed": False}, sort_keys=True))


if __name__ == "__main__":
    main()
