# H01 原资源链完整适配方案（r1202）

状态：基于当前源码的完整范围盘点及实施方案草案；资源边界 A/B 尚待确认，实际入口来源、完整字段与生命周期准入仍须闭合。不是产品候选、安装或运行许可。Goal「按照方案推进执行」保持完整，不以本子方案代替 H01–H06、H02 持久链、分享撤销、P06 历史缺证、条件 T15 或生产云。

修订03：在02的D001 directory直接Create坐标、D002 stream generic接纳域修正上，补D003：S506–510仅先于source-role获取，unit其它角色此前已经创建；并明确构造时先初始化本层NULL/scalar自动槽。01、02完整保存在本批DESIGN-01-RETAINED.md及DESIGN-02-RETAINED.md，source两份早期转录保留，选中SOURCE-INVENTORY-03.md。独立复审01的结论不冒认03已审。

## 1. 要交付的行为

原目录链、source census/read/final metadata 及失败清理真正使用同一批原生资源拥有者。创建成功后，拥有权先写入真实私有槽，再做 Python 结果、事件、计费和错误表示；每个消费者持续持有同一个原拥有者，每次真实关闭最多一次。正常关闭、事件失败、lease 失败和债务发表失败分别记录，不能由一个 `unknown` 标签反推是否调用了 OS。原 UUID、业务对象、预算、checkpoint、first 和正常/失败退出语义继续保留。

不能只把 EMPTY owner 放进旧 dict，不能只替换 Create/Duplicate 后让旧 ctypes 从另一个 raw 值继续工作，也不能用外来 owner/type/raw/fd/DTO 的 register/adopt/setter 补来源。所有原调用次数、审计时点和中间 commit/sync 都有明确消费位置。

本轮实际证据：Root读完整 directory 相关实现和原审计实现；同一只读代理读完整 source native 操作、source 消费/退出及 operation 的实际 driver，并独立还原 live↔r1198 的五个完整函数。没有运行示例、候选、编译、产品、native 或诊断。

## 2. 当前基线与已具备的前件

| 对象 | 当前已确认状态 |
| --- | --- |
| live directory native | 72134B，SHA256 `b6ce5525994f52ef1b4b327685e0c709344ffd304edec2ef11039f2e9954b5df` |
| live source native | 130700B，`f5355dfd190b59c6ebbd367c4a2119c1294b5aaaee0cc6a93060e1d167587db7`，2586行 |
| live operation | 164318B，`74b55bbac5193659b3f25b72f1247cd15c23af67251887550040649654d8def5` |
| live source | 52239B，`2b0d6bbb5c5dc4859ccdc760be034e7ca7d769581c6d53145655299f600331e1`，1076行 |
| r1201 selected | 11份 C/H、117472B，未安装；实际 kernel 消费 typed factory，仍只产 EMPTY owner |
| r1198 selected source | 132027B，`59a8c270cc1d2012a115fb9baf059207b9360c1d27bfaa21026ceec608512f91`，2616行，未安装 |
| 构建入口 | 当前 pyproject 声明 Python>=3.11，无已配置 native extension；reno/__init__.py 目前只有编码行 |

r1201 的 opaque owner 已有 Create、pre-transfer HandleInfo、Duplicate/prepared job、HANDLE close/失败观察。更低层有 audited transfer、转移后 HANDLE query 和 fd close，但它们接 `RenoWindowsSerializedSlot *`；不能把 opaque owner cast 成 slot 或从 Python 传地址来调用。r1111/r1112 的 bootstrap/根转交仍是设计，不能当现成 C caller。

## 3. 全部资源范围与不可拆链

### 3.1 Directory：H/H2/H3 必须联合迁移

以下行号均指当前 `reno/personal_authority_replica_directory_native.py`。

