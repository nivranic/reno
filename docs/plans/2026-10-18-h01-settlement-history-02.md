# Task7 原 request/grant/charge 历史闭合 Implementation Plan 修订02

> For Codex: 使用 executing-plans 执行，沿用同一名只读复审代理；本修订与首稿合读，冲突处以02为准。

**Goal:** 核对原 claim 创建的请求/charge 容器及其全部结算历史，在原 scope 正常归还前和 directory fence 内后验拒绝缺失、替换或未闭合的账本。

**Architecture:** 原 claim 创建两个 list 时，在现有 claims 分区登记同一个三元原历史 `(ledger, requests, charges)`，分别以两个原 list 的整数 id 为键。只读 history 门直接复用原 Call，按两个原索引和两个容器来源行核账；原 scope 将自己的原 grants 逐项配对。保持32分区、43anchors和所有原slots。

**Tech Stack:** 现有 Python custody/pending、slots、固定目录；PowerShell 7。

## D001：首稿缺少原 list 创建身份

首稿要求拒绝原 list 替换，但旧 `_Ledger` 只在自身 slots 中保存当前 requests/charges。只读门在进入时取它们，无法证明这就是 claim 创建的原两个 list。原完整首稿保留；它不能直接进入实现。

修订采用已有 claims 分区的两条来源登记，不新增表、全局强根、slots、budget镜像或caller provider。原 `_make_core.claim` 中，先初始化 row 和两个 list，并按原顺序根存 budget→row、claim→row，再创建三元 history 并用原 Call.put 分别登记 `id(row.requests)` 和 `id(row.charges)`，subject 为对应的原 list。原 row/list 在后继分配或登记失败时仍由已有 owner/pending 原图持有；不删除或重试，claim 未成功的 phase/flags 会阻止归还。

本来源 row 必须在 class/dict/clock 等可能失败操作前登记。两个原 list 是不同对象；两个整数 id 因原对象都被引用而不冲突。已有 control `lookup` 只查询 budget 与 claim token，不遍历 claims 的 value，原语义保持；实现仍须复核全有效组合内该分区的实际消费者，不凭本句代替搜索。

## 只读门的返回合同

`settled_history(budget, *, _custody_call)` 明确要求已有非 None Call，原 leaf gate 复用它；无独立 `_lease` 或新 Call。返回固定八项：

`(ledger, data, original_requests_list, original_charges_list, request_tuple, charge_tuple, pairs, total)`

每项 pair 为七元 `(request_cell, requested, grant_amount, charge_cell, before, actual, after)`。tuple 是本次只读结果，不作为签发票据，也不追加到原按身份比对的 scope evidence。

门内在读取请求/charges之前，以其当前 list id 查询两条来源，要求是同一个 exact 三元 tuple，且其三项按身份分别为当前原 row/requests/charges；禁止缺一、foreign、错配、换 list或补登记后继续。随后执行首稿的 exact types、唯一身份、完整标志、原累计链和最后复核；最后再次检查两条来源行仍是同一原 tuple。零项也必须有原 claim 和这两条原容器来源，不能凭零值通过。

前述“禁止补登记后继续”指正常实现不提供修补/重建路径；不宣称能够在任意 Python 私有内存均可被外部直接改写的威胁模型下，证明无法访问的对象出生历史。被支持入口识别为损坏的来源不能自愈。

## 文件和回归范围修订

完整37文件组合仍只改 control/scope 两份，35份继承。control 原 request/charge/state/lookup/facts 等定义保持 AST；原 claim 因新增原容器来源登记而允许窄增量，除此保持原初始化、部分根、class/clock、首错和实际return顺序。不要以“七个原方法全部AST不变”掩盖这一必要改动。

scope 的固定class目录加入原 `_Ledger/_Charge`。origin目录补原 helper、leaf gates和所需control绑定及函数pins；初始化在原control就绪以后，不在scope factory执行时读取尚未定义的 helper。原 scope grants 在已有raw来源验证后与原 pairs 按ordinal和身份配对；保持五项evidence和全部Token门。

检查新增：两条来源行缺失/不同/foreign、空list换同值空list、非空list等值替换、claim两条来源登记的部分失败和原图保留、grant与原request/charge一一对应；确认原list真实append后不换来源。完整计费读来源、marker覆盖、所有当前和未来正文费用仍独立未完，不用本门返回或零项账本签发费用完成/terminal/发布权限。

其余准入、双审、原限额、正常库/service-dist/ACLsettings/平台/Git和r920/r964/r967/P06约束按首稿保持；旧绿不重跑，候选未安装。
