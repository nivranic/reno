# r1122：原factory副作用边界与CORE实际返回的局部运输

r1122明确原23个P2及CORE factory body均在return前产生业务effect，不能藏入effectless fresh producer。仅CORE拟将原两个真正local返回为exact pair，并以三个新caller local跨CORE001/CORE002持根；原15403 finally、15381 getter及15515正常路径del保持。38处原global context观察仍须P0联合消费，来源运输不代metadata可信或N4。B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

24完整输入pairs每侧4114779B，15确切原文区间、2份既有官方3.12.10文本156545B；无新下载。父r1121完整71文件14243914B、159源码7956657B及保护56同18lstat缺席核同。

24次actual call分23P2与CORE第24项；23P2返回22函数和1类。CORE实际row源为9599的_hx_defined_core_row，9607原fact STORE、9609原解锁、9610原return；拟返回actual函数和row，不重读共享fact造expected。新增pair分配失败须保已effect的partial/first，不重跑body/tool。

三个拟议新local持function/row/child tuple，原_factory_result仍在15403清；child清理只覆盖新增引用，不在错误路径新执行原15515 del。新增引用存活及清理成本显式保留，原17lease/1449slot不冒充已含新增3slot。control23/scope8/registration7共38处原global context Load有精确行列目录；传入actual context不等于原消费已接held来源。

本批双审材料0只适用于该有限子设计；完整P0/J真实参数、23P2/CORE实际联合门、metadata及三D0D1D2、native state/type/A/J N4、>=3.11兼容和打包仍待。control1MiB仍原累计实读上限，不是确认的native总heap限额。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍未完成，非只剩验收。

# r1122：原工厂副作用边界与 CORE actual-return 的局部运输

状态：有限静态运输子设计，待本版双审；不是完整来源方案或产品实施批准。父r1121接口合同保持；产品源码仍r1106 SOURCE02完整159文件7956657B。本批不生成产品候选、不compile/import/运行、不native build/模型/QA/诊断；B001/B002仍open2，full_design_approved/source_approved/execution_admitted/complete_Goal均False。

## 1. 已确认的实际路径与本次决定

三份原`_AUTH_BOOTSTRAP_CONSTRUCT`共24次`_factory_actual(_held_original_context)`，10/8/6分列。实际返回全部先进入真正局部`_factory_result`，然后原global STORE；这些局部不因constructor有538/399/252项global声明而变成global。23个P2是control9/scope8/registration6，返回22个业务函数与1个类；第24项是control的`_AUTH_HELPER_DECLARE_CORE`，不能混进23个P2。

本次选择一个确切修改方向：**仅CORE的私有返回协议改为exact pair，来自原producer的两个真实local直接返回；sourcecaller用三个新真实local跨原CORE001/CORE002持有actual值。** 其余23个P2的单参数/单业务值返回不改；CORE也保单位置参数，变的是其返回ABI。新源码仍未生成，不能称已修复或新字节码已验证。

## 2. effectless producer 的界线：不能代执行原 factory body

| 层次 | 实际行为 | 本方案的处理 |
| --- | --- | --- |
| native创建私有factory/J/facts | 从原code根及独立环境构造自己的新对象；不执行该factory body、不写共享业务slots/records/G | 继续保留effectless fresh的方向；J2不覆盖J1、不写last-result/首用/唯一token槽。实际P0及native构造仍待完整设计 |
| 原23个P2 body | 定义原业务值→原tool调用→锁内slot阶段/record STORE/effect→return业务值 | **effectful**，保持原sourcecaller位置的实际调用，不能藏进上行producer以“每次fresh”冒认不会抢占原slot |
| CORE私有factory body | 创建原`_make_core`，原tool返回row，写原core fact阶段1/actual row，然后返回 | 同样不是无副作用；本次只改变最后的私有返回运输，不提前重跑body/tool |
| 原`_make_core()`业务调用 | 创建custody tables及四个函数，返回原四项tuple | 独立effectful业务消费，不是P2 factory创建或可随意重放的pure builder |

原23个P2中，control的FAILURE_CLASS body含`GuardControlError.__init__`和`class:GuardControlError`两条catalog记录，其余control8、scope8、registration6各对应一条；这是固定源码的记录种类，不是本轮执行次数或动态覆盖。各body的原定义/tool/slot/record/effect先于constructor的`_factory_result`和global发布，不能颠倒。

原constructor开头的私有alias删除仍在control11974–11984、scope10268–10276、registration4317–4323。每个原调用前还存在alias重新发布、单次lookup到`_factory_actual`、原审计检查和实际call。未来23模板若不创建这些alias，旧del和再发布必须同版设计；本批不删它们，不从“已知原factory”推出这些effect可省。

## 3. CORE三跳：真实local、global运输和断开的清理点

