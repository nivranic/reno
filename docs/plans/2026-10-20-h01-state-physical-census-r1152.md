# r1152 有限落地方案：具名根物理存储、快照运输与模块lease

状态：已知固定段与lease协议经Root及同一代理有限复审，完整state layout仍未选择。原FIELD-INVENTORY-01保持；其32/12/14是角色库存。本稿把已知部分落实为84个T0 owning槽、19个后继owning槽和模块lease退出协议；103只计本稿具名固定槽，不含未展开的class/source/type/metadata变长区域，不是全图、运行引用数或heap界。容量、实际build、完整caller/N4/COMPAT仍未闭，不能生成产品候选。

## 1. state的物理位置与所有权分层

采用原多阶段module的m_size内联存储RootArena；不创建新的Python可调用register接口，不使用进程全局PyObject表，不复制md_state或由本方释放它。tagged moduleobject.c405–440显示md_state的实际malloc/清零早于原exec slot；704–727显示本方m_free返回后解释器才free md_state。该条件路线只支持存储先于T0入口存在，不能自动证明任意变长快照容量、sizeof/offset或实际平台ABI。

state另有published_arena非拥有C指针和arena_owner枚举STATE/LOCAL_DRAIN/CLEARED。只有已初始化且实际归STATE的同代arena可以被接管；LOCAL_DRAIN与CLEARED都不再被重入领走，不清零后复用旧代。只有arena_owner=STATE时，m_traverse访问本arena真正拥有的槽；私有drain接管后，published_arena=NULL、arena_owner=LOCAL_DRAIN，arena中尚未释放的引用归当前C退出路径，不能再被state遍历或其它清理重领。内存仍在module的md_state中，持续module_guard或真实m_free调用跨度负责其物理寿命。退出不需要复制大数组到C栈，也不分配一个用于回收的备用arena。

每笔固定字段取得都写一个原本NULL的槽，并在同一无callback段将固定slot id追加到known_acquired_order，增known_seq_count。相同指针的独立取得仍有独立slot；借用别名不追加。known部分有103个slot id，ledger每id最多出现一次；变长/source扩展须给独立完整ledger，不能借103声称全部字段已齐。只在真实新module state初始化ledger，失败/退休/reexec不重捕获或清零后复用旧代。

## 2. T0固定根：30槽与6个九字段行

以下每个名字都是一个实际owning存储位置；同一格用于既有C002/C005角色复用，不再次取得第二份同名根。函数行的G/B与module字典即使同指针，也属于各自独立取得，不与这些位置抵消。

| 物理前缀 | 槽位 | 原实际producer及取得 |
| --- | --- | --- |
| t0.runtime | modules_dict、sys_module、sys_dict、meta_path | 原C001映射/raw字典路径；每borrow立即NewRef写空槽 |
| t0.external | module、dict、pathfinder_class、pathfinder_dict | 原C002；前三borrow→own，class dict为公开PyType_GetDict实际NEW |
| t0.descriptors | find_spec、invalidate_caches、find_distributions | 原PathFinder dict中actual原descriptor；各独立own，不从后继bound属性反取 |
| t0.loader_basics | cls、dict、mro | r1150新增原_LoaderBasics；actual class、NEW type dict、borrowed tp_mro→own |
| t0.file_loader | cls、dict、mro | r1150新增原FileLoader；同上 |
| t0.source_loader | cls、dict、mro | 原C005 SourceLoader三位置；字典既有取得复用本位置 |
| t0.source_file_loader | cls、dict、mro | 原C005 SourceFileLoader三位置；同上 |
| t0.bootstrap | module、dict | 原C006 frozen bootstrap；各borrow→own |
| t0.builtins | module、dict | 原C006 builtins；各borrow→own |
| t0.builtin_exec | callable、self_anchor、module_metadata_anchor | 原builtin exec对象独立own；actual C self/m_module先由私有C temp分别强持，再各运输为0或1项的actual snapshot tuple并move入anchor。NULL按presence保存，NULL与实际Py_None不混同。methoddef/function/flags/vectorcall为独立C scalar事实 |

