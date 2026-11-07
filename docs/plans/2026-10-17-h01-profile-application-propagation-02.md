# 应用层 profile 来源传递与线程边界实施方案

2026-10-17；r1026 DESIGN02。承接 r1025 SOURCE01，落实 profile-route-custody 的 Task6；Task7 的终止、pending 和激活门继续独立保留。

## 目标与已核事实

把每个实际配置创建点取得的同一个 profile 和构造 owner 成对传到其实际消费者。应用长期持有的是构造来源；每次严格 owned 操作另建自己的 custody。不同实例即使 identity 相等也不能借用构造 owner。

r1024 十份源调查给出19个句法 site、8种持有点。r1026 再核完整当前应用源、四个 source-control 消费者、媒体响应及 r1025 十二候选；调查仅覆盖清单文件，不排除其他动态/别名调用。原普通同步接口没有调用 enrollment 三个 owned 主体。连接来源参数不能被描述成已启用 H01 写入。

原 storage_profile_origin 的 create_owner/owner_facts 使用同一 RLock；construct 在短锁段外执行 class/Path/Context，再在锁内发布实际返回。owner_facts 可以从别的线程读取已完成记录；构造 thread/task 是历史，绝不是当前业务权限。getter 的45个导出键不含 context_token，禁止再造这个合同键。读取终态不证明任意并发篡改期间的完整稳定性；scope 原有持续 fresh 门保持。

原 custody 的 _new_operation/_validate/_loan/Call 与多个表操作没有线程锁或跨线程所有权移交协议。不能把 async 请求中创建的 root lease 当作可以并发使用的请求上下文。原 control._OWNER、scope Token、EX 和资源拥有者仍按实际操作线程/task工作。

## 两类持有者

新增私有候选模块 `reno/storage_profile_route.py`，集中只携带来源的 `_ProfileSource` 和 DTO 外置 `_ProfileEnvelope`，避免十多个调用者各造一种松散 tuple。模块不登记全局 profile→owner 表，不持强全局操作目录，不创建 ContextVar，不取得资源、不打开库、不创建 custody、不调用模型或外部平台。

`_ProfileSource` 是 frozen slots、repr=False 的内存对象，只有原 profile/原 owner。它不是许可。调用者必须按真实顺序在局部先执行 `owner = storage_admin._new_profile_owner()`，再执行 `profile = storage_admin._profile_with_origin(owner)`，最后 `_pack_source(profile, owner)`。不把这三步藏到“只返回 profile”的 helper；构造失败时调用者仍持有原 owner，原异常/tb保持。未完成/出错的 owner 不封成成功 source。

`_check_source(source, profile)` 先检查 exact 类型及同一 profile，再调用固定捕获的原 owner getter，核 owner/profile/constructor_result 同一实际对象、construct/constructor/post-init完成、无 pending/first/final/mapped/capture/constructor/cleanup错误、Context set/reset实际返回及效果。只用真实导出键。它不重复实现48调用和九槽 Path认证；那些仍由原 scope消费，不能把 source封套当作已经做过这些检查。

静态 factory 捕获原getter及其既有递归函数/code/default/kwdefault/closure/cell目录，在调用前验证；不能认证旧函数却改调模块当前同名函数。不得从一个同identity profile或当前配置查 owner，也不得重新构造profile补签。此目录不宣称认证全部 globals/CPython/C。source自身可被普通Python反射修改的风险由每次实际getter及scope身份核对拒绝，不能以 frozen 宣称安全隔离。

`_ProfileEnvelope(dto, source)` 保存原 DTO 和对应 source；每次解包要求 `dto.profile is source.profile`，不改变 DTO 字段、类型、repr、JSON或普通 copy/pickle 行为。封套本身不提供JSON/to_dict/迭代投影，明确拒绝pickle及copy/deepcopy，不能把内存来源送入任务payload或网络。保留调用方的局部 owner/source 即可处理封套构造失败，不重建历史。

所有新私有参数统一用本模块唯一 `_OMITTED`。没有已选择source的holder时，省略保持原普通路径；已选择source的嵌套holder须按后文矩阵保持原source。显式 None、坏source、其他实例同identity、缺失已持有字段都拒绝，不能降级。普通 DTO/copy/unpickle 仍按现有 identity 语义工作；只有明确选入的 source 路径要求 object identity。已选择source的对象后续直接读取其配对字段，不用 `getattr(..., omitted)` 隐藏字段丢失。

## 操作 custody 的位置

