# Task7：原始操作图的容量预留与恢复持有

承接 profile-route-custody 的 Task7，基于 r1035 SOURCE02。目标仍是完整的正常终止、失败恢复及最终释放。本次先实现其中必要的产品前件：资源取得前预留一个有界恢复 owner，三个 owned 主体最外 finally 把实际原图和错误移交给它。严格 route 继续不激活，`_finish_operation` 的终止拒绝保留；不把本次前件称为 Task7 完成。

采用独立的专用 reservation/pending 持有者。普通 `_OWNERS`、`_SUBJECTS`、`_CALLS` 与32个 locator 分区目录继续只存弱引用；专用恢复 owner 明确且有界地强持 original owner/state/五容器/32分区及实际 root lease。这与仅把调用方暂存称为恢复持有不同。另两个方案——最后失败时才新建 pending owner、或依赖 weakref/GC 回收未明操作——都会留下容量或持有空窗，故不采用。

## 状态及不变量

1. 模块内固定32个 owner 槽，reserved/active/pending/allocation_failed 同占此上限，锁内原子准入；满额先拒绝，再不执行任何 `_Custody`/state/分区或物理资源分配。这个上限只约束 owner 数量，尚不证明每个图的字节容量。原 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/并发限制保持。
2. `_new_operation` 先 reserve。每个原对象分配实际返回后，先记入同一槽的原始 anchor，才发布到 facade/32表/后续对象：owner、tables、每个原 partition、subjects/loans/cleanup/carriers、state、ticket、lease。不得等完整 return 才第一次保存 state。allocation 失败也留在这个原槽，记原异常和 traceback，不丢部分图、不另申请槽。
3. activate 只能接受该槽保存的同一原 `(owner,ticket)` tuple；对外仍返回原 root lease，不新增 JSON/DTO 字段。槽的 roots 保存的是实际分配对象，恢复读取不通过可损坏的 owner facade 重新找图。它们保证绑定不因 facade 改指而丢失；不声称能恢复对原字典/list任意原地 clear/pop 所删去的全部内容。
4. 三个 owned 入口在严格 root admission 时额外确认该 root 有原 reservation，且尚 active；foreign、复制 tuple、显式 None 或已经 pending 的 lease 不补签。在最外层新增 finally，位于 packet retirement 和 route context end 之后，即使两者 raise 也会执行。只把这个 reservation 从 active 转 pending，不重试 close/reset/consumer，不释放 root/scope/carrier，也不更改已有提交事实。
5. 移交保存 budget 的原 first 及其实际 traceback，另记录当前后继清理异常及其 traceback。原 first 不被当前 `sys.exception()` 替换。移交函数自身出错时，已预留槽仍强持原 anchors；有原 first 时维持原异常对象，移交错误作为后继记录，不从 finally 抛新错误覆盖它。正常主体成功但移交失败时必须失败，不返回伪成功。
6. 恢复读取仅返回不可序列化的只读 snapshot，含同一原 lease/anchors/异常身份，不授任何 physical-close/source_closed/write 权；不删除或重试 pending。没有 TTL、逐出、GC 自动归还或同 identity 查找补签。快照句柄只能认原 registry 发出的实际对象，不能凭 id/字符串伪造。
7. `_finish_operation` 继续拒绝；正常原 consume 的外层返回/effect、scope/carrier loan 归还、完整资源债务汇总、两阶段 terminal permit、完整图字节限额和 durable 重启恢复仍须继续实现。本前件填满后会明确拒绝新操作，在这些项目完成前不得启用 strict。内存 owner 不是重启保全。

## 产品改动

- 新 `personal_authority_guard_pending.py`：固定有界槽、原分配 anchors、active admission、pending 移交、原错误记录及只读快照。
- `personal_authority_guard_custody.py`：根分配顺序改为 reserve→逐分配保存→原弱登记→activate；捕获新的固定函数/类目录。增加具名的 root admission、handoff 与 snapshot 薄入口。原 loan/表/record协议与 `_finish_operation` 硬门不改。
- `personal_authority_enrollment.py`：三个严格入口同步 root reservation admission；三个最外 finally 直接调用同一原 lease 的固定 handoff，保持现有 packet→route end 顺序及 first/tb 语义。legacy 两参数均省略的 AST 除机械外层 finally 外保持。

保全36父文件，新增一个模块，形成37文件候选；源码、设计和运行各按既有两审。验证以新增可复用测试模块执行真实新 runtime、原 custody 全文和三个原 owned 主体的控制流；EX/native/SQL 叶仍合成并明确区分。检查容量拒绝发生在分配前、部分分配保存、两实际线程竞争、各阶段失败、同一原lease/分区/异常/tb、foreign/重复拒绝、三个 finally 在 packet/reset 失败下仍移交、legacy不分配，及强持到快照读取。原已通过业务检查不机械重跑。

不改正常库、服务/dist、既有ACL/settings、真实平台或Git，不执行已耗 r920/r964，不执行/重问 r967。Task6 剩余实际 caller、Task7 后续、原安装门及完整 Goal 保持。
