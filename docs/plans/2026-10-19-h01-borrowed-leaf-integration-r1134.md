# r1134：逐叶借用接线与原append保留

r1134将六工具250个原Call逐点分为226个拟采用直接入口借用的候选叶、7个保留原Python list.append、3个独立cell reply点及14个owner专用C producer原点。raw字段只在最后callout后重新取得并在同段用完，可不增加guard；跨callback、实际输出/异常、TMD拒绝、frame PREP和source bank退休的拥有责任保持。完整接线/ABI/D2/N4/COMPAT未闭，B001/B002 open2；完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

六完整body与r1128 AST核同，其余292个语法节点计数保持；数量不代表运行覆盖。五工具7个append逐点核list literal局部来源，不改成新增bound-method参数；owner14沿r1129的stub/bank9/原选择时序独立证明，其binding/allocation仍待。未来实际code/局部槽仍待。仅段内借用不是来源证书，也不延伸到dispatch内部D1/D2。

01混入owner模板的分类错误及Root漏审保留，02纠正为226+7+3+14；不撤回r1129。本批复用已有完整输入和父before全文，保留本批after及完整逆像。原保护/预算/禁项保持；candidate/compile/import/运行、native/模型/QA/诊断0，apply0/历史11、产品pass0。完整Goal仍有工程，非只剩验收。

# r1134：逐调用借用接线与原 list.append 保留

父 r1133。产品仍是 r1106 SOURCE02 完整159文件、7,956,657 B，未安装。本批固定现有六工具的调用点和拟用所有权协议，不生成未来产品源码/AST/code，不执行产品、native、模型、QA或诊断。B001/B002 open2；完整设计、源码、执行及 Goal 均未完成。

## 1. 原调用点与两项具体接线决定

完整原 catalog 与 r1128 的六个 AST body 核同，仍有250个 Call：function_row27、module_row7、class_row47、owner_storage14、make_view16、check_view139。位置与完整表达式逐项保存在 CALL-OWNERSHIP-02.json；它们是语法调用点，不能当运行次数。01误将owner模板混入普通Python接线，Root初审漏掉r1129路线，已保留01与问题记录；02按226+7+3+14修正，不以本次修订撤回r1129。

- 五个保留原算法的Python工具中226处列为“若接 native 叶，采用直接入口借用”，不为 callable/args 统一新增 strong temp。该分类没有证明操作来源、原成员选择或整个调用已安全，也不是226处已实现。
- 7处 list.append 保留原 Python 表达式。逐点核实 receiver 是上述五个Python工具的普通局部变量，赋值仅来自原 list literal：cells、rows、functions、function_batches。保留原方法选择及调用，可避免把原 LOAD_METHOD 路线改成先创建一个新的 bound-method 参数。元素身份/来源、未来实际局部槽与原工具身份仍另核。
- owner原模板的14个Call单列为C producer work，含names.append和slots.append两处。沿r1129专用C producer及Python stub，不把模板当将执行的Python body；C中原target选择先于RHS、真实work roots/bank9/返回及释放另证，不能从stub的descriptions参数替这些工作值补根。
- 3处 cell_contents try 保留 r1128 的 CELL_TRY/MATCHER/CELL_ACCEPT 独立协议，不能归入普通结果直传。reply、部分分配及原 getter 异常的所有权仍待。

其余原163 Compare、79 Raise、33 For、3 ExceptHandler、11 Attribute、1 GeneratorExp、2 Subscript也逐类计数核同。上述计数包含被Call包住的语法节点，不代表其它协议已完成准入。

## 2. 直接叶入口的拟定参数运输

本节仅对五个Python工具的拟定叶选择consumer参数布局；owner保留r1129专用OWNER_BUILD/descriptions路线，不套用本节私有头。拟定叶consumer的vectorcall参数数组为 operation、actual_target、原 actual_args。operation 是未来固定私有操作号，actual_target 仍在原时点先观察，原实参按原顺序求值；新增参数准备和解释器事件的 COMPAT 变化须同版审查。入口 self 来自已认证原 native consumer，不能从当前 G 属性重 pin。任意外部 C caller 也须实际持有传入对象，不能用“vectorcall借用”代替调用方证明。

这250处现有 Call 没有 keyword 或 starred 参数。对五个Python工具的226处候选叶，C 在完成具体 source/receiver/operand 门后，可将原参数子数组直接传给 actual_target 的原调用；nargs先以本次真实入口规则取出并验边界，再减去两个私有头项。不设置 OFFSET，不把 argv[-1] 当 scratch，不构造 Python 参数 tuple/list，也不把 operation/target 当原 callee 实参。

本次调用方实际拥有的 callable/各参数持续覆盖整个 C 调用；native 不偷引用、不 DECREF 借用、不修改调用方槽、不给借用安排“清理”。参数中的 tuple/list/dict/cell 本身存活，不意味着较早读出的可变 child 跨回调仍有效。

三处 incoming HELPER014/015/028 沿 r1133 的真实 target/argument 绑定运输，仍是原一位置参数；两个原 local factory 仍零参。它们的 actual source/fact/owner、D1 frame PREP、D2及全部调用方接线没有因本节参数布局而闭合。make_view 的原六 kwonly/default、owner descriptions 的原调用资格不受“250处叶无keyword”概括；本节不是外层工具 ABI 的成功证明。

## 3. 最终 raw 段内借用，明确不跨 callback

r1133排除的是“只靠可变 container 持有，证明历史 child 跨 callback 存活”。它不排除在最后一次 callout 之后重新读取、并在同一个无 callback 段内用完的借用。选择如下有限流程：

