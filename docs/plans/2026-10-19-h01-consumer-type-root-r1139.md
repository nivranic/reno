# r1139：Consumer lifetime ReplyType 与自有 Factory 根修订03

r1139选择每实际Consumer保留至dealloc的独立ReplyType owning边，跨source退休仍可创建本次DENIED，由旧try外ACCEPT拒绝；创建端两笔guard、move与partial分账。01的可写m_module自环风险保留；03延续自有immutable Factory入口，并补原FactoryType owning local持续跨GC分配、state另独立引用，修复02遗漏的初始化前窗口。actual实例→type→ht_module保module/state寿命，type_clear与module先detach给出新增有限环breaker条件；实际caller运输、state全字段/一般NULL入口及整图N4仍未闭。B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

02实质红项与Root漏审、01/12段staging及未采用的01版工具保留，不撤回r1112其它NewEx根义务；新tp_call入口可能有tuple/kwargs适配、递归门及返回清理，实际caller路线/预算/first分类仍待。15段完整同版源码、八边十一分支及每模块两对象/两state字段、每Consumer一字段/一长期type引用只给静态合同，实际layout/build与完整heap上限未确认。

本批candidate/产品compile/import/运行/native/模型/QA/诊断0，downloads0，apply0/历史11、源码缺口关闭0、产品pass0。159源/保护56同18缺/原有限额及禁项保持，原>=3.11不缩减；完整Goal仍有工程，非只剩验收。

# ADR-r1139 修订03：Consumer 的 lifetime ReplyType 与自有 Factory 根

状态：修订03，02跨Factory分配type根的实质红项及Root漏审保留；有限子设计待双审；未产生产品源码或执行准入。保留 PROPOSED-CONSUMER-TYPE-LINK-01、FACTORY-MODULE-ROOT-01 及 STAGED-MODULE-BINDING-01 的原字节和未采用状态。03 延续02的自有Factory方向并补初次分配根，只替换 01 尚未采用的新 Consumer 创建入口，不撤回 r1112 其它 NewEx 根及各自 N4 义务。产品仍为 r1106 SOURCE02 完整159文件、未安装；B001/B002 open2，完整 design/source/execution/Goal 均 False。

## 1. 本次解决的具体问题

原 shared state 的 ReplyType 边会因退休移走；仅持 Consumer/module 不保证这笔边持续。三处 CELL_TRY 此时若直接抛本方认证 ValueError，可能被原 getter 的旧 except 吞掉。选择让每个 actual Consumer 独立拥有一笔直到最终 dealloc 才释放的 `reply_type_lifetime`，使源退休后仍能制造本次 DENIED reply，由旧 try 外的 ACCEPT 拒绝。它只保类型寿命，不恢复 source、expected、phase 或调用资格。

01 选普通 PyCFunction 以 m_self 绑定 actual module，该 owning 来源有完整同版源码依据。但另一个 m_module 字段可写，且此类型 tp_clear=NULL；若 callable 可达，合法 `f.__module__ = f` 可形成其自身持有环。单清 module/state 不能证明断开它。独审发现有效，记为条件可达的 N4 缺口，未执行、未声称全路由可达。03 为这一个未采用的新创建入口继续选择自有 Factory 实例，消除其 m_module 字段；不以此声称其它 PyCFunction 根已经破环。

## 2. Factory 的实际创建与固定形状

在原 `Py_mod_exec(actual_module)` 内，以静态 C spec、固定原内建 object 基类和 `PyType_FromModuleAndSpec(actual_module, spec, bases)` 实际创建 FactoryType；actual 返回那一笔先保存在私有 C owning local `factory_type_local`，持续跨 Factory GC_New、Track及发布前fresh核。state只有在原阶段/原slot仍合法时另取一笔独立NewRef；不是把这个local责任move掉。后续发布不读取 current module alias 反认 expected。固定默认 metaclass、完整静态 slots、实际 ABI 与所有类型创建失败仍须 future 源码核对；本文件没有创建该对象。

