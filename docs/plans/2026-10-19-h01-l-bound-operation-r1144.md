# r1144：L五操作绑定与单owner退出协议

r1144选择L的get/keys/items/values/copy五条静态只读getset，返回自有LBoundOp；单owner_L payload和隐含type分账，role在untracked阶段由原静态closure固定。两guard跨分配、owner一次move，调用时再取独立L lease；Track后未return也可能被外部保留，失败只结清本方引用。外层owner guard四出口单列，type的SetDefault同名冲突/实际effect须核；L十字段不增。新目录20literal+6state=26已知schema、5类type，五descriptor内部边另计；其它NewEx/全state/source/SCC/NULL/build/COMPAT仍未闭。B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

三份完整tagged v3.12.10源413729B/原12区间/新增trampoline及receiver两区间、六份完整设计分别核对。getset路径不经普通method_get；不以初始m_module=NULL排除后续写入，也不把五条定义当五指针或完整heap界。一般平台直接getter与wasm trampoline分列；原>=3.11/实际build/布局和预算继续待项。LBoundOp owner clear及type dict clear只给两个有限环的breaker，不关闭整图。

本批仅静态文档/read/hash/AST/diff，无新下载、产品candidate/compile/import/运行/native build/load/模型/QA/诊断；apply0/历史11、源码缺口关闭0、产品pass0。159源码/保护56同18缺/原限额及禁项保持；完整Goal仍有工程，非只剩验收。

# ADR-r1144：L五操作绑定与单owner退出协议

状态：有限binding/拥有子设计待正式双审，不生成产品源码或授予执行准入。保留01/02、旧STAGED、修订diff和两次口头预审原字节，不改变r1121/r1143。选择L五个公开操作的getset及LBoundOp路线；不关闭其它native入口、原source运输、整体SCC、NULL/build/预算或B001/B002。

## 1. 固定名字及直接C来源

LType的静态spec选Py_tp_getset，放get、keys、items、values、copy五条静态寿命PyGetSetDef。每条get指向同一本方固定C getter，set=NULL，closure指向本方静态寿命只含C role枚举的五个描述记录；不含全局PyObject、用户subject或current别名。静态记录地址/role属于原C定义，不由外部实参登记。

原PyType_FromModuleAndSpec创建actual LType时，type_add_getset逐条实际生成getset descriptor，并用PyDict_SetDefault加入actual type dict。完整spec必须证明五名字没有先前同名项；有冲突或最终实际dict/descriptor/静态definition关联不合则本方准备拒绝，不能以曾经构造过descriptor推论它就是lookup值。认可使用实际原type及C静态定义，不在发布后getattr反认expected；完整源码须共同核type/slots/spec/五记录/实际effect及所有callback后的fresh。class-level LType.get是descriptor；instance-level L.get是本次getter的实际返回，均不是普通PyCFunction method_get。没有新增PyMethodDef、METH_STATIC或五个全局Python closure。

同版descrobject.c的getset_get先作原descriptor receiver检查，再直接调用静态getter/closure；它不通过method_get生成PyCFunction。set=NULL由原getset_set拒绝赋值/删除。本方不称整个lookup无callback，getter内部准备、分配和清理均单独分账。

## 2. LBoundOp实际字段和新type

新增一个原native模块代的actual LBoundOpType，用固定本方spec/原object基类/原module创建，实际newref先保C owning type local，原state按合法空slot另取得独立引用，不从public属性或类型名反认。HAVE_GC/IMMUTABLETYPE/DISALLOW_INSTANTIATION，排BASETYPE、dict/weakref、用户setter及自有PyMethodDef；固定C tp_call/traverse/clear/dealloc。不以这些flag的名字代替完整layout/build证明。

每个实际LBoundOp只拥有一个owner_L payload，另计正确Init的隐含LBoundOpType责任；role/phase为C scalar。没有m_self/m_module、可写__module__、可写owner、Python回调字段、实例缓存或公开实例化。实例的__module__等类型级默认观察仍按实际type定义处理，不能据此声称所有标准继承方法都不存在。其它原生callable及外部自行持有/构造的对象图仍属其各自边界。

## 3. 每次绑定创建的真实顺序

