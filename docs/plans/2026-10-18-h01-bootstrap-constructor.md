# 三模块原 context 活动构造帧实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用原P0已进入的真实caller frame持原context，修复r1100 BP001/BP005/BP006的原tools/rows/pin来源缺口；不提前宣称完整认证闭合。

**Architecture:** 三个模块各新增一个无decorator/default/closure的私有普通constructor。原body绑定名显式global，原尾scratch与原context参数为本帧局部；初次原source构造处以同次RHS单STORE原alias后直接传参进入，入口先删除已知constructor alias，再依原顺序执行body和tail。

**Tech Stack:** Python3.12.10原compiler/symtable语义；源码静态缩进/global声明/NamedExpr实际运输。没有runtime compile/exec、FunctionType构造、CodeType.replace或延迟owner方法。

---

## 1. 父版本与范围

源码修订父是r1100完整159 SOURCE01，两源码审各保P001，不能称已批准父。文档before取r1101 DELIVERY；r1095 SOURCE03仍为最后零阻塞源码双审回退参考。r1101两轮DESIGN01被双审否决，with/exec方向未批准，所有旧红稿原保。本设计采用新的直接活动frame路线，不按旧草稿实施。

仅改candidate-full/reno/personal_authority_guard_control.py、personal_authority_guard_scope.py、personal_authority_guard_registration.py，其余156文件逐byte继承。catalog及metadata实际格式不变，原17引用lease/1449slot、五处S002同锁原get/phase/fact/current核对与锁外witness/alias STORE保持。

## 2. 具体源码变换

每个原module context赋值位置之前的prefix按原byte保留。原 `_AUTH_CATALOG_CONTEXT = RHS` 后至尾lease前为body，最终完整lease为tail。真实切片位置与完整global清单在CONSTRUCTOR-INVENTORY01；body C325/S324/R230个顶层AST节点，原body无escaping module transfer、yield/await/return，故新constructor是普通非generator函数。

```python
# 原module prefix、docstring、future imports、原tool/owner构造全部原序

def _AUTH_BOOTSTRAP_CONSTRUCT(_held_original_context):
    global ...  # inventory原body绑定的全部名字、scope尾_bootstrap、新入口名
    del _AUTH_BOOTSTRAP_CONSTRUCT
    # 原body完整缩进，所有原module绑定/删除仍作用于同一module globals
    ...
    # 原最终tail lease完整缩进：原None预绑/try/finally原序
    # 四个原context LOAD各自改为_held_original_context，仍各自一次实际赋值
    ...

_AUTH_BOOTSTRAP_CONSTRUCT((_AUTH_CATALOG_CONTEXT := RHS))
```

上面的ellipsis只说明结构；实现须逐byte搬运完整原body/tail，不手写其逻辑。RHS是原赋值的同一原AST与求值顺序，walrus只STORE原alias一次并将该次实际值直接作为唯一实参，不在任何callback之后重新从当前global认领expected。新constructor def在原context赋值位置建立，没有decorator/default表达式、没有新增helper调用；紧接实际调用前仅完成原RHS。

原body global名字为C537/S397/R251，共1185；另scope尾原_bootstrap一个，共1186。再加三个constructor自身的entry del全局声明，按三region合计1189个名字：C538/S399/R252。这不是跨module unique数量或同时峰值。需完整collector复核import、def/class、Store/Del、except target与comprehension规则，不能只global命名def。原comprehension局部target不提升global，NamedExpr外层绑定另按原语义。

尾local为C76/S187/R143，共406；其名与原body所有层Name引用无交集。C一条、S两条、R一条原 `_AUTH_CATALOG_CONTEXT` LOAD全部替换为参数；scope两epoch仍分别取得、分别清理，不能因同identity合并。原body中的context读取/原factory closure不悄改，其来源义务仍另行复审。

## 3. P0原caller转交和入口生命周期

