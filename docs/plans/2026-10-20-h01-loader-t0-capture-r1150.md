# r1150：C005原exec函数与实际继承来源

r1150明确C005新增原_LoaderBasics/FileLoader及原exec_module的固定T0授信与捕获方向，以raw字典和分别持有的实际MRO关联已有SourceLoader/SourceFileLoader/get_code根；T0 expected、binder每使用点actual与后继fresh分开。公开PyType_GetDict的NEW责任、code参数名字的真实拥有链及新增根的partial/traverse/清理义务明确；完整metadata/state/Record/原域/N4与ABI/build仍未闭。B001/B002 open2、完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

五份既有tagged v3.12.10完整源码536696B、15段原文、四个完整class AST及九份相关设计分别核对。原exec是_LoaderBasics的plain function，MRO和源码继承关系只作静态依据，不冒认实际运行对象已观察；T0新增集合显式列出，不从安装后的loader/当前bound属性补pin。同一代理窄审建议已补明：fresh先关联当前actual class dict与原dict，再于同一raw段完成全键exact-str前验及absent/目标扫描，不能只扫旧dict。原参数code/defaults/qualname按各自时点运输，未将无关function字段扩成新的拒绝谓词；完整原direct域及COMPAT仍待。

父r1149的34封存文件3441083B、159源码7956657B及保护56同18缺保持。本批只做既有文件read/hash/AST、完整diff/逆像和文档交付；产品candidate/compile/import/执行、native、模型、QA、诊断及下载0，apply0/历史11、产品pass0。原限额/禁项保持，正常库/服务/dist/ACL/settings/真实平台/Git写入未操作；旧单次授权不重放，P06无历史身份材料不再索取。完整Goal仍有工程，非只剩验收。

# ADR-r1150：C005 原 exec 函数、实际 MRO 与分阶段字段

状态：选择本批 C005 新增原 exec 函数与继承关联的有限来源方向；正式双审与交付另见本批报告。父 r1149 已封存，研究提议的未选择历史状态保留；本稿不是完整 T0/state/Record 或源码准入。以下基于已有 v3.12.10 原文，实际 build、>=3.11 其它分支和版本化 raw 布局仍须核。没有调用这些 API、访问运行对象或执行模型。

## 1. 固定起点与两个新增原 class

延续 r1112 的同一个 TRUSTED_T0_CAPTURE：原 native 初始化入口开始时，具名 stdlib 原根由已经接受的可信首次导入边界给定。扩展该固定集合，加入同一原 _frozen_importlib_external 字典中的 _LoaderBasics 和 FileLoader，并具名加入原 _LoaderBasics.exec_module plain function；不是把 T0 后推到第一次包装 loader，也不以函数名字/shape/hash 为原来源证书。

外部授信集合确有新增，必须在后继完整设计中显式列出，不能说旧 C005 已经包含这两个 class 或 exec 函数。原 SourceLoader、SourceFileLoader、get_code 继续使用原 C005 的独立 owning 根，原 external module/dict 继续复用 C002 的同一根；语义关联不能凭名称默默产生第二个 owner 或抵消已有取得责任。

从原 external actual exact dict 用 PyDict_Next 扫描；只对 exact Unicode key 用长度/KIND/DATA/READ 与固定 ASCII 名称比较，不执行 hash/eq/getattr/descriptor。找到的 actual value 立即在匹配引用包络内 INCREF 到本次尚空的 owning 槽，之后才继续读取；class 的 metaclass 必须是同一原 PyType_Type。缺席、形状不符或失败保留 partial，不能回调后补齐 expected。不存在以 current loader 属性或当前 bound method 重建原函数的入口。

## 2. 实际继承路径，不用动态 getattr 认证继承

原文本关系是 SourceFileLoader(FileLoader, SourceLoader)、SourceLoader(_LoaderBasics)，两个根 class 默认继承 object。提议在捕获原 class 时分别直接持有其实际 tp_mro tuple；先已有 class 根、核 ready/exact 原 type，再从匹配 full API 字段读实际 MRO 并立即 NewRef，不调用 mro()、PyType_Ready 或 _PyType_Lookup。该直接字段路线是版本化条件方案，尚未用实际 SDK/build 核 layout；不能把源码函数返回字段当跨 ABI 保证。

| 原 class | 原结构校验采用的实际 MRO 顺序 |
| --- | --- |
| _LoaderBasics | actual _LoaderBasics、原 object |
| FileLoader | actual FileLoader、原 object |
| SourceLoader | actual SourceLoader、actual _LoaderBasics、原 object |
| SourceFileLoader | actual SourceFileLoader、actual FileLoader、actual SourceLoader、actual _LoaderBasics、原 object |

