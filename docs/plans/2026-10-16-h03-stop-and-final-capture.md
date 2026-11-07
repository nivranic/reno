# H03 旧主停写、最终捕获与原操作恢复：实施依赖及候选设计

日期：2026-10-16。主方案：`<PROJECT_ROOT>/docs/plans/2026-09-30-product-evolution.md` §17.91 H03。

本文件细化已有工作，不替代主方案。当前交付是有限源码调查和设计；没有实现、应用或运行 H03，没有停写真实实例。r967 修订02定点计时仍待其单独批准，本文件不授权诊断，也不改其434项冻结输入。

## 1. 要交付的行为

用户预览并明确选择源、目标及原操作 UUID 后，源端核对当前合法主身份，排空正在进行的写入，永久撤销旧任期写权，生成最终个人资料包，最后发布停写回执。任何中断均从永久原事实恢复；停写后不自动恢复旧主、不换目标、不另造操作 UUID。

完成 H03 只证明旧主停写及最终材料来源。新主安装、任期变更和开放写入属于 H04；不能把 H03 回执直接当作目标写权。

## 2. 当前实现与硬依赖

| 当前源码 | 已有能力 | 本任务不能据此推导的结论 |
|---|---|---|
| `reno/personal_authority_contract.py`，`encode/decode/cross_validate` | intent、fence、stop 的封闭字段和原字节引用关系 | 当前 active grant、真实停写、真实 SQL/附件或 producer 成功退出 |
| `reno/personal_authority_files.py`，`guard/read_fact/put_immutable` | existing-only 文件锁、原字节读取、同槽同 bytes 重试和 no-replace | 写权、同前驱 grant 唯一 handoff、全写者已排空 |
| `reno/storage_lifecycle.py`，`exclusive/exclusive_many/require_guard` | profile 独占锁及持有上下文核验 | process/必要 State 锁、发送者排空或 current grant 的完整观察 |
| `reno/personal_preservation_source.py`，`capture_locked` | 实际 EX 下的原文件持有和退出核验 | final-capture permit 或可直接传输的最终个人包 |
| `reno/personal_mirror_snapshot.py`，`capture_snapshot` | 普通 SH 下十表与附件快照 | 停写后的专属捕获或停写证明 |
| `reno/personal.py`，`require_write` | 现有 legacy fence 与当前主/epoch 检查 | 现代 live-grant writer 工厂已接通 |

具名搜索未找到 `reno/personal_handoff.py`、`reno/personal_promotion.py`，也未在 reno/app 中找到 intent/fence/stop 的运行消费者。已有 H04 transfer 诊断、H06 文件状态及前端草稿/取消处理不重复开发。

实施前必须闭合下列接口，其来源目前尚未完整实现或验证：

1. **H01 当前活授权来源**：同一真实 owner 窗口内核定完整 grant、SQL 登记、原操作、设施及 epoch；不能接收调用方的 DTO、`True` 或验证 callback 代替。
2. **H05 全部 writer/发送排空**：逐一核对个人、现场、上传、失败清理、发送入口的实际锁，以及占位 pending/fence 的拒绝位置；只有 profile EX 不足以证明所有相关工作停止。持久 pending 门必须先于首次 handoff 占位发布完成接线，不能留到末尾集成才补。
3. **H02 永久事实与恢复保护**：新 reservation、intent、fence、final package 及 stop 的版本和文件角色必须进入保全、备份、恢复和 GC 规则；旧 DB/旧包恢复不得撤销 fence。
4. **连续 owner 与真实关闭**：新资源的所有权、异常优先级和成功退出事实接入实际运行链，不能拿已返回的文件摘要当活许可。

现有 H01 的完整94项、Store、联合 SQL 提交/独立读回、source_closed/live witness 仍未闭合。可以先设计并准备独立完整候选；真实 H03 正例、公开提交及启用依赖上述前件。

## 3. 持久事实与唯一性

复用 authority format1 的现有 intent/fence/stop 字节含义，不静默扩字段。新增操作 reservation、最终包及完成观察如需额外字段，采用独立明确版本；其命名和 storage roots 接点需与 H01/H02 一并审定后再编码。

### 3.1 固定的原请求

持久保存并交叉核对：原 handoff UUID、domain、source/target endpoint、前驱 grant 原字节/摘要（promotion 前驱还含原 receipt）、current/next epoch、原 promotion UUID、preview revision 与个人 SQL SHA。目标、两个 UUID 和 next epoch 均不能在恢复时重选。

