# r1116 R2 替代执行器顺序与兼容裁决

r1116完成R2替代执行器的原顺序核对与兼容裁决：三份既有module根的首constant字符串与候选docstring核同，公开New与原private构造在CREATE时doc不同，现行约束下否决R2。原r1111 builtin接线保持；条件carrier方向不变，原X来源仍待。B001/B002继续open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

本批20份完整输入pairs，每侧905414B；继承6份既有CPython3.12.10官方文本362075B，无新下载。23段准确原文、3份既有raw root0和三源码AST核同；raw完整文件按r1106 DELIVERY指纹引用，没有重编译、构造CodeType或运行反例。核父r1115封存54文件/6956344B，完整159源码7956657B及保护56同18lstat缺席。离线根不冒认runtime get_code身份或尚未生成P2模板的code布局。

原private module构造将doc固定None，公开New用首字符串constant；二者均在CREATE前安装字段。既有合法C watcher在该条件下能看到不同字段，CREATE后改doc、改code/const或恰好无watcher的局部结果均不能抹去这项静态差异。没有安装watcher或观测本机watcher状态，没有执行异常/攻击或证明全route可利用。否决的是r1115具名R2替代，不是全部native路线；现行r1111合同未被替换。

原loader None/第二次name/ImportError、两位置参数与locals=G、先插builtins再closure/同code audit、原builtins→name读取、private/public字段、nonoptimized locals、M→locals→结果释放→None顺序已逐项列出。O001保留读取顺序及失败/再入义务，不把无callback exact-str窗口的反向读取一概写成必然可观察；O002明确E不能直接返回求值结果，但只有设计义务，没有源码修复或执行证明。额外H CREATE/帧、C-call/first/tb/引用、N4与版本预算仍待。

下一批仅比较保留原builtin/EvalCode构造、同时向actual native producer交原环境的接点。先区分原module→native直接边与helper→native边；公开API取得当前builtins也不证明caller身份，不读取真实frame/locals、不从外部任意值补expected。必须连同P0原两次环境求值、原caller/STORE、23P2/CORE第24项、D1/三D0/D2/N2/N4/COMPAT一起闭合；不重复以返回None或CREATE后修doc重提R2。

本批candidate/compile/import/执行/模型/native build/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal未完，非只剩验收。正常库/服务-dist/ACLsettings/真实平台/Git保持；原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06无材料0/7不再索取。

# r1116：R2 替代执行器的顺序核对与兼容裁决

**决定：否决 r1115 所述 R2 作为现行保真约束下的执行路线。** 它用公开 `PyFunction_NewWithQualName(same_code,G,co_name)` 代替原 builtin exec 内部的 module function 构造；在仍保留文档字符串的目标根上，两者在 CREATE 时的 `func_doc` 不同。CREATE 后修改字段或最终模块字典一致不能恢复此前已发生的观察。这是具名路线的否决，不是证明所有 native 或原环境取得办法都不可能。

本文件为有限静态裁决，不是完整修复设计或实施许可。现行 r1111 修订02 的原 helper→原 builtin exec 合同不替换。r1115“已可信强持归一化 X 时，经新 carrier 的实际活动帧直接 New”的条件绑定子方向保持，但它尚无已闭的最初 X 来源，不因本裁决变成可实施方案。B001/P0 实际创建与运输、B002/原环境取得及同阶段消费继续 open2；full_design/source/execution/complete_Goal 均 False。

## 1. 有限输入与判定依据

父为 r1115 封存54文件；产品源码仍 r1106 SOURCE02 完整159文件、7956657B（154 reno、3 app、2 scripts）。本批只冻结20份必要完整输入，继承6份既有 CPython v3.12.10 官方文本，不下载新资料。三份已封存 raw compiler JSON 按 r1106 DELIVERY 的完整文件指纹引用，不重复编译或生成 CodeType；同时对三候选源码仅作 AST 解析。

