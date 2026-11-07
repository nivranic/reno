# r1133：原操作数root与tuple路径guard

r1133核同三份原源码及HELPER014/015/028的真实local、每轮错时覆盖和解锁边界，选择有真实caller operand持有的borrowed argv与逐层tuple guard方向。新同版tuple文档补GetItem借用/SET_ITEM新tuple/Resize前提；非空共享resize拒绝与empty只换指针分列。可变边、实际future ABI/slot/root持续区间、错误policy、COMPAT/D2/N4仍待；B001/B002 open2，完整design/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

本批单次取得tuple.rst7844B，无重试或源执行。当前Python调用不冒认为已接native；checked-target只在tool成功返回后有本轮值；caller local不自动认证来源或持续root。tuple guard最终由真实operand覆盖，不以新增guard自证，也不覆盖dict/list/cell/attr可变边。

159源/保护56同18缺保持；产品candidate/compile/import/运行、native build/load、模型/QA/诊断0，apply0/历史11、产品pass0。原限额和禁项保持，完整目标非只剩验收。

# r1133：原操作数持有、借用入口与tuple路径guard

父r1132；产品仍r1106 SOURCE02完整159文件、7,956,657 B，未安装。本批把三处原incoming的真实binding/覆盖点落到源码，并选择有确切调用方持有前提的borrowed argv与逐层tuple guard子协议。没有产品候选、future K、产品compile/import/运行、native build/load、模型/QA/诊断。B001/B002 open2；完整design/source/execution/Goal均False。

## 1. 原caller的实际持有，不靠名字推测

r1109冻结的scope 1,181,315 B、registration 546,347 B及control 1,111,071 B与当前r1106三份源码逐byte相同。三原调用坐标仍有效；没有因旧坐标或同名函数重建source身份。

| 原边 | target／argument取得 | 原tool／identity／actual call | 原caller |
| --- | --- | --- | --- |
| HELPER014 | 768／771 | 881／882／884 | scope._authenticate，619–1122 |
| HELPER015 | 1003／1006 | 1116／1117／1119 | 同一caller、同一组_sc局部变量 |
| HELPER028 | 1735／1738 | 1857／1858／1860 | registration._check_stack_class(pin)，1569–1860 |

上述incoming_target、incoming_argument、checked_target是普通函数local，无global/nonlocal声明或nested-scope捕获。scope的pin由原for626、651及886绑定；registration的pin是参数，functions在1571原解包取得，function由1578原for逐轮绑定。scope的_PINS仍是module-global读取；_ac_state与_sh_source_record/fact是外factory的cell读取，registration对应_rh_*；这些不是caller私有local。

各轮错时覆盖target、argument、checked；checked只有原tool返回后才建立本轮值，此前可能尚未绑定或仍为上轮值。不能拿它提前覆盖本轮acquire。row/view/tool/_hx_pin也逐轮重新绑定。caller没有逐项del/None清空或包住actual call的finally；已列finally只解锁，最后foreign锁在view/tool/actual dispatch前已退。scope末尾1121–1122还有ContextVar/Token检查与_fail，不能把actual call返回就当整个caller已退出。

control实际callee的pin参数也不能倒用为尚未进入callee之前的root。词法local、别名等值与参数身份只证明binding，不能自动证明持续独立来源、每轮原对象、任意callback期间不被改变，或完整退出销毁次序。本批保留原body/lookup/实参求值、tool→identity→实际call、每轮次数及错误/锁顺序，不替换成G再查目标。

## 2. 选择的C入口：直接argv借用

继承3.12.10 bytecodes.c2661–2743的CALL有两条不同路径。普通Python函数且eval_frame为空/standard slot时，2692–2712把func与args引用交给新frame，不能把它误称为下面的C借用路径。非inline分支2715调用PyObject_Vectorcall，2738–2740在返回后才DECREF callable和各args。2923–2955的METH_FASTCALL专门分支同样先调用cfunc，再清args/callable。这些是所选兼容构建的静态原文，不是当前三个Python调用已经接入native。

