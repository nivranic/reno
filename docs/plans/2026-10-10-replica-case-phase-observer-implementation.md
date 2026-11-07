# Replica case phase observer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现具名 case 与最后已报告 pytest 阶段的有限观察输出，以定位原矩阵聚合超时；保留全 93 项及完整产品目标。

**Architecture:** 原冻结 runner、测试和产品源码保持，后继候选新增一个独立协议/有限写入模块及三个普通 pytest hooks。实际 pytest 返回、observer 失败、监督终态分别记录。观察日志只声明 protocol/报告边界，不证明正文位置、产品关闭或资源权。

**Tech Stack:** CPython 3.12.10，现有 pytest/pluggy，JSON/JSONL，现有 Windows 原 Popen HANDLE/Job 监督。

---

依据 `2026-10-10-replica-case-phase-diagnostic-design.md` 及主方案 §17.225 执行。r765 两 finding 已在设计层关闭；框架来源和 loaded bindings、pre-case gate、写入/异常隔离、freeze、Root 准入和终态保全仍需证明。本次实现候选，不自动重跑原 run-03 或启动新原生组。

候选包：`data/t16-h02-replica-directory-case-phase-r766/`。Root 独占最终冻结、正式运行和文档应用。Worker 分别拥有 observer/test、runner 候选和框架来源清单；互不修改其他人的文件。原 1027 来源与八份 before 映射、原 red/claims 保持。修改候选前保存已交付稿的真实 before；没有事前副本的草稿只能如实标注。

### Task 1: 定向框架来源和接入边界

**Files:**
- Create: `data/t16-h02-replica-directory-case-phase-r766/framework-candidates/framework-source-proposal.json`
- Read: 原 r761 runner、pytest/pluggy 的配置、protocol、report、hook 分发和默认插件源码。

1. 从原 `pytest.main` 调用、默认插件名单及三个 hook 的实际实现列出需要冻结的具体文件。
2. 区分最低语义清单、静态 import 依赖、实际加载文件与类/函数绑定；前两项不能冒称完整执行闭集。
3. 明确需要捕获的 `pytest.main`、原 TestReport 类型/C 字典 descriptor、hook dispatcher 和配置/插件对象边界。不访问 frame，不采时间/耗时。
4. 只读提案提交独审。未运行 pytest/产品，也不能以磁盘版本号证明 loaded bindings。

### Task 2: 实现有限协议和写入候选

**Files:**
- Create: `data/t16-h02-replica-directory-case-phase-r766/observer-candidates/case_phase_observer.py`
- Create: `data/t16-h02-replica-directory-case-phase-r766/test-candidates/test_case_phase_observer.py`

统一接口在编码前由 Root 和两个 worker 固定，避免 runner 与 observer 各自发明合同。实现需覆盖：

- 固定原矩阵 12 个具名 literal/顺序及 native/pure 分类；原/最终实际 nodeid 原样通过相对闭集验证，禁止删改、过滤或 fallback。
- 原 run_nonce 为 32 小写 hex；每 nodeid 4096 UTF-8 bytes、collection 131072 bytes/12 rows、事件每行含 LF 1024 bytes、61 行/62464 bytes、状态 1024 bytes。
- 严格 exact type、固定字段/enum/int，写前 serialized UTF-8 计费/预留；短写残件保留、预留不退、不补写重试。
- `case_start`、`phase_report`、`case_end`、`session_return` 的顺序及中断/不完整状态；实际 pytest.main 提前返回时不能补造 case_end 或完整成功。
- recorder 自身 OSError/短写 sticky-disable；不能把真实 KeyboardInterrupt/SystemExit 转为成功或吞掉。元数据代码不读取产品 first/账本/inspection API。
- collection/gate 错误在 case 前独立失败；实际 pytest 返回沿真实路径记录，不能生成虚构码。

测试应检查非法绝对/UNC/drive/参数路径、超限 UTF-8/escaped JSON、乱序/重复、setup skip/failure、缺报告/提前返回、short write/OSError、KI/SystemExit 原实例传播，以及不可信值的 repr/eq/bool hook 零调用。先审 test/source，再由 Root 冻结一次有限纯检查的实际输入并执行。不得使用 native 或原产品 fixture。

### Task 3: 三个 hooks 接入后继 runner

**Files:**
- Create: `data/t16-h02-replica-directory-case-phase-r766/harness-candidates/supervised-runner-case-phase-draft01.py`
- Preserve: 原 r761 runner 完整 bytes。

1. 仅机械重绑新批次及导入已绑定 observer 候选，原 HWND/Popen/Job identity、exact singleton、两轮 self/GO、DETACHED flags 与 20/25/10 不动。
2. 原矩阵 selectors/12/1680 保持；测试/产品源码、原 native 预算与参数顺序不动。
3. 在原 collection 读取/缩名输出前后实施 observer 验证门；非法输入在 case 前停止，实际异常/pytest 返回不能由 observer 假造。
4. 只接入 `logstart(nodeid)`、`logreport(report)`、`logfinish(nodeid)`，report 投影只用原绑定类型及 C 字典 descriptor/exact strings，禁止属性 bool、longrepr/异常/frame。
5. 固定 sink 预先打开，logger failure 与 pytest 返回分层；真实 sink close/failure 要单独记录，进程强停不冒称正常关闭。
6. 新增输出 IO/effect sites 明确列出，不声称效果等价；old runner 的无关定义和既有 native/effect 顺序需审计保持。

### Task 4: 独审和最小必要验证

**Files:**
- Create: `data/t16-h02-replica-directory-case-phase-r766/review-candidates/*`
- Create: Root 的纯检查冻结、运行/返回及结果记录。

1. 独审 source/test/runner/框架声明，修复真实 finding 后保 before 再复核。
2. Root 执行审定纯检查，建议命令形态 `.venv/Scripts/python.exe -I -B <Root 审定 standalone pure runner>`。具体脚本与 hash 未产生前不预填完成或准入。
3. 纯检查成功只证明协议/故障模型；不证明真实 pytest 插件加载、Windows 原生关闭或全目录组成功。
4. 不为未修改的失败原 native run 续 claim/扩时或补绿。

### Task 5: 后继实际绑定、冻结和运行门

在 Task 1–4 的实际证据足够后，Root 才设计实际 loaded-binding 核对及新一次准入。不得在纯测试中偷偷导入原产品/创建原生资源；不得以新 metadata 增加 profiling。新诊断运行结束后，仍按原 Job/进程/全部 writers 的真实终态完整 raw 保全、独审，并继续原 93 项/S2–S5，不能仅交付纯 2 项当目标完成。

### Delivery and rollback

交付写明实际执行的静态/纯/框架/原生验证边界。候选保持项目现有 canonical 源码和原 manifest；拒绝新候选时保留它、原结果和 before，不回滚他人脏改动，不修改旧 ACL/Codex 设置或正常库/服务/dist。本任务不 commit/push，goal 不因一个子模块通过而完成。
