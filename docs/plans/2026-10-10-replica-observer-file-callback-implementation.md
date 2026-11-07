# Replica observer file callback Implementation Plan

**Goal:** 修复 r768 实际 `ObserverGateError("gate_invalid_callback")` 暴露的真实文件回调接线，继续原12 collect-only及完整原方案。r768工具1/内部3、JUnit internal error、空collection/events、fallback状态与原3个目录pass均保持，不能记为收集完成。

**Architecture:** 新独立 `data/t16-h02-replica-observer-file-callback-r769/` 从已冻结r768 108132/SHA `5fc54286f37719a6328c662a3e1609e2b57a80c06ab46657423976ae988de503` 复制。原r766 observer API不变；新runner用具名普通Python函数转调自己刚捕获的原unbuffered FileIO写方法，供既有callback类型门接受。实际C回调精确类型由静态C源码与后继真实运行限定，不能用BuiltinFunctionType泛化全部C方法。

## Task 1: 证据与有限接线

- 读取 r768 `exploration-candidates/observer-callback-contract-followup.json` 的源码链及未知边界。实际表达式是 `Path.open('xb', buffering=0)` 后 bound `.write`，没有BufferedWriter/flush回调。
- r766 `_accepted_write_callback` 已接受 exact FunctionType/BuiltinFunctionType/MethodType；不得描述它仅接受普通函数，不扩大到任意callable或新增类型白名单。
- 在新runner仅捕获原 collection/event 文件的 write，然后分别定义具名普通函数 `def ... (raw): return original_write(raw)`。只转调一次、不吞异常、不改返回值、不额外flush/reopen/close/write，不格式化输入、异常或自定义对象。
- 原first/控制异常传播、shortwrite门、table131072/line1024/events62464/status1024、strongref成本和原正常close顺序保持。所持方法与文件引用生命周期如实；不宣称完整own-closure监控或写权限owner真实性。

## Task 2: 新独立候选

- Worker仅拥有新r769 runner、完整before和stage notes；原r768/r767及1171/1154/103/111/1027 frozen文件绝不修改。
- 新BATCH/claim仅r769，slot仍r767、observer/proposal仍r766；原Windows条件插件绑定与已修函数codequalname门保持。
- 不改产品/test选择器、九组93、原12、two-self/GO/DETACHED、20/25/10/1680、Job/HANDLE/三sink正常收尾。
- Worker不import/compile/run candidate或pytest/product/native，不写manifest/claim/准入，不改正常库/服务/dist/ACL/settings/模型平台、commit/push。各方不回退他人变更。

## Task 3: 独审、冻结与实际验证

- 独审准确delta（局部write bridge及新batch/来源声明）、原core effects和新增强引用；两次原失败完整保留。不扩大guard/kernel。
- Root独占新freeze、准入与一次实际collect-only；不使用已消耗旧claim，不重跑未改red，不扩时间或r730已耗profiling。
- 实际Root tool/runner/child/pytest返回分开；成功须12真实table/map/bindings/session return、JUnit0case、genuine session_return/status_unknown和正常writers终态。
- 成功仍是collection integration，不加到原93或3个已确认目录pass；失败保完整raw残件及fallback状态与真实traceback的不同证据层次。

## Task 4: 后续原范围

- 完成实际collection后推进原矩阵/callback/其余93，依据已有静态成本报告决定保原断言与期限的优化，不把报告当超时归因或跨gate缓存许可。
- H01原30秒、H02完整source/target/coordinator/restore/consumer、H03–H06/writers、分享/P06/T15/生产云及主产品计划原未完继续保；goal active。
