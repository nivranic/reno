# r1120：PL普通名称解析协议修订

r1120裁决MF1只转G再造KeyError并非透明原lookup；选MF2为原module LOAD_NAME的条件解析方向：保两次G读取，再用公开getter取该实际当前B，B命中不造中间KeyError。最终NameError逐操作保UTF8、准确%.200s文案、实际异常/name字段/忽略setter失败后的原对象恢复。完整mapping接口、原消费者及N4仍未闭，B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

30完整输入pairs每侧4495368B，9官方3.12.10文本630352B，其中新增公开pyerrors.h及ceval_macros.h两份纯文本17199B；27原文加C019实际slot补证共28段，三限定AST、父r1119封存54文件10025540B核同。159源码7956657B及保护56同18lstat缺席保持，没有运行普通lookup或故障反例。

原合同保护具名原getter/audit/业务first及传播，不将它扩大成禁止所有新增native资源失败。MF1的type在G缺/B有而新增异常分配可先失败只是静态分支，不称已观察OOM。MF2只对实际module→固定C slot成立，不用外部直接mapping调用的当前B证明原module；不把L当秘密或要求识别任意caller。

DELETE_NAME仍转G一次并由原evaluator对任何删除错误作既有NameError转换。annotations在G缺/B有同名键及双缺时与MF2冲突；keys/items/len/get/contains/hidden merge和外部L合同尚待选择。当前三源有限non-declaration AST无AnnAssign等不代未来真实K/LOAD_NAME/hidden验证。MF1透明性否决不代表全部PL/native不可能，MF2条件方向不代表完整mapping批准。

I002的effectless每次fresh J→原sourcecaller local→原return/STORE/effect、P0两环境及tools/rows/owner/context/catalog第四目标和私有ABI、23P2/CORE第24项、联合metadata/三D0/D1/D2/N2/N4/COMPAT均仍须同版接线。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云和完整Goal未完，非只剩验收。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取。

# r1120：PL 普通名称解析与映射消费的协议修订

状态：有限静态协议设计，待双审。父 r1119 的 PL 尚未实施；当前完整源码仍为 r1106 SOURCE02 的159文件。本文不生成产品候选、可执行 CodeType、编译、导入、native 构建、模型、QA或诊断。完整设计/source/execution/Goal四项仍False，B001/B002仍open2。

## 1. 原合同与本次裁决范围

原 r1075 方案第143–145行及 r1109 的8项原要求保护具名原getter/audit、原callee及清理的顺序、次数、错误class/code/first对象和传播。它们不等于禁止新增native层的所有资源失败。实际type/L/metadata创建仍可有独立新增准入错误，必须列出，不能伪称原业务拒绝。

本次区分两条具体普通名称协议：

| 协议 | 本次决定 | 依据与边界 |
| --- | --- | --- |
| MF1：L只转发G，G缺席时造KeyError让原LOAD_NAME继续fallback | 不作为“保原普通lookup协议”的实现接线 | 原exact-G缺席无异常；MF1增加KeyError构造、归一化、清除及新的可失败分支。仅列成本delta不足以证明保真。不是据此否决一切新增资源失败或所有PL/native |
| MF2：L的固定C槽内保留原G/G/B解析，并直接形成最终NameError | 选为仅对具名原module LOAD_NAME的条件子方向 | B命中时可不造中间KeyError；下面列完整成功/失败算法。普通mapping/annotations/keys/hidden merge不是同一消费，尚无完整映射实施批准 |

MF1的具名静态分支：现有control/scope第15行、registration第14行读取`type`。当原G没有type、actual B含原type时，原LOAD_NAME依次G首查miss、G次查miss、B命中，均不因缺席构造KeyError。MF1新增KeyError构造若分配失败，会先以MemoryError离开，原应执行的B命中未发生。这只是源代码条件分支，没有执行故障注入，也不宣称本机已发生OOM。

这个反例证明MF1不是透明原协议；是否把某新增资源错误纳入明确的新执行合同，是另外的设计决定，不能偷偷将原first错误保真降成“通常成功即可”。本批没有批准那种合同修订。

## 2. MF2实际消费和根来源

