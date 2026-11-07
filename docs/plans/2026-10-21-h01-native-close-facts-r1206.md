# I07 原生唯一关闭尝试事实：持存与同调用运输（r1206）

状态：事前方案01，尚无本批产品候选。沿用用户已选A、r1202完整适配方案、r1205未安装Python组合与r1201未安装11份C/H；Goal完整active/incomplete。本批是原生实际closer事实前件，绝不替代event/lease/debt最终组合、I02–I10或完整159，禁止独立安装/执行。

## 1. 依据与实际消费者

r1201实际selected为11份117472B，本轮Root逐项重算与其DELIVERY01身份全同。core `RenoOwnedNativeSlot`只长期保failed_close，正常关闭依赖CLOSED和当次RenoNativeResult；typed owner的retire/dealloc实际消费当次close返回。r1205两Python archive只保首次历史cache，不能从unknown或该cache推导后来实际close/event/lease结果。

选择在实际core唯一closer处先存独立纯scalar事实，由serial两个close及opaque owner的retire在同一gate内运输本次新产生的事实，并让现有dealloc实际消费。不是新增无消费者observer，不改Python current alias/DTO来补授信；不把typed pointer或fact当来源。opaque owner仍只支持pre-transfer HANDLE；fd routine仅原底层core/serial范围，不新增opaque fd转换或外部fd API。

本轮只核固定源码，不执行产品/候选/AST/compile/import/test/collection/collector/native/QA/诊断。Root两次读取不存在的旧猜测名称CANDIDATE01和resource_owner.h失败，随后使用实际目录/DELIVERY列名；未据缺文件推导产品行为，也未运行或重放任何case。

## 2. 决策、状态与生命周期

| 路线 | 裁决及成本 |
| --- | --- |
| 用Python ownership/outcome或首次cache推断真实normal close | 否决，混淆真实返回、事件及lease阶段，且当前字段可写 |
| 仅新增查询当前CLOSED/UNKNOWN | 不足，既没有实际返回status，也可能把旧历史当本次attempt |
| core实际closer写一次scalar事实，同gate当次运输，现有dealloc消费 | 选择；扩大原native布局/返回ABI与sizeof成本，必须完整重建/复审，当前不安装 |

在core header的RenoNativeResult之后增加：

```c
typedef enum {
    RENO_NATIVE_NO_CLOSER = 0,
    RENO_NATIVE_HANDLE_CLOSER,
    RENO_NATIVE_FD_CLOSER
} RenoNativeCloserKind;

typedef enum {
    RENO_NATIVE_CLOSE_NOT_STARTED = 0,
    RENO_NATIVE_CLOSE_STARTED,
    RENO_NATIVE_CLOSE_RETURNED
} RenoNativeClosePhase;

typedef struct {
    RenoNativeCloserKind closer;
    RenoNativeClosePhase phase;
    int returned_status;
    RenoNativeResult native;
} RenoNativeCloseFact;

#define RENO_NATIVE_CLOSE_FACT_INITIALIZER \
    { RENO_NATIVE_NO_CLOSER, RENO_NATIVE_CLOSE_NOT_STARTED, 0, \
      { RENO_NATIVE_INVALID_STATE, 0, 0 } }
```

`RenoOwnedNativeSlot`在原failed_close之后追加`RenoNativeCloseFact close_fact`，fresh宏追加对应initializer；session initialize_fresh原样通过完整fresh值初始化该字段，不增加live reset或复制session。fact只描述本slot唯一一次受管close。NOT_STARTED除phase外无完成含义；STARTED只有closer/phase可消费，不把returned_status初始0当成功；RETURNED才使status/native有效。HANDLE status为actual BOOL转int，FD status为actual _close/close int；native仍按原规则分类（FD status<0才失败，不能顺手改成status!=0）。不保存新的正常raw HANDLE/fd。

