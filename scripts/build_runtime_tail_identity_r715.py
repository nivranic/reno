"""Mechanically build a bounded runtime-tail identity candidate; no product execution."""
from pathlib import Path
import argparse
import ast
import copy
import difflib
import hashlib
import json
import textwrap

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/t16-runtime-origin-graph-r713"
NEW = ROOT / "data/t16-runtime-origin-graph-r715"
OLD_PACKAGE = OLD / "authority_runtime_origin_r713"
PACKAGE = NEW / "authority_runtime_origin_r715"
OLD_MANIFEST_SHA = "8ee449b03c90b3b39b2efa643bc9716de45aa13345cc0a87757d9604ca9fbff6"
NAMES = ("test_store_stage_wiring.py", "test_whole_append.py", "test_joint_sql_boundary.py", "test_policy_scope.py")
NS_NAME = "personal_authority_namespace.py"
HELD_NAME = "shared_held_io_r250.py"
ADAPTERS = ("personal_authority_store.py", "personal_origin_observer.py")

HELPERS = '''

# One fixed body tail owns its successful first stack and these local objects.
# The ordinary stack/write/held helpers remain unchanged and accept no proof.
_TAIL_ID_HELD = _held_io
_TAIL_ID_ORIGINAL = _closed_io
_TAIL_ID_LIFECYCLE = lifecycle
_TAIL_ID_REQUIRE_PROFILE = _require_profile
_TAIL_ID_REQUIRE_GUARD = lifecycle.require_guard
_TAIL_ID_GUARD_STATE = lifecycle._verify_guard_plan_state_original
_TAIL_ID_NS_CALLS = tuple((name, globals()[name]) for name in (
    "_runtime_body_write_check_original", "_require_runtime_namespace_write_original",
    "_runtime_body_stack_check_original", "_closed_stack_check_original",
    "_runtime_body_binding_original", "_runtime_body_profile_original",
    "_runtime_directory_check_original", "_runtime_directory_binding_original",
    "_runtime_directory_replace_original", "_closed_stack_custody_original",
    "_closed_stack_capture_first_original", "_closed_stack_directories_verify_batch_original",
    "_closed_runtime_directory_record_verify_original",
    "_closed_effect_ledger_original", "_closed_stack_directory_graph_original",
    "_runtime_directory_graph_matches_original", "_store_original_method", "_need", "_owner",
    "_verify_closed_namespace_owner_runtime_native", "_closed_runtime_directory_observation_original",
    "_runtime_directory_begin_original", "_runtime_directory_finish_original"))
_TAIL_ID_HELD_CALLS = tuple((name, getattr(_held_io, name)) for name in (
    "_verify_scope_identity_original", "_verify_identity_record", "_verify_lock_record",
    "_scope_original", "_poll_record", "_remember_failure", "_need",
    "_runtime_directory_verify_original", "_runtime_directory_identity_original"))
_TAIL_ID_RUNTIME_HELD_CALLS = tuple((name, globals()[name]) for name in (
    "_closed_runtime_held_directory_verify_original", "_closed_runtime_held_directory_identity_original",
    "_runtime_directory_current_original"))
_TAIL_ID_GRAPH = _closed_io._verify_graph
_TAIL_ID_ROUTE_FUNCTIONS = (_require_runtime_namespace_write_original, _runtime_body_stack_check_original,
    _closed_stack_check_original, _held_io._verify_scope_identity_original, _held_io._verify_identity_record)
_TAIL_ID_SCOPE_CURRENT = _RUNTIME_DIRECTORY_CURRENT
_TAIL_ID_SCOPE_CONTEXT = _RUNTIME_DIRECTORY_CONTEXT
_TAIL_ID_STACK_CHECK = _ClosedNamespaceStack.__dict__["check"]
_TAIL_ID_BUDGET_CHECK = _Budget.__dict__["check"]
_TAIL_ID_CANCEL_CALL = _ClosedCancellation.__dict__["__call__"]
_TAIL_ID_CLASSES = (_ClosedNamespaceAcquisition, _ClosedNamespaceStack, _ClosedStackRecord,
    _ClosedRuntimeDirectoryBinding, _held_io._ScopeRecord, _held_io._OwnedRecord)
_TAIL_ID_SLOTS = tuple(tuple((name, cls.__dict__[name]) for name in cls.__slots__) for cls in _TAIL_ID_CLASSES)
_TAIL_ID_BINDING_FIELDS = tuple(name for name, _ in _TAIL_ID_SLOTS[3]
    if name not in ("current_token", "context_token"))
_TAIL_ID_MISSING = object()


def _runtime_tail_identity_dispatch_original():
    # Low-level stat observation/fault seams remain usable. Their current
    # identity is compared locally, never received from an application caller.
    return (_held_io is _TAIL_ID_HELD and _closed_io is _TAIL_ID_ORIGINAL
        and lifecycle is _TAIL_ID_LIFECYCLE and _held_io.original is _TAIL_ID_ORIGINAL
        and _require_profile is _TAIL_ID_REQUIRE_PROFILE
        and lifecycle.require_guard is _TAIL_ID_REQUIRE_GUARD
        and lifecycle._verify_guard_plan_state_original is _TAIL_ID_GUARD_STATE
        and _runtime_tail_identity_binding_original is _TAIL_ID_BINDING
        and all(globals().get(name) is original for name, original in _TAIL_ID_NS_CALLS)
        and all(vars(_held_io).get(name) is original for name, original in _TAIL_ID_HELD_CALLS)
        and all(globals().get(name) is original for name, original in _TAIL_ID_RUNTIME_HELD_CALLS)
        and _closed_io._verify_graph is _TAIL_ID_GRAPH
        and _RUNTIME_DIRECTORY_CURRENT is _TAIL_ID_SCOPE_CURRENT
        and _RUNTIME_DIRECTORY_CONTEXT is _TAIL_ID_SCOPE_CONTEXT
        and _ClosedNamespaceStack.__dict__.get("check") is _TAIL_ID_STACK_CHECK
        and _Budget.__dict__.get("check") is _TAIL_ID_BUDGET_CHECK
        and _ClosedCancellation.__dict__.get("__call__") is _TAIL_ID_CANCEL_CALL
        and all(actual is original for actual, original in zip((
            _ClosedNamespaceAcquisition, _ClosedNamespaceStack, _ClosedStackRecord,
            _ClosedRuntimeDirectoryBinding, _held_io._ScopeRecord, _held_io._OwnedRecord), _TAIL_ID_CLASSES))
        and all("__getattribute__" not in cls.__dict__ and "__getattr__" not in cls.__dict__
            and all(cls.__dict__.get(name) is original for name, original in slots)
            for cls, slots in zip(_TAIL_ID_CLASSES, _TAIL_ID_SLOTS))
        and type(_CLOSED_NAMESPACE_OWNERS) is dict and type(_CLOSED_STACK_RECORDS) is dict
        and type(_RUNTIME_DIRECTORY_SCOPES) is dict and type(_held_io._SCOPES) is dict)


def _runtime_tail_identity_binding_original(held, scope):
    if (_runtime_tail_identity_dispatch_original is not _TAIL_ID_DISPATCH
            or not _runtime_tail_identity_dispatch_original()
            or type(held) is not _TAIL_ID_CLASSES[0]
            or _RUNTIME_DIRECTORY_CURRENT.get() is not scope or _RUNTIME_DIRECTORY_CONTEXT.get() is not scope):
        return None
    actual = _RUNTIME_DIRECTORY_SCOPES.get(id(scope))
    if type(actual) is not _TAIL_ID_CLASSES[3]:
        return None
    binding_values = tuple(getattr(actual, name, _TAIL_ID_MISSING) for name in _TAIL_ID_BINDING_FIELDS)
    held_values = tuple(getattr(held, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[0])
    if any(value is _TAIL_ID_MISSING for value in binding_values + held_values):
        return None
    stack = held.stack
    custody = _CLOSED_STACK_RECORDS.get(id(stack))
    if type(stack) is not _TAIL_ID_CLASSES[1] or type(custody) is not _TAIL_ID_CLASSES[2]:
        return None
    stack_values = tuple(getattr(stack, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[1])
    custody_values = tuple(getattr(custody, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[2])
    if any(value is _TAIL_ID_MISSING for value in stack_values + custody_values):
        return None
    if (actual.original is not scope or actual.held is not held or actual.custody is not custody
            or type(actual.phase) is not str or actual.phase != "reading" or actual.guard_plan is None
            or custody.first is not None or _CLOSED_NAMESPACE_OWNERS.get(id(held.original)) is not held
            or custody.original is not stack or stack.owner is not held.original
            or stack.scope is not custody.scope or stack.profile is not custody.profile
            or held.profile is not custody.profile or stack.guard is not custody.guard
            or held.storage_guard is not custody.guard or stack.budget is not custody.budget
            or held.budget is not custody.budget or actual.budget is not custody.budget):
        return None
    io_scope = _held_io._SCOPES.get(id(custody.scope))
    if type(io_scope) is not _TAIL_ID_CLASSES[4] or io_scope is not actual.io_scope:
        return None
    io_values = tuple(getattr(io_scope, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[4])
    if (any(value is _TAIL_ID_MISSING for value in io_values) or io_scope.first is not None
            or type(io_scope.resources) is not list or type(actual.resources) is not tuple
            or len(io_scope.resources) != len(actual.resources)
            or not all(value is original for value, original in zip(io_scope.resources, actual.resources))):
        return None
    objects = [held, scope, stack, custody, io_scope, _CLOSED_NAMESPACE_OWNERS,
        _CLOSED_STACK_RECORDS, _RUNTIME_DIRECTORY_SCOPES, _held_io._SCOPES, _held_io.original._identities]
    objects.extend(binding_values + held_values + stack_values + custody_values + io_values)
    identities = []
    rows = []
    for resource in io_scope.resources:
        if type(resource) is not _TAIL_ID_CLASSES[5]:
            return None
        fields = tuple(getattr(resource, name, _TAIL_ID_MISSING) for name, _ in _TAIL_ID_SLOTS[5])
        if any(value is _TAIL_ID_MISSING for value in fields):
            return None
        if (type(resource.resource_kind) is not str or resource.resource_kind not in {"filehold", "lock"}
                or resource.scope is not custody.scope or resource.finished or resource.failure is not None
                or type(resource.path_identity) is not dict or type(resource.handle_identity) is not dict):
            return None
        for value in (resource.path_identity, resource.handle_identity):
            if not all(type(key) is str and type(item) in {str, int} for key, item in value.items()):
                return None
        path = tuple(sorted(resource.path_identity.items()))
        handle = tuple(sorted(resource.handle_identity.items()))
        identities.append((path, handle))
        rows.append((resource, path, handle))
        objects.extend((resource,) + fields)
    return tuple(objects), tuple(identities), tuple(rows)


_TAIL_ID_BINDING = _runtime_tail_identity_binding_original
_TAIL_ID_DISPATCH = _runtime_tail_identity_dispatch_original
'''

