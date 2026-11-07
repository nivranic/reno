# r1148：Proxy三操作绑定与不可变owner

r1148选择Proxy三协议的静态只读getset→每次独立ProxyBoundOp→公开PyObject_Vectorcall路线，只运输本层actual args/nargsf/kwnames。op公开后owner不可变、tp_clear=NULL，旧op持续持Proxy拒绝错误根；Proxy两寿命字段仅是前缀，creation总guard、getter三笔与call两笔独立记账。原三协议不增加参数限制，退休拒绝和getter/绑定分配是新增行为。完整Proxy/T0/state/loader、原调用域/COMPAT/SCC/N4仍未闭；B001/B002 open2、完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

九份既有v3.12.10完整源码/文档632724B、25段原文、12份相关设计文件pin及原三协议AST核对。标准FASTCALL方法会剥原flag，不代表标准绑定原则不可行；LBoundOp只有tp_call，不冒认其已有vectorcall。框架tuple/kwargs适配和递归责任逐层分开；只核到公开Enter/Leave薄封装及框架配对，没有冒认底层helper、Leave无callback、实际build或>=3.11跨版本通过。原find_distributions自身委派并return，不被称为原generator body。

01的可清owner方向保留，02六处变换/完整差异/逆像已核；正式ADR再明确公开API及递归证据限度。不可变实例需本方实现保证，不靠IMMUTABLETYPE代替；没有op.clear可用，完整SCC需module/state/type/dict等真实breaker。属性身份、inspection、分配失败、非目标/共享loader外溢及late retirement错误均留COMPAT义务，不以本批选择作完整批准。

本批仅静态文档/read/hash/AST与有限设计交付；父r1147的24文件3248284B、159来源及保护56同/18缺核同。产品candidate/compile/import/执行、native build/load、模型、QA、诊断和新下载均0，apply0/历史11、产品pass0。原限额和禁项保持，旧单次授权与终态失败下载不重放；正常库、服务/dist、权限/settings、真实平台和Git写入未操作。完整Goal仍有工程，非只剩验收。

# ADR-r1148：Proxy三操作绑定、不可变owner与本层vectorcall运输

状态：选择修订02的有限绑定方向，实际双审和交付结论另见本批报告；未生成产品文件。修订01的owner可清理方向保留原字节；本版选择公开后不可变owner，修复清理owner后旧绑定无法取得拒绝错误根的缺口。保留前期METHOD-BINDING-RESEARCH和口头预审原字节，不把标准方法表作原则否决，也不从LBoundOp推断已有vectorcall。完整Proxy/root/state/loader/原调用域/COMPAT/N4仍未闭，B001/B002 open2、四项完整False。

## 1. 所选绑定和本层输入边界

拟给实际ProxyType三个静态只读getset：find_spec、invalidate_caches、find_distributions。get指向同一本方固定C getter，set=NULL，closure分别指向本方静态寿命只含三种role scalar的记录。每次合法getter调用产生一个实际ProxyBoundOp返回，不缓存最后bound对象、不以属性读取冒认业务已经执行。class-level属性仍是getset descriptor，instance-level是该次实际返回，不是普通PyCFunction或Python closure。

原type创建走固定spec/原object基类/原native module，type_add_getset实际创建descriptor并SetDefault。三个名字与slot/type dictionary不得已有冲突；完整实现须独立核实际原创建返回、静态definition和最终dict effect，不能发布后从current getattr反认expected。原getset_get的receiver核验与静态getter/closure直达链是绑定入口依据；外部调用同一descriptor仍可绑定同一实际Proxy，不因此得到原caller身份。

ProxyBoundOpType选择HAVE_GC、IMMUTABLETYPE、DISALLOW_INSTANTIATION、HAVE_VECTORCALL，不设BASETYPE、实例dict/weakref或公开payload setter。其正basicsize包含owner_Proxy、固定vectorcall pointer、role/phase；隐含heap type另计。公开tp_call使用PyVectorcall_Call，Py_tp_members包含原静态只读__vectorcalloffset__条目，offset为本方vectorcall字段的offsetof。固定pointer在Track前赋本方原C入口，不在清理时置NULL后任框架改走别路。

本层vectorcall保留其收到的actual args/nargsf/kwnames，在完整原callee/metadata/D1门通过后，明确使用公开PyObject_Vectorcall(original_callable, args, nargsf, kwnames)交给对应捕获的原callable一次，保原分派与结果检查，不直接绕过原分派调用一个任意函数指针。不插Proxy作额外Python实参，不对原keywords重新打包/归一，不自行写args[-1]，不剥掉或臆造OFFSET。原PathFinder.find_spec bound已持原cls，Proxy只是本方owner，二者不能混同。

