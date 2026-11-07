# r1110 原代码根 bootstrap 与实际函数 producer 子设计

r1110已明确原代码根/原exec消费/实际function产物三事实，否决CREATE watcher来源方案；P2 native实际producer与23顶层factory的空tuple For模板取得3.12.10静态依据。L1可信安装、完整来源与共同消费接线仍待，完整设计/source/execution均False。源码仍r1106 SOURCE02完整159，Goal active未完。

本批冻结31份完整原/副本pairs，每侧4612046B，核父r1109封存53及当前159源码，保护56同18lstat缺席。新增11份官方CPython3.12.10源码/文档共638891B；核心两报告19段原文核同，追加3份CFG宏依赖单列，同一独审分别核bootstrap与模板材料。没有新产品候选、可执行模板、syntax compile、候选import/执行、marshal-load、native、hook、模型、QA或诊断；三源AST仅头部调查。

官方loader原get_code单次返回→原local code→原builtin exec是保真方向。仅prepared根不能冒认实际exec，exec audit单code payload不能无frames唯一绑定module。L1只提按原loader实例包装，尚未选定可信安装/原helper与exec来源、实际globals/attempt绑定、错误/循环import/reload与恢复；没有安装全局finder/audit hook或任何包装。

CREATE watcher早于MAKE_FUNCTION的closure/defaults等后填，亦来自其它构造路径，缺原producer位置，负返回是unraisable且API自3.12新增，因此否决作为原来源。P2由native自己从独立expected原code/globals创建并握该次actual返回，不接受任意subject登记，也不以first-wins认领原槽；恶意再入另产对象不能据此替换可信caller actual local。actual return/opaque origin/原owner-fact-STORE接线、func_version、可变环境与分配失败仍待实施设计。

模板选择取得具体静态路径：module-scope `for unused in ()` 内保原def，AST/符号表/编译/CFG保body及const引用，避免新嵌套函数层；不使用可能被unused-const清理的if False模板。23个顶层factory=control9/scope8/registration6，全部一位置参数且无defaults/kwonly/varargs/kwargs/decorator/注解/type_params；另3nested分列，不改写r1106原24运输统计。未compile新模板、未构建新const槽，不宣称ABI/qualname已在新候选或其它版本验证。

Root与同一独审对本子设计/静态材料无阻塞错误，不代表完整设计通过。N0/L1、N1真实根与实际producer发布、TMD及三处D0、D1/D2、N3/N4锁/错误/引用预算、>=3.11 native打包仍未闭；源码缺口闭0、产品pass0，非只剩验收。原正常库/service-dist/ACLsettings/真实平台/Git保持，canonical apply0/历史11。原17lease/1449slot/406尾/五S002和case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。

禁止profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitor/eval。r920/r964耗不重放，r967待原问不执行不重问，P06用户无历史材料0/7不再索取。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal active未完成。

# r1110：原代码根取得与原函数实际产物的两个不同接口

状态：具体子设计候选，等待本批双审；不是完整 N0–N4/COMPAT 设计批准。源码父保持 r1106 SOURCE02 完整159。禁止由本文件进入候选执行、局部模型、native 构建或 QA。

## 原问题与范围

r1109已固定五条代码树路径和三处 D0 incoming。这里不再盘点这些目录，而是解决目录怎样取得可信 runtime 起点、怎样对应实际新函数。遵守 r1075 的可信原 caller body/首次源码导入/原 primitive 边界，不增加对任意解释器整体被替换后的防护保证。审计、监控、合法再入及各具名 subject/alias 的变动仍须处理，不能用这条边界自动排除全部回调。

本批区分三个事实：`get_code` 实际返回了某个代码对象；原 `exec` 尝试消费了该对象；后续某个 function 是指定 producer 的实际产物。任何一个事实均不能替代另两个。

## ADR-1110-1：用真实返回根，保留原 exec 路径；安装接点尚未闭合

保留的代码根方向是，在可信首导入中原 loader 的**单次** `get_code` 返回边取得并强持同一 exact CodeType，按 r1109 的确切 const 路径导出 expected。禁止重复 compile/marshal-load 同源材料、从 current subject 取得 code 再叫作原根，或用内容 hash 代替 runtime identity。

官方 `_LoaderBasics.exec_module` 先 `code = self.get_code(module.__name__)`，再处理 None，最后把这个 local code 交给 `_call_with_frames_removed(exec, code, module.__dict__)`。因此最小保真方向是继续走该原 body 和原 builtin exec；直接换成 `PyEval_EvalCode` 缺少 builtin exec 内的原 audit，不能当等价实现。代码缓存命中、hash/timestamp 校验、源码编译与 cache 写入仍由原 get_code 单次完成。