三份原始编译记录的 module root0 均为 `<module>`，0参数/0 kwonly/无freevars/cellvars、flags=16777216，第一项 constant 均为字符串，并与当前候选的原模块 docstring 一致。它们是当前父源码的具体反例输入，不是已经取得 runtime 原 get_code 身份，也不证明尚未生成的 P2 模板的新 code/const 槽。后者不能借本库存得到批准。

约束仍来自 r1075 原方案：可信首次源码基线是有限信任起点；原认证图的实际 callee/依赖、unknown0、原业务错误首对象/次数/传播及原 RHS/STORE/effect 必须保持。r1111 原 exec 接线属于可显式修订的内部决定；r1115 已明确修订不得自动豁免 CREATE/audit/原错误与引用差异。因此本裁决不是把内部设计抬升成用户永不可改的指令，而是指出这个具体替代仍未满足其必须保留的行为。

## 2. 原路径与 R2 所需的完整顺序

下表是静态顺序清单，**不是已实现、已验证的 E**。支持目标仅为该次可信原 SourceFileLoader 返回的 exact-code、actual exact module/G；E 不是对外提供的通用 exec 实现，任意外来调用不得登记原槽。

| 次序 | 原路径 | R2 必须处理的事实及本批裁决 |
| --- | --- | --- |
| 01 | 原 loader 读取 module name，原 get_code 一次 | 原 helper 及 same actual code/G 来源仍须 r1112/完整 N0 接线；离线 raw0 不能冒认 actual root |
| 02 | get_code 抛异常直接透传；返回 None 时第二次读 name 并抛原 ImportError | None 分支留在 loader 包装侧，不调用 E/audit、不产生 seed；不能把 source=None 交 E 或重跑 get_code |
| 03 | 非 None 后读取实际 module dict；helper(original_exec,code,G) | R2 明确改为 helper(E,code,G)，原 builtin 的实际 C 调用消失；不沿用 EXEC_RETURNED 声称 builtin 已执行 |
| 04 | builtin 的 locals 参数省略/None，实际 locals=G 并取得一份引用 | E 的受支持两位置参数分支同样使用 G；globals=None、任意 mapping、字符串 exec 不是这条 loader 路径，不能宣称完整 builtin 参数兼容 |
| 05 | 检查 globals 为 dict、locals 为 mapping；检查 builtins 键，缺席才插当前 helper 环境 | 插入发生在 closure 检查和 exec audit 前；不能提前拒绝某条后来失败的 code 分支而丢掉原已发生的插键 effect。原 G 不做 X 补偿预插 |
| 06 | closure=None 归 NULL；code freevars=0 时无需 closure，否则省略 closure 会抛原 TypeError | module 受支持快路须实际证明布局；未知 flags/freevars 门与原业务错误分列，不能静态库存一概覆盖所有 runtime code |
| 07 | 对同一 actual code 发一次 exec audit；失败走原 error cleanup | E 自发 PySys_Audit 仅是拟复现事件；不是原 builtin 已调用。audit 可改 G、删 builtins、正常再入或抛首异常，不能在它之前拍定 X |
| 08 | EvalCode 先从当次 G 取得/归一 builtins；缺席回当前 helper B | 这才是原 module 创建事件的环境 X；None、缺席和 module dict 分开。E 必须从独立可信读点握 X，不能从 public New 的 CREATE 后结果补 expected |
| 09 | private constructor 再读 G 的 name，构造固定 doc=None 的 M | public New 先读 name、后取 builtins。O001 的顺序差异保留；在已证 exact-str keys/GIL/无 callback 的局部窗口，反向读取本身不等于必然可观察，不能以顺序不同单独声称所有路径不可能。完整读点/失败/再入证明仍未闭 |
| 10 | M 的 name/qualname 均 co_name；defaults/kwdefaults/closure=NULL、version0、标准 vectorcall；GC track 后 CREATE | R2 显式 qualname=co_name 可对齐该字段；**doc 则取 co_consts[0] 的字符串，在三具名父根上与原 None 不同，R2 由此否决**。不得在 CREATE 后改 doc 或改原 code/const 再称 same actual root |
| 11 | 原 EvalCode 以 locals=G 直接求值 M | public function vectorcall 对非 CO_OPTIMIZED code 使用 func_globals 作为 locals，是有限静态可行路径；仍须联合 metadata/实际 vectorcall、D1/D2/原 flags 和必要 code 参数门，非最终批准 |
| 12 | 原求值返回后先 DECREF M，再将结果 v 交回 builtin | E 若自持 M，成功与失败均须在相应原位置释放 M；额外 X/临时引用可能延长存活或触发重入，不能只在末尾统一逆序释放便称等价 |
| 13 | v 为 NULL：builtin 释放 locals 引用，返回原异常；成功：先释放 locals、再 DECREF v、返回 None | O002 的正确设计义务已明确：E 不能直接返回 v，即使普通 module 往往产生 None。求值结果及额外引用释放、首异常保护和新增失败仍须实际源码证明，本批未实现 |
| 14 | helper 正常返回或沿原失败传播 | 新 E/M/H 的分配、额外 H 的 CREATE/帧、C-call 事件、traceback/首失败/引用变化均独立列明；没有“bootstrap 一律免保真”的条款 |

