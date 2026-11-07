# r1125：IMPORT_FROM版本化C路线与完整错误构造

r1125已选IMPORT_FROM版本化C端口：原LookupAttr/IsInitializing使用确实导出的API，未导出的name_from错误helper按原完整算法本地搬入；不采用弱setter或新Python fragment。PE/COFF静态表30目标中28可导入、2缺席，四项数据以__imp_导入分列。原最终错误/循环导入/实际v与K-name/逐alias STORE及标识符identity路线已具体化；IMPORT_NAME新增同T0 default根、全P0/generator/联合消费/N4/COMPAT仍未闭。B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

39完整输入pairs每侧4130724B，4AST/16顶层ImportFrom/25alias；20确切官方原文区间、10官方文本944821B，其中7新下载713751B。SDK两binary仅只读bytes/hash与静态表解析，不复制/加载/执行；SDK两头与tagged源码仅CRLF规范化后内容核同。父r1124完整44文件9321065B、159源码7956657B及保护56同18lstat缺席核同。

原error helper保IsSubclass、msg=NULL原TypeError、name/path/name_from写序及原Vectorcall/SetObject/释放；first指原算法最终逃逸错误，不以统一异常快照篡改原内部覆盖。T0三interned key固定顺序及partial逆序释放明列；public intern静态链支持取原_Py_ID同对象，非仅内容相同。default importer只在原固定T0捕获，禁止后补current pin。

本机export证据不是link/build/runtime或DLL实现等同tag源码的证明。direct C本身不增Pythonframe，错误栈/traceback入口对应新K的native ingress是显式delta。CPython full API/逐版本源码与许可notice/修改摘要/发行携带要求已列，>=3.11不收窄、其他版本分支未完成。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；control1MiB不是确认native总heap限额。

正常库/服务-dist/ACLsettings/平台/Git不操作；禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# r1125：IMPORT_FROM 版本化 C 路线与原错误构造

状态：选定一个具体静态子设计，不是完整P0、源码或执行批准。父r1124；产品仍r1106 SOURCE02完整159文件7956657B。B001/B002仍open2，完整设计/source/execution/Goal均False；本批无产品候选、compile/import/运行、native build、模型、QA或诊断。

## 1. 决定与新证据

选择把3.12.10原 `import_from` 算法搬入同一P0 C body的版本分支；直接链接本机确实导出的 `_PyObject_LookupAttr` 和 `_PyModuleSpec_IsInitializing`，并把未导出的完整ImportError构造helper作为本扩展的私有static实现。这里的“搬入”是拟议产品实现；当前只产生本文和静态证据，没有C候选文件或可执行模型。

否决两个具体捷径：不能直接链接原ceval静态 `import_from`；不能用 `PyErr_SetImportError` 或 `PyErr_SetImportErrorSubclass` 直接替代带 `from_name` 的原setter。后两者会把 `from_name` 传NULL，遗漏原实际导入名的 `name_from` keyword。也不采用“只做getattr，缺席就raise”的弱化版本。

与fragment比较，direct C路线自身不新增Python函数/frame、hidden M或CREATE，不为运输v再执行一次IMPORT_NAME；原内部操作仍可回调Python。fragment不是一般性不可能，而是本次不选：需要额外说明原v进入新operand stack、B/L/环境与frame/CREATE/exec audit差异，复杂度和现有P0方向不合。C路线仍是新增受T0授信的二进制实现，不声称透明或已经可运行。

## 2. 可链接表与证据强度

只读解析固定 `<PYTHON312_ROOT>/python312.dll` 的PE export directory及同SDK `libs/python312.lib` 的COFF first linker member。二进制仅记录完整bytes/hash，不复制、不加载、不调GetProcAddress、ctypes、编译器或原生代码。结果只证明这些确切SDK字节的静态符号面，不证明链接成功、二进制来源自证、运行语义或其他平台版本。

| 目标 | 本机静态结果 | 本路线处理 |
| --- | --- | --- |
| `_PyObject_LookupAttr`、`_PyModuleSpec_IsInitializing` | DLL具名非forwarded导出，.lib直接及`__imp_`符号均有 | CPython full API固定版本依赖，不是Limited/Stable ABI承诺 |
| `_PyErr_SetImportErrorWithNameFrom` | DLL与.lib均无 | 搬入errors.c完整内部算法，不能只写一个extern后假称可链接 |
| `_PyImport_IsDefaultImportFunc` | DLL与.lib均无 | 见§6原T0 default importer identity路线，不调用缺符号 |
| `_PyLong_AsInt`、`_PyObject_FastCall`及所用公开函数 | SDK导出表和.lib符号有 | 原IMPORT_NAME转换/调用优先保持同原API，不以近似公开调用替代后声称完全等价 |
| `_Py_NoneStruct`、`PyExc_ImportError`/TypeError/KeyError数据根 | DLL有，.lib为`__imp_`数据符号，没有同名直接函数stub | 由对应CPython SDK声明的数据导入路线消费，不能把“无直接stub”误报为不可链接 |