1. 完成所有可再入准备、原 getter/audit/锁/finally，所需独立原 expected 已由原 producer 持有。
2. 从本次真实直接 operand 或独立原 native root重新走 actual 路径。先验 parent 非NULL/exact type，再核长度/索引，再 raw读 child；先判 child 非NULL再读其 type。中间指针仅在本段使用。
3. 用 r1127 的具体 raw 字段、dict Next、Unicode codepoint、compact phase及版本化 type/module helper路线做共同谓词。全键 exact-str 扫描和查询在同段；不调用 hash/eq/descriptor/getter。SDK来源不冒认 upstream 身份，未来 C 每个操作/布局仍须审查。
4. 本段不分配、不释放可能last的ref、不 WAIT_LOCK/放GIL、不进入解释器/处理 pending，不通过本方写操作移走仍被借用的边。不能仅以“raw/持GIL”两个标签当证明。
5. 谓词用完后，全部无独立root的字段借用终止；只是让C指针失效/置NULL，不DECREF。后续 dispatch 仅使用本次直接argv仍覆盖的实际 target/args；错误构造、free、cleanup和回调后处理不得再访问旧字段。

段内临时字段不新增 owned acquire，因而也没有对应最终 guard release。若某值必须跨 callback，就移入第四节的独立 owned 类；不能用这项优化删除那类责任。具体反例仍是合法 cell setter 可换边并释放旧内容；“旧borrow→回调换边→用旧borrow”不安全。

最终 raw 到 actual call attempt 必须逐操作连续。标准 vectorcall 的内部绑定/扩容/默认值/错误与 closure 消费仍按 D1/D2另证；丢弃只用于比对的 metadata borrow，不等于首 body 或整个 body 保持 fresh。

## 4. 成功输出、失败与仍需拥有的对象

普通叶在 actual primitive 成功后，直接将那次实际 owned result移交原 Python 求值栈；不再给它增加一个重复的 native strong temp，也不在移交前 DECREF。原 append 的 None/原 error(failure)产生的异常实例仍由原 Python 语句按原路线消费。不能把 borrowed argv元素当作 owned return直接返回；若确有这类返回，仍需真正取得一份输出引用。

若 actual call返回NULL且本叶没有任何需清理的自有资源，原 indicator可原样返回NULL；不能多做一次无需要的 GetRaised/Fetch再还原。若 frame PREP、reply、输出拒绝或其它资源确实存在，按它的实际 owned路径处理，不能套用这个零临时资源分支。

下列拥有责任保持未闭，详见 REMAINING-OWNERSHIP-02.json：三cell reply及部分构造；outer TMD已持有结果却最终拒绝时的释放；实际异常和E2/旧indicator；frame PREP/free；A/J/Product/consumer/source bank的循环、退出和退休；owner真实generator/CORE/P0/import的producer；确需跨callback的tuple guard。特别是可变child额外INCREF后跨callback，最后释放仍可能是sole-owned，不能自动归nonlast。

原成功结果和原最终逃逸异常身份不换成重建对象。原finally导致的覆盖关系先自然完成；“保first”不覆盖原算法本来的覆盖。新增拒绝/准备失败不能被旧cell handler吞掉。所有普通叶来源门、原时点member selection、实际返回后到outer TMD的联系仍需具体接线。

## 5. 成本、材料复用与后继实施

本批不为226处候选叶的直接operand增加owned槽，也不为仅在raw段使用的字段增加guard；这是一项具体设计选择，不是未来全局slot/heap已算清。跨callback/producer/输出/异常的实际acquire仍逐项计账、不按identity去重。原17lease/1449slot/406尾/五S002和case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持；control1MiB不是已确认native heap上限。

已有完整输入保留在封存父批及原完整源码，按完整文件pin复用，不再次复制相同全文。文档发布保留父批已封存的before全文与本批after全文，校验完整逆像，不重复存一份相同before。该去重仅改变本批材料存储，不改变产品、预算或审查范围。

下一步具体完成三个cell reply、TMD拒绝结果及source bank退休的逐acquire/错误表，然后与future ABI/K、七处保留append的实际fast-local/LOAD_METHOD、其余协议、38context/foreign、CORE及P0/import同版组合，完成D1/D2/N4/COMPAT。完整设计两审前不生成产品候选，源码准入前不产品compile/import/执行/native build/模型/旧绿F001/QA/诊断。

正常库/服务-dist/ACLsettings/真实平台/Git不操作；禁profiling、真实frame/locals、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964单次已耗不重放，r967不执行不重问；P06历史0/7用户无材料不再索取。apply本批0/历史11、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal继续，非只剩验收。

# 下一批：关闭确实跨callout的owned路径

产品仍r1106完整159未安装。先完成三cell reply的实际结果、成功/拒绝/部分分配及旧try外ACCEPT/late MATCHER所有权；再完成outer TMD拒绝其实际owned结果、异常/E2和原finally关系。普通叶直接argv借用、原成功owned output移交及段内raw借用不重复加guard，不把它们的简化套到跨callback对象。

逐项列source bank/A/J/Product/consumer的真实acquire、原独立root持续区间、最后依赖、detach/退休、traverse/clear/free和错误policy。五工具7处append保原语法，未来K核真实fast-local/原方法路线；226处候选叶仍逐点核原target/operand观察、来源门和实际调用，owner原14Call由r1129的专用C producer单独逐项证明真实工作根、原selection-before-RHS、部分结果和bank9退休，不能从Python stub的直接argv覆盖代证；未关闭的非Call协议不能漏掉。

与原frame PREP/D1/D2、owner/真实gen、CORE、38context/foreign、P0/import及>=3.11/平台/预算组合。完整设计两审前不生成产品候选；源码准入前不产品compile/import/执行/native build/模型/旧绿F001/QA/诊断。保护/有限额/禁项、r920/r964耗、r967不执行不重问和P06无材料保持。B001/B002 open2，完整Goal active。
