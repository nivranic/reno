# r1108 联合校验与实际调用：有限架构调查、实施义务仍待闭合

r1108完成联合校验/实际调用的有限架构可行性调查及后续设计契约，明确C侧metadata检查、实际call尝试、frame代码参数、closure与首body的不同保证；完整修复设计仍未闭，未获产品实施批准。源码仍r1106 SOURCE02完整159，未执行，Goal active未完。

## 本批实际完成

冻结30份完整原/副本pairs，每侧2639938B；核r1107父50封存及r1106当前159源码，保护56同18lstat缺席。新增下载15份官方CPython v3.12.10源码740676B，加继承2源共17源871916B；只作静态阅读，未执行官方源码。来源URL、完整byte与SHA均在两份CPYTHON-SOURCES及INPUTS中。没有新产品候选、产品修改、syntax compile、候选import/执行、native build/load/执行、hook、模型、QA、SQL/Store或诊断。

Root负责实际构建/发布约束及架构契约；同一只读代理负责官方调用路径调查。独审23输入2512346B，Root逐项核同23pin和13段原文坐标；Root另保存18段官方原文。调查分工不是两份全量CPython源码审。两份设计材料复核均须保持“材料准确”与“完整可实施设计批准”分列：本批材料0不代表六组实施义务已闭，完整设计/产品实施/source/execution仍False。

## 确认的设计边界

拒绝把有限轮audited getter重读、code移末或snapshot返回后再由Python调用当最终一致性证明。C侧exact-type直接字段读取能够避开原getter的audit窗口，但expected原根、全部行协议、锁/引用边界仍须设计。exact PyFunction也不等于原vectorcall；3.12允许单独更换该指针。

把终段metadata检查、实际call尝试、frame f_code与参数建立、COPY_FREE_VARS的cell身份复制、首body前分别论证。固定完整位置参数成功快路与generic kwargs/default/varargs/错误清理不可混同；PEP523可以在default evaluator及closure复制前取得控制，RESUME监控/pending等又可在活动frame之后、首body之前使fact失效。公共API本身不能证明更强首bodyfresh契约；不把所有合法instrumented调用一概拒绝作为最终修复。

本版GC分配只安排GC，真实collection在eval-breaker，不能写成每次分配立即Python回调；frame数据栈虚拟分配也与普通GC分配分列。code object/co_consts身份根可作为后代expected方向，但不表示自适应指令缓冲永不变化，更不能从待核current function/co_consts自洽重pin。

## 当前具体未完成项

N0 native入口自身可信初始化/原根与加载来源；N1原factory code到后代的确切co_consts路径和原发布关系；N2实际metadata/sourcefact/current owner完整协议；N3各原调用的D0–D3契约、参数形状及锁/再入/失败路径；N4原audit/异常/引用释放兼容；COMPAT项目>=3.11的版本、平台及打包交付策略。六组实施义务仍open，不能生成或运行产品候选。

当前本机CPython3.12.10具备头文件/python312.lib，vswhere找到MSVC路径但未运行cl；PATH解析了两个cmake pyenv shim，未验证其可用，不能写成系统没有编译器/cmake。pyproject无build-system/扩展配置，当前环境setuptools/wheel/build元数据缺席。保持>=3.11，本批不冒认全部版本兼容，不安装依赖、不改构建配置。

准备脚本01因父DELIVERY重复列入而在任何inputs写入前断言停止；已核inputs目录/manifest缺席，原01与STOP保留。02仅按exact Path去重成30项后成功。此为静态工具错误，不是产品运行失败，不消耗候选compile/执行。

正常库/service-dist/ACLsettings/平台/Git保持；本批canonical apply0/历史11、产品pass0。r920/r964已耗不重放，r967待旧问不执行不重问，P06历史0/7不再索材料。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal均未完，非只剩验收，也不是等待用户重复批准。

# r1108：联合验证与原调用的架构决策、证明边界和实施门槛

状态：调查结论与后续设计契约；不是可实施完整修复的批准。源码仍为 r1106 SOURCE02 完整159文件。新产品候选、syntax compile、候选 import/执行、native build/执行、模型、QA、诊断均为0；source_approved=False、execution_admitted=False、完整 Goal 未完成。

## 问题和必须保留的行为

r1107已分别证实：后一次 function metadata 的 audited getter 可以使先前字段失效；原 caller 的 owner/source fact 可以在工具的后续 audit 中失效，而实际 incoming call 仍被尝试。这里解决的是这两个具体契约。五个调查入口不是整个认证图的覆盖证明。

