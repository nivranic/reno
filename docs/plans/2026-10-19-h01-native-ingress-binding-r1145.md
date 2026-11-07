# r1145：原生入口角色与独立 ingress 调用协议

r1145分清十组原生入口讨论角色，选择独立Ingress实例/vectorcall与C(A)条件绑定，不合并Consumer Factory。单module_lifetime payload、独立type根、静态offset member、递归enter/leave及result/first四出口分列；offset实际会留下可观察descriptor，不冒认返回offset或跨ABI安全。原C/A lookup、J逐次独立、原P0时序和真实caller仍须接线；角色表不是完整NewEx或state清单。B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

五份既有tagged v3.12.10源392419B/20区间/八完整设计固定；250个原Call=226候选借用+7原append+3cell reply+14owner producer的计数只核旧分类来源，不作语义安全或native实例数。r1144旧26schema保持历史快照；本次新增1payload及2state语义角色的物理合并/整图总量未判。入口source、A/J内部字段、proxy/loader、全state/SCC/NULL/free/build/COMPAT与预算仍待。

本批仅静态文档/read/hash/AST/diff，没有新下载、产品candidate/compile/import/执行、native build/load、模型、QA或诊断；apply0/历史11、源码缺口关闭0、产品pass0。159源码/保护56同18缺/原限额及禁项保持，r964迟到单次授权不重放；正常库、服务/dist、权限与Git未操作。完整Goal仍有工程，非只剩验收。

# ADR-r1145：原生入口角色与独立 ingress 调用协议

状态：有限绑定子设计待正式双审，不生成产品候选或授予执行准入。选择独立 Ingress vectorcall 与 C(A) 条件路线，只具体化 PL reserved C，不合并 Consumer Factory，不把 ABI 选择冒认完整 P0/source/caller/N4。旧角色库存与提议原字节保留；该设计只修订 r1112 普通 NewEx 默认方向中的 ingress C 一项。

## 1. 三种方向与所选条件子路线

| 方向 | 收益 | 实际成本/本ADR处理 |
| --- | --- | --- |
| 自有独立 Ingress，vectorcall + PyVectorcall_Call | C 无可写 m_module，不经普通 Python wrapper；正常栈实参不新增 tuple/dict 适配；递归在本 vectorcall 内统一负责 | 本ADR选择待审；新增 heap type、实例、module lifetime、vectorcall scalar、offset member descriptor；全 API/layout/build/跨版本仍须完成 |
| 保留 PyCFunction_NewEx | 现成 FASTCALL_KEYWORDS、递归及框架检查 | m_module 可写、tp_clear=NULL，任意后来写入的边与元数据仍须完整处理；本ADR不选此 ingress 形状，未否决其它原 NewEx/捕获 |
| 自有仅 tp_call | 不需 vectorcall offset 成员 | 栈调用会先转 tuple/可能 keyword dict、分配/清理及递归；不是不可能或非法，只是新增准备/callback/错误前缀比本ADR多；不以提前拒绝容量不足删原调用域 |

这不是已经存在的入口 ABI。r1112 的 flags 仅是普通默认示例，未给出此 ingress 实例的参数合同；不得据此声称本方案与某个已实现 ABI 完全相同。r1139/r1141 的 Consumer Factory 仍维持自己的 type/tp_call/module_lifetime，二者是不同角色和不同实际创建返回。

## 2. 原创建、固定 layout 与拥有

每实际原 native module 代创建一个独立 IngressType 和一个实际 C 实例，type 由固定 spec/原 object base/原 module 的实际 FromModuleAndSpec 返回持根。IMMUTABLETYPE、DISALLOW_INSTANTIATION、HAVE_GC、HAVE_VECTORCALL，不设 BASETYPE、实例 dict/weakref/自有 Python methoddef 或 payload setter。标准继承属性/类型级 __module__/inspection 仍按实际类型处理，不声称不存在。

