# directory carrier 归还：线程与清理观察边界

本补充记录源码复审获得的两项具体规则。

原 RLock 在归还 gate 整段持有。其他真实线程调用新 loan 时会排队，等清 fence、释放锁以后才登记；这是在归还以后的合法线性化，不能要求该线程必然收到 LOAN_FENCED，也不能在 gate 内 join 它。同线程 GC 重入可以重入 RLock，必须在实际插入以前被 fence 拒绝。线程与 GC 两类 oracle 分列。

当前 gate 的所有 close 在 fence 内也拒绝。原 `_leave(gate, True)` 尽管 close 被拒，仍会真实记录 Call.cleanup_failure 和 owner cleanup。这不是无写入的新借用尝试。即使 callback 捕获其异常，最后删除前仍须核原 raw/facade cleanup_failure 都为空、owner cleanup 为空、Call仍open且原单项membership未变，否则不删除 carrier。gate正常finally只在清fence后归还其Call loan。

首版六项源码阻断、第二版重入清理漏核均保留原红；静态compile不是候选执行或产品成功。后续须先完成源码双审、脚本与实际准备双审，再消费新claim。未授重放r920/r964或执行待答r967。