| 跳点 | 当前原来源与消费 | 本次保留和修订 |
| --- | --- | --- |
| factory actual return | control15368进`_factory_result`，15369写global`_hc_actual_factory_return`；15382/15387/15393/15396及15445/15447后读 | 只复用`_factory_result`不够：它在15403原finally已None。必须新增跨块强持，不能延迟原finally或从global补pin |
| published row | 15381原`_hx_core_fact[2]`读取后写global`_hc_published_core_row`；15449再写global`_hx_original_core_row` | 这是共享fact观察，不能直接当独立原producer。15381读取/STORE保持，另以原producer实际返回的held row作对照 |
| business child tuple | 15508原`_hx_incoming_target()`返回，写global`_hx_actual_core_return`；15509形状、15511遍历、15514四global unpack、15515原del | 用新local先保actual RHS；原global STORE/读取/形状/遍历/unpack保原顺序；只有正常到达原位置才原del |

`_hc_actual_factory_return`、`_hc_published_core_row`、`_hx_actual_core_return`、`_hx_original_core_row`都在constructor的global集合，不能因名称像temporary就称为安全local。前两项在该constructor内未找到原del；第三项有15515原del。对这些结论只做AST及原文检查，没有读取真实frames/locals。

## 4. row 从原 producer 返回：仅CORE改exact pair

实际源头为`_AUTH_HELPER_DECLARE_CORE`（control8511–9610），该函数没有global声明。`_make_core`于8529定义，`_hx_defined_core_row`于9599接收原`_hx_tool(_make_core)`的actual return；两者均为该函数的词法local。9607把同一actual row写到core_fact[2]，9609原finally解锁，9610原为`return _make_core`。

拟在同一原return位置改为`return (_make_core, _hx_defined_core_row)`。不从15381共享fact取回row构造pair，不调用tool第二次，不在原锁里准备pair，不从function当前字段重新生成row。此前读到的“_hc_original_core_row是9599绑定名”已以实际原文纠正，真正源名是`_hx_defined_core_row`。

这改变CORE私有返回ABI：原直接业务函数→exact tuple(function, actual tool row)。其单参数和原body中的CREATE、tool、fact写入、解锁顺序保持。额外tuple分配在fact已经阶段1、已经持row且原锁已退之后发生；分配失败会新增“body已经effect，caller尚未收到返回”的运输错误。保该次实际first，caller不继续原global发布；原fact保持实际partial，不回滚、不重试body/tool、不缓存成功假装原RETURN透明。

pair中的row是**该次原tool actual return的运输来源**，不是其metadata天然可信。tool自身来源/联合metadata/原创建code及closure环境、原row内容一致性仍须由完整方案独立证明；不能拿“来自local”替代N1/N2或后续fresh消费。

## 5. caller的三个新local与原操作保持

拟在control constructor加入三个未占用且不在global集合的新名称：`_held_core_factory_return`、`_held_core_source_row`、`_held_core_child_tuple`。当前AST中三名均无Name节点；未来实际新K还必须核它们确为fast locals、没有意外cell/freevar或global绑定。原`_factory_result`仍保原finally的清理时点。

1. 在进入CORE001前把三个新local初始化None，由一层新增外层try/finally包住原CORE001、其15403原finally、两段之间原语句及CORE002完整范围。不得移动任何原body/handler/finally内的业务语句。外层成功/错误出口最终都按child→row→function的实际取得逆序清本方新强引用，先清槽再释放，不在原metadata短锁里进行任意DECREF。它们是新增3个运输slot，不偷偷并入原17lease/1449slot。
2. 原15368实际调用保持一次、一个原context参数，结果仍先进`_factory_result`。仅CORE这一路对本次packet做原根提供的exact tuple/长度2安全前验后才解包，禁止对未知对象先运行iter/getitem。解包到新function/row locals后，原15369仍只STORE同一actual业务函数到`_hc_actual_factory_return`，不能把pair写到该原槽。前验所需可信primitive/native联合门属于完整来源设计，不从current G再找一个checker来实现。
3. 原15381 `_hx_core_fact[2]` getter照常只读一次，结果仍进原global；原15382等已有检查保顺序。新的联合消费要对实际观察值与`_held_core_source_row`、原实际function与`_held_core_factory_return`及本次J1/phase共同核对，不能从共享slot或global反造expected。原getter/旧错误先失败的路径不追加成功门覆盖first；新完整门的确切源码接线仍未批准。
4. 原15403 `_factory_result=None`照常发生：此时清的是新增pair的本方引用，两个新local继续持住其实际成员，覆盖15445/15447及CORE002。不得把“两个global仍像原值”当跨块来源，也不得悄悄把15403推迟到15515。
5. 原15508的零参业务调用保持原目标、原参数、原位置及次数。actual return先入`_held_core_child_tuple`，再原STORE到`_hx_actual_core_return`。用覆盖实际call→原STORE→原形状/遍历→原unpack的独立内层try/finally归还child local；成功清完后才执行原15515 `del _hx_actual_core_return, _hx_core_child`。若call/STORE/形状/遍历/unpack失败，finally只清新增local，**不新执行原本未到达的global del**。外层再清None是幂等槽清理，不重复归还实际引用。
6. 原四个global分别接收claim/state/facts/history函数的unpack不改为新顺序、合并API或C预发布。对该次四函数来源和closure/defaults的原期望另接原code树与真实原RHS，不能拿返回后的current function属性自证。

