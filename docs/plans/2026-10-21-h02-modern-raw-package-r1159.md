# 现代raw持久包3：候选与失败记录

r1159已完成9份产品与2份测试的现代raw-package3完整候选，未安装。本地Graph/metadata2、policy3/producer3/target3/admission3、source3副本与关闭接口、固定第三路由和v3目录GC保护均有完整before/after/diff与逆像。同一代理双源码审查后，仅新q1159一个child执行固定23case：22纯合同通过；实际case在首次request发布阶段提前失败，69阶段报告68pass/1fail，约3.391秒，无runner timeout/cleanup_failed。既有产物仅证明run01临时文件写入2636B，最终request/anchor/run-attempt均缺；失败窗口含写入结账、flush/fsync、关闭、读回及rename，未证明具体步骤或错误码。source3实际采集/复制、resume与verify均未到达；不能套用历史WinError6，也未发现可静态证明的单一根因。

单次run03已耗，不重跑、不新增诊断、不安装候选；RUN-ADMISSION的安装标志仍False，INSTALL/成功文档脚本未执行。固定fixture产品control静态上界1021952B（998KiB）<1MiB，非实测；37份固定输入单侧1006214B，显式pin/结果实际记2047098B<2MiB，准备静态上界2069254B。源码/配置保护56同、18严格缺，r1106完整159及r1157/r1158封存保持。9产品候选同01/02，03只增6个已持有bytes大小帽；无来源门patch、profiler/frame/locals/异常链，r920/r964旧诊断未重放。正常库、服务、dist、既有ACL/settings及Git不操作；fixture新私有目录按原规则建立。

当前实际产品仍为r1158采集版本，持久包3接线只有候选，没有成功持久保全/续作证据，属于未完成工程。后继先基于现有证据和静态源码缩小首request发布失败窗口，保留第一异常、关闭和原预算，不以切换fallback掩盖未知错误。完整H02下游消费者、H01完整source/caller/state/生命周期/COMPAT/准入与B001/B002 open2、H03现代pending真实落点/阶段集合/占位发布/全部writer暂停恢复仍待。r1106封存不回写；后续完整组合须保r1157 GC与r1158 census3/projection2/source3实际基底，候选未通过不得冒认为overlay已安装。P06历史七文件缺证（用户无备份）、条件T15与生产云仍待；不是只剩验收，Goal active未完成。

# r1159 现代原字节持久包与两次尝试

本批承接 r1158 采集能力，准备固定 package3 完整版本族。新增 raw3 合同、Q1 policy3、coordinator3、target3、admission3；扩展 source3 的原 operation/copy/closed 接口；固定路由增加第三实际登记，GC 保护仅追加 v3 常量与目录。旧包1/2、旧采集合同和原预算不扩张。

package facts / target_closed 版本3，source metadata / source_closed 版本2，capsule3、projection2 与完整 layout/hash 联合绑定。Graph 本地完整实现，避免继承旧模块 global/装饰器仍校验旧版；metadata validator 从原完整算法搬入并固定新版本，保留 profile/node/完整源集/空 artifacts 与 links 约束。未变的 lexical schema、JSON facts helper 和实际 IO/资源引擎允许固定复用，但不混用旧 token 或登记表。

实际创建占 slot1，原生 CREATE_NEW claim 占 slot2。claim 前仅物理元数据准入，之后验证原 request 和冻结 selection。每次尝试独立 reserve，不按已用量退款或转移；旧 formal copy 以原 record/identity/hash 验证后复用，旧未记录 payload 永不晋升。跨 source/target 的 operation budget、原第一异常、fresh 和逐资源关闭保持；只有原所有关闭成功才返回完成结果。verify 为独立只读观察，不代表之前调用已正常关闭，更不代表恢复数据库或取得 authority。

执行前完整候选/before/diff/逆像、Root 与同一只读代理两审。固定23 case：22纯合同及一个新私有 fixture 的实际 partial→resume→verify→第三次拒绝。两原件各≤8B、一个现代空目录，无 SQL/模型/正常库。仅既有 copy_record_published 故障接点产生一次合成 OSError，不新增诊断。原控制读取1MiB、case30、并发1、max2 pytest child、native20/parent25/cleanup≤10、parent360/矩阵1680均保留。本次用一个 child；source pin/结果准备另列2MiB，产品静态上界不能描述成实测。

候选通过后逐文件核 before 再安装并读回；保留全部11文件完整 before（新增为空）、after 与差异，可按 before 精确恢复本批4个既有文件和撤去本批7个新增文件；不得覆盖他人后续编辑。此说明不执行自动回滚。

H01完整来源组合仍须包含 r1157 GC overlay、r1158新模块与本批实际变更，r1106封存159不回写。H03现代pending的真实落点、阶段集合、占位发布及全部writer暂停/恢复仍未实现；P06缺证、条件T15和生产云仍在。r964/r920旧单次诊断不重放，正常库、服务、dist、现有ACL/settings、Git不操作。组件交付不等于H02/H03或Goal完成。


# 固定23case读取包络：1021952B，静态上界而非全产品实测

仅适用于source03：22纯合同案不取得源/目标；一个实际案只有两个≤8B的origin原件和一个modern空目录，create在第一个copy_record_published既有接点合成中断，resume复制/复用，verify(g2)，第三resume元数据拒绝。timeout=0，无RP1 owner、无SQL、无回调递归读取。

记P为partial整树（含failure），N为slot2新增且不含late三事实，K为bootstrap五fact，T为slot2 attempt，L为late三事实，R为request。保守帽分别23/11/20/2/21/3KiB。固定逐段读取（stage/final、双verify_pass、publisher scan、verify scan、额外request/claim及两次内容snapshot）上界13P+11N+K+T+8L+2R=616KiB。verify/exhaust后的检查仅用physical_snapshot，不重新读整包。六帽断言只读取已持有old/after bytes。

同一只读代理独立按完整字段构造内存wire包络：P18486/N8844/K15774/T1580/L19471/R2972B，均小于上述帽；L=source_verified15986+manifest2963+receipt522。固定pytest tmp_path源码把测试名截到30字符；本案实际最长profile路径的JSON表达≤83B，以100B计算；device20/inode40/time20字符，F15文件/15目录。此为合成字段计长证明，不是正常库观察或产品读取实测。

guard.verify：target read_file约185次；每次基础6次check，open_reader最多另1次，request/claim wrapper另≤20，合计≤1315；writer119、publish70、目录构造256、scan/verify listing665、verify_pass根/末尾30、record/操作/失败/生命周期杂项256、source取得/复制/退出128，合计2839，取3000。每次4根×32B原marker=384000B=375KiB。direct marker另2KiB，config/其它4KiB，源及payload小字节另1KiB。

总616+375+2+4+1=998KiB=1021952B<1048576B。目录stat/listing不冒认payload读取；显式源码pin与worker结果准备另守2MiB。没有替换reader、guard、来源门或使用profiler/frame/locals/停点/异常链。夹具、case、路径或读取次数改变需重审，不能把此有限证明扩成全产品测量。原case30/并发1/max2child/20-native/25-parent/cleanup≤10/parent360/矩阵1680保持；本批只启动一个child。


设计部分是执行前候选方案；实际结果以本文开头及RUN-RESULT-03为准。INSTALL和成功路径交付脚本均未执行。
