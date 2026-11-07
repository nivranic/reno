# H01 关闭后最终事实与退休消费协议（r1207）

状态：完整适配的后继设计子方案修订02，尚无本批源码候选，不构成安装、构建、运行或诊断准入。用户已选边界A；保留真实audit实参、顺序和回调兼容，只排除外部代码直接使用、关闭、重绑资源。A不豁免本方来源认证、registry、unknown-callee/getter/hash/eq、fresh、N4或ABI。Goal「按照方案推进执行」active/incomplete。

原01全文保留于data/t16-finalization-protocol-r1207/DESIGN-01-RETAINED.md。独立复核01保留R701首次错误跨retire寿命、R702全局顺序来源两项源码阻断；本02与同目录FIRST-AND-GLOBAL-ORDER-ADDENDUM-01.md联合补其字段/producer/consumer合同，不冒认已实施或完整关闭。D06固定budget身份子项本轮已独立核同；本02的聚焦复核另列，不沿用01冒认已审。

## 1. 要解决的具体问题

r1206已在未安装的C候选中保存唯一原生closer的实际返回，但Python侧仍可能在真正关闭以后，才遇到计数、事件、lease释放或表示失败。r1205私有archive保存首次历史cache，不保存这些后继事实；现有失败退休门可能撤掉唯一保有后继状态的scope/unit根。

本方案选择把**真实producer的调用结果保存、后续投影、最终退休消费**联合设计。不能只把`release_attempted`改名，也不能从当前镜像或公开debt反造实际结果。先行的正常关闭不因后继失败重新变成“OS是否关闭未知”；“已知normal close、账本/表示未完成”必须独立表达。

当前实际缺口还有三个：预算begin返回前可能已改pending但ticket尚未保存；settle返回后lease尚未保存；release中pop与counter逐步提交。因而不能只包close或release，必须覆盖begin/settle/release的实际返回边界。

## 2. 固定输入与证据级别

| 输入 | 本批使用范围 |
| --- | --- |
| r1205 candidate-01/reno三个完整Python源 | 唯一已选Python before；directory 74764B/5be5999a9c161aef72043a0d53d6fc0c066d045f3128e87acc47e0c46dd33d37，source_native 133762B/607fd5f4fb61ab9a84a43aeb041113a85c4a9ec69fab0424cf1ff560c7de0ac5，operation 164766B/7eb0a71cd20e4bf453327edfbb453cfc3b76f527cb241d1d0c686845eb24c040 |
| r1206 candidate-01/native完整11C/H | 原生close事实的未安装before，122318B；以其CANDIDATE-01.json逐源身份为准，不以本方案当源码 |
| r1202主方案及A附录 | 原五资源链、真实audit、业务/预算/first/正常与失败生命周期范围保持 |
| r1206 INDEPENDENT-PYTHON-FACT-GAPS-01.md | 已有独立调查；本轮Root复读实际接点，不冒认所有新设计已双审 |
| r1106固定selected的personal_authority_replica_budget.py | SOURCE-02.json唯一条目及actual selected/live已独立核同，均15070B/56d30c0fe125063af5361435830103b12d1e18880f75d3e7b4dddefde2fdc22d；后继引用固定source-01/candidate-full/reno路径。source_approved/execution_admitted仍False，不能据同字节称新集成已准入 |

下述D、S、O行号均指r1205三个Python before。文本设计、字节身份、源码复审均不等于编译、运行、原生验证、真实服务验收或权限证明。

## 3. 已确认的producer、错时与consumer

| 事项 | 真实原点 | 必须保留的区别 |
| --- | --- | --- |
| D begin | D1144返回，D1145才保存ticket | 原预算可能已提交而holder.ticket仍None；不得据None宣布从未进入 |
| S begin | S1400赋值RHS的begin_resources | 同一返回/赋值窗口；holder已登记，不代表ticket必已持存 |
| D settle | D1125/1127 end/fail，D1131保存lease | 同票只选一次end或fail；end失败不得再调用fail |
| S settle | S1412/1414，S1419保存lease/count | actual returned lease必须先持存，再处理returned first/peak/sync |
| D release | D662 attempted，663 sync，682 pop，683 counter，684 released | attempted不等于pop已入场；pop成功不等于counter成功 |
| S release | S634 attempted，646 pop，647 counter，648 released | 同上；close S653早返会挡住S731尚未入场的release |
| D normal close后计数/事件 | D814 actual normal事实，817/818两个counter，828 append_event；事件双视图在497/498 | 任一后继失败不重closer；两append可能只成功一笔 |
| S normal close后计数/事件 | S701 certified，707/708 known evidence，710–713四counter，724/725两append | known evidence分配可失败；role/global两个视图不可当一个事务 |
| source首次archive后的settle | S1454/1455、1465/1466、1584/1585、1595/1596、1630/1631、1644/1645、1732/1733 | 首次cache不覆盖；新增最终事实须关联原record。首次retain失败时后继settle可能未到 |
| D退休 | D879–898 | 原needs-evidence未覆盖closed但post-close不完整；不能用PHASE拒绝此状态的retain冒充修复 |
| S退休 | S817–836 | archive的六字段不是最终lease/event/counter；普通validator能拒不一致不等于失败退休保全 |

