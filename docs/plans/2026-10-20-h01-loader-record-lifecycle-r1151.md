# r1151：LoaderRecord八字段与单调退出协议

r1151选择LoaderRecord八个独立owning payload、非拥有登记表和每轮own-head/unlink的有限生命周期方向。原01红审L001/L002和02剩余取消分支L003保留；修订03规定restore_cancelled由actual record_guard持续运输，联合NONE→RESTORE→CLEAR与真实恢复消费点复核，actual first私有持有后清完余表。实际条件恢复helper、完整C005/state/metadata、caller/原域/COMPAT与N4/ABI/build仍未闭；B001/B002 open2、完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

六份既有tagged v3.12.10完整源/文档181295B与17段原文核对。Record前七字段的guard早于分配，installed_bound只能来自后继实际Method_New；source六字段detach，module/error持续至dealloc，旧bound及__func__晚调用不重新授信。原Method/Cfunction没有tp_clear，Record切断自有bound环；外部保留合法延长寿命。登记不强持Record，不把裸next跨callback；dealloc在首callout前unlink且不INCREF(self)。这些条件方向不代替完整构造、所有退出路径和一般NULL/free实现。

修订03将单Record取消与全代清理分开：Record.clear首callout前单调置restore_cancelled，正常unlink不置位；已detach输入的当前helper持续持record_guard，callback后和真正write/delete点联合读mode/bit，观察当前bit后升级全代CLEAR。module.m_clear/m_free先锁存CLEAR再判断busy，另一个Record的单独clear只取消它。构造器返回也先核取消/阶段再继续构造或move，不能补回已clear的partial。首个实际失败后停止全部恢复、私有C自动槽保first并继续clear余head，module_guard有效时清draining，结清拥有责任后最后还原first；无错误取消用scalar outcome区分clear-only。尚无已实现helper；实际error/return/effect及GC存活/可被clear分列。

父r1150的24封存文件3322186B、159源码7956657B及保护56同18严格lstat缺席保持。本批只做静态read/hash/AST、完整diff/逆像、双审与文档交付；产品candidate/compile/import/执行、native、模型、QA、诊断及下载0，apply0/历史11、产品pass0。原限额/禁项保持，正常库/服务/dist/ACL/settings/真实平台/Git写入未操作；不重放旧单次授权或终态下载，P06无历史身份材料不再索取。完整Goal仍有工程，非只剩验收。

# ADR-r1151 修订03：八字段、独立取消位与全表退出

状态：选择修订03的八字段与有限生命周期方向，正式双审及交付见本批报告。原01红审L001/L002、原02剩余Record.clear取消分支L003和修订历史保留；本版规定独立取消位运输、单调清理模式和首错后全表clear，不是完整安装恢复算法、C005/state/metadata、原调用域或整图 N4。父 r1150 的24文件3322186B保持封存。没有产品 C 文件、代码生成、native构建、诊断或运行对象观察；以下仅为匹配 v3.12.10/full API/GIL/既有引用包络的设计。

## 1. 记录拥有八个 payload，生命周期分两组

原 NewEx 的 C self 是本次实际 Record，原 Method_New 的 im_self 是 actual loader，两者及本次 binder 的 self 槽继续分开。Record 没有公开构造、call、实例 dict、weakref 或 payload setter；type 用原固定 spec、原 object base、IMMUTABLETYPE/DISALLOW_INSTANTIATION/HAVE_GC、不设 BASETYPE。IMMUTABLETYPE 不表示实例字段不可由本方 C 生命周期路径改变。

| owning 字段 | 独立取得来源 | 何时结清 |
| --- | --- | --- |
| module_lifetime | 本次原 native module，经独立创建 guard 覆盖后 move | 直到 Record dealloc；普通 retire/tp_clear 不清 |
| retired_error_class | 已接受原 C RuntimeError 根的独立 guard | 直到 dealloc；不从 builtins/current module 重取 |
| attribute_key | 固定 native C literal 经真实 Unicode 创建返回的 exact-str `exec_module`，直接持 actual newref | retire/clear 的 detached temp；不是原函数的来源证书 |
| owner_proxy | 创建端已持有的本代 actual Proxy 独立引用 | retire/clear；用实际 Proxy 关联原代，不从本次 self 重认领 |
| original_loader | 本次原 finder/spec 路径实际选中并验证的 loader，另笔强持 | retire/clear；不靠 outer Method 自动引用抵消此笔 |
| loader_dict | 创建端实际取得、并与 actual loader 独立关联的 exact dict，另笔拥有 | retire/clear；其取得/当前关联 API 与原 source 门仍需完整接线 |
| previous_value | 安装准备时该 actual dict 中原有 exec_module 的实际 value；只有 previous_present=True 时有独立引用 | retire/clear；缺席是 scalar false/NULL，存在且为 None 是 true/Py_None owning |
| installed_bound | 该次真实 Method_New 返回的新引用，直接 move；其 im_func 真实来源为本次 NewEx 返回 | retire/clear；不能安装后从 current 属性读回来作为 expected |

