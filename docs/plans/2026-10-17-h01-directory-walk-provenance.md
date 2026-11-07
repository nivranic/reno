# Directory 完整遍历与后续验证来源接线实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将原严格 Directory 的构造、完整祖先遍历、目标 open、进入末验证及后续直接/去重验证，连接到同一实际 resource owner 的独立调用记录；不把这些记录升级为完整物理认证或写权。

**Architecture:** 沿用 r1002 source05 八份完整未安装候选，并纳入 c973 原 storage_lifecycle.py，形成九文件组合及原 resource attempt/owner/root/scope 图。在 resources 原独立 raw records 内新增 Directory walk、verification invocation、Path image 和实际 call 记录；sync_media 与 lifecycle 固定 producer/caller 接线，scope 提供不消费新 attempt 的 live 检查。原 cleanup 继续以已登记 journal/slot 为来源，不能依赖新增业务验证成功。

**Tech Stack:** 项目 Python 3.12.10、WindowsPath、原 os.stat/Win32 Directory owner、既有 scope/registration/ContextVar；仅源内固定调用，不引入 profiling、frames/locals、body 计时标记或替代 budget。

---

状态：r1003 修订02设计候选，01及其三项阻断保留。不是实现、运行准入、测试覆盖或产品成功。主方案仍为 `docs/plans/2026-09-30-product-evolution.md`，r919 全部原义务保持。普通开发和同一只读代理复审已获授权；不新增代理，不 commit、不建立新 worktree、不安装半组合。

## 1. 已确认事实和本段边界

当前八份 parent 来自 `data/t16-h01-directory-lock-path-r1002/candidate-full-05/reno/`。r1002 完成两次 Path 转换/字段写和 stream join/native 参数来源；完整 Path/physical 旗标均为 False。69 唯一 ID 最新全部 pass 来自两批 49/69 与 20/20，89 次尝试、旧 20 fixture fail 保留，本段不重跑或改变这些记录。

`sync_media._Directory.__enter__` 的严格 Windows、create=False、parent=None、base==path 正常路径，在字段不漂移时：由 anchor/parts 形成 N 个 ancestor；第一遍每项 native_path/stat/safe_stat，只有目标项 open 一个受管 HANDLE；末尾 verify 再 stat 全部 N 项；然后 activated 和返回。2N stat/1 open 是条件化的源码推导，尚无本段实际计数。类注释“Hold each validated ancestor”不代表为每个 ancestor 开 HANDLE。

begin 和 `_close_failure` 门位于 enter 的 try 外。begin 自己在 journal/clock 失败时 capture/retired，但 begin 的前置 owner/phase/create/parent/base 拒绝没有因此进入原 try。try 内失败才调用原 exit，再交外层已登记 callback。不得把所有新失败统一包进一个大 try 后改写这些行为。

后续 `owned.LockStreamDeadlineLease._verify_directory` 直接调用原 `safe._Directory.verify`，group 还会通过 `_directory_verification_plan/_verify_directory_plan` 去重状态查询。原 resources.check_row 只允许 pending 未完成 attempt；不能用于全部后续验证。原 scope._check_budget_owned 还会拒绝 pending read grant并旋转 Context；同样不能不加区分地插到每次 live verify。

原 journal native identity 是 `(volume,index_high,index_low)`；外层和 verify 使用 stat `(st_dev,st_ino)`。本段只接齐两组实际来源，不添加未经证明的等价桥接，不改变原安全谓词，不据此写入 physical authenticated。

## 2. 方案取舍

采用在原 producer 内接固定私有调用的方法。原 legacy/POSIX/create/parent 分支继续走原表达式；strict 分支调用 resources 固定 helper，helper实际执行已捕获的原 callee并返回原结果。两支的先后顺序和原 clock、业务 native 次数、异常映射、唯一 cleanup 保持；新增验证有成本，不声称耗时或所有校验次数不变。

不复制一个独立 strict enter driver：复制会产生两套路径遍历与异常边界，后续难以核同。不采用通用 callback/monkeypatch/profiling observer：它无法提供固定来源，也会越过当前诊断边界。记录是产品内部的固定数据传递，不提供任意 kind/callee/expected/callback 的公开入口。

本段的“完整”指列出的 Directory 调用路径与实际返回/效果记录闭合。不是任意同进程代码篡改的隔离边界，也不声称完整 stdlib/C/native/Context 区间已被认证。所有现有完整认证、marker、计费、发布和写权旗标保持 False。