保留原 pin/source 的真实来源，原目标及实参的 lookup、求值、实际调用、返回和原 STORE/effect 顺序；保留原 partial/skip/False/deferred、再入、异常和清理行为。不可从待核 function 的当前 code/defaults/cells 重新生成 expected，从而把污染后的自洽状态认作原来源。r1106已完成的24私有 factory 运输、r1107建立的两个 inner local producer 的词法来源不重复实施。

本批只读官方 CPython v3.12.10 源码，并记录当前构建条件。没有运行 hook、payload、自定义求值器、真实 frame/locals 检查或诊断。下述 C 接口和字段是源码设计调查，不是运行中读取 frame 的操作许可。

## ADR-1108-1：拒绝有限 getter 重读与“快照返回后 Python 调用”作为完整修复

决定：不采用它们作为最终一致性或实际分派保证。

理由：在允许 audit 回调改变先前字段的既有模型中，把 code 移到最后仍不能保护更早读出的 defaults、kwdefaults 或 cells。有限次重复没有提供共同成立的消费时点。Python helper 返回 snapshot 后，由 Python 再核 fact/再调用，也需要另行证明返回清理、后续字节码和 actual dispatch 之间没有使快照失效的边；不能只靠“紧邻”二字。现有 catalog.check_view 连续 getters 自身不是原子性依据。

后果：保留已有 audited getter 顺序和次数作为前置行为是一个待评估方向；不能删除其 audit 事件后声称原语义未变。若增加最后联合检查，必须明确它保护到哪里、原报错时机如何保持，以及原 getter 已失败的路径是否根本不进入新增成功门。

## ADR-1108-2：将联合 metadata 检查与真实 dispatch 分成两个证明对象

决定：只将“同一 C 调用内的无回调终段验证”和“同一消费点的验证加实际调用”列为后续设计方向；本批不批准通用 native dispatcher，也不批准其产品实现。

| 证明对象 | 要求 | 当前状态 |
| --- | --- | --- |
| TMD：终段 metadata 验证 | 对实际原 subject、expected pin、caller source/fact/owner 在同一无回调终段读取并核对；不能返回一份新 pin 供以后自洽认领 | 官方 C 字段读取提供方向，原来源/全行协议/原异常和释放接线尚未设计完整 |
| AD：验证并实际调用 | 最终检查后由同一个可信入口消费原 callable 和原实参；对实际参数形状逐边证明到相应 frame 阶段的窗口 | 固定成功快路与 generic/异常路必须分列；没有全边实施批准 |
| 原 caller 自身检查 | 终段必须包含 caller 的原 owner/source/fact/root 与当前允许 phase，不能只有 incoming function row | r1107已证原 view 缺项；尚未接线 |
| 真实原代码与环境 | code身份之外还要核 defaults/kwdict entries/closure/cells/globals/builtins及实际 vectorcall | 原来源登记和后代 code 根路径尚未落地 |

这里“无回调终段”是待实现的严格约束：持有 GIL，使用已证明的 exact-type C 操作；不调用 Python getter、未知 hash/eq/descriptor、audit、用户 callable，不等待释放 GIL，不分配记录/容器，不 DECREF 可能触发用户析构的对象。所需强引用、参数缓冲和可能阻塞的准备必须在最终核对前完成，并在所有准备后重核 actual 对象与当前状态。该约束不能靠源码注释代替逐操作证明。

exact dict 仍可能包含非 exact-str key；必须先完整扫 key 类型，再进行依赖名称的查找。kwdefaults 同时核原字典身份、长度、全部 exact-str key 及每项 value identity；closure 同时核原 tuple/cell 身份、空/非空状态及原内容身份。所有索引、长度、阶段、布尔值先验类型，防止 bool/int 子类或未知容器协议混入。不能把 tuple 身份或 func_version 单项当作全部 metadata 未变的证明。

本批未给出上述协议的 C 源码、可信初始化或所有调用点接线，因而不能把这些要求记为已实现。

## ADR-1108-3：按真实调用阶段界定保证，不扩大或缩小契约

公共 PyObject_Vectorcall 先读取实际 function.vectorcall；exact FunctionType 并不意味着该指针必为原标准实现，3.12还提供 PyFunction_SetVectorcall。必须核实际分派指针来源，不能仅验 function.__code__。

标准路径中的不同阶段不得混为一谈：

