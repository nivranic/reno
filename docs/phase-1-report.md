# Phase 1 报告:复核界面(产品核心上线)

日期:2026-09-19(与 Phase 0 同日完成;单人 + agent 模式)

## 交付物

| 项 | 状态 | 验证方式 |
|---|---|---|
| FastAPI + HTMX 六页面(收件箱/详情/争议/搜索/报告) | ✅ | HTTP 全端点结构化验证(下表) |
| 四联动详情页(播放器↔时间轴↔证据流↔原子) | ✅ | events API 93 事件/3 原子;app.js seek+联动逻辑 |
| **证据回跳实时抽帧**(±1s 问题的最终解法) | ✅ | `/api/frame/BV…/15000` → 200, image/jpeg 73872B |
| 视频流 Range 支持(seek 必需) | ✅ | Range 请求 → 206 |
| 冲突复核卡(采纳A/B/各适用/都不采+备注) | ✅ | POST /api/decision → user_decision 表落库,status=decided:* |
| 中文 FTS5 检索(CJK 逐字空格化) | ✅ | "插座" → 3 命中(API+页面一致) |
| URL 导入(收件箱一键) | ✅ | POST /api/import → 入队+后台处理线程 |
| iOS 快捷指令采集方案 | ✅ 文档 | docs/ios-shortcut.md(零 App 开发) |

## 端点验证记录(2026-09-19 22:4x)

```
GET  /                       → 200(视频列表渲染)
GET  /videos/{BV}            → 200(详情页)
GET  /api/video/{BV}/events  → 200 {events:93, atoms:3}
GET  /api/frame/{BV}/15000   → 200 image/jpeg 73872B(实时抽帧)
GET  /api/video/{BV}/file    → Range 请求 → 206
POST /api/decision           → {"ok":true}(决策不可被AI覆盖)
GET  /conflicts              → 200
GET  /search?q=插座           → 200,3 结果(中文FTS修复后)
```

## 视觉验收说明

本会话环境图像注入通道故障(可行性报告 §5.3 已记录),无法由 AI 目验界面像素;
已交付全部端点的结构化验证 + 界面截图待用户浏览器最终验收(`reno serve` → http://127.0.0.1:8765)。

## Phase 1 期间修复的缺陷

14. app 包相对导入越界(`from ..reno`)→ 绝对导入
15. `app/api/` `app/pages/` 空目录与同名 .py 模块冲突 → 删除目录
16. api.py 字典推导括号笔误(SyntaxError)
17. FTS5 unicode61 中文整句单 token → 0 命中 → CJK 逐字空格化 + fts_reseed
18. (验证侧)URL 中文未编码 → 400

## 设计决策记录

- **实时抽帧而非最近帧缓存**:可行性报告发现"2–5s 采样 vs ±1s 验收"不自洽;详情页点击原子 → `/api/frame/{vid}/{evidence.start_ms}` → ffmpeg 精确抽帧(帧级偏差=0),缓存于 frames_cache/。这是对方案文档 §P0 验收表的**实现级修正**。
- **决策语义**:conflict 决策写 conflict_case.status=decided:*;atom 级 confirm/reject 更新原子 status(verified/rejected);全部仅插入 user_decision,任何模型重跑不触碰该表。
