# r1119：native 私有 locals 条件通道

r1119提出PL条件通道：原helper保持body，未来显式将exec两位置参数改为三参K/G/L，由actual native私有locals的固定mapping C slot交付原C/A，不从可写G的同名alias取入口。保留名成功只回state-held原根，失败必须非KeyError以阻断LOAD_NAME回查G。该子方向有具体静态接线，当前三源没有新名字，未来实际LOAD_NAME、完整映射兼容及原消费者绑定未完成。B001/B002仍open2，完整设计/source/execution均False，源码仍r1106 SOURCE02完整159，Goal active未完。

25完整输入pairs每侧4044389B，8份官方3.12.10文本849813B，其中新增abstract.c纯文本79783B；20原文加两项签名/运输补证共22段，三源AST及父r1118封存40文件9469532B核同。159源码7956657B及保护56同18lstat缺席保持；没有live frame读取或运行这条C路径。

PL不替换原module构造器、原K/G与builtin的audit/环境解析；明确新增L身份和exec/helper实参变化，不能称完全透明。ordinary miss新增KeyError分配/清理；STORE/DELETE/annotations走mapping路线，keys/locals/hidden-merge、普通异常/回调保真和N2/N4/COMPAT仍待。STORE_GLOBAL及函数LOAD_GLOBAL仍直达G，module L不覆盖全部callee；L可被外部获得，不当秘密或caller证明。

I002的每次fresh invocation仅是后继结构要求，不先占attempt原槽、不以foreign调用填本次expected；实际return→原sourcecaller local→原STORE/effect尚未完整接线。P0两次环境与tools/rows/owner/context、catalog第4目标/私有ABI、23P2/CORE第24项、联合metadata/三D0/D1/D2仍须同版闭合。条件子设计材料0不代表完整方案批准，不运行局部getter/producer模型或QA。

本批candidate/compile/import/运行/native build/模型/QA/诊断0，canonical apply0/历史11、源码缺口关闭0、产品pass0。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal未完，非只剩验收。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。正常库/服务-dist/ACLsettings/平台/Git不操作，禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964耗不重放，r967不执行不重问，P06无历史材料0/7不再索取。

# r1119：native 私有 locals 通道的条件子设计

状态：有限静态子设计，待材料双审。提出 PL 通道替代 r1118 的 DGI；没有产品源码候选、编译、导入、native 构建或运行。完整源码仍是 r1106 SOURCE02 的159文件。**只解决 I001 的具体候选运输路径；I002 的实际原消费者绑定及 I003/I004 均未关闭。**

## 1. 选择与合同变化

| 入口方向 | 本批处理 | 具体理由 |
| --- | --- | --- |
| DGI：将 C/A 写入可写 G，再从 G 调用 | 保留 r1118 的否决，不重做候选 | 原 exec audit 之后值可变，入口内检查不能阻止 unknown 先执行 |
| R2：自建替代 module function/executor | 不重开 r1116 已否决版本 | 原 module 构造/CREATE/doc 差异没有因本批消失 |
| PL：原 builtin exec 显式接收 native locals L | 选为待完整接线的条件子方向 | 解释器从实际 L 的固定 C mapping slot 取入口，独立于 G 的同名 value；保留原 executor 对实际 code 的构造 |

现行 r1111 是 `helper(original_exec,K,G)`，本子设计明确拟改为 `helper(original_exec,K,G,L)`：helper 的 f/args 实际参数多一个 L，原 helper body 仍为 `f(*args, **kwds)`；builtin exec 从两位置参数改为三位置参数。K 仍是该次原 get_code 的实际返回，G 仍是 actual module 的原字典，原 builtin 插 builtins/exec audit/原 EvalCode 路线保留。**L 不等于 G，不能再称原两参数或完全透明。**

r1118 允许提出具名执行边界修订并列代价；这不是一条用户已批准完整实施的证明。未来修改源码后，必须使用该版本单次 get_code 实际返回的新 K 重建路径，不在 runtime 改 co_consts/code，也不将当前旧 K 与未来源码混同。本批没有新 compile 或可执行 CodeType。

## 2. 入口根如何越过可写 G

设 C 为 r1112 原 native producer 直接构造、先 state-held 的普通原 C callable；A 为本代该次 loader attempt 的实际 opaque 对象。原 native wrapper 自行创建 actual L，原 type 来自固定 C spec/slots 的 actual 创建返回；其实例只由原 allocator 创建。沿用 IMMUTABLETYPE、DISALLOW_INSTANTIATION、非 BASETYPE、原 object base/原 type metaclass、不暴露实例 dict 或可写 state 的约束。L 不是 dict 子类，不能依赖外来 Python `__getitem__`。

