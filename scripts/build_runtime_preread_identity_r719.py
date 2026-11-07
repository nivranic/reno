"""Mechanically build a bounded runtime pre-read identity candidate; no product execution."""
from pathlib import Path
import argparse
import ast
import copy
import difflib
import hashlib
import json
import textwrap

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r717"
NEW = ROOT / "data/t16-runtime-origin-graph-r719"
OLD_PACKAGE = OLD / "authority_runtime_origin_r717"
PACKAGE = NEW / "authority_runtime_origin_r719"
OLD_MANIFEST_SHA = "2636ea3697481ec0f4b60f7467c851657a8f259f126c4a893479bd7e7c387de4"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
NS_NAME = "personal_authority_namespace.py"
HELD_NAME = "shared_held_io_r250.py"
ADAPTERS = ("personal_authority_store.py", "personal_origin_observer.py")

HELPERS = '''

# This joint entry owns the original full first stack and the immediately
# following full read sweep. Only that read's first non-lock metadata sample
# shares the just-completed stack; all real reads and after-read samples stay.
_PREREAD_ID_HELD = _held_io
_PREREAD_ID_BASE_BINDING = _runtime_tail_identity_binding_original
_PREREAD_ID_BASE_DISPATCH = _runtime_tail_identity_dispatch_original
_PREREAD_ID_BUDGET_TYPE = _Budget
_PREREAD_ID_CANCEL_TYPE = _ClosedCancellation
_PREREAD_ID_BODY = _verify_closed_namespace_owner_runtime_body_original
_PREREAD_ID_SCOPE = _held_io._verify_scope_original
_PREREAD_ID_FILE = _held_io._verify_file_record
_PREREAD_ID_READ = _held_io._read_owned
_PREREAD_ID_IDENTITY = _held_io._verify_identity_record
_PREREAD_ID_PRIVATE = _held_io._PRIVATE
_PREREAD_ID_HASHLIB = _held_io.hashlib
_PREREAD_ID_SHA = _held_io.hashlib.sha256
_PREREAD_ID_READ_TYPE = _held_io.HeldRead
_PREREAD_ID_READ_SLOTS = tuple((name, _held_io.HeldRead.__dict__[name]) for name in _held_io.HeldRead.__slots__)
_PREREAD_ID_READ_METHODS = tuple((name, _held_io.HeldRead.__dict__.get(name))
    for name in ("__init__", "__new__", "__setattr__", "__getattribute__", "__getattr__"))
_PREREAD_ID_READ_MAP = _held_io.original._BUFFER_READ
_PREREAD_ID_SEEK_MAP = _held_io.original._BUFFER_SEEK
_PREREAD_ID_READ_ITEMS = tuple(_held_io.original._BUFFER_READ.items())
_PREREAD_ID_SEEK_ITEMS = tuple(_held_io.original._BUFFER_SEEK.items())
_PREREAD_ID_CHUNKS = (_held_io._PIN_CHUNK, _held_io._FILEHOLD_CHUNK)
_PREREAD_ID_COUNTER_INDEX = tuple(name for name, _ in _TAIL_ID_SLOTS[5]).index("observed_bytes")
_PREREAD_ID_PREFIX = 10 + len(_TAIL_ID_BINDING_FIELDS) + sum(len(_TAIL_ID_SLOTS[index]) for index in (0, 1, 2, 4))


def _runtime_preread_identity_dispatch_original():
    return (_held_io is _PREREAD_ID_HELD
        and _runtime_tail_identity_binding_original is _PREREAD_ID_BASE_BINDING
        and _runtime_tail_identity_dispatch_original is _PREREAD_ID_BASE_DISPATCH
        and _runtime_preread_identity_binding_original is _PREREAD_ID_BINDING
        and _verify_closed_namespace_owner_runtime_body_original is _PREREAD_ID_BODY
        and all(current is original for current, original in zip((
            globals().get("_ClosedNamespaceAcquisition"), globals().get("_ClosedNamespaceStack"),
            globals().get("_ClosedStackRecord"), globals().get("_ClosedRuntimeDirectoryBinding"),
            vars(_held_io).get("_ScopeRecord"), vars(_held_io).get("_OwnedRecord")), _TAIL_ID_CLASSES))
        and globals().get("_Budget") is _PREREAD_ID_BUDGET_TYPE
        and globals().get("_ClosedCancellation") is _PREREAD_ID_CANCEL_TYPE
        and _runtime_tail_identity_dispatch_original()
        and _held_io._verify_scope_original is _PREREAD_ID_SCOPE
        and _held_io._verify_file_record is _PREREAD_ID_FILE
        and _held_io._read_owned is _PREREAD_ID_READ
        and _held_io._verify_identity_record is _PREREAD_ID_IDENTITY
        and _held_io._PRIVATE is _PREREAD_ID_PRIVATE
        and _held_io.hashlib is _PREREAD_ID_HASHLIB and _held_io.hashlib.sha256 is _PREREAD_ID_SHA
        and _held_io.HeldRead is _PREREAD_ID_READ_TYPE
        and all(_PREREAD_ID_READ_TYPE.__dict__.get(name) is original
            for name, original in _PREREAD_ID_READ_SLOTS + _PREREAD_ID_READ_METHODS)
        and _held_io.original._BUFFER_READ is _PREREAD_ID_READ_MAP
        and _held_io.original._BUFFER_SEEK is _PREREAD_ID_SEEK_MAP
        and type(_PREREAD_ID_READ_MAP) is dict and type(_PREREAD_ID_SEEK_MAP) is dict
        and len(_PREREAD_ID_READ_MAP) == len(_PREREAD_ID_READ_ITEMS)
        and len(_PREREAD_ID_SEEK_MAP) == len(_PREREAD_ID_SEEK_ITEMS)
        and all(_PREREAD_ID_READ_MAP.get(kind) is original for kind, original in _PREREAD_ID_READ_ITEMS)
        and all(_PREREAD_ID_SEEK_MAP.get(kind) is original for kind, original in _PREREAD_ID_SEEK_ITEMS)
        and _held_io._PIN_CHUNK is _PREREAD_ID_CHUNKS[0]
        and _held_io._FILEHOLD_CHUNK is _PREREAD_ID_CHUNKS[1])


def _runtime_preread_identity_binding_original(held, scope):
    if (_runtime_preread_identity_dispatch_original is not _PREREAD_ID_DISPATCH
            or not _runtime_preread_identity_dispatch_original()):
        return None
    actual = _runtime_tail_identity_binding_original(held, scope)
    if actual is None:
        return None
    objects, identities, rows = actual
    width = 1 + len(_TAIL_ID_SLOTS[5])
    if len(objects) != _PREREAD_ID_PREFIX + len(rows) * width:
        return None
    omitted = set()
    for index, (resource, path, handle) in enumerate(rows):
        start = _PREREAD_ID_PREFIX + index * width
        if (objects[start] is not resource or type(resource.observed_bytes) is not int
                or resource.observed_bytes < 0 or type(resource.byte_size) is not int
                or type(resource.maximum) is not int
                or not any(resource.kind is kind for kind, _ in _PREREAD_ID_READ_ITEMS)
                or not any(resource.kind is kind for kind, _ in _PREREAD_ID_SEEK_ITEMS)):
            return None
        if resource.resource_kind == "filehold" and (
                type(resource.payload) is not bytes or type(resource.sha256) is not str):
            return None
        # This is only an observation counter. It changes after each genuine
        # preceding file/lock read, but conveys no custody or identity grant.
        # Every original increment, count, read/hash and post-read check runs.
        counter = start + 1 + _PREREAD_ID_COUNTER_INDEX
        if objects[counter] is not resource.observed_bytes:
            return None
        omitted.add(counter)
    return tuple(value for index, value in enumerate(objects) if index not in omitted), identities, rows


_PREREAD_ID_BINDING = _runtime_preread_identity_binding_original
_PREREAD_ID_DISPATCH = _runtime_preread_identity_dispatch_original
'''