| 阶段 | 实际位置与责任 | 完整适配要求 |
| --- | --- | --- |
| holder 构造/登记 | 452–470；holder_states、resources、holders及mirror分别发布 | 在 ticket/native 前完成；每个 partial 出口仍有真实 cleanup root |
| authority 暴露 | 881–884 返回同一个 holder dict；901–910 wrapper限键不令dict不可写 | 原资源关联必须在私有 C 存储中；不能从当前dict补expected |
| begin/settle | 1064–1096；settlement门先锁存，返回lease先保存再递送first | 一次ticket只结算一次，不能把end失败再送fail_resources |
| H 创建 | 1099–1127；真实Create、last-error、原获取分类 | actual成功先入槽；表示/事件失败仍保真实资源和当次结果 |
| H2/H3 duplicate | 1130–1170；原output和GetCurrentProcess在begin之前准备 | 两个实际owner持续持根；原process检查位置、unknown输出和计费保持 |
| transfer/GET | 1173–1198；OPEN成功→fd/closer/transfer更新→原GET比较 | 不合并OPEN/GET，不省commit/sync，不再把转移后HANDLE交给CloseHandle |
| 原关系核验 | 1201–1248；HandleInfo、FinalPath两次、父/当前path stat | 不用仅HandleInfo替代；保MAX_FINAL_PATH、DOS/casefold、属性/reparse及父关系 |
| H2首次身份 | 1274–1293；duplicate→transfer→fstat→关系→双finalize→close | H2实际fd及原H同属连续生命周期，所有中间错误有唯一closer |
| H3终末身份 | 1296–1320；duplicate→transfer→fstat→finalize→比较→finally close | 原identity first先于H3 closer；真实关闭及lease结果继续独立消费 |
| 正常/失败关闭 | 715–794、811–847；transient逆序后H逆序，最后retire/pop | 全部借用停止/结束后才close；失败不重试，UNKNOWN不退款 |
| callback/checkpoint | 944–965、1337–1382 | callback可以在H仍OWNED时发生；不能以callback准入等同资源已关闭 |

目录内资源型调用点为14个句法位置：Create1、CurrentProcess1、Duplicate1、OPEN1、GET1、HandleInfo1、FinalPath2、path stat2、fstat2、两个captured closer各1；这只是当前文件的具名索引，不是运行次数或全项目调用图。

### 3.2 Source：五类实际资源，而非一个HANDLE owner

以下 SN 指 `personal_authority_replica_source_native.py`，S 指 `personal_authority_replica_source.py`。

| 资源/用途 | producer | 真实消费者与唯一closer |
| --- | --- | --- |
| directory_handle / D | SN1547–1566；H/H2先登记；直接Create1556–1558、Duplicate1508–1528 | HandleInfo1467、FinalPath1474/1480、OPEN1412、GET1425、fstat1533，**转移后仍在1535查询HANDLE**；H2于1574关闭，H随scope关闭 |
| file_handle / reader、acl_object | SN1620–1635；Create后transfer | fstat1627/1614、HANDLE查询1612、read1961、ACL GetSecurityInfo1796；已转移者只能fd close；ACL临时文件1852关闭 |
| scan_iterator / find | SN1878登记、1882 FindFirstFileW | name/attributes有界读取1910–1919、1921 FindNextFileW；1933 FindClose。无匹配1890–1899只结算零获取 |
| token_handle / acl_token | SN1748–1750 OpenProcessToken | GetTokenInformation1753/1761，1775 CloseHandle |
| security_buffer / process_sid_text、owner_sid_text | SN1683–1687 ConvertSidToStringSidW | LocalSize1691、WCHAR有界读取1694–1705，1709 LocalFree |
| security_buffer / security_descriptor | SN1794–1797 GetSecurityInfo | LocalSize1799、Valid/Length1802/1803、owner SID1807–1814、SDDL1819；1848 LocalFree |
| security_buffer / sddl_text | SN1817–1819转换 | LocalSize1821、count/容量1822–1824、WCHAR读取1829–1839、文本验证1840；1844 LocalFree |

原closer四路在SN645–658：fd→os.close且返回None；handle→CloseHandle且exact-int非零；find→FindClose且exact-int非零；local→LocalFree且None或exact-int零。必须按真实producer固定kind选择，不能接收caller自报closer。

SN49–50虽然声明六个kind，当前固定业务producer调用点未产出`stream` holder；generic kind声明及`_NEW_HOLDER/public_new_holder`的接纳域仍存在，不能据此删除。`_stream_file_original`是按fd读取函数，不创建stream。`_SEEK`、`_WSTRING`有捕获而没有本文件调用。现阶段不为不存在的业务producer加新stream/seek入口；若其它实际入口后来需要，须先补来源和退休路径，不能据此宣称通用stream支持。SN1382–1402的generic Create helper在当前固定业务链由file1623消费，directory直接Create不可误归到它。

