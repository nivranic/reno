# reno 前端测试报告(frontend-testing)

> 2026-09-20。全部结果来自真实执行;未验证项如实列出(§4)。
> 本机:Windows 11 26200 / Node 22.22.1 / npm 10.9.4(registry npmmirror)/ Python venv。

## 1. 已实际执行的命令与结果

| 命令(在 `frontend/` 下) | 结果 |
|---|---|
| `npm install` | exit 0(npmmirror) |
| `npm run typecheck` | 0 错误(strict 全开) |
| `npm run lint` | 0 错误 0 警告(eslint9 + typescript-eslint8 + react-hooks) |
| `npm run build` | ✓ 2298 模块,主包 386KB(gzip 123KB),构建 ~5s |
| `npm run test`(Vitest) | **33/33 通过**,8 个文件:时间工具/活动证据区间(重叠与点事件)/API 错误映射与模态归一/fixture 确定性与 10k 规模/状态徽章/抽帧竞态(乱序 C,A,B→只显 C、stale 标注、失败重试)/搜索(高亮、空态、IME 组合期不提交)/复核(成功载荷断言、失败保留输入)/导入弹窗(逐行分类、部分失败展示) |
| `npx playwright install chromium(+headless-shell)` | ✓ 经 `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright` |
| `npm run test:e2e`(Playwright,真实后端) | **7/7 通过**:收件箱真实数据渲染、SPA 深链直开+刷新、未知 API 返回 JSON 404、原子点击(URL `?t=` + 证据帧可见 + 播放器定位)、模态筛选总数下降、`?t=ms` 冷加载定位(±500ms)、搜索→详情→返回恢复查询与结果 |
| 后端回归 | `python -m pytest tests/ -q` 10/10 通过(旧套件无回归);SPA 承载语义 curl 验证(深链 200 html / api 404 JSON / 缺失静态 404 / favicon 200) |

### 真实浏览器交互验证(Playwright MCP,127.0.0.1:8765 + 真实数据库)

- **四联动**:点击原子 → 视频 seek 到 21.000s 并播放、证据帧 `/api/frame/…/21000` 解码成功(naturalWidth>0)、URL 更新 `?t=21000`、原子卡选中环。
- **抽帧竞态**:快速连点 3 个原子(120ms/240ms 间隔)→ 最终帧=最后点击原子的 291017ms,图片真实解码。
- **虚拟化**:182 条证据(三模态)容器 259px 有界,DOM 仅渲染 ~25 行,主区 0 溢出(1440 宽)。
- **暗色**:localStorage 切换后 bg #0e1116 / surface #151a21 / border #262d38(与设计 token 逐一相符)。
- **移动端 390×844**:Tab(证据流 182|原子 14)可见、无横向溢出、桌面侧栏隐藏、竖屏视频高 473px(56vh 上限内)。
- **搜索**:防抖即搜、URL 持参、`<mark>` 高亮、首条深链 `/videos/BV…?t=12360`、返回恢复输入与 24 条结果。
- **决策落库**:UI 提交"两者各适用"+备注 → SQLite `user_decision` 新行(action=both)与 `conflict_case.status='decided:both'` 双确认;测试数据已回滚清理。
- **布局回归(e2e 抓出并修复)**:720p + 竖屏视频曾发生 flex 压缩导致视频盒溢出盖住筛选按钮;修复后 videoBottom 452 < 按钮顶 562,elementFromPoint 命中 BUTTON,矮视口主区滚动 223px 兜底。

## 2. 性能记录(可复现)

- 构建:入口 JS 386KB(gzip 123KB);首屏为路由级代码分割(工作台独立 chunk 52KB)。
- 虚拟化:182 条证据 DOM 节点 ~25(可视窗口+overscan);10,000 条规模由确定性 fixture 生成(`mocks/fixtures.ts` `makeEvents(v, 10000)`)且 ID 唯一性有单测保障——但 10k 的真实渲染/内存数据未在本机实测(见 §4)。
- 抽帧:同一 (vid,ms) 请求由浏览器 HTTP 缓存与后端磁盘缓存双命中;乱序竞态仅序号比对,无额外网络开销。
- 轮询:无活动任务时轮询完全停止(无请求);有任务时 3s/次轻量 `/api/videos`。

## 3. Mock 环境

- `npm run dev:mock`(vite --mode mock)→ MSW 在网络层拦截,同一套页面与 API client 运行于 fixture;
  页面顶栏显示「Mock 演示环境」徽标。场景切换:`window.__renoScenario = 'offline' | 'server-error' | 'not-found' | 'empty'`;
  规模压测:`/api/video/:id/events?scale=10000`。
