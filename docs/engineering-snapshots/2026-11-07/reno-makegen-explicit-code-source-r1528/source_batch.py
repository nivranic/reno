"""Single-use byte/text reconstruction; never parse or execute candidate C."""
from pathlib import Path, PurePosixPath
import difflib
import hashlib
import json
import re
import sys

assert __debug__
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
assert HERE.name == "reno-makegen-explicit-code-source-r1528"
assert len(sys.argv) == 2, "supply the read-only reference repository root"
PARENT = ROOT / "reno-loader-body-operands-source-r1524"
CHANGED = "runtime/Objects/genobject.c.txt"
REFERENCE_SOURCE = "data/t16-h01-catalog-generator-route-r1126/cpython-v3.12.10/Objects/genobject.c"
REFERENCE_IDENTITY = {"bytes": 78785, "sha256": "7c13a1de31fc8af6e082fc9aa055bcfe980786b4104fec0f7564e40735e44cad"}
INPUTS = ("DESIGN.md", "ROOT-DESIGN-REVIEW.json", "DESIGN-REVIEW.json",
          "EDITS.json", "source_batch.py")
OUTPUTS = ("INTEGRATION-BLOCK.c.txt", "FULL-EDITS.diff", "BLOCK-PREVIEW.diff",
           "INTEGRATION-INVENTORY.json", "STATIC-VERIFICATION.json")
for name in (*OUTPUTS, "before-01", "working-01"):
    assert not (HERE / name).exists(), "existing output: " + name


