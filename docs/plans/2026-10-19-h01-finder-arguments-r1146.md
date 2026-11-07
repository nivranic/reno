# r1146：原find_spec实参运输与完整慢路保留

r1146选择两个有独立fullname依据的原样委派快路：实际位置fullname优先，或无位置时持明确exact-str fullname keyword的原value；原args/kwnames交原bound方法一次绑定，只在正常返回后分类。保原keyword比较/错误和bound/frame准备次序，不自行归一或多比较一轮；不以keyword白名单或缺seed回退冒认全域完成。str子类key完整慢路、Proxy/loader真实绑定与原source接线仍未闭；B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

五份既有tagged v3.12.10源261382B/12区间/六份完整设计、三个协议AST签名与标准三pos调用核对。原binder接受str子类，先全形参identity轮再richcompare，keyword先于too-many/missing/defaults；C归一会改变原frame准备及bound大参数分配前缀，未采用为已闭路线。四目标含catalog；另外两协议原样单次委派，distribution iterable保惰性。AST不是运行metadata或future K，未更新完整native字段目录。

本批仅静态文档/read/hash/AST/diff，无新下载、产品candidate/compile/import/执行、native build/load、模型、QA或诊断；apply0/历史11、源码缺口关闭0、产品pass0。159源码/保护56同18缺/原限额及禁项保持，不重放旧单次授权或终态失败下载；正常库、服务/dist、权限和Git写入未操作。完整Goal仍有工程，非只剩验收。

# ADR-r1146：原 find_spec 实参运输与完整慢路保留

状态：有限参数运输子设计待正式双审。选择两个有独立实参依据的原样委派快路；完整keyword慢路、Proxy/loader类型、source与执行准入保持未完成。提议与r1145及既有库存保留原字节，不把快路通过作为全域完成。

## 1. 保留原 binder，不新增一次参数比较

在原callee/source/metadata与实际签名已经由独立原根认可的条件下，保留捕获的原bound PathFinder.find_spec(actual cls)及原实际args/kwnames。只有原调用正常返回后，才使用本次C local持有的fullname作目标分类。原调用失败保持actual first，不追加目标识别错误覆盖它。

两个快路如下；它们是分支，不是允许调用的白名单。

| 分支 | call前取得本次fullname根 | 原调用及正常返回后的依据 |
| --- | --- | --- |
| P：至少一个实际位置参数 | 从原argv[0]直接独立NewRef，不读keyword字符或调用eq | 原bound method先插原cls，argv[0]因而进入fullname位置。原binder成功意味着没有keyword再次绑定已占的fullname；错误/重复仍交原binder一次处理 |
| K：没有位置参数，存在exact-str内容为fullname的keyword | 只在exact-str key上按原raw字符路线识别；持其原actual value，不做hash/eq/getattr/repr。kwnames的NULL/空tuple均按原协议 | 匹配原四形参根和原签名时，这个exact key的原比较必定匹配fullname。其它key若也匹配会由原binder报重复；正常返回才可认可本地所持value就是这次fullname |

若P成立，不再扫K。原args/kwnames、所有其余keyword值/顺序、OFFSET实参个数语义保持；不将keyword删掉、转为三positional、重建kwargs或更换keyword对象。不能仅为快路自造一个不同的bound method或改用current class属性。

P分支不限位置参数个数；即便多于合法个数，也原样送原binder，不提前用通用arity错误盖掉原keyword错误优先级。K扫描遇到非exact-str key只跳过，不拒绝、不比较；存在一个明确exact fullname key时，其它子类key的真实比较仍由原binder按原次序执行。无明确key不等于缺失fullname，必须进入未解慢路问题域。

## 2. 前提必须是实际原根

原bound method来自r1112 PyMethod_New(actual原func, actual原class)的创建返回，原func/code/defaults/qualname/varnames与实际self关系须沿T0及共同metadata消费认可，不能在调用点从current __func__/签名或spec.name生成expected。这里只用原四形参排列 cls/fullname/path/target、posonly0、kwonly0、两个None default这一有限签名前提；当前完整源码未接通，不能当已运行事实。

实际callable与argv由原调用协议保持有效，本方取得额外fullname local前只做scalar/NULL初始化与匹配raw读取；borrow→NewRef之间无callout。该引用跨原bound适配/函数frame准备/参数比较/body与返回，保证identity及寿命，不冻结可变对象内容，不作caller证书。不是对运行中frame/locals/refcount的读取。

元数据、state/代、实际selected与fresh/原D1等门仍须完整接线。fullname local不能替代C入口、Proxy lease、实际原callee根或完整callback后的fresh；本提议不增加全局last/first-use槽，不将外部传来的subject登记为expected。

## 3. 原错误及资源前缀保持在哪里

同版原binder先接收bound cls及位置实参，然后处理keyword，之后才报过多位置参数，再处理missing/defaults。它先对所有形参做指针轮，再从首形参开始逐项PyObject_RichCompareBool(keyword,varname,Py_EQ)。str子类可运行合法比较/真值代码或抛错；本快路不提前运行这些操作，也不排除它们。

原bound method的OFFSET就地插self/还原、无OFFSET栈或大参数malloc适配，以及原function的PushFrame/Initialize在initialize_locals前发生，仍由原样实际调用完成。原keyword比较/错误不会被本方搬到这些原准备之前。框架原来的kwargs unpack/free、结果检查及操作数释放仍按其实际层归属。

本方新增的fullname owning local与门检查仍有成本和自己的准备失败，不能据此声称整个调用无新增行为、引用数/GC时机完全相同。引用API/实际build、门中的分配/回调和预算仍待共同证明；不把control1MiB当heap界，也不以容量不足提前拒绝删除合法旧调用。