preview 是一次观察的 CAS 输入，没有长期授权。commit 在所有必要锁持有后重新观察；任一字段变化即拒绝，不自动替用户接受新版本。

### 3.2 同一前驱 grant 只有一个操作槽

唯一占位键由 domain 与前驱 grant 身份确定，不能只按任意 handoff UUID 建文件。占位绑定完整原请求和原 intent bytes；同键同原 bytes 才能只读核对/续跑，不同 UUID、目标、epoch 或 bytes 均冲突。未知/半写事实只能按原操作调查和恢复，不能视为空位。

intent 发布不能早于该唯一占位的永久建立和独立读回。占位存在但 intent 缺失时，只能重发占位中绑定的原 intent。跨进程竞争以真实锁及 no-replace 共同约束，不能用内存字典或派生 head 当唯一依据。

占位本身同时是**持久 pending 暂停事实**。发布前先在排空后的完整锁窗口核所有原 CAS；一旦占位存在，即使 intent/fence 尚缺或进程已退出，所有普通 mutation/upload/失败清理/send 都必须暂停。入口必须先核占位/未知状态再决定 grant 是否可用于业务；不因旧 grant 存在、SQL/config 仍旧、文件 head 缺失而继续写。未能确认占位发布结果、原件损坏或未知版本时同样拒绝，不按无占位放行。

pending 与 fence 的语义分开：前者表示原 handoff 已占用且业务暂停，后者表示旧 epoch 已永久撤销。本批没有取消/解除 pending 或终结槽的协议；一旦发布 pending，只能续原操作进入 fence。预览、普通读取和只读回执查询可以继续，但不能带隐含业务写入。外部篡改导致原 CAS 不符仍报冲突，不能通过更新 preview 或重算原请求恢复。

### 3.3 顺序与边界

`锁内排空并核原 CAS → 唯一占位（立即 pending 暂停）→ intent → epoch fence → 最终包 → stop receipt`

fence 引用原 intent 并撤销该前驱 grant 的旧 epoch。fence 发生后，旧 grant 即使仍在目录或旧 SQL 中也不能恢复写权。最终包只包含此前已存在的 intent/fence；stop 引用最终包，禁止最终包反向包含该 stop。

当前 authority 合同要求 intent 的 expected personal SQL SHA 与 stop 的 final personal SQL SHA 相等。实现必须复用其十表和个人 meta 投影范围，不能混入新增 authority journal、知识表或 worker 字段，也不能重写旧回执响应字节以使摘要相等。

## 4. 运行 owner 和最终捕获许可

新增 `reno/personal_handoff.py` 负责固定流程；其内部 owner/permit 是否拆文件，在完整 H01 接口确定后决定。公开参数只接受 selector/CAS/原 UUID 以及取消信号，不接受 connection、锁对象、授权布尔值、随意 callable 或调用者构造的 grant proof。

1. 按既定 process → 必要 State → profile EX 顺序取得真实资源。哪些 State 必须取得由实际发送接线决定，不为缺少 State 的个人实例擅自初始化同步设施。
2. 在同一窗口内核当前 grant、旧主、设施、目标、epoch、preview 及唯一操作；排空全部相关写者和发送者后，再核原 CAS 并发布唯一 pending 占位，随后 intent/fence。恢复只准真实 handoff owner 认证这个 pending 对应的原操作；不得为恢复而解除普通 writer 的 pending 拒绝。每个 effect 前后仍作原有 fresh/guard/clock 检查。
3. 只有此 owner 可以创建并登记 final-capture permit，绑定真实 owner/线程/guard、domain、原操作、前驱 grant 和 fence 原件。验证 registry 身份与同一对象，拒绝拷贝、相等 DTO、跨线程、退出后、重入及重复消费。
4. 一次 attempt 在最小真实身份识别后即被消耗；后续校验失败不能使同一 permit 再次可用。新的合法恢复调用必须重新取得实际 owner，复核原永久状态，再发行本次 permit。
5. permit 仅供固定最终捕获接点使用，不授普通 mutation、mirror selection、模型调用或切主权。取消 callback 只可触发取消，不能参与权限判定。
6. 捕获所有十表、历史、原 mutation receipts/响应及实际附件，沿原 fd 核身份、size、SHA 并完成退出检查。含 config、identity、原整库的本地保全包不得直接用作上云传输包。
7. 最终包永久发布并独立读回，捕获资源成功退出后，才可准备和发布 stop。写入 stop 的资源也要实际完成关闭和最终观察，公开成功结果在整个最外 owner 成功退出之后返回。

