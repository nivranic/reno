# r1147：慢路全候选与延后来源判别

r1147选择U-total作为完整慢路的有限后继方向：保原args/kwnames与单次原find_spec，不恢复actual fullname，所有支持的actual慢路返回均延后至loader实际module/name/get_code/K/G链判别。候选不授seed；相较r1111允许实际非目标包装及共享loader别名外溢，明确扩大准备/错误/恢复/frame代价。keyword后actual defaults反例否决value集合过滤；真实Proxy/loader/getter接线、原调用域/COMPAT/N4仍未闭。B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

五份既有v3.12.10源码969847B、九段原文、七份完整设计与原find_spec/exec_module的AST核对。原get_code callee选择先于name读取，None分支再读name；不先探读再委托，不用current spec/loader/defaults补认原fullname。U仅新增安装义务，既有共享loader包装可能被后续P/K或非目标观察；不再保留快速非目标永不见包装的假承诺。Unicode carrier本批未选，普通subtype分配与GC追踪仅作有限证据，不宣布所有自有carrier不可行。

本批为静态文档/read/hash/AST与有限设计交付；父r1146的30文件3400702B、159来源及保护56同/18缺核同。产品candidate/compile/import/执行、native build/load、模型、QA、诊断和新下载均0，apply0/历史11、产品pass0。原限额及禁项保持，旧单次授权与终态失败下载不重放；正常库、服务/dist、权限/settings、真实平台和Git写入未操作。完整Goal仍有工程，非只剩验收。

# ADR-r1147：慢路全候选与延后来源判别

状态：选择U-total的有限内部设计修订，待正式双审；未实施。r1146 已发布并封存，本文件不改其有限 P/K 结论，也不把后继 loader 接线算作已完成。B001/B002 open2，完整 design/source/execution/Goal 四 False。

## 1. 问题与选择

保留 r1146 的 P/K 分支：有位置 fullname，或无位置但有明确 exact-str fullname keyword 时，持原 actual value，原 args/kwnames 单次委派，正常返回后才分类。

对余下 U 分支（无位置参数且无明确 exact-str fullname key），选择 **U-total：不恢复原 binder 实际绑定的 fullname，也不从 kwargs value 建候选名字集合**。原 bound PathFinder.find_spec 仍接收全部原 args/kwnames 一次；原 bound 适配、frame 准备、比较/真值/错误及 body 均由这次原调用完成。失败走 actual first，不作包装或第二次调用。

原调用正常返回后，U 对每个满足独立支持合同的 actual spec/loader 都承担延后来源判别义务；不按名字缩小这一集合。None、无 loader 的 namespace、未知 spec/loader 的既有支持与兼容缺口分别保留。这里没有放宽它们的认证资格，也不把未知类型统一改造为 SourceFileLoader。

这是对 r1111 §决定一“非目标不包装 loader”的明确内部修订：**P/K 中可证明非目标的调用不因这次分类新增包装；U 中实际非目标也可能被包装。** 原 finder 的输入、次数和返回 spec/module/loader 身份要求保留；loader 实例的 exec_module 属性、额外准备/失败/恢复和移除原 loader Python frame 的影响扩大到 U 的假阳性。它不是透明修复，必须进入完整 COMPAT、预算与源码双审。

影响不能限定成“仅慢路可见”：原 _get_spec 可直接返回 custom finder 给出的同一 spec/loader（1506–1510），r1111 又允许实例包装持续到 generation 退休。此前 U 安装的自有包装可能通过共享别名被后续 P/K 或非目标调用观察。不能在快速非目标路径盲删既有包装，也不能把先前 U 的拥有记录重置成后一次调用的名字或来源；共享对象的条件恢复及外部替换保留必须一起设计。

U-total 的意义是删除“在 finder 返回后证明究竟哪个 keyword 绑定 fullname”这个后继接口依赖，不能据此宣布完整 loader、所有返回类型或完整参数兼容已经通过。

## 2. 为什么不能只看原 keyword value

