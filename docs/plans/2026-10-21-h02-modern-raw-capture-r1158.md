# 现代原字节采集：已实现与后继

r1158已新增并实际安装census3、projection2和固定capture_raw3_locked三模块及两份测试。新版可在原真实EX/资源/预算/fresh/first/close边界内采集现代origin-v1两原件、namespace-v3两锚点及目录内root-seal；exact角色与opaque内容分开，坏JSON原件保持原字节，不推断grant。新版独立exact token/LIVE/CLOSED，旧新API双向拒绝混用。旧census2/projection1/source2全文未改，raw持久包1/2未扩。两份源码审查后仅新q1158两串行组完成12项纯合同和12项真实文件/锁采集回归，共24/24通过、72条阶段报告全passed，约7.094秒；各child均在原20秒内完成，无超时或cleanup失败。

产品固定fixture控制读取静态上界471040B<1MiB，不称全产品实测；21份固定输入每侧636005B，显式pin/结果准备另列2MiB。runner02在child启动后即保守计完整pin，修正01失败无报告时可能偏低的记账；01未执行。stage01缩进锚点断言红及纯测试不完整limits预期错误均保留说明，修正后才准入，未改产品限额语义。本批不patch来源门、不用profiler/frame/locals/异常链；未运行H01活授权、Store、模型或旧诊断，正常库/服务/dist/现有权限/Git保持。

H02尚未完成：持久raw-package3/target3、固定路由第三登记、source-metadata2完整validator、请求/投影/capsule/副本消费者仍须接线。raw2委托的raw1 metadata validator硬编码format1/capsule2/projection1，新包不可继续沿用。本次没有持久副本或恢复能力，不能从采集/解码通过推导保全完成。H03现代pending真实落点/全writer暂停/恢复，以及H01完整设计/source/caller/state/COMPAT与B001/B002 open2仍有工程。r1156的legacy槽容量不证明现代持久落点；r1106封存159仍不回写，未来完整组合须包含r1157 GC overlay及本次三个新模块。P06历史缺证、条件T15与生产云仍保持原待项；Goal active未完成。

# r1158：现代原字节采集版本族

上一轮r1157已修GC兼容并实际安装，本轮继续H02，完整目标仍含持久保全与恢复。本阶段新增census3、source projection2及固定capture_raw3_locked实际采集/解码入口；不将该阶段冒认为raw持久包3或H02完成。旧census2/projection1/source2以及raw包1/2字节不改。

选择独立版本模块并复用旧纯helper、真实资源及预算实现。直接扩大旧前缀会改变已发布合同；给调用方传入任意codec/factory则破坏固定来源，因此不选。新source3持独立exact token与LIVE/CLOSED表，避免旧API反向接纳新capture。两代capability互相拒绝。

census3保持旧namespace前缀范围，仅显式增加origin-v1两个原件（casefold只用于选择，实际原拼写保留）。origin两顶层、namespace-v3两顶层和目录内root-seal的exact角色固定；旧路径仍原c2角色，其它现代内容opaque，不解析grant/任意JSON或自动沿用Store2角色。layout/hash记录该完整选择与分类声明。未知authority/origin范围不能过滤成成功。

source3固定构造_Source3。仅精确namespace-v3实际打开并核身份、完整listing的目录可缺legacy锁；若有任何authority.lock候选，仍原_MeteredLock获取/验证。其它authority根保持旧锁要求。源profile真实EX、全部目录/文件/fd、fresh与回调/重入、原预算和第一异常、关闭/闭合证据保持。采集不创建锁、数据库、grant或副本。

完整before与派生对应、固定输入、Root和同一只读代理源码两审后，才准入一个新QA目录中≤30case隔离回归；不patch来源门、不用profiler/frame/locals/异常链。产品control1MiB/并发1/max2 children/native20-parent25-cleanup≤10保持。纯helper与固定fixture读取上界另审；不能把源码pin准备额混作产品实测。

后继必须实现raw-package3、target3、固定路由第三登记与完整source-metadata2 validator、请求layout/version、projection/capsule/copied bytes一致校验，不能继承raw1硬编码metadata1 validator。H03现代持久落点/占位发布/暂停恢复和H01完整设计/源码/执行仍未闭；旧r1106封存不改，未来完整组合须叠加r1157 GC及本新增模块。旧单次诊断不重放；正常库/服务/dist/现有权限/Git不操作。


# 固定24case的控制读取上界，非实测

仅适用于本批两固定组：pure12case与source12case、每案一个新profile、最多两个capture、timeout=0、无RP1 owner、回调不递归读取文件。case/输入改变须重审。

真实Source组每case保守：128次普通StorageGuard.verify×4根×32B=16384B；64次直接lifecycle.FileLock.verify×32B=2048B；至多两次capture×8192B=16384B（原_MeteredLock读2B也走同一个raw budget，不另漏计）；另4096B配置与fixture原件assert读取。合计38912B×12=466944B，pure组再留4096B固定配置，共471040B<原1048576B。

原capture core每次实际9次require_guard，当前fixture只增加固定读取/显式verify/进入退出/竞争线程，远低于128/64包络。失败的SH入口不取得token、不作源读取。有效锁/1B失败限额案与capability案最多两次capture，均已计入。

目录、inode与listing是物理元数据，不冒认payload实读；新版复用原_Budget累计读取且从不重置。同owner变更与非协作修改仍由fresh检测，profile锁不宣称禁止它们。

没有profiler、frame/locals或reader替换；显式源码pin/worker结果读另以2MiB准备上限计额。全产品471040B是静态界，runner字段不得标为实际测量。两pytest child严格串行，各native20/必要报告读取parent25/cleanup≤10且不超parent；总parent360、矩阵1680、case24≤30、max2children保持。


实际结果：data/t16-h02-modern-raw-capture-r1158/RUN-RESULT-02.json；实际新增及读回：INSTALL-RESULT-02.json。未对正常库执行采集、持久保全或恢复。
