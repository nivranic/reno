"""Finite byte-only enrollment completion investigation; never opens a Store.

prepare copies only package/test paths declared by the frozen r483 manifest.
red runs the original codec against two contradictory completion declarations.
Each phase writes fresh artifacts exclusively and refuses implicit retries.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/t16-enrollment-contract-completion-r724"
OLD_BASE = "data/t16-enrollment-sql-trigger-r483/"
OLD_PACKAGE = OLD_BASE + "authority_enrollment_r478/"
MANIFEST = ROOT / (OLD_BASE + "r483-frozen-manifest.json")
CURRENT_SOURCE_FACTS = ROOT / "data/t16-public-checklist-summary-preflight-20261009.json"
MANIFEST_SHA = "02420389624d2004960233f1b5f496af47d14be726540251d48dfe12526cf9bb"
CURRENT_SOURCE_FACTS_SHA = "a4efb443a352514fbfd8a666a2feb9bea71d5f77056fbd5ad1a83131b248521c"
CODEC_SHA = "dd6d1681748ce07a6c83bdc9434d017428412e0153f0c16aa7c3266ac2278014"
TEST_SHA = "ccdaaf880f3b7def688108ef75e470e06516756218ed44e3ae8d18431fcf9a7d"
BASELINE_SOURCE_SET = "84e1fa07d0cf7e64b3ef5822e02dcddaf84e1934703b5159b74f743cac4eb003"
TEST_NAMES = ("test_enrollment_contract.py", "test_enrollment_owner_boundary.py", "test_activation_sql_engine.py")
OLD_EXTRA = (
    "data/t16-enrollment-family-r478/authority_enrollment_r478/personal_enrollment_contract.py",
    "data/t16-enrollment-family-r478/test_enrollment_contract.py",
)


def need(value, message):
    if not value:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": digest(raw)}


def write_bytes(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path, value):
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def selected():
    raw = MANIFEST.read_bytes()
    need(digest(raw) == MANIFEST_SHA, "original frozen r483 manifest bytes changed")
    document = json.loads(raw)
    rows = [row for row in document["sources"]
        if row["path"].startswith(OLD_PACKAGE)
        or row["path"] in {OLD_BASE + name for name in TEST_NAMES}]
    need(len(rows) == 25 and len({row["path"] for row in rows}) == 25, "exact 25-path frozen copy set required")
    for row in rows:
        path = ROOT / row["path"]
        need(path.resolve().is_relative_to(ROOT.resolve()), "copy source escaped workspace")
        need(fact(path) == row, "frozen copy source changed: " + row["path"])
    for relative, expected in zip(OLD_EXTRA, (CODEC_SHA, TEST_SHA), strict=True):
        need(fact(ROOT / relative)["sha256"] == expected, "original r478 byte source changed")
    return rows


def current_sources():
    raw = CURRENT_SOURCE_FACTS.read_bytes()
    need(digest(raw) == CURRENT_SOURCE_FACTS_SHA, "current source preflight bytes changed")
    document = json.loads(raw)
    need(document["source_file_count"] == 193 and document["source_set_sha256"] == BASELINE_SOURCE_SET,
        "current 193-source baseline mismatch")
    rows = document["production_sources"]
    need(len(rows) == 193 and len({row["path"] for row in rows}) == 193, "source rows incomplete")
    for row in rows:
        need(fact(ROOT / row["path"]) == row, "current production source changed: " + row["path"])
    return rows


def copy_facts(rows):
    result = []
    for row in rows:
        relative = row["path"][len(OLD_BASE):]
        path = DEST / relative
        actual = fact(path)
        need(actual["sha256"] == row["sha256"] and actual["byte_size"] == row["byte_size"],
            "new byte copy mismatch: " + relative)
        result.append(actual)
    return result


def load_original_declarations():
    # The package __init__ is an empty declaration. The selected test imports
    # only this codec and pytest, and its prefix() constructs JSON documents.
    need(fact(DEST / "authority_enrollment_r478/personal_enrollment_contract.py")["sha256"] == CODEC_SHA,
        "new original codec bytes changed")
    need(fact(DEST / "test_enrollment_contract.py")["sha256"] == TEST_SHA, "new original test bytes changed")
    sys.path.insert(0, str(DEST))
    from authority_enrollment_r478 import personal_enrollment_contract as codec
    need(Path(codec.__file__).resolve() == (DEST / "authority_enrollment_r478/personal_enrollment_contract.py").resolve(),
        "codec import resolved outside new successor")
    spec = importlib.util.spec_from_file_location("r724_original_enrollment_contract_test", DEST / "test_enrollment_contract.py")
    need(spec is not None and spec.loader is not None, "test declaration loader missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    need(module.codec is codec, "prefix and harness codec differ")
    return codec, module


def prepare():
    rows = selected()
    production = current_sources()
    before = {"r483_manifest": fact(MANIFEST), "r478_originals": [fact(ROOT / path) for path in OLD_EXTRA],
        "production_sources": production, "selected_old_sources": rows}
    DEST.mkdir(exist_ok=False)
    for row in rows:
        destination = DEST / row["path"][len(OLD_BASE):]
        destination.parent.mkdir(exist_ok=True)
        write_bytes(destination, (ROOT / row["path"]).read_bytes())
    need(selected() == rows and current_sources() == production, "prepare inputs changed")
    copied = copy_facts(rows)
    write_json(DEST / "r724-source-ready.json", {"kind": "r724_byte_only_source_ready", "state": "copied-original-not-executed",
        "script": fact(Path(__file__)), "before": before, "copied_sources": copied,
        "copied_count": len(copied), "codec_changed": False, "no_store_or_sql_or_runtime_calls": True})
    print(json.dumps({"state": "source-ready", "copied_count": len(copied), "codec_sha256": CODEC_SHA,
        "production_source_count": len(production), "output": str(DEST / "r724-source-ready.json")}, sort_keys=True))


def red():
    ready = json.loads((DEST / "r724-source-ready.json").read_bytes())
    need(ready["script"] == fact(Path(__file__)), "script changed since source-ready")
    rows = selected()
    need(ready["before"]["selected_old_sources"] == rows, "frozen old source set changed")
    production = current_sources()
    need(ready["before"]["production_sources"] == production, "current source set changed")
    need(copy_facts(rows) == ready["copied_sources"], "new original copy changed before red")
    output = DEST / "red"
    output.mkdir(exist_ok=False)
    codec, declarations = load_original_declarations()
    raws = declarations.prefix()
    need(type(raws) is tuple and len(raws) == 3, "original prefix is not three original bytes")
    records = codec.cross_validate(raws)
    need(records[0]["payload"]["init_kind"] == "origin", "origin baseline required")
    for index, raw in enumerate(raws):
        write_bytes(output / f"original-{index + 1}.json", raw)
    results = []
    for name, index, replacement in (("epoch", 5, 2), ("genesis-sha", 6, "4" * 64)):
        completion = codec.decode(raws[2])
        original_value = completion["payload"]["binding"][index]
        need(original_value != replacement, "counterexample did not change binding")
        completion["payload"]["binding"][index] = replacement
        changed = codec.encode(completion)
        altered = (raws[0], raws[1], changed)
        need(altered[:2] == raws[:2], "counterexample request/prepare bytes changed")
        accepted, error = False, None
        try:
            observed = codec.cross_validate(altered)
            accepted = observed[2]["payload"]["binding"][index] == replacement
        except codec.EnrollmentContractError as exc:
            error = exc.code
        write_bytes(output / (name + "-completion.json"), changed)
        results.append({"case": name, "binding_index": index, "original_value": original_value,
            "contradictory_value": replacement, "original_request_prepare_bytes_unchanged": True,
            "accepted_by_original_cross_validate": accepted, "error_code": error,
            "raw_sha256": digest(changed)})
    need(selected() == rows and current_sources() == production and copy_facts(rows) == ready["copied_sources"],
        "red source preservation failed")
    result = {"kind": "r724_enrollment_completion_original_counterexamples", "state": "confirmed-contract-gap" if all(
        item["accepted_by_original_cross_validate"] for item in results) else "unexpected-outcome",
        "script": fact(Path(__file__)), "original_valid_prefix_accepted": True, "cases": results,
        "original_codec_sha256": CODEC_SHA, "original_test_sha256": TEST_SHA,
        "source_ready_sha256": digest((DEST / "r724-source-ready.json").read_bytes()),
        "production_sources_193_preserved": True, "frozen_25_old_sources_and_new_copies_preserved": True,
        "runtime_store_not_executed": True, "fake_owner_or_witness_created": False,
        "profile_or_clock_diagnostic_performed": False}
    write_json(output / "result.json", result)
    write_bytes(output / "counterexamples.log", (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"state": result["state"], "cases": results, "result": str(output / "result.json")}, sort_keys=True))
    # Explicit defect outcome: both contradictory bytes were accepted.
    return 1 if result["state"] == "confirmed-contract-gap" else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "red"))
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare()
        return 0
    return red()


if __name__ == "__main__":
    raise SystemExit(main())
