# Path 原记录叶函数复用父 Call

2026-10-17，r1017 DESIGN01。依赖已交付 r1016 SOURCE-01；仅准备未安装候选，不改真实库或正式树。

有限静态调查已确认 `_make_path_records.get` 除自身包装，还调用各有独占 Call 包装的 entry/descriptor，一次 get 至少具有三个具名包装点。resources 的 pg/ps/pc/pi 有 327/99/16/16 个句法调用点；这些不是实际执行次数或耗时。六个具名叶函数 entry/descriptor/get/set_/check/is_ 不调用 custody.owner/put/register/select/_select_owner/hold_scope，不负责选择写入分区或创建业务持有者。new 调用 owner/put，必须继续独占。调查全文和两份父源在 data/t16-h01-path-leaf-borrowing-r1017。

三个目录 timeout 的实际停点和根因仍 unknown；本方案减少可确认的重复临时借用，不宣称已找到超时根因，不删 freshness/auth/clock/预算检查。

## 两文件实现

1. custody 的 `_make_entry_gates` 新增私有仅关键字 `_reuse_leaf_parent=False`，默认路径与 r1016 原 enter/leave 完全相同。resources 另在固定创建点调用该 factory(_reuse_leaf_parent=True)，捕获固定 gate 对；其余模块及资源 new 保默认独占 gate。
2. 专用 gate 的 enter 首先执行原完整 implementation check。有 parent 时核同一原 Call/state/membership 与 closed，明确拒绝额外显式 lease，然后对原 subjects 逐项执行 parent.observe，保跨 owner 的原父借用；返回同一 parent、own=False。不创建或归还 child loan，不修改 preferred/selected。parent 为 None 时仍调用原 enter 创建并拥有完整 Call，返回 own=True。没有“未知时补 root”或来源重建。
3. 专用 leave 保原完整 implementation check 和原 primary 传播，然后调用原 leave。own=False 只结束叶函数借用，绝不关闭父 Call；own=True 正常完整关闭自己的 Call，保 r1016 成功清理及全部失败证据。
4. resources 只改六个列名叶函数的 ENTER/LEAVE 对，主体句法与参数/返回/记录/descriptor 身份保持。new 及其他全部操作仍独占。set_ 只更新既有记录及原 descriptor 镜像，不进行 custody 分区选择；名称不把它误称为只读。
5. 新 gate 的函数 code/default/kwdefault/closure 与固定参数必须进入原资源函数 pins；新增模块绑定进入原完整 bindings。不能仅保存可替换 gate 的 facade 身份，不能为节省调用跳过原表 lookup/镜像/原状态校验。其余九完整文件字节继承，保十一完整候选、父源、逆向、精确差异。

## 语义证明与错误边界

原子叶函数不读取 preferred/selected 来选择业务写入分区，故父 preferred=A、当前记录属于 B 时仍查同一原表/同一记录。observe(B) 延长 B 的父借用，与旧 child.keep→parent.keep 的父效果一致；只省去已被父持有的临时 child ticket。多个 subject 属于不同 owner 也不在叶层新选写来源。原业务入口和 new 的独占选择保持。

观察失败时父已获取的借用继续由父的完整 finally 归还；不在叶失败时清父账，不撤销原父 pin，也不把 first 换成清理错误。错误 traceback 继续保原异常，不检查 frames/locals。无 parent 的原调用仍独占，closed/损坏/伪 Call 拒绝；额外 lease 必须在变更父账前拒绝。部分归还中断仍仅静态边界。

## 新检查与执行门

先双审设计、完整源码、六最终脚本和实际准备，再新 claim 执行。保 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest child，本批 max1；保原 Popen 返回对象、kill/wait/finally 和 raw-first 失败保全。不能拿脚本时间门替代可抢占上限。

新增具名普通模型验证：同父 own=False/原贷款集合和 preferred/selected、无父 own=True、父A访问B和多来源保持父选择、显式 lease/closed/损坏拒绝、活动 primary 不被借用退出覆盖、真正原 six leaf/new 的分配与镜像/记录身份、new 仍独占、固定 gate 的 code/default/kwdefault/closure/导出篡改拒绝。只做调用语义和记录断言，不做 profiling 或定点计时诊断。

