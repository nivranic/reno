# Profile 构造记录独立持有实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 保留原 StorageProfile 布局、四字段字典和序列化分派，以显式构造 owner 持有实际规范化记录，并把真实 owner 接到后续 root 来源链，关闭 r1009 K001/K002。

**Architecture:** 在 r1008 source08 十文件组合上，恢复 StorageProfile 的原无 slots/state/reduce 方法布局。新增内部 owner，调用前由原固定工厂登记并持有输入、partial self、journal、实际 constructor 普通返回及失败；临时 ContextVar 只在固定 type-call 区间选择一次原 post_init。全局仅弱 owner/journal/reader 索引，profile 不持 owner，不从旧对象或副本补签历史。普通构造在没有匹配捕获时执行原 post_init 体。

**Tech Stack:** Python 3.12.10、现有 frozen dataclass、原 C 字典/异常 descriptor、ContextVar、weakref/GC、source-selected 模型。正式/c973 不安装半组合。

---

## 1. 事实与完整范围

r1009 的 source08 只对 E008/C014 两项新批通过，不是同版61项绿；原 source04 的59pass/2fail及所有旧红/耗尽claims保持。当前候选在 profile 上新增两个实际 data slots 和 state 方法。后置 cooperative Mixin 到 object 默认 state 时，原 copyreg 遍历所有 MRO 的 slots；元类拒绝或无效写缓存仍返回计算出的 names。原 hook 在返回前就可能看到 dict→tuple 的变化，外层过滤不能撤回已发生的分支/异常/副作用。移动拒绝条件不能独自解决持有结构问题。

正式 `storage_lifecycle._profile`、`storage_members`、`storage_admin` 仍用 isinstance。`tests/test_sync_source_opportunity17.py` 明确要求 copy(live17.profile) 得到不同实例、同 identity 且 consume 成功。限定正式目录调查未命中 pickle/state/reduce 消费者，不等于可以破坏原 dataclass 的常规 state/copy 协议。兼容目标保持，不以删除用例或拒绝所有子类来收缩。

本段必须同时完成：原布局/状态协议保持；真实四字段 producer 和实际固定 constructor 返回记录；实例外但非全局强根的完整原输入/异常持有；copy/旧对象 unknown 且原功能可用；显式 owner 的 root 交接设计和接线前件。恢复旧布局不是把来源要求删除，不能用重新构造的同值 profile 冒充历史身份。

不保留 slot 隐藏/临时摘锚/强制写元类缓存的权宜方案，不 patch 全局 object/copyreg，不用 frames/locals、profiling、计时诊断或 GC 引用扫描。新设计不以任意私有布局反射的完全透明性作为额外保证；公开四字段、原方法分派、计划内显式 state/copy/pickle/继承场景必须用真实对照证明。

## 2. 对象与真实生命周期

### 2.1 原 StorageProfile

候选恢复到 r1006 原类的字段、decorator、slots 缺失状态、state/reduce 方法缺失状态、roots/identity 原 AST。唯一类体改动在 post_init：先用固定私有入口尝试取得当前精确捕获 journal；None 分支逐字保原 for/赋值体并 return，有 journal 分支执行已保原 lookup 顺序的观察体。不得给 profile/子类设置 `__slotnames__`、新增 slot、dict 隐藏键、state/reduce descriptor 或缓存修复。

普通构造、普通子类、手动 post_init、copy/deepcopy/pickle/replace 不因没有 owner 而拒绝。普通构造没有来源记录；replace 自己的普通构造也不自动补成受认证来源。后续需要真实来源时，必须在其实际新构造入口使用固定捕获工厂，不能把未观察的旧构造称为有记录。

### 2.2 新 owner

内部固定 owner 类型可用私有 slots 持 journal/独立 raw/锚；这些不是 StorageProfile 或已有公开类型的新增字段。调用者先取得 owner，再调用一次固定 construct(owner, database, media, reports, checkpoints)，使失败时调用者也已持有实际 attempt。owner 不接收 callee、profile class、expected 值、任意 callback 或补签 profile 参数。

固定私有接口命名：`_profile_origin_create_owner_original()` 返回新 owner；`_profile_origin_construct_original(owner, database, media, reports, checkpoints)` 执行一次原 class type-call，成功返回其实际 profile；`_profile_origin_owner_facts_original(owner)` 只读观察，不重新规范化或建立记录。完整实现只从原 `_bind` 捕获的 class 与 C 读取原语执行，不把这些选择交给参数。无 active owner 的 `_PROFILE_ORIGIN_BEGIN(self)` 返回 None；该返回不是拥有/准入回执。

