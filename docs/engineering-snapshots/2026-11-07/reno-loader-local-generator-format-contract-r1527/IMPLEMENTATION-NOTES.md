# r1527：既有缓存中的生成器分配与格式化合同

本批补到实际实现证据，不生成或执行源码候选。当前完整源仍为 r1524/working-01（22源 / 2,058,033 B）；r1526已双审封存，最终独立456c95。r1523原“未取得MakeCoro直接分配实现”的材料保留为历史记录；当前已有可读的本地实现，不再把该实现本身缺失当作等待联网的原因。

## 发现及身份

6fb8de按四个精确文件名（含c.txt/h.txt形式）在项目data下作文件名搜索，找到r1126/r1101的genobject.c、r1119/r1232的abstract.c及r1125的unicodeobject.c。该次exec返回1，并有部分旧测试目录访问拒绝，输出也被截断；因此不是全项目无遗漏证明。1493a2在General同类文件名搜索无匹配、exit1。没有修改ACL或读被拒目录内容，也没有重新运行整树搜索。

d667e7列具体材料目录；09bee6读已有下载清单与相应函数；176309核genobject/abstract身份并取得unicode明确下载记录；11d3c2核RETURN_GENERATOR及既有frame.c服务表示的Copy正文。使用的是现有本地文本，不执行待批准的r1523阅读服务请求，也不复用旧单次网络权限。

- genobject.c：项目 data/t16-h01-catalog-generator-route-r1126/cpython-v3.12.10/Objects/genobject.c，78785 B / 7c13a1de31fc8af6e082fc9aa055bcfe980786b4104fec0f7564e40735e44cad；OFFICIAL-DOWNLOADS-01.json记录v3.12.10 URL。r1101同名实现的下载记录给出同一身份。
- abstract.c：项目 data/t16-h01-private-locals-ingress-r1119/cpython-v3.12.10/Objects/abstract.c，79783 B / abb8e2403b6479b976e900668273a44d29f117ebb021b0f4669f3bf03521766e；CPYTHON-NEW-SOURCE-01.json记录同tag。
- unicodeobject.c：项目 data/t16-h01-import-from-route-r1125/cpython-v3.12.10/Objects/unicodeobject.c，467971 B / 74028ecb90344d6f7e503e7bea29f505d04795a2c416309ac9bac4db29539abf；IDENTIFIER-SOURCE-DOWNLOAD-01.json给出同tag URL和身份。

这些是与已有下载记录一致的本地文本身份，不新增上游commit/HTTP真实性认证或目标编译输入证明。全部本次输入另列 INPUT-IDENTITIES.json。pycore_genobject.h的单独目标尚未本次读取；不据不完整文件名搜索断言全本地绝无该文件，也不把未取得的header合同称已核。

## MakeCoro现在可以确认的实际读点

| 站点 | 已读正文 | 对后继V3的约束 |
| --- | --- | --- |
| RETURN_GENERATOR，generated_cases:4548 | 从frame.f_funcobj取func，调用_Py_MakeCoro(func)；成功后同步stack pointer，取gi_iframe并_PyFrame_Copy(frame,gen_frame)，设CREATED/owner、leave recursion、PopFrame并交结果到父frame | 传的是function，不是显式held frame code；evaluator的转交与退出仍由真实路径负责，外层不能再Clear/Pop同一frame。 |
| _Py_MakeCoro:895 | 从当时func->func_code读co_flags，决定generator/async-generator/coroutine路线 | 此读点不等同于frame.f_code或更早guard；不能凭func存活证明code不变。 |
| make_gen:871 | 再从当时func->func_code取code；NumSlotsForCodeObject(code)后PyObject_GC_NewVar | 尺寸来源是这个读点的function code，并非自动采用frame code；与前一flags读点也不能未经效果分析就合并。 |
| make_gen分配后 | 初始化frame state、weakref和exc_state，再读func_name及func_qualname取得独立引用，随后GC_TRACK | 名称取得在分配之后；不能提前快照或称无新增root/GC站点。 |
| async-generator分支 | make_gen成功后初始化origin/finalizer、closed、hooks_inited、running_async | 不由generator常规分支代证这些字段与效果。 |
| coroutine分支 | make_gen后读取coroutine_origin_tracking_depth；0时置NULL，非0时取当前frame链并compute_cr_origin；失败则DECREF已建coro并返回NULL | 原点跟踪与实际C/evaluator/frame链有关。F4删除为已选delta，不能把原F4加回以“修复”该差异。 |

