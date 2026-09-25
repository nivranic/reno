# reno 健壮性验证报告

- 日期：2026-09-26
- 范围：API 后端（app/api.py + reno/*）全功能面；前端为同源消费方（XSS 由 React/MarkdownView 转义设计覆盖）
- 方法：隔离测试套件 `tests/test_robustness.py`（临时库 + 临时媒体目录，**不触碰真实 data/reno.db**）+ 真实服务冒烟复核
- 结果：**38/38 pytest 通过**（原 10 + 健壮性 28），发现 **9 项真实缺陷，全部修复**，另确认 3 项设计本就安全

## 1. 验证矩阵与结果

| 维度 | 覆盖内容 | 结果 |
|---|---|---|
| 边界 | 非 dict/畸形 JSON/坏编码 body、非法 URL scheme、批量 urls 类型、超 200 条上限、Range 各畸形、ms 超界/负数、FTS 特殊字符、20k 字符查询、emoji、空字节 | 9 项缺陷修复后全过 |
| 高并发 | 16 线程同冲突并行决策、24 线程混合读写风暴、8 线程同 URL 并发导入、8 线程同目标并发抽帧、20 线程模型配置写竞争 | 写竞争/TOCTOU/撕裂文件 3 项缺陷修复后全过 |
| 渗透 | SQL 注入探测（参数化验证）、FTS 语法注入、路径遍历（原样与 %2F/%2e%2e 编码）、存储型 XSS（JSON 层无 HTML 包裹 + 前端转义）、方法滥用、超大载荷 | 注入类确认安全；无新增缺陷 |
| 欺骗 | 伪造决策 action（含 SQL 风格/原型污染串）、伪造不存在的 conflict_id、伪造 ask history 角色（system/developer 注入）、伪造 config model 值 | 枚举外 action、未知 conflict、非法 model 均被 400/404 拒绝；system/developer 角色被过滤不进 prompt |
| 乱序/幂等 | judge 重跑后对旧冲突 ID 决策、同内容先并发后顺序重复导入、同目标帧请求乱序、配置并发切换 | 旧 ID 404（无静默假成功）、重复导入报 duplicate 且流水线只跑一次、终态一致 |

## 2. 发现的缺陷与修复（本轮全部落地）

| # | 严重度 | 缺陷 | 修复 |
|---|---|---|---|
| 1 | 高 | 非 dict body、畸形 JSON、坏 UTF-8 编码一律 500（此前真实使用中已复现） | 新增 `json_body()`：统一 400 + 明确 detail；套用于 import/import-batch/decision/ask/config-model |
| 2 | 高 | `/api/video/{id}/file` Range 解析：`bytes=abc`/`bytes=-100` 直接 500 | 正则解析 + 416 语义；同时修复：忽略显式 end 的 RFC 不合规、后缀区间误当结束字节（修复过程中自测发现） |
| 3 | 高 | `/api/import` 未校验 URL scheme——`javascript:`/`data:`/`file:`/盘符路径都会进入 yt-dlp | http(s) 白名单前置校验 |
| 4 | 高 | `/api/decision` 无 action 枚举校验（任意字符串入库，欺骗面）、不存在的 conflict_id 返回假 `ok:true`、note 无长度上限 | 枚举校验（conflict 四值 + atom confirm/reject）、存在性检查 404、note/revised_claim ≤2000 且必须为字符串 |
| 5 | 中 | 同 URL 并发导入：sha 去重 check-then-insert 是 TOCTOU；同 vid 时 upsert 走 UPDATE 双双报 imported，流水线重复触发 | `_register` 进程级锁串行化去重+插入；`IntegrityError`（sha UNIQUE）兜底转 duplicate |
| 6 | 中 | 并发同目标抽帧：多 ffmpeg 直接写同一缓存文件 → 撕裂图片 | 模块级锁串行化同目标抽取 + 临时文件原子 rename |
| 7 | 中 | `config.set_local` 并发写 `config.local.json` 非原子，可能损坏含 API key 的配置文件 | 写入锁 + tmp 文件原子 rename |
| 8 | 低 | 帧抽取 ms 钳制到视频末尾整点会 seek 越界 → 500 | 钳制到 duration-200ms |
| 9 | 低 | `from ..reno import pipeline` 相对导入在部分包上下文（如 pytest）下 500 | 改绝对导入 `from reno import pipeline` |

## 3. 确认安全、无需修改的项

- **SQL 注入**：全部查询参数化；search 的 category/space 注入探测无效（注入串仅作过滤值）。
- **FTS 语法注入**：`fts_prep` CJK 分词 + 引号包裹 + 引号转义，畸形 MATCH 表达式被 try 兜底为空结果，无 500。
- **路径遍历**：frame/video 端点以 DB 资产存在为前置门槛（遍历串不命中资产 → 404）；编码变体 %2F/%2e%2e 被 Starlette 拒绝；SPA 兜底只返回 HTML 壳，未发生文件读取。
- **存储型 XSS**：payload 仅作为 JSON 字符串数据传输（API 层无 HTML 包裹）；前端 react-markdown 不渲染原始 HTML、页面文本经 React 转义。
- **ask 历史注入**：history 仅接受 user/assistant 角色，system/developer 伪造角色不进入模型上下文。
- **config/model**：白名单校验在先，任意值无法写入配置。

## 4. 未覆盖边界（如实列明）

- **多进程部署**：并发修复中的进程级锁以单进程 uvicorn 为前提（本地单用户部署形态）；多 worker 部署需改用文件锁/数据库事务约束。
- **真实下载链路的并发**：导入并发用 ingest_file 桩替代了 yt-dlp 网络下载；真实双击导入同一视频的文件写入竞态由锁消除，但跨进程（CLI + Web 同时导入同一 URL）未验证。
- **前端交互层**：乱序/竞态在服务端验证；前端层的乱序防护（帧请求 seq 守卫、搜索竞态取消）此前已在桌面走查中验证，本轮未重复自动化。
- **LLM 链路**：ask/atomize/judge 的模型调用以打桩验证参数与降级路径，未对真实模型做注入鲁棒性评估。
- **性能压测**：本轮为正确性并发（≤24 线程），非吞吐量压测；数据规模沿用当前库（28 视频/354 原子），万级原子下的分页/虚拟列表已在移动端方案中另行规划。

## 5. 复现

```bash
.venv/Scripts/python -m pytest tests/test_robustness.py -q   # 健壮性 28 例
.venv/Scripts/python -m pytest -q                            # 全量 38 例
```
