# 固定认证目录与晚期元数据原对象发布实施方案

**目标：**统一修复 r1074 的四类源码缺口，在执行下一条认证依赖或解引用晚期元数据之前，确认原实现、实际依赖和原发布对象。原资源获取、计费、清理、业务发布和正常库暂不接入。

**父版本：**r1073 SOURCE01 的完整158文件候选。依据为 r1074 SOURCE-REFERENCE02、DEFINITION-CATALOG02、Root02 与同一独立代理静态报告。31定义/602句法调用只作为此次调查的索引，不是完整运行图或必须每次遍历的清单。

**技术：**Python 3.12.10，stdlib 原始对象访问、不可变目录、分阶段发布记录、未安装完整候选。r1073封存方案的3.13笔误已由r1074记录纠正，原证据不重写。

**修订02：**保留DESIGN01完整初稿与三项阻塞；本版补充首次信任转交和分阶段无环表，取消可调用publisher，显式纳入enrollment。原槽只在原producer固定代码中写入，不能通过传入value的发布能力补造来源。

## 一、设计边界与交付口径

本方案修订一个固定认证子图，交付源码、调用边目录、阶段表和对应的定向检查。材料审查阻塞0、源码缺口关闭、定向检查通过、原生链通过、产品安装是不同结论。此次不以一条wrapper、旧30项或全部静态compile代替整组修复。

信任起点沿用现有原始入口及导入时捕获的内建primitive。目录建立发生在可信的原定义/原构造处；后续检查针对已登记实现、模块绑定、builtin shadow、闭包内容和所列原元数据。纯Python自检不能证明任意并发外部代码没有同时改写所有独立证据或解释器本身。每个新增入口由原调用方用捕获primitive内联校验后调用，不用“helper校验自身”消除这个信任起点。实施前须列清上游实际提供的入口锚，缺锚的边列为阻塞，不继续递归声称已经自证。

不读取frames/locals、不profiling、不停点、不定点计时、不新增异常链诊断。r920/r964已耗，不因迟到批准重放；r967待原问题，不执行、不重问；P06历史0/7不再索取材料。

## 二、文件与责任

| 文件 | 具体修改 |
| --- | --- |
| 新 `reno/personal_authority_guard_catalog.py` | 无业务模块导入的metadata工厂；建立原行/固定槽存储和按边checker。没有可调用的publisher、bind-root、register-value接口。只保元数据与原实现，不能保业务budget、Context、异常或资源。 |
| `reno/personal_authority_guard_control.py` | 原helper定义处登记；保护函数/类pin、错误构造、class-auth与dictionary入口；在实际 `_BOUND` 和三静态root生成处发布原对象。 |
| `reno/personal_authority_guard_scope.py` | scope/origin两个认证入口与初始化阶段接线；`_PINS`、`_ORIGIN_PINS`的原构造和STORE effect分列；保两侧互补bootstrap。 |
| `reno/personal_authority_guard_registration.py` | 原scope调用前门、marker observer与stack helper边；合法None/deferred阶段；`_MARKER_BINDING_COMPLETE`与`_PINS`原发布。 |
| `reno/personal_authority_guard_resources.py` | 保r1073两个direct edge的顺序和调用次数；接统一目录与自身余体依赖门；`_PINS`实际发布。原七余体检查保留。 |
| `reno/personal_authority_enrollment.py` | 原alias捕获处同时保scope原函数行；九个origin认证调用逐次前门；原_Budget classdef→137 bind和定义完成→1097 origin初始化的最小bootstrap门。原入参、调用次数和业务体保留。 |
| 候选检查脚本与来源文件 | 新目录 `data/t16-h01-authentication-catalog-owner-r1075/` 保存设计/完整输入/源码差异；实际QA另建独立批次与新claim，不消费旧claim。 |

预期159完整候选（五改一增，153不变）；最终以精确文件集合核对，不能用“159完整”代表159模块均import。初稿文件表只有四个修改项，计数与清单不符；本版为覆盖enrollment的实际九次入口而明确加入第五项。lifecycle、custody、pending只作固定来源/上游依据，若实现需要改它们，先解释具体未覆盖的边并修订本方案，不隐式扩展。

## 三、统一目录的具体结构

### 3.1 创建与注册

