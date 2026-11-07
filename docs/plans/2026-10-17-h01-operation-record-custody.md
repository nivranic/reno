# 操作记录 custody 实际接线方案

2026-10-17，r1014 DESIGN01（保留草稿；下文修订02优先）。依赖 r1013 source02；沿用 profile-route-custody Task 2，不安装、不启用严格 route。

## 已核来源

四份父源共264712 bytes，独立全文及32表清单在 data/t16-h01-operation-record-custody-r1014。32表包括 control 2、scope raw 1/core 4/origin 7、registration 3、resources raw 1/core 14。origin 的 sync 是固定 bootstrap pins，不属于操作表。

## 具体实现

新增私有 personal_authority_guard_custody.py，表目录为固定32个tag。原表变量改为静态 Table locator，目录只保存标量key及weak custody。每operation custody强持原32个分区字典，原value/record/attempt身份及独立raw五元组保持。新增内部subjects和lease账也只归custody持有，必须列入强根清单。全局函数pins只能捕获静态table/弱目录，不捕获operation图。

根调用者先取得原不可变(custody,ticket) lease，强持它后才能进入origin begin或无origin claim。三处根claim/begin增加私有kwonly _lease；缺失拒绝，不在begin内创建无人持有的owner。后继enrollment真实caller仍待接，不为了兼容旧候选调用而静默建根。

所有接触操作记录的函数显式传内部kwonly _custody_call。外层入口创建局部Call；实际subject从弱目录升级owner，并在custody内部subjects原字典核同一subject身份。Call持子lease到完整函数退出，内部调用共享同一Call；原first/tb和返回身份不变。退出只归还子借用，最后root业务释放接口硬拒绝，Task 7/pending未完成不提供伪终止。

函数体内原get/item/values/写点实接Call。写点保原RHS求值→原subject/key求值→首次登记顺序，id表达式的对象仅求值一次。原跨索引候选和全表扫描仍遍历全部活跃分区并保全部候选的局部借用，不能仅看preferred分区。owner_key跨operation占用、2/3 votes和foreign first隔离保持。首次raw分配先有lease；partial constructor/返回失败同一Call保持。

weak subject marker仅持weak subject/weak owner。subject仍活但owner已失效时明确拒绝；弱回调只有标量key/weakref，删除前核同一条目。非weak subject的locator只在owner活跃时存在，独立raw subjects核is，不保永久id墓碑。raw记录、attempt和row的严格入口未知按原拒绝。

## 两处真实legacy边界

LockStreamDeadlineLease原不可weak且stream_path有真实legacy回退。新增__weakref__只用于known-strict弱标记；不作为物理资源字段，相关slots目录/认证同步。类型/constructor和资源行仍原身份，不能从新facade字段恢复来源。纯legacy对象不分配custody；已登记对象即使调用者改facade也不降级。

plan仍是原tuple，sync_media的裸plan调用保持。原plan_begin逐directory查walk，Directory本身weakable；已登记但owner失效的directory在walk入口拒绝。纯legacy计划继续None；mixed候选全部借用后按原guard执行，原guard完成后才能核写分区唯一性，不能预选首owner跳过检查。新方案不声称支持跨operation合法plan。

scope新增原raw/descriptor字段custody_lease，实际scope镜像强持一个子lease，使_CURRENT或copied Context保活scope时有明确持有者。查找仍用weak locator和raw账，不从该镜像恢复来源。reset不释放此业务持有，不作为整图退休证明；外部scope/异常引用仍在时不能报GC成功。原ExitStack/callback形式和唯一关闭尝试不变，最终callback/group/body holder由后继真实caller接。

## 固定目录与验证

父组合十份完整文件；本段预计四模块、owned_ex_deadline改动，加一个runtime，共十一完整候选，其余全文继承。同步class/function/bootstrap目录，保parent/inverse/diff和操作函数/调用点/表访问的机械变更清单；ASTcompile十一份不执行import。独立源码审前不得运行新模型。

新检查必须实选原producer、32个表读写、跨模块传播、legacy/expired、部分失败、同一原record/raw/tb、foreign/全扫描/mixed计划、Context持有和traceback环；纯runtime检查不能代替原接线。新批次需新claim及源码/runner/准备双审，原35与69不重跑不外推。产品import/native/EX/SQLite/Store/容量/恢复仍单列，所有原认证旗标保持False。

## 不得外推

根caller、profile owner、三个enrollment owned body、Task 7 pending容量/恢复、callback实际持有、原111/94/r919/Store和完整Goal仍未完成。本段若仅完成迁表，只报告迁表候选；不称Task 2–7完整或操作GC闭合。正常库、service/dist、既有ACL/settings、平台/Git保持；r920/r964已耗不重跑，r967待原问不执行不重问。

## 修订02：原计划必须显式带外部holder

独审D001成立：原plan_selected([strictDirectory], ())能登记空tuple，原plan_begin通过原plan_rows识别；只有目录扫描会在owner失效时误判legacy。因此修订02撤回上文“目录marker足以闭合plan”结论，草稿红记录保留。

plan producer与全部实际consumer必须显式传同一原Call/lease，或者传不可变外部封套(holder, rawplan)。封套持原tuple而不复制，不改变原plan_rows身份；即使rawplan为空、仅含legacy目录、或实际返回值损坏，仍保存严格来源与原first。真正legacy由原producer明确创建legacy封套，不能在consumer发现弱目录为空后补签legacy。

