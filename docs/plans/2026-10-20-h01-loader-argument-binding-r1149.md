# r1149：原bound适配与逐引用参数归属

r1149选择静态NewEx＋实际Method_New的有限绑定方向及逐occurrence输入引用、两个owned绑定槽、scalar范围/游标与detached释放temp协议；保extra位置引用先处理、keyword后报too-many、kw_fail含当前项和defaults独立NewRef。actual qualname在原字段读取点独立持根，长defaults保原m/i后缀；direct inner入口不假定loader注入，正常绑定后的source拒绝仍属COMPAT。完整C binder/error helper、Record/T0/source、原调用域/frame/COMPAT/N4及build仍未闭；B001/B002 open2、完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

五份既有v3.12.10完整源码233453B、15段原文、九份相关设计pin及原exec_module AST核对。两槽只表示value归属，不代原frame/code/func/locals/caller/result或first；原argv持续根是合法接口和实际caller证明义务，不用类型/地址/refcount样本补证。固定签名不限制后来actual defaults长度，qualname guard保该次actual对象跨helper，不能晚到formatter中再读current。

原方向01遗漏外露__func__直接进入的nargs0，02已保留原稿并补三处差异/完整逆像；本协议另将INPUT与DEFAULT、游标已退休但R未release、可能last的field guard逐笔分开。标准Cfunction adapter已有递归配对，不机械叠加r1148检查；新module寿命、mutable m_module、额外T0 exec函数根、完整slot与error helper内部first均保留后继工程。不运行binder模型或以本有限材料替代组合双审。

父r1148的33文件3406313B、159来源及保护56同/18缺核同。本批仅静态read/hash/AST、完整diff/逆像及文档交付；产品candidate/compile/import/执行、native、模型、QA、诊断和新下载0，apply0/历史11、产品pass0。原限额和禁项保持，旧单次授权不重放，P06无历史身份材料不再索取；正常库/服务/dist/ACL/settings/真实平台/Git未操作。完整Goal仍有工程，非只剩验收。

# ADR-r1149：原bound适配与逐引用参数归属

状态：选择静态NewEx＋Method_New及下述逐引用责任协议的有限子方向，实际双审/交付另见本批报告。ENTRY/RESEARCH-01及草稿的unselected历史状态保留；本ADR不是完整binder/Record/source或执行准入。完整Proxy/LoaderRecord/T0/state、参数算法移植、原调用域/COMPAT/N4仍未闭；四项完整False和B001/B002 open2保持。

## 1. 用两个实际标准构造保留原bound适配

候选先从原静态PyMethodDef经PyCFunction_NewEx创建actual native body，flags固定METH_FASTCALL|METH_KEYWORDS，不设METH_METHOD/CLASS/STATIC。它的C self是本次实际自有LoaderRecord envelope，Python实参数组不包含这个C self。该record的完整payload/source/module/type/退休表仍待；不能用可写loader字典或当前type属性充当其来源。

随后实际PyMethod_New(native_body,actual_original_loader)创建外层bound对象，直接强持actual返回，再按原安装合同另行写loader实例exec_module。外层method的im_func是本次native body，im_self是原loader；内层C function的m_self是record。两种self与当前Python实参的self槽分开，不额外往args插第三个owner，也不从current exec_module读回expected。

同版classobject.c54–62先用原OFFSET许可在scratch插入loader，再以bare nargs传给im_func，返回时还原；无OFFSET的68–96保原small-stack/大参数malloc前缀。原plain Python函数在此子层本就没有收到最外层OFFSET。因此内层标准FASTCALL adapter再取NARGS，不当然重现r1148“丢掉原finder本可取得的flag”的问题。本候选不需要据此另造一个LoaderBody vectorcall type；这只是该原bound路径的有限判断，不否决其它路径/类型。

