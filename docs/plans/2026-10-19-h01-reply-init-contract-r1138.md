# r1138：公开heap实例type合同与引用包络

r1138以同版官方heap实例持type及free后释放type合同，给成功guard的独立实例type根提供设计依据；与完整object.h和统计宏网页观察组合，限定full API非Py_REF_DEBUG的32/64位INCREF、immortal与nonlast DECREF包络。没有冒认读到缺失Init raw头或实际build，guard初次源根/成功allocator/NULL入口/整图N4仍待；B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

统计网页24词及heap文档12词/工具坐标非raw行；一新raw文档单次Timeout终止不重放，既有失败头不重试。公开合同不代actual来源/正确Init/构建接线，source/phase仍fresh，guard与instance type真实acquire不去重。七段完整源码、六行有限包络和三原Try/既有出口分列，不称运行覆盖或全部try分类关闭。

本批candidate/产品compile/import/运行/native/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。159源/保护56同18缺/原有限额及禁项保持；完整Goal仍有工程，非只剩验收。

# r1138：公开堆实例合同与有限引用包络

承接已封存r1137的24文件/3,200,017 B。产品仍r1106 SOURCE02完整159文件/7,956,657 B，未安装。本批用同版官方合同收敛成功heap实例的type拥有责任，并将已读统计宏接回具体INCREF/nonlast DECREF分支；不是全部Init实现、构建身份、NULL入口退出或完整源码准入。B001/B002 open2，完整design/source/execution/Goal均False。

## 1. 证据的层级与新的设计依据

既有完整gcmodule.c2341–2351已证明已知GC分配NULL分支在Init前返回，成功分支调用实际`_PyObject_Init`；没有取得该inline实现的完整同版头。两个已失败header和新allocation文档均不重试，不用SDK未核tag或较新版文档回填。本批保留这一事实，不将新文档冒认为raw Init body。

新读取的官方tagged v3.12.10 isolating-extensions HOWTO直接规定：heap实例持有自身type引用；traverse要访问type；不需要在new/clear额外处理type引用计数；自定义deallocator在instance free之后释放type，默认deallocator已经承担这一责任。文档同时允许在不适合调用allocator slot时使用GC-aware New/NewVar，并要求heap type的free保持GC_Del。

因此选择**公开合同路线**：对于匹配公开GC_New路径成功、正确初始化的实际heap实例，用这个官方拥有合同证明实例的独立type强根，而非继续把读到每个内部inline实现当作设计依据的唯一形式。此处是API合同层面的设计依据，不声称实际未来C结构/宏/headers/binary/build已经核验。source阶段仍须验证产品确实使用该构造器、actual type来自原创建、初始化正确且实例持续拥有。没有额外猜测一次type INCREF来补缺口；私有guard那笔与实例type那笔仍分开。

HOWTO还说明指向module state的指针须由module引用维持寿命，FromModuleAndSpec的type/实例通常持有module。这支持生命周期设计，但**不冻结state内部可变type/source字段**。模块活着不代表未退休，caller身份、source/phase仍须独立核对。该公开合同与完整typeobject.c1809–1828的heap-type traverse边、2043–2065的instance free后释放type顺序互证。

网页只保存版本、工具坐标、12词原句/示例及明确解释；未取得629行raw全文，工具坐标不是raw源码行。旧INIT-NEXT分析和staging记录保留原时点的未审状态；本次以新增合同推进，不改写历史。

## 2. 同版统计宏与真实引用分支

既有完整object.h624–659有full/limited与32/64位INCREF路径；678–708有debug/nondebug DECREF路径；239–247给出immortal检测的原始字段比较；808–840的NewRef只调用INCREF并返回原对象。新tagged pystats.h网页观察补上统计部分：启用统计的四个宏体是对普通统计数据指针所指标量计数的条件递增；禁用统计时是void zero。补充见证确认该指针为PyStats指针、其object_stats为普通ObjectStats字段，四counter为uint64_t。

这仍是网页完整定义观察加小片段/上下文，不是完整raw宏闭包hash。全部quoted片段合计24词，版本与边界工具坐标分列。在**匹配这些定义、有效普通运行时数据、full API且非Py_REF_DEBUG**的前提下，这些统计宏本身没有Python协议分派、分配、引用释放或callback。不能由statements单独证明实际build身份，不能把任意错误统计指针当合法反例，也不把原始数据指针视作业务source来源证书。

| 操作/分支 | 有限推导 | 前提与未覆盖 |
| --- | --- | --- |
| full/nondebug INCREF，64位 | 原refcount饱和早退或字段更新及统计标量操作，不进入deallocator | 非NULL有效原对象，匹配头/ABI/运行时数据；不以计数具体值认领来源 |
| full/nondebug INCREF，32位 | immortal早退或增计数及同一统计操作 | 同上；原有效refcount域及对象持续根，不造溢出状态 |
| full/nondebug DECREF，immortal | 原检测成立后直接返回 | immortal判定本身是原字段比较，不是Python协议；不改变该对象拥有合同 |
| full/nondebug DECREF，nonlast | 有真实独立持续强根时，数值减计数不进_Py_Dealloc；统计宏不额外引入callback | 先证明本次待释放拥有及另一个根跨释放持续；不能从早读一次count推断此刻nonlast |
| guard失败可能last | 不能套上行；actual准备first按r1137保存，结清guard/temp后沿r1135还原 | 不要求该清理一般NULL→NULL；中途API前置条件仍逐项核 |
| debug/limited/其它版本或build | 不纳入本次无callback推导 | 声明/函数名不是未读实现的证明；原>=3.11支持不删除，保后继具体路径义务 |