若 stop 文件已发布但后续退出失败，返回结果未知/需恢复；不能谎报成功或删除该文件。恢复必须重新验证原 fence、包和 stop，不能把文件存在等同于上次执行已经全部成功。

## 5. 每个中断点的恢复规则

| 当前永久事实 | 恢复动作 | 必须保持的拒绝 |
|---|---|---|
| 无占位 | 重新预览并按显式原请求进行 CAS | 不依据过期 preview 自动提交 |
| 占位已有，intent 缺失 | 普通业务持续 pending 暂停；真实 owner 验原占位/前驱/全部 CAS，再发布绑定的原 intent bytes | 不换 UUID/目标，不覆盖占位，不解除 pending 来借普通写权 |
| intent 已有，fence 缺失 | 普通业务持续 pending 暂停；重核原占位/前驱/全部 CAS 与排空条件，再发布原 fence | 仅称暂停、未称 epoch 永久撤销；原事实变化则拒绝 |
| fence 已有，最终包缺失或未完成 | 保持旧主禁写；新 owner 验原链，仅续原操作捕获/发布 | 不清 fence、不回退 legacy、不补普通业务写 |
| 最终包已有，stop 缺失 | 独立核完整包、原 intent/fence、SQL/附件及退出条件，发布原绑定 stop | 不仅凭包摘要跳过实际读取；不改目标 |
| stop 已有，上次结果未知 | 核同原字节及全部前件，完成新的观察/关闭后交付已完成状态 | 不重复停写、不再增 epoch、不推导目标已激活 |
| 原件冲突、缺失链、未知版本/设施 | 报明确冲突或 unknown，保持拒绝写入 | 不自动修复、替换或另造操作 |

占位发布前失败没有本操作的持久暂停事实，但仍必须重新核当前状态；占位发布后、fence 前失败保持 pending 暂停且不自动撤销原占位。恢复资格只能由真实 owner 核原操作与前驱取得，不授普通业务写权。fence 后任何错误或取消均不能解除永久停写。异常时保首错、继续已拥有资源的必要清理，记录真实效果与返回结果的差异。

## 6. 文件级实施顺序

| 批次 | 候选文件/责任 | 具体交付与通过条件 |
|---|---|---|
| A：依赖接点 | H01 live witness/SQL/Store、H05 writer、H02 roots 现有及待建模块 | 列出实际 factory/getter、锁顺序和每个写入口；先接齐占位即生效的 pending 拒绝与原操作专属恢复，再允许发布占位。所有授权均能追到原运行 owner；没有来源的接口不先写伪实现 |
| B：持久操作 | 新 `personal_handoff.py` 的固定操作层及必要的独立版本合同、对应测试 | 前驱 grant 唯一槽兼 pending 事实、原 bytes 恢复、冲突、所有合法前缀；旧 format1 原义不变 |
| C：捕获与包 | `personal_mirror_snapshot.py`、`personal_preservation_source.py` 的独立完整候选接缝；新最终包 producer/contract 以审定名称落地 | 固定 owner 产生 permit；完整个人白名单和附件，原退出核验、无循环引用；普通 capture 原行为保留 |
| D：恢复与关闭 | `personal_handoff.py` 及 owner/permit 内部模块、`tests/test_personal_handoff.py` 等 | 顺序发布、首错与清理、真实 stop 后未知结果恢复；每个中断点均只续原 UUID |
| E：集成 | H02 保全/恢复/GC、H05 全 writer、H06 CLI/API 与文档 | 先真实组合回归，再显式提供 preview/commit/resume；正常启动不选择操作，不实际切换用户主实例 |

B/C/D 可先在新的 data 候选目录准备完整源码和必要现有文件的父本/逆差。不得为了绕开依赖写一个永远返回 unknown 的替代功能，或以注入成功 callback 演示正例。源码候选能编译/被审阅不等于运行功能完成。公开 apply、受控执行与产品启用分别核对各自前件。

## 7. 必要验证

运行前先完成完整候选及两份源码审查，冻结实际输入并采用项目已有隔离运行方式。保留 product30、parent360、matrix1680、native20/25/10、cleanup10、max2 及累计1MiB产品限额，不扩大诊断授权；最终命令随确定的运行入口和选集编写，本设计不提供可误启动半成品的命令。

