# reno 前端布局与样式规则（2026-09-27 全局优化后）

> 来源：2026-09-27 全局布局/样式审计（静态扫源码 + Playwright 实测几何 + 截图视觉判定）。
> 新页面/组件请直接遵循本规则；与规则冲突的旧写法按「公共层 → 页面层」顺序收敛。

## 1. 主题与令牌

- 唯一色源：`src/styles/global.css` 的 CSS 变量（亮/暗双套）→ Tailwind `@theme inline`。
  业务代码只允许使用令牌类（`text-ink` / `bg-surface` / `border-line` / `st-*` / 模态 `asr|ocr|vis`…）。
- 禁止新增裸颜色（`bg-amber-500/10`、`bg-[#xxxxxx]` 等）。既有豁免：采集页书签拖块的
  rose 色为有意视觉锚点；中性遮罩允许 `bg-black/40`。
- 语义纪律：`st-ok/run/wait/bad/review` 表业务状态，模态色（asr/ocr/vis）表证据来源，
  两轴不得混用（OCR 绿 ≠ 成功绿）。warn/警示类一律用 `st-wait`。
- 暗色是独立设计（`.dark` 变量整套重设），新组件写完必须在暗色下过一遍。

## 2. 控件高度档位

根字号 15px，故 `h-8=30px`、`h-9=33.75px`、`h-10=37.5px`、`h-11=41.25px`、`h-12=45px`。

| 档位 | 移动端 (<md) | 桌面端 (md+) | 用途 |
|---|---|---|---|
| Button `sm` | h-9 (33.75) | h-8 (30) | 页头次级操作、弹窗操作条 |
| Button `md` | h-11 (41.25) | h-9 (33.75) | 表单主操作、同排含输入框的动作按钮 |
| Button `lg` | h-12 (45) | h-10 (37.5) | 页面级主 CTA（当前未用） |
| Button `icon` | 41.25 方 | 33.75 方 | 独立图标按钮（与 md 同排对齐） |
| Button `iconSm` | 33.75 方 | **30 方** | 与 `sm` 文本按钮同排的图标按钮（勿再降 h-7） |
| Input/Select 默认 | h-9 (33.75) | h-9 | 工具栏、表单单行控件 |
| 与按钮同排的 Input | **max-md:h-11** | h-9 | 跟随按钮档，避免移动端 41.25/33.75 错位 |
| 紧凑档 h-8 (30) | 同上 | 同上 | 侧栏小面板（原子面板、报告 TOC），同排内必须一致 |

- **同排单行控件必须同高、顶底对齐**（实测几何 ≤1px）；多行 Textarea 豁免，
  但与按钮同排时用 `items-end` 底边对齐。
- 禁止在页面里给 Input/Select 覆写 `h-8/h-7` 来「对齐旧按钮」——要么升按钮档，要么按
  上表选组件档位。侧栏紧凑档（h-8）整排统一即可。
- 顶栏图标按钮：移动 36px（h-9 w-9）、桌面 30px（h-8 w-8）。

## 3. 布局

- 页面容器：`mx-auto w-full max-w-[<场景宽>] px-4 py-5 md:px-6`。
  场景宽：表格 1180 / 报告 1080 / 争议 980 / 问答 900 / 搜索 880 / 采集 820。
- 页头：`PageHeader`（标题 19px/600 + 描述 13px muted + 右侧 actions shrink-0，
  根容器 flex-wrap 允许换行）。各页不要自造标题区。
- 工具栏（搜索/筛选行）：`flex flex-wrap items-center gap-2`；输入类给
  `min-w`（搜索输入 `min-w-[220px] flex-[1_1_220px]`）保证窄屏换行后仍有效，
  禁止让固定宽度筛选器把输入挤扁（实测 375px 曾被挤到 110px）。
- 列表行/卡片：桌面真表格（`hidden md:block`），手机卡片（`md:hidden`），不做压缩表格。
- 滚动归属：页面滚（main）为主；问答页、工作台证据流为局部滚 + `overscroll-contain`。
  局部定高容器用固定高度（如证据流 h-64 / clamp），禁止位图加载引发 CLS。

## 4. 弹层

- 对话框：`DialogContent` 头部 **sticky**（关闭钮恒可达，关闭钮 p-2 + shrink-0），
  正文包在 `pt-4` wrapper；操作条用 `sticky bottom-0 -mx-5 -mb-5 … max-md:-mx-4
  max-md:-mb-4 max-md:mt-auto`（全断点贴底，长内容滚动时仍可提交）。
