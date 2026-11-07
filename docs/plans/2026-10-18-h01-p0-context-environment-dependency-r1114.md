# r1114 P0 context 与原 builtins 环境依赖调查

r1114完成P0 context与原builtins环境依赖静态调查：control的context[3][19][21]保存两次独立原求值，实际环境运输须先于context assembler。第二次原helper不保证同module环境；本机3.12.10 DLL无私有constructor named export。B001/B002仍open2，完整设计/source/execution均False；源码仍r1106 SOURCE02完整159，Goal active未完。

本批冻结30完整原/副本pairs，每侧3901645B；核父r1113封存60及当前159源码7956657B，保护56同18lstat缺席。继承8份官方CPython3.12.10文本484252B，四份项目源AST与13段准确官方原文核同，无新下载。另纯字节读取本机python312.dll的1706 named exports，没有动态加载、地址定位、链接或native执行。没有产品候选、compile/import/运行、模型、QA、SQL/Store或新诊断。

三原prefix均有六tools/rows/lock-slots/records/globals/owner的创建链，owner四项、slot16/13/8，primitives20/18/20；control另有四fact list及28项native。context[3][19][21]是G字典当次读取与实际lambda环境两独立operand；缺键时第一项可为None、第二项仍来自module frame，不能用post-exec current补pin，也不能把顶层tuple原生化当B001已闭。23个P2与CORE第24项仍分列。

有限静态反例：既有exec audit令G键为X，EvalCode/frame持X；在已配置的初始RESUME回调分支删键条件下原module内创建fallback X，原helper返回后再调用helper包装C builder却fallback B。本轮未核具名事件枚举/注册API，也未注册或执行回调，不声称完整产品路线成功。原稿E001和Root漏审保留，修订02收窄条件表述后双审。原环境两次读取的时序/分歧与合法值、缺席、异常分列。

私有constructor显式fc_builtins，但doc固定None、name/qualname/default/closure来源及CREATE与public New/MAKE_FUNCTION后填不同。internal header要求Py_BUILD_CORE；本机DLL无该私有named export，仅此构建结论，不作跨平台ABI证明。未选择自造constructor、scratch globals或原位改字段。原helper/builtin exec/same actual get_code合同保持。

使用catalog原seed须显式增加第4目标与首次/partial/reload/原捕获协议；零参maker改接受primitive为0→1私有ABI，尚未批准或实现。原Python RHS变native生产者是来源/时机delta，必须共同处理原STORE/effect、del、循环import与失败。材料双审0不代表完整方案通过；B001/B002未关闭，源码缺口关闭0、产品pass0。

下一步先选原环境取得和实际绑定的可实施接点，再做完整P0/catalog方案；23个P2/第24项CORE、原caller/STORE、D1/联合metadata/三D0/D2、N2容量、N4生命周期/预算及>=3.11构建发布仍待。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal仍未完成，非只剩验收。

正常库/service-dist/ACLsettings/真实平台/Git保持，canonical apply0/历史11。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964耗不重放，r967不执行不重问，P06用户无材料0/7不再索取。

# r1114：P0 context 与原 builtins 环境的创建依赖

状态：修订02的有限静态依赖调查，不选择完整实施方案。原稿具名事件证据不足E001及Root初审漏项均保留；本版只采用已核回调分支的条件推理，未核事件名称或注册API。沿用 r1113 的 B001/B002，两项均未关闭；完整设计、源码批准和执行准入均 False。权威源码仍为 r1106 SOURCE02 的完整159文件，本轮没有产品变换、compile/import、模型、native build/load、QA 或诊断。

## 1. 新发现与决策

原 P0 不能当作独立的四项 tuple 直接搬到 C。control 的 context 经零基路径 `context[3][19][21]` 引用 prefix 中创建的两项环境事实：`dict.get(_AUTH_CATALOG_GLOBALS, '__builtins__')` 与 `object.__getattribute__((lambda: None), '__builtins__')`。前者是当次字典读取结果，缺键默认 None；后者是该次实际 lambda 持有的 builtins。两次求值有先后，不能把它们概括为永远相同，也不能用 exec 返回后的 current 值替代。

