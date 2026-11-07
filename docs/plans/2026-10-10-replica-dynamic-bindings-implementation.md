# Replica dynamic bindings and actual collection Implementation Plan

**Goal:** 完成§17.226下一步的有限可变绑定核对，验证真正原12项collection/dispatch接线，继续完整目录93与产品方案；收集成功不能替代case运行成功。

**Architecture:** 在独立r767包实现C descriptor槽快照，以对象identity核默认值、keyword映射与cell当前值。原r766/103、r761/1027和所有before/旧红保留。使用新独立监督collect-only probe走真正原模块与LongIds；不复制或伪造固定12 IDs通过合成选集。完整native运行保持后续独立门。

**Tech Stack:** 固定CPython3.12.10 / PowerShell7 / pytest9.1.1 / pluggy1.6.0；原监督20/25/10与matrix1680秒不扩。

---

已比较：有限slot identity snapshot可检测默认值或cell被替换及kw map增删；deep value graph会扩大信任面、读取更多对象并影响生命周期，本轮不采用。当前采用前者，必须明确值对象内部mutation及两门之间变更后恢复不在证明范围，不宣称deep closure或持续监控。闭包槽读取是已选择函数的绑定核对，不读取frame、运行调用args/locals或产品first/owner。

包：`data/t16-h02-replica-dynamic-bindings-r767/`。Root独占freeze、实际运行、文档应用。workers只写自己候选，不执行或import候选，不改原canonical/数据库/服务/dist/ACL/Codex设置/模型平台，不commit/push；彼此不回退改动。修改已交稿前保存真实完整before。

### Task 1: 有限函数槽快照与纯回归

- 新建 `slot-candidates/function_slot_snapshot.py`，`test-candidates/test_function_slot_snapshot.py`。
- API在接线前固定：`capture(function)`返回私有快照；`unchanged(snapshot)`返回exact bool或固定类型拒绝，不调用输入的eq/repr/bool/hash。
- 使用捕获的原FunctionType/CellType C descriptors；function/type/tuple/dict/cell/key均exact type先验。
- 保存code/defaults/kwdefaults/closure容器identity；defaults tuple元素identity；kw map先有界C keys/items遍历确认每个key为exact str，再以已验证pairs构造私有map并copy，核key集合及value identity；closure exactcells当前value identity或empty marker。空cell→有值、值→空均检出。
- 快照保持强引用的成本及边界如实；不授authority，不序列化对象或采帧。只检测所声明两门的当前槽，不读值内部图。
- meaningful纯tests覆盖同dict换值/增删/恢复、dict陌生碰撞key无eq、custommetaclass分类无eq、同cell换值/empty、容器换/原控制异常传播、值内部变更边界。标准库unittest，不import pytest/产品/native。

### Task 2: 独立runner绑定接线

- 新建 `harness-candidates/supervised-runner-collection-probe-draft01.py`，保原r766 79431/83d3完整before。
- 新BATCH仅r767，原LANES九组93、产品/test/native来源及启动/Job/HANDLE effects、budget保持。
- 已源码匹配函数在capture点取槽快照，pre-collection写前复核；不得用指针比较冒称映射/cell值核对。
- 选中自然framework函数closure当前静态来源与runner自己的内部closure明确分层，未锚定者列unknown，不称完整execution closure。

### Task 3: 真正原12项collect-only probe

- 固定probe模式只准原ordinary_matrix_pure的12选择；独立claim/run，不用旧耗尽claim、不重跑旧未改red。
- 原两self/GO/DETACHED/20/25/10和1680 wall保持；添加`--collect-only`，不改变原测试函数/断言/参数顺序。
- 先固定loaded `_pytest.main._main`、`pytest_collection`、`pytest_runtestloop`、`Session.perform_collect`，真正loop HookImpl/dispatcher及`collectonly is True`。不能只凭CLI宣称不执行case。
- 保原相对12实际ID严格门；collection表为actual IDs，不filter/drop/sanitize或fallback。
- 原observer零case时session_return/status_unknown保持。JUnit tests=0、pytest实际return0仅collection完成，不写casepass。
- Parent仅为此明确probe scope处理0case与12collected证据；原runtime effects收尾不变。若必须改变原full-case结果判断，单列代码差异和scope，不称parent逐byte同或完整native通过。

### Task 4: 独审、Root冻结与最小验证

- 独审槽模块/test/接线/新增effects/完整旧before；先Root冻结并执行纯回归。
- 据实际源审及纯证据，Root冻结新完整probe inputs，原r766/103与r761/1027按current/before映射验证，不改旧manifest。
- 此文仅实现计划，未产生实际probe准入。正式Root准入须包含模式、唯一claim、source pins、无case门、所有writers终态/保全策略。

### Task 5: 实际收集、保全与继续full scope

- Root执行一次审定probe，只观察原固定监督状态；不新增profiling/clock/duration/frame/args/locals采样，r730已耗。
- 真正Tool/runner/child/pytest返回与case0/collected12/observerpartial分层；Job/实际进程与writers terminal后完整原bytes/副本/末验保全并结果独审。
- 失败保残件/原首错，不重启未改失败，不扩大wall；根据真实新证据修正确切问题。
- 收集成功后仍推进原matrix/callback/其余93、H01原30秒、H02 source/target/coordinator/restore/consumer、H03–H06/writers、分享/P06/T15/生产云原未完。Goal仅在原完整方案逐项成立时完成。
