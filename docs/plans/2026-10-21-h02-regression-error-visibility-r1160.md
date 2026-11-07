# 普通回归错误传递：修复与实际结果

r1160修复了普通回归的两处可证问题：实际测试在预设checkpoint未触发时bare raise原public错误，runner保留有界标准pytest短报告；独立复审另发现并修复Popen后Thread构造失败的清理窗口。9产品候选与r1159完全相同，原22纯合同源码不变，其旧通过证据仅复用，不计本轮新通过。完整11候选263382B、修订02 runner、4个纯内存helper、预算与38份精确输入经Root及同一只读代理复审后，仅全新q1160一个child执行4helper+1完整实际流程：4通过/1失败，15阶段14pass/1fail，约2.656秒。本次成功保留公开错误AUTHORITY_RAW_PRESERVATION_PUBLICATION_FAILED，标准输出1424B。无timeout、cleanup_failed、输出溢出、reader或monitor失败，reader已join；这不等同于产品所有资源正常关闭。

单次run01已耗，不重跑、不安装。public code仍是泛化发布失败，不能确定底层异常、错误码或具体native调用，也不能单凭该码排除全部具名PUBLICATION_FAILED分支；未读取隐藏异常链、frames/locals/notes，未新增产品接点或诊断，没有依据修改rename或增加fallback。r920/r964旧诊断未重放。固定fixture产品control静态上界1021952B（998KiB），非实测；38输入单侧1010632B、manifest10423B，准备上界2062591B，实际账2053924B<2MiB，已全额计pipe8193B。4 live before同、7新目标严格缺，保护56同/18严格缺，r1106完整159与r1157/1158/1159旧封存均保持。正常库、服务、dist、既有ACL/settings及Git不主动操作。

本轮完成错误传递与报告保存，尚未修复持久包首次发布失败，source3实际采集/复制、resume/verify完整成功仍无证据，实际产品继续为r1158采集基底，r1159/r1160持久包3为未安装候选。后继先按已冻结来源和普通失败记录做静态开发，保原first、来源门、关闭责任和预算，不把新普通测试改装成未授权诊断。完整H02下游、H01原source/caller/state/生命周期/COMPAT/准入及B001/B002 open2、H03现代pending真实落点/阶段集合/占位发布/全部writer暂停恢复仍待。P06历史七文件用户无备份，不再追问；条件T15和生产云仍待。不是只剩验收，Goal active未完成。

# r1160 普通回归错误传递修复

上一轮r1159是有效工程进展：9产品/2测试完整候选和22纯case通过证据已封存，但实际首request未发布。现有产物不能确定错误码、具体步骤或任何native API是否到达。r964实际记录已核是SQL deadline，不用其替代本轮根因。

源码证实的缺陷在测试：broad pytest.raises接纳任意过早public Error，后继interrupted断言覆盖了正常失败；runner丢弃标准pytest stdout/stderr。本轮只修该测试的错误传递和runner输出保存，不改9份产品候选，不加产品/原生接点、observer、异常链、frame/locals、profiler或计时停点。没有依据改rename或加fallback。

checkpoint未触发时，except块直接bare raise原public Error；已触发时仍接受原合成故障，若产品吞错则else立即pytest.fail。全部原profile/fixture、原source/copy/resume/verify/exhaust及wire帽断言保持。新batch、新q1160和独立固定输入，不修改r1159任何文件或重用其run。仅运行受影响的完整actual case；原22纯case源码与9产品逐字节相同，复用已封存通过证据，不计成本轮新通过。

runner仅保存标准pytest --tb=short输出，不启用showlocals、chain读取或额外观测；有界pipe收集≤8192B加1字节溢出判据，溢出中止并失败，不继续放大。新增4个纯内存pipe边界例（0/exact/overflow/read-error）先于一个完整实际case，同一pytest child共5case；原22纯合同例不重跑。20秒native、25秒parent、进程与pipe reader共用cleanup≤10且不超parent，case≤30/并发1/max2child/parent360/矩阵1680保留。worker报告降为8192B适配5case，source pin与输出报告准备仍累计2MiB；child启动即计满8KiB+1的输出配额，未读满不退款。产品control沿原同夹具998KiB静态包络，q1160与q1159等长，原纯合同不再运行；旧预算、grant门、真实库/服务/dist/现有权限/Git不动。

完整候选、前版对照/逆像、Root与同一只读代理复审、固定输入和预算通过后才运行。标准测试失败仍不准安装，不把可见性修复当产品修复；若得到public code，只按该code的明示含义收窄，不提取隐藏异常链。H01/H02/H03及原全部目标继续保留。


# r1160 固定夹具读取包络

产品上界沿 r1159 的 998KiB（1021952B）静态证明，不是实测。9 产品候选完全相同；唯一实际测试的夹具、路径长度、profile、原件、copy/resume/verify/exhaust 和已持有字节帽断言均保留，仅改早期错误传递。q1160 与 q1159 等长。此前 22 个纯合同例不重跑；新增 4 个 pipe helper 仅用内存 BytesIO、合成 read-error 和 Event，不导入产品、不取得产品源目标、不新增产品控制读取。

六文件帽 P/N/K/T/L/R 仍为 23/11/20/2/21/3KiB，数据读取 13P+11N+K+T+8L+2R≤616KiB；3000 次 guard.verify×4 根×32B=375KiB，direct marker 2KiB、config/其它 4KiB、源/payload 1KiB。完整字段 wire 包络及 guard 次数依据保留在 r1159/CONTROL-BOUND-03.md/json；本轮只在上述固定域复用，不扩为全产品证明。

准备读取另守 2MiB：双侧完整 RUN-INPUTS 和所有输入字节，加准入 4096B、worker report 8192B、启动 child 即全额计入 pipe 8193B。pipe 仅保留 8192B，另 1B 为溢出判据；不足不退款。manifest 生成脚本重算准确上界，超限即拒绝，不放宽帽。

本轮为 4 个纯内存 helper + 1 个原完整实际流程，同一 child 串行；最多 15 个 phase、case≤30、native20/parent25、进程及 reader 共用 cleanup≤10 且不越 parent；原 parent360、matrix1680、max2child 限额保留，本批仅准入一个 child。此文件与 JSON 尚待独立复审，不作为执行准入。


设计和预算段是执行前说明；实际结果以本文开头、RUN-RESULT-01及标准报告为准。候选未安装。
