# Task9 原严格构造与scope准备链 Implementation Plan

> For Codex: 沿用 executing-plans；复用同一名只读复审代理，不新增代理。普通开发已授权，不新增确认。保留dirty树，不作Git操作。

**Goal:** 在真实marker计费改造前，用完整原模块验证实际Profile owner→Budget→binding/group→scope安装的原路径，确认首marker之前可用同一个已认证预算。

**Architecture:** 继承r1055完整候选及r1056已封存的114文件只读代码副本。每个新具名case用独立进程和新的兄弟state目录，通过原storage_admin、enrollment、custody、scope函数完成EX进入前的构造，不调用EX的__enter__。按原函数恢复ContextVar仅证明该层reset事实，不宣称资源退出/业务退休或root释放。

**Tech Stack:** 现有Python包、stdlib子进程、原ContextVar/Token、Windows DLL及第三方包初始化、原profile和budget producer。

## 已确认与尚未验证

r1056的12项实际检查已通过，覆盖四首入与原认证、Profile48记录、原control四种ledger/history。它没有把Profile owner、真实group构造、scope安装串为同一条原路径；不能将分项通过直接升级为该链的运行证明。

当前源码中，三条严格owned入口均在创建Budget并绑定pending根后调用原_construct_group_origin_owned，再调用_claim_joint_origin_owned，之后才执行_ExclusiveScope(context,budget).__enter__。claim_scope原体内调用_claim_joint_owned和_install_owned；前者调用原control._claim_budget_owned，后者要求同原counter为0并安装原ContextVar。因此静态存在首次EX/marker之前安装同一原预算的路径，仍待完整原调用验证。

原OwnedExclusiveDeadlineLease构造仅建立路径/root对象、绑定、回调和未进入的lifecycle._guard_many上下文；本方案不得调用context.__enter__或任何资源获取入口。代码及第三方初始化仍有正常DLL/资源读取，不能称无IO/native。

三处实际marker读点仍是lifecycle._FileLock.verify、_verify_lock_group_body_original、_verify_guard_plan_files_original中的os.read(fd,32)；没有接request/charge。r993 DESIGN03的原合同保持：先完整grant32再读，成功实际31，短/32损坏也按实际长度结算；read抛错未知，不猜0、不重试；原first及return/effect分列。此方案不实现marker、不完成计费目录和容量。

## Task 1：静态来源与设计复核

Create `data/t16-h01-pre-ex-integration-r1057/`。保全r1055 SOURCE、实际r1056结果和相关完整源码、r993 DESIGN03；核原构造/claim/install/reset和装饰器上下文创建的副作用边界。Root及同一代理设计双审后才准备新case。当前方案只新增文档，不作候选行为修改。

## Task 2：三个新具名实际case

Create `data/t16-h01-pre-ex-qa-r1058/`。

1. X001：原配置profile的四去重roots，原custody._new_operation入口，按原caller顺序执行_PROFILE_ROOT_ADMIT→创建新原Budget→_PROFILE_ROOT_BIND_BUDGET，再原_construct_group_origin_owned、原_claim_joint_origin_owned；核origin/binding/group实际返回、同预算、原root身份与registration/pending/owner事实、初counter0与原ContextVar。不得省略pending准入、填造slot或替换原函数。检查后仅调用原scope reset并核该层Token事实，仍保pending根。
2. X002：原profile producer显式四参数，使根合法去重为一个，再按同一路径构造、安装及仅scope reset。独立验证去重root数与原tuple/Path身份，不能用已过的四root case替代。
3. X003：四root同原准备链后，调用原Budget.request(32)/charge(31)、原history及scope原grant对应校验，再调用原scope reset核真实Token恢复。没有读marker，不称实际marker费用完成；保留pending根和原索引，不执行terminal或额外重置。

每case都完整原模块导入并调用原authenticate。调用前核实际ready/bound和fresh状态，任何失败按原候选/组合失败保存。所需操作若有资源业务副作用，先静态收窄方案，不能直接运行或加替身蒙混。

## Task 3：脚本、准入与实际结果

新脚本和具名catalog双审；既有代码副本只读复用，全部114源文件pin逐份核同，新state/config/temp不与代码相含。准备器先核两脚本审所审版本的精确pins，后封存输入；实际准备双审后消费新单次claim。仅执行这三个新ID，不重跑r1056已通过的12ID。

保持-I -B -X utf8、最小环境白名单、case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/并发1/最多2pytest children。保存CASE/stdout/stderr/Tool/wait、旧红与完整来源；独立实际复审后更新主方案和AGENTS。若失败只修并复检原失败ID。

## 后继与回滚

该链具名通过后，下一实现才是严格资源原lock来源→固定marker producer→同原request/charge→实际read返回事实，补齐三读点及调用前固定目录认证。不能用环境开关/无scope/跳过fresh/预扣31或追加第二cap替代。完整费用资源债务、terminal两阶段、容量/durable恢复与完整Goal继续active。

回滚为不安装候选和不执行新case。正常库、服务/dist、ACL/settings、真实平台、Git保持；无profiling/frames/locals/停点/新异常链诊断。r920/r964已耗不重放，r967待原问题不执行或重问，P06历史0/7；canonical/c973本段apply0/历史11、产品pass0。