- 同 grant 两 UUID/两目标竞争，两个进程只有一个原操作永久占位；同操作逐字节恢复，不以测试字典代替真实文件竞争。
- preview 后 revision/SQL/grant/epoch/目标变化拒绝；前驱为 genesis 与 promotion 两种谱系都覆盖。
- 每个 mutation/upload/cleanup/send 在排空、pending 和 fence 阶段的行为；拒绝必须在其实际业务 SQL/staging/effect 之前。单独覆盖“占位/intent 后进程退出、fence 缺失、重启后普通写仍拒绝、原操作可续跑”，并核个人 SQL/revision 未因合法业务写入改变。
- permit 拷贝、伪造、重复消费、跨线程、跨 owner、取消后与退出后使用均拒绝；实际 owner 正例另列。
- 占位/intent/fence/包/stop 各发布前后及每次实际关闭故障；原错误与持久副作用分别断言。
- 原十表/历史/回执字节保持；缺附件、同 SHA 换身份、同身份改内容、未知文件、含 config/identity 的包拒绝。
- 恢复旧 DB/旧包、重启、缺坏 authority 链均不复活旧任期；H02 保全和 GC 保留新永久事实。
- 完整实际 producer 生成 stop 后由 H04 消费者核对；声明/模型测试单列，不代实际停写、安装或新主写权。

目前本文件中的验证项均未运行。r964 的初始化期限错误及 r967 待批准状态保持，不能用 H03 设计完成替代其修复或完整目标。

设计复审 H03-D001 已据上述持久 pending 规则修订：初稿允许占位后、fence 前发生新业务写，会使原 preview CAS 与最终 SQL 永久不符。初稿及该阻断记录保存在 `data/t16-h03-independent-scope-r968`；修订后的独立复核另记，不能把设计修订当运行修复。

## 8. 本轮结论与后续

同一只读代理与 Root 核对表明，H03 是一个真实缺失的完整功能块，可先细化设计与准备候选；当前还不能直接交付运行功能。H04依赖真实 stop 与可信导入，H05依赖现代活授权，H06公开操作依赖H03/H04。永久分享已有预览，发布/撤销及永久凭据设施仍缺失，继续保留原方案 H01–H06 后实施的顺序。

P06 七项历史来源用户没有，不再询问。T15 仍需真实报价/变更，生产云仍缺目标配置。完整目标保持未完成；本轮没有业务源码改动、产品/pytest/原生诊断执行、正常库或服务操作、Git 提交/推送。


### 9. 现代永久事实集合的真实缺口（2026-10-21，r1166）

本段纠正批次A/B的存储前提，不改变旧格式或H03完整目标。固定13份源码297743B的哈希/AST证据及同一只读代理调查确认：现代namespace3根只能含原root seal，底层发布只接受reservation/root-seal/header；现代enrollment只定义五类初始化记录、固定29文件。当前没有现代完整authority事实集合producer/reader可接pending分流。原§2中的personal_authority_files是旧设施接口，不能直接作为现代namespace3的facts运行实现。

r1156的canonical pending封装、domain+前驱grant唯一键及80字符fact id继续保留；80字符只证明旧facts命名约束，不证明现代地址、原文件竞争、占位成功或全writer暂停。live具名引用搜索未找到handoff_pending/pending_fact_id/personal_handoff_contract调用者，这不是对所有动态Python调用的证明。旧status读取和source3 opaque census也不是现代active authority集合消费者。

下一批A须先完成下列实际存储接点，不能先发布占位再补普通writer门：

1. 新增独立明确版本的现代handoff文件合同（拟模块personal_handoff_files_contract.py），联审新role/root和有限阶段集合。保namespace3原封闭root及三类publisher、enrollment原29文件；不把pending喂旧authority DAG parser或在旧协议中静默扩kind。选择既有旧facts设施须有真实该设施owner与关系证明；目前没有这种现代接点，因此不采用旧facts直连。
2. 在实际namespace/Store/活授权接口完整后，实现真实producer/reader owner（拟模块personal_handoff_files.py）。逐次绑定原profile/EX、原请求、前驱grant和真实资源生命周期，定义no-replace竞争、同bytes重试、发布结果未知及原UUID中断恢复。不能先写返回True/unknown的授权替身、注入验证callback，或以caller DTO代替来源。
3. 新名称须逐接H02原字节选择、文件角色/版本包、备份/恢复、GC及别名保护。source3的namespace前缀选择和GC的authority前缀保护只是现有保守基础；GC对旧exact namespace3 root有特殊锁规则，新root可能仍使整组unknown。落入前缀不能记成新role完整支持，更不应为新root随意跳过锁。
4. 在首次占位发布前，补齐H05全mutation/upload/失败清理/send实际暂停点及排空锁顺序。普通writer持续拒绝pending/fence；只读状态与原回执读取无隐含写入；恢复由真实原操作owner继续，不能清pending借普通写权。

