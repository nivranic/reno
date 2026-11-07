# r1126：catalog实际创建来源与真实generator消费路线

r1126已选择真实maker frame内的seed→独立Product路线，以及只运输原next结果的native carrier→真实generator body调用路线。maker参数0→1、返回tuple6→Product、cells/closure原点与六def header顺序明确；不再以返回后首次快照冒认来源。watcher不得修改本次func的官方限制已纠正，后续CREATE修改先前function的风险分列。完整metadata、future K、38处联合消费及共同N4/COMPAT尚未闭；B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

4份AST限定库存确认catalog18body、11赋值26binding、六工具lexical captures11/7/15/9/7/20及三处原GeneratorExp。21段官方原文覆盖watcher限制、New及raw后填、header/closure、GET_ITER/实际generator call、cell所有权和PEP479 cause/context。新增3份固定v3.12.10纯文本92473B；全部仅静态读取，不是native link/build/runtime证据。

保原逐RHS/STORE、missing=object()时点、每次New独立G/B解析。make_view的6项kwdefaults及fresh kwdict在New前，closure随后准备、CREATE后只按实际flags后填；setter事件和引用变化不当作等价。future template/stub以actual loader K精确不同路径选择，当前未生成新K或产品候选。

carrier.tp_iternext只做原next一次和exact pair；工具实际call留在真实generator body，iterator结束与body StopIteration/PEP479分开。每轮G tools观察作为待核actual，由本代consumer/J独立来源核对后同C body调用。新增pair/UNPACK/FAST locals、.0手工next、native cells/stub/ABI/源码栈差异均明列，不报告完全透明或来源缺口已闭。

159源码7956657B及保护56同18lstat缺席保持；本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002、case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不放宽；control1MiB不是确认native heap上限。

正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史身份0/7不再索取。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# r1126：catalog 创建来源与真实生成器消费的具体路线

本批是 r1125 之后的有限子设计，不是完整实施批准。产品仍为 r1106 SOURCE02 的159文件、7,956,657 B；不生成或执行产品候选。完整 design/source/execution/Goal 均 False，B001/B002 保持 open2，源码缺口关闭0、产品 pass0。

## 1. 决定及一项调查纠正

选择在真实 Python maker frame 内通过真参数 seed 进入固定 native producer，producer 独立构造并持有每次实际 cells/closures/functions，返回本次独立 opaque Product。后续 P05 保真实 generator，在其 body 中调用固定 native consumer；carrier 只运输原 iterator 的实际元素及 consumer，不执行工具。两条路线均有具体 ABI、资源和可观察行为变化，不宣称透明替换。

否决“Python maker 返回 tuple6 后首次读取六个函数的 closure/cells 当 expected”作为完整来源方案。后续函数的 CREATE 期间，先前已经创建的另一函数仍可能被修改；同内容的新 cell/closure 不能取代原独立身份。返回值内容自洽和实际创建来源是两项事实。这个判断不否决全部 Python maker 方案。

纠正调查假设：官方 function.rst 明确 callback 可以检查但不得修改本次参数 func。不能将 CREATE callback 直接改本次 func 当合法威胁。文档没有把禁令扩展为不能修改任何先前函数；后续 CREATE(F5) 中修改已创建的 F4，与直接修改 F5 必须区分。本设计不添加 watcher，不为禁止行为创造新修复义务。官方 CREATE“fully initialized”说的是 New 返回对象的初始化，不表示 caller MAKE_FUNCTION 的 closure/kwdefaults 后填已经完成；官方 bytecodes.c 的 New→CREATE→后填顺序另有直接依据。

## 2. 静态输入与 future K 的责任

原 catalog maker 有18条直接 body：11条赋值、6个 def 和 return；11条赋值产生26个 capture binding。六工具依次为 function_row/module_row/class_row/owner_storage/make_view/check_view，静态 lexical capture 数11/7/15/9/7/20，合69。这里是 AST 名称库存，不是实际 cell slot 顺序，也不是新 K 的 co_freevars 证明。CATALOG-INVENTORY-01.json 保存原文、完整签名、每条 capture RHS、def 头和返回顺序；三模块原 GeneratorExp 也保存准确位置和原文。