原D完整纯validator为D534–616；S被kernel捕获的pure_validate原定义S1147–1233。它们允许活的pending/lease，不是终态证明。D不独立重算全部close counters；S的global_event_order原先只有初始化和两append，没有selected O读者。新consumer必须实际补齐这些消费，不称原有联合认证已成立。

## 4. 路线裁决：不交付两个看似简单的补丁

| 路线 | 结论 |
| --- | --- |
| 在Python中依次写STARTED、调用pop、写POP_RETURNED、更新counter、写COMMITTED | 不足。调用正常返回与下一条Python写入之间仍可中断；预登记字段不提供原子receipt |
| 看到lease key缺失或current已减，就补写成功/再执行一次 | 否决。可能是外部改动或部分提交；不可倒推真实本次返回，也不可重放pop/refund |
| 失败且有holder就永久保留整scope/unit | 只有狭窄保根价值，不选为本批源码候选。漏掉success参数/first未显现的partial，又把合法业务失败也长期保留并熔断 |
| 只在ticket/native/attempted非空时保根 | 更窄但不完备；遗漏begin已改预算而ticket未保存的窗口 |
| 在原实际C调用体中接收结果后立即存入私有拥有槽，再做Python投影；最终消费者联合认可 | 选为后继实施方向。必须连同实际来源/拥有/退出与原调用域一起闭合；尚不准入写产品候选 |

保根是证据未完整时的必要行为，不是最终业务功能。不得把全量失败拒绝或永久占用作为完整I07交付。

## 5. 实际调用运输：begin、settle、release同时覆盖

### 5.1 共同返回包络

每个原registered holder在第一次预算/native调用之前，须已经有实际C constructor建立的私有receipt拥有根。它来自实际Ingress/Consumer/source bank和原operation，不从公开holder dict、别名、caller自报raw/DTO、current属性或外来owner认领。既有这些C入口尚未接通，是源码准入阻塞，不是可略去的前提。

同一C调用体应做到：本次NOT_ENTERED先锁存STARTED；用原actual callable/实参调用一次；返回非NULL时，把**实际NEW返回引用**直接move到预留拥有槽，然后只做scalar发表RETURNED。失败时先按既有actual-first合同私有保存原indicator，清完本层owned/temp后才还原。返回值验证、tuple拆解、计数转换、mirror、事件和错误表示都在实际返回根已经存在之后。

这只关闭“调用已返回、Python尚未存返回值”的窗口。原被调用Python budget函数内部若已改账后抛错或被中断，仍只是STARTED/partial；不得根据调用失败宣布原账本没变化。新增C包络不是让原Python函数自动变事务。

实际返回根需要跨本层所有可回调准备和分配；先move后发布，不在已发布对象中暂存未初始化payload。公开getattr、mutable closure、binder准备、allocator、GC和析构仍各有source/fresh和owned义务。不得假定持GIL意味着整个原Python调用或所有native callout不回调。

### 5.2 begin与settle

begin保存actual返回对象后，才验证原ticket合同及同步投影。call已经STARTED但未返回时必须保原预算、pending/leases容器及holder关联；ticket None不是NO_TICKET证明。

settle分别记录原选择END或FAIL、actual ticket、actual acquired输入及actual返回根；不通过异常fallback二次选择另一条。actual lease/count和returned first有独立拥有责任，先保存lease事实再沿原first路径处理。原acquired None→reservation1、uncertain与capacity_peak仍保留，不将None变零。

