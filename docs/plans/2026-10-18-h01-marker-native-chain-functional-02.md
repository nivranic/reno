# Task9A 原生锁发行、marker 三调用者与实际账本回归

承接 r1065：SOURCE01 的 158 个完整文件保持；S001/S006/N001/N002 新批 4pass/0fail/缺0，13.094 秒，源码、脚本、实际准备与记录均双审。它们没有执行资源 start、marker IO 或 EX。本批不重跑任何旧绿，不新增产品修订，不安装候选。

## 固定新用例 F001

仅一个全新隔离子进程，使用 r1065 完整原模块，没有 AST 摘取、合成产品模块、monkeypatch、native 替换、业务 slot 手填、计时探针或新异常链观察器。普通未捕获测试失败保留 stderr；不枚举异常链或读取 frames/locals。

测试 fixture 只在本批新 `runtime/F001/shared` 建目录，用原模块的 LOCK_NAME 和 _MARKER 以 xb 写一个初始化内容文件并关闭。四个 profile 路径共用这一根，数据库路径仅是该目录下不存在的 reno.db。此 fixture 不是产品 initialize 成功证据；不调用初始化的 rename，也不接触真实库。必须保 fixture 步骤，不因任何失败跳过、改 seam 或重试。

随后按原 profile owner、custody root lease/reservation、同 Budget、`_construct_group_origin_owned`、`_claim_joint_origin_owned` 取得实际 binding/group/scope。保原 declared_capture_count=2，不改成 0，不填 capture 槽，不构造伪回执。

1. 不提供 parent Call，执行原 `with enrollment._ExclusiveScope(group,budget)`。原 group.__enter__ → _guard_many_owned → _open_root → resources.perform 的十八 stage 自行创建和登记真实 directory/stream/lock 与 native journal，完成原 weak lock 发行、LockFileEx 和第一个 file_lock marker 读取；不得以空锁或仅负例拒绝替代。原 StorageGuard.verify 的 lock_group 调用保完整执行，group 首 Call 的来源仍由产品自身选择。
2. active body 内，通过原 `_capture_guard_file_plan_original(guard)` 获取实际 guard carrier；用同一原 lease 的临时 held Call 解包并核来源，执行原 context/shape → directory-before → files → directory-after。directory-after 在 finally 中执行，files 抛出时仍完成后目录观察，且次级失败不能替换 primary。保全部原调用和断言；这一段只证明原 guard-plan consumer，不宣称完整 Namespace/Store 正文成功。关闭这个临时 Call，不让它改变最初 file/group 无 parent 的入口条件。
3. 退出原 with，让原 group 的后置 guard.verify、_retire_context、ExitStack 三种真实资源清理和原 close receipt 读取自然执行。没有实际 capture，因此 group receipt 的 lineage_complete/normal_exit 必须保持 False；它不能成为业务 EX 正常完成或 source_closed 的证据。各 directory/stream/lock 的 native 关闭状态及 context reset 必须单独核定，不能由子进程退出推断。
4. 原 with 成功返回后，再创建 held Call 检查自己 custody 的原 path-record partition（只读）与原 settled_history。确认至少存在 file_lock、lock_group、guard_plan 三种真实 _MarkerRead，全部绑定同 scope/group/root/row 和真实 stream/raw/fd，actual 是原 _MARKER，四个 request/seek/read/charge cell 已完成且无 failure，caller body 已完成。分列两层实际断言：marker 的四个 _MarkerCell 只保存 request 的 int32 和 charge 的 None 等原返回，必须核自身原 raw/descriptor 身份、实际 bytes、同 scope/group/root/row/stream 和全部正常返回；它们不直接引用原 Scope._Grant 或 control._Charge，不能把两类 cell 或 32/None 的身份当作桥。另核原 Scope._Grant 的 request_cell/charge_cell 与 settled_history 中原 request/control._Charge 逐项为同一对象；结合原同步调用顺序、没有手工 request/charge 的脚本、marker/grant/history 精确集合数和累计实际 bytes 核同同预算费用。此集合/顺序关系不是新增 direct pointer 证据；总额必须等于本批实际 marker bytes，不允许手工 charge 修正。
5. 检查 directory/stream/lock 各自缓存的原 close receipt：directory handle closed；stream handle transferred、fd/raw/buffer closed、numeric_owner；lock acquired、unlock consumed/unlocked。保 group 两个 None capture 和非 normal 状态。用原 `_reset_owned` 复位 scope，和外层 finally 共用一个在调用前消费的 attempt flag，全流程最多一次；post-close 仅读自己的原 raw/cache，不调用要求资源仍活的 marker admission。确认原 ContextVar baseline、group context reset、pending reservation 的原 lease/budget 根仍在。context reset 不是业务退休或 root release。最后核所有实际导入 reno 模块的完整源码 hash。

任何业务/断言失败仍先走原 with 的退出协议。测试 finally 只尝试一次原 scope reset；若已有主异常，reset 失败不能替换主异常。原 entry 失败时不伪造 entered，不主动第二次 group exit。测试自身不增加任何产品异常诊断输出。

## 原生批限制与准入

设计双审 → 完整冻结来源及脚本双审 → 新空隔离根/保护清单及实际准备双审 → 新 admission/单次 claim → 唯一 F001。复用同一只读复审代理，不新增代理或重复索权。没有产品源码变更，完整来源以 r1065 SOURCE01 全部 158 个 candidate pin 为准；prepare/admit 及执行前复核原字节，不能因旧模块重导入换成正式源码。

保 product30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2直接 pytest children 和 native20–25–10：本批只有一个直接 Python child，原生 child 等待最多20秒；supervisor业务加清理总期限25秒，清理最多10秒且受剩余25秒夹紧。唯一 Budget deadline 在原构造前设为原允许的20秒，不延长、不分段刷新。输出总量沿用 wait 后累计1MiB检查，明确不是流式限额。fixture/包/DLL/ABI/第三方/词典/隔离配置读取和实际业务 IO 分列。

本批只操作新隔离目录的一个 marker 文件和原生资源，不运行 SQL/Store/正常库/服务/dist/ACL/settings/真实平台/Git。r920/r964单次诊断耗，不重放；r967仍待原问题，不执行、不重问；P06历史0/7不再索备份。

若红，保单次完整结果，按实际失败位置审源码，后继新修订只复检失败ID；不重跑未改失败，不为取绿缩减 F001。即使通过，也不能完成 Task9A 的 legacy 精确查找/迭代、外层异常映射与原 tb/清理矩阵，更不能完成 Task9B 全 precall/物理费用债务/terminal/容量 durable 恢复、原111/94/r919或真实 Store 写权。canonical/c973 apply0，历史11，产品pass0，完整 Goal active。

## 修订02

I-D001 修复：旧01把 marker runtime cell 与原 grant/ledger 身份关系表述过强，独审红与 Root01 漏审保持；本02明确两层断言及无直接指针。原 F001 全部真实构造/读取/清理步骤不删，原捕获缺项和非 normal 结论保持。候选与native执行仍0。
