# 2026-11-07 静态候选交付快照

此目录保存最新累计源码候选和直接后继所需的合同材料。它是工程源码交付，完整产品目标仍未完成。

## 文件与身份

- `reno-makegen-explicit-code-source-r1528/`：完整 63 文件，包括 before/working、源码差异、独立预测、实际核对及最终审查。当前 `working-01` 为 **23 源 / 2,136,879 B**。
- `reno-native-v3-call-design-r1523/`：既有草稿、读取请求与本次官方读取结果。草稿不是已通过的完整 V3 实施设计。
- `reno-loader-body-effect-contract-r1525/`、`reno-loader-primitive-consumption-contract-r1526/`、`reno-loader-local-generator-format-contract-r1527/`：当前 body、primitive 消费和 MakeCoro 调查合同；保留修订前材料和纠错记录。
- `MANIFEST.json`：上述五组 **90 文件 / 4,571,959 B** 的相对路径、长度和 SHA-256。文件按原字节复制，`.gitattributes` 禁止该目录的换行转换。

r1528 `FINAL-REVIEW.json`：13,874 B，SHA-256 `d532e204503daa6f71ab2de383f63530607345f2f5ff51379ac4995f3b263174`。其中记录 62 项身份；最终审查文件本身构成第 63 文件。最终源码只在原 private `make_gen` 增加显式 code 输入，三个既有 caller 仍在各调用点读取当前 `func_code`，没有改成 early held frame code。

## 本次读取与独立复核

用户批准的两份 CPython v3.12.10 官方文本已完成读取：实际 2/8 次，无服务错误、无应用重试，剩余 6 次未使用，本次读取已经结束。仅保存摘记和调用账，原文服务副本保存 0 B。独立一致性复核记录见 `OFFICIAL-READ-INDEPENDENT-REVIEW.json`。

`pycore_genobject.h` 的服务表示没有 `_Py_MakeCoro` 声明或完整对象布局。服务表示不是原 HTTP 字节、上游 commit 身份或目标编译输入证明。已有本地 genobject 参考可继续静态核对，不能把该实现缺失作为等待网络的理由。

## 验证及使用边界

r1528 的文字生成、可逆重建、51 项实际输出对照和主／独立审查已封存。**没有 C 解析、预处理、编译、链接、导入或运行证明**，没有安装到 `reno/`、修改正常数据库、重启服务或发布 dist。不得执行快照中的工具来重放旧批次；旧一次性诊断、runner 及官方读取额度不随 Git 交付重置。

此目录保存累计候选，没有复制全部历史 General 工作目录、忽略的 `data/`、真实数据库、附件、私密配置、原始运行日志或本机一次性运维脚本。JSON 中历史回执只是记录引用；干净 clone 不含这些本机原始回执。引用到父候选或外部旧证据的材料不代表已经随包归档。

后继工作先读当前交接文档，再读 r1528 `NEXT.md`。封存文件中的“不执行 Git／只写 General”等文字描述旧批次边界；本次用户另行授权的是收尾、Git 推送及文档交接，不能据此扩大产品执行、原生诊断或安装权限。

固定状态：`source_only_closed_fields=[]`；`candidate_executed=false`；`complete_child_implemented=false`；`complete_exec_dispatch_implemented=false`；`full_goal_complete=false`。

CPython 参考与修改片段随附 Python 3.12.10 发行版许可通知 PYTHON-LICENSE.txt。许可通知不认证候选源码或目标 ABI 身份。
