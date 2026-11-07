# Task8 完整原模块启动与固定目录验证 Implementation Plan

> For Codex: 使用 executing-plans 执行；复用既有同一名只读复审代理，不新增代理。候选源码、脚本、实际准备和结果继续双审。普通开发已有授权，不新增审批。

**Goal:** 让原 control/scope/registration/resources/enrollment 完整模块在支持的首入顺序下正确完成初始化，并用真实配置/profile producer、原 Budget 与 custody 验证固定目录；不再用 AST 摘取或替身绕开这些模块的启动链。

**Architecture:** 基于 r1052 SOURCE03 的37份完整候选，以及当前 c973 的静态导入前驱生成全新只读代码副本。只修 scope 的独立通道初始化判断。Python 子进程使用原包名和原 import，代码目录与可变 QA state 为不相含的兄弟路径。正常 DLL 初始化、原第三方库和包内词典读取按实际边界记录。

**Tech Stack:** Python 3.12、现有完整 Python 包、stdlib subprocess、Windows DLL ABI 初始化、原第三方依赖、PowerShell7。

## 已确认事实

- r1053 的129模型只证明具名历史校验；完整导入和原 late catalog 没有执行。
- `scope._make_origin_core` 的同一 `sync` 字典保存 failure 通道的 `attempted/fixed`，也保存 resources 的 `directory_return`。原 `bind_failure_sync` 的 `if sync` 会把“只已绑定目录通道”误判成 failure 通道已消费。该结论是静态路径推导，尚无运行结果。
- resources 底部先绑定目录通道；enrollment 底部才初始化 origin/failure 通道。因此 resources→enrollment 是本轮必须覆盖的真实导入顺序，不以强制 enrollment-first 掩盖缺口。
- 完整导入有真实依赖初始化：sync_media / namespace3_io / census_native 加载 Windows 系统 DLL 并设置 ABI；dict 的 OpenCC 创建及 atomize 的 `TAX=taxonomy()` 读取包资源。不得描述成“无IO/无native”。它们不等同于执行资源业务 API、EX、SQL 或旧诊断。
- config 的 QA 合同要求代码 ROOT 与 RENO_QA_ROOT 互不包含。测试代码目录与 state 根必须是兄弟路径，不能把代码放到 RENO_QA_ROOT 里面。

## 采用方案与替代方案

采用独立通道的一次绑定判断：允许 sync 为空或仅有 exact str 键 `directory_return`；其他键，包括 attempted/fixed，继续按原 ORIGIN_CONSUMED 拒绝。先写 attempted=True 消费，再在已存在目录通道时调用原无 provider 的 binder 重核既有模块/函数身份和 pins，最后执行原 Bound/remember/fixed 构造。

只支持 enrollment-first 会把已有循环导入顺序缺口留给调用者；清空 sync 或移除目录记录会丢失原归还通道；失败后清 attempted 重试会违反单次合同。这些方案不采用。

本窄修不新增全局表/slots/分区/anchors，不改变原 failure 实际数据及目录 publisher，不提供 caller pins，不声称认证外部任意改写整个私有 closure 的情形。所有现有键先 exact str，再做字面比较，不运行未知 key 的比较方法。原 binder 针对任意私有 record/pin 对象替换的完整结构认证不借本轮升级为完成。

## Task 1：有限调查与完整来源

Create `data/t16-h01-runtime-integration-r1054/` 调查、来源与设计双审。

1. 独立静态核完整首入 imports，区分顶层和 deferred imports，列明 c973 前驱与包资源，保路径、完整字节和 SHA；不把句法闭包称为所有动态行为。
2. Root 保当前三 marker 实际读取位置及 r993 原计费要求。marker hook 尚未实现；这不是本轮“启动通过”可替代的计费证明。
3. 双审本方案及发现的通道顺序缺口，源码执行仍为0。

## Task 2：完整候选