原批次B/C/D/E、最终包producer、实际关闭、真实文件竞争/重启后拒写及H04消费等要求全部保持。该存储缺口是运行工程前件，不由新增无人调用的纯helper、重复同一State失败或19项历史合成测试关闭。

本轮仅完成固定源码证据和依赖纠正，未新增业务源码、运行产品/pytest/native/诊断或安装H03。当前安装仍为r1165基底；真实State/request失败未知。正常库/服务/dist/权限/settings/Git未操作，完整Goal active，非仅验收。证据：data/t16-h03-modern-fact-storage-audit-r1166/CURRENT-STATUS-01.md。



### 9.1 独立文件布局合同候选（2026-10-21，r1167）

§9第1项已有纯候选和合成用例通过记录：首轮7绿/1红/20未跑，修测试夹具后仅续跑21项通过，累计2child/29次调用，28个唯一用例均有通过记录。不是一次28项全绿。这里只落实文件布局与原字节关系合同，不关闭第2–4项真实owner、H02/H05接线和首次发布前件。新root、handoff.lock、四前缀、原件局部绑定及全虚拟集合关系检查见本批检查点；未知/stage目录必须拒绝且不清理。

当前source3/GC在新root仍要求旧authority.lock，不兼容新handoff.lock。下一步须设计并实现真实新root owner及独立H02锁/版本包/恢复/GC消费者，不能以假旧锁或跳过锁兼容。实际H01授权与资源生命周期、H05全writer/send暂停排空未闭合前，不发布pending；纯合同unknown输出不代运行功能。原批次B/C/D/E、原UUID续跑、最终包、停写/关闭和H04消费验证全部保持。

这些通过记录仅证明给定合成声明：无磁盘扫描、真实锁竞争、重启拒写、最终包或真实服务证明。本批未安装合同或pending，最近产品安装仍r1165；完整工程目标继续active。详细结果与输入预算：data/t16-h03-files-contract-r1167/CURRENT-STATUS-01.md。


### 9.2 新handoff锁与GC物理保护已安装（2026-10-21，r1168）

§9第3项的GC接点已有真实运行增量：exact handoff根用实际handoff.lock，opaque原件/目录/inode持续保护，缺坏或busy锁继续整组unknown，不用假旧锁或豁免。唯一q1168的19旧+10新用例29/29通过、87phase passed，并已安装三产品+一测试。storage_roots固定目录项静审/pin，本轮无其导入或SQL GC apply/delete证明。

这只关闭GC锁名不兼容这一局部缺口；source3/package3/角色采集及恢复仍未支持新布局。§9.1关于旧锁不兼容的记录保留原时点，最新以本段为准。原pending/文件纯合同未安装，真实owner/H01/H05/首次发布/原UUID恢复/最终包和全部原批次要求不减。完整Goal active，服务未重启。

证据：data/t16-h02-handoff-gc-lock-r1168/CURRENT-STATUS-01.md。


### 9.3 新handoff原字节采集已安装（2026-10-21，r1169）

§9第3项的opaque采集接点已有运行增量：source3对exact新root持真实handoff.lock，复用原流及原预算，26/26与78phase passed后安装2产品+1测试。capsule3原namespace范围和unclassified角色本已能容纳这些字节，layout/metadata2/projection2不扩；坏JSON和未知stage保存不等于业务handoff合同成立。名称别名继续旧锁规则，有效原锁可成功，缺原锁的fixture拒绝。

source3仍没有已安装coordinator3持久消费者，不能认领raw3包/复制/恢复、owner/活授权、首次pending、全writer停写或H03运行交付。§9.1/9.2关于source3旧锁不兼容的记录保留原时点，采集锁局部缺口最新以本段为准，其余打包/恢复前件保持。未来raw3须在r1168 GC和r1169源基底上重基；base.close双故障first窗口另修。完整Goal active，服务未重启。

证据：data/t16-h02-handoff-raw-capture-r1169/CURRENT-STATUS-01.md。


### 9.4 共享raw source退出首错修复（2026-10-21，r1170）

