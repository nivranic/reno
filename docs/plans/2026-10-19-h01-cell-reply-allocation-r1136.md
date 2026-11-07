# r1136：空壳跨getter、不可变reply与逐引用退出

r1136选CELL_TRY内独立untracked空壳跨原getter，三payload待getter成功/denied决定后才在无callback段填完，固定mode后Track并发布；避免tracked准备态被gc.get_objects提前取得。reply公开后immutable、tp_clear=NULL，traverse含heap type及consumer/cell/contents；按原实际return运输，不设reservation/first-use/最近槽。八出口及heap type释放顺序已列；Init/build/NULL→NULL/allocator/整图N4与COMPAT仍待，B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

同版gcsupport完整文档10750B一次成功，两新header各一次Timeout终止不重试；旧7段gcmodule原文与新GC可发现性、heap type边、GC_Del包络和三cell原完整Try分列。安排GC不等于该点执行GC；Track后可被发现不等于原caller身份，新增完成reply的可观察变化明确。getter失败仍交原late handler，三处分支和原finally不改；有pending还原不代原indicator为空的析构合同。

复用完整输入/父before，保after/preview/逆像；159源/保护56同18缺/预算禁项保持。本批candidate/compile/import/运行/native/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。完整Goal仍有工程，非只剩验收。

01两处证据坐标错配及Root漏审保留；02更正E002为97–103、E003为134–146并重新核全部原文。三笔已列取得不作完整入口上界，跨分配type根/如需guard另计，修订不授予完整设计/源码/执行准入。

# r1136：空壳跨getter、不可变reply与逐引用退出

父r1135封存26文件、3,144,955 B，不修改或追加。产品仍为r1106 SOURCE02完整159文件、7,956,657 B，未安装。本批选择三cell reply的有限构造/运输方向，补原取得点和清理责任；没有产品候选或native类型实现。B001/B002保持open2，完整design/source/execution/Goal均False。

## 1. 来源依据及本批选择

新取得v3.12.10完整官方`Doc/c-api/gcsupport.rst`，10,750 B，SHA d684d142c56caf4d9bcaa0a7b8742f7672a95fe9832201f14f360c30c4aad3cc。它规定GC分配/Track/UnTrack/Del顺序、traverse访问，以及immutable对象允许tp_clear为NULL。另两个新目标`Include/internal/pycore_object.h`和`Include/objimpl.h`各一次Timeout终止，不重试、不换地址重放；完整头未取得。不能以SDK或本批文档补成本机Init/宏/ABI已核。

既有同版gcmodule.c有一个必须保留的边界：GC_Link只安排后续回收，不能说该分配点已经运行collect；但**一旦Track，gc.get_objects可合法发现并保留该实例**。该事实由append_objects338–354及gc_get_objects_impl1774–1820的实际列表构造链支持。只因尚未return、未存共享槽，就称tracked对象绝对私有，是错误推导。本批预案未规定Track时点；正式方向明确选择以下时点，不采用提前Track方案。

1. CELL_TRY实际C入口先从本代独立原生根取得reply的actual类型，拟用PyObject_GC_New产生该次新对象。
2. 成功后立即把**全部本方payload置NULL、mode置PRIVATE**，不发布、不存任何外部反向边、不Track。只有C局部持有该次新引用；对象隐含type引用另计。跨原getter期间不持有consumer/cell/contents payload字段，不存在未跟踪的payload环。
3. 所有准备结束后重新fresh核原actual getter/cell/name/source/phase，在同一C消费点实际调用原getter一次，或者在调用前决定denied。getter错误自然返回原异常；它只属于CELL_TRY层，原late matcher仍处理。
4. actual getter成功或已决定denied后，才在已证明无callout的构造段填三项payload和固定mode。没有额外reply分配，也不把成功后的current cell值重读成结果。
5. 全部traverse字段有效且mode已固化后Track一次，再移交这次actual reply。Track以后不得再填字段、改mode或设一次性消耗标志。future build/Init/引用操作/Track包络若不能证明该段安全，停止该实现候选并修订，不把本设计文字当ABI证据。

这是C入口内私有准备，不是Python旧try外的可变reservation；外部不预先取得填充权，不设first-use/BUSY/最近reply。C入口事件和allocator/build可能行为仍独立审查，未声称所有try内错误都来自原getter。

