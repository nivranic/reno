# Task9A marker启动绑定与登记封存顺序修订

沿既有 executing-plans 流程继续，仅复用 `/root/r924_independent_review`。来源是r1063完整组合八项启动失败，不新增异常链诊断或运行时探针。

## 已观察结果与静态解释

r1063 SCRIPTS02唯一批0pass/8fail/缺0，10.297秒；均在registration._authenticate→control._check_class→control._check_function的closure cell值校验处拒绝，尚未到各自caller/scope目标断言。8wait，无timeout/kill，两actual审0仅证记录一致。r1062的21项AST绿及旧12/3绿保持，不重跑。

源码可确定：scope/control循环bootstrap会调用registration._initialize_owned，后者为_FileLock.verify捕获class/function/closure快照；resources稍后才调用lifecycle._bind_marker_consumers_original，合法更新原marker工厂共享dispatcher_pin等cell。file_verify直接捕获dispatcher_pin，因此其早期class快照会失效。这是静态可达的不一致；未用新探针读取失败进程的原cell，不能声称已观测到确切失败cell身份。

## 选定修订：第一次完整封存放在最终绑定之后

不修改通用_check_function/_check_class，不忽略closure字段，不刷新已有_PINS，不换caller，不改已完成的原读费/清理逻辑。

完整候选以r1063的158文件为父，仅计划修改四份，其余154逐字节继承：

1. `reno/storage_lifecycle.py`：模块初始化的`_MARKER_CONSUMERS_READY`为False；原bind完成全部module/functions/checker_pin/dispatcher_pin赋值后才置True。仅是启动完成信号，不证明scope、资源、写权或业务就绪。重复bind原拒绝保持；任何中途失败不发True。
2. `reno/personal_authority_guard_registration.py`：原_PINS已存在时仍先认证并返回，绝不重封。首次初始化遇启动信号False仅延迟class/function快照，_PINS保持None；信号不是exact True/False则拒绝。_authenticate的NOT_READY拒绝保持，不因延迟而放行。最终首次封存额外记录原lifecycle模块dictionary中的启动信号、原bind函数和原marker函数tuple绑定，后续变化拒绝。原全部类/函数/字典/stack检查原样保留。
3. `reno/personal_authority_guard_resources.py`：原trace bind→原marker bind完成后，立即调用原registration._initialize_owned，确认_PINS非None才继续自身第一次封存。将该初始化函数/实际模块绑定加入既有函数和dictionary快照。自身已有_PINS仍走原认证分支，不再次bind。
4. `reno/personal_authority_enrollment.py`：在原_initialize_origin_owned之后，模块末尾显式导入原resources模块，使完整固定入口在任何profile/budget/operation/scope业务创建前完成上述启动。不从运行中的marker或registration._authenticate隐式导入模块，不在原budget计费区间增加初始化读取。

私有registration单独导入可处于尚未封存阶段，认证仍拒绝NOT_READY；完整enrollment入口会完成初始化。须新增具名“registration先导入再enrollment”顺序检查，不能把模块可导入冒认私有未完成组件可独立授权资源。

## 检查与交付

1. Root与同一独立代理先复核本设计及静态来源。生成158完整父本/候选、全文diff与精确manifest，静态compile157Python。四改154继承；原37候选中三份新改/34保持，registration作为原闭包文件第四改。
2. 双源码审。确认所有已有class/function完整校验仍在，None阶段拒绝、False不发布、True先于首次registration快照、资源启动及SQL/EX不执行；正常入口导入在业务预算前完成。
3. 新脚本保r1063八项全部原目标/oracle，不删认证或断言，只改受审组合与runtime路径；因旧八项全部失败，可新claim定向复检这八项。新增B003仅覆盖registration先导入再enrollment。额外新增B004在独立进程只导入registration，验证_PINS为None及认证按NOT_READY拒绝，再导入enrollment，验证最终首次封存/认证成功、重复初始化_PINS identity保持。B004不操纵启动信号，不授任何资源许可。
4. 脚本及实际准备双审后，唯一新claim运行十项，保持原限额。若失败，保原红，仅复检失败ID；本轮r1063旧8红不覆盖。双实际记录审之后更新主方案和AGENTS。

允许完整原模块、DLL/ABI/第三方/词典/隔离配置正常初始化读取；无AST摘取或产品模块替身，无资源start、marker IO、EX、SQL、Store或业务native。原scope/current/Token/grant/ledger查询保持断言继续，pending原根不冒退休释放。

## 保留边界

canonical/c973本段apply0，历史11，产品pass0，候选未安装；完整Task9A/Task9B及主Goal仍未完。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children及native20–25–10保持。禁profiling、frames/locals、停点、定点计时、新异常链诊断。r920/r964单次耗不重放；r967待原问题不执行不重问，P06历史0/7。正常库、服务/dist、ACL/settings、平台和Git保持。
