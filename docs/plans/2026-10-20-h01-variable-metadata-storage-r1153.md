# r1153 有限后继方案：按实际维度的私有bootstrap工作区

状态：经Root及同一代理有限复审，可登记为条件存储方向；完整容量/layout/source/执行未通过。r1152已知103固定槽及lease协议保持，只有其“变长捕获scratch须全部放m_size内联”的未闭存储方向拟改为本模块自有的平台工作区。不是把T0后移，不以后来current建立expected，不调用或修改CPython全局allocator表。

## 1. 原预算不提供所需上界

control1MiB是实际文件读预算；17/1449是静态源码region/slot；406是constructor尾fastlocals。原metadata允许完整kwdict和全MRO dispatch，现无已批准的metadata数值上界。独立原文核对见BUDGET-INDEPENDENT-FINDINGS-02.md，不能再以这四项给固定scratch常量作容量证明。

## 2. 平台工作区与可替换Python allocator分开

既有tagged v3.12.10 obmalloc.c131–165的默认低层实现分别直达Windows VirtualAlloc/VirtualFree、条件mmap/munmap或malloc/free；635–644的_PyObject_VirtualAlloc/Free则从可替换runtime arena表取alloc/free/ctx。后者不纳入无callback段，也不取得其current指针作原来源。

拟采用本模块自有、固定编译实现的平台工作区入口：Windows路线直接采用同进程普通读写虚拟内存，不使用可执行页、large pages、远程进程或文件映射；不经Python/ctypes调用，不设置全局allocator，不新增Python register/expected入口。模块m_size仍仅提供原固定RootArena/scalars及非拥有工作区关联；变长工作区由当前真实C bootstrap拥有。

这是一项待证明的原平台primitive/匹配build条件，不是现有Python根授信自动涵盖的结论。原系统导入/调用约定、完整wrapper无Python API/无GIL释放/无callback、实际build及所有支持平台必须列清。CPython默认实现相似不证明本方原导入或无callback。POSIX官方两页本轮403终态，没有重取或换址；本稿不据Windows文档缩减既定平台/版本域。

Microsoft官方文档支持VirtualAlloc的尺寸参数、普通reserve+commit/零初始化及NULL失败；VirtualFree用原allocation base、size=0和MEM_RELEASE释放整区并有失败返回。原文只作公开API语义依据，没有证明本方调用已成功、OS源身份或一般free失败已闭。公开依据为[VirtualAlloc](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc)与[VirtualFree](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualfree)。详见WEB-OBSERVATIONS-01.json；没有实际调用任何内存API。

平台工作区是新增OS准备effect、延迟和失败点；未扣control_bytes并不代表零成本，也不自动满足r1075原“无新clock/I/O/计费”合同。即便本方未显式read/write文件，也不从此推底层分页或资源行为零I/O。原合同仍保持，源码准入前必须给出这项新增准备与原目标/原域一致的逐项COMPAT解释，或对确实不兼容处提交明确合同修订；本稿不静默批准此修订、不缩小原目标，也不借增设业务计费/clock或放宽原限额处理。

## 3. 必须数完整的维度

五个具名class捕获对象为PathFinder、_LoaderBasics、FileLoader、SourceLoader、SourceFileLoader，记C=5。这只是固定的根集合，不限制它们各自MRO/字典/dispatch长度。四个C005原MRO复用已有强根；PathFinder原MRO需要本次独立C guard，不从以后tp_mro补expected。

| 符号 | 实际计数，保留occurrence |
| --- | --- |
| B | 五个实际MRO内base出现次数之和；同一base在两个MRO出现两次，不去重 |
| E | 每个上述base occurrence的实际class字典条目数之和 |
| D | 逐entry按原property三accessor、SM/CM一个__func__或plain值展开后，exact FunctionType的出现总数；同func重复出现仍计重复行 |
| F | 六个具名函数行加D个dispatch行，F=6+D |
| K | 全F个实际行的kwdefaults条目数之和，缺席为0；不能按kwonly形参数截断 |
| Z | 全F个实际行的closure长度之和，缺席为0 |
| P | 上述Z个cell中populated出现数，0≤P≤Z；contents=None仍populated |

