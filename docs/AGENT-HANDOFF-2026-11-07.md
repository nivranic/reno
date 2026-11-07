# reno 阶段交接：2026-11-07

记录时间：2026-11-07 17:34（UTC+08:00）。本轮按用户要求结束当前读取环节，完成截至目前的源码收尾与首次推送，然后更新此交接。**完整产品目标没有完成，剩余工作包含开发，不能表述为只剩验收。**

## 1. 接手入口与交付状态

| 入口 | 用途 |
| --- | --- |
| [产品演进方案](plans/2026-09-30-product-evolution.md) | 原产品目标、阶段方案和历史证据；正文很长，按标题或关键字定向阅读 |
| [同步方案](sync-plan.md)／[部署说明](deploy.md) | cloud/home、协议、恢复、资源治理与发布要求 |
| [本次静态快照](engineering-snapshots/2026-11-07/README.md) | 最新 r1528 源码和 r1523/r1525/r1526/r1527 直接合同 |
| [收尾验证记录](closeout/2026-11-07-validation.json) | 本轮实际验证、独立复核及未验证边界 |
| [条件调用边补记](engineering-snapshots/2026-11-07/CALLOUT-MAP-NOTES.md) | code 读点、GC 调度、binder/evaluator 与 watcher 的静态事实 |
| [个人设施状态接口](personal-authority-status.md) | 只读文件观察 DTO 与活写权之间的区别 |

真实产品 Git 仓库为 `reno`，分支 `main`，远端 `git@github.com:nivranic/reno.git`。General 是独立的本机工作目录，本身不是 Git 仓库。

本轮首次交付已于 17:34 前完成 push，并由 `ls-remote` 独立确认：

- `44a5cd3`：原有、有实际功能的部署／cloud-role 提交。仅将其未推送版本中的一处本机目录替换成占位符，其余功能保留。
- `af7f05fd877b3ba63a3f4ab47d48316f929aff6c`：个人装修、同步治理等累计阶段源码、测试、方案及最新静态候选。此时本地／跟踪引用／实际远端 SHA 相等，ahead/behind 为 0/0，工作区干净。

本文件、稳定入口及 README/AGENTS 指引随后单独提交并补推。最终交接提交以 `origin/main` 和实际远端查询为准；不要把上面的源码提交误当成必然最新 HEAD。未创建分支、worktree 或新代理。

首版交接已作为 `2d4b15c` 推送。后续只读核查发现该版本工作流在创建任务前被 GitHub 拒绝，CI 配置修正及证据见本文末尾第 10 节；不要把首次 push 成功解释成 CI 通过。

## 2. 产品目标与已有工程内容

原始目标是从装修短视频知识库发展为个人装修决策与现场核对工具。用户所在地为浙江绍兴，工序、防水品牌及型号尚未确定；不要自行补成已确定条件。用户已授权此前真实库维护，但授权不代表每个新原生诊断或安装步骤都可以重放。

本次提交保存的累计工程范围包括：

- 个人项目条件、决策卡、来源快照、人工资料与附件、现场检查／整改／复检／作废、草稿和回执处理；个人采纳与全库知识复核分开。
- cloud/home 采集与知识同步、schema 迁移、知识归档／受控恢复、复核对账、来源判定依据和显示限制。
- 生命周期设施、共享成员及引用根、帧缓存与历史资源回收、永久停发、本地引用退休、mapping 代次和公开预览／CAS／原 UUID 续跑入口。
- 预算／等待控制，以及个人镜像、保存、来源、权属和恢复方向的模块、合同及测试。

以上是**源码交付范围**，各功能的实际完成程度仍应按对应方案和具名证据核对。不要把模块存在、测试替身通过或本轮 Git 推送解释成完整生产功能可用。新 clone 不含用户现有配置、数据库、附件、本机运行证据或目标原生构建环境。

## 3. 最新原生源码基线：r1528

源码入口：`engineering-snapshots/2026-11-07/reno-makegen-explicit-code-source-r1528/working-01`。这是 General 中同名目录的**原字节副本**，不在产品模块路径上，尚未安装。

| 身份 | 结果 |
| --- | --- |
| 完整 working 源集 | 23 源 / 2,136,879 B |
| r1528 目录 | 精确 63 文件；FINAL 记录其中 62 项固定身份 |
| FINAL | 13,874 B；SHA-256 `d532e204503daa6f71ab2de383f63530607345f2f5ff51379ac4995f3b263174` |
| 修改后的 `runtime/Objects/genobject.c.txt` | 78,846 B；SHA-256 `03c6f81f803c0abbc1e8dc1aa225c1b453d710b076822638771d08c8e9b9e1cc` |
| 本次快照复制 | 5 组、90 文件、4,571,959 B，全部匹配原件与 MANIFEST |

