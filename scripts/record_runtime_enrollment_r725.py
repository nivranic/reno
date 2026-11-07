"""Seal named r725 source and execution evidence without running product code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data/t16-runtime-enrollment-graph-r725"
PINNED = {
    "r725-candidate-manifest.json": "d006d07812126af97d1a93046c148eadd42abae455b135f4c5e8b758c7c90be8",
    "r725-source-evidence.json": "c842c459ed06ff0e5f8feb91e2166fe02a476a350934b68216756c1a0bb733a1",
    "r725-source-independent-review.json": "5cb6c3d4b4dc226c0ed086a96e3adb8bee87e5a237710e27f647df9570fac668",
    "r725-runner-independent-review.json": "63303e2baacaa4b5f98bd4c9a8709e32993e2bb16c97a04abf191cb0932107d0",
    "r725-runner-v2-independent-review.json": "aa752150508461bed2779f1a3cbea44e14129f81107a88101a8129d90a0737d9",
    "r725-store-result-independent-review.json": "aebb5e790a161891fe69f72b329fb442fcc86f4b9328f03cb45ffbec62ccf967",
    "r725-evidence-preservation-independent-review.json": "50a71424e9ababe4156f7f0f97a91f1dce5850d6f5c37721777f4be91a517f5b",
    "finite-initial-collection/result.json": "0cbb3d532dd1977e7f74f185d61887a3efb453065f719a1771baf83a4ad16635",
    "finite-v2/result.json": "6998e02e9b534ce5156ecf08ab25d25b1518dbcfc124619e974ca69e0b4900bb",
    "store-v2/result.json": "5d55ff604c33688ce8d68324b86b573aa0abc190a1b0eeb5777f1b3d2d3b2921",
    "README.md": "aace8f6dc68b2c40a3109fd496eba3b6693305f806b794058e63e51c13803126",
}
ATTEMPTS = (
    ("finite-initial-collection", "finite", "", 283, 2),
    ("finite-v2", "finite", "-v2", 291, 0),
    ("store-v2", "store", "-v2", 291, 1),
)
LOG_NAMES = ("stdout.log", "stderr.log", "pytest.log", "pytest-result.json",
             "execution-result.json", "child-started.json")


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "byte_size": len(raw), "sha256": sha(raw)}


def load(path):
    return json.loads(path.read_bytes())


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def verify():
    inputs = []
    for relative, expected in PINNED.items():
        row = fact(GRAPH / relative)
        need(row["sha256"] == expected, "Named evidence pin changed: " + relative)
        inputs.append(row)
    manifest = load(GRAPH / "r725-candidate-manifest.json")
    need(manifest["original_inputs_before"] == manifest["original_inputs_after"],
         "Original construction sources changed")
    expected_sources = {}
    attempts = []
    for directory, suite, suffix, count, exit_code in ATTEMPTS:
        plan_path = GRAPH / ("r725-verification-" + suite + suffix + ".json")
        claim_path = plan_path.with_name(plan_path.stem + "-started.json")
        plan, claim = load(plan_path), load(claim_path)
        need(plan["namespace_deadline_seconds"] == 30.0 and plan["profiling_enabled"] is False
             and plan["automatic_downstream_execution"] is False and len(plan["source_files"]) == count,
             "Original plan limits changed")
        need(claim["plan_sha256"] == sha(plan_path.read_bytes()) and claim["suite"] == suite,
             "Single-use claim changed")
        qa = Path(claim["root"])
        prefix = "reno-r725-" + suite + ("-v2-" if suffix else "-")
        need(qa.name.startswith(prefix), "Unexpected attempt root")
        saved = GRAPH / directory
        record = load(saved / "result.json")
        need(record["source_count"] == count and record["sources_unchanged"] is True
             and record["child_exit_code"] == exit_code,
             "Saved outcome differs from actual attempt")
        logs = []
        original_log_rows = {row["name"]: row for row in record["copied_original_logs"]}
        need(set(original_log_rows) == set(LOG_NAMES), "Saved original logs incomplete")
        for name in LOG_NAMES:
            original, copied = (qa / name).read_bytes(), (saved / name).read_bytes()
            need(original == copied, "Original QA log differs: " + directory + "/" + name)
            expected = original_log_rows[name]
            need(len(copied) == expected["byte_size"] and sha(copied) == expected["sha256"],
                 "Saved log pin changed")
            logs.append(fact(saved / name))
        result, execution = load(saved / "pytest-result.json"), load(saved / "execution-result.json")
        need(result["pytest_exit_code"] == execution["child_exit_code"] == exit_code
             and execution["prepared_sources_unchanged"] is True,
             "Original pytest/execution result disagreement")
        phases = [(row["phase"], row["outcome"]) for row in result["phases"]]
        if directory == "finite-initial-collection":
            need(phases == [] and sum(row["outcome"] == "failed" for row in result["collections"]) == 2,
                 "Original collection failure changed")
        elif directory == "finite-v2":
            need(len(phases) == 222 and all(outcome == "passed" for _, outcome in phases)
                 and sum(phase == "call" for phase, _ in phases) == 74,
                 "Finite pass count changed")
            need(b"74 passed in 3.46s" in (saved / "stdout.log").read_bytes(), "Finite stdout changed")
        else:
            need(phases == [("setup", "passed"), ("call", "failed"), ("teardown", "passed")],
                 "Original Store phase result changed")
            need(b"1 failed in 70.49s" in (saved / "stdout.log").read_bytes(), "Store stdout changed")
        for row in plan["source_files"]:
            previous = expected_sources.get(row["path"])
            need(previous is None or previous == row, "Two source plans disagree")
            expected_sources[row["path"]] = row
        attempts.append({"directory": directory, "plan": fact(plan_path), "claim": fact(claim_path),
                         "result": fact(saved / "result.json"), "logs": logs,
                         "child_exit_code": exit_code, "phase_count": len(phases), "qa_root": str(qa)})
    sources = [expected_sources[name] for name in sorted(expected_sources)]
    need(len(sources) == 292, "Combined original/v2 source set changed")
    for row in sources:
        need(fact(ROOT / row["path"]) == row, "Current named source changed: " + row["path"])
    for suite in ("whole-append", "whole-conflict", "enrollment-request",
                  "enrollment-completion", "enrollment-receiver"):
        for suffix in ("", "-v2"):
            path = GRAPH / ("r725-verification-" + suite + suffix + ".json")
            need(not path.exists() and not path.with_name(path.stem + "-started.json").exists(),
                 "An unselected downstream suite was prepared or claimed")
    return inputs, attempts, sources


def main():
    inputs, attempts, sources = verify()
    record_text = """# r725 同源接线执行记录