CONSUMER_PREFIX = '''def _runtime_body_tail_identity_original(held, scope, *, writing):
    held_module = _TAIL_ID_HELD
    expected = (_runtime_tail_identity_binding_original(held, scope)
        if writing is True and _runtime_tail_identity_binding_original is _TAIL_ID_BINDING
        and _runtime_tail_identity_dispatch_original is _TAIL_ID_DISPATCH else None)
    # The complete original first stack is always invoked, including failures.
    _runtime_body_stack_check_original(held.stack, scope)
    if not writing:
        return

    def _tail_same():
        if (_runtime_tail_identity_binding_original is not _TAIL_ID_BINDING
                or _runtime_tail_identity_dispatch_original is not _TAIL_ID_DISPATCH):
            return False
        current = _runtime_tail_identity_binding_original(held, scope)
        return (expected is not None and current is not None
            and len(current[0]) == len(expected[0])
            and all(actual is original for actual, original in zip(current[0], expected[0]))
            and current[1] == expected[1])

    if expected is None or not _tail_same():
        return _runtime_body_write_check_original(held, scope)

    def _tail_identities(record):
        if _tail_same():
            for original, path, handle in expected[2]:
                if original is record and record.resource_kind == "filehold":
                    return dict(path), dict(handle)
        # A late change keeps only the remaining real identity observation.
        return held_module.original._identities(record)

'''

