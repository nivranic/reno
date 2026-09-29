# reno 云上部署（一键脚本 + 容量参考）

> `deploy/deploy.sh`：Ubuntu/Debian 一键安装/更新（幂等，重复执行同一命令即升级）。
> 架构：FastAPI 单进程托管 SPA + /api + SQLite + 媒体文件流，无数据库服务、无前端独立进程。

## 1. 场景与建议形态

| 形态 | 说明 |
|---|---|
| **推荐：本地处理 + 云端只读** | 采集/ASR/OCR/atomize 等重活在开发机跑（CPU/磁盘友好），云端只 `serve` 提供搜索/问答/对比/报告。云端资源需求极低（见 §7）。 |
| 云端全功能 | 也可在服务器上 `reno import/run`，但 ASR(faster-whisper CPU) 很慢且吃内存，不推荐 2C4G 以下做。 |

## 2. 一键部署

```bash
# 在云服务器上（Ubuntu 22.04/24.04，root）
curl -fsSL https://raw.githubusercontent.com/nivranic/reno/main/deploy/deploy.sh -o deploy.sh
# （或 git clone 后 bash deploy/deploy.sh）
sudo bash deploy.sh --auth-token 换成随机长串 --api-key 你的智谱APIKey
```

脚本完成：系统依赖 → clone 到 `/opt/reno` → venv + pip → 前端构建（或 `--dist-tgz` 离线包）→
`config.local.json`（只写提供的键，保留已有键）→ systemd 服务 `reno`（专用用户、开机自启、
崩溃自动重启）→ 健康检查。

**角色**（`--role`，默认 `all` 单机全功能）：

- `--role cloud`：云端纯服务面——不起流水线 worker、`/api/import` 只入队等本地同步器、
  `glm-5.3*`（coding plan）模型在 llm 层直接拒绝（配错也打不出去）、`/api/config/model` 403。
  云端的 `--api-key` 应配 **paas key** 并在 config.local.json 设 `ask_model` 为非 glm-5.3* 的
  paas 模型（如 glm-4-flash 档），供 `/ask` 使用；无 key 时 /ask 自动降级纯检索，不会 500。
  完整分体架构（家庭机推理 + 定时同步）见 `docs/sync-plan.md`。
- `--role home`：家庭机全功能（现状行为，显式声明用）。

常用变体：

```bash
sudo bash deploy.sh --dist-tgz reno-dist.tgz        # 服务器不装 Node，用本地构建产物
sudo bash deploy.sh --import-data reno-data.tgz ... # 首次部署顺带导入本地知识库
sudo bash deploy.sh --host 127.0.0.1 ...            # 只监听回环，配合 nginx 反代
```

所有参数见 `--help`。**更新 = 重跑同一命令**（拉代码、重装依赖、重建前端、重启服务）。

## 3. 数据与前端产物迁移（开发机 → 服务器）

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
- 导入是**覆盖式**（db 整文件替换、media 目录覆盖）；重复导入等于回到打包时刻的快照，
  服务器上产生的用户决策（冲突复核状态等）会被覆盖——先在服务器侧 `tar` 备份再导。
- 迁移前先在开发机跑一次路径规范化（存量库有反斜杠路径，云端直接用会断）：
  `.venv/Scripts/python -m scripts.normalize_files_json`（幂等，先快照 db 更稳）。

## 4. 运维

```bash
journalctl -u reno -f          # 日志
systemctl restart reno         # 重启
sudo bash deploy.sh ...        # 升级（同参数重跑）
```

数据库/媒体在 `/opt/reno/data` 与 `/opt/reno/media`（按默认参数）；定期快照照旧
`sqlite3 reno.db ".backup ..."` 或直接 tar（服务可在线，SQLite WAL 下建议先 checkpoint）。

## 5. 公网安全（必读）

- **`auth_token` 必须设置**：脚本参数或交互输入均可；令牌走 `X-Reno-Token` 头或 `?token=`。
- `?token=` 会出现在 URL 里，**明文 HTTP 下等于裸奔**——公网请配合 nginx + HTTPS
  （`deploy/nginx-reno.conf`，certbot 一条命令拿证书），并让 reno 只监听 `127.0.0.1`。
- 防火墙只开 80/443；`ufw deny 8765`。
- API Key 只存服务器 `/opt/reno/config.local.json`（600 权限，脚本已设）。

## 6. 国内网络提示

- pip 慢：`export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 再跑脚本。
- NodeSource 慢：用 `--dist-tgz` 离线前端包，服务器完全不装 Node。
- GitHub clone 慢：`--repo` 可指镜像（如 `https://ghfast.top/https://github.com/nivranic/reno.git`）。

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
