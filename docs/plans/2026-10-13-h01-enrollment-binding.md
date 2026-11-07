# H01 原主登记：来源内容与 namespace3 的版本化绑定

本记录补充主方案 H01 的登记依赖。新模块已实现并通过 76 项隔离纯合同测试；完整 Store 登记、SQL 提交和活写权见证仍未实现。

## 问题和决策

最新非生产候选保存在 `data/t16-runtime-graph-cpu-r729/authority_runtime_graph_cpu_r729/`，含 Store、登记和 SQL/CAS 代码。它绑定旧 namespace2；登记准备从六个观察标量重新构造 intent，不能直接消费当前 origin second 封存的原始 intent bytes。普通 origin 观察和 activation 的拒绝门也尚未闭合。直接搬入该候选不能证明当前登记完成。

采用独立 `personal_authority_enrollment_binding` family、format version 1。旧 authority format1 和 namespace3 的字段、摘要含义及字节解释保持各自版本；新族显式连接它们。修改旧格式字段意义会破坏原件核对，而直接沿用旧候选会漏掉新的 origin pair 和两种 profile 角色，因此不采用这两种做法。

## 已实现的接口

`reno/personal_authority_enrollment_contract.py` 提供以下纯内容接口：

- `build_binding(first, second, namespace_reservation, root_seal, header)` 校验五份原始 bytes，绑定原 init UUID、domain、facility、node、namespace 原件和 SQL 观察。
- `cross_validate(binding, ...)` 对原件重新构造预期内容，拒绝原件、引用、身份或角色不符。
- `prepare_authority_prefix(binding, ...)` 直接保留 second 内的原 intent bytes，准备旧 format1 的 local contract、init seal 和 genesis 内容链，以及新族的 prefix 引用。
- `cross_validate_authority_prefix(content, binding, ...)` 重新核对内容对象的全部五份 bytes；冻结对象的声明不替代验证。

新族只有 `enrollment_binding` 与 `authority_prefix` 两种 kind，唯一 scope 为 `content_only`。issuer 的 profile 摘要表示四路径 ARRAY；物理 profile 的 DICT 摘要另具名保存。两种 role 标签独立核验，即使摘要值相等也不能互换。

当前 epoch、primary node、个人 meta 与十表摘要从 second 内的原 intent 取得，不接受调用者另外传入六个观察标量。原件之间和四条 authority 记录之间的 context、完整 bytes 引用及 epoch 都须闭合。输入、JSON 深度、节点数和单记录大小有界，拒绝多余字段、重复 key、非 canonical JSON、错误版本及非法整数/UUID/SHA；控制异常和 MemoryError 保留原对象。

## 实际验证与边界

五份测试原件逐字节复制自 r847 已通过的隔离 namespace 工厂场景，并附来源 pin。测试仅读这些可随代码保存的 bytes，不会打开原件内部记录的历史物理路径。重连的异常素材明确作为合成声明，不当作新的物理事实。

r849 两份独立源码复核均无阻断。唯一隔离运行实际 76 unique / 76 passed / 0 failure-error-skip；228 份普通 pytest phase 报告完整。pytest、子进程和工具退出码均为 0，无超时，parent 为 2.5149999999999864 秒；49 个冻结输入前后一致。291 对选定原件与副本完整 bytes 已保存，合计 5221095 bytes。记录位于 `data/t16-h01-enrollment-binding-r849/`。

这些结果证明声明校验和原字节前缀准备，不证明 SQL COMMIT、原文件原生关闭、最外 EX 退出、永久 Store 发布或活写权。普通文件保全也不证明原生 identity、ACL 或恢复行为。系统 CIM 进程查询返回 OS AccessDenied，未据此作全进程或 allwriters 判断；本次明确创建的 QA 子进程已有退出终态。

## 后续原范围

1. 将原主登记 continuation 接入当前 origin pair 和 namespace3，使用同一个实际 EX、同一个原 30 秒 deadline；永久发布和独立读回新绑定及 authority 原件。
2. 实现实际 owner/producer 关闭见证。借 EX 的内部结果与拥有最外 EX 的公共 factory 分开，完整退出和末 deadline 检查后才交付完成结果；不能添加一个布尔 `source_closed` 代替物理关闭证据。
3. 实现 joint authority SQL 登记与 journal/CAS。SQL 安装会改变原 SQLite bytes，须保留原 second 并产生独立后继状态，不能放宽原 resume 或重新解释旧摘要。
4. 接活 witness 到个人 mutation/upload/现场写 gate，再落实停写、最终包、晋升、恢复和永久撤销分享。

原 30/360/1680 秒和 native 20/25/10 秒限制保持；r730 采样授权已耗尽。P06 七历史身份仍 0/7，用户没有来源，不再追问。正常库、服务/dist、既有 ACL/settings、平台及 Git 提交未由本批修改。完整 goal 仍 active。