表中每项按原 independently held class identity 核；它是已授信对象的结构要求，不自己产生授信。每个 MRO tuple 单独持有，不能仅因 class 活着便假定被替换掉的旧 MRO 仍有效。后继 raw fresh 既核当前 tp_mro 仍是该原 tuple，也核固定长度/各项；不比较 class 对象的 __eq__。

四个 class 的实际字典均用公开 PyType_GetDict 取得 NEW reference 并立即纳入拥有账。其中 SourceLoader/SourceFileLoader 字典若已由原 C005 取得，完整 state 目录选择复用该既有 owning 槽，不再把本表当已多取得一笔；新增 _LoaderBasics/FileLoader 字典各独立 owning。公开 getter 的成功原文只是 lookup_tp_dict＋XNewRef，支持本提议匹配版本的有限成功路径；NULL/错误和完整清理不被该结论涵盖。对 static 原 object 如以后需要字典，仍走正确 interpreter-local helper，不把 tp_dict 一概当真实字典。

在同一无回调捕获段 raw 扫这四个已持字典：SourceFileLoader 与 FileLoader 没有 exec_module/get_code 定义；SourceLoader 没有 exec_module，get_code 是原 C005 plain function；_LoaderBasics 的 exec_module 是本次新增原 plain function。扫描以 raw 字符名称和实际 value 身份判断，不以普通 MRO lookup 代替。原 typeobject.c 的普通查找可能 hash/eq，MRO 缺席还可能 Ready，故本提议不调用它来做所谓无回调认证。

这里的无定义事实必须记录为明确 absent 状态，后继每次 fresh 重新扫描验证；不能用一个新 missing Python sentinel 或 T0 后第一次查到的值代表 expected。后继 fresh 沿 r1127 的 _PyType_GetDict borrowed 路线重新读取本次 actual class dict，先与独立持有的原 dict 关联并核同一对象，不能只扫描旧 dict 就宣称当前 class 仍无定义。在同一个无 callback 段先满足既定完整 exact-str 键前验，再扫原 absent/目标值；raw 跳过非 exact key 仅表示没有匹配本方原字符条目，不能当作普通 Python lookup 的无定义事实。当前 class dict 路线、全键前验和目标扫描之间不得插入 callback/释放/GIL 退出。只读扫描不写 class 字典，不运行 descriptor；未知非目标 key 不参与 hash/eq。本提议不声称全部无关字典 value 都被冻结，后继工具本身要求的全键 exact-str 前验仍另外遵守。

## 3. 新增 exec 函数与绑定所需原链

从原 _LoaderBasics 的 actual type dict 直接取得该 actual exec_module value，核 exact PyFunction_Type 后独立强持。它是 plain function，不通过创建 bound method 再反取 __func__。r1149 的实际 Method_New 仍仅属于后续原 loader 包装，不能反过来成为这个 T0 函数的来源。

| T0 记录 | 具体取得与拥有义务 |
| --- | --- |
| 原 exec function | 上述 raw dict actual value，borrow→own；与原 get_code/function 独立 |
| 原 exec code | PyFunction_GetCode 的 actual borrowed 返回，立即 own；不是 AST 编译物 |
| 原 exec G | PyFunction_GetGlobals 的 actual borrowed 返回，立即 own，并核与原 external dict 是同一 actual dict；相等仍不合并两笔已有取得责任 |
| 原 exec B | 匹配 PyFunctionObject 布局直接取 func_builtins，立即 own；与 C006 helper 的 B 或 builtin exec 不混同，即便实际指针相等 |
| 原 defaults/kwdefaults/closure | 各 C getter 返回的 actual borrowed 指针与 present 位；NULL 合法缺席，不改写为对象 Py_None；非NULL必须有各自拥有及完整子项快照义务 |
| 原 code 参数链 | 由已持 actual code 读取 co_argcount/posonly/kwonly/flags 和 co_localsplusnames；code→tuple→两个原参数名作为持续不可变链，段内借用不伪报新取得 |
| 原诊断字段 | func_qualname/func_name 的 T0 值如需 expected，须另列独立 owning 字段；不自动把它们纳入旧 row9 的拒绝谓词，也不替代 binder 每使用点的 actual qualname guard |
| 原 dispatch | 保存 actual vectorcall scalar，并与既有标准原 _PyFunction_Vectorcall 根关联；func_version 不作来源证书或永久锁 |

本表只把新增 exec 函数接入既有 function-row 需求，不能冒称已给出全部 function/class/module metadata 的物理 state layout、任意 kwdict/cell 数组容量和完整字段/entries 取得顺序。func_doc、func_dict、func_module、annotations、typeparams、weakref 管理等不得因 funcobject.h 有字段便随意加成新的认证拒绝条件；若后续确需保留，必须说明用途、拥有、COMPAT 和预算。也不能调用会懒分配 annotations/dict 的 getter来冒认最小捕获成功段。

