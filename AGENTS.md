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

## 关键事实（2026-09-29 状态）

- **数据规模**：28 视频 / 349 原子 / 16 聚类 / 13 冲突（2026-09-29 查库实测）
- **知识模型 v2（2026-09-29 多维知识体系落地）**：原子新增 4 列——`dimension` 知识维度（15 类受控，判不了留空待归类）、`evidence_nature` 证据性质（单一事实源，v1 的 conditions.authority_level 已迁移）、`exceptions` 不适用例外、`prices_json` 结构化价格（对象/口径类型/含项/时点；金额非数值拒收）。**stage 不再由 LLM 判断**，由 taxonomy v2 的 stage_map 从 category 确定性派生。词表 `reno/dict/taxonomy.yaml`；全量说明与验证见 docs/multidim-audit-2026-09-29.md
- **prompt v2**：atomize_v2（维度/性质/例外/规范条件键/价格结构）、judge_v2（a_nature/b_nature 注入 authority_gap 判定）；回滚写 `config.local.json` `"atomize_prompt_version": "v1"`。存量回填用 `scripts/backfill_atoms_v2.py`（幂等，dimension IS NULL 为选择器，meta 游标断点续跑）
- **检索/对比**：/api/search 与 /ask 共用检索（整句短语 + CJK bigram 兜底 + 同义词扩展，组合词不再 0 命中）；筛选轴 category/space/stage/dimension；/api/compare 按 15 维度分组对比 2-3 对象（词面闸门去噪，复用冲突聚类提示），前端 /compare 页
- **报告**：新增 prices.md（价格记录，无数据时诚实声明"暂无可核验价格数据"）；checklist v2 验收项带判定依据、缺失标"待核实"，不编造阈值。行情预测/源头成本明确不做（无数据来源）
- **鉴权（局域网/手机访问）**：`config.local.json` 加 `"auth_token": "<令牌>"` 即开启 /api 令牌门（X-Reno-Token 头或 ?token= 查询参数；媒体标签用后者）；留空 = 关闭。前端 401 会弹出令牌输入门（存 localStorage）
- **数据目录重定位**：环境变量 `RENO_DATA_DIR` 可整体迁移 db/媒体/帧缓存（多实例隔离、测试用）
- **任务续跑**：serve 启动时自动恢复 pending 任务（守护线程 worker.process_pending），无需手动 `reno run`
- **judge ID 稳定化**：冲突/聚类 ID 已改为内容哈希（cfl_/clu_ 前缀），judge 重跑后用户决策自动回填 status；重跑前自动快照 `data/reno.db.bak-judge-*`（保留 3 份）
- **测试**：pytest 71 例（健壮性 28 + 增强 10 + 多维 15 + 原始 10 + 性能 5 + 多进程 3 + LLM 探针 4 需 `RENO_LLM_TESTS=1`）+ 前端 43 例；CI 在 .github/workflows/ci.yml
- **移动端 H5**：底部导航/安全区/触控目标已适配，真机核对清单见 docs/mobile-h5-checklist.md
- **知识问答（/ask）**：基于知识原子的接地问答——[n] 引用芯片跳视频时间点（带证据性质徽章）、冲突主题并列双方观点、价格问题注明证据时点、条件缺失分情景回答、LRU 缓存 64 条（问题|模型|k|库版本键）；报告页含 LLM 全库总结（summary.md）
- **布局样式规则**：控件高度档位/同排同高/令牌纪律/弹层 sticky 等统一规则见 docs/frontend-style-rules.md（2026-09-27 全局审计后沉淀；根字号 15px，h-8=30px、h-9=33.75px、h-11=41.25px）
- **SPA 缓存**：index.html 以 `Cache-Control: no-cache` 返回（app/main.py），重建后浏览器不会再用旧壳混载新 chunk

- **处理模型**：GLM-5.3-FlashX（atomize + judge 默认），可在采集页 Select 切换（预设 GLM-5.3 / 5.3-Flash / 5.3-FlashX），写 config.local.json 即时生效无需重启
- **模型路由**：glm-5.3* 走 `https://open.bigmodel.cn/api/anthropic/v1/messages`（coding plan 配额，max_tokens 128000，thinking 默认 disabled）；其他模型走 paas v4。API key 只存在 `config.local.json`（gitignore，永不入库）
- **已知坑**：
  - evidence modality 在库里是小写 asr/ocr/vision，前端 toModality() 负责归一
  - 重跑某视频 atomize 需同时删 meta 的 `step:<vid>:atomize` 标记和 processing_run 里 ok=1 的 atomize 行（`scripts/rerun_atomize_flashx.py` 是范本，且要先清旧原子防残留）
  - React 里 javascript: URL 会被拦，书签脚本用 ref.setAttribute 挂
  - 旧库迁移曾丢时间轴数据（已修复案例）；files_json 损坏行用 `scripts/fix_files_json.py` 修

## 约定

- 前端布局/尺寸/状态改动遵循 docs/frontend-style-rules.md；色彩/字体/动效令牌见 docs/frontend-design-system.md（两份互补）
- 修改后最小验证：`pytest`（tests/）+ `cd frontend && npm run typecheck && npm run test`
- 提交信息用英文 conventional 风格；推送前按用户规则清理无意义提交
- vlm_enabled 目前 false（配额原因，恢复后可开）