FactoryType 选择 DEFAULT/HAVE_GC/IMMUTABLETYPE/DISALLOW_INSTANTIATION，排 BASETYPE、MANAGED_DICT 和 MANAGED_WEAKREF；基本结构仅 PyObject_HEAD，没有实例 payload、dict、weakref、m_self/m_module 或公开 owning setter，不挂可变 Python callbacks。固定 tp_call 直接进入自有 C body；tp_traverse 访问实际 heap type；tp_clear=NULL，因为实例无可清的 payload。在上述实际type owning local持续下，使用匹配 GC_New 创建 untracked 实例；GC分配先于Init，未来instance type根不倒推保护分配窗口。成功actual Factory先由独立C owning local持有，正确初始化后Track，所有可能callback准备之后重新核原state/phase/slot；通过才将Factory local那笔move给state并沿原native创建运输。Factory已经存在并正确Init后，其独立type根才允许匹配包络内factory_type_local的nonlast退出；local持续到该点，不能先释放再借state。若分配NULL，无instance/type隐含责任，factory_type_local仍owned且可能last，沿actual first结账。成功但退休/slot变动则不覆盖或补登记state；partial Factory与type local各清一次，再还原本层actual first或走具名准备拒绝（一般NULL入口政策仍待）。与state-owned FactoryType为独立取得，不去重；state退休可移其边，不影响这笔local。

同版 typeobject.c 显示 FromModuleAndSpec 调相同实现、ht_module=NewRef(actual_module)，type_traverse 访问 ht_module，type_clear 清 ht_module/字典/mro。IMMUTABLETYPE 阻止 Python type_setattro；对于这类非 module 实例，__class__ 赋值受 immutable 检查拒绝。DISALLOW_INSTANTIATION 使 tp_new=NULL。无实例 dict/weakref 的形状依赖完整 spec/base/layout，未由这几段源码或 flag 名称单独证明；也不声称阻止任意外部 C 扩展改内存。

这新增一个 FactoryType 和一个 Factory 实例（每独立模块代，不是每次工具 call），不是新增 PyCFunction_NewEx，因此这个入口没有 01 的 m_module=self 边。type 自身字典/mro/slot wrappers 仍有 owning 图，必须按下述实际 type/module breaker 处理，不能因 immutable 就不计入 GC。

## 3. Factory receiver 到 module/state 的寿命链

条件链为：actual caller 的真实 callable operand 强持原 Factory → 正确初始化实例隐含持其原 FactoryType → FromModuleAndSpec 所建 ht_module 持实际 module → module 持有效 state 内存。tp_call 的 self 是实际 receiver，原 args/kwargs 另传，不接受用户参数中的 module/type 作为来源。Type_GetModule 的借用返回只能在此持续根和匹配 raw 引用包络下使用；无关联/已清的情形走原调用点准备失败，工具 call0，不从 sys.modules/current alias 补根。

该链不是 caller 身份、完整原源码来源、业务资格或秘密；任何能拿到 Factory 的调用者仍须通过实际 A/J/Product/facts 与逐叶原 fresh 验证。actual caller 怎样得到原 Factory、P0/38context/CORE/import/owner14 怎样运输真实参数，仍是完整接线义务。GC get_referents 能暴露边，不以不可见性建立信任。

`PyType_GetModule` 的已读 body 核 heap flag/ht_module 后给借用值；`PyType_GetModuleState` 再调内部 _PyModule_GetState，本批没有读取其 inline body，不把 wrapper 当所有 build 下无 callback 的证明。实现时可选择明确匹配的 state API，但须把原 module 根、NULL state、阶段与入前失败列入源码审查；本批没有猜其 body。

### Factory 调用的入前适配成本

本候选只有tp_call而没有vectorcall字段；若future实际caller走vectorcall→tp_call，同版call.c198–250会先把实参转为tuple，非空keyword names tuple还会建dict，再作递归检查，C body返回后释放适配temp并核function result。不能沿用01普通FASTCALL的无该适配成本结论；actual caller未选，也不声称每次必分配新tuple/dict。入前适配NULL/递归拒绝意味着Factory body未进入、Consumer未创建、工具call0；返回后的temp释放亦可能callback，不能当末尾仅scalar的自有C终段。future实际caller须把适配owned/temp、first传播及额外分配预算与原finally/COMPAT一起核，不禁用这些检查，也不以本批提出未经论证的绕过入口。

## 4. Consumer 创建：两笔 guard 分开取得和结清

所有创建准备发生在给六工具组装 mandatory positional-only Consumer 真参数时，原工具 call 之前；不在三个旧 getter try 内补建来源。Factory receiver/module 连续根先成立，在 state 的原 ConsumerType 与 ReplyType 均存在且原 phase 合法的匹配 full API 非 debug raw 段，分别取得 consumer-type guard 和 reply-type guard；读字段到 NewRef 之间没有 allocator/getter/DECREF/锁等待/释放 GIL。原类型来源来自实际创建，不接受当前属性登记。