原 AST 的 self/module、无默认/closure、无 varargs/varkw/kwonly只提供源码结构依据；运行时 actual code/metadata 必须由固定 T0 原根给出。提议的原代码结构要求为两普通参数、无 posonly/kwonly/varargs/varkw；两个参数名字按实际 code 中的原对象持链和 raw 内容核，不临时创建替代字符串作为 expected。code 字段布局和完整原 code 不变性包络需实际版本核，现有 ceval 使用文本不能代完整 code.h/build。

## 4. 三类观察时点不能合并

T0 保存的是独立原 expected，正常后继参数处理保存的是那个使用点的 actual，最终消费门检查的是当次 current 与原 source 的关系。三个时点分别列账：

1. 捕获完成前不分配 type/record/callable 或发布字典。最小 T0 段仅固定 C/scalar/NULL 槽、raw 读取和已匹配的 NewRef；不做任意 DECREF。全部所需原根/metadata 真正冻结后才可 PREPARING。新增捕获失败仍无 ROOTS_FROZEN，不能仅因本表完成就标整体冻结。
2. r1149 进入原参数算法时，actual binding_code_guard 是新一笔真正取得，原 frame 早先的 co 根另计；如果进入前 code 已变，要按完整 domain/source 方案处理，不把改后 code 偷登记成原 expected。本提议不决定用何种早拒绝来代替未完成的原参数域。
3. keyword 比较可回调。原算法随后实际读取的 defaults/qualname 保原顺序、长 defaults 后缀和 actual-value guard，不能强行替换为 T0 原值或每个字段读取前都插新拒绝。原 binder 错误应按实际算法逃逸；绑定成功后的 source fresh 拒绝是新增准备/COMPAT，不能反称原 binder 错误。如何保完整正常调用域仍待。

准备期或后续 callback 后，不从 current class/MRO/function 补取未完成的 T0 expected；只能核已完成的 independently held 原根。此处不宣称一度 metadata 一致能保证未来 frame/body/D1/D2 不变。

## 5. 清理、目录增量与后继

新增两个 class、两个新增 type dict、四个 MRO 和 exec function/其 metadata 是明确新语义拥有角色；这不是去重后的最终对象数。已有 C002/C005 根复用和相同指针的多笔取得须在完整 state 表逐笔区分。code 的参数链属于它的真实 owning 子边，不把每个 borrowed 名字又计成自动 acquired 引用。

每个实际取得写入空槽才更新 acquired 标记。partial 退出先禁止重捕获并 detach 全部已取得拥有，短锁外按真实取得责任清理，已有 first 按 r1135 最后还原；不得释放仍标在 state 的任意对象后才改状态。GC traverse 访问各真实 owning 槽，相同指针的独立 owning occurrence 不去重。m_clear 与无需先 m_clear 的 m_free 都必须覆盖新增字段。class/function/G 可形成既有及新增环，四 MRO 强根也会延长类寿命；没有证明整个 SCC/N4 已闭或预算满足。

后继仍须给完整 C005/state 的物理字段与 metadata 容量/错误/逐 root 终段、LoaderRecord module lifetime/退休、全 binder/error helpers、direct 调用域及原 frame/递归、实际 loader/spec 安装恢复/共享调用、真正 continuation、P0/A/J/Product/K/D1/D2 与 build。该有限来源提议不生成 attempt/seed、不运行产品；四项完整 False，B001/B002 open2、Goal active。

# r1150 后继工程

1. 把新增固定T0原根、四class字典/MRO及原exec function-row纳入完整state物理表，逐项决定已有根复用或独立acquire，补code/header/布局/版本、metadata容量/计费及捕获失败的精确所有权；未完成全部捕获不得提前ROOTS_FROZEN。
2. 完成LoaderRecord的独立module寿命、source/原loader关联、安装/恢复/共享调用、phase/retire/clear/dealloc和SCC。实际NewEx/Method_New创建返回与STORE/effect分别持有，不从可写m_module或后取实例属性补认原根。
3. 按r1149完成完整版本化binder和全部错误helper内部first/temp，保逐P/Q、双S/R、长defaults、actual qualname每使用点guard及原错误次序；完整原frame/准备/递归/direct域和COMPAT不得以统一拒绝缩减。
4. 接通actual get_code→name→单次返回→None第二name或原helper/exec/dict、隐式None，以及本次K/G/L/attempt/seed。与Proxy/Ingress/P0/A/J/Product/caller/D1/D2及一般NULL/free/整图N4/预算组成同一完整设计，双审前不生成产品候选。
5. 原Task9A9B、111/94/r919、真实Store、P06、条件T15和生产云继续；P06七历史身份无材料不再索取。有限来源设计不代完整目标或实际产品验收。

仅复用同一只读代理；不新增模型/QA/诊断、正常库/服务/dist/ACL/settings/真实平台/Git写入、全局hook/GC/allocator或真实frames/locals操作，不重放旧单次授权和终态失败下载。封存后不向本批目录追加或改字节。
