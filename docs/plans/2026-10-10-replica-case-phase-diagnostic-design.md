# r765：具名 case／阶段记录设计 r02（仅设计）

本稿采用 brainstorming 的两方案比较。原矩阵 wall 已到 1680.031 秒；八个 stdout 点不能绑定具名 case。此事实来自 Root 交接，本稿未读运行日志、查询进程或重跑。旧 claim 已耗，旧红保持；本稿不准入任何运行。

## 两方案

**A，推荐：三个最小 pytest 边界钩子。** 保留原 12 项选择、顺序及 1680 秒聚合 wall，在后继源码中记录实际 collection、protocol 进入／返回及 setup／call／teardown 已生成报告。不触碰测试正文、原生单元、原 first 或清理。它增加输出 IO 和执行成本，不能声称原运行效果等价；需新源码独审、完整冻结、Root 另准入，本轮不准备或运行未来 same12。

**B，辅助：两个纯 oracle 独立子 lane。** 固定原函数的 `[1]`、`[129]`，实际 collection 必须恰为二项；deadline 和 claim 必须单独设计、独审、冻结，不能沿用或迁移旧 claim。保留原 12 项／1680 秒要求及全 93 项目标。纯 body 不调用 DATA/G1，但 collection 仍导入原测试模块，不能称零原生效果。其结果不能替代十个 native 项，不能说明旧纯项是否进入或补绿旧 run；它也无法解释原生矩阵的阶段，故不是主路径。

## A 的最小观察边界

仅新增 `pytest_runtest_logstart`、`pytest_runtest_logreport`、`pytest_runtest_logfinish` 三个普通非 wrapper 钩子，返回 `None`。logstart 只证明 protocol 进入；logreport 只证明某阶段报告已生成；logfinish 只证明 protocol 返回。报告在阶段结束后到达，不能区分中途正在 setup／call／teardown，也不证明正文开始。最后报告之后的未完成区间保持 unknown。三个阶段入口钩子会增加 IO／信任面，本次不纳入；如后继确需入口观察，应另设计、审查，仍不能定位阶段内操作。不用 wrapper、clock、trace 或正文插桩弥补未知。

仅 collection 沿用原 LongIds 的 nodeid 读取及缩名规则，新增 exact-string 表：索引、实际原 nodeid、最终 nodeid、原 nodeid SHA256、声明 native／pure 类别。依据冻结源码预期集合检查数量、顺序和唯一性；最终白名单来自本次实际 collection，不从参数对象、repr、日志点或静态十二项猜出。完整合法字符串仅写一次表；事件只写索引。report 只接受原预绑 exact TestReport，以原 C 字典 descriptor／dict.get 投影 exact-string nodeid／when／outcome，不取属性 bool、duration、longrepr、异常或 repr。新增运行阶段钩子不读取 item／测试对象。

**nodeid 门。** 新表及新增 nodeid 输出均不得含绝对路径。另设需独审的 pre-case observer gate：原 nodeid 仅准冻结的 repo-relative prefix `tests/test_personal_authority_replica_directory_native.py::`，函数仅原 matrix／pure 两个，参数 ID 仅冻结的十个 `preview/create/resume/verify` 与 `detached/same/distinct/none` 实际组合，或纯 `1/129`；完整预期 literal、次序和允许 ASCII 字符均冻结，不从常见 pytest 格式猜 actual。每项原／最终 ID 都须匹配该相对闭集；拒 drive、UNC、绝对 prefix、反斜杠及参数里的 `/`、`\\`、`:` 等路径字符／绝对路径。不得删除、规范化或改写实际 ID 冒称完整。

gate 必须在新增 collection 表写入及该选集 case 前完成，且须先验证原 ID、后核 LongIds 实际最终 ID；原 LongIds 的 32767 UTF-16 规则不改。若不能证明 gate／LongIds 排序及合法相对绑定，保持 unknown 并阻止准入，不声称路径保证。非法／超限时仅以有界固定 observer failure 记录原因类别／索引，不输出被拒 ID；停止该选集，不 fallback、drop item、迁移 claim 或同进程重试。实际 pytest 返回依真实路径记录，未返回保持未知，不能写成 observer 虚构码。

现 1027 没有 pytest／pluggy pins，producer 可信未建立。后继须新增 exact 源清单及实际 loaded bindings 观察；语义起点为 pytest 配置／main／hookspec／runner／reports／skipping／nodes／python／fixtures／monkeypatch／tmpdir、pluggy dispatch／result／wrappers、terminal／JUnit／默认插件。此清单不是已证明完整执行闭集，磁盘包版本也只是线索；不足处保留信任未知。原 pytest 在 logreport 前可能已格式化 repr，本稿只限制新增观察器，不称整链无 repr。

事件 JSONL 固定键：`schema,run_nonce,lane,collection_sha256,sequence,kind,case_index,phase,outcome`。值均 exact 内建 scalar；kind 仅 `case_start/phase_report/case_end/session_return`，phase 为 `null/setup/call/teardown`，outcome 为 `null/passed/failed/skipped`。session_return 不承载假造 outcome，实际 pytest 整数返回码沿用独立原记录。无时间、耗时、args、locals、frames、traceback、异常文本、绝对路径、产品状态或 ownership 字段。十二项最多 61 行；重复／超限／未知值使观察器失效。

