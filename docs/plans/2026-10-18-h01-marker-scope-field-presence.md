# Task9A scope字段存在性与合法哨兵值修订

继续既有executing-plans流程，仅复用同一只读代理。来源为r1064新批9pass/2fail/缺0，28.234秒，S001/S006首次positive query在marker_positive_preflight字段检查拒绝，尚未到S006 request32；其余9绿不重跑。无新诊断或运行探针。

## 静态可确认的缺陷

原Scope的baseline正常可为原`_MISSING`，claim和install都通过原_raw_set保留该哨兵。marker_positive_preflight却以`dict.get(values,name,marker_missing) is marker_missing`判定字段缺失，而marker_missing就是同一个原_MISSING。因此一个存在且值合法的baseline也会被拒绝。这是源码可证明的正常路径冲突；不声称已经探针观测失败进程的确切字段。

## 唯一产品变更

以r1064 SOURCE02的158完整文件为父，新增158完整候选，只改`reno/personal_authority_guard_scope.py`一份，其余157字节继承；四份已修启动顺序文件和原三caller业务体保持。

- 在row[2]及原marker_scope_descriptors首次按字段名查字典前，检查全部key是exact str。保持原row类型/长度、同own partition/subject/locator、类/descriptor身份、raw[3]无污染、全字段数量和后续control/budget/group/claim来源检查。
- 将缺失判定改为原exact dict membership（`dict.__contains__`），不再以合法_MISSING值代替缺失。字段存在后仍核原descriptor的当前值与原raw值`is`一致；不补值，不改镜像，不放宽foreign-before-keep，不调用额外getter/clock/Token，也不改变原后续by_scope/live认证。
- 通用raw_get/raw_set/check_record、_Scope字段布局、原baseline/Token/current/request/charge逻辑及Startup observer都保持。

## 新检查批

设计双审→完整候选/diff/逆向及157静态compile→双源码审→脚本/实际准备双审→新claim。只复检两个旧失败ID S001/S006，保r1064原完整模块及前后current/C Token/TokenRoot/transition/grant/ledger/Call loans快照断言。

新增两项具名负例N001/N002，共4项，使用同一原profile/lease/budget/group/scope构造路径：

1. N001在新子进程内，从原held Call获取自己的Scope raw row，临时删除baseline字段但不改facade；positive query必须按MARKER_SCOPE_RECORD_CHANGED拒绝，且Call/loans/counter不变；finally恢复原值，然后原scope reset正常完成。
2. N002在新子进程内以未知key替换baseline key，保持原字典长度。该key的hash碰撞一个稍后会查找的原字段，插入时允许原dict正常比较；插入完成后清零计数并开启一个只会计数/抛错的__eq__。query必须在执行该未知比较前按同确切code拒绝；计数0、Call/loans/counter保持。finally先关闭该fixture的抛错，再删除同key/恢复baseline，原scope reset。key仅作raw值故障夹具，不替换产品函数，不增加诊断/异常捕获接点。

两负例不得调用marker IO或资源构造/start，不用合成产品模块/AST摘取。原scope reset不授业务退休/root释放，pending原根保持。若失败保红，仅后续新claim复检失败ID，旧9绿和旧21/12/3绿不重跑。

## 边界

完整原模块/DLL/ABI/第三方/词典/隔离配置正常初始化读取允许，marker IO/业务native/资源start/EX/SQL/Store均0。scope/计费positive通过也不完成真实已发行lock/首marker、group/guard原运行和清理矩阵；Task9B及完整Goal active未完。

保原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children及native20–25–10。禁profiling、frames/locals、停点、定点计时、新异常链诊断。r920/r964单次耗不重放；r967待原问题不执行不重问，P06历史0/7。正常库、服务/dist、ACL/settings、平台和Git保持。canonical/c973本段apply0，历史11，产品pass0，候选未安装。
