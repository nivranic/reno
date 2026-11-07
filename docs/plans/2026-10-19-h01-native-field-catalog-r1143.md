# r1143：Consumer五字段与共同拥有目录

r1143选择Consumer五个owning payload：module_lifetime、reply_type_lifetime、实际A/J/Product；创建端六guard后五move，ConsumerType与Factory逐调用module_guard另计。accessor后先按state指针/indicator四组合分类，才取字段；GC_New NULL与成功Init后的partial分账。clear先退休并全部detach三source，再Product→J→A释放，不退休共享source；两lifetime至dealloc。共同目录更新为19 literal payload+5 state语义边=24已知schema，另4类实例type；旧20条stage原样保留。新增module环、全state/A/J/Product字段、一般NULL/build/完整N4仍未闭；B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

旧目录10完整设计136128B/16区间、tagged methodobject.c17966B/4区间分别核对；正式目录补L的RETIRING && count==0条件，OPEN归零不清。五字段名单完整只指本实例payload角色，不证明实际C布局、来源接线、全部出口、SCC breaker或预算；隐含type、call-local/guard、返回/异常责任分层，不按identity去重。Consumer分配期间source退休须fresh拒绝，清三source不保证新module环可回收。

本批只作静态文档/read/hash/AST/diff，无新下载或产品candidate/compile/import/运行/native build/load/模型/QA/诊断；apply0/历史11、源码缺口关闭0、产品pass0。159源码/保护56同18缺/原限额与禁项保持。完整Goal仍有工程，非只剩验收。

# ADR-r1143：Consumer五字段与共同拥有目录

状态：有限字段/顺序子设计待正式双审，无产品源码或执行准入。保留原问题、STAGED目录、提议与口头预审原字节；旧20条schema只描述其原时点。本ADR选择Consumer五payload与分层清理，细化r1139 source并新增Consumer自己的module lifetime；不宣布A/J/Product内部布局、实际producer来源或完整N4已闭。

## 1. 选择实际字段，不引入一个未展开的“source_record”替身

选择将 actual Consumer 的本方 PyObject owning payload 固定为以下五项，实际 C 名称/布局仍待完整源码阶段：

| 拟定字段角色 | 实际来源与拥有 | 退休/clear | 最终释放 |
| --- | --- | --- | --- |
| module_lifetime | 本次原Factory实际持有的同一native module；独立新取一笔，不move掉r1140逐调用module_guard | 保留，不因source或Consumer退休切断 | 本实例最终dealloc才detach/release |
| reply_type_lifetime | r1139创建端原reply-type guard成功move | 保留，不由tp_clear切断 | 最终dealloc才detach/release |
| origin_attempt | 这次原创建路线验证并运输的实际A；独立owning引用 | 可detach | 本字段或其已detach temp只清一次 |
| invocation | 这次原创建路线实际建立并运输的J；独立owning引用 | 可detach | 同上 |
| product | 本次原maker call的actual Product，经本方原producer提取验证；独立owning引用 | 可detach | 同上 |

实例隐含actual ConsumerType是第六条独立owning责任，traverse不可漏。phase和固定role是C scalar，不是Python对象字段；没有dict、weakref、公开实例化或setter。role只由本方原创建控制流给出，不由外部subject登记或变更expected。

origin_attempt/J/Product之间即使也互相持有，五项仍按每次实际取得分别计账，不按identity或传递可达去重。A与Product所含catalog来源A可能不是同一个attempt；不拿catalog D/A冒认当前模块原A。这里的三项必须是实际原producer/return运输值，不是对外部传入A/J/Product的自洽快照登记；完整B001来源接线仍open。

原metadata、source bank、owner、G/B/context等数据继续属于其实际A/J/Product或原producer，不复制一份“最后source”到Consumer。Consumer每次消费仍沿独立原来源及实际operand共同核。A/J/Product必须在完整方案中明确哪些真实字段实际拥有这些原数据、取得时点和持续区间；本表不会把缺失内部字段当作已经存在，也不授权从current alias补pin。

## 2. 创建端的六笔准备guard与move

前提是原Factory receiver及r1141 module_lifetime连续持根。先独立取得r1140逐调用module_guard，再进入可回调state accessor，返回后立即按state指针/raised indicator四组合分类；只有非NULL且无error才访问state及取得下述六guard。其余三格先按r1140失败/准备拒绝合同结账，不解引用state。新增module_lifetime的那笔引用不能偷走或提前结清逐调用guard。