原G/B只核身份与exact-str键，不复制全部values。其键扫描工作仍需列成本，但不假造额外snapshot值。closure→cell、MRO→base为已持不可变tuple的借用边，不能把cell contents或class/kwdict条目也当不可变借用。

## 4. 一次工作区的物理记录与容量式

工作区只承担本次bootstrap捕获及表示转换，不是长期新source bank、历史记录、最近槽或公开owner。下表是本批待选工作记录，明确每个PyObject owning位置；C scalar/index/borrowed指针另计。

| 记录 | 个数 | 每条owning声明位置 | 责任 |
| --- | ---: | --- | --- |
| DispatchCapture | D | 原function/code/defaults/kwdefaults/closure/G/B七项，kw_entries/cells两个成品根，row_tuple一个成品根，共10 | 原七项先raw强持；后继逐项move进实际row9，已move槽NULL |
| KwCapture | K | actual key、value、pair_tuple，共3 | key/value独立持有后才能分配；pair完成再构造outer |
| CellCapture | Z | populated contents、cell_record_tuple，共2 | empty contents槽NULL；cell身份借closure，填tuple前另NewRef；True/False占位亦有实际移交引用 |
| ClassEntryCapture | E | actual key、value、entry_pair，共3 | class字典可变边独立持根，pair成品后才能建entries outer |
| BaseCapture | B | actual dict guard、entries_tuple、base_row_tuple，共3 | dict guard只属本次准备，不用后来type字典重认expected；base从持续原MRO借出，填row前NewRef |
| ClassCapture | C | base_rows_tuple、functions_tuple、class_row_tuple，共3 | 最终class_row保持原四项(class,mro,rows,functions)，原class/MRO填tuple前另NewRef |

六个具名函数的七项原根与两个snapshot目的槽仍用r1152既有行，不再为同一目的字段重复建七根。D中的同名function是原class_row另一个occurrence，仍是独立取得。PathFinder MRO guard、creation_module_guard、bootstrap lease、平台allocation责任和actual first为独立自动责任，不与表内PyObject槽混数。

表内owning声明槽上界R=10D+3K+2Z+3E+3B+3C；实际非NULL引用数依presence/阶段变化。构造原Python row9时，内部defaults/kwdefaults/closure的NULL缺席在对应tuple槽运输为独立owned Py_None；内部present仍分列，不得将NULL指针填入成品tuple，也不得把原C current NULL反认成实际Py_None。该Py_None引用从原C根在已匹配raw引用包络取得后直接移交本次brand-new tuple；不steal borrowed。每槽仅首次实际取得入ledger，move后置NULL并清owns，不在本次阶段重新使用该槽；逆序清理跳过已move槽，不释放别人的tuple-owned引用。原103固定槽的ledger仍单独记录。

需字节数为header、各记录数组、R个ledger entry及必要scalar/index数组按真实sizeof/align作checked加乘和对齐之和。scalar/index语义清单见WORKSPACE-SCALAR-DRAFT-01.md，列header、六类记录、ledger和固定六/五自动映射，不设隐藏变长index数组；实际C类型/原call结构/sizeof仍待闭合。必须逐项给出所有scalar/index数组，不能只以R乘指针大小冒充完整bytes。没有本机sizeof/ABI实测，不报一个已证明的数值bytes。溢出或实际平台分配失败是新增bootstrap准备失败，原业务callee/attempt/seed尚未进入；不能冒认原函数本来也会OOM，更不能以固定cap掩盖原域缩减。新增峰值成本仍要完整预算/COMPAT审查。

## 5. count→allocate→capture的条件包络

