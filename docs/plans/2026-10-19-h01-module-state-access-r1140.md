# r1140：私有module_guard与state accessor准备区

r1140选择先从actual Factory/type/module链取得私有C module_guard，把公开state accessor放可回调准备区，再按state指针/raised indicator四组合处理；NULL/NULL选择工具call前的具名RuntimeError准备拒绝，actual error不被phase核覆盖。后继raw段只为两type guard取得，Consumer分配后及真正消费前fresh仍保留；module_guard成功nonlast须实际Consumer→原type→同一module独立根持续，失败maylast沿actual first。m_free可未先m_clear而发生，两入口须到同一逐字段清理；完整state/caller/一般NULL/N4仍未闭。B001/B002 open2，完整design/source/execution均False，产品仍r1106 SOURCE02完整159未安装，Goal active未完。

三份完整同版source/doc384910B、八段原文及四组合/三退出表仅支持有限静态合同，不冒认缺失内部header无callback或本机build。新header单次Timeout终态0retry，旧失败不重放；borrow→acquire raw/build、成功indicator/错误构造及完整字段/预算/COMPAT保持待项。原01/02草案与口头预审记录保留，不以材料通过代完整源码准入。

本批candidate/产品compile/import/运行/native/模型/QA/诊断0，apply0/历史11、源码缺口关闭0、产品pass0。159源/保护56同18缺/原有限额和禁项保持，原>=3.11不缩减；完整Goal仍有工程，非只剩验收。

# ADR-r1140：模块状态读取进入准备区，私有 module_guard 覆盖

状态：有限子设计待双审；承接r1139封存，不改变其任何字节。原01/02候选、口头预审及失败下载保持。产品仍为r1106 SOURCE02完整159文件、7,956,657 B，未安装；B001/B002 open2，完整design/source/execution/Goal均False。

## 1. 决策及原依据

选择把公开PyModule_GetState调用放在允许callback的准备区，先以本次真实module的私有C owning guard覆盖它，之后才检查state指针/raised indicator和各原phase/source/type字段。这样不要求尚未取得的内部inline body无callback，也不把指针非NULL当作业务成功或fresh证明。

已有同版完整module.rst93–97规定返回模块state内存指针或NULL；163–179规定模块创建分配、模块dealloc在m_free后释放。完整moduleobject.c416–440给出实际md_state分配/清零先于exec slot，704–726给出m_free之后free。已读module_clear883–901只调m_clear和清dict，没有在该wrapper内free md_state。state内存寿命与state中可退休字段的寿命是两件事。

module.rst216–250特别说明m_clear不总先于m_free；引用计数可直接进入m_free。本方m_free必须独立到达同一真实字段的detach/释放路线；不能依赖GC已经清过，也不能从m_clear/free模块拥有的md_state区域。partial初始化和重复clear必须按实际NULL/nonNULL责任结账，完整全字段表仍待。

本批新目标Include/internal/pycore_moduleobject.h单次Timeout终止，attempt1/retry0，不重放、不换地址。内部inline body仍缺失；公开API合同和上述区间不是它无callback或实际binary/build的证明。原>=3.11不缩减。

## 2. 取得 module_guard 的前置区间

从真实caller callable operand持续持有的actual Factory进入原固定C body，Factory/type来源沿r1139实际创建路线，不能从current module alias/type名称/用户实参登记。原type→ht_module所建module关联有同版typeobject.c4545–4577依据；借用返回的module必须在实际Factory/type根持续的、已匹配full API非debug raw区间内取得一笔私有C module_guard，borrow→acquire之间不允许callback、DECREF、allocator、锁等待或释放GIL。

这个取得窗口的完整宏/layout/build证明仍独立待项。将后续state accessor移到准备区，不豁免此前窗口；也不以Factory身份替代caller/业务source资格。module_guard不保存在可被退休清除的state、A/J/Product或Python容器，不是最近槽、reservation、BUSY、first-use或秘密。

module_guard取得后跨公开PyModule_GetState及本层其它准备。即使accessor合法运行callback或释放GIL，只要该guard、本方真实module和API合同成立，内部退休不能靠移走state字段销毁module及其owned state区域。本方m_clear仅detach自己的字段；任意外部C破坏内存不属于此合同。返回void*只在这些物理前提下作为原state指针使用，不创建替代state。

## 3. state指针与raised indicator的四种组合

下面是本C caller准备层的合同，不是三旧getter try。Factory自身tp_call适配/递归门可能在C body前失败，沿r1139另列，不能冒认guard已取得或Consumer已创建。

| state指针 | raised indicator | 选择的处理 | 原工具call |
| --- | --- | --- | --- |
| NULL | actual error | 不解引用；移动这笔实际preparation first到私有C local，先清本层资源，最后还原 | 0 |
| 非NULL | actual error | 仍按本层失败，不让phase核覆盖或清掉first，不继续成功业务 | 0 |
| NULL | NULL | 本caller准备点以原C运行时RuntimeError根构造固定消息 `native module state unavailable` 的准备拒绝；构造失败使用实际错误 | 0 |
| 非NULL | NULL | 仅具备继续核原state/phase/type来源的条件，不代表业务准入 | 尚未call |

NULL/NULL拒绝是显式新增准备错误，选择发生在六工具call之前，不能塞进三旧cell getter try或冒充原getter ValueError。原错误root来自匹配runtime的原C数据而非current Python alias，API/构造与实际first取得仍须future源码核对；若构造违反C API错误合同、没有实际异常，不能凭设计文字捏造first。完整错误观察/原caller finally和COMPAT仍待。

