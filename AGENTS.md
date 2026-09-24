# reno · 装修知识库（B站/抖音视频 → 结构化原子知识）

项目从 `F:\ZCode_data\.zcode\workspace\default\reno` 迁移至本目录（E:\Mix\project\reno），迁移于 2026-09-24 完成。

## 快速上手

```bash
# 起服务（127.0.0.1:8765，SPA + API 一体）
.venv/Scripts/python -m reno serve

# 常用 CLI：import / run / judge / report / status
.venv/Scripts/python -m reno status

# 前端（已构建的 SPA 在 frontend/dist，生产由后端托管；开发模式另起端口）
cd frontend && npm run dev
```

桌面有双击启动器 `reno启动.bat`（指向本目录，2026-09-24 已更新路径）。

## 架构（一屏）

- `reno/` Python 包：ingest→media→asr→ocr→vlm→atomize 流水线（步骤标记在 meta 表，断点续跑）；judge 跨视频聚类/冲突；FTS 搜索；report 生成
- `app/` FastAPI：SPA 托管（web_ui 可切 react/classic/auto 回滚）+ /api/*
- `frontend/` React 19 + TS + Vite + Tailwind4 + Router7 + TanStack Query + Zustand + 虚拟滚动；Vitest+MSW 测试
- `data/reno.db` SQLite（含 FTS5）；`media/` 原片/音频/帧；均 gitignore
- GitHub: `github.com/nivranic/reno`（main 分支）

## 关键事实（2026-09-24 状态）

- **处理模型**：GLM-5.3-FlashX（atomize + judge），可在采集页 Select 切换（预设 GLM-5.3 / 5.3-Flash / 5.3-FlashX），写 config.local.json 即时生效无需重启
- **模型路由**：glm-5.3* 走 `https://open.bigmodel.cn/api/anthropic/v1/messages`（coding plan 配额，max_tokens 128000，thinking 默认 disabled）；其他模型走 paas v4。API key 只存在 `config.local.json`（gitignore，永不入库）
- **数据规模**：21 视频 / 158 原子 / 6 聚类 / 5 冲突（2026-09-23 用 FlashX 全量重跑提质，130→158；旗舰冲突=插座安装方向 5 组带场景条件）
- **已知坑**：
  - evidence modality 在库里是小写 asr/ocr/vision，前端 toModality() 负责归一
  - 旧库迁移曾丢时间轴数据；`scripts/fix_files_json.py` 可修 files_json 损坏行
  - 重跑某视频 atomize 需同时删 meta 的 `step:<vid>:atomize` 标记和 processing_run 里 ok=1 的 atomize 行（`scripts/rerun_atomize_flashx.py` 是范本，且要先清旧原子防残留）
  - React 里 javascript: URL 会被拦，书签脚本用 ref.setAttribute 挂
- **备份**：重跑前快照 `data/reno.db.bak-20260923-211142`

## 约定

- 修改后最小验证：`pytest`（tests/）+ `cd frontend && npm run typecheck && npm run test`
- 提交信息用英文 conventional 风格；推送前按用户规则清理无意义提交
- vlm_enabled 目前 false（配额原因，恢复后可开）