拟在 catalog 原 module scope 放一个不执行的空 tuple For，body 内保原同名 `_make_catalog_tools` 完整定义作为 code template；真正发布的同名 maker 是仅有 seed 真参数、直接调用 seed 的 stub。template 不放进 stub 的词法 scope，避免 stub 自动 MAKE_CELL 与 native cells 两套分配。module-level For 本身不引入作用域；同名 template 和 stub 必须分别按 actual loader K 的精确 co_consts 后代路径选择，绝不按 name/qualname 合并或用旧离线 code。

这是未来源码变换的设计，当前没有生成模板候选、AST 变换、新 compile 或新 K。新 module co_consts 槽、stub/六 child 的签名、qualname、flags、cellvars/freevars、const 身份及字节码要在完整设计批准后的候选审查中取得。原 template 的子 code 是实际 K 的后代，原 template function 本身不执行；seed 也不从 observed maker.__code__ 回填来源。实际模板布局/编译器优化若不满足上述要求，停止该候选并修订设计，不能用相同源码文本冒充 runtime 身份。

三模块 future K 中的 genexpr 也须是实际新 K 的确切后代。本批只给语义形状，尚未证明未来 co_consts 路径、flags、stack/异常表或实际 code。空 For 增加编译符号/常量和源码位置；stub frame 的 locals/code 与原 maker 不同。这些是显式 delta。

## 3. D、seed、Product 与 J 的实际运输

沿用 r1124：D 证明实际 maker 的构造来源，保存原 A/K/G 与 actual New 返回和独立 metadata expected；D 不保存最近一次 tools/owner/context。原 maker STORE 成功后再独立发布 D；partial 和旧 maker/旧 D 匹配规则保持，不自修 current alias。

每次 J 先选择原 maker/运输 D 并完成本次来源、metadata 和实际调用点检查；本次调用准备一个固定 native seed 真参数。seed 的类型不开放公开构造、实例 dict 或可写 slot，固定 C call-slot 直接进入本方 producer，不经可写 global helper。maker 私有参数 ABI **0→1**，其返回 ABI **tuple6→opaque Product**，两项都变。stub 的语义形状是 `def _make_catalog_tools(seed): return seed()`，不是可执行候选。

seed 仅持本次所需 A/D/K/G 和独立 primitive 来源；不预先求原 captures，不提前造 missing，不拥有 first/last 返回槽。实际 producer 在真实 stub maker frame 活动时运行；G 缺 __builtins__ 时 public New 的 fallback 因而取该实际 maker B，不能在 maker 返回后的外层 frame 才创建六工具。每次 New 仍分别按原算法从 G 解析 B，不能取一次 B 后固定给六次。原 captures 的 Name 解析也按该 frame 的 G/B 逐次观察，不用 seed 缓存值偷换原重复读取。

每次 seed 调用新建一个 Product，持这次实际创建结果与独立 expected；外层 C 只从本次 actual maker return 提取该次 tools6 与来源记录，绑定 J，再走原 tools STORE。提取使用固定 native 字段，先核 exact native type/state/A/D/G 及初始化状态，不触发未知属性 getter。Product 不由返回 tools 的自洽快照登记；Product 自身也是 actual producer 的实际返回对象。

外部拿到 seed 并在另一 frame 调用，只产生那次独立 Product；不得覆盖 J 的 expected、消耗首用资格、替换最后结果或据 seed 身份冒认 caller。外部 call 的 actual frame/B 与其返回独立保留；外层仍消费自己实际 maker call 的 actual return。任意真实 frame locals 改写不升级为本批必须抵抗的统一新要求；D0、D1 实际 code/参数、D2 closure 消费仍分别证明，不能以 seed 身份完成全部三项。

## 4. 26 cells、原 captures 和六次 New