沿用r1119显式拟议的helper(original_exec,K,G,L)，原helper body与builtin exec的原audit/EvalCode保留。K仍为该次原get_code实际根，G是该actual module原dict；实际L由原native wrapper创建、强持后传入。当前没有修改这条执行链。

两个保留名`_AUTH_NATIVE_INGRESS`与`_AUTH_NATIVE_ATTEMPT`仍是封闭分支：成功只回state-held原C/A；退休、缺根、错代或metadata失配以非KeyError失败，不能回查G或认领current alias。此分支不执行下面的普通B解析。

MF2的普通解析保证只针对：原evaluator的实际module LOAD_NAME从actual L进入固定native mp_subscript，且该槽没有新建Python frame。这个实际消费位置是设计前提，不靠外来code/G/字符串证明任意caller身份。普通C分派不push Python frame，故此限定位置的公开PyEval_GetBuiltins()可取当前module实际f_builtins；不得用G['__builtins__']重读取代它，也不声称外部直接L[key]时仍取得同一module B。

L并非秘密。外部如果持有它，保留名仍只回原根或非KeyError拒绝；普通解析可能在另一个实际Python frame取得另一个B。那是不同消费，不能成为原module来源证明。原C producer的每次独立J、原sourcecaller local、return/STORE/effect仍由I002单独建立。

## 3. 普通LOAD_NAME的逐操作协议

所需key是原co_names实际exact str。actual L/type/state/generation以及原G身份先验；依赖dict名称查找前完整扫原G的全部key为exact str。原metadata短锁只处理metadata，不跨可能的B mapping调用、异常构造或释放。新增准入失败独立于下面已经发生的原lookup错误，不在原lookup抛错后追加检查覆盖first。

| 步骤 | 实际操作及结果 | 错误和引用 |
| --- | --- | --- |
| M1 | PyDict_GetItemWithError(G,key)，保原首次G读取 | 命中立即Py_NewRef(value)返回；NULL且PyErr_Occurred()则原错误透传，不fallback |
| M2 | M1无错缺席后，再PyDict_GetItemWithError(G,key) | 保原L=G时实际存在的第二次G读取；命中/错误同M1。不以“两次应相同”为由删一次 |
| M3 | 两次均无错缺席后，调用公开PyEval_GetBuiltins()取得actual当前B，随即持有自己的强引用 | 正常具名路径中的B原已由活动module frame强持；不借此证明caller。该API不按G的当前值重建B |
| M4 | 若PyDict_CheckExact(B)，先完成依赖key读取所需exact-str键前验，再PyDict_GetItemWithError(B,key)一次 | 命中取新引用返回；NULL有错透传；NULL无错进入M6。不能以PyDict_GetItem吞掉原异常 |
| M5 | 否则保原PyObject_GetItem(B,key)一次 | 实际值的新引用直接返回；非KeyError原错误透传；KeyError及其子类沿原分支进入M6，不先另造KeyError或提前Clear |
| M6 | 按下一节完整对应原format_exc_check_arg(NameError,NAME_ERROR_MSG,key) | 不是仅复制文案；保实际格式失败、.name处理和首异常。全miss与B原KeyError来自不同前驱但使用同一原helper分支 |

M1/M2不能返回subject当前metadata作为expected，也不把普通名称解析成功等同于下一callee准入。B非exact mapping可能运行其原协议，这是原LOAD_NAME已有的generic分支；返回后仍需该原边自己的fresh认证。B回调可能改变G/阶段，本文不声称整个lookup/后续body无回调或永久有效。

新增dict键前验是安全门，扫描失败的固定准入错误须明列，不能叫原lookup错误。取得B后的强引用、frame根和L/state各自独立；不得跨回调留borrowed值，任意清理在短锁外。原错误已发生时不再运行新成功门来覆盖它。这里没有完成全N4的m_traverse/m_clear/m_free或外部持L的退休实现。

## 4. 最终NameError的具体C API等价路径

原ceval.c 2733–2758的format_exc_check_arg是本节的操作模板，不调用不可导出的static函数，也不更换evaluator。v3.12.10的ceval_macros.h原literal为`name '%.200s' is not defined`，不能误写成无截断`%s`，也不能改成任意repr格式。