原调用失败时，先私有C local持actual first，结清本方已取得fullname及其它guard/temp，最后还原；不为分类增读spec/module或构造新成功检查覆盖first。原调用成功时独立持actual result与fullname，跨后继目标处理和本方cleanup；若后继准备实际失败，按其实际first结清owned result等责任，保原finder已经发生的effect，不重跑原find_spec。一般NULL/free/type及整图清理仍未闭。

## 4. 四目标与另外两个公开协议

P/K成功后，只在actual fullname为exact str时按raw字符比较以下四个固定fullname；非exact-str fullname仍是原API的合法候选实参，不能先阻止其原body。其认证支持与完整慢路另列，不将其自动认领为同名target。

- reno.personal_authority_guard_catalog
- reno.personal_authority_guard_control
- reno.personal_authority_guard_scope
- reno.personal_authority_guard_registration

catalog已是r1124要求的第四来源目标；这里具体化它在finder层的名称，不宣称r1111旧三目标文本已经包含它。原None、namespace/custom loader与非目标实际spec按原结果/异常规则处理；目标loader类型/原属性写入/恢复及phase仍须后继binding设计，不以识别名称作原来源证明。

invalidate_caches保持原args/kwnames单次委托，让原零参数函数自身处理非法实参和原body；不在本方提前改为统一TypeError。find_distributions同样原样单次委托，返回同一actual iterable，不list化、不预消费next，也不缓存最后返回。它们没有本次fullname推断需求，不附加find_spec的解析问题。

## 5. 完整慢路保持未完成

没有位置fullname且没有明确exact-str fullname key时，合法str子类key仍可能通过原比较绑定fullname。即使某key的字符文本是别的名字，它也可能匹配fullname；反过来字符为fullname的子类key可能只匹配path。不能用字符扫描、key类型限制或spec.name倒推原实际绑定。

| 方向 | 本批决定 | 原因 |
| --- | --- | --- |
| P/K原样委派 | 选择有限快路 | 实际fullname根有独立call-site依据；不重复keyword比较、不改原bound/frame准备相对次序 |
| 拒keyword或只允许exact-str key | 不采用 | 删去原binder合法调用域，不能用其容易检查冒认完成 |
| C先完整归一，再三pos调原bound方法 | 不采用为当前已闭路线 | 省去第二轮比较，但原frame与大参数bound准备的次序/失败前缀发生实质变化，尚无满足原合同的完整COMPAT裁决 |
| 为识别fullname再比较一轮，随后原样委派 | 不采用 | 重复合法eq/真值/异常effect，不能说原调用仍只发生一轮参数绑定 |
| 先原样调用，成功后只回原spec并放弃seed | 不作为最终慢路方案 | 原普通结果可保，但完整认证/直接调用兼容没有完成；不得用这种fallback关闭完整调用域 |

当前不为此未解慢路捏造可执行的产品行为；没有产品候选，故不存在已发布的“慢路统一拒绝”补丁。后续必须提出覆盖完整原实参域、原效果与准备/错误顺序的具体路线，再完成源码准入。有限快路通过不能替代该义务。

## 6. 静态反例与边界

静态例只描述源码允许的值/比较结果，不执行类定义、hook或原函数：给原bound方法一个位置fullname，并给一个字符内容为fullname的str子类keyword key；该key对cls/fullname比较为False，对path比较为True且不抛错。原binder将该值填入path，正常参数绑定保留位置fullname。按key字符扫描会误判重复fullname；本提议P分支不扫描，仍委托原binder一次。

若完全没有位置fullname，子类key可先后对cls为False、fullname为True，绑定出一个合法fullname；没有exact fullname key的raw扫描会漏掉它。此情形解释完整慢路缺口，不冒认已在本机运行通过。普通kwargs dict转vectorcall时保留原key引用并接受Unicode subtype flag，不能把问题推为必然非法的私有C输入。

P/K不证明原caller、完整root metadata/D1、所有来源创建、Proxy/loader字段/真实binding/退休、所有恢复/NULL/原handler/finally、跨版本或预算。原159源码和保护56/18保持；B001/B002 open2，full_design_approved/source_approved/execution_admitted/complete_Goal四False。正常库、服务/dist、权限、Git写入及产品candidate/compile/import/执行/native/模型/QA/诊断均0；无新下载，不重放已耗单次授权或终态失败下载，P06无材料不再索取。

# r1146 后继工程

1. 解决由str子类keyword实际绑定fullname的完整慢路。保原合法参数域、比较/真值/错误次数及原frame/bound准备顺序；不得用统一拒绝、改key或spec.name回填原实参，也不以缺seed fallback当最终完成。
2. 完成Proxy三协议与loader.exec_module的实际callable/receiver/type/持根/创建/参数/退休/恢复绑定，核旧代晚调用和state清理后的根，不从泛称C/Factory推断已有形状。
3. 接通Ingress/C(A)/actual L及原P00–P12唯一effect、新K/LOAD_NAME与A/J来源。补state/source/SCC/NULL/free、实际first、D1/D2/COMPAT/build/预算及完整双审，之后才生成产品候选。
4. 按原总方案继续Task9A9B、原111/94/r919、真实Store、P06和条件T15/生产云。P06历史身份仍无用户材料，不再索取，不以本批完成替代整体目标。

只复用 /root/r924_independent_review，不新增代理。保持原保护与限额；不执行模型/QA/诊断、真实frames-locals、全局hook/GC/allocator或服务/真实库/ACLsettings操作；不commit/push/reset/clean。本批封存后不向其目录追加或改字节。
