# r1128：真实参数与原工具叶消费

r1128提出六工具新增mandatory positional-only native consumer真参数，并在原actual callable/operand消费点由同C body先验后调用；只追加末尾TMD不足以阻止此前未知effect。三处cell小try选择denied reply/late matcher/try外accept，保原getter异常自然处理；owner实际producer/真实genexpr、全调用方接线、未来ABI/D1慢路/D2/N4及COMPAT仍未闭。B001/B002保持open2，完整设计/source/execution均False；源码仍r1106 SOURCE02完整159，Goal active未完。

23完整pairs每侧1608294B、六工具完整AST及250call/163compare/79raise/33for/3catch/11attribute/1gen/2subscript静态库存、四原code ABI前缀与15段官方原文分列。exact immutable比较/新list append/dict_items不冒认未知协议；可变dict四查询点与audit后的captured callable才逐消费核。固定完整位置参数D1正常路径与allocator慢路、PEP523前后D2分开；未查allocator后端，不报告无条件D1/D2。

产品159源码7956657B及保护56同18缺保持，本批candidate/compile/import/运行/native build/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002与原预算不放宽，control1MiB非确认native heap上限。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964耗不重放、r967不执行不重问、P06历史0/7不再索材料；完整Goal非只剩验收。

# r1128：真实参数运输的 native 叶消费门

父 r1127，产品仍为 r1106 SOURCE02 的159文件、7,956,657 B，未安装。本批给六工具内部消费的具体修订方向及三处小 try 的错误协议；没有产品候选、future K、compile/import、native build、模型、QA或诊断。B001/B002 保持 open2；完整 design/source/execution/Goal 均 False。原算法保留与源代码逐字节不变是不同要求，本方案显式改变私有参数及表达式，不将它描述成透明替换。

## 1. 具体缺口和处置

原 function_row 的 return 按原顺序先调用 get(function, '__code__')，再调用 get(function, '__defaults__')，之后转换 keywords/cells。第一个已知原 getter 的合法 audit 可改变共享 get/sequence/error cell；只有入口检查或末尾 TMD，不能阻止后一次直接使用改后的 callable。这里的原 getter 本身是已知 C 实现，audit 发生不等于 getter 来源未知。module_row 没有这些敏感 function getter，不给它捏造同一 audit 轨迹。

另有四个直接查询点：check_view 的 mappingproxy data.get（264）、keywords dict_get（305）、binding dict_get（344）、root dict_get（371）。先前完整扫描 key，并不冻结可变映射；若之后插入碰撞的非 exact-str stored key，使用 exact-str name 查询仍可能调用 stored key 的比较协议。当前全键类型核对和实际查询须在同一持 GIL、不可再入的 C 段完成；不能把已读 name 为 exact str 当作充分条件。本批未构造碰撞对象或运行这些轨迹。

| 方向 | 决定 | 理由 |
| --- | --- | --- |
| 只保原 Python body，末尾追加 TMD | 不作完整消费修复 | 最终拒绝不能撤销此前 unknown target/hash/eq effect |
| 全部六 body 改为 C 重演 | 本批不选择 | 可以形成独立设计，但会改 Python 函数/真实 generator/观测边界，不能只声称原 body 保留；不是对全部 C 方案的否决 |
| C 直接绑定真实 consumer 参数，Python 保算法和原观察，逐叶交 C 校验并消费 | 选为后继修订方向 | 消费器不从可变 closure/global/helper 属性取得；unsafe actual call 与其门在同一 C body，明确私有 ABI、分支和异常变化 |

## 2. 参数来源与私有 ABI

六工具分别增加首个 mandatory positional-only 参数 `_catalog_consumer`。原五个单参数工具由1位置参数改为2；make_view 由0位置参数+6个 keyword-only 参数改为1位置参数+原6个 keyword-only 参数，原默认值仍来自实际 K 的原 constants。原参数名和原取值顺序不借此改变。这里只描述签名契约，未生成 Python/C 候选或编译未来代码。

