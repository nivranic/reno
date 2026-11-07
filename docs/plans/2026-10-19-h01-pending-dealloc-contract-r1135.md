# r1135：已有 pending first 的有限还原合同

r1135以同版tagged网页的deallocator pending合同及完整errors.c，确定已有first时的有限还原路线：私有C局部持有实际异常，先结清本层全部owned/temp/free，再安装first并释放old E2，最后仅scalar返回。first按层选取，三cell getter错误仍由原handler处理，原工具/调用方finally覆盖保留；附加cleanup E2选择不额外报告、不覆盖本层first，逐site COMPAT待审。reply分配/自有类型/NULL→NULL/allocator/build与共同N4仍待，B001/B002 open2；完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

新依据为v3.12.10官方网页片段124词，坐标不是原rst行号；raw单次RemoteDisconnected已终止不重试，未获完整tutorial文件。errors.c三段及public ownership文档、三处原cell Try完整AST分列；只证明有pending且实际release包络合规时的有限退出，不覆盖3.11、所有build、异常内部字段或source/phase不变。226普通候选叶无自有清理仍直接透传NULL；owner14仍独立。

完整输入和父before全文复用，after/preview/逆像保留。159源/保护56同18缺/预算禁项保持；本批candidate/compile/import/运行/native/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。完整Goal仍有工程，非只剩验收。

# r1135：已有 pending first 的有限还原合同

父批 r1134 已封存，不追加或修改。产品仍为 r1106 SOURCE02 的159文件、7,956,657 B，未安装。本批只确定一个条件设计：本层已有实际失败时，怎样跨本层附加清理运输同一个异常。它不完成 reply 分配、native 类型、全版本 ABI 或共同 N4。B001/B002 仍 open2，完整 design/source/execution/Goal 均 False。

## 1. 新依据及其准确范围

本批已有 `TAGGED-WEB-EVIDENCE-01.json` 冻结官方 GitHub `v3.12.10/Doc/extending/newtypes.rst` 页面提取行246–247的124个原文词。页面两处版本为 v3.12.10；文字明确要求 deallocator 保持已有 pending exception，对可运行 Python 的危险动作先保存、后还原。这里是同版 tagged 网页片段，坐标是网页提取行，不能称为原 rst 行号、完整 raw 文件或本机 binary 证明。新选 tutorial 的单次 raw 下载以 RemoteDisconnected 终止，不重试；旧失败下载也不重放。

完整既有 v3.12.10 `Python/errors.c`（55,402 B，SHA d38cf667e13fb9f298a278759daf6117167f45a08d2c5bae7f279d9ba86a2454）给出三个独立事实：30–36行的 setter 先安装 incoming actual exception，再 XDECREF old；117–122行 public wrapper 直接转发；497–509行 GetRaised 取走实际 owned exception 并清 indicator。public C API 文档还明示 Get/SetRaised 的 new/stolen ownership，见既有同版 exceptions.rst 428–461行。

这补充 r1132 `MAY_LAST_WITH_CONTRACT` 的一个有限前提。它直接约束 deallocator，不能自动覆盖 Py_XDECREF 外层 debug/limited/stats 包络、任意 allocator/free、任意 tp_clear 或未来自有类型实现。实际 runtime/build 和所有 release 包络仍须匹配；本批不执行 refcount/frames 探测、不构造运行时反例。3.11.13 Restore 的三个 old slot 在安装新三项后释放，是另一份静态源码事实；本批3.12文档不足以单独证明3.11通用合同，不据此收窄项目 Python>=3.11。

## 2. first 必须按当前 C 层定义

first 是本层决定失败返回时真正逃逸的那个实际异常，不能只按出现时间取最早异常。原业务 handler/finally 在它自己的 Python 层自然完成，调用方原 finally 之后仍可合法覆盖；本方案不承诺跨层永久 first 优先。

| 层或出口 | 允许保留的 first | 后续原语义 |
| --- | --- | --- |
| 普通226候选叶，无本层 owning 清理 | 不新增运输；原操作 NULL/indicator 直接返回 | 原 Python handler/finally 自然处理 |
| CELL_TRY 原 getter 失败 | 只是这个 CELL_TRY 层的 actual getter error | 原 late MATCHER 仍可将 ValueError 作为 empty；它不自动成为整个工具的最终错误 |
| CELL_TRY 自有准备失败 | 该次 reply/本层准备实际产生的逃逸异常 | 不冒充 getter error；是否会落入旧 catch 仍须完整错误矩阵证明 |
| MATCHER 自身拒绝 | 该层认证/错误构造的实际失败 | 在原 except 头自然逃逸；原正在处理的 getter exception 与 current indicator 不混同 |
| CELL_ACCEPT denied 或自身失败 | try 外 accept 层实际失败 | 不重新进入旧 cell getter catch；原工具剩余 finally 仍自然完成 |
| 原完整工具调用返回 NULL 后的外层 C | 原工具所有内部 handler/finally 已完成后的 actual escaping error | 该外层仅清自己的资源；返回 Python 后调用方原 finally 仍可覆盖 |
| 原工具成功、TMD 拒绝该实际结果 | TMD 本层新拒绝或错误构造失败的 actual exception | 不是原工具异常；先定义实际错误再处置 owned result，具体构造合同尚待 |
| PREP_NULL | 依 r1131 的实际 NoMemory 新失败 | actual callee call 为0，不称为原 callee OOM |

