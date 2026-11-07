# r1523：同一 native C caller 的 V3 拥有调用设计草案

本稿细化r1522/NEXT的真实inputs/调用/close，不是源码准入。当前源码为r1522/working-01完整22源 / 2,054,375 B；主封存347f26、最终独立33d1a7，FINAL为12,826 B / 33c29ad3607cb4d876885448182ca93c04b565bef2f848b05530d6c17fffc702。6ffae1已核60固定项/61文件；该exec末尾两个genobject.c精确路径缺席导致exit1，前面的身份核通过，无候选执行。

后继继续既有native C inner→真实child→同一活跃C续体，不恢复F4 Python frame，不强加跨C返回carrier、原F4 PC/cache或解释器CALL/RETURN接线。实际所选入口的function/vector/frame/eval统计、C递归、eval_frame/监控及物理成本仍如实计账。

## 真实输入 producer

外层参数接受域由原binder负责，不受内部四参数上限限制。只在原BOUND两值已真实拥有后，消费同次dynamic_get_code实际mode/callee/receiver与第一次name；原两值不重绑，getter和name不重跑。

在第一次name之后的实际调用点展开shape0的exact PyMethod：先实际拥有self/function，再自然释放旧method，之后观察真正callee及slot。shape1已经含receiver，不二次展开。展开是CALL producer的实际历史，不是freshness证书。新callee即使不可调用也由随后真正call产生错误，不提前探测。

V3输入packet拟包含callee_owned、arguments_owned[4]和实际argument_count；各笔owning不按地址去重。四槽是已归一化的实际内部位置参数，不是外层argv上限：get_code为1或2；ImportError为1；frames为3或4。RAISE内的异常类无参实例化沿其真实primitive，不强塞新协议。需要第五项的普通CAPI内部适配不因此被截断；普通callable继续其真实调用入口。

producer必须先明确每个packet槽由哪个同次actual槽MOVE，先NULL源槽再允许callout。原binding.values仍为独立binder结果；不能把借用argv变成owning声明，不能复制V1/V2实体。新record只在同一C activation存活，不存借用自动地址到持久对象或TState供外部回调复用。

## 调用 record 与输出

ABI拟独立major=3、minor=0，具体布局/符号尚不冻结，也不由V1 DescribeWord认证。record至少分开记录：

- 实际输入MOVE历史和当前argument-owning mask；二者不能混用。
- callee/check guard、function、locals、code、每个argument及result的真实owning槽。
- 实际function/frames统计站点、allocation、Initialize、binder、eval及result-check的called/returned历史。
- raw/reservation、pending/first及实际未消费退出债；只有选择确实需要这些责任的路线才加入，不能预置为“已经结清”。

输入未选中的返回必须在MOVE和效果之前，原packet保持。实际选中后同步推进到明确结果/异常及残留输出；不得回一个没有同次consumer的READY状态，也不重新Prepare、getter/name或已发生F3。out必须是独立实际空对象，MOVE后NULL record槽；close只处理真实非空残留，不据phase/NULL结果猜已消费。

## 标准 PyFunction 与普通 callable

先在实际调用点取得实际callee的真实slot。标准PyFunction路线应与native C调用的实际function-vectorcall入口绑定，不能套原F4字节码inline条件；eval_frame的最终选择仍在真实eval分派站点处理。自定义slot/其它callable保持其实际generic入口和内部result check，不能换成保存的expected slot。

直接owned frame路线需要一笔真实callee检查根覆盖结果核对中的callable格式化。可把caller实际callee occurrence保为check根，并在原prefix的function取得站点取得frame function那笔；参数则MOVE进frame，尤其first_name不额外pin到Invoke返回。不能把必要callee guard泛化成所有argument长期guard，也不称新增根零成本。

generic路线在真实borrowed public-call跨度内保原实际packet，随后在规定native close点结清。它内部可能有Method适配、heap scratch、额外Python入口和结果检查，必须按实际分支列账，不将它冒称direct-owned分支或再次调用。

## code/布局问题必须继续分层解决

