# r1123：原context读取的实际操作数与消费边界

r1123已重建38处原context的实际消费：14立即下标/5直接解包/19别名后读；19别名及5处20个解包接收点均是global，否决identity-only门作为完整接线。原短路、callee先取、三次独立context读取、global再读和owner动态状态必须逐点保留并联合消费。三模块78顶层语句/27空头def已核；单C body驱动P0仅有限候选，共同G及原for/gen/import/CREATE时序未闭。B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

25完整输入pairs每侧4289814B，25确切原文区间、2份既有官方3.12.10文本156545B；无新下载。父r1122完整49文件10185186B、4AST、159源码7956657B及保护56同18lstat缺席核同。

新证据改变下一步：不能只对context外层identity准入后，让后续原global alias/primitive/callee继续裸用；实际观察值须真正local运输并接独立P0/J原根的联合消费。control两处or末项保持条件可达，scope/reg同一call两次Load及后续第三次Load不合并；原callee先于实参、两位置参数和tuple完成后STORE保持。

owner的slot内部为六项mutable list，records/G为可变dict；原phase/attempted/STORE/raw effect/first和foreign来源均不由context身份认证。r1122 CORE actual row/pair/3local方向保持。27当前顶层def头为空，不代future K/code/version/CREATE证明；78语句库存非运行次数。

单原module直达C内持P0创建链可继续设计，但effectful orchestration不同于effectless准备。隐藏module M仍由原EvalCode创建，不能套用R2对象错位否决；共同G、catalog第四目标、generator/for/import/原发布及constructor绑定前partial仍须具体接线。材料双审0不是完整批准。

actual P0 producer/真实参数、38处后续operand/联合metadata/三D0D1D2/native state-type-A-J N4/>=3.11兼容和打包仍待。control1MiB仍累计控制数据实读上限，不是确认的native总heap限额。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# r1123：38处原context读取的实际操作数与消费边界

状态：修订02，有限静态调查与后继消费契约，不是完整P0 producer设计、源码或执行批准。父r1122完整封存保持，当前产品仍r1106 SOURCE02完整159文件7956657B。B001/B002仍open2，full_design_approved/source_approved/execution_admitted/complete_Goal均False；没有产品候选、compile/import/运行、native build、模型、QA或诊断。

## 1. 新证据改变的下一步

r1122已列38个原`_AUTH_CATALOG_CONTEXT` Load，本轮进一步逐项重建它们的原表达式、短路可达性、接收名称和下一次实际消费。结论是：**不能采用“在38处各加一次context identity检查，随后原代码不变”作为完整接线。** 外层context相同不等于原global别名、实际callee或owner动态状态仍为原值。

| 直接constructor作用域中的原Load | control | scope | registration | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 同表达式立即下标 | 8 | 3 | 3 | 14 |
| 直接四项解包 | 5 | 0 | 0 | 5 |
| 先写context别名，下一句再读取并解包 | 10 | 5 | 4 | 19 |

19个别名接收点全部是原constructor的global绑定；5处直接解包的20个接收点也全部为global绑定。接收点不是20个不同名称，重复绑定另列；不能把名称看似temporary当作真正local。原`_held_original_context`参数是真local，但该事实不会自动改变原body的LOAD_GLOBAL行为。

本轮选择后继的**实际操作数消费契约**：原观察仍发生，实际观察值先由新真正local持有；original expected来自本代actual P0/J独立持有链；需要运行的下标、解包、原primitive和实际callee必须各有对应的原根及联合消费。C检查返回Python后再重读可写global并执行，不能作为已闭的实际消费保证。本批未生成这些门或新增可执行checker占位。

## 2. 14处立即下标：原可达性和求值次序不能合并

control的八个坐标（行号1起、UTF-8 AST列号0起）：12161:16、12178:58、12353:27、12354:30、12381:16、12398:58、12573:27、12574:30。

