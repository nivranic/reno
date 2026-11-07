# H01 登记来源资源接管与关闭实施方案

> 继续使用已安装的 executing-plans 工作流逐项实施。用户已授权推进既有完整目标；本方案不要求重复确认普通开发，也不授权复用耗尽 claim、扩大原期限或提交 Git。

**Goal:** 为登记流程实际创建的额外资源记录原生关闭事实，并在同一活 EX、原 operation 和 deadline 内形成可一次消费的来源完成前件。

**Architecture:** 在现有 owned EX 路径增加私有可选 journal。每次原生取得资源之前预留槽位，在合法返回后立即接管；HANDLE 与 LocalFree allocation 分别记录实际关闭返回。将 namespace I/O 的所有临时 owner 与现有 physical directory/node 关闭回执合并，来源完成 carrier 与最外 EX 关闭回执分开。

**Tech Stack:** 现有 Python、contextvars、Windows ctypes、ExitStack、pytest 普通报告，无新依赖。

## 已确认前提与方案选择

Task3 当前结果见主方案 §17.306 和 `<PROJECT_ROOT>/data/t16-h01-owned-ex-group-fixed-qa-r869/ACTUAL-DELIVERY-01.json`：115 pass、2 failure、1 平台 skip、5 未执行。两个失败在测试 spy 二次注入，test07 窄修仍须实际运行。本方案可以先准备独立候选；应用与运行须使用后继目录和新准入，不改运行中的冻结输入。

采用实际调用 journal。给 `retired` 布尔增加 DTO 会丢失 native 返回表示；退休后重新打开或探测资源会引入资源复用和新的 I/O；这两种方式均不足以证明本次关闭。可选 journal 保留旧默认路径，并覆盖成功、失败进入、body/control、cleanup 和 deadline 失败。

`<PROJECT_ROOT>/reno/personal_authority_namespace3_io.py` 的 `_Owner` 只有 `_registered` 一处直接构造。七个生产取得点为 token、system SID、SID text、private descriptor、security descriptor、parent 和 relative owner。目录验证递归 reopen，权限核验反复创建 token/SID/descriptor，因此总数依实际原路径变化。不得只登记五原件、六前缀文件或 publisher 返回的 `PublishedFile`。

`GetCurrentProcess` pseudo HANDLE、SID/dacl interior aliases 不拥有独立释放义务。CloseHandle 的合法成功为 exact signed32 int 非零；LocalFree 的合法成功为 NULL。现有 `_native_close`/`_native_free` 将成功折叠为 Python None，`_Owner.retired=True` 又早于关闭调用，两者均不能单独升级为 CLOSED。

## Task 1：固定类型的原生 owner journal

**文件：**修改 `<PROJECT_ROOT>/reno/personal_authority_namespace3_io.py`；新建 `<PROJECT_ROOT>/reno/personal_authority_enrollment_custody.py`；测试 `<PROJECT_ROOT>/tests/test_personal_authority_enrollment_custody.py`。先在新 data 候选目录保留完整 before/candidate/inverse，正式应用由 Root 在源码复核后进行。

1. 定义私有 exact journal/slot 类型和 frozen 缓存回执。绑定实际 group、operation triple、parent/effective token 和作用域身份；不接 caller DTO、closer/provider 或数值 HANDLE/FD。原始资源数值不出现在公开回执或错误中。
2. 增加固定 HANDLE/LOCALALLOC 义务类型。每次原 API 前预留槽，取得失败或返回不明记 UNKNOWN；确认合法返回后立即接管，先于 clock、getter、cast 或其他分配。特别检查 SID text 的 `ctypes.cast` 发生在旧 `_registered` try 之外的窗口。
3. 两个固定 native closer 内记录原返回：attempt 在调用前设定；合法实际返回才为 CLOSED；零、非法表示、抛错和 opaque transfer 为 UNKNOWN，保原 first，数值资源不重试。
4. `_registered` 构造失败和 postclock 失败仍独立关闭已确认资源。注册/缓存分配自身失败不得吞掉 first 或把 UNKNOWN 填绿。伪 HANDLE、interior pointer 不新增关闭。
5. getter 只读自身缓存，不能访问 clock、provider、native API、资源属性或 retired 后的数值身份。

Task1 只证明 namespace 原生 owner 的有限事实；不得声明全部 enrollment extras 已闭或 source_closed。

