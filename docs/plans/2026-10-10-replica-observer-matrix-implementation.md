# Replica observer matrix Implementation Plan

> **For Codex:** 使用现有 `executing-plans` 技能按本计划推进；本批实现、审查和 Root 执行已在当前 goal 范围内授权，不新增用户确认、提交或推送步骤。

**Goal:** 在保留原 12 项选择器、1680 秒矩阵期限和全部原断言的前提下，执行原矩阵正文，并用已接通的 r766 observer 保留真实具名协议边界；继续完整产品方案。

**Architecture:** 独立 r770 runner 从已冻结 r769 runner 完整复制，固定为矩阵执行模式。保留真实收集、Windows 条件插件、有限函数槽、两个文件写桥及原进程监督链，只调整 collect-only 模式与对应的验后合同。父进程严格核对真实 JUnit 具名结果、原 12 项 table/map、observer 完整事件和资源终态，失败时完整保留原字节。

**Tech Stack:** Windows、CPython 3.12.10、pytest 9.1.1、pluggy 1.6.0、原 Native Job/HANDLE 监督、r766 observer、r767 function-slot helper。PowerShell 仅使用 `pwsh`。

## 已确认前件与证据边界

- r769 当前 runner 为 108643 bytes / SHA256 `a238f8edab00c46f1296be90a31a20deecb782482029470b0c29830445331b5c`；原 manifest 为 247211 bytes / `f874d60faabcbdf4f3cbf0bc981030827f0b6d3489b4f95476baa3712e82ce7f`，1186 个来源。
- 当前 r769 `run-01/result.json` 实际 runner/child/pytest 返回均为 0，1186 来源保持。其证据是实际收集 12、JUnit 0 tests、唯一 `session_return` 事件和 `status_unknown`，属于收集接线成功。正式结果独审 `review-candidates/probe-result-independent-review.json` 为 24452 bytes / `82a9ce477e891f20d0c11a803e09396d795802a8bf1bb2fb7d11c820d173df2b`，Root `delivery.json` 为 5717 bytes / `3d8187b53a60f42a41613c98d9edba6132171c983dc56f2fa8d329bc660337ef`；独审和交付已经完成，均不将此记为新增 case pass。
- 当前已确认的目录具名 pass 仍为此前两批的 3 项；原 93 项整体尚未通过。本批原矩阵含 10 项真实 native case 和 2 项 pure oracle，pure pass 不充当 native 执行或恢复证明。
- r763 保留的旧矩阵在原 1680 秒 wall 超时，只有 8 个 stdout dots，没有具名终态、JUnit 或实际 pytest 返回。原结果、旧 claim、1027 来源和 8 份完整 before 保持；8 dots 不加进 pass 数，也不据此指定旧卡住 case 或 phase。
- `data/t16-h02-replica-dynamic-bindings-r767/exploration-candidates/matrix-static-flow.json` 只证明源码重复调用。它没有证明旧超时根因、运行主耗时、实际分支或新缓存可行性；本批不实施其性能候选。

## Task 1：保留祖先，创建固定执行模式

**文件与责任：**

- Worker 创建 `data/t16-h02-replica-observer-matrix-r770/harness-candidates/supervised-runner-matrix-draft01.py`。
- Worker 创建 `data/t16-h02-replica-observer-matrix-r770/root-candidates/prepare-matrix-inputs.py` 和 `preserve-matrix-run.py`。
- 将 r769 上述三个实际原稿完整保存到 r770 `before/`，记录原字节数及完整 SHA256，再修改新候选。
- 本计划和 `data/t16-h02-replica-observer-matrix-r770/exploration-candidates/matrix-scope.json` 由范围设计代理拥有；独审文件由 reviewer 拥有；Root 独占 freeze、claim 和实际执行。各方不回退其他人的变更。

**步骤：**

1. 新 `BATCH` 仅指向 r770，固定 `RUN_KIND = "actual_original12_matrix_with_case_phase"`，固定 lane `ordinary_matrix_pure`，新 CLI 为 `run-NN --matrix` 和内部 `--child-matrix run-NN nonce`。
2. 原选择器严格保持：`tests/test_personal_authority_replica_directory_native.py::test_real_directory_chain_mode_source_matrix` 与 `::test_pure_boundary_oracle_includes_anchor_and_is_not_native_coverage`。参数、顺序、节点身份、原 LongIds 行为和 12 项计数保持。
3. 从实际 pytest 参数中移除 `--collect-only`；在实际 Config option 门要求 `collectonly is False`，不得只删 CLI 参数而留下 True 门或 probe-only 验后。
4. 新 bindings、started、claim、child/session return 和 result 均如实声明固定执行模式。不得沿用 `fixed_collect_only_mode=True` 或把 execution 成功硬编码成 True。
5. 保留原两次 self 身份、exact singleton Job、DETACHED、GO、20/25/10 启动与收尾期限、1680 秒原矩阵 wall、所有正常 writer/HANDLE/Job 收尾顺序。九组 93 的定义和其他 lane 期限不改，本批只准入矩阵 lane。

