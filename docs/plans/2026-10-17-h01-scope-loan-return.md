# Task7 正常 scope 借用归还 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在原 consume 和原 Context reset 均有完整事实时，只归还该原 scope 的逻辑借用；所有 carrier、root 和未知债务继续保留。

**Architecture:** 承接 r1043 SOURCE01。先在 pending 原 reservation 预分配 scope-return 事实槽，再由 scope 原 origin 闭包核来源和实际 reset 历史，最后由 custody 精确删除一个由该 scope 发行的 loan。不签 terminal permit、不释放 pending 槽、不启用 strict。

**Tech Stack:** Python 3.12，原 custody/raw-record/ContextVar/固定函数目录；隔离 AST 模型验证。

## 选择与范围

采用具名 holder 的精确一次归还。仅增加 reset 观察仍不能消除当前 scope loan；按 cleanup bool 批量清账会误认无实际 set 的路径。scope 是逻辑 holder，归还它不证明 Directory/native/Store/source_closed 或写权终止；这些债务保留在原 root 图和 pending 中。

## Task 1：原发行身份与归还记录

- 修改候选 `reno/personal_authority_guard_custody.py`：`hold_scope(scope)` 将原 scope 作为 loan entry 的 issuer 保存。保留无参形式供既有调用使用，但无 issuer 的 loan 不获得新归还权。原 scope claim 改为显式传入新建的实际 scope。
- 修改候选 `reno/personal_authority_guard_pending.py`：在占32槽之前预建固定 scope-return cell。准备时核 active/claimed、原 budget、原 consume source_history、原 scope/root；只能准备一次。cell保 lease、原 loan entry、attempt、actual return/error/tb、删除后effect，snapshot只追加新位置。
- custody 归还函数在当前 Call 内核原 owner、scope来源、issuer、同一原 loan entry。先保存原 entry 与 attempt，再删除唯一 `id(ticket)`，保存实际返回及 effect。未知或失败不重试，不 `clear` 任何账。

## Task 2：原来源与实际 reset 准入

- 修改候选 `reno/personal_authority_guard_scope.py`：原 origin 闭包导出专用 scope-return。先核 recover 唯一 origin、budget/group/scope 原反向索引和原 constructor cell，与 consume 已存 source_history 逐身份相同。
- consume cell必须 prepared/attempted/returned，failure/effect_failure 均无，effect attempted/returned，实际 result 是 effect 中同原 receipt；原 group 五slot 当前值仍同观察，phase retired/normal_exit/first 等符合原成功结果。不能仅核 `_consume_attempted`。
- 原 origin、scope 和预算 first 均无；route_cleanup attempted/returned，failure无。scope phase=context_reset、reset_attempted=True、pending=None；grant 逐项已结算且原 charge存在。
- 原 token roots 恰一项，state=reset/reset_attempted=True。transitions恰原一set和一reset、同scope/Token；set实际返回为该Token且effect=True，reset实际返回None且effect=True，两者无failure。核Token原var/old_value、scope baseline和当前原ContextVar实际恢复、原 owner/task身份。没有实际set、复制Context失败、未知Token、额外transition、未决grant均拒绝。
- 只归还该 scope lease；同 Call 自身 loan 在原 finally 正常leave时归还。scope原 lease字段保留作历史，后续借用它应失败。不冒充最后Call或root终止。

## Task 3：三个正常 caller 接线

- 修改候选 `reno/personal_authority_enrollment.py`：packet→route_context_end顺序保持，仅无原body/cleanup错误的strict路径，在route_end之后调用原scope-return gate。异常继续原budget remember/最外pending；所有legacy路径保持。
- 新导出、函数code/default/closure/cells和捕获descriptor同步原固定目录；首次调用前认证。不能在归还后再次调用要求活动scope lease的构造期检查。

## Task 4：验证与交付

- 37份完整候选，四文件修改、33逐byte继承；完整父本/差异、静态compile、源码双审后才准备执行。
- 模型核正常归还一个scope loan且其他call/carrier/root不动、来源替换、重复、消费失败/仅attempt、没有set却cleanup返回、reset失败/效果不符、额外transition、grant未结算、foreign owner、issuer替换、先删除后observer失败保原事实、legacy。
- 真实ContextVar/Token的set/reset应另行明确覆盖；native/SQL/物理EX及Store继续合成，不能外推为产品成功。通过项不机械重跑。
- 实际脚本/准备双审后新claim，case30/parent360/matrix1680/cleanup10/control1MiB，批内并发1。先raw保全，再actual双审与完整readback。失败保原红，定向修订。

回滚为保留未安装候选并使用原候选，不覆盖canonical/c973。r920/r964耗、r967待原问题不执行重问、P06历史0/7；正常库/service-dist/ACL/settings/真实平台/Git保持。完整Task7的carrier归还、最后Call两阶段permit、资源债务/字节容量/durable恢复和完整Goal仍未完成。
