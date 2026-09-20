# reno · 装修短视频结构化知识库

个人装修知识操作系统:短视频收藏 → 多模态提取 → 知识原子 → 去重/冲突 → 施工/验收工具。
本仓库是《落地方案》(见 `../reno-feas/report/落地方案.md`)的 P0/P1/P2 实现。

## 快速开始

```bash
# 1) 环境:Windows + Python 3.10+;首次安装
python -m venv .venv
.venv/Scripts/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  faster-whisper scenedetect opencv-python-headless pillow rapidocr-onnxruntime \
  opencc-python-reimplemented jsonschema imageio-ffmpeg yt-dlp pyyaml \
  fastapi "uvicorn[standard]" jinja2 python-multipart httpx pytest

# 2) 配置 config.local.json(已含或参考字段):
#    zhipu_api_key / atomize_model=glm-4.6 / vlm_model=glm-4.6v / judge_models 回退链

# 3) 采集与处理
.venv/Scripts/python -m reno import "https://www.bilibili.com/video/BVxxxx/"
.venv/Scripts/python -m reno run            # 处理队列(断点续跑,孤儿job自动恢复)
.venv/Scripts/python -m reno judge          # 跨视频去重聚类 + 冲突检测
.venv/Scripts/python -m reno report         # 增量diff / Checklist / 争议报告

# 复核界面(产品核心)
.venv/Scripts/python -m reno serve          # http://127.0.0.1:8765

# 5) 前端(React SPA,可选开发模式;生产无需 Node)
cd frontend && npm install && npm run build   # 构建后 reno serve 同源托管
cd frontend && npm run dev                    # 开发(5173,/api 代理到 8765)
cd frontend && npm run dev:mock               # Mock 演示模式(MSW fixture)
```

前端为 React 19 + Vite + Tailwind 的证据工作台(六页面:收件箱/工作台/搜索/争议复核/报告/采集助手)。
技术方案、回滚(`"web_ui": "classic"` 切回旧界面)、设计系统与测试报告见
`docs/frontend-migration.md` / `docs/frontend-design-system.md` / `docs/frontend-testing.md`。

## 架构(一图)

```
浏览器复核界面(四联动/冲突卡/FTS搜索)
   │
FastAPI ── SQLite(11表 + FTS5)
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
- **冲突不做多数表决**:authority_level(cited_standard vs 经验)进判定;数值冲突升级 verify_authoritative_source。
- **人工决策不可覆盖**:user_decision 独立表,只增不改。

## 手机采集(iOS 快捷指令,免写App)

快捷指令:`获取剪贴板` → `URL 编码` → `获取 URL 内容`(POST `http://<家庭IP>:8765/api/import`,请求体 JSON `{"url":"剪贴板内容"}`)。配合 Tailscale 全网可达。

## 测试

```bash
.venv/Scripts/python -m pytest tests/ -q          # 单测(归一化/dhash/融合/预算均匀性/db/JSON容错)
cd frontend && npm run test                       # 前端单测(Vitest, 33)
cd frontend && npx playwright test                # 前端 e2e(真实后端, 7)
.venv/Scripts/python -m reno status               # 全库状态
```

## 离线边界

前端构建产物(字体/图标/脚本/样式)全部本地资源,零 CDN、零远程字体;`reno serve`
关闭公网仍可完整使用界面(外部视频下载与云端 LLM 调用除外,如实说明)。

## 目录

- `reno/` 流水线包(ingest/media/asr/ocr/vlm/atomize/dedup/judge/report/pipeline/worker/cli)
- `reno/dict/` 参数字典/同义词/taxonomy(版本化)
- `reno/prompts/` 原子化与判定 Prompt(版本化)
- `app/` FastAPI(JSON API + SPA 承载;`app/templates/` 为旧 Jinja2 界面=回滚路径)
- `frontend/` React SPA(src/features 六页面 + ui/shared 组件 + mocks + tests/e2e)
- `docs/generated/` 自动生成报告
- `data/reno.db` SQLite(含 FTS5)