1. **D0：最终验证到原调用尝试。** caller fact/source 必须在这一消费点有效；只在早期 Python 锁区核过不够。
2. **D1：frame 容器、f_code 与位置参数建立。** `_PyEvalFramePushAndInit` 先读取 code、分配 frame，再 `_PyFrame_Initialize` 持有 f_code，随后 `initialize_locals` 又读 func_code 并初始化参数。固定 exact function、完整位置参数、无 kwargs/varargs/varkw/kwonly 的正常路径，与可能比较 keyword、补 defaults、创建参数容器或报错清理的通用路径必须分别证明。不能把 generic 路径的风险无条件归到固定正常快路。
3. **D2：closure cell 身份复制。** `COPY_FREE_VARS` 在 default evaluator 中从 function.func_closure 复制 cell 身份。`f_code` 已有强引用不等于 closure 已复制；cell 身份已复制也不等于其可变 cell_contents 永远保持。
4. **D3：原 body 首条及后续执行。** 自定义 PEP523 eval_frame 可以在 default evaluator/COPY_FREE_VARS 前取得控制；RESUME、监控和 pending 等又是不同阶段。不能把“到D0/D1的条件保证”扩写成“首body或整个body都保持来源阶段有效”。反之，也不能把活动 frame 已建立之后的事件误记为 frame 建立前发生。

CPython3.12.10 的 `_PyObject_GC_Link` 只安排GC，不能把本版每次 PyTuple_New/PyDict_New/MAKE_CELL 分配一律写成立即 GC Python 回调。pending、GC执行、GIL drop 的真实检查点必须引用对应官方路径。内存分配失败/错误清理中的 DECREF 与正常成功路径也分列；失败未 dispatch 不代表失败清理天然没有副作用。

后续设计必须为每条原边明确需要 D0、D1、D2 还是 D3：既不能默默将原更强契约降为D0，也不能凭空要求目标整个执行期间不可变。若某原边要求D2/D3而存在合法回调窗口，必须提出同等强度且保持原行为的方案，或明确记为未解决。不得通过关闭系统 GC/trace/monitoring、替换全局 eval hook、直接建立/改写 frames，或一律拒绝所有合法原调用来冒认修复。本批没有执行、配置或许可这些动作。

## ADR-1108-4：原根登记、后代代码与发布协议先于实现

后续源码设计至少完成以下来源链：

- **N0 native 自身根：** 原 native 入口及其模块/初始化/加载路径从哪个已接受 bootstrap 边界取得，expected 怎样强持有，实际 alias 怎样比对。读取当前模块属性或当前 function.vectorcall 重新 pin，不解决原来源。
- **N1 原 code 根：** 从此前已认证并强持有的原 factory CodeType，沿确切不可变 co_consts 槽及父子路径导出 expected descendant code；逐项记录原 producer 与实际新 function，不从待核 subject 当前 code/co_consts 洗白。r1107两 inner local 的词法来源只支持这条方向，不代表完整槽表已经建立。
- **N2 原 environment：** 原参数、defaults、kwdefaults entries、实际 closure/cell/source fact/current owner 与 code 分别绑定；新记录分配、强引用和任何可回调准备后的 actual-return/STORE/raw-effect 关系必须再次核对，不能只证明 code。
- **N3 阶段与同步：** 逐原边保持锁域、锁序、同锁再入和解锁时机；潜在等待必须在终段前，终段后不能解锁返回Python再冒认仍连续安全。不能新增foreign锁嵌套、缓存成功或跳过原partial状态。GIL不能替代尚未证明的原锁语义。
- **N4 释放及错误：** 保留原异常类别、原 fail 调用时机/次数、bare raise、原实参求值异常，以及17 lease/1449 slot/406尾等已接受边界。成功后的引用释放与失败后的清理需单独追踪；不得让新检查的错误被旧catch误吞，也不得把早期guard移到原STORE之前改变产品失败。

以上是下一批设计输入，不是通过本文件宣告它们闭合。暂不新增依赖、不改 pyproject、ABI、导入顺序、正常库或服务。

## 构建、兼容与交付成本

`ROOT-BUILD-CONSTRAINTS-01.json` 保存了实际只读观察。pyproject声明 `requires-python >=3.11`，无 build-system 或 C 扩展配置；定向排除生成目录的源码清单未找到 setup/CMake/meson/C/C++ 配置，不能扩大成整台机器无工具。

