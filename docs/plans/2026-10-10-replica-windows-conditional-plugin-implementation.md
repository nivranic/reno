# Replica Windows conditional plugin Implementation Plan

**Goal:** 修复 r767 实际收集门暴露的 Windows 条件插件分类缺口，继续原 12 项真实 collect-only 验证；保留 r767 内部返回 3、工具返回 1、JUnit internal error 与所有空/残留输出。

**Architecture:** 新建独立 `data/t16-h02-replica-windows-conditional-plugin-r768/`。只对已冻结 pytest 9.1.1 源码明确导入的条件插件及其必要后继角色增加有限绑定。原 r767 runner、1154 个来源、原 103/111/1027 manifests 与旧 claim 不变。所有新增角色必须有源文件、字面模块名、实际对象身份、平台条件及 HookImpl 来源依据；不放宽全部 builtin 或未知模块。

**Known facts:** r767 已打印原 12 个 selected IDs，随后在注册模块分类门抛出 `FRAMEWORK_REGISTERED_MODULE_PLUGIN_UNADMITTED`；它没有完整收集表或成功 bindings，不能记收集完成或 case pass。源码显示 win32 的 terminal plugin 会导入 `terminalprogress`，它在 builtin plugins 中而不在 default plugins 中；实际被拒对象名和 identity 尚未记录，因此此候选还不是已证根因。

## Task 1: 有限源码前件

- 读取并引用 r767 `registered-plugin-rejection-followup.json`，核 Windows 导入路径以及 `terminalprogress.py` 是否额外注册实例、参与 collection/loop hooks。
- 普通 ModuleType 仅允许 literal `_pytest.terminalprogress` 对应已加载对象和已 pin 来源，且原 win32 分支前件成立。
- 若确需实例/HookImpl 后继，固定源码声明类和方法，保原 immutable code qualname、精确 C descriptor、有限槽快照与 flags 门。未知对象仍拒。
- 不注册原 audit 为 pytest plugin；目前 explicit plugins 只有 LongIds。

## Task 2: 独立候选

- Worker 仅拥有 r768 `harness-candidates/supervised-runner-collection-probe-draft01.py` 与其 before/stage notes。
- 从实际 r767 101718 bytes / `6e026ee0f6b4d4d02f99110660065ab4b2809846d36567d490027dd9e52bbbd9` 完整保 before 后复制；新 BATCH/claim 指向 r768，slot 来源仍为已过 21 pure checks 的 r767 模块，observer/proposal 仍为 r766。
- 只改已确认条件角色分类、相应来源绑定及必要固定声明。原产品/tests、九组93、原12 selectors、startup 20 / barrier 25 / stop 10 / matrix wall1680、两 self / GO / DETACHED / Job / HANDLE 顺序保持。
- Worker 不执行/import/compile 候选或产品/native，不产生 manifest/claim，不改正常库、服务、dist、ACL、Codex 设置或模型平台。各方并行工作，不回退他人变更。

## Task 3: 独审与 Root 冻结

- 独审 source delta 和原 effect segments，保 r767 原源审与失败结果，不倒改其结论。
- Root 单独准备新 freeze、actual-tool terminal 与原字节保全工具；逐 current 核旧1154/103/111/1027及相关完整 before。
- 本文不产生实际运行准入。Root 据稳定源审及保全设计另写一次明确准入；不得使用旧消耗 claim。

## Task 4: 实际执行与下一步

- 只执行一次改动后的新 collect-only candidate；没有变更的 r767 red 不重跑，不扩预算，不新增 clock/duration/frame/args/locals profiling（r730 已消耗）。
- 层次分开：真实工具返回、runner/child/interpreter/pytest返回，12 actual collection、JUnit case0、observer genuine partial/status、原 writers terminal。
- 非零完整保残件；成功仍是 collection integration，原已确认目录 case pass 仍为3，原全93仍待独立运行。
- 收集成功后再决定完整矩阵的诊断运行；`matrix-static-flow.json` 的 namespace/guard/事件匹配重复与未使用 fixture 是后继候选，不能用来解释旧超时，也不能跨 gate 缓存可变状态。
- H01 原30秒、H02 source/target/coordinator/restore/consumer、H03–H06/writers、分享/P06/T15/生产云原未完继续保留；goal 保持 active。
