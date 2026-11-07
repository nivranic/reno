# H01 关闭链：live-SQL、SQLite 与临时目录

本块继续主方案 §17.300，补齐来源采集中的实际资源回执，供登记前的统一聚合消费。保持原读取、复制、终态验证与首异常规则。

1. `personal_authority_live_sql_deadline` 为原 DB/WAL/journal 的实际 HANDLE→FD→FileIO owner 增加缓存回执；仅合法原生关闭返回可记 CLOSED，转移与关闭分开，UNKNOWN 不重试。
2. `personal_authority_live_sql_held_deadline` 缓存当次下层回执；自身正常退出必须包含原第五遍、下层合法退出及末 deadline，旧 evidence 与费用账本保持。
3. `personal_authority_sql_bootstrap_deadline` 补四个 Cursor 和 Connection 的严格 None 关闭校验，并公开其已有账本的固定快照。projection 的既有严格关闭记录继续复用，不能拿四个 bootstrap Cursor 覆盖其他 Cursor。
4. `personal_preservation_source._PersonalPrivateDirectory` 直接记录实际 TEMP cleanup 的一次尝试与合法返回。父类丢弃返回值，因此子类必须观察真正的 `temporary.cleanup()` 返回。仅记录清理调用事实；本块没有路径缺失末核，`post_cleanup_path_missing=None`。
5. `personal_authority_committed_image_deadline` 在实际 SQL/TEMP 收尾中缓存回执，保留原 evidence 和保守 aggregate 状态。获取回执失败不能替换首异常或返回正常结果。

回执只含固定版本、类型、scope、缓存标志与资源状态，不含路径、数字 HANDLE/FD、异常或 callback。getter 不调用 clock、provider、文件系统、SQL 或 frame/locals。物理关闭与正常退出分别记录；真实关闭后返回边界失败时，不能抹去 CLOSED，也不能把 UNKNOWN 修成 CLOSED。

新增独立 Windows 临时 EX/WAL 与 SQLite/TEMP 测试，控制 seam 明确标注。执行相关既有回归，保全完整 before、candidate、inverse、实际工具返回、普通 pytest 报告和结果；声明用例不冒充实测通过。原 product30、parent360、matrix1680、native20/25/10 与 r730 采样耗尽保持。

本块仍不提供整体 `source_closed`、SQL 登记、Store 安装、活 witness 或写权。最外 EX 的实际 unlock、锁文件和目录资源，以及同 operation/token/capture-instance 的一次消费聚合仍需后继实现。