- 12161/12381从context[3]取primitives，原STORE到`_dh_p`/`_hp_p`仍为global；后续从该别名取callable是另一实际读取，不能把第一次context门扩成整个helper可信。
- 12178/12398的context[2][0]是原`_dh_kind`/`_hp_kind`的一位置实参，位于`or`末项。只在原短路分支可达时读取并准入；不能在if之前无条件读取、分配、拒绝或释放。
- 12353/12573取context[2][0]，12354/12574取context[2][3]，分别写原control-lock/control-globals绑定。原foreign source锁在12350/12570已释放，随后才进入control锁；不合并两个锁域、不跨原callee持锁。

scope10282:12/10282:40/10283:12，以及registration4328:12/4328:40/4329:12，分别保以下顺序：

1. 原tuple前部的类型/iterator等表达式先求值，不能被新增门提前越过。
2. 第一次context Load取[3][3]，原callee先被求值并持有。
3. 第二次独立Load取[2][3]作为第一个实参，第二实参是`'__builtins__'`；该原call只有两个位置参数，没有另加missing默认参数。
4. 原实际call返回后，才发生第三次Load取[3][0]，并以新建`lambda: None`与`'__builtins__'`实际调用。
5. 整份tuple RHS成功，才到原`_SCOPE_HELPER_NATIVE`/`_REGISTRATION_HELPER_NATIVE`的global STORE。失败不能发布半份tuple。

新增consumer必须持住原先选中的actual callee，不在dispatch重新lookup。若前面的原读取/call已抛错，不在它之前或之后补一个成功门来覆盖first。这些静态先后不代表已验证运行窗口或future K指令。

## 3. 5处直接解包和19条别名链

五个直接解包坐标为control13061:62、13661:66、14053:62、14235:62、14635:62。每处先取完整actual RHS，再按原顺序写tools、tool_rows、owner、primitives四个global，后续primitives/owner又从各自global读取。

13060原`object()` RHS先于13061；不得把context门移到该RHS之前。13661只在原settlement检查失败分支执行；不得为普通成功路线增加该读取或拒绝。原UNPACK先取得四个待写值；UNPACK失败本身不能说成已经向四个目标部分STORE。解包成功后，各次原global STORE仍可能中途失败，新门及清理须保这时的实际partial；不能用一次原生“全部发布成功”替代四次原STORE后声称完全透明。这里是语言操作边界和官方C文本的静态区分，不是本轮观察到的新K指令或运行失败。

19条别名链是：

`原context Load → 原别名STORE_GLOBAL → 原别名再次读取 → 四项解包/各次STORE → primitives再次读取与解包 → owner再次读取与解包 → 后续实际使用`

新local对第一次观察持根，不授权删原别名STORE或再次读取。再次观察的actual operand也必须同held来源关联；expected不能取自这个current别名。若决定让某个实际下标或解包消费新local，仍须把对应的原global观察、原STORE/异常可达性及新增delta逐点列出，不能默默把原所有global绑定改成fast locals。

本轮完整坐标、所有接收点的global归属、相邻原语句及表达式结构保存在READER-CONSUMPTION-INVENTORY-01.json。原5处20个目标与19处别名的统计口径是语法接收点，不能当未来新增local或运行次数预算。

## 4. context身份不冻结owner、catalog或foreign来源

原owner是`(lock, slots, records, globals)`。catalog142–158按原descriptions创建每个六项list：`[phase, present, actual_RHS, STORE_attempted, raw_effect, failure_code]`；外层slots tuple不使内部list不可变。control/scope/registration有16/13/8个slot，records和globals均为可变dict。

因此context同一也不能省：

- control原slot13/14/15的初态、原锁和实际phase检查；例如14206–14222先写phase/RHS，再观察public值、置attempted、实际global STORE、再读raw effect。原except的失败标记及bare raise继续保持。
- CORE15381共享fact getter、15386–15399的attempted/原发布/effect/phase及15439原target观察、15508实际零参call。r1122的actual-row pair与三个新local方向保持，不被context identity替代。
- scope10532–10539、registration4607–4614的fact→attempted→records STORE→raw getter→success，及各自原except/bare raise。
- scope10478、registration4519/4531及link producer从foreign module dict取得的context。这些是另一批实际对象，不属于38个直接Name Load；不能用本模块context身份授信它们，也不把本轮38目录冒认完整caller闭包。