原模块源码的首次定义/首次调用与原context构造均处于既有P0边界，在任何原业务循环import之前。新constructor实际FunctionType由原module MAKE_FUNCTION建立；其__globals__为原module exactdict。MAKE_FUNCTION仍依当前GLOBALS取__builtins__，因此仅首次真实原定义/立即原call可引用P0基线，不称它无条件继承caller f_builtins，也不把后续body或未来重入划为可信。P0要求此时原module dict/keys/builtins与刚构造的原function alias仍为可信基线；若原RHS之前/期间已有GC/audit等回调污染了这些来源，则该前提不成立，不能仍把它判作P0或为本设计扩大边界。

一经进入，真实正在执行的原code/frame与参数已经建立。之后改公开FunctionType.__code__不改变该活动frame，写同名module字段不修改其fastlocals。原context→原tools/rows→pin5→pin4/pin6原tuple/item的桥在任何尾索引/迭代前由该实际实参提供；原owner的可变list/dict、phase、实际alias仍须所有既有fresh检查。

入口的第一条可执行语句是声明后的 `del _AUTH_BOOTSTRAP_CONSTRUCT`。它在原body及任何原业务callback之前删除P0已知原FunctionType绑定，当前执行frame仍持该function。删除不放到body之后，不新增外层finally去删可污染key；本源码删除后不再写回该binding；unknown回调仍可插入同名key，它不是可信新入口，退出也不再删除它。由本源码建立的原binding仅在可信def→entry-del期间存在，在任何原body callback前已删除，不把整个原body的字典状态称为可信。原公开initialize函数的skip/False/deferred/repeated路径不改，不提供新的later callable初始化接口。

删除key只说明这条原source初次路径不留下新入口；不是凭私有前缀证明任意外部code无法重新造FunctionType、reload源码或经frames/GC取内部对象。原first-source信任边界、frames/locals禁令与解释器不能完全防任意外部改写的边界保持，不能据此豁免任一原业务callee gate。

## 4. 为什么省掉动态code修改

已留存官方CPython v3.12.10 compile.c259653B、symtable.c97689B，URL/SHA在两份CPYTHON-SOURCE报告，仅bytes/text/AST调查，没有编译或执行C源码。

compile.c741–754在named FUNCTION/ASYNC_FUNCTION/CLASS的parent符号GLOBAL_EXPLICIT时force_global；757–772跳过constructor.<locals> parent，786–789使用原name。2446–2448将此u_qualname作为class __qualname__常量。所以原63个module根命名声明（C19/S30/R14）显式global后，其原qualname及所有named子树继承原parent；原class常量也不需要修补。原源无type_params或class显式__qualname__赋值。

因此不引入_launch、__code__ setter、CodeType.replace、runtime compile/exec、匿名with-owner或tuple双轮协议；避免这些路线新增的audit/builtins交接窗口。所有既有metadata getter自身仍可能audit，不能将raw object.__getattribute__当绝对无callback；其完整caller/EDGE闭合义务仍保留，不能用本设计替整组认证清零。

## 5. 如实列出的可观察差异

named/public qualnames、原签名/posonly/kwonly、全部原freevars/cellvars必须保。原423个供给code-shapes需全部逐一映射，不删除不好匹配的项；新增3constructor另列。

8处原module层匿名code的co_qualname会增加constructor前缀：C lambda101/690和gen3741、S lambda36/88和gen1691、R lambda34/88。6lambda都仅立即读取__builtins__后丢function；2gen都由原tuple完整消费以提取closure内entry，未发布generator对象，也不读它的qualname。必须逐位置供给原用途与新位置/新真实qualname，不声称423个qualname逐字全同。

symtable.c118–121因父function-like设置ste_nested；compile.c7424–7427据此加入CO_NESTED，不因global声明清零。因此原module层function/匿名及其class methods等原非nested code会新增CO_NESTED，源物理行列/stack trace增加一个constructor frame。应供给所有co_flags增量、实际行列和完整ABI清单，不动态赋码伪造不变；当前159文件有限搜索未见co_flags/CO_NESTED/isnested消费者，不代表外部introspection兼容已验证。该变化不授其他flags变更，未来compile供给须确认仅所列变化。

