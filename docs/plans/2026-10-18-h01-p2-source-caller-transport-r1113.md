# r1113 P2 原源码 caller 运输静态调查

r1113完成P2原源码caller运输静态调查：三既有constructor的实际参数/工厂临时值是函数局部，C位置参数到localsplus有原文路径。23顶层P2与24原tail行分列；const注入因改变exec原根否决。post-helper方案仍有P0实际context和builtins来源两项阻塞，未获实施批准。完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

本批冻结29完整原/副本pairs，每侧3922474B；核父r1112封存83及当前159源码7956657B，保护56同18lstat缺席。9份继承官方CPython3.12.10文本516805B/16准确原文和三源AST库存核同，无新下载或产品候选。原28份/14段之后补齐frameobject.c及当前frame fallback两段，前记录保持原范围。没有compile/import/运行、可执行CodeType、marshal-load、native build/load、hook、模型、QA、SQL/Store或诊断。

control/scope/registration原constructor参数和_factory_actual等不在538/399/252项global声明中；原tail行与实际factory调用分别10/8/6，共24，顶层P2仍9/8/6共23，control CORE另列。C实际位置参数可经原vectorcall/evaluator原对象引用进入localsplus，但尚未改私有caller协议或验证新候选布局，不将实际参数运输外推为D1或全body不变。

const注入会使exec audit收到派生code，并可被audit提前读取/调用token；不满足当前same-code合同，未选用，也不以first-use/抢先占槽认caller。未调查codeobject/GC/pyc的部分明确未知，不凭记忆报额外漏洞。

保留的C直接调用原sourcecaller方向仍非完整接线。post-helper候选的B001是原尾context NamedExpr的actual RHS若改G槽后再取会丢失原直接参数运输；B002是PyFunction_New在原helper返回后可能回退外层importlib builtins。原首部del/template、CREATE watcher、doc/version及明确frame/失败时机差异均须配套。调查材料双审0与候选两阻塞同时成立，不能把材料0报成实施通过。

初导入的锁和_initializing=True覆盖整个loader调用；reload有锁但不新置该flag；直接loader调用不自动提供二者。同线程再入仍合法，模块锁不作owner/globals事务锁。下一批先闭原P0 context/tools/owner/原24行实际创建→参数→原STORE/effect和原builtins构造，再选原exec内部或post-helper接点；不先做局部模型。

完整来源、23个P2 actual产物/原caller/STORE、原helper/D1、联合metadata/三D0/D2、N2容量/N4生命周期/预算和>=3.11交付仍待；源码缺口闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal未完，非只剩验收。

正常库/service-dist/ACLsettings/平台/Git保持，canonical apply0/历史11；原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不变。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitor/eval。r920/r964已耗不重放、r967不执行不重问、P06用户无材料0/7不再索取。

# r1113：P2 原生入口到原源码 caller 的运输调查

状态：有限静态调查与下一步修订条件，非完整设计批准。源码仍r1106 SOURCE02完整159文件；没有新产品候选、可执行模板、compile/import、native、模型、QA或诊断。

## 1. 本轮改变了什么

确认三源已经存在顶层私有`_AUTH_BOOTSTRAP_CONSTRUCT(_held_original_context, _held_original_factories)`，不需要为“拿到真正局部变量”另包一层全模块函数。三个原函数的global声明分别538/399/252项，但两个参数及`_factory_actual/_factory_expected/_factory_result/_factory_actual_code/_factory_kind`均不在global集合内，是函数局部名称。这里只读源码AST，不读取任何实际frame/locals，不声称AST就是新候选的已编译fast-local布局。

三个原尾调用坐标为control16235、scope15423、registration7607；24个factory行=10/8/6，原`_factory_actual(...)`调用也是10/8/6。r1110的P2顶层适用集合仍23=9/8/6；control的`_AUTH_HELPER_DECLARE_CORE`是第24行的非P2项，不把它悄悄算入23个变换或宣布来源已闭。完整原尾RHS、factory列表、全局声明和调用坐标已存SOURCE-CALLER-INVENTORY-01.json。

## 2. 可保留的局部运输方向

