# Task7 原 request/grant/charge 历史闭合 Implementation Plan

> For Codex: 使用 executing-plans 按源码、脚本、实际准备、实际记录的既定双审门执行；沿用同一名只读复审代理。

**Goal:** 在 scope 的正常归还前与 directory fence 内后验，核对原预算 claim、请求、grant、charge 和累计账的一一对应，拒绝尚未结束或被替换的结算历史。

**Architecture:** 在原 control factory 内增加只读 settled-history 门，直接使用原两个索引和已持有 Call，不调用要求活动 Context 的 state，也不创建新 Call。scope 的原来源门把自身原 grants 与该原历史逐项核同；不创建独立“费用完成”票据，不改变五项 scope evidence、消费、归还顺序或原计费方法。

**Tech Stack:** Python 3、现有 custody/pending、原 slots 记录、固定函数/类目录、PowerShell 7。

## 已确认的缺口与范围

来源为 r1049 SOURCE03 的 control/scope/resources/custody 和 r1050 已完成的模型记录。`scope_return_facts` 当前只核 grant 的归属、Token、settle_attempted、settlement_finished 和 charge_cell 非空；尚未证明该 charge 是原 control ledger 的唯一对应项、actual 与 grant 一致、累计链从零到原 counter。`finish_settlement` 的 finished 明确只表示尝试已结束，因此不能直接代替这些检查。

本实现关闭这条原历史的准入缺口。它不证明所有实际 marker/body read 都已接入收费、actual 来源于真实读返回、完整 future-demand/capacity、物理关闭、terminal、root 释放、Store 写权或完整 Goal。没有完整读来源时仍不得颁发完整费用或发布权限；相关安装门保持。

## 方案比较

1. **采用：原 control factory 只读历史 + 原 scope 配对。** control 认识原 budget/claim/ledger，scope 认识原 grant/Token/来源；各自核对自己的原件，scope 在归还门内组合。没有 caller 提供的 expected 列表或自选 provider。
2. 只比较 `control_bytes == ledger.total` 无法发现漏项、替换、重复或金额配对错误，不能关闭本缺口。
3. 新建影子费用账、从当前数值回填缺失历史会丢失原 producer 事实，与原单调账合同不符。

## Task 1：原 control 只读历史

Modify 未安装组合的 `reno/personal_authority_guard_control.py`；其余现有 request/charge/claim/state/facts 物理定义保持 AST。

- 增加原 custody leaf gate，已有 Call 时原样复用；没有 Call 的直接调用拒绝，不能在 fence 中发新 loan。禁止调用原 regular `lookup/state`，因为它们会创建子 Call、要求活动 scope，或在失败时修改 ledger。
- `_make_core` 增加 `settled_history` 并导出 `_settled_history_owned`。预算必须已由原 Call 观察属于当前 owner；通过原两个 budgets/claims 表、原 claim 镜像、原 budget dict descriptor 核同一个原 `_Ledger`。未知、foreign、partial、failed 都拒绝，不降级 legacy，不修镜像，不改 first/phase。
- 核原 claim_write_returned/effect、phase=ledger_only、first/traceback 为空；原 owner/两 deadline token/原 absolute 不变；callback/callback_primary 为空；exact int 的 total/body/control_bytes 相同且在原 cap 内。
- 核原 requests/charges 都是原 list；逐项保存原对象到只读局部 tuples，数量相同、每种对象身份唯一。request 必须是原五槽 list：exact positive int requested、clock_returned=True、exact positive int grant、returned=True、failure=None。
- 顺序从零开始核每对：grant 必须等于当时 `min(requested, cap-before)`；charge 必须 exact `_Charge`、exact int before/actual/after，0<=actual<=grant，before 等于前项 after，after=before+actual<=cap。七个原写入/效果/post-clock/returned 标志均为 True、failure=None。actual=0 是合法值，不按真值拒绝。
- 最后累计必须等于原 total/body/control_bytes。零项只在原 claim 存在且全部累计为零时允许，不推断不存在未计费读。
- 返回当前原 ledger/data/list/tuple/pair 引用和累计观察，仅供原 scope 当场配对；不是可复用的授权对象。返回前重核两个索引、claim、原 list 身份与逐项身份、累计和失败状态。无 clock/IO/reset/delete/settlement 写入。