§9.3所列base.close缺口已局部修复：stream校验与close独立记错、目录raw退出运输、可选note/mapping失败不阻断余下关闭尝试，资源先detach且finally收尾。单child共29Fake清理案+1实际handoff成功采集案通过后安装1产品+1测试；不是30个native案，_ExistingLock内部优先级和native故障释放不由此证明。

source3原锁/原件、wire/role和GC overlay保持，未来持久raw3在本基底重基。完整raw包/恢复、真实owner/H01授权/H05全writer排空/首次pending/原UUID与最终包仍未完成；完整Goal active，服务未重启。

证据：data/t16-source-close-first-error-r1170/CURRENT-STATUS-01.md。


### 9.5 下层锁退出首错修复（2026-10-21，r1171）

§9.4保留的_ExistingLock内部首错窗口已作有限修复：保原验证门，解锁错不再被后close错覆盖，close前detach且逐项尝试，optional note安全延后；__enter__原错误运输避免自定义traceback lookup阻断清理。28Fake+2实际成功案通过后，仅安装1产品+1测试；不证明native故障释放、unlock任意重入或_open_lock未知fd转交域。

GC/raw source/wire/原锁名规则保持，未来持久raw3须在r1170及本锁基底重基。H02完整raw包/复制/恢复、H01实际授权、H03实际owner/首次pending/原UUID及H05全writer/send排空仍待。服务未重启，完整Goal active。

证据：data/t16-existing-lock-first-error-r1171/CURRENT-STATUS-01.md。


### 9.6 完整持久raw3组合重基（2026-10-21，r1172）

H02前件的9产品+2测试已三方重基到r1171实际基底，保handoff.lock/GC algorithm4/HANDOFF保护及r1170/1171清理修复；11候选264457B，仍未安装或执行。静态合成不是完整包/复制/续作/恢复交付。首次request无静态唯一失败因，不猜改native或重放未变失败。

共享Target清理叶及外层scope有独立first/关闭尝试缺口，后继先按真实资源合同修复，再核完整最终组合。实际H01、H03 owner/pending/原UUID、H05全writer/send排空及最终包未完成；Goal active，服务未重启。

证据：data/t16-h02-package3-rebase-r1172/CURRENT-STATUS-01.md。


### 9.7 Target清理候选及夹具失败记录（2026-10-21，r1173）

§9.6共享Target清理和base/target2/target3 scope完整候选已双审，未安装。唯一普通child collected30，实际9call=8Fake通过/1夹具顺序失败，21未跑；修订04仅改夹具未执行，四产品字节不变。两实际Python stream案未运行，不能认领运行交付。

本矩阵显式准备已用1704525B、余392627B，不足完整worker1640169B；不重置预算/q或重放全套。本矩阵不再运行/安装，live仍r1171；后继保留完整真实范围和原预算。failure-record writer、首次request因、完整H02/H01/H03 owner/pending/原UUID及H05排空/最终包仍待。Goal active，服务未重启。

证据：data/t16-target-close-first-error-r1173/CURRENT-STATUS-01.md。


### 9.8 失败记录清理修复（2026-10-21，r1174）

已安装四现有writer/coordinator调用者+私有helper及测试；三个版本完整候选均有实际接线，版本3仍staged。保actual budget.first、原stream→逆序owned退出、原closer/body传播；受保护builtin附注不覆盖coordinator稳定failure。tb仅descriptor运输，无链/frames/locals/notes观察。

唯一新child21/21=19Fake+2Python stream、63phase；三组完整authority caller未运行。沿旧账累计30call/2child、1990365B；旧夹具失败1及原21未跑保持，无再次执行额度。r1173 IO/_scope/04仍未装，live为r1171+r1174。原发布因/完整包及H01/H03/H05仍待，服务未重启，Goal active。

证据：data/t16-failure-record-cleanup-r1174/CURRENT-STATUS-02.md。


### 9.9 完整持久包与清理源码合成（2026-10-21，r1175）

r1172/r1173/r1174已合成15产品4测试完整19文件384921B，未安装/运行。三Target双顺序合并和完整函数文本逆像相同，IO仅换清理函数；保handoff锁、GC/根保护及当前Source/ExistingLock/State。27历史输入/11live before/6引用及18旧seal1307文件核同，四个现有保持项不退回旧源码。

