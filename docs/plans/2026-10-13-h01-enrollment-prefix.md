# H01 登记前缀的永久发布

本批承接 §17.297 的原字节绑定，实现 H01 的文件前缀 continuation；完整 SQL 登记与活写权仍为后继工作。

## 状态与接口

新增 `reno/personal_authority_enrollment.py` 和独立 `personal_authority_enrollment_io.py`。借 EX 的 `publish_prefix_locked` / `verify_prefix_locked` 消费五项预期 SHA、原 UUID 和不可变 deadline；公共 `publish_prefix` / `verify_prefix` 在验证、唯一用户 callback 和取 EX 前建立一个 30 秒 token，等待自身最外 EX 普通正常退出后交付结果。

新状态继承 namespace3 root 的原 native/path identity，在 root 内增加六个固定文件。绑定先发布，保留 second 中的原 intent bytes，随后发布 local contract、init seal、genesis，最后发布引用各原件 SHA 的 authority prefix。发布器使用单独的 name/kind/operation 门和 stage 命名，不扩大旧 namespace3 publisher 的同版接受域。

每个文件用 private CreateNew stage、分块完整写、flush、原句柄完整读回、no-replace rename、独立 creator-live 读回和 creator 关闭后的独立全文读回。目录/文件 owner 在普通失败时保留首个异常；正常关闭失败拒绝返回，残件不删除、不收编。所有检查和 I/O 使用同一受限 deadline，原大小/名称计数预算保持。

开始前读取并保持五原件，检查完整 SHA/context、ARRAY issuer 与 DICT physical role、原 parent/root creator identity、当前四路径/node 与 fresh origin；发布后再核完整原件、全前缀内容链、完整名字集合与 fresh origin。完成标记之后发现的错误仍拒返回，磁盘标记只能供后继重新校验。

无续写 partial API；完整状态可以由独立 `verify_prefix` 冷读取，部分、stage、alias、未知名字均拒绝。旧 namespace verifier 的 root-only-seal 规则保持，因此前缀发布后它会拒绝新的后继状态；调用者必须使用新层验证。旧 r729 Store/layout/source bindings 不继承本层结果。

## 完成结果的边界

返回 scope 为 `durable_authority_prefix_files_only`，字段只有原件/绑定/前缀 SHA 与身份字符串。locked 返回不关闭 caller EX；owned 返回在自身所有普通 context 和 EX 返回后交付。两者都不提供 `source_closed`、SQL COMMIT、enrolled、active、writable 或活 witness。

现有 capture 的退休账本与普通 context 正常返回不足以证明全部原生关闭。directory、node、image/private-copy 和生命周期锁资源的固定实际关闭回执及其聚合仍须独立实现；不能用新增布尔标记替代。原 native 20/25/10、product30、parent360、matrix1680 和 r730 耗尽保持，不添加 profiling、trace、frame/locals 采样或 body markers。

## 验证

新增隔离临时 profile 测试，重点覆盖真实发布/独立读回与原库保全、原 intent bytes、完整状态冷读取、旧 verifier 拒新状态、重发/partial/未知 stage 拒绝、SHA 首错无发布、正常 EX 退出失败不交付、body/control 原对象优先，以及同 deadline 的边界拒绝。故障 seam 明示为受控注入，不据此推 OS 自然故障或完整 native 关闭。所有产物留在本批目录；不触碰正常库/service/dist、既有 ACL/settings 或 Git 提交。

P06 用户无历史材料、七身份 0/7，问题已关闭。完整 goal 保持 active。