拟议算法，尚无可编译产品实现：

1. 对actual原key调用PyUnicode_AsUTF8；失败原样返回。与原helper相同，此时可能仍有M5的KeyError；不得先额外清除它。
2. 用捕获的原NameError class与上述准确literal调用公开PyErr_Format。errors.c证明它和原_PyErr_Format经同一_PyErr_FormatV；后者按原顺序先清当前raised error、创建格式字符串、设置并归一化异常。成功与分配失败分列，不能声称这是无分配/无回调段。
3. 用公开PyErr_GetRaisedException取得并清当前raised exception（owned）。如PyErr_GivenExceptionMatches(actual_exc,原NameError)为真且`((PyNameErrorObject*)actual_exc)->name == NULL`，对原固定name属性执行原PyObject_SetAttr(actual_exc, name_key, actual_key)，与原helper一样忽略该设置的失败。
4. 用PyErr_SetRaisedException(actual_exc)将同一实际异常移交回raised状态；不得重建NameError、重设traceback、追加诊断或把属性设置失败冒作最终first。若格式阶段产物为MemoryError等，它不匹配NameError，仍保同一实际异常。

新冻结的公开full-API头Include/cpython/pyerrors.h给出PyNameErrorObject及name字段声明；通过Python.h的正常包含路径使用，不直接include该受保护头，不声称limited/abi3。原PyErr_Get/SetRaisedException的实际取走/移交和_PyErr_FormatV路径均来自已冻结errors.c。原NameError及必要type根须在可信初始化时取得并强持；name属性key用native固定literal创建的自有exact str强持，不声称它与解释器内部_Py_ID(name)是同一对象，也不从当前G认领。具体模块初始化接线仍是完整设计义务。

这里复制的是一个已明确的版本分支和原实际异常对象运输，不是生成原历史异常对象。3.11等其它版本尚未给等价实现；pyproject的>=3.11支持不因此收窄。没有调用上述API验证运行时，也没有读取真实frames/locals。

## 5. 同一mapping槽的消费冲突及接口决定

完整L接口不能由“两个mapping slots已存在”推出。已有官方路径具体要求如下：

| 原消费/接口 | 已确认规则或本批决定 | 仍待完整设计的内容 |
| --- | --- | --- |
| reserved get | 不取G value，原C/A新引用或非KeyError拒绝 | actual allocator/type/state、成功终段metadata与退休实现 |
| 普通module LOAD_NAME | 条件采用M1–M6，不增中间KeyError | 实际未来源码/单次get_code根及真实LOAD_NAME校验，M1前安全门/所有权接线 |
| 普通STORE_NAME | mp_ass_subscript(value非NULL)只转原PyDict_SetItem(G,key,value)一次；不把返回0当effect永久一致 | 原STORE尝试/返回/raw effect与watcher再入、原phase及错误运输 |
| 普通DELETE_NAME | mp_ass_subscript(value=NULL)只转原PyDict_DelItem(G,key)一次；原(-1)与错误交回evaluator | bytecodes原DELETE_NAME对任何删除错误调用原NameError转换，不仅是KeyError；slot不得抢先包装 |
| reserved set/delete | 拟独立非KeyError拒绝，绝不写C/A私有根 | 这是新增只读overlay语义，不是原G普通键；错误/活动调用/退休表待完整接线 |
| SETUP_ANNOTATIONS | MF2通用套用存在明确条件冲突，不能标兼容 | G缺__annotations__、B有同名键时MF2会跳过原PyDict_New及写G；G/B都缺时MF2直接NameError，原应创建。须采用操作专用设计或在真实新K上证明该消费不在修改范围，不能现在猜已核 |
| keys/iteration/len | 尚不选“G键+两个reserved”或“完整namespace可解析键”的外部契约 | 两者对B fallback名称和overlay可见性不同；len/contains/get/items/copy及退休行为必须同版定义，不能先实现不一致接口 |
| PyMapping_Keys/Items/Values | 非exactdict会调用实际keys/items/values方法，再要求list或可迭代 | 固定native方法来源、返回类型/分配、错误/再入及是否保dict-view语义尚待明确；PyMapping_Check仅需mp_subscript并不证明这些已具备 |
| hidden locals merge | PyDict_Merge通用路实际keys→iter→每key GetItem→写目标 | G snapshot后键被删、B同名时MF2可能回B值；这是具名条件差异，不是atomic G拷贝。keys协议和partial first/引用仍待 |
| 外部直接L[key]、locals()/vars()/dir | 不声称是原G exactdict兼容，不用当前frame B当原module证明 | 必须显式选择新L的完整外部合同及与合法原观测的关系，不以L是秘密或拒绝一切外部消费消除问题 |
| STORE_GLOBAL/DELETE_GLOBAL、函数LOAD_GLOBAL | 仍直接走G，不受L保护 | 原函数局部实参/独立native consumer及每条实际边另行接线 |

