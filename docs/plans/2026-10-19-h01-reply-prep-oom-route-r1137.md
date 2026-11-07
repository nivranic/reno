# r1137：准备分配 NULL 与类型 guard 分支

r1137核明已初始化且匹配v3.12.10源码/build条件下，reply的已知GC分配NULL分支走actual NoMemory；该分支没有Init实例，getter attempts0。选择每次独立私有C自动type guard，成功须实际实例type根成立后nonlast退出，失败可能last退出并先结清guard再还原actual准备first。初次取得前原根、完整Init/ABI、NULL入口析构与整图N4仍待；B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

复用同版完整gcmodule/exceptions/tuple/errors/cell/typeobject/object头和三原Try；空tuple singleton不另malloc，原cell成功getter不冒认function audit，public PyCell_Get空值错误语义不同而不采用。仅确认已知NULL分支，不宣称整个旧try新错误矩阵关闭。guard逐取得另计，不把其与实例type责任同identity去重。

本批无下载、候选、产品compile/import/运行/native/模型/QA/诊断，apply0/历史11、源码缺口关闭0、产品pass0。159源、保护56同18缺、原有限额和全部禁项保持；完整Goal仍有工程，非只剩验收。

# r1137：reply 准备失败与私有类型 guard

本批承接已封存 r1136 修订02，仅选择 CELL_TRY 准备段的有限子协议，不生成产品候选。r1136 的48份封存文件、159份 r1106 SOURCE02 产品文件保持；B001/B002 open2，完整 design/source/execution/Goal 均 False。PREP-NULL-ANALYSIS-01.md 创建时 r1136 尚未封存，是历史准备记录，原样保留；本 ADR 采用当前封存状态。

## 1. 原操作、已知分支与适用前提

三处原点仍为 function_row59、class_row131、check_view322 的 actual captured `get(cell, 'cell_contents')`。原空 cell 的 ValueError 由原 late handler 处理，成功值不经第二次 getter 重建。CELL_TRY 的认证拒绝仍以独立 DENIED reply 运输，ACCEPT 在旧 try 外拒绝；本批不把准备失败或 C 入前事件冒认为 getter 失败。

同版 gcmodule.c2322–2351 显示：大小溢出或实际 PyObject_Malloc 返回 NULL 后进入 `_PyErr_NoMemory(tstate)`；`_PyObject_GC_New` 在这个 NULL 分支没有执行 `_PyObject_Init`。故这条路径没有实例、没有隐含实例 type 引用、没有 payload，不调用实例 dealloc、UnTrack、GC_Del 或原 getter。实际 getter attempts0。malloc/free/回调自身责任不因此由本层接管；本层只结清自己已取得的 type guard 等责任。

exceptions.c3316–3348、3361–3375 的实际 NoMemory 路线分为：freelist 空且 allow_allocation=0 时取本解释器 last_resort_memory_error 新引用；freelist 非空时补空 args、复活原 freelist 对象。tupleobject.c61–74 的 PyTuple_New(0) 直接取静态空 tuple 新引用，不走 tuple_alloc；不能以 args==NULL 防御分支声称正常空 tuple 还要 malloc。没有调用任意 current MemoryError constructor。异常系统尚未初始化时会 FatalError，因此结论只适用于已初始化、源码/ABI/运行时/build/引用包络匹配的条件；不保证任意运行状态都能捕获 MemoryError。

NoMemory setter 先安装该次 actual error，再释放 old indicator。异常对象可从freelist/last-resort复用，“该次 actual”不表示fresh identity或新分配实例。即使 allocator 回来时已有其它错误，这一已知分支选择的是该次准备的 actual NoMemory，而非缓存的旧错误或将来 getter 的错误。释放 old 的匹配 pending 合同沿 r1135；不声称异常内部字段、traceback 或其它可变 source/phase 在释放期间不变。`_Py_NewReference`、引用统计和实际 build 包络仍待核。

## 2. 选择一个独立、逐调用的 type guard

选定在实际 C 入口、第一次可再入准备之前，为该次实际 reply type 新取**一笔私有 C 自动 owned guard**。它不是全局/最近/first-use/reservation，不放入 Python globals 或可被退休清理的共享槽。普通调用参数仍由实际 caller operand 持根，不能拿本层 guard 释放调用方的那份引用。