L 另持该次原 G、A、generation 及原 C 根；固定 `mp_subscript` 和 `mp_ass_subscript` 是静态 C 定义。借助 PyObject_GetItem 的实际 dispatch，不先取可写 module 属性、Python descriptor 或 G 同名函数。

| 顺序 | 实际运输 | 必须独立检查/保留的事实 |
| --- | --- | --- |
| P1 | 原 C producer 的 C/A/type/actual L 被 wrapper 强持，传给已捕获的原 helper | 不从发布后的 alias 认领 expected；actual L 在进入 helper 前已经明确持有 |
| P2 | helper 的原实际 f/args 向原 builtin exec 传 K/G/L | 原 helper 的 D1、metadata/实际参数准入仍必须在完整设计闭合；不是把当前 G 值当 L |
| P3 | builtin 先 INCREF 实际 supplied L，再检查 L 是 mapping；audit 后把同一 locals 交原 EvalCode | audit 的参数只有 K，不直接给 L；但 L 不被视为秘密。原代码、G、builtins 解析不被 scratch 替换 |
| P4 | 原 evaluator 初始化实际 module frame，f_globals=G、f_locals=L；在选定的 module `LOAD_NAME` 进入 PyObject_GetItem(L,key) | 这是冻结 C 文本的数据流，不是读取真实 frame/locals；没有执行该路径 |
| P5 | PyObject_GetItem 取实际 type->tp_as_mapping->mp_subscript 并直接调用原 C slot | actual type/slots 的来源是 P1，不从 Python可写名称建立；不提供 Python 覆盖 slot 的入口 |
| P6 | 对 `_AUTH_NATIVE_INGRESS` / `_AUTH_NATIVE_ATTEMPT`，slot 成功只返回 state-held C/A 的新引用 | 不查 G 的这两个 value。C 原 pointer/flags/self/metadata、原 A/L/type/generation/阶段都要 raw 核对，成功段不调 Python、不分配或释放任意旧对象 |
| P7 | evaluator 直接强持该实际结果到操作数栈，再求参数并分派 | 不在 dispatch 重新 lookup G；C 入口自身重核原 self/state/phase/metadata。合法参数/monitoring 回调仍可发生，不能声称整个窗口无回调 |

未来源码必须显式安排这两个新名字的 module 读取；当前三源均没有它们，当前尾调用还是两参 Python constructor。不能把当前 `_AUTH_BOOTSTRAP_CONSTRUCT` 的调用或某个函数内 LOAD_GLOBAL 冒认成已接 PL。

**完整候选必须核实际选定指令为 LOAD_NAME。** 新保留名不得被 `global` 声明或改成函数内 LOAD_GLOBAL；仅有 AST Name 不等于已经验证编译结果。当前没有新字节码，这是一项实施前的具名义务，不是已通过的检查。

## 3. reserved lookup 的完整失败协议

这是 PL 与普通映射缺席的关键区别。LOAD_NAME 遇到 L 的 KeyError（包括子类）会清除异常并回查 G/BUILTINS。因此：

1. slot 先核 actual self/type 与仍可安全访问的原 state；key 必须 exact str，比较通过固定 C 字符读取对固定 literal，不运行未知 hash/eq/getattr/repr。
2. 已判明为两个保留名时，必须进入**保留名封闭分支**。成功仅给已独立持有并重核的原 C/A。当前 G 即使含 U，也不取用、不比较为 expected、不调用 U。
3. 退休、缺根、错代、阶段/metadata 失配等保留名失败，必须以已捕获的原非 KeyError 异常类型阻断；拟用独立原 RuntimeError 的固定消息。不把这类失败转成 KeyError、普通 miss、None 或外来 current 值，也不清错后 fallback。具体新准入错误与原业务异常的首对象/链仍须完整实现表。
4. 非保留名才允许普通 mapping 缺席路线。构造/设置错误及释放可回调的操作在短 metadata 锁之外；不得把失败段叫无 callback 成功段。MemoryError 等实际设置失败同样不能被替换成保留名的 KeyError。

因此 r1118 的分支可具体改写为：audit 将 `G['_AUTH_NATIVE_INGRESS']=U`，但随后的 genuine module 保留名 LOAD_NAME 仍进原 L slot；成功取得 C，或以非 KeyError 失败离开。**这只排除这一条 G fallback 接点**，不是整模块未知依赖为零或解释器防篡改证明。

原 L type 的 IMMUTABLETYPE 阻止 Python 层修改其方法；非 ModuleType 的此类实例也不能通过普通 `__class__` 赋值换成可写类型。仍不允许暴露通用 setter、可写 C state 或接受外来 bases/metaclass/slots；具体 actual allocation/强引用/失败及退休尚未实现，不以 flags 字样自证来源。