新工厂仅依赖导入时捕获的stdlib/内建对象，不import五业务模块、不调用目标函数试探ready。每模块在循环业务import之前建立自己的原owner存储，故部分模块已可交出稳定原owner身份而其中晚槽尚未就绪。工厂与返回的入口由导入方当场捕获；返回对象须是固定数量的exact tuple/exact FunctionType，记录实际return，不能用后来全局查得的同名函数替代。工厂本身的首次执行依赖可信的原源码导入基线，不声称通过调用该工厂自证首次执行安全。owner新增直接存储不含budget实例、Context、异常/tb或资源；原function/globals/closure/root仍可能经原业务registry间接可达对象，不能据此宣称零间接可达或完整产品GC。

每个原 `def`/原函数工厂实际返回处登记函数行；类在原class构造结果处登记。行包括函数identity、原 `__globals__`、`__builtins__`、code、defaults、kwdefaults原字典和完整原条目、closure原tuple与逐cell原内容。隐式 `__class__` 以实际runtime cell为准，AST free-symbol不冒认完整closure布局。新owner内部函数和调用方持有的checker同样要有原行与外部调用锚。原行存储和晚槽的原容器身份稳定，合法阶段只更新各固定槽内容；不能把合法晚槽更新当作任意closure替换豁免。

模块行保存原模块、原字典及固定alias的原值。builtin行分开记录：模块globals是否原本缺少该名字；函数实际 `__builtins__` 原映射及需要的原builtin值。仅核模块 `__builtins__` 绑定不能代替函数实际映射。按函数原 `LOAD_GLOBAL`/helper结构列明需要的名字；源码AST只用于生成可复核目录，运行时不分析AST。

### 3.2 检查次序

所有可能查key的exact dict先用捕获primitive遍历并确认key为exact str，随后才用捕获的dict.get/items查值；不让碰撞非str/str子类的hash/eq先执行。先核type和原identity，再读属性、遍历、索引或解包。kwdefaults的None、空dict、None/False值和缺失分别处理。

每个新checker调用点首先以内联primitive核原function/code/defaults/kwdefaults/closure及所需原cell身份；这一小段不调用可写global helper。checker主体只用捕获primitive和固定原目录，不动态调用五模块的 `_GET`、`_fail` 或未经校验的checker。源定义位置生成原行的工具本身也先过同样内联门，实际返回行在原位置立即保留；不能在第一次业务认证时运行row工具为当前目标造expected。

### 3.4 原入口与可信转交表

下表的原行只从原定义/原工厂返回事件产生；跨模块链接取得的是源owner已记录的原行，而非把当前同名global重新pin成expected。固定owner身份在可信原import返回/部分模块import处就地捕获；尚未出现的函数行保持未发布，来源producer完成后填对应固定槽。第一次业务调用不再有创建或覆盖expected的权限。

| 入口/转交 | 原caller与位置 | 原行建立/转交 | 既有锚与此次补充 |
| --- | --- | --- | --- |
| 新factory首次调用 | 五模块原顶层、循环业务import前 | 可信原导入；实际factory return当场捕获，caller闭包握原checker/owner/原行 | 此处是明确的原源码/primitive信任起点，不用自调用证明。后续checker调用都先以内联门检查。 |
| class/function helper与failure | control原32–148的def/class/工厂return | 定义事件生成原行；两个class gate用原_make_class_authentication实际return，不后来getattr认领 | 初代helper门只要本身primitive/已定义failure行，不要求未来_BOUND或自身将生成的root。 |
| resources认证入口 | lifecycle原checker736–758；以及resources内既存调用点 | 原_make_authenticate实际return登记；lifecycle仍捕获其原接口/function与直接cell身份 | lifecycle不代globals；resources原body内按边门补足。内部每个进入本认证子图的动态call都列清单并加前门。 |
| scope认证入口 | resources原两个direct edge之一；registration原74/127；scope内既存调用点 | scope原def事件登记；源owner身份在原import处捕获，caller闭包持原行引用 | r1073只代函数metadata；新增门补scope的control/primitive/root依赖。不能把custody/pending自身check称为此caller/global的认证。 |
| registration认证/初始化 | resources原第二direct edge与4043；scope原3204/3213 | registration原def登记，原import握owner；scope初始化回边用当场已sealed的scope行 | 原registration内部scope调用仍保留并再次核。初始化时不要求registration自己的_PINS已sealed。 |
| enrollment→origin认证 | 原41 alias捕获；514/531/601/691/709/781/894/914/987九次调用 | alias捕获时同时握scope原owner/已建立的_origin_authenticate原行；九处调用均核actual alias与row后调用 | 原enrollment caller body/原导入primitive为本次边界；九次global目标与其依赖由新前门保护，不宣称custody已代核这些globals。 |
| enrollment→budget bind/origin初始化 | 原_Budget classdef→137；原_construct_group_origin_owned定义完成→1097 | 原classdef及caller def实际结果就地建行；137/1097调用前握原callee行及最小阶段 | 保原cls参数/原检查。不以producer函数metadata推断调用者或cls来源已认证；额外原行仅保证该原调用site使用原classdef/原callee。 |

