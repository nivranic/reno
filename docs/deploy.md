# reno 云上部署（一键脚本 + 容量参考）

> `deploy/deploy.sh`：Ubuntu/Debian 安装/更新，配套 `deploy_support.py` 与 `tls.sh`。重复部署保留凭据和节点角色，代码只允许快进更新，现有服务明确重启。
> 架构：FastAPI 单进程托管 SPA + /api + SQLite + 媒体文件流，无数据库服务、无前端独立进程。

## 1. 场景与建议形态

| 形态 | 说明 |
|---|---|
| **推荐：本地处理 + 云端只读** | 采集/ASR/OCR/atomize 等重活在开发机跑（CPU/磁盘友好），云端只 `serve` 提供搜索/问答/对比/报告。云端资源需求极低（见 §7）。 |
| 云端全功能 | 也可在服务器上 `reno import/run`，但 ASR(faster-whisper CPU) 很慢且吃内存，不推荐 2C4G 以下做。 |

## 2. 一键部署

```bash
# 在云服务器上（Ubuntu/Debian，已有 Python 3.11+ 与 Git；sqlite3 支持 serialize/deserialize）
git clone https://github.com/nivranic/reno.git reno-deploy-source
cd reno-deploy-source
# 先准备权限 0600 的凭据文件；不要把真实令牌写进命令参数或日志。
sudo bash deploy/deploy.sh --role cloud --tls-domain reno.example.com \
  --auth-token-file /root/reno-secrets/auth-token
```

将示例域名换成自己的公开域名，DNS A/AAAA 必须均能到达该服务器，主机及云平台允许 80/443。须使用完整、同版本的 `deploy/` 目录（包括 nginx 模板），不能只下载单个 shell 文件。`--api-key-file` 可另外指定云端问答使用的 paas key；不配置时仍可纯检索。`RENO_DEPLOY_AUTH_TOKEN` / `RENO_DEPLOY_API_KEY` 环境变量也是输入方式，但经 sudo 时需明确传递；文件方式更容易复用。旧 `--auth-token` / `--api-key` 明文参数现已拒绝。

脚本完成：系统依赖 → clone 到 `/opt/reno` → venv + pip → 前端构建（或 `--dist-tgz` 离线包）→
`config.local.json`（只写提供的键，保留已有键）→ systemd 服务 `reno`（专用用户、开机自启、
崩溃自动重启）→ 使用最终有效凭据的健康检查 → 可选 TLS 配置。默认仅监听 `127.0.0.1:8765`；TLS 模式拒绝公开后端监听。`--no-service` 只安装和校验数据库，不启动服务、不配置 TLS、不探测已有端口。

**角色**（`--role`，新库默认 `all`；重跑未指定时保留已有角色）：

- `--role cloud`：云端纯服务面——不起流水线 worker、`/api/import` 只入队等本地同步器、
  `glm-5.3*`（coding plan）模型在 llm 层直接拒绝（配错也打不出去）、`/api/config/model` 403。
  云端的 `--api-key-file` 应配 **paas key** 并在 config.local.json 设 `ask_model` 为非 glm-5.3* 的
  paas 模型（如 glm-4-flash 档），供 `/ask` 使用；无 key 时 /ask 自动降级纯检索，不会 500。
  完整分体架构（家庭机推理 + 定时同步）见 `docs/sync-plan.md`。
- `--role home`：家庭机全功能（现状行为，显式声明用）。

常用变体：

```bash
sudo bash deploy/deploy.sh --dist-tgz reno-dist.tgz       # 使用本地构建产物
sudo bash deploy/deploy.sh --role all --import-data reno-data.tgz  # 独立实例首次迁移
sudo bash deploy/deploy.sh --no-service --skip-deps      # 依赖已具备，仅安装和校验
```

