"""Independent file-only comparison of the r709 Stage effect and source facts."""
from pathlib import Path
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    graph = ROOT / "data/t16-runtime-origin-graph-r709"
    manifest = graph / "r709-candidate-manifest.json"
    selected = json.loads(manifest.read_bytes())
    rows = selected["source_files"] + selected["artifacts"]
    assert len(rows) == 25
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    old = ROOT / "data/t16-runtime-origin-graph-r705/authority_runtime_origin_r705/owned_stage_io_r251.py"
    new = graph / "authority_runtime_origin_r709/owned_stage_io_r251.py"
    a, b = ({node.name: node for node in ast.parse(path.read_bytes()).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))} for path in (old, new))
    assert [key for key in a if ast.dump(a[key]) != ast.dump(b[key])] == ["_effect"]
    x, y = a["_effect"], b["_effect"]
    assert ast.dump(x.body[0]) == ast.dump(y.body[0])
    assert ast.dump(x.body[-1]) == ast.dump(y.body[-1])
    assert ast.dump(x.body[1].test) == ast.dump(y.body[1].test)
    assert [ast.dump(n) for n in x.body[1].body] == [ast.dump(n) for n in y.body[1].body]
    assert sum(ast.dump(node) == ast.dump(x.body[1].orelse[0]) for node in y.body[1].orelse) == 1
    assert ast.dump(x.body[1].orelse[1].value.args[-1]) == ast.dump(y.body[1].orelse[-1].value.args[-1])
    integration = ROOT / "tests/test_store_stage_effect_integration.py"
    before = ROOT / "data/h01-stage-effect-profile-before-r709/test_store_stage_effect_integration.py"
    raw = integration.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    added = eol + b'        "authority_runtime_origin_r709": "' + fact(new)["sha256"].encode() + b'",'
    assert raw.count(added) == 1 and raw.replace(added, b"", 1) == before.read_bytes()
    test = ROOT / "tests/test_stage_effect_profile_r709.py"
    assert b"PENDING" not in test.read_bytes()
    count = 1  # Existing imported real publication integration.
    for node in ast.parse(test.read_bytes()).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            cases = 1
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "parametrize":
                    cases *= len(ast.literal_eval(dec.args[1]))
            count += cases
    assert count == 14
    result = {"state": "static_source_and_test_comparison_only", "manifest": fact(manifest),
              "entities_verified": len(rows), "stage": fact(new), "test": fact(test),
              "changed_original_definitions": ["_effect"], "unchanged_original_definitions": len(a) - 1,
              "first_scope_namespace_branch_native_call_error_code_and_tail_ast_equal": True,
              "integration": fact(integration), "integration_before": fact(before),
              "integration_only_hash_row_inverse_equal": True, "expanded_cases": count,
              "runtime_test_performed": False}
    path = ROOT / "data/h01-r709-root-source-check.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(fact(path)))


if __name__ == "__main__":
    main()