本方案不选“仅外层finally持一个Python临时返回值”代替C拥有根。也不引入外来register/adopt接口来填receipt。完整C调用ABI、原defaults/bound函数来源及>=3.11版本适配还须逐入口核同。

### 5.3 release阶段及重入规则

release采用下列逻辑状态；它们是拟议产品事实，不是诊断marker，本批不运行：

```text
NOT_ENTERED → STARTED → POP_RETURNED → COUNTER_COMMITTED → COMPLETE
```

| 状态 | 实际producer和拥有责任 | consumer允许行为 |
| --- | --- | --- |
| NOT_ENTERED | 原close成功或明确empty0是否允许释放，由独立实际原因决定 | 仅允许尚未进入的一次release；不据ownership unknown一概跳过已知正常close |
| STARTED | 原pop即将执行；actual lease/原ledger根持续持有 | 不重放pop，不reset attempted；尚不能声称lease已取出 |
| POP_RETURNED | 同一C体保存原dict.pop返回的NEW count对象，再发表phase | 保存实际count，不从缺key反造；后继counter尚未保证 |
| COUNTER_COMMITTED | 原counter实际写调用正常返回后，同C体记receipt | 仅表示这笔实际写返回，不证明Python镜像或整个计数图完整 |
| COMPLETE | 原required投影和本层清理完成，实际消费者认可对应receipt | 才可作为本项lease的完整释放输入；并不替代最终event/source/N4 |

原pop和setitem须消费actual捕获的原builtin descriptor及原实参，不能新查可替换global、当前budget方法或caller传入callable。即便descriptor入口不可替换，其间的dict watcher、错误构造、allocator和释放回调仍须逐层分析；未证明无回调时不得把整段叫raw原子区。返回后立即保存本次结果只证明返回边界，phase不证明来源。

每个counter旧值、actual算术结果与实际写返回分开保存；算术/分配可以失败。不得为了预构造一批值而未经COMPAT审查改变原各counter提交与出错顺序；不得无条件回滚原已完成的pop/计数或靠当前值补偿。

D原三调用点为760、769、833；S原两调用点为664、731。D empty0不再临时伪造normal_closed=True给release借道，后继接口应带来源固定的EMPTY_CONFIRMED_ZERO原因；实际normal close count1另一个原因。unknown_acquire/unknown_close没有实际成功证据时，reservation1绝不退款。S只在独立receipt证明normal close成立且release确为NOT_ENTERED时，才在原S653早返之前分派这一次lease，不重复closer；STARTED/partial原样拒绝重放。

## 6. Event/counter不能脱离实际消费者另建journal

### 6.1 实际producer记录什么

每个actual acquired/normal-close事件分别保有原holder来源、actual action/身份输入、生成的实际event引用、原role/position，以及每个实际append的返回阶段。D的events与event_facts，S的scope.events与global_event_order都是逐笔提交；第一笔成功、第二笔失败是合法partial记录，不回写成未发生，不盲目再append。

事件tuple/event对象可以准备失败；已有原生close事实与此前counter仍须保留。将tuple预构造只能缩小窗口，不能保证第二append。任何新receipt只有下列三个原consumer全部接入才可入候选，不以增加一个只写不读的journal充数。

计数也逐笔：D的current_resources/resource_normal_closes；S的role current/normal_closes和global current/normal_closes。保存实际使用的旧operand、实际计算结果及实际write receipt，不用投影当前数字制造expected；逐笔失败不把已经normal close的资源重新交给closer。

### 6.2 三个必须同时接入的consumer

1. **原cleanup/close重复入口**：消费实际closer与lease阶段。closer已尝试不再调用；只有明确NOT_ENTERED且有独立真实释放原因的lease可首次执行。event/counter partial不无限重试，不把已关闭事实抹成unknown native。
2. **正常/失败一致性validator**：用producer事实核Python投影。D按真实holder独立重算acquire/close/current，逐holder核实际acquired与normal_closed两事件及identity、sequence、event/fact身份。S按真实role/holder核本地事件，再消费global_event_order逐项的实际event身份、role、各role游标及总覆盖；还必须核独立canonical producer的global_commit_ordinal，否则跨role互换可漏检。attempt occurrence、local sequence与global commit分别持有，完整字段/真实提交关系见补充R702；wrapper入口序号或当前列表均不能造expected。普通业务成功仍须原Q/read/identity/source图等全部原合同。
3. **retirement与最终archive**：逐holder认可最终记录或明确pending保根，不能只跑正常validator便pop。所有必要事实/关联都成立后才进入原retired/sync/pop后缀；任何准备/核同失败都在第一笔detach之前。

