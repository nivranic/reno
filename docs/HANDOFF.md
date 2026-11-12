# 当前工程入口

用户已撤销暂停，恢复原方案开发，长期 goal 为 `active`。当前继续修复后端整库 CI 的剩余失败，并推进 V3 owner 与 selector 接线。

前次暂停时点的记录保存在[2026-11-12 阶段记录](AGENT-HANDOFF-2026-11-12.md)。

恢复开发的起始提交为 `60074e2`。本轮已修正普通存储的 Python 3.11 导入兼容、Linux 锁清理夹具、Windows 专属模块收集门以及预算化同步场景。Linux／Python 3.12.10 的完整同步恢复链已通过。后端 CI 固定为 Ubuntu 24.04／Python 3.12.10，以匹配既有 selector 合同和官方解释器构建，最低版本 checkpoint CI 保持 Python 3.11。本轮各批结果、历史失败和待验证范围见[继续开发记录](closeout/2026-11-12-ci-resume-result.json)。

[2026-11-07 阶段交接](AGENT-HANDOFF-2026-11-07.md) 保留累计产品范围、r1528 静态候选身份、历史运行限制和剩余设计／开发事项。完整产品目标仍未完成。

`a4f116c` 的六项 CI 成功，后端整库结果为 12,389 通过、490 失败、93 错误、2,313 跳过。后继已修订序列化缓存基线、两组历史快照前提、原16解析／Home生产者、外部QA实例目录及resolver测试接口。已修复原发布确认完成后仍读取旧pending状态的恢复缺陷，两个完整相关模块通过64项。额外原Home回归通过76项，最终Home writer／QA模块通过62项。前端类型检查通过，两个worker的完整Vitest复检通过314项，原失败及退出码不一致记录继续保留。各批原始失败、重叠范围和当前待验证项见[继续开发记录](closeout/2026-11-12-ci-resume-result.json)。本批完整远端结果以该提交的GitHub Actions为准。

`849bb43` 已推送并核对远端主分支，CI 43229352903 的六项作业通过，后端以失败结束，原日志记录12,771通过、136失败、27错误、2,351跳过。后继隔离复现确认原预算等待／来源控制两模块为32失败、19通过。将两模块完整原控制与清理生命周期接入既有released16夹具后，连同原来源控制迁移模块共62项通过。原测试主体保持一致，本批仅调整测试前提。完整后端CI和其余历史／现代夹具问题继续处理。

原Home映射／恢复、纯版本保持断言、现代机会测试及历史零claim夹具的最终七模块回归通过233项。原复现33失败／165通过、首版2失败／200通过均保留。映射测试核对恢复启动保留原work，随后正常轮次复用同一generation完成发布。现代正向会话／控制观察取自真实隔离Cloud，异常pull沿既有负向注入接口测试。

历史原点、17-v1聚合reader、原终态发布及原聚合根四模块回归通过85项，跳过1项。原16创建范围先退出，随后显式安装声明的历史17-v1读侧变体。原控制／原点生产者和测试主体保持既有实现。

后端CI的pytest临时目录改用runner临时目录，满足现有隔离QA目录边界。相同HTTP／TLS套接字和隔离门用例在仓库内临时目录为11失败、82通过，改用仓库外临时目录后93项全部通过。生产隔离校验沿用既有实现。首次仓库外复检另有一项套接字服务端结束等待失败，单项观察及最终整组复检通过，原等待失败原因未确认且记录保留。YAML解析及其余workflow字段一致性已核对，完整远端结果继续跟进。

旧回执尾续作的两个独立捕获工厂已补齐共享读取作用域，按各自原seal和上下文绑定真实reader。公共捕获先失活，随后在原owner和SH仍持有时完成最终验证、reader关闭及作用域退休。首错收集沿用异常基类的原始traceback成员，覆盖异常子类覆写读取的情况。修复前三模块为32通过、15失败，修复后连同既有创建作用域和新增生命周期回归，五模块共64项通过。首轮新增复制测试断言位置错误已修正，原失败记录保留。

独立锁组测试子进程依赖的原审计脚本已按原字节纳入测试目录，并接入既有pytest隔离目录校验，保留历史runner子树合同。完整锁组与新增目录边界测试共213项通过，11项Windows专属用例跳过。本批各次原始结果与范围见继续开发记录，完整远端后端CI仍在跟进。

V3 的 r1530 已生成并双审 29 源／2,537,465 B 的静态运行时候选，包含 code／closure 配对、frame 模式、V3 调用、V2 同次续作及栈分配／释放接点。完整 owner／None／exec／selector、C 构建、ABI、COMPAT 与运行验证仍待完成。

V3 的 r1531 已准备并双审八份owner／F3／None／exec片段，共25,995 B。完整dispatcher的source／authority接点、selector与退休／恢复仍待组合，完整C候选尚未生成。

V3 的 r1532 已把 r1531 片段组合为29源／2,562,791 B的部分源码文本，主核和同一名只读代理均核对实际输出及完整逆像。working-01为3变26保，working-02另修dynamic producer的3行根索引，按metadata row4把globals／builtins对应到73／74，28份保持。入口域文档已核对原S1与固定F4 body删除的实际边界。完整dispatcher定义、逐站source／authority消费者、selector及正常退休／恢复、C解析／构建／ABI／COMPAT与运行验证仍待完成。