## 3. 原图、原始记录和损坏规则

在原 directory allocation 登记的 PathState 上强持有 `_DirectoryWalk`，并建立独立 owner→walk、attempt→walk 反向锚；与原 path_directories/path_attempts/owners/attempts、root、scope、journal、ordinal互核。walk 在第一次业务调用前登记，不在 enter 返回后从当前字段重建。

新增 `_DirectoryWalk`、`_DirectoryVerification`、`_DirectoryCall`、`_PathImage` 的字段由原 `_make_path_records` 的独立 raw dictionary 保存，C slot仅作镜像；新增类、descriptor、factory及全部局部函数纳入现有 pins。字段需包含原对象/输入、phase、完整有序 cells、first/tb、return/result、effect、check_failure及未确认债务；不能只留 mutable presentation dict。

恢复优先沿有独立原始支持的 owner/attempt/raw record 查找。一个锚删改不能降级 legacy、误认已完成或污染 foreign scope；无共识则拒绝并保实际根。后续strict lock登记时，原walk还应独立保存原lock owner及反向锚，供lifecycle在分支选择前从原lock身份恢复原Directory，不能只从可能被换掉的lock.directory推归属。外部参数、同值 copy、已知 strict 的 None、错误 owner/attempt/journal 均不能创建新 walk或重试已消费调用。原 row/state被暂时改后再还原，也不能清掉已记录 first。

这不是“任意多锚一致伪造可恢复”的承诺。新增业务表、Path image或展示列表损坏不应改变 cleanup 的原 slot、owner、history。发生记录分配失败时，原 journal已有的真实资源仍由原退出责任关闭；不能因观察 cell未创建而漏关。

## 4. Path 实例状态的来源和合法变化

已核项目解释器源码 `<PYTHON312_ROOT>/Lib/pathlib.py`。PurePath 有九个 slots：`_raw_paths`、`_drv`、`_root`、`_tail_cached`、`_str`、`_str_normcase_cached`、`_parts_normcase_cached`、`_lines_cached`、`_hash`。前三个解析 cache由 _load_parts惰性写入，str和相等判断也可能写 cache。`_raw_paths`及 `_tail_cached`是可变 list；exact WindowsPath/class MRO pin不能代替实例内容验证。

1. **原输入与新返回分开。** 原 root Path在 `_origin_path_for_root_owned` 返回时只有已有 origin 身份历史，没有九slot的出生记录。将其首个原始状态保存为 `selected_input_observation`，明确 birth_proven=False。不能回头把这个快照标成 StorageProfile/_roots 内部 Path 创建历史。若它在本段记录前已损坏，本段没有历史证据恢复真值，完整来源仍不成立。
2. **在实际返回点保存新对象。** 两次 Directory Path factory、每个 ancestor factory/join、stream join的原实际返回先强根保存，再用固定原 slot descriptor读取其 raw image。image保每slot的 absent/present、原值身份、exact类型，以及列表原对象/长度/逐项原字符串身份。不能只保repr、路径文本、hash或再调用Path重算的对象。
3. **不提前预热。** image读取不得调用 Path的 str/parts/anchor/hash/fspath/eq。按原 callee的真实顺序分别保存 before/after；anchor/parts/index/原base比较/native_path可合法触发对应 cache。原 constructor尾部 `base != path` 和 begin内比较须有自己的操作记录，不能在后验一律放行“新增cache”。
4. **冻结基础材料，限定cache变化。** `_raw_paths`从首次返回后不得替换或原地变化。已存在cache与其list不得静默重写。允许缺失cache在实际固定操作中生成，但只接受该操作在3.12.10实现允许的字段集合、exact类型和从已保存基础字符串得到的规范值；无关 `_hash/_lines_cached/_parts_normcase_cached` 不因 anchor/native 等操作获准变化。独立词法 expected只核结果一致性，不替代实际调用、return或出生来源。
5. **失败也是实际状态。** Path/callee抛出后仍保存可安全读取的 raw after image；已发生的cache效果不回滚，原错误优先。不能用当前 after image补签 missing before或将同值新 list当原list。若固定代码在理论上有合法部分写入后失败，记录unknown/partial并停止业务，不把它误报成完整认证。

本段可证的是从相应记录点开始的实际对象与变化；StorageProfile创建到原root选择之间的来源仍须后继补齐。由此不得提升 path private state完整旗标。

