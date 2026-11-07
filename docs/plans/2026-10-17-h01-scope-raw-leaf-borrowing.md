# Scope 原记录叶函数复用父 Call

2026-10-17，r1019 DESIGN01。依赖已交付 r1018 SOURCE03；先设计与静态复核，再准备未安装候选。不改正式树或 c973，不提高原限额。

## 事实与目标

r1018 新组合实际40pass、2error、行缺0，I003/I004均在原30秒上限超时、kill返回后wait、缺CASE；I009当前版本pass，29L全pass。不能据新版本结果追认旧超时停点或性能根因。

本轮有限静态调查只读父组合十一源及指定证据，22份完整来源974502bytes已核同。scope._make_raw_records有new_record及五个叶函数entry/raw_get/raw_set/check_record/record_is。五leaf只在custody上读get，不调用owner/put/register/select/_select_owner/hold_scope；raw_set和check_record会更新既有原记录或污染标记，不能称只读。new_record明确owner/put，保持独占。十一源中_raw_get/_raw_set/_check_record/_record_is分别有440/188/16/43个句法调用点；raw_get还嵌套entry。数字不是实际执行次数或耗时。

目标是按已完成Path leaf规则减少可确认的嵌套临时Call，同时保原每次认证、原记录身份及来源语义。I003/I004是否改善，只能由后续新候选原测试体在原上限内的一次结果说明，当前仍unknown。

## 方案与取舍

采用五个已列名leaf的显式父借用。复用现有custody._make_entry_gates(_reuse_leaf_parent=True)，不增加runtime参数或改变原False路径。保留全部独占包装可维持现状，但不会减少这处可确认的临时ticket；把整个scope或所有helper统一共享parent会改变select/put的写分区与传播语义，超出本段范围。只在无ownership选择的五个叶函数实施。

预计十一完整组合一改十继承，仅修改personal_authority_guard_scope.py。custody和resources全文继承r1018，父源、逆向、按原始bytes的完整差异都保留；new_record的参数、完整AST和默认gate不变，其余原函数除固定pins目录外不改。

scope固定导出_CUSTODY_RAW_ENTER/_CUSTODY_RAW_LEAVE，factory在原位置创建此专用对，_make_raw_records闭包捕获同一原callee。像r1018一样由enter原closure中具名check取得原内部check，捕获enter/leave/check的完整七字段pins。捕获对象仅为静态函数与原静态pins，不捕获operation/Call/异常/原记录。FunctionType/sys.exception等固定依赖按现有来源取得，原模块镜像与初始化函数目录同步。

每个leaf在执行专用enter前，以闭包固定的原GET/type/len/any/dict.get内联核三函数的function/code/default/kwdefault及内容/closure/cell及内容，含空cell拒绝，并核两个模块导出仍指原callee。通过后调用闭包原enter，不从可替换导出重新取函数。认证发生在任何parent.observe、raw字典/污染标记/descriptor写入前。

有parent：仍走现有专用gate完整implementation check、原Call/state/membership/closed、额外lease拒绝、逐subject observe，返回原parent/own=False。保parent.preferred/selected和跨owner父pin，不分配child、不关闭父。无parent：认证后仍走原独占enter，最终归还自身ticket；不能凭raw记录镜像补来源。new_record及所有其他helper保原独占。

leaf最外finally再次以内联原pins核验，才调用闭包原leave。退出认证失败有实际活动primary时继续传播同一primary；无primary则报告认证错误。未知借用不清理、不补回执，own=False由父完整finally负责。原fresh/auth/clock/预算检查逐个保持，不加缓存、采样或认证豁免；_finish_operation继续硬拒。

## 静态与模型验证

实施前先完成Root与同一独立代理的设计复核，候选后完成两源码审及六脚本/实际准备双审，随后新claim。先以AST核五leaf原核心try body与参数保持、new_record完整AST保持、十继承原bytes一致和精确diff；核scope两组固定function pins及origin镜像，原五raw返回tuple不变。

新具名普通模型覆盖：五leaf实际原函数、无parent独占/同parent借用、parent A读写B及多个subject保持preferred/selected、原记录/descriptor/污染标记、unknown record_is与原错误、closed或损坏parent、new_record仍独占、原gate进入前与退出前code/default/kwdefault/closure/cell/空cell/导出或内部check篡改拒绝，以及活动primary保留。退出认证的body触发用明确列出的合成fail/lookup provider，不称真实资源故障链；不得替换原leaf AST主体。

回归选择实际scope构造/Context/foreign/legacy中受影响路径，I003/I004保r1018原测试体及30秒上限，作为新组合仅各一次；不机械重跑旧40、原111/94/r919或所有历史批次。若仍超时，完整保留缺CASE、实际kill/wait及工具记录，不同版重试、不加上限、不做停点、profiling、frames/locals或定点计时诊断。执行保持case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest children；本批max1，采用已审原Popen所有权/finally与raw-first保全。

## 完成边界与后继

本设计未产生候选compile/import/模型/诊断/产品执行，不预报通过或性能效果。后续检查即使全绿，也只证显式合成IO/native/EX/clock环境的具名范围；不代表真实三个enrollment owned body/profile或route owner、严格lifecycle/GuardDTO、完整Path/目录/三marker唯一预算/容量、最后业务释放/pending恢复/final、Store/source_closed/live witness/写权或全Goal完成。

正常库、服务/dist、既有ACL/settings、真实平台与Git保持。canonical/c973本段apply0、历史正式累计11、产品pass0；r920/r964已耗不重复，r967待原问不执行不重问，P06历史0/7不再问。设计稿或候选发现问题时保旧稿红证据，以新版本修订，不覆写已冻结输入。
