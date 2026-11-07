# r1109 原消费契约与后代代码树静态目录

r1109完成原消费契约和五入口代码树静态目录：8条原要求、三处actual incoming、两个local call0及精确co_consts路径已对应；静态N1目录完成不代表runtime原根取得，完整设计及source/execution仍False。源码仍r1106 SOURCE02完整159，Goal active未完。

## 本批取得的具体结果

冻结26份完整原/副本pairs，每侧37926426B；核父r1108的76封存及当前159源码，保护56同18lstat缺席。上一Goal轮属于progress：r1108调用链证据改变了下一步，须先对原要求和原代码树作精确映射。本批没有新产品候选、可执行CodeType、syntax compile、候选import/执行、marshal load、native、hook、真实frames/locals、模型、QA、SQL/Store或诊断。

Root保存r1075原方案8条准确原句/行号，将三条已证incoming的要求定位为D0实际call消费点fresh，不凭空要求全body不可变；metadata自身校验则仍要求共同有效的消费点，不能退回逐字段曾通过。原锁只限metadata区、不跨原业务/helper调用，不能靠跨foreign锁完成修复。

三actual incoming为scope HELPER014→control._check_class(pin)，scope HELPER015→control._check_function(pin)，registration HELPER028→control._check_function(原变量function)；tool5/actual call坐标依次881/884、1116/1119、1857/1860。三处都为一位置参数、无keyword/star；两inner local factory的3561/4603调用均0参数，原词法来源不重做。六份直接相关源签名无defaults/kwonly/varargs，但不冒认整个认证图均满足固定快路。

五完整AST子树Call库存601/495/580/339/215，各自含后代；本次五目标互不嵌套，2230个(role,line,col)坐标不同、重复0。语法Call不是运行次数、隐式协议闭包或全图coverage。01的泛重叠警示及stdout键命名不精确，保原稿后在02只收窄五条scope文案，完整逆回01；全部AST/Call/原句/签名/假准入字段不变，新AST/compile0，不报成实质契约阻塞。

同一独审完成五入口精确co_consts路径、18个选中节点ABI/完整RESUME前缀及15条原父槽边，直接producer/原返回/STORE/P0五事实对应均保存。全部三raw树67/300/62，共429节点426父槽边，每个非module节点恰有一条原父槽入边，重复名/匿名不去重。Root独立核五路径、18前缀/ABI和15槽边；分工不是两份完整runtime源码认证。

## 已完成目录与仍未完成的实现

N1的静态路径目录本批完成。离线raw0和这些目录不是live原module/factory代码根；最早原factory/code身份的可信取得、强持有及actual producer/return/STORE关系仍未实现。r1106的P0首次捕获前提保留，五项事实自洽和current co_consts重pin不能证明原来源。N0、N2、N3、N4及COMPAT仍有实施义务；本批材料0不作完整设计或产品实施批准，源码缺口关闭0、产品pass0。

下一步使用本批固定目录设计最早可信根取得和共同metadata消费机制；不要再次只盘点相同树，也不要先写通用native dispatcher后用组件绿替代完整来源。原audit/错误/锁/引用释放及>=3.11交付策略必须同一设计说明，完整源码准入前不运行候选/局部模型/旧绿/F001/QA。

正常库/service-dist/ACLsettings/平台/Git保持；本批canonical apply0/历史11。r920/r964耗不重放，r967待原问不执行不重问，P06历史0/7不再索材料。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍未完成，非只剩验收，不等待重复用户批准。

# r1109 原消费契约与后代代码树：限定后续实现的具体输入

本批完成原要求与当前源码的静态对应，不是完整修复设计或产品实施批准。源码仍r1106 SOURCE02完整159；新增候选、可执行CodeType、syntax compile、候选import/执行、native、hook、模型、QA和诊断均0。

## 1. 从原方案确认消费时点