上述槽数为4+4+3+12+2+2+3=30。builtin exec不是Python函数；其self/module_metadata不能因builtin module已另持有而漏账，也不能从后继可写__module__补pin。两个anchor只改拥有层级：state持原metadata的实际snapshot tuple，tuple持T0 actual值；不把任意可写m_module值直接存成state业务对象/异常实例字段。原callable的actual字段仍与该captured value及presence核对，不将anchor本身当原m_module。raw结构/原C getter、完整ABI和错误路线仍须实现审查。

具名Python函数行固定六个：F0 PathFinder.find_spec原func；F1 invalidate_caches原func；F2 find_distributions原func；F3 SourceLoader.get_code；F4 _LoaderBasics.exec_module；F5 _frozen_importlib._call_with_frames_removed。前三从独立原descriptor link取得，后两loader函数和helper从各原dict取得。actual function各直接进入对应行，不另设一个重复owning的get_code或exec_function别名槽。

每行采用与原function_row9对应的九个owning位置：

| 行字段 | 存储合同 |
| --- | --- |
| function | 实际原function独立own |
| code | 原C getter actual borrowed code立即own；不重compile |
| defaults | actual原tuple或NULL缺席，present另存；持tuple可借不可变元素，不递归冻结其内部对象 |
| kwdefaults | actual原exact dict或NULL缺席；独立own不代替entries快照 |
| kw_entries | 由本次T0捕获的actual key/value运输构造的exact tuple of pairs，独立own；不在分配callback后重扫current生成expected |
| closure | actual原exact tuple或NULL缺席；独立own，tuple→原cell身份可在持续根下借用 |
| cells | 由T0逐cell populated/contents运输构造的exact tuple of records，独立own；非空contents须有独立快照根，不能仅借cell |
| globals | actual原G独立own，仅按原协议检查身份/键；不复制所有无关values |
| builtins | actual func_builtins原dict独立own；不由G.__builtins__或builtin exec替代 |

六乘九为54，与前30合84个声明槽；defaults/kwdefaults/closure的NULL仍是合法未持有状态，不为凑数造Py_None引用。kw_entries/cells在缺席时为本次实际空tuple NEW责任，原Getter NULL仍保原present位。六行只是显式具名T0函数，不能代替原class_row遍历整个MRO产生的property/staticmethod/classmethod/plain-function全部dispatch行，后者仍须变长区域与producer字段。

func_name/qualname/annotations/doc/module/dict/typeparams不是原row9的新拒绝谓词。binder实际使用点的qualname/defaults guard仍按r1149，不以本T0值替代。dispatch存独立actual vectorcall scalar并核原标准入口；func_version不作来源证书。参数名字只沿已持code→tuple→字符串不可变链借用，原caller/domain条件另计。

## 3. T0变长快照：先捕获，再改变存储表示

不可把kwdict/cell可变边的借用带过allocation。拟定捕获顺序是：先有真实module创建guard；捕获固定原根与每行function/code/G/B/defaults/kwdict/closure；在同一匹配raw段把kwdict每个actual key/value和每个非空cell contents分别取得独立引用，放在本次私有C捕获scratch的已取得槽。closure中的cell身份由已持原closure tuple及index保根；scratch记录populated bit与原长度。没有业务budget/Context/异常/资源的持久直接state字段；裸item引用只属于本次C准备temp，后继持久state字段保存原metadata根及其snapshot tuple。原metadata可间接可达业务对象，不能报告零间接可达。

scratch必须在T0原入口前已具备可靠容量。本稿仅确定其内联m_size区域/所有权位置，尚未确定可满足完整原域的数值容量。m_size本身不能补这项证明；不得从AST推定kwdict/closure必空，不得在容量不足后allocator回调再补current expected。原control1MiB是累计实读预算，不是已确认heap上限；不能任意拿它当scratch固定字节数或据此删去合法大metadata路径。容量与计费没有闭合前本路线不得进入源码准入。

