# H01 原主登记：联合 SQL 后继与恢复实施方案

> 使用已安装的 executing-plans 工作流继续现有目标。用户已授权普通开发和真实库维护；本方案不要求再次确认，也不扩大既有诊断授权或运行期限。当前 dirty main 保持，不提交、推送或重置。

**Goal:** 在来源原件已经核对、最外层独占锁仍持有时完成初始 SQL 登记，并产生能独立复核、能处理提交后中断的后继状态。

**Architecture:** 保留现有 namespace3、原始 intent、十张个人表和四项 meta 的含义。新增私有原字节 packet、独立版本的 SQL successor 和固定联合 SQL 观察；事务、文件发布和完成记录分别记录事实，再通过同一实际操作和独占锁连接。

**Tech Stack:** 现有 Python、SQLite、Windows 资源关闭账本、pytest；不新增依赖。

## 当前事实与进入条件

- 主方案为 `<PROJECT_ROOT>/docs/plans/2026-09-30-product-evolution.md`。本文件细化其中已有 SQL/journal-CAS 工作，不替代完整目标。
- r877 的五个产品文件和一份测试已在 r878 应用，源码与测试复核通过。
- r878 实际首批在原 360 秒期限超时，后续 16 批没有启动；完整矩阵未通过。正式结果以 `<PROJECT_ROOT>/data/t16-h01-enrollment-physical-node-qa-r878/` 的实际报告为准。**当前可继续静态设计和独立候选准备，不能把本文件当作运行准入或写权。**
- P06 用户没有历史材料，七项身份仍缺失；不再追问，也不以当前源码替代历史版本。
- product30、parent360、matrix1680、native20/25/10 保持。r730 profiling、frames/locals、额外 body marker 授权已耗尽。后继只用已有普通报告和明确的测试故障入口。

## 已定位的接点

`<PROJECT_ROOT>/reno/personal_authority_enrollment.py` 的 `_owned_with_actual_ex` 当前先完成来源栈、native journal 的 finish/reset，再注册一次 consumer 并发行来源前件，随后释放最外 EX，最后交付 `AuthorityPrefixFiles`。SQL continuation 必须位于发行来源前件之后、释放 EX 之前，沿用原 operation、budget、parent/effective token。

`_perform` 当前只把文件摘要交给外层，实际读取的 `originals/binding/content` 原 bytes 没有随之保存。仅凭这些摘要不能重新生成原 intent。来源前件目前也明确不授完整 `source_closed` 或写权。

旧实现位于 `<PROJECT_ROOT>/data/t16-runtime-graph-cpu-r729/authority_runtime_graph_cpu_r729/`。其中 `activation_sql_engine.activate_original`、`personal_enrollment_store.prepare_original/complete_original` 和 `personal_authority_store_v2_bridge` 可供逐项比较事务与恢复语义；它们绑定旧 namespace2，不能直接复制为当前实现。

## Task 1：保存本次已核对的原字节

**拟新增：** `<PROJECT_ROOT>/reno/personal_authority_enrollment_packet.py`、`<PROJECT_ROOT>/tests/test_personal_authority_enrollment_packet.py`。

**窄改：** `<PROJECT_ROOT>/reno/personal_authority_enrollment.py`；只有确需接入私有登记信息时才改 `personal_authority_enrollment_physical.py`。

1. 列出 SQL 登记实际需要的原件：原 first/second、second 中封存的 intent、namespace reservation/root seal/header，以及本次独立读回的 binding 和完整 `observed_content` 五份 bytes；其中 `local_contract_bytes`、`init_seal_bytes`、`genesis_grant_bytes` 必须保留实际原 bytes，不能后续重新生成。沿用现有合同校验与输入上限。
2. 在 `_perform` 已完成原件核对的位置保存这些原 bytes，绑定当次实际 group、operation、capture 和 token；不增加一次隐含文件读取，不从摘要或六个观察标量重建 intent。
3. 使用固定私有构造和实际登记对象传递 packet。外部 DTO、相等但不同的 token、拷贝对象、替换原件、重复消费均不得获得资格。
4. 普通 publish/verify 的公开文件摘要接口保持。新增 SQL 路径单独接收内部 packet 和实际来源前件；不能让可传入的 callback 决定是否授写。
5. 回归原件逐字节保持、两个 capture 身份、失败消费 attempt、首异常与清理。此任务不进行 SQL 写入。

