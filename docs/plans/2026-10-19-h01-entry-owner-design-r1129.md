# r1129：参数绑定边界与 owner 实际构造

r1129核明frame扩容实际走可替换arena allocator及alloc/ctx，getter还可能WAIT_LOCK，不能以持GIL关闭D1；make_view保原6kwonly/default，仅给条件正常路径。owner选专用C构造/独立source bank与真实gen：静态template7def、runtime仍6次New，第四次用closure拟0/NULL的stub；原append选择在slot RHS前、实际list/slot/lock/owner从本次实际return持根。future K/完整接线/D1慢路/D2/N4/COMPAT仍未闭，B001/B002 open2；完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

29完整pairs每侧1,527,072B、原owner完整AST/14call等库存、make_view原签名/default及20段tagged/继承tagged原文加1段本机SDK原文分列。SDK tag身份未核；新增obmalloc.c一次下载84,255B，不调用allocator getter/setter或安装hook。26bank/六closure/bank9分表，原真实gen code须future actual K后代；独立OwnerProduct无current登记或first/last结果槽，方法绑定具体路线及全部partial/异常仍待共同N4。

产品159源码7,956,657B及保护56同18缺保持，本批candidate/compile/import/运行/native build/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002与原预算不放宽，control1MiB非确认native heap上限。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964耗不重放、迟到授权不触发新执行，r967不执行不重问、P06历史0/7不再索材料；完整Goal非只剩验收。

# r1129：参数绑定边界与 owner 实际构造子路线

父 r1128；产品仍为 r1106 SOURCE02 的159文件、7,956,657 B，未安装。本批只完成有限静态调查及选定 owner 子方向，不生成产品候选或 future K。B001/B002 保持 open2；完整 design/source/execution/Goal 均 False。这里选定的结构修订 r1128 对 owner 的“Python 保全部算法、逐叶 native 消费”方向；另外五工具仍沿 r1128，不能把 owner 专项改成六工具整体 C 重演。

## 1. D1：把成功参数绑定与 allocator 慢路分开

已知固定正常位置参数路径的直接引用复制仍成立，但不据此关闭全部 D1。v3.12.10 的 `_PyEvalFramePushAndInit` 先读 actual func_code、用 co_framesize 推 frame，随后 initialize_locals 又读 actual func_code。frame 扩容慢路到 allocate_chunk → `_PyObject_VirtualAlloc`；本批新取得的 tagged obmalloc.c 显示后者实际调用运行时 `_PyObject_Arena.alloc(_PyObject_Arena.ctx, size)`。原 default Windows 实现内部用 VirtualAlloc，与当前运行时 allocator 表必为原 default 是两项事实。

因此，若慢路 allocator 可回调并修改 function，前后两次 code 观察不必相同；这不仅是抽象的 OOM 风险。这里只给静态条件轨迹，没有装 hook、调用 allocator getter/setter或执行该轨迹。不能以持 GIL 推出任意 native allocator 无再入，也不能把当前 pointer 快照重新登记为原根。

PyObject_GetArenaAllocator/SetArenaAllocator 在 allocator mutex 存在时使用 WAIT_LOCK；getter 不可塞入声称“无等待、无回调”的最后 raw 段。源码还明确指出使用 alloc/ctx 时不加该锁以及存在 race。只检查 alloc 函数指针而遗漏 ctx、只核前后相等或短暂 getter 快照，都不是原来源及全过程不变证明。不能通过安装 allocator、替换全局状态或关闭监控补洞。完整原 allocator 根、ctx、同步与可支持条件尚未选定，D1 慢路保持 open。

本批新下载仅一次 tagged obmalloc.c。另复制本机 SDK 两份完整头，其 tag 身份未核，明确标成 local-SDK-unverified-tag，只用于本机构造声明观察，不混称 tagged，也不以本机头声明证明运行时 ABI。

### make_view 的保语义正常路径

未来签名保六个原 keyword-only 参数和实际默认值，只增加 mandatory positional-only native consumer。原 modules/functions/bindings/classes/roots 默认空 tuple、result 默认 ('none', 0)；不把所有调用强制填满、转为位置参数或用新建等值默认对象替代。库存保存原完整 args/default AST，不是 future ABI 证明。

