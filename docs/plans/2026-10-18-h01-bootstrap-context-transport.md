# 三模块原 context 的解释器持有运输实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 BP001/BP005/BP006 使用工具行之前，持有原构造 context 的实际身份，关闭 r1100 P001 的具名来源缺口；完整认证准入仍另行闭合。

**Architecture:** 在原可信 context RHS 构造处，以匿名 exact tuple 和解释器持有的原 tuple iterator 跨越原模块构造。首轮执行原模块余体，第二轮直接将该原 context 运输到原尾部 scratch；保原全局 STORE 和原尾部检查，不新增 helper、callback 或 publisher。

**Tech Stack:** Python 3.12.10，源级 For/If/Continue/Try/NamedExpr，stdlib bytes/text/JSON/AST 静态核对；候选仍未安装。

---

## 1. 范围与方案选择

源码修订父为 r1100 SOURCE01 的完整159文件，仍是两份源码审各1项阻塞的红稿；最后源码双审零阻塞回退父仍为 r1095 SOURCE03。文档 before 取 r1100 DELIVERY。r1100 的17段引用清理、1449有限slot、五处 S002 原 get/同锁事实/effect 核对均保持。

选择解释器持有的 tuple iterator。仅增加三个外层 For；不引入新函数工厂或可写的 __exit__/__next__ 实现。备选 context manager 需要证明新增 constructor/implicit exit/callback 的原实现及对原模块 STORE 的改写；备选 shape preflight 只能防未知迭代，不能证明 original identity。两者均不作为本次修复。

仅改完整候选中的 control/scope/registration 三文件，其余156文件逐byte继承。catalog 的原 function_row 保持；不重新拍当前函数的 expected，不扩大原可信首次源码导入/工厂构造边界。没有任何新增业务写权或原生操作。

## 2. 实际运输的源码合同

对每个模块，在原 `_AUTH_CATALOG_CONTEXT = RHS` 位置用以下结构替代。`RHS` 是父版原表达式，原工具/owner/primitive 构造顺序保持：

```python
# original prefix, including future imports and trusted tool/owner creation
_tail_context = None  # 实际采用父尾lease的具名第一个context slot
try:
    for _tail_context in (None, (_AUTH_CATALOG_CONTEXT := RHS)):
        if _tail_context is None:
            # 原 context STORE 后至原最终尾lease前的完整module body
            # imports / defs / classes / assignments 均仍在module词法作用域
            ...
            continue
        # 原尾lease的全部其他None预绑，唯独不重绑_tail_context
        try:
            # 删除原首条 `_tail_context = _AUTH_CATALOG_CONTEXT`
            # 用本轮FOR_ITER实际返回的原context执行原preamble与BP
            ...
        finally:
            # 原逐slot逆序清理；原最后 `del _tail_context`
            # 改为保key的None清理，先释放late scratch，后释放early iterator根
            _tail_context = None
finally:
    # outer prebind保证即使RHS/iter构造/首轮body失败也有这个key
    del _tail_context
```

实际目标采用 control `_bpl_c05_0001_context`、scope `_bpl_s08_0001_context`、registration `_bpl_r04_0001_context`，不用新公开别名。父尾lease第一个None预绑搬到 context RHS 前、从原尾lease移除；原首条live读取删除，不能在任何None预绑中覆盖第二轮实际context。

scope 同一个最终lease还含 BP004 之后的 BP005，原第二次live context读取必须改为从持续保留的 `_bpl_s08_0001_context` 运输到该原第二epoch slot。仍保这次实际赋值和后续清理，不因原对象相同合并epoch。control/registration 各一条live context load；scope共两条，修订后尾lease内 `_AUTH_CATALOG_CONTEXT` load 必须为0。

`NamedExpr` 的STORE和外层tuple的第二项使用同一次RHS实际结果；不是STORE后重新LOAD当前global。STORE先于任何原module body及循环业务import，故模块alias可见窗口保持。匿名tuple的建立和For协议只对新建exacttuple/原tuple_iterator发生；无可写callee lookup，也不从可写工具行反推iterator身份。

## 3. 原来源链及边界

原可信构造事件 → 原RHS实际context → 同次alias STORE且值在解释器求值栈 → 新建exacttuple的第二项 → 原tuple_iterator持有 → 第二次FOR_ITER实际返回 → 父尾原context scratch → 原context四项 → 原tools/rows → 原row5 → pin4/pin6及其原tuple/item。

None为literal sentinel，context按原RHS必为新exacttuple且不为None。第一轮只通过identity分支进入原module body，结尾无条件continue；第二轮identity分支不进入原body，走尾lease。条件不调用 __bool__/__eq__。首轮body执行一次；尾lease仅在body正常完成后执行一次，原尾内skip/False/deferred分支保持。其后第三次FOR_ITER自然StopIteration，不能多执行一轮body。

此运输证明原context身份；原owner中的list/dict/slot仍可变，必须保已有fresh内容/阶段/锁检查。四个函数内门仍需要原factory/caller及closure来源组合，不能据本方案宣布整组认证闭合。任意外部并发同时改写所有scratch/primitive/独立证据或解释器的情形仍沿原方案明示边界，不能将这点用作省略业务module alias替换检查的豁免。

## 4. 源级控制流、循环导入与原ABI

静态调查SURVEY01确认三模块尾lease后均无后续代码，原context STORE至尾lease前没有escaping的module-level break/continue/return。实现时再按真实AST核对，不能将函数/类内或原内层loop的控制转移误当外层。