仍须核每层实际callable/root/source及参数array寿命。PyMethod_New保持func/self和standard adapter，不执行原Python binder，不证明原调用方身份；外部可观察或保留__func__/__self__，新native对象及record都不是秘密。PyCFunction的m_module可写，应保存actual创建字段供后继核，不能冒称所有metadata不可变。该创建字段也不能替Record的独立、不可退休module寿命根；其完整字段、原取得及退出仍待，不能从可写m_module补认lifetime。

## 2. 原binder必须按实际算法处理，不能改成一位置参数

仅实际经过外层Method的调用，native body的nargs才包括它注入的loader。外部可经可见的__func__直接调用内层PyCFunction，此时没有该注入保证，nargs可为0；不能无条件读args[0]、减去一个位置参数或由其值认领caller。两条入口都只能按既定原参数算法处理本层实际args/nargs/kwnames；必须等绑定成功及原source/代际联合门通过，才使用本次实际绑定槽。直接内层入口的原参数域/source/COMPAT仍须单独完成，不以统一拒绝或普通bound路径证明替代。原函数code的参数合同是self/module两个可位置或关键字参数；原代码/参数名/flags无varargs、varkw或kwonly。该结论来自源码AST，不代T0运行对象或全部metadata已取得。候选增加原_LoaderBasics.exec_module plain function/code/参数名字等的独立T0捕获和持续根义务，C005需正式扩表，不能安装后补pin。

拟使用版本化C移植原initialize_locals对应分支及完整错误helper，保：

1. 先按原算法填位置槽，多余位置参数的引用责任先处理，错误本身仍在keyword处理之后报告；不得先统一arity拒绝。
2. 每个keyword依原顺序检查Unicode、先遍历参数名字作identity比较，再以原方向PyObject_RichCompareBool(keyword,varname,Py_EQ)慢比较。不能颠倒左右、限制exact-str、先归一或多比较一轮；str子类回调与其原异常保留。
3. 重复实参、unexpected keyword、too-many positional、missing与defaults填入保原先后。原func_defaults和func_qualname可能在keyword回调中变化，不能把T0快照当作binder以后每个使用点的actual值；如何兼顾本层实际算法与后继原source fresh门须逐使用点完成。
4. 参数绑定成功后，才从本次实际绑定槽取得actual self/module进入原source/代际联合门与native continuation。源已变或已退休时不得从这些current补造expected。该门的新增拒绝与原binder异常不能混写成原业务失败。

错误helper中的格式化、missing_names列表、%S对actual keyword的处理与临时释放可能产生真实错误/回调。内核未导出helper不能仅凭同名声明调用；公开API或本地完整算法、SDK链接面、版本/ABI均须另核。本稿没有移植成可编译C，也没有把固定签名当作任意Python函数通用binder。

## 3. 输入所有权是单独的移植条件

原_PyEval_Vector先为func/locals/全部位置和关键字value取得引用，PushAndInit及initialize_locals消费这些引用，已绑定locals由frame最后清理。这里native body的args由外层标准method或框架持有，属于borrowed输入；不能让原fail/extra/kw_fail循环直接DECREF这些借用责任。

选择同一次实际argv逐occurrence独立增引用、两个真实owned绑定槽及scalar范围/cursor/R协议，完整责任见下文；不偷调用方引用、不按identity去重。每笔取得/转移/extra/keyword失败及槽清理须有inverse。有限value归属设计已选，实际完整C逐slot实现及所有caller/清理仍待；不宣称没有其它准备分配、所有cleanup均nonlast或NULL→NULL已闭。

## 4. 递归、frame和后继行为的限度

标准PyCFunction adapter已有自身enter/leave，native body不能不经核对再补一笔相同检查。它相较原Python函数路径的计数、错误、准备及frame差异须作显式COMPAT；原r1111移除exec_module Python frame，不等于可以省略全部原参数域或把所有错误都换成准备拒绝。原bound无OFFSET大参数缓冲和释放仍归外层标准method，本层原算法临时与record/module/source guard另计。

