# r1528 后继入口

封存后当前完整候选为reno-makegen-explicit-code-source-r1528/working-01：23源 / 2,136,879 B。parent22源与block/caller不变，新增tag参考genobject的private make_gen接口已显式化code，三个既有caller仍现场读当前func_code。该能力没有关闭完整V3。

1. 以r1527/IMPLEMENTATION-NOTES.md的实际MakeCoro实现为证据，分开当前func flags、make_gen尺寸code、actual frame copy范围与分配后的name/qualname观察。既有本地实现已可读，不再把genobject实现缺失当成等网络的理由。未取得的header/target layout与COMPAT仍按其实际责任继续核。
2. 整合r1523/DRAFT.md的实际V3 packet、source/MOVE、callee check根、标准PyFunction owned路线与generic callable、frame reservation/Initialize/binder、eval/hook、生成器交接、结果核对和每个失败close。不得以code存活代替code稳定，也不新增当前code相等的S1拒绝，或以generic旁路替换完整owned路线。
3. 将r1526 CONSUMPTION-CONTRACT和ENTRY-PRECONDITIONS落实为真实consumer：正常前提进入primitive，失败后的规定输入释放仍带原pending；指定消费结束后才向private first交接。不要统一套预先settle的cleanup helper，也不把NULL结果当输入未消费。
4. 接r1525完整None/exec效果序列及target/seed/helper/exec/audit、普通动态变化selector和正常退休/restore_returned。原getter、两次name各在实际站点，F3、V2 ENTRY_CODE_HELD/READY/INVOKE_RETURNED持同一packet/result，不转换ABI、重定位或重呼。
5. 完整设计双审之后才继续下一源码/工具批；保独立完整预测、单次文字生成、实际与文档双审。上列不是本批已授予的全V3/source/runtime准入。

原生路线保持native C inner→实际Python/C child→同一活跃C续体，保已选F4 Python frame删除，不恢复原PC/cache或强制原evaluator inline，不引入跨C返回carrier。统计、C栈、递归、hook、GC与全部物理成本如实计账，额外参数不称零成本。

只写General、真实库只读，不执行/import候选、产品、参考Python或模型，不C解析/预处理/编译/链接/运行，不联网/诊断/安装/权限/服务/Git，不改ACL，不新代理/分支/worktree、不更新memory，仅复用原reviewer。A仅排除外部直接操作HANDLE/fd；内部allocator/callback/清理不豁免。S1不扩大普通动态域。P06无备份不再问，初始化重入未答不代选。

完整H01–H06/H02/API/CLI/UI、永久分享撤销、P06、条件T15、opaque GC、direct/alias/exec、source/capacity、COMPAT/M/S1及生产交付仍开放。完整产品目标没有完成。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
