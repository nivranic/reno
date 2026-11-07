# Task9A 原锁标记读取与同预算结算修订02 Implementation Plan

> For Codex: 沿用 executing-plans、同一只读复审代理、原全部限额及未安装边界。01原稿和红项完整保留；本稿优先于01相应设计，不新增授权确认或代理。

**Goal:** 关闭01的前置动态调用和异常包装歧义，再实现三个真实 marker 读点同原预算结算。

**Architecture:** resources 原工厂发行一次 `_MarkerRead` carrier，原caller按 begin→原物理前验→read→原内容/后验→body-completed 或 caller-failed 顺序消费。scope 增加只读原owner/current恢复门；control 原request/charge不改。

**Tech Stack:** 同01，新增载体复用现有独立原path-record层与custody分区；不新增ContextVar、计费账本、cap、资源获取或诊断。

## D001：前置动态dispatch必须先核来源

01后置 `_read_marker_owned` 不足以阻止之前的 `stream.fileno`。原 `_FileLock.verify_stat` 会经 `_verify_entry_stat` 调用 stream.fileno；组路径的 `_original_lock_dispatch` 还会更早访问 lock.stream/fileno/raw。Root与同一独审确认，原漏项保留。

改为以下固定内部接口（确切名称可在源码清单明确，但行为不得省略）：

1. `resources._marker_begin_owned(lock, role, *, _custody_call=None)`：在原caller第一次相关动态dispatch之前，发行原carrier并保存部分原根，执行纯subject来源前验，返回原carrier；严格无法确认时拒绝。只有明确legacy才返回None。三role只接受写死字符串。
2. `resources._marker_read_owned(carrier, *, _custody_call=None)`：只接受原工厂本次发行、尚未read的exact carrier。原目录/entry前验之后，再fresh核subject/当前scope/stream/fd，原request32→lseek/read→保存实际bytes/len→原charge；返回原bytes。不能把begin成功当成读前fresh或物理成功缓存。
3. `resources._marker_body_completed_owned(carrier)`：仅记录调用体的原内容/物理后验已走完，不能声称本函数自己的outer已返回。原resource step/现有lock-consumer wrapper继续独立记录真实outer返回。
4. `resources._marker_caller_failed_owned(carrier, actual_failure)`：只观察该固定caller从原filesystem退出后实际收到的异常，同步原scope/group/budget已有first。内部原OS异常、原caller映射后异常、后验失败和观察失败分列，不能接收成功布尔值补证。

调用点位置必须完整核：

- `_FileLock.verify`：begin早于本函数原 `verify_stat` 及 `_directory_lock_preflight_owned` 内可能触及stream的调用。已有原目录/entry前验、filesystem、内容门和后验的相对顺序保持。
- `_verify_lock_group_body_original`：对严格原tuple/list逐锁begin，早于原 `_directory_locks_preflight_owned` 和 `_original_lock_dispatch`，而非仅放在每锁entry_stat之前。legacy未知iterable不能为新preflight多消费一次迭代。对同scope全部原locks建立最多R份carrier，循环中逐份消费；前一个失败后，后续尚未读取的carrier保not-started。
- `_verify_guard_plan_files_original`：早于原context/shape检查中的动态lock dispatch。仅在已认证exact原 `_GuardFilePlan` 后，用已pin的原locks slot描述符取得tuple，再逐lock begin；不经外来plan getter。原carrier/context和完整plan检查仍继续执行。

三caller对实际异常作固定观察时，观察器若自身抛错不能替换原caller异常；保存可确认的次级错误并继续抛原first。正常body观察若失败则作为实际失败阻止返回，不补成功。legacy None不进入新观察器，保各自原fileno次数和顺序。

## D002：保原filesystem错误映射与未决债务

producer内捕获 `os.lseek/os.read` 原异常时，先保raw对象、traceback和attempt/return未知，直接重抛；不得抢在原 `_filesystem` 前把原OSError写为全局scope first。原context将它映射成原StorageLifecycleError后，固定caller的except观察器再同步实际caller首错；不增加第二层filesystem或手抄错误映射。

唯一grant保持未结算，任意重入request仍被原scope pending门拒绝。原read没有返回，不charge0清债，不猜实际长度或重试。实际返回bytes已知后，先保存实际len，再结算；结算自身失败及postclock已计费事实按原control保存，原first保持。

carrier独立原字段至少分列 request/seek/read/charge attempted/returned/result/failure、raw_read_failure、caller_failure、caller_body_completed、check_failure；只读facts不把这些布尔聚合作完整资源/费用认证。原path-record镜像与raw独立行继续核同，不能从镜像修复缺失原返回。

## 原owner/current scope恢复：禁止丢Context后的legacy降级

Modify 当前未安装 `reno/personal_authority_guard_scope.py`，在原scope工厂内新增固定只读 `_marker_owner_scope_owned(*, _custody_call=None)`，直接使用原owners索引、原owner key、原scope raw记录和原ContextVar；无budget/owner/provider参数，无Token旋转/新claim/新grant，不做guard或物理读取。

- 原current owner有已登记scope时返回同一原scope，仅允许其原scope/claims/budget/group与current核同。missing/不同current、失效owner、原镜像或索引损坏拒绝，不能返回None。
- ambient有scope而原owner索引无对应项时拒绝。两者确无strict来源时才返回None；bootstrap尚无scope时保持legacy可用，不因尚未BOUND而无条件抛NOT_READY。
- resources还要独立找回该lock的原resource anchor/root/walk。已知lock即使当前owner无scope也必须按其原scope拒绝foreign/丢Context，不能因owner门None直接fallback。
- 不全表扫描无关scope，不keep/修改foreign owner或新建root/Call loan证明；使用现有Call，任何临时loan按原finally归还。

本门只解决本操作scope身份选择和明确legacy边界，不证明任意同进程反射篡改隔离或复制Context精确身份。scope原完整认证、copied Context限制及原raw first语义保持。

## 来源、候选与验证调整

来源仍为r1060已保全的九份完整源码。补充本02全文及新双审报告，旧20pairs与01不覆盖。新完整候选37份预计 **3改34继承**：lifecycle/resources/scope。完整逆向、diff、全部静态compile和源码双审继续；当前仍无候选行为修改或执行。

新增必要验证：

- 三处原最早dynamic lookup的恶意stream/fileno/raw/plan getter，在begin前验即拒绝且getter调用0；group检查顺序早于原dispatch。
- 原scope存在但current丢失/被替换、owner索引或known lock索引损坏、foreign，不能降级；无scope bootstrap/三处legacy保持。
- producer原OSError身份与原filesystem映射后caller异常身份分列，scope/group first为原caller合同的实际异常；未知grant未清，第二次read0。
- 一个组已建立R份carrier、在第i项失败，前项原事实保留，后项未读取，未补完成；正常body-completed不冒认outer已返回。
- 独立raw/mirror损坏、重复read/body/failure观察均保第一次事实和原首错，不重试IO。

01其余原31/0/short/32/request缩小/charge后clock/同原counter和history、三callerlegacy fileno顺序、所有限额和不安装边界继续适用。Task9B动态precall全目录、完整物理/费用/terminal/容量/durable恢复仍未完成，相关旗标False，publication硬拒绝，完整Goal active。
