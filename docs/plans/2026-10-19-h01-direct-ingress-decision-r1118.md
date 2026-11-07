# r1118：可写 globals 直接 C 入口裁决

r1118完成一个具名DGI入口的有限静态裁决：loader将原C入口及opaque attempt写入原G，再经原helper/builtin exec执行。原exec audit仍位于module执行前，条件回调可替换该alias，unknown callable可能先执行；C内后验太晚，因此不采用这条接线。r1117条件环境读取保持，真实入口/原调用关联仍未闭。B001/B002保持open2，完整设计/source/execution均False；源码仍r1106 SOURCE02完整159，Goal active未完。

18完整输入pairs每侧3583660B，7段准确官方原文、三源AST、父r1117封存58文件6949413B、159源码7956657B及保护56同18lstat缺席核同。没有为新接线生成code或执行反例。exec audit仅传source，G可达性限定为原module提前发布或已持引用，不冒认事件直接提供G。

I002分三策略：首个匹配能力就填槽/耗token不能授原事件来源；幂等复用不能补首次producer来源，但后续原调用可实际返回同对象；每次fresh且无共享消耗不适用该抢占反例，保留为未完成方向。不能因foreign可调用就要求识别任意caller或否决全部native。完整actual return→sourcecaller local→原STORE/effect、P0两次环境、23P2/CORE第24项、联合metadata/三D0/D1/D2/N2/N4/COMPAT仍待。

下一设计必须给独立于可写G发布的调用前原根通道，并将fresh产物与原消费者绑定；不再以同DGI加hash/pin/token/后验重复申请候选。完整设计双审及源码准入前不跑局部模型/QA。材料0与完整方案open2分开，源码缺口关闭0、产品pass0，非只剩验收。

本批产品candidate/compile/import/执行/native build/模型/QA/诊断0，canonical apply0/历史11。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal未完。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。正常库/服务-dist/ACLsettings/真实平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放、r967不执行不重问、P06无材料0/7不再索取。

# r1118：loader 写入 globals 的直接 C 入口方案裁决

状态：有限静态裁决，承接已封存 r1117。仅审一个具名 DGI（loader 注入 globals 入口）方案；没有产品源码、运行模型或新诊断。当前完整产品候选仍为 r1106 SOURCE02 的159文件。r1117 的条件环境读取结论保持，但其直接入口前提没有由本方案建立。

## 1. 被审方案必须具体到调用前

DGI 拟让 r1112 的实际 native producer 创建原 C callable C，将 actual 返回先强持于本代 state；r1111 的 loader wrapper 对每次原 get_code 返回 code K、原 module globals G 建立独立 attempt A。wrapper 在调用原 helper(original_exec,K,G) 前，将 C 和 opaque A 分别发布到 G 的新键 `_AUTH_NATIVE_INGRESS`、`_AUTH_NATIVE_ATTEMPT`，并核 effect。未来修改过的原源码在 module 帧活动期经这两个名字进入 C，再在 C 内核 pointer/self/generation/A/K/G 后取得环境和生产 P0/P2。

这些名字是本方案新提议的通道，不是当前源码已有的可信 native 入口。r1117 的三段尾调用仍为两参数 Python constructor；本批没有修改这三段调用，也没有生成带上述新通道的派生 code。完整 P0/23P2/CORE、原 context/STORE/del、ABI、预算与回收在本裁决中均未实施。

唯一待裁决命题是：**state 已独立持 C，且 wrapper 曾正确写入 G，是否足以让稍后的源码调用在 unknown 执行前取得原 C，并绑定该次原调用？** 先写 alias 再检查不等于该命题成立。

## 2. I001：一个不需要逐指令竞态的静态反例

下面是允许既有 audit callback 的条件控制流推导，不是已经执行的利用、测试或诊断。不注册 hook，不查看真实 frame，不调用 unknown hash/eq，不要求回调收到 G 参数。