新增参数由已持有原 native state/root 的 C 调用方直接放入真实参数缓冲；其 exact C 类型、固定调用槽和本次 state/A/Product/J 关联在调用前验证。C 对象无公开构造、可写实例字典或可写调用槽，不从 observed pointer/current alias 补 pin。函数体不 STORE、DELETE、global/nonlocal 声明这个参数，也不通过它的可写属性选择入口。未来 K 必须实核它是实际 LOAD_FAST 参数、没有变为 cell/free/global；不能仅以 AST 拼写宣告未来 ABI 已验证。

调用方先保原 actual tool/callee 与原实参观察，再准备新增参数，完成可再入准备后作最终来源/phase/metadata门，立即调用原实际选中的函数。原 Product 提供独立 expected，参数中的 current row、callee 或 reply 不可反过来登记 expected。任意外部代码拿到 consumer 也不能覆盖本调用的来源、消耗 first-use 资格或替换最近结果；入口只按本身原根与 actual operands 作安全消费，不证明任意调用者身份。

这项参数运输避开 closure 后读的一个具体问题：合法回调可改变 captured get，Python仍把该实际 get 观察值作为不可信实参送给真正 local consumer，C 在调用它之前比对独立原根。它不要求所有 closure 内容永久不变。任意改写真实 frame/locals 不升级为统一新增威胁或本批操作许可；不读取、创建或改写真实 frame。

但参数必须已经正确绑定。原三处 incoming 的1位置参数与两个 local factory 的0参数正常路径见第四节；未来六工具新增 ABI，尤其 make_view 的 keyword/default 路径，尚未证明。所有相关 control/helper 调用方必须同版传参；不能只改 catalog 六定义而保留其他原消费者裸调用。

## 3. 逐叶门、原求值次序与已知安全操作

Python 只运输原 actual callable/receiver/argument。C 在同一实际消费点验证独立 primitive/receiver/source/owner/phase 和必要当前 metadata，随后立即执行已选原操作；不返回“检查通过”供 Python 再调用可写目标，不在 dispatch 重读 alias。

| 操作族 | 具体消费门与保持项 |
| --- | --- |
| kind/size/error/sequence/dict_items 等 captured callable | 原 callable 先观察，参数按原顺序取得，C 核原根与具体 operand 契约后实际调用；错误构造也不得走污染后的 error |
| get/class_get，包括 code/defaults/kwdefaults audit | C 先验实际原 primitive、receiver exact type/来源及原 attribute，再调用原 getter一次；保原 audit、实际返回或实际异常；返回后不缓存后继叶成功 |
| dict/map 查询 | 接收已经按原时点选中的实际 callable/receiver/name/missing；用 r1127 的 raw 遍历/Unicode 路线核当前全键 exact-str、原名称内容及 receiver/source 关联，raw只用于fresh准入/谓词。准入成功后在同一持GIL且无callback的C段，实际调用本次先选的原 dict_get/mappingproxy query 一次，保 missing effective-value 语义；失败在调用前拒绝。不以raw重演替代原query，不重新lookup或改变次数 |
| property/CM/SM链接 | exact builtin member getter只读链接，不执行 fget/func；actual链接与独立 producer expected 的联系另核，不能以同一个 wrapper identity 冒认链接未改 |
| 原 acquire/release | 各自原位置 fresh 准入后由同一 C 段尝试已选原锁操作；acquire在try外、release留在finally，等待/释放可再入后下一叶重新fresh；不持多root锁，不跨原业务callee持锁 |
| 迭代/解包/下标及异常匹配 | 不只枚举 ast.Call；实际 mutable容器/iterator来源和形状各有门。若操作仍在Python，必须先证明实际 operand 是已知安全对象且协议固定，不能先运行未知协议再验 |

例如 nested sequence(dict_items(keywords))：原 sequence 的实际观察在内层 dict_items 前，保留这个先选目标，再处理内层；外层 native 不重新读取 sequence。data.get/name/missing 也保 member selection 在实参前。若原 member selection 已由 exact mappingproxy/固定 builtin descriptor 及原 receiver 来源证明安全，可以保该 Python lookup 并把实际 bound target运输到 C；否则须拆出原时点的 native binding叶及真正 local 的 selection产物，后续只消费该产物，不重新 lookup。新增 bound/selection对象的分配失败与引用须计入，不能默默删除原 lookup。

