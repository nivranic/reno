# H01 固定 runtime 核验中的目录观察



状态：实现与验证中。本文不表示 Store、H01 或完整切主已经完成。



## 问题与真实约束



r651 原单例已完成初始化与 Store 预览，但仍在 journal-v1/node.json 改名前的 runtime 核验中触发原 30 秒时限。此前唯一 profiling 对初始化测得大量重复 stat；当前 Store 的精确耗时分布尚未测量，报错位置不能当作热点证明。



主方案 §17.113 已明确撤回“每次重复 stat 的采样时点不可改变”的额外要求。可以在同一次完整核验内合理调整物理采样；必须保持全部实际资源、known-invalid、guard、owner、clock、取消、private、锁、文件内容与 SHA、effect 前后以及原第一异常/关闭要求。普通业务调用、写操作及下一次 effect 不能继承某次观察的成功结果。



因此，本轮不将外部主体在首尾之间改变又恢复祖先这一采样差异，单独当作禁止重构的理由；也不声称能够检测所有瞬时替换。动态方法调用可进入任意业务代码则是实际边界，必须隔离。



## 备选与决定



| 方案 | 结果 |

|---|---|

| 继续仅去除一个 guard 内的父链重复 | r649 有窄测试收益，但 r651 原完整单例仍失败，不能重复同一候选等待变绿。 |

| 用全局 ContextVar 或通用 contextmanager 自动减弱所有 require_guard | 不采用。自定义方法可在其中重入普通写入者，扩大观察范围。 |

| 固定 Namespace verifier 内建立预期结构计划，仅内部明确核验点使用部分校验 | 采用此实现方向。普通 require_guard 保持完整路径，所有返回均等完整尾核成功；实际收益仍须原单例证明。 |



不改变技术栈、30 秒时限、原 fixture 或 Store 工作量，不增加 profiling。



## 代码职责



正式 `reno/storage_lifecycle.py` 提供四个私有低层函数：



- `_capture_guard_file_plan_original(guard)`：捕获原 guard/profile/lock/stream/directory 的预期对象与字段。未知自定义分派返回 None。计划不包含历史 stat 成功值，不是授权 DTO，不创建可供业务使用的 permit。

- `_verify_guard_plan_state_original(plan, profile, exclusive=False)`：只读取并核对当前active/current/owner、原dispatch/完整shape、member/EX；不读取旧成功结果，不返回guard或授权。
- `_verify_guard_plan_files_original(plan, profile, exclusive=False)`：逐次核对实际 active/current/owner、原对象绑定、member/EX、每把锁的 entry/fstat/marker，以及 guard 全部 profiles 的数据库类型条件。仅验证文件部分，返回 None，不能代替完整 guard。

- `_verify_guard_plan_directories_original(plan)`：实际重新读取完整目录图，核 expected dev/inode/type/reparse。没有观察结果跨调用缓存。



普通 `require_guard()`、`StorageGuard.verify()`、writer 与 API 均不接这个部分校验入口，也不根据任何环境 Context 自动跳过目录检查。



独立候选 `data/t16-runtime-origin-graph-r653/` 的 Namespace 拥有完整观察生命周期：



1. 只能由原 `_verify_closed_namespace_owner_runtime_native(owner, writing=...)` 的固定调用链建立；无 callback 参数、无新 yield/contextmanager 对外接口。

2. 原 begin 取得 genuine owner/custody/完整原目录与资源图，在现有私有 binding 中保存 plan；进入 reading 前做目录→文件→目录。

3. `_runtime_directory_check_original` 仅原 `_require_profile(custody.profile)` 的位置使用实时状态核验，其他 profile 调用保持 full。原每次 registry/owner/profile/scope/资源账本、时钟、取消以及 Context token reset/set 全部保持。

4. 此处 `exclusive=False` 保原 profile SH 语义；namespace 的 writing/EX 不错误升级为 profile EX。

5. 正文的实际文件读、SHA、private、名称、所有 marker/FD、观察字节计数和原异常记录继续执行。

6. finish 中目录、文件、目录各自 try/capture；即使 body、取消、deadline 或 context 已失败仍尝试这些尾门，然后继续原逐 record/native、PF identity、graph、token 退出。原首异常及 traceback 保持，尾核失败不能交付正文结果。

7. plan 不返回、不交业务调用方、不跨 effect。外层 Stage 的 CREATE_NEW、写、flush、rename 和下一次 effect 均在本观察结束之后执行。



## 自定义方法与兼容



真实反例已证明：原 guard 批量核验没有排除自定义 entry/helper/fileno 时，可能改变该回调之间的目录检查边界。r652 已补原方法、receiver、目录和流类型的准入；自定义行为沿原完整逐锁路径，186 项相关测试通过、2 项跳过。



进一步静审发现自定义 Directory 的 entry_stat 属性 getter 可能被提前访问，导致错误转换改变；已有真实红例，必须在 getter 之前先检查 exact Directory 类型，回退原路径。此类兼容问题与 Store 性能收益分开记录。



新计划的快路径只消费固定原对象/方法。准入时未知对象回退完整原路径；进入范围后对象或方法改变须拒绝并完成尾核，不能重新 capture、采用新对象或重跑正文。底层 OS primitive 的测试故障注入用于验证错误检测，不扩成任意同进程代码的安全沙盒承诺。



## 验证与失败处理



本设计的通过条件包含：



- 实际 guard 叶子测试：每次 marker/entry/fstat 与全部 profiles DB 保留，member/EX/current/线程/失效拒绝，shape 改变拒绝，自定义方法正确回退。

- 普通 require_guard 在同一时段仍独立完整扫描；创建 plan 本身不改变任何普通调用。

- 原目录、stream、FD、marker、数据库在两次观察之间变化仍拒绝；第二次 effect 必须读取新状态。

- Namespace 使用真实初始化/closed owner 验证原 Context、取消、期限、异常尾核与首错误，不构造假 owner 或 DTO 许可。

- 最终运行原完整安装/resume/SH-open/prepare/put 单例，全部 fixture、断言、读取预算与 30 秒门不改。窄叶子绿色不代表它已通过。



任一失败保留原日志/QA/暂存；不重试未变候选、不修改真实库/配置/服务、不清理旧拒绝目标。工程完成仍需 H01 剩余 enrollment/live witness/全部 writer 及 H02–H06、完整分享、P06、条件 T15 与生产云等原范围。



## r655 后继范围

r654 实测仅到 journal 父节点返回及 segments 子节点，整例仍原时限失败。后继 r655 只将 runtime 内部计划分支从 files 改为 state；同一次固定核验内的生命周期锁文件/DB物理观察保留在原 begin/finish 的完整 dir→files→dir，以及正文其他原完整 guard 调用。Namespace 自身实际文件 SHA/private/authority lock 不减少。必须准确称为完整 guard 物理观察合并，不能继续仅称目录去重。结果在末次完整尾核之前不交付，普通 require_guard 与 writer 不使用 state leaf。

BufferedReader.fileno 会委派 raw.fileno；因此快路径还要求原 FileIO/raw.fileno 并绑定原 raw 对象。未知自定义实现只在准入前回退原完整路径，进入观察后变化拒绝。测试不执行dup2/reinit或别名诊断。

本文件的r654原字节已保存于 `data/h01-guard-test-before-r655/runtime-observation-design.md`，SHA与原r654终态的design事实相等。当前扩展须经过新叶子/真实owner测试及原完整单例，不将原r654绿色边界测试或失败位置前进当作本扩展已经通过。


## r657 收尾目录重复读取的限定合并

r656 原整例已到最后 successors 子节点，但仍在原30秒期限失败。退出处抛错只能说明期限已到；本次依据源码中明确重复的目录调用链改动，不把它描述为新增性能测量。

本次保留 finish 的完整 guard 三段、逐 record 实际目录/原生句柄扫描及逐目录原 pf.directory_identity 调用。原 PF 初始化上下文接缝继续执行；只在成功比较本次实际返回的身份后，才将这些结果纳入本次 closing 的私有观察。opening 的预期身份不能充当新的实际采样。

合并范围限于随后同一次 finish 的末次 stack/held 身份核验中的重复目录读取。普通 runtime-current 在 closing 仍返回 None；普通 guard、writer 和下一 effect 沿原完整路径。私有观察须绑定原 scope、真正 owner、context token、registry、完整资源图，并逐次检查结构。原 held 文件 path/fstat、private、lock、poll/clock/cancel 和首错记录均保持。观察表不完整或尾核失败时，仍调用原完整后尾路径，不将失败记录当已验证；该原路径遇到已经记录的首错时会直接拒绝，不承诺首错之后每个 held 资源都完成物理读取。

原 body 错误以及更早尾错仍优先，后续校验和 token 释放继续尝试；任何尾错不能交付正文结果。新候选保存为独立普通包，r655及所有旧失败保持。需要真实owner边界测试和原完整单例的新一次未profile执行，才能说明工程收益；本节为实现约束，尚不表示通过。


## r659 固定正文三处 guard 重复观察

r658真实原整例已通过五个子目录创建，header暂存write/flush后、fsync前仍触原30秒。下一切片只针对固定runtime body两次stack.check和一次profile检查中的重复full guard；writing尾helper与closing保持原路径。不能用runtime检查函数整体替代stack检查：真实held identity/poll、首错、文件/锁及完整目录图都保留。

固定body持有本次真正reading scope并仅在这三点显式传递。原stack首错先行，接着验证registry/owner/reading/context/同stack/profile/guard及资源图；已有guard plan时使用原state叶子，无plan保原full。未知自定义stack方法沿原无参dispatch，scope失效或不匹配必须拒绝。普通stack.check、require_guard和writer不会因ambient scope而减弱，不能接受caller trusted布尔值或伪造观察。begin/finish的完整物理门、其后才交付结果及原30秒全部保持。

这是基于明确重复调用的后继实现约束，尚未验证；不从r658失败位置或总时间宣称精确热点。不新增profiling，必须保存r657/旧失败，新增普通后继、真实owner边界用例与原完整单例才能给出结果。


### r661 修正与本轮运行边界

r659静态审查发现dispatcher先读取check描述符、后重抛既存首错。原稿未执行并保持；r661仅在显式scope分支前置custody.first及原traceback，再读取描述符，None原路径不变。其余固定body三点设计同上，独立静审无剩余阻止finding。

r661的16项真实owner用例唯一执行得到6passed／1setup-error，203.92秒；第7项在原initialize发布header的StageCustody进入核验时触原30秒，未进入该项body，后9项未运行。五关键源码SHA保持。原4项与两个closing物理损坏负例通过，不称全部新reading边界或H01通过。r662完整Store仅prepare201源，started不存在，尚未执行。保原日志/QA/暂存，不重试未变候选或追加profiling；先静态核本初始化链与旧源码是否相同，再确定新的修复。


## r663 初始化自身的 guard 观察计划

上一选集在初始化fixture内触原期限，未到该例runtime body。本轮只处理初始化固定观察域scopechecker每次Budget检查导致的整组guard重复，不修改普通Budget、Stage、snapshot或writer。初始化自己的binding保存expected guard plan；原permit、真实EX=True、原budget/started/callbackNone、ledger、native marker与Context始终核实，不能借runtime SH观察。

首尾各增加完整guard directories→files→directories，尾部三段各自捕获错误并继续原文件/目录/ledger/期限/token退出；保原first/traceback。仅支持原分派及原native marker分支的显式scopechecker改用私有budget/state叶子，保原两个deadline检查和实际native注册/owner/permit。无plan、native=None或自定义分派沿原full Budget.check，不将原来合法的None分支额外拒绝。marker不匹配/过期、预算或owner改变仍拒绝，观察不跨effect/实际获取/普通writer。

需要真实initialize/EX/permit/ledger边界用例及原runtime选集完整结果，再决定后继原Store整例；当前只是实现约束，不表示通过。原r661失败、r662仅prepared不执行以及其他暂停项保持，不新增profiling或修改30秒条件。


### r663 实际验证与完整 Store 剩余期限

r663固定源已独审封存，初始化测试首轮误要求marker[0]只能是permit或stack，原合法NamespaceOwner关闭路径被测试额外断言拒绝。原产品没有此类型门；保留失败日志/测试原bytes后，仅删除该一行测试限制，真实registry、marker同一、budget/owner、EX/permit/ledger/context断言全部保持。

后继r665初始化4例实际4passed／21.48秒；随后新TEMP串行r663 runtime16例16passed／209.72秒，两组六源SHA前后保持。原full Budget与ordinary guard、实际marker尾部拒绝后即时恢复、copycontext、first/独立后尾、真实reading scope及custom分派等已在各用例范围验证。

r664原完整Store selector唯一执行仍1failed／43.74秒，201源保持、无profiling。原Store header发布和_acquire_v2已返回，安装末_verify_v2_body的journal扫描经Store Budget.check进入原Namespace Budget.check时触30秒NAMESPACE_DEADLINE；安装返回、显式resume重放和后续SH-open/prepare/put未达。该检测位置不当热点证明。后续只读比较固定runtime writing尾剩余full guard与Store固定验证同次观察边界，不能改普通Budget或跨effect缓存授权。所有旧失败/QA/暂存保持，未改真实库/服务/生产dist或commit/push。


## r666 固定 runtime 写尾的两个 guard 入口

r664已到Store第二次完整校验，原30秒仍阻止安装返回。下一切片仅固定runtime body的writing尾：原_require_runtime_namespace_write_original中的profile检查和stack检查显式消费该body既有真实reading scope；不是Store Budget观察域。

保留真实closed acquisition类型、owner registry、执行owner、namespace EX和原held.lock.verify；profile仍是原SH兼容，不能使用初始化EX检查。固定原write-helper指针才接私有scope参数，未知custom仍helper(held)，custom stack仍check()。默认无scope沿原full；显式scope必须核registry/owner/context/同stack/guard，既有first/tb先于新增属性/分派，错scope拒绝、无planfull。