producer 先为原 template 的26个 cellvars 独立创建空 cell，顺序来自未来实际 template K 的 cellvar/entry 布局，并独立强持；原始内容均未绑定。原 maker MAKE_CELL 在 captures 之前发生；stub/native 入口增加的准备/失败时点和 extra references 明列，不称指令级透明。

随后逐条执行11个原赋值 RHS。tuple unpack 型赋值先按原顺序求完整 RHS，再按原目标顺序 STORE cell；不可把第一项存入提前到后续 RHS 之前。保 object.__getattribute__/type.__getattribute__、dict/tuple 等的原 Name/属性读取、三次 LockType Name 观察（其中两次用于 acquire/release 属性读取）及 missing=object() 唯一次数/原位置。每次实际 primitive lookup/属性操作的来源门使用独立 T0 根；未知对象不得先执行未知 descriptor/call 再验。可写 G/B 的拒绝是认证行为，不能假称替未知对象完整执行原任意代码。具体 Name/属性错误和联合认可点仍在完整 native consumer 设计中逐条审查。

各 capture 的 expected value 在原 RHS 实际取得时独立持根，再按原 STORE 顺序写入对应 cell；不能在后续 CREATE 之后读取 current cell_contents 补 expected。各 child 的 closure tuple 在该次 New 前，按实际 child code.co_freevars 顺序从独立26cell表装入；相同 binding 使用同一 cell，不能按 AST 集合排序当编译顺序。每个 closure 自身独立持根，actual New 返回也立即持根；后来 CREATE 修改先前工具的 closure/cells 不改变这些 expected。即使内容相同的新 cell 也拒绝，不重新采样。make_view 的 kwdict 身份及各项 expected 也在 CREATE 前独立保存，不从后来的 current entries 重建。

六 def 均无 positional defaults、annotation 或 decorator；仅 make_view 有6个 kwonly defaults：modules/functions/bindings/classes/roots 原空tuple，result 原 ('none',0)。这些值来自实际 template K 的原 constants；fresh kwdict 必须在 make_view New/CREATE **之前**按原 header 时点准备，随后才构建 closure，再 New。其他五个 def 不分配 kwdict。不能把值换成重新创建的内容相等对象，也不能 New 后才分配 defaults 字典。

选用版本化 full C API 的原 raw 后填方向：New/CREATE 后，仅填实际 flags 有的 closure，以及 make_view 的 kwdefaults，最后设置原 code.co_version。为 function 字段转交的引用和本方 expected 强引用分别记账：先给后填字段准备独立 owned reference，再按原 raw 路线转交；保留本方 expected 引用不被转交掏空。原本没有的 defaults/annotations 不额外设 NULL，不用洗字段把未知改写伪装成干净。被填字段预期为 NULL；若合法再入路径已使其非 NULL，选择新增认证失败，保 actual function 的所有权清理，不原地覆写泄漏旧引用、不清洗后继续。该拒绝及其错误路径是新增 delta，需与完整 metadata 检查同版实现。

不选 public setters 作为等价替换：SetDefaults/SetKwDefaults 会发 MODIFY 事件、version=0、INCREF/XSETREF；SetClosure 不发这类 handle_func_event，但仍 version=0、INCREF/XSETREF/DECREF，与原 MAKE_FUNCTION 的直接所有权转移不同。本批无 C 源码、编译、link 或 ABI 验证；逐版本结构、原填字段与 actual New metadata 的独立来源还须源码审查。不能依赖非法 watcher 行为，也不能把 New 之后所有事情都称不可再入。

六个工具都完成后，producer 构造原顺序 tools6 并将实际强持根与 expected 放入本次 Product。必须共同检查 function code/G/B/defaults/kwdict entries/closure tuple/cell identities/contents 及必要 source/phase；逐字段历史通过不等于同一认可点通过。完整 metadata 算法尚未交付，本批不报告六工具已经认证完成。

## 5. P05 的真实 generator 与 native carrier