临时Python输出壳、HandleInfo、FindData、固定buffer以及CurrentProcess pseudo-handle不混计成新增OS资源。process SID借自实际Python buffer（SN1764），buffer覆盖检查和SID转换1771；owner SID借自descriptor HLOCAL（1807–1814）；WCHAR pointer必须持续持其实际HLOCAL，不能以pointer对象本身替代底层拥有根。

### 3.3 长期根、回调与退出

SN319私有units/holders持登记；new_holder494–518依次发布holders、unit序列、role scope序列、mirror，返回后SN1335才begin。SN819–821返回实际权威dict。C关联必须在这些发布之前来自实际producer，且覆盖任一个partial位置。

coordinator目录SN2100、observer reader2152、output目录2199/2208、source ancestors/directory/file reader2283–2295及2357/2362不是一次局部调用就结束。observer reader在2471–2472 final metadata仍被核验。已有owned资源可跨合法callback，SN912–919/1187–1198仅排除pending identity、active read、pending ticket、terminal、unknown等，不证明所有资源已关闭。

S506–510在首个source-role acquisition之前捕获真实close/audit/gate；coordinator/observer/output已在operation2602/2605/2608创建，由2595–2599预捕获的unit cleanup/retire覆盖。S912–939在reader仍活着时执行cache/builder/codec；S528–579清理source role，S581–611移除source登记。operation2625只清source role，2626再清unit其余角色；2663–2674失败出口仍分别调用两套cleanup和retire。不能让source record退休提前释放coordinator/observer/output。

两份source没有async/await函数，但owner含实际Task（SN407–435、S242–265）；持GIL不等于同owner。任意异步中断、finally自身失败及持续OOM的完整保证仍待，不能用同步函数形式消除它们。

## 4. 架构选择及资源边界裁决

### 4.1 对比后保留的结构

| 路线 | 结果与代价 | 当前裁决 |
| --- | --- | --- |
| 原C调用链内建立私有immutable binding，原Python层保业务投影/纯codec | 复用现有factory、预算和业务入口；须逐一覆盖本表所有资源与原source/Consumer转交 | 推荐的实现结构，资源边界仍须按4.2确认 |
| 仅在dict加owner或只替换H Create | dict仍可写，原duplicate/transfer/fstat/read/closer会继续消费另一套raw | 否决，不作为阶段性可安装版本 |
| 独立资源进程/更强OS隔离和逻辑资源协议 | 必须重做audit兼容、错误/取消/部署及真实授权协议；单有进程不证明任意同用户特权操作都被隔离 | 仅当要求更强外部资源防护时另作完整架构，不声称现有C候选已提供 |

推荐结构的binding由原C constructor实际调用r1201 typed factory后创建，owner引用在untracked初始化阶段写入，发布后不接受setter或adopt。原Pythonmirror和key仅作原可观察投影/定位输入；实际owner从独立私有binding与原operation source取得，绝不从它们制造来源。原C ingress/Consumer→actual registration→actor/binding的运输尚未实现，不能把这些拟议类型当现成能力。

### 4.2 R001：必须明确定义的审计与外部资源边界

当前audit.c68–69将actual OWNED HANDLE交给`PySys_Audit("msvcrt.open_osfhandle", "Ki", ...)`，164将actual fd交GET audit。官方固定CPython3.12.10 `PC/msvcrtmodule.c185–215`也是先同实参audit再调helper；`Python/fileutils.c2749–2785`最终直调CRT。audit.h15–18明确允许Python callback，外部效果不回滚。session的SRW gate只控制本方API；operation2894–2914与lifecycle788–823是route/lifecycle观察门，不拦标准fd/handle调用。

因此只要合法hook保留实参数字，未来就不能继续声称“没有真实raw/fd外溢”。hook通过正常Python I/O建立外部使用者也不需要非法C cast。注册borrow计数归零只证明本方登记工作已归零，不能证明这些外部使用者已退休。当前owner仅pre-transfer、尚未接audit入口，故这不是r1201已开放的错路，而是完整fd组合的未闭前提。原r1187也已承认外部close/dup2未闭，本轮把它与owner退休证明的矛盾具体接起来。

需要在实现前选择并记录产品交付合同：

