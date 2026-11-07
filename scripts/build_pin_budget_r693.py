"""Build a fresh candidate; no product imports or runtime execution."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r691"
NEW = ROOT / "data/t16-runtime-origin-graph-r693"
OLD_PACKAGE = OLD / "authority_runtime_origin_r691"
PACKAGE = NEW / "authority_runtime_origin_r693"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
BEFORE = '''        record = _pin_record_original(self)
        _budget_record_original(budget)
        _need(budget.namespace_owner is record.budget.namespace_owner
            and budget.original_deadline is record.budget.original_deadline,
            "NATIVE_OWNER_BUDGET_CHANGED")
        if record.kind == "pin":
            _held_io._rebind_store_pin_budget_original(record.stack, record.lease, budget)
        record.budget = budget
'''
AFTER = '''        _set_pin_budget_original(self, budget)
'''
HELPERS = '''_PIN_BUDGET_RECORD_ORIGINAL = _budget_record_original
_PIN_BUDGET_BOUND_ORIGINAL = _bound_budget
_PIN_BUDGET_REBIND_ORIGINAL = _held_io._rebind_store_pin_budget_original
_PIN_BUDGET_POLL_ORIGINAL = _held_io._poll_record
_PIN_BUDGET_BIND_ORIGINAL = _held_io._bind_pin_budget_original
_PIN_BUDGET_NEED_ORIGINAL = _need
_PIN_BUDGET_HELD_NEED_ORIGINAL = _held_io._need
_PIN_BUDGET_IDENTITY_ORIGINAL = _pin_stack_identity_binding_original


def _pin_budget_dispatch_original():
    return (_budget_record_original is _PIN_BUDGET_RECORD_ORIGINAL
        and _bound_budget is _PIN_BUDGET_BOUND_ORIGINAL
        and _held_io._rebind_store_pin_budget_original is _PIN_BUDGET_REBIND_ORIGINAL
        and _held_io._poll_record is _PIN_BUDGET_POLL_ORIGINAL
        and _held_io._bind_pin_budget_original is _PIN_BUDGET_BIND_ORIGINAL
        and _need is _PIN_BUDGET_NEED_ORIGINAL
        and _held_io._need is _PIN_BUDGET_HELD_NEED_ORIGINAL
        and _pin_stack_identity_binding_original is _PIN_BUDGET_IDENTITY_ORIGINAL
        and _store_directories_verify_original is _PIN_ENTRY_DIRECTORIES_ORIGINAL)


def _set_pin_budget_original(pin, budget):
    # This fixed entry owns the complete first check. No checked record or
    # caller permission is accepted, and no observation survives this setter.
    eligible = _pin_budget_dispatch_original()
    before = _PIN_RECORDS.get(id(pin))
    shared = (_pin_stack_identity_binding_original(before)
        if eligible and type(before) is _StorePinRecord else None)
    record = _pin_record_original(pin)
    _budget_record_original(budget)
    _need(budget.namespace_owner is record.budget.namespace_owner
        and budget.original_deadline is record.budget.original_deadline,
        "NATIVE_OWNER_BUDGET_CHANGED")
    if record.kind == "pin":
        if (shared is None or record is not before
                or not _pin_budget_dispatch_original()
                or _pin_stack_identity_binding_original(record) is not shared):
            _held_io._rebind_store_pin_budget_original(record.stack, record.lease, budget)
        else:
            # The original first stack check has just returned. Retain the
            # second target-budget check, real lookup, both polls and binding.
            custody = _STACK_RECORDS[id(record.stack)]
            previous = record.budget
            _budget_record_original(budget)
            actual = _held_io._resource_original(custody.scope, record.lease, _held_io._FileLease)
            _held_io._need(actual.resource_kind == "pin" and actual.budget.namespace_clock is custody.clock
                and budget.namespace_owner is custody.budget.namespace_owner
                and budget.original_deadline is custody.budget.original_deadline,
                "OWNED_IO_ACTUAL_PIN_BUDGET_CHANGED")
            _held_io._poll_record(actual)
            _need(actual is shared and record.budget is previous
                and _pin_budget_dispatch_original()
                and _pin_stack_identity_binding_original(record) is shared,
                "NATIVE_ORIGINAL_PIN_REQUIRED")
            actual.budget = _held_io._bind_pin_budget_original(budget, custody.clock)
            _held_io._poll_record(actual)
            _need(record.budget is previous and _pin_budget_dispatch_original()
                and _pin_stack_identity_binding_original(record) is shared,
                "NATIVE_ORIGINAL_PIN_REQUIRED")
    record.budget = budget


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


def inputs():
    manifest = OLD / "r691-candidate-manifest.json"
    assert digest(manifest.read_bytes()) == "44cf3a29dfb21a780301b42ebe727d62434038ae1864ac9cd362994df515283c"
    rows = json.loads(manifest.read_bytes())["source_files"]
    for row in rows:
        assert fact(ROOT / row["path"]) == row
    return rows


def replacements(raw):
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    return [(a.encode().replace(b"\n", eol), b.encode().replace(b"\n", eol)) for a, b in
            ((BEFORE, AFTER), ("class _ClosedStorePin:\n", HELPERS + "class _ClosedStorePin:\n"))]


def definitions(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def validate():
    original = inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = definitions(old), definitions(new)
    assert [key for key in a if a[key] != b[key]] == ["_ClosedStorePin"]
    assert set(b) - set(a) == {"_pin_budget_dispatch_original", "_set_pin_budget_original"}
    restored = new
    for before, after in reversed(replacements(old)):
        assert restored.count(after) == 1
        restored = restored.replace(after, before, 1)
    assert restored == old
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
        assert raw.replace(b"r693", b"r691").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "pin_budget_static_only", "original_inputs": original,
            "changed_definitions": ["_ClosedStorePin.budget.setter"],
            "new_definitions": ["_pin_budget_dispatch_original", "_set_pin_budget_original"],
            "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
            "store_inverse_bytes_equal": True, "consumer_inverse_bytes_equal": True,
            "compiled_without_execution": 20, "product_execution": False}


def prepare():
    original = inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in original:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r691", b"r693").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": original})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True), fromfile="r691", tofile="r693")).encode())
    write_new(NEW / "README.md", b"# r693\n\nFixed Pin budget setter combines one adjacent duplicate Store stack identity check. Real lookup, target budgets, pre/post polls, native binding, reads, accounting and original error/cleanup boundaries remain. Unknown dispatch retains original rebind. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r693-candidate-manifest.json"
    json_new(path, {"state": "pin_budget_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
