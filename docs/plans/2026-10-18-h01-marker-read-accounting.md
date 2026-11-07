# Task9A 原锁标记读取与同预算结算 Implementation Plan

> For Codex: 沿用 executing-plans，复用同一只读复审代理，不新增代理或普通开发确认。当前 dirty 树保持，不作 Git 操作；仅生成未安装完整候选。

**Goal:** 将三个固定 marker 读点接入同一个原 Budget 的 request/charge，保留每次实际读取与结算事实；不把局部计费升级为完整资源认证或发布权。

**Architecture:** 承接 r1055 的37份候选和 r1059 的158文件完整代码组合。原 resources 工厂持有实际 root/lock/stream 来源，在该工厂内增加固定 marker producer、独立原记录及只读事实门；lifecycle 三处读点先调用固定 producer，明确 legacy 返回才执行各处原始读法。原 scope/control 账本继续唯一计费，无第二 counter 或 cap。

**Tech Stack:** 原 Python 包、custody Call/原分区、scope 原 ContextVar/Token、原 Budget/control ledger、os.lseek/read、io 原描述符、有限 AST/bytes/SHA 和具名隔离验证。

---

## 已知事实和方案选择

r1059 三个旧失败ID定向实际3通过，10.61秒；旧r1058的0通过/3失败仍保留，合计6次尝试、3唯一ID最新通过。完整准备链已观察原 profile owner、pending admission、预算绑定、binding/group、scope 安装、原 grant/history 配对和 Token reset。没有进入 EX 或调用资源业务 API，因此不是实际 marker 或资源关闭证明。

r993 DESIGN03仍是原计费合同：marker31字节、完整请求32字节；第一root的首次读取发生在guard创建前。严格 scope 在EX前已安装。resources 原记录覆盖18阶段，其中读取发生在第18项 lock_verify 内，第1至17项已实际返回。后续组校验复用同一实际锁，仍必须每次真实读取。

选择在 resources 原工厂内增加 producer，因为原对象来源、原 custody 分区、失败同步和 root 历史均已在此。将独立计费表放在 lifecycle 会重复保存 owner/root 图并引入双来源；仅根据 ambient scope 对任意 fd 收费不能确认原锁来源。两种方案均不采用。

## Task 1：保存确切来源并完成设计双审

Create `data/t16-h01-marker-accounting-design-r1060/`。保全以下完整来源及其当前 bytes/SHA，而非仅引用摘要：

- r1055 SOURCE37 与两份源码审、r1059 BUNDLE158、三项实际结果与两actual审。
- r993 DESIGN03、当前本方案。
- 当前候选 `reno/storage_lifecycle.py`、`reno/personal_authority_guard_resources.py`、`reno/personal_authority_guard_scope.py`、`reno/personal_authority_guard_control.py`、`reno/personal_authority_guard_registration.py`、`reno/personal_authority_guard_custody.py`、`reno/personal_authority_guard_pending.py`、`reno/personal_authority_owned_ex_deadline.py`、`reno/personal_authority_owned_ex_group.py`。

Root 与同一代理核以下顺序和边界，0阻断后实现。调查只做原文件完整读取/AST，不导入候选、不运行诊断。

## Task 2：原 subject 准入与独立 read 记录

Modify 未安装完整候选 `reno/personal_authority_guard_resources.py`，不改 canonical/c973。

1. 新固定 `_read_marker_owned(lock, role, *, _custody_call=None)`，role 只接受三处已写死的字符串，不接受 fd、reader、provider、budget、成功值或调用者提供的 pin。固定描述符、read/lseek、SEEK_SET、marker、io 类型/原 raw-buffer 关系和新函数纳入现有初始化目录；保持旧目录全部成员和 bootstrap 次序。
2. 先由独立原 resource anchor/root/walk 与 owners 索引找回锁，不能只信可能被替换的单索引。已知来源、原 scope/owner 或当前 scope 仍存在却无法核同必须拒绝；不得因 ambient Context 丢失、镜像损坏、foreign subject 或实例覆写转成 legacy。legacy 仅在没有该锁的严格来源且没有本操作严格 scope 的情况下显式返回 None。
3. 在任意 stream/fileno 动态调用前，核 exact lock/stream/directory/journal 类型、原字典与槽、原 root ordinal/path、原 binding/token、scope/group、原 caller 返回链和目录 walk 当前状态。第一读要求1至17项完整且本次第18项唯一在途；后续读要求原第18项和外层 producer 返回完成。两种状态分别验证，不能把旧 prefix 直接视为当次读取成功。
4. 核原 stream constructor 的十次实际调用、四原 list 和 binding setter return/effect；核当前 raw/buffer/fd 仍为原对象/值及合法 owned 状态，原 buffer.raw、raw.fileno、raw.closefd、closed 通过已 pin 原描述符读取；fd 必须 exact int、非负且与原 open_osfhandle 返回一致。校验不获取新资源，也不调用 guard.verify/live/lineage 触发递归 marker。
5. 每次 invocation 先登记唯一 attempt 和部分原根，记录 role、原scope/group/root/ordinal/lock/stream/fd、实际 request/seek/read/charge 的 attempted/returned/result/failure。独立原记录与可变观察镜像分开；镜像不能作为缺失原返回的补证。部分失败保根，不覆写旧记录或重试同attempt。