SDK-EXPORTS-03.json列30个具名目标：28项有DLL导出和所需.lib导入符号，其中四项为数据；两项缺席。头文件声明不代替PE证据；本机两个cpython头与tagged官方文本只在CRLF规范化后比较，原完整字节各自封存，不称二者原bytes相同。

## 3. actual操作数与原标识符

原 `import_from(tstate,v,name)` 不读取frame字段。本次C body持实际原IMPORT_NAME返回v，name取该次actual K对应co_names对象；不能按AST字符串重造name，也不从current module属性重读v。相同fromlist的多项IMPORT_FROM复用同一v，逐项结果按原序STORE，再到原POP对应的v释放。v/name是caller持根，helper不多取/释放它们，不把每项结果都提前求完再一并发布。

四目标当前直接顶层AST有16条ImportFrom、25个alias接收项，level仅0/1；其中包含future节点，不能由“是future编译语义”删掉其原runtime import。库存是语法输入，不是已验证K的opcode数或运行次数；inactive imports和后续函数体imports不混入这16条。

helper的`__name__`/`__spec__`属性名原本是`_Py_ID`静态对象。拟在固定T0准备期用原C API `PyUnicode_InternFromString`分别取得并强持，helper只消费已有根，不在每次调用补造名称或晚取别的frame。3.12.10官方链为：module初始化前 `_PyUnicode_InitStaticStrings` 注册两项 `_Py_ID`→公共InternFromString创建exact string→mortal intern→intern_common优先返回原global static cache对象。该条件下可得到原同identity对象，不仅是字符串内容相同。初次创建临时string仍会分配/失败，必须在T0的可再入准备/失败前缀列入，不藏进helper原错误路线；T0 roots冻结及最终安装前重核仍保持。`__import__`键走同一具名static identifier路线。

三个key的准备顺序固定为`__name__`→`__spec__`→`__import__`，每个实际成功返回立即移交本代state的独立owned槽；任一步失败只逆序detach/释放实际取得前缀，不把NULL或borrowed项当已取得。原default importer在固定T0原始捕获阶段先独立持根，三个key在同次初始化的准备阶段完成，所有字段就绪并最终重核前不发布/安装入口；不得为了补失败字段再次从current builtins采样。活动调用退出、state clear/free及alias退休仍与全N4联合，不能清掉活动lease尚需的key/default根。

这不是从外部current字符串建立代码来源，也不是读取真实frame/locals；解释器static-cache来源仍位于原固定T0有限边界内。未知版本没有此链的当前证据，不按名字相同推过COMPAT。

## 4. IMPORT_FROM 分支表：保原最终逃逸错误

下面是后继代码的逐分支实现义务。保持原 `PyUnicode_Check` 允许Unicode子类，不改成exact；原格式化`%R/%S`可调用外部repr/str，不能为了“安全”删掉。helper不加统一入口异常快照或finally强还原，因为原实现会主动替换某些内部错误。first指**按原算法最终逃逸的实际异常**。

| 顺序 | 原操作和分支 | 所有权/错误处理 |
| --- | --- | --- |
| F01 | `_PyObject_LookupAttr(v,actual_name,&x)`返回值不为0 | 直接return x；成功或错误均不再读`__name__`。保三态API，不以try-getattr/吞全部异常替换 |
| F02 | 缺席才 `PyObject_GetAttr(v,original_name_key)` | NULL去error；非Unicode先Py_CLEAR(pkgname)再去error，不额外INCREF或改检测 |
| F03 | `PyUnicode_FromFormat("%U.%U",pkgname,name)` | 分配失败只按原DECREF pkgname返回NULL；不覆盖该失败 |
| F04 | `PyImport_GetModule(fullmodname)`，随后DECREF fullmodname | x为NULL且无error才error；其余先DECREF pkgname，再原样return x，包括NULL有error |
| E01 | error入口先 `PyModule_GetFilenameObject(v)` | 与pkgname形成原别名/所有权关系，不在入口清掉原异常 |
| E02 | pkgname为NULL时创建`<unknown module name>`，否则直接alias pkgname | 创建失败只XDECREF pkgpath返回；alias分支不多INCREF，也不在结尾把pkgname与alias各DECREF一次 |
| E03 | pkgpath为NULL或非Unicode | 原PyErr_Clear，再按原unknown-location字符串format，再完整name_from setter；不能全程冻结先前内部错误 |
| E04 | known-location取`__spec__`，即使spec为NULL仍调用原IsInitializing | 保原helper的错误清除/boolean规则，先XDECREF spec，再按原选定format构造errmsg，调用完整setter |
| E05 | error分支末尾 | 按原顺序XDECREF errmsg→pkgname_or_unknown→pkgpath，返回NULL。不得加通用decref pkgname造成双释放 |

