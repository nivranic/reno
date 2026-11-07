# r1130：原调用域与空 chunk PREP

r1130确认容量不足提前拒绝会删除原可成功调用，不能作为完整D1修复；选择有匹配ABI前提的原runtime数据分派与空chunk PREP子方向，回调准备后fresh核再以原actual function/实参调用standard vectorcall。提交前rollback先恢复完整栈状态再free，提交后归解释器，不再访问或释放chunk。PREP OOM属新增准备失败，不能冒认原call已进入；ABI/错误合同/原race/D2/N4/全接线与COMPAT仍未闭。B001/B002 open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

28完整pairs每侧1,535,081B、8原要求、23原文（22tagged/继承tagged+1未核tagSDK）及17项PE/COFF链接面分列。12 named export存在含_Runtime数据的__imp__导入，VirtualAlloc/Free、Push/Pop、_PyEval_Vector五项缺席。tagged下载四成功一RemoteDisconnected终止，不重试；SDK pymem头tag未核。空chunk基础设施修改属于未来版本化runtime端口，不是live frame/locals诊断，不产生运行授权，也不默增allocator T0根。

产品159源码7,956,657B及保护56同18缺保持，本批candidate/compile/import/运行/native build/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002及原预算不放宽，control1MiB非确认native heap上限。正常库/服务-dist/ACLsettings/平台/Git不操作，禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964耗不重放、r967不执行不重问、P06历史0/7不再索材料；完整Goal非只剩验收。

# r1130：保原调用域的 D1 空 chunk 准备子方向

父 r1129，产品仍为 r1106 SOURCE02 完整159文件、7,956,657 B，未安装。本批选择有匹配 ABI 前提的空 stack chunk PREP 子方向，否决以容量不足提前拒绝作为完整修复。没有新产品候选、future K、compile/import、native build/load、模型、QA或诊断。B001/B002保持open2；完整 design/source/execution/Goal均False。

## 1. 原契约与不能采用的捷径

r1109的8条原要求及原句位置均冻结核同。R002要求每条边fresh，R004保observer次数，R007保原合法错误；CONTRACT-DECISIONS§1还明确三incoming的原单次lookup/actual target、原实参、顺序及每轮次数。它们没有授予“metadata全部合法但现有frame容量不足”这一新增业务拒绝条件。

反例无需恶意hook：`HasStackSpace=False`，原arena分配成功并且不改任何metadata，原standard vectorcall仍可正常调用。容量gate直接拒绝会删掉这条合法成功路径。原bytecodes.c的CALL_PY_EXACT_ARGS也在容量不足时DEOPT到CALL，而非失败。因此capacity-only最多证明已成功快路的条件，不替代慢路、不计作D1完整修复，也不将其作为本批支持范围。

同样不选择只支持default allocator、默增allocator T0授信、修改全局allocator/trace/monitoring/PEP523、执行expected code副本、新建replacement function、或全C替代原业务body。r1112 T0原六组C001–006不含allocator；本批不后移T0或从current allocator首次读取建立expected。调用原运行时内存服务与认证业务subject来源是不同用途，不能把前者快照自洽冒认后者可信。

## 2. allocator 原文与有限轨迹的界限

本批新取得memory.rst：468–508说明PyMem allocator域、thread-safe和初始化后必须wrap原分配器等合同；647–673的Arena部分只列ctx/alloc/free和Get/Set，没有同等详细的reentry合同。所读原文未明确禁止allocator进入Python，也没有保证任意Arena再入都安全。不能仅由“未禁止”就宣布任意hook轨迹合法，更不能把PyMem域条款无条件套到Arena。

funcobject.c473–505的公共code setter要求CodeType、通过audit并匹配freevar数量，不要求参数数目或locals布局相同。若某合法allocator wrapper在正确GIL/interpreter分支进入Python，改actual F.__code__后恢复其调用栈，则ceval1589保存的frame code与1321之后的func_code布局可能不同。独立code强根可避免把释放旧code当唯一前提，但该完整allocator再入轨迹的合法性仍未被本批证明或执行。不能误写成必然执行改后code：frame->f_code来自先前保存值，而initialize_locals后读的是actual function字段。

原obmalloc.c616–627明确保留alloc/ctx字段读取的race；635–644用runtime-global arena表分派alloc/free。GetArenaAllocator有WAIT_LOCK，不能放进最终无等待raw段。把分配准备放在最终metadata认可之前，可以避免依赖“任意allocator都不再入”这一未经证明的前提；这不是对当前allocator来源、其安全性或任意native内存改写的认证。

## 3. 具体入口：原 runtime 数据与版本化服务端口

本机SDK的PE/COFF纯字节检查覆盖17个具名目标：12个DLL named export存在，其中11个函数可直接导入；`_PyRuntime`作为数据须通过`__imp__PyRuntime`导入，不能因direct symbol缺席就写成不可用。另5个确实缺席：`_PyObject_VirtualAlloc`、`_PyObject_VirtualFree`、`_PyThreadState_PushFrame`、`_PyThreadState_PopFrame`和`_PyEval_Vector`。没有加载DLL、调用GetProcAddress或native API，更没有link/build结果。