fact是纯C scalar，没有新增PyObject owning occurrence/type/module/SCC边，仍有实际owner的全部原根/allocator/last-reference义务。额外嵌入字段和返回struct扩大大小/ABI，不能在未构建时报固定字节或声称原heap预算已闭合。旧failed_close字段/历史数字语义保留，不从新fact授borrow/closer/retry/refund。

## 3. core真实producer，严格不重放

仅修改原两个close函数：

- HANDLE原null→stopped→borrowers→state检查次序保持，在最后state检查中附加close_fact.phase必须NOT_STARTED，避免覆盖旧事实。已实际消耗active handle并标ATTEMPTED后，设closer=HANDLE、phase=STARTED，随后唯一一次CloseHandle。新增`BOOL status`保存actual返回，失败分支第一项仍GetLastError，不能插其它API。原failed_close/UNKNOWN提交保持；再写close_fact.returned_status=(int)status、native=原failed_close.failure、phase=RETURNED后原return。正常分支原CLOSED保持，再写actual status、原OK result、phase=RETURNED并返回该result。
- FD同样只在最后state gate加NOT_STARTED，原fd=-1/ATTEMPTED后设closer/STARTED；原Windows `_close`/POSIX `close`各保持唯一句法调用，失败第一项仍errno捕获，原failed_close/UNKNOWN保留，再写status/native/RETURNED。正常保持CLOSED，写原status及OK/RETURNED。原stopped/borrow与close attempt语义不放宽，不新增EINTR/OS重试。

不动Create/Duplicate/transfer/begin/end/stop，不允许新的初始化入口、raw/fd/adopt/setter或flag宣称“已退休”。新增phase gate在现有合法fresh→获取/转移→唯一close图成立；任意直接篡改native storage仍不在本有限合同内，不把此核当来源认证。非本地SEH/longjmp或不返回native调用仍无保证；STARTED不伪造RETURNED。

## 4. 同调用运输与现有dealloc消费

serial header在原CloseAttemptFailure后新增纯scalar envelope：

```c
typedef struct {
    unsigned int available;
    RenoNativeCloseFact fact; /* payload valid only available == 1 */
} RenoWindowsCloseAttemptObservation;
```

向`RenoWindowsSerializedResult`和`RenoWindowsHandleOwnerCloseResult`末尾各追加`close_observation`；原字段顺序与含义保持。所有原零初始化继续令available=0；非close entry、NULL/BUSY/stop失败、core拒绝、already terminal不得把零payload当actual OK。没有新增Python公开符号或normal-close历史查询API。

在serial HANDLE/fd close的try gate成功后、原core调用之前，取本次`RenoNativeClosePhase before_close_phase`（在gate内）。原core_called及唯一core调用不变。在core返回后、unlock之前，仅当before为NOT_STARTED、当前fact为RETURNED且closer匹配本entry时，实际复制fact到本次close_observation并最后置available=1。核心phase单调且不重置，故后来拒绝不会重放旧RETURNED。旧close_failure及fd转移view退休逻辑原样，raw历史仅原失败路径保留。

opaque owner的`retire_handle_only`也在原gate内、唯一core close前取得before phase，返回后同规则复制HANDLE fact；不绕过原stop/borrow gate，不在already_empty_or_terminal分支制造新observed fact。现有dealloc更改两处实际消费：

```c
if (closed.close_observation.available
        && closed.close_observation.fact.closer == RENO_NATIVE_HANDLE_CLOSER
        && closed.close_observation.fact.phase == RENO_NATIVE_CLOSE_RETURNED
        && closed.close_observation.fact.native.outcome == RENO_NATIVE_WINERROR) {
    PyErr_SetFromWindowsErr((int)closed.close_observation.fact.native.saved_winerror);
    /* original unraisable handling */
}
```

原invariant分支中，非already_empty_or_terminal的正常退出要求available且HANDLE/RETURNED/native OK，替换原close_called/close.outcome判断；stop/admission条件与原PyErr处理/free/type/first还原全文保持。原close_called仍表示core被调用，不能冒认OS已调用；close字段保持供原typed消费者使用，但actual fact成为这个真实dealloc消费者的依据。合法原图下正常/失败/terminal/BUSY原分类保持，整体COMPAT/ABI未运行验证。