原 `_PyErr_Occurred(tstate)`/`_PyErr_Clear(tstate)` 可分别映射到public `PyErr_Occurred`/`PyErr_Clear`：官方薄wrapper取同一current thread state再调用同一内部实现。此论证有同解释器、同线程、持GIL且遵循正常C API thread-state调用约定的前提；不读真实frame、不暂停或替换解释器。错误构造本身仍原样使用当前线程状态；额外跨interpreter调度不在本helper引入。

## 5. 未导出错误helper的完整本地实现义务

保errors.c 1086–1145及1156–1159的原算法，改变的只是本方static函数名和内部薄wrapper调用；不是调用一个缺失的DLL符号。

1. 仍先`PyObject_IsSubclass(exception,PyExc_ImportError)`；<0透传，非子类按原TypeError字符串返回。当前路线传原ImportError也不据此删除这个步骤。msg为NULL仍按原TypeError路径处理，不能一律保之前的MemoryError而改变原算法。
2. name/path/from_name各自NULL归一为Py_None。新kwargs dict按原顺序写`name`、`path`、`name_from`，任一次SetItemString失败跳同一done。不能改成较少keyword的公开setter，也不先set异常后再补属性。
3. `PyObject_VectorcallDict(exception,&msg,1,kwargs)`，成功后用`PyErr_SetObject((PyObject*)Py_TYPE(error),error)`，随后原DECREF error。不提前给error类型/内容增加原算法没有的拒绝分支。
4. done统一原DECREF kwargs并返回NULL。每次失败时只有已实际取得的kwargs/error自有引用，不能清未取得的指针或用外层通用清理重复释放。

public `PyErr_SetObject`/`PyErr_SetString`同current-thread薄wrapper有独立官方区间；新增二进制函数边界和C栈与原解释器不同，不把“调用相同底层操作”夸成所有观察一致。native来源/状态门不在上述原error helper中随意插入；需要额外准入失败时必须在完整P0设计列清位置和原错误优先关系。

## 6. IMPORT_NAME 的固定default根与两分支

原 `_PyImport_InitDefaultImportFunc` 在解释器初始化时强持实际builtins `__import__`，原IsDefaultImportFunc仅identity比较。拟在r1112原固定T0的具名builtin捕获集合中明确增加该原default importer与static `__import__`键；必须是**同一个固定T0**捕获并冻结，不在第一次P0、post-helper、current B或override importer处重授信。T0条件明确要求该项确为解释器原default；不能仅因“当前builtins里有同名函数”推导成立。缺此基线，当前路线未获执行准入。

每条实际IMPORT_NAME先从其原module环境B实际getitem取得import_func；KeyError按原转换成`ImportError("__import__ not found")`，其他错误原传播。然后与独立T0 default root比较，不能从current B生成expected。default分支先按原DECREF临时import_func，再原`_PyLong_AsInt(actual_level)`、`PyImport_ImportModuleLevelObject(actual_name,G,L_or_None,actual_fromlist,ilevel)`。override分支保原五位置实参name/G/L_or_None/fromlist/level及一次 `_PyObject_FastCall`，返回后DECREF import_func；不因它不是default就一律拒绝、不追加第二次import。

G/L来自本次原wrapper/attempt的C-held实际根，B来自原module直接进入的当前环境时点；callee活动期间不能把其frame B冒认外层B。默认比较的T0新增原根、API/key准备、原getitem callback和最终native/source联合门仍须接完整P0/N4；本节不自称该来源已经实现。原static helper内部的interpreter默认槽不通过不存在的export、地址查找、手写偏移或运行时patch去读。

## 7. 引用、预算、执行与兼容差异

helper内部保§4/§5原自有引用顺序；P0的actual v根相当于原from operand跨同group存在，不能因为J长寿就把所有临时import返回值永久加入其持根表。新增独立expected/native/D/J根的实际寿命及可能延迟DECREF是另一成本，必须在N4逐项列清；不能用helper释放正确证明全J无环/无泄漏。