来源跨线程时只传 source及原profile，不传创建线程的scope/Token/EX/group/root lease。实际严格 owned 调用在目标线程、目标task内建立新 `_new_operation()` root lease；同一时刻同一custody只在该操作线程使用。不同请求/heartbeat/job不得共用root；不同严格操作可以持同一个已完成构造source，但各自使用不同root及budget/group。

同一操作内部若需要跨线程迁移，当前明确拒绝，必须先设计/验证原借用集合、全部表与Context/资源的迁移协议；不能凭 GIL 或单次 dict 原子性宣称已支持。来源读取与操作custody并发是两种不同合同。

Task6不在普通只读/同步路径创建闲置root。已有两个owned wrappers、Store/joint三个主体的真实调用者，实际调用时显式解包同source.owner、持新root并传 `_profile_owner`/`_custody_lease`；不得新增HTTP写接口。当前清单中不存在的应用owned调用者不虚构。本阶段不得以一个无人调用的新adapter作为完成证明。

最后root的释放仍被 `_finish_operation` 拒绝。只有Task7具名实现并通过最后借用、unknown债务、pending容量/恢复/最终释放后，才允许完整严格route激活；Task6的暂存source不能充当pending物理债务持有者。

## 文件级接线

### A：请求及线程池

`app/sync_api.py` 的 source_continuity_status/source_register/source_dispose/source_readmit 四点，在当前async调用点按三步创建原owner/profile/source，`run_in_threadpool` 显式传同profile和私有 `_profile_source`。线程池侧 status/register/dispose/readmit 的实际函数签名必须同步，不用partial猴子补丁或重新读取config替换。v1/v2分派、请求规范化、期限和已有授权流程保持。

`reno/sync_source_continuity_admin.py` 的 status→_read_checked→_read逐层传source；preview/activate普通省略保持。`reno/sync_source_continuity_service.py` 的 register/dispose→_dispose/writer，以及 `sync_source_readmission.py:readmit` 和 `sync_source_readmit_sql2.py:dispose/readmit` 的实际委托分支逐层显式传同source。writer及read入口在原资源获取前核source；必要内部传递不会把普通shared或source-writer变成enrollment owned EX。原交易、SQLite authorizer、外部锁、恢复及业务授权保持。

`_registered_control_transaction` 和 `_protected_connection` 原本在同步函数内创建profile；分别原地建立独立source并在现有连接/业务生命周期持有，不把四个async请求source拿来复用。只改实际需要的私有签名和调用，列出每个委托site；未递达的分支不能报完成。

`app/api.py:frame_at/video_file` 各创建自己的source。frame_at保持至原连接和帧字节返回完成。video_file显式交 `app/storage_response.py:media_response/protected_media_path`；Range字节响应由同步调用持有到原stream.close尝试，整文件响应交给原OpenFileResponse和其_chunks迭代器的私有source字段/参数，响应显式close路径继续持有至原close尝试。source不进headers/body。

保现有weakref.finalize不捕获response或source，不给全局finalizer目录新增owner强根。finalizer兜底不是来源/物理关闭回执，不能据此证明pending结束或GC。完整取消/send异常/构造失败关闭由原响应顺序处理；若未来严格流资源产生unknown关闭债务，必须交Task7恢复owner，不能以本段纯来源持有替代。

### B：worker和heartbeat

`reno/worker.py:process_pending` 在原一次profile创建处取得其独立source；保持database resolve、进程锁、租约、跟随循环、busy读取重试和停止行为。heartbeat使用显式线程参数持同profile/source；仅核来源和现有业务租约，不获任何主线程custody。原join(timeout=1)仍保持，join返回不等于线程已退出；线程实际参数保证它尚活时source仍有持有者，不提前给出终止/GC结论。

`_execution(..., profile=None, _profile_source=_OMITTED)` 配对保存/安装/恢复 `_current.profile` 与 `_current.profile_source`，包括正常job和review_only两个实际with点、嵌套和BaseException。校验在改变TLS之前执行。`execution_context()`原字典字段完全保持，新增私有 `_execution_profile_source()` 只在内部取得显式已安装封套，不进JSON、持久job、日志或外部模型adapter。

dispatch调用run_cycle时显式交原worker source。其余分发链若还没有source参数，只能记为后续site，不能在summary称全worker下游已经消费。owner校验不能替代原_stop/current-owner/continuation/generation/租约检查。

### C：Cycle和StateStore

