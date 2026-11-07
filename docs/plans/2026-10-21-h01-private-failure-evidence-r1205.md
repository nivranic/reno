# I07 私有首次失败证据与 source 续遍历联合方案（r1205）

状态：事前设计01，尚无本批源码候选。继承用户已选A、r1202主方案+边界附录及r1204 G1；Goal active/incomplete。本批拟交付两kernel私有历史证据及source清理继续性，禁止独立安装，不是完整I07/H01或完整159。

## 1. 已有事实与本批目标

r1204 candidate-01三个完整文件为唯一before：directory74031B/7dfeb4b0974921f10522edeee3d548622c6ee5d108e1906f8e0f8d4000d30e67，source132027B/59a8c270cc1d2012a115fb9baf059207b9360c1d27bfaa21026ceec608512f91，operation164776B/772a620ac4cd46e459f7f8dfa9d6e39bb8a6466acc43908146243a5798226428。

G1不允许directory在unknown&&!submitted或publishing时撤掉私有登记，但submitted只表示过去put路径，公开dict条目可被删/替换，不能承担唯一保全。source现依赖末轮retain失败传播来保units；直接catch后正常返回会使旧retirement误pop。因此本批必须联合改变source继续性和退休前证据认可门。

有限目标：在原bootstrap kernel及其私有state/closure没有被篡改的条件下，由原retain实际构造、保存首次cache，并在任何公开put之前建立独立私有根；退休逐实际holder认可该根。原公开schema、normal-close/lease/event实现、closer次数不变。不读取公开value来造expected，不把提交镜像当保全证明。

## 2. 决策与拥有图

| 路线 | 裁决 |
| --- | --- |
| G1长期持整scope/unit，或公开dict作为唯一证据 | 不足：前者长期强持Task/registration/budget/callback/first，后者不能抗删条目 |
| 失败后复制整scope、增加retry API或scheduler | 不选：复制也会失败且保留大图，调度不证明实际来源 |
| 每bootstrap独立私有exact-dict，数值identity索引、强持真实key(s)、首次cache及独立put历史位 | 选择。保原builtin/closure模式，仍须未来native来源/N4/ABI联合适配 |

两个kernel各新增一个失败证据dict；不共用公开debt表，也不以Python heap key对象作hash key。directory索引id(holder key)，record为exact-list三槽`[实际holder key, 原首次payload tuple, False]`。source同样索引id(holder key)，record为四槽`[实际unit key, 实际holder key, 原首次payload pair, False]`。只在首次构造，已有record不得覆盖、删除或重置历史位。强持key(s)防其id复用；遇同索引但key/cache身份或形状不符，拒绝，不替换旧记录。

目录cache仍是原18对字段/value tuple；source仍是原`(key三tuple,value六tuple)`。它们是首次历史快照，绝非最终close/lease/event或最终退休事实。source resource_id允许None，outcome保原任意长度exact-str，不截断/拒绝。固定槽数不等于条数/bytes有界。

最后bool槽仅由本层实际公开put正常返回后置True，永不根据submitted/debt镜像造True，也不因公开entry随后被删而重置。准确语义是“本层put后历史位曾提交”，异步中断可使实际put已发生而该位尚False；它不是当前公开可见性、持久化或正常关闭证明。退休不以该位为True为前提，因为本批目的正是公开失败时仍有私有历史证据。

拥有图（新增直接payload）为`长期kernel函数→retain/has_failed_evidence闭包→failed_evidence→record→真实key(s)、首次cache、bool`。没有新增直接scope/unit/holder/Task/registration/异常payload。key→heap type→Python函数globals→模块/其它registry的隐含图依然存在，类可变；不能据空slots声称完整图无Task/module或N4已闭。进程期强key/cache累计成本明确新增，无删除器/归档getter/授权入口。

## 3. directory精确接线

1. 原`scopes, resources = {}, {}`改为`scopes, resources, failed_evidence = {}, {}, {}`。原docstring改正“state永不留在weak history/debt”混合表述，明确private failure evidence与public weak history分开，原closure trust条件不变。
2. retain之前新增唯一内部helper：

```python
    def has_failed_evidence(state):
        record = get(failed_evidence, id(state["key"]))
        return (type(record) is list and len(record) == 3
            and record[0] is state["key"]
            and type(record[1]) is tuple
            and record[1] is state["debt_payload"]
            and type(record[2]) is bool)
```

该helper只在本kernel内部消费，不导出；不证明初次来源或fresh。exact-list门先于索引，只有`is`比较真实key/cache，不hash/eq未知对象。