所示32/64位与immortal分支必须一起保留，不能只看最常见64位加法。Py_REF_DEBUG的total函数、limited外部IncRef/DecRef分派仍未由此检查；_Py_NewReference的初始化/trace bookkeeping也不由普通INCREF结论代替。该表六行是静态分支责任，不是测试或执行次数。

## 3. 接回私有guard与空壳的具体时段

r1137的guard继续从独立原type来源取得一笔私有C自动owned引用。**初次取得前**，future actual C receiver与原module/state/actual type来源链必须持续，且原owning边从raw字段读取到真实NewRef完成之间不被移走。本批引用包络使“已匹配raw段内仅有原始读取/检查/NewRef”的有限推导具体化；字段可变本身不否决这个短段，但该段尚没有实际产品源码，不能宣布来源接线已存在。

不能将shared state中后来current type当expected，不能借“receiver存活”推断它的可变边永久存在；若读取→acquire之间另有allocator/getter/DECREF/锁等待/释放GIL等callout，仍须重排或独立持续根。guard只保活对象，不保phase/source资格。类型缺失或原根不能建立的拒绝协议仍须与三处旧try完整矩阵一起实现，不在本批塞一个ValueError捷径。

**成功GC_New之后**，实际owned shell保持C局部持有，先raw初始化三个payload为NULL、mode为PRIVATE且untracked。匹配公开heap实例合同给出此刻另一笔持续type拥有边；与本批所示nonlast释放包络结合，可以在getter前结清guard：detach自动guard槽至temp，释放temp后不再使用它。不是把guard move成实例type拥有；两个真实acquire不能去重。若公开合同适用/正确Init/actual type或持续shell根尚未在源码建立，就不能依据此条件执行该释放。

原fresh门仍在全部可再入准备后，getter成功实际结果move到reply，填完才Track。既有GenericAlloc自动Track，不用它替换跨getter的untracked空壳；不改变r1136的DENIED/成功/GETTER_NULL/partial五态，也不新增getter后拒绝再冒称attempts0。失败GC_NULL仍无实例、getter0；guard可能last，按r1137 first运输，不以成功nonlast推导替失败路径。

在这个条件子设计中，**成功heap实例存在独立type拥有边**现在有同版公开文档依据；raw Init实现及实际构建仍未核，来源前置根/全调用链/引用预算/N4仍未闭。有限合同与真实产品实现的证据层级分开，不能把后者当已验证。

## 4. 生命周期与兼容义务保持

自定义reply dealloc仍UnTrack→detach payload→按选定顺序结清payload→GC_Del actual self→释放实际instance type；free后不读self。官方合同也要求type在instance free期间保持有效；default deallocator代做type release与自定义实现不能双算。traverse含type且不重复访问，immutable reply的tp_clear=NULL不代consumer/Product/A/J/bank和module状态的整图破环。

已有actual first路径仍按r1135在所有temp/free/type结清后才restore，仅scalar返回；成功后的payload/type/free及一般NULL入口合同仍未闭。成功allocator indicator、getter完整descriptor/call事件、actual未来FAST local/late MATCHER/try外ACCEPT及三原分支没有被该文档或引用宏自动证明。

每次guard额外acquire/release、实例type责任与actual result移动继续分别记账；不以nonlast或immortal早退把拥有责任删掉。control1MiB是控制数据实读限额，不是确认native heap上限。产品requires-python>=3.11、CI的3.11/3.12及其它原平台策略不缩减，不用临时拒绝未核版本换取当前图通过。

普通226叶/7原append/三reply/owner14、bank9、完整P0/38context/CORE/import、D1/D2/COMPAT及所有SCC退休仍按完整范围继续。完整设计双审前不生成产品候选，源码准入前不产品compile/import/运行/native build/load/模型/旧绿F001/QA/诊断。159源/保护56同18缺、17lease/1449slot/406尾/五S002和case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持。

正常库、服务/dist、ACL/settings、真实平台、Git不操作；禁profiling、真实frames/locals、停点/定点计时、新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523/allocator。r920/r964单次已耗不重放，r967不执行不重问；所有失败目标不重试。P06历史0/7无材料不再索取；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal仍有工程，非只剩验收。

# 下一批：原type取得的实际consumer/module/state根

沿同版公开heap实例拥有合同及已收敛的有限引用包络，给future actual C receiver→原module/state→实际reply type→私有guard的原创建/真实持有/raw读到acquire区间具体接线；对已退休/字段缺失与三旧try的拒绝协议分别列明，不能从current属性补expected。公开合同不代实际headers/ABI/build/正确Init；已失败下载不重试，不用tag未核SDK回填。

继续成功allocator indicator、payload/type/GC_Del的NULL入口后置条件；已有actual first在全部owned/temp/free结清后才还原，仅scalar返回。consumer/Product/A/J/source bank与owner14/bank9各SCC的实际traverse/clear/退休继续，immutable reply不能代整图破环。三reply future FAST local、late MATCHER/try外ACCEPT、原三handler与TMD结果拒绝保持。

普通226叶/7append/三reply/owner14、完整P0/38context/CORE/import、D1/D2/COMPAT及>=3.11平台/预算不缩减。完整设计双审前不生成候选，源码准入前不产品compile/import/运行/native/模型/QA/诊断。全部保护/有限额/禁项及已耗单次授权保持，P06无材料不再索取；Goal active，B001/B002 open2。