原矩阵30call2child1990365B、原截止及失败/未跑事实不变；无新增执行额度或准入。live仍r1171+r1174，首次发布因/完整包/恢复及H01/H03/H05仍待，服务未重启，Goal active。

证据：data/t16-h02-integrated-cleanup-rebase-r1175/CURRENT-STATUS-01.md。


### 9.10 Python源码依赖集合与harness分列（2026-10-21，r1176）

从r1175十九入口递归得到230份Python源码6049769B：明示import闭包229，默认conftest比较230；补scripts namespace、默认importer及find_spec语法，7dict属性与114regex不误报。4905为展开边，136为宽匹配语法行，不是实际加载次数。资源/第三方/native/实际runtime及harness选择仍未闭，当前无产品运行/安装准入。

保r1175完整候选/旧1409封存/159/55+State18及r1174六已装；routing原raw1/2的COMPAT也须联审。原30call2child1990365B和已过截止不变，原发布因/完整包及H01/H03/H05未完，live仍r1171+r1174，Goal active。

证据：data/t16-h02-source-dependency-closure-r1176/CURRENT-STATUS-01.md。


### 9.11 本地资源候选与OpenCC文件链（2026-10-21，r1177）

r1177保230源并核49本地候选1190620B：仓库8资源、2声明及OpenCC完整39文件。RECORD35条hash/size同，3pyc与RECORD无发行hash另存字节；不执行/解码pyc、不认领实际缓存或模块绑定。t2s候选仅conversion_chain消费两词典，segmentation未消费。

43宽语法分7已识别候选资源/1未读配置/2未执行证书key/33其它，非完整资源图。私密配置、环境及证书key未读，提示词实际版本、其它三方/native/实际运行仍未闭。旧20seals1692/159/55+State18/r1174六/r1175十九11before8缺保持；live仍r1171+r1174，无运行安装准入，原额度和已过截止不变，Goal active。

证据：data/t16-h02-resource-dependency-r1177/CURRENT-STATUS-01.md。


### 9.12 实际库入口与回归证据边界（2026-10-21，r1178）

8公开库入口分raw1两条/raw2三条/raw3三条；*_locked只要求已有EX，实际获取在lifecycle原scope链。候选routing联审三版，不能省raw1/2 COMPAT；live和candidate的source3坐标分开。原no-owner shared gate可早退完整integrity，cache/preflight仍核，不冒认每次完整integrity。

四candidate suite38函数/74静态组合；12既有源码273函数/734已知组合+4未解析函数，不是完整COMPAT或实际collection。279证据7240389B，去重增加11compat源后290/7510229B，非runtime manifest。旧runner overlay不证明source适配；不造owner/State/stub或挑helper代全链。旧21seals1768/159/55+State18/r1174六/r1175十九11before8缺保持，live仍r1171+r1174，无运行安装准入，原额度及已过截止同。原发布因及H01/H03/H05等未完，Goal active。

证据：data/t16-h02-entry-regression-scope-r1178/CURRENT-STATUS-01.md。

### 9.13 最终包编解码与实际文件reader候选（2026-10-23，r1263/r1264）

§6 C批已新增具体源码，仍未安装或运行。r1263 source-02含最终包codec/builder与测试夹具三源41180B；r1264 source-01原字节继承该三源，再加 `personal_handoff_bundle_reader.py` 与专测两源20628B，完整五源61808B。Root与同一r924的完整源码审查均未发现确定功能阻断；这不是语法、测试、真实文件/退出或完整H03完成证明。r1263补充的六种容量负例也仅完成测试源码覆盖。

最终包独立magic/version/u64 framing，保原snapshot和十表/meta/history/receipt字符串，严格当前fence祖先闭包，允许promotion所需历史stop，禁止本次stop/后继/无关记录。原typed SQL摘要与完整manifest/body长度和SHA在包内重核；meta schema hash仍是独立捕获声明，未来必须来自同次真实DB。没有新持久目录、文件名或第五种handoff文件role，namespace3/四前缀/raw3旧合同未扩字段。

新reader直接读取显式选择的实际包路径，复用普通no-follow目录/文件primitive和原codec检查；仅收header/snapshot，body每次最多128KiB，整包两遍流式SHA、精确EOF及path/fstat各自签名核同。入场还核path/fstat的device/inode/size/mtime关联，ctime不跨API等同。stream与成功进入的directory在返回前按顺序退出，body或首close错误优先于后续取消。它只返回无句柄的已完成读取事实和偏移；不得用该事实代后续活文件body来源、安装权、停写或final permit。两个顶层对象不代Directory内部多HANDLE及open_file/os.fdopen partial的原责任。