- **边界A（推荐，保原接口）：**保原业务、原audit实参/时序；保证项目实际producer/consumer/closer全部受管。审计hook或其它外部代码直接挪用、关闭、重绑这些系统资源不属于该拥有者保证的使用域。仍须处理本方真实调用返回的错误，不把hook的未知效果写成零，也不降低原认证子图的unknown-callee/getter/hash/eq要求。
- **边界B（更强隔离）：**还需防御此类外部操作。须重新设计隔离主体/权限和逻辑资源协议，并明确修改audit兼容及部署方式；不能仅加一层C锁、禁止全部hook、隐藏一次PyLong结果或给同一资源再套一个dict就称完成。

本方案尚未代用户选择A或B。A是明确的保证范围，不能在继续声称原无外溢强保证的同时暗中采用；B也不是现成可安装备选。确认前不扩展owner的fd/dealloc图，不把外部退休前提当已兑现。

## 5. 按实际消费建立的状态与记账协议

每个实际资源记录至少分开：producer是否调用/真实结果、拥有态、表示是否成功、native closer是否尝试/真实结果、known-close证据是否生成、event/counter是否完整、lease是否尝试/是否释放、debt是否实际发表。它们不是由一个Pythonoutcome字符串重建。

1. 构造：合法实际module/source根先存在；先初始化本层NULL/scalar自动槽，再进行可能失败或回调的type/owner/binding准备。原typed factory当次返回实际NEW后，才将该owner填入untracked binding，再按协议发布；完整字段、部分初始化及根持续区间仍按I03/I08闭合。登记序列逐一发布，记录已完成位置。尚未begin ticket时失败，不虚构native attempt；已有公开GC观察者时仅释放本方引用，不能假称对象已销毁。
2. 获取：保原begin位置；C producer真实成功后首动作写原生槽；之后再作Python表示/原acquired/event。任何表示或记账失败，实际owner仍是唯一cleanup根；settlement尝试先锁存，实际lease先保存再递送其first。
3. 转移：OPEN audit/CRT成功→fd在同slot提交、HANDLE不再拥有→原Pythoncommit/sync→独立GET audit/helper/比较。GET/表示失败不撤销转移、不恢复HANDLE closer、不重放OPEN或GET。
4. 使用：每次从原binding取真实owner，持续持根，登记相应borrow，执行实际native工作，end后才退guard。FinalPath两次调用、固定buffer分配、SID/HLOCAL读取及纯codec之间的根持续区间分别列清。prepared duplicate同时持source/destination，固定process准备位置不挪到begin之后。
5. 关闭：先停止新本方借用并完成相应实际退休条件；原closer最多一次，先保存真实normal/failed事实，再做event/release/debt。失败UNKNOWN的历史数字不授closer，不重试或退款。最后引用析构不是原正常记账协议，不能用它补lease或event。
6. 退出：source-role与unit cleanup/retire分开，保原first优先级。全部C owning槽先detach再作可能回调释放；成功nonlast与失败maylast逐笔证明。无论是否先m_clear，m_free/dealloc都需同一清理协议；完整N4/SCC、一般NULL和allocator合同仍须完整字段表才能准入。

### r1198必须合入的原记账修复

source live↔r1198仅五个完整closure函数改变，代理已原字节正逆核同：new_holder494–518→494–520；retain555–567→557–579；release598–616→610–632；close617–711→633–735；cleanup712–736→736–766。retain在实际put成功后才提交，release先锁存，正常close后的证据/事件失败仍进入原lease finally；certified closed后的失败记unknown_bookkeeping，原已有unknown标签不覆盖。

目录同样保r1198对正常close后计数/sync/event及独立lease finally的完整修订。不能只移native调用而遗漏这些修复；也不能把r1198说成已经解决计数/两次append事务、持续OOM或退休前未发表记录保全。共享debt的键域仍须统一审查；本轮不声称已修复该冲突。

## 6. 文件级任务、依赖与交付门

以下是最终应有的改动责任，不是已创建的产品文件。

