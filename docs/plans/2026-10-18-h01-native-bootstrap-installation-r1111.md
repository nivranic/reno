# r1111 原 PathFinder 槽位安装与原 helper 执行子设计

r1111安装子设计修订02选择原PathFinder槽位C代理及原loader执行包装，单次get_code实际根经捕获原helper/builtin exec消费；明确全局槽位与调用帧delta。01的builtins预插方案被实质红审否决，02恢复原helper环境；首native根/实际发布/联合消费/兼容仍待，完整设计/source/execution均False。源码仍r1106 SOURCE02完整159，Goal active未完。

本批冻结24完整原/副本pairs，每侧3474648B，核父r1110封存76及当前159源码，保护56同18lstat缺席。新增10份官方CPython3.12.10源码/文档581071B，另继承两份importlib文本；24原段与2追加段共26段原文、ADR01→02全文差异/正逆和静态工具修订均核同。没有新产品候选、compile/import/执行、native build/加载/运行、模型、QA或诊断。

安装选择收敛为显式替换原PathFinder的一个actual槽位，不在前方额外插项；原find_spec单次委托，保原path/cache/hooks/None/namespace/StopIteration处理，并纳入find_distributions懒resolver委托。原package路径与spec搜索路径不做代理。仅对三个exact目标的原SourceFileLoader实例包装exec_module，原importlib继续管module/sys.modules/锁/循环import/reload；全局finder槽位identity/type、目标动态lookup及exec_module调用帧改变不冒称完全透明。

L1R路径代理被否决，除类型/alias/非目标/yield窗口外，hook的StopIteration挪进iternext会被吞成耗尽。标准extension reload的state skip位于import.c上层；直接PyModule_ExecDef虽可重复exec slots，也不等于原reload接线已修。没有修改解释器全局hook或运行任何安装。

修订01的预插builtins不能覆盖exec audit删键后的fallback，Root与同一独审均保1项阻塞原红。02改为C包装单次get_code后实际调用捕获原helper(original_exec, same_code, G)，保原helper frame的builtins环境，不预插或audit后补写。02子设计材料双审0不代表完整设计通过；HELPER_CALL_ENTERED不冒认builtin/module/P2已经执行。静态证据工具01因reload原文EOF138而引139在输出前停止，保原工具/STOP，02只修引用末行，候选执行消耗0。

首可信native/finder/get_code/exec/helper根及alias转交、原helper实际dispatch/D1、P2 actual return/origin/STORE、联合metadata/三D0/D2、N3/N4错误/引用/预算与>=3.11构建发布仍未闭。源码缺口关闭0、source/executionFalse、产品pass0；Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal未完成，非只剩验收。

正常库/service-dist/ACLsettings/真实平台/Git保持，canonical apply0/历史11。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。禁profiling/真实frames-locals/停点/定点计时/新异常链诊断/runtime journal，不改全局GC/trace/monitor/eval。r920/r964已耗不重放，r967不执行不重问，P06用户无历史材料0/7不再索取。

# ADR-r1111 修订02：原 PathFinder 槽位代理与原 helper 的实际 exec 接线

状态：修订02具体安装子设计候选，待本批材料双审；修订01的B001原红保留。它替代 r1110 中尚未选定的 L1 安装方向；不是完整 N0–N4/COMPAT、源码或执行批准。产品源码仍是 r1106 SOURCE02 完整159文件。本文件没有安装任何 finder、改变路径或执行 native。

## 问题、原合同与信任边界

r1110区分原get_code返回根、同根进入实际exec、实际function产物。本批解决前两项的安装接口与兼容性选择，不再复盘23个模板头部或429代码树节点。

r1075第15/37/63行沿用可信首次源码导入、原caller body和捕获primitive；第159–162行的unknown=0约束固定认证callee/getter/hash/eq/协议。它没有要求任何第三方import finder都不得运行，也不提供解释器和全部独立证据被整体替换后的自证。因此：旁路安装本身不自动证明认证失守；无原attempt/seed的P2/D0必须在认证消费前拒绝，不能从current补pin。另一方面，正常clear/reload/cycle兼容不能靠统一拒绝来冒认完成。