construct 在任何用户 PathLike 回调之前一次 claim，保存四个原参数与实际当前 thread/task、自己的 ContextVar set token、constructor attempted/returned/result/failure、post_init partial-self 的尝试与实际绑定。固定 class 由原 _bind 的闭包捕获，不由调用者提供。先记 constructor 原普通返回，再做后验；内部 body complete 不等于外层返回，return/effect 不合并。

post_init begin 只有当前固定构造区间、exact 原类及未绑定的原 capture 才能绑定第一次 partial self。精确 class/type-call/init/new 等来源门需要在本段目录中列明，完整动态 stdlib/C 认证仍为 False，不能借一个类身份推出全闭包。固定工厂要保实际类调用的完整返回/异常，不以手造 profile fixture 代替。

首个 partial self 前的固定门必须明确核：原 class 与 exact `(StorageProfile, object)` MRO、元类原 `type.__call__`、解析到的原 `object.__new__`/`object.__getattribute__`、原 generated `__init__` 的 function/code/globals/defaults/kwdefaults/closure（尤其原 `__dataclass_builtins_object__` cell 为 object）、四字段无新增/替换 data descriptor、原 post_init function/code 及其原 begin 入口。ContextVar set 后到首个 post_init 绑定之间不得新增用户可选调用；thread/task 采样在 set 前完成，begin 的当前 thread/task 验证只能走固定已核原语。未知 class/init 分派只使新捕获入口拒绝，不改变普通构造。并发任意修改globals的完整认证仍未证明，不能据此发布写权。

嵌套普通构造不得被父 owner 劫持；已绑定后 foreign profile 走原 legacy 体。相同 self 的 reentry 不建第二份历史，不改变原业务异常或成功；记录 incomplete/reentered，后续来源消费者拒绝这份不完整证据。第二次 construct(owner,...) 拒绝且原 attempt 不重用；失败、Context reset 后也不能重新占用。

### 2.3 清理、失败与弱索引

owner 是完整 raw 的唯一持有根：raw 可强持 profile、PathLike、Path、callee、原异常/原 traceback；它们的内部环由正常 GC 处理。全局表只保 weak owner/journal/reader，weak callback 不捕获 owner/raw/profile。

ContextVar 的值只能是弱 registration：它及其间接字段只含 weak owner/journal/reader、原始标量身份/nonce/active状态，不得强持 owner、raw、profile、异常/traceback。construct 普通局部变量显式强持 owner；post_init 在同 thread/task、active/claim、一次 partial-self 门内升级弱引用，再核实际 owner/raw 身份。finally 先将原 registration 标为 inactive，再对原 token 唯一 reset；reset 的普通返回/效果与后继错误分列，原首错优先。copy_context()/新Task留下的 registration 副本不延长 owner/raw 存活，也不能在原构造结束后成为新捕获入口。原 reset 不证明副本Context已清空，更不证明业务退休；精确Context身份仍unknown。

原 profile 不反向持 owner，因此普通 copied/unpickled profile 无 journal；即使原 profile 继续存活，owner 不应被全局索引保住。若用户自己的输入/结果主动建立反向引用，只承诺没有新全局强根，不把任意用户对象图当作一定可提前释放。

没有通过 registry/原 owner/raw 身份核对的复制 owner 为 unknown。单锚损坏、未知 index、原 reader 损坏的门需明确；不能通过重建 profile 当前值恢复历史。owner 的显式结束只表示观察 owner 状态，不能推出 source_closed、文件关闭、SQL 成功或写权。

## 3. 与后续 root 链的交接

producer 工厂返回的 profile 仍传给原接口；需要来源的 fixed route 同时持有并显式传递原 owner。新 getter 以 owner 为输入，核 actual constructor returned profile、partial self、body complete、原参数/字段实际返回、无 pending/first/reentry，再交出只读观察。不能只凭 profile 当前 dict 在全局搜索/新建 owner。

`select_paths` 的下一段必须从实际 owner 取得同一 profile/journal，继续原 database.parent、两层 roots 去重/排序、identity/roots 比较和合法 cache 变化。每个应用/CLI/config/test 的严格入口要列出创建点、owner 的长期持有者、每次调用交接和释放；不能只实现一个未被 route 使用的 helper 就称端到端接齐。

本段先完成 owner 与原四字段 producer，后继 root consumer/真实固定 route 不接齐前全部 path/physical/marker/metering/publication 旗标 False，正式/c973 apply0。原通用 copy 消费者保持同 identity 语义；来源 unknown 不得被误写为副本业务不可用。

