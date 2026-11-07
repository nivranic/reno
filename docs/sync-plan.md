# 云端服务 + 本地推理 同步方案（v2，2026-09-29）

> **2026-10-01 实施状态更新**：Phase 0、S01–S07、P01–P04工程完成；P05 R07已加载本机，N01–N03已完成冻结Linux3012 passed /40 skipped（711.18秒）。N04独立退休/本地映射代次与N05公开CLI已接源码，专项与独立审查收尾中；成功push永久published事实及本地ack恢复仍在接线，上一批全量结果不证明本批。本批没有真实库维护、发送状态升级、弃用、GC或服务重载；现用R07最后实测知识/附件/配置/身份及同步关闭未配对状态见§13.31，真实根完整性、旧路径/备份依赖未知保持。P05完整收尾、P06预算、T15/T16与生产部署继续推进；当前合同§13.33、操作部署说明5.14、主方案17.29，个人域保持§12单主契约。

> 目标：coding plan 配额的 LLM 推理只从家庭 IP 出（atomize/judge/报告总结），
> 云服务器只跑纯服务面（浏览/搜索/问答/播放/决策），视频地址定时下推本地处理、成果回推云端。
> 本文是评审后定稿：四视角对抗评审 63 条发现（29 条必办）已全部合入，
> 三个悬而未决项已裁决（§9）；完整评审证据见 workflow 产物「云本地同步方案评审结论」。

## 0. 为什么不能"直接同步"——本方案存在的理由

评审证实的四个硬事实（均有 path:line 证据，摘录）：

1. **云端现在根本不是纯服务面**：serve 启动即派 worker 线程排空队列
   （`reno/cli.py:126-127`），`/api/import` 在请求内同步 yt-dlp 下载并起流水线线程
   （`app/api.py:167-175`）——云端一有任务就从数据中心 IP 下载+推理，正是要规避的封号路径。
2. **表级写域分区会被决策路径击穿**：`/api/decision` 直写 `conflict_case.status`
   （`app/api.py:236-238`）且 `add_decision` 副作用改 `knowledge_atom.status`
   （`reno/db.py:295-298`）——"决策"天然跨端跨表。
