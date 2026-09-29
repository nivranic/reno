# reno 前端布局与样式规则（2026-09-29 精细化审计后）

> 来源：2026-09-27 全局布局/样式审计 + 2026-09-29 精细化审计（三视角静态质证 36 条 +
> Chrome 实测几何/溢出/暗色对比/动画采样，见 §9）。
> 新页面/组件请直接遵循本规则；与规则冲突的旧写法按「公共层 → 页面层」顺序收敛。
> 姊妹篇：色彩/字体/动效等**视觉令牌**见 `docs/frontend-design-system.md`；本文管**布局、尺寸档位、状态与弹层**。

## 1. 主题与令牌

- 唯一色源：`src/styles/global.css` 的 CSS 变量（亮/暗双套）→ Tailwind `@theme inline`。
  业务代码只允许使用令牌类（`text-ink` / `bg-surface` / `border-line` / `st-*` / 模态 `asr|ocr|vis`…）。
- 禁止新增裸颜色（`bg-amber-500/10`、`bg-[#xxxxxx]` 等）。既有豁免：采集页书签拖块的
  rose 色为有意视觉锚点；中性遮罩允许 `bg-black/40`。
- 语义纪律：`st-ok/run/wait/bad/review` 表业务状态，模态色（asr/ocr/vis）表证据来源，
  两轴不得混用（OCR 绿 ≠ 成功绿）。warn/警示类一律用 `st-wait`。
- Select 下拉箭头用 `--ctl-arrow` token（亮/暗各一套内联 SVG data-URI，类 `.select-arrow`），
  主题跟随自动切换；禁止页面再写 inline `background-image` 覆写。
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
- Badge 尺寸档：`md` 默认（紧凑内边距）；`sm`（px-1.5 / 10.5px 字号 / 15px 行高）用于
  卡内芯片与引用芯片（ask 引用、对比页 CTYPE、采集状态点）。旧写法 `scale-[0.92]`
  缩放芯片会模糊渲染且不省空间，一律改 `size="sm"`。

## 3. 布局

- 页面容器：`mx-auto w-full max-w-[<场景宽>] px-4 py-5 md:px-6`。
  场景宽：表格 1180 / 对比 1100 / 报告 1080 / 争议 980 / 问答 900 / 搜索 880 / 采集 820。
- 卡片密度两档：**全宽卡**（搜索/收件箱，正文 13.5px）与**窄列卡**（对比页侧列、
  证据芯片行，12.5px + 更紧内边距）。同密度档内的卡不得混用两种正文字号。
- 页头：`PageHeader`（标题 19px/600 + 描述 13px muted + 右侧 actions shrink-0，
  根容器 flex-wrap 允许换行；actions 包装层带 `print:hidden`，打印只留内容）。
  各页不要自造标题区。
- 正文排版梯度（`.reno-md` 报告正文）：页面标题 19 > h1 18 > h2 16 > 正文 15。
  改标题字号必须维持这条单调梯度。
- 工具栏（搜索/筛选行）：`flex flex-wrap items-center gap-2`；输入类给
  `min-w`（搜索输入 `min-w-[220px] flex-[1_1_220px]`）保证窄屏换行后仍有效，
  禁止让固定宽度筛选器把输入挤扁（实测 375px 曾被挤到 110px）。
- 列表行/卡片：桌面真表格（`hidden md:block`），手机卡片（`md:hidden`），不做压缩表格。
- 行内键值对（如价格行）：`flex flex-wrap gap-x-2 gap-y-0.5` + 标签 `shrink-0`，
  窄屏自然换行到标签下，禁止挤成单行截断。
- **无归属对象的数据行（video 已删/缺失）渲染静态元素**（div/li/span），禁止
  `Link to="#"` 或挂 href 的死链——死链点击会导航回首页，且语义上是可用链接。
  长标题加 `title` 属性保完整内容可达。
- 滚动归属：页面滚（main）为主；问答页、工作台证据流为局部滚 + `overscroll-contain`。
  局部定高容器用固定高度（如证据流 h-64 / clamp），禁止位图加载引发 CLS。