文件/private/native/poll/clock、完整begin/finish与普通helper/普通Budget、初始化、所有写入effect保持。需要真实namespace EX/profile SH writing=True及对应拒绝/分派边界，再走原完整Store单例；没有额外profiling或放宽deadline。Store native-tail现只分派，不能凭名字或已退休Namespace观察给后续scan授权。

### r666 实际结果及 r668 固定 effect 末预算整合

r666 runtime20例实际全部通过，初始化选集本轮未重跑。r667原完整Store仍在header暂存核验时触原期限，未发布header；实际原Namespace verifier已返回，随后Store Budget.check再次完整检查Namespace stack时由held poll检测超时。旧日志/QA及输入均保留，不从这一位置推断精确热点，也不原样重跑。

r668只整合fixed `_native_namespace_verify`的一次完整effect前核验。现场新建原Namespace观察，原runtime body writing=True后保actual/acquisition/原budget绑定，在本次真实scope内显式执行Store预算对应的Namespace stack检查；原完整finish之后重新核预算登记/当前acquisition、原Namespace预算取消/期限以及原v2/native deadline，成功后才能返回actual。末clock和原取消poll必须位于finish之后，不能因整合提前末期限门；后置检查不读已退休scope。原Namespace Budget已有的取消异常转换须保留，不另造异常类型。原Store `_Budget.check`本身、所有收费、ReadStack创建/关闭、Stage与实际写入effect均保持。

只有已审定原函数路径可整合；被跳过的namespace wrapper/native facade/Store Budget.check或相关dispatch被定制时沿原三步完整路径，无plan保full，不以失败后重试作为fallback。原Budget直接调用closed-stack函数，不能无故新增custom stack.check调用。只在对应Budget适配器的catch中保原remember-first与裸raise；原Namespace body/finish错误不能被新宽catch额外登记成Store Budget.first，既有异常/traceback及cleanup仍保持。每次effect独立新scope，不借旧reading/closing/初始化或_NativeTail声明，不跨effect保留许可。

post预算段也须重新核仍存活的Namespace custody、预算/callback/clock和真实guard active/current/owner/profile成员；只检查本时点真实状态，不读取已退休观察或新建许可。该段Namespace预算/取消错误沿原closed-stack capture-first/tb，再经原Store budget first记录；原body/finish错误依旧不提前写Store first。未知guard/helper后置分派回原完整Budget一次，不能重跑已经执行的Namespace body。真实取消及收尾后丢失current guard各需要实际拒绝用例。

真实测试须使用原Namespace owner和实际Store Budget：正例两次新scope/完整尾核、普通Budget仍拒物理损坏、finish之后到期拒绝、Namespace与Budget异常记账边界、自定义原参数分派。独审后先运行该选集，再决定原完整Store后继。此处为实施约束，尚无r668运行结果；追加前设计快照已按r667终态SHA保至data/h01-store-effect-before-r668。

### r668 已执行结果

r668最终源码及11case独审通过。真实选集11passed／182.48秒，八源保持；新增post取消、当前guard丢失与原namespace/Store双first均实际拒绝并保原语义。普通Budget及Namespace原bytes保持，旧runtime/init选集没有重跑。

r669原完整selector唯一运行仍1failed／56.15秒，201源保持。records/node.json暂存writer返回后，read-custody首次identity经Store stage_stack/held poll触原30秒，未records发布或Store header/安装返回；原WinError2是正常新建分支。检测位置不是性能测量，不能据此宣称优化收益或回退原因。失败终态与原日志已保存，下一仅只读核准入及累计工作，未重跑、profile或扩时；详见主方案§17.154。

## r670自然Stage证据与r671单次Store目录批量核验

r670使用真实Namespace EX、Store预算、三份真实source pin及原legacy/声明生成canonical init reservation，经原Store/Stage发布工厂完成一次元数据发布。自然Stage各effect均实际完成不同的r668 inside/post范围，被准入identity比较的函数未替换；原文件bytes、creator退休、Stage/Store/Namespace正常退出、预算退休与fixture DB保全通过。1passed／17.44秒，203输入保持，无profiling。它补足自然Stage调用链证据，不代表原r669分支观测或完整安装/性能通过。

r671下一只处理_stage_stack_original末尾逐目录循环的共同路径重复读取，复用Namespace已存在的_closed_directory_verify_sweep_original。每次调用建立新的局部path观察字典；各实际record的registry/owner/原字段、所有native handles和每个expected identity仍逐项核验。Store真实record及directory tuple在首尾绑定，变化拒绝；原guard、held资源、预算/cancel/clock、来源文件SHA及全部effect/close不改。无新ContextVar、许可或跨调用观察。

只在原single/sweep/with_paths函数分派仍原样时使用批量函数；未知custom回原逐项调用，原参数/异常保留。批量正文中途分派变化拒绝，不重新执行正文。需真实原Store stack验证：共同父路径只在本次sweep合并、每一record/native仍核、新一次调用读新状态、实际坏路径/handle拒绝、custom回退及首异常保留；再复用自然Stage发布/正常退出集成，最后原完整消费者仍必须独立通过。此节是实施约束，不是r671运行结论。

### r671实际结果与完整流程剩余

Root自审封存r671，15支持模块和131原Store定义保持。四项真实资源边界加复用Stage集成实际5passed／97.96秒，204输入保持；路径/native错误使用受控返回值注入，未改原生句柄或做别名诊断。原r670测试只适配真实package名称，原bytes已备份。

r672原单例仍1failed／44.75秒，201输入保持，但首次install（含内部resume及全部发布/校验）已正常返回，后续原Namespace验证和init bytes读回也已返回。显式第二次resume在_acquire_v2访问init_pin.raw的held poll超原30秒；重复resume相等断言及SH读写未达。当前不能声称完整Store/H01或生产已通过，详情与终态见主方案§17.155。

## r673单次Stage存活核验的同scope重复观察

原_scope_live先调用_HELD_IDENTITY(scope.held_scope)，store分支随后经原_verify_store_stage_scope_original→Store._stage_stack_original再次完整验证该held scope。后继仅在这个固定_scope_live内合并重复的第一次观察；第二次原完整验证及其后全部Store/Stage字段、取消和时钟门保留，effect/poll各自的前后_scope_live均仍执行。

准入必须来自实际登记Stage scope及实际Store custody，held_scope/预算/stack/permit同一；相关Stage verifier、Store stage-stack及held identity直接分派仍为原函数。Namespace初始化模式、错关联与未知custom保原完整路径，不能凭mode字符串或caller开关跳核。成功后重核同一真实关系与原分派，过程中改变必须拒绝，不重跑正文；已有Stage/Store首错优先。原有首取消poll/held scope有效性、Store完整physical资源核验、预算/EX/current guard/末期限、正常关闭不变，无新观察注册或跨调用缓存。

测试要用原工厂与真实来源资源：原Store模式每个held资源在该_scope_live内实际完整验证一次；custom回退保两次原检查；不同真实scope关联拒绝；现存首错不被新增准入覆盖；实际路径故障仍拒绝。再复用自然Stage发布/完整退出，并保原完整消费者为最终门。底层叶子观察只做透明计数或声明故障注入，不包裹准入函数、不使用profiler/函数计时。此处是设计前件，尚无r673运行证明。

### r673实际验证

最终Stage b8cf7065…经Root自审封存，Store/Namespace/held等15模块与56其余原Stage定义不变。真实六项测试6passed／88.65秒，204输入保持；含正确合并/custom两次/真实错scope拒绝/注入错误stat及已记录first/late分派变化不重跑/原Stage发布正常退出。无新增独审，不继承旧版本独审结论。

r674原完整单例仍1failed／43.22秒，201源保持。首次安装正常返回，显式重复resume已完成init/header解析与journal身份核验；节点_fixed_read首Budget检查超原30秒，未本次节点读取、重复resume返回或后续SH读写。原失败与完整证据保存，详见主方案§17.156；未profile、扩时或动真实库/服务。

## r675固定Pin观察的重复身份核验合并

_ClosedStorePin._observation原先通过_pin_record_original执行真实pin登记/owner/closed检查、完整Store stack和specific file identity，然后_file_observation_original又对同一scope/lease做相同资源查找与完整identity。后继仅在这次固定observation中合并两个specific identity；保真实登记检查、完整Store stack、原file observation全部资源/路径/handle/预算/cancel/private及异常记账，结果构造仍由原函数完成。

普通_pin_record_original保持整段原实现，所有name/path/budget/setter/verify消费者不减门。三个直接分派pin-record/file-identity/file-observation任一未知时走原链；原Store stack检查中途改变后两分派时，保当前specific identity再当前observation，不重跑stack。每次属性读取独立重新核验，不缓存raw/identity、不新增scope或caller skip参数，源/业务pin的实际字节收费保持。

需要真实原工厂资源测试：source和业务pin的完整核验次数与下一读取新核、原直接pin-record仍full、观察不计费/真实verify照常计费；三个custom分派原参数/异常；中途custom变化保原链；原检查后的坏身份由真实file observer拒绝并记原first；最后自然Stage发布及原完整消费者继续独立验证。此处为设计约束，尚无r675运行结论。

### r675实际结果

prepare静态AST检查因__slots__为Assign失败，原builder保留；修复为比较全部类成员后完成原草稿，未产品执行。Root自审全文逆替及25实体后封存，Store3e4faa26…，15支持模块和133其余原定义不变。真实source/business计费、三个custom、两个late变化、实际最终身份拒绝/首错及Stage集成共9passed／138.57秒，204输入保持。

r676原完整用例1failed／48.27秒，201源保持：首次install的init-reservation暂存writer返回后，read-custody身份核验中的Store整栈held poll超原30秒，首次安装/显式resume/SH后续未达。本轮比前轮检测位置更早，不推断优化收益、回退或环境原因；无新profiling/原样重试。终态与边界见主方案§17.157。

## r677单次Stage目录核验

仅_stage_directory_verify_original的Store分支：原Store._directory_record_original先完整核整栈、真实目录registry/type/owner/native/路径及祖先登记，返回后Stage要求与resource.directory_record同一，再重复NS目录核验。后继在这一次固定函数内合并第二次核验，两个直接函数分派及Store.ns同一在前后均原样才省重复。未知custom或中途分派变化保原末NS调用，不重跑已经完成的Store查找。原记录相等拒门及异常顺序保留；Namespace分支逐bytes相同。

不改Store/Namespace任何函数，不跨调用保留采样，不新增许可或caller skip参数。真实目录/原Stage资源登记、整栈held/预算/guard、Stage所有effect/poll及native/file/private检查、原first/tb和cleanup保持。测试用原创建的Stage资源和真实目录句柄，证明本次目标完整核验一次/下次重新核、自定义原参数/两次核、late变化保末核无重放、错关联与真实native/path返回值故障拒绝和首异常；复用真实发布/完整退出后才运行原完整消费者。此处尚未执行r677。

### r677实际结果

Root自审后封存Stage09fc023f…／manifest8b448e95…，其余57定义及15模块保持。九个pre-open Stage资源/真实目录测试加实际元数据发布集成，共10passed／206.86秒，204输入保持；pre-open测试未制造文件句柄，真实文件发布由集成另证。

r678原完整用例仍1failed／43.86秒，201输入保持；首次install已返回，显式repeat resume的_acquire_v2和第一个完整_verify_v2_body已返回，在native第二body内pin.verify的pre-read observer首poll超原30秒，本次payload重读未开始，具体pin索引不推断。第二body/resume返回和SH后续未达，不当稳定提速或完整H01证明。详见主方案§17.158；无新profiling/原样重试，所有句柄terminal。

## r679普通Pin记录查询的同一资源物理核验

_pin_record_original原先完整Store stack已逐一核held scope活资源，然后specific file identity再次核目标。后继仅在实际登记Pin/Store stack/held scope/唯一_FileLease资源一致且相关直接函数原分派时合并第二次物理核验；不能仅以相同path/FD或caller参数认定覆盖。原Pin._observation及verify/计费保持原定义。

末specific检查同时承担时限/取消/原first检查，不能随物理去重删除。合并路径仍真实调用原_resource_original在尾部查找lease，并在原异常记账边界内调用_poll_record；再核同一实际资源/registry/绑定与原分派，变化拒绝而不重跑整栈。真实查找错误保持在原try外，poll/新增绑定错误由原_remember_failure记入同一资源和scope后原样raise。未知helper/旧first/不符合实际绑定时沿原完整链。无新观察许可/跨effect缓存，不变更读取、SHA、收费、private/native/预算或关闭。

真实测试须覆盖source/business本次逐资源一次与下一访问新核、既有observation仍完整、实际verify收费不变；custom整栈/scope/specific/resource查询保原链；真实尾poll过期/取消与已有first保持；中途分派或关联变化拒绝、无重放；缺失lease和closed pin仍拒绝。复用原Pin custom/late/final-observer故障及真实Stage发布。此处为源码实施约束，尚无r679执行证明。

### r679实际发现及r681后继

r679十八项选集首红停止：2passed／1failed／46.03秒，205输入保持。复用原custom observation用例实测目标身份核验变2次，原断言要求3次；准入漏检查_file_observation_original仍为原函数，导致原custom observation完整回退链被普通pin-record内部合并。这是产品候选的兼容性缺口，未改测试断言。原候选/测试/日志/QA及failed-terminal4cfa1ff7…保留，r680完整runner未prepare或run。

r681仅在同一准入helper增file-observation原分派条件；未知观察函数使普通pin-record回原full。其余135原Store定义及15支持模块保持；新测试只适配包名/Store SHA，所有原断言逆替全文同。必须从新的单次选集验证修复，再决定完整原消费者；此处仍仅静态后继准备。

r681实际修复原custom observation失败：前13项passed，随后closed-pin测试得到原产品真实拒绝，但新测试少写PERSONAL_STORE_前缀而failed；整轮13passed／1failed／209.91秒，205输入保持，后四项未执行。原v2.StoreError.__init__始终为self.code="PERSONAL_STORE_"+code；这次为测试预期错误，不是产品没有拒绝。failed-terminal0b6ba753…和原QA/log/test保持；r682完整runner未prepare/run。