在正常 C 调用方中，consumer 是唯一位置参数；actual kwnames 若非 NULL，只可为六个原 literal 名的合法 subset，所有名字 exact str、无重复，无 *args/**kwargs 收集。actual K 的 co_localsplusnames、co_argcount/posonly/kwonly、flags、vectorcall，actual kwdict identity、全 exact-str keys及六项独立 expected，在最终共同核中成立。完整六项预期存在，即使某次调用显式提供全部值，也不借此删除原 kwdefaults 认证。

在这些条件与 final→frame initialization 之间无再入修改的条件下，initialize_locals 的 keyword identity/字符串 RichCompare 只走已知 exact-string 协议；未提供参数的 PyDict_GetItemWithError 也在全 exact-str stored keys 下不走未知 key hash/eq。实际 supplied values 仍按原位置运输，不在绑定前偷做 body 的 rows/result 类型检查。exact-str 查询名本身不足以证明当前 dict 全键安全；该条件沿 r1127 保持。

这是一条有条件的正常成功路径，不是 bad kwargs、重复名、缺参、OOM、frame cleanup或 D2 证明。保原错误路径及新增认证拒绝的区分，不能把所有错误统一改成认证异常。future K 未生成；allocator 慢路及最终元数据门与实际 frame 的联动未闭，故 make_view D1 整体仍 open。owner 新 stub 的 descriptions 保 positional-or-keyword 资格；本方正常两位置实参并不授权把其改成 posonly。

## 2. owner 的具体结构与 source bank

选定 owner 专用 C 重演，Python 入口只把真正 local descriptions 交给 mandatory posonly consumer。语义形状为 `def owner_storage(_catalog_consumer, /, descriptions): return _catalog_consumer(descriptions)`，不是候选源码；首 docstring 拟保原文。consumer 使用固定 native OWNER_BUILD role，无可写 call slot/instance dict、无公开构造或 expected 写接口。

沿 r1126 module 空 tuple For 内的完整 maker template，保原六 def 作为 code/source templates；在同一 template scope 追加同名 owner runtime stub，总计七个静态 def。原 owner-body-template 与新 owner-runtime-stub 同名、甚至 qualname 相同也必须按两个独立 actual K/co_consts 路径及角色区分。原 template 不执行，不能顺带执行第七 def 或额外创建旧 owner function。

运行时 producer 仍六次 native New，第四个位置选新 stub code。原第四次 New/CREATE 的顺序位置保持，但其 code、参数、源码行和 closure 从原 lexical9 改为拟0/NULL，这是明确变化。不能照旧 closure flag 后填，不声称 stub COPY_FREE_VARS 消费旧九 cell。其余五工具也有 r1128 consumer 参数变化；其 closure/code/constants 的具体保持项须 future K 逐项核，不能因原 body 文本保留便先报真实 ABI 保持。

| 表 | 保存内容 | 不可混同的事实 |
| --- | --- | --- |
| 独立 source bank | 原11 capture RHS、26 binding 的实际 cell 身份及各原 RHS 返回 expected | 不能从后来 tool.__closure__ 或 current 内容补 pin；未来 cellvar 顺序未核 |
| 六个 actual function closure | 按各 future actual code.co_freevars 构造的实际 tuple/cell 根 | owner stub 拟 NULL；其余五项待 future K；不把26项全塞给每个工具 |
| owner C 工作根 | 每次从独立 bank9 按原位置观察的 actual cell/值与该次实际 producer 返回 | 不声称来自 stub frame 的旧 closure 消费；不从 stub.__closure__ 回填 |

原 owner 的九 lexical captures 为 allocate/boolean/error/failure/kind/pair/sequence/size/text；这是静态名字集合，不是 co_freevars 顺序。原26 binding/cellvar 布局即使文本不变也未证明 future K 保持。pair/allocate 等旧 owner 可见 closure 消失、bank 继续被 C 强持，是 COMPAT/N4 变化。原 owner template 的真实 gen code仍作为实际 K 后代保留；取得该子 code不要求先 New 原 owner template function。

OWNER_BUILD 每次只持自己的 C 工作根与私有 owned fields；外部重入调用生成另一独立 OwnerProduct，不写 J 的 last product，不设 first/last 或一次性 token。consumer 身份不证明任意 caller 正当。J 只消费自己实际 owner call 的 actual return；不能把外部另一 Product 冒认本次结果。原 owner return ABI 从二元 tuple 改为 opaque OwnerProduct；其内保存本次实际生成的原二元 tuple，由 J 提取后走原 owner STORE。

## 3. 原146–158的实际时序与独立 producer 来源

原 body 的逐节点原文/完整 AST/唯一 genexpr 在库存中保存。以下顺序是后续 C 候选的义务，不宣称已实现。每个 captured primitive 在原时点观察 actual 值；最终在同一 C 消费点对独立根及 operand fresh 核后实际操作，不能把历史通过当后续授权。

| 原位置 | 拟保原观察与实际操作 | 独立来源与失败边 |
| --- | --- | --- |
| 144–145 | kind(descriptions) 与 sequence 原 identity 条件；失败才按原顺序观察 error/failure并构造/raise | 不先执行未知 kind/error；新增认证拒绝另列 |
| 146 | 先实际创建 names exact list，再创建 slots exact list，再按原 targets STORE 顺序绑定 | 拟 PyList_New(0) 实际 return 立即各自持根；额外 owned ref 不取代实际 list；第二项失败保首项 cleanup |
| 147–154 | 对原 descriptions 实际 GET_ITER一次，逐 next；原 kind/size short-circuit、实际 tuple UNPACK、name/initially_none类型及 duplicate membership | kind 非 sequence 时不调用 size；name 非 text 时不调用后项；membership实际 stored entries须已知 exact str，原字符串比较不冒认 unknown-safe |
| 155 | 先选择实际 names.append，再观察 name 参数，最后调用该已选 target一次 | 保原 lookup；不得默默换 PyList_Append；原 lookup 的具体 C binding 路线及多分配失败待候选/N4 |
| 157 | **先选择 slots.append，再构造原 list6 RHS，再实际调用这个已选 target一次** | list6 为 [原0, actual initially_none, 原None, 原False, 原None, 原None]；实际创建 return、actual六字段引用立即独立持根；不能构造完才重新 lookup append |
| 158a | 先观察并实际调用 allocate()，返回原实际 lock；再观察 sequence target | lock 根来自实际原 allocate return，不从未来 owner tuple反推或重采样 |
| 158b | 用旧 owner-body-template 的实际 gen child code New/CREATE；随后观察 pair、names、slots并实际调用原 pair一次，再对 actual zip做原 GET_ITER | 保 gen-function CREATE先于 pair观察/zip调用，sequence先于 gen-function；New 后 raw flags/version按真实 code，不能用 JSON/CodeType重建 |
| 158c | 以 actual iterator为 .0 调用刚才实际创建的 gen-function一次，得到真实 generator；最后 fresh核并实际调用先选 sequence一次消费它 | 不重复 GET_ITER，不把迭代放 carrier.tp_iternext或改 eager loop；原 tuple target在消费前核，其实际返回独立持根 |
| 158d | 由 actual lock与actual sequence return构造原二元 owner tuple；再准备 opaque OwnerProduct | 新增 Product 分配/拒绝/cleanup是显式新窗口，不能称原 return后完全无失败 |

本方 per-description 私有 source row 只持这次实际 name/initially_none、slot list6及六初始字段的原身份；不是运行事件日志，也不接受外部 register/claim/expected 接口。names/slots、每个 slot、actual lock、原 owner tuple均在本次实际创建/调用返回时直接持根。绝不等 owner tuple返回后扫描 current容器来建立 expected。row表自身额外分配和溢出检查不得偷挪到原验证前并声称透明；header/capacity的确切时点与上限仍待 N4。

method selection 必须有一份实际先选产物：可为已核 original descriptor+self 的固定 selection表示，或 actual bound callable；具体 C 路线须在候选中选择并证明一次原 member lookup和一次实际消费。若 public绑定额外生成 bound method，明确相对原 LOAD_METHOD/specialization 的 allocation/引用/错误变化。不能把 list.append 内部实现最终使用 _PyList_AppendTakeRef 当作可跳过原 lookup 的依据。C 重演也改变 Python line/traceback/monitoring事件；这里只保语义顺序义务，不声称指令/事件完全等价。

追加引用、list/slot/lock/Product分配以及原实际调用都按可能再入准备处理；每次后续消费再核 source/phase和必要当前 metadata。Product准备及可再入释放后，返回前最终 raw重核独立 actual结果/expected、各list/slot字段和owner关联；不在raw段分配、等待、触发未知hash/eq或DECREF可执行对象。成功安装段只移转已持owned字段。这个最终核不撤销此前unknown effect，所以不能替代各原消费点的先验，也不证明 final→caller提取/STORE 的共同认可点已经完成。

## 4. 真实 genexpr、环境与部分消费

保原 `(name, slot) for name, slot in pair(names, slots)` 的真实 Python generator body：原 FOR_ITER、UNPACK、BUILD_TUPLE、yield、PEP479、partial推进、close/throw与实际错误。原 zip 仍实际对 names后slots取得 iterator；不预先 freeze两list或凭索引构造预期返回替代。real generator root只从同一次可信 actual loader K 中旧 owner-body-template 的确切子 code取得，不从 current function.__code__/co_consts补 pin，不复用离线旧code。

gen New仍用当时 actual G，并按原规则每次解析 G.__builtins__或实际活动 owner stub frame 的B fallback；不能把外层 maker的B缓存当原 owner时点环境。stub 的实际 G/B、调用 D1/D2、source bank与gen原 New环境的关联均需联合核。r1126 的后填引用、NULL前置条件、原code flags/func_version规则沿用，但具体 gen是否有closure及何种flags须 actual future K实核，不能先写“0”当结果。

真实 .0 的绑定仍经 actual Python function call、RETURN_GENERATOR创建及原 tuple实际消费。不要直接用 PyGen_New拼出等价假象；这会跳过原 function call/参数frame阶段。原 tuple消费期间发生合法外部推进/监控异常或源变化时，保其实际迭代/异常而非回放原元素；成功 Product还必须满足独立原来源/初始化检查。partial结果或被保留的generator不自动注册为 owner来源。额外拒绝与原异常差异、code参数/closure是否实耗及首body freshness仍列 D1/D2/COMPAT，不能由“真实generator”四字关闭。

## 5. N4、预算与后续实施门

新引用链包括 native consumer→source bank→cells/G、本次工作根→actual list/slot/lock/gen/tuple、OwnerProduct→owner/independent rows、J→Product及可能环。每个实际 acquired引用记录静态slot所有权；同对象多次acquire仍是不同本方引用。不能用tuple/list内部dealloc顺序代替本方逆序。各阶段先detach/置NULL，短metadata锁外DECREF；error/partial/pair失败、第二个list失败、append失败、gen New/调用/消费失败、Product额外失败及重入、长期保留结果都必须进入共同 traverse/clear/free表。

本批尚未给最终slot/heap字节数与全部异常释放矩阵，N4保持open。control1MiB是累计控制数据实读限额，不是已确认native heap上限。新增source行/selection/bound method/Product/真实gen成本须逐项计入，原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不放宽。pyproject >=3.11不收窄，本批只读3.12.10文本；跨版本/平台/打包及缺native fail-closed仍待。

后续先将 owner专项、其余五工具的真参数/逐叶门与CELL_TRY协议、全部实际调用方、r1123 context/foreign、r1122 CORE pair/locals、r1124 P00–P12和r1125 import、r1126 seed/Product/carrier接成同一完整设计。allocator slow D1与future keyword/default条件、D2及全metadata联合消费、共同N4仍需具体解决；不能只把open改名或以本批材料0替代完整设计批准。

完整设计两审前不生成产品候选；源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。本批只执行静态下载/复制、AST parse、byte/hash/pin与全文逆像及文档核验；不创建CodeType、不调用native API。原17lease/1449slot/406尾/五S002、159源码和保护56同18缺保持。正常库、服务/dist、ACL/settings、真实平台和Git不操作；禁profiling、真实frames/locals读取、断点/停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。

r920/r964单次已耗不重放，迟到授权不触发新执行；r967不执行不重问，P06历史0/7用户无材料不再索取。apply本批0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：联合设计的 allocator 与全接线义务

父r1129；产品仍r1106 SOURCE02完整159。owner专用C producer/真实gen与template7、runtime6的路线已选，未来K/source/执行均未完成。

1. 选定有独立根和同步依据的allocator/frame D1支持条件；核actual code两次读取间的扩容分支。保make_view原6kwonly/default和owner descriptions关键字资格，逐成功/错误路径证明，不通过更改全局allocator或关监控补洞。
2. 把owner stub、26source bank/六closure/bank9、实际New第4位置、真实gen K后代与原146–158时序纳入同版设计；方法选择在slot RHS前，actual list/slot/lock/owner来源从实际return取得；无current登记/last产品槽。
3. 与五工具逐叶门/三cell小try、全caller、38context/foreign、CORE pair/locals及P00–P12/import/seed/carrier联合，完成D2/metadata认可点、共同N4/heap/实读成本、兼容与>=3.11/打包。完整设计两审前不生成产品候选，源码准入前不compile/import/执行/native build/模型/旧绿F001/QA/诊断。

全部原保护/限额保持，r920/r964耗不重放，r967不执行不重问，P06历史0/7不再索取。B001/B002 open2；完整Goal active、非只剩验收。