direct C port不新建Pythonframe本身，但其Python回调/错误返回的可见调用栈和traceback入口会对应新K的native ingress位置，不能声称仍停在原IMPORT_FROM行。原最终异常对象/字段/传播与这个源码位置delta分列，不通过读取真实frame、修改tb、profiling或关闭monitoring来掩盖差异。完整修复设计仍须接受并说明该执行边界。

私有API使该实现绑定CPython分支和SDK。当前仅3.12.10 Windows x64字节与tagged源码调查，未做native link/build/import/运行；不改pyproject的>=3.11声明，不将未知ABI拒载冒认已完成兼容。完整COMPAT须为3.11及其他受支持版本/平台逐分支定义原算法、可链接符号/宏/对象布局、构建配置与加载失败行为，或提出明确的产品支持边界修订；当前均未完成。

原17lease/1449slot/406尾/五S002不变；case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。control1MiB是累计控制数据实读上限，不是已确认native总heap上限。新T0三键/default根与helper持根的实际slot/分配/生命周期须单列，不能报零成本。

## 8. 实施文件、许可与后续验证

完整设计准入后，拟在native版本适配责任单元增加import_from端口、完整local ImportError helper和API条件表；bootstrap责任单元增加同T0 default importer与三个interned key；P0责任单元按实际K operands接16条原top import及逐alias STORE；不为本文先落一个可运行C壳或局部模型。具体产品路径/生成K与全四目标仍待完整冻结。

搬入CPython源码需保存v3.12.10 tag、完整hash、原copyright/license notice和逐函数修改说明。源码分发与wheel/二进制分发均须携带完整CPython LICENSE及第三方notice；构建配置应明确包含，不能仅链接外站LICENSE充数。当前pyproject未声明native build-system，本文不伪造已有构建。这里只下载封存原LICENSE，没有生成或发布native发行物，也没有法律合规验收结论。

完整源码双审及执行准入后，验证应覆盖：属性命中/缺席/原自有错误；循环导入fallback命中/缺失/有error；Unicode子类原检查；known/unknown location与spec NULL/false/error；完整name/path/name_from及原格式化错误；多alias中途失败保前序STORE；overridden importer五operand identity和一次调用；default分支；新T0失败/退休与所有权。当前不写可执行模型、不运行这些case，不以本批静态PE表或文档审核冒认它们通过。

本批完成的是IMPORT_FROM具体版本路线、缺符号替代的完整算法与名字identity依据；P05真实惰性generator body门、catalog maker seed/六工具来源、38处及foreign联合消费、r1122 CORE/三D0D1D2、完整metadata/N4/COMPAT和全Goal仍有工程。完整设计双审前无产品候选，源码准入前无compile/import/执行/native build/模型/旧绿F001/QA/诊断。

正常库/服务-dist/ACLsettings/平台/Git不操作。禁profiling/真实frames-locals/断点停点/定点计时/新异常链诊断/runtime journal；不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问；P06历史0/7无材料不再索取。canonical apply0/历史11、源码缺口关闭0、产品pass0，Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal未完，非只剩验收。

# 下一批：catalog六工具来源与真实generator消费

父r1125，产品仍r1106 SOURCE02完整159。IMPORT_FROM已选具体C子路线，尚无产品源码或完整批准。

1. 同版接r1125原IMPORT_NAME/IMPORT_FROM版本分支、同固定T0 default importer/三key及原K operand/每次STORE/错误与引用路线；补具体未来K、native源码与traceback入口delta，不能把本机静态exports当执行准入。
2. 收敛catalog maker seed私有ABI、原captures/missing-object时点、六nested def独立code/closure/kwdefaults及actual return；D只证maker来源，不从返回row重pin。原maker STORE后另行D发布及partial保持。
3. 优先完成P05真实generator内的actual tool call门：tuple target先取、gen-function CREATE、左GET_ITER、actual generator call、lazy body和每次G观察分列，不用eager C loop或返回后检查代替。与38处14/5/19、foreign和r1122 CORE pair/3locals同版接。
4. 补全metadata认可点/三D0D1D2、native state/type/A/D/J/L共同N4、actual slot/字节/错误释放及>=3.11/构建/许可分发。完整设计双审前无产品候选；源码准入前无compile/import/执行/native build/模型/旧绿F001/QA/诊断。

原17lease/1449slot/406尾/五S002及全部原限额、正常库/服务-dist/ACLsettings/平台/Git保护保持。r920/r964已耗不重放，r967不执行不重问，P06历史0/7不再索取；完整Goal仍active，非只剩验收。