## 4. 文件级任务与依赖

### Task 1：封存来源和设计双审

Create `data/t16-h01-profile-construction-owner-r1010/`，封存 source08 十文件/manifest、r1006 原 lifecycle、r1009 完整结果/审查/交接、正式相关构造/复制/JSON投影源码与测试、3.12.10 copy/copyreg/pathlib/ntpath/contextvars/dataclasses/weakref 及官方 typeobject.c 原文。记录完整 pairs/SHA、20保护目标10原件/10缺失。有限查询及反例只标静态，不当运行失败。Root 与同一只读代理审 capture 生命周期、嵌套/reentry、弱根和原布局兼容。

### Task 2：十文件完整候选

Modify 候选 `storage_lifecycle.py`：移除新增 profile slots/state hooks；legacy post_init 体保原；新增固定 owner creation/construct/facts 入口的原绑定。Modify 候选 `storage_profile_origin.py`：删除 profile slot/cache/state/reduce 改写，改 owner 私有持有与 ContextVar 一次选择。其余八 source08 文件完整继承。保完整 parent/inverse/diff，10 ASTcompile 和原63顶层定义/roots/identity/_path legacy AST 核对；不覆盖 source08 或旧候选。

### Task 3：完整 source-selected 检查

新 architecture 改变全部 producer/lookup/GC 前件，原61项不能直接沿用为本组合通过。新 catalogue 要显式映射哪些旧语义重新验证、哪些改为普通 legacy unknown；用新 epoch/claims，不重用旧61/2运行许可。旧结果保持原样。

至少覆盖：普通无owner构造的值/字段/回调顺序；fixed owner 四字段48原调用/两Path及setter普通返回与effect；完整 constructor returned 与 partial body 分列；四字段非法输入/原异常映射；Context reset 成功/失败原首错、重复claim、嵌套构造、同self reentry、真实线程/Task与copied Context的已知边界；在PathLike内保存copy_context/创建Task，原调用结束、丢owner而profile仍活时验证弱registration不保活raw，副本inactive不得再捕获；owner/index/reader/锚损坏；owner释放而profile仍存活、PathLike/traceback环GC；profile无新增slot/cache/state/reduce方法、vars四项、weakref/frozen/equality/hash/repr；copy/deepcopy、pickle protocol0–5、显式profile/object getstate、已有subclass slots、None/自定义/预热cache、拒写/无效写cache元类和后置Mixin内提前检查dict/tuple形状。基线与候选都执行实际原方法，不能外层过滤来伪装 hook 内部兼容。

普通copy/replace及业务身份模型要继续满足原 sync_source_opportunity17 语义。新 owner 的 actual capture 结果与 legacy profile unknown 分开断言。全部低层文件资源/EX/native/SQL可继续合成，但 type-call/Path/ContextVar/copy/pickle/GC 必须选入实际代码，不能用fixture自己制造 journal/profile 关系来证明producer。

脚本和实际准备双审，封存新 source/pins/catalogue 后才唯一执行。保 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest child，本批可按既有监督器max1。命令 `.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-profile-construction-owner-r1010/<最终封存脚本>.py`。实际脚本/ID/SHA未定前该命令只是模板。

### Task 4：实际结果、完整后继与回滚

Root 与同一代理复核 actual 原输出和完整pairs，记录失败/未启动/耗claims，追加主方案与AGENTS项目事实。K001/K002只有原协议/原 hook 内部真实对照通过才可闭，不能用源码推测或新 owner 单独模型闭合。

随后必须完成第3节的真实 route owner 持有/交接和root消费，保原111/94/r919、Store恢复/source_closed/live witness/写权、H02–H06/永久分享等完整Goal范围。完整Path/stdlib/global/C/ctypes/lock、三marker同预算/future N/容量、最终Context、新五件图/final/父退出/四恢复都仍待证。不能以本段兼容恢复代替完整原目标。

回滚为保留未安装候选和原红，不改 dirty main。正常库、服务/dist、既有ACL/settings、真实平台及Git保持。r920/r964耗、r967仍待原特定答复不执行/重问，P06历史0/7用户无材料不再问。普通开发与同一只读代理授权覆盖本设计实施，不另建worktree/提交/新代理或重复权限问题。

## 修订02

01漏掉复制Context/Task会保存ContextVar当前值的强根风险。02将registration及其间接字段限为弱引用和标量，当前构造由普通局部持owner，退出先inactive再reset；保01全文和原设计红。另具体列出首个partial-self前无用户分派的class/type-call/generated-init闭包门。尚未候选或运行，不把这些设计要求称为已实现。
