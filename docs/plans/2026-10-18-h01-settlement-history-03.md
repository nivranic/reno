# Task7 原 request/grant/charge 历史闭合 Implementation Plan 修订03

> For Codex: 按 executing-plans 执行本稿与01/02，冲突处以03为准；复用同一名只读独立代理，不新增代理。

**Goal:** 在不新增任何 loan、不探测外来 owner 的前提下，读取并闭合原账本历史，再由原 scope 的归还门配对。

**Architecture:** control 只读门使用原 leaf gate 的空 subjects 入口认证当前 Call，随后只读取已持原 owner 的原 state/subjects/partition/index。拒绝缺失或 foreign locator，不调用普通 Call.get/observe/keep，不枚举或借入外来 partition。其余原容器来源、逐项结算与 scope 配对沿02。

**Tech Stack:** 原 custody/pending raw containers、exact weakref、Python固定目录、PowerShell7。

## D002：同一 Call 不等于不会新增 loan

原 `_Call.get` 为了检查索引缺失、partition 或 foreign locator，会先 `keep(owner)`；leaf enter 的非空 subjects 又会先 `observe(subject)`。仅复用同一个 Call 无法保证损坏检查没有新增 loan。首稿、02、Root02漏审和独审01红完整保留，未执行候选。

## 1. 只读入口

`_settled_history_owned` 首先要求非None原Call，使用捕获的原 leaf `enter(parent, ())`，空 subjects，禁止传新lease。原entry gate执行既有custody模块/函数目录认证，之后用原 `_call_state` 核当前仍open，preferred就是已持owner、原call membership合法。读取该owner的原 `_state`；不得通过 `keep/owner/get/observe` 发现或借用新owner。

保存该原 Call raw state、loans逐身份tuple及原membership逐身份观察；最终核同这些身份、数量和当前原Call状态。仅允许原caller原有持有，不把新增foreign借用当成修复。

## 2. 无副作用的原主体和分区检查

用捕获的原 `dict` 方法和exact类型执行：

- 预算、ledger、claim、两个原list及三元来源row已经存在于原owner state的subjects分区，按 `id(subject)` 必须是同一原subject；全局弱locator必须是原二元tuple，owner弱引用是exact原weakref并指向这个已持owner，subject弱引用如存在也须exact弱ref且返回同一subject。不得调用该locator里的未知callable。
- 四个必需表键（budget→ledger、claim→ledger、两list id→原三元来源）通过原 `_table_data` 取固定tag/index/partitions后，只访问这个已持owner的原partition。每个index locator须exact弱ref且返回此owner；`partitions[id(owner)]` 的两exact弱ref分别返回原owner和该原partition；key必须已经在该partition，读取其当前原value。
- 缺key、缺index、foreign index、损坏partitions、foreign subject或失效locator均直接拒绝，不遍历其他partition、不调用外来owner的 `_state`，也不把“没读到”转换为legacy或零项通过。
- 只读取固定原预算字典描述符；原classes/functions先认证，再做预算和ledger字段读取。未知类型不得触发自定义属性/weakref/callable执行。

末次复核所有必需索引、partition/subject登记、原两个list/source、原Call loan/membership以及原累计/失败/token状态；不做clock、remember、phase修补、清除或重建。helper的局部闭包不得进入返回对象/全局目录，避免新增长期强根。

## 3. 现有固定目录与两文件范围

不新增custody primitive、分区、slots或export。control factory在原可信创建阶段捕获既有 `_call_state/_state/_table_data/_SUBJECTS/_ref` 等原件和builtins；custody原entry gate的目录负责调用前核原实现。scope的既有初始化目录继续pin新helper、两leaf gates、原classes及control依赖，保循环bootstrap时序。原 scope raw访问仍按已有源合同执行；本稿“无新增loan”的范围是新control只读门自身，不据此宣称旧scope全部 raw恢复链已关闭。

仍为37完整候选、control/scope两改35继承；原claim只有02的两list来源登记增量，原request/charge/state/lookup/facts定义不变。旧Root02的“无新Call”不能代替本稿“无新loan”。

## 4. 检查补充

具名损坏矩阵须包括：budget/claim/两个list四个key的foreign index或缺index，原owner partition有key但locator缺、foreign partition有同key而原owner没有；budget/ledger/claim/list/source主体foreign locator；partitions登记错配、dead/未知callable locator。对每项独立断言拒绝前后原Call state/loans/membership、原owner及具名foreign owner loan图保持，未知callable没有执行。不得只断言某个异常即视为无副作用。

正常0/actual0/31/多项/partial grant以及原scope before/after调用配对仍按02。fence内不得发行新Call/loan；失败保原账与pending原图。源码、脚本、实际准备、实际结果的双审与全部限额保持，旧绿不重跑。完整读来源/费用、bootstrap、terminal/容量/durable/Store及完整Goal继续未完。