Modify 未安装候选 `reno/personal_authority_guard_scope.py` 内 `bind_failure_sync`；其余36份逐字节继承。

1. 对 sync 当前 key 先 exact str 检查，再只允许目录通道键；保原错误合同 ORIGIN_CONSUMED。
2. 消费 attempted 后才重核已有目录绑定；失败也耗，不清理、不重试、不覆盖原目录记录。
3. 原 Bound/remember/fixed 和 finally 原样保持。目录 binder 身份/函数 pin 重核仍用原无参函数。
4. 保存37完整父本、候选、精确diff、37静态compile；原其他定义/slots、32 partitions/43 anchors不变。双源码审0后才准备运行。

## Task 3：实际完整模块检查

Create `data/t16-h01-runtime-bootstrap-qa-r1056/`，候选交付预计 r1055。

代码目录为 `.../source-full-01`，各 case state 为 `.../runtime/<case>`；二者互不包含。完整37候选覆盖 c973 的同路径来源，所需额外原模块/包资源逐份从已核清单拷贝。禁止 `.py` AST 替换、假模块、修改 sys.modules 中原业务模块或 monkeypatch 原实现。原 stdlib 和第三方包照常使用。

每项新 child 用 `.venv/Scripts/python.exe -I -B -X utf8`，仅将本批完整代码根加入 sys.path，并核原 reno 模块 __file__ 均在该根。清理继承的 RENO/SDK 凭据环境，设置明确 RENO_QA_ROOT / RENO_CONFIG_FILE / RENO_DATA_DIR，配置使用本批非敏感固定值。子进程不打印环境或凭据。

至少覆盖：

1. enrollment→resources，核实际 Bound、scope/origin/registration/resources pins 和原 authenticate。
2. control→enrollment→resources。
3. scope→enrollment→resources。
4. resources→enrollment，确认目录通道保原、failure 通道首绑成功。
5. 原失败通道第二次绑定明确 ORIGIN_CONSUMED，目录通道原 idempotent 绑定保持。
6. 实际 storage_admin 配置 profile producer 与实际 owner facts、四原 Path/constructor 历史，原来源门能读取；不初始化设施或打开数据库。
7. 实际 enrollment Budget + 原 control/custody 的 claim/request/charge/history，覆盖原零项、actual0/31、多个记录；没有真实 scope 的这个 ledger case 不冒认 marker/full scope。
8. 部分 failure 绑定的消费合同用函数原体和明确数据准备边界验证；如需适配器必须单独标注，不能把它计作完整启动路径。测试目录和脚本双审时固定最终具名ID。

正常 DLL/第三方初始化和只读包资源、配置读是本轮真实边界。不得调用 CreateFile/NtCreateFile 之类资源业务入口、锁获取/EX、SQLite connect/SQL、网络客户端发送、服务启动、设施setup、真实库或旧 K001。没有 profiling、frames/locals、停点或额外异常链诊断。

## Task 4：准入、结果与交付

源码、脚本、实际输入双审后才消费新 claim。沿用 case30 / parent360 / matrix1680 / cleanup10 / control1MiB / 并发1 / 最多2pytest children，native20–25–10保持。完整 source import 自身也受 case 子进程时间上限，不因加载慢提高限额。

所有 CASE/stdout/stderr/实际 child 返回与 wait/timeout/kill 保存。完整导入失败若由原候选触发，应记为候选或组合失败，不能改记夹具通过。只修并复检原失败ID，旧绿不重跑。双审实际记录后回写交付与主方案，保旧红和 before 字节。

回滚为不安装候选。正常库、服务/dist、ACL/settings、真实平台和Git保持。r920/r964已耗不重放，r967待原问题不执行/重问，P06历史0/7无备份。真实 marker/全部读计费、guard消费者、最终Call两阶段终止、容量/durable恢复、完整物理链及Store写权/H02–H06/永久分享与完整Goal仍未完成。
