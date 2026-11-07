# Task6D 四类DTO、预算会话与实际调用者来源接线

2026-10-17，r1034 DESIGN01。沿用r1026 DESIGN02的目标、嵌套矩阵和全部限制；当前基底为r1031 SOURCE03，最近实际检查为r1032/r1033两批103尝试、54唯一ID最新通过，不能外推本批。

## 已核来源与完整实施范围

r1034有限调查冻结47份完整来源、1552706字节，覆盖拟组合36个文件及指定依赖。四个原DTO有7处构造：Execution三处、ModelScope一处、SourceScope两处、PreparedSource一处；55条指定调用清单仅是句法事实，不排除动态或别名。调查01误读不存在的manifest.file键，在复制/产品执行前退出；02仅从实际candidate路径还原文件名，原错误草稿保留。

完整候选在data内形成36文件：r1031的23文件全部保留，新增进入组合的13个现有完整原文件为model_scopes/model_budget/source_budget/source_runner、import_tasks/pipeline/ask/atomize/judge/vlm/report，以及两个维护脚本。worker/sync_agent同步修改；其余21个前驱文件逐字节继承。不安装正式reno/app或c973。

四DTO的定义、字段、decorator、构造参数和普通copy/pickle/repr保持。特别是atomize.Prepared/Generated并非这四个DTO，也保持定义，不往它们加source。私有_Session和Context frame可以持外置封套，外部适配器、参数、持久任务、SQL、控制输出和网络内容不携owner/source。

## 固定消费者与外置持有

每个固定消费者在其模块初始化时捕获原DTO class及其原profile slot descriptor。先做原exact类型门，再用捕获的C slot读取profile；不能先泛读任意对象的profile，也不能让调用者传入expected class/descriptor。route仍只核实际dto/profile/source配对，不承担业务类型和SEAL校验。

Execution的_current由原DTO值改为明确的二元frame `(原Execution, envelope或唯一omitted)`；current_execution仍返回exact原DTO。frame为局部Context值，不进外部adapter，不新增全局profile→owner目录。解包先核frame形状、DTO原类型/slot，再对严格envelope走原route解包。丢字段、坏envelope、None或同identity其他实例拒绝。

ModelScope和SourceScope各自的私有_Session在原字段尾增加profile_envelope及独立原绑定。绑定保原DTO与原envelope身份；每次消费先核当前字段仍与原绑定相同，不能把严格session改为omitted降级。ordinary仍保原DTO业务语义。current_scope返回原DTO，另一个私有getter只返回当前显式frame/session已持的source，不按identity寻找owner。

构造source仍由实际调用者先持owner，再原profile构造，再pack；绑定或封套失败时原调用者/原异常tb继续持owner。封套不是操作root、预算许可或物理关闭回执。

## Execution、ModelScope和发送链

execution新增私有_profile_source参数，默认唯一omitted。先保原consumer/selection/connection/new_execution校验；有父frame时沿r1026七行矩阵，按实际被复用的Execution.profile选择source。父严格且本次省略，保父原source；显式其他source拒绝。父普通可临时为同一实际profile选入source，内层退出恢复普通父。原同identity不同实例的ordinary复用继续；不创建另一个Execution规避原复用语义。

没有父frame而显式source时，在_manual及其SQL之前核输入profile/source；随后在真实Execution返回后包装实际DTO。worker构造分支仍用原job/continuation/generation门。call先按原type/current DTO门，再实际解包当前frame，并把同source显式传入ModelScope scope。admit closure每次仍核原frame与原业务准入，不把来源当作新业务许可。

model_budget.scope新增私有source参数，私有Session持envelope和原绑定。父严格session不能被省略参数擦掉；新DTO使用父source时必须持同一profile实例。current_scope与request_once实际session选择处核绑定，传输前/原guard回调内再次核同一session；返回后持source到原结算/异常路径。原paid recovery先于业务准入、HTTP重试/ordinal、冻结payload、预算与SQL合同保持，不把source加到payload或request hash。

原ordinary scope的校验、Token和回调顺序不因未选择source改变。严格scope要覆盖部分Context set：保存每个实际返回Token，finally按原逆序逐项reset一次；前一个set失败也清理已返回Token。保首个原异常及tb，后继reset错误不能盖首错，不用重新set或重试reset补历史。set已抛且无实际Token不伪造reset成功；异常tb仍持原frame/source，不能宣称Context或custody已退休。

## SourceScope、三个prepare点和实际runner

source_budget.scope/source_scope/home_source_scope各新增私有source参数，scope实际Session持独立原绑定。worker source_scope的实际profile必须配worker原source；home_source_scope以真实binding.profile为DTO profile，由Cycle实际caller显式传其对应source。Home binding与worker可以同identity不同实例，不能把worker source借给它，也不能按当前配置重新pack。原worker job/业务identity、home binding/客户端/取消与现有门保持。

_prepare仍调用实际runner.prepare，保policy/cookies/extra/resume/verified_files求值和返回。ordinary省略走原签名/返回，不强迫既有合成runner接受新关键字。严格模式只允许该包实际source_runner及其固定PreparedSource消费者工具；未知custom runner明确拒绝，不能fallback普通路径。