guard 的来源必须是 r1112 原 type 创建→独立 state 持有的实际产物。读取 actual type、检查有效源根、取得 guard 必须处于已证明的同一 raw 段：原 owning 边在 acquire 前持续，receiver 本身的实际 C argv 根不自动证明它的任意可变字段持续。如果实现中读取字段到 INCREF 之间有 callback/GIL 释放，必须重排或增加真正持续源根，不能在重入后 INCREF 旧 borrowed 指针。这个前置根及未来 consumer 布局/原 producer 接线尚未形成源码；**本批不把它报为已证明**，也不从 current module/type 名称补 expected。

guard 取得后，原共享边在 allocator 回调中被 detach 不会释放 guard 所持 type；但 guard 只保对象存活，不保证 active/source/metadata 不变。准备结束仍须原 fresh 门。不能以“有 guard”授予任意 caller 来源、禁止正常退休或替代 D1/D2/TMD。

成功分配返回后，先以 raw writes 把三个 payload 置 NULL、mode 置 PRIVATE，保持 untracked；再处理 guard。不得在 NULL 返回上写字段，也不得对尚未 Init 的原始内存调用实例析构。`_PyObject_GC_New` 成功点调用 Init 的事实已知，但**缺少同版完整 Init/宏头与实际构建核对，尚未证明实际实例已建立 heap-type owning 责任**。只有这个责任确立且持续、release 包络也成立时，才在空壳仍由 C 局部拥有的时点释放 guard：此刻实例对同 type 的真实引用独立覆盖，guard release 不会是最后一笔。头文件缺口不能以额外猜测 INCREF、SDK 不明 tag 或 generic allocator 替换绕过。

释放 guard 前先把 owning 自动槽 detach 到 temp，槽置 NULL；temp 实际释放后才结清责任。全 API object.h624–659/678–708 给出不同 build 的 INCREF/DECREF 分支，nonlast 能排除末引用调用 `_Py_Dealloc`，但不自动证明所有 ABI/debug/statistics 包络无 callback/无 indicator 变化。这里保留逐 build 义务，不用 void 签名作证明。条件成立时 guard 只跨 allocation/Init/raw 空壳初始化，不跨原 getter，不延长到 reply 发布；getter前仍执行实际 fresh 核。

## 3. 无实例的失败与有实例的失败分别结账

| 状态 | 本层真实拥有 | 退出责任 | getter 次数 |
| --- | --- | --- | --- |
| GUARD_NOT_ACQUIRED | 无新增拥有 | 前置根未建立；不是已覆盖的 NULL 分配分支，不用 stale 指针继续 | 0 |
| GUARDED_BEFORE_ALLOC | 一笔 type guard | 进入实际分配；回调后只信本次实际结果，不登记新 expected | 0 |
| PREP_NULL | type guard；actual NoMemory 在 indicator | GetRaised 移到私有 first；无实例可析构；结清 guard/temp，最后一次 SetRaised 移回 first，仅 scalar NULL 返回 | 0 |
| SHELL_INIT | guard + actual owned shell；隐含 type 责任须另证 | raw 清 payload/mode；证明实际实例 type 根后才退 guard；不是把 guard move 到实例 | 0 |
| GUARD_RELEASED | actual owned untracked 空壳及其实际 type 责任 | 原 fresh 门；DENIED 或一次原 getter；不能继续使用已释放 guard | 0 |
| GETTER_NULL | 空壳/type 与本次 actual getter error | 按 r1136/r1135 结清空壳/type后还原；原 late matcher/handler继续 | 1 |
| SUCCESS_OR_DENIED | 实际 reply/三字段的逐取得责任 | 按 r1136 填完→Track→实际 return；本批不补新的 getter后拒绝 | 1 或 0 |

PREP_NULL 的 first 是该次准备的 actual NoMemory，通过 GetRaised **move** 到自动 owned local，不是重新构造或额外 acquire。guard 可能在原共享根退休后成为最后引用；不能将失败清理写成 nonlast。先 detach guard，再释放实际 temp；所有本层资源结清后，按 r1135 `tmp=first; first=NULL; SetRaised(tmp)`，setter 先装回 first再释放附加 E2。最后只 scalar NULL return，不再 DECREF/写日志/查 phase/调用其它 API。若 actual indicator 或匹配合同前提不成立，不能声称这条已知路线成立；不得用虚构 first 掩盖缺口。

这条**已有 actual first**的有限路线允许本层 cleanup 附加 E2不额外报告，保原业务报告/finally合同；不需要证明失败 guard 的 NULL-entry 析构是 NULL→NULL，但中途调用的 indicator 前置条件仍逐项核。没有将它推广到 getter成功后的 payload/type/free 析构、整个 GC 或一般 NULL→NULL，也不引入 Clear 循环或假 pending exception。

