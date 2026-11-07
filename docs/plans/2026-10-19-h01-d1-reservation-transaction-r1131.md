# r1131：空 chunk 事务、容量与异常合同

r1131明确scalar reservation的逐字段所有权与precommit完整恢复后free，条件x64算式保严格容量并列原signed域；纠正原committed Pop在free后才恢复limit。PREP_NULL选清本层后一次NoMemory的新失败合同，actual call仍0；3.12单异常与3.11三slot运输分列，不把DECREF当合法first覆盖反例。free/cleanup indicator、实际N/ABI/预算/D2与共同N4仍待，B001/B002 open2，完整design/source/execution均False；源码仍r1106 SOURCE02完整159，Goal active未完。

三份官方源码单次下载成功，无重试或执行；3.11.13仅静态参考，不冒认全部>=3.11支持。NoMemory的freelist/last-resort与SetRaised旧pending释放已核；普通__del__的save/restore也列明，未证明arena free同等合同。不用无限drain、隐藏owned引用或重建callee first闭环。

159源/保护56同18缺保持；本批产品candidate/compile/import/运行、native build/load、模型/QA/诊断0，apply0/历史11、产品pass0。原限额和禁项保持，完整目标非只剩验收。

# r1131：空 stack chunk 的有限事务、容量与异常合同

父r1130；产品仍r1106 SOURCE02完整159文件、7,956,657 B，未安装。本批只确定空chunk事务和条件容量证明，选择PREP_NULL的新增失败合同，并明确尚缺的异常清理证明。没有产品候选、future K、产品compile/import/运行、native build/load、模型、QA或诊断。B001/B002 open2；完整design/source/execution/Goal均False。

## 1. 容量算式与证明域

原pystate.c2951–2988以16KiB起步、倍增，满足`A >= W*(N+1000)`；HasStackSpace使用严格`N < limit-top`。N为原实际K的co_framesize槽数，不是字节数，未来K尚未生成或核实。本批没有读取tstate、执行sizeof/offsetof或编译ABI探针。

只在匹配的3.12.10端口、W=sizeof(PyObject*)=8、H=offsetof(_PyStackChunk,data)=24、32位signed int、足够size_t/ptrdiff_t域等条件成立时：nonroot剩余槽为`A/W-3`，root按原data[1]跳一槽后为`A/W-4`。因为A/W≥N+1000，分别至少N+997、N+996，均严格大于N。W/H目前是结构布局的条件值，不能当成实测ABI。

checked计算义务逐项为：N非负且原K可表示；检查N+1000、W乘法、header/skip、size_t与ptrdiff_t表示范围；每次倍增前检查`A <= INT_MAX//2`；不在C中先做溢出再检查。原`(int)sizeof(PyObject*)*(size+1000)`及signed倍增也有定义域，不能掩盖。令Q为不超过INT_MAX的最大`16384*2^k`，则T=W*(N+1000)≤Q时，checked计算与原定义的size序列一致。条件x64/32位int下Q=1,073,741,824，N≤134,216,728；本批用整数区间覆盖每个倍增桶并核首末边界，而非跑原runtime。

这不是新增任意业务帧上限。域外、未来K及其他ABI不作认证拒绝来冒充完整支持；其原调用路线仍待设计，完整COMPAT不因此关闭。原容量不足但allocation成功的合法调用必须保留，不能回到capacity-only拒绝。capacity proof也不证明原function/args、metadata或caller来源。

## 2. 本次scalar记录与逐状态所有权

记录只存在本次原C调用栈：state、owned_raw、raw_bytes、same_tstate、fresh_old_chunk/top/limit、必要的fresh_old_chunk_top和是否保存标志。它们不是PyObject、公开builder、全局last reservation或runtime journal。原六组T0不新增allocator；原runtime-global alloc/ctx与free/ctx读取/race照r1130，不能从首次快照建立expected。