只承诺**这一层**输入的原样运输。上游可能通过tp_call、__call__ wrapper或其它合法适配构造新的输入；本方不能恢复最外层曾有的nargsf，也不称整个调用链零分配/零callback。完整COMPAT须核调用site和各层准备/错误，而非只核本vectorcall签名。

## 2. 必需的Proxy寿命前缀，不冒认完整payload

为使getter和旧bound对象不依赖可能被type_clear移走的ht_module，选择Proxy两个必须持续至dealloc的独立owning字段：

| 字段 | 原来源及持续用途 |
| --- | --- |
| module_lifetime | 原native Py_mod_exec实际module，经r1142 creation_module_guard覆盖后另取得instance_module_guard，move进本次实际Proxy；跨普通退休/state清理仍保module/state内存寿命 |
| retired_error_class | 已接受可信native/解释器边界给出的实际原RuntimeError C根，另取得独立guard并move；仅用于本方未ACTIVE/退休/空state准备拒绝，不从可写builtins/module属性重pin |

这些是所选Proxy的**必须寿命前缀**，不是其全部payload或完整T0 root bank。原三callee/metadata、安装/恢复记录、generation及其实际source字段仍须完整state/Proxy表决定；不能用“只有这两个字段”删去原source义务。解释器级C根与native binary仍是已接受外部前提，不以当前type名、hash、第一次调用或refcount作其证书。

原creation_module_guard持续跨整个创建期，不因给Proxy一个module引用便提前move走全局创建guard。ProxyType本次actual newref先由独立C type local跨分配持有，state的type边另取。Proxy实例GC_New/Init成功后保持untracked，两个payload先NULL，再分别move instance_module_guard/retired_error_guard；未move归自动槽、已move归partial，不借未来实例根覆盖分配前窗口。完整Init/layout/build/一般NULL仍待。

这两字段不在普通retire或Proxy.tp_clear中切断。Proxy普通phase变动可禁止新增业务；完整其它字段清理须先退休、detach再release。新增module/state→Proxy→module有限环由module的m_clear及无需先m_clear的m_free先退休并detach原state-owned Proxy边破环；不能强清外部仍持有的Proxy寿命根，也不能用该有限breaker关闭全SCC。

## 3. 每次getter的实际创建及三笔独立责任

外部前提是同解释器GIL、匹配full API/引用包络，已知原static C getter由原descriptor以有效存活的实际receiver进入。该前提不通过ptr形状/type名推导，也不提供caller证明。只在匹配raw段读已初始化字段；owning取得前无本方分配/任意释放或Python callback。

1. raw核合法实际Proxy和固定closure role、已初始化寿命前缀；从持续module_lifetime取得getter_module_guard。未ACTIVE/已退休沿其持续原retired_error_class拒绝，不从已清state/普通source补根。错误构造可分配/回调，按实际first结账，不称raw成功段涵盖错误路径。
2. getter_module_guard覆盖公开state accessor准备区；按r1140指针/indicator四组合立即处理，只在非NULL且无error时读字段。NULL/NULL用持续原error根构造本方准备拒绝，actual error不被phase检查覆盖。不能只因accessor返回非NULL就读state。
3. raw fresh核原state/代/实际Proxy创建关联、实际state-held ProxyBoundOpType的原创建来源和阶段；分别取得op_type_guard和owner_Proxy_guard，borrow→NewRef之间无callout。不从本次current属性造expected，也不把尚未设计的source bank列作已经存在。
4. 两guard跨GC_New/Init；NULL无实例。成功实例untracked、owner先NULL、phase INIT；固定role直接来自本次原静态closure，vectorcall pointer来自本方静态C定义。owner_Proxy_guard一笔move进字段且自动槽NULL。正确实例type根成立后，op_type_guard才有条件nonlast退出；state独立type引用另计。
5. 全部可回调准备及extra释放后fresh原module/state/Proxy/source，保持不持产品锁跨callback。通过才设op ACTIVE并Track。Track前partial不发布；Track后即使尚未return也可被GC观察保留，只放弃本方实际引用，不强销外部retain。
6. 实际输出由本层C owning local跨getter_module_guard等全部本层清理；该module guard可按持续receiver→module_lifetime条件nonlast结清。已有first结清后最后还原；成功清理真实产生error时，结清输出及其它owned后还原actual first。不能返回非NULL result同时留pending error。

