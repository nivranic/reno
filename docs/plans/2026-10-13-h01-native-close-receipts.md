# H01 原生资源关闭回执

本块接续登记前缀发布，为实际目录和只读文件 owner 提供退休后可读取的关闭事实。回执只用于后续聚合；不等于 `source_closed`、SQL 登记、活 witness 或写入许可。

## 选择与范围

采用实际 owner 的缓存账本。普通 context 返回无法覆盖失败退出中的物理关闭；退休后的重新探测可能混入复用后的句柄或产生新的 I/O。当前块因此只记录本次实际调用的返回结果，并在 node stream、held file 和 image private pin 的收尾中缓存下层回执。

目录与只读 lease 分别增加 frozen DTO 和 `close_receipt` 属性。字段包含固定版本、kind、`native_owner_close_facts_only` scope、阶段、进入是否返回、退出是否尝试、失败及正常退出标志，以及已有资源账本和缓存文件身份。不得包含数字 HANDLE/FD、路径、异常、callback 或写权标志。读取属性只复制自身缓存业务字段，不读 clock、provider、native 状态或 frame/locals。

## 关闭规则

关闭前置 `attempted`；Win32 CloseHandle 只有 exact signed32 int 非零返回可记 `closed`。原零/False 失败分支保留立即 GetLastError，任意 truthy 或非法表示均拒绝。FD close 和实际 wrapper retirement 只有 `None` 返回合法。`unknown` 不重试。HANDLE 的 `transferred` 仅表示责任交给 FD，必须查看 FD 独立终态。

`normal_exit` 必须本次 enter 已返回、第一次正常退出完成且没有 body、进入、cleanup 或末 deadline 错误。物理 `closed` 可以与正常退出失败同时存在；首异常原对象优先。未进入或失败收尾不能被后来的空退出升级成正常。

source 层保存下层回执，不改变旧 evidence；image private pin 增加单独缓存属性，不改变已有 `pin_state` 和 evidence 规则。下层 enter/exit 抛错后仍保存其已经记录的关闭事实，不能为了快照丢失首异常。

## 验证与后续

新增真实 Windows 临时目录/文件验证及受控 seam 验证：合法关闭、FD transfer、False/非法返回、关闭后抛错、body/control、失败进入、等于 deadline、重复退出不升级、退休快照没有动态访问、外层缓存下层终态。旧测试保留，与修改相关的旧用例作为回归执行。

先保留完整 before/修改记录，完成源码审查后执行一次新隔离测试。保留真实工具返回、普通 pytest phase/JUnit、输入及完整结果，失败不伪报通过。原 product30、parent360、matrix1680、native20/25/10 和 r730 采样耗尽保持。不修改正常 DB/service/dist、既有 ACL/settings 或 Git 提交。

后继仍需 TEMP/live SQL/最外 EX 的实际回执、同 operation/token 的一次消费聚合和 joint authority SQL/journal-CAS。本块不新增写权限。