当前解释器为 CPython3.12.10，扩展后缀 `.cp312-win_amd64.pyd`，Python.h 和 python312.lib 存在。vswhere找到 MSVC14.44.35207 的 cl.exe，未执行；PATH没有解析cl/clang/gcc/ninja，解析出的是两个 cmake pyenv shim，未验证它们运行。当前环境的 setuptools/wheel/build distribution metadata 缺席，未安装任何包。

因此本机具备部分构建材料不等于项目已支持 native。尚需独立设计源码包/wheel/加载失败/升级回滚/多Python版本和平台矩阵。保留 `>=3.11`；本批仅证明3.12.10源码路径，不能悄悄降支持范围或宣称 stable ABI。3.12非limited接口、下划线API和内部字段均有版本维护成本；尚未评估其它版本，不以乐观fallback替代等强度保护。

## 后续落地顺序及停止条件

1. 固定现有五入口和直接相关catalog，逐实际原边建立 D0–D3 契约、原实参形状/默认值/闭包/异常矩阵。使用静态源码与既有 raw 元数据，不重复运行候选或重新compile旧输入。
2. 在同一份设计里完成 N0–N4、各版本/平台支持策略、native自身原根及后代code槽来源；显式列所有未闭义务。有限调查不能折算整认证图覆盖。
3. 主代理及同一只读代理分别复审确切设计。实质未闭时只修设计，不能因本批材料复核无错而生成或执行产品候选。
4. 只有可实施的完整设计获两审后，才形成完整候选、可逆差异与源码两审。完整认证源准入前，不 import/运行候选、不跑局部 bootstrap/control 模型、QA、旧绿/F001。任何新syntax compile也需要确切新输入与未耗claim。
5. 发布/真实库/服务等后续动作按原方案与已有授权推进；本批没有完成这些步骤。保留原预算：case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2 pytest children/native20–25–10。

## 本批结论和限制

本批排除了有限getter重读和无证明的通用native分派，给出联合验证/实际调用必须满足的来源、窗口、同步、兼容和交付契约。**完整修复设计仍未闭，不能实施产品候选。**这属于具体设计工作尚未完成，不是需要重复请求用户批准，也不是已完成修复只等验收。

正常库、service/dist、ACL/settings、真实平台、Git保持。r920/r964已耗不重放；r967待原问，不执行不重问；P06历史0/7，用户已明确没有材料，不再索取。禁profiling、真实frames/locals检查、停点、定点计时、新异常链诊断/runtime journal。Task9A/9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal均未完成。

# 下一批：固定原边契约和可信根，完成可实施设计

源码父仍r1106 SOURCE02完整159，文档父为r1108 DELIVERY。先读本批ADR、两份设计材料复核、独审CALL-PATH与Root构建记录；本批材料无错不等于完整修复设计获准。不得新建另一个通用native dispatcher然后用组件模型替代未闭的全认证。

1. 以r1107五入口及直接相关catalog为固定范围，逐真实原producer/consumer建立最终metadata与caller fact需要在哪个消费点成立的契约。区分D0实际call尝试、D1frame代码参数、D2closure cell身份与D3首body；查原设计要求，不能默降也不凭空升级。将原实参形状/defaults/kwargs/生成器/异常路线列全，不重复24factory运输或两个innerlocal来源调查。
2. 在同一设计内完成N0–N4：native自身original入口和初始化根、原factory CodeType强根到后代的确切co_consts槽、原环境/actual-return/STORE/effect、锁顺序和实际调用，以及原audit/异常/释放。不能从待核当前对象重pin，不以有限getter重读/仅返回snapshot充当最终闭环。不改系统GC/trace/monitoring/eval设置，不检查或操作真实frames/locals。
3. 给出>=3.11及当前平台的明确兼容/打包/加载失败/回滚策略；本批3.12.10源码事实不证明其它版本，不静默改项目支持范围。无native build/install/import授权由本批材料自动产生。
4. 同一只读代理复审确切可实施设计，主代理独立核对；实质未闭先修设计。只有完整设计获两审后才形成完整新源码候选及源码双审；完整来源准入之前不import/运行候选、局部bootstrap/control模型、旧绿/F001或QA。新syntax compile需确切新输入和未耗claim，旧compile不重放。

保护56同18缺与原17lease/1449slot/406尾/五S002边界保持。正常库/service-dist/ACLsettings/平台/Git不动。r920/r964耗不重放、r967不执行重问、P06不再索材料。原预算与禁止项不变；完整Goal active未完。
