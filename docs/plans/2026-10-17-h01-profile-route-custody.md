# Profile 来源接线与操作记录释放实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把真实创建时持有的 profile owner 传入已有 owned 库入口，并使操作记录在成功、失败和恢复路径都有明确持有者。

**Architecture:** 保持普通 StorageProfile、四字符串 JSON、copy/pickle 和只读 HTTP 接口。严格来源路径显式传 owner；统一操作 custody 持有原始记录，各模块全局索引只弱登记 custody。保留原异常及 traceback，不把元数据释放当作物理关闭或写权。

**Tech Stack:** Python 3.12.10、现有 ContextVar/SQLite/资源 owner 合同；本阶段候选不安装到正式树或 c973。

2026-10-17；r1013 设计01。前驱为 r1010 source05 和 r1011/r1012 两批实际记录。本方案不重放已耗诊断或测试 claim。

## 已确认的问题与本段交付

三个 enrollment owned body 仍直接创建 binding/group；`_construct_group_origin_owned` 仅被 bootstrap 取值和封存，没有实际调用者。scope 的 `select_paths` 只读取当前 profile 字段，没有消费构造 owner。普通 sync route 和 `/api/personal/authority/status` 也不是 enrollment 写入口，不能据此宣称产品接通。

scope 的 raw records、scope/origin 索引、control ledger、registration histories 和 resources/path/walk 索引均有强引用。把 owner 字段改成弱引用仍不能解决原异常 traceback 经调用帧保活 owner 的问题。EX 普通退出、group retired 或删除 origin 的七个索引均不能代表整个记录图已释放。

另有独立兼容缺口：r1010 已容许普通 copy/pickle 写入 `StorageProfile.__slotnames__`，control 的全量类字典门仍会误拒该合法变化。本段先完成这个窄修订及来源选取检查；完整 route/custody 接线按以下依赖继续，不在本段启用严格 route，不承诺操作后 GC。

## 决策与替代方案

采用统一操作 custody：原始 rows 保留独立身份锚，统一 custody 强持完整记录图；全局索引只有弱 custody 引用。查找先升级为局部强引用，再核原 row、claim、budget、group 和 class/code 身份。显式 scope/consumer 借用保证活跃期间不会因 GC 丢失历史。

保留全局强表、退出后逐表删除也可实现，但多模块部分删除需要可恢复的移交协议，漏掉任一反向索引就会保活 traceback。仅弱化 owner 字段无法闭合。本方案不以清空 traceback、删形参、修改原错误或重建当前 profile 值代替记录图管理。

custody 只管理内存记录。若物理关闭、reset、consumer 释放或欠账仍 unknown，最后一个业务借用必须移交给明确的 pending 恢复持有者，不能直接丢弃。该持有者、容量、拒绝准入及恢复时点必须在启用 route 前实现；未实现时候选不可激活。GC 断言只适用于已终止或已完成真实移交且调用者也放弃引用的图。

## 文件级任务与依赖

### Task 1：合法 cache 的固定类门（r1013 本段）

Modify 候选 `personal_authority_guard_control.py`：无参数私有 factory 捕获原 `lifecycle.StorageProfile`，返回 `_class_pin/_check_class` 闭包对。保既有四元组 pin 协议。仅从捕获的同一 StorageProfile 自身类字典排除 `__slotnames__`；其他基类、类名、descriptor、MRO、函数 code/default/kwdefault/closure 仍按原身份核对。不得提供调用者可选的 ignore 名称或重新封 pin。

其余九份 r1010 source05 全文继承。保完整 parent/candidate/inverse/diff 和来源清单；ASTcompile 十文件。新增反例覆盖合法 cache 新增/删除/替换、copy/deepcopy/pickle0–5 与预热缓存；其他 class 的同名新增、StorageProfile 其他属性新增/删除/替换、原 descriptor、code/default/kwdefault/closure/MRO 变化仍拒绝。完整 scope `_authenticate` 需实际选入并经过该闭包，不能只测同名替代校验器。普通 type-call/Path/copy/pickle 选原 Python 代码；资源和 EX 不执行。

### Task 2：统一 custody 容器和弱索引（接线前件）

Modify 候选 control/scope/registration/resources：统一私有 custody 持有原 raw rows 和各模块索引分区，保独立字典/descriptor 身份锚与篡改检测。涉及 scope raw records、scope 四索引、origin 六索引/root_index、control budgets/claims、registration histories/group_index/attempts、resources path records/histories/resource_origins/guard_owners/path/walk/plan 索引。逐个列出并核对实际 closure 表，不用本段文字清单替代实现时枚举。

全局弱登记值及 weak callback 的 closure/defaults 不得强持 custody、row、profile、owner 或异常；只持原标量 key/weakref。弱回调只移除与自身引用仍相同的条目，不能因 id 重用删除新条目。真实查找以原显式 custody/claim 身份为依据，不能搜索同 identity 的 profile 补签。

借用是私有一次 token：owner/consumer 正常取得后持有 custody，最后一次释放前检查原借用集合和 pending 事实。重复/异属释放拒绝，不清其他借用。原 caller/body/cleanup 错误分列；不通过销毁原错误或 raw 数据取得 GC 结果。所有正常、失败、构造未返回、部分 setter、重入和并发路径都要有持有者。

### Task 3：真实配置创建点（依赖 Task 2）

Modify 候选 `storage_admin.py`：增加私有捕获入口，调用者先 create owner，再以原四个实际值调用 construct；原 `profile()` 返回类型和行为保持。config/env/`Path.absolute()` 更早历史仍 unknown。构造失败也由同一显式调用者接管原 owner/原异常，不能把新建同值对象当来源。

