"""Build one complete immutable-file observation consolidation, preserving source pins."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r685"
NEW = ROOT / "data/t16-runtime-origin-graph-r687"
OLD_PACKAGE = OLD / "authority_runtime_origin_r685"
PACKAGE = NEW / "authority_runtime_origin_r687"
OLD_MANIFEST_SHA = "3155a925531e16f1676608e3d85876603a4185808953b210ee426a322ae77103"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = '''def _immutable_pin_verify(pin):
    pin.verify()
    _need(type(pin.raw) is bytes and _sha(pin.raw) == pin.sha256, "NAMESPACE_IMMUTABLE_PIN_CHANGED")
'''
AFTER = '''def _closed_immutable_dispatch_original(pin):
    return (type(pin) is _CLOSED_IMMUTABLE_TYPE and _ClosedFileHold is _CLOSED_IMMUTABLE_TYPE
        and _ClosedFileHold.verify is _CLOSED_IMMUTABLE_VERIFY
        and _ClosedFileHold._observation is _CLOSED_IMMUTABLE_OBSERVATION
        and _ClosedFileHold.raw is _CLOSED_IMMUTABLE_RAW
        and _ClosedFileHold.sha256 is _CLOSED_IMMUTABLE_SHA256
        and _held_io._verify_file_original is _CLOSED_IMMUTABLE_FILE_VERIFY
        and _held_io._file_observation_original is _CLOSED_IMMUTABLE_FILE_OBSERVATION
        and _held_io.FileObservation is _CLOSED_IMMUTABLE_OBSERVATION_TYPE
        and _held_io._resource_original is _CLOSED_IMMUTABLE_RESOURCE_LOOKUP
        and _held_io._poll_record is _CLOSED_IMMUTABLE_POLL
        and _held_io._remember_failure is _CLOSED_IMMUTABLE_REMEMBER
        and _sha is _CLOSED_IMMUTABLE_SHA)


def _closed_immutable_finish_original(pin, scope, lease, expected):
    current = _held_io._resource_original(scope, lease, _held_io._FileLease)
    try:
        _held_io._poll_record(current)
        _need(current is expected and pin.scope is scope and pin.lease is lease
            and _closed_immutable_dispatch_original(pin), "NAMESPACE_IMMUTABLE_PIN_CHANGED")
    except BaseException as primary:
        _held_io._remember_failure(current, primary)
        raise


def _immutable_pin_verify(pin):
    admitted = _closed_immutable_dispatch_original(pin)
    pin.verify()  # Original real payload reread and SHA verification, exactly once.
    if admitted and _closed_immutable_dispatch_original(pin):
        scope, lease = pin.scope, pin.lease
        resource = _held_io._resource_original(scope, lease, _held_io._FileLease)
        if resource.resource_kind == "filehold":
            observed = pin._observation()
            _need(type(observed.raw) is bytes, "NAMESPACE_IMMUTABLE_PIN_CHANGED")
            if (pin.scope is not scope or pin.lease is not lease
                    or not _closed_immutable_dispatch_original(pin)):
                _closed_immutable_finish_original(pin, scope, lease, resource)
            computed = _sha(observed.raw)
            # The original last property observation checked clocks after SHA.
            # Keep that late gate and original failure bookkeeping explicitly.
            _closed_immutable_finish_original(pin, scope, lease, resource)
            _need(computed == observed.sha256, "NAMESPACE_IMMUTABLE_PIN_CHANGED")
            return
    _need(type(pin.raw) is bytes and _sha(pin.raw) == pin.sha256, "NAMESPACE_IMMUTABLE_PIN_CHANGED")
'''
POINTERS = '''_CLOSED_IMMUTABLE_TYPE = _ClosedFileHold
_CLOSED_IMMUTABLE_VERIFY = _ClosedFileHold.verify
_CLOSED_IMMUTABLE_OBSERVATION = _ClosedFileHold._observation
_CLOSED_IMMUTABLE_RAW = _ClosedFileHold.raw
_CLOSED_IMMUTABLE_SHA256 = _ClosedFileHold.sha256
_CLOSED_IMMUTABLE_FILE_VERIFY = _held_io._verify_file_original
_CLOSED_IMMUTABLE_FILE_OBSERVATION = _held_io._file_observation_original
_CLOSED_IMMUTABLE_OBSERVATION_TYPE = _held_io.FileObservation
_CLOSED_IMMUTABLE_RESOURCE_LOOKUP = _held_io._resource_original
_CLOSED_IMMUTABLE_POLL = _held_io._poll_record
_CLOSED_IMMUTABLE_REMEMBER = _held_io._remember_failure
_CLOSED_IMMUTABLE_SHA = _sha


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
    path = OLD / "r685-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    rows = json.loads(path.read_text(encoding="utf-8"))["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def replacements(eol):
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in (
        (BEFORE, AFTER), ("class _ClosedAuthorityLock:\n", POINTERS + "class _ClosedAuthorityLock:\n"))]


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_namespace.py").read_bytes()
    new = (PACKAGE / "personal_authority_namespace.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if b.get(key) != a[key]] == ["_immutable_pin_verify"]
    assert set(b) - set(a) == {"_closed_immutable_dispatch_original", "_closed_immutable_finish_original"}
    restored = new
    for before, after in reversed(replacements(b"\r\n" if b"\r\n" in old else b"\n")):
        assert restored.count(after) == 1
        restored = restored.replace(after, before, 1)
    assert restored == old
    same, pinned = 0, []
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name in {"personal_authority_store.py", "personal_origin_observer.py"}:
            assert target.read_bytes().replace(digest(new).encode(), digest(old).encode()) == path.read_bytes()
            pinned.append(path.name)
        elif path.name != "personal_authority_namespace.py":
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        restored = raw.replace(b"r687", b"r685")
        for source in ("personal_authority_namespace.py", "personal_authority_store.py", "personal_origin_observer.py"):
            restored = restored.replace(digest((PACKAGE / source).read_bytes()).encode(), digest((OLD_PACKAGE / source).read_bytes()).encode())
        assert restored == (OLD / name).read_bytes()
    return {"state": "immutable_pin_static_only", "original_inputs": inputs,
            "changed_definitions": ["_immutable_pin_verify"], "new_definitions": sorted(set(b) - set(a)),
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "namespace_pin_only_modules": pinned, "compiled_without_execution": 20,
            "consumer_inverse_bytes_equal": True, "namespace_inverse_bytes_equal": True,
            "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_namespace.py"
    old = target.read_bytes()
    new = old
    for before, after in replacements(b"\r\n" if b"\r\n" in old else b"\n"):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    target.write_bytes(new)
    for name in ("personal_authority_store.py", "personal_origin_observer.py"):
        p = PACKAGE / name
        assert p.read_bytes().count(digest(old).encode()) == 1
        p.write_bytes(p.read_bytes().replace(digest(old).encode(), digest(new).encode()))
    for name in NAMES:
        p = NEW / name
        raw = p.read_bytes().replace(b"r685", b"r687")
        for source in ("personal_authority_namespace.py", "personal_authority_store.py", "personal_origin_observer.py"):
            raw = raw.replace(digest((OLD_PACKAGE / source).read_bytes()).encode(), digest((PACKAGE / source).read_bytes()).encode())
        p.write_bytes(raw)
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "namespace.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r687\n\nOne fixed immutable ClosedFileHold verification shares its raw/SHA observation. Real verify/read and late lease/poll/first checks remain; unknown descriptors/helpers use the original chain. Store and origin observer only update Namespace source pins. Static preparation only.\n")
    print(json.dumps({"namespace": fact(target), "store": fact(PACKAGE / "personal_authority_store.py"), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "namespace.diff", "README.md")]
    path = NEW / "r687-candidate-manifest.json"
    json_new(path, {"state": "immutable_pin_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "unchanged_support_modules": evidence["unchanged_support_modules"],
        "namespace_pin_only_modules": evidence["namespace_pin_only_modules"],
        "unchanged_original_definitions": evidence["unchanged_original_definitions"],
        "original_inputs_preserved": True, "compiled_without_execution": 20,
        "consumer_inverse_bytes_equal": True, "product_execution": False})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