全部T0原值已由state固定槽或本次私有scratch真正持有后，才允许准备分配；所固定的是原对象/值，不是后来第一次看到的snapshot容器。builtin exec两个可空字段也在T0 raw段进入独立C temp，后继仅把这些captured值运输为anchor tuple，不晚读current self/m_module。NULL构造0项anchor，非NULL构造1项anchor并move该actual值，故实际Py_None是1项而不是缺席。在这次bootstrap metadata lease覆盖下，后继准备只把既有拥有值运输成元组，不再读取current kwdict或cell作为expected。重入只见PREPARING/退休状态，不借尚未完成的snapshot发放attempt/seed。ROOTS_FROZEN的原值条件须包含固定原根和全部私有捕获已完成，不能因六个function已持有就提前标记；此处不要求anchor/kw_entries/cells表示槽先齐，其实际转换完成另核，不把原值捕获与表示ready混同。

构造snapshot时不能持一个含NULL槽的外层tuple跨下一次分配：先逐个分配并在无callback段填完整pair/record，将完整子tuple放本次C owning scratch；所有子tuple齐备后才分配外层tuple，随后无callback地将完整子tuple逐槽move进去。closure中借来的cell身份在填入子tuple前，须先由持续closure根覆盖，在匹配raw段另取得一笔NewRef；该新owned引用才可move给新tuple，不能steal borrowed cell。bool/None等占位槽同样须有各自实际移交引用；PyTuple_SET_ITEM仅填本次brand-new且未填槽，steal后原owned槽NULL；不覆盖已有项、不Resize共享tuple。tuple空单例照实际NEW责任分账。GC可以保留完整产物，不声称它们秘密；partial/free及实际Track/layout仍须同版证明。

每个构造返回仍先由C local持有，完成其raw填槽后，核本次module阶段、lease、原捕获存储和槽仍合法，再move到kw_entries/cells并追加ledger；退休后不把snapshot补回已clear的state。失败只清实际private scratch/子tuple/外tuple/state已持有项，保实际escaping first，不重试或重采expected。原empty cell用populated=False及无contents拥有表示；snapshot record的占位None不是cell内容。原row checker在False分支不比较第三项；populated=True且contents=None仍有真实None拥有边。新存储不改原getter ValueError、late matcher或missing fallback语义。

## 4. 后继已知19槽与Proxy的字段选择

capture_complete=1且全部必需row_ready均实际成立后，在原ROOTS_FROZEN/PREPARING后继，原actual C创建返回直接建立以下位置；type创建local、实例创建local、state-owning与public STORE/effect各分账。新type的state槽从持续actual type local另NewRef；不得从公开属性造expected。

| 分组 | 物理槽及取得 |
| --- | --- |
| native.types[9] | FactoryType、ConsumerType、ReplyType、LType、LBoundOpType、IngressType、ProxyType、ProxyBoundOpType、RecordType；各原FromModuleAndSpec实际返回local独立NewRef入state |
| native.instances | factory按r1143实际创建local在fresh后move；ingress按r1145由actual创建local另NewRef；proxy在实际创建local持续期间另NewRef |
| native.pathfinder_bound | 原func与原PathFinder class实际Method_New返回，C local持有并fresh后独立NewRef入state；框架func/self边另计 |
| native.errors | 原C RuntimeError、NameError、TypeError各独立NewRef；用于OPEN生产者，退休拒绝不读已清state |
| native.keys | reserved_ingress_key、reserved_attempt_key、name_attribute_key；原C literal实际Unicode创建返回先C local持根，fresh后move，不从current G或alias认领 |

9+3+1+3+3=19，与84合为本稿具名固定段103。source/A/D/J/Product/seed/carrier/owner的其它type与metadata并未因此消失，后继完整物理表必须列其全部扩展槽和实际创建顺序。不能把“已知9个type”叫作native type总数。

本批拟把Proxy的全部直接owning payload确定为r1148原两项module_lifetime、retired_error_class；phase/generation为C scalar。三原callee、原descriptor/class/source和metadata使用本模块RootArena中的独立原根，按实际operation取得metadata lease和必要call-local callee guard，不在Proxy另缓存同名alias/最近返回。find_spec用native.pathfinder_bound，两个static协议用F1/F2实际func；原args/nargsf/kwnames和单次原binder/原call规则不变，不加新arity限制。