3. **存量数据带毒上云**：26/28 行 `files_json` 存 Windows 反斜杠路径、`frame.path`
   存 `F:\` 绝对路径（实测），原样进云后 `/api/video`、`/api/frame` 全断。
4. **`docs/generated`（报告落点）被 git 跟踪**：云端 `deploy.sh` 更新走
   `git reset --hard` 会抹掉同步上去的报告；脏树时的 die 提示引导 wipe 目录，
   而默认 DATA_DIR 在 DEST 内，会连带删掉云端权威数据。

因此方案的核心不是"写个同步脚本"，而是先给系统装上**角色开关**和**所有权契约**。

## 1. 角色与总架构

新增配置键 `node_role: cloud | home | all`（默认 all=现状单机，完全向后兼容）：

| | role=cloud（云服务器） | role=home（家庭机） |
|---|---|---|
| serve | ✅ 但**不起** worker 线程 | ✅ 全功能 |
| /api/import(-batch) | ✅ 但**只入队**（存 URL，不下载） | ✅ 本地直接处理 |
| 流水线/judge/report CLI | ❌ 拒绝执行 | ✅ |
| llm 层 | **硬拒 anthropic 路由**（glm-5.3* 一律配置错误，不发请求） | ✅ coding plan |
| sync agent | ❌ | ✅ 定时外呼云端 |
| ask | paas 便宜模型（§6） | 任意 |

- `deploy/deploy.sh` 加 `--role cloud`：强制 role、默认**不写** api key（配了反而告警）、
  走 nginx+TLS（§7）。docs/deploy.md 同步改写"processing pipeline 需要 key"的旧表述。
- 连接方向维持草案 D1：**本地定时外呼云端 HTTPS**（家庭无公网 IP、不开端口），
  调度间隔加 ±30% 抖动（防等间隔指纹）。

## 2. 数据所有权契约（表级清单 + 列级例外 + 派生）

原则升级：**表级分区 → 列级所有权**；status 类字段一律从"决策事实"派生，不参与同步覆盖。

| 类别 | 表/列 | 说明 |
|---|---|---|
| 本地权威·随闭包推送 | `video_asset`、`knowledge_atom`（**除 status**）、`evidence_ref`、`transcript_segment`、`ocr_span`、`frame`（**path 列导出时置空**，云端不消费）、`visual_observation`、`conflict_case`（**除 status**）、`knowledge_cluster`、`meta` 中 `step:<vid>:*` 键 | 实际表名为 `knowledge_cluster`；缺少 evidence_ref 会断开引用证据链 |
| 事实·双向合并（§4.4） | `user_decision`（加幂等键列） | 冲突决策+原子级 confirm/reject 都是事实行 |
| 云端权威·回传拉取 | 队列表（§4.1） | 用户在云端提交的 URL |
| 派生·不同步·落库后重建 | `atom_fts`（云端重建）、`conflict_case.status`、`knowledge_atom.status`（两端各自跑同一回放器，§4.4） | 从 user_decision 确定性派生 |
| 运维·两端各自记录 | `processing_run`、`meta` 全局键（last_judge、backfill 游标）、sync 游标 | 同步游标存 `RENO_DATA_DIR/sync-state.json` **不进 reno.db**——评审证实 meta 游标会被 bak-judge 全库快照回滚连带摧毁 |

同步端点落库的硬规则（安全门约束的完整版）：每张可同步表在服务端维护
**表名+列名+主键三重白名单常量**，payload 键不在白名单整批拒绝；值全部 SQL 参数绑定
（表/列名无法参数绑定，只能白名单）。

## 3. 上云前置改造（Phase 0，均有独立价值，不依赖同步功能上线）

1. `node_role` 开关 + llm 路由守卫（§1）。
2. **files_json 规范化迁移**：存量 26/28 反斜杠路径统一为 POSIX 相对路径；
   同时修 `app/api.py:45,75` 的解析基准 `ROOT / files["video"]` → 按 `config.MEDIA`
   基准（消除 `--data-dir` 分叉）。这是媒体上云能用的硬前提。
3. **报告落点迁移**：`OUT_DIR` 从 `repo/docs/generated` → `RENO_DATA_DIR/generated`，
   `/api/reports` 改读数据目录；`docs/generated` 移出 git 跟踪；
   `deploy.sh` 修 die 提示（绝不允许引导删除含 DATA_DIR 的目录）。
4. `deploy.sh --import-data` 白名单只收 `reno.db`（评审证实现在会把
   `dy_cookies.txt` 抖音登录态与 `reno.db.bak-*` 一并放上公网）；docs/deploy.md
   打包命令改为显式列举。
5. token_gate：`hmac.compare_digest`；`/api/sync/*` 分支验 sync_token（§7）。
6. /ask：无 key/调用失败**显式降级**为纯检索结果（现在直接 500，`reno/llm.py:136-138`），
   降级答案缓存键加 `degraded` 标记防污染 LRU；对外错误统一脱敏文案；
   按令牌限速（如 10 次/分钟）。
7. nginx `client_max_body_size`：评审实测 29 个存量原片 11 个超 10MB（最大 149MB），
   `/api/sync/` 独立 location 放宽（如 512m）+ `limit_req`。

## 4. 同步协议

### 4.1 队列（新表，不复用现有 job）

评审证实 job 表不能直接当 URL 队列：无 url 列、claim 不分 kind、serve 启动把
running 重置 pending（会二次拾走本地在处理的任务）、存量还有代码不认识的
`cancelled` 47 行。新建 `sync_queue`：

```
item_id / url / status(queued|claimed|done|retry_wait|dead) /
attempts / lease_until / last_error_class / created_at / updated_at
```

- 云端 `/api/import` 在 role=cloud 时只 INSERT 此表；claim 用
  `UPDATE ... WHERE status='queued' AND item_id=?` 原子抢占（修掉现有
  先 SELECT 后 UPDATE 的双认领，`reno/db.py:322-330`）。
- **租约**：claimed 带 `lease_until`，本地心跳自动续期；心跳消失超 N 分钟（建议 10）
  → 回 queued、attempts+1；`retry_wait` 用于可重试失败（网络/429/配额窗口）延迟重排；
  不可重试失败（源下线/格式错误）→ `dead` + 状态回写云端 UI 可见（死信原因分类展示）。
  评审证实现有 failed 是终态无出路、同 URL 重提交因 sha 去重直接 duplicate。
- **URL 域白名单**（B站/抖音域），本地执行前再校验一次：防云端被入侵后把内网地址
  塞进队列让家庭机当 SSRF 探针；失败 stderr 回传前脱敏（去域名/IP/路径）。

### 4.2 同步单元与顺序（每周期固定编排）

```
① heartbeat（含 cookie 有效性/磁盘水位/队列深度）
② pull decisions（云端 user_decision 增量 → 本地合并+回放，§4.4）
③ pull queue（claim 任务 → 本地完整流水线：下载/ASR/OCR/atomize/judge/报告）
④ push closures（新完成视频：media → db 行 → ready，§4.3）
⑤ push globals（conflict/cluster 全集 + 报告文件，前序=②已回放）
```

固定顺序消除循环依赖；conflict/cluster 推送的**硬前序**是"先拉决策并本地回放"，
云端替换落库后**服务端再回放一次**（对现存 conflict_case 重放 user_decision 的
last-wins UPDATE），使云端即刻自洽、不依赖下一轮——评审证实缺这一步会出现
"已裁决查无此决策"的漂移窗口。

### 4.3 闭包推送与可见性

- **闭包**=该 video_id 的全部本地权威行（§2 表清单）。同步语义按表区分：
  步骤输出表（transcript/ocr/frame/visual/evidence_ref）用 **video 作用域 delete+insert**
  （与本地 replace-* 语义一致，天然处理"消失行/幽灵行"；评审证实 AUTOINCREMENT
  主键两端不对齐，行级 upsert 不可行）；`knowledge_atom` 等内容稳定主键表用逻辑键 upsert。
- **媒体先行两阶段**：originals 上传（sha256 校验、服务端临时区与 media 同文件系统、
  原子 rename）→ db 行落库（WAL+busy_timeout+短事务分批）→ **服务端**校验
  （最终路径存在+sha 复核）通过才置 `sync_state=ready`。
- **ready 门禁全端点化**：评审证实只过滤视频列表页不够——`/api/search`、`/ask`、
  `/api/compare`、`/api/conflicts`、stats 全部只统计 ready 视频，杜绝半同步数据
  漏进检索面。
- **audio/frames 不上云**（评审确认 /api/frame 与流媒体只消费 originals 实时抽帧，
  app/ 无 audio 消费者）：明文固化此结论 + 每周期自检 originals 完整性
  （评审实测存在无 db 行的孤儿目录，以 db 为准、不按目录枚举）。
- 上传并发=1（不占满家宽）；单文件最大 149MB 实测，整文件重传可接受，
  断点分块留后续优化。

### 4.4 决策双向合并与回放器（悬决 Q1 裁决：双向合并）

两端都会用决策（在家翻本地、出门用手机翻云端），单决策面不成立。设计：

- 当前显式迁移已新增 `event_id / client_id / decided_at / target_fingerprint / target_snapshot_json / event_schema_version`。新增事件使用 UUID、UTC 毫秒时间；同目标按 `(decided_at,event_id)` 确定顺序，再核对目标指纹。相同 ID 内容改变、目标消失或旧事件没有可信指纹时，保留历史并进入复看，不自动作用于新内容。真实的跨节点事件合并仍属于 Phase 1，不能把本地回放测试视为双实例验收。
- **回放器**（确定性、两端同构、与 judge 互斥）：输入=合并后的 user_decision 全集，
  输出=`conflict_case.status` + `knowledge_atom.status` 的 UPDATE。从此
  **status 不再是同步对象，而是派生值**——两端口径天然收敛，atomize 重跑/推送覆盖
  都不再能静默抹掉用户决策（评审证实的"重跑重置 candidate、下次推送抹掉云端复核"根治）。
- 首次引导：先拉云端全部决策播种本地（现网已有 1 行决策，无播种即丢），再首推。

### 4.5 快照与回滚（悬决 Q2 裁决：批次快照 + stale 标记）

- 评审证实按 video 回滚与全局表互相悬空（cfl_/clu_ 内嵌 atom id）。回滚单元改为
  **推送批次**：云端每次接受推送前，把受影响行集+全局表打成一个 sqlite 快照文件，
  保留 N 代（沿用 snapshot 实现但按批次、有代数上限防线性堆积）。
- 恢复后按 cfl_/clu_ 生成时的原子依据重新派生有效性：同代原子与关系完整匹配为
  `active`；内容变化为 `stale`，成员缺失为 `missing`，旧记录无可信绑定为 `unknown`。
  后三类保留原分析并提示更新；**re-judge 不自动触发**，用户明确点击“更新争议”
  才在有执行能力的本机/家庭节点重判，再经完整同步发布。具体合同见 §13.16。

### 4.6 schema 握手（修掉假安全）

当前已有 `migration_version=5` 和显式 `init-db / migrate-db`，普通连接只做结构校验，旧的隐式破坏性迁移已移除。v4 为决策卡添加显式空间与阶段分类，旧卡片保留未指定；v5 增加同步配对、会话、队列租约与发布绑定结构，保留旧数据。S01 握手模块已比较迁移版本、核心表结构指纹和协议版本，不一致即拒绝；网络端点接入与双实例互通仍待后续批次，迁移入口本身不代表同步产品已经可用。

## 5. atom id 策略（悬决 Q3 裁决：保留索引式 + 失效对账）

评审证实 atom id 是位置序号（`reno/atomize.py:144`），atomize 重跑全体移位 →
cfl_/clu_ 换号 → 云端决策孤儿。裁决：**不改内容哈希**（牵动 judge/决策/全量迁移，
成本大收益窄），改为：

- 文档明确"重跑 atomize 是破坏性操作"（现状重跑本来就要清旧原子）；
- 回放器加对账：回放时发现 decision 指向的 cfl_/atom 不存在于当前集合 →
  标记孤儿决策 + 对账报告（UI 列出"这些决策因知识重算已失效，建议重判"）；
- 方案文档把 clu_/cfl_ 稳定性表述收窄为"**原子集不变时稳定**"（评审证实
  新增视频场景 conflict_id 仍稳定、cluster 成员集变化才换号）。

## 6. LLM 与模型路由（云端）

- 云端 config：`ask_model` 指向 paas 便宜模型（非 glm-5.3* 前缀自动走 paas v4）；
  `ask_model`/`summary_model` 补进 DEFAULTS（评审证实现在缺省回落
  atomize_model=GLM-5.3-Flash → anthropic 路由 → 云端 paas key 401 → /ask 500）。
- **路由守卫**（role=cloud）：llm 层对 glm-5.3* 一律抛配置错误、不发请求——
  即便运维误贴 plan key 上云也打不出去；模型切换器三预设全 glm-5.3*，
  role=cloud 时该端点 403。
- 报告 LLM 总结（summary.md）本地生成随同步上云（落点已迁移到数据目录，§3.3）。
- 当前缓存键包含知识库身份/epoch/revision、模型与实际条件签名；降级回答不进入成功缓存。双实例恢复时仍需推进 library_epoch 并验证缓存隔离。

## 7. 安全

- **sync_token**：独立于 auth_token，仅 `X-Sync-Token` 头（禁 query，防落 nginx 日志），
  `hmac.compare_digest` 比较，支持无停机轮换（新旧并存窗口）；token_gate 对
  `/api/sync/*` 改验 sync_token（评审证实现状会被全局门拦成 401，最小权限落空）。
- **TLS 机制化**：deploy.sh 加 `--tls-domain`（自动 nginx+certbot、reno 改绑
  127.0.0.1、ufw 关 8765）；sync 端点注册时校验前端是 https，否则拒绝启用。
- **上传端点四条硬规则**：video_id 白名单校验；服务端从 video_id 重建目标路径
  （拒绝请求携带任何路径成分）；单文件上限+临时区总量配额；入库前对 files_json/
  frame.path 规范化并断言 resolve 后落在 media/ 内。
- 敏感数据面最小化（悬决外新增采纳）：dy_cookies 永不上云（§3.4 白名单）；
  云端同步内容本身是观看史画像——接受此模型（私人服务器+令牌门+TLS），
  方案文档明示泄露面。

## 8. 风控（封号/平台两侧）

LLM 侧（家庭机，防 plan 风控指纹）：
- 进程内全局 `Semaphore(1)` 流水线互斥（评审证实现有三个运行方可双跑双烧配额：
  serve worker / import 线程 / sync agent——互斥锁同时根治双跑互踩写库）；
- 请求补常规 UA 与版本头（现在裸 Python-urllib）；max_tokens 按需而非常量 128000；
  退避加随机抖动；批间与调度间隔 ±30% 抖动。

平台侧（抖音/B站账号）：
- 下载间隔每视频随机 20–60s + 单日配额；import-batch 200 上限在同步场景收紧；
  cookie 有效性随心跳上报（现在 health 只查文件存在）。

## 9. 悬而未决裁决记录

| # | 问题 | 裁决 | 理由 |
|---|---|---|---|
| Q1 | 决策面：单决策面 vs 双向合并 | **双向合并 + 回放器派生 status** | 两端都会用；把 status 变派生值后合并语义退化为"事实行去重收敛"，复杂度可控且根治静默覆盖 |
| Q2 | 回滚后冲突集：stale vs re-judge | **批次快照 + stale 标记，re-judge 手动** | 回滚是罕见恢复操作不应自动烧配额；stale+UI 提示已消除误导 |
| Q3 | atom id：内容哈希 vs 索引式 | **索引式 + 失效对账** | 重跑本就是破坏性操作；对账报告覆盖 harm；内容哈希迁移成本不成比例 |

## 10. 实施路线图

- **Phase 0（上云前置，独立价值）**：§3 全部七项。验收：pytest 全绿 + role=cloud
  冒烟（import 只入队、llm 守卫生效、报告读数据目录）。
- **Phase 1（同步核心）**：sync_queue+租约心跳 / sync API 四端点（pull/push/media/
  heartbeat，三重白名单+sha256+原子 rename）/ 闭包导出导入（delete+insert+fts 重建+
  服务端回放）/ 决策幂等键迁移+双向合并+回放器 / ready 全端点门禁 / 状态机 UI
  （云端采集页显示队列与本地同步器在线状态）。
  验收：WSL 双实例端到端（模拟云+本地）：提交 URL→本地处理→云端可看可播；
  断网/杀进程重放缓存注入测试。
- **Phase 2（加固）**：批次快照回滚+stale / 孤儿决策对账报告 / 磁盘回收
  （帧缓存 LRU、快照代数上限）/ 风控 pacing 家族（UA/抖动/日配额/域白名单/脱敏）。
  验收：回滚演练 + 重跑 atomize 后对账报告正确。

## 11. 验证策略

- 单元/集成：同步语义 pytest（幂等重放、断点重推、决策合并收敛、回放器确定性、
  白名单拒绝、域白名单、脱敏）；用当前完整测试集检查回归，用例数量以本次执行结果为准。
- 端到端：WSL 双实例（A=cloud role，B=home role），覆盖：正常链路、中途断网重试、
  云端决策→本地回放、本地重跑→云端对账、源下线死信。
- 诚实边界：真机家庭网络上传带宽、平台风控真实表现、plan 封禁阈值——
  均无法在测试环境验证，上线后靠心跳观测指标回看。

## 12. 个人域单主与恢复边界（2026-09-30）

知识复核与个人选择是不同事实：`user_decision` 的 confirm/reject 只影响知识复核；`decision_card.state=adopted` 不写回知识状态。个人域首版不支持两端离线编辑。

| 数据 | 权威与传输规则 |
|---|---|
| `renovation_project`、`decision_card`、`source_record`、`checklist_instance`、`checklist_item`、`checklist_check` | 当前个人主实例写；未来只允许向副本单向镜像，知识闭包不得覆盖 |
| `personal_evidence_ref`、`personal_revision`、`mutation_receipt` | 必须与个人实体一起备份/切主；旧快照和保存回执不可省略 |
| `source_attachment` 和 `DATA/personal-attachments/` | 独立文件清单，文件先完成 SHA 与 ready 校验，引用后可见；附件不能原位替换 |
| `personal_primary_id`、`primary_epoch` | 数据库内声明当前主和任期；请求必须带 epoch，不能靠 `node_role` 推断主身份 |
| `DATA/node-identity.json` | 本节点身份，不随数据库复制；复制库的新节点默认不得写原主的个人数据 |
| 引用的 active/stale/missing/legacy_unbound、清单完成度 | 依据当前目标、当前项版本和不可变检查事实派生，不作为远端任意覆盖的状态 |

全新 all/home 初始化一次默认项目“我的装修”；`personal_features_enabled`、`field_checks_enabled` 默认 false。cloud 可按12.4显式开放已安装个人镜像的只读入口，`personal_cloud_read_enabled`默认 false，所有个人写入仍拒绝；主实例切换尚未实现。共享家庭令牌不提供家庭成员身份审计。

写入统一执行：主身份/能力/epoch校验 → 查 operation_id 回执 → 对比规范化请求 hash → base_revision CAS → 校验同项目引用 → 写历史与回执 → 同事务提交。已提交操作重试先返回原回执，不因为对象后来改版而报错；相同 operation_id 不同请求拒绝。主任期变更后旧请求不得重放，不能自动换 ID 或带令牌重定向。

现有来源补录只保存人工字段和 JPEG/PNG/PDF；不抓取网页、不解析 PDF、不自动认定标准现行。`validity=current` 表示有日期和说明的用户核对记录。来源正文、有效性或附件变化后，旧事实仍保留，当下引用和完成度重新评估。

### 12.1 后续切主的必要顺序

1. 确认旧主停写且无运行上传/任务；网络分区或无法确认停写时不晋升第二个主。
2. SQLite backup 与 ready 附件 SHA 清单一并取得，传输全部个人表、历史、回执和文件。
3. 新节点保留自己的 node identity，核对行数、外键和附件内容；缺文件/损坏不可开放写入。
4. 在明确维护事务中变更 personal_primary_id 并增加 primary_epoch；旧主必须保持拒写，不能只在 UI 隐藏按钮。
5. 新主开放后，旧 epoch 请求必须返回 PRIMARY_CHANGED；未提交草稿先获取新版本，再由用户明确操作。

上述为待实现/待验收流程，不存在可直接运行的自动切主或双向个人同步命令。普通 `--import-data` 已拒绝已有数据库的整库覆盖；旧 judge 全库快照也不是个人数据安全恢复入口。

### 12.2 尚待真实验收

- [ ] 知识 Phase 1 两实例：URL 入队、家庭处理、媒体与闭包 ready、云端可看可播。
- [ ] 个人只读镜像所有写 API 均拒绝；断网重传不重复实体或检查事件。
- [ ] 附件断点/中断及重新上传后，只有完整文件可以 ready 和被引用。
- [ ] 确认旧主停止后切主，旧 epoch 与旧主请求均不能写，历史与回执完整。
- [ ] 数据库＋附件真实恢复演练，缺文件失败可见，节点身份不被知识恢复覆盖。
- [ ] 用户自己的 5 个待决问题和一项现场工序试用；人工样本桩不替代真实模型、真实资料或真实手机。

### 12.3 T16 显式保全与个人镜像操作入口（2026-10-04）

个人保全公开入口为 `personal-preservation`，默认仅查询记录。指定 `--operation-id` 查询一项，不指定时返回有界列表；记录中的 `phase=ready` 仍为 `verified=false`，只有单独 `--verify` 完成原包全部读取与退出核验后才返回 `verified=true`。目录缺失不会初始化设施；孤立包目录、坏记录或未知条目不能被当作空列表。

创建使用 `personal-preservation --create --apply --operation-id <新UUID>`；原操作续跑使用 `personal-preservation --resume --apply --operation-id <原UUID> --expected-request-sha256 <原摘要>`。实际 worker 锁忙时拒绝创建/续跑；先停止原写者，入口取得实际 worker 锁后再取原 profile EX。`--max-files`、`--max-bytes`、`--max-seconds` 仅新创建可指定，续跑保持原 request 的 limits。该独立包保留原个人事实、原 SQL/WAL、个人附件及完整镜像历史；仍无恢复、切主或发送权限，也不声明知识恢复依赖完整。

个人镜像发送先要求既有配对/State/租约、原主身份及显式 `personal_mirror_enabled=true`。`personal-mirror-init --apply` 仅创建原绑定的 sender 设施，不配对、不捕获新批次。`personal-mirror --operation-id <新UUID> --snapshot-id <新UUID> [--expected-head-operation-id <已提交前驱UUID>]` 是只读预览；提交同组选集时增加 `--apply --expected-snapshot-sha256 <预览摘要>`。公开 apply 持实际 process→State→profile EX；在写不可变 request 前重捕获同 snapshot ID 并核 SHA。旧 operation ID 只读原 request/快照，不重新选择今天的个人事实。

`personal-mirror-status --operation-id <原UUID> --expected-request-sha256 <原摘要>` 仅读原 request 和实际回执的有界摘要，不返回个人正文、snapshot JSON、peer URL 或令牌。合法 request/catalog 发布间隙可以被识别，但 status 不修复它。

显式发送使用 `personal-mirror-deliver --apply --operation-id <原UUID> --expected-request-sha256 <原摘要> [--request-id <独立任务UUID>]`，只有已有原 request 能入持久任务队列。任务 kind 为内部 `personal_mirror`，payload 严格只有原 UUID 与 request SHA；不开放普通 `/api/tasks` 选择镜像。默认仅入队；`--wait` 才运行原本机 worker，且提交前核四根 profile 必须等于运行配置。worker 只续送已明确选择的原请求；普通定时同步和启动不能自动冻结新的个人批次。结果不确定或发送失败时保留原 operation UUID 和 request SHA，显式新的 task request UUID 可以重新排队，不替换原传输操作。

这些源码入口的本机加载及本批测试进度以产品主方案最新实施记录为准。云端个人只读产品源码已按12.4实现；持久停写/安全切主和可撤销分享仍待后续实施。

### 12.4 T16 已安装个人镜像的云端只读产品（2026-10-04）

本节描述17.90已实现的源码合同，正常服务是否已加载以产品主方案最新维护记录为准，不作为生产云或真实跨端验收证明。读取使用独立开关`personal_cloud_read_enabled`（默认false），不依赖transport当前租约，也不自动打开同步、配对或选择个人快照。已installed的镜像在传输租约到期后仍可读；prepared/stored、缺head、缺receipt及损坏证明均不能获得读取资格。

`personal_mirror_read.open_installed_read(...)`持完整profile SH，复制原committed映像到私有TEMP，建立真正owned `mode=ro`事务。它在同一读取窗口内核installed head→原intent/prepare→实际receipt、receiver node/library身份、个人三项meta及全部十表原快照；源观察器与文件身份的退出核验成功后才交付HTTP结果。普通、关闭、跨线程或其它工厂connection以及返回DTO均不授读取权限。receiver保留自己的知识`library_id`，不冒充source个人主。

capabilities中的`personal_mode=mirror`及`personal_mirror`注明来源主/任期、installed revision、snapshot ID/SHA与提交时间；`personal_read_version`绑定来源/receiver/installed镜像和同事务知识版本/ready状态。个人JSON、导出和附件响应使用`Cache-Control: private, no-store`；响应header `X-Reno-Personal-Read-Version`与JSON版本一致，后续个人请求带同一header，错版返回受控409。项目、决定卡、来源、现场核对、历史及Markdown导出复用这一读取工厂；现场读取另受`field_checks_enabled`控制，cloud的`field_checks_write`恒false。

cloud个人/现场mutation、上传和主实例operation receipt在请求体处理、SQL writer或staging之前统一403 `PERSONAL_CLOUD_READ_ONLY`。选定内容的Markdown导出POST属于读取，保持可用；这不构成共享链接或云端写主。all/home保留原写入、revision/epoch CAS、原幂等回执及409草稿合同，`personal_read_version`可为null。

附件metadata只核当前installed manifest/transfer行及原物理身份，不逐次扫描全库附件正文。下载必须在活读取工厂内no-follow打开原文件、持同一fd完成完整SHA，并经工厂成功退出后才交付响应；不能从旧DTO、path/stat缓存或`FileResponse`重新开路径。初始工厂/SHA阶段以及后续流响应均处理断开和取消，drain实际IO后关闭原fd。单附件不可读保留文字；整镜像禁读、证明失效或来源/版本变化立即撤销前端个人查询scope，取消请求/下载、移除旧缓存并清理编辑状态。云只读页面不挂编辑表单、主回执恢复或现场草稿写监听。

工程证据按17.90的实际选集分别记录：Windows九模块146项、全前端219项/typecheck与实际Chromium四项；隔离fixture沿真实source/upload/capture/prepare/commit生成installed镜像，资料与模型输出为合成，未手种成功证明，未调用真实模型/平台。原附件下载SHA不证明PDF渲染。正常配置、知识和个人事实没有因此被改写，持久停写/安全切主与可撤销分享仍未实现。

## 13. Phase 1 实施批次与协议补充（2026-09-30）

个人与现场功能已在本机启用，绍兴所在地及一份待核实官方来源已保存。知识同步可以独立推进，不需要先确定装修工序或获得报价；报价仍遵守产品方案 T15 的启动条件。以下批次落实本方案的知识同步范围，不包含 T16 的个人数据镜像、切主或分享。真实云端配置和现用库维持现状，先用隔离实例实现及验证。

### 13.1 必须贯穿全部批次的约束

- **实例身份独立。** 家庭端与云端保留各自的 `library_id`、节点身份与个人主声明。首次配对须明确绑定来源库及目标库，已绑定关系不因持有同步令牌自动改写。同步握手比较协议版本、迁移版本及核心结构指纹；握手通过不等于已经获得配对或写租约。
- **单一写入者和旧请求拒绝。** 队列认领与数据发布均需要可校验的租约所有者及代次。续期、完成、失败回报和最终发布核对相同租约；仅客户端声称任务完成不能改变云端状态。进程重启、租约过期或重新认领后，旧请求被拒绝。
- **原始事实不改写。** `user_decision.event_id` 是传输幂等键，本地自增 `decision_id` 不上行。保留原始时点、来源客户端、快照和指纹，合并后调用现有回放器；不能通过 `record_decision()` 重新制作远端历史。相同事件 ID 不同载荷整批拒绝，个人选择不进入知识复核事件流。
- **传输顺序不等于决定顺序。** 增量分页按接收库的 `decision_id`，不用 `decided_at` 截断；晚到但时间更早的事实仍能取得。每次分页固定高水位，并携带来源库身份及 `library_epoch`。来源恢复、换库或 epoch 改变后，客户端重新扫描并以 `event_id` 去重，不能继续套用旧游标。
- **知识发布保留旧可用版本。** 媒体以内容哈希区分版本，云端先验证文件，闭包和全局关系在暂存批次中完成校验。最终发布绑定同一知识版本，不能用较旧批次覆盖较新批次，不能引用尚未 ready 的原子。未完成或失败批次不覆盖旧已发布数据；发布后统一回放并更新知识修订号。
- **闭包包括消失项。** 作用域替换清除同视频此次已消失的步骤、证据和原子，不能仅 upsert 后遗留旧知识。步骤表当前已采用复合主键，不能沿用早期文档关于全部步骤表自增主键的假设。
- **私人数据保持独立。** 仅允许明确列出的知识表和 `step:<video_id>:*` 元信息。项目、卡片、来源、现场记录、个人附件、历史、操作回执、节点身份及主任期均不在知识传输白名单内。`frame.path` 只能为空；`files_json` 由接收端根据受控媒体描述符生成，不接受远端文件路径。
- **传输错误不包含秘密。** 原始下载异常、配置和令牌不进入同步状态返回值。生产同步使用 HTTPS，客户端不携带凭据跟随重定向；本地双实例测试的传输例外限定为显式隔离环境，不能作为普通部署的默认选项。

### 13.2 工作包和依赖

| 工作包 | 主要文件与责任 | 必须证明的行为 |
|---|---|---|
| S01 协议与复核事件 | 新增 `reno/sync_protocol.py`、`reno/sync_events.py` 及对应测试 | 白名单拒绝越界；结构漂移拒绝握手；两库事件乱序合并收敛；重复事件幂等；冲突事件整批回滚；旧时点新到事件不漏；个人域保持 |
| S02 配对、队列与租约 | 新增同步 schema/queue 模块，接续显式迁移；修改 `reno/db.py` 的云队列入口 | 既有队列安全升级；认领原子性；所有者及代次验证；过期回收；重试退避与死信；配对错误不改库；不自动迁移现用库 |
| S03 媒体与知识发布 | 新增同步 transfer 模块及隔离文件/数据库测试 | 路径、大小、临时区和 SHA 校验；单文件原子发布；完整闭包与全局引用校验；过期批次拒绝；消失原子清理；失败保留旧版本；报告文件与版本一致 |
| S04 同步端点与读取门控 | 新增 `app/sync_api.py`；接入 `app/main.py`，补齐 API/检索层门控 | heartbeat/pull/push/media 的角色、令牌、握手、配对与租约检查；视频、媒体、搜索、问答、比较、争议、统计及报告一致隐藏未发布数据 |
| S05 家庭同步器 | 新增 `reno/sync_agent.py`，接入 CLI 与既有持久任务协调 | 按 heartbeat→拉复核→处理队列→推闭包→推全局的次序运行；首次拉取播种；崩溃恢复和持久游标；下载/流水线不另起第二套并发写入链；完成以云端发布回执为准 |
| S06 同步状态页面 | 采集页新增云队列及家庭端状态，服务端返回受控状态摘要 | queued/claimed/retry_wait/dead/完成状态准确；家庭端离线可见；未配置不能显示“已交给家庭节点”；响应不暴露 URL 查询密钥或内部异常 |
| S07 双实例链路与文档 | 隔离双实例脚本、端到端测试；更新部署文档和本方案 | URL 入队到本地受控处理、云端可查可播；断网重传/进程退出/旧租约重放；复核双向收敛；半上传不可见；私人记录和附件保持；最后执行项目规定回归检查 |

依赖：S01 → S02；S03 可在 S01 契约稳定后与 S02 并行；S02/S03 → S04 → S05；S06 依赖 S04 的状态接口；S07 验证整个链路。各包的局部通过不代表 Phase 1 完成，T16 的启动门槛仍是完整双实例链路通过。

### 13.3 第一批接口边界

首批只增加可测试的协议与事件合并模块，不注册网络端点，不改变现用 schema 4，不修改生产配置或调用真实模型。

- `schema_handshake(con)` 返回 `protocol_version / migration_version / core_schema_sha256`。结构指纹包含固定传输合同与实际核心列、类型及主键信息，不以自报版本号替代结构检查。
- `validate_rows(table, rows, video_id=...)` 只接受固定表与列；表名、主键及视频作用域在构造 SQL 前确定。远端 `status` 不参与原子和冲突的状态覆盖，证据自增 ID 由接收端生成。
- 媒体描述符仅含 `video_id / sha256 / byte_size / extension`，不允许客户端指定路径；同步媒体默认上限 512 MiB，未来上传入口仍需流式限额及临时区总量约束。
- `export_events(con, after=0, limit=200, through=None)` 返回事件页、接收序号游标、高水位、是否有后续页和来源流身份；本地序号不进入事件本体。
- `merge_events(con, rows)` 在调用方事务内校验、去重和回放；不独立提交。不合法记录或同 ID 不同内容必须使该批事件整体失败，调用方还可回滚此前的操作。

S01 的验证命令采用独立临时目录，例如 `.venv/Scripts/python -m pytest tests/test_sync_protocol.py tests/test_sync_events.py --basetemp <本轮全新临时路径> -q`。测试继续使用现有 `tests/conftest.py` 的人工配置与模型出口阻断；不能复制实际配置作为夹具。

### 13.4 S01 实际实现与验证（2026-09-30）

`reno/sync_protocol.py` 已实现固定传输白名单、协议/迁移/核心结构握手、视频与平台 URL 校验、媒体描述符及载荷上限。表合同同时记录真实 SQLite 主键与传输逻辑键，个人数据和运维表不会因数据库增加列而自动获得传输权限。原子/冲突复核状态禁止上行；`video_asset.status` 保留家庭流水线状态，不能用作云端 ready 的判据。

`reno/sync_events.py` 已实现固定高水位导出和原始事件合并。两个独立临时 SQLite 库的事实可在不同接收顺序下汇合；本地接收序号只作传输游标，原始时间与快照保持。相同事件 ID 的不同载荷整批拒绝；合并不独立提交，空闲连接也不会因释放保存点意外提交。新复核事实即使不改变兼容 `status` 列，也会推进知识修订号，使变化后的复看状态与报告/问答缓存版本一致。

| 本轮实际检查 | 结果 | 证明范围 |
|---|---|---|
| 协议定向测试 | 181 passed | 表/列/主键、结构漂移、作用域、路径、JSON、平台 URL、媒体大小及 SQLite 数值绑定边界 |
| 事件定向测试 | 71 passed | 两库合并、重复/冲突事实、调用方回滚、旧时点新到、原始快照、真实迁移形态、个人表与节点身份保持 |
| 事件＋既有复核/迁移回归 | 94 passed，13.83 秒 | 新事件合并与现有历史、状态回放及迁移行为衔接 |
| 完整 `pytest tests` | 497 passed、4 skipped，92.47 秒 | 当前工作树后端自动回归；4 项真实模型探针仍关闭 |
| 前端 `npm run typecheck`、`npm run test` | 类型检查通过；83 passed / 23 文件，13.85 秒 | 项目要求的前端回归；本批未改前端行为，未重复构建和浏览器 E2E |
| 独立只读审查 | 已解决确认问题，无剩余阻断项 | 修正巨大整数导致非受控异常及超出 SQLite 整数绑定范围的输入；三类极值经独立重放均受控拒绝 |

S01 当批只增加两个 Python 模块及对应测试，没有接入网络路由、同步后台或迁移生产库；当时真实服务仍使用 schema 4，后续 S02 升级见 §13.6。S03 仍须检查已有原子归属与闭包交叉引用，S04 仍须接入配对、租约和所有读取面的 ready 门控，S05 仍须在来源 epoch 改变时实际重扫并校验短链最终目标。上述模块测试不能替代 URL 入队到云端可播的完整链路，也不代表 Phase 1、T16 或真实家庭网络已验收。

### 13.5 S02 配对、租约和队列契约

S02 采用显式 schema 5 增量迁移。保留 `sync_queue` 的所有既有列和历史行，增加会话所有者、会话代次、认领令牌、认领代次、重试时点、结果视频/发布 ID 和结果摘要；`lease_until` 延续 TEXT 列，新的租约使用 UTC 毫秒 `Z` 时间。新增 `sync_pair`、`sync_session`、`sync_published_video`、`sync_queue_publication`，不改变个人域主声明或节点身份。

- `bind_peer(con, source)` 接受来源库 ID、来源节点 ID 和结构握手，本机身份从数据库与本地节点文件读取。显式绑定相同关系可幂等重试，已有不同配对直接拒绝；持有网络令牌或完成握手不能自动替换配对。
- `heartbeat(con, source, session_id, lease=...)` 管理单一家庭同步会话。活跃会话续期须同时匹配会话 ID、令牌和代次；租约过期后重新取得不同令牌并递增代次。旧会话不能依靠相同节点 ID 恢复写权限。续期只延长仍有效且属于当前会话代次的队列认领。
- `claim(con, source, lease, limit=...)` 在事务中回收过期认领并选择可执行项目。每次真正认领递增尝试次数和认领代次，返回独立认领令牌；提交结果只携带 `item_id / lease_token / lease_generation`，所有者由服务端的有效会话推导。
- `fail(...)` 只收受控错误分类。网络、限速、额度窗口或处理失败按指数延迟重试，最多尝试 5 次；非法链接、源下线、非法媒体等进入死信。错误详情不接受任意远端文本，避免令牌、内网地址或路径进入页面。
- `complete(...)` 必须查到由 S03 云端发布事务写入的 `sync_published_video`，且来源配对、视频和发布 ID 一致；同时核对 `sync_queue_publication` 中绑定的任务、认领令牌/代次及会话所有者/代次。只有云端某视频曾发布过还不够，不能拿旧视频的凭据完成另一条任务。通用视频发布与任务绑定分表，允许同一视频对应多个队列项目。S02 不提供自行登记发布凭据的入口；客户端声称处理完毕不能单独把队列标为 done。同一认领的相同结果可重复读取，不同结果或旧会话请求拒绝。
- `enqueue(con, urls)` 在写入前校验整批 URL，最多 200 条，不自行提交。既有 `db.enqueue_sync_urls` 保留其提交契约并调用该域函数；云端单条入口受控拒绝非法地址，批量入口仅入队合法链接并准确返回跳过数量。历史非法链接在认领时进入死信，不阻塞后续有效记录。
- 所有领域写函数参与调用方事务，空闲连接先取得写事务，保存点用于撤销本次失败操作；不能意外提交调用方此前的修改。状态摘要和分页队列列表不返回租约令牌或原始异常，列表中的来源只显示去除凭据、查询参数和片段的域名与路径。认领、续期、失败、完成都在同一写事务中再次验证当前会话与配对。
- 真实时间在取得数据库写锁后取样。调用方已有 DEFERRED 事务时，先执行不改变任何行的同步表写操作取得写权；不能用等待写锁之前的时间判断已经到期的租约。用于测试的显式 `now` 不作为未来 HTTP 请求字段接受。

本批的隔离测试需覆盖两个数据库连接的竞争认领、租约恰好过期、同会话假令牌、重新认领后的旧结果、到期重试/次数耗尽、缺发布凭据拒绝完成、结果幂等、调用方回滚及所有个人数据保持。完整媒体检查与发布凭据生成属于 S03；本批通过不代表这些环节已实现。

### 13.6 S02 实际实现、回归与本机升级（2026-09-30）

已新增 `reno/sync_schema.py`、`reno/sync_queue.py`，接入显式 schema 5 迁移和既有云端 URL 入队入口。配对、心跳、队列认领、租约回收、重试/死信、发布绑定校验及安全状态分页均已有领域实现。云端新链接在入队前执行平台白名单校验，历史非法链接认领时进入死信；超出批量处理上限的链接计入 skipped。尚未注册同步网络端点或启动家庭同步器。

独立审查发现并修正了两处会造成错误完成或过期写入的缺口：完成凭据必须额外绑定当前任务和认领/会话代次；租约时间必须在取得实际写锁后检查。审阅者分别用隔离真实 SQLite 库复验了“旧视频发布不能完成新任务”和“等待写锁跨过租约到期后不能续期”。

首次完整回归为 **627 passed、1 failed、4 skipped**，失败来自既有 Windows 导入文件锁。双进程复现确认 `msvcrt.LK_LOCK` 约 9 秒后会因等待预算耗尽抛出 `errno 36`，尚未进入 SQLite 注册步骤；新同步队列不在该调用链。`reno/ingest.py` 已改为非截断打开锁文件，在固定字节位置用 `LK_NBLCK` 持续等待正常锁竞争，其他 I/O 错误立即失败。新增真实子进程持锁超过 11 秒、进程死亡及异常释放检查；原多进程测试仍要求恰好一次 imported。测试夹具的死亡进程 Event 清理曾卡住，已仅终止该隔离测试并修正清理，保留原业务断言后重跑通过。

| 最终实际检查 | 结果 | 范围 |
|---|---|---|
| 同步迁移定向 | 18 passed | 真实 v0–v4 旧 DDL 升级；旧列/状态/身份/附件保持；失败回滚；结构与约束门禁 |
| 队列定向 | 102 passed | 两连接竞争、代次/令牌、过期拒绝、重试和死信、精确发布绑定、事务与状态脱敏 |
| 云端导入入口 | 29 passed | 白名单、错误类型/URL 解析、批量跳过和 200 条处理上限；云端没有调用下载 |
| 文件锁与原多进程导入 | 15 passed，35.81 秒 | 新锁 12 项及原多进程 3 项；真实长等待后成功获取互斥锁 |
| 最终完整 `pytest tests` | 640 passed、4 skipped，111.64 秒 | 修复后的全工作树回归；真实模型探针仍未运行 |
| 前端类型检查与测试 | 类型检查通过；83 passed / 23 文件，11.48 秒 | 本批未改前端行为，未重复构建或浏览器 E2E |
| 真实库只读副本升级 | schema 4 → 5 通过；重复升级不改动 | 31 张原有非 meta 表全部旧列内容保持；仅迁移版本元数据变化；节点身份与 1 份个人附件保持 |

按用户此前明确授予的真实库维护授权，21:45 在确认没有待处理/运行任务和上传后，停止已核实的本机 8765 服务，取得 worker 文件锁并保存恢复点，再显式升级现用库到 schema 5。正式升级同样保持 31 张原有非 meta 表全部旧列内容；元数据仅改 `migration_version`，配置文件和节点身份原字节保持，个人附件已备份且哈希保持。现用库与备份完整性为 `ok`、外键问题 0，重复升级 `changed=false`，四张同步新表为空，没有自动配对任何节点。

恢复点及核验结果位于 `data/maintenance-schema5-20260930T214501+0800-b3596666/`：`before/` 包含一致数据库、个人附件及其清单和节点身份；`before.json`、`result.json`、`runtime-verification.json` 保存核对结果。另有迁移自动生成的数据库备份。恢复包没有复制原视频，不是全媒体灾备包。正常 `reno serve` 服务已恢复，绍兴资料、待核实规范及 PDF 可读，知识统计仍为 28 视频 / 349 原子 / 16 聚类 / 13 冲突，任务仍为 47 cancelled / 29 done；“防水”搜索仍返回 49 条。

服务恢复后已确认 worker 更换为新所有者，并通过两次采样确认同一新所有者持续续期；任务数量与状态保持不变。服务启动及核验日志保存在上述维护目录。

S03–S07 尚待实施。上述结果不等于实际媒体上传、知识闭包发布、远端调用、家庭同步器或同步状态页面已经完成；本批没有连接真实云端、调用真实模型、提交或推送代码。

### 13.7 S03 发布合同与实现边界

本批新增 `sync_media`、`sync_snapshot`、`sync_claims`、`sync_reports`、`sync_transfer` 和显式 schema 6 迁移。网络入口仍由 S04 注册，家庭端编排仍由 S05 接入；仅有领域函数不表示两实例同步已经可用。

- **完整知识代次。** `export_snapshot(con, publication_id=..., media=..., reports=...)` 要求调用方已有一致读事务，导出全部 `processed` 视频及其闭包和完整全局关系。未处理视频仍留有原子、缺少媒体描述符或存在悬空引用时拒绝。单快照最多 64 MiB、10,000 个视频，各表继续服从 S01 上限，不截断超限内容。当前整批在内存中有界校验，然后在同一数据库事务发布；暂不提供分片闭包发布。S05 必须持既有处理锁导出，不能只凭 `processed` 状态判断流水线已停止。
- **闭合引用。** 同视频的 ASR/OCR/视觉证据、帧引用必须可解析；聚类成员和原子 `cluster_id` 双向一致，冲突双方和关联聚类属于本代次。已有原子不能借相同 ID 换属其他视频。目标已有知识视频不在快照内时拒绝，不能替换全局关系后留下旧悬空知识；整视频远端删除需后续明确删除合同。
- **不可变媒体。** `MediaStore.receive()` 只接收受控描述符和字节流，固定原片路径 `sync/<video_id>/<sha256>.<ext>`，不传音轨或帧文件。临时上传串行持跨进程锁，单文件遵循 512 MiB 上限，默认临时区总量 1 GiB；失败移除本次临时文件，崩溃残留仅在持锁后回收。校验大小和 SHA 后同文件系统发布，不覆盖已有哈希文件；重复上传也检查实际内容。目录和文件使用受保护句柄或相对目录描述符操作，拒绝重解析点和外部硬链接。
- **数据库决定可见性。** `publish_snapshot()` 先校验和准备不可变文件，再取得写所有权、重新检查配对/会话及代次，替换作用域内的全部输出，删除消失的原子、证据、步骤和 FTS 项，更新全局关系并回放本机复核事实。`files_json` 由接收方生成，`sync_state=ready` 仅随完整事务写入。最终媒体保护范围内再检查租约，失败整批撤销；函数不独立提交，HTTP 调用方提交成功后才可返回成功回执。
- **版本与重试。** 来源 `library_epoch/kb_revision` 较旧时拒绝；同版本但知识内容不同也拒绝。相同知识允许以新发布 UUID 补交报告或绑定新的合法任务；相同发布 UUID 的载荷与认领集合必须相同。报告与认领不会绕过来源校验。成功事务才写入视频发布凭据、精确任务绑定并完成队列；原任务链接必须能够对应发布视频，不同短链别名在没有受控解析映射时不能凭客户端声明完成。
- **报告随提交切换。** 仅允许五个报告名、每份最多 4 MiB UTF-8 文本、正确 SHA、匹配知识版本及报告策略。文件先写入 `generated/.sync-versions/`，`sync_report` 表中的指针随知识事务提交，兼容旧文件不能覆盖此权威指针。保留来源版本、接收方版本和原始复核事件集指纹；两端复核事实不一致时显示过期。新报告失败保留旧文件与旧指针；损坏文件不能回退成另一个版本冒充成功。
- **schema 6。** 为 `video_asset` 增加本地 `sync_state`（旧视频默认 ready），新增 `sync_publication`、`sync_report`。迁移只增加结构与迁移版本，保留个人表、历史、回执、附件和节点身份。已有配对指纹不自动改写；两端升级后仍需明确维护配对合同，旧握手不会静默获得新版本权限。

不同短链指向已有内容的来源映射、超出有界整批上限的分片协议、已发布文件的代次回收不在本批自动处理。S05 需落实来源解析与处理锁；S04 需落实所有读取入口的 ready 过滤，不能仅依靠接收方赋值。实际测试、真实库迁移和服务状态以本节后续记录为准。

**实际实现与验证（2026-09-30）：**上述领域模块已实现。独立审查补齐了报告目录的句柄保护、任务原链接与结果视频的关联校验、最终媒体复核结束后的租约检查；同版本刷新保留接收方复核历史，旧读事务仍读取对应旧报告。调用方事务完成后立即提交是 S04 的接线要求：当前函数不自行 commit，不能保证调用方任意延迟后提交仍有效；后续 HTTP 适配层不得在发布返回与提交之间执行 await 或其他 I/O，应把最终授权检查和提交绑定在同一受控执行段。

| 实际检查 | 结果 | 范围 |
|---|---|---|
| schema 6 定向迁移 | 22 passed；相关五组 337 passed | 实际 v5 DDL、有配对/任务绑定旧库升级、失败回滚、结构及旧数据保持 |
| 快照与来源绑定 | 各 56 passed | 全部引用闭合、稳定规范化、读事务一致性、平台/ID/分页及短链边界 |
| 媒体 | 完整回归中 72 passed、1 skipped | 流式限额/SHA、临时配额、两进程、崩溃清理、不可变发布、目录替换、硬链接、长路径、调用方异常原样传递 |
| 发布与独立审查 | 完整回归中 28＋12 passed | 事务和旧版本可见性、精确任务绑定、等待写锁及最终媒体复核跨期、报告版本/复核口径、私人表/附件保持 |
| 完整后端 | 886 passed、5 skipped，135.91 秒 | 首轮 881 passed、1 failed、5 skipped；修正媒体 guard 误包装调用方异常后重跑，原断言保留 |
| 前端 | 类型检查通过；83 passed / 23 文件，12.07 秒 | 本批未改 UI；未重复构建或浏览器 E2E |
| 真实库只读导出 | 28 视频 / 349 原子 / 16 聚类 / 13 冲突闭合 | 媒体描述符取存量 SHA 与实际文件大小；此项不代表全库媒体重新算 SHA |
| 真实库副本与正式升级 | schema 5→6，重复迁移 changed=false | 35 张旧非 meta 表全旧列内容、身份、配置及 1 份附件保持；仅 migration_version 元数据变化 |

真实模型探针仍关闭；Windows 缺少文件符号链接创建权限的一项测试跳过，但实际目录 junction 测试已运行。POSIX 分支尚未有实机证据，需随 S07 Linux 双实例环节补齐，不能将 Windows 测试当作 Linux 结果。

正式维护于 22:33 完成，恢复点为 `data/maintenance-schema6-20260930T223315+0800-102a9479/`：`before/` 含数据库、个人附件、附件清单与节点身份；记录和日志在同目录，不含原视频。自动迁移备份为 `data/reno.db.bak-migrate-20260930T143315375342Z-0fa831b9`。正常服务保持 loopback 8765、角色 all；配置未改，新同步表为空，没有自动连接或绑定云端。真实 API、PDF 下载哈希和原视频 Range 读取已核对。S04–S07 仍待实施，T16 仍未启动。

### 13.8 S04 同步 HTTP 与已发布数据读取

本批接入 `app/sync_api.py`，使用 S01–S03 的领域合同。没有新增 schema 版本。下面的 `source` 均为 `{library_id,node_id,handshake}`；`lease` 是 heartbeat 返回的完整会话租约，客户端不能传 `now` 或自行选择租期。

| HTTP 入口 | 请求与行为 |
|---|---|
| `GET /api/sync/handshake` | 返回 `{target:{library_id,node_id,handshake}}`；只读身份，不配对、不取得租约 |
| `POST /api/sync/heartbeat` | `{source,session_id,lease?,metrics?}`；显式配对后才允许取得/续期，返回租约及已续期认领 |
| `POST /api/sync/pull` | `{source,lease,events:{after,limit,through,stream},claim_limit}`；首次事件页用 `after=0/through=null/stream=null`，后续带返回的高水位及来源流身份。先返回当前会话仍有效的已有认领，不足才新领，丢响应重试不会把旧认领留在无人处理状态。拉取事实与认领同事务提交 |
| `POST /api/sync/push` | `kind=events` 时发送 `{source,lease,kind,events}`；`kind=snapshot` 时发送 `{source,lease,kind,snapshot,claims}`；`kind=fail` 时发送 `{source,lease,kind,claim,error_class}`。字段按类型形成闭集，不接受额外配对、时钟或完成标志 |
| `POST /api/sync/media` | `application/octet-stream` 字节流；`X-Sync-Media` 为规范带 padding 的 base64url JSON `{source,lease,descriptor}`，不放 URL 查询参数。不接受 multipart 或压缩请求体。完整媒体存储成功仍不代表知识已发布 |
| `GET /api/sync-status?offset=0&limit=30` | 普通浏览器 Reno 鉴权；返回配置/配对/兼容/在线状态、脱敏统计和队列分页。同步凭据不能替代浏览器凭据；未配置或配对结构指纹不兼容时不会显示在线 |

所有同步入口使用 `X-Sync-Token`，与 `X-Reno-Token` 分离。同步路由只在 cloud 角色可用，空或非法主同步令牌始终关闭；查询参数不参与同步鉴权。仅接受 ASGI 的 `https` scheme，不自行相信 `Host`、`Forwarded` 或 `X-Forwarded-Proto`。`reno serve` 为 Uvicorn 显式设置 `trusted_proxy_ips`，默认仅 `127.0.0.1,::1`，拒绝通配符、网段和不明确地址，覆盖宽泛的 `FORWARDED_ALLOW_IPS` 环境值。使用其他 ASGI 启动方式时，必须同样限制代理信任；实际证书、TLS 终止和部署脚本配套仍在 S07。

测试 HTTP 例外需同时满足 `sync_allow_insecure_test_transport=true`、明确的 `RENO_QA_ROOT/RENO_CONFIG_FILE/RENO_DATA_DIR`、全部可变目录及有效人工配置在独立 QA 根内、数据目录与环境一致、客户端确为回环或人工 TestClient。只设置该开关不能让真实部署接受 HTTP。

**显式配对与升级后的合同刷新：**

```text
python -m reno sync-identity
python -m reno sync-pair --source <家庭节点身份JSON文件>
python -m reno sync-pair --source <家庭节点身份JSON文件> --refresh-schema
```

第一条只导出当前节点身份与握手，可在家庭端运行；后两条仅在 cloud 本机执行。普通配对不会替换已有 peer。刷新只允许来源与目标身份均保持一致、没有有效会话或认领的情形，保留原队列和历史；不能拿刷新选项换成其他家庭节点。远程接口不能自动完成配对。首次 heartbeat 响应彻底丢失且客户端没有取得租约令牌时，不开放免令牌续期；客户端等待原租约到期再明确取得新租约，S05 需保存和呈现这一等待状态。

**凭据轮换：**配置支持 `sync_token`、`sync_previous_token`、`sync_previous_token_until`（UTC 日期，旧令牌宽限最多 24 小时）。同步访问层每次从已选配置文件和 `RENO_*` 环境变量读取凭据快照，外部原子替换文件不需要重启；不会把该快照写入状态响应。旧令牌缺截止时间、过期或不符合窗口时拒绝，清空主令牌也不会只凭旧令牌继续开放。角色、worker 和代理信任不随凭据热切换，变更这些设置须正常重启。

**事务与资源边界：**JSON 先流式检查大小再解析，拒绝重复键、非有限数、重复敏感头；媒体元信息头最多 8 KiB，媒体仍遵循 S03 的字节数和 SHA 合同。ASGI 通过 AnyIO 工作线程按需取流，不先落第二份无界上传文件；每进程最多两个上传请求，锁等待最多 30 秒，读取空闲最多 30 秒、整体最多一小时。断开、取消或超时后等待存储线程释放锁/句柄及临时文件，再归还并发名额。最终会话和认领检查与 `commit` 在同一工作线程连续执行，不在中间 `await`；提交失败返回受控错误，不能返回成功回执。

**公开读取边界：**cloud 的视频、元信息、时间轴、媒体、帧、搜索、FTS、问答、比较、争议、聚类、筛选项、统计、复核和 classic 页面均使用已发布视图；过滤先于排序、分页、LIMIT 和模型材料构造。混有未发布成员的全局关系整条隐藏。原始事实导出、合并和回放保持全库语义，个人域历史不被修改。问答缓存含角色与 ready 状态签名，帧缓存含媒体 SHA；报告以同一读事务读取指针和代次成员，不能把多次独立读取的结果混成一份报告集合。

**实际完成与回归：**S04 上述接口与门控已落地。完整后端 **1060 passed、5 skipped（163.46 秒）**，前端类型检查通过、**83 passed / 23 文件（10.37 秒）**。定向 HTTP 27 项及独立审查 18 项覆盖真实 SQLite 提交失败、提交前租约/认领过期、丢响应恢复、严格头部及整数游标合同、空闲超时、AnyIO/asyncio 取消与真实上传锁；公开读取 25 项覆盖超过分页上限的隐藏行、猜 ID、控制模型输入、旧快照和报告、缺失目标复看以及云端本地任务详情关闭；访问控制与既有 Phase 0 共 107 项覆盖真实外部配置替换和挂载前缀；媒体为 98 passed / 1 skipped。

独立审查修复了上传锁无期限等待、idle 异常被误归类、带前缀部署时鉴权路径不一致等问题。原 S03 媒体/发布回归保持通过。最终 5 个跳过仍为 4 个真实模型探针和 1 个 Windows 文件符号链接权限用例；Linux 文件保护实机、生产 TLS 和 S07 双实例完整链路尚未验证。框架 TestClient 的 HTTPS scheme 仅证明应用门控，不代表证书或网络 TLS 已验证。

23:12 本机正常服务首次加载 S04，23:19 加载最终游标类型修正，schema 仍为 6，现用库/配置/身份/附件保持；同步配置为空、无配对，状态接口正确显示未开放，handshake 无凭据返回 401。记录见 `data/service-s04-20260930T231032+0800-fed70d87/`。本批未连接实际云端或启动家庭同步器，S05–S07 仍待实施。

T16个人只读产品已按12.4接入同事务知识版本和ready规则，保留`personal_evidence_ref`原始冻结内容。未打开读取开关、未安装完整镜像或原证明失效时，cloud能力门仍拒绝个人读取；所有个人写入始终拒绝。内部raw解析函数不授HTTP读取资格，不能绕过owned工厂和上游能力门。

### 13.9 S05 家庭同步器（2026-10-01）

本批新增 `sync_client`、`sync_client_transport`、`sync_state`、`sync_sources`、`sync_agent`，并把内部 `sync` 任务接入既有 `tasks` / `worker` 和 `sync-run` CLI。实现与本批回归已完成；下列合同及隔离 HTTP 结果不替代 S07 的完整双实例、Linux 和生产 TLS 证据。

- **默认关闭与单写入协调。** `sync_enabled` 必须为布尔 true，并配置 HTTPS 目标地址和目标库/节点 UUID。home/all 可执行，cloud 拒绝。serve 的 follower 在同一持久任务队列中按间隔和抖动排一轮同步；该轮必须持真实 worker OS 锁，普通任务接口既不能创建也不能读出内部同步任务。同步 heartbeat 线程仅续远端租约及本地状态文件，不写本地知识库；退出时等待线程收尾。
- **首次播种与恢复。** 先取得配对租约，完整拉取远端复核，再推本地原始复核事件，之后认领并处理一项队列任务。每轮最多 20 页复核，未完成则保留游标下轮继续，不提前发布。合并提交成功后才保存 inbound 游标，远端确认后才保存 outbound 游标；重试按原事件 UUID 去重。本机恢复 epoch 与游标重置在一次状态文件替换中提交；目标流 epoch 改变时重扫双向事件并清媒体确认缓存。
- **租约与丢响应。** 已知 token 即便本地记录到期仍先向服务器尝试续租，只有远端明确过期才清旧会话认领、重新申请。首次心跳没有取得 token 时不作免凭据续期。待发内容、publication UUID、claims 及 origin 均先写不可变 outbox，再发请求；网络错误保持原批次。确定的内容拒绝走受控失败/死信，旧认领被撤销时重拉，不用新 claim 篡改旧批次。
- **来源与下载。** 公网域、每跳 Location、最终 canonical 视频资源均校验；DNS 结果只允许公网 IP，解析 HTTP 连接固定已检 IP，保留原域 Host/SNI，使用绝对期限限制慢响应头。同步导入拒绝多视频元数据，冻结已校验 yt-dlp metadata 后下载，并阻止 `--load-info-json` 失败时暗中按网页 URL 重新提取。实际下载 SHA、去重后 canonical asset SHA 和来源文件 SHA 必须一致；不改原有 `video_asset.source_url`。完整轨迹只存本地 SourceMap，origin 绑定完整原 URL、最终资源、提取 ID、canonical ID、内容 SHA、来源节点和不可变 mapping ID。
- **合同与真实边界。** origin 是已配对家庭节点的来源声明，不是云端独立核实的平台证明；接收方校验其闭集字段、映射摘要、节点、队列 URL 和媒体绑定，并纳入 publication hash。握手增加 claim-origin 合同标记，schema 仍为 6，旧配对须显式刷新合同。短链解析器的 IP 固定边界不覆盖 yt-dlp 的平台 CDN 下载。
- **流水线、报告与完整代次。** 导入抑制额外 process 排队，在持锁任务中按 download→pipeline→judge→静态报告执行；pipeline 步骤间检查取消。上传前重算媒体 SHA，完整快照从一致 SQLite 读事务导出。每轮比较完整 videos/globals 指纹，补足零原子新视频、转写/帧等未触发既有 revision 的变更；必要时在写事务中重查并提升知识修订号。缺失发布基线或恢复后的首次同步也预留新修订号，避免与云端保留历史的同版本内容冲突。仅报告变化可以按相同知识版本、新 UUID 同步；AI 总结仍需用户显式生成。
- **成功定义。** 只有目标库身份匹配的云端发布回执才保存 published_version、实际冻结内容和报告指纹。下载、流水线、报告成功不等于队列 done。恢复、路径越界、损坏 outbox 或来源不匹配均不通过修改快照字段来绕过校验。

客户端控制请求具有覆盖连接、TLS、读写的总时限；媒体请求从上传前内容核验起共享最长一小时预算。实际网络后端在等待中检查取消，仅保留两个有界 DNS 后台线程，不让超时后的 HTTP 请求或状态写入继续后台执行。外部注入的自定义 transport 仍须自行遵守请求 deadline。服务关闭等待 worker 释放任务与锁，ASGI 通过线程池等待，避免占住事件循环；前台 Ctrl+C 也会结束当前任务状态。

| 实际检查 | 结果 | 范围 |
|---|---|---|
| 最终完整后端 | **1470 passed、5 skipped，316.62 秒** | 包含 S05/S06、来源、总时限、取消、服务关闭及既有产品回归 |
| 客户端、真实传输与 HTTP 集成 | 166 passed | 总时限、DNS 名额、取消、协议错误、真实回环服务器 |
| 独立 HTTP 双实例场景 | 2 passed | 正常发布与发布回执丢失后的原批次重放；平台/模型使用合成输入 |
| 状态文件 | 52 passed | 身份绑定、原子保存、游标、冻结批次、租约与恢复 |
| 服务关闭专项 | 5 passed | 正常退出、启动异常、运行任务的取消及锁释放 |
| 前端 | 类型检查通过；**114 passed / 25 文件，13.01 秒**；生产构建通过 | 包含 S06；浏览器证据见下节 |

五项跳过为四个真实模型探针及一个 Windows 文件符号链接权限用例。DNS 名额测试曾受 CA 加载耗时影响，已改用显式线程与 entered 事件确认实际 DNS 阻塞，保留原始 0.3 秒请求预算和小于 0.5 秒拒绝断言；未放宽生产超时。HTTP 集成的合成媒体只证明上传、发布、读取和 Range，不证明同步视频实际可解码播放。Linux 文件保护、生产证书/TLS、真实平台和模型调用均未验证。

部署参数与命令见 [部署说明 §5.2](deploy.md#52-家庭同步器s05)。本机服务于 00:51 后重新加载 S05/S06；恢复材料与前后记录在 `data/service-s05-s06-20261001T005101+0800-c7f5b60e/`，包含数据库、节点身份与实际个人附件副本，不含原视频。36 张非 worker 表、全部 meta、配置、节点身份、附件哈希及同步状态文件存在性保持；schema 仍为 6，all / loopback / 同步关闭，无云端配对。01:04 正常 API 核对 28 视频 / 349 原子 / 16 聚类 / 13 冲突、“防水”49 条、任务 47 cancelled / 29 done、绍兴资料、PDF 哈希及视频 Range 206；随后确认同一 worker 所有者租约持续续期。证据在同目录 `runtime.json`、`worker-renewal.json`。检查脚本曾误用不存在的 `/api/stats` 和缺少 `/file` 的视频路径，修正为实际路由后通过，没有因此改动产品接口。

### 13.10 S06 同步状态页面（2026-10-01）

采集页已根据节点模式显示同步状态：cloud 提供每页 10 条队列、有效/失效认领、重试与完成状态、家庭节点在线状态；只有 done 且视频 ready 才出现观看入口。home/all 展示是否启用、配置状态、处理阶段、最近成功、待发批次、复核积压和会话到期提示。刷新失败保留上一次结果并明确标记旧数据；云端隐藏本地模型、cookie 与处理任务控件。导入后刷新同步查询，批量导入及书签脚本保留完整 query（包括 B 站分 P），仅去除 URL fragment，不误删本地文件名中的 `#`。

`/api/sync-status` 家庭摘要由 `sync_home_status` 只读配置/状态文件生成，不初始化数据库、不创建状态文件、不发网络请求。显式字段投影排除端点、令牌、租约明文、认领、outbox 和磁盘路径；家庭 paired/compatible 返回 null，不推测远端配对结果。

相关后端回归 **179 passed**；Playwright 浏览器专项 **6 passed，7.3 秒**，覆盖亮暗主题移动宽度、分页、有效/失效认领和匿名鉴权。首次浏览器启动受 sandbox EPERM 限制，正常授权环境重跑通过；两项测试修正分别为显式清空匿名上下文继承的请求头、按真实带序号标题定位，未修改业务来迎合测试。QA 队列状态是人工种入的合成数据，只证明页面；实际发布由 S05 HTTP 集成独立覆盖。截图位于外部 QA 根 `reno-s06-browser-bc27018727d54075a5775a7a1dbaebd3/playwright-results/`。残留隔离服务已按完整命令及 18766 端口核实后停止，8765 正常服务保留。

本机正常服务已在真实浏览器以 375×812 移动视口核对：“家庭同步 / 同步已关闭”正确，文档宽度与视口同为 375，无横向溢出。这不等于真实手机、真实云端或完整同步播放验收。

**下一批 S07：**补齐部署脚本 TLS 选项及代理合同、已有服务可靠重启、可复用双实例运行/故障恢复脚本；继续完成 URL 到云端可播链路及 Linux/代理环境证据。现有 `/api/sync/` 代理缺少 `X-Forwarded-Proto`，尚不能作为可用的同步 TLS 部署模板。Phase 2 的历史批次恢复、专门对账报告、回收与配额，以及 T16 个人镜像/切主/分享尚未实现。

### 13.11 S07 部署配套与完整隔离同步链路（2026-10-01）

S07 已补齐工程实现。`deploy/deploy.sh`、`deploy_support.py`、`tls.sh` 与 nginx 模板作为同版本目录使用；配置、升级及场景命令见 [部署说明](deploy.md)。本节分别记录真实同步、操作系统/代理验证与受控系统命令测试，不将其混同为生产云部署。

- **安装与升级。** 默认回环监听；cloud 禁止导入家庭整库，先初始化独立空库后配对播种。代码只快进更新，拒绝脏目录和未发布本地提交，不再硬重置；Git 对服务所有的目录只在当前命令信任精确安装路径。新版明确 enable 后 restart，健康检查读取最终保留的令牌并拒绝重定向。新库初始化、旧库兼容性校验与显式迁移分开，`--no-service` 不起服务、不调用旧服务健康接口。
- **凭据、文件与目录。** 凭据来自文件或环境，拒绝旧明文 argv 参数；原子写配置保持 0600 和原 uid/gid。归档先检查路径、类型、重复条目与白名单，再以排他创建写文件；保留已有库与并发创建的库。仅调整专用安装、数据、媒体和缓存目录的所有权，不对通用父目录递归操作。部署 Bash 文件由 `.gitattributes` 固定 LF。
- **TLS 事务。** 校验公开域名及全部 A/AAAA 指向的随机 HTTP 挑战；Certbot webroot 签发或复用证书，不让其改 nginx。先验证 HTTPS 证书/域名及本机、公网路由，再启用 HTTP 跳转。已有管理站点在失败时恢复，其他站点不覆盖；nginx 停止时能够启动恢复。UFW 只在已启用时调整应用端口拒绝与 80/443 允许，状态未知或端口与 SSH 冲突则拒绝，不启用/重置防火墙。续期 timer 与校验后 reload hook 已接入。
- **代理合同。** 每个同步 location 都覆盖 scheme 与客户端转发头；媒体/push/控制体限额分别为 512 MiB/65 MiB/64 KiB，应用保留精确边界；8 KiB 元信息可穿过 16 KiB 头缓冲。关闭自动重试和请求/响应缓冲，设置连接、空闲与整体对应的超时；429 使用 `SYNC_BUSY` JSON 与 Retry-After。普通 API 令牌不能代替同步令牌，access log 关闭以避免媒体查询令牌记录。
- **可重复的两进程链路。** `run_sync_scenario.py` / `sync_scenario_node.py` 创建系统 TEMP 内的新 home/cloud，拒绝仓库或现有根目录。真实 API 入队、持久任务与 worker、ingest、SourceMap、上传、快照发布、事件回放均走生产代码。平台解析/下载输入/ASR/OCR/模型、个人哨兵及测试图样 MP4 明确标记为合成。人工预置仅用于测试前置记录，不种入 done/ready 或发布回执。
- **故障和成功判据。** 真实 200 发布后丢回复，重发相同 UUID/hash；家庭进程在 durable outbox 和上传完成后硬退出，随后 cloud 断网/重启，在不清租约、不改绑定/批次的情况下恢复。真实队列先 queued、发布前 claimed 且无观看入口、收到发布凭据才 done；旧会话提交返回 409。截断上传清理、上传未发布不可见、三条家庭复核抵达 cloud 与一条 cloud 复核抵达 home、三个来源映射的 URL/身份/SHA、十张个人表及附件字节均有实际断言。

| 检查层 | 实际结果 | 证明范围 |
|---|---|---|
| Ubuntu 24.04.2 / Python 3.12.3 / nginx 1.24.0 | POSIX、真实代理及既有 HTTP 专项 201 passed、4 Windows-only skipped | 普通 Linux 用户、独立临时数据；非生产服务 |
| 部署 Bash 驱动 / TLS 控制流 | 44 passed、2 root-only skipped；另 root 专项 2 passed | Bash 和文件操作真实；systemctl、UFW、Certbot 等使用受控 stub，无公网签证 |
| 真实 nginx 与 TLS | Linux 和 Windows 各 7 passed | 测试证书默认不被信任、显式测试 CA 可信后握手成功；转发头覆盖、非受信来源伪造头拒绝、真实体限额/头缓冲/429 |
| 双实例场景 | Windows 5 passed（45.77 秒）、Linux 5 passed（29.88 秒） | 正常发布、丢回执、进程/网络恢复、来源绑定、双向事件和私人数据保持 |
| Chromium | 5 passed（8.4 秒） | 三段实际同步的 H264 MP4 解码、播放时间推进、seek 至 4 秒后继续播放、队列/检索/Range/匿名拒绝 |
| 前端 | typecheck 通过；114 passed / 25 文件（13.13 秒） | 本批未改产品 UI；沿用 S06 已构建的前端 |

Windows 浏览器场景证据在外部 TEMP 根 `reno-sync-chain-ad1a9859634e47b09cbc153d7939fd9b/`，含 `evidence.json` 和 `playwright-sync-chain-results/` 图像；服务已用实例鉴别的私有停止入口关闭，并确认端口关闭。生产 8765 服务和现用 schema 6 保持原状。新 CI 包含真实 nginx 测试和独立同步/播放 job，只上传脱敏 evidence 与截图，排除私有 manifest、配置和租约；尚未推送或执行远程 CI。

完整 Linux 回归首轮为 1512 passed、9 failed、12 skipped。已区分并修正：临时环境未装项目声明的 yt-dlp（影响四例）；两个旧导入测试把 API 响应当成后台已完成（改为等待唯一持久任务 done，并在桩恢复前停止 worker）；测试服务/占用 socket 的 TIME_WAIT；旧测试抽取 shell 内嵌 Python 的写法（改为真实归档调用 helper，新增排他创建竞争断言）；报告目录攻击过早发生在目标尚未创建阶段（等待句柄身份捕获，POSIX 明确证明真实替换，保留全部外部文件不变及拒绝结果断言）。这些首轮修正没有更改业务生产模块或削弱保护。相关回归 47 passed，报告目录专项两平台各 12 passed；两个 TLS/SNI 用例在精简 Linux 缺少 CPython 公用证书时改用临时生成的合成证书，实际运行 2 passed。

第二轮全量为 1520 passed、1 failed、12 skipped，进一步暴露真实上传完整性缺口：发送后仅比较 stat，在同大小、同一时间戳 tick 的重写中可能漏检。`sync_client.upload_media` 已在回执后以原句柄重新校验 SHA，采用无缓冲读取避免小文件 seek 复用旧缓存；预检、传输和最终扫描始终共享原 deadline 与取消。新增六项确定性回归先复现失败再修复，涵盖大小文件头/尾同 stat 重写和最终扫描超时/取消。代价为每次成功上传额外一次有界顺序读取，没有增加超时预算或后台传输。客户端/transport Ubuntu **170 passed**；Windows 加实际 HTTP 集成 **172 passed**，独立复审通过。

**最终完整回归：1529 passed、10 skipped（222.19 秒）**。十项跳过为四个 Windows 专项、四个真实模型探针、两个 root 所有权专项；两项 root 测试已在独立 Linux 临时夹具中另跑 **2 passed（0.91 秒）**。最终日志 `data/s07-linux-full-complete.log`，首轮/二轮失败日志保留用于追溯。额外的旧测试清理顺序检查 Linux 2 passed，Windows 健壮性 28 passed；全量包含所有最终生产代码与用例。现有 FastAPI/Pillow 弃用提示未扩展成本批无关重构。

**保留的真实边界：**公网 ACME、目标云服务器 systemd/UFW 的实际修改、真实平台/模型、家庭实际网络和手机尚未验证。本次 Linux runtime 与 nginx 均位于新建 `/var/tmp` 测试目录，没有安装系统服务。测试缩短的 1 秒旧会话、12 秒 worker 租约与合成输入均写入 evidence，不外推为生产时长。Phase 2 和 T16 尚未实现；T15 仍等真实报价及变更。

### 13.12 Phase 2 分批实施合同（2026-10-01，实施中）

Phase 2 继续按下表推进。P01–P04 已完成，P05/P06 继续实施；当前完成范围不代表整个 Phase 2 完成。

| 批次 | 交付范围 | 完成判据 |
|---|---|---|
| P01 发布前归档与预览 | schema 7 增加 `sync_checkpoint`；每个新发布批次归档接收方替换前的知识为独立 SQLite 文件；只读列表与恢复预览 | 同一写事务取得前像；提交后可列举；失败孤立文件不可见；丢回执重放不重复归档；预览校验内容、身份和依赖 |
| P02 复核对账 | 独立只读 API、页面、Markdown 和 CLI；区分当前待核对与已有有效后续事件的历史 | 云端可见性先于汇总/分页/导出；时间与事件顺序匹配回放器；不改历史、不调用模型 |
| P03 受控恢复 | 本地恢复入口、预览版本 CAS、幂等操作号、持久恢复暂停和显式续传 | 知识版本单调增加；个人域、附件、队列、配对、事件和历史保留；恢复不被下一轮同步立即覆盖 |
| P04 关联有效性 | 独立 stale 轴、界面和按需人工重算入口 | 不借用会被 `review.replay()` 覆盖的 status；不自动消费模型预算 |
| P05 引用回收 | 保留 N 代、历史文件/媒体引用回收、帧缓存 LRU | 现用知识、保留归档、未完成发布等作为引用根；预览与执行分开；保护未知文件 |
| P06 节流与预算 | 下载节流、日配额、模型预算与耗尽后的暂停/恢复 | 可解释的状态、持久化计量、重试不重复扣减；现有抖动及临时区配额继续复用 |

**P01 归档边界。** `sync_publication` 只有发布版本、摘要和回执，家庭 outbox 是重试材料；两者都不能替代接收方恢复前像。新增归档在发布写事务中、替换知识之前构建，只复制受控知识列、`step:` 元数据、报告指针及依赖清单，不备份整库。保留本地 `files_json`、ready 状态、证据主键及聚类/冲突关系，不提升未发布数据。排除个人十表、原始复核事件、任务/队列、配对、节点身份、凭据及全局元数据。空知识库可以归档。

归档文件先以受保护目录和排他暂存文件落盘，校验后原子发布；只有与已提交 `sync_publication` 关联的登记才可查询。登记与知识发布同事务回滚，失败后的无登记文件不作为恢复点。列表和预览只读，不创建目录、升级库或补写元数据；预览重查 SHA、大小、结构、库身份及依赖文件。旧知识缺失媒体或报告时保留问题清单并标记不可恢复，不能因此阻止一次修复旧库的正常发布。当前没有恢复执行 API。

**P02 分类口径。** 汇总单位为复核事件，不是目标数量；完整可见事件集的汇总与所选筛选后的分页总数分别返回。`active/stale/missing/legacy_unbound/invalid_time` 保持既有绑定语义，另有 `decision_valid` 判断动作及旧改写内容是否合法；绑定有效但决定无效的当前记录同样需要核对。记录只有在同一目标存在按有效 UTC 时间及稳定事件键排序之后、合法动作且当前绑定 active 的事件时才归入历史；后续 stale/无效时间/无效动作事件不能消除待核对记录，时间未知的旧记录继续保守保留。被冻结的旧内容与当前来源分开显示，隐藏来源不能借历史快照或导出泄漏。

对账使用同一 SQLite 读取快照，携带知识版本、事件高水位与 ready 可见性签名。Markdown 导出选中筛选的完整结果，页面分页不裁掉导出记录；普通 Reno 令牌适用于列表和导出。CLI `reno reconciliation --scope attention` 输出 JSON，`--markdown` 输出 Markdown 到 stdout。该报告不进入五类同步静态报告集合。

**后续恢复合同。** P03 不能重放旧 outbox 或放宽旧版本发布校验来实现恢复。它需从已校验的归档替换知识白名单、清理较新成员、重建 FTS，并回放当前完整复核历史；保留个人数据、附件、节点身份、配对、任务、队列、发布历史、事件流及游标。恢复完成进入持久暂停：暂停新认领与知识发布，心跳和复核事件仍可同步。显式恢复同步时，家庭端生成新修订号、新发布 UUID 与完整代次，不清空历史游标或假装旧批次是新数据。逻辑知识恢复不等同于整库灾备恢复的 epoch 规则。

### 13.13 P01/P02 实现、回归与本机维护（2026-10-01）

本批已完成 §13.12 的 P01 和 P02。`sync_checkpoints` 使用固定格式 1 / 归档 schema 7 的白名单 SQLite，和以后仅增加运维表的 live schema 升级解耦；检查接收库及节点 UUID，不复制节点身份文件。`sync_transfer.publish_snapshot` 在取得写所有权后拍前像、替换知识，随后与 publication 同事务登记；已提交批次不能事后补挂前像。同版本的新报告批次也归档旧指针；原 UUID 重试在归档前返回原回执。归档损坏、缺文件、报告发布引用缺失及知识闭包不完整均不能宣称可恢复。

`/api/reconciliation` 和 `/export`、`/reconciliation` 页面与只读 CLI 已接入。页面保留 URL 筛选及分页，刷新失败展示明确标记的上次结果，导出当前筛选的完整记录；视频来源返回对账时保留筛选。无来源时只有静态文字。绑定状态与决定合法性分开，无效动作或旧版 `revised_claim` 不再被当作有效决定。对账审查同时修正既有 `review_history/projection` 按 ISO 字符串排序的问题：现在统一按有效 UTC 时刻、事件 UUID、旧记录 ID 排序，等时不同 offset 有稳定结果，不改变原始事件内容。

| 检查 | 实际结果与边界 |
|---|---|
| 归档、迁移专项 | Windows 59 passed / 1 POSIX-only skipped；Linux 60 passed；报告指针缺失的最终追加断言两平台各 1 passed |
| 发布与只读 CLI 接线 | Windows 39 passed；包含提交前不可见、回滚孤立文件、同 UUID 去重、旧知识/报告前像、个人/事件保留及缺库不初始化 |
| 对账、排序、可见性及夹具 | Windows 88 passed；Linux 对账/排序 36 passed；包含真实 SQLite 并发快照、时区顺序、非法决定和云端隐藏历史 |
| 完整后端 | Linux 1614 passed / 10 skipped，236.92 秒；日志 `data/phase2-linux-full.log`。跳过为 4 Windows-only、4 真实模型探针、2 root 专项（S07 已另跑） |
| 最终运行时能力门增补 | Linux 53 passed / 1 root-only skipped，15.90 秒；日志 `data/phase2-linux-final-targeted.log`。在完整回归后补充 Python/SQLite 实际能力检查，覆盖接口缺失、编译实现不可用时无部分发布；未重复完整回归 |
| 前端 | typecheck 及全套 135 passed / 27 文件，13.62 秒；最终页面专项 21 passed，ESLint、production build 通过 |
| Chromium 真后端 | 8 passed，10.2 秒；375/1280 亮暗、分页、回链、39 条完整导出、匿名拒绝、传输故障保留旧状态。输入为合成夹具，不是真实施工记录 |

浏览器第一轮受受限环境 `spawn EPERM` 阻止启动，在获准环境重跑通过；桌面 tabs 数字折行在截图检查中发现并修复。最终截图/Markdown 位于系统 TEMP `reno-reconciliation-browser-969cfd89332a42a6bbc49537904f0c99/playwright-results/`，测试端口 54304 及所属进程已关闭。CI 增加对账浏览器及 Python 3.11 归档专项，尚未提交、推送或远程执行。声明最低 Python 已从 3.10 调整为 3.11，实际本机与 Linux 验证均为 3.12；部署在系统及 venv 解释器实际验证 SQLite 内存往返，不采用不受控文件路径的兼容回退。

**真实库维护。** 先在副本演练 schema 6→7，再于 02:35 核对精确 8765 进程/监听，停服务并持 worker 文件锁，备份数据库、节点身份及个人附件后升级。PowerShell `Stop-Process` 出现自身异常且服务仍在运行，重新核对同一进程后用 Python 终止，未在服务运行时迁移。37 张原有非 meta 表的全部旧列保持，meta 仅 `migration_version` 改为 7；重复迁移不改动，完整性 `ok`、外键问题 0，新 checkpoint 表为空。恢复点 `data/maintenance-schema7-20261001T023523+0800-6a5422c2/`，自动 DB 备份 `data/reno.db.bak-migrate-20260930T183523365483Z-eba603d0`；恢复包不含原视频。

正常 `reno serve` 已恢复。重启后排除 worker 租约的 36 张原表、配置、身份、个人附件哈希及 sync-state 保持，28 视频 / 349 原子 / 16 聚类 / 13 冲突、“防水”49 条、任务 47 cancelled / 29 done 均保持。正常 API 校验了 PDF 字节/SHA、视频 Range 206 和对账 JSON/导出；真实浏览器 375×812 展示 1 条 `legacy_unbound`，无横向溢出、无 Mock 标识，没有生成新决定或现场检查。本机仍 all、同步关闭且未配对，不生成虚假的同步批次。最初读取仍是未过期的旧 worker 租约，首个同主续期断言因此失败；等待正常换主后已确认新 owner 取得租约，并在下一次采样保持同主且续期，没有清除或篡改租约。维护及读取证据在恢复目录 `result.json/runtime.json/lease-acquired.json/renewal.json`。

**本批限制。** 没有开放恢复执行、持久暂停/续传、stale 派生、保留代数或垃圾回收。归档最大 256 MiB，媒体/报告仍依赖本机不可变文件；列表逐条深核验可能耗时，不宜轮询；创建时已有缺损保守保留为不可恢复。跨分页与独立导出不是同一个长期冻结快照。未调用真实平台或模型、未进行生产云部署、未提交或推送。整体目标继续按 P03–P06/T15/T16 推进。

### 13.14 P03 恢复与显式续传合同

本批实现知识域恢复及其完整同步协调。下面是已落地的实施合同，验证和真实库维护结果见 §13.15。

**本地执行与事务。** cloud 本机 CLI `sync-restore <checkpoint_id>` 默认为只读预览，`sync-resume` 默认为读取版本与控制状态；写操作必须显式 `--apply`，并提供稳定操作 UUID、预览时的库 UUID/epoch/revision 以及控制 revision。命令不初始化或升级数据库，不提供远程恢复 HTTP 入口。`sync_recovery.restore/resume` 要求无调用方未提交事务，自持 `BEGIN IMMEDIATE`、最终依赖复验和 COMMIT；操作号与完整请求参数绑定，丢回执后以相同参数/操作号重试不重复恢复。

恢复只整体替换受控知识列、`step:` 元数据与报告指针，清理较新知识成员、重建 FTS、回放**当前全部原始复核历史**。个人十表、附件、节点身份、配对、队列、任务、处理记录、发布历史、复核事件与游标保留；`library_epoch` 不变，`kb_revision` 单调增加。报告保留原始生成版本及发布关联，依既有版本比较显示过期，不将旧报告伪装为新生成。目标归档、全部媒体与报告使用同一受保护句柄检查并持有至 COMMIT；失败不写成功回执。

每次恢复前生成独立的知识保护点，可用 `checkpoint_kind=recovery` 再次恢复。保护点不伪造 `sync_publication`：格式 2 用显式 recovery origin / operation_id，共用受控归档构建器；格式 1 的历史发布前像继续可读。保护点登记与恢复操作同事务，失败残留文件不成为可选恢复点。

**schema 8 与控制状态。** 仅新增 `sync_recovery_control`、`sync_recovery_operation`、`sync_recovery_checkpoint`、`sync_publication_fence`。无控制行表示 `running/generation=0/revision=0`；显式迁移不自动暂停、恢复、发布或重配对。握手摘要增加 `recovery_fence_version=1`，返回仍是原三键，双方升级后须显式刷新合同。

| 动作 | 控制结果 | 知识发布及队列 |
|---|---|---|
| 恢复知识 | `generation += 1`、`revision += 1`，进入 `held` | 禁止知识发布；新旧认领均不返回；心跳续租和双向复核继续 |
| 本机明确恢复同步 | generation 不变、revision 加一，进入 `resume_pending` | 家庭端生成新的完整批次；确认前仍不领取新工作 |
| 新完整批次发布提交 | generation 不变、revision 加一，进入 `running` | 与发布同事务生效，随后恢复普通认领 |

`source_floor` 保存已配对家庭库在云端保留发布历史中的最高完整版本。恢复续传必须使用高于该下限的来源版本、新 publication UUID 和匹配的 `recovery_generation`；同时覆盖当前所有 ready 视频，包括零原子视频。家庭 epoch 落后时不能通过擅改 epoch 绕过检查。新请求对旧 UUID 重贴 generation 也不能成功：每个 publication 有独立 fence 记录，存量缺记录只视为 generation 0。generation 0 沿用原载荷摘要，以保持升级前丢回执重试的兼容性；后续代次摘要明确包含 generation。

heartbeat/pull 响应追加 `{recovery:{stream,generation,revision,state,source_floor}}`，其中 `stream={library_id,library_epoch}` 是接收方知识库身份及恢复代次；snapshot 和 fail 请求顶层必须带原操作冻结的 `recovery_stream` 与 `recovery_generation`，generation 0 也不能省略，成功回执形状保持不变。暂停/待续传期间拒绝 fail，恢复前在途的旧代失败通知即使迟至新一代 running 才到达也不能改变旧认领；不能仅依赖家庭端发送前检查。家庭状态在同一接收流内以单调 control revision 合并观测，同版本内容冲突拒绝、迟到响应不回退；心跳只合并租约与恢复观测，不覆盖主线程的待发批次、处理进度或事件游标。发布回执和认领列表对工作状态的条件检查与合并必须在同一状态锁中完成，避免刚观察到新暂停就被旧响应清掉进度。恢复确认代次单独持久化，避免丢回执及迟到 `resume_pending` 造成重复重建。

**物理整库恢复的接收流边界。** 云端整库灾备恢复增加 `library_epoch` 后，控制 generation/revision 即使随数据库回退，也属于新的接收流。家庭端接受同库更高 epoch 的新命名空间，并拒绝迟到的旧 epoch 观测；按既有 S05 合同重扫事件并重建完整发布。旧 outbox、恢复尝试和确认保留其原接收流，不能重贴新 epoch 再提交；存量缺少绑定的记录仅在已有入站游标能证明原接收流时迁移，否则保守保留并重新发布。云端在 snapshot/fail 中先校验接收流，旧流返回 `SYNC_RECOVERY_STREAM_MISMATCH`；已发布 UUID 的重试也必须通过原回执 `local_version_json` 的接收方 epoch 校验，不能用灾备前的回执完成灾备后的队列。

**途中暂停的工作。** 旧 outbox、claim 和 work 先保留，不因 hold 调用 fail/complete。如果暂停发生于提取中途，明确续传后只允许在原 worker 锁下，凭已有下载及 SourceMap 的节点、URL、视频与实际 SHA 证明，继续原流水线步骤和原先未完成的 judge；不下载新 URL、不删除部分原子、不伪造 processed。先用 `claims=[]` 完整重发知识，确认后再向云端取得权威认领；原 item/URL 匹配时复用已完成成果并绑定实际 claim generation，失配的本地 work 留存不可变记录。缺依赖或版本不满足时呈现 `recovery_blocked`，不将失败伪装成队列完成。

采集页分别展示“恢复后知识同步已暂停”“等待重新发布完整知识”和家庭端待处理状态；连接在线与知识暂停是两条独立信息。页面没有恢复写按钮，暂停时仍保留队列和处理进度说明，并降低轮询频率。P04 的关联 stale 派生及 P05/P06 回收、配额保持后续独立范围。

### 13.15 P03 实现、恢复场景与本机维护（2026-10-01）

§13.14 已实现。恢复与续传 CLI 默认只读，显式执行持有自己的写事务，操作 UUID 与两个版本绑定；保护点、恢复控制及结果回执原子提交。文件句柄持有到最终提交，Linux 同长度原地改写、Windows 文件共享保护均有对应测试。整库灾备恢复的接收方 epoch 与逻辑恢复 generation 分开；旧失败通知、发布回执与家庭状态更新都不能越过原 stream。家庭端保留旧 outbox 和工作证据，恢复续传用新版本及 UUID 完整重发；不能证明原接收流的旧文件保守留存并重建发布。

| 检查 | 实际结果与边界 |
|---|---|
| 恢复 domain | 最终 Windows/Linux 各 40 passed / 2 另一平台专属 skipped；Linux `data/p03-domain-linux.log` |
| 云端 HTTP、fail、queue、publication | Linux 212 passed；Windows 最终恢复门控 37 passed，覆盖实际写锁等待、旧接收流、旧回执和提交失败回滚 |
| 家庭状态、同步器、客户端、HTTP | Windows 349 passed；Linux 最终 349 passed，57.32 秒，`data/p03-home-linux-final.log` |
| 本地 CLI | Windows 8 passed；无库不初始化，预览不写，CAS 拒绝陈旧预览，同操作号重试不重复恢复 |
| 完整后端 | Linux 首轮 1753 passed / 12 skipped / 1 failed，321.23 秒，`data/p03-linux-full.log`；失败仅为新测试手工旧 outbox 默认 0644，被现有 POSIX 权限门拒绝。夹具改为 0600，生产门未改；随后上列 7 模块 349 项全部通过，包含该失败项，未重复完整套件 |
| 前端 | typecheck、全套 142 passed / 27 文件、构建通过；最终错误提示修改后专项 33 passed，ESLint 和构建通过 |
| Windows 实际双进程恢复 | 专项 1 passed，71.93 秒；独立 CLI、HTTP 和 worker，旧成员清理、重启保持暂停、暂停中复核继续/队列不认领、完整重发、丢回执原 envelope 重试及个人域保持 |
| Chromium 实际后端 | 5 passed，16.7 秒；375/1280 明暗、暂停与续传状态、队列保留、无远程恢复按钮及横向溢出。专用配置缺少恢复标记时已实际验证受控失败，避免整组跳过的假绿 |

场景平台、下载输入与模型响应采用明确合成样本，不代表真实平台或模型验收。截图已检查，恢复场景根为系统 TEMP `reno-sync-chain-f5050b1b0cf945d4bcd5b2b98e2ecbf7/`；仅公开 `evidence.json` 和截图，私有配置与日志留本机。浏览器结束后家庭端已完成续传，测试云通过实例校验停止。CI 新增专用恢复场景和浏览器 job，尚未远程执行。domain/home/cloud 与 root CLI/场景的独立审查已完成，发现的专用浏览器入口假绿问题已修复并定向复核。

**真实库维护。** 副本演练保持 38 张原表后，于 03:29 核对 8765 的确切进程，停服务、持 worker 锁、保存数据库/个人附件/节点身份恢复点，再将 schema 7 升至 8。meta 仅迁移版本变化，四张新增恢复表为空，重复迁移无改动，完整性检查通过。恢复点 `data/maintenance-schema8-20261001T032924+0800-dc663f98/`；自动备份 `data/reno.db.bak-migrate-20260930T192924898029Z-69da02d7`，不包含原视频。没有对真实库执行历史知识恢复。

正常服务已重启并加载 schema 8，仍为 all / 同步关闭 / 未配对。重启后除 worker 租约外的 37 张原表、配置、身份、sync-state 和个人附件哈希保持；28 视频 / 349 原子 / 16 聚类 / 13 冲突、搜索“防水”49 条、47 cancelled / 29 done 保持。正常 API 核对 PDF 字节及 SHA、视频 Range 206、对账 JSON/导出；375px 实际浏览器采集页显示同步关闭。旧 worker 租约自然到期后由新 owner 接管，未清除或修改租约；后续续期证据保存在同一恢复目录。

非验收剩余为 P04、P05、P06、T15、T16。没有调用真实平台或模型、部署生产云、提交或推送；整体目标继续保持未完成。

### 13.16 P04 关联判定依据合同与实施批次

**目标与取舍。** 现有稳定 cluster/conflict ID 只含成员 ID，不能证明判定依据未变化。采用逐关系的生成时输入绑定，避免仅绑定整个库 revision 导致无关变化误报；也不把 stale 写入由人工复核回放维护的 status。完整恢复同代原子、关系和绑定后可以重新匹配为 active；§4.5 的回滚提示只适用于依据不匹配的关系，不对完整一致恢复一律报过期。

**持久数据。** schema 9 给 `knowledge_cluster` 与 `conflict_case` 各加可空 `derivation_json`，结构为 `{version:1,inputs:[{atom_id,fingerprint}]}`，排序且精确覆盖该关系成员。独立输入指纹覆盖 `atom_snapshot` 及 category/space/subject，排除人工 status、模型评分和时间。只在真正 judge 成功后产生绑定；迁移、同步接收、旧归档恢复不伪造历史绑定。judge 先冻结完整原子集，用该集合构造候选并请求模型，再在写事务内核对输入集合未变后提交结果与绑定。

**独立读状态。** 对外统一 `derivation={state,reason,changed_count,missing_count}`；state 为 `active/stale/missing/unknown`，分别表示输入匹配、内容变化、成员缺失、缺少可信生成依据。缺失 DTO 的旧保存结果也按 unknown 展示。人工决定及历史照常按自身内容指纹回放，关系状态不覆盖个人选择；非 active 的旧关系不再派生当前 has_conflict/disputed，另有 `has_outdated_conflict` 提示。复核旧关系时明确提示只记录个人意见、不会刷新判定依据，不强迫用户为保存意见调用模型。

**同步与归档。** wire 白名单与结构指纹包含新列，缺列旧 outbox 仍可规范校验且不改其原字节/UUID/hash；新导出对 NULL 省略新字段。新归档采用 schema 9 列合同，历史 schema 7 的 format 1/2 继续按原 DDL 解码。旧档恢复的新列留空，新档原绑定完整保留。来源不匹配的合法绑定可以同步，不放宽引用闭合规则，也不把 stale 关系自动重新认定为 active。

**页面与消费边界。** 争议、问答、对比、工作台及 classic 支持页显示独立提示；只有 active 关系参与当前冲突补充、聚类统计和报告推断，历史关系保留但明确待核对。重新生成报告不会消除关系待更新事实，正文与元信息均记录关系摘要；复制/下载保留提示。人工“更新争议”复用现有持久任务、幂等请求与 worker 锁；all/home 可执行，cloud 提示到家庭节点操作，下一次完整同步发布更新结果。不新增云端远程维护队列，不在读请求、恢复或状态计算中自动调用模型。

| 批次 | 文件责任 | 最小验证 |
|---|---|---|
| 领域与生成 | `derivation.py`、schema/migrations、judge/dedup、db/review及对应测试 | 同ID内容变化、无关变化、缺失/旧记录、冻结输入CAS、复核状态独立、失败不覆盖 |
| 同步与历史兼容 | protocol/snapshot/checkpoints/recovery/transfer及对应测试 | 新绑定往返、旧outbox原hash、真实v7归档恢复unknown、v9完整恢复active/stale、严格闭合 |
| 消费面与UI | ask/report/API/classic、前端类型/共享提示/四页/工作台及任务缓存 | 非active不当当前结论、历史仍可读、报告下载提示、显式任务去重/状态更新、375/1280明暗 |
| 集成与维护 | 隔离夹具/场景、CI、维护记录及进度文档 | Python/前端相关及完整回归、真实后端浏览器；副本迁移后按授权备份维护真实库，禁止真实模型重算 |

### 13.17 P04 实现、全量回归与本机维护（2026-10-01）

§13.16 已落地。新关系绑定与结果同事务提交，旧记录保留 NULL；真实 judge 输入冻结、完整候选判词检查与提交前 CAS 已接入。成员内容、分类或证据变化会使关系独立过期，人工意见按原内容指纹回放。只读投影不会改写旧库状态；搜索按同一投影过滤并保持过滤先于分页/检索上限，不把保留的旧 `disputed` 当作当前争议。

问答仅从依据匹配的聚类补充兄弟原子，历史提示同时覆盖没有聚类的独立冲突；保存回答/比较保留当时状态。报告只用依据匹配的关系做当前归类，未确认关联单列，不称为“全新观点”。历史分析仍可阅读与复核，但保存个人意见不刷新模型判定依据。报告 `reports-v3` 固化生成摘要；浏览器、复制及下载另加当前资格说明，明确正文没有因提示自动重算，不修改不可变原文或 SHA。旧 v2 报告的已冻结发布仍可原 UUID/字节重试。

| 检查 | 实际结果与边界 |
|---|---|
| 领域、迁移、复核与生成 | Windows/Linux 领域各 75 passed，含真实写失败回滚、输入集合CAS、漏判拒绝、原人工决定与10k规模；Windows历史迁移组合83 passed。Linux `data/p04-domain-linux.log` |
| 同步与历史档案 | Linux 444 passed / 2 平台跳过；Windows最终专项113 passed。真实 schema 7 format 1/2 档案可恢复为 unknown，schema 9 active/stale 绑定完整往返，旧 outbox/v2报告原hash重试 |
| 消费与可见性收尾 | root消费、cloud报告和对账73项通过；events/visibility最终两平台各96 passed，保留隐藏时全空、可见摘要精确计数、原文SHA和跨报告一致快照断言 |
| 最终完整后端 | **Linux 1836 passed / 12 skipped**，321.04秒，`data/p04-linux-final-full.log`。平台差异、真实模型和root条件项按既有约束跳过 |
| 前端 | 完整 Vitest、typecheck、ESLint、production build通过；当前29个测试文件全通过。旧DTO按unknown、云端能力门、提交未确认重试同请求、任务完成读缓存刷新及报告刷新失败均有专项 |
| Chromium真实后端 | 最终 **7 passed**，11.8秒；375/1280明暗、四种状态、问答/对比历史提醒、最新报告仍有待更新依据且下载保留提示、显式生产judge任务及旧复核事件保留 |

首轮全量为 1824 passed / 12 skipped / 8 failed：旧测试将 API 呈现文本等同原始报告、事件夹具未绑定却假设当前 disputed，以及扩展夹具的基础 atom_count 兼容问题。保持原测试意图补齐真实绑定/资格前缀/原文SHA/OLD和NEW一致快照断言，基础样本数量保留原义，另加 total_atom_count；没有通过修改生产权限门或过滤历史来使测试变绿。消费审查修复了“未知关系即全新观点”和独立冲突未进入模型限制两项问题；最终完整回归覆盖这些修正。

隔离浏览器根为系统 TEMP `reno-p04-browser-final-c822018a80db4ee7866138a87afd5202/`。模型判词为显式合成替身，真实任务、worker、judge 和绑定生成没有替换：只读流程中判定调用数为0，用户点击后为1（21个候选），新结果为active，旧个人事件ID保持。专用配置对缺少P04标记的负例已实际拒绝，避免整组跳过假绿；截图与合成下载已保留，两个测试端口49477/53374均已确认关闭。CI增加专用job，尚未远程执行。

**真实库维护。** schema 8→9 先在副本演练，原42张表全部旧列、配置、身份、sync-state和附件保持。04:11核对精确服务进程，停服务、持worker锁、备份后升级；两新列全部NULL，旧知识版本与人工状态不改写，重复迁移无改动。恢复点 `data/maintenance-schema9-20261001T041108+0800-81bd55ec/`；自动备份 `data/reno.db.bak-migrate-20260930T201108721602Z-7abe10e5`，不含原视频。没有执行真实judge或补造生成时依据。

正常8765服务已加载schema9。除worker租约外41张旧表及个人附件哈希保持，28视频/349原子/16聚类/13冲突、47 cancelled/29 done、“防水”49条不变；16聚类/13冲突均显示unknown，1条旧未绑定复核保持待核对。真实API核对了PDF、Range、对账与关联摘要；375px正常浏览器显示13条未知依据提示和显式更新入口，无Mock标记、无横向溢出。租约接管与续期证据保存在同一恢复目录；同步仍关闭且未配对。

本批没有调用真实平台/模型、部署生产云、提交或推送。P05/P06、T15/T16继续推进，整体目标未完成。

### 13.18 P05 实施前只读核对（尚未实现）

下一批的文件回收不能只依赖“数据库没有引用”。已确认上传完成到snapshot之间、报告文件写入到manifest切换之间存在在途窗口；SQLite写锁和worker锁不能独自覆盖这些窗口。API帧缓存也只有线程锁，FileResponse延迟打开文件，需先处理跨进程读取与淘汰竞争，再开启在线回收。

候选根包括全部当前知识（含未ready）、保留归档内的媒体/报告、当前报告指针/manifest、恢复保护点、家庭pending/recovery_attempt/work及未完成任务；坏manifest或无法证明的引用必须保守保留。发布/队列/事件/恢复回执继续保留用于幂等与版本约束，不能随媒体一起删除。历史发布记录可证明文件曾由系统创建，但不能全部永久作为媒体保护根，否则无法回收。

实施合同需明确共同文件生命周期锁或持久在途pin、保留N代与额外保护点、只读预览及版本摘要CAS、退休/删除journal与崩溃恢复。未知文件、真实DB/WAL/备份/维护材料、身份配置、个人附件、source maps、家庭工作历史不纳入自动删除；旧outbox在没有明确退休合同前保留。帧LRU只管FRAME_CACHE，不能混同MEDIA/frames处理证据；需显式访问记录而非依赖OS atime。这是实现前事实核对，不代表P05已交付。

### 13.19 P05 帧缓存子项合同（已实现）

P05 分两步交付：先完成可再生 API 帧缓存的容量和并发治理，再接入知识文件的在途保护、归档保留与引用回收。帧缓存完成不能等同于整个 P05 完成。

帧缓存独立于知识库，访问记录不增加 `kb_revision`，不改 schema。缓存键仍绑定视频、内容版本和毫秒位置；持久索引记录实际文件身份、大小、SHA 和访问顺序，按 `(last_access,key)` 确定性淘汰。所有提取、命中读取和淘汰经过同一跨进程锁；API 在保护范围内取得完整 JPEG 字节再返回，避免 `FileResponse` 延迟打开已经被淘汰的路径。缺失或损坏的受管缓存可重新提取；来源视频的公开资格与存在性仍在每次请求中先检查。

`frame_cache_max_bytes` 默认 512 MiB，可通过 `config.local.json` 或 `RENO_FRAME_CACHE_MAX_BYTES` 设置，0 表示本次帧仍可读取但不保留受管缓存。容量仅针对新索引明确登记的缓存内容，不把历史未知文件、元数据或处理证据帧算作已经受控的空间。旧未登记缓存和任何无法核实所有权的文件保留，不凭文件名清理，也不冒充实际释放空间。

`reno frame-cache` 默认只读预览，不创建数据库、缓存目录、锁文件或索引，不刷新访问顺序。`--budget-bytes` 仅覆盖本次命令的预算；执行须显式传 `--apply --expected-digest <预览摘要>`，锁内复查预算、索引访问事实及文件身份。并发命中或文件变化使预览过期，先重新预览再执行；删除失败保留可重试登记。此入口只处理 `FRAME_CACHE`，不清理原视频、`MEDIA/frames`、报告、知识归档、个人附件、备份或配置。

### 13.20 P05 历史知识文件回收接入合同（尚未实现）

**统一保护与事务顺序。** 普通媒体/报告/归档读写使用 shared 生命周期锁，GC 使用 exclusive；Windows 使用真正支持共享语义的 `LockFileEx`，POSIX 使用 `flock`。生命周期锁覆盖共同的物理存储根，不能只按数据库文件划分：两个 DATA 目录可能共享其父目录下的媒体。锁文件不自动删除；只读预览不创建锁设施。顺序为既有 worker/StateStore 锁（若已持有）→生命周期锁→SQLite 事务→文件句柄，GC 不反向等待 worker/StateStore。已有事务的底层函数只检查调用方已持 guard，不能临时补拿锁或将 shared 升为 exclusive。

`sync_recovery.restore()` 自己提交，`sync_transfer.publish_snapshot()` 由调用方提交；后者的 guard 必须由 HTTP `_push_snapshot` 外层保持到 `_authorized_commit`/rollback 之后。报告指针读取、归档列表/预览、家庭端冻结发布必须在 BEGIN 前取得 guard。生成报告时仅在落文件和切 manifest 阶段持锁，不跨越模型请求。完整视频响应先在保护内打开受保护文件句柄，随后按句柄流式返回，取消/断连均关闭句柄；Range 在保护内完成有界读取。不能继续在释放保护后交给 `FileResponse(path)` 重新打开。

**持久在途依据与协议升级。** 现有媒体上传只有 source/lease/descriptor，不能证明其属于哪一个 outbox。因此不改协议时只能保守登记永久 hold，不能靠 TTL、会话过期或当前快照遗漏安全释放。完整方案扩展 upload intent，绑定 source node、publication UUID、原 source version、target stream/recovery generation 和精确媒体 descriptor；以规范摘要生成幂等 pin，握手规则同步升级。接收前短事务登记 reserved，网络上传期间不持 SQLite 写事务，完成后短事务复查并登记 stored。只有完全匹配的 publication 成功提交才在同事务转 published；晚到旧上传不得重开终态。无法证明完成或撤销的 pin 永久保护。

若开放主动弃用，家庭端先持久化不会再发该冻结批次的事实，cloud 收到精确绑定、幂等且鉴权的 abandon 后永久 tombstone 同一意图；晚到上传、发布或旧 outbox 灾备重现均不能复活它。该语义不能用“任务失败”代替。已发布批次的回执重试，应先核对原身份、载荷、fence 和 claim，再回既有回执，不能因为历史媒体已经合法退休而使幂等重试失败。

**后续 schema 与 journal。** 拟增加空表 `storage_artifact`（可信创建登记）、`sync_upload_pin`、`sync_checkpoint_retirement`、`storage_gc_operation`、`storage_gc_item`；具体迁移版本在实施时确定，本批不升级真实库。所有权登记仅证明由系统创建，不证明可删除。历史归档事实保留，退休状态独立记录；恢复入口明确返回 `SYNC_CHECKPOINT_RETIRED`，不能把主动退休混成损坏。publication/recovery 各保留最近 N 代（默认3、最小1，按created_at/id确定顺序），当前恢复保护点、最近可恢复点及其他引用根可使实际保留数超过 N。

预览生成根摘要、候选身份和策略摘要；apply 携带操作 UUID 与预览摘要，exclusive 内重算。先提交 operation/items 和退休记录，再逐文件核对身份、SHA、大小与当前引用并删除，最后登记字节删除结果。SQL 退休成功不等于空间释放成功；Windows 已打开句柄阻止删除时标记 blocked 并允许重试。崩溃续跑先重算当前根，新引用出现则 retained，不机械重放旧清单。同一操作号不同参数拒绝。必须验证真实整库备份的依赖；无法核验时对相关 namespace 保守停删，不能只保留 `.bak` 文件就承诺备份可恢复。

实施顺序为生命周期/schema基础→协议pin与发布回执→归档退休/GC journal→所有读写消费者接线→并发故障验证，最后才开放 apply。此前 §13.18 的未知文件、家庭状态历史、个人附件及维护材料排除规则继续适用；新框架不能代替逐个读写入口完成接入。

### 13.21 P05 帧缓存交付与正常服务重载（2026-10-01）

§13.19 已完成，§13.20 保持未实现。`reno/frame_cache.py` 使用私有 JSON 索引和 OS 锁，命中仅读取并解码本次帧，不全量解码缓存；字节预算与10000条索引上限均按访问顺序腾位。JPEG限制16MiB/2000万像素，缺源版本或单帧超过预算时仅返回本次字节。损坏登记不覆盖原文件，失败提取按本次创建的文件身份清理；无法证明的崩溃残留保留。Windows路径stat与句柄fstat的ctime语义差异、只读预览共享打开冲突均经实际探针修复。

| 检查 | 实际结果 |
|---|---|
| 缓存领域 | Windows18 passed / 1 Linux专属路径skip；Linux19 passed，`data/p05-frame-cache-linux.log` |
| API/CLI及既有读取 | Windows68 passed，含真实FFmpeg8请求只提取1次、响应JPEG一致/完整、ready资格、源文件删除、0预算与知识版本保持 |
| 最终定向收尾 | Linux四模块87 passed，25.27秒，`data/p05-frame-linux-focused.log`；覆盖最终私有目录/路径校验及domain/API/CLI/可见性/健壮性 |
| 完整后端 | Linux **1870 passed / 12 skipped**，334.41秒，`data/p05-frame-linux-full.log`；真实模型及既有平台条件项保持跳过 |
| 前端 | typecheck通过；完整Vitest **155 passed / 29文件**，本批未改前端，不重复P04浏览器及构建 |
| 独立审查 | 无剩余阻断；Windows正常JPEG与锁繁忙实际等待探针通过，POSIX同账户主动绕过锁的权限边界已写明 |

正常服务于04:38重载并验证，记录在 `data/service-p05-frame-20261001T043322+0800-05534641/`。先保存只读前像，复查8765端口、确切PID及本仓venv父进程后停止旧实例并启动新实例；无schema迁移、配置写入、真实模型或云端请求。除worker租约外41张原表、meta、个人附件、节点身份和同步状态文件逐项保持，schema仍为9。旧18个缓存文件字节保持；真实API返回576×1024、79088字节JPEG，再次命中字节一致；只新增1条受管缓存登记，未执行真实历史文件清理。正常服务继续为all、同步关闭。

非验收剩余：P05历史媒体/报告/归档回收、P06节流与预算、T15报价试验和T16个人跨端/分享；生产云部署尚未执行。整体目标继续保持未完成，未提交或推送。

### 13.22 P05 生命周期与持久意图基础（线上接入待完成）

§13.20 的基础层已实现：`storage_lifecycle.py`、`storage_schema.py`、`storage_artifacts.py`、`sync_upload_pins.py`。本批新增 live schema10，知识归档自身仍使用 schema9、兼容7/9；五张 operational 表不进入知识快照、归档或个人域。迁移只建空表，不猜测旧文件归属，不将现有上传伪造成已绑定pin。

生命周期profile显式包含database/media/reports/checkpoints；锁覆盖数据库父目录与所有物理根，按统一顺序取得。`initialize(profile)`先预检所有既有目录、DB叶文件和带固定marker的私有锁，再以完整临时文件原子无覆盖发布；未知同名锁不覆盖。`shared`/`exclusive`从不初始化设施，Windows使用LockFileEx共享/独占，POSIX使用flock；timeout=0也会立即尝试一次。嵌套只复用同profile，禁止锁升级、其他线程/task/进程借用guard；取消、超时与进程死亡释放OS锁。POSIX没有原子no-replace rename支持时受控失败，不用短暂nlink=2的发布方式替代。

CLI新增 `storage-init` 与纯只读 `storage-status`。不传路径时使用当前节点四个配置根；自定义必须同时提供 `--db --media --reports --checkpoints`，不能为一份临时库借用现用媒体目录。显式初始化先核验现有schema并关闭连接，不替代init-db/migrate-db。服务lifespan在数据库校验结束后初始化设施；CLI写命令在worker启动前初始化，纯读取分支不经过此步骤。正常DB连接仍只验证，SQL迁移中不创建文件锁。

五表采用实际落地合同：`storage_artifact`按namespace/物理root身份/相对key登记内容和文件身份，`sync_upload_pin`包含publication/media/legacy三类记录，`sync_checkpoint_retirement`以(origin_kind,checkpoint_id)为复合主键，`storage_gc_operation`/`storage_gc_item`预留删除journal和结果。根dev/inode与文件身份以文本保存以适配Windows标识范围；不存任意绝对文件路径。所有新领域写入要求真实持有guard、匹配的持久数据库和已有事务，内部savepoint失败只回滚本次操作，最终commit仍由调用方负责。

文件登记仅接受可信创建方提交的受控key、SHA及大小，不通过扫描收编未知文件。同内容重传可更新新inode身份，既有GC候选必须按自己的冻结身份再核对；换内容的同一路径拒绝。pin绑定source库/节点/完整版本、publication UUID、target stream/generation和完整媒体descriptor。publication父记录充当永久墓碑，允许上传前显式abandon；同UUID换意图、同视频换descriptor、终态重开均拒绝。reserved即作为保护根，无需等待文件出现，legacy_hold不随时间释放。

首次完成pin要求真实publication/fence/全量video回执，且当前ready视频指针必须精确指向该descriptor对应文件；不能把“相同SHA但另一扩展名的文件”当作本次发布内容。所有子pin须stored且精确覆盖全量媒体，同事务转published。已published的原请求回放依赖持久事实，不要求已经合法退休的历史媒体重新存在；网络层仍须先校验原身份、payload、claim与恢复边界。

显式abandon不会发布内容或改变恢复控制。已存在且完全匹配的旧intent，可以在同receiver库的更高epoch/generation或held期间永久弃用；未曾登记的意图仅允许当前stream/generation，未来代次或其他库拒绝。幂等操作号不能换参数，迟到上传不能越过墓碑。发送方必须先持久化不会再发的事实；本批尚未提供网络abandon入口。

**本批运行边界：**线上媒体/发布HTTP和家庭端尚未携带intent，普通读写消费者尚未全部持生命周期guard；设施初始化不等于线上文件均已受保护。尚未实现保留策略、退休读取提示、依赖根汇总和GC apply。下一批先补全线上全量intent声明（包括跳过重新上传的已缓存媒体）、写入/读取guard和精确回执，再实现preview/CAS/journal清理；不得仅因这五表存在就开放删除。

### 13.23 P05 基础验证与 schema10 本机维护（2026-10-01）

§13.22 基础实现已冻结，独立审查发现并修复两项实际问题：首次初始化曾暴露0字节锁，现改完整marker原子发布，真实四进程并发初始化均成功；首次pin完成曾仅凭video_id/SHA判断，现在必须核对本次发布实际媒体路径，同内容mp4/mkv误配受控拒绝且原保护和调用方事务保持。生命周期锁及意图领域未绕过缺guard、内存库、源/目标身份或恢复代次门。

| 检查 | 实际结果 |
|---|---|
| 生命周期 | Windows32 passed / 1 POSIX专属skip；Linux33 passed，`data/p05-storage-lifecycle-linux.log`；真实多进程SH/EX、不同库共享media、取消/死亡释放、并发初始化与上下文边界 |
| 文件登记与上传意图 | Windows78 passed / 1 POSIX专属skip；Linux79 passed；覆盖指纹/身份替换、完整字段与媒体集合、held及恢复后的显式abandon、终态拒绝晚到上传和媒体已退休后的原回放 |
| 迁移/CLI/历史兼容 | Windows64 passed / 1 POSIX skip；真实v9 DDL升级、逐旧列保持、失败回滚、只读命令、启动初始化、旧7/9归档及旧outbox字节/hash保持 |
| 最终完整后端 | Linux **1994 passed / 12 skipped**，351.26秒，`data/p05-storage-linux-full.log` |
| 前端 | typecheck和完整Vitest **155 passed / 29文件**；本批无前端修改，不重复已有浏览器/构建 |
| 独立审查 | 两项P2均以TEMP独立复现并复验修复，已审范围无剩余阻断；线上接线缺口仍明确保留 |

副本演练目录 `TEMP/reno-v10-rehearsal-5ifsd6f7/`，指针 `data/sync-v10-rehearsal-result.json`。05:05复查8765端口对应进程及本仓venv父进程，停服务、持worker锁、备份后将schema9升级10。恢复点 `data/maintenance-schema10-20261001T050500+0800-1f26a17e/`，自动备份 `data/reno.db.bak-migrate-20260930T210501000551Z-2f2aa65b`；恢复材料包含DB、个人附件/清单与节点身份，不包含原视频。

迁移保持原42张非meta表全部旧列；meta仅migration_version变化，重复迁移不再修改，五表为空。服务重载后除worker租约外41张旧表、meta、身份、配置、sync-state、规范PDF字节/SHA保持，锁设施已初始化。真实API验证28视频/349原子/16聚类/13冲突、47 cancelled/29 done、防水搜索49条、视频Range206和未配置同步401；角色仍为all，同步关闭、未配对。runtime证据在同一恢复目录；旧worker租约自然到期后已核对新owner取得有效租约，`lease-acquired.json`记录结果，没有手工清除租约。

本批没有登记真实历史文件、创建真实上传pin、执行GC、调用真实模型/平台或生产云部署。P05尚需线上guard/pin及回收执行，P06/T15/T16继续保留；未提交或推送，整体目标未完成。

### 13.24 P05 上传意图与在线文件保护合同（2026-10-01）

本批接入使用 **protocol 2**，核心规则增加 `upload_intent_version=1`。live schema仍为10、知识归档仍为9且读取7/9；§13.22“operational表本身不改变核心指纹”只描述上一批DDL，不代表旧二进制可以免协议升级。已有配对须双端升级后，在同peer且空闲的条件下显式 `sync-pair --refresh-schema`；不自动改配对、冻结outbox字节、publication UUID或原载荷哈希。当前本机同步关闭且未配对，不需要伪造配对刷新。

上传先 `POST /api/sync/uploads/prepare`，携带 `{source,lease,binding,descriptors}`；binding绑定publication、source node/source version及target recovery stream/generation，descriptors固定本批全部媒体，包含缓存命中项。prepare仅持久固定集合，不扫描全库SHA，未完成批次返回reserved；已发布且身份吻合的重试返回published及空media列表，随后仍须push原载荷核对原回执。nginx为prepare单独允许4MiB，普通控制请求仍为64KiB。

家庭端逐项 `POST /uploads/verify`，携带 `{source,lease,intent}`，服务端只核验该精确文件后返回 `{pin_id,stored}`，不会因客户端旧缓存记录跳过校验。单文件不超过512MiB，客户端总期限/读取期限与服务端取消检查均有300秒边界。SHA在连续shared guard内、SQLite写事务外完成，最终短事务再核对源/会话、终态、恢复代次、登记及物理文件身份。`POST /media`携带同一intent，收包前reserved已提交，网络流及尾部SHA不占SQLite写事务；最终登记和stored提交仍受原guard保护。

新发布在调用方事务中完成全部匹配pins，实际发布媒体路径必须与descriptor精确相符。该完成阶段复用既有 `MediaStore.hold_verified` 的真实SHA和仍打开的文件句柄，不增加第三轮全库SHA；句柄退出、guard改变或文件身份改变即失效。既有发布过程的hold入/出校验及归档事务范围仍保留，不能将本批优化说成发布长事务已全部消除。历史回执先核身份、载荷、claim和fence，再考虑媒体/报告；合法退休历史文件不会阻止原回执重放，旧缺fence行继续按既有generation 0规则处理。

`POST /uploads/abandon`保留精确binding与operation_id的显式原语，永久墓碑阻止晚到上传和发布复活。**家庭端持久“不会再发送此批次”的流程尚未接入**，不会因为失败/超时/租约过期自动abandon；现有旧outbox与未闭合pin继续保留。

报告、同步发布、家庭端冻结/读取、归档和恢复入口在BEGIN/首次指针读取前持guard，已有事务只校验调用方guard。报告模型请求期间不占guard；本地文件登记先提交再切manifest，失败保留旧指针/孤儿；同步报告登记和指针同事务。视频完整响应先打开精确句柄再释放guard，取消/断连关闭句柄；Range在guard内取得最多4MiB字节（含suffix），不晚开路径。视频和帧路径须属于本profile媒体根。读取、CLI错误不暴露本机路径。

### 13.25 P05 在线接入验证与本机重载（2026-10-01）

已完成各责任模块的定向验证与交叉审查；整合中发现并修正旧测试场景未prepare即提前拒绝、QA脚本缺初始化、nginx大集合413、旧generation 0回执兼容、媒体Range和路径根保护，以及SHA期间阻塞heartbeat/abandon的问题。首轮整合留下的9项失败均保留在`data/p05-online-linux-full.log`，没有以模块通过掩盖；修复后最终全量为 **2080 passed / 12 skipped，425.99秒**，记录于`data/p05-online-linux-final.log`。

最后proof/媒体/pin/HTTP领域在Linux为239 passed / 1平台skip，Windows为104 passed / 1平台skip；公共API/媒体/部署辅助Windows132 passed / 2 skips，补充边界56 passed。真实子进程同步与恢复场景两模块6 passed（`data/p05-online-scenarios-linux.log`），保留实际`.part`出现后断连、清理、reserved pin、不可见性、恢复暂停与新代续传断言。前端类型检查及29文件/155项测试通过。SHA barrier用例证明另一SQLite连接能执行heartbeat/abandon，取消后不能标stored；完整响应取消/断连关闭句柄，Range释放锁前已持字节，Windows打开句柄阻止删除和POSIX unlink后仍读原句柄分别验证。最终独立只读复审无剩余阻断。没有将隔离的合成平台/模型输入外推为真实平台或云部署验收。

05:58在核对精确服务PID/项目venv父进程、保存数据库与附件恢复材料后重载本机8765，恢复目录`data/service-p05-online-20261001T054956+0800-bbf53f09/`，记录脚本`data/runtime_p05_online.py`。前后46张非worker表、meta、配置、节点身份、sync-state及附件哈希保持；仍为28视频/349原子/16聚类/13冲突，“防水”49条，任务47 cancelled/29 done。真实API报告/元信息、经典报告页、PDF哈希、视频Range206及帧读取通过，新prepare/verify/abandon路由已加载。同步关闭、未配对，没有真实上传pin、历史文件登记、模型调用或GC删除。P05其余部分按§13.26继续，未提交或推送。

### 13.26 P05 引用回收实施细化（待实现）

后续按以下顺序落地，不把“所有namespace永远停删”作为完成的GC实现：

1. **成员完整性与组合保护**（`storage_members.py`及lifecycle扩展）。物理根锁只能保护正在执行的操作，不能证明另一空闲库没有长期引用。新建根登记完整profile/库身份/物理根身份；已有根标记`membership_unknown`，提供只读登记预览与显式、可审查的完整成员声明。成员暂时失联不等于退休。需要多个profile时，在任何SQL事务前一次按统一顺序持全部根锁，禁止持一个profile后递归补锁。成员版本纳入预览CAS。
2. **受保护的备份生产与依赖清单**（`storage_backups.py`及既有入口）。`db.snapshot`、judge/backfill/rerun、迁移与受支持维护入口必须在备份快照前持shared，已有事务仅require。迁移不能在已有BEGIN IMMEDIATE的 `_backup`里补锁；旧schema/缺设施走明确离线维护入口，不在SQL事务内创建设施。新备份同时冻结本地manifest与依赖清单，外部目的地必须登记。手工外部程序须遵守维护窗口和登记合同，不能宣称锁能约束未协作程序。
3. **只读根扫描**（`storage_roots.py`）。扫描全部当前知识（含未ready）、报告指针/本地manifest、保留归档、当前恢复保护与恢复目标、reserved/stored/legacy pins、pending outbox/recovery attempt/work/source maps、未完成任务、全部成员及备份依赖。无退休合同的outbox/工作历史不能擅自丢弃。损坏、缺失或不明映射按受影响namespace报告阻断；公开输出数量、原因和必要定位，不输出私人正文、URL或凭据。原历史发布/事件/恢复回执永久保留，但不能全部永久作为媒体根。
4. **保留、退休与执行日志**（`storage_gc.py`）。publication/recovery分别按`created_at DESC,checkpoint_id DESC`保留最近N代（默认3、最小1），每类额外保留最近实际可恢复点、当前恢复保护/暂停恢复目标以及其他根要求保留的归档。损坏归档不能因不可恢复就自动退休。preview纯只读；apply用operation UUID与预览摘要，在exclusive内重新核对成员、备份发现集合、引用根、策略和文件身份。先提交退休与operation/items，再删除；重试重算根，新引用改为retained，Windows句柄阻止删除时blocked可重试，实际释放字节与SQL退休分开报告。

备份发现不能仅扫描`*.bak`：默认闭集包括DATA的`<db.name>.bak-*`（排除JSON/WAL/SHM）、`maintenance-*/before/reno.db`、`maintenance-*/reno-before-schema*.db`、`service-*/reno-before-reload.db`及登记外部备份。外部显式项只能追加，不能关闭内置发现；缺文件也需阻断。冻结文件身份/SHA后以只读、`trusted_schema=OFF`及受限authorizer解析支持的旧schema；非空WAL未被可靠纳入映像时阻断。备份里的旧checkpoint行及其全部依赖仍是根。

现有旧整库备份没有本地报告manifest副本，不能从当前manifest倒推当时引用；对这些备份造成的`local_report`依赖未知保守停删，其他能证明依赖的namespace可继续工作。未知文件、DB/WAL/SHM、备份、配置/身份、个人附件、source maps与家庭工作历史本身均不进入删除候选。登记所有权不等于可删除，不能用新框架绕过旧备份与成员完整性问题。

### 13.27 P05 组合保护与成员登记（2026-10-01）

`storage_lifecycle.shared_many/exclusive_many`、`storage_members` 和 `storage-members/storage-member-init` CLI 已进入工作树。组内先按统一顺序取得去重后的全部物理根锁，再读取数据库；嵌套操作只能借用已保护的 profile 子集，不补锁或升级。文件 proof 绑定完整 profile 身份，同一物理根或同一组合 guard 不允许跨成员复用。

成员登记保存库身份、完整 profile、物理根身份、成员组与代次；多根写入以协调回执和各根本地见证确认。已有根没有登记时保持 `membership_unknown`，完整性需新预览和明确的全部成员声明，不能从“当前只发现一个库”推断独用。成员缺失不自动退休；组快照仍需核对全部成员，而普通准入的本地见证不需要打开外库。初始化只可凭实际 mkdir 后仍为空的创建证明确认新根；新成员加入已有组、身份漂移或中断写入不能绕过预览摘要 CAS。

预览不初始化数据库或产生源 WAL/SHM。无有效 WAL 时以 `immutable` 读取并前后检查源文件；非空 WAL 通过有界私有 TEMP 中的 DB/WAL 副本交给 SQLite 回放，源 SHM 不参与读取。组合大小上限 256 MiB、稳定性最多检查两次，持续变化或无法可靠复制时受控拒绝。元数据读取仅接受普通表，限制行数与 SQL 执行量。

本批完整 Linux 为 **2149 passed / 12 skipped**，467.74 秒，日志 `data/p05-members-linux-full.log`；组合锁领域 Windows 150 passed / 2 平台 skip、Linux 152 passed；成员与 CLI 收尾 Windows 43 passed / 2 skip、Linux 45 passed，连续变化拒绝两平台各 1 passed。前端类型检查及 155 项测试通过。本机真实只读预览保存为 `data/p05-members-real-preview.json`，三处根均为 `membership_unknown`，没有写入真实成员登记、完整性声明或执行清理。

服务启动准入接线正在实施：需先移除配置读取创建媒体目录的副作用，保留新根创建证明，再接入明确 setup。当前 8765 仍运行 §13.25 的在线保护版本。本批未完成备份生产/依赖、根扫描、保留与 GC；`data/p05-backups-draft/` 仍为未接入草稿。

### 13.28 P05 启动准入与受保护备份生产（2026-10-01）

`storage_admin.setup` 已接入 CLI 写命令、直接 ASGI lifespan 与恢复 CLI 的 `--apply` 分支。配置读取和 app 构造不再创建媒体目录；数据库初始化仍只负责 schema。首次创建根的证明由成员初始化取得，已有根登记自身但保持完整性未知。重复启动以自身 profile 和本地提交见证准入，其他成员离线不阻止本实例服务；新加入、身份漂移或不完整见证须重新明确登记。普通请求、成员预览及恢复预览不做 setup。

同一实例首次并发启动在整组 EX 内重新核对赢家：只有完全吻合的自身成员和完整本地见证才返回幂等成功，不写新代次或把旧根升级为 fresh。设施初始化的 preflight 与末尾 SH 共用有界等待预算，默认 30 秒，保留 marker 原子发布协议；普通 guard 和 `status(timeout=0)` 语义不变。取消回调保持原异常对象，释放锁和句柄，不将业务取消误归类为文件系统错误。

`storage_backups` 已接入 `db.snapshot`、judge、回填、原子重跑和迁移入口。快照先取得 SH；已有 SQL 事务只能借用现有 guard，独立源连接复制已提交图像，不提交或回滚调用者事务。新镜像和 `.reno-backup.json` 描述冻结 schema、源知识版本、物理根、文件身份/SHA、全部 files_json/frame.path 引用、报告原字节 manifest 观察及归档边界。`consistency=committed-db-plus-observed-manifests` 明确不表示全局同一时点，恢复仍须核对报告依据和相关文件。

源 DATA 的 `.reno-storage-backups-v1/` 先登记 reserved，再发布镜像/描述和 ready，包含外部目的地。残缺、失联、未知 schema、非空未物化 WAL 或 rollback journal 均保守阻断，不能借当前 manifest 补造旧依赖。固定历史发现集合仍始终启用；任意未登记外部副本不会被自动发现。旧备份缺 manifest 仍阻断 `local_report`，归档退休事实保留原始请求/记录，等待正式 verifier，不据 `completed` 独自认定退休。

judge 使用 UUID 避免同秒覆盖，只保留同完整 source_profile/kind 的最近三份已登记备份。先冻结 operation 候选，再记录 retiring、精确身份删除、最后 retired；短暂失败后下次 judge 先续原 UUID，不先生成新备份或扩充旧候选。其他库、其他类型和旧未登记备份继续保留。这个备份保留入口不删除知识媒体、报告或归档。

迁移在 `_open` 和 `BEGIN IMMEDIATE` 前取得 SH，`_backup` 仅借已有 guard，复制后保留完整性检查。自定义数据库必须提供完整 profile；缺设施旧库使用明确 `migrate-db --initialize-storage-offline`，普通迁移不暗建锁，当前版本 no-op 也校验既有设施。原迁移 `<backup>.json` 行数/附件说明与新的依赖描述独立，附件说明不是附件副本。完整使用方式见[部署 §5.8–5.10](deploy.md#510-受保护备份生产与保留边界p05)。

第一轮合并全量的 6 failed / 3 errors 保留在 `data/p05-members-backups-linux-full.log`，原因已分别修正：并发成员登记、初始化立即 BUSY，以及测试状态夹具复用已登记媒体。另独立审查修正 backup 的 hot journal、judge 原操作续跑和跨 profile 未完成记录误判。冻结版最终 Linux 为 **2235 passed / 12 skipped**，477.66 秒，日志 `data/p05-members-backups-linux-final.log`。相关通过结果：迁移两平台各 183，完整性收尾各 21；最后消费者/帧 Linux 158；成员/生命周期六模块 Windows 131 / 3 skips、Linux 134；取消收尾 Windows 70 / 1 skip、Linux 71；前端类型检查及 155 passed。最终独立只读复审无本批剩余阻断，没有把隔离模型/平台替身外推成真实验收。

真实库 07:26 已通过新入口生成一份 ready 受保护备份和附件/身份恢复材料：`data/service-p05-members-backups-20261001T072605+0800-b18dc516/`，记录工具 `data/runtime_p05_members_backups.py`。最终回归通过后核对精确旧 PID/项目启动器/监听，重载为 PID96824，07:50真实 API 核对通过：46 张非 worker 表及meta、配置、身份、sync-state和附件哈希保持，28视频/349原子/16聚类/13冲突，防水49条；报告、PDF哈希、视频Range206及帧读取通过，同步关闭且未配对。新 worker 已自然取得活跃租约，记录 `lease-acquired.json`；未手改租约。三根登记自身与有效本地见证，均保持 `membership_unknown`，没有声明独用或完整成员集合。描述中旧媒体路径 `media_mapping_unknown` 继续阻断 `sync_media` 判断，未擅自映射或删除。没有真实模型调用、知识重判或历史资源GC。P05 根扫描、归档保留和历史文件 GC 尚未实现，后续 `data/p05-roots-draft/`、`data/p05-readonly-draft/` 是准备材料，不能视作运行能力。

### 13.29 P05 只读映像、引用输入与冻结退休合同（2026-10-01）

本批把 R01–R04 的基础模块接入生产源码，但尚未提供完整整组根扫描、可执行 GC 预览或删除入口。归档图、最近 N 与额外实际可恢复点、旧备份退休边界的逐项解除，以及 EX 下重算、逻辑退休/删除 journal 与失败续跑，继续作为 P05 剩余实作。`data/p05-roots-draft/README.md` 仍是后续整组扫描与执行的设计材料；不能因为基础模块已存在就开放清理。

`storage_source` 统一 no-follow 源签名/读取/复制，`storage_members` 保留原错误码、读取探针、无 WAL 的 immutable 快路和原预算。`storage_readonly.committed_image_locked(profile)` 要求已有精确 guard，只在新建私有 TEMP 中解释 DB/WAL committed 视图，不触碰源 SHM、不调用配置或初始化设施。默认 DB+WAL 256 MiB、捕获至多两次，非空 rollback journal 拒绝；yield 后源变化使整轮失败并要求上层重扫。TEMP 创建物理身份和父目录身份在清理前复核，自动 finalizer 从创建起禁用，替换目录不得被误删；取消异常原对象保持。SQLite 首次 schema 查询也有长度/执行预算。此接口用于活跃成员；不能将外部备份捏成新 profile 借现有 guard，旧备份保留原 immutable/未物化 WAL 或 journal 阻断合同。

`storage_root_inputs.parse_image` 读取受限的 schema 7–10 committed 映像：全部 files_json 字段与 frame.path、不筛 ready、当前 sync_report、reserved/stored/legacy_hold pins 及父 binding、未完成 job/queue 的输入选择器。只接受普通表及所需受控字段，限制 SQL 步数、deadline、行/cell/累计字节和 JSON 对象；坏 JSON、view、virtual/generated 表达式或预算耗尽按可触及 namespace 阻断。五个 manifest 使用独立纯解析器，损坏不默认成空对象。该模块不认领文件、选择可删候选或解析完整知识归档；schema 0–6 的旧备份仍需既有 backup 读取与后续完整图处理。

Windows 非规范 `SYNC`、末尾点/空格、SHA/扩展名等别名不得当成本地非候选路径忽略。可证明的别名进入 sync_media blocker，合法 video_id 大小写保留真实 Reference。后续根与候选索引必须按 Windows 物理 key 等价匹配；不能仅以大小写敏感字符串相等判断是否仍有引用。Linux 不套用 Windows 路径别名规则。

`storage_home_roots.capture_locked/parse_bundle/scan_locked` 仅从显式 DATA 读取 sync-state、全部合法 outbox、work-history 与 source-map；不构造 StateStore、SourceMap 或网络客户端。state 缺失仍扫描历史；旧格式、pending/attempt 的唯一 outbox/hash/版本/stream 校验、来源库/节点/摘要校验均保留。source-map 与 uploaded_media 产生无扩展内容选择器，不猜 mp4；旧 outbox 收到回执后仍保其媒体依赖，合法内联报告不制造外部报告文件根。目录、文件身份和原字节在整轮结束复核，纯构造 bundle 也校验来源覆盖、固定 state 记录与预算。公开 InputScan 摘要不输出 URL、lease、正文或附件。

`storage_gc_contract` 冻结 request/policy v1 的精确键、canonical UTF-8 JSON、规范 UUID/SHA、排序唯一性、物理根/文件身份、单 owner DB 与 journaled/preserve_unowned 两种归档处置。JSON 上限 16 MiB、深度 24、节点 200 万；每批最多 10 万文件/4096 退休项；两种保留数至少 1、最多 4096，保护活动恢复和最新可恢复点不可关闭。`verify_retirement` 只从原 committed 映像读取 schema10 的普通 operational 表、原 request/hash/policy、原 checkpoint descriptor 与 committed origin join。planned/running/blocked/completed 四状态均可证明已提交的逻辑退休；物理 pending/blocked/retained 不撤销它。签发 DB 身份不能用备份的新 inode 替代，当前 epoch 也不替代原映像。未证明仍保留边界；无所有权的 archive 不生成删除项。

证明查询只投影必要不可变事实，不读取 result_json、状态展示时间/原因或 artifact 今天的可变文件身份；这些字段即使是 BLOB 或超长值，也不得否定原请求和冻结 journal。原 descriptor 的 library/member/root 身份由后续扫描提供并核对。backup discovery 仍保守输出 retirement_unproven；完整根图尚未接线，不能提前整体清除旧 blocker 或把当前退休借给旧备份。

原 DB/WAL SHA 与 Home rawbindings 只证明一轮读取一致性，不是 GC CAS。后续 roots/preview 摘要需绑定完整成员、物理根、引用/保留资格投影、manifest/归档/备份集合与候选身份。真实 schema 的组合测试已确认：未提交 WAL 指针不可见且写入者事务保持；纯 worker lease/display 更新时间改变原字节证据，但不改变引用语义摘要。

冻结生产源码 Linux 全量 **2472 passed / 29 skipped**，517.51 秒，终态日志 `data/p05-roots-foundation-final-full-linux.log`。29 skips 包含原有12项与17项Windows别名专属用例；同源 Windows 五模块组合 **249 passed / 2 skipped**。R03 测试在全量采集后只追加两个纯接口边界（False limits 和非字符串错误脱敏），生产 SHA `88B49306C37B50DEE9B838A17B26B8FE2F04DE821B989F9E55AF8148D128634A` 未变；新增项由当前 Home 尾回归 **Windows 45 passed / 1 skipped、Linux 46 passed** 补验，日志 `data/p05-r03-home-linux.log`，不是把未采集用例算进原全量。其他相关终态：R01 启动/成员 Windows 127 / 3 skips、Linux 130；R02 Windows 65、Linux 48 / 17 Windows skips；R04/真实 schema 组合两平台各99。独立复审关闭 TEMP 清理、Windows别名及proof可变字段误读问题。

前端 typecheck 通过，首轮1项输入测试5秒超时（session34038），原文件单独5/5通过；减少 Vitest 并发至2后完整155/155通过（session80674，`data/p05-roots-foundation-front-final.log`）。没有修改业务、断言或timeout。`git -c core.autocrlf=false diff --check` 通过。

未迁移或重载现用服务，未登记/删除真实历史文件，未调用真实平台或模型，未配对/部署生产云。真实根完整性和旧路径/备份依赖未知保持原限制。P05、P06、T15/T16 及生产云部署仍未完成。

### 13.30 P05 归档保留、备份整组根图与只读预览（2026-10-01）

R05/R06 已实现并通过独立源码审查。`storage_checkpoint_roots`、`storage_backup_roots`、`storage_roots` 与 `storage-roots` CLI 形成只读整组预览；本批不开放历史文件删除。实际操作见[部署 §5.11](deploy.md#511-完整引用根与历史资源只读预览p05)。下一批仍须完成 R07 写者覆盖、EX 下重算、退休与删除 journal 同事务、逐文件删除及原操作续跑，还有发送端 durable no-resend→abandon 闭环。P05 整体仍未完成。

**归档保留。** 只解释原提交映像内的 publication/recovery checkpoint、committed origin 和退休证明；知识归档支持封闭 schema7/9，不用今天的 live 身份或退休记录补旧备份。每个成员两类归档分别保最近 N，默认 3、最少 1；相同时间按 checkpoint ID 稳定排序，额外保留最新一份真正可恢复的归档及活动恢复所需目标/保护点。可恢复必须验证归档字节、manifest、库/节点与媒体/报告依赖；最新 N 损坏时仍保留更老可恢复点，全部不可恢复则阻断该类自动退休。归档没有 checkpoint 表，不制造 archive→archive 边。来源节点缺失时不借当前节点：完整依赖仍保留，仅对不能证明的相关范围阻断。

**旧备份。** 固定内置位置及 catalog 登记的外部目标均从原镜像重读引用，schema0–6 采用独立有界投影。ready/legacy、reserved/retiring、缺失目标及未物化 WAL/journal 不当成空引用；旧 manifest 未知只阻断相关 `local_report`。完整原图重算逐项退休证明，不重复添加旧描述中已被证明解除的边；不完整图仍保留已知依赖和未知 blocker。任何已验证 catalog 的 source DB、目标 DB 及 descriptor/WAL/SHM/journal 都进入私有特殊路径保护，包括不属于当前 producer 的记录；这些记录不因此获得当前根的引用归属。

备份 discovery 新增可选 read-control，默认 producer 行为保持；读取每 128KiB 和 SQL progress 均检查取消与预算，SQL 逐行检查后才保留对象。计量含唯一主镜像大小、累计读取字节、metadata、候选数及 deadline；超过上限留下真实 blocker，不先物化巨量结果再检查。来源集合变化优先报告 `SOURCE_CHANGED` 以触发整轮重试；异常退出保留调用方原异常对象。归档缓存仅在同一精确 group guard 内共享，复用字节不合并各来源的保护边。

**整组候选与摘要。** 先发现全部成员闭包并取得全组 SH，再查询任何业务数据库；锁定前加入成员会重新取得全组，已有单实例 guard 不允许临时补锁。扫描所有成员当前知识（含未ready）、五 manifest、pins、未完成 job/queue、Home/outbox/history/source-map、备份和归档。只有第一项 owner 可提议自身逻辑退休或受管文件；假设退休仅移除该 owner 的 live 逻辑边，其他成员、旧备份和显式物理持有仍保护依赖。一个成员仅共享 media 且其 DB 不可读时，媒体受阻，独立报告仍可产生实际候选；不采用全 namespace 一律永久停删。

候选来自可信 `storage_artifact`，重新核根/文件身份及 SHA。未知文件不收编，冲突登记、missing、changed、特殊文件、硬链接和无完整成员证明分别排除；Windows 合法 video_id 大小写按物理路径等价命中，空间只算一次。候选 SHA 后还在整轮结束用 no-follow 原身份复验，不能把其间替换后的文件当作新 inventory 基线。晚发现 checkpoint blocker 时，先撤销该退休，再把媒体/报告传递引用放回，最后重算候选。

`roots_sha256` 绑定算法/limits 版本、政策、成员登记与物理根、知识版本、影响引用/退休的 SQL 投影、Home/manifest/备份/归档集合及来源边；`preview_sha256` 另绑定拟退休项、完整候选身份、数量与 bytes。DB/WAL raw 签名只检查本轮一致性，纯 worker/Home heartbeat 和 WAL/SHM 重建不进入语义 CAS。备份运行时剩余额度由总预算扣除已读镜像推导，其 discovery limits binding 绑定固定规则，不绑定无关 WAL 页数；不足时仍真实阻断，其他 parser 的实际 limits 保留。源 SHM 不读取也不 stat，仅精确路径排除。组级 row/model metadata、镜像、inventory 与 deadline 有上限；`max_candidate_hash_bytes` 明确仅计 owner 候选核 SHA，子读者各自的读取/hash 上限另绑定，不能称作全组 I/O 总额。公开输出仅受控 key/ID、数量/bytes/摘要和原因，不输出原 JSON、来源 URL、正文或凭据。

**当前验证。** R05 两平台各38；原 checkpoint 兼容 Windows57 / 1skip。备份图与 producer/consumer 三模块最终 Windows111 / 3skip、Linux114；日志 `data/p05-r06-backup-roots-final-linux.log`。整组/新CLI 最终 Windows78、Linux77 / 1skip（Windows路径大小写专项），日志 `data/p05-roots-group-final-linux.log`；新CLI与既有成员CLI另两平台各41。前端 typecheck 及155项通过。冻结生产源码完整Linux回归 **2643 passed / 30 skipped（555.57秒）**，日志 `data/p05-roots-preview-final-full-linux.log`（exit 0）；30项为原有29项及新增Windows大小写路径专项。源码指纹和全量终态在 `data/p05-roots-preview-frozen-sources.json`；全量后仅去掉group测试一处尾随空格，AST保持一致、断言和生产源码未改，记录保留测试时SHA与当前SHA。

本批没有迁移/重载现用服务、登记或删除真实历史文件、调用真实平台/模型或配对云端。现用库仍沿用 §13.28 的最后维护状态；真实根 `membership_unknown`、旧媒体路径映射及 legacy manifest 未知均保持，不擅自声明独用。可直接继续的工程为 R07 清理闭环和 P06；T15 等真实报价/变更材料，T16 保留个人单主与附件一致性门槛，生产部署等目标配置。

R07 前置审计已确认 Home/任务写者尚有实际缺口：`StateStore._mutating` 当前只持状态锁，`observe_recovery` 可改变引用字段；`freeze` 导出快照后释放SH再写outbox/pending，`SourceMap.record` 原子发布也未完整持SH；任务提交/重试/完成还需同样接入保护，已有资产写者另补调用链核对。它们必须在已有worker/StateStore锁之后取得对应profile SH并贯穿引用发布，GC EX不得反向等待状态锁，事务内只 require 已有 guard。本批仅扫描和预览，不因来源收尾复验而豁免这些写者。后续执行准入先关闭这些缺口，再实现退休/删除事务与可重试清理。具体矩阵/文件锚点保存在 `data/p05-roots-draft/r07-writer-coverage.md`。P06 最小合同已准备在 `data/p06-budget-draft.md`，仍仅草稿：复用现有任务等待/恢复，区分重放结算和实际外部重发，记录可核验tokens/文件bytes，不假称已取得供应商金额或套餐余额。

### 13.31 R07 引用写入保护、owner SQL 核对与清理执行（2026-10-01，已完成）

本节覆盖13.30末段的改前缺口状态：W01–W05、`storage_gc.py` 及显式 `storage-roots --apply/--resume` 已接入生产源码；**专项、独立审查和冻结完整回归通过，R07工程完成，P05整体未完成**。本机正常8765服务已于12:30加载本批实现；真实成员完整性和旧依赖未知保持，没有删除真实历史文件。执行方式与实际字节、暂存恢复及锁/I/O边界见[部署5.12](deploy.md#512-历史资源显式清理与原操作续跑r07)。

`StateStore` 的写操作要求显式profile并验证DATA绑定，按既有状态所有权→SH→原子发布；`reference_lifetime` 持续覆盖快照证明、outbox/pending与source-map/work。长证明释放RLock，heartbeat仍可请求并合并恢复状态；SH内采用完整原子 `read_observation`，GC EX不反向等待状态锁。`SourceMap` 全程采用显式media profile。任务submit/retry/finish在SQL前取SH，已有事务enqueue仅要求同guard；foreign process lock使用对应数据库目录，不借全局worker。

资产登记、媒体输入/输出、帧替换、files_json规范化/修复均已接精确profile/guard。pipeline保留原配置实例API，只在媒体引用步骤持SH，不跨模型HTTP持锁；repair先取实际worker锁，再SH、受保护备份与事务。所有新增清理/关闭/rollback保护都保留原业务或取消异常。normalizer文档已移除直接复制DB的示例，避免遗漏已提交WAL。

新增 `storage_roots.owner_sql_sha256`：同一纯SQL固定投影用于private committed image与owner `BEGIN IMMEDIATE`内重算，覆盖全部当前引用、pins/父binding、任务/队列、归档/恢复证明、所有权和原GC request/items。每个投影表的附属index/trigger schema也参与，防止capture后新增trigger改变journal提交行为；不执行view/generated表达式。行、cell、累计元数据、VM和deadline均有界，纯lease/display/GC结果不进投影；真实恢复`result_json`是恢复证明，仍保留。专属GC writer的authorizer/progress/长度/row_factory会复位，取消原对象不被secondary cleanup覆盖。该摘要不打开文件或新live连接，不读取全局配置。

备份节点范围已修正：历史descriptor没有冻结旧node时，完整可解析的backup仍保持 `recoverable=false`、历史保留原因、archive及全部依赖根；这不阻断已独立证明的live owner逻辑退休，物理字节仍被backup保留。缺失live node、坏归档、未知闭包及未证明退休继续阻断。Windows归档夹具已改用真实句柄身份：失败TEMP原件显示path ctime为birth time、fstat ctime为change time，不能手造path stat身份或放宽生产核对。

已取终态：Home六模块Windows193、Linux193，后增close尾3两平台通过；asset七模块Windows165、Linux162/3平台skip；owner SQL/整组/CLI/归档/backup组合Linux190 passed/1 Windows-only skip（29.77秒，`data/p05-r07-roots-checkpoint-final-linux.log`）、Windows191 passed（485.36秒）。随后CLI接线单独验证，两平台各40通过，加部分成功回执尾验两平台各1；生产入口未变，Linux日志 `data/p05-r07-gc-cli-linux.log`、`data/p05-r07-gc-cli-partial-final-linux.log`。GC领域Linux45 passed/1 Windows skip（51.91秒，`data/p05-r07-gc-restore-linux.log`）、Windows33 passed/13 POSIX skip（256.85秒，`data/p05-r07-gc-final-windows.log`）；实际TEMP多进程退出、rename/删除/恢复授权、取消续跑、替身保留、备份retain及短事务均通过。source方法级绑定37与task rollback8两平台均通过。前端typecheck及155项通过。

独立源码审查已关闭暂存取消与恢复rename→登记窗口，并核对owner附属DDL CAS、历史backup节点scope和CLI参数/脱敏/部分成功行为。Windows首轮组测试的1项失败证明是夹具path/fstat ctime差异，保留TEMP原件后修正真实句柄采样；没有放宽生产身份门。后增CLI部分成功测试首轮因wrapper未透传 `restored_ctime` 参数失败，修夹具后两平台通过，生产逻辑未变。旧2643/30skip只证明R01–R06；本批205个源码/测试文件冻结在 `data/p05-r07-frozen-sources.json`，冻结完整Linux回归为 **2858 passed / 31 skipped / 6 warnings（713.97秒，exit 0）**，终态日志 `data/p05-r07-linux-full.log`。

本机恢复点为 `data/service-p05-r07-20261001T120125+0800-7f838511/`。12:30正常8765服务重载后，运行核对确认schema10、46张非worker表及meta、配置、身份、sync-state和附件保持；28视频/349原子/16聚类/13冲突、防水49条，报告、PDF哈希、视频Range206和帧200通过。受管帧缓存仅补实际服务账号所需ACL，原owner、规则和既有帧SHA保持，恢复材料保存在同一目录。12:40两次只读观察确认新worker同owner自然续期，未手改租约，证据为同目录的 `lease-observation-first.json` 与 `lease-acquired.json`；205个冻结源码/测试SHA复核全部保持。同步仍关闭且未配对，`membership_unknown`、旧媒体路径映射及legacy依赖未知保持；未执行真实历史GC、调用真实模型/平台或部署生产云。

下一步仍为sender durable no-resend→abandon和独立本地引用退休，拟合同 `data/p05-no-resend-draft.md`，不是已实现入口；不能因有GC就自动清掉旧Home缓存。随后推进P06；T15/T16和生产云部署保持原材料/单主/目标配置门槛。真实unknown及旧依赖未知不因专项成功而解除。

### 13.32 P05 发送端永久事实与历史回执（2026-10-01，N01–N03）

`sync_no_resend`已接独立永久ledger：不可变激活意图先于目录初始化，激活seal确认初始化完成，中断恢复不换ledger UUID；catalog请求/回执reserved→不可变原字节fsync→committed，缺失/冲突/孤立文件或恢复旧catalog一律阻断。严格canonical原字节、SHA、owner/profile/peer、闭集、真实总bytes、数量和deadline均校验。首次明确操作先持久化StateStore format v2能力门，再写请求；不改原outbox。读取旧状态、heartbeat与精确detach仍可进行，重新安装/发送被永久事实拒绝。完整删除永久锚点并降级旧软件不属于支持的恢复，正常恢复须保留/验证合并现用ledger。

原owner/target node关联不能由当前配置补造：首版只接受当前精确pending/attempt或已验证永久请求的证明，历史orphan缺证据继续保留。请求绑定原metadata、原envelope SHA/size、原payload SHA、原source version和receiver stream/generation。detach只清匹配pending/attempt，保work/claims/legacy cache及applied generation；旧format outbox在detach后可由已验证的原请求补原stream，保持原字节与hash。显式本地续跑先重证原outbox，修同operation的请求/回执中断，不扩大选择或伪造远端事实。

发送门已覆盖freeze、write/install/update、prepare、verify、upload及其SH内callback、push和恢复复用。普通周期只续送已经明确授权的请求；显式resume即使sync关闭仍核worker/StateStore所有权、当前仍为原peer以及合法session，不运行其他内容发送。长网络不持SH/RLock，delivery-only lease heartbeat不重装recovery或覆盖claims/work。新upload hint按publication binding记录；旧uploaded_media不再增长，永久契约启用后恢复v1也不能自动清空旧cache。

新增SELECT-only `POST /api/sync/uploads/receipt`与共享原payload hash helper。原binding/receiver完整epoch/原hash吻合才返回published；held或较新fence不妨碍已知历史查询，future/换source/stream/坏proof拒绝。generation0保升级前hash和既有absent-fence==0合同，仍核原local_version；不打开媒体/报告、不写SQL/pin/claim/recovery。`SYNC_UPLOAD_PUBLISHED`须查询精确终态，不能显示成abandoned。重复查询的replayed不改变稳定回执身份，HTTP/domain清理不盖主异常。

基本Home消费者支持v2 state/history中的mapping代次和新缓存独立保根，GC特殊保护整个ledger与两个锚点。**本批尚不据marker减边，N04独立history/map/legacy-cache退休与禁止旧代次复用、N05公开CLI/私有DB原像证明仍待接入。** 成功push后的自动永久marker未接，旧ack保持；不能因领域方法存在宣布整个sender/P05完成。完整拟合同与当前字段见`data/p05-no-resend-draft.md` §14，内部方法不是新的用户命令。

Windows消费者179 passed /1 skip，266.84秒；N01最终模块28项通过，38.93秒；N02配对修补后相关334 passed /2 warnings，800.86秒，严格回执/race尾10项通过，1.15秒；N03新增65及旧API27共92 passed，137.94秒，不变transfer/pins75项此前通过。前端typecheck与155/29文件通过，15.41秒。210文件冻结后完整Linux **3012 passed /40 skipped /6 warnings（711.18秒，exit0）**，指纹复验全部保持（`data/p05-n01-n03-frozen-sources.json`、`data/p05-n01-n03-linux-full.log`）。首轮因配对切换窗口审查中断，日志`data/p05-n01-n03-linux-pre-peer.log`不作新版本证明。未维护真实库、升级真实sender状态、重载现用R07服务、弃用真实批次或调用模型/平台/生产云；真实membership_unknown与旧依赖未知保持，整体目标未完成。

### 13.33 N04 独立本地引用退休与 N05 显式管理入口（工程完成）

永久 references 请求与publication停发相互独立。selection闭集、最多128项，history work/source-map/state或history缓存各自选原实例/容器及rawSHA。请求绑定契约owner/四UUID peer、原operation/selection/CAS/policy_version；reserved已禁止生产复用，完整committed文件和catalog证据才允许减根。history/map/outbox/ledger不作为物理GC候选，也不因同video/SHA清空独立历史或旧cache。显式state cache退休提交后精确detach对应entry，未选条目与所有原history证据保持。

SourceMap本地v2保存独立UUID mapping_generation与原v1 origin/resolution；wire origin/mapping_id/outbox原bytes不变。work先保存generation及不可变source_mapping_inputs，再在连续SH内SHA证明→immutable map→链接origin。固定时钟同事实可用于明确新work的新generation，同work失败/崩溃/恢复则沿原UUID、resolution、媒体摘要和recorded_at。phase/claim续约、补链接、重新序列化或换inode不能复活旧实例；legacy也不能静默升级撤销退休。

Home捕获扩展覆盖完整source树和sender锚点/目录/catalog/publication/receipt/reference集合，使用纯frozen validator，不向当前live ledger借证明。所有目录集合、原selected、有效证明、契约/limits进入语义CAS。原根先完整收集；任一坏、缺、异主、超预算或活跃依赖证明保根并block，不返回空根。活跃outbox缺独立generation provenance时，同origin或video/mediaSHA保守阻断相关map退休；其他成员、旧backup原像、当前SQL/checkpoint引用继续独立保留。

公开sync-abandon与sync-retire-local默认纯预览，完整四路径、现有设施和成员见证是前提；apply在实际worker→StateStore所有权→SH下核私有committed DB/schema/node/owner/peer并重新CAS。sync-abandon apply提交本地停发，远端交付使用独立resume原UUID；pending要求显式sender-config文件，同一次fresh快照先核原peer再取token，completed回原本地receipt无需配置和网络。local resume也核原选集，不扩大集合。stdout只受控状态/ID/数量/摘要，不输出路径、URL、claim、凭据或正文；操作示例见部署说明5.14。

成功push永久published事实/本地ack恢复已接齐并独立审查通过：先核成功typed receipt及精确pending，持久原UUID请求/回执，再完成原ack；stored receipt可零RPC补ack。public远端交付后重新证明私有committed source epoch；旧source epoch或receiver stream不推进新代次。最终220文件冻结后Linux完整3155 passed/40 skipped/6 warnings（939.50秒），Windows责任专项及前端155/typecheck通过，完整工程与本机证据按主方案17.29维护。17:39正常8765已加载，schema10、46非worker表/meta/知识/资料/配置/身份/附件保持，新worker自然续租。未升级真实sender状态、弃用实际publication或执行GC；同步关闭未配对，成员/旧依赖unknown保持。P05工程完成，原整体目标继续active；P06/T15/T16及生产交付继续保留。