冻结的r1075原方案明确要求“每次fresh检查：下一callee实现、它实际读取的可变globals/builtin映射、必要原root和阶段”，并要求未知target在实际调用前被拒绝。主代理报告保存8条原句、准确行号及解释。

据此，三条已确认incoming缺口的主契约是 **D0：完成准备与可能审计后，在同一实际调用消费点fresh校验原caller/source/fact/owner及原callee**。它不是“整个目标body期间fact永远不变”。这项界定来自原要求，不能把它理解为降低原约束。

| 原位置 | 实际lookup及实际参数 | 当前消费点 | 后续必须保留 |
| --- | --- | --- | --- |
| scope HELPER014 | 原control `_check_class`，原 `pin` 一位置参数 | tool5调用881；原目标调用884 | 原单次lookup/实际target、原参数、返回后identity门及每轮fresh；不能误记为 `_class_pin` |
| scope HELPER015 | 原control `_check_function`，原 `pin` 一位置参数 | tool5调用1116；原目标调用1119 | 原两个helper的执行顺序和每轮次数；不以014成功代替015 |
| registration HELPER028 | 原control `_check_function`，原变量 `function` 一位置参数 | tool5调用1857；原目标调用1860 | 原for实际元素及其求值/错误顺序；不因变量名function把参数改成它的属性或别的对象 |

三处actual syntax均无keyword、无`*args/**kwargs`；对应 `_check_class(pin)`、`_check_function(pin)` 和 `check_view(view)` 源定义均无defaults/kwonly/varargs。两处inner local factory实际call分别在3561/4603，均0实参。它们是确定的固定源码输入；不代表所有认证边、所有实际callee metadata或所有解释器环境都满足native成功快路。

原helper的D1实际代码/参数绑定与D2闭包cell消费仍需按实际原路径证明；D0不能自动推到D1/D2。后续body内对其他可变对象的受保护消费仍各自需要fresh门。原方案没有将全body不可变或无条件D3首bodyfresh列为这些调用的统一要求，不能据未满足该额外强条件就把所有D0方案判死；也不能拿D0替代某一原边已有的更强要求。

## 2. metadata校验与subject调用分开

control `_check_function` 和catalog `check_view` 是检查actual subject/原行；它们没有调用这个subject。它们需要的是：在真正认可metadata结果的共同消费点，原code/defaults/kwdict entries/closure/cells等及相应caller来源状态同时满足原谓词。逐字段曾经通过并不构成最终一致性。

这个验证结果不授予以后任意调用subject的永久权限。以后如有actual调用，它是另一条fresh边。原 getter/audit 的顺序、次数、原失败分支、原异常类别/首对象、锁和引用释放仍是设计约束；只在某条正常返回末尾加检查，不能宣称所有失败、publication或调用分支已修。

原方案还明确：metadata锁只约束遵守协议的写方，只在metadata区持有，不跨原业务/helper调用。后续C联合门不能用跨原callee持foreign锁来替代证明。阻塞准备、可能释放GIL的操作、可能触发析构的引用释放与最终C检查/dispatch必须各自安排并逐操作论证。

## 3. 静态代码路径和原runtime身份分开

raw JSON的 `index` 是库存编号；只有父 `constants[槽] = ['code', 子index]` 的槽号才表示co_consts路径。同名或匿名后代不能按name/qualname去重。

| 五具名入口 | r1106冻结编译树的精确路径（每个role各有自己的raw0） |
| --- | --- |
| control `_check_function` | `0[22] -> 11[1] -> 12` |
| control inner `_AUTH_CATALOG_DEFINE_class_pin` | `0[23] -> 15[1] -> 16[57] -> 17`；其原返回 `_class_pin` 为 `17[1] -> 18` |
| control inner `_AUTH_CATALOG_DEFINE_check_class` | `0[23] -> 15[1] -> 16[58] -> 20`；其原返回 `_check_class` 为 `20[1] -> 21` |
| scope `_authenticate` | `0[16] -> 18[22] -> 19` |
| registration `_check_stack_class` | `0[16] -> 9[27] -> 10` |