拟选入口将原实际target及实际operand作为该次C调用的直接参数，在原求值点/顺序运输。入口只借用argv[i]：不偷取调用方引用、不改其槽、不把该借用放到本次返回之外，不为借用本身安排DECREF。对应调用方operand持有必须真实覆盖整个C调用；直接由C调用时也须给调用方实际owned root及持续区间，不能只凭“vectorcall是借用”证明任意C调用方正确。

若确有额外native strong temp，它在本次C返回前释放，由这个独立的实际operand root覆盖；不是以新增guard自证最终存活。原Python local可作为另外的候选root，但不替代真实入口operand证明。入口对象身份不认证caller/source/fact/owner，原C入口来源与future接线仍需N0及全caller设计。

对三处固定原实参，拟用本次C栈的一个PyObject*槽运输原actual argument；两处已知local零参factory可用NULL/0，但其原function来源和具体接线另外证明。此类buffer不拥有元素、不创建Python tuple/list，也不因此新增heap释放。调用actual function仍走原standard PyObject_Vectorcall；不声明无kw/default/kwargs的其它原工具也已覆盖。

不为这个固定borrowed buffer设置PY_VECTORCALL_ARGUMENTS_OFFSET；不能把argv[-1]或邻接source参数误当可写scratch。_PyEval_Vector1666–1685会为实际func/args取得供callee frame消耗的引用；这与外层C入口的borrowed参数root是两层责任，不偷换或重复释放。最终门到push、原allocation慢路、bad args、D2及所有build仍按原准入保留未闭。

## 3. tuple派生借用：选择逐层guard，不把类型当冻结证明

本批新取得官方v3.12.10 tuple.rst全文7,844 B。GetItem明确是parent持有期间才有效的borrowed reference；GET_ITEM不检查参数；SET_ITEM只应填brand new tuple，偷取新item且替换时不释放旧item；Resize只可唯一引用、不得tuple已为别处所知，并且可能替换/清空调用者*p。这补齐了r1132所缺的同版unchecked宏使用前提，不回填此前下载失败的其它文档。

tupleobject.c111–130的PyTuple_SetItem在写边前要求refcnt==1。因此仅持有一个可见tuple的borrowed pointer，不能不加说明就称其全部child永久稳定。拟选协议只用于从真实直接入口root出发、已由原创建/发布阶段证明完成初始化并已发布的exact tuple路径；type、长度、索引与child非NULL逐点检查。未知或仍在construction的tuple不默认为满足这些前提。

设固定原路径t0[index0]→t1…→td[indexd]→value，不作运行时任意图递归。流程如下：

1. t0直接由本次真实caller operand持有；先为t0取得本次独立strong guard G0。外部operand与G0是两次真实持有，不读取数值refcount来补证。
2. 持有parent guard后，先核parent非NULL且为exact tuple，再核长度与index合法；通过这些检查后才raw取child。先判child非NULL，再检查child是否为路径要求的tuple或leaf，并为下一层child tuple取得Gi。child同时由受保护parent edge持有。自parent检查至child取得所需pin的连续段不得有尚未覆盖的callout，不能把mutable字段的历史读值冒认仍由原parent持有。
3. 每个受guard tuple在其边仍被依赖期间有parent/入口root与Gi至少两份持有。所读SetItem写前refcount门不能改该边。已发布/完成初始化的来源前提才允许援引SET_ITEM的brand-new约束；只凭exact type或对API的命名不能宣布所有unchecked/native raw写已证违规。
4. 最深leaf若有额外strong temp先清owning并退该temp；再由深到浅清Gi并释放，最后退G0。每项释放仍有独立parent edge或外部operand root；不是把最后一次释放难点移到G0。Gi退出后不得再依赖该tuple自身双root保护，不再访问已经退出的leaf/子路径。
5. 同一对象在多个位置/层次被取得也逐acquire计账，不按identity去重。部分失败只释放已实际取得的前缀，实际顺序逆退，未取得slot不虚构DECREF；空路径和NULL child不读取Py_TYPE(NULL)。guard地址/owning slot私有，不交给其它callee当PyObject**。