3. retain保原PHASE gate、永久gate、publishing防递归及finally清publishing。将原`if not state["debt_submitted"]`内的payload构造提升到try内、该if之前；原18字段构造全文不变。payload存在后：

```python
            record = get(failed_evidence, id(state["key"]))
            if record is None:
                record = [state["key"], payload, False]
                setitem(failed_evidence, id(state["key"]), record)
            if not has_failed_evidence(state):
                raise make_error("UNSETTLED")
            if not state["debt_submitted"]:
                public_payload = dict(payload)
```

随后原lock、共享key grammar及public setitem原样；紧接真实`setitem(debts, id(state["key"]), public_payload)`后、原submitted赋值前加`put(record, 2, True)`。原debt_transferred/sync/finally保持。不新增公开put重试次数控制；原metadata可重试语义保持，不重试closer/lease。

4. 原G1退休门改为以下逐holder谓词，原cleanup和any unknown永久gate保持，拒绝body保持先gate、一次peek、同一actual first、否则原UNSETTLED：

```python
        if any(item["debt_publishing"] or (
                (item["ownership"] == "unknown" or item["debt_submitted"]
                    or item["debt_payload"] is not None)
                and not has_failed_evidence(item))
                for item in state["holder_states"]):
```

已有cache/提交位也需要关联，不只核unknown；publishing覆盖所有holder。全部认可后才第一笔retired/sync/pop。原正常退休后缀逐字节保持。公开put失败但archive已实际成立，cleanup可记actual first后进入原failed退休；archive不持first，原driver仍按actual first失败。

## 4. source精确接线与三层继续性

1. 原factory在`integer=int`后新增定义时捕获`boolean=bool`；原`units, holders, permanent_gate = {}, {}, [False]`改为加第四local `failed_evidence`。新增`mark = sequence.__setitem__`用于确实exact-list历史位，原dict `put`不变。新增helper：

```python
    def has_failed_evidence(holder):
        record = get(failed_evidence, identity(holder["key"]))
        return (kind(record) is sequence and length(record) == 4
            and record[0] is holder["unit"]["key"]
            and record[1] is holder["key"]
            and kind(record[2]) is tuple_type
            and record[2] is holder["debt_payload"]
            and kind(record[3]) is boolean)
```

不新增运行期可写bool global查找；directory原factory已捕获bool。record不是隐式unit payload，unit仅在消费时从本次实际holder读取。

2. retain先原permanent_gate及unit unknown锁存，保publishing/finally。原payload构造提升到`if not holder["debt"]`之前，原tuple内容不改，注释改为首次历史cache、None/任意长度outcome，不称bounded IDs。payload之后先：

```python
            record = get(failed_evidence, identity(holder["key"]))
            if record is None:
                record = [unit["key"], holder["key"], payload, False]
                put(failed_evidence, identity(holder["key"]), record)
            if not has_failed_evidence(holder):
                raise error("UNSETTLED")
            if not holder["debt"]:
                put(debt_map, payload[0], payload[1])
                mark(record, 3, True)
                holder["debt"] = True
```

原sync/finally保持；不对公开value做eq/iter，不增加public schema或dict key类型。

3. close函数及其中unknown/retain不改。close抛出的retain/prepare错误仍由原cleanup外层close handler捕获并调用first。该handler中原certified_closed/unknown分支整体包一层try，保原所有分支条件/outcome字符串；新except仅`first(unit, retain_error)`，然后继续原reverse遍历。不得将第一次first也包在这个新try里，不将first失败误称已保全。

```python
                except exception_type as exc:
                    first(unit, exc)
                    try:
                        if holder["certified_closed"]:
                            if holder["ownership"] != "unknown":
                                unknown(holder, "unknown_bookkeeping")
                        else:
                            unknown(holder, "unknown_close")
                    except exception_type as retain_error:
                        first(unit, retain_error)
```

原certified_closed解释注释保留在try内。末轮仍遍历整个unit而非只role，每个unknown的retain各自try/except，失败用原first记录后继续，不删除任何unknown。snapshot/end段不改。这样覆盖close内retain、外层handler再retain和末轮retain三个实际接点；不另调用close/release、不放宽原attempted/release_attempted。

4. source retirement在原cleanup与any unknown gate后、任何retired/sync/pop前新增全holder门：