首可信native二进制/入口、原PathFinder与原get_code/exec/helper-builtins的取得仍须从该已接受bootstrap边界具体转交。保存当前属性、代码hash、ABI suffix或第一次看见的任意对象不能代替原来源。本批选择接口，不把这些未接线根改成已闭；不递归扩大为整个解释器自证。

## 决定一：选择原 PathFinder 槽位的 C 代理

选择一个C实现的finder代理，替换可信bootstrap时`sys.meta_path`的**原PathFinder那一个实际槽位**。不采用“在PathFinder前插入另一个finder”的版本：原查找返回None时后面的PathFinder会再执行一次，改变原次数。

该选择是显式的解释器级meta_path槽位identity/type变化，不称完全透明，也不偷偷装入。原列表对象、其它finder对象及其相对次序保持；前三方finder照原先机会优先选择spec。native、自己的bootstrap依赖和非目标模块均单次委托原PathFinder，不包装其loader；仅以下三个exact-str fullname在原spec实际返回后进入目标分支：

- `reno.personal_authority_guard_control`
- `reno.personal_authority_guard_scope`
- `reno.personal_authority_guard_registration`

代理将原fullname、path、target按原API语义交给捕获的原PathFinder.find_spec**一次**。原None、namespace spec、custom finder/loader选择以及原异常不转成自造成功。非目标直接返回同一actual result或原异常；仍会多一层C调用和固定fullname检查，不能说没有新增行为。字符串判别先exact-type后比较，不调用未知hash/eq。

目标只接受本批接线明确支持的可信原SourceFileLoader实例及实际原spec。其它合法loader不会被强改为SourceFileLoader；原选择保留，但没有可信seed的认证消费拒绝。zip/custom loader支持是尚未完成的兼容义务，不伪称已有。原package的`__path__`、spec.submodule_search_locations及其list alias保持，无路径代理。

原PathFinder body继续处理路径迭代、cache miss、path_hooks、负缓存None、自定义finder、namespace和StopIteration。`cache.clear()`后下一次正常搜索自然走原路径，无须在iterator.next中提前运行hook。`invalidate_caches`对自有代理的调用须单次委托原PathFinder.invalidate_caches，保其删除None/相对项、委托有效绝对finder及namespace epoch处理。

`find_distributions(*args, **kwargs)`也是原PathFinder的公开协议，必须用捕获的原入口单次委托并保原args/kwargs、返回iterable及其懒执行；不能遗漏后令importlib.metadata.version/entry points失去默认resolver，也不能预先list化结果。当前3.12.10代理的固定协议至少为find_spec/invalidate_caches/find_distributions；>=3.11的原方法集合需逐版本核对，不添加无界`__getattr__`从current对象转发后冒认原根。

初次安装准入要求：actual meta_path是exact-list；从可信bootstrap已持的原PathFinder identity在该实际列表恰有一个槽，且没有本代未完成安装。逐项identity检查不调用元素eq。条件不满足不能猜原位置或抢占其它finder。具体原根转交未实现，因此本条件目前不是已执行的准入检查。

## 决定二：目标原 loader 实例只包装 exec_module

原spec/module/loader对象身份保持，loader实例新增自有exec_module值；不修改其类型、类方法或get_code实例属性。finder实际返回spec后、importlib创建/执行模块前安装包装；新loader有自己的原属性状态和绑定。它调用的是捕获并前验的原get_code函数及actual原loader/name，不依赖可被替换的动态`self.get_code`查找。该查找变化是显式delta：受支持目标要求原绑定；未知override不能被当作原方法使用。

