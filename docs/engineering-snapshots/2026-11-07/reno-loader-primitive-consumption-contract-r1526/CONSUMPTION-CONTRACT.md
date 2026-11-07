# r1526：body primitive 的引用消费和异常交接合同

本稿承接 r1525 的具体缺口，核对已有本地生成 handler、error API、引用释放及现有 owner cleanup。仅为有限源码事实与后继设计合同，不是完整 V3、body、源码或执行准入。当前完整候选仍是 r1524/working-01：22源 / 2,058,033 B；FINAL 13801 B / 7f0c85b54d9b9b055ee5cec3f515880a43eb61f1f632d3c5dedee2dcfa81876e，最终独立 c6e73f。r1525 FINAL 2616 B / 03857ec72f39bb256d0c7430a9dd1cd7e0b605926fd1e0b356beeffaede27423，最终独立3567b2。

## 新取得的本地证据与范围

ccc1a2 从项目 r1108/CPYTHON-SOURCES-02.json 得到 generated_cases.c.h 的明确索引。其项目相对路径为 data/t16-h01-combined-admission-design-r1108/cpython-v3.12.10/Python/generated_cases.c.h，195111 B / 579706b9011997eed918fb546bd9382e8416d8cba3f9e87282aa5fa472c08b29；d6a905核身份并读取本稿所列handler。r1525/b24afd只证明它不在当时猜测的r1112目录，不能据此说全部本地缺失；历史封存不回写。

1ff4b9由r1112已有清单找到 object.h/errors.c 明确身份；6582bd读引用宏、_Py_Dealloc及当前ceval错误出口。ccc1a2读take_failure/cleanup实际正文；3468b6读r1250 ERROR-CONTRACT、CURRENT和ROOT-REVIEW。各实际输入路径和身份固定在 INPUT-IDENTITIES.json。

8ed44c中一次rg读取猜测的 ADR-PRIMITIVES-01.md 报路径缺席；该次exec整体exit0，不能写成整次exit1。随后按已列文件名读取真实 ERROR-CONTRACT-01.json、CURRENT-STATUS-01.md 和 ROOT-REVIEW-01.md，3468b6通过。没有源码/工具执行或产物变更。

这里取得的是既有 CPython tag 文本的生成 handler 展开，未预处理、编译或运行它，也未运行 Python 生成器。它能确定所列文本中的输入释放方向、条件和错误跳转；不能单独证明目标build采用同一宏/ABI，也不证明原 exec_module 的实际opcode、oparg、专门化或监控路径。r1525中相应未核项因此取得了更强的局部证据，没有变成全运行闭合。

## 所列 handler 的完整消费边界

| handler及站点 | 实际正文顺序 | 已消费与仍需运输的内容 |
| --- | --- | --- |
| FORMAT_VALUE:4593，存在转换 | conv_fn(value)；Py_DECREF(value)；若result NULL则Py_XDECREF(fmt_spec)并调整栈后去pop_1_error；否则value=result | 转换输入已归还，不能因result NULL再在外层释放同一 occurrence；成功的转换结果成为下一format输入。 |
| FORMAT_VALUE，format段 | PyObject_Format(value,fmt_spec)；Py_DECREF(value)；Py_XDECREF(fmt_spec)；仅随后检查result NULL | 无论format结果如何，这两笔输入在返回分类前已消费。没有fmt_spec时NULL不构成引用债。 |
| BUILD_STRING:2021 | _PyUnicode_JoinArray(empty,pieces,oparg)；for(int _i=oparg;--_i>=0;) Py_DECREF(pieces[_i])；然后检查str NULL | pieces按下标从高到低消费。对未来三片段consumer才对应suffix→formatted→prefix；本稿不以AST代替真实oparg=3证明。 |
| LOAD_ATTR:2421，method命中 | _PyObject_GetMethod(owner,name,&meth)为真；res2=meth，res=owner | owner occurrence转移到真实self槽，不应额外释放或按相等地址去重。 |
| LOAD_ATTR，method未命中 | 上述函数返回假后Py_DECREF(owner)；然后检查meth NULL；成功时NULL前导槽和meth | 即使属性失败，owner也已归还；NULL前导槽不是Py_None或一笔owning。 |
| LOAD_ATTR，classic | PyObject_GetAttr(owner,name)；Py_DECREF(owner)；然后检查res NULL | 输入owner先消费，再由res分类。不能把保活guard自动延长到所有后继调用。 |
| POP_TOP:252 | Py_DECREF(value)；STACK_SHRINK(1)；DISPATCH | helper的实际返回值被丢弃；没有该正文新增的结果/indicator复核。完整native合同不能凭方便加入第二次call或默认成功。 |
| POP_JUMP_IF_NONE:3107 | None分支跳转而不DECREF；非None分支DECREF；随后缩栈 | 这是所读3.12 handler正文，不能把所有NULL/None或所有分支统一成“总要释放一次”。immortal/source/build前提另列。 |
| POP_JUMP_IF_NOT_NONE:3093 | 非None分支DECREF后跳转；None分支不DECREF；随后缩栈 | 同上；并非证明目标函数实际选此opcode。 |

FORMAT_VALUE跳到pop_1_error，而ceval:924的该label只有 STACK_SHRINK(1)，不会再次DECREF已消费的value。BUILD_STRING在str NULL时先缩oparg再去error，避免exception_unwind把同一批pieces再消费。LOAD_ATTR错误也走pop_1_error。生成handler的这些缩栈事实是明确的引用消费证据，不能用一个“失败后清全部旧输入”的通用close替代。

ceval:error仍有NDEBUG下的“error return without exception set”SystemError与debug assert区别，随后traceback/monitor及exception_unwind。无handler时后者从栈顶逐项POP并Py_XDECREF。它不是完整native body的现成cleanup：F4删除是已选delta，不能为复刻旧label恢复F4 frame/PC、traceback或原CALL controller；真实child evaluator自己的错误出口保持独立。

