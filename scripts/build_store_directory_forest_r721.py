"""File-only construction of the isolated same-sweep Store directory forest."""
from pathlib import Path
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r719"
NEW = ROOT / "data/t16-runtime-origin-graph-r721"
OLD_PACKAGE = OLD / "authority_runtime_origin_r719"
PACKAGE = NEW / "authority_runtime_origin_r721"
OLD_MANIFEST_SHA = "963a8bf227a437c6472bd55733ad5f1b64fe7f80c2b2ef515239da080bd64b0f"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")

HELPERS = '''

# A Store sweep owns this graph and discards it on return. Actual records,
# never path or numeric-handle equality, select the recursive physical leaves.
_FOREST_NS = ns
_FOREST_SAFE = safe
_FOREST_CUSTODY_TYPE = _StoreStackRecord
_FOREST_CUSTODY_DICT = _StoreStackRecord.__dict__["__dict__"]
_FOREST_CUSTODY_FIELDS = ("original", "execution_owner", "directories", "closed")
_FOREST_RECORD_TYPE = ns._ClosedDirectoryRecord
_FOREST_RECORD_SLOTS = tuple((name, ns._ClosedDirectoryRecord.__dict__[name])
    for name in ns._ClosedDirectoryRecord.__slots__)
_FOREST_DIRECTORY_TYPE = safe._Directory
_FOREST_DIRECTORY_DICT = safe._Directory.__dict__["__dict__"]
_FOREST_DIRECTORY_FIELDS = ("path", "base", "parent", "create", "handles", "identities", "fd")
_FOREST_PATH_TYPE = ns.lifecycle._PROFILE_PATH_TYPE
_FOREST_TASK_TYPE = ns.asyncio.Task
_FOREST_TASK_EQ = ns.asyncio.Task.__eq__
_FOREST_OWNER = ns._owner
_FOREST_NEED = _need
_FOREST_SWEEP = _STORE_DIRECTORY_SWEEP
_FOREST_STORE_DISPATCH = _store_directory_sweep_dispatch_original
_FOREST_ROW_MATCH = ns._closed_directory_batch_row_matches_original
_FOREST_MISSING = object()


def _store_directory_forest_dispatch_original():
    return (ns is _FOREST_NS and safe is _FOREST_SAFE
        and _StoreStackRecord is _FOREST_CUSTODY_TYPE
        and vars(ns).get("_ClosedDirectoryRecord") is _FOREST_RECORD_TYPE
        and safe._Directory is _FOREST_DIRECTORY_TYPE
        and _store_directory_sweep_dispatch_original is _FOREST_STORE_DISPATCH
        and _STORE_DIRECTORY_SWEEP is _FOREST_SWEEP and _need is _FOREST_NEED
        and vars(ns).get("_owner") is _FOREST_OWNER
        and vars(ns).get("_closed_directory_batch_row_matches_original") is _FOREST_ROW_MATCH
        and _store_directory_sweep_dispatch_original()
        and type(_STACK_RECORDS) is dict and type(ns._CLOSED_DIRECTORY_RECORDS) is dict
        and _FOREST_TASK_TYPE.__eq__ is _FOREST_TASK_EQ
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            for cls in (_FOREST_CUSTODY_TYPE, _FOREST_RECORD_TYPE, _FOREST_DIRECTORY_TYPE))
        and _FOREST_CUSTODY_TYPE.__dict__.get("__dict__") is _FOREST_CUSTODY_DICT
        and all(name not in _FOREST_CUSTODY_TYPE.__dict__ for name in _FOREST_CUSTODY_FIELDS)
        and _FOREST_DIRECTORY_TYPE.__dict__.get("__dict__") is _FOREST_DIRECTORY_DICT
        and all(name not in _FOREST_DIRECTORY_TYPE.__dict__ for name in _FOREST_DIRECTORY_FIELDS)
        and all(_FOREST_RECORD_TYPE.__dict__.get(name) is original for name, original in _FOREST_RECORD_SLOTS))


def _store_directory_forest_plan_original(custody):
    # Admission is not an extra rejecting contract. Unknown/duplicate/malformed
    # graphs return to the unchanged ordinary sweep and its original errors.
    if (_store_directory_forest_dispatch_original is not _FOREST_DISPATCH
            or not _store_directory_forest_dispatch_original()
            or type(custody) is not _FOREST_CUSTODY_TYPE):
        return None
    values = object.__getattribute__(custody, "__dict__")
    if any(name not in values for name in _FOREST_CUSTODY_FIELDS):
        return None
    acquired, stack = values["directories"], values["original"]
    owner = values["execution_owner"]
    if (type(acquired) is not tuple or values["closed"] is not False
            or _STACK_RECORDS.get(id(stack)) is not custody
            or type(owner) is not tuple or len(owner) != 3
            or type(owner[0]) is not int or type(owner[1]) is not int
            or (owner[2] is not None and type(owner[2]) is not _FOREST_TASK_TYPE)
            or owner != ns._owner() or len({id(record) for record in acquired}) != len(acquired)):
        return None
    rows, by_record, chains = [], {}, []
    for first in acquired:
        chain, seen, record = [], set(), first
        while record is not None:
            if type(record) is not _FOREST_RECORD_TYPE or id(record) in seen:
                return None
            fields = tuple(getattr(record, name, _FOREST_MISSING) for name, _ in _FOREST_RECORD_SLOTS)
            if any(value is _FOREST_MISSING for value in fields):
                return None
            selected_owner = record.owner
            if (type(selected_owner) is not tuple or len(selected_owner) != 3
                    or type(selected_owner[0]) is not int or type(selected_owner[1]) is not int
                    or (selected_owner[2] is not None and type(selected_owner[2]) is not _FOREST_TASK_TYPE)
                    or selected_owner != ns._owner() or record.entered is not True or record.closed is not False
                    or type(record.attempted) is not set or type(record.released) is not set
                    or record.attempted or record.released):
                return None
            seen.add(id(record))
            directory = record.original
            if (type(directory) is not _FOREST_DIRECTORY_TYPE
                    or ns._CLOSED_DIRECTORY_RECORDS.get(id(directory)) is not record):
                return None
            directory_values = object.__getattribute__(directory, "__dict__")
            if set(directory_values) != set(_FOREST_DIRECTORY_FIELDS):
                return None
            if (type(directory.path) is not _FOREST_PATH_TYPE or type(directory.base) is not _FOREST_PATH_TYPE
                    or type(directory.create) is not bool or type(directory.handles) is not list
                    or type(directory.identities) is not list or type(record.handles) is not list
                    or type(record.native) is not list or type(record.initial_fields) is not tuple
                    or len(record.initial_fields) != 4 or type(record.identity_tuple) is not tuple
                    or directory.handles is not record.initial_handle_list
                    or directory.identities is not record.initial_identity_list
                    or not all(actual is original for actual, original in zip(
                        (directory.path, directory.base, directory.parent, directory.create), record.initial_fields))
                    or any(type(value) is not int for value in directory.handles + record.handles)
                    or any(type(row) is not tuple or len(row) != 2
                        or any(type(value) is not int for value in row) for row in record.native)
                    or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not _FOREST_PATH_TYPE
                        or type(row[1]) is not int or type(row[2]) is not int
                        for row in tuple(directory.identities) + record.identity_tuple)
                    or tuple(directory.handles) != tuple(record.handles)
                    or tuple(directory.identities) != record.identity_tuple):
                return None
            if os.name == "nt":
                if directory.fd is not None:
                    return None
            elif (type(directory.fd) is not int or not record.handles or directory.fd != record.handles[-1]):
                return None
            parent = record.initial_fields[2]
            parent_record = None if parent is None else ns._CLOSED_DIRECTORY_RECORDS.get(id(parent))
            if (parent is not None and (type(parent) is not _FOREST_DIRECTORY_TYPE
                    or type(parent_record) is not _FOREST_RECORD_TYPE)):
                return None
            if parent is not None:
                parent_fields = object.__getattribute__(parent, "__dict__")
                if (type(parent_fields.get("path")) is not _FOREST_PATH_TYPE
                        or getattr(parent_record, "original", _FOREST_MISSING) is not parent
                        or directory.path.parent != parent.path):
                    return None
            # Exact fourteen-field actual directory row used by Namespace's
            # original graph, including expected lists AND their current values.
            row = (record, directory, parent, parent_record, record.initial_fields,
                record.initial_handle_list, record.initial_identity_list, record.identity_tuple,
                record.handles, record.native, tuple(record.handles), tuple(record.native), directory.fd, record.owner)
            prior = by_record.get(id(record))
            if prior is None:
                by_record[id(record)] = row
                rows.append(row)
            elif not ns._closed_directory_batch_row_matches_original(prior, row):
                return None
            chain.append(record)
            record = parent_record
        chains.append(tuple(chain))
    ancestor_ids = {id(record) for chain in chains for record in chain[1:]}
    selected = tuple(chain for chain in chains if id(chain[0]) not in ancestor_ids)
    covered = {id(record) for chain in selected for record in chain}
    if (covered != {id(row[0]) for row in rows}
            or not all(id(record) in covered for record in acquired)):
        return None
    # Shared parents across different leaves intentionally still recur. This
    # removes redundant acquired interior roots, not every repeated tree node.
    return custody, acquired, None, tuple(rows), tuple(chains), tuple(chain[0] for chain in selected)


def _store_directory_forest_matches_original(before, after):
    return (after is not None and after[0] is before[0] and after[1] is before[1]
        and after[2] is before[2] and len(after[3]) == len(before[3])
        and all(ns._closed_directory_batch_row_matches_original(first, last)
            for first, last in zip(before[3], after[3], strict=True))
        and len(after[4]) == len(before[4])
        and all(len(first) == len(last) and all(a is b for a, b in zip(first, last, strict=True))
            for first, last in zip(before[4], after[4], strict=True))
        and len(after[5]) == len(before[5])
        and all(actual is original for actual, original in zip(after[5], before[5], strict=True)))


_FOREST_DISPATCH = _store_directory_forest_dispatch_original
_FOREST_PLAN = _store_directory_forest_plan_original
_FOREST_MATCHES = _store_directory_forest_matches_original
'''