外部原caller body、可信首次源码导入及捕获primitive是明确边界；若原caller本身被整体替换或所有独立owner证据同时被改写，此子图不提供完整解释器防篡改保证。边界以内不信第一次lookup当前globals。候选静态清单必须收齐五模块中进入所列认证函数的调用点，并把上表外的新incoming引用单列；不能遗漏后又宣称全部入口已覆盖。

### 3.3 按边目录，而非每次全图扫描

导入/原发布时建立不可变行和每条边所需行的不可变视图。每次fresh检查：下一callee实现、它实际读取的可变globals/builtin映射、必要原root和阶段。某边不扫描158/159文件，不重算源码hash，不顺带认证全部31定义。

EDGE-CATALOG同时列每次需扫的完整module/builtin字典、每个helper循环的原条目乘数及本边fresh行集合。按原发布实际条目数推导静态工作量上界，不把未实测的成本写成已超时；不靠成功缓存、缩小fixture或减少原检查控制成本。

原 `_check_class` 循环中每次 `_check_function` 调用前，只核这个helper及其primitive/失败路径；待检查的业务function仍由原 `_check_function` 原逻辑核对，不递归运行该业务function的所有依赖。不可变目录结构可以复用，dict/class dict/kwdict/cell等可变事实不能用上次成功或generation计数跳过。保原类pin中StorageProfile原 `__slotnames__` 唯一例外，不能扩大到任意类。

## 四、必须覆盖的实际边

| 调用/解引用位置 | 前置校验与保留行为 |
| --- | --- |
| resources→scope、resources→registration | 原两个callee各一次，scope返回后再核registration。继续检查registration自己的scope alias，不能以上一次scope调用代核。 |
| registration `_authenticate`/`_marker_bootstrap_facts`→scope `_authenticate` | 原scope模块/字典/alias、实际callee及其primitive/需要的scope root；不先执行未知scope再后验拒绝。两处原调用都保留。 |
| scope/control/registration/resources→`_check_class`/`_class_pin`/`_check_function`/`_function_pin` | 原helper和实际control alias；helper内部下一层调用也逐次fresh。不能只pin两个class gate。 |
| registration→marker pin tool/observer、stack helper | 原bootstrap observer及其现有生命周期checker接点；保原observer次数、None与完整facts分支。未知target计数须为0。 |
| `_origin_authenticate`→scope与control class-auth | 保原调用顺序及合法 `_ORIGIN_PINS` 对_BOUND的已有局部检查；class-auth仍建立自身原root门。 |
| control `_dictionary`→class-auth→`_BOUND`索引 | `_BOUND`原发布sealed和当前binding先核，再调用原class-auth/取元素/descriptor；不从当前_BOUND创建expected。 |
| 所列固定子图的原 `_fail` 路径 | 原 `_fail`及下一failure callee、GuardControlError class/MRO/构造函数、`super`和隐式__class__ cell先核。原合法业务错误仍用原class、code及抛出次数。enrollment其余业务失败路径不在本子图闭包声明内。 |
| 晚root的getitem/iter/unpack与元数据中的字典读取 | owner/阶段/原root identity先核。原tuple结构在实际发布处建立，不能对foreign root先解包。嵌套可变对象另有fresh行，root identity不代其内容检查。 |

上游lifecycle/custody/pending的“只校验自身目录/资源接口”不等于代核四模块内部globals。需要它们提供的原入口锚逐一引用原检查位置，未实际覆盖的必须显式写入修订范围或阻塞。该表在候选生成时要落成机器可核的EDGE-CATALOG：调用方、原位置、实际callee/implicit protocol、原始登记位置、必要root/phase、上游锚、precall实现位置、对应检查ID；每条边都可追溯。