**选择理由：** 单独固定执行 runner 的 delta 最小、祖先证据清楚。给 r769 添加可切换的双模式会增加不需要的分支；拆成逐项运行则改变原聚合期限与运行合同，本批均不采用。

## Task 2：保持已验证接线与原产品断言

**步骤：**

1. r766 observer 15736-byte API、expected nodeids、三普通 pytest hooks和全部字节上限原样保留。r767 slot helper、r768 的固定 Windows `terminalprogress` 对象/源码绑定、r769 两个具名 Python 文件写桥保留。
2. 保留所有现有 source/code/descriptor、registered-plugin、HookImpl/flags、默认 main collection/loop、Session.perform_collect、后续 modifier/filter、有限函数槽重核。执行模式只让原 pinned runtestloop 走其正常 case 分支，不新增 producer 或允许任意插件。
3. 不把上一次 gate 成功缓存为下一次成功，不缓存 live namespace、descriptor、function defaults/kwdefaults/cells、G1/lock/file 身份等 mutable 校验结果。不去掉 checkpoint、原 first、期限、完整 cleanup、正常 close 或独立 oracle。
4. 产品与测试文件完全保持。原 matrix 的 caller-held G1、每目录 `3*n` acquire/normal-close、`6*n` 原事件、完整 original-registry retirement、独立摘要及 checker-zero-effect 断言仍由原 case 正文执行。
5. 本矩阵原正文明确 `source_observed/copy_performed/data_restored/write_authority/sender_authority=False`。即使 10 个 native case 全部通过，也只证明该目录单元合同，不证明 raw 复制、完整 restore、ACL 或正常服务加载。
6. 不新增 clock/duration/frame/args/locals profiling。保留 runner 原 wall 和现有 pytest 输出的原始值；observer 只记录协议开始、已报告阶段、协议结束和实际会话返回。

## Task 3：执行模式验后合同

**文件：** 新 runner 的原 `after_return_probe`、`probe_result_evidence` 及 result 判定替换为明确的矩阵 return/evidence。原工具/child/pytest 返回保持独立，不改写实际返回来补齐元数据。

**成功需要同时满足：**

1. 真实 tool、runner、native child、解释器和 pytest 返回的对应成功终态实际建立；GO 成立，原监督失败为空，所有原 writer/Job/HANDLE 已按原路径收尾。Root 必须记录实际工具层返回，不能从内部 JSON 推断外部工具成功。
2. 新来源和原 1186 祖先来源在始末保持，所有历史 manifest 和完整 before 映射保持。原 12 项 table 与原 SHA/nodeids/classification 合同一致；map 显示 selected=12、changed=0、mapping=[]。
3. bindings 声明并证明 `collectonly is False` 及现有选择函数槽/dispatch/条件插件合同。实际 Session `testscollected` 为 exact int 12，`testsfailed` 为 exact int 0。
4. JUnit 真正含原 12 个唯一 testcase，完整 classname/name 与原 table 的 12 个实际 nodeid 一一对应，计数 tests=12、failures/errors/skipped 全为 0。检查真实 testcase 内容与唯一性，不能只读 testsuite 汇总或依据 stdout dots 推 pass。
5. observer 每项具有连续 `case_start → phase_report(setup,passed) → phase_report(call,passed) → phase_report(teardown,passed) → case_end`，按原 index 0–11 共 60 行，然后真实 pytest 返回后的 `session_return` 为第 61 行。schema、nonce、lane、collection SHA、sequence、index、phase/outcome及 canonical UTF-8 bytes 逐项核对。
6. 每行不超过 1024 bytes，总 JSONL 不超过 62464 bytes，table 不超过 131072 bytes、status 不超过 1024 bytes。末状态为 `healthy`、last_sequence=61、case_index=11；status 单次写返回完整字节数，三个 observer sink 正常 close 原记录成立。
7. 只有上述实际证据齐全，才将当批 `case_execution_success` 建立为真。10 native/2 pure 必须分别标明；`full_93_success=False` 保持，跨批唯一 pass 数由结果 reviewer 核对后计算。

