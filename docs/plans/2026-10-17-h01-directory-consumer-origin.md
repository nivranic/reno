# Task7 directory carrier：原消费者结束事实

承接 r1045 SOURCE03 和 r1046 实际检查。r1047 只补合法归还所需的原消费者事实，不删除任何 loan，不签 terminal permit，不释放 root/reservation，不安装 canonical/c973，不启用 strict。

## 调查与选择

已确认 `_verify_lock_group` 是 directory carrier 实际 consumer。同一 carrier 跨首次 fresh verification、全部 marker reads、第二次 fresh verification和 filesystem 出口。guard producer `_capture_guard_file_plan_original` 目前没有外部 namespace caller；其 parent directory carrier 不可冒认已被本 consumer 消费。既有 r1014 调查和当前九个 namespace 文件一致。

直接在首次 verify 后删除会截断 lifetime；在 terminal 仅扫描 holder 不能证明消费者结束。采用保留原主体的私有 inner + 同签名 wrapper：wrapper观察实际 inner return/error，inner 原 finally 完成后才会返回；后续归还门还必须独立确认 wrapper Call 已关闭。此阶段先补这条来源，避免把 body 内的 return intent 当作实际 callee 返回。

## 实施范围

1. `storage_lifecycle.py`：原 `_verify_lock_group` 主体改名为 `_verify_lock_group_body_original`，保留原顺序、所有 IO、两次 fresh、fallback、filesystem和 finally。仅在实际 producer outer-return观察之后、unpack之前，调用新 resources consumer-begin，传原 locks、inputs、实际 carrier和 wrapper预建 channel。
2. 同名 wrapper 保持原 public 私有签名，原入口先建立自己的 Call、预建固定15槽 channel，在 try 内调用固定 inner。channel保存原 record、wrapper Call、inner callee、locks、attempt、actual result/returned、actual error/tb、observer attempt/result/returned/error/tb、最终 observation。实际 inner返回或异常先直接写 channel，再调 resources finish；原 inner错误优先，finish错误分层保存。最后原 Call leave；后续 terminal 必须核该实际 Call 的关闭事实，不能仅信 finish返回。
3. `personal_authority_guard_resources.py`：新增内部 `_DirectoryConsumer` raw trace record，复用原 path-record partition，不新增 custody partition/tag、DTO属性或全局强表。record先分配/强根，再发布到 channel，保 source、原 carrier六元登记、原selection、walks、原resource七元anchors、原ledger和原selection之前的verification history。逐ledger追加 `directory_consumers`，partial record仍在原raw表保留。
4. begin 对 legacy carrier返回None；已知 strict carrier不得降级。校验原 carrier、目录source identity、每个原walk、同一selection.outer_result、原selection.result/plan及所属scope/group/root/ledger。callee必须为当前封存 inner，channel的wrapper Call必须为原传入Call，locks/inputs逐身份对应，不能拿guard producer的parent冒充本consumer。未接guard不添加虚构consumer。
5. finish 仅保存和验证事实。先保存实际 channel和原 inner outcome；strict record须同一channel、Call、callee、locks和carrier。原inner正常返回必须为None；非None plan须有恰两项新原verification，complete/outer_returned=True、outer_result=None、first/failures空，且同原selection/plan/members。None fallback单列为fallback，不称两次fresh。finish observation包含原原始history及实际result，不声明physical/fee/source_closed已完成。异常完整保留，不能补写inner成功或重试。
6. 新函数/class/global依赖和原resources固定函数/类目录及lifecycle绑定同步；37完整候选仅两文件改，其余35逐byte继承。主体剥除新增begin接线后AST与父函数相同。

## channel布局和错误语义

15槽：record、wrapper Call、inner callee、locks、inner attempted、actual result、actual returned、actual error、actual tb、observer attempted、observer result、observer returned、observer error、observer tb、finish observation。初始化所有bool为False。wrapper在调用inner前设attempt；实际返回先写result再returned；except先存error/tb再raise；finally保存observer实际结果或错误。inner原错优先于observer错；若inner正常而observer错则observer错外抛。Call leave沿原_CUSTODY_LEAVE首错合同，不能伪造wrapper已退出。

## 审查与验证

- Root + 同一只读代理设计审、完整源码审通过后准备具名模型；script和实际准备也须两审，新claim才运行。原已绿项不重跑。
- 检查实际wrapper+原完整inner、完整custody/pending、resources新增原闭包函数；底层IO/EX/native/SQL和原walk/raw-record的必要adapter明确列出。
- 覆盖正常两fresh/多lock、实际marker或前后fresh/文件系统exit/内部Call leave错误、None fallback、legacy、自定义异常身份、finish错误不覆盖原first、错误carrier/source/selection/owner/callee/Call/history、重复finish与partial begin。实际IO与合成IO分列，无profiling/frames/locals/定点诊断。
- case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest children、批内并发1。先raw保全→两actual审→delivery/readback→§17.431。

## 后继与边界

有本事实后，仍需设计原scope-return之后的carrier逻辑归还门、guard未来真实caller、同pending RLock协调的新loan/fence事务、逐holder actual delete/effect及最后Call两阶段terminal permit。费用原ledger认证、全部资源债务、图字节容量和durable恢复仍待，不把本批视为完成这些工作。

74保护逐路径56同18缺，继承missing16陈旧原保；正常库/service-dist/ACL/settings/平台/Git保持，dirty main不commit/push/reset/clean。r920/r964已耗不重放，r967待原问题不执行重问，P06历史0/7且无备份。回滚为候选不安装，父本与旧证据保留。