1. 原exec第一对象操作取得真实creation_module_guard；原state/accessor四组合、唯一CAPTURING初始化、bootstrap lease按r1152。先持全部已知固定原根及五个class原MRO，原来源不从public alias补认。
2. 同解释器GIL内，用匹配版本的exact-type/raw primitive扫描B/E/D/K/Z/P及所有需存储维度，只做C scalar算术/读取，不分配、不调Python、不任意DECREF、不等待可回调锁。必须从真实原descriptor路径取得field，不能用通用getattr或audited getter。借用指针只在段内用完。
3. 若实际平台封装的无Python-callback/GIL释放包络已证，可按刚得到的完整checked bytes取得本次工作区。该一步是新关键前提；若前提未证，不允许继续从current补expected。不是“持GIL所以任意allocator都安全”。
4. 在同一已证明无callback区间raw第二遍捕获。固定字段与当前来源联合核对，按原occurrence取独立引用并登记；count与写index须匹配，所有写前边界检查。不能跨未证明的callout使用第一遍borrowed指针。只有外部源在整个区间不能发生本模型内合法变化，第二遍才仍为原T0值，而非新的T0起点。仅前后数量相同不证明此条件。
5. 固定和全部变长原值均真实owned后，才置capture_complete并进ROOTS_FROZEN。此后允许Python tuple等分配；所有原值由固定槽/工作记录持有，不重扫current造expected。每个构造实际return先C local持根，raw填完整子tuple、fresh后move，callback退休不补回partial。
6. 具名六行snapshot进入原目的槽；每个class的完整原四项row进入本次实际构造根。拟在RootArena增加五个具名class_snapshot目的槽，来自actual class_row_tuple，fresh后move；这是103之外新增5槽，108仍只是已知片段，不是全state。准备工作区所有临时责任清完后，在bootstrap lease有效期间释放其原平台allocation。

计数/原字段读取/allocator包络/实际发生的所有callback/引用宏/原API与build缺一项，均不能声称capacity complete。该路线解决的是不再需要任意固定metadata条目上限的算法方向，尚不证明完整原调用域与内存成本兼容。

## 6. partial、GC与平台释放

工作区中的owning引用归本次私有C上下文，module.m_traverse不重复访问它们；其真实NewRef在活动bootstrap期间作为C根持续存在，bootstrap module_guard保state内存，lease延迟固定arena释放但不延迟退休/Record CLEAR。工作区指针不授source资格，不从GC可见性推秘密。

所有出路先停止业务、按本方实际acquired ledger将槽置NULL再release，结清子tuple/actual output/temp；未move引用仍由workspace持有，已move项归实际tuple或state。平台region在最后一次读取其中ledger/scalars后才释放；需要的allocation base/length及actual first必须先由独立C自动槽持有。释放成功后不读region、不用其中next或长度。

不能照抄CPython默认free忽略VirtualFree返回就宣称本方完整释放已证。平台free失败的剩余责任、错误构造/indicator与原first、无first入口、POSIX对应合同及是否引入额外保留均未闭。不得把未释放region悄悄移入永久quarantine/最近槽，也不能为掩盖失败无限重试或改变全局allocator。该未闭项阻止源码准入；本稿没有执行一个声称通过的free路径。

正常成功的表示完成后五个class snapshot是新增持久拥有根，必须纳入module arena ledger/traverse/clear和完整SCC。真实partial、free及N4未闭，不能用“私有C根”四字替代退出协议。

## 7. 决策边界与后继

本稿若经双审可选择的只是：不再从原I/O/静态slot预算硬推metadata上限；用完整维度/逐条owning记录计算一次工作区；将平台自有存储作为待证明的容量来源，固定RootArena不冒充变长scratch。平台导入/无callback/ABI、全scalar数组与bytes、free失败及全部预算/COMPAT未闭，capacity/layout/source/execution不批准。

后继优先补平台分配与释放的完整可支持合同，完整记录layout与尺寸算式，再接五个class snapshot实际producer/consumer与所有source/type。真实恢复helper、P0/binder/D1/D2、一般NULL/Init/free/SCC/N4及完整产品目标仍未完成。B001/B002 open2，四完整False，Goal active。

## 交付与继续实施

本批Root及同一代理只批准记录附条件的后继方向，不批准平台调用、完整容量/源码或执行。父r1152封存保持，原预算负结论与01/02全部证据留在data/t16-h01-variable-metadata-storage-r1153/。完整Goal没有缩小为本批文档。

后继需优先解决新增OS准备与原合同的精确关系、真实平台导入及无callback/ABI包络、free失败剩余责任，再完成逐字段types/bytes式和实际source/caller/N4。静态检查不计产品测试通过；尚有工程，不只是验收。