CLI/JSON、storage_members/backups 的重建点逐条列为新的捕获操作或来源 unknown；不复用旧 owner。普通 copied/unpickled profile 继续满足现有同 identity 业务语义，strict 来源未知只影响明确选入的来源入口。

### Task 4：三个 owned 入口实际接线（依赖 Task 2–3）

Modify 候选 enrollment：`_owned_with_actual_ex`、`_owned_store_initial_with_actual_ex`、`_owned_joint_precommit_with_actual_ex` 增加私有 kwonly owner/custody。唯一 omitted sentinel 保原 legacy；显式 None/坏 owner 不降级。`publish_prefix_owned_ex/verify_prefix_owned_ex` 以及原私有 Store/joint 调用者真实传递同一个 owner。

strict 分支实际调用 `_construct_group_origin_owned`，经 scope begin 进入 group 的现有 `_origin` 分支。新增 `profile_owner` 字段，不能覆盖现有表示线程/task 的 `owner` 字段。同步 slots、固定函数/消费者/bootstrap 目录、原逆向和签名调用点。不能只新增无调用 helper。

### Task 5：scope/root 消费原构造结果（依赖 Task 4）

scope 用原 owner getter 校验 actual constructor returned 同一 profile/partial self、body 完成、无 pending/first/final/mapped/reentry/cleanup 错，四原参数和48调用、四 setter return/effect 均完整；保存同一原 Cdict 与四个原 Path。持续核对当前字段，不重新构造 profile 补历史。只记录事实，不升级完整 globals/C、物理资源、marker、metering 或写权旗标。

接齐原 database.parent、两级 roots 去重/排序和 identity 比较；合法 Path/cache 变化按既有合同处理。完整字段/调用 schema 依原 producer 源码封存后固定，不能以当前快照替代外层实际返回。

### Task 6：应用长期持有和跨线程（依赖 Task 3–5）

按 r1010 `ROUTE-PROPAGATION-01.md` 逐点接线。API 请求局部显式跨 threadpool 传 profile 与 owner/custody；构造线程/task 只作历史，后继操作重新核当前 owner。worker `_current.profile` 使用配对字段并对称恢复；Cycle/StateStore 原 profile 持有点配对持有。frozen slots DTO 保原字段，使用显式外部封套，不临时挂属性。JSON 仍四字符串，不把内存 owner 序列化为授权。

只读 authority status 保观察语义，不新增 HTTP 写端点。普通 sync 路由不因本段候选被描述成已调用 enrollment。

### Task 7：正常退出、失败和 pending 恢复（依赖 Task 2、4–6）

三个 owned body 最外层 finally 负责最后借用的释放或真实移交。顺序晚于已进入 EX 的实际 exit、group 最终事实读取、成功路径原 consume，以及 packet/carrier 退休。失败先保原 first/tb，并观察全部已获取 owner 的 close 欠账、scope reset 和实际 consumer 释放；不调用未进入的 exit，不覆盖已提交事实，不把 close 返回替代实际效果。

最后借用释放前区分：已终止记录可结束内存 custody；仍有未明物理债务则移交 pending owner 并保 original roots。pending 的持有、容量拒绝、恢复读取、重启后新准入和最终释放必须实现并测试后才能启 route。元数据退休回执不授 `source_closed`、physical close、业务退休或写权。

### Task 8：隔离检查与安装门

先双审完整源码和新测试/准备输入，再以新 epoch 验证真实 producer → owned body → scope/group/root 调用；包括 strict/legacy、显式 None、不同实例同 identity、失败原错、borrow 重复/误归属、threadpool/worker恢复、正常 GC、错误 traceback 环 GC、pending 不提前 GC、弱回调 id 重用、各模块剩余强根枚举和四恢复。旧69唯一ID最新pass不能外推为新组合通过。

固定使用 PowerShell7 与 `.venv/Scripts/python.exe -I -B -X utf8`；具体最终脚本和 SHA 双审封存后执行。保 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest children；不运行旧 supervisor，不用脚本时间门冒充可抢占超时。实际 source-selected 模型、真实 EX/native/SQLite/产品验收分列。

完整 strict 路由、custody/pending 恢复、原111/94/r919、Store/source_closed/live witness/写权、H02–H06/永久分享仍有工程工作。正式安装须完整组合通过准入；本段 cache 窄修订通过不能替代这些门。回滚为保留未安装候选和旧红，不覆盖正式树/c973。

## 范围保持

普通开发和同一只读代理复审已有授权，不另建代理/分支/worktree，不 commit/push。正常库、服务/dist、既有 ACL/settings 和真实平台保持。r920/r964 已耗不重跑；r967 待原特定答复不执行/重问；P06 无历史材料0/7不再问。完整 Goal active，产品 pass 不因设计或模型提升。

## 修订02：在使用类校验器前认证新闭包

source01的S001：scope既有固定目录未保存control两类门的闭包pins，新共享profile_class cell可被改指其他类。source02保原control候选，在scope._PINS第三项独立保存两个导出名及完整function pin；_authenticate在首次调用类门前，用原_GET直接检查导出身份、code/default/kwdefault/closure/cell身份，不调用正在认证的校验器或另一个可写control函数。空cell也拒绝。原两项class/function目录保持，其他scope函数不变。新实际检查必须覆盖真正scope入口的cell改指/清空、code/default/kwdefault变化及导出替换，并证明被改checker未被执行。完整global/C/全部scope认证并未因此获得证明。