所有参数见 `--help`。同一数据库版本内可重跑部署命令更新代码和资源；**数据库版本升级需要停写并显式执行 `migrate-db`**，不能把健康检查失败当成可覆盖旧库的理由。当前源码迁移版本为17、同步协议为3；源码版本不代表现用数据库或服务已经升级，实际进度见[产品主方案](plans/2026-09-30-product-evolution.md#1715-排除验收后的剩余实现更新至2026-10-04)。现用实例需单独核对版本及生命周期锁设施，缺设施的旧库按下文「个人域与数据库升级」显式离线初始化后迁移。已有同步对需双端同版本升级并显式刷新schema，不能自动改写原配对或旧请求。

## 3. 数据与前端产物迁移（开发机 → 服务器）

以下数据库导入用于独立运行的首次迁移，不用于建立 home/cloud 同步对。双节点同步要求各自独立的 `library_id` 和 `node_id`：cloud 应新建空库，经 §5.1 显式配对后由家庭同步器发布知识；不能先复制家庭 `reno.db` 到 cloud 再配对，也不能通过手改身份绕过检查。包含个人项目的现用库及附件不得借此整库混送到云端。

```bash
# 开发机（Git Bash / PowerShell 均可，Windows 自带 bsdtar）
cd <PROJECT_ROOT>
tar -czf reno-data.tgz data/reno.db media   # 只打包 db + 媒体：dy_cookies.txt、
                                            # db 快照等绝不随包上云（导入端也是白名单）
cd frontend && npm ci && npm run build && cd ..
tar -czf reno-dist.tgz -C frontend dist    # 约 1MB，秒传

scp reno-data.tgz reno-dist.tgz user@server:/tmp/
```

要点：
- `data/` 只打包 `reno.db`：部署脚本导入端也是白名单（只收 reno.db），
  `dy_cookies.txt`（平台登录态）与 `reno.db.bak-*` 永不上云。
- `data/`（reno.db）与 `media/`（原片/音频/帧）建议一起迁，时间轴跳转依赖两者；
  若后续开启云→家同步（sync-plan），audio/frames 不需要上云。
- 服务器上 `RENO_DATA_DIR` 指向 `<base>/data`，`media/` 自动落在 `<base>/media` 旁边
  （`reno/config.py` 的布局规则），脚本已处理。
- `--import-data` **仅用于目标尚无数据库的独立实例首次安装**，cloud 角色直接拒绝。只要目标 `reno.db` 已存在或是符号链接，脚本就拒绝导入，并在实际写入前再次检查；不会覆盖线上决定或检查历史。归档仅接收允许路径的普通文件，拒绝路径穿越、链接、设备与重名条目，不将 cookie、配置、节点身份或同步状态随包导入。
- 此包仅用于独立实例的首次迁移，不是同步播种包，也不是包含个人资料后的完整备份。数据库包含个人项目、来源或现场记录后，不应把整库当成普通知识闭包复制到另一节点；个人域的单主规则见 `docs/sync-plan.md`。
- 迁移前先在开发机跑一次路径规范化（存量库有反斜杠路径，云端直接用会断）：
  `.venv/Scripts/python -m scripts.normalize_files_json`（幂等，先快照 db 更稳）。

## 4. 运维

```bash
journalctl -u reno -f          # 日志
systemctl restart reno         # 重启
sudo bash deploy/deploy.sh ... # 升级（同参数重跑，需完整部署目录）
```

数据库/媒体在 `/opt/reno/data` 与 `/opt/reno/media`（按默认参数）。项目内维护备份使用5.10的受保护生产入口；在独立维护流程中使用SQLite backup API或 `sqlite3 reno.db ".backup ..."` 只能得到数据库镜像，不会自动产生项目备份登记与依赖描述。不要把在线WAL数据库的主文件直接 `tar` 当成一致备份。个人域备份还必须包含相应附件，流程见下文。

### 4.1 个人域与数据库升级（2026-09-30）

普通 `db.connect()` 只校验数据库兼容性，不迁移表。全新服务启动可以初始化空库；已有旧库需在维护窗口执行显式迁移。迁移在打开SQL连接前要求生命周期锁设施已存在，普通 `migrate-db` 不隐式创建设施。Linux默认路径示例：

```bash
sudo systemctl stop reno
# 确认其它 CLI / worker / 写入入口也已停止，关闭数据库连接并保存恢复点。
# 然后更新代码；维护阶段不启动服务。
sudo bash deploy/deploy.sh --no-service --role home
```

已有旧schema时，`--no-service` 在代码及依赖更新后会因兼容性校验非零退出，保持服务停止。只有核对失败明确来自待迁移版本时，才继续以下显式迁移；依赖、路径或其它失败先修复。`-m reno` 从实际安装目录运行，并指定同一配置文件。以下示例同时给出完整四路径；对缺设施的旧库，`--initialize-storage-offline` 是明确的停写维护操作，先建立锁设施，再在保护内迁移：

```bash
cd /opt/reno
sudo -u reno env RENO_DATA_DIR=/opt/reno/data RENO_CONFIG_FILE=/opt/reno/config.local.json \
  /opt/reno/.venv/bin/python -m reno migrate-db \
  --db /opt/reno/data/reno.db --media /opt/reno/media \
  --reports /opt/reno/data/generated --checkpoints /opt/reno/data/sync-checkpoints \
  --initialize-storage-offline
```

使用自定义安装、数据或配置路径时，以上命令必须换成服务实际路径。路径参数要么全部省略而使用当前配置，要么同时给出 `--db`、`--media`、`--reports`、`--checkpoints`；只给 `--db` 会报 `STORAGE_PROFILE_REQUIRED`。已具备设施时可省略 `--initialize-storage-offline`；它不停止现存进程、不关闭别的连接、不绕过维护窗口，也不声明旧根独用。旧schema不能借要求当前schema的 `storage-init` / `storage-member-init` 绕过此流程。

迁移会先产生受保护数据库备份与 `.reno-backup.json` 依赖描述，另保留原有 `.json` 行数/附件清单；SQL迁移失败会回滚，已写出的恢复点或锁设施不会假装一并撤销。清单中的 `attachment_files_copied=false` 表示**清单不是附件副本**，须另行保全附件。设施可用后再运行不带离线初始化标志的 `migrate-db`，当前schema应返回 `changed=false`，不新建备份或catalog；缺设施时，即使是此空操作也会拒绝，不隐式初始化。不要用旧版本直接打开新库，不要删除新表“回退”。

仅在迁移成功、核对输出及备份后启动服务：

```bash
sudo systemctl start reno
```

新增功能默认关闭。确认主实例、备份与接口验证后，将以下字段合并到现有配置（不要覆盖其它配置键）：

```json
{
  "personal_features_enabled": true,
  "field_checks_enabled": true
}
```

项目、决策、来源和检查均需经过 `/api/capabilities` 的服务端能力门。`node_role=cloud`可按§5.16显式开放已安装个人镜像的只读入口，所有个人写入仍关闭；单改开关不会将副本提升为主实例。`DATA/node-identity.json` 保存节点身份，数据库保存 `personal_primary_id` 和 `primary_epoch`；复制数据库不复制节点身份。

附件保存在 `DATA/personal-attachments/`，不会公开挂载。默认图片 10 MiB、PDF 20 MiB，每来源 20 个，允许 JPEG/PNG/PDF；HEIC 需先自行转为 JPEG/PNG。`staging/failed` 附件不可用于保存依据。若已 ready 附件缺失或 SHA 不符，个人记录仍可读，写入能力关闭；恢复对应文件并核对清单后才恢复写入。

反向代理需给个人附件请求留出 PDF 上限和 multipart 头空间：例如在 `/api/sources/` 上传路径设置 `client_max_body_size 21m`，应用仍按文件类型独立限制 10/20 MiB。现有通用代理的 10m 上限会提前拒绝较大的 PDF；不要把同步媒体的 512m 限额直接套到个人附件接口。

### 4.2 备份、恢复与安全回退

1. 停止 worker 与写入入口，记录恢复点、库版本、`library_id`、`primary_epoch`。同一节点恢复时保留本节点的 `node-identity.json`。
2. 使用5.10的受保护入口获取已提交数据库镜像，并保存依赖描述与catalog；同时复制 `DATA/personal-attachments/`。为每个ready附件记录 `attachment_id / storage_key / byte_size / sha256`；`reno.personal_attachments.attachment_manifest(con)` 可核对记录与文件，`complete=false` 必须先处理。数据库镜像和manifest观察本身不是所有文件同一时点的完整备份，停写与附件复制仍由维护流程保证。
3. 在独立目录验证数据库完整性、外键、各个人表行数、`personal_revision`、`mutation_receipt` 和附件 SHA，确认后在维护窗口恢复。仅恢复 `.db` 不算完成；更换节点不能自动继承主身份。
4. 恢复对应附件后重新执行 manifest 核对（会清理完整性缓存），并重启服务；知识恢复还须更新 `library_epoch` 并重算引用与缓存，不能回放旧库版本后继续用旧回答缓存。
5. 需要关闭新功能时，将两个功能开关设为 false，保留全部个人表和附件；回退仅到已验证兼容的应用基线。归档不会删除附件；临时清理仅处理超过配置期限且未被活动上传锁占用的不完整文件。

`judge` 旧快照是全库快照；当前没有安全的知识范围恢复产品入口，不要直接用它覆盖含个人记录的现用库。当前也没有自动切主工具；切主、双实例同步和真实恢复演练见同步计划的未完成门槛。

本轮已在临时人工库验证决定历史、回执、附件一致性、缺文件和等长损坏的恢复边界（`tests/test_personal_restore.py`）；未对真实用户数据库或云端生产实例执行恢复。

### 4.3 知识回填和选定视频重跑

两个历史维护脚本现在默认只预览，不调用模型，不改数据库。PowerShell 7 下可先查看计划：

```powershell
& .\.venv\Scripts\python.exe scripts/backfill_atoms_v2.py --dry-run
& .\.venv\Scripts\python.exe scripts/rerun_atomize_flashx.py --video-id '<选定视频ID>'
```

确认计划后，先停止服务及 worker，在同一配置/数据目录下明确执行：

```powershell
& .\.venv\Scripts\python.exe scripts/backfill_atoms_v2.py --apply
& .\.venv\Scripts\python.exe scripts/rerun_atomize_flashx.py --apply --video-id '<选定视频ID>'
```

实际执行会取得与worker相同的OS锁；活动worker未退出则拒绝执行。写入前先完成存储成员准入，再通过5.10的受保护入口在 `config.DATA` 生成SQLite镜像、依赖描述和备份登记；失败不继续修改内容。配置的模型不会被脚本切换，`--apply` 才可能产生模型费用。

重跑脚本必须明确选择视频，可重复 `--video-id`，不会预先清空全库、旧聚类或复核历史。它只重做 atomize，保留媒体/ASR/OCR；失败或没有可验证的非空结果时回滚该视频的替换，旧知识仍在。全局争议更新需要额外显式 `--judge`，或维护完成后通过既有“更新争议”任务触发。摘要也写入 `config.DATA`，遵循 `RENO_DATA_DIR`。

这些维护备份是恢复点，不包含完整附件副本；不要直接覆盖正在服务的数据库。以上行为由隔离人工库和模型桩测试验证，本轮没有执行真实视频重跑或真实模型调用。

## 5. 公网安全（必读）

- **`auth_token` 必须设置**：使用凭据文件或部署环境变量；TLS 模式缺少普通 API 令牌时拒绝。浏览器令牌走 `X-Reno-Token` 头，媒体兼容 `?token=`，因此公网必须使用 HTTPS。
- `--tls-domain` 先校验所有 DNS 目的地址的随机 HTTP 挑战文件，再以 Certbot webroot 签发或复用证书；真实证书链、域名与 HTTPS 探测通过后才启用 HTTP 跳转。签发前的 HTTP 站点只开放挑战路径，其他请求返回 503。
- 只管理带 Reno 标记的独立 nginx 站点，不删除默认站点或覆盖他人域名配置。失败时恢复此前站点及链接，再检查和重载；已有证书保留。续期使用 `certbot.timer`，成功续期 hook 执行 `nginx -t` 后重载。
- 后端固定回环。UFW 已启用时，仅调整实际应用端口的拒绝规则及 80/443 允许规则，不修改 SSH、不自动启用或重置 UFW；云平台防火墙仍由目标环境配置。脚本不对 `/srv`、`/home` 等数据父目录递归改所有权。
- 配置以独占临时文件原子写入并设为 0600；健康检查读取保留后的实际令牌，拒绝重定向。nginx 模板关闭访问日志，避免媒体查询令牌进入 access log。模板含占位符，须经 `deploy_support.py render-nginx` 生成，不能直接复制启用。

### 5.1 同步接口与显式配对（S04）

同步 HTTP、家庭同步器和采集页同步状态已接入；家庭端显式配置见下节。仅配置云端令牌不会自动搬运知识。全部端点和请求合同见 [同步方案 §13.8](sync-plan.md#138-s04-同步-http-与已发布数据读取)，页面行为与验证范围见 [§13.10](sync-plan.md#1310-s06-同步状态页面2026-10-01)。

- cloud 同步面使用独立 `sync_token`，仅接受 `X-Sync-Token`；浏览器继续使用 `auth_token`。`/api/sync-status` 属于普通浏览器鉴权，不向客户端返回任何同步令牌。
- 同步面强制 HTTPS。使用 `reno serve` 时，`trusted_proxy_ips` 默认只信任 `127.0.0.1` 和 `::1`；远端代理应填写明确 IP，不能用 `*` 或整个网段。后端只绑定回环，HTTPS 由受信代理终止；应用不会自行采信传入的 `X-Forwarded-Proto`。直接运行 Uvicorn 时也必须设置同等严格的代理信任。
- 先在家庭节点运行 `python -m reno sync-identity` 取得身份 JSON，经本机管理流程放到 cloud 后执行 `python -m reno sync-pair --source <身份文件>`。该命令只绑定既有库，不初始化、不迁移、不自动换 peer。两端升级后，可在没有有效会话/认领时使用 `--refresh-schema` 显式刷新同一节点关系的结构合同。
- 无停机轮换时，通过原子替换配置文件同时设置新 `sync_token`、旧 `sync_previous_token` 和未来最多 24 小时内的 UTC `sync_previous_token_until`。同步凭据每次请求热读，截止后旧令牌失效；环境变量仍优先于文件。角色与代理配置变化需要重启，不随凭据轮换切换。
- 同步代理按路径限制请求：媒体 512 MiB、push 65 MiB（应用仍执行精确 64 MiB + 64 KiB 上限）、其他同步控制请求 64 KiB。单头缓冲 16 KiB 容纳 8 KiB 元信息及头名，应用继续检查真实元信息边界。所有同步 location 均覆盖 `X-Forwarded-Proto`，关闭请求/响应缓冲与自动转发重试；读取上游最多 3700 秒，应用整体预算仍为一小时。限流返回带 `Retry-After: 60` 的 JSON 429，客户端可以正常归类重试。实际代理测试与公网签证的不同证明范围见同步方案 S07 记录。

`sync_allow_insecure_test_transport` 仅供明确隔离的 QA 实例，普通数据目录或非回环客户端即使设为 true 也不会获准 HTTP 同步。不要将测试例外写进正式配置。

### 5.2 家庭同步器（S05）

家庭端只在显式开启后工作，默认配置保持关闭。先按 5.1 在 cloud 本机配对，再从 cloud 的 `sync-identity` 输出取得目标库和节点 UUID；家庭端必须填写这两个确切身份，不能从 URL 或角色推断。

```json
{
  "node_role": "home",
  "sync_enabled": true,
  "sync_cloud_url": "https://reno.example.com",
  "sync_target_library_id": "<云端 library_id UUID>",
  "sync_target_node_id": "<云端 node_id UUID>",
  "sync_token": "<独立同步凭据>",
  "sync_interval_seconds": 60
}
```

`sync_cloud_url` 是部署根地址，可以包含反向代理前缀，不填写 `/api/sync`，不能包含用户名、密码、查询参数或片段。生产必须使用 HTTPS，客户端验证证书、不跟随重定向、不读取环境代理。`sync_interval_seconds` 为 15–3600 秒；运行间隔附带小幅抖动。端点、身份、启用开关和间隔沿普通配置缓存，需要重启服务加载；同步凭据仍逐次请求热读。

开启后，普通 `python -m reno serve` 的既有 worker 会自动调度同步。手动提交一轮用 `python -m reno sync-run`；若已有服务持有处理锁，命令返回已排队任务，由服务执行，不应将 pending 当成发布成功。没有运行服务时可用 `python -m reno sync-run --follow` 持续运行。同步与普通处理、judge、报告共用持久任务队列及 OS 锁；不能另启动绕开 worker 的知识写入脚本。

家庭端在 `DATA/sync-state.json` 保存配对绑定、租约、双向游标和状态；`DATA/sync-outbox/` 保存已冻结待发批次；`DATA/sync-sources/v1/` 保存来源映射。数据库恢复会改变 library epoch，下一轮同步原子重置游标并重扫原始事件 UUID。不要将这些状态文件复制给另一节点，或通过删除状态文件规避身份不匹配。首次心跳回执丢失但未取得租约令牌时会等待云端原租约到期；发布回执丢失则重发同一 publication UUID 和原 claims。

S05 增加来源声明合同标记，**数据库迁移版本仍为 6，但握手指纹发生变化**。已有配对须两端更新同一代码，并在会话和认领均已失效后按 5.1 显式 `--refresh-schema`；不要以自动重新配对跳过合同检查。来源声明是已配对家庭节点的可追溯记录，云端没有独立访问平台验证短链。解析器固定 IP 的保护覆盖短链及最终网页解析；yt-dlp 自身的 CDN 下载不属于该网络边界。

本节是配置和运行契约。Windows 隔离测试、真实云端 TLS、Linux 文件保护与家庭网络的证明范围分别记在 [同步方案 §13.9](sync-plan.md#139-s05-家庭同步器2026-10-01)，不能据此认为已有实际云端部署。

### 5.3 可重复执行的隔离双实例场景（S07）

`scripts/run_sync_scenario.py` 会在系统 TEMP 下新建 home/cloud 两个独立库、节点身份和测试配置，用真实 HTTP、持久 worker、来源映射、媒体传输、发布事务及复核事件执行全链。平台解析、下载输入、ASR/OCR/模型响应和个人哨兵记录均为合成样本；三段 MP4 由 ffmpeg 生成，不读取现用配置或视频，不调用外部模型。

```powershell
# Windows / PowerShell 7；默认运行后停止自己创建的服务并保留证据目录
& .\.venv\Scripts\python.exe scripts/run_sync_scenario.py

# 需要浏览器复查时保留隔离 cloud；输出只含 manifest/evidence 路径
& .\.venv\Scripts\python.exe scripts/run_sync_scenario.py --keep-server
$env:RENO_E2E_MANIFEST = '<上一步返回的 qa-manifest.json 绝对路径>'
Push-Location frontend
npx playwright test -c playwright.sync-chain.config.ts
Pop-Location
& .\.venv\Scripts\python.exe scripts/run_sync_scenario.py --stop $env:RENO_E2E_MANIFEST
```

Linux 使用对应虚拟环境的 `python` 执行同一脚本；`--root` 必须是系统 TEMP 下尚不存在的目录，不能指向仓库、现用数据或 8765 端口。`--stop` 先校验回环地址及实际服务的实例标记，再经私有控制接口退出，不根据旧 PID 杀进程。不得以 `python -O` 运行，工具依赖的核对断言必须启用。

场景包含正常入队/处理/发布、发布成功后丢回执的原 UUID 重放、家庭进程退出加 cloud 断网重启后的持久恢复、旧租约拒绝、半上传及已上传未发布内容不可见、双向复核汇聚、个人表与附件哈希保持。为缩短纯测试等待，预置过期租约为 1 秒、家庭 worker 租约为 12 秒，续期间隔仍为 10 秒；正常生产租期未修改，也不手工清租约来制造恢复成功。

结果在 QA 根 `evidence.json`；私有 manifest、配置、租约文件只留本机，不加入 Git 或 CI 公共产物。浏览器检查实际同步后的三段视频解码、播放时间推进、拖动后继续播放、Range 与匿名拒绝。CI 已加入同一场景和浏览器入口；本地通过不表示远程 CI 已运行。

### 5.4 发布归档与复核对账（P01/P02）

P01/P02 引入 schema 7，当前 P04 已升至 schema 9。旧实例先停止服务/worker、保存数据库和个人附件恢复点，再显式运行 `reno migrate-db`；不要通过普通请求升级。配对双方升级同一代码后，在会话与认领失效时按 5.1 显式刷新握手，不能自动重配对。

Python 最低声明为 3.11，sqlite3 必须能完成非空内存库的 `serialize → deserialize` 往返；部署在系统解释器和实际 venv 解释器分别检查。Ubuntu 22.04 默认 Python 3.10 不满足这一要求，应使用已具备合适解释器的环境，不改写系统 Python 链接。当前本地实测为 Python 3.12，3.11 专项 CI 已配置、尚未远程运行。

```text
python -m reno sync-checkpoints --limit 10
python -m reno sync-checkpoint-preview <checkpoint_id>
python -m reno reconciliation --scope attention
python -m reno reconciliation --scope all --markdown
```

这些命令只读，不初始化、迁移或恢复知识。cloud 每次新发布在同一事务中登记接收方知识前像，存放于该接收库同级 `sync-checkpoints/`；重复回执不生成第二份归档。schema 升级不会凭空生成旧批次归档；本机 all 角色未配对时列表为空是正常状态。

每份 SQLite 归档最多 256 MiB，只含知识白名单、步骤、报告指针和依赖清单，不含个人资料、附件、复核事件或凭据。媒体和报告字节继续引用本机不可变文件，**单独复制归档不能构成完整备份**。预览深度核验文件，列表也逐条核验，数据较多时会耗时；不要高频轮询。创建时已有缺损会保守保留为不可恢复。实际恢复/暂停/续传见 5.5；保留代数、垃圾回收和配额继续属于 P05/P06。

浏览器 `/reconciliation` 使用普通 Reno 令牌，提供当前/历史筛选与全量 Markdown 导出。每次查询或导出各取一个一致快照，不承诺多次分页请求间版本冻结；导出的评估时间与版本用于追溯。它不属于五类同步静态报告，也不调用模型。

对账浏览器回归使用 `prepare_product_fixture.py --root <系统TEMP下新目录> --port 18767 --reconciliation`，再设置 `RENO_E2E_MANIFEST` 为新根下的 `qa-manifest.json` 并从 frontend 执行 `npx playwright test tests/e2e/reconciliation.spec.ts`。该扩展含明确的 39 条合成事件；普通夹具缺扩展时此专项跳过。Playwright 管理自己的测试服务，不复用 8765。非项目 venv 的运行环境需设置 `RENO_E2E_PYTHON`。

### 5.5 受控知识恢复与显式续传（P03）

P03 始于 schema 8，当前 schema 9 兼容这些恢复操作；工程实现及隔离恢复链路已完成，合同与验证边界见[同步方案 §13.14–13.15](sync-plan.md#1314-p03-恢复与显式续传合同)。两端同版本升级并显式刷新配对合同后才可同步。恢复入口仅在接收方本机 CLI 提供，浏览器展示状态。

```text
python -m reno sync-recovery-status
python -m reno sync-checkpoints --limit 10
python -m reno sync-restore <checkpoint_id>
python -m reno sync-recovery-checkpoints --limit 10
python -m reno sync-resume
```

上述命令均只读。`sync-restore` 返回目标归档可恢复性、当前知识版本和控制版本；从恢复保护点预览时增加 `--checkpoint-kind recovery`。执行恢复须将同一次预览返回的版本原样填入，并为这次操作生成稳定 UUID：

```text
python -m reno sync-restore <checkpoint_id> --apply --operation-id <操作UUID> --expected-library-id <预览library_id> --expected-epoch <预览library_epoch> --expected-revision <预览kb_revision> --expected-control-revision <预览control_revision>
```

命令恢复受控知识并保存恢复前保护点，保留个人域、附件、队列和原始复核历史。随后知识同步持久暂停，服务重启也不自动解除。若响应丢失，用同一操作号和完全相同的参数重试；版本冲突则重新预览，不能盲目替换版本号。准备续传时，先重新运行 `sync-resume` 取得当前版本，再执行：

```text
python -m reno sync-resume --apply --operation-id <另一个操作UUID> --expected-library-id <当前library_id> --expected-epoch <当前library_epoch> --expected-revision <当前kb_revision> --expected-control-revision <当前control_revision>
```

此时进入等待完整重发状态；家庭端用新发布号和新知识修订号重新发布，云端确认后才恢复任务认领。知识逻辑恢复保持接收库 epoch；整库灾备恢复使用既有 epoch 递增规则。不要手改 generation、删除 outbox 或复用旧回执来解除暂停。

隔离验证使用 `python scripts/run_sync_scenario.py --recovery --keep-server`；设置返回的 `RENO_E2E_MANIFEST` 后，从 frontend 运行 `npx playwright test -c playwright.sync-recovery.config.ts`，最后用场景工具的 `--stop` 停止匹配实例。恢复场景会增加队列项，应使用该专用浏览器配置。公共产物只包含脱敏 `evidence.json` 和截图；私有日志保留在 QA 根。

### 5.6 关联判定依据（P04）

schema 9 为聚类与冲突保存生成时的原子依据。迁移保留旧数据，旧关系显示“判定依据未知”，不自动调用模型补齐。两端升级同一版本并刷新配对合同；历史 schema 7 归档仍可预览和恢复，但没有生成依据的旧关系不能据此变成“匹配”。新归档保留其原始绑定，整体恢复后按内容重新判断。

| 页面状态 | 含义与下一步 |
|---|---|
| 依据匹配 | 当前成员与生成时输入一致；这不等于结论经过权威认证 |
| 依据已变化 | 原子内容或判定所用分类等信息改变，旧分析保留供核对 |
| 依据成员缺失 | 部分原子已经不可用，需要先核对来源再重新判定 |
| 判定依据未知 | 旧记录未保存绑定，或绑定无法校验；不能补造历史依据 |

在有执行能力的本机或家庭节点进入“争议复核”，点击“更新争议”才提交模型任务。排队和运行状态均不等于已完成；已有个人决定继续保留，重算后的适用性仍按各自内容指纹判断。cloud 页面只提供到家庭节点操作的指引，不假装远程任务已经提交。家庭节点下一轮完整同步会发布新的关系；若 cloud 处于 P03 恢复暂停，重新判定不会解除暂停，仍须按 5.5 显式续传。

历史关系仍可记录个人意见，但保存意见不会刷新判定依据。问答、对比结果保留生成或检索时的状态，不因后台重算自动重发问答。搜索筛选使用当前派生状态，避免保留的旧状态与显示内容不一致。

报告版本与关系依据独立：最新报告也可能明确包含待核对历史。新生成规则为 `reports-v3`，正文和清单元信息保存生成时关系摘要；浏览器呈现、复制和下载还会标明当前限制。旧 `reports-v2` 同步回执仍可原字节重试，但不会伪装成新规则报告；呈现提示不重写不可变报告文件及其 SHA。

隔离浏览器入口为 `prepare_product_fixture.py --root <新TEMP目录> --port <非8765端口> --derivation`，设置 `RENO_E2E_MANIFEST` 后，从 frontend 执行 `npx playwright test -c playwright.derivation.config.ts`。专用配置拒绝缺少 P04 标记的夹具；仅在该合成夹具中替换模型判词，任务入队、worker、judge 事务与新绑定生成使用生产逻辑。公开产物限截图和合成报告，不上传配置或完整数据库。

### 5.7 API 帧缓存容量（P05 子项）

API 即时抽帧使用独立的持久索引和跨进程锁，命中刷新访问顺序，按 LRU 回收受管缓存。`frame_cache_max_bytes` 默认 536870912（512 MiB），可在 `config.local.json` 配置，或通过 `RENO_FRAME_CACHE_MAX_BYTES` 覆盖；配置实时读取。0 表示仍返回本次提取的图像，但不保留受管缓存。索引另有 10000 项上限，达到后淘汰最久未访问项。单帧超过预算或缺少原视频内容版本时只供本次读取。

```bash
# 只读查看当前容量及按配置预算计算的候选；不初始化任何目录或库
python -m reno frame-cache
# 按 128 MiB 预算预览，budget 覆盖仅用于这次命令
python -m reno frame-cache --budget-bytes 134217728
# 把上一次输出的 digest 原样代入，并保持相同预算
python -m reno frame-cache --budget-bytes 134217728 --apply --expected-digest <digest>
```

预览不刷新访问顺序。若预览后又访问了帧，或文件已变化，执行会拒绝旧摘要，需要重新预览。删除中途失败返回非零，保留可重试登记；不能按“已开始执行”判断释放空间，应看成功结果的 `deleted_bytes` 和剩余容量。被替换或无法核实的登记项保留并计入 `preserved_invalid`，因此结果可能仍有 `remaining_over_budget_bytes`。

仅新索引登记的 API 帧接受自动回收。旧布局缓存、未知文件、进程崩溃后无法证明所有权的残留继续保留，`preserved_unknown` 按目录项报告，不能当作已经计入预算的全部磁盘容量。处理用证据帧 `media/frames`、原视频、报告、知识归档、个人附件和备份不在此入口范围内。不要手工覆盖正在运行的受管目录；跨进程锁保护遵循本协议的进程，不充当同账户恶意进程之间的安全沙箱。

持久上传保护、历史媒体/报告显式回收及归档保留代数已接入，操作与边界见§5.12；本帧缓存入口仍只管理API帧。N01–N03已通过冻结回归；N04独立本地引用退休与N05公开操作入口已接源码，已通过最终工程回归，操作合同见§5.14。17:39本机正常8765已加载本批，数据保持证据见主方案17.29；真实成员完整性和旧依赖未知保持，未执行真实历史GC。

### 5.8 文件生命周期设施与 schema10（P05 基础）

本节记录P05基础批次：schema10新增五张文件登记、上传意图和回收记录表，不更改知识或个人数据。该批后续protocol2 / upload_intent_version=1改变了握手；当前源码已为schema17 / protocol3，并发布continuity1 / readmit2 / dispose2，实际加载进度见产品主方案。升级仍按前文停写、备份、`migrate-db`、重启流程执行；不要仅修改meta里的版本号。知识归档独立保持schema9，并继续读取schema7旧档。两个节点须同时升级，再对同一空闲peer显式`sync-pair --refresh-schema`，不自动刷新配对或改写旧outbox。nginx模板需同步升级以允许prepare的4MiB集合；verify为单文件最多300秒，不将整库SHA塞入一次30秒控制请求。

```bash
# 纯只读，缺设施时返回 initialized:false，不创建目录或数据库
python -m reno storage-status
# 对当前节点显式建立锁设施；必须已有当前版本的数据库
python -m reno storage-init
# 自定义实例须同时指定四个路径，防止临时库误用现用媒体
python -m reno storage-init --db /srv/reno/data/reno.db --media /srv/reno/media --reports /srv/reno/data/generated --checkpoints /srv/reno/data/sync-checkpoints
```

服务启动及CLI写命令在数据库兼容性检查后、启动worker前，通过 `storage_admin.setup()` 初始化设施并登记自身成员；直接ASGI启动在lifespan内使用同一入口。配置读取不创建存储目录，普通请求不隐式建立生命周期锁设施或补做成员准入。本次实际新建且验证有效的根可保留 `fresh` 依据；旧根登记为 `membership_unknown`，允许正常使用但不据此宣称成员完整。已登记实例只核对自身根及本地提交见证，不要求其它成员数据库或其独立根在线；本地登记不完整则拒绝启动，按5.9核对后显式登记。

`init-db`只负责数据库；`storage-init`只建立锁设施，不替代成员登记，也不用于升级旧schema。`migrate-db`须先具备锁设施；缺设施的旧库按4.1停写并使用 `--initialize-storage-offline`，迁移成功后再由正常启动登记自身。锁是同一服务账户的私有文件，已有未知marker、链接或不兼容权限会拒绝，不覆盖修复；不要删除正在使用的锁文件。若数据库已可用但后续成员准入失败，数据库初始化或迁移不会因此假装回滚，应核实路径、权限和成员预览后处理。

`initialized:true`仅说明锁设施可用。上传、发布、报告、家庭冻结/媒体读取、归档/恢复已使用保护；SHA与网络收包不持新增SQLite写事务，完整视频按已打开句柄返回，Range最多4MiB在保护内取得。已有绝对媒体路径仍须位于当前MEDIA根，不会拿一个库的锁去保护另一目录。

仍没有历史媒体/报告/归档的GC apply命令。共享物理根的成员登记见5.9，受保护备份生产与有限保留见5.10；后续仍须完整组合成员及历史依赖，再提供文件保留策略、只读预览与可重试删除。旧备份缺本地manifest时不能猜其历史报告引用；不明成员、损坏备份或无法证明依赖会阻断相关namespace清理。旧未登记备份、个人附件、配置/身份均保留，不能因设施初始化成功就手动删除。帧缓存继续按5.7的独立机制工作；最新合同见同步方案。

### 5.9 共享存储根的成员登记（P05）

同一物理 `media`、`reports` 或 `checkpoints` 根可能由多个数据库共同使用。成员登记保存数据库及根目录的文件身份、数据库 `library_id` 和使用关系；管理员的完整成员声明用于确认某个根有哪些使用者。数据库父目录也参与生命周期锁，但不属于这三类文件根的成员声明。**登记不会迁移数据库、退休成员或删除业务文件，也没有历史文件 GC apply 入口。**

#### 先预览，核对状态

```bash
# 当前配置实例，只读预览；缺设施时报告阻断，不自动初始化
python -m reno storage-members
# 自定义实例必须同时指定四个路径
python -m reno storage-members --db /srv/reno-a/data/reno.db --media /srv/reno-shared/media --reports /srv/reno-a/data/generated --checkpoints /srv/reno-a/data/sync-checkpoints
```

预览不改源数据库、源 WAL/SHM、成员登记文件或锁设施。存在非空 WAL 时可能创建并清理系统 TEMP 下的私有身份读取副本，限制见本节末尾。程序会沿已登记的 `group_profiles` 扩展已知成员组合，在完整组合的共享锁内重读核对；它不会扫描整个磁盘来证明没有其他数据库使用这些目录。

检查 JSON 的 `blocked`、`digest`、`profiles`、各根的 `state` / `proposed_state`、`existing_members` / `proposed_members` 和 `unavailable_members`。**预览即使返回退出码0，也可能是 `blocked:true`；不能只凭退出码判断可以执行。** 缺设施、损坏登记或缺失根等情况会返回阻断信息，通常 `digest:null`，预览不会修复它们。

| 根的状态 | 含义与处理 |
|---|---|
| `membership_unknown` | 已见到根或成员，但尚无完整所有者依据。旧根即使看似独用，也保持此状态，需核实完整清单。 |
| `complete` | 完整性具有显式声明、本次新建根的有效证明，或已验证登记的继承依据。仅说明成员完整性，不代表文件可删除。 |
| `enrollment_incomplete` | 多根登记与提交见证尚未形成一致状态，可能曾中断。先重新预览并核对全部成员，不手工删登记文件重置。 |

`blocked:false` 且有 `digest` 仅表示当前登记计划可执行，不保证各根完整。没有 `declarations` 时可以登记成员，但旧根仍可能保持 `membership_unknown`；有完整声明时应先确认 `proposed_state`，执行后再次预览核对实际 `state`。

#### 完整 owners.json 与显式执行

下面示例中，A、B两个数据库共用媒体根，各自使用独立的报告和归档根。文件名可为 `/srv/reno-admin/owners.json`；顶层字段只有 `profiles` 和 `declarations`，没有 `owners` 字段。应按实际部署替换全部路径，不照搬示例为未知目录声明独用。

```json
{
  "profiles": [
    {
      "database": "/srv/reno-a/data/reno.db",
      "media": "/srv/reno-shared/media",
      "reports": "/srv/reno-a/data/generated",
      "checkpoints": "/srv/reno-a/data/sync-checkpoints"
    },
    {
      "database": "/srv/reno-b/data/reno.db",
      "media": "/srv/reno-shared/media",
      "reports": "/srv/reno-b/data/generated",
      "checkpoints": "/srv/reno-b/data/sync-checkpoints"
    }
  ],
  "declarations": [
    {
      "root": "/srv/reno-shared/media",
      "databases": ["/srv/reno-a/data/reno.db", "/srv/reno-b/data/reno.db"]
    },
    {
      "root": "/srv/reno-a/data/generated",
      "databases": ["/srv/reno-a/data/reno.db"]
    },
    {
      "root": "/srv/reno-a/data/sync-checkpoints",
      "databases": ["/srv/reno-a/data/reno.db"]
    },
    {
      "root": "/srv/reno-b/data/generated",
      "databases": ["/srv/reno-b/data/reno.db"]
    },
    {
      "root": "/srv/reno-b/data/sync-checkpoints",
      "databases": ["/srv/reno-b/data/reno.db"]
    }
  ]
}
```

输入必须为UTF-8 JSON，最大4MiB，`profiles` 为1–128个完整四路径配置。所有路径使用运行主机上的绝对路径；Windows可用 `E:/...`。重复字段、额外字段、相对路径和 `..` 均拒绝；每项声明的 `databases` 不能为空或重复。要确认全部文件根的完整性，就应像上述示例一样，分别列全三个角色实际对应的每个根及其全部数据库。`--members-file` 不可与 `--db` / `--media` / `--reports` / `--checkpoints` 混用。

```bash
# 1. 使用完整清单预览，检查全部根、成员、阻断项和拟议状态
python -m reno storage-members --members-file /srv/reno-admin/owners.json
# 2. 生成本次操作的新UUID；将其与上一步digest代入下一条命令
python -c 'import uuid; print(uuid.uuid4())'
# 3. 只有显式apply才写入登记，两个占位符都须替换
python -m reno storage-members --members-file /srv/reno-admin/owners.json --apply --expected-digest '<本次预览digest>' --operation-id '<新生成的UUID>'
# 4. 再次预览，确认实际state和成员符合预期
python -m reno storage-members --members-file /srv/reno-admin/owners.json
```

`--apply`、`--expected-digest`、`--operation-id` 必须成组提供；预览时也不能单独附带后两项。执行在完整组合的排他锁内重算计划，再校验摘要；预览后成员或登记变化会报 `MEMBERSHIP_PREVIEW_STALE`，需重新预览。成功结果包含 `operation_id`、`changed_roots`、`preview_digest`。

已登记成员缺失、不可用或身份变化时，程序保留旧记录并阻止登记执行，不把“找不到数据库”解释为已退休。声明遗漏已观察到的成员会报 `MEMBERSHIP_DECLARATION_OMITS_MEMBER`，不可通过缩减JSON清单移除它。目录或数据库被替换也须先核实身份，不得删除登记来绕过检查。

多根登记不是一笔SQLite原子事务，执行会写各根的 `.reno-storage-members-v1.json` 及 `.reno-storage-members-commit-<UUID>.json` 提交见证。操作中断或结果不确定时，先重新预览真实状态；如仍需完成登记，使用**新预览摘要和新UUID**。此处不能套用恢复命令的“同UUID重放”：已使用UUID会报 `MEMBERSHIP_OPERATION_ALREADY_USED`。保留原登记和见证，不手工删除或伪造完整状态。

#### 初始化与旧根的区别

`storage-member-init` 是显式写入初始化，不是预览，也不接受 `--apply`、`--members-file`、digest或UUID参数。它要求数据库已存在且schema兼容；不会新建或迁移数据库。自定义实例仍须同时提供四路径，各文件根的父目录必须已经存在。

```bash
# 当前配置实例的显式成员初始化
python -m reno storage-member-init
# 自定义实例的显式成员初始化
python -m reno storage-member-init --db /srv/reno-a/data/reno.db --media /srv/reno-shared/media --reports /srv/reno-a/data/generated --checkpoints /srv/reno-a/data/sync-checkpoints
```

只有本次初始化实际新建、在锁内验证仍只有生命周期锁设施且文件身份一致的根，才可凭本次创建证明标为 `fresh`。**已有空目录、先前执行过 `storage-init` 的目录、或看起来只由一个数据库使用的旧根，都不能自动断言独用。** 这些根保持 `membership_unknown`，需通过完整声明确认；中途崩溃会失去本次创建证明，重跑也不会猜测此前的新建状态。

服务启动复用同一成员初始化入口，先于worker和流水线目录准备执行，从而保留本次实际新建根的创建证明；旧根仍只登记为unknown。已登记且当前成员及本地提交见证有效时，初始化在自身根的共享保护内幂等返回 `unchanged:true`，无需其它成员数据库或其独立根在线。这个启动准入不证明全部成员可用于清理：`storage-members` 的完整组合预览仍会因外部成员缺失而阻断，也不会退休它。

本地见证缺失、登记不完整或新成员试图直接加入已登记共享根时，报告 `MEMBERSHIP_ENROLLMENT_REQUIRED`，应转到完整清单的预览与显式登记流程；初始化不替代中断登记的核对，也不退休其他成员。`storage-member-init` 的数据库兼容性预检会正常打开SQLite，不能把上文针对成员预览的源WAL/SHM只读承诺套用到整个初始化命令。

#### WAL身份读取限制与失败处理

成员预览读取的是 `library_id` 等身份依据，不迁移schema，不恢复业务内容，也不证明备份完整。无WAL或WAL为空时，以只读immutable方式查询源数据库，并在前后核对文件状态；非空WAL时，将稳定的数据库及WAL复制到系统TEMP私有目录，仅由SQLite在副本上解释已提交WAL，不忽略WAL，也不创建或改写源SHM。临时副本及其SHM在退出时清理。

| 返回信息 | 处理方式 |
|---|---|
| `MEMBERSHIP_SNAPSHOT_TOO_LARGE` | 非空WAL的复制分支要求数据库与WAL合计不超过256MiB；此限制不适用于无WAL的直接身份读取。按正常SQLite维护流程停写、处理checkpoint后再预览，不手工删除WAL。 |
| `MEMBERSHIP_SOURCE_CHANGED` | 源文件在核对或复制时变化，最多尝试两次后拒绝。等写入稳定或按维护流程停写，再获取新预览。 |
| `MEMBERSHIP_DATABASE_BUSY` | 存在非空rollback journal；先让正常数据库事务/恢复完成，再预览，不删除journal绕过检查。 |
| `MEMBERSHIP_MEMBER_UNAVAILABLE` / `MEMBERSHIP_MEMBER_CHANGED` | 检查全部成员的路径、挂载、文件身份及数据库身份；成员记录继续保留，不视为退休。 |

成员完整性只是后续保留策略的前提之一；备份依赖、上传保护、个人附件边界仍需独立满足。即使所有根均为 `complete`，当前也没有成员退役命令或历史媒体、报告、归档的GC apply命令，不能据此手动清理文件。

### 5.10 受保护备份生产与保留边界（P05）

数据库迁移、`judge`、历史回填和选定视频重跑已经共用受保护备份生产入口，分别记录 `migration`、`judge`、`backfill`、`rerun` 类型。通用Python入口为 `reno.db.snapshot(con, destination, profile=..., kind=...)`，本节不新增独立备份CLI。它从源库的独立只读连接，通过SQLite backup取得**已提交数据库镜像**；不会替调用者提交或回滚事务。若调用者已有SQL事务，必须已持对应存储保护，不能在事务内才临时补锁。

完整四路径profile必须与连接的main数据库一致。镜像、描述及catalog的生产全程处于源profile的共享生命周期保护内；设施缺失会失败，不由备份函数自动初始化。离线旧库按4.1显式初始化设施，当前库由启动/setup完成准入。备份目标的父目录须已存在，目标不能覆盖现有文件，不能落在受管媒体、报告、归档根或catalog中。

需要维护脚本生成新恢复点时，可在设施已就绪、维护范围及目标路径已确定后使用以下形式；这里的示例路径须换成实际新目标，自定义实例需给 `storage_admin.profile()` 传入完整四路径：

```python
from pathlib import Path
from reno import db, storage_admin

profile = storage_admin.profile()
with storage_admin.connection(profile) as con:
    result = db.snapshot(
        con,
        Path("/srv/reno-backups/before-maintenance.db"),
        profile=profile,
        kind="maintenance",
    )
print(result["path"], result["descriptor_path"])
```

每份受管备份有以下产物，均应纳入对应恢复点的保全范围：

| 产物 | 内容与边界 |
|---|---|
| 数据库镜像 | SQLite已提交内容，包含当时数据库中的知识、个人记录等；不是只含知识的同步归档。 |
| `<备份文件>.reno-backup.json` | 源profile与物理根身份、镜像身份/SHA、源版本、依赖、归档边界，以及本地报告manifest的原字节观察。 |
| 源数据库同级 `.reno-storage-backups-v1/` | 以备份UUID登记绝对目标、类型、状态和描述依据；目标位于数据库目录外时也由此发现，不扫描整个磁盘。 |
| 迁移专用 `<备份文件>.json` | 原有迁移前后版本、行数和附件清单，独立于上述依赖描述；`attachment_files_copied=false`，不是附件副本。 |

描述中的 `consistency` 明确为 `committed-db-plus-observed-manifests`：先取数据库镜像，再保存 `incremental_diff`、`checklist`、`conflicts`、`prices`、`summary` 等本地manifest的原字节或缺失/不可读状态。**它不承诺数据库、各manifest、报告正文、媒体和个人附件在全局同一时点。** `source_version.library_id / library_epoch / kb_revision` 来自已提交镜像，不取调用者未提交的值，也不从稍后的现用库补写；旧schema缺少的字段保持未知。完整恢复仍需停写、保全相关文件并在隔离目录核验，不能把描述JSON当成文件副本。

生产先写catalog的 `reserved` 记录，镜像和依赖描述齐备后才转为 `ready`。途中只完成一部分、目标失联、描述损坏、身份变化或catalog不可读时，依赖发现会保守记录阻断；不会把残缺备份当成“不再引用任何文件”。已登记的外部目标也受此规则约束，不会因移出数据目录或暂时卸载磁盘而失去保护。未通过该入口产生、又不在受支持的固定历史路径中的任意外部副本无法自动发现，不能据此声称已覆盖全部备份。

历史备份没有冻结manifest时，本地报告依赖保持 `legacy_manifest_unknown`，不能拿今天的manifest推算当时依赖。读取历史镜像如发现非空 `-wal` 或 `-journal`，分别记录 `unmaterialized_wal` / `unmaterialized_journal` 并阻断相关依赖判断，不让immutable读取静默忽略可能承载已提交数据或恢复前像的伴随文件。应在独立维护流程中保留原件、由SQLite完成正确物化并核验，不能删除WAL/journal消除阻断。这一限制针对被发现的备份镜像；生产入口仍通过SQLite backup正确读取在线源库的已提交WAL。

`judge` 的“保留最近3份”只适用于**同一完整 `source_profile`、同一 `kind=judge` 的已登记受管备份**，候选删除前仍须核验；无法核验或执行中断时不会强行凑到3份。它不按文件名前缀扫删其它类型，不删除旧未登记的judge快照，也不删除备份引用的媒体、报告、个人附件或知识归档。迁移、回填、重跑、maintenance/service等类型不因judge运行而被清理。

备份保留通过catalog的 `operations/<UUID>.json` 固定本次候选集，并在排他保护内逐个核对身份、记录 `retiring` / `retired`。若镜像或描述删除了一部分，或身份核验阻断，下次执行 `judge` 会先找回并继续**原操作UUID与原候选集**，未完成则报 `JUDGE_BACKUP_RETENTION_BLOCKED`，不先创建新备份或启动新一轮清理。不要删除操作日志或重新选一批文件来绕过中断；这与5.9成员登记中断后使用新UUID的合同不同。

目前的受管judge备份保留不等于历史文件GC开放。成员完整性、当前与历史引用合并、保留策略及可审查的删除入口仍需后续完成；旧legacy备份继续保留，无法证明的依赖继续阻断相应清理。

### 5.11 完整引用根与历史资源只读预览（P05）

`storage-roots` 默认读取完整共享成员的当前/归档/备份/Home 引用，并列出第一项 owner 的已登记历史资源候选；默认不删除文件或提交逻辑退休。保留策略默认 publication/recovery 各3，至少1；额外保护最新实际可恢复点及活动恢复目标/保护点。只读实现见[同步方案 §13.30](sync-plan.md#1330-p05-归档保留备份整组根图与只读预览2026-10-01)，显式执行和原操作续跑见5.12。

命令要求显式完整四路径，或者提供5.9节格式的 `owners.json` 且 `declarations: []`；第一项为 owner。成员完整性须提前通过5.9节专门登记流程确认，不能在只读预览内声明。下面的路径需要换为实际实例路径：

```bash
python -m reno storage-roots --db /srv/reno-a/data/reno.db --media /srv/reno-shared/media --reports /srv/reno-a/data/generated --checkpoints /srv/reno-a/data/sync-checkpoints
python -m reno storage-roots --members-file owners.json --keep-publication 3 --keep-recovery 3
python -m reno storage-roots --members-file owners.json --namespace sync_media --namespace local_report
```

| 输出 | 含义 |
|---|---|
| `roots_sha256` / `preview_sha256` | 引用/政策摘要及本次候选摘要；执行必须另带 `--apply` 和原操作UUID，并在EX内重新匹配。 |
| `files` / `candidate_bytes` | 受控相对 key、身份 ID、状态和可信候选 bytes；只列第一 owner，不收编未知文件。 |
| `retirements` | 当前政策下可提议逻辑退休的归档；其他成员/备份仍有引用时，不表示文件可删。 |
| `namespaces` | 按类型统计候选、引用、missing、excluded、unknown；未知旧文件继续保留。 |
| `blockers` | 具体 namespace/root 的受控阻断原因；成员未知、旧备份/manifest缺证据、坏归档及资源预算均可能阻断。 |

已有设施、登记或成员组锁不可建立时，返回受控错误，不初始化数据库、配置、节点身份或锁。原 DB/WAL 在读期间变化会有限整轮重试；持续变化、非空 rollback journal、坏 sidecar、无法证明的路径/身份及预算耗尽均不能解读成“无引用”。源 SHM 不读、不 stat；活跃 WAL 在私有 TEMP 副本中解释，外部备份只读原 immutable 映像，不用 live 状态替它证明已退休。当前真实根仍为 `membership_unknown`，不会因本命令存在或只登记了自己就变成完整。

### 5.12 历史资源显式清理与原操作续跑（R07）

R07已经接入显式CLI，默认仍为5.11的纯预览。领域、入口专项与独立审查通过，冻结完整Linux回归2858 passed / 31 skipped / 6 warnings（713.97秒，exit 0），前端155项及typecheck通过；最新证据见[同步方案13.31](sync-plan.md#1331-r07-引用写入保护owner-sql-核对与清理执行2026-10-01已完成)。只处理有可信系统登记、冻结身份/SHA和完整引用证明的资源，不扫描收编未知文件，不删除个人附件、配置、身份、数据库或备份。真实成员完整性和旧依赖未知仍会阻断相关资源。

实际清理前，所有共享成员的服务、worker与维护入口都需加载这一版写入保护；修改磁盘源码不代表旧进程已经重载。先完成同版本升级与必要备份，再确认5.9的实际成员完整性及旧依赖；不能让仍运行旧写层的实例参与清理。本机正常8765服务已于12:30加载本批实现，恢复点为 `data/service-p05-r07-20261001T120125+0800-7f838511/`；schema10及知识、资料、附件、配置和身份保持。真实根仍为 `membership_unknown`，旧依赖未知未解除，未执行真实历史GC。

先使用同一完整profile/member document和同一保留政策预览。以下字符串需替换为预览返回的64位 `preview_sha256` 与本次新建UUID：

```bash
python -m reno storage-roots --members-file owners.json --keep-publication 3 --keep-recovery 3
python -m reno storage-roots --members-file owners.json --keep-publication 3 --keep-recovery 3 --apply --expected-digest "预览返回的preview_sha256" --operation-id "本次新建UUID"
python -m reno storage-roots --members-file owners.json --resume --operation-id "原操作UUID"
```

- `--apply` 必须同时带摘要和UUID。整组EX内重新扫描，退出全部只读capture后，在owner短事务内复核同一SQL投影，再原子提交operation/items/逻辑退休；文件SHA及删除不跨SQLite writer事务。摘要过期时不写journal、不删除。
- 同UUID重试apply必须保持原摘要和政策；completed仅返回原回执。`--resume`只接受原UUID和同一owner的完整显式profile，不接受新的摘要、保留代数或namespace选项；只续原selected集合，不纳入后来出现的候选。成员闭包仍按当前登记完整展开。
- 每项删除前重新检查当前base根，不借本次扫描新提议但未提交的退休释放依赖。新引用保留；改身份、未知根、特殊路径或删除失败按项阻断。备份仍持有的归档可逻辑退休，但journal明确 `retain`，物理字节保留。
- Windows通过同一DELETE句柄核SHA/冻结身份并删除，不重新打开未经证明的替身。POSIX先将原集合移入根内私有 `.reno-gc-stage-v1/<UUID>/`，核移入对象后删除；误移替身则无覆盖恢复或保留阻断。暂存恢复先持久保存精确授权，rename后的ctime登记窗口可按原UUID恢复。该目录不参与新预览的候选/未知收编；不要人工删除其中恢复材料。
- POSIX隔离依赖目录权限和所有正规写者遵守生命周期锁，不宣称能防止同一账号的恶意不合作进程任意替换私有暂存对象。已知成功删除后如fsync/停止发生，日志保留实际bytes和受控原因；无法观察到原删除的崩溃后missing记录为0，不反推释放量。

回执包含 `state`、原操作UUID、原request摘要、selected/retired数量、各项状态和 `deleted_bytes`。`state=blocked`仍保留已实际删除的非零bytes，CLI退出码2；`completed`退出码0。不能拿预览candidate_bytes当实际释放量。一次operation只在第一owner数据库提交，不能宣称多库原子退休；其他owner需单独预览/UUID。

整组EX会保持到本批文件阶段结束；当前每项重扫完整组，I/O随候选数和库体量增加。各次扫描和reader有自己的预算，本版没有整批总I/O或总时长预算，不把单次120秒上限说成整批上限。中断后用原UUID续跑，保留日志和暂存材料；不重新选更大集合绕过失败。

### 5.13 发送端永久停发与终态证明

N01–N03 的源码已接入永久 no-resend 账本、本地 v2 sender 能力门、统一发送检查，以及精确 abandon/published 终态证明，已通过该批工程回归。N04 的独立本地引用退休与 mapping 代次、N05 的预览/CAS/原操作续跑 CLI 也已接入源码，操作形态见 §5.14；本批最终冻结与回归已完成。完整合同和工程进度见[同步方案](sync-plan.md)及[产品演进方案](plans/2026-09-30-product-evolution.md)。

已接入的发送检查覆盖冻结、重新安装、prepare、verify、upload 和 push；恢复旧 v1 state/outbox 时仍核永久账本。同一批次的原 UUID、绑定和网络 payload hash 保持不变。普通同步仅续送已经明确提交的停发请求，不按年龄、超时、失败次数、租约或恢复代次自动选择真实批次。交付前重新核对当前完整 peer 快照、目标身份及令牌配置；取消与清理继续核对原 peer。

本地决定提交后先永久禁止重发，再精确解除该 publication 的 pending/attempt，随后向原节点交付。远端响应丢失时续送原 operation UUID 和原 binding。接收端已发布的错误码不能充当发布证明；发送端另查纯只读 `POST /api/sync/uploads/receipt`，核原 source/version、receiver stream/generation 和原 payload hash 后保存原回执。历史 held 或较新恢复代次不妨碍证明已知旧回执，缺原证据则保持阻断。双端必须升级到支持该新 receipt 合同的同一版本；旧端不支持或证明不符时，永久停发决定保持、远端终态继续待确认。

激活与受支持恢复必须保留完整 `DATA/sync-no-resend/` 账本，以及 DATA 下的 `sync-sender-contract.json`、`sync-sender-activated.json` 见证。备份只含数据库或旧 state/outbox 不足以覆盖这些独立永久事实；恢复时必须保留已提交的新请求与回执，同 UUID 冲突或缺失证据阻断发送，不能用旧副本抹去已有停发决定。公开操作沿 §5.14 的预览、显式提交和原 UUID 续跑执行，不手工调用内部方法。

停发、远端终态、本地引用退休是三个独立事实。旧 outbox、history/work/source-map 和无 publication 归属的 `uploaded_media` 继续保护原资源；新的按 publication 归属缓存也不替旧缓存生成退休证明。基本 Home v2/history/cache 解析仍保全部原引用根；永久账本、两份见证及合法伴随文件排除在 GC 候选之外。单批次终态不能解除其他批次、成员或备份的引用，更不授权物理删除。

本批冻结210份源码/测试并复验指纹保持，Linux完整回归3012 passed / 40 skipped / 6 warnings（711.18秒），日志 `data/p05-n01-n03-linux-full.log`。Windows相关专项通过：N01消费者179/1 skipped及最新28项，N02最新334项及后续strict/race/合法abandon尾10项，N03最新92项及源码未变的transfer/pins75项；前端155 tests和typecheck通过。首轮因peer修正而中断的运行、R07旧全量结果均不作本批全量证明；详细证据以产品方案为准。

本机最新已于17:39启动正常8765加载N04/N05；实际server118120/launcher52372父链及创建时间已核，恢复点 `data/service-p05-n04-n05-20261001T173647+0800-18d2e95f/`。schema10、46非worker表/meta/知识/绍兴资料/配置/身份/sync-state/附件保持，新worker自然续租。同步关闭未配对、成员和旧依赖unknown保持；未升级实际sender状态、弃用或删除真实批次，未执行历史GC。P06预算、条件后续T15/T16及生产部署继续保留。

### 5.14 显式发送端停发、交付与本地引用退休

当前命令已完成源码及独立审查、最终工程回归和正常本机加载。Linux全量3155/40skipped，Windows责任专项及前端155/typecheck通过；真实业务选集仍由用户明确提供。完整最新进度见主方案17.29，同步合同13.33。以下是命令形态示例，publication、operation与selection必须由明确选择和实际预览取得；本批没有替用户执行真实弃用或退休。

三个命令都必须同时指定 `--db`、`--media`、`--reports`、`--checkpoints`，DATA取所选DB父目录；缺失设施、库、节点或成员见证会阻断，不自动初始化或回落全局配置。执行时须释放服务/其他worker的实际所有权，预览可只读运行。

```text
reno sync-abandon <publication-id> --db <db> --media <media> --reports <reports> --checkpoints <checkpoints>
reno sync-abandon <publication-id> --db <db> --media <media> --reports <reports> --checkpoints <checkpoints> --apply --expected-digest <preview-digest> --operation-id <UUID>
reno sync-abandon-resume --db <db> --media <media> --reports <reports> --checkpoints <checkpoints> --operation-id <原UUID> --sender-config <显式配置文件>
reno sync-retire-local --db <db> --media <media> --reports <reports> --checkpoints <checkpoints> --request-file <selection.json>
reno sync-retire-local --db <db> --media <media> --reports <reports> --checkpoints <checkpoints> --request-file <selection.json> --apply --expected-digest <preview-digest> --operation-id <UUID>
reno sync-retire-local --db <db> --media <media> --reports <reports> --checkpoints <checkpoints> --request-file <原selection.json> --resume --operation-id <原UUID>
```

`sync-abandon --apply`只提交本地永久停发，返回`no_resend_remote_pending`时远端尚未确认。`sync-abandon-resume`沿原operation、binding和peer取得session交付，不启动普通同步；同步关闭仍可交付。远端已发布须读精确历史published回执，不当成abandoned。completed本地回放不需要sender-config，也不联网。pending交付的显式配置文件须给出原sync_cloud_url、sync_target_library_id、sync_target_node_id、sync_token；凭据不作为命令行明文参数。每次读取先核同一快照中的原peer再取token，同peer轮换有效，换peer阻断。

本地selection为闭集`{format_version:1,selected:[...]}`，不得自动按时间/失败/同SHA选择。history_work标明原history_name/raw_sha256/work_key；source_map标明原mapping_generation（legacy为null）、origin_mapping_id/raw_sha256；两类cache另外标明holder_kind/holder_name/holder_raw_sha256/video_id/descriptor，publication_cache再绑定publication_id/binding。预览和apply摘要必须来自同一选集、profile和最新源事实；resume拒绝换UUID、扩大选集或给新摘要。

停发与本地退休均保留原outbox/history/map/ledger字节，不直接删除资源；物理回收仍经独立`storage-roots`整组成员/旧备份原映像、政策、CAS和GC日志门。共享成员和旧依赖仍unknown时继续保根。永久发送契约或退休实例启用后不能降级为忽略这些事实的旧发送端；恢复须保留两个锚点和整个ledger，原state/map可审计读取，旧退休实例不能恢复成发送输入。

### 5.15 T16 个人保全与显式镜像任务（2026-10-04）

本批新增源码入口，实际服务是否已加载以产品主方案最新维护记录为准。个人保全默认纯读；在停止实际服务、worker 和其它写者后，可显式新建原 UUID 的独立包。写入口不会停止进程或借租约接管活 worker。

```text
reno personal-preservation
reno personal-preservation --operation-id <原UUID>
reno personal-preservation --create --apply --operation-id <新UUID>
reno personal-preservation --verify --operation-id <原UUID> --expected-request-sha256 <原request摘要>
reno personal-preservation --resume --apply --operation-id <原UUID> --expected-request-sha256 <原request摘要>
```

四根路径参数要么全部省略使用当前配置，要么同时指定 `--db/--media/--reports/--checkpoints`。原实际 `RENO_CONFIG_FILE` 单独作为 CONFIG 根保留；不能以自定义 DB 路径推断另一份配置。包包含原配置和个人资料，在原 DATA 下设当前用户与 SYSTEM 的私有权限，不属于可直接上云或分享的归档。包完成仍不授予恢复、发送或切主权限；status 的 recorded ready 不能替代 `--verify`。

个人镜像另走已配置、已启用、已配对的原 sender；先停本机 worker 后显式选择快照，随后重新启动正常服务来处理已选任务，或明确 `--wait` 使用当前配置的 worker。操作路径如下：

```text
reno personal-mirror-init --apply
reno personal-mirror --operation-id <新UUID> --snapshot-id <新UUID>
reno personal-mirror --operation-id <同UUID> --snapshot-id <同UUID> --apply --expected-snapshot-sha256 <同次预览摘要>
reno personal-mirror-status --operation-id <原UUID> --expected-request-sha256 <原request摘要>
reno personal-mirror-deliver --apply --operation-id <原UUID> --expected-request-sha256 <原request摘要> --request-id <独立任务UUID>
```

已有前驱时，预览和提交都同时指定 `--expected-head-operation-id <已提交前驱UUID>`。snapshot SHA 在 immutable request 写前重新核对，变化须重新预览；旧 operation ID 只续其原快照，不换当前资料。发送任务 request ID 与镜像 operation ID 独立；默认仅入持久队列，`--wait` 在入队前拒绝非配置 profile。原结果不确定时沿原 UUID/request SHA 查状态或显式重新排队；普通同步定时器不替用户选择个人批次。云只读页面已按§5.16实现，持久停写/切主、可撤销分享仍待后续实现，完整传输协议见 [同步方案 §12.3](sync-plan.md#123-t16-显式保全与个人镜像操作入口2026-10-04)。

### 5.16 T16 云端个人镜像只读入口（2026-10-04）

以下入口已实现源码，当前实例实际加载状态以产品主方案最新维护记录为准。部署前使用同版本后端与前端；保留原配置与节点身份，cloud不启动家庭worker，也不开放处理流水线或GLM调用。只读功能不替代家庭端配对和显式镜像发送。

1. 沿既有同步配对与§5.15显式任务路径安装完整个人镜像。prepared、stored或保全包ready均不能替代receiver的installed head/receipt证明。
2. 在选定cloud实例的私有配置中显式设置`personal_cloud_read_enabled: true`；现场记录另需`field_checks_enabled: true`。读取开关与transport开关独立，已安装镜像不因传输租约到期丢失读取资格。未安装或证明失效时保持禁读并显示原因。
3. 通过`/api/capabilities`核`personal_mode=mirror`、`personal_read=true`、所有write/upload为false，以及source主/任期和installed版本；项目、决定卡、来源、核对页面显示来源与云端只读提示。请求/响应绑定`X-Reno-Personal-Read-Version`，错版409后重新读取能力，不能沿旧版本继续操作。
4. 个人JSON、Markdown导出及附件均`private, no-store`。附件metadata可能显示不可读，原文字仍保留；合法下载由同一fd完整核SHA后响应。cloud上传、个人/现场写入和主实例operation receipt始终403 `PERSONAL_CLOUD_READ_ONLY`，不创建staging或SQL副作用。

关闭`personal_cloud_read_enabled`只撤销读取入口，不删除镜像或原个人资料。上述开关不授切主、恢复写权或分享权限；不要通过改`node_role=all`、复制旧主identity或重写primary/epoch来开放cloud写入。持久停写、安全切主与可撤销分享仍待独立实施。实际工程结果与受保护本机加载见产品主方案17.90及后续记录，隔离浏览器通过不代表已部署生产云。

## 6. 国内网络提示

- pip 慢：`export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 再跑脚本。
- NodeSource 慢：用 `--dist-tgz` 离线前端包，服务器完全不装 Node。
- GitHub clone 慢：`--repo` 可指定不含凭据的普通 HTTPS Git 镜像或 `file:///` 本地镜像；带嵌套 URL、查询参数或用户信息的代理地址会被参数校验拒绝。

## 7. 服务器配置参考（5 个并发用户，实测依据）

实测（2026-09-29，本机同版本）：

| 指标 | 实测值 | 说明 |
|---|---|---|
| serve 进程内存（空载/10 并发突发后） | 53–54 MB | whisper/rapidocr/yt-dlp 全部惰性导入，纯服务态不加载 |
| 视频流 | /api/video 带 Range 206 | 单请求读 1–4MB 切片入内存，5 路并发 ≈ 十几 MB 瞬时 |
| /ask | 外部 LLM API | 服务器只等待（I/O），无本地推理 |
| 磁盘 | venv ~0.6GB + 数据当前 1.2GB | media 随视频数线性增长（现 28 视频 ≈ 1.2GB） |
| 前端构建（服务器上构建时才需要） | node 峰值 ~0.55GB 内存（实测 node 22） | 不想在服务器装 Node 就用 --dist-tgz |

结论：

| 档位 | 配置 | 适用 |
|---|---|---|
| 最低可用 | 2 vCPU / 2GB / 40GB SSD / 3Mbps | 数据已导入；服务器现场构建前端也够（峰值 0.55GB）；1–2 人同时看视频片段 |
| **推荐** | **2 vCPU / 4GB / 60GB SSD / 5Mbps+** | 5 并发查库/问答 + 系统/页缓存余量舒适；多人同时看视频不卡 |
| 重度视频 | 上述 + 按流量计费或 ≥8Mbps | 多人同时拖动时间轴看长视频是带宽大头，CPU 反而不是 |

5 并发的页面请求（JSON 搜索/对比/报告）对单进程 FastAPI + SQLite 是毫秒级负载，
瓶颈只会出现在：①多人同时看视频（带宽）；②在服务器上跑处理流水线（不建议）。
轻量云（腾讯/阿里 2C4G 档）绰绰有余。