## 4. 弹层

- 对话框：`DialogContent` 头部 **sticky**（关闭钮恒可达，关闭钮 p-2 + shrink-0），
  正文包在 `pt-4` wrapper；操作条用 `sticky bottom-0 -mx-5 -mb-5 … max-md:-mx-4
  max-md:-mb-4 max-md:mt-auto`（全断点贴底，长内容滚动时仍可提交）。
- 移动端弹窗为全屏任务面板（max-md 变体），表单 flex 链 `max-md:flex max-md:min-h-0
  max-md:flex-1 max-md:flex-col` 不得破坏。
- 移动端弹窗顶部安全区：面板 `max-md:pt-[max(1rem,env(safe-area-inset-top))]`，
  sticky 头负边距同值补偿（`max-md:-mt-[max(1rem,env(safe-area-inset-top))]`），
  刘海屏下关闭钮不被状态条吞掉。
- pop-in 弹窗动画只允许 scale/opacity；**禁止 translate**。Tailwind v4 里
  `-translate-x-1/2` 生成独立 `translate` CSS 属性，与 keyframes 的 `transform`
  叠加（不是覆盖），会让居中弹窗双重偏移跳位（2026-09-29 实测修复）。
- z 序：toast 70 > dialog/drawer 50。toast 在手机上用
  `max-md:bottom-[calc(var(--reno-bottom-occupy,0px)+12px)]` 避让底部导航。

## 5. 状态

- disabled：组件基类统一 `disabled:pointer-events-none disabled:opacity-50`；
  页面不得再覆写 disabled 配色（已删除 import-dialog 的灰描边覆写）。
- 焦点：全局 `*:focus-visible` 2px outline；输入类 `focus:border-acc + focus:ring-2
  focus:ring-acc/25`（ring 不产生布局位移）。
- 加载态尺寸稳定：按钮内 spinner 用等尺寸图标替换文本不发生跳宽；
  骨架屏（ListSkeleton）结构与内容对齐。
- 空态/错误态/后台刷新警告：一律用 `components/shared/states.tsx`，不得各页自造。
  `EmptyState` 有 `compact` 档（px-4 py-8 / 13px），用于侧列、面板内的局部空态；
  页面级首屏空态用默认档并可带 action。
- 骨架屏防 CLS：数据就位前区域高度已知时用与内容同尺寸的 `Skeleton`
  （如采集页健康卡 57px），不得渲染 `null` 留塌陷。
- 骨架行数与真实内容首屏行数对齐（如证据流 4 行 ≈ clamp 下限 240px），
  避免加载完成瞬间明显跳动。

## 6. 交互细节

- 触控目标：主操作按钮移动端 41.25px；证据流行头整行为 44px 级定位按钮
  （`-my-2 py-2` 补偿）；纯图标/小按钮至少 30px 且桌面为主场景。
- IME：搜索/问答输入已按 composition 事件处理，新输入框涉及回车提交时必须带
  `!e.nativeEvent.isComposing` 守卫。
- 图标：行内 lucide 14–16px；按钮内图标与文字 `gap-1.5`；纯图标按钮用 size 档，
  不手工定宽高。
- 警示图标：横幅/条目内的警示一律 `<ShieldAlert size={13} className="mt-0.5 shrink-0" />`
  配 `items-start` 行（对比页冲突横幅范本），不再用 ⚠ 文字字符（基线不稳、字号联动）。
- 「有分歧」横幅统一 `st-review` 语义类（三类横幅同屏时不混 st-wait/st-review）；
  A/B 侧别标识用 acc/neutral（侧别是对照关系，不是证据模态，禁挪用 asr/ocr/vis 色）。

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

## 9. 2026-09-29 检查与修复记录

三视角静态审计 36 条（全部经反方质证成立）+ Chrome 实测（1440/768/375 三档、
亮暗双主题、几何/溢出/对比度/动画采样）。处置分「修复」「仅入册」「不适用」三类。

