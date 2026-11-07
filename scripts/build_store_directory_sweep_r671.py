"""Build a new isolated candidate; never edit a frozen predecessor."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r668"
NEW = ROOT / "data/t16-runtime-origin-graph-r671"
OLD_PACKAGE = OLD / "authority_runtime_origin_r668"
PACKAGE = NEW / "authority_runtime_origin_r671"
OLD_MANIFEST_SHA = "7a23cf99a0d89a8a0852bb3b6d804b53ab821e751849da7768c5f4ed1ff6d74b"

ADDITION = '''_STORE_DIRECTORY_SINGLE = ns._closed_directory_verify_original
_STORE_DIRECTORY_SWEEP = ns._closed_directory_verify_sweep_original
_STORE_DIRECTORY_PATHS = ns._closed_directory_verify_with_paths_original


def _store_directory_sweep_dispatch_original():
    return (ns._closed_directory_verify_original is _STORE_DIRECTORY_SINGLE
        and ns._closed_directory_verify_sweep_original is _STORE_DIRECTORY_SWEEP
        and ns._closed_directory_verify_with_paths_original is _STORE_DIRECTORY_PATHS)


def _store_directories_verify_original(record):
    _need(type(record) is _StoreStackRecord
        and _STACK_RECORDS.get(id(record.original)) is record
        and record.execution_owner == ns._owner() and not record.closed,
        "FAMILIES_STACK_ORIGINAL_CUSTODY_REQUIRED")
    if record.first is not None:
        raise record.first
    if record.stage is not None and record.stage.first_failure is not None:
        raise record.stage.first_failure
    stack, directories = record.original, record.directories
    _need(type(directories) is tuple, "NATIVE_ORIGINAL_DIRECTORY_CHANGED")
    batched = _store_directory_sweep_dispatch_original()
    if batched:
        # All real records and native handles are verified separately; only
        # repeated path stats share this one original sweep's fresh dictionary.
        _STORE_DIRECTORY_SWEEP(directories)
    else:
        # Preserve unknown helpers' original per-record calls and exceptions.
        for directory in directories:
            ns._closed_directory_verify_original(directory)
    if record.first is not None:
        raise record.first
    if record.stage is not None and record.stage.first_failure is not None:
        raise record.stage.first_failure
    _need(record.original is stack and _STACK_RECORDS.get(id(stack)) is record
        and record.execution_owner == ns._owner() and not record.closed
        and record.directories is directories,
        "NATIVE_ORIGINAL_DIRECTORY_CHANGED")
    if batched:
        _need(_store_directory_sweep_dispatch_original(), "NATIVE_ORIGINAL_DIRECTORY_CHANGED")


'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": digest(raw)}


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def json_new(path, value):
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def original_inputs():
    manifest = OLD / "r668-candidate-manifest.json"
    assert digest(manifest.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(manifest.read_text(encoding="utf-8"))["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def definitions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    first, last = definitions(old), definitions(new)
    assert [key for key in first if last.get(key) != first[key]] == ["_stage_stack_original"]
    assert set(last) - set(first) == {"_store_directory_sweep_dispatch_original", "_store_directories_verify_original"}
    unchanged = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name != "personal_authority_store.py":
            assert target.read_bytes() == path.read_bytes()
            unchanged += 1
    for name in ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py"):
        raw = (NEW / name).read_bytes()
        compile(raw, str(NEW / name), "exec")
        assert raw.replace(b"r671", b"r668").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "single_store_directory_sweep_static_only", "original_inputs": inputs,
            "changed_definitions": ["_stage_stack_original"], "new_definitions": sorted(set(last) - set(first)),
            "unchanged_original_definitions": len(first) - 1, "unchanged_support_modules": unchanged,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "ordinary_budgets_stage_namespace_unchanged": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        old = ROOT / row["path"]
        new = PACKAGE / old.name if old.parent == OLD_PACKAGE else NEW / old.name
        write_new(new, old.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    start = b"def _stage_stack_original(stack):" + eol
    assert old.count(start) == 1
    addition = ADDITION.encode().replace(b"\n", eol)
    new = old.replace(start, addition + start, 1)
    loop = b"    for directory in record.directories:" + eol + b"        ns._closed_directory_verify_original(directory)" + eol + b"    return record"
    assert new.count(loop) == 1
    new = new.replace(loop, b"    _store_directories_verify_original(record)" + eol + b"    return record", 1)
    target.write_bytes(new)
    for name in ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py"):
        path = NEW / name
        path.write_bytes(path.read_bytes().replace(b"r668", b"r671").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"state": "original_sources_verified_before_copy", "inputs": inputs})
    evidence = validate()
    json_new(NEW / "candidate-evidence.json", evidence)
    diff = "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target)))
    write_new(NEW / "store.diff", diff.encode())
    write_new(NEW / "README.md", b"# r671\n\nOne complete Store directory sweep shares duplicate path stats. All native handles, original record bindings, ordinary budgets and effect boundaries remain. Custom helpers retain original per-record calls. Static preparation only; tests and full Store consumer pending.\n")
    print(json.dumps({"state": "prepared_static_only", "store": fact(target), "support_unchanged": 15}))


def freeze():
    evidence = validate()
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / name for name in
        ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")]
    artifacts = [NEW / name for name in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    saved = json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8"))
    assert saved == evidence
    manifest = NEW / "r671-candidate-manifest.json"
    json_new(manifest, {"state": "single_store_directory_sweep_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "unchanged_support_modules": 15,
        "unchanged_original_definitions": evidence["unchanged_original_definitions"],
        "original_inputs_preserved": True, "compiled_without_execution": 20,
        "consumer_inverse_bytes_equal": True, "product_execution": False})
    print(json.dumps({"manifest": fact(manifest)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
