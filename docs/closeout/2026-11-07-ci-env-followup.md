# CI 环境初始化收尾

首次交付后的 GitHub Actions [运行 42360558764](https://github.com/nivranic/reno/actions/runs/42360558764) 对应交接提交 `2d4b15c7ab4c5c45dc2f50c29f5e048558486141`，状态为 completed/failure；job API 返回 0 个任务。失败发生在工作流校验阶段，不是后端、前端或 E2E 执行失败。

公开失败页明确列出原工作流第 67/68、103/104、139/140、179/180 行的 `runner.temp` 上下文错误。四个任务是 reconciliation、derivation、sync-recovery、sync-chain。原始公开响应与页面保存在本机收尾记录，不把原页、账号或私密日志发布到仓库。

## 修正

按 GitHub 官方 [context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)，任务级 `env` 不支持 `runner`。将四个任务中的 TMPDIR 与 RENO_E2E_MANIFEST 移至第一个 shell 步骤，以实际 `$RUNNER_TEMP` 计算，并写入 `$GITHUB_ENV`，供后继步骤读取。其余原有步骤、测试命令、Python 选择、资料路径与上传清单保持。使用方法依据官方 [环境文件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#setting-an-environment-variable)。

这修正的是 CI 配置。没有修改产品模块、正常数据库、服务、dist、权限或封存原生候选，没有启动或重放原生诊断。

## 实际本地核对

- 用现有 js-yaml 解析前后完整工作流；将这四处环境初始化位置还原后，完整 YAML 对象相等。
- 在新隔离目录执行四条实际 Bash 命令，TMPDIR／manifest 路径全部匹配预期；runner temp 与环境文件名含空格，原环境文件已有记录保持。
- `git diff --check` 通过。没有运行产品测试或 C 候选。

首次沙箱内 Bash 因 MSYS 命名对象权限失败，尚未执行目标 shell 命令（`f5acbb`）；通过正式工具权限通道完成上述控制命令核对（`f15ac8`），没有修改 ACL。该控制工具检查不消耗或重置旧产品 runner／诊断额度。

GitHub 实际是否接收新工作流、各项测试是否通过，必须查看本次 `fix(ci)` 提交关联的 Actions 运行。上述本地核对只支持配置与环境传递，不声称 CI、后端全量、E2E、原生构建运行或完整产品目标通过。旧失败运行保留，不重跑或删除。

## 推送后的实际结果

修正提交 `4e2a96eaeedfbc57f04c8d7ee7c21f463889339e` 已推送。GitHub [运行 42361078787](https://github.com/nivranic/reno/actions/runs/42361078787) 接收工作流并启动全部七个任务，于 2026-11-07 09:47:31 UTC 结束，整体 failure。环境上下文校验错误已解除，CI 没有全通过。

通过任务为 frontend、reconciliation、derivation。checkpoint-runtime 在 pip 安装依赖时失败，测试步骤 skipped；backend 在 pytest 步骤以 2 退出；sync-chain 与 sync-recovery 在各自场景脚本步骤以 1 退出。详见 [机器记录](2026-11-07-ci-remote-result.json)。

当前公开日志 API 返回 403，公开任务页要求 GitHub 登录。公开注记仅提供退出码，未取得四项具体异常或依赖错误。因此不根据猜测改依赖、产品行为、超时或校验条件；后继应先取得有权限的同一任务日志，再定位对应问题。原公开元数据和读取回执保存在本机。

后继结果记录只修改文档，提交使用 GitHub 官方支持的 [skip ci](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs) 标记，避免为相同源码再次启动七项任务。该文档提交没有新的 CI 通过结论，源码证据仍为上述 4e2a96e 运行的 3 项成功／4 项失败。