在原phase及actual ConsumerType/ReplyType与A/J/Product关联已通过本次原来源认可的匹配raw段，依序取得consumer-type guard、reply-type guard、consumer-module guard、A guard、J guard、Product guard，合六笔独立准备引用。源字段或实际operand到相应NewRef之间不得插入allocator/getter/DECREF/等待或释放GIL；若认可本身需要callout，必须先完成准备及持有后重新fresh，不能以本段文字跳过它。真实参数缓冲、原C入口及每项borrow→acquire前置root仍须在完整接线给证据。

六guard持续跨Consumer GC_New/Init。GC_New返回NULL时没有实例，六笔仍归各自动槽，可能last；沿本层actual preparation first完成全部清理后还原，不伪装工具已进入。GC_New成功且正确Init后发生的失败属于partial，不套无实例出口。成功Init且untracked后，五payload先置NULL，phase置INIT；按module_lifetime、reply_type_lifetime、origin_attempt、invocation、product顺序将对应五笔move入字段，每个自动槽立即置NULL。instance已有type责任成立后，consumer-type guard才可在匹配nonlast包络下结清；state另有的ConsumerType引用仍是另一笔。

所有可回调分配/准备/extra释放完成后，重新认可原module/state阶段、三source关联、字段与实际producer，不重采expected；通过才设ACTIVE、Track并移交本次实际Consumer。未move由自动槽拥有，已move由partial实例拥有，各清一次。Track前完整payload有效；partial未完成不公开、不建立外部反向边。若source在分配期间退休，不能把持有旧pointer当fresh通过。

## 3. 活动调用和清理：不退休仍被其它对象使用的A/J/Product

actual Consumer由本次真实operand持续持有；module_lifetime与reply_type_lifetime均不被source退休或tp_clear切断。所需逐调用module/type guard分别从本方实际字段取得，不能从ConsumerType.ht_module或shared state已清的ReplyType字段补根。该引用和布局包络仍须匹配实际build，不把self identity当caller证书。

消费正常source时，先raw核Consumer ACTIVE、三source非NULL及本次关联，再为跨callout需要的源取得单独call-local引用。callback返回后必须重核Consumer phase/实际字段关联与来源、actual selected/metadata；call-local根只保证对象寿命，不冻结source或current metadata。每个本层extra的结清独立于五个实例字段。

Consumer.tp_clear先在raw段转RETIRING，将product、invocation、origin_attempt三字段全部detach到本方owned temp、字段置NULL并转CLEARED，再允许任意释放。实际字段取得顺序为A→J→Product，故本段三个temp按Product→J→A释放；source实际共享时，清本Consumer的拥有边即可，**不顺便退休A/J/Product、不清它们内部根、不复活自己**。module_lifetime/reply_type_lifetime保留，重复clear只处理仍实际拥有的source项。

三cell协议仍可先从Consumer不可退休ReplyType字段取得逐调用type guard、准备私有空Reply，再根据Consumer/source状态决定getter0的DENIED；旧try外ACCEPT拒绝。CLEARED并不重授信旧source。原getter失败、late MATCHER与success实际return move仍按r1136/r1137，不把DENIED当empty cell或把新分配失败当getter异常。

## 4. traverse与dealloc的完整实例payload顺序

traverse访问五payload中每个实际非NULL值及actual heap type，逐边visit、不按identity去重、不分配、不改phase。它展示边，不能取代整图breaker证明。

dealloc先UnTrack，置不可消费状态，将五字段全部detach到私有temp、实例全NULL，并保存已有heap type责任；所有detach先于release。若source尚未被clear，先结清Product→J→A，再结清ReplyType lifetime、module lifetime，随后以匹配固定GC_Del释放实例，最后结清saved type。free后不读self；这里明确不使用current tp_free动态认领原路径。已有actual first时按r1135跨全部责任持根，最后还原后仅scalar返回；一般NULL入口、free/allocator和type析构的indicator合同仍未闭。

新增直接边Consumer→module可能参与module/state/G→Consumer→module等环。原state、模块dict和三source图的真实可清边必须逐一进入共同SCC表；不能仅清本Consumer三个source就宣布所有新module环有breaker，也不能强清lifetime来赶走外部真实持有者。未公开partial、Track后外部保留、重复clear、直接m_free和源退休期间的活动调用都仍需同版退出矩阵。

## 5. 增量和尚未完成的组合

