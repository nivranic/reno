# Task9A marker启动封存顺序修订02

本稿完整承接`2026-10-18-h01-marker-bootstrap-sealing-repair.md`的目标、四文件范围、原有限额和保护约束；以下启动来源合同替代01的单bool首次封存门。01、I-D001红及Root01漏审全部保留，不修改原文。继续既有executing-plans流程，仅复用同一只读代理。

## 结果与证据边界

r1063唯一八项0pass/8fail/缺0，10.297秒，全部在registration class/function闭包校验拒绝，8wait无timeout/kill；双actual0仅证记录一致。源码可确定早期registration快照与后期bind的合法cell更新存在时间不一致，未新跑探针读取失败进程确切cell。产品源码与c973未安装。

## 原工厂启动完成事实

保原最终三caller、四marker接口、五项`_MARKER_CONSUMER_FUNCTIONS`内容和通用class/function pin校验。四文件仍为lifecycle、registration、resources、enrollment。scope等其他154文件字节保持。

1. **固定工厂内独立原record**：`_make_marker_consumers_original`建立不导出可写slot的独立完成record容器，原bind开始仅记一次attempt，原bind完成全部module/dictionary/functions/function_pins/checker_pin/dispatcher_pin后才写一个完整不可变完成tuple。记录attempt/body-completed/result/effect分列；不把body完成说成最外层已返回。record不接收调用者声明、不从READY或module镜像重建。未attempt/未完成返回None；partial/重复/不一致拒绝，不修补、不清空重试。
2. **原只读observer**：工厂另外返回具名`_marker_binding_facts_original`，不改原五项functions tuple。observer仅捕获稳定原record容器、原静态metadata校验工具及原state-reader函数，导出前将这些稳定捕获全部定值。registration模块在自己的早期导入阶段捕获该observer并保存其完整原code/defaults/kwdefaults/closure/cell值pin，后期先核这个初代pin再调用observer。observer本身不得直接捕获随bind合法变化的非局部cell，从而避免再次制造同类pin漂移。
3. **动态绑定状态显式核同**：固定state-reader由工厂创建，仅读原非局部module/dictionary/functions/function_pins/checker_pin/dispatcher_pin值。observer在执行reader之前核其原code/defaults/kwdefaults/closure-cell身份；reader唯一允许变化的是列明的原bind初始化cell，其他稳定cell值仍核原身份。reader输出与完成record逐项`is`核同；实际五interface与原module dictionary、原trace registry/cache绑定、原三个最终caller/alias与实际checker/dispatcher也必须核同。每个function pin核原function/code/defaults/kwdefaults/closure与当下cell值一致，先验exact类型与str-key再查字典；不执行接口、dispatcher或resources._authenticate，不产生认证循环或IO。
4. **READY只是延迟信号**：原bind结束才将`_MARKER_CONSUMERS_READY`置True，但首次registration seal即使看到True也必须通过上述原observer的正向完成事实；单独把False改True而没有完成record，仍不能封存。此信号不证明任何scope、资源、写权或业务就绪。False仅延迟，其他类型拒绝；后续认证将信号和已封原绑定事实视为应保持的metadata，变化拒绝。
5. **原caller实际return单列**：resources原initializer仍先trace bind，再调用原marker binder，把实际return留在本地且要求`is None`，随后才能调用原registration initializer。不得由record补最外层return。registration首次seal前还核资源启动record；已有_PINS只认证、不重封。原_PINS None认证继续NOT_READY，无legacy放行。
6. **最终完整封存**：首次registration pin在最终绑定后建立，保原所有class/function/stack/dictionary检查；另封原observer和启动record/原tuple绑定。resources在registration首次完成之后才建自身原pins，并把实际原registration初始化callee与相关绑定纳现有目录。不得更新已经存在的registration pins来消除错误。
7. **固定完整入口**：enrollment原origin初始化之后在模块末尾显式导入原resources，使所有业务profile/budget/operation/scope创建之前完成绑定与首次封存。不得在活动scope的marker/registration._authenticate中lazy import。私有registration单独导入时允许尚未封存，其认证仍拒绝；完整入口负责完成启动。正常DLL/ABI/第三方/词典/隔离配置初始化读取按原完整模块边界记录。

## 源码与实际检查顺序

设计双审→158完整父本/候选与全文diff→157静态compile→源码双审→完整脚本/catalog双审→新空根/完整输入副本与实际准备双审→新claim→实际检查→双actual审。源码任何细节与此合同不符须保红修订，不把设计0冒认源码0。

保留r1063八项所有原认证/业务断言及修订02快照oracle，仅切新受审组合/runtime路径；新claim仅定向复检八个旧失败ID。新增三个具名覆盖，共11项：

- B003：registration先导入再enrollment，完成后核相同原绑定/最终caller/认证。
- B004：只导入registration，确认_PINS None且认证按NOT_READY拒绝；再完整enrollment，确认首次seal成功，重复initialize保持同一个_PINS identity。
- B005：只导入registration，在子进程内临时单独将READY设True，原完成record仍未生成；registration首次initializer不得产生_PINS，认证仍NOT_READY。finally恢复原False，再完整enrollment确认正常首次seal。不替换observer或产品函数，不操作其他进程或产品文件。它是启动状态负例，不是资源授权、诊断或性能测试。

若失败保留实际红，仅后续新claim复检失败ID，不重跑本轮绿或旧21/12/3绿。无AST摘取/产品模块替身，资源start/marker IO/业务native/EX/SQL/Store均0；原scope reset/pending根保持，不冒业务退休/root释放。

## 仍需后续开发

完整Task9A真实已发行lock/首marker、group/guard/原tb和清理矩阵、Task9B全动态precall目录、完整物理/费用债务、guard消费者、两阶段terminal、容量/durable恢复、原111/94/r919、真实Store写权及全Goal仍未完成，非只剩验收。canonical/c973本段apply0、历史11，产品pass0。

原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children及native20–25–10保持。禁profiling、frames/locals、停点、定点计时、新异常链诊断；r920/r964单次耗不重放，r967待原问题不执行不重问，P06历史0/7。正常库service-dist/ACLsettings/平台/Git保持。不新增代理或重复索权。
