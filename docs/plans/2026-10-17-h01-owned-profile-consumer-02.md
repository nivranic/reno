# 显式 profile owner 到 owned 主体与 scope/root 的接线

2026-10-17；r1023 DESIGN02。依赖 r1022 SOURCE01 的私有创建入口及其具名检查，继续 profile-route-custody Task4–5；不把 Task6–7 或完整目标提前关闭。

## 已核来源与边界

`data/t16-h01-owned-profile-consumer-r1023/INVESTIGATION-01.json` 保存21份完整来源970604bytes。指定文件内六个函数定义、两个直接调用 site；三个 owned 主体均没有 `_construct_group_origin_owned` 调用。两个 site 是公开 publish/verify wrapper 到 `_owned_with_actual_ex`，Store/joint 私有函数在这批指定源中没有外部直接调用者。动态/别名未排除，不能编造已有应用路由；应用长期持有和新调用者属于 Task6。

原 helper 能记录 factory/group 实际返回并赋 budget._owned_ex；`_claim_joint_origin_owned` 会同时完成 scope claim 和 install，不能重复 install。原 `select_paths` 只保存当前 profile.__dict__/四个 Path，尚未消费 r1022 创建时持有的 owner。原 scope reset 只重置自己取得的 Token，不释放 root/物理债务；custody._finish_operation 仍明确拒绝，不能在本段把它改成成功。

## 本段的显式输入

修改十二文件候选中的 enrollment 和 guard_scope，其余十份逐byte继承。三个 owned 主体、两个公开 owned wrapper 增私有 kwonly `_profile_owner` 与 `_custody_lease`，默认均为捕获的唯一 omitted sentinel。两项均省略保持原 legacy body；显式 None、坏 owner、缺一项不转 legacy。strict 调用者必须在进入主体前持有两个实际对象：配置创建 owner 与 custody root lease。主体不悄悄新建一份 owner/lease，不从同 identity profile 查找来源。

strict 操作保留原参数、deadline/budget及实际 profile.identity 顺序。检查两项成对及原 lease 后，以同一 lease 调实际 `_construct_group_origin_owned(..., _profile_owner=...)`，随后对实际返回 origin/group 调唯一 `_claim_joint_origin_owned`。factory/group/budget 记录、原 EX with、内容/source/Store/joint 主体、原 consume 与 packet retirement 顺序保持。原三个直接 binding/group 构造块仅留在两项均省略分支。strict 的自定义 callback 无固定来源，须在调用 callback 前明确拒绝；legacy callback 行为保持。不能用“新增 helper 但无人调用”作为完成证据。

helper `_construct_group_origin_owned` 增 optional 私有 `_profile_owner`，把同一对象传到原 `_origin_begin_owned`。scope raw `_OriginAttempt` 和 `_Scope` 新增 `profile_owner`，另存创建 owner 的实际历史快照/原 Cdict/四个实际 Path；原表示当前线程/task 的 `owner` 字段保持含义。原 records 创建、descriptor 清单、function/consumer/bootstrap 目录、raw 镜像及函数签名调用点全部同步，不扩展 DTO 或 JSON。

## scope/root 消费创建事实

在固定 bootstrap 捕获原 lifecycle owner getter 及其直接递归函数闭包目录。源函数/code/default/kwdefault/closure/cell 身份先核再调用，不能认证后改调模块当前同名函数。不用尚未初始化的 control helper 提前封 pin；最终控制类与 _BOUND 就绪后才按现有初始化时点封存。两方向首入必须覆盖。

只有显式原 owner 走严格消费。使用原 getter 的实际返回核以下具名合同，不把当前四字符串快照冒充历史：

1. 原 owner/实际 class type-call 结果/partial self 为同一 profile；原 Cdict 为同一原字典，当前四字段仍为四个原实际 Path。四 inputs 是 constructor 当时的实际参数，不从当前字段重建。
2. construct/constructor/post-init body/Context set-reset 和 inactive 效果完整；pending0、first/final/mapped/capture/constructor 错及 cleanup_errors 均无，未重入。`helper_outer_return_observed=False` 保持；它不妨碍核实际 class 返回，不能改成 True。
3. 四字段各12个、共48个原调用，顺序/字段/kind/实际参数/原 callee/attempt/returned/result/异常均按 producer 实现核对；四个原 setattr 的普通返回与独立 effect 分列。普通 copy/unpickle/同 identity 的其他实例不能借用该 owner。
4. 首次消费保留原 facts 内各 identity 锚和独立 immutable 外层 tuple；后继 fresh getter 重新核同一 owner、原 class result/Cdict/四 Path及原调用记录，不修损坏镜像、不重封历史。当前 Path 合法 lazy cache 变化按既有原 Path 合同处理，不预热 cache 或把新的等值 Path 替换原对象。

在原 roots factory 前完成该门；保持 database.parent、两层去重/排序、actual roots tuple和 RootOwners 原构造/slot return-effect。base、check_profile_paths、scope/origin 交接、root/目录消费者持续核同 profile owner；不能只在新 helper 首次检查一次。profile 的构造线程只作历史，操作仍以原 control._OWNER 当前身份核对，允许显式传入已完成构造的跨线程 profile/owner。

这里只在指定原 producer、函数目录和记录合同范围内消费事实。任意 Python 反射、全 globals/CPython/C、完整物理资源与 marker/metering 闭包未因此认证；birth/path/physical/marker/publication 等原旗标保持 False。原公开 profile/copy/pickle/四字符串投影不变。

## D001修订：原构造 image 到九槽 Path 判据的固定接线

DESIGN01仅写“按既有原Path合同”，没有确定历史基线、两种schema转换、合法lazy范围及bootstrap来源，Root01漏审；原稿和独审红保留。下面是实施前必须满足的具体合同，不能用“相同Path对象”替代。

