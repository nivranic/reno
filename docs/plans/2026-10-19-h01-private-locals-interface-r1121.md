# r1121：namespace-resolver接口与L退出协议

r1121已选择固定三模块的namespace-resolver新接口：subscription保reserved或MF2解析，get/contains及枚举只看局部绑定，明确print示例的不对称。13处原顶层import确实运输locals；默认C实现不读locals，自定义import第三参G→L是显式兼容变化。L十字段/活动lease/退出detach释放与A/J分寿命已具体设计；未新K，完整N4/消费者仍待，B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

32完整输入pairs每侧7991235B，11份既有官方3.12.10文本1224973B、22原文区间、三AST/27顶层def头/13顶层import；无新官方文本下载。父r1120完整60文件10395704B、159源码7956657B及保护56同18lstat缺席核同。

接口是具名执行合同修订，非G exactdict透明替身：get/contains/keys不枚举B，copy为局部绑定快照，generic merge不承诺原子G拷贝；自定义import依赖locals is globals/exactdict的旧行为未保真，不禁止原hook，也不宣称修订已获完整实施批准。未来actual K仍须核reserved LOAD_NAME、def头/flags/模块scope及annotations/hidden真实消费；当前AST不代未来指令准入。

退出只退休本次L，不提前退休A/J；十字段、heap type/module环、GenericAlloc已track、活动强根、detach-before-release和外部已返回引用分别说明。完整native state/type/attempt/J生命周期、实际I001/I002、P0/23P2/CORE/联合metadata/三D0D1D2/N0–N4/COMPAT仍待。原control1MiB是累计控制数据读取计费，不是已确认的native总RAM限额；新内存/引用成本单独核算，原限额不放宽。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍未完，非只剩验收。

# r1121：固定三模块的 namespace-resolver 接口与 L 退出协议

状态：修订02，拟议执行合同，待本版静态双审；保留01原红审，不是完整来源方案或产品实施批准。继承 r1120 的 MF2，当前产品仍 r1106 SOURCE02 的完整159文件。本批无新产品候选、compile/import/运行、native build、模型、QA或诊断。B001/B002仍open2，full_design_approved/source_approved/execution_admitted/complete_Goal 均False。

## 1. 决定与原合同

选择一个固定范围的 **namespace resolver L**，不把它称为 G 的 dict 替身，也不宣称实现 Mapping ABC 的全部定律。修改对象限 control/scope/registration 三个原 module 的执行；原 helper 从 `helper(original_exec,K,G)` 拟改为 `helper(original_exec,K,G,L)`，helper body、builtin exec、该次原 get_code 的 actual K 与 actual module G 不替换。

原 r1109 的8条要求及 r1075 第143–145行保护具名认证子图、原 getter/audit/callee、原 source/STORE/first 错误和传播，不是“所有 Python 模块都必须可运行”或“每个解释器观察都不得改变”。r1118 NEXT 第3项允许先提出执行/信任边界修订及完整差异与代价；不代表修订已获完整实施批准。本文明确选择接口，不以罗列 delta 自动宣布等强保真。

比较过三种方案：MF1转G后造KeyError仍保留 r1120 的透明性否决；完整 G exactdict 替身无法同时给两个保留名提供封闭原根并保持对象身份；本方案选择显式 resolver 合同，保留原普通 LOAD_NAME 的 MF2 具体算法，承担下列外部语义变化。

## 2. 完整接口表：名称解析与局部绑定明确分开

两个保留名固定为 `_AUTH_NATIVE_INGRESS`、`_AUTH_NATIVE_ATTEMPT`。G中同名 value 始终不作为这两项的来源。普通 key 必须 exact str，str子类及其它key以固定TypeError拒绝；不先执行未知 hash/eq/str/repr。所有操作须先取得 §6 的活动 lease；G在每次依赖键查找/写入前全键 exact-str 前验。失败属新准入错误，不冒称原 dict 错误。