本方 PyObject payload 仅 module_lifetime，一笔独立 owning；正确 Init 的实例→actual heap type 是另一笔。固定 vectorcall function pointer 和 phase 是 C scalar。参数 A/J/G/K 不放进这个共享实例，不缓存最后调用、最后 tools、owner 或 environment。

原 Py_mod_exec 的实际 module 参数先按 r1142 取得 creation_module_guard，跨 type 创建和 Ingress GC_New。type actual newref 保 C local，state 的 type 边另取得。GC_New/Init 成功且 untracked 后，先 module_lifetime=NULL、phase=INIT，再把 creation_module_guard 一次 move 入字段并清自动槽；固定 vectorcall 指针此时赋原静态 C function。正确实例 type 根成立后才条件 nonlast 释放 type local。实际 partial/未 move 各自负责，不把未来实例根倒推到分配前。

所有可回调准备后 fresh 原 state/代/完整关联，通过才 ACTIVE/Track。Track 后即使未 return/发布，也可能被外部保留，后续失败只释放本方引用。state 仅在合法空 slot 独立取得 actual C 引用；这里的 L.native_ingress 仅指 r1121 十字段中的既有 L.C 语义角色，由该字段独立持有这个 actual return，不新增第十一字段，也不改原十字段取得/清理顺序。C state-held、L-held、public alias 的 STORE/effect 不混合，不从 current 属性反建 expected。完整准备顺序/空 slot/partial 的源码仍待，不借未选的 state 字段作既成事实。

## 3. vectorcall 与 offset 是明确的实现和观察成本

type 的固定 tp_call 选择公开 PyVectorcall_Call；Py_tp_members 放静态只读 T_PYSSIZET 的 __vectorcalloffset__ 定义，offset 来自本方结构内 vectorcallfunc 字段的 offsetof。spec 的正 basicsize 覆盖整个实例，itemsize=0；不在 PyType_Ready 后临时改 slots/offset。实例指针在 Track 前固定且有效；不可调用阶段在原 C vectorcall 内拒绝，不将指针清 NULL 导致另一套不受控分派。

同版 typeobject.c 按该 member.offset 设置 tp_vectorcall_offset，并要求 HAVE_VECTORCALL 时 offset>0 且 tp_call!=NULL。member 实际被建成 descriptor；PyDict_SetDefault 可能留下同名旧项，完整 type/spec 创建必须核无冲突和 actual definition/effect，所有 callback 后 fresh。不能把静态声明或 flags 当作实际 effect 证明。

同版 FromSpec 返回前只删非零 __weaklistoffset__/__dictoffset__，不删 __vectorcalloffset__。故保留该只读 descriptor 的可观察性；普通 T_PYSSIZET getter 在相应 layout/build 下从此位置读数并构造 Python int。它不是“返回 offset 的 Python 属性”，也不保证对所有 ABI 正确；function pointer 与 Py_ssize_t 的大小、对齐、表示和可读范围仍须实际 build 论证，不偷称可移植。此观察不是秘密、不是原来源证书，也不为隐藏它修改全局环境或 type dict。框架 descriptor 内部 d_type/name 等另计。

## 4. 单次实际调用、参数及来源边界

拟议新 source 的 module 原位使用保留名调用 C(A)，A 为 _AUTH_NATIVE_ATTEMPT 的实际 lookup 结果；语义形状选恰好一个 positional-only A，不接受实际 keyword；kwnames=NULL 或长度为0的 tuple 都表示无 keyword，不能只接受 NULL。nargs=PyVectorcall_NARGS(nargsf)，不写 args[-1]，不把 OFFSET bit 当实参数量。不要求特定 specialization，不接受外部 target/G/K/expected 注册参数。

catalog/control/scope/registration 的固定目标只能由独立原 A 的受审 code/G/source 关联选择；A 运输视为不可信，原 state-held source 及 producer 关联独立认可。不能因为 exact type/相同 A 就认为实际 caller 是原 module，也不把此 API 变成通用登记入口。外部实际调用同一 C 的 J2 与原调用 J1 独立；本方不凭全局 last/first-use/秘密 token 冒认 caller。