state.raw source门还要核本次actual Proxy正是原创建state槽所持对象、同代phase和相应原callee/source；来源是本方实际创建/独立T0根，不是type/name/identity检查自证。module退休后旧op从持续Proxy寿命前缀取得拒绝根，不能从已经取消发布的arena补根。原U-total慢路/共享loader变化与COMPAT均保留，完整constructor/caller和实际三body仍待接线。此选择只消除Proxy“另有哪些存储字段”歧义，不表示整个source已闭。

## 5. 模块metadata lease的取得与持有

原每次C调用先由真实receiver/module寿命链取得独立module_guard，公开state accessor仍在准备区，立即按r1140指针/indicator四组合分类。非NULL且无error后，raw核同module/state/phase、arena_owner=STATE、published_arena是原内联arena、本次普通操作所需capture_complete=1和row_ready成立、active_count不会溢出，再增meta_active_count；借用arena时state仍独立拥有其各槽。phase必须满足该原调用阶段，不能把ACTIVE门用于删除原PREPARING合法创建过程；bootstrap内部lease只由实际原Py_mod_exec创建路径在UNENTERED→CAPTURING的raw初始化后取得：真实creation_module_guard已持有，state/代/原内联arena均已核且归STATE、未退休、计数加法合法；此内部入口不要求capture_complete或row_ready，其用途正是覆盖原值捕获及表示转换。普通外部操作不得用CAPTURING/内部入口加入。capture_complete最初0，固定根与全部私有原值scratch真正持有后才raw一次置1并允许ROOTS_FROZEN；它不表示kw_entries/cells等tuple槽已齐。各row_ready初始0，仅在本行原值已完整捕获、两snapshot容器已fresh成功move入空槽并记ledger后置1。快照运输仍持同一bootstrap lease，不能为取得该lease反过来要求snapshot槽先齐；失败/退休不补标记、不重新捕获，按实际partial结账。

每个lease都有私有C自动状态，记录是否实际增count和独立module_guard；不返回Python token，不存state最近lease，不依靠弱引用、secret或first-use。增count之前失败不leave；成功后每出口恰好leave一次。module_guard从取得起一直覆盖最后state/inline arena访问，不能放入提前清理的普通call-local列表。

callback期间lease仅保持存储/拥有根，不保证phase或current metadata不变。每次callback后及原实际消费点仍fresh，RETIRING即停止新业务；不得因为arena还在就调用旧callee。清理对已有leased roots的延迟不阻止Record取消、Proxy退休或共享source阶段更新。各层本来需要的actual callee/结果/异常/参数拥有责任不由lease计数替代。

## 6. m_clear、最后leave与m_free三入口

module.m_clear先在首callout前置本代RETIRING、停止新lease/登记，锁存r1151 record_exit_mode=CLEAR，并标实际Proxy退休。Record drain及其它source退休按各自协议进行；重入仅移交单调取消，不重开drainer。不把所有字段推迟退休，只推迟仍被lease覆盖arena的拥有释放。

meta_active_count>0时不detach/release arena、不阻塞等待；m_traverse仍逐个visit state实际拥有的非NULL槽。最后一个已准入lease负责结清。count=0的retire入口当场进入下面的唯一arena接管，不能等一个不会再来的leave。所有其它m_clear/free字段和SCC仍须全表，不能以本段return冒认全模块已清完。

每次leave：先独立拥有实际结果或actual first；count仍包含本次，module_guard仍owned，先结清本层call-local根/参数/临时输出/scratch，逐项从各自owned槽detach再release。完成后在raw段减count。OPEN归零不清arena；RETIRING且归零时，先取消published_arena并把arena_owner从STATE转LOCAL_DRAIN，完整转交固定槽ledger和变长区域的实际拥有责任给私有C自动arena指针。该指针只运输已存在的存储，不新增一个PyObject owner或大栈复制。