因此调整下一步顺序：**先解决原环境在原消费阶段如何取得、持有和运输，再设计 context assembler。** B001 与 B002 必须联动；不能先宣布原生构造顶层 tuple 已关闭 B001。重新调用原 helper 与直接调用私有 constructor 两个捷径均不足以作为当前完整接线。

本轮30完整输入 pairs/每侧3901645B，继承8份官方 CPython v3.12.10 文本484252B，四份项目源 AST。另只按完整文件字节读取一份本机 DLL 的 PE named exports，无动态加载、链接、地址定位或 native 执行。以上均为静态证据，不是本项目运行证明。

## 2. 实际创建链库存

| 原创建事实 | control | scope | registration |
| --- | --- | --- | --- |
| context 项数 | 4 | 4 | 4 |
| catalog 返回 tools | 6 | 6 | 6 |
| owner 项数 | 4 | 4 | 4 |
| owner_storage 原 slot 定义数 | 16 | 13 | 8 |
| context primitives 项数 | 20 | 18 | 20 |
| 原尾 factory 行 / P2 顶层子集 | 10 / 9 | 8 / 8 | 6 / 6 |

三个 prefix 均先调用 catalog maker，核 tuple/六个 function，计算 tool_rows，再实际调用 owner_storage 创建 lock/slots；随后创建 records dict、读取 globals，组合 owner。control 另创建四组事实 list（宽6/6/6/8）及28项 `_AUTH_HELPER_NATIVE`。尾部才求值四项 context 的 NamedExpr RHS，直接成为原 constructor 第一参数；不能把该原 RHS 偷换成事后 G 槽认领。

完整原 RHS、语法调用和读写名称在 P0-CREATION-DEPENDENCIES-01.json。该库存是源码语法依赖，不断言任意 current 全局仍为原值，不代表原 metadata 联合校验已实现。23个 P2 顶层项与 control CORE 第24项继续分列；本轮不重复盘点 r1113 的局部参数链，也不改变24项原运输统计。

catalog 原 `_make_catalog_tools()` 无位置参数，捕获 object/type、dict/tuple/str/int/bool/list、len/dict.get/dict.items、zip、types 与 descriptor 类型、ValueError、object()、LockType/allocate_lock 及 acquire/release，返回六个原工具函数。它的代码根、实际 maker 和捕获环境都不能从当前 module alias 再 pin。

## 3. 第二次调用原 helper 仍可能取得不同环境

以下是对“原 helper 相同即可恢复原 module builtins”的有限静态反例，不是执行记录，不证明完整产品调用成功或可利用。本轮没有安装任何 audit/monitoring 回调，也没有读取真实 frame/locals。

1. 原 helper function 实际持有环境 B。目标模块字典是已持的 actual G。
2. 原 builtin exec 在检查/可能补入 `G['__builtins__']` 后发 exec audit。一个既有合法回调把该键设为另一个 exact dict X；X 与 B 身份不同，所需原 primitive 可保持相同。回调正常返回。
3. 原 `PyEval_EvalCode` 此时取得 X，用显式 `fc_builtins=X` 创建 module function；随后 module frame 由该 actual function 取得 `f_builtins=X`。
4. 条件设定：原 module 的初始 RESUME 已进入冻结源码中 INSTRUMENTED_RESUME 的回调分派分支，且已配置的回调在 frame 建立后、prefix 前删除 G 的该键并正常返回。该回调只须事先持有 G、用收到的原 code/offset 区分目标，不须读取真实 frame/locals。设后续该键持续缺席且相关操作正常返回。本轮未独立核实此分支的具名事件枚举、注册API或任意既有进程确已配置该回调，因此这里只是条件静态路径。
5. 原 module 内的 lambda/函数创建此时调用 `PyFunction_New(code,G)`；缺键 fallback 读当前 module frame 的 X。control 两项原环境事实在此具体轨迹下可为 `(None, X)`。
6. 原 exec/helper 返回后，若仅再调用同一个原 helper 来执行一个 C builder，则 builder 创建 function 时的缺键 fallback 读 helper frame 的 B。B 不等于 X，不能由“同 helper/code/G”推出同原 module 环境。