scope/registration各3个PEP709 dictcomp必须按Python3.12原inlining/隐式local保存恢复单列；不要把它们算成新增6个独立function或把comp target误global。新constructor可能含编译器comp临时locals，需供给实际varnames/cellvars及异常清理，不能只凭源码global列表声称尾scratch是唯一局部。

## 6. 原行为、异常和引用顺序

原body statement及其所有显式Call的词法顺序/次数、原class AST原样；实际module绑定使用STORE_GLOBAL/DELETE_GLOBAL写同一原exactdict，globals()仍返回该dict。原scope_bootstrap的STORE仍module全局，五处S002调用/同锁校对/锁外alias及witness原样搬运，不缩锁、不多读、不修镜像。

原body失败时普通函数直接展开，不走尚未到达的tail。tail的原prebind只在到达原lease位置后执行，不为了参数释放而提前预绑406个locals或新加可能del未初始化locals的finally。tail局部保原逆序finally；late context各epoch在各自实际位置取得/释放。最早实参held context持续到frame退出，晚于所有tail locals；无新增异常捕获/重放/吞错，也无异常日志。

新增三普通frame及3FunctionType/对应code的分配与递归深度成本必须另列；新的RecursionError/分配失败点不冒称不存在。没有新业务资源/native/SQL/ContextVar/Lock或权限，控制运行原限额不变。本设计是源级异常/引用论证，不是实际GC/线程/导入证明。

control-first、scope-first、registration-first三链：context alias都先于原body STORE，def不执行body，constructor entry随即删除自己alias；原body imports及全部可见alias时序原序，scope两个尾门仍在同一个frame/tail，不分裂成不同轮次。原module spec._initializing到ctor返回后仍由原import机制完成；原未ready skip/False、marker None/deferred及重复initialize不改。这些后续须完整包QA验证。

## 7. 具体实施任务和准入

Task1：Root与同一独立代理复核确切DESIGN01、20完整输入pairs/3442862B、两官方C源、CONSTRUCTOR-INVENTORY01和原P0/ABI边界。设计红项先修并保原稿，不因材料核同自动通过。

Task2：设计双审后，在新的source子目录供给完整159候选（三改156继承），每项parent/candidate/inverse三pin、三完整全文diff/ndiff。将新增constructor/全global/entry-del/唯一call折叠，四ctxload还原，回放原context Assign并摊平body/tail后，三个完整Module AST应与父全同；原236声明与10class/22357 Calls另核，新增3def/3call/3del与Name STORE区别单列。

Task3：158 Python AST，Root供给阶段对三改候选各一次syntax compile；因旧423供给shape没有co_flags，为精确观察本次新flags差异，另对三份父源各一次syntax compile收原code元数据，共6次，均不执行产生的code、不import、不跑旧QA/绿例/F001。所有复审新增compile0，代理不得compile。原父compile0的准备选择在本修订中明确更改，不能把静态推导写成已观测parent flags。原423args/freevars/cellvars/named qualnames及8匿名、CO_NESTED、PEP709实际差异全列；原17lease/1449slot/406尾slot、四原ctxload/五S002、三源作用域和真实清理目录全核；重算EDGE/PHASE/P0–P8/SCAN静态成本而非沿旧global cardinality。

Task4：完整源码双审确认本次原context桥；若原审计getter/其他已有caller缺口仍红须实报，不把P001局部修复叫整个认证完成。继续原factory/caller closure、初始化余体、origin/failure-readers、actual return/STORE及全图闭合。整组认证源码闭合后才完整包QA脚本/实际准备双审/新claim，不跑局部bootstrap/control模型或候选运行。

## 8. 回退和保持

未安装候选回退为保r1100红稿，或明确引用r1095 SOURCE03作为零阻塞源码回退参考，不覆盖旧目录。新文件xb，已封存r1101不追加。唯一独立代理复用，每个新报告路径单独授权。

case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。r920/r964耗不重放、r967待原问题不执行重问、P06历史0/7不再索材料。旧绿/F001不重跑；canonical/c973本段apply0、历史11、产品pass0。正常库/service-dist/ACLsettings/真实平台/Git保持；Task9A9B、原111/94/r919、真实Store写权与完整Goal active未完。