## Task 2：原 scope 归还门接线与固定目录

Modify 未安装组合的 `reno/personal_authority_guard_scope.py`。

- `scope_return_facts` 在原 grants 完整 raw 校验后调用原 `_settled_history_owned`，使用同一原 Call。将每个原 grant 的 request_cell/charge_cell 逐身份配对到同 ordinal 的原 request/charge；amount 与原 grant 返回数相等，actual 与原 charge 的 exact int actual 相等；数量相同，保原 Token 归属检查。
- 此原函数已同时被 scope-return before 和 directory-return after 调用，因此两处都会重新核历史，不复用前次 bool/快照作当前认证。保持原五项 evidence tuple；不把新建 tuple 加进按身份比较的历史槽。
- 原 control/scope 为循环 bootstrap：scope factory 创建时 control 尚可能未定义 helper。不得在 scope factory 顶层捕获尚未就绪的 helper，也不得冻结初始 None。在现有 `_initialize_origin_owned` 中，control 完成且 enrollment class 已绑定后把 helper、leaf gates、原 classes 与所需固定依赖纳入已有目录。
- scope 的类目录加入原 `_Ledger/_Charge`；原 origin 目录保存 control helper、相关原函数的 function/code/default/closure pins 和原绑定。调用前认证，不能先运行可替换 helper 再核结果。保持原所有目录项，不删旧 pins。
- 不改变两个原 charges/requests list 的生产方式、Scope/Grant/Charge/Ledger slots、32 partitions、43 anchors、原 consume/scope/carrier cells、首错优先、物理关闭或完成状态。

## Task 3：完整候选和源码双审

1. 以 r1049 SOURCE03 的37份完整候选为父本，只改 control/scope 两份，35份逐字节继承。保完整父本、完整 diff、来源 manifest 和旧证据 pins；不覆盖父稿。
2. 37份 AST/静态 compile，核七个原 control 方法和 scope 原关键结构未被重排，检查 leaf 无新 Call、零项/actual0、类和函数先认证及 circular bootstrap。
3. Root 和同一独立代理源码双审。红稿保全、修订使用新版本；有阻断不执行候选或模型。

## Task 4：具名检查与准入

新 batch，不重跑 r1050 的21绿或其他旧绿。至少覆盖：

- 原 claim/0项、1项actual0、1项actual31、多项、剩余额度缩小 grant；拒绝无 claim 却零值。
- 两索引/mirror/原 list/foreign row、duplicate request/charge、错 ordinal、缺任一侧、counter/body/链前后值、bool充int、超过grant/cap、七标志/错误槽等损坏。
- 原 scope 的实际 raw grants 配对、错 request_cell/charge_cell/amount/actual、Token仍由原来源门核；before/after 重验，不把仅 helper 单测当实际scope接线。
- 在原 pending fence 下使用同一 Call 不新增 Call/loan、不写 ledger；失败保原 first/phase/账本和 pending图。已明确 adapter 的依赖逐项列出。
- 原 request/charge 含 failed/post-clock/actual0 合同的具名回归仅因本次改变涉及的 factory/export/固定目录而选择；不改旧脚本/结果。不为凑红先运行未审模型。

脚本双审、实际输入全文封存与双审后才消费新 claim。PowerShell7 + `.venv/Scripts/python.exe -I -B -X utf8`；case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/批内并发1/最多2pytest children 全部保持。无 profiling、frames/locals、停点、定点计时或新诊断。

## Task 5：交付与后续

实际原 CASE/stdout/stderr/Tool 输出及输入全文保全，结果双审；把候选、设计、脚本、模型、实际资源与产品证据分开。主方案只追加记录，AGENTS具名更新且保存前字节并验证其余内容不变。失败仅修原失败ID，不重跑旧绿。

回滚为候选不安装，保完整父本/差异/旧红。正常库、服务/dist、ACL/settings、平台和Git保持。r920/r964已耗不重放，r967待原问题不执行/重问，P06历史0/7且无备份。完整bootstrap、真实guard consumer/marker全计费与完整动态调用闭包、资源债务、两阶段终止/容量/durable恢复、Task6D剩余caller及完整Goal仍须继续实施。