1. 原getset getter的实际receiver来自descriptor调用；先按原安全布局和固定role路线取得r1121活动L lease。lease拥有真实self和十字段所需独立local；L不OPEN沿固定C错误根拒绝，不从已清owner_native_module补根。
2. 使用lease实际持有的owner_native_module覆盖可回调state accessor，立即按r1140指针/indicator四组合分类；只在非NULL/无error时读state。其它格沿实际错误/具名准备拒绝，绑定产物0。此lease-module是持续根，不借未来LBoundOp的type/module边。
3. raw fresh核原state/代、L及来源、actual state-held LBoundOpType是本方原创建值；依序取得独立bound-type guard和独立owner_L guard，borrow到NewRef之间无callout。来源/布局及两guard的初始原根仍须完整源码给证据，不能用本次current快照建立expected。
4. 两guard跨GC_New/Init。GC_New返回NULL没有实例，两自动槽仍owned/maylast；正确Init成功后实例untracked、owner_L先NULL、phase INIT；role此时直接取本次原getset传入的静态closure记录并固定，不读用户subject或current属性。owner_L guard那一笔move进字段、自动槽NULL。实例隐含type成立后，bound-type guard才可条件nonlast退出；state另有type边独立计账。
5. 所有Track前可回调准备/extra释放后fresh核本次L/source/state；通过才设ACTIVE、Track。Track前失败的partial不发布；未move归自动槽、已move归partial，各清一次。Track后实例已对GC观察可见，后续lease退出可让外部取得并保留它，不再称私有。绑定期间退休不是保旧pointer继续通过。
6. 实际输出由本层C owning local跨L lease退出，退出严格沿r1121三层顺序及r1135 actual-first规则。Track后lease清理失败时，先detach并释放本local拥有的输出引用及全部temp，再还原实际first，仅scalar失败返回；不能保证实例已销毁或外部未retain，也不为撤销外部持有而退休共享L。清理可令L退休；无论外部已retain还是正常得到输出，它只保绑定owner，不保证未来业务资格，实际调用必须重新取L lease并核阶段。框架外层lookup/返回清理仍是独立责任，不被本段scalar终段吞掉。

不缓存最后bound operation、不把get属性读取当get业务已经调用、不为每lookup重建L/root expected。每次lookup一个新LBoundOp及其实际分配失败是显式成本。

## 4. 固定tp_call与退休后的调用

actual callable operand持续拥有LBoundOp。C body先检查本实例phase/固定role与owner_L非NULL，在匹配raw段独立取得call-local owner引用；不借state中的可退休L alias。接着以固定role检查实际args/kwargs形状：get仅1或2个位置实参，default省略时为原Py_None；keys/items/values/copy仅0参；这五项均不接受keyword。拒绝沿本层实际TypeError/构造失败，原L业务调用0。该顺序是本ADR选择的新协议，不冒认此前已有实际实现。

所有可能callback准备后重核LBoundOp phase/字段及同一owner，再直接C分派到r1121对应L操作；后者独立取得新的实际L lease、执行原key检查/原G或snapshot算法及原cleanup。绑定不代调用、phase不作原caller证书；call-local根只保寿命。L已退休时按原固定C RuntimeError路线拒绝，不读已清十字段，也不恢复C/A/J资格。

本类型暂只选tp_call，不宣称vectorcall无适配成本；若实际caller经vectorcall→tp_call，原tuple/keyword dict/递归检查、入前失败和返回后的adaptation temp沿r1139同版call.c另计。完整caller/COMPAT和预算仍待。已有实际结果/错误均按本层真实拥有与原L handler/finally运输，不把准备失败包装成原G查找或snapshot错误。

## 5. traverse、clear、dealloc与有限环

traverse逐边访问非NULL owner_L和actual heap type，不identity去重、不分配。tp_clear先不可调用/RETIRING，owner_L全部detach到C owned temp且字段NULL，转CLEARED后释放；重复clear看实际NULL，不调用L.retire、不清共享L内部根。没有lifetime owner例外：本对象拥有的owner边本身可以被自身clear切断。

dealloc先UnTrack，置不可调用，先detach owner并保存已有type责任，释放owner，固定GC_Del释放实例，最后释放saved type；free后不读self。已有actual first跨全部清理后最后还原；一般NULL入口、allocator/type/free/外层适配及partial仍待，不从文字外推无callback。

新有限边为LType→dict→五getset descriptors→LType，标准descriptor d_type拥有由type_clear的dict边作具名breaker；这不是所有descriptor内部字段已经穷尽。LBoundOp→L→G→LBoundOp这类owner环，LBoundOp.tp_clear切自身owner是具名breaker，L退休清G是另一条件路线；不能只靠它关闭LBoundOpType→module/state→其余source的整图。外部真实保留bound op可以延长L/type/module寿命；不得为赶走外部持有者强清其它lifetime。

## 6. 成本、兼容与未闭项

新增schema选择：每模块代一个LBoundOpType及state独立type边；LType五getset descriptor（各自内部name/type等真实边另计，五不是五pointer）；每次实际lookup一个LBoundOp，owner payload1+隐含type1，创建type/owner两guard中owner move、type独立退出。L十payload不变。不报实际sizeof/峰值、不把control1MiB当heap界。

显式接口变化：五个属性是只读getset，不是method_descriptor；LType.get观察、bound对象type/identity/repr/inspection/签名显示可能变化；每次lookup分配可失败；keyword/arity/错误先后按上表；绑定完成后退休只保证后续调用拒绝。保留L.get与subscription不对称、snapshot与generic merge区别、所有原import/公开L观察，未禁外部对象或监控。其它标准继承属性的真实可达行为不能因为这五名字而被宣告消失。

尚待：本新type实际来源/布局/创建先后、完整state字段、descriptor内部全图、实际caller及method观察COMPAT、一般NULL/partial/free、完整SCC/whole N4、>=3.11/build/预算，以及r1112其它拟议NewEx入口。r1143的24条目录保留原时点字节；本ADR选择后的新目录另列26条已知角色，不把旧目录改写。B001/B002 open2、四项完整False；产品candidate/compile/import/执行/native/模型/QA/诊断/新下载0。

