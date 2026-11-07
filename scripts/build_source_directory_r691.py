"""Share one genuinely acquired directory within one fixed source-pin batch."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r687"
NEW = ROOT / "data/t16-runtime-origin-graph-r691"
OLD_PACKAGE = OLD / "authority_runtime_origin_r687"
PACKAGE = NEW / "authority_runtime_origin_r691"
OLD_MANIFEST_SHA = "686c5374a869d56fb8bccc56b31f2910eb0c7eecc2cb8790447db7ef35f28662"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = '''def _source_pins(stack, budget):
    for module, expected in ((ns, NAMESPACE_SOURCE_SHA256), (v2, V2_SOURCE_SHA256), (mc, MUTATION_SOURCE_SHA256)):
        _source_acquire_original(stack, module, expected, budget)
    _need(mc.VERSION == 3, "MUTATION_CODEC_BINDING_CHANGED")
'''
AFTER = '''_SOURCE_BATCH_MODULES_ORIGINAL = (ns, v2, mc)
_SOURCE_BATCH_ACQUIRE_ORIGINAL = _source_acquire_original
_SOURCE_BATCH_ENTER_ORIGINAL = _ClosingStack.enter_context
_SOURCE_BATCH_DIRECTORY_ORIGINAL = safe._Directory
_SOURCE_BATCH_DIRECTORY_ENTER_ORIGINAL = ns._closed_directory_enter_original
_SOURCE_BATCH_FILE_ACQUIRE_ORIGINAL = _held_io._acquire_file_original


def _source_batch_paths_original(stack):
    if (type(stack) not in {_ClosingStack, _IoStack}
            or _source_acquire_original is not _SOURCE_BATCH_ACQUIRE_ORIGINAL
            or _stage_stack_original is not _PIN_STACK_CHECK_ORIGINAL
            or safe._Directory is not _SOURCE_BATCH_DIRECTORY_ORIGINAL
            or ns._closed_directory_enter_original is not _SOURCE_BATCH_DIRECTORY_ENTER_ORIGINAL
            or _held_io._acquire_file_original is not _SOURCE_BATCH_FILE_ACQUIRE_ORIGINAL
            or not ns._store_original_method(stack.enter_context, stack, _SOURCE_BATCH_ENTER_ORIGINAL)
            or any(actual is not original for actual, original in
                zip((ns, v2, mc), _SOURCE_BATCH_MODULES_ORIGINAL, strict=True))):
        return None
    names = tuple(module.__dict__.get("__file__") for module in _SOURCE_BATCH_MODULES_ORIGINAL)
    if not all(type(name) is str for name in names):
        return None
    paths = tuple(Path(name).absolute() for name in names)
    return paths if all(path.parent == paths[0].parent for path in paths) else None


def _source_batch_binding_original(custody, directory, original, paths, sources):
    _need(_source_batch_paths_original(custody.original) == paths
        and sources == ((ns, NAMESPACE_SOURCE_SHA256), (v2, V2_SOURCE_SHA256), (mc, MUTATION_SOURCE_SHA256))
        and type(directory) is _SOURCE_BATCH_DIRECTORY_ORIGINAL
        and type(original) is ns._ClosedDirectoryRecord and original.original is directory
        and ns._CLOSED_DIRECTORY_RECORDS.get(id(directory)) is original
        and any(row is original for row in custody.directories)
        and original.entered and not original.closed and original.owner == ns._owner()
        and directory.path == paths[0].parent and directory.base == paths[0].parent
        and directory.parent is None and directory.create is False,
        "CODEC_SOURCE_BINDING_CHANGED")


def _source_pin_batch_original(stack, budget):
    sources = ((ns, NAMESPACE_SOURCE_SHA256), (v2, V2_SOURCE_SHA256), (mc, MUTATION_SOURCE_SHA256))
    custody = _stage_stack_original(stack)
    _need(custody.budget is budget, "CODEC_SOURCE_BINDING_CHANGED")
    paths = _source_batch_paths_original(stack)
    _need(paths is not None, "CODEC_SOURCE_BINDING_CHANGED")
    directory = stack.enter_context(safe._Directory(paths[0].parent, base=paths[0].parent))
    original = ns._CLOSED_DIRECTORY_RECORDS.get(id(directory))
    for (module, expected), path in zip(sources, paths, strict=True):
        current = _stage_stack_original(stack)
        _need(current is custody and current.budget is budget, "CODEC_SOURCE_BINDING_CHANGED")
        _source_batch_binding_original(custody, directory, original, paths, sources)
        lease = _held_io._acquire_file_original(custody.scope, directory, path.name,
            maximum=512 * 1024, budget=custody.clock, kind="filehold")
        pin = _ClosedStorePin()
        record = _StorePinRecord(pin, stack, directory, path.name, 512 * 1024, budget, "source", lease)
        _PIN_RECORDS[id(pin)] = record
        custody.pins += (record,)
        try:
            _need(pin.sha256 == expected and _sha(pin.raw) == expected, "CODEC_SOURCE_BINDING_CHANGED")
        except BaseException as failure:
            custody.first = custody.first if custody.first is not None else failure
            _remember_budget_first_original(budget, failure)
            raise
    _need(_stage_stack_original(stack) is custody, "CODEC_SOURCE_BINDING_CHANGED")
    _source_batch_binding_original(custody, directory, original, paths, sources)


def _source_pins(stack, budget):
    if _source_batch_paths_original(stack) is not None:
        _source_pin_batch_original(stack, budget)
    else:
        for module, expected in ((ns, NAMESPACE_SOURCE_SHA256), (v2, V2_SOURCE_SHA256), (mc, MUTATION_SOURCE_SHA256)):
            _source_acquire_original(stack, module, expected, budget)
    _need(mc.VERSION == 3, "MUTATION_CODEC_BINDING_CHANGED")
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
    path = OLD / "r687-candidate-manifest.json"
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
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if b.get(key) != a[key]] == ["_source_pins"]
    assert set(b) - set(a) == {"_source_batch_paths_original", "_source_batch_binding_original", "_source_pin_batch_original"}
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    assert new.replace(AFTER.encode().replace(b"\n", eol), BEFORE.encode().replace(b"\n", eol), 1) == old
    same = 0
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        compile(target.read_bytes(), str(target), "exec")
        if path.name != "personal_authority_store.py":
            assert target.read_bytes() == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        assert raw.replace(b"r691", b"r687").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "source_directory_batch_static_only", "original_inputs": inputs,
            "changed_definitions": ["_source_pins"], "new_definitions": sorted(set(b) - set(a)),
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
            "store_inverse_bytes_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    before, after = BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol)
    assert old.count(before) == 1
    new = old.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r687", b"r691").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile=str(OLD_PACKAGE / target.name), tofile=str(target))).encode())
    write_new(NEW / "README.md", b"# r691\n\nOne fixed source-pin batch owns one genuine directory and three independent original fileholds. Every file read/SHA, budget, binding and original close remains. Unknown helpers or different parents keep the original three-acquisition path. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_text(encoding="utf-8")) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r691-candidate-manifest.json"
    json_new(path, {"state": "source_directory_batch_frozen_static_only", "source_files": [fact(p) for p in sources],
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