r721 最新 runtime 与 r724 修正后的 enrollment 已接到同一新候选中。
Namespace 精确继承两个设施文件名和一个 journal 目录名；完整 borrowed observer、
20 项实际源码绑定和两关联修正版 codec 保留。22 模块、7 测试源完成独立源码审查。

首有限运行在收集阶段因运行器遗漏 taxonomy.yaml 的读取准入退出，0 业务测试阶段；
原运行器、计划、claim 和六份原日志保持。v2 仅增八份固定静态输入及新单次执行名称。
v2 同次 **74 passed / 3.46 秒**，222 个阶段全部通过，291 份输入运行前后及当前核同。
这些测试包含合成 SQLite、临时文件系统和 lifecycle，不证明真实 owner/activation 成功。

原完整 Store 唯一运行 **1 failed / 70.49 秒**，291 份输入保持。
原安装、internal resume、显式 repeat 相等、正常初始化退出及原 DB 字节保全已返回；
SH profile、runtime Namespace EX、Store 和 IoStack 已进入。operations 第 11 层数字 4
的目录创建、node writer 正常关闭、read custody 及 verify-stage 真读/身份核已返回。
当前 rename 的首 effect 中，Namespace reading observation body 的 facts scan 已枚举完，
末目录 verify 已返回，但 af._scan:265 的末 budget check 创建 NAMESPACE_DEADLINE。
scan 返回、NS 名称相等/后续 pin/lock/ledger/tail、正常 observation finish 均未达。
当前 rename 的 graph/native NtSetInformationFile、node 发布、后续 32 位 walk、prepare/put
及最终正常退出也未达。两 Namespace cleanup 附注、Stage retained 和 Store exit failure 保留。

这次是新的完整同源接线验证，未建立性能收益或热点因果；保留原 30 秒门和全部原 fixture。
whole/enrollment 下游未准备或运行，所有测试执行句柄已终态。原正常服务是长驻服务，
本轮未查询或操作它；未操作真实库、生产 dist、已有 ACL、Codex 设置、模型平台或 commit/push。
r723 单次采样许可已消耗，本轮没有新 profiling。H01 与所有原后续范围仍未完，完整 goal active。
城市仍为浙江绍兴，工序和防水品牌/型号未定。
"""
    note = GRAPH / "execution-record.md"
    write_new(note, record_text.encode("utf-8"))
    terminal = {
        "kind": "r725_runtime_enrollment_integration_terminal",
        "state": "same_source_finite_passed_original_full_store_failed",
        "seal_script": fact(Path(__file__)), "evidence": inputs,
        "attempts": attempts, "sources_current": sources,
        "source_count_v2": 291, "combined_preserved_source_count": 292,
        "candidate_module_count": 22, "candidate_test_file_count": 7,
        "formal_source_count_preserved": 193, "construction_original_input_count_preserved": 249,
        "copied_original_log_count": 18, "source_and_log_preservation_confirmed": True,
        "finite_passed": 74, "finite_duration_seconds": 3.46,
        "full_store_failed": 1, "full_store_duration_seconds": 70.49,
        "full_store_failure": "Namespace facts scan final Budget.check created NAMESPACE_DEADLINE before current node rename",
        "original_namespace_deadline_seconds": 30.0, "profiling_enabled": False,
        "downstream_suites_prepared_or_run": False, "performance_improvement_proved": False,
        "actual_registered_enrollment_owner_and_activation_success_proved": False,
        "goal_status": "active", "goal_complete": False, "H01_complete": False,
        "normal_service_or_real_database_or_production_dist_operated": False,
        "existing_ACL_or_Codex_settings_changed": False, "commit_or_push_performed": False,
        "new_automatic_approval_rejection": False,
        "remaining_scope": "All original H01-H06, true share/revocation, P06, SourceMap/work, conditional T15 and real cloud requirements retained",
        "city": "浙江省绍兴市", "current_stage": "未定", "waterproof_brand_model": "未定",
        "execution_record": fact(note),
    }
    need(verify() == (inputs, attempts, sources), "Evidence changed while sealing")
    path = GRAPH / "r725-terminal.json"
    raw = (json.dumps(terminal, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_new(path, raw)
    need(path.read_bytes() == raw, "Terminal writeback mismatch")
    print(json.dumps({"terminal": fact(path), "state": terminal["state"],
                      "preserved_sources": len(sources), "log_count": 18}, ensure_ascii=False))


if __name__ == "__main__":
    main()