## 5. 文件责任与完整差异

| 文件 | 本批责任 |
| --- | --- |
| reno_owned_native_slot.h | 纯scalar enum/fact/init、slot末尾字段/fresh宏及有效性合同 |
| reno_owned_native_slot.c | 两actual closer的phase gate/唯一status保存和RETURNED事实，不动其它函数 |
| reno_windows_serialized_slot.h | 当次envelope及result末尾字段/语义，不新API |
| reno_windows_serialized_slot.c | 两actual close在同gate捕获当次新fact，原failure/view逻辑不动 |
| reno_windows_cpython312_handle_owner.h | result末尾字段、actual-call与原close_called区分 |
| reno_windows_cpython312_handle_owner.c | retire同gate运输，dealloc真正消费，原traverse/factory/Create/query/Duplicate及失败snapshot保持 |
| 其余五C/H | 原字节继承：factory C/H、audit C/H、result H；不新增adapter或隐藏调用 |

选中组合仍11份完整C/H；before固定r1201 selected117472B。r1205 Python三源不改，不用新fact倒写首次cache，不声称最终event/lease/debt已归档。完整source/C bank/native owner binding仍需要实际constructor与Ingress/Consumer接线，不能用current alias或DTO认领。

## 6. 事前复审与验证矩阵

先Root/同一只读代理全文设计双审，之后才写候选；再实际源码双审及每块唯一文字正逆、完整11源字节等式，明确六改五同。Root生成/metadata不冒认独立事前审。只文字/JSON/bytes/hash/diff，不C/Python AST/compile/import、测试/collection、collector/native/QA/诊断。

| 情形 | 所需合同，未运行 |
| --- | --- |
| fresh但无资源或已terminal、stopped不符、borrow未退、BUSY、NULL | 不改变旧close_fact，new envelope available0，不把旧历史当本次call |
| 实际HANDLE close TRUE/FALSE | STARTED先于唯一call；失败first GetLastError保原；actual status和原typed result持存，正常/失败都RETURNED |
| 实际fd close各平台正常/errno | 原唯一call/status分类/errno时点保持，消费一次后不能重新close |
| 本次core拒绝而slot已有RETURNED | before phase非NOT_STARTED，new available0；原failed snapshot仍可读旧历史但不证本次attempt |
| close正常，后续Python event/lease/表示失败 | scalar事实在native slot中，但本批没有接该Python caller或最终归档，不能标I07完成 |
| dealloc首次owned、已closed、UNKNOWN、EMPTY、invariant错误 | 用本次fact判实际normal/failure，原first保存/释放次序保持；fallback close不作业务正常记账 |

核心C存储持续有效与原serialization、attached GIL/full API/CRT、合法actual owner/source持续根都仍为前提。A仅排除外部直接操作资源，不豁免本方Python registry、unknown-callee/getter/hash/eq/fresh/N4。opaque owner pre-transfer限制仍真实有效，没有新fd/audit/raw出口，不用A冒认当前原source已适配。

## 7. 未缩减范围与回退

原预算30calls/2children/1990365B和已过截止不重置，r920/r964已耗、r967不执行；不重放旧QA/失败/r1173修订04及21未跑，不新增异常链/frames/locals/notes、profiler/marker/native微诊断。正常库/live/服务/dist/权限/settings和Git不写，只复用r924代理。未安装所以没有运行回退；红稿保留，不reset/clean。

仍缺完整event/counter事务、lease部分提交、first/持续OOM/异步、最终debt跨退休关联、实际owner↔holder/Ingress/source bank、五资源consumer、全字段/NULL/N4/SCC、构建加载及>=3.11多版本支持。本C候选仍限CPython3.12.10，不缩减产品支持域。完整H01/B001+B002、H02、H03/H05/H04/H06、分享撤销、P06缺证、条件T15/生产云保持未完成。P06无备份不再问，A不再问，不标Goal complete/blocked。