## 5. constructor与进入阶段：实际调用和效果

在原 constructor严格分支中，把 parent/create、handles/identities新list、fd/_close_failure/_known_closed的实际赋值接到原记录。两个原 BUILD_LIST结果先保存，不允许在enter结束时把当前列表认作原list。C setter或list append的普通返回与通过固定 C dict/list观察到的效果分列；即使setter/append已经发生而后置检查失败，也保实际效果，不回填普通return。

Directory owner 字典要求exact str keys并使用原C dict descriptor。strict业务边界核path/base原对象、parent=None、create=False、原journal、原handles/identities、fd状态及没有 `verify/__exit__`等影响本链dispatch的实例shadow。绑定方法本身每次读取可产生新MethodType；应核实际 `__self__/__func__` 与原函数，不能要求每次bound-method对象 `is` 相等。

enter固定顺序如下；每个call在实际执行前消耗一次cell，普通返回先保存，随后再做getter/pins/原图后验：

| 原步骤 | 实际记录及后验 |
|---|---|
| begin（try外） | 原owner/journal参数、begin普通返回；opening效果；base比较和原journal/clock内部边界仍分列 |
| close_failure门/known_closed写 | 保存原读取/写入结果，不把前置拒绝改成try内exit |
| anchor/Path | 原path.anchor实际返回，原Path(actual_anchor)实际返回和image |
| parts/逐join | 原parts tuple、slice/逐component、每次原join返回；实际paths list及append返回/效果 |
| index | 原paths.index(base)实际返回；index为exact int且正常strict应指向目标，不能用算出的len代替实际返回 |
| parent/create/os.name分支 | 每个可调用边界后及进入分支前，重新核当前严格字段，防begin后的clock/callee漂移引入legacy分支 |
| 第一遍每ancestor | `_native_path(path)`、`os.stat(actual_native,follow_symlinks=False)`、`_safe_stat(actual_stat,directory=True)`的实际参数和返回；stat对象先保，再读取其字段 |
| 目标open | 原 `_win_open(...,_owned_directory_slot=原slot)`普通返回；原slot custody/native返回仍是独立事实，不能以slot已owned补写outer return |
| append及字段写 | 原handles append、journal stat identity写、identities append、fd=None的实际返回与effect；固定原列表的完整前缀持续核同 |
| entry verify | 创建新的verification invocation，走第6节；不能把首次stat复用作第二遍 |
| activated | 原调用实际return与原journal active/enter_returned effect，slot闭包仍独立 |
| enter外层 | 内部walk completed、`_DIR_ENTER`真实return、filesystem wrapper正常退出、root.directory_entered原slot write四层分别保留 |

strict create=False时FileNotFoundError原样抛出，不启动mkdir、parent open或第二个slot；legacy对应分支保持原行为。保护目标前已有N-1次stat不意味着这N-1项拥有HANDLE。目标 `_win_open`内部Path转换、ctypes、native获取/query/close全链尚未在本段全部认证，不能因为outer call记录完整扩大结论。

## 6. 每次 live verify的新调用，不消费第二个resource attempt

新增私有 `scope._directory_live_scope_owned(actual_scope,actual_group,actual_root)`：只接受原内部实际对象；从原独立scope/origin/registration图核当前owner/thread/Task、原guard_scope ContextVar绑定、有效token、root身份、first和可用phase。它不建立/旋转Context、不issue/settle grant、不改变counter、不调用只适合构造的check_row，不把一次成功缓存为长期有效。

合法phase覆盖entry末尾（directory journal opening，原attempt仍pending）、同owner后续opening/active（journal active，原attempt可能已completed）及固定stream terminal caller。另一root正在opening不应使此前completed目录无法verify。原业务可能有尚在进行的read grant；live检查核其原记录身份及所属scope，不消费它或因pending一概拒绝。guard_scope rotating/已reset/failed、group retired、wrong线程/Task、空或不同ContextVar绑定拒绝；cleanup不调用这个入口。

原正常close有一个必须保留的区间：group._retire_context先使EX guard inactive并reset lifecycle._CURRENT，然后ExitStack才调用stream.__exit__→_terminal→_verify_directory；此时group仍active（partial unwind可opening），stream/Directory journal仍active，独立guard_scope绑定尚可存在。因此terminal caller不能新增“EX guard active、lifecycle._CURRENT仍是guard、context_reset_state仍owned、group._exit_attempted为False”等门。failed/primary路径原stream exit跳过terminal，本段不为它额外添加verify，原raw cleanup照常执行。