已存pycore_frame.h中的NumSlotsForCodeObject正文使用 co_framesize - FRAME_SPECIALS_SIZE。它证明该函数文本的计算式，不证明未来目标layout/宏尺寸。176309的辅助输出第一次按名字命中的是_PyFrame_Copy声明，不能把随后打印到的Initialize正文冒称Copy实现。Copy实现另由11d3c2从已存r1445服务表示L59–67核得：根据src->stacktop到localsplus尾部计算字节并memcpy，随后dest->previous=NULL。该表示不是原HTTP字节或可编译官方输入。

因此frame/binder使用的held code、MakeCoro分流读的current func code、make_gen取尺寸的current func code和Copy读的actual frame范围必须分层处理。r1522仅提供显式binder code参数，r1524仅提供borrowed operands，均不能据此声称完整关联已解决。

这不证明已存在普通合法运行反例、实际越界或修复。是否有合法writer在有关callback/分配跨度改code、它与原普通调用算法的关系、实际尺寸条件及兼容性都需进一步论证。不能直接加func_code==held_code的拒绝guard、禁watcher/allocator或扩S1；也不能未经选择就把全部当前code读点改成held code。当前下一步是解决这个具体code-view合同，不是继续等待未取得实现。

## PyObject_Format的实际实现

abstract.c:761–817现在全文已读：

1. 非NULL spec先核PyUnicode_Check；不符时构SystemError。
2. spec为NULL或长度0时，仅exact Unicode直接Py_NewRef原obj，exact long走PyObject_Str。repr接受Unicode子类，不能据PyUnicode_Check把所有repr结果当作此exact快路。
3. NULL spec在后继路线取得empty=PyUnicode_New(0,0)；然后按实际obj查未绑定__format__，没有已有异常的missing才构TypeError。
4. 真实PyObject_CallOneArg(meth,format_spec)之后释放meth；有结果却非Unicode时构TypeError并Py_SETREF(result,NULL)。
5. done统一Py_XDECREF(empty)，返回实际result。方法lookup、调用、方法释放与empty释放的发生项不能用只做repr或直接拼接替代。

这里只核直接实现，没有展开全部LookupSpecial、CallOneArg、Unicode_New/Format内部、signal/GC/allocator或结果检查族。对它们的C API/合法域、target build与预算不作推广。特别是源码只按NULL spec进入PyUnicode_New并继续，不能按猜测新增重试/错误分支后称修复。

## JoinArray的实际拥有边界

unicodeobject.c:9746起的完整函数已读。seqlen为0返回empty；为1且子项exact Unicode时NewRef该项。单项非exact路径设置seplen=0、maxchar=0，跳过separator处理，直接进入子项预扫；至少两项的正常路线才准备separator（NULL时构空格，否则核Unicode并INCREF）。后继预扫逐项PyUnicode_Check、长度/maxchar及溢出检查，PyUnicode_New后按实际kind复制。

正常尾释放sep并返回res；onError释放sep与res后返回NULL。函数不steal或DECREF传入items数组中的各项；这些仍交外部BUILD_STRING的高下标到低下标释放。不得把函数内部的sep/res清理与外部pieces消费合并成两次或遗漏一次。三片段是否为实际目标指令的oparg仍由目标source/operand合同决定，不能只从唯一内容匹配推定。

这也不把全部Unicode_New/copy helper实现、宏、不可变性来源或物理峰值证明为已闭合；正常消息对象的分配和实际拥有笔数要进入整版预算。

## 对实施顺序的实际改变

现有本地实现已经足以继续MakeCoro、format和join的语义设计核对；不应再将genobject.c本身列为必须等待网络批准才能分析的缺口。原r1523两目标外部读取仍未执行，pending许可不自动被消耗或扩大。

下一步把r1523的V3输入/phase/close草案与本批分层code读点、r1526 primitive内pending/正常入口/外层first，以及r1525 N1–N7/E1–E6整合。先固定合法code变化域和真实结果/消费/清理合同，再做完整设计双审与源码批；不发布无consumer helper、不用普通generic调用替换标准function已选owned路线。

始终沿native C inner→实际Python/C child→同一活跃C续体，保留已选F4 frame删除。完整API/布局、生成器分配与actual frame关联、closure、allocator失败、结果核对、全部callback/预算、target/seed/helper/exec/audit和selector尚未实现。header/build/ABI有后继验证责任，不把本文字核对当执行准入。

只写General、真实库只读。不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行、不联网/诊断/安装/权限/服务/Git，不改ACL、不新代理/分支/worktree、不更新memory，仅复用原reviewer。A与S1保既定范围；P06无备份不再问，初始化重入未答不代选。完整产品功能、H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、正常退休/restore_returned、opaque GC、direct/alias/exec、source/capacity、COMPAT/M/S1及生产交付仍开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
