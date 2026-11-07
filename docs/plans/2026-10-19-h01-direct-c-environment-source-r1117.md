# r1117 原 module 直达 C 的环境取得子方向

r1117完成原module直达C的环境取得子设计：已知原module直接进入原native body时，公开PyEval_GetBuiltins可取得该实际帧环境，再在同帧直接New产品函数；不替换原builtin/EvalCode。当前三源仍是两参Python constructor，真实native入口/attempt接线未闭，getter不证明caller或原pair。B001/B002继续open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

本批29份完整输入pairs，每侧917391B；继承9份既有官方CPython3.12.10文本412021B，无新下载。26段准确原文、三源AST核同；纯PE字节读取确认本机同一DLL的6个列明公开named exports，无加载/链接/地址定位/native执行。核父r1116封存44文件6778139B，完整159源码7956657B及保护56同18lstat缺席。静态依据不是runtime原frame/get_code证明，不泛化其它版本/构建/ABI。

新机制给出此前“先持X”的一个条件读点：已知原module活动帧直接调用普通FASTCALL|KEYWORDS原C入口时，已核generic/specialized成功分派不新建Python帧，getter读该实际帧环境。getter返回borrowed根须立即强持；若经helper/其它Python函数或无Python帧则所得不同，不能由G/code相同推回X。没有读取真实frames/locals、注册hook或执行这条C路径。

当前control/scope/registration尾调用16235/15423/7607仍是两参数Pythonconstructor，分别10/8/6原factory行；本批没有安装原native入口。getter不返回caller/code/attempt证明，相同原C pointer/self/ACTIVE也不自动建立调用来源。原control两次环境表达式独立，G缺席/None/module/dict/其它值分列，不把getter代填原pair；不接受外部值登记、不first-wins。

在已闭direct-C来源前提下，先持当次帧X、保原第一G读取，再于实际创建点独立按G有键/归一/缺席规则持expected，仍在该帧直接public New产品函数并联合核actual返回。G不换scratch、不预插X、原module构造仍由builtin/EvalCode完成，R2否决保持。MAKE_FUNCTION还会后填closure/annotations/defaults/version，不能因New相同就称最终metadata一致；原pair/STORE/effect、额外调用与引用/异常delta仍待。

后继仅处理I001/I002：原native root如何到具名原调用消费点、unknown可写alias尚未执行、actual attempt怎样关联且不first-wins。I003/I004的P0/tools/rows/owner/原STORE/del、catalog第4目标与必要ABI、23P2/CORE第24项、D1/三D0/D2/N2/N4/COMPAT必须在同一完整设计接线。条件子机制双审0不代表完整设计或入口实施批准；不再重复API环境表。

本批candidate/compile/import/执行/模型/native build/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal未完，非只剩验收。正常库/服务-dist/ACLsettings/真实平台/Git保持；原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523；r920/r964已耗不重放，r967不执行不重问，P06无材料0/7不再索取。

# r1117：原 module 帧直达 C 的环境取得子方向

状态：有限静态子设计。选择一个**有前提的环境取得/构造机制**，不选择或批准完整入口安装。原 r1111 helper→原 builtin exec→原 EvalCode 的 same actual code/G 路线保持，r1116 对 R2 的否决保持。产品源码仍 r1106 SOURCE02 完整159文件、7956657B；没有新增产品候选、compile/import/运行、模型、native build/load、QA 或诊断。

## 1. 本次新增的具体结论

不必先拿到隐藏的原 module function M，才能在**已知原 module Python 帧仍活动、直接进入的 C body**里取得该帧的环境。CPython v3.12.10 的公开 `PyEval_GetBuiltins()` 返回当前 Python 帧的 `f_builtins`；原 module→普通 PyCFunction 的已核成功分派不新建 Python frame。因此这条 C body 可以先独立取得/强持原 module 的环境 X，再在该实际帧下直接使用公开 New 创建原 P0/P2 所需函数。

这是真正的条件：**入口必须已被独立证明来自该次原 module 的直接 C 调用。** API 不返回 caller/code/attempt 证明，也不会查 G 来恢复原 module；一个外来 Python 帧同样可以调用它。仅有原 C pointer/self/state、相同 G、阶段 ACTIVE 或“处在一次 loader attempt 内”，均不能自动证明调用者就是原 module。这里没有用同一 API 自证这一前提。

相对 r1115 的“已经可信持有 X 后再建 carrier”，本次给出在仍保留原执行器时取得 X 的一个具体 C API 读点；相对 r1116，不再用 public New 替换原 module 构造，因此不重开其 doc/CREATE 差异。**真正的直接 C 入口尚不存在于当前三源，如何将原 native root 交给实际调用点仍未闭。** B001/P0 实际创建运输和 B002/完整来源/同阶段消费仍 open2，四项完整批准均 False。