## 4. 普通名称和原 STORE 的代价必须显式保留

对非保留名，拟由 C slot 转发同一 G：get 成功持原 actual value；set 用原 PyDict_SetItem；delete 以 value=NULL 的 mp_ass_subscript 路线转发原 PyDict_DelItem。每次普通 dict lookup/store 前仍需全键 exact-str 前验，不能先 hash 后查；G watcher/旧值 DECREF/分配引起再入时，按原根重新核 current，不重建 expected、不锁跨它。

| 原操作 | PL 拟议路径与明确 delta |
| --- | --- |
| LOAD_NAME 的 G 命中 | 原 exact-dict 首查改为 C slot→同 G 实际查找；成功仍回原 value。增加 C slot/检查/引用成本，不保证完全相同事件/耗时 |
| LOAD_NAME 的 G 缺席 | L 需要 KeyError，evaluator 清除后再查 G/BUILTINS；原 exact-dict 首查缺席不先产生此 KeyError。新增异常分配、失败、释放和可再入时点，不能用“结果同”抹平 |
| STORE_NAME | 原直接 PyDict_SetItem(G,...) 改为 PyObject_SetItem(L,...)→原 C slot→同 G；原实际 STORE 尝试/返回/effect仍需分列，不把 slot 返回0当 effect永久一致 |
| DELETE_NAME | 原 PyObject_DelItem(G,...) 改为经 L 转发；原 missing/error 到 NameError的转换及原 del 次序都须保留，不吞失败 |
| SETUP_ANNOTATIONS | 从 exact-dict 支路改走 mapping get/set、KeyError处理和 DECREF；本子设计不以当前少量顶层语句冒认未来源码无此协议 |
| STORE_GLOBAL/DELETE_GLOBAL、函数 LOAD_GLOBAL | 仍直接走原 G；**不受 module L 入口保护**。函数内部须通过原局部实参/独立 native consumer 接线，不能改名为 L 已覆盖 |
| 原 builtin globals() | 仍读 frame 的原 G；现有三模块首部的原 globals() 结果不因 L 自动改成另一 globals。但 Name target 本身的准入仍独立 |
| locals()/vars()/dir/映射方法 | module locals 身份变成 L；L 可能被外部获得，不能当秘密 capability。完整 keys/迭代/length/get/items/copy 接口与错误/再入语义尚需设计，不冒认 L 是完整 dict |
| hidden locals 合并 | 3.12 _PyFrame_GetLocals 可能 PyDict_Merge(innerlocals,L,1)，仅有两个 mapping slot 不证明 keys 协议已满足；必须完整设计并验证 |

对保留名的 set/delete 拟明确拒绝，不能修改 C/A 的私有根。普通 G 上直接同名写入仍是 G 的实际 effect，只是不成为 PL 的 expected/保留名读取来源。未来源码不为这两个新绑定发 del；其生命周期归 actual L 退休。该 overlay/只读语义本身就是额外差异，不能伪装成 G 普通键。

以上差异中，隐藏 locals、完整映射接口、普通 miss 的异常/回调保真与最低版本兼容尚未闭合；它们阻止完整实施批准。原 module 的 private constructor 仍由原 EvalCode 创建，PL 不替换它的 doc/CREATE；这与 R2 是不同方向，但不意味着其它 CREATE/原 factory 时序自动相同。

## 5. I002：每次 fresh 的方向与尚未建立的消费者

PL 不识别任意 caller，也不将“从 L 取得 C/A”标成真正原调用已发生。外部若持有 L，可以读取同一 capability，调用 C，甚至提出其它执行；不以 L 不公开到 G 推出它不可达。getter 仍只给该次实际帧环境。

下游方向固定为**每次实际 C producer 调用具有独立 invocation J**：actual code 根取本代原 get_code 的持有树；该次实际取得的环境、原构造参数、actual function/origin 由 C 自己构造并强持到 J；不从 subject/current G 注册 expected。J 不凭 supplied code/G/route 字符串称原源码已消费，不占 attempt 的一个“首个正确 P0/P2”槽，也不消耗下一真正原调用的唯一 token。

foreign 的 J2 不得覆盖 J1 期望根或供 J1 补 pin。CREATE/audit/释放可再入时，每次只核本次实际对象；同一次 actual return 的 function/facts 直接经 C 参数/返回引用运输，不能再查 G 同名 alias。原 sourcecaller 的实际 local、原 STORE/del/effect、联合消费应使用该次独立事实；复用先前 invocation 不能补首次来源。