后继continuation保持原约定的实际get_code→第一次name→一次调用，None时第二次name及原ImportError，成功时原helper/exec/dict顺序和实际新K/G/L路线。原exec_module最后是表达式语句，正常隐式返回None；helper实际返回先按原语义丢弃，再处理本层退出，不能直接返回它来偷换接口。

完整record字段、原callee/getter effect前门、共享loader/多次调用/旧代退休/条件恢复、原安装字典写入、所有一般NULL/free/first、整图SCC/N4及版本/build/预算保持未闭。没有凭本方向增加seed或原source资格，也未修改r1148封存物或正式主方案状态。

本稿只用既有源码的静态分析。产品candidate/compile/import/执行、native、模型、QA、诊断和下载0；不操作正常库、服务/dist、权限/settings、真实平台、Git、全局hook/GC/allocator或真实frames/locals。


# r1149 所选有限协议：逐 occurrence 引用、双槽与 scalar 游标

本协议把修订02方向细化为已选择的有限value ownership责任，实际正式双审见本批报告。它不等于完整C binder、原frame/错误helper或LoaderRecord设计完成；没有实现或运行产品。以下“原”指已固定v3.12.10正文；实际build及>=3.11其它分支仍须分别实现/验证，完整四False和B001/B002 open2保持。

## 1. 本协议的真实输入与独立前提

只用于原exec_module的两个参数self/module，co_argcount=2、co_posonlyargcount=co_kwonlyargcount=0、没有CO_VARARGS/CO_VARKEYWORDS的已认可原code。原function/code/参数名必须来自额外T0捕获，不能从安装后属性或本次输入重建expected。扩展C005时须从原frozen external实际dict取得原_LoaderBasics类和其实际exec_module plain function，核原SourceLoader/SourceFileLoader的原MRO关联，直接保存actual返回/borrow→own及完整metadata；本稿没有把这条新捕获写成现有state字段。

进入本方原native body时，actual record C self、argv与kwnames的有效性/存活由合法原C API调用、实际构造和匹配build给定。有效输入必须跨整个调用持续；检查pointer/type/refcount不能创造这个前提。普通外层Method持loader且插入它，直接内层调用则没有这笔注入。算法只看actual p=nargs和q=实际kwnames长度，不凭p或args[0]判断调用来源；p=0也合法进入参数处理。

本方另有真正C自动owned record/module/function/code guard，取得来源、跨公开state accessor的四组合及回调后fresh依既有路线逐步落实；不把可写m_module当module lifetime。binding_code_guard另笔持本次原binder入口实际选择的co及其不可变参数名字链，function guard仅保function活着，不能替code或可替换qualname字段；原frame早先co与本笔取得/持续时段分开，不借前者抵消本笔责任。原frame早先持code、func、locals、body/result等的责任仍单独列账，不能说双槽已经运输这些原frame根。

以下callback-free引用段只在r1138 full API、非Py_REF_DEBUG、匹配32/64位及统计宏/运行时数据包络成立时适用。没有实际build证明时只可作为设计条件；不删除其它版本/平台支持，不用拒绝未核版本冒认完成。借用argv、kwnames及各对象的实际持续根不是保存最近一次样本或refcount采样。

## 2. 两槽及输入范围的拥有表示

不改caller argv，不写args[-1]，不另分配参数数组。C自动区只为本层value ledger保存两个NULL起始槽S0/S1、scalar取得/转移/释放游标、槽来源标记及短时detached release temp R。guards、first、error-helper临时和后继result不算进“双槽”。这个有限表示不声称总C栈/heap预算已确认。