## 2. reply具体字段与实际来源

选定native heap type：本解释器实际PyType_FromModuleAndSpec产物由r1112的原创建→state持根→另行发布路线运输，不从current module alias/type名称补expected。构造器拒绝公开创建、无BASETYPE、实例dict/weakref/可写成员或公开setter，type拟IMMUTABLETYPE，独立实际slot/布局须核。type的真实隐含引用及释放采用matched full API设计；两个头缺失仍是源码/ABI待项。

| 字段 | 取得点与种类 | 转移/释放 |
| --- | --- | --- |
| 隐含heap type | 该次GC_New/Init按匹配构建建立的实例type责任 | 与payload不同；实际Init和macro仍待核，dealloc保存type指针至实例free后再释放一次，不能从self free后重读 |
| consumer | getter成功或denied决定后，从该次真实C调用持有的actual consumer取得一笔新引用 | 发布后immutable，间接根必须是它原独立state/Product/source链；不能从当前行补pin |
| cell | 同段从该次真实argv的actual cell取得一笔新引用 | 是该次subject身份，不是后来current cell快照；denied若actual cell未认证也只作为data持有，不执行其协议 |
| contents | getter成功actual new reference直接move；denied保持NULL | 成功actual None是非NULL的Py_None引用，不能以值是否等于sentinel判断成功；move后原临时槽清空且不再DECREF |
| mode | PRIVATE→SUCCESS或DENIED，在Track前唯一固化 | mode是scalar，不拥有Python对象；成功与denied不按contents身份猜测，无first-use消费状态 |

不额外复制A/J/Product/bank字段：consumer如按完整设计持续持有这条原链，可形成间接根；该持续区间仍须证明，consumer身份本身不证明任意caller来源或状态永远有效。没有原根时必须拒绝，不能重新认领current值。reply不保存getter错误对象，不把原getter异常编码进success/denied。完成态Track后，gc.get_referents还可经traverse公开type和三个payload引用；opaque没有保密性承诺，不靠隐藏根建立caller资格。ref-tracing/debug等未核包络也不能被untracked方向默默豁免。

本表列明reply自身、consumer、cell三笔显式owning取得，**不是完整C入口取得数的上界**；reply实际类型必须从分配前到Init/实例type责任成立期间持续存活。若原state owning边可被回调/退休移除，须由已证明的独立原根覆盖，或先取得私有type guard、在实际依赖结束后释放，并额外计入acquire/cleanup/预算；不能仅借当前指针跨分配。getter结果那一笔从原实际return转移，不额外INCREF。隐含heap type单独计账；没有用“三笔”冒认总native heap/refcount成本。原argv保持借用，不能因为给payload新增一份引用就释放调用方那份。same identity的consumer/cell/contents仍分别拥有自己的取得责任，不去重。

## 3. 逐阶段与出口矩阵

阶段标签只是本次C控制流/未发布实例的scalar字段，不是持久运行日志。每次重入独立分配一个新reply，不能覆盖其它调用的产物。

| 点 | 此刻实际owner | 正常下一步 | 失败退出 |
| --- | --- | --- | --- |
| A0入口 | consumer/getter/cell/name为真实caller根覆盖的borrowed operands；无reply | 核本方类型根/实际C入口前提，准备新对象 | C尚未进入或原根缺失分类另审，不捏造getter已调用 |
| A1分配成功 | C局部owned reply；隐含type责任；payload全NULL、untracked | 结束准备后fresh门 | 分配NULL是实际准备失败，callee attempts0，不伪装原getter错误 |
| A2门失败 | 同一空壳；actual operands仍借用 | 形成DENIED，不调用getter | denied准备/返回包络错误不能假称getter失败 |
| A3门通过 | 空壳保持untracked/三payload NULL | actual getter一次 | 失败取actual getter error为本层first，释放空壳/type等自有责任后按r1135还原；原late handler仍可处理 |
| A4getter成功 | C临时slot拥有actual result，空壳仍owned | consumer/cell各新取一份，result move→contents，固化SUCCESS | 若所谓填充段可回调/失败，必须逐取得清partial/未移交result；不能两边都释放 |
| A5denied完成 | consumer/cell已各持一份，contents NULL，DENIED | Track | 新错误矩阵与原state错误构造仍待；没有把denied当空cell |
| A6Track/发布 | 所有引用字段有效、mode固定；reply可被gc.get_objects发现 | 仅移交actual新引用；没有后续填充或自有资源释放 | 若包络可能失败需独立失败合同，不以void签名当全build保证 |
| A7ACCEPT成功 | actual reply argv借用且caller持续持根；payload不改 | fresh核type/consumer/实际关联、mode；contents另取真实owned输出 | 返回同一次getter实际值，不再get、不要求后来cell.contents等于旧观察来重建expected |
| A8ACCEPT denied/无效 | reply仍caller拥有；只操作本层extra refs | 旧try外实际认证错误逃逸 | 不DECREF借用reply，不消费一次性资格；原caller以后释放仍独立负责 |