官方obmalloc.c211宏将arena表定位到`_PyRuntime.allocators.obj_arena`；tagged pycore_runtime.h116/192分别有allocator成员和PyAPI_DATA原声明。本机SDK pycore_pymem.h完整文本显示obj_arena成员，但其upstream tag身份未核。该tagged头的单次下载遇RemoteDisconnected，已终止，不重试；不能用本机副本填成tagged成功。

选定子方向：在确实匹配该runtime完整ABI的native版本中，由原导入数据根访问同runtime-global表，本地搬原VirtualAlloc/Free两段的分派表达式。每次仍按原runtime当时的alloc/ctx或free/ctx选择服务，不缓存“已认可allocator”、不改表、不固定到OS allocator，不用GetArenaAllocator快照代替原表达式。原runtime service可能回调，发生在PREP/cleanup，不在最终raw段。此选择保留原服务与free域的方向，不声称字段对读取原子或原race已经消除。

`_PyRuntime`符号存在不证明实际已加载runtime就是这些SDK字节，也不证明完整结构偏移、构建宏、transitive headers、同一tstate/interpreter/runtime和全部版本/平台匹配。私有头要求Py_BUILD_CORE；future native的构建方式、正确导入及结构静态断言仍待完整设计及后续源码/构建审查。本批不设置构建宏、不创建native源码、不把内部结构叫稳定API。完整ABI/packaging/COMPAT仍open；项目>=3.11不收窄。

## 4. PREP → raw admission → 原实际调用

目标保持原已选actual Python function与原实参数组；最终还核actual vectorcall就是独立原standard `_PyFunction_Vectorcall`。使用公开PyObject_Vectorcall实际调用，不替换其调用槽、不直接执行expected code。这里只覆盖原固定正常实参及r1129 make_view条件正常绑定的子方向；bad kwargs、varargs/varkw、缺参及全部错误/未来ABI不因此已闭。

1. 原callee lookup及原operand求值先按原顺序完成；准备独立强持引用、native参数buffer与本次scalar reservation记录。任何可能分配、释放可执行对象、等待或回调的准备都先完成。actual K和expected frame size来自原来源链，不从后来current co_consts补pin。
2. 在同一actual tstate看当前chunk/top/limit。已有空间满足原**严格**`N < limit-top`时无需新chunk；NULL或不足仍保慢路，不能直接拒绝。N是本次actual/原K的co_framesize，计量单位为PyObject*槽，不是字节数。
3. 不足时使用上述原runtime服务准备一块新raw buffer，size按原16KiB起、倍增至至少`sizeof(PyObject*)*(N+1000)`的方向。回调可能发生在这里。本方不读取live frame内容，不创建PyFrameObject，不修改任何已存在frame/locals。
4. allocation返回后立即私有持有actual buffer，再fresh取得same实际tstate和其**当前**chunk/top/limit；不能沿用分配前previous/top。初始化新header并保存完整rollback值，包括旧chunk/top/limit及必要的旧chunk.top。原metadata若已变化，仍只能按原独立expected处理，不能以这个新current补齐baseline。
5. 将empty chunk接入真实datastack基础设施：new.previous为此时原current chunk，保存其实际top offset；limit为新buffer尾；非root top=`data[0]`，无previous的root按原skip规则top=`data[1]`。这确实修改runtime栈基础设施，是明确版本化runtime端口，不把它称为普通Python对象分配或诊断能力。
6. 安装后无回调raw核本次caller/source/fact/owner/phase、actual callee全metadata、standard slot、actual code==原K、同一tstate及`N < remaining`。final阶段不分配、不等待、不getter、不DECREF可能执行的对象。未通过时走提交前rollback，绝不以扩大expected或retry“修好”。
7. 成功后在同一连续段标记CALL_COMMITTED、转交chunk所有权，立即以原实际func和原实际args执行PyObject_Vectorcall。standard路径的INCREF/flags读取与frame fast push不触发上述arena慢路；原frame/参数初始化、原eval/PEP523选择随后自然执行。最终门与实际push之间无回调/容量变化仍需按完整future源码逐操作证明，不能由此文字宣布完成。

新chunk容量证明不能只写“足够大”：从原byte size扣除实际`offsetof(_PyStackChunk,data)`及root skip一槽，证明剩余槽严格大于N。绑定actual sizeof/offset、整型域和倍增溢出；不新增任意帧上限来通过。future K的N与所有原callee覆盖尚未取得，本批没有执行sizeof、编译或动态查看tstate。原allocator成功且metadata不变的正常形状，应按该证明继续原实际调用，不能在最后再变相因容量拒绝。

## 5. 所有权、异常与两种失败域

