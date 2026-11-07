# r1528：make_gen 的显式分配 code 输入

目标是为完整V3的actual frame/code分配关联提供必要的既有helper能力，保持现有生成器三条路线的读取选择。不是完整V3准入，也不以此解决r1527所有code-view问题。

## 已确认基线

当前完整源码为r1524/working-01：22源 / 2,058,033 B；FINAL13801 B / 7f0c85b54d9b9b055ee5cec3f515880a43eb61f1f632d3c5dedee2dcfa81876e，d9de5d本轮核完整62固定/63文件及caller当前身份。

r1527 FINAL4526 B / 6bfecfaab06772c6fb94654172406f10ab8b91e15dd9398dbfccd95444ed600f，主529f4b、独立73c73e；r1526也已封存。所用genobject实际源为项目 data/t16-h01-catalog-generator-route-r1126/cpython-v3.12.10/Objects/genobject.c，78785 B / 7c13a1de31fc8af6e082fc9aa055bcfe980786b4104fec0f7564e40735e44cad，与已存同tag下载清单及r1101副本一致，不重新联网。

ea76f8确认make_gen恰一完整定义、三个调用；func->func_code现有两个字面站点：make_gen内部尺寸读点与_MakeCoro flags读点。不能把静态词频等同动态次数。

## 四组源码改动

只修改该genobject源文本，并将它以 runtime/Objects/genobject.c.txt 纳入完整候选。父22源、block及r1507四caller材料保持原字节：

1. private static make_gen增加PyCodeObject *code形参；删除原函数入口的局部code=(PyCodeObject *)func->func_code。NumSlotsForCodeObject及后续分配、初始化、名称/qualname与TRACK正文不变。
2. _Py_MakeCoro的CO_GENERATOR分支调用make_gen时，新增实参(PyCodeObject *)func->func_code。
3. CO_ASYNC_GENERATOR分支同样新增该实参，保外层cast和原o初始化。
4. CO_COROUTINE分支同样新增该实参，保原origin tracking与失败DECREF。

原flags读取仍在原_MakeCoro入口，不改成frame code或earlier guard。三个caller各自到真正调用点才取当时func_code，不把它合并成_MakeCoro早期统一快照；也不增加拥有引用、code相等guard、source检查、fallback、额外error/result-check或新的helper定义。

现有分配code读取从private C helper第一语句移到它的实际调用实参求值。源文本两站之间没有显式Python API/callback操作；这只支撑有限的读取顺序设计，不能认证编译器序言、stack probe、C ABI、异步外部instrumentation或实际runtime等价。所有合法target前提与物理成本保持待验。

三个caller得到的borrow只用于该次helper读取NumSlots和分配前的标量计算，不从此新增跨callback的code存活保证。func/current code的形状与合法性仍取原调用合同；不以参数类型名新增source权限。raw指针不是新owning，也不据此称零成本。

## 完整源集及可逆材料

这是首次把该已存tag参考纳入当前完整候选。before-01由父22个working源加上述原始genobject源组成23源 / 2,136,818 B；working-01保持相同23路径，只对genobject做上述四组字面变更。不会把新增参考误记成父22源本来已含此文件，也不会执行参考源码。

输出准备应有完整before/working各23份、source diff、未变block及空block diff、inventory和static报告，合计51项输出。明确区分父22源身份和新增参考身份；四编辑反向完整还原78785 B原genobject。保持原make_gen剩余正文、_MakeCoro除三个调用实参外的全部正文、其余22源/block/caller和122继承布尔48 true/74 false。

流程继续设计双审→源码/工具双审及独立51项完整预测→单次文字生成→实际/文档双审→封存。当前仅设计，不运行工具生成C候选；不沿用r1524“49项”或22源/61文件计数。

## 后继与未解决项

新显参只提供以后选择actual allocation code的能力，三个现有caller仍传原当前func_code。它没有给出V3该何时捕获code、是否怎样关联实际frame、如何保持类型flags/名称观察、合法code变化域或COMPAT结论；不能在后继偷偷改三caller使用earlier held code并称本次已批准。

完整r1523 V3、r1526 primitive消费/正常入口/失败pending/outer first和r1525完整N/E body还需整合。保持native C inner→实际Python/C child→同一活跃C续体与已选F4删除；不恢复F4 frame/PC/cache/inline，不加跨C返回carrier，不将普通函数变化扩大成S1拒绝。

额外参数、删局部变量、函数调用/寄存器/自动栈布局、所有预算/ABI/build/runtime未验证。局部static C接口改变也必须如实记账。原目标opcode、header/目标宏、全合法callback/错误优先、生成器实际分配安全/兼容及全部完整功能均未闭合。既有本地实现可继续使用；r1523网络请求未执行，不使用旧单次授权。

只写General、真实库只读；不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行，不联网/诊断/安装/权限/服务/Git，不改ACL，不新代理/分支/worktree、不更新memory，仅复用原reviewer。A、S1、P06无备份不再问、初始化重入未答不代选保持；完整H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、正常退休/restore_returned、opaque GC、direct/alias/exec、source/capacity、COMPAT/M/S1及生产交付仍开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
