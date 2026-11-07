# r1526 补充：正常入口与失败后 pending 的边界

本文补充 CONSUMPTION-CONTRACT.md，不修改其已核字节。primitive内部保留失败后pending，不能解释为允许带已有first任意调用下一primitive。完整V3和body尚未准入，本文不新增源码、ABI字段、状态开关或拒绝域。

## 新的局部源码事实

9deefa只读既有 r1524/working-01/runtime/Objects/object.c.txt:542–592 的 PyObject_Repr。该文件已经列入本批12个固定输入，不新增外部来源。正文依次包括：

- PyErr_CheckSignals，失败即NULL；USE_STACKCHECK条件分支的栈检查及错误。
- v为NULL或tp_repr为NULL的既有fallback。本次第二次name的正常输出应是一个真实对象，不能把NULL lookup失败送入fallback生成字符串。
- 常规tp_repr路线中，Py_DEBUG注释明确不应带异常调用，并断言当前indicator为空；该断言在前述fallback之后，不能写成每条早返都执行过。
- 真实递归进入、调用当前tp_repr、递归离开；result NULL返回。
- PyUnicode_Check结果检查，不是PyUnicode_CheckExact。非字符串结果构TypeError后先DECREF该结果再返回NULL。非debug文本还保有PyUnicode_READY分支，不能单凭读取证明目标build选择。

因此不能绕过实际repr的信号、递归、slot、返回类型和内部失败处理，把 !r 换成直接读字符串/名称。接受str子类也不证明后继format没有效果；PyObject_Format实现尚未取得，本稿不补写其行为或省略它。

本次只确认这些直接站点。PyErr_CheckSignals内部、实际signal handler、tp_repr合法实现、全部递归/统计/预算与目标宏展开没有由此核完；没有运行任何callback或反例。

## 后继阶段必须有的分界

| 概念阶段 | 实际责任 |
| --- | --- |
| 正常输入已就绪 | 只有同次真实get_code调用及其result-check、第二次lookup等前驱自然成功，才进入相应primitive。没有已有失败需要继续“正常格式化”。 |
| primitive执行中 | 保该primitive自己的输入拥有；例如repr调用发生在正常前提，返回失败后才出现其实际pending。不能提前取走这个失败再释放原name。 |
| 失败后的规定释放 | 依生成handler先消费name、format输入或join pieces。返回NULL不代表输入还持有；pending由当前线程实际槽持有，尚未移进private first。 |
| primitive完成后的失败交接 | 在规定消费已经结束且实际异常存在时，将该笔异常MOVE到private first，才开始需要空indicator的native外层cleanup。 |
| 外层cleanup | 只结清真实残留；已有first采用既定有限gate及其实际前提，不重跑repr、format、lookup、F3或call。 |

这是拟实施的责任分界，不是现有phase字段，也没有用名称证明已执行。每个站点的正常result/indicator合同仍须依据实际API与合法域固定；不能为了满足表格对所有primitive插入额外CheckFunctionResult、retry或source guard。V3的call结果核对与body内部primitive处理分开。

NULL且没有异常、nonNULL同时带异常、释放callback异常的总优先和全部合法可达域仍未闭合；不得由上述正常路径推断这些格都已支持，也不把它们直接列为已运行的反例。r1250私有first不冻结异常属性，A不豁免内部callback，S1不扩大到普通动态对象。

只读调查及General文本写入；候选/产品/参考Python/生成器未执行，未C解析/预处理/编译/链接/运行，未联网或改真实库。完整V3/body/selector、COMPAT和物理预算继续开放。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