接管后m_traverse不再visit该arena引用，重入clear不领同一ledger；未释放引用是当前C退出上下文的强根。cleanup从稳定C scalar长度/本地ledger读，不从已经释放的borrowed code/closure/tuple取长度。逐ledger逆序先将对应PyObject槽NULL并递减拥有标记，再DECREF；跨callback只用尚owned的项。所有子scratch/输出/temp/free责任先结清，再清理arena。固定及扩展ledger的每笔责任全部结清后，在仍持module_guard的raw段一次将arena_owner从LOCAL_DRAIN设为CLEARED；published_arena保持NULL，实际acquired计数/标记均已随释放归零，但不重置同代初始化/退休标记。count=0的retire入口与最后leave共用同一STATE→LOCAL_DRAIN唯一接管条件；重入看到LOCAL_DRAIN/CLEARED只能维持退休与取消，不能重领、重清同一ledger或重开捕获。最后一次state scalar更新必须仍在module_guard有效期间。此后才结清module_guard；最后还原actual first，仅scalar退出，之后不读state或release对象。成功但cleanup首次错误则结清结果后返回该实际first；后继E2按r1135不覆盖first，不存state异常/tb历史。

m_free不要求先m_clear。合法module dealloc/refcount0入口的meta_active_count应为0，因为每个活动lease独立持有module_guard；这是正常拥有图的条件推导，不读refcount认证、不INCREF(self)复活。m_free仅在原调用跨度内完成同一retire/detach/release，不能由本方free md_state；其本来在m_free返回后才被解释器释放。一般NULL、partial、损坏计数、防御路径和实际free/indicator合同仍须完整N4，不能用此正常入口推导代替。

## 7. traverse、获取顺序与新增成本

m_traverse只读匹配版本的actual md_state和本方已初始化scalar，不在GC callback内调用允许callback的公开state accessor或分配。raw module layout/API仍属ABI/build待核。state拥有arena时，按真正取得ledger visit每个非NULL owning occurrence；不同槽即使同指针也不去重，private/nonowning record_head不visit。模块wrapper随后遍历/清理md_dict的原行为不改。

ledger记录实际取得顺序，不把row9字段次序当作acquire次序。T0固定root/每行7个原字段先取得；kw_entries/cells表示转换在原捕获完整后各实际取得；post19按实际类型/实例/错误/key生产顺序追加。容量checked加法/乘法/长度及slot索引在每次写前核，NULL缺席不伪造NewRef或ledger项。partial ledger和private scratch拥有信息不能在整体detach时丢弃。

显式新增：已知103个声明位置及其ledger/scalars，module每个活动lease一笔module_guard与count；snapshot transport临时强根、pair/record/外tuple峰值与读取/扫描计费。没有实测sizeof、峰值或运行成本，保全部原预算，control1MiB不重解释为heap上限。保留原audits/getters/错误次序和三D0/D1/D2，新增准备/容量拒绝及representation/GC观察的COMPAT须与完整原域同审。

## 8. 后继必须补齐

本稿仍缺：变长T0 scratch和完整class/dispatch快照的真实容量来源及累计计费；全部source/A/D/J/Product/seed/carrier/owner/type物理扩展；原producer/caller运输；真实compare→write/delete、原binder及continuation/P0；所有其它state字段、SCC/N4、NULL/Init/free及版本/build/预算。容量限制的条件推导另见CAPACITY-BOUNDARY-01.md，不声称构造过真实预算内超限反例或否定所有其它路线。只有这些同版闭合并双审，才可进入产品候选。B001/B002 open2，四项完整False，Goal active；本批未运行产品/native/模型/QA/诊断或下载。

## 交付与后续入口

本方案的有限复审不批准完整设计、源码或执行；完整目标仍是原产品方案。六函数snapshot根共12个，native另2个anchor，103计数不变。容量分析须与勘误共同读取。

证据目录：data/t16-h01-state-physical-census-r1152/；正文02、两份复审记录、STORAGE-RESEARCH-02.json、CAPACITY-BOUNDARY-01.md与CAPACITY-ERRATUM-01.md均保留。仅完成静态文档检查和交付，不计为产品测试通过。