| 状态 | 必须成立 | 允许的下一步 |
| --- | --- | --- |
| NONE | owned_raw=NULL；未改runtime字段 | 有空间走原final/call；不足单次PREP |
| PRIVATE | 独占本次实际raw返回及exact bytes；未接入runtime | 回调返回后fresh取得same tstate的当前字段；或free本次buffer |
| ATTACHED | 保有完整rollback字段；new为空，无frame/locals owned对象 | 无回调final；失败先完整detach；成功转交 |
| DETACHED | owning已清空；旧字段已完整恢复；actual ptr/bytes只在本次free实参scalar | 单次原runtime free；返回后不访问buffer或复写旧状态 |
| COMMITTED | owning已清空；chunk所有权交解释器 | 立即原actual function/args的standard vectorcall；本方不再看/free chunk |

allocation可以回调，分配前的old字段不能用于attach/rollback。成功返回后先接住actual指针，再fresh保存同一actual tstate的当前chunk/top/limit及旧chunk.top；header.previous指向此时chunk。不能缓存上次owner/tools/reservation，不扩大metadata expected，也不重试allocation。

ATTACHED失败的最终raw段：先把待释放actual ptr/bytes留在本次scalar、清owning并标DETACHED；恢复必要旧chunk.top和完整chunk/top/limit；这些赋值中不得调用/等待/分配/DECREF。全部恢复后才调用可能回调的原runtime free。free返回后不得再次restore分配前状态，或读取已释放buffer；回调可能合法改变其自身状态，不能用旧快照覆盖。PRIVATE失败没有runtime字段可回滚。实际helper源码、同tstate保持及free合法性仍待证明。

COMMITTED在紧邻原实际调用前转交；原PyObject_Vectorcall与独立standard slot、final到实际frame push无回调等仍需完整future源码证明。不能在返回后以“确认没用”取回所有权。root的data[1] skip使其由解释器长期保留；nonroot由原Pop释放。

## 3. 原Pop的精确边界

原pystate.c2995–3003依次恢复top、chunk，调用VirtualFree，**free返回后才恢复limit**。因此原committed Pop没有在free回调前恢复完整三字段；r1130的本方precommit rollback义务不能延伸为原Pop的保证，也不能为“修复”它修改全局runtime。

Arena文档未给所需的Python再入/异常indicator完整合同。该顺序本身是静态事实；不据此宣称某任意再入必合法、可利用或已执行。原free再入安全性、原race及同runtime ABI继续是未闭条件。未执行native/allocator/API调用。

## 4. 三类退出与实际错误

| 域 | actual vectorcall | 错误来源与本批决定 | 尚缺 |
| --- | --- | --- | --- |
| PREP_NULL | 0次，原call未进入 | 选择新增准备失败合同：逐项清本层实际owned引用，最后一次PyErr_NoMemory，随后只scalar返回NULL | cleanup/free合同及完整错误/观察兼容 |
| PRIVATE/ATTACHED_REFUSAL | 0次 | ATTACHED先完整restore/detach，再释放；使用真实已选拒绝错误，不能替换成OOM | 拒绝错误的取得/持有/覆盖/退出与N4 |
| COMMITTED_ERROR | 1次原actual call尝试 | 运输该次实际callee错误；chunk归runtime，本方不再free | 各cleanup indicator合同、原finally覆盖及共同N4 |
| COMMITTED_SUCCESS | 1次原actual call尝试 | 保持实际return强根，按实际acquire逆序清本层引用 | cleanup不得留错误；result与原观察时序/预算 |

PREP_NULL不能失败后再走原vectorcall重试allocation。这里的最后NoMemory是新增失败的实际产物，不保原callee可能产生的OOM identity、traceback、call-attempt或时间。也不以延迟重建覆盖已经真实发生的callee first。若更早已有必须保留的实际错误，则归原first运输义务，不能一律套PREP_NULL。

3.12.10的_NoMemory通过get_memory_error(0)：freelist空时NewRef本interpreter的last_resort_memory_error，否则设置空args、取出实例、_Py_NewReference及GC_TRACK；不是总new异常，也不是原first缓存。随后SetRaised会DECREF旧pending。文档的NoMemory简写与save/restore示例不证明普遍无回调，移到末尾也不能自动建立terminal无回调保证。若清理结束仍有pending，须解释该实际对象的最终引用及覆盖合同；本批不声称已闭。

## 5. 异常运输必须分版本