原importlib继续负责module_from_spec/create_module、sys.modules提前发布、模块锁、spec属性、循环import、partial/deferred、失败删除和成功后原sys.modules排序。C包装不自行创建第二module、不重复get_code/compile/marshal-load，不在失败时额外pop真实模块。初导入失败与reload失败保留的字典内容不同，清理不能将两者混为事务回滚。

旧loader实例可能被外部保存和再次调用，因此实例包装不能在首次exec后无条件删除。它的生命周期归所属generation及实际loader所有；generation退休后不再产生新认证seed。恢复只在实际实例字典的exec_module仍是本代自有值时还原原有值或删除原来缺席项；外部新值保持。原get_code不被写入。原loader自有字典、spec类、绑定验证、失败清理和属性写入异常仍须源码级实现审查，材料0不能代替。

## 决定三：C 将实际根和原 builtin 交给捕获的原 helper

不再执行原`_LoaderBasics.exec_module`的Python body；保留捕获的原`_call_with_frames_removed` helper。C路径取得原module.__name__，调用原get_code一次，强持其actual exact-CodeType返回；在原code非None分支取得actual module.__dict__，绑定同一exact globals，再以三个位置参数调用捕获的原helper：`helper(original_exec, actual_code, actual_globals)`。原helper的f实参为该次捕获builtin，原args为同一code/globals；其原body执行`f(*args, **kwds)`，最终builtin仍为原**两个位置参数**。不从helper globals重新lookup exec，也不重读loader.get_code。

这保留原helper的Python frame/varargs-kwargs构造及builtin自身参数处理、exec audit和向求值器传入同一code的官方路径；区别于省去audit的直接PyEval_EvalCode。移除原exec_module的Python frame、改用捕获helper/exec而非current属性读取仍是明确delta；不能声称全部trace/monitor/traceback事件完全相同。它不提供首body前fresh、整个body不可变或PEP523/监控回调不存在的保证。本批不改全局GC/trace/monitoring/eval，不读取真实frames/locals，也不通过观察frame来猜module。

原`get_code is None`分支不exec、不产生seed。原loader此时第二次读取module.__name__并格式化ImportError；不能为了复用原Python错误路径而再调用get_code。最终实现须保留该次原取值/格式化及错误类别/对象流；受支持exact-module/exact-name门的拒绝与原业务错误分列。get_code原异常原对象向外传播，不能改写成认证失败。

### B001：保留原 helper frame；不使用预插 builtins 补偿

修订01拟预插原builtins后直接C调用builtin。独审指出：exec audit可以删除该键，而PyEval_EvalCode随后重新从globals解析builtins；缺席又回当前Python frame。预插不能防止原helper环境B与新caller环境X分离。01因此有一项实质设计阻塞，原稿和红审保留，不能报成文字润色。

修订02选择实际调用原helper body，让builtin调用及audit返回后的求值仍在原helper frame中。native不预插、不在audit后补写`__builtins__`，既有值、缺席插入、audit删键后的缺席effect和后续fallback继续由原builtin/求值路径处理。C持原helper对象、原code/default/kwdict/closure/globals/builtins及实际vectorcall的独立来源，终段核后实际dispatch；完整N0及D1尚待，不把metadata一度一致冒认整个body不变。

原helper的func_builtins与原builtin exec是不同根，须从可信bootstrap转交；字典内容正常可变，不能将强持identity写成内容永不变，也不能从目标current globals重pin。原helper自身varargs/kwargs分配、错误和引用释放仍须纳入原调用顺序/预算。此处只是官方静态路径修正，未运行audit callback或访问任何运行中frame。

### actual attempt 的状态

| 状态 | 唯一可以陈述的事实 | 后继与约束 |
| --- | --- | --- |
| PREPARED | 已持该次actual module/loader/spec及generation；还无code返回 | 调get_code；其原异常进入FAILED |
| ROOT_RETURNED | 原get_code单次actual code已强持 | None走原ImportError，不产生可消费seed |
| HELPER_CALL_ENTERED | 即将/正在调用捕获原helper，original_exec及同一actual code/globals已作为实参持有 | 不声称builtin已进入、audit成功、module开始执行或P2已运行 |
| EXEC_RETURNED | 原helper正常返回；在原body调用契约成立时对应builtin正常返回 | 不是完整认证、不是所有factory已发布 |
| FAILED | 原get_code、准备或builtin失败 | 失败原因分列，原异常不被清理覆盖 |