- Mock 默认关闭,不参与生产构建(动态 import,构建产物按 chunk 拆分,不进主包路径)。

## 4. 未验证项与残余风险(如实)

1. ~~**像素级视觉验收**~~:**已于 2026-09-20 补验完成(GLM-5.3-Flash 之眼,终版)**。
   根因(rollout + 对照实验定案):ZCode harness 以私有格式 `{"type":"image","dataUrl":…}`
   发送图像块,`open.bigmodel.cn/api/anthropic` 网关不认该格式而静默丢弃;**标准 Anthropic
   base64 图像块在同端点同 key 下完全正常**——GLM-5.3-Flash(与 flash-visual-worker 同款
   模型、同 coding-plan 配额、max_tokens 128000)经 `glm-hybrid-router/scripts/vision-describe.mjs`
   直连,逐字读出合成验证图。像素验收由 flash-visual-worker 走该通道对 7 张真实截图
   (收件箱浅色/工作台浅+深/搜索/争议复核/采集助手/390px 移动端)逐图评审:
   **7/7 PASS-with-notes,零阻断/明显缺陷**;浅色基线、深色独立设计(非反相,肤色/字幕正常)、
   ASR靛/OCR绿/VIS琥珀三端一致、无重叠/溢出/错位均获视觉确认。
   worker 提出 9 个疑点,主模型逐一定性:**7 个为识图误差或按设计**(D1 多条"播放中"=重叠
   证据同时激活,任务书要求;D2 移动端 OCR 徽章色=组件零响应式分支,全断点同色;
   D3 "两者各适用"=源码原文;D5 浅色侧栏=主题化表面色,基线措辞偏差;D6/D7 时间轴小字
   9-11px=密集仪表取舍;D8 "证据可追溯"=源码原文;D9 证据流在首屏之下=内容驱动高度设计);
   **1 个真实轻微发现(D4)**:搜索高亮只画主张行,依据行命中词未高亮(FTS 实际匹配
   主张+依据)→ **已修复**(依据行同样高亮)。

   **盲区补审(同日,flash-visual-worker + GLM-5.3-Flash 之眼)**:此前未截图审查的 7 个
   状态(报告阅读页/导入弹窗/搜索空态/工作台 404/深色争议页/移动端原子 Tab/移动端抽屉)
   逐图评审:2 PASS + 4 PASS-with-notes + **1 FAIL**。据其发现修复:
   ①404 错误正文渲染 `[object Object]`(ErrorState 对非 Error 对象 String() 序列化)→
   改为提取 message 字段,现已显示"视频 xxx 不存在(或已被移除)";②移动端抽屉遮罩
   增强为 45% 黑 + 2px 模糊,宽度 240→270px(62%→69%);③报告页副标题反引号裸露→
   改为 `<code>` 渲染;④搜索空态补图标;⑤404 卡片垂直居中;⑥时间轴最右刻度贴边裁切→
   末刻度右对齐。识图误报复核:抽屉"无遮罩"实为 40% 黑存在但视觉不明显(已顺势增强);
   采纳 A/B 双主按钮=有意的层级设计;移动端页内分段控件(非底部 Tab)=设计如此。
   修复后回归:typecheck/lint 0 问题、Vitest 33/33、e2e 7/7、浏览器 DOM 复验通过。
   详见 worker 报告与 `glm-hybrid-router/docs/VISION-CHANNEL-2026-09-20.md`;
   截图 `reno-fe-01..16-*.png`(15/16 为修复后证据)。
2. **真机 iOS/iPadOS、Firefox、WebKit**:未测试(仅 Chromium 153 headless + MCP 桌面 Chromium)。
   Playwright 配置已就绪,可在相应设备/浏览器补跑。
3. **10,000 条证据的真实渲染性能/内存**:仅验证了 fixture 生成确定性与 182 条真实数据;
   未做 10k 真实数据集的 FPS/内存测量。
4. **20 次连续切换内存泄漏浸泡**:e2e 全程含十余次路由/视频切换,控制台无错误无警告,
   但未做显式内存曲线测量。
5. **旧模板保留双路径**:classic 模式未做全页面回归(代码未动,风险低)。
6. **iOS Safari 上 `100vh` 类断点行为**:已用 `h-dvh`/`dvh` 相对单位缓解,未真机验证。

## 5. 复验入口

```bash
cd frontend
npm run typecheck && npm run lint && npm run test && npm run build
# 后端需在 8765(reuseExistingServer)或由 Playwright 自动拉起
npx playwright test
```