上述顺序不接受把“不支持”伪装成原正常路径成功。get_code 原异常、None 的 ImportError、插键/closure/audit 失败与认证门拒绝是不同分支。完整实现如果改变其中的原 effect、首对象或次数，就必须继续保红，不能通过缩小 fixture 或删掉合法回调取得准入。

## 3. 决定性不兼容：CREATE 观察的是当时的函数

CPython v3.12.10 的 `_PyFunction_FromConstructor` 在 CREATE 前把 `func_doc` 固定为 None；`PyFunction_NewWithQualName` 则在 CREATE 前把第一项字符串 constant 赋给 `func_doc`。两者都在 GC track 后通知既有 active function watchers。通知实参包含实际 function，回调能观察它当时的字段；回调失败另有 unraisable 路径。这里没有安装 watcher、没有推断本机是否已启用任何 watcher、更没有执行反例。

具体静态区别为：在三份具名父根任一仍作为该次 code 输入、且有既有合法 C function watcher 的条件下，原 module M 的 CREATE 看到 `func_doc is None`，R2 的 M 看到该原字符串。一个按这一字段分支的既有回调可以产生不同结果或后续状态；无需外部制造同 hash code、假 caller 或首次任意登记。这已足以否定“在现行允许的条件下兼容”的普遍主张，不能用恰好无 watcher 的局部绿例消去。

这是**静态可观察差异与未满足兼容义务**，不是已运行攻击、已观测异常或完整认证漏洞利用。原要求没有自动授予 CREATE 字段差异豁免，因此本批选择不采用 R2，而不是请用户为尚未实现的候选作宽泛放行。

以下补偿均不被本裁决采用：

- public New 返回后修改 M.doc：先前 CREATE 已发生，无法撤回。
- 修改 co_consts[0]、替换同结构 CodeType、去掉 docstring 后称仍消费原 code：改变 same actual root 或已有源码语义；未获合同修订。
- 临时停用/替换 watcher、全局 trace/monitoring/eval/GC：越出当前边界。
- 调用未导出的私有 constructor、自行复刻解释器或原位改 func_builtins：没有因“公开路线失败”自动取得许可。
- 在无 watcher/无 doc 的分支放行，再把结果推广到全部正常 bootstrap：排除的合法条件仍是兼容缺口，不能计作完整闭合。

## 4. 引用、错误与版本的剩余义务