以上是所选 Record 的全部直接 owning payload；隐含实例 heap type 是第九类独立拥有责任。它不列出 Proxy、state、C005 或 binder 的全部字段。original_loader/dict/value 等输入须来自已认可原生产者的真正 C 局部，不提供接受外部 Python subject 登记 expected 的接口；真实 producer/完整 caller 尚未因此接完。

Record 另有非 owning C 字段 registry_owner_state、prev、next，以及 linked、phase 和 previous_present 等 scalar。phase 为 INIT/PREPARED/INSTALLING/ACTIVE/RETIRING/RETIRED/FAILED；固定 store_attempted/store_returned/effect_observed 及 restore 结果只记该对象生命周期事实，不保存调用历史、异常/tb、最近结果或 runtime journal。所有 pointer/scalar 在实例仍 untracked 时初始化，其中restore_cancelled初始为0；公开后该位只能0→1，不能因unlink、draining归零或旧调用重入而重置。Record.tp_clear、失败清理和dealloc在首callout前置1；正常own-head/unlink只选择退休恢复，不因此置1。公开后phase/source字段仅由本方原生命周期路径改变。

module_lifetime 不依赖可能被 type_clear 移走的 ht_module，也不依赖可写 PyCFunction.m_module。选择 NewEx 的创建 module 参数为 NULL，并独立保存这个创建事实：NULL 与后来写入 Py_None 不等同。原 callable metadata/source 检查仍须接线，不能以这个初值代替 Record 的 module 字段。

## 2. 创建、可见 partial 和发布

创建端先有本次 module、实际 RecordType 及除 installed_bound 外前七个 payload 的独立 owning guards（previous 缺席时无该笔）；installed_bound 必须等 Record 真实产生后再创建，不能倒置这条循环依赖。RecordType 的原实际创建返回和 state 的引用另计。分配 NULL 没有实例；成功 Init 后 untracked，所有字段 NULL，再逐笔 move 已取得 guard。字段尚未 move 的责任留在自动槽，已经 move 的仅归 partial；不得用未来实例根覆盖此前分配窗口。full Init/layout/NULL 与原 module 参数起点仍沿既有未闭项。

先完整建立 module/error/source/key/previous 字段，installed_bound 暂为 NULL，设 PREPARED 后才 Track。实际 NewEx 返回由独立 C local 持有，m_self 会另增 Record 引用；随后Method_New返回另由C local持有。每个标准构造器返回后先在无callback段核actual Record仍处于本次允许阶段、restore_cancelled==0及原source关联，才继续下一构造或把实际bound move入installed_bound；不得在callback已clear的partial上补回source。若取消/失配，只沿实际local/partial清理，保构造器实际escaping first；新增准备拒绝不能覆盖原构造错误。外层 Method 自动持有内层 callable 和 loader，Record 的八笔不因此合并。

两个标准构造器都会 Track 实际产物。后续分配/回调可能让 gc.get_objects/get_referents 取得尚未写入 loader 的 callable/Record；不能称这些对象秘密或从未发布。PREPARED/INSTALLING 的内层调用只沿持续原 error 根作新增准备拒绝，不建立 attempt/seed。此新增 partial API 的可见性和错误属于 COMPAT，不能推广为全部正常 direct 域已经处理。

安装前全部任意 extra 释放、可回调准备和 fresh 按原方案完成。STORE_ATTEMPTED、实际 STORE 返回、当前 dict effect 与源码/代际再核分列；只有 STORE 正常且 raw effect/来源仍成立，才能在同一无 callback 的本方 C 段标 ACTIVE 并登记。成功末尾每笔 constructor guard 的 nonlast 必须逐笔有实际独立根覆盖，不能笼统说所有清理无 callback。

安装失败或实际 effect/source 失配先 FAILED/退休，分离全部已取得 source 字段再结清，保 actual escaping first；partial 的外部 retain 合法，不能强销。installed_bound→inner→Record 构成直接自环，失败 cleanup 必须切断 Record.installed_bound，不依赖 importlib 删除模块来回收。

