# Task7 pending 前件：实施修订02

保留原 `2026-10-17-h01-pending-reservation.md`，其32槽、逐分配根存、内存/字节/终止边界不变。本修订补齐独立设计审查指出的三个实施前缺口，优先于原稿。

1. 三个入口的最外 try 在 token、budget、`_arguments` 前开始。严格参数对先完整校验原 root 并原子 admission，一次占用原 reservation；并发或重复 admission 拒绝。admission 返回原 reservation capability，finally 通过该原 capability 移交，不在损坏 facade 上重新寻找根。未 admission 的 foreign 请求不移交别人的槽。
2. budget 实际构造返回后立即绑定同槽。每次 `_Budget.remember(primary)` 在原 callback_primary 逻辑之后，观察实际异常及 BaseException 原 traceback descriptor。槽首次原 first/tb 只写一次；后继 cleanup 的原异常/tb 在它被 raise first 替换前独立保留。重复观察原 first 不更新其首次 tb。最外 except 另记最终 escaping 对象/tb；token/budget 构造前失败也由同槽接管。
3. observer 自身失败不能覆盖原 first：budget 保存 observer failure/tb，保持返回原 first；最外 handoff 接收该失败。handoff 本身失败时，保留原已预留 anchors；若有原异常继续抛同一对象，无原异常则抛 handoff 错。内存耗尽等可能同时阻止错误记录分配，不能宣称此时能完整记录所有异常，但原已写入 anchors 不主动释放。

实现范围仍为新 pending 模块与 custody/enrollment 两处接线，37文件未安装候选。正常退出暂也 pending，`_finish_operation` 硬拒不变；不授 physical/source_closed/write 权，不启 strict。Task7 后续和 Task6 剩余 caller 保留。只读快照认证原返回句柄，对外不返回可修改 reservation；快照中的原容器身份仅供恢复核对，不提供资源操作方法。
