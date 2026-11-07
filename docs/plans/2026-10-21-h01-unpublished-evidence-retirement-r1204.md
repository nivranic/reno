# I07 未发表记录与退休：分阶段修订方案（r1204）

状态：Root事前方案；尚无本批产品候选。Goal保持完整active/incomplete，用户已选A。本稿不把提交标志当成证据仍存在的证明，也不把下述G1当作完整保全方案或可安装版本。

## 1. 当前实际问题

基线是r1203 selected三完整文件370315B。directory cleanup捕获unknown holder的retain失败后可以正常返回；retirement随后只看any unknown来置永久gate，无论debt_submitted是否为False，仍会retired=True、sync并pop全部resources/scopes。DATA driver不检查详细发表结果，继续清普通operation登记。

因此本方实际发表尚未确认时，可能失去最后的持久私有registry根。不能说最后内存引用必然同时消失：driver局部、闭包和其它真实引用仍可能存在，但不构成持久登记保证。

source/unit不同：原retirement先cleanup，cleanup末轮retain异常向外传播，会在retired/holders/units pop之前退出，私有units仍持根。source record和operation普通map的退休不能证明native unit已退休。source还存在unknown→retain失败打断后续holder close的风险，属于后继穷尽清理工作。

## 2. 完整终态要求与路线裁决

完整I07要求真实close/lease/event事实分开、详细证据在原上下文退休后仍有可靠根、公开registry删除/替换不能销毁唯一证据、待发表不能被假报完成，且不能将整scope内强Task/registration/budget/callback/first当作弱历史保存。

| 路线 | 结论与代价 |
| --- | --- |
| 只查submitted，随后把工作称为完整保全 | 否决。True只记录原put成功标记，不能保证公开条目仍存在；scope.debt_transferred也不是全holder聚合 |
| G1先拒绝本方未提交/正在发表分支的脱离，G2建立实际producer私有最小证据，G3完成native来源和生命周期 | 选择此分阶段路线。G1是必要的中间安全门，不能替代G2/G3或被单独安装 |
| 发表失败后临时复制整scope到新全局容器，或增加公开retry/scheduler | 否决。失败后分配本就可能失败；整scope长持Task/first且没有可信最小schema，额外调度也不能替代实际拥有根 |

G2方向是原ownership kernel内的私有最小失败证据，使用已有原dict/封存closure模式，不暴露可写接口。必须由实际producer建立独立source/expected关联，不能从当前公开debt value反造expected。公开表不再承担唯一保全职责；未能建立最小记录时保留原私有root。记录的明确字段、首次snapshot与最终事实区别、public提交观察、identity key/id复用、Task弱引用、隐含type/module路径及SCC仍待完整设计和复审，本轮不写archive代码。

## 3. 本批G1精确改动

保持directory原retirement的cleanup调用、any unknown永久gate及正常/published路径。在cleanup返回后、任何retired赋值、retirement sync和pop之前，新增按真实holder_states检查的拒绝门：

```python
if any(item["debt_publishing"] or (
        item["ownership"] == "unknown" and not item["debt_submitted"])
        for item in state["holder_states"]):
    put(gate, 0, True)
    pending_first = peek(state["registration"])
    if pending_first is not None:
        raise pending_first
    raise make_error("UNSETTLED")
```

publishing检查覆盖所有holder，不限于unknown；不靠scope.debt_transferred，不只保存某一个holder。put永久gate在新增peek/错误构造前；一次peek取得实际first并直接raise同一对象，不读异常链/frames/locals/notes。无first才调用已有make_error；其分配/准备失败自然沿原driver first路径传播，不能保证每次都构造出UNSETTLED。

拒绝路径保留原scopes/resources以及所有holder登记，retired不改，retirement sync/pop均不运行。若前面的cleanup自己已经抛错，本门不执行，原私有登记同样未被此retirement移除。本门不新增close/release/retain调用，不重试OS或账本操作，不新增phase、field、type、公开API、retry入口或scheduler。