BEFORE = '''    batched = _store_directory_sweep_dispatch_original()
    if batched:
        # All real records and native handles are verified separately; only
        # repeated path stats share this one original sweep's fresh dictionary.
        _STORE_DIRECTORY_SWEEP(directories)
'''
AFTER = '''    batched = _store_directory_sweep_dispatch_original()
    forest = None
    if batched:
        sweep = _STORE_DIRECTORY_SWEEP
        forest = (_store_directory_forest_plan_original(record)
            if _store_directory_forest_plan_original is _FOREST_PLAN else None)
        # Every selected leaf keeps the original recursive verifier and this
        # sweep's fresh path dictionary, including all external ancestors.
        sweep(directories if forest is None else forest[5])
'''
BEFORE_TAIL = '''    if batched:
        _need(_store_directory_sweep_dispatch_original(), "NATIVE_ORIGINAL_DIRECTORY_CHANGED")


def _stage_stack_original(stack):
'''
AFTER_TAIL = '''    if batched:
        _need(_store_directory_sweep_dispatch_original(), "NATIVE_ORIGINAL_DIRECTORY_CHANGED")
    if forest is not None:
        # Preserve the complete original first/stage-first/custody/dispatch
        # tail above before any additional graph observation or failure.
        _need(_store_directory_forest_plan_original is _FOREST_PLAN
            and _store_directory_forest_matches_original is _FOREST_MATCHES
            and _store_directory_forest_matches_original(forest, _store_directory_forest_plan_original(record)),
            "NATIVE_ORIGINAL_DIRECTORY_CHANGED")


def _stage_stack_original(stack):
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
    path = OLD / "r719-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    manifest = json.loads(path.read_bytes())
    for row in manifest["source_files"] + manifest["artifacts"]:
        assert fact(ROOT / row["path"]) == row
    return manifest["source_files"]


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    return [(before.encode().replace(b"\n", eol), after.encode().replace(b"\n", eol))
        for before, after in ((BEFORE, AFTER), (BEFORE_TAIL, AFTER_TAIL))]


def helper_bytes(old):
    return HELPERS.encode().replace(b"\n", b"\r\n" if b"\r\n" in old else b"\n")


def nodes(raw):
    return {node.name: node for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def dump(value):
    return ast.dump(value, include_attributes=False)


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / "personal_authority_store.py").read_bytes()
    new = (PACKAGE / "personal_authority_store.py").read_bytes()
    a, b = nodes(old), nodes(new)
    added = {"_store_directory_forest_dispatch_original", "_store_directory_forest_plan_original", "_store_directory_forest_matches_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if dump(b[key]) != dump(a[key])] == ["_store_directories_verify_original"]
    helpers = helper_bytes(old)
    assert new.endswith(helpers)
    inverse = new[:-len(helpers)]
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
    original, current = a["_store_directories_verify_original"], b["_store_directories_verify_original"]
    assert [dump(row) for row in current.body[:6]] == [dump(row) for row in original.body[:6]]
    assert [dump(row) for row in current.body[8:12]] == [dump(row) for row in original.body[7:11]]
    assert dump(current.body[7].orelse[0]) == dump(original.body[6].orelse[0])
    namespace = nodes((OLD_PACKAGE / "personal_authority_namespace.py").read_bytes())
    original_graph = namespace["_closed_stack_directory_graph_original"]
    plan = b["_store_directory_forest_plan_original"]
    def assignment(node, name):
        return next(row for row in ast.walk(node) if isinstance(row, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in row.targets))
    assert dump(assignment(plan, "row")) == dump(assignment(original_graph, "row"))
    original_sweep = namespace["_closed_stack_directories_verify_batch_original"]
    for name in ("ancestor_ids", "selected", "covered"):
        assert dump(assignment(plan, name)) == dump(assignment(original_sweep, name))
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
        assert raw.replace(b"r721", b"r719").replace(digest(new).encode(), digest(old).encode()) == (OLD / name).read_bytes()
    return {"state": "store_directory_forest_static_only", "original_inputs": inputs,
        "changed_definitions": ["_store_directories_verify_original"], "new_definitions": sorted(added),
        "unchanged_original_definitions": len(a) - 1, "unchanged_support_modules": same,
        "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
        "store_inverse_bytes_equal": True, "original_prefix_ast_equal": True,
        "original_post_tail_ast_equal": True, "unknown_directory_loop_ast_equal": True,
        "namespace_fourteen_field_row_ast_equal": True,
        "namespace_leaf_selection_ast_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / "personal_authority_store.py"
    old = target.read_bytes()
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    new += helper_bytes(old)
    target.write_bytes(new)
    for name in NAMES:
        p = NEW / name
        p.write_bytes(p.read_bytes().replace(b"r719", b"r721").replace(digest(old).encode(), digest(new).encode()))
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    write_new(NEW / "store.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r719", tofile="r721")).encode())
    write_new(NEW / "README.md", b"# r721\n\nThe same Store directory sweep selects actual-record leaves only after a bounded original-dispatch graph admission. Each leaf keeps the original recursive verifier, native observations and fresh path dictionary, including Namespace parents outside the Store tuple. A long acquired chain no longer restarts every interior root; branched shared ancestors still recur once per selected leaf. Duplicate/unknown/malformed input keeps the original sweep. Original prefix and complete first/stage-first/custody/dispatch tail precede the new after-graph check. No changes to Namespace, lookup ancestry, reads, budget, locks or close. Static preparation only.\n")
    print(json.dumps({"store": fact(target), "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json", "store.diff", "README.md")]
    path = NEW / "r721-candidate-manifest.json"
    json_new(path, {"state": "store_directory_forest_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