不增加一个无证据的MODULE_EXECUTING标志。官方源码证明原helper以实际f/args调用builtin，builtin通过其检查和exec audit之后把同一local code/globals传给求值器；完整原helper实际dispatch/D1契约仍须闭合，本批不是运行中观察证明。audit再入可在module执行前产生自己的producer调用，不能把HELPER_CALL_ENTERED当作原caller已经执行P2。P2每次actual return/origin及原STORE后消费仍须独立接线，陌生调用不得占原槽或替换可信caller actual local；不采用first-wins注册。

## generation、并发、reload 与恢复

| 状态/事件 | 所有权及行为 |
| --- | --- |
| UNINSTALLED→INSTALLING | 在已接受可信bootstrap边界准备native根和记录；不持产品锁跨importlib/native加载或Python调用 |
| INSTALLING→ACTIVE | 准备成功后重核actual meta_path、唯一原槽和原实际value，再单次替换为自有代理；回调后不能用旧索引盲写 |
| 安装失败 | 只恢复仍自有的修改；不改变其它finder和外部新写入；准备产生的新引用按实际取得逆序清理 |
| ACTIVE正常child import/reload | 每次实际loader执行有独立attempt；原已加载普通import不制造新seed；当前partial/deferred按原阶段消费 |
| package reload | importlib可重设spec和path；本方案不依赖path代理。可信package bootstrap识别本代自有安装后复用，不层层包finder/loader，不把当前模块重pin原根 |
| ACTIVE→RETIRING | 先禁止新attempt/seed；实际已进入attempt按其原generation及状态完成或拒绝，不跨代借根 |
| RETIRING→RETIRED | 从当前同一actual列表按identity定位自有代理；仅自有项仍在、邻接/原排序所有权条件满足才换回原PathFinder。外部删除/替换/重排不盲目覆盖或补插 |
| 旧代理/loader晚调用 | 自有generation已退休时不重新安装、不复活seed；不能据旧对象占据新代原槽 |

“同一实际列表”仍是所有权条件：若sys.meta_path整体已被替换，不把旧列表当当前全局强改，也不将旧代理自动写进新列表。外部finder前置、删除自有proxy、复制原PathFinder形成旁路时，缺seed消费门负责拒绝；不把所有外部import一概称恶意或要求全解释器unknown=0。

原module锁不是meta_path所有权事务锁。native持GIL及自己的短metadata锁只能保护自有状态；任何Python callback、分配/DECREF可再入边之后都要重核实际容器/value/effect。锁不能跨原find_spec/get_code/exec/业务callee。并发/再入状态和逆序强引用释放、retiring记录的有界回收仍未实现/验证；预算未测，不宣称control1MiB已满足。

## 替代方案的具体结论

| 方向 | 本批处理 | 依据和代价 |
| --- | --- | --- |
| L1：只包一个cache finder | 不作为最终安装选择 | 正常cache clear后入口可能消失；缺seed拒绝可保安全，但不满足正常兼容义务 |
| L1R：package path iterator先包装cache | 不采用 | 改path类型和spec alias；无fullname，影响非目标/list(path)/namespace copy；yield后仍有清cache窗口；hook在iternext抛StopIteration会被当耗尽，原PathFinder body本应传播 |
| PathFinder前新增过滤项 | 不采用 | 返回None后会再次运行原PathFinder；不能多调用一次当透明 |
| 原PathFinder槽位C代理＋C包装原helper | 选择为后续具体方向 | 保原path/cache/hook、原finder单次调用及原helper的builtins环境；显式global槽位/exec_module帧与动态lookup变化；仍需可信根与完整源码设计 |
| L2：三个同名extension模块 | 本批不选，不作原则否决 | native缺失后的.py回落、实际package路径/打包、metadata/audit变化和标准reload skip需重接；直接PyModule_ExecDef能重复slots不代表普通reload已到该入口 |

