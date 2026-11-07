# 私有工厂首次源码前移与实际对象运输方案

## 目标与确切父

源码父为 r1104 SOURCE01 的完整159文件；r1104 DELIVERY 为项目文档父。当前两源码审各保1项完整认证阻塞，候选未安装。本批先设计，不能把库存、原code一致或本设计当作源码准入。

本方案修24个新增私有 producer 的真实来源运输。24项来自 r1103 库存并按 r1104 AST/raw JSON重导，C10/S8/R6；每个现为 constructor 内 global def，header无入参/default/decorator/annotation，factory自身freevars为空，原cellvars及返回对象的闭包另保。原同名零参lookup/call和实际return/public STORE是独立事件，helper的中间结果还是module global，不能误称fastlocal。

## 为什么选择前移首次定义

选择把24个私有factory的原首次def移至各自模块真正的首次源码段，位于当前constructor def与首次direct call之前、原body/循环imports之前；factory body不在这里执行。首次源码产生的实际function及code/global/builtins/closure事实通过原调用参数直接进入活动constructor，之后不会从当前module alias重新认领expected。

这明确改变新增私有factory的定义时机和constructor私有协议，不把它伪装成旧全部shape不变。它不把原body或后续factory调用划进P0，也不提前执行任何工厂body、业务class、真正imports、原slot发布或业务初始化。

已排除的方向：仅加context参数不修callee身份；删global改fastlocal会给136具名code增加constructor前缀，包含GuardControlError；FunctionType(code, globals)新增function.__new__ audit后到原globals内部查值的窗口；append decorator返回None并依赖可变bucket，不能证明原def实际值跨STORE/finalizer仍在。禁用runtime exec/compile、CodeType.replace、FunctionType重构造、frames/locals或新诊断。

## 确切变换与P0限制

保持原module prefix和r1104 literalFalse原import符号region。每份factory源切片包含完整原def，不复制业务逻辑；仅增加唯一显式参数 `_held_factory_context`，把第一条原context RHS改为该参数。factory内原公开函数/类及闭包声明原文保持，原返回语句原文保持。

将这批factory def逐项按原出现顺序撤出constructor，解除一层constructor缩进，放在原constructor def之前的明确region内。原factory不再是constructor子code，但其全局原名和所有后代named qualname仍由原模块词法根产生。原constructor的global声明保留factory名，因为原位置仍须发布原alias。

首次direct call形状（完整实现不能使用ellipsis）：

```python
_AUTH_BOOTSTRAP_CONSTRUCT(
    (_AUTH_CATALOG_CONTEXT := ORIGINAL_RHS),
    ((ORIGINAL_FACTORY_1, ORIGINAL_FACTORY_1.__code__,
      ORIGINAL_FACTORY_1.__globals__, ORIGINAL_FACTORY_1.__builtins__,
      ORIGINAL_FACTORY_1.__closure__), ...),
)
```

这组tuple的5项是首次原对象及其当时事实，不是普通业务门的当前alias快照。不要读取defaults/kwdefaults以增加非必要audit：新私有factory只有一个完整显式positional参数，原零defaults/kwonly/vararg协议已被明确替换，后续调用不依赖缺省实参。

P0仅覆盖这段真正原首次定义/原context与原fact构造/首次direct call。在此期间必须仍满足既有P0前提：原module exactdict/keys、builtins、各原新function绑定及其原code未被GC/audit/其他回调污染。24次新增code getter本身会audit；它们不是无callback getter，若回调污染P0前提，该路径不能仍认作可信，也不能靠tuple自洽检测就宣布原source。该P0的明确增加工作量和窗口必须双审，不能自动沿用旧P0评语。原code getter边的其余完整认证义务独立保留。

进入constructor后的第一阶段在任何原body/业务callback之前删除constructor及这批首次factory的module绑定。仅删本次P0定义并已持入的确切名字，原顺序/失败点/清理矩阵在实施目录列明；不在body之后清理可能被污染的key。实际原functions及事实tuple由参数强持有至frame退出。unknown后来可写同名key，该binding不成为expected。

## 原位置的definition发布和实际调用

原每个factory def位置改为从held tuple取出本次原function并作原同名module alias STORE；这是原时点的真实绑定来源。工厂对象的分配已前移，不能把此STORE仍称MAKE_FUNCTION。除这项私有producer机制外，不移动前后的原业务语句/进口/原Call。

原实际同名lookup仍在原call位置、各一次，把实际callee绑定到constructor fastlocal。先以held tuple的原function作`is`门；expected不是该次lookup取得的对象。调用显式传实际held context参数，factory第一条实际capture只从该参数取得。

dispatch门按原context持有的native primitives内联：核actual exact FunctionType及原identity；原expected code/global/builtins/closure事实来自P0 tuple。对actual的code使用捕获的原getattr原语读取，明确其audit发生在实际call前；code getter返回后重核原identity、__globals__/__builtins__/__closure__（readonly原生字段）与source expected对象，不从current global重pin。原factory freevars全空，closure必须为原None；新code有唯一完整显式参数，defaults/kwdefaults不参与参数填充。原globals exactdict/全exactstr key的前门、原actual alias与该时点STORE/effect另列，不用metadata tuple形状替真实来源。