vectorcall 开始只设 scalar/NULL 自动槽，统一用原公开 Py_EnterRecursiveCall(" while calling a Python object")。enter 失败：本层尚无 guard/业务 effect，沿实际错误返回，不 Leave；enter 成功：每出口恰好一次 Py_LeaveRecursiveCall。PyVectorcall_Call 适配自身不替本方做这笔递归检查；不得既省略，也在 tp_call shim 再增加第二笔。普通 __call__ wrapper 若进入，仍由该适配最终进入同一 vectorcall；框架本来的额外递归成本另列，不声称所有调用形式计数相同。

在仍由真实 callable operand 和实参缓冲持有的区间，raw 核合法本方实例/固定字段后取得独立 call-module guard；module_lifetime 贯穿整次 body。检查 actual arity/keyword，拒绝以本方固定 TypeError/其实际构造失败处理，J/P0业务0。合法参数从 actual argv 取得 A guard；不借 current alias。借用到 NewRef 之间无 callout，完整 caller/引用/build 前提仍须证明。module guard 从持续 immutable module_lifetime 取得并结清，可在匹配包络下 nonlast；A guard 是否 nonlast 按实际 operand 持根，不从 current refcount 采样推断。

state accessor 仍放可回调准备区，立即按 r1140 指针/indicator 四组合分类；仅非NULL且无error可访问。每一可回调准备/分配后 fresh module/state/C/A/代/实际 source；不重采 expected。按原 r1124 每次建立独立 J，由此次 C local 持有；J 分配/准备失败与原P0业务错误分列。A/J 内部字段和全 P0 算法仍待，不能仅因这里列 ABI 就称已完成。

## 5. 环境与 caller 义务不被接口替代

本 binding 自身没有新建 Python frame；tp_call 适配和 vectorcall 是原 C 调用链。合法监控、递归错误或其它入前回调仍允许，不宣称整个调用前窗口无 callback。已独立证明原 module 直接进入时，在它仍活动且没有 Python callee 活动的 C body 指定点，以 PyEval_GetBuiltins 原公开 getter 取本次 actual environment 并独立持根；不能在 maker/helper 活动时取环境后冒认外层 X。

若外来 Python frame 调同一 C，实际 environment 是那个 frame 的 X2，getter 不给 caller 证明；J2 不冒认 J1/X1。完整实际调用点/新 K/LOAD_NAME/原 code-G-A 与每次 J 关联仍未闭。新 C(A) 的 source 位置必须与原 P00–P12 时序共同设计，不能一边保留全部原 P0 effect，一边又由 C 重跑一次；此项是完整源码准入前实质工程，不是 ABI 文档自动完成。

## 6. 结果、错误和四类退出

| 出口 | 本层真实拥有与退出 |
| --- | --- |
| recursion enter 失败 | 无本层 module/A/J guard；保 actual error，直接 NULL，无 Leave |
| arity/keyword 或准备/fresh 拒绝 | enter 已成功；已有实际 guard/temp 全部 detach/结清，保本层 actual first；Leave 后最后还原 first，仅 scalar NULL |
| 原 P0 成功 | 真实结果由 C owning local 跨 J/A/module 及本层 temp 清理；清理成功且无indicator才 move 返回；Leave 一次；不是固定吞成 None |
| 原 P0 失败/已存在 first | 先私有 C local 保 actual first，结清全部本层 owning/partial/free，Leave 一次后最后还原；原业务 handler/finally 仍各自保留 |

原成功且本方实际可设错 cleanup 产生错误时，先取该 actual first，结清 result 及其余责任，Leave 后最后还原；不同时返回 result 和 pending error。Py_LeaveRecursiveCall 的 scalar/无callback条件及 3.11/3.12 还原 API 分别核，不把文档当实际 build 证明。外层适配 _PyStack_UnpackDict_Free、框架 CheckFunctionResult、调用方操作数清理在 C body 返回后，仍是独立责任，不在“最后只 scalar”里冒称已完成。