- 移动端弹窗为全屏任务面板（max-md 变体），表单 flex 链 `max-md:flex max-md:min-h-0
  max-md:flex-1 max-md:flex-col` 不得破坏。
- z 序：toast 70 > dialog/drawer 50。toast 在手机上用
  `max-md:bottom-[calc(var(--reno-bottom-occupy,0px)+12px)]` 避让底部导航。

## 5. 状态

- disabled：组件基类统一 `disabled:opacity-50 disabled:pointer-events-none`；
  页面不得再覆写 disabled 配色（已删除 import-dialog 的灰描边覆写）。
- 焦点：全局 `*:focus-visible` 2px outline；输入类 `focus:border-acc + focus:ring-2
  focus:ring-acc/25`（ring 不产生布局位移）。
- 加载态尺寸稳定：按钮内 spinner 用等尺寸图标替换文本不发生跳宽；
  骨架屏（ListSkeleton）结构与内容对齐。
- 空态/错误态/后台刷新警告：一律用 `components/shared/states.tsx`，不得各页自造。

## 6. 交互细节

- 触控目标：主操作按钮移动端 41.25px；证据流行头整行为 44px 级定位按钮
  （`-my-2 py-2` 补偿）；纯图标/小按钮至少 30px 且桌面为主场景。
- IME：搜索/问答输入已按 composition 事件处理，新输入框涉及回车提交时必须带
  `!e.nativeEvent.isComposing` 守卫。
- 图标：行内 lucide 14–16px；按钮内图标与文字 `gap-1.5`；纯图标按钮用 size 档，
  不手工定宽高。

## 7. 部署相关

- `index.html` 由后端以 `Cache-Control: no-cache` 返回（hash 资源 `/assets/*` 走长缓存）。
  本地实测曾因缺此头出现「重建后浏览器仍用旧壳 → 新旧 chunk 混用」。

## 8. 2026-09-27 检查与修复记录（摘要）

| 问题 | 位置 | 处理 | 验证 |
|---|---|---|---|
| 报告页头 sm 按钮 30px vs iconSm 26.25px 错位 | ui/button iconSm | md:h-7→md:h-8 | 实测 4 键全 30px 同顶 ✓ |
| 争议卡同排按钮 34.5/35.83 混档 | conflicts-page | 收敛到 Button 变体 + md 档 | 实测整排 33.75 同顶 ✓ |
| 导入弹窗移动端输入 33.75 vs 粘贴钮 41.25 | import-dialog | Input max-md:h-11 | 实测双双 41.25 ✓ |
| 375px 搜索框被挤到 110px | search-page | flex-wrap + min-w-[220px] | 实测输入宽 227.8 ✓ 无溢出 |
| 收件箱工具栏 h-8 与搜索页 h-9 跨页不一致 | inbox-page | 删 h-8 覆写回归默认 | 实测 33.75 ✓ |
| 分隔条图标 `mt-1/2` 非法类导致贴顶 | workbench-page | absolute 居中 | 实测 iconY==divider 中点 ✓ |
| 对话框头部随内容滚走 | ui/dialog | sticky 头 + sticky 操作条 | 矮窗实测关闭钮恒在 ✓ |
| 问答冲突横幅裸 amber 色 | ask-page | 迁回 st-wait 令牌 | 静态核对 ✓（横幅需真实问答触发） |
| collect 平台切换重复实现 | collect-page | 复用 ui/tabs | role=tab 实测 ✓ |
| 顶栏/弹窗关闭/抽屉关闭触达区小 | root-layout, dialog | p-2 / 36px 移动档 | 类核对 ✓ |
| toast 盖住底部导航 | toaster | max-md 避让变量 | 类核对（变量由 BottomNav 发布）✓ |
| 重建后浏览器缓存旧 index.html | app/main.py | Cache-Control: no-cache | curl 实测响应头 ✓ |
| 原子面板/采集模型选择 26.25px 档 | atom-panel, collect | 统一紧凑档 h-8=30 | 实测 30 ✓ |
| 输入类无键盘焦点环 | ui/field | focus:ring-2 acc/25 | 实测类生效 ✓ |

保留的合理差异（非缺陷）：报告 TOC 输入框 h-8（侧栏紧凑档、独行）；时间轴缩放小按钮
（桌面鼠标场景、独行等高）；工作台模态过滤钮移动增大/桌面紧凑的触控优先设计；
采集页书签拖块 rose 色（有意视觉锚点）；工作台标题移动端 truncate（头部高度稳定，
完整标题有 title 属性 + 收件箱卡片两行截断）。