已证实际类型之后，exact str/int/bool 自身不可变；相等、大小和真值操作不是未知用户协议，identity比较也不调用用户函数。names 是新建 exact list且每个实际 name 为 exact str时，原 membership 可以用固定字符串比较。新建 cells/rows/functions/names/slots/function_batches list 的 append 不因元素任意就调用元素协议。dict_items 或 items迭代本身不调用 key hash/eq。上述白名单仍以“actual对象/类型已证明”为前提，不用 capture 名称或较早一次 kind 检查代替来源。

完整 AST 库存列250 Call、163 Compare、79 Raise、33 For、3 ExceptHandler、11 Attribute、1 GeneratorExp、2 Subscript。它们是语法节点，包含原重复/错误表达式，不是执行次数或已完成所有门的声明。六 body 的完整 AST、签名、节点原文与坐标均保存；future K 的 source-to-source替换、原短路/再读/释放映射仍待具体候选。

## 4. D0、D1 与 D2 的确切边界

沿 r1109 原合同，D0 是 actual call消费点的 fresh caller/source/phase/callee门，不是统一全 body 不可变。原三 incoming 均1完整位置实参，两个local factory均0；原四个涉及 code 节点的 ABI/entry前缀从既有冻结 raw派生报告取得，不新compile，不冒认 live身份。未来新增consumer后不直接复用这个旧 ABI。

3.12.10 的具体路径为 PyObject_Vectorcall → _PyObject_VectorcallTstate → 标准 _PyFunction_Vectorcall → _PyEval_Vector → _PyEvalFramePushAndInit。最后metadata门还须核实际 vectorcall 指针是独立原标准根。原固定成功形状中，kwnames=NULL、实参数等于co_argcount、无varargs/varkw/kwonly：initialize_locals直接把已持有的真实参数引用放到localsplus，不走 keyword RichCompare、补defaults或kwargs dict分支。Py_NewRef/原指针复制不等于执行Python；不能把通用分支风险无条件套到这个固定正常形状。

仍须区分frame空间充足快路和新chunk慢路。_PyThreadState_PushFrame的慢路到allocate_chunk/_PyObject_VirtualAlloc；本批没有检查该后端/当前allocator来源，不宣称所有stack allocation均无可再入行为。只证明已有文本中的分支，不以缺少明写 callback 就把整个D1关闭。参数错误、内存失败、frame清理中的DECREF不属于上述成功证明，原异常/first引用另列N4。

_PyEval_EvalFrame先选择PEP523 evaluator；default bytecode COPY_FREE_VARS随后才从actual function.func_closure复制cell identities。故D1的frame code/参数已建立不等于D2已消费原closure。RESUME/monitoring/pending又在不同阶段。真参数consumer不靠closure取得入口，后继每次实际primitive消费可拒绝改后的值；这不证明整个D2已解决，也不证明任意body首条都fresh。禁止通过关闭GC/trace/monitoring、替换PEP523或真实frame改写补洞。

## 5. 三处 cell 小 try 的失败隔离

原 function_row59–63、class_row130–135、check_view321–328 都只将 cell_contents getter的原ValueError当empty。若把native admission新增ValueError直接放入旧try，会被当empty吞掉。把所有异常先编码返回到Python再匹配也不选：那会改变原 active exception、late matcher及context处理，不能只说透传first。

选择具体的两类结果协议，待精确源码审查：

1. try内的 CELL_TRY native叶先校验并实际调用原get一次。原get失败仍以原异常自然抛出；新增admission拒绝则**不在此抛ValueError**，返回本次独立的 opaque denied reply。成功返回本次独立 success reply，内含该次实际contents。不能以contents是否等于某sentinel区分二者。
2. except头在原late时点观察原error，并将其交真正local consumer的 MATCHER叶。它只返回已核的原ValueError class供原匹配器使用；source admission失败在except头直接逃逸，不会被该handler吞。不得提前缓存error替代这个原late观察。
3. else内、原try外，由 CELL_ACCEPT消费actual reply：denied在这里抛新增认证错误，success取该实际contents，然后原append/原empty条件继续。保三个小try的原分支差异，不扩大为整段body的catch。