## 五、晚期root发布协议

### 5.1 固定槽与原producer

| 原槽 | 原生成处 | 允许阶段 |
| --- | --- | --- |
| control `_DEPENDENCIES`、`_FUNCTIONS`、`_TOKEN_CLASS` | 原模块顶层RHS | helper定义完成后依原顺序发布，不能等待晚budget绑定才登记。 |
| control `_BOUND` | `_bind_budget_class`的原 `_class_pin(cls)`实际返回 | 初始UNPUBLISHED，原budget检查后一次发布。第二次bind保原拒绝。 |
| scope `_PINS` | `_initialize_owned`原类/function/class-gates RHS | control helpers与scope定义齐备即可；不要求marker/预算类先ready。 |
| registration `_MARKER_BINDING_COMPLETE`、`_PINS` | `_initialize_owned`原observer实际返回与原RHS | observer None时合法保持未发布；完整facts后依原顺序两槽发布。 |
| scope `_ORIGIN_CALLER`、`_ORIGIN_REMEMBER`、`_ORIGIN_PINS` | `_initialize_origin_owned`原读取/构造 | 原budget bound与enrollment定义完成后；不把前两项的当前值首次复制成原来源。 |
| resources `_PINS` | `_initialize_owned`原dedup/class/function/context/bindings RHS | 原directory trace与marker bind及registration初始化完成后，保持原顺序。 |

`_BOUND`的局部已有检查不省略；初始None是独立阶段事实，不是最终pin。**取消初稿的专属publisher callable。** 没有module/name/value、bind-root或传入任意result的登记API。原producer在原定义时闭包捕获本模块原槽，写槽代码内联在原producer中；读端闭包握同一原槽。它证明原固定RHS的实际结果/effect，不证明谁调用producer，也不额外证明_bind_budget_class入参cls的来源。原cls约束照原保留，不能用此元数据owner给业务写权。

### 5.2 一次发布的时序

1. 原producer先通过其适用的bootstrap目录/前置门；原RHS求值和原callee调用按原顺序、原次数执行。将**该次实际结果**立即保留在local，不能重新读global来登记。
2. 原producer固定代码在原STORE前内联记录PENDING：固定槽、原producer、该次RHS local实际result、原阶段。原result与后续global effect分开；这里不调用任何接受result的publisher。public参数不能直接作为“实际result”填槽，必须经过该原固定RHS与原callee约束。
3. 执行原global STORE（从同一local写入），然后用捕获原模块字典/raw primitive读取**实际effect**，身份必须是同一result。STORE发生与独立raw读取的effect分别记录，不能用一个成功返回值代替两者。
4. effect核同后SEALED；metadata读端只认对应原record。失败保PENDING/FAILED及固定失败码，不补领、不重建expected、不再次执行该段发布。owner不存异常对象/traceback；原首异常由原业务路径保真传播，不能为了记录而增加强引用。未产生业务资源，此处不引入业务清理或权限。
5. 已发布原producer再入沿原认证路径核现存root，不重复发布；合法None/deferred只走原分支，不额外bootstrap或调用observer。

metadata状态与发布读用同一私有锁协议；只在处理metadata时持锁，不跨原业务/helper调用、不引入新ContextVar。锁/primitive在工厂构造时捕获，原producer内联调用捕获的原锁方法，读端用同一原锁；持锁区不执行可写callback。锁只约束遵守协议的写方；每次读取后调用仍要fresh核实际target，不能声称锁消除了任意外部Python赋值的竞态。

## 六、循环导入与分阶段准入

必须保留control-first、scope-first两种实际顺序，以及registration早于control helpers的阶段。

1. 新metadata工厂不依赖业务模块，可先创建模块owner和固定未发布槽。只登记已完成定义的原对象，不读取尚未定义的control helper。
2. registration早期仍使用原lifecycle pin tool，不能为统一目录改成提前调用control._function_pin。此时scope原函数行可以登记，但未发布scope root不允许业务认证；原bootstrap观察允许未完成阶段。
3. control helpers、scope定义齐备后，保持control `_bootstrap_scope_owned`与scope尾部互补启动。原未ready返回False、缺helper跳过、later一次补齐都保持；不用一个总READY替代各阶段。
4. 原scope PINS发布后，registration可因marker observer None合法返回；不认该None为最终PINS，也不判整个包损坏。
5. enrollment原budget类bind、origin初始化、resources import的实际顺序不变。resources原marker bind后registration原initialize完成，才发布resources PINS。新的目录连接只传递原定义/原producer已经建立的行，不以首次业务lookup接收新的可信目标。