`reno/sync_agent.py` 原 `_Cycle.storage_profile`、run_cycle/ resume_no_resend给StateStore的两个profile创建点，仍分别创建自己的原owner/source。它们可能与worker或彼此同identity，但实例不同；不合并创建、不共借owner、不改变原配置读取次数/顺序。Cycle持 `_storage_profile_source`，StateStore增加私有 `_profile_source` 参数/字段，每次选中严格来源的连接、reference_operation/reference_lifetime及原关键消费者入口重新核同原pair。

run_cycle收到worker source仅用于配对保持原worker上下文；不能赋给新建Cycle/Store profile。Cycle实际storage_connection继续用Cycle自己的pair；StateStore使用自己的pair。原StateStore RLock只约束其业务数据，不能借它宣称custody跨线程安全。其heartbeat共享同一已完成profile/source作为只读来源，不共享操作root。

普通StateStore(profile=None/原profile省略source)保持。严来源持有后丢字段/换profile/None source必须拒绝，不以当前profile再pack或从worker identity找owner。StateStore与Cycle的错误路径及heartbeat closure继续持实际对象，不清tb求GC。

### D：frozen DTO外置封套

`reno/model_scopes.py` 保Execution定义；私有当前执行容器持 `(原Execution, source或omitted)` 的明确frame，current_execution仍返回原Execution。execution的显式source与嵌套原frame核同，call实际解包该frame传入ModelScope会话；不能通过复制Context给别的线程授操作custody。普通省略source继续原identity比较，strict source只认同实例。

`reno/model_budget.py` 保ModelScope定义；scope/_Session另持外置envelope，传输前仍用原DTO业务校验和原预算准入。`reno/source_budget.py` 保SourceScope定义；source_scope/home_source_scope/scope和_Session明确持封套，home binding另有同identity的不同profile时不得借worker source；必须接收该实际profile的source，否则仅普通省略路径、严格请求拒绝。

`reno/source_budget.py:_prepare` 在真实runner.prepare返回后封存 `(原PreparedSource, source)`；run_source的实际preflight/verifier/run调用逐次显式解包，原runner仍收到exact PreparedSource，原policy/resume/verified_files/argv不变。`reno/source_runner.py:prepare/run` 私有source参数只做来源配对并由实际调用者显式持有；DTO本身不嵌source，直接普通prepare返回类型不变。子进程参数、环境、序列化控制和结果不带owner。新封套不能绕过runner原SEAL、路径检查和能力门。

原四DTO各自的所有定义/构造site和选中调用点必须列AST清单；模型会话传递不等于真实模型调用通过。需要改的实际直接caller都纳入候选清单，不能只给callee增参数却遗留正常caller为implicit ambient source。

## 实施批次与准入

按依赖拆为6A来源封套+worker实际持有、6B API线程池及版本分派、6C Cycle/StateStore、6D DTO/runner。先做6A，后续批次各保存完整组合；拆批不是删除其他site。每批候选仅写data，不安装正式reno/app或c973。每批都要完整parent/candidate/inverse/差异、固定调用/类目录及普通legacy AST变化说明。

6A先继承r1025十二完整文件，增加route模块和worker候选；dispatch函数逐AST保持原样，不向尚未修改的run_cycle传新参数。6C才同步run_cycle签名及dispatch/review两个直接caller。接线证据必须来自原process_pending/_execution两个with点及heartbeat实际目标，不接受只测helper。原下游仍未接线时逐项标待，不因6A通过宣布Task6完成。

模型清单至少包括：构造失败原owner/错误/tb；未claimed/失败/None/不同实例同identity拒绝；getter导出真实合同及code/closure篡改先拒绝；async创建到真实threadpool同pair；worker两个入口/嵌套恢复/BaseException/heartbeat尚活；Cycle/Store独立同identity对象；四DTO未变与实际封套解包；copied Context只有来源不授custody；两个并行操作新root分离；视频Range/整文件/发送异常source寿命；已丢字段不得降级；普通copy/pickle和JSON没有owner。6A只运行相应子集，其他明确待后续。

先两设计审；完整新候选两源码审后，再审六脚本/实际输入并取新claim单次运行。最终检查所选原Python来源与合成IO/EX/clock分列。未得到真实运行结果不声称真实threadpool/async/GC/EX/SQL/native/Store或产品通过。失败只重检必要失败ID，保旧红及审漏。原111/94/r919和完整产品门不被这些模型替代。

保 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest children，新批max1；保原Popen/finally/kill-wait/raw-first，不扩超时、不profiling/frames/locals/停点或定点计时诊断。

## 交付边界和后续

