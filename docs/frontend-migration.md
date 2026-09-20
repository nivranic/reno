# reno 前端 React 迁移说明(frontend-migration)

> 2026-09-20 完成。原 Jinja2 前端整体升级为 React SPA,后端(FastAPI/SQLite/流水线)不变。
> 本文档记录技术方案、真实路由与接口映射、后端增量、兼容与回滚。

## 1. 技术方案(实际安装版本见 `frontend/package-lock.json`)

| 领域 | 选型 | 说明 |
|---|---|---|
| 基础 | React 19 + TypeScript 5.9 + Vite 7 | strict 全开,`verbatimModuleSyntax` |
| 路由 | React Router 7(Data Mode) | `createBrowserRouter`,6 条懒加载路由,统一 errorElement |
| 服务端状态 | TanStack Query 5 | 全部业务数据唯一缓存;自适应轮询(见 §4) |
| 样式 | Tailwind CSS 4(`@tailwindcss/vite`) | CSS-first tokens,无独立组件库依赖 |
| 组件 | 自写 shadcn 风格组件 + Radix Dialog | `frontend/src/components/ui/`,单一 UI 体系 |
| 图标 | lucide-react | 全部本地打包,无 CDN |
| 联动状态 | Zustand 5(页面级 store) | 仅跨面板选中/定位/筛选,按视频隔离实例 |
| 长列表 | @tanstack/react-virtual | 证据流虚拟化 + 动态高度测量 |
| Markdown | react-markdown + remark-gfm | 默认禁原始 HTML,远程图片不加载 |
| 测试 | Vitest 3 + Testing Library + MSW 2 + Playwright 1.63 | 单测 33 / e2e 7(真实后端) |

## 2. 页面 → 路由 → 数据来源映射

| 页面 | 路由 | 数据来源 | 说明 |
|---|---|---|---|
| 收件箱 | `/` | `GET /api/videos` | 原为 Jinja2 注入,已补 API;排序/筛选/搜索存 URL |
| 证据工作台 | `/videos/:id?t=ms` | `GET /api/videos/{id}/meta` + `GET /api/video/{id}/events` | 四联动;`?t=` 深链保留旧语义 |
| 搜索 | `/search?q=&category=&space=` | `GET /api/search` + `GET /api/facets` | 毫秒深链;返回搜索页恢复条件与滚动 |
| 争议复核 | `/conflicts` | `GET /api/conflicts` | 四类决策枚举与后端一致(accept_a/accept_b/both/reject) |
| 报告 | `/reports` | `GET /api/reports` | Markdown 渲染 + 目录 + 原文/复制/下载/打印 |
| 采集助手 | `/collect` | `GET /api/health` | 书签按当前 origin 动态生成(不再硬编码 localhost) |

## 3. 后端增量(全部为增量,未改任何既有接口语义)

- `GET /api/videos` — 收件箱列表 + 全库统计(原 index.html 上下文)
- `GET /api/videos/{id}/meta` — 详情页头(原 detail.html 上下文)
- `GET /api/conflicts` / `GET /api/facets` / `GET /api/reports` / `GET /api/health`
- `GET /api/search` 响应增补字段(polarity/status/video_title/mod/evidence_text),向后兼容
- `app/main.py`:SPA 承载 —— dist 存在时挂载 `/assets` + catch-all 返回 index.html;
  `/api/*` 缺失返回 JSON 404、带扩展名的缺失静态返回 404、dist 根文件(favicon 等)直出
- CORS 来源可配(`cors_origins`),默认保持原白名单

## 4. 关键机制

- **统一定位协议**:原子卡/证据 chip/时间轴/深链全部走 `store.locate(ms)`;
  只有显式定位驱动 `video.currentTime`,播放回报只更新观察状态(无 seek 环路)。
  应用内时间恒为整数毫秒,仅在 `<video>` 边界换算秒。
- **抽帧竞态**:`useFrame` 用请求序号守卫;连点 A→B→C 即使按 C,A,B 乱序返回也只显示 C;
  URL 确定性(vid+ms)→ 浏览器 HTTP 缓存天然生效,无需旧版 `?r=Date.now()` 防缓存。
  加载中保留上一帧并明确标注"上一帧"时间;失败仅在帧面板内重试。
- **虚拟化**:证据流仅渲染可视窗口(182 条实测 DOM 25 个),`measureElement` 动态高度,
  长文展开后重新测量;滚动容器 aria-label 带筛选总数。
- **自适应轮询**:存在 pending/running 任务或非终态视频时 3s 轮询 `/api/videos`,
  否则停止;页面隐藏时 TanStack 默认暂停。无任何 `setTimeout` 整页刷新。
- **反馈治理**:主动提交失败在操作位置内联反馈(决策卡保留备注/导入弹窗保留输入),
  页面级错误有重试,后台刷新失败仅轻提示保留旧数据;同一失败不弹多层。

## 5. 开发 / 构建 / 部署

```bash
cd frontend
npm install                # Node ≥20.19(开发机 22.22.1)
npm run dev                # 开发:Vite 5173,/api 代理到 127.0.0.1:8765(RENO_API 可改)
npm run dev:mock           # Mock 演示模式(MSW,页面顶部有"Mock 演示环境"标识)
npm run build              # 产出 frontend/dist(主包 gzip ≈123KB)
npm run typecheck / lint / test / test:e2e
```

- 生产:**无需 Node 常驻**。`python -m reno serve` 检测到 `frontend/dist` 即同源托管 SPA。
- 开发代理仅用于 `npm run dev`;生产为同源,无 CORS 问题。

## 6. 兼容与回滚

- 旧 Jinja2 页面完整保留(`app/templates/`、`/static`),未删除。
- 回滚:在 `config.local.json` 加 `"web_ui": "classic"` 并重启 `reno serve`,
  即回到原 HTMX/Jinja2 界面;删除该键或设 `"auto"`(默认)恢复 React。
- 深链格式 `/videos/{id}?t={ms}` 与旧版一致,旧链接继续可用。
- htmx CDN 引用随旧模板保留,React 版零 CDN/零远程字体(离线边界见 README)。