若后续设计能由已持原seed的C生产者直接创建该原源码caller，并将本代actual native producer作为明确第三位置实参传给该次actual function，则不需要从模块public install/producer alias找回native入口。前三个实际引用经PyObject_Vectorcall→实际vectorcall→_PyFunction_Vectorcall→_PyEval_Vector→initialize_locals逐一进入localsplus。此处证明的是固定成功路径的实际参数运输，不是原caller全body永不变，也不消除D1/PEP523/监控边界。

原code、globals、builtins、defaults/kwdefaults、closure、vectorcall及三实参必须来自已持原根；先raw联合核对actual function，再实际调用，不能把“C刚创建”当metadata自动保持。构造可能发CREATE watcher，原row/alias的来源仍独立。未来第三参数属于这三个私有caller的明确ABI变化，不能写成原2参完全不变；不能改已有23个factory的一参数ABI来代替caller参数运输。

这只保留“原生actual实参→原源码caller参数”的方向，不批准任何已有完整安装接点。C如何在正确原阶段拿到actual context和原环境，是下面两个具体阻塞；不可由本节跳过。

## 3. 否决常量注入作为当前接线

把actual producer或token放进派生module CodeType的co_consts，确有LOAD_CONST直接取值的解释器路径；但原builtin exec的audit收到实际source code本身。若交付派生code，就改变r1111/r1112的helper(original_exec,same_code,G)合同，不能再叫原get_code返回的same actual root。当前不选择该改造，也不原位改写code字段或tuple破坏不可变约定。

audit可在module body前查看派生code的const并提前调用其中的producer/token，不需真实frame/locals。token持有者不等于原caller身份；first-use/一次消费/首次登记会把陌生者抢先调用变成原槽或耗尽合法调用的能力，均不接受。若LOAD_CONST后先STORE_NAME再LOAD_NAME，也只是回到可变模块字典。

本轮没有下载codeobject.c/marshal.c，未确认任意PyObject const的完整API合法性、GC遍历或pyc序列化；不凭记忆将这些写成已证实的额外错误。仅上述已固定合同差异已足以排除此路线。没有构造或运行派生CodeType。

## 4. post-helper C 直接调用：两个实质未闭项

一个具体候选是：保同次原module code由原helper/builtin exec完整消费，原helper正常返回后，C从原seed自行创建sourcecaller并传第三actual producer参数，所有bootstrap仍在外层loader.exec_module返回之前。此候选暂不批准，有两个不能作为普通备注的阻塞：

**B001，P0实际context运输不能退回current。** 现有尾调用的第一参数为`_AUTH_CATALOG_CONTEXT := (...)`，原RHS在求值栈上直接交给原caller参数；第二参数的原24行同样直接进入参数。若把tail改成写一个G槽，再让C在exec返回后get该槽，就丢失了原actual RHS运输，不能从可变current重建expected。必须设计原prefix/context/tools/owner的实际生产与持有、原STORE和同阶段消费；不能仅把尾调用删去后从globals补取。原constructor首部还删除自身和factory全局alias；模板若不再建立这些alias，旧del会失败或删除外部新值，须连同实际prefix/effect一起修订。该问题没有被第三参数解决。

**B002，PyFunction_New的builtins来源改变。** 原PyFunction_NewWithQualName会从G['__name__']及_Eval_BuiltinsFromGlobals(G)取module/builtins；随后分配并发CREATE watcher。若exec audit/原prefix删除G['__builtins__']，helper返回后的C构造不再处在原helper/module Python frame，fallback可能取外层importlib环境。code/G相同不能推出func_builtins同原来源；不能exec后从current补pin，也不能照搬r1111被否决的预插builtins补偿。必须明确取得并绑定原builtins根的准确构造路径及原缺席/None/合法值语义。已有私有_PyFunction_FromConstructor可显式接fc_builtins，但它的module查找、doc/version/CREATE、非公开ABI和各版本支持须另行完整论证，不在本轮批准调用。

两个阻塞都只是静态可证明的设计缺口，没有执行攻击/失败模型，也没有证明实际产品失败。调查材料准确可以审0；这个具体post-helper候选仍为proposal_blocking_count=2，不能以调查0宣布方案或源码通过。

