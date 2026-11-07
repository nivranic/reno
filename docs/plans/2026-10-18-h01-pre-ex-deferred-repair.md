# Task9 原延迟RP1闭包补齐与定向复检 Implementation Plan

> For Codex: 沿用executing-plans与同一只读复审代理，不新增代理，不重复申请普通开发权限。只复检r1058的三个失败ID，旧r1056的12项不重跑。

**Goal:** 补齐原current_guard调用真实延迟加载的RP1源码闭包，再验证三个原严格准备链case的完整结果。

**Architecture:** r1055的37份候选保持不变；基于r1056原114文件code bundle，按独立静态调查的确切c973来源增加完整依赖和资源，在r1059生成新的只读完整代码副本。每项保原checks02的全部业务调用和身份断言，只更新代码/manifest位置及准备、准入版本。

**Tech Stack:** Python原包/原import、stdlib AST/bytes/hash/subprocess、原ContextVar/Token与RP1 decorator/gate/bootstrap。

## 原红事实

r1058唯一实际批0pass/3fail/缺0、7.828秒，3exit1/wait、无timeout/kill。三个case均在末次lifecycle.current_guard()中进入原decorator→gate，deferred import `reno.personal_authority_replica_operation` 因114文件副本缺该模块而抛ModuleNotFoundError。此前构造/scope/history/reset断言到达不改变整case失败结论。原脚本/Tool/CASE/stdout/stderr/输入及完整来源保留。

这是本批源码组合的缺项，尚未验证RP1模块加载后是否仍有其他真实组合问题。不能删除该调用、改为读原始ContextVar、加入假模块、改模块名或重置原状态来宣称通过。

## Task 1：有限延迟闭包调查与复核

同一代理在r1058写独立实际记录复审和INDEPENDENT-DEFERRED-INVESTIGATION-01.json。以原RP1模块为根，解析顶层导入、顶层本地bootstrap调用及触发的原固定导入；与既有114文件覆盖集合对照。每个补充源保c973当前路径/完整bytes/SHA，候选37覆盖始终优先，不使用正式树fallback。额外deferred路径分列，不宣称所有未来动态catalog完成。

核import及无owner的current_guard gate的副作用，正常DLL/扩展/第三方资源读如实记录；若出现会执行资源业务API/EX/SQL/网络/设施setup的必要路径，先收窄方案，不在现有QA边界下直接运行。

## Task 2：新完整bundle与脚本

Create `data/t16-h01-pre-ex-deferred-repair-r1059/`。对调查列明的完整来源逐份核当前pin，再与既有114源文件合成为新source-full-01。保原candidate37、旧三red结果、原checks02全文及精确脚本差异。

仅允许脚本的code/bundle位置、当前版本文件名及输出目录变化；原业务动作、错误处理和所有断言均保持。核原检查主体AST，不能把原真实错误变为成功。新catalog仍X001/X002/X003，全部来自r1058失败ID；每项使用新state/config/data/temp，与代码互不包含。

新source bundle、脚本和实际准备都继续Root+同一独立复核。prepare先核报告所审版本与每份来源pin，再封输入；新单次claim唯一执行，不复用r1058 claim。保存完整Tool/CASE/stdout/stderr/wait，任一新失败继续保留，仅针对失败ID修订复检。

## Task 3：交付

双实际记录复审后，按实际结果更新主方案、AGENTS及最新入口。若三个新结果全通过，只能称两批6次尝试、3唯一ID最新通过，不能把旧红改为绿或称单批6通过。旧r1056的12通过单列。

候选不安装，canonical/c973本段apply0、历史11、产品pass0，完整Goal active未完成。marker/完整费用资源债务/guard consumer/两阶段terminal/容量-durable恢复等保持待办。

原case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/并发1/最多2pytest children、最小环境及-I -B -X utf8保持。无profiling/frames/locals/停点/新异常链诊断，不重放r920/r964，不执行或重问r967；P06历史0/7。正常库、服务/dist、ACL/settings、真实平台和Git保持。回滚为不安装候选和不运行新claim。