依据分别为 builtin exec 的插键→audit→EvalCode、EvalCode 的显式 constructor、frame 初始化、INSTRUMENTED_RESUME 调用 instrumentation、public New 的 G 查找及缺键 fallback。若创建时 G 仍有键，取当时值（module 值还会归一到其 dict），并非总是 frame 环境；本反例只选持续缺键且上述instrumentation条件成立的分支，不作具名事件注册可行性或实际进程状态证明。None、缺席、异常不能混同。

这不要求无限追溯整个解释器：r1112 的固定 T0、既有可信首次源码导入边界仍保留。有限边界也不能无依据排除所有合法回调或把 helper 的 B 自动当作原模块的 X。原 module function 创建另有 CREATE watcher，字段可变及 D1/首 body 保证继续独立，不能靠上述反例证明它们已闭。

## 4. 显式 builtins constructor 的源码与本机构建边界

| 项目 | `_PyFunction_FromConstructor` | `PyFunction_NewWithQualName` |
| --- | --- | --- |
| builtins | 从 fc_builtins 直接持新引用 | 从 G 取键；缺键读当前 frame/interpreter |
| name / qualname | fc_name / fc_qualname | co_name；qualname 参数缺省取 co_qualname |
| doc | 固定 None | co_consts 首项为字符串时取该项，否则 None |
| defaults / kwdefaults / closure | 来自 fc 各字段 | 初始 NULL；MAKE_FUNCTION 后续另填 |
| module | 两者均查 G 的 __name__，可缺席并保错误分支 | 同左 |
| vectorcall / version / CREATE | 标准 vectorcall、version0、GC track 后 CREATE | 初始同；MAKE_FUNCTION 后续还写 co_version |

`PyEval_EvalCode` 的 module case 自行取得 builtins，并把 fc_name 和 fc_qualname 都设 co_name；调用私有 constructor 后再进入 evaluator。这是解释器内部路径，不等于 extension 已有可链接接口，也不等于与任意原 factory 创建完全等价。

冻结的 internal header 要求 Py_BUILD_CORE，私有 constructor 只以 extern 声明。本机 `<PYTHON312_ROOT>/python312.dll` 的完整文件为6945272B、SHA256 `9a0e3435aaa680d868150f87ab3e388ad2eebc22f87e036155c7b4eda8cd2120`；纯 PE 读取到1706 named exports，其中该 constructor 和 `_PyEval_Vector` 均无 named export，`PyFunction_New`、`PyFunction_NewWithQualName`、`_PyFunction_Vectorcall`、`PyEval_EvalCode` 有 named export。

该结果只适用于此份 DLL，不能推广所有平台/构建/版本；有 named export 也不证明兼容 ABI。没有尝试链接、动态加载、按地址寻找或修改解释器。故不能直接把私有 constructor 写成当前 Windows extension 可实施 API。

自行重实现 constructor/evaluator、scratch globals、原位改 func_builtins/其它字段均未选定。它们涉及原 CREATE/audit/错误/引用/GC/ABI 语义，必须显式论证和修订合同；不能悄悄拿它们填空。当前仍保留 r1111 原 helper/builtin exec 消费 same actual get_code root 的合同，不重开 const 注入、预插 builtins、first-wins、全局 eval hook 或真实 frame 读取。

## 5. 尚可调查的方向与必须明确的 delta

