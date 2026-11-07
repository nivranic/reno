# Resource dependency precall implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** 在 resources 的两个固定依赖认证调用前验证实际目标，关闭 r1072 R001；不推断 F001 超时原因。

**Architecture:** 使用导入时工厂生成原 `_authenticate` 入口，在闭包内保存原模块、模块字典、函数及函数元数据。入口按 scope、registration 原顺序逐项核对并各调用一次，然后执行原资源认证余体。每次均重新检查，不缓存成功。

**Tech Stack:** Python 3.13、完整158文件候选、只读 AST/字节核验、隔离子进程定向检查。

本方案沿用用户持续实施及同一只读复审代理授权。正常库、service/dist、ACL/settings、平台、Git均保持。r920/r964耗尽不重放；r967待原问，不执行或重问；P06历史0/7不再索材料。禁profiling、frame/locals、停点、定点计时、新异常链诊断。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children与native20–25–10保持。

## 方案选择

采用闭包内的逐callee内联门：预期不依赖可写global checker，也不早捕获晚期 `_PINS`。整体入口只查一次两目标，会遗漏第一调用与第二调用之间的变动；增加全局checker则新增一个需要前置认证的动态目标。两者均不采用。

拒绝使用闭包捕获的原 `ValueError`，固定文本 `GUARD_CONTROL_SCOPE_RESOURCES_DEPENDENCY_CHANGED`。拒绝路径不调用可能已损坏的 scope._fail/control._fail。正常依赖异常照原样传播，不捕获、重试或改写。

## Task 1：冻结依据并完成两份设计审查

Create `data/t16-h01-resource-dependency-precall-r1073/`。冻结本方案、r1072 SOURCE-REFERENCE/SUPPLEMENT及两静态报告、r1070 SOURCE01、resources/scope/registration完整源码和原保护清单。核当前158源与56同18缺保护。

保存 DESIGN-01.json 和 Root/同一独立代理设计审查。源码实际缺口阻塞数与静态报告交付一致性分列，不以交付0阻塞把R001写成已修。

## Task 2：生成完整候选并完成两份源码审查

仅改 `reno/personal_authority_guard_resources.py`，其余157文件完整继承；不安装至canonical/c973。

1. 导入原 ModuleType。将原 `_authenticate` 变为 `_make_authenticate` 返回的同名闭包。
2. 工厂捕获原 builtins：object.__getattribute__、type、dict及dict.get/dict.items、str、tuple、len、ValueError、FunctionType、ModuleType；捕获资源原global字典和独立missing。
3. 工厂仅保存两个既存顶层 `_authenticate` 的原模块/字典/函数，以及 code/defaults/kwdefaults字典与完整条目、closure与cell内容；不调用依赖认证，不捕获registration._PINS，不要求Budget或marker已经ready。
4. 每项调用前先扫描资源原字典所有key为exact str，再查该alias仍是原exact ModuleType及原字典。依赖字典同样先扫描exact str keys，再取 `_authenticate`，核原FunctionType身份、code/defaults/kwdefaults/closure及原cell内容；kwdefaults先exact dict/长度/全部exact str keys，再用捕获dict.get比较原value is。合法None/False与缺失分离。
5. 通过后调用该原callee一次。scope返回后才验证registration，防止第二callee沿用首个callee前的旧检查。保原scope/registration顺序、两次调用次数及正常异常传播。
6. 依赖门之后的原body完整保留，初始 `_PINS` 逻辑和原类/函数/Context校验不合并、不删除。将工厂加入原函数目录，ModuleType加入绑定目录；其余目录与元数据结构保持。
7. 保存parent/candidate/inverse完整文件与全文diff，compile全部Python，AST证明原余体及其他顶层定义不变、原依赖调用次数未删减。两份源码审查通过后才准备运行。

## Task 3：定向检查脚本与准备复核

完整包具名检查：正常导入/认证；resources.scope/registration替换foreign模块、模块子类/属性trap；依赖认证函数绑定替换、同函数code/defaults/kwdefaults变动；模块字典碰撞非str/str子类key。armed期间未知callback必须0；finally恢复元数据后原认证通过。不执行坏target来制造旧版失败，不重跑旧绿或F001。

单列纯工厂模型：两callee各一次/返回值忽略/第一callee异常原对象透传、第一callee合法返回后改变第二callee须在未知第二callee前拒绝；原kwdefaults值None/False内容变动与collision key检测。模型不冒认完整包/native/实际owner。

脚本/目录/准备、实际记录均Root与同一独立代理复核。准备必须封存完整输入副本、精确hash、所有新隔离根及原保护；新claim只消费一次，超时/失败保留红，只修新缺口并定向复检失败ID。

## Task 4：交付与后续

核RESULTS/CASE/streams、每项导入reno实际源码hash和callback计数；区分完整包与模型，记录wait/timeout/kill。封存CURRENT/NEXT/DELIVERY/FINAL，主方案和AGENTS追加下一节并保存全文可逆文档备份。

本批只关闭两个具名依赖的precall缺口；scope/control内部传递动态目录、Task9A真实多root/原first与tb/清理、Task9B完整物理费用债务/guard消费者/两阶段terminal/容量durable恢复、原111/94/r919/真实Store写权及完整Goal仍未完成。F001仍20.016秒超时且阶段未知；不将本修订称为超时根因修复。

回退：候选未安装，保留所有原稿/红审/失败记录；后继候选从完整原parent另建版本，不覆盖已封存输入。
