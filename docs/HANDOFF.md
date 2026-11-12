# 当前工程入口

用户已撤销暂停，恢复原方案开发，长期 goal 为 `active`。当前继续修复后端整库 CI 的剩余失败，并推进 V3 owner 与 selector 接线。

前次暂停时点的记录保存在[2026-11-12 阶段记录](AGENT-HANDOFF-2026-11-12.md)。

恢复开发的起始提交为 `60074e2`。本轮已修正普通存储的 Python 3.11 导入兼容、Linux 锁清理夹具、Windows 专属模块收集门以及预算化同步场景。Linux／Python 3.12.10 的完整同步恢复链已通过。后端 CI 固定为 Ubuntu 24.04／Python 3.12.10，以匹配既有 selector 合同和官方解释器构建，最低版本 checkpoint CI 保持 Python 3.11。本轮各批结果、历史失败和待验证范围见[继续开发记录](closeout/2026-11-12-ci-resume-result.json)。

[2026-11-07 阶段交接](AGENT-HANDOFF-2026-11-07.md) 保留累计产品范围、r1528 静态候选身份、历史运行限制和剩余设计／开发事项。完整产品目标仍未完成。

`a4f116c` 的六项 CI 成功，后端整库结果为 12,389 通过、490 失败、93 错误、2,313 跳过。后继已修订序列化缓存基线、两组历史快照前提、原16解析／Home生产者、外部QA实例目录及resolver测试接口。已修复原发布确认完成后仍读取旧pending状态的恢复缺陷，两个完整相关模块通过64项。额外原Home回归通过76项，最终Home writer／QA模块通过62项。前端类型检查通过，两个worker的完整Vitest复检通过314项，原失败及退出码不一致记录继续保留。各批原始失败、重叠范围和当前待验证项见[继续开发记录](closeout/2026-11-12-ci-resume-result.json)。本批完整远端结果以该提交的GitHub Actions为准。

V3 的 r1530 已生成并双审 29 源／2,537,465 B 的静态运行时候选，包含 code／closure 配对、frame 模式、V3 调用、V2 同次续作及栈分配／释放接点。完整 owner／None／exec／selector、C 构建、ABI、COMPAT 与运行验证仍待完成。

V3 的 r1531 已准备并双审八份owner／F3／None／exec片段，共25,995 B。完整dispatcher的source／authority接点、selector与退休／恢复仍待组合，完整C候选尚未生成。