两 guard 跨 Consumer GC_New；NULL 没有实例，两 guard 都可能 last。已有 actual preparation first 沿 r1137/r1135：先 detach/结清本层所有 owned/temp/free，最后还原 first，仅 scalar 返回。不能套成功 nonlast，也不把新增准备失败冒认原工具/原 getter 已进入。一般 NULL→NULL 与 allocator indicator 仍未闭。

成功实例保持 untracked，先置 owning 字段为 NULL；将 reply-type guard 那一笔 move 到 `reply_type_lifetime`，自动槽置 NULL，不再重复释放。成功实例独立隐含 ConsumerType 责任按 r1138 官方 heap 合同成立后，consumer-type guard 才可在匹配 nonlast 包络下结清。其它 A/J/Product/source 的 acquire/move/失败次序按其原真实 producer 接线，不由本表补造；全部有效后才 Track/发布。若 source 填充失败，未 move 的 ReplyType 归自动 guard，已 move 的仅归 partial 实例，各释放一次。

## 5. 发布后 lifetime 边与三处 cell 协议

`reply_type_lifetime` 发布后无 setter，直到 Consumer 最终 dealloc 才结清；Consumer.tp_clear 与 source/phase 退休均不能 detach 它。actual receiver 由本次真实参数/operand 连续持根，故不会在同一区间最终 dealloc；raw lifetime 字段读取到逐调用 type guard 的 acquire 可由这笔独立根覆盖。真实 ABI/receiver/字段/调用缓冲和引用包络仍需 future 源码验证。

逐调用 type guard 跨 r1136 的空 untracked Reply GC_New 与原 getter。shared state 已退休也有这笔原类型可建 DENIED；准备之后的 source/fresh 核拒绝时 getter0，payload 固定后 Track，并运输该次 actual reply。旧 try 外 ACCEPT 拒绝；不在旧 try 内抛本方认证 ValueError、不重新登记 expected、不恢复 eligible。原 getter 的 ValueError/其它错误仍由原三个 handler 处理；late matcher、本方 C 入口失败和 shell 分配错误分别列入完整 try 矩阵，不能用本子图宣布已关闭。

成功 Reply 的隐含 type 根支持额外 guard 的条件 nonlast 退出；失败 guard 可能 last，仍沿 actual first 路线。所有实例/type 取得均独立计账，即使 identity 相同也不去重。

## 6. 新 owning 边及具体 breaker

| 边/责任 | 取得与持续区间 | traverse/清理合同 |
| --- | --- | --- |
| state→FactoryType | 从 actual type owning local 另独立 acquire；至 module detach | m_traverse 访问；m_clear 先 detach 再释放 |
| state→Factory | actual GC_New 返回 move；至 module detach | m_traverse 访问；m_clear 先 detach 再释放 |
| Factory→FactoryType | 正确 Init 的实例隐含责任；至自身 free 后 | 实例 traverse 访问；dealloc UnTrack、GC_Del、再释放保存的 type，不再读 self |
| FactoryType→module | FromModuleAndSpec 的实际 ht_module NewRef | type_traverse；同版 type_clear 清 ht_module，type dealloc 为另一最终路径 |
| Consumer→ReplyType | 创建端 guard move；至 Consumer dealloc | Consumer traverse 访问；tp_clear 保留，dealloc detach 后释放 |
| Consumer→ConsumerType | 正确 Init 的隐含独立责任 | traverse；GC_Del 后释放实际保存的 type |
| Consumer→mutable sources | 各原 producer 的实际 acquire/move | tp_clear 先全部 detach、置拒绝态再释放；完整逐字段顺序仍待 |

module m_clear 的要求：先以 scalar 转 RETIRING、detach 本代全部真实 state-owned Factory/各 type/source/consumer 根到 C 私有释放槽，再允许任一可能 callback 的释放；不能只删公开 module dict alias。只清本批两个字段不足以证明全 module 图，完整原 state 字段表仍待。重复 clear 看 NULL 安全；m_free/partial 初始化仍须同一物理 state 表，不把“有 module 根”当 state 字段永不变。