S portable_incomplete是单独的失败清理合同：actual normal close和known_close_evidence可成立，但identity缺失时原实现不会产生普通normal_closed portable事件。不可机械要求业务成功的event pair/source_graph.final_complete，也不可把它说成OS关闭未知。若新final schema尚不支持此合同，就明确“表示待支持、原根保留”，不能伪造事件。

## 7. 首次archive与最终continuation必须分别拥有

r1205首次cache及其private record原样保留，不覆盖或用新版schema冒充原历史。另设每实际holder的最终continuation关联，必须从原actual holder/首次record构造，不从公开debt/current镜像补填；strong actual key关联防id复用。

首次错误另有寿命接线：O966会在原retire_first中pop独立originals，O932–934之后peek可能为None。原driver仍可正常交付这次actual first，问题是pending/final不能据镜像恢复独立expected。后继须在原接受first的actual producer、任何mirror同步之前建立delivery-first/continuation-first两笔独立拥有责任，continuation实际根必须在原pop之前持续成立；不能只在remember正常return后复制，也不能以普通map退休清continuation。字段/producer/交付与清理合同见补充R701，完整N4/异常隐含图和原提交顺序仍待。

最终记录至少要区分：begin真实入场/返回；settle END或FAIL的入场/实际lease与returned first；native acquisition及unique close实际结果；逐lease/counter/event阶段；原first-cache身份；最终是完整关闭、明确empty0、可支持的portable-incomplete，还是partial/pending。每一PyObject边逐项有acquire/move/partial/traverse/clear/dealloc责任，纯scalar不混计成引用。

archive生产在任何原registry detach之前；记录构造/插入/封闭可以失败，失败就保原根。旧首次public-put history只证明历史put返回后的本层位，不证明最终记录、当前公开可见性或持久化。不得以公开条目尚在授权退休，也不以public条目被删撤回私有真实历史。

终态分类：

| 分类 | 退休前必须成立 | 不得推导 |
| --- | --- | --- |
| NO_CALL | 实际producer的NOT_ENTERED及完整预算/registry覆盖；不能只读ticket None | 不能据未保存ticket/attempted False排除begin partial |
| CONFIRMED_EMPTY_ZERO | 原真实no_acquire、实际settle lease/count exact0、一次release完整、原leases中包括零lease都已退出 | current0不代表零lease已pop |
| NORMAL_CLOSED_ONE | 实际唯一normal close、count1 lease完整、event/counter投影全部核同，最终archive成立 | first存在或success=False不否认资源确已正常清理 |
| PORTABLE_INCOMPLETE | 按独立失败schema保存known close/lease及不完整身份事实 | 不伪造业务成功的portable事件/source complete |
| STARTED/PARTIAL/UNKNOWN | 现有支持域不能形成足够最终证据时保原scope/unit/holder根，原first不覆盖 | 不重closer、pop、refund或新票，不把保根当完成 |

共同终态门还须核原ledger容器身份/type、pending与leases完整覆盖（包括0 lease）、current exact-int0、全holder覆盖、callback/terminal已退出。原pure validator仅作其中一部分；不能调用会因已有first直接拒绝的assert_settled成功入口来判普通失败是否可退休。

保根拒绝先锁存原预配gate；若actual first存在就原样抛，无first才构造UNSETTLED。gate/peek/error本身失败仍在pop之前。不能新建覆盖较早first的异常链/frames/locals/notes。即使driver后来移除普通operation map，kernel的实际scope/unit根也不得被该map退休冒认已释放。

## 8. 拥有图、成本与未闭条件

pending时保原scope/unit会强持holder、registration、budget、owner/Task、callback、first及type/module间接图；可能长期阻塞新请求并增长内存。它不符合轻量最终归档目标，没有reset/无限重试/无证据逐holder detach。

最终archive目标是已证明足够的最小事实图，不等于只列几个payload名就已轻量：首次key→type/module、实际返回对象、lease、event、异常、receipt type及source bank都须列真实边，N4/SCC/m_clear/m_free不能省。若private返回仍可能持有unit/Task/任意对象，必须在封闭前按真实拥有链处理，不能靠注释保证没有。

以下是**源码准入的实质待项**，不是用户许可待答：