### 6.1 明确的阶段准入表

| 阶段 | 实际原事件与最小前提 | 新门允许/要求 | 不能依赖 |
| --- | --- | --- | --- |
| P0 | pure factory与五模块各原owner在循环import前建立 | captured primitive、固定ValueError、原factory实际return行 | 任何业务_fail、control/scope定义齐备或晚root |
| P1 | registration原63–70的lifecycle tool/observer已存在 | 两原callee原行与lifecycle原接口；control缺helper仍合法 | control._function_pin、scope_PINS、marker已绑定 |
| P2 | control32–124的failure/helper原定义/工厂返回 | 对应primitive/failure行；按136→146→148原序发布三static roots | _BOUND、未来_TOKEN_CLASS sealed；helper自身门不递归消费其待产物 |
| P3 | control_GUARD_CORE_READY与scope定义齐备；control752或scope3218互补启动 | 原bootstrap/initialize行与已ready helper行；按3199–3211发布scope_PINS | registration_PINS、marker/budget/origin/resources ready |
| P4 | scope3213→registration_initialize→marker_bootstrap→scope_auth | scope_PINS刚已sealed；observer原None时deferred；若原marker已ready则当场完成registration两槽 | registration自己的_PINS已sealed；不再次要求scope初始化；不强迫等到P7 |
| P5 | enrollment原_Budget classdef与137 bind | 原binder/helper行、原cls约束，发布_BOUND | origin_PINS或resources已存在 |
| P6 | enrollment原定义完成与1097 origin初始化 | scope_PINS、_BOUND已sealed；原enrollment函数/remember原行，依原序三槽发布 | resources_PINS、marker绑定（此时尚未import resources） |
| P7 | enrollment1099 import resources；resources4035初始化 | 原trace bind→marker bind→registration两槽完整→resources_PINS；原重复初始化先核已sealed行 | resources自身待生成_PINS；不得用业务认证门替代初代门 |
| P8 | 正常业务认证/合法再入 | 该边所需root sealed且当前binding/effect仍原同；每次fresh | 不能因上次成功省略检查，不能再次发布或补造行 |

control-first顺序：C先建owner及namespace/deadline/lifecycle，再import部分S；S建owner→原定义→R原定义/P1→S_READY，control bootstrap尚无则跳过；C恢复→P2→CORE_READY→P3→P4。scope-first顺序：S先建owner再import C；C建owner→P2，见S未ready而原bootstrap返回False；S恢复→R/P1→S_READY，现有C bootstrap执行P3→P4。常规初代marker未ready时P4 deferred，两序随后均由原enrollment P5→P6→P7补齐；原marker早已ready的合法情形允许P4当场完成。阶段编号表达依赖，不能强加原代码没有的总时序。P4回scope认证只读P3已sealed root，不回P3初始化、不要求P4自己已完成。

所有跨模块原行在源定义/实际return时写入原owner固定槽，接收方原import已握该owner身份；因此P2晚于P1不会让P1提前lookup不存在的helper，也不会从第一次业务global值认领行。候选须把这张阶段表细化为机器可核PHASE-CATALOG，但无环依赖已在设计阶段明确；任何违背这张表的新增依赖须先修订双审。

## 七、拒绝与异常保真

目录损坏时直接抛导入时捕获的原ValueError，固定文本 `GUARD_CONTROL_AUTHENTICATION_CATALOG_CHANGED`，不调用可能已损坏的业务_fail或GuardControlError。这与目录有效时的原业务拒绝分列：后者保持原错误class/code、对象、调用次数和传播；不包装原first、不改traceback、不捕获后重建异常。

前一个原callee合法返回后再检查下一边。若前callee直接抛异常，原异常沿原路径透传，不额外调用第二callee或认证它来覆盖first。不插入新的时钟、计费、I/O、资源操作或用户callback。原profile-cache例外、旧目录检查和资源余体仍保留。

## 八、执行任务与可审查产物