r683保持r681全部产品源，仅在新测试副本将两条NATIVE_ORIGINAL_PIN_REQUIRED断言纠正为PERSONAL_STORE_NATIVE_ORIGINAL_PIN_REQUIRED（closed-pin和late变更）；除这两条外全文逆替同，suite runner仅test/plan名变，原fixture/18case/时限不变。该修正不弱化错误拒门，下一从新TEMP验证完整选集；尚无r683结果。

### r683实际通过及r684完整结果

r683十八项实际18passed／320.34秒，205输入保持；真实截止时钟等待/原取消、first/计费/late dispatch与真实lease变化拒绝全部通过。r679兼容性红和r681测试前缀红仍保留，未倒写绿色。

r684原完整单例1failed／44.22秒，201输入保持。首次install已返回，显式repeat resume的_fixed_v2_verify_node已返回；node_pin的stack.enter_context首整栈held poll超原30秒，尚未_enter_pin_original/_acquire_v2返回和后续body/SH。不从位置/总时长宣称稳定收益或归因性能回退。详见主方案§17.159及root-terminal73509cfb…；所有句柄terminal，无新profiling/真实库/服务维护。

## r685固定Pin获取入口的重复整栈检查

本轮先核45当前事实，再读原enter_context/_enter_pin_original。前者完整_stage_stack_original后仅type(resource) is _PinSelection分派，后者第一句立即再次完整_stage_stack_original。优先合并这个没有中间操作的相邻重复，暂不改上一节预算setter/rebind；不把失败栈位置当耗时测量。

新enter_context仅对精确_PinSelection和固定原_enter_pin_original、Store stack、held scope identity及Store directories直接分派，直接调用同一个原pin获取函数；其第一句完整整栈仍执行，全部真实owner/registry/guard/资源/预算/clock/cancel/目录/读取/hash/收费/close保持。未知直接helper、目录或子stack分支仍原全文；不新增DTO/skip参数或复用观察。首个实际物理整栈检查仍先于selection字段/预算检查和文件获取，原首异常优先。

测试须用真实_ClosingStack及_ReadStack、真实目录文件，证明ClosingStack来源资源从获取中3次核验变2次（factory首门及目录lookup整栈仍保），实际Pin读取/verify费用和完整退出；四个custom直接helper仍3次、参数不变；预算不匹配在获取前拒绝，实际scope核验首错不被selection错误覆盖且custom factory不被提前调用；复用真实Stage发布。通过后才执行原完整消费者，原期限/fixture不变。此处为实施约束，尚无运行证明。

### r685实际结果

Root自审封存Store0fc4abc1…／manifest3155a925…，只改enter_context成员/2指针，135其余定义及15模块保持。真实ClosingStack/ReadStack、费用、四custom、错误预算、首错优先及Stage发布共10passed／153.42秒，204输入保持。

r686原完整单例1failed／43.51秒，201输入保持。首次install已返回，但显式repeat resume的_require_install_profile内原Namespace immutable pin raw观察首poll超原30秒，repeat ClosingStack及_acquire_v2未开始。当前pin.verify已返回，checksum断言未完；不推断具体pin或稳定性能收益。原_immutable_pin_verify三属性访问各有完整ClosedFileHold观察，是下一源码评估方向；须保真实verify/read/hash/末clock/cancel及未知分派，未形成下一候选。详见主方案§17.160及root-terminal30adf3da…；所有句柄terminal，无新profiling/真实库/服务维护。

## r687不可变ClosedFileHold单次完整校验

只在_immutable_pin_verify内对精确原_ClosedFileHold及原verify/observation/raw/sha256 descriptor、原held文件verify/observation/类型/lookup/poll/记账、原_sha分派整合三次属性观察；普通pf.FileHold/未知类型或入口custom仍pin.verify后原完整表达式。入口资格不读取pin字段，不改变原verify错误优先级；verify后再核直接分派，不重跑verify。

实际pin.verify及完整文件重读/hash保持。固定分支真实查找scope/lease对应filehold资源，取得一个原完整FileObservation，原raw类型校验和实际_sha计算保持；计算后真实再次查找同一lease、执行原poll及首错误记账，重核同一resource/绑定/分派，最后比较SHA。原type/SHA检查错误仍在held记账外，真实lookup错误仍在try外；hash后取消/到期不能被前置观察替代。观察之后发生分派/关联变化时先经同一真实末poll处理既有first并拒绝，避免调用新custom分支或重跑正文。无跨函数/调用观察缓存或caller信任参数。

Namespace源码SHA改变必须同步Store和origin observer仅源码pin常量，并完整逆替核对，不能借此声称origin SQL/激活已验证。测试须证明两类真实profile guard的完整重读/每次新观察、入口custom全链、实际普通FileHold、raw类型/SHA拒绝及原记账边界、SHA过程中真实时限/取消、late descriptor/真实lease变化拒绝；再复用Stage真实发布和原完整Store消费者。此处仅设计约束，尚无运行证明。

### r687实际末门拒绝与r689测试预期修正

r687首选集16passed／1failed／264.57秒、204输入保持，前16项与SHA过程中真实到期的首次拒绝/记账均通过。失败在随后重复调用的异常对象is断言：原未改_poll_record先检查期限、最后才检查scope.first，因此再次到期产生新的SharedIoError；原_remember_failure仍保scope.first但更新resource.failure。原_immutable_pin_verify也先pin.verify，故相同前缀调用链有同样的重复行为。这是新测试误要求重复异常同一，不是原首错记录丢失；failed-terminal1754a98d…及原候选/test/QA/log保留，r688完整runner未prepare/run。

r689产品仍为冻结r687，仅新测试副本按原顺序明确断言：首次scope.first保持、resource.failure是最新异常、重复拒绝没有payload重读；deadline的新异常应不同但代码相同，取消仍是同一原取消对象。其余测试全文逆替同、suite仅test/plan名变化，原20case/fixture/期限保持。此处尚无r689执行结论。

### r689通过及r690完整边界

r689实际20passed／344.49秒，204输入保持；真实EX/SH、十custom、普通FileHold、类型/SHA、hash后真实期限/取消及late descriptor/真实lease门全通过，原r687红保留。

r690原完整单例1failed／44.13秒，201输入保持。首次install及repeat profile要求已返回，源码目录native获取/entered/完整identity返回后，末Budget native双时间门抛PERSONAL_STORE_TIME_LIMIT；不定位两项deadline或source索引。该次目录未返回/source filehold未获取，_source_pins/_acquire_v2/resume/SH未完成，不当稳定收益。三固定源同一包父目录却逐个打开原目录，是下一资源获取评估方向；只据路径相同不能授权共用，不省每file读取/SHA/收费/关闭。详见主方案§17.161及root-terminal463483cb…；全部句柄terminal，无新profiling/真实库/服务维护。

## r691一次固定源码获取中的真实目录共用

只改_source_pins的固定分支；原_source_acquire_original逐文件入口完整保留。固定原source helper/Store stack/enter_context/Directory类型/目录enter/held acquire及三个模块对象，且实际模块路径同父目录时，在本次source_pins内由原stack.enter_context获取一个真实目录。不是按路径复用历史缓存：每次调用新获取，实际目录record必须在同一真实Store custody及NS目录注册表，原字段/owner/clock、全部原physical checks保持。

每个源码仍原filehold获取、独立lease/read/SHA检查、Pin登记和原失败记账，source verify实际费用不改；逐文件和批尾完整_stage_stack核同一真实目录和全部资源，重核原分派、模块路径/expected、目录record关系，变化拒绝且不重放。未知custom或父目录不同时仍原三调用链，不接受caller传入目录/skip/许可，也不跨source_pins或effect缓存权限。原closing先全部file资源后目录，复用这个原关闭流程，不自行关handle或清registry。

需要实际三文件全部独立lease/数据/SHA与收费、单目录登记/原退出、下一调用新目录；custom回退三原目录及错误优先级；错误预算获取前拒绝；真实文件SHA错和中途路径/目录关系变化拒绝并保原已取得资源/first。再走真实Stage集成与原完整消费者。此处只是实施约束，尚无执行证明。

### r691实际通过及r692完整边界

r691 Root自审封存Store117eedb4…／manifest44cf3a29…，135其余定义/15模块不变。真实三file/单目录/原读与收费/关闭、下一批新目录、五custom、错误预算/实际取消/第二源SHA错/late函数和目录字段拒绝及Stage发布共13passed／173.19秒，204输入保持。未跨父目录搬源或做别名诊断，该fallback只静态核对。

r692原完整单例1failed／44.33秒，201输入保持。首次install、repeat source pins/_acquire_v2和第一完整body已返回，第二body全部pin verify及journal node校验返回后，scan charge("entries")首预算check抛原PERSONAL_STORE_TIME_LIMIT，本次entry未增加；第二body/正常退出/resume/SH未完成。两项deadline或entry索引不推断，不当稳定提速。详见主方案§17.162及root-terminala8664acb…；全部句柄terminal，无新profiling/真实库/服务维护。不能跳scan计费门/删完整body，预算setter重复仍为未实施方向。


## r693固定Pin预算setter内相邻整栈合并

新固定setter自己取得原Pin并完成原_pin_record完整整栈；不接收caller已检查record、skip或信任参数。入口与末尾核原预算checker/bound/rebind/poll/bind/need/真实Pin关联分派，预算字段更新前真实查同一已登记lease/resource/custody。只合并紧随的rebind整栈，原held rebind全文保留，unknown入口及首门后late分派仍走原剩余rebind。目标预算两次原校验、同owner/deadline断言、真实lookup、原lower约束、poll→真实binding赋值→poll全部保留；最后才更新wrapper预算。

两个poll后重核同一resource/Pin关联与分派，late变化拒绝、不重放；不在原rebind没有catch的位置增加记账或回滚。post poll失败时held预算可能已更新而wrapper仍旧，须原样保留并原cleanup。源Pin保持只换wrapper预算、后续实际source重读收费到新预算，held clock不改。无跨调用缓存，不删除第二完整body/扫描/字节收费。测试须用真实同owner的两个预算、实际文件/lease/registry，覆盖同预算/新预算/source、custom回退、非法预算、前后真实取消/到期、late变化、错误记账/原关闭，再复用Stage和原完整Store单例。此处为设计，尚无运行证明。

### r693实际通过及r694完整退出边界

r693 Root自审封存Store6a980e86…／manifestd6d8fbeb…，仅setter/2helper/8指针，138原定义及15模块保持。真实同/新/source预算及计费/原关闭、六custom、非法目标、pre/post各真实取消与原时钟到期、pre/post各late lease/bind、原取消优先及Stage共21passed／348.78秒，204输入保持。原lower rebind全文、前后poll/实际赋值及无catch记账边界保持，无新独审。

r694原完整单例1failed／42.70秒，201输入保持。首次install及repeat正文双完整body返回，return值已求但正在原ClosingStack正常退出；首栈/全部pin.verify/退出第一完整body返回，退出native第二body某pin setter首整栈身份poll触OWNED_IO_NAMESPACE_DEADLINE，未到该次目标预算检查/rebind。具体pin未知，resume仍未返回、SH未达，不当稳定提速。详见主方案§17.163与root-terminala3537e73…；十日志保存、全部句柄terminal，无新profiling/真实库/服务操作或审批拒绝。

下一已读方向是_ClosedStorePin.verify的相邻读前重复栈/观察：record→Budget.check→before observation→原verify_file真实读。只可在固定真实入口合并同次读前采样，保source真实byte差额、完整重读/hash/读后门/原first/unknown与close；尚无下一候选或执行，不删body/scan/charge或退出校验。


## r695固定Pin核验的重复读前资源遍历

上轮中断仅只读重核/设计，无新候选或运行。本轮再实核r694终态及43当前事实。独审指出仅same Pin/resource不能代替原Store镜像/Stage.first/namespace预算/目录首尾逻辑；因此缩窄为仅合并第二整栈的held资源identity sweep，第二原目录完整物理核验仍保留。

新固定_verify_pin_original自己做原_pin_record完整核验，保持在原Budget try之外。try内原_Budget.check后，固定直接分派/原资源关系才调用逐句原_stage首逻辑helper（mirror/owner/first/Stage.first/IoStack/预算/currentguard/namespace登记），保原held scope first traceback与取消门，再原_store_directories_verify_original完整目录逻辑/physical首尾和原完整held文件observation。独审要求捕获省略sweep直接调用的identity/lock函数，unknown回原chain；正常计数测试使用更底层真实_identities透传观测，不通过修改捕获指针假装fixed路径。

读前observation后保真实lookup在inner catch外，原poll失败保held first；随后Store状态复核在held catch外、Budget try内，真实Store/Stage first优先。最后same resource/Pin/实际budget binding/分派门失败依原Pin关联错误记held，外层原Budget first。原_verify_file_original的真实read/hash完全保持；source读后的原完整pin._observation与实际byte差额charge保持。不要求Pin budget与stack budget同一对象，不接caller已检查record/skip/缓存，不删完整body/close/scan/charge或扩30秒。此处为设计约束，尚无候选运行证明。

### r695二十七项实际通过／r696更早超时及新增guard复核

r695 Store c6745b97…／manifest ec20558a…，140旧定义/15支持模块保持；独审源和最终测试无阻止finding。固定逻辑前段逐AST原样，类descriptor准入读取已改为__dict__.get保first。实际27passed／479.01秒，204源保持：真实source/business及原/新预算、每次新核/精确read与charge/正常退出、九custom、late dispatch/lease/mirror、真实取消及原deadline、真实读取后expectedSHA拒绝、source读后取消、descriptor与首错记账、Stage发布全部通过。