## 7. 外层owner guard的四出口与Track可见性

本次BoundOp C body取得的call-local owner guard，与内层L操作自己的lease self/十字段local、实例owner_L字段是不同笔引用。外层在owner guard尚未取得前的phase/字段拒绝，没有这笔责任；取得后每条出口均须结清。

| 本层出口 | 内层L业务 | 外层独立责任 |
| --- | --- | --- |
| 参数拒绝 | 尚未进入 | 以本层实际TypeError或其构造失败为first；detach并结清owner guard与本层其它temp后还原 |
| 准备或fresh拒绝 | 尚未进入 | 保该准备点实际first/实际构造的拒绝；同上，不冒充原L查找失败 |
| 原L业务成功 | 内层完整结清自己的lease后交回actual owned result | 另持实际result跨外层owner guard及其它temp的结清；所有清理完成且错误合同允许成功才move result到C返回 |
| 原L业务失败 | 内层按其原规则交回actual error | 私有C first跨外层owner guard及其它temp的清理，全部结清后最后还原并仅scalar NULL返回 |

如果成功路径某个本方明确可设置错误的清理操作实际产生错误，先取得该实际first，再结清已owned result及其余责任，最后还原；不返回非NULL result且error已设置。既有first按r1135保持，不添日志/全局错误缓存。没有实际异常时不凭空造first；一般NULL入口、普通dealloc/allocator/free/type的indicator前提仍须逐site完成，表格不等于它们已获证明。任一可回调清理之后不再借旧fresh去新增业务effect；外层只做拥有清理和真实结果/错误运输。

Track前partial不可由GC对象枚举取得，Track后即可能被正常观察保留；未return不等于私有。Track后准备/lease退出失败仅放弃本方local引用，不承诺撤销外部实际引用。新BoundOp的可调用性始终取决于其自身phase/owner及实际L的新lease，不把创建时一次fresh冻结成永久资格。完整N4、外部retain图和兼容性仍待。

## 8. 正式目录与创建顺序的有限选择

在原T0根固定后的PREPARING区先实际创建LBoundOpType，独立C type local跨所有分配/发布准备；state仅在本代原合法空slot另取owning引用。之后才创建带五getset的LType，后续才创建实际L实例。所有callback后以原根fresh，不因state尚未ACTIVE而重捕T0；失败各按实际local/state/partial责任结账。这是具体实施顺序，未证明完整spec/ABI或安装成功。

FIELD-OWNERSHIP-01从r1143目录复制并新增LBoundOp.owner_L和state→actual LBoundOpType语义角色：L10+Factory1+Reply3+Consumer5+LBoundOp1=20 literal payload，另6条state语义边，共26已知schema；隐含实例type为5类。它们不是物理C member、运行引用次数、实例数或heap界。五个框架getset descriptor及其d_type/name/qualname等内部责任、type自身dict/mro、所有call-local/guard/结果/first仍另计；完整native census保持False。

L十字段原lease-local→减count→RETIRING且归零才detach→字段释放→self协议保持。BoundOp内层L lease与外层owner guard不能合并，Track后外部retain不以错误返回撤销。其它NewEx入口、全state/source/SCC、一般NULL/free/build/caller/COMPAT/预算的未闭项不因有限binding选择而关闭；四项完整批准False。

# 下一步：全state、其它入口和source家族

本批只固定L五操作binding，LBoundOp仅owner_L一个payload；Track后的外部retain、两guard、外层owner四出口、SetDefault同名冲突/实际effect和固定role不得在后继接线中省略。实际type/静态spec/descriptor定义/原caller仍须在同一完整源码里给来源，不从current alias登记expected。

继续枚举原native state全部真实字段：固定T0六组多根metadata、各actual type/entry/proxy/loader、活跃A/D/J/seed/Product/carrier/source bank及owner bank9/OwnerProduct。26个已知schema只覆盖已选角色，不是全state、全部对象或C物理布局；descriptor/type框架内部字段与调用临时槽分层另计。其余r1112拟议NewEx入口必须逐个选择真实绑定，不把五个L名字当其余入口已解决。

把每笔acquire→持续root→move/detach→release/partial接到同版m_clear和无需先clear的m_free表，逐SCC给真实可清边；完整一般NULL入口、actual-first/E2、allocator/free/type/Track后保留及外层适配仍待。再与原caller/P0/38context/CORE/IMPORT/owner14、D1/D2、N4、COMPAT、>=3.11/打包与原预算组合。

继续只复用同一只读独审代理；完整设计双审前不生成产品候选，源码准入前不产品compile/import/运行/native/模型/QA/诊断。历史单次诊断已耗及失败下载终态不重放，P06无材料不再索取。不改正常库、服务/dist、权限/settings/平台或未知dirty，不commit/push/reset/clean。Task9A9B/原111/94/r919/真实Store/P06/条件T15/生产云与完整Goal仍未完成，非只剩验收。