| 对象/事件 | 原已有路径 | 替代方向不得省略的义务 |
| --- | --- | --- |
| code/G/helper | 该次实际返回与位置实参持有 | 保 actual provenance；释放/重入后 fresh，metadata 锁不跨原业务调用 |
| builtins X | EvalCode 的借用值随后由 M 持有 | C 新增强持的实际取得时点、正常 key/module/None/缺席分支、CREATE 前后 mutation、释放时机均需独立说明 |
| name、qualname、doc | private 的借用/强持顺序与 public 不同 | 即使末字段相同也不证明原 first/释放等价；doc 此处已有决定性差异 |
| M 与结果 v | EvalCode 释放 M 后 builtin 释放 locals/v | 新 E 不能返回 v 或以 finally 额外失败覆盖原 first；失败的 owned refs 和可能复活分列 |
| S/H/body/payload | 原 exec 无这组 carrier 根 | H 是新函数，不冒认原 helper；额外 CREATE、frame、预算、引用环/N4 均需完整接线，保密假设不作来源证明 |
| 版本与构建 | 本批仅既有3.12.10文本 | >=3.11 合同不收窄；function watcher 是版本有限条件，不能向3.11移植 API。公开 symbol 存在不等于 abi3/链接/行为已验证 |

本批没有因明确 O002 的返回顺序而宣称其源码修复，也没有因局部 exact-str 读窗口可能无 callback 而宣称 O001 已闭。它们继续记在 B002 里；R2 的决定性 doc/CREATE 不兼容不需要先执行模型来证明。首次 X、P0/catalog 第4目标、可能的私有 ABI、23个 P2/CORE 第24项、原 caller actual return/STORE/del、D1/三 D0/D2、N2/N4/COMPAT 全部仍待。

## 5. 下一步与停止重复的条件

R2 判为 rejected_under_current_constraints，后继不再以“补返回 None/先取 name/CREATE 后改 doc”反复提报同一条路线。现行 r1111 原 builtin 路线保留，但它仍未完成 X 的可信取得，不因此恢复准入。

下一有限调查只比较**保留原 builtin/EvalCode 构造、同时给实际 native producer 转交原环境的接点**。先核现有 P0 实际调用帧及允许公开 API 的具体语义：原 module→native 直接边与原 helper→native 边必须区分；若某 API 内部使用当前 frame 的 builtins，只能如实证明当次根，不能从它反推出 caller 认证或读取真实 frame/locals。任何新接点都必须解决实际 caller 来源、原两次环境求值与 STORE，而不是再次先假设 X 已持有。未得到这一具体证据前不生成新产品候选。

正常库、服务/dist、ACL/settings、真实平台/Git保持；不 profiling/读取真实 frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局 GC/trace/monitoring/PEP523。r920/r964 已耗不重放；r967 不执行不重问；P06 用户无材料0/7不再索取。原17lease/1449slot/406尾/五S002，以及 case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10 保持。

本批新增候选/compile/import/执行/局部模型/native build/QA/诊断均0，apply0/历史11、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal仍未完成，非只剩验收。

# 下一批：保留原执行器的环境转交接点裁决

父r1116，源码仍r1106 SOURCE02完整159。R2已在现行保真约束下否决，不能只补返回None/CREATE后改doc就重提。r1115条件carrier绑定并未提供原X的最初来源。

1. 限定读取既有P0/context、原调用边和允许公开API的官方源，列实际调用帧。区分原module活动帧直接调用native与helper活动帧调用native；实际返回builtin映射不等于caller来源认证。不得以API内部访问当前frame推成允许读取真实frame/locals、profiling或观察停点。
2. 比较一个保留原builtin/EvalCode私有module构造的具体环境转交接点，明确原actual X的取得时点/强持、G有键/缺席/None/module归一、原两次环境求值可分歧及callback/原first顺序。没有具名来源就否决该接点，不先假设X已持有，也不重开CREATE watcher first-wins、code const注入、预插G、全局hook或私有未导出constructor。
3. 可行时在同一完整设计联动P0/catalog第4目标、必要私有ABI、23P2/CORE第24项、actual return/原STORE/del、D1/联合metadata/三D0/D2、N2/N4和>=3.11交付。任何外部任意producer调用不能占原槽；安全拒绝不冒认正常clear/reload兼容完成。
4. 完整设计双审前不生成产品候选，源码准入前不compile/import/运行候选、局部模型/native build/QA/旧绿F001或诊断。原保护/预算/禁项及完整Goal active保持。