r696原完整唯一1failed／46.78秒、201源保持：首次install返回，repeat源码批目录某path组件前置Budget.check经Namespace held poll超原30秒。该组件stat/open未到，组件索引/先前组件工作未知；目录未返回/source filehold未得/repeat body与SH未达。比r694早，不当稳定收益或性能根因；root-terminal7e32c21e…，十日志/全部句柄terminal。

后验独审纠正“状态helper是纯逻辑”的理解：其中ns._require_profile实际调用lifecycle.require_guard→allows→StorageGuard.verify，含锁marker读取/身份/目录和covered DB路径检查；r695新增tail因此比r693多一次完整guard。current_guard也有物理verify，不是替代。下一应保原record/pre物理完整检查，仅将额外tail改为限定固定原分派的真实内存关联/owner/current/active/member/mode/first/预算门，unknown回原完整tail；不接受caller检查结果/skip、不重放、不授runtime权限。此为源码因果链，不是r696耗时归因；r697尚无候选或执行。详见主方案§17.164及data/h01-r696-next-source-review.json（02b061de…）。


## r697固定Pin额外尾门的guard物理去重

本轮实核r696与45当前facts。从r695新建，只改_verify_pin_original额外尾门；pre _pin_verify_stack_state_original完整原样。固定完整verify在pre目录检查后从实际stack登记/guard获取本次局部对象期望（custody/namespace/guard/profile、owner/profile集合/locks、active/mode、原Path与lock字段），不接caller检查结果或skip，不向任何callee传该tuple，不复用Namespace专属plan/current_guard或授予普通writer权限。

新tail helper自己查实际stack，保逐句原mirror/Store first/Stage first/IoStack/预算门；仅原lifecycle/require/profile/identity/guard/lock直接分派和同current guard时核真实active/owner/member，未知或另current guard走原require_profile余段，不重放prefix，保原Storage错误顺序。调用者在helper返回后对自身局部期望逐对象is比较，变化拒NATIVE_ORIGINAL_GUARD_CHANGED；全tail仍held catch外、Budget try内。入口custom无法产生局部期望，保r695原完整tail。late unknown可在剩余full后拒，不重放正文。

独审提出guard及lock字段descriptor会提前getter，已补类identity/__dict__字段门；测试增加真实data descriptor准入零getter与full fallback。原文件重读/hash、source post/精确收费、clock/cancel/目录/所有close不变。复用r695原27并加实际marker差额/guard失效/current/member/mode/集合/custom/first，须实际运行后才称通过；真实第二guard切换未作为合成owner测试。此处是设计及静态准备，未执行验证。

### r697原红／r699三十八项通过／r700进入共享写入目录

r697最终Store92d19bf5…／manifest927530e1…，143旧定义/15模块不变，独审descriptor finding关闭。首suite28passed1failed402.19秒/205源保持：原27及实际marker3vs4过，active原拒门之后Namespace._errors把Lifecycle错误转换成新的NamespaceError，测试outer类型错。原test/log/failed-terminal d3036eb7…保持，r698未prepare/run。r699只新副本按四Lifecycle/两Store的原内外语义修outer，并加退出后Budget.first仍原caught；产品不变，独审无阻止，实际38passed471.99秒/205源保持。真实guard变更/未知回退/first/descriptor及全部原读取/费用/clock/close均过，未测试另一个有效guard切换或做新profiling。

r700原完整1failed67.27秒/201源保持，首次完成_install整段的安装/repeat相等/原Namespace与profile退出/安装DB保全；已进入SH profile、真实runtime Namespace EX、open_authority、IoStack。operations 32位key的第12层前缀e3ce53e862da目录已创建和预核，writer_new_resource的目录入口scope末双clock抛STAGE_ORIGINAL_DEADLINE＋原退出附注；当前StageResource/lease/暂存writer尚未取得，node发布/walk/prepare/put/最终退出未完成，哪个clock未知。root-terminal1123d7a4…、十五日志/全句柄terminal；不据单次通过段落宣称稳定性能或整体通过。

独审定位下一重复实际在Stage._directory_original首scope已完整核Store stack，紧接的Store目录lookup又核同stack；_new_resource自身没有_scope_live，不能以错误前提去重。只能在固定入口本次实际binding下合并，保NS目录真核/128层祖先/参数与first/clock、unknown剩余调用，不跨publication真实effect复用。下一r701尚无候选/执行，详见§17.165及stage-review45b59e6e…。

## r701固定Stage目录入口合并相邻Store栈核验

从冻结r697新建，仅修改Stage._directory_original并加两个内部helper，Store、Namespace、held及其余15支持模块保持原字节。入口先纯读取本次真实scope/custody/stack镜像及关联Namespace、held登记和资源图；保原_scope_live完整执行、原Directory类型门，再比较本次关联。仅固定直接分派和真实guard原分派成立时，合并紧邻的第二次Store整栈物理核验。Store._directory_record_original自身完整保持；内联余段逐项保Store._need、实际Namespace目录登记、原全量目录核验及128次祖先门和原Store异常。

快照仅在本次入口局部逐对象is比较，不传入任何callee作为权限或检查凭据。类slot/普通字段descriptor先准入；缺失字段返回None，由原流程确定first与异常顺序。只读取当前scope/stack/Namespace owner/held scope的登记项，不遍历其他scope；保actual held资源list及逐资源原字段。未知分派、迟到变化、任何关联不相等均继续原Store lookup，不重放_scope_live、不新增通用替代错误。原_new_resource输入检查、真实读取/计费、取消/两clock、发布effect及正常关闭不改。

独立源码审查已要求并补齐stack镜像、相关stage.first、missing字段、nested自定义函数回退和相关资源图；当前为静态候选，尚无本轮运行通过证据。新增测试覆盖真实每次新核、原余链、自定义guard/held、缺字段和descriptor首错、真实目录与祖先、原取消/自然期限、迟到镜像、Namespace初始化及实际metadata发布。原r700失败和全部旧候选保持。


### r701原22项通过与不可达新测试失败／r703真实组件通过／r704正常退出超时

最终Stage17e1a4fa…／manifest89682593…，57原定义/15模块保持、两路源审无剩余阻止。r701实际22passed1failed352.12秒/204源保持，最后新增测试误以为public initializer自然调用Stage Namespace入口，实际沿自身Namespace writer返回后calls空。原红和旧静审记录保留；新增分析717d68a1…明确接线遗漏，不能删断言后仍称覆盖。

产品不改，r703新增原snapshot登记返回后的真实factory组件消费者，原registered stack/permit、原CancellationData/真实空held scope、两次原目录核与Stage→held原关闭，随后原初始化完成；实际1passed13.51秒/205源保持。原22项未重跑；此组件不是public自然接线或Stage writer IO。独审在执行同期/后完成，不倒记为执行前；r702未prepare/run。

r704原完整actual1failed43.74秒/201源保持：首次Store install返回；显式repeat正文两完整verify及return值求值完成，正常退出首栈通过后，某Pin的Budget→Namespace held poll触原OWNED_IO_NAMESPACE_DEADLINE。本次Pin重读、repeat返回/相等断言、_install最终DB保全/返回、SH链均未达，pin/resource索引未知。比r700早，不推稳定收益或归因。Root终态59af8551…、十五日志/全执行句柄terminal，详见主方案§17.166。完整goal和原所有剩余范围仍active/未完，原暂停保持；无profiling/扩时/真实库/服务/dist/ACL/Codex设置/commit/push。

## r705安装Stage重复profile核验与共用自定义分派修正

本轮先重核r704及52项当前记录。相邻发布入口的第二_scope_live还承担原held资源逐项poll、首错、预算与时钟，不能只保Stage两个clock就省略。因此选择更窄的_verify_store_stage_scope_original：其首Store._stage_stack_original已完整核profile，安装分支随后又exclusive require_profile。保完整首栈、原关联/预算门、runtime分支、末bytes和双clock原样，只在同一完整调用内的实际绑定与原分派保持时，以现时Context/active/owner/member/exclusive逻辑核代替第二次profile物理观察。unknown/late直接在原调用点继续exclusive require_profile，不重放首栈。

局部binding自己取真实scope/custody/stack/Namespace/held资源图与guard/profile/lock及guard目录字段；不接受caller检查结果/skip或把tuple传入callee当授权。missing与未知descriptor先返回None，已有first回原链。runtime state非None在guard字段读取前回原分支；原type(stack)/native_owner短路门仍先于profile逻辑。guard.exclusive原property单独固定，取消和时限门不变。

独立静审发现两项具体缺口并在未运行草稿中修复，不能称为已运行红例：一是目录identity元素若为可变list，逐对象is不足以发现原地inode变化；合并准入现限定原3元tuple(Path,int,int)与parent=None，未知继续原完整核验。handles/fd同时限定原工厂类型，仅是防御性准入，不声称原门必拒其他shape。二是已有Store guard-dispatch只核Directory.entry_stat，漏了原guard明确支持的Directory.verify及两目录plan helper自定义分派；此判定还被旧Stage目录入口、Pin尾消费，不能只在新入口修。最小修正集中到Store._pin_verify_guard_dispatch_original及3个原指针：先class verify/属性分派与两plan函数，再在原guard形状检查之后核每个实际目录的bound verify，均不调用verify或做物理IO。

最终Stage `5e714478c4f982ea66293ac4bc5125a632cf0cae77f27ee4e0bad116574a0496`，Store `dda8b7c6832ef42b44a3cab9690c9f896629a5b290011235b2811478aa7b0cdc`；Stage仅1原定义+2helper，Store仅1原predicate+3常量，59/146其余原定义和14其他模块保持，四consumer包名/两真实SHA逆替等旧。旧bd718/94f45e草稿及中间review d486保留；最终源独审cc64a4c3…无剩余finding，仅静态结论。24项真实测试已准备，包含原native marker读取/源身份/费用、custom与late、缺字段首错、目录可变行、原预算/实际期限、Pin/Stage目录继承消费者和metadata发布；此处尚无运行通过证据。

### r705中断保全／r707二十四项通过／r708首次安装正常退出仍超时

上述准备态后，r705 session21602恢复时工具句柄已不存在；stdout仅6个点且无两份最终结果。普通Get-Process权威枚举确认PythonProcessCount=0，CIM查询失败未作终态证据，实际退出码/原因仍未知。206输入和26冻结实体重核，三原日志按bytes保存至新证据目录；中断终态64579268…保持，未重启旧QA，不冒称6项或24项最终通过。

新r707只改runner PLAN名，产品/测试与r705同一冻结输入；实际24passed431.99秒、72阶段全passed、206源保持，终态56d71d2d…。覆盖真实每次新marker观察/原字节计费，custom verify/allows/exclusive与Directory.verify/两plan函数回退，Pin和Stage目录继承消费者，late owner/current/member/mode、目录list及可变identity行、missing/descriptor/first、实际原期限、原metadata发布和正常关闭。两静态finding修正没有伪记为前置运行红例；r706从未prepare/run。

r708原完整唯一1failed47.59秒、201源保持。此次是首次install内部resume正常退出：正文_acquire_v2、writing两完整body、return值求值及Store退出首栈/全部Pin.verify/两完整body均已返回，Stage无既存first进入初始_verify_stage_scope_original/_effect。原_N2_EFFECT内Namespace body的_anchor_names当前entry预算门触NAMESPACE_DEADLINE，附两条Namespace cleanup notes；entry已由scandir迭代取得，其前缀/名称/stat处理未开始、索引未知，scan/native effect未返回。后效profile及末scope未达；首次install未返回，repeat/相等/末DB断言与SH全链未达。比r704早，不推稳定性能或故障归因。

记录器仅文件操作实际exit0，r708五日志逐bytes保存；r707五件已存只核，r705三件保持，全部执行句柄terminal。Root终态48af23bcd4e6e11b37c95b4d875dcf0bcca341391b8d07e726dea14d88399b23，详见主方案§17.167。下一仅只读评估_effect原_N2_EFFECT返回后紧邻profile是否具备同次物理重复，保完整native begin/body/finish、现时真实owner/guard、双clock/取消/first、末scope和全部effect/cleanup；此调用不是r708抛错点，无新候选/运行或热点证明。完整goal及全部原剩余仍未完，原暂停保持；无新profiling/扩时/真实库/服务/dist/ACL/Codex配置/commit/push。

后置独立边界review b06987e85ebc95bba85fd69d3d887153657797d15310b89680c9d5436ac7da71绑定已封终态、保存日志与实际源码，确认Stage资源循环前初始effect、Store前置first=None证明链及entry已yield/前缀处理未达；不把清理尝试或teardown通过当全部native关闭证明。其调查建议仅安装scope的post-native非EX profile，双scope大拆分deferred；旧终态未倒填review，也没有下一候选/执行。

## r709安装Stage effect后置profile合并：静态候选与实施前边界

以[r708独立边界及源码复核](/<PROJECT_ROOT>/data/h01-r708-boundary-source-review.json)（SHA `b06987e85ebc95bba85fd69d3d887153657797d15310b89680c9d5436ac7da71`）为依据，在新的r709候选目录评估更窄改动：只合并安装scope的Stage._effect内原_N2_EFFECT成功返回后紧邻的profile重复物理核验。r708在该次native正文中失败，尚未到这条profile调用；本候选不是已证热点修复，也不预告原完整期限可以通过。相邻两次_scope_live的大范围分解继续deferred。

候选保留_effect首尾两次完整_scope_live，原_N2_EFFECT仍恰好调用一次；Store完整native begin/body/finish及其inside/after预算路径不改。首次scope已处理原first之后，才读取本次固定分派和真实安装绑定；native完整成功返回后再取当前绑定逐对象is比较。局部绑定包含实际Stage/Store custody、stack、Budget与BudgetRecord、Namespace acquisition/登记、guard/profile/lock和目录字段。目录沿用原不可变shape限定，unknown shape或missing/descriptor保原剩余require，不在准入中触发自定义getter。所有局部tuple仅作本次期望比较，未作为caller提交的权限、writer许可或跳核凭据。