CONSUMER_PREFIX = '''def _runtime_body_preread_identity_original(held, scope):
    held_module = _PREREAD_ID_HELD
    expected = (_runtime_preread_identity_binding_original(held, scope)
        if _runtime_preread_identity_binding_original is _PREREAD_ID_BINDING
        and _runtime_preread_identity_dispatch_original is _PREREAD_ID_DISPATCH else None)
    # The original first complete stack proves its stored path/handle values
    # equal this invocation's actual samples; a binding alone proves nothing.
    _runtime_body_stack_check_original(held.stack, scope)

    def _preread_same():
        if (_runtime_preread_identity_binding_original is not _PREREAD_ID_BINDING
                or _runtime_preread_identity_dispatch_original is not _PREREAD_ID_DISPATCH):
            return False
        current = _runtime_preread_identity_binding_original(held, scope)
        return (expected is not None and current is not None
            and len(current[0]) == len(expected[0])
            and all(actual is original for actual, original in zip(current[0], expected[0]))
            and current[1] == expected[1])

    if expected is None or not _preread_same():
        return _held_io._verify_scope_original(held.stack.scope)

    def _preread_identities(record):
        if _preread_same():
            for original, path, handle in expected[2]:
                if original is record and record.resource_kind == "filehold":
                    return dict(path), dict(handle)
        # The original prefix already completed. Late changes use only its
        # remaining real identity sample, never a repeated poll or read.
        return held_module.original._identities(record)

'''

