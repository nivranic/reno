# reno 移动端 H5 适配说明

> 依据 `reno-mobile-h5-adaptation-plan.md`（下称"方案"）实施的增量改造记录。同一套 React 应用、同一套后端接口，按屏幕重排布局；不另建 /m 站点、不复制 mobile-api、不做 UA 重定向。

## 断点与骨架

- 断点沿用 Tailwind 默认并与全站统一：`<768px`（md 以下）= 手机单列 + 底部主导航；`768–1279px` = 平板紧凑布局；`≥1280px`（xl）= 完整工作台。禁止各页面自定义断点。
- `index.html` 已有 `viewport-fit=cover`，未添加 `user-scalable=no`（保留浏览器缩放）。
- **底部主导航**（`root-layout.tsx` 的 `BottomNav`）：仅手机显示，5 个一级入口——收件箱 / 知识问答 / 搜索 / 争议复核 / 报告。采集助手按方案 §4.1 不占导航位，保留在抽屉导航与导入流程中（抽屉在所有页面可达，无功能死角）。
- **底部导航显隐规则**（§4.2：一屏只有一个主固定区域）：
  - 显示：`/`、`/ask` 之外的一级页面；
  - 隐藏：视频详情 `/videos/:id`（工作台）、问答页（对话输入占底部）、报告阅读态（`/reports?r=<name>`）。
- **底部占高实测**：BottomNav 用 ResizeObserver 把自身实际高度写入 `--reno-bottom-occupy`（挂在 documentElement），`<main>` 的 padding-bottom 引用该变量——文字放大导致导航增高时内容预留自动跟随，不假设固定 56px（§15.1）。
- **安全区**：顶栏 `pt-[env(safe-area-inset-top)]`，底部导航 `pb-[env(safe-area-inset-bottom)]`，不写死 34px。
- **触控目标**（§5）：`ui/button.tsx` 手机优先尺寸——`md`(默认) 44px、`sm` 36px、`icon` 44×44，`md:` 断点恢复 PC 紧凑尺寸（32/36px），桌面密度不变。底部导航条目 ≥56px。

## 各页面适配要点

| 页面 | 改造 |
|---|---|
| 收件箱 | 已有手机卡片列表（video-table）；按钮触控尺寸随全局提升；统计/筛选单列自排 |
| 导入面板 | `ui/dialog` 在 `<md` 变全屏任务面板（inset-0 + dvh，无圆角浮窗）；链接输入 `inputMode=url`；新增"从剪贴板粘贴"按钮——仅渲染于支持 Clipboard API 的环境，读取发生在用户点击（§7.3），拒绝时回退提示长按系统粘贴；手机不 autoFocus（不自动弹键盘） |
| 视频详情 | `<lg` 已有 知识/证据 双标签；本次把默认标签改为**知识阅读态**，`?t=ms` 深链进入时自动切到**证据态**（§9.1）；播放器保持 `playsInline` + object-contain + 高度上限；抽帧竞态由 use-frame 的单调 seq 守卫保证（先建）；详情页隐藏底部导航 |
| 争议复核 | §11.2 两步提交：点选决定（高亮，不写入）→ 备注 → 明确"提交决策"按钮；提交失败保留选择与备注；四类决策枚举不变；A/B 上下堆叠不变 |
| 搜索 | `autoFocus` 仅桌面（手机进入不弹键盘，§4.2）；IME 合成保护、350ms 防抖、URL 持久化、React Query 竞态防护此前已建 |
| 报告 | 打开的报告写入 `?r=<name>`（可分享、参与浏览器历史，§15.3）；手机默认显示目录，进入阅读后隐藏目录列与底部导航，提供"← 返回报告目录"；桌面默认展示第一份报告，行为不变 |
| 知识问答 | 高度改用 `100svh` 基线（含 fallback），软键盘/地址栏变化下输入区保持可达；作为对话页隐藏底部导航 |

## 验证

见 `docs/mobile-h5-test-report.md`。截图基线（改造前/后、390px 与 1440px）保存在仓库根目录 `baseline-*.png` / `after-*.png`。

## 明确未做 / 未验证

- **真机项未验证**（本环境无 iPhone/Android 实机，按方案 §18.1 如实标记）：`env(safe-area-inset-*)` 真机刘海/横条表现、iOS Safari 软键盘与 VisualViewport 行为、真实媒体播放与 Range 行为、系统分享、弱网/切后台生命周期。
- 未引入 PWA/Service Worker/Web Share 接收（方案 §17 定位为可选增强）。
- 未做 Core Web Vitals 真机测量（§16.4 的口径与工具需要真实移动网络与设备）。
- 后端接口零改动；本次为纯前端适配 + 文档。
