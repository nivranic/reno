# r1132：clear-before-callout与逐slot释放证明

r1132以tagged refcounting与继承object.h确认释放可运行Python/释放GIL，选owning先脱离后释放及逐acquire的原强根覆盖nonlast/具体析构合同两类证明；Py_CLEAR不进入final raw段。异常P(k)与有限E2处置只给条件推导，不把缺少通用合同当合法overwrite反例。完整slot/root持续区间、free/indicator policy、ABI/D2/N4仍待；B001/B002 open2，完整design/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

本批新tagged下载1成功2终止（RemoteDisconnected/TimeoutError），失败不重跑。较新/live官方web不回填3.12.10；GitHub未取得完整正文，Raw链接工具受限未绕过。继承官方原文与新tagged原文分列，不作通用native析构保indicator已证。

159源/保护56同18缺保持，产品candidate/compile/import/运行、native build/load、模型/QA/诊断0，apply0/历史11、产品pass0。原限额和禁项保持，完整目标非只剩验收。

# r1132：释放可回调、owning先清空与异常运输的证明域

父r1131；产品仍r1106 SOURCE02完整159文件、7,956,657 B，未安装。本批选择逐acquire的两类释放证明和clear-before-callout合同，不能把“有DECREF”直接变成错误覆盖反例，也不能把Py_CLEAR视为无回调。所有新增文件均是资料/静态工具，没有产品候选、future K、产品compile/import/运行、native build/load、模型/QA/诊断。B001/B002 open2；完整design/source/execution/Goal均False。

## 1. 新证据及其边界

tagged v3.12.10 refcounting.rst全文6,947 B已单次取得。111–140明确释放strong ref，末次引用进入type deallocator；该过程可运行任意Python代码，文中所述代码的异常不传播，而且可访问所有Python globals，因此共享对象必须在Py_DECREF之前一致。153–162的Py_CLEAR和185–203的Py_SETREF只把owning目标先清空/改写，再释放旧值。

继承的tagged Include/object.h712–758进一步明确：析构/weakref回调可能释放GIL、运行其他线程、再入self方法或触发GC。760–781给出两种Py_CLEAR宏，均先改owning lvalue再Py_DECREF，并处理single evaluation与strict aliasing。不能因使用“safe”宏就把操作算进final raw段；safe只解决该持有指针的先后关系。

同一头的664–708显示Py_DECREF有limited/debug/non-limited不同分支。只有已匹配的非limited、非Py_REF_DEBUG路径，且对象有效、拥有本次待释放strong ref、无竞态移走另一个持续强根时，nonlast分支可由源码推出不进入_Py_Dealloc；不把未读的外部函数实现、统计宏或其它build自动叫无回调。项目>=3.11支持不收窄，未核分支仍待。

tagged typeobj.rst单次下载RemoteDisconnected，Objects/object.c单次下载TimeoutError，均终止、不重跑，也没有把缺失文件写成成功。官方web另查结果单列：当前3.12页面声称3.12.15，所读tp_finalize有“不改current exception”指导；较新3.15搜索有通用tp_dealloc保状态条款，不能回填3.12.10。GitHub v3.12.10页面未取得完整源正文，Raw链接被工具限制，未绕过。所有web观察不是已冻结的完整tagged源；不能由检索未命中证明旧版不存在合同。

## 2. 采用的本层清理顺序

每次实际acquire独立登记静态slot。同一对象获得两次仍是两项ownership，不按id去重；不用当前Py_REFCNT或“看上去还有别处持有”补来源证明。tagged refcounting.rst18–23还明确数值并不总反映实际强根数量，immortal对象尤其如此。

一次释放的合同是：取得该slot实际owned旧指针的本地临时值；先清本方owning lvalue/安装已经准备好的替代值，使本方可见状态一致；再按实际取得的逆序对旧值做一次释放。临时值是本次owned引用的运输，不是新acquire；释放后不得重读旧对象。若共享record/lease/list仍宣称持有该值，必须先完成其独立的detach/状态迁移；只对私有local做Py_CLEAR不能修复别处的悬挂状态。

该规则适用于本方新增ownership。原body、原单root锁、foreign锁、finally覆盖和原业务effect的先后保持；本批不擅自把原17lease/1449slot/406尾重新排序。所有可能last的Py_DECREF/Py_CLEAR/SETREF、arena free、异常恢复中的DECREF和其它回调都属于可再入清理区，绝不放进final raw认可与原actual dispatch之间。

回调返回后如仍要继续认证或actual调用，必须重取本次actual current/source/fact/owner/phase再做最终联合认可。不得使用回调前metadata自称fresh；也不从新current建立expected。不能以持GIL、私有local或清空owning来推出globals/阶段/subject没有变化。

## 3. 逐slot的两类证明

| 类别 | 所需真实源证 | 能推出 | 不能推出 |
| --- | --- | --- | --- |
| ROOT_COVERED_NONLAST | 独立原strong root及其真实producer；在本次释放后仍持续存活且未移走；同runtime/build与实际branch成立 | 该次释放不销毁对象；在已核具体Py_DECREF分支内不进入deallocator | 全build无回调、该原root来源可信、所有其它slot也覆盖 |
| MAY_LAST_WITH_CONTRACT | 允许末次释放，具体类型/slot或同版通用C API合同可证indicator后置条件、共享一致性及再入规则 | 只按实际已证明的后置条件作有限归纳 | 任意native直接改indicator必合规、无Python回调、原观察透明 |