L1R委托独立原list_iterator确实可保实时append/remove和耗尽不复活，官方listobject.c支持；这只解决取entry，不解决准备逻辑异常被next协议吞掉，不能将一个局部正确性推广成完整方案。

标准extension reload在import.c的exec_builtin_or_dynamic因state非NULL跳过；moduleobject.c的PyModule_ExecDef本身只跳过state分配，仍遍历exec slots。两者不矛盾。后者不自动补importlib锁/spec/回滚，因此L2仍有真实接线义务。

## 文件责任、实施顺序与尚未关闭的门

后续完整设计拟新增独立native模块与构建源，`reno/__init__.py`承担首次安装调用；三目标模块沿r1110的23个P2模板设计，r1106其余源码按完整清单继承。这里没有创建这些产品文件，不能凭这些暂定名称认作批准。

1. 从已接受可信首次导入边界给actual native/module/package和原finder/get_code/exec/helper-builtins逐根转交与alias终段门，补初始安装失败、先加载未seed模块、package reload及旧代持有。
2. P2 actual return/opaque origin/原STORE/effect接线；同次actual get_code根重建23模板const槽，不复用旧父槽号。联合metadata与三处D0、D1/D2仍独立完成。
3. 将本批新增槽位/loader恢复、原helper实际分派、错误/traceback、原audit、强引用取得与逆序清理和N3/N4放在同一完整设计，核原预算；不能以材料0绕过源码准入。
4. >=3.11声明保持。需CPython版本/平台native构建、wheel/source/editable/in-place、缺native fail-closed、升级/回滚安排；3.12.10官方静态依据不冒认其它版本已支持。无build-system/扩展配置及构建依赖元数据的现状仍保持。

本批仅静态设计与官方原文核对。完整设计、source_approved、execution_admitted均False；不import/运行候选、不做局部bootstrap/control模型、native build或QA。正常库/service-dist/ACLsettings/真实平台/Git不动。r920/r964已耗不重放，r967不执行不重问，P06用户无历史材料0/7不再索取。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云与完整Goal仍未完成，非只剩验收。

# 下一批：固定可信 bootstrap 根与 P2 原发布接线

当前源码父r1106 SOURCE02完整159，文档父r1111。读ADR-INSTALLATION-02、ADR-REVISION及两版双审；01的B001红保。沿已选原PathFinder槽位代理＋原helper actual f/args继续，不重开L1R路径代理、before-finder重复调用、CREATE watcher或first-wins注册。

1. 把已接受可信首次导入边界具体化为native/package/原PathFinder三协议/get_code/helper/builtin exec的原根转交、actual alias终段与安装entry。明确先已加载无seed、首次/失败/再入、package reload与代际所有权。只保存current属性不是原来源；不递归扩大到整个解释器自证。
2. 与23个P2模板的actual return/opaque origin/原STORE/effect同时接线；重建同次get_code真实根的const路径，两个已证inner local词法来源不重做。保原STORE后消费拒绝，不允许陌生producer调用抢原槽。
3. 同一完整设计闭合原helper实际dispatch/D1、联合metadata/三D0/D2、锁/原audit/异常/强引用与逆序释放/control预算；原helper带来的varargs/kwargs分配和被移除的exec_module帧delta要显式核。保持>=3.11并补native构建/source-editable/in-place/缺失/回滚，不从缺native静默回落原不安全快照。
4. 完整设计双审前不生成产品候选；完整源码准入前不import/运行候选、局部bootstrap/control模型、native build/QA或旧绿/F001。原保护、诊断禁项和限额保持；不等待重复用户批准，完整Goal active未完。
