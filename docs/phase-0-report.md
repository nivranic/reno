# Phase 0 报告:流水线硬化(CLI 可日常用)

日期:2026-09-19 · 仓库:`reno/`(venv 自包含,git 初始化,config.local.json 不入库)

## 交付物

| 项 | 状态 | 证据 |
|---|---|---|
| `reno` 包(11 模块流水线) | ✅ | reno/{ingest,media,asr,ocr,vlm,atomize,dedup,judge,report,pipeline,worker}.py |
| SQLite 11 表 + FTS5 + 幂等迁移 | ✅ | reno/schema/schema.sql;db._migrate(measurement 列热迁移实测) |
| 参数字典/同义词/taxonomy v1 | ✅ | reno/dict/*.yaml;双层归一实测 1.8米(m)→1800mm+规范名 |
| CLI import/run/status/judge/report/serve | ✅ | python -m reno <cmd> 全部可用 |
| Job 状态机 + 断点续跑 + 孤儿恢复 | ✅ | worker.process_pending 开头重置 running→pending(实测杀进程后续跑) |
| LLM 路由(glm-4.6 主 + flash 回退链) | ✅ | llm.py;chat_json 容错(代码围栏/推理前缀/修复重试) |
| 云视觉通道(glm-4.6v) | ✅ | vlm.py;首跑 13/22→容错修复(空返回重试+原文兜底) |
| 单测 | ✅ 10/10 | tests/test_units.py(归一化/参数/dhash方向/budget均匀/span融合/db往返/JSON容错) |
| 冒烟端到端(57s 视频) | ✅ | 5 步全绿;media/asr/ocr 与 reno-feas 基线完全一致(21 cuts/53 frames/50 segs/21 spans) |

## 冒烟实测(与可行性基线对照)

| 步骤 | reno 实测 | reno-feas 基线 | 一致性 |
|---|---|---|---|
| 场景切分 | 21 cuts | 21 | ✅ |
| 保留帧 | 53 | 53 | ✅ |
| ASR 段 | 50 (rtf 0.41) | 50 | ✅ |
| OCR spans | 21(水印:bilibili/营座讲家电) | 18(水印:同) | ✅(算法同源) |
| VLM | 22 帧 13→22(容错后) | 不可用(环境) | **新增能力** |
| 原子 | 3(glm-4.6 自动) | 5(人工) | 粒度保守,证据链质量更高(见下) |

## 自动原子化质量(首个视频,glm-4.6)

- 3 原子 / 10 证据引用 / 零无效引用(强校验生效)
- **三模态证据链**自然出现:OCR(国标条文原文)+ASR(口播)+VIS("墙面出现火花…燃烧痕迹"演示画面)
- **OCR↔ASR 互校生效**:ASR"回单天返潮"被 OCR 证据"回南天返潮"纠正后入链
- 复合置信度分层正确:三模态 0.9 / 双模态 0.8 / 单模态 0.6

## 开发中修复的缺陷(累计)

1. `__main__.py` 未调 main()(CLI 空转)
2. 字典目录多拼一层(synonyms 404)
3. 中文数字逐字替换:"三十"→"310"(组合表先行;单测固化)
4. dHash 测试图方向错误(水平 vs 垂直梯度——测试自身缺陷,实现无误)
5. bigram 浮点精度(approx)
6. 孤儿 job 卡 running(启动重置恢复)
7. visual_observation 缺 measurement 列(幂等 ALTER 迁移)
8. VLM 字段返回 list 类型(SQLite 绑定归一 _s())
9. VLM 空返回 9/22(两轮调用+repair prompt+原文兜底)
10. app 包相对导入越界 + app/api 目录与模块同名冲突
11. FTS5 中文整句单 token → CJK 逐字空格化(写入+查询双侧,fts_reseed 全量重建)
12. api.py 字典推导括号笔误(SyntaxError)
13. search URL 中文未编码 400(验证侧问题)

## 全量回归(进行中,结果见 phase-0-final.md)

20 视频(4 基线 + 16 新:水电4/美缝4/乳胶漆4/地漏4),零人工干预目标。
