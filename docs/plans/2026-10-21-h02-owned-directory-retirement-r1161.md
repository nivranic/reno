# 原目录关闭状态：已应用修复与验证边界

r1161已修复并应用一处可静态证明的目录生命周期缺陷：原_OwnedDirectory.__exit__只清handles，遗漏基类_known_closed/fd/_close_failure，使关闭对象仍可能走继承路径I/O或传递旧数字fd。新方法在closer前退休状态、detach本次句柄，逆序尝试全部原closer，独立保留首个close错误，原primary优先且可选note失败不打断余下关闭；重复exit不重试本次旧数字。正常成功reenter仍沿原base语义，verify本身未改变，不冒认完整并发/重入协议。完整源与测试双审后，全新q1161单child仅执行14纯模拟case：14通过、42phase全pass，约0.703秒；没有timeout/cleanup/输出溢出/reader或monitor失败。结果及精确应用再独立复核后，仅应用1产品方法所在完整源码与1测试文件，并逐字节读回。

候选source37281B/test7303B，原source36649B；完整before/after/diff/逆像保持。测试body用Fake closer及后端拦截，不执行目标native文件/目录I/O；原DLL正常import仍发生，不将14模拟通过写成真实句柄/完整发布验收。8KiB产品control为本scope静态额度，非实测。完整top-level相对import闭包45模块1163919B均保留，50输入单侧1184325B、manifest10915B；parent核manifest/admission和runner自身，worker在任何产品导入/编译/pytest前完整pin，准备上界1263421B、实记1236011B<2MiB，child pin1195240B，原全部时间和child限额保持。保护56同/18严格缺、r1106完整159与r1157/1158/1159/1160旧封存56/78/108/63同；raw3四live before/七新目标保持。旧运行所需legacy source的live字节已按本批授权升级，不声称旧manifest的该live输入仍等历史hash；旧封存字节不回写、不重跑。正常库、服务、dist、既有ACL/settings和Git不主动操作，服务未重启，现有进程载入状态未验证。

本修复是独立生命周期缺陷，不是已证实的r1159/r1160首次request发布失败根因。其公开PUBLICATION_FAILED仍未定位，持久包3九产品候选仍未安装，source3采集/复制/resume/verify完整成功仍无证据。当前源码基底是r1158加本批raw目录关闭修复，不能冒认为raw3持久包已生效。H02完整下游、H01原source/caller/state/生命周期/COMPAT/准入及B001/B002 open2、H03现代pending真实落点/阶段集合/占位发布/全部writer暂停恢复仍待。P06七历史身份用户无备份，不再问；条件T15和生产云仍待。r920/r964诊断授权耗尽，不加异常链/frames/locals/profiler/marker或微型native诊断。不是只剩验收，Goal active未完成。

# r1161 原目录关闭状态修复

源码已证 `_OwnedDirectory.__exit__` 覆盖基类后只clear handles，遗漏 `_known_closed`、`fd` 和 `_close_failure`。四个继承路径操作与作为新子目录的parent据此可能在close后继续；POSIX会留下已关闭数字fd。该独立生命周期缺陷不等同于r1159/r1160首request发布失败，后者根因仍未确认。

候选仅改这一方法：原primary判定不变，close前退休状态并detach句柄；逆序尝试所有原closer，实际close首错独立保留；原primary优先，不让可选note错误中断close或替换first；重复exit不重试旧数字，失败reenter由基类拒绝。正常成功后的reenter仍沿基类行为，不承诺清空旧identities或取得新授权；verify自身仍为原语义，不冒认所有方法都拒绝。

14个纯模拟case不打开任何实际目录/文件，不把假句柄传原native。实际closer全部替换为内存函数，继承路径操作的后端有拦截；检查四操作/parent拒绝、close前退休和重入、不重试、allclose/primary/firstclose、note附加失败和partial opening。测试不读隐藏异常链、trace/frame/locals/notes，不加产品观测点、profiler或微型native诊断。

完整source与测试、before/after/diff/逆像、独立复审、精确manifest与预算通过后才执行普通模拟回归。原单child/native20/parent25/共同cleanup≤10、case≤30、parent360/matrix1680/max2、product control≤1MiB、显式准备≤2MiB保持。旧r1160与r1159封存不改、不重跑；正常库/服务/dist/既有ACL/settings/Git不动。

完整顶层相对import依赖45模块1163919B，原父子双读超预算；runner修订02保留完整闭包，移除parent未消费的重复源码读取。parent核manifest/admission及runner自身，worker仍须在任何产品导入/编译/pytest前核完整全部输入，启动即全额计其准备额度，报告校准准确bytes/hash。此项只消除重复准备I/O，不弱化实际执行前的完整源码核对。

通过后只允许应用本方法修复及其测试，不安装r1159/r1160持久包候选，不改变r1157 GC/r1158 source3等既有产品基底，完整Goal仍待原全部工程工作。


# r1161 纯模拟回归预算

14个case只构造Python目录对象、假数字句柄及内存closer，不进入实际产品目录/file或native文件API；实际close函数在fixture中全部替换，路径调用被要求在继承guard返回，后端拦截防止错误用例触及真实路径。正常模块导入包含原DLL加载与API声明，不冒认为整个Python进程零OS操作。note只测试附加失败的行为，不读取异常notes或隐藏链/trace/frame/locals。没有产品树、SQL、grant、来源门patch或新诊断。

测试body的产品control读取为0；模块import留8192B保守control额度，低于原1MiB。Python源码pin及pytest报告单独计算，不冒认全产品控制读取测量。

完整top-level相对import闭包45模块1163919B，双侧重复读取会超2MiB；修订02保留全部依赖，不扩大限额。parent只核admission、完整manifest与runner自身；worker在任何reno导入、overlay编译和pytest前逐项完整读取/hash核全部输入。启动child即按manifest+全部输入的准确额度扣账，worker报告必须与准确额度/manifest hash一致。

准备上界为2×RUN-INPUTS字节+全部输入字节+runner自身字节+准入4096+worker report32768+pipe8193，生成器精确重算必须≤2MiB。pipe沿已审runner最多读取8193、保存8192，启动child即计满不退款；最多14×3=42个阶段，单child串行，native20/parent25/共同cleanup≤10、原parent360/matrix1680/max2、case≤30保持。

尚待同一只读代理复审及精确manifest复核；旧r1160和r1159单次记录不重放。通过模拟回归不等于Windows native或完整raw发布成功。


以上设计/预算为准备时说明，实际以本文开头和RUN/INSTALL结果为准。回滚须先核live仍等本批after hash，再恢复before-01原源码；本批新增测试只有仍等候选hash才可移除，不动其它脏文件。当前未执行回滚。