**事件的实际含义：** `setup/call/teardown` 是已返回的 TestReport；`case_start/case_end` 是 pytest 协议边界。observer healthy 只描述该观察序列，不证明某个当前 body/fixture 位置或任何资源 normal-close。原 native 正常关闭结论来自未改原 case 断言，不能由 Job 终态或 observer 单独推出。

**失败与部分结果：**

- 原 case 失败、skip、collection error、观察失败或原 wall 超时均保留实际返回及全部 raw。成功的严格 61 行模式不能作为抛弃失败轨迹的理由。
- 报告实际具名 JUnit 结果及有效 observer 前缀，并分别列缺失/残缺/超限/未正常关闭的证据。缺少 session return 或 status 不得制造一条来补全；原控制异常传播及 unknown 保持。
- 本批 timeout 若留下 `case_index` 与最后已报告 phase，只能报告这两个真实边界；不能把它改写成 body/fixture/profile 采样或声称其就是旧 r763 超时原因。
- 失败不扩时、不复用旧 claim、不自动重跑未改红。Root 观察超时或暂时工具错误时继续核同一个活 handle，未证终态前不得重启或以 terminal 名义保全。

## Task 4：独审、Root 冻结与一次原矩阵执行

**独审文件：**

- `review-candidates/matrix-source-independent-review.json`：限定模式/事件/JUnit验后 delta 与失败保留。
- `review-candidates/matrix-effects-independent-review.json`：原启动、GO、native、writer/Job/HANDLE effect顺序，以及产品/tests/九lane/期限保持。
- `review-candidates/matrix-root-helpers-independent-review.json`：完整祖先保留、CREATE_NEW freeze/claim、保全包原/副本一致和真实终态门。

**Root 顺序：**

1. 核对上述已完成的 r769 成功结果独审及交付；新三个候选和本计划/范围文件均稳定，真实 source finding 修闭后记录完整 before 和独审实文件 facts。
2. 新 manifest 从 r769 当前 1186 逐源继承，不修改原 manifest；增加新 runner/helpers/plan/scope/reviews/来源声明。只写一次新的 `test-inputs.json`，始末重核全部来源和 manifest 原字节。
3. 新 `lane-claim-ordinary_matrix_pure.json` 在该实际运行写入时单次建立，不创建替代旧 r763/r767/r768/r769 claim，不重标旧失败。准入是当前授权范围内的工程事实记录，不增加用户审批步骤。
4. 按新 helper 实际经审查的 CLI 执行准备；下列命令仅交给 Root 在对应步骤运行，本范围设计代理不执行：

```powershell
& .venv/Scripts/python.exe -I -B data/t16-h02-replica-observer-matrix-r770/root-candidates/prepare-matrix-inputs.py
& .venv/Scripts/python.exe -I -B data/t16-h02-replica-observer-matrix-r770/harness-candidates/supervised-runner-matrix-draft01.py run-01 --matrix
```

5. 保持同一实际 tool/session/Job 的监督直到权威终态；及时在当前聊天报告真实变化，不用新采样填补未知。原 wall 上限保持 1680 秒。
6. Root 记录真实 tool/runner/child/interpreter/pytest 与 writer 终态；再用独审后的 `preserve-matrix-run.py` 保全全部原目录/files/raw，核原/副本/原址末 inventory 同；未终态不得假称已保全完成。
7. Reviewer 独核实际 JUnit 12 项、61-event 成功条件或有效失败前缀、来源保持、所有返回层及原副本一致。Root 再写 batch delivery、主计划新进展及 AGENTS；本范围代理不写主计划 §17.229 或 AGENTS。

## Task 5：验后继续原 goal

- 成功：报告该原矩阵 12 项真实结果及范围，再推进 callback/reentry 和其他原 lane/93；不重跑已保持且无新问题的先行 3 项只为凑单次整套 green。
- 失败：保留新具名证据与旧超时，依据本批实际边界确定必要源码后继。r767 静态成本候选若进入实施，必须单独保持原 mutable 校验/first/期限/cleanup，不把本批计划当缓存许可。
- H01 原 30 秒、H02 全部 source/target/coordinator/restore/consumer、H03–H06/writers、永久分享、P06 历史 provenance/SourceMap/work、条件 T15、生产云与原产品方案其余工作继续保留，goal active。
- 不修改正常 DB/service/dist、ACL、Codex settings、真实模型平台；不 commit/push/stash/reset/clean。回退仅废弃未运行的新候选并保留全部历史/失败字节，不修改已运行祖先或恢复旧 claim。

**本文件的完成边界：** 当前只完成静态范围与实施计划，不代表 r770 候选、冻结、准入、实际矩阵或原产品目标已完成。