1. **冻结与设计双审。** 保存本方案、r1074完整来源/报告/清单、八份原源、r1073完整候选manifest、原保护清单。Root与同一只读代理审设计，重点看3.4入口信任转交、6.1无环bootstrap、原producer内联实际发布和checker自身前门。DESIGN01三阻塞及Root清单错误保留，02逐项对应关闭；审查未闭项不能进入候选执行。
2. **形成目录与候选。** 按上表生成159完整文件、parent/candidate/inverse/full diff，EDGE-CATALOG与PHASE-CATALOG。静态核所有Python语法、原调用序列/次数与原错误分支；涉及工厂重构的定义以归一化AST核原余体，不能只数函数名。两份源码审查后才写实际runner。
3. **检查与准入复核。** 每个ID明确完整包/纯模型、实际覆盖边、unknown callback计数和finally恢复。脚本与完整prepare输入双审后，以新claim执行一次。失败原记录保留，只对新修订的失败ID定向复检，不重复旧绿。
4. **实际记录双审与交付。** 核源码hash、case/stream/exit/wait及callback计数，发布CURRENT/NEXT/DELIVERY/FINAL与主方案进度；只按观察到的范围报告。未经完整包检查的门只标模型/静态，不把该子图交付写成产品通过。

## 九、定向检查矩阵

| 组 | 必须观察到的行为 |
| --- | --- |
| 正常导入/阶段 | 两种顺序；registration原None/deferred；budget绑定与origin/resources完整ready；合法再入不重发原root、不增加原observer调用。完整包正常认证通过。 |
| 实际callee | 四类缺口每个实际边：alias替换、模块子类/attribute trap、同函数code/default/kwdefault/cell变更；包含首callee返回后改下一callee。armed未知callee/attribute均0。 |
| builtin与primitive | `_GET`、`_CLASS_GET`、globals shadow、函数实际builtin映射原值变动；exact-str collision与非str/str子类key。armed未知hash/eq/getter均0。 |
| failure构造 | `_fail`绑定/code、GuardControlError.__init__、super、隐式__class__变动先拒绝；目录有效时原业务错误class/code/first对象保持。 |
| 原root/协议 | 五类晚root及三control静态root被foreign getitem/iter替换：unknown协议0；原root identity恢复后认证正常。嵌套可变kwdict/cell/模块binding分别fresh。 |
| 发布模型 | 实际RHS result与STORE effect不等、PENDING后失败、错误重入、重复发布、从public参数或当前root代填槽均拒绝；原None/deferred无额外重试。不声称识别任意caller身份。模型不能代完整producer运行。 |
| 线程与释放 | 用有限真实线程验证metadata发布/读取协议和无死锁；明确实际线程数。owner不留业务budget/Context/异常/资源强引用；GC仅核相应具名对象，不冒称完整产品GC。 |
| 保真/成本 | 原scope/registration/helper/observer次数及顺序；class/function pin原内容；profile__slotnames__原例外；无新clock/I/O/计费。静态按边核扫描集合，不能以缩小fixture或取消fresh解决成本。 |

最终先从EDGE-CATALOG推导具名ID，覆盖缺口再决定总数；不预报未经执行的全绿。case30、parent360、matrix1680、cleanup10、control1MiB、并发1、最多2pytest children及native20–25–10原限额全部保持。新定向metadata检查不运行F001、不增native/SQL/Store诊断；若后续实际准入超限，保红与事实，不擅自放宽或定点探时。

## 十、完成标准与回退

本修订完成须同时满足：固定边/阶段表无未标注的动态helper；原producer来源与实际STORE effect独立绑定；先核后调用/解引用；两种导入顺序及合法deferred保持；新增入口自身前门可追溯；设计、源码、脚本/准备与实际记录双审；定向检查范围明确。即使满足，也仅关闭所列固定认证子图缺口。

候选保存在data目录，canonical/c973不安装；回退只切换后继候选来源，封存输入、旧红和旧绿不覆盖，不执行Git或删除。当前正常库/service-dist/ACLsettings/真实平台保持，canonical/c973本段apply0、历史11，产品pass0。

仍需后续开发：Task9A真实多root/原first与tb/清理全矩阵，Task9B完整物理费用债务/guard消费者/两阶段terminal/容量durable恢复，原111/94/r919与真实Store写权、T16其余项及完整主方案。F001仍为历史20.016秒超时、双空输出、阶段未知，本方案不声称找到或修复其根因。完整Goal active未完成，非只剩验收。