原语句是 `tuple(_AUTH_CATALOG_TOOLS[0](tool) for tool in _AUTH_CATALOG_TOOLS)`。每轮都重新读 G 中 tools，再取0槽，随后取真正 local tool 并调用。不能只在返回 rows 后检查；未知 callee 早已可能执行。不能 eager C loop 代替；也不能把工具调用移进 carrier.tp_iternext，因为 FOR_ITER 会把 StopIteration 当正常结束，破坏 generator body 的 PEP479 路线。

拟议 genexpr body 的语义形状为：

```text
(_held_consumer(_AUTH_CATALOG_TOOLS, tool)
 for _held_consumer, tool in native_carrier)
```

这里只描述未来 code，不在 Python module 中另执行这段表达式来重复 GET_ITER。外层固定 C orchestration 按以下顺序：原 tuple target 先观察持住；创建来自实际 future K 的 gen-function（保其 CREATE/后填）；观察原左 tools 并作本次 J 来源准入；对该实际左值执行原 GET_ITER 一次；创建 native carrier 持原 raw iterator、本代 native consumer 和 J；以 carrier 直接作 genfunction 的隐式 .0 实参实际调用一次；最后在实际 tuple-target 调用点完成其来源/metadata 检查并传真正 generator 消费。左 iterator 创建失败时，不得提前创建 generator 对象或消费 target。

carrier 是固定 native iterator type，不公开构造、不开放可写 slots。tp_iternext 只调用原 raw iterator 的 next 一次；正常有值时建立 exact pair(本代 native consumer, actual tool)；结束/错误按原 next 结果返回，绝不在此调用 function_row。新增 pair 分配失败直接传播，不自动重试或重复 next。原 raw iterator 的正常 StopIteration 仍由 generator FOR_ITER 正常结束。carrier.tp_iter 仅返回自身，但上述 direct .0 路线不额外调用它。

generator 的原解释器 UNPACK 把 pair 写入真正 FAST locals。consumer 来源由 carrier 的固定 C 字段给出，不依赖可写 global/closure q。body 仍在原每轮时点从 G 观察 tools 一次；实际 tool 来自 raw iterator 的实际返回，不按预设 i 重新取 J.tools[i]。合法外部推进 iterator 可能跳项，不能用“必须第i槽”改变原语义。禁止重 pin foreign tools，不表示禁止原 iterator 实际推进。

native consumer 固定 call-slot 在一个 C body 中：先核本代 consumer/J 与 actual observed tools 就是 J 独立持有的来源有效 exact tools6，再核 actual tool 关联本代实际工具来源；仅“tool在six中”不代替此关联，也不从参数重建 expected。然后才读取原0槽；联合 fresh 核其 actual function_row 的 source/metadata/G/B/cells 与本次 J；最后用已选 actual callee 及 actual tool 尝试原实际调用，不返回 Python 再读 global 重新 dispatch。不先对未知 tools 执行 __getitem__。其返回的 actual row 同时绑定原实际读数与联合 metadata 认可点；没有永久授权以后任意 subject call。

工具调用留在 generator body 的 CALL 内：若实际 function_row call 逃出 StopIteration，仍走真正 generator 的 PEP479 RuntimeError/原 cause 路线，不能由 carrier 吞作结束。其他异常由实际 generator 传播；新增认证错误与原工具错误分列，不统一包成 RuntimeError。公开消费者不得成为跨 J 修改 expected 或 phase 的登记入口。

## 6. 兼容变化、资源与尚未闭合的义务

明确变化：maker 参数和返回 ABI、stub/template 的 code/locals/源码位置、native ingress traceback；26cell 在 native 入口分配；Product/seed/carrier/consumer 强引用；每轮 pair allocation 和 UNPACK/FAST local；generator .0 从原 raw iterator 改为 carrier，外部手工 next(.0) 返回 pair，调试可观察 locals 增加；工具选择的0槽下标从 Python body 移到 native consumer，先拒绝 unknown __getitem__。本设计没有读取真实 frames/locals；承认可观察差异不代表本方开启相关诊断。未来新 code、异常表、allocation/partial/StopIteration 分支必须被源码审查，不能给出完全等价结论。