具体待审接线 L1：在目标 spec 已找到、首次 exec 尚未开始时，仅对**该原 loader 实例**安装 C 侧的 exec_module/get_code 包装。C exec 包装先握本次 actual module、exact globals、原 loader/spec、解释器及 attempt；调用已捕获的原 exec_module。C get_code 包装调用已捕获的原 get_code 一次，在返回处强持其 actual result，仍返回同一对象给原 exec_module 的 local。原 module/spec/loader 身份、`sys.modules` 提前发布、相对导入、循环 import 与原 importlib 回滚应保持。

这不是已选定的可实施安装方案。以下均是 L1 实质未闭项，不能标为普通备注：

- 当前项目没有在首次目标导入前安装此实例包装的已实现可信入口；`reno/__init__.py` 仅24B，原先无安装行为。新增 finder、package bootstrap 或 startup wrapper 的影响必须选定，不能偷偷安装全局 meta_path/audit hook。
- 原 loader/原 exec_module/get_code/builtin exec 本身怎样从已接受 bootstrap 取得、怎样拒绝 alias 或 code 被替换的路径尚未接线；保存一次当前 bound method 不自动证明原来源。
- 仅 get_code prepared 不能冒认该 code 已由原 exec 消费。即使静态原 body 的 local 对应成立，也须说明实际调用原 body、原 exec 和 actual globals 的可信绑定，不能用 audit 的单个 code payload 事后猜 module。
- 实例包装如何安装和恢复、原属性存在/缺席/自有属性状态、失败与再入、reload 与先已加载模块均需明确。不能无条件拒绝原合法循环导入/partial/deferred，也不能从已加载对象事后重 pin。

attempt 至少分 `PREPARED`（exec包装已绑定 actual module）、`ROOT_RETURNED`（单次原 get_code 返回）、`EXEC_ATTEMPTED`（同根进入原 exec）、`EXEC_RETURNED`/`FAILED`。这些是未来记录语义，不授权新增 runtime journal；本批没有创建运行中的 attempt。模块初始化可以在 EXEC_ATTEMPTED 内按原阶段进行，不能等整个模块结束才允许所有 bootstrap，也不能把 exec 正常返回当完整认证。

## ADR-1110-2：拒绝 function watcher 作为原 producer 证明

不采用 `PyFunction_AddWatcher(CREATE)` 识别“原 MAKE_FUNCTION 的第一次结果”。理由已从官方3.12.10源码核实：

1. `PyFunction_NewWithQualName` 发 CREATE 时 defaults、kwdefaults、closure 仍为 NULL；MAKE_FUNCTION 在其返回后才填这些字段。文档的“fully initialized”不等于 Python def 的全部元数据及 STORE 已完成。
2. 该构造 API 也供其它创建路径使用；CREATE payload 没有 opcode、父槽、原 producer 位置或原 STORE。使用同 expected code/globals 的另一个实例也能触发 CREATE。
3. 通知按当前解释器 watcher 列表执行，负返回由 `PyErr_WriteUnraisable` 处理；它不是能够阻止原创建/STORE 的异常门。注册会影响该解释器所有函数创建，且 API 自3.12新增，不能覆盖当前 >=3.11 声明。

本批没有注册/清理 watcher，没有为证明而改全局 GC、trace、monitoring 或 PEP523。

## ADR-1110-3：原函数身份应来自实际 producer，不能靠 first-wins 登记

拒绝两种接口：`register(subject)` 接受任意传入函数后认作原来源；或公开 decorator/watcher 依同 code 的第一次见到者占原槽。回调可抢先登记另一个对象，current metadata 也可能已经被改。增加重读不补这条来源链。

保留两个能够进一步设计的 producer 方向，当前不混称已实现：

**P1，原局部 producer 直接运输。** 原 MAKE_FUNCTION 实际结果先在可信原 caller 的局部持有，然后作为该次 actual local 直接送入 C；C 已持独立 expected code。CALL 监控即使看见 arg0，也不能据此把另一个函数替换进原 caller local。它能修改 actual 对象，终段必须按独立 expected 拒绝。r1107已经接受的两个 inner local factory 正属此类来源，不重复改它们。把现有顶层 factory 改成新的嵌套局部域会改变 qualname/CO_NESTED/cells/模块符号或发布时机，未证保真前不能直接选用。

**P2，native 自己产生 actual factory。** 在有可信 runtime code seed 后，C 入口自行从该原 expected CodeType 与 attempt 的原 globals 创建新 factory，不接收 subject/current code。它直接强持该次构造器返回的真实 function，并把该对象和独立 origin 结果交给可信原 caller 的 actual local；每次调用有独立结果，不能用 first-wins 槽。