- D01：实际Ingress/Consumer/source bank→constructor→每holder receipt的全接线和全caller，没有外来setter/adopt。
- D02：begin/END/FAIL/pop/setitem/append的完整actual callable/binder/operand来源、可回调段、fresh点及first/NULL/partial退出。
- D03：receipt与最终archive每字段/隐含type/预算的完整拥有目录及N4/SCC，包含partial、初始化前窗口及R701跨原retire_first的独立first occurrence。
- D04：实际counter算术/写与两append的逐笔返回运输、原错误/提交顺序及COMPAT；含R702独立global ordinal原点和真正提交关系，callback和dict watcher不能默认为无。
- D05：D/S正常、empty0、portable-incomplete、failed-clean及pending退休矩阵；完整消费global事件，而不是只新增写者。
- D06：固定budget身份关联子项已关闭，详见BUDGET-IDENTITY-01.json与独立复核01；原完整selected源码审批、native集成和执行不因此通过。后继不得用新live正文静默替换固定before。
- D07：full API/CPython>=3.11多版本布局、SDK/CRT/构建/加载与预算；r1206的3.12.10条件不缩减产品支持域。
- D08：原五类实际资源及source-role/unit/operation多层清理的完整图；pre-transfer HANDLE前件不能当全fd/find/local/stream支持。

本方案不声称完整准入已闭合：D01–D05/D07–D08及R701/R702实际接线仍待，D06只关闭身份关联子项。双审只能认可本设计子方向及事实边界；尚不允许据此直接生成完整产品candidate。

## 9. 后续文件级实施顺序和验证门

| 次序 | 文件/责任 | 可交付物与准入 |
| --- | --- | --- |
| P1 | 固定budget源、D/S/O before与C bank | 已核budget固定身份及14源495610B；完整调用/返回/退出矩阵仍待接线。不读现场debt、不运行collector |
| P2 | 实际C constructor/receipt与source bank/入口组合 | 完整拥有目录、完整C调用包络设计、Root+同一代理事前双审；先闭D01–D03，不能用stub冒充 |
| P3 | directory_native/source_native与原budget消费者 | begin/settle/release完整接线，empty0/normal理由分开，错误/部分提交一项一项核对 |
| P4 | D/S event/counter、原validator、O真实driver | producer/validator/retirement同组合审；global_event_order有真实读者，失败清理保合法失败域 |
| P5 | private首次archive关联、final archive、first与retirement | 包括R701两种first寿命、R702独立全序consumer；全字段/root/partial/N4/预算和失败矩阵闭合后才candidate；旧cache不改，禁止单文件先安装 |
| P6 | 全候选源码 | 先全文源码双审、唯一literal正逆字节与完整差异封存。未来是否能编译/运行按原授权门另判，本轮不执行 |

必须核的失败边界包括：begin内部部分提交但未返回；begin返回但投影未存；settle返回后first/sync失败；pop入前sync失败；pop返回后counter失败；counter返回后COMPLETE未发；第一append成功第二失败；normal close后known evidence分配失败；首次archive后settle产生新lease；portable身份不完整；某holder完整而其它pending；普通业务失败但全部资源正常清理。

这些是待验证矩阵，不是已跑测试。本轮只文字、复制、bytes/hash和read-only diff；无产品/候选、AST/compile/import、测试/collection、collector、native、QA或诊断执行。

## 10. 本批交付与不变范围

本批交付整体协议设计、Root实际event/counter/retirement调查和同一代理lease/budget调查、独立设计复核及状态/身份封存。没有r1207产品候选；没有产品源码修复、安装、构建或运行结论。r1205/r1206仍各保持自己的有限源码PASS，不因本方案变成集成可用。

不重置原30calls/2children/1990365B或已过截止；r920/r964单次诊断已耗、r967不执行。不重放旧QA/未变失败/r1173修订04及21未跑，不增profiler/marker/native微诊断。正常库、live源码、服务、dist、权限/settings和Git不改，保既有约960份未提交变更，只复用r924同一只读代理；没有新用户确认问题。

仍缺I02–I10、H01/B001+B002、H02持久链、H03/H05/H04/H06、分享撤销、P06历史缺证、条件T15及生产云。P06用户无备份、A已选，两项不再询问。live仍r1171+r1174，历史State/request发布根因未确认，未读现场debt表。Goal不标complete/blocked；非只剩验收。
