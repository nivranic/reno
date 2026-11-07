# r1107 原审计读取与实际消费路径有限静态调查

r1107完成五个具名入口及直接相关catalog的有限静态调查，确认连续审计读取的metadata一致性与caller来源阶段freshness两类源码缺口。两处inner local factory已有词法来源，不重复24项已完成运输；源码仍r1106 SOURCE02完整159，未执行，Goal active未完。

## 本批范围与实际证据

源码父r1106 SOURCE02、文档父r1106 DELIVERY。先核上一批371封存文件及159候选当前pin，冻结19份完整输入36248381B；补充相关原catalog完整源码19366B，共20个原/冻结pairs、36267747B。未修改159源码，未生成新候选、未compile/import/执行、未执行hook/反例/模型/QA/native/SQL/Store或诊断。保护56同18lstat缺席。

Root负责control._check_function及两处inner local producer，并读取直接相关maker/caller/catalog；原同一代理负责scope._authenticate及registration._check_stack_class。调查职责分工，不冒称两份完整源码审。Root四个完整AST子树调用库存601/1234/495/580，外层含后代、相互重叠，不求和成唯一调用数或运行次数。独审两完整定义scope339/reg215，其中各2项在genexpr体；当前函数自己的句法call为337/213。两边均从冻结完整源码和原raw元数据派生定义/调用库存，并保存AST/text摘要与原producer/actual getter/call/return坐标；完整原件在冻结输入，独审另保存完整AST表达。

## 已确认：连续审计读取不是一致metadata快照

已保存官方CPython v3.12.10 funcobject.c463–469、549–557、589–598明确：function的__code__/__defaults__/__kwdefaults__ getter先PySys_Audit，再读取对应当前字段。__closure__/__globals__/__builtins__是readonly成员。原code setter473–513要求CodeType及同freevar数量，并有独立setter audit；既有锁和对象身份不自动禁止同线程回调写这些可变字段。

control._check_function1256–2335中，subject code1351先比较，defaults1368、kwdefaults1385后审计，closure1402后读。Root用两份已存在原code给出单位契约静态反例：原_fail code7与原_check_function code12均1参数/1个_ac_state freevar；给原_fail的合法七项pin，在其defaults审计时仅把subject code换成原code12，其defaults/kwdefaults为None、closure/cell原context保持。原checker对象不改，后续正常cell检查可通过并隐式结束，但subject当前code已不等于pin code。未生成替代code或payload，未执行hook；不声称原_FUNCTIONS实际含_fail pin、不声称整包/真实route已触发。

scope helper014工具code833→defaults834→kw835→实际tool call881，helper0151068→1069→1070→1116；registration helper0281809→1810→1811→1857也有同类后审计失效窗口。替代tool5 CodeType仅作为满足closure数量/调用ABI的条件轨迹，未发现/生成既有可用替代体，不能把条件轨迹写成已执行利用。原catalog.check_view194–390自身也连续读取row9 metadata；它不是无callback原子检查的依据。

## 已确认：caller自身来源阶段在后续audit后未再次检查

Root的checker自身slot在1264–1290原锁内核phase2，但后续subject defaults审计1368若只将该原slot phase改3，正常路径未重核该phase，可到隐式返回。

三条caller路径也可在保持工具/目标metadata不变时单独说明：scope helper014/015最后核caller自身fact约790/1025，registration helper028约1757；后续工具defaults审计834/1069/1810可把已持有caller source fact[0]从2改3。真实tool5 view只含control incoming function row，roots/bindings/modules/classes为空，不包含该caller source fact。工具可返回同原target，identity门882/1117/1858通过，实际call884/1119/1860仍被尝试。这条轨迹显式假定回调事先持有确切fact列表；未通过frame/locals/profiling或运行观察证明访问获取。结论限于该门未fresh及call尝试，不证明callee正常返回或产品route可利用。scope原_PINS[2]读629–632后可能还有class gate复核，不能省略这些后续边而宣称整个认证已绕过。

## 已建立的来源保证

control两处inner factory（def2722/call3561、def3564/call4603）由真实_make_class_authentication活动帧内原def产生，原raw证明STORE_FAST及原fastlocal实际call；首capture均读该帧原_ac_context_anchor，后代仍持原profile_class词法cell。此处没有本批证实的module alias认领原身份问题。各自body中工具metadata验证及publication/freshness仍独立待闭；不把这项词法保证扩张成整个body可信，也不重复24个已完成的外层运输。

Root调查工具初稿把嵌套generator helper的Return误计为checker自己的Return，写报告前断言停止；已确认报告缺席，原01与TOOL-STOP保。02仅修AST归属过滤后完成，候选compile/执行消耗0。该工具错误不冒称产品运行失败。

## 下一步工程约束

两类缺口尚未修。下一批先设计同一真实消费点的来源阶段复核及metadata/实际分派保证，不能只追加有限轮getters或把code挪末尾就宣布多个可写字段同时当前一致。在允许audit回调修改先前字段的模型中，最后一次audited read仍可使较早字段失效；须给出无callback的combined验证/实际dispatch机制、可证明的修改排除机制或同等方案，并核原余体、错误与释放语义。native实现若作为候选必须独立论证其真实原source、GIL/GC/audit/分配/实际callee读取及ABI边界，当前没有批准或实现native方案。不能默默缩小既有威胁模型，也不以相关catalog函数自身连续读取作为原子保证。

完整来源认证、完整EDGE/PHASE及后续工程仍未闭，本批仅有限静态调查，source/executionFalse、产品pass0。本批canonical apply0/历史11。正常库/service-dist/ACLsettings/平台/Git保持；r920/r964耗不重放，r967待原问不执行重问，P06历史0/7不再索材料。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal未完，非只剩验收。

# 下一批：针对明确原边设计修复

源码仍r1106 SOURCE02完整159，文档父为本批DELIVERY；没有r1107新产品候选。先读ROOT/INDEPENDENT-INVESTIGATION01完整报告及本批CURRENT，不重做24factory运输，不把两innerlocal词法来源再列待修。

1. 将两类已确认缺口落成修复契约：subject metadata的读取/消费，caller自身source owner/fact在工具审计后到actual call之间的阶段有效性。固定control1256–2335、scope helper014/015、registration helper028及直接相关catalog.check_view194–390。保原真实pin/source/actual参数/return/publication和既有错误、锁、清理/partial分支；调查五入口不是全认证穷尽证明。
2. 明确拒绝把连续audited getters的有限重读当作一致快照证明。研究无callback的combined验证与实际dispatch或同等可证明机制；若选native组件，先查现有构建/发布约束及官方CPython真实读取/调用路径，列GIL、GC、分配、audit、source/callee/defaults/cells及版本ABI，不预设native本身天然安全，不在方案前执行或导入候选。若仅修caller phase，也必须明确metadata缺口仍红，不能替代完整最终状态。
3. 用同一代理先双审确切设计，实质红先修；完整候选与源码双审的原全认证红仍保。完整认证源准入之前不import/运行候选、局部bootstrap/control模型、旧绿/F001或QA；任何以后新compile/整包QA须有新的确切输入和未耗claim，现有已耗compile不重放。

原17lease/1449slot/406尾及五S002边界、原skip/False/deferred/repeated导入语义保持。正常库/service-dist/ACLsettings/平台/Git不动；r920/r964耗不重放，r967不执行重问，P06用户无历史材料不再索取。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal active未完。