| 接口 | OPEN 时的精确定义 | 结果/缺席/失败 |
| --- | --- | --- |
| `L[key]` / mp_subscript | reserved只回本代state-held原C/A；普通名称按r1120 M1–M6：原G首查、原G次查、actual当前B exact/generic分支、原完整NameError | reserved缺根/错代/退休以非KeyError失败；普通最终缺席为原M6 NameError，不以KeyError触发evaluator额外fallback |
| `L[key]=value` | reserved固定RuntimeError拒绝；普通只调用原PyDict_SetItem(G,key,value)一次 | 原primitive返回/错误原样运输；返回0不证明effect或phase永久一致 |
| `del L[key]` | reserved固定RuntimeError拒绝；普通只调用原PyDict_DelItem(G,key)一次 | slot不包装删除错误；若由原DELETE_NAME消费，evaluator仍对任意删除错误作既有NameError转换 |
| `L.get(key,default=None)` | **局部绑定查询**：reserved原C/A；普通仅G查一次，不查B | 无错缺席回所给default；原查找错误透传；不调用subscription后捕捉NameError，防止吞掉B回调本身的NameError |
| `key in L` / sq_contains | **局部绑定存在性**：reserved先核原根；普通G查一次 | 只给局部绑定的bool；准入/原查找错误透传，不吞成False |
| `keys()`、`iter(L)` | §3局部绑定快照，G中非reserved键保持G插入顺序，末尾追加两reserved固定顺序 | keys返回新list；iter返回独立tuple快照的iterator；没有live dict-view或修改中检测承诺 |
| `len(L)` / mp_length | 同一局部绑定集合的数量：G总数减G中两reserved碰撞数再加2 | 校验溢出；reserved不能核对时拒绝，不把无效根计入有效绑定 |
| `items()`、`values()`、`copy()` | 同一次§3快照中的实际局部绑定值；reserved值为原C/A，绝不从G取替身 | items新list of exact tuple；values新list；copy新exact dict；各输出独立拥有引用，不是可撤销view |
| 其它dict API/属性 | 不提供update/setdefault/pop/clear、`__dict__`、弱引用、pickle/reduce或公开构造入口 | 未定义属性保持标准AttributeError；没有通用state setter；不提供L的公开close/retire方法 |

这是一项**显式的不对称合同**：假设G缺`print`而actual当前B含它，`L['print']`可命中，`L.get('print',d)`回d、`'print' in L`为False、keys/items/copy不含print。subscription回答名称解析，其他上述方法回答局部绑定；不能向调用方宣传get等于try-subscription或keys穷尽所有可解析名称。若产品接口要求这些dict定律，本方案不满足，须重设计。

外部直接 `L[key]` 也沿相同resolver算法，但其actual当前B属于实际调用时的Python frame或解释器fallback。**不缓存原module B，不把外部调用当原module来源，不读取真实frame/locals。** locals()/无参vars()/PyEval_GetLocals等公开原观察在该module执行期间可以拿到L；L不是秘密。外部仍可改G；L只保护这两个保留名的自身读取，不是整个G的防篡改层。

## 3. 枚举快照、所有权和 generic merge

固定native方法的来源是原actual immutable type及静态C method/slots，不经可写Python alias。快照选择如下算法，尚无产品实现：

1. active lease持原L/G/C/A及所需primitive。读取G大小n，检查n+2及指针数组字节的溢出；在原metadata短锁外分配最多n+2对自有key/value slot的C缓冲，全部初始化NULL。此新内存分配可能失败，不能报已满足预算。
2. 分配准备结束后，持同解释器GIL重核actual type/state/generation/OPEN、原G与reserved根；以checked加法确认current_G_size+2<=pair_capacity，且每次写缓冲slot前再核index<pair_capacity；预留的两格只留给reserved，不作为接纳普通G增长的额外容量。变化超容量以固定新准入错误退出，不无界重试，不重新pin。随后无分配/无Python调用/无等待/无任意DECREF地用PyDict_Next扫当前G，拒非exact-str键，跳过reserved碰撞，逐项Py_NewRef强持实际键值，最后追加自有reserved exact-str键和原C/A引用。遇前验错误先退出这个raw段，再构造错误和释放已取得项。
3. raw捕获后才分配Python输出容器；tuple/list填槽仅移交本次拥有的引用，不从G重取。copy用本次自有exact-str键值建新dict；输出构造和任意释放都允许再入。即使此时G改变，输出仍是本次捕获的局部绑定快照。不是对callback后的G作当前性证明。
4. 输出分配失败保实际first；已取得C缓冲项按实际取得顺序逆序释放，短锁外清理。list/tuple/dict拥有的项按原容器销毁规则释放，不能声称容器内部也按本方slot逆序；本方额外自有引用另计。外部返回值可继续强持G值、原C/A及其间接可达图，不能声称退休会撤销它们。

