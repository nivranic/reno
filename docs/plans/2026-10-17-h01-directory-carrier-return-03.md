# directory carrier 归还：复审补充

补首稿与02稿，关闭设计复审提出的并发和发行身份缺口；源码仍须独立复审，本文不是通过声明。

1. **原调用身份。** 原 scope-return producer 在任何 scope delete 前，将自己的原 Call 和原16槽 scope-return cell写入 Reservation 预分配 carrier_return；只允许一次。resources gate必须核该 Call 同一对象、仍open、原 membership及issuer，且cell同一；不接受新建的合法 Call重用历史完成事实。已存在的其他 role=call loan一概阻止删除，即使issuer.closed=True也不能按历史忽略。consumer里已关闭的历史Call对象和实际loan entry分开处理。
2. **同锁写入口。** 新loan锁内重核issuer Call仍open；同时检查其既有全部owner的fence，拒绝在当前gate通过坏locator跨owner新借。scope delete与Call.close同原pending锁协调。
3. **支持的重入写入。** fence内原pending的consume/scope准备、budget绑定与handoff拒绝状态转换；观察错误与fallback sink仍保原first/observer事实，同时置预分配batch的poison。raw scope/path setter和custody register/put在fence内拒绝写入；只读get/check/无变化bind可使用当前Call。先完整完成所有可分配的证明/return-cell构造，最后无callback地核原phase/claimed/root/budget、first/escaping/observer/poison、原fence及所有loan原entry身份，然后直接进入精确builtin删除。发生已被拒绝且无写入的新loan尝试不虚构错误债务；实际错误观察则使整批拒绝。不能只依赖早先scope-return预检。
4. **after完整性。** scope after helper除原16槽、consume、原constructor/Token/grant等来源外，明确再核原pending phase active/claimed、first/escaping/observer_failure为空；不能把仅核consume的旧helper当作新后验。
5. **bootstrap时点。** resources先完成自身完整_PINS，然后调用原无参binder；binder不调用未就绪的_authenticate。链接保原模块、原字典、原函数`__globals__`与原目录、原sys.modules/safe字典/固定名，保函数code/default/closure pins。重复绑定只核原行，绝不覆盖。scope仅固定这些稳定依赖，不冻结TRACE_MODULE初始None。

验证增加：历史scope成功配新Call拒绝、已closed但仍存在loan拒绝、pending观察/handoff/fallback重入、同gate raw setter/put拒绝、foreign locator借用拒绝、fence解除后正常借用恢复。这里不承诺在任意Python代码能直接修改所有私有容器的情况下恢复原记录；被损坏的原记录应拒绝，原图由pending保留，不能以修镜像换通过。