| 任务 | 文件/真实位置 | 具体产物与前置条件 |
| --- | --- | --- |
| I01 交付合同 | 本方案§4.2、原owner/audit/slot合同 | 确认A/B；所有无外溢/外部退休用语统一，保持原认证子图边界，不偷改保证 |
| I02 原入口运输 | r1111/r1112及后继真实Ingress/Consumer/A/J/Product/source bank | 实际原module、实际operation/registration和holder constructor之间的拥有链；不从Pythoncurrent alias认领；尚未实施者保持open |
| I03 私有holder关联 | directory452–470、source494–518及原driver | constructor真实NEW owner→immutable C field→多登记partial；原字段镜像/校验和退出根一并更新，禁止generic attach/adopt |
| I04 owner完整资源变体 | 现有11份C/H的未来正式native源码位置 | HANDLE/fd、Find、token、HLOCAL及内部指针五实际类目；closer由实际producer固定；无假stream入口 |
| I05 directory全链 | directory上述H/H2/H3及关系/关闭表 | 完整Create/Duplicate/OPEN/GET/FinalPath/stat/fstat/close替换，保持checkpoint和票据/原错误分类 |
| I06 source全链 | source_native上述资源/生命周期表；source506–611/729–939 | reader+转移后HANDLE、Find、ACL token/SID/HLOCAL、read Q逐次结算、final metadata及两层退出 |
| I07 记账与保全 | r1198两候选及operation first/debt消费者 | native truth与event/lease/debt分别提交；first OOM/永久gate/未发表记录跨退休的协议和统一debt schema |
| I08 生命周期合成 | 全state/implicit type/module、actor/binding/borrow/job、source bank | 完整owning occurrence表、每个acquire到release持续区间、partial/NULL/SCC和N4；不把角色表当完整字段数 |
| I09 构建/加载/兼容 | pyproject、未来显式extension构建配置、reno/__init__.py及实际入口 | 当前5个C翻译单元/6个header只作输入起点；同CPython/full API/CRT/SDK/错误表绑定，source-editable/in-place/缺失和回滚路径；不缩减>=3.11，先解决与r1112全局T0导入的真实接点 |
| I10 准入与交付 | 完整最终候选、真实调用入口和原限额内runner | 两审、完整差异/逆像、精确输入、原合法成功/失败域；实际运行许可/额度未具备时保持未执行，不能重置旧预算 |

I01/I02/I03/I04/I07/I08/I09要联合闭合后，I05/I06才能联合安装；不是按表随便挑一行先装。可在完整设计认可后分责任编写，但每个源码块必须有真实最终消费者和未安装边界。I09目前仅有项目元数据事实，不包含已选正式构建脚本或可执行命令。

## 7. 验证矩阵与可信口径

| 目标 | 必需证据 |
| --- | --- |
| 来源/不可替换关联 | 真正原constructor产物及连续强根；改mirror/dict/key/type/alias不改变C资源选择；foreign输入拒绝不授信 |
| 多登记/分配失败 | 每个partial位置、publish/GC可见前后、本方最后引用与外部观察者分列；已有native资源绝不丢失/重领 |
| 原顺序/审计 | OPEN→commit/sync→GET；GetCurrentProcess/output准备→begin；实际audit失败/返回与原first；不能用无hook模型覆盖R001 |
| 五类资源正常使用 | 真H/H2/H3、file read+转移后HANDLE、Find、token/SID/descriptor/SDDL；真实final metadata及全部closer，不用Fake当native通过 |
| 关闭/记账失败 | 每个closer真实一次结果、normal closed后的event失败、lease部分提交、debt发表失败、持续first和退休保全；不重关/退款/重放 |
| callback/并发/退休 | 已owned资源跨原允许callback，同Task/thread/pid核验，双owner duplicate、BUSY、每个已登记borrow结束；I01所选边界单独验证 |
| N4/ABI/兼容 | 全owning图/NULL/partial/SCC、实际build/import/export/同CRT、支持版本和非目标业务入口；字节检查不能代这些证据 |

当前仅有源码/原文盘点与有限正逆证据，没有上述运行结论。原30calls/2children/1990365B和已过截止不重置，r920/r964单次诊断已耗，r967不执行；不重放旧QA/未变失败/r1173修订04及21未跑，不新增异常链/frames/locals/notes、profiler/marker/native微诊断。正常库、服务、dist、既有权限/settings和Git不操作。

## 8. 发布与回退

发布前要求完整组合源码双审、真实准入及本矩阵的范围匹配证据。原源码、构建输入和installed候选分别封存；不拿单个C helper的通过支持整条产品通过。

回退只针对本次明确安装的源码/extension和加载入口，按预先保存的准确before恢复；不reset/clean整仓，不触碰真实库或既有权限。已经产生的资源必须先经原真实owner cleanup/debt/retire，不能通过换回旧Python文件或reload遗弃C owner、重设first或重领资源。当前未安装，因此本轮没有运行回退动作。