父 r1016 的 32 项按 runtime gate 与真实 selected-source 风险复核选择有意义回归，不机械全量重跑。三个原目录 I003/I004/I009 可作为新组合的目标检查，保原测试体与原 30 秒上限；只运行一次新版本，不因失败提高限额或重试同版。全部通过也仅为合成 IO/native/EX/clock 的实际选取源码模型，不是产品、原111/94/r919、完整物理资源或 Store 写权认证。

本方案不授 route、最后业务释放或 pending 恢复；_finish_operation 继续硬拒。正常库、服务/dist、既有 ACL/settings、真实平台及 Git 保持；r920/r964耗不重复，r967待原问不执行不重问，P06历史0/7不再问。完整 Goal 仍 active 未完。

## 修订02：专用 gate 在实际 leaf 入口先认证

DESIGN01 的 D001 成立：六 leaf 本来不执行 resources._authenticate，原 custody check 只核自身模块；仅增加资源 pins 库存不能保证新 gate 被篡改后在第一次 observe/set_ 之前拒绝。原稿与独审发现保留，不按旧稿实施。

在 _make_path_records 固定创建时，原 factory 创建专用 gate 对；用原 _GET 从原 enter 的原 closure 按原 co_freevars 中的 check 名取得同一内部 check 函数。一次捕获 enter/leave/check 三函数的完整 function/code/default/kwdefault 及内容/closure/cell 与内容 pins，强持对象只限这些静态函数和静态数据，不捕获操作。pins 的具体 tuple 布局依父 control._function_pin 原实现固定；不提供调用者可选名称、重封 pin 或运行时再认原件。

实际调用也必须使用 factory 闭包里捕获的同一原 enter/leave；模块导出仅作额外镜像校验，不在核验后重新从模块导出取 callee。这样可替换导出不能在检查与使用之间改变所选函数。默认 False 路径与无 parent 路径保原调用；专用有 parent 路径顺序固定为原 custody implementation check→原 parent state/membership/closed→额外 lease 拒绝→原 subjects 逐个 observe。

六个 leaf 在调用专用 enter 前，使用原 _GET、type 和原字典读取以内联代码逐项核这三份 pins，以及两个模块导出仍指向原 gate；空 cell 同样拒绝。不能先执行待认证 gate、其 check 或新增可写 helper，再来验证它。无 parent 时也先认证再走原独占 fallback。所有核验成功以后才调用同一原 enter，对 parent.observe 或 set_ 镜像写入之前完成。leave 仍保原 gate check；叶函数最外层退出之前需再次用同一原 pins 检查 leave/check，不能在父已借用后悄悄接受被替换的退出函数。

这几段有限内联核验允许机械生成完整来源及差异，不增加每次 leaf 的全资源 _authenticate，也不缓存或豁免任何原 fresh/auth/clock/预算检查。原 _PATH_RECORD_FUNCTIONS 的每个 leaf 自身及新捕获的 pins 继续由原资源完整函数目录/闭包门认证；本修订只解决新 gate 的前置校验时序，不宣称全部 globals、所有私有直接调用或同时篡改原 leaf 与其全部锚均已认证。

对应篡改模型必须从真正原 leaf 入口调用：逐一改 gate code/default/kwdefault/closure/cell/导出或内部 check 的 code/cell，确认在实际 parent.observe/既有原记录镜像发生变化前拒绝；不能只直接调用事后 resources._authenticate。观测限定父原贷款集合/原记录身份及镜像等普通模型断言，不增加 profiling、frames/locals、定点计时或执行停点诊断。若无法用这条实际前置链闭合，保留设计阻断，不生成或运行候选。

finally 中的前置退出认证失败也遵守原 leave 的 primary 规则：先由捕获的原 sys.exception 取得实际活动异常；认证或退出失败且已有 primary 时继续传播同一原 primary，无 primary 时传播认证/退出错误。不为取得归还回执调用被改 gate，不补成功、不清未知借用；父 own=False 的归还仍由父完整 finally 负责。此分支须有实际 leaf 入口的具名检查。