当前八类出口表（allocation fail / pregetter deny / getter fail / success fill / deny fill / publication / accept success / accept denied）是后继源码逐项实现义务。A0–A8九个阶段与八类出口不同计数；不得把阶段/静态行数称为运行覆盖。

本方向不在CELL_TRY中新增getter后的fresh拒绝，再把它记成“getter未调用”的denied。原getter成功的实际结果如实装入reply，后续ACCEPT再fresh可能拒绝。若未来组合要求getter后再拒绝，必须新增独立GOT_VALUE_REJECT出口，保actual getter attempts1、actual result拥有与清理，不能复用A2的attempts0。

partial销毁至少分五种真实状态：分配失败无对象（不得走实例dealloc）；已Init的untracked空payload（只有reply/type责任）；部分字段已取得（按真实NULL/nonNULL及contents是否已move结账）；全部完成但尚未Track；已Track并可能被外部持有。后四种都必须由初始化一致的字段和匹配固定类型安全识别；scalar阶段不能取代实际owned指针记账。只有已成功Init的对象才可使用其类型/实例释放合同，不能假设所有partial都等同完成态。

## 4. 真实caller、原late matcher和三处分支

future K给CELL_TRY实际return一个专用真实FAST local，旧try外ACCEPT只消费该次实际返回；不能用global/closure/最近reply替代。future local名、STORE_FAST/LOAD_FAST、code/closure布局、原target先于arguments的求值、call/entry事件和新增reply寿命尚未编译核。对外任意人可调用consumer/保留reply，不授予更新expected、替换本调用实际return或冒认caller资格。

原getter失败没有reply交给Python，仍进入原except头的late MATCHER。MATCHER核原时点actual error后，成功以一笔真实owned引用返回原ValueError class；不拿当前handled exception替代raised indicator，不提早缓存error。matcher失败在except头自然逃逸。r1135的first仅按本C层运输，不覆盖原handler/finally决定的后续异常。

function_row59与class_row131仍在empty handler append `(cell, False, missing)`，success在try外ACCEPT后append `(cell, True, contents)`；check_view322仍在empty按populated判断，success按populated和actual identity判断。不以reply denied或contents None当empty，不统一三处分支。ACCEPT的专用local退出/每轮覆盖会释放旧reply，须保新增寿命/源码位置/COMPAT，不通过隐藏额外容器或漏del使其常驻。

## 5. 不可变对象、traverse和实际析构

选定公开后immutable，不提供tp_clear，也没有重入可见的mutable准备态；在Track前即完成所有payload，Track后的gc.get_objects观察只会见到完成态。官方gcsupport198–209允许immutable的tp_clear=NULL；不得将这条许可外推到可变Product/bank/consumer。与原对象相比新增了可被GC观察的完成reply，是明确COMPAT变化，不宣称完全不可观察。

traverse须访问三个非NULLpayload及**隐含heap type**，传入原visitor/arg，非零立即返回，不分配、不改state、不清引用。typeobject.c1809–1828中heaptype实例type边的原注释/访问提供独立源码依据；不是直接复用subtype_traverse替本方实现。Py_DEBUG Track会实际调用这个traverse，故所有字段在此前必须有效。Init/type引用及layout还缺完整头，不报已实现。