## 3. 登记表不拥有记录，也不保存跨回调裸游标

提议每 actual native module state 有 record_head 裸指针、record_draining scalar 和 record_exit_mode scalar。模式按 NONE→RESTORE→CLEAR 单调推进；module.m_clear/m_free的本代清理请求、本层实际失败或当前helper观察到本Record取消位时都锁存CLEAR；单独clear另一个Record只取消并unlink那个Record，不自动取消其余记录。不在同代退休途中或旧C局部尚存时降回 RESTORE/NONE。新模块state从零初始化不代表允许旧代重置/重新授信。每 Record 的 prev/next/owner_state 只构成私有双链登记关系，均不是 PyObject owning 引用；state.traverse 不访问这些非拥有边。RecordType 的 state owning 引用另列。这样没有 state→所有 Record 的新强持有表，也不新增 Python weakref/callback 或按 refcount 采样清理。

这是实际存活安装记录的索引，不是来源授信：只有已实际创建、安装 effect 成立的原 C 路径能插入。插入以持续的 constructor Record owning local 为根，module_lifetime 已建立；所有 pointer/scalar 更新在同一 GIL、无对象释放/分配/回调段完成。登记完成时记录为 ACTIVE/linked，指向这个真实 state。不能由裸地址/type/name 检查造出这一不变量。

每个离开 ACTIVE 的原路径在任何可回调操作前先断链：调整真实邻居或 state.head，清自身 prev/next/owner_state/linked，再改/保持退休阶段。tp_clear 和 dealloc 的开头也执行该固定 C 指针段。dealloc 可能以 refcount0 进入，绝不 NewRef(self) 复活；其首个可能 callout/任意 release 之前已断链。先断链不清 owning payload，GC UnTrack 与后续真正字段清理仍须执行。

由此在合法同解释器 GIL/真实布局包络中，登记表只能指向尚未进入可回调析构的存活对象；refcount0 析构进入后不会把仍登记的对象暴露给另一段 Python/GIL 操作。退休遍历的 acquire 依赖这条逐路径不变量，不读取 refcount 来认证地址。完整 C 实现仍须核所有退出无漏 unlink、全部 inline/build 和错误路径；单凭本表不能运行裸指针。

明确退休的遍历协议为：先把本代 Proxy/state 置 RETIRING，禁止新登记；每轮从当下 head 取得一笔真正 record_guard，立即先断链并标 RETIRING，之后才准备/执行本轮恢复和清理。回调返回后结清该轮 guard，再重新读取当下 head；不保存裸 next 过 callback，不在释放 guard 后读该 Record，不按旧索引继续。

恢复回调可以释放其它 loader/记录，使其它节点 dealloc/unlink；也可以再入退休或GC清理。清理入口在首个可能callout之前先将本代置RETIRING并把 record_exit_mode 锁存CLEAR，再判断 record_draining；busy时不另开遍历，但清理请求绝不只当作“已经退休”而丢弃。正常退休只可从NONE选RESTORE，已为CLEAR则保持CLEAR，不能降级。外层每轮重新读模式；本轮已detach输入的恢复helper同时接受本次实际record_guard所覆盖的Record指针，并在每次callback后及真正write/delete消费点联合重核record_exit_mode与restore_cancelled。record_guard必须持续到helper结束和本轮清理，不借已清owner_state或source字段重新认领Record。helper观察到取消位1即在持续module_guard覆盖的实际state上锁存CLEAR，此后只结清本轮C局部和后续记录，不再发起业务恢复写；每个真实effect点都检查二者，不能先做写再报告取消。该要求覆盖helper内部的分配/watcher窗口，外层API调用前读一次不够；尚无满足该点检查的实际helper，不作已实现批准。已发生的effect不因取消被抹去。record_draining只表示当前drainer；其拥有责任与单调模式分开，真实module/当前record guards仅保存活，不使对象免于clear。回调后不从旧head/next认领对象，禁止新登记使有限旧集合递减；不设固定轮数/重试或强registry保留。

## 4. 正常退休恢复和 GC 清理采用不同入口

只有明确的正常卸装/代际退休路径尝试恢复，且本轮先持 record_guard，再把 attribute_key/proxy/loader/dict/previous/bound 六字段一次 detach 到真实 owning C locals，Record留module/error两个寿命字段及restore_cancelled。恢复输入来自本轮detached C locals，取消信号来自同一仍有record_guard覆盖的Record scalar，两者明确分开。脱离前不DECREF，脱离后不再从已空字段读取恢复输入；已有第一异常和新增恢复失败按实际层次处理，不把强持根当写入原子性证明。