非NULL/error这一格是防止把任意成功指针当成功的完整分类，不声称本机该API已实际产生这种结果；NULL/error也不把异常重新命名成统一拒绝。前两格的actual first只管本C层资源，原业务handler/finally在各自层自然完成，不承诺跨层永久first优先。

## 4. accessor之后的raw核与后继fresh

当且仅当指针非NULL且raised indicator允许继续，准备结束后在本段raw区重新读本代实际phase、原ConsumerType/ReplyType及来源字段，分别取得两笔独立type guard。不能借accessor之前的phase或旧borrowed type指针；callback可以合法退休state，此时拒绝，不能从current属性补expected或重新登记。

这个raw段只关闭本次type acquire准备，不是整个业务的最终准入。Consumer GC_New、其它source填充及适配清理仍可callback；两type guard只保type寿命，module_guard只保state内存，均不冻结source/phase。Consumer创建后、全部可能callback准备后及真正消费前的实际fresh门全部保留；P0、38context、CORE、IMPORT、TMD、D1/D2不因本表被删。

Consumer的ReplyType guard按r1139 move到lifetime字段，ConsumerType guard的成功nonlast由正确Init实例独立type根覆盖；失败可能last、partial移动责任分开。module_guard是第三种不同owning责任，不能因identity相同与type guard去重。

## 5. module_guard的三类退出

| 路线 | module_guard如何结清 | 前提与未闭边界 |
| --- | --- | --- |
| 成功且实际Consumer链持续 | 输出仍由本C local实际owned，且正确Init的Consumer→原ConsumerType→同一module独立根持续时，detach并沿匹配nonlast包络释放guard；之后才转移输出 | 不借已移交输出的未来根；实际源码/宏/build与source freshness仍待 |
| 失败或准备拒绝 | guard可能last；先建立本层actual first，移到私有C local，结清所有guard/partial/temp/free后按r1135最后还原，仅scalar NULL返回 | 不套成功nonlast，不凭空造first；本层中途indicator前置条件仍逐步核 |
| 所谓成功但独立根尚未证明 | 不采用成功nonlast出口，不声称一般NULL清理安全；须补真实链或修订源码级合同 | 本批不给猜测性fallback、不漏释放或常驻quarantine |

失败时的actual first来自四组合中真实错误或本层准备拒绝的实际构造结果。先结清module_guard及其他本层资源，再还原；原finally仍可在外层覆盖。成功路径不得在可能callback的释放之后沿用旧fresh；当前选择nonlast需匹配r1138有限包络，debug/limited/其它版本和实际build仍待。

module_guard持续到本层最后一次真实state内存观察；输出实际Consumer根只作为成功guard退出的独立覆盖，不自动授予source资格。所有detached temp仍须真实释放一次，不能把指针置NULL当责任已结清。

## 6. 预算及后续原范围

新增每实际Factory C调用一笔module_guard acquire/release及一笔峰值拥有；tp_call的条件tuple/kwargs适配仍沿r1139另计。原Factory已有间接module根不让新guard变成零成本；state内存不移交给guard，不分配第二份state，不增加跨代登记或历史getter/error槽。完整heap/refs峰值与所有caller仍待，不把control1MiB当native heap上限。

必须继续module state全字段、Consumer/source/Product/A/J/owner14/bank9每条SCC、m_clear和无需先clear的m_free、一般NULL→NULL、成功allocator/错误合同、原caller/真实参数、D1/D2/完整N4/COMPAT及>=3.11平台预算。原17lease/1449slot/406尾/五S002及case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10保持。

本批仅静态read/hash/AST/diff和文档交付。完整设计双审前不生成产品候选，源码准入前不产品compile/import/执行、native build/load、模型、QA或诊断。不改真实库、服务/dist、ACL/settings、平台或Git，不重放旧单次诊断/失败下载，不profile/真实frames-locals/停点/计时/runtime journal或改全局GC/trace/monitoring/PEP523/allocator。P06历史0/7无材料不再索取；Task9A9B、原111/94/r919、真实Store、条件T15/生产云等完整范围仍有工程，非只剩验收。

# 下一步：逐字段state图与源码级退出

以实际native创建的根为起点，补同版本module state/Factory/Consumer/A/J/Product/source bank的真实字段表，每个owning slot给acquire→持续root→detach→release与partial、重复clear、m_free直接调用路线。module_guard只保内存，typeguard只保类型；source退休、Consumer分配后以及实际消费前的fresh必须分别接回，不以“有guard”替代。

给NULL/NULL准备拒绝的原RuntimeError根/实际构造与四种指针indicator组合、三个guard出口逐步源码核对；一般NULL→NULL、成功allocator、frame PREP与完整SCC的释放/重入、NULLindicator和COMPAT仍待。原三cell late matcher/try外ACCEPT、原finally与actual first分层不变。

原P0/38context/CORE/IMPORT、226候选叶/7append/3reply/14ownerproducer、D1/D2/N4/COMPAT及>=3.11/平台预算仍须完整组合双审；此前不生成产品候选、不产品compile/import/运行/native/模型/QA/诊断。全部保护/旧红/原限额及已耗授权保持，P06历史0/7不再索取，Goal active。