调用原完整工具失败与 TMD 成功后拒绝是互斥出口。不得在原工具已失败后另跑成功 TMD，制造错误去覆盖原工具异常。

## 3. 有 first 的有限退出顺序

仅对满足下列全部条件的失败出口选用此路线，不为无自有清理的普通叶额外 Fetch/Restore。

1. 本层取得 actual first 时，使用实际 `PyErr_GetRaisedException` 将 current indicator 的 owned 引用移入**私有 C 自动局部**。该引用不是借用、不是重建异常，也不额外 INCREF。first 非 NULL、有效异常类型和该次实际错误来源必须成立；违反 C API 返回合同另列错误，不能捏造 first。
2. 这个私有局部不发布给 Python，不成为 A/J/Product/bank/retirement 可以清除的共享 slot，不保存在被回调可变的容器。它跨本层全部可回调清理保持同一个真实强根；不读取或修改真实 Python frame/locals。
3. 按各资源的真实取得顺序、依赖和退出合同先完成全部本层责任。owning slot 先脱离再释放；脱离后暂存的 owned temp 仍是一笔责任，必须真正释放或合法移交。先恢复 PREP 未提交 runtime 状态再 free；已经 committed 的 chunk 归解释器，不再访问或释放。原业务锁/handler/finally 的顺序仍由原算法负责，不搬成新外层保 first 的手段。
4. 允许这些本层附加清理在 indicator 中留下 E2，不要求未经证明的 NULL→NULL。但每一步仍须有正常返回、状态一致、后续清理前置条件及错误政策，不能以最终 restore 为由忽略中途要求 indicator 为空的 API。不得用无限 drain、泄漏、quarantine 或后台清理补洞。
5. 确认所有 owning slot **及其 detached temp**、reply/partial、rejected result、guards、bank/Product、锁和 raw reservation 均已按本层职责结清后，执行 `tmp = first; first = NULL; PyErr_SetRaisedException(tmp);`。这是一次 owned 引用移动，SetRaised 的 stolen 接收不新增 acquire；tmp 随即失去拥有资格，不能再 DECREF 或归还共享槽。
6. setter 先把这个 actual first 安装成 indicator 的强根，再释放 old E2。若 E2 非 NULL、release 包络匹配且其实际 deallocator 遵守同版 pending 合同，释放必须保持已经 pending 的 first。因此只处理 setter 自带的一个 old，不需要 E2/E3 无限排空。E2 与 first 同 identity 仍是两笔实际 owning 责任；安装 first 的那笔根覆盖 old 的释放，不去重、不漏释放。
7. SetRaised 后本 C 实现只允许 scalar `return NULL`。不得再 DECREF/Py_CLEAR/free、释放锁、调用 getter/日志、退 bank/guard、检查成功回退或进行新的业务调用。调用框架返回后的清理属于外层，仍须另审，不能被“本 C 层纯返回”代证。

first 的对象 identity 保留不表示 traceback/context/cause/attrs 被冻结，也不保证回调未改 source/phase/metadata。失败路径不借清理后状态重试业务；成功路径仍须 fresh 准入，不能沿用失败保存策略作来源证书。

## 4. E2 政策及观察差异

本批选择的有限政策是：**本层附加 native 清理产生的 E2 不覆盖本层已确定的 actual first；最终由 setter 释放当时的 old E2，不另加日志或 unraisable 报告。** 原业务 handler/finally 产生的最终错误不属于此处“附加 E2”。

如某资源原有 cleanup 必须报告或传播自己的失败，须在该 site 的原合同下另选路线；本批政策不默改它。原 deallocator 自身已有的 unraisable 行为也不删除。若后续确定需要新增报告，必须在 restore 前可回调区完成，并重新证明所有责任与 first 强根。当前未发现八条原要求指定本层新增 E2 日志，但未据此宣称所有观察兼容已完成。选择丢弃附加 E2 是显式设计变化，逐 site COMPAT 仍 open。

## 5. 三 cell 出口与 owned 责任

三个原点为 function_row59、class_row131、check_view322；其完整 Try/Except/Else AST 和原文纳入 `LAYER-OWNERSHIP-01.json`。前两处 empty append `(cell, False, missing)`、成功 append `(cell, True, contents)`；check_view 的 empty 分支按 populated 判断，成功分支按 populated 与 actual identity 判断。不得统一成 sentinel 或抹平分支。