def identity(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def relative(name):
    part = PurePosixPath(name)
    assert not part.is_absolute() and ".." not in part.parts
    assert not any(c in name for c in ("\\", ":"))
    return part


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def privacy(name, data):
    values = strings(json.loads(data)) if name.endswith(".json") else (data.decode("utf-8"),)
    for value in values:
        assert not re.search(r"\b[a-z]:[\\/]|[\\/]Users[\\/]|(?:^|\s)\\\\", value, re.I), name


parent_final_data = (PARENT / "FINAL-REVIEW.json").read_bytes()
assert identity(parent_final_data) == {"bytes": 13801, "sha256": "7f0c85b54d9b9b055ee5cec3f515880a43eb61f1f632d3c5dedee2dcfa81876e"}
parent_final = json.loads(parent_final_data)
assert len(parent_final["artifact_identities"]) == 62
for name, expected in parent_final["artifact_identities"].items():
    assert identity((PARENT / relative(name)).read_bytes()) == expected, name
parent_report = json.loads((PARENT / "STATIC-VERIFICATION.json").read_bytes())
baseline = PARENT / "working-01"
parent_sources = {p.relative_to(baseline).as_posix(): p.read_bytes()
                  for p in sorted(baseline.rglob("*")) if p.is_file()}
assert {n: identity(d) for n, d in parent_sources.items()} == {n: p["working"] for n, p in parent_report["sources"].items()}
assert len(parent_sources) == 22 and sum(map(len, parent_sources.values())) == 2058033
assert CHANGED not in parent_sources
reference = (Path(sys.argv[1]) / relative(REFERENCE_SOURCE)).read_bytes()
assert identity(reference) == REFERENCE_IDENTITY
before = dict(parent_sources)
before[CHANGED] = reference
before = dict(sorted(before.items()))
assert len(before) == 23 and sum(map(len, before.values())) == 2136818
flags = parent_report["inherited_bool_fields"]
assert len(flags) == 122 and all(isinstance(v, bool) for v in flags.values()) and sum(flags.values()) == 48
caller = "reno-loader-native-parent-observation-publication-r1507/CALLER-EDITS.json"
caller_identity = {"bytes": 76433, "sha256": "d19ef9b061c95eefc81ced380369cb50a88a8e362fe7fe54a68434d366ff3059"}
assert identity((ROOT / relative(caller)).read_bytes()) == caller_identity
inputs = {name: identity((HERE / name).read_bytes()) for name in INPUTS}
assert inputs["DESIGN.md"] == {"bytes": 5456, "sha256": "625e552e1d76929159ca065887f52e4c07405effd96dd4721e682c9da5014e25"}
root_design = json.loads((HERE / "ROOT-DESIGN-REVIEW.json").read_bytes())
independent_design = json.loads((HERE / "DESIGN-REVIEW.json").read_bytes())
assert root_design["design_identity"] == independent_design["design_identity"] == inputs["DESIGN.md"]
assert root_design["root_design_review_passed"] is True
assert independent_design["independent_design_review_passed"] is True
reviews = {}
for name in ("ROOT-PREPARATION-REVIEW.json", "PREPARATION-REVIEW.json"):
    review = json.loads((HERE / name).read_bytes())
    assert set(review) == {"reviewed_input_identities", "source_review_passed",
                           "tool_review_passed", "expected_output_identities"}
    assert review["reviewed_input_identities"] == inputs, name
    assert review["source_review_passed"] is True and review["tool_review_passed"] is True
    reviews[name] = review

edit_material = json.loads((HERE / "EDITS.json").read_bytes())
assert edit_material["source"] == CHANGED and len(edit_material["edits"]) == 4
assert edit_material["added_reference_source"] == REFERENCE_SOURCE
assert edit_material["added_reference_identity"] == REFERENCE_IDENTITY
edits = edit_material["edits"]
old = before[CHANGED].decode("utf-8")
new = old
for edit in edits:
    assert new.count(edit["before"]) == 1, edit["id"]
    new = new.replace(edit["before"], edit["after"], 1)
reverse = new
for edit in reversed(edits):
    assert reverse.count(edit["after"]) == 1, edit["id"]
    reverse = reverse.replace(edit["after"], edit["before"], 1)
assert reverse == old
assert old.count("make_gen(") == new.count("make_gen(") == 4
assert old.count("func->func_code") == 2 and new.count("func->func_code") == 4
assert old.count("    PyCodeObject *code = (PyCodeObject *)func->func_code;") == 1
assert new.count("    PyCodeObject *code = (PyCodeObject *)func->func_code;") == 0
flags_line = "    int coro_flags = ((PyCodeObject *)func->func_code)->co_flags &"
assert old.count(flags_line) == new.count(flags_line) == 1
for type_name in ("PyGen_Type", "PyAsyncGen_Type", "PyCoro_Type"):
    assert old.count("make_gen(&" + type_name + ", func)") == 1
    assert new.count("make_gen(&" + type_name + ", func, (PyCodeObject *)func->func_code)") == 1
for token in ("Py_INCREF(", "Py_XINCREF(", "Py_DECREF(", "Py_XDECREF(", "Py_NewRef(", "Py_XNewRef(",
              "PyErr_", "_PyErr_", "PyObject_GC_NewVar(", "_PyObject_GC_TRACK(", "compute_cr_origin("):
    assert old.count(token) == new.count(token), token
working = dict(before)
working[CHANGED] = new.encode("utf-8")
assert all(parent_sources[n] == working[n] for n in parent_sources)
pairs = {n: {"before": identity(before[n]), "working": identity(working[n])} for n in before}
block = (PARENT / "INTEGRATION-BLOCK.c.txt").read_bytes()
assert identity(block) == {"bytes": 102779, "sha256": "9c8338a7661f9bb39bfd9f6ae082ec653e9c16691cdf45b076be72b44d04ec03"}
source_diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
    fromfile="before-01/" + CHANGED, tofile="working-01/" + CHANGED)).encode("utf-8")