| 时点 | 具体对象流与官方依据 | 对 DGI 的影响 |
| --- | --- | --- |
| t0 | 原初导入先将 actual module 发布到 sys.modules，随后才调用 loader.exec_module；既有外部引用也可能直接持有该 module/G | 回调取得 G 的条件可以来自原发布或已持引用；exec audit 本身仅给 source K，不能误写成 audit 参数直接含 G |
| t1 | wrapper 已实际持有 C/A/K/G，并完成两项 exact-string G 写入及自己的 effect 检查 | state-held expected 可靠不等于后续 G 当前值不可变；即使此处所有检查正确，仍有下一步 |
| t2 | 捕获的原 builtin exec 在真正 PyEval_EvalCode(K,G,G) 前调用 PySys_Audit("exec","O",K) | 原 audit 回调仍被保留；wrapper 的最后检查在这条回调之前 |
| t3 | 条件中已有的 callback 经持有的 G 将 `_AUTH_NATIVE_INGRESS` 改为一个合法 callable U，正常返回 | exact-string key、exact dict、GIL、opaque A 与包/module 锁均不禁止这次普通 G 变动；无需改 K 或越过 C state |
| t4 | 原 module 名称读取从当次 locals/G 获取 U；普通 CALL 实际分派取得的 callable | U 可以先执行；预期 C 内的 pointer/self/attempt 检查没有进入，不能回溯阻止已经发生的 U 执行 |

只需存在这一允许的分支，DGI 就不能满足本来要求的“unknown target 实际调用前拒绝”。无需声称每个 audit hook 都有 G、每次导入都会变更，或当前源码已经走到了新调用。原源码若在新调用前又重新建立 alias，则必须另给该建立事件的独立来源与到实际调用的路径，不能用未提供的前验修复来挽救被审版本。

官方 LOAD_NAME/CALL 原文在本目录 evidence 中保存。这里用其解释拟议名字表达式的语义，没有为新源码 compile 或读取新字节码。builtin exec 默认 locals=explicit globals 的原文也已保存，因此本方案没有借用一个额外私有 locals 通道。

**I001 裁决：否决 DGI 当前接线。** 这不否决 r1112 actual C 创建/state-held 来源，也不否决 r1117 在已证明直接入口时的 PyEval_GetBuiltins 子机制。失败点在“发布后的可写 G”到“调用前已持原 C”，两端之间没有独立的前验交接。

## 3. I002：能力相同与原调用事件分开

即使另行假定 I001 的实际目标确为 C，原 pointer/self、同代 A、supplied K/G 与 ACTIVE/HELPER_CALL_ENTERED 也只证明对象/阶段谓词，不自动证明本次是原源码的具名调用。DGI 把 capability 放进 G；持有 G 的条件回调可以取得同一实际 C/A，并在原 module 执行前或合法再入时调用它。exec audit 提供 K 的对象，K 的 equality/identity 入参也不是来源证明。环境 getter 返回当次 Python 帧的 builtins；普通共享 builtins 的合法情形足以说明它不是唯一的调用者标识。

必须分别审三种状态策略：

| 变体 | 可以确认的结论 | 不得外推 |
| --- | --- | --- |
| DGI-A：首个满足对象谓词的调用填 attempt/P0/P2 原槽，或消耗一次 route token | 外来调用若被这些谓词接纳，可先填槽/耗机会；将其标成原具名调用即 first-wins。后续拒绝或复用不能补原来源 | 不是已经实际执行的抢占测试；没有声称任意真实 C 实现都采用这条策略 |
| DGI-B：重复调用幂等复用先前槽 | 复用不能证明对象最初由该次具名原事件产生；但真正原调用随后仍可能实际返回同一对象。当前实际 return 与最初 producer/环境/首次来源必须分开核 | 幂等本身不是错误；仍须另证当前 actual return→sourcecaller local→STORE，以及原创建次数/CREATE/环境时点，不能从复用补首次来源 |
| DGI-C：每次 fresh 返回独立产物，不消耗共享原槽 | A 的“一次机会被抢”反例不能直接套用。此方向不因 foreign 可调用而被本批一概否决 | 尚无完整的 actual return→原 sourcecaller local→原 STORE/effect 与联合消费设计；N2/N4/预算及原 P0 环境仍待，不能仅凭 route 参数授“原调用已发生” |

