# Profile 构造前关键词默认值的完整预期核验

## 问题与证据范围

r1069 的有限静态核对确认 `storage_profile_origin._bind.function_pin` 只保存 `__kwdefaults__` 字典身份；`same_function` 未冻结或比较字典条目。原 `prefix_functions` 包含 `*locked_targets`，其中 `returned(..., *, setter=None)`、`failed(..., *, path_inner=False)` 都有可写的非空关键词默认值字典。同一字典内原地换值不会改变旧检查所比较的身份，因此漏检是源码可证明的。

`lifecycle` 的多处原 `_PROFILE_ORIGIN_RETURN(journal, cell, value)` 调用省略 setter，确实使用 returned 的默认值。`_profile_note_failure` 虽有自己的默认值，但对 FAILED 显式转发 `path_inner`；不能把这当作 failed 默认值在该链被省略的证明。route 通用关键词检查分支尚无固定非空 kwdefaults 的可达证据，本版不改它。

r1068 的 G001 已通过，F001 仍在20秒超时，具体阶段未知。本修订不解释或解决该超时，不重跑F001，不借新测试缩短或定位原F001。r920/r964单次诊断已耗，不重放；r967待原问题，不执行或重问。

## 唯一产品变更

父本是 r1068 SOURCE01 全部158文件。本版仅改 `reno/storage_profile_origin.py`，另外157文件逐字节继承，包含已完成的资源约束去重。

1. `function_pin` 保存七项：code、globals、defaults、关键词字典、关键词条目快照、closure、cell values。条目由原 `dict.items` 的C描述符取得，不调用可写 `.items()`；末项仍为 cell values，保 `init_pin[-1]` 现有取值及原 init 闭包核验。
2. `same_function` 继续逐项核 code/globals/defaults/keywords/closure 的 `is`，继续核 cell 数量和值身份。新增关键词字典内容门：先 exact dict，再条目数和全部当前 keys 的 exact str，最后用原字典操作按原键比较 value 身份。遇到同长非str或str子类碰撞键，须在 keyed lookup 前拒绝，不调用目标 `__hash__`/`__eq__`。
3. 正常的 None/False 默认值必须按身份正常通过；换值、删键、加键、同长碰撞键、替换整个字典仍拒绝。不使用整个条目 tuple 的 `is`，避免每次新快照导致正常路径误拒。
4. 不改 constructor_gate 的调用位置、次数或其余门；不改 prefix_functions 目录、构造顺序、partial self、原48记录、RLock、ContextVar/Token、first、一次claim和原finally清理。原profile布局/状态协议、路径规范化、scope、资源、账本、native和产品route均保持。
5. 不扩大为完整globals认证或CPython/C来源认证；本版只补已固定函数的关键词默认预期内容。

## 顺序实施与复核

同一只读独立代理，不新增代理或重复索权。设计双审 → 158完整候选/父本/逆向/全文diff、157静态compile → 双源码审 → 完整模块测试脚本双审 → 实际隔离准备双审 → 一个新claim执行 → 实际记录双审。旧稿、红记录和已耗claim不覆盖。

产品仅两处窄替换：function_pin 的七字段快照和 same_function 的检查。删除这两处变更后全文应能精确逆回父本；所有其他函数/类的源片段和AST保持。若需要额外产品改动，先修订设计及复审，不顺带扩范围。

## 九个具名完整模块检查

每项新隔离目录、新进程，按原完整候选包导入，不AST摘取执行、不替换构造/路径/Context原语。只通过两个固定原wrapper的 `function` closure取得实际 returned/failed target，以及原construct的 `constructor_gate` closure及该gate的原 `prefix_pins`；核精确名称、模块、FunctionType和登记对象身份。这是本测试固定的函数元数据来源，不读取frame、locals或任意运行栈，不遍历任意闭包图，不新增异常链观察。

| ID | 动作 | 必须证明 |
|---|---|---|
| P001 | 原owner与原四Path构造，再走原route pack/check | 构造普通返回、48完整原记录、原Token set/reset、完整关键词快照及原callee身份；profile/layout与路由来源保持 |
| N001 | 原returned字典中 setter 原地换值，身份不变 | constructor_gate 拒绝，class constructor/Context set尚未attempt，原first保存 |
| N002 | 原failed字典中 path_inner 原地换值，身份不变 | 同上；仅证固定目录预期内容，不声称当前caller省略使用了该默认值 |
| N003 | returned 的同长关键词字典换入非str碰撞键 | 未知hash/eq在armed验证期间均0，先拒绝后不进入构造 |
| N004 | failed 的同长关键词字典换入str子类碰撞键 | exact str 门先拒绝，未知hash/eq均0 |
| N005 | returned 的原关键词字典删键 | 数量门拒绝 |
| N006 | returned 的原关键词字典加键 | 数量门拒绝 |
| N007 | returned 的整个字典换成同内容新dict | 原字典身份门仍拒绝 |
| N008 | returned 的整个字典换成覆盖len/iter的dict子类 | 在未知len/iter/hash/eq调用前拒绝；使用dict.items不触动态override |

每项负例创建原owner后再变更；armed只覆盖原construct调用。所有修改都在finally先disarm，再用捕获的原字典操作恢复原内容/原函数字典身份，恢复后才能读取普通facts或继续。失败owner的claim保持耗尽，不能重试构造或重置该owner的状态；同owner第二次调用应按原协议拒绝。随后新owner正常构造，证明恢复后静态预期未损坏。不得执行坏默认target来制造失败，也不得改真实库、stdlib或正常产品实例。

每项输出本进程实际导入reno模块的完整路径/字节数/hash，与SOURCE逐一核同；导入subset不冒认158文件全部已import。正常DLL/ABI/第三方与隔离配置初始化可沿现有完整包惯例，业务资源/marker/EX/SQL/Store不执行。G001/F001旧批不重跑，成功不称完整物理链或产品通过。

## 限额与交付

保case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children，本批无pytest child。沿已复核的普通监督器，wait后1MiB输出检查仍分列为非流式，kill后必须wait确认。未确认终止不进入后续项。任何失败保原结果，未修改版本不重跑；不扩大期限、加探针、profiling、停点、定点计时、frames/locals或异常链诊断。

交付CURRENT/NEXT、完整来源/候选/逆向/diff、逐项实际记录、双审与主方案最新状态。修正主方案开头仍停留§17.423的过时摘要，将原叙述保为历史。canonical/c973不安装，本段apply0/历史11，产品pass0。F001、Task9A其余矩阵、Task9B全precall/完整物理费用债务/两阶段terminal/容量durable恢复、原111/94/r919/真实Store写权以及完整Goal仍未完成。正常库/service-dist/ACLsettings/真实平台/Git保持。