len采用同一无callback raw遍历计数，无需构造整份快照。get/contains的单G查找在原前验后进行；所有接口均可因实际源字典不满足准入条件拒绝，不能宣称任意外来key/dict都兼容。

PyMapping_Keys/Items/Values可以通过这三个固定方法取得list，区别于原dict view。`dict(L)`/PyDict_Merge的通用路仍可能先keys、后逐key subscription；两步间G若删除某键且B含同名，后者可取得B值；全缺则是NameError而非原G的KeyError。这是本resolver的公开非原子merge合同，**不等于copy()快照**，不隐称保G拷贝语义。外部依赖dict(L)严格等于G的代码存在兼容代价。

## 4. 实际原消费清单和未来 K 的准入

不能用“原源码无locals这个Name”推出不存在L的消费。本批补齐两类实际路径：

| 原消费 | 本方案处理 | 保真边界/合同变化 |
| --- | --- | --- |
| module LOAD_NAME/STORE_NAME/DELETE_NAME | 采用§2，普通解析保持r1120两G读取及完整M6，普通set/del转原G一次 | 增加native slot/检查/引用与独立准入错误；reserved是新绑定语义 |
| 原globals()、函数LOAD_GLOBAL/STORE_GLOBAL/DELETE_GLOBAL | 仍G | 不冒称L保护这些操作；原callee认证单独接线 |
| **IMPORT_NAME** | evaluator仍取原实际B中的__import__并按原分支调用一次，第三位置实参从G变L，其余name/G/fromlist/level保持原实际值 | 这是当前原导入就存在的实际运输变化，非未来假设；不插import wrapper、不换原import target、不拒绝全部overridden import |
| 默认C importer | 3.12.10的PyImport_ImportModuleLevelObject完整body不读取/转发locals；builtin文档亦明确unused | 该参数身份变化本身不改变此函数通过locals产生的行为；不扩写为所有import回调/外部观察完全同一 |
| overridden __import__ | 接收实际L，可以观察、保存和操作它，原调用仍允许发生 | 依赖`locals is globals`、exactdict、dict-view、get与subscription同义的合法实现行为会变化；本文选择新L合同，**不声称已等强保持这些旧依赖或获得完整批准**；其返回/异常仍原传播，不追加成功门覆盖其first |
| locals()/vars()/dir/调试工具等公开原观察 | 有机会拿L并按本合同操作，不能当作不可达 | dir等结果可含两个reserved；身份/类型/view/错误变化均显式保留，不通过关闭监控/拒绝观察“修复” |
| SETUP_ANNOTATIONS | 本版固定module执行范围不接此原消费，MF2不作annotations局部映射 | 未来实际新K必须证明module求值域无此opcode；不能只凭当前AST缺席放行 |
| module inline comprehension/hidden-locals merge | 本版不接带该module消费的新K | 未来K须检查实际module执行指令与scope，不能将每个nested code一律禁止；原nested函数合法语法保持，若其实际f_locals是独立函数locals不受L合同排除 |
| nested import/GeneratorExp | 按其真实执行scope分别记录 | 原三module各一个GeneratorExp不是PEP709 inline comp，不能冒认其另帧f_locals=L；不因顶层扫描遗漏def头执行表达式 |