原调用至少有不同观察站点：function-vectorcall的flags/locals选择、frame分配前的code/framesize读取、原binder入口的晚func_code读取。r1522只把最后一个code输入改为显参，并保唯一旧caller的晚读取，没有把三次观察合并。

V3若选择自己实际持有的code同时用于frame预留、Initialize和显式binder，可以解决这个具体frame/binder布局关联；但它的取得时点、额外root可见性、allocator callback改变function code后的语义均需要另行准入。不得以code存活证明code稳定，不得把普通动态function改写为受保护T0或默认source拒绝。原PushAndInit分配NULL时func/locals残留与binderNULL已消费的区别保持。

还不能认定这个绑定方案已覆盖全部body。当前已知RETURN_GENERATOR调用_Py_MakeCoro(func)，随后Frame_Copy；但未取得MakeCoro及其直接分配helper的实际实现，不能断言它使用frame code、current func code或某种分配尺寸，也不能断言已经存在或已经修复错配。COPY_FREE_VARS使用frame code与func_closure，需要原正常closure形状及读取域；不提前复制closure或额外持根来掩盖未知路径。

因此目前不为生成器新增hook/context或改runtime；先取缺失原始证据。同步C caller的既有选择不因资料缺口而重开。

## 每个实际退出的责任表

| 实际站点 | 必须明确运输/清理的责任 | 当前结论 |
| --- | --- | --- |
| 未选中、未MOVE | 原callee/args仍在packet，indicator不变 | 协议要求，不是所有动态域已实现 |
| inputs已MOVE、frame未Initialize | record仍有真实function/locals/args/code/check根，raw/pending逐笔保留 | 不从NULL返回猜全消费 |
| allocation NULL | 先保真实pending/first及未消费债；最终NoMemory时点不得早于规定close | 具体新错误优先尚须完整设计，不能借旧注释补修原函数 |
| 已Initialize、binder返回失败 | frame/root和binder已消费参数交原Clear/Pop；record源槽先NULL，不能再按argv释放 | 保原varargs tuple-steal失败已清extras |
| binder成功、eval成功或失败 | 实际evaluator/provider负责frame返回/异常/生成器转交，不统一二次Clear/Pop | MakeCoro直接分配路径待证 |
| result check | callee检查根独立覆盖实际回调；参数已消费后不以死地址读对象 | 不能由expected callee的名字代替寿命 |
| 最终close | loan先结束，再逐笔真实残留及entered/metadata/module/Record债；actual first不被覆盖 | 需要真实owner consumer，不能发布孤立helper |

不能把r1521观察到的某一个opcode的callee→args或args→callee顺序泛称native统一“原CALL顺序”。native接口自己的实际close顺序必须明确，与既有source/first/arg生存及已选择语义共同审查。

## 完整 body 与后继接线

真实V3 producer/close与same-C续体一体落地。None分支保entry G/B实际ImportError lookup、第二次name、repr/释放、format/释放、join/pieces释放、真实call/raise；非None保helper方法、exec、module dict、实际call、丢结果、返回None，接真实target/seed/exec/audit消费者，不提前限制code/dict类型或用普通exec旁路。

旧V2的ENTRY_CODE_HELD/READY/INVOKE_RETURNED继续持同一packet/result与reservation/record_address/tstate责任；不转换ABI、不重定位、不重呼F3。普通class变化与登记INSTALLING域分别由完整selector接实，不留下dict-held FAILED bound后报成功。

本稿尚未固定完整API/代码布局、错误优先及全部admitted域。先补MakeCoro两份明确实现/声明资料，再形成整体设计双审；之后才准备source/tool、独立预测、单次生成及实际/文档复核。当前没有实现V3或给出源码准入。

只写General、真实库只读；不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行，不联网/诊断/安装/权限/服务/Git，不新代理/分支/worktree、不更新memory，仅复用同一reviewer。旧单次读取/诊断许可不复用；P06无备份不再问，初始化重入未答不代选。完整H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、正常退休/restore_returned、opaque GC、direct/alias/exec、source/capacity、C/ABI/build/runtime/COMPAT/M/S1、物理预算及生产交付继续开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
