# Task9A 完整模块与原 scope 准入检查

继续既有 executing-plans 流程，仅复用 `/root/r924_independent_review`。本方案细化 r1062 NEXT 的第1/2项，不缩小完整Goal，也不授权旧诊断重跑。

## 目标和来源

把 r1059 的158份完整源码/资源闭包与 r1062 SOURCE04 的37份候选组合起来，检查真实模块启动、固定 marker consumer 的原绑定/别名，以及原 scope 查询的拒绝和账本保持。候选字节不修改、不安装；预期相对r1059仅 scope/resources/lifecycle 三份字节变化，其他155份保持。

上轮21项是AST/合成检查；本轮禁止AST摘取、产品模块替身、删除原认证/延迟gate或以旧绿补数。完整模块导入允许原DLL/ABI、第三方、词典和隔离配置的正常初始化读取。这不等于调用业务native、真实EX、SQLite/SQL、Store或marker IO。

## 落地步骤

1. 在 `data/t16-h01-marker-bootstrap-qa-r1063/` 保全158份完整父本、组合、全文diff及manifest，核r1062源码双审精确0与r1059来源。静态compile全部Python文件，其他资源逐字节核同。Root和同一只读代理复核组合/设计，不能把r1062的局部运行证明升级为完整启动。
2. 新增八个具名用例，每项独立新进程、真实包导入，所有`reno.*`模块的`__file__`/bytes/SHA须落在受审组合并核同：
   - B001：enrollment先导入，核resources原认证、固定dispatcher/checker/接口绑定、三个最终caller和原alias。
   - B002：lifecycle先导入后enrollment，核同样绑定；只证明这两条具名导入顺序。
   - S001：原profile owner/lease/admission/budget/group/origin/scope建立后，在原Call查询得到同一scope，原current/Token/grants/ledger保持。
   - S002：原current临时丢失时，同preferred查询按确切code拒绝；恢复故障注入Token后原scope正常reset，不授业务退休。
   - S003：当前strict scope存在，原未登记_FileLock无parent调用marker_begin，在任何stream/fileno getter前拒绝。
   - S004：同S003且current临时丢失，独立presence仍拒绝，不降legacy。
   - S005：第二个原custody/Call查询第一scope，拒绝前后两owner loans和Call事实核同，不keep foreign。
   - S006：同一原预算request32→原查询保持pending grant→charge31→原查询；核实际grant/request/charge/history身份和counter，查询不写账、不旋转Token。这是内存账本检查，不是marker IO结算。
3. 脚本、catalog、prepare/admit完整版本双审。实际准备封存全部输入/副本、新空子目录和隔离`{}`配置，再双审。新一次claim先于spawn，顺序运行八项，原限额不变，失败只定向复检失败ID。
4. 任何导入、认证或断言失败均保原红和准确失败阶段，不删除caller检查或以stub补通过。必要源码修订另保完整候选、diff/逆向及双审；只重检失败项，并明确新组合未重跑的旧绿。
5. 两份实际记录复审后，更新主方案/AGENTS/当前入口。只有实际观察的范围写通过；仍未安装。

## 边界和后继

本轮不进入resource start/EX，不调用marker seek/read、SQL、Store或业务native；不操作正常库、服务/dist、ACL/settings、真实平台和Git。上下文故障注入仅在新子进程中临时set/reset原ContextVar，不新增产品Token或改变原scope实现。未知锁仅分配空_FileLock和无副作用getter计数对象，不构造真实资源。

原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children及native20–25–10不变。无profiling、frames/locals、停点、定点计时或异常链诊断；预算用墙钟等待不充作性能测试。r920/r964已耗不重放；r967待原问题不执行不重问，P06历史0/7不再索备份。

本轮通过也不能完成Task9A：真实已发行lock来源/首marker、group/guard运行、原清理与全部矩阵仍待。Task9B全precall目录、完整物理和费用债务、guard消费者、两阶段terminal、容量/durable恢复、原111/94/r919、真实Store写权和主方案其他开发继续，Goal active，非只剩验收。
