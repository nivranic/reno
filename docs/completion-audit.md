# 完成度审计清单(对照目标:直接开发直至完成、迭代优化)

> 目标拆解 → 逐项证据;收尾时逐项核对,未全绿不宣告完成。

| # | 目标拆解 | 证据要求 | 状态 |
|---|---|---|---|
| 1 | P0 流水线硬化(CLI 日常可用) | `reno import/run/status/judge/report` 全可用;5 步全绿 | ✅ 冒烟视频全绿,CLI 全命令实测 |
| 2 | 断点续跑/幂等/重复拦截 | 杀进程恢复实测;SHA-256 拦截实测;worker 孤儿恢复代码+实测 | ✅(孤儿恢复实战两次) |
| 3 | 原子可追溯率 100%/Schema 合法 | 证据 ID 强校验+零证据丢弃;冒烟 3 原子 10 引用零无效 | ✅ 机制级保证;全量数据待批量后复核 |
| 4 | 参数字典双层归一 | 1.8米(m)→1800mm+规范名;未覆盖标 unnormalized | ✅ 单测固化 |
| 5 | 单元测试 | pytest 全绿 | ✅ 10/10 |
| 6 | P1 四联动详情页+实时抽帧+Range 流 | 端点验证记录(phase-1 报告) | ✅ 端点级;视觉验收归用户 |
| 7 | 冲突复核卡+决策落库 | POST /api/decision 实测 | ✅ |
| 8 | 中文 FTS 检索 | "插座"3 命中 | ✅ |
| 9 | URL 导入(收件箱+iOS 快捷指令) | /api/import 实测 + ios-shortcut.md | ✅ |
| 10 | **批量回归:21 视频零干预跑通** | `reno status` 全 processed;atomize 全 OK | ⏳ **1/21,配额看护续跑中** |
| 11 | judge:跨视频聚类+冲突(与基线对齐:插座 polarity/防水高度 numeric) | `reno judge` 输出 cluster/conflict 记录 | ⏳ 待批量(只读预验证已通过) |
| 12 | 质量闭环:增量 diff + Checklist + 争议报告 | docs/generated/ 三文件生成 | ⏳ 代码就绪,待数据 |
| 13 | 决策界面闭环(冲突→决策→状态) | #7 已证;全量后复核 | ✅(机制) |
| 14 | 文档(README/快捷指令/phase 报告/成本实证) | docs/ 文件齐 | ✅ 0/1/成本;phase-2 待批量 |
| 15 | 迭代优化记录 | phase 报告缺陷清单 #1-#22 | ✅ 持续累计 |
| 16 | git 版本纪律 | commit 历史 | ✅ 2 commits(后续批量后再 commit) |

## 待批量完成后的收尾动作

1. `reno status` 全 processed → ② 失败视频逐个归因(瞬态/内容问题)
2. `reno judge` → 聚类/冲突统计;与可行性基线冲突场景对照(插座 polarity、防水高度 1.8 vs 2)
3. `reno report` → 三报告生成 + 抽查内容质量
4. FTS reseed(批量原子入库后)
5. 重启 serve,端点快照
6. phase-2 报告 + git commit + 更新 memory
7. 向用户交付总结(含 coding-plan 配额结论)