只读owner/task和原ContextVar绑定无法区分具有相同绑定的 `copy_context()`；本段不声称精确Context身份认证，不把新marker或当前token对象相同当作证明。这项保持unknown，后继最终probe→IO Context闭包义务不变；验证矩阵应确认该边界而非要求无法实现的copied Context拒绝。

resources的verify entry从原walk索引恢复owner；每次先登记独立invocation，再做live核验。其不可变输入是原directory、原walk、实际caller类别和完整原identities记录。caller类别由原固定 producer接点决定，不接受caller自报字符串来选择弱校验。

直接verify按原顺序：close_failure→parent门→对每个实际identity执行新的native/stat/safe_stat→实际dev/ino比较。strict parent持续为None。identity每项必须仍为原enter append产生的原tuple和原path对象，不以tuple值相等替代原记录。stat immutable structseq原对象/类型/相关原descriptor及读取值保留；不会对未知对象执行外来属性getter后再认证。

每次invocation都记录自己的实际stat集合和普通return。entry verify通过后还须等待activated及enter外层返回；后续verify通过不重置walk或令旧attempt变pending。验证失败保原first，后续调用拒绝，不继续新stat；普通成功并不授权业务I/O、source_closed或写权。

## 7. 去重校验路径必须接线

`_directory_verification_plan`只选择实际图，不产生物理成功。保留legacy/custom verifier的原fallback。strict owner若出现shadow/索引损坏，必须拒绝，不能因plan返回None走无记录的fallback。原函数、bound method、directory/parent/identities的计划来源按实际返回记录，计划图与原owner/有效scope绑定。原plan对象允许重复调用：lifecycle._verify_lock_group实际在marker读前后两次调用同一plan，_verify_guard_plan_directories_original也复用原plan.directories。每次调用创建新的invocation和observed，只拒绝把旧成功receipt/cell或旧stat缓存用于新调用；不得消费原plan对象使第二次正常验证被拒。

分支选择前必须全量preflight：lifecycle._verify_lock_group在 `_original_lock_dispatch` 前核输入中的全部已知strict lock/Directory原归属及本链dispatch；不能等dispatch=False后才看第一个legacy项。_FileLock.verify/verify_stat原固定入口和 _verify_guard_plan_directories_original同样接原strict gate；strict lock的verify/verify_stat/_verify_entry_stat实例shadow也不能靠custom fallback绕开。归属从resources保存的原lock/Directory索引恢复，不对未知custom对象先调用外来directory getter。纯legacy保持原自定义dispatch；原内部locks为tuple，strict核其原完整序列，不为了preflight消费外来iterator或把外来序列归入strict。

`_directory_verification_plan`进入原 `all(visit(...))` 前也先遍历原输入中的全部已知strict目录完成preflight。custom项在前导致all短路，不能漏后面的strict损坏；preflight不做stat、不产生物理成功、不补原graph之外的Directory或改变原fallback调用次数。

`_verify_directory_plan`每次仍建立新的observed字典。对每项保存实际native返回；原字典miss才执行一次os.stat并保存实际返回，hit保存对本次同一stat cell的明确reuse边。共享stat只对本次plan有效；每个directory/identity仍各自执行原safe_stat与dev/ino比较，不能把命中缓存当跳过安全检查或跨调用永久缓存。

多root路径重叠时保原去重次数和各自原期望；不为了更多记录新增第二次stat，也不把一个共用stat报告成多次真实query。mixed strict/legacy plan保持原计划顺序和fallback，strict各项的caller/invocation/结束事实不能在整个plan失败时被冒充为所有目录成功。

entry、直接live、去重plan三个入口须在同一完整候选组合中接齐；本段不能只加entry记录后宣称Directory验证链完成。

## 8. 固定callee目录和原始清理

固定目录至少含：Directory init/enter/verify/exit；原 begin/activated/journal/capture/raise/exit/slot retirement；_native_path/_safe_stat/_error/_win_open/_win_open_owned_directory；实际Path factory/class/MRO/slot descriptors、anchor/parts getters、joinpath/with_segments/eq/str/fspath及其本段触及的纯Python函数；实际 os.name/os.stat、stat类型与S_IS*函数、_REPARSE；list append/index与C setter；plan两个函数。class/function/code/default/closure、本地alias、实际callee的必要module globals和模块原字典分别列pins，不能认为function code pin同时冻结其globals。

