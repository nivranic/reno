# 现代v3 GC保护修复：实施与验证

r1157已实际修复现代namespace-v3根被GC误判缺legacy锁的问题，并安装GC单文件与新增测试。精确v3根只有实际持有目录身份、完整listing和同profile lifecycle guard才免旧锁；四个精确现代锚点须与本次实际v3根关联。已有锁仍核原锁，legacy/别名/未知项继续拒绝；目录/文件/inode/fresh及EX删除门保留，无现代根的algorithm2绑定保持。双审后仅一次新q1157普通GC组件回归19/19通过，约10.547秒，无超时或cleanup失败；覆盖源变化、原锁/未知拒绝、SH不能授EX删除及另一owner的EX互斥。

runner01的profiling路线违反旧约束且计量不完整，未执行；02已完全移除。固定19案控制实读静态上界430464B<1MiB，不称实测；16份固定输入每侧529988B，源码审查pin准备额另列2MiB，不放宽产品预算。本次是普通无RP1 owner的真实GC原语，不授H01 grant、不执行Store、模型或旧诊断，正常库/服务/dist/现有权限保持。r1106 SOURCE02完整159仍未整体安装，历史封存不回写；后继H01来源组合必须叠加此次GC overlay，不能旧159覆盖回退。B001/B002 open2、H01完整design/source/execution未闭，Goal active未完成。

更正r1156：legacy facts能容纳80字符id/32KiB pending封装，只证明旧原语容量，不能证明现代H01已有实际持久落点。raw保全capsule2/source只接受namespace前缀，现代origin-v1两原件不在其范围；须另定显式新版本producer/consumer，不静默扩旧capsule2。GC修复不等于H02/H03完成。H03真实占位发布、全writer暂停与恢复，H01完整native接线/生命周期/兼容证明、P06历史缺证、条件T15及生产云仍有后继工作。

# 独立 GC 修复的交付范围

本条接续 DESIGN-01 的“候选不安装”准备阶段。完成两份源码审查、固定无 RP1 owner 的19项组件回归后，Root可安装本批独立 GC 修复及其新增测试。该执行属于已授权的项目开发，不涉及正常库、服务、现有权限或旧单次诊断。

安装产品文件仅 `reno/storage_personal_authority_roots.py`，须核当前原字节仍等于 before-01 原件，31,051 B after 必须等于 source-01 候选；新增 `tests/test_namespace3_gc_census.py` 必须原路径缺席，不能覆盖他人编辑。保留完整before、六处diff及逆像。若回归失败或输入变化，不安装且不重放本次运行。

GC在r1106 SOURCE02完整159候选集合中，但不在r1059保护清单。r1106历史封存不回写；后继H01完整来源组合须纳入本次GC overlay，不能盲目用旧159整组覆盖回退。本批安装不等于安装H01组合，也不改变B001/B002状态。

run_component_01.py和control_meter_01.py未执行且被拒绝。run_component_02.py不使用profiler、frame/locals、异常链或新增诊断观察点；只保留pytest正常结果汇总。静态控制读取上界与源码pin准备计额分列，原control1MiB、单child、19<30case、native20/parent25/cleanup≤10且不超parent等约束保持。

通过仅支持v3原字节的GC保护与原锁边界；raw保全新版合同和H03实际持久落点仍有独立工程。r1156的legacy事实槽容量不代表现代H01已有持久落点，后继文档必须保留这项更正。


# 固定19案的有限控制实读上界

这不是实测，也不适用于任意调用图、完整H01或完整GC集成。只对source-01测试的19个展开case、普通新profile、无RP1 owner、原来源门、无回调递归读文件成立。Source/解释器加载和审查pin属于准备，单列计额。

实际产品正文读入口：storage_lifecycle.py:548/645为marker read；普通guard不进入owned-plan的1092分支；personal_authority_files.py:315是原legacy锁read(2)。GC census、root_identity和私有目录验证读目录/物理元数据，不读payload。no-owner gate及未登记guard直接返回，不增加RP1业务读。另有固定config与测试结尾ordinary.raw读，纳入余量。

每case只有一个新profile、最多一个capture，最多20次view属性读取（每次至多2次guard.verify），最多4次显式verify/assert（每次至多8次），capture/owner进入退出与4项root_identity保守共留56次guard.verify。因此合计至多128次普通guard.verify。guard每次最多4根，每根read请求保守按32B计。原root_identity每项两次guard.verify，_root_ids固定4项，已计入而非忽略。

初始化、owner获取（包括竞争线程的立即失败/后续单次成功）和退出的独立FileLock.verify另留64次×32B；legacy.verify另留64次×2B；配置/fixture其余控制正文留4096B。

每case：128×4×32 + 64×32 + 64×2 + 4096 = 22,656 B。19case累计上界430,464 B，小于原1,048,576 B。不同case/profile不重用预算成功缓存；共享线程竞争测试不引入第二profile或重试循环（timeout=0）。固定输入或case变化须重审，不从此表授予新增运行。

禁止profile/帧/locals或reader替换。runner02仅保存正常pytest的nodeid/phase/outcome和这个静态上界，字段明确product_control_bytes_measured=false。


实际运行记录：data/t16-h02-namespace3-gc-census-r1157/RUN-RESULT-02.json；安装与读回：INSTALL-RESULT-02.json。没有进行真实库GC或端到端删除验收。