| 路径 | actual 资源及结清要求 | 本批未关闭的点 |
| --- | --- | --- |
| CELL_TRY 原 getter NULL | 无资源时直接 NULL；有额外 owning 责任才保这个实际 getter error、清本层后还原 | 是否真有额外责任、原 late matcher 和 C入前异常 |
| getter成功且success reply成功 | getter actual new reference 转移给本次独立 reply；不在转移后再释放 contents | reply 创建时点、GC/回调、source关联和类型实现 |
| getter成功但reply准备失败 | 实际准备异常是本层 first；尚未移交的 contents、partial reply、其他 owned temp 全须结清后还原 | 哪一步 acquire/steal、partial销毁不能 double release；内部失败不得假装 getter ValueError |
| admission拒绝且denied reply成功 | 本次独立 opaque reply，不运行原 getter；最终在旧try外 ACCEPT拒绝 | 独立产品构造与身份运输，不能保存最近reply/first-use资格 |
| denied reply准备失败 | 实际准备失败归本 C 层，不能虚构 denied成功或原getter已调用 | 具体分配/错误类型与旧try不误吞尚未闭 |
| MATCHER成功/失败 | 成功 C返回的已核 original ValueError class也须真实owned引用移交；失败仅清自己的资源 | Python异常匹配仍用原时点；handled exception与indicator不能混用 |
| ACCEPT success | actual contents 以真实owned output移交；reply由真实caller引用保持，不能靠borrow返回新引用 | 成功前若释放自有引用可回调，需再fresh；重复观察不消耗一次性资格 |
| ACCEPT denied/失败 | first来自try外该层；reply的borrowed argv不得擅自DECREF，自有extra refs/partial先清 | caller退出时reply释放仍是外层责任，不被本C纯返回覆盖 |

first保存不能解决“新准备错误在旧try里被吞”的分类问题，也不能把所有旧try内异常改成 reply。仍保持 r1128 的 actual getter自然抛、late MATCHER、try外ACCEPT 和独立reply；不引入外部可先消费的可变 reservation 或 first-wins 状态。

## 6. 外层结果拒绝、PREP 与退休

外层必须持有这次 actual tool return 的 owned result，不能以当前槽重读、另次reply或snapshot代替。TMD认可成功才转移它；TMD拒绝时它可能是 sole-owned，不能假称借用或非last。若已确定本层 actual refusal first，则按本合同先清 actual result 与所有自有资源，再还原；actual result 若已转移给 partial容器，就只清真实owner，不重复DECREF。TMD错误构造、清理重入与返回前 fresh/site COMPAT 仍须逐步接线。

native A/J/Product/source bank/owner14 的 clear/traverse/dealloc 尚未实现，不能先假设合规来批准自己。表中私有 first 根只解异常运输，不能覆盖其他资源的可达性、退休环或未知分配成本。standalone arena free 必须在 final restore 之前，first根覆盖它；这不证明该free的并发/状态/indicator合同，也不批准更改allocator。NULL→NULL、原成功出口的 pending清洁、3.11合同和所有build包络仍未关闭。

## 7. 交付及下一步

本批复用既有完整输入及r1134封存的 before全文，保留本批after、完整preview及逆像。Root与同一只读独审分别检查材料，不把有限材料零阻塞写成 B001/B002关闭或完整设计准入。正式脚本只做静态读取、hash/AST和文档交付，不导入/compile/执行产品，不重跑任何下载或诊断。

下一步完成三个reply的逐acquire矩阵及自有类型合同、外层TMD实际拒绝错误构造、source bank/owner14退休，再与原D1/D2/P0/38context/CORE/import/预算/COMPAT组合。原159源码、保护56同18缺、17lease/1449slot/406尾/五S002保持；case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不放宽，control1MiB不是确认native heap上限。

完整设计双审前不生成产品候选，源码准入前不产品compile/import/执行、native build/load、模型、旧绿F001、QA或诊断。正常库/服务-dist/ACLsettings/真实平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964已耗不重放，迟到授权不触发执行；r967不执行不重问，P06历史0/7无材料不再索取。apply本批0/历史11、源码缺口关闭0、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云和完整Goal仍有工程，非只剩验收。

# 下一批：把reply与退休责任接到实际取得点

沿r1128/r1129/r1134/r1135完成三处CELL_TRY的actual getter、独立success/denied reply、partial allocation、late MATCHER、try外ACCEPT的逐acquire/steal/release矩阵。保两个row的empty/success append与check_view的populated分支，不用first政策遮掉旧try误吞新错误问题。每个 C 层只保其实际逃逸错误；完整工具与调用方原finally仍自然决定后续错误。

外层TMD在原工具成功后拒绝时，先确定该层实际拒绝/构造错误，再结清actual owned result及所有detached temp；成功路径不得带pending返回。普通226候选叶只借真实argv，7原append保持，owner14继续独立C producer/work root/bank9证明。first只在私有C自动局部持根，不成为共享retirement可清槽；SetRaised后只scalar NULL，本C返回后的框架/调用方资源仍各自负责。

实现native reply/Product/A/J/bank/consumer的真实traverse/clear/dealloc合同前不把“应当合规”当“已合规”；逐site选择附加E2政策、原有报告与COMPAT。继续NULL→NULL、allocator/free、3.11/build、D1/D2、P0/import/CORE/38context及预算组合。完整设计双审前不生成产品候选，源码准入前不产品compile/import/执行/native/模型/QA/诊断；所有原保护、有限额、禁项、耗尽单次授权及P06无材料边界保持。Goal active，B001/B002 open2。