| 问题 | 位置 | 处置 | 验证 |
|---|---|---|---|
| 弹窗 pop-in `transform` 与 `-translate-x-1/2` 的 `translate` 属性叠加 → 居中偏移跳位 | global.css keyframes | keyframes 去 translate 纯 scale | 逐帧采样 11 点中心恒 (720,450) maxDrift=0 ✓ |
| 报告页 768 死区：目录不可达 + `isDesktop` matchMedia 快照不随 resize 重算 | reports-page | 删 isDesktop，`?r=` 单一事实源 + CSS 断点 `hidden lg:block` | 768 实测目录可见/切换/返回全通 ✓ |
| 引用芯片 `scale-[0.92]` 模糊 + 无 video 时死链 `Link to="#"` | ask-page | Badge `size="sm"`；无 video 渲染静态 span | 测试 43/43 + 375 目检 ✓ |
| 搜索/对比结果行无 video 死链 | search-page, compare-page | 无 video 分支渲染静态 div/li | 复测无 a[href="#"] ✓ |
| 对比页提交钮手写 h-9 按钮不入档位体系 | compare-page | `Button size="md"` | 实测 33.75/41.25 档位 ✓ |
| 冲突横幅 ⚠ 字符基线不稳 | compare-page | ShieldAlert 13px `mt-0.5 shrink-0` | 暗色+亮色目检 ✓ |
| ask 分歧横幅 st-wait 与语义 review 不符 | ask-page | 统一 st-review | 类核对 ✓ |
| A/B 侧别挪用模态色系 | conflicts-page | acc / neutral + 注释 | 暗色实测对比度 ✓ |
| CTYPE 手写小徽章 | conflicts-page | `Badge tone="neutral"` | 375 复测 ✓ |
| 采集状态点手写色点 | collect-page | `Badge tone={ok?"ok":"wait"}` | 复测 ✓ |
| 健康卡 isPending 渲染 null → CLS | collect-page | 同尺寸 Skeleton 57px | 类核对 ✓ |
| Select 箭头 inline backgroundImage 不随主题 | ui/field | `--ctl-arrow` token + `.select-arrow` | 构建产物 grep 双主题 ✓ |
| 暗色 `--st-bad-bg` 7 位 hex 无效（存量笔误） | global.css | `#450a0aaa` | 构建产物 grep ✓ |
| `.reno-md` h1/h2 与页标题 19px 梯度倒挂 | global.css | h1 18 / h2 16 | 构建产物 grep + 实测 ✓ |
| disabled 60% 与全局 50% 不一致 | ui/field | 统一 50 + pointer-events-none | 类核对 ✓ |
| 收件箱工具栏 13px 覆写破坏档位 | inbox-page | 删覆写 | 375 实测 33.75 一档 ✓ |
| 价格行单行挤压 | search-page | flex-wrap + shrink-0 标签 | 类核对 + 375 无溢出 ✓ |
| 工具栏 print 版式杂入操作钮 | states.tsx | actions `print:hidden` | 构建产物 @media print ✓ |
| 弹窗移动端顶部无安全区 | ui/dialog | `max(1rem,env(safe-area-inset-top))` 双补偿 | 类级验证（真机未测）△ |
| 空侧列/空证据流裸 `<p>` 自造空态 | compare/search 页、workbench 面板 | `EmptyState compact` | 375/1440 复测 ✓ |
| 证据流骨架 7 行 vs clamp 下限 240px | workbench-page | rows=4 | 类核对 ✓ |
| 令牌门手写 input/button 不入组件体系 | root-layout | Input/Button + IME 守卫 | 代码级验证（运行时未测）△ |

仅入册（不拉平的有意差异）：对比/搜索卡密度两档并存（全宽 13.5 / 窄列 12.5）；
报告页 xl 档 TOC DOM 顺序在正文之后（键盘 Tab 顺序取舍，视觉布局由 grid 控制）；
报告目录项与 TOC 输入框跨语义区 5.25px 高度差（目录是内容列表非工具行）。

不适用：error-boundary 手写按钮（崩溃兜底零依赖场景，不应引组件依赖）；
报告 TOC DOM 顺序同上；三处已确认有意设计的模态过滤钮差异。