原 `_pre_marker_facts_owned` 及 `physical_resource_chain_authenticated` 等全部认证旗标仍False；本段不改它们为True，不生成完整费用或发布证明。任意Python反射、全C/native、最终probe到IO的完整Context闭包另列待办。

## Task 3：request → seek/read → charge 顺序

同一个原 `_Budget` 的固定入口继续使用原 control/scope 逻辑；不新增计费限额。

```text
登记 attempt/原根 → 固定来源/当前状态检查
→ 原 request(32)，保存实际返回
→ grant 必须 exact int32，否则原 LIMIT，read 不发生
→ 原 lseek(fd,0,SEEK_SET)，保存实际返回/错误
→ 原 read(fd,32)，先保存原 returned bytes 和实际长度
→ 原 charge(actual_len)，保存 return 与原账本 effect
→ 返回原 bytes，由原 lifecycle 内容门和原后验继续校验
```

- request 不预扣；成功 marker 收31，EOF/short/32字节损坏也先结算实际0至32，再由原内容门拒绝。
- seek/read 抛错无实际 read 返回，保未知债务和首错，不猜0、不调用 charge(0)清债、不重读。grant<32也保原未决状态，禁止后续新读。
- 读取已返回后，必须先保存实际字节/长度再结算；charge后clock失败仍保存原counter/ledger已应用事实；原first同步不覆盖已有错误。
- 同一attempt不重复request/read/charge。任何验证/观察错误都禁止新业务读，已有资源的原raw清理独立保持。
- 不因为观察失败补造 returned/effect，不绕过原LIMIT、clock或scope认证，也不删原fresh/callback。

## Task 4：三个原读点接线，保各自 legacy 合同

Modify 未安装 `reno/storage_lifecycle.py` 三处：

- `_FileLock.verify`：保原前/后 verify_stat 和 filesystem；严格 producer 返回原bytes后进入原内容门。明确legacy时保原两次stream.fileno lookup及原lseek/read顺序。
- `_verify_lock_group_body_original`：保原两fresh、plan/carrier、每锁entry前/后检查和finally；每锁严格 producer 使用固定组角色。明确legacy时保原lseek和第二次fileno读取，不改custom原fallback。
- `_verify_guard_plan_files_original`：保原guard/context/全部lock/database/state检查；严格 producer 使用固定plan角色。legacy保原 captured `_LOCK_STREAM_FILENO_ORIGINAL` 只读取一次fd的合同。

仅接三处实际读点不足以证明所有动态 caller 都不能绕过校验：`guard.allows/verify`、外部 Post/source/transfer/completion precall 目录和未知 callback 门仍须 Task9B 全闭包接齐。本段保未安装、发布硬拒绝和认证False，不以“未发生marker”推导零费用或允许发布。

## Task 5：完整候选、双源码审与具名验证

Create `data/t16-h01-marker-accounting-candidate-r1061/`，37份完整候选预期2改35继承；若准入必须修改 scope 等实际来源，则先精确说明增量并重新完整复审，不默改受审版本。存完整父本/候选/逆向/全文diff，静态compile全部；同一代理与Root源码审后，才准备新的具名验证。

验证覆盖：三个真实callee读点、首marker无guard/后续group、31/0/short/32/read抛/seek抛/grant缩小/charge后clock错、同原counter/原grant-history配对、fd或buffer替换/原list或独立来源损坏/foreign/上下文丢失/未知role/重复attempt、已有first与原清理保持、legacy各处原dispatch和fileno顺序。合成OS/native检查与实际文件读取分列；完整源导入与AST摘取分列，不把替身或受控异常称自然OS故障。

脚本及实际准备继续双审，原限额不变、新单次claim。只复检新失败ID，不重跑r1056旧12或r1059已绿三项来补数。真实资源级QA若当前固定闭包或清理边界未核清，先完成对应静态设计；不以fake资源冒认完整EX运行。

## 完成标准、交付和回滚

本段完成标准是：三处实际读取均已接唯一预算；原 subject/actual read/charge 事实按具名测试核对；双源码、脚本、准备和实际记录复审齐全；主方案与AGENTS如实更新。只有实际观察过的范围能写通过。

回滚为不安装候选，保全原版本及所有红记录。完整费用/资源债务、Task9B precall动态目录、guard consumer、最后Call两阶段terminal、容量/durable恢复、原111/94/r919和真实Store写权、H02–H06/永久分享及Goal仍active未完成。

case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/并发1/最多2pytest children、`-I -B -X utf8`和最小环境保持。无profiling/frames/locals/停点/新异常链诊断，r920/r964耗不重放，r967待旧问题不执行重问，P06历史0/7不再问。正常库、服务/dist、ACL/settings、平台和Git保持；canonical/c973本段apply0、历史11、产品pass0。