后继仍须实际final owner/permit/capture、同次meta schema来源、held-fd持续消费及退出、H02新role/版本包/锁/no-replace/独立读回/保全恢复GC、H05全mutation/upload/失败清理/send的pending暂停与排空、stop外部引用/真实关闭/未知恢复及H04/H06。包文件reader是其中的必要消费者，不是最终捕获producer；不得退回caller chunks、普通SH快照或授权callback来伪造正例。正常source-gate及完整运行依赖/新合法选集未闭，旧30call/2child/1990365B、截止和r920/r964耗尽不重置。

本两批未调用候选、测试、AST/compile/import/collection/native/诊断，未安装五条live目标，未操作正常库、服务、dist或既有权限/settings/Git。用户A、P06无历史原件不再询问及完整H01–H06/永久分享撤销/条件T15/生产云范围保持，Goal active/incomplete，非仅剩验收。

证据：data/t16-h03-final-bundle-candidate-r1263/CURRENT-STATUS-01.md；data/t16-h03-final-bundle-reader-r1264/CURRENT-STATUS-01.md。后继不得重复编写这两个已准备候选；继续按r1264/NEXT-01列出的真实接点推进。

### 9.14 原快照持有资源接缝候选（2026-10-23，r1265）

§9.13之后已准备r1265 source-03完整候选两源26932 B：原 `personal_mirror_snapshot` 提取普通class管理的私有文件窗口，由原 `capture_snapshot` 真实消费；专测源码涵盖原stream窗口、SH/EX、退出、原public、meta投影和取消。原image仍由外层持有，node及ready行关联的原HeldFile在窗口内持续存活，原public仍在原文件退出后序列化observation、最外退出后返回闭窗事实。source01的新增generator/PEP479风险及source02的提前序列化均已改入03，旧版不采用；无安装/运行证明。

这是资源体接缝，不是final owner、permit或最终捕获producer。holder可构造/可写，active只描述本地窗口；不能据此授信来源、任意后继consumer或完整native关闭。普通SH参数、准入、投影和meta读取保原行为；同次实际meta descriptor及SQL预算留给真正新消费点，未强加共同路径。原Directory/open_file partial、原ExitStack清理优先级及H01来源/COMPAT义务保持。

后继复用r1263/r1264/r1265已有候选，继续真实EX owner/一次性许可、同image元数据、原held stream流式producer与fresh检查、H02发布/恢复GC、H05全写入口排空、stop未知恢复及H04/H06。仍不得用caller chunks、关闭后重开、普通SH或授权callback代这些前件。证据：data/t16-h03-held-snapshot-seam-r1265/CURRENT-STATUS-01.md及NEXT-01.md；Goal active/incomplete，非仅剩验收。

### 9.15 两代handoff库存与最终包联合合同候选（2026-10-23，r1266）

最终包地址已落实为独立handoff-v2根、同slot固定 `final.bundle`，与原四事实共用 `handoff.lock`；v1 marker/四前缀和原件不改。r1266 source-02完整七源80194 B，新增files2合同与专测两源23712 B，另五源原字节继承。Root和同一r924源码复审未发现确定阻断；原01缺参断言期待错误已改入02，所有源码/小帽用例均未安装或运行。

新检查入口强制输入v1/v2两代库存，v1声明缺席不证物理缺席；跨代同前驱槽先拒绝，两代未公开pending intents联合校验operation/promotion UUID与前驱唯一性。五种合法前缀为pending→intent→fence→bundle→stop；完整supplied binary由原codec验证并关联同slot原fence/intent及stop四项final摘要，不以声明SHA代实际字节校验。控制JSON/路径和唯一DAG occurrence联合收费，binary另有界；这是纯全bytes合同，不是4GiB流式producer或物理来源/发布证明。

后继须接精确v2原锁/GC与独立H02 capsule4/projection3/raw-package4及metadata/closed/route/target族；现有source3/GC只特判精确v1 root，census3的unclassified保全不等于新role支持。之后在真实EX owner/一次性许可下接r1265活source、同image元数据、流式写包/独立读回，补H05全writer排空及stop未知恢复/H04/H06。复用既有候选，不重做v1锁修复或另加bundle根；完整Goal active/incomplete，非仅验收。证据：data/t16-h03-final-bundle-layout-r1266/CURRENT-STATUS-01.md及NEXT-01.md。