本批29份必要完整输入pairs，继承9份既有官方3.12.10文本，无新下载；三源仅解析 AST。只用 PE 字节读取确认本机同一 DLL 的6个列明公开 named exports，没有加载、链接、地址定位或 native 执行。该证据不冒认 ABI/行为或其它版本支持。

## 2. 当前源码与拟议入口必须分开

`CURRENT-INGRESS-01.json` 保存三段当前尾调用的完整原文。control/scope/registration 分别在16235/15423/7607调用 `_AUTH_BOOTSTRAP_CONSTRUCT`；当前源定义是有两个参数的 Python FunctionDef，第一参数为原 NamedExpr context RHS，第二参数分别有10/8/6行。它们**不是已经接通的直接 C 入口**。本批不靠运行时 lookup 推断这个 alias 当前究竟是什么，也不读取运行中的 frame/locals。

同一文件保存 control 原环境 pair 的两段准确表达式：先 `dict.get(G,'__builtins__')`，后 `object.__getattribute__((lambda: None),'__builtins__')`，位于原 context 的零基路径 `[3,19,21]`。这里引用已有定位以限制本次 API 接点，不重做 prefix/24 factory 库存。r1113 的第三 actual native 参数仍是未来私有2→3 ABI变化，没有因本子方向自动发生。

直接 C 子方向将来若采用，必须显式增加或修订**在原 module 帧活动期间的原调用点**；不能把 post-helper C 调用重命名为直接 module 调用，不能把当前 Python constructor 的第三实参当作已经提供的 C 原入口，也不能先从 G 读取现有 context 再登记 expected。

## 3. API 与实际分派链

| 局部链 | 已冻结的官方源码事实 | 有限保证 |
| --- | --- | --- |
| `PyEval_GetBuiltins()` | 取当次 thread state，转 `_PyEval_GetBuiltins`；后者有当前 Python 帧时返回其 f_builtins，否则返回 interpreter builtins | 返回 borrowed 根，不查询调用者身份，不读取 G，不返回 frame 对象；None 是一个实际对象，不能当 NULL/缺席 |
| generic CALL→PyObject_Vectorcall | 普通 Python function 有显式 new frame 路径；非该分支调用实际 callable 的 vectorcall | 需实际 callable 确为所持原 native 根，不能只看名称 |
| plain `METH_FASTCALL|METH_KEYWORDS` PyCFunction | constructor 选 cfunction_vectorcall_FASTCALL_KEYWORDS；它取原 method pointer/self，进入 C body 后退出 C recursion 计数 | 该已核成功分派本身不新建 Python frame；不是对任意 callable、METH_METHOD 或外来 vectorcall 的保证 |
| specialized builtin fast call | exact PyCFunction/flags 检查后直接以 self、actual args/kwnames 调 C pointer | 同样不因 C 调用自身创建 Python frame；不能把 specialization 作为必须出现的运行条件 |
| instrumented CALL | 可能有原监控及 C_RETURN/C_RAISE/结果清理等分支 | 不排除合法回调；回调尚活动时调用 API 取得的是该回调帧环境。回调已返回后才恢复原外层 frame，不能把两时点混同 |

本节限定的是已证明实际原 native callable 被正常分派、C body 还没有再进入任何 Python callee 的时段。普通 kwargs 检查、递归失败、错误格式化、monitoring/PEP523 等不是被抹去的步骤；本批没有注册、禁用或修改任何全局 hook。原 native pointer/flags/self/state/phase/metadata 联合核和 D1 仍需完整设计。

| C body 此刻实际由谁包围 | API 所得根 | 可否直接称原 module X |
| --- | --- | --- |
| 已独立证明的原 module 直接调用，module 帧已初始化且仍活动 | 该 module frame 的 X | 仅在此已证明前提下可以 |
| 原 helper 调用 C body，module exec 已返回 | helper frame 的 B | 不能；这正是 post-helper 缺口 |
| 原源码 constructor Python 函数调用 C | 此次 constructor 函数实际帧的环境 Y | 须单独证明 Y 的实际来源，不能由 code/G同一直接推出 X |
| C body 先调用另一个 Python 函数，再由它调用 C | 那个实际 Python 帧的环境 | 不能套用外层 module 的结论 |
| 没有活动 Python 帧 | interpreter builtins | 不是原 module 来源证明 |

`PyEval_GetBuiltins` 的实现内部使用解释器 frame，不等于本工程获准读取真实 frame 对象、locals、停点或诊断数据。本批仅阅读冻结文本和 PE 字节，未调用这一运行 API。未来产品只取得此 API 的 mapping 根，也不能据此越过原 caller/source 门。

