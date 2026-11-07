# r1142：合法原slot实参域与首对象操作取引用

r1142选择在合法原Py_mod_exec实参有效/存活、同解释器GIL和匹配full API引用包络的显式外部前提下，先仅初始化scalar/NULL自动槽，再以actual module实参直接作首对象操作取得creation_module_guard，之后才访问state、分配type/Factory。该有限路线不借未来Factory/type根，不把pointer/type/refcount检查当来源证明；创建期maylast/actual-first及r1141未move/已move责任保持。完整wrapper/caller/build、全state/NULL/N4仍未闭；B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

八完整tagged v3.12.10源487320B/12直接区间/两继承引用区间分别核对，同一module运输不等于持续owning root或完整wrapper证明。Python/clinic/import.c.h一次RemoteDisconnected/0retry/目标缺保持终态；不由Clinic声明或impl补成完整生成wrapper。首次acquire前加入GetState/对象检查/分配/DECREF/等待或其它callout须重证前置根；入口有效不外推后继独立caller强根。原>=3.11、引用与预算边界保持。

本批新增产品candidate/产品compile/import/运行/native/模型/QA/诊断0，下载1失败0重试（此前调查，本次准备无下载），apply0/历史11、源码缺口关闭0、产品pass0。159源/保护56同18缺/原限额及禁项保持；完整Goal仍有工程，非只剩验收。

# ADR-r1142：合法原slot实参域与首对象操作取引用

状态：有限子设计待正式双审，无产品源码或执行准入。原提议、口头预审、STAGED-SLOT-ROOT-01及失败下载记录均保留原字节；本ADR选择有显式合法接口前提的首对象操作取引用路线，不关闭完整wrapper/caller/build或修改其False。

## 1. 把接口前提与整条解释器实现证明分开

原 Py_mod_exec 是接受 `PyObject *module` 的 C slot。与任何访问 PyObject 的 C API 一样，进入其受支持调用域的前提是 module 实参为有效、仍存活的对象，调用者按借用参数规则保持其有效性。这个前提来自合法原初始化协议及匹配构建，不由 slot 内读取一个 type/refcount/current alias 来自证。slot 内任何类型检查或 GetState 都已经要求实参有效；增加检查不能修复无效指针。

选择：沿原可信 native 加载和实际 exec slot，在合法实参域、同解释器 GIL 及匹配 r1138 full API 引用包络下，把取得 creation_module_guard 安排为本方 slot body 的第一个对象操作。此前仅允许 C scalar/自动槽 NULL 初始化；不调用 state accessor，不分配对象，不释放 Python 引用，不先查 module/type 属性或可变公开别名。

本次 actual module 指针直接作为匹配 `_Py_NewRef`/Py_NewRef 的原操作数。r1138已冻结的包络里，引用取得只作原有效对象的 raw 引用操作及已列 scalar 统计；从合法 entry 到这一笔 acquire 没有未覆盖的 callout。这是一段具体的有限直达路线，不把“持 GIL”当成所有 C API 都不会 callback 的理由。debug/limited/其它版本、外部函数、不同 interpreter 或未证明的实际 build 不套此结论。

## 2. 新取得的责任与原身份分别运输

成功取得后，creation_module_guard 独立拥有该原实参对象，按 r1141 跨 state accessor、type/Factory 分配和准备回调；任何 state/phase/slot 的认可仍须回调后重核。原实参值与 guard 的相等来自这次直接实际操作数，不是后来从 public module/type 字段比较出来。

这里不借未来 Factory 字段、type.ht_module 或尚未返回的实例证明先前存活，也不从“module 类型正确”推导其为本方 module。原 slot 静态安装、原 native binary/加载与 N0 外部可信起点仍须完整源码/构建路线。正常 reload 的原上层 skip、显式再次 exec 的既存 phase 处理不变，不重新捕获 expected。

原 first/partial 及创建期 maylast 责任先全部保留；本提议不以合法 entry 假设宣告所有准备失败出口均有独立 caller 强根，因此不把 guard 最后释放改为统一 nonlast。成功 move 入已 Init/untracked Factory 后，自动槽置 NULL，Factory partial/dealloc 才拥有那一笔；未 move 仍由自动槽结清。逐调用 Factory 字段链与此创建期来源仍是两层。

## 3. 证据限制

八份 tagged v3.12.10 完整源、12段实际路径与两段继承引用实现支撑有限路径调查。`Python/clinic/import.c.h` 本次下载失败已终态；不能将 Clinic 输入和 impl body 冒认为完整生成 wrapper 源。若完整方案要求穷尽该 wrapper 的实际操作、参数转换或 build 分派，该义务继续开放，本提议不关闭它。

本ADR仅在原native slot合法C API实参域由明确外部加载前提给定时，选择first-operation raw acquire作为本方创建端的有限路线；不宣称全部解释器或外部C调用者已经证明。完整实施须明确满足这些接口前提与实际build；不满足时不得套用此路线，也不能以pointer检查补造前提。

尚无 future slot、接线或构建；完整 caller/参数/全 state/N4/NULL/COMPAT/预算未闭。B001/B002 open2，full_design_approved/source_approved/execution_admitted/complete_Goal 均 False。无产品 compile/import/运行、native build/load、模型、QA、诊断、真实库或权限操作。

# 下一步：全字段拥有表与初始化清理组合

将本批显式合法entry条件接回原native实际加载/slot静态安装与匹配build；完整wrapper/caller义务仍列open，不再重试失败下载或把接口前提改写成已核事实。固定首对象操作前只留scalar/NULL自动槽初始化，后继任何callout前已经有creation_module_guard；创建期maylast/first不因该有限入口而被降级。

继续两类module guard、factory_type_local、Factory.module_lifetime、state独立type/Factory边、Consumer/source/A/J/Product/L/owner14/bank9的逐acquire→持续持有→move/detach→release/partial表；m_clear与未先clear的m_free须独立到达先退休/全部state边detach再释放。原metadata/实际参数/P0/38context/CORE/IMPORT、D1/D2、全部SCC/一般NULL/actual-first/N4/COMPAT和>=3.11/ABI/build/预算未闭。

完整设计双审前不生成产品候选，源码准入前不产品compile/import/运行、native build/load、模型、旧绿F001、QA或诊断。仅复用同一只读独审代理，保留未知dirty；不改真实库、服务/dist、ACL/settings、真实平台或全局运行机制，不commit/push/reset/clean。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持，control1MiB非已确认native heap限额。r920/r964耗不重放、r967不执行不重问；P06历史0/7无材料不再索取，Goal active。