3.12.10：GetRaised取走actual E并清indicator，不做DECREF；SetRaised先安装E，再DECREF old。因此只有逐cleanup已证明保indicator为空，末次Set面对old=NULL时，该次恢复才不释放old。恢复之后不得再做本方owned引用清理。必须保本次实际错误，不能换到new expected error。

3.12 Fetch会另持type/tb；Restore即使已经normalized，仍会回写E.traceback、DECREF old tb、SetRaised及DECREF type；未normalized还可调用构造器。若cleanup通过正常接口改同一E的traceback，Fetch/Restore会恢复保存的tb，Get/SetRaised不会自行还原该快照，二者不能泛称等价或自动保原错误全部状态。

3.11.13作为有限静态参考：Fetch把三个curexc owned字段移出并清零；Restore先安装保存的type/value/tb，再DECREF旧三项。保存的合法tb且旧indicator全空时，该具体分支无旧引用释放；不主动Normalize保存的可能未normalized triple。Get/SetRaised是3.12新增，不可用于声称3.11已支持；3.11完整异常creator、ABI及全部>=3.11版本仍未核。

“存在DECREF”只证明不能普遍称无回调，不证明合法Python必能覆盖first。原slot_tp_finalize9071–9092保存/恢复异常，失败WriteUnraisable；releasebuffer_call_python9251–9306也保存/恢复并assert无pending。因此普通__del__抛错不是已成立的first覆盖反例。arena free尚无同等已核indicator合同，不能由void返回或PyMem域条款推定。

若cleanup实际留下E2，把E2 Get出来虽使Set(Efirst)面对NULL，E2的owned引用仍要释放；放Set之后再开窗口，放之前仍要证明该释放。不能无限drain、泄漏/quarantine、隐藏容器或“保存first”一句话闭环。这里明确未证条件，不编造合法执行轨迹。

## 6. 有限N4及未闭项目

每次实际Python acquire占独立静态slot：同对象取得两次也有两项ownership；按实际取得逆序释放，不按identity去重。raw buffer在真实acquire点入该顺序；ATTACHED先detach再在其释放位置free，COMMITTED后从本方ownership表移除。空chunk未装入frame/locals引用，不对data[]虚构PyObject DECREF。异常运输slot（3.12一项、3.11三项）、实际return及原callee/args/context source根都须入同版slot表。原17lease/1449slot/406尾/五S002保留，不以本scalar表冒充全部N4。

空chunk PREP会新增瞬时/长期root保留成本；control1MiB是控制数据累计实读限额，不能当native heap上限。实际N、chunk峰值/长期保留、全slot、free回调/indicator、原finally及预算的共同证明仍待。正常绑定以外kwargs/default错误、D2、六工具逐叶消费、owner、seed/Product/carrier、38处context/foreign、CORE及P0/import都须同版接线，不以该条件容量证明替代。

完整design两审前不生成产品候选；源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。不改正常库/服务-dist/ACLsettings/平台/Git；禁profiling、真实frame/locals诊断、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。未来runtime端口设计不产生执行授权。r920/r964已耗不重放；r967不执行不重问；P06历史0/7且无材料，不再索要。

case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持。apply本批0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：具体清理合同与完整调用组合

父r1131，产品仍r1106 SOURCE02完整159。条件事务/容量证明不作完整ABI或D1批准，不回退到capacity-only拒绝。

1. 为原runtime free及本层每个实际cleanup列出indicator/再入/引用合同；分PREP_NULL、refusal、actual callee error和success。仅据具体原文与原合同收敛，普通__del__抛错不作first覆盖反例；不得用无限drain、泄漏、隔离藏引用或重建原first。
2. 确定匹配runtime/tstate/完整构建ABI、未来实际K与N；核域外原路线、最终门到frame push、kwargs/default错误及D2。root长期保留/峰值与每个slot进入共同N4/预算。未核tag的pymem下载不重跑，3.11.13错误参考不代全版本ABI。
3. 汇合六工具/owner、seed/Product/carrier、context/foreign、CORE及P0/import与全部调用方；完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

正常库/服务/权限/平台/Git不操作；r920/r964耗不重放、r967不执行不重问、P06历史0/7不再索材料。B001/B002 open2，Goal active，非只剩验收。