相对r1139：原先只有reply_type_lifetime已具名、其它source未展开；本提议固定三source owning字段并增加一个module_lifetime owning字段。五payload+一实例type是每个Consumer的schema，不是整个native峰值。创建端六guard中五笔move进payload，consumer-type guard独立退出；原Factory逐调用module_guard成本保留。新增长期module指针/引用必须计入字节/同时存活/重入预算，不报“只多一笔所以已满足control1MiB”。

尚未完成：上述三source的实际producer/参数/metadata认证，A/J/Product及source bank完整字段/活动lease/clear/free，module state全表和每个SCC breaker，ConsumerType/ReplyType/Factory各partial与一般NULL，原工具完整调用和三cell分类，D1/D2/P0/38context/CORE/IMPORT/owner14、COMPAT及>=3.11/ABI/build/原预算。B001/B002 open2，四项完整批准False；无产品源码、compile/import/运行/native/模型/QA/诊断或真实库操作。

## 6. 共同目录的版本与层次

旧STAGED-FIELD-CATALOG-01保留20条已知schema原计数。采用本ADR字段选择后的FIELD-OWNERSHIP-01另记：L10+Factory1+Reply3+Consumer5=19条literal payload，另5条state语义边，共24条已知显式schema，4类隐含heap type。不是运行slot/引用次数、物理C member、对象总数或heap界；完整state及source家族未穷尽。

L的retirement条件明确为RETIRING && active_count==0：lease-local先结清、raw减count、满足该条件才全detach十字段并逆序release、最后self；OPEN归零不清。Reply的contents→cell→consumer是依赖释放次序。r1141 Factory.module_lifetime替换旧无payload和调用期ht_module依赖；其它拟议NewEx与L binding未解决，methodobject.c四区间仅给同版已有C类型行为。

## 7. 有限出口矩阵与禁止外推

| 出口 | 拥有责任及本层动作 | 未闭证明 |
| --- | --- | --- |
| accessor四组合中的三失败格 | 不访问state；保实际错误或实际构造的准备拒绝；逐调用module_guard仍独立 | 错误构造、一般NULL及适配 |
| 六guard已取而GC_New返回NULL | 无实例；六自动槽各清一次，maylast/actual-first；原逐调用guard另清 | allocator/build及各真实source前置根 |
| Init成功、部分move后失败 | 未move仍自动拥有，已move只归partial；partial不公开，全部detach后释放 | Init、NULL入口、各析构及完整图 |
| 完整payload、最终fresh拒绝 | 不发布/授信；清partial及extra；source退休不被旧pointer掩盖 | phase/来源接线和COMPAT |
| ACTIVE/Track后仍外部持有 | 不强清lifetime；各call-local根独立，callback后重核 | caller、Track/build和新SCC |
| clear及重复clear | 不可消费，三source全detach后Product/J/A释放；不退休共享对象，两lifetime保留 | 共同SCC及NULL/free合同 |
| 最终dealloc | 全detach先于release；三source、ReplyType、module、GC_Del、saved type | 一般NULL/allocator/type及first包络 |

表中责任是有限设计选择，不把未闭条件记作通过；module m_clear和未先clear的m_free须独立覆盖完整state。旧历史红、原caller和finally、>=3.11/预算、B001/B002及四项False均保留。

# 下一步：完整state与source字段、binding和SCC

先把24条已知schema分成对象payload与state语义角色，补未列的真实state/T0多根字段；不把语义角色直接变成C member、指针数或运行对象数。Consumer五字段是本批选择，A/J/Product及source bank内部仍须逐producer取得、持有、detach、release、partial落表。

固定其余拟议PyCFunction_NewEx和L方法的实际binding路线；完整保留r1112实际入口责任，不以换掉一个Factory就宣布其余m_self/m_module已解决。逐项区分descriptor、bound method、实例字段、type/module边及外部真实持有；选定后再列共同SCC和具名breaker。

Consumer补未move/已move、Track前失败、外部保留、重复clear、直接m_free、source退休与活动调用退出矩阵；module/state真实反向边未穷尽前，不声称清三source已覆盖新增Consumer→module环。一般NULL入口、actual-first/E2、free/type/allocator、>=3.11/layout/build、预算仍待。

继续原caller与参数、P0/38context/CORE/IMPORT/owner14、D1/D2、COMPAT及原Task9A9B/111/94/r919/真实Store/P06/条件T15/生产云范围。完整设计双审前不生成产品候选，源码准入前不运行产品/native/QA/模型或诊断；同一代理静态复审，不新增代理。所有单次诊断和失败下载保持已耗/终态，不重放、不再索权。保留未知dirty，不改正常库、服务/dist、权限/settings，不commit/push/reset/clean。