r1528 只把 private static `make_gen` 的 code 输入改成显式参数。generator、async-generator、coroutine 三个原 caller 仍各自在调用点传入当前 `func_code`。没有选择 earlier held frame code，没有新增 code guard/ref，父 22 源、selector block 和 r1507 caller 未改变。

已完成设计／源码／工具／实际／文档双审，51 项实际输出匹配独立预测、四组编辑可逆恢复原参考，文字生成只执行一次。以上只证明静态候选和文字证据的一致性。没有 C 解析、预处理、编译、链接、候选导入或运行证明，没有 ABI、COMPAT、物理预算或完整安全闭包结论。

必须保留这些状态：

```json
{
  "source_only_closed_fields": [],
  "candidate_executed": false,
  "complete_child_implemented": false,
  "complete_exec_dispatch_implemented": false,
  "full_goal_complete": false
}
```

不要从早期 r1454 或其他旧目录重新开始；其有用改动已由最新累计源集承接。历史静态材料不是运行通过证据。

## 4. 当前官方读取环节已结束

r1523 的单次许可仅覆盖 CPython v3.12.10 官方 `Objects/genobject.c` 与 `Include/internal/pycore_genobject.h`。本次实际用 2/8 次阅读服务调用，无服务错误、无应用重试，剩余 6 次未使用；**读取已经结束，不再继续使用剩余额度。**

三份实际材料为 r1523 下 `OFFICIAL-READ-CALLS-01.json`、`OFFICIAL-READ-NOTES-01.md`、`OFFICIAL-READ-RESULT-01.json`。只保存摘记与记账，原文服务副本保存 0 B。四文件独立一致性复核通过，回执 `e0a72d`。服务表示不证明原 HTTP 字节、上游 commit 或目标编译输入身份，也没有原 HTTP 的字节、超时和重定向保证。

header 的服务表示不含 `_Py_MakeCoro` 声明和完整 generator 对象布局，不能据此认定 ABI 已闭。genobject 的本地参考实现已经可读，后继不能再把“实现缺失、等待网络”当成无法继续静态设计的理由。

**没有 r1529 ADR、没有新 V3 决策、没有 V3 新源码。** 关于 frame mode、新字段或统一 code 快照的讨论没有形成选定方案。

## 5. 后继优先工作

先核对 Git 和本交接对应的完整静态包，再按以下依赖推进；本文件不另授产品运行或诊断准入。

1. 读 r1528 `NEXT.md`，再读 r1523 `DRAFT.md`、r1527 `IMPLEMENTATION-NOTES.md`、r1526 的 `CONSUMPTION-CONTRACT.md`／`ENTRY-PRECONDITIONS.md`，以及 r1525 `DRAFT.md`。整理完整 V3 设计，保留尚未确定的选择，不把草稿写成最终方案。
2. 具体化同一实际 packet 的 source/MOVE、callee check 根、标准 PyFunction owned 路线、generic callable、frame reservation/Initialize/binder、eval/hook、generator 交接、result check 和每个失败 close。先解决各站点实际 code 的关联与合法变化，不能以对象存活替代 code 稳定，也不能添加普通域 code-equality 拒绝。
3. 将 r1526 primitive 的正常入口、规定输入消费和失败 pending exception 顺序接到真实 consumer。不得预先统一 settle、把 NULL 当成未消费，或提前把输入转交 private first。
4. 再接 r1525 完整 None/exec 效果序列和 target/seed/helper/exec/audit、普通动态变化 selector、正常退休／`restore_returned`。原 getter、两次 name、F3 及 V2 阶段仍须在原实际站点消费，不重呼或伪造历史值。
5. 完整设计通过主核和独立复核后，才进入下一源码批；后续构建／运行也必须有对应前提、范围和可审结果。不要重跑已经封存的绿色文字工具。

几个不能误用的源码事实：

- GC 分配达到条件时先 `_Py_ScheduleGC`，只置调度位；之后实际到达 poll 才可能运行 GC callbacks。不能说 `PyObject_GC_NewVar` 或 binder 分配会同步执行 GC callback；已读 RETURN_GENERATOR handler 没有 pending poll。
- binder 的慢比较、hash、kwdefaults lookup、格式化和实际 LAST DECREF 是条件调用边。frame 扩容最终 VirtualAlloc 实现尚未完整读，不能泛称全部 allocator 无回调。
- `_PyEval_EvalFrame` 在 hook 非 NULL 时调用实际 hook，但没有实际安装／触发的运行见证。
- function watcher 可 inspect、不得修改本次 function，且须保留进入时 pending exception；SetClosure 文字不能证明任意 tuple 长度形状合法。

## 6. 全产品仍开放的范围

下列范围含未完成设计或实现，不能合并成一个“待验收”事项：