P2 的实质优势：来源是“可信 producer 自己创建的那个对象”，不是“恰好通过 metadata 的任意克隆”。恶意再入另产一个对象不能替换原 caller 持有的本次 actual return。CREATE watcher 若改变刚创建对象，expected code/globals/builtins 仍来自独立 seed，不能用构造后 current 重新认领为 expected。

P2 仍不是实施批准，至少需要下列确切修订：

- 原 Python def producer 要替换为 C producer；这属于新的来源结构变化，必须证明原 code template 保留、模块 compiler 符号/const 槽、function ABI/qualname/annotations/defaults/closure 与各原发布的顺序。不得把另编译得到的模板对象叫作旧 runtime 根。
- 目前只为无 defaults/kwdefaults/closure 的具名顶层私有 factory讨论固定构造路径；不能推广到所有原函数或163 incoming。应从当前源逐一证明适用集合，不能只靠函数名。
- 原 global STORE/effect 与之后的 guard 必须分开。不得因 CREATE 回调改 metadata 而抢在原 STORE 前抛新的认证错误；先保同一 actual result 的原 STORE，再在原消费点联合拒绝。真正的构造失败/新增 origin 分配失败需单独评估，不谎称与原成功路径完全相同。
- native 创建入口及 origin 的可信取得、预算、强引用、失败清理、先已加载/reload、并发和再入必须闭合；公共可调用能力不能让陌生调用覆盖原 caller 的 actual result、原 owner 槽或原阶段。

本批选择的是**后续论证方向 P2 优先、P1只保已成立的 inner local**。尚未选定 L1 安装；没有把 P2 可构造性折算成 N0/N1 完成或完整设计双审通过。

## 与共同 metadata 消费和实际分派的接线

代码根和 actual function 身份取得后，C 终段仍需同时核实际 code、defaults、kwdict全键/entries、closure/cells、globals/builtins、原 caller owner/source/fact/current phase及实际 vectorcall；持 GIL、无未知 hash/eq/getter/audit/分配/DECREF 回调。原 audited getter 的前置顺序/次数、原失败和锁域不因 P2 消失。

三处 incoming 的 D0：同一可信 native 入口在全部准备后终段核对，再消费原 target 和原一位置参数。metadata 验证只认可该次检查结果，不给以后 subject 调用永久权限。原锁不跨业务/helper调用；D1 frame/参数与 D2 closure 的实际消费仍须分别论证，不能把 P2 变成整个 body 不可变的声明。

## 交付与兼容约束

当前项目仍声明 >=3.11、无 native build 配置。仅核 v3.12.10 源不能证明3.11/其它版本/平台支持；不能自动缩窄声明或回落到原不安全 Python 快照。后续完整设计须给 native 初始化、wheel/source构建、加载失败和升级回滚的同强度方案。

whole module code 只应持到该次原 exec 结束，所需固定后代根按实际消费者寿命持有；但确切保留集合、字节预算、转交及逆序释放尚待设计，不能宣称已在 control1MiB 内。保 case30/parent360/matrix1680/cleanup10/并发1/最多2pytest children/native20–25–10。

本批31完整输入 pairs、父r1109封存53、当前159源码和保护56同18缺核同；新增4份官方源码/文档仅保存读取。没有新产品候选、syntax compile、候选 import/执行、marshal-load、native、模型、QA或诊断。正常库/service-dist/ACLsettings/平台/Git保持。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索材料。Task9A/9B、原111/94/r919、真实Store及完整Goal仍未完成。

# r1110 P2 的具体模板载体与适用范围

本附录与 ADR-BOOTSTRAP-01 一起构成本批当前子设计；不是完整设计/源码/执行准入。它将 P2 的一个开放点推进到具体3.12.10静态路径，不创建产品候选或可执行模板。

## 同一次真实导入中保留模板

不选 `if False: def ...`。官方 CFG 优化后会删除不再被指令引用的常量，因此不能假设这种死分支的函数 code 必留在实际 module.co_consts。

具体模板载体候选如下。这里是说明结构，省略号不代表已实现源码：

```python
for _AUTH_TEMPLATE_UNUSED in ():
    def ORIGINAL_FACTORY(_held_factory_context):
        ...  # 保持本次原 factory 的全部 body
# 该处由可信 native producer 产生本次实际 function，接回原发布位置。
```

这层 For 仍在 module scope。空的 exact tuple 不从 mutable global 读条件，不调用条件函数；正常解释器语义下 body 和 loop target STORE 均不执行。官方3.12.10 AST folding保留 For body，compiler_for产生GET_ITER/FOR_ITER并编译body，CFG保留fallthrough与jump两条结构边，FOR_ITER优化没有删除空tuple循环的规则。因此 body 的LOAD_CONST code仍被视为使用，后续 unused-const清理有保留该模板的静态路径依据。