CLONES = (
    ("held", "_verify_identity_record", "_preread_file_identity"),
    ("held", "_read_owned", "_preread_read_owned"),
    ("held", "_verify_file_record", "_preread_file"),
    ("held", "_verify_scope_original", "_preread_scope"),
)
HELD_NAMES = {"original": "held_module.original", **{name: "held_module." + name for name in (
    "_scope_original", "_need", "_poll_record", "_runtime_directory_verify_original", "_PRIVATE",
    "_runtime_directory_identity_original", "_verify_lock_record", "_verify_identity_record", "_remember_failure",
    "_read_owned", "_verify_file_record", "hashlib", "HeldRead", "_PIN_CHUNK", "_FILEHOLD_CHUNK")}}
BEFORE = '''    _runtime_body_stack_check_original(held.stack, runtime_scope)
    # This original full sweep verifies every original graph/path/handle,
    # bounded actual file SHA/read and lock content with its original polling.
    # No effect call is skipped and no observation survives this invocation.
    _held_io._verify_scope_original(held.stack.scope)
'''
AFTER = '''    # The joint entry retains both original checks, all real SHA/read and
    # lock content. Only each read's duplicate first metadata sample is shared.
    _runtime_body_preread_identity_original(held, runtime_scope)
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
    path = OLD / "r717-candidate-manifest.json"
    assert digest(path.read_bytes()) == OLD_MANIFEST_SHA
    manifest = json.loads(path.read_bytes())
    for row in manifest["source_files"] + manifest["artifacts"]:
        assert fact(ROOT / row["path"]) == row
    return manifest["source_files"]


def nodes(raw):
    return {n.name: n for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def dump(node):
    return ast.dump(node, include_attributes=False)


class Qualify(ast.NodeTransformer):
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in HELD_NAMES:
            return ast.copy_location(ast.parse(HELD_NAMES[node.id], mode="eval").body, node)
        return node


class Unqualify(ast.NodeTransformer):
    def visit_Attribute(self, node):
        value = ast.unparse(node)
        for name, qualified in HELD_NAMES.items():
            if value == qualified:
                return ast.copy_location(ast.Name(id=name, ctx=ast.Load()), node)
        return self.generic_visit(node)

def clone_functions(namespace_raw, held_raw):
    originals = {"namespace": nodes(namespace_raw), "held": nodes(held_raw)}
    output, proofs = [], []
    for source, original_name, new_name in CLONES:
        original = originals[source][original_name]
        clone = copy.deepcopy(original)
        clone.name = new_name
        clone = Qualify().visit(clone)
        counts = {}

        class Route(ast.NodeTransformer):
            identity_calls = 0

            def visit_Call(self, node):
                self.generic_visit(node)
                call = ast.unparse(node.func)
                target = pointer = None
                if new_name == "_preread_file_identity" and call == "held_module.original._identities":
                    node.func = ast.Name(id="_preread_identities", ctx=ast.Load())
                    counts[call] = counts.get(call, 0) + 1
                    return node
                if new_name == "_preread_read_owned" and call == "held_module._verify_identity_record":
                    self.identity_calls += 1
                    if self.identity_calls == 1:
                        target, pointer = "_preread_file_identity", "_PREREAD_ID_IDENTITY"
                elif new_name == "_preread_file" and call == "held_module._read_owned":
                    target, pointer = "_preread_read_owned", "_PREREAD_ID_READ"
                elif new_name == "_preread_scope" and call == "held_module._verify_file_record":
                    target, pointer = "_preread_file", "_PREREAD_ID_FILE"
                if target is not None:
                    counts[call] = counts.get(call, 0) + 1
                    node.func = ast.IfExp(test=ast.parse(call + " is " + pointer, mode="eval").body,
                        body=ast.Name(id=target, ctx=ast.Load()), orelse=node.func)
                return node

        route = Route()
        clone = ast.fix_missing_locations(route.visit(clone))
        expected_counts = ({"held_module.original._identities": 1} if new_name == "_preread_file_identity" else
            {"held_module._verify_identity_record": 1} if new_name == "_preread_read_owned" else
            {"held_module._read_owned": 1} if new_name == "_preread_file" else {"held_module._verify_file_record": 1})
        assert counts == expected_counts, (new_name, counts)
        if new_name == "_preread_read_owned":
            assert route.identity_calls == 2
        reverted = copy.deepcopy(clone)

        class Unroute(ast.NodeTransformer):
            def visit_Call(self, node):
                self.generic_visit(node)
                if isinstance(node.func, ast.IfExp):
                    assert "_PREREAD_ID_" in ast.unparse(node.func.test)
                    node.func = node.func.orelse
                elif ast.unparse(node.func) == "_preread_identities":
                    node.func = ast.parse("held_module.original._identities", mode="eval").body
                return node

        reverted = Unqualify().visit(Unroute().visit(reverted))
        reverted.name = original_name
        assert dump(reverted) == dump(original), original_name
        output.append(clone)
        proofs.append({"source": source, "original": original_name, "local_copy": new_name,
            "original_ast_sha256": digest(dump(original).encode()), "routes": counts,
            "inverse_ast_equal": True,
            "read_second_identity_unmodified": new_name == "_preread_read_owned"})
    return output, proofs


def helper_bytes(namespace_raw, held_raw):
    clones, proofs = clone_functions(namespace_raw, held_raw)
    consumer = CONSUMER_PREFIX + "\n\n".join(textwrap.indent(ast.unparse(n), "    ") for n in clones)
    consumer += "\n\n    return (_preread_scope if _held_io is held_module and _held_io._verify_scope_original is _PREREAD_ID_SCOPE else _held_io._verify_scope_original)(held.stack.scope)\n"
    raw = (HELPERS + "\n\n" + consumer).encode()
    if b"\r\n" in namespace_raw:
        raw = raw.replace(b"\n", b"\r\n")
    return raw, proofs


def counter_projection_proof(namespace_raw, held_raw):
    binding = nodes(namespace_raw)["_runtime_tail_identity_binding_original"]
    owned = nodes(held_raw)["_OwnedRecord"]
    slots = next(ast.literal_eval(row.value) for row in owned.body if isinstance(row, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in row.targets))
    objects = next(row for row in binding.body if isinstance(row, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "objects" for target in row.targets))
    assert isinstance(objects.value, ast.List) and len(objects.value.elts) == 10
    additions = [ast.unparse(row.args[0]) for row in ast.walk(binding)
        if isinstance(row, ast.Call) and ast.unparse(row.func) == "objects.extend"]
    assert additions == ["binding_values + held_values + stack_values + custody_values + io_values", "(resource,) + fields"]
    resource_loop = next(row for row in binding.body if isinstance(row, ast.For)
        and ast.unparse(row.target) == "resource")
    field_source = next(row.value for row in resource_loop.body if isinstance(row, ast.Assign)
        and ast.unparse(row.targets[0]) == "fields")
    assert ast.unparse(field_source) == "tuple((getattr(resource, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[5]))"
    assert slots.count("observed_bytes") == 1
    return {"fixed_prefix_objects": 10,
        "variable_prefix_fields": ["binding_values", "held_values", "stack_values", "custody_values", "io_values"],
        "resource_width": 1 + len(slots), "removed_resource_slot": "observed_bytes",
        "removed_slot_index": slots.index("observed_bytes"),
        "removed_tuple_offset_inside_resource": 1 + slots.index("observed_bytes"),
        "remaining_slots": [name for name in slots if name != "observed_bytes"],
        "original_binding_layout_ast_verified": True}


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    return [(BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol))]


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / NS_NAME).read_bytes()
    new = (PACKAGE / NS_NAME).read_bytes()
    a, b = nodes(old), nodes(new)
    added = {"_runtime_preread_identity_dispatch_original", "_runtime_preread_identity_binding_original",
        "_runtime_body_preread_identity_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if dump(b[key]) != dump(a[key])] == ["_verify_closed_namespace_owner_runtime_body_original"]
    helpers, proofs = helper_bytes(old, (OLD_PACKAGE / HELD_NAME).read_bytes())
    assert new.endswith(helpers)
    inverse = new[:-len(helpers)]
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
    consumer = b["_runtime_body_preread_identity_original"]
    actual_locals = {n.name: n for n in consumer.body if isinstance(n, ast.FunctionDef)}
    clones, _ = clone_functions(old, (OLD_PACKAGE / HELD_NAME).read_bytes())
    for clone in clones:
        assert dump(actual_locals[clone.name]) == dump(clone)
    same, adapters, hashes = 0, [], [(digest(new), digest(old))]
    for path in OLD_PACKAGE.glob("*.py"):
        target = PACKAGE / path.name
        raw = target.read_bytes()
        compile(raw, str(target), "exec")
        if path.name in ADAPTERS:
            assert raw.replace(digest(new).encode(), digest(old).encode()) == path.read_bytes()
            assert {key: dump(value) for key, value in nodes(raw).items()} == {
                key: dump(value) for key, value in nodes(path.read_bytes()).items()}
            adapters.append(path.name)
            hashes.append((digest(raw), digest(path.read_bytes())))
        elif path.name != NS_NAME:
            assert raw == path.read_bytes()
            same += 1
    for name in NAMES:
        raw = (NEW / name).read_bytes()
        compile(raw, name, "exec")
        inverse = raw.replace(b"r719", b"r717")
        for current, previous in hashes:
            inverse = inverse.replace(current.encode(), previous.encode())
        assert inverse == (OLD / name).read_bytes()
    return {"state": "runtime_preread_identity_static_only", "original_inputs": inputs,
        "changed_definitions": ["_verify_closed_namespace_owner_runtime_body_original"], "new_definitions": sorted(added),
        "unchanged_namespace_definitions": len(a) - 1, "unchanged_support_modules": same,
        "namespace_sha_only_adapters": adapters, "mechanical_extractions": proofs,
        "binding_counter_projection": counter_projection_proof(old, (OLD_PACKAGE / HELD_NAME).read_bytes()),
        "compiled_without_execution": 20, "consumer_inverse_bytes_equal": True,
        "namespace_inverse_bytes_equal": True, "product_execution": False}


def prepare():
    inputs = original_inputs()
    NEW.mkdir()
    PACKAGE.mkdir()
    for row in inputs:
        path = ROOT / row["path"]
        write_new(PACKAGE / path.name if path.parent == OLD_PACKAGE else NEW / path.name, path.read_bytes())
    target = PACKAGE / NS_NAME
    old = target.read_bytes()
    new = old
    for before, after in replacements(old):
        assert new.count(before) == 1
        new = new.replace(before, after, 1)
    helpers, proofs = helper_bytes(old, (OLD_PACKAGE / HELD_NAME).read_bytes())
    new += helpers
    target.write_bytes(new)
    hashes = [(digest(old), digest(new))]
    adapter_diff = []
    for name in ADAPTERS:
        path = PACKAGE / name
        prior = path.read_bytes()
        assert prior.count(digest(old).encode()) == 1
        current = prior.replace(digest(old).encode(), digest(new).encode())
        path.write_bytes(current)
        hashes.append((digest(prior), digest(current)))
        adapter_diff.extend(difflib.unified_diff(prior.decode().splitlines(True), current.decode().splitlines(True),
            fromfile="r717/" + name, tofile="r719/" + name))
    for name in NAMES:
        p = NEW / name
        raw = p.read_bytes().replace(b"r717", b"r719")
        for previous, current in hashes:
            raw = raw.replace(previous.encode(), current.encode())
        p.write_bytes(raw)
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    json_new(NEW / "extraction-ast.json", {"mechanical_extractions": proofs})
    write_new(NEW / "namespace.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r717", tofile="r719")).encode())
    write_new(NEW / "sha-adapters.diff", "".join(adapter_diff).encode())
    write_new(NEW / "README.md", b"# r719\n\nA private joint entry owns the original complete body stack and its immediately following full read sweep. Only each filehold read's first path/handle observation is shared; every original poll, graph, directory, actual read/hash/counter, independent lock and post-read identity check remains. Four Held helpers are mechanically extracted with AST inverse proofs; Held source is unchanged. Only each resource's observed_bytes observation counter is excluded from the local identity binding, because preceding real reads advance it and it conveys no identity permission. Unknown/late dispatch continues only the original remaining path. No caller proof, cross-effect observation or new scope. Static preparation only.\n")
    print(json.dumps({"namespace": fact(target), "store": fact(PACKAGE / ADAPTERS[0]),
        "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json",
        "namespace.diff", "sha-adapters.diff", "extraction-ast.json", "README.md")]
    path = NEW / "r719-candidate-manifest.json"
    json_new(path, {"state": "runtime_preread_identity_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
