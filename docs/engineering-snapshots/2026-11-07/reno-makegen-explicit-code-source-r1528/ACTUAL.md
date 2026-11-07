# r1528 实际结果：make_gen 显式 code 输入

本批将既有 private static make_gen 的分配code输入显式化；三个原_MakeCoro caller仍各自传调用时的func_code。没有实现完整V3，也没有把当前function code读取改成earlier held frame code。

## 实际源码

当前完整候选为working-01下23源 / 2,136,879 B。before-01为23源 / 2,136,818 B，由父r1524的22源 / 2,058,033 B加既有tag genobject参考78,785 B组成；不是声称父源集本来已含genobject。

仅runtime/Objects/genobject.c.txt发生四组变化：make_gen增加PyCodeObject *code形参并删内部局部读取；generator、async-generator、coroutine三个caller各自加当前func_code实参。新genobject为78,846 B / 03c6f81f803c0abbc1e8dc1aa225c1b453d710b076822638771d08c8e9b9e1cc，较参考增加61 B。FULL-EDITS.diff为1,345 B / 7ab3ae8362818f9e048eb2519abf6d38e2703b18a9693bc290d63c568f163d17。

原make_gen其余正文、_MakeCoro flags与三个调用实参以外的全部分配/初始化/name/qualname/origin语句保持；parent22源、selector block、r1507四caller材料逐字不变。字面func->func_code站点从2变4是原一个callee读点迁到三条caller分支，不是动态发生次数或时间等价证明。

## 执行与验证

设计双审c1ee23/fd0b92通过；dbb42f准备原字节独审设计对象、EDITS和文字工具。主2b1ef2仅读控制工具Python AST并独立重构51项完整预测，独立源码/工具核6d4515、565b61通过；准备对象经1849ef原字节转录，单次文字生成102730通过。实际/文档核对回执见FINAL-REVIEW.json。文字工具只执行一次；没有运行任何产品、C候选、参考Python或参考生成器。

4组编辑可逆恢复全部78,785 B参考；actual51项须逐项匹配主核和独立预测。原122继承布尔逐值保持48 true/74 false，source_only_closed_fields=[]。输入reference root通过CLI提供，仅只读固定项目相对文件并核身份；本机绝对路径不写入工具或产物。

当前取code的位置从private C helper第一语句移到实际调用的实参求值；所读源文本之间没有显式Pythoncallout。新增C参数、删局部、编译器序言/stack probe、私有C ABI与物理成本均未验证，不能从文字还原推导runtime等价或零成本。未C解析/预处理/编译/链接/运行，未联网/安装/诊断/权限/服务/Git或改真实库。

## 后继

以本批23源作为下一源码基线。下一步整合r1527的MakeCoro当前flags/current allocation code/actual frame Copy读点、r1523 V3 inputs/MOVE/frame/binder/eval/result-check/close与r1526 primitive消费/正常入口/失败pending/outer first，然后接完整r1525 N/E body和真实target/seed/helper/exec/audit/selector消费者。

显参能力不等于V3 code-view选择准入；三个现有caller保持原当前func_code，不在后继未经完整设计将其换成早期guard。生成器实际分配安全、合法code变化、closure、错误优先、目标ABI/build/runtime、COMPAT、物理预算与完整产品交付仍开放。保持同一活跃native C caller与F4删除，不恢复原F4或跨C返回carrier；A/S1及P06、初始化重入边界保持。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