J→Product/tools→closures→cells/primitives/G，D→maker→G→D，generator→carrier→consumer/J 等均可能形成环。不得把 J 存进全局 last-result表，或在 alias 删除/L退出时自动退休仍在消费的对象。活动调用使用实际 lease；释放按实际 acquired 自有slot逆序，先 detach/置NULL，再在短 metadata 锁外 DECREF。不能把 tuple/list 内部销毁顺序当本方slot逆序。Product 不成功、某个 New失败、kwdict/closure/pair失败、target不消费或只部分消费、generator外部保留、close/throw/再入均须纳入共同 traverse/clear/free 和 partial 初始化表。

本批明确路线而未完成 N4：尚无最终 slot 总数、native heap 字节、all-clear/free表或逐操作首异常释放证明。control1MiB 是累计控制数据实读限额，不是已确认 native heap 上限。原 case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10 不放宽。新 carrier 正常每轮 pair 成本必须计入未来预算，不借静态库存声称性能通过。

下批应将本路线与 r1125 原 IMPORT_NAME/IMPORT_FROM、r1123 的38处联合消费、r1122 CORE pair/3locals、三 D0/D1/D2、原 caller/phase 和完整 metadata 认可点接在同一设计中；再完成 native state/type/A/D/J/L/Product/carrier/consumer 共同 N4、实际引用和错误表、>=3.11/平台/打包许可。当前仅 v3.12.10 静态依据，pyproject >=3.11 不收窄。完整设计两审之前不生成产品候选；源码准入之前不 compile/import/执行候选、native build、模型、旧绿F001、QA或诊断。

## 7. 本批验证与保护

仅执行静态资料工具：固定官方源下载、AST parse/原文库存、byte/hash/pin/全文逆像和文档发布核验；不创建可执行 CodeType，不 import 产品。复用同一只读独立代理审查本子设计及交付，不新增代理。材料审查0只表示此限定子设计和材料没有已知阻塞，不关闭 B001/B002 或完整 Goal。

原17lease/1449slot/406尾/五S002 保持。正常库、服务/dist、ACL/settings、真实平台及 Git 不操作；禁 profiling、真实frames/locals读取、断点/停点/定点计时、新异常链诊断/runtime journal，不改全局 GC/trace/monitoring/PEP523。r920/r964 单次授权已耗不重放，r967 不执行不重问；P06 历史身份0/7、用户无材料，不再索取。canonical apply本批0/历史11；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整 Goal 仍有工程，非只剩验收。

# 下一批：联合metadata认可点与完整P0接线

父r1126；产品仍r1106 SOURCE02完整159。本批只选catalog seed/Product和真实generator carrier子路线，未完成完整认证或执行准入。

1. 将六工具独立创建根、原function_row/check_view读取的actual结果，与最终metadata共同认可点接线；实际工具call仍在真实generator body内，carrier不吞工具StopIteration。原D0/D1/D2分列，不添加统一全body不可变要求。
2. 同版连接r1125 IMPORT_NAME/IMPORT_FROM、r1124 P00–P12、r1123的38处14/5/19、foreign与r1122 CORE pair/3locals。实际caller/phase与source不能用current属性补pin，原metadata锁不跨业务callee。
3. 冻结future K的template/stub/六child/三gen路径和ABI/const/closure方案，完成native state/type/A/D/J/L/Product/carrier/consumer共同N4、逐slot/字节/partial/first异常及>=3.11/打包。完整设计两审前不生成产品候选；源码准入前无compile/import/执行/native build/模型/旧绿F001/QA/诊断。

原17lease/1449slot/406尾/五S002、全部原限额与保护保持；正常库/服务-dist/ACLsettings/平台/Git不操作。r920/r964已耗不重放、r967不执行不重问、P06历史0/7不再索取。Goal active，非只剩验收。