同版 ceval.c 在 keyword 循环后才从 actual func->func_defaults 填缺失位置形参（1467–1490）。给原 bound 方法零位置参数，一个合法 str 子类 key 实际匹配 path；其原比较可把原 find_spec 函数的 defaults 改为预先持有的 `(TARGET, None, None)`。随后 binder 能从新 defaults 填 fullname 并正常进入原 body。TARGET 可以完全不在任何 keyword value 中。

这是原源码允许的条件静态轨迹，未运行对象、比较或原函数；能否被完整 D1/metadata 消费合同排除仍须独立证明。call 前观察到两个 None defaults 不提供该持续证明。本提议不靠排除此轨迹成立：U 无条件承担延后判别，所以不会因为 value 集合漏名而丢掉这个候选。

同理，key 字符文本、spec.name、loader.name、call 后 current defaults 或“本次只有一个 keyword”均不反认原实际 fullname。U 不新增第二轮 eq/真值、不替换 key、不改原 kwargs、不读取真实 frame/locals，也不使用 first/last 消费槽。

## 3. 候选不产生认证资格

以下是未来接线的语义角色，尚未选定新增字段、实例布局或枚举 ABI：

| 路径 | finder 层能证明什么 | exec 层仍必须证明什么 |
| --- | --- | --- |
| P/K 正常返回且 actual fullname 为四目标 exact str | 本次独立 actual 实参依据；不是 source/caller 证明 | actual module、实际消费的 name、原 get_code/callable、其 actual K 返回和 G、generation/attempt/source 关联 |
| U 正常返回、actual spec/loader 支持 | 这个实际返回需要延后判别；不知道实际 fullname 是否目标 | 同上；若实际非目标则不给 attempt/seed，按原业务继续 |
| None / namespace / 不支持的返回 | 只保本次原 actual 返回及既有支持边界 | 不捏造 loader、K、G 或 seed；原兼容待项保持 |

四目标仍为 catalog/control/scope/registration，按独立 exact-str 来源门识别；不把 caller 提供的字符串或 current 属性登记成 expected。U 标记仅授予执行包装所需的有限所有权，不授认证、原 callee 或 caller 身份，也不重授信/复活已退休 generation。

actual loader 的一次性属性安装、同代重复返回不叠包、外部改写不覆盖、旧 loader 晚调用、条件恢复和有界记录回收沿 r1111 所有权要求补完整实物接线。一个名字、opaque 类型或当前属性相等都不能替代这些证明。

## 4. 延后判别须与原 exec 消费在同一条链

不能先读 module.__name__ 再对非目标调用原 exec_module：这会双读，而且原表达式 `self.get_code(module.__name__)` 是先选 get_code callable，后读 name（_bootstrap_external.py 993–999）。因此未来完整 loader body 必须提供下列实际运输顺序，不能以额外探读代替：

1. 保留原 exec_module 参数绑定/receiver 的实际输入来源；完整 callable/type/signature/defaults/错误及准备顺序由后继绑定设计解决。本文件没有为它发明 METH_O 或提前拒绝 keyword。
2. 在独立支持/原根合同成立时，取得这次原业务应选择的 get_code callable，再执行原 name 读取一次；二者分别持有这次 actual 结果，不从 current 重取补证。原 getter/descriptor 的来源门必须在其 effect 前成立，不能先调用未知 getter，等 name 命中目标才追认它。对支持前提失败的原合法调用如何保留、拒绝或恢复仍须逐项完成 COMPAT，不暗加 exact-module 门关闭原任意 module/name 域。
3. 在该原 name 实际值已取得之后决定是否进入目标来源路径。目标必须认可原 callee 根后才能消费；非目标保持原动态 get_code 选择。未知 override 不因 U 被认证。
4. 用同一 actual name 调同一实际选定的 get_code 一次。原异常保持；None 分支保第二次原 name 读取、repr/格式化及原 ImportError，不为重用错误路径重跑 get_code。
5. 非 None 后，保原 helper/exec 选择、module.__dict__ 读取及调用相对顺序；非目标保持原动态选择及原返回/异常，且不创建认证 attempt/seed。目标沿独立原 helper/builtin/K/G/actual L 接线，不能从非目标动态值补 pin。