**完成条件：** packet 能追溯到本次真实独立读回，且不能由 caller 提供摘要或 DTO 冒充；公开文件路径行为无回归。

## Task 2：定义独立版本的 SQL successor

**拟新增：** `<PROJECT_ROOT>/reno/personal_authority_enrollment_successor.py`、`<PROJECT_ROOT>/tests/test_personal_authority_enrollment_successor.py`。

1. 采用新的明确 family/version；保留旧 authority format1 和 namespace3 的字节解释。
2. successor 直接引用原 sealed intent、原 second、binding、prefix 原件，以及提交后的独立 SQL/DB/WAL 观察。每个引用同时校验角色、身份和内容，不能仅比较一个摘要。
3. 将提交前观察和提交后观察分开：SQL 安装会改变物理 bytes，禁止放宽旧 `_fresh` 的原 evidence 全文相等条件来容纳这种变化。
4. 明确 initial binding、空 journal 和初始 head 的合同。后续 mutation journal writer 属于后继任务，不用初始空表证明所有写入路径已完成。
5. 为重复字段、额外字段、错误版本、角色混淆、非法整数、旧 namespace2、替换 intent 和前后观察错配准备精确拒绝测试。

**完成条件：** 纯内容合同可以独立交叉校验；没有 SQL 提交、Store 发布或写权声明。

## Task 3：固定联合 SQL schema 与观察

**拟新增：** `<PROJECT_ROOT>/reno/personal_authority_joint_sql_schema.py`、`<PROJECT_ROOT>/reno/personal_authority_joint_sql_observation.py` 及对应测试。

1. 从旧 `personal_authority_schema` 提取需要保留的 binding/journal/trigger 约束，逐项重新绑定当前 family、namespace3 和原始 intent。先列出完整 schema/trigger 合同，再实现 DDL。
2. 固定表、列、SQL、整数和 bytes 校验；不引入由调用者传表名或 SQL 的接口。
3. 新 observation 分别核对 binding、journal、head、schema 和 triggers。原十表及四 meta 摘要保持原有范围，不能在旧 observer 内悄悄增加 authority 表。
4. 验证 zero-or-same binding：未登记可进入安装，同一完整 binding 只走复核，不同 binding 拒绝。journal/head 不符合初始或恢复合同即拒绝。
5. 覆盖缺表、额外/替换 trigger、不同 binding、非空 journal、错误 head 和合法同 binding 恢复。

**完成条件：** 能区分个人数据观察与 authority 状态观察；尚不开放业务 mutation。

## Task 4：在原活 EX 内拥有并完成 SQL 事务

**拟新增：** `<PROJECT_ROOT>/reno/personal_authority_enrollment_sql.py`、`<PROJECT_ROOT>/tests/test_personal_authority_enrollment_sql.py`。

**窄改：** `<PROJECT_ROOT>/reno/personal_authority_enrollment.py` 的固定内部 continuation 接点。

本任务的真实 COMMIT 依赖 Task 5 的“提交前 prepare”先完成。可先制作事务候选和测试，但不能把 Store 原件的永久准备留到 SQL 提交之后。按以下顺序实现，不能省略提交后观察：

1. 仅做识别本次实际 self/claim/registry 所必需的最小检查，立即记录一次消费 attempt；此后才核实际来源前件、packet、operation/lineage/token、活 guard、所有 getter 和 deadline。验证失败也消耗 attempt，不能在完整校验后才消费。
2. 核对 Task 5 已永久 prepare 的原 bytes、reservation 和实际 Store 资源关闭记录，再创建并登记自有 rw 连接、cursor 和相应资源义务，沿用原 budget/deadline。连接配置、authorizer、progress handler 的安装与恢复需要明确所有者。
3. `BEGIN IMMEDIATE`；严格区分允许的未安装原始 catalog 与已完整安装的 joint catalog，拒绝未知或部分安装状态，并核原十表/四 meta。未安装时调用固定 DDL 安装器，在本事务内用 SAVEPOINT 执行逐条语句；安装器不拥有外层 BEGIN/COMMIT，不用会隐式提交的脚本接口。随后复核完整联合 catalog 与 zero-or-same binding。
4. 仅在未登记时安装初始 binding；随后重新核对同 binding、空 journal、head 与固定 schema。DDL 与 binding 同属本次外层事务，不能以旧 `activate_original` 会自行安装 DDL 为前提。
5. 在同一事务内执行原件、catalog、摘要及末 deadline 检查；通过固定 commit gate 后才 `COMMIT`。
6. 记录实际提交结果和 `in_transaction` 终态；关闭事务连接与 cursor，保留严格关闭回执。
7. 使用新的自有只读观察获得已提交联合状态，再构造 SQL successor。不得把同一 rw 连接中的缓存值作为 fresh committed observation。