这是下一完整 producer/consumer 必须满足的具体结构，**当前尚未给出 P0 全原 RHS 的 native 实现及真正消费者完整接线**。尤其不能在 effectless producer 尚未绑定原 local 时，将 current G 的 owner/ready 或 foreign J2 的 STORE 当作 J1 的原写入证明。若未来入口直接调用会改 G 的 constructor，还须逐条证明 foreign 不先占原状态；“每次fresh”四字不能代替该证明。额外分配/保留也未证明 N2/预算，不能靠无界生成逃避一次 token问题。

因此 I002 保持 open；本批不调用 producer 或做任何局部模型，不把 PL 作为源码认证/真实 Store 写权。

## 6. 强引用、退休与完整接线义务

L 从 actual creation 开始强持；helper、builtin、evaluator 对实际 locals 的引用各按已核原文转交。L 另持 G/C/A/原 generation/type，不能把 borrowed 根跨回调使用；native callable/type/module/attempt/locals可成环，m_traverse/m_clear/m_free和实际引用逆序释放必须覆盖。原执行正常返回与失败后将 L 置退休，再在短锁外释放自有根；外部仍持有 L 时，保留名读取只能非 KeyError 拒绝，不复活、不用 current G补根。具体活动调用与退休安全仍是 N4，不声称已实现。

N2 要计入原 type、每次 L、metadata/固定键/实际 J及结果、额外异常/映射接口、原 helper新增参数构造与所有权成本；原 control1MiB与时间/并发限额不放宽，不虚报未测的 sizeof 或耗时。

完整设计还须联合闭合：I003 的 P0 两次环境、tools/rows/owner/context实际 RHS、catalog第4目标、0→1/2→3等私有 ABI与原 STORE/del；I004 的23 P2/CORE第24项、联合 metadata/三D0/D1/D2、N2/N4及初导入/reload/direct-loader/失败路径。PL 显式引入 L 的这次执行合同修订需要同版双审；源码仍 r1106，不因本子方向自动发生。

仅有 CPython3.12.10 文本，不将当前 >=3.11 声明收窄，也不报其它版本、ABI、free-threaded构建或平台已支持。只有本子方向来源/协议证据，不能据此运行旧绿/F001、native build、局部bootstrap模型或完整包 QA。

## 7. 本批证据与边界

冻结父 r1118 的交付及具名必要原文/三份当前源；新增官方 v3.12.10 abstract.c 文本79783 B，未执行。源码只解析 AST，当前新保留名不存在的事实不代未来 LOAD_NAME 字节码验证。准确原文、完整输入指纹、父40文件、159源码与保护56同18缺核对保存在本目录证据。

full_design_approved/source_approved/execution_admitted/complete_Goal 均 False；B001/B002仍open2，源码缺口关闭0、产品pass0。原17lease/1449slot/406尾/五S002与全部原预算/禁项保持。正常库、服务/dist、ACL/settings、真实平台和Git不操作；不读取真实frames/locals、不注册hook、不profiling/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitoring/PEP523。r920/r964已耗不重放，r967不执行不重问；P06历史材料0/7不再索取。完整Goal仍有工程，非只剩验收。

# 下一批：PL 的完整映射协议与原消费者接线

父r1119，当前源码仍r1106 SOURCE02完整159。PL只是待完整接线的子方向；当前exec/helpers/三源没有被改成新路线。

1. 为PL给出完整的native mapping协议和实际slot/allocator/state交接。保留名成功只回原根、失败非KeyError，普通缺席的新增异常/回调、原STORE/DELETE/annotations/原首错及keys/locals/hidden merge要逐操作设计。选择清楚overlay keys/get/items/len等接口和退休/外部仍持L的行为，不以两个slot冒认dict兼容，不缓存异常/tb来隐藏分配。
2. 明确未来源码的两个具名module读取点和原symbol约束，防止global/函数LOAD_GLOBAL绕过L。新候选只可在完整设计双审后生成；届时核该次实际get_code根对应的真实LOAD_NAME和const路径，不提前compile局部模型证明一个不同入口。
3. 同版设计真正的effectless fresh producer到原sourcecaller实际local/原STORE/effect：每次J的原expected/实际返回独立，foreign J2不得成为J1原槽或expected；public可调用性不等于原事件，也不强制识别任意caller。若consumer会改G，必须具体处理foreign先写、CREATE/audit/释放再入及原阶段，不能只写fresh。
4. 联合接P0实际两次环境/tools/rows/owner/context、catalog第四目标和必要ABI、23P2/CORE第24项、联合metadata/三D0/D1/D2/N2/N4/COMPAT。完整设计/源码准入前不运行候选、native build、局部bootstrap模型、旧绿F001或QA/诊断。原预算与保护保持，不重复索取P06材料或旧已耗授权。