仍只改scope/enrollment两文件。scope在静态源码中限定复用现有resources `_path_snapshot`、`_path_snapshot_values`、`_same_path_snapshot` 三个pure helper的同AST正文；采用有前缀的私有名称和该scope的固定失败callee。对应原PurePath九个C slot descriptor及ntpath.join/splitroot/splitdrive在无参数factory内一次捕获，函数/code/default/kwdefault/closure/cell与descriptor身份纳入原bootstrap目录；不import resources模块，不在运行时读源/exec AST，不在factory提前调尚未就绪的control helpers。候选构建和源码两审须核变名之外三helper AST与给定parent原件相同，新增adapter另核。限定副本避免现有scope/registration/resources的首入循环；不改既有resources合同或接线。

历史来源固定为原owner getter实际返回的最后一条调用，即 checkpoints 的 setattr：此cell须kind/field/attempted/returned/result=None/effect=True，48条及四字段门全部成立。只从这个原cell的 `after` tuple中，以 `is` 对每个constructor实际final Path选唯一image；不从首次消费的Path重新造历史，不挑最早或当前同值image。保存四个完整原image身份，以及以下schema适配结果到原独立raw records；同一image缺失、重复、type或slot顺序不符均拒绝。

producer image是 `(原Path, 原type, slots)`，每slot为 `(name, tag, actual, items)`；tag仅exact str的present/absent/unknown。按PurePath给定原九槽顺序转换：present→True；absent→False且actual/items必须None；unknown直接拒绝并保留原unknown观察，不伪称缺失或读取成功。适配后的tuple才能传给三pure helper：

`('_raw_paths','_drv','_root','_tail_cached','_str','_str_normcase_cached','_parts_normcase_cached','_lines_cached','_hash')`。

历史和fresh两份snapshot分别先过原values判据：raw list与元素exact类型、词法解析、cached值一致、drv/root/tail三槽全有或全无；不调用Path getter或预热cache。随后原same判据比较历史与fresh：所有历史present槽保持present且同一原值，list长度/各原字符串identity保持。历史absent仅允许其余八个cache按固定tuple新增，必须已经通过原values判据；_raw_paths绝不允许新建/替换/原地改列表。允许的tuple由静态闭包固定，不让caller选ignore列表。历史baseline不因合法cache新增而推进或重封。

首次原_roots前做同一原Cdict/四Path及上述历史/fresh检查；后续base/check_profile_paths/scope-root/目录交接继续用同一四个历史image验证当前原对象，不换成新的等值Path。该检查只证明具名原构造image到当前Path的合同，仍不宣称全stdlib/globals/CPython/C、物理/marker认证。

新检查额外覆盖：保持Path identity但改_raw_paths原list/元素、替换等值raw list、改已present缓存、unknown/顺序错/重复/缺历史image均拒绝；原history absent的八种合法cache新增及完整parsed三槽正常、缓存不一致或partial parsed拒绝，合法新增不改历史锚。两方向首入不得import或提前访问resources。copy/pickle普通语义仍单列。

## 失败与仍未完成的释放

caller 在实际调用之前已持 owner/root lease，因此 helper 失败、group 未返回、scope install 部分成功时都保留原记录。实际 scope 可能已分配或 Context 已 set，但 claim helper 尚未返回，清理必须从同一 origin 的原 raw 记录找到实际 partial scope/Token，不能只看局部变量是否接到返回。没有实际 Token 时不调用 reset；已 attempted 的 Token 不重复，foreign/copied Context 错误不跨 Context 重试。

strict 主体在原 EX 实际 exit、原 consume/packet retirement完成或尝试之后，唯一收尾自己安装的 scope Context，保原 first/tb 与后继 cleanup 错分列。仅 reset 不算结束 custody。外部 caller 继续持 root lease；本段不调用 `_finish_operation`、不删表/清异常/tb、不开启自动丢 root。完整 pending owner 的持有/容量拒绝/恢复/最终释放仍属 Task7，在其完成前候选保持不可激活，不能把 caller 的暂存视为已实现 durable pending。

Store/joint没有在指定源中找到外部直接调用者，故本段只给真实现有私有主体传递能力与具名模型，不新增 HTTP 写接口、不声称应用已调它们。Task6 才把配置 owner/操作 lease 从真实长期/请求持有点逐层显式传入。

## 检查、交付与后续

两设计审后才生成十二完整 parent/candidate/inverse/raw diff；两源码审后准备新具名检查，最终六脚本和实际输入双审、新claim单次执行。先检查代码实际保留的 legacy AST和原三个主体，覆盖 strict/omitted/None/缺对/不同实例同identity/重复/构造失败/partial scope/错误原件/Context reset/跨线程。原 profile→helper→scope/root 链选原 Python 源；三个 outer owned body 的控制接线模型可显式合成 EX/source/内容/Store consumer，必须与原 scope/root 模型分列，不冒充真实 EX/SQL/native运行。只选受影响导入/copy/Path/Context回归，不机械重跑旧32/48/69/111/94/r919。

限额仍为 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest child，本批max1。保原 Popen 对象/finally/kill-wait/raw-first，数量替换须定向，不改数字子串。保留旧红和独审 checker 范围误判；禁止 profiling/frames/locals/停点或定点计时诊断。

正式/c973不安装，本段apply0/历史累计11、产品pass0。Task6–7、完整Path/资源/三marker预算/容量、完成图/final/正常父退出/恢复、原111与94/r919/Store/source_closed/live witness/写权、H02–H06/永久分享及完整Goal仍未完。正常库、服务/dist、既有ACL/settings、平台和Git保持，r920/r964耗不重放，r967待原问不执行重问，P06历史0/7不再问。回滚为保留未安装候选，不覆盖正式树。