control-first：context alias先STORE，原control body在首轮依原序import scope；scope的原module body与条件tail保持，control已ready阶段仍按原事实；control首轮完成后才执行BP001。

scope-first：scope context alias先STORE、首轮按原序import control；control的首轮和BP001不要求scope未来ready，仍沿原False/deferred；scope返回首轮后执行原BP004及有条件BP005，不将两者拆成不同循环轮次。

registration-first：registration alias先STORE，原lifecycle工具捕获/observer与import scope仍在首轮原位置；control缺helpers阶段保原合法路径，原BP006只在首轮正常完成后按原ready分支运行。三种只是源码时序推导，非已执行导入证明。

For/Try/If不创建Python词法作用域。原def/class仍为module词法子节点，原qualname、参数/posonly/kwonly、freevars/cellvars必须与父供给423 code-shapes逐项比较。原class AST、imports、函数体及原原Call AST词法顺序不变。scope `_bootstrap` 的原alias STORE仍是真实module STORE，不挪入callback/local。新增 Continue 必须只位于新增首轮branch末尾。

## 5. 异常与真实引用清理

原module body抛出时，首轮不执行continue后的tail，For iterator通过解释器异常展开释放，outer finally仅删除预绑context scratch；无异常捕获、重放、吞错、额外bootstrap。原tail抛出时先执行父原逆序清理；第一个context slot最后改None释放late scratch，随后iterator解栈释放early持有，outer del仅删None键。正常路径tail先清scratch，第三次FOR_ITER耗尽释放early tuple/iterator，outer del None键。RHS或新tuple/iterator构造失败也走outer key清理，不调用业务tail。

匿名tuple的原context强根取得早于原body与所有tail scratch，释放晚于tail清理；与后取得的scratch分列，不把早/晚相同identity合为一次引用。首轮scratch为None，不因body含未知actual就把该actual存入新增运输根。原1449 slot中的此三个slot仍只清理一次实际late context引用；outer预绑/None-key最终del单列。

不声称控制解释器所有栈临时或证明所有MemoryError分配点保持：新增3个outertuple及3个tuple_iterator是显式新增的有限分配/隐式协议。本方案不新增explicit Call，不调用close/__exit__或GC；不增加持锁区，五处S002 get/witness/实际effect/alias STORE与锁边界原样保留。原异常first行为仅为上述源级路径论证，实际GC/线程/异常验证仍待完整包QA准入。

## 6. 静态成本与目录

每模块新增一个外层For、一个None identity条件、一个unconditional continue及一个outer finally；每轮取值2次、终止next1次、iter1次。三模块合计新增For3、成功next6、终止next3、iter3。条件每模块2次；不是把原body/tail执行两次。早期匿名tuple持有原context更久，元数据root自身尚有原alias/source/factory等引用，不宣称全产品GC或量化实际峰值。

现有global scratch的原尾key提前可见，首轮之前到原尾本来未绑期间每模块多1个exactstr key；原尾其他841区域合计预绑不追加新命名key。不能将此当同时峰值；扫描成本须按各实际dict-live区间重算。完整七domain循环库存预期598→601，其中For596→599、While2不变；这是静态库存预测，供给时重算。原384个For及105/279/7清理结构与原22357 explicit Call保持，完整目录中的固定列名Call数量另实数核。不得因此称1MiB/时间容量已准入。

## 7. 落地任务与验证

Task1（本设计）：保完整17输入pairs、真实三模块AST调查、Root设计审与同一独立代理设计审；每份报告必须pin确切设计、父SOURCE与调查。设计红项先修稿并保旧版本，不能依材料核同自动清除。

Task2（生成）：在新批次生成完整159候选，三改156继承；保存父/候选/精确逆向159triple及三份全文diff/ndiff。原context语句归一化回Assign，将新外层结构折叠、首轮body摊回、尾live读取与预绑/del恢复后，三个完整module AST与父完全同。这不能单独代源码审。

Task3（静态供给）：158 Python AST，三改源各一次syntax compile以取得所有nested code-shapes，父新增compile0；复审compile0。236原声明/10class、423份参数/closure形状与原22357 Call词法全核同。重算lifetime、EDGE/PHASE/P0–P8与SCAN位置；逐列真实原context链、两轮control flow及actual return/STORE/清理。五处S002与17原lease核对后做两份完整源码审，不只审delta。

Task4（后续）：继续初始化余体、origin/failure-readers、原factory/caller closure与actual return/STORE、全EDGE/PHASE/P0–P8。完整认证组合源码闭合后，才完整包QA脚本及准备双审/新claim；本设计不授权局部bootstrap/control模型或candidate runtime。

## 8. 回退与保持

本方案仅未安装候选；回退为保r1100红稿不修改，或明确引用r1095 SOURCE03零阻塞源码回退父，不能把r1100称已批准父。设计双审0不等于source_gap_closed或execution_admitted。

case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling、frames/locals、停点、定点计时、新异常链诊断与runtime journal。r920/r964耗，不因迟到批准重放；r967待原问题不执行重问；P06历史0/7不再索材料；旧绿/F001不重跑。canonical/c973本段apply0、历史11、产品pass0；正常库/service-dist/ACLsettings/真实平台/Git保持。Task9A9B、原111/94/r919、真实Store写权与完整Goal仍未完。