_PyTuple_Resize889–959需精确区分：**非空**共享tuple先被refcount门拒绝，拒绝会置*pv=NULL、消耗传入owned ref并设错，不是无callback或无effect；shared empty有实现豁免，但922–930只替换调用方*p，旧empty不原地resize且没有child edge。不能写“全部shared resize都失败”。文档还要求不得对已为别处所知的tuple这样做；这里分别记录API前提和实际实现，不把原失败副作用删掉。

exact tuple的Python item-assignment槽与tp_clear为0，traverse只visit，实际dealloc才逐item DECREF。这些有限原文与上述公开接口支持该持有方向；不构成整个GC、任意公开native API、非法引用偷取、Py_SET_REFCNT或raw写地址的全称证明。可能last的清理与所有Py_CLEAR仍不进入final raw段；额外guard可被C API/refcount/析构时机观察，其COMPAT成本还须同版评估。

## 4. 哪些值仍不能借此证明

dict/list/cell/attribute边可在callback时换值；持有其container不等于持续持有先前child。原cellobject.c63–72及140–144会换内容并释放旧值，是具体例子。function本体的strong root同样不冻结func_code/defaults/closure等可替换字段。tuple链走到此类边就停止本协议，后续值要么作为直接实际operand运输，要么另有原独立root/具体析构合同；不默归ROOT_COVERED_NONLAST。

原view/row tuple经guard保持存活，也不证明它仍是当前slot/records的selected row，不证明原source/fact阶段或caller来源。每次准备/回调后仍须以独立原expected与actual metadata共同fresh认可；不得从持有tuple或current快照重pin expected。38处context、六工具逐叶消费、owner、seed/Product/carrier、CORE与P0/import仍需同版组合。

actual成功return的一项owned引用可以持有该原结果，最后合法transfer给调用方；actual错误的GetRaised或3.11 Fetch持有也最后transfer回indicator。这是输出所有权，不是新增永不释放guard。其余临时ref只有在输出root真实存在、仍覆盖该ref时才可归nonlast；不把尚未产生的返回/异常对象或未来callee参数当先前root。E2覆盖policy、arena free、构造半成品、native/source bank退休与全部future slot仍未闭。

## 5. 本批证据与后继

原三边binding、每轮覆盖及finally目录为静态AST事实；官方CALL/argv生命周期、tuple接口及cell反例逐段冻结。所有持有协议是待接线的具体子设计，尚无future ABI/K/slot逐项源码目录，也没有运行时refcount、真实frame/locals或API实验。不能把新增guard计作所有原失败/观察透明，也不计源码缺口关闭。

完整design两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling、真实frame/locals、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator；设计不产生执行授权。r920/r964耗不重放，r967不执行不重问，P06历史0/7不再索材料。

原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持；control1MiB非确认native heap上限。apply本批0/历史11、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：把borrow/guard协议接到实际消费清单

父r1133，产品仍r1106 SOURCE02完整159。先固定实际入口的target/operand参数位置、caller operand拥有者与持续区间，不借G重查或历史checked-target补根；原source/fact/owner及C入口认证另外完成。

1. 对六工具与三incoming列每个新增acquire：直接argv借用、已发布完整exact-tuple路径guard、实际output transfer或MAY_LAST_WITH_CONTRACT；逐site列真实源、部分失败前缀、逆序release、迭代覆盖、原观察及slot成本。mutable dict/list/cell/attr边不归tuple协议。guard最后退必须仍有独立operand，不靠瞬时refcount或新增持有自证。
2. 将原C callable/argv与actual standard call分开，固定一位置/零参的borrowed C栈buffer不设OFFSET；完整kwargs/default/错误、actual N/ABI、D2、free/异常policy、source bank退休与共同N4/COMPAT仍待。原Pop和precommit restore-before-free保持。
3. 汇合owner、seed/Product/carrier、38处context/foreign、CORE与P0/import和全部caller。完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

原保护/预算/禁项保持；正常库/服务/权限/平台/Git不操作，旧失败下载不重跑，r920/r964耗、r967不执行不重问、P06历史0/7不再索材料。B001/B002 open2，Goal active，非只剩验收。
