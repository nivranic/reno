"""File-only construction of r729; never import products or execute tests."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "data/t16-runtime-row-cpu-r728"
PARENT_PACKAGE_NAME = "authority_runtime_row_r728"
PARENT_PACKAGE = PARENT / PARENT_PACKAGE_NAME
PARENT_MANIFEST = PARENT / "r728-candidate-manifest.json"
PARENT_MANIFEST_SHA = "fae784ba6b5321d5f97509b99d8c033601e8ba29b7cbd9ea9aafb40f32825de4"
PARENT_TERMINAL = PARENT / "r728-terminal.json"
PARENT_TERMINAL_SHA = "1b1a52fc6d0ba1e2abcec1cef0c3b8dd7150b77d949c1ed98d6094b5ea9474eb"
PARENT_RUNNER = ROOT / "scripts/verify_runtime_row_r728.py"
DEST = ROOT / "data/t16-runtime-graph-cpu-r729"
PACKAGE_NAME = "authority_runtime_graph_cpu_r729"
PACKAGE = DEST / PACKAGE_NAME
RUNNER = ROOT / "scripts/verify_runtime_graph_cpu_r729.py"
NS = "personal_authority_namespace.py"
MATCH_NAME = "_runtime_directory_graph_matches_original"
HELPER_NAME = "_runtime_directory_chain_identity_cpu_original"
CAPTURE_NAMES = ("_DIRECTORY_CHAIN_IDENTITY_IMPL", "_DIRECTORY_CHAIN_IDENTITY_CODE")
PIN_MODULES = ("personal_authority_store.py", "personal_origin_observer.py",
    "personal_committed_readfacts.py", "personal_enrollment_bindings.py")

HELPER = '''def _runtime_directory_chain_identity_cpu_original(first, last):
    for a, b in zip(first, last, strict=True):
        if a is not b:
            return False
    return True


_DIRECTORY_CHAIN_IDENTITY_IMPL = _runtime_directory_chain_identity_cpu_original
_DIRECTORY_CHAIN_IDENTITY_CODE = _runtime_directory_chain_identity_cpu_original.__code__


'''
OUTER_GATE = '''(_ROW_ALL is not None and _ROW_ZIP is not None
    and all is _ROW_ALL and zip is _ROW_ZIP)'''
INNER_GATE = '''(_ROW_ALL is not None and _ROW_ZIP is not None
    and all is _ROW_ALL and zip is _ROW_ZIP
    and type is _ROW_TYPE and tuple is _ROW_TUPLE
    and _ROW_TYPE(first) is _ROW_TUPLE and _ROW_TYPE(last) is _ROW_TUPLE
    and _runtime_directory_chain_identity_cpu_original is _DIRECTORY_CHAIN_IDENTITY_IMPL
    and _runtime_directory_chain_identity_cpu_original.__code__ is _DIRECTORY_CHAIN_IDENTITY_CODE)'''

DESIGN = '''# r729：链内身份比较的严格 zip CPU 分支

父版本是冻结 r728：完整 22 模块、7 原测试与旧 row 契约文件。
仅改 Namespace `_runtime_directory_graph_matches_original` 的末尾 chain-all
表达式，并增加一项私有比较函数与它的函数、code 指针。旧 row helper 和 builtin
捕获全文保留；目录 graph 生成、fieldnames literal set 和全部物理门全文保留。

原 graph 前六个 and 项（3 个 is、row len、row all、chain len）原 AST 不改。
在原 chain len 成功后的原 outer-all 位置，实时核 all/zip 是 r728 已捕获的 native
builtin，未知分派执行完整原 outer-all AST。fast 分支的 outer all 与 zip 仍位于
原位置，参数索引、strict=True、每链 len(first)==len(last) and 都不改。

每链在原 inner-all 位置实时核 native all/zip、type/tuple 的真捕获，两个链参数
为 exact tuple，新 helper 当前指针及同函数原 code 指针保持。任一不满足，只运行
该位置的原 inner-all，不重放原 row、length 或已完成链。陌生 helper 不读 code。
通过准入后，helper 使用当前原 zip(first,last,strict=True)，逐个 is；首 False
立即返回，仍先于后续 strict zip 长度错误。没有额外 len、切片、tuple 缓存或
物理观察。原 len equality 的返回对象仍由 and 与外层 all 判真，保留可能两次的
有状态 truth。未知 all 的选择仍先于未知 zip；原异常对象和原短路保持。

同 function 对象的 __code__ 变化会走原 inner fallback，不声称任意并发写 globals
或 native 伪造可由 Python 身份门防御。无 caller 参数、owner、receipt 或 authority
授权改变。新增准入本身有成本；仅声明减少固定分支的内层 generator/all，未证实
耗时收益，不承诺原 30 秒完整链通过。

旧 8 测试仅机械适配包名和 NS/Store SHA。新 focused 只运行新链契约，Store
独立使用原完整 selector、初始化、repeat、32 位 walk、prepare/put 和退出。
runner 保父 exact audit、-I/-B、新 TEMP、single-use claim、静态资源闭集、原
30 秒门、失败保全；无 profiling、重试或自动下游运行。当前仅 file-only 构建。
'''

FOCUSED_TEST = '''"""Pure comparison contracts; these objects never grant authority or custody."""
import ast
import builtins
from pathlib import Path

import pytest

from authority_runtime_graph_cpu_r729 import personal_authority_namespace as ns


def _legacy(namespace):
    tree = ast.parse(Path(ns.__file__).read_bytes())
    actual = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name == "_runtime_directory_graph_matches_original")
    # Inverse only the actual candidate's final conditional: its else is the
    # untouched original complete chain tail, not an implementation copy.
    actual.body[0].value.values[-1] = actual.body[0].value.values[-1].orelse
    target = dict(vars(ns), **namespace)
    exec(compile(ast.Module(body=[actual], type_ignores=[]), ns.__file__, "exec"), target)
    return target[actual.name]


def _graphs(chains_before=(), chains_after=None, *, rows=()):
    tokens = tuple(object() for _ in range(3))
    if chains_after is None:
        chains_after = chains_before
    return tokens + (rows, chains_before), tokens + (rows, chains_after)


@pytest.mark.parametrize("length", [0, 1, 2, 8, 129])
def test_exact_tuple_identity_chains_accept_original_equal_graphs(length):
    chain = tuple(object() for _ in range(length))
    before, after = _graphs((chain,), (tuple(value for value in chain),))
    assert ns._runtime_directory_graph_matches_original(before, after) is True


@pytest.mark.parametrize("index", range(8))
def test_identity_mismatch_uses_is_and_rejects_at_each_position(index):
    class Equal:
        def __eq__(self, other):
            raise AssertionError("is must not call equality")
    chain = tuple(Equal() for _ in range(8))
    changed = chain[:index] + (Equal(),) + chain[index + 1:]
    before, after = _graphs((chain,), (changed,))
    assert ns._runtime_directory_graph_matches_original(before, after) is False


@pytest.mark.parametrize("left,right", [(0, 1), (1, 0), (1, 2), (2, 1)])
def test_original_chain_length_gate_rejects_before_helper(left, right):
    values = tuple(object() for _ in range(max(left, right)))
    before, after = _graphs((values[:left],), (values[:right],))
    assert ns._runtime_directory_graph_matches_original(before, after) is False


@pytest.mark.parametrize("lengths", [(0, 1), (1, 0), (1, 2), (2, 1)])
def test_helper_retains_strict_zip_length_error_after_equal_prefix(lengths):
    values = tuple(object() for _ in range(max(lengths)))
    with pytest.raises(ValueError):
        ns._runtime_directory_chain_identity_cpu_original(values[:lengths[0]], values[:lengths[1]])


@pytest.mark.parametrize("lengths", [(1, 2), (2, 1)])
def test_helper_first_identity_false_precedes_later_strict_zip_length_error(lengths):
    assert ns._runtime_directory_chain_identity_cpu_original(
        tuple(object() for _ in range(lengths[0])),
        tuple(object() for _ in range(lengths[1]))) is False


class TrackedTuple(tuple):
    def __new__(cls, values, label, events):
        instance = super().__new__(cls, values)
        instance.label, instance.events = label, events
        return instance

    def __len__(self):
        self.events.append(("len", self.label))
        return super().__len__()

    def __iter__(self):
        self.events.append(("iter", self.label))
        return super().__iter__()


class TrackedList(list):
    def __init__(self, values, label, events):
        super().__init__(values)
        self.label, self.events = label, events

    def __len__(self):
        self.events.append(("len", self.label))
        return super().__len__()

    def __iter__(self):
        self.events.append(("iter", self.label))
        return super().__iter__()


@pytest.mark.parametrize("kind", [TrackedTuple, TrackedList])
@pytest.mark.parametrize("lengths", [(2, 2), (1, 2), (2, 1)])
def test_unknown_chain_sequences_preserve_original_length_and_iteration_order(kind, lengths):
    values, events = tuple(object() for _ in range(2)), []
    left, right = kind(values[:lengths[0]], "left", events), kind(values[:lengths[1]], "right", events)
    before, after = _graphs((left,), (right,))
    oracle = _legacy({})
    expected = oracle(before, after)
    expected_events = events[:]
    events.clear()
    assert ns._runtime_directory_graph_matches_original(before, after) is expected
    assert events == expected_events


@pytest.mark.parametrize("global_name", ["all", "zip", "len", "type", "tuple"])
def test_custom_global_dispatch_retains_original_calls_and_exception_order(monkeypatch, global_name):
    chain, events = tuple(object() for _ in range(2)), []
    original = getattr(builtins, global_name)
    def replacement(*args, **kwargs):
        events.append((global_name, tuple(id(value) for value in args), kwargs))
        if global_name in {"type", "tuple"}:
            raise AssertionError("original expression does not call type or tuple")
        return original(*args, **kwargs)
    before, after = _graphs((chain,))
    oracle = _legacy({global_name: replacement})
    expected = oracle(before, after)
    expected_events = events[:]
    events.clear()
    monkeypatch.setattr(ns, global_name, replacement, raising=False)
    assert ns._runtime_directory_graph_matches_original(before, after) is expected
    # Generator identity differs, so compare selected names and keyword/order,
    # not transient argument object addresses for all.
    assert [(name, kwargs) for name, _, kwargs in events] == [
        (name, kwargs) for name, _, kwargs in expected_events]
    if global_name in {"type", "tuple"}:
        assert events == []


@pytest.mark.parametrize("answer", [False, True])
def test_custom_chain_length_equality_truth_keeps_original_and_and_outer_all_counts(monkeypatch, answer):
    chain, events = tuple(object() for _ in range(2)), []
    before, after = _graphs((chain,))
    class Truth:
        def __bool__(self):
            events.append("truth")
            return answer
    class Number:
        def __eq__(self, other):
            events.append("equality")
            return Truth()
    def length(value):
        if value is chain:
            events.append("chain-length")
            return Number()
        return builtins.len(value)
    oracle = _legacy({"len": length})
    expected = oracle(before, after)
    expected_events = events[:]
    events.clear()
    monkeypatch.setattr(ns, "len", length, raising=False)
    assert ns._runtime_directory_graph_matches_original(before, after) is expected
    assert events == expected_events
    assert events.count("truth") == (1 if answer else 2)


def test_false_first_truth_and_true_second_truth_retains_outer_all_original_result(monkeypatch):
    chain, events = (object(),), []
    before, after = _graphs((chain,))
    class Truth:
        def __bool__(self):
            events.append("truth")
            return len(events) == 2
    class Number:
        def __eq__(self, other):
            return Truth()
    monkeypatch.setattr(ns, "len", lambda value:
        Number() if value is chain else builtins.len(value), raising=False)
    assert ns._runtime_directory_graph_matches_original(before, after) is True
    assert events == ["truth", "truth"]


@pytest.mark.parametrize("changed_name", ["all", "zip", "type", "tuple", "helper", "code"])
def test_chain_length_truth_late_dispatch_change_uses_remaining_original_inner(monkeypatch, changed_name):
    chain, events = tuple(object() for _ in range(2)), []
    before, after = _graphs((chain,))
    original_helper = ns._runtime_directory_chain_identity_cpu_original
    def forbidden_helper(*args):
        raise AssertionError("late helper must not run")
    def observed_all(values):
        events.append("late-all")
        return builtins.all(values)
    def observed_zip(*args, **kwargs):
        events.append("late-zip")
        return builtins.zip(*args, **kwargs)
    def forbidden_builtin(*args):
        raise AssertionError("unknown type or tuple must not run")
    class Truth:
        def __bool__(self):
            events.append("truth")
            if changed_name == "helper":
                monkeypatch.setattr(ns, "_runtime_directory_chain_identity_cpu_original", forbidden_helper)
            elif changed_name == "code":
                monkeypatch.setattr(original_helper, "__code__", forbidden_helper.__code__)
            else:
                monkeypatch.setattr(ns, changed_name,
                    observed_all if changed_name == "all" else observed_zip
                    if changed_name == "zip" else forbidden_builtin, raising=False)
            return True
    class Number:
        def __eq__(self, other):
            return Truth()
    def length(value):
        if value is chain:
            return Number()
        return builtins.len(value)
    monkeypatch.setattr(ns, "len", length, raising=False)
    assert ns._runtime_directory_graph_matches_original(before, after) is True
    assert events == ["truth"] + (["late-all"] if changed_name == "all" else
        ["late-zip"] if changed_name == "zip" else [])


def test_unknown_outer_zip_retains_selected_outer_all_when_zip_changes_global_all(monkeypatch):
    chain, events = tuple(object() for _ in range(2)), []
    before, after = _graphs((chain,))
    def forbidden_all(*args):
        raise RuntimeError("new all")
    def observed_zip(*args, **kwargs):
        events.append("zip")
        # Only the chain outer zip changes all. The previously selected outer
        # all stays selected; the first inner all sees the replacement.
        if args[0] is before[4]:
            monkeypatch.setattr(ns, "all", forbidden_all, raising=False)
        return builtins.zip(*args, **kwargs)
    monkeypatch.setattr(ns, "zip", observed_zip, raising=False)
    with pytest.raises(RuntimeError, match="new all"):
        ns._runtime_directory_graph_matches_original(before, after)
    assert events == ["zip", "zip", "zip"]


def test_unknown_helper_object_does_not_read_its_code_or_call_it(monkeypatch):
    class Stranger:
        def __getattribute__(self, name):
            raise AssertionError("stranger attribute must not be read")
        def __call__(self, *args):
            raise AssertionError("stranger helper must not run")
    chain = (object(),)
    before, after = _graphs((chain,))
    monkeypatch.setattr(ns, "_runtime_directory_chain_identity_cpu_original", Stranger())
    assert ns._runtime_directory_graph_matches_original(before, after) is True


@pytest.mark.parametrize("mismatch", [False, True])
def test_false_identity_precedes_strict_error_under_custom_equal_length(monkeypatch, mismatch):
    first, second = object(), object()
    before, after = _graphs(((first,),), (((second if mismatch else first), second),))
    monkeypatch.setattr(ns, "len", lambda value:
        2 if value is before[4][0] or value is after[4][0] else builtins.len(value), raising=False)
    if mismatch:
        assert ns._runtime_directory_graph_matches_original(before, after) is False
    else:
        with pytest.raises(ValueError):
            ns._runtime_directory_graph_matches_original(before, after)


@pytest.mark.parametrize("changed_name", ["all", "zip", "helper", "code"])
def test_row_tail_callback_late_changes_chain_dispatch_without_replaying_row(monkeypatch, changed_name):
    chain, events = (object(),), []
    helper = ns._runtime_directory_chain_identity_cpu_original
    def forbidden_helper(*args):
        raise AssertionError("changed helper must not be called")
    def observed_all(values):
        events.append("late-all")
        return builtins.all(values)
    def observed_zip(*args, **kwargs):
        events.append("late-zip")
        return builtins.zip(*args, **kwargs)
    class Tail:
        def __eq__(self, other):
            events.append("row-tail")
            if changed_name == "helper":
                monkeypatch.setattr(ns, "_runtime_directory_chain_identity_cpu_original", forbidden_helper)
            elif changed_name == "code":
                monkeypatch.setattr(helper, "__code__", forbidden_helper.__code__)
            else:
                monkeypatch.setattr(ns, changed_name,
                    observed_all if changed_name == "all" else observed_zip, raising=False)
            return True
    prefix = tuple(object() for _ in range(10))
    row_before, row_after = prefix + (Tail(), 1, 2, 3), prefix + (object(), 1, 2, 3)
    before, after = _graphs((chain,))
    before, after = before[:3] + ((row_before,), before[4]), after[:3] + ((row_after,), after[4])
    assert ns._runtime_directory_graph_matches_original(before, after) is True
    assert events == ["row-tail"] + (["late-all", "late-all"] if changed_name == "all"
        else ["late-zip", "late-zip"] if changed_name == "zip" else [])


def test_inner_custom_all_exception_object_selected_before_zip_change(monkeypatch):
    chain, events = (object(),), []
    before, after = _graphs((chain,))
    original = RuntimeError("actual original inner all failure")
    def observed_all(values):
        events.append("selected-all")
        raise original
    def observed_zip(*args, **kwargs):
        events.append("selected-zip")
        monkeypatch.setattr(ns, "all", lambda values: True, raising=False)
        return builtins.zip(*args, **kwargs)
    class Truth:
        def __bool__(self):
            monkeypatch.setattr(ns, "all", observed_all, raising=False)
            monkeypatch.setattr(ns, "zip", observed_zip, raising=False)
            return True
    class Number:
        def __eq__(self, other):
            return Truth()
    monkeypatch.setattr(ns, "len", lambda value:
        Number() if value is chain else builtins.len(value), raising=False)
    with pytest.raises(RuntimeError) as raised:
        ns._runtime_directory_graph_matches_original(before, after)
    assert raised.value is original
    assert events == ["selected-zip", "selected-all"]


def test_native_outer_zip_argument_getter_can_change_inner_all_without_replaying_rows(monkeypatch):
    chain, events = tuple(object() for _ in range(2)), []
    original_before, original_after = _graphs((chain,))
    class Graph(tuple):
        def __getitem__(self, index):
            events.append(index)
            if index == 4 and events.count(4) == 3:
                monkeypatch.setattr(ns, "all", lambda values: False, raising=False)
            return super().__getitem__(index)
    before, after = Graph(original_before), Graph(original_after)
    assert ns._runtime_directory_graph_matches_original(before, after) is False
    assert events == [0, 0, 1, 1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4]


def test_original_graph_identity_gate_is_first_and_skips_all_chain_callbacks():
    class Forbidden:
        def __len__(self):
            raise AssertionError("length must not run")
    before, after = _graphs(Forbidden())
    after = (object(),) + after[1:]
    assert ns._runtime_directory_graph_matches_original(before, after) is False


@pytest.mark.parametrize("global_name", ["all", "zip"])
def test_unknown_callee_raises_original_exception_without_replaying_prefix(monkeypatch, global_name):
    original, events = RuntimeError("selected original failure"), []
    before, after = _graphs((tuple(object() for _ in range(2)),))
    def replacement(*args, **kwargs):
        events.append(global_name)
        raise original
    monkeypatch.setattr(ns, global_name, replacement, raising=False)
    with pytest.raises(RuntimeError) as raised:
        ns._runtime_directory_graph_matches_original(before, after)
    assert raised.value is original
    assert events == [global_name]
'''


def need(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": sha(raw)}


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


def replace_once(raw, before, after):
    need(raw.count(before) == 1, "expected one replacement: " + before.decode())
    changed = raw.replace(before, after)
    need(changed.replace(after, before) == raw, "replacement not invertible")
    return changed


def named_path(relative):
    need(type(relative) is str, "named source is not a string")
    name = PurePosixPath(relative)
    need(relative == name.as_posix() and not name.is_absolute() and name.parts
        and all(part not in {".", ".."} and ":" not in part and "\\" not in part for part in name.parts),
        "invalid exact workspace source path")
    path = ROOT.joinpath(*name.parts)
    need(path.resolve(strict=True).is_relative_to(ROOT.resolve()), "named source escaped workspace")
    return path


def checked_parent():
    need(sha(PARENT_MANIFEST.read_bytes()) == PARENT_MANIFEST_SHA, "r728 manifest changed")
    need(sha(PARENT_TERMINAL.read_bytes()) == PARENT_TERMINAL_SHA, "r728 terminal changed")
    parent = json.loads(PARENT_MANIFEST.read_bytes())
    need(parent["original_inputs_before"] == parent["original_inputs_after"]
        and parent["selectors_executed"] == [], "r728 source evidence changed")
    rows = {}
    for source in (*parent["original_inputs_before"], *parent["source_files"],
            parent["builder"], parent["source_evidence"], parent["design"]):
        need(set(source) == {"path", "byte_size", "sha256"}, "unexpected source fact")
        path = named_path(source["path"])
        need(fact(path) == source, "frozen source changed: " + source["path"])
        need(source["path"] not in rows or rows[source["path"]] == source, "source rows disagree")
        rows[source["path"]] = source
    for path in (PARENT_MANIFEST, PARENT_RUNNER, PARENT_TERMINAL):
        rows[fact(path)["path"]] = fact(path)
    prefix = PARENT_PACKAGE.relative_to(ROOT).as_posix() + "/"
    package_rows = [row for row in parent["source_files"] if row["path"].startswith(prefix)]
    test_rows = [row for row in parent["source_files"] if not row["path"].startswith(prefix)]
    need(len(package_rows) == 22 and len(test_rows) == 8, "r728 complete graph changed")
    return parent, sorted(rows.values(), key=lambda row: row["path"]), package_rows, test_rows


def definitions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def assignment(raw, name):
    node = next(node for node in ast.parse(raw).body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)
    return b"".join(raw.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]), node


def patch_graph(raw):
    tree = ast.parse(raw)
    old = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == MATCH_NAME)
    need(len(old.body) == 1 and isinstance(old.body[0], ast.Return)
        and isinstance(old.body[0].value, ast.BoolOp) and len(old.body[0].value.values) == 7,
        "original graph match shape changed")
    tail = old.body[0].value.values[-1]
    need(isinstance(tail, ast.Call) and isinstance(tail.args[0], ast.GeneratorExp), "original outerall changed")
    fast_tail = copy.deepcopy(tail)
    item = fast_tail.args[0].elt
    need(isinstance(item, ast.BoolOp) and len(item.values) == 2, "original chain length and changed")
    original_inner = copy.deepcopy(item.values[-1])
    item.values[-1] = ast.IfExp(test=ast.parse(INNER_GATE, mode="eval").body,
        body=ast.parse("_DIRECTORY_CHAIN_IDENTITY_IMPL(first, last)", mode="eval").body,
        orelse=original_inner)
    new = copy.deepcopy(old)
    new.body[0].value.values[-1] = ast.IfExp(test=ast.parse(OUTER_GATE, mode="eval").body,
        body=fast_tail, orelse=copy.deepcopy(tail))
    ast.fix_missing_locations(new)
    lines = raw.splitlines(keepends=True)
    old_span = b"".join(lines[old.lineno - 1:old.end_lineno])
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    replacement = (HELPER + ast.unparse(new) + "\n").encode().replace(b"\n", newline)
    result = replace_once(raw, old_span, replacement.rstrip(b"\r\n"))
    need(result.replace(replacement.rstrip(b"\r\n"), old_span) == raw, "graph bytes inverse differs")
    changed = ast.parse(result)
    newdefs = definitions(result)
    olddefs = definitions(raw)
    need(set(newdefs) == set(olddefs) | {HELPER_NAME}, "unexpected added definition")
    need(all(olddefs[name] == newdefs[name] for name in olddefs if name != MATCH_NAME),
        "another NS definition changed")
    inverse = copy.deepcopy(changed)
    inverse.body = [node for node in inverse.body if not (
        isinstance(node, ast.FunctionDef) and node.name == HELPER_NAME or
        isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in CAPTURE_NAMES)]
    recovered = next(node for node in inverse.body if isinstance(node, ast.FunctionDef) and node.name == MATCH_NAME)
    recovered.body[0].value.values[-1] = recovered.body[0].value.values[-1].orelse
    need(ast.dump(inverse, include_attributes=False) == ast.dump(tree, include_attributes=False),
        "inverse NS module AST differs")
    need([ast.dump(node, include_attributes=False) for node in new.body[0].value.values[:-1]] ==
        [ast.dump(node, include_attributes=False) for node in old.body[0].value.values[:-1]],
        "original graph prefix changed")
    inverse_tail = copy.deepcopy(fast_tail)
    inverse_tail.args[0].elt.values[-1] = inverse_tail.args[0].elt.values[-1].orelse
    need(ast.dump(inverse_tail, include_attributes=False) == ast.dump(tail, include_attributes=False),
        "fast tail changed beyond original innerall")
    return result, len(olddefs)


def patch_bindings(raw, files):
    result = raw
    for name in ("SOURCES", "SOURCE_SIZES"):
        span, node = assignment(raw, name)
        old = ast.literal_eval(node.value)
        need(len(old) == 20 and len(dict(old)) == 20, "20 unique binding sources required")
        new = tuple((filename, sha(files[filename]) if name == "SOURCES" else len(files[filename]))
            for filename, _ in old)
        result = replace_once(result, span, span.replace(repr(old).encode(), repr(new).encode()))
    pins = ast.literal_eval(assignment(result, "SOURCES")[1].value)
    sizes = ast.literal_eval(assignment(result, "SOURCE_SIZES")[1].value)
    need(definitions(raw) == definitions(result), "binding definitions changed")
    for filename, expected in pins:
        need(sha(files[filename]) == expected and len(files[filename]) == dict(sizes)[filename]
            and len(files[filename]) <= 1048576, "actual source pin/size differs")
    return result, pins, sizes


def make_runner():
    raw = PARENT_RUNNER.read_bytes().replace(b"r728", b"r729")
    raw = raw.replace(b"data/t16-runtime-row-cpu-r729", b"data/t16-runtime-graph-cpu-r729")
    raw = raw.replace(b"authority_runtime_row_r729", PACKAGE_NAME.encode())
    raw = raw.replace(b"exact-tuple row source graph", b"strict-zip chain source graph")
    raw = replace_once(raw, PARENT_MANIFEST_SHA.encode(), b"__R729_MANIFEST_SHA__")
    raw = replace_once(raw, b'"focused": (("test_runtime_row_cpu.py", None),)',
        b'"focused": (("test_runtime_graph_cpu.py", None),)')
    raw = replace_once(raw, b'len(copied) == 30', b'len(copied) == 31')
    raw = raw.replace(b"all seven original test files plus the focused contract file",
        b"all eight original test files plus the focused chain contract file")
    ast.parse(raw)
    return raw


def main():
    _, before, package_rows, test_rows = checked_parent()
    need(not DEST.exists() and not RUNNER.exists(), "fresh destination/runner required; no rebuild")
    original = {Path(row["path"]).name: named_path(row["path"]).read_bytes() for row in package_rows}
    files = dict(original)
    files[NS], definition_count = patch_graph(files[NS])
    for name in ("personal_authority_store.py", "personal_origin_observer.py"):
        files[name] = replace_once(files[name], sha(original[NS]).encode(), sha(files[NS]).encode())
    files["personal_committed_readfacts.py"] = replace_once(files["personal_committed_readfacts.py"],
        sha(original["personal_origin_observer.py"]).encode(), sha(files["personal_origin_observer.py"]).encode())
    files["personal_enrollment_bindings.py"], pins, sizes = patch_bindings(files["personal_enrollment_bindings.py"], files)
    need(sorted(name for name in files if files[name] != original[name]) == sorted((NS, *PIN_MODULES)),
        "module byte delta exceeds graphmatch and source pins")
    for name in files:
        ast.parse(files[name])
        if name != NS:
            need(definitions(files[name]) == definitions(original[name]), "support definition changed: " + name)
    tests, deltas = {}, {}
    for row in test_rows:
        name, old = Path(row["path"]).name, named_path(row["path"]).read_bytes()
        new = old.replace(PARENT_PACKAGE_NAME.encode(), PACKAGE_NAME.encode())
        for filename in (NS, "personal_authority_store.py"):
            if sha(original[filename]).encode() in new:
                new = replace_once(new, sha(original[filename]).encode(), sha(files[filename]).encode())
        inverse = new.replace(PACKAGE_NAME.encode(), PARENT_PACKAGE_NAME.encode())
        for filename in (NS, "personal_authority_store.py"):
            inverse = inverse.replace(sha(files[filename]).encode(), sha(original[filename]).encode())
        need(inverse == old, "test exceeds mechanical package/SHA adaptation: " + name)
        ast.parse(new)
        tests[name] = new
        deltas[name] = {"source": row, "exact_inverse_package_and_existing_ns_store_pins": True}
    ast.parse(FOCUSED_TEST)
    runner = make_runner()
    DEST.mkdir()
    PACKAGE.mkdir()
    for name, raw in files.items():
        write_new(PACKAGE / name, raw)
    for name, raw in tests.items():
        write_new(DEST / name, raw)
    write_new(DEST / "test_runtime_graph_cpu.py", FOCUSED_TEST.encode())
    write_new(DEST / "design.md", DESIGN.encode())
    evidence = {"kind": "r729_strict_zip_chain_CPU_source_delta", "state": "constructed-not-executed",
        "parent_manifest_sha256": PARENT_MANIFEST_SHA, "parent_terminal_sha256": PARENT_TERMINAL_SHA,
        "module_count": 22, "changed_modules": sorted((NS, *PIN_MODULES)), "unchanged_module_bytes_count": 17,
        "namespace_old_definition_count": definition_count,
        "namespace_nonmatch_definitions_unchanged": True, "namespace_inverse_module_AST_equal": True,
        "original_prefix_six_values_AST_equal": True, "original_outerall_zip_length_and_AST_equal": True,
        "unknown_outer_fallback_AST_equal": True, "unknown_inner_fallback_AST_equal": True,
        "rowhelper_and_fieldnames_graph_AST_unchanged": True, "helper_real_strictzip_is_firstfalse": True,
        "support_module_definitions_unchanged": True, "source_binding_actual_sha_and_size_checked": True,
        "source_binding_count": len(pins), "source_pins": pins, "source_sizes": sizes, "tests": deltas,
        "parent_original_sources_include_formal_193_source_set": True,
        "graph_physical_native_clock_cancellation_first_read_hash_charge_close_unchanged": True,
        "runner_parent": fact(PARENT_RUNNER), "namespace_seconds_unchanged": 30,
        "selectors_executed": [], "profiling_enabled": False, "runtime_SQL_activation_permission_created": False}
    write_json(DEST / "r729-source-evidence.json", evidence)
    after = checked_parent()[1]
    need(before == after, "original named inputs changed during construction")
    sources = [fact(PACKAGE / name) for name in sorted(files)] + [fact(DEST / name) for name in sorted(tests)]
    sources.append(fact(DEST / "test_runtime_graph_cpu.py"))
    manifest = {"kind": "r729_strict_zip_chain_CPU_candidate", "state": "frozen-source-not-executed",
        "builder": fact(Path(__file__)), "original_inputs_before": before, "original_inputs_after": after,
        "original_input_count": len(before), "source_files": sources,
        "source_evidence": fact(DEST / "r729-source-evidence.json"), "design": fact(DEST / "design.md"),
        "copied_test_files": [fact(DEST / name)["path"] for name in sorted(tests)],
        "focused_test_file": fact(DEST / "test_runtime_graph_cpu.py")["path"],
        "selectors_executed": [], "H01_complete": False, "goal_complete": False}
    manifest_path = DEST / "r729-candidate-manifest.json"
    write_json(manifest_path, manifest)
    write_new(RUNNER, runner.replace(b"__R729_MANIFEST_SHA__", sha(manifest_path.read_bytes()).encode()))
    ast.parse(RUNNER.read_bytes())
    need(checked_parent()[1] == before, "original input changed at final check")
    print(json.dumps({"state": manifest["state"], "module_count": len(files), "copied_test_count": len(tests),
        "focused_test_file_count": 1, "parent_named_inputs_preserved": len(before),
        "manifest": fact(manifest_path), "source_evidence": fact(DEST / "r729-source-evidence.json"),
        "runner": fact(RUNNER), "product_imports_or_tests_or_prepare_or_profile": False}, sort_keys=True))


if __name__ == "__main__":
    main()