runner.prepare/verify_prepared_files/run增加私有source参数。原exact PreparedSource/SEAL/plan/timeout等业务门保留，source配对检查在各自原资源访问前；运行参数和子进程内容不带source。直接prepare仍返回exact原PreparedSource，绝不把外置封套作为公开返回类型。

严格_source_budget._prepare在实际prepare返回后，用runner内部固定class/profile descriptor工具包装实际PreparedSource。run_source里的preflight→verifier、guard里的第二prepare、实际run前第三prepare均持其实际返回；每次给verifier/run前显式解包并复核expected source身份，原runner仍收到原PreparedSource。run内部调用verifier也显式传同source，normal omitted时不新增关键字破坏旧fixture签名。guard中未消费的prepare结果也保持原检查与调用次数，不重建旧payload、不更改resume/verified_files/argv或原能力门。

## 实际调用者同步

| 文件 | 必须接通的真实链 |
| --- | --- |
| worker | ingest/favlist实际dispatch将原worker profile/source显式传import_tasks；process将source传pipeline；judge将source传judge.run；summary将source交原report装饰入口。原sync分派保持6C source。 |
| import_tasks | run_ingest/run_favlist新增私有source并先核原profile，两个source_scope显式传同source；导入后实际run_video继续传它。 |
| pipeline | run_video显式接收source，原profile选择处核同；vlm/atomize两类实际mod.run传原profile/source，其他步骤签名和原处理保持。无worker的配置fallback在原profile创建点显式owner→profile→source，配置读取顺序/次数保持。 |
| sync_agent | 两个home_source_scope使用Cycle的source并核真实binding.profile。advance_work的pipeline实际选择worker profile，显式交worker已持source，不能误交Cycle source。 |
| atomize | run/generate增加私有来源传输；原Prepared/Generated不变。实际generate→execution传参数；已有父Execution时省略必须沿矩阵保原父source。 |
| vlm | run及实际execution传来源；原from_connection/profile业务选择不改，不把同identity结果补签。 |
| judge | run→_run→execution传同source；source在备份/读取前核同，原备份/保留/事务逻辑保持。 |
| ask | 保原worker/父Execution/配置profile选择优先级；真正配置fallback原地显式创建owner/profile/source。已有父Execution时按实际复用DTO保父来源，不误把另一个输入profile source装到父DTO。实际execution选入该来源。 |
| report | 原_managed装饰器接收并消费私有source；按原_profile优先级确定实际value后先核pair，_generation私有frame保source及原绑定；summary实际execution显式解包。保原status与finally reset、外部report schema。 |
| backfill/rerun脚本 | 原main配置创建点显式持owner/profile/source，传实际_apply→execution；实际二级调用借原Execution的已有source，原预览/显式apply/选择/锁/备份和外部参数保持。 |

对这些调用者，只为实际原profile建立来源；from_connection选出的其他未有构造来源的实例保持原ordinary可用性，不从同identity worker/current config补签。显式严格请求遇到该实例必须拒绝。普通API/CLI的签名兼容与业务identity语义继续；本方案不是把所有普通调用强制改成严格owned操作。

## 检查与交付

完整36文件给出原件/候选/逆差分/完整diff与逐字节继承证明，四DTO AST和字段精确保持，列所有实际private参数及调用site。主代理和同一只读代理先复核此实施细化；完整候选另做两源码审，随后六脚本和实际准备双审、新claim单次具名检查。普通开发与复用该代理已授权，不重复问。

具名模型必须选入原execution/call/current、两个完整budget scope/Session、实际run_source三个prepare/一个verifier/一个run、固定原PreparedSource工具和真实worker/import/pipeline/Cycle/消费者caller；不能只测新helper。覆盖七行nested矩阵、缺字段/降级、四DTO普通copy/pickle和无owner JSON、None/同identity异实例、三实际返回envelope、真实ContextVar及部分set/reset首错、原ordinary runner fixture签名、source不进外部payload/argv，以及carrier/原tb寿命。SQL/native/真正模型网络或yt-dlp进程可合成，但必须逐项标明，不能称真实平台通过。

case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest children、新批max1不变；原Popen ownership/finally/kill-wait/raw-first保留。失败先保完整原记录，只重检必要失败ID；旧6A–C绿批不重放。无profiling/frames/locals/停点或定点计时诊断。

Task7最后借用、unknown债务pending/容量拒绝/恢复/最终释放仍未实现，_finish_operation仍拒绝。完整物理/marker/费用容量/完成图/final/正常父退出/恢复、原111与94/r919、Store/source_closed/live witness/写权、H02–H06/永久分享、P06历史、条件T15和生产云均有工程工作。canonical/c973本段apply0、历史正式累计11、产品pass0，完整Goal active。

正常库、服务/dist、既有ACL/settings、真实平台及Git保持；不新增代理/分支/worktree，不commit/push。r920/r964已耗不重放；r967待原专门问题不执行/重问；P06历史0/7无备份不再问。回滚为保留data候选及原红/前驱字节，不覆盖正式树。
