# r1525：None/exec body 的实际效果顺序与缺口

本稿为本地源码调查与后继设计输入，不是实现准入。完整源码基线为 r1524/working-01（22源 / 2,058,033 B）；主封存df6862、最终独立c6e73f已通过，FINAL为13801 B / 7f0c85b54d9b9b055ee5cec3f515880a43eb61f1f632d3c5dedee2dcfa81876e。没有生成新的 C 源或执行产品。

当前 native C inner→实际 Python/C child→同一活跃 C 续体路线保持；已选 F4 Python frame 删除不重开。下面引用旧 opcode primitive 是为了核实效果，不要求恢复原 F4 frame、PC/cache、inline 或跨 C 返回 carrier。AST 的求值顺序与 opcode 源码事实也不等于已取得原目标函数的实际编译指令序列。

## 本地证据

- 项目 data/t16-h01-native-root-handoff-r1112/inputs-02/importlib/_bootstrap_external.py，69406 B / 9bde390a6f254cf6641bfdba053084c1d506c7324c142131c695d030662ab63d；exec_module:993。8c8e10 仅读 Python AST 核名称/错误文本；4cdf82核完整body表达式、is None、!r无源码format spec、raise及helper三个源实参，未执行参考Python或生成指令。
- 项目 data/t16-h01-combined-admission-design-r1108/cpython-v3.12.10/Python/bytecodes.c，143622 B / f8c050621f1498d953d4898aaf29457e154620fec7f8bb987b4d3fde6f5a1d84。658025、48418e读取有关 primitive 全文或相关分支，48418e核身份。
- r1524 runtime/Python/ceval.c.txt 中 do_raise:2564 与 format_exc_check_arg:3532，48418e、ef48d7读取。ceval 的完整身份由本目录 INPUT-IDENTITIES.json 固定。
- r1523/DRAFT.md 为未准入的 V3 整体草案，不能以本稿跳过其尚缺的 generator 分配、code/layout、input/MOVE、错误优先、result-check 与 close 合同。

b24afd/exit1 是在错误参考目录探测 generated_cases.c.h 缺席；随后 b26c5d 从 r1521/INPUT-IDENTITIES.json 取得已有 bytecodes.c 明确路径。没有联网、生成或执行，未将探测缺席当成全本地缺失。

## 入口已有历史

get_code callable/receiver 的动态 lookup、第一次 module.__name__ 与原 F3 可能已发生。完整 V3 要消费同次实际发生项和真实 result，不能重新运行旧 getter/name/F3。原 BOUND 两值仍由原 binder 持有；新增六个 operand loan 不是 lookup result、源码权限或拥有引用。

以实际 get_code 返回对象判断 code is None，不能用返回 NULL 或 operand 未选中表示 Python None。原 Python AST 只能证明条件意图，不能证明它编译成 IS_OP 加分支；已有 bytecodes 同时定义 POP_JUMP_IF_NONE/NOT_NONE。本稿不虚构原函数的 opcode witness，也不让 future body 消费未认证的常量位置。

## None 路线：求值与错误顺序

| 顺序 | 实际效果 | 后继必须保留 |
| --- | --- | --- |
| N1 | 从 entry G/B 观察 ImportError | 先于第二次 name。不可替成固定 PyExc_ImportError；取到非callable也暂不提前报错。 |
| N2 | 取得本次 module.__name__ | 不复用第一次 name；N1失败不得运行此 getter。该值是实际 repr 输入。 |
| N3 | repr 转换及转换输入的释放 | FORMAT_VALUE 中 conv_fn(value) 后先 Py_DECREF(value)，再核转换NULL。释放顺序、indicator可见性要单独落实。 |
| N4 | 对转换结果执行 format，随后释放其输入 | primitive 仍调用 PyObject_Format(value,fmt_spec)，再释放 value/fmt_spec后判断NULL。不能把 !r 简化成只做 repr；本稿未核 PyObject_Format 实现或原编译实参。 |
| N5 | prefix、formatted name、suffix 组成消息，并结清 pieces | BUILD_STRING 的 _PyUnicode_JoinArray 后 DECREF_INPUTS，再检查str NULL。真实 pieces 的取得、拥有笔数、顺序仍需完整producer。 |
| N6 | 用实际消息调用N1实际值 | 不在N2–N5之前验证callable。走同次V3真实call/result-check，不硬编码异常构造器。 |
| N7 | raise N6实际返回对象 | 可能是异常实例、异常类或其它值，不能假定返回永远是 ImportError 实例；遵循真实 raise primitive。 |