| 范围 | 仍需完成 |
| --- | --- |
| H01 原生调用与活权 | 完整 source/caller/state/lifetime、B001/B002、V3 child 和完整 exec dispatch；布局／链接／构建／运行／COMPAT／全部物理成本 |
| H02 保存／恢复 | 完整持久包、复制、续作、核验、恢复及实际入口和资源图；已保存 candidate 或 closed receipt 不自动授活写权 |
| H03 / H05 | 实际 owner／producer、pending、原 UUID 恢复、最终捕获；全 mutation/upload/send 的暂停排空和失败清理 |
| H04 / H06 与分享 | 完整下游及 API/CLI/UI；永久分享与撤销；只读目录 ready 不等于 write/handoff authority |
| P06 | 预算相关剩余工程与历史迁移证据；七项历史源身份仍缺，用户已明确没有备份，不再索要 |
| 条件 T15 / 生产 | 条件任务、生产云部署及真实平台／模型／网络链，分别建立证据 |

早期 F001 的 20.016 秒超时仍没有阶段定位，不得因后继静态工作宣称修复。PUBLICATION_FAILED、State 及 Windows 原生句柄问题也不能用当前文字候选代替真实故障结论；保留原失败回执、未跑项和未知原因。

## 7. 既定选择与执行边界

- 原生路线保持同一活跃 native C caller → 实际 Python/C child → 同一活跃 C 续体，保留已选 F4 Python frame 删除；不恢复 F4/carrier 路线，不强制原 evaluator inline，不用跨 C 返回 carrier。
- A 已由用户选择：保留原审计接口，保证项目内部资源管理；外部直接操作暴露的 HANDLE/fd 不在保证内。内部 allocator、callback、清理仍在保证范围，不能由 A 豁免。
- S1 不扩大普通 class/instance/callback/alias 的合法域。普通动态变化不能简单统一拒绝。
- 旧 r920/r964 单次诊断已用完；r967／初始化重入待原问题，不执行、不代选、不重问。旧 profiling、frames/locals、异常链诊断及原 runner 次数／并发／期限不因新版本、Git 提交或新会话而刷新。原 30-call／2-child 运行界限及既定硬截止已经过去，不据此另开运行。
- 继续遵守原 30/360/1680、native 20–25–10 等对应阶段限制；不能把它们拼成新的授权。新的具体执行前提缺失时先完成可读、可审的静态部分。
- 本轮没有数据库维护、服务重启、dist 覆盖、权限/ACL 修改、原生诊断或候选安装。过去的真实库维护授权不能证明本轮做过这些事。
- 不新建分支／worktree。默认不新增代理；必要的独立复核优先复用已授权的原只读代理，不把历史某次窄复核许可无限扩大。
- Windows 使用 PowerShell 7，显式工作目录。保留无关 dirty，不自动 stash/reset/clean，不改系统设置。仅更新本轮授权的交付文件，不更新个人 memory。

## 8. 本轮实际验证与局限

| 检查 | 实际结果 |
| --- | --- |
| 前端 `npm run typecheck` | exit0 |
| 前端 `npm test -- --maxWorkers=1` | 41 文件、314 测试通过，183.26 秒；终态 `88f6ed` |
| 前端隔离构建 | exit0，输出到独立收尾目录；正常服务 dist 未覆盖 |
| 最终源码暂存区核对 | 842 Python AST、79 JSON 可解析；路径／私钥／常见令牌规则扫描 0 命中；90 项快照身份匹配；`diff --cached --check` exit0；回执 `eebffe` |
| 独立复制包核验 | 90/90 匹配原件与 MANIFEST，5 组 / 4,571,959 B；回执 `15e02c` 等 |
| 独立补充文档与三份便携测试清理 | 0 阻断；回执 `7fe814`、`31f231`、`7383f1` |
| 最终交接只读复核 | 0 阻断；回执 `a56b45`、`531975`、`bed6f5`；未重查远端或执行产品 |

三份测试清理只涉及虚构路径、Windows Bash 硬编码移除与通过 PATH 查找 pwsh。19 份 Python 文件仅去除尾随空白／末尾多余空行，AST 核同；原字节在本机保留。它们没有新增后端运行证明。封存快照保留原始字节和空白，不套用产品源码的格式清理。

未重跑后端全量 pytest、E2E、真实云／模型／数据库链，未 C 解析／编译／运行，没有完整产品或生产验收。前端测试、AST、JSON 和身份核验不能互相替代这些证据。扫描只覆盖已说明的规则，不能称完整安全审计。

## 9. 本机材料、隐私与恢复