新增held引用会延长部分实际值的存活，不能声称回调/析构时机完全透明。原slot、global或四函数发布值可能被合法再入改变，不能一概推断额外DECREF不是最后一次。原异常对象/裸raise/partial与新增清理错误须在完整N4表里保持、逐出口审查；本设计没有通过运行证明清理或first已闭。不能使用新finally掩盖旧异常，也不将整个认证图的释放协议重写成这三个local。

## 6. P0到caller的下一真实消费，不能只修返回末端

现有原尾第一参数是context NamedExpr的actual RHS，第二参数是24条factory facts tuple；它们确实进入两个真实参数。然而constructor仍从G做隐式global `_AUTH_CATALOG_CONTEXT`读取：control23、scope8、registration7，共38个直接constructor作用域Load节点。原首次分别control12025、scope10282（同一表达式两次Load）、registration4328；scope/registration直接用读取结果继续下标取原callable。

没有显式global声明不等于local：这些context名称不是参数，也没有该函数local Store。static inventory要记录每个Load的scope/行/列，不把只统计Name当未来LOAD_GLOBAL指令已核。不能仅把真实context传入就宣告消费已用held来源，更不能直接用这38处current读出的context作为expected。

完整P0接线须把本代actual context、tools/rows/owner/primitives、两次原环境及catalog第四目标与本次J1一起进入原caller的真实参数。每次原global context观察和其后dependent消费，要明确原实际读/原错误与新联合门的位置；不得随意删原读取、合并两次环境、在未知下标/target运行后才拒绝，也不能将原module L保护扩到函数LOAD_GLOBAL。native与caller新增私有参数ABI、原D0/D1/D2/metadata根仍须同版完整设计，本批没有创造一个可执行checker占位来绕过它们。

## 7. 文件级后继与准入

| 顺序 | 拟涉及的文件/责任 | 必须提供的下一证据 |
| --- | --- | --- |
| 1 | 当前159候选中的`reno/personal_authority_guard_control.py`：CORE producer最后return及constructor两个原CORE段 | 仅CORE返回ABI、3个真实local、原getter/STORE/del/旧finally的完整可逆差异；实际新K及错误/引用表，当前尚未生成 |
| 2 | 同候选scope/registration及control的P0消费；既定native bootstrap/producer来源方案 | actual context/J到真实参数，38处原context观察的joint消费；23P2/CORE及catalog第四目标；不是重做current pin |
| 3 | native模块/type/A/J共同生命周期与平台打包 | 本次J1无共享首用消耗、跨块根所有权/退役/失败；3.11及以后支持不收窄；独立内存成本与environment N2分列 |
| 4 | 新完整设计、候选及源码两审后才安排相应最小运行验证 | pair分配失败后partial、CORE001原finally、CORE002各错误出口/原del可达性、J2不覆盖J1等；本轮仅静态，不授权现在执行模型/pytest/native或诊断 |

本批静态验证只解析当前源AST、核确切原文与文件指纹；官方3.12.10 STORE_FAST/SETLOCAL及STORE_GLOBAL/DELETE_GLOBAL文本仅解释局部槽和global effect差别，不是新源码字节码或运行证明。只保留effectless创建层，不批准能执行原body的通用native消费者。

full_design/source/execution/Goal仍False、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002和case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；control1MiB仍原累计读取上限，不作native总heap限额。正常库/服务-dist/ACLsettings/平台/Git不操作，禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云和完整Goal仍未完成，非只剩验收。

# 下一批：P0实际入参与38处原观察的联合消费

父r1122，产品仍r1106 SOURCE02完整159；CORE运输仅子设计选择，未改源码。

1. 形成actual P0 context/tools/rows/owner/primitives、两次环境、catalog第四目标与本次J1到原constructor真实参数的完整私有ABI；原38处global观察和dependent消费逐点定门。保原lookup/下标/错误先后，不把L保护扩成函数LOAD_GLOBAL保护。
2. 把effectless native factory/J/facts创建与23P2/CORE原effectful body分开；J2不占J1槽，原alias del/再发布/单lookup与实际call保持。接CORE原actual row pair和3新local，同版完整联合metadata/三D0D1D2、实际child tuple/四函数来源及错误/引用表。
3. 完成native state/type/A/J与L的共同N4、原first/partial和新增分配/清理错误、>=3.11平台打包/加载回滚；分别核原environment N2、新引用存活及独立内存成本。
4. 完整设计双审前不生成产品候选；源码准入前不compile/import/执行候选、native build、局部模型、旧绿F001、QA或诊断。保持17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10。