## pending indicator 和 private first 是两个真实持有位置

errors.c:498–509 中 GetRaisedException把current_exception MOVE到返回值并清槽；不是额外NewRef。errors.c:31–36 的 SetRaisedException先把incoming放入current_exception，再Py_XDECREF旧异常。安装/取回的次序会改变相关释放可见的pending；不能把Get/Set视为无效果的存取。

owner:875 take_failure会在无indicator时构造“owner factory preparation failed”RuntimeError，然后GetRaisedException。它不是“仅取现有失败”的纯primitive。owner:1187 reno_cleanup_release_owned先settle_indicator、detach并NULL源槽、Py_XDECREF，再settle_indicator；已有first时可能经begin_empty暂时安装独立duplicate first再取回。

因此：

1. primitive原始效果跨度内保持同次实际pending，依上述原序消费其输入，再分类原result。不能在这些规定的释放之前调用take_failure或cleanup_release_owned，随后宣称异常可见性等价。
2. 这不是已运行的行为反例。_Py_Dealloc的debug正文明确要求deallocator不改变当前异常身份；这个约束并不证明它不能读取pending，也不冻结异常属性。非debug正文直接调用tp_dealloc，目标build/合法域仍须绑定；本稿不把任意违反C API合同的deallocator列作普通合法域。
3. r1250选择的duplicate-first门只解决已有first且后继清理站需要空indicator时的有限责任。它允许E2析构观察临时pending first，并明确列COMPAT及匹配pending/reference前提。它不把原primitive的pending释放改成空indicator释放认证为等价。
4. primitive已自然结束、所有指定输入消费完成后，才进入native失败交接边界。若实际indicator存在，可MOVE真实异常到独立first槽，随后按实际残留进行外层cleanup。不能用phase或result NULL推定哪些输入已消费，也不让旧slot的死地址再次拥有。
5. NULL且无异常是单列的API/不变量问题：所读evaluator在NDEBUG构SystemError，在debug assert；现有take_failure则构RuntimeError。future native站点须根据真实primitive/API与已选delta固定策略，不能直接复制其中任一错误、伪报NoMemory或补默认source拒绝。
6. nonNULL结果不能在未经实际call/result-check合同核定时统一当成功，亦不能把所有primitive一律追加同一个CheckFunctionResult。V3真实call的result-check和body primitive自己的结果语义分开设计。

## 后继实际owning/MOVE表

后继consumer的automatic record只能持同一C activation内的实际发生项；这里不冻结新ABI、布局或符号，也不发布孤立helper。每笔引用至少分开记录“已取得/转入历史”和“当前仍拥有的槽”，不能用前者掩盖后者已经被消费。

| primitive输入 | 进入时的真实来源责任 | primitive完成后必须运输的责任 |
| --- | --- | --- |
| 第二次name | 实际module属性lookup产生的一笔owning；与第一次name不同发生项 | repr调用完成后的原序DECREF已消费name；仅实际repr结果可成为format输入。 |
| repr/format输入及可选spec | 从同次真实返回或实际参数MOVE；源槽NULL后才允许callout | 结果独立接入实际out；原value/spec按handler消费，不在外层重复释放。 |
| message pieces | 真实prefix/formatted/suffix occurrence逐笔进入固定自动槽，不能把borrowed code子项直接称owning | join后按高下标到低下标归还；返回message仍是一笔独立实际结果，异常仍在pending直到规定边界。 |
| frames helper owner | E1实际_bootstrap lookup返回的owning | method命中时转移receiver；未命中或classic时归还owner；meth/res独立接后续call。 |
| V3 callee/argument packet | 只来自同次已存在的真实callee/receiver/exec/code/dict等槽 | 按实际分支由frame/binder消费或由generic同步借用跨度后结清；输入消费不凭result推测。 |
| helper返回值 | 同次真实call输出，不改成exec_module输出 | 在规定POP效果站点消费；随后才取得/返回实际None。 |

移动应先把来源槽清空，再让后续消费者持有同一笔实际引用；这不是额外NewRef，也不能用地址相等合并两笔债。operand loan仍由entry code guard持续覆盖，只是operand；copy、类型名或loan不产生owning。任何额外check guard、automatic字段/数组、C栈与回调跨度仍须预算，不报零成本。

“从槽移到局部再原序DECREF”还须在完整设计中核定callback可达性、合法别名及source前提；本表不是对任意公开record/外部指针操作的全域安全证明。内部allocator/callback/清理不因用户A选择而豁免；S1仅保原受保护T0范围，不扩大普通class/instance/alias。

## 实施入口与仍缺的证据

下一源码批须把以上消费表与r1523真实V3 producer/frame/generic/result-check/close、r1525完整N1–N7/E1–E6整合，保getter/name/F3只发生一次。不能单独发布一个没有同次consumer的raw release helper当成功，也不以generic call旁路代替标准PyFunction的已选owned路线。

生成器的MakeCoro实际code/分配仍缺两份待批准资料；没有批准不联网。原函数实际opcode/oparg、PyObject_Format实现、全部合法callback/source、first/pending总策略、正常None返回、target/seed/helper/exec/audit及selector完整消费者仍需继续。本文不声称V3设计已完整或新增源码准入。

只写General、真实库只读；不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行，不联网/诊断/安装/权限/服务/Git，不新代理/分支/worktree、不更新memory，仅复用原reviewer。P06无备份不再问，初始化重入未答不代选；完整H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、正常退休/restore_returned、opaque GC、direct/alias/exec、source/capacity、COMPAT/M/S1、物理预算和生产交付继续开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
