"""File-only construction of the r728 exact-tuple row CPU candidate.

This script reads named frozen files, parses AST, and writes a fresh candidate.
It never imports product modules, prepares/runs pytest, or enables profiling.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "data/t16-runtime-enrollment-graph-r725"
PARENT_PACKAGE_NAME = "authority_runtime_enrollment_r725"
PARENT_PACKAGE = PARENT / PARENT_PACKAGE_NAME
PARENT_MANIFEST = PARENT / "r725-candidate-manifest.json"
PARENT_MANIFEST_SHA = "d006d07812126af97d1a93046c148eadd42abae455b135f4c5e8b758c7c90be8"
PARENT_RUNNER = ROOT / "scripts/verify_runtime_enrollment_consumers_r725_v2.py"
DEST = ROOT / "data/t16-runtime-row-cpu-r728"
PACKAGE_NAME = "authority_runtime_row_r728"
PACKAGE = DEST / PACKAGE_NAME
RUNNER = ROOT / "scripts/verify_runtime_row_r728.py"
ROW_NAME = "_closed_directory_batch_row_matches_original"
CAPTURE_NAMES = ("_ROW_TUPLE", "_ROW_TYPE", "_ROW_FUNCTION_TYPE", "_ROW_ALL", "_ROW_LEN", "_ROW_ZIP")
NS = "personal_authority_namespace.py"
PIN_MODULES = ("personal_authority_store.py", "personal_origin_observer.py",
    "personal_committed_readfacts.py", "personal_enrollment_bindings.py")
BUILTIN_CAPTURE = '''# Literal types do not trust a replaced module/global builtin at import time.
_ROW_TUPLE = ().__class__
_ROW_TYPE = _ROW_TUPLE.__class__
_ROW_FUNCTION_TYPE = {}.get.__class__
_ROW_ALL = (all if _ROW_TYPE(all) is _ROW_FUNCTION_TYPE
    and all.__name__ == "all" and all.__module__ == "builtins" else None)
_ROW_LEN = (len if _ROW_TYPE(len) is _ROW_FUNCTION_TYPE
    and len.__name__ == "len" and len.__module__ == "builtins" else None)
_ROW_ZIP = (zip if _ROW_TYPE(zip) is _ROW_TYPE
    and zip.__flags__ & 256 != 0 and zip.__flags__ & 512 == 0
    and zip.__name__ == "zip" and zip.__module__ == "builtins" else None)


'''
FAST_PREFIX = '''    if (_ROW_ALL is not None and _ROW_ZIP is not None and _ROW_LEN is not None
            and all is _ROW_ALL and zip is _ROW_ZIP and len is _ROW_LEN
            and type is _ROW_TYPE and tuple is _ROW_TUPLE
            and _ROW_TYPE(before) is _ROW_TUPLE and _ROW_TYPE(after) is _ROW_TUPLE):
        return (len(before) == len(after) == 14
            and before[0] is after[0]
            and before[1] is after[1]
            and before[2] is after[2]
            and before[3] is after[3]
            and before[4] is after[4]
            and before[5] is after[5]
            and before[6] is after[6]
            and before[7] is after[7]
            and before[8] is after[8]
            and before[9] is after[9]
            and before[10:] == after[10:])
'''

FOCUSED_TEST = '''"""Row compatibility contracts; these objects never grant authority or custody."""
import ast
import builtins
from pathlib import Path

import pytest

from authority_runtime_row_r728 import personal_authority_namespace as ns


def _legacy(before, after):
    # Frozen r725 expression: compatibility oracle for unknown dispatch.
    return (len(before) == len(after) == 14
        and all(first is last for first, last in zip(before[:10], after[:10], strict=True))
        and before[10:] == after[10:])


class EqualityProbe:
    def __init__(self, index, events, *, answer=True, failure=None):
        self.index, self.events = index, events
        self.answer, self.failure = answer, failure

    def __eq__(self, other):
        self.events.append(self.index)
        if self.failure is not None:
            raise self.failure
        return self.answer


def _rows(events, *, false_at=None, failure_at=None, failure=None):
    prefix = tuple(object() for _ in range(10))
    before = prefix + tuple(EqualityProbe(index, events,
        answer=index != false_at, failure=failure if index == failure_at else None)
        for index in range(10, 14))
    after = prefix + tuple(object() for _ in range(4))
    return before, after


@pytest.mark.parametrize("index", range(10))
def test_each_identity_mismatch_short_circuits_before_tail_equality(index):
    events = []
    before, after = _rows(events, failure_at=10, failure=RuntimeError("tail must not run"))
    after = after[:index] + (object(),) + after[index + 1:]
    assert ns._closed_directory_batch_row_matches_original(before, after) is False
    assert events == []


def test_distinct_equal_prefix_objects_are_rejected_without_calling_equality():
    events = []
    first, other = EqualityProbe("prefix", events), EqualityProbe("other-prefix", events)
    before, after = _rows(events)
    before, after = (first,) + before[1:], (other,) + after[1:]
    assert ns._closed_directory_batch_row_matches_original(before, after) is False
    assert events == []


@pytest.mark.parametrize("false_at", [None, 10, 11, 12, 13])
def test_tail_equality_order_and_equal_nonidentical_values(false_at):
    events = []
    before, after = _rows(events, false_at=false_at)
    assert ns._closed_directory_batch_row_matches_original(before, after) is (false_at is None)
    assert events == list(range(10, 14 if false_at is None else false_at + 1))


@pytest.mark.parametrize("failure_at", range(10, 14))
def test_tail_equality_preserves_original_exception_object(failure_at):
    events = []
    original = RuntimeError("actual tail equality failure")
    before, after = _rows(events, failure_at=failure_at, failure=original)
    with pytest.raises(RuntimeError) as raised:
        ns._closed_directory_batch_row_matches_original(before, after)
    assert raised.value is original
    assert events == list(range(10, failure_at + 1))


@pytest.mark.parametrize("left,right", [(0, 0), (9, 9), (10, 10), (13, 13),
    (15, 15), (13, 14), (14, 13), (14, 15), (15, 14)])
def test_exact_tuple_invalid_lengths_stop_before_any_tail_equality(left, right):
    events = []
    probe = EqualityProbe("forbidden", events, failure=RuntimeError("invalid length equality"))
    before, after = (probe,) * left, (object(),) * right
    assert ns._closed_directory_batch_row_matches_original(before, after) is False
    assert events == []


class TrackedTuple(tuple):
    def __new__(cls, values, label, events):
        instance = super().__new__(cls, values)
        instance.label, instance.events = label, events
        return instance

    def __len__(self):
        self.events.append(("len", self.label))
        return super().__len__()

    def __getitem__(self, key):
        self.events.append(("get", self.label, key.start, key.stop, key.step))
        result = super().__getitem__(key)
        return TrackedTuple(result, self.label + "-slice", self.events)

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

    def __getitem__(self, key):
        self.events.append(("get", self.label, key.start, key.stop, key.step))
        return TrackedList(super().__getitem__(key), self.label + "-slice", self.events)

    def __iter__(self):
        self.events.append(("iter", self.label))
        return super().__iter__()


@pytest.mark.parametrize("kind", [TrackedTuple, TrackedList])
@pytest.mark.parametrize("lengths", [(14, 14), (13, 13), (13, 14), (14, 13)])
def test_unknown_sequence_preserves_legacy_length_slice_and_iteration_order(kind, lengths):
    values = tuple(object() for _ in range(14))
    events = []
    before, after = kind(values[:lengths[0]], "before", events), kind(values[:lengths[1]], "after", events)
    expected = _legacy(before, after)
    legacy_events = events[:]
    events.clear()
    actual = ns._closed_directory_batch_row_matches_original(before, after)
    assert actual is expected
    assert events == legacy_events
    assert events[:2] == [("len", "before"), ("len", "after")]
    if lengths != (14, 14):
        assert len(events) == 2


@pytest.mark.parametrize("global_name", ["all", "zip", "len", "type", "tuple"])
def test_replaced_global_uses_original_dispatch_without_calling_unknown_type_or_tuple(monkeypatch, global_name):
    events = []
    original = getattr(builtins, global_name)
    before, after = _rows([])

    def replacement(*args, **kwargs):
        events.append((global_name, args, kwargs))
        if global_name in {"type", "tuple"}:
            raise RuntimeError("type and tuple were not used by original expression")
        return original(*args, **kwargs)

    monkeypatch.setattr(ns, global_name, replacement, raising=False)
    assert ns._closed_directory_batch_row_matches_original(before, after) is True
    if global_name in {"type", "tuple"}:
        assert events == []
    elif global_name == "len":
        assert len(events) == 2 and events[0][1][0] is before and events[1][1][0] is after
    elif global_name == "zip":
        assert len(events) == 1 and events[0][2] == {"strict": True}
        assert events[0][1] == (before[:10], after[:10])
    else:
        assert len(events) == 1


@pytest.mark.parametrize("global_name", ["all", "zip", "len"])
def test_replaced_original_callee_exception_and_first_failure_are_preserved(monkeypatch, global_name):
    original = RuntimeError("original dispatch failure")
    events = []
    before, after = _rows(events, failure_at=10, failure=RuntimeError("tail must not run"))

    def replacement(*args, **kwargs):
        raise original

    monkeypatch.setattr(ns, global_name, replacement, raising=False)
    with pytest.raises(RuntimeError) as raised:
        ns._closed_directory_batch_row_matches_original(before, after)
    assert raised.value is original
    assert events == []


def test_replaced_length_preserves_left_then_right_before_mismatched_length_short_circuit(monkeypatch):
    events = []
    before, after = tuple(object() for _ in range(13)), tuple(object() for _ in range(14))

    def observed_len(value):
        events.append("before" if value is before else "after")
        return builtins.len(value)

    monkeypatch.setattr(ns, "len", observed_len, raising=False)
    assert ns._closed_directory_batch_row_matches_original(before, after) is False
    assert events == ["before", "after"]


def _row_import_with(replacements):
    # Execute only the actual capture declarations and row helper in fresh
    # globals; no owner/registry/factory/physical operation is synthesized.
    tree = ast.parse(Path(ns.__file__).read_bytes())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id.startswith("_ROW_"):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_closed_directory_batch_row_matches_original":
            selected.append(node)
    assert len(selected) == 7
    namespace = {"__builtins__": dict(vars(builtins), **replacements)}
    exec(compile(ast.Module(body=selected, type_ignores=[]), ns.__file__, "exec"), namespace)
    return namespace


@pytest.mark.parametrize("global_name", ["all", "zip", "len", "type", "tuple"])
def test_preimport_custom_builtin_capture_is_never_admitted(global_name):
    events = []
    original = getattr(builtins, global_name)

    def replacement(*args, **kwargs):
        events.append(global_name)
        if global_name in {"type", "tuple"}:
            raise RuntimeError("original expression never calls these")
        return original(*args, **kwargs)

    # A Python callable can spoof names/module but cannot become a native
    # built-in function or an immutable native type.
    replacement.__name__, replacement.__module__ = global_name, "builtins"
    actual = _row_import_with({global_name: replacement})
    before, after = _rows([])
    assert actual["_closed_directory_batch_row_matches_original"](before, after) is True
    if global_name in {"type", "tuple"}:
        assert events == []
    else:
        assert actual["_ROW_" + global_name.upper()] is None
        assert events == [global_name] * (2 if global_name == "len" else 1)


def test_preimport_heap_zip_type_with_spoofed_builtin_name_uses_original_call():
    events = []

    class ReplacementZip:
        def __new__(cls, *args, **kwargs):
            events.append(kwargs)
            return builtins.zip(*args, **kwargs)

    ReplacementZip.__name__, ReplacementZip.__module__ = "zip", "builtins"
    actual = _row_import_with({"zip": ReplacementZip})
    before, after = _rows([])
    assert actual["_ROW_ZIP"] is None
    assert actual["_closed_directory_batch_row_matches_original"](before, after) is True
    assert events == [{"strict": True}]


@pytest.mark.parametrize("global_name", ["all", "zip", "len"])
def test_preimport_none_capture_retains_original_typeerror_before_tail(global_name):
    actual = _row_import_with({global_name: None})
    events = []
    before, after = _rows(events, failure_at=10, failure=RuntimeError("tail must not run"))
    assert actual["_ROW_" + global_name.upper()] is None
    with pytest.raises(TypeError):
        actual["_closed_directory_batch_row_matches_original"](before, after)
    assert events == []
'''

DESIGN = '''# r728：原目录行比较的 exact tuple 分支

父版本为冻结 r725 的完整 22 模块和 7 原测试文件。只改 Namespace
`_closed_directory_batch_row_matches_original(before, after)`，在原 builtin 分派及
两个参数均为 exact tuple 时，将十个 identity 比较展开为从 0 到 9 的 `is` 短路。
保留 `len(before) == len(after) == 14` 在任何索引之前、原尾部切片 equality 及异常。

tuple/type 从字面量真类型取得；all/len 仅接收原 native builtin function 类型与原
name/module，zip 仅接收原 type、IMMUTABLETYPE 且非 HEAPTYPE 的 native 类型与原
name/module。此准入排除 Python 替换，不声称防御任意 C 扩展构造的 native 伪装。
导入前的 Python 替换对象因此不会变成 fast 指针，未知捕获为 None。分支准入先比较
捕获 all/zip/len 非 None，再比较当前 all/zip/len/type/tuple 五个指针，全部保持才
使用捕获 type。未知参数、tuple
subclass、list 和任一替换分派都执行原 return 表达式；不提前
调用未知 getter，也不重放已经执行的原链。没有 caller skip 参数或历史缓存。

物理/native 核验、完整 graph shape 前后核验、owner/registry/cancellation/first、
时钟、真正读取/hash/收费及关闭原样保持。Store 在新包导入时自然捕获新 row helper，
不改其入口或 graph 分支。NS SHA 沿 Store/observer→readfacts→bindings 传播；
20 个 SOURCES 与 SOURCE_SIZES 均按真正文件重算，其余 17 个模块精确原 bytes。
原 7 测试仅做包来源与现有 NS/Store SHA 的机械替换，原 fixture 和 selector 不缩。

focused 独立包含新行契约测试；store
独立使用原完整 install/internal resume/repeat/32 位 walk/prepare/put selector。
两套都需 Root 复核后分别 prepare/run，保留 r725 runner 的 exact audit、-I/-B、
新 TEMP、单次 claim、原 30 秒门与 8 个静态资源闭集，不采样、不自动运行下游或重试。

此候选仅减少 exact tuple 分支中 all/zip/generator 和前两次 slice 的 Python 计算。
新增准入本身也有成本；没有承诺耗时减少、30 秒完整链通过或产品授权已经获得。
当前为 file-only 候选，尚未 prepare、运行产品、执行测试或采样。
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
    need(changed.replace(after, before) == raw, "replacement is not mechanically invertible")
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
    need(sha(PARENT_MANIFEST.read_bytes()) == PARENT_MANIFEST_SHA, "r725 manifest pin changed")
    parent = json.loads(PARENT_MANIFEST.read_bytes())
    need(parent["original_inputs_before"] == parent["original_inputs_after"]
        and parent["selectors_executed"] == [], "r725 source evidence changed")
    rows = {}
    for source in (*parent["original_inputs_before"], *parent["source_files"],
            parent["builder"], parent["source_evidence"]):
        need(set(source) == {"path", "byte_size", "sha256"}, "unexpected source fact")
        path = named_path(source["path"])
        need(fact(path) == source, "frozen source changed: " + source["path"])
        need(source["path"] not in rows or rows[source["path"]] == source, "source rows disagree")
        rows[source["path"]] = source
    for path in (PARENT_MANIFEST, PARENT_RUNNER):
        rows[fact(path)["path"]] = fact(path)
    prefix = PARENT_PACKAGE.relative_to(ROOT).as_posix() + "/"
    package_rows = [row for row in parent["source_files"] if row["path"].startswith(prefix)]
    test_rows = [row for row in parent["source_files"] if not row["path"].startswith(prefix)]
    need(len(package_rows) == 22 and len(test_rows) == 7, "r725 complete source graph selection changed")
    return parent, sorted(rows.values(), key=lambda row: row["path"]), package_rows, test_rows


def definitions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def assignment(raw, name):
    node = next(node for node in ast.parse(raw).body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)
    return b"".join(raw.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]), node


def patch_row(raw):
    tree = ast.parse(raw)
    old = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == ROW_NAME)
    need(len(old.body) == 1 and isinstance(old.body[0], ast.Return), "row helper original shape changed")
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.splitlines(keepends=True)
    span = b"".join(lines[old.lineno - 1:old.end_lineno])
    old_return = b"".join(lines[old.body[0].lineno - 1:old.body[0].end_lineno])
    fast = FAST_PREFIX.encode().replace(b"\n", newline)
    altered = span.replace(old_return, fast + old_return)
    need(altered.count(fast) == 1 and altered.replace(fast, b"") == span, "row prefix inverse failed")
    replacement = BUILTIN_CAPTURE.encode().replace(b"\n", newline) + altered
    result = replace_once(raw, span, replacement)
    changed = ast.parse(result)
    newer = next(node for node in changed.body if isinstance(node, ast.FunctionDef) and node.name == ROW_NAME)
    inverse = copy.deepcopy(newer)
    need(len(inverse.body) == 2 and isinstance(inverse.body[0], ast.If)
        and not inverse.body[0].orelse, "row fast/fallback shape changed")
    del inverse.body[0]
    need(ast.dump(inverse, include_attributes=False) == ast.dump(old, include_attributes=False),
        "fallback helper AST differs from original")
    olddefs, newdefs = definitions(raw), definitions(result)
    need(set(olddefs) == set(newdefs) and all(olddefs[name] == newdefs[name]
        for name in olddefs if name != ROW_NAME), "another Namespace definition changed")
    inverse_module = copy.deepcopy(changed)
    captures = [node.targets[0].id for node in changed.body if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id in CAPTURE_NAMES]
    need(tuple(captures) == CAPTURE_NAMES, "original builtin capture declarations changed")
    inverse_module.body = [node for node in inverse_module.body if not (
        isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in CAPTURE_NAMES)]
    inverse_module.body[next(index for index, node in enumerate(inverse_module.body)
        if isinstance(node, ast.FunctionDef) and node.name == ROW_NAME)] = inverse
    need(ast.dump(inverse_module, include_attributes=False) == ast.dump(tree, include_attributes=False),
        "Namespace inverse module AST differs")
    fast_values = newer.body[0].body[0].value.values
    need(len(fast_values) == 12 and ast.dump(fast_values[0], include_attributes=False) ==
        ast.dump(old.body[0].value.values[0], include_attributes=False)
        and ast.dump(fast_values[-1], include_attributes=False) ==
        ast.dump(old.body[0].value.values[-1], include_attributes=False), "length or tail AST changed")
    for index, compare in enumerate(fast_values[1:-1]):
        expected = ast.parse(f"before[{index}] is after[{index}]", mode="eval").body
        need(ast.dump(compare, include_attributes=False) == ast.dump(expected, include_attributes=False),
            "identity short-circuit index/order changed")
    return result, len(olddefs)


def patch_bindings(raw, files):
    result = raw
    for name in ("SOURCES", "SOURCE_SIZES"):
        span, node = assignment(raw, name)
        old = ast.literal_eval(node.value)
        need(len(old) == 20 and len(dict(old)) == 20, "binding graph must contain 20 unique sources")
        new = tuple((filename, sha(files[filename]) if name == "SOURCES" else len(files[filename]))
            for filename, _ in old)
        result = replace_once(result, span, span.replace(repr(old).encode(), repr(new).encode()))
    pins = ast.literal_eval(assignment(result, "SOURCES")[1].value)
    sizes = ast.literal_eval(assignment(result, "SOURCE_SIZES")[1].value)
    need(set(dict(pins)) == set(dict(sizes)) and definitions(raw) == definitions(result), "binding shape changed")
    for filename, expected in pins:
        need(sha(files[filename]) == expected and len(files[filename]) == dict(sizes)[filename]
            and len(files[filename]) <= 1048576, "source binding actual bytes differ")
    return result, pins, sizes


def make_runner():
    raw = PARENT_RUNNER.read_bytes()
    raw = raw.replace(b"r725", b"r728").replace(b"runtime/enrollment source graph", b"exact-tuple row source graph")
    raw = raw.replace(b"data/t16-runtime-enrollment-graph-r728", b"data/t16-runtime-row-cpu-r728")
    raw = raw.replace(b"authority_runtime_enrollment_r728", PACKAGE_NAME.encode())
    raw = replace_once(raw, PARENT_MANIFEST_SHA.encode(), b"__R728_MANIFEST_SHA__")
    start = raw.index(b"SUITES = {")
    end = raw.index(b"\n\n\ndef need", start)
    suites = '''SUITES = {
    "focused": (("test_runtime_row_cpu.py", None),),
    "store": (("test_store_stage_wiring.py",
        "test_authority_v1_install_resume_open_prepare_put_under_original_shared_profile_guard"),),
}'''.encode()
    raw = raw[:start] + suites + raw[end:]
    raw = replace_once(raw, b'    need(len(before) == 249 and len(copied) == 29, "Frozen named source counts changed")\n',
        b'    need(len(copied) == 30 and len(before) == document["original_input_count"], "Frozen named source counts changed")\n')
    raw = replace_once(raw, b'    expected_tests = {GRAPH.joinpath(name).relative_to(ROOT).as_posix()\n                      for selection in SUITES.values() for name, _ in selection}\n',
        b'    expected_tests = set(document["copied_test_files"]) | {document["focused_test_file"]}\n')
    raw = replace_once(raw, b'[document["builder"], document["source_evidence"]]',
        b'[document["builder"], document["source_evidence"], document["design"]]')
    raw = raw.replace(b"all seven original test files", b"all seven original test files plus the focused contract file")
    raw = raw.replace(b'74 if suite == "finite" else None', b'None')
    raw = raw.replace(b'"r728-verification-" + suite + "-v2.json"', b'"r728-verification-" + suite + ".json"')
    raw = raw.replace(b'"r728-verification-" + suite + "-v2-started.json"', b'"r728-verification-" + suite + "-started.json"')
    raw = raw.replace(b'"reno-r728-" + suite + "-v2-"', b'"reno-r728-" + suite + "-"')
    ast.parse(raw)
    return raw


def main():
    parent, before, package_rows, test_rows = checked_parent()
    need(not DEST.exists() and not RUNNER.exists(), "fresh r728 destination and runner required; no rebuild")
    original = {Path(row["path"]).name: named_path(row["path"]).read_bytes() for row in package_rows}
    files = dict(original)
    files[NS], namespace_definition_count = patch_row(files[NS])
    for name in ("personal_authority_store.py", "personal_origin_observer.py"):
        files[name] = replace_once(files[name], sha(original[NS]).encode(), sha(files[NS]).encode())
    name = "personal_committed_readfacts.py"
    files[name] = replace_once(files[name], sha(original["personal_origin_observer.py"]).encode(),
        sha(files["personal_origin_observer.py"]).encode())
    files["personal_enrollment_bindings.py"], pins, sizes = patch_bindings(files["personal_enrollment_bindings.py"], files)
    need(sorted(name for name in files if files[name] != original[name]) == sorted((NS, *PIN_MODULES)),
        "module byte delta exceeds the row and required source pins")
    for name in files:
        ast.parse(files[name])
        if name != NS:
            need(definitions(files[name]) == definitions(original[name]), "support definition changed: " + name)
    tests, deltas = {}, {}
    for row in test_rows:
        name, old = Path(row["path"]).name, named_path(row["path"]).read_bytes()
        new = old.replace(PARENT_PACKAGE_NAME.encode(), PACKAGE_NAME.encode())
        need(new.replace(PACKAGE_NAME.encode(), PARENT_PACKAGE_NAME.encode()) == old, "test package inverse failed")
        for filename in (NS, "personal_authority_store.py"):
            oldsha, newsha = sha(original[filename]).encode(), sha(files[filename]).encode()
            if oldsha in new:
                new = replace_once(new, oldsha, newsha)
        inverse = new.replace(PACKAGE_NAME.encode(), PARENT_PACKAGE_NAME.encode())
        for filename in (NS, "personal_authority_store.py"):
            inverse = inverse.replace(sha(files[filename]).encode(), sha(original[filename]).encode())
        need(inverse == old, "test changed beyond package/SHA mechanical adaptation: " + name)
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
    write_new(DEST / "test_runtime_row_cpu.py", FOCUSED_TEST.encode())
    write_new(DEST / "design.md", DESIGN.encode())
    evidence = {"kind": "r728_exact_tuple_row_cpu_source_delta", "state": "constructed-not-executed",
        "parent_manifest_sha256": PARENT_MANIFEST_SHA, "module_count": 22,
        "changed_modules": sorted((NS, *PIN_MODULES)), "unchanged_module_bytes_count": 17,
        "namespace_old_definition_count": namespace_definition_count,
        "namespace_nonrow_definitions_unchanged": True, "namespace_inverse_module_AST_equal": True,
        "original_length_and_tail_AST_equal": True, "identity_indices_order": list(range(10)),
        "original_fallback_AST_equal": True, "support_module_definitions_unchanged": True,
        "source_binding_actual_sha_and_size_checked": True, "source_binding_count": len(pins),
        "source_pins": pins, "source_sizes": sizes, "tests": deltas,
        "parent_original_sources_include_formal_193_source_set": True,
        "graph_physical_native_clock_cancellation_first_read_hash_charge_close_unchanged": True,
        "runner_parent": fact(PARENT_RUNNER), "namespace_seconds_unchanged": 30,
        "selectors_executed": [], "profiling_enabled": False,
        "runtime_SQL_activation_permission_created": False}
    write_json(DEST / "r728-source-evidence.json", evidence)
    _, after, _, _ = checked_parent()
    need(before == after, "an original named input changed during construction")
    sources = [fact(PACKAGE / name) for name in sorted(files)] + [fact(DEST / name) for name in sorted(tests)]
    sources.append(fact(DEST / "test_runtime_row_cpu.py"))
    manifest = {"kind": "r728_exact_tuple_row_cpu_candidate", "state": "frozen-source-not-executed",
        "builder": fact(Path(__file__)), "original_inputs_before": before, "original_inputs_after": after,
        "original_input_count": len(before), "source_files": sources,
        "source_evidence": fact(DEST / "r728-source-evidence.json"), "design": fact(DEST / "design.md"),
        "copied_test_files": [fact(DEST / name)["path"] for name in sorted(tests)],
        "focused_test_file": fact(DEST / "test_runtime_row_cpu.py")["path"],
        "selectors_executed": [], "H01_complete": False, "goal_complete": False}
    manifest_path = DEST / "r728-candidate-manifest.json"
    write_json(manifest_path, manifest)
    write_new(RUNNER, runner.replace(b"__R728_MANIFEST_SHA__", sha(manifest_path.read_bytes()).encode()))
    ast.parse(RUNNER.read_bytes())
    need(checked_parent()[1] == before, "an original input changed at final source check")
    print(json.dumps({"state": manifest["state"], "module_count": len(files), "copied_test_count": len(tests),
        "focused_test_count_file": 1, "parent_named_inputs_preserved": len(before),
        "manifest": fact(manifest_path), "source_evidence": fact(DEST / "r728-source-evidence.json"),
        "runner": fact(RUNNER), "product_imports_or_tests_or_prepare_or_profile": False}, sort_keys=True))


if __name__ == "__main__":
    main()
