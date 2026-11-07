# Task9A legacy调用者差分矩阵 Implementation Plan

> For Codex: 沿用 executing-plans，复用同一名只读独立复审代理。用户已授权继续开发，不新增代理、不重复索权；dirty树保持，不作Git操作。

**Goal:** 补充尚未实际执行的组与guard-plan调用者兼容性矩阵，以接线前原函数为对照，验证legacy查找、迭代、内容拒绝、原filesystem异常映射和调用者finally合同。

**Architecture:** 不改产品。冻结r1070 SOURCE01所指完整lifecycle与r1062 SOURCE04的完整parent lifecycle，读取两份完整文件后只编译具名原定义。对照原组/guard函数与当前完整marker消费者工厂返回的原函数，使用同一组显式依赖adapter；两侧均执行原StorageLifecycleError/_fail/_filesystem。每项分别覆盖bootstrap资源未发布与已绑定资源明确返回legacy None两种状态，比较完整业务事件及实际返回/异常。

**Tech Stack:** Python3.12、stdlib AST/JSON/SHA256、原函数体与完整工厂、显式合成custody/目录/carrier/OS adapter、具名新进程监督器。无候选完整import、真实scope/账本/资源IO/native/EX/SQL/Store。

---

## Task 1：完整来源与设计双审

Create `data/t16-h01-marker-legacy-caller-differential-r1071/`。保存两份完整lifecycle、r1070 SOURCE01/DELIVERY01/FINAL、r1062 SOURCE04及QA-CATALOG/QA-RESULTS和本方案的完整副本及bytes/SHA。核当前158候选逐一仍同SOURCE01，保护74项仍56同18缺；不复制或安装新产品组合。

原有G001只证file_verify模型，不重跑。新增仅组与guard两调用者。原FileLock/原native F001和原tb清理覆盖继续待办，本批不缩短/代替它们。

Root与同一独立代理先复核本设计，0阻塞后才准备可执行脚本。

## Task 2：固定31项双状态差分

Create `checks_01.py`、`CATALOG-01.json`。每个ID一个新子进程，内部四次独立环境：接线前/当前各bootstrap与bound-legacy，合计两对差分。不得复用带可变状态的adapter、generator、异常容器或事件list；每对相同注入异常对象允许用于身份断言。每个环境单次实际调用，不重试。

原定义取完整AST节点，不擦除或重写函数体/装饰器，不抽掉marker pin前验。当前完整`_make_marker_consumers_original`创建原三callee及bind；接线前取top-level原组/guard，另原`_original_lock_dispatch`/`_original_bound_method`两侧AST必须一致。原异常类、_fail、_filesystem两侧AST必须一致。只选固定静态名称，不读frame、locals、运行栈、traceback、cause/context链或任意闭包图。

| 类别 | ID | 固定动作与独立oracle |
|---|---|---|
| legacy fallback | L001–L008 | tuple/list三custom lock；可重复iterable；single-use iterator；custom verifier原异常；iter入口原异常；next原异常；verify属性读取原异常。使用真正原`_original_lock_dispatch`与`_original_bound_method`；断言逐次iter/next/yield/verify及一次custody leave。单次迭代器原先被dispatch消耗首项的行为保持，不把旧行为“修好”或称每锁均验证。 |
| 组plan legacy | P001–P012 | 三个有名字的合成lock/directory，独立pre/post-entry与dir-plan事件。success；首/中marker坏值；seek OSError；read FileNotFound/SyncError/StorageLifecycleError/RuntimeError；中间post-entry失败；尾dir-plan失败；directory plan None回退；正常body后leave失败。选择`original=True`的dispatcher、原plan/carrier/目录与OS依赖显式adapter，不能把这组称真实原dispatch/目录/多root来源认证。 |
| guard-plan legacy | Q001–Q011 | 原guard caller函数执行。success；中marker坏值；seek OSError；read FileNotFound/SyncError/StorageLifecycleError/RuntimeError；context拒绝；database拒绝；state拒绝；中间post-entry拒绝。plan是显式合成slots fixture，context/shape/carrier为adapter；用捕获fileno依赖adapter每锁一次，后seek/read必须同一fd，动态fileno getter0，不能称真实C描述符/guard准入。 |

每项必须有基于原合同写定的完整expected event list，不能只检查两版相等。实际异常需核确切类型/code/status；原透传对象以is核同。原映射使用原filesystem函数，不手抄一个新contextmanager。两版原异常类各自构造，不能把不同类的对象误称同原类；同注入异常对象若本身是各环境的原StorageLifecycleError，应分别创建并核本环境is保持。

bound legacy额外marker事件单独收集：group只对exact tuple/list逐锁begin，早于preflight；未知iterable不额外遍历。guard只对exact fixture plan的原locks slot tuple逐锁begin，早于context；每次begin明确返回None，不调用marker read/body/failed。资源_authenticate等checker调用可单列，但业务事件比较不丢任何原preflight/plan/verify/lookup/IO/leave事件。

失败后不得为取事件继续调用产品。已发生前缀必须精确，未读后项与后验必须缺席；finally leave每环境恰好一次，leave本身抛错仍只一次。本批不声称原资源close/Token reset、原first/原tb或未决grant已核定。

## Task 3：脚本、实际准备和唯一执行

沿用r1070监督器逻辑生成本目录`supervise_01.py`、`prepare_01.py`、`admit_01.py`；31项并发1，新空runtime根、最小env、`-I -B -X utf8`，case30/parent360/matrix1680/cleanup10/control1MiB/最多2pytest children保持（本批0pytest）。wait后1MiB检查仍分列非流式，不扩限；kill后必须wait，未确认退出不启后项。

脚本双审与实际准备双审0后，依次运行：

```text
.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-marker-legacy-caller-differential-r1071/prepare_01.py
.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-marker-legacy-caller-differential-r1071/admit_01.py
.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-marker-legacy-caller-differential-r1071/supervise_01.py
```

准入与claim使用xb，本版只消费一个新claim。失败原记录保全，不重跑未改版或已绿项；只按新证据修订必要缺口，不把fixture失败误归产品。

## Task 4：实际记录双审与交付

Root与同一代理核RESULTS/CASE/streams、完整来源/AST选择、expected事件/差分和边界，保存精确pin。交付CURRENT/NEXT/DELIVERY/FINAL，主方案追加§17.449、更新最新摘要并同步AGENTS，保完整可逆文档备份。

无产品文件变更、canonical/c973本段apply0/历史11、产品pass0。通过只能补充31具名模型/两状态差分，不代替完整模块、原scope/费用债务、真实目录/多root/native/原tb清理。r1068 F00120.016秒超时/阶段未知仍保，不重跑、缩减、扩限或添加诊断。Task9B、原111/94/r919/真实Store写权和完整Goal仍active未完成。

正常库/service-dist/ACLsettings/真实平台/Git保持；禁profiling、frames/locals、停点、定点计时、新异常链诊断。r920/r964单次耗不重放；r967待原问题不执行/重问；P06历史0/7不再索材料。回滚为不安装候选、保所有原红及记录。