V3 的 r1533 已补入 CLEAR 回调中的同代数据存活借用，沿原metadata计数与outer清理结账。29源／2,563,551 B，主核和同一只读代理复审通过，完整逆像与28份父源保持已核。有限Python条件模型修订02共226项通过，包含补齐的4项计数上限边界。原222项与修订02范围重叠。四处inner创建及22处Record phase writer的独立文本核对已完成，合法创建路径先完成prepare→Track，构造回调中的退休按实际phase处理。

V3 的 r1534 已固定S1的binder前entry身份漂移消费点，实际比较code/defaults/kwdefaults/closure/G/B及已捕获typed slot。29源／2,566,341 B，主核和同一只读代理核对实际输出及完整逆像，182项有限Python条件模型通过。当前候选位于General的reno-loader-entry-identity-consumer-r1534/working-01。完整dispatcher、原非标准/NULL调用槽及其它入口、逐站source/authority消费者、selector和正常退休/恢复、C解析/构建/ABI/COMPAT/预算与运行验证继续推进。源码文本仍处于静态候选阶段。最近一次核对中，47f26b8的六项CI作业成功，后端整库仍执行pytest。

本轮继续依据849bb43的完整失败日志核对当前f766c4e基线。四项隔离复现为1通过、3失败，历史17图像、真实注册回执及缺少真实手动恢复SQL回执的断言修订后，完整相关组合通过83项。新增只读schema连接补充显式关闭后，双设施恢复单项再次通过。缺少真实SQL授权的合成budget_pair已改为拒绝用例，真实手动授权的两个不同hash仍由实际生产者套件验证。

共享ASGI/SDK测试transport已复用现有response/close桥，原始复现7失败、36通过。调度用例补齐原控制注册，读取实际交付回执并核对原schema全局保持，五模块组合通过61项。共享helper及相关隔离模块交叉回归通过266项。各批范围有重叠，记录分别保留。完整远端后端结果和其余旧失败继续跟进。

6947aba后端原日志记录12,584通过、49失败、301错误。301项setup错误源于两份材料夹具仍要求仓库内QA路径。改为核对pytest实际basetemp后，连同普通终态、bootstrap和原no-resend完整模块通过510项。普通终态夹具显式移除共享bootstrap marker，原bootstrap待修复行为继续由既有完整模块核对。旧peer缺少source_continuity的AttributeError已通过原异常位置确认，其完整测试生命周期接入released16。现代HTTP双节点通过真实CLI初始化存储并激活Cloud连续性，复用既有SyntheticSourceRunner接入当前解析/下载接口后，HTTP正常与丢回执两项均通过，属于前述266项交叉回归。丢回执原始任务状态为pending/blocked/indeterminate，回归沿真实resume_sync恢复同一job_id，同时继续核对原发布内容重放与Cloud单次提交。原扩大回归的6失败、260通过及CLI前提失败记录保留。

V3的r1535已补齐原NULL-slot的实际consumer、声明及唯一inner调用点，29源共2,572,313 B。主核与同一只读代理核对完整逆像及26份父源保持，128项入口条件模型与68项路径/owning模型分别通过。新增官方specialize与pycore_function文本闭合CALL/getitem正向cache producer，最终guard仍非标准slot的有限排除已双审，保留正常setter和有效cache不变量前提。custom显式标准C委托、改标准slot的后继消费、真实native binder/body、完整dispatcher/selector及退休恢复继续推进。C解析、构建、ABI与运行验证仍待完成，长期goal为active。

后端整库中的真实同步场景按React配置启动服务，服务日志观察到缺少frontend/dist/assets。后端CI现已补齐Node 22及既有npm ci/npm run build步骤，YAML两项新增步骤和其余字段、完整逆像已核对。本地真实前端构建成功，隔离副本保留859份Python源码并加入49份构建资源。首轮扩大回归和首轮带构建回归均因执行器／WSL重启中断，完整结果缺失，原pytest日志保留。两份原TEMP服务日志已在重启前读到，重启后源文件缺失，未保留其原始字节；这一证据边界单独登记。确认原进程退出后，两组回归已用独立进程和PID／启动身份回执恢复，最终结果见继续开发记录。304e186的六项远端作业已成功，后端整库仍在执行。完整产品目标继续为active。

构建前提修订b935240已推送并核对远端SHA。补齐真实构建的两个子进程场景模块6项全部通过，完整来源恢复模块23项全部通过，JUnit的29个具名用例互不重叠。这些完整回归分别耗时约649秒和1241秒，早先中断运行单独保留且不计为完成。304e186至b935240的Python变更仅为夹具注释，AST一致性已核对。b935240的六项远端作业成功，backend131398631479仍在执行整库pytest，完整远端结论继续跟进。

V3的scoped合同跟进已由同一只读代理完成有限复核。现有frameless binder可直接复用，版本查询前置零返回与producer/backoff、generic/specialized回退及opcode映射的有限文本依据已登记。29源身份核对一致，本批working源码修改为零。下一源码计划位于General的reno-loader-scoped-custom-slot-r1536/DESIGN.md，正在复核V1/V2真实终态、V3分流、typed slot与递归责任。完整dispatcher、效果站点接线及C解析／构建／ABI／COMPAT／物理成本和运行验证继续推进。