异常语义：COMMIT 前仍活跃的事务尝试 ROLLBACK；清理继续执行并保原 first。COMMIT 已成功但后续关闭、观察或文件完成失败时，必须记录“SQL 可能/已经提交、登记未完成”，恢复只能认证原 binding，不能声称已经回滚。

**完成条件：** 同一 EX 和原期限内的实际提交与独立读回成立；原公开路径仍不返回 active/writable。

## Task 5：Store no-replace 发布和 CAS 恢复

**拟新增或窄改的具体模块须在 Task 4 接口稳定后确定；参考旧 `personal_enrollment_store` 与 `personal_authority_store_v2_bridge`，不得直接安装旧目录。**

1. **提交前 prepare：**保持 intent → contract → seal → genesis 原件顺序及独立自读闭包，永久准备实际 SQL 登记将消费的原 bytes 和 prepare/reservation；新 binding/prefix 另具名保存。该阶段及其实际资源关闭须先于 Task 4 的 BEGIN/COMMIT 完成。
2. 文件 CAS 的 successor、logical-operation、slot reservation 必须不存在或全文同 bytes；resume 必须依原 reservation 和原 `payload_raw`，不得重新分配一个“等价”请求。提交前只能固定当时已知的声明，不能预造尚未观察到的 SQL successor 内容。
3. **提交后 complete：**Task 4 提交并独立观察后，先准备 successor/complete 的实际声明，再 no-replace 发布，再记录 published/receipt 并自读。文件 CAS 与 SQL journal 的事实分别记录。
4. 明确四类恢复入口：SQL 未提交；SQL 已提交但 successor 未发布；successor 已发布但 complete 未发布；complete 已有。每一类只认证或补齐原操作允许的缺项。
5. 注入每个提交/发布边界后的中断，验证不同 binding 拒绝、同 binding 不重复登记、首错保留及无覆盖发布。

Store 准备和读回新增的 owner 必须有独立实际关闭记录，由固定 continuation 一并核对；不能向已经封存的来源 journal 追加资源，也不能假定 Task3 的旧来源前件覆盖这些新 I/O。

**完成条件：** 提交前材料确已永久准备；提交后的中断可恢复到同一登记，不覆盖原件，不把重复调用当作新的授权。实际实施顺序是 Task 1–3 → Task 5 提交前部分 → Task 4 → Task 5 提交后部分。

## Task 6：验证、交付与原后继范围

先解决 r878 当前未完成的固定验证。后继采用新的明确候选和执行目录；保留所有原失败及已耗 claim，不重复运行未改的失败尝试。测试选择需结合已有普通报告和实际测试工作量检查批次容量，不能通过放宽时间、删断言或虚计通过数完成。

每块流程为：完整 before/candidate/inverse → 两份有限源码复核 → 正式固定选择与本地依赖 → 同 raw AST/compile → freeze/admit → 一次已审执行 → 完整结果保全与独立结果核对。使用 PowerShell 7 和 `.venv/Scripts/python.exe -I -B`；具体 supervisor 命令在相应候选确定后写入该块执行清单，不复用 r878 命令或 claim。

最终交付需要分别说明：具名测试终态、未执行项、真实 SQL 提交、连接/native 关闭、独立提交后读回、文件发布、恢复与普通路径回归。Mock、受控故障和纯合同测试不得替代真实临时库中的事务证据；临时库证据也不称正常库或完整产品验收。

之后仍须接 Store/live witness、H05 的全部 mutation/upload/附件阶段，以及 H03/H04 的停写、最终包、晋升恢复和永久撤销分享。未接入这些调用点前，不发行可写状态。条件 T15、云部署与 P06 原缺项保留各自边界。

## 回滚

源码回滚只针对本块 owned delta：当前全文必须仍等于候选，才可恢复保存的 before，不能覆盖其他 dirty 修改。SQL 已提交后的恢复不能靠源码回滚冒充数据回滚；必须按原 binding、reservation、提交后观察和完成记录确定状态。正常库操作另依已经授权的真实维护范围和具体已验证迁移方案执行。