- 真实 `data/`、`media/`、私密配置、数据库、附件、原始日志和全部 General 历史目录不发布。原文件保留，不强制加入 Git。
- 9 份既有遥测文件取消跟踪，本地文件保留；新遥测由 `.gitignore` 排除。已有远端历史没有改写，本次不能宣称清除了历史内容。
- 12 份依赖本机路径和旧 `data/` 身份的一次性 checklist 运维脚本保留本地并精确忽略。不能在 clone 中假定这些脚本或它们的旧运行材料存在；也不能重放它们。
- 本机完整原件、收尾备份和原始验证输出保存在 `<GENERAL_WORKSPACE>/closeout-2026-11-07/`，不随 Git 交付。该目录只是本轮新建的私有收尾记录，不是产品数据备份，也不覆盖旧审查材料。
- 便携快照的 JSON 仍可能引用外部父材料／历史回执；本次只归档明确列出的五组。缺失依赖必须如实记录，不能由摘要或散列值重建“原始证据”。
- 原封存文件保持历史表述；不要为更新日期或宣称完成而改写原 FINAL／调查记录。新增进展使用新文件、明确来源和验证边界。

若需要撤销后继改动，只撤销本方明确修改的源码或交付文件，保留用户数据和旧失败证据。`web_ui` 切 classic 不是数据回滚，知识快照也不是包含个人数据与附件的整库恢复。任何真实恢复或部署按既有运行手册与当前授权另行执行。

## 10. 推送后 CI 配置核查

交接提交 `2d4b15c` 对应 [Actions 运行 42360558764](https://github.com/nivranic/reno/actions/runs/42360558764) 在校验工作流时失败，job 数为 0。原工作流四个任务的任务级 `env` 使用了不受支持的 `runner.temp`；这不是测试执行结果。

修正将 TMPDIR／RENO_E2E_MANIFEST 放到四个任务的首个 runner shell 步骤，通过实际 RUNNER_TEMP 写入 GITHUB_ENV，其余测试步骤和路径保持。完整 YAML 对象保持性核对、四条 Bash 命令的隔离执行及带空格路径检查通过。没有触及产品模块、原生候选或正常库／服务。

详细原因、官方文档、原始失败与验证范围见 [CI 收尾记录](closeout/2026-11-07-ci-env-followup.md)。新提交的 GitHub 工作流状态需按关联运行核实；本交接不预先宣称 CI、后端或 E2E 通过。

**已取得后继实际终态。** 修正源码 `4e2a96e` 的 [运行 42361078787](https://github.com/nivranic/reno/actions/runs/42361078787) 已完成，工作流被接收并实际启动七项任务：frontend、reconciliation、derivation 成功；checkpoint-runtime 的 pip 安装、backend 的 pytest、sync-chain 与 sync-recovery 的场景步骤失败。checkpoint 测试没有执行。完整记录见 [远端 CI 结果](closeout/2026-11-07-ci-remote-result.json)。

四项失败的具体原因尚未取得：公开日志 API 为 403，任务页面要求 GitHub 登录，有限注记只给退出码。先获得同一失败任务的授权日志再改代码，不猜测 numpy 版本、collection 原因、native 机制或恢复行为。上述失败和原工作流错误不能合并为同一个根因。

最终结果补记仅改文档并标记 `[skip ci]`，不会为相同源码再次触发七项任务。最新文档 HEAD 不具有新的 CI 通过结论，完整目标仍开放。

## 11. Python 3.11 依赖声明冲突

后继只读核对从官方 PyPI 元数据确认：无条件固定的 NumPy 2.5.3 要求 Python >=3.12，与项目 >=3.11 的声明及 checkpoint-runtime 的 Python 3.11 不兼容。已按 Python 版本使用环境标记：3.11 选择 NumPy 2.4.6，3.12 及以上选择 2.5.3；其他依赖和最低支持版本保持。

六个 Python 版本的环境标记选择及发行版 Requires-Python 核对通过，未安装或运行 NumPy。此证据支持修正依赖声明，旧 pip 日志全文和另外三个失败的具体原因仍未取得。详细事实、来源和验证界限见 [依赖修正记录](closeout/2026-11-07-python311-numpy.md)，实际结果须核对后继 fix(deps) 提交的关联 CI；不将元数据核对称作运行通过。

**已取得修正后的有限实际结果。** 源码 `bbc5c6b` 的 [运行 42362145344](https://github.com/nivranic/reno/actions/runs/42362145344) 七项均终态，3 项成功／4 项失败。checkpoint 已从旧 pip 安装失败推进到第 5 步实际测试失败，当前退出码 1；默认步骤成功条件支持此前 pip 安装已完成的判断，但不证明具体测试通过。其余三个失败仍是 backend、sync-chain、sync-recovery。详见 [后继结果](closeout/2026-11-07-python311-ci-result.json)。

公开 API 后继触发限流；公开页面和现有浏览器仍要求 GitHub 登录查看详细日志。当前没有可用的已登录日志通道，不读取用户密码、Cookie、token 或私密配置，不根据有限退出码继续猜测修补。源码与原生候选的其余未完成范围保持，后继须先取得授权日志或明确新的工作方向。