源码中出现的pathlib、ntpath、genericpath、os、stat调用边逐项记在有限catalog。Windows ntpath.abspath可能调用nt._getfullpathname并走失败fallback；源码文件保存只证明调查了该实现，不证明已观测C返回或当前运行绑定。未实现/未覆盖的边保持explicit unknown，不以全module摘要、源码版本号或无阻断review替代动态闭包认证。实现时禁止以扩容catalog为理由修改原native行为或开启诊断。

原六raw stream cleanup不改；Directory唯一close仍由原journal/slot与原registered callback负责。新增验证失败、Path/list/owner展示字段损坏或scope未可用时，try内失败仍执行固定原Directory exit；严格分支使用已捕获的原exit callee，不能再通过已知可能受shadow污染的 `self.__exit__` 决定是否关闭。legacy保持原动态dispatch。

cleanup不新加live/Path/callee认证门，不从可写handles列表决定nativeclose，不重试UNKNOWN数字；原first/tb、capture次序、closed/unknown和后置clock效果保持。原begin try外拒绝仍由外层已登记callback接续，本段不新调用enter内exit。原返回后/效果后抛出的模型故障必须明确为合成，不能声称复现真实C写后抛或内核故障。

## 9. 文件级实施顺序与检查

### Task 1：冻结有限来源和实际调用目录

Create `data/t16-h01-directory-walk-provenance-r1003/` 的SOURCES、调查记录、完整raw副本和本设计副本。读取r1002 source05八文件及其final/delivery/当前记录、r919设计、c973/storage_lifecycle.py第九parent、解释器配置和实际3.12.10的pathlib/ntpath/genericpath/os/stat源码。逐份保大小/SHA/全文；external源码只读。主方案、AGENTS在更新前保原before；不遍历正常库、media、.git内容或服务。

生成仅AST的有限调查：标原enter try外begin、所有原clock位置、完整实际call expression、verify/plan入口、raw cleanup函数和Path slots；它是静态catalog，不是动态调用覆盖。两份设计复核无阻断后继续，不新增普通开发审批。

### Task 2：Path images与constructor/walk原记录

Modify 九文件组合中的 `reno/personal_authority_guard_resources.py`、`reno/sync_media.py`；其余parent全文继承。资源的原raw工厂增加四类record、独立索引、固定Path slot images和constructor字段/list来源；接原base比较，保legacy分支。对记录API只暴露固定内部函数，任何caller-provided callee/expected不进入API。纯legacy冷路径不因strict preflight无条件导入完整新resources图；若必要的固定惰性分类增加import依赖，须显式列出并覆盖冷调用，不声称原分支全文无差异。

先静态核全部parent/inverse/diff、未变native原函数与清理方法、实际clock位置、constructor/enter原try边界。不得通过改原期望或跳过callees使模型变绿。

### Task 3：完整enter、live verify和plan同版接线

Modify `reno/personal_authority_guard_scope.py` 的live检查与固定catalog，resources/sync_media三个入口，以及 `reno/storage_lifecycle.py` 的strict全量preflight与原verify/fallback callers；如固定group/owned consumer目录需新export，窄改原对应catalog并保存完整diff。不得手改r1002父源或正式/c973目标。

entry helper不得重用live构造门，live helper不得重置已completed attempt；原pending grant可并存，不issue/settle/charge。plan dedup保存实际reuse，原同plan重复调用每次fresh。两份源码审查覆盖完整九文件组合及必要追加文件，不能只review patch片段。

### Task 4：有界验证准备与唯一运行

新固定runner仅source-selected执行完整原constructor→enter→entry verify→后续direct/plan，不再用合成Directory enter/verify替换目标。原os.stat/native/EX/clock若合成，逐项列出；stat可用真实os.stat_result的合成数据，不能称真实磁盘stat。Path词法用本解释器真实WindowsPath；记录各callee真正被选中的原AST及被合成项，标真实线程/Task/Context测试。

优先以下固定行为矩阵，具体ID与全集在执行前封存。表中是待测要求，当前执行0。