三笔责任为getter_module_guard、op_type_guard、owner_Proxy_guard；只有owner那笔move，另外两笔分别结清。原descriptor/caller持receiver及结果的外层责任不计成本方已释放。getter完成只交出owner绑定，不冻结未来业务资格。

## 4. 实际调用、原协议与阶段退出

owning callable operand持有ProxyBoundOp进入其固定vectorcall。只初始化scalar/NULL自动槽，先本层Py_EnterRecursiveCall；失败无本层guard/业务effect、不Leave。成功后各出口恰好Leave一次，不因PyVectorcall_Call名字而省略，也不在本方shim再加第二次。其它框架层真实递归责任另计。本批原文仅核公开Enter/Leave的同版ceval.c2812–2823薄封装与call.c238–242/363–369配对使用，未据此宣布底层inline helper、Leave无callback、实际build或跨版本合同完成。

原合法已发布op在Track前已固化ACTIVE/role/nonNULL owner；本方不提供修改payload/phase的公开入口，普通退休也不改这些字段，tp_clear=NULL。raw核这些不变量后取得独立call_owner_guard，再从其持续寿命前缀取得call_module_guard，跨state accessor和后续可回调准备。未公开INIT或dealloc态不作为合法可调用输入，匹配layout/Init及原native dispatch是外部前提，不能反靠检查指针形状证明来源。两者不与实例owner或getter旧guard合并。不ACTIVE或source已退休时选择本方具名RuntimeError拒绝，不复活seed或重新安装；这项生命周期拒绝及其错误次序是显式新接口行为，不据此宣称完整旧调用域已保留。

state accessor四组合与每次callback后fresh同getter。只有当前原state/本代/实际active Proxy及原callee/source仍被独立认可时，才取得本次相应callee及它的必要metadata/root guard并进入原实际调用。state中原callee根的完整物理字段、取得/退休责任仍待整体目录，不凭本次actual current建立expected。

| role | 单次真实调用与实际输出 |
| --- | --- |
| find_spec | 原actual args/nargsf/kwnames交捕获的原bound方法一次；P/K仅持独立实参根、正常返回后分类，U沿r1147全候选延后判别；不提前限制keyword/arity或重跑binder |
| invalidate_caches | 同一actual输入交原入口一次，由原函数处理非法实参和body；不能改成METH_NOARGS或本方统一TypeError |
| find_distributions | 同一actual输入交原入口一次，返回同一actual iterable；不list化/预取next/缓存最后结果 |

原调用失败先保actual first，后继分类/包装业务0，结清全部本层guard/临时后Leave并最后还原。原调用成功以独立actual result持根跨后继处理和本层清理；find_spec需要新增包装时必须再次fresh本代及source。

若原finder执行中合法再入令本代退休，正常return不证明还能安装包装：停止新增包装/attempt/seed，以本方实际阶段拒绝作为新增后继准备失败，保留原finder已经发生的effect，不重跑原finder。该选择不能反称原find_spec自身失败，也不以无seed fallback关闭正常ACTIVE的U慢路义务。完整late-call/退休时原返回与错误COMPAT尚待共同审查。

每条已取guard出口均独立结清call_module、call_owner及实际callee/root/结果/first，不从“还有实例字段”推断所有guard均nonlast。call_module的nonlast依赖持续call_owner→module_lifetime；在实际callable operand持续持有op、其owner公开后不可变的匹配包络内，call_owner_guard另有op.owner_Proxy独立根，可条件nonlast结清；这不是采样refcount，也不外推到getter创建失败、partial或dealloc。已有first按r1135仅在全部cleanup后还原；外层框架unpack/CheckFunctionResult/调用方操作数释放仍在本层返回后，不宣称已在scalar终段完成。

## 5. GC、清理与字段范围

ProxyBoundOp只选owner_Proxy一个owning payload，隐含actual heap type另计；role/phase/vectorcall为scalar。选择公开后不可变、tp_clear=NULL：role/owner和可调用phase在Track前固化，普通退休只改变Proxy/source阶段，不清op.owner，因而旧op仍可从持续owner取到原拒绝错误根。此处不复制r1144的可清owner选择。同版gcsupport对immutable对象允许tp_clear=NULL，不等于全图已经可回收。traverse访问owner和heap type，不去重。dealloc先UnTrack、不可调用、detach owner并保存已有type责任，release owner、固定GC_Del实例，再release saved type；free后不读self。已有first/NULL入口/allocator/free按实际合同仍需逐site完成。