本次当前源码AST证据应包含：每个顶层def的decorator/default/kwdefault/参数和返回注解/type_params，以及非声明语句求值子树；当前三源顶层def分别11/9/7、这些头表达式均空、无顶层class，各一个GeneratorExp。13个直接顶层Import/ImportFrom与literalFalse保符号块的26个词法imports、原函数体内的27个imports分列：literalFalse为control7/scope13/registration6，函数体内为7/14/6。这些AST节点不是实际执行或IMPORT_NAME指令数；原函数体调用有独立执行frame，不能把其f_locals实参统一当成module的L，也不能未核就宣称一定为某个dict或None。此事实只用于当前原输入，没有新K，**future_K_admitted=False**。

未来完整源码审核必须针对实际版本单次get_code得到的K：核原code来源/全部typed const子树、compile flags、module求值域、def头、实际新增reserved LOAD_NAME及其位置；准确记录所有IMPORT_NAME及其实际scope；检查SETUP_ANNOTATIONS、LOAD_FAST_AND_CLEAR等相关inline/hidden路径。拒绝未设计的新消费是对新产品版本的实施门槛，不在运行中偷偷禁掉原nested函数语法，不修改co_consts或伪造K。当前pyproject >=3.11不收窄；这些3.12.10源码路径不等于跨版本K审查已完成。

## 5. actual L 创建：具体 API 和自有字段

仍以r1112固定T0、原native module state和actual C/A来源为前提，不从public module alias或G读回建立expected。用原PyType_FromModuleAndSpec(actual_native_module,固定静态spec,固定原object bases)的实际返回建立state-held type；IMMUTABLETYPE、DISALLOW_INSTANTIATION、HAVE_GC，不设置BASETYPE，固定native mapping/sequence/method/traverse/clear/dealloc slots，无dict/weakref/member setter。

原wrapper直接调用捕获的原PyType_GenericAlloc(actual_original_type,0)而非Python type(...)或当前tp_alloc别名。该API已经GC track对象；返回后无callback段立即UnTrack，保持全NULL字段可被遍历/清理，再按下面顺序强持原根并设置OPEN，最后Track一次；不能误作GenericAlloc后无条件再Track，也不称DISALLOW_INSTANTIATION本身创建了actual实例。

本版拟定的L自有PyObject字段取得顺序固定为：owner_native_module、G、C、A、runtime_error_class、name_error_class、type_error_class、reserved_ingress_key、reserved_attempt_key、name_attribute_key。generation用C整数副本而非外来可变对象；phase/active_count为C字段。error/key根来自已接受初始化的actual原根，不晚读取G补根。Py_TYPE(L)由heap实例自带所有权，与这十个字段分列。初始化partial失败只释放实际取得前缀的逆序，不造未取得项。

type的ht_module强持module；native state也持type/C/必要原根，L又持module/G/C/A，允许形成真实引用环。本文只给L实例边界；native module/type/attempt/invocation J全部边与共同发布仍是完整N4义务，不能把§6冒认整套N4闭合。

## 6. L 的活动调用与退出：不把 A/J 一起过早退休

L的C阶段为INIT→OPEN→RETIRING→CLEARED；与native module ACTIVE/RETIRING、loader attempt A、每次producer invocation J的阶段分别保存。**wrapper结束module exec只退休自己的L，不退休A/J。** 已产生function/facts/J可能在exec返回后仍须消费原A；其持根和最终释放由各自owner承担，不依赖已清L来补根。

每个L槽/方法在同解释器GIL下先raw核type/state/阶段与必要根，检查active_count溢出，取得self及本次实际需要的字段强引用，再增加active_count；准备前没有可回调操作。跨B mapping、字典watcher/旧值释放、输出分配等可能再入时只用自有强引用，不能裸持state指针跨owner释放。原module state可达由本次owner_native_module自有根保证。L不在module state建立强持registry；wrapper/实际frame/活动lease本身持L，避免用永久registry掩盖泄漏。

原helper返回或抛错后，wrapper对同一actual L做内部retire：raw标RETIRING，禁止新槽/方法lease；不得从G/current alias寻找L。若active_count==0，retire当次立即执行下述raw detach，不能只标RETIRING等一个不会再来的leave；若active_count>0，只标RETIRING并由最后一个已有调用完成detach，不阻塞等待、不在短锁里释放根、不强清活动引用。已准入的普通primitive可以按其原返回/错误完成；原错误发生后不追加成功检查覆盖first。reserved成功则仍须在返回原C/A前完成原metadata/generation最终raw重核，不能从早期lease推导调用时仍有效。

