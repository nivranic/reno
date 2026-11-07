# 成功归还 Call 后解除临时引用环

2026-10-17，r1016 DESIGN01。父组合为 r1015 SOURCE-01；候选继续留在 data，尚不安装。

已确认：原 Call membership 保存 issuing Call 的原 loan entry，构成 Call→state→membership→entry→Call；close 成功后仅删除 owner 的 loan 登记，Call 仍保此环及 owner 引用。它可被循环 GC 回收，不是已证永久泄漏，也不是三个目录超时的已证根因。r1015 的 I003/I004/I009 保持 timeout、缺 CASE，既不重跑原版本也不提高限额。

仅修改 personal_authority_guard_custody.py 的成功 close 尾部：保留完整原 membership/issuer 预检及逐 loan 归还循环；整个循环成功后按原顺序发布 raw.closed/slot.closed=True，再用原生 dict.clear 和 list.clear 清空同一 membership/loans 容器，同步 raw.preferred/slot.preferred=None。不调用会在 closed 前重验已归还 lease 的 _set_call_field，不替换容器，不清 parent/selected/cleanup_failure，不动父 Call、root、scope、directory 或 guard tickets。预检或部分归还异常不能进入新增分支，原 first/traceback/失败记录行为保持。

本次不声称闭合整图或最终业务退休：外部 Call 仍可能经 parent 保活父调用，scope、carrier、root 和原异常仍有自己的合法持有。最后 _finish_operation 继续硬拒 TERMINAL_PROTOCOL_REQUIRED；32 原分区、219 原操作函数、原 facade 和原认证旗标保持。

实施分三步：

1. 生成十一完整候选及父源全文、精确差异、源码检查；仅 runtime 一改，十文件字节继承；源码双审后才准备模型。
2. 新模型使用实际 runtime，并保留原 C001–C015、I001/I002/I005/I006/I007/I008/G001/G002 的原测试体。新增 N001–N009 验证成功空容器及原身份、禁用自动循环 GC 时 Call 的弱回收、父子独立借用、多 owner 归还、closed 后拒绝、issuer 篡改预检、原 first 保留、scope/carrier tickets 独立、closed 空容器替换拒绝。新增检查不得 profiling/frames/locals 或定点计时，不获取真实资源。部分归还中途故障仅静态审查，不把预检失败说成已覆盖中途故障。
3. 六脚本、实际准备双审后，用新 xb claim 执行一次 32 项；原 case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest child 保持，本批 max1。保原监督器返回对象、kill/wait/finally 和 raw-first 保全；所有失败、超时和缺 CASE 原样保留。执行后双审实际证据，再追加主方案及项目事实。

三个目录 timeout 不纳入本批，因为本次仅关闭已确认的临时引用环，没有足以定位目录超时的静态证据。后继仍需有限静态调用和复杂度调查及有依据的修订；不得把本批其他绿项追认为目录已通过。

N007 仅验证 _leave 入口当前活动 body primary 的原异常身份及传播，并验证该预检失败后原 loans/membership/preferred 未清空；不等于 scope/origin/budget 全首错或真实资源失败链认证。cleanup_failure 是否可登记继续服从原完整性检查；不为使检查变绿而修复损坏状态。

正常库、服务/dist、既有 ACL/settings、真实平台及 Git 保持。r920/r964 已耗不重复；r967 待原问题，不执行不重问；P06 历史 0/7 无备份不再问。真实 route/三个 owned body/最后释放与 pending 恢复、原111/94/r919、Store 写权和完整 Goal 仍未完成。