| 责任 | 实际取得 | 仍拥有时的表示 | 结束方式 |
| --- | --- | --- | --- |
| P(j)，0<=j<p | 按原Vector顺序对actual args[j]逐项INCREF | 未转移/释放的位置范围及游标；原argv只运输pointer，不拥有caller那笔 | move到S0/S1，或extra/失败范围release |
| Q(i)，0<=i<q | 完成全部P取得后，对actual args[p+i]逐项INCREF | 尚未消费的keyword value连续范围[q_cursor,q) | 匹配成功move到空S槽，或kw_fail含当前项到尾release |
| S0/S1，来源P或Q | 从对应一笔P/Q move，不新增INCREF | 实际C自动owned slot；同时从输入ledger移除这一笔责任 | 正常移交后继，或本层失败detach/release |
| S0/S1，来源DEFAULT | 按原defaults实际使用点对该actual default NewRef | 本次独立owned slot，不属于argv ledger | 与其它槽相同，但不能套caller argv的nonlast证明 |
| R，短时释放temp | 从一笔范围责任或S槽detach | 游标/原slot已退休，但R仍是一笔真实owned责任 | 实际DECREF消费完，不可只清标记漏释放 |

同一个对象出现在多个位置、keyword或槽时，每个occurrence都有自己的引用；不按identity去重。保持守恒：每笔已取得输入引用恰在未消费范围、某个S槽、R、已释放或已合法移交这五类之一；DEFAULT笔另加且不冒称来自input。该式是待实现逐点核对合同，不是执行过的状态机/模拟测试。

取得顺序对应ceval1668–1677：先原function所需独立根，再逐P、逐Q；本方附加code/record/module根另计。在匹配无callback的NewRef包络中，取引用后立即更新本层scalar责任；不得夹入分配、任意释放、等待锁、解释器切换或可回调日志。其它build须另作具体合同。全输入取得期间caller的原强根仍在；本方新增refs不接管caller原有引用。

## 3. 原顺序的逐事件责任

| 事件 | 原正文与本层转移 | 失败或后继 |
| --- | --- | --- |
| 填位置槽 | n=min(p,2)，按j=0..n-1把P(j) move到S[j]，对应1351–1354 | p=0时不读args[0]；没有通过复制pointer新增一笔拥有 |
| excess位置项 | 对P(n)..P(p-1)按1373–1377顺序，先将本方标记/游标退休并运输到R，再实际release | 先做引用处置，too-many错误仍在keyword处理之后；不改成早期arity门 |
| 当前keyword i | keyword借自持续kwnames，当前value的一笔仍归Q(i)，q_cursor=i | 不因取得borrowed pointer便提前将Q标成已消费 |
| keyword匹配 | 按原Unicode检查、identity全遍历，再以keyword在左的RichCompareBool慢遍历；比较会回调 | 成功且目标槽空才Q(i)→S[j]并推进q_cursor；不额外INCREF |
| cmp/keyword错误 | cmp失败直接保actual escaping error；其它错误走原相应formatter，不多做一轮比较 | Q当前i到尾全部仍需release，先保存本层actual first；已入S槽的refs不再归Q |
| kw_fail尾部 | 按1441–1445从当前q_cursor（含当前项）到q逐笔经R结清 | 不从i+1开始，不重复清已成功move的项；S槽留到外层绑定失败清理 |
| keywords完成 | q_cursor=q，未消费Q范围为空 | p>2才依1460–1463构造too-many；有pending错误不再进入成功defaults/continuation |
| defaults/missing | 按1467–1493在实际使用点读取func_defaults，缺参先构造原missing错误，正常default只NewRef到空S槽 | DEFAULT refs单独计账；不将原源码无defaults硬写成本次始终无defaults |
| 绑定成功 | 两个实际S槽含本次实际self/module，原source/phase联合门仍在后面 | 不把绑定成功当attempt/seed；直接inner入口也不能因此取得caller身份 |

P/Q的额外引用若有合法调用方实际operand/tuple/框架数组所维持的独立对象根，可以在这个持续区间按r1133/r1138条件nonlast结清；这是原持根覆盖证明，不是“args看起来仍在”的推测。对DEFAULT槽、function/code/record/type等guard、formatter结果不能沿用这个结论。outer Method的malloc buffer和框架解包数组本就有独立生命周期，native body不得free它们；原外层返回后的cleanup仍属外层。