**UTF-8 字节门，不 clamp／truncate。** 原／最终 nodeid 各 ≤4096 bytes；collection 表 exact 12 rows，整包含 JSON escaping、编码及末换行 ≤131072 bytes。表固定 header `schema/run_nonce/lane/rows`，row 固定 `case_index/original_nodeid/final_nodeid/original_utf8_sha256/declared_class`；不加自由文本。schema ≤80 bytes，lane ≤48 bytes 且为冻结 enum；nonce 沿原 runner 第436行 `os.urandom(16).hex()`，固定32 bytes 小写 hex，不能改为 UUID；SHA 固定64小写 hex。所有字符串按实际 UTF-8 核，枚举严格闭集、索引 exact int 0..11、sequence exact int 1..61，bool 不代 int；不适用值为 null。JSONL 每行含换行 ≤1024 bytes、≤61行、总 ≤62464 bytes；独立 observer 状态同字符串限制、固定字段／code enum、整包含换行 ≤1024 bytes。

每次写前先验证字段、serialize 一次并核实际 encoded byte 数，再预留全行／整包空间，不能用字符数或 raw nodeid 长度代替 escaped JSON 大小。超限不写该 payload，记录 failure／partial unknown；short write 留实得残件、sticky-disable、无补写重试。残件和已预留空间不退额，总写量仍受上界。此为新增观察输出限制，不改原 UTF-16 缩名规则／产品预算／wall；original LongIds 输出不因本稿被假称已满足新门。

## 闭合转换与异常

collection 表成功写定后才允许 `case_start(i)`；i 按实际顺序增长，同一时刻最多一个未结束 protocol。随后 setup report：passed 允许 call report，failed／skipped 直接等 teardown report；两路均须 teardown report→case_end。setup 报错仍记录 failed，不把它改名为另一结果。failed 只证明 pytest 报告失败，不采异常类或消息。session_return 只能在真实 pytest.main 返回后记录，不能代替其原整数返回记录。

原 case 的 first 可早于 pytest report；记录器不观察／生成 first，也不以报告覆盖原 first 或证明清理。first 来源、内容及发生点未记录即未知。源码必须证明写失败不会覆盖先发生的异常，不能靠“记录在报告之后”推断安全。

缺报告、缺结束、乱序、重复、跳号均保留 partial／unknown，不补事件。中断可能没有报告：只陈述最后一个实际记录边界，不推测正在执行阶段、正文开始、异常类型、下一项或资源关闭。receipt 是受信范围有限的 declared_observation；不能用外部元数据仿造 ownership kernel。case_end 和全报告 passed 都不授 ownership；真实成功仍须原断言、JUnit、pytest 实际返回及原监督终态分别证明。

## 写失败、副作用与 first

GO 后、pytest.main 前预先 CREATE_NEW 打开唯一固定 sink；打开／有限格式预检失败应在 case 前停止。事件使用预先绑定内建函数、固定字段与有界二进制写，不写 stdout、不跨文件重试、不启线程／PIPE／新进程。正常返回后再完成 sink 的独立正常关闭记录；进程强停不能改写为正常关闭。

记录器自身 short write／IO 失败 sticky-disable：活跃 case 内不抛新异常、不调用 pytest.exit、不改报告／原异常／first／清理、不触碰产品账本。已写 prefix 和末行残件照留；后续 case 按原 pytest 路径继续。固定 observer 状态与 `actual_pytest_return_code` 分层；记录失败使证据不完整，不能改写 pytest 实际码，也不能伪装 timeout 或 native code。只处理经源审证明是记录器自身的有限错误，不以 broad BaseException 归类；不得吞 pytest 原 KeyboardInterrupt／SystemExit。记录状态本身也写失败时，缺状态保持 unknown。

新增 IO 可能阻塞或改变调度，并计入原 wall；不能保证无行为影响。实现若无法把观察器故障与原异常／清理隔离，或需读取测试 hooks、放宽 wall 才可用，应以具体 finding 阻止准入。

## 交付与下一门

原 20／25／10 秒、原 native 预算／case／断言／normal unknown 政策及 1680 秒均不变。后继包保持原 1027 来源和八份 before 映射，完整保原 runner，新增 observer／pytest 属性依赖／映射算法／文件协议后重新冻结，不修改旧 manifest。新源审检查 hook 顺序、失败隔离、原规范字节和效果差异；仅设计／静态检查不能算测试通过。

须等 Root 的新准入才可执行；旧 run 不重跑。只在原 Job／实际进程和全部 writers 被证明 terminal 后，原址／副本／原址末验逐字节保全 collection 表、JSONL 完整或残行、observer 状态及原 stdout／stderr／JUnit／返回链。终态未知不称稳定副本；raw 保全不证明产品正常 close、恢复或全 goal 完成。本稿仅写设计及 fact，没有运行 helper。

r02 修复独审 F001 字节界、F002 路径界；当前旧稿／fact 已事前逐 bytes 保于 `design-candidates/before-r02`。旧独审原稿不改；修订声明尚待复审，并非已证明实现安全。