C 从可信 seed 自行创建并强持 actual tools、rows、owner、slots、records、facts，再构造 context、参数交原 sourcecaller，可能避免从 G current 认领。但它没有解决原环境取得和绑定，因此本轮不选择或批准此方案，也不生成候选。

若使用 catalog maker 原 seed，r1111 当前只包装三个目标；将 catalog 纳入是明确的**第4目标**，必须补其可信原 get_code/消费、首次导入、partial/循环、reload、失败与恢复，不能直接 getattr 当前 maker 再授信。把原零参 maker 改为接受 C-held primitive 是0→1私有 ABI 变更，尚未实现或批准。即使改用 C 完整重建六工具，也必须逐项证明原捕获/实际返回/原存储与异常，不是免除来源义务。

将原 Python RHS 移为 native 生产者是创建来源和时机的结构变化，必须如实列出。原 global STORE/effect、del、先后次序、原环境两次读取与可能分歧、context/factory 参数、CREATE/释放再入、初导入/reload/direct loader 的区别，均需共同设计。不能因为持有某次 actual tuple 就称原 RHS 完全未变。

## 6. 下一批与不变准入

下一批优先选定原 module 环境取得及实际绑定的可实施接点，明确它与 same-code/原 exec 合同是否相容；无解的候选如实否决。然后才能选择完整 P0 producer/catalog 接线，而非再做同一 prefix 库存。B001/B002 保持 open，proposal_blocking_count=2；调查材料可审0，但不代表方案通过。

完整方案仍须覆盖23个 P2 actual function/origin 与第24项 CORE、原 caller actual return/原 STORE、D1/联合 metadata/三 D0/D2、N2 完整字段容量、N4 生命周期/错误/预算及当前 >=3.11 构建发布。本轮只调查3.12.10，不收窄版本合同。

原17lease/1449slot/406尾/五S002，以及 case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。完整设计双审前不生成产品候选；源码准入前不compile/import/运行候选、局部bootstrap/control模型、native build、QA/旧绿F001或诊断。

正常库、服务/dist、ACL/settings、真实平台与Git保持；不profiling、真实frames/locals、停点、定点计时、新异常链诊断或runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06用户无材料0/7不再索取。canonical apply0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal仍未完，非只剩验收。

# 下一批：原环境取得、持有与实际绑定的接点裁决

父记录r1114；源码仍r1106 SOURCE02完整159。先读ADR-DEPENDENCY-02（01仅历史红稿）、13段官方原文、prefix库存及LOCAL-CPYTHON-EXPORTS。r1114新发现P0历史环境耦合、第二helper反例与本机私有导出缺席，改变接线优先级；是实质progress，不是完成Goal或等待重复批准。

1. 优先设计原module环境在原消费阶段取得/持有/绑定的可实施接点，必须明确与same actual get_code→原helper/builtin exec合同的关系。不能从post-exec G current/原helper builtins补认原module环境，不重开预插、const注入、first-wins、真实frame读取或全局eval hooks。
2. 显式fc_builtins私有constructor在本机DLL无named export。若提出其它构造/执行路线，完整列原CREATE/audit、doc/name/qualname/version、字段可变、引用/GC/错误/ABI与>=3.11后果；无可实施路径如实否决，不先造候选。
3. 环境接点裁决后联动B001/B002完成实际P0生产→参数→原STORE/effect设计；catalog原seed意味着第4目标，0→1私有maker ABI及首次/partial/循环/reload另列。保23P2与CORE第24项的区别，不重复做同一prefix或caller库存。
4. 同一完整设计覆盖function/origin实际return、原caller/STORE、D1/联合metadata/三D0/D2、N2字段容量/N4释放预算和版本交付。完整设计双审前不生成候选；源码准入前不compile/import/执行候选、局部模型、native build/QA/旧绿F001或诊断。原所有限额和禁项保持，Goal active未完。
