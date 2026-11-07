# reno · 装修短视频结构化知识库

当前工程交接、最新静态候选与剩余开发事项见 [交接入口](docs/HANDOFF.md)；源码交付不代表完整产品或原生运行验收。

个人装修知识操作系统:短视频收藏 → 多模态提取 → 知识原子 → 去重/冲突 → 施工/验收工具。
在视频知识库上新增个人装修条件、决策卡、人工资料与现场核对。实施范围、验证证据和未完成的发布门槛见 [产品演进方案](docs/plans/2026-09-30-product-evolution.md)。

## 快速开始

```bash
# 1) 环境:Windows + Python 3.11+（sqlite3 需支持 serialize/deserialize）；首次安装
python -m venv .venv
.venv/Scripts/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  faster-whisper scenedetect opencv-python-headless pillow rapidocr-onnxruntime \
  opencc-python-reimplemented jsonschema imageio-ffmpeg yt-dlp pyyaml \
  fastapi "uvicorn[standard]" jinja2 python-multipart httpx pytest

# 2) 配置 config.local.json(已含或参考字段):
#    zhipu_api_key / atomize_model=glm-4.6 / vlm_model=glm-4.6v / judge_models 回退链

# 3) 全新安装显式初始化；已有旧库不要执行 init-db
.venv/Scripts/python -m reno init-db
# 旧库升级：先停止服务、worker和其它写入进程，关闭数据库连接
# 已具备生命周期锁设施：.venv/Scripts/python -m reno migrate-db
# 缺设施的旧库：.venv/Scripts/python -m reno migrate-db --initialize-storage-offline

# 4) 采集与处理
.venv/Scripts/python -m reno import "https://www.bilibili.com/video/BVxxxx/"
.venv/Scripts/python -m reno run            # 断点处理；serve守护进程等待租约到期后恢复中断任务
.venv/Scripts/python -m reno judge          # 跨视频去重聚类 + 冲突检测
.venv/Scripts/python -m reno report         # 增量diff / Checklist / 争议报告
.venv/Scripts/python -m reno report --summary # 明确调用模型生成AI总结

# 复核界面(产品核心)
.venv/Scripts/python -m reno serve          # http://127.0.0.1:8765

# 5) 前端(React SPA,可选开发模式;生产无需 Node)
cd frontend && npm install && npm run build   # 构建后 reno serve 同源托管
cd frontend && npm run dev                    # 开发(5173,/api 代理到 8765)
cd frontend && npm run dev:mock               # Mock 演示模式(MSW fixture)
```