真实caller涉及sync_media._directory_verification_plan/_verify_directory_plan、storage_lifecycle._verify_lock_group、_capture_guard_file_plan_original/_verify_guard_plan_directories_original，以及持有冻结_GuardFilePlan的namespace caller。冻结DTO字段不改，外部封套另持；必须同步全部实际return/解包/consumer和固定目录。只改四模块不足以关闭此合同，具体调用清单以本轮后续独立调查为依据。

内部共享Call采用明确定义的所有者规则：外层创建并关闭自己的Call，嵌套callee只借不关闭parent；若callee另建child则仅释放自己的ticket。两种规则不能混用。scope的子lease同时写原raw与原descriptor镜像形成真实Context强弧，但镜像不用于寻根；copied Context结束前不认整图退休。

## 修订03：source05 的实际合同（本节优先，旧稿与红项保留）

当前为十一份完整未安装候选。新增 custody runtime；control、scope、registration、resources 的 32 个原强表全部改为静态 locator，219 个原操作定义逐层传私有 Call；enrollment 原构造 helper、sync_media 目录计划 producer/consumer、lifecycle 实际返回与消费者也已修改。owned stream 增加 weakref slot；profile origin 和 owned group 全文继承父版。本节不代表真实三个 enrollment owned body 已接通。

custody 原 state 的五容器为分区、subjects、loan 账、cleanup failures、carrier 账；分区含原固定 32 个 tag。新增容器使用仅增加 weakref 的 RawDict/RawList/RawState，原业务 raw 五元组及原记录内部字典不改。全局 OWNERS 只持原 owner/state/五容器/32 分区的独立弱引用；SUBJECTS 只持 scalar id 与弱引用；CALLS 只持原 Call/state/loans/membership/parent 的弱引用。每个 Table 的 index 保原插入顺序，partition directory 只持弱 owner/弱原分区。固定静态 code/class pins 不捕获操作图。

nonweak 原 subject 可以由活跃 owner 的 subjects 原字典强持；global id+weakowner 只作定位，升级时必须核 subjects[id] is 实际参数及独立原 state/root/loan。owner 活时原 subject 不发生 id 复用；owner 死后 nonweak locator 删除，不建永久墓碑。此为定位既有来源，不是凭 id 新建来源。独审 S003 已撤回，不再保留“nonweak 一律无法寻根”的旧判断。weakable strict subject 在 owner 失效后仍活时明确拒绝降级。

每个操作函数新建独占 child Call，仅在自身 finally 归还自身 loan；parent.keep 延长扫描候选到整个外层调用结束。原 Call 有独立 state/loans/membership，owner loan 另记原 issuing Call；移植另一 Call 的合法 loan 仍拒绝。preferred 写来源与扫描 pin 分开；values 按 index 的原全局顺序返回。missing-key/空表审计先看原分区是否相关，再核相应 owner，避免无关 owner 的坏 facade 阻断当前操作。最后业务释放仍硬拒 TERMINAL_PROTOCOL_REQUIRED。

两个 begin 从原 registration_scope 获得正确 scope 后、任何 partial 分配前局部 select 该 scope；不向 parent 传播 plan selected。foreign root/owner/stack 继续被 pin，并由原验证在 partial 登记后记录正确 scope 的 first。相反，显式根 lease 与已知 subject 的 owner 不一致必须在入口拒绝。enrollment helper 在原 try/caller_failed 之前执行根 admission，局部原 Call 持到完整构造的最外 finally，再给实际 origin begin 传同 Call 与 lease；错误根 lease 不能进入另一操作的 first 恢复。

目录 producer 实际返回不可变 carrier=(holder, 原 rawplan)，consumer 必须显式接同 carrier。strict holder 是原 custody/ticket，原账独立核 carrier/raw/source/parent；legacy producer 明确签发固定 legacy holder。继承 preferred 不等于 strict selection，只有原 guard 后显式 select 才可产生 strict carrier。outer hook 在解包前记录实际 carrier 返回，严格空 tuple 也不丢来源。冻结 GuardFilePlan 仍原十字段，外部 guard carrier 不增 DTO 字段。独立调用图未发现当前九个 namespace 文件调用这些 DTO helper，因此不宣称已接 namespace DTO 链；普通 require_guard/current_guard→StorageGuard.verify→verify_lock_group 原链保持。

scope 的原 raw 与 descriptor 同写 custody_lease，形成 CURRENT→scope→lease→custody 实际强弧。copied Context 可继续持 scope；reset 不等于业务退休。不得往原 ExitStack 字典增加字段、替换 callback 或额外捕获 owner。

已保留的源码红项包括 S001–S008 与各旧组合；source05 的独立源码审查在当前有限范围新发现 0。新模型按 runtime 与实际 selected-source 分列，实际原 producer 的其他 IO/native/EX/clock 依赖明确合成。原 35/69/111/94 不重跑、不外推；真实 route、全部 catalog、pending/容量/恢复、物理资源关闭、SQL/Store、安装和完整目标仍未完成。不得把 weak GC 检查说成已实现最后业务释放协议。