新状态helper自己重新解析真实_SCOPES、_STACK_RECORDS、_BUDGET_RECORDS和_CLOSED_NAMESPACE_OWNERS，确认native实际returned acquisition与所选custody/预算/registry对象一致，再执行现时profile类型、CURRENT guard、active、owner及member检查；保原返回guard与actual.storage_guard的is比较和错误路径。这里原profile gate是**非exclusive**，不能复用安装exclusive helper强加模式条件；原安装EX要求仍由首尾scope承担。runtime Store scope及Namespace分支保完整原路径，Namespace EX/profile SH语义不因此扩大或收紧。

固定准入覆盖实际_N2_EFFECT与Store native dispatch、inside/after、Namespace observation wrapper与begin/check/finish、guard plan及物理files/directories相关原分派。unknown或native中途变更只执行原剩余ns._require_profile，不重放已完成native，不跳末_scope_live。原取消、两层clock、首错/traceback、全部read/hash/计费、资源effect/rename和cleanup保留；无跨effect缓存，不借已退休的Namespace observation token，也不让普通require_guard接partial输入。

当前[data/t16-runtime-origin-graph-r709](/<PROJECT_ROOT>/data/t16-runtime-origin-graph-r709)为静态候选，独立审查进行中；最终源SHA、manifest与审查结论待审定后补记，不将草稿当冻结通过。测试草稿[tests/test_stage_effect_profile_r709.py](/<PROJECT_ROOT>/tests/test_stage_effect_profile_r709.py)当前展开13项，包括真实native读取及少一次新profile观察、五类custom native组件、late分派且native不重放、Stage/Store/Budget原first优先、原native finish中真实取消和自然原期限到期，以及原metadata发布组件。该计数仅是静态计划，不是运行结果。

截至本段记录，[r709选集runner](/<PROJECT_ROOT>/scripts/verify_stage_effect_profile_r709.py)与[r710原完整runner](/<PROJECT_ROOT>/scripts/verify_authority_store_r710.py)均尚未prepare/run，两份对应plan文件不存在。没有本候选测试通过、性能改善或完整Store/H01完成证据。r705/r707/r708全部原证据保持，本段不修改主方案或AGENTS的最近真实终态；完整goal、原未完成范围及暂停边界继续保持。

### r709草稿收敛、冻结与十四项通过；r710首次安装退出Pin预算超时

上述实施前草稿后，初3d595…按独审补模块identity先行与vars分派；中间edacc…额外类shape/init-new约束超出本次兼容性范围，最终撤回，不记产品缺陷或运行红例。最终Stage3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611，manifest ae9fa10fc28bca6ceebb1f9e4e5ccf6975e22d32b7b7cb3dabe9231dde6e428f；仅_effect+3helper，61旧定义/15模块保持，25实体实核。源/测试独审口头0阻止在执行前，artifact可与启动同期；Root源码记录08b399b1…，原首尾scope/Namespace/native call/error AST保持。

实际r709 **14passed363.86秒**，session27673／terminal008437／exit0，42阶段全passed、206源保持。静态13项草稿后新增完全原dispatch真实metadata发布，和旧inside/after wrapper导致fullfallback的integration分别验证两种自然发布路径；真实写入/SHA/读回、原registry/factory及正常cleanup/DB保全通过。其余真实native新读/逐次费用、五custom、late不重放、三first、真实取消/原期限均按原断言。suite终态2379ee76a286cace29accb32376218956b89fec5f85f6d63a0f236af84a31776；五日志已存，不能外推完整安装/SH链或稳定性能。

随后r710原完整唯一实际1failed51.92秒，session95423／terminal8603d8／exit1，201源保持。首次install内部resume正文/acquire/writing双body/return求值和Store正常退出首栈已返回；某Pin的_pin_record原整栈已返，接着Budget→Namespace held identity核lock(content=False)在身份首poll报OWNED_IO_NAMESPACE_DEADLINE。当前Pin索引未知，当前held资源lock可确认；此次lock后续物理身份、当前Pin读前state/observation/payload重读及Store退出两body/Stage正常verify未达。首次install/内部resume未返，repeat/相等/末DB与SH链未达；比r708早，不推稳定收益或归因，不把teardown当全部native关闭证明。

下一仅只读评估Pin.verify原Budget成功之后fixed state helper再require同guard的相邻profile重复，保完整Budget/NS/lock poll、原State/first、真实目录/read/SHA/计费/close；该后置步骤不是本次抛错点。没有后继候选/执行，原失败/草稿/QA和完整goal、其余暂停/未完保持，详见主方案§17.168。

r710独立边界review6ee1568dc5e6d6af4bf79e5702ac1c8e5013bbfea1a2ef64e9b317796dd41624绑定已存五日志/plan/claim/源码，确认lock非content读取、前置Namespace profile与Budget已返，原first/cleanup边界及下一same acquisition/guard/Pin-Store预算关联/r705目录shape限制。stdout无r708 cleanup附注，不移植旧失败细节。记录器actual exit0、25实体与206/201源和42/3阶段重核，两组十日志保持；root终态03b0e6d2356df4d6fdb5734c433a3b66ee7708de1f7544f335b9c4ad46032189，全部执行句柄terminal。本轮不建r711/不再执行新测试，全部原未完/暂停保持。


## r711 Pin读前profile的同次核验设计与静态草稿

新goal续轮已重新核r710终态03b0e6d2…及254当前事实，上一轮属于实现与实际验证的进展，两执行均已terminal。本轮按r710独审6ee1568…追踪的源码重复推进；该prestate步骤在r710失败Pin中尚未到达，不能把源码重复当作已测得的超时原因。

原_pin_record_original及完整_Budget.check均保留。在原try范围内，原Pin-record返回后捕获本次实际绑定；只有原Budget成功完成Namespace profile/整栈/held lock poll/取消/两层期限之后，fixed分支原_pin_verify_stack_state_original的profile物理部分才可与本次前核合并。现有_pin_verify_tail_state_original保原stack镜像、owner、first、IoStack state、budget、Context/member及Namespace登记的逻辑顺序；未知、缺字段、自定义分派或late变化只走原剩余prestate，不重放已完成的Pin-record/Budget。

局部绑定须关联实际Pin、Store custody与selected/stack两个BudgetRecord，允许合法不同预算但要求同一实际Namespace acquisition、stack/scope、profile/guard与登记；不得仅凭same guard授权。guard目录沿用r705的parent、handles/fd与不可变(Path,int,int) identity行形状限制，前后逐对象比较，仅当前调用保存，不传caller checked/skip，不借Stage或退休Namespace观察。已有Budget first必须让原Budget在原位置抛出，新捕获不能提前触发未知guard getter。

当前新r711为未冻结静态草稿，仅Store._verify_pin_original与三个纯准入helper；原Store Budget/后续tail、Namespace/held/Stage与其他模块保持。原真实目录遍历、held first/cancel/poll、file observation、payload重读/SHA、来源后观察、精确费用及全部close保留。独立源审与16项候选测试正在准备，尚未获得本候选运行证据，r711选集/r712完整consumer均未执行。没有新增profiling、扩时、真实库/服务/dist或旧ACL/Codex设置操作；完整goal及其余未完、暂停范围保持。

### r711冻结、静态source计数修正与十六项实际通过；r712第二body入口仍超时

最终Storeb38a4e85e7f4754de575bc6fe04e690594d75b51bab5c141765b028219b544e9／manifesta011b8faca62cbc4d6fcfdef8d2c4819212c80155ba3a928d5c88b77e3df3731，25实体；仅_verify_pin_original+3helper，146旧定义/15模块保持。完整Budget、原first/读前后链与真读/SHA/收费/close保持；source review43f6c8…在freeze前，最终test口头0阻止在执行前、artifact可同期。

独审执行前纠正source收费尾charge→self.check还产生完整NSprofile/sweep：source每verify4marker/2sweep，business2/1，禁新增prehelper参考都只多1marker。旧测试bc72816e…以xb保留，最终1c7fc2…五处delta逆替精确等原；产品未改，无运行红例。实际r711 **16passed360.86秒**，session57789／terminala8e9cf／exit0，48阶段全passed、207源保持。真实same/rebound及source/business新读费用/关闭、custom/late剩余路径、目录shape与late inode、原selectedfirst先于后装descriptor、实际取消/原deadline/错误SHA及原dispatch metadata写读回/关闭均过。

suite终态394f2ce3108942128e51a7e8a0845aec7c4e54229620c93fe3a86ff21bbca97e、五日志已存。其后r712原完整唯一实际1failed50.48秒，session66378／terminalaa0c99／exit1，201源保持。首次install内部resume的_acquire_v2与writing第一完整body已返，第二native body首_namespace_verification进入runtime observation begin，在原首NS stack check的held非lock identity首poll触OWNED_IO_NAMESPACE_DEADLINE；具体kind/name/index未知，不是r710的lock。context未yield/当前NS body未进，当前资源后续物理identity、第二Store body的state/Pin重读/node scan及return dict求值未达。

首次install/internal resume未返，正常退出校验、repeat/相等/末DB与SH链未达；无Namespace cleanup附注。比r710早，不推稳定性能或退化因果，也不以teardown当native全部关闭。完整goal/其余未完/暂停保持，无新profiling/扩时/真实库/服务/dist/ACL/Codex配置/commit/push；详见主方案§17.169。

后置边界review5d4fdb8e98f949818227e57be9179bd387a08428f2cb6a96f2b63cf312d0b81e确认201源及原QA/保存日志保持；失败begin尚未注册observation/Context，原membership条件不执行该scope finish。Root记录器actual exit0，25实体/207与201输入/48与3阶段/两组十日志复核，终态2e6a910082adde09d0475894afee02d43825c141c5b40c1333837f2fe358abbd，全部执行句柄terminal。

唯一下一源码问题为默认_source_pin_batch_original同一_need内sha256/raw两metadata观察，保第一个full、sha失败短路及第二raw的lookup/poll/first在hash前原时点；必须保真实acquire/read/hash、每source/每batch新核、owner/registry/mirror/budget/clock/取消、原收费与close，unknown/late原余链不重放，不能照搬Namespace的hash后门或跨pin.verify/effect共享。该批获取已在失败前返回，非热点证明；_verify_sources本次未达且后续_V2State owner/runtime也使用，不能只归families。本轮无r713/新测试，完整goal/原全部未完与暂停保持。


## r713 默认源码批校验的相邻属性观察设计

本轮重新核r712终态2e6a9100…及257当前事实。上一轮16项通过和原完整失败均保留，属于实际进展；本轮依独审5d4fdb8e…只处理首次install/internal resume已执行的默认_source_pin_batch_original。_verify_sources在当前失败前未达，虽然之后_V2State owner/runtime也会调用，不能将该未到入口当成本次修复依据。

设计保守地保留第二次原_stage_stack_original全量：所有其他资源、guard、目录、真实预算、first和取消/期限门不动，仅合并它刚核过的同一目标文件紧随的第二次身份观察/Observation构造。首pin.sha256原getter与失败短路保留；只有原SHA匹配后才进入raw部分。固定原raw/sha/_observation/helper分派及真实Pin/stack/held resource/lease绑定，内部消费者自行完成第二完整栈，再执行原late resource lookup、poll/first与held记账，在原_sha(raw)之前取当前真实source payload。不将Namespace immutable校验的hash后尾门搬到这里，也不增加hash后的新检查。

未知入口保原余raw，第二stack期间发生late变化保原剩余file identity条件/observation，不重放已完成的SHA或第二stack。原_source_acquire_original与_verify_sources保持；初始真实open/read/hash、FileHold第二真实校验、三独立源、每源与批尾检查、费用和原close不变。此设计只省目标重复物理观察，不能描述为省第二完整栈或少了文件读取。没有跨源/批/Pin.verify/budget setter/Store body/Stage effect/退出的缓存，也不接caller checked/skip。

当前设计和候选/测试准备中，尚未有r713执行证据。须以真实empty_stack/source factory比较新分支和原路径，证明获取两次真读、账本、正常关闭、下一批新核；验证首SHA短路、原hash前取消/自然期限/first及custom/late剩余路径。完整goal及原其他未完/暂停范围保持；无新profiling、扩时或真实库/服务/dist/旧ACL/Codex设置操作。

### r713静态收敛与冻结边界（选集运行中，尚无终态）

最终Store `f776b3f3e31df7b7b9bf0993ee627ec71607c3f0804ac8f239bd5b7bb652beaf`、manifest `8ee449b03c90b3b39b2efa643bc9716de45aa13345cc0a87757d9604ca9fbff6`，25实体。仅默认batch原_need调用点与3helper/原指针新增，149旧定义及15支持模块不改；Root静态记录dbe5b69a…核原batch逆替、第二raw原type/owner/closed前缀、完整第二Store栈和late identity条件AST保持，_source_acquire_original与_verify_sources原样。

执行前独审发现并修正两个具体语义缺口：初5b2a…漏既有Namespace目录single/sweep/with-paths及Store sweep-dispatch支持分派，补其原准入；中间efdd…在raw之后才重取_sha，与原_sha(pin.raw)先选callee相异，现首SHA匹配后先捕获原checksum函数，后续raw/第二stack中晚替换仅影响下一个独立表达式。两草稿及各original-facts均保在候选内具名目录；源review1aa9bc94…0剩余finding，这些是静态缺口，不是运行红例。未知入口完整保原==/and；首SHA失败不触新增binding/raw/hash/poll，不以!=替换原比较语义。

最终测试 `72e9b307d15c2fb6cc0c5cefe8b81265ed443d425d7a02e86d3233d9760e8799` 静态展开19项，原bda57f05…草稿另存；真实获取/费用/close/fresh batch、custom属性和目录helper、首SHA短路、raw分派前后late差别、late_sha callee、hash前真实cancel/deadline/first、实际scope拒与默认metadata均已静审。测试reviewbc47cd3d…原state的before_prepare_or_execution标签不作时点证明；原artifact保留，独立timing-addendum `bc64c10b64dc5e66503517cd73dbf66c5015ed570c814a5d27cd9be717f2340c`明确只有口头0finding结论在执行前、文件写入可能同期。