本r1026是来源调查与设计，候选compile/import/执行、模型、SQL/native/诊断/产品运行均0。后续6A–D实现/运行、Task7最后借用/pending容量/恢复/最终释放、完整物理/marker/费用/容量/完成图/final/正常父退出/恢复、原111与94/r919、Store/source_closed/live witness/写权、H02–H06/永久分享及完整Goal仍未完。

canonical/c973本段apply0，历史正式累计11，产品pass0。普通开发与同一只读代理复审已有授权，不另问、不新增代理。正常库、服务/dist、既有ACL/settings、平台和Git保持；r920/r964已耗不重跑，r967待原专门问题不执行不重问，P06历史0/7无备份不再追问。回滚是保留未安装候选/旧红和前驱字节，不覆盖正式树。


## 修订02：实际方法、首批边界与嵌套frame

原01与Root01红保。R001：StateStore实际只有reference_operation/reference_lifetime；前者持短业务RLock及_reference_frame，后者只在进入保护时短持RLock，yield期间允许heartbeat。不存在long_reference_lifetime。R002：PreparedSource的三处_prepare调用与verifier/runner.run均在run_source内；不存在_new_attempt。Root01 remedy又误写_invoke，同样不作为来源依据；这里纠正为已核actual run_source。

R003：6A仅十四完整候选（12前驱+原worker窄改+新route模块）。process_pending原创建点、两个_execution with、_wait_for_process_lock、_profile_matches及实际heartbeat Thread kwargs接线。dispatch整函数AST与原父相同；6A不向run_cycle/导入/个人镜像/pipeline等尚无source参数的下游传新参数。source由worker真实holder继续持有，称来源持有接线，不称这些下游消费已完成。6C同步run_cycle与两个实际caller后才传参。源码双审核这一范围，不能以一个单独新helper代替process_pending实际调用。

_execution源参数在TLS变动前检查，始终为本次上下文安装profile_source（普通调用也显式安装omitted），在finally连同job/stop/profile逐字段恢复旧存在性和值。_execution_profile_source在没有job上下文时返回omitted观察值；有job却丢profile_source或profile字段时明确拒绝，不getattr降级。_execution_profile对实际存在job的上下文也走配对检查。check_admission原业务逻辑保持。

D001：model_scopes.execution的私有frame转换按实际被复用的Execution.profile选来源，保原ordinary identity业务语义。下表的“父”是同一次实际上下文显式安装的frame，不是按identity搜索全局记录。

| 父frame | 本次参数 | 实际结果 |
| --- | --- | --- |
| 无父 | omitted | 原新Execution，普通来源 |
| 无父 | 显式source | 原创建/取得的Execution.profile必须is source.profile；核真实getter后保存该source |
| 普通父 | omitted | 按原规则复用原Execution，仍普通 |
| 普通父 | 显式source | source.profile必须is被复用Execution.profile；临时新inner frame持source，父frame不变 |
| strict父 | omitted | 复用原Execution与父原source；不得擦掉source，不能给传入的另一个同identity实例补来源 |
| strict父 | 父同一source对象 | 核原source与实际Execution.profile后复用；父frame不变 |
| strict父 | 其他source/None/坏值 | 在Context set与业务副作用前拒绝；不改父frame |

原嵌套new_execution=True仍拒绝。例：父Execution持profile A，inner传同identity不同实例B且omitted，原业务继续返回Execution A；若父strict，内层仅保持A的已有source，不宣称B有来源。若inner显式传B的source，不能装到复用的A，必须拒绝；不偷偷创建Execution B绕过原语义。普通父允许临时选入A真实source时，inner退出恢复原普通父；没有跨调用永久升级。

每个实际frame设置独立原Context Token并在finally恢复；部分set/reset失败保原异常及原Token记录，不用重新set补造历史。current_execution和原call判据返回/比较原DTO；frame不进入外部adapter。copied Context可保来源信息，不能生成新custody或跨线程使用原scope/Token。

DTO包装不得先读取任意dto.profile而执行custom getter。消费者先按现有exact DTO类型门，用该模块固定的原slots descriptor取profile，再将原dto/原profile显式交route包装/解包。route仅核封套原dto身份与source/profile原pair，不接收caller可选expected class或descriptor作为认证；四种DTO的原class/descriptor由各自固定consumer保有。普通DTO定义、构造参数和JSON不变，source不改原SEAL/业务/能力门。

本修订仍只有设计与静态来源，没有新候选compile/import/执行、模型/诊断/SQL/native/产品运行。6A及6B–D均未实施，不安装，不解除Task7及原完整目标门。