CLONES = (
    ("held", "_verify_identity_record", "_tail_file_identity"),
    ("held", "_verify_scope_identity_original", "_tail_scope_identity"),
    ("namespace", "_closed_stack_check_original", "_tail_closed_stack"),
    ("namespace", "_runtime_body_stack_check_original", "_tail_body_stack"),
    ("namespace", "_require_runtime_namespace_write_original", "_tail_write_gate"),
    ("namespace", "_runtime_body_write_check_original", "_tail_body_write"),
)
HELD_NAMES = {"original": "held_module.original", **{name: "held_module." + name for name in (
    "_scope_original", "_need", "_poll_record", "_runtime_directory_verify_original", "_PRIVATE",
    "_runtime_directory_identity_original", "_verify_lock_record", "_verify_identity_record", "_remember_failure")}}
BEFORE = '''    _runtime_body_stack_check_original(held.stack, runtime_scope)
    if writing:
        _runtime_body_write_check_original(held, runtime_scope)
    return held
'''
AFTER = '''    _runtime_body_tail_identity_original(held, runtime_scope, writing=writing)
    return held
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
    path = OLD / "r713-candidate-manifest.json"
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
        if source == "held":
            clone = Qualify().visit(clone)
        counts = {}

        class Route(ast.NodeTransformer):
            def visit_Call(self, node):
                self.generic_visit(node)
                call = ast.unparse(node.func)
                target = None
                if new_name == "_tail_file_identity" and call == "held_module.original._identities":
                    node.func = ast.Name(id="_tail_identities", ctx=ast.Load())
                    counts[call] = counts.get(call, 0) + 1
                    return node
                route_index = None
                if new_name == "_tail_scope_identity" and call == "held_module._verify_identity_record":
                    target = "_tail_file_identity"
                    route_index = 4
                elif new_name == "_tail_closed_stack" and call == "_held_io._verify_scope_identity_original":
                    target = "_tail_scope_identity"
                    route_index = 3
                elif new_name == "_tail_body_stack" and call == "_closed_stack_check_original":
                    target = "_tail_closed_stack"
                    route_index = 2
                elif new_name == "_tail_write_gate" and call == "_runtime_body_stack_check_original":
                    target = "_tail_body_stack"
                    route_index = 1
                elif (new_name == "_tail_body_write" and call == "write_check"
                        and any(k.arg == "_runtime_observation" for k in node.keywords)):
                    target = "_tail_write_gate"
                    route_index = 0
                if target is not None:
                    counts[call] = counts.get(call, 0) + 1
                    route = call + " is _TAIL_ID_ROUTE_FUNCTIONS[" + str(route_index) + "]"
                    if route_index == 3:
                        route = "_held_io is held_module and " + route
                    node.func = ast.IfExp(test=ast.parse(route, mode="eval").body,
                        body=ast.Name(id=target, ctx=ast.Load()), orelse=node.func)
                return node

        clone = ast.fix_missing_locations(Route().visit(clone))
        expected_counts = ({"held_module.original._identities": 1} if new_name == "_tail_file_identity" else
            {"held_module._verify_identity_record": 1} if new_name == "_tail_scope_identity" else
            {"_held_io._verify_scope_identity_original": 2} if new_name == "_tail_closed_stack" else
            {"_closed_stack_check_original": 1} if new_name == "_tail_body_stack" else
            {"_runtime_body_stack_check_original": 1} if new_name == "_tail_write_gate" else {"write_check": 1})
        assert counts == expected_counts, (new_name, counts)
        reverted = copy.deepcopy(clone)

        class Unroute(ast.NodeTransformer):
            def visit_Call(self, node):
                self.generic_visit(node)
                if isinstance(node.func, ast.IfExp):
                    assert "_TAIL_ID_ROUTE_FUNCTIONS[" in ast.unparse(node.func.test)
                    node.func = node.func.orelse
                elif ast.unparse(node.func) == "_tail_identities":
                    node.func = ast.parse("held_module.original._identities", mode="eval").body
                return node

        reverted = Unroute().visit(reverted)
        if source == "held":
            reverted = Unqualify().visit(reverted)
        reverted.name = original_name
        assert dump(reverted) == dump(original), original_name
        output.append(clone)
        proofs.append({"source": source, "original": original_name, "local_copy": new_name,
            "original_ast_sha256": digest(dump(original).encode()), "routes": counts,
            "inverse_ast_equal": True})
    return output, proofs


def helper_bytes(namespace_raw, held_raw):
    clones, proofs = clone_functions(namespace_raw, held_raw)
    consumer = CONSUMER_PREFIX + "\n\n".join(textwrap.indent(ast.unparse(n), "    ") for n in clones)
    consumer += "\n\n    return _tail_body_write(held, scope)\n"
    raw = (HELPERS + "\n\n" + consumer).encode()
    if b"\r\n" in namespace_raw:
        raw = raw.replace(b"\n", b"\r\n")
    return raw, proofs


def replacements(old):
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    return [(BEFORE.encode().replace(b"\n", eol), AFTER.encode().replace(b"\n", eol))]


def validate():
    inputs = original_inputs()
    old = (OLD_PACKAGE / NS_NAME).read_bytes()
    new = (PACKAGE / NS_NAME).read_bytes()
    a, b = nodes(old), nodes(new)
    added = {"_runtime_tail_identity_dispatch_original", "_runtime_tail_identity_binding_original",
        "_runtime_body_tail_identity_original"}
    assert set(b) - set(a) == added
    assert [key for key in a if dump(b[key]) != dump(a[key])] == ["_verify_closed_namespace_owner_runtime_body_original"]
    helpers, proofs = helper_bytes(old, (OLD_PACKAGE / HELD_NAME).read_bytes())
    assert new.endswith(helpers)
    inverse = new[:-len(helpers)]
    for before, after in reversed(replacements(old)):
        assert inverse.count(after) == 1
        inverse = inverse.replace(after, before, 1)
    assert inverse == old
    consumer = b["_runtime_body_tail_identity_original"]
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
        inverse = raw.replace(b"r715", b"r713")
        for current, previous in hashes:
            inverse = inverse.replace(current.encode(), previous.encode())
        assert inverse == (OLD / name).read_bytes()
    return {"state": "runtime_tail_identity_static_only", "original_inputs": inputs,
        "changed_definitions": ["_verify_closed_namespace_owner_runtime_body_original"], "new_definitions": sorted(added),
        "unchanged_namespace_definitions": len(a) - 1, "unchanged_support_modules": same,
        "namespace_sha_only_adapters": adapters, "mechanical_extractions": proofs,
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
            fromfile="r713/" + name, tofile="r715/" + name))
    for name in NAMES:
        p = NEW / name
        raw = p.read_bytes().replace(b"r713", b"r715")
        for previous, current in hashes:
            raw = raw.replace(previous.encode(), current.encode())
        p.write_bytes(raw)
    write_new(NEW / "build-candidate.py", Path(__file__).read_bytes())
    json_new(NEW / "source-inputs.json", {"inputs": inputs})
    json_new(NEW / "candidate-evidence.json", validate())
    json_new(NEW / "extraction-ast.json", {"mechanical_extractions": proofs})
    write_new(NEW / "namespace.diff", "".join(difflib.unified_diff(old.decode().splitlines(True), new.decode().splitlines(True),
        fromfile="r713", tofile="r715")).encode())
    write_new(NEW / "sha-adapters.diff", "".join(adapter_diff).encode())
    write_new(NEW / "README.md", b"# r715\n\nA private fixed runtime body tail owns its original complete first stack. Only the second sweep's unchanged filehold path/handle observation is shared; every original logical/poll/Context/graph/directory and lock check remains. Six existing helper bodies are mechanically extracted into private closures with AST inverse checks. No new scope, token, caller proof or persistent observation. Original general helpers and Held/Stage remain unchanged; Store/observer adapt only the actual Namespace source SHA. Static preparation only.\n")
    print(json.dumps({"namespace": fact(target), "store": fact(PACKAGE / ADAPTERS[0]),
        "prepared_static_only": True}))


def freeze():
    evidence = validate()
    assert json.loads((NEW / "candidate-evidence.json").read_bytes()) == evidence
    sources = sorted(PACKAGE.glob("*.py")) + [NEW / n for n in NAMES]
    artifacts = [NEW / n for n in ("build-candidate.py", "source-inputs.json", "candidate-evidence.json",
        "namespace.diff", "sha-adapters.diff", "extraction-ast.json", "README.md")]
    path = NEW / "r715-candidate-manifest.json"
    json_new(path, {"state": "runtime_tail_identity_frozen_static_only", "source_files": [fact(p) for p in sources],
        "artifacts": [fact(p) for p in artifacts], "evidence": evidence})
    print(json.dumps({"manifest": fact(path)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "freeze"))
    args = parser.parse_args()
    prepare() if args.action == "prepare" else freeze()