步骤2–5提出的是完整接线义务和所选非目标行为，不声称现有泛称 C/Factory 已经提供 continuation，也不声称原 callee 的选择已实现无未知 effect。实际 raw 字段、getter/descriptor/分配边、callback 后 fresh、None/error 运输、finally、partial/free、m_clear/m_free 和全 SCC/N4 均仍待。

原 finder 已发生的 effect 不回滚。find_spec 正常返回后新增包装准备若失败，是新增准备失败，不伪称原 finder 失败或未进入；actual result 和本方 first/临时引用按本层责任结清，不重跑 finder 或 loader。目标/非目标的完整错误优先级与原调用域还需逐 site COMPAT，不能靠统一拒绝旧调用交差。

## 5. 未采用的另一方向与真实证据边界

本批不选择 Unicode key carrier。已有同版 unicodeobject.c 14708–14780 显示：普通 subtype 构造先调用 type->tp_alloc，再设置 Unicode 字段并另行分配字符数据；同版 typeobject.c 1726–1738 的 GenericAlloc 会 Track GC 类型。因而不能把“构造返回后再 UnTrack”当作全过程私有性证明。自有 allocator/手工 layout/私有 truth relay 或许可以另行设计，本批没有证明它们不成立。

直接在 carrier.richcmp 中再调 RichCompareBool 可能额外引入递归/真值/cleanup 层；避免它需要原比较和真值的精确算法证据。r1132 对完整 Objects/object.c 的单次下载已超时终止，本次没有重试或换址重放；不把缺失正文的算法猜测当作已证路线。r1136 Reply 的 GC 生命周期不自动覆盖 Unicode subtype。

U-total 的新增成本是扩大受支持 loader 包装与既有恢复记录，不能沿用“仅四目标 loader”的旧峰值估计。重复返回、retiring/external roots/再入不能形成无限保留；完整物理字段/峰值/限额尚未核实。原 control 1MiB 不当作已证明的 native heap 上限。>=3.11 声明保持，当前 v3.12.10 源码文本不代表跨版本或实际 build 已通过。

## 6. 进入产品候选之前

下一步据此设计 Proxy 三协议和 loader.exec_module 的完整真实绑定；同时接上实际 module/name/callee/K/G、独立 P0/caller/source、所有生命周期与 COMPAT。只有该完整组合通过原双审门后才能生成产品候选，不能把本提议的有限认可变为 source_approved/execution_admitted。

本批只有普通静态 read/hash/AST/文档；产品 candidate/compile/import/执行、native build/load、模型、QA、诊断、新下载均0。159来源与保护56同/18缺及原限额保持；正常库、服务/dist、权限/settings、真实平台和 Git 写入未操作。P06无历史材料不再索取，r920/r964旧单次授权不重放，完整 Goal 仍有工程工作。

# r1147 后继工程

1. 完成Proxy三协议的真实callable/receiver/type、原args/kwnames、独立根与generation寿命；find_spec的P/K/U分支不多跑原binder，另外两协议保原错误及distribution惰性。
2. 完成loader.exec_module实际绑定和原参数域，落实get_code callee→name→一次get_code→None第二name或helper/exec/dict的实际消费链。原getter/callee来源门必须先于其effect，非目标动态行为与目标独立原根分别落实；不靠新增exact-module门或统一keyword拒绝关闭原调用域。
3. 把共享loader别名、同代重复返回、外部属性改写、旧代晚调用、条件恢复、失败后partial和有界回收加入完整拥有字段/state/SCC/N4。U候选不能授seed，也不能从后续current值补造原安装依据。
4. 接通Ingress/C(A)/actual L、原P00–P12唯一effect、实际新K/LOAD_NAME及A/J/Product/caller/source，补D1/D2/NULL/free/build/COMPAT/预算。完整组合双审通过前不生成或执行产品候选。
5. 继续原Task9A9B、111/94/r919、真实Store、P06和条件T15/生产云；P06历史身份无材料不再索取，不用本有限选择替代全目标。

仅复用同一只读审查代理，不新增代理；不执行模型/QA/诊断、真实frames/locals、全局hook/GC/allocator或正常库/服务/dist/ACL/settings操作，不commit/push/reset/clean。本批封存后不向其目录追加或改字节。