## 5. 导入时序与明确delta

| 调用形态 | 原模块锁 | 原initializing | post-helper的约束 |
| --- | --- | --- | --- |
| 标准初次导入 | _find_and_load的ModuleLockManager覆盖loader.exec_module | _load_unlocked在调用loader前置True，loader完整返回后finally清False | 若在同一loader内，原importlib失败删除仍覆盖新constructor失败；不能提前返回loader |
| 标准reload | _exec仍用ModuleLockManager | 此路径不新置_initializing=True | 不可照抄初导入条件，也不重新T0授信；已有state/代际路线按r1112保留 |
| 直接调用loader.exec_module | 不由此调用自动提供 | 不由此调用自动提供 | 不能声称已获importlib锁/标志；须显式准入或另列不支持的调用形态及原异常语义 |

模块锁允许同线程再入，不是G/owner的防改事务锁。helper返回后才运行原constructor会让“exec正常返回”早于bootstrap完成，减少原module/helper活动帧，改变traceback及构造失败阶段。即使仍在同一importlib loader调用内、最终初导入失败仍删除sys.modules，也不能称与原时序完全透明。原audit保留不代表这些delta已经兼容通过。

模板若最终采用，只有r1110明确的module-scope `for _AUTH_TEMPLATE_UNUSED in (): def ORIGINAL(...): ...`拥有本项目已核静态依据。不是`def`后赋空tuple（会创建/释放第一份函数），也不是`if False`（可能丢const）。本轮未生成新模板或重建新const槽。constructor模板、23个P2模板及第24个CORE项须分开逐一对应，不能从名字/常量叶值扫描冒认原路径。

## 6. 下一次实质动作及准入条件

优先给出原P0 context/tools/owner/24行的完整实际创建→参数→原STORE/effect方案，以及原sourcecaller的确切builtins构造路线；然后才能选定在原exec内部还是post-helper接线。可以修订私有sourcecaller协议和prefix，但必须列全原先后顺序、delete/partial/循环import/reload及新增失败，不把current G值当原expected。不能先补三个参数就运行组件模型。

后续完整设计还须接23个P2实际function/origin对、原caller actual return/原STORE、原helper/D1、联合metadata和三D0/D2、N2容量/完整字段、N4任意释放/环保/预算及>=3.11构建发布。原17lease/1449slot/406尾/五S002和原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不变。

full_design_approved/source_approved/execution_admitted均False，源码缺口关闭0、产品pass0。正常库/service-dist/ACLsettings/平台/Git保持；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitor/eval。r920/r964已耗不重放、r967不执行不重问、P06用户无材料0/7不再索取。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal仍未完，非只剩验收。

# 下一批：原P0 context和sourcecaller环境的实际创建/运输

父文档r1113，源码仍r1106 SOURCE02完整159。读取ADR-TRANSPORT-01、SOURCE-CALLER-INVENTORY和16段官方证据。上一轮取得实际源局部/三条调用链/两个具体post-helper阻塞，是progress，不是等待不存在的任务或用户重复批准。

1. 给出原prefix实际context/tools/owner/24行创建、直接参数、原STORE/effect的完整接线。不能把原NamedExpr RHS移成G槽后由C读current；需精确说明原helper内接点或prefix重排、原del和partial/循环import/reload差异。23P2与CORE第24项分列。
2. 明确sourcecaller/native P2原函数的原builtins取得及构造：public PyFunction_New fallback与显式fc_builtins constructor的doc/version/module/CREATE/非公开ABI分别论证，不能从post-exec current补pin或预插builtins冒认保真。直接C第三参数只是运输方向，尚无完整入口准入。
3. 选择接点后同一设计接function/origin实际return、原STORE、D1/联合metadata/三D0/D2、N2完整字段容量/N4释放与预算和>=3.11 native构建发布。不要重新盘点同一三函数，也不重开const注入或first-wins。
4. 完整设计双审前不生成产品候选；源码准入前不compile/import/运行候选、局部bootstrap-control模型、native build/QA/旧绿F001或诊断。所有原保护/限额/禁项保持，Goal active未完。