三份当前原源的non-declaration statement子树未见AnnAssign、ListComp/SetComp/DictComp或locals/vars/dir/exec/eval/__annotations__这些Name；各有一个GeneratorExp。这个有限库存包含子节点，不冒认完整module求值作用域，也没有跳过def默认值/注解等事实就宣称覆盖整个执行树。它不证明未来新K无SETUP_ANNOTATIONS、无hidden locals，或外部永不消费L；当前两个保留名位置/新LOAD_NAME仍未产生。

因此本批没有把MF2升格为完整mapping设计批准。它仅把普通LOAD_NAME的候选解析与最终异常算法具体化，并排除MF1的透明性声称。完整L接口、operation-specific接线或具名合同修订必须先双审，不能到实现/测试时临时决定。

## 6. 后继实际开发和保持事项

下一完整设计必须同时固定：L外部接口和annotations/hidden消费；actual native type/allocator/state、活动调用/退休/外部强持及N4；每次effectless fresh J到原sourcecaller local和原STORE/effect的I002；P0两次环境、tools/rows/owner/context、catalog第四目标及必要私有ABI；23 P2/CORE第24项、联合metadata和三D0/D1/D2、N2/N4/COMPAT。没有以原G alias、缓存异常、first-call token或foreign J2给J1补expected的捷径。

当前完整候选仍r1106 SOURCE02；完整设计两审前不生成产品候选，源码准入前不compile/import/运行候选或局部模型/native build/QA。只下载两份版本固定官方头/宏纯文本，不执行下载内容；其它原文继承既有冻结版本。

原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不变。正常库、service/dist、ACL/settings、真实平台和Git不操作。不profiling/读取真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取。canonical apply0/历史11，源码缺口关闭0、产品pass0，完整Goal仍有工程，非只剩验收。

# 下一批：L的完整消费合同与原消费者绑定

父r1120，当前产品源码仍r1106 SOURCE02完整159；MF2只有原module LOAD_NAME的具体条件算法，未形成完整L或可实施来源方案。

1. 在同一份设计中选择L究竟是namespace resolver还是保持G普通mapping的接口，并逐项固定subscription/set/delete、keys/iter/len/get/items/contains/copy、外部仍持L及退休。不能把MF2的G/G/actual-B无条件用于annotations或hidden merge，也不能把当前三源AST缺席当未来K已核。给出operation-specific接线或完整明确的新执行合同；若无法等强保持某具名原操作，保为阻塞。
2. 保留M1/M2两次G和M3 actual当前B及M6原NameError算法，不退回MF1透明性声称，不缓存异常/tb规避分配。native actual allocator/type/state、初始强持、活动消费和退休/清理逐引用接线，公开name key与内部_Py_ID身份分开，N2字节/时间与>=3.11兼容不得偷减。
3. 联合effectless每次fresh J到原sourcecaller实际local/原return/STORE/effect，foreign J2不得占J1原槽/expected；原C入口可公开不等于任意caller来源，完整consumer不能只写fresh。P0两次环境/tools/rows/owner/context/catalog第四目标/私有ABI、23P2/CORE第24项、联合metadata/三D0/D1/D2/N2/N4/COMPAT要同版闭合。
4. 完整设计双审前不生成产品候选，源码准入前不compile/import/执行候选、native build、局部bootstrap模型、旧绿F001、QA或新诊断。原保护/限额/完整Goal口径保持，不重放已耗授权，不再索取P06材料。