| 组 | 必测行为与结果 |
|---|---|
| 正常完整链 | 1/2/4 roots；本地/UNC/长路径词法；每目录两遍N stat/一个目标open；entry后direct以及plan再次fresh验证；plan重叠路径只执行原dedup stat |
| Path状态 | factory原返回与input image分列；正常lazy变化；raw_paths/tail原地改、同值新list、cache替换/不合规范值/无关cache写拒绝；不预热或回填历史 |
| 返回/效果 | stat/safe/open/verify/activated/enter不同cell；真实返回先保存；list append/C setter前抛与发生后抛合成故障分列；outer FS与root slot后置失败保内部事实 |
| 分支与dispatch | begin后每个callee处改变parent/create/os.name/path/base、verify/exit shadow拒绝；known strict缺锚或None不降级；legacy owned/nonowned/POSIX/create/parent保持原行为 |
| live阶段 | entry/opening/active/completed且另一root pending；原pending grant不被消费/结算；正常EX context先reset/guard inactive后的stream terminal仍可fresh verify；failure路径不新增verify；rotating/retired/wrong线程/Task/空或不同ContextVar绑定拒绝；同绑定copied Context精确身份保持unknown |
| 安全与失败 | FileNotFoundError不create；每个ancestor stat/native/safe故障停止后续IO；目标open返回后故障仍原slot唯一close；first对象/tb、close UNKNOWN保留与不重试 |
| 原图损坏 | 单个owner/attempt/walk/path/state索引、field/list/tuple或presentation损坏拒绝且不污染foreign；清理不依赖上述业务getter恢复成功 |
| plan | mixed strict/legacy/custom且original_dispatch=False；custom在strict之前的短路；严格损坏不走fallback；同native不同expectation都检查；命中reuse仍safe/compare；原同plan两次fresh成功、第二次故障不沿用首成功；跨次无cache；半途失败不假报全完成 |

runner、实际准备两份复核通过后，只使用新claim运行一次；旧claim和诊断不能复用。若fixture失败，保原红并只修已证runner缺陷，原已pass不因凑单批全绿而重跑。纯模型、真实解释器机制、实际SQLite/native/产品行为分栏。不得以模型全绿代替原111/94/r919真实组合准入。

准备及静态命令形态（实现后在本工作区，PowerShell7）：

```text
.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-directory-walk-provenance-r1003/prepare_source_01.py
.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-directory-walk-provenance-r1003/verify_source_01.py
```

实际runner文件、固定ID全集、hash、限额及claim待Task4准备后封存，当前没有可执行准入；不能把这里的命令模板当执行授权或成功记录。产品pytest/full111等另按已修改完整组合及普通回归准入推进，不添加定点计时诊断。

### Task 5：交付及后继

保完整实际输出、每具名case、return/effect、raw完整pair、旧红与claim耗尽事实，Root及同一独立代理复核。主方案只追加§17.398及以后对应事实，AGENTS只在项目facts处更新，公共生成段不改。

回滚边界是“不安装”：九文件仅候选，canonical/c973 apply0、累计正式11；不对现有dirty main执行还原。发现设计/实现缺陷保旧版另出修订，不能覆盖原记录。未来任何正式apply前先核新的完整组合与实际准入，不借本设计开放写权。

## 10. 本段之后仍必须完成

原root Path在StorageProfile/_roots内部的完整来源、全部stdlib/global/C/ctypes/lock实际返回、buffer→raw→fd/native完整认证、三marker实际read与同唯一1MiB budget request/charge及完整future N/容量、最终probe→IO Context区间、新五件图/final census/正常父退出与四恢复仍未完。

原111/94/r919、Store/source_closed/live witness/写权、H02–H06/永久分享与完整Goal仍active未完成。不用本段设计或未来source-selected模型替代这些义务，不把stat/native两套identity合称同一物理资源。

r967仍待原专门问题，不执行/重问；r920/r964及旧已启动claims耗尽，重复旧授权不重跑。P06历史0/7不问。原case30/parent360/matrix1680/native20–25–10/cleanup10/最多2pytest child/control1MiB保持。正常库、服务/dist、既有ACL/settings、真实平台和Git保护保持。

## 修订02说明

01独审D001–D003分别是原同plan复用误拒、同绑定copied Context精确身份过度承诺和custom fallback/短路遗漏strict gate。02保原plan可多次fresh调用，Context精确身份继续unknown，并增加lifecycle第九完整parent及原caller分支前全量strict preflight。另明确原正常terminal发生在EX guard inactive/lifecycle current reset之后，不新增guard active门。01原稿、原source与红报告保留；本修订未生成、compile或运行产品候选。