本段时r713已prepare208输入并在唯一session86825运行，尚无实际终态/通过结论；r714原完整runner存在但未prepare/run，总记录器仅骨架。不得据静审或stdout点数推十九项通过。完整goal与原未完/暂停保持，冻结测试/产品/runner不再修改。

### r713十九项实际通过；r714 header暂存打开后Namespace写尾仍超时

上述静态阶段后，r713唯一session86825／terminal4fd365／exit0实际 **19passed416.27秒**，57阶段全passed、208源保持。每源checksum的hash前metadata identity3对4、整个批每目标少1成立，完整第二Store栈仍保；真实acquire两次实读、3独立fd/lease、原验证费用/正常close和下一batch新核均过。custom属性/目录helper、首SHA短路含晚取消、第二stack前后late raw/observation、late_sha旧callee、hash前真实取消/自然期限/heldfirst、真实scope mirror拒及默认metadata发布均按原断言通过。

suite终态a53324ad98791d9ac30d8bf11b02bb82f7534b4e4e74cb3b1e687793fc3f40e8、五日志已存；timing补件bc64c10…继续约束旧review标签，两个源码静态finding不是运行红例。其后r714唯一session56229／terminal49b1ec／exit1实际1failed51.18秒，201源保持。

本次首次install内部resume已创建/扫描journal和五子、构造header。header暂存CreateFileW成功且已转fd/fstat/initial_native，creation为fresh-empty-before-private；_open_resource259 effect失败前尚未private初始化、graph/stream或任何payload写入。Namespace body4933首完整尾stack已返，writing4309第二stack中非lock资源首poll/graph/首目录/path-handle身份相等均已过，随后directory identity的runtime Namespace Budget抛NAMESPACE_DEADLINE，kind/name/index未知。原已登记scope异常finish尝试保留，带STAGE_ORIGINAL_CLEANUP_RETAINED而无Namespace cleanup附注，不证明清理成功。

独立lock尾核、native成功返回/Stage末门、header payload/read custody/rename/发布、内部resume_acquire_v2和两Store body/return、首次install/repeat/SH均未到，比r712更早，不推性能或退化因果。独审185fc0049b1d5627cfbde4d0d4953d1723ddff93ec61d9aea1470879bd1075e8绑定原QA/五日志/201源；记录器actual exit0核25实体/208与201源/57与3阶段/草稿与timing补件，两组十日志保持。Root终态1c235b2f4ffdb01f2b15a477ec176bb2baa3a26c62b66bb5035d08e41bb91aa2，全部执行句柄terminal，详见主方案§17.170。

下一仅设计评估同一NS body尾4933→writing4309相邻完整stack的物理重复，保首full、真实current graph/资源/binding、owner及profile SH/Namespace EX、全部晚first/tb/Budget/取消/poll、独立lock真实核与原异常finish；unknown/late原余链不重放，不移除第二门或借旧观察。无r715候选/执行；完整goal/原全部未完与暂停保持。

## r715 同一runtime body写尾的非lock filehold身份观察：静态设计

按r714独审已证的body尾相邻两次stack方向建立本次草稿。缩小的合并点仅为第二遍非lock filehold的_closed_io._identities物理path/handle采样：第一次完整stack真实检查全部资源/目录保持原调用；第二遍仍执行原stack/custody/owner/mirror/registry/资源成员顺序和状态核验、first/tb、scope及预算关联、取消和每资源poll、graph/私有文件约束、目录身份比较与首尾目录核验。每个适用非lock资源在第二遍的三次runtime目录Context/Budget检查全链保留，包含其后的取消/graph/ledger约束；r714原期限失败门仍然存在。lock保持原完整身份与locked/native检查，writing尾独立held.lock.verify的实际锁内容读取及所有payload read/hash/计费不减；正常和异常finish的完整物理尾核保持。

当前实现由原六个函数体机械抽取为本次tail内的私有闭包：held._verify_identity_record、held._verify_scope_identity_original、Namespace._closed_stack_check_original、_runtime_body_stack_check_original、_require_runtime_namespace_write_original、_runtime_body_write_check_original。只对明确调用位置改路由，builder在[extraction-ast.json](/<PROJECT_ROOT>/data/t16-runtime-origin-graph-r715/extraction-ast.json)与candidate-evidence中记录逐体逆映射AST相等；这属于静态构造证据，尚不代替最终独审或执行结果。普通函数本体和原自定义调用合同保留；未知准入走原完整余链，second遍晚变化回实际身份观察，不重放已完成的body或首完整stack。

本次期望值只在当前tail调用局部绑定真实held/stack/custody、registered runtime scope/Context、guard plan/预算、ordered资源与原身份字段；它们不是新增scope、token或caller proof，不提供writer权限，也不形成跨body/effect/退出的缓存。nonwriting保持首完整stack后原行为，profile SH与Namespace EX组合仍按原合同准入，未新增profile EX要求。Namespace改变原runtime body尾调用点并新增三个helper；Store与observer只适配实际Namespace源码SHA，13个其余支持模块逐bytes保持。

此处候选builder已prepare为静态草稿，尚未freeze或执行，不将当前draft SHA写为最终。Root的测试/runner仍待静审收敛；计划在真实原owner/factory下比较非lock与lock采样、全部晚门、custom/late、first/tb和正常/异常finish。r713十九项绿色与r714原完整失败及暂存/retained证据保持，不由本设计推性能改善、完整通过或goal完成。本段仅追加设计，主方案/AGENTS仍以§17.170的已封终态为准；其余原未完与暂停边界保持。

### r715静态收敛与冻结边界（37项选集运行中，尚无终态）

上述草稿已完成执行前独审并冻结：Namespace SHA `62a1d30835444f14880d96579773d2c9259102db56d4998831e051767176cee5`，manifest `005ae33a7aabf736685930441a334aa49c4738550909dfa0fed865e793cc57c3`，20源加7artifact共27实体。只有原runtime body尾调用点变化、增加3helper；216个其他Namespace定义及13支持模块保持，Store/observer仅适配Namespace SHA。六个闭包的逐体逆映射AST、整个runtime body逆替、四原consumer包名/SHA逆替均有静态相等证据；这些检查不构成运行通过。

独审静态发现并修正两组准入缺口：既有profile wrapper允许自定义 `_require_profile` 与 `lifecycle.require_guard`，需连同guard-plan state leaf核原分派；runtime目录batch的直接 `_closed_runtime_directory_record_verify_original` shim也须纳入准入。三版具名草稿和original-facts保留，均无运行红例。最终仅 `writing is True` 捕获首stack前局部期望值，原首完整stack及原writing判断保持；每条闭包helper边按当时实际callee选择剩余原链，Held全局仍通过实际Held模块取得，不重放已完成节点。原first先于custom descriptor、所有runtime目录Context/Budget、lock完整采样/独立读锁和正常/异常finish均保。

源独审 `data/h01-r715-runtime-tail-identity-source-review.json` SHA `4852b0dc04d63c6bf0caef75e7cc874a42002663cee8ac807f537fb151e330de`，测试独审 `data/h01-r715-runtime-tail-identity-test-review.json` SHA `dd7c47caf1a21fa9b90c6492ed01ac1fd0f4dae57e4a2a962f7aa3f89bdca4b2`，均0剩余finding；本次两份报告确已在Root prepare/run前落盘，非仅口头先行。Root静态核 `f3be04e9…`绑定最终源码。测试SHA `48d1ecb99b0a6b4831b8017dd8e51f9e6195ecfe283925fd9681c40b9bc33572`，AST展开17项本地加20项原runtime用例，共37项；profile/guard custom两项和真实first先于descriptor均已列入，guard state leaf与record-directory shim只作源审覆盖，不称独立运行用例。

本段时r715已prepare **202输入**并正式启动唯一session **36145**（初始chunk `b0f341`），新QA `<SYSTEM_TEMP>/reno-h01-runtime-tail-identity-wgpxe_i6`；尚未收到terminal，**37仅为静态计划数，没有通过/失败或源保持终态结论**。记录器骨架已AST检查而未执行；r716完整runner未prepare/run。主方案追加§17.171运行中记录，AGENTS仍保留§17.170已封终态。完整goal active，原30秒门与全部未完/暂停边界保持，无新增profiling、真实库/正常服务/dist或ACL/Codex设置操作。

### r715三十七项实际通过；r716安装恢复完成后SH第13层node写尾仍超时

上述运行已取得终态：r715唯一session36145／terminal9e68e8／exit0，实际 **37passed537.46秒**，111阶段全passed、202源保持，实际17本地＋20原runtime与最终AST逐函数匹配。真实非lock一次对两次/lock四次及独立读锁、profile SH/EX、nonwriting与下一scope新核、原body自然接线、六custom、晚取消/原期限/first、late分派与expected identity、真实first先于descriptor和原20runtime边界均通过。suite记录器由Root执行exit0/chunk24497c，五日志保存，终态SHA `fe5394b344100588430af7f2a57d8e51d1882b544a198e47cd444016ec2b526c`。

随后r716原完整唯一session30834／terminal2f740d／exit1，实际 **1failed69.15秒**，201源保持。首次_install/internal resume与显式repeat相等、原EX正常退出及helper末DB保全已完成，随后profile SH＋runtime Namespace EX/Open Store/新Budget/IoStack完成。test131 operations的32位key前12层完成，第13层digit4、可见前缀25687b6692f04目录已经创建并核验；node.json暂存writer已private-ready、graph/stream接入、写满raw并以charge=False记长度、flush返回。Stage372的下一effect在fsync前进入Namespace observation begin首stack，held非lock identity首poll原clock比较触OWNED_IO_NAMESPACE_DEADLINE；当前资源kind/name/index未知、随后graph/目录/path-handle未到，不能认定当前资源就是Stage writer。

该新observation尚未登记binding/Context，当前runtime body及r715优化尾未达，失败的未登记scope没有自己的finish；外层原异常清理保留。当前fsync/末writer身份/正常close/返回、read custody/readback/rename/node发布未达，walk未返回，prepare/put/idempotence/最终正常退出和SH末DB/空registry未达；带STAGE_ORIGINAL_CLEANUP_RETAINED及AUTHORITY_STORE_EXIT_VERIFICATION_FAILED。WinError2仅正常fresh分支，teardown不证明清理成功；本次69.15秒跨多个所有权阶段，不推稳定性能或热点。独审b88de75f8ae3f31aba207d6140795cff25456d868886a12f7d873f5785b77d5c确认201源和五日志原bytes。

总记录器最终实际exit0／chunked5750，27实体/六闭包逆AST证据/202与201源/111与3阶段/三版36草稿副本实核，两组十日志保持。两次file-only记录失败（历史事实比错活动路径、原stdout非UTF-8）均在终态写入前，原脚本/说明保全，仅修映射及ASCII bytes断言，无测试重跑。终态data/h01-store-root-terminal-r716.json SHA **f0bfa5bc382b44b7dabe264276d4f34516cd1dcffd2e2d8cefd258ae7ae9ef4f**，全部执行句柄terminal；主方案§17.171及AGENTS同步封存，原证据保持。

下一仅只读核Store metadata入口1334完整stack后_directory_record831的重复；须保预算身份比较及错误优先级、第二逻辑/first/clock/poll、lookup全physical/native与128祖先、不跨metadata预算/Stage effect。Root后续只读评估认为Stage现有helper依赖尚未建立scope、不能提前创建，较窄建议是Store guard-only binding/tail_state合并第二profile物理核，仍保第二held及Store目录全量；尚非实现或完整独审设计，无r717候选/测试。完整goal active，Store/shared后续及全部原未完/暂停保留；用户城市绍兴，当前工序与防水材料品牌、型号仍未定。未改真实库/服务/dist/ACL/Codex设置，无新profiling/扩时/commit/push。

## r717 发布入口第二profile观察：中断恢复与静态设计

本轮恢复先由Root重核r716终态及298项当前事实，一致；上一轮37项真实通过与原完整消费者超时证据保持。中断边界只有r717选集及r718完整runner已保存，候选、测试、plan和claim尚未存在，没有本轮运行。CIM进程查询不可用属于诊断读取失败，没有据此宣称进程终态或重启测试。随后新建本轮静态候选与测试；下述内容只描述这次源码设计与静态审查，不改变§17.171的最近封存执行结果。

目标是Store `_publish_store_metadata_original` 原入口完整stack之后、目录lookup内第二stack的相邻profile物理观察。只修改该入口并新增三个helper，152个其他原Store定义与15个支持模块保持。新入口仍先选择原 `_need` callee，再执行完整首stack和原预算对象is比较，保原异常优先级；只有实际Store custody、Budget record、Namespace acquisition/登记、profile guard与相关原分派的本次局部绑定前后相同，才将第二次profile物理部分与首核共享。随后原tail逻辑、完整held身份核、全部Store目录核、lookup物理/native核及128层祖先限制仍执行。原payload类型/长度、metadata预算、Stage创建和后续effect保持；Stage scope此时尚未建立，不能提前创建来借用Stage helper。

局部guard绑定只用于选择本次重复观察的合并，不形成caller证明、writer许可、scope或跨effect缓存。未知类型、descriptor、自定义helper或late变化保原剩余lookup路径，不重放已完成的首stack。Store、Stage及Budget已有first仍交原checker按原顺序处理。所有原clock、取消、poll、读/SHA、计费与关闭路径保留。

静审指出，只在原Budget调用前后比较对象is还不够：自定义limits相等运算可改变真实marker bytes而不改变对象身份。因此新准入限定本地参与比较的实际scalar/owner类型、Namespace budget started、原Limits/Extension类方法与slot、整数字段及原default limits，unknown比较仍走完整第二profile；原tail helper本体不改。其他静态修正包括原module/class身份先于属性访问、继承dict descriptor与未知metaclass顺序，以及避免经可替换asyncio模块作新Task查询。没有把静态发现写成运行红例。三份草稿均有来源记录：首顺序稿 `0dfa3ca7…` 是由精确逆delta机械重建的builder草稿，非原始现场拷贝；后两份为具名保存的实际静态候选，连同facts共24个文件由源审绑定。