这项依据严格限于本次核对的源码链。未新compile、未构造CodeType、未运行空循环；不能冒认已验证候选字节码、-O/-OO全矩阵、其它CPython版本或平台。未来确切候选须重建实际const槽，按同次get_code实际module根导出；不得继续照抄r1109旧父源码槽号。

For不新开函数词法域；模板原def仍是module的function scope。与把def搬入新嵌套函数相比，这条路径避免主动新增`<locals>`层和相应closure结构。模板必须保全部原import/global符号规则、原factory body/ABI、原qualname，新增唯一loop target也需进入完整symbol/bytecode差异核对。当前三源中不存在`_AUTH_TEMPLATE_UNUSED`，这只证明本次选名不冲突，不是新候选已经编译保真。

## 精确适用集合

对冻结的control/scope/registration三源做AST头部核对，本次有23个**顶层** `_AUTH_CATALOG_DEFINE_*`：control9、scope8、registration6。全部为一个`_held_factory_context`位置参数，默认值、keyword-only、varargs、kwargs、decorator、参数/返回注解及type_params均0。它们可以作为“无需额外填默认值/注解”的P2固定构造集合；这不是所有认证函数的统一ABI。

另有3个具名nested factory，不属于本次顶层模板变换。r1106的24次private协议运输与本次23个top-level集合统计不同；不改写旧24，也不把26个具名def等同26个新变换。r1107已证的control两个inner local继续沿其原词法来源，scope的nested项按其原caller链保留，不能因本附录自动宣布整个来源图已闭。

## native实际构造仍要补齐的行为

从可信seed的原code与原globals自行`PyFunction_New`，握其实际返回对象，仍是P2核心；不能接收外部subject/current code来替代。原MAKE_FUNCTION除PyFunction_New之外还会填选定元数据、设func_version；即使23头部没有optional字段，native实现也必须核原func_version/标准vectorcall及模块名/builtins来源，不能只比较可见签名。

origin及输出容器的分配、强引用准备应先于终段。要明确CREATE回调改变对象时，独立expected保持原值，原STORE发生后在原消费点拒绝；新origin分配失败、tuple iterator分配失败、PyFunction_New自身失败各是不同路径。这里增加了一个真实tuple iterator及新的native记录，尚未证明与原预算、所有异常首对象/时机及逆序释放兼容，不能隐去成本。

恶意再入调用只能得到它自己的新对象/opaque origin；opaque origin必须绑定实际object、固定route、原attempt与对应producer结果，不允许陌生调用覆写原owner发布槽。如何把本次actual return/origin经可信原caller接入既有fact/STORE，是后续完整设计的实质义务。此处不能靠一个“首次登记成功”标志替代。

## 本批能关闭与不能关闭的项目

本批可以确定：CREATE watcher不是来源方案；P2不必接受任意subject登记；module-scope空tuple For是一个有官方3.12.10静态依据的模板载体；其具体适用集合为23个顶层factory。

本批不能关闭：L1可信安装/原loader与exec绑定、完整N0/N1 runtime来源、原actual return/STORE完整接线、联合metadata/三处D0实现、完整引用与预算、>=3.11打包兼容。完整design/source/execution均False，不因本附录或材料审0启动native或QA。原保护、禁项和Goal未完成状态保持。

# 下一批：闭合可信安装及原producer实际接线

当前源码父r1106 SOURCE02完整159；文档父r1110。先读ADR-BOOTSTRAP-01、TEMPLATE-PRODUCER-ADDENDUM-01、Root与两份独审；不重盘r1109的五路径/429树或本批23头部，不再尝试CREATE watcher/任意subject first-wins/late compile根。

1. 对L1做明确可行/否决选择，给原spec/loader/actual module/globals/attempt可信取得和原exec实际消费的前置安装路径；只包装实例也需证原方法/alias、单get_code、None与错误、sys.modules/cycle/partial/reload/失败回滚和恢复。不能将可信安装缺口当材料0消掉，更不能偷偷装全局hook。
2. 对23个顶层factory的P2逐次actual return/opaque origin/原STORE/effect接线。保原def template的compiler符号、code ABI/qualname/func_version/metadata；新const槽只来自未来同次loader实际module code，不用旧槽号或另编译副本冒认。已证inner local继续原词法来源。
3. 将C联合metadata和三处原D0实际分派、D1/D2、原audit/锁/异常和引用释放、原control字节预算一起设计。新iterator/record分配失败和未知回调需单列；原锁不跨callee。
4. 补native自身可信根与>=3.11构建/安装/加载失败/回滚。完整设计双审前不生成产品候选；完整源码准入前不import/运行候选、局部bootstrap/control模型、native或QA。已耗compile/诊断/旧绿/F001不重跑。

正常库等与所有原保护/预算/禁止项保持，不等待重复用户批准；完整Goal active未完。
