# reno 移动端 H5 适配 · 测试报告

- 日期：2026-09-25
- 被测：`frontend/`（React 19 + TS + Vite + Tailwind4），后端 `reno serve`（本地 8765）
- 依据：`reno-mobile-h5-adaptation-plan.md` §18（测试矩阵）与 §19.2（交付物）

## 1. 执行环境

| 项 | 值 |
|---|---|
| 自动化浏览器 | Playwright MCP（Chromium），视口 390×844（手机）与 1440×900（PC） |
| 单元/组件测试 | Vitest 40 用例（jsdom + MSW），`cd frontend && npx vitest run` |
| 后端测试 | pytest 10 用例，`.venv/Scripts/python -m pytest -q` |
| 类型检查 | `tsc --noEmit` 通过 |
| 构建产物 | `npm run build` 成功（es2022） |
| 数据规模 | 28 视频 / 354 原子 / 19 聚类 / 13 冲突（真实库，非 fixture） |

## 2. 结论分级（方案 §19.2 要求的口径）

### 已实现 + 桌面浏览器模拟验证（Playwright 390×844 截图复核）

- 底部主导航：一级页面显示（5 入口），视频详情/问答/报告阅读态自动隐藏（截图 `after-mobile-inbox.png`、`after-mobile-workbench.png`、`after-mobile-report-reading.png`）。
- 工作台手机态：默认进入知识阅读标签；`?t=ms` 深链进入证据标签；竖屏视频 object-contain 不溢出；抽帧竞态守卫（代码审查确认）。
- 导入面板：手机全屏化；粘贴按钮仅在支持 Clipboard API 时渲染、点击才读取（截图 `after-mobile-import.png`）。
- 争议两步提交：选择高亮不写入，提交按钮未选时禁用（截图 `after-mobile-conflicts.png`）。
- 报告目录/阅读分层：手机默认目录，`?r=` 进阅读并隐藏导航（截图 `after-mobile-reports-catalog.png` / `after-mobile-report-reading.png`）。
- PC 回归：1440×900 截图对比，侧栏/表格/按钮密度与改造前一致（`after-desktop-inbox.png` vs `baseline-mobile-inbox.png` 同页面对照）。

### 已实现，仅 Mock/单测验证

- 争议两步提交的写入语义与失败保留（Vitest + MSW，含 500 注入）。
- 底部导航路由显隐规则（`root-layout.test.tsx` 4 用例）。
- 搜索 IME/防抖/竞态（既有用例回归通过）。

### 尚未验证 / 受阻（如实列明，方案 §18.1 允许）

- **真机未验证**：本环境无 iPhone/Android 实机。以下项在真机上仍需人工确认：
  - `env(safe-area-inset-*)` 刘海屏/横条实际表现；
  - iOS Safari 软键盘弹出、VisualViewport、`100svh` 基线下的输入可达性；
  - 真实媒体播放（Range/206、内联播放策略）、锁屏/切后台后的任务状态恢复；
  - 弱网、断连恢复、系统分享（未实现，不存在误报入口）。
- **Core Web Vitals（LCP/INP/CLS）**：需要真实移动设备与网络，未测量；不做达标声明。
- **生命周期 20 次切换**：模拟器可跑但无内存曲线观测手段，标记未验证。

### 明确未实现（非缺口）

- Web Share 接收目标、PWA/Service Worker、断点续传上传——方案定位为可选增强或需后端协议支撑，未实现也未提供任何伪装入口。

## 3. 缺陷与修复记录

| # | 现象 | 修复 | 状态 |
|---|---|---|---|
| 1 | 手机无底部导航，仅汉堡抽屉（单手不可达） | BottomNav + 路由显隐规则 | 已修复（模拟验证） |
| 2 | 争议页轻点即写入，违反 §11.2 | 两步提交 + 用例同步 | 已修复（Mock 验证） |
| 3 | 搜索/导入 autoFocus 在手机自动弹键盘 | 仅桌面 autoFocus + 手势粘贴按钮 | 已实现（真机待验） |
| 4 | 报告页手机直接落入阅读视图 | 目录/阅读分层 + `?r=` 深链 | 已修复（模拟验证） |
| 5 | 问答页 100vh 在移动浏览器含地址栏高度 | 改 `100svh` 基线（含 fallback） | 已实现（真机待验） |

## 4. 复现命令

```bash
.venv/Scripts/python -m reno serve            # 后端 + 生产 SPA (8765)
cd frontend && npm run dev                    # 或 dev 模式 (5173, 代理 /api)
cd frontend && npm run typecheck && npx vitest run
.venv/Scripts/python -m pytest -q
```

Playwright 复核：视口 390×844 依次访问 `/`、`/videos/<id>`、`/ask`、`/conflicts`、`/reports`、`/reports?r=summary`。