独立报告须保存直接producer、原ABI、完整entry前缀、MAKE_FUNCTION/STORE/actual return的静态对应，并核429节点/426条父子边的完整树关系。树映射是后续实现的确切输入；它不等于运行中的原module CodeType、已认证原factory或native registry已经存在。

r1107已建立两个inner local factory的词法来源；此处补const路径，不重新把它们列成module-alias来源问题。相反，r1106明确保留的P0首次捕获前提仍在：五项事实自洽不证明function/code来源，首次code getter可能有audit。不能用离线raw0、当前function/current co_consts、新建同结构code或hash相同来冒认live原对象身份。N1中“静态后代路径目录”可在本批完成，N1中“原runtime根的可信取得/强持有/真实producer绑定”仍未完成。

## 4. 后续实施设计要收敛的选择

下一批聚焦最早原根取得与共同metadata消费点的具体实现，输入为本批三actual incoming、两个local producer和直接catalog。需同时给出native自身入口如何来源可信、早期原factory/后代expected如何取得、怎样绑定真实function及actual-return/STORE/effect、怎样保原锁和异常/释放，以及>=3.11支持策略。不得只将本批静态路径转为运行时重pin helper，也不把新registry接受任意value当来源证明。

本批材料即使复核0，也不是完整可实施设计两审。N0–N4/COMPAT仍有工程工作，完整source/execution/产品实施均False。完整来源准入前，禁止运行候选、局部bootstrap/control模型、旧绿/F001或QA；既有已耗compile/诊断不重放。

原保护范围、正常库/service-dist/ACLsettings/平台/Git、17lease/1449slot/406尾/五S002、原预算和禁用诊断边界保持。r920/r964耗不重放，r967不执行不重问，P06用户无历史材料不再索取。完整Goal active未完，非只剩验收。

# 下一批：把最早可信根和共同消费点落为可实施设计

源码父仍r1106 SOURCE02完整159，文档父为r1109 DELIVERY；本批当前Root合同为02，01及可逆澄清保留。先读CONTRACT-DECISIONS01、ROOT-CONSUMPTION-CONTRACT02、INDEPENDENT-CODE-ROOTS01、两材料审及r1108官方调用链证据；不用重新盘点已完成的五路径/429树或24factory运输。

1. 指定最早可信原factory/function/code及native入口实际取得事件：原expected根来自哪个已接受bootstrap，如何避免P0 audited getter把current污染结果自洽认领，原对象何时强持有、怎样绑定本批确切后代const槽。静态raw0/hash不是runtime identity；接受任意result的register/publisher不是来源证明。若机制不能建立这条来源，明确保红而不是继续加同类getter。
2. 对三条actual incoming落实D0联合metadata/source/fact检查与actual call同一消费机制；保原一次lookup、实际原实参和原tool/原返回/原STORE时序。metadata subject没有被checker调用，joint检查与以后subject调用是两条不同契约。分别处理code/参数/closure实际消费的D1/D2需求，不能默升全bodyD3，也不能降低某边原本更强的要求。
3. 完整说明原锁域、准备、GIL/回调、错误和释放：原锁不能跨helper调用；不得禁用全局GC/trace/monitor/eval或读取/操纵真实frames。原audit getter的成功/失败、原fail次数和partial/STORE/返回清理必须逐分支保真，不只解决正常末尾。
4. 将native自身原根、初始化/加载失败、>=3.11版本平台与打包回滚放在同一确切设计中；本批不授予build/install/import。设计双审完整通过后才能生成完整候选和源码双审；源码准入之前继续禁止候选运行/局部bootstrap或control模型/旧绿/F001/QA。新compile需新确切输入及未耗claim，不重放已耗内容。

保护与原预算、原禁止项保持；正常库等不动，r920/r964不重放、r967不执行重问、P06不再索材料。完整Goal active未完。