当前已冻结Store SHA `dae1e849df7b3d46a4a50d813c4ba225c8367c99276638ce94625faf3a72f6f7`，manifest SHA `2636ea3697481ec0f4b60f7467c851657a8f259f126c4a893479bd7e7c387de4`。源审 [h01-r717-publish-entry-source-review.json](/<PROJECT_ROOT>/data/h01-r717-publish-entry-source-review.json) SHA `972c5326e9c50a98a20e320947e73beaab9ae1c07652043e19824d54fa8e6863` 无剩余阻止项；Root静态核 [h01-r717-root-source-check.json](/<PROJECT_ROOT>/data/h01-r717-root-source-check.json) SHA `f7c84bb8812a31a32da5811021fba5b1979cebe19d4d5b00e49df5327e2b5ebd` 重核25实体、152旧定义/15模块、原lookup及发布余链AST。这里的SHA绑定当前静态输入，不是未来运行终态。

测试 [test_publish_entry_profile_r717.py](/<PROJECT_ROOT>/tests/test_publish_entry_profile_r717.py) 当前SHA `25484d6fb92aaace775822b5e4a03775d7cac67aff75415cb6f8e877d1a55773`，AST展开25项本地病例，只导入原profile/initialized fixtures，不导入旧test函数。覆盖真实发布入口观察数、实际metadata写读回、预算身份拒绝、custom/late分派、原_need callee时点、三first优先级、第二资源遍历的真实取消/自然期限/held first、真实外部目录、无效payload，以及custom limits相等运算中的真实marker损坏。25仅为静态病例数；本段核对时测试独审待落盘，r717/r718两份plan及claim均不存在，尚未prepare/run，没有本轮通过、性能改善或完整Store完成证据。文件记录器已准备，病例数按最终AST与实际nodeid核验，不假定Windows stdout为UTF-8。主方案及AGENTS最近sealed保持；完整goal与原其余未完/暂停边界保留，无新增profiling、扩时、真实库、正常服务/dist、ACL/Codex设置、commit或push。

### r717测试独审落盘与唯一选集运行中（尚无终态）

上述静态状态之后，测试独审 [h01-r717-publish-entry-test-review.json](/<PROJECT_ROOT>/data/h01-r717-publish-entry-test-review.json) 已实际落盘，SHA `19f39125946492de89aaaa84b3338d53e8004be021d92b191e4aa593c9fe3d91`，无剩余阻止项；源审及测试独审文件均先于Root本次prepare/run。Root随后prepare成功，固定 **202输入**，以原30秒门及最终25项本地病例启动唯一session **61786**（初始chunk `b3b193`），新QA `<SYSTEM_TEMP>/reno-h01-publish-entry-profile-qe0c_ta4`。本段只记录已收到的启动事实，尚无terminal、最终通过/失败或源保持结论，不能将25项静态计划或中间输出当通过数。

r718完整消费者仍须等待本选集终态；记录助手未轮询QA、运行记录器或启动其他测试。主方案新增§17.172运行中记录，AGENTS仍以r716封存终态为准；冻结候选、测试及runner保持，完整goal与原未完/暂停边界不变。

### r717二十五项实际通过与记录器空nodeid前缀修正

上述运行已取得实际终态：唯一session61786／terminal67fc6c／exit0，**25passed300.57秒**，75阶段全passed、202源保持。实际nodeid与最终测试AST逐函数/参数展开相符，25项均为本地case、未导入旧test，每项三phase各一。完整选集所列真实factory/metadata发布读回、profile观察次数、完整第二held与目录核、自定义及晚分派、first优先级、真实取消/原期限与真实fixture marker损坏恢复均按断言通过；这不是完整Store/shared链或稳定性能证明。

首次file-only记录器在五日志已保存后，以错误的“nodeid必须含文件前缀”断言拒绝原pytest正常空prefix，actual exit1／chunk610540且未写terminal；产品、测试及runner无误改。原失败脚本SHA c0746965…逐bytes保全，说明148e68e5…；修正前先核同一QA与保存五日志全部相同。仅允许原空文件prefix，仍严格核prepared TEST、冻结源、最终AST展开与三phase，使用--already-copied后实际exit0／chunk4e486e。选集终态 `data/h01-publish-entry-profile-suite-terminal-r717.json` SHA `fbc670b3ef4b5e67ea3dbca9802303071d70d2d930ade649d4d1957c17230960`，包含三草稿来源及失败记录器证据；没有日志重写或测试重跑。r718后续原完整结果另记，主方案§17.172更新实际绿色，AGENTS暂保r716封存完整结果。

### r718安装恢复完成后SH第14层read custody首payload读前超时

r717真实绿色封存后，Root另核57具名fact并prepare201原输入，r718唯一session74424／terminalc88061／exit1实际 **1failed64.54秒**、201源保持。首次install/internal resume、repeat返回相等、原EX正常退出与helper末DB保全完成；profile SH＋runtime Namespace EX、Store open/Budget/IoStack已入。operations前13层完成，第14层digit2、前缀fd119dcca61f42的目录已创建核验，node暂存writer写满raw/charge=False长度、flush/fsync/末identity/正常native关闭及wrapper退役均返回。独立read custody已native打开、private/graph/stream接入到read-custody phase，身份、长度和原writer device/inode相等检查已过；其lease尚未返回。Stage349进入首次payload读取，305前置effect失败，尚未seek/read/count。

当前Namespace begin已成功登记reading scope并完成原entry全部核验；正文owner门后，4864首stack内非lock资源的最后runtime目录verify调用原Budget产生 **NAMESPACE_DEADLINE**。该资源首poll/graph/首目录及适用private、path/handle身份相等、目录identity比较已过，不是首poll或既存first重抛；kind/name/index未知，不认作Stage read-custody资源。原异常finish因scope已登记而尝试独立尾核；本次body完整read/hash与r715 writing-tail未达。当前payload读取/lease返回、rename/node发布、walk返回/prepare/put和末正常退出/SH断言未达，保两cleanup附注，无独立Namespace cleanup附注；不推清理成功、稳定性能或热点因果。

独审3a7a8f7037bce4904062508d68a28ae9373b6d607a2a6c970e5398c4b202a434核201源与五日志原bytes。总文件记录器实际exit0／chunk2b0c96，25冻结实体/202与201源/75与3phase/三草稿和失败记录器副本均核，十日志保持，两执行句柄terminal；Root终态 `data/h01-store-root-terminal-r718.json` SHA `e0fcec79788e5ee60bbdb86b1976c4c7e8784f61effe2272c7278b9b7fb373b2`，主方案§17.172及AGENTS同步。

唯一下一源码问题为同一runtime observation中begin首full与body首stack的重复，尚未证明可安全共享额外采样。两次核验之间有graph/ledger、Context/registry、真实目录、完整guard dir/files/dir及末runtime gate，不能当相邻或直接沿用r715尾方案；完整begin、owner/SH-EX、全部late first/clock/cancel/poll/资源图、body实读/hash/收费/lock与独立正常异常finish须保，unknown只剩余原链不重放。4469是期限门而非已证重复IO；仅只读评估，无r719候选/实现/测试，无新增profiling或真实库/服务/dist/ACL/Codex设置操作。完整goal及所有原未完/暂停范围继续保持。

## r719 正文首stack与紧接完整实读的读前身份采样：冻结静态设计

本轮Root重核r718终态与209项当前事实一致。后续源码评估没有实现前述跨begin/body的非相邻合并，而选择原runtime正文中首完整stack与**紧接的full-read sweep**：consumer自己完成首stack，只在它成功后合并当前固定非lock filehold实读前的第一次重复path/handle物理采样。这个新的共享范围不跨begin，也不把r718的Budget失败门当作冗余IO。原begin、首stack及各clock/取消门完整保持；当前r718失败的body首stack位置仍须由原门检查。

仅修改Namespace `_verify_closed_namespace_owner_runtime_body_original` 的两处相邻调用为共同helper，新增三个定义；219个其他Namespace定义、Held及13个支持模块保持。Store与observer只适配真实Namespace SHA，四consumer仅包名和实际SHA适配。四个局部闭包由原Held `_verify_identity_record`、`_read_owned`、`_verify_file_record`、`_verify_scope_original` 机械抽取，逐体逆AST及原body两调用逆替保持；只有 `_read_owned` 的首次identity路由参与合并，读后第二次真实identity原样执行。原read循环、seek/read方法、真实byte计数、SHA计算和payload/SHA比较、原收费分支、lock content=True读核及独立正常/异常finish全部保留。

固定路径以本次真实scope/Context、registry、owner、ordered资源及原字段的局部前后绑定选择共享，不接caller checked/proof、不造scope、不借上次effect。每个资源再次检查准入；unknown及late变化只选择原剩余scope/file/read/identity链，不重放首stack、已读资源或本次已发生的read。固定准入覆盖read/seek映射及每条目、hashlib/sha256、HeldRead实际类/slot/方法和直接scope/file/read/identity/private helper。局部比较投影只排除每resource的 `observed_bytes`（原slot22、组内offset23），以允许本次实读自然累积；实际counter不重置、不绕过，其余payload/SHA/size/limit/lease/buffer/图/预期身份/模式/lock/失败字段都保留。

独审修正一项静态类型准入顺序缺口：初稿先调用已有tail dispatch，再证明Namespace类身份，而旧dispatch会读取类字典。最终先固定六个实际类、原Namespace Budget与ClosedCancellation类型，再调用保持不变的旧dispatch，避免新准入抢在原首checker之前触发未知类型属性错误。初稿Namespace5a31…及14个实际保存实体、facts清单原样保在 `draft-before-class-admission-order`；源审共绑定含facts自身的15 rows，不称运行红例。

当前冻结Namespace SHA `1cf99b186b8193725abd4a6fda2bfa4a3c7062efaf11730f9ec38cc072933cbd`，manifest `963a8bf227a437c6472bd55733ad5f1b64fe7f80c2b2ef515239da080bd64b0f`，20源＋7artifact共27实体。源审 `data/h01-r719-runtime-preread-source-review.json` SHA `cc5e5c27ff9bdbdeca2df7a0190a86b94465cbe33e8589228f78daa4a2fe889b` 无剩余阻止项；Root静态核 `data/h01-r719-root-source-check.json` SHA `91f9a67b6ff155b23664d995b3f61d348055a1f480d62f70ff2aa7cc3bd34532` 复核27实体、builder validation与manifest一致、四闭包逆AST及实际测试AST。

最终测试当前25924 bytes、SHA `dfa9c7c18a6ed215c6b4228e5cbe250bdfb4d9a3d89633ffc16dcf4c28c61002`，**26项本地＋原runtime20项＝46项计划**。计划覆盖profile SH/EX的非lock采样减少与lock原核、真实read/hash/counter与每次新scope、原body自然接线、12类custom和多种late剩余链、真实取消/原时钟/held first、SHA及expected身份错误、已有first先于descriptor，并包含只在真read之后改变本次native身份采样返回的尾核拒绝测试；该注入不修改文件或expected身份。原15个test函数按其AST参数展开为20项。此处仅记录冻结/静态范围，46不是通过数；prepare/run与终态须以之后的实际工具证据记录。r719/r720记录器只准备未执行，AGENTS保持r718完整消费者终态，主方案§17.173单列当前静态阶段，完整goal与原未完/暂停边界保持。

### r719双独审已先于prepare落盘，46项选集运行中

上述冻结静态阶段后，测试独审 `data/h01-r719-runtime-preread-test-review.json` SHA `44c31b0e62c36f61e11ed1c93df3ac2fe6e5f7f44dbb16f740d2117250f51cec` 实际落盘，0剩余阻止项；源和测试两review文件确在Root本次prepare/run前。Root prepare实际exit0／chunk98df9b，固定202输入；唯一session55065（初始chunk3a6d54）已启动，新QA `<SYSTEM_TEMP>/reno-h01-runtime-preread-identity-_q8h0cvc`。这里没有terminal或通过/失败/源保持终态，46仍是静态计划，不能用点数替代结果。

记录助手只读审查记录脚本与最终review/manifest结构，不轮询QA、不启动或重启测试，也未执行记录器；r720完整消费者须等待r719实际终态。主方案§17.173记录本次运行中，AGENTS保持r718封存完整消费者，冻结输入与原未完/暂停边界保持。

### r719四十六项实际通过与选集封存

上述唯一运行已terminal：session55065／terminal910c65／exit0，实际 **46passed621.66秒**，138阶段全passed、202源保持。最终本地26项与明确导入的原runtime20项逐函数/参数展开与实际nodeid一致，每项三phase各一。真实首stack＋读前身份采样、原read/hash/counter与末identity/lock，fresh scope、body自然接线、custom/late剩余链、原first/取消/期限、SHA与预期身份、真read后的physical尾核拒绝及全部原runtime边界均按断言通过，不外推完整Store或稳定性能。

suite文件记录器实际exit0／chunkea7fd9，27冻结实体、202源、138phase、最终双源AST与review逐fact保持，14草稿保存实体和facts来源实核，五日志首次按原bytes保存/核同；无测试重跑或记录失败。终态 `data/h01-runtime-preread-identity-suite-terminal-r719.json` SHA `22178a382a135b70530cda2d8c0e22007e41eec86e1673a648d9c58e6a070035`。主方案§17.173更新实际绿色，r720完整消费者后续结果另记，AGENTS暂保r718完整终态；完整goal及全部原未完/暂停范围保持。

随后Root重核suite终态及46个具名当前fact、r720 runner SHA保持，prepare201输入实际exit0／chunka7c90c，唯一原完整run session69855（初始chunkb612eb）已启动，新QA `<SYSTEM_TEMP>/reno-h01-store-7tai1yd4`。这是启动事实，尚无r720终态；Root独占跟踪，不写full通过/失败结论，不重启测试，AGENTS仍r718。

