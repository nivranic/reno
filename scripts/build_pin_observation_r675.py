"""Build a new isolated candidate; never edit a frozen predecessor."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r673"
NEW = ROOT / "data/t16-runtime-origin-graph-r675"
OLD_PACKAGE = OLD / "authority_runtime_origin_r673"
PACKAGE = NEW / "authority_runtime_origin_r675"
OLD_MANIFEST_SHA = "018780b3ff516899cb55b9a5d5af10de98be290c6100a15750e099f8cd88e9de"

ADDITION = '_PIN_RECORD_CHECK_ORIGINAL = _pin_record_original\n_PIN_FILE_IDENTITY_ORIGINAL = _held_io._verify_file_identity_original\n_PIN_FILE_OBSERVATION_ORIGINAL = _held_io._file_observation_original\n\n\ndef _pin_observation_original(pin):\n    if (_pin_record_original is not _PIN_RECORD_CHECK_ORIGINAL\n            or _held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL\n            or _held_io._file_observation_original is not _PIN_FILE_OBSERVATION_ORIGINAL):\n        record = _pin_record_original(pin)\n    else:\n        record = _PIN_RECORDS.get(id(pin))\n        _need(type(pin) is _ClosedStorePin and type(record) is _StorePinRecord\n            and record.original is pin and record.execution_owner == ns._owner() and not record.closed,\n            "NATIVE_ORIGINAL_PIN_REQUIRED")\n        _stage_stack_original(record.stack)\n        # Late custom dispatch retains the remaining original calls, without\n        # repeating the completed Store stack verification.\n        if (_held_io._verify_file_identity_original is not _PIN_FILE_IDENTITY_ORIGINAL\n                or _held_io._file_observation_original is not _PIN_FILE_OBSERVATION_ORIGINAL):\n            _held_io._verify_file_identity_original(record.scope, record.lease)\n    # This original producer performs its own real lease/identity check, owns\n    # the original failure bookkeeping and builds the complete observation.\n    return _held_io._file_observation_original(record.scope, record.lease)\n\n\n'


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
    manifest = OLD / "r673-candidate-manifest.json"
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
    assert [key for key in first if last.get(key) != first[key]] == ["_ClosedStorePin"]
    assert set(last) - set(first) == {"_pin_observation_original"}
    def methods(raw):
        cls = next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == "_ClosedStorePin")
        return {getattr(n, "name", type(n).__name__) + str(i): ast.dump(n, include_attributes=False) for i, n in enumerate(cls.body)}
    old_methods, new_methods = methods(old), methods(new)
    assert [k for k in old_methods if new_methods.get(k) != old_methods[k]] == ["_observation1"]
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
        assert raw.replace(b"r675", b"r673").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "fixed_pin_observation_static_only", "original_inputs": inputs,
            "changed_definitions": ["_ClosedStorePin"], "new_definitions": sorted(set(last) - set(first)),
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
    start = b"class _ClosedStorePin:" + eol
    assert old.count(start) == 1
    addition = ADDITION.encode().replace(b"\n", eol)
    new = old.replace(start, addition + start, 1)
    loop = b"    def _observation(self):" + eol + b"        record = _pin_record_original(self)" + eol + b"        return _held_io._file_observation_original(record.scope, record.lease)"
    assert new.count(loop) == 1
    new = new.replace(loop, b"    def _observation(self):" + eol + b"        return _pin_observation_original(self)", 1)
    target.write_bytes(new)
    for name in ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py"):
        path = NEW / name
        path.write_bytes(path.read_bytes().replace(b"r673", b"r675").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"state": "original_sources_verified_before_copy", "inputs": inputs})
    evidence = validate()
    json_new(NEW / "candidate-evidence.json", evidence)
    diff = "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target)))
    write_new(NEW / "store.diff", diff.encode())
    write_new(NEW / "README.md", b"# r675\n\nOne fixed Store pin observation consolidates its duplicate specific identity check. Original pin-record callers, full stack verification, file observation, budgets and error bookkeeping remain. Custom helpers keep the original chain. Static preparation only; tests and full Store consumer pending.\n")
    print(json.dumps({"state": "prepared_static_only", "store": fact(target), "support_unchanged": 15}))


def finish_draft():
    # The failed prepare already copied and adapted every source. Preserve that
    # attempt; only finish missing static artifacts after validating all bytes.
    assert not (NEW / "r675-candidate-manifest.json").exists()
    evidence = validate()
    previous = NEW / "build-candidate.py"
    assert previous.read_bytes() == (ROOT / "data/h01-pin-test-before-r675/build-pin-observation-failed.py").read_bytes()
    previous.write_bytes(Path(__file__).read_bytes())
    json_new(NEW / "candidate-evidence.json", evidence)
    old = (OLD_PACKAGE / "personal_authority_store.py").read_text(encoding="utf-8")
    new = (PACKAGE / "personal_authority_store.py").read_text(encoding="utf-8")
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile=str(OLD_PACKAGE / "personal_authority_store.py"), tofile=str(PACKAGE / "personal_authority_store.py"))).encode())
    write_new(NEW / "README.md", b"# r675\n\nFixed Pin observation removes one duplicate specific identity check. Full stack and original file observer remain; custom dispatch retains the original chain. Preparation AST member-key failure preserved separately; no product execution.\n")
    print(json.dumps({"state": "prepared_static_only", "store": fact(PACKAGE / "personal_authority_store.py")}))


def freeze():
    evidence = validate()
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / name for name in
        ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")]
    artifacts = [NEW / name for name in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    saved = json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8"))
    assert saved == evidence
    manifest = NEW / "r675-candidate-manifest.json"
    json_new(manifest, {"state": "fixed_pin_observation_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "unchanged_support_modules": 15,
        "unchanged_original_definitions": evidence["unchanged_original_definitions"],
        "original_inputs_preserved": True, "compiled_without_execution": 20,
        "consumer_inverse_bytes_equal": True, "product_execution": False})
    print(json.dumps({"manifest": fact(manifest)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "finish-draft", "freeze"))
    args = parser.parse_args()
    {"prepare": prepare, "finish-draft": finish_draft, "freeze": freeze}[args.action]()