没有pending时原retired/phase/sync/pop顺序原样保留。submitted=True但公开entry已被删除/替换的情况仍不能由G1识别，必须交G2处理，不能借A的外部系统资源排除豁免本方registry完整性。

## 4. 明确承认的强根与兼容影响

G1保留整个scope，会直接/间接长持owner Task、registration/record、budget及callback和first；_FIRST_RETIRE只清独立first map，不会清这些字段。没有新retry消费者时，根可能一直保留到进程结束。它不是弱历史、不是仅scalar记录，也不是完整的长期内存生命周期方案；故G1候选不允许独立安装。

原public debt的weak Task及closed history形状不变。新拒绝只处理原本错误地脱离根的失败支；原正常路径不变，但增加generator/调用准备、错误和traceback位置，不能因此声称完整原调用域/COMPAT、异步/OOM或ABI已验证。

重复captured cleanup在原私有状态未篡改前提下仍由close_attempted/release_attempted防止重close/重lease；可以重复尝试原metadata发表/同步，不等于账本最终已结清。G1没有新增跨driver结束后的调用机会。

## 5. 文件级交付与依赖

建立三个完整文件的局部未安装候选组合：

| 文件 | before身份 | 本批责任 |
| --- | --- | --- |
| directory_native.py | 73648B/d3432ab4c32d369aead4ab9298b542a027039d2a0f4f6c95397378bcb3c8f4e3 | 仅上述retirement guard；G2 archive留待完整设计 |
| operation.py | 164640B/7541afe67183abf10cd74ed86706d6e1d5ae449cf18bf1eceb68f5d546625c0a | 只修DATA/SOURCE两处假定详细debt已经公开的注释，不改控制流；明确native私有pending根与operation普通map退休分离 |
| source_native.py | 132027B/59a8c270cc1d2012a115fb9baf059207b9360c1d27bfaa21026ceec608512f91 | 全文原字节继承，已有source传播/保根和穷尽清理待办不冒认修复 |

设计须Root及同一只读代理事前复审后才写候选；完整before保留，实际候选另经源码双审和完整字节正逆。该组合不是完整159，不支持单独覆盖live。G2/G3、原资源联合适配和原执行准入完成前不安装。

## 6. 验证要求与实际限制

| 情形 | 所需行为与证据 |
| --- | --- |
| 任一unknown&&!submitted，其他holder已submitted | gate置True；任何retired赋值/pop前拒绝；全部scope/holder原根保留 |
| publishing=True，即使不是unknown | 同样拒绝；不能用递归retain早返冒认发表完成 |
| 有实际first | 一次peek、抛同一对象；不得从镜像制造first |
| 无first且错误构造成功/失败 | 成功用原coded UNSETTLED；失败保实际准备异常及所有原根，不重置first |
| 全无pending | 原retirement后缀原样；正常闭合逻辑、public key schema和source producer不变 |
| cleanup自身失败/已retired partial状态 | 不把本门当作全面修复；原根、实际标记与partial另行分析 |
| 公开entry删除/替换、cache/actual publication差异 | G1明确未解决，不声称整体证据已保全 |

本批只执行原文/字节/身份核对，不执行产品、候选、AST/compile/import、测试、collector、native、QA或诊断。上述是验证要求，不是已运行用例。原30calls/2children/1990365B及已过截止不重置，r920/r964已耗、r967不执行，不新增异常链/frames/locals/notes、profiler/marker/native微诊断。

## 7. 剩余范围与回退

G2实际私有记录、submitted后entry损失、final事实与first cache、id复用、source穷尽、first/OOM/异步、全字段/NULL/partial/N4/SCC、actual Ingress/Consumer/source和>=3.11/ABI/build全部仍待；I07及H01不能标完成。完整H01–H06、H02持久链、分享撤销、P06缺证、条件T15和生产云保持原范围。

未安装，无运行回退。若候选复审失败，保留红稿再修，不reset/clean仓库，不写正常库、服务、dist、权限/settings或Git，保留既有未提交变更。A已选，不需再问；P06用户无备份，不再询问。