CELL_TRY自身必须将所有本方认证拒绝编码为denied，不得在其中偷走ValueError错误构造捷径。reply准备或C内部错误也不能伪装成原getterValueError；MemoryError等准备失败另列并由源码逐项证明。原getter/except/finally自然完成，尤其root release的异常覆盖关系不被本方“保first”改写。该协议增加reply准备、真实local及独立引用；其初始化/失败/成功/清理顺序仍须逐slot N4，不因本节文字就报告源码批准。

上述失败分类只约束已经进入本方C实现的操作。Python CALL到consumer之前仍可能有monitoring/entry事件并抛错，C尚未进入时不能事后把它分类为getter错误或本方拒绝。原直接getter表达式也有解释器事件，但新call对象、调用次数和源码/栈可见性存在显式变化；必须在D1/COMPAT与小try的完整异常矩阵中审查，不能把本节写成“try内只可能有原getterValueError”的全路径证明。当前只解决本方admission拒绝误吞和late matcher来源两项，不宣称所有回调异常语义已经相同。

reply由实际native操作创建并强持本次actual结果/类型/state/source关联，不接受任意Python tuple作认证结果、不保存最近reply、不设first-wins或一次性消耗状态。泄露/重复观察reply不授予登记来源能力。consumer调用方须消费自己的actual return，不能拿另一调用的reply替代。新增reply仍属于该次操作的产品返回/自有引用，不是runtime journal。

## 6. producer、真实 generator 和整合义务

owner_storage158仍有真实genexpr。不能借叶门方向把其改为C eager loop并声称CREATE/原generator语义未变；实际sequence/pair目标、原左iterator、body tuple/UNPACK与错误路线须保留或明确提出兼容delta。原native allocate的实际lock、slot/list创建及owner返回来源是producer问题；consumer不得以公开“记录当前allocation”接口或current list快照补expected。每个实际producer结果须有独立owned来源并随该次actual return运输，不能更新某个最近slot冒认。

本批不完成owner receipt/layout，也不借参数名称声称sourcecaller身份已经证明。全control/helper的同类真参数、r1123的38处14/5/19及foreign、r1122 CORE pair/3local、r1124 P00–P12、r1125 imports、r1126 seed/Product/carrier须同版组合。新增私有ABI、六工具actual代码及dispatcher/source/leaf/reply共用native state都要共同N4，而非各自单独clear后留环。

后续完整设计必须给：future K精确后代与签名/closure/constant；各叶原operand求值与错误边；make_view实际keyword/default D1与allocator慢路；producer slots/receipt；参数/selection/reply/异常的实际acquired顺序、detach/短锁外DECREF与traverse/clear/free；每次原control实读与加法溢出/heap成本；>=3.11版本/平台/打包与缺native fail-closed。上述待项不能用本批23份输入或操作节点数代替实现。

完整设计两审前不生成产品候选；源码准入前不compile/import/执行/native build、模型、旧绿F001、QA或诊断。原17lease/1449slot/406尾/五S002、保护56同18缺和case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；control1MiB不是确认native heap上限。

正常库/服务-dist/ACLsettings/真实平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放、r967不执行不重问；P06历史0/7用户无材料不再索取。apply本批0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：叶消费参数的完整接线及新增引用协议

父r1128；产品仍r1106 SOURCE02完整159。本批是真参数/叶消费/小try的有限方向，未批准完整设计或产品执行。

1. 将六工具consumer真参数与全原调用方、38处context/foreign、CORE pair/三local及P00–P12/import/真实generator同版接线，给future K准确ABI/后代/LOAD_FAST和原operand观察→actual call映射；不删除原lookup或把安全类型白名单扩大成任意可变operand已认证。
2. 具体化CELL_TRY/MATCHER/CELL_ACCEPT的本方拒绝与原异常分路、实际reply/source关联及逐引用释放；owner_storage真实genexpr和原lock/slot/owner实际producer另闭，不造最近allocation登记接口。
3. 核frame allocator慢路与make_view keyword/default D1，按原路径分清D2消费，完成native共同N4、slot/字节/预算/异常覆盖、>=3.11/平台/打包。完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

全部原保护和限额保持，r920/r964耗不重放，r967不执行不重问，P06历史0/7不再索取；Goal active，非只剩验收。