## 7. GC、有限环和成本

Ingress.traverse 逐边 visit actual heap type 与非NULL module_lifetime；tp_clear=NULL，module_lifetime 在普通退休时不切断，确保旧 C 能拒绝且其 state 内存存活。dealloc 先 UnTrack、不可调用，detach module payload，保已有 type 责任，释放 module temp、固定 GC_Del 实例，再释放 saved type，free 后不读 self。既有 first 沿 r1135 结清后还原；一般 NULL/type/free/allocator 仍须逐 site 证明。

module/state→C→module 的新增直接有限环，由 m_clear 或不先 m_clear 的 m_free 先退休/detach state 自有 C 边作 breaker；state 的 type 边、type→module、type dict→offset member→type 均另计。L.native_ingress 的边归 L lease/retire 协议，不因清 C 自动退休共享 L/A/J。外部持续 C root 延长 module 寿命是合法持有，不强清 lifetime 伪称回收。只关闭这些指定有限边的条件路线，不关闭整图 SCC/N4。

新增已知 schema 增量为 ingress.module_lifetime 一项、state→actual IngressType 及 state→actual C 两个语义角色；是否与先前泛称 state 根物理共槽须完整 state 表判定，不能直接给旧26加3作去重后的全图总数。新增实例隐含 type、vectorcall scalar、phase、静态 member、框架 descriptor、每 call module/A/J local 及 actual result/first 另计。没有实测 sizeof/峰值，不把 control1MiB 当 native heap界。

显式兼容变化：新的私有 C(A) ABI、独立 callable/type/identity/repr/inspection/__module__/weakref 行为、只读 offset descriptor 和不可公开构造；普通 FASTCALL 栈路径无本方新增 packing，tuple+kwargs 调用仍有框架 unpack 分配/释放/失败，不能报零成本。原 builtin exec/PathFinder/loader/proxy/Consumer Factory 不因本选择更换。

## 8. 准入与后续

此提议只选择未来 ingress 绑定的待审方向，不宣称 full ABI/caller/metadata/source/P0/A/J/state/NULL/COMPAT/build 已闭。实际 >=3.11 未收窄，3.12.10 文本不代表 3.11、其它版本或平台已获准。不得由本方设计自批完整设计/source/execution。

当前 B001/B002 open2；full_design_approved/source_approved/execution_admitted/complete_Goal 四False。源码仍 r1106 SOURCE02 完整159，未安装。原限额和禁项保持；新下载、产品 candidate/compile/import/执行、native build/load、模型、QA、诊断、正常库/服务/dist/ACL/settings/平台/Git 操作均0。失败下载不重试，旧单次授权不重放；P06无历史材料不再索取。

# r1145 后继工程

1. 把独立 Ingress binding 与原 wrapper/actual L/source 的真实参数流接通，按实际新 K 逐点核 C/A 的 module LOAD_NAME、C(A)位置及原 P00–P12 的唯一 effect 执行；不以 ABI 设计认领 caller 或重复原P0。
2. 对 PathFinder 三协议及 loader.exec_module 包装明确真实 callable/receiver/type/字段、原一次委派和退休/恢复路线；原T0捕获、PyMethod_New、carrier slots、Consumer Factory分开。
3. 补全 state 与 A/J/Product/D/seed/carrier/owner 真实字段、每 acquire/hold/move/release/clear 和完整 SCC/NULL/free/actual first；旧26角色不是完整物理 census，本次增量不能直接当整图去重总数。
4. 关闭实际版本/build、offset读表示、公开成员/调用形态COMPAT、预算和全source/D1/D2/N4；完成完整设计双审后才能生成产品候选，源码准入后才有后续执行。

只复用 /root/r924_independent_review，不新增代理。保护56 regular/18 lstat缺和原限额保持；无新诊断/真实frame-locals/全局hook/allocator/服务/真实库/ACLsettings操作；终态失败下载与已耗单次授权不重放，P06用户无材料不再索取。