原要求保护的是具名 producer/caller 与消费事实，不是任意 Python caller 的普遍身份识别。一个 fresh producer 若能在真正原消费者处独立绑定该次实际返回，未必需要在公共入口识别所有 caller；本批既不删除原来源义务，也不为阻止无关调用新增全解释器安全目标。拒绝所有合法调用或无限期占用原预算不构成正常兼容。

**I002 裁决：DGI-A/B 不能授原调用来源；DGI-C 保留为未完成方向，未选为完整方案。** I001 已足以否决当前 DGI，不需要先造一个有 first-wins 的运行模型。

## 4. 本批决定与停止重复的范围

不实施“写 G alias 即取得可信直接入口”的 DGI；不把入口内后验改名为调用前门；不跑局部 getter/producer 绿例。该结论仅针对上述通道及状态策略，**不是所有 C/native 实现不可能的证明**。不重开 r1113 const 注入或 r1116 R2 的已否决版本，也不把新的设计前提当用户已经接受的范围变更。

可继续的工程任务必须提出一个**独立于可写 G 发布的调用前原根通道**，或明确修订执行/信任边界并列全兼容影响；若仍用相同 DGI，只增加 pin、hash、opaque token、同代检查或事后报告，就不再作为新候选反复审阅。DGI-C 只能在该入口通道已具体化后，与原实际返回/消费者接线一起评审，不能单独执行。

完整设计仍须在同一版闭合 I003/I004：P0 两次环境求值、tools/rows/owner/context 原 RHS、catalog 第四目标及私有 ABI；23 P2 与 CORE 第24项；原 actual-return/STORE/del、联合 metadata/三 D0/D1/D2；N2 容量、N4 引用/回收与 >=3.11 构建交付。不得将材料审阅无误写成这些工程已通过。

## 5. 验证与边界

本批只使用冻结文件、准确原文和 AST；核父 r1117 的封存清单、r1106 完整159源码及原保护清单。原文锚与完整输入指纹见 INPUTS-01.json / ROOT-INGRESS-EVIDENCE-01.json。没有产品 candidate、compile/import/运行、native build/load、hook、模型、QA、SQL/Store 或诊断。

B001/B002 保持 open2；full_design_approved/source_approved/execution_admitted/complete_Goal 均 False。源码缺口关闭0、产品 pass0。Task9A9B、原111/94/r919、真实 Store、完整 P06、条件 T15 与生产云仍有工程工作，非只剩验收。

原17lease/1449slot/406尾/五S002及 case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。正常库、服务/dist、ACL/settings、真实平台和 Git 不操作。禁 profiling、真实 frames/locals、停点、定点计时、新异常链诊断、runtime journal；不改全局 GC/trace/monitoring/PEP523。r920/r964 已耗，不因迟到单次批准重放；r967 不执行不重问。P06 无历史材料0/7，不再索取同一批备份。

# 下一批：独立调用前通道与 fresh 产物的原消费者绑定

父r1118。当前源码仍r1106 SOURCE02完整159；原r1111执行器及r1117有前提环境机制保持，DGI alias-only接线已否决。

1. 仅提出一个具体的独立调用前原根通道。必须把native实际构造/state-held原根运输到源码实际目标消费前，列出中间每个可变槽、可能callback及前验。不能把原G写入/当前alias相同、C内检查、code/hash/token当通道已经闭合；同一DGI没有新增独立通道时不生成新候选。
2. 对每次fresh且不消费共享原槽的方向，明确actual返回如何进入原sourcecaller实际local并到原STORE/effect，expected来自何处。foreign可调用本身不是否决理由；capability/阶段相同也不证明原事件。原创建次数/CREATE/环境与当前actual return分别处理。
3. 如需变更原执行/信任边界，先给具体可审的完整差异、原合同依据与兼容/错误/引用代价，不偷偷重开r1113 const注入或r1116 R2已否决版本。完整设计须联合接P0/工具/owner/context/ABI、23P2/CORE、联合metadata/三D0/D1/D2/N2/N4/COMPAT。
4. 完整设计双审前不造产品候选；源码准入前不compile/import/执行候选、局部模型/native build/QA/旧绿F001或诊断。原保护/预算和完整Goal未完口径保持；不重复索取P06历史材料或旧已耗单次授权。