最后从这次已核fastlocal实际调用，不重读global。实际return先存入本次fastlocal，再在原public alias的STORE位置用该actual return；不要新增logger、异常chain、捕获重抛或新的业务helper。helper路径专门保持原_hc_actual_factory_return STORE、原context后读、原fact[4]及_make_core STORE顺序，不以一条链赋值折叠其原事件。

审计/GC等callback后必须哪些原可变fact/current/alias freshness核对，要按原语句逐项列清；本方案不据固定code和identity就宣称原owner/list/dict、初始化余体或完整EDGE/PHASE都已闭。

## 兼容、代价与释放

必须保持原公开函数/类参数、所有非factory的named qualname、freevars/cellvars、原body calls/真实imports和原skip/False/deferred/repeated初始化语义。公开class与函数仍在原factory body被原位置实际call时才创建；前移def不执行这些body。三种原导入顺序均须后续整包验证，不先跑局部模型。

明确元数据预期：24私有factory由0入参变1入参，新增一个fastlocal、初始LOAD_GLOBAL改LOAD_FAST；从constructor子code前移至module将去掉各factory的CO_NESTED，但所有后代已有function父作用域，原后代flags应保持。三个constructor新增held tuple参数及precall/result locals；原423 code中只这24factory属本批私有ABI变更组，另399个原code须完整逐一映射和核对，不能删匿名或难映射项。原8匿名qualname/68CO_NESTED现有增量要以真实新差异清单更新，不能继续原数值冒充新结果。

P0早期会同时存在C10/S8/R6个factory module key，进入constructor即删除；之后各原发布点才恢复原key。各function分配数量原则相同但时间前移；P0事实tuple5槽/每factory及新增frame locals、24code getter审计和删除成本须实际静态列出。原函数创建失败点可能提早，初始化前缀/部分失败可观察状态及额外tuple分配失败必须如实列出，不能称异常完全相同。

constructor在global声明后立即进入最外层try，进入try前不执行entry删除、临时取值或其他阶段动作。该try覆盖原constructor alias删除、各P0 factory alias删除失败、整个原body/tail、每个caller失败以及正常完成；入口删除的具体顺序为先constructor，再逆首次def顺序删除本模块factory名字。删除失败直接展开，不重试或事后清理可能已被污染的module key。

```python
def _AUTH_BOOTSTRAP_CONSTRUCT(_held_original_context, _held_original_factories):
    global ORIGINAL_NAMES
    try:
        del _AUTH_BOOTSTRAP_CONSTRUCT
        # 本模块factory aliases按首次def逆序逐个del，均在本try内
        # 原body与tail，原内层锁try/finally保持
        # 每个caller有其实际取值逆序的局部try/finally
        ...
    finally:
        _held_original_factories = None
    # 原context参数仍由frame持有；不在factory根释放前清它
```

这是必须实现的源码结构，不是对当前r1104源码的描述。最外层finally须晚于所有原内层锁finally和当前caller局部finally执行；每个caller在区域内先将其全部scratch预绑None，再进入局部try，依实际取得次序取值，finally逆序清临时callee/result/primitive等引用。原helper多段global STORE和中间context/fact动作仍在同一局部cleanup覆盖内，不能提前释放其实际return。无实际取得的slot只持None，不假装取得过业务对象。临时值不逃到尚无cleanup覆盖的前置阶段；不在锁内释放未知对象。

调用临时根全部清完后，最外层finally以fastlocal赋None释放held factory facts根；实际先取得的原context参数仍保持至这次释放完成之后，再随frame退出。正常/entry失败/body失败三条路径同用此顺序，不依赖解释器默认frame locals清除顺序。held tuples/原function及其可变附属字典的最终释放可有GC/finalizer可观察效果，不把释放说成无callback或运行验证。原17lease/1449slot/406尾、四held读取、五S002以及原锁内外分区保持。新locals/原alias STORE与全字典扫描key增长要另列，不能沿用旧峰值当新control-1MiB证据。

## 实施与准入

1. Root及同一独立代理先复核确切设计、两份24项库存和P0新增原source边界。任何实质红项先修、留原稿，未零阻塞不产生候选。
2. 设计批准后生成完整159候选（预计三改156继承），精确字节切片、477三方pin、全文diff/ndiff。逆变换撤回早期defs/facts/entry删除、恢复原def位置/zeroarg/第一capture及原call/STORE，完整Module AST和原文本应恢复r1104。原public声明AST、calls/imports、原catalog坐标重导，私有新增calls单列。
3. 本批若进入实施，只新增三候选syntax compile、父0；先保存所有raw code/完整JSON再断言，失败不重供给。24私有差异明确，另399原code比对全ABI/qualname/flags/raw bytes/names/typed constants/异常表，IMPORT顺序数量、位置表另列，不声称byte offsets全同。所有复审compile0，不load marshal。
4. 完整source双审保原未闭项。实际caller门、P0真实源、return/STORE、24项每个source/fact/lifetime矩阵不得仅以批次共用说明替代；原初始化余体、origin/failure/audit敏感getter及全EDGE/PHASE仍独立待闭。完整认证源码批准前不import/执行候选、局部bootstrap/control模型、QA或旧绿/F001。

本设计目前未获批准；不写任何source_approved/execution_admitted=True。正常库/service-dist/ACLsettings/真实平台/Git保持。r920/r964单次已耗、不重放；r967待原问题，不执行重问；P06历史0/7不再索材料。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不变。禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store及完整Goal仍未完。