工具function的code/defaults/kwdefaults/closure及原cell、globals/builtins内容、实际owner phase、J/attempt代际与实际call仍须联合核。外层exact tuple身份检查最多绑定该外层原对象；它不证明上述动态内容或下一calleemetadata不变，也不证明公共getter读取与实际消费原子一致。

## 5. 本轮三种方向的处置

| 方向 | 当前处置 | 原因及必要代价 |
| --- | --- | --- |
| 每处context读后identity-only检查，原后续global消费不变 | 否决作为完整接线 | 19别名/20解包接收点是global，后续actual operand/callee/owner动态状态未覆盖；不能用38个绿色布尔检查冒认fresh消费 |
| 原观察actual值逐点真正local运输，再由独立原native consumer联合消费 | 选为后继消费契约；未实现 | 必须保原短路、callee先取、STORE/再次读/first/partial；不能“先C核过、返回Python再重读current”。actual P0/J、consumer primitive根及完整错误/释放表仍待 |
| 同一原module直接进入的C body按原顶层顺序持有P0创建链，最终以真实参数调用原constructor | 仅有限架构候选；未批准 | 可作为解决prefix到tail无真local运输的调查方向；其effectful orchestration不能叫effectless producer。同一C body不自动解决catalog第四目标、for/gen/import/CREATE/STORE时序、共同G发布、联合门、N4或兼容 |

第三方向仍保原helper/builtin exec K/G/L及原EvalCode创建隐藏module function M；不能用r1116“public New替换M”的doc/CREATE差异直接否决一个没有替换M的候选。与此同时，未来源码K若改为C驱动原顶层private函数MAKE_FUNCTION/STORE，确实改变执行边界，必须明确审查，不能用“仍exec同一K/G/L”声称原bytecode仍执行。

effectless层只可创建独立J/私有factory/facts且不执行原23P2/CORE body。任何P0发布、原业务factory body或constructor执行都是另一effectful阶段，需要真实原消费证据。native C自有局部持根与Python constructor参数局部也不是同一槽；原P0effect发生在constructor绑定前时不能偷偷说已由其局部证明。

J2各自新建owner/slots并不隔离共同G；其alias或业务发布可能改变J1之后的实际观察。完整方案必须逐项区分per-J slot与共同G publication，不把foreign的STORE当J1 expected，不从current重建原根，不消耗attempt首个/last-result槽。也不扩张为证明任意caller身份或保证共同可写G免受外部修改。该候选的确切共同G/状态处理、原时序及API源码仍未提供，不获实施批准。

### 5.1 原顶层顺序和共同G的具名边界

三份当前module AST分别有32/24/22条顶层语句，含doc/future及未执行的`if False`符号声明；不能将这个78语句库存当作78次运行。顶层def为11/9/7（24个私有factory加3个constructor），其decorator/default/kwdefault/annotation头表达式当前全空。空头有利于界定未来New步骤，但不证明co_version、CREATE或未来code根已保持。

原顺序是imports→maker实际call/STORE→tools形状及逐项for→tool_rows generator→owner_storage实际call/unpack→records→globals()→owner；control还在原位置import helper类型并创建四组事实及native tuple；随后才private def CREATE/STORE、constructor CREATE/STORE、尾部context/factory facts求值与实际调用。

| C可以为本次J独立持有的实际值 | 原共同G的发布仍须单独记录 |
| --- | --- |
| maker actual返回tools6、每次actual tool-row结果 | `_AUTH_CATALOG_TOOLS`、`_AUTH_CATALOG_TOOL_ROWS` |
| actual lock/slots/records/owner | `_AUTH_CATALOG_LOCK`、`_AUTH_CATALOG_SLOTS`、`_AUTH_CATALOG_RECORDS`、`_AUTHENTICATION_OWNER` |
| 实际G的本方强引用 | 所有J仍可指向同一G，不是新私有globals字典 |
| control本次four facts/native tuple | missing/profile/deadline/core事实名及`_AUTH_HELPER_NATIVE` |
| 本次New返回的private函数、context和facts | 原factory/constructor/context同名STORE及constructor开头del |
| 本次constructor持有的owner/slots/facts | handoff/link/ready、业务函数及root的原发布 |

