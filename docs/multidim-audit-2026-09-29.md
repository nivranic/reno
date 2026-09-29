# 多维装修知识体系 · 审计与完善报告（2026-09-29）

对应需求文档：`E:\11585\#reno项目多维装修知识体系.txt`。本文记录现状审计结论、差距清单、实际改动、验证结果与边界。所有数据为 2026-09-29 实测。

## 一、现状审计结论（含证据）

### 已实现且可复用（审计确认）

| 能力 | 证据 |
|---|---|
| 视频采集→转写→OCR→原子化流水线，断点续跑 | `reno/pipeline.py`（meta 步骤标记）、serve 自动恢复 pending 任务 |
| 原子结构：工种(category)/空间(space)/极性/条件/参数/依据/风险 | `reno/schema/schema.sql` knowledge_atom 表 |
| 结论级证据追溯（时间戳→实时抽帧跳转） | evidence_ref 表 + `/api/frame/{vid}/{ms}` + 前端 workbench |
| 参数字典归一 + 数值冲突候选 | `reno/dict/param_dictionary.yaml`、`reno/dedup.py` |
| 跨视频聚类/冲突判定（含条件重叠/适用面差异/权威差异） | `reno/judge.py`、conflict_case 表、决策回填 |
| 接地问答（[n] 引用、冲突双方并列、LRU 缓存） | `reno/ask.py` |
| 报告（全库总结/增量/checklist/争议） | `reno/report.py`、docs/generated/ |
| 前端 7 页 + H5 适配 | frontend/src/features/* |

### 已实现但不完整（实测问题）

1. **stage 失控**：v1 prompt 未约束 stage，354 原子产生 **44 种自由取值**（38 条为空）。"装修阶段"轴失效。→ 已修复（派生制）。
2. **无知识维度轴**：category 只是工种/系统轴；需求 15 个知识维度（安全/材料/性能/场景/工艺/维护/验收/排查/报价/成本/比较/体验/工期…）没有任何存储字段。→ 已补 `dimension` 列。
3. **证据性质混装**：仅 16 条原子带 `conditions.authority_level`，观点/转述标准/实测/商家宣传无结构化区分。→ 已补 `evidence_nature` 列（单一事实源，旧键已迁移）。
4. **`/api/search` 断链（实测）**：整句 phrase 匹配，"卫生间防水高度"返回 **0 命中**（库内明确存在相关原子）；`/ask` 有 bigram 兜底而搜索页没有。→ 已修复（复用同一检索）。
5. **参数归一覆盖不足**：66/85 参数 `param_status=unnormalized`（字典只覆盖防水/留缝/闭水等少数）。→ 结构保留，扩展字典是后续数据工程（见边界）。
6. **checklist 报告简陋**：按失控 stage 分组，无检查方法/判定依据结构。→ 已重写。
7. **价格无结构**：28 条原子提及价格但只是 claim 文本，无口径字段（对象/规格/地区/渠道/时点/含项/口径类型），无法可比展示。→ 已补 `prices_json` 结构。

### 尚未实现 → 本次补齐

多轴交叉筛选、方案对比、验收结构化清单、价格口径记录、"暂无可核验数据"诚实状态。

### 明确不做/暂不具备数据条件（防造假能力）

- **行情预测**：无时序价格数据，不建预测模块，不输出未经验证的数字（需求 §8.3）。
- **源头成本**：无可靠证据来源，报告明确写"暂无可核验数据"，估算不与真实价混排。
- **vlm 视觉理解**：`vlm_enabled=false`（配额原因），vision 证据仅 17 条，维持关闭。

## 二、知识模型 v2（最终结构）

知识组织从单轴变多轴，**不引入新表**（反方评审结论）：

```
工程系统/工种 category (20 类, 原有)
装修阶段     stage     — 不再由 LLM 判断!由 stage_map 从 category 确定性派生
                          (v1 让 LLM 选,产生 44 种失控值;两轴永不矛盾)
空间         space     (12 类, 原有)
知识维度     dimension (15 类: 安全合规/材料产品/性能可靠/场景适配/施工工艺/
                          使用注意/维护维修/验收质检/问题排查/报价采购/
                          成本行情/方案比较/空间体验/工期协同/其他)
                          LLM 判定+受控词校验;判不了留空=待归类,不硬塞
证据性质     evidence_nature (author_opinion/cited_standard/author_test/
                          product_claim/third_party/user_feedback/inference)
                          单一事实源;v1 的 conditions.authority_level 已迁移
不适用情形   exceptions (JSON 数组)
价格记录     prices_json (结构化: 对象/品牌/型号/规格/地区/渠道/金额/单位/
                          计价基数/口径类型/含项/时点;金额非数值或对象为空即拒收;
                          口径类型越界 → null=未注明口径,不强行归类)
```

词表：`reno/dict/taxonomy.yaml` v2（新增 stages/stage_map/dimensions/evidence_natures/condition_keys/price_kinds）。

### 关键设计决策（多角色评审收敛）

反方评审（code-reviewer，独立子代理）否决了初版三个设计，全部采纳：

1. ~~stage 由 LLM 二次选择~~ → 与 category 高度共线，会产出矛盾标签；改为字典派生，省回填成本。
2. ~~独立 price_observation 子表~~ → `replace_atoms` 重跑会孤儿化子表；改为 `prices_json` 列随原子生死。
3. ~~atom_fts 重建加列~~ → `_migrate` 在每次 connect() 执行，DROP 会与并发连接相撞；且收益小。改为只修 /api/search 兜底，FTS 不动。
4. evidence_nature 与 conditions.authority_level 双轨 → 定单一事实源并迁移存量（16 条）。
5. 回填幂等以 `dimension IS NULL` 为选择器（自然幂等）+ replace_atoms 同 id 继承 v2 字段（防重跑清空）。

## 三、实际改动清单

### 后端（reno/ + app/）
| 文件 | 改动 |
|---|---|
| `reno/dict/taxonomy.yaml` | v2：新增 stages/stage_map/dimensions/evidence_natures/condition_keys/price_kinds |
| `reno/dict/__init__.py` | 新增 stage_for/dimensions/evidence_natures/condition_keys/price_kinds/expand_synonyms |
| `reno/db.py` | 迁移加 4 列；replace_atoms 同 id 继承 v2 字段 + list/str 双类型归一；all_atoms 解析新列 |
| `reno/atomize.py` | prompt 按版本加载；_validate_atom：stage 派生、dimension/nature 受控校验、authority_level 迁移、价格结构校验（_validate_price）、exceptions |
| `reno/prompts/atomize_v2.txt` | 新增：维度/证据性质/例外/规范条件键/价格结构 规则；移除 stage 输出 |
| `reno/prompts/judge_v2.txt` + judge.py + dedup.py | 证据性质以 a_nature/b_nature 注入判定；authority_gap 规则改读新字段 |
| `reno/ask.py` | retrieve 支持同义词扩展查询；refs 携带维度/性质/条件/参数/价格；system 提示增强（价格注明时点、条件缺失分情景、观点与标准区分） |
| `reno/dedup.py` | 候选对携带 nature |
| `reno/report.py` | checklist v2（验收结构化+判定依据待核实机制）；新增 prices_report（无数据时诚实声明；未注明口径原样展示） |
| `app/api.py` | /api/search 修复（bigram 兜底+6 个筛选轴）；/api/facets 加 stages/dimensions；新增 /api/compare（词面闸门+维度分组+冲突聚类提示）；events 原子带新字段 |
| `reno/config.py` | prompt/schema/taxonomy 版本 → v2 / 2.0.0 |

### 脚本
- `scripts/backfill_atoms_v2.py`：存量回填（确定性 stage 重派生 + authority 迁移 + LLM 批量标注 dimension/nature/exceptions），meta 游标断点续跑，跑前快照 `data/reno.db.bak-backfill-*`，幂等（`dimension IS NULL` 选择器）。

### 前端（frontend/）
- `components/shared/knowledge-tags.tsx`：DimensionBadge + NatureBadge（观点/标准/实测/商家宣传…分级着色）
- `features/search`：阶段/维度筛选轴；结果卡显示维度、证据性质、条件、参数、价格摘要
- `features/compare`（新页 + 路由 + 侧栏）：2-3 对象按维度分组对比，缺证据维度如实列出，跨对象冲突聚类置顶提示
- `features/ask`：引用芯片带证据性质徽章
- `features/reports`：价格记录页签
- `lib/api*.ts`、mocks：契约与 MSW 同步更新

## 四、验证结果（真实执行）

### 自动化测试
- 后端：`pytest` **71 passed**（原 56 + 新增 test_multidim 15：stage 派生、nature 迁移、价格校验、replace_atoms 继承、search 兜底、筛选、compare 闸门、checklist 待核实、价格报告诚实性、回填确定性/LLM 标签校验）。4 skipped = 需 `RENO_LLM_TESTS=1` 的 LLM 探针（未跑）。
- 前端：`tsc --noEmit` 干净；`vitest` **43 passed**（41 + compare 页 2）。
- 生产构建：`npm run build` 成功。

### 存量数据实战（354 原子，现有 28 个视频源）
- 确定性迁移：**282 条 stage 重派生归一、16 条 authority_level 迁入 evidence_nature**。
- LLM 回填：**354/354 全部标注，0 待归类**（92 秒/18 批，检查点无中断）。维度分布：施工工艺 155 / 材料产品 45 / 方案比较 39 / 报价采购 22 / 安全合规 22 / 验收质检 18 / 问题排查 10 / 空间体验 10 / 其余 6 维 3-8 条；证据性质：author_opinion 322（这些视频主体确为 UP 主经验）、cited_standard 22、product_claim 3、user_feedback 3、third_party 2、inference 1、author_test 1；88 条带不适用例外。
- 检索复测（修复后实测）：`卫生间防水高度` 0→**70 命中**且首位即目标原子；`磁砖缝隙`（错别字同义词）30 命中。
- judge 重跑（v2 nature 注入）：325 候选对 → **16 聚类 / 13 冲突**，冲突类型覆盖 numeric/polarity/method/scope 四类。
- 报告重生成：checklist v2（验收项带判定依据，缺失时"待核实"）；价格记录报告生成。

### 新视频 v2 流水线实战（40 分钟长视频 BV1NT4y1S7JL 重提取）
- 66 原子 0 丢弃；65 条维度（1 条留空待归类——诚实不硬塞）；11 条例外；**3 条结构化价格**（监理 50 元/平米、前置过滤器 300 元、墙固 100 元/桶），口径类型被 LLM 如实标注为"估算价"（视频口播值，非成交价），含项/计价基数/单位齐全——价格记录报告即由它生成。
- 二次 judge + 报告重生成：全库 349 原子 / 16 聚类 / 13 冲突。

### 端到端真实查询质检
- **维度/性质抽样**：随机 20 条人工核对，19 条维度判定合理（1 条边界："楼板不能开槽防破坏承重"归施工工艺，归安全合规更佳，属可接受模糊带）；证据性质全部正确（商家宣传话术→product_claim、JC/T 标准转述→cited_standard）。样本小，仅作质量信号，不宣称准确率。
- **ask 实问**："卫生间墙面防水要做多高？不同说法有什么区别？" → 回答并列三种说法，区分「转述标准 [4]」与「经验观点 [5]」，按干湿分离/淋浴区条件分情景，明确"材料未说明两说法的规范版本差异，无法评判谁更权威"，结论分"求稳妥按标准/按主流经验"两路——条件保留与不确定性表达符合 §9.2。
- **compare 实测**（浏览器真机）：瓷砖 vs 防水，两列各 10 条证据按维度分组，跨对象冲突聚类提示条正常弹出，缺证据维度如实折叠列出。
- **浏览器视觉验证**（flash-visual-worker，PC+375px 移动端）：搜索 4 轴筛选/维度徽章/价格摘要、对比页双列分组、报告 5 页签、console 0 报错——全部通过，截图在 `.zcode-shots/`。

## 五、边界与未验证事项

1. **未验证**：真机 H5 布局仅做了 375px 视口模拟（非真实手机）；LLM 探针测试（4 例）需 `RENO_LLM_TESTS=1`，未在本次运行。
2. **数据边界**：行情预测/源头成本无数据来源，系统以"暂无可核验数据"状态呈现，不输出估算数字；价格记录全部来自视频口播/字幕且由 LLM 标为"估算价"，未经渠道核验，报告页明确标注。
3. **参数字典**：66 条 unnormalized 参数待扩字典（数据工程，非结构缺陷）。
4. 权威边界：转述标准的原文核验（标准号→原文比对）不在本次范围；evidence_nature 只是来源性质标注，不等于内容已核实。
5. **回滚**：judge 快照 `data/reno.db.bak-judge-*`、回填快照 `data/reno.db.bak-backfill-*` 均在 data/ 下；prompt v1 文件保留，`config.local.json` 写 `"atomize_prompt_version": "v1"` 可整体回退旧行为（新列无害保留）。