对新增有限环 `module/state→Factory→FactoryType→module` 和 `module/state→FactoryType→module`，module detach 对应 state 边与既有 type_clear 的 ht_module/dict/mro 是具名 breaker。factory 无实例 payload/self setter，故没有 01 新入口的 m_module 自环。若外部仍强持 Factory/Consumer/Reply，延长 type/module 是合法活跃寿命，不能清 lifetime 强制回收。GC/type_clear 不在外部真实根仍可达时任意切断这条链；关停/重入/失败状态仍须容错，不能依赖正常 GC 语句豁免。

Consumer/source 与 reply→consumer→bank/function/cell 等其它 SCC 未闭，必须逐边找实际 mutable breaker；不能把 type_clear 的存在当全部环可回收。原 r1112 NewEx 根的 m_module 可达性与 breaker 义务仍保留。本批没有调用 GC、观察真实 refcount、frames 或运行析构。

## 7. 增量预算、失败与兼容边界

- 每实际模块代：新增 1 FactoryType、1 Factory；state 两 owning 字段；初次创建另有持续 factory_type_local 与成功Factory local，前者跨alloc/Track/fresh后才按成功nonlast或失败possibly-last结账，后者发布move或partial释放；Factory 隐含 type 和 type 的 ht_module 各为独立责任。类型构造内部 dict/mro/descriptor/分配不能按两个指针估成完整 heap 成本。
- 每 actual Consumer：新增 1 pointer 字段及 1 长期 ReplyType owning；创建期间两笔 type guard，ReplyType move 后该自动责任归零；隐含 ConsumerType 非新增虚构 INCREF。
- 每 CELL_TRY：沿 r1137 独立逐调用 ReplyType guard 和空 Reply，没新增最近槽/全局 reservation/BUSY/first-use 记录。额外 guard 的时间峰值与所有真实 caller 要纳入完整预算，尚无实测字节上限。
- 类型创建/Factory 创建/Consumer 创建/source 填充/Track 发布/已退休来源分别列 partial；guard acquired/moved/NULL 与 instance 是否存在不可混账。Factory 失败没有 Consumer；Consumer 失败没有原工具 call；cell shell 失败不是 getter 已调用。
- 新 Factory 参数协议/原 callable 运输/type 可见性/错误分类是显式兼容工作；无新增 Python 版本缩减，原 >=3.11 保持，当前同版 3.12.10 静态证据不代其它版本/build。公开 GC_Init 合同不代缺失 raw 头或真实构建证明。

## 8. 后续源码级义务

给实际 Factory 创建与运输、module state 完整原字段、Consumer 全字段/guard/move/退出、三 cell full try、成功 allocator/一般 NULL 入口建立同一来源版本；所有 owning slot 对应 acquire→持续 root→detach→release，明确每个 SCC breaker、失败 partial 与外部保留。再与完整 P0、38context、CORE、IMPORT、owner14/bank9、D1/D2、N4、COMPAT、原预算及 >=3.11 接回。

本批只允许文档及静态 read/hash/AST/diff 验证，不生成产品候选、产品 compile/import/执行、native build/load、模型、QA 或诊断；不重放旧单次诊断/失败下载，不改真实库、服务/dist、ACL/settings、平台或 Git。原17lease/1449slot/406尾/五S002、case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10均保持，control1MiB不是已确认 native heap 上限。P06历史0/7无材料不再索取，完整范围并非只剩验收。

# 下一批：完整 state/source 与 NULL 入口

从本批已分开的Factory actual创建根、Consumer lifetime ReplyType和两guard/move合同，继续核实际原Factory caller运输与module state全字段：每个source/consumer/type/root有明确acquire、持续根、detach、可能callback释放、partial与重复clear处理。先退休和detach全部实际state边再释放，不以只删公开alias代替；保留所有r1112 NewEx根的m_module可达性/SCC义务。

完整Consumer/source及Reply/owner14/bank9的SCC继续给具名breaker；一般NULL→NULL、成功allocator indicator、free/guard/最后type释放与三原cell完整try矩阵仍须同批关闭，已有first沿r1135/r1137，不能把本方认证错误放旧getter try里。

最后与原P0/38context/CORE/import、226候选叶/7原append/3reply/14ownerproducer、D1/D2、N4、COMPAT及>=3.11/构建/原预算接回同一完整设计。完整设计双审前不生成产品候选；源码准入前不产品compile/import/运行/native/模型/QA/诊断。保护/旧红/已耗单次授权保持，P06历史0/7无材料不再索取，Goal active。
