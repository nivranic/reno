# directory carrier 归还：bootstrap 接线修订

本修订替代首稿第4节的运行时 resolver 接线，其余归还准入保持。调查确认 scope 的 origin 初始化先于 resources；在此处冻结 safe._DIRECTORY_TRACE_MODULE 的初始 None 会使后续合法加载失败。不能用放松模块身份核对来绕过。

采用无参、仅初始化一次的固定链接：origin 闭包捕获原 safe 模块字典、原 sys.modules、固定 resources 模块名；resources 原 `_initialize_owned` 在自身全部导出已定义后调用 scope 的 `bind_directory_return`。后者只从上述原目录取得实际同名模块，核模块名/原字典、固定归还函数与认证函数的来源，保存模块、原函数及 code/default/closure/cell pins到既有 origin `sync` 字典。重复调用只核同，不替换。不是调用方传入 provider，也不在 scope 顶部 import resources。

原 scope-return 在实际 scope 删除和 effect 成功以后，从这个原链接调用原 resources 归还函数；调用前核原 sys.modules、原模块字典中的函数身份及固定 pins，并由原 resources `_authenticate` 核完整真实目录。链接未初始化拒绝，不能跳过归还；资源目录替换拒绝。resources 同步把 scope 的原只读 facts helper、binder 和自身新 gate 纳入原函数/绑定目录。

源码生成时将原 scope-return 的来源校验全文提取为只读 helper：原 raw/recover 两个会新建 Call 的入口换成原 leaf读取和同原 candidates 的五入口枚举；原其余 constructor/consume/Token/grant合同保留。after 模式核原 scope-return 16槽和原已不存在的 holder。scope-return 函数仍自行内联删除 scope loan，helper不删除。resources consumer 证明段提取为只读 leaf helper，原 consumer_finished 继续在原调用点写 actual observation；归还门在 wrapper 结束以后用同一 helper复核，不重跑物理动作。

新链接是未安装候选内部的来源接线；不建立平台权限、不执行诊断、不修改真实库或服务。方案仍须双审及后续源码/脚本/实际准备复核。