恢复必须核本次当前 loader→dict 关联和实际 key/value：仅当前同一 actual dict 的 value 仍 identity==本次 actual installed_bound 时才有本方恢复资格。previous_present=True 恢复当次原 value，False 删除本方项；外部新值/新 dict 保持。比较不得运行 value 的 __eq__。当前字典全键前验、已准备 key、真正 compare→write/delete 间的 watcher/分配/回调窗口和失败 effect 必须由后继完整原子准入路径实现，不能盲用 PyDict_SetItem 的 precheck 冒认整个条件写入已闭。本批只规定输入根与阶段/遍历，不批准这个尚未实现的恢复 helper。

恢复的actual error/返回/effect分开。选择首个本层准备/恢复/清理失败后停止所有后续RESTORE：立即将actual escaping first移到外层私有C自动owned槽（3.12单异常、3.11三slot分别按原合同），锁存CLEAR，然后结清当前轮detached source/结果/record_guard，继续逐head own→unlink→clear余下记录，不能直接返回。后继E2按r1135逐层结账，不覆盖已持first、不另写Record/state异常历史；原helper内部的实际finally/覆盖先自然结束再选逃逸first，内部temp仍须由helper自己完成。已有first跨后继全部清理，不能为继续drain把它提前安装回indicator。head归空、没有新登记可能时，在实际module_guard仍有效的同一有限段清record_draining；随后结清全部剩余owned/temp/guard，最后还原实际first并仅scalar失败返回，之后不访问state/Record或释放对象。没有actual first而模式转CLEAR时正常完成clear-only；调用方用C scalar outcome区分RESTORED与CLEAR_ONLY/CANCELLED，不能将其报成原安装恢复完成。模式不因draining清零而回退。失败不重试覆盖外部写入、不复活seed；Record最终留RETIRED/FAILED及持续module/error供旧对象拒绝。外部outer Method自己的loader及Cfunction.m_self不强制切断。

Record.tp_clear不主动恢复loader dict，不构造新的业务绑定，也不运行get_code/helper/exec。其首个可能callout前先将本Record的restore_cancelled置1，再退休/断链、detach六source字段，短锁外结清；即使owner_state早已unlink、source早已detach，该取消位仍保留供持有本次record_guard的helper消费，无需调用state accessor。对不在本次helper中的其它Record直接clear只取消并unlink该Record；若本轮helper看到当前Record取消，选择升级整个本代为CLEAR，停止后续恢复。重复调用可见空字段，module/error 仍到 dealloc。GC 能收集 Record→bound→inner→Record 及经 loader.dict 的自有环，因为 Record.clear 会切断 bound/loader/source 边。原 Method/Cfunction 的 tp_clear 均空，不误称它们替本方破环。

module.m_clear及无需先m_clear的m_free走CLEAR入口，不偷换成正常卸装恢复：首callout前先停止新登记并锁存CLEAR，再处理draining重入；外层仍在时只移交单调取消请求，不开第二遍历、也不把模式降回RESTORE。任何仍在表内的record只按同一own-head/unlink协议clear，不写loader dict；每轮失败仍按前述私有first/full-drain合同完成余表。原module其它source字段/metadata leases的清理与重入先后仍属完整N4待项，不能借这个return表示所有字段已经清完。GC调用module/Record tp_clear的暂时存活根在同版delete_garbage的INCREF→clear→DECREF中有具体依据，但不证明对象不会被clear；本方直接调用clear必须另持owning guard，dealloc不取self guard。其它版本/合法C caller有效性另证。

每个已登记 Record 都先有不可清的 module_lifetime，因此合法 module dealloc/m_free 的 refcount0 入口不可能同时仍有一个这样的记录强持该 module；最后 Record 在释放 module_lifetime 前已经 unlink。此为正常拥有图的条件推导，不是读取当前 refcount 或假定 m_clear 必然先执行。partial/一般 NULL/free、module 自己全部字段的清理及对不变量破坏的处置仍须共同 N4；不把一条正常 m_free 推导当完整防御实现。

## 5. 调用期 source、原 binder 和旧对象

实际 inner 的 C self 是 Record，外层实际 callable operand/Cfunction m_self 的持续根与匹配 C API 调用前提必须成立。ACTIVE 路径仍按 r1149 对本次 actual argv/kwnames 做完整原参数算法，再用本次绑定 self/module 与 Record 独立 source 联合门；原 defaults/qualname per-use 和代码 guard 不从 T0 值或 outer im_self 替换。