如果成功 Init 后出现其它失败，shell 与 guard 是两笔不同责任，shell 的隐含 type acquire 又另列。必须按 actual取得/是否已经退 guard，分别结账；不能把成功 shell放入“无实例 PREP_NULL”清理，或把 guard 与实例 type 引用因 identity 相同而去重。部分填充、getter actual result未 move、Track后公开等出口继续沿 r1136 五态表，不能由本表替代。

## 4. 三处旧 try 的有限错误分类

在上述条件下，**已经到达已知 GC allocation NULL 分支**的本次准备错误是 MemoryError，actual getter attempts0；它不是原 cell getter的 ValueError。原 late MATCHER仍在原 except 头观察 actual error class并核源；matcher自身失败从 except头逃逸。这里只确认已知 NULL 分支，不能由一条 MemoryError 路线推导 try 内全部新操作都不会抛 ValueError。

未覆盖项包括：C尚未进入的 call/monitoring事件；取得 guard前的原根缺失；成功 allocator的错误包络；Init/build/reference tracing；本方其它拒绝/错误构造；getter后到 ACCEPT的原local清理。所有本方正常认证拒绝仍须 DENIED，不能把 RuntimeError/ValueError构造塞进旧 try 代替该路线。原三handler差异、原 finally覆盖及 whole-tool first的层次均保留。

cellobject.c51–60 的 public PyCell_Get 在已核 cell为空时返回 NULL而不设原 ValueError；129–137 的 cell_get_contents 则设置 `ValueError("Cell is empty")`。getset表与 PyCell_Type 的 GenericGetAttr接线分列。因此本批不采用 PyCell_Get替换原 actual getter。原 cell成功getter body没有 function.__code__式audit，不能机械套那个轨迹；actual object getter→descriptor/初始化缓存/entry事件及引用包络仍待，不据短body宣称整个调用无 callback。

## 5. 预算、验证与后续闭环

每次实际取得 guard新增一笔 acquire和一次 release（成功或失败择一）；两者不能算成零成本 move。PREP_NULL没有实例 acquire，成功则另计实例、隐含 heap type、consumer/cell新引用和getter actual返回移动。guard是控制流中的暂存根，不是额外长期容器；实际布局、peak refs/heap和递归重入成本待完整预算，control1MiB仍只是控制数据实读上限。不能把三payload表或本表行数称为完整入口取得/运行覆盖上界。

静态核对复用同版完整输入、三份原 cell Try AST及 r1136封存；不下载、不执行产品/原getter/GC/真实frame或诊断。准备发布保完整after、preview和逆像，Root及同一名只读独审核对；材料通过不代表整体方案/source/execution通过。

本批有限推进为：已知 PREP_NULL错误路线、显式私有type guard选择、成功nonlast/失败可能last的分支责任以及原getter差异。完整Init/类型来源前置根/NULL-entry payload与free/consumer-Product-A-J-bank- owner14各SCC/未来K和真实locals/普通226叶与7append/三reply/owner14/全P0与38context/CORE/import/D1/D2/COMPAT及>=3.11平台仍待。完整设计双审前不生成候选，源码准入前不产品compile/import/运行/native build/load/模型/旧绿F001/QA/诊断。

正常库、服务/dist、ACL/settings、真实平台及Git不操作；禁profiling、真实frames/locals、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964单次已耗不重放，r967不执行不重问；失败下载不重试，P06历史0/7无材料不再索取。17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云和完整Goal仍未完成，非只剩验收。

# 下一批：实际 Init 与来源持续根、NULL入口退出

继续取得并验证实际匹配的Init/对象头/引用包络证据；两个已失败新header不重试、不换地址重放，不以tag未核SDK或较新版文档回填。对type guard最初取得前的actual consumer/state源根、raw读取到acquire区间、成功实例type责任与release包络给源码级可复核接线；不能把本批条件guard子协议当已实现。

分别给成功payload/type/GC_Del与一般NULL入口的退出后置条件；已有actual first沿r1135在全部owned/temp/free结清后才还原，仅scalar返回。不用Clear循环、虚构first或假设持GIL补证明。继续consumer/Product/A/J/source bank、owner14/bank9及各SCC实际clear/退休，保原selection-before-RHS。

三reply仍须future真实FAST local、原late MATCHER/try外ACCEPT及三分支；普通226叶/7原append/owner14和TMD真实结果拒绝独立推进。全P0/38context/CORE/import、D1/D2/COMPAT/版本预算与>=3.11不缩减，完整设计双审前不生成候选，源码准入前不产品compile/import/运行/native/模型/QA/诊断。全部保护/有限额/禁项/已耗授权保持，P06无材料不再索取；Goal active，B001/B002 open2。