## Task 2：同一实际调用的可选接线

**文件：**修改 `<PROJECT_ROOT>/reno/personal_authority_enrollment.py`、`<PROJECT_ROOT>/reno/personal_authority_enrollment_io.py` 和必要的私有 custody 模块。

1. 在 `_owned_with_actual_ex` 创建实际 group 后、进入 `_entry` 前安装 journal。嵌套或 foreign group/operation/token 必须拒绝；旧 locked/默认非可选调用保持原接口行为。
2. `_OwnedScope` 传播并核同 journal，不重复登记同一实际 owner。journal 覆盖 creator、creator-live reader、creator-close 后 reader、descriptor，以及 verify/security 的全部临时 owner。
3. `_perform` 的 ExitStack 正常顺序保留：末读回 controls 逆序、publish 独立 controls 逆序、五原件逆序、root、parent、node/physical roots 逆序。发生 first 仍继续独立关闭。
4. 安装与 context reset 分别记录；不把 reset 返回或普通 context exit 当 native closure。journal 在实际源栈退出、末 deadline 门后封存，失败不补造缺失槽。

## Task 3：physical/node 与来源完成聚合

**文件：**custody、enrollment；必要修改 `<PROJECT_ROOT>/reno/personal_authority_namespace3_deadline.py` 的 `_physical` 可选接线；相关原关闭回执模块仅在明确缺口下窄改。

1. 登记 `_physical` 四个 safe directory 与 node 的实际 owner、同一 guard/token，使用已有原生关闭账本，不能以 locked/retired/普通 exit 代替。
2. 在 `_perform` 源 ExitStack 完整退出且 `_entry` 验证 `_result` 后聚合。此时 EX 仍活；要求每个实际取得 obligation 已登记、类型及身份核同、native terminal CLOSED、first/UNKNOWN/缺项不存在。
3. 新 registered carrier 明确 `outer_ex_borrowed=True`、`owned_outer_ex_closed=False`、无写权。once consumer 在 getter/验证前消费 attempt，foreign/equal-value token、替换缓存或重复调用均拒绝。
4. Task3 原 `ExclusiveGroupCloseReceiptV1.source_closed=False/write_authority=False` 保持。将来的 SQL continuation 使用活 EX 下的新 carrier；最外 EX 退出后的 receipt 不能作 SQL 活许可。

## Task 4：有限验证和实际交付

1. 固定新 selectors：真实 Windows 临时 owner 全部正常关闭；HANDLE 与 LocalFree 的零/非法成功表示；取得前/合法返回后/构造失败；postclock；body/control/MemoryError 与 cleanup first；重复 close/consume；递归 reopen/security 临时资源；未知/foreign/缺项；快照无动态访问。
2. 相关旧 namespace/enrollment tests 保留，分别说明新的 opt-in 用例与默认路径回归。正常 DB、现有权限、服务和 dist 不作为隔离 fixture。
3. 两有限源码复核后，Root 在新目录准备完整 local import inputs、same-buffer AST/compile、freeze、admission 和一次 claim。PowerShell7/no profile；解释器 `.venv/Scripts/python.exe -I -B`。只调用已准入的新 supervisor，失败先保全，禁止未改红重跑。
4. 保持 product30、parent360、matrix1680、native20/25/10；r730 profiling/trace/frames/locals/body-marker 授权耗尽。只使用普通 pytest phase/JUnit 和明确 consumer-local fault seam。
5. 保留实际 Tool/child/pytest 返回、具名终态、skip 原因、未执行身份、完整 selected 原件/副本和两独立结果复核。没有观察到的终态不能按声明数量填绿。

## 依赖、回滚和后续

只有 test07 实际完成 Task3 原失败验证，且本方案相应源码/结果复核关闭后，才接 joint authority SQL/journal-CAS。后续仍需 versioned SQL successor、原 sealed intent、fresh committed re-observation、Store 原件/no-replace reservation、live witness 和 H05/H03/H04；本方案的 carrier 本身不授写。

回滚只针对 owned delta：确认当前完整字节仍等于候选后恢复已保存 before，不覆盖其他 dirty 改动。保留原失败、耗尽 claim 与既往证据；不 commit/push/stash/reset/clean。

P06 用户没有历史材料，七身份 0/7，不再追问。本方案是待实施设计，不代表 native runtime、完整 source_closed、生产写权、云/设备验收或 full goal 已完成。