do_raise 的已读事实：异常类输入调用 _PyObject_CallNoArgs 后核实例；异常实例输入取得 type 的独立引用；其他值先释放 exc 再设置“exceptions must derive from BaseException”。正常路径 _PyErr_SetObject 后释放 value/type；失败路径处理真实 value/type/cause。N6调用和N7类实例化是两个不同效果，不能合并或把N7强塞成同一个“一参数ImportError调用”。

这些表项尚未证明原指令的实参、具体 DECREF_INPUTS 展开或所有错误优先。FORMAT/Join先释放后判NULL的源码次序意味着在所有释放前先 take_failure/清indicator 并不自动等价；不能直接套现有 cleanup helper。需要明确 first/pending 的取得站点、回调时可见indicator、每笔真实残留与原生关闭策略，再审查是否属于已选delta。未获得证据前不加重试、白名单或默认拒绝。

## 非None路线：求值与错误顺序

| 顺序 | 实际效果 | 后继必须保留 |
| --- | --- | --- |
| E1 | 从 entry G/B 观察 _bootstrap | 取实际当前binding；不是metadata中某个expected module。 |
| E2 | 从该实际对象取 _call_with_frames_removed | 依实际 LOAD_ATTR 形状和真实producer区分method pair与普通attribute，不凭名字/模块类型认定为无self。 |
| E3 | 从 entry G/B 观察 exec | 在helper lookup之后、module dict之前；不能替成固定builtin exec。 |
| E4 | 运输同次实际 code，再取 module.__dict__ | 不重跑get_code或提前限制code/dict类型；实际helper可以接受自定义值。真实读取/引用取得时点需要固定。 |
| E5 | 用实际helper、exec/code/dict及实际receiver调用 | helper不可调用的错误在实参求值之后发生；保mode0/mode1与exact-PyMethod在真实call点展开。V3的3或4实参不可截断。 |
| E6 | 丢弃helper实际返回值，随后返回None | 不将helper任意返回值作为exec_module成功结果；丢结果也可能触发清理效果。None返回的真实拥有/常量合同仍需固定。 |

该次调用是helper调用，由其真实实现决定是否及如何调用exec；不得绕过helper直接执行exec以报告完成。真实target/seed/exec/audit消费者仍需接线，generic Python/C callable的内部成本及结果核对如实计账。

## 全局与属性 primitive 的已确认差别

LOAD_GLOBAL:1323–1361 的 exact globals/builtins dict 路线使用 _PyDict_LoadGlobal，并在仅missing且没有异常时格式化NameError；命中后增加引用。非exact路线先 PyObject_GetItem(globals,name)，只有KeyError才清错并继续builtins；builtins KeyError再转NameError，其它异常直接保留。入口G/B对象的实际来源与合法域仍由完整设计负责，本稿不宣布任意NULL/非dict输入已被支持。

format_exc_check_arg 不只是写一条固定字符串：先 PyUnicode_AsUTF8，再 _PyErr_Format；NameError还临时取异常，必要时给 name 属性赋值后恢复异常。只拼相同消息不能冒称等价。

LOAD_ATTR的method路线使用 _PyObject_GetMethod：命中unbound-method形状转移owner occurrence，普通attribute形状释放输入后核meth并设置NULL前导槽；classic路线 PyObject_GetAttr 后释放owner再核NULL。旧动态get_code的shape不能代替本次frames helper的真实shape。这里仅核primitive源码，不证明目标函数采用哪一专门化、监控或具体opcode。

## 可实施前的具体清单

1. 完成V3整体设计双审：同次inputs/MOVE、standard function与generic callable、code/frame布局、生成器/closure、result-check、allocator失败与close。
2. 为N1/E1/E3固定entry G/B与新key的实际lookup接口、输出拥有笔数及missing/error语义。六loan未选中不构成新S1拒绝或假成功。
3. 为N2–N7与E2–E6逐笔列callee/receiver/code/name/repr/formatted/pieces/message/dict/result债，固定每个失败出口和实际释放时点。地址相等不去重，额外guard不称零成本。
4. 明确原Python的求值事实、旧opcode primitive事实、已选native delta与仍无编译/运行证据的项，再准备真实producer/consumer源改动，不发布孤立helper当完成。
5. 按设计/源码工具双审与独立全预测后才做下一源码批单次文字生成；C/ABI/build/runtime、COMPAT与物理预算仍另行验证。

只写General、真实库只读；不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行，不联网/诊断/安装/权限/服务/Git、不新代理/分支/worktree、不更新memory，仅复用原reviewer。r1523读取批准仍待答，不复用旧权限。A与S1保持既定范围；P06无备份不再问；初始化重入未答不代选。

完整selector/正常退休/restore_returned、opaque GC、direct/alias/exec、source/capacity、H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、生产交付和完整产品Goal仍开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