每次调用共同leave流程（OPEN成功、OPEN失败、重入后RETIRING均适用）：

1. 先独立拥有要返回的实际结果或实际first异常；本次lease取得的字段副本与临时B/输出/缓冲各有单独的已取得清单，不能当作L十字段已归还。
2. active_count仍包含本次调用，self仍强持。先按本次实际取得逆序释放所有lease-local自有引用和未移交临时项，逐项先从本地owned槽置NULL再DECREF；跨可能callback只用尚自有的根。OPEN且字段仍由L持有时，其本地副本并非最后根；不据此推断所有临时项都无析构。
3. 本地字段副本清完后，在无callback raw段将active_count减一；若此时RETIRING且计数为0，则执行唯一detach：把L全部十字段移入固定C栈数组，L字段全置NULL、phase=CLEARED。OPEN计数归0只退出，不清L字段。retire入口原本计数为0时直接复用同一个detach，不额外等leave。
4. 先于任何DECREF完成detach，再在短锁外按L字段实际取得逆序释放该C数组；没有取得的项不释放。最后归还本次self强引用。这是三套独立所有权：lease-local副本、L字段、self，不合并计费、不遗漏或重复归还。**detach是无callback段，release不是。**重入看到RETIRING/CLEARED而非半清根，不从current补根，不复活L。generation不回绕复用旧实例。新外部操作在RETIRING/CLEARED按下节独立固定C异常primitive设置拒绝错误；不回KeyError/空dict/0/None，不读取已清字段。对del-name消费仍须尊重evaluator外层既有错误转换，不能把slot非KeyError宣传成所有消费者最终都RuntimeError。

tp_traverse只Py_VISIT实际非NULL十字段及heap type，不分配/不改阶段/不执行外部Python；typeobject.c的subtype_traverse注释明确说明实例到heap type的引用和循环遍历需要，不能漏这条边。tp_clear使用相同retire/detach逻辑，partial/repeated安全；active调用强持self和owner，不能假设collector会替代活动lease。tp_dealloc先UnTrack，再执行幂等detach/release，保存actual heap type；以固定PyObject_GC_Del释放实例，之后才DECREF保存的heap type。无公开析构回调，不使用外部当前tp_free认领原路径；具体C版本及module/type环仍须在完整源码两审。

wrapper已有first错误时先用PyErr_GetRaisedException取得其原实际对象，完成原清理及L退休后PyErr_SetRaisedException恢复同一实际对象；不保存全局异常缓存、不重建traceback、不加journal。清理任意DECREF及其既有unraisable路径仍可能再入；成功返回值若已owned则先持住，不由L retire撤销。清理本身的独立错误按完整原N4路由表处理，本段不覆盖17lease/1449slot/406尾/五S002或冒认全图first清理已闭。

### 6.1 清空后的固定错误根与局部清理错误

01的十字段全清后再引用runtime_error_class不可实施。02明确：retired拒绝所需primitive取固定native C API符号`PyExc_RuntimeError`，不取L/native state/G任何已清或可写字段；这项固定builtin primitive纳入原native T0授信，OPEN期自有runtime_error_class必须与它同一。Python层替换builtins.RuntimeError不能改变此C根；任意外部native改写C全局不在本来T0可信二进制前提内，不扩写成解释器防篡改证明。

固定slot/method先确认自己实际native实例的安全布局，再只读其C phase；非OPEN走固定`PyErr_SetString(PyExc_RuntimeError,固定ASCII消息)`，在此路径不先取十字段、raw state或generation。错误设置本身可分配失败，实际MemoryError等照实返回；保证不主动KeyError/fallback，不承诺任何资源失败都能产生RuntimeError。OPEN分支随后才取得正常lease与原根。该错误根无需留在已CLEARED的L字段里，也不因拒绝而恢复L的G/C/A引用。