### r720原完整仍失败：第13层writer首写前、未登记begin的lock首poll期限门

上述同一次run已terminal，session69855／terminal6f90a7／exit1，实际 **1failed69.31秒**，201源保持。安装helper的首次install/internal resume/repeat相等、原EX正常退出及DB保全全部返回；SH profile＋runtime Namespace EX/open/Budget/IoStack完成。operations前12层完成，第13层digit4、前缀8f5f64798f064已建并核目录。当前node暂存writer的native open/转fd/fstat/private/graph/stream和writer-private-ready已返，Stage363的_open_resource完成；364首次pre-write effect失败，后续writer graph、payload写/observe/flush/fsync/末identity/正常close/返回尚未发生，read custody和node发布未达。

新Namespace begin首stack held sweep明确选择lock(content=False)，在identity首poll的原held-clock比较新建 **OWNED_IO_NAMESPACE_DEADLINE**；取消/clock合同已过，graph/private/目录/path-handle/overlap未到，锁内容读取未选，name/index未知，非Stage writer。当前begin没有graph/ledger、binding/Context登记，故该scope无自己的finish；外层原异常清理保。当前body及r719/r715优化均未入，不能移用r718的已登记reading/非lock尾目录语义。walk/prepare/put/末退出未达，两cleanup附注与暂存保，不以teardown证明成功清理；比r718更早且key不同，不推稳定性能或根因。

独审99e4e63a0446cb4c838650907c12701e14ccb335e8f5442754f94f768cc8ee5b核201源及五日志原bytes；总文件记录器实际exit0／chunk874894，27实体/202与201源/138与3phase/26＋20AST及14草稿副本核，十日志保持、两句柄terminal。终态 `data/h01-store-root-terminal-r720.json` SHA `60f9287c871921368c8681161c107822c00deca5ccf4b1a24144344ebbf8956c`，主方案§17.173与AGENTS同步。

下一仍只有源码评估：独审提出runtime begin首stack完整guard与后续plan完整物理块，二者非相邻且本次后块未达，尚无可安全共享结论；Root另评估单次Store目录sweep祖先native重复，也未选实现/候选。实际guard/数据库/held/目录、owner/SH-EX、first/clock/cancel、登记顺序、真read/hash/费用及正常异常finish须保，不借旧effect/不提前scope/不重放；没有r721或新profiling。完整goal及全部原未完/暂停范围保持。

## r721 Store单次目录遍历的真实叶链覆盖：冻结静态设计

Root已重核r720终态与411项输入/事实。后续选择Store一次 `_store_directories_verify_original` 内的祖先native重复，未实施Namespace begin跨段共享。原逐个Store acquired目录调用递归checker会在同一条链上反复从中间目录重走祖先；新规划只选本次真实图的末叶，各末叶仍调用原完整递归checker。单链覆盖每个实际record一次，分叉的不同叶链允许重复核验共享父节点，不声称整个分叉图每record只核一次。

候选只改原Store目录sweep并增加三个helper，155个其他Store定义及Namespace/held/Stage等15支持模块保持，四consumer仅包名/真实Store SHA适配。前置原六statement和后置原四statement的AST不改；先保原custody/registry/execution owner/Store first/Stage first，再可选规划，原_STORE_DIRECTORY_SWEEP callee在规划前选定。物理调用之后先执行完整原post tail，再做新增after-graph比较，不让新getter或图变化抢在原first/Stagefirst前，也不重复调用Stage first getter。

规划只读当前真实Store directories tuple和Namespace登记的全部parent record，包含不在Store tuple内的祖先。节点按实际record对象身份覆盖，不按相同path或fd数值合并；同路径独立登记的records分别实核。原Directory/record类型、descriptor、handle/identity字段、owner及expected native必须固定；unknown、duplicate tuple、缺字段、非法/cyclic图或高层分派变化返回None，走原完整sweep及原错误，不将Namespace-only的唯一输入合同强加给Store。每个选择叶仍通过原sweep、新path-stat字典和完整递归核验，所有native、结构、expected身份与parent链门保持。末尾核整个图、所有parent链与叶选择相同；late graph/plan/match变化拒绝而不重新采样。

本变更按§17.113调整同一次完整核验内的重复物理采样，多个同时存在的物理故障可能因观察顺序不同而先检出不同故障；已记录first、原traceback和cleanup优先级保持。未改普通Budget/stack/close、128祖先lookup、任何runtime body/read/hash/charge/lock/clock/取消，不跨sweep/effect缓存，不建Namespace stack或caller proof。源码本版一次独审无阻止项；重复Store tuple回原路径、after-graph在完整post之后、tuple外真实祖先覆盖均在构造前纳入设计，没有失败源码草稿或运行红例。

冻结Store SHA `70535e0e91b8a955c398ec6c92a47b9f1078f62e3141da2ee5c9e922ae4a4384`，manifest `1b17b9698af0505fb8fa4fb2dbbcad6414f0ab0ada4de32c9815f59df7f54956`，20源＋5artifact共25实体。源审 `data/h01-r721-store-directory-forest-source-review.json` SHA `d22a401acb675c0960c3f18c57443547bf3cb418fc8a49c3b270aedaaab654ff` 在freeze之前，0剩余阻止项；Root静态核 `data/h01-r721-root-source-check.json` SHA `14b4c9dd49059bb93d797e24bd22bb4d171b35564bdc253852e21e80e70cc57d` 重核25实体、builder validation、155定义/15模块和原prefix/post/unknown循环/Namespace14字段与leaf选择的AST保持。

最终测试 `tests/test_store_directory_forest_r721.py` 当前18796 bytes，SHA `625172ffebf88f5c731b2e1c3aa88bfadbf69d43d2588c2c1ef36acaa098df6c`，**19项本地计划**，仅导入profile/initialized fixtures，0旧test函数。live_entry工厂和一例真实metadata publication由r717机械复制，逆AST相等；其余计划覆盖单链、分叉、同path独立record、tuple外parent、重复tuple原路径、三custom/三expected native错误、原first/Stagefirst先于after-graph，以及五late图变化。first用例静审后加强真实failure-close及关闭后的首错保留断言，未跑过旧红例。Root静核时r721/r722均未prepare或started；本段仍只有静态证据，测试独审和实际执行按后续证据记录。没有19项通过、完整Store/稳定性能证明；AGENTS保持r720封存，主方案§17.174单列本次静态阶段，完整goal及所有原未完/暂停保持。

### r721双独审先于prepare，19项选集运行中

测试独审 `data/h01-r721-store-directory-forest-test-review.json` SHA `163fca525a2787254e8af4dcf6a256040266ac76da38070ce7c79b224b1e14d9` 随后落盘，0剩余阻止项；源/test两review文件均实际先于Root prepare。prepare exit0／chunk536133固定202输入，唯一run session19459（初始chunkcab13c）正式启动，新QA `<SYSTEM_TEMP>/reno-h01-store-directory-forest-8hqtqt20`。当前没有terminal，19不是通过数，也不声明运行后源保持；Root独占跟踪且不重启。r722未prepare/run，记录器未执行，AGENTS保r720，主方案§17.174记运行中；原全部未完/暂停范围保持。

### r721十九项实际通过与选集封存

上述唯一session19459现已terminalcfb8bf／exit0，实际 **19passed281.55秒**、57phase全passed、202源保持。实际19个本地病例与最终AST逐函数/参数展开相同，0旧test导入，每项三phase各一。真实单链/分叉、同路径独立登记与tuple外parent、duplicate/custom、每record expected native错误、原first/Stagefirst在after-graph之前及failure-close、五late图变化和原真实publication均按断言通过，不外推完整Store/shared流程或稳定性能。

suite文件记录器实际exit0／chunk4aa3b2，核25实体/202源/57phase/本地AST和全部review事实，五日志首次逐bytes保存并与原QA相同；没有失败源码草稿、记录错误或测试重跑。选集终态 `data/h01-store-directory-forest-suite-terminal-r721.json` SHA `38a501b9a75d706bf9c12c48ce255a6e0eb47e96c064f9996736db24d56dde3b`。主方案§17.174更新实际绿色，r722原完整结果另记，AGENTS暂保r720，完整goal及原所有未完/暂停保持。

随后Root再核suite SHA、31具名当前fact和r722 runner SHA保持，prepare201输入实际exit0／chunk23c3b1，原完整唯一run session61907（初始chunkdb7979）已启动，新QA `<SYSTEM_TEMP>/reno-h01-store-gtiwplie`。仅记录启动，无r722完整终态；Root独占跟踪，不推通过或失败、不重启，AGENTS保持r720。

### r722显式repeat正常退出首body的Pin setter首held poll仍超时

上述同一次run实际terminal28ef80／session61907／exit1，**1failed44.69秒**，201源保持。首次install及内部resume/Store正常退出已返回，helper原Namespace核验与init bytes读回完成。显式repeat正文两完整body和return/header SHA求值已返；正常退出初始Store栈及全部pin.verify循环已返。退出第一个writing=False body完成Namespace核验、Store2 lock/journal handle和三目录private/identity后，某state Pin预算setter的强制_pin_record首Store栈进入held非lock identity，在首poll新建 **OWNED_IO_NAMESPACE_DEADLINE**；取消/clock合同已过，当前物理观察未开始。

当前外层Pin与内层held资源名称/索引未知且可能不同。setter原record未返，目标预算核/owner-deadline/rebind/最终赋值及此循环当前Pin.verify未到；本次失败栈的r721forest在held后，所以尚未进入。不能移植旧begin/reading scope语义，前一Namespace核验已返回。本次正常close继续原剩余closers与closed/retained/Budget first后，在1053抛原首错；不能由此或teardown断言所有native关闭成功。退出第一body未返、第二native body未入，repeat未返回，相等/末helper DB/外层正常退出和全部SH链未达；无旧两cleanup附注，不据44.69秒或更早失败推性能归因。

独审07c3356c9933bc1016d8f274be9e86f4335a9744f458175e30049d6a5efa3b0b核201源与五日志原bytes；总文件记录器实际exit0／chunk852a30，25实体/202与201源/57与3phase/19本地AST及全部review实核，十日志保持、两句柄terminal。Root终态 `data/h01-store-root-terminal-r722.json` SHA `f3a2bc3f655d6b582c1017e503b6c86a7718fcc4306b82f280b5cad24b77689a`，主方案§17.174与AGENTS同步。

后继仅仅读forest在空/单record或acquired roots互不为祖先时不能减少根列表的新增规划成本，尚无图规模/耗时证据，也未选产品变更。Root另安排r723受限新profiling提案静态准备，不是产品候选；r646旧初始化采样已用完且未到Store，不能证明当前热点。新采样当前没有新增授权、没有prepare/run，必须另得明确许可，其他暂停项保持。完整goal与全部原未完保留，无新profiling执行/真实库/服务/dist/ACL/Codex设置或commit/push。

## r723限定诊断提案已静态就绪，新增一次采样请求待明确授权

主方案§17.175记录当前提案：runner SHA `7aff18bb912397338e3ba1874e34a7a71b60352aa152723aa61ea629c9a4b303`、静态对照 `79f00df88f23c5384b95dab269dbb0e997d278b091371627f29913fe06dff1b4`，独审 `data/h01-r723-profile-proposal-review.json` SHA `fdae643e61b9b1e26e08a66efe3ee929728cdefc2aade5a24018bd453a426c17` 无剩余代码阻止项，11当前fact已核同。固定原完整测试/200源/30秒门，仅替runner，拟一个新TEMP child观测call阶段函数次数与耗时，不采集参数/locals；不是产品候选或运行结果。

§17.146第3598行原文“普通后继修复和无profiling验证在当前执行授权内；没有第二次profiling授权”限定此前单次许可，非平台禁令/本轮审批拒绝。Root新增一次采样问题仍待用户明确答复；r646旧初始化未到Store，非当前热点证据。PLAN/claim均无，未prepare/run/新QA/产品或profiler导入/采样，现有runtime句柄全terminal；未来覆盖不足也不自动retry/扩时/删初始化。整体goal active未complete、原全部剩余/暂停保持，r722结果不改。

## r723新增一次许可已执行并封存（2026-10-09）

用户“允许，继续”回复上面的待答问题；Root复核审定脚本及原201输入后prepare/run唯一一次，session79530／terminalb76119／exit1，**1 failed／46.52秒**。原30秒、完整选集及真实读写/权限/计费/退出保持；只在call阶段记录函数名、次数和耗时，不记录参数/locals/caller graph。201来源保持、8日志原/副本逐bytes相同，独审SHA `9fa514786a51d1f696be352ce106b98b63ff17783bffd688470f632db18d3930`，file-only seal chunk5117c9／exit0。终态 `data/h01-store-profile-terminal-r723.json` SHA `16db2678b653035a48510bf3a82541a27371481cc232251b6f2aaf6b2aaa6b08`；本次新增许可已消耗，无重跑。

失败在首次install的internal resume，Store1703核验进入Namespace begin最后runtime check，scope已登记/reading但尚未yield；原Budget4469→226触NAMESPACE_DEADLINE，异常finish尝试。runtime正文/header发布/acquire_v2/install返回/repeat/SH均未到。不是r722普通执行边界，也不据位置推稳定性能。

记录self44.7859418秒中，nt.stat283636次/self20.1334933秒占44.95%。Namespace graph31349次/self2.0254375秒但本身无stat/native；Store forest800次/累计0.4550653秒。不能从总stat统计推具体caller，也不加总嵌套累计耗时。空/单根短路会取消新增aftergraph拒绝，未实施。单次graph祖先尾链复用尚有getter/eq/原地变更准入问题，未选r724候选。保全部before/aftergraph、原期限、完整正文/first/cleanup。完整goal与全部原未完保持；另在源窗口封存后独立推进原计划未完的核对单本人摘要，不授H01写权或真实分享发布。
