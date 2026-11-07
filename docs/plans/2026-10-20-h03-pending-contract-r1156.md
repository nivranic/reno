# H03 pending字节合同候选：实现与验证

r1156已准备H03批次B的独立纯字节合同候选：固定三字段pending封装保原intent字节，唯一fact槽取domain+前驱grant，不取handoff UUID；同槽改UUID/目标/epoch/preview/SQL均冲突。先核原DAG再虚拟加入预留intent，防止嵌入文本冒认已发布事实。支持genesis/promotion及pending/intent/fence/stop声明前缀，输出只属canonical声明、授权仍unknown。两份源码审查后单次固定合成runner实际19/19通过，无失败/错误；两类最大封装1095/1185B，现有facts 32KiB与80字符id界保持。候选/依赖/测试/runner02四pin共58912B，未安装。

这是独立代码增量和合成证据，未实现真实pending发布/全writer暂停、最终包、owner恢复或成功关闭；旧DAG parser/保全/恢复/GC仍须按角色接线。H01完整native设计、B001/B002 open2、完整design/source/execution/Goal四False保持；产品仍r1106 SOURCE02完整159未安装，Goal active。未执行产品/pytest/native/模型/QA/诊断，不重放r920/r964、不执行r967；正常库/服务/dist/权限/Git未操作。本批另保留逻辑释放与物理页回收的合同澄清，未关闭W42。

# r1156：H03 原操作占位的纯字节合同

本批执行既有 H03 方案的批次 B。Root 与同一独立代理已核定，该纯合同不依赖 H01 native 完整设计闭合；运行发布、真实恢复及启用仍依赖 H01/H02/H05。设计使用 executing-plans 与 brainstorming 技能，沿既有已授权方案收窄接口，没有另建运行权限机制。

## 固定格式与现有接点

新增候选 `reno/personal_handoff_contract.py`，独立格式：

`{"format_version":1,"kind":"handoff_pending","original_intent_utf8":"原 canonical handoff_intent 的 UTF-8 文本"}`

严格闭合三字段。原 intent 必须由现有 `personal_authority_contract.decode` 接受，kind 必须为 handoff_intent，重新 encode 须与原 bytes 全等。源/目标、两个操作 UUID、前驱 grant/receipt、domain、epoch、revision、SQL 全沿原 intent 绑定，不复制一套可能漂移的 DTO 字段。此记录只是声明；codec 不证明占位已发布、授权仍有效或原主已停写。

唯一 fact id 为 `handoff-pending-` 加 SHA256(canonical `{domain_id,predecessor_grant_sha256}`)，16+64=80 字符，使用既有 facts 文件角色及文件层的 no-replace/同 bytes 重试接点；不按 handoff UUID 分槽。`require_same_pending` 只检原字节一致，不能代替真实文件竞争或授权。

不在 authority 根添加新目录。`personal_authority_files._ROOT_NAMES` 是封闭集合，`_FACT_ID` 允许的 80 字符和 `max_fact_bytes=32768` 容纳本角色。authority 的完整 DAG parser 不能直接解码 pending，后续运行消费者必须明确分离角色，并接入保全、恢复与 GC。现有树的保护不等于 H02 接线完成。本批不调用文件层、也不创建永久 facts。

## API 与边界

- `encode_pending(original_intent: bytes) -> bytes`：封装原 intent。
- `decode_pending(raw: bytes) -> bytes`：校验外层 canonical 格式后，返回原 intent bytes。
- `pending_fact_id(raw: bytes) -> str`：只从已经校验的原 intent 推导唯一槽。
- `require_same_pending(existing: bytes, requested: bytes) -> None`：只接受逐字节相同的合法占位声明。
- `inspect_declared_prefix(raw: bytes, authority_records: list|tuple[bytes]) -> dict`：校验所选原 authority DAG 和预留 intent 的关系，返回 pending/intent/fence/stop 的**声明前缀**。不返回恢复动作或许可；stop 中的 final hashes 仍是 opaque 声明，不说明真实最终包存在或资源关闭成功。

所选 DAG 先独立闭合校验，防止用 pending 内文本冒充已发布 intent。若原 intent 尚不在 DAG，另在内存加入预留原字节核前驱及唯一性；这个额外校验不改变返回前缀。两次校验都沿旧 codec 原规则，同 domain、同前驱只许一个 handoff，不放宽旧 64 条/512KiB/32KiB/depth12/nodes512 限额。计入预留原 intent 后仍须满足这些界限，外层 pending 另有 32KiB 上限，总输入不超过 544KiB。

合法 handoff_intent 只含固定 ASCII keys、固定枚举、UUID36、SHA64 和至多19位整数。待保存标准库派生的两种前驱最大长度记录，证明 32KiB 外层不会仅因封装开销删除合法 intent 域；不靠“通常很小”判断。

## 验证与交付

完整候选、固定依赖副本、纯合成测试及 bounded runner 均准备后，先经 Root 与同一代理两份源码审查。只有两审通过才执行该 runner；只加载固定字节的纯 codec，无项目包初始化、native、文件/SQL/服务、模型或旧诊断。最多一个子进程，执行期限不超过30秒，输入/输出不超过1MiB。测试覆盖 genesis/promotion、各声明前缀、同前驱不同 UUID/目标/epoch/preview/SQL 冲突、canonical/重复 key/未知字段/类型/边界、缺失引用及不同 domain。

本批不安装 H03 候选、不修改 r1106 SOURCE02、不执行产品/pytest/native/QA/诊断，不重放 r920/r964、不执行 r967，不操作真实库、服务、权限或 Git。全局 Goal 仍未完成；B001/B002 与完整 H01 的四 False 保留。


运行器01被R001拒绝，未执行；02改用有界报告文件、发出child预算前预留报告实读、共用deadline及保存前限长。01原件和初审保留，最终两审/执行只认02。02失败路径的total_control_bytes可能是部分或保守记账，不能据此声称失败时的实际总实读；本次成功结果已核worker实读60174B及合计123113B。执行约0.328秒，19测试、0失败、0错误。

验证原始记录：data/t16-h03-pending-contract-r1156/RUN-RESULT-02.json。当前仍不能据此启用切主。