局部清理进入时以PyErr_GetRaisedException独立拥有本次原first（成功时为空），清理期间不把它留作可被回调覆盖的current error。每个本方明确可设置错误的清理操作返回后，若出现实际异常且first为空，取走该实际对象作为本次first；已有first时保原对象，后续清理错误走完整N4既有次错处理，不覆盖first。DECREF所调用析构的既有unraisable处理仍由解释器承担，本方不添日志、异常缓存或递归“清错重试”。本方所有owned槽先置NULL再释放，任意释放引起再入不重复清它。

所有lease-local/L字段/self清理完成后才恢复同一first并返回错误；没有first才移交成功结果。如果清理首次产生错误而此前结果已owned，则先按本方所有权释放该结果，再恢复这次first，不返回“非NULL结果且error已设置”。上述局部规则不取代全图原清理错误/notes传播表，完整N4仍open；不能用“清理不抛错”或三段释放计数冒认已运行验证。

外部已拿到keys/items/copy/C/A/J时，其自有引用继续有效；从清L并不能撤销这些对象。C/A/J每次真实消费仍独立核原owner/phase，不以L是唯一能力通道、秘密或“首次调用”作来源保证。

## 7. 预算、兼容与下一步实际工程

原control1MiB在具名旧方案中是同一budget.control_bytes累计控制数据读取的上限，实返bytes计费，纯内存不charge；不是已经批准的native heap总上限。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10均不放宽。L/type/十字段/临时快照缓冲/输出及每次J的实际分配、持有时长、递归/重入和失败成本必须独立核算；不报未实测的sizeof/峰值/耗时，也不将N2的原environment义务改名为纯内存预算。

完整来源方案仍须同版完成：I001实际PL接线与新K；I002 effectless每次fresh J→原sourcecaller实际local→原return/STORE/effect（foreign J2不能占J1槽或补expected）；P0两次环境及tools/rows/owner/context/catalog第四目标/私有ABI；23P2/CORE第24项；联合metadata/三D0/D1/D2/N0–N4与>=3.11平台打包/失败回滚。本文固定的是L接口选择和实例退出协议，**不是完整设计获批或任何源码缺口已关闭**。

实施顺序：先将这份具体合同与所有代价纳入完整设计双审；随后才可生成完整产品候选和可逆差异，逐实际新K核指令/来源；源码双审及执行准入后，才安排新输入对应的最小测试及原限额回归。本批只做惰性官方文本/当前AST/完整文件指纹检查，不执行上述API或运行模型来试探方案。

正常库、服务/dist、ACL/settings、真实平台与Git不操作。禁profiling、真实frames/locals读取、断点、定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问；P06历史身份0/7，用户无备份，不再索取。canonical apply本批0/历史11，产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍未完成，非只剩验收。

# 下一批：完整producer/原消费者和生命周期接线

父r1121，产品仍r1106 SOURCE02完整159。L接口已明确选择；这不是实际PL已接通或完整设计批准。

1. 将新L接口与IMPORT_NAME第三参变化纳入完整具名合同；保MF2两次G/current B/M6、原set/del、原import target及其first传播。实际新K必须核reserved LOAD_NAME、def头/flags/module scope、annotations/hidden及每处import，不把旧AST当新指令。
2. 接effectless每次fresh J到原sourcecaller真实local、原actual return/STORE/effect；foreign J2不占J1槽或补expected，不把L可读C/A当caller证明。给P0两次环境/tools/rows/owner/context/catalog第四目标/私有ABI、23P2/CORE第24项及联合metadata/三D0D1D2同版源码设计。
3. 完成native state/type/A/J共同N4、失败和清理错误表及>=3.11平台/打包/加载回滚；L的退出不提前失效A/J。原environment N2与新内存/引用成本分别核算，control1MiB仍原累计读取限额。
4. 完整设计双审前不生成产品候选；源码准入前不compile/import/执行候选、native build、局部模型、旧绿F001、QA或诊断。保持17lease/1449slot/406尾/五S002及原全部预算，不重放旧单次授权，不再索取P06。