Record 模块 guard 从持续 module_lifetime 取得，再按 r1140 的 state 指针/indicator 四组合处理。任何 callback 前先取得本次真正要用的独立 source/root/参数 guards；callback 后重核 Record/Proxy/state 阶段与原关联，不读取已 detach 的 source 字段继续业务。模块内 metadata 数组/缓冲的 lease 和完整 C005 物理表仍未闭，module 内存存活不代表逻辑 source 或数组永远可用。

若 binder keyword 回调令本代退休，原 binder 自己的错误和 actual first 保持；正常绑定后来源拒绝是新增准备/COMPAT，不冒称原 binder 错误。RETIRING/RETIRED/FAILED 或 partial 内层入口可用持续 error 根拒绝，不从已经清理的 C005/type.ht_module 取错误类，也不据旧对象重装原槽或重新授信。完整 direct 成功域、原 frame/递归与错误次序仍须共同审查。

## 6. traverse、dealloc 与新增成本

traverse 逐一访问八个实际 owning payload（previous 缺席允许 NULL）以及真实 heap type，指针相同的独立 owning occurrence 不去重；不访问 registry 非拥有边。dealloc不增self引用，首callout前置restore_cancelled=1并按上述raw段断链，然后UnTrack、标不可调用、detach 全部残留 payload 并保存既有 type 责任。先结清 source 六笔，再 error/module 两笔，匹配 GC_Del free 实例后才结清保存的 type；free 后不读 self。已有 first 按 r1135 在全部实际清理结束后最后还原；NULL 入口、allocator/free 和 maylast 依然逐 site 待核，不能用成功 guard nonlast 覆盖析构。

新增 schema 为八个 Record owning payload＋隐含实例 type、三个非拥有C指针、一个单调restore_cancelled及其它固定scalar，state另有一个 RecordType owning role、非拥有head，以及draining/exit_mode两个独立scalar。NewEx/Method 的框架拥有、type→module、字典/descriptor/原业务值的间接边另计。没有 strong registry→Record 边，不等于零间接保留或整图 GC 已闭；外部保留旧方法合法延长 Record/module 寿命。原 control1MiB 不是确认的 heap 界，实际 sizeof/峰值和每步扫描计费仍待。

待闭的是：完整 C005/state 与 metadata leases、真实 constructor/caller 与 loader dict API、条件 restore helper 的实际准入/错误/effect、完整 binder/continuation/P0/A/J/Product/K/D1/D2、全 SCC/N4/版本/build/预算。以上未完成前不生成产品候选；B001/B002 open2、四项完整 False、Goal active。

# r1151 后继工程

1. 汇总完整C005/state物理字段表：T0固定根、原class/dict/MRO/function-row、各heap type/Factory/Ingress/Proxy/Record、错误根、非拥有head及scalar逐项列实际producer、独立acquire/move、traverse、retire/clear/dealloc和partial责任；metadata leases单列，不能把原26条角色目录冒认为全量物理表。
2. 补真实loader→dict取得及关联API、Record constructor/caller运输和条件compare→write/delete helper。必须在真实effect消费点覆盖watcher/分配窗口、record_exit_mode和actual record_guard所持restore_cancelled；外层precheck或普通PyDict_SetItem不能冒认为原子恢复。构造器返回后先fresh再move/继续，各返回/异常/partial effect与全表退出按修订03接线。
3. 完成module.m_clear/m_free与Record.clear的重入顺序、全部原state/source字段和metadata lease释放；八字段、非拥有索引和有限自环不代整图SCC/N4。逐slot一般NULL/free、Init/layout、ABI/build和实际heap/扫描预算继续待核。
4. 按r1149接完整binder、原错误helper内部temp/first及actual per-use defaults/qualname；完成get_code→name→None第二name或原helper/exec/dict/隐式None及actual K/G/L/attempt/seed，联合Proxy/Ingress/P0/A/J/Product/caller/D1/D2保原域/COMPAT。完整设计双审前不生成产品候选。
5. 原Task9A9B、111/94/r919、真实Store、P06、条件T15和生产云继续，P06七历史身份无材料不再索取。有限生命周期方向不代完整Goal或产品验收。

只复用同一只读独立复审代理；不新增产品/native/模型/QA/诊断、正常库/服务/dist/权限/settings/真实平台/Git写入、全局hook/GC/allocator或真实frames/locals操作，不重放旧单次授权及终态下载。本批封存后不追加或改字节。