```python
        if any_values(holder["debt_publishing"] or (
                (holder["ownership"] == "unknown" or holder["debt"]
                    or holder["debt_payload"] is not None)
                and not has_failed_evidence(holder))
                for holder in unit["holders"]):
            permanent_gate[0] = True
            pending_first = peek(unit["registration"])
            if pending_first is not None:
                raise pending_first
            raise error("UNSETTLED")
```

全部认可后才原后缀。原archive缺失时，即使cleanup已完成遍历也抛actual first而不pop；archive已成立但公开失败时允许原failed退休。source继续性与该门同候选、同复审，不能单独安装任一半。

## 5. operation责任及边界

operation只修SOURCE finally原“Every captured cleanup is exhaustive / repeat is a no-op”三行注释，改为captured cleanup在first/root bookkeeping可用时继续剩余holder；重复调用不再次close，但可补未尝试的lease或metadata；外层继续其它root仍受first自身可用性约束。DATA/SOURCE已修的map/debt注释和全部控制流保持，不新增第三类业务行为。

不承诺持续OOM/异步/first或sync失败时全部holder均能访问。first自身失败可在嵌套原handler路径再被捕获，原first身份未必保存；cleanup入口peek/sync、遍历准备、未知析构、错误构造也有准备失败域。没有足够证据时保原registry根，不能把保根说成已经完成OS清理。没有永久重试消费者，未归档pending整图可长留。

source入口sync_unit在首次retain之前失败时，permanent_gate未必已置True；这里只证明该retirement尚未pop原units，不保证所有这种pending都已阻止新业务。旧first若map写入失败与已入map但mirror同步失败也不同，不把后继重记当前实际异常称为更早native错误已保存。

私有cache不包含异常，原first生命周期和driver清理不改。没有新的closer、lease refund、OS重试、phase、holder field、公开API、scheduler、异常链/frames/locals/notes或诊断标记。normal-close后的event/lease失败、最终真实事实分账、native owner运输、来源认证、fresh、完整调用域/COMPAT仍按G3/I07/I08待办。

## 6. 失败矩阵与验证准入

| 情形 | 本批预期文本合同，均未运行 |
| --- | --- |
| 首次payload/record构造或archive插入失败且后继仍未补成 | 永久gate在先；继续性以first成功为条件；退休门在任何pop前拒绝，原scope/unit保根 |
| archive成功，公开dict构造/lock/schema/put失败 | archive保存真实keys/cache，history位False；实际first不覆盖，处理后继holder，允许failed退休，不假报公开成功 |
| public put成功、位与submitted之间中断 | seen/镜像可能错时；只实际put后可置seen；不以镜像补造历史，不重close |
| 提交后公开entry被删/合法key下value被替换 | 不回读unknown value，私有record保原cache；bool仅历史，不保证当前public entry |
| 某holder无归档、其它已归档或publishing | 全holder门拒绝全部退休pop；不是只保存最后holder |
| 同id record存在但真实key/cache不匹配 | 不覆盖旧证据，原错误/first路径拒绝；强keys防自然id复用 |
| source role cleanup发生retain错误 | reverse closer限原role；末轮retain仍全unit；全部pop只在unit退休门之后 |
| 没有任何需归档holder | 新空dict/bootstrap与循环准备仍是COMPAT成本；原closer、lease、public schema及退休后缀保持 |

设计必须Root及同一只读代理事前复审通过后才写三完整文件candidate。源码再双审；对每一原文替换块核唯一、完整正逆字节、未涉及块全字节不变，封存actual identities。Root metadata命令不冒认独立事前复审。本批只文字/复制/bytes/hash/diff，不执行候选/产品、AST/compile/import、测试/collection、collector、native/QA/诊断。表格是所需行为，不是已跑用例。

原30calls/2children/1990365B与已过截止不重置，r920/r964已耗，r967不执行；不重放旧QA/未变失败/r1173修订04及21未跑。正常库、live源码、服务、dist、权限/settings与Git不写。保留现有约960变更，只复用r924同一只读代理。

## 7. 交付与剩余范围

目录data/t16-private-failure-evidence-r1205，完整before/candidate及差异、实际双审、CURRENT和hash封存；parent不改。若复审红，保留红稿再修，不reset/clean。未安装所以没有运行回退动作。

G2本批只实现“首次历史证据不依赖公开表”与受条件的source继续性；不完成最终close/lease/event事实、任意state篡改防护、key/type/module全图/N4/SCC、>=3.11 ABI/build或联合安装准入。H01/B001+B002、H02持久链、H03/H05/H04/H06、分享撤销、P06无备份历史缺证、条件T15和生产云仍未完；不再询问A或P06，不把Goal标complete/blocked。