本候选用scalar游标表示逐输入拥有，不需要为refs再复制一个数组；前提是actual原argv及其所有pointer跨这些回调持续有效。若完整caller/build接线不能给出该前提，必须修正表示和预算，不能读悬空argv或以指针检查补救。该选择没有把一般C调用者自证或整个caller链证明做完。

## 4. defaults实际域不能写死为长度<=2

原function setter允许tuple或None，没有限制tuple长度<=参数个数。keyword回调之后的actual defaults可能是长tuple；必须保原defcount/m/n/i及后缀算法，不能将m<0当准备错误、截短或归一tuple。T0原无默认是独立source事实；它不允许提前抹去原binder随后实际读取的行为。

too_many_positional路径在其1144读取actual defaults长度，首个1148/1152分配之前已消费完该借用，后面只需scalar defcount/固定co值。可在这个raw段用完defaults借用，不跨分配持裸tuple指针。missing路径1468–1478按actual长度计缺参，调用missing helper后即失败，不再续用之前的defaults。非missing填槽路径1486–1490只在明确的无callback raw tuple读取与NewRef包络内取得actual default并写空槽；不得在借用取得和NewRef之间塞可回调准备。

原实现对defaults字段的多次读取、NULL与None及tuple子类的原base访问语义须按完整port保持。上述段内说明不是把快照拷贝当actual字段，也未证明任意插入的helper都无callback。若真实实现需要额外tuple guard，新增acquire/release及COMPAT应另列，不能藏进input账。

## 5. actual qualname采用独立guard

选择在每次原formatter/helper的字段读取点1392/1431/1452/1462/1478，从actual当前func_qualname取得独立私有C qualname_guard；固定kwonly0不走1517，不到helper内部1174/1079才补读current；不使用T0 qualname快照替actual错误名字。borrow→NewRef须在已匹配raw段完成，之后才进入分配/原repr/格式化。一次具体格式化调用用该次原actual对象跨调用持根，不在helper中途改读新的current字段。

too-many helper的1148/1152及1169等分配先于1174使用qualname；missing helper的1099分配、1113 Repr和format_missing1079也跨操作。只保function对象活着无法保被callback替换掉的旧qualname。三个直接formatter位1390–1392、1429–1431、1450–1452也统一使用这笔actual-value guard，避免未经完整消费顺序证明便保裸字段指针。这里选择加一笔引用；寿命延长、refcount可观察性与新增cleanup均是COMPAT代价，不能声称所有观察透明。

qualname_guard不属于P/Q/S账。若格式化失败，actual formatter error（可能为TypeError构造或MemoryError/原keyword格式化异常）才是该层逃逸first；先按r1135保存，再释放qualname guard及本层其它拥有。guard字段在callback中可能被替换，故其释放可能last，不能只因function仍活着就标nonlast。missing_names、sig、kwonly_sig等helper内部temp也须逐site保first并结清，外层在helper返回后保first不能代替其内部错误清理证明；完整error-helper移植及一般NULL/free合同仍未闭。

## 6. 成功、失败与直接inner域

绑定失败时不新增attempt、get_code或exec。先保存实际逃逸first，按已完成事件保留P/Q游标范围和S槽/R真实责任，结清全部本层拥有及附加guard/formatter temp后，最后还原first并scalar NULL返回。已move到槽的value不能再被输入范围释放；游标已推进而R未release的责任也不得消失。错误构造前不提前清S槽，保原缺参/重复/too-many所读的实际槽状态。

本层双槽代替的是value归属表示，不是原frame cleanup实现。原binder失败之后1596–1598才clear_thread_frame，1548–1553还有_ClearExceptCode、f_code释放、PopFrame；原准备失败、func/locals/code/caller/body局部/traceback/result与清理次序仍须另审。r1111移除原exec_module frame是既有显式变化，不能据此把所有这些义务报成已完成。标准Cfunction adapter已自带递归enter/leave；本body不机械再加一笔，实际跨Python/native计数和错误差异继续COMPAT。

