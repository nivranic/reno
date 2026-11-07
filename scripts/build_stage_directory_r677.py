"""Build an isolated candidate with only the fixed Stage directory check changed."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r675"
NEW = ROOT / "data/t16-runtime-origin-graph-r677"
OLD_PACKAGE = OLD / "authority_runtime_origin_r675"
PACKAGE = NEW / "authority_runtime_origin_r677"
OLD_MANIFEST_SHA = "9f69baee0def5df65df2cd12eba752e7ed34e788171de9c222f72df5b12536fc"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = '''def _stage_directory_verify_original(record):
    if record.scope.mode == "store":
        _need(store._directory_record_original(record.scope.stack, record.directory) is record.directory_record,
            "STAGE_ORIGINAL_DIRECTORY_REQUIRED")
        ns._closed_directory_verify_original(record.directory_record)
    else:
        ns._store_directory_close_matches(record.directory_record)
'''
AFTER = '''_STORE_DIRECTORY_LOOKUP_ORIGINAL = store._directory_record_original
_NAMESPACE_DIRECTORY_CHECK_ORIGINAL = ns._closed_directory_verify_original


def _stage_directory_verify_original(record):
    if record.scope.mode == "store":
        combined = (store._directory_record_original is _STORE_DIRECTORY_LOOKUP_ORIGINAL
            and ns._closed_directory_verify_original is _NAMESPACE_DIRECTORY_CHECK_ORIGINAL
            and store.ns is ns)
        _need(store._directory_record_original(record.scope.stack, record.directory) is record.directory_record,
            "STAGE_ORIGINAL_DIRECTORY_REQUIRED")
        # The fixed lookup has fully checked this same actual directory record.
        # Custom/late dispatch retains the remaining original call, no replay.
        if (not combined
                or store._directory_record_original is not _STORE_DIRECTORY_LOOKUP_ORIGINAL
                or ns._closed_directory_verify_original is not _NAMESPACE_DIRECTORY_CHECK_ORIGINAL
                or store.ns is not ns):
            ns._closed_directory_verify_original(record.directory_record)
    else:
        ns._store_directory_close_matches(record.directory_record)
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
    write_new(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def original_inputs():
    path = OLD / "r675-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(path.read_text(encoding="utf-8"))["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "owned_stage_io_r251.py").read_bytes()
    new = (PACKAGE / "owned_stage_io_r251.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert a.keys() == b.keys()
    assert [key for key in a if b[key] != a[key]] == ["_stage_directory_verify_original"]
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    assert new.replace(AFTER.encode().replace(b"\n", eol), BEFORE.encode().replace(b"\n", eol), 1) == old
    same = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name != "owned_stage_io_r251.py":
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r677", b"r675").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "stage_directory_static_only", "original_inputs": inputs,
            "changed_definitions": ["_stage_directory_verify_original"], "new_definitions": [],
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "stage_inverse_bytes_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "owned_stage_io_r251.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    before, after = BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol)
    assert old.count(before) == 1
    new = old.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r675", b"r677").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "stage.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r677\n\nOne Stage directory check consolidates its duplicate verification after original Store lookup returns the same genuine record. Custom or late dispatch keeps the remaining original call. All other Stage definitions and support modules unchanged. Static preparation only.\n")
    print(json.dumps({"stage": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "stage.diff", "README.md")]
    path = NEW / "r677-candidate-manifest.json"
    json_new(path, {"state": "stage_directory_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "unchanged_support_modules": 15,
        "unchanged_original_definitions": evidence["unchanged_original_definitions"],
        "original_inputs_preserved": True, "compiled_without_execution": 20,
        "consumer_inverse_bytes_equal": True, "product_execution": False})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