block_diff = b""
inventory = {
    "scope": "explicit private make_gen code input text candidate",
    "parent_material": PARENT.name, "parent_final_identity": identity(parent_final_data),
    "source_baseline": PARENT.name + "/working-01", "input_identities": inputs,
    "parent_source_count": 22, "added_reference_source": REFERENCE_SOURCE,
    "added_reference_identity": REFERENCE_IDENTITY, "added_candidate_source": CHANGED,
    "changed_source": CHANGED, "literal_edits": edits, "source_pairs": pairs,
    "block_identity": identity(block), "source_diff": identity(source_diff), "block_diff": identity(block_diff),
    "caller_path": caller, "caller_identity": caller_identity,
    "full_added_reference_reconstructed_by_reverse_edits": True,
    "source_only_closed_fields": [], "candidate_executed": False,
    "complete_child_implemented": False, "complete_exec_dispatch_implemented": False, "full_goal_complete": False,
}
inventory_data = encode_json(inventory)
report = {
    "scope": "UTF-8 text evidence only; no C control-flow, ABI, compatibility or budget proof",
    "parent_material": PARENT.name, "source_baseline": PARENT.name + "/working-01",
    "candidate_source": HERE.name + "/working-01", "source_count": 23,
    "parent_source_count": 22, "added_reference_source": REFERENCE_SOURCE,
    "added_reference_identity": REFERENCE_IDENTITY,
    "before_total_bytes": sum(map(len, before.values())), "working_total_bytes": sum(map(len, working.values())),
    "sources": pairs, "input_identities": inputs,
    "artifact_identities": {"INTEGRATION-BLOCK.c.txt": identity(block), "FULL-EDITS.diff": identity(source_diff),
                            "BLOCK-PREVIEW.diff": identity(block_diff), "INTEGRATION-INVENTORY.json": identity(inventory_data)},
    "inherited_bool_fields": flags, "inherited_bool_count": 122, "inherited_true_count": 48, "inherited_false_count": 74,
    "full_added_reference_reconstructed_by_reverse_edits": True,
    "explicit_private_code_parameter_and_three_current_code_callers_as_text": True,
    "original_flags_and_postallocation_name_origin_bodies_preserved_as_text": True,
    "parent_22_sources_and_selector_block_unchanged": True,
    "latest_four_caller_material_unchanged": True,
    "new_owning_or_error_operations_added": False, "positive_checks_are_text_facts_only": True,
    "full_v3_code_view_selection_admitted": False, "actual_frame_allocation_safety_or_compat_proved": False,
    "actual_abi_build_runtime_proved": False, "physical_peak_budget_proved": False,
    "source_only_closed_fields": [], "candidate_executed": False,
    "complete_child_implemented": False, "complete_exec_dispatch_implemented": False,
    "full_goal_complete": False, "goal_status": "active",
}
payloads = {}
for stage, sources in (("before-01", before), ("working-01", working)):
    for name, data in sources.items():
        payloads[stage + "/" + name] = data
payloads.update({"INTEGRATION-BLOCK.c.txt": block, "FULL-EDITS.diff": source_diff,
                 "BLOCK-PREVIEW.diff": block_diff, "INTEGRATION-INVENTORY.json": inventory_data,
                 "STATIC-VERIFICATION.json": encode_json(report)})
predicted = {name: identity(data) for name, data in payloads.items()}
assert len(predicted) == 51
for name, review in reviews.items():
    assert review["expected_output_identities"] == predicted, name
for name in (*INPUTS, "ROOT-PREPARATION-REVIEW.json", "PREPARATION-REVIEW.json"):
    privacy(name, (HERE / name).read_bytes())
for name, data in payloads.items():
    privacy(name, data)
assert not HERE.is_symlink() and not HERE.is_junction()
for name in payloads:
    target = HERE / relative(name)
    assert target.resolve().is_relative_to(HERE)
    assert not target.exists() and not target.is_symlink() and not target.is_junction()
    for ancestor in target.parents:
        if ancestor == HERE:
            break
        assert not ancestor.is_symlink() and not ancestor.is_junction()
for name, data in payloads.items():
    target = HERE / relative(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(data)
print(json.dumps({"single_execution_count": 1, "output_file_count": len(payloads),
                  "before_source_bytes": report["before_total_bytes"], "working_source_bytes": report["working_total_bytes"],
                  "genobject_identity": identity(working[CHANGED]), "source_flags": [122, 48, 74],
                  "candidate_executed": False, "complete_child_implemented": False, "full_goal_complete": False}))