## 4. 不能把环境 getter 当作原 pair

设 X 为已知原 module 的实际帧环境，G为已持的原 globals；下表仅说明**两表达式之间无变动的局部例子**。原实际求值若有 callback/改键，必须分别按各自时点处理。

| G 的当次 builtins 状态 | 原第一次 dict.get 的结果 | 原 lambda public New 的环境结果 | 单次 PyEval_GetBuiltins 的结果 |
| --- | --- | --- | --- |
| 缺席 | None（另有 missing 事实） | 缺席时回 X | X |
| 显式 None | None（present） | None，不能错误回 X | X |
| exact dict Y | Y | Y | X |
| module Q | Q | Q 的实际 module dict | X |
| 其它非 module 对象 Z | Z | Z | X |

因此本次**不采用**“把 getter 返回值代填 context 两项”的简化，也不从 getter 推断 G 的当前 key/value。第一项和第二项的实际来源、缺席/None/module归一必须分别保留，r1114 `(None,X)` 的条件路径继续成立。

## 5. 选择的有前提 C 局部顺序

这是一段未来完整 P0 producer 的子机制，非独立可执行模型。其前提是原 module→实际原 C body 的来源门已经由别的设计闭合，且 code/G/原标准 API 与预备 key/原 native callable均已强持；本批不能通过执行该子机制来绕过入口未闭。

1. C body 在不再进入其它 Python frame 的原阶段，用 `PyEval_GetBuiltins` 取得 borrowed X_frame，立即以独立 owned 引用强持。它是**该次实际帧根**，不是任意 Python 入参，不从 post-CREATE 的函数字段补 expected，不记录为 first-wins module 槽。X 的 identity 被持有不代表映射内容恒定。
2. 在原第一表达式位置保留当次 G 的实际 dict.get 语义：完整 exact-str key 前验后再 lookup；显式 present/missing 与 None 分开，实际返回值 V1 立即强持。固定 key 的构造/分配在此前准备，不能在最后读窗口中隐藏回调。原第一结果不能用第二次 G 读取覆盖。
3. 在原 lambda/目标函数实际创建点另行核 actual G/phase，并按 public New 的当次规则独立得到 expected builtins：当次有键取实际值，module 值走已核原 `PyModule_GetDict` 成功 C 路线；缺席才用所持 X_frame。该 getter 成功路返回原 dict borrowed，不走未知 module.__getattribute__。在 exact-str/GIL/无分配或任意 DECREF 的局部窗口内完成所需读取与强持；若允许回调边插在其中，必须重新给出具体顺序，不能用“字段最后相同”代替。
4. **仍在同一实际原 module 帧下**直接 `PyFunction_NewWithQualName(actual_product_code, original_G, actual_qualname)`。这是生产原 lambda/P2 函数的 public 构造，原 MAKE_FUNCTION 本来也使用 public New；不是 R2 的 public module 构造替换。public New 自身仍读 name/builtins，再分配、CREATE；G仍是产品 globals，不使用 scratch 替代、不预插X、不改原code/const。原 MAKE_FUNCTION 随后另填 closure/annotations/kwdefaults/defaults，并写 code.co_version；New 初态一致**不等于原最终 function 字段或版本已经一致**，这些后填步骤仍须完整 producer 设计。
5. C 直接持 actual 返回，使用第3步之前已独立持有的 expected 和原 code/G/name/qualname/defaults/kw/closure/annotations/version/vectorcall 等联合检查；不能 CREATE 后从 current 产物反向造 expected。原 lambda 的 actual builtins 读取、实际 first/STORE/effect与 context 组合仍须保持原时点；expected 不取代该实际 RHS。CREATE 后的合法回调、字段变化、释放再入及失败都保留为完整设计义务。
6. 下游若一直在该直接 C body 内 New，无须为本局部缺席 fallback 再创建 H carrier。若离开到其它 Python frame 或延迟到 exec 返回后，不能沿用这一保证；才可另用 r1115 已持X条件的 carrier，并另审额外 CREATE/帧、来源与预算。不能把两个局部机制拼接后宣称整个 P0 已闭。

这段顺序保留原 executor 对隐藏 module M 的创建，故它的 doc=None/原 CREATE 不变；新增私有 C 入口和 C 内部准备仍是明确 delta，绝非全透明或免费。原 Python primitive 调用/监控事件、原 pair 求值位置、强引用存活、原异常/首对象/tb/计数均须全量设计，不在此静态子机制中冒认保真完成。

