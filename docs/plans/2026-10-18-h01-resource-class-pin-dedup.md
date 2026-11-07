# Resource 类函数约束去重：保持每次完整认证

## 事实与边界

r1066 完整 F001 在20秒限额超时，监督器kill并wait确认退出，stdout/stderr空；具体执行阶段和根因未知，不重放未修改用例，不扩大期限，不新增诊断。r1067有限静态调查没有证明普通entry递归、同锁死锁或确定产品缺陷。

已确认资源初始化分别对 PurePath、Path、WindowsPath 建 class pin。当前解释器stdlib只读元数据中，三者按现有class_pin规则产生45/91/91共227条Python function pin，只有91个不同函数对象，重复136条。这不是F001实际调用次数、候选完整PINS数量或耗时测量。资源每次_authenticate都重新验证这些约束；可先减少同一次认证中完全相同约束的重复项，不能把它称为超时根因修复。

## 唯一变更

以r1065 SOURCE01完整158文件为父，只改 `reno/personal_authority_guard_resources.py` 一份，其余157字节继承。保全部业务/native/caller/cleanup/clock/getter/Scope/Token/账本逻辑及调用点；不改custody、pending、通用_class_pin/_check_class/_check_function，也不改变实际类元数据布局检查。

新增私有纯元数据函数 `_deduplicate_class_function_pins(pins)`，仅用于本模块原初始化取得的完整class pins：

1. 对每个class pin，原cls、mro、完整rows三字段保持原对象身份和原顺序。每个原class仍由原_check_class在每次_authenticate按原顺序独立检查。
2. 原class pin第四字段中的function pin按原顺序处理。只有七字段完整预期约束完全相同时才合并：function/code/defaults/kwdefaults/closure原对象身份，以及每条keyword entry的key/value身份、每条closure cell/value身份和原顺序。使用只含builtin int/tuple的本次局部签名，不调用目标对象的hash/eq。不能只按function对象或code对象去重；相同function但不同预期必须都保留。
3. 相同约束保留第一次出现的位置；不同约束和不同函数即使代码相同也保留。原pin格式、类数、每个class的layout rows及全部不同函数约束的有序首次出现集合保持。已保留pin强根持有所有身份来源；局部seen仅活在构造期间，不能形成成功缓存、弱历史或按id复用的跨调用目录。
4. 只去重资源_PINS[0]内部的function constraints。资源_PINS[1]完整独立function pins、_PINS[2]context pin、_PINS[3]bindings及scope/registration认证都原样保持。原_class_pin仍对每个class真实调用，随后仅正规化tuple数据；_PINS仍只在原末次赋值安装，不新增部分ready或失败fallback。
5. helper自身加入原functions目录及原自动module bindings，防止它变成未认证的产品动态接点。helper不执行任何被pin函数，不import产品、不创建资源或发生业务I/O。

这项改变只减去同一次认证中重复的相同静态预期。它不合并相邻业务阶段，不复用上次通过结果，不减少任何实际认证调用或fresh观察；每一次认证仍核全部独立约束。并发任意内存篡改不构成新的原子性保证；不得据此扩大现有保护声明。若独审发现去重跨越原要求的独立观察边界，须保红并停止该方案，不能为了速度删认证。

## 验证与固定准入

设计双审 → 完整158候选/原父本/逆向/全文diff与157静态compile → 双源码审 → 脚本/实际准备双审 → 新claim。同一只读独立代理，不新增、不重复索权。

新批G001为完整模块导入后的纯元数据行为检查：从实际_PINS[0]原cls逐项调用原_class_pin重建完整class约束，核class/layout/七字段预期有序集合保持，136条stdlib重复约束确实可合并；核资源正常认证通过。用不修改产品/stdlib对象的本地pin输入核“同function但不同预期code/defaults/keywords/cells保留”“不同function同code保留”“重复完整约束保首次位置”，以及错误预期code仍由原_check_class拒绝。只比较数据，不执行目标函数、无native、无profiling/计时或AST摘取产品运行。

然后只复检r1066失败的同一完整F001。保原fixture、真实_ExclusiveScope入退、十八资源stage、file/group/guard marker、前后目录finally、原ledger与close及总计一次scope reset全部步骤和断言；不能缩短、改变capture_count=2、换替身或延长限额。只因新candidate路径和版本绑定适配脚本，原F001正文须全文核同；两Nonecapture及group normal/lineage False保留。

G001不是native通过或根因证明，F001结果如实保留。若仍超时，保新失败，不重复未改版本；静态减少重复约束并不证明满足20秒或完整产品目标。

保case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children及native20–25–10（F001仍child20，总25，cleanup最多10且夹紧）。正常库、服务/dist、ACL/settings、真实平台和Git保持；r920/r964单次诊断耗不重放，r967待原问题不执行重问，P06历史0/7。canonical/c973本段apply0，历史11，产品pass0，完整Goal active；Task9A其余矩阵、Task9B全precall/物理费用债务/terminal/容量durable恢复/原111/94/r919/真实Store写权均未完成。