原for每轮先写`_AUTH_CATALOG_TOOL`这个global，再原检查，末轮绑定保留；不能只用C局部循环代替后声称原STORE保真。原tool_rows先选择actual tuple target，再建立generator/取得原左iterator，每轮另读`_AUTH_CATALOG_TOOLS[0]`并实际call；eager C loop不是同一执行合同。原import仍须保实际B的importer和name/G/L/fromlist/level；通用import API不自动保r1121的overridden importer合同。

constructor实际参数绑定前，P0 call、allocation、G STORE或private CREATE可以已经发生。后续New/metadata、alias STORE、参数准备或实际call失败，均须保已有partial与first；不得当作未开始，不重跑maker/tool、回滚G或重试orchestration。最终调用C-held constructor是对原tail名称lookup的明确delta；原callee观察、alias STORE/effect不能因此被省略或混成调用成功。

原module直接进入C的条件下，初次强持X_frame及Python callee返回后的外层frame不同于callee活动期间的环境。foreign J2从其它frame进入取得X2，不能供J1补X1。第三方向依赖这项有前提机制，不把“同一C body”当caller来源证明；catalog原maker的实际创建、独立expected和每次实际call仍要接第四目标。

## 6. 下一批必须产出的完整接线

1. 先选定具体P0实际producer到原sourcecaller的完整路线，覆盖tools6/tool_rows6/owner4/primitives20、18、20、两次环境/原context RHS、catalog第4目标及必要私有ABI。不再重复只列prefix名称或只给环境getter。
2. 以本轮14/5/19和foreign来源边界为消费约束，给实际原观察→独立expected→真正local→联合native消费的具体源码级设计，保原分支、STORE/read/first/partial。actual callee必须同一实际取值，原callee先于参数的次序保持。
3. 联合r1122 CORE返回ABI/3新持根local、23P2 actual call/STORE、metadata/三D0D1D2、native state/type/A/J共同N4，给每个新增引用/分配/清理错误及版本/打包/加载回滚表。
4. 完整设计双审前不生成产品候选；源码准入前不compile/import/执行候选、native build、局部模型/旧绿F001、QA或诊断。当前静态AST目录不当future K字节码或完整认证准入。

原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。control1MiB仍是累计控制数据实读上限，不是确认的native总heap限额；新local/原生持根及生命周期成本另核，不以本轮接收点计数冒充内存预算。

正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问；P06历史0/7不再索取。canonical apply0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：具体P0创建链与联合原消费的一体接线

父r1123，产品仍r1106 SOURCE02完整159；identity-only方向已否决，尚无完整P0方案。

1. 将单C body持根方向形成具名可审的P0 orchestration设计：actual maker/tool/owner、两次原环境、prefix到tail的C-owned根、catalog第四目标及sourcecaller真实参数。区分hidden M、顶层private函数与真正sourcecaller local，不在constructor绑定前假称其local已证明P0effect。
2. 按78原顶层语句的实际求值树说明for逐轮global、原tuple target/gen/iterator、实际importer的name/G/L/fromlist/level、CREATE前expected与后填metadata、原STORE/观察/失败。不以eager C loop或通用import API代替后仍称透明；foreign J2独立slot与共同G效应分列。
3. 同版接38处14/5/19实际operand与foreign context、原primitive/callee联合消费、r1122 CORE pair/3locals/24factory实际return-STORE；补完整三D0D1D2/N4/错误引用预算/COMPAT。
4. 完整设计双审前不生成产品候选；源码准入前不compile/import/执行候选、native build、局部模型/旧绿F001、QA或诊断。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。