析构使用固定自有C实现，无weakref/finalizer/subclass覆盖，按以下责任设计：先UnTrack；把contents/cell/consumer全部从owning字段脱离到各自temp，字段置NULL且实例不再可成功消费；保存当前heap type指针及其已有实例责任；依次释放contents、cell、consumer；以匹配的PyObject_GC_Del释放实例；再释放原heap type那一笔。每个真实acquire只释放一次，包括相同identity；free后不再访问self或其字段。保存type责任不是额外INCREF。实际选择用固定GC_Del，不能经被替换的current tp_free重新分派。

有actual pending error时，析构本C层先按r1135把它取入私有自动owned local，跨全部payload/temp/free/type释放持根，最后才还原并只scalar void返回。**原indicator为空的析构出口尚未闭**：不能仅用最后Clear/Set(NULL)就宣称所有cleanup NULL→NULL，不引入虚构first或无限drain。PyObject_Free/GC_Del包络和type析构的中途indicator前置条件亦独立待证。immutable/no tp_clear没有替这些出口提供证明。

reply虽不直接创建self环，consumer/cell/contents可与其它可变对象形成环。traverse只能展示实际边；整图仍须由Product/bank/function/cell等实际clear/退休打断。不能因为reply没有tp_clear就把共同N4环及真实strong-root持续区间报完成。外层TMD拒绝实际结果、owner14/bank9/原selection-before-RHS和其它producer沿原方案独立继续。

## 6. 准入和下一步

本批有限选择明确了空壳untracked、三字段、八出口、不可变发布及heap type退出顺序，不是三reply全部关闭。未闭项为：完整Init/宏与实际build/ABI；原caller实际local/代码/调用事件；allocator及成功NULL→NULL；自有类型实际实现/整图退休；新增E2与每site COMPAT；完整源码/版本预算。新单次下载三目标1成功2终止，旧失败无重放，不用较新doc回填同版。

交付复用完整既有输入和r1135封存before全文，保after、完整preview/inverse，并经Root及同一只读独审检查。完整设计双审前不生成产品候选；源码准入前不产品compile/import/执行、native build/load、模型、旧绿F001、QA或诊断。159源/保护56同18缺、17lease/1449slot/406尾/五S002和原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；control1MiB不是确认native heap上限。

正常库/服务-dist/ACLsettings/真实平台/Git不操作，禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964已耗不重放，r967不执行不重问；P06历史0/7无材料不再索取。apply本批0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。


## 修订02：证据坐标及Root漏审

01的E002实际截取107–115而声称Track有效性、E003截取147–161而声称GC_Del/UnTrack，支持含义与坐标不符。Root01仅核字节与配方相等，漏审这两处支持含义；独审01红项及全部旧文件保留。02独立重读全文后改为97–103及134–146；其余原文逐段重新核对。这纠正证据坐标，不改变untracked/immutable方向，不代表完整设计或产品准入。contents→cell→consumer是所选依赖退出次序，不能冒称所有真实acquire的严格逆序；完整partial/逐引用实现仍待。Root另自查type根跨分配仍未证明，02同时澄清三笔显式取得不是完整入口上界；若原type owning边可被退休移除，必须由真正独立持续根或另计私有type guard覆盖分配，不能把borrowed current type跨callback使用。

# 下一批：析构NULL入口与真实来源链的持续根

继续reply空壳方向的实际Init/宏/ABI与source bank根证明；本批两个超时header不重试，不把未核tag SDK当同版全文。先给原indicator为NULL的payload/type/free退出各自明确后置条件，不能用最后一次Clear或虚构pending exception补洞；有actual first路径沿r1135私有C局部运输，全部detached temp和heap type结清后才还原纯返回。

对consumer/Product/A/J/bank与owner14逐acquire/最后依赖/退休/traverse/clear/free接线，证明reply的consumer间接根在getter及ACCEPT期间持续，完成态immutable/no tp_clear不能代整图环清理。source不得从current属性补expected；track前无外部反向边，track后可被gc.get_objects观察须纳入COMPAT。三reply caller仍须future实际FAST local及原分支/late MATCHER/try外ACCEPT，TMD实际结果拒绝另列。

ordinary226叶、7原append、3cell、owner14路线保持。组合D1/D2/P0/38context/CORE/import、>=3.11平台/预算/全N4，完整设计双审前不生成产品候选，源码准入前不产品compile/import/运行/native/模型/QA/诊断。全部保护/原有限额/禁项及已耗单次授权保持；P06无历史材料不再索取。Goal active，B001/B002 open2。
