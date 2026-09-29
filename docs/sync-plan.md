# 云端服务 + 本地推理 同步方案（v2，2026-09-29）

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
| 本地权威·随闭包推送 | `video_asset`、`knowledge_atom`（**除 status**）、`evidence_ref`、`transcript_segment`、`ocr_span`、`frame`（**path 列导出时置空**，云端不消费）、`visual_observation`、`conflict_case`（**除 status**）、`cluster`、`meta` 中 `step:<vid>:*` 键 | 评审证实漏掉 evidence_ref 会让云端原子全失去证据链、ask 引用芯片消失（`reno/db.py:222-223`、`reno/ask.py:134-135`） |
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

- `user_decision` 加列 `client_id` + 每端写入时生成 uuid 幂等键（修掉评审证实的
  "无 conflict_id 唯一键、按 cfl_id upsert 无键可依"；迟到重传按 uuid 去重，
  同一冲突按 `decided_at` 最后一笔生效）。
- **回放器**（确定性、两端同构、与 judge 互斥）：输入=合并后的 user_decision 全集，
  输出=`conflict_case.status` + `knowledge_atom.status` 的 UPDATE。从此
  **status 不再是同步对象，而是派生值**——两端口径天然收敛，atomize 重跑/推送覆盖
  都不再能静默抹掉用户决策（评审证实的"重跑重置 candidate、下次推送抹掉云端复核"根治）。
- 首次引导：先拉云端全部决策播种本地（现网已有 1 行决策，无播种即丢），再首推。

### 4.5 快照与回滚（悬决 Q2 裁决：批次快照 + stale 标记）

- 评审证实按 video 回滚与全局表互相悬空（cfl_/clu_ 内嵌 atom id）。回滚单元改为
  **推送批次**：云端每次接受推送前，把受影响行集+全局表打成一个 sqlite 快照文件，
  保留 N 代（沿用 snapshot 实现但按批次、有代数上限防线性堆积）。
- 回滚时计算引用了被回滚数据的 cfl_/clu_ → 标记 `stale`（UI 提示"判定基于回滚前
  数据，建议重判"）；**re-judge 不自动触发**（避免恢复操作烧配额），由用户在 UI
  手动点"重新判定"才本地 re-judge+全量重推。

### 4.6 schema 握手（修掉假安全）

评审证实 `schema_version` 是无人维护的死字符串而 `_migrate` 含 DROP 重建分支。
握手改为**运行时指纹**：两端各自对核心表 `PRAGMA table_info` 拼接 + 代码版本
（git commit）做哈希上报比对，不一致拒绝同步；role=cloud 时 db.connect 禁止执行
_migrate 的 DROP 分支（只读校验模式）。

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
- 缓存键已含 model（`reno/ask.py:121`），降级标记并入键。

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
  白名单拒绝、域白名单、脱敏）；现有 71 例不得回归。
- 端到端：WSL 双实例（A=cloud role，B=home role），覆盖：正常链路、中途断网重试、
  云端决策→本地回放、本地重跑→云端对账、源下线死信。
- 诚实边界：真机家庭网络上传带宽、平台风控真实表现、plan 封禁阈值——
  均无法在测试环境验证，上线后靠心跳观测指标回看。
