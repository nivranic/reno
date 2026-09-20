# reno 设计系统(frontend-design-system)

> 实现于 `frontend/src/styles/global.css`(Tailwind 4 CSS-first tokens)与
> `frontend/src/components/`。产品名:**reno · 装修知识工作台**。

## 1. 设计原则

- 温和浅色工作区(微暖中性底)+ 深色文字 + 靛蓝交互主色;专业工具感,不是营销页。
- 默认浅色,支持深色与跟随系统(三态切换,localStorage 持久,`<html class="dark">`,
  index.html 内联脚本预置避免 FOUC)。深色是独立设计的表面体系,不是反相。
- 模态语义色(ASR/OCR/VIS)与业务状态色(成功/失败/处理中/待复核)分离,
  OCR 的绿永远不会被读成"处理成功"。
- 颜色从不单独承载含义:模态/状态同时有文字标签(含 `<mark>` 高亮、图标)。

## 2. 色彩 tokens(CSS 变量,light / dark 双套)

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--bg` | #f7f7f5 | #0e1116 | 页面底 |
| `--surface` | #ffffff | #151a21 | 卡片/面板 |
| `--surface-2` | #f1f2ef | #1b222c | 次级表面/表头 |
| `--ink / --ink-2 / --muted` | #1b2130 / #3f4757 / #6f7889 | #e7eaf0 / #c0c7d2 / #8b93a3 | 文字三级 |
| `--line / --line-2` | #e5e7e3 / #d4d8d2 | #262d38 / #333c4a | 边框两级 |
| `--acc` | #4f46e5 | #818cf8 | 交互主色(靛蓝) |
| `--asr` | #4338ca / bg #eef2ff / line #c7d2fe | #a5b4fc / #312e8155 / #4338ca | 语音模态(靛) |
| `--ocr` | #15803d / #f0fdf4 / #bbf7d0 | #86efac / #14532d55 / #15803d | 画面文字模态(绿) |
| `--vis` | #b45309 / #fffbeb / #fde68a | #fcd34d / #78350f55 / #b45309 | 视觉理解模态(琥珀) |
| 状态 | st-ok/run/wait/bad/review 各三档 | 同名深色套 | 见 status-badge.tsx 映射 |

Tailwind 工具类映射:`bg-bg / bg-surface / text-ink / border-line / bg-acc / text-asr / bg-vis-bg …`
另有 `--radius-panel: 12px`(`rounded-panel`)与 `--radius-ctl: 8px`(`rounded-ctl`)。

## 3. 字体与排版

- 系统字体栈(离线,无远程字体):`system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei"`
- 等宽(时间戳):`ui-monospace, Cascadia Mono, Consolas` + `tabular-nums`
- html 基准 15px;正文 13–15px;页面标题 19px;报告正文 15px/1.75 行高,阅读宽 `74ch`

## 4. 动效

- 120–200ms 短过渡(颜色/透明度);Dialog 出入 150/160ms keyframes。
- `prefers-reduced-motion: reduce` 全局降为 0.01ms。

## 5. 布局体系

- 应用壳:桌面 224px 侧导航 + 顶栏;移动抽屉导航(hamburger)。无假搜索/头像/通知。
- 阅读页(收件箱/搜索/复核/报告/采集)统一 `max-width` 限宽;
  工作台不限宽,左右分区(播放器/时间轴/证据流 | 可拖拽原子面板,宽度持久化 300–620px)。
- 断点:xl(1280)播放器与证据帧并排、以下堆叠;lg(1024)起双栏 + 原子侧栏,
  以下 Tab 切换(证据流|原子);竖屏视频 `max-h-[min(48vh,420px)]` + object-contain。
- 证据流高度 `clamp(240px, 34vh, 520px)`;矮视口下主内容区自然滚动兜底。

## 6. 共享组件清单

- `ui/`:Button(cva 变体)、Badge(7 tone)、Input/Textarea/Select/Field、
  Dialog(Radix,焦点陷阱/ESC/焦点归还)、Tabs、Spinner/Skeleton/ListSkeleton
- `shared/`:StatusBadge 系列(视频/原子/立场/冲突状态,未知值可见可诊断)、
  ModalityTag(+图例,未知模态显示 `?`)、PageHeader、EmptyState、ErrorState、
  RefreshWarning、MarkdownView(安全渲染+标题锚点+TOC)、Toast(单入口+按 key 去重)

## 7. 可访问性要点

- 时间轴:`role="slider"` + aria-valuenow(毫秒)+ 方向键/Home/End 定位。
- Dialog:Radix 语义(title/description),关闭归还焦点。
- 模态筛选按钮 `aria-pressed`;流容器 aria-label 带总数;选中原子有 aria-live 播报。
- 触摸目标 ≥ 28px 的紧凑场景均保留文字标签;主要操作 36px+。