future静态表必须逐项给：acquire_site、actual_producer、ownership_kind、原独立root及持续区间、可能移走该root的原操作/回调、last_use、release_site、release_order、选择的类别、具体source依据和未闭前提。未知项继续阻塞，不能默塞任一类别；不能把新增native持有本身当“独立原root”，也不能通过不释放或公开quarantine创造覆盖。

新增guard即使能覆盖一次释放，其自身最终release仍须证明，不能把难点后移。weakref、历史检查或瞬时refcount>1不算持续strong root。真正的原owner/source bank及合法输出transfer只是候选覆盖；异常first最终转回indicator，不作为普通本方cleanup销毁。

本批只选该表合同，未生成future源码或逐槽完成表。原source/fact强根、callee/args/actual return、异常slot及raw chunk均须进入同一最终目录；raw chunk不是PyObject，不能套用Py_DECREF/tp_dealloc证明。r1131的PRIVATE/ATTACHED先完整restore/detach再free、COMMITTED交解释器及原Pop limit-after-free边界保持。

## 4. 异常证明：已确认、条件结论与未证

已确认：普通Python __del__路径slot_tp_finalize9071–9092先GetRaised保存，再执行/WriteUnraisable，最后SetRaised；releasebuffer_call_python9251–9306也保存恢复，末尾assert无pending。tagged refcounting的“exceptions in such code are not propagated”承接其描述的Python代码，不能单独升级成每个native tp_dealloc都保持任意entry indicator/同一E identity的全称原文证明。反过来，缺少该证明也不是某native任意改indicator合规的反例。

有限条件证明P(k)：3.12本次actual E经GetRaised转交到一owned slot，indicator清空；若随后有限k个实际cleanup均有真实source合同证明“入口NULL→返回NULL”，则逐项归纳后仍NULL；末次SetRaised(E)面对old=NULL，不做old DECREF，随后只scalar返回可保同一E identity。3.11对应actual Fetch/Restore triple分支，仍要其合法形状与三个旧字段为空。该归纳允许cleanup内部Python回调，并不证明E的traceback/context字段不变，也不证明清空indicator对观察者透明。

若能证明某old pending E2的deallocator保持入口indicator，则还有两个有限子路线：Get E2清空，再对E2这个owned引用做一次DECREF，P(1)返回NULL后Set actual first；或者直接SetRaised(actual first)，原实现先安装first再DECREF old E2，在这个deallocator合同下返回仍为first。不需无限drain或藏引用。**这两条仅是条件所有权推导，未选为任意free错误的覆盖policy，也不宣称恢复段无回调。**是否应保原finally覆盖、报告unraisable或保其它实际错误仍按具体原路径决定。

相较r1131，“E2必须释放”不等于逻辑上无法有限释放；本批收敛为缺少具体cleanup合同和错误policy。不能用重建MemoryError、无界循环或泄漏绕开。PREP_NULL仍仅为本层清理后一次真实NoMemory的新失败，actual call=0；如果已有必须保留的actual first，不套用PREP_NULL重建。COMMITTED_ERROR保actual callee错误，success保actual return，均不能靠最终metadata门替代退出清理证明。

## 5. 兼容、预算及后继准入

同一原strong root持续覆盖若成立，可以减少对未知末次析构的依赖；必须由原locals/参数/持有区间静态证明，不执行真实frames/locals诊断，不读运行时refcount，不修改GC或hook。root覆盖、原早释放观察、新增持有延长、回调中的共享状态及错误运输一起进入N4/COMPAT；source依据不能替代native自身原根认证或D1/D2。

实际K/N、完整runtime/tstate/build ABI、域外容量路线、最终门到push、kwargs/default错误、arena free的indicator/再入、root长期成本及全调用方尚未闭。六工具/owner、seed/Product/carrier、38处context/foreign、CORE和P0/import需同版组合。没有产品源码缺口关闭或执行准入。

完整design两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling、真实frame/locals、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。未来端口设计不产生执行授权。r920/r964耗不重放、r967不执行不重问、P06历史0/7不再索材料。

原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持；control1MiB非确认native heap上限。apply本批0/历史11、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：具体原强根覆盖与错误policy

父r1132，产品仍r1106 SOURCE02完整159。先按actual acquire列独立原strong root持续区间/可移走操作/实际release，区分ROOT_COVERED_NONLAST与MAY_LAST_WITH_CONTRACT；不靠当前refcount、同对象去重、新增持有自证或缺省归类。

1. 优先用固定原caller参数/local/原持有链证明nonlast；确有may-last项再按确切类型/同版合同补indicator与再入后置条件。P(k)允许回调，不保共享metadata或透明原观察；E2有限处置需具体覆盖policy，不把条件推导计为完整first闭合。
2. arena free独立处理；维持precommit完整restore后free、committed归解释器及原Pop limit-after-free。actual K/N、完整ABI/所有build/域外路线/D2和root长期成本继续进入同一N4/COMPAT。
3. 汇合六工具/owner、seed/Product/carrier、context/foreign、CORE及P0/import与全调用方。完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

原保护/预算/禁项保持。typeobj/object及旧pymem失败下载不重跑，web来源级别不冒认tagged源码；正常库/服务/权限/平台/Git不操作，r920/r964耗、r967不执行不重问、P06历史0/7不再索材料。B001/B002 open2，Goal active，非只剩验收。