迁移会在已建立的文件保护内生成数据库镜像、依赖描述和附件清单，JSON清单不是附件副本。缺设施时，普通 `migrate-db` 在打开迁移连接前拒绝；即使已是当前schema的空操作，也不会隐式初始化设施。自定义实例须同时提供 `--db`、`--media`、`--reports`、`--checkpoints`，不能只换数据库路径。停写迁移示例及恢复边界见[部署说明](docs/deploy.md#41-个人域与数据库升级2026-09-30)。

前端为 React 19 + Vite + Tailwind 的证据工作台，包含问答、比较、筛选浏览、复核和维护任务；“我的装修”聚合个人决定、来源和现场清单。
`"web_ui": "classic"` 仅切换旧版知识界面，不提供新的个人域页面，也不是数据库回滚。技术方案、设计系统与测试报告见
`docs/frontend-migration.md` / `docs/frontend-design-system.md` / `docs/frontend-testing.md`。

## 架构(一图)

```
浏览器复核界面(四联动/冲突卡/FTS搜索)
   │
FastAPI ── SQLite(知识、复核、个人记录、任务及 FTS5)
   │
worker: ingest → media(场景/帧预算) → asr(faster-whisper)
        → ocr(RapidOCR+水印过滤+span融合) → vlm(glm-4.6v 云视觉)
        → atomize(glm-4.6 结构化,证据ID强校验) → judge(聚类/冲突)
```

## 关键设计(全部有实验依据,见可行性报告)

- **毫秒时间轴**:所有证据 start/end_ms 对齐视频容器;ASR 尾段 clamp。
- **证据强校验**:LLM 引用的证据 ID 必须存在于时间轴,无效即丢;原子零有效证据直接丢弃 → 可追溯率恒为 100%。
- **参数双层字典**:名称归一(淋浴区涂刷高度→卫生间墙面防水高度)+ 单位归一(cm/m→mm);未覆盖参数标 `unnormalized`,禁止 LLM 擅自补全。
- **帧预算均匀分布**:快剪视频 128 镜头也不失控;预算内覆盖全片(修复"头部截断"缺陷)。
- **证据回跳实时抽帧**:点击原子 → 播放器 seek + ffmpeg 实时抽当前帧(帧级精度,不依赖采样密度)。
- **冲突不做多数表决**：evidence_nature 区分视频转述标准与经验；复核与内容指纹绑定，内容变化后要求重新核对。
- **个人采用与知识复核分开**：user_decision 保存复核事件，decision_card 保存本项目的取舍；个人采纳不改全库结论。
- **复核对账**：`/reconciliation` 按当前待核对、历史和原因筛选复核记录，区分冻结内容与当前来源，支持完整 Markdown 导出；不会自动重绑旧记录或改变决定。

## 个人装修与现场记录

新安装默认关闭个人功能。先在隔离样本上完成验收与备份恢复，再在选定的 `all/home` 主实例配置 `personal_features_enabled: true`；现场记录另需 `field_checks_enabled: true`。本机已按授权启用并保存绍兴所在地与待核实资料，实施及维护记录见产品方案。

- 默认“我的装修”可跳过资料直接使用；本次问答条件与项目条件分开，旧回答保留当时条件。
- 决策卡保存理由、候选、待确认事项和来源快照；引用变化会提示复看，可导出单卡或选定卡片。
- 来源支持人工记录及 JPEG/PNG/PDF 附件；默认图片 10 MiB、PDF 20 MiB、每来源 20 份。资料不自动成为已核实规范。
- 现场清单按条目版本保存检查、整改、复检和作废；判据不完整时不能记录“符合”。个人要求与规范条款分别标识。
- 文字草稿在当前标签页暂存，照片需要明确上传；没有提交回执不会显示已保存。仅复制数据库到另一节点不会获得个人写权限。

云端个人镜像和切主尚未开放。开关关闭保留数据。备份应同时包含数据库、附件和对应清单；旧 judge 全库快照不能作为保留个人数据的知识回退方式，详见 [部署与恢复](docs/deploy.md)。

## 家庭处理与云端知识同步

`home` 节点可通过既有 worker 处理 cloud 的采集队列，上传媒体、完整知识快照和报告，双向合并知识复核事件。同步默认关闭，需分别初始化两端独立库、显式配对并配置目标身份及独立令牌；采集页显示队列和家庭同步状态。个人项目、决定、现场记录及附件尚不随知识同步复制。

Ubuntu/Debian 部署支持 `--tls-domain`，后端监听回环，nginx 与 Certbot 配套完成证书检查、代理和续期。凭据使用文件或环境变量；具体安装、升级、配对及隔离双实例脚本见 [部署说明](docs/deploy.md)。Linux/Windows 隔离同步与浏览器播放已有证据，真实平台、模型、公网 ACME 和生产云部署仍分别验证，详见 [同步方案](docs/sync-plan.md)。

当前live schema10支持发布前知识归档、复核对账、受控恢复和独立关联有效性；归档自身仍用schema9，继续兼容7/9。`reno sync-checkpoints` / `sync-checkpoint-preview <id>` 只读核验，`reno reconciliation --markdown` 导出对账；归档依赖原媒体与报告文件，不是整库备份。`sync-restore` / `sync-resume` 默认预览，显式执行需操作 UUID 和预览版本；恢复后持久暂停，家庭端完整重发确认后续传。旧关系显示依据未知，只有显式“更新争议”才调用模型生成新绑定；问答、对比及报告保留历史限制。操作见[部署说明](docs/deploy.md#56-关联判定依据p04)，资源回收见下文P05入口，P06预算控制仍在[后续范围](docs/sync-plan.md#1312-phase-2-分批实施合同2026-10-01实施中)。

API 帧缓存默认限制为 512 MiB，按 LRU 回收已登记的可再生帧；`reno frame-cache` 只读预览，显式执行须带预览摘要。历史未登记文件和处理证据帧保留，详见[缓存容量管理](docs/deploy.md#57-api-帧缓存容量p05-子项)。P05 历史资源清理、发送端永久停发与精确终态证明、独立本地引用退休及 mapping 代次（N04）、公开预览/CAS/原操作续跑入口（N05）均已实现。最终冻结220份源码/测试，Linux完整 **3155 passed /40 skipped /6 warnings（939.50秒）**，前端155 tests/typecheck通过；工程及本机加载证据见[产品方案 §17.29](docs/plans/2026-09-30-product-evolution.md#1729-p05-独立本地退休映射代次与公开操作入口2026-10-01工程完成本机已加载)。P06预算正在进入首批实施，条件后续T15/T16及生产部署继续保留。

永久停发决定、远端终态和本地引用退休分别保存；完整证明只解除明确选中的引用，其他 history/work/source-map、旧缓存、成员和备份继续独立保根，账本及见证始终排除在 GC 候选之外。`sync-abandon`、`sync-abandon-resume`、`sync-retire-local` 要求完整四路径，默认预览不初始化或联网，显式提交及原UUID续跑见[发送端操作](docs/deploy.md#514-显式发送端停发交付与本地引用退休)。启用与恢复必须保留永久账本和两个见证，双端同版本并支持精确receipt合同。本机17:39正常8765已加载本批，schema10知识、资料及个人数据保持，同步仍关闭未配对；真实成员/旧依赖未知保持，未执行实际publication弃用、引用退休或历史GC。

schema10的文件保护已接入上传、发布、报告、媒体和归档。同步使用protocol 2：完整声明媒体集合，再逐文件校验；旧配对须双端升级后显式刷新协议，冻结outbox保持原样。`reno storage-status`纯只读检查设施，`storage-init`仅显式初始化锁设施；服务启动在worker前登记自身成员，本次实际新建根可记录 `fresh`，旧根保持 `membership_unknown`。已登记实例凭自身根与本地提交见证启动，不要求其它成员在线；完整成员预览与登记仍核对全部已知组合。见[设施与升级边界](docs/deploy.md#58-文件生命周期设施与-schema10p05-基础)及[成员登记](docs/deploy.md#59-共享存储根的成员登记p05)。

P05已接完整共享成员的引用根、归档最近N代/实际可恢复点保留、备份原映像退休证明，以及默认只读的 `reno storage-roots` 预览。要求显式完整路径或成员文件，返回第一owner的受管候选/bytes、引用摘要与阻断原因，不声明成员完整性。**显式清理和原操作失败续跑已实现**：`--apply --expected-digest <预览摘要> --operation-id <UUID>`，续跑使用 `--resume --operation-id <原UUID>`。真实成员完整性及旧依赖未知仍阻断相关资源；本机未执行真实历史GC。命令与限制见[部署5.12](docs/deploy.md#512-历史资源显式清理与原操作续跑r07)。

迁移、judge、回填及选定视频重跑的备份已统一为受保护数据库镜像与原字节manifest观察，并登记外部备份位置；这不承诺数据库、报告及附件处于全局同一时点。judge仅对同一来源配置、同一 `judge` 类型的已登记备份保留最近3份，中断清理下次沿原操作续跑，旧未登记备份不自动删除。历史媒体、报告和归档的显式GC只处理有可信登记及完整引用证明的资源；备份仍引用时保留字节。受保护备份和保守依赖处理见[备份生产](docs/deploy.md#510-受保护备份生产与保留边界p05)。

## 手机采集(iOS 快捷指令,免写App)

快捷指令:`获取剪贴板` → `URL 编码` → `获取 URL 内容`(POST `http://<家庭IP>:8765/api/import`,请求体 JSON `{"url":"剪贴板内容"}`)。配合 Tailscale 全网可达。

## 测试

```bash
.venv/Scripts/python -m pytest tests/ -q          # 单测(归一化/dhash/融合/预算均匀性/db/JSON容错)
cd frontend && npm run typecheck && npm run test  # 前端单测与类型检查
# E2E必须先创建全新人工样本根并设置RENO_E2E_MANIFEST；参见产品方案13.2
.venv/Scripts/python -m reno status               # 全库状态
```

## 离线边界

前端构建产物(字体/图标/脚本/样式)全部本地资源,零 CDN、零远程字体;`reno serve`
家庭服务可达时，关闭公网仍能读取本地知识和记录；外部下载及云端模型不可用。手机与服务断开时只能保留当前文字草稿，不承诺照片离线保存或自动补写。

## 目录

- `reno/` 流水线包(ingest/media/asr/ocr/vlm/atomize/dedup/judge/report/pipeline/worker/cli)
- `reno/dict/` 参数字典/同义词/taxonomy(版本化)
- `reno/prompts/` 原子化与判定 Prompt(版本化)
- `app/` FastAPI(JSON API + SPA 承载;`app/templates/` 为旧 Jinja2 界面=回滚路径)
- `frontend/` React SPA(src/features 产品页面 + ui/shared 组件 + mocks + tests/e2e)
- `data/generated/` 报告版本与发布清单（随 RENO_DATA_DIR 重定位）
- `data/personal-attachments/` 个人附件（通过鉴权API读取）
- `data/reno.db` SQLite(含 FTS5)