## 6. 不能省掉的直接入口义务

| ID | 完整设计必须提供的证据 | 本批状态 |
| --- | --- | --- |
| I001 | 原 module 调用点在调用前已经握原 native root；unknown alias/descriptor/slot不能先运行后才拒绝 | **open**。当前三源仅有 Python constructor 两参数尾调用；普通可写 G alias 不是独立来源 |
| I002 | 本代 actual attempt、source code及真正原调用之间的关联；重入外来调用不能占原槽或消耗合法调用 | **open**。getter/原 pointer/self/ACTIVE不足，first-wins、任意register(subject,X)不采用 |
| I003 | C 取得实际 tools/rows/owner/context、原两次环境结果与原 sourcecaller 参数运输/STORE/del；catalog第4目标及必要0→1/2→3 ABI | **open**。不能拿 current G 的 context 或用户传入 tuple 当原 RHS |
| I004 | 23 P2/CORE第24项、联合metadata/三D0/D1/D2、初导入/reload/direct-loader、N2容量/N4释放/版本打包 | **open**。没有因一个 API 读点变成已实现 |

I001/I002 是本子方向最早尚未闭的接点，属于既有 B001/B002 的具体义务，不新造一个“只有验收没做”的口径。原方案不要求证明任意 caller 身份，但要求具名原调用点/原 producer 的实际来源；不能为了堵 I002 扩成整个解释器防篡改，也不能反过来删掉本来要求的原 RHS/STORE 事实。

## 7. 文件、引用与后继工作

未来若采用，原 native bootstrap/loader 仍沿 r1111/r1112；三个源模块须明确修订原 module 活动期入口与原 P0 prefix，并与 native C producer 在同一完整设计中接线。当前没有创建任何 native 产品源或修改三模块，第三 native 实参和 catalog 第4目标均未实施。后续不得仅实现 getter 或把一个原 C alias 写入 G 就运行模型。

X_frame/V1/当次 normalized expected、原 code/G/key、actual function、tools/rows/owner和下游参数各有独立的取得与 owned/borrowed 路线。任何可能最后释放、分配/回调的准备在 metadata 短锁外，最终重核后才实际消费；原 callback 可合法再入，不能锁跨它来“保证”X。C引用环、失败/退休/reload回收与控制字节预算仍待N4。public getter/模块 API 有 named export不等于 abi3；仅3.12.10静态依据不把当前>=3.11合同收窄。

**下一批直接处理 I001/I002 的一个具名入口设计，而不是重复 API/环境表。** 该设计需解释实际 native root 如何到原调用消费点、为什么外部可写 alias 尚未被调用、怎样关联 actual attempt 且不 first-wins。若没有具体证据，就如实否决该入口；不要先实现纯 getter 再拿局部绿例冒认来源。

正常库/服务-dist/ACLsettings/真实平台/Git保持；不 profiling/读取真实frames-locals/停点/定点计时/新增异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964 已耗不重放；r967 不执行不重问；P06 无材料0/7不再索取。原17lease/1449slot/406尾/五S002与case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。

本批新增候选/compile/import/执行/模型/native build/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal未完，非只剩验收。

# 下一批：原 module 到 actual native 的具名入口与 attempt 接线

父r1117，源码仍r1106 SOURCE02完整159。原builtin/EvalCode路线保留，R2拒绝不重开；条件direct-C环境机制不能代真实入口。

1. 直接给出一个I001入口设计：原native root从r1112 actual C创建/独立state持有如何进入原module的具名实际调用点；第一次actual调用前如何排除unknown alias/descriptor/slot。当前三源两参Python尾调用不得冒认已有C入口，不能先执行current目标再后验拒绝，也不把普通G别名当独立expected。
2. 同时完成I002：关联该次same actual code/G与原attempt/实际生产消费，处理合法audit/CREATE/监控/重入时的外来调用，避免抢占原槽、耗尽原合法调用或first-wins。getter/相同C pointer/self/ACTIVE不证明caller；原方案不要求识别任意caller身份，也不能凭此取消具名原producer来源义务。
3. 可行时在同一完整设计接I003/I004：P0原两次环境结果与tools/rows/owner/context/STORE/del，catalog第4目标及必要ABI，23P2/CORE第24项、原actual return与STORE、联合metadata/三D0/D1/D2/N2/N4/COMPAT。MAKE_FUNCTION后填与原版本/异常/引用/预算具体列全。没有实际入口证据就否决该入口，不只补一个getter再跑模型。
4. 完整设计双审前不生成产品候选，源码准入前不compile/import/运行候选、局部模型/native build/QA/旧绿F001或诊断。原保护/预算/禁项和完整Goal active保持。
