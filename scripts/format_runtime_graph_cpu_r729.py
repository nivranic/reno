"""Single-use file-only preservation and formatting of unprepared r729."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/t16-runtime-graph-cpu-r729"
PACKAGE = DEST / "authority_runtime_graph_cpu_r729"
MANIFEST = DEST / "r729-candidate-manifest.json"
MANIFEST_SHA = "19c2ab232081eb6dc6a0058900e5a4759123bc6f9a3864bbe6898a42d6b31ce2"
RUNNER = ROOT / "scripts/verify_runtime_graph_cpu_r729.py"
RUNNER_SHA = "a1574b894c6a125cc9f493eae52b2f4e423f5d4deb99ce88dfe290cc84ab507d"
DRAFT = DEST / "static-draft-initial"
NS = "personal_authority_namespace.py"
MATCH = "_runtime_directory_graph_matches_original"
FORMATTED = '''def _runtime_directory_graph_matches_original(before, after):
    return (after[0] is before[0] and after[1] is before[1] and after[2] is before[2]
        and len(after[3]) == len(before[3])
        and all(_closed_directory_batch_row_matches_original(first, last)
            for first, last in zip(before[3], after[3], strict=True))
        and len(after[4]) == len(before[4])
        and (all(len(first) == len(last)
            and (_DIRECTORY_CHAIN_IDENTITY_IMPL(first, last)
                if (_ROW_ALL is not None and _ROW_ZIP is not None
                    and all is _ROW_ALL and zip is _ROW_ZIP
                    and type is _ROW_TYPE and tuple is _ROW_TUPLE
                    and _ROW_TYPE(first) is _ROW_TUPLE and _ROW_TYPE(last) is _ROW_TUPLE
                    and _runtime_directory_chain_identity_cpu_original is _DIRECTORY_CHAIN_IDENTITY_IMPL
                    and _runtime_directory_chain_identity_cpu_original.__code__ is _DIRECTORY_CHAIN_IDENTITY_CODE)
                else all(a is b for a, b in zip(first, last, strict=True)))
            for first, last in zip(before[4], after[4], strict=True))
            if (_ROW_ALL is not None and _ROW_ZIP is not None
                and all is _ROW_ALL and zip is _ROW_ZIP)
            else all(len(first) == len(last) and all(a is b for a, b in zip(first, last, strict=True))
                for first, last in zip(before[4], after[4], strict=True))))
'''


def need(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": sha(raw)}


def named_path(relative):
    name = PurePosixPath(relative)
    need(type(relative) is str and name.as_posix() == relative and not name.is_absolute()
        and name.parts and all(part not in {".", ".."} and ":" not in part
        and "\\" not in part for part in name.parts), "invalid exact source path")
    path = ROOT.joinpath(*name.parts)
    need(path.resolve(strict=True).is_relative_to(ROOT.resolve()), "source escaped workspace")
    return path


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def replace_once(raw, before, after):
    need(raw.count(before) == 1, "one mechanical replacement required")
    value = raw.replace(before, after)
    need(value.replace(after, before) == raw, "replacement inverse failed")
    return value


def definitions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def assignment(raw, name):
    node = next(node for node in ast.parse(raw).body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)
    return b"".join(raw.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]), node


def main():
    need(sha(MANIFEST.read_bytes()) == MANIFEST_SHA and sha(RUNNER.read_bytes()) == RUNNER_SHA,
        "initial r729 manifest/runner changed")
    need(not DRAFT.exists(), "single-use formatting draft already exists")
    for name in ("r729-verification-focused.json", "r729-verification-focused-started.json",
            "r729-verification-store.json", "r729-verification-store-started.json"):
        need(not (DEST / name).exists(), "r729 already prepared or claimed")
    manifest = json.loads(MANIFEST.read_bytes())
    need(len(manifest["source_files"]) == 31 and manifest["selectors_executed"] == [],
        "initial candidate selection changed")
    preserved_rows = [*manifest["source_files"], manifest["builder"], manifest["design"],
        manifest["source_evidence"], fact(MANIFEST), fact(RUNNER)]
    need(len({row["path"] for row in preserved_rows}) == len(preserved_rows), "draft duplicate sources")
    original_inputs = manifest["original_inputs_before"]
    need(original_inputs == manifest["original_inputs_after"], "parent evidence changed")
    before_parent = [fact(named_path(row["path"])) for row in original_inputs]
    need(before_parent == original_inputs, "parent source changed before formatting")
    old = {}
    for row in preserved_rows:
        path = named_path(row["path"])
        need(fact(path) == row, "initial candidate source changed")
        old[row["path"]] = path.read_bytes()
    prefix = PACKAGE.relative_to(ROOT).as_posix() + "/"
    original = {PurePosixPath(row["path"]).name: old[row["path"]]
        for row in manifest["source_files"] if row["path"].startswith(prefix)}
    need(len(original) == 22, "complete 22 module graph required")
    files = dict(original)
    tree = ast.parse(original[NS])
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == MATCH)
    lines = original[NS].splitlines(keepends=True)
    span = b"".join(lines[node.lineno - 1:node.end_lineno])
    newline = b"\r\n" if b"\r\n" in original[NS] else b"\n"
    formatted = FORMATTED.encode().replace(b"\n", newline)
    files[NS] = replace_once(original[NS], span, formatted + newline)
    need(ast.dump(ast.parse(files[NS]), include_attributes=False) == ast.dump(tree, include_attributes=False),
        "formatting changed NS module AST")
    for name in ("personal_authority_store.py", "personal_origin_observer.py"):
        files[name] = replace_once(files[name], sha(original[NS]).encode(), sha(files[NS]).encode())
    files["personal_committed_readfacts.py"] = replace_once(files["personal_committed_readfacts.py"],
        sha(original["personal_origin_observer.py"]).encode(), sha(files["personal_origin_observer.py"]).encode())
    raw = files["personal_enrollment_bindings.py"]
    for name in ("SOURCES", "SOURCE_SIZES"):
        old_span, old_node = assignment(raw, name)
        old_values = ast.literal_eval(old_node.value)
        new_values = tuple((filename, sha(files[filename]) if name == "SOURCES" else len(files[filename]))
            for filename, _ in old_values)
        raw = replace_once(raw, old_span, old_span.replace(repr(old_values).encode(), repr(new_values).encode()))
    files["personal_enrollment_bindings.py"] = raw
    pins = ast.literal_eval(assignment(raw, "SOURCES")[1].value)
    sizes = ast.literal_eval(assignment(raw, "SOURCE_SIZES")[1].value)
    need(len(pins) == len(sizes) == 20, "binding source count changed")
    for filename, pin in pins:
        need(pin == sha(files[filename]) and dict(sizes)[filename] == len(files[filename]),
            "actual source SHA/size differs")
    for name in files:
        if name != NS:
            need(definitions(files[name]) == definitions(original[name]), "support definition changed")
    tests = {}
    for row in manifest["source_files"]:
        if row["path"].startswith(prefix):
            continue
        raw = old[row["path"]]
        for name in (NS, "personal_authority_store.py"):
            old_pin = sha(original[name]).encode()
            if old_pin in raw:
                raw = replace_once(raw, old_pin, sha(files[name]).encode())
        inverse = raw
        for name in (NS, "personal_authority_store.py"):
            inverse = inverse.replace(sha(files[name]).encode(), sha(original[name]).encode())
        need(inverse == old[row["path"]], "test changed beyond same-source SHA mapping")
        ast.parse(raw)
        tests[row["path"]] = raw
    # Preflight complete before preserving or modifying any candidate file.
    DRAFT.mkdir()
    mapping = []
    for row in preserved_rows:
        saved = DRAFT / "inputs" / row["path"]
        need(saved.absolute().is_relative_to(DRAFT.absolute()), "draft target escaped its named directory")
        saved.parent.mkdir(parents=True, exist_ok=True)
        write_new(saved, old[row["path"]])
        saved_fact = fact(saved)
        need(saved_fact["sha256"] == row["sha256"] and saved_fact["byte_size"] == row["byte_size"],
            "draft bytes changed")
        mapping.append({"original": row, "preserved": saved_fact})
    receipt = {"state": "original_static_draft_preserved_before_formatting", "source_mapping": mapping,
        "original_builder_observation": {"tool_chunk_id": "f4f2ca", "exit_code": 0,
            "manifest_sha256": MANIFEST_SHA, "runner_sha256": RUNNER_SHA,
            "product_imports_or_tests_or_prepare_or_profile": False}}
    write_new(DRAFT / "draft-preservation.json", encode_json(receipt))
    # Every replacement is hash guarded against the preflight bytes.
    for name, value in files.items():
        path = PACKAGE / name
        if value != original[name]:
            need(path.read_bytes() == original[name], "candidate changed since preflight")
            path.write_bytes(value)
    for relative, value in tests.items():
        path = named_path(relative)
        if value != old[relative]:
            need(path.read_bytes() == old[relative], "candidate test changed since preflight")
            path.write_bytes(value)
    evidence_path = named_path(manifest["source_evidence"]["path"])
    evidence = json.loads(old[manifest["source_evidence"]["path"]])
    evidence.update(formatter=fact(Path(__file__)), static_draft_preservation=fact(DRAFT / "draft-preservation.json"),
        initial_manifest_sha256=MANIFEST_SHA, initial_runner_sha256=RUNNER_SHA,
        formatting_only_namespace_AST_equal_to_initial=True, source_pins=pins, source_sizes=sizes,
        old_static_draft_file_count=len(mapping))
    need(evidence_path.read_bytes() == old[manifest["source_evidence"]["path"]], "source evidence changed")
    evidence_path.write_bytes(encode_json(evidence))
    final = copy.deepcopy(manifest)
    final["source_files"] = [fact(named_path(row["path"])) for row in manifest["source_files"]]
    final["source_evidence"] = fact(evidence_path)
    final["formatter"] = fact(Path(__file__))
    final["static_draft_preservation"] = fact(DRAFT / "draft-preservation.json")
    final["initial_manifest_sha256"] = MANIFEST_SHA
    final["initial_runner_sha256"] = RUNNER_SHA
    runner = old[fact(RUNNER)["path"]]
    runner = replace_once(runner, MANIFEST_SHA.encode(), b"__R729_FORMATTED_MANIFEST_SHA__")
    runner = replace_once(runner,
        b'[document["builder"], document["source_evidence"], document["design"]]',
        b'[document["builder"], document["source_evidence"], document["design"],\n'
        b'        document["formatter"], document["static_draft_preservation"]]')
    need(MANIFEST.read_bytes() == old[MANIFEST.relative_to(ROOT).as_posix()], "manifest changed")
    MANIFEST.write_bytes(encode_json(final))
    runner = runner.replace(b"__R729_FORMATTED_MANIFEST_SHA__", sha(MANIFEST.read_bytes()).encode())
    ast.parse(runner)
    need(RUNNER.read_bytes() == old[RUNNER.relative_to(ROOT).as_posix()], "runner changed")
    RUNNER.write_bytes(runner)
    need([fact(named_path(row["path"])) for row in original_inputs] == before_parent,
        "parent source changed during formatting")
    for row in mapping:
        need(fact(named_path(row["preserved"]["path"])) == row["preserved"], "static draft changed")
    terminal = {"state": "formatted-file-only-not-executed", "formatter": fact(Path(__file__)),
        "manifest": fact(MANIFEST), "runner": fact(RUNNER), "source_evidence": fact(evidence_path),
        "parent_named_sources_unchanged": len(before_parent), "initial_static_draft_files_preserved": len(mapping),
        "namespace_AST_exactly_equal_to_initial": True, "all_support_definitions_unchanged": True,
        "source_pins_and_sizes_actual_checked": 20, "product_imports_or_tests_or_prepare_or_profile": False}
    write_new(DEST / "r729-formatting-terminal.json", encode_json(terminal))
    print(json.dumps(terminal, sort_keys=True))


if __name__ == "__main__":
    main()