| 状态 | chunk归属 | 本方后续动作 |
| --- | --- | --- |
| NO_RESERVATION | 没有新buffer | 现有空间够则原final/actual call；不足则PREP |
| PRIVATE_BUFFER | native独占actual raw返回 | 未接入；失败可按原runtime free域释放，不能传给任意subject登记 |
| EMPTY_ATTACHED | native持buffer及完整rollback责任 | call前拒绝/失败必须先detach并恢复旧chunk/top/limit及必要top字段，再调用可能回调的free |
| CALL_COMMITTED | 解释器负责已转交的新chunk | 标准actual frame push/pop管理；本方立即清空自己的raw owning pointer，返回后不能再解引用或free |
| ROLLED_BACK | buffer不再在datastack上 | 只按本次实际取得的引用逆序清理；不能恢复回调发生前过时的状态 |

标准Pop在frame base==current.data[0]时detach并经runtime-global free/ctx释放非root chunk；root skip路径保留root给解释器长期使用。因此call提交后无论结果成功或错误，都不能“返回后核指针、再释放一下”。本方未跨业务callee持metadata/foreign锁，也没有公开current builder、last reservation、first/last product或可写expected接口；reservation为本次原C调用的私有scalar状态，不是runtime journal。

PREP allocation失败须明确：**原standard vectorcall尚未进入**，这是新增准备资源失败，不冒称原actual call已尝试。不能失败后再调原vectorcall让分配重试，以掩盖次数差异。此路线保原allocation成功的正常域，并不自动证明literal call-attempt计数、OOM第一异常对象、traceback/monitoring/引用观察和错误时点完全保持。r1120已区分新增层资源失败与原错误，不代表本批已获得任意错误合同变更；这些项须进入完整异常/COMPAT设计，未闭则不能批准实现。

原actual call进入后的参数/业务失败仍沿原路径；不能统一换成本方认证异常。提交前refusal是新增认证拒绝；先恢复runtime状态、再做可回调free与引用释放，按实际异常所有权保first/原finally覆盖关系。失败初始化、不足回调、嵌套调用、free回调、部分metadata拒绝、root/nonroot、actual参数错误与长期保留结果均需共同N4证明。当前没有最终slot/heap/clear/free表，不声称错误及释放已经完成。

## 6. 与其余设计的连接和准入

这条方向避免将“所有allocator无回调”作为正常D1的必要假设，也不关闭D2：原PEP523和COPY_FREE_VARS/监控阶段仍独立；真参数consumer和逐叶门仍要结合原代码、实际closure消费与metadata共同认可点。r1129 owner专项、r1128其余五工具/小try、r1126 seed/Product/carrier、r1123 context/foreign、r1122 CORE pair/locals及r1124/1125 P0/import必须同版组合，未来K、全调用方、所有成功/错误与COMPAT仍open。

本批28完整输入pairs每侧1,535,081B、8原要求、23段原文（22tagged/继承tagged+1未核tagSDK）及17项SDK链接面静态检查分列。五个tagged下载目标四成功、一终止；SDK二进制只读hash与PE/COFF，不复制到文档、不运行。静态parser四个纯helper与已封存原工具AST相同；新增_Runtime查询的修订完整diff/逆像保留，旧16目标记录不覆写。

Root发布工具写前复核发现初稿继承了错误目录slug及AST_files标签；publish_01未执行，保留原稿及完整差异，由publish_02修正当前链接、标成new_product_AST_files=0并使用自身确切pins。这是资料工具修订，不是新产品候选或compile。

完整design/source/execution/Goal均False。完整设计两审前不生成产品候选；源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。原159源、保护56同18缺、17lease/1449slot/406尾/五S002与case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；control1MiB不是确认native heap上限，新增raw chunk/持有/错误成本还未核进共同预算。

正常库/service-dist/ACLsettings/真实平台/Git不操作；禁profiling、真实frame/locals诊断、断点/停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523或allocator。这里的未来runtime端口设计不产生实际修改tstate/加载native的授权。r920/r964耗不重放，r967不执行不重问，P06历史0/7不再索材料。apply本批0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：空 chunk 事务与同版调用消费

父r1130，产品仍r1106 SOURCE02完整159。capacity-only拒绝不作修复；原runtime数据→PREP→final→实际standard call仅为有ABI前提的子方向。

1. 给原runtime服务端口、same-runtime/tstate/完整构建布局、实际N和原16KiB倍增容量算式、root/nonroot及commit/rollback的逐字段合同。pymem tagged单次下载已终止，不重跑；本机SDK头仅作未核tag文本，完整ABI/跨版本仍需独立解决。
2. 具体处理PREP OOM的新增失败域、原actual call尝试/错误/观察差异；成功路径不能借容量门删原可成功域。final到原frame push无callout及真实kwargs/default绑定、D2独立核；不靠新runtime hook、全局allocator改写或替代function补洞。
3. 与owner/five-tools/小try、seed/carrier/Product、context/foreign、CORE及P0/import整合，完成共同N4/slot/heap/实读/异常/COMPAT与>=3.11打包。完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

所有原保护和限额保持；B001/B002 open2，Goal active，非只剩验收。
