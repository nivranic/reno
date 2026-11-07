# Task7 directory carrier 逻辑归还实施方案

承接 r1047 SOURCE03、r1048 已完成的 27 项检查。候选仍未安装；本方案不能授予 terminal、root 释放、费用完成或真实库写权。

## 目标与真实触发点

在三个原 enrollment caller 已完成 consume、route cleanup、Context reset，且原 scope-return 实际删除及 effect 均成功后，由**同一次原 scope-return 调用**进入 resources 的 directory-carrier 归还门。不得添加可单独调用的低层删除接口，不把 consumer finish 当成 wrapper 最终关闭。guard 尚无已确认 namespace consumer，遇到 guard 或未知 holder 必须保留，不推断其 parent directory 已消费。

## 1. 同锁与短暂 fence

候选 `personal_authority_guard_pending.py` 的原 Reservation 在占用32槽之前预分配 `carrier_return` 和 `loan_fence`；保留43 anchors、32 partitions、原 snapshot 前10位置。归还批次只尝试一次；每一 carrier 的原 holder/entry/六元 carrier row/consumer/proofs 和 actual delete/result/error/tb、独立 effect 都先保存。partial/unknown 不重试，不清空历史行。

候选 `personal_authority_guard_custody.py`：新 loan 在锁外分配 ticket、nonce、lease、entry、整数 key，随后持**原 pending RLock**重核 owner/state/root 和所属 reservation。最后读取该 reservation 的 fence 与原整数键 dict 插入相邻，之间不调用 Python callback 或进行可触发 GC 的分配；有 fence 拒绝。Call.close 的完整预检、逐项删除和 closed/membership 更新也持同一锁，fence 内拒绝其他 close。部分 loan 已登记而 Call membership 写入失败仍视为债务，不能被遗漏。

resources 归还门复用 scope-return 的原 Call（leaf gate），不制造子 Call。持锁发布 fence **以后**完整重核所有 loan。当前 gate Call 必须通过原 `_call_state`、原 membership 和 entry issuer 精确认领；root 必须是原 anchor[41]。其他活动 Call、scope、guard、未知类型以及未完成 carrier 均阻止批次删除。closed 历史 Call 本身不等于活动 loan。预分配/枚举发生 GC 重入时，只能拒绝新 loan，不能隐式允许相同线程借用。

fence 不是永久关闭标志：唯一批次无论成功或失败，在 finally 中按原身份解除；原 first 优先，解除失败分列保全。退出 fence 后正常借用仍按原规则；已删除的 carrier holder 不可再次 bind。原 pending owner 不释放。

## 2. scope 来源检查复用

将原 scope-return 的完整来源、consume effect、route cleanup、原 owner/ContextVar、全部 n 轮 Token/transition 和 grants 检查移到**只读** origin 闭包 helper；原 scope-return 和 resources 归还门共用，避免复制并漂移原140行合同。helper 在已有 Call 上只使用 raw leaf 读取和原索引，不在 fence 内发行 child Call。

before 模式核原活动 scope lease；after 模式不能再次 bind 已归还 holder，必须核原16槽 scope-return：同原 scope/holder/entry/source_history、prepare/attempt/return/effect 成功、actual result None、错误槽为空、原 entry issuer 为该 scope、holder 在原 loans 中确已不存在。after 仍重核原 consume 和全部历史，不以 retained tuple 或 bool 替代。helper 只读，不删除任何 loan。

scope 内原唯一 scope-loan 删除保留在完整 producer 门中，补原 pending 锁协调，不迁出为独立 primitive。原函数正常返回仍为原 delete 实际结果 None；resources 后继失败交原 caller remember/pending，不抹除已发生的 scope 归还事实。

## 3. directory 的完整 consumer 证明

对原 carriers/loans 做一一对应核对，先完成全部预检和 return-cell 分配，再删除任何 directory loan。必须精确核：

- 原 carrier 六元 row、holder、entry、kind、raw plan、source，parent=None；原 selection、members、anchors 和各原 ledger 登记；同一个原 scope/group 与原 scope-return 来源。
- r1047 原 channel 发行 reader，channel 的 inner 和 observer 均 actual returned None、无 error/tb/later/sink failure；consumer begin/finish/observation 正常。
- 原 inner Call、wrapper Call 都已实际 closed，cleanup_failure=None；仅 observer 中间观察 inner 关闭不足以归还。
- plan 非 None 时原两项不同 fresh verification 的完整来源/当前原 plan_rows 历史、selection、members、complete/outer-return/failure 均核同；None 仅接受原明确 fallback observation，不虚构两个 verify。
- 所有相关原 raw record 当前镜像/独立行完整一致。当前 wrapper 已关闭以后才执行本门；不重跑 marker/verify/IO，不为补证明调用额外物理动作。

删除仅内联在这个完整 resources producer gate 中。原 carrier registry 六元 row 和所有原 proof 引用继续保留，逻辑归还只删除精确的 loan entry；每项保存 actual return 与独立 effect。部分失败保原图和逐项事实，禁止重试/补删。

## 4. 无循环的固定接线

不在 scope/enrollment 顶部 import resources。scope origin factory 捕获原 `groups.safe._directory_trace_resources` resolver；初始化时将其函数 code/default/closure 和真实 globals（模块身份、sys.modules、固定名/绑定状态）纳入原目录。resources 初始化时捕获已完成 bootstrap 的只读 scope helper并纳入原固定目录。resolver 未加载、模块身份改变或 helper 未绑定一律拒绝；不能悄悄跳过存在的 carrier。不得提供运行时可写 provider 或以 docstring 充当 caller 接线。

## 5. 实施、验证和交付

1. 设计由主代理及同一名独立只读代理复核；修订保原稿/红。
2. 37份完整未安装候选，预计 pending/custody/scope/resources 四改，余33份逐字节继承；完整父本、完整差异、37静态compile和源码双审。
3. 新具名模型覆盖正常一个/多个 directory、None fallback、两次 consumer、scope未归还/来源替换/重复、wrapper未关闭/cleanup失败、所有channel失败槽、history/ledger/row替换、未知guard/Call、partial delete/error/effect，以及 fence 后真实线程与 GC 重入借用拒绝、解除后可再借用。涉及真实 C ContextVar 的项单列。
4. 脚本与实际准备双审后消费新 claim；不重跑旧绿批次。case30秒、parent360秒、matrix1680秒、cleanup10秒、control1MiB、批内并发1、最多2 pytest child 不变。
5. 实际保全与双审，清楚列明 full modules / selected AST / synthetic callee；不得把模型归还当作 EX/native/SQL/Store 产品通过。

本步骤仍不完成 guard consumer、完整费用和资源债务、最后 Call 两阶段 permit/root 终止、字节容量与 durable 恢复、原111/94/r919、真实 Store 写权、H02–H06 或完整 Goal。r920/r964 已耗不重放；r967 待原问题不执行重问；P06历史0/7且无备份。正常库、服务/dist、ACL/settings、真实平台和 Git 保持。回滚为不安装候选，保留原候选与所有证据。