绑定成功时保actual S0/S1跨后继source/metadata/generation门及一切可回调准备；通过后以明确move或独立NewRef向本次native continuation运输，不从current caller/global/loader槽重读代替。成功移交前的实际清理如果可回调，必须在业务fresh门之前完成或由持续owned槽覆盖并再次fresh。后继get_code/name/None/helper/exec/dict与新K/G/L/seed仍未接完；原normal返回隐式None，不能直接把helper实际返回作为exec_module结果。

直接inner输入可能从keywords或defaults正常绑定self/module。若其bound self与Record独立原loader/source不满足，后继来源拒绝属于新增COMPAT/domain问题，不是原binder错误；不能把传入self登记成expected，也不能全拒绝direct来冒认完整调用域已闭。本协议保持参数算法对actual输入的处理，尚未决定/完成所有正常绑定的direct调用如何保原行为。相等只说明本次值相等，不证明经过外层Method。

## 7. 本批关闭与未关闭

可正式选择的有限部分是：静态NewEx＋实际Method_New绑定方向，以及在匹配原入口、来源根和引用包络下，逐occurrence增引用、双槽/scalar范围move/release、defaults独立笔、actual qualname guard的责任协议。没有新参数数组并不等于没有其它分配、没有callback或满足control1MiB。所需acquire数按p+q和实际defaults/guards分列，不虚报固定总拥有数。

仍待完整T0/C005扩表及source/alias/metadata接线、Record全字段与module寿命/retire/清理、完整C binder及error helpers/SDK/ABI/实际build、全部direct和原frame/准备/错误域、所有一般NULL/free/first、共享loader恢复及SCC/N4/预算，再与P0/Ingress/actual L/A/J/Product/K/D1/D2组合。完整组合双审前不生成产品候选；Task9A9B、111/94/r919、真实Store、P06与条件T15/生产云及完整Goal保持未完成。

本批验证只读既有文件、hash、AST、完整diff/逆像和文档交付，不执行binder状态机、模拟模型、产品、native、QA或诊断，不新下载。正常库/服务/dist/ACL/settings/真实平台/Git写入、全局hook/GC/allocator及真实frames/locals边界保持；旧单次授权不重放，P06无历史材料不再索取。

# r1149 后继工程

1. 把原_LoaderBasics/exec_module plain function/code/参数名及完整metadata正式接入T0/C005和全state/source目录，落实Record的独立module寿命、原root取得、retire/clear/dealloc与SCC。不得用可写m_module或本次bound self补造expected。
2. 按本协议实现完整版本化C binder：逐P/Q取得和转移、双槽/R/scalar完整inverse、长defaults原后缀、actual qualname每使用点guard、全部格式化helper和内部first/temp。核公开API/本地移植/SDK、NULL/free、实际build与>=3.11各版本；不能对借用args照抄steal/DECREF。
3. 完整闭合direct inner、普通bound、原frame准备/清理/递归/错误域及COMPAT。正常绑定后source拒绝不冒称binder错误，不能以全拒绝direct、exact-module新硬门或统一关键字拒绝缩掉原成功域。
4. 接通actual get_code callee/name/一次返回/None第二name或原helper/exec/dict、隐式None与本次K/G/L/attempt/seed；共享loader、多次调用、退休晚调用、条件恢复及失败partial须纳入整图N4/预算，再与Ingress/P0/A/J/Product/caller/D1/D2组合。完整组合双审前不生成或执行产品候选。
5. 原Task9A9B、111/94/r919、真实Store、P06与条件T15/生产云继续；P06七历史身份无材料不再索取。本有限协议不代完整目标。

仅复用同一只读代理；不新增模型/QA/诊断、正常库/服务/dist/ACL/settings/真实平台/Git写入、全局hook/GC/allocator或真实frames/locals操作，不重放旧单次授权和终态失败下载。封存后不向本批目录追加或改字节。