Proxy两个寿命字段traverse均访问，dealloc时先从字段detach到真实owned temp并保实例type责任，再分别结清error/module、free实例、最后type。其它未选Proxy字段必须纳入同一完整dealloc顺序，不能把本段作为完整Proxy析构实现。source/type初始化前失败、GC_New NULL及已Init partial不混同。

ProxyType→dict→三个getset→ProxyType与ProxyBoundOpType→dict→offset member→type是新增框架环，type_clear原dict边是有限breaker；descriptor内部d_type/name/qualname和type mro/module等另计。Op→Proxy→module/state→OpType及其它root的整图仍未闭。由于op不清owner，包含op实例的环不能再声称靠op.tp_clear打断；module/state先退休并detach自身Proxy/type/source边以及原module字典清理必须纳入完整SCC证明，不能把一个有限breaker当作全图证明。外部保存op延长Proxy/module寿命是合法持有，不强清共享Proxy。

新增已知schema仅为Proxy的两个必须寿命字段、ProxyBoundOp.owner_Proxy，以及state对实际ProxyType、ProxyBoundOpType和安装中/active实际Proxy的语义拥有角色。是否复用先前已有state角色须全表去重，不把旧26机械加数作完整census；三getset和offset member的内部边、每次getter/call各guard另计。没有实测sizeof/峰值，control1MiB不是已确认heap界。

## 6. 显式代价与未闭项

三属性从标准方法观察变为只读getset及自有bound callable，身份/type/repr/inspection/签名和每次lookup分配可失败；__vectorcalloffset__实际descriptor可能读到函数指针表示，须沿r1145真实layout/build/表示前提，不能说它返回offset整数或偷偷隐藏它。type slots/spec/descriptor同名冲突及最终effect须核，不能仅凭声明。

普通本层vectorcall不新增本方tuple/dict打包；经tp_call的原框架适配、上游方法选择/参数构造及cleanup仍各自有成本和callback/错误。实际>=3.11和平台ABI未缩减，只有v3.12.10静态正文不等于实际build或跨版本通过。新增退休错误和finder成功后的退休拒绝不代完整原调用域保真。

尚待：完整Proxy/T0 root/state/安装/恢复/共享loader/source字段；三个getter和call body真正source guard接线；loader.exec_module完整参数/原callee→name→get_code→helper/dict实际消费；P0/新K/actual L/A/J/caller、D1/D2/NULL/free/完整SCC/N4、原调用域/COMPAT/build/预算。上述共同组合双审前不生成产品候选。

本批仅静态提议及既有源码read/hash/AST/文档，新产品candidate/compile/import/执行/native/模型/QA/诊断/下载均0。159来源及保护56同18缺、原限额和禁项保持；正常库、服务/dist、ACL/settings、真实平台和Git写入未操作，不重放旧单次授权或终态失败下载，P06无材料不再索取。完整Goal仍有工程。

# r1148 后继工程

1. 完成完整Proxy/T0根、原三callee的实际取得和认证、state全字段、创建/安装/条件恢复与共享loader。两寿命字段和三个getter不能替完整初始化、一般NULL入口或raw source门。实现须保证op实例owner/role/phase公开后不变；IMMUTABLETYPE仅限制type。
2. 完成loader.exec_module真实绑定与原实参域，保原callee→name→一次get_code→None分支第二次name或helper/exec/dict的实际消费顺序。不得以新增exact-module或统一arity/keyword拒绝缩掉原可成功域；get_code/动态name effect门必须在effect之前。
3. 把外部保存op、module/type清理、GC可见对象、共享loader别名、重复返回、退休晚调用与finder成功后退休拒绝纳入全字段/SCC/N4及COMPAT。op无tp_clear，不可把某个type/module有限breaker当整图完成；新增RuntimeError行为仍须共同裁决。
4. 接通Ingress/C(A)/actual L、原P00–P12唯一effect、新K/LOAD_NAME与A/J/Product/caller/source，补D1/D2/NULL/free、公开递归helper、实际build/版本与预算。完整组合双审前不生成或执行产品候选。
5. 继续原Task9A9B、111/94/r919、真实Store、P06与条件T15/生产云。P06历史身份无材料不再索取；本有限设计不代全目标。

仅复用同一只读代理，不新增代理；不执行模型/QA/诊断、真实frames/locals、全局hook/GC/allocator或正常库/服务/dist/ACL/settings操作，不commit/push/reset/clean。本批封存后不向目录追加或改字节。
